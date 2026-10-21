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

In this Windows workspace, discover the players in an export with:

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
