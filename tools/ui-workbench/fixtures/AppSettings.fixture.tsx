/**
 * App-Extraktion 05 — EINSTELLUNGEN, Dialoge, Formulare der Original-App.
 *
 * Nachbau der echten Bausteine aus dem App-Quellbaum (nur gelesen). Werte stammen aus
 * `designs/APP-EXTRAKTION/05-einstellungen.md` (dort mit Pfad:Zeile belegt):
 *   Dialog-Shell   rounded 16 px, max-w-lg 512 px / max-w-md 448 px, max-h 85vh, Body p-4 gap-3
 *   Overlay        rgba(0,0,0,0.22) + blur 2 px, Öffnen zoom 95 % + Einblenden
 *   Knopf          rounded 2.5 px, 12/16 px, gap 1.5, px-3 py-1.5 (≈29 px); Icon-Knöpfe 36/32/24/40
 *   Control        rounded 2.5 px, px-2.5 py-1.5 (≈29 px)
 *   Schalter       36×20 px (xs 28×16), voll gerundet, Knopf 16 px (xs 12 px)
 *   Segmente       Spur rounded 5 px p-0.5 gap-0.5; Knopf rounded 3 px px-2.5 py-0.5 11 px
 *   Marke          rounded 3 px px-1.5 py-0.5 10.4 px (bewusst keine Vollpille)
 *   Kbd-Kachel     h-22 min-w-22 px, rounded 4 px, 11 px (sm 18 px / 10 px)
 *   ListRow        zweispaltig, Steuerung rechts in Spur minmax(15rem, 22rem), py-3 gap-3
 * Kein Gold, nur Schwarz/Weiß/Grau. Symbole aus @tabler/icons-react (die App nutzt Tabler —
 * der Port mappt auf Lucide, so wie es der UI-Vertrag §3 Nr. 5 als Option beschreibt).
 *
 * Werkbank-Regeln (UI-BEFUNDE-2026-09-18): Palette, Schriften, Radien und Feder kommen
 * ausschließlich aus `./_tokens` (A2/A3 Kontrast · B1 nur Grau · B3 Schriftuntergrenze 11 ·
 * B4 eine Radius-Tabelle statt drei Systemen — die App-Radien 2,5/3/4/6/10/12/16/999 sind
 * jetzt RADIUS.micro/row/module/dialog/pill); `useBarrierefrei()` (A1/A5) liegt im Rahmen
 * `Stage` und in jedem Varianten-Wrapper; jeder Rahmen trägt `data-stage` + `data-themetoggle` (C2).
 */
import { motion, AnimatePresence, animate } from 'motion/react'
import React, { useState } from 'react'
import { IconAlertTriangle, IconCheck, IconChevronDown, IconChevronRight, IconDeviceDesktop, IconDeviceFloppy, IconDownload, IconExternalLink, IconKey, IconLoader2, IconMoon, IconPlus, IconRefresh, IconSearch, IconSun, IconTrash, IconUpload, IconX } from '@tabler/icons-react'

import { useBarrierefrei } from './_barrierefrei'
// Eine Palette für die ganze Werkbank (UI-BEFUNDE-2026-09-18: A2/A3 Kontrast, B1 nur Grau,
// B3 Schriftuntergrenze, B4 eine Radius-Tabelle). Lokale Kopien sind damit weg — auch die
// Radius-Konstante `R = 2,5` der App, die jetzt RADIUS.micro ist.
import { LIGHT, DARK, MONO, SANS, SPRING, RADIUS, TEXT } from './_tokens'

