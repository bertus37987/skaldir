/**
 * Panel-Manager (Kategorie E) — EIN Panel, in dem alle anderen Panels umgeschaltet
 * werden. Owner-Vorgabe: "oder einfach ein neues Panel, wo alle anderen Panels
 * getoggelt werden koennen". Genau das steht hier.
 *
 * Die sieben schaltbaren Panels: Kontext-Kapsel · Subagenten-Fenster ·
 * Subagenten-Panel · Orb-Widgets (Suche/Reasoning/Werkzeuge/Quellen) ·
 * Computer-Use-Overlay · Ziel-Baum (Goal) · Datei-Uploads.
 *
 * Der Zustand ist echt: useState je Panel, ein Klick schaltet wirklich um, die
 * Umschaltung animiert (Feder + Farbwechsel) und der Zaehler der offenen Elemente
 * aendert sich sichtbar. Nur Schwarz/Weiss/Grau, Symbole aus @tabler/icons-react.
 */
import { motion, AnimatePresence, animate } from 'motion/react'
import React, { useState } from 'react'
import { IconAdjustmentsHorizontal, IconBorderCornerRounded, IconCheck, IconChevronsRight, IconDeviceDesktop, IconFilter, IconLayoutGrid, IconLayoutRows, IconLayoutSidebarRight, IconPackages, IconSearch, IconTarget, IconUpload, IconX } from '@tabler/icons-react'
/* Eine Wahrheit fuer Farben, Schrift, Radien und Federn — plus die gemeinsame
   Barrierefreiheits-Bruecke (reduzierte Bewegung, Tastaturbedienbarkeit). */
import { LIGHT, DARK, MONO, SANS, SPRING, RADIUS, TEXT } from './_tokens'
import { useBarrierefrei } from './_barrierefrei'

/* Die sieben schaltbaren Panels — genau diese, keine anderen. working = lebt gerade. */
const PANELS = [
  { id: 'context', short: 'CTX', name: 'Kontext-Kapsel', desc: 'Laufender Auftrag, Regeln und Umgebung des Agents', items: 4, working: false, Icon: IconPackages },
  { id: 'windows', short: 'FEN', name: 'Subagenten-Fenster', desc: 'Eigene Fenster je Subagent auf dem Schreibtisch', items: 3, working: true, Icon: IconLayoutSidebarRight },
  { id: 'subpanel', short: 'SUB', name: 'Subagenten-Panel', desc: 'Offene Zeilen und Auswahl der Subagenten', items: 3, working: true, Icon: IconLayoutRows },
  { id: 'orbs', short: 'ORB', name: 'Orb-Widgets', desc: 'Suche, Reasoning, Werkzeuge und Quellen am Orb', items: 4, working: false, Icon: IconBorderCornerRounded },
  { id: 'cuse', short: 'PC', name: 'Computer-Use-Overlay', desc: 'Bildschirm-Wiedergabe des Computer-Use-Agenten', items: 1, working: true, Icon: IconDeviceDesktop },
  { id: 'goal', short: 'ZIL', name: 'Ziel-Baum (Goal)', desc: 'Zielkriterien mit Haken und Teilbaeumen', items: 11, working: false, Icon: IconTarget },
  { id: 'uploads', short: 'DAT', name: 'Datei-Uploads', desc: 'Vorschau, Pfad und Fortschritt der Uploads', items: 2, working: false, Icon: IconUpload }
]

/* Echter Zustand: je Panel an/aus plus Zeitpunkt des letzten Wechsels. */
function useManager() {
  const [on, setOn] = useState(() => Object.fromEntries(PANELS.map(p => [p.id, p.id !== 'cuse' && p.id !== 'uploads'])))
  const [last, setLast] = useState({})
  const [now, setNow] = useState(() => Date.now())
  const tick = () => setNow(Date.now())
  const toggle = (id) => {
    setOn(m => ({ ...m, [id]: !m[id] }))
    setLast(m => ({ ...m, [id]: Date.now() }))
    tick()
  }
  const setAll = (v) => {
    setOn(Object.fromEntries(PANELS.map(p => [p.id, v])))
    setLast(m => ({ ...m, ...Object.fromEntries(PANELS.map(p => [p.id, Date.now()])) }))
    tick()
  }
  const count = PANELS.filter(p => on[p.id]).length
  const openItems = PANELS.reduce((a, p) => a + (on[p.id] ? p.items : 0), 0)
  return { on, toggle, setAll, last, now, count, openItems }
}

