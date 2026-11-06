/** @typedef {import('../../shared/contracts').ReplaySelection} ReplaySelection */
/** @typedef {import('../../shared/contracts').Player} Player */
/** @typedef {'' | 'attacker' | 'defender' | 'unknown'} SideChoice */
/** @typedef {{match: ReplaySelection | null, side: SideChoice, player: string}} SelectionState */
/** @type {SelectionState} */
export const initialSelection = { match: null, side: '', player: '' }

/** @param {Player[]} players @param {SideChoice} side */
export function playerChoices(players, side) {
  const filtered = players.filter(player => side === 'unknown'
    ? player.starting_side === null : side !== '' && player.starting_side === side)
  return filtered.map(player => {
    const duplicate = filtered.filter(other => other.agent === player.agent).length > 1
    const unknownAgent = player.agent === 'Unknown'
    const agent = unknownAgent && player.agent_internal ? `Unknown (${player.agent_internal})` : player.agent
    const identity = player.state_id ?? player.pawn ?? player.id
    const label = duplicate || side === 'unknown' || unknownAgent
      ? `${agent} · Team ${player.team ?? 'unknown'} · Player ${identity}` : agent
    return { id: player.id, label }
  })
}

/**
 * @param {SelectionState} state
 * @param {{type: 'match', match: ReplaySelection} | {type: 'side', side: SideChoice} | {type: 'player', player: string}} action
 * @returns {SelectionState}
 */
export function selectionReducer(state, action) {
  if (action.type === 'match') return { match: action.match, side: '', player: '' }
  if (action.type === 'side') {
    if (!state.match || !['', 'attacker', 'defender', 'unknown'].includes(action.side)) return state
    return { ...state, side: action.side, player: '' }
  }
  if (action.type === 'player') {
    const valid = state.match && playerChoices(state.match.players, state.side).some(player => player.id === action.player)
    return { ...state, player: valid ? action.player : '' }
  }
  return state
}
