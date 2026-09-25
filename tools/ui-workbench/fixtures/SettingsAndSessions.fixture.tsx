/**
 * Kategorie H — EINSTELLUNGEN, SCHLÜSSEL UND SITZUNGEN — 10 Varianten.
 *
 * Alles Zustandshafte ist echt (useState): Schalter schalten wirklich, Umbenennen
 * tippt in ein echtes Eingabefeld, die Lösch-Rückfrage erscheint wirklich. Beim
 * Einhängen animiert alles mit Motion (Feder), Auf- und Zuklappen laufen mit Feder.
 * Nur Schwarz/Weiß/Grau, Symbole nur aus @tabler/icons-react.
 */
import { motion, AnimatePresence, animate } from 'motion/react'
import React, { useState } from 'react'
import { IconChevronLeft, IconChevronRight, IconLayoutSidebarLeftCollapse, IconLayoutSidebarLeftExpand, IconPencil, IconCopy, IconTrash, IconExternalLink, IconCheck, IconX, IconKey, IconPlus, IconEye, IconEyeOff } from '@tabler/icons-react'

/* Farben, Schrift, Radien, Federn: eine Quelle fuer alle Fixtures (_tokens.ts). */
import { LIGHT, DARK, MONO, SANS, SPRING, RADIUS, TEXT } from './_tokens'
import { useBarrierefrei } from './_barrierefrei'

/* ------------------------------------------------gemeinsame Bausteine */

/** Rahmen mit Titelzeile und hell/dunkel-Umschalter im Kopf (Variante 1–8). */
function Stage({ num, title, note, children, fixedDark }) {
  useBarrierefrei()   // A1 reduzierte Bewegung + A5 Tastatur — erste Zeile jeder Buehne
  const [dark, setDark] = useState(!!fixedDark)
  const P = dark ? DARK : LIGHT
  return (
    <div data-stage style={{ padding: 20, background: P.bg, fontFamily: SANS, color: P.ink, width: '100%', maxWidth: 920, boxSizing: 'border-box' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <span style={{ fontFamily: MONO, fontSize: 12, fontWeight: 600 }}>{num} · {title}</span>
        {fixedDark ? (
          <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>dunkle Fassung</span>
        ) : (
          <motion.span onClick={() => setDark(d => !d)} data-themetoggle title="hell/dunkel umschalten"
            whileHover={{ scale: 1.04 }} whileTap={{ scale: 0.95 }} transition={SPRING}
            style={{ display: 'inline-flex', gap: 0, cursor: 'pointer', border: `1px solid ${P.line}`, background: P.surface }}>
            {['hell', 'dunkel'].map(t => {
              const on = (t === 'dunkel') === dark
              return (
                <motion.span key={t} animate={{ background: on ? P.ink : 'transparent', color: on ? P.bg : P.sub }} transition={SPRING}
                  style={{ padding: '3px 9px', fontSize: TEXT.meta, fontFamily: MONO }}>{t}</motion.span>
              )
            })}
          </motion.span>
        )}
      </div>
      {note && <div style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, margin: '3px 0 12px' }}>{note}</div>}
      {children({ P, dark })}
    </div>
  )
}

/** Einstellungs-Zeile: Beschriftung links, Steuerung rechts. */
const Row = ({ P, label, hint, children, i = 0 }) => (
  <motion.div initial={{ opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }} transition={{ ...SPRING, delay: 0.05 + i * 0.04 }}
    style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 12px', borderBottom: `1px solid ${P.hair}` }}>
    <span style={{ display: 'block' }}>
      <span style={{ display: 'block', fontSize: 12.5, color: P.ink }}>{label}</span>
      {hint && <span style={{ display: 'block', fontFamily: MONO, fontSize: TEXT.meta, color: P.faint, marginTop: 2 }}>{hint}</span>}
    </span>
    <span style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 8 }}>{children}</span>
  </motion.div>
)

