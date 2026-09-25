/**
 * Not-Aus (Killswitch) und Leck-Alarm.
 *
 * Die Sicherheitsunterlagen verlangen zwei Dinge, die im UI bisher keinen Ort hatten:
 *   · "alles anhalten in einem Klick"  — erreichbar auch mitten in der Arbeit
 *   · einen Alarm, wenn ein Schluessel im Klartext auftaucht
 * Hier sind beide gebaut, in zehn Fassungen (01-10).
 *
 * Aufbau wie ErrorCards.fixture.tsx: jede Fassung liegt in <div data-stage> mit Titelzeile
 * und hell/dunkel-Umschalter. KEIN Rot — Dringlichkeit entsteht aus dem Kreuz (✕), 1.5px
 * Rahmen, Gewicht und Text (nur Schwarz/Weiss/Grau).
 *
 * Alle Zustaende sind echt (useState): der 3-Sekunden-Ring laeuft mit animate() ab und ist
 * die Rueckfrage (Bestaetigen oder Abbrechen), das Abhaken wirkt, die Stufen-Auswahl wirkt.
 */
import { motion, AnimatePresence, animate } from 'motion/react'
import React, { useState, useRef, useEffect } from 'react'
import { IconAlertOctagon, IconCheck, IconChevronRight, IconKey, IconPlayerStop, IconPower, IconShieldExclamation } from '@tabler/icons-react'

// @ts-expect-error — Laufzeit-Alias (siehe vite.config.ts)
import { ThinkingOrb } from 'thinking-orbs'

/* Farben, Schrift, Federn, Radien und Schriftgroessen: ./_tokens (eine Wahrheit).
   Befunde 18.09.2026: kein eigener Hexwert, Schriftuntergrenze 11 px (TEXT.meta),
   Radien aus RADIUS, useBarrierefrei() gegen reduzierte Bewegung und fuer die Tastatur. */
import { LIGHT, DARK, MONO, SANS, SPRING, RADIUS, TEXT } from './_tokens'
import { useBarrierefrei } from './_barrierefrei'
const RESP = { width: '100%', maxWidth: 640, boxSizing: 'border-box' }

const secs = (rest) => (rest * 3).toFixed(1).replace('.', ',')

/* ------------------------------------------------------------------ Bausteine */

