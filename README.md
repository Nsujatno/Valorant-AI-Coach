# ValCoach — replay evidence queries

ValCoach reads an **existing vrfkit Parquet export** and produces match/round evidence, compact fight investigations, firing/resource observations, utility context and bounded spatial queries. An initial coaching skill is included. Fresh-host coaching validation, managed replay import and packaged distribution remain in development.

The reference export already exists locally. Nothing needs downloading to run these commands in this workspace.

Contributor guidance: [AGENTS.md](AGENTS.md), [architecture and code map](docs/architecture.md), and [replay correctness](docs/replay-correctness.md). Local planning and handoff notes (`plan.md`, `mvp-plan.md`, `PROGRESS.md`) are intentionally ignored by Git and may be absent from another checkout. Private replays and exports are also excluded.

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
- Selected-player equip observations, preserving unknown item classes and distinguishing them from ammo, charges or ultimate readiness.
- Positions/distances from last-observed movement samples, with sample ages and a 250 ms default freshness limit.
- Pairwise damage groups involving the selected player, using the same grouping rule as teammate fights; JSON exposes these as `player_fights` with underlying damage/death evidence IDs.

Default heuristics are a 3-second engagement inactivity gap, a 5-second trade window, and contextual response windows of 3 seconds before / 5 seconds after the exchange. They are included in every JSON response. A 100 ms death-replication tolerance prevents near-simultaneous delayed damage packets from spuriously splitting a fight; resurrections still split lives. Lethal damage timestamps are aligned to matched death events, with the original RPC timestamp retained in `observed_time_ms`.

The same damage response can contextualize more than one teammate fight; never sum response totals across summaries as if they were distinct damage events. JSON evidence IDs identify the actual underlying records.

## Important limits

“No recorded damage contribution” does **not** mean the player did not swing, shoot, distract, hold another angle, or help. Missed shots and fights without damage are outside this slice. Distance does not prove visibility, access, information, or a missed trade opportunity.

Release-13.06 and release-13.08 exports are accepted, with local automated regression coverage on one game from each build (22 and 24 rounds respectively). Other branches still require explicit validation; this is not a blanket compatibility claim for all replay semantics. The user confirmed selected round-one and participation sequences; broader playback verification remains pending. Other agents' cast/effect reconstruction, shot/reload attribution, comprehensive weapon names, `.vrf` import orchestration, skills/MCP, and distribution packaging remain future work. Unknown weapon/agent classes remain available rather than receiving guessed names. Self-attributed deaths are labeled explicitly; raw death-event counts must not be treated as scoreboard K/D.

The second private replay was exported with the user's vrfkit v0.3.0. Discover its player IDs separately; the selected Jett is player-state 58 in that game:

```powershell
.\.venv\Scripts\python.exe -m valcoach players --export output/replay-5c31718d/raw
.\.venv\Scripts\python.exe -m valcoach round 1 --export output/replay-5c31718d/raw --player 58
```

Raw files are read only. Normalized facts are cached under `.valcoach-cache/`, keyed by content hashes of the required export files and normalization code. Follow-ups reuse that cache; movement is loaded for the requested round. This command has no model calls or network access. A future host agent may send query evidence to its model under that host's configuration.

## Full-match overview

For the second local replay:

```powershell
.\.venv\Scripts\python.exe -m valcoach overview --export output/replay-5c31718d/raw --player 58
.\.venv\Scripts\python.exe -m valcoach overview --export output/replay-5c31718d/raw --player 58 --json
```

The overview includes observed round score and sides, latest replicated scoreboard K/D/A, per-round kills/death events and damage, pairwise player fights, teammate-fight responses, completed trades, and Jett ability stages. It uses the round query core for every observed round. It computes facts and investigation references; it does not generate coaching judgments or rank mistakes.

