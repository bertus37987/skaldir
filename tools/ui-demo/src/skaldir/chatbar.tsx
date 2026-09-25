/** abgenommene Chat-Bar: Referenz-Pille + Goal-Bar + Upload + Selector
 *  Quelle: designs/GEWAEHLT/code/chatbar.tsx (abgenommen am 17.09.2026) — unveraendert uebernommen. */
/**
 * Chat-Bar — die GEWAEHLTE Fassung (Referenz-Pille + Goal-Bar).
 *
 * Owner-Entscheidung 17.09.2026: "1 und weg mit dem opencode & free — einfach ersetzen durch
 * den Model & Reasoning Selector."
 *
 * Aufbau wie die Referenz aus dem Screenshot: flache, voll gerundete Pille, duenner heller Rand,
 * keine Trennlinien, nur Schwarz/Weiss/Grau. Links "+" und der Platzhalter "Start with a goal",
 * rechts gruppiert Punkteraster, "Med" und der MODEL & REASONING SELECTOR (schwarzer Knopf).
 * KEIN Modelltext mehr in der Leiste — die Auswahl steckt im Selector.
 * Darueber die Goal-Bar: Ziel, Kriterien mit Haekchen rechts vom Wort, links oben einklappbar,
 * Erledigtes weichgezeichnet, "momentan" samt Subgoals offen.
 */
import React, { useState } from 'react'
import { ChevronDown, PanelTopClose, PanelTopOpen } from 'lucide-react'

// @ts-expect-error — Laufzeit-Alias (siehe vite.config.ts)
import { ThinkingOrb } from 'thinking-orbs'

const P = { bg: '#F7F7F5', pill: '#FFFFFF', line: '#E5E5E3', hair: '#EFEFED', grey: '#8A8A8A', mid: '#6E6E6E', ink: '#171717' }
const MONO = 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace'
const SANS = 'Geist, system-ui, sans-serif'

const MODELS = [
  { id: 'm1', name: 'opencode', sub: 'free' },
  { id: 'm2', name: 'klarer', sub: 'local' },
  { id: 'm3', name: 'waehler', sub: 'remote' }
]
const LEVELS = ['Low', 'Med', 'High']

const Plus = () => <span style={{ fontSize: 17, color: P.grey, lineHeight: 1 }}>+</span>
const DotGrid = () => (
  <span style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 3px)', gap: 2 }}>
    {Array.from({ length: 9 }).map((_, i) => <span key={i} style={{ width: 3, height: 3, borderRadius: 999, background: P.grey }} />)}
  </span>
)
const Wave = ({ size = 30 }) => (
  <span style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: size, height: size, borderRadius: 999, background: P.ink }}>
    <svg width={size * 0.55} height={size * 0.4} viewBox="0 0 18 12">
      <polyline points="0,6 3,6 5,2 7,10 9,4 11,7 13,3 15,6 18,6" fill="none" stroke="#FFFFFF" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  </span>
)
const Med = () => (
  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 12.5, color: P.grey }}>Med<ChevronDown size={11} strokeWidth={2} color={P.grey} /></span>
)

