/**
 * App-taugliche Panels mit LEEREN Zustaenden — abgeleitet aus den abgenommenen Quellen
 * (keine Fixture-Huelle, keine Schein-Daten).
 *
 * Quellen (nur gelesen):
 *   src/skaldir/context-widget.tsx  — Terminal-Kapsel (runde Pille, [██████░░░░], Prozent, belegt/gesamt · reserve)
 *   src/skaldir/subagent-panel.tsx  — Subagenten-Panel (Kopfzeile „SUBAGENTEN · N OFFEN", Zeilen, „−" nimmt heraus)
 *   src/skaldir/subagent-chat.tsx   — Subagenten-Fenster (schmale Spur 234 px, Kopf/Verlauf/Beenden-Viereck/CLI-Anzeige)
 *   Owner-Matrix (UI-CONTRACT §1)   — Zustandszeile: Orb 20 ODER statisches ✓/✕, sonst kein Orb
 *   Startschirm/SkaldirApp          — leerer Chat: Punktraster, Wortmarke SKALDIR, wechselnder Spruch, Hinweiszeile
 *
 * Regeln dieser Datei:
 *   · Optik und Masse unveraendert aus den Quellen; Schrift nie unter 11 px (Befund B3);
 *     Radien nur 4/8/12/16/24/999.
 *   · Farben ausschliesslich ueber die CSS-Variablen aus src/styles.css — damit gilt derselbe
 *     Baustein hell (#FAFAFA/#FFFFFF/#171717/#595959/#6E6E6E/#8F8F8F/#DEDEDE/#F2F2F0) und
 *     dunkel (#111111/#191919/#F5F5F5/#B7B7B7/#8A8A8A/#6A6A6A/#3A3A3A/#202020).
 *   · Symbole nur aus @tabler/icons-react (Strichstaerke heisst dort `stroke`).
 *   · Orb nur size 20 (oder 64) und nur die neun gueltigen Zustaende.
 *   · Bedienelemente sind echte `button` mit `aria-label`.
 *   · Fehler traegt ✕ und einen kraeftigeren Rahmen — NIE Farbe.
 *   · KEINE Schein-Daten: Vorgaben sind leer bzw. 0; Zieh-/Positionier-Logik verdrahtet der Aufrufer.
 */

import React, { useEffect, useState } from 'react'
import { motion, AnimatePresence } from 'motion/react'
import { IconCheck, IconMinus, IconX } from '@tabler/icons-react'

import { ThinkingOrb } from 'thinking-orbs'

const MONO = "'Geist Mono', ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"
const SANS = "'Geist Sans', system-ui, sans-serif"

/** Die einzige Farbquelle: die Tokens aus src/styles.css (hell + dunkel). */
const T = {
  bg: 'var(--bg)',
  surface: 'var(--surface)',
  ink: 'var(--ink)',
  sub: 'var(--sub)',
  faint: 'var(--faint)',
  line: 'var(--line)',
  hair: 'var(--hair)',
  soft: 'var(--soft)'
} as const

/* ---------------------------------------------------------------- Orb (size 20, neun Zustaende) */

export type OrbZustand =
  | 'working' | 'searching' | 'solving' | 'listening' | 'connecting'
  | 'weaving' | 'composing' | 'breathing' | 'shaping'

const ORB_ZUSTAENDE: readonly OrbZustand[] = [
  'working', 'searching', 'solving', 'listening', 'connecting',
  'weaving', 'composing', 'breathing', 'shaping'
]

/** Nie „working" raten: ein unbekannter/fehlender Wert traegt keinen Orb. */
const istOrbZustand = (s?: string): s is OrbZustand => !!s && (ORB_ZUSTAENDE as readonly string[]).includes(s)

/** Orb-Thema folgt der .dark-Klasse des Dokuments (styles.css), Vorgabe hell. */
const orbThema = (): 'light' | 'dark' =>
  typeof document !== 'undefined' && document.documentElement.classList.contains('dark') ? 'dark' : 'light'

const Orb20 = ({ zustand }: { zustand: OrbZustand }) => (
  <ThinkingOrb state={zustand as never} size={20} theme={orbThema()} />
)

/** Kurzform der Zaehlwerte wie in der Quelle: 128000 → „128k", 0 → „0". */
const k = (n: number) => (n >= 1000 ? `${Math.round(n / 1000)}k` : `${Math.round(n)}`)