Teammate-fight summaries include a linked opponent death when the group's damage caused it: killer, victim, weapon, global time and death evidence ID. Readable views spell out that kill and its round-relative time. The round index includes groups with linked teammate kills separately from response counts. `opponent_death: null` means no opponent kill is linked to that observed damage group; it does not establish that the opponent survived afterward. A different player's later finishing kill is not credited to the teammate. These summaries still cover teammate damage groups while the selected player was alive; the round timeline contains the full kill feed. This addition is overview version 2.

Scoreboard counters preserve source rows and observation times. Missing counters stay `null`. The second game's scoreboard is 30/16/4 while its selected-player death-event count is 17; both are retained. Post-round deaths are not automatically discarded. Damage totals include player-versus-opponent observations through the post-round phase, distinguish raw and actual amounts, and count each source hit once. Response damage is deduplicated across overlapping teammate-fight windows. Pairwise groups are not a count of complete tactical engagements, and response counts are not a measure of missed trade opportunities.

The complete round index and match totals are always returned. Only `review_events` are paginated: default `--limit 20`, maximum 100, with a zero-based `--offset`. Filter those event details without changing match totals:

```powershell
.\.venv\Scripts\python.exe -m valcoach overview --export output/replay-5c31718d/raw --player 58 --event-kind death --limit 10 --json
.\.venv\Scripts\python.exe -m valcoach overview --export output/replay-5c31718d/raw --player 58 --event-kind death --limit 10 --offset 10 --json
```

Event kinds are `kill`, `death`, `player_fight`, `teammate_fight`, `trade`, and `ability`. `pagination.total`, `returned`, and `next_offset` describe the matching event list; an out-of-range offset returns a valid empty page. `review_index` lists counts and rounds for all kinds. `coverage.truncated` refers only to omitted review-event details, while `round_totals_complete` covers every observed round, not proof of a complete source recording.

Each review event gives its round, global/round-relative milliseconds, and evidence IDs. Resolve those IDs in a round query's `evidence` object, using the same player and heuristic settings. Round-result source rows are in `round.result_sources`. For example:

```powershell
.\.venv\Scripts\python.exe -m valcoach round 14 --export output/replay-5c31718d/raw --player 58 --json
```

Jett preparation, corroborated dash activation, and unconfirmed dash-equippable records remain separate. Match ability counts retain phase; the compact round index combines phases using `ability:stage` keys. Zero recorded events does not establish that utility was available or unused. Other agents' ability summaries are explicitly unsupported (`null`).

Use the same `--output <new-path>` option to save Markdown or JSON. Existing outputs and raw exports are preserved. Normalized schema v5 retains scoreboard/result, utility and item resources and adds validated checkpoint seeds. Code and checkpoint-content revisions invalidate older caches automatically. No parser or dependency download is needed for these additions.

## Spatial inspection and candidate map geometry

Inspect a short window using milliseconds since the round's live start:

```powershell
.\.venv\Scripts\python.exe -m valcoach spatial 2 --export output/replay-5c31718d/raw --player 58 --start-ms 29635 --end-ms 29885 --step-ms 125 --target 54 --target 64 --target 72 --json
```

This returns last-observed XYZ positions in centimeters, yaw/pitch in degrees, facing vectors, distances and signed bearing offsets from the selected player. Every sample retains its source movement row, global time and age. Samples come from at or before the requested time; there is no interpolation. The default freshness limit is 250 ms. Dead, parked, nonfinite, stale and missing positions remain unusable; a revive requires a new-life sample. Missing rotation does not erase a known position or distance. Pawn-origin bearings do not establish camera aim, shots, attention or visibility.

`--target` accepts the same player selectors as `--player` and can be repeated; omitting it inspects the other discovered players. Windows are start inclusive/end exclusive, at most 10 seconds, and must stay inside the round's buy/live/post interval. Sampling defaults to 250 ms and 50 snapshots; `--limit` permits at most 200. Output is further bounded to 16 targets, 500 relations, 32 route evaluations, 100 context events and 64 vertices per route. `coverage` reports returned/requested counts, budget omissions, truncation and the next sampled start when snapshots are paginated. Context events cover the requested window, even when snapshots are truncated. Global replay milliseconds remain the canonical clock.

