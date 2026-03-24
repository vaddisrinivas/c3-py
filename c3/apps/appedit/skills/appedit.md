# App Editor Skill

## Valid tool names (for allowed_tools)

When editing an app's allowed_tools, only these exact names are valid:
- `reply` — send text message
- `send_poll` — WhatsApp native poll
- `send_private` — DM a specific person
- `get_group_members` — list group members
- `resolve_group` — resolve invite link to group JID
- `set_timer` — countdown timer
- `end_session` — end active session
- `memory_read` — read from persistent memory
- `memory_write` — write to persistent memory
- `memory_search` — search across memory
- `memory_delete` — delete memory entries
- `send_image` — send image file
- `send_audio` — send audio / voice note
- `send_video` — send video file
- `send_document` — send document / file
- `react` — react to message with emoji
- `load_app` — load another app's skills
- `save_file` — write file to data directory

## Valid access roles

- `hosts` — the trusted host user
- `session_group` — the active WhatsApp group
- `session_participants` — members of the active group
- `elevated_participants` — participants approved for DM access

## Trust level rules

- `builtin` — core apps only, never set this for user apps
- `verified` — reviewed marketplace apps
- `community` — user-generated or unreviewed
- `untrusted` — unknown source, maximum restrictions

## Editing checklist

Before saving changes, verify:
1. `name` is kebab-case lowercase
2. `trust_level` is not being escalated (community → builtin is blocked)
3. `sandboxed` is not being disabled for community apps
4. `allowed_tools` only contains valid tool names from the list above
5. `access.dm` includes `"hosts"` (host must always have DM access)
6. CLAUDE.md still has the mandatory rules section
