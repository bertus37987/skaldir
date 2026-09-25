/**
 * Kategorie F — ZUSTÄNDE, ENDZUSTÄNDE & PRÜFZEILEN.
 *
 * Zustandsliste wörtlich aus `Skaldir_Release_Paket/01_PRODUCT_UX_DESIGN.md:68–80`
 * (UI-CONTRACT §4, „Zustandsliste"): zehn Zustände von `queued` bis `cancelled`,
 * je ein Textbeispiel und genau EINE zulässige Aktion.
 *
 * Indikatorregel (Paket `thinking-orbs@0.3.1`, hier lokal vendor'd — aus den
 * Paket-Typen gelesen, nicht geraten): nur NEUN Arbeitszustände (`working` … `shaping`),
 * nur die getunten Größen 20/64. `queued` ist eine App-Projektion ohne eigenen
 * Orb; ENDZUSTÄNDE (`succeeded`, `failed`, `cancelled`) zeigen KEINEN Orb —
 * statisches Symbol (✓ / ✕ / ◆), Produktregel: „Status immer Text UND Symbol".
 *
 * 04 („fertig") ist bewusst OHNE jede Animation: Finished ist ein einfaches
 * Statussymbol. 07/08 fahren echte Uhren (setInterval), alle Tabellen tooltippen
 * zeilenweise gestaffelt ein, Auf-/Zuklappen läuft über die gemeinsame Feder.
 */
import { motion, AnimatePresence, animate } from 'motion/react'
import React, { useEffect, useState } from 'react'
import { IconAlertCircle, IconArrowRight, IconBan, IconCheck, IconChevronRight, IconDiamond, IconEye, IconLock, IconPlayerPause, IconPlayerPlay, IconPlus, IconRefresh, IconSquare, IconX } from '@tabler/icons-react'

// @ts-expect-error — Laufzeit-Alias (siehe vite.config.ts)
import { ThinkingOrb } from 'thinking-orbs'

/* ---------- Farben, Schrift, Radien, Federn: eine Quelle (_tokens.ts) ---------- */
import { LIGHT, DARK, MONO, SANS, SPRING, RADIUS, TEXT } from './_tokens'
import { useBarrierefrei } from './_barrierefrei'

/* ---------- Echte, sekündlich laufende Zeit ---------- */
const useLive = (start: number) => {
  const [t, setT] = useState(start)
  useEffect(() => {
    const id = setInterval(() => setT(v => v + 1), 1000)
    return () => clearInterval(id)
  }, [])
  return t
}
const fmt = (s: number) => String(Math.floor(s / 60)).padStart(2, '0') + ':' + String(s % 60).padStart(2, '0')

/** Statisch, produktrein: Endzustände zeigen KEINEN Orb. */
const StateGlyph = ({ tone, dark }: { tone: 'done' | 'failed' | 'cancelled'; dark: boolean }) => {
  const P = dark ? DARK : LIGHT
  if (tone === 'done') return <span style={{ width: 20, display: 'inline-flex', justifyContent: 'center', color: P.ink }}><IconCheck size={15} stroke={2.4} /></span>
  if (tone === 'failed') return <span style={{ width: 20, display: 'inline-flex', justifyContent: 'center', color: P.ink }}><IconX size={15} stroke={2.4} /></span>
  return <span style={{ width: 20, display: 'inline-flex', justifyContent: 'center', color: P.faint }}><IconDiamond size={13} stroke={2.2} /></span>
}

/* ---------- Kopf eines Stage-Rahmens ---------- */
function Head({ dark, setDark, label, sub }: { dark: boolean; setDark: (d: boolean) => void; label: string; sub: string }) {
  const P = dark ? DARK : LIGHT
  return (
    <div style={{ marginBottom: 10 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
        <div style={{ fontFamily: SANS, fontSize: 15, fontWeight: 600, letterSpacing: -0.2 }}>{label}</div>
        <span data-themetoggle style={{ marginLeft: 'auto', display: 'inline-flex', gap: 0, border: `1px solid ${P.line}`, fontFamily: MONO, fontSize: TEXT.meta }}>
          {(['hell', 'dunkel'] as const).map(m => {
            const on = (m === 'dunkel') === dark
            return (
              <button key={m} onClick={() => setDark(m === 'dunkel')}
                style={{ padding: '3px 9px', border: 'none', cursor: 'pointer', fontFamily: MONO, fontSize: TEXT.meta,
                         background: on ? P.ink : 'transparent', color: on ? P.bg : P.sub }}>{m}</button>
            )
          })}
        </span>
      </div>
      <div style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, marginTop: 3 }}>{sub}</div>
    </div>
  )
}

