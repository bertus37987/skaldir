/**
 * Fehlerkarten — Kategorie D: FEHLERMELDUNGEN MIT AUSWEG.
 *
 * Regel (Produkt): nach einem Fehler darf KEIN Orb erscheinen — hier gibt es
 * keinen ThinkingOrb-Import und keine Orb-Buehne. Fehler werden NICHT rot,
 * sondern durch das Kreuz (✕), starke Rahmen (1.5px) und klaren Text deutlich.
 * Jede Karte nennt: was passiert ist, was schon steht, und welche Wege bleiben.
 *
 * Aufbau wie ChatBarRef.fixture.tsx: <div data-stage> als aeusserer Rahmen,
 * Titelzeile, dann die Karte. Hell/Dunkel-Umschalter im Kopf (useState) fuer
 * die Varianten 01-08. Bewegung (Motion): Karten und Knoepfe setzen sich mit
 * Feder, der Countdown in 06 laeuft mit animate(). Variante 09 ist ein bewusst
 * stiller Endzustand OHNE Animation (Panik, keine Aktion), Variante 10 ist die
 * dunkle Vollbild-Fassung des Favoriten (01).
 */
// @ts-expect-error — Laufzeit-Alias (siehe vite.config.ts)
import { ThinkingOrb } from 'thinking-orbs'
import React, { useState, useRef, useEffect } from 'react'
import { motion, animate } from 'motion/react'
import { useBarrierefrei } from './_barrierefrei'
import { LIGHT, DARK, MONO, SANS, SPRING, RADIUS, TEXT } from './_tokens'

/* ------------------------------------------------------------------ Bausteine */

/** Rotes-Licht-Kreuz: pulsiert kurz beim Einhaengen, bleibt aber Text, kein Symbol-Farbfehler. */
const Cross = ({ P, delay = 0 }) => (
  <motion.span
    initial={{ opacity: 0, scale: 0.4 }}
    animate={{ opacity: 1, scale: 1, x: [0, -2, 2, 0] }}
    transition={{ ...SPRING, delay }}
    style={{ fontFamily: MONO, fontSize: TEXT.body, color: P.ink, flex: '0 0 auto', lineHeight: 1 }}>✕</motion.span>
)

/** Knopf: erster ist haupt-weg (gebraeunte Tinte), die anderen bleiben offen. */
const Btn = ({ P, label, primary, i = 0 }) => (
  <motion.span
    initial={{ opacity: 0, y: 5 }}
    animate={{ opacity: 1, y: 0 }}
    whileHover={{ scale: 1.03 }}
    whileTap={{ scale: 0.96 }}
    transition={{ ...SPRING, delay: 0.12 + i * 0.05 }}
    style={{
      fontFamily: MONO, fontSize: TEXT.meta, padding: '4px 9px', cursor: 'pointer',
      border: `1px solid ${primary ? P.ink : P.line}`,
      background: primary ? P.ink : 'transparent',
      color: primary ? P.surface : P.ink, whiteSpace: 'nowrap'
    }}>{label}</motion.span>
)

/** Countdown-Zahl: laeuft optisch mit animate() von einem Startwert auf einen Endwert. */
const Countdown = ({ P, from, to, duration, suffix, delay = 0.4 }) => {
  const ref = useRef(null)
  useEffect(() => {
    if (!ref.current) return
    const steuerung = animate(from, to, {
      duration, delay, ease: 'easeInOut',
      onUpdate: v => { if (ref.current) ref.current.textContent = `${Math.ceil(v)}${suffix}` }
    })
    return () => steuerung.stop()
  }, [from, to, duration, suffix, delay])
  return <span ref={ref} data-countdown style={{ fontFamily: MONO, color: P.ink }}>{''}</span>
}

const BAR = { display: 'flex', gap: 7, flexWrap: 'wrap', alignItems: 'center' }

