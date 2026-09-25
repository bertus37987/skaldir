/**
 * AppPanels — Panels, Schubladen und Vorschauen (Kategorie D).
 *
 * Extrahiert aus der Original-App (app/overlays/panel.tsx, components/ui/sheet.tsx,
 * preview-artifact.tsx, diff-lines.tsx, log-view.tsx, empty-state.tsx, skeleton.tsx,
 * app/artifacts/index.tsx). Bericht: designs/APP-EXTRAKTION/04-panels.md.
 *
 * Nur Schwarz/Weiss/Grau, Symbole aus @tabler/icons-react, Bewegung ueber Motion.
 */
import { motion, AnimatePresence, animate } from 'motion/react'
import React, { useState } from 'react'
import { IconChevronLeft, IconChevronRight, IconCopy, IconDotsVertical, IconDownload, IconExternalLink, IconFileText, IconFileTypePng, IconFolderOpen, IconInbox, IconPlus, IconScript, IconX } from '@tabler/icons-react'

import { LIGHT, DARK, MONO, SANS, SPRING, RADIUS, TEXT } from './_tokens'
import { useBarrierefrei } from './_barrierefrei'

const DIFF_SPRING = { stiffness: 320, damping: 30, mass: 0.5 }

/* Buehne mit Kopfzeile: Titelzeile + Hinweis + Umschalter hell/dunkel. */
function StageHead({ name, note, dark, setDark, toggleable, P }) {
  return (
    <div style={{ marginBottom: 10 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <span style={{ fontFamily: MONO, fontSize: 12, fontWeight: 600 }}>{name}</span>
        {toggleable && (
          <span style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 6, fontFamily: MONO, fontSize: TEXT.meta }}>
            <motion.span data-themelight whileTap={{ scale: 0.94 }} transition={SPRING}
              animate={{ borderColor: !dark ? P.ink : P.line, backgroundColor: !dark ? P.ink : P.surface, color: !dark ? P.bg : P.ink }}
              onClick={() => setDark(false)}
              style={{ padding: '3px 9px', border: `1px solid ${P.line}`, cursor: 'pointer', backgroundColor: P.surface }}>hell</motion.span>
            <motion.span data-themedark whileTap={{ scale: 0.94 }} transition={SPRING}
              animate={{ borderColor: dark ? P.ink : P.line, backgroundColor: dark ? P.ink : P.surface, color: dark ? P.bg : P.ink }}
              onClick={() => setDark(true)}
              style={{ padding: '3px 9px', border: `1px solid ${P.line}`, cursor: 'pointer', backgroundColor: P.surface }}>dunkel</motion.span>
          </span>
        )}
      </div>
      <div style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, marginTop: 3 }}>{note}</div>
    </div>
  )
}

function Stage({ name, note, dark, setDark, toggleable, children }) {
  useBarrierefrei()
  const P = dark ? DARK : LIGHT
  return (
    <div data-stage style={{ padding: 20, background: P.bg, fontFamily: SANS, color: P.ink, width: '100%', maxWidth: 880, boxSizing: 'border-box' }}>
      <StageHead name={name} note={note} dark={dark} setDark={setDark} toggleable={toggleable} P={P} />
      {children(P)}
    </div>
  )
}

/* Variante mit echtem Them-Zustand. */
function Managed({ name, note, render, dark0 = false, toggleable = true }) {
  const [dark, setDark] = useState(dark0)
  return (
    <Stage name={name} note={note} dark={dark} setDark={setDark} toggleable={toggleable}>
      {(P) => render({ P, dark })}
    </Stage>
  )
}

