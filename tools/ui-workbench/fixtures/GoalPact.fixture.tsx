/**
 * Schritt 3c — ZIEL-VERTRAG (PAKT) INLINE IM CHAT.
 *
 * Fachliche Quelle (gelesen, nicht verändert):
 * `Skaldir_Release_Paket/01_PRODUCT_UX_DESIGN.md` §6 „Ziele, Voice und Freigaben":
 *   Zeile 89 — „Plan als kompakte einklappbare Nachricht: Ziel, erforderliche Kriterien,
 *               offene Fragen. Hinzufügen, Umformulieren, Reihenfolge und Auswahl inline.
 *               ‚Gehört zum Auftrag' ist getrennt von ‚unabhängig geprüft'. Elternpunkte
 *               werden erst erfüllt, wenn erforderliche Kinder belegt sind."
 *   Zeile 91 — „Speichern verwendet erwartete Revision. Bei Konflikt bleibt der Entwurf
 *               erhalten; zeige aktuelle Fassung und eigene Änderung zum Abgleich.
 *               Zustände ‚gespeichert', ‚übernommen' und ‚verifiziert' stammen aus der
 *               Runtime. Keine optimistische grüne Erfolgsmeldung für unbestätigte Übernahme."
 *   Zeile 62 — „nur eine autorisierte Goal-Änderung verändert Kriterien."
 *
 * Der Pakt war bisher NUR im Panel zu sehen (Ziel-Baum). Hier ist er eine Nachricht im
 * Verlauf: kompakt, einklappbar, mit Zielen, Nicht-Zielen, Budget, Kriterien und Nachweis.
 *
 * Zustände sind echt (useState): Auf-/Zuklappen läuft über die gemeinsame Feder, ein
 * Kriterium hinzufügen wirkt wirklich (Zähler 6/9 → 6/10), die drei Wege „annehmen ·
 * ändern · ablehnen" schalten wirklich um (‚eingereiht' erst, ‚übernommen' nach
 * Runtime-Bestätigung — kein optimistisches Grün), Budgetbalken laufen per animate()
 * von 0 auf den Wert, Durchstreichungen wachsen als Linie, Diff- und Belegzeilen fahren
 * versetzt ein, der Revisionskonflikt zeigt beide Fassungen zum Abgleich.
 *
 * Hell/Dunkel: jede Variante hat ihren eigenen Umschalter; nur Schwarz/Weiß/Grau.
 */
import { motion, AnimatePresence, animate } from 'motion/react'
import React, { useState, useRef, useEffect } from 'react'
import { IconAlertCircle, IconArrowRight, IconBan, IconCheck, IconChevronDown, IconChevronRight, IconClock, IconCoins, IconEye, IconFileText, IconGauge, IconHistory, IconListCheck, IconLock, IconPencil, IconPlus, IconRefresh, IconStack2, IconTarget, IconX } from '@tabler/icons-react'

// @ts-expect-error — Laufzeit-Alias (siehe vite.config.ts)
import { ThinkingOrb } from 'thinking-orbs'

/* ---------------------------------------------------------------- Grundwerte */

/* Farben, Schrift, Federn, Radien und Schriftgroessen: ./_tokens (eine Wahrheit).
   Befunde 18.09.2026: kein eigener Hexwert, Schriftuntergrenze 11 px (TEXT.meta),
   Radien aus RADIUS, useBarrierefrei() gegen reduzierte Bewegung und fuer die Tastatur. */
import { LIGHT, DARK, MONO, SANS, SPRING, RADIUS, TEXT } from './_tokens'
import { useBarrierefrei } from './_barrierefrei'

type Pal = typeof LIGHT

/* ---------------------------------------------------------------- Pakt-Inhalt */

const ZIELE = [
  { id: 'g1', t: 'Abnahme läuft am gebauten Skaldir', sub: 'Vorher/Nachher, nicht nur Webvorschau', done: true },
  { id: 'g2', t: 'Nachweise sind messbar', sub: '28/28 Anker · 0 Overlay-Befunde', done: true },
  { id: 'g3', t: 'Echter Nutzerweg läuft', sub: 'Composer → Session → Senden → Transkript', done: true },
  { id: 'g4', t: 'Zustände bleiben erhalten', sub: 'Reload stellt Session her, Baum überlebt Neustart', done: false },
]

const NICHT_ZIELE = [
  { t: 'Neues Designsystem einführen', wirkung: 'Tokens würden wandern und jede geprüfte Kontrastzeile neu kippen', rest: 'bleibt außen vor' },
  { t: 'Mobil-Layout unter 900 px', wirkung: 'bricht die Mindestfälle 1280×800 und 900×650 auf', rest: 'bleibt außen vor' },
  { t: 'Voice-Oberfläche umbauen', wirkung: 'Voice meldet nur Relevantes — kein Umbau in diesem Auftrag', rest: 'bleibt außen vor' },
]

const BUDGET = [
  { k: 'Zeit', wert: '42 von 90 min', pct: 47, rest: '48 min übrig', warn: false },
  { k: 'Tokens', wert: '198k von 250k', pct: 79, rest: '52k übrig · Warnung ab 80 %', warn: true },
  { k: 'Kosten', wert: '0,84 € von 2,00 €', pct: 42, rest: '1,16 € Spielraum', warn: false },
]

type Krit = { id: string; t: string; done: boolean; wo: string; beleg: string }
const KRITERIEN: Krit[] = [
  { id: 'S01', t: 'Leerer Chat startet in die letzte Session', done: true, wo: 'overlay-test §6', beleg: 'Heimatroute „/" → letzte Session, 1 Prüflauf' },
  { id: 'S02', t: 'Zustand immer Text UND Symbol', done: true, wo: 'design: 68-80', beleg: 'Zehn Zustandszeilen, je Text und Symbol' },
  { id: 'S03', t: 'Nach Fehler kein Orb', done: true, wo: 'Widgets-Chat 65', beleg: 'Fehlerfläche statisch, Retry gezielt' },
  { id: 'S04', t: 'Endzustand ohne Orb und ohne Animation', done: true, wo: 'StateRows · Variante 04', beleg: 'fertig-Karte bewusst ohne Motion' },
  { id: 'V2-01', t: 'Wartezeit mit Grund und Ausweg', done: true, wo: 'StateRows · Variante 02, waiting', beleg: '„Der Provider antwortet nicht; Verbindung testen"' },
  { id: 'V2-08', t: 'Keyrotation nachgewiesen', done: true, wo: 'Zugangsdaten-Test', beleg: 'Schlüssel maskiert, widerrufen → neu gesetzt' },
  { id: 'PX-04', t: 'Freigabe steht bei der Aktion', done: false, wo: 'offen — Approval-Karte', beleg: 'fehlt: Wirkung, Umfang und Kosten an der Zeile' },
  { id: 'PX-06', t: 'Kontrast dunkel 3:1 an Haarlinien', done: false, wo: 'offen — dunkle Haarlinie unter 3:1', beleg: 'fehlt: gemessener Wert an der dunklen Haarlinie' },
  { id: 'VD-22', t: 'Pakt inline im Chat, nicht nur im Panel', done: false, wo: 'offen — diese Werkbank', beleg: 'fehlt: Abnahme der Inline-Fassung' },
]
const DONE = KRITERIEN.filter(k => k.done).length