/** Karte als Endzustand: gleiche Geometrie, aber statisch ohne Animations-Motion. */
const StaticCard = ({ P, title, note, ways }) => (
  <div
    data-errorcard
    style={{
      border: `1.5px solid ${P.ink}`, background: P.surface,
      padding: '10px 12px', maxWidth: 560
    }}>
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <span style={{ fontFamily: MONO, fontSize: TEXT.body, color: P.ink, flex: '0 0 auto', lineHeight: 1 }}>✕</span>
      <span style={{ fontFamily: SANS, fontSize: 12.5, fontWeight: 600, color: P.ink }}>{title}</span>
    </div>
    <div style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, margin: '5px 0 8px', lineHeight: 1.5 }}>{note}</div>
    <div style={BAR}>
      {ways.map((w, i) => (
        <span key={w} style={{
          fontFamily: MONO, fontSize: TEXT.meta, padding: '4px 9px',
          border: `1px solid ${i === 0 ? P.ink : P.line}`,
          background: i === 0 ? P.ink : 'transparent',
          color: i === 0 ? P.surface : P.ink, whiteSpace: 'nowrap'
        }}>{w}</span>
      ))}
    </div>
  </div>
)

/** Der Karten-Koerper: Titel mit Kreuz, Erklaer-Hinweis (mono), Knopf-Spalte. */
const Card = ({ P, title, note, extra, ways, bold }) => (
  <motion.div
    data-errorcard
    initial={{ opacity: 0, scale: 0.97, y: 6 }}
    animate={{ opacity: 1, scale: 1, y: 0 }}
    transition={SPRING}
    style={{
      border: `1.5px solid ${P.ink}`, background: P.surface,
      padding: '10px 12px', maxWidth: 560, transformOrigin: 'bottom left'
    }}>
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <Cross P={P} />
      <span style={{ fontFamily: SANS, fontSize: 12.5, fontWeight: 600, color: P.ink }}>{title}</span>
    </div>
    {note && (
      <div style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, margin: '5px 0 8px', lineHeight: 1.5 }}>{note}</div>
    )}
    {extra}
    <div style={BAR}>
      <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint, margin: '6px 2px 0 0' }}> ways </span>
      {ways.map((w, i) => <Btn key={w} P={P} label={w} primary={i === 0} i={i} />)}
      {!!bold && null}
    </div>
  </motion.div>
)

/* ---------------------------------------------------------------- Buehnen-Rahmen */

const Stage = ({ P, children }) => (
  <div data-stage style={{ padding: 20, background: P.bg, fontFamily: SANS, width: '100%', maxWidth: 860, boxSizing: 'border-box' }}>
    {children}
  </div>
)

const Head = ({ P, name, note, dark, toggle, onOff }) => (
  <div style={{ display: 'flex', alignItems: 'center', gap: 8, margin: '3px 0 12px' }}>
    <div style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, flex: 1 }}>{onOff}</div>
    {!!toggle && (
      <motion.span
        data-themetoggle
        onClick={toggle}
        whileHover={{ scale: 1.04 }}
        whileTap={{ scale: 0.94 }}
        transition={SPRING}
        style={{ fontFamily: MONO, fontSize: TEXT.meta, padding: '3px 10px', border: `1px solid ${P.line}`, color: P.ink, cursor: 'pointer', background: dark ? P.surface : P.hair }}>
        {dark ? 'hell/dunkel: dunkel' : 'hell/dunkel: hell'}
      </motion.span>
    )}
  </div>
)

const TitleLine = ({ P, name }) => (
  <div style={{ fontFamily: MONO, fontSize: 12, fontWeight: 600, color: P.ink }}>{name}</div>
)

/** Kaiserliche Buehne fuer 01 bis 08: Kopf + Karte in einem schmutzfreien flachen Rahmen. */
const Case = ({ name, note, children }) => {
  useBarrierefrei()
  const [dark, setDark] = useState(false)
  const P = dark ? DARK : LIGHT
  return (
    <Stage P={P}>
      <TitleLine P={P} name={name} />
      <Head P={P} name={name} note={note} dark={dark} toggle={() => setDark(d => !d)} />
      {children(P)}
    </Stage>
  )
}

/* ---------------------------------------------------------------- Varianten */

/* 01 Modell antwortet nicht — der Favorit, wird wie eine Karte zwischen Brief und Bild gestellt. */
const Case01 = ({ name, note }) => (
  <Case name={name} note={note}>
    {(P) => (
      <Card
        P={P}
        title={'opencode antwortet nicht'}
        note={'Timeout nach 45 Sekunden · kein Byte empfangen · der Faden ist heil.'}
        ways={['erneut versuchen', 'anderes Modell']}/>
    )}
  </Case>
)