/* Kopf einer Overlay-Karte: mb-3, Titel fett, subtitle mittig, Aktionen gap-1.5. */
const PanelHeaderEl = ({ title, subtitle, actions, P }) => (
  <header style={{ marginBottom: 12, display: 'flex', flexShrink: 0, alignItems: 'flex-start', justifyContent: 'space-between', gap: 12, paddingRight: 32 }}>
    <div style={{ minWidth: 0 }}>
      <h2 style={{ fontFamily: SANS, fontSize: 14, fontWeight: 600, color: P.ink, margin: 0 }}>{title}</h2>
      {subtitle ? <p style={{ fontFamily: SANS, fontSize: 12, color: P.sub, opacity: 0.8, margin: '2px 0 0', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{subtitle}</p> : null}
    </div>
    {actions ? <div style={{ flexShrink: 0, display: 'flex', alignItems: 'center', gap: 6 }}>{actions}</div> : null}
  </header>
)

/* Pill: rounded-full px-1.5 py-0.5 text-[0.62rem] — Toene grau uebersetzt. */
const Pill = ({ children, P, filled = false }) => (
  <span style={{ display: 'inline-flex', alignItems: 'center', borderRadius: RADIUS.pill, padding: '2px 6px',
    fontFamily: MONO, fontSize: TEXT.meta, fontWeight: 500,
    backgroundColor: filled ? P.ink : P.hair, color: filled ? P.bg : P.sub }}>{children}</span>
)

const IconBtn = ({ children, P, label, onClick }) => (
  <motion.span title={label} aria-label={label} onClick={onClick} whileTap={{ scale: 0.92 }} transition={SPRING}
    style={{ width: 20, height: 20, borderRadius: RADIUS.micro, border: `1px solid ${P.line}`, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer', color: P.sub, background: P.surface }}>
    {children}
  </motion.span>
)

/* ------------------------------------------------------------ 01 · Panel-Kopf */

export function V01() {
  return (
    <Managed name="01 · Panel-Kopf mit Aktionen" note="PanelHeader — Titel sm fett, subtitle xxs, Aktionen gap-1.5, pr-8 freihalten"
      render={({ P }) => (
        <div data-frame style={{ border: `1px solid ${P.line}`, background: P.surface, padding: '14px 16px' }}>
          <PanelHeaderEl P={P} title="Agenten" subtitle="3 laufende Uebertraegungen · foo, bar"
            actions={<React.Fragment>
              <Pill P={P}>muted</Pill>
              <Pill P={P} filled>aktiv</Pill>
              <IconBtn P={P} label="mehr"><IconDotsVertical size={12} stroke={1.9} /></IconBtn>
            </React.Fragment>} />
          <div style={{ borderTop: `1px solid ${P.hair}`, paddingTop: 10 }}>
            <span style={{ fontFamily: MONO, fontSize: TEXT.meta, letterSpacing: 1.2, color: P.faint, textTransform: 'uppercase' }}>Bereichs-Beschriftung</span>
            <div style={{ fontFamily: SANS, fontSize: 12.5, color: P.ink, marginTop: 4 }}>Nach der Beschriftung folgt der Inhalt; die Kopfzeile haelt diesre Abstand fuer die absolute Schliessen-Flaech.</div>
          </div>
        </div>
      )} />
  )
}

/* ------------------------------------------------------------ 02 · Panel mit Inhalt: Liste + Detail */

const ListRowEl = ({ title, meta, active, P, last = false }) => (
  <motion.div data-panelrow title active={active} initial={{ opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }} transition={SPRING}
    style={{ display: 'flex', alignItems: 'center', height: 28, borderRadius: RADIUS.row, paddingHorizontal: 0,
      backgroundColor: active ? P.hair : 'rgba(0,0,0,0)', borderBottom: last ? 'none' : `1px solid ${P.hair}` }}>
    <span style={{ width: 6, height: 6, borderRadius: RADIUS.pill, background: active ? P.ink : P.faint, margin: '0 8px 0 8px', flexShrink: 0 }} />
    <span style={{ flex: 1, minWidth: 0, fontFamily: SANS, fontSize: 12.5, fontWeight: 500, color: active ? P.ink : P.sub, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{title}</span>
    <span style={{ flexShrink: 0, fontFamily: MONO, fontSize: TEXT.meta, color: P.faint, paddingRight: 8 }}>{meta}</span>
    <span title="Aktionen" style={{ flexShrink: 0, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 20, height: 20, borderRadius: RADIUS.micro, cursor: 'pointer', color: P.faint, marginRight: 4 }}>
      <IconDotsVertical size={13} stroke={1.9} />
    </span>
  </motion.div>
)

const MetaRow = ({ label, value, P }) => (
  <React.Fragment>
    <dt style={{ margin: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: P.faint }}>{label}</dt>
    <dd style={{ margin: 0, minWidth: 0, overflowWrap: 'break-word', color: P.ink, opacity: 0.85 }}>{value}</dd>
  </React.Fragment>
)

export function V02() {
  return (
    <Managed name="02 · Panel mit Inhalt" note="PanelList (w-52 = 208px Rail) + PanelDetail — Master/Detail"
      render={({ P }) => (
        <div data-frame style={{ border: `1px solid ${P.line}`, background: P.surface, padding: '14px 16px', display: 'flex', gap: 20, minHeight: 260 }}>
          <div style={{ width: 208, flexShrink: 0, display: 'flex', flexDirection: 'column' }}>
            <span style={{ fontFamily: MONO, fontSize: TEXT.meta, letterSpacing: 1, color: P.faint, textTransform: 'uppercase', marginBottom: 4 }}>Sitzungen</span>
            <ListRowEl P={P} title="reise-planung" meta="2 m" active />
            <ListRowEl P={P} title="ui-rework" meta="14 m" />
            <ListRowEl P={P} title="fehlerdiagnose" meta="1 h" last />
            <motion.span whileTap={{ scale: 0.97 }} transition={SPRING} title="Hinzufuegen"
              style={{ height: 28, marginTop: 6, display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer', color: P.faint, border: `1px dashed ${P.line}` }}>
              <IconPlus size={13} stroke={2} />
            </motion.span>
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <PanelSectionLabel P={P}>SITZUNG</PanelSectionLabel>
            <dl style={{ display: 'grid', gridTemplateColumns: '5rem 1fr', columnGap: 8, rowGap: 4, fontSize: 11.2, fontFamily: SANS, margin: '6px 0 12px' }}>
              <MetaRow P={P} label="Loc" value="B: workspace/foo" />
              <MetaRow P={P} label="Modus" value="agent" />
              <MetaRow P={P} label="Tokens" value="12 480" />
            </dl>
            <pre style={{ margin: 0, maxHeight: 192, overflow: 'auto', whiteSpace: 'pre-wrap', overflowWrap: 'break-word', borderRadius: RADIUS.micro, backgroundColor: P.hair, padding: 10, fontFamily: MONO, fontSize: TEXT.meta, lineHeight: 1.6, color: P.ink, opacity: 0.8 }}>
              {'Erzeuge einen Zusammenzug der letzten drei Uebertr.\nKennzeichne offene Fragen und nenne naechste Schritte.'}
            </pre>
          </div>
        </div>
      )} />
  )
}

function PanelSectionLabel({ children, P }) {
  return <div style={{ fontFamily: MONO, fontSize: TEXT.meta, fontWeight: 500, letterSpacing: 1.2, textTransform: 'uppercase', color: P.faint }}>{children}</div>
}

/* ------------------------------------------------------------ 03 · Inspektions-Schublade */

export function V03() {
  const [open, setOpen] = useState(true)
  return (
    <Managed name="03 · Inspektions-Schublade" note="Sheet rechts: w-3/4 max-w-sm (384px), Overlay 22%, open 500ms"
      render={({ P }) => (
        <div data-frame style={{ height: 400, border: `1px solid ${P.line}`, background: P.bg, position: 'relative', overflow: 'hidden' }}>
          <div style={{ position: 'absolute', inset: 0, padding: 16 }}>
            <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>Arbeitsflaeche — Schubs-Knopf rechts oeffnet die Inspektion</span>
            <button onClick={() => setOpen(true)} style={{ position: 'absolute', right: 16, top: 14, padding: '5px 10px', border: `1px solid ${P.line}`, background: P.surface, fontFamily: MONO, fontSize: TEXT.meta, cursor: 'pointer', color: P.ink }}>Inspect öffnen</button>
          </div>
          <AnimatePresence>
            {open && (
              <motion.div key="overlay" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.2 }}
                onClick={() => setOpen(false)}
                style={{ position: 'absolute', inset: 0, backgroundColor: 'rgba(0,0,0,0.38)', backdropFilter: 'blur(0.125rem)' }} />
            )}
          </AnimatePresence>
          <AnimatePresence>
            {open && (
              <motion.aside key="sheet" data-sheet initial={{ x: 384 }} animate={{ x: 0 }} exit={{ x: 384 }}
                transition={{ type: 'spring', stiffness: 420, damping: 34, mass: 0.7 }}
                style={{ position: 'absolute', top: 0, right: 0, bottom: 0, width: 384, maxWidth: '75%', borderLeft: `1px solid ${P.line}`, background: P.surface, boxShadow: '0 0 20px rgba(0,0,0,0.14)', display: 'flex', flexDirection: 'column', gap: 8 }}>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 4, padding: 12 }}>
                  <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between' }}>
                    <span style={{ fontFamily: SANS, fontSize: 15, fontWeight: 600, color: P.ink }}>Inspektion: reise-planung</span>
                    <motion.span onClick={() => setOpen(false)} whileTap={{ scale: 0.9 }} transition={SPRING}
                      title="Schliessen" style={{ width: 22, height: 22, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer', color: P.faint, opacity: 0.7 }}>
                      <IconX size={14} stroke={2} />
                    </motion.span>
                  </div>
                  <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, opacity: 0.8 }}>Sitzung · 14:32 · 12 480 Tokens</span>
                </div>
                <div style={{ padding: '0 12px 16px', overflow: 'auto' }}>
                  <PanelSectionLabel P={P}>Eigenschaften</PanelSectionLabel>
                  <dl style={{ display: 'grid', gridTemplateColumns: '5rem 1fr', columnGap: 8, rowGap: 4, fontSize: 11.2, fontFamily: SANS, margin: '6px 0 12px' }}>
                    <MetaRow P={P} label="Pfad" value="workspace/plan.md" />
                    <MetaRow P={P} label="Modus" value="agent" />
                    <MetaRow P={P} label="Status" value="läuft" />
                  </dl>
                  <PanelSectionLabel P={P}>Eingabeblock</PanelSectionLabel>
                  <pre style={{ margin: '6px 0 0', maxHeight: 120, overflow: 'auto', whiteSpace: 'pre-wrap', overflowWrap: 'break-word', borderRadius: RADIUS.micro, backgroundColor: P.hair, padding: 10, fontFamily: MONO, fontSize: TEXT.meta, lineHeight: 1.6, color: P.ink, opacity: 0.8 }}>
                    {'mit den drei Spuren vergleichen\nund Abweichungen markieren'}
                  </pre>
                </div>
              </motion.aside>
            )}
          </AnimatePresence>
        </div>
      )} />
  )
}

/* ------------------------------------------------------------ 04 · Artefakt-Vorschau */

export function V04() {
  const [mode, setMode] = useState('rendered')
  const [ver, setVer] = useState(2)
  const total = 3
  return (
    <Managed name="04 · Artefakt-Vorschau" note="RENDERED/SOURCE + Versionsstepper + Aktionen (size-3 Knöpfe)"
      render={({ P }) => (
        <div data-frame style={{ border: `1px solid ${P.line}`, background: P.surface, display: 'flex', flexDirection: 'column', height: 340 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '4px 8px', borderBottom: `1px solid ${P.hair}` }}>
            <span style={{ display: 'inline-flex', border: `1px solid ${P.line}`, padding: 2, gap: 0 }}>
              {['rendered', 'source'].map(m => (
                <motion.span key={m} data-previewmode={m} onClick={() => setMode(m)} whileTap={{ scale: 0.94 }} transition={SPRING}
                  title={m} animate={{ backgroundColor: mode === m ? P.ink : 'rgba(0,0,0,0)', color: mode === m ? P.bg : P.sub }}
                  style={{ padding: '2px 8px', fontFamily: MONO, fontSize: TEXT.meta, fontWeight: 700, cursor: 'pointer', textTransform: 'uppercase' }}>{m}</motion.span>
              ))}
            </span>
            <span style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 4 }}>
              <motion.span disabled={ver === 0} onClick={() => ver > 0 && setVer(ver - 1)} whileTap={{ scale: 0.9 }} transition={SPRING}
                style={{ opacity: ver === 0 ? 0.35 : 1, cursor: 'pointer', display: 'inline-flex', color: P.faint, padding: '0 4px' }}><IconChevronLeft size={12} stroke={2.2} /></motion.span>
              <span style={{ fontFamily: MONO, fontSize: TEXT.meta, fontWeight: 700, color: P.sub, fontVariantNumeric: 'tabular-nums' }}>Versn.{ver + 1}/{total}</span>
              <motion.span disabled={ver === total - 1} onClick={() => ver < total - 1 && setVer(ver + 1)} whileTap={{ scale: 0.9 }} transition={SPRING}
                style={{ opacity: ver === total - 1 ? 0.35 : 1, cursor: 'pointer', display: 'inline-flex', color: P.faint, padding: '0 4px' }}><IconChevronRight size={12} stroke={2.2} /></motion.span>
              {ver < total - 1 && <span style={{ fontFamily: MONO, fontSize: TEXT.meta, fontWeight: 700, color: P.sub, textDecoration: 'underline' }}>neueste</span>}
              <span style={{ width: 1, height: 14, background: P.hair, margin: '0 4px' }} />
              <IconBtn P={P} label="Kopieren"><IconCopy size={12} stroke={1.8} /></IconBtn>
              <IconBtn P={P} label="Herunterladen"><IconDownload size={12} stroke={1.8} /></IconBtn>
              <IconBtn P={P} label="Im Browser öffnen"><IconExternalLink size={12} stroke={1.8} /></IconBtn>
            </span>
          </div>
          <div style={{ flex: 1, minHeight: 0, position: 'relative' }}>
            {mode === 'rendered' ? (
              <div style={{ position: 'absolute', inset: 0, display: 'grid', placeItems: 'center', overflow: 'auto', backgroundColor: LIGHT.surface, colorScheme: 'light' }}>
                <div style={{ padding: 20, fontFamily: SANS, color: LIGHT.ink }}>
                  <div style={{ fontFamily: SANS, fontSize: 14, fontWeight: 600, marginBottom: 6 }}>Live-Ansicht (HTML-iframe sandbox)</div>
                  <div style={{ fontFamily: MONO, fontSize: 11, opacity: 0.7 }}>auslaendisches HTML ; rendert absichtlich in beiden Themes hell.</div>
                </div>
              </div>
            ) : (
              <pre style={{ position: 'absolute', inset: 0, margin: 0, overflow: 'auto', padding: 10, fontFamily: MONO, fontSize: 11.2, lineHeight: 1.6, color: P.sub }}>
                {'<!doctype html>\n<html>\n  <head><title>plan</title></head>\n  <body>\n    <h1>Routenplan</h1>\n  </body>\n</html>'}
              </pre>
            )}
          </div>
        </div>
      )} />
  )
}

/* ------------------------------------------------------------ 05 · Diff-Ansicht */

const DIFF_KIND = {
  add: (P) => ({ borderLeft: `2px solid ${P.ink}`, backgroundColor: P.hair }),
  remove: (P) => ({ borderLeft: `2px solid ${P.faint}`, backgroundColor: P.hair, textDecoration: 'line-through' }),
  context: (P) => ({ borderLeft: '2px solid rgba(0,0,0,0)' })
}

function DiffCount({ added, removed, P }) {
  return (
    <span style={{ display: 'inline-flex', flexShrink: 0, alignItems: 'center', gap: 6, fontFamily: MONO, fontSize: 11, fontVariantNumeric: 'tabular-nums', color: P.ink }}>
      {added > 0 && <motion.span key={`a${added}`} initial={{ opacity: 0, y: -5 }} animate={{ opacity: 1, y: 0 }} transition={DIFF_SPRING}>+{added}</motion.span>}
      {removed > 0 && <motion.span key={`r${removed}`} initial={{ opacity: 0, y: 5 }} animate={{ opacity: 1, y: 0 }} transition={DIFF_SPRING}>−{removed}</motion.span>}
    </span>
  )
}

export function V05() {
  return (
    <Managed name="05 · Diff-Ansicht" note="kein +/- in der Rinne — nur Tint + 2px-Rand-Akzent, max-h-12rem"
      render={({ P }) => (
        <div data-frame style={{ border: `1px solid ${P.line}`, background: P.surface, overflow: 'hidden' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '5px 10px', borderBottom: `1px solid ${P.hair}` }}>
            <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.ink }}>workspace/plan.md</span>
            <span style={{ marginLeft: 'auto' }}><DiffCount P={P} added={6} removed={2} /></span>
          </div>
          <div style={{ margin: '0 0 -6px -6px', maxHeight: 192, overflow: 'auto', fontFamily: MONO, fontSize: 11.2, lineHeight: '1.7', color: P.sub }}>
            {[
              { k: 'context', t: '## Routenplan' },
              { k: 'remove', t: 'Start: München' },
              { k: 'add', t: 'Start: Freiburg' },
              { k: 'add', t: 'Via: Basel — Genf' },
              { k: 'context', t: 'Ziel: Lissabon' },
              { k: 'add', t: 'Eta-Wort: 5 Tage' },
              { k: 'remove', t: 'Eta-Wort: 3 Tage' },
              { k: 'context', t: 'Ende der Route' }
            ].map((l, i) => (
              <span key={`${l.k}-${l.t}`} data-diffl={l.k} style={{ display: 'block', minWidth: 'max-content', whiteSpace: 'pre', paddingLeft: 10, paddingRight: 4, ...DIFF_KIND[l.k](P) }}>{l.t}</span>
            ))}
          </div>
        </div>
      )} />
  )
}