/** Ring als Restzeit-Anzeige: `rest` laeuft 1 → 0 (voller Kreis = 3 s uebrig). */
const Ring = ({ P, rest, size = 22, width = 2, children }) => {
  const r = (size - width) / 2
  const c = 2 * Math.PI * r
  return (
    <span data-ring style={{ position: 'relative', width: size, height: size, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', flex: '0 0 auto' }}>
      <svg width={size} height={size} style={{ position: 'absolute', inset: 0, transform: 'rotate(-90deg)' }}>
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={P.line} strokeWidth={width} />
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={P.ink} strokeWidth={width}
          strokeDasharray={c} strokeDashoffset={c * (1 - rest)} />
      </svg>
      {children}
    </span>
  )
}

/** Hochzaehlende Zahl — laeuft bei Montage (oder neuem key) mit animate() hoch. */
const Num = ({ to, size = 26 }) => {
  const ref = useRef(null)
  useEffect(() => {
    if (!ref.current) return
    const steuerung = animate(0, to, {
      duration: 0.85, ease: 'easeOut',
      onUpdate: v => { if (ref.current) ref.current.textContent = String(Math.round(v)) }
    })
    return () => steuerung.stop()
  }, [to])
  return <span ref={ref} style={{ fontFamily: MONO, fontSize: size, fontWeight: 600, lineHeight: 1 }}>0</span>
}

const Btn = ({ P, label, primary, onClick, i = 0 }) => (
  <motion.span
    initial={{ opacity: 0, y: 5 }} animate={{ opacity: 1, y: 0 }} whileHover={{ scale: 1.03 }} whileTap={{ scale: 0.96 }}
    onClick={onClick} transition={{ ...SPRING, delay: 0.1 + i * 0.05 }}
    style={{
      fontFamily: MONO, fontSize: TEXT.meta, padding: '4px 9px', cursor: 'pointer', whiteSpace: 'nowrap', userSelect: 'none',
      border: `1px solid ${primary ? P.ink : P.line}`, background: primary ? P.ink : 'transparent', color: primary ? P.surface : P.ink
    }}>{label}</motion.span>
)

/** Der Not-Aus-Knopf selbst: 1 Klick startet den 3-Sekunden-Ring (Rueckfrage), der zweite bricht ab. */
const KillButton = ({ P, onFire, label = 'Alles anhalten', klein, versatz }) => {
  const [phase, setPhase] = useState('ruhe')   // ruhe | frage | aus
  const [rest, setRest] = useState(1)
  const ctrl = useRef(null)
  useEffect(() => () => { if (ctrl.current) ctrl.current.stop() }, [])

  const fire = () => {
    if (ctrl.current) ctrl.current.stop()
    setPhase('aus')
    setRest(0)
    if (onFire) onFire()
  }
  const start = () => {
    setPhase('frage')
    setRest(1)
    ctrl.current = animate(1, 0, {
      duration: 3, ease: 'linear',
      onUpdate: v => { setRest(v); if (v <= 0.001) fire() }
    })
  }
  const cancel = () => {
    if (ctrl.current) ctrl.current.stop()
    setPhase('ruhe')
    setRest(1)
  }
  const aus = phase === 'aus'
  const gross = !klein
  return (
    <div style={{ position: 'relative', display: 'inline-flex' }}>
      <motion.span
        data-kill
        onMouseDown={() => (phase === 'ruhe' ? start() : phase === 'frage' ? cancel() : null)}
        whileHover={{ scale: 1.03 }} whileTap={{ scale: 0.96 }} transition={SPRING}
        style={{
          display: 'inline-flex', alignItems: 'center', gap: 7, userSelect: 'none',
          padding: gross ? '7px 12px' : '3px 8px',
          border: `${gross ? 1.5 : 1}px solid ${aus ? P.line : P.ink}`,
          background: aus ? P.surface : P.ink, color: aus ? P.sub : P.surface,
          fontFamily: MONO, fontSize: gross ? 11.5 : TEXT.meta, fontWeight: 600, cursor: 'pointer'
        }}>
        {phase === 'frage'
          ? <Ring P={P} rest={rest} size={gross ? 18 : 13} width={1.8} />
          : <IconPower size={gross ? 13 : 11} stroke={2.4} color={aus ? P.faint : P.surface} />}
        {aus ? 'angehalten' : label}
        {phase === 'frage' && <span style={{ fontWeight: 400, color: P.line, opacity: 0.9 }}>noch {secs(rest)} s</span>}
      </motion.span>

      <AnimatePresence>
        {phase === 'frage' && (
          <motion.div
            key="frage"
            initial={{ opacity: 0, y: -5, scale: 0.98 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, y: -5, scale: 0.98 }}
            transition={SPRING}
            style={{
              position: 'absolute', top: 'calc(100% + 7px)', left: versatz || 0, width: 268, zIndex: 6,
              border: `1px solid ${P.line}`, background: P.surface, boxShadow: '0 8px 26px rgba(0,0,0,0.18)', padding: '8px 10px'
            }}>
            <div style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.ink, fontWeight: 600 }}>Rückfrage: alles sofort anhalten?</div>
            <div style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, margin: '3px 0 8px', lineHeight: 1.5 }}>
              3 Sekunden Bedenkzeit · ohne Antwort wird nicht angehalten
            </div>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              <Btn P={P} label="Ja, anhalten" primary onClick={fire} />
              <Btn P={P} label="Abbrechen" onClick={cancel} i={1} />
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

/** Kreuz wie in den Fehlerkarten: pulsiert beim Einhaengen, bleibt Text. */
const Cross = ({ P }) => (
  <motion.span
    initial={{ opacity: 0, scale: 0.4 }} animate={{ opacity: 1, scale: 1, x: [0, -2, 2, 0] }} transition={SPRING}
    style={{ fontFamily: MONO, fontSize: 14, color: P.ink, lineHeight: 1, flex: '0 0 auto' }}>✕</motion.span>
)

/* ---------------------------------------------------------------- Buehnen-Rahmen */

const useTheme = (startDark) => {
  const [dark, setDark] = useState(!!startDark)
  return [dark ? DARK : LIGHT, dark, () => setDark(d => !d)]
}

const Frame = ({ P, dark, toggle, name, note, children, full }) => {
  useBarrierefrei()
  return (
  <div data-stage style={{
    padding: full ? 0 : 20, background: P.bg, color: P.ink, fontFamily: SANS,
    width: '100%', maxWidth: full ? 'none' : 900, boxSizing: 'border-box'
  }}>
    <div style={{ padding: full ? '20px 20px 0' : 0 }}>
      <div style={{ fontFamily: MONO, fontSize: 12, fontWeight: 600, color: P.ink }}>{name}</div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, margin: '3px 0 12px' }}>
        <div style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, flex: 1 }}>{note}</div>
        <motion.span
          data-themetoggle onClick={toggle} whileHover={{ scale: 1.04 }} whileTap={{ scale: 0.94 }} transition={SPRING}
          style={{
            fontFamily: MONO, fontSize: TEXT.meta, padding: '3px 10px', cursor: 'pointer',
            border: `1px solid ${P.line}`, color: P.ink, background: dark ? P.surface : P.hair
          }}>{dark ? 'hell/dunkel: dunkel' : 'hell/dunkel: hell'}</motion.span>
      </div>
    </div>
    <div style={{ padding: full ? '0 20px 24px' : 0 }}>{children}</div>
  </div>
  )
}

const Fenster = ({ P, children, hoehe = 300 }) => (
  <div style={{ ...RESP, maxWidth: 720, border: `1px solid ${P.line}`, background: P.bg, height: hoehe, position: 'relative', overflow: 'hidden' }}>
    {children}
  </div>
)

const Kopf = ({ P, titel, rechts }) => (
  <div style={{ display: 'flex', alignItems: 'center', gap: 9, padding: '8px 12px', borderBottom: `1px solid ${P.line}`, background: P.surface }}>
    <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.ink }}>{titel}</span>
    <span style={{ flex: 1 }} />
    {rechts}
  </div>
)