/** Feder-Schalter, mindestens 32 px hoch als Trefferfläche. */
const Switch = ({ on, onClick, P, label }) => (
  <motion.span onClick={onClick} role="switch" aria-checked={on} aria-label={label} data-switch={on ? 'on' : 'off'}
    whileTap={{ scale: 0.94 }} transition={SPRING}
    style={{ display: 'inline-flex', alignItems: 'center', height: 32, padding: '0 2px', cursor: 'pointer' }}>
    <motion.span animate={{ background: on ? P.ink : 'transparent' }} transition={SPRING}
      style={{ width: 34, height: 19, borderRadius: RADIUS.pill, border: `1.4px solid ${P.ink}`, display: 'flex', alignItems: 'center', padding: 1.6 }}>
      <motion.span animate={{ x: on ? 15 : 0 }} transition={SPRING}
        style={{ width: 13.5, height: 13.5, borderRadius: RADIUS.pill, background: on ? P.bg : P.ink, display: 'block' }} />
    </motion.span>
  </motion.span>
)

/** Echte Schaltfläche (minimal 32 px hoch). */
const Btn = ({ P, children, primary, onClick, title, i = 0 }) => (
  <motion.span onClick={onClick} title={title} whileHover={{ scale: 1.03 }} whileTap={{ scale: 0.96 }}
    initial={{ opacity: 0, y: 5 }} animate={{ opacity: 1, y: 0 }} transition={{ ...SPRING, delay: 0.08 + i * 0.04 }}
    style={{ display: 'inline-flex', alignItems: 'center', gap: 6, height: 32, padding: '0 12px', borderRadius: RADIUS.module,
      border: `1px solid ${primary ? P.ink : P.line}`, background: primary ? P.ink : P.surface,
      color: primary ? P.bg : P.ink, fontSize: 11.5, cursor: 'pointer' }}>{children}</motion.span>
)

/** Auswahlfeld (angezeigt, stationär, nicht tippen — Auswahl per Klick). */
function Choice({ P, value, options, onChange }) {
  return (
    <span style={{ display: 'inline-flex', border: `1px solid ${P.line}`, background: P.surface, borderRadius: RADIUS.module, overflow: 'hidden' }}>
      {options.map(o => (
        <motion.span key={o} onClick={() => onChange(o)} data-choice={o}
          animate={{ background: o === value ? P.ink : 'transparent', color: o === value ? P.bg : P.sub }} transition={SPRING}
          style={{ padding: '5px 10px', fontSize: 11, cursor: 'pointer' }}>{o}</motion.span>
      ))}
    </span>
  )
}

const DialogShell = ({ P, width, title, children, footer }) => (
  <motion.div initial={{ opacity: 0, scale: 0.96, y: 10 }} animate={{ opacity: 1, scale: 1, y: 0 }} transition={SPRING}
    style={{ width, borderRadius: RADIUS.dialog, border: `1px solid ${P.line}`, background: P.surface, boxShadow: '0 18px 44px rgba(0,0,0,0.18)', overflow: 'hidden' }}>
    <div style={{ display: 'flex', alignItems: 'center', padding: '10px 14px', borderBottom: `1px solid ${P.line}` }}>
      <span style={{ fontFamily: MONO, fontSize: TEXT.meta, letterSpacing: 1, color: P.sub }}>{title}</span>
      <motion.span whileTap={{ scale: 0.85 }} transition={SPRING} style={{ marginLeft: 'auto', display: 'inline-flex', cursor: 'pointer' }}>
        <IconX size={13} stroke={2} color={P.faint} />
      </motion.span>
    </div>
    {children}
    {footer && <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, padding: '10px 12px', borderTop: `1px solid ${P.hair}` }}>{footer}</div>}
  </motion.div>
)

/* 01 — Einstellungsdialog: Modell, Reasoning, Denken anzeigen, Übertragungssprache */

const MODELS01 = [
  { id: 'm1', name: 'opencode', provider: 'openapi' },
  { id: 'm2', name: 'klarer', provider: 'lokal' },
  { id: 'm3', name: 'waehler', provider: 'fernanbieter' }
]