/** Äußerer Rahmen jeder Variante; die Umschaltung hell/dunkel gehört der Variante. */
function Stage({ head, children, wide }: { head: React.ReactNode; children: React.ReactNode; wide?: boolean }) {
  useBarrierefrei()   // A1 reduzierte Bewegung + A5 Tastatur — erste Zeile jeder Buehne
  return (
    <div data-stage style={{ padding: 20, width: wide ? 960 : 780, boxSizing: 'border-box' }}>
      {head}
      <div data-stagebody style={{ position: 'relative' }}>{children}</div>
    </div>
  )
}

/* ---------- Der Zustandskatalog (wörtlich aus 01_PRODUCT_UX_DESIGN.md:68-80) ---------- */
type Tone = 'live' | 'done' | 'failed' | 'cancelled'
type S = {
  id: string            // englischer Identifier (Code-Ebene)
  text: string          // deutscher UI-Text (Produktwortlaut)
  orb: string | null    // welcher Orb — null: kein eigener Orb
  w: string             // deutsche Zustandszusammenfassung
  act: string           // die EINE erlaubte Aktion (Wortlaut Spalte 3)
  actKey: 'edit' | 'details' | 'pause' | 'reason' | 'decide' | 'resume' | 'retry' | 'open' | 'check' | 'cleanup'
  open: string          // deutsche Erklärung für Variante 02
}
/* Befund 8: Orb-Zustaende bleiben unveraendert gueltig — es sind GENAU die neun
   getunten Zustaende des Pakets (working, searching, solving, listening, connecting,
   weaving, composing, breathing, shaping). queued und die Endzustaende
   (succeeded, failed, cancelled) tragen keinen Orb: statisches Symbol, keine Animation. */
const STATES: S[] = [
  { id: 'queued',            text: 'Wartet auf Recherche',                       orb: null,         w: 'eingereiht',        act: 'Auftrag ändern',   actKey: 'edit',    open: 'Der Auftrag ist bestätigt, aber noch nicht übernommen — das Modell rechnet noch nicht. Jede Änderung ist hier billig, weil nichts von der Ausführung abhängt; sobald ein Token läuft, wird aus „ändern“ eine Zielsteuerung oder eine eigene Nachfrage.' },
  { id: 'starting',          text: 'Werkzeug wird gestartet',                    orb: 'working',    w: 'startet',           act: 'Abbruch, falls unterstützt', actKey: 'details', open: 'Werkzeug, Voice oder Worker bauen ihre Verbindung auf. Nicht jedes Tool kennt an dieser Stelle einen Abbruch — ob der Knopf „Abbruch“ zeigt oder schon „Stoppen“, entscheidet der Details-Weg, nicht das Symbol.' },
  { id: 'running',           text: 'Quellen werden gelesen',                     orb: 'searching',  w: 'läuft',             act: 'Pause/Stop, Details', actKey: 'pause', open: 'Die Aktionsgruppe arbeitet; die Zeile zeigt den Indikator und einmal die erlaubte Aktion Pause. Hohe Tokenraten bündeln statt neue DOM-Zeilen pro Token zu erzeugen.' },
  { id: 'waiting',           text: 'Wartet auf Provider',                        orb: 'breathing',  w: 'wartet',            act: 'Grund zeigen',     actKey: 'reason',  open: 'Die Runtime blockiert auf einem Backend, einem Key oder einer Rate. Vorgeschrieben ist ein sichtbarer Grund und DER vorhandene erlaubte Alternativweg — kein stiller Spinner, keine Erfundene Fehlertexte in der UI.' },
  { id: 'approval_required', text: 'Freigabe für Preview auf Projekt X',         orb: 'connecting', w: 'Freigabe nötig',    act: 'Freigabe/Ablehnung', actKey: 'decide', open: 'Die Zustandszeile steht bei der auslösenden Aktion und nennt Zielkonto, Wirkung, Umfang und Kosten. Ablehnung erhält Arbeit und zeigt die erlaubten Alternativen — die Freigabe darf durch keine kosmetische Vereinfachung entfallen.' },
  { id: 'paused',            text: 'Von dir pausiert',                           orb: 'breathing',  w: 'pausiert',          act: 'Fortsetzen',       actKey: 'resume',  open: 'Der Nutzer selbst hat angehalten. Fortsetzen ist der Weg — sofern weitere Sperren (Freigabe, Key, Verbindung) geklärt sind; danach zeigt die Zeile das, was noch blockiert.' },
  { id: 'failed',            text: 'Rendern fehlgeschlagen',                     orb: null,         w: 'fehlgeschlagen',    act: 'Verständlicher Fehler, gezielter Retry', actKey: 'retry', open: 'Fehlerflächen nennen NICHT pauschal „etwas ist schiefgegangen“, sondern Ursache und nächsten sinnvollen Schritt. Nach einem Fehler tritt der Orb ab: die Fehlerfläche ist statisch, der Retry ist zielgerichtet, kein Blindversuch.' },
  { id: 'succeeded',         text: 'Video erstellt und geprüft',                 orb: null,         w: 'fertig',            act: 'Vorschau, Nachweise', actKey: 'open', open: 'Endzustand: statisches Symbol. Toolerfolg ist nicht der Goal-Abschluss — die Vorschau öffnet das Artefakt, die Nachweise zeigen, welche Kriterien belegt sind und wo. Keine Animation; Finished ist ein einfaches Statussymbol.' },
  { id: 'unknown',           text: 'Bestätigung fehlt; Zielzustand wird geprüft', orb: 'connecting', w: 'unbestätigt',       act: 'Abgleichen, kein blinder Retry', actKey: 'check', open: 'Nach Abbruch oder Reload fehlt die letzte Bestätigung. Erlaubt ist gezielt ein Abgleich (mehrere kurze Zustandswechsel), nicht ein pauschaler Neustart — ein blinder Retry würde Arbeit doppelt ausführen.' },
  { id: 'cancelled',         text: 'Abgebrochen',                                orb: null,         w: 'abgebrochen',       act: 'Verbleibende externe Wirkung zeigen', actKey: 'cleanup', open: 'Abgebrochen heißt nicht aufgeräumt: ein laufender externer Prozess wird NICHT als beendet dargestellt. Die Zeile zeigt, was draußen noch läuft, und benennt „als neuen Lauf starten“ als Weg.' },
]

