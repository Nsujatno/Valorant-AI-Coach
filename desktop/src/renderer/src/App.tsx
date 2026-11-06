import { useEffect, useReducer, useRef, useState } from 'react'
import type { Environment, Result, StartingSide } from '../../shared/contracts'
import { IslandScene } from './IslandScene'
import { initialSelection, playerChoices, selectionReducer } from './player-selection.mjs'

type IconName = 'arrow' | 'chevron' | 'code' | 'folder' | 'key' | 'sides' | 'spark' | 'user'

function Icon({ name }: { name: IconName }): React.JSX.Element {
  const paths: Record<IconName, React.ReactNode> = {
    arrow: <path d="M5 12h14m-6-6 6 6-6 6" />,
    chevron: <path d="m7 10 5 5 5-5" />,
    code: <path d="m8 7-5 5 5 5m8-10 5 5-5 5m-3-13-2 20" />,
    folder: <path d="M3 7V5a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Zm0 2h18" />,
    key: <><circle cx="8" cy="8" r="5" /><path d="m12 12 9 9m-5-5 3-3" /></>,
    sides: <path d="M4 7h15m-4-4 4 4-4 4M20 17H5m4-4-4 4 4 4" />,
    spark: <path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5L12 3Z" />,
    user: <><circle cx="12" cy="8" r="4" /><path d="M4 21v-2a8 8 0 0 1 16 0v2" /></>
  }
  return <svg className="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>
}

function unwrap<T>(result: Result<T>): T {
  if (!result.ok) throw new Error(`${result.error.message} (${result.error.code})`)
  return result.value
}

const reviewPrompts = [
  { label: 'Review my match', text: 'Review my match and help me choose one thing to practise.' },
  { label: 'Work through a round', text: 'Help me work through a decision in round ' }
]