const Blase = ({ P, text, ich, delay = 0 }) => (
  <motion.span
    initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ ...SPRING, delay }}
    style={{
      alignSelf: ich ? 'flex-end' : 'flex-start', maxWidth: 400, fontFamily: MONO, fontSize: 11,
      background: ich ? P.ink : P.surface, color: ich ? P.bg : P.ink,
      border: `1px solid ${ich ? P.ink : P.line}`, padding: '5px 9px'
    }}>{text}</motion.span>
)

/* ------------------------------------------------- Die Killswitch-Buehne (01 und 10) */

const KillScene = ({ P, dark }) => {
  const [angehalten, setAngehalten] = useState(false)
  return (
    <Fenster P={P} hoehe={318}>
      <Kopf P={P} titel={'skaldir · Lauf 41'}
        rechts={angehalten
          ? <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub }}>angehalten · 14:02:11</span>
          : <ThinkingOrb state={'weaving'} size={20} theme={dark ? 'dark' : 'light'} />} />
      <div style={{ padding: 14, display: 'flex', flexDirection: 'column', gap: 8 }}>
        <Blase P={P} ich text={'> zerleg das in drei spuren'} />
        <Blase P={P} text={'drei subagenten gerufen — sie arbeiten.'} delay={0.08} />
        {angehalten && (
          <motion.span initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ ...SPRING, delay: 0.05 }}
            style={{ alignSelf: 'flex-start', fontFamily: MONO, fontSize: 11, color: P.sub, border: `1px solid ${P.line}`, padding: '5px 9px' }}>
            angehalten — Kette steht bei Zeile 3
          </motion.span>
        )}
      </div>

      {/* Der Knopf am Rand — immer da, auch waehrend gearbeitet wird */}
      <div style={{ position: 'absolute', right: 14, top: '50%', transform: 'translateY(-50%)' }}>
        <KillButton P={P} versatz={-150} onFire={() => setAngehalten(true)} />
      </div>

      <AnimatePresence>
        {angehalten && (
          <motion.div
            data-result initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={SPRING}
            style={{ position: 'absolute', left: 14, right: 200, bottom: 12, border: `1px solid ${P.line}`, background: P.surface, padding: '8px 10px' }}>
            <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.ink }}>
              alles angehalten · 3 Läufe · 2 Subagenten · 7 Werkzeugaufrufe gestoppt
            </span>
          </motion.div>
        )}
      </AnimatePresence>
    </Fenster>
  )
}

/* ------------------------------------------------------------------ Varianten */

/* 01 Knopf am Rand des Fensters: Rückfrage mit 3-Sekunden-Ring. */
const V01 = ({ name, note }) => {
  const [P, dark, toggle] = useTheme(false)
  return (
    <Frame P={P} dark={dark} toggle={toggle} name={name} note={note}>
      <KillScene P={P} dark={dark} />
    </Frame>
  )
}

/* 02 Was gestoppt wurde — und die Wahrheit: was noch laeuft. */
const V02 = ({ name, note }) => {
  const [P, dark, toggle] = useTheme(false)
  const [lauf, setLauf] = useState(1)
  const [gesichert, setSichert] = useState(false)
  const ZAHLEN = [
    { l: 'Läufe', n: 'beendet', v: 3 },
    { l: 'Subagenten', n: 'beendet', v: 2 },
    { l: 'Werkzeugaufrufe', n: 'abgebrochen', v: 7 }
  ]
  return (
    <Frame P={P} dark={dark} toggle={toggle} name={name} note={note}>
      <div style={{ ...RESP, border: `1.5px solid ${P.ink}`, background: P.surface, padding: '12px 14px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <IconAlertOctagon size={15} stroke={2.2} color={P.ink} />
          <span style={{ fontFamily: SANS, fontSize: 12.5, fontWeight: 600 }}>Not-Aus ausgelöst — 14:02:11</span>
        </div>
        <div style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, margin: '5px 0 14px' }}>
          in einem Klick angehalten · nichts Neues startet mehr
        </div>
        <div style={{ display: 'flex', gap: 26, flexWrap: 'wrap' }}>
          {ZAHLEN.map(z => (
            <div key={z.l}>
              <Num key={`${lauf}-${z.l}`} to={z.v} />
              <div style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, marginTop: 4 }}>{z.l} · {z.n}</div>
            </div>
          ))}
        </div>
        <div style={{ marginTop: 14, paddingTop: 12, borderTop: `1px solid ${P.hair}`, minHeight: 30 }}>
          {!gesichert ? (
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
              <motion.span animate={{ opacity: [1, 0.3, 1] }} transition={{ duration: 1.4, repeat: Infinity, ease: 'easeInOut' }}
                style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.ink }}>läuft noch: Protokoll speichern</motion.span>
              <Btn P={P} label="speichern" primary onClick={() => setSichert(true)} />
            </div>
          ) : (
            <motion.div initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} transition={SPRING} style={{ fontFamily: MONO, fontSize: TEXT.meta }}>
              <span style={{ color: P.ink }}>0 offene Vorgänge — nichts läuft mehr. </span>
              <span style={{ color: P.sub }}>Protokoll: ~/.skaldir/vorfaelle/2026-09-18-1402.md</span>
            </motion.div>
          )}
        </div>
      </div>
      <div style={{ marginTop: 11 }}>
        <Btn P={P} label="nochmal auslösen" onClick={() => { setLauf(l => l + 1); setSichert(false) }} i={1} />
      </div>
    </Frame>
  )
}

