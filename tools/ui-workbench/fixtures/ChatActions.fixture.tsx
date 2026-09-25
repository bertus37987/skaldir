/**
 * Chat-Aktionen — Memory · Skills · Analyse · Jobs · Werkzeug suchen.
 *
 * Heute leben diese fuenf Dinge in Panels und in der CLI; die Designvorgabe ordnet
 * sie dem Chat (Trefferlisten, Vorschlaege, Kontextmenue) und dem Composer
 * (Aktionsleiste, Schraegstrich-Menue) zu. Genau das zeigen die zehn Varianten.
 *
 * Der Zustand ist echt (useState): das Schraegstrich-Menue oeffnet und schliesst und
 * laesst sich mit den Pfeiltasten bedienen, die Auswahl wirkt, Beleg-Haeufigkeiten
 * zaehlen sichtbar hoch, Jobs laufen mit Sekundenzaehler und Orb, Werkzeuge werden
 * angehaengt und wieder geleert.
 *
 * Nur Schwarz/Weiss/Grau (Palette aus ./_tokens), Symbole aus @tabler/icons-react, ThinkingOrb
 * nur size 20/64 und nur die neun gueltigen Zustaende (Suche = searching).
 * Reduced Motion und Tastaturbedienung: useBarrierefrei() auf jeder Buehne (A1/A5).
 */
import { motion, AnimatePresence, animate } from 'motion/react'
import React, { useState, useRef, useEffect } from 'react'
import { IconBookmark, IconBrain, IconCalendarClock, IconChartBar, IconCheck, IconChevronRight, IconClock, IconCornerDownLeft, IconDots, IconFileText, IconHistory, IconPlayerPlay, IconPlug, IconPuzzle, IconRefresh, IconRepeat, IconSearch, IconServer, IconSparkles, IconTerminal, IconTool, IconTrash, IconX } from '@tabler/icons-react'

// @ts-expect-error — Laufzeit-Alias (siehe vite.config.ts)
import { ThinkingOrb } from 'thinking-orbs'
import { LIGHT, DARK, MONO, SANS, SPRING, RADIUS, TEXT } from './_tokens'
import { useBarrierefrei } from './_barrierefrei'

/* Farben, Schriften, Federn, Radien und Schriftgroessen: ./_tokens (eine Wahrheit) */

/* Die fuenf Aktionen — genau die, keine anderen. */
const ACTIONS = [
  { id: 'memory', label: 'Memory', short: 'MEM', slash: '/mem', desc: 'Gemerktes abfragen und ablegen', Icon: IconBrain },
  { id: 'skills', label: 'Skills', short: 'SKL', slash: '/skill', desc: 'Skill anlegen oder aufrufen', Icon: IconSparkles },
  { id: 'analyse', label: 'Analyse', short: 'ANL', slash: '/analyse', desc: 'Laufende Sitzung auswerten', Icon: IconChartBar },
  { id: 'jobs', label: 'Jobs', short: 'JOB', slash: '/job', desc: 'Geplante Aufgaben verwalten', Icon: IconCalendarClock },
  { id: 'werkzeug', label: 'Werkzeug suchen', short: 'WZG', slash: '/werkzeug', desc: 'MCP, CLI und eigene Werkzeuge', Icon: IconTool }
]

/* ---------------------------------------------------------------- Buehne */

const StageHead = ({ name, note, dark, setDark, toggleable, P }) => (
  <div style={{ marginBottom: 10 }}>
    <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
      <span style={{ fontFamily: MONO, fontSize: 12, fontWeight: 600, color: P.ink }}>{name}</span>
      {toggleable && (
        <span style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 6, fontFamily: MONO, fontSize: 11 }}>
          <motion.span data-themelight whileTap={{ scale: 0.94 }} transition={SPRING} onClick={() => setDark(false)}
            animate={{ borderColor: !dark ? P.ink : P.line, backgroundColor: !dark ? P.ink : P.surface, color: !dark ? P.bg : P.ink }}
            style={{ padding: '4px 10px', border: `1px solid ${P.line}`, cursor: 'pointer' }}>hell</motion.span>
          <motion.span data-themedark whileTap={{ scale: 0.94 }} transition={SPRING} onClick={() => setDark(true)}
            animate={{ borderColor: dark ? P.ink : P.line, backgroundColor: dark ? P.ink : P.surface, color: dark ? P.bg : P.ink }}
            style={{ padding: '4px 10px', border: `1px solid ${P.line}`, cursor: 'pointer' }}>dunkel</motion.span>
        </span>
      )}
    </div>
    <div style={{ fontFamily: MONO, fontSize: 11, color: P.sub, marginTop: 3 }}>{note}</div>
  </div>
)

/** Buehne mit hell/dunkel-Umschalter (Varianten 01-09). */
function Managed({ name, note, render, dark0 = false, toggleable = true }) {
  useBarrierefrei()   // A1 reduzierte Bewegung, A5 Tastatur (Befund 1+2)
  const [dark, setDark] = useState(dark0)
  const P = dark ? DARK : LIGHT
  return (
    <div data-stage style={{ padding: 20, background: P.bg, fontFamily: SANS, color: P.ink, width: '100%', maxWidth: 880, boxSizing: 'border-box' }}>
      <StageHead name={name} note={note} dark={dark} setDark={setDark} toggleable={toggleable} P={P} />
      {render(P, dark)}
    </div>
  )
}

/* ---------------------------------------------------------------- Bausteine */

/** Orb nur size 20/64, Thema passend zur Buehne. */
const Orb = ({ state, dark, size = 20 }) => <ThinkingOrb state={state} size={size} theme={dark ? 'dark' : 'light'} />

/** Schalter: Kapsel faerbt weich um, Knopf faehrt mit Feder auf die andere Seite. */
const Switch = ({ value, onChange, P, mini = false }) => (
  <motion.span data-switch data-checked={value ? 'true' : 'false'} role="switch" aria-checked={value ? 'true' : 'false'}
    onClick={(e) => { e.stopPropagation(); onChange(!value) }} whileTap={{ scale: 0.9 }} transition={SPRING}
    animate={{ backgroundColor: value ? P.ink : P.surface, borderColor: value ? P.ink : P.line }}
    style={{ display: 'inline-flex', alignItems: 'center', justifyContent: value ? 'flex-end' : 'flex-start',
      width: mini ? 30 : 34, height: mini ? 17 : 19, borderRadius: RADIUS.pill, cursor: 'pointer', padding: 2,
      boxSizing: 'border-box', border: `1px solid ${P.line}`, flex: '0 0 auto' }}>
    <motion.span layout transition={SPRING} style={{ width: mini ? 11 : 13, height: mini ? 11 : 13, borderRadius: RADIUS.pill, background: value ? P.surface : P.faint }} />
  </motion.span>
)

/** Zahl, die beim Wechsel mit Feder springt. */
const Count = ({ value, P }) => (
  <span style={{ display: 'inline-flex', fontFamily: MONO, fontSize: 11, color: P.ink, whiteSpace: 'nowrap' }}>
    <AnimatePresence initial={false}>
      <motion.span key={String(value)} initial={{ opacity: 0, y: -7 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 7 }} transition={SPRING}>{value}</motion.span>
    </AnimatePresence>
  </span>
)

/** Zahl, die mit Motion von 0 auf den Wert laeuft (Analyse-Kennzahlen). */
const Counter = ({ to, P, ms = 1100, size = 21 }) => {
  const ref = useRef(null)
  useEffect(() => {
    if (!ref.current) return
    const c = animate(0, to, {
      duration: ms / 1000, ease: 'easeOut',
      onUpdate: v => { if (ref.current) ref.current.textContent = Math.round(v).toLocaleString('de-DE') }
    })
    return () => c.stop()
  }, [to, ms])
  return <span ref={ref} data-counter style={{ fontFamily: MONO, fontSize: size, fontWeight: 600, color: P.ink }}>0</span>
}

