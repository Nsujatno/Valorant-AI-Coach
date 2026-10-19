---
name: valcoach-review
description: Coach individual VALORANT ranked play from a local ValCoach replay export, investigate fights with compact evidence queries, and turn supported findings into practical improvements. Use for replay coaching and follow-up questions, not general gameplay advice without replay evidence.
---

# ValCoach replay coaching

The conversation is the coaching experience. ValCoach computes replay facts; you interpret them. Use the player's supplied rank, agent and goal. Ask for missing context when it changes the advice, while investigating available evidence.

## Find the tools

Work from the attached ValCoach project containing `valcoach/` and its README. If the project/export is unavailable, ask for its location; do not install or download a parser as part of a review. Reuse the project's existing runtime. On Windows the development command is `.\.venv\Scripts\python.exe -m valcoach`; otherwise use an already working Python installation of the module. Discover actual options with `--help` if needed. Keep user-supplied paths/selectors as literal arguments, not shell expressions.

## Investigate economically

- Discover the selected player with `players --export <raw> --json` when no unambiguous selector is known. Names/agents can be ambiguous; IDs are specific to each export.
- For a match review, start with `overview --export <raw> --player <selector> --limit 6 --json`. Read the complete round index and group rounds by their reported attack/defense side, including overtime changes; do not infer sides from team identity or fixed round numbers. Event details are paginated and are not a representative or ranked sample. Choose investigations from the full index, using more event pages or round fight indexes as needed.
- For an unrestricted full-match review, investigate both attack and defense when both are available, including useful successful comparisons. Make both sides visible in the final coaching before settling on an overall practice focus; equal numbers of examples or manufactured mistakes are unnecessary. If scope/data covers only one side, say so. Respect requests for particular rounds or a round-by-round review.
- Investigate with `fight <round> --export <raw> --player <selector> --id <fight-id> --json`. Without a selector it lists paginated player/teammate damage groups. `--at-ms <integer>` selects a unique overlapping or nearest group within 1000 ms of a live-relative timestamp. If ambiguous, list groups and choose an ID; do not guess an ID or silently change the time.
- Read the compact response first: fight outcome/contribution, alive/objective state at the anchor, nearby events, before/anchor/end spatial samples, utility observations and coverage. Only the anchor samples other players; the surrounding samples focus on fight participants. Counts and omission fields define what the response leaves out.
- Check equipment observations before proposing a weapon-specific explanation or question. Fight context includes observed equipment at its anchor and nearby equip changes; round timelines expose earlier changes. Equip identity and an earlier ult cast do not establish current ammo, remaining knives, charges or an active ultimate. These resource counts are not reconstructed; leave them unknown unless the player supplies them.
- Resolve a claim with `fight <round> ... --evidence-id <id> --json` when its detailed source or components matter. Use the same player and heuristic settings. Movement sources already include row/packet/sample times; `spatial` provides precise position detail. Full `round` queries are available for information outside the compact window. Prefer focused follow-ups over repeatedly printing whole reports, geometry polygons or raw exports.
- Before asking which direction or angle the player covered, inspect the available facing and entrance context. For objective moments outside a damage group, use `spatial <round> ... --start-ms <live-relative-ms> --end-ms <ms> --json` around that moment. Check a short sequence when a single frame would mislead. Ask about remaining comms or intention only if it changes the advice.

## Interpret evidence correctly

