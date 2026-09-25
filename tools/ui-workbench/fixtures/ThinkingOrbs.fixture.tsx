/**
 * Alle neun Thinking-Orb-Zustände mit dem ECHTEN Paket (thinking-orbs@0.3.1).
 *
 * API laut Paket-Typen (dist/types.d.ts) — nicht geraten:
 *   state: 'working' | 'searching' | 'solving' | 'listening' | 'connecting'
 *        | 'weaving' | 'composing' | 'breathing' | 'shaping'
 *   size : 20 | 64   ← GENAU zwei getunte Presets, keine freien Größen
 *   theme: 'auto' | 'dark' | 'light'   ← pino die Tintenfarbe des Canvas
 *   speed: Zahl (>1 schneller) · paused: Standbild
 *
 * Die Beschreibungen sind wörtlich aus dem Paket übernommen, damit „Verb → Animation"
 * nachprüfbar bleibt. `queued` gibt es im Paket NICHT — das ist eine App-Projektion.
 *
 * Werkzeug-Fach — Regeln aus UI-BEFUNDE-2026-09-18.md:
 *   C2  `data-stage` auf JEDEM Variantenrahmen (fehlte in dieser Datei).
 *   C3  dunkle Zwillinge: die neun Zustände auch in der DARK-Palette (hell + dunkel).
 *   B3  Schriftuntergrenze 11 px (TEXT.meta) · B4 Radien nur aus RADIUS.
 * Zustände: nur die neun gültigen · Größen: nur 20 oder 64.
 */
import React from 'react'
import { motion } from 'motion/react'

// @ts-expect-error — Laufzeit-Alias auf node_modules/thinking-orbs (siehe vite.config.ts)
import { ThinkingOrb } from 'thinking-orbs'

import { useBarrierefrei } from './_barrierefrei'
import { LIGHT, DARK, MONO, SANS, SPRING, RADIUS, TEXT } from './_tokens'

const STATES: [string, string][] = [
  ['working', 'Partikel auf gekippten Umlaufbahnen — allgemeine Arbeit, Tool-Ausführung'],
  ['searching', 'Scan-Meridian über einem punktierten Globus — Suche, Recherche, Memory'],
  ['solving', 'Bänder verschieben sich in Vierteldrehungen — Analyse, Reasoning, Verifikation'],
  ['listening', 'Wellenform durch Breitengrad-Ringe — Spracheingabe'],
  ['connecting', 'Konstellation verdrahtet sich, Pakete laufen die Kanten — API/MCP/Provider'],
  ['weaving', 'Drei Stränge flechten sich um die Kugel — Subagenten aggregieren'],
  ['composing', 'Wogende Mehrband-Schärpe — Antwort wird geschrieben'],
  ['breathing', 'Frontaler Ring morpht langsam — Warten, Recovery, blockiert'],
  ['shaping', 'Punktierte Kontur: Kreis → Dreieck → Quadrat — Artefakt/UI/Design']
]

const Chip = ({ P, children }) => (
  <span style={{
    fontFamily: MONO, fontSize: TEXT.meta, color: P.ink,
    border: `1px solid ${P.line}`, borderRadius: RADIUS.micro, padding: '2px 6px'
  }}>{children}</span>
)

function Buehne({ titel, note, dark, width, children }) {
  useBarrierefrei()
  const P = dark ? DARK : LIGHT
  return (
    <div data-stage
      data-theme={dark ? 'dunkel' : 'hell'}
      className={dark ? 'dark' : undefined}
      style={{ padding: 24, background: P.bg, color: P.ink, fontFamily: SANS, width, maxWidth: '100%', boxSizing: 'border-box' }}>
      <motion.div initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} transition={SPRING}>
        <h2 style={{ fontFamily: SANS, fontSize: TEXT.title, fontWeight: 600, margin: '0 0 4px' }}>{titel}</h2>
        <p style={{ fontFamily: SANS, fontSize: TEXT.body, color: P.sub, margin: '0 0 18px' }}>{note}</p>
      </motion.div>
      {children(P)}
    </div>
  )
}

const NeunInline = ({ dark }) => (
  <Buehne dark={dark} width={860}
    titel="Thinking-Orbs — Inline-Größe 20 px"
    note="Genau EIN Indikator je Aktivität; monochrom, reduced-motion-fest. Die neun gültigen Paket-Zustände.">
    {(P) => (
      <>
        {STATES.map(([state, desc]) => (
          <div key={state} style={{ display: 'flex', alignItems: 'center', gap: 14, marginBottom: 14 }}>
            <span style={{ width: 26, display: 'inline-flex', justifyContent: 'center' }}>
              <ThinkingOrb state={state as never} size={20} theme={dark ? 'dark' : 'light'} />
            </span>
            <span style={{ width: 110 }}><Chip P={P}>{state}</Chip></span>
            <span style={{ fontFamily: SANS, fontSize: TEXT.meta, color: P.sub }}>{desc}</span>
          </div>
        ))}
        <hr style={{ border: 0, borderTop: `1px solid ${P.line}`, margin: '18px 0' }} />
        <div style={{ fontFamily: SANS, fontSize: TEXT.meta, color: P.ink }}>
          Endzustände OHNE Orb: ✓ abgeschlossen · ✕ fehlgeschlagen · ◆ abgebrochen (statische Symbole)
        </div>
      </>
    )}
  </Buehne>
)

const NeunAvatar = ({ dark }) => (
  <Buehne dark={dark} width={900}
    titel="Thinking-Orbs — Avatar-Größe 64 px"
    note="Zweite getunte Größe: eigenes Dot-Raster, eigenes Tempo (kein Skalierungsfaktor).">
    {(P) => (
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 22 }}>
        {STATES.map(([state]) => (
          <div key={state} style={{ textAlign: 'center', width: 118 }}>
            <ThinkingOrb state={state as never} size={64} theme={dark ? 'dark' : 'light'} />
            <div style={{ marginTop: 6 }}><Chip P={P}>{state}</Chip></div>
          </div>
        ))}
      </div>
    )}
  </Buehne>
)

const Tempo = () => (
  <Buehne width={820}
    titel="Tempo und Freeze"
    note="speed multipliziert auf das Preset · paused friert das Bild ein (z. B. für Reduced Motion oder einen ruhenden Endzustand).">
    {(P) => (
      <div style={{ display: 'flex', gap: 30, alignItems: 'center', flexWrap: 'wrap' }}>
        {[0.5, 1, 2].map(s => (
          <div key={s} style={{ textAlign: 'center' }}>
            <ThinkingOrb state={'weaving' as never} size={64} theme="light" speed={s} />
            <div style={{ marginTop: 6 }}><Chip P={P}>speed {s}</Chip></div>
          </div>
        ))}
        <div style={{ textAlign: 'center' }}>
          <ThinkingOrb state={'composing' as never} size={64} theme="light" paused />
          <div style={{ marginTop: 6 }}><Chip P={P}>paused (Standbild)</Chip></div>
        </div>
      </div>
    )}
  </Buehne>
)

export default {
  'neun Zustände · Inline 20 px (hell)': () => <NeunInline dark={false} />,
  'neun Zustände · Avatar 64 px (hell)': () => <NeunAvatar dark={false} />,
  'Dark-Ink (theme="dark")': () => <NeunAvatar dark />,
  'Tempo & Standbild': () => <Tempo />,
  'neun Zustände · Inline 20 px (dunkel)': () => <NeunInline dark />,
  'neun Zustände · Avatar 64 px (dunkel)': () => <NeunAvatar dark />
}