/** Mini-Balken, laeuft mit Feder von 0 auf den Wert. */
const Bar = ({ pct, P, i = 0, hoch = false }) => (
  <span style={{ display: hoch ? 'block' : 'inline-block', width: hoch ? '100%' : 74, height: 5, background: P.hair, border: `1px solid ${P.line}`, boxSizing: 'border-box' }}>
    <motion.span data-bar initial={{ width: 0 }} animate={{ width: `${pct}%` }} transition={{ ...SPRING, delay: 0.08 + i * 0.06 }}
      style={{ display: 'block', height: '100%', background: P.ink }} />
  </span>
)

const Tag = ({ children, P, aktiv = false }) => (
  <span style={{ fontFamily: MONO, fontSize: 11, letterSpacing: 0.6, padding: '3px 8px', border: `1px solid ${aktiv ? P.ink : P.line}`,
    background: aktiv ? P.ink : 'transparent', color: aktiv ? P.bg : P.sub, whiteSpace: 'nowrap' }}>{children}</span>
)

const Btn = ({ children, P, onClick, primary = false, i = 0, Icon }) => (
  <motion.span data-btn onClick={onClick} initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} transition={{ ...SPRING, delay: i * 0.05 }}
    whileHover={{ scale: 1.03 }} whileTap={{ scale: 0.95 }}
    style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontFamily: MONO, fontSize: 11, padding: '5px 10px',
      border: `1px solid ${primary ? P.ink : P.line}`, background: primary ? P.ink : 'transparent', color: primary ? P.bg : P.ink, cursor: 'pointer', whiteSpace: 'nowrap' }}>
    {Icon && <Icon size={11} stroke={2} />}{children}
  </motion.span>
)

const ActionChip = ({ a, active, onClick, P, i = 0, kompakt = false }) => (
  <motion.span data-action={a.id} data-active={active ? 'true' : 'false'} onClick={onClick} title={a.desc}
    initial={{ opacity: 0, y: 5 }} animate={{ opacity: 1, y: 0, backgroundColor: active ? P.ink : P.surface, borderColor: active ? P.ink : P.line, color: active ? P.bg : P.sub }}
    transition={{ ...SPRING, delay: i * 0.04 }} whileHover={{ scale: 1.04 }} whileTap={{ scale: 0.93 }}
    style={{ display: 'inline-flex', alignItems: 'center', gap: 5, padding: kompakt ? '3px 8px' : '4px 10px', border: `1px solid ${P.line}`,
      borderRadius: RADIUS.pill, cursor: 'pointer', fontFamily: MONO, fontSize: 11, userSelect: 'none', whiteSpace: 'nowrap' }}>
    <a.Icon size={11} strokeWidth={2} />
    <span>{kompakt ? a.short : a.label}</span>
  </motion.span>
)

const Talk = ({ P, children }) => (
  <div style={{ flex: 1, padding: 14, display: 'flex', flexDirection: 'column', gap: 8, justifyContent: 'flex-end', overflow: 'hidden' }}>{children}</div>
)
const SayUser = ({ P, children }) => (
  <motion.span initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={SPRING}
    style={{ alignSelf: 'flex-end', fontFamily: MONO, fontSize: 11.5, background: P.ink, color: P.bg, padding: '5px 9px', borderRadius: RADIUS.pill, maxWidth: 420 }}>{children}</motion.span>
)
const SayBot = ({ P, children, i = 0 }) => (
  <motion.span initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ ...SPRING, delay: i * 0.06 }}
    style={{ alignSelf: 'flex-start', maxWidth: 470, fontFamily: MONO, fontSize: 11.5, background: P.surface, border: `1px solid ${P.line}`, color: P.ink, padding: '6px 10px', borderRadius: RADIUS.module }}>{children}</motion.span>
)
const Frame = ({ P, children, hoehe = 430 }) => (
  <div data-frame style={{ height: hoehe, border: `1px solid ${P.line}`, background: P.bg, display: 'flex', flexDirection: 'column', overflow: 'hidden', position: 'relative' }}>{children}</div>
)

/* ================================================================ 01 · Aktionsleiste am Composer */

const PREVIEW = {
  memory: { kopf: '12 Eintraege im Memory', zeilen: ['Overlay-Lint nur mit --strict · vor 2 min', 'Anbieter-Limit 5 MB je Datei · vor 1 h', 'Nie force-push auf main · vor 3 d'] },
  skills: { kopf: '3 Skills angelegt', zeilen: ['overlay-lint · zuletzt 17.09. genutzt', 'abnahme-lauf · 6 Aufrufe', 'bericht-bauen · ruht'] },
  analyse: { kopf: 'Sitzung 18.09. 09:12–09:54', zeilen: ['42 min · 128.400 Tokens', '37 Werkzeugaufrufe · 2 Fehler', '3 Subagenten · 2 Nachfragen'] },
  jobs: { kopf: '3 Aufgaben geplant', zeilen: ['Nachtlauf: Regression · heute 23:30', 'Wochenbericht · Mo 09:00', 'Aufraeumen Uploads · tgl. 04:00 (aus)'] },
  werkzeug: { kopf: '5 Treffer in MCP, CLI und eigenen Werkzeugen', zeilen: ['web_search · MCP', 'overlay_lint · eigenes', 'date · CLI', '+ 2 weitere'] }
}

