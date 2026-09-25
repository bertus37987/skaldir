/**
 * Chat-Bar — die GEWÄHLTE Fassung, mit Darkmode und Datei-Upload.
 *
 * Owner-Vorgabe 17.09.2026:
 *   1) zu jedem bestehenden Baustein einen IDENTISCHEN Darkmode,
 *   2) für den Chat ein Datei-Upload-Beispiel: Pfad, Vorschau und Endung,
 *      dazu eine Live-Zusammenfassung des Uploads,
 *   3) eine Fehlermeldung, wenn der Anbieter ein Upload-Limit hat.
 *
 * Aufbau der Leiste wie die Referenz: flache, voll gerundete Pille, links "+" und
 * "Start with a goal", rechts Punkteraster, "Med" und der Modell-&-Reasoning-Selector.
 * Darüber die Goal-Bar. Nur Schwarz/Weiß/Grau.
 *
 * Bewegung (Motion): Goal-Baum und Unterbäume klappen mit Feder auf und zu, Auswahlpunkte
 * setzen sich mit Feder, das Selector-Popover wächst aus dem Knopf, Uploadzeilen fahren
 * versetzt ein, der Fortschrittsbalken laeuft von 0 auf den Wert, Haken und Kreuz springen auf.
 *
 * Befunde 18.09.2026: Paletten und Groessen kommen aus _tokens (keine eigenen Hexwerte),
 * useBarrierefrei() schaltet reduzierte Bewegung ab und macht die Zeigerziele per
 * Enter/Leertaste bedienbar, data-stage sitzt am aeusseren Rahmen jeder Variante,
 * Schriftuntergrenze 11 px, eine einzige Radustabelle.
 */
import { motion, AnimatePresence, animate } from 'motion/react'
import React, { useState, useRef, useEffect } from 'react'
import { IconChevronDown, IconLayoutNavbarCollapse, IconLayoutNavbarExpand } from '@tabler/icons-react'
import { LIGHT, DARK, MONO, SANS, SPRING, RADIUS, TEXT } from './_tokens'
import { useBarrierefrei } from './_barrierefrei'

// @ts-expect-error — Laufzeit-Alias (siehe vite.config.ts)
import { ThinkingOrb } from 'thinking-orbs'

const MODELS = [
  { id: 'm1', name: 'opencode', sub: 'free' },
  { id: 'm2', name: 'klarer', sub: 'local' },
  { id: 'm3', name: 'waehler', sub: 'remote' }
]
const LEVELS = ['Low', 'Med', 'High']

const IconPlus = ({ P }) => <span style={{ fontSize: 17, color: P.faint, lineHeight: 1 }}>+</span>
const DotGrid = ({ P }) => (
  <span style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 3px)', gap: 2 }}>
    {/* statisch: Indexschluessel ok */}
    {Array.from({ length: 9 }).map((_, i) => (
      <motion.span key={i} animate={{ opacity: [0.55, 1, 0.55] }} transition={{ duration: 2.6, repeat: Infinity, ease: 'easeInOut', delay: i * 0.12 }}
        style={{ width: 3, height: 3, borderRadius: RADIUS.pill, background: P.faint }} />
    ))}
  </span>
)
const Wave = ({ P, size = 30 }) => (
  <span style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: size, height: size, borderRadius: RADIUS.pill, background: P.ink }}>
    <svg width={size * 0.55} height={size * 0.4} viewBox="0 0 18 12">
      <motion.polyline points="0,6 3,6 5,2 7,10 9,4 11,7 13,3 15,6 18,6" fill="none" stroke={P.surface} strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round"
        initial={{ pathLength: 0 }} animate={{ pathLength: 1 }} transition={{ duration: 1.1, ease: 'easeInOut' }} />
    </svg>
  </span>
)
const Med = ({ P }) => (
  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 12.5, color: P.faint }}>Med<IconChevronDown size={11} stroke={2} color={P.faint} /></span>
)