const fmtClock = (ms) => (ms ? new Date(ms).toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '—')
const fmtAgo = (ms, now) => (ms ? `${Math.max(1, Math.round((now - ms) / 1000))} s` : '—')

/* Schalter: Kapsel faerbt weich um, Knopf faehrt mit Feder auf die andere Seite. */
const Switch = ({ value, onChange, P, mini = false }) => (
  <motion.span data-switch data-checked={value ? 'true' : 'false'}
    onClick={(e) => { e.stopPropagation(); onChange(!value) }} role="switch" aria-checked={value ? 'true' : 'false'}
    whileTap={{ scale: 0.9 }} transition={SPRING}
    animate={{ backgroundColor: value ? P.ink : P.surface, borderColor: value ? P.ink : P.line }}
    style={{ display: 'inline-flex', alignItems: 'center', justifyContent: value ? 'flex-end' : 'flex-start',
      width: mini ? 30 : 40, height: mini ? 17 : 20, borderRadius: RADIUS.pill, cursor: 'pointer', padding: 2,
      boxSizing: 'border-box', border: `1px solid ${P.line}`, flex: '0 0 auto' }}>
    <motion.span layout transition={SPRING}
      style={{ width: mini ? 11 : 14, height: mini ? 11 : 14, borderRadius: RADIUS.pill, background: value ? P.surface : P.faint }} />
  </motion.span>
)

/* Kopf der Buehne: Titel, Hinweis, Umschalter hell/dunkel (Varianten 1-8). */
function StageHead({ name, note, dark, setDark, toggleable, P }) {
  return (
    <div style={{ marginBottom: 10 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <span style={{ fontFamily: MONO, fontSize: 12, fontWeight: 600 }}>{name}</span>
        {toggleable && (
          <span data-themetoggle style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 6, fontFamily: MONO, fontSize: TEXT.meta }}>
            <motion.span data-themelight whileTap={{ scale: 0.94 }} transition={SPRING}
              animate={{ borderColor: !dark ? P.ink : P.line, backgroundColor: !dark ? P.ink : P.surface, color: !dark ? P.bg : P.ink }}
              onClick={() => setDark(false)}
              style={{ padding: '3px 9px', border: `1px solid ${P.line}`, cursor: 'pointer', backgroundColor: P.surface }}>hell</motion.span>
            <motion.span data-themedark whileTap={{ scale: 0.94 }} transition={SPRING}
              animate={{ borderColor: dark ? P.ink : P.line, backgroundColor: dark ? P.ink : P.surface, color: dark ? P.bg : P.ink }}
              onClick={() => setDark(true)}
              style={{ padding: '3px 9px', border: `1px solid ${P.line}`, cursor: 'pointer', backgroundColor: P.surface }}>dunkel</motion.span>
          </span>
        )}
      </div>
      <div style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, marginTop: 3 }}>{note}</div>
    </div>
  )
}

/* Aeussere Buehne mit data-stage als Rahmen. */
function Stage({ name, note, dark, setDark, toggleable, children }) {
  useBarrierefrei()   // A1 reduzierte Bewegung + A5 Tastatur — erste Zeile jeder Buehne
  const P = dark ? DARK : LIGHT
  return (
    <div data-stage style={{ padding: 20, background: P.bg, fontFamily: SANS, color: P.ink, width: '100%', maxWidth: 880, boxSizing: 'border-box' }}>
      <StageHead name={name} note={note} dark={dark} setDark={setDark} toggleable={toggleable} P={P} />
      {children(P)}
    </div>
  )
}

/* Buehne plus Manager: verdrahtet den echten Zustand und den Themen-Umschalter. */
function Managed({ name, note, render, dark0 = false, toggleable = true }) {
  const [dark, setDark] = useState(dark0)
  const m = useManager()
  const text = `${note} · ${m.count}/7 an · ${m.openItems} offene Elemente`
  return (
    <Stage name={name} note={text} dark={dark} setDark={setDark} toggleable={toggleable}>
      {(P) => render(m, P, dark)}
    </Stage>
  )
}

/* Kopfzeile im Fensterrahmen — Zaehler live. */
const ManHead = ({ m, P, children }) => (
  <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 12px', borderBottom: `1px solid ${P.line}`, background: P.surface }}>
    <span style={{ fontFamily: MONO, fontSize: TEXT.meta, letterSpacing: 1, color: P.sub }}>PANELS</span>
    <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.ink }}>{m.count}/7 an</span>
    <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>· {m.openItems} offene Elemente</span>
    {children && <span style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 6 }}>{children}</span>}
  </div>
)

