# ValCoach — first replay query

The first implementation reads an **existing vrfkit Parquet export** and produces round timelines, selected-player damage and Jett abilities, teammate-fight summaries, and observed trade context. This is the first slice from [mvp-plan.md](mvp-plan.md), not the full coaching plugin or packaged application.

The reference export already exists locally. Nothing needs downloading to run these commands in this workspace.

Contributor guidance: [AGENTS.md](AGENTS.md). Current implementation status, verification checks and handoff notes: [PROGRESS.md](PROGRESS.md).

## Run on Windows

From the project root, discover players:

```powershell
.\.venv\Scripts\python.exe -m valcoach players --export output/first-replay/raw
```

Inspect your Jett in round one:

```powershell
.\.venv\Scripts\python.exe -m valcoach round 1 --export output/first-replay/raw --player 254
```

Player selectors are subject UUID, player-state ID, or pawn ID. Agent names only work when unique. `Jett` is ambiguous in this game. The player's chosen alias is not assumed to be present in the export.

Get machine-readable evidence or save a readable report:

```powershell
.\.venv\Scripts\python.exe -m valcoach round 1 --export output/first-replay/raw --player 254 --json --output output/my-round-one.json
.\.venv\Scripts\python.exe -m valcoach round 1 --export output/first-replay/raw --player 254 --output output/my-round-one.md
```

Output files must be new paths outside the raw export. Existing files are preserved. Without `--output`, the result goes to stdout; errors use nonzero exit codes, and `--json` errors use a structured error object.

Discover options and change heuristic windows explicitly:

```powershell
.\.venv\Scripts\python.exe -m valcoach round --help
.\.venv\Scripts\python.exe -m valcoach round 2 --export output/first-replay/raw --player 254 --trade-window-ms 3000 --json
```

## What the query includes

- All round kills, deaths, plants, defuses, resurrection events, and alive counts.
- Your incoming/outgoing damage, actual versus raw amounts, recorded life-section changes, weapons where mapped, and source row references.
- Your Jett cast records, with Tailwind preparation separate from dash activation corroborated by dash-equippable and movement evidence.
- Brief summaries of **all** damage-based teammate fights while you are alive, including surviving fights; no fixed distance cutoff.
- Recorded damage responses and completed trades with exact delays and configurable windows. Nonlethal contribution is independent of who gets the kill.
- Relevant weapon ownership changes, including pickups after your death, with the actual subsequent owner.
- Positions/distances from last-observed movement samples, with sample ages and a 250 ms default freshness limit.

Default heuristics are a 3-second engagement inactivity gap, a 5-second trade window, and contextual response windows of 3 seconds before / 5 seconds after the exchange. They are included in every JSON response. A 100 ms death-replication tolerance prevents near-simultaneous delayed damage packets from spuriously splitting a fight; resurrections still split lives. Lethal damage timestamps are aligned to matched death events, with the original RPC timestamp retained in `observed_time_ms`.

The same damage response can contextualize more than one teammate fight; never sum response totals across summaries as if they were distinct damage events. JSON evidence IDs identify the actual underlying records.

## Important limits

“No recorded damage contribution” does **not** mean the player did not swing, shoot, distract, hold another angle, or help. Missed shots and fights without damage are outside this slice. Distance does not prove visibility, access, information, or a missed trade opportunity.

Release-13.06 and release-13.08 exports are accepted, with local automated regression coverage on one game from each build (22 and 24 rounds respectively). Other branches still require explicit validation; this is not a blanket compatibility claim for all replay semantics. Independent playback verification remains pending. Other agents' cast/effect reconstruction, shot/reload attribution, comprehensive weapon names, scoreboard assists in the CLI, `.vrf` import orchestration, skills/MCP, and distribution packaging remain future work. Unknown weapon/agent classes remain available rather than receiving guessed names. Self-attributed deaths are labeled explicitly; raw death-event counts must not be treated as scoreboard K/D.

The second private replay was exported with the user's vrfkit v0.3.0. Discover its player IDs separately; the selected Jett is player-state 58 in that game:

```powershell
.\.venv\Scripts\python.exe -m valcoach players --export output/replay-5c31718d/raw
.\.venv\Scripts\python.exe -m valcoach round 1 --export output/replay-5c31718d/raw --player 58
```

Raw files are read only. Normalized facts are cached under `.valcoach-cache/`, keyed by content hashes of the required export files and normalization code. Follow-ups reuse that cache; movement is loaded for the requested round. This command has no model calls or network access. A future host agent may send query evidence to its model under that host's configuration.

## Playback verification pack

Generate the reference game's seven pending manual checks and reports for rounds 1, 2, 3, and 20:

```powershell
.\.venv\Scripts\python.exe -m valcoach.verification --export output/first-replay/raw --output-dir output/first-slice-review
```

This developer helper is specific to the existing reference game. Use a new output directory on each run so earlier annotations are preserved. Open `verification.md` in that directory to check the dash, damage, pickup, trade, survival, and nonlethal-contribution cases. Automated checks pass against recorded expectations; independent replay verification remains pending until the user completes it.

## Tests

```powershell
.\.venv\Scripts\python.exe -m unittest discover -v
```

Unit tests exercise state reconstruction, missing/stale data, trade boundaries, resurrection, fight grouping, and participation semantics. Integration tests use the private export in `output/first-replay/raw/` when available; set `VALCOACH_TEST_EXPORT` to relocate that same reference export. The 13.08 tests use `output/replay-5c31718d/raw/`; set `VALCOACH_TEST_EXPORT_1308` to relocate that second export. Each integration class skips when its input is unavailable. Minimal expected-values fixtures are in `tests/fixtures/`; original replays/exports are not bundled. The last full local run passed all 37 tests, including both private exports, the user-confirmed Iris -> Miks / Ghost-kill case, and the three confirmed trade/participation sequences.

The existing development environment already has PyArrow 25.0.1. The packaging declaration in `pyproject.toml` is for future installation/build work; the module commands above work without installing the project or fetching dependencies.
