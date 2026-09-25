/**
 * Voice-Meldungen im Verlauf (Kategorie Voice) — 10 animierte Varianten.
 *
 * Hintergrund: die Live-Lenkung der Voice (Pause, Umleiten, Zweifel, Tor,
 * Warnung, Rueckfrage) gibt es im Produkt, im Chat hat sie KEINE UI. Die
 * Designvorgabe: kurze Meldung IM Gespraechsverlauf, Begruendung und Belege
 * erst aufklappbar — kein Statusband, kein eigenes Panel.
 *
 * Vorlage (nur gelesen): fixtures/AppChat.fixture.tsx (Verlaufsschrift 13px/1.5,
 * Spalte 560, Meta 11px tabular) und fixtures/ChatBarRef.fixture.tsx
 * (Aufklappen mit Feder, echter Schalter, Kontrollen als Pille).
 *
 * Palette ausschliesslich aus ./_tokens (nur Schwarz/Weiss/Grau; Kontrast A2/A3).
 * Reduced Motion und Tastaturbedienung: useBarrierefrei() auf der Buehne (A1/A5).
 * Nur Schwarz/Weiss/Grau, Symbole nur aus @tabler/icons-react, Feder durchgehend.
 * Jede Variante hat den hell/dunkel-Umschalter im Kopf (10 startet dunkel).
 *
 * Echte Zustaende: 02 pausiert/geht weiter, 03 uebernimmt den getippten Text,
 * 06 haelt den laufenden Balken wirklich an, 08 sendet eine echte Antwort.
 */
import { motion, AnimatePresence, animate } from 'motion/react'
import React, { useState, useEffect, useRef } from 'react'
import { IconAlertTriangle, IconArrowRight, IconCheck, IconChevronRight, IconClock, IconPlayerPause, IconPlayerPlay, IconRoute, IconSend, IconShieldCheck, IconWaveSine, IconX } from '@tabler/icons-react'

// @ts-expect-error — Laufzeit-Alias (siehe vite.config.ts)
import { ThinkingOrb } from 'thinking-orbs'
import { LIGHT, DARK, MONO, SANS, SPRING, RADIUS, TEXT } from './_tokens'
import { useBarrierefrei } from './_barrierefrei'

/* Farben, Schriften, Federn, Radien und Schriftgroessen: ./_tokens (eine Wahrheit) */
const TXT = TEXT.body   // Verlaufsschrift (0.8125rem der Original-App)
const LINE = 1.5
const META = TEXT.meta  // Mono-Meta — Untergrenze 11 px (Befund B3)

/* ------------------------------------------------------------- Grundbausteine */

/** Kontrolle: mindestens 30 px hoch, Radius 10, Fuellung = Tinte wenn aktiv. */
function KleinKnopf ({ P, aktiv, children, ...rest }) {
  return (
    <motion.span whileHover={{ scale: 1.03 }} whileTap={{ scale: 0.95 }} transition={SPRING}
      style={{ display: 'inline-flex', alignItems: 'center', gap: 5, minHeight: 30, padding: '0 10px',
        border: `1px solid ${aktiv ? P.ink : P.line}`, background: aktiv ? P.ink : P.surface,
        color: aktiv ? P.bg : P.ink, borderRadius: RADIUS.row, fontFamily: SANS, fontSize: 11.5,
        cursor: 'pointer', whiteSpace: 'nowrap' }}
      {...rest}>{children}</motion.span>
  )
}

/** Gespraechsverlauf als Flaeche — die Voice-Zeile sitzt IN diesem Verlauf. */
function Verlauf ({ P, children }) {
  return (
    <div data-verlauf style={{ width: '100%', maxWidth: 520, border: `1px solid ${P.line}`, borderRadius: RADIUS.dialog,
      background: P.surface, padding: '12px 14px', display: 'flex', flexDirection: 'column', gap: 9, boxSizing: 'border-box' }}>
      {children}
    </div>
  )
}

const Nutzerzeile = ({ P, children }) => (
  <div data-nutzerzeile style={{ alignSelf: 'flex-end', maxWidth: '86%', padding: '7px 11px', borderRadius: RADIUS.module,
    background: P.hair, border: `1px solid ${P.line}`, fontSize: TXT, lineHeight: 1.3, color: P.ink }}>{children}</div>
)

