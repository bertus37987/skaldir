/**
 * shadcn/ui-Basisbausteine aus dem App-Spiegel (READ-ONLY importiert) — dieselben
 * Komponenten, die der Chat für Buttons, Fehlerflächen und Dialoge benutzt.
 * Zweck: Zustände isoliert sehen, ohne die App zu starten; die Fehlerfläche zeigt
 * exakt die Form, die im E2E-Audit als F-07 aufgetreten ist.
 *
 * Werkzeug-Fach — Regeln aus UI-BEFUNDE-2026-09-18.md:
 *   C2  `data-stage` auf JEDEM Variantenrahmen (fehlte in dieser Datei).
 *   C3  dunkler Zwilling je Bühne (fehlte): dieselben Bausteine in der DARK-Palette;
 *       die `.dark`-Klasse am Rahmen schaltet zusätzlich die Tokens der App-Spiegel-CSS.
 *   B1  keine Farbe außer Schwarz/Weiß/Grau — Palette kommt ausschließlich aus `_tokens`.
 *   B3  Schriftuntergrenze 11 px (TEXT.meta).
 */
import React from 'react'

import { Button } from '@/components/ui/button'
import { ErrorState } from '@/components/ui/error-state'

import { useBarrierefrei } from './_barrierefrei'
import { LIGHT, DARK, MONO, SANS, TEXT, RADIUS } from './_tokens'

function Buehne({ dark, note, children, style }) {
  useBarrierefrei()
  const P = dark ? DARK : LIGHT
  return (
    <div data-stage
      data-theme={dark ? 'dunkel' : 'hell'}
      className={dark ? 'dark' : undefined}
      style={{ padding: 24, background: P.bg, color: P.ink, fontFamily: SANS, width: '100%', maxWidth: 880, boxSizing: 'border-box', borderRadius: RADIUS.module, ...style }}>
      <div style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, margin: '0 0 14px' }}>{note}</div>
      {children(P)}
    </div>
  )
}

const VARIANTEN = ['default', 'secondary', 'outline', 'ghost', 'destructive', 'link']

const KnopfReihe = () => (
  <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'center' }}>
    {VARIANTEN.map(v => (
      <Button key={v} variant={v as never}>
        {v}
      </Button>
    ))}
  </div>
)

export default {
  'Button — alle Varianten (hell)': () => (
    <Buehne note="Button — alle sechs Varianten des App-Spiegels; Fehlerkennzeichnung nur über Rahmen/Gewicht, nie über Farbe.">
      {() => <KnopfReihe />}
    </Buehne>
  ),

  'Button — Größen + deaktiviert (hell)': () => (
    <Buehne note="Button — Größen sm/default/lg und der deaktivierte Zustand.">
      {() => (
        <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
          <Button size="sm">Größe sm</Button>
          <Button>Größe default</Button>
          <Button size="lg">Größe lg</Button>
          <Button disabled>deaktiviert</Button>
        </div>
      )}
    </Buehne>
  ),

  'ErrorState — die F-07-Fläche aus dem Audit (hell)': () => (
    <Buehne note="Die Absturzfläche aus dem E2E-Audit (F-07): Beschreibung, Ursache, ein Weg zurück.">
      {() => (
        <ErrorState
          description="Cannot read properties of undefined (reading 'count')"
          title="“workspace” failed to render"
        >
          <Button size="sm" variant="outline">
            Retry
          </Button>
        </ErrorState>
      )}
    </Buehne>
  ),

  'Button — alle Varianten (dunkel)': () => (
    <Buehne dark note="Dieselben sechs Varianten in der dunklen Palette (.dark am Rahmen).">
      {() => <KnopfReihe />}
    </Buehne>
  ),

  'Button — Größen + deaktiviert (dunkel)': () => (
    <Buehne dark note="Größen sm/default/lg und der deaktivierte Zustand in der dunklen Palette.">
      {() => (
        <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
          <Button size="sm">Größe sm</Button>
          <Button>Größe default</Button>
          <Button size="lg">Größe lg</Button>
          <Button disabled>deaktiviert</Button>
        </div>
      )}
    </Buehne>
  ),

  'ErrorState — die F-07-Fläche aus dem Audit (dunkel)': () => (
    <Buehne dark note="Dieselbe Absturzfläche in der dunklen Palette — der einzige Fehlerkanal bleibt ✕ + Rahmenstärke.">
      {() => (
        <ErrorState
          description="Cannot read properties of undefined (reading 'count')"
          title="“workspace” failed to render"
        >
          <Button size="sm" variant="outline">
            Retry
          </Button>
        </ErrorState>
      )}
    </Buehne>
  )
}