const Btn = ({ P, children, kind = 'default', size = 'default', i, icon: I }) => {
  const pad = size === 'sm' ? '4px 8px' : size === 'xs' ? '2px 6px' : '6px 12px'
  const bg = kind === 'primary' ? P.ink : kind === 'ghost' ? 'transparent' : P.surface
  const col = kind === 'primary' ? P.surface : P.ink
  return (
    <motion.span whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.97 }} transition={SPRING}
      style={{ display: 'inline-flex', alignItems: 'center', gap: 6, padding: pad, borderRadius: RADIUS.micro,
               background: bg, color: col, border: kind === 'default' ? `1px solid ${P.line}` : '1px solid transparent',
               fontFamily: SANS, fontSize: size === 'xs' ? 11 : 12, lineHeight: '16px', cursor: 'pointer', whiteSpace: 'nowrap' }}>
      {I ? <I size={13} strokeWidth={1.8} /> : null}{children}
    </motion.span>
  )
}
const IconBtn = ({ P, icon: I, size = 24, title }) => (
  <motion.span title={title} whileHover={{ backgroundColor: P.hair }} whileTap={{ scale: 0.92 }} transition={SPRING}
    style={{ width: size, height: size, borderRadius: RADIUS.micro, display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
             border: `1px solid ${P.line}`, cursor: 'pointer' }}>
    <I size={size > 30 ? 16 : 13} strokeWidth={1.8} color={P.ink} />
  </motion.span>
)
const Field = ({ P, value, password, width }) => (
  <span style={{ display: 'inline-flex', alignItems: 'center', width: width || 200, height: 29, padding: '0 10px',
                 border: `1px solid ${P.line}`, borderRadius: RADIUS.micro, background: P.surface, fontFamily: MONO, fontSize: 12, color: P.ink, overflow: 'hidden' }}>
    {password ? '••••••••••' : value}
  </span>
)
const Switch = ({ P, on, xs, onClick }) => (
  <motion.span data-switch onClick={onClick} layout transition={SPRING}
    style={{ width: xs ? 28 : 36, height: xs ? 16 : 20, borderRadius: RADIUS.pill, padding: 2, display: 'inline-flex',
             alignItems: 'center', justifyContent: on ? 'flex-end' : 'flex-start', cursor: 'pointer',
             background: on ? P.ink : P.hair, border: `1px solid ${on ? P.ink : P.line}` }}>
    <motion.span layout transition={SPRING} style={{ width: xs ? 12 : 16, height: xs ? 12 : 16, borderRadius: RADIUS.pill, background: on ? P.surface : P.faint }} />
  </motion.span>
)
const Kbd = ({ P, children, tone = 'default' }) => (
  <span style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', height: 22, minWidth: 22, padding: '0 6px',
                 borderRadius: RADIUS.micro, border: `1px solid ${tone === 'capturing' ? P.ink : P.line}`,
                 background: tone === 'capturing' ? P.ink : P.soft, color: tone === 'capturing' ? P.surface : P.ink,
                 fontFamily: MONO, fontSize: TEXT.meta }}>{children}</span>
)
const Badge = ({ P, children, solid }) => (
  <span style={{ display: 'inline-flex', alignItems: 'center', padding: '2px 6px', borderRadius: RADIUS.micro,
                 fontSize: TEXT.meta, fontFamily: SANS, background: solid ? P.ink : P.soft, color: solid ? P.surface : P.sub,
                 border: `1px solid ${solid ? P.ink : P.line}` }}>{children}</span>
)
const Segments = ({ P, items, value, onChange }) => (
  <span style={{ display: 'inline-flex', gap: 2, padding: 2, borderRadius: RADIUS.row, background: P.soft, border: `1px solid ${P.line}` }}>
    {items.map(it => (
      <motion.span key={it} onClick={() => onChange(it)} whileTap={{ scale: 0.96 }} transition={SPRING}
        style={{ display: 'inline-flex', alignItems: 'center', gap: 4, padding: '2px 10px', borderRadius: RADIUS.micro,
                 fontSize: TEXT.meta, fontFamily: SANS, cursor: 'pointer',
                 background: it === value ? P.surface : 'transparent', color: P.ink,
                 boxShadow: it === value ? '0 1px 2px rgba(0,0,0,0.10)' : 'none' }}>{it}</motion.span>
    ))}
  </span>
)
const ListRow = ({ P, label, desc, control, mono }) => (
  <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12, padding: '12px 0', borderBottom: `1px solid ${P.hair}` }}>
    <span style={{ flex: 1, minWidth: 0 }}>
      <span style={{ display: 'block', fontSize: 13, color: P.ink, fontWeight: 500 }}>{label}</span>
      {desc ? <span style={{ display: 'block', fontSize: 11.5, color: P.sub, marginTop: 2 }}>{desc}</span> : null}
      {mono ? <span style={{ display: 'block', fontFamily: MONO, fontSize: TEXT.meta, color: P.faint, marginTop: 2 }}>{mono}</span> : null}
    </span>
    <span style={{ minWidth: 240, maxWidth: 352, display: 'inline-flex', justifyContent: 'flex-end' }}>{control}</span>
  </div>
)
const Section = ({ P, title, children, meta }) => (
  <div>
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10, paddingTop: 8 }}>
      <span style={{ width: 16, height: 16, background: P.faint, opacity: 0.5, borderRadius: RADIUS.micro }} />
      <span style={{ fontSize: 13, fontWeight: 600, color: P.ink }}>{title}</span>
      {meta ? <span style={{ marginLeft: 'auto' }}>{meta}</span> : null}
    </div>
    {children}
  </div>
)
const WarnChip = ({ P, children }) => (
  <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 12px', border: `1px solid ${P.line}`,
                borderRadius: RADIUS.row, background: P.soft, fontSize: 12, color: P.ink }}>
    <IconAlertTriangle size={13} stroke={1.8} /><span style={{ flex: 1 }}>{children}</span>
  </div>
)
const Dialog = ({ P, w = 512, title, icon: I, children, footer, banner, open = true }) => (
  <AnimatePresence>
    {open && (
      <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.16 }}
        style={{ position: 'relative', borderRadius: RADIUS.dialog, background: P.surface, border: `1px solid ${P.line}`,
                 boxShadow: '0 18px 50px rgba(0,0,0,0.28)', width: w, overflow: 'hidden' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: 16, paddingBottom: 12 }}>
          {I ? <I size={16} strokeWidth={1.8} /> : null}
          <span style={{ fontSize: 15, fontWeight: 600, letterSpacing: -0.2, color: P.ink }}>{title}</span>
          <span style={{ marginLeft: 'auto' }}><IconBtn P={P} icon={IconX} title="Schliessen" /></span>
        </div>
        <div style={{ padding: '0 16px 16px', display: 'flex', flexDirection: 'column', gap: 12 }}>{children}</div>
        {footer ? <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, padding: '0 16px 16px' }}>{footer}</div> : null}
        {banner ? <div style={{ padding: '0 16px 10px', display: 'flex', justifyContent: 'center', fontSize: 11.5, color: P.sub }}>{banner}</div> : null}
      </motion.div>
    )}
  </AnimatePresence>
)