function Variant01({ P }) {
  const [model, setModel] = useState(MODELS01[0].id)
  const [reasoning, setReasoning] = useState('Mitte')
  const [thinking, setThinking] = useState(true)
  const [lang, setLang] = useState('Deutsch')
  return (
    <DialogShell P={P} width={380} title="EINSTELLUNGEN — MODELL"
      footer={<><Btn P={P}>Abbrechen</Btn><Btn P={P} primary>Endgültig speichern</Btn></>}>
      <div style={{ padding: '2px 0 6px' }}>
        <Row P={P} label="Modell" hint="gilt für die nächste Nachricht" i={0}>
          <Choice P={P} value={model} options={MODELS01.map(m => m.id)} onChange={setModel} />
        </Row>
        <div style={{ padding: '6px 12px 10px', borderBottom: `1px solid ${P.hair}` }}>
          {MODELS01.map((m, i) => (
            <motion.div key={m.id} onClick={() => setModel(m.id)} data-modelrow={m.id}
              initial={{ opacity: 0, x: -4 }} animate={{ opacity: 1, x: 0 }} transition={{ ...SPRING, delay: 0.12 + i * 0.04 }}
              whileHover={{ x: 2 }} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '5px 6px', cursor: 'pointer', background: model === m.id ? P.hair : 'transparent' }}>
              <motion.span animate={{ scale: model === m.id ? 1 : 0.6, opacity: model === m.id ? 1 : 0.3 }} transition={SPRING}
                style={{ width: 6, height: 6, borderRadius: RADIUS.pill, background: P.ink }} />
              <span style={{ fontSize: 12, color: P.ink }}>{m.name}</span>
              <span style={{ marginLeft: 'auto', fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>{m.provider}</span>
            </motion.div>
          ))}
        </div>
        <Row P={P} label="Reasoning" hint="Denkstrategie des Modells" i={1}>
          <Choice P={P} value={reasoning} options={['Niedrig', 'Mitte', 'Hoch']} onChange={setReasoning} />
        </Row>
        <Row P={P} label="Denken anzeigen" hint="Gedankenzüge im Transkript sichtbar" i={2}>
          <Switch on={thinking} onClick={() => setThinking(t => !t)} P={P} label="Denken anzeigen" />
        </Row>
        <Row P={P} label="Übertragungssprache" hint="Sprache zu Anbietern" i={3}>
          <Choice P={P} value={lang} options={['Deutsch', 'English']} onChange={setLang} />
        </Row>
      </div>
    </DialogShell>
  )
}

/* 02 — Grenzen: Limit pro Datei, maximale Uploadgröße, Kontextbudget, Warnschwelle */

function LimitsRow({ P, label, value, unit, i }) {
  return (
    <Row P={P} label={label} i={i}>
      <span style={{ fontFamily: MONO, fontSize: 12, color: P.ink, minWidth: 44, textAlign: 'right' }}>{value}</span>
      <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint, width: 42 }}>{unit}</span>
    </Row>
  )
}

function Variant02({ P }) {
  const [warn, setWarn] = useState(80)
  return (
    <DialogShell P={P} width={380} title="EINSTELLUNGEN — GRENZEN"
      footer={<Btn P={P} primary>Zurücksetzen auf Werkseinstellung</Btn>}>
      <LimitsRow P={P} label="Anbieter-Limit pro Datei" value="5" unit="MB je Datei" i={0} />
      <LimitsRow P={P} label="Maximale Uploadgröße" value="22" unit="MB gesamt" i={1} />
      <LimitsRow P={P} label="Kontextbudget" value="128" unit="Tausend Zeichen" i={2} />
      <Row P={P} label="Warnschwelle" hint="Meldung, wenn der Kontext so voll ist" i={3}>
        <motion.span initial={{ opacity: 0, x: 6 }} animate={{ opacity: 1, x: 0 }} transition={{ ...SPRING, delay: 0.2 }}
          style={{ fontFamily: MONO, fontSize: 12, color: P.ink, minWidth: 34, textAlign: 'right' }}>{warn} %</motion.span>
        <Choice P={P} value={warn <= 60 ? '60 %' : warn <= 80 ? '80 %' : '95 %'} options={['60 %', '80 %', '95 %']} onChange={v => setWarn(parseInt(v, 10))} />
      </Row>
    </DialogShell>
  )
}

/* 03 — Darstellung: hell/dunkel, Reduce Motion, Schriftgröße, Dichte */

