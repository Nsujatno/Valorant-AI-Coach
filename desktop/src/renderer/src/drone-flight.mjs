// Decorative choreography, not a reconstruction of the in-game ability.
export const DRONE_DURATION_MS = 34_000
export const DRONE_REDUCED_DURATION_MS = 50_000

export const DRONE_POSES = [
  { at: 0, x: -28, y: -16, scale: .8, bank: -5, front: false, opacity: .65 },
  { at: .12, x: -22, y: -32, scale: .76, bank: 2, front: false, opacity: .65 },
  { at: .27, x: 9, y: -31, scale: .77, bank: 6, front: false, opacity: .65 },
  { at: .4, x: 34, y: -10, scale: .84, bank: 8, front: false, opacity: .65 },
  { at: .42, x: 35, y: -5, scale: .85, bank: 7, front: true, opacity: .65 },
  { at: .52, x: 24, y: 22, scale: .9, bank: 4, front: true, opacity: .65 },
  { at: .64, x: -2, y: 23, scale: .9, bank: -3, front: true, opacity: .65 },
  { at: .74, x: -28, y: 8, scale: .85, bank: -7, front: true, opacity: .65 },
  { at: .8, x: -35, y: -5, scale: .81, bank: -5, front: true, opacity: .65 },
  { at: .82, x: -34, y: -11, scale: .8, bank: -4, front: false, opacity: .65 },
  { at: .9, x: -25, y: -18, scale: .8, bank: -2, front: false, opacity: .65 },
  { at: 1, x: -28, y: -16, scale: .8, bank: -5, front: false, opacity: .65 }
]

function curveValue(property, index, progress) {
  const a = DRONE_POSES[index]
  const b = DRONE_POSES[index + 1]
  if (a[property] === b[property]) return a[property]
  // Wrap the neighboring times so position and velocity meet at the loop seam.
  const previous = index === 0
    ? { ...DRONE_POSES.at(-2), at: DRONE_POSES.at(-2).at - 1 }
    : DRONE_POSES[index - 1]
  const next = index === DRONE_POSES.length - 2
    ? { ...DRONE_POSES[1], at: DRONE_POSES[1].at + 1 }
    : DRONE_POSES[index + 2]
  const span = b.at - a.at
  const startSlope = (b[property] - previous[property]) / (b.at - previous.at)
  const endSlope = (next[property] - a[property]) / (next.at - a.at)
  const squared = progress * progress
  const cubed = squared * progress
  return (2 * cubed - 3 * squared + 1) * a[property]
    + (cubed - 2 * squared + progress) * span * startSlope
    + (-2 * cubed + 3 * squared) * b[property]
    + (cubed - squared) * span * endSlope
}

function keyframe(pose, reduced) {
  return {
    offset: pose.at,
    transform: `translate(${pose.x}%, ${pose.y}%) rotate(${reduced ? 0 : pose.bank}deg) scale(${reduced ? .8 : pose.scale})`,
    opacity: pose.opacity,
    zIndex: pose.front ? 4 : 1
  }
}

// Sample a time-weighted curve so the drone banks through turns instead of
// stopping at each waypoint. The compositor interpolates these local frames.
function flightFrames(reduced = false) {
  return DRONE_POSES.slice(0, -1).flatMap((a, index) => {
    const b = DRONE_POSES[index + 1]
    return Array.from({ length: 8 }, (_, step) => {
      const progress = step / 8
      return keyframe({
        at: a.at + (b.at - a.at) * progress,
        x: curveValue('x', index, progress),
        y: curveValue('y', index, progress),
        scale: curveValue('scale', index, progress),
        bank: curveValue('bank', index, progress),
        opacity: a.opacity + (b.opacity - a.opacity) * progress,
        front: a.front
      }, reduced)
    })
  }).concat(keyframe(DRONE_POSES.at(-1), reduced))
}

export const DRONE_KEYFRAMES = flightFrames()
export const DRONE_REDUCED_KEYFRAMES = flightFrames(true)

export function mountDronePatrol(scene, carrier, environment = window) {
  const reducedMotion = environment.matchMedia('(prefers-reduced-motion: reduce)')
  let duration = reducedMotion.matches ? DRONE_REDUCED_DURATION_MS : DRONE_DURATION_MS
  const animation = typeof carrier.animate === 'function' ? carrier.animate(reducedMotion.matches ? DRONE_REDUCED_KEYFRAMES : DRONE_KEYFRAMES, {
    duration, iterations: Infinity, fill: 'both'
  }) : null
  animation?.pause()
  if (animation) animation.currentTime = 0
  let visible = true
  let destroyed = false

  function update() {
    const running = Boolean(animation) && !destroyed && visible
      && environment.document.visibilityState !== 'hidden'
    scene.setAttribute('data-drone-running', String(running))
    scene.setAttribute('data-drone-reduced', String(reducedMotion.matches))
    if (destroyed) return
    if (running) animation.play()
    else animation?.pause()
  }

  function preferenceChanged() {
    const nextDuration = reducedMotion.matches ? DRONE_REDUCED_DURATION_MS : DRONE_DURATION_MS
    if (animation) {
      const progress = Number(animation.currentTime || 0) / duration
      animation.effect.setKeyframes(reducedMotion.matches ? DRONE_REDUCED_KEYFRAMES : DRONE_KEYFRAMES)
      animation.effect.updateTiming({ duration: nextDuration })
      animation.currentTime = progress * nextDuration
    }
    duration = nextDuration
    update()
  }

  environment.document.addEventListener('visibilitychange', update)
  reducedMotion.addEventListener('change', preferenceChanged)
  const observer = environment.IntersectionObserver ? new environment.IntersectionObserver(([entry]) => {
    visible = entry.isIntersecting
    update()
  }) : null
  observer?.observe(scene)
  update()

  return {
    destroy() {
      destroyed = true
      animation?.cancel()
      update()
      environment.document.removeEventListener('visibilitychange', update)
      reducedMotion.removeEventListener('change', preferenceChanged)
      observer?.disconnect()
    }
  }
}