/** Model & Reasoning Selector — die einzige Modell-/Denk-Bedienung in der Leiste. */
function Selector() {
  const [open, setOpen] = useState(false)
  const [model, setModel] = useState(MODELS[0])
  const [level, setLevel] = useState('Med')
  return (
    <span style={{ position: 'relative', display: 'inline-flex', alignItems: 'center', gap: 4 }}>
      <span data-selector onClick={() => setOpen(o => !o)} title="Modell und Reasoning waehlen"
        style={{ display: 'inline-flex', alignItems: 'center', gap: 4, cursor: 'pointer' }}>
        <Wave />
        <ChevronDown size={12} strokeWidth={2.2} color={P.ink} />
      </span>
      {open && (
        <div data-selectorpopover style={{
          position: 'absolute', right: 0, bottom: 42, width: 226, padding: 10, borderRadius: 14,
          background: P.pill, border: `1px solid ${P.line}`, boxShadow: '0 14px 34px rgba(0,0,0,0.16)', zIndex: 5
        }}>
          <div style={{ fontFamily: MONO, fontSize: 9.5, letterSpacing: 1, color: P.grey, marginBottom: 6 }}>MODELL</div>
          {MODELS.map(m => (
            <div key={m.id} onClick={() => { setModel(m); setOpen(false) }} data-model={m.id}
              style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '5px 6px', cursor: 'pointer', background: model.id === m.id ? P.bg : 'transparent' }}>
              <span style={{ width: 6, height: 6, borderRadius: 999, background: P.ink, opacity: model.id === m.id ? 1 : 0.25 }} />
              <span style={{ fontSize: 12.5, color: P.ink }}>{m.name}</span>
              <span style={{ marginLeft: 'auto', fontFamily: MONO, fontSize: 10, color: P.grey }}>{m.sub}</span>
            </div>
          ))}
          <div style={{ fontFamily: MONO, fontSize: 9.5, letterSpacing: 1, color: P.grey, margin: '9px 0 6px' }}>REASONING</div>
          <div style={{ display: 'flex', gap: 6 }}>
            {LEVELS.map(l => (
              <span key={l} onClick={() => { setLevel(l); setOpen(false) }} data-level={l}
                style={{ flex: 1, textAlign: 'center', padding: '4px 0', border: `1px solid ${l === level ? P.ink : P.line}`, fontSize: 11.5, color: P.ink, cursor: 'pointer' }}>{l}</span>
            ))}
          </div>
          <div style={{ marginTop: 9, fontFamily: MONO, fontSize: 9.5, color: P.grey }}>
            gilt fuer die naechste Nachricht · {model.name} · {level}
          </div>
        </div>
      )}
    </span>
  )
}

type Node = { id: string; label: string; done?: boolean; note?: string; children?: Node[] }
const GOAL: Node[] = [
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

function Row({ node, ancestors, isLast, openMap, toggle }) {
  const kids = node.children || []
  const done = leaves(node).every(l => l.done === true)
  const depth = ancestors.length
  const open = openMap[node.id] !== undefined ? openMap[node.id] : OPEN_DEFAULT.has(node.id)
  const soft = done ? { opacity: 0.6, filter: 'blur(0.3px)' } : {}
  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', minHeight: 24, whiteSpace: 'nowrap' }}>
        <span style={{ fontFamily: MONO, fontSize: 11.5, whiteSpace: 'pre', color: P.ink, ...soft }}>
          {ancestors.map((a, i) => <span key={i}>{a.last ? '   ' : '│  '}</span>)}{isLast ? '└─ ' : '├─ '}
        </span>
        <span onClick={() => kids.length && toggle(node.id)} style={{ width: 13, fontSize: 10, color: P.ink, cursor: kids.length ? 'pointer' : 'default' }}>
          {kids.length ? (open ? '▾' : '▸') : ''}
        </span>
        <span style={{ fontFamily: SANS, fontSize: depth === 0 ? 12.5 : 12, fontWeight: depth === 0 ? 600 : 400, color: P.ink, ...soft }}>{node.label}</span>
        <span style={{ marginLeft: 7, width: 12, height: 12, border: `1.3px solid ${P.ink}`, background: done ? P.ink : 'transparent', color: '#FFF', fontSize: 8.5, lineHeight: '11px', textAlign: 'center', flex: '0 0 auto' }}>{done ? '✓' : ''}</span>
        {node.note && <span style={{ fontFamily: MONO, fontSize: 9.5, marginLeft: 7, color: P.ink, opacity: done ? 0.6 : 0.8 }}>{node.note}</span>}
        {node.id === 'b' && depth === 0 && (
          <span style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            <span style={{ fontFamily: MONO, fontSize: 9.5, color: P.mid }}>momentan</span>
            <ThinkingOrb state="working" size={20} theme="light" />
          </span>
        )}
      </div>
      {open && kids.map((k, i) => (
        <Row key={k.id} node={k} ancestors={[...ancestors, { last: isLast }]} isLast={i === kids.length - 1} openMap={openMap} toggle={toggle} />
      ))}
    </div>
  )
}

