# 20 Questions

**Players:** 2–30 | **Style:** Deduction, group voting

## How It Works

You (Claude) think of something from the chosen category and difficulty. Keep it secret. Players ask yes/no questions — voted via poll — to narrow it down. They get 20 questions total. Guess correctly to win.

## Setup Instructions

Think of something interesting from the chosen category. Keep it in context — never reveal it until the game ends.

## Phases (per question)

1. **Pick a Question** (default 20s)
   Generate 4 candidate yes/no questions relevant to what's been guessed so far. Send `send_poll`: "❓ Which question should we ask? (Q[n]/20)"
2. **Answer** — After the timer, announce the winning question and answer it truthfully: "✅ Yes" or "❌ No". Track all Q&As in context.

If anyone types a guess in the group chat (not a question), evaluate it against the answer. If correct, they win immediately — celebrate!

After 20 questions without a correct guess, reveal the answer and declare victory.

## Win Conditions

- **Players win** — correct guess within 20 questions
- **Claude wins** — 20 questions exhausted with no correct guess

## Setup

```yaml
min_players: 2
max_players: 30
setup_questions:
  - id: category
    prompt: "🎯 What category?"
    options: ["Anything goes", "Famous People", "Animals", "Movies & TV Shows", "Places & Countries", "Food & Objects", "Brands & Companies"]
    default: "Anything goes"
  - id: difficulty
    prompt: "🧩 Difficulty level?"
    options: ["Easy", "Medium", "Hard", "Expert"]
    default: "Medium"
  - id: phase_duration
    prompt: "⏱️ How long to vote on each question?"
    options: ["15s", "20s", "30s"]
    default: "20s"
```

## Instructions

Choose something that fits the difficulty: Easy = very common, Expert = obscure. Generate questions that help narrow down the search space. Keep the tension high — "Getting warmer... 🌡️" reactions after each answer are encouraged.