/* ------------------------------------------------------------ 06 · Log-Flaeche */

export function V06() {
  return (
    <Managed name="06 · Log-Fläche" note="LogView: kein bg, Haar-Rand, mono 0.6875rem, 1.5-Zeilenabstand, selektierbar"
      render={({ P }) => (
        <div data-frame style={{ border: `1px solid ${P.line}`, background: P.surface, padding: 12 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 8 }}>
            <IconScript size={12} stroke={1.8} color={P.sub} />
            <span style={{ fontFamily: MONO, fontSize: TEXT.meta, letterSpacing: 1, color: P.sub, textTransform: 'uppercase' }}>Protokoll · gateway.log</span>
            <IconBtn P={P} label="Kopieren"><IconCopy size={11} stroke={1.8} /></IconBtn>
          </div>
          <div data-logview data-selectable-text="true" style={{ maxHeight: 170, overflow: 'auto', borderRadius: RADIUS.row, border: `1px solid ${P.hair}`, padding: '6px 10px', fontFamily: MONO, fontSize: 11, lineHeight: 1.5, whiteSpace: 'pre-wrap', overflowWrap: 'break-word', color: P.sub }}>
            {'12:00:01 gateway: verbinde profil default\n12:00:01 ws: socket auf\n12:00:02 tools: 12 aktiv\n12:00:02 orchestrator: 3 laufende uebertrag.\n12:00:07 session: reise-planung angehaengt\n12:00:09 artefakte: 4 indiziert\n12:00:11 warn: sichere Ladegrenze (1 ingest)\n12:00:11 session: fertig — dauer 3.1 s'}
          </div>
          <div style={{ marginTop: 8, fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>whitespace-pre-wrap · break-words · text-tertiary — Logs existieren zum Lesen und Kopieren</div>
        </div>
      )} />
  )
}

/* ------------------------------------------------------------ 07 · Leerer Panel-Zustand */

export function V07() {
  return (
    <Managed name="07 · Leerer Panel-Zustand" note="PanelEmpty: Icon 1.25rem /50, Titel sm fett, Desc xxs /70, Aktion"
      render={({ P }) => (
        <div data-frame style={{ border: `1px solid ${P.line}`, background: P.surface }}>
          <PanelHeaderEl P={P} title="Artefakte" subtitle="4. Sitzung — Kontext" />
          <div style={{ display: 'grid', placeItems: 'center', padding: '40px 24px', textAlign: 'center' }}>
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8 }}>
              <span style={{ color: P.faint, opacity: 0.5, display: 'inline-flex' }}><IconInbox size={20} stroke={1.6} /></span>
              <p style={{ margin: 0, fontFamily: SANS, fontSize: 14, fontWeight: 500, color: P.ink, opacity: 0.9 }}>Noch keine Artefakte</p>
              <p style={{ margin: 0, maxWidth: 384, fontFamily: SANS, fontSize: 12, lineHeight: 1.7, color: P.sub, opacity: 0.7 }}>Bilder, Dateien und Links erscheinen hier, sobald ein Übertrag sie erzeugt.</p>
              <div style={{ marginTop: 8 }}>
                <motion.span whileTap={{ scale: 0.94 }} transition={SPRING} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, padding: '5px 12px', border: `1px solid ${P.line}`, background: P.ink, color: P.bg, fontFamily: MONO, fontSize: TEXT.meta, cursor: 'pointer' }}>
                  Neustart Indizierung
                </motion.span>
              </div>
            </div>
          </div>
        </div>
      )} />
  )
}