/* Zustandskatalog — wörtlich 01_PRODUCT_UX_DESIGN.md:68-80; Zeilen-IDs englisch, UI-Texte deutsch */
const AktionIcon = ({ k, color, size = 12 }: { k: S['actKey']; color: string; size?: number }) => {
  const I = { edit: IconSquare, details: IconEye, pause: IconPlayerPause, reason: IconAlertCircle, decide: IconLock, resume: IconPlayerPlay, retry: IconRefresh, open: IconArrowRight, check: IconCheck, cleanup: IconBan }[k]
  return <I size={size} strokeWidth={2} color={color} />
}

function ActionPill({ s, dark }: { s: S; dark: boolean }) {
  const P = dark ? DARK : LIGHT
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, border: `1px solid ${P.line}`, padding: '3px 8px', fontFamily: MONO, fontSize: TEXT.meta, color: P.ink, cursor: 'pointer', whiteSpace: 'nowrap' }}>
      <AktionIcon k={s.actKey} color={P.ink} size={11} />
      {s.act}
    </span>
  )
}

/* ---------- 01 — alle zehn Zustände als kompakte Zeilen ---------- */
function CueOrb({ orb, dark }: { orb: string | null; dark: boolean }) {
  if (!orb) return null
  return <ThinkingOrb state={orb as never} size={20} theme={dark ? 'dark' : 'light'} />
}

function RowsBase10({ dark }: { dark: boolean }) {
  const P = dark ? DARK : LIGHT
  return (
    <AnimatePresence initial>
      {STATES.map((s, i) => (
        <motion.div key={s.id} initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }}
          transition={{ ...SPRING, delay: i * 0.055 }}
          style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '9px 12px', background: P.surface, borderBottom: `1px solid ${P.hair}` }}>
          <span style={{ width: 20, display: 'inline-flex', justifyContent: 'center' }}>
            {s.orb ? <CueOrb orb={s.orb} dark={dark} /> : <StateGlyph tone={s.id === 'failed' ? 'failed' : s.id === 'succeeded' ? 'done' : 'cancelled'} dark={dark} />}
          </span>
          <span style={{ width: 132, fontFamily: MONO, fontSize: TEXT.meta, fontWeight: 600 }}>{s.id}</span>
          <span style={{ flex: 1, fontSize: 12.5, color: P.ink }}>{s.text}</span>
          <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, width: 96 }}>{s.w}</span>
          <ActionPill s={s} dark={dark} />
        </motion.div>
      ))}
    </AnimatePresence>
  )
}

