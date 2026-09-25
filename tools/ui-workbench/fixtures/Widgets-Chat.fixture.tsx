/**
 * Die Chat-Zeilen der Owner-Matrix (UI-CONTRACT §1) als lebende Widgets.
 * Der Orb ist der ECHTE aus `thinking-orbs@0.3.1`; die Zeilen selbst sind Mockups,
 * weil die Originale den assistant-ui-Runtime-Kontext der App brauchen.
 * Prüfzweck: Zeigt jede Zeile den vorgeschriebenen Indikator — und nach einem Fehler KEINEN Orb?
 *
 * Werkzeug-Fach — Regeln aus UI-BEFUNDE-2026-09-18.md:
 *   B1  Die beiden Farbtöne (Rot/Orange) sind ENTFERNT. Fehler werden nur über ✕ und einen
 *       kräftigeren Rahmen gezeigt (Rahmenstärke + Glyphe), nie über Farbe. Kein Hexwert
 *       außerhalb der Tokens mehr in dieser Datei.
 *   C2  `data-stage` auf JEDEM Variantenrahmen (fehlte in dieser Datei).
 *   C3  dunkle Zwillinge: dieselbe Bühne in der DARK-Palette (Orb mit theme="dark").
 *   B3  Schriftuntergrenze 11 px (TEXT.meta) · B4 Radien nur aus RADIUS.
 * Zustände: nur die neun gültigen Paket-Zustände · Größen: nur 20 oder 64.
 */
import React from 'react'

// @ts-expect-error — Laufzeit-Alias auf node_modules/thinking-orbs (siehe vite.config.ts)
import * as orbs from 'thinking-orbs'

import { useBarrierefrei } from './_barrierefrei'
import { LIGHT, DARK, MONO, SANS, TEXT, RADIUS } from './_tokens'

type OrbState = 'working' | 'searching' | 'solving' | 'connecting' | 'weaving' | 'composing' | 'breathing' | 'shaping'

function Orb({ state, size = 20, dark }: { state: OrbState | null; size?: 20 | 64; dark?: boolean }) {
  if (!state) return <span style={{ width: size, display: 'inline-block' }} />
  const Cmp = (orbs as Record<string, unknown>).ThinkingOrb as React.ComponentType<{ state: string; size?: number; theme?: string }> | undefined
  if (!Cmp) return <span style={{ fontSize: TEXT.meta, fontWeight: 700 }}>✕ kein Orb-Export</span>
  // WICHTIG: thinking-orbs liefert NUR die getunten Presets 20 und 64. Jeder andere Wert
  // trifft kein Preset und wirft „can't access property 'count', o is undefined".
  const safe = size === 64 ? 64 : 20
  return <Cmp state={state} size={safe} theme={dark ? 'dark' : 'light'} />
}

function Row({ P, dark, orb, title, ui, sample, tone }: { P: typeof LIGHT; dark?: boolean; orb: OrbState | null; title: string; ui: string; sample: string; tone?: 'error' | 'done' }) {
  const fehler = tone === 'error'
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 12, padding: '9px 0 9px 10px',
      // Fehlerzeile: kräftigerer Rahmen links + unten — das ersetzt die frühere rote Schrift.
      borderBottom: `${fehler ? 1.5 : 1}px solid ${fehler ? P.ink : P.line}`,
      borderLeft: fehler ? `3px solid ${P.ink}` : '3px solid transparent'
    }}>
      <span style={{ width: 22, display: 'inline-flex', justifyContent: 'center' }}>
        {tone === 'done'
          ? <span style={{ fontSize: 15 }}>✓</span>
          : fehler
            ? <span style={{ fontSize: 15, fontWeight: 700 }}>✕</span>
            : <Orb state={orb} dark={dark} />}
      </span>
      <span style={{ fontSize: TEXT.body, fontWeight: 600, width: 210, color: P.ink }}>{title}</span>
      <span style={{ fontSize: TEXT.meta, color: P.sub, width: 190 }}>{ui}</span>
      <span style={{ fontSize: TEXT.meta, color: P.ink }}>{sample}</span>
    </div>
  )
}

