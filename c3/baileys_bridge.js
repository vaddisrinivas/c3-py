#!/usr/bin/env node
import makeWASocket, {
  Browsers,
  DisconnectReason,
  decryptPollVote,
  downloadMediaMessage,
  fetchLatestBaileysVersion,
  jidNormalizedUser,
  makeCacheableSignalKeyStore,
  normalizeMessageContent,
  proto,
  useMultiFileAuthState,
} from '@whiskeysockets/baileys'
import { createHash, randomBytes } from 'crypto'
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'fs'
import { basename, extname, join } from 'path'
import { fileURLToPath } from 'url'
import pino from 'pino'
import qrcode from 'qrcode-terminal'
import { createInterface } from 'readline'

const logger   = pino({ level: 'silent' }, pino.destination(2))
const SESSIONS = process.env.SESSIONS_DIR ?? './sessions'
const MEDIA_DIR = join(SESSIONS, 'media')
const ALLOWED  = new Set((process.env.ALLOWED_SENDERS ?? '').split(',').map(s => s.trim()).filter(Boolean))

mkdirSync(MEDIA_DIR, { recursive: true })

let sock             = null
let adminJid         = ''
let reconnectAttempts = 0

const nameCache  = new Map()
const pollStore  = new Map()
const lidToPhone = new Map()
const LAST_SEEN_FILE = join(SESSIONS, 'last_seen.json')
const processedIds   = new Set()

function loadLastSeen() {
  try {
    if (existsSync(LAST_SEEN_FILE)) return JSON.parse(readFileSync(LAST_SEEN_FILE, 'utf8')).timestamp || 0
  } catch {}
  return 0
}

function saveLastSeen(ts) {
  try { writeFileSync(LAST_SEEN_FILE, JSON.stringify({ timestamp: ts })) } catch {}
}

let lastSeenTs = loadLastSeen()

function emit(obj)         { process.stdout.write(JSON.stringify(obj) + '\n') }
function log(tag, msg)     { process.stderr.write(`[${tag}] ${msg}\n`) }
function respond(id, res)  { emit({ id, result: res }) }
function respondErr(id, e) { emit({ id, error: String(e) }) }

