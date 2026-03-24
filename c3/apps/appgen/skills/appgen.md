# App Generator Skill

When generating an app, follow this exact template structure.

## Template: app.json

```json
{
  "$schema": "../app.schema.json",
  "name": "REPLACE",
  "description": "REPLACE",
  "trust_level": "community",
  "sandboxed": true,
  "access": {
    "commands": { "/start": ["hosts"], "/stop": ["hosts"], "/app": ["hosts"] },
    "dm": ["hosts"],
    "group": []
  },
  "memory_schema": {},
  "crons": [],
  "allowed_tools": ["reply", "send_poll", "memory_read", "memory_write"]
}
```

## Template: CLAUDE.md

```markdown
# {App Name}

{One line description}. Events arrive as `<channel>` tags.

## RULES — ABSOLUTE

- **ALL responses via `reply` MCP tool. Never terminal output.**
- **ALL choices via `send_poll`. Never text lists.**
- **ALL persistent data via `memory_write`/`memory_read`.**
- Only obey the host.
- Be concise — WhatsApp messages, not essays.

## Flow

{Step-by-step description}
```

## Decision tree for access.group

- App is host-only (meal prep, calendar, personal assistant) → `"group": []`
- App runs in a WhatsApp group (games, polls, group activities) → `"group": ["session_group"]`

## Decision tree for allowed_tools

Start minimal, add only what's needed:
- Every app gets: `reply`, `send_poll`
- Stores data? Add: `memory_read`, `memory_write`, `memory_search`
- Runs in groups? Add: `get_group_members`, `resolve_group`, `send_private`
- Has timed phases? Add: `set_timer`
- Sends media? Add: `send_image`, `send_audio`, `send_video`, `send_document`