/* ---------------------------------------------------------------- Gerüst */

function Head({ dark, setDark, label, sub }: { dark: boolean; setDark: (d: boolean) => void; label: string; sub: string }) {
  const P = dark ? DARK : LIGHT
  return (
    <div style={{ marginBottom: 10 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
        <div style={{ fontFamily: SANS, fontSize: 15, fontWeight: 600, letterSpacing: -0.2 }}>{label}</div>
        <span style={{ marginLeft: 'auto', display: 'inline-flex', border: `1px solid ${P.line}`, fontFamily: MONO, fontSize: TEXT.meta }}>
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
      <div style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, marginTop: 3, maxWidth: 660, lineHeight: 1.5 }}>{sub}</div>
    </div>
  )
}

/** Äußerer Rahmen: data-stage, Titelzeile und der Hell/Dunkel-Umschalter gehören der Variante. */
function StagePact({ label, sub, wide, full, dark0 = false, children }:
  { label: string; sub: string; wide?: boolean; full?: boolean; dark0?: boolean; children: (dark: boolean) => React.ReactNode }) {
  useBarrierefrei()
  const [dark, setDark] = useState(dark0)
  const P = dark ? DARK : LIGHT
  return (
    <div data-stage style={{ padding: 20, width: wide ? 900 : 800, boxSizing: 'border-box', background: P.bg,
                             color: P.ink, fontFamily: SANS, minHeight: full ? 620 : undefined }}>
      <Head dark={dark} setDark={setDark} label={label} sub={sub} />
      <div data-stagebody style={{ position: 'relative' }}>{children(dark)}</div>
    </div>
  )
}

/** Zustandszeile: Status IMMER Text UND Symbol (Produktregel, design: 68-80). */
const Status = ({ P, done, text }: { P: Pal; done: boolean; text: string }) => (
  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontFamily: MONO, fontSize: TEXT.meta, color: done ? P.ink : P.sub }}>
    {done
      ? <IconCheck size={10} stroke={2.6} color={P.ink} />
      : <span style={{ display: 'inline-block', width: 8, height: 8, border: `1px solid ${P.sub}` }} />}
    {text}
  </span>
)

const Abschnitt = ({ P, icon: I, text, note }: { P: Pal; icon: React.ComponentType<any>; text: string; note?: string }) => (
  <div style={{ display: 'flex', alignItems: 'center', gap: 6, margin: '9px 0 4px' }}>
    <I size={11} strokeWidth={2} color={P.faint} />
    <span style={{ fontFamily: MONO, fontSize: TEXT.meta, letterSpacing: 0.8, color: P.faint, textTransform: 'uppercase' }}>{text}</span>
    {note && <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>· {note}</span>}
  </div>
)

/** Einfeld-Eingabe: echte <input>-Zeile, nur Tinte und Haarlinie. */
const Zeile = ({ P, value, onChange, onEnter, placeholder }: any) => (
  <input value={value} onChange={e => onChange(e.target.value)} placeholder={placeholder}
    onKeyDown={e => { if (e.key === 'Enter' && onEnter) onEnter() }}
    style={{ flex: 1, minWidth: 0, background: 'transparent', border: 'none', borderBottom: `1px solid ${P.line}`,
             outline: 'none', fontFamily: MONO, fontSize: 11, color: P.ink, padding: '5px 2px' }} />
)

const Knopf = ({ P, prim, children, onClick, icon: I }: any) => (
  <motion.span onClick={onClick} whileHover={{ scale: 1.03 }} whileTap={{ scale: 0.96 }} transition={SPRING}
    style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontFamily: MONO, fontSize: TEXT.meta, padding: '5px 10px',
             border: `1px solid ${prim ? P.ink : P.line}`, background: prim ? P.ink : 'transparent',
             color: prim ? P.bg : P.ink, cursor: 'pointer' }}>
    {I && <I size={11} strokeWidth={2.2} />}{children}
  </motion.span>
)

/* ---------------------------------------------------------------- Bausteine des Pakts */

/** Kopflinie des Pakts: einklappbar, Zähler, Revision, Status. */
function PaktKopf({ P, open, onToggle, titel, zaehler, rev, status, tone }: any) {
  return (
    <motion.div onClick={onToggle} whileTap={{ scale: 0.995 }} transition={SPRING} data-paktkopf
      style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 11px', cursor: 'pointer', background: P.surface }}>
      <motion.span animate={{ rotate: open ? 0 : -90 }} transition={SPRING} style={{ display: 'inline-flex', color: P.sub }}>
        <IconChevronDown size={13} stroke={2.2} />
      </motion.span>
      <IconStack2 size={13} stroke={1.9} color={P.ink} />
      <span style={{ fontSize: 12.5, fontWeight: 600, color: P.ink }}>{titel}</span>
      <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub }}>{zaehler}</span>
      <span style={{ flex: 1 }} />
      <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>{rev}</span>
      {status && <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub }}>{status}</span>}
      <motion.span animate={{ opacity: [0.45, 1, 0.45] }} transition={{ duration: 2.4, repeat: Infinity, ease: 'easeInOut' }}>
        <IconGauge size={12} stroke={1.8} color={P.faint} />
      </motion.span>
    </motion.div>
  )
}