const Echozeile = ({ P, children }) => (
  <div data-echozeile style={{ alignSelf: 'flex-start', maxWidth: '86%', paddingLeft: 16,
    fontSize: TXT, lineHeight: LINE, color: P.sub }}>{children}</div>
)

const BelagIcon = ({ art, farbe }) => art === 'fehlt'
  ? <IconX size={12} stroke={2.4} color={farbe} />
  : art === 'offen'
    ? <IconClock size={12} stroke={2.2} color={farbe} />
    : <IconCheck size={12} stroke={2.6} color={farbe} />

/** Belegliste im aufgeklappten Teil: Datei/Zeile links, Wert rechts, Art als Zeichen. */
function Belege ({ P, titel = 'Belege', rows }) {
  return (
    <div data-belege style={{ marginTop: 9, borderTop: `1px solid ${P.hair}`, paddingTop: 8, display: 'flex', flexDirection: 'column', gap: 5 }}>
      <span style={{ fontFamily: MONO, fontSize: META, letterSpacing: 1.1, textTransform: 'uppercase', color: P.faint }}>{titel}</span>
      {rows.map((r, i) => (
        <motion.div key={r[0]} initial={{ opacity: 0, x: -4 }} animate={{ opacity: 1, x: 0 }}
          transition={{ ...SPRING, delay: 0.05 + i * 0.05 }}
          style={{ display: 'flex', alignItems: 'center', gap: 7, fontSize: 11.5, lineHeight: '17px' }}>
          <span style={{ display: 'inline-flex', flex: '0 0 auto' }}>
            <BelagIcon art={r[2]} farbe={r[2] === 'belegt' ? P.ink : P.faint} />
          </span>
          <span style={{ fontFamily: MONO, fontSize: 11, color: r[2] === 'belegt' ? P.ink : P.sub,
            overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r[0]}</span>
          <span style={{ marginLeft: 'auto', fontFamily: MONO, fontSize: META, color: P.faint, whiteSpace: 'nowrap' }}>{r[1]}</span>
        </motion.div>
      ))}
    </div>
  )
}

/**
 * Die Voice-Zeile selbst: duenne Randlinie links (Geometrie statt Statusband),
 * Kopf mit Orb, Marke, Lauftext, Zeit und Caret. Aussage in Verlaufsschrift,
 * Details erst nach dem Aufklappen.
 */
function Zeile ({ P, dark, orb, label = 'Voice', ticker, zeit = '14:36', rechts, body, children,
                  offenStart = false, offenSteuerung, delay = 0, haken = 'voiceline' }) {
  const [eigen, setEigen] = useState(offenStart)
  const offen = offenSteuerung ? offenSteuerung[0] : eigen
  const schalten = () => (offenSteuerung ? offenSteuerung[1](o => !o) : setEigen(o => !o))
  return (
    <motion.div data-voiceline={haken} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }}
      transition={{ ...SPRING, delay }} style={{ display: 'flex', gap: 10, alignItems: 'stretch' }}>
      <span aria-hidden style={{ width: 1, flex: '0 0 auto', background: P.line, margin: '3px 0' }} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
          <span onClick={schalten} title={offen ? 'Details einklappen' : 'Details aufklappen'}
            style={{ display: 'inline-flex', alignItems: 'center', gap: 7, cursor: 'pointer', minWidth: 0 }}>
            {orb && (
              <span style={{ display: 'inline-flex', flex: '0 0 auto' }}>
                <ThinkingOrb state={orb} size={20} theme={dark ? 'dark' : 'light'} />
              </span>
            )}
            <IconWaveSine size={12} stroke={2} color={P.faint} />
            <span style={{ fontFamily: MONO, fontSize: META, letterSpacing: 1, textTransform: 'uppercase',
              color: P.faint, whiteSpace: 'nowrap' }}>{label}</span>
            {ticker && (
              <motion.span animate={{ opacity: [1, 0.45, 1] }} transition={{ duration: 1.8, repeat: Infinity, ease: 'easeInOut' }}
                style={{ fontSize: 11.5, color: P.sub, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{ticker}</motion.span>
            )}
            <motion.span animate={{ rotate: offen ? 90 : 0 }} transition={SPRING}
              style={{ display: 'inline-flex', color: P.faint, flex: '0 0 auto' }}>
              <IconChevronRight size={11} stroke={2.2} />
            </motion.span>
          </span>
          {rechts && <span style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 8 }}>{rechts}</span>}
          <span style={{ marginLeft: rechts ? 0 : 'auto', fontFamily: MONO, fontSize: META, color: P.faint,
            fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap' }}>{zeit}</span>
        </div>
        <div style={{ marginTop: 4, fontSize: TXT, lineHeight: LINE, color: P.ink }}>{children}</div>
        {body && (
          <AnimatePresence initial={false}>
            {offen && (
              <motion.div key='details' data-voicebody initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }}
                exit={{ height: 0, opacity: 0 }} transition={SPRING} style={{ overflow: 'hidden' }}>{body}</motion.div>
            )}
          </AnimatePresence>
        )}
      </div>
    </motion.div>
  )
}

