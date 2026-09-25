/**
 * Werkzeugkarten (Kategorie A) — Werkzeug-Aufruf und Werkzeug-Ergebnis.
 *
 * 10 animierte Variationen: Karte, die ihre Parameter zeigt, Ergebnisse ein- und
 * aufklappt, Automationen lebt, Fehler benennt und Wege anbietet. Karte animiert
 * BEIM EINHAENGEN (Einblenden + Hoehe als Feder), Auf-/Zuklappen mit Feder,
 * der Orb läuft live. Die ersten 8 Varianten teilen einen hell/dunkel-Umschalter
 * im Kopf der Buehne; Variante 10 ist fest dunkel, Vollbild.
 *
 * Nur Schwarz/Weiss/Grau, Geometrie bevorzugt, echte Symbole nur aus @tabler/icons-react.
 */
import { motion, AnimatePresence, animate } from 'motion/react'
import React, { useEffect, useState } from 'react'
import { IconChevronRight, IconExternalLink, IconLock, IconRotateClockwise, IconServer } from '@tabler/icons-react'

// @ts-expect-error — Laufzeit-Alias (siehe vite.config.ts)
import { ThinkingOrb } from 'thinking-orbs'
import { useBarrierefrei } from './_barrierefrei'
import { LIGHT, DARK, MONO, SANS, SPRING, RADIUS, TEXT } from './_tokens'

/** Sekuendlich laufende Zeit — der Orb-Aufruf lebt. */
const useLive = (start) => {
  const [t, setT] = useState(start)
  useEffect(() => {
    const id = setInterval(() => setT(v => v + 1), 1000)
    return () => clearInterval(id)
  }, [])
  return t
}
const fmt = (s) => String(Math.floor(s / 60)).padStart(2, '0') + ':' + String(s % 60).padStart(2, '0')

/* -------------------------------------------------------- Bausteine */

/** Einhänge-Animation: Karte blendet ein und die Höhe federt auf. */
const Mount = ({ P, children, delay = 0 }) => (
  <motion.div data-toolcard initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: 'auto' }} exit={{ opacity: 0, height: 0 }}
    transition={{ ...SPRING, delay }} style={{ overflow: 'hidden' }}>
    {children}
  </motion.div>
)

/** Parameterzeile: Name links, Wert rechts, haardünn getrennt. */
const ParamRow = ({ k, v, P, i = 0, last = false }) => (
  <motion.div key={k} initial={{ opacity: 0, x: -4 }} animate={{ opacity: 1, x: 0 }} transition={{ ...SPRING, delay: 0.08 + i * 0.05 }}
    style={{ display: 'flex', gap: 10, alignItems: 'baseline', padding: '2px 0', borderBottom: last ? 'none' : `1px solid ${P.hair}` }}>
    <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint, minWidth: 74 }}>{k}</span>
    <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.ink, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{v}</span>
  </motion.div>
)

/** Kopfzeile der Karte: Orb oder Kennzeichen, Titel, rechts Metadaten. */
const CardHead = ({ P, orb, title, meta, onClick, chevron }) => (
  <motion.div data-toolhead onClick={onClick} whileTap={onClick ? { scale: 0.995 } : undefined}
    style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '7px 10px', cursor: onClick ? 'pointer' : 'default' }}>
    {orb}
    <span style={{ fontFamily: SANS, fontSize: 12, fontWeight: 600, color: P.ink }}>{title}</span>
    <span style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 9, fontFamily: MONO, fontSize: TEXT.meta, color: P.sub }}>{meta}</span>
    {chevron && (
      <motion.span animate={{ rotate: chevron.open ? 90 : 0 }} transition={SPRING} style={{ display: 'inline-flex', color: P.sub }}>
        <IconChevronRight size={11} stroke={2} />
      </motion.span>
    )}
  </motion.div>
)

/** Aufklappkörper mit Höhenfeder. */
const CardBody = ({ P, open, children, data }) => (
  <AnimatePresence initial={false}>
    {open && (
      <motion.div key='koerper' data={data} initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }}
        transition={SPRING} style={{ overflow: 'hidden', borderTop: `1px solid ${P.hair}` }}>
        <div style={{ padding: '7px 10px 9px' }}>{children}</div>
      </motion.div>
    )}
  </AnimatePresence>
)

