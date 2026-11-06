# ValCoach

ValCoach helps individual VALORANT players improve through replay coaching grounded in what happened in their match. The goal is to understand the decisions behind a round and leave with something specific to practise in the next game.

The experience is a conversation with an AI agent. Ask for a match review, focus on attack or defense, or dig into a particular fight. The agent investigates the replay, considers your explanation and compares possible decisions using the information available at the time. The initial focus is ranked Jett play.

## How it works

1. **Decode the replay.** vrfkit converts a `.vrf` replay into a local Parquet export. This step is currently managed separately.
2. **Build the evidence.** ValCoach reconstructs round events, fights, player state and supported utility observations. A Python CLI exposes focused queries with timestamps and source references.
3. **Coach through conversation.** An agent follows the review skill to investigate those facts, explain decisions and suggest a practice focus. It can reconstruct a whole round when an isolated fight needs more context.

The engine computes replay facts; the agent interprets them. Replay processing runs locally. The host agent supplies model access and conversation, so query results may be sent to that host's model provider.

## Using it today

ValCoach is a development prototype. It currently works with an existing vrfkit export and a Python environment; automatic replay import and a packaged installer are still planned.

The planned release is an [open-source Windows desktop app](docs/desktop.md) centered on AI coaching. Users will select a replay and player, connect an eligible ChatGPT plan or supply their own OpenAI API key, then ask for a review and follow-ups. BYOK will stay in memory for the session, with no saved key or silent switch to API billing. The app does not offer a standalone log/query browser; evidence queries support the coach internally.

The initial [desktop prototype](desktop/README.md) lets you choose an existing export, select whether you started on attack or defense, then pick your agent from that team and draft a coaching request. This distinguishes mirror agents on opposing teams. AI connections and reviews are visibly pending. Its development loop uses a one-time user-run dependency install and `npm run dev`; automatic replay import, the project license and release packaging also remain pending.

The desktop interface is a minimal dark workspace with a still Ascent-inspired island, soft dusk lighting and a small muted Sova-inspired drone flying around it. Flight runs automatically with no controls and suspends when hidden/offscreen. Reduced-motion uses slower, level flight with fixed scale and static rotors. Compact match/player selection, two review shortcuts and a coaching draft keep the session focused. AI connections and coaching remain pending.

For development or an external coaching agent, discover the players in an export with:

```powershell
$exportPath = 'C:\path\to\raw-export'
.\.venv\Scripts\python.exe -m valcoach players --export $exportPath
```

For coaching, give your agent the export path, your player ID and the [review skill](skills/valcoach-review/SKILL.md). For example: “Review my attack rounds and help me choose one thing to practise.” See the [CLI reference](docs/cli.md) for round, overview, fight, spatial, resource and firing queries, setup requirements and test commands.

## Current scope

Round and match queries, focused fight investigations, position/facing samples, supported utility, resources and firing observations are implemented. Current export support covers release-13.06 and release-13.08, with automated coverage on one private match per build.

Replay evidence does not fully reconstruct what a player saw or knew. Map geometry remains experimental, ability coverage varies, and firing observations do not establish complete shot counts or hit/miss results. Full-round walkthroughs reconstruct available evidence; they are not continuous visual playback. Broader playback and coaching validation remain in progress.

## Development

See the [architecture](docs/architecture.md), [replay correctness rules](docs/replay-correctness.md) and [contributor instructions](AGENTS.md). Private replays, exports, caches and local progress notes stay outside Git.