/* ------------------------------------------------------- 01 · Voice-Meldung im Verlauf
 * Der Grundfall: eine kurze Aussage der Voice, Details (Begruendung + Belege)
 * erst aufklappbar. Aufgeklappt gestartet, damit das Zuklappen messbar ist.
 * Quelle der Masse: AppChat 01/02 (13px/1.5, Einzug 16px, Meta 11px tabular).
 */
function VoiceGrundfall ({ P, dark }) {
  return (
    <Verlauf P={P}>
      <Nutzerzeile P={P}>zerleg das in drei spuren</Nutzerzeile>
      <Zeile P={P} dark={dark} orb='working' ticker='liest die Verlaufs-Spalte' zeit='14:36' offenStart
        body={
          <>
            <p style={{ margin: 0, fontSize: 12.5, lineHeight: 1.6, color: P.sub }}>
              Begruendung: die Vorlage nennt 13px/1.5 bei 44rem Spaltenbreite. Ich nehme erst die
              Zahlen, dann Farben und Raender.
            </p>
            <Belege P={P} rows={[
              ['02-chat.md · 31-43', '13px / 1.5', 'belegt'],
              ['user-message.tsx:67', '86% · 12px', 'belegt'],
              ['Klapphoehe 40 ms', 'Messreihe', 'offen']
            ]} />
          </>
        }>
        Ich halte die Spur bei der Verlaufs-Spalte.
      </Zeile>
      <Echozeile P={P}>drei spuren stehen, jede mit eigener Zeile.</Echozeile>
    </Verlauf>
  )
}

/* ------------------------------------------------------- 02 · Pause
 * Die Voice haelt an: Orb wechselt working -> breathing, die Zeile nennt den
 * Grund, der Schalter heisst dann 'fortsetzen'. Echter Zustand (useState),
 * der Schalter oeffnet und schliesst zugleich die Details.
 */
function VoicePause ({ P, dark }) {
  const [laeuft, setLaeuft] = useState(true)
  const [offen, setOffen] = useState(false)
  const umschalten = () => {
    const neu = !laeuft
    setLaeuft(neu)
    setOffen(!neu)
  }
  return (
    <Verlauf P={P}>
      <Echozeile P={P}>Ich messe jetzt die acht Karten der Reihe nach.</Echozeile>
      <Zeile P={P} dark={dark} haken='voicepause'
        orb={laeuft ? 'working' : 'breathing'}
        label={laeuft ? 'Voice · laeuft' : 'Voice · angehalten'}
        ticker={laeuft ? 'misst Werkzeugkarte 3 von 8' : undefined}
        zeit='14:37' offenStart={false} offenSteuerung={[offen, setOffen]}
        rechts={
          <KleinKnopf P={P} aktiv={!laeuft} onClick={umschalten} title={laeuft ? 'Voice anhalten' : 'Voice fortsetzen'}>
            {laeuft
              ? <><IconPlayerPause size={11} stroke={2.4} />anhalten</>
              : <><IconPlayerPlay size={11} stroke={2.4} />fortsetzen</>}
          </KleinKnopf>
        }
        body={
          <>
            <p style={{ margin: 0, fontSize: 12.5, lineHeight: 1.6, color: P.sub }}>
              Grund: ohne die Messreihe der Klapphoehe wuerde ich den Zustand raten. Ich stehe bei
              Schritt 3 von 8, nichts ist verloren.
            </p>
            <Belege P={P} titel='Stand' rows={[
              ['Werkzeugkarte 1-2 gemessen', '2/8', 'belegt'],
              ['Werkzeugkarte 3 wartet', 'angehalten', 'offen'],
              ['Darkmode-Gegenprobe', 'offen', 'offen']
            ]} />
          </>
        }>
        {laeuft ? 'Ich messe weiter und melde mich mit Zahlen.' : 'Ich halte an, bis die Messreihe steht.'}
      </Zeile>
      <Echozeile P={P}>gut, dann erst die Zahlen.</Echozeile>
    </Verlauf>
  )
}

