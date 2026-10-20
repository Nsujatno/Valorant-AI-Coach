# Architecture and code map

Use this reference when navigating modules or changing their organization. [AGENTS.md](../AGENTS.md) contains project-wide instructions; [README.md](../README.md) contains executable commands.

## Code map

This map describes the current layout; it is not a required file structure or a limit on where code may go. Create, split, rename, or consolidate modules when that makes a domain concept easier to understand and change. Update this map after meaningful structural changes.

| Path | Responsibility |
| --- | --- |
| `valcoach/source.py` | Export validation, Parquet reads, source row identities |
| `valcoach/normalize.py` | Identity/state reconstruction and normalized cache |
| `valcoach/query.py` | Round queries, fights, trades, movement and Jett evidence |
| `valcoach/overview.py` | Full-match aggregation, round index and paginated review references |
| `valcoach/fight.py` | Paginated fight discovery, compact context and individual normalized evidence retrieval over shared cores |
| `valcoach/spatial.py` | Bounded positions, replicated facing, bearings and map estimate orchestration |
| `valcoach/angles.py` | Observer-relative facing intersections with mapped entrances and target-side projections; no visibility/intent judgments |
| `valcoach/equipment.py` | Last equip observations scoped to round/life; conflicts and unknown ammo/charges remain explicit |
| `valcoach/resources.py` | Item/component resource observations, ownership/life scopes, ammo/knives/charges/weapon states and bounded snapshots; no effective-readiness or complete-shot inference |
| `valcoach/checkpoints.py` | Explicit checkpoint round/identity validation and resource/equipment seeds; snapshots keep their own clocks and never become live action events |
| `valcoach/gunfire.py` | Tagged firing-effect reconstruction, item/shooter identity, projectile observations and paginated windows; no complete-shot or hit/miss inference |
| `valcoach/compare_exports.py` | Same-replay parser-upgrade inventories and normalized fact comparisons; no parser execution, baseline updates or automatic compatibility approval |
| `valcoach/effects.py`, `valcoach/utility.py` | Cached smoke lifecycles/status reconstruction and shared bounded utility/fight/spatial context |
| `valcoach/map_model.py`, `valcoach/geometry.py` | Versioned map validation/transforms, planar intersections and bounded walking routes |
| `valcoach/mapdata/` | Public-source annotation data, hashes and explicit build/validation coverage; no private replay facts |
| `scripts/build_minimap_model.py` | Offline candidate footprint generation from local public artwork/metadata; Pillow is a development-tool dependency only |
| `valcoach/render/` | Round/overview/spatial document sections, diagnostic SVG and shared formatting; no replay analysis |
| `valcoach/__main__.py` | CLI arguments, output and errors |
| `valcoach/verification.py` | Reference-game review pack using the same query core |
| `tests/test_core.py` | Synthetic cases for reconstruction and inference boundaries |
| `tests/test_reference.py` | Private-export integration checks |
| `tests/test_build_1308.py` | Second private export: 13.08 compatibility and post-round self-death checks |
| `tests/test_overview.py` | Match aggregation, pagination, missing values and evidence retrieval |
| `tests/test_spatial.py`, `tests/test_spatial_reference.py` | Synthetic spatial/geometry boundaries and private-export measurement regressions |
| `tests/test_angles.py` | Entrance direction, viewpoint conventions, footprint coverage and uncertainty regressions |
| `tests/test_equipment.py` | Equip transitions, life boundaries, conflicting observations and resource-availability limits |
| `tests/test_resources.py` | Resource component identity, pickups, actor reuse, life/cast resets, conflicts, pagination and private-export knife/ammo/reload regressions |
| `tests/test_checkpoints.py` | Checkpoint clocks/round markers, identity/incarnation boundaries, missing defaults, source retrieval and supplemental-cache invalidation |
| `tests/test_gunfire.py` | Tag shifts, repeated invocations, missing/conflicting arrays, item lifetimes, projectiles, pagination and both private-build firing checks |
| `tests/test_compare_exports.py` | New/removed fields, typed coverage, schema/quality drift, semantic multiplicity, input/output guards and identical-export sanity check |
| `tests/test_utility.py`, `tests/test_utility_reference.py` | Utility pairing, missing data, smoke hypotheses and private-source regressions |
| `tests/test_fight.py` | Compact context selection, limits, source agreement and private replay checks |
| `skills/valcoach-review/SKILL.md` | Portable coaching workflow and evidence/interpretation boundaries |
| `tests/fixtures/reference-match.json` | Minimal recorded expectations, not independently verified playback truth |

## Code organization and readability

- Organize around cohesive concepts such as replay identity, life state, damage, fights/trades, Jett abilities, round queries, and match summaries. A feature does not have to fit inside an existing file.
- Keep public query interfaces small. Hide reconstruction and aggregation details behind them instead of making callers assemble a large chain of helpers.
- Split a function when it mixes distinct responsibilities or makes a behavior difficult to locate. Do not use arbitrary line limits or extract one-line wrappers solely to make functions shorter.
- Keep orchestration readable: input preparation, analysis, aggregation, and serialization should be identifiable steps. Use named structures where positional tuples or large nested dictionaries make contracts unclear.
- Renderers format query results. Keep named document sections and shared formatting helpers; do not combine replay analysis, output layout, and console writes in a single function. Console/file writes belong at the CLI edge.
- Domain constants, validated identifier mappings, and output templates are appropriate. Private replay IDs, selected rounds, player IDs, and expected outcomes belong in fixtures or explicitly reference-specific verification tooling, never general analysis or rendering.
- Prefer a few useful modules over many shallow pass-through modules. Refactors must improve locality and preserve evidence, missing-data semantics, and observable behavior. Test through the supported query interfaces.