/* 03 Dezent in der Kopfzeile — erreichbar, auch mitten in der Arbeit. */
const V03 = ({ name, note }) => {
  const [P, dark, toggle] = useTheme(false)
  const [arbeitet, setArbeitet] = useState(true)
  const [angehalten, setAngehalten] = useState(false)
  const laeuft = arbeitet && !angehalten
  return (
    <Frame P={P} dark={dark} toggle={toggle} name={name} note={note}>
      <Fenster P={P} hoehe={242}>
        <Kopf P={P} titel={'skaldir'}
          rechts={
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 10 }}>
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                {laeuft
                  ? <ThinkingOrb state={'working'} size={20} theme={dark ? 'dark' : 'light'} />
                  : <span style={{ width: 6, height: 6, borderRadius: RADIUS.pill, background: P.faint }} />}
                <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub }}>{laeuft ? 'arbeitet' : angehalten ? 'angehalten' : 'bereit'}</span>
              </span>
              <KillButton P={P} klein label="Not-Aus" versatz={-186} onFire={() => setAngehalten(true)} />
            </span>
          } />
        <div style={{ padding: 14 }}>
          <div style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, lineHeight: 1.7 }}>
            der Knopf sitzt fest in der Kopfzeile · er verschwindet nicht, wenn gearbeitet wird
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 7, marginTop: 12 }}>
            <Blase P={P} ich text={'> lauf weiter, ich schau zu'} />
            <Blase P={P} text={laeuft ? 'drei subagenten arbeiten · 7 werkzeuge offen' : angehalten ? 'alles angehalten — 3 läufe, 2 subagenten, 7 werkzeuge gestoppt' : 'nichts läuft'} delay={0.08} />
          </div>
        </div>
      </Fenster>
      <div style={{ marginTop: 11, display: 'flex', gap: 8, alignItems: 'center' }}>
        <Btn P={P} label={arbeitet ? 'Arbeit beenden (Probe)' : 'Arbeit laufen lassen (Probe)'} onClick={() => setArbeitet(a => !a)} i={2} />
        {angehalten && <Btn P={P} label="zurücksetzen" onClick={() => setAngehalten(false)} i={3} />}
      </div>
    </Frame>
  )
}

/* 04 Leck-Alarm: was erkannt wurde (Schluesselform, Ort) und was zu tun ist. */
const V04 = ({ name, note }) => {
  const [P, dark, toggle] = useTheme(false)
  const [weg, setWeg] = useState(null)
  const WEGE = [
    { id: 'w', l: 'Schlüssel widerrufen', a: 'widerrufen — der alte Schlüssel ist ungültig. Ein neuer gehört in die Einstellungen → Anbieter.' },
    { id: 'v', l: 'Verlauf prüfen', a: 'Verlauf wird durchsucht: Lauf 41, Zeile 218 und jede Kopie davon — 2 weitere Fundstellen.' },
    { id: 'b', l: 'Bericht speichern', a: 'Bericht liegt an: ~/.skaldir/vorfaelle/2026-09-18-1402.md — teilbar, ohne den Schlüssel zu zeigen.' }
  ]
  return (
    <Frame P={P} dark={dark} toggle={toggle} name={name} note={note}>
      <div style={{ ...RESP, border: `1.5px solid ${P.ink}`, background: P.surface, padding: '12px 14px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <Cross P={P} />
          <span style={{ fontFamily: SANS, fontSize: 13, fontWeight: 600 }}>Leck-Alarm: Schlüssel im Klartext erkannt</span>
        </div>
        <div style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, margin: '5px 0 10px' }}>
          vor 2 Minuten · der Prüfer hat den Verlauf durchsucht · nichts wurde gesendet
        </div>
        <div style={{ border: `1px solid ${P.line}`, background: P.bg, padding: '9px 10px' }}>
          <div style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.ink }}>
            Form: <span style={{ fontWeight: 600 }}>sk-live-••••7f2a</span> — Anbieter-Schlüssel, Klartext
          </div>
          <div style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, marginTop: 4, lineHeight: 1.6 }}>
            Ort: Protokoll von Lauf 41 · Zeile 218 · eine Kopie steckt in einer Nachricht an den Anbieter
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 7, margin: '11px 0 0', flexWrap: 'wrap' }}>
          <IconKey size={12} stroke={2.2} color={P.ink} />
          <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, marginRight: 2 }}>zu tun</span>
          {WEGE.map((w, i) => <Btn key={w.id} P={P} label={w.l} primary={i === 0} i={i} onClick={() => setWeg(w)} />)}
        </div>
        <div style={{ minHeight: 30, marginTop: 9 }}>
          <AnimatePresence mode="wait">
            <motion.div key={weg ? weg.id : 'leer'}
              initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -4 }} transition={SPRING}
              style={{ fontFamily: MONO, fontSize: TEXT.meta, color: weg ? P.ink : P.faint, paddingTop: 8, borderTop: `1px solid ${P.hair}` }}>
              {weg ? weg.a : 'einen Weg wählen — keiner ist ohne Wirkung'}
            </motion.div>
          </AnimatePresence>
        </div>
      </div>
    </Frame>
  )
}

