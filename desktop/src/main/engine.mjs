import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import { isAbsolute, join, resolve } from 'node:path'

export class EngineError extends Error {
  constructor(code, message) {
    super(message)
    this.name = 'EngineError'
    this.code = code
  }
}

export function pythonForRepo(repoRoot, platform = process.platform) {
  return platform === 'win32'
    ? join(repoRoot, '.venv', 'Scripts', 'python.exe')
    : join(repoRoot, '.venv', 'bin', 'python')
}

// Keep the command surface small: no caller-supplied executable, flags or shell text.
export function queryArguments(command, exportPath, request = {}) {
  if (typeof exportPath !== 'string' || !isAbsolute(exportPath) || exportPath.includes('\0')) {
    throw new EngineError('invalid_export', 'Choose an existing export folder first.')
  }
  const args = ['-X', 'utf8', '-m', 'valcoach']
  if (command === 'players') args.push('players')
  else if (command === 'round') {
    if (!Number.isSafeInteger(request.round) || request.round < 1) {
      throw new EngineError('invalid_round', 'Choose a valid round number.')
    }
    if (typeof request.player !== 'string' || !request.player.trim()
      || request.player.length > 200 || request.player.startsWith('-') || request.player.includes('\0')) {
      throw new EngineError('invalid_player', 'Choose a player from this export.')
    }
    args.push('round', String(request.round), '--player', request.player)
  } else throw new EngineError('invalid_query', 'This prototype supports player discovery and round queries.')
  return [...args, '--export', exportPath, '--json']
}

export function createEngineClient({ repoRoot, spawnProcess = spawn, fileExists = existsSync, timeoutMs = 120_000, maxBytes = 16 * 1024 * 1024 }) {
  const root = resolve(repoRoot)
  const python = pythonForRepo(root)
  const active = new Map()

  function run(command, exportPath, request) {
    const args = queryArguments(command, exportPath, request)
    if (!fileExists(python)) {
      throw new EngineError('python_missing', `The existing project environment was not found at ${python}. Nothing was installed.`)
    }
    return new Promise((resolveResult, reject) => {
      let stdout = ''
      let stderr = ''
      let bytes = 0
      let settled = false
      let timer
      let child
      const finish = (error, value) => {
        if (settled) return
        settled = true
        clearTimeout(timer)
        active.delete(child)
        if (error) reject(error)
        else resolveResult(value)
      }
      const stop = (error) => {
        finish(error)
        child.kill()
      }
      try {
        child = spawnProcess(python, args, {
          cwd: root,
          shell: false,
          windowsHide: true,
          stdio: ['ignore', 'pipe', 'pipe'],
          env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' }
        })
      } catch {
        finish(new EngineError('engine_start_failed', 'Could not start the project Python environment.'))
        return
      }
      active.set(child, () => stop(new EngineError('cancelled', 'Query cancelled.')))
      timer = setTimeout(() => stop(new EngineError('engine_timeout', 'The query took longer than two minutes. You can retry or cancel.')), timeoutMs)
      child.stdout.setEncoding('utf8')
      child.stderr.setEncoding('utf8')
      child.stdout.on('data', (text) => {
        if (settled) return
        bytes += Buffer.byteLength(text)
        if (bytes > maxBytes) stop(new EngineError('output_limit', 'The query exceeded the prototype output budget. Use the CLI for this report.'))
        else stdout += text
      })
      child.stderr.on('data', (text) => { stderr = (stderr + text).slice(-4000) })
      child.once('error', () => finish(new EngineError('engine_start_failed', 'Could not start the project Python environment.')))
      child.once('close', (code) => {
        if (settled) return
        let data
        try { data = JSON.parse(stdout) }
        catch {
          finish(new EngineError('invalid_engine_response', code === 0
            ? 'The engine returned unreadable JSON.'
            : `The engine stopped without a structured result. ${stderr.trim() || 'Run npm run doctor to check the environment.'}`))
          return
        }
        if (!data || typeof data !== 'object' || Array.isArray(data)) {
          finish(new EngineError('invalid_engine_response', 'The engine returned an unexpected JSON shape.'))
        } else if (code !== 0 || data.error) {
          finish(new EngineError(data.error?.code || 'engine_failed', data.error?.message || 'The engine query failed.'))
        } else if (data.query !== command) {
          finish(new EngineError('invalid_engine_response', 'The engine returned a different query type.'))
        } else if (command === 'players' && (data.schema_version !== 2
          || !Array.isArray(data.players) || !Array.isArray(data.rounds)
          || !data.players.every((player) => player && typeof player.id === 'string'
            && typeof player.agent === 'string'
            && (player.starting_side === null || ['attacker', 'defender'].includes(player.starting_side))))) {
          finish(new EngineError('invalid_engine_response', 'The engine player listing is incomplete.'))
        } else if (command === 'round' && (!data.round || !data.selected_player || !data.players
          || !Array.isArray(data.timeline) || !Array.isArray(data.player_fights)
          || !Array.isArray(data.teammate_fights) || !Array.isArray(data.coverage?.limitations))) {
          finish(new EngineError('invalid_engine_response', 'The engine round report is incomplete.'))
        } else finish(null, data)
      })
    })
  }

  return {
    environment: () => ({ pythonAvailable: fileExists(python), pythonPath: python }),
    listPlayers: (exportPath) => run('players', exportPath),
    loadRound: (exportPath, request) => run('round', exportPath, request),
    cancel: () => { for (const stop of [...active.values()]) stop() }
  }
}
