/**
 * Onboarding — erster Start, Diagnose und verstaendliche Startfehler.
 *
 * Zehn Varianten fuer den allerersten Moment mit Skaldir: Verbindung einrichten,
 * sechs Pruefungen der Diagnose, ehrliche Startfehler mit genauem Ort und einem
 * Weg nach vorn, erste Schritte, Beispielziele, Fortschrittsleiste und eine
 * kompakte Fertig-Zeile. Variante 10 ist die dunkle Vollbild-Fassung des
 * Favoriten (01 · Erster Start).
 *
 * Regel (Produkt): ein Startfehler nennt IMMER drei Dinge — was passiert ist,
 * wo genau es steht, und welcher Weg bleibt. Kein rotes Licht: deutlich wird es
 * durch Text, Kreuz (✕) und starke Rahmen.
 *
 * Der Zustand ist echt (useState): Schritte werden wirklich abgehakt, Pruefungen
 * wechseln wirklich ihren Zustand (idle → prueft → gruen/fehlt), die Leiste
 * faehrt mit Feder, die Pruefung laeuft sichtbar mit animate() durch.
 * Nur Schwarz/Weiss/Grau, Symbole aus @tabler/icons-react, Orb aus thinking-orbs.
 */
import { motion, AnimatePresence, animate } from 'motion/react'
// @ts-expect-error — Laufzeit-Alias (siehe vite.config.ts)
import { ThinkingOrb } from 'thinking-orbs'
import React, { useState, useRef, useEffect } from 'react'
import { IconActivity, IconAlertCircle, IconArrowRight, IconArrowUpRight, IconBolt, IconBook, IconCheck, IconChevronDown, IconChevronRight, IconCircle, IconCircleCheck, IconClick, IconClipboardCheck, IconClock, IconCompass, IconCopy, IconCpu, IconExternalLink, IconEye, IconEyeOff, IconFileText, IconFolderCheck, IconFolderOpen, IconHelpCircle, IconKey, IconLink, IconListCheck, IconLoader2, IconLock, IconPlayerPlay, IconPlugConnected, IconRefresh, IconRotateClockwise, IconRoute, IconScan, IconSearch, IconSend, IconServer, IconSettings, IconShieldCheck, IconStack2, IconTarget, IconTerminal, IconTool, IconWifi, IconWifiOff, IconWorld } from '@tabler/icons-react'

/* Farben, Schrift, Federn, Radien und Schriftgroessen: ./_tokens (eine Wahrheit).
   Befunde 18.09.2026: kein eigener Hexwert, Schriftuntergrenze 11 px (TEXT.meta),
   Radien aus RADIUS, useBarrierefrei() gegen reduzierte Bewegung und fuer die Tastatur. */
import { LIGHT, DARK, MONO, SANS, SPRING, RADIUS, TEXT } from './_tokens'
import { useBarrierefrei } from './_barrierefrei'

/* ------------------------------------------------------- gemeinsame Bausteine */

/** Aeussere Buehne: Titelzeile, Hinweis, hell/dunkel-Umschalter (echter useState). */
function Case({ name, note, dark0 = false, full = false, children }) {
  useBarrierefrei()
  const [dark, setDark] = useState(dark0)
  const P = dark ? DARK : LIGHT
  const themeBtn = (label, want, attr) => (
    <motion.span key={label} data-theme={attr} whileTap={{ scale: 0.94 }} transition={SPRING}
      animate={{
        borderColor: dark === want ? P.ink : P.line,
        backgroundColor: dark === want ? P.ink : P.surface,
        color: dark === want ? P.bg : P.ink
      }}
      onClick={() => setDark(want)}
      style={{ padding: '3px 9px', border: `1px solid ${P.line}`, cursor: 'pointer' }}>{label}</motion.span>
  )
  return (
    <div data-stage data-scheme={dark ? 'dunkel' : 'hell'}
      style={{ padding: full ? 0 : 20, background: P.bg, color: P.ink, fontFamily: SANS, width: '100%', maxWidth: 980, boxSizing: 'border-box' }}>
      <div style={{ padding: full ? '20px 20px 0' : 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ fontFamily: MONO, fontSize: 12, fontWeight: 600 }}>{name}</span>
          <span style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 6, fontFamily: MONO, fontSize: TEXT.meta }}>
            {themeBtn('hell', false, 'themelight')}
            {themeBtn('dunkel', true, 'themedark')}
          </span>
        </div>
        <div style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, marginTop: 3 }}>{note}</div>
      </div>
      <div style={{ marginTop: 12, padding: full ? '0 20px 20px' : 0 }}>{children(P)}</div>
    </div>
  )
}

/** Knopf: erster Weg ist der Hauptweg (Tinte gefuellt), die anderen bleiben offen. */
const Btn = ({ P, label, Icon, onClick, primary, i = 0, dim, disabled, attr }) => (
  <motion.span data-btn={attr}
    initial={{ opacity: 0, y: 5 }} animate={{ opacity: dim ? 0.4 : 1, y: 0 }}
    whileHover={disabled ? undefined : { scale: 1.03 }} whileTap={disabled ? undefined : { scale: 0.96 }}
    transition={{ ...SPRING, delay: 0.06 + i * 0.05 }}
    onClick={disabled ? undefined : onClick} title={label}
    style={{
      display: 'inline-flex', alignItems: 'center', gap: 6, fontFamily: MONO, fontSize: TEXT.meta,
      padding: '5px 10px', cursor: disabled ? 'default' : 'pointer', whiteSpace: 'nowrap',
      border: `1px solid ${primary ? P.ink : P.line}`,
      background: primary ? P.ink : 'transparent',
      color: primary ? P.bg : P.ink
    }}>
    {Icon && <Icon size={11} stroke={1.9} />}
    {label}
  </motion.span>
)

/** Balken mit Feder-Fuellung — sichtbarer Fortschritt. */
const FillBar = ({ P, pct, height = 6 }) => (
  <div style={{ height, background: P.hair, border: `1px solid ${P.line}`, boxSizing: 'border-box', overflow: 'hidden' }}>
    <motion.div animate={{ width: `${pct}%` }} transition={SPRING} style={{ height: '100%', background: P.ink }} />
  </div>
)

/** Zustand einer Pruefung: stiller Punkt, drehender Ring, Haken, Warnzeichen. */
const StateMark = ({ P, st, size = 13 }) => {
  if (st === 'running') {
    return (
      <motion.span data-mark="running" animate={{ rotate: 360 }} transition={{ duration: 1.1, repeat: Infinity, ease: 'linear' }}
        style={{ display: 'inline-flex', color: P.ink }}>
        <IconLoader2 size={size} stroke={2} />
      </motion.span>
    )
  }
  if (st === 'ok') {
    return (
      <motion.span data-mark="ok" initial={{ opacity: 0, scale: 0.3 }} animate={{ opacity: 1, scale: 1 }} transition={SPRING}
        style={{ display: 'inline-flex', color: P.ink }}>
        <IconCircleCheck size={size} stroke={1.9} />
      </motion.span>
    )
  }
  if (st === 'fail') {
    return (
      <motion.span data-mark="fail" initial={{ opacity: 0, scale: 0.5 }} animate={{ opacity: 1, scale: 1, x: [0, -2, 2, 0] }} transition={SPRING}
        style={{ display: 'inline-flex', color: P.ink }}>
        <IconAlertCircle size={size} stroke={1.9} />
      </motion.span>
    )
  }
  return <span data-mark="idle" style={{ display: 'inline-flex', color: P.faint }}><IconCircle size={size} stroke={1.6} /></span>
}

