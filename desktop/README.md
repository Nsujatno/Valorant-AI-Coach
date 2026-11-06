# Desktop prototype

An initial Electron + React + TypeScript app for learning the local development loop. It opens an existing vrfkit export, asks whether you started on attack or defense, then shows the agents on that side and lets you draft a coaching request. The two Jetts on opposing teams resolve to different underlying player IDs.

The interface is a minimal dark workspace: a still Ascent-inspired island with soft dusk lighting and a fine orbit line above compact match/player selection and a coaching composer. A small original SVG Sova-inspired drone uses muted peach/lavender tones and partial transparency while visibly looping around the island, behind rooftops and across the front. The normal 34-second lap uses gentle banks and modest scale changes. Reduced-motion keeps the drone flying on a slower 50-second lap with no banking, fixed scale and static rotors. Both start automatically without a button or pointer interaction. Hidden/offscreen scenes suspend flight while preserving phase; changing motion preference preserves the current lap position. Two review shortcuts populate and focus the editable draft. The illustration is bundled locally; no 3D library or external image host is used. See [artwork provenance](src/renderer/src/assets/README.md).

AI-powered coaching is the product workflow. The standalone round inspector, event filters and raw JSON browser have been removed. The existing CLI and query adapter remain internal coaching/developer tools. See the [accepted decisions](../docs/desktop.md#accepted-product-decisions).

This is a development app, not an installer. Automatic `.vrf` import, ChatGPT sign-in, session-only BYOK and conversational coaching are not implemented yet. The header's AI not connected menu explains the two planned options; connection and Start coaching buttons remain disabled. No API key can be entered and no coaching model requests are made. Replay selection uses the existing Python environment without installing Python packages or running/downloading a parser.

## One-time setup: you run the install

Node.js 22.12+ and npm are required. Node 24.21.0 and npm 11.19.0 were found in the current Windows workspace. Local npm dependencies and the Electron runtime are now installed; a fresh checkout needs the setup below.

From the repository root in PowerShell:

```powershell
Set-Location desktop
npm run doctor
```

The doctor checks local dependencies and Python without installing anything. Missing dependencies make it exit with code 1. If they are missing, install them yourself:

```powershell
npm install
```

This downloads the declared npm packages and runs `install-electron` to download the Electron desktop runtime. Electron is a local project dependency; no global Electron install, Visual Studio or Rust installation is required for this prototype. Keep `package-lock.json` so subsequent clean installs can use `npm ci`. The agent must not install or download dependencies under the current user instruction.

Check and launch:

```powershell
npm run doctor
npm run dev
```

The terminal starts Vite and opens a real desktop window. Keep that terminal running. Use Ctrl+C in the terminal to stop development mode. If you reach a browser page instead, the native replay bridge is not available there; use the Electron window.

## First manual check

1. Select Choose a replay export. Under the repository's `output/`, choose an existing completed export's `raw` folder containing `manifest.json` and the Parquet files. Select the export folder itself, not its parent and not a `.vrf` file.
2. Choose Attack or Defense under Starting side, then choose your agent. Changing the side clears the old player; the opponent's matching agent is not offered. If starting sides are missing/conflicting, Side not recorded lists only those players with explicit team/player labels. Same-side duplicate agents and unmapped agent identifiers retain disambiguating labels.
3. Draft a coaching request, or choose Review my match / Work through a round and edit the inserted text. Confirm the shortcut focuses the draft, the selected shortcut changes and the character count updates. The draft stays in React memory; closing or reloading the app clears it. It is not sent anywhere.
4. Expand AI not connected in the header. Confirm that ChatGPT/BYOK connection options and Start coaching are disabled with an explanation that AI coaching is not implemented.
5. Change the export and choose starting side/agent again; both selections reset on a successful new load. Canceling the picker or failing to load another export retains the last successful selections. Cancel stops active player discovery; the engine may take longer with a cold cache.
6. Select the code icon in the top-right corner (Open DevTools), or press F12, to inspect the interface and console.
7. Confirm the small muted drone automatically flies around the island, with turns and occasional front/back passes, and that no motion button appears. The island should remain still when the mouse moves. Turn reduced-motion on/off to check slower level flight, fixed scale and static rotors; it should continue moving and retain its lap position. Minimize/hide the window or scroll the island offscreen and confirm flight suspends without a phase jump. Check default and minimum window sizes across a full lap for clipping, keyboard access and absence of horizontal scrolling; the decorative scene clips its moving carrier.

No manual round-query or log-inspection step is part of this flow. Coaching will use focused evidence internally once the model adapter and agent are implemented.

## Edit, save, see the result

- Change the heading in `src/renderer/src/App.tsx` and save. React updates inside the open window through Vite's hot reload.
- Change `--accent` in `src/renderer/src/styles.css` and save to try a new color.
- Change `src/main/index.ts` or `src/preload/index.ts` and save. `npm run dev` uses `--watch` so main-process changes restart the app and preload changes reload the interface. Choose the export again after a restart.
- Python is launched for each engine request, so supported engine changes take effect on the next request. Existing normalized cache revision rules still apply.

See the [electron-vite development guide](https://electron-vite.org/guide/) and [hot reload behavior](https://electron-vite.org/guide/hmr-and-hot-reloading).

## Checks and a compiled preview

```powershell
npm test
npm run typecheck
npm run build
npm start
```

`npm test` runs with Node's built-in test runner, even before npm dependencies are installed. It checks the process bridge, schema-v2 starting-side contract and both private replay exports through the real CLI when available. Selection tests cover mirrored agents, side/match resets, rejected opponent identities, unknown sides and duplicate/unmapped labels. Drone tests check bounded full flight, front/back passes, gentle banking/scale, continuous faint opacity, automatic startup in both motion modes, phase-preserving preference changes, hidden/offscreen suspension, cleanup and static fallback without Web Animations. It honors `VALCOACH_TEST_EXPORT` and `VALCOACH_TEST_EXPORT_1308` as described in the [CLI test reference](../docs/cli.md#tests); private-export tests skip when inputs or project Python are missing. These tests do not launch Electron or independently verify rendered motion, layout or hot reload. The synthetic roster in `test/fixtures/player-selection.json` contains no private replay identities.

Type checking, building and the desktop launch require the npm dependencies/runtime. `npm run build` compiles the app; `npm start` opens those compiled files. Neither creates or publishes an installer. The compiled preview still expects this repository and its `.venv` beside `desktop/`.

## Boundaries

The renderer has a narrow preload API for folder/player discovery, cancellation and DevTools. Direct round querying is not exposed through IPC. It has no direct Node.js or shell access. The main process launches `.venv/Scripts/python.exe -m valcoach` using argument arrays and `shell: false`. Export paths come from the native picker. Requests have a two-minute timeout and a 16 MiB output budget. Closing the app cancels active requests. The internal engine adapter retains round-query support for future coaching and developer checks.

Replay files are read locally; the existing engine can write its normalized cache under the repository's ignored `.valcoach-cache/`. This prototype has no app telemetry, model requests or external UI assets. The development server binds to loopback. Private reports, caches, native dependencies and build output remain outside Git.
