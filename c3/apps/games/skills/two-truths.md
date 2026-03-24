# Two Truths and a Lie

**Players:** 3–20 | **Style:** Bluffing, social, no elimination

## How It Works

Each player takes a turn in the spotlight. They privately DM you 3 statements about themselves (2 true, 1 lie). You post all 3 in the group (anonymized or attributed — host decides). The group votes on which is the lie.

## Phases (per player)

1. **Submit** (120s) — DM the spotlight player: "🤥 Send me 3 statements — 2 true, 1 lie. Number them 1, 2, 3." Wait for their reply.
2. **Vote** (default 30s) — Post the 3 statements in group. Send `send_poll`: "Which statement is [name]'s lie?" with options "Statement 1", "Statement 2", "Statement 3".
3. **Reveal** — Announce the correct lie. Award points:
   - Guesser gets +1 for each correct vote
   - Spotlight player gets +1 for each person they fooled (wrong vote)

Repeat for every player. Call `end_game` when everyone has had a turn.

## Win Condition

Player with the most points after all rounds wins. Ties allowed.

## Setup

```yaml
min_players: 3
max_players: 20
setup_questions:
  - id: phase_duration
    prompt: "⏱️ How long to vote each round?"
    options: ["20s", "30s", "45s", "60s"]
    default: "30s"
```

## Flavor

Keep it light and funny. After the reveal, ask the spotlight player to share the story behind one of their truths. It sparks great conversation.
