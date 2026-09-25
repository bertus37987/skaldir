/**
 * Computer-Use — Fassung 3, **Option 2** (Stopp-Viereck „esc"), im Darkmode.
 *
 * Owner-Vorgabe 17.09.2026: „Computer use option 2 -> 20% schwächer pulsierend, sonst exakt gleich,
 * als Darkmode produzieren, den Rest löschen."
 *
 * Also unverändert: die vier identischen abgerundeten L-Elemente (Schenkel 120 px, Verjüngung
 * 12 -> 2 px, Eckradius 18 px), Blur 4 px, Grau 56 %, Wachstum von der Ecke Richtung Mitte mit
 * festgehaltenem Eckpunkt, Takt 3,6 s und 0,18 s Versatz je Ecke.
 * Schwächer: der Puls hat 20 % weniger Amplitude — Wachstumsfaktor 2,6 -> 2,28 und Deckkraft
 * 0,25..0,95 -> 0,32..0,88 (Mitte unverändert 0,60).
 * Dunkel: helle Tinte und dunkle Buehne kommen aus den Tokens (DARK.ink, DARK.bg).
 * Die anderen beiden Fassungen sind weg.
 *
 * Befunde 18.09.2026: Paletten und Groessen kommen aus _tokens (keine eigenen Hexwerte — die
 * Eckentinte ist jetzt DARK.ink mit Deckkraft GREY statt rgba-Hexmischung), useBarrierefrei()
 * schaltet reduzierte Bewegung ab (der Ecken-Puls hielt sich nicht an prefers-reduced-motion)
 * und macht die Zeigerziele per Enter/Leertaste bedienbar, data-stage sitzt am aeusseren Rahmen
 * jeder Variante, Schriftuntergrenze 11 px, eine einzige Radustabelle.
 */
import React from 'react'
import { motion } from 'motion/react'
import { LIGHT, DARK, MONO, SANS, SPRING, RADIUS, TEXT } from './_tokens'
import { useBarrierefrei } from './_barrierefrei'

// @ts-expect-error — Laufzeit-Alias (siehe vite.config.ts)
import { ThinkingOrb } from 'thinking-orbs'

const ARM = 120, TIN = 12, TEND = 2, BLUR = 4, GREY = 0.56
const R = 18 // Formradius der L-Klammer (Owner-Vorgabe der Geometrie) — kein CSS-Radius, siehe RADIUS
const TAKT = 3.6, VERSATZ = 0.18
const WUCHS = 2.28                                    // vorher 2,6 — 20 % weniger Puls
const VERSCHIEB = (ARM / 2) * (WUCHS - 1)            // haelt den Eckpunkt fest
const PULS_MIN = 0.32, PULS_MAX = 0.88                // vorher 0,25 / 0,95 — 20 % weniger Amplitude

const ORDER = { tl: 0, tr: 1, bl: 2, br: 3 }

const LShape = ({ corner }: { corner: 'tl' | 'tr' | 'bl' | 'br' }) => {
  const d = [
    `M ${ARM} 0`, `L ${R} 0`, `A ${R} ${R} 0 0 0 0 ${R}`, `L 0 ${ARM}`,
    `L ${TEND} ${ARM}`, `L ${TIN} ${TIN}`, `L ${ARM} ${TEND}`, 'Z'
  ].join(' ')
  const flip = corner === 'tl' ? 'none' : corner === 'tr' ? 'scaleX(-1)' : corner === 'bl' ? 'scaleY(-1)' : 'scale(-1,-1)'
  const pos: React.CSSProperties =
    corner === 'tl' ? { left: -1, top: -1 } : corner === 'tr' ? { right: -1, top: -1 } : corner === 'bl' ? { left: -1, bottom: -1 } : { right: -1, bottom: -1 }
  const i = ORDER[corner]
  return (
    <motion.svg data-cu-corner={corner} width={ARM} height={ARM} viewBox={`0 0 ${ARM} ${ARM}`}
      initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.5, delay: i * 0.08 }}
      style={{ position: 'absolute', ...pos, transform: flip, filter: `blur(${BLUR}px)`, pointerEvents: 'none', overflow: 'visible' }}>
      <motion.g data-cu-pulse
        animate={{ scale: [1, WUCHS, 1], x: [0, VERSCHIEB, 0], y: [0, VERSCHIEB, 0], opacity: [PULS_MIN, PULS_MAX, PULS_MIN] }}
        transition={{
          scale: { duration: TAKT, ease: 'easeInOut', repeat: Infinity, delay: i * VERSATZ },
          x: { duration: TAKT, ease: 'easeInOut', repeat: Infinity, delay: i * VERSATZ },
          y: { duration: TAKT, ease: 'easeInOut', repeat: Infinity, delay: i * VERSATZ },
          opacity: { duration: TAKT, ease: 'easeInOut', repeat: Infinity, delay: i * VERSATZ }
        }}>
        <path d={d} fill={DARK.ink} fillOpacity={GREY} stroke={DARK.ink} strokeOpacity={GREY} strokeWidth={1.5} strokeLinejoin="round" strokeLinecap="round" />
      </motion.g>
    </motion.svg>
  )
}