/* ------------------------------------------------------- 03 · Umleiten
 * Der Mensch gibt eine neue Richtung ein; die Zeile zeigt sie und quittiert mit
 * 'uebernommen'. Der Haken springt bei jeder Uebernahme neu auf (eigener key),
 * der Text kommt wirklich aus dem Eingabefeld.
 */
function VoiceUmleiten ({ P, dark }) {
  const [wert, setWert] = useState('lieber erst die Kanten messen')
  const [richtung, setRichtung] = useState('lieber erst die Kanten messen')
  const [stand, setStand] = useState(0)
  const [offen, setOffen] = useState(false)
  const gib = () => {
    const t = wert.trim()
    if (!t) return
    setRichtung(t)
    setStand(n => n + 1)
    setOffen(true)
  }
  return (
    <Verlauf P={P}>
      <Echozeile P={P}>Ich messe jetzt die Knoepfe in allen acht Karten.</Echozeile>
      <div data-voiceeingabe style={{ display: 'flex', alignItems: 'center', gap: 6, paddingLeft: 11 }}>
        <IconRoute size={13} stroke={2} color={P.faint} />
        <input value={wert} onChange={e => setWert(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') gib() }} placeholder='neue Richtung'
          style={{ flex: 1, minWidth: 0, height: 34, padding: '0 11px', borderRadius: RADIUS.row,
            border: `1px solid ${P.line}`, background: P.bg, color: P.ink, fontFamily: SANS, fontSize: 12.5 }} />
        <KleinKnopf P={P} aktiv onClick={gib} title='Richtung uebernehmen'>
          <IconArrowRight size={11} stroke={2.4} />umleiten
        </KleinKnopf>
      </div>
      <Zeile P={P} dark={dark} orb='working' label='Voice · umgeleitet' zeit='14:38' offenSteuerung={[offen, setOffen]}
        haken='voiceumleitung'
        rechts={stand > 0 && (
          <motion.span key={stand} initial={{ opacity: 0, scale: 0.5 }} animate={{ opacity: 1, scale: 1 }} transition={SPRING}
            style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontFamily: MONO, fontSize: META, color: P.ink }}>
            <IconCheck size={11} stroke={2.6} />uebernommen
          </motion.span>
        )}
        body={
          <>
            <p style={{ margin: 0, fontSize: 12.5, lineHeight: 1.6, color: P.sub }}>
              Deine Richtung ersetzt meine Reihenfolge. Ich verwerfe die alte Spur und arbeite ab
              hier nach deiner Vorgabe.
            </p>
            <Belege P={P} titel='Neue Reihenfolge' rows={[
              [richtung, 'Schritt 1', 'belegt'],
              ['danach Farben und Raender', 'Schritt 2', 'offen']
            ]} />
          </>
        }>
        Verstanden: {richtung}
        {stand > 0 ? ' — ich arbeite danach.' : ' (noch nicht uebernommen).'}
      </Zeile>
    </Verlauf>
  )
}

/* ------------------------------------------------------- 04 · Zweifel geaeussert
 * Die Voice sagt selbst, dass es noch nicht belegt ist, und listet die offenen
 * Punkte auf. Orb 'searching' — die Voice sucht Belege, sie laeuft nicht blind.
 */
