/** abgenommenes Subagenten-Panel: nur offene Zeilen, − nimmt heraus
 *  Quelle: designs/GEWAEHLT/code/subagent-panel.tsx (abgenommen am 17.09.2026) — unveraendert uebernommen. */
/**
 * Subagenten-SubagentPanel — die GEWÄHLTE Fassung (vorher Option 9: „Minimal: nur Zeilen").
 *
 * Owner-Entscheidung 17.09.2026: „Subagent-panel Nummer 9, jeder offene wird selected,
 * so wie der Prüfer nach Minus geht das weg."
 *
 * Heißt konkret: das SubagentPanel zeigt nur die OFFENEN Subagenten. Jeder davon ist ausgewählt
 * (die Zeile des gerade geöffneten Fensters ist hervorgehoben). Ein Klick auf − in einer Zeile
 * nimmt genau diesen Subagenten heraus — er verschwindet aus dem SubagentPanel und erscheint unten als
 * geschlossen, wo man ihn per + zurückholt.
 */
import React, { useState } from 'react'
import { Plus, Minus } from 'lucide-react'

// @ts-expect-error — Laufzeit-Alias (siehe vite.config.ts)
import { ThinkingOrb } from 'thinking-orbs'

const C = { ink: '#171717', sub: '#595959', faint: '#8C8C8C', line: '#DEDEDE', hair: '#EDEDED', bg: '#FAFAFA', surface: '#FFFFFF' }
const MONO = 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace'
const SANS = 'Geist, system-ui, sans-serif'

type State = 'läuft' | 'wartet' | 'fertig'
type Sub = { id: string; name: string; task: string; state: State; pct: number; steps: string }
const SUBS: Sub[] = [
  { id: 's1', name: 'Prüfer', task: 'Anker und Bilder gegenmessen', state: 'läuft', pct: 62, steps: '4/7' },
  { id: 's2', name: 'Sammler', task: 'Messwerte und Screenshots sammeln', state: 'läuft', pct: 38, steps: '3/8' },
  { id: 's3', name: 'Schreiber', task: 'Bericht aus den Nachweisen bauen', state: 'wartet', pct: 0, steps: '0/4' },
  { id: 's4', name: 'Wächter', task: 'Tore prüfen: Lint, Tests, Bau', state: 'fertig', pct: 100, steps: '3/3' }
]

const Orb = ({ state }: { state: State }) => <ThinkingOrb state={(state === 'läuft' ? 'working' : state === 'wartet' ? 'breathing' : 'composing') as never} size={20} theme="light" />