function Variant03({ P, dark }) {
  const [appearance, setAppearance] = useState(dark ? 'dunkel' : 'hell')
  const [reduceMotion, setReduceMotion] = useState(false)
  const [fontSize, setFontSize] = useState(13)
  const [density, setDensity] = useState('normal')
  return (
    <DialogShell P={P} width={380} title="EINSTELLUNGEN — DARSTELLUNG">
      <Row P={P} label="Helligkeit" hint="Oberfläche hell oder dunkel" i={0}>
        <Choice P={P} value={appearance} options={['hell', 'dunkel']} onChange={setAppearance} />
      </Row>
      <Row P={P} label="Reduce Motion" hint="Bewegung auf das notwendigste drosseln" i={1}>
        <Switch on={reduceMotion} onClick={() => setReduceMotion(r => !r)} P={P} label="Reduce Motion" />
      </Row>
      <Row P={P} label="Schriftgröße" i={2}>
        <motion.span onClick={() => setFontSize(s => Math.max(11, s - 1))} whileTap={{ scale: 0.9 }} transition={SPRING}
          style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 30, height: 32, border: `1px solid ${P.line}`, cursor: 'pointer' }}>
          <IconChevronLeft size={13} stroke={2} color={P.ink} />
        </motion.span>
        <span style={{ fontFamily: MONO, fontSize: 12, color: P.ink, minWidth: 44, textAlign: 'center' }}>{fontSize} px</span>
        <motion.span onClick={() => setFontSize(s => Math.min(17, s + 1))} whileTap={{ scale: 0.9 }} transition={SPRING}
          style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 30, height: 32, border: `1px solid ${P.line}`, cursor: 'pointer' }}>
          <IconChevronRight size={13} stroke={2} color={P.ink} />
        </motion.span>
      </Row>
      <Row P={P} label="Dichte" hint="abstand zwischen Zeilen" i={3}>
        <Choice P={P} value={density} options={['kompakt', 'normal']} onChange={setDensity} />
      </Row>
    </DialogShell>
  )
}

/* 04 — Schlüssel und Verbindungen: Anbieter mit Zustand + 'Schlüssel hinterlegen' */

const PROVIDERS04 = [
  { id: 'p1', name: 'opencode', state: 'verbunden', note: 'Schlüssel geprüft heute' },
  { id: 'p2', name: 'lokalmodell', state: 'kein-schluessel', note: 'kein Schlüssel hinterlegt' },
  { id: 'p3', name: 'waehler', state: 'pruefen', note: 'Schlüssel wird geprüft …' }
]

function Variant04({ P }) {
  const [providers, setProviders] = useState(PROVIDERS04)
  const [input, setInputOpen] = useState('')
  const [value, setValue] = useState('')
  const [show, setShow] = useState(false)
  const zustand = (s) => s === 'verbunden' ? 'verbunden' : s === 'pruefen' ? 'prüfen' : 'kein Schlüssel'
  return (
    <DialogShell P={P} width={400} title="SCHLÜSSEL UND VERBINDUNGEN">
      <div>
        {providers.map((p, i) => (
          <motion.div key={p.id} initial={{ opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }} transition={{ ...SPRING, delay: 0.05 + i * 0.05 }}
            style={{ display: 'flex', alignItems: 'center', gap: 9, padding: '10px 12px', borderBottom: `1px solid ${P.hair}` }}>
            <motion.span animate={{ opacity: [1, 0.3, 1] }} transition={p.state === 'pruefen' ? { duration: 1.4, repeat: Infinity, ease: 'easeInOut' } : { duration: 0 }}
              style={{ width: 7, height: 7, borderRadius: RADIUS.pill, background: p.state === 'verbunden' ? P.ink : p.state === 'pruefen' ? P.sub : 'transparent', border: `1.3px solid ${P.ink}`, flex: '0 0 auto' }} />
            <span>
              <span style={{ display: 'block', fontSize: 12.5, color: P.ink }}>{p.name}</span>
              <span style={{ display: 'block', fontFamily: MONO, fontSize: TEXT.meta, color: p.state === 'verbunden' ? P.sub : P.faint }}>{p.note}</span>
            </span>
            <span style={{ marginLeft: 'auto', display: 'inline-flex', gap: 6, alignItems: 'center' }}>
              <span data-state={p.state} style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.ink, padding: '3px 8px', border: `1px solid ${P.line}` }}>{zustand(p.state)}</span>
              {p.state !== 'verbunden' && (
                <Btn P={P} title="Schlüssel hinterlegen" onClick={() => setInputOpen(p.id)}>
                  <IconKey size={11} stroke={2} /> Schlüssel hinterlegen
                </Btn>
              )}
            </span>
          </motion.div>
        ))}
      </div>
      <AnimatePresence initial={false}>
        {input && (
          <motion.div key="schluessel" initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }} transition={SPRING}
            style={{ overflow: 'hidden', borderBottom: `1px solid ${P.hair}` }}>
            <div style={{ display: 'flex', gap: 7, padding: '10px 12px', alignItems: 'center' }}>
              <IconKey size={12} stroke={2} color={P.faint} />
              <input value={value} onChange={e => setValue(e.target.value)} autoFocus type={show ? 'text' : 'password'}
                placeholder="sk-… einfügen" data-keyinput
                style={{ flex: 1, height: 32, background: P.bg, border: `1px solid ${P.line}`, color: P.ink, fontFamily: MONO, fontSize: 11, padding: '0 9px', outline: 'none', borderRadius: RADIUS.module }} />
              <motion.span onClick={() => setShow(s => !s)} whileTap={{ scale: 0.9 }} transition={SPRING} title="zeigen/verbergen" style={{ display: 'inline-flex', cursor: 'pointer' }}>
                {show ? <IconEyeOff size={13} stroke={2} color={P.sub} /> : <IconEye size={13} stroke={2} color={P.sub} />}
              </motion.span>
              <Btn P={P} primary title="Schlüssel speichern"><IconCheck size={11} stroke={2.4} /> Speichern</Btn>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </DialogShell>
  )
}

