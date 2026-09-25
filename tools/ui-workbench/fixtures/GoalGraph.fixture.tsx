/**
 * Goal-/Task-GRAPH — inline im Chat UND als eigenes Tab.
 *
 * Entschieden: der Graph lebt an zwei Orten — eingeklappt als Kopfzeile im Chat
 * (Variante 01/02) und aufgeklappt als eigenes Tab (Variante 03 ff.).
 *
 * React Flow ist NICHT installiert und wird hier auch nicht gebraucht: der Graph
 * ist reine Geometrie — Kästchen mit absoluter Position, Verbindungen als
 * kubische SVG-Kurven (Motion zeichnet sie über `pathLength`), Raster und
 * Achsenkreuz als CSS-Verläufe. Keine Abhängigkeit, kein Layout-Engine.
 *
 * Nur Schwarz/Weiß/Grau (Palette aus ./_tokens). Bewegung: Kanten wachsen,
 * Knoten setzen sich gestaffelt mit der gemeinsamen Feder, Auf-/Zuklappen und
 * Knotenauswahl sind echter Zustand (useState), Uhr und Orb laufen live.
 * Reduced Motion und Tastaturbedienung: useBarrierefrei() auf der Buehne (A1/A5).
 */
import { motion, AnimatePresence, animate } from 'motion/react'
import React, { useEffect, useState } from 'react'
import { IconAlertCircle, IconCheck, IconChevronRight, IconClock, IconCornerDownRight, IconCrosshair, IconLayoutGrid, IconLink, IconListTree, IconPlus, IconRefresh, IconRoute, IconX, IconZoomIn, IconZoomOut } from '@tabler/icons-react'

// @ts-expect-error — Laufzeit-Alias (siehe vite.config.ts)
import { ThinkingOrb } from 'thinking-orbs'
import { LIGHT, DARK, MONO, SANS, SPRING, RADIUS, TEXT } from './_tokens'
import { useBarrierefrei } from './_barrierefrei'

/* Farben, Schriften, Federn, Radien und Schriftgroessen kommen aus ./_tokens (eine Wahrheit) */

/** Echte, sekündlich laufende Uhr (Variante 06/07). */
const useLive = (start: number) => {
  const [t, setT] = useState(start)
  useEffect(() => {
    const id = setInterval(() => setT(v => v + 1), 1000)
    return () => clearInterval(id)
  }, [])
  return t
}
const fmt = (s: number) => String(Math.floor(s / 60)).padStart(2, '0') + ':' + String(s % 60).padStart(2, '0')

/* ---------- Modell: Position + Zustand + Kanten (reine Geometrie) ---------- */
type NS = 'open' | 'run' | 'done' | 'fail' | 'blocked'
type Pos = { id: string; label: string; x: number; y: number; w: number; h: number; s: NS; note?: string; kids?: boolean }
type IconLink = { f: string; t: string }

const cx = (n: Pos) => n.x + n.w
const cy = (n: Pos) => n.y + n.h / 2
/** Kubische Kurve von der rechten Kante des Quellknotens zur linken des Zielknotens. */
const edgePath = (a: Pos, b: Pos) => {
  const x1 = cx(a), y1 = cy(a), x2 = b.x, y2 = cy(b)
  const m = Math.max(22, (x2 - x1) * 0.5)
  return `M ${x1} ${y1} C ${x1 + m} ${y1}, ${x2 - m} ${y2}, ${x2} ${y2}`
}

/* ---------- Flächenmuster ---------- */
const GridBg = ({ P, step = 28 }: { P: typeof LIGHT; step?: number }) => (
  <div style={{ position: 'absolute', inset: 0, pointerEvents: 'none',
    backgroundImage: `linear-gradient(${P.hair} 1px, transparent 1px), linear-gradient(90deg, ${P.hair} 1px, transparent 1px)`,
    backgroundSize: `${step}px ${step}px` }} />
)

const AxisCross = ({ P, W, H }: { P: typeof LIGHT; W: number; H: number }) => (
  <>
    <div style={{ position: 'absolute', left: 0, right: 0, top: Math.round(H / 2), borderTop: `1px solid ${P.line}`, pointerEvents: 'none' }} />
    <div style={{ position: 'absolute', top: 0, bottom: 0, left: Math.round(W / 2), borderLeft: `1px solid ${P.line}`, pointerEvents: 'none' }} />
  </>
)

/* ---------- Knoten als Kästchen ---------- */
const Glyph = ({ s, P, dark }: { s: NS; P: typeof LIGHT; dark: boolean }) => (
  <span style={{ width: 20, display: 'inline-flex', justifyContent: 'center', flex: '0 0 auto' }}>
    {s === 'run' ? <ThinkingOrb state="working" size={20} theme={dark ? 'dark' : 'light'} />
      : s === 'done' ? <IconCheck size={12} stroke={2.4} color={P.ink} />
      : s === 'fail' ? <IconX size={13} stroke={2.6} color={P.ink} />
      : s === 'blocked' ? <span style={{ width: 7, height: 7, border: `1.2px dashed ${P.faint}` }} />
      : <span style={{ width: 8, height: 8, border: `1.3px solid ${P.faint}` }} />}
  </span>
)

function NodeBox({ n, i, P, dark, sel, onSel, small, open, onOpen }: {
  n: Pos; i: number; P: typeof LIGHT; dark: boolean; sel?: boolean
  onSel?: () => void; small?: boolean; open?: boolean; onOpen?: () => void
}) {
  const done = n.s === 'done', blocked = n.s === 'blocked'
  return (
    <motion.div key={n.id} data-node={n.id} data-state={n.s}
      initial={{ opacity: 0, scale: 0.9 }} animate={{ opacity: blocked ? 0.4 : done ? 0.7 : 1, scale: 1 }}
      exit={{ opacity: 0, scale: 0.92 }} transition={{ ...SPRING, delay: i * 0.035 }}
      whileHover={onSel ? { scale: 1.015 } : undefined} whileTap={onSel ? { scale: 0.985 } : undefined}
      onClick={onSel}
      style={{ position: 'absolute', left: n.x, top: n.y, width: n.w, height: n.h, boxSizing: 'border-box',
        background: P.surface, border: `1.2px ${blocked ? 'dashed' : 'solid'} ${sel ? P.ink : P.line}`,
        boxShadow: sel ? `0 0 0 3px ${P.hair}` : 'none',
        display: 'flex', alignItems: 'center', gap: 6, padding: '0 8px',
        cursor: onSel ? 'pointer' : 'default', filter: done ? 'blur(0.3px)' : 'none' }}>
      <Glyph s={n.s} P={P} dark={dark} />
      <span style={{ flex: 1, minWidth: 0, fontFamily: SANS, fontSize: small ? 11 : 11.5, color: P.ink,
        whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
        textDecoration: done ? 'line-through' : 'none', textDecorationColor: P.faint }}>{n.label}</span>
      {n.note && <span style={{ fontFamily: MONO, fontSize: 11, color: P.faint, whiteSpace: 'nowrap' }}>{n.note}</span>}
      {n.kids && (
        <motion.span onClick={e => { e.stopPropagation(); onOpen && onOpen() }} whileTap={{ scale: 0.8 }} transition={SPRING}
          animate={{ rotate: open ? 90 : 0 }} style={{ display: 'inline-flex', color: P.sub, cursor: 'pointer', flex: '0 0 auto' }}>
          <IconChevronRight size={11} stroke={2} />
        </motion.span>
      )}
    </motion.div>
  )
}

