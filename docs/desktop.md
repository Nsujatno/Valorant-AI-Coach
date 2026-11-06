# Open-source desktop release

Accepted direction, 2026-10-22: make ValCoach usable as an open-source Windows desktop app centered on AI-powered coaching. The current Electron/React/TypeScript [prototype](../desktop/README.md) supports existing-export/player selection and a coaching-request draft. Authentication, model requests, coaching answers, managed replay import and an installer remain unimplemented.

## Accepted product decisions

Player-selection flow, 2026-11-06: choose the export, choose whether you started on Attack or Defense, then choose your agent from that starting-side roster. Preserve the stable player ID internally, so mirrored agents resolve unambiguously. Side comes from normalized team-role observations at round-one live start, not final/halftime roles or team-ID guessing. Changing side clears the selected player; loading another replay clears both. Missing/conflicting starting sides have an explicit unknown group with team/player labels; duplicate or unmapped agents remain distinct.

Visual direction, 2026-11-06: the user wants a natural, scene-matched drone that remains small and muted but visibly flies around the island automatically, without a pause button. Its peach/lavender SVG and 65% opacity are retained. The normal 34-second loop uses curved turns and front/back passes with gentle banking and modest scale variation. Reduced-motion still flies, using a slower 50-second loop with level orientation, fixed scale and static rotors; this replaces the earlier fully parked behavior at the user's clarification. Preference changes preserve lap position. The island/fragments/shadow stay still, and hidden/offscreen scenes suspend flight. The restrained dark layout, dusk lighting, orbit line, match/player row, shortcuts and composer remain. This is decorative choreography, not the ability's in-game behavior. No game texture/model, new dependency, parser change or 3D framework is involved; AI connections/coaching remain pending.

- **Coaching is the player workflow.** Select a replay and player, ask for a review, discuss decisions and leave with a practice focus. Remove the standalone log/query/timeline/raw-JSON browsing workflow. Players can use the game's replay viewer for manual inspection.
- **Queries serve the coach internally.** Preserve the deterministic engine, CLI and focused evidence tools for the AI and developers. Coaching still explains observations with useful round/time references and material uncertainty.
- **Support two explicit AI connection options.** Eligible users can sign in with ChatGPT and authorize plan usage, or bring their own OpenAI API key. BYOK is an accepted option, not a deferred fallback.
- **BYOK is session-only.** Keep the key only in main-process memory for direct requests to OpenAI. Never save it to files, settings, browser storage, credential storage, logs, analytics, prompts or replay artifacts. Release the in-memory reference on disconnect, provider change and app exit; require re-entry after restart. Do not add a remember-key option.
- **Make billing explicit.** ChatGPT mode consumes the authorized plan allowance; BYOK uses the user's separately billed OpenAI API account. Never switch billing modes silently, including when ChatGPT usage runs out.
- **Keep payment separate from source licensing.** Open-source distribution is accepted. Selling ValCoach credits or providing ValCoach-funded inference is undecided and outside this prototype.

These decisions describe the intended product. The current prototype has disabled connection/review controls and does not accept keys or generate coaching.

## Player experience

1. Install ValCoach with its tested parser and managed runtime/dependencies.
2. Select a local `.vrf` replay and let ValCoach import it without assembling parser commands or installing development tools.
3. Choose whether you started on attack or defense, then choose your agent from that team's roster.
4. Choose Sign in with ChatGPT and separately authorize eligible plan usage, or enter an OpenAI API key for this session. Show which account/billing mode will be used before requests start.
5. Ask for a review or a round follow-up inside ValCoach. The coaching agent investigates through focused local evidence queries.
6. Discuss the coach's reasoning, clarify what you were trying to do and choose a practical next-game focus.
7. Manage ChatGPT usage or disconnect the selected connection. Disconnecting BYOK releases the key; restarting requires entering it again.

Local preparation and saved match data do not depend on a model connection. The player-facing coaching conversation requires an explicitly authorized inference path; this does not imply a separate offline fact browser. Declined consent, ineligible accounts, exhausted usage and API failures must preserve saved match data and explain the next supported action.

## Eligibility and usage