/* 05 Leck-Alarm mit Schritten — echtes Abhaken. */
const V05 = ({ name, note }) => {
  const [P, dark, toggle] = useTheme(false)
  const [erledigt, setErledigt] = useState([])
  const SCHRITTE = [
    { id: 'w', t: 'Schlüssel widerrufen', d: 'beim Anbieter · der alte wird sofort ungültig' },
    { id: 'v', t: 'Verlauf prüfen', d: 'Lauf 41 · Zeile 218 und jede Kopie davon' },
    { id: 'b', t: 'Bericht speichern', d: '~/.skaldir/vorfaelle/2026-09-18-1402.md' }
  ]
  const um = (id) => setErledigt(e => (e.indexOf(id) >= 0 ? e.filter(x => x !== id) : e.concat(id)))
  const quote = erledigt.length / SCHRITTE.length
  const fertig = erledigt.length === SCHRITTE.length
  return (
    <Frame P={P} dark={dark} toggle={toggle} name={name} note={note}>
      <div style={{ ...RESP, border: `1.5px solid ${P.ink}`, background: P.surface, padding: '12px 14px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <Cross P={P} />
          <span style={{ fontFamily: SANS, fontSize: 13, fontWeight: 600 }}>Schlüssel im Klartext — drei Schritte</span>
        </div>
        <div style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, margin: '4px 0 11px' }}>
          {erledigt.length} von {SCHRITTE.length} erledigt
        </div>
        <div style={{ height: 2, background: P.hair, marginBottom: 12, position: 'relative' }}>
          <motion.div animate={{ width: `${quote * 100}%` }} transition={SPRING}
            style={{ position: 'absolute', inset: 0, background: P.ink }} />
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {SCHRITTE.map((s, i) => {
            const fertig1 = erledigt.indexOf(s.id) >= 0
            return (
              <motion.div key={s.id} onClick={() => um(s.id)} whileHover={{ scale: 1.006 }} whileTap={{ scale: 0.995 }} transition={SPRING}
                initial={{ opacity: 0, y: 5 }} animate={{ opacity: 1, y: 0, borderColor: fertig1 ? P.line : P.hair }}
                style={{
                  display: 'flex', alignItems: 'center', gap: 9, padding: '7px 9px', cursor: 'pointer',
                  border: `1px solid ${P.hair}`, background: fertig1 ? P.bg : 'transparent'
                }}>
                <span style={{
                  display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 16, height: 16, flex: '0 0 auto',
                  border: `1.5px solid ${fertig1 ? P.ink : P.line}`, background: fertig1 ? P.ink : 'transparent'
                }}>
                  <AnimatePresence>
                    {fertig1 && <motion.span key="h" initial={{ opacity: 0, scale: 0.3 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0, scale: 0.3 }} transition={SPRING}>
                      <IconCheck size={11} stroke={3} color={P.surface} />
                    </motion.span>}
                  </AnimatePresence>
                </span>
                <span style={{ flex: 1, minWidth: 0 }}>
                  <span style={{ display: 'block', fontFamily: MONO, fontSize: 11, fontWeight: fertig1 ? 600 : 400, color: P.ink, textDecoration: fertig1 ? 'line-through' : 'none' }}>{s.t}</span>
                  <span style={{ display: 'block', fontFamily: MONO, fontSize: TEXT.meta, color: P.sub }}>{s.d}</span>
                </span>
                <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>0{i + 1}</span>
              </motion.div>
            )
          })}
        </div>
        <div style={{ marginTop: 11, paddingTop: 10, borderTop: `1px solid ${P.hair}`, display: 'flex', alignItems: 'center', gap: 9 }}>
          {fertig
            ? <motion.span initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} transition={SPRING}
              style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontFamily: MONO, fontSize: TEXT.meta, color: P.ink }}>
              <IconCheck size={12} stroke={2.6} color={P.ink} /> alle drei erledigt — Vorfall abgeschlossen
            </motion.span>
            : <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>anhaken wirkt · „{SCHRITTE[erledigt.length] ? SCHRITTE[erledigt.length].t : ''}" ist der nächste Schritt</span>}
          {erledigt.length > 0 && <Btn P={P} label="zurücksetzen" onClick={() => setErledigt([])} i={1} />}
        </div>
      </div>
    </Frame>
  )
}