/* ------------------------------------------------------------------------------------ 1 · Kontext */

export type KontextKapselProps = {
  /** Belegte Tokens des Kontextfensters. */
  belegt?: number
  /** Gesamtgroesse des Kontextfensters. */
  gesamt?: number
  /** Fuer die naechste Antwort reservierte Tokens. */
  reserve?: number
  /** ✕ in der Kapsel — der Aufrufer entscheidet, was danach passiert (z. B. Griff zurückholen). */
  onSchliessen?: () => void
}

/**
 * Kontext-Kapsel — dieselbe Optik wie das abgenommene Widget (runde Pille, `[██████░░░░]`,
 * Prozent, Zeile „belegt/gesamt · reserve …"), aber mit Props statt fester Zahlen.
 * Ohne Props neutral: belegt 0, gesamt 200000, reserve 0 → „0 %" und leerer Fortschrittsbalken.
 */
export function KontextKapsel({ belegt = 0, gesamt = 200000, reserve = 0, onSchliessen }: KontextKapselProps) {
  const prozent = gesamt > 0 ? Math.round((belegt / gesamt) * 100) : 0
  const gefuellt = Math.max(0, Math.min(10, Math.round(prozent / 10)))
  return (
    <div
      data-kontextkapsel
      data-prozent={prozent}
      style={{
        width: 208, boxSizing: 'border-box', borderRadius: 999, border: `1px solid ${T.line}`,
        background: T.surface, boxShadow: '0 10px 28px rgba(0,0,0,0.18)', padding: '9px 13px',
        display: 'flex', flexDirection: 'column', gap: 5, fontFamily: SANS
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span aria-hidden="true" style={{ fontFamily: MONO, fontSize: 13, color: T.ink }}>
          [{'█'.repeat(gefuellt)}{'░'.repeat(10 - gefuellt)}]
        </span>
        <span style={{ marginLeft: 'auto', fontFamily: MONO, fontSize: 12, color: T.ink }}>{prozent} %</span>
        <button
          type="button"
          aria-label="Kontext schliessen"
          title="Kontext schliessen"
          onClick={() => onSchliessen?.()}
          style={{
            display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 18, height: 18,
            borderRadius: 999, border: `1px solid ${T.line}`, background: 'transparent', cursor: 'pointer', padding: 0
          }}
        >
          <IconX size={11} stroke={2} color={T.ink} />
        </button>
      </div>
      <div style={{ display: 'flex', gap: 7, fontFamily: MONO, fontSize: 11, color: T.sub }}>
        <span>{k(belegt)}/{k(gesamt)}</span><span>·</span><span>reserve {k(reserve)}</span>
      </div>
    </div>
  )
}

/* ---------------------------------------------------------------------------------- 2 · Subagenten */

export type Subagent = {
  id: string
  name: string
  task: string
  /** Fortschritt in Schritten, z. B. „4/7". */
  schritte: string
  zustand: OrbZustand
}

/**
 * Subagenten-Panel — dieselbe Optik wie die abgenommene Fassung (Kopfzeile „SUBAGENTEN · N OFFEN",
 * Zeilen mit Orb/Titel/Aufgabe/Schritten, „−" nimmt genau diesen Subagenten heraus), aber mit
 * LEERER Vorgabe: keine Subagenten, Kopfzeile „SUBAGENTEN · 0 OFFEN", Leerzeile „kein Subagent offen".
 * Kein „herausgenommen"-Abschnitt (die Abnahme verlangt nur die offenen Zeilen).
 * Ohne Eintraege entsteht keine einzige Scheinzeile — die Liste fuellt der Aufruferzustand.
 */
export function SubagentenPanel() {
  const [offen, setOffen] = useState<Subagent[]>([])
  const [aktiv, setAktiv] = useState<string | undefined>(undefined)

  const herausnehmen = (id: string) => {
    const rest = offen.filter(x => x.id !== id)
    setOffen(rest)
    if (aktiv === id) setAktiv(rest[0]?.id)
  }

  return (
    <div
      data-subagentenpanel
      data-offen={offen.length}
      style={{
        width: 236, boxSizing: 'border-box', background: T.surface, border: `1px solid ${T.line}`,
        boxShadow: '0 8px 26px rgba(0,0,0,0.14)', fontFamily: SANS
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '7px 9px', borderBottom: `1px solid ${T.line}` }}>
        <span style={{ fontFamily: MONO, fontSize: 11, letterSpacing: 1, color: T.sub }}>
          SUBAGENTEN · {offen.length} OFFEN
        </span>
      </div>
      <div style={{ padding: 4 }}>
        {offen.map(s => {
          const gewaehlt = aktiv === s.id
          return (
            <div
              key={s.id}
              data-panelrow={s.id}
              data-selected={gewaehlt ? 'true' : 'false'}
              style={{
                display: 'flex', alignItems: 'center', gap: 7, padding: '6px 7px',
                background: gewaehlt ? T.bg : 'transparent', border: `1px solid ${gewaehlt ? T.line : 'transparent'}`
              }}
            >
              <Orb20 zustand={s.zustand} />
              <span style={{ flex: 1, minWidth: 0 }}>
                <span style={{ display: 'block', fontFamily: MONO, fontSize: 11, fontWeight: gewaehlt ? 600 : 400, color: T.ink }}>{s.name}</span>
                <span style={{ display: 'block', fontFamily: MONO, fontSize: 11, color: T.sub, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.task}</span>
              </span>
              <span style={{ fontFamily: MONO, fontSize: 11, color: T.sub }}>{s.schritte}</span>
              <button
                type="button"
                aria-label={`${s.name} herausnehmen`}
                title={`${s.name} herausnehmen`}
                onClick={() => herausnehmen(s.id)}
                style={{
                  display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 20, height: 18,
                  border: `1px solid ${T.line}`, background: 'transparent', cursor: 'pointer', padding: 0
                }}
              >
                <IconMinus size={11} stroke={2} color={T.ink} />
              </button>
            </div>
          )
        })}
        {offen.length === 0 && (
          <div style={{ padding: 9, fontFamily: MONO, fontSize: 11, color: T.faint }}>kein Subagent offen</div>
        )}
      </div>
    </div>
  )
}

/* ----------------------------------------------------------------------------- 3 · Subagenten-Fenster */

export type SubagentenFensterProps = {
  /** Name der Spur, z. B. „prüfer". Leer = keine Titelzeile (nichts erfunden). */
  titel?: string
  /** Laufzeit wie im Kopf der Quelle, z. B. „00:42" (wird als „t 00:42" gezeigt). */
  dauer?: string
  /** ✕ im Kopf schliesst das Fenster. */
  onSchliessen?: () => void
}

/** Viereckige CLI-Kontextanzeige; ohne Wert neutral (kein Segment gefuellt, „0 %"). */
const CliKontext = ({ prozent = 0, gedaempft = false }: { prozent?: number; gedaempft?: boolean }) => {
  const gefuellt = Math.max(0, Math.min(10, Math.round(prozent / 10)))
  return (
    <span style={{
      border: `1px solid ${T.ink}`, padding: '2px 5px', display: 'inline-flex', alignItems: 'center', gap: 6,
      fontFamily: MONO, fontSize: 11, color: T.ink, opacity: gedaempft ? 0.45 : 1
    }}>
      {Array.from({ length: 10 }).map((_, i) => (
        <span key={i} aria-hidden="true" style={{
          width: 5, height: 9, background: i < gefuellt ? T.ink : 'transparent', border: `1px solid ${T.ink}`, boxSizing: 'border-box'
        }} />
      ))}
      <span>{prozent} %</span>
    </span>
  )
}

/**
 * Subagenten-Fenster — die schmale Spur (234 px) wie in der abgenommenen Fassung: Kopf mit Orb 20,
 * Titel, Dauer, „−" klappt ein, „✕" schliesst; Verlaufsbereich; unten Beenden-Viereck und viereckige
 * CLI-Kontextanzeige nebeneinander.
 * Ohne Props ist der Verlaufsbereich LEER und traegt den Hinweis „noch keine Ausgabe" — keine Beispielzeilen.
 * Position/Frei-Verschieben verdrahtet der Aufrufer (die Quelle zieht am Kopf).
 */
export function SubagentenFenster({ titel, dauer, onSchliessen }: SubagentenFensterProps) {
  const [eingeklappt, setEingeklappt] = useState(false)
  const [beendet, setBeendet] = useState(false)

  return (
    <div
      data-subwin
      data-eingeklappt={eingeklappt ? 'true' : 'false'}
      data-beendet={beendet ? 'true' : 'false'}
      style={{
        width: 234, boxSizing: 'border-box', background: T.surface, border: `1px solid ${T.line}`,
        boxShadow: '0 14px 38px rgba(0,0,0,0.26)', fontFamily: MONO
      }}
    >
      <div style={{
        display: 'flex', alignItems: 'center', gap: 7, padding: '5px 7px', background: T.bg,
        borderBottom: eingeklappt ? 'none' : `1px solid ${T.line}`
      }}>
        <ThinkingOrb state={(beendet ? 'breathing' : 'working') as never} size={20} theme={orbThema()} />
        {titel ? <span style={{ fontSize: 11, color: T.sub }}>◆ {titel}</span> : null}
        {dauer ? <span style={{ fontSize: 11, color: T.ink }}>t {dauer}</span> : null}
        {eingeklappt && <span style={{ fontSize: 11, color: T.faint }}>▮▮▮▮▮▮▯▯▯▯ 0 %</span>}
        <span style={{ marginLeft: eingeklappt ? 6 : 'auto', display: 'inline-flex', gap: 4 }}>
          <button
            type="button"
            aria-label={eingeklappt ? 'ausklappen' : 'einklappen'}
            title={eingeklappt ? 'ausklappen' : 'einklappen'}
            onClick={() => setEingeklappt(c => !c)}
            style={{
              display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 20, height: 18,
              border: `1px solid ${T.line}`, background: 'transparent', cursor: 'pointer', padding: 0,
              fontSize: 11, color: T.ink
            }}
          >−</button>
          <button
            type="button"
            aria-label="Fenster schliessen"
            title="Fenster schliessen"
            onClick={() => onSchliessen?.()}
            style={{
              display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 20, height: 18,
              border: `1px solid ${T.line}`, background: 'transparent', cursor: 'pointer', padding: 0
            }}
          >
            <IconX size={11} stroke={2} color={T.ink} />
          </button>
        </span>
      </div>

      {!eingeklappt && (
        <>
          <div style={{ padding: 7, height: 104, overflow: 'hidden' }}>
            <div style={{ fontSize: 11, color: T.faint }}>noch keine Ausgabe</div>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 7, padding: '6px 7px', borderTop: `1px solid ${T.line}` }}>
            <button
              type="button"
              aria-label={beendet ? 'Weiter' : 'Beenden'}
              title={beendet ? 'Weiter' : 'Beenden'}
              onClick={() => setBeendet(v => !v)}
              style={{
                display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 24, height: 20,
                background: beendet ? 'transparent' : T.ink, border: `1px solid ${T.ink}`,
                color: beendet ? T.ink : T.surface, fontFamily: MONO, fontSize: 11, cursor: 'pointer', padding: 0
              }}
            >{beendet ? '▶' : '■'}</button>
            <CliKontext prozent={0} gedaempft={beendet} />
          </div>
        </>
      )}
    </div>
  )
}

/* ----------------------------------------------------------------------------------- 4 · Zustandszeile */

export type ZeilenZustand = 'laeuft' | 'fertig' | 'fehler'

export type ZustandsZeileProps = {
  /** Orb-Zustand der laufenden Zeile (nur die neun gueltigen Werte). */
  orb?: OrbZustand
  /** Titel der Zeile, z. B. „Thinking / Working". */
  titel?: string
  /** Detailtext der Zeile (Text UND Symbol — Produktregel). */
  detail?: string
  /** laeuft = Orb (bzw. „·" ohne Orb) · fertig = ✓ ohne Orb · fehler = ✕ mit kraeftigerem Rahmen. */
  zustand?: ZeilenZustand
}

/**
 * ZustandsZeile — EINE Verlaufszeile nach der Owner-Matrix (UI-CONTRACT §1): Orb 20 px ODER statisches
 * ✓/✕, daneben Titel und Detail. `fertig` traegt ✓ und keinen Orb, `fehler` traegt ✕ und einen
 * kraeftigeren Rahmen (1,5 px + 3 px Kante) — Farbe kommt nie ins Spiel.
 */
export function ZustandsZeile({ orb, titel = '', detail = '', zustand = 'laeuft' }: ZustandsZeileProps) {
  const fehler = zustand === 'fehler'
  return (
    <div
      data-zustandszeile={zustand}
      style={{
        display: 'flex', alignItems: 'center', gap: 10, padding: '6px 10px',
        background: T.bg, color: T.ink, fontFamily: SANS,
        borderBottom: `${fehler ? 1.5 : 1}px solid ${fehler ? T.ink : T.line}`,
        borderLeft: fehler ? `3px solid ${T.ink}` : '3px solid transparent'
      }}
    >
      <span aria-hidden="true" style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 22, minWidth: 22 }}>
        {zustand === 'laeuft'
          ? (istOrbZustand(orb)
            ? <Orb20 zustand={orb} />
            : <span style={{ fontFamily: MONO, fontSize: 11, color: T.faint }}>·</span>)
          : zustand === 'fertig'
            ? <IconCheck size={15} stroke={2.4} color={T.ink} />
            : <IconX size={15} stroke={2.4} color={T.ink} />}
      </span>
      {titel ? <span style={{ fontSize: 13, fontWeight: 600 }}>{titel}</span> : null}
      {detail ? <span style={{ flex: 1, fontSize: 11, color: T.sub }}>{detail}</span> : null}
    </div>
  )
}