/* 05 — Werkzeuge und MCP: Server mit Schaltern und Toolzahl */

const SERVERS05 = [
  { id: 'sv1', name: 'dateisystem', tools: 9 },
  { id: 'sv2', name: 'web-suche', tools: 4 },
  { id: 'sv3', name: 'git-werkzeuge', tools: 7 },
  { id: 'sv4', name: 'benachrichtigungen', tools: 2 }
]

function Variant05({ P }) {
  const [on, setOn] = useState({ sv1: true, sv2: true, sv3: false, sv4: true })
  const anzahl = SERVERS05.filter(s => on[s.id]).reduce((a, s) => a + s.tools, 0)
  return (
    <DialogShell P={P} width={380} title="WERKZEUGE UND MCP"
      footer={<span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>{anzahl} Werkzeuge aktiv</span>}>
      <div>
        {SERVERS05.map((s, i) => (
          <motion.div key={s.id} initial={{ opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }} transition={{ ...SPRING, delay: 0.05 + i * 0.05 }}
            style={{ display: 'flex', alignItems: 'center', gap: 9, padding: '10px 12px', borderBottom: `1px solid ${P.hair}` }}>
            <span>
              <span style={{ display: 'block', fontSize: 12.5, color: P.ink }}>{s.name}</span>
              <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>{s.tools} Werkzeuge</span>
            </span>
            <span style={{ marginLeft: 'auto' }}>
              <Switch on={!!on[s.id]} onClick={() => setOn(o => ({ ...o, [s.id]: !o[s.id] }))} P={P} label={`${s.name} schalten`} />
            </span>
          </motion.div>
        ))}
      </div>
    </DialogShell>
  )
}

/* 06 — Sitzungsverlauf links, einklappbar; aktive Sitzung hervorgehoben */

const SESSIONS06 = [
  { id: 'se1', title: 'skaldir ui-abnahme', time: 'heute 14:22', msgs: 42 },
  { id: 'se2', title: 'fixtures fuer kategorie g', time: 'heute 11:03', msgs: 17 },
  { id: 'se3', title: 'upload-limit abklaeren', time: 'gestern 19:40', msgs: 9 },
  { id: 'se4', title: 'goal-bar messwerte', time: 'gestern 09:15', msgs: 33 }
]