/* ------------------------------------------------------------ 08 · Ladender Panel-Zustand */

function Skeleton({ w, h = 10, P, r = RADIUS.micro }) {
  return <motion.span animate={{ opacity: [0.45, 1, 0.45] }} transition={{ duration: 1.6, repeat: Infinity, ease: 'easeInOut' }}
    style={{ display: 'block', width: w, height: h, borderRadius: r, backgroundColor: P.hair }} />
}

export function V08() {
  return (
    <Managed name="08 · Ladender Panel-Zustand" note="Skeleton-Zeilen + CountSkeleton-Chip (h-2 w-3.5) + Loader-Flow"
      render={({ P }) => (
        <div data-frame style={{ border: `1px solid ${P.line}`, background: P.surface }}>
          <PanelHeaderEl P={P} title="Subagenten" subtitle="lädt…" actions={
            <React.Fragment>
              <span style={{ display: 'inline-block', height: 8, width: 14, transform: 'translateY(1px)', borderRadius: RADIUS.micro, backgroundColor: P.hair }} />
              <Pill P={P}>lädt</Pill>
            </React.Fragment>} />
          <div style={{ padding: '0 16px 16px', display: 'flex', flexDirection: 'column', gap: 10 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <Skeleton P={P} w={20} h={20} r={RADIUS.pill} />
              <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 6 }}>
                <Skeleton P={P} w="40%" />
                <Skeleton P={P} w="65%" h={8} />
              </div>
            </div>
            <Skeleton P={P} w="100%" h={44} r={RADIUS.row} />
            <Skeleton P={P} w="70%" h={44} r={RADIUS.row} />
            <div style={{ marginTop: 4, fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>page-loader mit Label: „indiziere …" — hier still dargestellt</div>
          </div>
        </div>
      )} />
  )
}

/* ------------------------------------------------------------ 09 · Bild-Vorschau-Karte */

export function V09() {
  const cards = [
    { name: 'route-01.png', kind: 'BILD', path: 'media/route-01.png', session: 'reise-planung · 14:32' },
    { name: 'diagram.pdf', kind: 'DATEI', path: 'media/diagram.pdf', session: 'ui-rework · 11:05' },
    { name: 'board.svg', kind: 'BILD', path: 'media/board.svg', session: 'ui-rework · 10:58' },
    { name: 'notes.md', kind: 'DATEI', path: 'notes.md', session: 'fehlerdiagnose · 09:41' }
  ]
  return (
    <Managed name="09 · Bild-Vorschau-Karte" note="ArtifactImageCard: h-40 Zone, Kind-Zeile uppercase, Raster 11rem"
      render={({ P }) => (
        <div data-frame style={{ border: `1px solid ${P.line}`, background: P.surface, padding: 12, display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(176px, 1fr))', gap: 8 }}>
          {cards.map((c, i) => (
            <motion.article key={c.name} data-artifactcard initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ ...SPRING, delay: i * 0.05 }}
              whileHover={{ y: -2 }}
              style={{ overflow: 'hidden', borderRadius: RADIUS.row, border: `1px solid ${P.line}`, background: P.surface }}>
              <div style={{ position: 'relative', height: 160, display: 'flex', alignItems: 'center', justifyContent: 'center', overflow: 'hidden', borderBottom: `1px solid ${P.hair}`, backgroundColor: P.hair, padding: 6 }}>
                {c.kind === 'BILD'
                  ? <FileImageIcon P={P} />
                  : <span style={{ display: 'inline-flex', color: P.faint, opacity: 0.6 }}><IconFileText size={44} stroke={1.2} /></span>}
              </div>
              <div style={{ padding: 8, display: 'flex', flexDirection: 'column', gap: 6 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 4, color: P.faint, textTransform: 'uppercase', letterSpacing: 1, fontFamily: MONO, fontSize: TEXT.meta }}>
                  <FileKindIcon kind={c.kind} P={P} />{c.kind}
                </div>
                <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontFamily: SANS, fontSize: 12.5, fontWeight: 500, color: P.ink }}>{c.name}</span>
                <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>{c.path}</span>
                <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>{c.session}</span>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, padding: '3px 8px', border: `1px solid ${P.line}`, fontFamily: MONO, fontSize: TEXT.meta, cursor: 'pointer', color: P.sub }}>
                    <FolderOpenIcon P={P} /> Chat öffnen
                  </span>
                </div>
              </div>
            </motion.article>
          ))}
        </div>
      )} />
  )
}

function FileImageIcon({ P }) { return <span style={{ display: 'inline-flex', color: P.faint }}><IconFileTypePng size={44} stroke={1.2} /></span> }
function FolderOpenIcon({ P }) { return <IconFolderOpen size={11} stroke={1.8} color={P.sub} /> }
function FileKindIcon({ kind, P }) { return kind === 'BILD' ? <IconFileTypePng size={11} stroke={1.8} color={P.faint} /> : <IconFileText size={11} stroke={1.8} color={P.faint} /> }

/* ------------------------------------------------------------ Exportregister */

export default {
  '01 · Panel-Kopf mit Aktionen': () => <V01 />,
  '02 · Panel mit Inhalt': () => <V02 />,
  '03 · Inspektions-Schublade': () => <V03 />,
  '04 · Artefakt-Vorschau': () => <V04 />,
  '05 · Diff-Ansicht': () => <V05 />,
  '06 · Log-Fläche': () => <V06 />,
  '07 · Leerer Panel-Zustand': () => <V07 />,
  '08 · Ladender Panel-Zustand': () => <V08 />,
  '09 · Bild-Vorschau-Karte': () => <V09 />
}

/* Exportregister-Ende — unten ohne weitere Elemente. */
