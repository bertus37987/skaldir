/**
 * STEP 4 — VOLLSTÄNDIGES UI: Merge aus Original-Hermes-Desktop-App und Skaldir-UI.
 *
 * Zusammengesetzt aus den echten Werten der Extraktionen (`designs/APP-EXTRAKTION/01..06`)
 * und den abgenommenen Skaldir-Bausteinen (Chat-Bar, Subagenten-Fenster/-Panel, Kontext-Kapsel,
 * Computer-Use dunkel, Orb-Widgets, Startbildschirm):
 *   Titelleiste 34 px, Rail 56 px, Seitenleiste 220 px, Verlaufsspalte min(86 %, 44rem),
 *   Verlaufsschrift 13 px (Nutzer 1.3, Assistent 1.5), Werkzeugzeile 11 px mit 14 px Glyph,
 *   Codeblock 10 px Radius, Composer-Pille radius 999 mit Goal-Bar darüber,
 *   Subagenten-Fenster 234 px, Kontext-Kapsel 208 px, Statusleiste 26 px.
 *
 * Sechs Fassungen: hell · dunkel · Startbildschirm · mit Panels · mit Panel-Manager ·
 * dunkel mit Computer-Use aktiv. Alles beweglich, alle Zustände echt (useState).
 */
import { motion, AnimatePresence, animate } from 'motion/react'
import React, { useEffect, useState } from 'react'
import { IconAdjustmentsHorizontal, IconBorderCornerRounded, IconCheck, IconChevronDown, IconChevronRight, IconColumns2, IconCpu, IconDownload, IconEye, IconFiles, IconFolderOpen, IconGitBranch, IconLayoutGrid, IconLayoutSidebar, IconLock, IconMessage, IconMinus, IconPaperclip, IconPlayerPlay, IconPlus, IconRefresh, IconSearch, IconSettings, IconSquare, IconTerminal2, IconX } from '@tabler/icons-react'

// @ts-expect-error — Laufzeit-Alias (siehe vite.config.ts)
import { ThinkingOrb } from 'thinking-orbs'
import { LIGHT, DARK, MONO, SANS, SPRING, RADIUS, TEXT } from './_tokens'
import { useBarrierefrei } from './_barrierefrei'


const useLive = (start) => {
  const [t, setT] = useState(start)
  useEffect(() => { const id = setInterval(() => setT(v => v + 1), 1000); return () => clearInterval(id) }, [])
  return t
}
const fmt = s => String(Math.floor(s / 60)).padStart(2, '0') + ':' + String(s % 60).padStart(2, '0')