export function SubagentPanel() {
  const [open, setOpen] = useState<string[]>(['s1', 's2', 's3'])
  const [active, setActive] = useState('s1')
  const closed = SUBS.filter(s => !open.includes(s.id))

  const remove = (id: string) => {
    const rest = open.filter(x => x !== id)
    setOpen(rest)
    if (active === id) setActive(rest[0] ?? '')
  }

  return (
    <div style={{ padding: 20, background: C.bg, fontFamily: SANS, color: C.ink, width: '100%', maxWidth: 880, boxSizing: 'border-box' }}>
      <div style={{ fontFamily: MONO, fontSize: 12, fontWeight: 600 }}>Subagenten-SubagentPanel — gewählte Fassung (nur offene Zeilen)</div>
      <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.sub, margin: '3px 0 10px' }}>
        jeder offene Subagent ist ausgewählt · − nimmt ihn heraus · + holt ihn zurück · Streifen über der Chatbar
      </div>
      <div style={{ position: 'relative', height: 430, border: `1px solid ${C.line}`, background: C.bg, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        <div style={{ flex: 1, padding: 14, display: 'flex', flexDirection: 'column', gap: 8 }}>
          <span style={{ alignSelf: 'flex-end', fontFamily: MONO, fontSize: 11.5, background: C.ink, color: C.surface, padding: '5px 9px' }}>&gt; zerleg das in drei spuren</span>
          <span style={{ alignSelf: 'flex-start', maxWidth: 420, fontFamily: MONO, fontSize: 11.5, background: C.surface, border: `1px solid ${C.line}`, padding: '6px 9px' }}>drei subagenten gerufen; jeder in seinem fenster.</span>
        </div>
        <div style={{ display: 'flex', gap: 6, padding: '0 12px 8px', flexWrap: 'wrap' }}>
          {SUBS.map(s => (
            <span key={s.id} onClick={() => open.includes(s.id) && setActive(s.id)} title={open.includes(s.id) ? `${s.name} zeigen` : `${s.name} ist geschlossen`}
              style={{ display: 'inline-flex', alignItems: 'center', gap: 6, padding: '4px 8px', border: `1px solid ${active === s.id && open.includes(s.id) ? C.ink : C.line}`, background: C.surface, opacity: open.includes(s.id) ? 1 : 0.45, fontFamily: MONO, fontSize: 10 }}>
              <span style={{ width: 6, height: 6, borderRadius: 999, background: s.state === 'fertig' ? C.faint : C.ink }} />
              {s.name}
              <span style={{ color: C.sub }}>{s.state === 'fertig' ? '✓' : `${s.pct}%`}</span>
            </span>
          ))}
        </div>
        <div style={{ padding: '0 12px 12px' }}>
          <div style={{ border: `1px solid ${C.line}`, background: C.surface, padding: '6px 10px', fontFamily: MONO, fontSize: 12, color: C.faint }}>nachricht an skaldir ▍</div>
        </div>

        <div style={{ position: 'absolute', left: 12, top: 12, width: 236, background: 'rgba(255,255,255,0.98)', border: `1px solid ${C.line}`, boxShadow: '0 8px 26px rgba(0,0,0,0.14)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '7px 9px', borderBottom: `1px solid ${C.line}` }}>
            <span style={{ fontFamily: MONO, fontSize: 9.5, letterSpacing: 1, color: C.sub }}>SUBAGENTEN · {open.length} OFFEN</span>
          </div>
          <div style={{ padding: 4 }}>
            {open.map(id => {
              const s = SUBS.find(x => x.id === id)!
              const sel = active === id
              return (
                <div key={id} onClick={() => setActive(id)} data-panelrow={id} data-selected={sel ? 'true' : 'false'}
                  style={{ display: 'flex', alignItems: 'center', gap: 7, padding: '6px 7px', cursor: 'pointer', background: sel ? C.bg : 'transparent', border: `1px solid ${sel ? C.line : 'transparent'}` }}>
                  <Orb state={s.state} />
                  <span style={{ flex: 1, minWidth: 0 }}>
                    <span style={{ display: 'block', fontFamily: MONO, fontSize: 11, fontWeight: sel ? 600 : 400 }}>{s.name}</span>
                    <span style={{ display: 'block', fontFamily: MONO, fontSize: 9.5, color: C.sub, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.task}</span>
                  </span>
                  <span style={{ fontFamily: MONO, fontSize: 9.5, color: C.sub }}>{s.steps}</span>
                  <span onClick={e => { e.stopPropagation(); remove(id) }} title={`${s.name} herausnehmen`}
                    style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 20, height: 18, border: `1px solid ${C.line}`, cursor: 'pointer' }}>
                    <Minus size={10} strokeWidth={2} color={C.ink} />
                  </span>
                </div>
              )
            })}
            {open.length === 0 && <div style={{ padding: 9, fontFamily: MONO, fontSize: 10, color: C.faint }}>kein Subagent offen</div>}
          </div>
          {closed.length > 0 && (
            <div style={{ padding: '6px 8px 8px', borderTop: `1px solid ${C.hair}` }}>
              <div style={{ fontFamily: MONO, fontSize: 9, color: C.faint, marginBottom: 5 }}>HERAUSGENOMMEN</div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5 }}>
                {closed.map(s => (
                  <span key={s.id} onClick={() => { setOpen(o => [...o, s.id]); setActive(s.id) }} title={`${s.name} wieder öffnen`}
                    style={{ display: 'inline-flex', alignItems: 'center', gap: 4, padding: '3px 7px', border: `1px solid ${C.line}`, fontFamily: MONO, fontSize: 10, cursor: 'pointer', color: C.sub }}>
                    <Plus size={9} strokeWidth={2} color={C.sub} />{s.name}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

export default { '01 · Subagenten-SubagentPanel (gewählt): nur offene Zeilen, − nimmt heraus': () => <SubagentPanel /> }