async function connect() {
  const { state, saveCreds } = await useMultiFileAuthState(SESSIONS)
  const { version } = await fetchLatestBaileysVersion().catch(() => ({ version: [2, 3000, 1023809250] }))

  sock = makeWASocket({
    version,
    auth: { creds: state.creds, keys: makeCacheableSignalKeyStore(state.keys, logger) },
    logger,
    printQRInTerminal: false,
    browser: Browsers.macOS('Chrome'),
  })

  sock.ev.on('creds.update', saveCreds)

  sock.ev.on('connection.update', update => {
    const { connection, lastDisconnect, qr } = update

    if (qr) {
      process.stderr.write('\n[baileys] Scan QR to authenticate:\n')
      qrcode.generate(qr, { small: true }, str => process.stderr.write(str + '\n'))
    }

    if (connection === 'open') {
      adminJid = sock?.user?.id ?? ''
      reconnectAttempts = 0
      log('wa', `connected as ${adminJid}`)
      const phoneUser = adminJid.split(':')[0]
      const lidUser   = sock?.user?.lid?.split(':')[0]
      if (lidUser && phoneUser) lidToPhone.set(lidUser, `${phoneUser}@s.whatsapp.net`)
      emit({ event: 'ready', adminJid })
    }

    if (connection === 'close') {
      const code      = lastDisconnect?.error?.output?.statusCode
      const loggedOut = code === DisconnectReason.loggedOut
      log('wa', `disconnected code=${code ?? 'unknown'} loggedOut=${loggedOut}`)
      if (!loggedOut) {
        const base  = code === 440 ? 5000 : 2000
        const delay = Math.min(base * 2 ** reconnectAttempts++, 60000)
        log('wa', `reconnecting in ${delay / 1000}s (attempt ${reconnectAttempts})`)
        setTimeout(() => connect(), delay)
      }
    }
  })

  sock.ev.on('contacts.upsert', contacts => {
    for (const c of contacts) {
      const name = c.notify ?? c.name
      if (name) nameCache.set(c.id, name)
    }
  })

  sock.ev.on('messages.upsert', ({ messages, type }) => {
    const isCatchup = type === 'append'
    if (type !== 'notify' && type !== 'append') return

    for (const raw of messages) {
      try {
        if (!raw.message) continue

        const msgId = raw.key.id
        if (msgId && processedIds.has(msgId)) continue
        if (msgId) processedIds.add(msgId)

        const msgTs = Number(raw.messageTimestamp ?? 0)
        if (isCatchup && msgTs > 0 && msgTs <= lastSeenTs) continue

        if (raw.pushName) {
          const cacheJid = raw.key.participant ?? (raw.key.remoteJid?.endsWith('@g.us') ? null : raw.key.remoteJid)
          if (cacheJid) nameCache.set(cacheJid, raw.pushName)
        }

        const pollUpdateMsg = raw.message.pollUpdateMessage
        if (pollUpdateMsg) { if (!isCatchup) handlePollVote(raw, pollUpdateMsg); continue }

        const normalised = normalizeMessageContent(raw.message)
        if (!normalised) continue
        const rawJid = raw.key.remoteJid
        if (!rawJid || rawJid === 'status@broadcast') continue
        if (raw.key.fromMe) continue

        const jid     = translateLid(rawJid)
        const isGroup = jid.endsWith('@g.us')
        const sender  = isGroup ? translateLid(raw.key.participant ?? jid) : jid

        if (!isGroup && ALLOWED.size > 0 && !ALLOWED.has(sender)) continue

        const content = extractText(normalised)
        if (!content) continue

        const ts = msgTs || Math.floor(Date.now() / 1000)
        const mediaInfo = detectMedia(normalised)

        emit({
          event: 'message',
          msg: {
            jid,
            sender,
            pushName:  raw.pushName ?? nameCache.get(sender) ?? sender.split('@')[0],
            text: content,
            timestamp: ts,
            isGroup,
            messageId: msgId,
            ...(isCatchup ? { catchup: true } : {}),
            ...(mediaInfo ? {
              mediaType:     mediaInfo.type,
              mediaMimetype: mediaInfo.mimetype ?? null,
              mediaSize:     mediaInfo.fileSize ?? null,
              mediaDuration: mediaInfo.seconds ?? null,
              mediaFileName: mediaInfo.fileName ?? null,
            } : {}),
          },
        })

        if (mediaInfo && msgId) {
          downloadMediaMessage(raw, 'buffer', {}, { logger, reuploadRequest: sock.updateMediaMessage })
            .then(buffer => {
              const ext = mediaInfo.ext || '.bin'
              const filename = `${msgId}${ext}`
              const mediaPath = join(MEDIA_DIR, filename)
              writeFileSync(mediaPath, buffer)
              log('media', `saved ${mediaInfo.type} → ${filename} (${buffer.length} bytes)`)
              emit({ event: 'media_ready', messageId: msgId, mediaPath, mediaType: mediaInfo.type })
            })
            .catch(err => log('media', `download failed: ${err.message ?? err}`))
        }

        if (ts > lastSeenTs) { lastSeenTs = ts; saveLastSeen(ts) }
      } catch (err) {
        log('bridge', `message error: ${err}`)
      }
    }

    if (processedIds.size > 10000) {
      const arr = [...processedIds]; processedIds.clear()
      for (const id of arr.slice(-5000)) processedIds.add(id)
    }

    // Cap pollStore to prevent memory leak
    if (pollStore.size > 500) {
        const keys = [...pollStore.keys()]
        for (const k of keys.slice(0, keys.length - 200)) pollStore.delete(k)
    }
  })
}

function translateLid(jid) {
  if (!jid || !jid.endsWith('@lid')) return jid
  const lidUser = jid.split('@')[0].split(':')[0]
  const phone   = lidToPhone.get(lidUser)
  return phone ?? jid
}

function extractText(msg) {
  if (msg.conversation)                return msg.conversation
  if (msg.extendedTextMessage) {
    const ext = msg.extendedTextMessage
    if (ext.matchedText) return `[link: ${ext.matchedText}]${ext.text ? ` ${ext.text}` : ''}`
    if (ext.text) return ext.text
  }
  if (msg.imageMessage) {
    const cap = msg.imageMessage.caption
    return cap ? `[image] ${cap}` : '[image]'
  }
  if (msg.videoMessage) {
    const cap = msg.videoMessage.caption
    return cap ? `[video] ${cap}` : '[video]'
  }
  if (msg.audioMessage) {
    const ptt  = msg.audioMessage.ptt
    const secs = msg.audioMessage.seconds
    return `[${ptt ? 'voice note' : 'audio'}${secs ? ` ${secs}s` : ''}]`
  }
  if (msg.documentMessage) {
    const name = msg.documentMessage.fileName || 'document'
    const cap  = msg.documentMessage.caption ? ` — ${msg.documentMessage.caption}` : ''
    return `[document: ${name}${cap}]`
  }
  if (msg.stickerMessage) return '[sticker]'
  if (msg.locationMessage) {
    const { degreesLatitude: lat = 0, degreesLongitude: lng = 0, name = '' } = msg.locationMessage
    return `[location: ${lat},${lng}${name ? ` | ${name}` : ''}]`
  }
  if (msg.liveLocationMessage) {
    const { degreesLatitude: lat = 0, degreesLongitude: lng = 0, caption = '' } = msg.liveLocationMessage
    return `[live location: ${lat},${lng}${caption ? ` | ${caption}` : ''}]`
  }
  if (msg.contactMessage) return `[contact: ${msg.contactMessage.displayName ?? 'unknown'}]`
  if (msg.contactsArrayMessage) {
    const names = (msg.contactsArrayMessage.contacts ?? [])
      .map(c => c.displayName ?? 'unknown')
    return `[contacts: ${names.join(', ')}]`
  }
  if (msg.eventMessage) {
    const ev = msg.eventMessage
    const title = ev.name ?? ev.title ?? 'untitled'
    const time = ev.startTime ? new Date(Number(ev.startTime) * 1000).toISOString() : ''
    return `[event: ${title}${time ? ` | ${time}` : ''}]`
  }
  if (msg.reactionMessage) {
    const emoji = msg.reactionMessage.text
    const id    = msg.reactionMessage.key?.id ?? ''
    return emoji ? `[${emoji} reaction on ${id}]` : null
  }
  if (msg.protocolMessage?.type === proto.Message.ProtocolMessage.Type.REVOKE)
    return `[deleted: ${msg.protocolMessage.key?.id ?? ''}]`
  return null
}

