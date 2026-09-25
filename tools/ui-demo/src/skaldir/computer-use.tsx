/** abgenommenes Computer-Use-Overlay: vier L-Ecken, Puls zur Mitte
 *  Quelle: designs/GEWAEHLT/code/computer-use.tsx (abgenommen am 17.09.2026) — unveraendert uebernommen. */
/**
 * Computer-Use — Fassung 3 (angenommen), nur das Stopp-Viereck ist präzisiert.
 *
 * Angenommen: „03 · L 120 px · Schenkel 12 → 2 px · Blur 4" — vier identische abgerundete
 * L-Elemente, eins je Ecke, gleich lange Schenkel, weich abgerundete Ecke, Verjüngung nach
 * außen zur Bildkante, geblurt und pulsierend (Takt 4,8 s, Grau 56 %).
 *
 * Neu hier: Im Stopp-Viereck steht nicht mehr „esc esc", sondern eindeutig der Text,
 * der die Wirkung beschreibt. Zwei Schreibweisen zur Wahl:
 *   · „2× esc"  — zweimal Esc beendet sofort (einmal hält nur an)
 *   · „esc"     — nur ein Zeichen, die Wirkung steht in der Rand-Marke
 */
import React from 'react'

// @ts-expect-error — Laufzeit-Alias (siehe vite.config.ts)
import { ThinkingOrb } from 'thinking-orbs'

const C = { ink: '#171717', sub: '#595959', faint: '#8A8A8A', line: '#DEDEDE', dark: '#111111', darkText: '#F5F5F5', darkSub: '#B7B7B7' }
const SANS = 'Geist, system-ui, sans-serif'
const MONO = 'ui-monospace, SFMono-Regular, Menlo, monospace'

const CSS = `@keyframes cuL { 0% { opacity: .55 } 50% { opacity: .95 } 100% { opacity: .55 } } .cuL { animation: cuL 4.8s ease-in-out infinite; pointer-events: none; }`

const ARM = 120, TIN = 12, TEND = 2, R = 18, BLUR = 4, GREY = 0.56

const LShape = ({ corner, dark }: { corner: 'tl' | 'tr' | 'bl' | 'br'; dark?: boolean }) => {
  const d = [
    `M ${ARM} 0`, `L ${R} 0`, `A ${R} ${R} 0 0 0 0 ${R}`, `L 0 ${ARM}`,
    `L ${TEND} ${ARM}`, `L ${TIN} ${TIN}`, `L ${ARM} ${TEND}`, 'Z'
  ].join(' ')
  const flip = corner === 'tl' ? 'none' : corner === 'tr' ? 'scaleX(-1)' : corner === 'bl' ? 'scaleY(-1)' : 'scale(-1,-1)'
  const pos: React.CSSProperties =
    corner === 'tl' ? { left: -1, top: -1 } : corner === 'tr' ? { right: -1, top: -1 } : corner === 'bl' ? { left: -1, bottom: -1 } : { right: -1, bottom: -1 }
  const col = dark ? `rgba(226,226,226,${GREY})` : `rgba(108,108,108,${GREY})`
  return (
    <svg className="cuL" width={ARM} height={ARM} viewBox={`0 0 ${ARM} ${ARM}`}
      style={{ position: 'absolute', ...pos, transform: flip, filter: `blur(${BLUR}px)` }}>
      <path d={d} fill={col} stroke={col} strokeWidth={1.5} strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  )
}

const Arrow = ({ size = 22, dark }: { size?: number; dark?: boolean }) => (
  <svg width={size} height={size} viewBox="0 0 24 24">
    <path d="M2 1 L2 19 L6.6 14.6 L9.6 21 L12.4 19.6 L9.3 13.3 L15.4 12.9 Z"
      fill={dark ? 'rgba(245,245,245,0.94)' : 'rgba(250,250,250,0.96)'} stroke={dark ? 'rgba(120,120,120,0.9)' : 'rgba(168,168,168,0.95)'} strokeWidth="1.1" />
  </svg>
)