/** Ziele · Nicht-Ziele · Budget · Kriterien — der aufgeklappte Paktkörper. */
function PaktKoerper({ P, added }: { P: Pal; added: { id: string; t: string }[] }) {
  const alle = KRITERIEN.concat(added.map((a, i) => ({ id: `NEU-0${i + 1}`, t: a.t, done: false, wo: 'offen — gerade ergänzt', beleg: 'noch kein Nachweis' })))
  const fertig = alle.filter(k => k.done).length
  return (
    <div style={{ padding: '0 11px 11px', background: P.surface }}>
      <Abschnitt P={P} icon={IconTarget} text="Ziele" note="Elternpunkt erfüllt erst, wenn Pflichtkinder belegt sind" />
      {ZIELE.map((z, i) => (
        <motion.div key={z.id} data-ziel initial={{ opacity: 0, x: -5 }} animate={{ opacity: 1, x: 0 }}
          transition={{ ...SPRING, delay: i * 0.04 }}
          style={{ display: 'flex', alignItems: 'baseline', gap: 7, padding: '3px 0', borderBottom: `1px solid ${P.hair}` }}>
          <span style={{ width: 14, display: 'inline-flex', justifyContent: 'center', color: P.ink }}>
            {z.done ? <IconCheck size={11} stroke={2.6} /> : <span style={{ display: 'inline-block', width: 8, height: 8, border: `1px solid ${P.sub}` }} />}
          </span>
          <span style={{ fontSize: 11.5, color: P.ink }}>{z.t}</span>
          <span style={{ flex: 1, fontFamily: MONO, fontSize: TEXT.meta, color: P.faint, textAlign: 'right' }}>{z.sub}</span>
        </motion.div>
      ))}

      <Abschnitt P={P} icon={IconBan} text="Nicht-Ziele" note="ausdrücklich außen vor" />
      {NICHT_ZIELE.map((n, i) => (
        <motion.div key={n.t} data-nichtziel initial={{ opacity: 0, x: -5 }} animate={{ opacity: 1, x: 0 }}
          transition={{ ...SPRING, delay: 0.1 + i * 0.04 }} style={{ padding: '3px 0', borderBottom: `1px solid ${P.hair}` }}>
          <div style={{ position: 'relative', display: 'inline-block' }}>
            <span style={{ fontSize: 11.5, color: P.sub }}>{n.t}</span>
            <motion.span initial={{ width: 0 }} animate={{ width: '100%' }} transition={{ ...SPRING, delay: 0.28 + i * 0.05 }}
              style={{ position: 'absolute', left: 0, top: '53%', height: 1, background: P.faint }} />
          </div>
          <span style={{ marginLeft: 8, fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>{n.rest}</span>
        </motion.div>
      ))}

      <Abschnitt P={P} icon={IconClock} text="Budget" note="Zeit · Tokens · Kosten" />
      <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', padding: '2px 0 1px' }}>
        {BUDGET.map((b, i) => (
          <motion.span key={b.k} initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} transition={{ ...SPRING, delay: 0.18 + i * 0.04 }}
            style={{ display: 'inline-flex', alignItems: 'baseline', gap: 5, fontFamily: MONO, fontSize: TEXT.meta, color: P.sub }}>
            <span style={{ color: P.faint }}>{b.k}</span>
            <span style={{ color: P.ink }}>{b.wert}</span>
            <span style={{ color: P.faint }}>{b.pct} %</span>
          </motion.span>
        ))}
      </div>

      <Abschnitt P={P} icon={IconListCheck} text="Kriterien" note={`${fertig}/${alle.length} belegt — „gehört zum Auftrag" ist getrennt von „unabhängig geprüft"`} />
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0 16px' }}>
        {alle.map((k, i) => (
          <motion.div key={k.id} data-kriterium={k.id} initial={{ opacity: 0, y: 5 }} animate={{ opacity: 1, y: 0 }}
            transition={{ ...SPRING, delay: 0.22 + i * 0.03 }}
            style={{ display: 'flex', alignItems: 'baseline', gap: 6, padding: '3px 0', borderBottom: `1px solid ${P.hair}`, minWidth: 0 }}>
            <span style={{ width: 11, display: 'inline-flex', justifyContent: 'center', color: P.ink }}>
              {k.done ? <IconCheck size={10} stroke={2.6} /> : <span style={{ display: 'inline-block', width: 7, height: 7, border: `1px solid ${P.sub}` }} />}
            </span>
            <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint, width: 48, flex: '0 0 auto' }}>{k.id}</span>
            <span style={{ fontSize: 11, color: k.done ? P.sub : P.ink, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{k.t}</span>
          </motion.div>
        ))}
      </div>
      <div style={{ marginTop: 8, fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>
        offene Kriterien: {alle.filter(k => !k.done).map(k => k.id.split(' ')[0]).join(' · ') || 'keine'} — gespeichert bleibt Sache der Runtime
      </div>
    </div>
  )
}

/** Sprechblasen des Verlaufs, damit der Pakt wirklich IM Chat steht. */
const Blase = ({ P, dir, children, mono = true }: any) => (
  <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={SPRING}
    style={{ alignSelf: dir === 'out' ? 'flex-end' : 'flex-start', maxWidth: 430, fontFamily: mono ? MONO : SANS,
             fontSize: 11.5, background: dir === 'out' ? P.ink : P.surface, color: dir === 'out' ? P.bg : P.ink,
             border: dir === 'out' ? 'none' : `1px solid ${P.line}`, padding: '5px 10px', borderRadius: RADIUS.module }}>
    {children}
  </motion.div>
)

/* ================================================================ 01 — Pakt als Nachricht */

function InlinePakt({ P, dark, defaultOpen = false, kopf = true }: any) {
  const [open, setOpen] = useState(defaultOpen)
  const [added, setAdded] = useState<{ id: string; t: string }[]>([])
  return (
    <motion.div data-pakt initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={SPRING}
      style={{ border: `1px solid ${P.line}`, background: P.surface, borderRadius: RADIUS.module, overflow: 'hidden', width: '100%' }}>
      {kopf && (
        <PaktKopf P={P} open={open} onToggle={() => setOpen(o => !o)}
          titel="Ziel-Auftrag" zaehler={`${DONE}/${KRITERIEN.length + added.length} Kriterien`}
          rev="Pakt v4" status="gespeichert · Revision 4" />
      )}
      <AnimatePresence initial={false}>
        {open && (
          <motion.div key="koerper" data-paktkoerper initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }} transition={SPRING} style={{ overflow: 'hidden' }}>
            <div style={{ borderTop: `1px solid ${P.hair}` }} />
            <PaktKoerper P={P} added={added} />
          </motion.div>
        )}
      </AnimatePresence>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 11px', borderTop: `1px solid ${P.hair}`, background: P.surface }}>
        {open
          ? <Knopf P={P} onClick={() => setAdded(a => a.concat({ id: 'n' + (a.length + 1), t: 'Neues Kriterium ' + (a.length + 1) }))} icon={IconPlus}>Kriterium ergänzen</Knopf>
          : <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>aufgeklappt zeigt der Pakt Ziele, Nicht-Ziele, Budget und Kriterien</span>}
        <span style={{ flex: 1 }} />
        <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>{dark ? 'dunkel' : 'hell'} · inline im Verlauf, nicht im Panel</span>
      </div>
    </motion.div>
  )
}

function Fall01({ dark }: { dark: boolean }) {
  const P = dark ? DARK : LIGHT
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8, padding: 14, minHeight: 430 }}>
      <Blase P={P} dir="out">nimm die ui-abnahme als auftrag</Blase>
      <Blase P={P} dir="in">verstanden. ich schreibe den pakt als vertrag mit — ziele, nicht-ziele, budget, kriterien.</Blase>
      <InlinePakt P={P} dark={dark} defaultOpen />
      <Blase P={P} dir="in" mono={false}>Klick auf die Kopflinie klappt den Pakt ein — die Nachricht bleibt im Verlauf stehen.</Blase>
    </div>
  )
}

/* ================================================================ 02 — Pakt bestätigen */