function Variant06({ P }) {
  const [open, setOpen] = useState(true)
  const [active, setActive] = useState('se1')
  return (
    <div style={{ display: 'flex', height: 400, borderRadius: RADIUS.dialog, border: `1px solid ${P.line}`, background: P.surface, overflow: 'hidden' }}>
      <motion.span onClick={() => setOpen(o => !o)} whileTap={{ scale: 0.88 }} transition={SPRING}
        style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 30, borderRight: `1px solid ${P.line}`, cursor: 'pointer', background: P.surface }}>
        {open ? <IconLayoutSidebarLeftCollapse size={13} stroke={1.8} color={P.ink} /> : <IconLayoutSidebarLeftExpand size={13} stroke={1.8} color={P.ink} />}
      </motion.span>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div key="verlauf" initial={{ width: 0, opacity: 0 }} animate={{ width: 250, opacity: 1 }} exit={{ width: 0, opacity: 0 }} transition={SPRING}
            style={{ overflow: 'hidden', borderRight: `1px solid ${P.line}`, background: P.surface }}>
            <div style={{ padding: '9px 12px', fontFamily: MONO, fontSize: TEXT.meta, letterSpacing: 1, color: P.sub, borderBottom: `1px solid ${P.hair}` }}>SITZUNGEN</div>
            <div>
              {SESSIONS06.map((s, i) => {
                const sel = active === s.id
                return (
                  <motion.div key={s.id} onClick={() => setActive(s.id)} data-sessionrow={s.id}
                    initial={{ opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }} transition={{ ...SPRING, delay: 0.05 + i * 0.04 }} whileHover={{ x: 2 }} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '9px 12px', cursor: 'pointer',
                      background: sel ? P.hair : 'transparent', borderLeft: `2px solid ${sel ? P.ink : 'transparent'}` }}>
                    <span style={{ minWidth: 0 }}>
                      <span style={{ display: 'block', fontSize: 12, fontWeight: sel ? 600 : 400, color: P.ink, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.title}</span>
                      <span style={{ display: 'block', fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>{s.time} · {s.msgs} Nachrichten</span>
                    </span>
                    {sel && <motion.span layoutId="se-mark" style={{ marginLeft: 'auto', width: 6, height: 6, borderRadius: RADIUS.pill, background: P.ink }} />}
                  </motion.div>
                )
              })}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', padding: 14 }}>
        {SESSIONS06.filter(s => s.id === active).map(s => (
          <motion.div key={s.id} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={SPRING}
            style={{ fontFamily: SANS }}>
            <span style={{ fontSize: 13, fontWeight: 600, color: P.ink }}>{s.title}</span>
            <div style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, marginTop: 4 }}>{s.time} · {s.msgs} Nachrichten</div>
          </motion.div>
        ))}
      </div>
    </div>
  )
}

/* 07 — Sitzung im Griff: umbenennen, duplizieren, löschen mit Rückfrage, neuer Tab */