A **candidate, independently unverified** full Sunset minimap footprint is included as JSON annotations in `valcoach/mapdata/sunset-full-v1.json`. Its floor outline spans B, A, mid and both spawns, with ten enclosed minimap voids and 17 public callout points. Those points are landmarks, not authoritative region boundaries. The opaque minimap artwork is a planar floor hypothesis; it does not resolve cover heights, levels, doors or exact collision. There are currently **no validated map builds**. Geometry stays unknown by default. Explicitly opt into the candidate for release-13.08 and save a diagnostic of the first snapshot:

```powershell
.\.venv\Scripts\python.exe -m valcoach spatial 2 --export output/replay-5c31718d/raw --player 58 --start-ms 29635 --end-ms 29885 --step-ms 125 --target 54 --target 64 --target 72 --allow-candidate-map --diagram output/my-spatial-check.svg --output output/my-spatial-check.md
```

`--diagram` writes a new SVG; `--output` writes new Markdown or JSON as usual. The SVG contains sample times/source references and geometry/replay revisions. Its view follows the displayed players when they are outside the annotated geometry area, so the same projection can check positions elsewhere on Sunset without adding geometry there. It can draw annotations without artwork. Optional `--map-image <local.png>` embeds a PNG only when its checksum matches the model's recorded source image; the command does not fetch it. `--map-model <local.json>` can supply an explicit versioned model; a replay-map mismatch is rejected. Candidate opt-in does not enable unsupported builds. Models, source hashes, projection, validation state and annotations are returned in JSON. Geometry/code changes alter the geometry revision independently of the normalized replay revision. Packaged data includes annotations, not downloaded map artwork.

Static sightlines describe only annotated 2D footprints: `clear_in_annotated_2d_model`, `blocked_in_annotated_2d_model` or `unknown`. Coverage gaps, samples inside blockers and edge ambiguity stay unknown. Walking routes use an annotated surface, bounded grid A* and configurable model clearance; lengths are estimates, not guaranteed shortest paths, arrival times or safe routes. `no_route_in_modeled_area` only describes that finite model. Endpoint coverage or grid attachment gaps remain unknown. The candidate uses a 50 cm grid, 35 cm actor-clearance assumption and 30 cm sight-edge uncertainty; its full grid is bounded to 100,000 cells. These are model settings awaiting scene validation.

Floor/eye height, temporary walls, doors and player knowledge are not reconstructed. Smoke lifecycles and supported player visual-effect observations are available as described below; actual smoke opacity and impairment severity remain unknown. `actual_visibility` and `actual_access` remain `null`. Coordinate alignment, the 13.08 scene layout and positive/negative sight/access cases still need independent playback validation before stronger coaching claims. Other maps still get position/facing and supported utility evidence without static map estimates.

The R2 spatial regression uses `valcoach/mapdata/sunset-full-v1.json`: the Sova line intersects an enclosed footprint, the Raze line grazes another corner's uncertainty band, and the modeled walking route to Miks is roughly 16.5 m. The earlier B-only model has been retired. Existing generated reports retain their original geometry revisions and are historical estimates; their sightline results may differ from the current full-map trace.

### Facing a mapped entrance

Spatial version 3 adds `entrance_context` to each snapshot. It intersects the selected player's horizontal facing with annotated entrance cross-sections. Left/right is from the observer looking toward the opening; the estimate divides its width into thirds, with uncertain boundaries/jambs. It also projects observed players in that entrance's approach area onto the same cross-section. `right_of_opening` means a position beyond that edge in projection, not an enemy visible through the opening. Blocked paths, missing/stale facing, near-vertical view and missing annotations remain explicit. At most four entrance candidates are returned per snapshot; omissions are counted.

Named entrance coverage currently includes **B Main only**. Whole-map footprint coverage does not mean every angle has been annotated. Inspect the R4 defuse window with:

```powershell
.\.venv\Scripts\python.exe -m valcoach spatial 4 --export output/replay-5c31718d/raw --player 58 --start-ms 69232 --end-ms 72232 --step-ms 250 --target 64 --target 56 --allow-candidate-map --json
```

The estimate agrees with the user's description of facing left Main while Raze was on the other side. The new entrance boundary and full map remain pending independent playback validation; no visibility, crosshair pixel, intention or missed-trade verdict is inferred. Fight version 2 exposes the same entrance context through the compact query, without full map polygons.

The footprint can be reproduced offline with `scripts/build_minimap_model.py --image <local-minimap.png> --metadata <local-map.json> --annotations valcoach/mapdata/sunset-entrances-v1.json --output <new-model.json>`. This development tool uses Pillow; the replay runtime still requires only the existing PyArrow dependency. The checked-in annotations record public source hashes, build restrictions, simplification settings and their generation method. No artwork is fetched by the tool or bundled in the model.

## Smoke and player-status context

Round reports now include a `utility` list with source-backed smoke actor lifecycles, supported player visual-effect intervals and raw blind-array update references. Both player and teammate fights include `utility_context` references for records overlapping the observed damage group; resolve them in the round's `evidence`. This is temporal context, not proof that a smoke affected that fight or that a player could trade. Existing damage/kill counts are unchanged. Readable reports include the same utility observations.

Spatial snapshots add `smoke_observations`, per-player `utility_status` and per-relation `smoke_context`. Inspect the new local game's R3 blind-effect example:

```powershell
.\.venv\Scripts\python.exe -m valcoach spatial 3 --export output/replay-5c31718d/raw --player 58 --start-ms 25200 --end-ms 27200 --step-ms 250 --target 62 --json
.\.venv\Scripts\python.exe -m valcoach round 3 --export output/replay-5c31718d/raw --player 58 --output output/my-round-three-utility.md
```

Smoke support currently maps Jett, Clove (including post-death) and Miks zone classes. Flying projectiles and ability items are not counted as deployed zones. Ownership uses recorded player-body Instigator references. Positions use spawn or last typed movement observations, never a future sample; raw/unresolved movement updates and sample ages remain explicit. Dormancy does not end a smoke. Missing actor closes remain unknown and are bounded at round/GUID-reuse scope. Those lifecycles do not identify the fully opaque phase.

Status support uses exact mapped effect-container names for blind, smoke nearsight, nearsight, concussion, slow, detain and suppression visuals. Only confirmed player-body actor observations are admitted. Start/stop matching uses actor, component, effect ID, source ID and local/transient flags; repeat invocations in one packet stay separate. Open-ended evidence is bounded by life, actor, round and ID-reuse transitions. Equip/cast/windup names are not inferred to be debuffs. Categories remain separate, and all impairment severity stays `null`. Not all mapped categories necessarily occur in a particular game.

The local parser export preserves `ActiveBlinds` arrays raw. They are exposed as undecoded observations, not flash counts, severity or duration. A separate named Phoenix blind visual can still have a matched start/stop interval. No observation does not establish an unaffected player, and status visuals are not authoritative gameplay flags.

Smoke radius, vertical shape, activation/fade and opacity remain unvalidated. By default the tool reports observed centers and distances from each center to the inspected XY line, with actual occlusion unknown. Optional `--smoke-radius-cm 400` tests a **user-chosen uniform 400 cm disk hypothesis** for all supported smoke actors in that query; 400 is an example assumption, not a measured game radius. Results such as `candidate_smoke_intersection` never become actual visibility. That hypothesis excludes height/eye position and opaque timing. SVGs mark smoke centers in purple, draw assumed disks only when configured, label supported player effects, and frame the displayed players/smokes.

Per window, the utility list is capped at 200 records; per snapshot, 32 smoke observations and 16 statuses per player; per fight, 32 smoke and 32 status references. Counts/omissions and truncation are explicit. Normalized schema v3 caches per-match utility alongside other facts, keyed by export content plus source/normalization/effect-reconstruction code. Static map JSON is unchanged. Spatial version 2 adds utility context. No parser update or dependency download is required for these observations. Utility radius/phase validation and independent replay checks are still pending.

