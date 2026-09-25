/** App-taugliche Chat-Bar — abgeleitet aus dem abgenommenen Fixture
 *  designs/GEWAEHLT/code/chatbar.tsx (abgenommen am 17.09.2026), uebernommen als
 *  src/skaldir/chatbar.tsx. Optik und Masse der Pille sind unveraendert.
 *
 *  Gegenueber der Fixture entfernt (Fixture-Zutaten, im leeren Mockup falsch):
 *   - die Buehnen-/Case-Huelle (padding:20, Titelzeile "Chat-Bar (gewaehlt) — …", Notiztext, 420px-Rahmen)
 *   - der fest eingebaute Beispiel-Plan (GOAL-Baum "skaldir ui-abnahme" samt Zaehler und "momentan"-Zeile)
 *   - die fest eingebaute Beispiel-Unterhaltung und der Upload-/Datei-Block im leeren Zustand
 *
 *  Neu: Ziel (titel + kriterien) und Dateien kommen als Props, Eingabe ist ein echtes
 *  <input> (Enter sendet und leert), alle Bedienelemente sind echte <button>.
 *  Symbole aus @tabler/icons-react. Kein Orb im Composer — ein Orb gehoert an einen laufenden Vorgang.
 */
import React, { useState } from 'react'
import { IconArrowBarDown, IconArrowBarUp, IconChevronDown, IconFile } from '@tabler/icons-react'

// Farben ausschliesslich ueber die Tokens (vorher eigene Palette #F7F7F5/#E5E5E3/#EFEFED —
// die brach im Dunkelmodus und widersprach dem UI-Vertrag).
const P = { bg: 'var(--background)', pill: 'var(--card)', line: 'var(--border)', hair: 'var(--hair)',
            grey: 'var(--faint)', mid: 'var(--muted-foreground)', ink: 'var(--foreground)' }
const MONO = 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace'
const SANS = "'Geist Sans', system-ui, sans-serif"

const MODELS = [
  { id: 'm1', name: 'opencode', sub: 'free' },
  { id: 'm2', name: 'klarer', sub: 'local' },
  { id: 'm3', name: 'waehler', sub: 'remote' }
]
const LEVELS = ['Low', 'Med', 'High']

const PLATZHALTER = 'Start with a goal'

export type Ziel = { titel: string; kriterien: string[] }
export type DateiEintrag = { pfad: string; endung: string; groesse: string }

/** Knopf-Grundlage: sieht aus wie im Fixture (kein eigener Rahmen, kein eigener Hintergrund). */
const BTN: React.CSSProperties = {
  background: 'none', border: 0, padding: 0, margin: 0, font: 'inherit', color: 'inherit',
  textAlign: 'left', cursor: 'pointer', display: 'inline-flex', alignItems: 'center'
}

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
  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 12.5, color: P.grey }}>Med<IconChevronDown size={11} stroke={2} color={P.grey} /></span>
)

/** Model & Reasoning Selector — die einzige Modell-/Denk-Bedienung in der Leiste. */
function Selector() {
  const [open, setOpen] = useState(false)
  const [model, setModel] = useState(MODELS[0])
  const [level, setLevel] = useState('Med')
  return (
    <span style={{ position: 'relative', display: 'inline-flex', alignItems: 'center', gap: 4 }}>
      <button type="button" data-selector onClick={() => setOpen(o => !o)} title="Modell und Reasoning waehlen"
        aria-label="Modell und Reasoning waehlen" aria-haspopup="true" aria-expanded={open}
        style={{ display: 'inline-flex', alignItems: 'center', gap: 4, cursor: 'pointer', background: 'none', border: 0, padding: 0, margin: 0, font: 'inherit' }}>
        <Wave />
        <IconChevronDown size={12} stroke={2.2} color={P.ink} />
      </button>
      {open && (
        <div data-selectorpopover style={{
          position: 'absolute', right: 0, bottom: 42, width: 226, padding: 10, borderRadius: 14,
          background: P.pill, border: `1px solid ${P.line}`, boxShadow: '0 14px 34px rgba(0,0,0,0.16)', zIndex: 5
        }}>
          <div style={{ fontFamily: MONO, fontSize: 11, letterSpacing: 1, color: P.grey, marginBottom: 6 }}>MODELL</div>
          {MODELS.map(m => (
            <button key={m.id} type="button" onClick={() => { setModel(m); setOpen(false) }} data-model={m.id}
              aria-pressed={model.id === m.id}
              style={{ ...BTN, width: '100%', gap: 8, padding: '5px 6px', background: model.id === m.id ? P.bg : 'transparent' }}>
              <span style={{ width: 6, height: 6, borderRadius: 999, background: P.ink, opacity: model.id === m.id ? 1 : 0.25 }} />
              <span style={{ fontSize: 12.5, color: P.ink }}>{m.name}</span>
              <span style={{ marginLeft: 'auto', fontFamily: MONO, fontSize: 11, color: P.grey }}>{m.sub}</span>
            </button>
          ))}
          <div style={{ fontFamily: MONO, fontSize: 11, letterSpacing: 1, color: P.grey, margin: '9px 0 6px' }}>REASONING</div>
          <div style={{ display: 'flex', gap: 6 }}>
            {LEVELS.map(l => (
              <button key={l} type="button" onClick={() => { setLevel(l); setOpen(false) }} data-level={l}
                aria-pressed={l === level}
                style={{ ...BTN, flex: 1, justifyContent: 'center', textAlign: 'center', padding: '4px 0', border: `1px solid ${l === level ? P.ink : P.line}`, fontSize: 11.5, color: P.ink }}>{l}</button>
            ))}
          </div>
          <div style={{ marginTop: 9, fontFamily: MONO, fontSize: 11, color: P.grey }}>
            gilt fuer die naechste Nachricht · {model.name} · {level}
          </div>
        </div>
      )}
    </span>
  )
}

