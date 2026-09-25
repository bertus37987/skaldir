/**
 * Startschirm, Hintergrund-Logo (CLI-Stil) und Sprüche — Kategorie G.
 *
 * Owner-Wunsch: coolerer Hintergrund, ein eigenes Logo im CLI-Stil und ein
 * eigener Hintergrund, auf dem wechselnde Sprüche stehen wie
 * „Give me a goal I actually complete it."
 *
 * Aufbau: 10 Bühnen, Varianten 1–8 hell/dunkel umschaltbar (Knopf im Kopf),
 * Variante 10 ist die dunkle Vollbild-Fassung des Favoriten (06).
 * Nur Schwarz/Weiß/Grau, Symbole nur aus @tabler/icons-react, sonst Geometrie/Blockzeichen.
 */
import { motion, AnimatePresence, animate } from 'motion/react'
import React, { useState, useEffect } from 'react'
import { IconTerminal } from '@tabler/icons-react'

/* Farben, Schrift, Radien, Federn: eine Quelle fuer alle Fixtures (_tokens.ts). */
import { LIGHT, DARK, MONO, SANS, SPRING, RADIUS, TEXT } from './_tokens'
import { useBarrierefrei } from './_barrierefrei'

/** Die Sammlung aller Sprüche — hier rotieren 06 und die Liste in 07 durch. */
const SPRUECHE = [
  'Give me a goal I actually complete it.',
  'Ich messe, ich behaupte nicht.',
  'Ein Ziel, ein Nachweis.',
  'Zeig mir das Ziel, ich zeige dir den Beleg.',
  'Kein Fortschritt ohne Messung.',
  'Langsam ist auch eine Geschwindigkeit.'
]

/** Blinkender Blockcursor — das Wahrzeichen des CLI-Stils. */
const Cursor = ({ P, size = 12 }) => (
  <motion.span animate={{ opacity: [1, 1, 0, 0] }} transition={{ duration: 1.1, repeat: Infinity, times: [0, 0.45, 0.5, 1], ease: 'linear' }}
    style={{ display: 'inline-block', width: size * 0.55, height: size, background: P.ink, verticalAlign: 'text-bottom' }} />
)

/** Dezente kleine Welle/Orb aus reiner Geometrie (kein Symbol). */
const MiniWave = ({ P, size = 22 }) => (
  <span style={{ display: 'inline-flex', alignItems: 'center', width: size, height: size * 0.5 }}>
    <svg width={size} height={size * 0.5} viewBox="0 0 20 10">
      <motion.polyline points="0,5 2,5 4,2 6,8 8,3 10,6 12,4 14,5 20,5" fill="none"
        stroke={P.faint} strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round"
        initial={{ pathLength: 0 }} animate={{ pathLength: 1 }} transition={{ duration: 1.4, ease: 'easeInOut' }} />
    </svg>
  </span>
)

/** Kopf einer Bühne: Titelzeile + Umschalter hell/dunkel (nur wo erlaubt). */
const Kopf = ({ P, titel, dunkelAktiv, setzeDunkel }) => (
  <div style={{ fontFamily: MONO, fontSize: 12, fontWeight: 600, color: P.ink, marginBottom: 2 }}>
    {titel}
    <div style={{ fontFamily: MONO, fontSize: TEXT.meta, fontWeight: 400, color: P.sub, margin: '3px 0 12px' }}>
      {setzeDunkel ? 'Umschalter oben rechts · hell/dunkel' : 'Vollbild dunkel — der Favorit in seiner Fassung'}
    </div>
    {setzeDunkel && (
      <motion.span data-themetoggle onClick={() => setzeDunkel(d => !d)} whileTap={{ scale: 0.94 }} transition={SPRING}
        title="hell/dunkel umschalten"
        style={{ position: 'absolute', right: 16, top: 24, zIndex: 9, fontFamily: MONO, fontSize: TEXT.meta, padding: '3px 9px',
          border: `1px solid ${P.line}`, background: P.surface,
          color: P.ink, cursor: 'pointer' }}>
        {dunkelAktiv ? 'dunkel → hell' : 'hell → dunkel'}
      </motion.span>
    )}
  </div>
)