function Buehne({ titel, note, dark, width, children }) {
  useBarrierefrei()
  const P = dark ? DARK : LIGHT
  return (
    <div data-stage
      data-theme={dark ? 'dunkel' : 'hell'}
      className={dark ? 'dark' : undefined}
      style={{ padding: 24, background: P.bg, color: P.ink, fontFamily: SANS, width, maxWidth: '100%', boxSizing: 'border-box' }}>
      <h2 style={{ fontFamily: SANS, fontSize: TEXT.title, fontWeight: 600, margin: '0 0 4px' }}>{titel}</h2>
      <p style={{ fontFamily: SANS, fontSize: TEXT.body, color: P.sub, margin: '0 0 16px' }}>{note}</p>
      {children(P)}
    </div>
  )
}

const Matrix = ({ dark }) => (
  <Buehne dark={dark} width={940}
    titel="Chat-Zeilen — verbindliche Zuordnung"
    note="Orb = echter thinking-orbs. Nach einem Fehler und im Endzustand darf KEIN Orb erscheinen.">
    {(P) => (
      <>
        <Row P={P} dark={dark} orb="working" title="Thinking / Working" ui="Orb + Label" sample="Denke nach …" />
        <Row P={P} dark={dark} orb="working" title="Tool Call" ui="Orb + Tool-Karte" sample="terminal · date" />
        <Row P={P} dark={dark} orb={null} title="Tool Result" ui="Tool-Karte" sample="exit 0 · Fr 17. Sep 17:49:11 CEST 2026" />
        <Row P={P} dark={dark} orb="searching" title="Research" ui="Orb + Quellen" sample="Suche in 3 Quellen …" />
        <Row P={P} dark={dark} orb={null} title="Sources" ui="Quellenliste" sample="1 · docs/UI.md  2 · audit/RESUME.md" />
        <Row P={P} dark={dark} orb={null} title="Approval / Confirmation" ui="Bestätigungskarte" sample="Ausführen erlauben? [Erlauben] [Ablehnen]" />
        <Row P={P} dark={dark} orb="working" title="Subagent Working" ui="Orb (working)" sample="Subagent 1/3 arbeitet" />
        <Row P={P} dark={dark} orb="weaving" title="Subagent Aggregation" ui="Orb (weaving)" sample="Ergebnisse zusammenführen" />
        <Row P={P} dark={dark} orb="connecting" title="Connecting API / MCP" ui="Orb (connecting)" sample="Verbinde MCP-Server …" />
        <Row P={P} dark={dark} orb="breathing" title="Waiting / Recovery" ui="Orb (breathing)" sample="Warte auf Backend …" />
        <Row P={P} dark={dark} orb="shaping" title="Artifact Generation" ui="Orb (shaping)" sample="Baue Bericht …" />
        <Row P={P} dark={dark} orb="composing" title="Response Generation" ui="Orb (composing)" sample="Schreibe Antwort …" />
        <Row P={P} dark={dark} orb="solving" title="Verification" ui="Orb + kompakte Zeilen" sample="3/7 Kriterien verifiziert" />
        <Row P={P} dark={dark} orb={null} title="Attachments" ui="Anhang-Chips" sample="resume.pdf · screenshot.png" />
        <Row P={P} dark={dark} orb={null} tone="error" title="Errors" ui="Fehlerfläche, KEIN Orb · ✕ + strenger Rahmen" sample="Tool fehlgeschlagen — Text + Retry" />
        <Row P={P} dark={dark} orb={null} tone="done" title="Finished State" ui="statisches Symbol" sample="abgeschlossen" />
      </>
    )}
  </Buehne>
)