/** Ein Kaestchen, das sich wirklich abhakt. */
const Tick = ({ P, on, size = 15 }) => (
  <motion.span data-tick={on ? 'true' : 'false'} whileTap={{ scale: 0.86 }} transition={SPRING}
    animate={{ backgroundColor: on ? P.ink : 'transparent', borderColor: on ? P.ink : P.line }}
    style={{ width: size, height: size, border: `1px solid ${P.line}`, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', flex: '0 0 auto' }}>
    <AnimatePresence initial={false}>
      {on && (
        <motion.span key="haken" initial={{ opacity: 0, scale: 0.3 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0, scale: 0.3 }} transition={SPRING}
          style={{ display: 'flex', color: P.bg }}>
          <IconCheck size={10} stroke={3} />
        </motion.span>
      )}
    </AnimatePresence>
  </motion.span>
)

/** Kopfzeile eines Kastens (Mono, Grossbuchstaben). */
const KastenKopf = ({ P, label, right }) => (
  <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '7px 10px', borderBottom: `1px solid ${P.line}`, background: P.surface }}>
    <span style={{ fontFamily: MONO, fontSize: TEXT.meta, letterSpacing: 1, color: P.sub }}>{label}</span>
    {right && <span style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 8 }}>{right}</span>}
  </div>
)

/** Rechteck mit Kreuz — Fehler werden ueber Text und Rahmen deutlich, nicht ueber Farbe. */
const Kreuz = ({ P, delay = 0 }) => (
  <motion.span initial={{ opacity: 0, scale: 0.4 }} animate={{ opacity: 1, scale: 1, x: [0, -2, 2, 0] }} transition={{ ...SPRING, delay }}
    style={{ fontFamily: MONO, fontSize: 13, lineHeight: 1, color: P.ink, flex: '0 0 auto' }}>✕</motion.span>
)

/* ------------------------------------------------------- Einrichten (01/08/10) */

const PROVIDERS = [
  { id: 'openai', name: 'OpenAI', host: 'api.openai.com', hint: 'sk-…', note: 'Wolke, Schluessel noetig' },
  { id: 'anthropic', name: 'Anthropic', host: 'api.anthropic.com', hint: 'sk-ant-…', note: 'Wolke, Schluessel noetig' },
  { id: 'deepseek', name: 'DeepSeek', host: 'api.deepseek.com', hint: 'sk-…', note: 'Wolke, Schluessel noetig' },
  { id: 'lokal', name: 'Lokal (Ollama)', host: '127.0.0.1:11434', hint: 'kein Schluessel', note: 'bleibt auf diesem Rechner' }
]

const SETUP_STEPS = [
  { id: 'prog', name: 'Anbieter waehlen', desc: 'Wer beantwortet die Anfragen' },
  { id: 'key', name: 'Schluessel einsetzen', desc: 'Bleibt auf diesem Rechner' },
  { id: 'check', name: 'Verbindung pruefen', desc: 'Ein kurzer Aufruf, kein Auftrag' },
  { id: 'ready', name: 'Bereit', desc: 'Der erste Start ist geschafft' }
]

/* Echter Zustand der Einrichtung: Auswahl, Eingabe, laufende Pruefung. */
function useSetup() {
  const [provider, setProvider] = useState(null)
  const [key, setKey] = useState('')
  const [check, setCheck] = useState('idle')
  const [progress, setProgress] = useState(0)
  const [reveal, setReveal] = useState(false)
  const [runs, setRuns] = useState(0)
  const ctrl = useRef(null)
  useEffect(() => () => { if (ctrl.current) ctrl.current.stop() }, [])

  const needKey = !!provider && provider.id !== 'lokal'
  const keyThere = !!provider && (!needKey || key.trim().length > 0)
  const stepIndex = check === 'ok' ? 3 : keyThere ? 2 : provider ? 1 : 0
  const done = {
    prog: !!provider,
    key: keyThere,
    check: check === 'ok',
    ready: check === 'ok'
  }
  const runCheck = () => {
    if (!keyThere || check === 'running') return
    if (ctrl.current) ctrl.current.stop()
    setCheck('running'); setProgress(0)
    ctrl.current = animate(0, 100, {
      duration: 1.5, ease: 'easeInOut',
      onUpdate: v => setProgress(v),
      onComplete: () => { setCheck('ok'); setRuns(r => r + 1) }
    })
  }
  const reset = () => {
    if (ctrl.current) ctrl.current.stop()
    setProvider(null); setKey(''); setCheck('idle'); setProgress(0); setRuns(0)
  }
  return { provider, setProvider, key, setKey, check, progress, reveal, setReveal, stepIndex, done, runCheck, reset, needKey, runs }
}

/** Die Schrittspalte: abgehakte Schritte sind wahrhaftig abgehakt. */
const StepLine = ({ P, step, i, state, onClick }) => (
  <motion.div data-setupstep={step.id} data-state={state} layout
    onClick={onClick} whileHover={{ x: state === 'todo' ? 0 : 2 }} transition={SPRING}
    style={{ display: 'flex', alignItems: 'center', gap: 9, padding: '7px 8px', cursor: onClick ? 'pointer' : 'default',
      borderBottom: `1px solid ${P.hair}`, opacity: state === 'todo' ? 0.45 : 1 }}>
    <Tick P={P} on={state === 'done'} size={15} />
    <span style={{ minWidth: 0 }}>
      <span style={{ display: 'block', fontFamily: SANS, fontSize: 12, fontWeight: state === 'now' ? 700 : 600, color: P.ink, whiteSpace: 'nowrap' }}>
        {i + 1}. {step.name}
      </span>
      <span style={{ display: 'block', fontFamily: MONO, fontSize: TEXT.meta, color: P.sub }}>{step.desc}</span>
    </span>
    {state === 'now' && (
      <motion.span animate={{ opacity: [1, 0.3, 1] }} transition={{ duration: 1.5, repeat: Infinity }}
        style={{ marginLeft: 'auto', width: 5, height: 5, borderRadius: RADIUS.pill, background: P.ink, flex: '0 0 auto' }} />
    )}
  </motion.div>
)

