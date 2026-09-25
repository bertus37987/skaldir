/**
 * Die Host-Tür des Produkts (`app-overlay/gv/gv-orb.tsx`) — genau die Komponente, die im
 * Chat und im Panel rendert. Ohne den vom Plugin veröffentlichten Orb-Runtime
 * (`globalThis.__GV_THINKING_ORB__`) fällt sie auf einen STATISCHEN Punkt zurück: das ist
 * die im Vertrag geforderte „kein zweiter Indikator"-Garantie und hier direkt prüfbar.
 *
 * Werkzeug-Fach — Regeln aus UI-BEFUNDE-2026-09-18.md:
 *   C2  `data-stage` auf JEDEM Variantenrahmen (fehlte in dieser Datei).
 *   C3  dunkler Zwilling je Bühne: derselbe Inhalt in der DARK-Palette.
 *   B3  Schriftuntergrenze 11 px (TEXT.*), keine Zahl unter 11.
 *   B4  Radien nur aus RADIUS.
 * Zustände: NUR die NEUN gültigen Paket-Zustände. `queued` ist eine App-Projektion und
 * trifft im Paket kein Preset — deshalb steht es hier nicht mehr in der Liste.
 * Größen: NUR 20 (inline) oder 64 (Avatar); jeder andere Wert wirft im Paket.
 */
import React from 'react'

// @ts-expect-error — Laufzeit-Alias auf app-overlay/gv/gv-orb.tsx (siehe vite.config.ts)
import { GvOrb } from 'gv-orb'

import { useBarrierefrei } from './_barrierefrei'
import { LIGHT, DARK, MONO, SANS, TEXT, RADIUS } from './_tokens'

/** Die neun gültigen Paket-Zustände (dist/types.d.ts), in Preset-Reihenfolge. */
const STATES = ['working', 'searching', 'solving', 'listening', 'connecting', 'weaving', 'composing', 'breathing', 'shaping']

/** Endzustände: hier darf NICHTS animieren und kein Orb erscheinen. */
const ENDS = ['VERIFIED_COMPLETE', 'INCOMPLETE', 'FAILED_UNRECOVERABLE', 'ready', 'failed', 'unbekannt']

function Buehne({ titel, note, dark, children }) {
  useBarrierefrei()
  const P = dark ? DARK : LIGHT
  return (
    <div data-stage
      data-theme={dark ? 'dunkel' : 'hell'}
      className={dark ? 'dark' : undefined}
      style={{ padding: 24, background: P.bg, color: P.ink, fontFamily: SANS, width: '100%', maxWidth: 880, boxSizing: 'border-box' }}>
      <div style={{ fontFamily: MONO, fontSize: TEXT.body, fontWeight: 600 }}>{titel}</div>
      <div style={{ fontSize: TEXT.meta, color: P.sub, margin: '4px 0 14px' }}>{note}</div>
      {children(P)}
    </div>
  )
}

/** Eine Zeile: Orb (nur 20 oder 64) + Zustandsname. */
const Punkt = ({ state, size, P }) => (
  <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginBottom: 10 }}>
    <span style={{ width: size, display: 'inline-flex', justifyContent: 'center' }}>
      <GvOrb state={state} size={size} />
    </span>
    <code style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.ink, border: `1px solid ${P.line}`, borderRadius: RADIUS.micro, padding: '2px 6px' }}>{state}</code>
  </div>
)

const BuehneZustande = ({ dark, size }) => (
  <Buehne dark={dark}
    titel={`GvOrb — statischer Punkt je Zustand · ${size} px`}
    note="Erwartung: ruhiger, statischer Punkt je Zustand, keine zweite Animation; neun gültige Zustände, zwei getunte Größen.">
    {(P) => STATES.map(s => <Punkt key={s} state={s} size={size} P={P} />)}
  </Buehne>
)

const BuehneEnden = ({ dark }) => (
  <Buehne dark={dark}
    titel="Endzustände — dürfen NICHTS zeigen"
    note="Endzustand, unbekannter Zustand und Fehler dürfen keinen Orb tragen (kein zweiter Indikator).">
    {(P) => ENDS.map(s => <Punkt key={s} state={s} size={20} P={P} />)}
  </Buehne>
)

export default {
  'Fallback (kein Orb-Runtime) — statischer Punkt · 20 px (hell)': () => <BuehneZustande dark={false} size={20} />,
  'Avatar-Größe 64 px (hell)': () => <BuehneZustande dark={false} size={64} />,
  'Endzustände (dürfen NICHTS zeigen) (hell)': () => <BuehneEnden dark={false} />,
  'Fallback (kein Orb-Runtime) — statischer Punkt · 20 px (dunkel)': () => <BuehneZustande dark size={20} />,
  'Avatar-Größe 64 px (dunkel)': () => <BuehneZustande dark size={64} />,
  'Endzustände (dürfen NICHTS zeigen) (dunkel)': () => <BuehneEnden dark />
}
