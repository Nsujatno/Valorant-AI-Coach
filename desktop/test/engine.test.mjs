import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import { resolve } from 'node:path'
import { PassThrough } from 'node:stream'
import test from 'node:test'
import { createEngineClient, pythonForRepo, queryArguments } from '../src/main/engine.mjs'

const root = resolve('synthetic-project')
const exportPath = resolve('synthetic export with spaces')
const listing = { schema_version: 2, query: 'players', players: [{ id: 'synthetic-player', agent: 'Jett', starting_side: 'attacker' }], rounds: [1] }

function harness(options = {}) {
  const launches = []
  const client = createEngineClient({
    repoRoot: root,
    fileExists: () => true,
    spawnProcess: (executable, args, settings) => {
      const process = new EventEmitter()
      process.stdout = new PassThrough()
      process.stderr = new PassThrough()
      process.killed = false
      process.kill = () => { process.killed = true; queueMicrotask(() => process.emit('close', null)); return true }
      launches.push({ executable, args, settings, process })
      return process
    },
    ...options
  })
  const complete = (value, code = 0) => {
    const child = launches.at(-1).process
    child.stdout.write(typeof value === 'string' ? value : JSON.stringify(value))
    child.emit('close', code)
  }
  return { client, launches, complete }
}

test('export paths and selectors stay literal arguments with shell execution disabled', async () => {
  const { client, launches, complete } = harness()
  const path = resolve('export & $(not-a-command)')
  const pending = client.listPlayers(path)
  complete(listing)
  assert.deepEqual(await pending, listing)
  assert.equal(launches[0].settings.shell, false)
  assert.equal(launches[0].settings.windowsHide, true)
  assert.equal(launches[0].settings.cwd, root)
  assert.equal(launches[0].executable, pythonForRepo(root))
  assert.deepEqual(launches[0].args, ['-X', 'utf8', '-m', 'valcoach', 'players', '--export', path, '--json'])
})

test('reject unsupported commands, malformed paths and flag-like player selectors before launch', () => {
  for (const [command, path, request] of [
    ['delete', exportPath, {}], ['players', 'relative-path', {}],
    ['players', `${exportPath}\0`, {}], ['round', exportPath, { player: '--output', round: 1 }],
    ['round', exportPath, { player: '', round: 1 }], ['round', exportPath, { player: 'a', round: 1.5 }],
    ['round', exportPath, { player: 'a', round: 0 }]
  ]) assert.throws(() => queryArguments(command, path, request))
})

test('round queries preserve supported selectors and add no output/write flags', () => {
  assert.deepEqual(queryArguments('round', exportPath, { round: 2, player: 'synthetic-player' }),
    ['-X', 'utf8', '-m', 'valcoach', 'round', '2', '--player', 'synthetic-player', '--export', exportPath, '--json'])
})

test('preserve structured Python query errors', async () => {
  const { client, complete } = harness()
  const pending = client.listPlayers(exportPath)
  complete({ error: { code: 'unsupported_build', message: 'This build is not validated.' } }, 1)
  await assert.rejects(pending, { code: 'unsupported_build', message: 'This build is not validated.' })
})

test('malformed, null, wrong-query and incomplete results produce readable bridge errors', async () => {
  for (const data of ['not-json', 'null', { query: 'round' }, { query: 'players' },
    { ...listing, schema_version: 1 }, { ...listing, players: [{ id: 'missing-side', agent: 'Jett' }] },
    { ...listing, players: [{ id: 'bad-side', agent: 'Jett', starting_side: 'blue-team' }] }]) {
    const { client, complete } = harness()
    const pending = client.listPlayers(exportPath)
    complete(data)
    await assert.rejects(pending, { code: 'invalid_engine_response' })
  }
})

test('failed process without JSON retains a bounded diagnostic', async () => {
  const { client, launches, complete } = harness()
  const pending = client.listPlayers(exportPath)
  launches[0].process.stderr.write('Python import failed')
  complete('', 1)
  await assert.rejects(pending, (error) => error.code === 'invalid_engine_response' && error.message.includes('Python import failed'))
})

test('explicitly unknown starting sides remain unknown across the process bridge', async () => {
  const { client, complete } = harness()
  const unknown = { ...listing, players: [{ ...listing.players[0], starting_side: null }] }
  const pending = client.listPlayers(exportPath)
  complete(unknown)
  assert.deepEqual(await pending, unknown)
})

test('query cancellation kills its process and the client remains reusable', async () => {
  const { client, launches, complete } = harness()
  const pending = client.listPlayers(exportPath)
  client.cancel()
  await assert.rejects(pending, { code: 'cancelled' })
  assert.equal(launches[0].process.killed, true)
  const retry = client.listPlayers(exportPath)
  complete(listing)
  assert.deepEqual(await retry, listing)
})

test('timeouts terminate stalled Python queries', async () => {
  const { client, launches } = harness({ timeoutMs: 10 })
  await assert.rejects(client.listPlayers(exportPath), { code: 'engine_timeout' })
  assert.equal(launches[0].process.killed, true)
})

test('oversized output is rejected and the query is terminated', async () => {
  const { client, launches } = harness({ maxBytes: 10 })
  const pending = client.listPlayers(exportPath)
  launches[0].process.stdout.write('a'.repeat(11))
  await assert.rejects(pending, { code: 'output_limit' })
  assert.equal(launches[0].process.killed, true)
})

test('missing Python and process launch failures never trigger installation', async () => {
  const missing = harness({ fileExists: () => false })
  assert.throws(() => missing.client.listPlayers(exportPath), { code: 'python_missing' })
  assert.equal(missing.launches.length, 0)
  const { client, launches } = harness()
  const pending = client.listPlayers(exportPath)
  launches[0].process.emit('error', new Error('ENOENT'))
  await assert.rejects(pending, { code: 'engine_start_failed' })
})