/** Die Graphfläche: Muster, Kanten, Kästchen. Breite/Höhe sind echte Pixel. */
function GraphField({ W, H, nodes, links, P, dark, grid, axis, sel, onSel, small, framed, open = {}, onOpen }: {
  W: number; H: number; nodes: Pos[]; links: IconLink[]; P: typeof LIGHT; dark: boolean
  grid?: boolean; axis?: boolean; sel?: string | null; onSel?: (id: string) => void
  small?: boolean; framed?: boolean; open?: Record<string, boolean>; onOpen?: (id: string) => void
}) {
  const map: Record<string, Pos> = {}
  nodes.forEach(n => { map[n.id] = n })
  return (
    <div data-graph style={{ position: 'relative', width: W, height: H, background: P.surface, overflow: 'hidden',
      border: framed ? `1px solid ${P.line}` : undefined, boxShadow: framed ? '0 10px 28px rgba(0,0,0,0.10)' : undefined }}>
      {grid && <GridBg P={P} />}
      {axis && <AxisCross P={P} W={W} H={H} />}
      <svg width={W} height={H} style={{ position: 'absolute', left: 0, top: 0, pointerEvents: 'none', overflow: 'visible' }}>
        <AnimatePresence>
          {links.map(l => {
            const a = map[l.f], b = map[l.t]
            if (!a || !b) return null
            const dead = a.s === 'blocked' || b.s === 'blocked' || a.s === 'fail'
            const strong = a.s === 'done' && b.s !== 'open' && !dead
            return (
              <motion.path key={`${l.f}>${l.t}`} d={edgePath(a, b)} fill="none"
                stroke={dead || !strong ? P.line : P.ink} strokeWidth={strong ? 1.4 : 1.2}
                strokeDasharray={dead ? '3 3' : undefined}
                initial={{ pathLength: 0, opacity: 0 }} animate={{ pathLength: 1, opacity: 1 }} exit={{ pathLength: 0, opacity: 0 }}
                transition={{ duration: 0.6, ease: 'easeInOut' }} />
            )
          })}
        </AnimatePresence>
      </svg>
      <AnimatePresence>
        {nodes.map((n, i) => (
          <NodeBox key={n.id} n={n} i={i} P={P} dark={dark} small={small} sel={sel === n.id}
            onSel={onSel ? () => onSel(n.id) : undefined} open={!!open[n.id]} onOpen={onOpen ? () => onOpen(n.id) : undefined} />
        ))}
      </AnimatePresence>
    </div>
  )
}

/* ---------- Reiterleiste (eigenes Tab) ---------- */
const TabBar = ({ tabs, active, onPick, P, uid }: { tabs: string[]; active: string; onPick: (t: string) => void; P: typeof LIGHT; uid: string }) => (
  <div style={{ display: 'flex', alignItems: 'stretch', borderBottom: `1px solid ${P.line}`, background: P.surface }}>
    {tabs.map(t => {
      const on = t === active
      return (
        <motion.button key={t} data-tab={t} onClick={() => onPick(t)} whileTap={{ scale: 0.97 }} transition={SPRING}
          style={{ position: 'relative', padding: '7px 14px', border: 'none', background: 'transparent', cursor: 'pointer',
            fontFamily: SANS, fontSize: 12, fontWeight: on ? 600 : 400, color: on ? P.ink : P.sub }}>
          {t}
          {on && <motion.span layoutId={`tabink-${uid}`} transition={SPRING}
            style={{ position: 'absolute', left: 9, right: 9, bottom: -1, height: 1.5, background: P.ink }} />}
        </motion.button>
      )
    })}
  </div>
)

const Legend = ({ P, items = [['open', 'offen'], ['run', 'läuft'], ['done', 'belegt'], ['blocked', 'blockiert']] as [NS, string][] }) => (
  <div style={{ display: 'flex', alignItems: 'center', gap: 14, padding: '7px 10px', borderTop: `1px solid ${P.hair}` }}>
    {items.map(([s, t]) => (
      <span key={s} data-legend={s} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontFamily: MONO, fontSize: 11, color: P.sub }}>
        <span style={{ width: 9, height: 9, border: `1.2px ${s === 'blocked' ? 'dashed' : 'solid'} ${P.line}`,
          background: s === 'done' ? P.ink : 'transparent' }} />{t}
      </span>
    ))}
    <span style={{ marginLeft: 'auto', fontFamily: MONO, fontSize: 11, color: P.faint }}>offen · läuft · belegt</span>
  </div>
)