/* 06 Sicherheitslage: drei Zeilen mit Zustand — und einem Weg zurueck. */
const V06 = ({ name, note }) => {
  const [P, dark, toggle] = useTheme(false)
  const [geheilt, setGeheilt] = useState([])
  const POSTEN = [
    { id: 'k', t: 'Schlüssel im Klartext', schlecht: 'gefunden in Protokoll 41 · Zeile 218', gut: 'verschlüsselt hinterlegt (Schlüsselbund)' },
    { id: 'e', t: 'Externe Verbindung', schlecht: 'offen: api.anbieter.test:443', gut: 'geschlossen · nur 127.0.0.1:5050' },
    { id: 's', t: 'Schreibrechte', schlecht: 'weit: /home/willy (alles)', gut: 'eng: app-src/Design_Docs' }
  ]
  const offen = POSTEN.filter(p => geheilt.indexOf(p.id) < 0).length
  const um = (id) => setGeheilt(g => (g.indexOf(id) >= 0 ? g.filter(x => x !== id) : g.concat(id)))
  return (
    <Frame P={P} dark={dark} toggle={toggle} name={name} note={note}>
      <div style={{ ...RESP, border: `1px solid ${P.line}`, background: P.surface }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '9px 12px', borderBottom: `1px solid ${P.line}` }}>
          <IconShieldExclamation size={14} stroke={2.2} color={P.ink} />
          <span style={{ fontFamily: MONO, fontSize: 11, fontWeight: 600 }}>Sicherheitslage</span>
          <span style={{ flex: 1 }} />
          <motion.span key={offen} initial={{ opacity: 0.4, scale: 0.97 }} animate={{ opacity: 1, scale: 1 }} transition={SPRING}
            style={{ fontFamily: MONO, fontSize: TEXT.meta, color: offen ? P.ink : P.sub }}>
            {offen ? `${offen} von 3 offen` : 'nichts offen'}
          </motion.span>
        </div>
        {POSTEN.map((p, i) => {
          const gut = geheilt.indexOf(p.id) >= 0
          return (
            <motion.div key={p.id} animate={{ backgroundColor: gut ? P.bg : 'transparent' }} transition={{ duration: 0.18 }}
              style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '9px 12px', borderTop: i ? `1px solid ${P.hair}` : 'none' }}>
              <motion.span animate={{ scale: gut ? 0.7 : 1, opacity: gut ? 0.55 : 1 }} transition={SPRING}
                style={{ width: 8, height: 8, borderRadius: RADIUS.pill, background: P.ink, flex: '0 0 auto' }} />
              <span style={{ flex: 1, minWidth: 0 }}>
                <span style={{ display: 'block', fontFamily: MONO, fontSize: 11, color: P.ink }}>{p.t}</span>
                <AnimatePresence mode="wait">
                  <motion.span key={gut ? 'gut' : 'schlecht'}
                    initial={{ opacity: 0, y: 3 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -3 }} transition={SPRING}
                    style={{ display: 'block', fontFamily: MONO, fontSize: TEXT.meta, color: gut ? P.sub : P.ink }}>
                    {gut ? p.gut : p.schlecht}
                  </motion.span>
                </AnimatePresence>
              </span>
              <Btn P={P} label={gut ? 'zurücksetzen' : 'beheben'} onClick={() => um(p.id)} i={i} />
            </motion.div>
          )
        })}
        <div style={{ padding: '9px 12px', borderTop: `1px solid ${P.line}`, fontFamily: MONO, fontSize: TEXT.meta, color: P.sub }}>
          {offen === 0 ? 'grün wäre schön — hier bleibt es grau: alles zu.' : 'Offenes wird nüchtern benannt, nicht rot angestrichen.'}
        </div>
      </div>
    </Frame>
  )
}

/* 07 Not-Aus-Menue: drei Stufen, die Auswahl wirkt. */
const V07 = ({ name, note }) => {
  const [P, dark, toggle] = useTheme(false)
  const [sel, setSel] = useState('a')
  const [folge, setFolge] = useState(null)
  const STUFEN = [
    { id: 't', t: 'nur Werkzeuge stoppen', d: 'Läufe und Subagenten arbeiten weiter', folge: '7 Werkzeugaufrufe gestoppt · 3 Läufe und 2 Subagenten arbeiten weiter' },
    { id: 'a', t: 'alles anhalten', d: 'kein neuer Schritt, kein neuer Aufruf', folge: '3 Läufe, 2 Subagenten, 7 Werkzeugaufrufe gestoppt — nichts Neues startet' },
    { id: 'd', t: 'Dienst beenden', d: 'erst anhalten, dann den lokalen Dienst schließen', folge: 'alles gestoppt, Dienst beendet · Port 5050 frei, keine offene Verbindung' }
  ]
  const gewaehlt = STUFEN.filter(s => s.id === sel)[0]
  return (
    <Frame P={P} dark={dark} toggle={toggle} name={name} note={note}>
      <div style={{ ...RESP, border: `1px solid ${P.line}`, background: P.surface, padding: 10 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 7, padding: '0 2px 9px' }}>
          <IconPlayerStop size={13} stroke={2.2} color={P.ink} />
          <span style={{ fontFamily: MONO, fontSize: TEXT.meta, letterSpacing: 1, color: P.sub }}>NOT-AUS · STUFE WÄHLEN</span>
        </div>
        {STUFEN.map((s, i) => {
          const an = sel === s.id
          return (
            <motion.div key={s.id} data-stufe={s.id} onClick={() => setSel(s.id)} whileHover={{ x: 1 }} transition={SPRING}
              style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '9px 10px', cursor: 'pointer', borderTop: i ? `1px solid ${P.hair}` : 'none' }}>
              <span style={{ position: 'relative', width: 14, height: 14, border: `1.5px solid ${an ? P.ink : P.line}`, flex: '0 0 auto' }}>
                {an && <motion.span layoutId="stufe-punkt" transition={SPRING}
                  style={{ position: 'absolute', inset: 2.5, background: P.ink }} />}
              </span>
              <span style={{ flex: 1, minWidth: 0 }}>
                <span style={{ display: 'block', fontFamily: MONO, fontSize: 11, fontWeight: an ? 600 : 400, color: P.ink }}>
                  {s.t}{s.id === 'd' && <span style={{ color: P.sub, fontWeight: 400 }}> (nicht umkehrbar)</span>}
                </span>
                <span style={{ display: 'block', fontFamily: MONO, fontSize: TEXT.meta, color: P.sub }}>{s.d}</span>
              </span>
              {an && <motion.span initial={{ opacity: 0, x: -3 }} animate={{ opacity: 1, x: 0 }} transition={SPRING}>
                <IconChevronRight size={12} stroke={2.2} color={P.ink} />
              </motion.span>}
            </motion.div>
          )
        })}
        <div style={{ display: 'flex', gap: 6, padding: '11px 2px 2px', borderTop: `1px solid ${P.line}`, marginTop: 9, flexWrap: 'wrap' }}>
          <Btn P={P} label={`ausführen: ${gewaehlt.t}`} primary onClick={() => setFolge(gewaehlt.folge)} />
          {folge && <Btn P={P} label="zurücksetzen" onClick={() => setFolge(null)} i={1} />}
        </div>
        <div style={{ minHeight: 26, paddingTop: 8 }}>
          <AnimatePresence mode="wait">
            {folge
              ? <motion.div key={folge} initial={{ opacity: 0, y: 5 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={SPRING}
                style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.ink }}>{folge}</motion.div>
              : <motion.div key="leer" initial={{ opacity: 0 }} animate={{ opacity: 1 }} style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>
                gewählt: {gewaehlt.t} — Ausführen zeigt, was tatsächlich passiert
              </motion.div>}
          </AnimatePresence>
        </div>
      </div>
    </Frame>
  )
}