const Stage = ({ titel, note, children, dark, setDark, P, hoch }) => {
  useBarrierefrei()
  return (
    <div data-stage data-theme={dark ? 'dunkel' : 'hell'} className={dark ? 'dark' : undefined}
      style={{ padding: 20, background: dark ? DARK.bg : LIGHT.bg, fontFamily: SANS, color: dark ? DARK.ink : LIGHT.ink, width: '100%', maxWidth: 940, boxSizing: 'border-box' }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
        <span style={{ fontFamily: MONO, fontSize: 12, fontWeight: 600 }}>{titel}</span>
        {setDark ? (
          <motion.span data-themetoggle onClick={() => setDark(d => !d)} whileTap={{ scale: 0.94 }} transition={SPRING}
            style={{ marginLeft: 'auto', fontFamily: MONO, fontSize: TEXT.meta, border: `1px solid ${P.line}`, padding: '2px 8px', borderRadius: RADIUS.pill, cursor: 'pointer' }}>
            {dark ? 'hell → dunkel' : 'dunkel → hell'}
          </motion.span>
        ) : null}
      </div>
      <div style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, margin: '3px 0 12px' }}>{note}</div>
      <div style={{ minHeight: hoch || 0 }}>{children}</div>
    </div>
  )
}

function V01({ dark, setDark }) { const P = dark ? DARK : LIGHT
  const [tab, setTab] = useState('model')
  const tabs = ['model', 'chat', 'appearance', 'workspace', 'safety', 'memory', 'voice', 'advanced', 'providers', 'gateway', 'keybinds', 'keys', 'sessions', 'about']
  return (
    <Stage titel="01 · Einstellungs-Overlay mit Reitern" note="linke Nav-Spalte mit Unterpunkten · schwebende Suchpille über der Karte · Nav-Fuß mit drei Icon-Knöpfen" dark={dark} setDark={setDark} P={P}>
      <div style={{ display: 'flex', border: `1px solid ${P.line}`, borderRadius: RADIUS.dialog, overflow: 'hidden', background: P.surface, height: 320 }}>
        <div style={{ width: 190, borderRight: `1px solid ${P.line}`, padding: 10, display: 'flex', flexDirection: 'column' }}>
          {tabs.slice(0, 9).map(t => (
            <motion.span key={t} layout onClick={() => setTab(t)} transition={SPRING}
              style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '5px 8px', borderRadius: RADIUS.row, fontSize: 12, cursor: 'pointer',
                       background: t === tab ? P.soft : 'transparent', color: t === tab ? P.ink : P.sub, fontWeight: t === tab ? 600 : 400 }}>
              <span style={{ width: 12, height: 12, background: t === tab ? P.ink : P.faint, opacity: t === tab ? 1 : 0.45, borderRadius: RADIUS.micro }} />{t}
            </motion.span>
          ))}
          <div style={{ marginTop: 'auto', display: 'flex', gap: 6 }}>
            <IconBtn P={P} icon={IconDownload} title="Konfiguration exportieren" /><IconBtn P={P} icon={IconUpload} title="importieren" /><IconBtn P={P} icon={IconRefresh} title="Werkseinstellungen" />
          </div>
        </div>
        <div style={{ flex: 1, padding: 16 }}>
          <div style={{ display: 'inline-flex', alignItems: 'center', gap: 8, height: 26, padding: '0 10px', borderRadius: RADIUS.pill, border: `1px solid ${P.line}`, background: P.soft, marginTop: -26 }}>
            <IconSearch size={12} stroke={1.8} color={P.sub} /><span style={{ fontSize: 12, color: P.faint }}>Einstellungen durchsuchen</span><Kbd P={P}>⌘K</Kbd>
          </div>
          <div style={{ fontSize: 13, fontWeight: 600, marginTop: 10 }}>{tab}</div>
          <div style={{ fontSize: 11.5, color: P.sub, marginTop: 4 }}>Reiter „{tab}" · Abschnittsliste der App: model · chat · appearance · workspace · safety · browser · vault · memory · voice · advanced · notifications · billing · providers · gateway · keybinds · keys · sessions · about</div>
        </div>
      </div>
    </Stage>
  )
}
function V02({ dark, setDark }) { const P = dark ? DARK : LIGHT
  const [prov, setProv] = useState('opencode-go'); const [loading, setLoading] = useState(false)
  return (
    <Stage titel="02 · Modellverwaltung" note="Provider- und Modellauswahl · Reasoning-Standard · Inline-Schlüsselaktivierung · Warnchip" dark={dark} setDark={setDark} P={P}>
      <div style={{ border: `1px solid ${P.line}`, borderRadius: RADIUS.dialog, background: P.surface, padding: 16, maxWidth: 720 }}>
        <Section P={P} title="Hauptmodell" meta={<Badge P={P}>aktiv</Badge>}>
          <ListRow P={P} label="Anbieter" desc="Wird fuer alle Aufgaben genutzt, die nichts anderes sagen" control={<Field P={P} value={prov} width={220} />} />
          <ListRow P={P} label="Modell" control={<Field P={P} value="glm-5.3-flash" width={240} />} />
          <ListRow P={P} label="Standard-Reasoning" desc="gilt fuer die naechste Nachricht" control={<Segments P={P} items={['Aus', 'Low', 'Med', 'High']} value="Med" onChange={() => {}} />} />
          <ListRow P={P} label="Schnelle Spur" desc="kurze Antworten ohne Nachdenken" control={<Switch P={P} on xs onClick={() => {}} />} />
          <ListRow P={P} label="Schlüssel für opencode-go" desc="noch nicht hinterlegt" mono="wird nur lokal gespeichert" control={
            <>
              <Field P={P} password width={200} />
              <motion.span onClick={() => { setLoading(true); setTimeout(() => setLoading(false), 1600) }} whileTap={{ scale: 0.96 }} transition={SPRING}
                style={{ marginLeft: 8 }}><Btn P={P} kind="primary" size="sm" i={loading ? IconLoader2 : undefined}>Aktivieren</Btn></motion.span>
            </>
          } />
        </Section>
        <div style={{ marginTop: 12 }}><WarnChip P={P}>Zwei Nebenslots zeigen auf ein altes Modell. <span style={{ textDecoration: 'underline' }}>Alles auf Haupt zurücksetzen</span></WarnChip></div>
      </div>
    </Stage>
  )
}
function V03({ dark, setDark }) { const P = dark ? DARK : LIGHT
  const [open, setOpen] = useState(true); const [edit, setEdit] = useState(false)
  return (
    <Stage titel="03 · Schlüssel maskiert" note="aufklappbare Karte · gesetzter Schlüssel als •••••••• · Klick startet den Editiermodus" dark={dark} setDark={setDark} P={P}>
      <div style={{ border: `1px solid ${P.line}`, borderRadius: RADIUS.row, background: P.surface, padding: 12, maxWidth: 620 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, height: 32 }}>
          <span style={{ width: 8, height: 8, borderRadius: RADIUS.pill, background: P.ink }} />
          <span style={{ fontSize: 12.5, fontWeight: 600 }}>ANTHROPIC_API_KEY</span>
          <span style={{ marginLeft: 'auto' }}><IconChevronDown size={14} stroke={1.8} color={P.sub} /></span>
        </div>
        <AnimatePresence initial={false}>
          {open && (
            <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }} transition={SPRING} style={{ overflow: 'hidden' }}>
              <div style={{ fontSize: 11.5, color: P.sub, margin: '6px 0 8px' }}>
                Damit laufen die Anthropic-Modelle. <span style={{ textDecoration: 'underline', display: 'inline-flex', alignItems: 'center', gap: 4 }}>Schlüssel holen <IconExternalLink size={11} stroke={1.8} /></span>
              </div>
              {!edit ? (
                <motion.span onClick={() => setEdit(true)} whileHover={{ borderColor: P.ink }} transition={SPRING}
                  style={{ display: 'inline-flex', alignItems: 'center', height: 32, width: 300, padding: '0 10px', border: `1px dashed ${P.line}`, borderRadius: RADIUS.micro, fontFamily: MONO, fontSize: 12, color: P.faint, cursor: 'pointer' }}>
                  ••••••••••
                </motion.span>
              ) : (
                <motion.div initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} transition={SPRING} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <Field P={P} value="sk-ant-…-neu" width={260} />
                  <Btn P={P} kind="ghost" size="xs"><IconTrash size={12} stroke={1.8} /></Btn>
                  <Btn P={P} kind="primary" size="sm" i={IconDeviceFloppy}>Speichern</Btn>
                  <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub }}>Enter speichert · Esc bricht ab</span>
                </motion.div>
              )}
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </Stage>
  )
}
function V04({ dark, setDark }) { const P = dark ? DARK : LIGHT
  const [mode, setMode] = useState('dunkel'); const [scale, setScale] = useState('100 %')
  return (
    <Stage titel="04 · Darstellung und Theme" note="Helligkeit als Segmente · UI-Skalierung · Transluzenz-Regler · Theme-Miniatur" dark={dark} setDark={setDark} P={P}>
      <div style={{ border: `1px solid ${P.line}`, borderRadius: RADIUS.dialog, background: P.surface, padding: 16, maxWidth: 720 }}>
        <Section P={P} title="Darstellung">
          <ListRow P={P} label="Helligkeit" control={<Segments P={P} items={['hell', 'dunkel', 'system']} value={mode} onChange={setMode} />} />
          <ListRow P={P} label="UI-Skalierung" desc="Standard 90 %" control={<Segments P={P} items={['90 %', '100 %', '125 %', '150 %']} value={scale} onChange={setScale} />} />
          <ListRow P={P} label="Transluzenz" control={<span style={{ display: 'inline-flex', alignItems: 'center', gap: 10 }}>
            <span style={{ width: 160, height: 4, borderRadius: RADIUS.pill, background: P.hair, display: 'inline-block', position: 'relative' }}>
              <motion.span layout style={{ position: 'absolute', left: '62%', top: -3, width: 10, height: 10, borderRadius: RADIUS.pill, background: P.ink }} />
            </span><span style={{ fontFamily: MONO, fontSize: TEXT.meta, width: 34, textAlign: 'right' }}>62 %</span></span>} />
        </Section>
        <div style={{ display: 'flex', gap: 12, marginTop: 12 }}>
          {['Skaldir Hell', 'Skaldir Dunkel'].map((n, i) => (
            <div key={n} style={{ border: `1px solid ${P.line}`, borderRadius: RADIUS.module, overflow: 'hidden', width: 200 }}>
              <div style={{ height: 80, background: i ? DARK.bg : LIGHT.bg, position: 'relative' }}>
                <span style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: 48, borderRight: `1px solid ${LIGHT.hair}` }} />
                <span style={{ position: 'absolute', left: 60, top: 18, width: 64, height: 10, borderRadius: RADIUS.pill, background: i ? DARK.hair : LIGHT.hair }} />
                <span style={{ position: 'absolute', left: 60, top: 34, width: 96, height: 8, borderRadius: RADIUS.pill, background: i ? DARK.soft : LIGHT.soft }} />
                <span style={{ position: 'absolute', right: 12, bottom: 10, width: 64, height: 18, borderRadius: RADIUS.pill, border: `1px solid ${i ? DARK.hair : LIGHT.hair}` }} />
              </div>
              <div style={{ padding: '8px 10px', borderTop: `1px solid ${P.hair}` }}>
                <div style={{ fontSize: 12, fontWeight: 500 }}>{n}</div>
                <div style={{ fontSize: TEXT.meta, color: P.sub }}>installiert · zuletzt benutzt</div>
              </div>
            </div>
          ))}
          <div style={{ border: `1px dashed ${P.line}`, borderRadius: RADIUS.module, width: 200, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6, fontSize: 11.5, color: P.sub, cursor: 'pointer' }}>
            <IconPlus size={13} stroke={1.8} />Theme suchen
          </div>
        </div>
      </div>
    </Stage>
  )
}
function V05({ dark, setDark }) { const P = dark ? DARK : LIGHT
  const [on, setOn] = useState(true); const tools = [['read_file', true], ['write_file', true], ['run_terminal', false], ['browser_open', true]]
  return (
    <Stage titel="05 · Werkzeuge und MCP" note="Server-Zeile mit Statuspunkt und Schalter · Werkzeug-Chips, abgeschaltete durchgestrichen" dark={dark} setDark={setDark} P={P}>
      <div style={{ border: `1px solid ${P.line}`, borderRadius: RADIUS.dialog, background: P.surface, maxWidth: 720 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, height: 44, padding: '0 6px 0 8px', borderBottom: `1px solid ${P.hair}` }}>
          <span style={{ width: 24, height: 24, borderRadius: RADIUS.row, background: P.soft, border: `1px solid ${P.line}`, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontFamily: MONO, fontSize: TEXT.meta, position: 'relative' }}>
            G<span style={{ position: 'absolute', right: -2, bottom: -2, width: 8, height: 8, borderRadius: RADIUS.pill, background: P.ink, border: `2px solid ${P.surface}` }} />
          </span>
          <span style={{ flex: 1, minWidth: 0 }}>
            <span style={{ display: 'block', fontSize: 12.5, fontWeight: 500 }}>godsv-tools</span>
            <span style={{ display: 'block', fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>verbunden · 18 Werkzeuge · stdio</span>
          </span>
          {!on ? <Badge P={P}>ungenutzt</Badge> : null}
          <IconBtn P={P} icon={IconRefresh} title="neu laden" /><IconBtn P={P} icon={IconTrash} title="entfernen" />
          <Switch P={P} on={on} xs onClick={() => setOn(v => !v)} />
        </div>
        <div style={{ padding: 12, display: 'flex', flexWrap: 'wrap', gap: 6 }}>
          {tools.map(([t, aktiv]) => (
            <motion.span key={t} whileHover={{ y: -1 }} whileTap={{ scale: 0.96 }} transition={SPRING}
              style={{ borderRadius: RADIUS.row, padding: '2px 6px', fontFamily: MONO, fontSize: TEXT.meta, border: `1px solid ${P.line}`,
                       background: aktiv ? P.soft : 'transparent', color: aktiv ? P.ink : P.faint,
                       textDecoration: aktiv ? 'none' : 'line-through', opacity: aktiv ? 1 : 0.7, cursor: 'pointer' }}>{t}</motion.span>
          ))}
        </div>
      </div>
    </Stage>
  )
}
function V06({ dark, setDark }) { const P = dark ? DARK : LIGHT
  const [cap, setCap] = useState(false)
  return (
    <Stage titel="06 · Tastenkürzel" note="Kategorie-Kopf · Zeile mit Kürzel-Kacheln · Capturing-Zustand · Konfliktwarnung" dark={dark} setDark={setDark} P={P}>
      <div style={{ border: `1px solid ${P.line}`, borderRadius: RADIUS.dialog, background: P.surface, padding: 14, maxWidth: 640 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontSize: 13, fontWeight: 600 }}>Tastenkürzel</span>
          <span style={{ marginLeft: 'auto' }}><Btn P={P} kind="ghost" size="xs" i={IconRefresh}>Alle zurücksetzen</Btn></span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, height: 28, borderBottom: `1px solid ${P.line}`, margin: '8px 0 10px' }}>
          <IconSearch size={12} stroke={1.8} color={P.sub} /><span style={{ fontSize: 11.5, color: P.faint }}>Kürzel suchen</span>
        </div>
        <div style={{ fontFamily: MONO, fontSize: TEXT.meta, fontWeight: 600, letterSpacing: 1.2, color: P.faint, padding: '8px 0 4px' }}>CHAT</div>
        {[['Neue Sitzung', ['⌘', 'N'], true], ['Senden', ['⏎'], false], ['Palette öffnen', ['⌘', 'K'], false]].map(([label, keys, konflikt]) => (
          <div key={label} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '4px 0' }}>
            <span style={{ flex: 1, fontSize: 12.5 }}>{label}</span>
            {konflikt ? <IconAlertTriangle size={13} stroke={1.8} color={P.sub} /> : null}
            <span style={{ display: 'inline-flex', gap: 4 }}>
              {cap && label === 'Neue Sitzung' ? <Kbd P={P} tone="capturing">Taste drücken…</Kbd> : keys.map(k => <Kbd P={P} key={k}>{k}</Kbd>)}
            </span>
          </div>
        ))}
        <div style={{ marginTop: 8, display: 'flex', gap: 8 }}>
          <Btn P={P} size="sm" onClick={undefined}><span onClick={() => setCap(c => !c)}>Aufnahme starten</span></Btn>
          <span style={{ fontSize: TEXT.meta, color: P.sub, alignSelf: 'center' }}>Konflikt mit „Neue Sitzung" — anderes Kürzel wählen</span>
        </div>
      </div>
    </Stage>
  )
}
function V07({ dark, setDark }) { const P = dark ? DARK : LIGHT
  const [zustand, setZustand] = useState('idle')
  return (
    <Stage titel="07 · Bestätigungsdialog" note="448 px · Fehlerband im Fuß · Zustand im Knopf idle → speichert → fertig" dark={dark} setDark={setDark} P={P}>
      <Dialog P={P} w={448} title="Werkseinstellungen zurücksetzen" icon={IconAlertTriangle}
        banner="Nach 600 ms schließt sich der Dialog selbst."
        footer={<>
          <Btn P={P} kind="ghost" size="sm">Abbrechen</Btn>
          <Btn P={P} size="sm">Sicherung behalten</Btn>
          <motion.span onClick={() => { setZustand('saving'); setTimeout(() => setZustand('done'), 1400) }} whileTap={{ scale: 0.97 }} transition={SPRING}>
            <Btn P={P} kind="primary" size="sm" i={zustand === 'saving' ? IconLoader2 : zustand === 'done' ? IconCheck : undefined}>
              {zustand === 'idle' ? 'Zurücksetzen' : zustand === 'saving' ? 'Setzt zurück…' : 'Fertig'}
            </Btn>
          </motion.span>
        </>}>
        <div style={{ fontSize: 11.5, color: P.sub, lineHeight: 1.6 }}>
          Alle Einstellungen, Schlüssel und Verläufe werden auf den Auslieferungszustand gesetzt.
          Eigene Themen und Tastenkürzel gehen verloren. Dieser Schritt lässt sich nicht rückgängig machen.
        </div>
        <WarnChip P={P}>Betroffen: 14 Einstellungen · 3 Schlüssel · 22 Sitzungen</WarnChip>
      </Dialog>
    </Stage>
  )
}
function V08({ dark, setDark }) { const P = dark ? DARK : LIGHT
  const [art, setArt] = useState('Anmeldung')
  return (
    <Stage titel="08 · Formular-Dialog" note="Art-Auswahl baut das Formular um · Pflichtfelder · Spinner im Knopf statt Größenwechsel" dark={dark} setDark={setDark} P={P}>
      <Dialog P={P} w={512} title="Eintrag hinzufügen" icon={IconKey}
        footer={<><Btn P={P} kind="ghost" size="sm">Abbrechen</Btn><Btn P={P} kind="primary" size="sm" i={IconPlus}>Hinzufügen</Btn></>}>
        <ListRow P={P} label="Art" control={<Segments P={P} items={['Anmeldung', 'Zahlung', 'Anschrift']} value={art} onChange={setArt} />} />
        {art === 'Anmeldung' ? (
          <>
            <ListRow P={P} label="Kennungstyp" control={<Segments P={P} items={['E-Mail', 'Telefon', 'Benutzer']} value="E-Mail" onChange={() => {}} />} />
            <ListRow P={P} label="Kennung" control={<Field P={P} value="willy@example.org" width={240} />} />
            <ListRow P={P} label="Passwort" control={<Field P={P} password width={240} />} />
          </>
        ) : (
          <>
            <ListRow P={P} label="Kartennummer" control={<Field P={P} value="•••• •••• •••• 4242" width={240} />} />
            <ListRow P={P} label="Gültig bis" control={<span style={{ display: 'inline-flex', gap: 6 }}><Field P={P} value="09" width={44} /><Field P={P} value="2029" width={64} /></span>} />
            <ListRow P={P} label="Prüfziffer" control={<Field P={P} value="•••" width={64} />} />
          </>
        )}
        <div style={{ fontSize: TEXT.meta, color: P.sub }}>Felder ohne Stern sind optional · Klartext wird nach dem Speichern sofort verworfen.</div>
      </Dialog>
    </Stage>
  )
}
function V09({ dark, setDark }) { const P = dark ? DARK : LIGHT
  const [a, setA] = useState(true); const [b, setB] = useState(false); const [c, setC] = useState(true)
  return (
    <Stage titel="09 · Baukasten (Knöpfe, Felder, Schalter, Marken)" note="die Bausteine, aus denen alle Dialoge der App bestehen — exakte Maße" dark={dark} setDark={setDark} P={P}>
      <div style={{ border: `1px solid ${P.line}`, borderRadius: RADIUS.dialog, background: P.surface, padding: 16, display: 'flex', flexDirection: 'column', gap: 16, maxWidth: 720 }}>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          <Btn P={P} kind="primary" i={IconCheck}>Primär</Btn><Btn P={P}>Standard</Btn><Btn P={P} kind="ghost">Still</Btn>
          <Btn P={P} size="sm">klein</Btn><Btn P={P} size="xs">sehr klein</Btn>
          <IconBtn P={P} icon={IconX} title="24" /><IconBtn P={P} icon={IconX} size={32} title="32" /><IconBtn P={P} icon={IconX} size={36} title="36" /><IconBtn P={P} icon={IconX} size={40} title="40" />
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <Field P={P} value="Eingabe" width={160} /><Field P={P} value="gross" width={200} /><Field P={P} password width={140} />
        </div>
        <div style={{ display: 'flex', gap: 16, alignItems: 'center' }}>
          <Switch P={P} on={a} onClick={() => setA(v => !v)} /><Switch P={P} on={b} onClick={() => setB(v => !v)} />
          <Switch P={P} on={c} xs onClick={() => setC(v => !v)} /><span style={{ fontSize: TEXT.meta, color: P.sub }}>36×20 und 28×16</span>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <Badge P={P} solid>v1.4.0</Badge><Badge P={P}>Entwurf</Badge><Badge P={P}>3 offen</Badge>
          <Kbd P={P}>⌘</Kbd><Kbd P={P}>⇧</Kbd><Kbd P={P}>K</Kbd><Kbd P={P} tone="capturing">Taste drücken…</Kbd>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 11.5, color: P.sub }}><IconCheck size={13} stroke={1.8} />Häkchen 16 px</span>
        </div>
      </div>
    </Stage>
  )
}
const wrap = (C) => () => {
  useBarrierefrei()
  const [dark, setDark] = useState(false)
  return <C dark={dark} setDark={setDark} />
}
export default {
  '01 · Einstellungs-Overlay mit Reitern (Split-Layout)': wrap(V01),
  '02 · Modellverwaltung: Anbieter, Reasoning, Inline-Schlüssel, Warnchip': wrap(V02),
  '03 · Schlüssel maskiert: aufklappbare Karte, Editiermodus': wrap(V03),
  '04 · Darstellung und Theme: Helligkeit, Skalierung, Miniaturen': wrap(V04),
  '05 · Werkzeuge und MCP: Server-Zeile, Tool-Chips, Schalter': wrap(V05),
  '06 · Tastenkürzel: Kacheln, Aufnahme, Konfliktwarnung': wrap(V06),
  '07 · Bestätigungsdialog: Fehlerband, Zustand im Knopf': wrap(V07),
  '08 · Formular-Dialog: Art wechselt das Formular': wrap(V08),
  '09 · Baukasten: Knöpfe, Felder, Schalter, Marken, Kacheln': wrap(V09)
}