function Selector({ P }) {
  const [open, setOpen] = useState(false)
  const [model, setModel] = useState(MODELS[0])
  const [level, setLevel] = useState('Med')
  return (
    <span style={{ position: 'relative', display: 'inline-flex', alignItems: 'center', gap: 4 }}>
      <motion.span data-selector onClick={() => setOpen(o => !o)} title="Modell und Reasoning waehlen" whileHover={{ scale: 1.04 }} whileTap={{ scale: 0.94 }} transition={SPRING}
        style={{ display: 'inline-flex', alignItems: 'center', gap: 4, cursor: 'pointer' }}>
        <Wave P={P} />
        <motion.span animate={{ rotate: open ? 180 : 0 }} transition={SPRING} style={{ display: 'inline-flex' }}>
          <IconChevronDown size={12} stroke={2.2} color={P.ink} />
        </motion.span>
      </motion.span>
      <AnimatePresence>
        {open && (
          <motion.div data-selectorpopover key="pop" initial={{ opacity: 0, scale: 0.94, y: 8 }} animate={{ opacity: 1, scale: 1, y: 0 }} exit={{ opacity: 0, scale: 0.96, y: 6 }}
            transition={SPRING} style={{ position: 'absolute', right: 0, bottom: 42, width: 226, padding: 10, borderRadius: RADIUS.dialog, background: P.surface, border: `1px solid ${P.line}`, boxShadow: '0 14px 34px rgba(0,0,0,0.24)', zIndex: 5, transformOrigin: 'bottom right' }}>
            <div style={{ fontFamily: MONO, fontSize: 11, letterSpacing: 1, color: P.faint, marginBottom: 6 }}>MODELL</div>
            {MODELS.map((m, i) => (
              <motion.div key={m.id} onClick={() => { setModel(m); setOpen(false) }} data-model={m.id}
                initial={{ opacity: 0, x: -4 }} animate={{ opacity: 1, x: 0 }} transition={{ ...SPRING, delay: 0.03 + i * 0.04 }}
                whileHover={{ x: 2 }} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '5px 6px', cursor: 'pointer', background: model.id === m.id ? P.soft : 'transparent' }}>
                <motion.span animate={{ opacity: model.id === m.id ? 1 : 0.25, scale: model.id === m.id ? 1 : 0.8 }} transition={SPRING}
                  style={{ width: 6, height: 6, borderRadius: RADIUS.pill, background: P.ink }} />
                <span style={{ fontSize: 12.5, color: P.ink }}>{m.name}</span>
                <span style={{ marginLeft: 'auto', fontFamily: MONO, fontSize: 11, color: P.faint }}>{m.sub}</span>
              </motion.div>
            ))}
            <div style={{ fontFamily: MONO, fontSize: 11, letterSpacing: 1, color: P.faint, margin: '9px 0 6px' }}>REASONING</div>
            <div style={{ display: 'flex', gap: 6 }}>
              {LEVELS.map(l => (
                <motion.span key={l} onClick={() => { setLevel(l); setOpen(false) }} data-level={l} whileHover={{ scale: 1.03 }} whileTap={{ scale: 0.95 }} transition={SPRING}
                  animate={{ borderColor: l === level ? P.ink : P.line, color: P.ink }}
                  style={{ flex: 1, textAlign: 'center', padding: '4px 0', border: `1px solid ${P.line}`, fontSize: 11.5, cursor: 'pointer' }}>{l}</motion.span>
              ))}
            </div>
            <div style={{ marginTop: 9, fontFamily: MONO, fontSize: 11, color: P.faint }}>gilt fuer die naechste Nachricht · {model.name} · {level}</div>
          </motion.div>
        )}
      </AnimatePresence>
    </span>
  )
}