/* Live-Punkt: atmet nur bei arbeitenden Panels. */
const LiveDot = ({ on, working, P }) => (
  <span style={{ width: 8, display: 'inline-flex', justifyContent: 'center', flex: '0 0 auto' }}>
    {on && working ? (
      <motion.span animate={{ opacity: [1, 0.25, 1], scale: [1, 0.78, 1] }} transition={{ duration: 1.6, repeat: Infinity, ease: 'easeInOut' }}
        style={{ width: 5, height: 5, borderRadius: RADIUS.pill, background: P.ink }} />
    ) : (
      <span style={{ width: 5, height: 5, borderRadius: RADIUS.pill, background: P.faint, opacity: on ? 0.65 : 0.2 }} />
    )}
  </span>
)

/* Zahlen-Anzeige, die beim Wechsel mit Feder springt. */
const Count = ({ value, hide = false, P }) => (
  <motion.span animate={{ opacity: hide ? 0.25 : 1 }} transition={SPRING} style={{ display: 'inline-flex', fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, whiteSpace: 'nowrap' }}>
    <AnimatePresence mode="popLayout" initial={false}>
      {/* Schluessel ist bewusst der WERT: der Wechsel selbst ist die Animation (kein Listenindex). */}
      <motion.span key={String(value)} initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 6 }} transition={SPRING}>
        {value}
      </motion.span>
    </AnimatePresence>
  </motion.span>
)

/* ------------------------------------------------- 01 · Liste mit Schaltern */

const ListRow = ({ p, on, last, toggle, P, i = 0 }) => (
  <motion.div data-panelrow={p.id} data-checked={on ? 'true' : 'false'} layout
    initial={{ opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }} transition={{ ...SPRING, delay: i * 0.04 }}
    style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 10px', borderBottom: `1px solid ${P.hair}` }}>
    <motion.span animate={{ backgroundColor: on ? P.ink : 'transparent', color: on ? P.bg : P.ink }} transition={{ duration: 0.16 }}
      style={{ width: 34, height: 34, border: `1px solid ${P.line}`, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', flex: '0 0 auto' }}>
      <p.Icon size={15} strokeWidth={1.8} />
    </motion.span>
    <span style={{ flex: 1, minWidth: 0 }}>
      <span style={{ display: 'block', fontFamily: SANS, fontSize: 12.5, fontWeight: on ? 600 : 500, color: P.ink }}>{p.name}</span>
      <span style={{ display: 'block', fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{p.desc}</span>
    </span>
    <LiveDot on={on} working={p.working} P={P} />
    <Count value={on ? `${p.items} offen` : '0 offen'} hide={!on} P={P} />
    <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint, width: 84, textAlign: 'right', whiteSpace: 'nowrap' }}>
      {last ? `${fmtAgo(last, Date.now())} her` : '—'}
    </span>
    <Switch value={on} onChange={() => toggle(p.id)} P={P} />
  </motion.div>
)

export function V01() {
  return (
    <Managed name="01 · Liste mit Schaltern" note="echter Zustand — Klick schaltet wirklich um"
      render={(m, P) => (
        <div data-frame style={{ border: `1px solid ${P.line}`, background: P.surface }}>
          <div style={{ padding: '6px 10px', borderBottom: `1px solid ${P.line}` }}>
            <span style={{ fontFamily: MONO, fontSize: TEXT.meta, letterSpacing: 1, color: P.sub }}>ALLE PANELS · KLICKEN SCHALTET</span>
          </div>
          {PANELS.map((p, i) => <ListRow key={p.id} p={p} on={m.on[p.id]} last={m.last[p.id]} toggle={m.toggle} P={P} i={i} />)}
        </div>
      )} />
  )
}

/* ------------------------------------------------- 02 · Raster/Kacheln, 2 Spalten */

const Tile = ({ p, on, last, toggle, P }) => (
  <motion.div data-tile={p.id} data-checked={on ? 'true' : 'false'} layout
    initial={{ opacity: 0, scale: 0.96 }} animate={{ opacity: 1, scale: 1 }} transition={SPRING}
    onClick={() => toggle(p.id)} whileHover={{ y: -2 }} whileTap={{ scale: 0.98 }}
    style={{ border: `1px solid ${on ? P.ink : P.line}`, background: on ? P.surface : P.hair, padding: 10, cursor: 'pointer', userSelect: 'none' }}>
    <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
      {/* Kasten zum Ankreuzen */}
      <motion.span data-checkbox whileTap={{ scale: 0.8 }} transition={SPRING}
        animate={{ backgroundColor: on ? P.ink : 'transparent', borderColor: on ? P.ink : P.line }}
        style={{ width: 15, height: 15, border: `1px solid ${P.line}`, flex: '0 0 auto' }}>
        <AnimatePresence initial={false}>
          {on && (
            <motion.span key="haken" initial={{ opacity: 0, scale: 0.3 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0, scale: 0.3 }} transition={SPRING}
              style={{ display: 'flex', width: '100%', height: '100%', alignItems: 'center', justifyContent: 'center', color: P.bg }}>
              <IconCheck size={10} stroke={3} />
            </motion.span>
          )}
        </AnimatePresence>
      </motion.span>
      <motion.span animate={{ color: on ? P.ink : P.faint }} transition={{ duration: 0.16 }} style={{ display: 'inline-flex' }}>
        <p.Icon size={14} strokeWidth={1.8} />
      </motion.span>
      <span style={{ fontFamily: SANS, fontSize: 11.5, fontWeight: 600, color: P.ink }}>{p.name}</span>
      <span style={{ marginLeft: 'auto', fontFamily: MONO, fontSize: TEXT.meta, color: on ? P.ink : P.faint }}><Count value={on ? p.items : 0} P={P} /></span>
    </div>
    <div style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, marginTop: 6, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{p.desc}</div>
    <div style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint, marginTop: 4 }}>
      {last ? `wechsel vor ${fmtAgo(last, Date.now())}` : 'wechsel —'} · {p.working ? 'arbeitet' : 'ruht'}
    </div>
  </motion.div>
)