/* ---------- Rahmen jeder Variante (Titelzeile + hell/dunkel) ---------- */
function Head({ dark, setDark, label, sub }: { dark: boolean; setDark: (d: boolean) => void; label: string; sub: string }) {
  const P = dark ? DARK : LIGHT
  return (
    <div style={{ marginBottom: 10 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
        <div style={{ fontFamily: SANS, fontSize: 15, fontWeight: 600, letterSpacing: -0.2 }}>{label}</div>
        <span style={{ marginLeft: 'auto', display: 'inline-flex', border: `1px solid ${P.line}`, fontFamily: MONO, fontSize: 11 }}>
          {(['hell', 'dunkel'] as const).map(m => {
            const on = (m === 'dunkel') === dark
            return (
              <button key={m} onClick={() => setDark(m === 'dunkel')}
                style={{ padding: '4px 10px', border: 'none', cursor: 'pointer', fontFamily: MONO, fontSize: 11,
                  background: on ? P.ink : 'transparent', color: on ? P.bg : P.sub }}>{m}</button>
            )
          })}
        </span>
      </div>
      <div style={{ fontFamily: MONO, fontSize: 11, color: P.sub, marginTop: 3 }}>{sub}</div>
    </div>
  )
}

function Stage({ head, children, wide }: { head: React.ReactNode; children: React.ReactNode; wide?: boolean }) {
  useBarrierefrei()   // A1 reduzierte Bewegung, A5 Tastatur (Befund 1+2)
  return (
    <div data-stage style={{ padding: 20, width: wide ? 960 : 780, boxSizing: 'border-box' }}>
      {head}
      <div data-stagebody style={{ position: 'relative' }}>{children}</div>
    </div>
  )
}

function Shell({ label, sub, wide, darkStart, children }: {
  label: string; sub: string; wide?: boolean; darkStart?: boolean; children: (dark: boolean) => React.ReactNode
}) {
  const [dark, setDark] = useState(!!darkStart)
  const P = dark ? DARK : LIGHT
  return (
    <div style={{ background: P.bg, color: P.ink, fontFamily: SANS, minHeight: darkStart || wide ? 520 : undefined }}>
      <Stage wide={wide} head={<Head dark={dark} setDark={setDark} label={label} sub={sub} />}>
        {children(dark)}
      </Stage>
    </div>
  )
}

/* ================================================================ Daten */

/** Fünf Kriterien für die Inline-Fassung (Variante 01/02). */
const CRITS: { id: string; label: string; s: NS; note?: string }[] = [
  { id: 'k1', label: 'Nachweise sind messbar', s: 'done', note: '28/28' },
  { id: 'k2', label: 'Overlay-Lint ohne Befund', s: 'done', note: '0 Befunde' },
  { id: 'k3', label: 'Echter Nutzerweg läuft', s: 'run', note: '3 von 5 Schritten' },
  { id: 'k4', label: 'Fehlerfall hat einen Ausweg', s: 'open' },
  { id: 'k5', label: 'Zustände bleiben erhalten', s: 'done', note: 'Reload ✓' },
]

/* --- Inline-Graph im Chat --- */
const IN_NODES: Pos[] = [
  { id: 'g', label: 'Ziel · UI-Abnahme', x: 14, y: 100, w: 150, h: 44, s: 'run' },
  { id: 'c1', label: 'Nachweise messbar', x: 320, y: 10, w: 226, h: 38, s: 'done', note: '28/28' },
  { id: 'c2', label: 'Overlay-Lint', x: 320, y: 62, w: 226, h: 38, s: 'done', note: '0 Befunde' },
  { id: 'c3', label: 'Nutzerweg läuft', x: 320, y: 114, w: 226, h: 38, s: 'run', note: '3/5' },
  { id: 'c4', label: 'Fehlerfall hat Ausweg', x: 320, y: 166, w: 226, h: 38, s: 'open' },
  { id: 'c5', label: 'Zustände bleiben erhalten', x: 320, y: 218, w: 226, h: 38, s: 'done', note: 'Reload ✓' },
]
const IN_LINKS: IconLink[] = [
  { f: 'g', t: 'c1' }, { f: 'g', t: 'c2' }, { f: 'g', t: 'c3' }, { f: 'g', t: 'c4' }, { f: 'g', t: 'c5' },
]

/* --- Tab-Graph --- */
const TAB_NODES: Pos[] = [
  { id: 'goal', label: 'Ziel · Abnahme belegt', x: 20, y: 100, w: 168, h: 44, s: 'run' },
  { id: 'k1', label: 'Nachweise messbar', x: 246, y: 34, w: 158, h: 40, s: 'done' },
  { id: 'k2', label: 'Nutzerweg läuft', x: 246, y: 166, w: 158, h: 40, s: 'run' },
  { id: 'l1', label: 'Anker-Prüfung', x: 462, y: 6, w: 176, h: 34, s: 'done', note: '28/28' },
  { id: 'l2', label: 'Overlay-Lint', x: 462, y: 50, w: 176, h: 34, s: 'done', note: '0 Befunde' },
  { id: 'l3', label: 'Composer → Senden', x: 462, y: 148, w: 176, h: 34, s: 'done', note: 'im Test' },
  { id: 'l4', label: 'Fehlerfall hat Ausweg', x: 462, y: 192, w: 176, h: 34, s: 'open' },
]
const TAB_LINKS: IconLink[] = [
  { f: 'goal', t: 'k1' }, { f: 'goal', t: 'k2' },
  { f: 'k1', t: 'l1' }, { f: 'k1', t: 'l2' },
  { f: 'k2', t: 'l3' }, { f: 'k2', t: 'l4' },
]

/** Knotenzustand zu einem Detail-Schlüssel (Seitenkarte). */
const SEL_STATE: Record<string, NS> = { goal: 'run', k1: 'done', k2: 'run', l1: 'done', l2: 'done', l3: 'done', l4: 'open' }

/** Seitenkarte (Variante 04/10): Kriterium, Nachweis, Zustand, als nächstes. */
const DETAILS: Record<string, { crit: string; ev: string; st: string; next: string }> = {
  goal: { crit: 'Ziel · Abnahme belegt', ev: 'Bericht „01 · Abnahme“ mit 9 Prüfzeilen', st: 'läuft', next: 'offene Kriterien einsammeln' },
  k1: { crit: 'Nachweise sind messbar', ev: 'Anker-Prüfung 28/28 · Overlay-Lint 0 Befunde', st: 'belegt', next: 'keiner — Kriterium abgeschlossen' },
  k2: { crit: 'Echter Nutzerweg läuft', ev: 'Composer → Session → Senden im Overlay-Test', st: 'läuft', next: 'Fehlerfall: Ausweg zeigen (offen)' },
  l1: { crit: 'Anker-Prüfung', ev: '28 von 28 Ankern gefunden, keine Lücke', st: 'belegt', next: 'keiner' },
  l2: { crit: 'Overlay-Lint', ev: '0 Befunde, Ausgabe protokolliert', st: 'belegt', next: 'keiner' },
  l3: { crit: 'Composer → Senden', ev: 'Nachricht erreicht Transkript, Antwort rendert', st: 'belegt', next: 'keiner' },
  l4: { crit: 'Fehlerfall hat einen Ausweg', ev: 'fehlt — Fehlerkarte noch ohne Weg', st: 'offen', next: 'Fehlerkarte mit Text, Ort und Wiederholen' },
}

/** Subkriterien über drei Ebenen (Variante 05). */
const SUB_NODES: Pos[] = [
  { id: 'z', label: 'Ziel · Graph belegt', x: 14, y: 118, w: 150, h: 44, s: 'run' },
  { id: 'a', label: 'Nachweise messbar', x: 214, y: 44, w: 158, h: 40, s: 'done', kids: true },
  { id: 'b', label: 'Nutzerweg läuft', x: 214, y: 196, w: 158, h: 40, s: 'run', kids: true },
  { id: 'a1', label: 'Anker-Prüfung', x: 422, y: 8, w: 186, h: 32, s: 'done' },
  { id: 'a2', label: 'Overlay-Lint', x: 422, y: 48, w: 186, h: 32, s: 'done' },
  { id: 'a3', label: 'Kontrast dunkel ≥ 3:1', x: 422, y: 88, w: 186, h: 32, s: 'open' },
  { id: 'b1', label: 'Composer → Senden', x: 422, y: 168, w: 186, h: 32, s: 'done' },
  { id: 'b2', label: 'Fehlerfall hat Ausweg', x: 422, y: 208, w: 186, h: 32, s: 'open' },
]
const SUB_LINKS: IconLink[] = [
  { f: 'z', t: 'a' }, { f: 'z', t: 'b' },
  { f: 'a', t: 'a1' }, { f: 'a', t: 'a2' }, { f: 'a', t: 'a3' },
  { f: 'b', t: 'b1' }, { f: 'b', t: 'b2' },
]
const SUB_KIDS: Record<string, string[]> = { a: ['a1', 'a2', 'a3'], b: ['b1', 'b2'] }

/** Übersichtsgraph mit kleinerem Maßstab (Variante 08). */
const OVER_NODES: Pos[] = [
  { id: 'g', label: 'Ziel', x: 16, y: 158, w: 118, h: 36, s: 'run' },
  { id: 'k1', label: 'Nachweise', x: 196, y: 58, w: 128, h: 32, s: 'done' },
  { id: 'k2', label: 'Nutzerweg', x: 196, y: 160, w: 128, h: 32, s: 'run' },
  { id: 'k3', label: 'Zustände', x: 196, y: 262, w: 128, h: 32, s: 'open' },
  { id: 'a1', label: 'Anker', x: 394, y: 8, w: 128, h: 28, s: 'done' },
  { id: 'a2', label: 'Lint', x: 394, y: 44, w: 128, h: 28, s: 'done' },
  { id: 'a3', label: 'Kontrast', x: 394, y: 80, w: 128, h: 28, s: 'done' },
  { id: 'b1', label: 'Senden', x: 394, y: 130, w: 128, h: 28, s: 'done' },
  { id: 'b2', label: 'Fehlerfall', x: 394, y: 166, w: 128, h: 28, s: 'blocked' },
  { id: 'c1', label: 'Reload', x: 394, y: 238, w: 128, h: 28, s: 'open' },
  { id: 'c2', label: 'Baum überlebt', x: 394, y: 274, w: 128, h: 28, s: 'open' },
]
const OVER_LINKS: IconLink[] = [
  { f: 'g', t: 'k1' }, { f: 'g', t: 'k2' }, { f: 'g', t: 'k3' },
  { f: 'k1', t: 'a1' }, { f: 'k1', t: 'a2' }, { f: 'k1', t: 'a3' },
  { f: 'k2', t: 'b1' }, { f: 'k2', t: 'b2' },
  { f: 'k3', t: 'c1' }, { f: 'k3', t: 'c2' },
]

/* ================================================================ Bausteine */

const Msg = ({ P, right, children }: { P: typeof LIGHT; right?: boolean; children: React.ReactNode }) => (
  <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={SPRING}
    style={{ alignSelf: right ? 'flex-end' : 'flex-start', maxWidth: 430, fontFamily: MONO, fontSize: 11.5,
      background: right ? P.ink : P.surface, color: right ? P.bg : P.ink,
      border: right ? 'none' : `1px solid ${P.line}`, padding: '7px 11px', borderRadius: RADIUS.module }}>{children}</motion.div>
)

const Btn = ({ P, onClick, primary, children }: { P: typeof LIGHT; onClick?: () => void; primary?: boolean; children: React.ReactNode }) => (
  <motion.button onClick={onClick} whileHover={{ scale: 1.04 }} whileTap={{ scale: 0.95 }} transition={SPRING}
    style={{ display: 'inline-flex', alignItems: 'center', gap: 5, cursor: 'pointer', fontFamily: MONO, fontSize: 11,
      padding: '4px 10px', border: `1px solid ${primary ? P.ink : P.line}`,
      background: primary ? P.ink : 'transparent', color: primary ? P.bg : P.ink }}>{children}</motion.button>
)

/** Kopfzeile des Plans — im Chat eingeklappt das einzige, was man sieht. */
const PlanHead = ({ P, dark, open, onToggle }: { P: typeof LIGHT; dark: boolean; open: boolean; onToggle: () => void }) => (
  <motion.div data-planhead onClick={onToggle} whileTap={{ scale: 0.996 }} transition={SPRING}
    style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '7px 10px', cursor: 'pointer' }}>
    <ThinkingOrb state="working" size={20} theme={dark ? 'dark' : 'light'} />
    <span style={{ fontFamily: SANS, fontSize: 12.5, fontWeight: 600, color: P.ink }}>Plan · 6/9 Kriterien</span>
    <span style={{ fontFamily: MONO, fontSize: 11, color: P.sub }}>4 belegt · 1 läuft · 2 offen</span>
    <span style={{ flex: 1 }} />
    <motion.span animate={{ rotate: open ? 90 : 0 }} transition={SPRING} style={{ display: 'inline-flex', color: P.sub }}>
      <IconChevronRight size={13} stroke={2} />
    </motion.span>
  </motion.div>
)