const GOAL = [
  { id: 'a', label: 'Nachweise sind messbar', children: [
    { id: 'a1', label: 'Anker-Pruefung', done: true, note: '28/28' },
    { id: 'a2', label: 'Overlay-Lint', done: true, note: '0 Befunde' }] },
  { id: 'b', label: 'Echter Nutzerweg laeuft', children: [
    { id: 'b1', label: 'Composer → Session → Senden', done: true },
    { id: 'b2', label: 'Antwort rendert im Transkript' },
    { id: 'b3', label: 'Fehlerfall hat einen Ausweg', children: [
      { id: 'b31', label: 'Fehlerkarte zeigt Text und Ort', done: true },
      { id: 'b32', label: 'Wiederholen ohne Modellwartezeit' }] }] },
  { id: 'c', label: 'Zustaende bleiben erhalten', children: [
    { id: 'c1', label: 'Reload stellt Session her', done: true },
    { id: 'c3', label: 'Baum ueberlebt Neustart' }] }
]
const leaves = (n) => (n.children && n.children.length ? n.children.flatMap(leaves) : [n])
const TOTAL = GOAL.flatMap(leaves).reduce((a, n) => ({ done: a.done + (n.done ? 1 : 0), total: a.total + 1 }), { done: 0, total: 0 })
const OPEN_DEFAULT = new Set(['b', 'b3'])