/* ------------------------------------------------------------------ Titelleiste 34 px */
const TitleBar = ({ P, tabs, aktiv, setAktiv, manager, setManager, dark, setDark, neu }) => (
  <div style={{ height: 34, borderBottom: `1px solid ${P.line}`, background: P.surface, display: 'flex', alignItems: 'center', padding: '0 14px', gap: 10 }}>
    <span style={{ width: 12, height: 12, borderRadius: 999, border: `1px solid ${P.line}` }} />
    <span style={{ fontSize: 11.5, color: P.sub }}>God&apos;s Voice</span>
    <span style={{ display: 'flex', gap: 4, marginLeft: 8 }}>
      {tabs.map(t => (
        <motion.span key={t.label} layout onClick={() => setAktiv(t.label)} transition={SPRING}
          style={{ display: 'inline-flex', alignItems: 'center', gap: 6, padding: '3px 8px', borderRadius: 4, fontSize: 11.5, cursor: 'pointer',
                   background: aktiv === t.label ? P.soft : 'transparent', border: `1px solid ${aktiv === t.label ? P.line : 'transparent'}`, color: P.ink }}>
          <t.Icon size={11.5} strokeWidth={1.8} color={P.sub} />{t.label}
        </motion.span>
      ))}
    </span>
    <span style={{ marginLeft: 'auto', display: 'inline-flex', gap: 4, alignItems: 'center' }}>
      {neu ? <KillChip P={P} /> : null}
      <ToolBtn P={P} icon={IconLayoutGrid} title="Layout" />
      <ToolBtn P={P} icon={IconLayoutSidebar} title="Seitenleiste" onClick={() => setManager(v => !v)} badge={manager ? undefined : 0} />
      <ToolBtn P={P} icon={IconColumns2} title="Spalten" />
      <ToolBtn P={P} icon={IconSearch} title="Suchen" />
      <ToolBtn P={P} icon={IconSettings} title="Einstellungen" />
      <ToolBtn P={P} icon={dark ? 'moon' : 'sun'} title="hell/dunkel" onClick={() => setDark(d => !d)} />
    </span>
  </div>
)
const ToolBtn = ({ P, icon: I, title, onClick, badge }) => (
  <motion.span onClick={onClick} title={title} whileHover={{ backgroundColor: P.hair }} whileTap={{ scale: 0.9 }} transition={SPRING}
    style={{ width: 24, height: 24, borderRadius: 4, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer', position: 'relative', border: `1px solid transparent` }}>
    {typeof I === 'string'
      ? <span style={{ fontFamily: MONO, fontSize: 11 }}>◐</span>
      : <I size={13.9} strokeWidth={1.7} color={P.sub} />}
    {badge ? <span style={{ position: 'absolute', top: 1, right: 1, width: 5, height: 5, borderRadius: 999, background: P.ink }} /> : null}
  </motion.span>
)

/* ------------------------------------------------------------------ Navigationsschiene 56 px */
const RAIL = [
  { Icon: IconMessage, label: 'Chat', on: true },
  { Icon: IconFolderOpen, label: 'Projekte' },
  { Icon: IconFiles, label: 'Dateien' },
  { Icon: IconGitBranch, label: 'Review' },
  { Icon: IconTerminal2, label: 'Terminal' },
  { Icon: IconCpu, label: 'Jobs' }
]
const Rail = ({ P, aktiv, setAktiv }) => (
  <div style={{ width: 56, borderRight: `1px solid ${P.line}`, background: P.surface, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 2, padding: '8px 0' }}>
    {RAIL.map(r => (
      <motion.span key={r.label} title={r.label} layout onClick={() => setAktiv(r.label)} whileHover={{ backgroundColor: P.hair }} whileTap={{ scale: 0.94 }} transition={SPRING}
        style={{ width: 40, height: 34, borderRadius: 8, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer',
                 background: aktiv === r.label ? P.soft : 'transparent', border: `1px solid ${aktiv === r.label ? P.line : 'transparent'}` }}>
        <r.Icon size={16} strokeWidth={1.8} color={aktiv === r.label ? P.ink : P.faint} />
      </motion.span>
    ))}
  </div>
)

/* ------------------------------------------------------------------ Seitenleiste 220 px */
const SESSIONS = [
  ['God&apos;s Voice — UI-Abnahme', '14:02', 42, true],
  ['Merge-Vorschau bauen', '13:20', 18],
  ['Panel-Manager und Toggles', '12:44', 27],
  ['Computer-Use dunkel', '11:58', 9],
  ['Orb-Widgets verdichten', '10:31', 33]
]
const Sidebar = ({ P, manager }) => (
  <div style={{ width: 220, borderRight: `1px solid ${P.line}`, background: P.surface, display: 'flex', flexDirection: 'column' }}>
    <div style={{ padding: 8 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, height: 28, padding: '0 8px', border: `1px solid ${P.line}`, borderRadius: 8, background: P.soft }}>
        <IconPlus size={12} stroke={1.8} color={P.sub} /><span style={{ fontSize: 11.5, color: P.sub }}>Neue Sitzung</span>
      </div>
    </div>
    <div style={{ padding: '0 8px 6px', display: 'flex', alignItems: 'center', gap: 6 }}>
      <IconSearch size={11.5} stroke={1.8} color={P.faint} /><span style={{ fontSize: 11, color: P.faint }}>Sitzungen durchsuchen</span>
    </div>
    <div style={{ flex: 1, overflow: 'hidden' }}>
      {SESSIONS.map(([titel, zeit, n, aktiv]) => (
        <motion.div key={titel} layout transition={SPRING}
          style={{ padding: '6px 10px', cursor: 'pointer', background: aktiv ? P.soft : 'transparent', borderLeft: `2px solid ${aktiv ? P.ink : 'transparent'}` }}>
          <div style={{ fontSize: 12, color: P.ink, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }} dangerouslySetInnerHTML={{ __html: titel }} />
          <div style={{ fontFamily: MONO, fontSize: 11, color: P.faint, marginTop: 2 }}>{zeit} · {n} Nachrichten</div>
        </motion.div>
      ))}
    </div>
    <div style={{ padding: 8, borderTop: `1px solid ${P.hair}`, display: 'flex', gap: 6 }}>
      <ToolBtn P={P} icon={IconDownload} title="exportieren" /><ToolBtn P={P} icon={IconRefresh} title="neu laden" /><ToolBtn P={P} icon={IconAdjustmentsHorizontal} title="verwalten" onClick={() => {}} />
    </div>
    {manager ? <ManagerHint P={P} /> : null}
  </div>
)
const ManagerHint = ({ P }) => (
  <div style={{ margin: 8, padding: 8, border: `1px dashed ${P.line}`, borderRadius: 8, fontSize: 11, color: P.sub }}>
    Panel-Manager offen — rechts alle sieben Panels schaltbar.
  </div>
)

/* ------------------------------------------------------------------ Verlauf */
const Bubble = ({ P, children, right }) => (
  <div style={{ display: 'flex', justifyContent: right ? 'flex-end' : 'flex-start' }}>
    <div style={{ maxWidth: right ? '62%' : '86%', background: right ? P.ink : P.surface, color: right ? P.bg : P.ink,
                  border: right ? 'none' : `1px solid ${P.line}`, borderRadius: 12, padding: '8px 12px',
                  fontFamily: MONO, fontSize: 13, lineHeight: right ? 1.3 : 1.5 }}>{children}</div>
  </div>
)
const ToolRow = ({ P, name, meta, orb, offen }) => (
  <div style={{ border: `1px solid ${P.line}`, borderRadius: 8, background: P.surface, maxWidth: 460 }}>
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '5px 8px' }}>
      <span style={{ width: 14 }}>{orb ? <ThinkingOrb state={orb} size={20} theme="light" /> : <IconChevronRight size={12} stroke={1.8} color={P.sub} />}</span>
      <span style={{ fontFamily: MONO, fontSize: 11, color: P.ink, flex: 1, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{name}</span>
      <span style={{ fontFamily: MONO, fontSize: 11, color: P.faint }}>{meta}</span>
    </div>
    <AnimatePresence initial={false}>
      {offen && (
        <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }} transition={SPRING} style={{ overflow: 'hidden', borderTop: `1px solid ${P.hair}` }}>
          <div style={{ padding: '6px 8px', fontFamily: MONO, fontSize: 11, color: P.sub, lineHeight: 1.6 }}>
            pfad: ~/Documents/Hermes_Skaldir_Agent_Docs/UI-CONTRACT.md<br />
            gefunden: 28 Anker-Erwartungen · 0 Abweichungen<br />
            8 px Radius · 6 px Abstand · 11 px Schrift
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  </div>
)
const CodeCard = ({ P, lines }) => (
  <div style={{ background: P.soft, borderRadius: 12, padding: '6px 8px', maxWidth: 460, fontFamily: MONO, fontSize: 11.2, color: P.ink, lineHeight: 1.6 }}>
    {lines.map((l, i) => <div key={i} /* statisch: Indexschluessel ok */>{l}</div>)}
  </div>
)
const OrbWidgetRow = ({ P, offen, setOffen }) => {
  const t = useLive(19)
  return (
    <div style={{ border: `1px solid ${P.line}`, borderRadius: 12, background: P.surface, maxWidth: 400 }}>
      <div onClick={() => setOffen(o => !o)} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 9px', cursor: 'pointer' }}>
        <ThinkingOrb state="solving" size={20} theme="light" />
        <span style={{ fontSize: 12, fontWeight: 600, color: P.ink }}>Reasoning</span>
        <span style={{ marginLeft: 'auto', display: 'inline-flex', gap: 8, fontFamily: MONO, fontSize: 11, color: P.sub }}>
          <span>{fmt(t)}</span><span>·</span><span>↑2,10k</span><span>↓1,05k</span>
        </span>
        <motion.span animate={{ rotate: offen ? 90 : 0 }} transition={SPRING} style={{ display: 'inline-flex' }}><IconChevronRight size={11} stroke={2} color={P.sub} /></motion.span>
      </div>
      <AnimatePresence initial={false}>
        {offen && (
          <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }} transition={SPRING} style={{ overflow: 'hidden', borderTop: `1px solid ${P.hair}` }}>
            <div style={{ padding: '6px 9px 8px' }}>
              {[['Anker-Erwartungen gelesen', '28 von 28'], ['Widerspruch erkannt', '27 von 28, Stelle 12'], ['Gegenprobe geplant', '900x650']].map(r => (
                <div key={r[0]} style={{ display: 'flex', gap: 10, padding: '2px 0', borderBottom: `1px solid ${P.hair}` }}>
                  <span style={{ fontFamily: MONO, fontSize: 11, color: P.ink, flex: 1 }}>{r[0]}</span>
                  <span style={{ fontFamily: MONO, fontSize: 11, color: P.sub }}>{r[1]}</span>
                </div>
              ))}
              <div style={{ marginTop: 5, fontFamily: MONO, fontSize: 11, color: P.faint }}>LINKS</div>
              {[['ziel', '#criterion/nachweise-messbar'], ['nachweis', 'messungen/anker-stelle-12.json']].map(l => (
                <div key={l[1]} style={{ display: 'flex', gap: 7, padding: '1px 0' }}>
                  <span style={{ fontFamily: MONO, fontSize: 11, color: P.faint, minWidth: 48 }}>{l[0]}</span>
                  <span style={{ fontSize: 11, color: P.ink, textDecoration: 'underline', textDecorationColor: P.line }}>{l[1]}</span>
                </div>
              ))}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

/* ------------------------------------------------------------------ Goal-Bar + Composer */
const GoalBar = ({ P, offen, setOffen }) => (
  <div style={{ borderBottom: `1px solid ${P.line}` }}>
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 10px' }}>
      <motion.span onClick={() => setOffen(o => !o)} whileTap={{ scale: 0.88 }} transition={SPRING} style={{ display: 'inline-flex', cursor: 'pointer' }}>
        {offen ? <IconChevronDown size={14} stroke={1.8} color={P.ink} /> : <IconChevronRight size={14} stroke={1.8} color={P.ink} />}
      </motion.span>
      <span style={{ fontSize: 12, fontWeight: 600, color: P.ink }}>skaldir ui-abnahme</span>
      <span style={{ fontFamily: MONO, fontSize: 11, color: P.sub }}>6/9 ✓</span>
      <span style={{ marginLeft: 'auto', fontFamily: MONO, fontSize: 11, color: P.faint }}>esc esc beendet</span>
    </div>
    <AnimatePresence initial={false}>
      {offen && (
        <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }} transition={SPRING} style={{ overflow: 'hidden' }}>
          <div style={{ padding: '0 10px 7px', fontFamily: MONO, fontSize: 11.5, color: P.ink }}>
            {[['├─ ', 'Nachweise sind messbar', true], ['│  ├─ ', 'Anker-Pruefung', true], ['│  └─ ', 'Overlay-Lint', true],
              ['├─ ', 'Echter Nutzerweg laeuft', false], ['│  ├─ ', 'Composer → Session → Senden', true], ['│  └─ ', 'Antwort rendert', false],
              ['└─ ', 'Zustaende bleiben erhalten', false]].map(([pre, label, done], i) => (
              <div key={i} /* statisch: Indexschluessel ok */ style={{ display: 'flex', alignItems: 'center', minHeight: 22, opacity: done ? 0.6 : 1, filter: done ? 'blur(0.3px)' : 'none' }}>
                <span style={{ whiteSpace: 'pre' }}>{pre}</span>
                <span style={{ fontFamily: SANS, fontSize: 12, textDecoration: done ? 'line-through' : 'none' }}>{label}</span>
                <span style={{ marginLeft: 7, width: 12, height: 12, border: `1.3px solid ${P.ink}`, background: done ? P.ink : 'transparent', color: P.surface, fontSize: 11, lineHeight: '11px', textAlign: 'center' }}>{done ? '✓' : ''}</span>
              </div>
            ))}
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  </div>
)
const UploadRow = ({ P, name, ext, size, state, pct }) => (
  <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '1px 5px', border: `1px solid ${P.line}`, background: P.surface, height: 24, borderRadius: 4 }}>
    <span style={{ width: 16, height: 20, border: `1px solid ${P.line}`, background: P.soft }} />
    <span style={{ flex: 1, minWidth: 0, fontFamily: MONO, fontSize: 11, color: P.ink, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{name}</span>
    <span style={{ fontFamily: MONO, fontSize: 11, color: P.faint }}>{ext} · {size}</span>
    {state === 'laeuft'
      ? <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, width: 58 }}>
          <span style={{ flex: 1, height: 3, background: P.hair, border: `1px solid ${P.line}`, display: 'block' }}>
            <motion.span initial={{ width: 0 }} animate={{ width: `${pct}%` }} transition={{ duration: 1.4, ease: 'easeInOut' }} style={{ display: 'block', height: 3, background: P.ink }} />
          </span><span style={{ fontFamily: MONO, fontSize: 11, color: P.sub }}>{pct}%</span>
        </span>
      : <span style={{ fontFamily: MONO, fontSize: 11, color: P.ink }}>✓</span>}
  </div>
)
const Composer = ({ P, dateien, orbOffen, setOrbOffen }) => (
  <div style={{ padding: 10 }}>
    <div style={{ borderRadius: 24, border: `1px solid ${P.line}`, background: P.surface, overflow: 'hidden' }}>
      <AnimatePresence initial={false}>
        {dateien && (
          <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} transition={SPRING} style={{ overflow: 'hidden' }}>
            <div style={{ padding: 8, display: 'flex', flexDirection: 'column', gap: 4, borderBottom: `1px solid ${P.hair}` }}>
              <UploadRow P={P} name="01_PRODUCT_UX_DESIGN.md" ext=".md" size="42 KB" state="fertig" />
              <UploadRow P={P} name="Screenshot 2026-09-17 22-21-00.png" ext=".png" size="1,4 MB" state="laeuft" pct={68} />
            </div>
          </motion.div>
        )}
      </AnimatePresence>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, height: 54, padding: '0 12px 0 16px' }}>
        <motion.span whileHover={{ scale: 1.15, rotate: 90 }} whileTap={{ scale: 0.9 }} transition={SPRING} style={{ display: 'inline-flex', cursor: 'pointer' }}>
          <IconPlus size={17} stroke={1.8} color={P.faint} />
        </motion.span>
        <span style={{ fontFamily: SANS, fontSize: 14.5, color: P.faint, flex: 1 }}>{dateien ? 'Beschreibung ergaenzen' : 'Start with a goal'}</span>
        <motion.span whileHover={{ scale: 1.04 }} whileTap={{ scale: 0.96 }} transition={SPRING} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, cursor: 'pointer' }}>
          <IconPaperclip size={14} stroke={1.8} color={P.faint} />
          <span style={{ fontFamily: SANS, fontSize: 12.5, color: P.faint }}>Med</span>
          <IconChevronDown size={11} stroke={2} color={P.faint} />
        </motion.span>
        <motion.span whileHover={{ scale: 1.05 }} whileTap={{ scale: 0.94 }} transition={SPRING}
          style={{ width: 30, height: 30, borderRadius: 999, background: P.ink, display: 'inline-flex', alignItems: 'center', justifyContent: 'center' }}>
          <svg width={17} height={12} viewBox="0 0 18 12"><polyline points="0,6 3,6 5,2 7,10 9,4 11,7 13,3 15,6 18,6" fill="none" stroke={P.surface} strokeWidth="1.4" strokeLinecap="round" /></svg>
        </motion.span>
      </div>
    </div>
  </div>
)
const SubagentStrip = ({ P, offen, setOffen }) => (
  <div style={{ display: 'flex', gap: 6, padding: '0 12px 6px', flexWrap: 'wrap' }}>
    {[['Pruefer', 'working', '4/7'], ['Sammler', 'working', '3/8'], ['Schreiber', 'breathing', '0/4'], ['Waechter', 'composing', '3/3']]
      .slice(0, offen).map(([n, st, s]) => (
      <motion.span key={n} layout initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -4 }} transition={SPRING}
        style={{ display: 'inline-flex', alignItems: 'center', gap: 5, padding: '3px 8px', border: `1px solid ${P.line}`, background: P.surface, borderRadius: 999, fontFamily: MONO, fontSize: 11 }}>
        <ThinkingOrb state={st} size={20} theme="light" />{n}<span style={{ color: P.sub }}>{s}</span>
      </motion.span>
    ))}
    {offen < 4 ? <motion.span onClick={() => setOffen(4)} whileHover={{ scale: 1.04 }} transition={SPRING}
      style={{ padding: '3px 8px', border: `1px dashed ${P.line}`, borderRadius: 999, fontFamily: MONO, fontSize: 11, color: P.sub, cursor: 'pointer' }}>+ {4 - offen} anzeigen</motion.span> : null}
  </div>
)

