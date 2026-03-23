# c3-py

> **WhatsApp x Claude Code** -- the first WhatsApp channel for Claude Code via MCP

> **Status: Active Development** -- this project is under active development. APIs may change. Contributions welcome.

[![CI](https://github.com/vaddisrinivas/c3-py/actions/workflows/ci.yml/badge.svg)](https://github.com/vaddisrinivas/c3-py/actions)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

<!-- ![Demo](docs/demo.gif) -->

c3-py is (probably) the first WhatsApp channel implementation for Claude Code. While Anthropic ships official channels for Telegram and Discord, WhatsApp has 2.5B+ users and no official channel. c3-py bridges that gap using Claude Code's MCP channel protocol, Baileys for WhatsApp connectivity, and a Markdown-based plugin system that lets anyone create AI agents without writing code.

### Why not just another WhatsApp bot?

Most WhatsApp bots are stateless request-response wrappers around an API. c3-py is different:

- **Claude Code is the runtime** -- Claude reads your plugin's Markdown, understands the rules, manages state across turns, and makes decisions. You write prose, not code.
- **Native channel protocol** -- uses Claude Code's `notifications/claude/channel` for real-time bidirectional communication, not polling or webhooks.
- **Subagent architecture** -- main agent routes to specialized agents (games, calendar, mealprep) registered at boot via `--agents`. Each plugin is a self-contained agent.
- **WhatsApp-native UX** -- polls for every choice, DMs for secret info, timers for phases. Not text menus pretending to be interactive.

---

## Quick start

### Prerequisites

- Python 3.11+
- Node.js 18+
- [Claude Code CLI](https://claude.ai/download) (authenticated via `claude login`)

### Local setup (Mac/Linux)

```bash
# Install
pip install c3-py     # or: uv tool install c3-py

# Interactive setup -- checks prerequisites, WhatsApp QR, config
c3-py setup

# Launch
c3-py games
```

### Docker setup

```bash
# 1. Clone and start
git clone https://github.com/vaddisrinivas/c3-py && cd c3-py
docker compose up -d && docker attach c3

# 2. First boot walks you through:
#    - Claude Code login (open URL, paste code)
#    - WhatsApp QR scan (phone -> Linked Devices -> Link)
#    - Both saved to volumes, only needed once

# 3. Ctrl+P Ctrl+Q to detach. Running forever.
```

### Test it

DM your bot number on WhatsApp:
```
/start
```

It will send you polls to pick a game, group, and settings. Then it runs the game.

---

## Codebase overview

The entire framework is deliberately compact:

| File | Lines | ~Tokens | Purpose |
|---|---|---|---|
| `c3/agent.py` | 1,141 | ~8,200 | The framework -- MCP server, tools, sessions, memory, access control |
| `c3/baileys_bridge.js` | 299 | ~1,400 | WhatsApp bridge -- Baileys wrapper, JSON IPC |
| `c3/plugins/CLAUDE.md` | 100 | ~700 | Main agent router -- delegates to subagents |
| `c3/plugins/games/CLAUDE.md` | 178 | ~1,200 | Game master agent -- personality, flow, rules |
| `entrypoint.sh` | 132 | ~600 | Docker entrypoint -- auth flow, auto-accept |
| `c3/plugins/games/skills/*.md` | 14 files | ~4,000 | Game rules -- trivia, mafia, werewolf, etc. |

Total framework: **~1,440 lines of code**. Everything else is Markdown.

---

## How it works

```
Your phone (WhatsApp)
        |  Baileys protocol
        v
 baileys_bridge.js  <---- Node.js subprocess (bundled)
        |  JSON IPC (stdin/stdout)
        v
  c3-py MCP server  <---- Python (this package, 1141 LOC)
        |  channel notifications + tools
        v
   Claude Code CLI  <---- reads CLAUDE.md, routes to subagents
```

1. **c3-py** is an MCP server with channel support. It exposes tools (`reply`, `send_poll`, `set_timer`, `memory_write`, etc.) and pushes WhatsApp messages as channel notifications.
2. **Claude Code** connects via `--dangerously-load-development-channels` and uses those tools to run games, plan meals, set reminders, etc.
3. **Subagents** are registered at boot from `plugins/*/CLAUDE.md` via `--agents`. The main agent routes messages to the right subagent based on intent.
4. **Plugins** are directories containing `CLAUDE.md`, `skills/*.md`, optional `plugin.json` / `.mcp` / `.crons` / `.memory_schema`. No Python required.

---

## Trust boundaries and security

This project has real security implications. Be aware of the trust model:

### What you're trusting

| Component | Trust level | Risk |
|---|---|---|
| **Claude Code** | High -- runs with `--dangerously-skip-permissions` in Docker | Full tool execution without approval prompts |
| **Baileys** | Medium -- unofficial WhatsApp Web reimplementation | WhatsApp may ban linked accounts; session files = full account access |
| **Plugin Markdown** | Medium -- injected into Claude's context | Malicious plugins can influence Claude's behavior (second-order prompt injection) |
| **WhatsApp messages** | Low -- untrusted user input | Prompt injection attempts are sanitized but Claude is the last line of defense |

### What we do about it

- **Message sanitization** -- XML-like tags (`<channel>`, `<system>`) and prompt format markers (`Human:`, `Assistant:`) are stripped from incoming messages before they reach Claude
- **Access control** -- only `hosts` and `admins` in `config.json` can run commands. Group messages require an active session grant. JIDs are masked before Claude sees them.
- **Path traversal protection** -- `save_file` rejects paths that resolve outside the plugin directory
- **Memory validation** -- `memory_write` requires both `plugin` and `entity` fields to prevent accidental overwrites
- **Anonymous voting** -- poll updates show only vote counts during a round. Voter names are revealed only after the timer expires.
- **Host-only stop** -- only the host who started a session can stop it via the stop poll
- **No auto-join** -- `resolve_group` only reads group metadata. The bot must already be a member.

### What's NOT covered

- **Claude hallucination** -- Claude may invent vote tallies, miscount scores, or generate incorrect trivia answers. The framework provides real data via FINAL VOTES in timer expiry notifications, but Claude can still ignore it.
- **Baileys ban risk** -- WhatsApp actively detects and bans unofficial clients. Your linked phone number is at risk. Use a dedicated number.
- **`--dangerously-load-development-channels`** -- this is a Claude Code research preview flag. It may change or be removed without notice.
- **LLM as game arbiter** -- game correctness (role assignments, win conditions, scoring) is entirely Claude's responsibility. There is no deterministic validation layer.

---

## What it does

- **12 multiplayer games** -- Mafia, Trivia, Werewolf, Would You Rather, and more
- **Native WhatsApp polls** -- all choices use real polls, not text lists
- **Subagent routing** -- main agent delegates to specialized plugin agents (games, calendar, mealprep)
- **Plugin system** -- write a `.md` file, get a new capability. No code required
- **Memory** -- persistent SQLite store across sessions (player scores, preferences, plans)
- **Hot-reload** -- edit a plugin file, changes apply instantly
- **Mid-game join** -- new players auto-admitted when they message in an active group
- **Docker** -- `docker compose up`, authenticate, play

---

## Bundled plugins

### Games

| Game | Players | Description |
|---|---|---|
| `trivia` | 2+ | Poll-based quiz with live scoring |
| `mafia` | 5-20 | Classic deception game with DM roles |
| `werewolf` | 6-18 | Mafia variant with special roles |
| `would-you-rather` | 2+ | Poll-based dilemmas |
| `20-questions` | 2+ | Yes/no guessing game |
| `story-chain` | 2+ | Collaborative storytelling |
| `two-truths` | 3+ | Two truths and a lie |
| `never-have-i-ever` | 3+ | Points-based party game |
| `hot-seat` | 3+ | Player-in-the-spotlight Q&A |
| `speed-quiz` | 2+ | Fast-paced individual quiz |
| `poll-party` | 2+ | Pure poll game |
| `whodunit` | 4-12 | Collaborative mystery |

### Other plugins

| Plugin | Description |
|---|---|
| `calendar` | Scheduling, reminders, events |
| `mealprep` | Meal planning, grocery lists, prep schedules |

---

## Writing a custom plugin

Create `c3/plugins/games/skills/my-game.md`:

```markdown
# My Game

**Players:** 3-10 | **Style:** Poll-based

## How It Works
Each round, players vote on a category. Majority wins. 5 rounds total.

## Setup
setup_questions:
  - id: rounds
    prompt: "How many rounds?"
    options: ["3", "5", "7"]

## Win Condition
Player with the most majority picks wins.
```

No Python. Claude reads the Markdown and runs the game.

For non-game plugins, create `c3/plugins/my-plugin/` with `CLAUDE.md`, `plugin.json`, and `skills/*.md`. See [Plugin system](#plugin-system) below.

---

## Plugin system

Each plugin directory can contain:

| File | Purpose |
|---|---|
| `CLAUDE.md` | Agent system prompt -- loaded at boot, defines behavior |
| `plugin.json` | Access control (who can use commands, DM, group) |
| `skills/*.md` | Skill files -- loaded on demand via `load_plugin` or `/plugin add` |
| `.memory_schema` | JSON entity schemas for the memory tools |
| `.mcp` | Declare extra MCP subprocess servers |
| `.crons` | Scheduled jobs (cron expressions) |

---

## MCP tools

### Messaging

| Tool | Description |
|---|---|
| `reply` | Send a message (to host, group, or player by name) |
| `send_private` | DM a player privately |
| `send_poll` | Send a native WhatsApp poll |

### Groups

| Tool | Description |
|---|---|
| `get_group_members` | List group members |
| `resolve_group` | Resolve an invite link to a group JID |

### Session

| Tool | Description |
|---|---|
| `set_timer` | Start a countdown -- fires `timer_expired` with full vote tally |
| `end_session` | End session, clear timers, revoke access |

### Memory (SQLite)

| Tool | Description |
|---|---|
| `memory_write` | Upsert an entity (must include `plugin` and `entity` fields) |
| `memory_read` | Read entities, filter by plugin/entity_type |
| `memory_search` | Full-text search across all entities |
| `memory_delete` | Delete matching entities |

### Plugins

| Tool | Description |
|---|---|
| `load_plugin` | Load a plugin's skills + start its MCP proxy |
| `save_file` | Write content to the data directory (path-traversal protected) |

---

## WhatsApp commands

DM your bot number:

| Command | Description |
|---|---|
| `/start` | Start a new session -- polls for game/group selection |
| `/stop` | End the current session |
| `/status` | Show active sessions and timers |
| `/plugin list` | List available skill plugins |
| `/plugin add <name>` | Load a plugin mid-session |
| `/plugin remove <name>` | Unload a plugin |

Or just chat naturally: "let's play trivia", "plan my meals for the week", "remind me in 2 hours".

---

## Configuration

`config.json` in your plugin directory:

```json
{
  "hosts": [
    { "jid": "911234567890@s.whatsapp.net", "name": "Alice" }
  ],
  "admins": []
}
```

Your WhatsApp JID is `<country-code><number>@s.whatsapp.net`.

---

## Docker

```yaml
services:
  c3:
    build: .
    container_name: c3
    stdin_open: true
    tty: true
    volumes:
      - plugin:/plugin
      - claude-auth:/home/c3/.claude
    restart: unless-stopped

volumes:
  plugin:
  claude-auth:
```

Auth tokens (Claude + WhatsApp) persist in named volumes. Rebuild the image to update plugins; volumes are untouched.

---

## Architecture decisions

- **Single MCP server** -- Claude connects to one server (c3-py). All tools flow through it. Plugin MCP servers are proxied.
- **Polls over text** -- every choice uses native WhatsApp polls. Never text lists.
- **Anonymous mid-round votes** -- poll updates show counts only. Voter names revealed only when the timer expires with FINAL VOTES data.
- **Auto-admit** -- new players joining a group mid-game are automatically granted access.
- **LID mapping** -- WhatsApp's linked ID format is automatically translated to phone JIDs via group metadata.
- **JID masking** -- phone numbers are replaced with tokens (host, group, player names) before Claude sees them.
- **Prompt sanitization** -- incoming messages are stripped of XML-like tags and prompt format markers.

---

## Development

```bash
git clone https://github.com/vaddisrinivas/c3-py && cd c3-py
uv sync --all-extras
uv run pytest              # 90 tests
uvx ruff check c3/ tests/  # lint
uvx ruff format c3/ tests/ # format
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full guide.

---

## License

[MIT](LICENSE)