function Row({ node, ancestors, isLast, openMap, toggle, P }) {
  const kids = node.children || []
  const done = leaves(node).every(l => l.done === true)
  const depth = ancestors.length
  const open = openMap[node.id] !== undefined ? openMap[node.id] : OPEN_DEFAULT.has(node.id)
  const soft = done ? { opacity: 0.6, filter: 'blur(0.3px)' } : {}
  return (
    <div>
      <motion.div initial={{ opacity: 0, x: -3 }} animate={{ opacity: 1, x: 0 }} transition={{ ...SPRING, delay: depth * 0.03 }}
        style={{ display: 'flex', alignItems: 'center', minHeight: 24, whiteSpace: 'nowrap' }}>
        <span style={{ fontFamily: MONO, fontSize: 11.5, whiteSpace: 'pre', color: P.ink, ...soft }}>
          {/* statisch: Indexschluessel ok */}
          {ancestors.map((a, i) => <span key={i}>{a.last ? '   ' : '│  '}</span>)}{isLast ? '└─ ' : '├─ '}
        </span>
        <motion.span onClick={() => kids.length && toggle(node.id)} whileTap={kids.length ? { scale: 0.82 } : undefined} transition={SPRING}
          style={{ width: 13, fontSize: 11, color: P.ink, cursor: kids.length ? 'pointer' : 'default' }}>
          {kids.length ? (open ? '▾' : '▸') : ''}
        </motion.span>
        <span style={{ fontFamily: SANS, fontSize: depth === 0 ? 12.5 : 12, fontWeight: depth === 0 ? 600 : 400, color: P.ink, ...soft }}>{node.label}</span>
        <motion.span animate={{ backgroundColor: done ? P.ink : 'transparent', scale: done ? 1 : 0.96 }} transition={SPRING}
          style={{ marginLeft: 7, width: 12, height: 12, border: `1.3px solid ${P.ink}`, color: P.surface, fontSize: 11, lineHeight: '11px', textAlign: 'center', flex: '0 0 auto' }}>
          <AnimatePresence>{done && <motion.span initial={{ opacity: 0, scale: 0.4 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0, scale: 0.4 }} transition={SPRING} style={{ display: 'block' }}>✓</motion.span>}</AnimatePresence>
        </motion.span>
        {node.note && <span style={{ fontFamily: MONO, fontSize: 11, marginLeft: 7, color: P.ink, opacity: done ? 0.6 : 0.8 }}>{node.note}</span>}
        {node.id === 'b' && depth === 0 && (
          <span style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            <motion.span animate={{ opacity: [0.55, 1, 0.55] }} transition={{ duration: 2.2, repeat: Infinity, ease: 'easeInOut' }} style={{ fontFamily: MONO, fontSize: 11, color: P.sub }}>momentan</motion.span>
            <ThinkingOrb state="working" size={20} theme="light" />
          </span>
        )}
      </motion.div>
      <AnimatePresence initial={false}>
        {open && kids.length > 0 && (
          <motion.div key="kids" initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }} transition={SPRING} style={{ overflow: 'hidden' }}>
            {kids.map((k, i) => (
              <Row key={k.id} node={k} ancestors={[...ancestors, { last: isLast }]} isLast={i === kids.length - 1} openMap={openMap} toggle={toggle} P={P} />
            ))}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

function GoalBar({ open, onToggle, P }) {
  const [openMap, setOpenMap] = useState({})
  const toggle = (id) => setOpenMap(m => ({ ...m, [id]: !(m[id] !== undefined ? m[id] : OPEN_DEFAULT.has(id)) }))
  return (
    <div data-goalbar style={{ background: P.surface, borderBottom: `1px solid ${P.line}` }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 10px' }}>
        <motion.span onClick={onToggle} title={open ? 'Ziel einklappen' : 'Ziel ausklappen'} whileTap={{ scale: 0.88 }} transition={SPRING} style={{ display: 'inline-flex', cursor: 'pointer' }}>
          {open ? <IconLayoutNavbarCollapse size={14} stroke={1.8} color={P.ink} /> : <IconLayoutNavbarExpand size={14} stroke={1.8} color={P.ink} />}
        </motion.span>
        <span style={{ fontFamily: SANS, fontSize: 12, fontWeight: 600, color: P.ink }}>skaldir ui-abnahme</span>
        <span style={{ fontFamily: MONO, fontSize: 11, color: P.sub }}>{TOTAL.done}/{TOTAL.total} ✓</span>
        <span style={{ marginLeft: 'auto', fontFamily: MONO, fontSize: 11, color: P.faint }}>esc esc beendet</span>
      </div>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div key="ziel" data-goalbody initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }} transition={SPRING} style={{ overflow: 'hidden' }}>
            <div style={{ padding: '0 10px 7px' }}>
              {GOAL.map((n, i) => <Row key={n.id} node={n} ancestors={[]} isLast={i === GOAL.length - 1} openMap={openMap} toggle={toggle} P={P} />)}
              <div style={{ marginTop: 3, fontFamily: MONO, fontSize: 11, color: P.faint }}>└─ Kriterium ergaenzen</div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

/* ---------------------------------------------------------------- Datei-Upload */

const EXT = { md: '.md', png: '.png', pdf: '.pdf' }

/** Vorschau als reines Formelement — kein Symbol, nur Geometrie. */
const Preview = ({ kind, P }) => (
  <motion.span initial={{ opacity: 0, scale: 0.8 }} animate={{ opacity: 1, scale: 1 }} transition={SPRING}
    style={{ width: 16, height: 20, border: `1px solid ${P.line}`, background: P.soft, position: 'relative', flex: '0 0 auto', display: 'inline-block' }}>
    {kind === 'png' ? (
      <>
        <span style={{ position: 'absolute', left: 2, right: 2, bottom: 2, height: 7, background: P.faint, opacity: 0.35 }} />
        <span style={{ position: 'absolute', left: 2, top: 2, width: 4, height: 4, borderRadius: RADIUS.pill, background: P.faint, opacity: 0.5 }} />
      </>
    ) : (
      <>
        <span style={{ position: 'absolute', right: 0, top: 0, width: 6, height: 6, borderLeft: `1px solid ${P.line}`, borderBottom: `1px solid ${P.line}`, background: P.bg }} />
        {[5, 9, 13].map(y => <span key={y} style={{ position: 'absolute', left: 3, right: 4, top: y, height: 1, background: P.faint, opacity: 0.45 }} />)}
      </>
    )}
  </motion.span>
)
const shortName = (p) => p.split('/').pop()

/** Fuellung des Uploads: laeuft mit Motion von 0 auf den Wert — auch wenn die Zeile neu eintrifft. */
const ProgressBar = ({ pct, P }) => {
  const ref = useRef(null)
  useEffect(() => {
    if (!ref.current) return
    ref.current.style.width = '0%'
    const steuerung = animate(ref.current, { width: `${pct}%` }, { duration: 1.6, ease: 'easeInOut', delay: 0.25 })
    return () => steuerung.stop()
  }, [pct])
  return <span ref={ref} data-progress style={{ display: 'block', height: 3, width: 0, background: P.ink }} />
}

const FileRow = ({ path, ext, size, state, pct, P, note, i = 0 }) => (
  <motion.div data-uploadrow layout initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: 24 }} exit={{ opacity: 0, height: 0 }} transition={{ ...SPRING, delay: i * 0.05 }}
    style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '1px 5px', border: `1px solid ${P.line}`, background: P.surface, overflow: 'hidden' }}>
    <Preview kind={ext === 'png' ? 'png' : 'doc'} P={P} />
    <span style={{ flex: 1, minWidth: 0, fontFamily: MONO, fontSize: 11, color: P.ink, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{shortName(path)}</span>
    <span style={{ fontFamily: MONO, fontSize: 11, color: P.faint, whiteSpace: 'nowrap' }}>{ext} · {size}{note ? ` · ${note}` : ''}</span>
    {state === 'laeuft' && (
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, width: 62 }}>
        <span style={{ display: 'block', flex: 1, height: 3, background: P.hair, border: `1px solid ${P.line}` }}>
          <ProgressBar pct={pct} P={P} />
        </span>
        <span style={{ fontFamily: MONO, fontSize: 11, color: P.sub }}>{pct}%</span>
      </span>
    )}
    {state === 'fertig' && <motion.span initial={{ opacity: 0, scale: 0.4 }} animate={{ opacity: 1, scale: 1 }} transition={{ ...SPRING, delay: 0.25 + i * 0.05 }} style={{ fontFamily: MONO, fontSize: 11, color: P.ink }}>✓</motion.span>}
    {state === 'fehler' && <motion.span initial={{ opacity: 0, scale: 0.4 }} animate={{ opacity: 1, scale: 1, x: [0, -2, 2, 0] }} transition={{ ...SPRING, delay: 0.2 + i * 0.05 }} style={{ fontFamily: MONO, fontSize: 11, fontWeight: 700, color: P.ink }}>✕</motion.span>}
  </motion.div>
)