export function V02() {
  return (
    <Managed name="02 · Raster mit Kaestchen zum Ankreuzen" note="2 Spalten — ganze Kachel klickt um"
      render={(m, P) => (
        <div data-frame style={{ border: `1px solid ${P.line}`, background: P.bg, padding: 12, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, alignContent: 'start', height: 430, boxSizing: 'border-box', overflow: 'auto' }}>
          {PANELS.map(p => <Tile key={p.id} p={p} on={m.on[p.id]} last={m.last[p.id]} toggle={m.toggle} P={P} />)}
        </div>
      )} />
  )
}

/* ------------------------------------------------- 03 · Leiste am unteren Rand */

const BarCell = ({ p, on, toggle, P }) => (
  <motion.span layout data-barcell={p.id} data-checked={on ? 'true' : 'false'}
    onClick={() => toggle(p.id)} whileHover={{ y: -2 }} whileTap={{ scale: 0.94 }} transition={SPRING}
    title={p.name}
    animate={{ borderColor: on ? P.ink : P.line, backgroundColor: on ? P.surface : P.bg }}
    style={{ display: 'inline-flex', alignItems: 'center', gap: 5, padding: '5px 9px', border: `1px solid ${P.line}`, cursor: 'pointer', fontFamily: MONO, fontSize: TEXT.meta }}>
    <motion.span animate={{ color: on ? P.ink : P.faint }} transition={{ duration: 0.16 }} style={{ display: 'inline-flex' }}>
      <p.Icon size={12} strokeWidth={1.9} />
    </motion.span>
    <span style={{ color: on ? P.ink : P.ink }}>{p.short}</span>
    <Count value={on ? p.items : 0} hide={!on} P={P} />
    <LiveDot on={on} working={p.working} P={P} />
  </motion.span>
)