The [Sign in with ChatGPT quickstart](https://developers.openai.com/siwc/quickstart) documents plan usage for eligible Plus and Pro users. OpenAI's [open-source integration guide](https://developers.openai.com/siwc/token-sharing-open-source) covers open-source and locally hosted apps; paid or remotely hosted offerings require requesting access. Open-source distribution is the selected direction, not a claim that our untested integration or every user account is eligible. Free-plan coaching through this route is not promised.

Signing in and allowing plan usage are separate permissions. Requests consume the user's existing allowance, with account/app limits and revocation controlled in ChatGPT. Follow the [user-facing usage guidance](https://learn.chatgpt.com/docs/sign-in-with-chatgpt) and [UI guidance](https://developers.openai.com/siwc/ui-ux-guidelines). Display the account's available models rather than promising all ChatGPT models or features.

OpenAI's [errors and recovery guidance](https://developers.openai.com/siwc/token-sharing-open-source/errors-and-recovery) permits an explicit API-key alternative and prohibits silent billing fallback. BYOK does not turn API requests into ChatGPT subscription usage. Users pay OpenAI for their API usage; ValCoach does not sell credits in the current scope.

## Open source and payment

Open-source licensing governs access to and reuse of the source; it does not by itself require that every service be free of charge. The user has accepted open-source distribution, but has not decided to sell ValCoach credits or permanently ruled them out.

The planned preview uses the user's chosen ChatGPT allowance or API account, with no ValCoach credit purchase required. A future ValCoach-funded inference option would require its own service, accounting and explicit user choice. It must not be presented as extra ChatGPT subscription allowance or switch billing automatically. Offering ChatGPT plan usage as part of a paid/hosted product requires resolving OpenAI's commercial access requirements first.

## Architecture and data boundaries

- Reuse the current normalization, query and evidence core. The desktop app and CLI must agree on facts, missing data and revision semantics. Keep direct queries out of the player-facing renderer.
- The desktop agent owns conversation history, focused tool calls, cancellation and presentation. Reuse the review skill's evidence and coaching rules without making the core depend on a model provider.
- Keep replay files, raw exports and caches local. Send the conversation and requested query evidence to OpenAI for coaching; local processing does not make inference offline. Do not upload raw replays automatically.
- Implement distinct connection modes behind the model adapter. ChatGPT uses the documented OAuth flow and the app's own registration, with protected local storage for OAuth session credentials. BYOK uses only transient main-process memory. Do not reuse another client's saved credentials.
- Keep all credentials out of the renderer after handoff, evidence tools, prompts, diagnostics and analytics. The BYOK input itself must not be persisted.
- Follow the supported [models and inference contract](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference) and [preview limitations](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations). Execute allowed replay query functions locally; constrain tool names and validate arguments. Model output is not authorization for arbitrary shell execution.
- Preserve user analysis when sign-in, inference or updates fail. Never silently switch to a different billing path.

## Delivery sequence

0. Learn the desktop development loop with the coaching setup screen: run the declared dependencies locally, `npm run dev`, choose an existing export/player and edit a heading/style to confirm hot reload. The initial round inspector has been removed from the product workflow. This prototype does not connect to a model. See the [setup and testing walkthrough](../desktop/README.md).
1. Prove a narrow desktop coaching flow over an existing supported export: explicitly select a connection, execute focused queries internally and receive an evidence-backed streamed answer. Implement both ChatGPT consent/usage handling and session-only BYOK. Check decline, limits, API errors, disconnect/key release, restart and absence of silent billing fallback.
2. Complete managed `.vrf` import, including pinned parser identity, staging/failure recovery and reusable saved analysis.
3. Package the desktop app, engine, parser and runtime into a Windows x64 preview. Add installer/update/uninstall behavior, an explicit project license and a dependency/license inventory before distribution.
4. Test the install-to-review flow on a clean Windows machine and supported accounts, including follow-ups without reimporting. Retain existing replay correctness and pending playback checks.

The development prototype uses Electron, React, TypeScript and electron-vite to provide the familiar localhost/edit/save workflow. Dependencies were installed locally after source preparation; the agent did not install or download them. Desktop launch, layout and hot reload still need manual verification. OpenAI provides an [open-source desktop integration example](https://developers.openai.com/cookbook/articles/sign-in-with-chatgpt) for the authentication implementation. Audit installed dependencies before release; preparing source does not establish working sign-in or publish a release.

## Distribution status

A first-party license has not yet been selected or added; a downloadable desktop release is not available. Public source/release preparation must exclude private replays, exports, annotations, caches, environments and credentials. Preserve the pinned parser's license and notices, and ship native binaries through release artifacts.

Plugin/MCP distribution remains optional follow-up work. An external host is still useful for today's development workflow, but the planned desktop release supplies its own coaching conversation.
