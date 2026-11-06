import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { initialSelection, playerChoices, selectionReducer } from '../src/renderer/src/player-selection.mjs'

const match = JSON.parse(readFileSync(new URL('./fixtures/player-selection.json', import.meta.url), 'utf8'))

test('starting side distinguishes mirror agents using their real identity', () => {
  const attack = playerChoices(match.players, 'attacker')
  const defense = playerChoices(match.players, 'defender')
  assert.deepEqual(attack.map(player => player.label), ['Jett', 'Sova'])
  assert.deepEqual(defense.map(player => player.label), ['Jett', 'Fade'])
  assert.equal(attack.find(player => player.label === 'Jett').id, 'attack-jett')
  assert.equal(defense.find(player => player.label === 'Jett').id, 'defense-jett')
  assert.deepEqual(playerChoices(match.players, ''), [])
})

test('changing side clears the old player and rejects an opponent identity', () => {
  let state = selectionReducer(initialSelection, { type: 'match', match })
  state = selectionReducer(state, { type: 'side', side: 'attacker' })
  state = selectionReducer(state, { type: 'player', player: 'attack-jett' })
  assert.equal(state.player, 'attack-jett')
  state = selectionReducer(state, { type: 'side', side: 'defender' })
  assert.equal(state.player, '')
  state = selectionReducer(state, { type: 'player', player: 'attack-jett' })
  assert.equal(state.player, '')
  state = selectionReducer(state, { type: 'player', player: 'defense-jett' })
  assert.equal(state.player, 'defense-jett')
})

test('a newly loaded match resets both selections without mutating the old state', () => {
  const state = { match, side: 'attacker', player: 'attack-jett' }
  const next = selectionReducer(state, { type: 'match', match: { ...match, match_id: 'another-match' } })
  assert.equal(next.side, '')
  assert.equal(next.player, '')
  assert.equal(state.player, 'attack-jett')
  assert.equal(state.side, 'attacker')
})

test('missing sides are only offered in an explicit unknown group with distinct labels', () => {
  const players = match.players.map(player => ({ ...player, starting_side: null }))
  assert.deepEqual(playerChoices(players, 'attacker'), [])
  assert.deepEqual(playerChoices(players, 'defender'), [])
  const choices = playerChoices(players, 'unknown')
  assert.equal(choices.length, 4)
  assert.notEqual(choices[0].label, choices[2].label)
  assert.ok(choices.every(player => player.label.includes('Team') && player.label.includes('Player')))
})

test('same-side duplicate agent names retain explicit player identities', () => {
  const duplicate = { ...match.players[0], id: 'second-jett', state_id: 33, pawn: 5 }
  const choices = playerChoices([...match.players, duplicate], 'attacker')
  assert.notEqual(choices[0].label, choices[2].label)
  assert.notEqual(choices[0].id, choices[2].id)
})

test('unmapped agents retain their raw name and explicit identity in the UI', () => {
  const player = { ...match.players[0], agent: 'Unknown', agent_internal: 'UnmappedAgent' }
  const [choice] = playerChoices([player], 'attacker')
  assert.equal(choice.id, player.id)
  assert.ok(choice.label.includes('UnmappedAgent') && choice.label.includes('Player 11'))
})