/* 08 Protokoll des Vorfalls als Zeitstrahl. */
const V08 = ({ name, note }) => {
  const [P, dark, toggle] = useTheme(false)
  const [lauf, setLauf] = useState(1)
  const SPUR = [
    ['14:02:11,2', 'Not-Aus geklickt', 'Knopf am Rand · ein Klick'],
    ['14:02:11,4', 'Werkzeuge gestoppt', '7 laufende Aufrufe abgebrochen'],
    ['14:02:12,0', 'Subagenten beendet', '2 beendet · Arbeitsstand gesichert'],
    ['14:02:12,6', 'Läufe beendet', '3 Läufe · Zwischenstand geschrieben'],
    ['14:02:13,1', 'Protokoll gespeichert', '~/.skaldir/vorfaelle/2026-09-18-1402.md'],
    ['14:02:14,0', 'Dienst sauber beendet', 'Port 5050 frei · keine offene Verbindung']
  ]
  return (
    <Frame P={P} dark={dark} toggle={toggle} name={name} note={note}>
      <div key={lauf} style={{ ...RESP, position: 'relative', paddingLeft: 20 }}>
        <motion.div initial={{ scaleY: 0 }} animate={{ scaleY: 1 }} transition={{ duration: 0.9, ease: 'easeOut' }}
          style={{ position: 'absolute', left: 4, top: 4, bottom: 8, width: 1, background: P.line, transformOrigin: 'top' }} />
        {SPUR.map(([zeit, was, wo], i) => (
          <motion.div key={zeit} initial={{ opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }} transition={{ ...SPRING, delay: 0.1 + i * 0.11 }}
            style={{ position: 'relative', marginBottom: 12 }}>
            <motion.span initial={{ scale: 0 }} animate={{ scale: 1 }} transition={{ ...SPRING, delay: 0.1 + i * 0.11 }}
              style={{ position: 'absolute', left: -20, top: 5, width: 9, height: 9, borderRadius: RADIUS.pill, background: P.ink, border: `2px solid ${P.bg}` }} />
            <div style={{ fontFamily: MONO, fontSize: 11, color: P.ink }}>
              <span style={{ color: P.sub }}>{zeit}</span> · {was}
            </div>
            <div style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, marginTop: 2 }}>{wo}</div>
          </motion.div>
        ))}
        <div style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint, paddingLeft: 0 }}>
          Dauer gesamt: 2,8 s · 0 offene Vorgänge
        </div>
      </div>
      <div style={{ marginTop: 12 }}>
        <Btn P={P} label="Zeitstrahl nochmal abspielen" onClick={() => setLauf(l => l + 1)} />
      </div>
    </Frame>
  )
}