export function V03() {
  return (
    <Managed name="03 · Leiste am unteren Rand" note="waagerecht, kompakt, mit Symbolen"
      render={(m, P) => (
        <div data-frame style={{ height: 430, border: `1px solid ${P.line}`, background: P.bg, position: 'relative', display: 'flex', flexDirection: 'column' }}>
          <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>Arbeitsflaeche · {m.openItems} offene Elemente aus {m.count}/7 Panels</span>
          </div>
          <div style={{ borderTop: `1px solid ${P.line}`, background: P.surface, padding: 8, display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
            {PANELS.map(p => <BarCell key={p.id} p={p} on={m.on[p.id]} toggle={m.toggle} P={P} />)}
          </div>
        </div>
      )} />
  )
}

/* ------------------------------------------------- 04 · Rail rechts am Rand */

function Rail({ m, P }) {
  const [wide, setWide] = useState(false)
  return (
    <motion.div data-rail layout animate={{ width: wide ? 178 : 36 }} transition={SPRING}
      style={{ position: 'absolute', top: 0, right: 0, bottom: 0, borderLeft: `1px solid ${P.line}`, background: P.surface, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      <motion.span data-railgrip onClick={() => setWide(w => !w)} whileTap={{ scale: 0.9 }} transition={SPRING}
        title={wide ? 'einklappen' : 'ausklappen'}
        style={{ height: 26, borderBottom: `1px solid ${P.hair}`, display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer', color: P.sub, flex: '0 0 auto' }}>
        <motion.span animate={{ rotate: wide ? 180 : 0 }} transition={SPRING} style={{ display: 'inline-flex' }}>
          <IconChevronsRight size={12} stroke={1.8} />
        </motion.span>
      </motion.span>
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', justifyContent: 'center', gap: 4, padding: '0 4px 6px' }}>
        {PANELS.map(p => {
          const on = m.on[p.id]
          return (
            <motion.span key={p.id} layout data-railrow={p.id} data-checked={on ? 'true' : 'false'}
              onClick={() => m.toggle(p.id)} whileTap={{ scale: 0.92 }} transition={SPRING} title={p.name}
              animate={{ borderColor: on ? P.ink : P.line, backgroundColor: on ? P.ink : P.surface, color: on ? P.bg : P.sub }}
              style={{ display: 'flex', alignItems: 'center', gap: 6, height: 26, border: `1px solid ${P.line}`, cursor: 'pointer', overflow: 'hidden', fontFamily: MONO, fontSize: TEXT.meta, flex: '0 0 auto' }}>
              <span style={{ width: 26, textAlign: 'center', flex: '0 0 auto' }}>{p.short}</span>
              <AnimatePresence>
                {wide && (
                  <motion.span key="name" initial={{ opacity: 0, x: 4 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: 4 }} transition={SPRING}
                    style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{p.name}</motion.span>
                )}
              </AnimatePresence>
              {wide && <span style={{ marginLeft: 'auto', paddingRight: 6 }}>{on ? p.items : 0}</span>}
            </motion.span>
          )
        })}
      </div>
    </motion.div>
  )
}

export function V04() {
  return (
    <Managed name="04 · Rail rechts am Rand" note="schmal, nur Kuerzel — Grip klappt aus"
      render={(m, P) => (
        <div data-frame style={{ height: 430, border: `1px solid ${P.line}`, background: P.bg, position: 'relative', overflow: 'hidden' }}>
          <div style={{ position: 'absolute', inset: '0 36px 0 0', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint, textAlign: 'center' }}>
              Arbeitsflaeche<br />{m.openItems} offene Elemente aus {m.count}/7 Panels
            </span>
          </div>
          <Rail m={m} P={P} />
        </div>
      )} />
  )
}

/* ------------------------------------------------- 05 · Popover aus der Chat-Bar */

function ChatBarPopover({ m, P }) {
  const [open, setOpen] = useState(true)
  const [q, setQ] = useState('')
  const shown = PANELS.filter(p => !q || p.name.toLowerCase().includes(q.toLowerCase()) || p.id.includes(q.toLowerCase()))
  return (
    <div data-frame style={{ height: 430, border: `1px solid ${P.line}`, background: P.bg, position: 'relative', display: 'flex', flexDirection: 'column' }}>
      <div style={{ flex: 1, padding: 14, display: 'flex', flexDirection: 'column', gap: 8, justifyContent: 'flex-end' }}>
        <span style={{ alignSelf: 'flex-end', fontFamily: MONO, fontSize: 11.5, backgroundColor: P.ink, color: P.bg, padding: '5px 9px' }}>zeige mir die panels</span>
      </div>
      {/* Chat-Bar mit Knopf */}
      <div style={{ padding: 10 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, height: 54, borderRadius: RADIUS.pill, border: `1px solid ${P.line}`, background: P.surface, padding: '0 14px' }}>
          <motion.span data-panelbutton onClick={() => setOpen(o => !o)} whileHover={{ scale: 1.12, rotate: 90 }} whileTap={{ scale: 0.9 }} transition={SPRING}
            title="Panels umschalten"
            animate={{ backgroundColor: open ? P.ink : 'transparent', color: open ? P.bg : P.ink }}
            style={{ width: 30, height: 30, borderRadius: RADIUS.pill, border: `1px solid ${P.line}`, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer' }}>
            <IconLayoutGrid size={14} stroke={1.9} />
          </motion.span>
          <span style={{ flex: 1, fontFamily: SANS, fontSize: 14, color: P.faint }}>Start with a goal</span>
          <motion.span animate={{ opacity: m.count > 0 ? 1 : 0.4 }} style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub }}>{m.count}/7</motion.span>
        </div>
      </div>
      {/* Das Popover waechst aus dem Knopf */}
      <AnimatePresence>
        {open && (
          <motion.div data-panelpopover key="pop" initial={{ opacity: 0, scale: 0.92, y: 8 }} animate={{ opacity: 1, scale: 1, y: 0 }} exit={{ opacity: 0, scale: 0.94, y: 6 }}
            transition={SPRING} style={{ position: 'absolute', left: 22, bottom: 78, width: 330, border: `1px solid ${P.line}`, background: P.surface, boxShadow: '0 14px 34px rgba(0,0,0,0.22)'   /* rgba nur als Schatten */, zIndex: 5, transformOrigin: 'bottom left' }}>
            <ManHead m={m} P={P} />
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '6px 10px', borderBottom: `1px solid ${P.hair}` }}>
              <IconSearch size={11} stroke={1.9} color={P.sub} />
              <input data-paneleditor value={q} onChange={e => setQ(e.target.value)} placeholder="Panel suchen"
                style={{ flex: 1, border: 'none', outline: 'none', background: 'transparent', fontFamily: MONO, fontSize: TEXT.meta, color: P.ink }} />
              {q && <IconX size={11} stroke={2} color={P.faint} onClick={() => setQ('')} style={{ cursor: 'pointer' }} />}
            </div>
            <div style={{ maxHeight: 240, overflow: 'auto' }}>
              {shown.map((p, i) => <ListRow key={p.id} p={p} on={m.on[p.id]} last={m.last[p.id]} toggle={m.toggle} P={P} i={i} />)}
              {shown.length === 0 && <div style={{ padding: 10, fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>kein Panel passt zu „{q}“</div>}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

export function V05() {
  return <Managed name="05 · Popover aus dem Chat-Bar-Knopf" note="Knopf in der Leiste oeffnet das Popup" render={(m, P) => <ChatBarPopover m={m} P={P} />} />
}

/* ------------------------------------------------- 06 · Tabelle */

const TableHead = ({ P }) => (
  <div style={{ display: 'flex', gap: 10, padding: '6px 10px', borderBottom: `1px solid ${P.line}`, fontFamily: MONO, fontSize: TEXT.meta, letterSpacing: 1, color: P.sub }}>
    <span style={{ flex: 1 }}>PANEL</span><span style={{ width: 64 }}>ZUSTAND</span><span style={{ width: 96 }}>OFFENE ELEMENTE</span><span style={{ width: 92 }}>LETZTER WECHSEL</span><span style={{ width: 40 }} />
  </div>
)

const TableLine = ({ p, on, last, toggle, P, i = 0 }) => (
  <motion.div data-tableline={p.id} data-checked={on ? 'true' : 'false'} layout
    initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} transition={{ ...SPRING, delay: i * 0.04 }}
    style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '7px 10px', borderBottom: `1px solid ${P.hair}` }}>
    <span style={{ flex: 1, display: 'flex', alignItems: 'center', gap: 7, minWidth: 0 }}>
      <LiveDot on={on} working={p.working} P={P} />
      <motion.span animate={{ color: on ? P.ink : P.faint }} transition={{ duration: 0.16 }} style={{ display: 'inline-flex' }}><p.Icon size={13} strokeWidth={1.8} /></motion.span>
      <motion.span animate={{ opacity: on ? 1 : 0.55 }} transition={SPRING} style={{ fontFamily: MONO, fontSize: 11, color: P.ink, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
        {p.name}<span style={{ color: P.faint, fontSize: TEXT.meta }}> — {p.desc}</span>
      </motion.span>
    </span>
    <motion.span animate={{ backgroundColor: on ? P.ink : 'transparent', color: on ? P.bg : P.ink }} transition={SPRING}
      style={{ width: 64, textAlign: 'center', border: `1px solid ${P.line}`, fontFamily: MONO, fontSize: TEXT.meta, padding: '2px 0', flex: '0 0 auto' }}>
      {on ? 'AN' : 'AUS'}
    </motion.span>
    <span style={{ width: 96, fontFamily: MONO, fontSize: TEXT.meta, color: P.ink, flex: '0 0 auto' }}>
      <Count value={`${on ? p.items : 0} von ${p.items}`} P={P} />
    </span>
    <span style={{ width: 92, fontFamily: MONO, fontSize: TEXT.meta, color: P.faint, whiteSpace: 'nowrap', flex: '0 0 auto' }}>
      {last ? fmtClock(last) : '—'}
    </span>
    <span style={{ width: 40, textAlign: 'right', flex: '0 0 auto' }}>
      <Switch mini value={on} onChange={() => toggle(p.id)} P={P} />
    </span>
  </motion.div>
)

export function V06() {
  return (
    <Managed name="06 · Tabelle: Panel · Zustand · offene Elemente · letzter Wechsel" note="jede Zeile spiegelt den echten Zustand"
      render={(m, P) => (
        <div data-frame style={{ border: `1px solid ${P.line}`, background: P.surface }}>
          <TableHead P={P} />
          {PANELS.map((p, i) => <TableLine key={p.id} p={p} on={m.on[p.id]} last={m.last[p.id]} toggle={m.toggle} P={P} i={i} />)}
        </div>
      )} />
  )
}

/* ------------------------------------------------- 07 · Kacheln mit Zaehler und Live-Punkt */

export function V07() {
  return (
    <Managed name="07 · Kacheln mit Zaehler und Live-Punkt" note="arbeitende Panels atmen — Klick schaltet um"
      render={(m, P) => (
        <div data-frame style={{ border: `1px solid ${P.line}`, background: P.bg, padding: 12, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, alignContent: 'start', height: 430, boxSizing: 'border-box', overflow: 'auto' }}>
          {PANELS.map(p => (
            <motion.div key={p.id} data-livetile={p.id} data-checked={m.on[p.id] ? 'true' : 'false'} layout
              initial={{ opacity: 0, scale: 0.96 }} animate={{ opacity: 1, scale: 1 }} transition={SPRING}
              onClick={() => m.toggle(p.id)} whileHover={{ y: -2 }} whileTap={{ scale: 0.98 }}
              style={{ border: `1px solid ${m.on[p.id] ? P.ink : P.line}`, background: P.surface, padding: '10px 11px', cursor: 'pointer', userSelect: 'none' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
                <LiveDot on={m.on[p.id]} working={p.working} P={P} />
                <span style={{ display: 'inline-flex', color: P.ink }}><p.Icon size={14} strokeWidth={1.8} /></span>
                <span style={{ fontFamily: SANS, fontSize: 11.5, fontWeight: 600, color: P.ink }}>{p.name}</span>
                <span style={{ marginLeft: 'auto', fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>{p.working ? 'arbeitet' : 'ruht'}</span>
              </div>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 5, marginTop: 8 }}>
                <span style={{ fontFamily: MONO, fontSize: 22, fontWeight: 600, color: m.on[p.id] ? P.ink : P.faint }}>
                  <Count value={m.on[p.id] ? p.items : 0} P={P} />
                </span>
                <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>offene Elemente</span>
              </div>
              <div style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint, marginTop: 4 }}>
                {m.on[p.id] ? 'Panel ist an' : 'Panel ist aus'}{m.last[p.id] ? ` · wechsel vor ${fmtAgo(m.last[p.id], Date.now())}` : ''}
              </div>
            </motion.div>
          ))}
        </div>
      )} />
  )
}

/* ------------------------------------------------- 08 · Suche/Filter + alle an/aus */

export function V08() {
  const [q, setQ] = useState('')
  return (
    <Managed name="08 · Suche und Filterzeile" note="alle an / alle aus wirken auf den echten Zustand"
      render={(m, P) => {
        const shown = PANELS.filter(p => !q || p.name.toLowerCase().includes(q.toLowerCase()) || p.desc.toLowerCase().includes(q.toLowerCase()))
        return (
          <div data-frame style={{ border: `1px solid ${P.line}`, background: P.surface }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 7, padding: '8px 10px', borderBottom: `1px solid ${P.hair}` }}>
              <IconSearch size={12} stroke={1.9} color={P.sub} />
              <input data-panelq value={q} onChange={e => setQ(e.target.value)} placeholder="Panel filtern …"
                style={{ flex: 1, border: 'none', outline: 'none', background: 'transparent', fontFamily: MONO, fontSize: 11, color: P.ink }} />
              {q && <IconX size={12} stroke={2} color={P.faint} onClick={() => setQ('')} style={{ cursor: 'pointer' }} />}
              <span style={{ width: 1, height: 16, background: P.hair }} />
              <motion.span data-allon whileTap={{ scale: 0.94 }} transition={SPRING} onClick={() => m.setAll(true)} style={{ padding: '3px 8px', border: `1px solid ${P.line}`, background: P.bg, fontFamily: MONO, fontSize: TEXT.meta, cursor: 'pointer', color: P.ink }}>alle an</motion.span>
              <motion.span data-alloff whileTap={{ scale: 0.94 }} transition={SPRING} onClick={() => m.setAll(false)} style={{ padding: '3px 8px', border: `1px solid ${P.line}`, background: P.bg, fontFamily: MONO, fontSize: TEXT.meta, cursor: 'pointer', color: P.ink }}>alle aus</motion.span>
            </div>
            {shown.map((p, i) => <ListRow key={p.id} p={p} on={m.on[p.id]} last={m.last[p.id]} toggle={m.toggle} P={P} i={i} />)}
            {shown.length === 0 && <div style={{ padding: 10, fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>nichts passt zu „{q}“</div>}
          </div>
        )
      }} />
  )
}

/* ------------------------------------------------- 09 · Mini-Zeile, sieben Kaestchen */

export function V09() {
  return (
    <Managed name="09 · Mini-Zeile: sieben Kaestchen zum Sofort-Umschalten" note="eine Zeile, sehr kompakt" toggleable={false}
      render={(m, P) => (
        <div data-frame style={{ height: 430, border: `1px solid ${P.line}`, background: P.bg, position: 'relative', display: 'flex', flexDirection: 'column' }}>
          <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>ausgeschaltete Panels sind verzogen — {m.count}/7 an</span>
          </div>
          <div style={{ borderTop: `1px solid ${P.line}`, background: P.surface, padding: '8px 10px' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, justifyContent: 'center' }}>
              {PANELS.map((p) => (
                <motion.span key={p.id} layout data-minibox={p.id} data-checked={m.on[p.id] ? 'true' : 'false'}
                  onClick={() => m.toggle(p.id)} title={p.name} whileHover={{ scale: 1.1 }} whileTap={{ scale: 0.85 }} transition={SPRING}
                  animate={{ borderColor: m.on[p.id] ? P.ink : P.line, backgroundColor: m.on[p.id] ? P.ink : P.surface }}
                  style={{ width: 30, height: 30, border: `1px solid ${P.line}`, display: 'inline-flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 1, cursor: 'pointer', color: m.on[p.id] ? P.bg : P.sub }}>
                  <p.Icon size={13} strokeWidth={1.9} />
                  <span style={{ fontFamily: MONO, fontSize: TEXT.meta }}>{m.on[p.id] ? p.items : 0}</span>
                </motion.span>
              ))}
            </div>
            <div data-minireadout style={{ marginTop: 7, fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, textAlign: 'center' }}>
              {m.openItems} offene Elemente · zeige Namen beim Hover
            </div>
          </div>
        </div>
      )} />
  )
}

/* ------------------------------------------------- 10 · Dunkelfassung ( Favorit) */

export default {
  '01 · Panel-Manager: Liste mit Schaltern': () => <V01 />,
  '02 · Panel-Manager: Raster mit Kaestchen zum Ankreuzen': () => <V02 />,
  '03 · Panel-Manager: Leiste am unteren Rand': () => <V03 />,
  '04 · Panel-Manager: Rail rechts am Rand': () => <V04 />,
  '05 · Panel-Manager: Popover aus dem Chat-Bar-Knopf': () => <V05 />,
  '06 · Panel-Manager: Tabelle': () => <V06 />,
  '07 · Panel-Manager: Kacheln mit Zaehler und Live-Punkt': () => <V07 />,
  '08 · Panel-Manager: Suche und Filterzeile': () => <V08 />,
  '09 · Panel-Manager: Mini-Zeile mit sieben Kaestchen': () => <V09 />,
  '10 · Panel-Manager: Dunkelfassung (Vollbild)': () => <V10 />
}

export function V10() {
  return <Managed name="10 · Dunkelfassung (Vollbild)" note="die Liste als dunkle Vollbild-Fassung" dark0 toggleable={false}
    render={(m, P) => (
      <div data-frame style={{ height: 430, border: `1px solid ${P.line}`, background: P.bg }}>
        {PANELS.map((p, i) => <ListRow key={p.id} p={p} on={m.on[p.id]} last={m.last[p.id]} toggle={m.toggle} P={P} i={i} />)}
      </div>
    )} />
}
