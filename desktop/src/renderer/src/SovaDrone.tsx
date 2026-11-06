import { useId } from 'react'

// Original simplified vector study; no game texture or model is bundled.
export function SovaDrone(): React.JSX.Element {
  const metal = `drone-metal-${useId().replace(/:/g, '')}`
  return (
    <svg className="sova-drone" viewBox="0 0 120 72" fill="none" aria-hidden="true">
      <defs><linearGradient id={metal} x1="60" y1="16" x2="60" y2="57" gradientUnits="userSpaceOnUse"><stop stopColor="#bba9a5" /><stop offset=".45" stopColor="#91818b" /><stop offset="1" stopColor="#6b6178" /></linearGradient></defs>
      <path d="m39 29 14 5-3 8-16-6m47-7-14 5 3 8 16-6" fill="#85778b" stroke="#60596e" strokeWidth="1.5" />
      {[26, 94].map((x) => <g key={x} transform={`translate(${x} 29) rotate(${x === 26 ? -13 : 13})`}>
        <ellipse cy="3" rx="22" ry="13" fill="#59536a" stroke="#70677e" strokeWidth="1.5" />
        <ellipse rx="22" ry="13" fill="#6c6278" stroke="#a494a3" strokeWidth="1.5" />
        <ellipse rx="17" ry="9" fill="#59566b" stroke="#a5adb6" strokeOpacity=".45" strokeWidth="1" />
        <g transform="scale(1 .53)"><g className="drone-rotor"><path d="m0-16 3 13 13 3-13 3L0 16-3 3-16 0l13-3L0-16Z" fill="#b1acb9" opacity=".3" /></g></g>
        <ellipse rx="3.5" ry="2.3" fill="#b6abb9" />
        <path d="M-12-9A21 12 0 0 1 11-10" stroke="#c4b1b1" strokeOpacity=".45" />
      </g>)}
      <path d="m45 19 15 7 15-7 4 18-6 16-13 8-13-8-6-16 4-18Z" fill={`url(#${metal})`} stroke="#625a70" strokeWidth="1.2" />
      <path d="m45 19 15 7-12 8-6-3 3-12Zm30 0-15 7 12 8 6-3-3-12Z" fill="#b09ea5" />
      <path d="m45 35 15 4 15-4-3 12-12 6-12-6-3-12Z" fill="#5c596f" />
      <path d="m47 35 11 5-8 2-3-7Zm26 0-11 5 8 2 3-7Z" fill="#b0bdc1" />
      <path d="m57 43 3-3 3 3-3 9-3-9Z" fill="#a69aa9" />
      <path d="m54 54 6 3 6-3" stroke="#b5a5b0" strokeWidth="1.1" />
      <circle cx="60" cy="32" r="1.4" fill="#c0b4bd" />
    </svg>
  )
}
