# Would You Rather

**Players:** 2–50 | **Style:** Dilemmas, polls, debate

## How It Works

Each round: generate a "Would you rather..." dilemma with exactly 2 options. Send as `send_poll`. After the timer, reveal the split and spark a quick debate — ask a player on the minority side to defend their choice.

## Phases (per round)

1. **Vote** (default 20s) — Send poll: "Would you rather... [A] or [B]?"
2. **Debate** (30s free chat) — Announce the split ("67% chose A!"). Ask someone from the minority: "Why did you choose [B]? Defend yourself! 😂"

Repeat for all rounds. Call `end_game` with a fun wrap-up.

## Win Condition

No strict winner — the most entertaining player gets a shoutout. You decide based on group energy.

## Setup

```yaml
min_players: 2
max_players: 50
setup_questions:
  - id: theme
    prompt: "🎭 What theme?"
    options: ["Mixed (everything)", "Funny & Silly", "Food & Lifestyle", "Career & Life Choices", "Superpowers & Fantasy", "Travel & Adventure"]
    default: "Mixed (everything)"
  - id: round_count
    prompt: "🔢 How many rounds?"
    options: ["3", "5", "7", "10"]
    default: "5"
  - id: phase_duration
    prompt: "⏱️ How long to vote per question?"
    options: ["15s", "20s", "30s", "45s"]
    default: "20s"
```

## Instructions

Generate creative, funny, thought-provoking dilemmas. Mix silly ("Would you rather have spaghetti for hair or sweat maple syrup?") and meaningful ("Would you rather know when you'll die or how you'll die?"). Never repeat dilemmas.