/** Äußerer Rahmen jeder Variante: data-stage, Titelzeile, dann die Bühne. */
function Buehne({ titel, note, dunkel, umschaltbar, children, hoch = 380 }) {
  useBarrierefrei()   // A1 reduzierte Bewegung + A5 Tastatur — erste Zeile jeder Buehne
  const [d, setD] = useState(!!dunkel)
  const P = d ? DARK : LIGHT
  return (
    <div data-stage style={{ padding: 20, background: d ? DARK.bg : LIGHT.bg, fontFamily: SANS, width: '100%', maxWidth: 900, boxSizing: 'border-box' }}>
      <Kopf P={P} titel={titel} dunkelAktiv={d} setzeDunkel={umschaltbar ? setD : null} />
      <div style={{ position: 'relative', height: hoch, borderRadius: RADIUS.dialog, border: `1px solid ${P.line}`, background: P.bg, overflow: 'hidden' }}>
        {typeof children === 'function' ? children({ P, dunkel: d }) : children}
      </div>
      <div style={{ fontFamily: MONO, fontSize: TEXT.meta, color: d ? DARK.sub : LIGHT.sub, marginTop: 8 }}>{note}</div>
    </div>
  )
}

/* ------------------------------------------------------------ 01 Wortmarke */

/** Zeile 1: gesetzte Wortmarke SKALDIR, darunter Monospace-Zeile mit Blockcursor. */
function Wortmarke({ P, dunkel, gross = 44 }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '100%', gap: 10 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
        <MiniWave P={P} />
        <motion.span initial={{ opacity: 0, letterSpacing: '0.6em' }} animate={{ opacity: 1, letterSpacing: '0.18em' }} transition={SPRING}
          style={{ fontFamily: SANS, fontSize: gross, fontWeight: 700, color: P.ink }}>SKALDIR</motion.span>
        <MiniWave P={P} />
      </div>
      <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ ...SPRING, delay: 0.25 }}
        style={{ fontFamily: MONO, fontSize: 12.5, color: P.sub, display: 'flex', alignItems: 'center', gap: 2 }}>
        <span style={{ color: P.faint }}>&gt;&nbsp;</span>
        <span>ein ziel, ein nachweis</span>
        <Cursor P={P} size={12} />
      </motion.div>
    </div>
  )
}

/* ------------------------------------------------------------ 02 CLI-Logo */

/** Zeile 2: Logo als Eingabeaufforderung — Klammerzeichen + Wortmarke. */
function CliLogo({ P }) {
  const zeilen = [
    [<span key="a" style={{ color: P.faint }}>$ skaldir --init</span>],
    [<span key="b" style={{ color: P.ink }}>[</span>,
      <span key="c" style={{ fontFamily: SANS, fontWeight: 700, letterSpacing: '0.14em', fontSize: 40, color: P.ink }}>SKALDIR</span>,
      <span key="d" style={{ color: P.ink }}>]</span>],
    [<span key="e" style={{ color: P.sub }}>beginn der messung... </span>, <Cursor key="f" P={P} size={13} />]
  ]
  return (
    <div style={{ display: 'flex', flexDirection: 'column', justifyContent: 'center', gap: 14, padding: '0 15%', fontFamily: MONO, fontSize: 13 }}>
      {/* statisches Deko-Array, feste Reihenfolge — Indexschluessel zulaessig */}
      {zeilen.map((z, i) => (
        <motion.div key={i} initial={{ opacity: 0, x: -10 }} animate={{ opacity: 1, x: 0 }} transition={{ ...SPRING, delay: i * 0.18 }} style={{ display: 'flex', alignItems: 'center', gap: 0, minHeight: 48 }}>
          {z}
        </motion.div>
      ))}
    </div>
  )
}

/* ------------------------------------------------------------ 03 Kästchen-Matrix */

/** Zeile 3: das Logo als Kästchen-Raster, gefüllt von der Mitte nach außen. */
function MatrixLogo({ P, dunkel }) {
  const N = 8                      // 8x8-Raster
  const zellen = []
  for (let y = 0; y < N; y++) for (let x = 0; x < N; x++) {
    const cx = x - (N - 1) / 2, cy = y - (N - 1) / 2
    zellen.push({ x, y, dist: Math.round(Math.sqrt(cx * cx + cy * cy) * 2) / 2 })
  }
  const sichtbar = (x, y) => {     // Buchstaben-SKALDIR grob im Raster angedeutet: Balken + Sockel
    const mitte = y >= 2 && y <= 5 && x >= 2 && x <= 5
    const boden = y === 6 && x >= 1 && x <= 6
    return mitte || boden
  }
  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '100%', gap: 12 }}>
      <div style={{ display: 'grid', gridTemplateColumns: `repeat(${N}, 15px)`, gridAutoRows: '15px', gap: 3 }}>
        {zellen.map(z => (
          <motion.span key={`${z.x}-${z.y}`}
            initial={{ opacity: 0, scale: 0.4 }}
            animate={{ opacity: sichtbar(z.x, z.y) ? (dunkel ? 0.95 : 1) : 0.35, scale: sichtbar(z.x, z.y) ? 1 : 0.75, backgroundColor: sichtbar(z.x, z.y) ? P.ink : P.hair }}
            transition={{ ...SPRING, delay: 0.08 + z.dist * 0.09 }}
            style={{ width: 15, height: 15 }} />
        ))}
      </div>
      <div style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>fuellt von der mitte nach aussen</div>
    </div>
  )
}