function VoiceZweifel ({ P, dark }) {
  return (
    <Verlauf P={P}>
      <Echozeile P={P}>Die acht Karten stehen, ich melde sie als fertig.</Echozeile>
      <Zeile P={P} dark={dark} orb='searching' label='Voice · Zweifel' ticker='sammelt offene Punkte'
        zeit='14:39' offenStart
        body={
          <>
            <p style={{ margin: 0, fontSize: 12.5, lineHeight: 1.6, color: P.sub }}>
              Ich glaube der Antwort noch nicht. Solange die Zahlen fehlen, nenne ich es Entwurf
              und nicht Ergebnis.
            </p>
            <Belege P={P} titel='Offene Punkte' rows={[
              ['Klapphoehe 40-50 ms abtasten', 'Messreihe fehlt', 'offen'],
              ['hell/dunkel gleiche Knotenzahl', 'Zaehlung fehlt', 'offen'],
              ['Rahmendicke zaehlt zur Rect', 'Toleranz offen', 'offen']
            ]} />
          </>
        }>
        Das ist noch nicht belegt.
      </Zeile>
      <Echozeile P={P}>dann belege es mir, bevor du fertig sagst.</Echozeile>
    </Verlauf>
  )
}

/* ------------------------------------------------------- 05 · Tor verweigert
 * 'fertig ist es erst mit Beleg' — die Voice verweigert die Abnahme und nennt
 * die fehlenden Nachweise. Kein Orb: hier laeuft nichts, das Tor ist zu.
 */
function VoiceGate ({ P, dark }) {
  return (
    <Verlauf P={P}>
      <Echozeile P={P}>Ich wuerde es jetzt abnehmen.</Echozeile>
      <Zeile P={P} dark={dark} haken='voicegate' label='Voice · Tor' zeit='14:40' offenStart
        rechts={
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontFamily: MONO, fontSize: META, color: P.faint }}>
            <IconShieldCheck size={12} stroke={2} color={P.faint} />zu
          </span>
        }
        body={
          <>
            <p style={{ margin: 0, fontSize: 12.5, lineHeight: 1.6, color: P.sub }}>
              Kein Abnehmen ohne Nachweis. Erst die drei Luecken, dann oeffne ich das Tor.
            </p>
            <Belege P={P} titel='Fehlende Nachweise' rows={[
              ['Feder-Aufklappen der Spur', 'Messreihe fehlt', 'fehlt'],
              ['Rect der Buehne gegen Werkzeugkarte', 'Messung fehlt', 'fehlt'],
              ['Karte bleibt im Einzug', 'Sichtpruefung fehlt', 'fehlt'],
              ['Anker und Farben der Karte', '28/28', 'belegt']
            ]} />
          </>
        }>
        Fertig ist es erst mit Beleg.
      </Zeile>
    </Verlauf>
  )
}

/* ------------------------------------------------------- 06 · Warnung
 * Drohende Zeit- und Kostenueberschreitung mit Obergrenze; 'anhalten' stoppt
 * den laufenden Balken wirklich (animate-Instanz wird gestoppt), 'weiterlaufen'
 * setzt ihn an derselben Stelle fort.
 */
