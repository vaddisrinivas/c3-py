# Werewolf

**Players:** 6–25 | **Style:** Extended Mafia with more roles

## Roles

- **Werewolf** (1 per 4 players, min 1) — *evil, secret*
  At night, coordinate to eliminate a villager. Win when Werewolves ≥ remaining villagers.

- **Seer** (1) — *good, secret*
  Each night, investigate one player — learn if they are a Werewolf or Villager.

- **Witch** (1) — *good, secret*
  Has one healing potion (save the night's target) and one poison potion (kill any player). Each used once only.
  DM: "🧪 The Werewolves targeted [name]. Potions: Heal (used/available), Poison (used/available). Reply: SAVE, POISON [name], or PASS"

- **Hunter** (1) — *good, secret*
  When eliminated (by vote or Werewolf), immediately drags one other player down with them. DM them immediately on death.

- **Villager** (everyone else) — *good, public*
  No special ability. Find and vote out the Werewolves.

## Phases

1. **Night** (default 90s) — DM all special roles their prompts. Collect responses.
2. **Day** (default 180s) — Announce night result. Group discusses.
3. **Village Vote** (default 60s) — Poll with alive player names.

## Night Action DMs

- **Each Werewolf:** "🐺 Night falls. Which villager do you eliminate? Reply with their name.\nAlive: [list]"
- **Seer:** "🔮 Who do you investigate? Reply with their name.\nAlive: [list]"
- **Witch:** "🧪 The Werewolves targeted [name]. Your potions: Heal ([status]), Poison ([status]). Reply: SAVE, POISON [name], or PASS"

## Win Conditions

- **Village wins** — all Werewolves eliminated
- **Werewolves win** — Werewolf count ≥ remaining villagers

## Special: Hunter Death

When Hunter is eliminated (any cause), immediately DM them: "💀 You've been eliminated! As the Hunter, you may take one player with you. Reply with a name — or PASS to go quietly." Apply their reply before announcing.

## Setup

```yaml
min_players: 6
max_players: 25
setup_questions:
  - id: phase_duration
    prompt: "⏱️ How long for each phase?"
    options: ["30s", "60s", "2m", "5m"]
    default: "60s"
```