/** Der ganze Einrichtungs-Vorgang — genutzt von 01, 08 (Ausschnitt) und 10. */
function SetupPanel({ P, compact = false, dark = false }) {
  const s = useSetup()
  const orbTheme = dark ? 'dark' : 'light'
  const p = s.provider
  return (
    <div data-setuppanel style={{ border: `1px solid ${P.line}`, background: P.surface }}>
      <KastenKopf P={P} label="ERSTER START · VERBINDUNG EINRICHTEN"
        right={<span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub }}>Schritt {Math.min(s.stepIndex + 1, 4)} von 4</span>} />
      <div style={{ display: compact ? 'block' : 'flex', alignItems: 'stretch' }}>
        {/* Schrittspalte */}
        <div style={{ width: compact ? '100%' : 252, flex: '0 0 auto', borderRight: compact ? 'none' : `1px solid ${P.line}`, borderBottom: compact ? `1px solid ${P.line}` : 'none' }}>
          {SETUP_STEPS.map((st, i) => (
            <StepLine key={st.id} P={P} step={st} i={i} state={s.done[st.id] ? 'done' : i === s.stepIndex ? 'now' : 'todo'} />
          ))}
        </div>

        {/* Inhalt des laufenden Schritts */}
        <div style={{ flex: 1, minWidth: 0, padding: 12, display: 'flex', flexDirection: 'column', gap: 10 }}>
          {/* 1 — Anbieter */}
          <motion.div animate={{ opacity: s.stepIndex >= 0 ? 1 : 0.4 }} transition={SPRING}>
            <div style={{ fontFamily: MONO, fontSize: TEXT.meta, letterSpacing: 1, color: P.sub, marginBottom: 6 }}>ANBIETER</div>
            <div style={{ display: 'grid', gridTemplateColumns: compact ? '1fr 1fr' : '1fr 1fr', gap: 6 }}>
              {PROVIDERS.map((pr, i) => {
                const on = p && p.id === pr.id
                return (
                  <motion.div key={pr.id} data-provider={pr.id} data-picked={on ? 'true' : 'false'} layout
                    initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} transition={{ ...SPRING, delay: i * 0.05 }}
                    onClick={() => s.setProvider(pr)} whileHover={{ y: -1 }} whileTap={{ scale: 0.98 }}
                    style={{ border: `1px solid ${on ? P.ink : P.line}`, background: on ? P.hair : P.bg, padding: '6px 8px', cursor: 'pointer' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                      <span style={{ display: 'inline-flex', color: on ? P.ink : P.faint }}><IconPlugConnected size={12} stroke={1.9} /></span>
                      <span style={{ fontFamily: SANS, fontSize: 11.5, fontWeight: 600, color: P.ink }}>{pr.name}</span>
                      {on && <motion.span layout style={{ marginLeft: 'auto', display: 'inline-flex', color: P.ink }}><IconCheck size={12} stroke={2.6} /></motion.span>}
                    </div>
                    <div style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, marginTop: 3 }}>{pr.host} · {pr.note}</div>
                  </motion.div>
                )
              })}
            </div>
          </motion.div>

          {/* 2 — Schluessel */}
          <motion.div animate={{ opacity: s.provider ? 1 : 0.4 }} transition={SPRING}>
            <div style={{ fontFamily: MONO, fontSize: TEXT.meta, letterSpacing: 1, color: P.sub, marginBottom: 6 }}>SCHLUESSEL</div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, border: `1px solid ${P.line}`, background: P.bg, padding: '0 8px', height: 34 }}>
              <IconKey size={12} stroke={1.9} color={P.faint} />
              <input data-keyinput value={s.key} disabled={!s.provider || !s.needKey}
                onChange={e => s.setKey(e.target.value)} type={s.reveal ? 'text' : 'password'}
                placeholder={!s.provider ? 'zuerst einen Anbieter waehlen' : s.needKey ? `${p.hint}` : 'kein Schluessel noetig'}
                style={{ flex: 1, minWidth: 0, border: 'none', outline: 'none', background: 'transparent', fontFamily: MONO, fontSize: 11, color: P.ink }} />
              <span style={{ display: 'inline-flex', color: P.faint, cursor: 'pointer' }} onClick={() => s.setReveal(r => !r)}>
                {s.reveal ? <IconEyeOff size={12} stroke={1.9} /> : <IconEye size={12} stroke={1.9} />}
              </span>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 5, fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>
              <IconLock size={10} stroke={1.9} />
              <span>Der Schluessel bleibt auf diesem Rechner — gesendet wird nur an {p ? p.host : 'den Anbieter'}.</span>
            </div>
          </motion.div>

          {/* 3 — Pruefen */}
          <motion.div animate={{ opacity: s.keyThere ? 1 : 0.4 }} transition={SPRING}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
              <Btn P={P} attr="pruefen" label={s.check === 'ok' ? 'erneut pruefen' : 'Verbindung pruefen'}
                Icon={s.check === 'ok' ? IconRefresh : IconScan} primary
                disabled={!s.keyThere || s.check === 'running'} onClick={s.runCheck} />
              {s.check === 'running' && <ThinkingOrb state="connecting" size={20} theme={orbTheme} />}
              {s.check === 'ok' && (
                <motion.span data-checkok initial={{ opacity: 0, x: -4 }} animate={{ opacity: 1, x: 0 }} transition={SPRING}
                  style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontFamily: MONO, fontSize: TEXT.meta, color: P.ink }}>
                  <IconShieldCheck size={12} stroke={1.9} />
                  {p.host} antwortete in 240 ms — {s.runs}. Pruefung bestanden
                </motion.span>
              )}
              {s.check === 'running' && (
                <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub }}>{Math.round(s.progress)} %</span>
              )}
              <span style={{ marginLeft: 'auto' }}>
                <Btn P={P} attr="zuruecksetzen" label="zuruecksetzen" Icon={IconRotateClockwise} onClick={s.reset} />
              </span>
            </div>
            {(s.check === 'running' || s.check === 'ok') && (
              <div style={{ marginTop: 8 }}>
                <FillBar P={P} pct={s.check === 'ok' ? 100 : s.progress} />
              </div>
            )}
          </motion.div>

          {/* 4 — Bereit */}
          <AnimatePresence>
            {s.check === 'ok' && (
              <motion.div key="fertig" data-setupready initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: 'auto' }} exit={{ opacity: 0, height: 0 }}
                transition={SPRING} style={{ overflow: 'hidden' }}>
                <div style={{ border: `1.5px solid ${P.ink}`, padding: '8px 10px', display: 'flex', alignItems: 'center', gap: 8, background: P.bg }}>
                  <IconCircleCheck size={14} stroke={1.9} color={P.ink} />
                  <span style={{ fontFamily: SANS, fontSize: 12, fontWeight: 600, color: P.ink }}>Alles bereit — der erste Start ist geschafft.</span>
                  <span style={{ marginLeft: 'auto' }}><Btn P={P} attr="ersteziel" label="erstes Ziel setzen" Icon={IconArrowRight} primary i={1} /></span>
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </div>
    </div>
  )
}

/* ------------------------------------------------------- Diagnose (02/09) */

const CHECKS = [
  { id: 'app', name: 'Programm', detail: 'skaldir 0.9.3 · /usr/local/bin/skaldir', okText: 'laeuft', failText: 'fehlt', Icon: IconCpu },
  { id: 'service', name: 'Dienst', detail: '127.0.0.1:5050 · PID 2345', okText: 'erreichbar', failText: 'unten', Icon: IconServer },
  { id: 'provider', name: 'Anbieter', detail: 'api.openai.com · 240 ms', okText: 'antwortet', failText: 'stumm', Icon: IconWorld },
  { id: 'net', name: 'Netz', detail: 'DNS ok · Ausgang offen', okText: 'offen', failText: 'zu', Icon: IconWifi },
  { id: 'key', name: 'Schluessel', detail: 'hinterlegt · gueltig bis 12.11.', okText: 'gueltig', failText: 'fehlt', Icon: IconKey },
  { id: 'folder', name: 'Ordner', detail: '~/.skaldir · 1,2 GB frei', okText: 'beschreibbar', failText: 'gesperrt', Icon: IconFolderCheck }
]

const DIAG_LABEL = { idle: '—', running: 'prueft', ok: 'gruen', fail: 'fehlt' }