/** Ergebnis-Zielzeile: Werkzeug → Ziel, darunter das Ergebnis. */
const TargetLine = ({ P, target }) => (
  <div style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, padding: '3px 10px', borderBottom: `1px solid ${P.hair}`, display: 'flex', gap: 8 }}>
    <span style={{ color: P.faint }}>ziel</span><span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{target}</span>
  </div>
)

const Knopf = ({ P, text, primary, i = 0 }) => (
  <motion.span initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} transition={{ ...SPRING, delay: 0.12 + i * 0.05 }}
    whileHover={{ scale: 1.03 }} whileTap={{ scale: 0.96 }} transition={SPRING}
    style={{ fontFamily: MONO, fontSize: TEXT.meta, padding: '4px 9px', border: `1px solid ${primary ? P.ink : P.line}`
      , background: primary ? P.ink : 'transparent', color: primary ? P.surface : P.ink, cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 5 }}>
    {text}
  </motion.span>
)

/* -------------------------------------------------------- 01 · Aufruf mit Parametern + Ergebniszeile */

function Card01 ({ P }) {
  const [open, setOpen] = useState(false)
  return (
    <Mount P={P}>
      <div style={{ border: `1px solid ${P.line}`, borderRadius: RADIUS.module, background: P.surface, overflow: 'hidden', maxWidth: 420 }}>
        <CardHead P={P}
          orb={<ThinkingOrb state='working' size={20} theme={P === DARK ? 'dark' : 'light'} />}
          title='terminal ausfuehren' meta={<span>00:09 · laeuft</span>} />
        <CardBody P={P} data='toolbody' open>
          <div>
            <ParamRow k='befehl' v='ruff check src tests tools' P={P} i={0} />
            <ParamRow k='arbeitsverz.' v='gods-voice/tools' P={P} i={1} />
            <ParamRow k='zeitlimit' v='120 s' P={P} i={2} last />
          </div>
        </CardBody>
        <TargetLine P={P} target='shell · lokal' />
        <motion.div data-toolresult initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ ...SPRING, delay: 0.35 }}
          style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.ink, padding: '6px 10px', display: 'flex', gap: 8, alignItems: 'center' }}>
          <motion.span initial={{ scale: 0.4, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} transition={SPRING} style={{ fontFamily: MONO }}>✓</motion.span>
          <span>All checks passed · 0 Befunde</span>
        </motion.div>
      </div>
    </Mount>
  )
}

/* -------------------------------------------------------- 02 · Aufgeklappt mit vollem Ergebnis */

function Card02 ({ P }) {
  const [open, setOpen] = useState(true)
  return (
    <Mount P={P}>
      <div style={{ border: `1px solid ${P.line}`, borderRadius: RADIUS.module, background: P.surface, overflow: 'hidden', maxWidth: 420 }}>
        <CardHead P={P}
          orb={<span style={{ width: 20, height: 20, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontFamily: MONO, fontSize: 11, color: P.ink }}>✓</span>}
          title='datei_lesen' meta={<span>18 KB · fertig</span>}
          onClick={() => setOpen(o => !o)} chevron={{ open }} />
        <CardBody P={P} open={open} data='toolbody'>
          <div>
            <ParamRow k='pfad' v='~/Documents/Hermes_Skaldir_Agent_Docs/UI-CONTRACT.md' P={P} i={0} />
            <ParamRow k='abschnitt' v='9 · Nachweise' P={P} i={1} last />
          </div>
        </CardBody>
        <TargetLine P={P} target='leser · utf-8' />
        <CardBody P={P} open={open} data='toolresult'>
          <pre data-toolresultpre style={{ margin: 0, fontFamily: MONO, fontSize: TEXT.meta, lineHeight: 1.55, color: P.ink, whiteSpace: 'pre-wrap' }}>
{`66 abgedeckte Anchors
- Anchors misst enamel
 jedes data-overlay erhaelt einen eindeutigen Namen.
 Die Pruefung laeuft vor jedem Build.viewmodel
- Kontext: 900x700, 1280x800, 1920x1080
- Status: bestanden`}
          </pre>
        </CardBody>
        {!open && (
          <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={SPRING}
            style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint, padding: '5px 10px' }}>Ergebnis eingeklappt · klick zum Aufklappen</motion.div>
        )}
      </div>
    </Mount>
  )
}