/* 09 Sehr kompakt: eine Zeile „Not-Aus" mit Ring und sichtbarer Restzeit. */
const V09 = ({ name, note }) => {
  const [P, dark, toggle] = useTheme(false)
  const [phase, setPhase] = useState('ruhe')   // ruhe | frage | aus
  const [rest, setRest] = useState(1)
  const ctrl = useRef(null)
  useEffect(() => () => { if (ctrl.current) ctrl.current.stop() }, [])

  const fire = () => {
    if (ctrl.current) ctrl.current.stop()
    setPhase('aus')
    setRest(0)
  }
  const start = () => {
    setPhase('frage')
    setRest(1)
    ctrl.current = animate(1, 0, { duration: 3, ease: 'linear', onUpdate: v => { setRest(v); if (v <= 0.001) fire() } })
  }
  const cancel = () => {
    if (ctrl.current) ctrl.current.stop()
    setPhase('ruhe')
    setRest(1)
  }
  const aus = phase === 'aus'
  return (
    <Frame P={P} dark={dark} toggle={toggle} name={name} note={note}>
      <div style={{ ...RESP, border: `1px solid ${P.line}`, background: P.surface, display: 'flex', alignItems: 'center', gap: 10, padding: '6px 10px' }}>
        <span style={{ fontFamily: MONO, fontSize: 11.5, color: P.faint, flex: 1, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
          nachricht an skaldir <motion.span animate={{ opacity: [1, 0.15, 1] }} transition={{ duration: 1.05, repeat: Infinity }}>▍</motion.span>
        </span>
        <motion.span
          data-kill onMouseDown={() => (phase === 'ruhe' ? start() : phase === 'frage' ? cancel() : null)}
          whileHover={{ scale: 1.04 }} whileTap={{ scale: 0.95 }} transition={SPRING}
          style={{
            display: 'inline-flex', alignItems: 'center', gap: 6, padding: '3px 8px', cursor: 'pointer', userSelect: 'none',
            border: `1px solid ${aus ? P.ink : P.line}`, background: aus ? P.ink : P.bg
          }}>
          {phase === 'frage'
            ? <Ring P={P} rest={rest} size={13} width={1.6} />
            : <IconPower size={11} stroke={2.4} color={aus ? P.bg : P.ink} />}
          <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: aus ? P.bg : P.ink }}>{aus ? 'angehalten' : 'Not-Aus'}</span>
          <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: aus ? P.bg : P.sub }}>
            {phase === 'frage' ? `noch ${secs(rest)} s` : aus ? '0 offen' : '3 s'}
          </span>
        </motion.span>
      </div>
      <div style={{ marginTop: 9, fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, lineHeight: 1.7 }}>
        {phase === 'ruhe' && 'eine Zeile, immer sichtbar · Klick startet die 3-Sekunden-Rückfrage, zweiter Klick bricht ab'}
        {phase === 'frage' && `Ring läuft ab — in ${secs(rest)} s hält alles an, wenn du nicht eingreifst`}
        {phase === 'aus' && 'alles angehalten · 3 Läufe, 2 Subagenten, 7 Werkzeugaufrufe gestoppt'}
      </div>
    </Frame>
  )
}

/* 10 Dunkle Vollbild-Fassung des Favoriten (01). */
const V10 = ({ name, note }) => {
  const [P, dark, toggle] = useTheme(true)
  return (
    <Frame P={P} dark={dark} toggle={toggle} name={name} note={note} full>
      <div style={{ maxWidth: 1100 }}>
        <KillScene P={P} dark={dark} />
      </div>
    </Frame>
  )
}

/* ---------------------------------------------------------------- Exporte */

export default {
  '01 · Not-Aus am Rand — Rückfrage mit 3-Sekunden-Ring': () => (
    <V01 name="01 · Killswitch am Rand: Alles anhalten" note="ein Klick stellt die Rückfrage · der Ring läuft 3 Sekunden" />
  ),
  '02 · Killswitch ausgelöst — was gestoppt wurde, was noch läuft': () => (
    <V02 name="02 · Was gestoppt wurde" note="Zahlen zählen hoch · die eine noch laufende Sache wird benannt, nicht versteckt" />
  ),
  '03 · Killswitch in der Kopfzeile (immer erreichbar)': () => (
    <V03 name="03 · Not-Aus in der Kopfzeile, dezent" note="fest im Kopf · bleibt auch während der Arbeit erreichbar" />
  ),
  '04 · Leck-Alarm — was erkannt wurde und was zu tun ist': () => (
    <V04 name="04 · Leck-Alarm" note="Schlüsselform und Ort zuerst, dann genau drei Wege" />
  ),
  '05 · Leck-Alarm mit Schritten (echtes Abhaken)': () => (
    <V05 name="05 · Leck-Alarm mit Schritten" note="widerrufen · Verlauf prüfen · Bericht speichern — anhaken wirkt" />
  ),
  '06 · Sicherheitslage — drei Zeilen mit Zustand': () => (
    <V06 name="06 · Sicherheitslage" note="Schlüssel im Klartext, externe Verbindung, Schreibrechte · mit Weg zurück" />
  ),
  '07 · Not-Aus-Menü mit drei Stufen': () => (
    <V07 name="07 · Not-Aus in drei Stufen" note="nur Werkzeuge · alles · Dienst beenden — Auswahl und Folge wirken" />
  ),
  '08 · Protokoll des Vorfalls als Zeitstrahl': () => (
    <V08 name="08 · Protokoll des Vorfalls" note="sechs Zeitstempel von 14:02:11,2 bis 14:02:14,0 · abspielbar" />
  ),
  '09 · Kompakt: eine Zeile Not-Aus mit Ring und Restzeit': () => (
    <V09 name="09 · Not-Aus, eine Zeile" note="Ring und Restzeit direkt am Knopf · zweiter Klick bricht ab" />
  ),
  '10 · Dunkle Vollbild-Fassung des Favoriten (01)': () => (
    <V10 name="10 · Favorit DARKMODE (01)" note="der Knopf am Rand, volle dunkle Fläche" />
  )
}