/* ------------------------------------------------------------------ schwebende Bausteine */
const SubagentWin = ({ P, onClose }) => (
  <motion.div drag dragMomentum={false} initial={{ opacity: 0, scale: 0.94 }} animate={{ opacity: 1, scale: 1 }} transition={SPRING}
    style={{ position: 'absolute', right: 18, top: 62, width: 234, background: P.surface, border: `1px solid ${P.line}`, boxShadow: '0 14px 38px rgba(0,0,0,0.30)', fontFamily: MONO, cursor: 'grab' }}>
    <div style={{ display: 'flex', alignItems: 'center', gap: 7, padding: '5px 7px', background: P.bg, borderBottom: `1px solid ${P.line}`, touchAction: 'none' }}>
      <ThinkingOrb state="working" size={20} theme="light" />
      <span style={{ fontSize: 11, color: P.sub }}>◆ pruefer</span>
      <span style={{ fontSize: 11, color: P.ink }}>t 00:42</span>
      <span style={{ marginLeft: 'auto', display: 'inline-flex', gap: 4 }}>
        <span title="einklappen" style={{ width: 20, height: 18, border: `1px solid ${P.line}`, display: 'inline-flex', alignItems: 'center', justifyContent: 'center' }}><IconMinus size={10} stroke={2} color={P.ink} /></span>
        <span onClick={onClose} title="schliessen" style={{ width: 20, height: 18, border: `1px solid ${P.line}`, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer' }}><IconX size={9} stroke={2} color={P.ink} /></span>
      </span>
    </div>
    <div style={{ padding: 7, background: P.surface, fontSize: 11, color: P.ink, lineHeight: 1.7 }}>
      › oeffne tools/check_ui_overlay_anchors.py<br />› lese 28 Anker-Erwartungen<br />
      › messe #composer bei 1280x800<br />› vergleiche: 27 von 28 stimmen <span style={{ color: P.sub }}>▍</span>
    </div>
    <div style={{ display: 'flex', alignItems: 'center', gap: 7, padding: '6px 7px', borderTop: `1px solid ${P.line}` }}>
      <span style={{ width: 24, height: 20, background: P.ink, color: P.surface, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontSize: 11 }}>■</span>
      <span style={{ border: `1px solid ${P.ink}`, padding: '2px 5px', display: 'inline-flex', alignItems: 'center', gap: 6, fontFamily: MONO, fontSize: 11 }}>
        {Array.from({ length: 10 }).map((_, i) => <span key={i} /* statisch: Indexschluessel ok */ style={{ width: 5, height: 9, background: i < 6 ? P.ink : 'transparent', border: `1px solid ${P.ink}` }} />)}<span>62%</span>
      </span>
    </div>
  </motion.div>
)
const ContextCapsule = ({ P }) => {
  const [offen, setOffen] = useState(true)
  return (
    <motion.div drag dragMomentum={false} transition={SPRING}
      style={{ position: 'absolute', right: 18, bottom: 132, cursor: 'grab', touchAction: 'none' }}>
      <AnimatePresence initial={false} mode="wait">
        {offen ? (
          <motion.div key="k" initial={{ opacity: 0, scale: 0.95 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0, scale: 0.95 }} transition={SPRING}
            style={{ width: 208, borderRadius: 999, border: `1px solid ${P.line}`, background: P.surface, boxShadow: '0 10px 28px rgba(0,0,0,0.22)', padding: '9px 13px' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ fontFamily: MONO, fontSize: 13, color: P.ink }}>[██████░░░░]</span>
              <span style={{ marginLeft: 'auto', fontFamily: MONO, fontSize: 12, color: P.ink }}>62%</span>
              <span onClick={() => setOffen(false)} title="Kontext schliessen" style={{ width: 18, height: 18, borderRadius: 999, border: `1px solid ${P.line}`, fontFamily: MONO, fontSize: 11, color: P.ink, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer' }}>✕</span>
            </div>
            <div style={{ display: 'flex', gap: 7, fontFamily: MONO, fontSize: 11, color: P.sub }}><span>128k/200k</span><span>·</span><span>reserve 12k</span></div>
          </motion.div>
        ) : (
          <motion.span key="g" initial={{ opacity: 0, scale: 0.9 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0, scale: 0.9 }} transition={SPRING} onClick={() => setOffen(true)} title="Kontext oeffnen"
            style={{ display: 'inline-flex', gap: 6, padding: '5px 10px', borderRadius: 999, background: P.surface, border: `1px solid ${P.line}`, fontFamily: MONO, fontSize: 11, color: P.ink, boxShadow: '0 6px 18px rgba(0,0,0,0.20)' }}>[██░░] 62% ▸</motion.span>
        )}
      </AnimatePresence>
    </motion.div>
  )
}
const CursorLayer = ({ P }) => (
  <div data-agent-visible="false" style={{ position: 'absolute', inset: 0, pointerEvents: 'none' }}>
    {[['tl', { left: 0, top: 0 }], ['tr', { right: 0, top: 0 }], ['bl', { left: 0, bottom: 0 }], ['br', { right: 0, bottom: 0 }]].map(([c, pos]) => (
      <motion.svg key={c} width={120} height={120} viewBox="0 0 120 120" style={{ position: 'absolute', ...pos, filter: 'blur(4px)', overflow: 'visible' }}>
        <motion.g animate={{ scale: [1, 2.28, 1], x: [0, 76.8, 0], y: [0, 76.8, 0], opacity: [0.32, 0.88, 0.32] }}
          transition={{ duration: 3.6, ease: 'easeInOut', repeat: Infinity }}>
          <path d="M 120 0 L 18 0 A 18 18 0 0 0 0 18 L 0 120 L 2 120 L 12 12 L 120 2 Z" fill="rgba(226,226,226,0.56)" stroke="rgba(226,226,226,0.56)" strokeWidth="1.5" />
        </motion.g>
      </motion.svg>
    ))}
    <motion.svg width={22} height={22} viewBox="0 0 24 24" style={{ position: 'absolute', left: '58%', top: '46%' }}
      initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.2 }}>
      <motion.path d="M2 1 L2 19 L6.6 14.6 L9.6 21 L12.4 19.6 L9.3 13.3 L15.4 12.9 Z" fill="rgba(245,245,245,0.94)" stroke="rgba(120,120,120,0.9)" strokeWidth="1.1"
        animate={{ x: [0, 2.5, 0], y: [0, 1.5, 0] }} transition={{ duration: 6, ease: 'easeInOut', repeat: Infinity }} />
    </motion.svg>
    <div style={{ position: 'absolute', left: '50%', bottom: 14, transform: 'translateX(-50%)', display: 'flex', alignItems: 'center', gap: 10, padding: '0 10px 0 13px', height: 32, background: 'rgba(17,17,17,0.88)', border: '1.15px solid rgba(255,255,255,0.3)', borderRadius: 999 }}>
      <ThinkingOrb state="working" size={20} theme="dark" />
      <span style={{ fontSize: 12, color: '#F5F5F5' }}>Skaldir steuert</span>
      <span style={{ fontFamily: MONO, fontSize: 11, color: '#B7B7B7' }}>00:42</span>
      <span style={{ minWidth: 40, height: 26, borderRadius: 8, background: '#F5F5F5', color: '#111111', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontFamily: MONO, fontSize: 11 }}>esc</span>
    </div>
  </div>
)

/* ------------------------------------------------------------------ Panel-Host und Statusleiste */
const PanelHost = ({ P, neu }) => {
  const [tab, setTab] = useState('Cockpit')
  return (
    <div style={{ width: 300, borderLeft: `1px solid ${P.line}`, background: P.surface, display: 'flex', flexDirection: 'column' }}>
      <div style={{ display: 'flex', gap: 2, padding: 6, borderBottom: `1px solid ${P.hair}` }}>
        {['Cockpit', 'Prufung', 'Werkzeuge', 'Settings'].map(t => (
          <motion.span key={t} layout onClick={() => setTab(t)} transition={SPRING}
            style={{ padding: '3px 7px', borderRadius: 4, fontSize: 11, cursor: 'pointer', background: tab === t ? P.soft : 'transparent', color: tab === t ? P.ink : P.sub, border: `1px solid ${tab === t ? P.line : 'transparent'}` }}>{t}</motion.span>
        ))}
      </div>
      <div style={{ padding: 10, display: 'flex', flexDirection: 'column', gap: 8, overflow: 'hidden' }}>
        <div style={{ fontSize: 12, fontWeight: 600 }}>{tab}</div>
        {[['Ziel', 'skaldir ui-abnahme · 6/9'], ['Laufzeit', '00:42 · 12,4 tok/s'], ['Werkzeug', 'read_file · 18 KB'], ['Nachweise', '6 belegt · 3 offen'], ['Subagenten', '3 offen · 1 fertig']].map(([k, v]) => (
          <div key={k} style={{ display: 'flex', gap: 8, borderBottom: `1px solid ${P.hair}`, paddingBottom: 5 }}>
            <span style={{ fontFamily: MONO, fontSize: 11, color: P.faint, minWidth: 74 }}>{k}</span>
            <span style={{ fontFamily: MONO, fontSize: 11, color: P.ink }}>{v}</span>
          </div>
        ))}
        {neu ? (
          <div style={{ border: `1px solid ${P.line}`, borderRadius: 8, padding: 8 }}>
            <div style={{ fontFamily: MONO, fontSize: 11, color: P.faint, marginBottom: 5 }}>MINING-KANDIDATEN · 2</div>
            {[['Skill: Anker messen', '7x', 'anlegen'], ['CLI: Bericht bauen', '5x', 'anlegen']].map(([n, h, a]) => (
              <div key={n} style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '3px 0', borderBottom: `1px solid ${P.hair}` }}>
                <span style={{ flex: 1, fontFamily: MONO, fontSize: 11, color: P.ink }}>{n}</span>
                <span style={{ fontFamily: MONO, fontSize: 11, color: P.sub }}>{h}</span>
                <span style={{ fontFamily: MONO, fontSize: 11, color: P.ink, textDecoration: 'underline', textDecorationColor: P.line, cursor: 'pointer' }}>{a}</span>
              </div>
            ))}
          </div>
        ) : null}
        <div style={{ border: `1px solid ${P.line}`, borderRadius: 8, padding: 8, background: P.soft }}>
          <div style={{ fontFamily: MONO, fontSize: 11, color: P.faint, marginBottom: 4 }}>ARTEFAKT-VORSCHAU</div>
          <div style={{ fontFamily: MONO, fontSize: 11, color: P.ink, lineHeight: 1.7 }}>bericht.md · 18 KB<br />32 Zeilen · 4 Abschnitte</div>
          <div style={{ display: 'flex', gap: 6, marginTop: 6 }}>
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 11, border: `1px solid ${P.ink}`, background: P.ink, color: P.surface, padding: '2px 7px', borderRadius: 4 }}><IconEye size={11} stroke={1.8} />oeffnen</span>
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 11, border: `1px solid ${P.line}`, padding: '2px 7px', borderRadius: 4 }}><IconLock size={11} stroke={1.8} />sperren</span>
          </div>
        </div>
      </div>
    </div>
  )
}
const StatusBar = ({ P, live, cuOn }) => (
  <div style={{ height: 26, borderTop: `1px solid ${P.line}`, background: P.surface, display: 'flex', alignItems: 'center', gap: 14, padding: '0 12px', fontFamily: MONO, fontSize: 11, color: P.sub }}>
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}><span style={{ width: 6, height: 6, borderRadius: 999, background: P.ink }} />godsv-tools · verbunden</span>
    <span>opencode-go · free</span>
    <span>Reasoning Med</span>
    <span>↑ 12,4k ↓ 4,1k tok</span>
    <span>{fmt(live)}</span>
    <span style={{ marginLeft: 'auto' }}>{cuOn ? 'Computer-Use aktiv · Agent sieht diese Ebene nicht' : 'Computer-Use aus'}</span>
  </div>
)

