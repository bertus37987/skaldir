/**
 * App-Shell (Kategorie Rahmen & Navigation) — Nachbau der Original-App-Chrome.
 *
 * Quelle des Extrakts: designs/APP-EXTRAKTION/01-shell.md, gelesen aus
 * ~/.hermes/godsv/app-src/apps/desktop/src/. Alle Masse sind die exakt
 * gemessenen Werte der Original-App:
 *   Titelleiste 34 px · Control 24 px · Glyph 13.9 px · Inset 14 px
 *   Sidebar 237 px (max 360) · Dock-Grenze 640 px
 *   Sitzungszeile min-h 26 px, Karte 54 px, Label 13 px, Lead-Zelle 14 px
 *   Tab-Leiste h-28 px (im Titelband 34) · Tab 11 px text, min 52 / max 192 px
 *   Statusleiste 20 px hoher Footer, Item-Text 11 px
 *   Popout Composer 19.5 rem breit (~312 px), Höhe ab 56 px
 *
 * Hell/Dunkel-Umschalter im Kopf jeder Variante (useState). Bewegung: mit
 * Motion eingeblendet, Aufklappen mit Feder. Nur Schwarz/Weiss/Grau,
 * Symbole nur aus @tabler/icons-react.
 */
import { motion, AnimatePresence, animate } from 'motion/react'
import React, { useState } from 'react'
import { IconActivity, IconChevronRight, IconColumns2, IconCpu, IconDots, IconFiles, IconFolderOpen, IconGitBranch, IconGripVertical, IconHourglass, IconLayoutGrid, IconLayoutSidebar, IconLayoutSidebarLeftCollapse, IconLayoutSidebarLeftExpand, IconLayoutSidebarRight, IconMessage, IconMinimize, IconPlus, IconSearch, IconSettings, IconTerminal2, IconX } from '@tabler/icons-react'

import { LIGHT, DARK, MONO, SANS, SPRING, RADIUS, TEXT } from './_tokens'
import { useBarrierefrei } from './_barrierefrei'

/* Kopf der Buehne: Titel, Anmerkung, hell/dunkel-Umschalter (echter useState). */
function StageHead({ name, note, dark, setDark, P }) {
  return (
    <div style={{ marginBottom: 10 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <span style={{ fontFamily: MONO, fontSize: 12, fontWeight: 600, color: P.ink }}>{name}</span>
        <span style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 6, fontFamily: MONO, fontSize: TEXT.meta }}>
          <motion.span data-themelight whileTap={{ scale: 0.94 }} transition={SPRING}
            animate={{ borderColor: !dark ? P.ink : P.line, backgroundColor: !dark ? P.ink : P.surface, color: !dark ? P.bg : P.ink }}
            onClick={() => setDark(false)}
            style={{ padding: '3px 9px', border: `1px solid ${P.line}`, backgroundColor: P.surface, cursor: 'pointer' }}>hell</motion.span>
          <motion.span data-themedark whileTap={{ scale: 0.94 }} transition={SPRING}
            animate={{ borderColor: dark ? P.ink : P.line, backgroundColor: dark ? P.ink : P.surface, color: dark ? P.bg : P.ink }}
            onClick={() => setDark(true)}
            style={{ padding: '3px 9px', border: `1px solid ${P.line}`, backgroundColor: P.surface, cursor: 'pointer' }}>dunkel</motion.span>
        </span>
      </div>
      <div style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, marginTop: 3 }}>{note}</div>
    </div>
  )
}

function Stage({ name, note, children }) {
  useBarrierefrei()
  const [dark, setDark] = useState(false)
  const P = dark ? DARK : LIGHT
  return (
    <div data-stage style={{ padding: 20, background: P.bg, fontFamily: SANS, color: P.ink, width: '100%', maxWidth: 900, boxSizing: 'border-box' }}>
      <StageHead name={name} note={note} dark={dark} setDark={setDark} P={P} />
      {children(P)}
    </div>
  )
}