function Bar01({ P }) {
  const [sel, setSel] = useState('memory')
  const a = ACTIONS.find(x => x.id === sel)
  const pv = PREVIEW[sel]
  return (
    <Frame P={P}>
      <Talk P={P}>
        <SayUser P={P}>wo steht das mit dem overlay-lint?</SayUser>
        <SayBot P={P}>drei Stellen — ich zeig sie dir als Treffer.</SayBot>
      </Talk>
      <div style={{ padding: 10 }}>
        <AnimatePresence initial={false}>
          <motion.div key={sel} data-preview={sel} initial={{ opacity: 0, y: 10, height: 0 }} animate={{ opacity: 1, y: 0, height: 'auto' }} exit={{ opacity: 0, y: 6, height: 0 }}
            transition={SPRING} style={{ overflow: 'hidden' }}>
            <div style={{ margin: '0 4px 9px', border: `1px solid ${P.line}`, background: P.surface, padding: '8px 10px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
                <a.Icon size={13} strokeWidth={1.8} color={P.ink} />
                <span style={{ fontFamily: MONO, fontSize: 11, fontWeight: 600, color: P.ink }}>{pv.kopf}</span>
                <span style={{ marginLeft: 'auto', fontFamily: MONO, fontSize: 11, color: P.faint }}>{a.slash} · {a.label}</span>
              </div>
              <div style={{ marginTop: 6, display: 'flex', flexDirection: 'column', gap: 3 }}>
                {pv.zeilen.map((z, i) => (
                  <motion.span key={z} initial={{ opacity: 0, x: -5 }} animate={{ opacity: 1, x: 0 }} transition={{ ...SPRING, delay: 0.05 + i * 0.05 }}
                    style={{ fontFamily: MONO, fontSize: 11, color: P.sub }}>· {z}</motion.span>
                ))}
              </div>
            </div>
          </motion.div>
        </AnimatePresence>
        <div data-composer style={{ borderRadius: RADIUS.bar, border: `1px solid ${P.line}`, background: P.surface, padding: '8px 10px 6px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
            {ACTIONS.map((x, i) => <ActionChip key={x.id} a={x} P={P} i={i} active={x.id === sel} onClick={() => setSel(x.id)} />)}
            <span style={{ marginLeft: 'auto', fontFamily: MONO, fontSize: 11, color: P.faint }}>{a.slash}</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '9px 6px 2px' }}>
            <span style={{ fontFamily: SANS, fontSize: 14, color: P.faint, flex: 1 }}>Start with a goal</span>
            <motion.span whileHover={{ scale: 1.06 }} whileTap={{ scale: 0.94 }} transition={SPRING} style={{ display: 'inline-flex', color: P.ink }}>
              <IconCornerDownLeft size={13} stroke={1.9} />
            </motion.span>
          </div>
        </div>
      </div>
    </Frame>
  )
}

export function V01() {
  return <Managed name="01 · Aktionsleiste am Composer" note="fuenf Kuerzel in der Pille — Klick waehlt, der Vorschlag faehrt darueber auf" render={(P) => <Bar01 P={P} />} />
}

/* ================================================================ 02 · Schraegstrich-Menue */

function Slash02({ P }) {
  const [text, setText] = useState('/')
  const [cur, setCur] = useState(0)
  const [eingefuegt, setEingefuegt] = useState(null)
  const offen = text.startsWith('/')
  const q = text.slice(1).toLowerCase()
  const treffer = ACTIONS.filter(a => a.slash.slice(1).includes(q) || a.label.toLowerCase().includes(q))
  const einfuegen = (a) => { setEingefuegt(a.label); setText(''); setCur(0) }
  const taste = (e) => {
    if (!offen) return
    if (e.key === 'ArrowDown') { e.preventDefault(); setCur(c => Math.min(c + 1, Math.max(0, treffer.length - 1))) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setCur(c => Math.max(0, c - 1)) }
    else if (e.key === 'Enter' && treffer[cur]) { e.preventDefault(); einfuegen(treffer[cur]) }
    else if (e.key === 'Escape') { e.preventDefault(); setText('') }
  }
  return (
    <Frame P={P}>
      <Talk P={P}>
        <SayBot P={P} i={0}>tippe <b>/</b> im Eingabefeld, dann Pfeiltasten und Enter.</SayBot>
      </Talk>
      <div style={{ padding: 10 }}>
        <AnimatePresence>
          {offen && (
            <motion.div data-slashmenu key="menu" initial={{ opacity: 0, y: 10, scale: 0.98 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, y: 6, scale: 0.98 }}
              transition={SPRING} style={{ marginBottom: 9, border: `1px solid ${P.line}`, background: P.surface, boxShadow: '0 12px 28px rgba(0,0,0,0.16)', transformOrigin: 'bottom left' }}>
              <div style={{ padding: '6px 10px', borderBottom: `1px solid ${P.hair}`, fontFamily: MONO, fontSize: 11, letterSpacing: 1, color: P.sub }}>AKTIONEN · {treffer.length} TREFFER</div>
              {treffer.map((a, i) => (
                <motion.div key={a.id} data-slashrow={a.id} data-active={i === cur ? 'true' : 'false'}
                  initial={{ opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0, backgroundColor: i === cur ? P.hair : 'rgba(0,0,0,0)' }} transition={{ ...SPRING, delay: i * 0.04 }}
                  onMouseEnter={() => setCur(i)} onClick={() => einfuegen(a)}
                  style={{ display: 'flex', alignItems: 'center', gap: 9, padding: '6px 10px', cursor: 'pointer' }}>
                  <a.Icon size={13} strokeWidth={1.8} color={P.ink} />
                  <span style={{ fontFamily: MONO, fontSize: 11, color: P.ink, width: 74 }}>{a.slash}</span>
                  <span style={{ fontFamily: MONO, fontSize: 11, color: P.sub, width: 118 }}>{a.label}</span>
                  <span style={{ fontFamily: MONO, fontSize: 11, color: P.faint }}>{a.desc}</span>
                  <span style={{ marginLeft: 'auto', display: 'inline-flex' }}>
                    <AnimatePresence>{i === cur && <motion.span key="caret" initial={{ opacity: 0, x: -4 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0 }} transition={SPRING} style={{ display: 'inline-flex' }}><IconCornerDownLeft size={11} stroke={2} color={P.sub} /></motion.span>}</AnimatePresence>
                  </span>
                </motion.div>
              ))}
              {treffer.length === 0 && <div style={{ padding: 10, fontFamily: MONO, fontSize: 11, color: P.faint }}>keine Aktion passt zu „/{q}“</div>}
              <div style={{ padding: '6px 10px', borderTop: `1px solid ${P.hair}`, fontFamily: MONO, fontSize: 11, color: P.faint }}>↑ ↓ waehlen · ↵ einfuegen · Esc schliessen</div>
            </motion.div>
          )}
        </AnimatePresence>
        <div data-composer style={{ borderRadius: RADIUS.bar, border: `1px solid ${P.line}`, background: P.surface, display: 'flex', alignItems: 'center', gap: 9, padding: '0 12px', height: 46 }}>
          <span style={{ fontFamily: MONO, fontSize: 14, color: offen ? P.ink : P.faint }}>/</span>
          <input data-slasheditor value={text} onChange={e => { setText(e.target.value); setCur(0) }} onKeyDown={taste}
            placeholder="Nachricht …  / fuer Aktionen"
            style={{ flex: 1, border: 'none', outline: 'none', background: 'transparent', fontFamily: MONO, fontSize: 12.5, color: P.ink }} />
          <span style={{ fontFamily: MONO, fontSize: 11, color: P.faint }}>{text.length}/4000</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 9 }}>
          {ACTIONS.slice(0, 3).map((a, i) => <ActionChip key={a.id} a={a} P={P} i={i} kompakt active={false} onClick={() => { setText(a.slash); setCur(0) }} />)}
          <span style={{ marginLeft: 'auto', fontFamily: MONO, fontSize: 11, color: eingefuegt ? P.ink : P.faint }}>
            {eingefuegt ? `eingefuegt: ${eingefuegt}` : 'noch nichts eingefuegt'}
          </span>
        </div>
      </div>
    </Frame>
  )
}

export function V02() {
  return <Managed name="02 · Schraegstrich-Menue im Eingabefeld" note="'/mem', '/skill', '/job' mit Trefferliste · Pfeiltasten und Enter wirken wirklich" render={(P) => <Slash02 P={P} />} />
}

/* ================================================================ 03 · Memory-Abfrage im Chat */

const HITS = [
  { id: 'h1', text: 'Overlay-Lint laeuft nur mit --strict', herkunft: 'Sitzung 17.09. · Notiz', zeit: 'vor 2 min' },
  { id: 'h2', text: 'Anbieter-Limit 5 MB je Datei', herkunft: 'Projektregel · UI-CONTRACT', zeit: 'vor 1 h' },
  { id: 'h3', text: 'Nie force-push auf main', herkunft: 'Memory · Regelkarte', zeit: 'vor 3 d' }
]

