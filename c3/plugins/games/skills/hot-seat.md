# Hot Seat

**Players:** 3–15 | **Style:** Group interview, rotating spotlight

## How It Works

One player sits in the Hot Seat. The group votes on which question to ask them. The hot-seat player must answer in the group chat — honestly! Rotate through all players.

## Phases (per player's turn)

1. **Pick a Question** (default 20s)
   Generate 4 questions suited to the chosen vibe. Send `send_poll`: "🔥 Which question for [name]?" with those 4 options.

2. **Answer** (60s free chat)
   Announce the winning question loudly: "🔥 [Name], the group wants to know: [question]"
   Wait for their answer. React and comment. Then move to the next player.

After all players have been in the hot seat, call `end_game`.

## Win Condition

No winner — everyone survives the hot seat. Call it a "🔥 Hot Seat complete!" moment.

## Setup

```yaml
min_players: 3
max_players: 15
setup_questions:
  - id: vibe
    prompt: "🎭 What's the vibe?"
    options: ["Fun & Silly", "Deep & Personal", "Would You Rather style", "Embarrassing Moments", "Unpopular Opinions"]
    default: "Fun & Silly"
  - id: questions_per_player
    prompt: "🔢 Questions per player?"
    options: ["2", "3", "4", "5"]
    default: "3"
  - id: phase_duration
    prompt: "⏱️ How long to vote on each question?"
    options: ["15s", "20s", "30s", "45s"]
    default: "20s"
```

## Instructions

Generate questions fresh each round based on the vibe. Match questions to the tone: silly vibes get absurd questions, deep vibes get introspective ones. React warmly to answers — this is a bonding game. If someone's shy, make it easier on them.