const FehlerWarten = ({ dark }) => (
  <Buehne dark={dark} width={780}
    titel="Fehler · Warten · Recovery"
    note="Fehler ohne Farbe: ✕ als Kanalkennzeichen und ein kräftigerer Rahmen tragen die Bedeutung.">
    {(P) => (
      <>
        <div style={{ border: `1.5px solid ${P.ink}`, borderRadius: RADIUS.module, padding: 14, marginBottom: 16 }}>
          <div style={{ display: 'flex', gap: 10, alignItems: 'center', fontSize: TEXT.body, fontWeight: 700, color: P.ink }}>
            <span style={{ fontSize: 15 }}>✕</span><b>Tool fehlgeschlagen</b>
          </div>
          <div style={{ fontSize: TEXT.meta, color: P.sub, margin: '6px 0 10px' }}>
            gemountete Ursache + Vorschlag, kein Orb — Fehlerflächen sind statisch
          </div>
          <button style={{ fontFamily: SANS, fontSize: TEXT.body, padding: '6px 12px', borderRadius: RADIUS.row, border: `1.5px solid ${P.ink}`, background: P.surface, color: P.ink, cursor: 'pointer' }}>
            Erneut versuchen
          </button>
        </div>

        <div style={{ border: `1px solid ${P.line}`, borderRadius: RADIUS.module, padding: 14, marginBottom: 16, display: 'flex', gap: 12, alignItems: 'center' }}>
          <Orb state="breathing" dark={dark} />
          <div>
            <div style={{ fontSize: TEXT.body, fontWeight: 600 }}>Warte auf Backend</div>
            <div style={{ fontSize: TEXT.meta, color: P.sub }}>Reconnect läuft · bounded, mit sichtbarem Ausweg</div>
          </div>
        </div>

        <div style={{ border: `1px solid ${P.line}`, borderRadius: RADIUS.module, padding: 14, display: 'flex', gap: 12, alignItems: 'center' }}>
          <span style={{ fontSize: 15 }}>✓</span>
          <div>
            <div style={{ fontSize: TEXT.body, fontWeight: 600 }}>Abgeschlossen</div>
            <div style={{ fontSize: TEXT.meta, color: P.sub }}>statisches Symbol, keine Animation</div>
          </div>
        </div>
      </>
    )}
  </Buehne>
)

const Absturz = ({ dark }) => (
  <Buehne dark={dark} width={780}
    titel="Isolierte Nachstellung des Audit-Befunds F-07"
    note="Tritt im realen Produkt auf, sobald ein Tool-Ergebnis gerendert wird. Hier ohne App sichtbar.">
    {(P) => (
      <div style={{ background: P.surface, border: `1.5px solid ${P.ink}`, borderRadius: RADIUS.dialog, padding: 32, textAlign: 'center' }}>
        <div style={{ fontSize: 22, fontWeight: 700, color: P.ink }}>✕</div>
        <div style={{ fontSize: TEXT.title, fontWeight: 600, margin: '10px 0 6px' }}>“workspace” failed to render</div>
        <div style={{ fontSize: TEXT.body, color: P.sub, fontFamily: MONO }}>
          Cannot read properties of undefined (reading 'count')
        </div>
        <button style={{ marginTop: 16, fontFamily: SANS, fontSize: TEXT.body, padding: '6px 14px', borderRadius: RADIUS.row, border: `1.5px solid ${P.ink}`, background: P.surface, color: P.ink, cursor: 'pointer' }}>
          Retry
        </button>
        <div style={{ fontSize: TEXT.meta, fontWeight: 600, color: P.ink, marginTop: 14 }}>
          ✕ Sackgasse: Chat und Composer fehlen, kein Weg in eine andere Session
        </div>
      </div>
    )}
  </Buehne>
)

export default {
  'Owner-Matrix — alle 16 Zeilen (hell)': () => <Matrix dark={false} />,
  'Fehler- und Wartezustände im Detail (hell)': () => <FehlerWarten dark={false} />,
  'F-07 — Absturzfläche des Chat-Panes (hell)': () => <Absturz dark={false} />,
  'Owner-Matrix — alle 16 Zeilen (dunkel)': () => <Matrix dark />,
  'Fehler- und Wartezustände im Detail (dunkel)': () => <FehlerWarten dark />,
  'F-07 — Absturzfläche des Chat-Panes (dunkel)': () => <Absturz dark />
}
