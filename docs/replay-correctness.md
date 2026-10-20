# Replay correctness

Read and follow these rules before changing replay decoding, normalization, state reconstruction, queries or map geometry. [AGENTS.md](../AGENTS.md) contains project-wide instructions; [README.md](../README.md) documents commands and current limitations.

## Reconstruction and inference boundaries

- Preserve provenance and distinguish source observations, deterministic derivations, configurable heuristics, and tactical interpretations.
- Use global replay milliseconds as the canonical clock; expose round-relative times and units explicitly. Buy start, live start, source observation time, and matched combat time are different concepts.
- Keep team identity separate from per-round attack/defense. Track deaths and resurrections explicitly.
- Reconstruct ability array deltas before identifying casts; indices can reorder. Never carry an old cast's effects onto a new cast. Tailwind preparation is distinct from actual dash activation; repeated ultimate events are not distinct casts.
- Separate raw damage from actual life removed, and preserve health/armor/overheal components. Respect weapon ownership intervals and post-death pickups.
- Unknown values remain unknown, not zero. Unknown weapon/agent identifiers remain available for later mapping. Do not silently extend build compatibility without validation.
- Resolve firing tags from declared names rather than fixed numeric IDs. One effect can contain multiple projectiles; other firing sources include ability weapons and automatic turrets, whose player attribution does not prove a trigger pull. Sequence indices are not fire modes. Nearby same-item damage is temporal context, never hit/miss or intended-target attribution; incomplete firing coverage cannot become a complete shot count.
- Resource fields require matching component group, item incarnation and ownership. Keep base/signature/temporary charge channels separate. Ammo deltas and firing/reload states do not establish individual shots, misses, completed reloads or effective readiness. Decode raw integers only for exact supported Blueprint fields and payload widths.
- Checkpoints are a separate state stream. Use explicit saved round markers and per-checkpoint identity/component metadata; never assume checkpoint index equals round or shift timestamps to buy start. Missing default fields stay unknown, and snapshot active/equip observations do not create casts/actions. Include supplemental checkpoint content in cache revision while preserving match identity.
- Include damage-based teammate fights with either outcome while the selected player is alive. Do not hide them behind a fixed distance cutoff in the agreed first slice.
- Nonlethal damage contribution is independent of completed trades. “No recorded damage contribution” does not establish that the player did not swing, shoot, or help.
- Distance, replicated view, or an event sequence alone does not establish visibility, information, access, intent, or a missed trade opportunity. Keep heuristic windows and movement freshness rules explicit.
- Keep facing, static sightline, modeled walking access, dynamic occlusion and player knowledge separate. Minimap-derived geometry must have provenance, coverage, calibration and build validation. Separate sight blockers from movement blockers; unknown height/occlusion or missing model coverage remains unknown. An open modeled route is not proof of a safe or available trade.
- Smoke actor lifetime is not an opaque-smoke interval. Preserve unknown radius, activation/fade and height; optional radius-based intersections are explicit hypotheses. Player statuses use exact supported visual-effect identities and scoped start/stop pairs, distinct from cast/equip/windup effects. Missing stops or raw blind-array updates never establish an invented duration, flash severity or an unaffected player.

## Changes and verification

- Prefer small functions, explicit contracts, the standard library, and the existing PyArrow dependency. Keep analysis out of the CLI and renderer.
- Preserve structured JSON, stable error codes, source evidence and revision semantics. Change schema/cache/heuristic versions when their meaning changes; inspect cache identity when changing reconstruction.
- Add targeted regression tests for changed reconstruction or query behavior, particularly state transitions and missing data. Run the appropriate tests; run the full suite for changes crossing normalization/query boundaries.
- Integration tests skip without their private reference export. Report skipped checks honestly. `VALCOACH_TEST_EXPORT` can relocate the original reference export; `VALCOACH_TEST_EXPORT_1308` can relocate the second 13.08 export.
- Automated agreement with recorded expectations does not complete independent replay verification. Do not mark a manual check verified without the user's confirmation or an actual independent playback check.
- For parser upgrades, preserve old exports and compare a fresh export of the same replay with `python -m valcoach.compare_exports`. Inspect new fields/typed coverage and changed normalized facts before adding regressions or accepting compatibility. Report unavailable semantic comparisons honestly; never regenerate expectations solely to approve an upgrade. Do not infer old exporter provenance from the currently installed binary.
- Update README commands when behavior changes. Update plans when accepted scope/design changes. Documentation-only edits need link/content checks, not an automatic replay test run.
