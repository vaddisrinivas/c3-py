# Trivia

**Players:** 2–50 | **Style:** Poll-based quiz, points

## How It Works

Each question is sent as a `send_poll` with 4 options (exactly 1 correct). Players vote before the timer fires. On `phase_expired`, reveal the correct answer and award 1 point to **every player who voted for the correct answer** — not just the first voter. All correct answers get a point.

## Phases

1. **Question** (default 30s)
   Generate a question from the chosen topic. Send as poll with 4 options. Call `set_phase_timer`.

2. On `phase_expired`:
   Announce the correct answer. Award points to all correct voters. Post updated leaderboard in group. Repeat until all questions done.

## Win Condition

Player with the most points after all questions wins. On a tie, both are declared co-winners.

## Setup

```yaml
min_players: 2
max_players: 50
setup_questions:
  - id: topic
    prompt: "🧠 What topic for Trivia?"
    options: ["General Knowledge", "Science & Tech", "History", "Movies & TV", "Sports", "Agentic AI", "Pop Culture", "Geography"]
    default: "General Knowledge"
  - id: question_count
    prompt: "🔢 How many questions?"
    options: ["3", "5", "7", "10"]
    default: "5"
  - id: phase_duration
    prompt: "⏱️ How long per question?"
    options: ["15s", "30s", "45s", "60s"]
    default: "30s"
```

## Instructions

Generate questions yourself — never hardcode, never repeat. Each question must have exactly 1 correct and 3 plausible-but-wrong options. **Be 100% certain of the correct answer before sending.** If you are not confident, pick a different question. Track scores as `{name: points}` in context. Post leaderboard after every question.

## Scoring Rules

- **Every player who votes for the correct answer gets 1 point** — not just the first voter
- If 5 players all vote correctly, all 5 get a point
- Speed does not matter — only correctness
- Players who don't vote before the timer get 0 points for that round
