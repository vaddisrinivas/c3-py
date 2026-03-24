# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

**c3-py** is a WhatsApp AI agent framework. Claude runs apps defined as Markdown + JSON, served via MCP (FastMCP 3.1), bridged to WhatsApp through a Node.js Baileys subprocess.

## Commands

```bash
# Setup
uv sync --all-extras
cd c3 && npm install

# Tests
uv run pytest                          # all 120+ tests
uv run pytest tests/test_foo.py        # single file
uv run pytest tests/test_foo.py::test_bar  # single test
uv run pytest -x -s                    # stop on first failure, show output

# Lint / format
uvx ruff check c3/ tests/
uvx ruff format c3/ tests/

# Run locally
uv run python -m c3

# Docker
docker compose up -d                   # production
docker compose -f docker-compose.test.yml up  # integration tests
```

## Architecture

### 3-Layer Stack

```
WhatsApp (Baileys Node.js) ←→ Python Core (MCP/FastMCP) ←→ Apps (Markdown + JSON)
```

1. **Baileys bridge** (`c3/baileys_bridge.js`): Node.js subprocess handling WhatsApp Web protocol, QR auth, media download. Communicates with Python over stdio JSON lines.
2. **Python core** (`c3/agent.py`, ~2500 lines): All logic lives here — RBAC, session engine, MCP tool/resource definitions, ChannelCore orchestrator, BaileysAdapter subprocess wrapper.
3. **Apps** (`c3/apps/*/`): Declarative — `app.json` (config/capabilities), `CLAUDE.md` (system prompt), `skills/` (optional Markdown skill files). Lazy-loaded on demand.

### Key Classes in `agent.py`

| Class | Role |
|-------|------|
| `ChannelCore` | Orchestrates everything: receives messages, manages active app, dispatches to SessionEngine |
| `SessionEngine` | Session lifecycle, timer management, app transitions |
| `AccessControl` | RBAC enforcement — host-only vs participant, trust level tagging |
| `ChatAdapter` (ABC) | Interface between Python and WhatsApp transport |
| `BaileysAdapter` | Concrete adapter wrapping the Node.js subprocess |
| `TestAdapter` | Mock adapter used in unit/integration tests (see `c3/test_adapter.py`) |

### App Lifecycle

1. `load_app` MCP tool called → finds app dir, reads `app.json`, validates against `c3/app.schema.json`
2. `CLAUDE.md` becomes the system prompt; skill files injected as tool descriptions
3. `allowed_tools` / `allowed_resources` in `app.json` gate which MCP tools the app can invoke
4. Memory scoped per-app under `c3://memory/{app}/`

### Security Model

- **RBAC**: host-only operations vs participant-accessible, enforced by `AccessControl`
- **Trust tagging**: `[host]` / `[participant]` labels injected into message context
- **Single active group**: framework enforces one WhatsApp group at a time
- **Input sanitization**: XML tags and prompt injection patterns stripped before Claude sees them
- **Sandboxing**: `app.json` `allowed_tools`/`allowed_resources` glob-gated; marketplace installs auto-generate sandboxed configs

### Configuration

Priority order: `C3_*` env vars → `c3/c3.yaml` → `c3/c3.json` → defaults.
`c3/c3.yaml` is the canonical config file. Key sections: `server` (host/port/model), `timeouts`, `defaults`.

### Test Patterns

- `asyncio_mode = auto` (pytest-asyncio)
- Use `TestAdapter` / `FakeWAAdapter` for injecting WhatsApp messages without a real connection
- Integration tests in `tests/test_integ_*.py` test RBAC, security, session, and tool execution end-to-end
- `tests/conftest.py` has shared fixtures

### MCP Tools (20+)

Messaging: `reply`, `send_private`, `send_poll`, `react`
Media: `send_image`, `send_audio`, `send_video`, `send_document`
Groups: `get_group_members`, `resolve_group`
Session: `set_timer`, `end_session`
Memory: `memory_read`, `memory_write`, `memory_search`, `memory_delete`
Apps: `load_app`

### MCP Resources

`c3://schema/app` — app JSON Schema
`c3://memory/{app}` — app memory entities
`c3://media/{messageId}` — downloaded media