/* Echter Zustand der Diagnose: sechs Pruefungen laufen wirklich durch. */
function useDiag(auto = true) {
  const [state, setState] = useState(() => Object.fromEntries(CHECKS.map(c => [c.id, auto ? 'running' : 'idle'])))
  const [folderOk, setFolderOk] = useState(true)
  const [rounds, setRounds] = useState(0)
  const ctrls = useRef([])
  const started = useRef(false)

  const stopAll = () => { ctrls.current.forEach(c => c && c.stop && c.stop()); ctrls.current = [] }

  const verdict = (id, folder) => (id === 'folder' && !folder ? 'fail' : 'ok')

  const run = (ids, folder = folderOk) => {
    stopAll()
    const list = ids || CHECKS.map(c => c.id)
    list.forEach((id, i) => {
      const c = animate(0, 1, {
        duration: 0.5, delay: i * 0.24, ease: 'linear',
        onUpdate: () => setState(s => (s[id] === 'running' ? s : { ...s, [id]: 'running' })),
        onComplete: () => setState(s => ({ ...s, [id]: verdict(id, folder) }))
      })
      ctrls.current.push(c)
    })
    setRounds(r => r + 1)
  }

  useEffect(() => {
    if (!auto || started.current) return
    started.current = true
    run()
    return () => stopAll()
  }, [])

  const toggleFolder = () => {
    const next = !folderOk
    setFolderOk(next)
    setState(s => ({ ...s, folder: 'idle' }))
    run(['folder'], next)
  }

  const green = CHECKS.filter(c => state[c.id] === 'ok').length
  const running = CHECKS.some(c => state[c.id] === 'running')
  return { state, run, stopAll, rounds, green, running, folderOk, toggleFolder }
}

/** Eine Pruefungszeile — Klick prueft genau diese Zeile erneut. */
const DiagRow = ({ c, st, P, i, onCheck, compact = false }) => (
  <motion.div data-diagrow={c.id} data-state={st} layout
    initial={{ opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }} transition={{ ...SPRING, delay: i * 0.03 }}
    onClick={() => onCheck(c.id)} whileHover={{ backgroundColor: P.hair }}
    style={{ display: 'flex', alignItems: 'center', gap: 9, padding: compact ? '5px 8px' : '7px 10px', borderBottom: `1px solid ${P.hair}`, cursor: 'pointer' }}>
    <span style={{ width: 15, display: 'inline-flex', justifyContent: 'center', flex: '0 0 auto' }}>
      <StateMark P={P} st={st} size={compact ? 12 : 13} />
    </span>
    <span style={{ display: 'inline-flex', color: st === 'idle' ? P.faint : P.ink, flex: '0 0 auto' }}>
      <c.Icon size={compact ? 12 : 14} strokeWidth={1.8} />
    </span>
    <span style={{ fontFamily: SANS, fontSize: compact ? 11.5 : 12.5, fontWeight: 600, color: P.ink, width: compact ? 78 : 92, flex: '0 0 auto' }}>{c.name}</span>
    <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.detail}</span>
    <span style={{ fontFamily: MONO, fontSize: TEXT.meta, width: compact ? 84 : 92, textAlign: 'right', flex: '0 0 auto', color: st === 'idle' ? P.faint : P.ink }}>
      {st === 'ok' ? c.okText : st === 'fail' ? c.failText : DIAG_LABEL[st]}
    </span>
  </motion.div>
)

/* ------------------------------------------------------- Varianten 01 und 02 */

export function V01() {
  return (
    <Case name="01 · Erster Start: Verbindung einrichten"
      note="echte Schrittfolge — Anbieter waehlen, Schluessel einsetzen, pruefen. Jeder Schritt hakt sich wirklich ab.">
      {(P) => <SetupPanel P={P} />}
    </Case>
  )
}

export function V02() {
  const d = useDiag(true)
  return (
    <Case name="02 · Diagnose: sechs Pruefungen"
      note="Programm · Dienst · Anbieter · Netz · Schluessel · Ordner — jede Zeile laeuft einzeln, Klick prueft genau diese Zeile erneut.">
      {(P) => (
        <div data-diagnose style={{ border: `1px solid ${P.line}`, background: P.surface }}>
          <KastenKopf P={P} label="DIAGNOSE"
            right={
              <>
                <motion.span data-diagcount key={d.green} initial={{ opacity: 0, y: -5 }} animate={{ opacity: 1, y: 0 }} transition={SPRING}
                  style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.ink }}>
                  {d.green} von 6 gruen
                </motion.span>
                <Btn P={P} attr="erneut" label="erneut pruefen" Icon={IconRefresh} onClick={() => d.run()} i={1} />
              </>
            } />
          {CHECKS.map((c, i) => <DiagRow key={c.id} c={c} st={d.state[c.id]} P={P} i={i} onCheck={(id) => d.run([id])} />)}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 10px', background: P.bg, borderTop: `1px solid ${P.hair}` }}>
            <motion.span animate={{ opacity: d.running ? 1 : 0.35 }} transition={SPRING} style={{ display: 'inline-flex', color: P.ink }}>
              <IconActivity size={13} stroke={1.9} />
            </motion.span>
            <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub }}>
              {d.running ? 'Pruefung laeuft …' : d.green === 6 ? 'Alles gruen — Skaldir ist bereit.' : `${6 - d.green} Pruefung(en) offen.`}
              {' '}Lauf Nr. {d.rounds} · 6 Pruefungen, kein Auftrag gesendet
            </span>
            <span style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 7 }}>
              <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub }}>Ordner wegnehmen</span>
              <motion.span data-foldertoggle onClick={d.toggleFolder} whileTap={{ scale: 0.9 }} transition={SPRING}
                animate={{ backgroundColor: d.folderOk ? P.surface : P.ink, borderColor: d.folderOk ? P.line : P.ink, justifyContent: d.folderOk ? 'flex-start' : 'flex-end' }}
                style={{ width: 34, height: 18, borderRadius: RADIUS.pill, border: `1px solid ${P.line}`, padding: 2, boxSizing: 'border-box', display: 'inline-flex', alignItems: 'center', cursor: 'pointer' }}>
                <motion.span layout transition={SPRING} style={{ width: 12, height: 12, borderRadius: RADIUS.pill, background: d.folderOk ? P.faint : P.bg }} />
              </motion.span>
            </span>
          </div>
        </div>
      )}
    </Case>
  )
}

/* ------------------------------------------------------- 03 Startfehler */