## Compact fight investigation and coaching

List a round's player and teammate damage groups without printing the whole report, then inspect a selected group:

```powershell
.\.venv\Scripts\python.exe -m valcoach fight 2 --export output/replay-5c31718d/raw --player 58 --limit 10 --json
.\.venv\Scripts\python.exe -m valcoach fight 2 --export output/replay-5c31718d/raw --player 58 --at-ms 29635 --json
.\.venv\Scripts\python.exe -m valcoach fight 9 --export output/replay-5c31718d/raw --player 58 --at-ms 2500 --json
```

Without a selector, `fight` returns a paginated index (`--limit` 1-40, default 20; `--offset` zero-based). `--id <fight-id>` selects the exact group from that index or an overview fight reference. `--at-ms` is relative to live start: it selects a unique overlapping group, otherwise the nearest group within 1000 ms. Ambiguity is an error with candidate IDs; the caller must choose rather than silently selecting a teammate or opponent. A death event ID is an evidence selector, not a fight ID.

Context defaults to 2 seconds before / 3 seconds after the group or requested anchor, constrained to the round and at most 10 seconds. `--before-ms` / `--after-ms` each accept 0-5000. For long groups, coverage identifies portions outside the window; late anchors get a window around that moment. Up to three exact requested snapshots show before/anchor/end positions. The anchor includes other players; surrounding snapshots focus on the selected player and fight participants. Last-observation freshness, life handling, status/smoke evidence and geometry checks use the same spatial core. The nearest 16 timeline events are returned chronologically (damage before a linked death at equal time), plus damage/outcome facts for the group and alive/objective state at the anchor.

The compact result omits raw map polygons, route vertices, repeated metadata and full source payloads. Utility detail caps at 20 records, per-snapshot smoke centers at eight, and fight/response ID lists at 20; counts and omissions remain explicit. Other underlying spatial budgets still apply. `--allow-candidate-map` and optional `--smoke-radius-cm` retain their experimental meanings. `--json` uses compact serialization for this command. These are evidence queries, not deterministic coaching judgments or mistake scores.

Fight context includes `state_at_anchor.equipment`, the latest selected-player equip observations within the current round/life, and nearby equip changes in the event list. Round timelines expose equip observations too. Same-time conflicting items remain ambiguous, cleared references do not fall back to an older weapon, and revives require a new-life observation. Equipment is event-driven, so its age is reported without applying movement's 250 ms rule. Up to eight same-time observations are returned, with explicit omissions. Equip identity alone does not establish resources or tactical intent; its legacy count placeholders stay `null`. Separate resource observations now provide supported counts/states as described below. Exact equip records can be retrieved with `--evidence-id` like other round evidence.

### Ammo, knives and action state

Inspect resource state at any round time, including moments without recorded damage:

```powershell
.\.venv\Scripts\python.exe -m valcoach resources 24 --export output/replay-5c31718d/raw --player 58 --at-ms 64639 --json
.\.venv\Scripts\python.exe -m valcoach resources 1 --export output/replay-5c31718d/raw --player 58 --at-ms 29676 --before-ms 1000 --after-ms 2000
```

`--at-ms` is milliseconds since live start, including negative buy-phase times inside the round. Snapshots use only observations at or before that time. The surrounding observation window is start inclusive/end exclusive; `--before-ms` and `--after-ms` each accept 0–5000. `--limit` is 1–200 (default 40); `--offset` pages chronological observations. Total/returned/omitted counts and `next_offset` remain explicit. The snapshot is independent of its surrounding page and can reference an older observation in the current round/life.

Resource version 2 separates magazine and reserve ammo using the exact AmmoComponent and component identities. Counts belong to item actors: a pickup can retain a previous owner's last observed reserve count, while its observation keeps the original attribution. Actor closes/GUID reuse, ownership changes, death/revive, round boundaries and future data cannot silently supply a snapshot. Source times/ages and evidence IDs are retained; same-time conflicting values stay unknown.

