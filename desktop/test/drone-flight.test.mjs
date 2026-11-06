import test from 'node:test'
import assert from 'node:assert/strict'
import { DRONE_DURATION_MS, DRONE_REDUCED_DURATION_MS, DRONE_KEYFRAMES, DRONE_REDUCED_KEYFRAMES, mountDronePatrol } from '../src/renderer/src/drone-flight.mjs'

function target(extra = {}) {
  const listeners = new Map()
  return {
    ...extra,
    addEventListener(name, callback) {
      if (!listeners.has(name)) listeners.set(name, new Set())
      listeners.get(name).add(callback)
    },
    removeEventListener(name, callback) { listeners.get(name)?.delete(callback) },
    emit(name) { for (const callback of listeners.get(name) || []) callback() },
    listenerCount() { return [...listeners.values()].reduce((total, set) => total + set.size, 0) }
  }
}

function scene({ reduced = false, supported = true } = {}) {
  const attributes = new Map()
  const element = { setAttribute: (name, value) => attributes.set(name, value) }
  const media = target({ matches: reduced })
  let observer
  let options
  const animation = {
    playState: 'idle', currentTime: null,
    effect: {
      frames: null,
      setKeyframes(frames) { this.frames = frames },
      updateTiming(settings) { Object.assign(options, settings) }
    },
    play() { this.playState = 'running' },
    pause() { this.playState = 'paused' },
    cancel() { this.playState = 'idle'; this.currentTime = null }
  }
  const carrier = supported ? { animate(frames, settings) { options = settings; animation.effect.frames = frames; return animation } } : {}
  const environment = {
    document: target({ visibilityState: 'visible' }),
    matchMedia: () => media,
    IntersectionObserver: class {
      constructor(callback) { this.callback = callback; this.disconnected = false; observer = this }
      observe() {}
      disconnect() { this.disconnected = true }
    }
  }
  const controller = mountDronePatrol(element, carrier, environment)
  return { attributes, media, animation, environment, controller, options, observer: () => observer }
}

test('small muted drone makes a full bounded flight around the island with front/back passes', () => {
  assert.equal(DRONE_KEYFRAMES[0].offset, 0)
  assert.equal(DRONE_KEYFRAMES.at(-1).offset, 1)
  assert.equal(DRONE_KEYFRAMES[0].transform, DRONE_KEYFRAMES.at(-1).transform)
  assert.ok(DRONE_DURATION_MS >= 30_000 && DRONE_DURATION_MS <= 40_000)
  assert.ok(DRONE_KEYFRAMES.some(frame => frame.zIndex === 1))
  assert.ok(DRONE_KEYFRAMES.some(frame => frame.zIndex === 4))
  assert.ok(DRONE_KEYFRAMES.some(frame => Number(frame.transform.match(/translate\([^,]+, ([^%]+)/)[1]) > 20), 'Flight crosses the front arc')
  let previous = -1
  for (const frame of DRONE_KEYFRAMES) {
    assert.ok(frame.offset > previous)
    previous = frame.offset
    const values = frame.transform.match(/-?\d+(?:\.\d+)?(?:e[+-]?\d+)?/g).map(Number)
    assert.equal(values.length, 4)
    assert.ok(values.every(Number.isFinite))
    const [x, y, bank, scale] = values
    assert.ok(Math.abs(x) < 40 && Math.abs(y) < 36, 'Route stays inside the scene')
    assert.ok(Math.abs(bank) < 10 && scale > .7 && scale < 1, 'Banks read as flight without looming')
    assert.ok(frame.opacity > .4 && frame.opacity <= .7)
    assert.equal(frame.opacity, DRONE_KEYFRAMES[0].opacity, 'No attention-grabbing blinking')
  }
})

test('gentle profile retains flight while removing banking and scale changes', () => {
  assert.ok(DRONE_REDUCED_DURATION_MS > DRONE_DURATION_MS)
  for (const [index, frame] of DRONE_REDUCED_KEYFRAMES.entries()) {
    const normal = DRONE_KEYFRAMES[index]
    assert.equal(frame.offset, normal.offset)
    assert.equal(frame.zIndex, normal.zIndex)
    assert.equal(frame.transform.split(' rotate')[0], normal.transform.split(' rotate')[0])
    assert.ok(frame.transform.endsWith('rotate(0deg) scale(0.8)'))
  }
})

test('ambient patrol starts automatically without interaction or a control API', () => {
  const s = scene()
  assert.equal(s.animation.playState, 'running')
  assert.equal(s.attributes.get('data-drone-running'), 'true')
  assert.equal(s.options.duration, DRONE_DURATION_MS)
  assert.equal(s.animation.currentTime, 0)
  s.controller.destroy()
})

test('reduced motion still flies automatically and profile changes preserve the lap phase', () => {
  const s = scene({ reduced: true })
  assert.equal(s.animation.playState, 'running')
  assert.equal(s.options.duration, DRONE_REDUCED_DURATION_MS)
  assert.equal(s.animation.effect.frames, DRONE_REDUCED_KEYFRAMES)
  s.animation.currentTime = DRONE_REDUCED_DURATION_MS * .4
  s.media.matches = false
  s.media.emit('change')
  assert.equal(s.animation.playState, 'running')
  assert.equal(s.animation.effect.frames, DRONE_KEYFRAMES)
  assert.equal(s.animation.currentTime, DRONE_DURATION_MS * .4)
  s.media.matches = true
  s.media.emit('change')
  assert.equal(s.animation.playState, 'running')
  assert.equal(s.animation.currentTime, DRONE_REDUCED_DURATION_MS * .4)
  assert.equal(s.attributes.get('data-drone-reduced'), 'true')
  s.controller.destroy()
})

test('hidden and offscreen scenes suspend the patrol and resume without a time jump', () => {
  const s = scene()
  s.animation.currentTime = 12_000
  s.environment.document.visibilityState = 'hidden'
  s.environment.document.emit('visibilitychange')
  assert.equal(s.animation.playState, 'paused')
  s.environment.document.visibilityState = 'visible'
  s.environment.document.emit('visibilitychange')
  assert.equal(s.animation.playState, 'running')
  assert.equal(s.animation.currentTime, 12_000)
  s.observer().callback([{ isIntersecting: false }])
  assert.equal(s.animation.playState, 'paused')
  s.observer().callback([{ isIntersecting: true }])
  assert.equal(s.animation.playState, 'running')
  assert.equal(s.animation.currentTime, 12_000)
  s.controller.destroy()
})

test('unmount cancels the animation and removes every lifecycle listener', () => {
  const s = scene()
  s.controller.destroy()
  assert.equal(s.animation.playState, 'idle')
  assert.equal(s.animation.currentTime, null)
  assert.equal(s.attributes.get('data-drone-running'), 'false')
  assert.equal(s.media.listenerCount(), 0)
  assert.equal(s.environment.document.listenerCount(), 0)
  assert.equal(s.observer().disconnected, true)
})

test('a renderer without Web Animations keeps a static drone and still cleans up', () => {
  const s = scene({ supported: false })
  assert.equal(s.attributes.get('data-drone-running'), 'false')
  s.controller.destroy()
  assert.equal(s.environment.document.listenerCount(), 0)
})
