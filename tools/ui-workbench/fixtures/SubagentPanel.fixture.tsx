/**
 * Subagenten-Panel — gewaehlte Fassung (nur offene Zeilen), mit Darkmode.
 *
 * Jeder offene Subagent ist ausgewaehlt (der geoeffnete ist hervorgehoben). "−" nimmt einen
 * heraus — kein Abschnitt "herausgenommen" mehr. Streifen ueber der Chatbar bleibt.
 *
 * Alles Bewegliche laeuft ueber Motion: Zeilen fahren beim Herausnehmen ein und die
 * uebrigen rutschen nach (layout), die Auswahl blendet weich um, der Punkt arbeitender
 * Subagenten atmet, der Cursor blinkt.
 *
 * Befunde 18.09.2026: Paletten und Groessen kommen aus _tokens (keine eigenen Hexwerte),
 * useBarrierefrei() schaltet reduzierte Bewegung ab und macht die Zeigerziele per
 * Enter/Leertaste bedienbar, data-stage sitzt am aeusseren Rahmen jeder Variante,
 * Schriftuntergrenze 11 px, offene Subagenten haengen an ihrer ID statt am Index.
 */
import { motion, AnimatePresence, animate } from 'motion/react'
import React, { useState } from 'react'
import { IconMinus } from '@tabler/icons-react'
import { LIGHT, DARK, MONO, SANS, SPRING, RADIUS, TEXT } from './_tokens'
import { useBarrierefrei } from './_barrierefrei'

// @ts-expect-error — Laufzeit-Alias (siehe vite.config.ts)
import { ThinkingOrb } from 'thinking-orbs'

const SUBS = [
  { id: 's1', name: 'Pruefer', task: 'Anker und Bilder gegenmessen', state: 'laeuft', pct: 62, steps: '4/7' },
  { id: 's2', name: 'Sammler', task: 'Messwerte und Screenshots sammeln', state: 'laeuft', pct: 38, steps: '3/8' },
  { id: 's3', name: 'Schreiber', task: 'Bericht aus den Nachweisen bauen', state: 'wartet', pct: 0, steps: '0/4' },
  { id: 's4', name: 'Waechter', task: 'Tore pruefen: Lint, Tests, Bau', state: 'fertig', pct: 100, steps: '3/3' }
]

const Orb = ({ state, theme }) => <ThinkingOrb state={state === 'laeuft' ? 'working' : state === 'wartet' ? 'breathing' : 'composing'} size={20} theme={theme} />