function Fall02({ dark }: { dark: boolean }) {
  const P = dark ? DARK : LIGHT
  const [weg, setWeg] = useState<'offen' | 'annehmen' | 'aendern' | 'ablehnen'>('offen')
  const [grund, setGrund] = useState('')
  const [phase, setPhase] = useState<'keine' | 'eingereiht' | 'uebernommen'>('keine')
  useEffect(() => {
    if (weg !== 'annehmen') { setPhase('keine'); return }
    setPhase('eingereiht')
    const t = setTimeout(() => setPhase('uebernommen'), 1700)
    return () => clearTimeout(t)
  }, [weg])

  const WEGE = [
    { k: 'annehmen', icon: IconCheck, t: 'annehmen', d: 'Der Pakt gilt. Kriterien werden gegen ihn geprüft.' },
    { k: 'aendern', icon: IconPencil, t: 'ändern', d: 'Kriterium umformulieren, Reihenfolge oder Auswahl inline ändern.' },
    { k: 'ablehnen', icon: IconX, t: 'ablehnen', d: 'Ablehnung erhält die Arbeit und zeigt die erlaubten Alternativen.' },
  ] as const

  return (
    <div style={{ padding: 14, display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div style={{ border: `1.5px solid ${P.ink}`, background: P.surface }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 11px', borderBottom: `1px solid ${P.hair}` }}>
          <IconLock size={13} stroke={2} color={P.ink} />
          <span style={{ fontSize: 12.5, fontWeight: 600, color: P.ink }}>Pakt bestätigen</span>
          <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub }}>9 Kriterien · 3 Nicht-Ziele · Budget 90 min / 250k</span>
          <span style={{ flex: 1 }} />
          <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>nur eine autorisierte IconTarget-Änderung verändert Kriterien</span>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 1, background: P.hair }}>
          {WEGE.map(w => (
            <motion.div key={w.k} data-weg={w.k} onClick={() => setWeg(w.k)} whileTap={{ scale: 0.985 }} transition={SPRING}
              animate={{ background: weg === w.k ? P.soft : P.surface }}
              style={{ padding: '9px 10px', cursor: 'pointer' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <w.icon size={12} strokeWidth={2.2} color={weg === w.k ? P.ink : P.sub} />
                <span style={{ fontFamily: MONO, fontSize: TEXT.meta, fontWeight: 600, color: P.ink }}>{w.t}</span>
                <span style={{ flex: 1 }} />
                <motion.span animate={{ opacity: weg === w.k ? 1 : 0.2, scale: weg === w.k ? 1 : 0.8 }} transition={SPRING}
                  style={{ width: 6, height: 6, borderRadius: RADIUS.pill, background: P.ink }} />
              </div>
              <div style={{ marginTop: 4, fontSize: 11, lineHeight: 1.5, color: P.sub }}>{w.d}</div>
            </motion.div>
          ))}
        </div>
        <AnimatePresence initial={false}>
          {weg === 'ablehnen' && (
            <motion.div key="grund" data-grund initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }}
              exit={{ height: 0, opacity: 0 }} transition={SPRING} style={{ overflow: 'hidden' }}>
              <div style={{ padding: '9px 11px', borderTop: `1px solid ${P.hair}`, display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint, whiteSpace: 'nowrap' }}>Grund</span>
                <Zeile P={P} value={grund} onChange={setGrund} placeholder="z. B. Kriterium PX-06 gehört nicht in diesen Auftrag" />
                <Knopf P={P} prim icon={IconArrowRight}>senden</Knopf>
              </div>
              <div style={{ padding: '0 11px 9px', fontFamily: MONO, fontSize: TEXT.meta, color: P.sub }}>
                Es geht nichts verloren: der Pakt bleibt als Entwurf, die Läufe dazu bleiben stehen.
              </div>
            </motion.div>
          )}
        </AnimatePresence>
        <AnimatePresence>
          {phase !== 'keine' && (
            <motion.div key="phase" data-confirm initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }}
              exit={{ height: 0, opacity: 0 }} transition={SPRING} style={{ overflow: 'hidden' }}>
              <div style={{ padding: '9px 11px', borderTop: `1px solid ${P.hair}`, display: 'flex', alignItems: 'center', gap: 9 }}>
                {phase === 'eingereiht'
                  ? <><ThinkingOrb state="working" size={20} theme={dark ? 'dark' : 'light'} />
                      <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.ink }}>eingereiht</span>
                      <span style={{ fontSize: 11, color: P.sub }}>wird von der Runtime übernommen — noch keine Bestätigung.</span></>
                  : <><IconCheck size={13} stroke={2.6} color={P.ink} />
                      <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.ink }}>übernommen</span>
                      <span style={{ fontSize: 11, color: P.sub }}>von der Runtime bestätigt · Revision 4 ist gespeichert. Prüfung folgt getrennt.</span></>}
                <span style={{ flex: 1 }} />
                <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>
                  {phase === 'eingereiht' ? 'kein optimistisches Grün vor der Bestätigung' : 'gespeichert ≠ verifiziert'}
                </span>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
      <div style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>
        gewählt: {weg} {wert(weg, grund)}
      </div>
    </div>
  )
}
const wert = (weg: string, grund: string) => {
  if (weg === 'annehmen') return '— Pakt gilt, Prüfung startet gegen 9 Kriterien'
  if (weg === 'aendern') return '— Eingabezeile im Pakt wird editierbar, Reihenfolge und Auswahl bleiben inline'
  if (weg === 'ablehnen') return grund ? `— Grund: „${grund}"` : '— Grund fehlt noch'
  return '— noch nichts entschieden'
}

/* ================================================================ 03 — Kriterium ergänzen */

function Fall03({ dark }: { dark: boolean }) {
  const P = dark ? DARK : LIGHT
  const [neu, setNeu] = useState('')
  const [liste, setListe] = useState<string[]>([])
  const add = () => {
    const t = neu.trim()
    if (!t) return
    setListe(l => l.concat(t))
    setNeu('')
  }
  const total = KRITERIEN.length + liste.length
  return (
    <div style={{ padding: 14, display: 'flex', flexDirection: 'column', gap: 9 }}>
      <div style={{ border: `1px solid ${P.line}`, background: P.surface }}>
        <PaktKopf P={P} open onToggle={() => {}} titel="Ziel-Auftrag" zaehler={`${DONE}/${total} Kriterien`} rev="Pakt v4"
          status={liste.length ? 'gespeichert als Revision 5' : 'gespeichert · Revision 4'} />
        <div style={{ padding: '0 11px 10px' }}>
          <div style={{ fontFamily: MONO, fontSize: TEXT.meta, letterSpacing: 0.8, color: P.faint, textTransform: 'uppercase', margin: '7px 0 4px' }}>
            Kriterien ergänzen — inline, ohne Panel
          </div>
          {KRITERIEN.slice(0, 4).map(k => (
            <div key={k.id} style={{ display: 'flex', alignItems: 'baseline', gap: 6, padding: '3px 0', borderBottom: `1px solid ${P.hair}` }}>
              <IconCheck size={10} stroke={2.6} color={k.done ? P.ink : 'transparent'} style={{ visibility: k.done ? 'visible' : 'hidden' }} />
              <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint, width: 48 }}>{k.id}</span>
              <span style={{ fontSize: 11, color: P.ink }}>{k.t}</span>
            </div>
          ))}
          <AnimatePresence initial={false}>
            {liste.map((t, i) => (
              <motion.div key={t + i} data-neu initial={{ height: 0, opacity: 0, x: -8 }} animate={{ height: 22, opacity: 1, x: 0 }}
                exit={{ height: 0, opacity: 0 }} transition={SPRING} style={{ overflow: 'hidden' }}>
                <div style={{ display: 'flex', alignItems: 'baseline', gap: 6, borderBottom: `1px solid ${P.hair}` }}>
                  <span style={{ display: 'inline-flex', width: 10, justifyContent: 'center', color: P.ink }}><IconPlus size={9} stroke={2.6} /></span>
                  <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint, width: 48 }}>NEU-0{liste.length - i}</span>
                  <span style={{ fontSize: 11, color: P.ink }}>{t}</span>
                  <span style={{ marginLeft: 'auto', fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>gehört zum Auftrag · noch nicht unabhängig geprüft</span>
                </div>
              </motion.div>
            ))}
          </AnimatePresence>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 8 }}>
            <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint, whiteSpace: 'nowrap' }}>neues Kriterium</span>
            <Zeile P={P} value={neu} onChange={setNeu} onEnter={add} placeholder="Enter fügt es wirklich hinzu — z. B. „Pakt bleibt nach Reload gleich“" />
            <Knopf P={P} prim onClick={add} icon={IconPlus}>hinzufügen</Knopf>
          </div>
        </div>
      </div>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        <motion.span key={total} initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }} transition={SPRING}
          style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.ink }} data-zaehler>
          {DONE}/{total} Kriterien
        </motion.span>
        <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>
          der Zähler folgt dem Pakt, nicht einem festen Text · {liste.length ? `${liste.length} selbst ergänzt` : 'noch nichts ergänzt'}
        </span>
      </div>
    </div>
  )
}

