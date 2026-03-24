# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-03-23

### Added

- **Host-only RBAC** — single trusted user model, no admin hierarchy. `AccessControl` class replaces `AppController` + `JidMask` + `AppSession`
- **Role tagging** — every message prefixed `[host]` or `[participant]` so Claude sees trust level inline, with few-shot examples in system prompt
- **DM elevation with host approval** — Claude can't DM participants without host voting "Yes" on a poll first
- **Single active group enforcement** — only one WhatsApp group active at a time, enforced in code
- **Send protection** — `reply`/`send_private` blocked to non-host, non-group, non-participant JIDs at tool level
- **MCP resources** — `c3://schema/app` (JSON Schema), `c3://memory/{plugin}` (read entities), `c3://media/{messageId}` (downloaded media)
- **Resource RBAC** — `allowed_resources` glob patterns in app.json, enforced at `read_resource()`
- **App Generator** (`appgen`) — create new apps via WhatsApp conversation, every config decision via polls
- **App Editor** (`appedit`) — edit existing apps (access, tools, prompts, memory) via WhatsApp polls
- **WhatsApp media support** — receive and send images, video, audio, voice notes, documents, stickers
- **Message type support** — location, live location, link previews, multi-contact cards, events
- **Catchup system** — on reconnect, process missed messages via `/catchup` command
- **Persistent sessions** — `--resume` keeps Claude context across restarts, `/clear` to reset
- **Restart notifications** — Claude sees "SESSION RESUMED (restart #N)" with restart count
- **Haiku default model** — faster, cheaper responses for WhatsApp's conversational pace
- **JSON Schema for app.json** — auto-generated from Pydantic model, referenced via `$schema` in all app configs
- **Marketplace safe defaults** — `c3-py app install` auto-generates `app.json` with host-only access + minimal tools if missing
- **Typed models** — `WAMessage.media_type` uses `Literal` types, `AppManifest.trust_level` uses enum

### Changed

- **Renamed** everything: `plugins/` → `apps/`, `plugin.json` → `app.json`, `load_plugin` → `load_app`, memory field `plugin` → `app`, resource URIs `c3://memory/{plugin}` → `c3://memory/{app}`
- **Removed** `admins` role — backwards compatible (mapped to `hosts`) but no longer a distinct concept
- **Rewrote** all CLAUDE.md files (main + games + calendar + mealprep) — strict system-level instructions with mandatory tool usage rules
- **Consolidated** `JidMask` + `AppController` + `AppSession` → single `AccessControl` class
- **Tool allowlist enforcement** — `allowed_tools` now checked at `call_tool()` (was declaration-only before)

### Removed

- `AppSession` class (grant/revoke moved to `AccessControl` directly)
- `JidMask` as standalone class (merged into `AccessControl`)
- `admins` as a distinct role concept

## [0.1.0] - 2025-01-01

### Added

- **MCP server** — exposes WhatsApp I/O as Model Context Protocol tools
- **Baileys bridge** — Node.js subprocess connecting to WhatsApp Web
- **12 bundled games** — Trivia, Mafia, Werewolf, and more
- **Plugin system** — Markdown + JSON app structure
- **Setup wizard** (`c3-py setup`) — interactive first-run configuration
- **Docker support** — Dockerfile and docker-compose.yml

[Unreleased]: https://github.com/vaddisrinivas/c3-py/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/vaddisrinivas/c3-py/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/vaddisrinivas/c3-py/releases/tag/v0.1.0