function VoiceWarnung ({ P, dark }) {
  const [angehalten, setAngehalten] = useState(false)
  const balken = useRef(null)
  const steuerung = useRef(null)
  useEffect(() => {
    if (!balken.current) return
    balken.current.style.width = '0%'
    steuerung.current = animate(balken.current, { width: '85%' }, { duration: 1.4, ease: 'easeInOut' })
    return () => { if (steuerung.current) steuerung.current.stop() }
  }, [])
  const anhalten = () => {
    if (steuerung.current) steuerung.current.stop()
    setAngehalten(true)
  }
  const weiter = () => {
    setAngehalten(false)
    if (balken.current) steuerung.current = animate(balken.current, { width: '85%' }, { duration: 1.2, ease: 'easeInOut' })
  }
  return (
    <Verlauf P={P}>
      <Echozeile P={P}>Lauf ruhig weiter bis zum Ende.</Echozeile>
      <Zeile P={P} dark={dark} haken='voicewarnung'
        orb={angehalten ? 'breathing' : 'shaping'}
        label={angehalten ? 'Voice · gestoppt' : 'Voice · Warnung'}
        ticker={angehalten ? undefined : 'rechnet gegen die Obergrenze'}
        zeit='14:43' offenStart
        rechts={angehalten && (
          <KleinKnopf P={P} aktiv onClick={weiter} title='Warnung zuruecknehmen'>
            <IconPlayerPlay size={11} stroke={2.4} />weiterlaufen
          </KleinKnopf>
        )}
        body={
          <>
            <div data-warnblock style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 2 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 7, fontSize: 11.5, color: P.sub }}>
                <IconAlertTriangle size={12} stroke={2.2} color={P.ink} />
                <span style={{ fontFamily: MONO, fontSize: 11 }}>3,40 EUR von 4,00 EUR Obergrenze</span>
                <span style={{ marginLeft: 'auto', fontFamily: MONO, fontSize: META, color: P.faint }}>85 %</span>
              </div>
              <span style={{ display: 'block', height: 3, background: P.hair, borderRadius: RADIUS.micro, overflow: 'hidden' }}>
                <span ref={balken} data-warnbalken style={{ display: 'block', height: 3, width: 0, background: P.ink }} />
              </span>
              <div style={{ fontFamily: MONO, fontSize: 11, color: P.faint }}>
                Restzeit ca. 06:20 · Abbruch in 40 s spart 1,10 EUR
              </div>
            </div>
            <div style={{ display: 'flex', gap: 6, marginTop: 9 }}>
              {angehalten
                ? <span style={{ fontFamily: MONO, fontSize: 11, color: P.sub }}>angehalten bei 3,40 EUR — nichts laeuft weiter.</span>
                : <KleinKnopf P={P} aktiv onClick={anhalten} title='Lauf anhalten'>
                    <IconPlayerPause size={11} stroke={2.4} />anhalten
                  </KleinKnopf>}
            </div>
            <Belege P={P} titel='Kostengrenze' rows={[
              ['Obergrenze der Sitzung', '4,00 EUR', 'belegt'],
              ['bisher verbraucht', '3,40 EUR', 'belegt'],
              ['Restbudget', '0,60 EUR', 'offen']
            ]} />
          </>
        }>
        Ich laufe aus dem Rahmen: Zeit und Kosten treffen die Obergrenze.
      </Zeile>
    </Verlauf>
  )
}

/* ------------------------------------------------------- 07 · Erfolg (Endzustand statisch)
 * Kurze Bestaetigung mit Nachweis-Zahl. Der Endzustand wird ausdruecklich NICHT
 * animiert (einfache Zeile, kein motion, kein Orb) — nur die laufende Zeile
 * darueber bewegt sich.
 */
function VoiceErfolg ({ P, dark }) {
  return (
    <Verlauf P={P}>
      <Echozeile P={P}>Ergebnis bitte knapp.</Echozeile>
      <Zeile P={P} dark={dark} haken='voicelauf' orb='working' ticker='prueft die Nachweise' zeit='14:42'
        body={
          <Belege P={P} rows={[
            ['acht Karten gemessen', '8/8', 'belegt'],
            ['Klapphoehe monoton', '142 px', 'belegt'],
            ['hell/dunkel gleich', 'identisch', 'belegt']
          ]} />
        }>
        Ich sammle die Nachweise ein.
      </Zeile>
      <div data-voicefertig style={{ display: 'flex', gap: 10, alignItems: 'stretch' }}>
        <span aria-hidden style={{ width: 1, flex: '0 0 auto', background: P.line, margin: '3px 0' }} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
            <IconCheck size={13} stroke={2.6} color={P.ink} />
            <span style={{ fontFamily: MONO, fontSize: META, letterSpacing: 1, textTransform: 'uppercase', color: P.faint }}>Voice · Fertig</span>
            <span style={{ marginLeft: 'auto', fontFamily: MONO, fontSize: META, color: P.faint }}>14:42</span>
          </div>
          <div style={{ marginTop: 4, fontSize: TXT, lineHeight: LINE, color: P.ink }}>
            Fertig: alle 9 Nachweise belegt — der Verlauf steht.
          </div>
          <div style={{ marginTop: 4, fontFamily: MONO, fontSize: 11, color: P.sub }}>
            8/8 Karten · 1/1 Messreihe · Tor offen
          </div>
        </div>
      </div>
    </Verlauf>
  )
}

/* ------------------------------------------------------- 08 · Rueckfrage mit zwei Antwortwegen
 * Die Voice fragt und bietet zwei Wege an: freies Textfeld oder einen der zwei
 * Vorschlaege. Senden ist ein echter Zustandswechsel und quittiert darunter.
 */