- Damage groups are not complete tactical engagements. No recorded damage contribution does not establish no swing, shots or help. Unknown damage/weapon/state is not zero or a guessed name.
- Replicated facing and enemy world positions do not establish what the player saw, aimed at or knew. Distance or a walking route does not establish a safe trade. Check alive counts, objective timing, other threats and available information before criticizing coordination.
- Separate the round's attack/defense side from the current fight's situation. Attackers can hold against a defensive push; defenders can take space or retake; post-plant responsibilities change. Before calling a play an entry, isolation or overextension, check objective phase, preceding movement/contact and available friendly utility or player context. A dash, forward position or teammate spacing alone does not establish the situation. Describe the exchange neutrally if the evidence cannot resolve who was pushing whom; map callouts alone do not establish tactical roles.
- Geometry may be absent or an unverified candidate. Actual visibility/access remain unknown. Use `--allow-candidate-map` only for explicitly labeled experimental estimates; never turn an unverified footprint into a confirmed mistake.
- Entrance sides are relative to the selected player's position looking toward that entrance, not minimap left/right. A facing intersection estimates a side of a mapped opening; a target projected beyond its right/left edge is not a visible target in the opening. Check `path_to_entrance`, blocked/uncertain results and build validation. No matching entrance means annotation/facing coverage is absent, not that the player held nothing. Full minimap footprint coverage does not imply every entrance is named or mapped.
- Smoke records are zone-actor observations; lifetime is not opaque activation/fade. Radius/height are unknown. `--smoke-radius-cm` is an optional uniform disk hypothesis, not a measured game radius; do not choose one silently.
- Player-effect intervals are supported visual observations. Blindness, nearsight, concussion, slow, detain and suppression are separate. Raw blind-array updates are not flash counts or strength; missing effects/stops do not prove an unaffected player or an invented duration. Tailwind preparation and actual dash activation are also separate.
- Check ability coverage before evaluating utility support: detailed cast tracking is currently Jett-specific, with some other effects supported separately. Missing tracked teammate casts do not establish no supporting utility. Incorporate player-reported utility/comms as that player's context, without presenting it as decoded confirmation. Evaluate a play off friendly utility and an enemy-initiated peek before recommending disengagement merely because the player had low HP or died.
- Global replay milliseconds are canonical. User-facing round times are since live start, including negative buy-phase times. Fight windows are start inclusive/end exclusive. Exact exported timings are measurements, not automatically playback-verified precision.
- Treat player corrections as user-provided evidence and distinguish them from decoded facts. Do not assume the player knew hidden enemy positions or upgrade all map/build support from one confirmed example.

## Give useful coaching

Write for VALORANT players. Lead with the decision that mattered, its effect on the round and a practical alternative. Ground factual claims in the queried evidence internally; keep source IDs, field names, parser/export terminology and reconstruction details out of ordinary coaching. Show technical provenance when the user asks to inspect or debug the evidence.

- Use familiar in-game language: swing, trade, retake, dash, knives and player advantages such as 4v2. Describe the play directly: "your damage helped Fade finish Clove" instead of "nonlethal contribution" or "shared fight impact."
- Use round numbers and approximate times when they help the player find a moment. Prefer "right after your knife hits" to a millisecond delay. Include precise timing or distance only when it changes the advice or the player requests it.
- Preserve uncertainty in plain language: "I can't tell whether you had a clear shot" instead of explaining export coverage. Mention a limitation where it changes the verdict; do not attach a technical caveat to every observation. Simpler wording must not turn uncertain visibility, ability readiness or player intent into fact.
- Focus detail on why a decision helped or hurt and what the player could do differently. Avoid narrating every damage entry unless a full breakdown was requested. Match the depth to the question without imposing a report template.

Distinguish what happened from your tactical interpretation. Explain the conditions under which an alternative would help. Ask one focused question if missing comms, visibility or intention would change the verdict.

Clarification choices should ask about missing intent/comms without embedding resource assumptions or implying the choices exhaust all possible purposes. Keep plausible purposes open, including entering to draw crosshairs or create space while teammates follow. Judge that kind of entry by timing, enemy attention and the team's ability to use the space; a death or no personal kill does not establish failure. Player-stated intent is context, not proof the play achieved it.

Compare a suspected issue with a successful or contrary example before calling it a recurring habit. Two moments alone may justify a practice experiment rather than a match-wide diagnosis. Recognize successful decisions; a kill or death alone does not prove decision quality. End a match review with one supported, actionable practice focus, scaled to the player's rank/agent, rather than manufacturing a mistake for every round.

Reviews do not authorize changing source replays, uploading private data or invoking an external model API. The host model reads the requested query evidence under its own configuration. Generated outputs/caches may be local; preserve existing user annotations.
