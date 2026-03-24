# Speed Quiz

**Players:** 2–30 | **Style:** Fast-fire, type-to-answer, no polls

## How It Works

Post a question in the group. **No polls** — players type their answers directly in the chat. First person to type the correct answer wins the point. Speed AND accuracy matter.

## Phases (per question)

1. Post question in group: "⚡ Q[n]: [question]" — start watching incoming messages
2. The first `type="message"` from any player with the correct answer (exact or close enough) wins the point. Award immediately: "✅ [Name] gets it! +1 point 🏆"
3. If no one answers within `phase_duration`, reveal the answer and move on.

Call `set_phase_timer` for each question. On `phase_expired` if no correct answer yet — reveal and continue.

## Win Condition

Player with the most points after all questions wins.

## Setup

```yaml
min_players: 2
max_players: 30
setup_questions:
  - id: topic
    prompt: "🧠 What topic?"
    options: ["General Knowledge", "Science", "History", "Geography", "Movies & TV", "Sports", "Music", "Food", "Agentic AI & Tech"]
    default: "General Knowledge"
  - id: question_count
    prompt: "🔢 How many questions?"
    options: ["5", "7", "10", "15", "20"]
    default: "10"
  - id: phase_duration
    prompt: "⏱️ How long per question?"
    options: ["10s", "15s", "20s", "30s", "45s"]
    default: "20s"
```

## Instructions

Generate questions with clear, short answers (1–3 words ideal). Accept reasonable variations (e.g. "Eiffel Tower" and "The Eiffel Tower" both correct). Post the leaderboard every 3 questions and after the final question. Keep the pace fast and energetic.