const StopSquare = ({ text, dark }: { text: string; dark?: boolean }) => (
  <span style={{
    minWidth: 40, height: 26, padding: '0 7px', borderRadius: 8,
    background: dark ? C.darkText : C.ink, color: dark ? C.dark : '#FFFFFF',
    display: 'inline-flex', alignItems: 'center', justifyContent: 'center'
  }}>
    <span style={{ fontFamily: MONO, fontSize: 8.5, whiteSpace: 'nowrap' }}>{text}</span>
  </span>
)

const Pill = ({ text, dark }: { text: string; dark?: boolean }) => (
  <div style={{
    position: 'absolute', left: '50%', bottom: 14, transform: 'translateX(-50%)',
    display: 'flex', alignItems: 'center', gap: 10, padding: '0 10px 0 13px', height: 32,
    background: dark ? 'rgba(17,17,17,0.88)' : 'rgba(255,255,255,0.94)',
    border: `1.15px solid ${dark ? 'rgba(255,255,255,0.3)' : 'rgba(23,23,23,0.85)'}`, borderRadius: 999
  }}>
    <ThinkingOrb state={'working' as never} size={20} theme={dark ? 'dark' : 'light'} />
    <span style={{ fontSize: 12, color: dark ? C.darkText : C.ink, whiteSpace: 'nowrap' }}>Skaldir steuert</span>
    <span style={{ fontFamily: MONO, fontSize: 11, color: dark ? C.darkSub : C.sub }}>00:42</span>
    <StopSquare text={text} dark={dark} />
  </div>
)

const Stage = ({ text, dark }: { text: string; dark?: boolean }) => (
  <div style={{ position: 'relative', height: 250, borderRadius: 14, overflow: 'hidden', background: dark ? '#1B1B1B' : '#F3F3F3', border: `1px solid ${dark ? '#333' : C.line}` }}>
    <div style={{ position: 'absolute', left: 18, top: 16, right: 18, height: 24, borderRadius: 6, background: dark ? '#262626' : '#E7E7E7' }} />
    <div style={{ position: 'absolute', left: 18, top: 50, width: 154, height: 168, borderRadius: 8, background: dark ? '#222' : '#EBEBEB' }} />
    <div style={{ position: 'absolute', left: 182, top: 50, right: 18, bottom: 20, borderRadius: 8, background: dark ? '#202020' : '#EEEEEE' }} />
    <div data-gv-overlay="human-only" data-agent-visible="false" style={{ position: 'absolute', inset: 0, pointerEvents: 'none' }}>
      <LShape corner="tl" dark={dark} /><LShape corner="tr" dark={dark} /><LShape corner="bl" dark={dark} /><LShape corner="br" dark={dark} />
    </div>
    <div style={{ position: 'absolute', left: '54%', top: '42%' }}><Arrow dark={dark} /></div>
    <div data-gv-overlay="human-only" data-agent-visible="false"><Pill text={text} dark={dark} /></div>
  </div>
)

export const ComputerUse = ({ name, text, note, dark }: { name: string; text: string; note: string; dark?: boolean }) => (
  <div style={{ padding: 22, background: dark ? C.dark : '#FAFAFA', fontFamily: SANS, color: dark ? C.darkText : C.ink, width: 820 }}>
    <style>{CSS}</style>
    <div style={{ fontSize: 14.5, fontWeight: 600 }}>{name}</div>
    <div style={{ fontSize: 11.5, color: dark ? C.darkSub : C.sub, margin: '3px 0 12px' }}>{note}</div>
    <Stage text={text} dark={dark} />
    <div style={{ display: 'flex', alignItems: 'center', gap: 14, marginTop: 12 }}>
      <span style={{ fontFamily: MONO, fontSize: 10.5, color: dark ? C.darkSub : C.faint, letterSpacing: 0.5 }}>STOPP</span>
      <StopSquare text={text} dark={dark} />
      <span style={{ fontSize: 11, color: dark ? C.darkSub : C.sub }}>Einmal Esc: hält an und fragt nach · zweimal Esc: beendet sofort.</span>
    </div>
  </div>
)