const UploadSummary = ({ P }) => (
  <motion.div data-uploadsummary initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} transition={{ ...SPRING, delay: 0.15 }}
    style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '6px 9px', border: `1px solid ${P.line}`, background: P.soft, fontFamily: MONO, fontSize: 11, color: P.ink }}>
    <span>3 Dateien</span><span style={{ color: P.faint }}>·</span>
    <span>4,2 MB von 6 MB</span><span style={{ color: P.faint }}>·</span>
    <span>1 laedt (68 %)</span><span style={{ color: P.faint }}>·</span>
    <span>Rest ca. 3 s</span>
    <span style={{ flex: 1 }} />
    <span style={{ color: P.faint }}>Anbieter-Limit 5 MB je Datei</span>
  </motion.div>
)

/* Fehler: kein Rot — kraeftiger Rand plus ✕ tragen die Bedeutung. */
const UploadError = ({ P }) => (
  <motion.div data-uploaderror initial={{ opacity: 0, scale: 0.97, y: 6 }} animate={{ opacity: 1, scale: 1, y: 0 }} transition={SPRING}
    style={{ border: `1.5px solid ${P.ink}`, background: P.surface, padding: '9px 10px', transformOrigin: 'bottom' }}>
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <motion.span animate={{ opacity: [1, 0.35, 1] }} transition={{ duration: 1.8, repeat: Infinity, ease: 'easeInOut' }} style={{ fontFamily: MONO, fontSize: 12, fontWeight: 700, color: P.ink }}>✕</motion.span>
      <span style={{ fontFamily: SANS, fontSize: 12.5, fontWeight: 600, color: P.ink }}>Der Anbieter nimmt hoechstens 5 MB je Datei</span>
    </div>
    <div style={{ fontFamily: MONO, fontSize: 11, color: P.sub, margin: '5px 0 8px' }}>
      2 Dateien abgelehnt: handbuch.pdf (9,8 MB), rohdaten.zip (12,4 MB) — es wurde nichts gesendet.
      Uebertragen: 0 von 22,2 MB.
    </div>
    <div style={{ display: 'flex', gap: 7, flexWrap: 'wrap' }}>
      {['Datei verkleinern', 'Andere Datei waehlen', 'Anderen Anbieter waehlen'].map((t, i) => (
        <motion.span key={t} initial={{ opacity: 0, y: 5 }} animate={{ opacity: 1, y: 0 }} transition={{ ...SPRING, delay: 0.1 + i * 0.05 }}
          whileHover={{ scale: 1.03 }} whileTap={{ scale: 0.96 }}
          style={{ fontFamily: MONO, fontSize: 11, padding: '4px 9px', border: `1px solid ${i === 0 ? P.ink : P.line}`, background: i === 0 ? P.ink : 'transparent', color: i === 0 ? P.surface : P.ink, cursor: 'pointer' }}>{t}</motion.span>
      ))}
    </div>
  </motion.div>
)