function detectMedia(msg) {
  if (msg.imageMessage) return {
    type: 'image', ext: '.jpg',
    mimetype: msg.imageMessage.mimetype || 'image/jpeg',
    fileSize: msg.imageMessage.fileLength ?? null,
  }
  if (msg.videoMessage) return {
    type: 'video', ext: '.mp4',
    mimetype: msg.videoMessage.mimetype || 'video/mp4',
    fileSize: msg.videoMessage.fileLength ?? null,
    seconds:  msg.videoMessage.seconds ?? null,
  }
  if (msg.audioMessage) return {
    type: msg.audioMessage.ptt ? 'voice_note' : 'audio',
    ext:  msg.audioMessage.ptt ? '.ogg' : '.mp3',
    mimetype: msg.audioMessage.mimetype || 'audio/ogg',
    fileSize: msg.audioMessage.fileLength ?? null,
    seconds:  msg.audioMessage.seconds ?? null,
  }
  if (msg.stickerMessage) return {
    type: 'sticker', ext: '.webp',
    mimetype: msg.stickerMessage.mimetype || 'image/webp',
    fileSize: msg.stickerMessage.fileLength ?? null,
  }
  if (msg.documentMessage) {
    const name = msg.documentMessage.fileName || 'file'
    const ext  = extname(name) || '.bin'
    return {
      type: 'document', ext, fileName: name,
      mimetype: msg.documentMessage.mimetype || 'application/octet-stream',
      fileSize: msg.documentMessage.fileLength ?? null,
    }
  }
  if (msg.liveLocationMessage?.jpegThumbnail) return {
    type: 'live_location', ext: '.jpg',
    mimetype: 'image/jpeg',
    fileSize: msg.liveLocationMessage.jpegThumbnail.length ?? null,
  }
  return null
}

function handlePollVote(raw, pollUpdateMsg) {
  const pollId = pollUpdateMsg.pollCreationMessageKey?.id
  if (!pollId) return
  const poll = pollStore.get(pollId)
  if (!poll) { log('poll', `MISS ${pollId}`); return }

  const rawVoter = raw.key.participant ?? raw.key.remoteJid ?? ''
  const mePn     = jidNormalizedUser(sock?.user?.id  ?? '')
  const meLid    = jidNormalizedUser(sock?.user?.lid ?? '')
  const ck       = pollUpdateMsg.pollCreationMessageKey

  const creators = [...new Set([
    ck?.fromMe ? mePn : (ck?.participant ?? ck?.remoteJid ?? ''),
    ck?.fromMe ? meLid : '',
    ck?.participantAlt ?? '', ck?.remoteJidAlt ?? '',
    mePn, meLid, adminJid, jidNormalizedUser(adminJid),
  ].filter(Boolean))]

  const voters = [...new Set([
    rawVoter, jidNormalizedUser(rawVoter),
    raw.key.participantAlt ?? '', raw.key.remoteJidAlt ?? '',
  ].filter(Boolean))]

  let decrypted = null
  outer: for (const creator of creators) {
    for (const voter of voters) {
      try {
        decrypted = decryptPollVote(pollUpdateMsg.vote, {
          pollEncKey: poll.messageSecret, pollCreatorJid: creator, pollMsgId: pollId, voterJid: voter,
        })
        break outer
      } catch { /* try next */ }
    }
  }

  if (!decrypted) { log('poll', `decrypt FAILED ${pollId}`); return }

  const hashes   = (decrypted.selectedOptions ?? []).map(b => Buffer.from(b).toString())
  const selected = poll.options.filter(opt =>
    hashes.includes(createHash('sha256').update(Buffer.from(opt)).digest().toString()),
  )
  if (!selected.length) return

  const voterKey = jidNormalizedUser(rawVoter)
  if (!poll.votes.has(voterKey)) {
    poll.votes.set(voterKey, selected[0])
    const tally = {}
    for (const [vj, opt] of poll.votes) {
      if (!tally[opt]) tally[opt] = []
      tally[opt].push(nameCache.get(vj) ?? vj.split('@')[0])
    }
    log('poll', `vote: ${nameCache.get(voterKey) ?? voterKey} → ${selected[0]}`)
    emit({ event: 'poll_update', pollId, tally })
  }
}

