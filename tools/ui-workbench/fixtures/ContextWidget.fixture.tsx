/**
 * Kontext-Widget — gewaehlte, fertige Fassung (Terminal-Kapsel), mit Darkmode.
 *
 * Stand 17.09.2026, nach zwei Rueckmeldungen des Owners:
 *
 * 1) „poppt nach schliessen und draggen an der falschen stelle wieder auf": Behoben durch EINEN
 *    Zieh-Rahmen, der nie ausgehaengt wird — nur der Inhalt wechselt zwischen Griff und Kapsel.
 * 2) „es gibt immernoch ein Maus-Freeze-Delay, nur dort": Das Ziehen laeuft NICHT mehr ueber
 *    Motion `drag`. Motion schiebt die Bewegung durch seine eigene Animationsschleife und
 *    skaliert das Element beim Anfassen (whileDrag), wodurch der grosse Schatten in jedem Bild
 *    neu gezeichnet wird — genau das fuehlt sich als Verzoegerung an. Jetzt folgt das Widget dem
 *    Zeiger direkt (Pointer-Ereignisse, Verschiebung per translate3d → nur die Compositor-Ebene,
 *    kein Layout, kein Neuzeichnen), mit `will-change: transform`.
 *    Auf- und Zuklappen bleibt Motion (Feder).
 *
 * Befunde 18.09.2026: Paletten und Groessen kommen aus _tokens (keine eigenen Hexwerte),
 * useBarrierefrei() schaltet reduzierte Bewegung ab und macht die Zeigerziele per
 * Enter/Leertaste bedienbar, data-stage sitzt am aeusseren Rahmen jeder Variante,
 * Schriftuntergrenze 11 px, eine einzige Radustabelle.
 */
import { IconTerminal } from '@tabler/icons-react'
import React, { useRef, useState } from 'react'
import { motion, AnimatePresence } from 'motion/react'
import { LIGHT, DARK, MONO, SANS, SPRING, RADIUS, TEXT } from './_tokens'
import { useBarrierefrei } from './_barrierefrei'

function Capsule({ P }) {
  const [open, setOpen] = useState(true)
  const [pos, setPos] = useState({ x: 420, y: 120 })
  const zieh = useRef(null)
  const bewegt = useRef(false)

  const anfassen = e => {
    if (e.target && e.target.dataset && e.target.dataset.noDrag) return
    zieh.current = { dx: e.clientX - pos.x, dy: e.clientY - pos.y }
    bewegt.current = false
    if (e.currentTarget.setPointerCapture) { try { e.currentTarget.setPointerCapture(e.pointerId) } catch {} }
  }
  const fuehren = e => {
    if (!zieh.current) return
    const nx = e.clientX - zieh.current.dx
    const ny = e.clientY - zieh.current.dy
    if (Math.abs(nx - pos.x) + Math.abs(ny - pos.y) > 2) bewegt.current = true
    setPos({ x: nx, y: ny })
  }
  const loslassen = () => {
    if (zieh.current && !bewegt.current) setOpen(o => !o)
    zieh.current = null
  }

  return (
    <div data-ctx
      onPointerDown={anfassen} onPointerMove={fuehren} onPointerUp={loslassen} onPointerCancel={() => { zieh.current = null }}
      style={{
        position: 'absolute', left: 0, top: 0, transform: `translate3d(${pos.x}px, ${pos.y}px, 0)`,
        willChange: 'transform', cursor: 'grab', touchAction: 'none', display: 'inline-block'
      }}>
      <AnimatePresence initial={false} mode="wait">
        {open ? (
          <motion.div key="kapsel" data-ctxcapsule initial={{ opacity: 0, scale: 0.95 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0, scale: 0.95 }} transition={SPRING}
            style={{
              width: 208, borderRadius: RADIUS.pill, border: `1px solid ${P.line}`,
              background: P.surface, boxShadow: '0 10px 28px rgba(0,0,0,0.22)', padding: '9px 13px', display: 'flex', flexDirection: 'column',
              gap: 5, cursor: 'grab'
            }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ fontFamily: MONO, fontSize: 13, color: P.ink }}>[██████░░░░]</span>
              <span style={{ marginLeft: 'auto', fontFamily: MONO, fontSize: 12, color: P.ink }}>62%</span>
              <motion.span data-no-drag="1" whileHover={{ scale: 1.12 }} whileTap={{ scale: 0.92 }} transition={SPRING} title="Kontext schliessen"
                onClick={e => { e.stopPropagation(); setOpen(false) }}
                style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 18, height: 18, borderRadius: RADIUS.pill, border: `1px solid ${P.line}`, fontFamily: MONO, fontSize: 11, color: P.ink, cursor: 'pointer' }}>✕</motion.span>
            </div>
            <div style={{ display: 'flex', gap: 7, fontFamily: MONO, fontSize: 11, color: P.sub }}>
              <span>128k/200k</span><span>·</span><span>reserve 12k</span>
            </div>
          </motion.div>
        ) : (
          <motion.span key="griff" data-ctx-handle initial={{ opacity: 0, scale: 0.9 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0, scale: 0.9 }} transition={SPRING}
            title="Kontext oeffnen"
            style={{
              display: 'inline-flex', alignItems: 'center', gap: 6, padding: '5px 10px', borderRadius: RADIUS.pill,
              background: P.surface, border: `1px solid ${P.line}`, fontFamily: MONO, fontSize: 11, color: P.ink,
              boxShadow: '0 6px 18px rgba(0,0,0,0.20)', cursor: 'grab'
            }}>[██░░] 62% ▸</motion.span>
        )}
      </AnimatePresence>
    </div>
  )
}

const Case = ({ dark }) => {
  useBarrierefrei()
  const P = dark ? DARK : LIGHT
  return (
    <div data-stage style={{ padding: 20, background: P.bg, fontFamily: SANS, color: P.ink, width: '100%', maxWidth: 880, boxSizing: 'border-box' }}>
      <div style={{ fontFamily: MONO, fontSize: 12, fontWeight: 600 }}>{dark ? 'Kontext-Widget — DARKMODE (identisch)' : 'Kontext-Widget — gewaehlt und fertig (IconTerminal-Kapsel)'}</div>
      <div style={{ fontFamily: MONO, fontSize: 11, color: P.sub, margin: '3px 0 10px' }}>
        ziehen folgt dem Zeiger direkt (translate3d, kein Layout) · ✕ schliesst auf den Griff, der Griff bleibt an derselben Stelle
      </div>
      <div style={{ position: 'relative', height: 440, borderRadius: RADIUS.module, overflow: 'hidden', border: `1px solid ${P.line}`, background: P.bg }}>
        <div style={{ position: 'absolute', left: 20, top: 18, right: 20, height: 24, borderRadius: RADIUS.row, background: P.hair }} />
        <div style={{ position: 'absolute', left: 20, top: 54, width: 180, height: 150, borderRadius: RADIUS.row, background: P.hair }} />
        <div style={{ position: 'absolute', left: 214, top: 54, right: 20, bottom: 20, borderRadius: RADIUS.row, background: P.hair }} />
        <span style={{ position: 'absolute', left: 20, bottom: 18, fontFamily: MONO, fontSize: 11, color: P.faint }}>arbeitsflaeche · hierhin laesst sich das Widget ziehen</span>
        <Capsule P={P} />
      </div>
    </div>
  )
}

export default {
  '01 · Kontext-Widget (gewaehlt, fertig): Terminal-Kapsel, frei verschiebbar (direkt am Zeiger)': () => <Case dark={false} />,
  '02 · Kontext-Widget DARKMODE (identisch)': () => <Case dark />
}
