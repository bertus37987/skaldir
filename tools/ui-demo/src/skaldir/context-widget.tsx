/** abgenommenes Kontext-Widget: Terminal-Kapsel, frei verschiebbar, ✕/Griff
 *  Quelle: designs/GEWAEHLT/code/context-widget.tsx (abgenommen am 17.09.2026) — unveraendert uebernommen. */
/**
 * Kontext-Widget — die GEWÄHLTE und FERTIGE Fassung (vorher Option 1).
 *
 * Owner-Entscheidung 17.09.2026: „Context widget 1 ist perfekt und fertig — soll man genauso
 * wie die Subagents überall hindraggen können."
 *
 * Also: Terminal-Block in runder Kapsel, per ✕ schließbar, und am Körper greifbar und
 * frei über die ganze Fläche verschiebbar — wie die Subagenten-Fenster.
 */
import React, { useRef, useState } from 'react'

const C = { ink: '#171717', sub: '#5A5A5A', faint: '#8C8C8C', line: '#E3E3E3', hair: '#EFEFEF', bg: '#FAFAFA', surface: '#FFFFFF' }
const MONO = 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace'
const SANS = 'Geist, system-ui, sans-serif'

function Capsule() {
  const [pos, setPos] = useState({ x: 420, y: 120 })
  const [open, setOpen] = useState(true)
  const drag = useRef<{ dx: number; dy: number } | null>(null)

  if (!open) return (
    <span onClick={() => setOpen(true)} title="Kontext öffnen" style={{
      position: 'absolute', left: pos.x, top: pos.y, display: 'inline-flex', alignItems: 'center', gap: 6,
      padding: '5px 10px', borderRadius: 999, background: 'rgba(255,255,255,0.98)', border: `1px solid ${C.line}`,
      fontFamily: MONO, fontSize: 10.5, color: C.ink, cursor: 'pointer', boxShadow: '0 6px 18px rgba(0,0,0,0.16)'
    }}>[██░░] 62% ▸</span>
  )
  return (
    <div data-ctxcapsule
      onPointerDown={e => { if ((e.target as HTMLElement).title === 'Kontext schließen') return; drag.current = { dx: e.clientX - pos.x, dy: e.clientY - pos.y }; (e.currentTarget as Element).setPointerCapture?.(e.pointerId) }}
      onPointerMove={e => { if (drag.current) setPos({ x: e.clientX - drag.current.dx, y: e.clientY - drag.current.dy }) }}
      onPointerUp={() => { drag.current = null }}
      style={{
        position: 'absolute', left: pos.x, top: pos.y, width: 208, borderRadius: 999, border: `1px solid ${C.line}`,
        background: 'rgba(255,255,255,0.98)', boxShadow: '0 10px 28px rgba(0,0,0,0.18)', padding: '9px 13px',
        display: 'flex', flexDirection: 'column', gap: 5, cursor: 'grab', touchAction: 'none'
      }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{ fontFamily: MONO, fontSize: 13, color: C.ink }}>[{'█'.repeat(6)}{'░'.repeat(4)}]</span>
        <span style={{ marginLeft: 'auto', fontFamily: MONO, fontSize: 12, color: C.ink }}>62%</span>
        <span onClick={() => setOpen(false)} title="Kontext schließen" style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 18, height: 18, borderRadius: 999, border: `1px solid ${C.line}`, fontFamily: MONO, fontSize: 10, color: C.ink, cursor: 'pointer' }}>✕</span>
      </div>
      <div style={{ display: 'flex', gap: 7, fontFamily: MONO, fontSize: 10, color: C.sub }}>
        <span>128k/200k</span><span>·</span><span>reserve 12k</span>
      </div>
    </div>
  )
}

export const KontextWidget = () => (
  <div style={{ padding: 20, background: C.bg, fontFamily: SANS, color: C.ink, width: '100%', maxWidth: 880, boxSizing: 'border-box' }}>
    <div style={{ fontFamily: MONO, fontSize: 12, fontWeight: 600 }}>Kontext-Widget — gewählt und fertig (Terminal-Kapsel)</div>
    <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.sub, margin: '3px 0 10px' }}>
      am Körper greifen und beliebig verschieben · ✕ schließt, der Griff holt sie zurück · schwebt über allem
    </div>
    <div style={{ position: 'relative', height: 440, borderRadius: 12, overflow: 'hidden', border: `1px solid ${C.line}`, background: C.bg }}>
      <div style={{ position: 'absolute', left: 20, top: 18, right: 20, height: 24, borderRadius: 6, background: '#EEEEEE' }} />
      <div style={{ position: 'absolute', left: 20, top: 54, width: 180, height: 150, borderRadius: 8, background: '#F1F1F1' }} />
      <div style={{ position: 'absolute', left: 214, top: 54, right: 20, bottom: 20, borderRadius: 8, background: '#F3F3F3' }} />
      <span style={{ position: 'absolute', left: 20, bottom: 18, fontFamily: MONO, fontSize: 10, color: C.faint }}>arbeitsfläche · hierhin lässt sich das Widget ziehen</span>
      <Capsule />
    </div>
  </div>
)

export default { '01 · Kontext-Widget (gewählt, fertig): Terminal-Kapsel, frei verschiebbar': () => <KontextWidget /> }