/* 02 Kein API-Schluessel — sagt, wohin er gehoert und dass nichts rausging. */
const Case02 = ({ name, note }) => (
  <Case name={name} note={note}>
    {(P) => (
      <Card
        P={P}
        title={'Kein API-Schlüssel hinterlegt'}
        note={'Es fehlt der API-Schlüssel für diesen Anbieter · gehört in die Einstellungen: Einstellungen → Anbieter → API-Schlüssel.\nEs wurde nichts gesendet.'}
        ways={['Schlüssel hinterlegen']}/>
    )}
  </Case>
)

/* 03 Gateway unten — Adresse nennen, Namen fuer die naechste Zeit und die Karte. */
const Case03 = ({ name, note }) => (
  <Case name={name} note={note}>
    {(P) => (
      <Card
        P={P}
        title={'Gateway nicht erreichbar'}
        note={'Adresse http://127.0.0.1:5050 antwortet nicht · der lokale Dienst ist unten.\nDer Chat läuft weiter — es wurde nichts gesendet.'}
        ways={['Dienst prüfen', 'später erneut']}/>
    )}
  </Case>
)

/* 04 Werkzeug hat abgelehnt — Werkzeugname, Grund, Weg ueber die Freigabe. */
const Case04 = ({ name, note }) => (
  <Case name={name} note={note}>
    {(P) => (
      <Card
        P={P}
        title={'Werkzeug abgelehnt: filesystem.write'}
        note={'Grund: Pfad liegt außerhalb der erlaubten Wurzel (app-src/Design_Docs).\nEs wurde nichts veraendert.'}
        ways={['mit Freigabe erneut']}/>
    )}
  </Case>
)

/* 05 Kontext ist voll — belegt/gesamt und zwei Wege: zusammen ueberleben oder neue Sitzung. */
const Case05 = ({ name, note }) => (
  <Case name={name} note={note}>
    {(P) => (
      <Card
        P={P}
        title={'Kontext ist voll'}
        note={'Belegt 31.900 von 32.000 Zeichen · die naechste Nachricht passt nicht mehr.'}
        ways={['Verlauf verdichten', 'neue Sitzung']}/>
    )}
  </Case>
)

/* 06 Anbieter-Limit pro Minute — Countdown liefert den genauen Zeitpunkt. */
const Case06 = ({ name, note }) => (
  <Case name={name} note={note}>
    {(P) => (
      <div style={{ maxWidth: 560 }}>
        <motion.div
          data-errorcard
          initial={{ opacity: 0, scale: 0.97, y: 6 }}
          animate={{ opacity: 1, scale: 1, y: 0 }}
          transition={SPRING}
          style={{ border: `1.5px solid ${P.ink}`, background: P.surface, padding: '10px 12px', transformOrigin: 'bottom left' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <Cross P={P} />
            <span style={{ fontFamily: SANS, fontSize: 12.5, fontWeight: 600, color: P.ink }}>Anbieter-Limit erreicht</span>
          </div>
          <div style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, margin: '5px 0 8px', lineHeight: 1.5 }}>
            token pro Minute: wir sind fuer diese Minute begrenzt · der Faden ist heil.
          </div>
          <div style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.ink, margin: '0 0 8px' }}>
            weiter in <Countdown P={P} from={20} to={0} duration={20} suffix={' s'} />
          </div>
          <div style={BAR}>
            {['warten', 'anderen Anbieter'].map((w, i) => <Btn key={w} P={P} label={w} primary={i === 0} i={i} />)}
          </div>
        </motion.div>
      </div>
    )}
  </Case>
)

/* 07 Datei ueber dem Anbieter-Limit — die abgelehnten Dateien und der Fakt mit 0. */
const Case07 = ({ name, note }) => (
  <Case name={name} note={note}>
    {(P) => (
      <Card
        P={P}
        title={'Der Anbieter nimmt höchstens 5 MB je Datei'}
        note={'2 Dateien abgelehnt: handbuch.pdf (9,8 MB), rohdaten.zip (12,4 MB).\nUebertragen: 0 von 22,2 MB — es wurde nichts gesendet.'}
        ways={['Datei verkleinern', 'Andere Datei wählen', 'Anderen Anbieter wählen']}/>
    )}
  </Case>
)

