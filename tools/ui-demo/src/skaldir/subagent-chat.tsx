/** abgenommenes Subagenten-Fenster: schmale Spur, ziehbar, −/✕
 *  Quelle: designs/GEWAEHLT/code/subagent-chat.tsx (abgenommen am 17.09.2026) — unveraendert uebernommen. */
/**
 * Subagenten-Fenster — die GEWÄHLTE Fassung (vorher Option 1: „Eine schmale Spur").
 *
 * Owner-Entscheidung 17.09.2026: „Subagent chat einfach genau Nummer 1."
 *
 * Eigenschaften der gewählten Fassung: eine schmale Spur (234 px), am Kopf greifbar und frei
 * über dem Chat verschiebbar, − klappt sie auf eine dünne Kopfleiste zusammen, ✕ schließt sie.
 * Kopf trägt die Dauer, unten sitzen Beenden-Viereck und viereckige CLI-Kontextanzeige
 * nebeneinander. Der Hintergrund ist weichgezeichnet, die eigene Chatbar bleibt stehen.
 */
import React, { useRef, useState } from 'react'
import { X } from 'lucide-react'

// @ts-expect-error — Laufzeit-Alias (siehe vite.config.ts)
import { ThinkingOrb } from 'thinking-orbs'

const C = { ink: '#171717', sub: '#595959', faint: '#8C8C8C', line: '#DEDEDE', hair: '#EDEDED', bg: '#FAFAFA', surface: '#FFFFFF' }
const MONO = 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace'
const SANS = 'Geist, system-ui, sans-serif'

const LINES = ['öffne tools/check_ui_overlay_anchors.py', 'lese 28 Anker-Erwartungen', 'messe #composer bei 1280×800', 'vergleiche: 27 von 28 stimmen']

const CliCtx = ({ dim }: { dim?: boolean }) => (
  <span style={{ border: `1px solid ${C.ink}`, padding: '2px 5px', display: 'inline-flex', alignItems: 'center', gap: 6, fontFamily: MONO, fontSize: 9.5, color: C.ink, opacity: dim ? 0.45 : 1 }}>
    {Array.from({ length: 10 }).map((_, i) => <span key={i} style={{ width: 5, height: 9, background: i < 6 ? C.ink : 'transparent', border: `1px solid ${C.ink}` }} />)}
    <span>62%</span>
  </span>
)