const CritRow = ({ c, P, dark, i }: { c: typeof CRITS[number]; P: typeof LIGHT; dark: boolean; i: number }) => (
  <motion.div data-crit={c.id} initial={{ opacity: 0, x: -4 }} animate={{ opacity: c.s === 'done' ? 0.65 : 1, x: 0 }}
    transition={{ ...SPRING, delay: i * 0.04 }}
    style={{ display: 'flex', alignItems: 'center', gap: 7, minHeight: 22, filter: c.s === 'done' ? 'blur(0.3px)' : 'none' }}>
    <Glyph s={c.s} P={P} dark={dark} />
    <span style={{ fontFamily: SANS, fontSize: 12, color: P.ink,
      textDecoration: c.s === 'done' ? 'line-through' : 'none', textDecorationColor: P.faint }}>{c.label}</span>
    {c.note && <span style={{ fontFamily: MONO, fontSize: 11, color: P.faint }}>{c.note}</span>}
    <span style={{ marginLeft: 'auto', fontFamily: MONO, fontSize: 11, color: P.faint }}>
      {c.s === 'done' ? 'belegt' : c.s === 'run' ? 'läuft' : 'offen'}
    </span>
  </motion.div>
)

/* ================================================================ Varianten */

/* 01 — inline im Chat, eingeklappt: nur die Kopfzeile */
function V01({ dark }: { dark: boolean }) {
  const P = dark ? DARK : LIGHT
  const [open, setOpen] = useState(false)
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8, padding: 14 }}>
      <Msg P={P} right>zerleg das in drei spuren</Msg>
      <Msg P={P}>drei spuren angelegt. den plan halte ich sichtbar — er liegt über der eingabe, nicht im weg.</Msg>
      <div style={{ border: `1px solid ${P.line}`, background: P.surface, maxWidth: 580, alignSelf: 'stretch' }}>
        <PlanHead P={P} dark={dark} open={open} onToggle={() => setOpen(o => !o)} />
        <AnimatePresence initial={false}>
          {open && (
            <motion.div key="plan" initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }}
              exit={{ height: 0, opacity: 0 }} transition={SPRING} style={{ overflow: 'hidden' }}>
              <div style={{ padding: '0 10px 9px' }}>
                {CRITS.map((c, i) => <CritRow key={c.id} c={c} P={P} dark={dark} i={i} />)}
                <div style={{ marginTop: 5, display: 'flex', alignItems: 'center', gap: 5, fontFamily: MONO, fontSize: 11, color: P.faint }}>
                  <IconPlus size={10} stroke={2} /> Kriterium ergänzen
                </div>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
      <div style={{ fontFamily: MONO, fontSize: 11, color: P.faint }}>
        Die Kopfzeile ist ein Knopf: auf und zu mit Feder. Dasselbe Ziel liegt in Variante 03 als eigenes Tab groß vor.
      </div>
    </div>
  )
}

