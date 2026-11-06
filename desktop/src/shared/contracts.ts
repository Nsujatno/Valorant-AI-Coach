export type Result<T> =
  | { ok: true; value: T }
  | { ok: false; error: { code: string; message: string } }

export type StartingSide = 'attacker' | 'defender'

export interface Player {
  id: string
  state_id: number | null
  pawn: number
  team: number | null
  agent: string
  agent_internal?: string | null
  starting_side: StartingSide | null
}

export interface PlayerListing {
  schema_version: 2
  query: 'players'
  match_id: string
  revision: string
  replay_build: string
  players: Player[]
  rounds: number[]
}

export interface ReplaySelection extends PlayerListing {
  exportPath: string
  folderName: string
}

export interface Environment {
  pythonAvailable: boolean
  pythonPath: string
  version: string
}

export interface DesktopApi {
  environment(): Promise<Result<Environment>>
  chooseExport(): Promise<Result<ReplaySelection | null>>
  cancelQuery(): Promise<Result<null>>
  openDevTools(): Promise<Result<null>>
}
