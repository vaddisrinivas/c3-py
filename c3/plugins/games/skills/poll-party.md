# Poll Party

**Players:** 2–50 | **Style:** Rapid-fire "this or that", consensus score

## How It Works

Rapid-fire "this or that" / "which is better" polls. After each vote, reveal the split and track who voted with the majority. Most consensus votes at the end wins.

## Phases (per round)

1. **Vote** (default 15s) — Send `send_poll` with a fun comparison: e.g. "Pizza or Tacos?", "Morning person or Night owl?", "Dogs or Cats?"
2. **Reveal** — Announce the majority choice and split. Add 1 "consensus point" to everyone who voted with the majority.

Repeat for all rounds. Most consensus points wins.

## Win Condition

Player with the most majority-side votes wins. They're crowned "Most Normal Person 🧠" (ironically).

## Setup

```yaml
min_players: 2
max_players: 50
setup_questions:
  - id: theme
    prompt: "🎭 What theme?"
    options: ["Mixed", "Food & Drinks", "Movies & Music", "Lifestyle", "Tech & Gadgets", "Travel", "Sports"]
    default: "Mixed"
  - id: round_count
    prompt: "🔢 How many rounds?"
    options: ["5", "8", "10", "15", "20"]
    default: "10"
  - id: phase_duration
    prompt: "⏱️ How long per poll?"
    options: ["10s", "15s", "20s", "30s"]
    default: "15s"
```

## Instructions

Generate fun, universally relatable comparisons. Keep them punchy and quick. Mix categories within the theme. Avoid controversial or divisive topics. After each reveal, add a one-liner reaction ("Tacos win with 70%! The people have spoken 🌮").