export function V03() {
  const [phase, setPhase] = useState('fehler')   // fehler | laeuft | bereit
  const [stelle, setStelle] = useState(false)
  const [pct, setPct] = useState(0)
  const ctrl = useRef(null)
  useEffect(() => () => { if (ctrl.current) ctrl.current.stop() }, [])

  const retry = () => {
    if (phase === 'laeuft') return
    if (ctrl.current) ctrl.current.stop()
    setPhase('laeuft'); setPct(0)
    ctrl.current = animate(0, 100, { duration: 1.3, ease: 'easeInOut', onUpdate: v => setPct(v), onComplete: () => setPhase('bereit') })
  }

  return (
    <Case name="03 · Fehler beim Start" note="verstaendlicher Satz, genauer Ort (Datei und Zeile), ein Weg vorwaerts.">
      {(P) => (
        <div style={{ maxWidth: 640 }}>
          <motion.div data-starterror initial={{ opacity: 0, scale: 0.97, y: 6 }} animate={{ opacity: 1, scale: 1, y: 0 }} transition={SPRING}
            style={{ border: `1.5px solid ${P.ink}`, background: P.surface, padding: '11px 13px', transformOrigin: 'bottom left' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <Kreuz P={P} />
              <span style={{ fontFamily: SANS, fontSize: 12.5, fontWeight: 600, color: P.ink }}>
                {phase === 'bereit' ? 'Skaldir startet' : 'Skaldir konnte nicht starten'}
              </span>
              <span style={{ marginLeft: 'auto', display: 'inline-flex', color: phase === 'bereit' ? P.ink : P.sub }}>
                {phase === 'bereit' ? <IconCircleCheck size={14} stroke={1.9} /> : phase === 'laeuft' ? <IconLoader2 size={14} stroke={1.9} /> : <IconWifiOff size={14} stroke={1.9} />}
              </span>
            </div>

            <div style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, lineHeight: 1.6, margin: '6px 0 8px' }}>
              {phase === 'bereit'
                ? 'Der Schluessel wurde angenommen. Nichts weiter zu tun — die Sitzung kann beginnen.'
                : 'Der Anbieter hat den hinterlegten Schluessel abgelehnt (HTTP 401).\nDer Eintrag steht in ~/.skaldir/config.toml, Zeile 24 — es wurde nichts gesendet.'}
            </div>

            {/* Genaue Stelle — aufklappbar, mit hervorgehobener Zeile */}
            <motion.span data-zeigestelle onClick={() => setStelle(s => !s)} whileTap={{ scale: 0.96 }} transition={SPRING}
              style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontFamily: MONO, fontSize: TEXT.meta, color: P.ink, cursor: 'pointer', paddingBottom: 6 }}>
              <motion.span animate={{ rotate: stelle ? 90 : 0 }} transition={SPRING} style={{ display: 'inline-flex' }}><IconChevronRight size={11} stroke={2} /></motion.span>
              genaue Stelle zeigen
            </motion.span>
            <AnimatePresence initial={false}>
              {stelle && (
                <motion.div key="stelle" initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }} transition={SPRING}
                  style={{ overflow: 'hidden' }}>
                  <div style={{ border: `1px solid ${P.line}`, background: P.bg, padding: '7px 9px', fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, lineHeight: 1.7 }}>
                    <div style={{ color: P.faint }}>~/.skaldir/config.toml</div>
                    <div>22 │ [anbieter.openai]</div>
                    <div>23 │ aktiv = true</div>
                    <motion.div animate={{ backgroundColor: stelle ? P.hair : 'transparent' }} transition={{ duration: 0.25 }}
                      style={{ color: P.ink, fontWeight: 600 }}>24 │ schluessel = \"sk-…4f2a\"  ← abgelehnt</motion.div>
                    <div>25 │ modell = \"gpt-4o-mini\"</div>
                  </div>
                </motion.div>
              )}
            </AnimatePresence>

            {(phase === 'laeuft' || phase === 'bereit') && <div style={{ margin: '8px 0' }}><FillBar P={P} pct={phase === 'bereit' ? 100 : pct} /></div>}

            <div style={{ display: 'flex', gap: 7, flexWrap: 'wrap', alignItems: 'center', marginTop: 4 }}>
              <Btn P={P} attr="schluesselneu" label={phase === 'bereit' ? 'Schluessel erneuern' : 'Schluessel in den Einstellungen erneuern'}
                Icon={IconKey} primary i={0} disabled={phase === 'laeuft'} onClick={retry} />
              <Btn P={P} attr="ohneanbieter" label="ohne Anbieter starten" Icon={IconPlayerPlay} i={1} />
              <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>
                {phase === 'bereit' ? '1 Weg gegangen.' : '1 Weg vorwaerts — der Rest bleibt offen.'}
              </span>
            </div>
          </motion.div>

          {/* Was schon steht */}
          <div style={{ marginTop: 10, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {[
              { Icon: IconFileText, t: 'Sitzung gesichert', d: '3 Nachrichten liegen bereit' },
              { Icon: IconFolderOpen, t: 'Arbeitsordner', d: '~/.skaldir · unveraendert' },
              { Icon: IconSend, t: 'Gesendet', d: 'nichts — 0 Byte hinaus' }
            ].map((x, i) => (
              <motion.div key={x.t} initial={{ opacity: 0, y: 5 }} animate={{ opacity: 1, y: 0 }} transition={{ ...SPRING, delay: 0.1 + i * 0.05 }}
                style={{ border: `1px solid ${P.line}`, background: P.surface, padding: '7px 9px', minWidth: 150 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, color: P.ink }}>
                  <x.Icon size={12} strokeWidth={1.9} />
                  <span style={{ fontFamily: SANS, fontSize: 11.5, fontWeight: 600 }}>{x.t}</span>
                </div>
                <div style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, marginTop: 3 }}>{x.d}</div>
              </motion.div>
            ))}
          </div>
        </div>
      )}
    </Case>
  )
}

/* ------------------------------------------------------- 04 Schluessel fehlt */

export function V04() {
  const [key, setKey] = useState('')
  const [state, setState] = useState('leer')    // leer | prueft | hinterlegt
  const [link, setLink] = useState(false)
  const ctrl = useRef(null)
  useEffect(() => () => { if (ctrl.current) ctrl.current.stop() }, [])
  const pruefen = () => {
    if (!key.trim() || state === 'prueft') return
    if (ctrl.current) ctrl.current.stop()
    setState('prueft')
    ctrl.current = animate(0, 1, { duration: 1.1, delay: 0.15, onComplete: () => setState('hinterlegt') })
  }
  return (
    <Case name="04 · Schluessel fehlt" note="wohin er gehoert, der Link2 zum Holen, und der Hinweis, dass nichts gesendet wurde.">
      {(P) => (
        <div style={{ maxWidth: 620 }}>
          <motion.div data-keymissing initial={{ opacity: 0, scale: 0.97, y: 6 }} animate={{ opacity: 1, scale: 1, y: 0 }} transition={SPRING}
            style={{ border: `1.5px solid ${P.ink}`, background: P.surface, padding: '11px 13px' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <Kreuz P={P} />
              <span style={{ fontFamily: SANS, fontSize: 12.5, fontWeight: 600, color: P.ink }}>Kein API-Schluessel hinterlegt</span>
            </div>

            {/* Wohin er gehoert */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, margin: '8px 0 6px', flexWrap: 'wrap' }}>
              <IconSettings size={12} stroke={1.9} color={P.sub} />
              <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.ink }}>Einstellungen</span>
              <IconChevronRight size={11} stroke={2} color={P.faint} />
              <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.ink }}>Anbieter</span>
              <IconChevronRight size={11} stroke={2} color={P.faint} />
              <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.ink, border: `1px solid ${P.line}`, padding: '1px 5px' }}>API-Schluessel</span>
            </div>

            {/* IconLink zum Holen */}
            <motion.span data-keylink onClick={() => setLink(true)} whileHover={{ x: 2 }} transition={SPRING}
              style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontFamily: MONO, fontSize: TEXT.meta, color: link ? P.sub : P.ink, cursor: 'pointer', textDecoration: 'underline' }}>
              <IconExternalLink size={11} stroke={1.9} />
              platform.openai.com/api-keys{link ? ' · als Naechstes geoeffnet' : ' — Schluessel holen'}
            </motion.span>

            {/* Einsetzen */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, border: `1px solid ${P.line}`, background: P.bg, padding: '0 8px', height: 34, marginTop: 10 }}>
              <IconKey size={12} stroke={1.9} color={P.faint} />
              <input data-keyinput2 value={key} onChange={e => setKey(e.target.value)} type="password" placeholder="sk-… hier einsetzen"
                style={{ flex: 1, minWidth: 0, border: 'none', outline: 'none', background: 'transparent', fontFamily: MONO, fontSize: 11, color: P.ink }} />
              <AnimatePresence>
                {state === 'prueft' && (
                  <motion.span key="orb" initial={{ opacity: 0, scale: 0.6 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0, scale: 0.6 }} transition={SPRING}>
                    <ThinkingOrb state="listening" size={20} theme="light" />
                  </motion.span>
                )}
              </AnimatePresence>
            </div>

            <div style={{ display: 'flex', gap: 7, flexWrap: 'wrap', alignItems: 'center', marginTop: 9 }}>
              <Btn P={P} attr="pruefenkey" label={state === 'hinterlegt' ? 'Schluessel hinterlegt' : 'Schluessel uebernehmen'}
                Icon={state === 'hinterlegt' ? IconCircleCheck : IconLink} primary i={0}
                disabled={!key.trim() || state === 'prueft' || state === 'hinterlegt'} onClick={pruefen} />
              <Btn P={P} attr="spaeter" label="spaeter" i={1} />
            </div>

            {/* Der wichtige Hinweis */}
            <motion.div animate={{ opacity: state === 'hinterlegt' ? 0.5 : 1 }} transition={SPRING}
              style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, marginTop: 9, display: 'flex', alignItems: 'center', gap: 6, lineHeight: 1.6 }}>
              <IconSend size={11} stroke={1.9} color={P.faint} />
              <span>
                {state === 'hinterlegt'
                  ? 'Nur die Pruefung ging hinaus — kein Auftrag, kein Text, keine Datei.'
                  : 'Es wurde nichts gesendet: die Anfrage wartet hier, bis ein Schluessel steht.'}
              </span>
            </motion.div>
          </motion.div>
        </div>
      )}
    </Case>
  )
}