/* 02 — inline aufgeklappt: der Graph selbst im Chat */
function V02({ dark }: { dark: boolean }) {
  const P = dark ? DARK : LIGHT
  const [open, setOpen] = useState(true)
  const [sel, setSel] = useState<string | null>('c3')
  return (
    <div style={{ padding: 14 }}>
      <div style={{ border: `1px solid ${P.line}`, background: P.surface, maxWidth: 600 }}>
        <PlanHead P={P} dark={dark} open={open} onToggle={() => setOpen(o => !o)} />
        <AnimatePresence initial={false}>
          {open && (
            <motion.div key="graph" initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }}
              exit={{ height: 0, opacity: 0 }} transition={SPRING} style={{ overflow: 'hidden' }}>
              <div style={{ borderTop: `1px solid ${P.hair}` }}>
                <GraphField W={560} H={268} nodes={IN_NODES} links={IN_LINKS} P={P} dark={dark} grid sel={sel} onSel={setSel} />
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
      <div style={{ marginTop: 10, display: 'flex', alignItems: 'center', gap: 8, fontFamily: MONO, fontSize: 11, color: P.sub }}>
        <IconCornerDownRight size={11} stroke={2} />
        fünf Kriterien als Kästchen · drei belegt (durchgestrichen, weichgezeichnet) · {sel ? `ausgewählt: ${sel}` : 'kein Knoten gewählt'}
      </div>
    </div>
  )
}

/* 03 — eigenes Tab, Knoten anklickbar */
function V03({ dark }: { dark: boolean }) {
  const P = dark ? DARK : LIGHT
  const [tab, setTab] = useState('Graph')
  const [sel, setSel] = useState<string | null>(null)
  return (
    <div style={{ padding: 14 }}>
      <div style={{ border: `1px solid ${P.line}`, background: P.surface }}>
        <TabBar uid="v03" tabs={['Chat', 'Graph', 'Nachweise']} active={tab} onPick={setTab} P={P} />
        <AnimatePresence mode="wait" initial={false}>
          {tab === 'Graph' ? (
            <motion.div key="g" initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -6 }} transition={SPRING}>
              <GraphField W={660} H={240} nodes={TAB_NODES} links={TAB_LINKS} P={P} dark={dark} grid axis sel={sel} onSel={setSel} />
            </motion.div>
          ) : (
            <motion.div key="o" initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -6 }} transition={SPRING}
              style={{ padding: 14, fontFamily: MONO, fontSize: 11, color: P.sub, minHeight: 240 }}>
              {tab === 'Chat' ? 'Chat liegt darunter unverändert — das Tab nimmt ihm nichts weg.' : 'Nachweise: 6 Prüfzeilen, je Kriterium ein Beleg.'}
            </motion.div>
          )}
        </AnimatePresence>
        <Legend P={P} />
      </div>
    </div>
  )
}

/* 04 — Knoten ausgewählt + Seitenkarte */
function V04({ dark }: { dark: boolean }) {
  const P = dark ? DARK : LIGHT
  const [sel, setSel] = useState<string>('l4')
  const d = DETAILS[sel]
  return (
    <div style={{ display: 'flex', gap: 10, padding: 14, alignItems: 'flex-start' }}>
      <div style={{ border: `1px solid ${P.line}`, background: P.surface }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 7, padding: '7px 10px', borderBottom: `1px solid ${P.hair}` }}>
          <IconListTree size={13} stroke={2} color={P.ink} />
          <span style={{ fontFamily: SANS, fontSize: 12.5, fontWeight: 600, color: P.ink }}>Graph</span>
          <span style={{ fontFamily: MONO, fontSize: 11, color: P.sub }}>Knoten anklicken zeigt den Beleg</span>
        </div>
        <GraphField W={660} H={240} nodes={TAB_NODES} links={TAB_LINKS} P={P} dark={dark} grid axis sel={sel} onSel={setSel} />
      </div>
      <AnimatePresence mode="wait" initial={false}>
        <motion.div key={sel} data-sidecard initial={{ opacity: 0, x: 12, scale: 0.98 }} animate={{ opacity: 1, x: 0, scale: 1 }}
          exit={{ opacity: 0, x: 8, scale: 0.98 }} transition={SPRING}
          style={{ width: 236, flex: '0 0 236px', border: `1.5px solid ${P.ink}`, background: P.surface, padding: 11 }}>
          <div style={{ fontFamily: MONO, fontSize: 11, letterSpacing: 0.6, color: P.faint, textTransform: 'uppercase' }}>Kriterium</div>
          <div style={{ fontFamily: SANS, fontSize: 12.5, fontWeight: 600, color: P.ink, margin: '3px 0 9px' }}>{d.crit}</div>
          <div style={{ fontFamily: MONO, fontSize: 11, letterSpacing: 0.6, color: P.faint, textTransform: 'uppercase' }}>Nachweis</div>
          <div style={{ fontFamily: MONO, fontSize: 11, color: P.sub, margin: '3px 0 9px', lineHeight: 1.5 }}>{d.ev}</div>
          <div style={{ fontFamily: MONO, fontSize: 11, letterSpacing: 0.6, color: P.faint, textTransform: 'uppercase' }}>Zustand</div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 7, margin: '3px 0 9px' }}>
            <Glyph s={SEL_STATE[sel] || 'open'} P={P} dark={dark} />
            <span style={{ fontFamily: MONO, fontSize: 11, color: P.ink }}>{d.st}</span>
          </div>
          <div style={{ borderTop: `1px solid ${P.hair}`, paddingTop: 8 }}>
            <div style={{ fontFamily: MONO, fontSize: 11, letterSpacing: 0.6, color: P.faint, textTransform: 'uppercase' }}>als nächstes</div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 4, fontFamily: SANS, fontSize: 11.5, color: P.ink }}>
              <IconRoute size={12} stroke={2} color={P.ink} /> {d.next}
            </div>
          </div>
        </motion.div>
      </AnimatePresence>
    </div>
  )
}