Jett's exact supported Blueprint knife-count fields are decoded as signed little-endian 32-bit integers with strict payload widths. A new observed ultimate activation cannot inherit an earlier activation's knives. Other agents retain generic source resource channels and raw item classes; this does not add detailed cast mapping for them. Base charge resources, signature charges, temporary charges, maximum charges, cooldown flags/durations, ultimate points and the ultimate-active flag remain separate observations. They are not summed into an invented effective charge count or ability-ready verdict.

Gun state observations resolve the main EquippableStateMachine's state identity, including firing, reload and readying; zoom-machine changes cannot overwrite it. A firing state can cover multiple bullets. A reload-state observation and a later ammo refill are distinct events; neither proves the exact reload-completion time. **Complete shot counts, missed shots and effective weapon/ability readiness remain unknown.** A positive count does not establish that the player could use it while impaired, equipping or otherwise constrained.

Round version 4 includes up to 200 resource observations under `resources`, with explicit omissions; use the focused command to inspect/page a window. Fight version 6 includes `state_at_anchor.resources` and up to four compact surrounding resource records, plus individual firing-evidence lookup. Counts, times and evidence references are retained; repeated owner metadata and known ability class paths stay in detailed queries. Individual record details, including pre-pickup ammo used in a snapshot, are retrievable with `fight --evidence-id` for an item held by that player in the round. This keeps fight output bounded. Normalized schema v6 caches resources, checkpoint state and firing observations; reconstruction code participates in its revision.

For the local R24 regression, the export records zero knives and the ultimate inactive at +14.993 s. At +64.639 s, the selected player has a Spectre equip observation, last-observed magazine/reserve counts of 25/90, and a last-observed `ReadyingState`. These are source-backed checks against the earlier user correction, not independent playback verification of animation timing or readiness.

### Starting state from checkpoints

When `checkpoint_fields.parquet`, `checkpoint_actors.parquet` and `checkpoint_net_guids.parquet` are present, resource snapshots can use explicitly saved state that never produced a live update. Inspect a round's initial resources with:

```powershell
.\.venv\Scripts\python.exe -m valcoach resources 2 --export output/replay-5c31718d/raw --player 58 --at-ms 0 --json
```

Each checkpoint uses its own component/GUID metadata. The saved game-state `RoundNumber` determines round membership; checkpoint index is not assumed to be a round number. Subject/pawn identity and the already-observed, still-live actor incarnation/class must match. A checkpoint just before a round's buy marker can seed that explicitly identified round while retaining its actual earlier source timestamp. It cannot seed an earlier query, another round, a revived life or a later item incarnation. Missing defaults stay unknown. Later live updates take precedence by their time, including unknown/cleared values; disagreement at the same timestamp remains a conflict because checkpoint packet IDs are a separate stream.

Checkpoint seeds are saved state, not new casts, shots, equip actions or reload completions. They remain outside the combat/action timeline. `checkpoint_coverage` reports accepted snapshots and rejection reasons. Evidence IDs use `checkpoint_fields:<row>:resource` (or `:equipped`) and detailed source includes checkpoint index/ID. Retrieve them through `fight --evidence-id`. A saved ultimate-active flag is not a new ultimate activation. The first checkpoint in each current reference export lacks an explicit round marker and is excluded; absence is not interpreted as round zero or an inactive ultimate.

All three checkpoint tables are optional together. Without them, live queries still work with explicitly missing starting state. A partial set returns `incomplete_checkpoint_export`; known checkpoint framing/partial-data problems reject seeding while retaining live evidence. Checkpoint content changes invalidate the normalized cache without changing the existing match ID. Supporting actors/equipment counts are bounded in query output as usual.

Retrieve one normalized record with its detailed source/components when needed:

```powershell
.\.venv\Scripts\python.exe -m valcoach fight 2 --export output/replay-5c31718d/raw --player 58 --evidence-id events:17:death --json
```