function Memory03({ P, dark }) {
  const [laufend, setLaufend] = useState(true)
  const [urteil, setUrteil] = useState({})
  useEffect(() => {
    if (!laufend) return
    const t = setTimeout(() => setLaufend(false), 1400)
    return () => clearTimeout(t)
  }, [laufend])
  const merken = Object.values(urteil).filter(v => v === 'merken').length
  const verwerfen = Object.values(urteil).filter(v => v === 'verwerfen').length
  return (
    <Frame P={P}>
      <Talk P={P}>
        <SayUser P={P}>was weiss ich ueber das overlay-lint?</SayUser>
        <AnimatePresence>
          {laufend ? (
            <motion.span key="lauf" data-memoryrunning initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -6 }} transition={SPRING}
              style={{ alignSelf: 'flex-start', display: 'inline-flex', alignItems: 'center', gap: 8 }}>
              <Orb state="searching" dark={dark} />
              <span style={{ fontFamily: MONO, fontSize: 11, color: P.sub }}>Memory wird durchsucht …</span>
            </motion.span>
          ) : (
            <motion.span key="kopf" initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={SPRING}
              style={{ alignSelf: 'flex-start', fontFamily: MONO, fontSize: 11, color: P.sub }}>3 Treffer · sortiert nach Zeit</motion.span>
          )}
        </AnimatePresence>
        {!laufend && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {HITS.map((h, i) => {
              const u = urteil[h.id]
              return (
                <motion.div key={h.id} data-hit={h.id} data-urteil={u || 'offen'} initial={{ opacity: 0, y: 8, scale: 0.98 }}
                  animate={{ opacity: u === 'verwerfen' ? 0.42 : 1, y: 0, scale: 1, borderColor: u === 'merken' ? P.ink : P.line }}
                  transition={{ ...SPRING, delay: i * 0.07 }}
                  style={{ alignSelf: 'flex-start', maxWidth: 500, border: `1px solid ${P.line}`, background: P.surface, padding: '7px 9px' }}>
                  <div style={{ fontFamily: SANS, fontSize: 11.5, color: P.ink }}>{h.text}</div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 5 }}>
                    <span style={{ fontFamily: MONO, fontSize: 11, color: P.faint }}>{h.herkunft} · {h.time || h.zeit}</span>
                    <span style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 5 }}>
                      <AnimatePresence initial={false}>
                        {u ? (
                          <motion.span key={u} initial={{ opacity: 0, scale: 0.7 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0, scale: 0.7 }} transition={SPRING}>
                            <Tag P={P} aktiv={u === 'merken'}>{u === 'merken' ? 'gemerkt ✓' : 'verworfen ✕'}</Tag>
                          </motion.span>
                        ) : (
                          <motion.span key="wahl" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={SPRING} style={{ display: 'inline-flex', gap: 5 }}>
                            <Btn P={P} i={0} onClick={() => setUrteil(m => ({ ...m, [h.id]: 'merken' }))}>merken</Btn>
                            <Btn P={P} i={1} onClick={() => setUrteil(m => ({ ...m, [h.id]: 'verwerfen' }))}>verwerfen</Btn>
                          </motion.span>
                        )}
                      </AnimatePresence>
                    </span>
                  </div>
                </motion.div>
              )
            })}
          </div>
        )}
      </Talk>
      <div style={{ borderTop: `1px solid ${P.line}`, background: P.surface, padding: '7px 12px', display: 'flex', alignItems: 'center', gap: 10 }}>
        <span style={{ fontFamily: MONO, fontSize: 11, color: P.sub, display: 'inline-flex', alignItems: 'center', gap: 5 }}>
          gemerkt: <Count value={merken} P={P} /> · verworfen: <Count value={verwerfen} P={P} />
        </span>
        <span style={{ marginLeft: 'auto' }}>
          <Btn P={P} Icon={IconRefresh} onClick={() => { setUrteil({}); setLaufend(true) }}>neu abfragen</Btn>
        </span>
      </div>
    </Frame>
  )
}

export function V03() {
  return <Managed name="03 · Memory-Abfrage im Chat" note="Frage, Treffer mit Herkunft und Zeit, 'merken'/'verwerfen' — Zaehler rechts laufen mit"
    render={(P, dark) => <Memory03 P={P} dark={dark} />} />
}

/* ================================================================ 04 · Skills-Vorschlag */

const MAXBELEG = 9

function Skill04({ P }) {
  const [belege, setBelege] = useState(4)
  const [angelegt, setAngelegt] = useState(false)
  return (
    <div style={{ border: `1px solid ${P.line}`, background: P.bg, padding: 12, minHeight: 400 }}>
      <motion.div data-skillbanner initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }} transition={SPRING}
        style={{ display: 'flex', alignItems: 'center', gap: 9, border: `1px solid ${P.ink}`, background: P.surface, padding: '8px 10px' }}>
        <motion.span animate={{ rotate: [0, 12, -6, 0] }} transition={{ duration: 3.2, repeat: Infinity, ease: 'easeInOut' }} style={{ display: 'inline-flex', color: P.ink }}>
          <IconSparkles size={15} stroke={1.8} />
        </motion.span>
        <span style={{ fontFamily: SANS, fontSize: 12.5, fontWeight: 600, color: P.ink }}>wiederkehrende Arbeit erkannt</span>
        <span style={{ fontFamily: MONO, fontSize: 11, color: P.sub }}>3× dieselbe Befehlsfolge · 15.09. · 17.09. · 18.09.</span>
      </motion.div>

      <motion.div data-skillkandidat initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ ...SPRING, delay: 0.08 }}
        style={{ marginTop: 10, border: `1px solid ${P.line}`, background: P.surface, padding: 12 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
          <span style={{ display: 'inline-flex', color: P.ink }}><IconPuzzle size={14} stroke={1.8} /></span>
          <span style={{ fontFamily: MONO, fontSize: 12, fontWeight: 600, color: P.ink }}>overlay-lint</span>
          <span style={{ fontFamily: MONO, fontSize: 11, color: P.sub }}>Prueft das Overlay und meldet Befunde</span>
          <span style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            <span style={{ fontFamily: MONO, fontSize: 11, color: P.sub }}>Beleg-Haeufigkeit</span>
            <Count value={belege} P={P} />
            <span style={{ fontFamily: MONO, fontSize: 11, color: P.faint }}>×</span>
          </span>
        </div>

        {/* Haeufigkeit als Kette — klick auf ein Kaestchen setzt den Zaehler, die Zahl springt mit */}
        <div data-belegkette style={{ display: 'flex', gap: 4, marginTop: 9 }}>
          {/* Statische Deko: Laenge ist fest (MAXBELEG), die Position IST die Kennung — Index hier stabil */}
          {Array.from({ length: MAXBELEG }).map((_, i) => (
            <motion.span key={i} data-beleg={i < belege ? 'voll' : 'leer'} onClick={() => setBelege(i + 1)}
              whileHover={{ scale: 1.25 }} whileTap={{ scale: 0.85 }} transition={SPRING}
              animate={{ backgroundColor: i < belege ? P.ink : 'rgba(0,0,0,0)', borderColor: i < belege ? P.ink : P.line, scale: i < belege ? 1 : 0.88 }}
              style={{ width: 17, height: 17, border: `1px solid ${P.line}`, cursor: 'pointer' }} />
          ))}
        </div>

        <div style={{ marginTop: 11, borderTop: `1px solid ${P.hair}`, paddingTop: 9 }}>
          <div style={{ fontFamily: MONO, fontSize: 11, letterSpacing: 1, color: P.sub, marginBottom: 6 }}>ERKANNTER ABLAUF</div>
          {['befehl am 15.09. · date', 'befehl am 17.09. · overlay_lint --strict', 'befehl am 18.09. · overlay_lint --strict'].map((z, i) => (
            <motion.div key={z} data-ablauf={i + 1} initial={{ opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }} transition={{ ...SPRING, delay: 0.12 + i * 0.05 }}
              style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '4px 0' }}>
              <span style={{ fontFamily: MONO, fontSize: 11, color: P.faint, width: 14 }}>{i + 1}</span>
              <IconChevronRight size={11} stroke={2} color={P.faint} />
              <span style={{ fontFamily: MONO, fontSize: 11, color: P.ink }}>{z}</span>
              <span style={{ marginLeft: 'auto', fontFamily: MONO, fontSize: 11, color: P.faint }}>gleich wie Nr. 1</span>
            </motion.div>
          ))}
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginTop: 12 }}>
          <Btn P={P} i={0} primary Icon={IconCheck} onClick={() => setAngelegt(true)}>als Skill anlegen</Btn>
          <Btn P={P} i={1} Icon={IconTrash} onClick={() => setAngelegt(false)}>verwerfen</Btn>
          <Btn P={P} i={2} Icon={IconSparkles} onClick={() => setBelege(b => Math.min(MAXBELEG, b + 1))}>+ Beleg</Btn>
          <span style={{ marginLeft: 'auto', fontFamily: MONO, fontSize: 11, color: P.faint }}>Kaestchen anklicken setzt die Haeufigkeit</span>
        </div>

        <AnimatePresence>
          {angelegt && (
            <motion.div key="ok" data-skillangelegt initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: 'auto' }} exit={{ opacity: 0, height: 0 }} transition={SPRING} style={{ overflow: 'hidden' }}>
              <div style={{ marginTop: 11, display: 'flex', alignItems: 'center', gap: 8, border: `1px solid ${P.ink}`, background: P.bg, padding: '7px 9px' }}>
                <motion.span initial={{ scale: 0.3, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} transition={{ ...SPRING, delay: 0.1 }} style={{ display: 'inline-flex', color: P.ink }}>
                  <IconCheck size={13} stroke={2.6} />
                </motion.span>
                <span style={{ fontFamily: MONO, fontSize: 11, color: P.ink }}>Skill angelegt · overlay-lint · {belege} Belege uebernommen</span>
                <span style={{ marginLeft: 'auto', fontFamily: MONO, fontSize: 11, color: P.sub }}>Argumente: pfad, --strict</span>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </motion.div>

      <div style={{ marginTop: 10, fontFamily: MONO, fontSize: 11, color: P.faint }}>
        Vorschlaege entstehen aus dem Verlauf — ohne Klick auf „als Skill anlegen“ passiert nichts.
      </div>
    </div>
  )
}