/* -------------------------------------------------------- 03 · Laufender Aufruf, abbrechbar */

function Card03 ({ P }) {
  const t = useLive(12)
  return (
    <Mount P={P}>
      <div style={{ border: `1px solid ${P.line}`, borderRadius: RADIUS.module, background: P.surface, overflow: 'hidden', maxWidth: 420 }}>
        <CardHead P={P}
          orb={<ThinkingOrb state='working' size={20} theme={P === DARK ? 'dark' : 'light'} />}
          title='parallel-suche' meta={<motion.span animate={{ opacity: [1, 0.5, 1] }} transition={{ duration: 2, repeat: Infinity, ease: 'easeInOut' }}>{fmt(t)}</motion.span>} />
        <TargetLine P={P} target='3 Arbeitssuchen · gemeinsam' />
        <div style={{ padding: '7px 10px 9px', display: 'flex', alignItems: 'center', gap: 10 }}>
          <motion.span animate={{ opacity: [1, 0.45, 1] }} transition={{ duration: 1.8, repeat: Infinity, ease: 'easeInOut' }}
            style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.ink }}>momentan im Lauf — kein Zwischenergebnis sichtbar</motion.span>
          <span style={{ flex: 1 }} />
          <Knopf P={P} text='abbrechen' i={0} />
        </div>
      </div>
    </Mount>
  )
}

/* -------------------------------------------------------- 04 · Fehler-Ergebnis */

function Card04 ({ P }) {
  return (
    <Mount P={P}>
      <div data-toolerrorcard style={{ border: `1.5px solid ${P.ink}`, borderRadius: RADIUS.module, background: P.surface, overflow: 'hidden', maxWidth: 420 }}>
        <CardHead P={P}
          orb={<motion.span initial={{ opacity: 0, scale: 0.6 }} animate={{ opacity: 1, scale: 1 }} transition={SPRING}
            style={{ fontFamily: MONO, fontSize: TEXT.body, color: P.ink }}>✕</motion.span>}
          title='webseite oeffnen' meta={<span>00:04 · gescheitert</span>} />
        <TargetLine P={P} target='http://127.0.0.1:5050/@/manifest' />
        <div data-toolerror style={{ padding: '7px 10px 9px' }}>
          <div style={{ fontFamily: SANS, fontSize: 12.5, fontWeight: 600, color: P.ink }}>Der IconServer antwortet nicht</div>
          <div style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, margin: '4px 0 7px' }}>
            Verbindung abgelehnt an Port 5050 — der Dev-IconServer laeuft nicht.
            Nichts wurde geladen. Neue Versuche enden solange gleich.
          </div>
          <div style={{ display: 'flex', gap: 7 }}>
            <Knopf P={P} text='erneut versuchen' primary i={0} />
            <Knopf P={P} text='anderen Weg suchen' i={1} />
          </div>
        </div>
      </div>
    </Mount>
  )
}

/* -------------------------------------------------------- 05 · Mehrere Aufrufe als kompakte Spur */

const RUNS = [
  { name: 'datei_lesen UI-CONTRACT.md', state: 'fertig', note: '18 KB', open: false },
  { name: 'grep "anchor"', state: 'offen', note: '00:07', open: true },
  { name: 'terminal ruff check', state: 'wartet', note: 'in Warteschlange' }
]