/* 08 Netzfehler mitten in der Antwort — die Antwort steht bereits da, Weg ab hier fortsetzen. */
const Case08 = ({ name, note }) => (
  <Case name={name} note={note}>
    {(P) => (
      <Card
        P={P}
        title={'Netz unterbrochen'}
        note={'Die Antwort brach mitten im Satz ab — der Text bis hier ist sicher im Transkript.\nVerhindere Verlust durch: \u201cab hier fortsetzen\u201d.'}
        extra="Die Antwort rendere ich gleich fertig — kein Verlust."
        ways={['ab hier fortsetzen', 'neu generieren']}/>
    )}
  </Case>
)

/* 09 Absturz des Werkzeugs — Endzustand, OHNE Animation: der Bericht ist der Ausweg. */
const Case09 = ({ name, note }) => {
  useBarrierefrei()
  const P = LIGHT
  return (
    <Stage P={P}>
      <TitleLine P={P} name={name} />
      <Head P={P} name={name} note={note} />
      <StaticCard
        P={P}
        title={'Werkzeug abgestürzt: codex'}
        note={'Exit-Code 1 · Bericht liegt an: ~/.hermes/crashes/2026-09-18/codex-2345.log\nDer Arbeitsstand der Sitzung ist sicher — nichts ist verloren.'}
        ways={['Bericht öffnen', 'Arbeitsstand sichern']}/>
    </Stage>
  )
}

/* 10 Dunkle Vollbild-Fassung des Favoriten (01). */
const Case10 = ({ name, note }) => {
  useBarrierefrei()
  const P = DARK
  return (
    <div data-stage style={{ background: P.bg, fontFamily: SANS, width: '100%', boxSizing: 'border-box' }}>
      <div style={{ padding: '20px 20px 0' }}>
        <TitleLine P={P} name={name} />
        <Head P={P} name={name} note={note} />
      </div>
      <div style={{ padding: 20, paddingTop: 12 }}>
        <Card
          P={P}
          title={'opencode antwortet nicht'}
          note={'Timeout nach 45 Sekunden · kein Byte empfangen · der Faden ist heil.'}
          ways={['erneut versuchen', 'anderes Modell']}/>
      </div>
    </div>
  )
}

/* ---------------------------------------------------------------- Exporte */

export default {
  '01 · Modell antwortet nicht (Timeout)': () => (
    <Case01 name="01 · Modell antwortet nicht" note="was passiert ist, was liegt auf dem Tisch, und welche Wege bleiben" />
  ),
  '02 · Kein API-Schluessel hinterlegt': () => (
    <Case02 name="02 · Kein API-Schluessel" note="wohin der Schluessel gehoert und dass nichts gesendet wurde" />
  ),
  '03 · Gateway nicht erreichbar (Dienst unten)': () => (
    <Case03 name="03 · Gateway nicht erreichbar" note="Adresse nennen, Wege fuer jetzt und spaeter" />
  ),
  '04 · Werkzeug hat abgelehnt': () => (
    <Case04 name="04 · Werkzeug abgelehnt" note="Werkzeugname, Grund, Weg ueber die Freigabe" />
  ),
  '05 · Kontext ist voll': () => (
    <Case05 name="05 · Kontext ist voll" note="belegt versus gesamt, Verlauf verdichten oder neue Sitzung" />
  ),
  '06 · Anbieter-Limit pro Minute': () => (
    <Case06 name="06 · Anbieter-Limit pro Minute" note="der genaue Zeitpunkt des Weitermachens als Countdown" />
  ),
  '07 · Datei ueber dem Anbieter-Limit': () => (
    <Case07 name="07 · Datei ueber dem Anbieter-Limit" note="abgelehnte Dateien und die Wahrheit: 0 uebertragen" />
  ),
  '08 · Netzfehler mitten in der Antwort': () => (
    <Case08 name="08 · Netzfehler mitten in der Antwort" note="der Text steht schon da, ab hier fortsetzen — kein Verlust" />
  ),
  '09 · Absturz des Werkzeugs — Endzustand (ohne Animation)': () => (
    <Case09 name="09 · Absturz des Werkzeugs" note="Bericht-Pfad angeben, Arbeitsstand kann gerettet werden" />
  ),
  '10 · Dunkle Vollbild-Fassung des Favoriten (01)': () => (
    <Case10 name="10 · Favorit DARKMODE" note="die helle Karte aus 01, als volle dunkle Fassung" />
  )
}