export function V04() {
  return <Managed name="04 · Skills-Vorschlag aus wiederkehrender Arbeit" note="Beleg-Haeufigkeit zaehlt sichtbar hoch — Zahl und Kette springen mit"
    render={(P) => <Skill04 P={P} />} />
}

/* ================================================================ 05 · Sitzungs-Analyse */

const KENN = [
  { id: 'k1', label: 'Dauer', to: 42, einheit: 'min', pct: 62, hinweis: '09:12 – 09:54' },
  { id: 'k2', label: 'Tokens', to: 128400, einheit: '', pct: 74, hinweis: 'Ein 412 / Aus 128.000' },
  { id: 'k3', label: 'Werkzeuge', to: 37, einheit: 'Aufrufe', pct: 88, hinweis: '4 Arten' },
  { id: 'k4', label: 'Fehler', to: 2, einheit: 'behoben 1', pct: 14, hinweis: '1 offen' }
]
const WERKZEUGE = [
  { name: 'terminal', n: 14, pct: 94 },
  { name: 'browser', n: 9, pct: 60 },
  { name: 'datei', n: 8, pct: 54 },
  { name: 'subagent', n: 6, pct: 40 }
]

function Analyse05({ P, dark }) {
  const [lauf, setLauf] = useState(1)
  const [arbeit, setArbeit] = useState(false)
  const [bericht, setBericht] = useState(false)
  useEffect(() => {
    if (!arbeit) return
    const t = setTimeout(() => setArbeit(false), 900)
    return () => clearTimeout(t)
  }, [arbeit])
  return (
    <div style={{ border: `1px solid ${P.line}`, background: P.bg, padding: 12 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
        <span style={{ display: 'inline-flex', color: P.ink }}><IconChartBar size={14} stroke={1.8} /></span>
        <span style={{ fontFamily: SANS, fontSize: 12.5, fontWeight: 600, color: P.ink }}>Sitzung 18.09. · 09:12 – 09:54</span>
        <span style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 7 }}>
          <AnimatePresence>
            {arbeit && (
              <motion.span key="orb" initial={{ opacity: 0, scale: 0.8 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0, scale: 0.8 }} transition={SPRING} style={{ display: 'inline-flex' }}>
                <Orb state="working" dark={dark} />
              </motion.span>
            )}
          </AnimatePresence>
          <Btn P={P} Icon={IconRefresh} onClick={() => { setLauf(l => l + 1); setArbeit(true) }}>neu auswerten</Btn>
        </span>
      </div>

      <div key={lauf} data-kennzahlen style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginTop: 11 }}>
        {KENN.map((k, i) => (
          <motion.div key={k.id} data-kennzahl={k.id} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ ...SPRING, delay: i * 0.05 }}
            style={{ border: `1px solid ${P.line}`, background: P.surface, padding: '9px 10px' }}>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}>
              <Counter to={k.to} P={P} ms={900 + i * 160} />
              <span style={{ fontFamily: MONO, fontSize: 11, color: P.sub }}>{k.einheit}</span>
              <span style={{ marginLeft: 'auto', fontFamily: MONO, fontSize: 11, color: P.faint }}>{k.hinweis}</span>
            </div>
            <div style={{ fontFamily: MONO, fontSize: 11, letterSpacing: 0.8, color: P.sub, margin: '6px 0 5px' }}>{k.label.toUpperCase()}</div>
            <Bar pct={k.pct} P={P} i={i} hoch />
          </motion.div>
        ))}
      </div>

      <div key={'w' + lauf} style={{ marginTop: 12, border: `1px solid ${P.line}`, background: P.surface, padding: '9px 10px' }}>
        <div style={{ fontFamily: MONO, fontSize: 11, letterSpacing: 1, color: P.sub, marginBottom: 8 }}>WERKZEUGE JE ART</div>
        {WERKZEUGE.map((w, i) => (
          <div key={w.name} style={{ display: 'flex', alignItems: 'center', gap: 9, padding: '3px 0' }}>
            <span style={{ fontFamily: MONO, fontSize: 11, color: P.ink, width: 74 }}>{w.name}</span>
            <Bar pct={w.pct} P={P} i={i} />
            <span style={{ fontFamily: MONO, fontSize: 11, color: P.faint }}>{w.n}×</span>
          </div>
        ))}
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 11 }}>
        <Btn P={P} i={0} primary Icon={IconFileText} onClick={() => setBericht(true)}>Bericht erzeugen</Btn>
        <Btn P={P} i={1} Icon={IconHistory} onClick={() => { setLauf(l => l + 1); setArbeit(true) }}>Balken neu laufen lassen</Btn>
        <AnimatePresence>
          {bericht && (
            <motion.span key="b" data-bericht initial={{ opacity: 0, x: -8, scale: 0.94 }} animate={{ opacity: 1, x: 0, scale: 1 }} exit={{ opacity: 0 }} transition={SPRING}>
              <Tag P={P} aktiv>Bericht im Chat · 2 Abschnitte</Tag>
            </motion.span>
          )}
        </AnimatePresence>
      </div>
    </div>
  )
}

export function V05() {
  return <Managed name="05 · Sitzungs-Analyse mit Mini-Balken" note="Kennzahlen zaehlen hoch, Balken laufen von 0 auf den Wert · 'neu auswerten' wiederholt es"
    render={(P, dark) => <Analyse05 P={P} dark={dark} />} />
}

/* ================================================================ 06 · Jobs */

const JOBS = [
  { id: 'j1', name: 'Nachtlauf: Regression', plan: 'taeglich 23:30', next: 'in 5 h 12 min', Icon: IconRepeat, on: true },
  { id: 'j2', name: 'Wochenbericht', plan: 'Mo 09:00', next: 'in 2 d 16 h', Icon: IconFileText, on: true },
  { id: 'j3', name: 'Aufraeumen: alte Uploads', plan: 'taeglich 04:00', next: 'in 9 h 42 min', Icon: IconTrash, on: false }
]