function Card05 ({ P }) {
  const [open, setOpen] = useState(true)
  return (
    <Mount P={P}>
      <div style={{ border: `1px solid ${P.line}`, borderRadius: RADIUS.module, background: P.surface, overflow: 'hidden', maxWidth: 420 }}>
        <CardHead P={P}
          orb={<ThinkingOrb state='working' size={20} theme={P === DARK ? 'dark' : 'light'} />}
          title='3 Aufrufe hintereinander' meta={<span>1 offen</span>} onClick={() => setOpen(o => !o)} chevron={{ open }} />
        <CardBody P={P} open={open} data='tooltrace'>
          {RUNS.map((r, i) => (
            <motion.div key={r.name} data-run={r.state} initial={{ opacity: 0, x: -4 }} animate={{ opacity: 1, x: 0 }} transition={{ ...SPRING, delay: 0.08 + i * 0.06 }}
              style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '3px 0', borderBottom: i === RUNS.length - 1 ? 'none' : `1px solid ${P.hair}` }}>
              <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>{i + 1}</span>
              <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.ink, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.name}</span>
              {r.state === 'offen' && <ThinkingOrb state='working' size={16} theme={P === DARK ? 'dark' : 'light'} />}
              <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, minWidth: 78, textAlign: 'right' }}>{r.note}</span>
              {r.state === 'fertig' && <motion.span initial={{ scale: 0.4, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} transition={{ ...SPRING, delay: 0.2 + i * 0.06 }} style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.ink }}>✓</motion.span>}
            </motion.div>
          ))}
        </CardBody>
      </div>
    </Mount>
  )
}

/* -------------------------------------------------------- 06 · Ergebnis ist eine Datei */

function Card06 ({ P }) {
  return (
    <Mount P={P}>
      <div style={{ border: `1px solid ${P.line}`, borderRadius: RADIUS.module, background: P.surface, overflow: 'hidden', maxWidth: 420 }}>
        <CardHead P={P}
          orb={<span style={{ width: 20, height: 20, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontFamily: MONO, fontSize: 11, color: P.ink }}>✓</span>}
          title='bericht erzeugen' meta={<span>00:31 · fertig</span>} />
        <TargetLine P={P} target='outputs/ui-audit/toolcards-ergebnis.md' />
        <motion.div data-filecard initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} transition={{ ...SPRING, delay: 0.3 }}
          style={{ margin: '7px 10px 9px', border: `1px solid ${P.line}`, background: P.bg, borderRadius: RADIUS.row, padding: 8, display: 'flex', alignItems: 'center', gap: 9 }}>
          <span style={{ width: 16, height: 20, border: `1px solid ${P.line}`, background: P.surface, position: 'relative', flex: '0 0 auto' }}>
            <span style={{ position: 'absolute', right: 0, top: 0, width: 6, height: 6, borderLeft: `1px solid ${P.line}`, borderBottom: `1px solid ${P.line}`, background: P.surface }} />
          </span>
          <span style={{ display: 'flex', flexDirection: 'column', minWidth: 0 }}>
            <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.ink, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>toolcards-ergebnis.md</span>
            <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>.md · 42 KB · 220 Zeilen</span>
          </span>
          <span style={{ flex: 1 }} />
          <Knopf P={P} text='oeffnen' primary i={0} />
        </motion.div>
      </div>
    </Mount>
  )
}

/* -------------------------------------------------------- 07 · Ergebnis ist eine Tabelle */

const TABLE = { rows: 128, cols: ['modell', 'dauer', 'botschaften'] }

function Card07 ({ P }) {
  return (
    <Mount P={P}>
      <div style={{ border: `1px solid ${P.line}`, borderRadius: RADIUS.module, background: P.surface, overflow: 'hidden', maxWidth: 420 }}>
        <CardHead P={P}
          orb={<span style={{ width: 20, height: 20, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontFamily: MONO, fontSize: 11, color: P.ink }}>✓</span>}
          title='messreihe laden' meta={<span>00:18 · fertig</span>} />
        <TargetLine P={P} target='messungen/historei-2026-09-18.json' />
        <motion.div data-tablecard initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} transition={{ ...SPRING, delay: 0.3 }}
          style={{ margin: '7px 10px 9px', border: `1px solid ${P.line}`, background: P.bg, borderRadius: RADIUS.row, padding: 8 }}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 6, fontFamily: MONO, fontSize: TEXT.meta, color: P.faint, borderBottom: `1px solid ${P.line}`, paddingBottom: 3 }}>
            {TABLE.cols.map(c => <span key={c}>{c.toUpperCase()}</span>)}
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 6, padding: '3px 0', fontFamily: MONO, fontSize: TEXT.meta, color: P.ink }}>
            <span>opencode</span><span>1,2 s</span><span>0</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 7 }}>
            <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub }}>{TABLE.rows} Zeilen · {TABLE.cols.length} Spalten</span>
            <span style={{ flex: 1 }} />
            <Knopf P={P} text='im Bericht oeffnen' primary i={0} />
          </div>
        </motion.div>
      </div>
    </Mount>
  )
}