/* 05 — drei Ebenen, auf-/zuklappen je Knoten */
function V05({ dark }: { dark: boolean }) {
  const P = dark ? DARK : LIGHT
  const [open, setOpen] = useState<Record<string, boolean>>({ a: true, b: true })
  const hidden: Record<string, boolean> = {}
  Object.keys(SUB_KIDS).forEach(id => { if (!open[id]) SUB_KIDS[id].forEach(k => { hidden[k] = true }) })
  const nodes = SUB_NODES.filter(n => !hidden[n.id])
  const links = SUB_LINKS.filter(l => !hidden[l.t] && !hidden[l.f])
  const shown = nodes.length
  return (
    <div style={{ padding: 14 }}>
      <div style={{ border: `1px solid ${P.line}`, background: P.surface }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '7px 10px', borderBottom: `1px solid ${P.hair}` }}>
          <span style={{ fontFamily: SANS, fontSize: 12.5, fontWeight: 600, color: P.ink }}>drei Ebenen · Subkriterien</span>
          <span style={{ fontFamily: MONO, fontSize: 11, color: P.sub }}>{shown} von {SUB_NODES.length} Knoten sichtbar</span>
          <span style={{ flex: 1 }} />
          <Btn P={P} onClick={() => setOpen({ a: true, b: true })}>alles aufklappen</Btn>
        </div>
        <GraphField W={624} H={256} nodes={nodes} links={links} P={P} dark={dark} grid small
          open={open} onOpen={id => setOpen(o => ({ ...o, [id]: !o[id] }))} />
      </div>
      <div style={{ marginTop: 10, fontFamily: MONO, fontSize: 11, color: P.sub }}>
        Der Pfeil im Kästchen klappt die Subkriterien auf und zu — die Kanten wachsen mit, die Nachbarn bleiben stehen.
      </div>
    </div>
  )
}

/* 06 — während der Arbeit: arbeitender Knoten mit Orb 20 und laufender Uhr */
function V06({ dark }: { dark: boolean }) {
  const P = dark ? DARK : LIGHT
  const t = useLive(0)
  const nodes: Pos[] = [
    { id: 'g', label: 'Ziel · Release 0.3', x: 16, y: 100, w: 158, h: 44, s: 'done' },
    { id: 'r1', label: 'Recherche', x: 250, y: 26, w: 176, h: 40, s: 'done', note: '8/8 Quellen' },
    { id: 'r2', label: 'Nachweis prüfen', x: 250, y: 116, w: 176, h: 40, s: 'run', note: fmt(212 + t) },
    { id: 'r3', label: 'Bericht schreiben', x: 250, y: 206, w: 176, h: 40, s: 'open' },
    { id: 'e1', label: 'Kriterium S04', x: 472, y: 96, w: 166, h: 32, s: 'done' },
    { id: 'e2', label: 'Kriterium PX-06', x: 472, y: 134, w: 166, h: 32, s: 'open' },
  ]
  const links: Link[] = [{ f: 'g', t: 'r1' }, { f: 'g', t: 'r2' }, { f: 'g', t: 'r3' }, { f: 'r2', t: 'e1' }, { f: 'r2', t: 'e2' }]
  return (
    <div style={{ padding: 14 }}>
      <div style={{ border: `1px solid ${P.line}`, background: P.surface }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '7px 10px', borderBottom: `1px solid ${P.hair}` }}>
          <ThinkingOrb state="working" size={20} theme={dark ? 'dark' : 'light'} />
          <span style={{ fontFamily: SANS, fontSize: 12.5, fontWeight: 600, color: P.ink }}>läuft · Nachweis prüfen</span>
          <span style={{ fontFamily: MONO, fontSize: 11, color: P.sub }} data-runclock>seit {fmt(212 + t)}</span>
          <motion.span animate={{ opacity: [0.5, 1, 0.5] }} transition={{ duration: 1.9, repeat: Infinity, ease: 'easeInOut' }}
            style={{ marginLeft: 'auto', fontFamily: MONO, fontSize: 11, color: P.sub }}>ein Knoten arbeitet, alles andere steht still</motion.span>
        </div>
        <GraphField W={650} H={250} nodes={nodes} links={links} P={P} dark={dark} grid small />
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '7px 10px', borderTop: `1px solid ${P.hair}`, fontFamily: MONO, fontSize: 11, color: P.sub }}>
          <IconClock size={11} stroke={2} color={P.ink} />
          die Uhr läuft wirklich (setInterval) — sie gehört dem Knoten, nicht dem Bildschirm
        </div>
      </div>
    </div>
  )
}