/* ================================================================ 04 — Budget */

/** Balken läuft per animate() von 0 auf den Wert — bei jeder neuen Zahl neu. */
const BudgetBalken = ({ P, pct, warn }: { P: Pal; pct: number; warn: boolean }) => {
  const ref = useRef<HTMLSpanElement>(null)
  useEffect(() => {
    if (!ref.current) return
    ref.current.style.width = '0%'
    const c = animate(ref.current, { width: `${pct}%` }, { duration: 1.5, ease: 'easeInOut', delay: 0.25 })
    return () => c.stop()
  }, [pct])
  return (
    <span style={{ position: 'relative', display: 'block', flex: 1, height: 5, background: P.hair, border: `1px solid ${P.line}` }}>
      <span ref={ref} data-budgetbar style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: '0%', background: warn ? P.ink : P.sub }} />
      {/* Warnschwelle 80 % — sichtbar als gestrichelte Kante */}
      <motion.span initial={{ scaleY: 0 }} animate={{ scaleY: 1 }} transition={{ ...SPRING, delay: 0.1 }}
        style={{ position: 'absolute', left: '80%', top: -3, bottom: -3, borderLeft: `1px dashed ${P.faint}` }} />
    </span>
  )
}

function Fall04({ dark }: { dark: boolean }) {
  const P = dark ? DARK : LIGHT
  const [heute, setHeute] = useState(false)
  const b = heute ? BUDGET.map(x => ({ ...x, pct: x.k === 'Tokens' ? 93 : x.pct + 13, warn: x.k === 'Tokens' })) : BUDGET
  const over = b.some(x => x.pct >= 80)
  return (
    <div style={{ padding: 14, display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div style={{ border: `1px solid ${P.line}`, background: P.surface }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 11px', borderBottom: `1px solid ${P.hair}` }}>
          <IconClock size={13} stroke={2} color={P.ink} />
          <span style={{ fontSize: 12.5, fontWeight: 600, color: P.ink }}>Budget des Pakts</span>
          <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub }}>Zeit · Tokens · Kosten</span>
          <span style={{ flex: 1 }} />
          <motion.span animate={{ opacity: over ? [0.35, 1, 0.35] : 1 }} transition={{ duration: 1.8, repeat: over ? Infinity : 0, ease: 'easeInOut' }}
            style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontFamily: MONO, fontSize: TEXT.meta, color: P.ink }}>
            {over ? <><IconAlertCircle size={11} stroke={2.2} /> Warnschwelle erreicht — Tokens</> : <><IconCheck size={11} stroke={2.6} /> alles innerhalb</>}
          </motion.span>
        </div>
        <div style={{ padding: '10px 11px', display: 'flex', flexDirection: 'column', gap: 11 }}>
          {b.map((x, i) => (
            <motion.div key={x.k} data-budget={x.k} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }}
              transition={{ ...SPRING, delay: i * 0.05 }} style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, width: 74, fontFamily: MONO, fontSize: TEXT.meta, color: P.ink }}>
                {x.k === 'Zeit' ? <IconClock size={11} stroke={2} /> : x.k === 'Tokens' ? <IconFileText size={11} stroke={2} /> : <IconCoins size={11} stroke={2} />}
                {x.k}
              </span>
              <BudgetBalken P={P} pct={x.pct} warn={x.warn} />
              <span style={{ width: 46, textAlign: 'right', fontFamily: MONO, fontSize: TEXT.meta, color: x.warn ? P.ink : P.sub }}>{x.pct} %</span>
              <span style={{ width: 168, fontFamily: MONO, fontSize: TEXT.meta, color: P.sub }}>{x.wert}</span>
              <span style={{ width: 190, fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>{x.rest}</span>
            </motion.div>
          ))}
        </div>
        <div style={{ padding: '0 11px 10px', fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>
          Budget ist Teil des Vertrags: Überziehen heißt anhalten und neu verhandeln, nicht heimlich weiterlaufen.
        </div>
      </div>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        <Knopf P={P} onClick={() => setHeute(h => !h)} icon={IconGauge}>{heute ? 'späteren Stand zeigen' : 'späteren Stand zeigen (Tokens 93 %)'}</Knopf>
        <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>die Balken laufen bei jeder Änderung wieder von 0 auf den Wert</span>
      </div>
    </div>
  )
}

/* ================================================================ 05 — Nicht-Ziele */