Use the same player and heuristic settings for evidence retrieval. Fight queries accept the existing round heuristic flags. A source must belong to that round/player report. Movement row/packet/time references are already present in snapshots; use `spatial` for full precision. Markdown output and new `--output` paths are supported as usual. The implementation calls the shared round/spatial analyses locally; follow-ups reuse normalized facts but still read relevant movement data.

The portable coaching skill is [skills/valcoach-review/SKILL.md](skills/valcoach-review/SKILL.md). It guides overview → investigations from both recorded sides → focused fight/resource/firing evidence → successful comparisons → one practice focus. When a scenario needs earlier context, it can reconstruct the full round with one `round` query, then inspect only decision-changing spatial/firing/resource windows. This is an evidence walkthrough, not continuous visual playback. It evaluates player explanations against information available at the decision, without treating hidden enemy positions or later outcomes as prior knowledge. Full-match reviews use the complete round index to cover attack and defense; a limited page of event details is not a representative match review. Round side is separate from the fight's tactical situation: an attacker being pushed is not automatically making an entry. Player-facing reviews use familiar VALORANT language and practical advice; technical evidence stays in the investigation unless requested, and uncertainty is explained in plain language where it affects the advice. A host can be instructed to read that file directly. Copying that skill folder into a host's supported skills directory enables its normal discovery; this repository source is not a full plugin/runtime installer. The format follows [OpenAI's skill guidance](https://learn.chatgpt.com/docs/build-skills).

Local R2/R9 measurements reduced compact JSON size from 90,635 to 14,177 bytes (84.4%) and 63,637 to 14,805 bytes (76.7%) respectively, compared with equally compact full-round serialization. This is a sample byte comparison, not a general token/latency promise. A guided coaching trial and payload measurements are saved locally under `output/coaching-20261018/`; a fresh-host coaching evaluation remains separate from automated fact tests.

## Firing observations, including firing without damage

Inspect the selected player's firing effects in a round or time window:

```powershell
.\.venv\Scripts\python.exe -m valcoach gunfire 2 --export output/replay-5c31718d/raw --player 58 --start-ms 28000 --end-ms 31000
.\.venv\Scripts\python.exe -m valcoach gunfire 2 --export output/replay-5c31718d/raw --player 58 --start-ms 28000 --end-ms 31000 --json
```

Gunfire version 1 resolves declared `FiringState.*` gameplay-tag names from the export's metadata and reads typed float/object/vector effect arrays. Numeric tag IDs are not assumed stable. It exposes firing time, shooter attribution, source item, ammo remaining, projectile count, sequence index, source origin and attack vectors where recorded, with source rows and explicit missing/conflicting values. Adjacent repeated effects in one packet remain separate. Other visual effects do not become shots. Standard guns use `item_kind: gun`; other sources, including Chamber ability weapons and Killjoy turret attacks observed in the reference exports, use `other_firing_source`. A turret's player attribution does not prove that player pulled the trigger. Detailed knife-attack coverage is not established by this audit.

One firing effect can describe several projectiles. `BurstShotNumber` is a sequence index, not a decoded burst-fire mode. Direction vectors are observations, not crosshair pixels or target/visibility judgments. Unresolved shooters/items and malformed arrays appear in coverage. **Complete bullet totals, intended targets and hit/miss classification remain unknown.** The legacy resource `shots_fired` placeholder stays unknown; this command returns observed firing activity rather than claiming a complete count. This interpretation uses the pinned vrfkit [data format](https://github.com/yakisoba0728/vrfkit/blob/v0.3.0/docs/DATA.md) and [bundle adapter](https://github.com/yakisoba0728/vrfkit/blob/b9496df61c40fa304d4f539689c1dd627804946e/tools/to_valplay_bundle.py), with stricter invocation/identity checks.

Windows use milliseconds since live start, start inclusive/end exclusive; negative buy-phase times are allowed inside the round. With no window, the command covers live start through the round boundary, including post-round observations. Pagination uses `--limit` 1–100 (default 20) and nonnegative `--offset`. Nearby damage requires the same shooter and item within a symmetric `--damage-window-ms` tolerance (0–1000, default 100). Several firing effects can share nearby damage; that relationship is context, never a projectile-to-hit assignment. No nearby damage does not prove a miss. Existing damage-based fight grouping is unchanged.

Retrieve a firing record's full source details using its returned ID with `fight <round> --evidence-id <id>` and the same export/player. JSON/readable output and new `--output` paths work as usual. These observations need no parser update or dependency download.

## Checking a parser upgrade

Existing regression tests exercise saved exports; they cannot discover what a different parser would export until that parser is run on the same replay. Preserve the baseline export, export that replay into a **new directory** with the chosen parser, then compare:

```powershell
.\.venv\Scripts\python.exe -m valcoach.compare_exports --baseline output/first-replay/raw --candidate output/first-replay-new-parser/raw --output output/parser-upgrade-review.json --check
```

The comparison command never runs or downloads a parser. It requires matching declared replay GUIDs and matching source sizes when both are available. Reports must use new paths outside both raw exports. Its source inventory covers all Parquet tables/columns/types/hashes, observed field names/checksums, typed-coverage gains/losses, declared fields and quality counters. It discovers fields not yet consumed by ValCoach. Added declarations can be unused metadata and need review.

The same current normalizer processes both exports. The report compares selected reconstructed facts: player/round identity, damage, timeline, casts, equipment, resources, firing, team roles, scoreboard and supported utility. It ignores provenance row IDs and row ordering while preserving duplicate fact counts. Byte changes still require review even when these facts agree. Raw movement changes are flagged by hashes/schema/counts; sampled spatial results and coaching judgments are not semantically compared. If normalization fails or the candidate build is unsupported, the source inventory remains available and semantic equality is unknown.

`--limit` bounds examples to 1–100 per category (default 40); difference totals stay complete. `--check` returns 0 when no review is required, 2 when differences or unavailable semantic checks require review, and 1 on comparison errors. Without `--check`, a successfully generated report returns 0 even if review is required. An identical-export comparison is a useful sanity check, not a parser-upgrade validation.

Parser version/checksum remains unknown if absent from the saved export metadata; the currently installed executable cannot establish which parser created an older export. Managed import will need to record that provenance. Investigate changes, add focused regressions and rerun the suite before accepting an upgrade. This report never updates reference expectations or extends the supported-build allowlist automatically.

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

Unit tests exercise state reconstruction, missing/stale data, trade boundaries, resurrection, fight grouping, and participation semantics. Overview tests cover deduplicated response damage, missing counters, scoreboard/event differences, side changes, pagination, and round evidence retrieval. Spatial tests add angle wrapping, partial rotation, life/freshness boundaries, coordinate transforms, build gating, separate sight/walking layers, wall detours, door clearance, edge/coverage uncertainty, output budgets and diagram/output guards. Integration tests use the private export in `output/first-replay/raw/` when available; set `VALCOACH_TEST_EXPORT` to relocate that same reference export. The 13.08 tests use `output/replay-5c31718d/raw/`; set `VALCOACH_TEST_EXPORT_1308` to relocate that second export. Each private-export test skips when its input is unavailable. Minimal expected-values fixtures are in `tests/fixtures/`; original replays/exports are not bundled.

Gunfire tests cover shifted tag IDs, repeated invocations, malformed/conflicting arrays, source lifetimes, multi-projectile/other-source boundaries, pagination and both private builds. Export-comparison tests cover newly decoded data, removals, schema/quality changes, provenance-independent fact comparisons, duplicate counts and read-only input/output guards. The latest test results and remaining manual verification are recorded in local `PROGRESS.md` when present. Automated agreement with recorded expectations does not independently verify unreviewed replay content, map geometry or coaching quality.

The existing development environment already has PyArrow 25.0.1. The packaging declaration in `pyproject.toml` is for future installation/build work; the module commands above work without installing the project or fetching dependencies.