/* 07 — nach Fehler: ✕ am Knoten, blockierte Folgeknoten, Weg prüfen */
function V07({ dark }: { dark: boolean }) {
  const P = dark ? DARK : LIGHT
  const [weg, setWeg] = useState(false)
  const nodes: Pos[] = [
    { id: 'g', label: 'Ziel · UI-Abnahme', x: 14, y: 104, w: 156, h: 44, s: 'run' },
    { id: 'x1', label: 'Overlay-Lint', x: 250, y: 34, w: 196, h: 40, s: 'fail', note: '1 Befund' },
    { id: 'x2', label: 'Anker-Prüfung', x: 250, y: 180, w: 196, h: 40, s: 'done', note: '28/28' },
    { id: 'y1', label: weg ? 'Bericht erzeugen' : 'Bericht erzeugen', x: 486, y: 8, w: 172, h: 34, s: 'blocked' },
    { id: 'y2', label: weg ? 'Nachweise anhängen' : 'Nachweise anhängen', x: 486, y: 50, w: 172, h: 34, s: 'blocked' },
    { id: 'y3', label: 'Vorschau öffnen', x: 486, y: 182, w: 172, h: 34, s: 'open' },
  ]
  const links: Link[] = [{ f: 'g', t: 'x1' }, { f: 'g', t: 'x2' }, { f: 'x1', t: 'y1' }, { f: 'x1', t: 'y2' }, { f: 'x2', t: 'y3' }]
  return (
    <div style={{ padding: 14 }}>
      <div style={{ border: `1.5px solid ${P.ink}`, background: P.surface }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 10px', borderBottom: `1px solid ${P.hair}` }}>
          <motion.span animate={{ opacity: [1, 0.35, 1] }} transition={{ duration: 1.8, repeat: Infinity, ease: 'easeInOut' }}
            style={{ display: 'inline-flex', color: P.ink }}><IconX size={14} stroke={2.6} /></motion.span>
          <span style={{ fontFamily: SANS, fontSize: 12.5, fontWeight: 600, color: P.ink }}>Overlay-Lint fehlgeschlagen</span>
          <span style={{ fontFamily: MONO, fontSize: 11, color: P.sub }}>2 Folgeknoten blockiert · nichts wird blind wiederholt</span>
          <span style={{ flex: 1 }} />
          <Btn P={P} primary={!weg} onClick={() => setWeg(w => !w)}>
            <IconRoute size={11} stroke={2} /> {weg ? 'Weg verbergen' : 'Weg prüfen'}
          </Btn>
        </div>
        <GraphField W={672} H={230} nodes={nodes} links={links} P={P} dark={dark} grid small
          sel={weg ? 'x1' : null} />
        <AnimatePresence initial={false}>
          {weg && (
            <motion.div key="weg" data-fixpath initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }}
              exit={{ height: 0, opacity: 0 }} transition={SPRING} style={{ overflow: 'hidden', borderTop: `1px solid ${P.hair}` }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 10px' }}>
                <IconRefresh size={12} stroke={2} color={P.ink} />
                <span style={{ fontFamily: SANS, fontSize: 11.5, color: P.ink }}>
                  Weg: Overlay-Lint erneut prüfen → Bericht erzeugen → Nachweise anhängen
                </span>
                <span style={{ flex: 1 }} />
                <span style={{ fontFamily: MONO, fontSize: 11, color: P.sub }}>blockiert, bis der Lint grün ist</span>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
        <Legend P={P} items={[['open', 'offen'], ['run', 'läuft'], ['done', 'belegt'], ['fail', 'Fehler'], ['blocked', 'blockiert']]} />
      </div>
    </div>
  )
}

/* 08 — Übersicht: kleiner Maßstab, Legende, „auf Mitte“ */
const PW = 560, PH = 316
function V08({ dark }: { dark: boolean }) {
  const P = dark ? DARK : LIGHT
  const panX = useMotionValue(0), panY = useMotionValue(0)
  const [scale, setScale] = useState(0.6)
  const [sel, setSel] = useState<string | null>(null)
  const aufMitte = () => {
    animate(panX, 0, SPRING as never)
    animate(panY, 0, SPRING as never)
    setScale(0.6)
  }
  return (
    <div style={{ padding: 14 }}>
      <div style={{ border: `1px solid ${P.line}`, background: P.surface }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '7px 10px', borderBottom: `1px solid ${P.hair}` }}>
          <IconListTree size={13} stroke={2} color={P.ink} />
          <span style={{ fontFamily: SANS, fontSize: 12.5, fontWeight: 600, color: P.ink }}>Übersicht {Math.round(scale * 100)} %</span>
          <span style={{ fontFamily: MONO, fontSize: 11, color: P.sub }}>ziehen verschiebt · {Math.round(PW * scale)} × {Math.round(PH * scale)} px</span>
          <span style={{ flex: 1 }} />
          <Btn P={P} onClick={() => setScale(s => Math.max(0.4, Math.round((s - 0.15) * 100) / 100))}><IconZoomOut size={11} stroke={2} /></Btn>
          <Btn P={P} onClick={() => setScale(s => Math.min(1, Math.round((s + 0.15) * 100) / 100))}><IconZoomIn size={11} stroke={2} /></Btn>
          <Btn P={P} primary onClick={aufMitte}><IconCrosshair size={11} stroke={2} /> auf Mitte</Btn>
        </div>
        <div style={{ position: 'relative', height: 300, overflow: 'hidden', background: P.bg }}>
          <div style={{ position: 'absolute', left: '50%', top: '50%', width: PW, height: PH, marginLeft: -PW / 2, marginTop: -PH / 2 }}>
            <motion.div drag dragMomentum={false} dragElastic={0.06}
              dragConstraints={{ left: -190, right: 190, top: -150, bottom: 150 }}
              animate={{ scale }} style={{ x: panX, y: panY, width: PW, height: PH, cursor: 'grab' }}>
              <GraphField W={PW} H={PH} nodes={OVER_NODES} links={OVER_LINKS} P={P} dark={dark} grid small framed sel={sel} onSel={setSel} />
            </motion.div>
          </div>
          <span style={{ position: 'absolute', left: 10, bottom: 8, fontFamily: MONO, fontSize: 11, color: P.faint }}>
            {sel ? `gewählt: ${sel}` : 'kein Knoten gewählt'}
          </span>
        </div>
        <Legend P={P} />
      </div>
    </div>
  )
}

/* 09 — sehr kompakt: eine Zeile, „Graph öffnen“ klappt das Tab auf */
function V09({ dark }: { dark: boolean }) {
  const P = dark ? DARK : LIGHT
  const [open, setOpen] = useState(false)
  const [tab, setTab] = useState('Graph')
  return (
    <div style={{ padding: 14 }}>
      <div style={{ border: `1px solid ${P.line}`, background: P.surface, display: 'flex', alignItems: 'center', gap: 10, padding: '8px 10px' }}>
        <ThinkingOrb state="working" size={20} theme={dark ? 'dark' : 'light'} />
        <span style={{ fontFamily: SANS, fontSize: 12.5, fontWeight: 600, color: P.ink }}>Plan · 6/9 Kriterien</span>
        <span style={{ position: 'relative', width: 150, height: 4, background: P.hair, flex: '0 0 150px' }}>
          <motion.span initial={{ width: 0 }} animate={{ width: '66%' }} transition={{ ...SPRING, delay: 0.2 }}
            style={{ position: 'absolute', left: 0, top: 0, height: 4, background: P.ink }} />
        </span>
        <span style={{ fontFamily: MONO, fontSize: 11, color: P.sub }}>66 %</span>
        <span style={{ flex: 1 }} />
        <Btn P={P} primary onClick={() => setOpen(o => !o)}>
          <IconLayoutGrid size={11} stroke={2} /> {open ? 'Graph schließen' : 'Graph öffnen'}
        </Btn>
      </div>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div key="flat" initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }} transition={SPRING} style={{ overflow: 'hidden' }}>
            <div style={{ border: `1px solid ${P.line}`, borderTop: 'none', background: P.surface }}>
              <TabBar uid="v09" tabs={['Chat', 'Graph', 'Nachweise']} active={tab} onPick={setTab} P={P} />
              <GraphField W={660} H={240} nodes={TAB_NODES} links={TAB_LINKS} P={P} dark={dark} grid axis small />
            </div>
          </motion.div>
        )}
      </AnimatePresence>
      <div style={{ marginTop: 10, fontFamily: MONO, fontSize: 11, color: P.sub }}>
        Die Kompaktzeile trägt Orb, Fortschritt und den Weg ins Tab — dieselbe Fläche wie Variante 03, nur auf Klick.
      </div>
    </div>
  )
}