export default function App(): React.JSX.Element {
  const api = window.valcoach
  const [environment, setEnvironment] = useState<Environment | null>(null)
  const [identity, dispatchIdentity] = useReducer(selectionReducer, initialSelection)
  const { match: selection, side: startingSide, player } = identity
  const [focus, setFocus] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const requestInput = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    let active = true
    if (api) void api.environment().then((result) => {
      if (active) setEnvironment(unwrap(result))
    }).catch((reason: Error) => { if (active) setError(reason.message) })
    return () => { active = false }
  }, [api])

  async function chooseExport(): Promise<void> {
    if (!api) return
    setBusy(true)
    setError(null)
    try {
      const chosen = unwrap(await api.chooseExport())
      if (!chosen) return
      dispatchIdentity({ type: 'match', match: chosen })
    } catch (reason) { setError((reason as Error).message) }
    finally { setBusy(false) }
  }

  async function cancel(): Promise<void> {
    if (!api) return
    try { unwrap(await api.cancelQuery()) }
    catch (reason) { setError((reason as Error).message) }
  }

  async function openDevTools(): Promise<void> {
    if (!api) return
    try { unwrap(await api.openDevTools()) }
    catch (reason) { setError((reason as Error).message) }
  }

  const ready = Boolean(api && environment?.pythonAvailable)
  const choices = selection ? playerChoices(selection.players, startingSide) : []
  const hasUnknownSides = selection?.players.some(entry => entry.starting_side === null) ?? false

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="brand"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 4h5l5 12-3 5L3 4Zm12 0h6l-5 12-3-7 2-5Z" fill="currentColor" /></svg>ValCoach</div>
        <div className="header-actions">
          <details className="connection-menu">
            <summary><span className="connection-dot" />AI not connected<Icon name="chevron" /></summary>
            <div className="connection-popover">
              <h2>AI connections are coming soon</h2>
              <button disabled><Icon name="spark" /><span>Sign in with ChatGPT<small>Use your eligible plan allowance</small></span></button>
              <button disabled><Icon name="key" /><span>Use an API key<small>Separate API billing · session only</small></span></button>
              <p>You’ll choose the billing mode. It will never switch automatically.</p>
            </div>
          </details>
          <button className="icon-button devtools" disabled={!api} onClick={() => void openDevTools()} aria-label="Open DevTools" title="Open DevTools (F12)"><Icon name="code" /></button>
        </div>
      </header>

      <main className="workspace">
        <IslandScene />
        <section className="session" aria-labelledby="session-heading">
          <h1 id="session-heading">What do you want to work on?</h1>
          <div className="review-prompts" aria-label="Review starting points">
            {reviewPrompts.map((prompt) => <button key={prompt.label} className={focus === prompt.text ? 'selected' : ''} aria-pressed={focus === prompt.text} onClick={() => { setFocus(prompt.text); requestInput.current?.focus() }}>{prompt.label}<Icon name="arrow" /></button>)}
          </div>

          {environment && !environment.pythonAvailable && <div className="notice" role="alert">The project Python environment is missing. Run <code>npm run doctor</code> from the desktop folder for setup details.</div>}
          {error && <div className="notice" role="alert">{error}</div>}

          <div className="match-toolbar">
            <button className={`match-picker ${selection ? 'has-selection' : ''}`} onClick={() => void chooseExport()} disabled={!ready || busy} title={selection?.exportPath} aria-describedby="export-help"><Icon name="folder" /><span>{busy ? 'Preparing match…' : selection ? selection.folderName : 'Choose a replay export'}</span><Icon name="chevron" /></button>
            <div className="select-wrap"><Icon name="sides" /><label className="sr-only" htmlFor="starting-side">I started on</label><select id="starting-side" value={startingSide} onChange={event => dispatchIdentity({ type: 'side', side: event.target.value as StartingSide | 'unknown' | '' })} disabled={!selection || busy}><option value="">Starting side</option><option value="attacker" disabled={!selection?.players.some(entry => entry.starting_side === 'attacker')}>Attack</option><option value="defender" disabled={!selection?.players.some(entry => entry.starting_side === 'defender')}>Defense</option>{hasUnknownSides && <option value="unknown">Side not recorded</option>}</select></div>
            <div className="select-wrap"><Icon name="user" /><label className="sr-only" htmlFor="player">Your agent</label><select id="player" value={player} onChange={event => dispatchIdentity({ type: 'player', player: event.target.value })} disabled={!selection || !startingSide || choices.length === 0 || busy}><option value="">{!selection ? 'Your agent' : !startingSide ? 'Choose side first' : 'Choose your agent'}</option>{choices.map(entry => <option key={entry.id} value={entry.id}>{entry.label}</option>)}</select></div>
          </div>
          <div className="match-detail" id="export-help">{busy ? <div className="loading" role="status" aria-live="polite"><span className="spinner" />Finding players…<button className="text-button" onClick={() => void cancel()}>Cancel</button></div> : selection ? `${selection.rounds.length} rounds · ${selection.players.length} players` : !api ? 'Open the desktop app to select a match.' : 'Existing export folders only. Direct .vrf import is coming later.'}</div>
          {selection && hasUnknownSides && <p className="selection-note">Some starting sides weren’t recorded. Choose “Side not recorded” to find those players; team and player IDs distinguish duplicate agents.</p>}

          <div className="composer">
            <label className="sr-only" htmlFor="coaching-focus">Your coaching request</label>
            <textarea ref={requestInput} id="coaching-focus" value={focus} onChange={(event) => setFocus(event.target.value)} maxLength={2000} rows={3} placeholder="Ask for a match review, or tell me about a round…" aria-describedby="draft-help" />
            <div className="composer-footer"><span>{focus.length > 0 ? `${focus.length.toLocaleString()} / 2,000` : 'Private draft'}</span><button className="start-button" disabled aria-describedby="draft-help">Start coaching<Icon name="arrow" /></button></div>
          </div>
          <p className="draft-help" id="draft-help">AI coaching is coming soon. Your draft stays in this window until you close or reload it.</p>
        </section>
      </main>

      <footer className="app-footer"><span>Replay processing stays on your computer.</span><span>Desktop preview{environment ? ` · v${environment.version}` : ''}</span></footer>
    </div>
  )
}
