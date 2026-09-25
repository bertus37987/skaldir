/**
 * Orb-Widgets — die abgenommene erste Fassung, hell und dunkel.
 *
 * Owner 17.09.2026: „die 1. Suche/Reasoning und co Widgets sind super, ich möchte die genauso,
 * aber auch Darkmode — alle in light und dark, den Rest löschen."
 *
 * Also je Thema genau eine Fassung (Kompaktzeile + Liste) in hell und in dunkel, sonst nichts.
 * Neu ist nur die Bewegung: Auf- und Zuklappen laufen jetzt über Motion (motion/react) —
 * Höhe als Feder, Inhalt mit Einblendung, Pfeil dreht mit. Optik unverändert.
 */
import { motion, AnimatePresence, animate } from 'motion/react'
import React, { useEffect, useState } from 'react'
import { IconChevronRight } from '@tabler/icons-react'

// @ts-expect-error — Laufzeit-Alias (siehe vite.config.ts)
import { ThinkingOrb } from 'thinking-orbs'
import { useBarrierefrei } from './_barrierefrei'
import { LIGHT, DARK, MONO, SANS, SPRING, RADIUS, TEXT } from './_tokens'

/** Echte, sekündlich laufende Zeit. */
const useLive = (start) => {
  const [t, setT] = useState(start)
  useEffect(() => {
    const id = setInterval(() => setT(v => v + 1), 1000)
    return () => clearInterval(id)
  }, [])
  return t
}
const fmt = (s) => String(Math.floor(s / 60)).padStart(2, '0') + ':' + String(s % 60).padStart(2, '0')

const TOPICS = {
  suche: {
    state: 'searching', label: 'Suche', start: 7, tokIn: '1,24k', tokOut: '0,38k',
    rows: [['skaldir ui contract anker', '28 Treffer'], ['thinking-orbs zustaende', '9 Zustaende'], ['react cosmos darkmode', 'HMR-Hinweis']],
    links: [['datei', 'Anker-Pruefung', 'tools/check_ui_overlay_anchors.py'], ['paket', 'thinking-orbs Typen', 'thinking-orbs/dist/types.d.ts'], ['web', 'React Cosmos Fixtures', 'react-cosmos.github.io/docs/fixtures']]
  },
  reasoning: {
    state: 'solving', label: 'Reasoning', start: 23, tokIn: '2,10k', tokOut: '1,05k',
    rows: [['Anker-Erwartungen gelesen', '28 von 28'], ['Widerspruch erkannt', '27 von 28, Stelle 12'], ['Gegenprobe geplant', '900x650']],
    links: [['ziel', 'Kriterium Nachweise', '#criterion/nachweise-messbar'], ['nachweis', 'Messwert Stelle 12', 'messungen/anker-stelle-12.json'], ['auftrag', 'Gegenprobe', '#task/gegenprobe']]
  },
  werkzeuge: {
    state: 'working', label: 'Werkzeuge', start: 41, tokIn: '0,92k', tokOut: '0,44k',
    rows: [['ruff check src tests tools', 'All checks passed'], ['datei_lesen UI-CONTRACT.md', '18 KB'], ['git status', '3 Aenderungen']],
    links: [['terminal', 'ruff (Lint)', 'ruff check src tests tools'], ['datei', 'UI-CONTRACT.md', '~/Documents/Hermes_Skaldir_Agent_Docs/UI-CONTRACT.md'], ['git', 'Arbeitsbaum', 'godsv-supervisor-hardening']]
  },
  quellen: {
    state: 'weaving', label: 'Quellen & Nachweise', start: 58, tokIn: '1,66k', tokOut: '0,72k',
    rows: [['Doku UI-Vertrag', 'Kriterium 1'], ['Messreihe 1280x800', 'Kriterium 2'], ['E2E-Report 17.09.', 'Kriterium 3']],
    links: [['doku', 'UI-CONTRACT 9', 'designs/GEWAEHLT/README.md'], ['bericht', 'E2E-Audit', 'outputs/ui-audit/E2E-AUDIT-2026-09-17.md'], ['zaehler', 'Belege', '#evidence/6-von-10']]
  }
}