function Jobs06({ P, dark }) {
  const [on, setOn] = useState(() => Object.fromEntries(JOBS.map(j => [j.id, j.on])))
  const [lauf, setLauf] = useState({})
  const [tick, setTick] = useState(() => Date.now())
  const n = Object.keys(lauf).length
  useEffect(() => {
    if (!n) return
    const t = setInterval(() => setTick(Date.now()), 1000)
    return () => clearInterval(t)
  }, [n])
  const schalten = (id) => setLauf(m => { const c = { ...m }; if (c[id]) delete c[id]; else c[id] = Date.now(); return c })
  return (
    <div style={{ border: `1px solid ${P.line}`, background: P.surface }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '7px 11px', borderBottom: `1px solid ${P.line}` }}>
        <IconCalendarClock size={13} stroke={1.8} color={P.ink} />
        <span style={{ fontFamily: MONO, fontSize: 11, letterSpacing: 1, color: P.sub }}>JOB-PLANER</span>
        <span style={{ fontFamily: MONO, fontSize: 11, color: P.ink }}>{JOBS.filter(j => on[j.id]).length}/3 an</span>
        <span style={{ marginLeft: 'auto', fontFamily: MONO, fontSize: 11, color: P.faint }}>{n ? `${n} laufen gerade` : 'keiner laeuft gerade'}</span>
      </div>
      {JOBS.map((j, i) => {
        const laeuft = !!lauf[j.id]
        const sek = laeuft ? Math.max(0, Math.round((tick - lauf[j.id]) / 1000)) : 0
        return (
          <motion.div key={j.id} layout data-job={j.id} data-checked={on[j.id] ? 'true' : 'false'} data-lauf={laeuft ? 'true' : 'false'}
            initial={{ opacity: 0, y: 5 }} animate={{ opacity: 1, y: 0 }} transition={{ ...SPRING, delay: i * 0.06 }}
            style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '9px 11px', borderBottom: `1px solid ${P.hair}` }}>
            <motion.span animate={{ color: on[j.id] ? P.ink : P.faint, opacity: on[j.id] ? 1 : 0.5 }} transition={{ duration: 0.16 }} style={{ display: 'inline-flex' }}>
              <j.Icon size={14} strokeWidth={1.8} />
            </motion.span>
            <span style={{ flex: 1, minWidth: 0 }}>
              <span style={{ display: 'block', fontFamily: SANS, fontSize: 12, fontWeight: on[j.id] ? 600 : 500, color: P.ink }}>{j.name}</span>
              <span style={{ display: 'block', fontFamily: MONO, fontSize: 11, color: P.sub }}>{j.plan}</span>
            </span>
            <AnimatePresence initial={false}>
              {laeuft ? (
                <motion.span key="orb" data-joblauf initial={{ opacity: 0, scale: 0.7 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0, scale: 0.7 }} transition={SPRING}
                  style={{ display: 'inline-flex', alignItems: 'center', gap: 7, width: 150, justifyContent: 'flex-end' }}>
                  <Orb state="working" dark={dark} />
                  <span style={{ fontFamily: MONO, fontSize: 11, color: P.ink }}>laeuft seit {sek} s</span>
                </motion.span>
              ) : (
                <motion.span key="next" data-jobnext initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={SPRING}
                  style={{ display: 'inline-flex', alignItems: 'center', gap: 5, width: 150, justifyContent: 'flex-end' }}>
                  <IconClock size={10} stroke={2} color={P.faint} />
                  <span style={{ fontFamily: MONO, fontSize: 11, color: on[j.id] ? P.sub : P.faint }}>{on[j.id] ? j.next : 'angehalten'}</span>
                </motion.span>
              )}
            </AnimatePresence>
            <motion.span data-jobstart whileHover={{ scale: 1.04 }} whileTap={{ scale: 0.94 }} transition={SPRING} onClick={() => schalten(j.id)}
              animate={{ borderColor: laeuft || on[j.id] ? P.ink : P.line, backgroundColor: laeuft ? P.ink : 'rgba(0,0,0,0)', color: laeuft ? P.bg : P.ink, opacity: on[j.id] ? 1 : 0.4 }}
              style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontFamily: MONO, fontSize: 11, padding: '5px 10px', border: `1px solid ${P.line}`, cursor: 'pointer', width: 128, justifyContent: 'center' }}>
              <IconPlayerPlay size={10} stroke={2.2} />{laeuft ? 'anhalten' : 'jetzt starten'}
            </motion.span>
            <Switch value={on[j.id]} onChange={() => setOn(m => ({ ...m, [j.id]: !m[j.id] }))} P={P} />
          </motion.div>
        )
      })}
      <div style={{ padding: '8px 11px', fontFamily: MONO, fontSize: 11, color: P.faint }}>
        Ausgeschaltete Aufgaben laufen nicht weiter — 'jetzt starten' wirkt sofort, der Sekundenzaehler laeuft mit.
      </div>
    </div>
  )
}

export function V06() {
  return <Managed name="06 · Jobs: drei geplante Aufgaben" note="naechster Lauf, Schalter und 'jetzt starten' mit laufendem Zaehler und Orb"
    render={(P, dark) => <Jobs06 P={P} dark={dark} />} />
}

/* ================================================================ 07 · Werkzeug suchen */

const TOOLS = [
  { id: 't1', name: 'web_search', herkunft: 'MCP', desc: 'Suchanfragen im Netz', Icon: IconPlug },
  { id: 't2', name: 'gh pr list', herkunft: 'CLI', desc: 'Offene PRs des Repos', Icon: IconTerminal },
  { id: 't3', name: 'overlay_lint', herkunft: 'eigenes', desc: 'Prueft das Overlay auf Befunde', Icon: IconPuzzle },
  { id: 't4', name: 'arxiv', herkunft: 'MCP', desc: 'Paper suchen und lesen', Icon: IconPlug },
  { id: 't5', name: 'date', herkunft: 'CLI', desc: 'Zeit und Zeitzone', Icon: IconTerminal },
  { id: 't6', name: 'speicher_index', herkunft: 'eigenes', desc: 'Memory durchsuchen', Icon: IconPuzzle }
]