/** Goal-Bar: Kopfzeile mit Ein-/Ausklappen + Titel, darunter die Kriterien mit Haekchen rechts vom Wort. */
function GoalBar({ titel, kriterien, open, onToggle }: { titel: string; kriterien: string[]; open: boolean; onToggle: () => void }) {
  return (
    <div style={{ background: P.pill, borderBottom: `1px solid ${P.line}` }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 10px' }}>
        <button type="button" onClick={onToggle} title={open ? 'Ziel einklappen' : 'Ziel ausklappen'}
          aria-label={open ? 'Ziel einklappen' : 'Ziel ausklappen'} aria-expanded={open}
          style={{ ...BTN, display: 'inline-flex' }}>
          {open ? <IconArrowBarUp size={14} stroke={1.8} color={P.ink} /> : <IconArrowBarDown size={14} stroke={1.8} color={P.ink} />}
        </button>
        <span style={{ fontFamily: SANS, fontSize: 12, fontWeight: 600, color: P.ink }}>{titel}</span>
        <span style={{ fontFamily: MONO, fontSize: 11, color: P.mid }}>{kriterien.length} {kriterien.length === 1 ? 'Kriterium' : 'Kriterien'}</span>
        <span style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          <span style={{ fontFamily: MONO, fontSize: 11, color: P.grey }}>Enter sendet</span>
        </span>
      </div>
      {open && (
        <div style={{ padding: '0 10px 7px' }}>
          {kriterien.map((k, i) => (
            <div key={`${i}-${k}`} style={{ display: 'flex', alignItems: 'center', minHeight: 24, whiteSpace: 'nowrap' }}>
              <span style={{ fontFamily: MONO, fontSize: 11.5, whiteSpace: 'pre', color: P.ink }}>{i === kriterien.length - 1 ? '└─ ' : '├─ '}</span>
              <span style={{ fontFamily: SANS, fontSize: 12, fontWeight: 400, color: P.ink }}>{k}</span>
              <span style={{ marginLeft: 7, width: 12, height: 12, border: `1.3px solid ${P.ink}`, background: 'transparent', flex: '0 0 auto' }} />
            </div>
          ))}
          <div style={{ marginTop: 3, fontFamily: MONO, fontSize: 11, color: P.grey }}>└─ Kriterium ergaenzen</div>
        </div>
      )}
    </div>
  )
}

/** Dateizeile — nur wenn dateien gesetzt und nicht leer; im leeren Zustand gibt es sie nicht. */
function Dateizeile({ dateien }: { dateien: DateiEintrag[] }) {
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, padding: '0 6px 8px' }}>
      {dateien.map(d => (
        <span key={d.pfad} style={{
          display: 'inline-flex', alignItems: 'center', gap: 6, fontFamily: SANS, fontSize: 12, color: P.ink,
          background: P.bg, border: `1px solid ${P.line}`, padding: '4px 8px', borderRadius: 12
        }}>
          <IconFile size={12} stroke={1.8} color={P.grey} />
          <span>{d.pfad}</span>
          <span style={{ fontFamily: MONO, fontSize: 11, color: P.grey }}>
            {d.endung}{d.endung && d.groesse ? ' · ' : ''}{d.groesse}
          </span>
        </span>
      ))}
    </div>
  )
}

export function ChatBarApp({ ziel, dateien, platzhalter, onSenden, onDatei }: {
  ziel?: Ziel | null
  dateien?: DateiEintrag[]
  platzhalter?: string
  onSenden?: (text: string) => void
  onDatei?: () => void
}) {
  const [goalOpen, setGoalOpen] = useState(false)   // Goal-Bar startet eingeklappt
  const [text, setText] = useState('')
  const kriterien = ziel?.kriterien ?? []
  const dateiListe = dateien ?? []

  const senden = () => {
    const t = text.trim()
    if (!t) return
    onSenden?.(t)
    setText('')
  }

  return (
    <div style={{ width: '100%', boxSizing: 'border-box', fontFamily: SANS, background: P.bg, padding: 14, display: 'flex', justifyContent: 'center' }}>
      <style>{`.cba-eingabe::placeholder{color:${P.grey};opacity:1}`}</style>
      <div style={{ width: '100%', borderRadius: 24, background: P.pill, border: `1px solid ${P.line}` }}>
        {ziel && (
          <GoalBar titel={ziel.titel} kriterien={kriterien} open={goalOpen} onToggle={() => setGoalOpen(o => !o)} />
        )}
        <div style={{ padding: 10 }}>
          {dateiListe.length > 0 && <Dateizeile dateien={dateiListe} />}
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, height: 58, padding: '0 12px 0 16px', borderRadius: 999, background: P.pill, border: `1px solid ${P.line}` }}>
            <button type="button" onClick={() => onDatei?.()} title="Datei anhaengen" aria-label="Datei anhaengen"
              style={{ ...BTN, display: 'inline-flex' }}>
              <Plus />
            </button>
            <input
              className="cba-eingabe"
              value={text}
              onChange={e => setText(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); senden() } }}
              placeholder={platzhalter ?? PLATZHALTER}
              aria-label={platzhalter ?? PLATZHALTER}
              style={{
                flex: 1, minWidth: 0, border: 0, outline: 'none', background: 'transparent',
                fontFamily: SANS, fontSize: 14.5, color: P.ink, padding: 0
              }}
            />
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 14 }}>
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}><DotGrid /><IconChevronDown size={11} stroke={2} color={P.grey} /></span>
              <Med />
              <Selector />
            </span>
          </div>
        </div>
      </div>
    </div>
  )
}

export default ChatBarApp
