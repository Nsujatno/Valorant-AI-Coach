import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'
import { createEngineClient, pythonForRepo } from '../src/main/engine.mjs'

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), '../..')
const cases = [
  { name: 'original build', variable: 'VALCOACH_TEST_EXPORT', path: 'output/first-replay/raw', fixture: 'reference-match.json', startingSide: 'attacker' },
  { name: 'second build', variable: 'VALCOACH_TEST_EXPORT_1308', path: 'output/replay-5c31718d/raw', fixture: 'reference-13.08.json', startingSide: 'defender' }
]

for (const reference of cases) {
  const exportPath = process.env[reference.variable]
    ? resolve(process.env[reference.variable]) : join(repoRoot, reference.path)
  const available = existsSync(join(exportPath, 'manifest.json')) && existsSync(pythonForRepo(repoRoot))
  test(`real CLI bridge: player discovery and round one on ${reference.name}`, {
    skip: available ? false : 'Private reference export or project Python unavailable.',
    timeout: 150_000
  }, async () => {
    const expected = JSON.parse(readFileSync(join(repoRoot, 'tests/fixtures', reference.fixture), 'utf8'))
    const engine = createEngineClient({ repoRoot })
    try {
      const listing = await engine.listPlayers(exportPath)
      assert.equal(listing.rounds.length, expected.rounds)
      const selected = listing.players.find((player) => player.id === expected.selected_subject)
      assert.ok(selected)
      assert.equal(listing.schema_version, 2)
      assert.equal(selected.starting_side, reference.startingSide)
      assert.equal(listing.players.filter(player => player.starting_side === 'attacker').length, 5)
      assert.equal(listing.players.filter(player => player.starting_side === 'defender').length, 5)
      assert.ok(listing.players.every(player => player.starting_side_evidence.normalized_team_role_indices.length > 0))
      const report = await engine.loadRound(exportPath, { player: selected.id, round: 1 })
      assert.equal(report.selected_player.pawn, expected.selected_pawn)
      assert.equal(report.round.number, 1)
      assert.equal(report.round.live_ms, expected.round_one_live_ms ?? expected.round_one.live_ms)
      assert.equal(report.match_id, listing.match_id)
      assert.equal(report.revision, listing.revision)
      assert.ok(report.timeline.length > 0)
      assert.ok(report.timeline.every((event) => typeof event.id === 'string' && Number.isFinite(event.round_time_ms)))
      assert.ok(Array.isArray(report.coverage.limitations))
    } finally { engine.cancel() }
  })
}