/* ------------------------------------------------------- 05 Dienst laeuft nicht */

export function V05() {
  const CMD = 'skaldir service status --json'
  const [copied, setCopied] = useState(false)
  const [svc, setSvc] = useState('unten')      // unten | prueft | oben
  const [later, setLater] = useState(false)
  const [tries, setTries] = useState(0)
  const ctrl = useRef(null)
  useEffect(() => () => { if (ctrl.current) ctrl.current.stop() }, [])

  const probe = () => {
    if (svc === 'prueft') return
    if (ctrl.current) ctrl.current.stop()
    setSvc('prueft')
    ctrl.current = animate(0, 1, { duration: 1.2, delay: 0.1, onComplete: () => { setSvc('oben'); setTries(t => t + 1) } })
  }
  const copy = () => {
    setCopied(true)
    try { if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(CMD) } catch (e) { /* still */ }
  }

  return (
    <Case name="05 · Dienst laeuft nicht" note="was zu tun ist, der Befehl zum Nachlesen, und ein spaeterer Versuch.">
      {(P) => (
        <div style={{ maxWidth: 640 }}>
          <motion.div data-servicedown initial={{ opacity: 0, scale: 0.97, y: 6 }} animate={{ opacity: 1, scale: 1, y: 0 }} transition={SPRING}
            style={{ border: `1.5px solid ${P.ink}`, background: P.surface, padding: '11px 13px' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <CrossOrCheck P={P} up={svc === 'oben'} />
              <span style={{ fontFamily: SANS, fontSize: 12.5, fontWeight: 600, color: P.ink }}>
                {svc === 'oben' ? 'Dienst laeuft wieder' : 'Der Dienst laeuft nicht'}
              </span>
              <span style={{ marginLeft: 'auto', fontFamily: MONO, fontSize: TEXT.meta, color: P.sub }}>
                {svc === 'unten' ? '127.0.0.1:5050 · keine Antwort' : svc === 'prueft' ? 'Klopfe an 127.0.0.1:5050 …' : '127.0.0.1:5050 · PID 2345'}
              </span>
            </div>

            <div style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, lineHeight: 1.6, margin: '6px 0 9px' }}>
              {svc === 'oben'
                ? 'Der Dienst nimmt wieder an. Der Chat laeuft weiter — es wurde nichts gesendet.'
                : 'Was zu tun ist: den Dienst neu starten. Er laeuft auf diesem Rechner und braucht kein Netz.'}
            </div>

            {/* Befehl zum Nachlesen */}
            <div style={{ border: `1px solid ${P.line}`, background: P.bg }}>
              <KastenKopf P={P} label="BEFEHL ZUM NACHLESEN"
                right={
                  <motion.span data-copy onClick={copy} whileTap={{ scale: 0.94 }} transition={SPRING}
                    style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontFamily: MONO, fontSize: TEXT.meta, color: P.ink, cursor: 'pointer' }}>
                    {copied ? <IconCheck size={11} stroke={2.4} /> : <IconCopy size={11} stroke={1.9} />}
                    {copied ? 'kopiert' : 'kopieren'}
                  </motion.span>
                } />
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '9px 10px', fontFamily: MONO, fontSize: 11, color: P.ink }}>
                <span style={{ color: P.faint }}>$</span>
                <motion.span animate={{ opacity: [1, 0.55, 1] }} transition={{ duration: 2.4, repeat: Infinity, ease: 'easeInOut' }}>{CMD}</motion.span>
                <IconTerminal size={12} stroke={1.9} color={P.faint} style={{ marginLeft: 'auto' }} />
              </div>
              <div style={{ padding: '0 10px 9px', fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, lineHeight: 1.6 }}>
                Erwartet: <span style={{ color: P.ink }}>\"zustand\": \"laeuft\"</span> · bei \"unten\" zeigt der Befehl den letzten Fehler mit Zeitstempel.
              </div>
            </div>

            {svc === 'prueft' && (
              <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={SPRING} style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 9 }}>
                <ThinkingOrb state="searching" size={20} theme="light" />
                <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub }}>Klopfe an · Versuch {tries + 1}</span>
              </motion.div>
            )}

            <div style={{ display: 'flex', gap: 7, flexWrap: 'wrap', alignItems: 'center', marginTop: 10 }}>
              <Btn P={P} attr="dienstpruefen" label={svc === 'oben' ? 'noch einmal pruefen' : 'Dienst jetzt pruefen'}
                Icon={svc === 'oben' ? IconRefresh : Server} primary i={0} disabled={svc === 'prueft'} onClick={probe} />
              <Btn P={P} attr="spaetererneut" label="spaeter erneut" Icon={IconClock} i={1} onClick={() => setLater(l => !l)} />
              <Btn P={P} attr="anleitung" label="Anleitung" Icon={IconBook} i={2} />
              {tries > 0 && (
                <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>
                  {tries} Versuch{tries === 1 ? '' : 'e'} von Hand · nichts gesendet
                </span>
              )}
            </div>

            <AnimatePresence initial={false}>
              {later && (
                <motion.div key="spaeter" data-later initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }} transition={SPRING}
                  style={{ overflow: 'hidden' }}>
                  <div style={{ marginTop: 9, border: `1px solid ${P.line}`, background: P.bg, padding: '7px 9px', display: 'flex', alignItems: 'center', gap: 8 }}>
                    <IconClock size={12} stroke={1.9} color={P.ink} />
                    <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, lineHeight: 1.6 }}>
                      Erinnerung gesetzt: in 5 Minuten fragen wir noch einmal nach. Bis dahin bleibt alles stehen, wie es ist.
                    </span>
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
          </motion.div>
        </div>
      )}
    </Case>
  )
}

/** Kreuz oder Haken je nach Dienst-Zustand. */
const CrossOrCheck = ({ P, up }) => (
  <AnimatePresence mode="wait" initial={false}>
    <motion.span key={up ? 'up' : 'down'} initial={{ opacity: 0, scale: 0.5 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0, scale: 0.5 }} transition={SPRING}
      style={{ display: 'inline-flex', color: P.ink }}>
      {up ? <IconShieldCheck size={15} stroke={1.9} /> : <IconWifiOff size={15} stroke={1.9} />}
    </motion.span>
  </AnimatePresence>
)

/* ------------------------------------------------------- 06 Erste Schritte */

const ERSTE = [
  { id: 'ziel', name: 'Ziel setzen', desc: 'Ein Satz, den der Agent abarbeitet', meta: '2 Beispielziele liegen bereit', Icon: IconTarget },
  { id: 'werkzeug', name: 'Werkzeug anhaengen', desc: 'Dateien, Terminal, Browser', meta: '4 Werkzeuge verfuegbar', Icon: IconTool },
  { id: 'sitzung', name: 'Sitzung fortsetzen', desc: 'Zuletzt: „Panel-Rahmen pruefen“', meta: 'von gestern, 18:04', Icon: IconPlayerPlay }
]