const Arrow = ({ size = 22 }: { size?: number }) => (
  <motion.svg width={size} height={size} viewBox="0 0 24 24"
    initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }} transition={{ type: 'spring', stiffness: 260, damping: 24, delay: 0.15 }}>
    <motion.path d="M2 1 L2 19 L6.6 14.6 L9.6 21 L12.4 19.6 L9.3 13.3 L15.4 12.9 Z"
      fill={DARK.ink} fillOpacity={0.94} stroke={DARK.line} strokeOpacity={0.9} strokeWidth={1.1}
      animate={{ x: [0, 2.5, 0], y: [0, 1.5, 0] }} transition={{ duration: 6, ease: 'easeInOut', repeat: Infinity }} />
  </motion.svg>
)

const StopSquare = ({ text }: { text: string }) => (
  <span style={{
    minWidth: 40, height: 26, padding: '0 7px', borderRadius: RADIUS.row,
    background: DARK.ink, color: DARK.bg, display: 'inline-flex', alignItems: 'center', justifyContent: 'center'
  }}>
    <span style={{ fontFamily: MONO, fontSize: 11, whiteSpace: 'nowrap' }}>{text}</span>
  </span>
)

const Pill = ({ text }: { text: string }) => (
  <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}
    transition={{ type: 'spring', stiffness: 280, damping: 28, delay: 0.2 }}
    style={{
      position: 'absolute', left: '50%', bottom: 14, x: '-50%',
      display: 'flex', alignItems: 'center', gap: 10, padding: '0 10px 0 13px', height: 32,
      background: DARK.bg, border: `1.15px solid ${DARK.hair}`, borderRadius: RADIUS.pill
    }}>
    <ThinkingOrb state={'working' as never} size={20} theme="dark" />
    <span style={{ fontSize: 12, color: DARK.ink, whiteSpace: 'nowrap' }}>Skaldir steuert</span>
    <span style={{ fontFamily: MONO, fontSize: 11, color: DARK.sub }}>00:42</span>
    <StopSquare text={text} />
  </motion.div>
)

const Stage = ({ text }: { text: string }) => (
  <div style={{ position: 'relative', height: 250, borderRadius: RADIUS.dialog, overflow: 'hidden', background: DARK.surface, border: `1px solid ${DARK.line}` }}>
    <div style={{ position: 'absolute', left: 18, top: 16, right: 18, height: 24, borderRadius: RADIUS.row, background: DARK.hair }} />
    <div style={{ position: 'absolute', left: 18, top: 50, width: 154, height: 168, borderRadius: RADIUS.row, background: DARK.soft }} />
    <div style={{ position: 'absolute', left: 182, top: 50, right: 18, bottom: 20, borderRadius: RADIUS.row, background: DARK.soft }} />
    <div data-gv-overlay="human-only" data-agent-visible="false" style={{ position: 'absolute', inset: 0, pointerEvents: 'none' }}>
      <LShape corner="tl" /><LShape corner="tr" /><LShape corner="bl" /><LShape corner="br" />
    </div>
    <div style={{ position: 'absolute', left: '54%', top: '42%' }}><Arrow /></div>
    <div data-gv-overlay="human-only" data-agent-visible="false"><Pill text={text} /></div>
  </div>
)

function Case() {
  useBarrierefrei()
  return (
    <div data-stage style={{ padding: 22, background: DARK.bg, fontFamily: SANS, color: DARK.ink, width: 820 }}>
      <div style={{ fontSize: 14.5, fontWeight: 600 }}>Computer-Use · Option 2 · dunkel</div>
      <div style={{ fontSize: 11.5, color: DARK.sub, margin: '3px 0 12px' }}>
        Vier identische L (120 px, 12 → 2 px, Ecke r 18), Blur 4 px · Puls 3,6 s, je Ecke 0,18 s Versatz,
        Amplitude 20 % schwächer (Wachstum 2,6 → 2,28 · Deckkraft 0,25–0,95 → 0,32–0,88) · im Stopp-Viereck steht „esc".
      </div>
      <Stage text="esc" />
      <div style={{ display: 'flex', alignItems: 'center', gap: 14, marginTop: 12 }}>
        <span style={{ fontFamily: MONO, fontSize: 11, color: DARK.sub, letterSpacing: 0.5 }}>STOPP</span>
        <StopSquare text="esc" />
        <span style={{ fontSize: 11, color: DARK.sub }}>Einmal Esc: hält an und fragt nach · zweimal Esc: beendet sofort.</span>
      </div>
    </div>
  )
}

export default {
  '01 · Computer-Use dunkel (Option 2): Stopp-Viereck „esc", Puls 20 % schwächer': () => <Case />
}