/* ------------------------------------------------------------ 04/05 Hintergründe */

/** Zeile 4: sehr feines Raster mit langsam wanderndem Lichtstreifen. */
function RasterLicht({ P, dunkel }) {
  return (
    <div style={{ position: 'absolute', inset: 0 }}>
      <div style={{ position: 'absolute', inset: 0, backgroundImage: `linear-gradient(${P.hair} 1px, transparent 1px), linear-gradient(90deg, ${P.hair} 1px, transparent 1px)`, backgroundSize: '28px 28px' }} />
      {/* Wanderndes Licht: Token-Tinte plus Deckkraft, weiche Kante ueber Unschaerfe */}
      <motion.div animate={{ left: ['-30%', '130%'] }} transition={{ duration: 9, repeat: Infinity, ease: 'linear' }}
        style={{ position: 'absolute', top: 0, bottom: 0, width: 140, background: P.ink,
          opacity: dunkel ? 0.10 : 0.07, filter: 'blur(20px)' }} />
      <div style={{ position: 'absolute', left: 16, bottom: 12, fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>hintergrund only · nichts verdeckt</div>
    </div>
  )
}

/** Zeile 5: fallende CLI-Zeilen, sehr blass, als Kulisse. */
function FallendeZeilen({ P }) {
  const zeilen = [
    '$ skaldir ziel setzen', '> messefortschritt --live', 'ok  nachweis: messreihe_01.json',
    '> pruefe anker ... 28/28', '$ skaldir beleg zeigen', 'ok  kriterium erfuellt',
    '> warte auf naechstes ziel', '$ git log --oneline | head -3'
  ]
  return (
    <div style={{ position: 'absolute', inset: 0 }}>
      {/* statische Kulisse, feste Reihenfolge — Indexschluessel zulaessig */}
      {zeilen.map((t, i) => (
        <motion.div key={i} animate={{ y: ['-12%', '112%'] }} transition={{ duration: 14 + (i % 4) * 4, repeat: Infinity, ease: 'linear', delay: i * 1.7 }}
          style={{ position: 'absolute', left: `${6 + (i % 3) * 30}%`, fontFamily: MONO, fontSize: 11.5, whiteSpace: 'nowrap',
            color: P.ink, opacity: 0.10 }}>
          {t}
        </motion.div>
      ))}
      <div style={{ position: 'absolute', left: 16, bottom: 12, fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>terminal-kulisse · sehr blass</div>
    </div>
  )
}

/* ------------------------------------------------------------ 06/07 Sprüche */

/** Zeile 6: leerer Chat — Punktraster Mitte, darunter der echte rotierende Spruch. */
function SpruchRotator({ P, sprueche = SPRUECHE, intervall = 4200, kompakt = false }) {
  const [i, setI] = useState(0)
  useEffect(() => {
    const id = setInterval(() => setI(v => (v + 1) % sprueche.length), intervall)
    return () => clearInterval(id)
  }, [sprueche.length, intervall])
  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '100%', gap: 18 }}>
      <Punktraster P={P} />
      <div style={{ height: kompakt ? 20 : 34, display: 'flex', alignItems: 'center' }}>
        <AnimatePresence mode="wait">
          <motion.div key={sprueche[i]} initial={{ opacity: 0, y: 8, filter: 'blur(2px)' }} animate={{ opacity: 1, y: 0, filter: 'blur(0px)' }}
            exit={{ opacity: 0, y: -8, filter: 'blur(2px)' }} transition={SPRING}
            style={{ fontFamily: SANS, fontSize: kompakt ? 13.5 : 17, fontWeight: 500, color: P.ink, textAlign: 'center' }}>
            {sprueche[i]}
          </motion.div>
        </AnimatePresence>
      </div>
      <div style={{ display: 'flex', gap: 5 }}>
        {/* variable Liste: der Spruch selbst ist der stabile Schluessel */}
        {sprueche.map((s, k) => (
          <motion.span key={s} animate={{ backgroundColor: k === i ? P.ink : P.hair, width: k === i ? 16 : 4 }}
            transition={SPRING} style={{ height: 4, borderRadius: RADIUS.pill }} />
        ))}
      </div>
    </div>
  )
}

/** Neun Punkte, unruhig pulsierend — reine Geometrie. */
const Punktraster = ({ P }) => (
  <span style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 5px)', gap: 3 }}>
    {/* statische Deko: neun Punkte, feste Reihenfolge — Indexschluessel zulaessig */}
    {Array.from({ length: 9 }).map((_, i) => (
      <motion.span key={i} animate={{ opacity: [0.4, 1, 0.4], scale: [1, 1.25, 1] }} transition={{ duration: 2.4, repeat: Infinity, ease: 'easeInOut', delay: i * 0.14 }}
        style={{ width: 5, height: 5, borderRadius: RADIUS.pill, background: P.faint }} />
    ))}
  </span>
)