/* -------------------------------------------------------------------------------------- 5 · Leerer Chat */

/** Die Spruchsammlung des Startschirms — mindestens vier, echter Wechsel. */
export const SPRUECHE = [
  'Give me a goal I actually complete it.',
  'Ich messe, ich behaupte nicht.',
  'Ein Ziel, ein Nachweis.',
  'Zeig mir das Ziel, ich zeige dir den Beleg.',
  'Kein Fortschritt ohne Messung.'
]

export type LeererChatProps = {
  /** Sprueche in Reihenfolge; Vorgabe: die Sammlung des Startschirms. */
  sprueche?: string[]
  /** Wechselabstand in Millisekunden. */
  intervall?: number
}

/**
 * Leerer Chat — Punktraster (9×9), Wortmarke „SKALDIR", darunter der wechselnde Spruch und die
 * Hinweiszeile „Ziel eintippen, Datei anhaengen oder ⌘K fuer Befehle · kein Orb im leeren Chat".
 * KEINE Schein-Nachrichten und kein Orb: der leere Chat traegt keinen Laufzustand.
 */
export function LeererChat({ sprueche = SPRUECHE, intervall = 4200 }: LeererChatProps) {
  const [i, setI] = useState(0)
  useEffect(() => {
    if (sprueche.length < 2) return
    const id = setInterval(() => setI(v => (v + 1) % sprueche.length), intervall)
    return () => clearInterval(id)
  }, [sprueche.length, intervall])

  const spruch = sprueche[Math.min(i, Math.max(0, sprueche.length - 1))] ?? ''

  return (
    <div
      data-leererchat
      style={{
        height: '100%', minHeight: 240, display: 'flex', flexDirection: 'column',
        alignItems: 'center', justifyContent: 'center', gap: 16, padding: 20,
        boxSizing: 'border-box', background: T.bg, color: T.ink, fontFamily: SANS, textAlign: 'center'
      }}
    >
      <div aria-hidden="true" style={{ display: 'grid', gridTemplateColumns: 'repeat(9, 4px)', gap: 4 }}>
        {Array.from({ length: 81 }).map((_, n) => (
          <motion.span
            key={n}
            initial={{ opacity: 0, scale: 0.4 }}
            animate={{ opacity: 0.35, scale: 1 }}
            transition={{ duration: 0.4, ease: 'easeOut', delay: n * 0.004 }}
            style={{ width: 4, height: 4, borderRadius: 999, background: T.ink }}
          />
        ))}
      </div>

      <span style={{ fontFamily: MONO, fontSize: 13, letterSpacing: 3, color: T.ink }}>SKALDIR</span>

      <div style={{ height: 34, display: 'flex', alignItems: 'center' }}>
        <AnimatePresence mode="wait">
          <motion.span
            key={spruch}
            initial={{ opacity: 0, y: 8, filter: 'blur(2px)' }}
            animate={{ opacity: 1, y: 0, filter: 'blur(0px)' }}
            exit={{ opacity: 0, y: -8, filter: 'blur(2px)' }}
            transition={{ duration: 0.32, ease: 'easeOut' }}
            style={{ fontSize: 17, fontWeight: 500, color: T.ink }}
          >{spruch}</motion.span>
        </AnimatePresence>
      </div>

      <span style={{ fontFamily: MONO, fontSize: 11, color: T.faint }}>
        Ziel eintippen, Datei anhaengen oder ⌘K fuer Befehle · kein Orb im leeren Chat
      </span>
    </div>
  )
}