/* -------------------------------------------------------- 08 · Aufruf mit Freigabe-Hinweis */

function Card08 ({ P }) {
  return (
    <Mount P={P}>
      <div data-approval style={{ border: `1px dashed ${P.ink}`, borderRadius: RADIUS.module, background: P.surface, overflow: 'hidden', maxWidth: 420 }}>
        <CardHead P={P}
          orb={<IconLock size={14} stroke={1.8} color={P.ink} />}
          title='datei schreiben' meta={<span>wartet auf Zustimmung</span>} />
        <TargetLine P={P} target='~/Documents/Design_Docs/notizen.md' />
        <div style={{ padding: '7px 10px 9px' }}>
          <div style={{ fontFamily: SANS, fontSize: 12.5, color: P.ink }}>Dieses Werkzeug braucht Ihre Zustimmung</div>
          <div style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, margin: '4px 0 7px' }}>
            Es wird eine Datei ausserhalb des Projektordners geaender — 3 Zeilen ueberschrieben.
          </div>
          <div style={{ display: 'flex', gap: 7 }}>
            <Knopf P={P} text='zustimmen' primary i={0} />
            <Knopf P={P} text='ablehnen' i={1} />
            <motion.span initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ ...SPRING, delay: 0.25 }}
              style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint, alignSelf: 'center' }}>Freigabekarte folgt</motion.span>
          </div>
        </div>
      </div>
    </Mount>
  )
}

/* -------------------------------------------------------- 09 · Sehr kompakt: eine Zeile */

const COMPACT = [
  { name: 'ruff check', dauer: '1,2 s', rechtsergebnis: '0 Befunde', ok: true },
  { name: 'datei_lesen', dauer: '0,4 s', rechtsergebnis: '18 KB', ok: true },
  { name: 'web laden', dauer: '3,1 s', rechtsergebnis: '200 OK', ok: true },
  { name: 'bericht bauen', dauer: '8,9 s', rechtsergebnis: '5 KB', ok: true }
]

function Card09 ({ P }) {
  return (
    <Mount P={P}>
      <div data-tooltracebar style={{ border: `1px solid ${P.line}`, borderRadius: RADIUS.module, background: P.surface, overflow: 'hidden', maxWidth: 420 }}>
        {COMPACT.map((r, i) => (
          <motion.div key={r.name} data-trace initial={{ opacity: 0, x: -4 }} animate={{ opacity: 1, x: 0 }} transition={{ ...SPRING, delay: 0.07 + i * 0.06 }}
            style={{ display: 'flex', alignItems: 'center', gap: 9, padding: '4px 10px', borderBottom: i === COMPACT.length - 1 ? 'none' : `1px solid ${P.hair}` }}>
            <motion.span initial={{ scale: 0.3, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} transition={{ ...SPRING, delay: 0.15 + i * 0.07 }}
              style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.ink }}>✓</motion.span>
            <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.ink }}>{r.name}</span>
            <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>{r.dauer}</span>
            <span style={{ marginLeft: 'auto', fontFamily: MONO, fontSize: TEXT.meta, color: P.sub }}>{r.rechtsergebnis}</span>
          </motion.div>
        ))}
      </div>
    </Mount>
  )
}

/* -------------------------------------------------------- 10 · Dunkle Fassung des Favoriten, Vollbild */