/** Zeile 7: alle Sprüche als Liste, aktiver hervorgehoben, Klick wechselt. */
function SpruchListe({ P }) {
  const [i, setI] = useState(0)
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%' }}>
      <div style={{ width: '78%', maxWidth: 500, display: 'flex', flexDirection: 'column', gap: 4 }}>
        <div style={{ fontFamily: MONO, fontSize: TEXT.meta, letterSpacing: 1, color: P.faint, marginBottom: 4 }}>SPRÜCHE · KLICK WÄHLT</div>
        {SPRUECHE.map((s, k) => (
          <motion.div key={s} onClick={() => setI(k)}
            animate={{ backgroundColor: k === i ? P.surface : 'transparent', x: k === i ? 4 : 0 }}
            transition={SPRING}
            whileHover={{ x: 3 }}
            style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '7px 10px', cursor: 'pointer',
              border: `1px solid ${k === i ? P.line : P.hair}`, borderRadius: RADIUS.row }}>
            <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: k === i ? P.ink : P.faint }}>{String(k + 1).padStart(2, '0')}</span>
            <span style={{ fontFamily: SANS, fontSize: 13, fontWeight: k === i ? 600 : 400, color: k === i ? P.ink : P.sub }}>{s}</span>
            {k === i && <motion.span initial={{ scale: 0 }} animate={{ scale: 1 }} transition={SPRING}
              style={{ marginLeft: 'auto', fontFamily: MONO, fontSize: TEXT.meta, color: P.ink }}>aktiv</motion.span>}
          </motion.div>
        ))}
      </div>
    </div>
  )
}

/* ------------------------------------------------------------ 08/09/10 Varianten */

/** Zeile 8: riesige, sehr blasse Wortmarke hinter dem Chat, atmend. */
function AtemMarke({ P }) {
  return (
    <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', overflow: 'hidden' }}>
      <motion.span animate={{ opacity: [0.05, 0.09, 0.05] }} transition={{ duration: 5, repeat: Infinity, ease: 'easeInOut' }}
        style={{ fontFamily: SANS, fontSize: 130, fontWeight: 800, letterSpacing: '0.06em', color: P.ink, whiteSpace: 'nowrap' }}>
        SKALDIR
      </motion.span>
    </div>
  )
}

/** Zeile 9: äußerst kompakt — Wortmarke und Spruch in einer Zeile. */
function KompaktZeile({ P }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', gap: 12 }}>
      <motion.span initial={{ opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }} transition={SPRING}
        style={{ fontFamily: SANS, fontSize: 15, fontWeight: 700, letterSpacing: '0.16em', color: P.ink }}>SKALDIR</motion.span>
      <span style={{ width: 1, height: 16, background: P.line }} />
      <motion.span initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ ...SPRING, delay: 0.2 }}
        style={{ fontFamily: SANS, fontSize: 12.5, color: P.sub }}>Ein Ziel, ein Nachweis.</motion.span>
      <Cursor P={P} size={12} />
    </div>
  )
}