async function handleCommand(cmd) {
  const { id } = cmd
  try {
    switch (cmd.cmd) {
      case 'send': {
        if (!sock) throw new Error('not connected')
        await sock.sendMessage(cmd.jid, { text: cmd.text })
        respond(id, 'sent')
        break
      }
      case 'sendPoll': {
        if (!sock) throw new Error('not connected')
        const messageSecret = randomBytes(32)
        const msg = await sock.sendMessage(cmd.jid, {
          poll: { name: cmd.question, values: cmd.options, selectableCount: 1, messageSecret },
        })
        const pollId = msg.key.id
        pollStore.set(pollId, { options: cmd.options, messageSecret, votes: new Map() })
        respond(id, pollId)
        break
      }
      case 'resolveGroup': {
        if (!sock) throw new Error('not connected')
        const code = new URL(cmd.link).pathname.replace(/^\//, '').trim()
        const info = await sock.groupGetInviteInfo(code)
        if (!info?.id) throw new Error('could not resolve group — bot must already be a member')
        respond(id, info.id)
        break
      }
      case 'getGroupMembers': {
        if (!sock) throw new Error('not connected')
        const meta = await sock.groupMetadata(cmd.groupJid)
        for (const p of meta.participants) {
          if (p.lid) {
            const lidUser = p.lid.split(':')[0].split('@')[0]
            const phoneUser = p.id.split(':')[0].split('@')[0]
            if (lidUser && phoneUser) {
              lidToPhone.set(lidUser, `${phoneUser}@s.whatsapp.net`)
              log('lid', `mapped ${lidUser} → ${phoneUser}@s.whatsapp.net`)
            }
          }
        }
        respond(id, meta.participants
          .filter(p => p.id !== adminJid)
          .map(p => ({
            jid:     p.id,
            lid:     p.lid ?? null,
            name:    nameCache.get(p.id) ?? nameCache.get(p.lid) ?? p.id.split('@')[0],
            isAdmin: p.admin === 'admin' || p.admin === 'superadmin',
          })))
        break
      }
      case 'sendImage': case 'sendVideo': case 'sendAudio': case 'sendDocument': {
        if (!sock) throw new Error('not connected')
        const buf = readFileSync(cmd.path)
        const mediaMsg = cmd.cmd === 'sendImage'    ? { image: buf, caption: cmd.caption || '' }
                       : cmd.cmd === 'sendVideo'    ? { video: buf, caption: cmd.caption || '' }
                       : cmd.cmd === 'sendAudio'    ? { audio: buf, ptt: cmd.ptt ?? false, mimetype: cmd.ptt ? 'audio/ogg; codecs=opus' : 'audio/mpeg' }
                       :                              { document: buf, fileName: cmd.fileName || basename(cmd.path), mimetype: cmd.mimetype || 'application/octet-stream' }
        await sock.sendMessage(cmd.jid, mediaMsg)
        respond(id, 'sent')
        break
      }
      case 'sendReaction': {
        if (!sock) throw new Error('not connected')
        await sock.sendMessage(cmd.jid, {
          react: { text: cmd.emoji, key: { remoteJid: cmd.jid, id: cmd.messageId } },
        })
        respond(id, 'ok')
        break
      }
      case 'sendPresence': {
        if (!sock) throw new Error('not connected')
        await sock.sendPresenceUpdate(cmd.presence || 'composing', cmd.jid)
        respond(id, 'ok')
        break
      }
      case 'sendReadReceipt': {
        if (!sock) throw new Error('not connected')
        await sock.readMessages([{ remoteJid: cmd.jid, id: cmd.messageId }])
        respond(id, 'ok')
        break
      }
      default:
        respondErr(id, `unknown cmd: ${cmd.cmd}`)
    }
  } catch (err) {
    respondErr(id, err)
  }
}

const rl = createInterface({ input: process.stdin })
rl.on('line', line => {
  const trimmed = line.trim()
  if (!trimmed) return
  try {
    handleCommand(JSON.parse(trimmed)).catch(e => log('bridge', `cmd error: ${e}`))
  } catch (e) {
    log('bridge', `invalid JSON: ${e}`)
  }
})

connect().catch(e => { log('bridge', `fatal: ${e}`); process.exit(1) })