function Werkzeug07({ P, dark }) {
  const [q, setQ] = useState('')
  const [dran, setDran] = useState([])
  const treffer = TOOLS.filter(t => !q || (t.name + ' ' + t.desc + ' ' + t.herkunft).toLowerCase().includes(q.toLowerCase()))
  const toggle = (id) => setDran(d => d.includes(id) ? d.filter(x => x !== id) : [...d, id])
  return (
    <div style={{ border: `1px solid ${P.line}`, background: P.surface }}>
      <div data-toolsearch style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 11px', borderBottom: `1px solid ${P.hair}` }}>
        <IconSearch size={12} stroke={1.9} color={P.sub} />
        <input data-tooleditor value={q} onChange={e => setQ(e.target.value)} placeholder="Werkzeug suchen — Name, Herkunft oder Zweck"
          style={{ flex: 1, border: 'none', outline: 'none', background: 'transparent', fontFamily: MONO, fontSize: 11, color: P.ink }} />
        <AnimatePresence>
          {q && (
            <motion.span key="orb" initial={{ opacity: 0, scale: 0.7 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0, scale: 0.7 }} transition={SPRING} style={{ display: 'inline-flex' }}>
              <Orb state="searching" dark={dark} />
            </motion.span>
          )}
        </AnimatePresence>
        {q && <IconX size={12} stroke={2} color={P.faint} onClick={() => setQ('')} style={{ cursor: 'pointer' }} />}
        <span style={{ fontFamily: MONO, fontSize: 11, color: P.faint }}>{treffer.length} Treffer</span>
      </div>
      <div style={{ maxHeight: 246, overflow: 'auto' }}>
        <AnimatePresence initial={false}>
          {treffer.map((t, i) => {
            const ist = dran.includes(t.id)
            return (
              <motion.div key={t.id} layout data-tool={t.id} data-dran={ist ? 'true' : 'false'}
                initial={{ opacity: 0, y: 5 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, height: 0 }} transition={{ ...SPRING, delay: i * 0.04 }}
                style={{ display: 'flex', alignItems: 'center', gap: 9, padding: '8px 11px', borderBottom: `1px solid ${P.hair}` }}>
                <span style={{ display: 'inline-flex', color: ist ? P.ink : P.sub }}><t.Icon size={13} strokeWidth={1.8} /></span>
                <span style={{ fontFamily: MONO, fontSize: 11, color: P.ink, width: 116 }}>{t.name}</span>
                <Tag P={P} aktiv={t.herkunft === 'eigenes'}>{t.herkunft}</Tag>
                <span style={{ fontFamily: MONO, fontSize: 11, color: P.sub, flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{t.desc}</span>
                <motion.span data-toolhang whileHover={{ scale: 1.04 }} whileTap={{ scale: 0.94 }} transition={SPRING} onClick={() => toggle(t.id)}
                  animate={{ borderColor: ist ? P.ink : P.line, backgroundColor: ist ? P.ink : 'rgba(0,0,0,0)', color: ist ? P.bg : P.ink }}
                  style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontFamily: MONO, fontSize: 11, padding: '4px 9px', border: `1px solid ${P.line}`, cursor: 'pointer', width: 96, justifyContent: 'center' }}>
                  {ist ? <IconCheck size={10} stroke={2.6} /> : <IconCornerDownLeft size={10} stroke={2} />}{ist ? 'dran' : 'anhaengen'}
                </motion.span>
              </motion.div>
            )
          })}
        </AnimatePresence>
        {treffer.length === 0 && <div style={{ padding: 11, fontFamily: MONO, fontSize: 11, color: P.faint }}>kein Werkzeug passt zu „{q}“ — MCP-IconServer verbinden oder eigenes Werkzeug anlegen?</div>}
      </div>
      <div style={{ borderTop: `1px solid ${P.line}`, background: P.bg, padding: '8px 11px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontFamily: MONO, fontSize: 11, color: P.sub }}>
            angehaengt: <Count value={dran.length} P={P} /> an die naechste Nachricht
          </span>
          <span style={{ display: 'inline-flex', gap: 5, flexWrap: 'wrap' }}>
            <AnimatePresence initial={false}>
              {dran.map(id => {
                const t = TOOLS.find(x => x.id === id)
                return (
                  <motion.span key={id} data-dranchip={id} initial={{ opacity: 0, scale: 0.8, y: 4 }} animate={{ opacity: 1, scale: 1, y: 0 }} exit={{ opacity: 0, scale: 0.8 }} transition={SPRING}>
                    <Tag P={P}>{t.name}</Tag>
                  </motion.span>
                )
              })}
            </AnimatePresence>
          </span>
          <span style={{ marginLeft: 'auto' }}>
            <Btn P={P} Icon={IconX} onClick={() => setDran([])}>leeren</Btn>
          </span>
        </div>
        <div style={{ marginTop: 7, fontFamily: MONO, fontSize: 11, color: P.faint }}>
          Herkunft sichtbar: MCP · CLI · eigenes — angehaengte Werkzeuge gelten nur fuer die naechste Nachricht.
        </div>
      </div>
    </div>
  )
}

export function V07() {
  return <Managed name="07 · Werkzeug suchen mit Herkunft" note="Suchfeld filtert echt · 'anhaengen' sammelt unten mit Zaehler, 'leeren' raeumt"
    render={(P, dark) => <Werkzeug07 P={P} dark={dark} />} />
}

/* ================================================================ 08 · Kontextmenue im Verlauf */

const MENUE = [
  { id: 'm1', label: 'hier erinnern', hinweis: 'Notiz aus dieser Stelle', Icon: IconBookmark, folge: 'Notiz im Memory · Stelle 14:32' },
  { id: 'm2', label: 'als Skill', hinweis: 'Ablauf als Skill anlegen', Icon: IconSparkles, folge: 'Skill angelegt · overlay-lint' },
  { id: 'm3', label: 'Job daraus machen', hinweis: 'wiederkehrend planen', Icon: IconCalendarClock, folge: 'Job geplant · taeglich 23:30' },
  { id: 'm4', label: 'Bericht', hinweis: 'Abschnitt in den Bericht', Icon: IconFileText, folge: 'Abschnitt im Bericht · 1 von 4' }
]

function Kontext08({ P }) {
  const [offen, setOffen] = useState(true)
  const [gewaehlt, setGewaehlt] = useState(null)
  const m = MENUE.find(x => x.id === gewaehlt)
  return (
    <Frame P={P} hoehe={410}>
      <div onClick={() => setOffen(false)} style={{ flex: 1, padding: 14, display: 'flex', flexDirection: 'column', gap: 9, position: 'relative' }}>
        <SayUser P={P}>zerleg das in drei spuren</SayUser>
        <motion.div layout data-verlauf style={{ alignSelf: 'flex-start', maxWidth: 500, position: 'relative', border: `1px solid ${offen ? P.ink : P.line}`, background: P.surface, borderRadius: RADIUS.module, padding: '7px 10px' }}>
          <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8 }}>
            <span style={{ fontFamily: MONO, fontSize: 11.5, color: P.ink, lineHeight: '17px' }}>
              drei subagenten gerufen. Spur 1: Nachweise · Spur 2: Nutzerweg · Spur 3: Zustaende nach Reload.
            </span>
            <motion.span data-kontextbtn onClick={(e) => { e.stopPropagation(); setOffen(o => !o) }} whileHover={{ scale: 1.12 }} whileTap={{ scale: 0.9 }} transition={SPRING}
              animate={{ color: offen ? P.bg : P.sub, backgroundColor: offen ? P.ink : 'rgba(0,0,0,0)' }}
              style={{ display: 'inline-flex', width: 20, height: 20, alignItems: 'center', justifyContent: 'center', border: `1px solid ${P.line}`, cursor: 'pointer', flex: '0 0 auto' }}>
              <IconDots size={12} stroke={2.2} />
            </motion.span>
          </div>
          <div style={{ marginTop: 5, fontFamily: MONO, fontSize: 11, color: P.faint }}>Antwort · 14:32 · 3 Werkzeugaufrufe</div>
          <AnimatePresence>
            {offen && (
              <motion.div data-kontextmenu key="menu" initial={{ opacity: 0, scale: 0.94, y: -6 }} animate={{ opacity: 1, scale: 1, y: 0 }} exit={{ opacity: 0, scale: 0.96, y: -4 }}
                transition={SPRING} onClick={e => e.stopPropagation()}
                style={{ position: 'absolute', right: -6, top: 30, width: 300, border: `1px solid ${P.line}`, background: P.surface, boxShadow: '0 14px 32px rgba(0,0,0,0.22)', zIndex: 5, transformOrigin: 'top right' }}>
                <div style={{ padding: '6px 10px', borderBottom: `1px solid ${P.hair}`, fontFamily: MONO, fontSize: 11, letterSpacing: 1, color: P.sub }}>DIESE STELLE</div>
                {MENUE.map((x, i) => (
                  <motion.div key={x.id} data-kontextitem={x.id} initial={{ opacity: 0, x: -5 }} animate={{ opacity: 1, x: 0 }} transition={{ ...SPRING, delay: i * 0.04 }}
                    onClick={() => { setGewaehlt(x.id); setOffen(false) }} whileHover={{ x: 2 }}
                    style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 10px', cursor: 'pointer' }}>
                    <x.Icon size={12} strokeWidth={1.8} color={P.ink} />
                    <span style={{ fontFamily: MONO, fontSize: 11, color: P.ink }}>{x.label}</span>
                    <span style={{ marginLeft: 'auto', minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                      fontFamily: MONO, fontSize: 11, color: P.faint }}>{x.hinweis}</span>
                  </motion.div>
                ))}
              </motion.div>
            )}
          </AnimatePresence>
        </motion.div>
        <AnimatePresence>
          {m && (
            <motion.div key={m.id} data-kontextfolge initial={{ opacity: 0, y: 8, scale: 0.97 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, y: 6 }} transition={SPRING}
              style={{ alignSelf: 'flex-start', display: 'inline-flex', alignItems: 'center', gap: 8, border: `1px solid ${P.ink}`, background: P.bg, padding: '7px 10px' }}>
              <motion.span initial={{ scale: 0.3, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} transition={{ ...SPRING, delay: 0.08 }} style={{ display: 'inline-flex', color: P.ink }}>
                <IconCheck size={12} stroke={2.6} />
              </motion.span>
              <span style={{ fontFamily: MONO, fontSize: 11, color: P.ink }}>{m.folge}</span>
              <motion.span whileHover={{ scale: 1.05 }} whileTap={{ scale: 0.94 }} transition={SPRING} onClick={() => setGewaehlt(null)} style={{ display: 'inline-flex', cursor: 'pointer', color: P.faint }}>
                <IconX size={11} stroke={2.2} />
              </motion.span>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
      <div style={{ borderTop: `1px solid ${P.line}`, background: P.surface, padding: '7px 12px', fontFamily: MONO, fontSize: 11, color: P.faint }}>
        Klick auf „···“ oeffnet das Menue · Klick daneben schliesst es · jede Wahl wirkt sofort
      </div>
    </Frame>
  )
}

export function V08() {
  return <Managed name="08 · Kontextmenue im Verlauf" note="'hier erinnern' · 'als Skill' · 'Job daraus machen' · 'Bericht' — Auswahl zeigt ihre Folge"
    render={(P) => <Kontext08 P={P} />} />
}

/* ================================================================ 09 · Sehr kompakt */

const KOERPER = {
  memory: { kopf: 'Memory · 12 Eintraege', zeilen: ['Overlay-Lint nur mit --strict', 'Nie force-push auf main'] },
  skills: { kopf: 'Skills · 3 angelegt', zeilen: ['overlay-lint · zuletzt 17.09.', 'bericht-bauen · ruht'] },
  analyse: { kopf: 'Analyse · Sitzung 42 min', zeilen: ['128.400 Tokens · 37 Aufrufe', '2 Fehler · 1 behoben'] },
  jobs: { kopf: 'Jobs · 2 von 3 an', zeilen: ['Nachtlauf: Regression · 23:30', 'Aufraeumen Uploads · aus'] }
}

function Kompakt09({ P }) {
  const [offen, setOffen] = useState('memory')
  const k = KOERPER[offen]
  return (
    <Frame P={P} hoehe={400}>
      <Talk P={P}>
        <SayUser P={P}>wo steht das mit dem overlay-lint?</SayUser>
        <SayBot P={P}>kurz nachgesehen — drei Stellen.</SayBot>
      </Talk>
      <div style={{ padding: '0 10px 10px' }}>
        <AnimatePresence initial={false}>
          {k && (
            <motion.div key={offen} data-kompaktkoerper initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: 'auto' }} exit={{ opacity: 0, height: 0 }} transition={SPRING} style={{ overflow: 'hidden' }}>
              <div style={{ marginBottom: 8, border: `1px solid ${P.line}`, background: P.surface, padding: '8px 10px' }}>
                <div style={{ fontFamily: MONO, fontSize: 11, fontWeight: 600, color: P.ink }}>{k.kopf}</div>
                {k.zeilen.map((z, i) => (
                  <motion.span key={z} initial={{ opacity: 0, x: -5 }} animate={{ opacity: 1, x: 0 }} transition={{ ...SPRING, delay: 0.05 + i * 0.05 }}
                    style={{ display: 'block', fontFamily: MONO, fontSize: 11, color: P.sub, marginTop: 3 }}>· {z}</motion.span>
                ))}
              </div>
            </motion.div>
          )}
        </AnimatePresence>
        <div data-kompaktzeile style={{ display: 'flex', alignItems: 'center', gap: 6, border: `1px solid ${P.line}`, background: P.surface, borderRadius: RADIUS.pill, padding: '5px 8px' }}>
          {ACTIONS.slice(0, 4).map((a, i) => (
            <motion.span key={a.id} layout data-kompaktchip={a.id} data-active={offen === a.id ? 'true' : 'false'}
              onClick={() => setOffen(o => (o === a.id ? null : a.id))} whileHover={{ y: -2 }} whileTap={{ scale: 0.92 }} transition={{ ...SPRING, delay: i * 0.03 }}
              animate={{ backgroundColor: offen === a.id ? P.ink : 'rgba(0,0,0,0)', borderColor: offen === a.id ? P.ink : P.line, color: offen === a.id ? P.bg : P.sub }}
              title={a.label}
              style={{ display: 'inline-flex', alignItems: 'center', gap: 4, border: `1px solid ${P.line}`, padding: '4px 9px', borderRadius: RADIUS.pill, cursor: 'pointer', fontFamily: MONO, fontSize: 11 }}>
              <a.Icon size={11} strokeWidth={2} />{a.short}
            </motion.span>
          ))}
          <span style={{ flex: 1 }} />
          <span style={{ fontFamily: MONO, fontSize: 11, color: P.faint }}>Start with a goal</span>
          <motion.span layout animate={{ rotate: offen ? 180 : 0 }} transition={SPRING} style={{ display: 'inline-flex', color: P.faint }}>
            <IconChevronRight size={12} stroke={2} style={{ transform: 'rotate(90deg)' }} />
          </motion.span>
        </div>
        <div style={{ marginTop: 7, fontFamily: MONO, fontSize: 11, color: P.faint, textAlign: 'center' }}>
          eine Zeile, vier Kuerzel zum Aufklappen · Werkzeugsuche bleibt in der Liste
        </div>
      </div>
    </Frame>
  )
}

export function V09() {
  return <Managed name="09 · Sehr kompakt: eine Zeile mit vier Kuerzeln" note="Klick klappt den Koerper auf, der zweite Klick wieder zu"
    render={(P) => <Kompakt09 P={P} />} />
}

/* ================================================================ 10 · Dunkle Vollbild-Fassung */

function Dunkel10({ P }) {
  const [sel, setSel] = useState('analyse')
  const a = ACTIONS.find(x => x.id === sel)
  const pv = PREVIEW[sel]
  return (
    <Frame P={P} hoehe={470}>
      <Talk P={P}>
        <SayUser P={P}>wo steht das mit dem overlay-lint?</SayUser>
        <SayBot P={P} i={0}>drei Stellen — ich zeig sie dir als Treffer.</SayBot>
        <motion.span key={sel} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={SPRING}
          style={{ alignSelf: 'flex-start', display: 'inline-flex', alignItems: 'center', gap: 8, fontFamily: MONO, fontSize: 11, color: P.sub }}>
          <a.Icon size={12} strokeWidth={1.9} color={P.ink} />{pv.kopf}
        </motion.span>
      </Talk>
      <div style={{ padding: 10 }}>
        <div style={{ margin: '0 4px 9px', border: `1px solid ${P.line}`, background: P.surface, padding: '8px 10px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
            <span style={{ fontFamily: MONO, fontSize: 11, fontWeight: 600, color: P.ink }}>{pv.kopf}</span>
            <span style={{ marginLeft: 'auto', fontFamily: MONO, fontSize: 11, color: P.faint }}>{a.slash}</span>
          </div>
          <div style={{ marginTop: 6, display: 'flex', flexDirection: 'column', gap: 3 }}>
            {pv.zeilen.map((z, i) => (
              <motion.span key={z} initial={{ opacity: 0, x: -5 }} animate={{ opacity: 1, x: 0 }} transition={{ ...SPRING, delay: 0.05 + i * 0.05 }}
                style={{ fontFamily: MONO, fontSize: 11, color: P.sub }}>· {z}</motion.span>
            ))}
          </div>
        </div>
        <div data-composer style={{ borderRadius: RADIUS.bar, border: `1px solid ${P.line}`, background: P.surface, padding: '8px 10px 6px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
            {ACTIONS.map((x, i) => <ActionChip key={x.id} a={x} P={P} i={i} active={x.id === sel} onClick={() => setSel(x.id)} />)}
            <span style={{ marginLeft: 'auto', fontFamily: MONO, fontSize: 11, color: P.faint }}>{a.slash}</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '9px 6px 2px' }}>
            <span style={{ fontFamily: SANS, fontSize: 14, color: P.faint, flex: 1 }}>Start with a goal</span>
            <span style={{ display: 'inline-flex', color: P.ink }}><IconCornerDownLeft size={13} stroke={1.9} /></span>
          </div>
        </div>
      </div>
    </Frame>
  )
}

export function V10() {
  return <Managed name="10 · Dunkle Vollbild-Fassung des Favoriten" note="Variante 01 in Dunkel — identische Geometrie, nur invertierte Tinte" dark0 toggleable={false}
    render={(P) => <Dunkel10 P={P} />} />
}

export default {
  '01 · Chat-Aktionen: Aktionsleiste am Composer': () => <V01 />,
  '02 · Chat-Aktionen: Schraegstrich-Menue im Eingabefeld': () => <V02 />,
  '03 · Chat-Aktionen: Memory-Abfrage im Chat': () => <V03 />,
  '04 · Chat-Aktionen: Skills-Vorschlag aus wiederkehrender Arbeit': () => <V04 />,
  '05 · Chat-Aktionen: Sitzungs-Analyse mit Mini-Balken': () => <V05 />,
  '06 · Chat-Aktionen: Jobs mit naechstem Lauf und Schalter': () => <V06 />,
  '07 · Chat-Aktionen: Werkzeug suchen mit Herkunft': () => <V07 />,
  '08 · Chat-Aktionen: Kontextmenue im Verlauf': () => <V08 />,
  '09 · Chat-Aktionen: Sehr kompakt, vier Kuerzel zum Aufklappen': () => <V09 />,
  '10 · Chat-Aktionen: Dunkle Vollbild-Fassung (Favorit)': () => <V10 />
}
