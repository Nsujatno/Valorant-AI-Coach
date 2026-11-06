import { useEffect, useRef, useState } from 'react'
import islandUrl from './assets/ascent-island.png'
import { mountDronePatrol } from './drone-flight.mjs'
import { SovaDrone } from './SovaDrone'

export function IslandScene(): React.JSX.Element {
  const stage = useRef<HTMLDivElement>(null)
  const drone = useRef<HTMLDivElement>(null)
  const [imageFailed, setImageFailed] = useState(false)

  useEffect(() => {
    if (!stage.current || !drone.current) return
    const controller = mountDronePatrol(stage.current, drone.current)
    return () => controller.destroy()
  }, [])

  return (
    <div className="island-scene" ref={stage}>
      <div className="scene-halo" aria-hidden="true" />
      <svg className="scene-orbit" viewBox="0 0 600 280" fill="none" aria-hidden="true"><ellipse cx="300" cy="140" rx="274" ry="100" stroke="currentColor" strokeWidth=".8" /><circle cx="40" cy="108" r="2.2" fill="currentColor" /><circle cx="556" cy="176" r="1.5" fill="currentColor" /></svg>
      <div className="scene-shadow" aria-hidden="true" />
      <svg className="scene-fragment fragment-back" viewBox="0 0 40 48" aria-hidden="true"><path d="m3 12 24-9 11 10-17 31L3 12Z" fill="#756779" /><path d="m3 12 24-9 11 10-22 4L3 12Z" fill="#b8a2a2" /><path d="m16 17 22-4-17 31-5-27Z" fill="#514a61" /></svg>
      {!imageFailed && <img className="scene-island" src={islandUrl} width="1536" height="1024" alt="An Ascent-inspired floating island with Venetian rooftops and a courtyard tree" draggable={false} onError={() => setImageFailed(true)} />}
      <svg className="scene-fragment fragment-front" viewBox="0 0 40 48" aria-hidden="true"><path d="m4 8 21-5 12 12-20 29L4 8Z" fill="#897580" /><path d="m4 8 21-5 12 12-21 4L4 8Z" fill="#c5a8a1" /><path d="m16 19 21-4-20 29-1-25Z" fill="#5c5268" /></svg>
      <div className="drone-flight" ref={drone} aria-hidden="true"><SovaDrone /></div>
    </div>
  )
}