/* ------------------------------------------------------------------ Startbildschirm */
const SLOGANS = ['Give me a goal I actually complete it.', 'Ich messe, ich behaupte nicht.', 'Zeig mir das Ziel, ich zeige dir den Beleg.', 'Kein Fortschritt ohne Messung.']
const StartScreen = ({ P, dark }) => {
  const [i, setI] = useState(0)
  useEffect(() => { const id = setInterval(() => setI(v => (v + 1) % SLOGANS.length), 4200); return () => clearInterval(id) }, [])
  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 18, position: 'relative' }}>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(9, 4px)', gap: 4 }}>
        {Array.from({ length: 81 }).map((_, k) => (
          <motion.span key={k} initial={{ opacity: 0, scale: 0.4 }} animate={{ opacity: 0.35, scale: 1 }} transition={{ ...SPRING, delay: k * 0.004 }}
            style={{ width: 4, height: 4, borderRadius: 999, background: P.ink }} />
        ))}
      </div>
      <motion.span initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.4 }}
        style={{ fontFamily: MONO, fontSize: 13, color: P.ink, letterSpacing: 3 }}>SKALDIR</motion.span>
      <div style={{ position: 'relative', height: 22 }}>
        <AnimatePresence mode="wait">
          <motion.span key={i} /* statisch: Indexschluessel ok */ initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }} transition={SPRING}
            style={{ position: 'absolute', fontFamily: SANS, fontSize: 13, color: P.sub, whiteSpace: 'nowrap' }}>{SLOGANS[i]}</motion.span>
        </AnimatePresence>
      </div>
      <span style={{ fontFamily: MONO, fontSize: 11, color: P.faint }}>Ziel eintippen oder Datei anhängen · Kein Orb im leeren Chat</span>
    </div>
  )
}