export function V06() {
  const [done, setDone] = useState({})
  const count = ERSTE.filter(x => done[x.id]).length
  const toggle = (id) => setDone(d => ({ ...d, [id]: !d[id] }))
  return (
    <Case name="06 · Erste Schritte: drei Karten" note="Klick erledigt wirklich — der Zaehler unten zaehlt mit.">
      {(P) => (
        <div data-firststeps style={{ border: `1px solid ${P.line}`, background: P.bg, padding: 12 }}>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 10 }}>
            {ERSTE.map((x, i) => {
              const on = !!done[x.id]
              return (
                <motion.div key={x.id} data-step={x.id} data-done={on ? 'true' : 'false'} layout
                  initial={{ opacity: 0, scale: 0.96 }} animate={{ opacity: 1, scale: 1 }} transition={{ ...SPRING, delay: i * 0.06 }}
                  onClick={() => toggle(x.id)} whileHover={{ y: -2 }} whileTap={{ scale: 0.98 }}
                  style={{ border: `1px solid ${on ? P.ink : P.line}`, background: P.surface, padding: '10px 11px', cursor: 'pointer', userSelect: 'none' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
                    <Tick P={P} on={on} />
                    <span style={{ display: 'inline-flex', color: on ? P.ink : P.sub }}><x.Icon size={13} strokeWidth={1.9} /></span>
                    <span style={{ fontFamily: SANS, fontSize: 12, fontWeight: 600, color: P.ink }}>{x.name}</span>
                    <span style={{ marginLeft: 'auto', display: 'inline-flex', color: on ? P.ink : P.faint }}>
                      {on ? <IconCheck size={13} stroke={2.6} /> : <IconChevronRight size={13} stroke={2} />}
                    </span>
                  </div>
                  <div style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, marginTop: 7, lineHeight: 1.55 }}>{x.desc}</div>
                  <div style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint, marginTop: 5 }}>{x.meta}</div>
                </motion.div>
              )
            })}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 9, marginTop: 11, paddingTop: 10, borderTop: `1px solid ${P.hair}` }}>
            <IconListCheck size={13} stroke={1.9} color={P.sub} />
            <motion.span key={count} initial={{ opacity: 0, y: -5 }} animate={{ opacity: 1, y: 0 }} transition={SPRING}
              style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.ink }}>
              {count} von 3 erledigt
            </motion.span>
            <span style={{ flex: 1, maxWidth: 220 }}><FillBar P={P} pct={(count / 3) * 100} /></span>
            <span style={{ marginLeft: 'auto' }}>
              <Btn P={P} attr="arbeiten" label={count === 3 ? 'Los geht es' : 'Diese Karten sind waehlbar'} Icon={IconArrowRight}
                primary={count === 3} dim={count !== 3} i={0} disabled={count !== 3} />
            </span>
          </div>
        </div>
      )}
    </Case>
  )
}

/* ------------------------------------------------------- 07 Willkommen */

const ZIELE = [
  'Give me a goal I actually complete it.',
  'Repair the start error without touching anything else.',
  'Set up the key and prove it works before Friday.'
]

export function V07() {
  const [ziel, setZiel] = useState(null)
  const [gestartet, setGestartet] = useState(false)
  const start = () => { if (ziel) setGestartet(true) }
  return (
    <Case name="07 · Willkommen mit Beispielzielen" note="drei Saetze zum Anklicken — das gewaehlte Ziel wandert wirklich in die Leiste.">
      {(P) => (
        <div data-welcome style={{ height: 400, border: `1px solid ${P.line}`, background: P.bg, display: 'flex', flexDirection: 'column' }}>
          <div style={{ flex: 1, padding: 18, display: 'flex', flexDirection: 'column', justifyContent: 'center', gap: 14 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <IconCompass size={15} stroke={1.9} color={P.ink} />
              <span style={{ fontFamily: SANS, fontSize: 15, fontWeight: 600, color: P.ink }}>Womit fangen wir an?</span>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8, maxWidth: 560 }}>
              {ZIELE.map((z, i) => {
                const on = ziel === z
                return (
                  <motion.div key={z} data-goal={i} data-picked={on ? 'true' : 'false'} layout
                    initial={{ opacity: 0, x: -8 }} animate={{ opacity: 1, x: 0 }} transition={{ ...SPRING, delay: i * 0.07 }}
                    onClick={() => { setZiel(z); setGestartet(false) }}
                    whileHover={{ x: 3 }} whileTap={{ scale: 0.99 }}
                    style={{ display: 'flex', alignItems: 'center', gap: 8, border: `1px solid ${on ? P.ink : P.line}`, background: on ? P.surface : 'transparent', padding: '8px 10px', cursor: 'pointer' }}>
                    <span style={{ display: 'inline-flex', color: on ? P.ink : P.faint }}>
                      {on ? <IconTarget size={13} stroke={1.9} /> : <IconCircle size={13} stroke={1.6} />}
                    </span>
                    <span style={{ fontFamily: SANS, fontSize: 12.5, color: P.ink }}>{z}</span>
                    {on && <motion.span layout style={{ marginLeft: 'auto', display: 'inline-flex', color: P.ink }}><IconCheck size={13} stroke={2.6} /></motion.span>}
                  </motion.div>
                )
              })}
            </div>

            <AnimatePresence>
              {gestartet && (
                <motion.div key="lauf" data-goalrunning initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 6 }} transition={SPRING}
                  style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
                  <ThinkingOrb state="composing" size={20} theme="light" />
                  <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub }}>
                    Ziel uebernommen · ich melde den ersten Schritt, bevor ich etwas anfasse.
                  </span>
                </motion.div>
              )}
            </AnimatePresence>
          </div>

          {/* Leiste */}
          <div style={{ padding: 12 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, height: 52, borderRadius: RADIUS.pill, border: `1px solid ${P.line}`, background: P.surface, padding: '0 14px' }}>
              <IconClick size={13} stroke={1.9} color={P.faint} />
              <span style={{ flex: 1, minWidth: 0, fontFamily: SANS, fontSize: 13, color: ziel ? P.ink : P.faint, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {ziel || 'Beispielziel anklicken oder selbst schreiben'}
              </span>
              <motion.span data-goalstart onClick={start} whileHover={ziel ? { scale: 1.12 } : undefined} whileTap={ziel ? { scale: 0.9 } : undefined} transition={SPRING}
                animate={{ backgroundColor: ziel ? P.ink : 'transparent', color: ziel ? P.bg : P.faint, borderColor: ziel ? P.ink : P.line }}
                style={{ width: 30, height: 30, borderRadius: RADIUS.pill, border: `1px solid ${P.line}`, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', cursor: ziel ? 'pointer' : 'default', flex: '0 0 auto' }}>
                <IconArrowUpRight size={14} stroke={2} />
              </motion.span>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 7, fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>
              <IconBolt size={10} stroke={1.9} />
              <span>{gestartet ? 'Sitzung angelegt · 0 Werkzeuge im Griff' : 'Kein Anbieter verbunden — Ziele kann man trotzdem setzen.'}</span>
            </div>
          </div>
        </div>
      )}
    </Case>
  )
}

/* ------------------------------------------------------- 08 Fortschrittsleiste */