function VoiceRueckfrage ({ P, dark }) {
  const [wert, setWert] = useState('')
  const [antwort, setAntwort] = useState(null)
  const vorschlaege = ['Kanten zuerst, dann Farben', 'Erst den Darkmode gegenpruefen']
  const sende = () => {
    const t = wert.trim()
    if (!t) return
    setAntwort(t)
    setWert('')
  }
  return (
    <Verlauf P={P}>
      <Nutzerzeile P={P}>mach weiter</Nutzerzeile>
      <Zeile P={P} dark={dark} haken='voicerueckfrage' orb='listening' label='Voice · Rueckfrage'
        ticker='wartet auf deine Wahl' zeit='14:41' offenStart
        body={
          <>
            <p style={{ margin: 0, fontSize: 12.5, lineHeight: 1.6, color: P.sub }}>
              Beide Wege sind richtig; der eine kostet Zeit, der andere Genauigkeit. Ohne deine
              Wahl rate ich und das waere geraten.
            </p>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 9 }}>
              <input value={wert} onChange={e => setWert(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter') sende() }} placeholder='Antwort tippen'
                style={{ flex: 1, minWidth: 0, height: 34, padding: '0 11px', borderRadius: RADIUS.row,
                  border: `1px solid ${P.line}`, background: P.bg, color: P.ink, fontFamily: SANS, fontSize: 12.5 }} />
              <KleinKnopf P={P} aktiv onClick={sende} title='Antwort senden'>
                <IconSend size={11} stroke={2.4} />senden
              </KleinKnopf>
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 7 }}>
              {vorschlaege.map(v => (
                <KleinKnopf key={v} P={P} onClick={() => setWert(v)} title='Vorschlag einsetzen'>{v}</KleinKnopf>
              ))}
            </div>
          </>
        }>
        Womit soll ich anfangen: Kanten messen oder den Darkmode?
      </Zeile>
      <AnimatePresence initial={false}>
        {antwort && (
          <motion.div key={antwort} data-voiceantwort initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }} transition={SPRING} style={{ overflow: 'hidden' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11.5, color: P.sub, paddingTop: 2 }}>
              <IconCheck size={12} stroke={2.6} color={P.ink} />
              <span style={{ fontFamily: MONO, fontSize: 11 }}>deine Antwort: {antwort}</span>
              <span style={{ marginLeft: 'auto', fontFamily: MONO, fontSize: META, color: P.faint }}>uebernommen</span>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </Verlauf>
  )
}

/* ------------------------------------------------------- 09 · Sehr kompakt
 * Eine einzige Zeile am Rand des Verlaufs: kein Rahmen, kein Orb, keine
 * Details — nur eine Notiz mit duenner Marke links.
 */