function Fall05({ dark }: { dark: boolean }) {
  const P = dark ? DARK : LIGHT
  const [durch, setDurch] = useState(true)
  return (
    <div style={{ padding: 14, display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div style={{ border: `1px solid ${P.line}`, background: P.surface }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 11px', borderBottom: `1px solid ${P.hair}` }}>
          <IconBan size={13} stroke={2} color={P.ink} />
          <span style={{ fontSize: 12.5, fontWeight: 600, color: P.ink }}>Nicht-Ziele</span>
          <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub }}>ausdrücklich außen vor — der Pakt schützt auch davor</span>
          <span style={{ flex: 1 }} />
          <Knopf P={P} onClick={() => setDurch(d => !d)}>{durch ? 'ohne Durchstreichung' : 'Wirkung durchstreichen'}</Knopf>
        </div>
        <div style={{ padding: '8px 11px 10px', display: 'flex', flexDirection: 'column', gap: 12 }}>
          {NICHT_ZIELE.map((n, i) => (
            <motion.div key={n.t} data-nichtziel={i} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }}
              transition={{ ...SPRING, delay: i * 0.06 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint, width: 30 }}>N-0{i + 1}</span>
                <span style={{ fontSize: 12, fontWeight: 600, color: P.ink }}>{n.t}</span>
                <span style={{ flex: 1 }} />
                <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>{n.rest}</span>
              </div>
              <div style={{ position: 'relative', display: 'inline-block', marginLeft: 30, marginTop: 2, maxWidth: 520 }}>
                <span style={{ fontSize: 11, color: P.sub, lineHeight: 1.5 }}>{n.wirkung}</span>
                <motion.span data-strike animate={{ width: durch ? '100%' : '0%', opacity: durch ? 1 : 0 }}
                  transition={{ ...SPRING, delay: durch ? 0.2 + i * 0.06 : 0 }}
                  style={{ position: 'absolute', left: 0, top: '50%', height: 1, background: P.faint }} />
              </div>
            </motion.div>
          ))}
        </div>
        <div style={{ padding: '0 11px 10px', fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>
          Wird ein Nicht-Ziel doch gebraucht, ist es eine Änderung des Auftrags — nicht eine stille Erweiterung währenddessen.
        </div>
      </div>
    </div>
  )
}

/* ================================================================ 06 — Revisions-Konflikt */

function Fall06({ dark }: { dark: boolean }) {
  const P = dark ? DARK : LIGHT
  const [wahl, setWahl] = useState<'offen' | 'neu' | 'behalten'>('offen')
  return (
    <div style={{ padding: 14, display: 'flex', flexDirection: 'column', gap: 10 }}>
      <motion.div data-konflikt initial={{ opacity: 0, scale: 0.985 }} animate={{ opacity: 1, scale: 1 }} transition={SPRING}
        style={{ border: `1.5px solid ${P.ink}`, background: P.surface }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '9px 11px', borderBottom: `1px solid ${P.hair}` }}>
          <motion.span animate={{ opacity: [1, 0.35, 1] }} transition={{ duration: 1.7, repeat: Infinity, ease: 'easeInOut' }} style={{ display: 'inline-flex' }}>
            <IconAlertCircle size={14} stroke={2.2} color={P.ink} />
          </motion.span>
          <span style={{ fontSize: 12.5, fontWeight: 600, color: P.ink }}>Konflikt beim Speichern</span>
          <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.ink }}>erwartet Revision 4, gefunden 5</span>
          <span style={{ flex: 1 }} />
          <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>dein Entwurf ist erhalten — nichts wurde überschrieben</span>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 1, background: P.hair }}>
          <div style={{ background: P.surface, padding: '9px 11px' }}>
            <div style={{ fontFamily: MONO, fontSize: TEXT.meta, letterSpacing: 0.7, color: P.faint, textTransform: 'uppercase' }}>Aktuelle Fassung · Revision 5</div>
            <div style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint, margin: '3px 0 6px' }}>von der Runtime gehalten · 14:05</div>
            {[['VD-22', 'Pakt inline im Chat, nicht nur im Panel', '+'], ['PX-06', 'Kontrast dunkel 3:1 an Haarlinien', '+']].map(([id, t, z], i) => (
              <motion.div key={id} initial={{ opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }} transition={{ ...SPRING, delay: 0.1 + i * 0.06 }}
                style={{ display: 'flex', gap: 6, padding: '3px 0', borderBottom: `1px solid ${P.hair}` }}>
                <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.ink, width: 10 }}>{z}</span>
                <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint, width: 48 }}>{id}</span>
                <span style={{ fontSize: 11, color: P.ink }}>{t}</span>
              </motion.div>
            ))}
          </div>
          <div style={{ background: P.surface, padding: '9px 11px' }}>
            <div style={{ fontFamily: MONO, fontSize: TEXT.meta, letterSpacing: 0.7, color: P.faint, textTransform: 'uppercase' }}>Deine Änderung · auf Basis 4</div>
            <div style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint, margin: '3px 0 6px' }}>nur im Entwurf · 14:06</div>
            {[['S04', 'Endzustand ohne Orb und ohne Animation', '~'], ['NEU-01', 'Pakt bleibt nach Reload gleich', '+']].map(([id, t, z], i) => (
              <motion.div key={id} initial={{ opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }} transition={{ ...SPRING, delay: 0.16 + i * 0.06 }}
                style={{ display: 'flex', gap: 6, padding: '3px 0', borderBottom: `1px solid ${P.hair}` }}>
                <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.ink, width: 10 }}>{z}</span>
                <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint, width: 48 }}>{id}</span>
                <span style={{ fontSize: 11, color: P.ink }}>{t}</span>
              </motion.div>
            ))}
          </div>
        </div>
        <div style={{ padding: '9px 11px', borderTop: `1px solid ${P.hair}`, display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          {([['neu', IconRefresh, 'neu laden und neu entscheiden'], ['behalten', Layers, 'eigene Fassung behalten']] as const).map(([k, I, t]) => (
            <motion.span key={k} data-wahl={k} onClick={() => setWahl(k)} whileHover={{ scale: 1.03 }} whileTap={{ scale: 0.96 }} transition={SPRING}
              animate={{ borderColor: wahl === k ? P.ink : P.line, background: wahl === k ? P.soft : 'transparent' }}
              style={{ display: 'inline-flex', alignItems: 'center', gap: 5, border: `1px solid ${P.line}`, padding: '5px 10px',
                       fontFamily: MONO, fontSize: TEXT.meta, color: P.ink, cursor: 'pointer' }}>
              <I size={11} strokeWidth={2.2} />{t}
            </motion.span>
          ))}
          <span style={{ flex: 1 }} />
          <AnimatePresence mode="wait">
            <motion.span key={wahl} initial={{ opacity: 0, y: 3 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -3 }} transition={SPRING}
              style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub }}>
              {wahl === 'neu' ? 'neu laden holt Revision 5; deine Änderung geht dabei nicht verloren, sie liegt weiter im Entwurf'
                : wahl === 'behalten' ? 'eigene Fassung wird gegen Revision 5 gespeichert — Ergebnis: Revision 6 mit Herkunft „Konflikt:Auflösung"'
                : 'beide Fassungen stehen zum Abgleich — erst deine Entscheidung schreibt'}
            </motion.span>
          </AnimatePresence>
        </div>
      </motion.div>
      <div style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>
        Kein stilles Überschreiben, kein Ersatztext: gespeichert · übernommen · verifiziert kommen aus der Runtime.
      </div>
    </div>
  )
}

/* ================================================================ 07 — Nachweis je Kriterium */

function Fall07({ dark }: { dark: boolean }) {
  const P = dark ? DARK : LIGHT
  const [offenId, setOffenId] = useState<string | null>('S04')
  return (
    <div style={{ padding: 14, display: 'flex', flexDirection: 'column', gap: 9 }}>
      <div style={{ border: `1px solid ${P.line}`, background: P.surface }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 11px', borderBottom: `1px solid ${P.hair}` }}>
          <IconEye size={13} stroke={2} color={P.ink} />
          <span style={{ fontSize: 12.5, fontWeight: 600, color: P.ink }}>Nachweis je Kriterium</span>
          <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub }}>{DONE}/{KRITERIEN.length} belegt · {KRITERIEN.length - DONE} offen</span>
          <span style={{ flex: 1 }} />
          <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>Klick auf ein Kriterium zeigt den Beleg und seinen Ort</span>
        </div>
        {KRITERIEN.map((k, i) => {
          const on = offenId === k.id
          return (
            <div key={k.id} style={{ borderBottom: i === KRITERIEN.length - 1 ? 'none' : `1px solid ${P.hair}` }}>
              <motion.div data-nachweis={k.id} data-open={on ? 'true' : 'false'} onClick={() => setOffenId(on ? null : k.id)}
                initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ ...SPRING, delay: i * 0.04 }}
                whileTap={{ scale: 0.997 }} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '7px 11px', cursor: 'pointer' }}>
                <span style={{ width: 12, display: 'inline-flex', justifyContent: 'center', color: P.ink }}>
                  {k.done ? <IconCheck size={11} stroke={2.6} /> : <span style={{ display: 'inline-block', width: 8, height: 8, border: `1px solid ${P.sub}` }} />}
                </span>
                <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint, width: 44 }}>{k.id}</span>
                <span style={{ flex: 1, fontSize: 11.5, color: P.ink }}>{k.t}</span>
                <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint, width: 250, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{k.wo}</span>
                <motion.span animate={{ rotate: on ? 90 : 0 }} transition={SPRING} style={{ display: 'inline-flex', color: P.sub }}>
                  <IconChevronRight size={12} stroke={2} />
                </motion.span>
              </motion.div>
              <AnimatePresence initial={false}>
                {on && (
                  <motion.div key="beleg" data-beleg initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }}
                    exit={{ height: 0, opacity: 0 }} transition={SPRING} style={{ overflow: 'hidden' }}>
                    <div style={{ padding: '0 11px 10px 35px', display: 'flex', gap: 22, flexWrap: 'wrap' }}>
                      <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, minWidth: 300 }}>{k.beleg}</span>
                      <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>Ort: {k.wo}</span>
                      <Status P={P} done={k.done} text={k.done ? 'belegt' : 'offen'} />
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
          )
        })}
      </div>
      <div style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>
        „Gehört zum Auftrag" ist etwas anderes als „unabhängig geprüft" — belegt wird hier nur, was einen Ort hat.
      </div>
    </div>
  )
}