/** Zeile 6/10: chat-los, nur Spruch im Vollbild — die dunkle Favoritenfassung bekommt zusätzlich oben die Wortmarke. */
function FavoritVollbild({ P }) {
  return (
    <div style={{ position: 'absolute', inset: 0 }}>
      <motion.div initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }} transition={{ ...SPRING, delay: 0.15 }}
        style={{ position: 'absolute', top: 26, left: 0, right: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 12 }}>
        <IconTerminal size={15} stroke={1.8} color={P.sub} />
        <span style={{ fontFamily: SANS, fontSize: 21, fontWeight: 700, letterSpacing: '0.2em', color: P.ink }}>SKALDIR</span>
      </motion.div>
      <SpruchRotator P={P} intervall={4500} />
      <div style={{ position: 'absolute', bottom: 14, left: 0, right: 0, display: 'flex', justifyContent: 'center', gap: 8, fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>
        <span>$</span><span>bereit</span><Cursor P={P} size={10} />
      </div>
    </div>
  )
}

/* ------------------------------------------------------------ Export */

export default {
  '01 · Wortmarke + Monospace-Zeile mit Blockcursor': () => (
    <Buehne titel="01 · Wortmarke mit Blockcursor" note="gesetzte Wortmarke, darunter CLI-Zeile mit blinkendem Cursor, Wellen nur dezent"
      dunkel={false} umschaltbar>{({ P, dunkel }) => <Wortmarke P={P} dunkel={dunkel} />}</Buehne>
  ),
  '02 · CLI-Logo: Prompt mit Klammer-Wortmarke': () => (
    <Buehne titel="02 · CLI-Logo als Eingabeaufforderung" note="Klammerzeichen + Wortmarke wie ein Terminal-Prompt aufgebaut"
      dunkel={false} umschaltbar>{({ P }) => <CliLogo P={P} />}</Buehne>
  ),
  '03 · Logo als Kästchen-Matrix, füllt von der Mitte': () => (
    <Buehne titel="03 · Logo als Kästchen-Matrix" note="kleine Kästchen füllen sich beim Erscheinen von der Mitte nach außen"
      dunkel={false} umschaltbar>{({ P, dunkel }) => <MatrixLogo P={P} dunkel={dunkel} />}</Buehne>
  ),
  '04 · Hintergrund: feines Raster + wandernder Lichtstreifen': () => (
    <Buehne titel="04 · Hintergrund: feines Raster mit Lichtstreifen" note="nur Hintergrundkulisse, es verdeckt nichts"
      dunkel={false} umschaltbar>{({ P, dunkel }) => <RasterLicht P={P} dunkel={dunkel} />}</Buehne>
  ),
  '05 · Hintergrund: fallende CLI-Zeilen, sehr blass': () => (
    <Buehne titel="05 · Hintergrund: fallende Terminal-Zeilen" note="blass laufende CLI-Zeilen als Kulisse"
      dunkel={false} umschaltbar>{({ P }) => <FallendeZeilen P={P} />}</Buehne>
  ),
  '06 · Leerer Chat: Punktraster + rotierender Spruch': () => (
    <Buehne titel="06 · Leerer Chat mit wechselndem Spruch" note="echte Rotation alle 4,2 s, Ein-/Ausblenden über Motion"
      dunkel={false} umschaltbar>{({ P }) => <SpruchRotator P={P} />}</Buehne>
  ),
  '07 · Spruchsammlung: Liste, Klick wechselt': () => (
    <Buehne titel="07 · Spruchsammlung als Liste" note="sechs Sprüche, aktiver hervorgehoben, Klick wechselt wirklich"
      dunkel={false} umschaltbar>{({ P }) => <SpruchListe P={P} />}</Buehne>
  ),
  '08 · Hintergrund: riesige blasse Wortmarke, atmend': () => (
    <Buehne titel="08 · Riesige blasse Wortmarke hinter dem Chat" note="atmet langsam, soll nicht ablenken"
      dunkel={false} umschaltbar>{({ P }) => <><AtemMarke P={P} /><KompaktZeile P={P} /></>}</Buehne>
  ),
  '09 · Sehr kompakt: Wortmarke + Spruch in einer Zeile': () => (
    <Buehne titel="09 · Sehr kompakt in einer Zeile" note="nur Wortmarke und ein Spruch, dazu Cursor"
      dunkel={false} umschaltbar hoch={160}>{({ P }) => <KompaktZeile P={P} />}</Buehne>
  ),
  '10 · Favorit 06 als dunkle Vollbild-Fassung': () => (
    <Buehne titel="10 · Favorit (06) — dunkle Vollbild-Fassung" note="Startschirm dunkel: Wortmarke oben, Spruch rotiert, alle unten"
      dunkel umschaltbar={false} hoch={430}>{({ P }) => <FavoritVollbild P={P} />}</Buehne>
  )
}