function VoiceKompakt ({ P, dark }) {
  return (
    <Verlauf P={P}>
      <Echozeile P={P}>Ich nehme die Karte mit dem Fehlerpfad.</Echozeile>
      <div data-voicekompakt style={{ display: 'flex', alignItems: 'center', gap: 8, marginLeft: -6 }}>
        <motion.span initial={{ scaleY: 0.3 }} animate={{ scaleY: 1 }} transition={SPRING}
          style={{ width: 2, height: 11, background: P.line, flex: '0 0 auto', transformOrigin: 'top' }} />
        <motion.span initial={{ opacity: 0, x: -3 }} animate={{ opacity: 1, x: 0 }} transition={SPRING}
          style={{ fontFamily: MONO, fontSize: 11, color: P.faint, overflow: 'hidden',
            textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          voice · 14:37 · angehalten, bis die Rechnung steht
        </motion.span>
      </div>
      <Nutzerzeile P={P}>und dann weiter</Nutzerzeile>
    </Verlauf>
  )
}

/* ------------------------------------------------------- Buehne + Exporte */

function Buehne ({ name, note, las = 'hell', breit = false, children }) {
  useBarrierefrei()   // A1 reduzierte Bewegung, A5 Tastatur (Befund 1+2)
  const [dunkel, setDunkel] = useState(las === 'dunkel')
  const P = dunkel ? DARK : LIGHT
  return (
    <div data-stage style={{ padding: breit ? 26 : 20, background: P.bg, fontFamily: SANS, width: '100%',
      maxWidth: breit ? 760 : 640, minHeight: breit ? 420 : undefined, boxSizing: 'border-box', color: P.ink, textAlign: 'left' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
        <div style={{ fontFamily: MONO, fontSize: 12, fontWeight: 600, color: P.ink }}>{name}</div>
        <span style={{ flex: 1 }} />
        <motion.span data-themetoggle onClick={() => setDunkel(d => !d)} whileTap={{ scale: 0.94 }} transition={SPRING}
          title='hell/dunkel schalten' style={{ fontFamily: MONO, fontSize: 11, padding: '4px 10px',
            border: `1px solid ${P.line}`, borderRadius: RADIUS.pill, cursor: 'pointer', color: P.ink, background: P.surface }}>
          {dunkel ? 'DUNKEL' : 'HELL'}
        </motion.span>
      </div>
      <div style={{ fontFamily: MONO, fontSize: 11, color: P.sub, marginBottom: 12 }}>{note}</div>
      {children({ P, dark: dunkel })}
    </div>
  )
}

export default {
  '01 · Voice-Meldung im Verlauf': () => (
    <Buehne name='01 · Voice-Meldung im Verlauf' note='kurze Aussage, Details aufklappbar (Begruendung + Belege), kein Statusband'>
      {({ P, dark }) => <VoiceGrundfall P={P} dark={dark} />}
    </Buehne>
  ),
  '02 · Pause mit Grund und fortsetzen': () => (
    <Buehne name='02 · Pause' note='der Schalter haelt die Voice wirklich an: Orb working -> breathing, Grund in den Details, dann fortsetzen'>
      {({ P, dark }) => <VoicePause P={P} dark={dark} />}
    </Buehne>
  ),
  '03 · Umleiten durch den Menschen': () => (
    <Buehne name='03 · Umleiten' note='neue Richtung ins Feld tippen (Enter oder umleiten), die Zeile zeigt sie und quittiert mit uebernommen'>
      {({ P, dark }) => <VoiceUmleiten P={P} dark={dark} />}
    </Buehne>
  ),
  '04 · Zweifel geaeussert': () => (
    <Buehne name='04 · Zweifel geaeussert' note='das ist noch nicht belegt — mit den offenen Punkten, Orb searching'>
      {({ P, dark }) => <VoiceZweifel P={P} dark={dark} />}
    </Buehne>
  ),
  '05 · Gate verweigert': () => (
    <Buehne name='05 · Gate verweigert' note='fertig ist es erst mit Beleg: Liste der fehlenden Nachweise, Tor bleibt zu, kein Orb'>
      {({ P, dark }) => <VoiceGate P={P} dark={dark} />}
    </Buehne>
  ),
  '06 · Warnung mit Obergrenze und anhalten': () => (
    <Buehne name='06 · Warnung' note='Zeit- und Kostenueberschreitung: Balken laeuft echt und stoppt bei anhalten, weiterlaufen setzt fort'>
      {({ P, dark }) => <VoiceWarnung P={P} dark={dark} />}
    </Buehne>
  ),
  '07 · Erfolg mit Nachweis-Zahl (statisch)': () => (
    <Buehne name='07 · Erfolg' note='kurze Bestaetigung mit Nachweis-Zahl; der Endzustand ist statisch — keine Bewegung, kein Orb'>
      {({ P, dark }) => <VoiceErfolg P={P} dark={dark} />}
    </Buehne>
  ),
  '08 · Rueckfrage mit zwei Antwortwegen': () => (
    <Buehne name='08 · Rueckfrage' note='Textfeld plus zwei Vorschlaege; senden ist ein echter Wechsel und quittiert darunter'>
      {({ P, dark }) => <VoiceRueckfrage P={P} dark={dark} />}
    </Buehne>
  ),
  '09 · Sehr kompakt: eine Zeile am Rand': () => (
    <Buehne name='09 · Sehr kompakt' note='eine einzige Voice-Notiz am Rand des Verlaufs, ohne Rahmen, Orb oder Details'>
      {({ P, dark }) => <VoiceKompakt P={P} dark={dark} />}
    </Buehne>
  ),
  '10 · Favorit in dunkel (Vollbild)': () => (
    <Buehne name='10 · Favorit dunkel' note='Favorit = 02 Pause, Vollbild dunkel; der Umschalter zeigt die helle Gegenprobe' las='dunkel' breit>
      {({ P, dark }) => <VoicePause P={P} dark={dark} />}
    </Buehne>
  )
}