/* ---------- 02 — dieselben Zeilen aufgeklappt mit Erklärung ---------- */
function RowsExplained({ dark }: { dark: boolean }) {
  const P = dark ? DARK : LIGHT
  const [open, setOpen] = useState<string | null>('running')
  return (
    <div>
      <div style={{ border: `1px solid ${P.line}`, background: P.surface, padding: '8px 12px', fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, borderBottom: 'none' }}>
        Klick auf eine Zeile klappt die Erklärung auf — jede Zeile nennt ihre Regel
      </div>
      {STATES.map((s, i) => {
        const on = open === s.id
        return (
          <div key={s.id} style={{ borderBottom: `1px solid ${P.hair}`, background: P.surface }}>
            <motion.div onClick={() => setOpen(on ? null : s.id)} whileTap={{ scale: 0.997 }} transition={SPRING}
              initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }}
              data-row={s.id} data-open={on ? 'true' : 'false'}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '9px 12px', cursor: 'pointer' }}>
                <span style={{ width: 20, display: 'inline-flex', justifyContent: 'center' }}>
                  {s.orb ? <CueOrb orb={s.orb} dark={dark} /> : <StateGlyph tone={s.id === 'failed' ? 'failed' : s.id === 'succeeded' ? 'done' : 'cancelled'} dark={dark} />}
                </span>
                <span style={{ width: 132, fontFamily: MONO, fontSize: TEXT.meta, fontWeight: 600 }}>{s.id}</span>
                <span style={{ flex: 1, fontSize: 12.5, color: P.ink }}>{s.text}</span>
                <motion.span animate={{ rotate: on ? 90 : 0 }} transition={SPRING} style={{ display: 'inline-flex', color: P.sub }}>
                  <IconChevronRight size={12} stroke={2} />
                </motion.span>
              </div>
              <AnimatePresence initial={false}>
                {on && (
                  <motion.div key="detail" data-explained initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }}
                    exit={{ height: 0, opacity: 0 }} transition={SPRING} style={{ overflow: 'hidden' }}>
                    <div style={{ padding: '0 12px 12px 44px', fontSize: 12, lineHeight: 1.55, color: P.sub, maxWidth: 620 }}>
                      {s.open}
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
            </motion.div>
          </div>
        )
      })}
    </div>
  )
}

/* ---------- 03 — Prüfzeilen (Verification): Kriterium · Nachweis · Ort · ✓ ---------- */
const CHECKS: [string, string, string][] = [
  ['S01 · Leerer Chat startet in die letzte Session', 'Overlay-Test bei Heimatroute "/"', '§6 Startregeln'],
  ['S02 · State immer Text UND Symbol', 'Zustandszeile 01 bis 03', 'design: 68-80'],
  ['S03 · Nach Fehler KEIN Orb', 'Fehlerfläche type "✕" Rein', 'widgets-Chat 65'],
  ['S04 · Endzustand KEIN Orb, KEINE Animation', 'Zeile Finished, state "fertig"', 'Variante 04'],
  ['V2-01 · Wartezeit mit Grund + Ausweg', "'Der Provider antwortet nicht; Verbindung testen'", 'Variante 02, waiting'],
  ['PX-04 · Freigabe steht bei der Aktion', 'Zeile approval_required', '§6 Freigaben'],
]

