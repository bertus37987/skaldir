/**
 * Subagenten-Fenster — gewaehlte Fassung (schmale Spur), mit Darkmode.
 *
 * 234 px schmal, am Kopf greifen und frei verschieben (Motion drag ohne Nachlauf),
 * Minus klappt auf eine duenne Kopfleiste (Feder), ✕ schliesst (Fenster faehrt ein,
 * Griff bleibt). Kopf traegt die Dauer, unten Beenden-Viereck und viereckige
 * CLI-Kontextanzeige nebeneinander. Kein Lenk-Feld. Hintergrund weichgezeichnet.
 *
 * Befunde 18.09.2026: Paletten und Groessen kommen aus _tokens (keine eigenen Hexwerte),
 * useBarrierefrei() schaltet reduzierte Bewegung ab und macht die Zeigerziele per
 * Enter/Leertaste bedienbar, data-stage sitzt am aeusseren Rahmen jeder Variante,
 * Schriftuntergrenze 11 px, Sitzungszeilen haengen an ihrem Text statt am Index.
 */
import { motion, AnimatePresence, animate } from 'motion/react'
import React, { useState } from 'react'
import { IconMinus, IconX } from '@tabler/icons-react'
import { LIGHT, DARK, MONO, SANS, SPRING, RADIUS, TEXT } from './_tokens'
import { useBarrierefrei } from './_barrierefrei'

// @ts-expect-error — Laufzeit-Alias (siehe vite.config.ts)
import { ThinkingOrb } from 'thinking-orbs'

const LINES = ['oeffne tools/check_ui_overlay_anchors.py', 'lese 28 Anker-Erwartungen', 'messe #composer bei 1280x800', 'vergleiche: 27 von 28 stimmen']

const CliCtx = ({ P, dim }) => (
  <span style={{ border: `1px solid ${P.ink}`, padding: '2px 5px', display: 'inline-flex', alignItems: 'center', gap: 6, fontFamily: MONO, fontSize: 11, color: P.ink, opacity: dim ? 0.45 : 1 }}>
    {/* statisch: Indexschluessel ok */}
    {Array.from({ length: 10 }).map((_, i) => (
      <motion.span key={i} initial={{ opacity: 0, y: 3 }} animate={{ opacity: 1, y: 0 }} transition={{ ...SPRING, delay: 0.15 + i * 0.02 }}
        style={{ width: 5, height: 9, background: i < 6 ? P.ink : 'transparent', border: `1px solid ${P.ink}` }} />
    ))}
    <span>62%</span>
  </span>
)

