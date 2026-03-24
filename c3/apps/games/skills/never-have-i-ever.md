# Never Have I Ever

**Players:** 3–30 | **Style:** Social, voting, no elimination

## How It Works

Each round: post a "Never have I ever..." statement in the group. Send `send_poll` with two options: "I HAVE 🙋" and "Never done it 😇". After the timer, reveal who voted what and react dramatically.

## Phases (per round)

1. **Vote** (default 15s) — Send poll: "Never have I ever... [statement]" with options ["I HAVE 🙋", "Never done it 😇"]
2. **Reveal** — Call out who voted "I HAVE" by name (from poll tally). React with appropriate drama. +1 point for each person who HAVE'd.

Repeat for all rounds. Most adventurous player (most HAVEs) wins.

## Win Condition

Player with the most "I HAVE" votes across all rounds wins. Crown them "Most Adventurous 🏆".

## Setup

```yaml
min_players: 3
max_players: 30
setup_questions:
  - id: theme
    prompt: "🎭 What theme?"
    options: ["Mixed", "Travel & Adventure", "Food & Weird Habits", "Work & Career", "Social Situations", "Embarrassing Moments"]
    default: "Mixed"
  - id: round_count
    prompt: "🔢 How many rounds?"
    options: ["5", "7", "10", "15", "20"]
    default: "10"
  - id: phase_duration
    prompt: "⏱️ How long to vote per statement?"
    options: ["10s", "15s", "20s", "30s"]
    default: "15s"
```

## Instructions

Generate fun, social, relatable statements. Mix silly ("Never have I ever eaten food off the floor"), adventurous ("Never have I ever hitchhiked"), and universal ("Never have I ever fallen asleep in a meeting"). Avoid anything too personal or offensive. Keep energy high with reactions after each reveal.
