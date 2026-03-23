# Whodunit

**Players:** 4–15 | **Style:** Murder mystery, secret roles

## Setup Instructions

When the game starts, create a vivid murder mystery:
- A location (from host's choice or random)
- A victim name and cause of death
- A motive hidden among the suspects
- Private clues for each role that collectively point to the killer

## Roles

- **Detective** (1) — *good, secret*
  Lead the investigation. Interrogate any suspect by DMing them directly (they reply in their DM chat — you relay clues to the group without revealing the source). Find the killer!
  DM: "🔍 You're the Detective! Interrogate suspects by messaging them in this chat. Their identities are: [list]. Gather clues and guide the group."

- **Killer** (1) — *evil, secret*
  You committed the crime. You know your motive and prepared an alibi. Deflect suspicion, mislead the detective, blend in.
  DM: "🔪 You're the Killer. Motive: [generated motive]. Alibi: [generated alibi]. Act innocent."

- **Witness** (1) — *good, secret*
  You saw something suspicious but aren't sure what it means. Share clues when questioned — but be careful who you trust.
  DM: "👁️ You're the Witness. You saw: [generated clue]. Share this carefully."

- **Suspect** (everyone else) — *neutral, public*
  Person of interest. Answer questions honestly — you have nothing to hide.

## Phases

1. **Crime Discovery** (60s) — Post the crime scene description in group. DM each role their private info.
2. **Investigation** (default 3m) — Detective interrogates via DM. Group discusses in chat.
3. **Final Accusation** (60s) — Send poll with all player names: "🔪 Who is the killer?"

## Win Conditions

- **Village wins** — killer identified in the vote
- **Killer wins** — killer not identified (escapes)

## Instructions

Generate all mystery details at game start — location, victim, motive, clues. Make it solvable but not obvious. The Killer's alibi should have one small hole the Detective can find. Clue chain: Witness → Detective → Group.

## Setup

```yaml
min_players: 4
max_players: 15
setup_questions:
  - id: setting
    prompt: "🏠 Where does the mystery take place?"
    options: ["Mansion Party", "Corporate Office", "Cruise Ship", "Mountain Cabin", "Museum Gala", "Tech Conference", "Random"]
    default: "Random"
  - id: phase_duration
    prompt: "⏱️ How long for the investigation phase?"
    options: ["2m", "3m", "5m", "7m"]
    default: "3m"
```