function GoalBar({ open, onToggle }) {
  const [openMap, setOpenMap] = useState({})
  const toggle = (id) => setOpenMap(m => ({ ...m, [id]: !(m[id] !== undefined ? m[id] : OPEN_DEFAULT.has(id)) }))
  return (
    <div style={{ background: P.pill, borderBottom: `1px solid ${P.line}` }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 10px' }}>
        <span onClick={onToggle} title={open ? 'Ziel einklappen' : 'Ziel ausklappen'} style={{ display: 'inline-flex', cursor: 'pointer' }}>
          {open ? <PanelTopClose size={14} strokeWidth={1.8} color={P.ink} /> : <PanelTopOpen size={14} strokeWidth={1.8} color={P.ink} />}
        </span>
        <span style={{ fontFamily: SANS, fontSize: 12, fontWeight: 600, color: P.ink }}>skaldir ui-abnahme</span>
        <span style={{ fontFamily: MONO, fontSize: 10, color: P.mid }}>{TOTAL.done}/{TOTAL.total} ✓</span>
        <span style={{ marginLeft: 'auto', fontFamily: MONO, fontSize: 9.5, color: P.grey }}>esc esc beendet</span>
      </div>
      {open && (
        <div style={{ padding: '0 10px 7px' }}>
          {GOAL.map((n, i) => <Row key={n.id} node={n} ancestors={[]} isLast={i === GOAL.length - 1} openMap={openMap} toggle={toggle} />)}
          <div style={{ marginTop: 3, fontFamily: MONO, fontSize: 10.5, color: P.grey }}>└─ Kriterium ergaenzen</div>
        </div>
      )}
    </div>
  )
}

export const ChatBar = () => {
  const [goalOpen, setGoalOpen] = useState(true)
  return (
    <div style={{ padding: 20, background: P.bg, fontFamily: SANS, width: '100%', maxWidth: 900, boxSizing: 'border-box' }}>
      <div style={{ fontFamily: MONO, fontSize: 12, fontWeight: 600 }}>Chat-Bar (gewaehlt) — Referenz-Pille mit Goal-Bar</div>
      <div style={{ fontFamily: MONO, fontSize: 10.5, color: P.mid, margin: '3px 0 12px' }}>
        kein Modelltext in der Leiste · der schwarze Knopf ist der Modell-&-Reasoning-Selector (anklicken)
      </div>
      <div style={{ height: 420, borderRadius: 14, border: `1px solid ${P.line}`, background: P.bg, display: 'flex', flexDirection: 'column' }}>
        <div style={{ flex: 1, padding: 14, display: 'flex', flexDirection: 'column', gap: 8 }}>
          <span style={{ alignSelf: 'flex-end', fontFamily: MONO, fontSize: 11.5, background: P.ink, color: '#FFF', padding: '5px 9px', borderRadius: 999 }}>zerleg das in drei spuren</span>
          <span style={{ alignSelf: 'flex-start', maxWidth: 420, fontFamily: MONO, fontSize: 11.5, background: P.pill, border: `1px solid ${P.line}`, padding: '6px 10px', borderRadius: 12 }}>drei subagenten gerufen.</span>
        </div>
        <div style={{ padding: 14, display: 'flex', justifyContent: 'center' }}>
          <div style={{ width: '100%', borderRadius: 24, background: P.pill, border: `1px solid ${P.line}` }}>
            <GoalBar open={goalOpen} onToggle={() => setGoalOpen(o => !o)} />
            <div style={{ padding: 10 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, height: 58, padding: '0 12px 0 16px', borderRadius: 999, background: P.pill, border: `1px solid ${P.line}` }}>
                <Plus />
                <span style={{ fontFamily: SANS, fontSize: 14.5, color: P.grey, flex: 1 }}>Start with a goal</span>
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 14 }}>
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}><DotGrid /><ChevronDown size={11} strokeWidth={2} color={P.grey} /></span>
                  <Med />
                  <Selector />
                </span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

export default { '01 · Chat-Bar (gewaehlt): Referenz-Pille mit Modell-&-Reasoning-Selector': () => <ChatBar /> }
