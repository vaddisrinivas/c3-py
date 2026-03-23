# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2025-01-01

### Added

- **MCP server** — exposes WhatsApp I/O as Model Context Protocol tools so any MCP-compatible LLM can drive the bot
- **Baileys bridge** — `c3/baileys_bridge.js` connects to WhatsApp Web via the Baileys library; the Python process communicates with it over a local WebSocket
- **12 bundled games** — out-of-the-box social games for group chats: Trivia, Mafia, Werewolf, Whodunit, Two Truths & a Lie, Never Have I Ever, Would You Rather, Hot Seat, Story Chain, Speed Quiz, Poll Party, and 20 Questions
- **Plugin system** — drop a `plugin.json` manifest and Markdown prompt files into a directory to add custom commands and games without touching core code
- **Setup wizard** (`c3-py setup`) — interactive CLI that handles first-run configuration: scans for sessions, links a WhatsApp account via QR code, and writes the config file
- **Docker support** — `Dockerfile` and `docker-compose.yml` for containerised deployment

[Unreleased]: https://github.com/vaddisrinivas/c3-py/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/vaddisrinivas/c3-py/releases/tag/v0.1.0
