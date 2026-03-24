# Contributing to c3-py

Thanks for your interest in contributing. This guide covers everything you need to get up and running.

## Dev setup

```bash
git clone https://github.com/vaddisrinivas/c3-py.git
cd c3-py
uv sync --all-extras
```

Install Node dependencies for the Baileys bridge:

```bash
cd c3
npm install
cd ..
```

Run the test suite:

```bash
uv run pytest
```

## Code style

We use [ruff](https://docs.astral.sh/ruff/) for both linting and formatting.

```bash
uvx ruff check c3/ tests/      # lint
uvx ruff format c3/ tests/     # format in place
uvx ruff format --check c3/ tests/  # CI-style check (no writes)
```

There is no separate `.ruff.toml` — defaults are intentionally used to keep things simple.

## Testing

Tests live in `tests/` and run with pytest. `asyncio_mode = auto` is set in `pyproject.toml`, so async test functions work without any extra decorators.

To add a test, create a file matching `tests/test_*.py`. Use `pytest-mock` fixtures for patching WhatsApp I/O; avoid hitting the real Baileys bridge in unit tests.

```bash
uv run pytest -x -q           # stop on first failure, quiet output
uv run pytest tests/test_foo.py  # run a single file
```

## Project structure

`c3/agent.py` is the main module. Key sections (marked with banner comments):

| Section | What it does |
|---|---|
| `Types` | Core dataclasses: `WAMessage`, `AppConfig`, `AppManifest`, etc. |
| `Utilities` | Logging helpers, `parse_duration`, `pick` |
| `RBAC` | `AccessControl` — role-based access, JID masking, trust tagging |
| `SessionEngine` | Per-chat session lifecycle (commands, polls, timers) |
| `MCP Tools & Core` | Tool definitions, `ChannelCore`, memory helpers |
| `Manifests` | Default manifest, merge logic |
| `BaileysAdapter` | WebSocket bridge to the Baileys JS process |
| `Channel` | `create_channel()` wires everything together |
| `Entry point` | CLI commands: `init`, `auth`, `setup`, `check` |

Bundled apps live in `c3/apps/` (e.g. `c3/apps/games/`) as Markdown prompt files and JSON configs, loaded at startup.

## Pull request guidelines

- **One feature or fix per PR.** Large, mixed PRs are hard to review.
- **Tests are required.** New behaviour without tests will not be merged.
- **Update `CHANGELOG.md`** under the `[Unreleased]` section using Keep a Changelog format.
- Keep commit messages short and in the imperative mood ("add X", "fix Y").
- If you're adding a game, drop a `.md` file in `c3/apps/games/skills/` and reference it in the PR description.
