#!/usr/bin/env node
import makeWASocket, {
  Browsers,
  DisconnectReason,
  decryptPollVote,
  fetchLatestBaileysVersion,
  jidNormalizedUser,
  makeCacheableSignalKeyStore,
  normalizeMessageContent,
  proto,
  useMultiFileAuthState,
} from '@whiskeysockets/baileys'
import { createHash, randomBytes } from 'crypto'
import pino from 'pino'
import qrcode from 'qrcode-terminal'
import { createInterface } from 'readline'

const logger   = pino({ level: 'silent' }, pino.destination(2))
const SESSIONS = process.env.SESSIONS_DIR ?? './sessions'
const ALLOWED  = new Set((process.env.ALLOWED_SENDERS ?? '').split(',').map(s => s.trim()).filter(Boolean))

let sock             = null
let adminJid         = ''
let reconnectAttempts = 0

const nameCache  = new Map()
const pollStore  = new Map()
const lidToPhone = new Map()

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
    if (type !== 'notify') return
    for (const raw of messages) {
      try {
        if (!raw.message) continue
        if (raw.pushName) {
          const cacheJid = raw.key.participant ?? (raw.key.remoteJid?.endsWith('@g.us') ? null : raw.key.remoteJid)
          if (cacheJid) nameCache.set(cacheJid, raw.pushName)
        }
        const pollUpdateMsg = raw.message.pollUpdateMessage
        if (pollUpdateMsg) { handlePollVote(raw, pollUpdateMsg); continue }

        const normalised = normalizeMessageContent(raw.message)
        if (!normalised) continue
        const rawJid = raw.key.remoteJid
        if (!rawJid || rawJid === 'status@broadcast') continue
        if (raw.key.fromMe) continue

        const jid     = translateLid(rawJid)
        const isGroup = jid.endsWith('@g.us')
        const sender  = isGroup ? translateLid(raw.key.participant ?? jid) : jid

        if (!isGroup && ALLOWED.size > 0 && !ALLOWED.has(sender)) continue

        const text = extractText(normalised)
        if (!text) continue

        emit({
          event: 'message',
          msg: {
            jid,
            sender,
            pushName:  raw.pushName ?? nameCache.get(sender) ?? sender.split('@')[0],
            text,
            timestamp: Number(raw.messageTimestamp ?? Math.floor(Date.now() / 1000)),
            isGroup,
            messageId: raw.key.id,
          },
        })
      } catch (err) {
        log('bridge', `message error: ${err}`)
      }
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
  if (msg.extendedTextMessage?.text)   return msg.extendedTextMessage.text
  if (msg.imageMessage?.caption)       return msg.imageMessage.caption
  if (msg.videoMessage?.caption)       return msg.videoMessage.caption
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
  if (msg.contactMessage) return `[contact: ${msg.contactMessage.displayName ?? 'unknown'}]`
  if (msg.reactionMessage) {
    const emoji = msg.reactionMessage.text
    const id    = msg.reactionMessage.key?.id ?? ''
    return emoji ? `[${emoji} reaction on ${id}]` : null
  }
  if (msg.protocolMessage?.type === proto.Message.ProtocolMessage.Type.REVOKE)
    return `[deleted: ${msg.protocolMessage.key?.id ?? ''}]`
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
        // Build LID→phone mapping from group participants
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