/* ------------------------------------------------- 01 · Titelleiste (34 px) */

const TitlebarTool = ({ Icon: I, P, badge, label }) => (
  <motion.span title={label} whileHover={{ backgroundColor: P.hair }} whileTap={{ scale: 0.9 }} transition={SPRING}
    style={{ width: 24, height: 24, borderRadius: RADIUS.micro, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer', color: P.sub, position: 'relative' }}>
    <I size={13.9} strokeWidth={1.7} color={P.sub} />
    {badge ? <span style={{ position: 'absolute', top: 1, right: 1, fontFamily: MONO, fontSize: TEXT.meta, background: P.ink, color: P.bg, borderRadius: RADIUS.pill, padding: '0 3px', lineHeight: '14px' }}>{badge}</span> : null}
  </motion.span>
)

export function V01() {
  return (
    <Stage name="01 · Titelleiste" note="34 px hoch · Knöpfe 24×24 · Icon 13.9 px · Rand-Inset 14 px · Trennlinie unten">
      {(P) => (
        <div data-frame style={{ height: 34, border: `1px solid ${P.line}`, background: P.surface, display: 'flex', alignItems: 'center', overflow: 'hidden', position: 'relative' }}>
          <div style={{ width: 14 }} />
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            <TitlebarToolEx Icon={IconLayoutGrid} P={P} label="Layout" />
            <TitlebarToolEx Icon={IconLayoutSidebar} P={P} label="Seitenleiste" />
            <TitlebarToolEx Icon={IconFiles} P={P} label="Datei-Baum" />
            <TitlebarToolEx Icon={IconColumns2} P={P} label="Spalten tauschen" />
          </span>
          <span style={{ width: 12 }} />
          <span style={{ minWidth: 0, flex: 1, overflow: 'hidden' }}>
            <span style={{ fontFamily: SANS, fontSize: 12, color: P.ink, display: 'block', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
              skaldir · ui-abnahme
            </span>
          </span>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 2, padding: '0 14px 0 12px' }}>
            <TitlebarToolEx Icon={IconSearch} P={P} label="Suchen" />
            <TitlebarToolEx Icon={IconSettings} P={P} label="Einstellungen" badge={3} />
          </span>
          <span style={{ position: 'absolute', left: 0, right: 0, top: '100%', height: 16, background: `linear-gradient(${P.surface}, rgba(0,0,0,0))`, pointerEvents: 'none' }} />
        </div>
      )}
    </Stage>
  )
}

/* Schrauben-Zeug oben weg — echte Werkzeug-Zelle mit Lucide-Symbol. */
function TitlebarToolEx({ Icon: I, P, label, badge }) {
  return (
    <motion.span title={label} whileHover={{ backgroundColor: P.hair }} whileTap={{ scale: 0.9 }} transition={SPRING}
      style={{ width: 24, height: 24, borderRadius: RADIUS.micro, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer', color: P.sub, position: 'relative' }}>
      <I size={13.9} strokeWidth={1.7} color={P.sub} />
      {badge ? <span style={{ position: 'absolute', top: 1, right: 1, fontFamily: MONO, fontSize: TEXT.meta, background: P.ink, color: P.bg, borderRadius: RADIUS.pill, padding: '0 3px', lineHeight: '14px' }}>{badge}</span> : null}
    </motion.span>
  )
}

/* ------------------------------------- 02 · Navigationsschiene (237 px Raster) */

export function V02() {
  return (
    <Stage name="02 · Navigationsschiene" note="237 px breit (max 360) · Dock-Grenze 640 px · Kante über Stichlinie">
      {(P) => (
        <div data-frame style={{ height: 360, border: `1px solid ${P.line}`, background: P.bg, display: 'flex', overflow: 'hidden' }}>
          <motion.nav data-rail initial={{ opacity: 0, x: -8 }} animate={{ opacity: 1, x: 0 }} transition={SPRING}
            style={{ width: 237, minWidth: 237, background: P.surface, borderRight: `1px solid ${P.line}`, display: 'flex', flexDirection: 'column' }}>
            <div style={{ padding: '10px 16px', borderBottom: `1px solid ${P.hair}`, display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ fontFamily: MONO, fontSize: TEXT.meta, letterSpacing: 1, color: P.sub }}>ARBEITSPLATZ</span>
              <span style={{ marginLeft: 'auto' }}><IconLayoutSidebarRight size={13} stroke={1.7} color={P.faint} /></span>
            </div>
            <div style={{ padding: '8px 8px', display: 'flex', flexDirection: 'column', gap: 2 }}>
              {[
                { Icon: IconMessage, label: 'Chat', hint: '⌘1', on: true },
                { Icon: IconFolderOpen, label: 'Projekte', hint: '⌘2', on: false },
                { Icon: IconFiles, label: 'Dateien', hint: '⌘3', on: false },
                { Icon: IconGitBranch, label: 'Review', hint: '⌘4', on: false },
                { Icon: IconTerminal2, label: 'Terminal', hint: '⌘J', on: false },
                { Icon: IconCpu, label: 'Cron-Jobs', hint: '', on: false }
              ].map((r, i) => (
                <motion.div key={r.label} initial={{ opacity: 0, x: -4 }} animate={{ opacity: 1, x: 0 }} transition={{ ...SPRING, delay: 0.04 + i * 0.04 }}
                  whileHover={{ backgroundColor: P.hair }} data-navrow={r.label} data-active={r.on}
                  style={{ minHeight: 26, display: 'flex', alignItems: 'center', gap: 10, padding: '0 8px', borderRadius: RADIUS.row, cursor: 'pointer', background: r.on ? P.hair : 'transparent' }}>
                  <span style={{ width: 14, display: 'inline-flex' }}><r.Icon size={14} strokeWidth={1.8} color={r.on ? P.ink : P.faint} /></span>
                  <span style={{ fontFamily: SANS, fontSize: 13, color: r.on ? P.ink : P.sub, fontWeight: r.on ? 500 : 400 }}>{r.label}</span>
                  {r.hint && <span style={{ marginLeft: 'auto', fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>{r.hint}</span>}
                </motion.div>
              ))}
            </div>
            <div style={{ marginTop: 'auto', padding: '8px 8px 10px', borderTop: `1px solid ${P.hair}`, display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ width: 14, height: 14, borderRadius: RADIUS.pill, background: P.ink }} />
              <span style={{ fontFamily: SANS, fontSize: 12, color: P.sub }}>Profil · sk</span>
              <span style={{ marginLeft: 'auto' }}><IconDots size={13} stroke={1.8} color={P.faint} /></span>
            </div>
          </motion.nav>
          <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint, textAlign: 'center' }}>
              Arbeitsfläche<br />Rail links andockt ab 640 px Fensterbreite
            </span>
          </div>
        </div>
      )}
    </Stage>
  )
}

/* ------------------------------------------- 03 · Sitzungsverlauf (26/54 px) */

const SESSIONS = [
  { title: 'Herme zwingen, Blogartikel zu scholarne', when: 'Heute', dot: 'läuft', model: 'opus-4.6', detail: 'git checkout -b ui-abnahme' },
  { title: 'React-Cosmos Werkbank aufsetzen', when: 'Heute', dot: 'lebt', model: 'glm-5.3', detail: 'fixtures/AppShell.fixture.tsx' },
  { title: 'Skaldir-Theme kalibrieren', when: 'Gestern', dot: '', model: 'opus-4.6', detail: 'designs/THEME.md' },
  { title: 'GatewaySwitcher flackt bei Profilwechsel', when: 'Gestern', dot: 'ungelesen', model: 'glm-5.3', detail: 'store/gateway-switch.ts' }
]

function SessionRow({ s, i, P, card }) {
  if (card) {
    return (
      <motion.div initial={{ opacity: 0, x: -5 }} animate={{ opacity: 1, x: 0 }} transition={{ ...SPRING, delay: i * 0.05 }}
        whileHover={{ backgroundColor: P.hair }} data-sessionrow={s.title} data-card
        style={{ minHeight: 54, borderTop: `1px solid ${P.hair}`, paddingLeft: 8, paddingRight: 8, paddingTop: 6, paddingBottom: 6, cursor: 'pointer' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontFamily: MONO, fontSize: TEXT.meta, letterSpacing: 0.5, color: P.faint }}>{s.when.toUpperCase()}</span>
          <span style={{ marginLeft: 'auto', fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>12.4k tok</span>
        </div>
        <div style={{ fontFamily: SANS, fontSize: 13, color: P.ink, fontWeight: 500, lineHeight: 1.35, marginTop: 2, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{s.title}</div>
        <div style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint, marginTop: 2, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{s.model} · {s.detail}</div>
      </motion.div>
    )
  }
  return (
    <motion.div initial={{ opacity: 0, x: -5 }} animate={{ opacity: 1, x: 0 }} transition={{ ...SPRING, delay: i * 0.05 }}
      whileHover={{ backgroundColor: P.hair }} data-sessionrow={s.title} data-card="false"
      style={{ minHeight: 26, display: 'flex', alignItems: 'stretch', paddingLeft: 8, paddingRight: 8, cursor: 'pointer' }}>
      <span style={{ display: 'flex', alignItems: 'center', width: 14, flex: '0 0 auto' }}>
        {s.dot === 'läuft' ? (
          <motion.span animate={{ opacity: [1, 0.3, 1], scale: [1, 0.8, 1] }} transition={{ duration: 1.6, repeat: Infinity, ease: 'easeInOut' }}
            style={{ width: 5, height: 5, borderRadius: RADIUS.pill, background: P.ink }} />
        ) : s.dot ? (
          <span style={{ width: 5, height: 5, borderRadius: RADIUS.pill, background: P.faint }} />
        ) : (
          <span style={{ width: 5, height: 5 }} />
        )}
      </span>
      <span style={{ display: 'flex', alignItems: 'center', minWidth: 0, flex: 1, gap: 6, paddingTop: 2, paddingBottom: 2 }}>
        <span style={{ fontFamily: SANS, fontSize: 13, color: P.sub, lineHeight: 1.35, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{s.title}</span>
        <span style={{ marginLeft: 'auto', fontFamily: MONO, fontSize: TEXT.meta, color: P.faint, flexShrink: 0 }}>{s.model}</span>
      </span>
    </motion.div>
  )
}

export function V03() {
  const [cards, setCards] = useState(false)
  return (
    <Stage name="03 · Sitzungsverlauf" note="Zeile min-h 26 px · Karte min-h 54 px · Label 13 px · führende Zelle 14 px — Klick auf die Kopfzeile schaltet Zeile/Karte">
      {(P) => (
        <div data-frame style={{ height: 360, border: `1px solid ${P.line}`, background: P.surface, display: 'flex', flexDirection: 'column' }}>
          <div onClick={() => setCards(c => !c)} style={{ padding: '8px 12px', borderBottom: `1px solid ${P.hair}`, display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer', background: P.surface }}>
            <motion.span animate={{ rotate: 90 }} transition={SPRING} style={{ display: 'inline-flex' }}>
              <IconChevronRight size={12} stroke={2} color={P.sub} />
            </motion.span>
            <span style={{ fontFamily: SANS, fontSize: 11, fontWeight: 600, color: P.ink }}>SITZUNGEN</span>
            <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>{SESSIONS.length}</span>
            <span style={{ marginLeft: 'auto', fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>{cards ? 'Karten' : 'Zeilen'}</span>
          </div>
          <div style={{ padding: '2px 0', overflow: 'hidden' }}>
            {SESSIONS.map((s, i) => <SessionRow key={s.title} s={s} i={i} P={P} card={cards} />)}
          </div>
          <div style={{ marginTop: 'auto', padding: '6px 10px', borderTop: `1px solid ${P.hair}`, display: 'flex', alignItems: 'center', gap: 6 }}>
            <IconSearch size={11} stroke={1.9} color={P.faint} />
            <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>Sitzungen durchsuchen …</span>
          </div>
        </div>
      )}
    </Stage>
  )
}

/* ------------------------------------------------ 04 · Sitzungskopf (Titel) */

export function V04() {
  return (
    <Stage name="04 · Sitzungskopf" note="steht in der 34-px-Leiste · Titel flex-truncate · Profiltag daneben">
      {(P) => (
        <div data-frame style={{ height: 34, border: `1px solid ${P.line}`, background: P.surface, display: 'flex', alignItems: 'center', gap: 12, padding: '0 14px' }}>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, flex: '0 0 auto' }}>
            <span style={{ width: 8, height: 8, borderRadius: RADIUS.pill, background: P.ink }} />
            <span style={{ fontFamily: MONO, fontSize: TEXT.meta, letterSpacing: 0.5, color: P.sub }}>PROFIL · SKALDIR</span>
          </span>
          <span data-sessiontitle style={{ minWidth: 0, flex: 1, overflow: 'hidden' }}>
            <span style={{ fontFamily: SANS, fontSize: 12.5, color: P.ink, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
              Schritt 2a · AppShell-Extraktion — Rahmen und Navigation nachbauen langes Titelfragment zum Abschneiden
            </span>
          </span>
          <span style={{ display: 'inline-flex', gap: 0, flex: '0 0 auto' }} />
        </div>
      )}
    </Stage>
  )
}

/* ------------------------------------- 05 · Arbeitsflächen-Spalten und Tabs */

const TABS = [
  { label: 'skaldir · ui-abnahme', active: true, closeable: false },
  { label: 'Terminal', active: false, closeable: true, Icon: IconTerminal2 },
  { label: 'Datei-Baum', active: false, closeable: true, Icon: IconFiles }
]

export function V05() {
  const [active, setActive] = useState(0)
  return (
    <Stage name="05 · Arbeitsfläche · Spalten und Tab-Leiste" note="Leiste h 28 px (im Titelband 34) · Tab-Text 11 px · min 52 / max 192 px · aktives Unterlinien-Ende 2 px">
      {(P) => (
        <div data-frame style={{ height: 300, border: `1px solid ${P.line}`, background: P.bg, display: 'flex', overflow: 'hidden' }}>
          <div style={{ width: 172, borderRight: `1px solid ${P.line}`, background: P.surface, display: 'flex', flexDirection: 'column' }}>
            <div style={{ height: 28, borderBottom: `1px solid ${P.line}`, display: 'flex', alignItems: 'center', padding: '0 10px', writingMode: 'horizontal-tb' }}>
              <span style={{ fontFamily: SANS, fontSize: 11, fontWeight: 500, color: P.sub, writingMode: 'horizontal-tb' }}>Datei-Baum</span>
            </div>
            <div style={{ flex: 1, padding: '6px 4px', fontFamily: SANS, fontSize: 11.5, color: P.sub, lineHeight: '20px' }}>
              <div style={{ paddingLeft: 4 }}>▾ projekt</div>
              <div style={{ paddingLeft: 18 }}>▾ tools</div>
              <div style={{ paddingLeft: 32, color: P.ink }}>ui-workbench</div>
              <div style={{ paddingLeft: 18 }}>▸ designs</div>
            </div>
          </div>
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
            <div data-tabstrip style={{ height: 28, background: P.surface, display: 'flex', alignItems: 'stretch', borderBottom: `1px solid ${P.line}` }}>
              {TABS.map((t, i) => (
                <motion.div key={t.label} layout onClick={() => setActive(i)} data-tab={t.label} data-active={active === i ? 'true' : 'false'}
                  whileTap={{ scale: 0.99 }} transition={SPRING}
                  style={{ minWidth: 52, maxWidth: 192, flex: '0 1 auto', display: 'flex', alignItems: 'center', gap: 5, padding: '0 8px', cursor: 'pointer', position: 'relative',
                    borderLeft: i > 0 ? `1px solid ${P.hair}` : 'none',
                    background: active === i ? P.bg : 'transparent', color: active === i ? P.ink : P.faint }}>
                  {t.Icon && <t.Icon size={11} strokeWidth={1.8} />}
                  <span style={{ fontFamily: SANS, fontSize: 11, fontWeight: 500, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{t.label}</span>
                  {active === i && <motion.span layoutId="tabunderline" style={{ position: 'absolute', left: 0, right: 0, bottom: 0, height: 2, background: P.ink }} />}
                </motion.div>
              ))}
              <span style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', padding: '0 8px' }}>
                <IconPlus size={12} stroke={1.9} color={P.faint} />
              </span>
            </div>
            <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>Zone · aktives Feld „{TABS[active].label}“</span>
            </div>
            <div style={{ height: 5, display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'row-resize', background: P.surface, borderTop: `1px solid ${P.hair}` }}>
              <IconGripVertical size={9} stroke={2} color={P.faint} style={{ transform: 'rotate(90deg)' }} />
            </div>
          </div>
        </div>
      )}
    </Stage>
  )
}

/* ----------------------------------------- 06 · Popout- und Andock-Zustände */

export function V06() {
  const [popped, setPopped] = useState(true)
  return (
    <Stage name="06 · Popout / Andock-Zustände" note="Popout 19.5 rem ≈ 312 px breit · Höhe ab 56 px · Andocken/abkoppeln mit Feder">
      {(P) => (
        <div data-frame style={{ height: 300, border: `1px solid ${P.line}`, background: P.bg, position: 'relative', overflow: 'hidden' }}>
          <div data-workspacezone style={{ position: 'absolute', left: 8, right: 8, top: 34, bottom: popped ? 66 : 8, borderRight: 'none', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>Arbeitsfläche · Sash unten · Rail links 237 px wenn angedockt</span>
          </div>
          <AnimatePresence initial={false} mode="wait">
            {popped ? (
              <motion.div key="pop" data-composerpopout initial={{ opacity: 0, scale: 0.94, x: 40 }} animate={{ opacity: 1, scale: 1, x: 0 }} exit={{ opacity: 0, scale: 0.94, x: 40 }}
                transition={SPRING}
                drag dragMomentum={false}
                style={{ position: 'absolute', right: 12, bottom: 48, width: 312, height: 56, borderRadius: RADIUS.module, border: `1px solid ${P.line}`, background: P.surface, boxShadow: '0 10px 30px rgba(0,0,0,0.18)', display: 'flex', flexDirection: 'column', overflow: 'hidden', cursor: 'grab' }}>
                <div style={{ height: 28, display: 'flex', alignItems: 'center', gap: 6, padding: '0 10px', borderBottom: `1px solid ${P.hair}` }}>
                  <span style={{ fontFamily: MONO, fontSize: TEXT.meta, letterSpacing: 1, color: P.sub }}>POPOUT · COMPOSER</span>
                  <span style={{ marginLeft: 'auto' }}><IconMinimize size={10} stroke={1.9} color={P.faint} /></span>
                </div>
                <div style={{ flex: 1, display: 'flex', alignItems: 'center', padding: '0 12px', fontFamily: SANS, fontSize: 12.5, color: P.faint }}>freie Nachricht …</div>
              </motion.div>
            ) : (
              <motion.div key="docked" data-composerdock initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 10 }} transition={SPRING}
                style={{ position: 'absolute', left: 8, right: 245, bottom: 8, height: 58, borderRadius: RADIUS.module, border: `1px solid ${P.line}`, background: P.surface, display: 'flex', alignItems: 'center', gap: 10, padding: '0 14px' }}>
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, flex: 1, minWidth: 0, fontFamily: SANS, fontSize: 12.5, color: P.faint }}>
                  <IconPlus size={12} stroke={1.9} color={P.faint} />
                  Start with a goal
                </span>
                <IconLayoutSidebarLeftExpand size={12} stroke={1.8} color={P.faint} />
              </motion.div>
            )}
          </AnimatePresence>
          <motion.span onClick={() => setPopped(p => !p)} whileTap={{ scale: 0.92 }} transition={SPRING} data-popouttoggle
            style={{ position: 'absolute', right: 12, bottom: 12, padding: '4px 10px', border: `1px solid ${P.ink}`, background: P.ink, color: P.bg, fontFamily: MONO, fontSize: TEXT.meta, cursor: 'pointer' }}>
            {popped ? 'andocken' : 'abkoppeln'}
          </motion.span>
          <div style={{ position: 'absolute', top: 10, left: 12, fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>
            fenstermodi: secondary · hud · watch (Query „win=“)
          </div>
        </div>
      )}
    </Stage>
  )
}

/* ------------------------------------------------- 07 · Statusleiste (20 px) */

const SB_LEFT = [
  { icon: IconActivity, label: 'Befehle', hint: '⌘K' },
  { icon: IconActivity, label: 'gw · lokal', hint: '' },
  { icon: IconSettings, label: 'skaldir', hint: 'Profil' }
]
const SB_RIGHT = [
  { icon: IconFolderOpen, label: '~/projects/gods-voice', hint: '' },
  { icon: IconColumns2, label: '3 Felder', hint: '' },
  { icon: IconGitBranch, label: 'ui-abnahme', hint: '' },
  { icon: IconHourglass, label: '14:02', hint: '' },
  { icon: IconCpu, label: '[████████░░] ~87%', hint: '' }
]

function CommandGlyph({ size = 11, color }) {
  return <IconSettings size={size} color={color} stroke={1.9} />
}

export function V07() {
  return (
    <Stage name="07 · Statusleiste" note="h 20 px · Item-Text 11 px · Farbe tertiär · Hover füllt die volle Höhe — rechts Zustands-Cluster">
      {(P) => (
        <div data-frame style={{ border: `1px solid ${P.line}`, background: P.surface }}>
          <footer data-statusbar style={{ height: 20, display: 'flex', alignItems: 'stretch', justifyContent: 'space-between', gap: 8, background: P.surface, padding: '0 4px' }}>
            <div style={{ display: 'flex', alignItems: 'stretch', gap: 2, minWidth: 0, overflow: 'hidden' }}>
              {SB_LEFT.map(it => (
                <motion.span key={it.label} whileHover={{ backgroundColor: P.hair }} transition={{ duration: 0.1 }}
                  style={{ display: 'inline-flex', alignItems: 'center', gap: 4, padding: '0 6px', fontFamily: MONO, fontSize: 11, color: P.faint, cursor: 'pointer', whiteSpace: 'nowrap' }}>
                  <it.icon size={11} color={P.faint} />
                  <span>{it.label}</span>
                  {it.hint && <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint, opacity: 0.7 }}>{it.hint}</span>}
                </motion.span>
              ))}
            </div>
            <div style={{ display: 'flex', alignItems: 'stretch', gap: 2, minWidth: 0, overflow: 'hidden' }}>
              {SB_RIGHT.map(it => (
                <motion.span key={it.label} whileHover={{ backgroundColor: P.hair }} transition={{ duration: 0.1 }}
                  style={{ display: 'inline-flex', alignItems: 'center', gap: 4, padding: '0 6px', fontFamily: MONO, fontSize: 11, color: P.faint, cursor: 'pointer', whiteSpace: 'nowrap' }}>
                  <it.icon size={11} color={P.faint} />
                  <span>{it.label}</span>
                </motion.span>
              ))}
            </div>
          </footer>
          <div style={{ padding: '8px 12px', fontFamily: MONO, fontSize: TEXT.meta, color: P.faint, borderTop: `1px solid ${P.hair}` }}>
            Rechtsklick: Sichtbarkeit je Eintrag · Layout-Reset · „Statusleiste verbergen“ (⌘⇧S)
          </div>
        </div>
      )}
    </Stage>
  )
}

/* --------------------------------------- 08 · Vollständige Shell (alles zusammen) */

export function V08() {
  return (
    <Stage name="08 · Vollständige Shell" note="Titelleiste 34 + Rails 237 + Tab-Leiste 28 + Statusleiste 20 — alles übereinander, wie im Fenster">
      {(P) => (
        <div data-frame style={{ height: 420, border: `1px solid ${P.line}`, background: P.bg, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
          <div style={{ height: 34, flex: '0 0 auto', background: P.surface, borderBottom: `1px solid ${P.line}`, display: 'flex', alignItems: 'center', gap: 10, padding: '0 14px' }}>
            <span style={{ display: 'inline-flex', gap: 6 }}>
              <IconLayoutSidebarLeftCollapse size={13.9} stroke={1.7} color={P.sub} />
              <IconLayoutGrid size={13.9} stroke={1.7} color={P.sub} />
            </span>
            <span style={{ fontFamily: SANS, fontSize: 12, color: P.ink, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>skaldir · ui-abnahme</span>
            <span style={{ marginLeft: 'auto' }}><IconSettings size={13.9} stroke={1.7} color={P.faint} /></span>
          </div>
          <div style={{ flex: 1, display: 'flex', minHeight: 0 }}>
            <div style={{ width: 196, flex: '0 0 auto', borderRight: `1px solid ${P.line}`, background: P.surface, overflow: 'hidden' }}>
              {SESSIONS.slice(0, 4).map((s, i) => <SessionRow key={s.title} s={s} i={i} P={P} card={false} />)}
            </div>
            <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column' }}>
              <div style={{ height: 28, flex: '0 0 auto', background: P.surface, display: 'flex', alignItems: 'stretch', borderBottom: `1px solid ${P.line}` }}>
                {TABS.slice(0, 2).map((t, i) => (
                  <span key={t.label} data-tab={t.label} data-active={activeProxy(i) === i}
                    style={{ minWidth: 52, maxWidth: 192, display: 'flex', alignItems: 'center', gap: 5, padding: '0 8px', borderLeft: i > 0 ? `1px solid ${P.hair}` : 'none', color: i === 0 ? P.ink : P.faint }}>
                    <span style={{ fontFamily: SANS, fontSize: 11, fontWeight: 500, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{t.label}</span>
                  </span>
                ))}
              </div>
              <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>Arbeitsfläche</span>
              </div>
            </div>
          </div>
          <footer style={{ height: 20, flex: '0 0 auto', display: 'flex', alignItems: 'stretch', justifyContent: 'space-between', background: P.surface, borderTop: `1px solid ${P.line}`, padding: '0 4px' }}>
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, padding: '0 6px', fontFamily: MONO, fontSize: 11, color: P.faint }}>⌘K Befehle</span>
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, padding: '0 6px', fontFamily: MONO, fontSize: 11, color: P.faint }}>
              <span>[████████░░] ~87%</span>
              <span>·</span>
              <span>14:02</span>
            </span>
          </footer>
        </div>
      )}
    </Stage>
  )
}

/* Hilfs-Proxy: V08 hat keine Auswahl — Feld 0 ist aktiv. */
function activeProxy(i) { return i === 0 ? 0 : -1 }

export default {
  '01 · Titelleiste (34 px, Werkknöpfe, Badge)': () => <V01 />,
  '02 · Navigationsschiene (237 px, Dock ab 640 px)': () => <V02 />,
  '03 · Sitzungsverlauf (Zeilen 26 px / Karten 54 px)': () => <V03 />,
  '04 · Sitzungskopf (Profil · Titel)': () => <V04 />,
  '05 · Arbeitsfläche: Spalten und Tab-Leiste': () => <V05 />,
  '06 · Popout / Andock-Zustände': () => <V06 />,
  '07 · Statusleiste (20 px, zwei Cluster)': () => <V07 />,
  '08 · Vollständige Shell (alles zusammen)': () => <V08 />
}