function Variant07({ P }) {
  const [title, setTitle] = useState('skaldir ui-abnahme')
  const [editing, setEditing] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  return (
    <div style={{ padding: 16, borderRadius: RADIUS.dialog, border: `1px solid ${P.line}`, background: P.surface }}>
      <div style={{ fontFamily: MONO, fontSize: TEXT.meta, letterSpacing: 1, color: P.sub, marginBottom: 10 }}>SITZUNG</div>
      {editing ? (
        <motion.div initial={{ opacity: 0, scale: 0.98 }} animate={{ opacity: 1, scale: 1 }} transition={SPRING} style={{ display: 'flex', gap: 7 }}>
          <input autoFocus value={title} onChange={e => setTitle(e.target.value)} data-renametitle
            onKeyDown={e => e.key === 'Enter' && setEditing(false)}
            style={{ flex: 1, height: 32, background: P.bg, border: `1px solid ${P.ink}`, color: P.ink, fontFamily: SANS, fontSize: 12.5, padding: '0 9px', outline: 'none', borderRadius: RADIUS.module }} />
          <Btn P={P} title="Endgültig speichern" onClick={() => setEditing(false)}><IconCheck size={11} stroke={2.4} /> Fertig</Btn>
        </motion.div>
      ) : (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontSize: 13.5, fontWeight: 600, color: P.ink }}>{title}</span>
          <motion.span onClick={() => setEditing(true)} title="umbenennen" whileHover={{ scale: 1.12 }} whileTap={{ scale: 0.88 }} transition={SPRING}
            style={{ display: 'inline-flex', cursor: 'pointer' }}>
            <IconPencil size={12} stroke={2} color={P.sub} />
          </motion.span>
        </div>
      )}
      <div style={{ display: 'flex', gap: 7, flexWrap: 'wrap', marginTop: 12 }}>
        <Btn P={P} onClick={() => setEditing(true)}><IconPencil size={11} stroke={2} /> Umbenennen</Btn>
        <Btn P={P}><IconCopy size={11} stroke={2} /> Duplizieren</Btn>
        <Btn P={P} title="in einem neuen Tab öffnen"><IconExternalLink size={11} stroke={2} /> In neuem Tab öffnen</Btn>
        <Btn P={P} onClick={() => setConfirmDelete(true)}><IconTrash size={11} stroke={2} /> Löschen</Btn>
      </div>
      <AnimatePresence initial={false}>
        {confirmDelete && (
          <motion.div key="rueckfrage" data-deleteconfirm initial={{ opacity: 0, scale: 0.96, y: 8 }} animate={{ opacity: 1, scale: 1, y: 0 }} exit={{ opacity: 0, scale: 0.96, y: 8 }} transition={SPRING}
            style={{ marginTop: 12, borderRadius: RADIUS.module, border: `1.4px solid ${P.ink}`, padding: '9px 11px' }}>
            <span style={{ fontSize: 12, color: P.ink }}>Wirklich löschen? Diese Sitzung kehrt nicht zurück.</span>
            <div style={{ display: 'flex', gap: 7, marginTop: 8 }}>
              <Btn P={P} title="Wirklich löschen" >Ja, löschen</Btn>
              <Btn P={P} onClick={() => setConfirmDelete(false)}>Nein, abbrechen</Btn>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

/* 08 — Sitzungskopf: Titel in der Mitte, darunter Meta, Stift zum Ändern */

function Variant08({ P }) {
  const [title, setTitle] = useState('goal-bar messwerte')
  const [editing, setEditing] = useState(false)
  return (
    <div style={{ padding: '12px 14px', borderBottom: `1px solid ${P.line}`, background: P.surface, borderRadius: RADIUS.dialog }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, position: 'relative' }}>
        <span style={{ flex: 1 }} />
        {editing ? (
          <motion.div initial={{ opacity: 0, scale: 0.97 }} animate={{ opacity: 1, scale: 1 }} transition={SPRING} style={{ display: 'flex', gap: 6 }}>
            <input autoFocus value={title} onChange={e => setTitle(e.target.value)} data-headertitle
              onKeyDown={e => e.key === 'Enter' && setEditing(false)}
              style={{ width: 260, height: 32, background: P.bg, border: `1px solid ${P.ink}`, color: P.ink, fontFamily: SANS, fontSize: 12.5, textAlign: 'center', padding: '0 9px', outline: 'none', borderRadius: RADIUS.module }} />
            <Btn P={P} title="Umbenennen fertig" onClick={() => setEditing(false)}><IconCheck size={11} stroke={2.4} /></Btn>
          </motion.div>
        ) : (
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 7 }}>
            <span style={{ fontSize: 14, fontWeight: 600, color: P.ink }}>{title}</span>
            <motion.span onClick={() => setEditing(true)} data-editpencil whileHover={{ scale: 1.15, rotate: -8 }} whileTap={{ scale: 0.88 }} transition={SPRING}
              title="Sitzungstitel ändern" style={{ display: 'inline-flex', cursor: 'pointer' }}>
              <IconPencil size={12} stroke={2} color={P.sub} />
            </motion.span>
          </span>
        )}
        <span style={{ flex: 1 }} />
      </div>
      <div style={{ textAlign: 'center', fontFamily: MONO, fontSize: TEXT.meta, color: P.faint, marginTop: 3 }}>
        waehler · fernanbieter · Dauer 12:40 · 33 Nachrichten
      </div>
    </div>
  )
}

/* 09 — sehr kompakt: eine Schalterzeile mit den vier wichtigsten Einstellungen */

function Variant09({ P }) {
  const [settings, setSettings] = useState({ thinking: true, reduceMotion: false, darkMode: false, autoScroll: true })
  const toggle = (k) => setSettings(s => ({ ...s, [k]: !s[k] }))
  const items = [
    { k: 'thinking', label: 'Denken anzeigen' },
    { k: 'reduceMotion', label: 'Reduce Motion' },
    { k: 'darkMode', label: 'Dunkelmodus' },
    { k: 'autoScroll', label: 'Automatisch scrollen' }
  ]
  return (
    <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={SPRING}
      style={{ display: 'flex', alignItems: 'center', gap: 14, padding: '5px 12px', borderRadius: RADIUS.module, border: `1px solid ${P.line}`, background: P.surface, flexWrap: 'wrap' }}>
      {items.map((it, i) => (
        <span key={it.k} style={{ display: 'inline-flex', alignItems: 'center', gap: 7, padding: '2px 0' }}>
          <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.ink }}>{it.label}</span>
          <Switch on={!!settings[it.k]} onClick={() => toggle(it.k)} P={P} label={it.label} />
          {i < items.length - 1 && <span style={{ width: 1, height: 18, background: P.hair }} />}
        </span>
      ))}
    </motion.div>
  )
}