function Win() {
  const [pos, setPos] = useState({ x: 40, y: 30 })
  const [collapsed, setCollapsed] = useState(false)
  const [closed, setClosed] = useState(false)
  const [stopped, setStopped] = useState(false)
  const drag = useRef<{ dx: number; dy: number } | null>(null)

  if (closed) return (
    <span onClick={() => setClosed(false)} title="Fenster öffnen" style={{ position: 'absolute', left: pos.x, top: pos.y, fontFamily: MONO, fontSize: 10.5, padding: '5px 9px', border: `1px solid ${C.line}`, background: 'rgba(255,255,255,0.98)', boxShadow: '0 10px 28px rgba(0,0,0,0.18)', cursor: 'pointer' }}>◆ prüfer t 00:42 ▸</span>
  )
  return (
    <div data-subwin style={{ position: 'absolute', left: pos.x, top: pos.y, width: 234, background: 'rgba(255,255,255,0.99)', border: `1px solid ${C.line}`, boxShadow: '0 14px 38px rgba(0,0,0,0.26)', fontFamily: MONO }}>
      <div onPointerDown={e => { drag.current = { dx: e.clientX - pos.x, dy: e.clientY - pos.y }; (e.currentTarget as Element).setPointerCapture?.(e.pointerId) }}
        onPointerMove={e => { if (drag.current) setPos({ x: e.clientX - drag.current.dx, y: e.clientY - drag.current.dy }) }}
        onPointerUp={() => { drag.current = null }}
        style={{ display: 'flex', alignItems: 'center', gap: 7, padding: '5px 7px', cursor: 'grab', background: C.bg, borderBottom: collapsed ? 'none' : `1px solid ${C.line}`, touchAction: 'none' }}>
        <ThinkingOrb state={(stopped ? 'breathing' : 'working') as never} size={20} theme="light" />
        <span style={{ fontSize: 10, color: C.sub }}>◆ prüfer</span>
        <span style={{ fontSize: 10, color: C.ink }}>t 00:42</span>
        {collapsed && <span style={{ fontSize: 9, color: C.faint }}>▮▮▮▮▮▮▯▯▯▯ 62%</span>}
        <span style={{ marginLeft: collapsed ? 6 : 'auto', display: 'inline-flex', gap: 4 }}>
          <span onClick={() => setCollapsed(c => !c)} title={collapsed ? 'ausklappen' : 'einklappen'} style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 20, height: 18, border: `1px solid ${C.line}`, cursor: 'pointer', fontSize: 11 }}>−</span>
          <span onClick={() => setClosed(true)} title="schließen" style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 20, height: 18, border: `1px solid ${C.line}`, cursor: 'pointer' }}><X size={9} strokeWidth={2} color={C.ink} /></span>
        </span>
      </div>
      {!collapsed && (
        <>
          <div style={{ padding: 7, height: 104, overflow: 'hidden' }}>
            {LINES.map((l, k) => <div key={k} style={{ fontSize: 9.5, color: C.ink, opacity: stopped ? 0.55 : 1, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>› {l}{k === LINES.length - 1 && !stopped ? <span style={{ color: C.sub }}> ▍</span> : null}</div>)}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 7, padding: '6px 7px', borderTop: `1px solid ${C.line}` }}>
            <span onClick={() => setStopped(v => !v)} title={stopped ? 'Weiter' : 'Beenden'} style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 24, height: 20, background: stopped ? 'transparent' : C.ink, border: `1px solid ${C.ink}`, color: stopped ? C.ink : C.surface, fontSize: 9.5, cursor: 'pointer' }}>{stopped ? '▶' : '■'}</span>
            <CliCtx dim={stopped} />
          </div>
        </>
      )}
    </div>
  )
}

export const SubagentFenster = () => (
  <div style={{ padding: 20, background: C.bg, fontFamily: SANS, color: C.ink, width: '100%', maxWidth: 880, boxSizing: 'border-box' }}>
    <div style={{ fontFamily: MONO, fontSize: 12, fontWeight: 600 }}>Subagenten-Fenster — gewählte Fassung (schmale Spur)</div>
    <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.sub, margin: '3px 0 10px' }}>
      234 px breit · Kopf greifen zum Verschieben · − klappt ein · ✕ schließt · Hintergrund weichgezeichnet
    </div>
    <div style={{ position: 'relative', height: 440, border: `1px solid ${C.line}`, background: C.bg, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      <div style={{ flex: 1, padding: 14, display: 'flex', flexDirection: 'column', gap: 8 }}>
        <span style={{ alignSelf: 'flex-end', fontFamily: MONO, fontSize: 11.5, background: C.ink, color: C.surface, padding: '5px 9px' }}>&gt; zerleg das in drei spuren</span>
        <span style={{ alignSelf: 'flex-start', maxWidth: 420, fontFamily: MONO, fontSize: 11.5, background: C.surface, border: `1px solid ${C.line}`, padding: '6px 9px' }}>prüfer läuft in seinem fenster.</span>
      </div>
      <div style={{ padding: '0 12px 12px' }}>
        <div style={{ border: `1px solid ${C.line}`, background: C.surface, padding: '6px 10px', fontFamily: MONO, fontSize: 12, color: C.faint }}>nachricht an skaldir ▍</div>
      </div>
      <div style={{ position: 'absolute', inset: 0, backdropFilter: 'blur(6px)', WebkitBackdropFilter: 'blur(6px)', background: 'rgba(255,255,255,0.40)' }} />
      <div style={{ position: 'absolute', inset: 0 }}><Win /></div>
    </div>
  </div>
)

export default { '01 · Subagenten-Fenster (gewählt): schmale Spur': () => <SubagentFenster /> }