function Card10 () {
  useBarrierefrei()
  const P = DARK
  return (
    <div data-stage data-stage-dark style={{ position: 'fixed', inset: 0, background: P.bg, fontFamily: SANS, color: P.ink, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', zIndex: 10 }}>
      <div style={{ fontFamily: MONO, fontSize: 11, color: P.sub, marginBottom: 14 }}>10 · Dunkle Fassung der Werkzeugkarte · Vollbild</div>
      <div style={{ transform: 'scale(1.15)' }}>
        <Card02 P={P} />
      </div>
    </div>
  )
}

/* -------------------------------------------------------- Buehne mit hell/dunkel-Umschalter */

const Buehne = ({ name, note, children }) => {
  useBarrierefrei()
  const [dunkel, setDunkel] = useState(false)
  const P = dunkel ? DARK : LIGHT
  return (
    <div data-stage style={{ padding: 20, background: P.bg, fontFamily: SANS, width: '100%', maxWidth: 620, boxSizing: 'border-box' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
        <div style={{ fontFamily: MONO, fontSize: 12, fontWeight: 600, color: P.ink }}>{name}</div>
        <span style={{ flex: 1 }} />
        <motion.span onClick={() => setDunkel(d => !d)} whileTap={{ scale: 0.94 }} transition={SPRING} data-themetoggle
          title='hell/dunkel schalten' style={{ fontFamily: MONO, fontSize: TEXT.meta, padding: '3px 9px', border: `1px solid ${P.line}`, borderRadius: RADIUS.pill, cursor: 'pointer', color: P.ink, background: P.surface }}>
          {dunkel ? 'DUNKEL' : 'HELL'}
        </motion.span>
      </div>
      <div style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, marginBottom: 10 }}>{note}</div>
      {children({ P })}
    </div>
  )
}

export default {
  '01 · Aufruf mit Parametern + Ergebniszeile': ({ }) => (
    <Buehne name='01 · Aufruf mit Parametern' note='Werkzeugname, Argumente als Zeilen, Ziel, Ergebniszeile mit Haken'>
      {({ P }) => <Card01 P={P} />}
    </Buehne>
  ),
  '02 · Aufgeklappt mit vollem Ergebnis': ({ }) => (
    <Buehne name='02 · Volles Ergebnis' note='aufgeklappt, mehrzeilig, Monospace; Kopf klappt mit Feder zu und auf'>
      {({ P }) => <Card02 P={P} />}
    </Buehne>
  ),
  '03 · Laufender Aufruf, abbrechbar': ({ }) => (
    <Buehne name='03 · Laeuft' note='Orb working, Laufzeit laeuft echt mit, abbrechbar'>
      {({ P }) => <Card03 P={P} />}
    </Buehne>
  ),
  '04 · Fehler-Ergebnis': ({ }) => (
    <Buehne name='04 · Fehler' note='klarer Fehlertext, Ort des Fehlers, Wiederholen als erster Knopf'>
      {({ P }) => <Card04 P={P} />}
    </Buehne>
  ),
  '05 · Mehrere Aufrufe als kompakte Spur': ({ }) => (
    <Buehne name='05 · Spur von 3' note='drei Zeilen, eine laeuft offen; Spur klappt mit Feder auf und zu'>
      {({ P }) => <Card05 P={P} />}
    </Buehne>
  ),
  '06 · Ergebnis ist eine Datei': ({ }) => (
    <Buehne name='06 · Datei' note='Pfad, Endung, Groesse, Knopf oeffnen'>
      {({ P }) => <Card06 P={P} />}
    </Buehne>
  ),
  '07 · Ergebnis ist eine Tabelle': ({ }) => (
    <Buehne name='07 · Tabelle' note='Zeilenzahl, Spalten, Knopf im Bericht oeffnen'>
      {({ P }) => <Card07 P={P} />}
    </Buehne>
  ),
  '08 · Aufruf mit Freigabe-Hinweis': ({ }) => (
    <Buehne name='08 · Freigabe' note='Werkzeug braucht Zustimmung, Verweis auf die Freigabekarte'>
      {({ P }) => <Card08 P={P} />}
    </Buehne>
  ),
  '09 · Sehr kompakt: eine Zeile je Aufruf': ({ }) => (
    <Buehne name='09 · Kompaktspur' note='eine Zeile: Name, Dauer, Ergebnis, Haken — ideal fuer lange Laeufe'>
      {({ P }) => <Card09 P={P} />}
    </Buehne>
  ),
  '10 · Dunkle Fassung, Vollbild': ({ }) => (
    <Buehne name='10 · Dunkel' note='Favorit (02) fest dunkel, Vollbild — ohne Umschalter'>
      {() => <Card10 />}
    </Buehne>
  )
}