export function V08() {
  const s = useSetup()
  const pct = (Math.min(s.stepIndex, 3) / 3) * 100
  return (
    <Case name="08 · Fortschritt des Einrichtens als Leiste" note="vier Schritte, die Fuellung faehrt mit Feder — der Fortschritt kommt aus dem echten Zustand.">
      {(P) => (
        <div data-progressbar style={{ border: `1px solid ${P.line}`, background: P.surface, padding: '16px 44px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
            <IconRoute size={14} stroke={1.9} color={P.ink} />
            <span style={{ fontFamily: SANS, fontSize: 13, fontWeight: 600, color: P.ink }}>Einrichtung</span>
            <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub }}>
              {Math.min(s.stepIndex + 1, 4)} von 4 · {Math.round(pct)} %
            </span>
            <span style={{ marginLeft: 'auto' }}>
              <Btn P={P} attr="reset8" label="zuruecksetzen" Icon={IconRotateClockwise} onClick={s.reset} />
            </span>
          </div>

          {/* Die Leiste */}
          <div style={{ position: 'relative', margin: '22px 0 0' }}>
            <FillBar P={P} pct={pct} height={5} />
            <div style={{ position: 'absolute', inset: '-9px 0 0', display: 'flex', justifyContent: 'space-between', pointerEvents: 'none' }}>
              {SETUP_STEPS.map((st, i) => {
                const stDone = s.done[st.id]
                const now = i === s.stepIndex
                return (
                  <div key={st.id} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6, width: 4 }}>
                    <motion.span animate={{ backgroundColor: stDone ? P.ink : now ? P.surface : 'transparent', borderColor: stDone || now ? P.ink : P.line, scale: now ? 1.18 : 1 }} transition={SPRING}
                      style={{ width: 14, height: 14, borderRadius: RADIUS.pill, border: `1px solid ${P.line}`, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', color: P.bg }}>
                      {stDone && <IconCheck size={9} stroke={3.2} />}
                    </motion.span>
                    <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: stDone || now ? P.ink : P.faint, whiteSpace: 'nowrap', marginTop: 2 }}>{st.name}</span>
                  </div>
                )
              })}
            </div>
          </div>

          <div style={{ marginTop: 62 }}>
            <SetupPanel P={P} compact dark={false} />
          </div>
        </div>
      )}
    </Case>
  )
}

/* ------------------------------------------------------- 09 kompakte Zeile */

export function V09() {
  const d = useDiag(true)
  const [open, setOpen] = useState(false)
  const fertig = d.green === 6
  return (
    <Case name="09 · Kompakt: eine Zeile zum Aufklappen" note="eine Zeile „Alles bereit — 6 von 6 Pruefungen gruen“, Klick klappt die sechs Pruefungen auf.">
      {(P) => (
        <div data-compactline style={{ maxWidth: 620 }}>
          <div style={{ border: `1px solid ${P.line}`, background: P.surface }}>
            <motion.div data-compacthead layout onClick={() => setOpen(o => !o)} whileHover={{ backgroundColor: P.hair }}
              style={{ display: 'flex', alignItems: 'center', gap: 9, padding: '8px 11px', cursor: 'pointer' }}>
              <StateMark P={P} st={fertig ? 'ok' : 'running'} size={13} />
              <motion.span key={fertig ? 'ja' : 'nein'} initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }} transition={SPRING}
                style={{ fontFamily: MONO, fontSize: 11, color: P.ink }}>
                {fertig ? `Alles bereit — ${d.green} von 6 Pruefungen gruen` : `Pruefung laeuft — ${d.green} von 6 gruen`}
              </motion.span>
              <span style={{ flex: 1 }} />
              <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>Lauf {d.rounds}</span>
              <motion.span animate={{ rotate: open ? 180 : 0 }} transition={SPRING} style={{ display: 'inline-flex', color: P.sub }}>
                <IconChevronDown size={13} stroke={2} />
              </motion.span>
            </motion.div>

            <AnimatePresence initial={false}>
              {open && (
                <motion.div key="auf" data-compactbody initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }} transition={SPRING}
                  style={{ overflow: 'hidden', borderTop: `1px solid ${P.hair}` }}>
                  {CHECKS.map((c, i) => (
                    <DiagRow key={c.id} c={c} st={d.state[c.id]} P={P} i={i} onCheck={(id) => d.run([id])} compact />
                  ))}
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '7px 9px', background: P.bg }}>
                    <Btn P={P} attr="erneut9" label="erneut pruefen" Icon={IconRefresh} onClick={() => d.run()} primary i={0} />
                    <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>Klick auf eine Zeile prueft nur diese</span>
                    <span style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 5, fontFamily: MONO, fontSize: TEXT.meta, color: P.sub }}>
                      <IconScan size={10} stroke={1.9} /> nichts gesendet
                    </span>
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
          </div>
          <div style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint, marginTop: 7 }}>
            Platzsparend fuer die Fusszeile: eine Zeile, wenn alles steht — aufklappen nur bei Zweifel.
          </div>
        </div>
      )}
    </Case>
  )
}

/* ------------------------------------------------------- 10 dunkle Vollbild-Fassung */

export function V10() {
  return (
    <Case name="10 · Dunkle Fassung des Favoriten (Erster Start)" note="die Fassung aus 01 als volle dunkle Flaeche — Ablauf, Zustand und Pruefung unveraendert." dark0 full>
      {(P) => (
        <div data-darkfavorite style={{ background: P.bg, minHeight: 440, display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
            <IconPlugConnected size={15} stroke={1.9} color={P.ink} />
            <span style={{ fontFamily: SANS, fontSize: 14, fontWeight: 600, color: P.ink }}>Skaldir einrichten</span>
            <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>laeuft auf diesem Rechner</span>
            <span style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 6, fontFamily: MONO, fontSize: TEXT.meta, color: P.sub }}>
              <IconShieldCheck size={11} stroke={1.9} /> nichts verlaesst den Rechner ungefragt
            </span>
          </div>
          <SetupPanel P={P} compact dark />
          <div style={{ display: 'flex', gap: 10 }}>
            {[
              { Icon: IconStack2, t: 'Werkbank', d: 'vier Werkzeuge bereit' },
              { Icon: IconBook, t: 'Kurzanleitung', d: '2 Minuten' },
              { Icon: IconHelpCircle, t: 'Fragen zum Start', d: 'Antwort in der App' }
            ].map((x, i) => (
              <motion.div key={x.t} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ ...SPRING, delay: 0.1 + i * 0.05 }}
                style={{ flex: 1, border: `1px solid ${P.line}`, background: P.surface, padding: '8px 10px' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, color: P.ink }}>
                  <x.Icon size={12} strokeWidth={1.9} />
                  <span style={{ fontFamily: SANS, fontSize: 11.5, fontWeight: 600 }}>{x.t}</span>
                </div>
                <div style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, marginTop: 3 }}>{x.d}</div>
              </motion.div>
            ))}
          </div>
        </div>
      )}
    </Case>
  )
}

/* ------------------------------------------------------- Exporte */

export default {
  '01 · Erster Start: Verbindung einrichten': () => <V01 />,
  '02 · Diagnose: sechs Pruefungen': () => <V02 />,
  '03 · Fehler beim Start': () => <V03 />,
  '04 · Schluessel fehlt': () => <V04 />,
  '05 · Dienst laeuft nicht': () => <V05 />,
  '06 · Erste Schritte: drei Karten': () => <V06 />,
  '07 · Willkommen mit Beispielzielen': () => <V07 />,
  '08 · Fortschritt als Leiste mit vier Schritten': () => <V08 />,
  '09 · Kompakt: eine Zeile zum Aufklappen': () => <V09 />,
  '10 · Dunkle Fassung des Favoriten (Erster Start)': () => <V10 />
}