/* ================================================================ 08 — Pakt geändert */

const DIFF = [
  { z: '+', id: 'VD-22', t: 'Pakt inline im Chat, nicht nur im Panel', wer: 'du', zeit: '14:02' },
  { z: '+', id: 'PX-06', t: 'Kontrast dunkel 3:1 an Haarlinien', wer: 'Modell (Reasoning)', zeit: '14:05' },
  { z: '~', id: 'S04', t: 'Endzustand ohne Orb und ohne Animation', wer: 'Modell (Reasoning)', zeit: '14:05' },
  { z: '+', id: 'NEU-01', t: 'Pakt bleibt nach Reload gleich', wer: 'du', zeit: '14:07' },
  { z: '−', id: 'PX-09', t: 'Rohfehler auch im Chat sichtbar', wer: 'du', zeit: '14:07' },
]

function Fall08({ dark }: { dark: boolean }) {
  const P = dark ? DARK : LIGHT
  const [alle, setAlle] = useState(true)
  const zeigen = alle ? DIFF : DIFF.filter(d => d.wer === 'du')
  return (
    <div style={{ padding: 14, display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div style={{ border: `1px solid ${P.line}`, background: P.surface }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 11px', borderBottom: `1px solid ${P.hair}` }}>
          <IconHistory size={13} stroke={2} color={P.ink} />
          <span style={{ fontSize: 12.5, fontWeight: 600, color: P.ink }}>Pakt geändert</span>
          <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub }}>Revision 4 → 5 · 5 Unterschiede · wer hat geändert</span>
          <span style={{ flex: 1 }} />
          <Knopf P={P} onClick={() => setAlle(a => !a)} icon={IconEye}>{alle ? 'nur meine Zeilen' : 'alle Zeilen'}</Knopf>
        </div>
        <div style={{ padding: '8px 11px 10px' }}>
          <AnimatePresence initial={false}>
            {zeigen.map((d, i) => (
              <motion.div key={d.id + d.zeit} data-diff={d.id} layout
                initial={{ opacity: 0, x: -10 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -10 }}
                transition={{ ...SPRING, delay: i * 0.05 }}
                style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '4px 0', borderBottom: `1px solid ${P.hair}` }}>
                <span style={{ fontFamily: MONO, fontSize: 12, width: 12, textAlign: 'center', color: P.ink }}>{d.z}</span>
                <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint, width: 52 }}>{d.id}</span>
                <span style={{ flex: 1, fontSize: 11.5, color: d.z === '−' ? P.faint : P.ink, textDecoration: d.z === '−' ? 'line-through' : 'none' }}>{d.t}</span>
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontFamily: MONO, fontSize: TEXT.meta, color: P.sub }}>
                  {d.wer === 'du' ? <IconPencil size={10} stroke={2} /> : <IconStack2 size={10} stroke={2} />}
                  {d.wer}
                </span>
                <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint, width: 40, textAlign: 'right' }}>{d.zeit}</span>
              </motion.div>
            ))}
          </AnimatePresence>
        </div>
        <div style={{ padding: '0 11px 10px', display: 'flex', alignItems: 'center', gap: 8, fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>
          <motion.span key={zeigen.length} initial={{ opacity: 0, y: -3 }} animate={{ opacity: 1, y: 0 }} transition={SPRING} style={{ color: P.ink }}>
            {zeigen.length} Zeilen
          </motion.span>
          <span>· + hinzugefügt · ~ umformuliert · − entfernt · Kriterien ändert nur eine autorisierte IconTarget-Änderung</span>
        </div>
      </div>
    </div>
  )
}

/* ================================================================ 09 — sehr kompakt */

function Fall09({ dark }: { dark: boolean }) {
  const P = dark ? DARK : LIGHT
  const [open, setOpen] = useState(false)
  return (
    <div style={{ padding: 14, display: 'flex', flexDirection: 'column', gap: 9 }}>
      <div style={{ border: `1px solid ${P.line}`, background: P.surface, borderRadius: RADIUS.module, overflow: 'hidden', maxWidth: 560 }}>
        <motion.div data-slimpakt onClick={() => setOpen(o => !o)} whileHover={{ scale: 1.006 }} whileTap={{ scale: 0.995 }} transition={SPRING}
          style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 12px', cursor: 'pointer' }}>
          <motion.span animate={{ rotate: open ? 0 : -90 }} transition={SPRING} style={{ display: 'inline-flex', color: P.sub }}>
            <IconChevronDown size={13} stroke={2.2} />
          </motion.span>
          <span style={{ fontFamily: MONO, fontSize: 11.5, fontWeight: 600, color: P.ink }}>Pakt v4 · {DONE}/{KRITERIEN.length} ✓</span>
          <span style={{ flex: 1 }} />
          <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>Zeit 47 % · Tokens 79 %</span>
          <IconCheck size={12} stroke={2.6} color={P.ink} />
        </motion.div>
        <AnimatePresence initial={false}>
          {open && (
            <motion.div key="mini" data-slimbody initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }}
              exit={{ height: 0, opacity: 0 }} transition={SPRING} style={{ overflow: 'hidden' }}>
              <div style={{ borderTop: `1px solid ${P.hair}`, padding: '7px 12px 9px', display: 'flex', flexDirection: 'column', gap: 3 }}>
                {[
                  ['Ziele', '4 — 3 erfüllt, 1 offen (Zustände bleiben erhalten)'],
                  ['Nicht-Ziele', '3 — Designsystem, Mobil < 900 px, Voice-Umbau: bleiben außen vor'],
                  ['Budget', 'Zeit 42/90 min · Tokens 198k/250k · 0,84 € von 2,00 €'],
                  ['Kriterien', `${DONE} belegt, ${KRITERIEN.length - DONE} offen (PX-04 · PX-06 · VD-22)`],
                ].map(([k, v], i) => (
                  <motion.div key={k} initial={{ opacity: 0, x: -4 }} animate={{ opacity: 1, x: 0 }} transition={{ ...SPRING, delay: 0.04 + i * 0.05 }}
                    style={{ display: 'flex', gap: 10, fontFamily: MONO, fontSize: TEXT.meta }}>
                    <span style={{ color: P.faint, width: 82 }}>{k}</span>
                    <span style={{ color: P.sub, flex: 1 }}>{v}</span>
                  </motion.div>
                ))}
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
      <div style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>
        Schlankste Fassung: eine Zeile im Verlauf, alles Weitere erst auf Klick — dieselben Daten wie im großen Pakt.
      </div>
    </div>
  )
}