function CheckRows({ dark }: { dark: boolean }) {
  const P = dark ? DARK : LIGHT
  return (
    <div>
      <div style={{ display: 'flex', gap: 10, padding: '5px 12px', fontFamily: MONO, fontSize: TEXT.meta, color: P.faint, borderBottom: `1px solid ${P.line}`, textTransform: 'uppercase', letterSpacing: 0.6 }}>
        <span style={{ flex: '0 0 250px' }}>Kriterium</span><span style={{ flex: 1 }}>Nachweis</span><span style={{ width: 160 }}>Ort</span><span style={{ width: 18 }} />
      </div>
      <AnimatePresence initial>
        {CHECKS.map((row, i) => (
          <motion.div key={row[0]} data-check={row[0]} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}
            transition={{ ...SPRING, delay: i * 0.06 }}
            style={{ display: 'flex', gap: 10, alignItems: 'baseline', padding: '7px 12px', background: P.surface, borderBottom: `1px solid ${P.hair}` }}>
            <span style={{ flex: '0 0 250px', fontFamily: SANS, fontSize: 11.5, color: P.ink }}>{row[0]}</span>
            <span style={{ flex: 1, fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{row[1]}</span>
            <span style={{ width: 160, fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>{row[2]}</span>
            <span style={{ width: 18, display: 'inline-flex', justifyContent: 'center', color: P.ink }}><IconCheck size={12} stroke={2.4} /></span>
          </motion.div>
        ))}
      </AnimatePresence>
      <div style={{ padding: '7px 12px', fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>
        {CHECKS.length} Kriterien geprüft · Kompakt für Berichte — jede Zeile trägt ihren eigenen Beleg
      </div>
    </div>
  )
}

/* ---------- 04 — 'fertig' = Endzustand OHNE Animation ---------- */
function DoneCard({ dark }: { dark: boolean }) {
  const P = dark ? DARK : LIGHT
  return (
    <div style={{ border: `1px solid ${P.line}`, background: P.surface, padding: 0 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 14px', borderBottom: `1px solid ${P.hair}` }}>
        <span data-glyph style={{ width: 20, display: 'inline-flex', justifyContent: 'center', color: P.ink }}>
          <IconCheck size={16} stroke={2.4} />
        </span>
        <span style={{ fontSize: 13, fontWeight: 600 }}>fertig — Ziel erreicht</span>
        <span style={{ flex: 1 }} />
        <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>succeeded · statisches Symbol, KEIN Orb, KEINE Animation</span>
      </div>
      <div style={{ padding: '10px 12px 12px', display: 'flex', gap: 14, flexWrap: 'wrap' }}>
        {[
          ['Ergebnis', 'Video erstellt und geprüft · 3 von 3 Kriterien verifiziert'],
          ['Prüfzeilen', 'K1 · render OK — Vorschau geöffnet · K2 · audit pass — Bericht · K3 · logo ✓ — Wortmarke'],
          ['Aktion', 'Vorschau, Nachweise'],
        ].map((r, i) => (   /* statisches Deko-Array, feste Reihenfolge — Indexschluessel zulaessig */
          <div key={i} style={{ border: `1px solid ${P.hair}`, borderRadius: RADIUS.row, padding: '8px 10px', minWidth: 200 }}>
            <div style={{ fontFamily: MONO, fontSize: TEXT.meta, letterSpacing: 0.6, color: P.faint, marginBottom: 4, textTransform: 'uppercase' }}>{r[0]}</div>
            <div style={{ fontFamily: SANS, fontSize: 11.5, color: P.ink }}>{r[1]}</div>
          </div>
        ))}
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <span style={{ fontSize: TEXT.meta, fontFamily: MONO, color: P.sub }}>Vorschau</span>
          <span style={{ fontSize: 11, fontFamily: SANS, textDecoration: 'underline', textDecorationColor: P.line, cursor: 'pointer' }}>Video öffnen</span>
          <span style={{ fontSize: 11, fontFamily: SANS, textDecoration: 'underline', textDecorationColor: P.line, cursor: 'pointer' }}>Nachweise</span>
        </div>
      </div>
    </div>
  )
}

/* ---------- 05 — 'unvollständig': was fehlt, welcher Weg bleibt ---------- */
const OPEN_CRIT = [
  'Kriterium V2-08 · Keyrotation nicht nachgewiesen — Verbindungstest fehlt',
  'Kriterium S04 · vergessen: Reduced Motion deaktiviert nicht die Feder',
  'Kriterium PX-06 · Kontrast dunkel an Grenzlinien — Linienfarbe zu dunkel',
]

function IncompleteCard({ dark }: { dark: boolean }) {
  const P = dark ? DARK : LIGHT
  return (
    <div style={{ border: `1px solid ${P.line}`, background: P.surface, borderRadius: RADIUS.module, overflow: 'hidden' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 12px', borderBottom: `1px solid ${P.hair}` }}>
        <span style={{ width: 20, display: 'inline-flex', justifyContent: 'center', color: P.ink }}><IconAlertCircle size={15} stroke={2.2} /></span>
        <span style={{ fontSize: 13, fontWeight: 600 }}>unvollständig</span>
        <span style={{ flex: 1 }} />
        <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>3 offene Kriterien · Zustand bleibt, bis sie belegt sind</span>
      </div>
      <div style={{ padding: '10px 12px' }}>
        <div style={{ fontFamily: MONO, fontSize: TEXT.meta, letterSpacing: 0.6, color: P.faint, textTransform: 'uppercase', marginBottom: 6 }}>Offene Kriterien</div>
        <AnimatePresence initial>
          {OPEN_CRIT.map((c, i) => (
            <motion.div key={c} data-open-criterion initial={{ opacity: 0, x: -8 }} animate={{ opacity: 1, x: 0 }}
              transition={{ ...SPRING, delay: 0.1 + i * 0.07 }}
              style={{ display: 'flex', alignItems: 'baseline', gap: 8, padding: '4px 0', borderBottom: `1px dashed ${P.hair}` }}>
              <span style={{ width: 14, display: 'inline-flex', justifyContent: 'center', color: P.faint }}><IconAlertCircle size={10} stroke={2.4} /></span>
              <span style={{ flex: 1, fontSize: 11.5, color: P.ink }}>{c.split('·')[0]}</span>
              <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub }}>{c.split('·')[1]}</span>
            </motion.div>
          ))}
        </AnimatePresence>
        <div style={{ marginTop: 12, display: 'flex', gap: 10, alignItems: 'center', paddingTop: 10, borderTop: `1px solid ${P.hair}` }}>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontFamily: MONO, fontSize: TEXT.meta, color: P.ink }}>
            <IconArrowRight size={11} stroke={2} /> verbleibender Weg: zuerst V2-08 (Verbindungstest), dann PX-06, zuletzt S04 (Reduced-Motion-Federn)
          </span>
          <span style={{ flex: 1 }} />
          <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub }}>Gesamtstand: 7 von 10</span>
        </div>
      </div>
    </div>
  )
}

/* ---------- 06 — 'abgebrochen': Grund, Zeitpunkt, 'als neuen Lauf starten' ---------- */
function CancelledCard({ dark }: { dark: boolean }) {
  const P = dark ? DARK : LIGHT
  const t = useLive(412)
  return (
    <div style={{ border: `1px solid ${P.line}`, background: P.surface, borderRadius: RADIUS.module, overflow: 'hidden' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 12px', borderBottom: `1px solid ${P.hair}` }}>
        <span style={{ width: 20, display: 'inline-flex', justifyContent: 'center', color: P.faint }}><IconDiamond size={13} stroke={2.2} /></span>
        <span style={{ fontSize: 13, fontWeight: 600 }}>abgebrochen</span>
        <span style={{ flex: 1 }} />
        <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>cancelled · externer Prozess nicht aufgeräumt</span>
      </div>
      <div style={{ padding: '10px 12px', display: 'grid', gridTemplateColumns: '150px 1fr auto', gap: '4px 14px', alignItems: 'baseline' }}>
        <span style={{ fontFamily: MONO, fontSize: TEXT.meta, letterSpacing: 0.5, color: P.faint, textTransform: 'uppercase' }}>Abbruchgrund</span>
        <span style={{ fontSize: 12, color: P.ink }}>Von dir abgebrochen — „Stoppen“ im Composer, kein Crash</span>
        <span />
        <span style={{ fontFamily: MONO, fontSize: TEXT.meta, letterSpacing: 0.5, color: P.faint, textTransform: 'uppercase' }}>Zeitpunkt</span>
        <span style={{ fontSize: 12, color: P.ink }}>heute, <span style={{ fontFamily: MONO, fontSize: 11 }}>{fmt(t)}</span> davor — gezielt „Stoppen“, kein Crash</span>
        <span />
        <span style={{ fontFamily: MONO, fontSize: TEXT.meta, letterSpacing: 0.5, color: P.faint, textTransform: 'uppercase' }}>Externe Wirkung</span>
        <span style={{ fontSize: 12, color: P.sub }}>Das Skript auf dem Host läuft WEITER — hier NICHT als beendet darstellen</span>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, border: `1px solid ${P.line}`, padding: '4px 9px', fontFamily: MONO, fontSize: TEXT.meta, cursor: 'pointer' }}>
          <IconPlus size={11} stroke={2} /> als neuen Lauf starten
        </span>
      </div>
    </div>
  )
}

/* ---------- 07 — Fortschritt mehrerer gleichzeitiger Zustände (echte Uhren) ---------- */
const CONC = [
  { name: 'Recherche',  orb: 'searching', step: '3/8 Quellen',   start: 143, pct: 42 },
  { name: 'Reasoning',  orb: 'solving',   step: 'Kriterium 4/7', start: 78,  pct: 55 },
  { name: 'Freigabe',   orb: 'breathing', step: 'Preview · wartet auf dich', start: 29,  pct: 0 },
] as const

function ConcurrentRows({ dark }: { dark: boolean }) {
  const P = dark ? DARK : LIGHT
  const t = useLive(0)
  return (
    <div style={{ border: `1px solid ${P.line}`, background: P.surface }}>
      <div style={{ display: 'flex', gap: 10, padding: '6px 12px', borderBottom: `1px solid ${P.line}`, fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>
        <span>drei parallele Aktivitäten — jede ihre eigene Uhr</span>
        <span style={{ marginLeft: 'auto' }}>nebenher läuft der Abnahmefall „drei parallele Aktivitäten“</span>
      </div>
      {CONC.map((w, i) => {
        const ever = t + w.start
        return (
          <motion.div key={w.name} data-conc={w.name} initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }}
            transition={{ ...SPRING, delay: i * 0.06 }}
            style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 12px', borderBottom: `1px solid ${P.hair}` }}>
            <CueOrb orb={w.orb} dark={dark} />
            <span style={{ width: 90, fontSize: 12.5, fontWeight: 600 }}>{w.name}</span>
            <span style={{ flex: 1, fontFamily: MONO, fontSize: TEXT.meta, color: P.sub }}>{w.step}</span>
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
              <span style={{ position: 'relative', width: 120, height: 4, background: P.hair }}>
                <motion.span animate={{ width: `${w.pct}%` }} transition={SPRING} style={{ position: 'absolute', inset: 0, right: 'auto', height: 4, background: P.ink }} />
              </span>
              <span style={{ width: 30, textAlign: 'right', fontFamily: MONO, fontSize: TEXT.meta, color: P.sub }}>{w.pct}%</span>
            </span>
            <span style={{ width: 44, textAlign: 'right', fontFamily: MONO, fontSize: TEXT.meta, color: P.ink }} data-conc-time>{fmt(ever)}</span>
          </motion.div>
        )
      })}
    </div>
  )
}

/* ---------- 08 — Zustandshistorie als Zeitstrahl (echte Uhr) ---------- */
const TIMELINE = [
  { id: 'queued',            from: 0,   to: 41,   rune: 'eingereiht' },
  { id: 'running',           from: 41,  to: 183,  rune: 'quellen' },
  { id: 'waiting',           from: 183, to: 224,  rune: 'provider' },
  { id: 'running',           from: 224, to: 402,  rune: 'weiter' },
  { id: 'approval_required', from: 402, to: 449,  rune: 'freigabe' },
  { id: 'running',           from: 449, to: 611,  rune: 'auftrag' },
  { id: 'succeeded',         from: 611, to: 646,  rune: 'fertig' },
] as const

const ORB_FOR: Record<string, string | null> = {
  queued: null, running: 'working', waiting: 'breathing', approval_required: 'connecting', succeeded: null
}

function Timeline({ dark }: { dark: boolean }) {
  const P = dark ? DARK : LIGHT
  const t = useLive(646)
  const SPAN = 646
  const pctOf = (a: number) => (a / SPAN) * 100
  return (
    <div style={{ border: `1px solid ${P.line}`, background: P.surface, padding: '12px 14px 14px' }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 10 }}>
        <span style={{ fontFamily: MONO, fontSize: TEXT.meta, letterSpacing: 0.8, color: P.faint }}>LAUF · HISTORIE</span>
        <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.ink }} data-timeline-now>Gesamtzeit {fmt(t)}</span>
        <span style={{ marginLeft: 'auto', fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>je Abschnitt: Zustand · Dauer</span>
      </div>
      {/* Balkenzeitstrahl */}
      <div style={{ position: 'relative', height: 30, border: `1px solid ${P.hair}`, background: P.bg, marginBottom: 10 }}>
        {TIMELINE.map(seg => {
          const missing = ORB_FOR[seg.id]
          const w = seg.to - seg.from
          return (
            <motion.div key={`${seg.from}-${seg.id}`} data-seg={seg.id}
              initial={{ opacity: 0, scaleY: 0.4 }} animate={{ opacity: 1, scaleY: 1 }} transition={{ ...SPRING, delay: 0.05 * TIMELINE.indexOf(seg) }}
              style={{ position: 'absolute', left: `${pctOf(seg.from)}%`, width: `${(w / SPAN) * 100}%`, top: 4, bottom: 4, background: missing ? 'transparent' : P.ink, opacity: missing ? 0 : 0.9 }} />
          )
        })}
        {TIMELINE.map(seg => seg.from > 0 && (
          <span key={`tick${seg.from}`} style={{ position: 'absolute', left: `${pctOf(seg.from)}%`, top: 0, bottom: 0, borderLeft: `1px dashed ${P.hair}` }} />
        ))}
      </div>
      <AnimatePresence initial>
        {TIMELINE.map((seg, i) => (
          <motion.div key={`${seg.from}-${seg.id}`} data-hist={seg.id} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}
            transition={{ ...SPRING, delay: i * 0.055 }}
            style={{ display: 'flex', alignItems: 'center', gap: 9, padding: '4px 2px', borderBottom: i === TIMELINE.length - 1 ? 'none' : `1px solid ${P.hair}` }}>
            <span style={{ width: 20, display: 'inline-flex', justifyContent: 'center' }}>
              {ORB_FOR[seg.id] ? <CueOrb orb={ORB_FOR[seg.id]!} dark={dark} /> : <StateGlyph tone={seg.id === 'succeeded' ? 'done' : 'cancelled'} dark={dark} />}
            </span>
            <span style={{ width: 140, fontFamily: MONO, fontSize: TEXT.meta, color: P.ink }}>{seg.id}</span>
            <span style={{ width: 120, fontFamily: MONO, fontSize: TEXT.meta, color: P.sub }}>{fmt(seg.from)} → {fmt(seg.to)}</span>
            <span style={{ flex: 1, fontSize: 11, color: P.sub }}>{seg.rune}</span>
            <span style={{ width: 54, textAlign: 'right', fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>{fmt(seg.to - seg.from)}</span>
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  )
}

/* ---------- 09 — sehr kompakt: eine Zeile: Zustand · Fortschritt · Aktion ---------- */
function SlimRow({ dark }: { dark: boolean }) {
  const P = dark ? DARK : LIGHT
  return (
    <div style={{ border: `1px solid ${P.line}`, background: P.surface, padding: '9px 12px', display: 'flex', alignItems: 'center', gap: 10 }}>
      <span style={{ width: 20, display: 'inline-flex', justifyContent: 'center' }}><CueOrb orb="solving" dark={dark} /></span>
      <span style={{ width: 96, fontFamily: MONO, fontSize: TEXT.meta, fontWeight: 600 }}>Prüfen</span>
      <span style={{ width: 120, fontFamily: MONO, fontSize: TEXT.meta, color: P.sub }}>4/7 Kriterien</span>
      <span style={{ width: 56, textAlign: 'right', fontFamily: MONO, fontSize: TEXT.meta, color: P.ink }}>57%</span>
      <span style={{ flex: 1 }} />
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, border: `1px solid ${P.line}`, padding: '3px 8px', fontFamily: MONO, fontSize: TEXT.meta, cursor: 'pointer' }}>
        <IconEye size={11} stroke={2} /> Details
      </span>
    </div>
  )
}

/** Variante-Rahmen: Umschalter hell/dunkel gehört der Variante (useState). */
function StageRows({ label, sub, wide, children }: { label: string; sub: string; wide?: boolean; children: (dark: boolean) => React.ReactNode }) {
  const [dark, setDark] = useState(false)
  const P = dark ? DARK : LIGHT
  return (
    <div style={{ color: P.ink, background: P.bg, fontFamily: SANS }}>
      <Stage wide={wide} head={<Head dark={dark} setDark={setDark} label={label} sub={sub} />}>
        <div style={{ border: `1px solid ${P.line}`, background: P.bg }}>
          {children(dark)}
        </div>
      </Stage>
    </div>
  )
}

const COMMON_HINT = 'Der Orb ist der ECHTE aus thinking-orbs@0.3.1 — Endzustände ohne Orb, ohne Animation.'

export default {
  '01 · Zustandszeilen: alle zehn, mit erlaubter Aktion': () => (
    <StageRows label="01 · Zustandszeilen" sub="alle zehn Zustände aus dem UX-Kapitel — Orb 20 oder Endsymbol · jede Zeile nennt die EINE erlaubte Aktion">
      {dark => <RowsBase10 dark={dark} />}
    </StageRows>
  ),

  '02 · Zustandszeilen aufgeklappt: Erklärung je Zustand': () => (
    <StageRows label="02 · Zustände — aufgeklappt" sub="dieselben Zeilen, auf Klick mit Erklärung (Satz aus der Design-Regel zu je Zustand)">
      {dark => <RowsExplained dark={dark} />}
    </StageRows>
  ),

  '03 · Prüfzeilen: Kriterium · Nachweis · Ort · ✓': () => (
    <StageRows label="03 · Prüfzeilen" sub="kompakt für Berichte — je Kriterium sein eigener Beleg und der Ort, wo er nachzuschlagen ist">
      {dark => <CheckRows dark={dark} />}
    </StageRows>
  ),

  '04 · „fertig“ — Endzustand, statisch, keine Animation': () => (
    <StageRows label="04 · fertig" sub="succeeded = statisches Häkchen, Zusammenfassung, Prüfzeilen — Produktregel: Finished ist ein einfaches Statussymbol; hier bewusst KEIN Motion">
      {dark => <DoneCard dark={dark} />}
    </StageRows>
  ),

  '05 · „unvollständig“ — was fehlt, welcher Weg bleibt': () => (
    <StageRows label="05 · unvollständig" sub="offene Kriterien im Blick, Reihenfolge des verbleibenden Wegs, Gesamtstand — kein Fortschritt Fake, kein Erfolg vor Evidence">
      {dark => <IncompleteCard dark={dark} />}
    </StageRows>
  ),

  '06 · „abgebrochen“ — Grund, Zeitpunkt, neuer Lauf': () => (
    <StageRows label="06 · abgebrochen" sub="Abbruchgrund, Zeitpunkt (echte Sekundenuhr), externe Wirkung, „als neuen Lauf starten“">
      {dark => <CancelledCard dark={dark} />}
    </StageRows>
  ),

  '07 · Fortschritt: drei gleichzeitige Zustände, je eigene Uhr': () => (
    <StageRows label="07 · gleichzeitig" sub="3 Zeilen, jeder mit eigenem Orb und live laufender Zeit — Abnahmefall „drei parallele Aktivitäten“">
      {dark => <ConcurrentRows dark={dark} />}
    </StageRows>
  ),

  '08 · Zustandshistorie eines Laufs als Zeitstrahl': () => (
    <StageRows label="08 · Historie" sub="wann welcher Zustand begann, Dauer je Abschnitt — eigener Balken oben, Liste unten, Gesamtzeit läuft (echte Uhr)">
      {dark => <Timeline dark={dark} />}
    </StageRows>
  ),

  '09 · sehr kompakt: Zustand · Fortschritt · Aktion in einer Zeile': () => (
    <StageRows label="09 · eine Zeile" sub="schlankste Fassung — passt direkt unter eine Aktion: Zustand, Fortschritt, erlaubte Aktion">
      {dark => <SlimRow dark={dark} />}
    </StageRows>
  ),

  '10 · dunkle Fassung des Favoriten (Vollbild dunkel)': () => (
    <StageRows label="10 · Favorit — dunkel" sub="Variante 01 als Ganzseite im dunklen Thema; Identische Zeilen, gleiche Federn, dunkle Tinte">
      {dark => <RowsBase10 dark={dark} />}
    </StageRows>
  ),
} as const