function Panel({ dark }) {
  useBarrierefrei()
  const P = dark ? DARK : LIGHT
  const theme = dark ? 'dark' : 'light'
  const [open, setOpen] = useState(['s1', 's2', 's3'])
  const [active, setActive] = useState('s1')
  const remove = (id) => {
    const rest = open.filter(x => x !== id)
    setOpen(rest)
    if (active === id) setActive(rest[0] || '')
  }
  return (
    <div data-stage style={{ padding: 20, background: P.bg, fontFamily: SANS, color: P.ink, width: '100%', maxWidth: 880, boxSizing: 'border-box' }}>
      <div style={{ fontFamily: MONO, fontSize: 12, fontWeight: 600 }}>{dark ? 'Subagenten-Panel — DARKMODE (identisch)' : 'Subagenten-Panel — gewaehlte Fassung (nur offene Zeilen)'}</div>
      <div style={{ fontFamily: MONO, fontSize: 11, color: P.sub, margin: '3px 0 10px' }}>
        jeder offene Subagent ist ausgewaehlt · IconMinus nimmt ihn heraus (Motion) · kein Abschnitt herausgenommen
      </div>
      <div style={{ position: 'relative', height: 430, border: `1px solid ${P.line}`, background: P.bg, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        <div style={{ flex: 1, padding: 14, display: 'flex', flexDirection: 'column', gap: 8 }}>
          <motion.span initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={SPRING}
            style={{ alignSelf: 'flex-end', fontFamily: MONO, fontSize: 11.5, background: P.ink, color: P.bg, padding: '5px 9px' }}>&gt; zerleg das in drei spuren</motion.span>
          <motion.span initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ ...SPRING, delay: 0.08 }}
            style={{ alignSelf: 'flex-start', maxWidth: 420, fontFamily: MONO, fontSize: 11.5, background: P.surface, border: `1px solid ${P.line}`, padding: '6px 9px' }}>drei subagenten gerufen; jeder in seinem fenster.</motion.span>
        </div>
        <div style={{ display: 'flex', gap: 6, padding: '0 12px 8px', flexWrap: 'wrap' }}>
          {SUBS.map(s => {
            const offen = open.indexOf(s.id) >= 0
            return (
              <motion.span key={s.id} layout onClick={() => offen && setActive(s.id)} title={offen ? `${s.name} zeigen` : `${s.name} ist geschlossen`}
                whileHover={{ scale: 1.03 }} whileTap={{ scale: 0.97 }} transition={SPRING}
                animate={{ opacity: offen ? 1 : 0.45, borderColor: active === s.id && offen ? P.ink : P.line }}
                style={{ display: 'inline-flex', alignItems: 'center', gap: 6, padding: '4px 8px', border: `1px solid ${P.line}`, background: P.surface, fontFamily: MONO, fontSize: 11 }}>
                {s.state === 'laeuft' && offen ? (
                  <motion.span animate={{ opacity: [1, 0.25, 1], scale: [1, 0.82, 1] }} transition={{ duration: 1.6, repeat: Infinity, ease: 'easeInOut' }}
                    style={{ width: 6, height: 6, borderRadius: RADIUS.pill, background: P.ink }} />
                ) : (
                  <span style={{ width: 6, height: 6, borderRadius: RADIUS.pill, background: s.state === 'fertig' ? P.faint : P.ink, opacity: offen ? 1 : 0.45 }} />
                )}
                {s.name}
                <span style={{ color: P.sub }}>{s.state === 'fertig' ? '✓' : `${s.pct}%`}</span>
              </motion.span>
            )
          })}
        </div>
        <div style={{ padding: '0 12px 12px' }}>
          <div style={{ border: `1px solid ${P.line}`, background: P.surface, padding: '6px 10px', fontFamily: MONO, fontSize: 12, color: P.faint }}>
            nachricht an skaldir <motion.span animate={{ opacity: [1, 0.15, 1] }} transition={{ duration: 1.05, repeat: Infinity, ease: 'easeInOut' }}>▍</motion.span>
          </div>
        </div>

        <motion.div layout initial={{ opacity: 0, scale: 0.97 }} animate={{ opacity: 1, scale: 1 }} transition={SPRING}
          style={{ position: 'absolute', left: 12, top: 12, width: 236, background: P.surface, border: `1px solid ${P.line}`, boxShadow: '0 8px 26px rgba(0,0,0,0.20)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '7px 9px', borderBottom: `1px solid ${P.line}` }}>
            <span style={{ fontFamily: MONO, fontSize: 11, letterSpacing: 1, color: P.sub }}>SUBAGENTEN · {open.length} OFFEN</span>
          </div>
          <div style={{ padding: 4 }}>
            <AnimatePresence initial={false}>
              {open.map(id => {
                const s = SUBS.find(x => x.id === id)
                const sel = active === id
                return (
                  <motion.div key={id} layout data-panelrow={id} data-selected={sel ? 'true' : 'false'}
                    initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: 'auto' }} exit={{ opacity: 0, height: 0 }}
                    transition={SPRING} onClick={() => setActive(id)}
                    style={{ overflow: 'hidden' }}>
                    <motion.div animate={{ backgroundColor: sel ? P.bg : 'transparent', borderColor: sel ? P.line : 'transparent' }} transition={{ duration: 0.16 }}
                      style={{ display: 'flex', alignItems: 'center', gap: 7, padding: '6px 7px', cursor: 'pointer', border: '1px solid transparent' }}>
                      <Orb state={s.state} theme={theme} />
                      <span style={{ flex: 1, minWidth: 0 }}>
                        <span style={{ display: 'block', fontFamily: MONO, fontSize: 11, fontWeight: sel ? 600 : 400, color: P.ink }}>{s.name}</span>
                        <span style={{ display: 'block', fontFamily: MONO, fontSize: 11, color: P.sub, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.task}</span>
                      </span>
                      <span style={{ fontFamily: MONO, fontSize: 11, color: P.sub }}>{s.steps}</span>
                      <motion.span onClick={e => { e.stopPropagation(); remove(id) }} title={`${s.name} herausnehmen`}
                        whileHover={{ scale: 1.12 }} whileTap={{ scale: 0.88 }} transition={SPRING}
                        style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 20, height: 18, border: `1px solid ${P.line}`, cursor: 'pointer' }}>
                        <IconMinus size={10} stroke={2} color={P.ink} />
                      </motion.span>
                    </motion.div>
                  </motion.div>
                )
              })}
            </AnimatePresence>
            {open.length === 0 && <div style={{ padding: 9, fontFamily: MONO, fontSize: 11, color: P.faint }}>kein Subagent offen</div>}
          </div>
        </motion.div>
      </div>
    </div>
  )
}

export default {
  '01 · Subagenten-Panel (gewaehlt): nur offene Zeilen, Minus nimmt heraus': () => <Panel dark={false} />,
  '02 · Subagenten-Panel DARKMODE (identisch)': () => <Panel dark />
}