/* ================================================================ 10 — dunkler Favorit */

function Fall10({ dark }: { dark: boolean }) {
  const P = dark ? DARK : LIGHT
  return (
    <div style={{ display: 'flex', gap: 14, padding: 14, minHeight: 470, background: P.bg }}>
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 8 }}>
        <Blase P={P} dir="out">nimm die ui-abnahme als auftrag</Blase>
        <Blase P={P} dir="in">verstanden. ich schreibe den pakt als vertrag mit — ziele, nicht-ziele, budget, kriterien.</Blase>
        <InlinePakt P={P} dark={dark} defaultOpen />
      </div>
      <div style={{ width: 250, borderLeft: `1px solid ${P.hair}`, paddingLeft: 14, display: 'flex', flexDirection: 'column', gap: 10 }}>
        <div>
          <div style={{ fontFamily: MONO, fontSize: TEXT.meta, letterSpacing: 0.8, color: P.faint, textTransform: 'uppercase' }}>Warum inline</div>
          <div style={{ marginTop: 6, fontSize: 11.5, lineHeight: 1.6, color: P.sub }}>
            Der Pakt liegt im Verlauf, nicht in einem Panel: er bleibt lesbar, wenn das Fenster schmal ist, und er scrollt mit der Antwort mit.
            Der Ziel-Baum im Panel bleibt der zweite Blick.
          </div>
        </div>
        <div>
          <div style={{ fontFamily: MONO, fontSize: TEXT.meta, letterSpacing: 0.8, color: P.faint, textTransform: 'uppercase' }}>Regeln</div>
          <div style={{ marginTop: 6, display: 'flex', flexDirection: 'column', gap: 5 }}>
            {['aufgeklappt: Ziel, Kriterien, offene Fragen',
              'hinzufügen · umformulieren · Reihenfolge × inline',
              'gespeichert ≠ übernommen ≠ verifiziert',
              'Elternpunkt erst erfüllt, wenn Kinder belegt',
              'Konflikt: beide Fassungen zeigen, nichts überschreiben'].map((t, i) => (
              <motion.div key={t} initial={{ opacity: 0, x: 6 }} animate={{ opacity: 1, x: 0 }} transition={{ ...SPRING, delay: 0.12 + i * 0.06 }}
                style={{ display: 'flex', gap: 6, fontFamily: MONO, fontSize: TEXT.meta, color: P.sub }}>
                <span style={{ color: P.faint }}>·</span>{t}
              </motion.div>
            ))}
          </div>
        </div>
        <span style={{ flex: 1 }} />
        <div style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>Favorit: die Pakt-Nachricht aus Variante 01, hier als ganze Seite dunkel.</div>
      </div>
    </div>
  )
}

/* ================================================================ Werkbank */

export default {
  '01 · Pakt als einklappbare Nachricht im Verlauf': () => (
    <StagePact label="01 · Pakt im Verlauf" wide
      sub={'Ziel-Auftrag als Nachricht im Chat: Kopf „6/9 Kriterien“, aufgeklappt Ziele · Nicht-Ziele · Budget · Kriterien — auf Klick fährt der Körper mit der Feder ein.'}>
      {dark => <Fall01 dark={dark} />}
    </StagePact>
  ),

  '02 · Pakt bestätigen: annehmen · ändern · ablehnen': () => (
    <StagePact label="02 · Pakt bestätigen" wide
      sub={'Drei Wege mit Grundfeld. „annehmen“ zeigt erst „eingereiht“, dann erst die Runtime-Bestätigung — kein optimistisches Grün vor der Übernahme.'}>
      {dark => <Fall02 dark={dark} />}
    </StagePact>
  ),

  '03 · Kriterium ergänzen (wirkt wirklich)': () => (
    <StagePact label="03 · Kriterium ergänzen" wide
      sub={'Eingabezeile im Pakt statt im Panel: Enter oder „hinzufügen“ hängt ein echtes Kriterium an — Zähler und Liste folgen (useState).'}>
      {dark => <Fall03 dark={dark} />}
    </StagePact>
  ),

  '04 · Budget: Zeit, Tokens, Kosten mit Warnschwelle': () => (
    <StagePact label="04 · Budget" wide
      sub={'Drei Balken laufen mit animate() von 0 auf den Wert, die Warnschwelle 80 % steht als gestrichelte Kante; der Umschalter zeigt den überzogenen Stand.'}>
      {dark => <Fall04 dark={dark} />}
    </StagePact>
  ),

  '05 · Nicht-Ziele ausdrücklich (bleibt außen vor)': () => (
    <StagePact label="05 · Nicht-Ziele" wide
      sub={'Jedes Nicht-Ziel mit durchgestrichener Wirkung: die Linie wächst mit der Feder über den Text; der Umschalter nimmt sie wieder weg.'}>
      {dark => <Fall05 dark={dark} />}
    </StagePact>
  ),

  '06 · Revisions-Konflikt: erwartet 4, gefunden 5': () => (
    <StagePact label="06 · Revisions-Konflikt" wide
      sub={'Beide Fassungen stehen zum Abgleich, der Entwurf bleibt erhalten; zwei Wege — neu laden oder eigene Fassung behalten — mit sichtbarer Folge.'}>
      {dark => <Fall06 dark={dark} />}
    </StagePact>
  ),

  '07 · Nachweis je Kriterium mit Beleg-Ort und ✓': () => (
    <StagePact label="07 · Nachweis je Kriterium" wide
      sub={'Kriterien einzeln aufklappbar: Beleg, Ort und Status „belegt / offen“ — „gehört zum Auftrag“ bleibt getrennt von „unabhängig geprüft“.'}
    >
      {dark => <Fall07 dark={dark} />}
    </StagePact>
  ),

  '08 · Pakt geändert: Diff mit + und − und Urheber': () => (
    <StagePact label="08 · Pakt geändert" wide
      sub={'Was sich geändert hat und wer es geändert hat: + hinzugefügt, ~ umformuliert, − entfernt, je Zeile Urheber und Zeitpunkt; Filter auf eigene Zeilen.'}
    >
      {dark => <Fall08 dark={dark} />}
    </StagePact>
  ),

  '09 · sehr kompakt: eine Zeile „Pakt v4 · 6/9 ✓"': () => (
    <StagePact label="09 · sehr kompakt" wide
      sub={'Schlankste Fassung für den Verlauf: eine Zeile mit Revision und Zähler, die auf Klick die vier Zeilen Ziele · Nicht-Ziele · Budget · Kriterien zeigt.'}>
      {dark => <Fall09 dark={dark} />}
    </StagePact>
  ),

  '10 · dunkle Fassung des Favoriten (Vollbild dunkel)': () => (
    <StagePact label="10 · Favorit — dunkel" wide full dark0
      sub={'Variante 01 als ganze Seite im dunklen Thema: identische Nachricht, gleiche Federn, invertierte Tinte — nur Schwarz/Weiß/Grau.'}>
      {dark => <Fall10 dark={dark} />}
    </StagePact>
  ),
} as const
