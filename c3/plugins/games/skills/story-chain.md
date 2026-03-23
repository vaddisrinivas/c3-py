# Story Chain

**Players:** 2–30 | **Style:** Collaborative storytelling, voting

## How It Works

You (Claude) open with a compelling story start. Each chapter, you present 3–4 choices for what happens next. Players vote. You narrate the chosen path beautifully, then present the next choice. Build a complete story together.

## Setup Instructions

Generate a gripping opening paragraph for the chosen genre. End with a cliffhanger that sets up the first choice.

## Phases (per chapter)

1. **Narrate** — Post the latest story development in the group (2–3 vivid sentences).
2. **Vote** (default 30s) — Present 3–4 genuinely different choices. Send `send_poll`: "📖 What happens next?" with those options.
3. After `phase_expired` — Narrate the winning choice. Make it feel earned. Then repeat.

After all chapters, write a satisfying conclusion incorporating the major choices made. Call `end_game`.

## Win Condition

Everyone wins — it's a collaborative story. Celebrate the tale they built together.

## Setup

```yaml
min_players: 2
max_players: 30
setup_questions:
  - id: genre
    prompt: "📚 What genre?"
    options: ["Adventure & Fantasy", "Mystery & Thriller", "Sci-Fi & Future", "Romance & Drama", "Horror & Suspense", "Comedy & Absurd", "Historical"]
    default: "Adventure & Fantasy"
  - id: chapters
    prompt: "📖 How many chapters?"
    options: ["5", "6", "8", "10", "12"]
    default: "8"
  - id: phase_duration
    prompt: "⏱️ How long to vote per chapter?"
    options: ["20s", "30s", "45s", "60s"]
    default: "30s"
```

## Instructions

Build narrative continuity — remember every choice made and weave them together. Each choice should feel meaningfully different, not just cosmetically. Build toward a satisfying arc. In Horror, raise the stakes. In Comedy, escalate the absurdity. Make the players feel like co-authors.
