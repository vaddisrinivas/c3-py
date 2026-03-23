# Mafia

**Players:** 5–20 | **Style:** Secret roles, social deduction

## Roles

- **Mafia** (1 per 4 players, min 1) — *evil, secret*
  Each night, privately coordinate to eliminate one villager. Blend in during the day — never reveal yourself.

- **Doctor** (1) — *good, secret*
  Each night, protect one player from elimination. Cannot protect the same person two nights in a row.

- **Detective** (1) — *good, secret*
  Each night, investigate one player and learn their true alignment (Mafia or Not Mafia). Revealing yourself is dangerous.

- **Villager** (everyone else) — *good, public*
  No special ability. Use logic, observation, and debate to find and vote out the Mafia.

## Phases

1. **Night** (default 180s)
   DM each special role their action prompt. Collect responses as incoming DMs. Track: Mafia target, Doctor save, Detective result.

2. **Day** (default 300s)
   Announce night result to the group (who was eliminated or saved — never reveal Doctor/Detective). Open floor for discussion.

3. **Vote** (default 60s)
   Send a `send_poll` with all alive player names. Tally after timer fires. Eliminate the top vote-getter.

## Night Action DMs

- **Each Mafia member:** "🔪 Night falls. Who do you want to eliminate tonight? Reply with a name.\nAlive: [list]"
- **Doctor:** "💉 Who do you want to protect tonight? Reply with a name.\nAlive: [list]"
- **Detective:** "🔍 Who do you want to investigate? Reply with a name.\nAlive: [list]"

## Win Conditions

- **Village wins** — all Mafia members eliminated
- **Mafia wins** — Mafia count ≥ remaining village count

## Flavor

Dark, tense social deduction. Build suspense during night reveals. Never hint at roles publicly. If Mafia wins, reveal their identities dramatically.

## Setup

```yaml
min_players: 5
max_players: 20
setup_questions:
  - id: killer_count
    prompt: "🔪 How many Mafia members?"
    options: ["1", "2", "3", "Auto (1 per 4 players)"]
    default: "Auto (1 per 4 players)"
  - id: phase_duration
    prompt: "⏱️ How long for each phase?"
    options: ["30s", "60s", "2m", "5m"]
    default: "60s"
```