/* ------------------------------------------------den Listeneintrag bauen */

const wrap = (num, title, note, fn, fixedDark) => ({ P, dark }) => (
  <div style={{ display: 'flex', justifyContent: 'center', padding: 6 }}>
    {fn({ P, dark })}
  </div>
)

const S = (num, title, note, fn) => () => (
  <Stage num={num} title={title} note={note}>{wrap(num, title, note, fn)}</Stage>
)

export default {
  '01 · Einstellungsdialog: Modell, Reasoning, Denken anzeigen, Übertragungssprache': S('01', 'Einstellungsdialog — Modell & Reasoning', 'Modell mit Anbieter, Reasoning-Level, Denken anzeigen, Übertragungssprache', ({ P, dark }) => <Variant01 P={P} dark={dark} />),
  '02 · Grenzen: Limits pro Datei, maximale Uploadgröße, Kontextbudget, Warnschwelle': S('02', 'Grenzen', 'Zahlen und Einheiten für Limits, Uploadgröße, Kontextbudget und Warnschwelle', ({ P, dark }) => <Variant02 P={P} dark={dark} />),
  '03 · Darstellung: hell/dunkel, Reduce Motion, Schriftgröße, Dichte': S('03', 'Darstellung', 'Helligkeit umschalten, Bewegung drosseln, Schriftgröße und Zeilendichte', ({ P, dark }) => <Variant03 P={P} dark={dark} />),
  '04 · Schlüssel und Verbindungen: Anbieter-Zustände + Schlüssel hinterlegen': S('04', 'Schlüssel und Verbindungen', 'verbunden / kein Schlüssel / prüfen — mit Eingabefeld zum Hinterlegen', ({ P, dark }) => <Variant04 P={P} dark={dark} />),
  '05 · Werkzeuge und MCP: Server mit Schaltern und Toolzahl': S('05', 'Werkzeuge und MCP', 'wie der Panel-Manager, aber für Werkzeuge — Schalter wirklich schaltbar', ({ P, dark }) => <Variant05 P={P} dark={dark} />),
  '06 · Sitzungsverlauf links (einklappbar) mit aktiver Sitzung': S('06', 'Sitzungsverlauf (links)', 'einklappbare Leiste; aktive Sitzung hervorgehoben; Klick wechselt wirklich', ({ P, dark }) => <Variant06 P={P} dark={dark} />),
  '07 · Sitzung im Griff: umbenennen, duplizieren, löschen mit Rückfrage': S('07', 'Sitzung im Griff', 'echtes Eingabefeld beim Umbenennen; Rückfrage erscheint wirklich', ({ P, dark }) => <Variant07 P={P} dark={dark} />),
  '08 · Sitzungskopf: Titel in der Mitte, Meta darunter, Stift zum Ändern': S('08', 'Sitzungskopf', 'Titel zentriert, Meta (Modell, Dauer, Nachrichten) darunter', ({ P, dark }) => <Variant08 P={P} dark={dark} />),
  '09 · Sehr kompakt: eine Schalterzeile mit den vier wichtigsten Einstellungen': S('09', 'Kompakt — eine Zeile', 'vier wichtigste Einstellungen als echte Schalter in einer Zeile', ({ P, dark }) => <Variant09 P={P} dark={dark} />),
  '10 · Dunkle Fassung des Favoriten (Varianten 04, Vollbild dunkel)': () => (
    <Stage num="10" title="Favorit dunkel" note="Schlüssel und Verbindungen, dunkle Fassung, ohne hell/dunkel-Umschalter" fixedDark>
      {({ P, dark }) => (
        <div style={{ padding: 20, margin: '-20px', background: DARK.bg, minHeight: 560, boxSizing: 'border-box', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <Variant04 P={DARK} dark />
        </div>
      )}
    </Stage>
  )
}