/* ---------------------------------------------------------------- Bühne */

const Transcript = ({ P, children }) => (
  <div style={{ flex: 1, padding: 14, display: 'flex', flexDirection: 'column', gap: 8, overflow: 'hidden' }}>
    <motion.span initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={SPRING} style={{ alignSelf: 'flex-end', fontFamily: MONO, fontSize: 11.5, background: P.ink, color: P.surface, padding: '5px 9px', borderRadius: RADIUS.pill }}>zerleg das in drei spuren</motion.span>
    <motion.span initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ ...SPRING, delay: 0.08 }} style={{ alignSelf: 'flex-start', maxWidth: 430, fontFamily: MONO, fontSize: 11.5, background: P.surface, border: `1px solid ${P.line}`, padding: '6px 10px', borderRadius: RADIUS.module }}>drei subagenten gerufen.</motion.span>
    {children}
  </div>
)

const Bar = ({ P, files, summary, error, onPick }) => (
  <div style={{ padding: 10 }}>
    <div style={{ borderRadius: RADIUS.bar, border: `1px solid ${P.line}`, background: P.surface, overflow: 'hidden' }}>
      <AnimatePresence initial={false}>
        {files && (
          <motion.div key="uploads" initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }} transition={SPRING} style={{ overflow: 'hidden' }}>
            <div style={{ padding: 10, display: 'flex', flexDirection: 'column', gap: 5, borderBottom: `1px solid ${P.line}` }}>
              {files.map((f, i) => <FileRow key={f.path} {...f} i={i} P={P} />)}
              {summary && <UploadSummary P={P} />}
              {error && <UploadError P={P} />}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, height: 58, padding: '0 12px 0 16px' }}>
        <motion.span onClick={onPick} title="Datei anhaengen" whileHover={{ scale: 1.15, rotate: 90 }} whileTap={{ scale: 0.9 }} transition={SPRING} style={{ display: 'inline-flex', cursor: 'pointer' }}><IconPlus P={P} /></motion.span>
        <motion.span animate={{ opacity: files ? 0.75 : 1 }} style={{ fontFamily: SANS, fontSize: 14.5, color: P.faint, flex: 1 }}>{files ? 'Beschreibung ergaenzen' : 'Start with a goal'}</motion.span>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 14 }}>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}><DotGrid P={P} /><IconChevronDown size={11} stroke={2} color={P.faint} /></span>
          <Med P={P} />
          <Selector P={P} />
        </span>
      </div>
    </div>
  </div>
)