/* ------------------------------------------------------------------ neue Elemente (Step 3) */
const KillChip = ({ P }) => (
  <motion.span whileHover={{ scale: 1.03 }} whileTap={{ scale: 0.95 }} transition={SPRING}
    style={{ display: 'inline-flex', alignItems: 'center', gap: 6, padding: '3px 9px', borderRadius: 4, border: `1.5px solid ${P.ink}`, fontSize: 11, color: P.ink, cursor: 'pointer' }}>
    <IconSquare size={10} stroke={2.4} />Not-Aus
  </motion.span>
)
const VoiceLine = ({ P }) => {
  const [offen, setOffen] = useState(false)
  return (
    <div style={{ borderLeft: `2px solid ${P.ink}`, paddingLeft: 9, maxWidth: 620 }}>
      <div onClick={() => setOffen(o => !o)} style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
        <ThinkingOrb state="breathing" size={20} theme="light" />
        <span style={{ fontFamily: MONO, fontSize: 12, color: P.ink }}>Voice: ich halte an — der Beleg fuer Kriterium 4 fehlt noch.</span>
        <motion.span animate={{ rotate: offen ? 90 : 0 }} transition={SPRING} style={{ display: 'inline-flex' }}><IconChevronRight size={11} stroke={2} color={P.sub} /></motion.span>
        <span style={{ fontFamily: MONO, fontSize: 11, color: P.faint }}>angehalten</span>
      </div>
      <AnimatePresence initial={false}>
        {offen && (
          <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }} transition={SPRING} style={{ overflow: 'hidden' }}>
            <div style={{ paddingTop: 5, fontFamily: MONO, fontSize: 11, color: P.sub, lineHeight: 1.7 }}>
              Grund: Nachweis „messe #composer bei 1280x800" liegt nicht vor.<br />
              Belege: 6 von 10 · offen: 4<br />
              Weg: fortsetzen nach der Messung — <span style={{ textDecoration: 'underline', textDecorationColor: P.line }}>Messen starten</span>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
const PactRow = ({ P }) => {
  const [offen, setOffen] = useState(false)
  return (
    <div style={{ border: `1px solid ${P.line}`, borderRadius: 12, background: P.surface, maxWidth: 620 }}>
      <div onClick={() => setOffen(o => !o)} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 9px', cursor: 'pointer' }}>
        <span style={{ fontFamily: SANS, fontSize: 12, fontWeight: 600, color: P.ink }}>Ziel-Auftrag · Pakt v4</span>
        <span style={{ fontFamily: MONO, fontSize: 11, color: P.sub }}>6/9 Kriterien · Budget 47 %</span>
        <span style={{ marginLeft: 'auto', fontFamily: MONO, fontSize: 11, color: P.faint }}>bestätigt 14:02</span>
        <motion.span animate={{ rotate: offen ? 90 : 0 }} transition={SPRING} style={{ display: 'inline-flex' }}><IconChevronRight size={11} stroke={2} color={P.sub} /></motion.span>
      </div>
      <AnimatePresence initial={false}>
        {offen && (
          <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }} transition={SPRING} style={{ overflow: 'hidden', borderTop: `1px solid ${P.hair}` }}>
            <div style={{ padding: '6px 9px 8px', fontFamily: MONO, fontSize: 11, color: P.ink, lineHeight: 1.7 }}>
              Ziel: Skaldir-UI vollständig entwerfen und messen<br />
              Nicht-Ziele: <span style={{ textDecoration: 'line-through', opacity: 0.7 }}>Produktcode ändern</span> · <span style={{ textDecoration: 'line-through', opacity: 0.7 }}>Publish</span><br />
              Budget: 00:42 · 12,4k tok · 0,31 € von 4,00 €<br />
              Kriterien: 6 belegt · 3 offen · <span style={{ textDecoration: 'underline', textDecorationColor: P.line }}>Kriterium ergänzen</span>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
const ActionsRow = ({ P }) => (
  <div style={{ display: 'flex', gap: 6, padding: '0 12px 6px' }}>
    {['Memory', 'Skills', 'Analyse', 'Jobs', 'Werkzeug suchen'].map(a => (
      <motion.span key={a} whileHover={{ y: -1 }} whileTap={{ scale: 0.96 }} transition={SPRING}
        style={{ padding: '2px 7px', border: `1px solid ${P.line}`, borderRadius: 999, fontFamily: MONO, fontSize: 11, color: P.sub, cursor: 'pointer', background: P.surface }}>{a}</motion.span>
    ))}
  </div>
)

/* ------------------------------------------------------------------ fehlende Flaechen (Befund C1) */
const Taste = ({ P, children }) => (
  <span style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', height: 22, minWidth: 22, padding: '0 6px',
                 borderRadius: RADIUS.micro, border: `1px solid ${P.line}`, background: P.soft, color: P.ink, fontFamily: MONO, fontSize: TEXT.meta }}>{children}</span>
)
const BEFEHLE = [['Sitzung fortsetzen', '1'], ['Ziel setzen', '2'], ['Datei anhaengen', 'U'], ['Einstellungen', ','], ['Graph oeffnen', 'G'], ['Not-Aus', '.']]
const Palette = ({ P }) => {
  const [q, setQ] = useState('')
  const treffer = BEFEHLE.filter(b => b[0].toLowerCase().indexOf(q.toLowerCase()) >= 0)
  return (
    <motion.div data-palette initial={{ opacity: 0, scale: 0.97, y: -6 }} animate={{ opacity: 1, scale: 1, y: 0 }} transition={SPRING}
      style={{ position: 'absolute', left: '50%', top: 64, x: '-50%', width: 520, background: P.surface, border: `1px solid ${P.line}`,
               borderRadius: RADIUS.dialog, boxShadow: '0 22px 60px rgba(0,0,0,0.34)', zIndex: 30, overflow: 'hidden' }}>
      <input value={q} onChange={e => setQ(e.target.value)} placeholder="Befehl suchen" aria-label="Befehl suchen"
        style={{ width: '100%', boxSizing: 'border-box', border: 'none', borderBottom: `1px solid ${P.hair}`, padding: '11px 13px',
                 background: 'transparent', color: P.ink, fontFamily: SANS, fontSize: TEXT.body, outline: 'none' }} />
      <div style={{ padding: 8 }}>
        {treffer.map(([n, k], i) => (
          <div key={n} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '6px 8px', borderRadius: RADIUS.row, background: i === 0 ? P.soft : 'transparent' }}>
            <span style={{ flex: 1, fontSize: TEXT.body, color: P.ink }}>{n}</span><Taste P={P}>{k}</Taste>
          </div>
        ))}
        {treffer.length === 0 && <div style={{ padding: 8, fontSize: TEXT.meta, color: P.sub }}>kein Treffer</div>}
        <div style={{ padding: '6px 8px', fontSize: TEXT.meta, color: P.sub, borderTop: `1px solid ${P.hair}`, marginTop: 4 }}>Befehlspalette · ⌘K · Pfeiltasten waehlen, Enter ausfuehren</div>
      </div>
    </motion.div>
  )
}
const Toasts = ({ P }) => (
  <div style={{ position: 'absolute', right: 16, bottom: 56, display: 'flex', flexDirection: 'column', gap: 8, width: 304, zIndex: 25 }}>
    {[['Fertig: 28 Anker geprueft', 'gerade eben'], ['Warnung: Kontext zu 62 % voll', 'vor 2 Minuten'], ['Bericht gespeichert', 'vor 5 Minuten']].map(([a, b], i) => (
      <motion.div key={a} initial={{ opacity: 0, x: 24 }} animate={{ opacity: 1, x: 0 }} transition={{ ...SPRING, delay: 0.08 + i * 0.07 }}
        style={{ background: P.surface, border: `1px solid ${P.line}`, borderRadius: RADIUS.module, padding: '8px 10px', boxShadow: '0 10px 26px rgba(0,0,0,0.22)' }}>
        <div style={{ fontSize: TEXT.meta, color: P.ink }}>{a}</div>
        <div style={{ fontSize: TEXT.meta, color: P.sub, marginTop: 2 }}>{b}</div>
      </motion.div>
    ))}
  </div>
)
const GraphTab = ({ P }) => {
  const knoten = [['Nachweise messbar', 40, 20, true], ['Anker-Pruefung', 180, 12, true], ['Overlay-Lint', 180, 52, true], ['Nutzerweg laeuft', 320, 34, false], ['Composer→Senden', 460, 20, true], ['Antwort rendert', 460, 62, false]]
  return (
    <div style={{ border: `1px solid ${P.line}`, borderRadius: RADIUS.module, background: P.surface, padding: 10, maxWidth: 620 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
        <span style={{ fontSize: TEXT.body, fontWeight: 600, color: P.ink }}>Plan · 6/9 belegt</span>
        <span style={{ marginLeft: 'auto', fontSize: TEXT.meta, color: P.sub }}>Graph-Tab · Klick auf einen Knoten zeigt den Nachweis</span>
      </div>
      <svg width="100%" height="110" viewBox="0 0 620 110" role="img" aria-label="Zielgraph mit sechs Knoten">
        <line x1="150" y1="30" x2="180" y2="22" stroke={P.line} strokeWidth="1" />
        <line x1="150" y1="30" x2="180" y2="62" stroke={P.line} strokeWidth="1" />
        <line x1="285" y1="62" x2="320" y2="44" stroke={P.line} strokeWidth="1" />
        <line x1="430" y1="44" x2="460" y2="30" stroke={P.line} strokeWidth="1" />
        <line x1="430" y1="44" x2="460" y2="72" stroke={P.hair} strokeWidth="1" />
        {knoten.map(([n, x, y, ok]) => (
          <g key={n}>
            <rect x={x} y={y} width={135} height={26} rx={RADIUS.row / 2} fill={ok ? P.soft : 'transparent'} stroke={P.line} />
            <text x={x + 8} y={y + 17} fontSize={TEXT.meta} fill={P.ink} fontFamily={SANS}>{ok ? '✓ ' : ''}{n}</text>
          </g>
        ))}
      </svg>
    </div>
  )
}
const MsgAktionen = ({ P }) => (
  <div style={{ display: 'flex', gap: 6, marginTop: 4 }}>
    {['kopieren', 'erneut senden', 'als Nachweis'].map(a => (
      <motion.span key={a} whileHover={{ y: -1 }} transition={SPRING}
        style={{ padding: '2px 8px', border: `1px solid ${P.line}`, borderRadius: RADIUS.pill, fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, background: P.surface, cursor: 'pointer' }}>{a}</motion.span>
    ))}
  </div>
)
const SettingsDialog = ({ P, onClose }) => (
  <motion.div data-settings initial={{ opacity: 0, scale: 0.97 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0, scale: 0.97 }} transition={SPRING}
    style={{ position: 'absolute', left: '50%', top: 60, x: '-50%', width: 520, background: P.surface, border: `1px solid ${P.line}`,
             borderRadius: RADIUS.dialog, boxShadow: '0 22px 60px rgba(0,0,0,0.34)', zIndex: 30, overflow: 'hidden' }}>
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '12px 14px', borderBottom: `1px solid ${P.hair}` }}>
      <span style={{ fontSize: TEXT.title, fontWeight: 600, color: P.ink }}>Einstellungen</span>
      <span style={{ marginLeft: 'auto' }}><span onClick={onClose} title="schliessen" style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 24, height: 24, border: `1px solid ${P.line}`, borderRadius: RADIUS.micro, cursor: 'pointer', fontFamily: MONO, fontSize: TEXT.meta }}>✕</span></span>
    </div>
    <div style={{ padding: 14, display: 'flex', flexDirection: 'column', gap: 10 }}>
      {[['Modell', 'opencode-go · glm-5.3-flash'], ['Reasoning', 'Med'], ['Helligkeit', 'dunkel'], ['Anbieter-Limit je Datei', '5 MB'], ['Schluessel', 'drei hinterlegt · einer fehlt']].map(([k, v]) => (
        <div key={k} style={{ display: 'flex', gap: 10, alignItems: 'center', borderBottom: `1px solid ${P.hair}`, paddingBottom: 6 }}>
          <span style={{ flex: 1, fontSize: TEXT.body, color: P.ink }}>{k}</span>
          <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub }}>{v}</span>
        </div>
      ))}
    </div>
  </motion.div>
)
const OnboardingKarte = ({ P }) => {
  const [schritt, setSchritt] = useState(1)
  const schritte = ['Anbieter waehlen', 'Schluessel einsetzen', 'Verbindung pruefen', 'Bereit']
  return (
    <div data-onboarding style={{ border: `1px solid ${P.line}`, borderRadius: RADIUS.dialog, background: P.surface, padding: 14, maxWidth: 560 }}>
      <div style={{ fontSize: TEXT.title, fontWeight: 600, color: P.ink }}>Erster Start</div>
      <div style={{ fontSize: TEXT.meta, color: P.sub, margin: '4px 0 10px' }}>{schritt} von 4 · dauert etwa eine Minute</div>
      <div style={{ height: 4, background: P.hair, borderRadius: RADIUS.pill, overflow: 'hidden', marginBottom: 12 }}>
        <motion.div animate={{ width: `${(schritt / 4) * 100}%` }} transition={SPRING} style={{ height: 4, background: P.ink }} />
      </div>
      {schritte.map((s, i) => (
        <div key={s} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '3px 0' }}>
          <span style={{ width: 14, height: 14, border: `1.3px solid ${P.ink}`, borderRadius: RADIUS.micro, textAlign: 'center', fontSize: TEXT.meta, lineHeight: '13px', background: i < schritt ? P.ink : 'transparent', color: P.surface }}>{i < schritt ? '✓' : ''}</span>
          <span style={{ fontSize: TEXT.body, color: P.ink, opacity: i < schritt ? 0.65 : 1 }}>{s}</span>
        </div>
      ))}
      <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
        <motion.span onClick={() => setSchritt(s => Math.min(4, s + 1))} whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.97 }} transition={SPRING}
          style={{ padding: '5px 11px', background: P.ink, color: P.surface, borderRadius: RADIUS.micro, fontSize: TEXT.body, cursor: 'pointer' }}>Weiter</motion.span>
        <motion.span onClick={() => setSchritt(1)} whileTap={{ scale: 0.97 }} transition={SPRING}
          style={{ padding: '5px 11px', border: `1px solid ${P.line}`, borderRadius: RADIUS.micro, fontSize: TEXT.body, color: P.ink, cursor: 'pointer' }}>Zuruecksetzen</motion.span>
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------ Rahmen */
function Frame({ variante }) {
  useBarrierefrei()
  const [dark, setDark] = useState(variante === 'dunkel' || variante === 'cu')
  const P = dark ? DARK : LIGHT
  const [tab, setTab] = useState('Chat')
  const [rail, setRail] = useState('Chat')
  const [goalOffen, setGoalOffen] = useState(true)
  // Regel des Owners: Orb-Widgets sind standardmaessig eingeklappt; in Fassung 07 bleibt nur Reasoning sichtbar
  const [orbOffen, setOrbOffen] = useState(variante !== 'neu')
  const [manager, setManager] = useState(variante === 'manager')
  const [subWin, setSubWin] = useState(variante !== 'start')
  const [panelHost, setPanelHost] = useState(variante === 'panels' || variante === 'manager' || variante === 'neu')
  const [dateien, setDateien] = useState(true)
  const [strip, setStrip] = useState(3)
  const live = useLive(42)
  const neu = variante === 'neu'
  const tafel = variante === 'palette' || variante === 'start-einrichtung'
  const zeigePalette = variante === 'palette'
  const zeigeTafel = variante === 'start-einrichtung'
  const cuOn = variante === 'cu' || (variante === 'dunkel' && false)
  const tabs = [{ label: 'Chat', Icon: IconMessage }, { label: 'Plan', Icon: IconGitBranch }, { label: 'Dateien', Icon: IconFiles }, { label: 'Terminal', Icon: IconTerminal2 }]
  return (
    <div data-stage style={{ padding: 18, background: P.bg, fontFamily: SANS, color: P.ink }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 8 }}>
        <span style={{ fontFamily: MONO, fontSize: 12, fontWeight: 600 }}>Vollstaendiges UI · Merge Original-App + Skaldir</span>
        <motion.span onClick={() => setDark(d => !d)} whileTap={{ scale: 0.94 }} transition={SPRING}
          style={{ marginLeft: 'auto', fontFamily: MONO, fontSize: 11, border: `1px solid ${P.line}`, padding: '2px 8px', borderRadius: 999, cursor: 'pointer' }}>
          {dark ? 'Anzeige dunkel — klicken fuer hell' : 'Anzeige hell — klicken fuer dunkel'}
        </motion.span>
      </div>
      <div data-fenster style={{ position: 'relative', width: 'min(1362px, 100%)', height: 900, border: `1px solid ${P.line}`, borderRadius: 12, overflow: 'hidden', background: P.bg, display: 'flex' }}>
        {cuOn ? <CursorLayer P={P} /> : null}
        {zeigePalette ? <Toasts P={P} /> : null}
        <Rail P={P} aktiv={rail} setAktiv={setRail} />
        <Sidebar P={P} manager={manager} />
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
          <TitleBar P={P} tabs={tabs} aktiv={tab} setAktiv={setTab} manager={manager} setManager={setManager} dark={dark} setDark={setDark} neu={neu} />
          <div style={{ flex: 1, display: 'flex', minHeight: 0 }}>
            <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0, position: 'relative' }}>
              {(variante === 'start' || zeigeTafel) ? <StartScreen P={P} dark={dark} /> : (
                <div style={{ flex: 1, padding: 14, display: 'flex', flexDirection: 'column', gap: 8, overflowY: 'auto' }}>
                  <Bubble P={P} right>&gt; zerleg das in drei spuren und miss die anker</Bubble>
                  <Bubble P={P}>Ich zerlege in drei Spuren und messe die 28 Anker gegen. Die Pruefung laeuft in ihrem eigenen Fenster.</Bubble>
                  {zeigePalette ? <MsgAktionen P={P} /> : null}
                  {zeigePalette ? <GraphTab P={P} /> : null}
                  {neu ? <VoiceLine P={P} /> : null}
                  {neu ? <PactRow P={P} /> : null}
                  <ToolRow P={P} name="check_ui_overlay_anchors.py" meta="28 Anker · 0 Abweichungen" orb="working" offen />
                  <CodeCard P={P} lines={['$ node tools/check_ui_overlay_anchors.py', '28 erwartet · 28 gefunden · RC 0']} />
                  <OrbWidgetRow P={P} offen={orbOffen} setOffen={setOrbOffen} />
                  <div style={{ display: 'flex', gap: 8, alignItems: 'center', fontFamily: MONO, fontSize: 11, color: P.sub }}>
                    <span style={{ textDecoration: 'underline', textDecorationColor: P.line }}>Quellen 6</span><span>·</span><span>belegt 6/10</span>
                  </div>
                </div>
              )}
              {subWin && variante !== 'start' ? <SubagentWin P={P} onClose={() => setSubWin(false)} /> : null}
              {variante !== 'start' ? <ContextCapsule P={P} /> : null}
              {variante !== 'start' && !zeigeTafel ? <SubagentStrip P={P} offen={strip} setOffen={setStrip} /> : null}
              {zeigeTafel ? (
                <div style={{ padding: '0 14px 14px', display: 'flex', gap: 14, alignItems: 'flex-start' }}>
                  <OnboardingKarte P={P} />
                </div>
              ) : null}
              <div style={{ padding: '0 14px 12px', display: 'flex', justifyContent: 'center' }}>
                <div style={{ width: '100%', borderRadius: 24, background: P.surface, border: `1px solid ${P.line}` }}>
                  {neu ? <ActionsRow P={P} /> : null}
                  <GoalBar P={P} offen={goalOffen} setOffen={setGoalOffen} />
                  <Composer P={P} dateien={dateien} orbOffen={orbOffen} setOrbOffen={setOrbOffen} />
                </div>
              </div>
            </div>
            {panelHost ? <PanelHost P={P} neu={neu} /> : null}
          </div>
          <StatusBar P={P} live={live} cuOn={cuOn} />
        </div>
        {zeigePalette ? <Palette P={P} /> : null}
        <AnimatePresence>
          {zeigeTafel ? <SettingsDialog P={P} onClose={() => {}} /> : null}
          {manager && variante !== 'dunkel' && variante !== 'cu' ? (
            <motion.div key="mgr" initial={{ opacity: 0, x: 20 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: 20 }} transition={SPRING}
              style={{ position: 'absolute', right: 20, top: 46, width: 250, background: P.surface, border: `1px solid ${P.line}`, borderRadius: 12, boxShadow: '0 14px 34px rgba(0,0,0,0.24)' }}>
              <div style={{ padding: '7px 9px', borderBottom: `1px solid ${P.line}`, fontFamily: MONO, fontSize: 11, letterSpacing: 1, color: P.sub }}>PANELS · 5 VON 7 AN</div>
              <div style={{ padding: 6 }}>
                {[['Kontext-Kapsel', true], ['Subagenten-Fenster', subWin], ['Subagenten-Streifen', strip > 0], ['Orb-Widgets', orbOffen], ['Computer-Use', cuOn], ['Ziel-Baum', goalOffen], ['Panels rechts', panelHost]].map(([n, an]) => (
                  <div key={n} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '4px 6px' }}>
                    <span style={{ flex: 1, fontSize: 11.5 }}>{n}</span>
                    <motion.span layout onClick={() => { if (n === 'Panels rechts') setPanelHost(v => !v); if (n === 'Subagenten-Fenster') setSubWin(v => !v); if (n === 'Ziel-Baum') setGoalOffen(v => !v); if (n === 'Orb-Widgets') setOrbOffen(v => !v) }}
                      transition={SPRING} style={{ width: 28, height: 16, borderRadius: 999, padding: 2, display: 'inline-flex', alignItems: 'center', justifyContent: an ? 'flex-end' : 'flex-start', background: an ? P.ink : P.hair, border: `1px solid ${an ? P.ink : P.line}`, cursor: 'pointer' }}>
                      <motion.span layout transition={SPRING} style={{ width: 12, height: 12, borderRadius: 999, background: an ? P.surface : P.faint }} />
                    </motion.span>
                  </div>
                ))}
              </div>
            </motion.div>
          ) : null}
        </AnimatePresence>
      </div>
      <div style={{ fontFamily: MONO, fontSize: 11, color: P.faint, marginTop: 8 }}>
        Masse aus der Extraktion: Titelleiste 34 · Rail 56 · Seitenleiste 220 · Verlauf 13 px · Composer-Pille radius 999 · Subagenten-Fenster 234 · Kontext-Kapsel 208 · Statusleiste 26
      </div>
    </div>
  )
}
export default {
  '01 · VOLLSTÄNDIG HELL: Titelleiste, Rail, Verlauf, Chat-Bar, Subagenten, Kontext, Panels': () => <Frame variante="hell" />,
  '02 · VOLLSTÄNDIG DUNKEL (identisch)': () => <Frame variante="dunkel" />,
  '03 · STARTSCHIRM: Punktraster, Logo, rotierende Sprüche': () => <Frame variante="start" />,
  '04 · MIT PANELS: Panel-Host rechts, Artefakt-Vorschau, Inspektion': () => <Frame variante="panels" />,
  '05 · MIT PANEL-MANAGER: alle sieben Panels schaltbar': () => <Frame variante="manager" />,
  '06 · COMPUTER-USE AKTIV (dunkel): Ecken-Puls von der Ecke zur Mitte + Zeiger + Pille': () => <Frame variante="cu" />,
  '07 · MIT DEN NEUEN ELEMENTEN: Not-Aus in der Kopfzeile, Voice-Zeile, Pakt inline, Aktionskuerzel, Mining im Panel': () => <Frame variante="neu" />,
  '08 · MIT BEFEHLSPALETTE, TOASTS, GRAPH-TAB UND NACHRICHTENAKTIONEN': () => <Frame variante="palette" />,
  '09 · ERSTER START: ONBOARDING-KARTE UND EINSTELLUNGEN': () => <Frame variante="start-einrichtung" />
}