/* 10 — dunkle Fassung des Favoriten, Vollbild */
function V10({ dark }: { dark: boolean }) {
  const P = dark ? DARK : LIGHT
  const [tab, setTab] = useState('Graph')
  const [sel, setSel] = useState<string>('l4')
  const d = DETAILS[sel]
  return (
    <div style={{ padding: 14, background: P.bg }}>
      <div style={{ border: `1px solid ${P.line}`, background: P.surface, display: 'flex', flexDirection: 'column', minHeight: 470 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '9px 12px', borderBottom: `1px solid ${P.line}` }}>
          <ThinkingOrb state="working" size={20} theme={dark ? 'dark' : 'light'} />
          <span style={{ fontFamily: SANS, fontSize: 13, fontWeight: 600, color: P.ink }}>skaldir ui-abnahme</span>
          <span style={{ fontFamily: MONO, fontSize: 11, color: P.sub }}>Plan · 6/9 Kriterien · 4 belegt</span>
          <span style={{ flex: 1 }} />
          <span style={{ fontFamily: MONO, fontSize: 11, color: P.faint }}>esc esc beendet</span>
        </div>
        <TabBar uid="v10" tabs={['Chat', 'Graph', 'Nachweise']} active={tab} onPick={setTab} P={P} />
        <div style={{ display: 'flex', flex: 1, gap: 0, minHeight: 300 }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <GraphField W={640} H={292} nodes={TAB_NODES.map(n => ({ ...n, y: Math.round(n.y * 1.2) }))} links={TAB_LINKS}
              P={P} dark={dark} grid axis sel={sel} onSel={setSel} />
          </div>
          <AnimatePresence mode="wait" initial={false}>
            <motion.div key={sel} initial={{ opacity: 0, x: 14 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: 10 }}
              transition={SPRING} style={{ width: 258, flex: '0 0 258px', borderLeft: `1px solid ${P.line}`, padding: 12 }}>
              <div style={{ fontFamily: MONO, fontSize: 11, letterSpacing: 0.6, color: P.faint, textTransform: 'uppercase' }}>Kriterium</div>
              <div style={{ fontFamily: SANS, fontSize: 13, fontWeight: 600, color: P.ink, margin: '3px 0 10px' }}>{d.crit}</div>
              <div style={{ fontFamily: MONO, fontSize: 11, letterSpacing: 0.6, color: P.faint, textTransform: 'uppercase' }}>Nachweis</div>
              <div style={{ fontFamily: MONO, fontSize: 11, color: P.sub, margin: '3px 0 10px', lineHeight: 1.5 }}>{d.ev}</div>
              <div style={{ fontFamily: MONO, fontSize: 11, letterSpacing: 0.6, color: P.faint, textTransform: 'uppercase' }}>Zustand</div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 7, margin: '3px 0 10px' }}>
                <Glyph s={SEL_STATE[sel] || 'open'} P={P} dark={dark} />
                <span style={{ fontFamily: MONO, fontSize: 11, color: P.ink }}>{d.st}</span>
              </div>
              <div style={{ borderTop: `1px solid ${P.hair}`, paddingTop: 9 }}>
                <div style={{ fontFamily: MONO, fontSize: 11, letterSpacing: 0.6, color: P.faint, textTransform: 'uppercase' }}>als nächstes</div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 4, fontFamily: SANS, fontSize: 11.5, color: P.ink }}>
                  <IconRoute size={12} stroke={2} color={P.ink} /> {d.next}
                </div>
              </div>
            </motion.div>
          </AnimatePresence>
        </div>
        <Legend P={P} />
      </div>
    </div>
  )
}

/* ================================================================ Export */

export default {
  '01 · Graph inline im Chat — eingeklappt (Kopfzeile mit Orb)': () => (
    <Shell label="01 · inline, eingeklappt" sub="nur die Kopfzeile „Plan · 6/9 Kriterien“ mit Orb — Klick auf die Zeile klappt die fünf Kriterien auf (echter useState)">
      {dark => <V01 dark={dark} />}
    </Shell>
  ),

  '02 · Graph inline aufgeklappt — fünf Kriterien, drei belegt': () => (
    <Shell label="02 · inline, aufgeklappt" sub="fünf Kriterien als Kästchen, Verbindungen als Kurven (Motion zeichnet sie), drei belegt = durchgestrichen und weichgezeichnet — Knoten anklickbar">
      {dark => <V02 dark={dark} />}
    </Shell>
  ),

  '03 · Graph als eigenes Tab — Reiter oben, Achsenkreuz-Muster': () => (
    <Shell label="03 · eigenes Tab" sub="Reiterleiste Chat/Graph/Nachweise, Fläche mit Raster und Achsenkreuz, Knoten anklickbar — reine Geometrie, kein React Flow">
      {dark => <V03 dark={dark} />}
    </Shell>
  ),

  '04 · Knoten ausgewählt — Seitenkarte mit Nachweis und „als nächstes“': () => (
    <Shell label="04 · Knoten ausgewählt" wide sub="Rand hervorgehoben, Seitenkarte federt ein: Kriterium, Nachweis, Zustand und „als nächstes“ — Klick auf einen anderen Knoten wechselt sie">
      {dark => <V04 dark={dark} />}
    </Shell>
  ),

  '05 · Subkriterien über drei Ebenen — Ein-/Ausklappen mit Feder': () => (
    <Shell label="05 · drei Ebenen" sub="Knoten mit Pfeil klappen ihre Subkriterien auf und zu; Kanten wachsen mit, die Nachbarn bleiben stehen — Zähler oben läuft mit">
      {dark => <V05 dark={dark} />}
    </Shell>
  ),

  '06 · Während der Arbeit — arbeitender Knoten mit Orb 20 und laufender Uhr': () => (
    <Shell label="06 · während der Arbeit" sub="ein Knoten arbeitet (Orb 20), sein Zeitfeld läuft sekündlich — alles andere steht still und ist belegt oder offen">
      {dark => <V06 dark={dark} />}
    </Shell>
  ),

  '07 · Nach Fehler — ✕ am Knoten, blockierte Folgeknoten, Weg prüfen': () => (
    <Shell label="07 · nach Fehler" sub="Knoten mit ✕, die zwei Folgeknoten ausgegraut und gestrichelt verbunden; „Weg prüfen“ legt den Ausweg darunter offen (echter Zustand)">
      {dark => <V07 dark={dark} />}
    </Shell>
  ),

  '08 · Übersicht — kleiner Maßstab, Legende, „auf Mitte“': () => (
    <Shell label="08 · Übersicht" sub="Maßstab 60 %, ziehen verschiebt die Fläche, Zoom-Knöpfe ändern den Maßstab, „auf Mitte“ federt zurück; Legende offen · läuft · belegt">
      {dark => <V08 dark={dark} />}
    </Shell>
  ),

  '09 · Sehr kompakt — eine Zeile mit Fortschritt und „Graph öffnen“': () => (
    <Shell label="09 · sehr kompakt" sub="eine Zeile trägt Orb, Fortschrittsleiste und „Graph öffnen“ — der Klick klappt das Graph-Tab darunter auf, derselbe Knopf schließt es">
      {dark => <V09 dark={dark} />}
    </Shell>
  ),

  '10 · Dunkle Fassung des Favoriten (Vollbild dunkel)': () => (
    <Shell label="10 · Favorit — dunkel" wide darkStart sub="das Tab-Tab als Ganzseite im dunklen Thema: gleiche Geometrie, gleiche Federn, gleiche Legende — nur invertierte Tinte">
      {dark => <V10 dark={dark} />}
    </Shell>
  ),
}
