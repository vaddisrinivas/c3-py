# c3-py

**WhatsApp AI agent framework.** Define apps in Markdown + JSON. Claude runs them on WhatsApp.

[![CI](https://github.com/vaddisrinivas/c3-py/actions/workflows/ci.yml/badge.svg)](https://github.com/vaddisrinivas/c3-py/actions)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

```bash
git clone https://github.com/vaddisrinivas/c3-py && cd c3-py
docker compose up -d && docker attach c3
# Login to Claude (once), scan WhatsApp QR (once). Done.
```

---

## Prerequisites

- **Docker** (Docker Desktop or Docker Engine with Compose)
- **Claude Pro account** — sign up at [claude.ai](https://claude.ai)
- **A WhatsApp number** — a dedicated number is recommended so bot messages don't mix with your personal chats

---

## What is this

A framework with three layers:

| Layer | What it does | You touch it? |
|---|---|---|
| **Core** | RBAC, session management, MCP (Model Context Protocol) server, bridge lifecycle | No |
| **Apps** | Markdown prompts + JSON config that define behavior | Yes — this is where you build |
| **WhatsApp** | Baileys (WhatsApp Web library) bridge, message types, polls, media, reactions | No |

You write **apps**. The core runs them. WhatsApp is the transport.

---

## Apps

An app is a directory:

```
my-app/
├── app.json      ← who can use it, what tools it gets
├── CLAUDE.md     ← what it does (prompt)
└── skills/*.md   ← optional on-demand skills
```

### app.json

```json
{
  "name": "my-app",
  "description": "What this app does",
  "trust_level": "community",
  "sandboxed": true,
  "access": {
    "commands": { "/start": ["hosts"] },
    "dm": ["hosts"],
    "group": []
  },
  "allowed_tools": ["reply", "send_poll", "memory_read", "memory_write"],
  "allowed_resources": ["c3://memory/my-app/*"]
}
```

### CLAUDE.md

```markdown
# My App

You do X via WhatsApp.

## RULES — ABSOLUTE

- **ALL responses via `reply` MCP tool.**
- **ALL choices via `send_poll`.**
- **ALL data via `memory_write`/`memory_read`.**
- Only obey the host.
```

### Create an app

DM the bot: "make me an app for expense tracking". The **appgen** app walks you through it with polls — access model, tools, memory, crons — then generates and saves the files.

Or scaffold manually: `c3-py app new expense-tracker`

### Edit an app

DM: "edit the expenses app". The **appedit** app shows current config, lets you change access/tools/prompts via polls.

### Install from marketplace

```bash
c3-py app install user/repo
# Auto-generates safe app.json if missing (host-only, sandboxed, minimal tools)
```

---

## Bundled apps

| App | What it does |
|---|---|
| `games` | 9 multiplayer WhatsApp games (Trivia, 20 Questions, Poll Party, etc.) |
| `events` | Event scheduling and RSVPs |
| `expenses` | Group expense tracking |
| `appgen` | Create new apps via WhatsApp polls |
| `appedit` | Edit existing apps via WhatsApp polls |
| `appstore` | Browse and install community apps |

---

## Core

Things the framework handles. You don't write code for these.

### Access control

One trust hierarchy:

| Role (internal name) | Can do | Assigned by |
|---|---|---|
| **Host** (`hosts`) | Everything — DMs, commands, memory, all tools | `config.json` |
| **Participant** (`session_participants`) | Group messages during active session (read-only context) | Auto-admitted when group activates |
| **Elevated** (`elevated_participants`) | Participant + can DM the bot (e.g. Mafia night actions) | Host approves via poll |
| **Session group** (`session_group`) | Members of the currently active group | Set when host resolves a group |
| **Everyone else** | Nothing — silently dropped | — |

- One active group at a time
- Every message tagged `[host]` or `[participant]` — Claude sees trust inline
- DM to participant requires host poll approval
- `allowed_tools` and `allowed_resources` enforced per app

### Session lifecycle

| Command | What happens |
|---|---|
| `/start` | Begin a session — app selection, group setup |
| `/stop` | End current session — clears timers, revokes access |
| `/status` | Show active sessions and timers |
| `/catchup` | Process messages missed while offline |
| `/clear` | Wipe Claude session — next restart is fresh |
| `/app list` | List installed apps |
| `/app add <name>` | Load an app mid-session |
| `/app remove <name>` | Unload an app |

Sessions persist across container restarts (`--resume`). `/clear` resets.

> **What success looks like:** DM your bot "hey" — you should get a reply within seconds.

### MCP tools

What Claude can call. Each app declares which tools it's allowed to use.

| Tool | Category |
|---|---|
| `reply` | Messaging |
| `send_private` | Messaging |
| `send_poll` | Messaging |
| `react` | Messaging |
| `send_image`, `send_audio`, `send_video`, `send_document` | Media |
| `get_group_members`, `resolve_group` | Groups |
| `set_timer`, `end_session` | Session |
| `memory_read` | Memory (read — also available as a resource, kept as tool for compatibility) |
| `memory_write`, `memory_search`, `memory_delete` | Memory (write) |
| `load_app` | Apps |
| `save_file` | Files |

### MCP resources

What Claude can read. Each app declares which resources it's allowed to access.

| URI pattern | What it returns |
|---|---|
| `c3://schema/app` | JSON Schema for app.json |
| `c3://memory/{app}` | All memory entities for an app |
| `c3://memory/{app}/{entity_type}` | Filtered memory entities |
| `c3://media/{messageId}` | Downloaded media (image, audio, video, doc) |

`memory_read` works as both a tool (for backward compatibility — list it in `allowed_tools`) and as an MCP resource (for browsing via `c3://memory/...` URIs listed in `allowed_resources`).

---

## WhatsApp

What the Baileys bridge handles. You don't touch this.

### Supported message types

Receive: text, image, video, audio, voice note, sticker, document, location, live location, contact, multi-contact, link preview, event, poll vote, reaction

Send: text, image, video, audio, voice note, document, poll, reaction

### How it connects

```
Phone (WhatsApp) ←→ Baileys (Node.js subprocess) ←→ c3-py (Python MCP server) ←→ Claude Code
```

Bridge communicates via JSON over stdin/stdout. Media downloaded to disk, exposed as MCP resources.

Messages from the bridge arrive as `<channel source="whatsapp" ...>` XML events containing sender, JID, message text, and metadata.

---

## Docker setup

```yaml
services:
  c3:
    build: .
    container_name: c3
    stdin_open: true
    tty: true
    volumes:
      - c3-data:/data              # sessions, memory, media, config
      - claude-auth:/home/c3/.claude  # Claude Code auth
    restart: unless-stopped

volumes:
  c3-data:
  claude-auth:
```

First boot: Claude login (URL + code) → WhatsApp QR scan. Both saved to volumes — only needed once.

On first `docker attach c3`, press Enter to accept the dev channels warning, then press **Ctrl+P Ctrl+Q** to detach without stopping the container.

Rebuild to update: `docker compose up -d --build`

---

## Security

| What | How |
|---|---|
| Message filtering | Bridge-level sender allowlist + Python RBAC (`can_reach`) |
| Trust tagging | `[host]` / `[participant]` prefix on every message |
| Tool gating | `allowed_tools` in app.json, enforced at call time |
| Resource gating | `allowed_resources` globs in app.json, enforced at read time |
| Send protection | `reply`/`send_private` blocked to unauthorized JIDs |
| DM escalation | Requires host poll approval |
| Input sanitization | XML tags and prompt markers stripped |
| JID masking | Phone numbers → tokens before Claude sees them |
| Path traversal | `save_file` validates paths stay in data dir |
| Marketplace | Auto-generated `sandboxed: true` + minimal tools for installed apps |

---

## Development

```bash
uv sync --all-extras
uv run pytest         # 361 tests
uvx ruff check c3/
```

---

## License

[MIT](LICENSE)