const Case = ({ name, note, dark, files, summary, error }) => {
  useBarrierefrei()
  const P = dark ? DARK : LIGHT
  const [goalOpen, setGoalOpen] = useState(true)
  const [extra, setExtra] = useState([])
  const shown = files ? files.concat(extra) : extra
  return (
    <div data-stage style={{ padding: 20, background: P.bg, fontFamily: SANS, width: '100%', maxWidth: 900, boxSizing: 'border-box' }}>
      <div style={{ fontFamily: MONO, fontSize: 12, fontWeight: 600, color: P.ink }}>{name}</div>
      <div style={{ fontFamily: MONO, fontSize: 11, color: P.sub, margin: '3px 0 12px' }}>{note}</div>
      <div style={{ height: 440, borderRadius: RADIUS.dialog, border: `1px solid ${P.line}`, background: P.bg, display: 'flex', flexDirection: 'column' }}>
        <Transcript P={P} />
        <div style={{ padding: '0 14px 14px', display: 'flex', justifyContent: 'center' }}>
          <div style={{ width: '100%', borderRadius: RADIUS.bar, background: P.surface, border: `1px solid ${P.line}` }}>
            <GoalBar open={goalOpen} onToggle={() => setGoalOpen(o => !o)} P={P} />
            <Bar P={P} files={shown.length ? shown : null} summary={summary} error={error}
              onPick={() => setExtra(e => [...e, { path: `~/Pictures/Screenshots/Screenshot From 2026-09-17 22-21-00 (${e.length + 2}).png`, ext: '.png', size: '1,4 MB', state: 'laeuft', pct: 68 }])} />
          </div>
        </div>
      </div>
      <div style={{ fontFamily: MONO, fontSize: 11, color: P.faint, marginTop: 8 }}>
        Lambda-Ordner leer? Dann ist hier nur die Leiste zu sehen. Datei anhaengen ueber das IconPlus links.
      </div>
    </div>
  )
}

export default {
  '01 · Chat-Bar (gewaehlt): Referenz-Pille mit Selector': () => (
    <Case name="01 · Chat-Bar (gewaehlt)" note="Referenz-Pille, Modell-&-Reasoning-Selector, Target-Bar darueber" dark={false} />
  ),
  '02 · Chat-Bar DARKMODE (identisch zur hellen Fassung)': () => (
    <Case name="02 · Chat-Bar DARKMODE" note="identische Geometrie und Abstaende, nur invertierte Tinte" dark />
  ),
  '03 · Datei-Upload: Pfad, Vorschau, Endung + Live-Zusammenfassung': () => (
    <Case name="03 · Datei-Upload mit Live-Zusammenfassung" dark={false} summary
      files={[
        { path: '~/Documents/Design_Docs/01_PRODUCT_UX_DESIGN.md', ext: '.md', size: '42 KB', state: 'fertig' },
        { path: '~/Pictures/Screenshots/Screenshot From 2026-09-17 22-21-00.png', ext: '.png', size: '1,4 MB', state: 'laeuft', pct: 68 },
        { path: '~/Documents/Hermes_Skaldir_Agent_Docs/UI-CONTRACT.md', ext: '.md', size: '18 KB', state: 'fertig' }
      ]} />
  ),
  '04 · Upload-Fehler: Anbieter-Limit erreicht': () => (
    <Case name="04 · Upload-Fehler: Anbieter-Limit" note="klare Meldung, was abgelehnt wurde und welche Wege bleiben" dark={false} error
      files={[
        { path: '~/Documents/Design_Docs/04_RUNTIME_MEMORY_SUBAGENTS_DESIGN.md', ext: '.md', size: '31 KB', state: 'fertig' },
        { path: '~/Downloads/handbuch.pdf', ext: '.pdf', size: '9,8 MB', state: 'fehler', note: 'ueber Limit' },
        { path: '~/Downloads/rohdaten.zip', ext: '.zip', size: '12,4 MB', state: 'fehler', note: 'ueber Limit' }
      ]} />
  ),
  '05 · Datei-Upload DARKMODE (identisch)': () => (
    <Case name="05 · Datei-Upload DARKMODE" note="gleiche Reihenfolge und Groessen, invertierte Tinte" dark summary
      files={[
        { path: '~/Documents/Design_Docs/01_PRODUCT_UX_DESIGN.md', ext: '.md', size: '42 KB', state: 'fertig' },
        { path: '~/Pictures/Screenshots/Screenshot From 2026-09-17 22-21-00.png', ext: '.png', size: '1,4 MB', state: 'laeuft', pct: 68 }
      ]} />
  )
}