function Win({ P, theme }) {
  const [collapsed, setCollapsed] = useState(false)
  const [closed, setClosed] = useState(false)
  const [stopped, setStopped] = useState(false)
  const controls = useDragControls()

  return (
    <AnimatePresence initial={false} mode="wait">
      {closed ? (
        <motion.span key="griff" data-subhandle drag dragMomentum={false} dragElastic={0.02} dragListener={false} dragControls={controls}
          initial={{ opacity: 0, scale: 0.92 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0, scale: 0.92 }} transition={SPRING}
          onPointerDown={e => controls.start(e)}
          onClick={() => setClosed(false)} title="Fenster oeffnen"
          style={{ position: 'absolute', left: 40, top: 30, fontFamily: MONO, fontSize: 11, padding: '5px 9px', border: `1px solid ${P.line}`, background: P.surface, color: P.ink, boxShadow: '0 10px 28px rgba(0,0,0,0.22)', cursor: 'grab', touchAction: 'none' }}>◆ pruefer t 00:42 ▸</motion.span>
      ) : (
        <motion.div key="fenster" data-subwin layout drag dragMomentum={false} dragElastic={0.02} dragListener={false} dragControls={controls}
          initial={{ opacity: 0, scale: 0.94, y: 6 }} animate={{ opacity: 1, scale: 1, y: 0 }} exit={{ opacity: 0, scale: 0.94 }}
          transition={SPRING}
          style={{ position: 'absolute', left: 40, top: 30, width: 234, background: P.surface, border: `1px solid ${P.line}`, boxShadow: '0 14px 38px rgba(0,0,0,0.30)', fontFamily: MONO, touchAction: 'none' }}>
          <motion.div layout onPointerDown={e => controls.start(e)}
            style={{ display: 'flex', alignItems: 'center', gap: 7, padding: '5px 7px', cursor: 'grab', background: P.bg, borderBottom: collapsed ? 'none' : `1px solid ${P.line}`, touchAction: 'none' }}>
            <ThinkingOrb state={stopped ? 'breathing' : 'working'} size={20} theme={theme} />
            <span style={{ fontSize: 11, color: P.sub }}>◆ pruefer</span>
            <span style={{ fontSize: 11, color: P.ink }}>t 00:42</span>
            {collapsed && <motion.span initial={{ opacity: 0, x: -4 }} animate={{ opacity: 1, x: 0 }} transition={SPRING} style={{ fontSize: 11, color: P.faint }}>▮▮▮▮▮▮▯▯▯▯ 62%</motion.span>}
            <span style={{ marginLeft: collapsed ? 6 : 'auto', display: 'inline-flex', gap: 4 }}>
              <motion.span onClick={() => setCollapsed(c => !c)} title={collapsed ? 'ausklappen' : 'einklappen'} whileHover={{ scale: 1.1 }} whileTap={{ scale: 0.9 }} transition={SPRING}
                style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 20, height: 18, border: `1px solid ${P.line}`, cursor: 'pointer', fontSize: 11, color: P.ink }}>−</motion.span>
              <motion.span onClick={() => setClosed(true)} title="schliessen" whileHover={{ scale: 1.1 }} whileTap={{ scale: 0.9 }} transition={SPRING}
                style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 20, height: 18, border: `1px solid ${P.line}`, cursor: 'pointer' }}><IconX size={9} stroke={2} color={P.ink} /></motion.span>
            </span>
          </motion.div>
          <AnimatePresence initial={false}>
            {!collapsed && (
              <motion.div key="rumpf" initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }} transition={SPRING} style={{ overflow: 'hidden' }}>
                <div style={{ padding: 7, height: 104, overflow: 'hidden', background: P.surface }}>
                  {LINES.map((l, k) => (
                    <motion.div key={l} initial={{ opacity: 0, x: -3 }} animate={{ opacity: stopped ? 0.55 : 1, x: 0 }} transition={{ ...SPRING, delay: 0.05 + k * 0.06 }}
                      style={{ fontSize: 11, color: P.ink, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                      › {l}{k === LINES.length - 1 && !stopped ? (
                        <motion.span animate={{ opacity: [1, 0.15, 1] }} transition={{ duration: 1.05, repeat: Infinity, ease: 'easeInOut' }} style={{ color: P.sub }}> ▍</motion.span>
                      ) : null}
                    </motion.div>
                  ))}
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 7, padding: '6px 7px', borderTop: `1px solid ${P.line}` }}>
                  <motion.span onClick={() => setStopped(v => !v)} title={stopped ? 'Weiter' : 'Beenden'} whileHover={{ scale: 1.06 }} whileTap={{ scale: 0.92 }} transition={SPRING}
                    style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 24, height: 20, background: stopped ? 'transparent' : P.ink, border: `1px solid ${P.ink}`, color: stopped ? P.ink : P.bg, fontSize: 11, cursor: 'pointer' }}>{stopped ? '▶' : '■'}</motion.span>
                  <CliCtx P={P} dim={stopped} />
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </motion.div>
      )}
    </AnimatePresence>
  )
}

const Case = ({ dark }) => {
  useBarrierefrei()
  const P = dark ? DARK : LIGHT
  const theme = dark ? 'dark' : 'light'
  return (
    <div data-stage style={{ padding: 20, background: P.bg, fontFamily: SANS, color: P.ink, width: '100%', maxWidth: 880, boxSizing: 'border-box' }}>
      <div style={{ fontFamily: MONO, fontSize: 12, fontWeight: 600 }}>{dark ? 'Subagenten-Fenster — DARKMODE (identisch)' : 'Subagenten-Fenster — gewaehlte Fassung (schmale Spur)'}</div>
      <div style={{ fontFamily: MONO, fontSize: 11, color: P.sub, margin: '3px 0 10px' }}>
        234 px · Kopf greifen zum Verschieben (Motion drag) · IconMinus klappt mit Feder ein · ✕ schliesst · Hintergrund weichgezeichnet
      </div>
      <div style={{ position: 'relative', height: 440, border: `1px solid ${P.line}`, background: P.bg, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        <div style={{ flex: 1 }} />
        <div style={{ position: 'absolute', inset: 0, backdropFilter: 'blur(6px)', WebkitBackdropFilter: 'blur(6px)', background: P.veil }} />
        <div style={{ position: 'absolute', inset: 0 }}><Win P={P} theme={theme} /></div>
      </div>
    </div>
  )
}

export default {
  '01 · Subagenten-Fenster (gewaehlt): schmale Spur': () => <Case dark={false} />,
  '02 · Subagenten-Fenster DARKMODE (identisch)': () => <Case dark />
}