function Widget({ topic, dark }) {
  useBarrierefrei()
  const T = TOPICS[topic]
  const P = dark ? DARK : LIGHT
  // Owner-Vorgabe: standardmaessig eingeklappt — nur Reasoning ist offen
  const [open, setOpen] = useState(topic === 'reasoning')
  const time = useLive(T.start)
  return (
    <div data-stage style={{ padding: 18, background: P.bg, fontFamily: SANS, color: P.ink, width: '100%', maxWidth: 620, boxSizing: 'border-box' }}>
      <div style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, marginBottom: 8 }}>
        {T.label} · Kompaktzeile + Liste · {dark ? 'dunkel' : 'hell'} · Klick klappt auf
      </div>
      <div style={{ border: `1px solid ${P.line}`, borderRadius: RADIUS.module, background: P.surface, overflow: 'hidden', maxWidth: 400 }}>
        <motion.div data-orbhead onClick={() => setOpen(o => !o)} whileTap={{ scale: 0.995 }}
          style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 9px', cursor: 'pointer', background: P.surface }}>
          <ThinkingOrb state={T.state} size={20} theme={dark ? 'dark' : 'light'} />
          <span style={{ fontSize: 12, fontWeight: 600, color: P.ink }}>{T.label}</span>
          <span style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 10, fontFamily: MONO, fontSize: TEXT.meta, color: P.sub }}>
            <span>{fmt(time)}</span>
            <span style={{ color: P.faint }}>·</span>
            <span>&#8593;{T.tokIn}</span>
            <span>&#8595;{T.tokOut}</span>
          </span>
          <motion.span animate={{ rotate: open ? 90 : 0 }} transition={SPRING}
            style={{ display: 'inline-flex', color: P.sub }}>
            <IconChevronRight size={11} stroke={2} />
          </motion.span>
        </motion.div>
        <AnimatePresence initial={false}>
          {open && (
            <motion.div key="koerper" data-orbbody initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }}
              exit={{ height: 0, opacity: 0 }} transition={SPRING}
              style={{ overflow: 'hidden', borderTop: `1px solid ${P.hair}` }}>
              <div style={{ padding: '6px 9px 8px' }}>
                <div style={{ display: 'flex', fontFamily: MONO, fontSize: TEXT.meta, color: P.faint, borderBottom: `1px solid ${P.line}`, paddingBottom: 2 }}>
                  <span style={{ flex: 1 }}>WERKZEUG</span><span>ERGEBNIS</span>
                </div>
                {T.rows.map(r => (
                  <div key={r[0]} style={{ display: 'flex', gap: 10, padding: '2px 0', borderBottom: `1px solid ${P.hair}` }}>
                    <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.ink, flex: 1 }}>{r[0]}</span>
                    <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub }}>{r[1]}</span>
                  </div>
                ))}
                <div style={{ marginTop: 6 }}>
                  <div style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint, marginBottom: 3 }}>LINKS</div>
                  {T.links.map(l => (
                    <div key={l[2]} style={{ display: 'flex', alignItems: 'baseline', gap: 7, padding: '1px 0' }}>
                      <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint, minWidth: 52 }}>{l[0]}</span>
                      <span style={{ fontFamily: SANS, fontSize: TEXT.meta, color: P.ink, textDecoration: 'underline', textDecorationColor: P.line }}>{l[1]}</span>
                      <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, marginLeft: 'auto', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 190 }}>{l[2]}</span>
                    </div>
                  ))}
                </div>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  )
}

export default {
  'Suche 01 · Kompaktzeile + Liste (HELL)': () => <Widget topic="suche" dark={false} />,
  'Suche 01 · Kompaktzeile + Liste (DUNKEL)': () => <Widget topic="suche" dark />,
  'Reasoning 01 · Kompaktzeile + Liste (HELL)': () => <Widget topic="reasoning" dark={false} />,
  'Reasoning 01 · Kompaktzeile + Liste (DUNKEL)': () => <Widget topic="reasoning" dark />,
  'Werkzeuge 01 · Kompaktzeile + Liste (HELL)': () => <Widget topic="werkzeuge" dark={false} />,
  'Werkzeuge 01 · Kompaktzeile + Liste (DUNKEL)': () => <Widget topic="werkzeuge" dark />,
  'Quellen 01 · Kompaktzeile + Liste (HELL)': () => <Widget topic="quellen" dark={false} />,
  'Quellen 01 · Kompaktzeile + Liste (DUNKEL)': () => <Widget topic="quellen" dark />
}
