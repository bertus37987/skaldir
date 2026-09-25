/**
 * Artefakt-Vorschau — die eigene, ausdruecklich geoeffnete Flaeche fuer Artefakte und Diffs.
 *
 * Die Designvorgabe erlaubt eine geoeffnete Vorschau; im UI fehlt sie als eigene Flaeche.
 * Diese Werkbank zeigt 10 Fassungen: offene Flaeche, Bild, Diff, Medien, Dokument,
 * Bottom-Sheet, rechte Spalte mit Griff, eigener Reiter, Kompaktzeile, dunkle Vollbild-Fassung.
 *
 * Nur Schwarz/Weiss/Grau · Symbole aus @tabler/icons-react · Bewegung ueber Motion.
 */
import { motion, AnimatePresence, animate } from 'motion/react'
import React, { useState, useRef } from 'react'
import { IconArrowBackUp, IconCheck, IconChevronDown, IconChevronLeft, IconChevronRight, IconColumns2, IconCopy, IconDownload, IconExternalLink, IconEye, IconFileCode, IconFileSpreadsheet, IconFileText, IconFileTypePng, IconFolderOpen, IconGridDots, IconLayoutBottombar, IconLayoutSidebarRight, IconMaximize, IconMessage, IconMovie, IconMusic, IconPlayerPause, IconPlayerPlay, IconPlayerSkipBack, IconPlayerSkipForward, IconSearch, IconVolume, IconX, IconZoomIn, IconZoomOut } from '@tabler/icons-react'

/* Farben, Schrift, Federn, Radien und Schriftgroessen: ./_tokens (eine Wahrheit).
   Befunde 18.09.2026: kein eigener Hexwert, Schriftuntergrenze 11 px (TEXT.meta),
   Radien aus RADIUS, useBarrierefrei() gegen reduzierte Bewegung und fuer die Tastatur. */
import { LIGHT, DARK, MONO, SANS, SPRING, RADIUS, TEXT } from './_tokens'
import { useBarrierefrei } from './_barrierefrei'

/* ---------------------------------------------------------------- Buehne */

function StageHead({ name, note, dark, setDark, P }) {
  return (
    <div style={{ marginBottom: 10 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <span style={{ fontFamily: MONO, fontSize: 12, fontWeight: 600 }}>{name}</span>
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
      </div>
      <div style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, marginTop: 3 }}>{note}</div>
    </div>
  )
}

function Stage({ name, note, dark, setDark, children }) {
  useBarrierefrei()
  const P = dark ? DARK : LIGHT
  return (
    <div data-stage style={{ padding: 20, background: P.bg, fontFamily: SANS, color: P.ink, width: '100%', maxWidth: 880, boxSizing: 'border-box' }}>
      <StageHead name={name} note={note} dark={dark} setDark={setDark} P={P} />
      {children(P)}
    </div>
  )
}

function Managed({ name, note, render, dark0 = false }) {
  const [dark, setDark] = useState(dark0)
  return (
    <Stage name={name} note={note} dark={dark} setDark={setDark}>
      {(P) => render({ P, dark })}
    </Stage>
  )
}

/* ---------------------------------------------------------------- Bausteine */

const Frame = ({ children, P, style }) => (
  <div data-frame style={{ border: `1px solid ${P.line}`, background: P.surface, ...style }}>{children}</div>
)

const Chip = ({ children, P, filled = false }) => (
  <span style={{ display: 'inline-flex', alignItems: 'center', borderRadius: RADIUS.pill, padding: '2px 7px',
    fontFamily: MONO, fontSize: TEXT.meta, fontWeight: 600, letterSpacing: 0.4, textTransform: 'uppercase',
    backgroundColor: filled ? P.ink : P.hair, color: filled ? P.bg : P.sub }}>{children}</span>
)

const IconBtn = ({ children, P, label, onClick }) => (
  <motion.span title={label} aria-label={label} onClick={onClick} whileTap={{ scale: 0.92 }} whileHover={{ scale: 1.06 }} transition={SPRING}
    style={{ width: 20, height: 20, borderRadius: RADIUS.micro, border: `1px solid ${P.line}`, display: 'inline-flex', alignItems: 'center',
      justifyContent: 'center', cursor: 'pointer', color: P.sub, background: P.surface }}>
    {children}
  </motion.span>
)

/* Textknopf — gefuellt = Hauptaktion. */
const Btn = ({ children, P, onClick, title, filled = false, lead }) => (
  <motion.span title={title} onClick={onClick} whileTap={{ scale: 0.95 }} whileHover={{ scale: 1.02 }} transition={SPRING}
    style={{ display: 'inline-flex', alignItems: 'center', gap: 5, padding: '4px 9px', border: `1px solid ${filled ? P.ink : P.line}`,
      background: filled ? P.ink : P.surface, color: filled ? P.bg : P.sub, fontFamily: MONO, fontSize: TEXT.meta, cursor: 'pointer', whiteSpace: 'nowrap' }}>
    {lead}{children}
  </motion.span>
)

const Path = ({ children, P }) => (
  <span style={{ fontFamily: MONO, fontSize: 11, color: P.ink, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{children}</span>
)

const Hint = ({ children, P }) => (
  <div style={{ marginTop: 8, fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>{children}</div>
)

/* ---------------------------------------------------------------- Daten */

const FILE_LINES = [
  '# Routenplan — Sommer',
  '',
  '## Abschnitt 1 — Vorbereitung',
  'Start: Freiburg · Ziel: Lissabon',
  'Etappen: 5 · Distanz: 2 310 km',
  '',
  '## Abschnitt 2 — Hinweise',
  'Grenzuebertritt Basel: Ausweis bereithalten.',
  'Zwischenstopp Genf: 2 Naechte.',
  'Ankunft Lissabon: Tag 5, abends.'
]

const DIFF_ROWS = [
  { k: 'ctx', l: '## Routenplan', r: '## Routenplan' },
  { k: 'chg', l: 'Start: Muenchen', r: 'Start: Freiburg' },
  { k: 'add', l: '', r: 'Via: Basel — Genf' },
  { k: 'ctx', l: 'Ziel: Lissabon', r: 'Ziel: Lissabon' },
  { k: 'chg', l: 'Eta: 3 Tage', r: 'Eta: 5 Tage' },
  { k: 'add', l: '', r: 'Uebernachtungen: Genf, Lyon, Porto' },
  { k: 'ctx', l: 'Ende der Route', r: 'Ende der Route' }
]

const DOC_PAGES = [
  [
    { mark: '1. Zusammenfassung', body: 'Die Lieferung umfasst vier Bauteile und einen Bericht.' },
    { mark: '', body: 'Alle Betraege sind in Euro angegeben, ohne Umsatzsteuer.' },
    { mark: '', body: 'Abweichungen zum Vorquartal sind in Abschnitt 3 vermerkt.' },
    { mark: '2. Grundlage', body: 'Grundlage ist die Messung vom 14. des Monats.' }
  ],
  [
    { mark: '2.1 Messung', body: 'Gemessen wurden 48 Laeufe bei gleicher Last.' },
    { mark: '', body: 'Der Median liegt bei 42 ms, der schlechteste Lauf bei 91 ms.' },
    { mark: '3. Abweichungen', body: 'Drei Laeufe ueberschritten die Grenze von 80 ms.' },
    { mark: '', body: 'Ursache war ein doppelter Indizierungslauf.' }
  ],
  [
    { mark: '4. Empfehlung', body: 'Den Indizierungslauf auf einen Durchgang begrenzen.' },
    { mark: '', body: 'Danach erneut messen und den Bericht fortschreiben.' },
    { mark: '', body: 'Offene Frage: Wer prueft die Grenze von 80 ms?' }
  ]
]

/* ============================================================ 01 · offene Flaeche */

export function V01() {
  const [pinned, setPinned] = useState(false)
  return (
    <Managed name="01 · Datei-Vorschau als geoeffnete Flaeche" note="Kopf: Pfad + Endung + Groesse · Inhalt nummeriert · „im Ordner zeigen"
      render={({ P }) => (
        <Frame P={P}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 12px', borderBottom: `1px solid ${P.hair}` }}>
            <IconFileText size={13} stroke={1.8} color={P.sub} />
            <Path P={P}>berichte/2026-routenplan.md</Path>
            <Chip P={P}>MD</Chip>
            <Chip P={P}>18 KB</Chip>
            {pinned && <Chip P={P} filled>angeheftet</Chip>}
            <span style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 6 }}>
              <IconBtn P={P} label="Kopieren"><IconCopy size={11} stroke={1.8} /></IconBtn>
              <IconBtn P={P} label="Herunterladen"><IconDownload size={11} stroke={1.8} /></IconBtn>
              <IconBtn P={P} label="In neuem Reiter"><IconExternalLink size={11} stroke={1.8} /></IconBtn>
              <Btn P={P} onClick={() => setPinned(v => !v)} lead={<IconFolderOpen size={11} stroke={1.8} />}>im Ordner zeigen</Btn>
            </span>
          </div>
          <div style={{ padding: '10px 0', maxHeight: 260, overflow: 'auto' }}>
            {FILE_LINES.map((t, i) => (
              <motion.div key={`${i}-${t || 'leer'}`} data-fileline initial={{ opacity: 0, x: -4 }} animate={{ opacity: 1, x: 0 }}
                transition={{ ...SPRING, delay: 0.04 + i * 0.035 }}
                style={{ display: 'flex', gap: 10, padding: '0 12px' }}>
                <span style={{ width: 20, flexShrink: 0, textAlign: 'right', fontFamily: MONO, fontSize: TEXT.meta, color: P.faint, fontVariantNumeric: 'tabular-nums' }}>{i + 1}</span>
                <span style={{ fontFamily: MONO, fontSize: 11.2, lineHeight: 1.7, color: t.startsWith('#') ? P.ink : P.sub, fontWeight: t.startsWith('#') ? 600 : 400 }}>{t || ' '}</span>
              </motion.div>
            ))}
          </div>
          <Hint P={P}>Vorschau ist kein Laufzustand — sie bleibt offen, bis sie geschlossen wird.</Hint>
        </Frame>
      )} />
  )
}

/* ============================================================ 02 · Bild */

function Bildflaeche({ P, zoom, grid }) {
  return (
    <div style={{ position: 'relative', height: 240, overflow: 'hidden', background: P.hair, display: 'grid', placeItems: 'center' }}>
      <motion.svg width="320" height="180" viewBox="0 0 320 180" animate={{ scale: zoom / 100 }} transition={SPRING}
        style={{ display: 'block', opacity: 0.85 }}>
        <rect x="8" y="8" width="304" height="164" fill="none" stroke={P.faint} strokeWidth="1" />
        <circle cx="90" cy="70" r="34" fill="none" stroke={P.ink} strokeWidth="1.6" />
        <rect x="150" y="40" width="120" height="60" fill="none" stroke={P.sub} strokeWidth="1.6" />
        <path d="M20 150 L110 96 L190 140 L300 62" fill="none" stroke={P.ink} strokeWidth="1.6" />
        <path d="M20 160 L300 160" stroke={P.line} strokeWidth="1" />
        <path d="M40 30 L40 168 M300 30 L300 168" stroke={P.line} strokeWidth="1" strokeDasharray="3 4" />
      </motion.svg>
      <AnimatePresence>
        {grid && (
          <motion.div data-grid initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.18 }}
            style={{ position: 'absolute', inset: 0, pointerEvents: 'none',
              backgroundImage: `linear-gradient(${P.line} 1px, transparent 1px), linear-gradient(90deg, ${P.line} 1px, transparent 1px)`,
              backgroundSize: '24px 24px' }} />
        )}
      </AnimatePresence>
      <span style={{ position: 'absolute', right: 8, bottom: 8, padding: '2px 6px', background: P.surface, border: `1px solid ${P.line}`,
        fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, fontVariantNumeric: 'tabular-nums' }}>{zoom} %</span>
    </div>
  )
}

export function V02() {
  const [zoom, setZoom] = useState(100)
  const [grid, setGrid] = useState(true)
  const set = (d) => setZoom(z => Math.min(400, Math.max(25, z + d)))
  return (
    <Managed name="02 · Bild-Vorschau" note="geometrische Platzhalterflaeche · Zoom aendert den Wert wirklich · Raster ein/aus"
      render={({ P }) => (
        <Frame P={P}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 12px', borderBottom: `1px solid ${P.hair}` }}>
            <IconFileTypePng size={13} stroke={1.8} color={P.sub} />
            <Path P={P}>medien/route-01.png</Path>
            <Chip P={P}>PNG</Chip>
            <Chip P={P}>1920 x 1080</Chip>
            <span style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 6 }}>
              <IconBtn P={P} label="Verkleinern" onClick={() => set(-25)}><IconZoomOut size={11} stroke={1.8} /></IconBtn>
              <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, width: 44, textAlign: 'center', fontVariantNumeric: 'tabular-nums' }}>{zoom} %</span>
              <IconBtn P={P} label="Vergroessern" onClick={() => set(25)}><IconZoomIn size={11} stroke={1.8} /></IconBtn>
              <span style={{ width: 1, height: 14, background: P.hair }} />
              <motion.span data-gridtoggle onClick={() => setGrid(g => !g)} whileTap={{ scale: 0.94 }} transition={SPRING}
                animate={{ borderColor: grid ? P.ink : P.line, backgroundColor: grid ? P.ink : P.surface, color: grid ? P.bg : P.sub }}
                style={{ display: 'inline-flex', alignItems: 'center', gap: 5, padding: '4px 9px', border: `1px solid ${P.line}`, fontFamily: MONO, fontSize: TEXT.meta, cursor: 'pointer' }}>
                <IconGridDots size={11} stroke={1.8} />Raster
              </motion.span>
              <Btn P={P} lead={<IconMaximize size={11} stroke={1.8} />}>Vollbild</Btn>
            </span>
          </div>
          <Bildflaeche P={P} zoom={zoom} grid={grid} />
          <Hint P={P}>Der Wert rechts unten folgt dem echten Zoom-Zustand, nicht nur dem Knopfdruck.</Hint>
        </Frame>
      )} />
  )
}

/* ============================================================ 03 · Diff */

export function V03() {
  const [applied, setApplied] = useState(false)
  const [dropped, setDropped] = useState(false)
  const added = DIFF_ROWS.filter(r => r.r && r.r !== r.l).length
  const removed = DIFF_ROWS.filter(r => r.l && r.l !== r.r).length
  const done = applied || dropped
  return (
    <Managed name="03 · Diff-Ansicht" note="zwei Spalten mit +/− Zeilen · Zahl der Aenderungen · uebernehmen schliesst wirklich"
      render={({ P }) => (
        <Frame P={P} style={{ overflow: 'hidden' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 12px', borderBottom: `1px solid ${P.hair}` }}>
            <IconColumns2 size={13} stroke={1.8} color={P.sub} />
            <Path P={P}>berichte/2026-routenplan.md</Path>
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontFamily: MONO, fontSize: 11, fontVariantNumeric: 'tabular-nums', color: P.ink }}>
              <motion.span key={`a${added}`} initial={{ opacity: 0, y: -5 }} animate={{ opacity: 1, y: 0 }} transition={SPRING}>+{added}</motion.span>
              <motion.span key={`r${removed}`} initial={{ opacity: 0, y: 5 }} animate={{ opacity: 1, y: 0 }} transition={SPRING}>−{removed}</motion.span>
            </span>
            <Chip P={P}>{added + removed} Aenderungen</Chip>
            <span style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 6 }}>
              {done ? (
                <Btn P={P} onClick={() => { setApplied(false); setDropped(false) }} lead={<IconArrowBackUp size={11} stroke={1.8} />}>zuruecksetzen</Btn>
              ) : (
                <React.Fragment>
                  <Btn P={P} onClick={() => setDropped(true)}>verwerfen</Btn>
                  <Btn P={P} filled onClick={() => setApplied(true)} lead={<IconCheck size={11} stroke={2} />}>uebernehmen</Btn>
                </React.Fragment>
              )}
            </span>
          </div>
          <AnimatePresence mode="wait" initial={false}>
            {done ? (
              <motion.div key={applied ? 'applied' : 'dropped'} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -6 }}
                transition={SPRING} style={{ padding: '22px 16px', textAlign: 'center' }}>
                <span style={{ fontFamily: MONO, fontSize: 11.5, color: P.ink }}>
                  {applied ? 'uebernommen · Datei neu geschrieben · 18 KB' : 'verworfen · Datei unveraendert'}
                </span>
                <div style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint, marginTop: 5 }}>
                  {applied ? 'Die Vorschau ist damit geschlossen — die Flaeche gibt den Platz frei.' : 'Die Vorschau bleibt geschlossen, bis ein neuer Vergleich ansteht.'}
                </div>
              </motion.div>
            ) : (
              <motion.div key="diff" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.18 }}
                style={{ display: 'grid', gridTemplateColumns: '1fr 1fr' }}>
                {['vorher', 'nachher'].map((side, si) => (
                  <div key={side} style={{ borderLeft: si === 1 ? `1px solid ${P.line}` : 'none' }}>
                    <div style={{ padding: '5px 10px', borderBottom: `1px solid ${P.hair}`, fontFamily: MONO, fontSize: TEXT.meta, letterSpacing: 1, textTransform: 'uppercase', color: P.faint }}>{side}</div>
                    <div style={{ padding: '6px 0', maxHeight: 220, overflow: 'auto' }}>
                      {DIFF_ROWS.map((r, i) => {
                        const text = si === 0 ? r.l : r.r
                        const changed = text && text !== (si === 0 ? r.r : r.l)
                        return (
                          <motion.div key={`${r.k}-${r.l}-${r.r}`} data-diffl={changed ? (si === 0 ? 'remove' : 'add') : 'context'}
                            initial={{ opacity: 0, x: si === 0 ? -5 : 5 }} animate={{ opacity: 1, x: 0 }}
                            transition={{ ...SPRING, delay: 0.03 + i * 0.035 }}
                            style={{ display: 'flex', gap: 6, padding: '0 10px', minHeight: 18, alignItems: 'center',
                              borderLeft: changed ? `2px solid ${si === 0 ? P.faint : P.ink}` : '2px solid transparent',
                              backgroundColor: changed ? P.hair : 'transparent' }}>
                            <span style={{ width: 9, fontFamily: MONO, fontSize: TEXT.meta, color: changed ? P.ink : 'transparent' }}>{changed ? (si === 0 ? '−' : '+') : ' '}</span>
                            <span style={{ fontFamily: MONO, fontSize: 11, lineHeight: 1.6, color: P.sub, textDecoration: si === 0 && changed ? 'line-through' : 'none',
                              whiteSpace: 'pre', overflow: 'hidden', textOverflow: 'ellipsis' }}>{text || ' '}</span>
                          </motion.div>
                        )
                      })}
                    </div>
                  </div>
                ))}
              </motion.div>
            )}
          </AnimatePresence>
          {!done && <Hint P={P}>Tint + 2px Rand-Akzent statt +/- in der Rinne; die Zahl der Aenderungen ist berechnet, nicht gesetzt.</Hint>}
        </Frame>
      )} />
  )
}

/* ============================================================ 04 · Medien */

const MEDIA = {
  video: { name: 'clip-route.mp4', kind: 'VIDEO', size: '42 MB', dauer: '02:14', rate: '24 fps', bild: '1920 x 1080' },
  audio: { name: 'notiz-14-32.mp3', kind: 'AUDIO', size: '3.1 MB', dauer: '07:48', rate: '48 kHz', bild: 'Stereo' }
}

export function V04() {
  const [kind, setKind] = useState('video')
  const [playing, setPlaying] = useState(true)
  const m = MEDIA[kind]
  return (
    <Managed name="04 · Medien-Vorschau (Video/Audio)" note="Abspielleiste · Dauer · Bildrate · Umschalten Video/Audio"
      render={({ P }) => (
        <Frame P={P} style={{ overflow: 'hidden' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 12px', borderBottom: `1px solid ${P.hair}` }}>
            {kind === 'video' ? <IconMovie size={13} stroke={1.8} color={P.sub} /> : <IconMusic size={13} stroke={1.8} color={P.sub} />}
            <Path P={P}>{m.name}</Path>
            <span style={{ display: 'inline-flex', border: `1px solid ${P.line}`, padding: 2 }}>
              {['video', 'audio'].map(k => (
                <motion.span key={k} data-mediatab={k} onClick={() => setKind(k)} whileTap={{ scale: 0.94 }} transition={SPRING}
                  animate={{ backgroundColor: kind === k ? P.ink : 'transparent', color: kind === k ? P.bg : P.sub }}
                  style={{ padding: '2px 8px', fontFamily: MONO, fontSize: TEXT.meta, fontWeight: 700, cursor: 'pointer', textTransform: 'uppercase' }}>{k}</motion.span>
              ))}
            </span>
            <span style={{ marginLeft: 'auto', display: 'inline-flex', gap: 6 }}>
              <Chip P={P}>{m.dauer}</Chip>
              <Chip P={P}>{m.rate}</Chip>
            </span>
          </div>
          <div style={{ position: 'relative', height: 210, background: kind === 'video' ? P.hair : 'transparent', display: 'grid', placeItems: 'center', overflow: 'hidden' }}>
            {kind === 'video' ? (
              <motion.svg width="300" height="170" viewBox="0 0 300 170" animate={{ scale: playing ? [1, 1.015, 1] : 1 }}
                transition={{ duration: 7, repeat: Infinity, ease: 'easeInOut' }} style={{ opacity: 0.85 }}>
                <rect x="6" y="6" width="288" height="158" fill="none" stroke={P.faint} strokeWidth="1" />
                <path d="M6 128 L86 84 L156 122 L294 58" fill="none" stroke={P.ink} strokeWidth="1.6" />
                <circle cx="86" cy="84" r="5" fill={P.ink} />
                <circle cx="156" cy="122" r="5" fill={P.ink} />
                <rect x="18" y="18" width="58" height="14" fill="none" stroke={P.sub} strokeWidth="1" />
                <text x="24" y="29" fontFamily={MONO} fontSize={TEXT.meta} fill={P.sub}>KAM 1</text>
              </motion.svg>
            ) : (
              <div style={{ display: 'flex', alignItems: 'flex-end', gap: 3, height: 130 }}>
                {Array.from({ length: 46 }).map((_, i) => (
                  <motion.span key={i /* rein statische Deko: 46 feste Balken, nie umsortiert */} animate={{ height: playing ? [8 + (i % 7) * 6, 14 + ((i * 5) % 9) * 8, 8 + (i % 7) * 6] : 12 + (i % 5) * 6 }}
                    transition={{ duration: 1.4, repeat: playing ? Infinity : 0, ease: 'easeInOut', delay: i * 0.02 }}
                    style={{ width: 4, background: i % 3 === 0 ? P.ink : P.faint, opacity: playing ? 0.9 : 0.45 }} />
                ))}
              </div>
            )}
          </div>
          <div style={{ padding: '8px 12px', borderTop: `1px solid ${P.hair}`, display: 'flex', alignItems: 'center', gap: 10 }}>
            <motion.span onClick={() => setPlaying(v => !v)} whileTap={{ scale: 0.9 }} transition={SPRING} title={playing ? 'Pause' : 'Abspielen'}
              style={{ width: 24, height: 24, borderRadius: RADIUS.micro, border: `1px solid ${P.line}`, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer', color: P.ink }}>
              {playing ? <IconPlayerPause size={12} stroke={2} /> : <IconPlayerPlay size={12} stroke={2} />}
            </motion.span>
            <motion.span whileTap={{ scale: 0.9 }} transition={SPRING} style={{ color: P.faint, cursor: 'pointer', display: 'inline-flex' }}><IconPlayerSkipBack size={12} stroke={2} /></motion.span>
            <motion.span whileTap={{ scale: 0.9 }} transition={SPRING} style={{ color: P.faint, cursor: 'pointer', display: 'inline-flex' }}><IconPlayerSkipForward size={12} stroke={2} /></motion.span>
            <div style={{ flex: 1, height: 4, background: P.hair, position: 'relative', overflow: 'hidden' }}>
              <motion.div data-progress
                animate={playing ? { width: ['18%', '100%'] } : { width: '38%' }}
                transition={playing ? { duration: 11, repeat: Infinity, ease: 'linear' } : { duration: 0.25 }}
                style={{ position: 'absolute', left: 0, top: 0, bottom: 0, background: P.ink }} />
            </div>
            <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, fontVariantNumeric: 'tabular-nums' }}>00:51 / {m.dauer}</span>
            <IconVolume size={12} stroke={1.9} color={P.faint} />
          </div>
          <div style={{ padding: '0 12px 10px', fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>
            {m.kind} · {m.size} · {m.bild} · {m.rate} — Bildrate und Dauer stehen im Kopf, nicht im Abspielbalken.
          </div>
        </Frame>
      )} />
  )
}

/* ============================================================ 05 · Dokument */

function markiere(text, q, P) {
  if (!q) return text
  const parts = text.split(new RegExp(`(${q.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'ig'))
  return parts.map((p, i) => p.toLowerCase() === q.toLowerCase()
    ? <mark key={`${i}-${p}`} style={{ background: P.hair, color: P.ink, borderBottom: `1px solid ${P.ink}`, padding: '0 1px' }}>{p}</mark>
    : <React.Fragment key={`${i}-${p}`}>{p}</React.Fragment>)
}

export function V05() {
  const [page, setPage] = useState(0)
  const [q, setQ] = useState('')
  const [palette, setPalette] = useState(false)
  const lines = DOC_PAGES[page]
  const treffer = DOC_PAGES.flat().filter(l => q && (l.body.toLowerCase().includes(q.toLowerCase()) || l.mark.toLowerCase().includes(q.toLowerCase()))).length
  return (
    <Managed name="05 · Dokument-Vorschau" note="Seitenzahl · Abschnittsmarken · Suchfeld im Text mit Trefferzahl"
      render={({ P }) => (
        <Frame P={P} style={{ overflow: 'hidden' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 12px', borderBottom: `1px solid ${P.hair}` }}>
            <IconFileText size={13} stroke={1.8} color={P.sub} />
            <Path P={P}>berichte/monatsbericht.pdf</Path>
            <Chip P={P}>PDF</Chip>
            <span style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 6 }}>
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, border: `1px solid ${P.line}`, padding: '3px 6px', background: P.surface }}>
                <IconSearch size={11} stroke={1.9} color={P.faint} />
                <input data-docsearch value={q} onChange={e => setQ(e.target.value)} placeholder="im Text suchen"
                  style={{ width: 118, border: 'none', outline: 'none', background: 'transparent', fontFamily: MONO, fontSize: TEXT.meta, color: P.ink }} />
                <AnimatePresence>
                  {q && (
                    <motion.span initial={{ opacity: 0, scale: 0.9 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0 }} transition={SPRING}
                      style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, fontVariantNumeric: 'tabular-nums' }}>{treffer}</motion.span>
                  )}
                </AnimatePresence>
              </span>
              <IconBtn P={P} label="Abschnittsmarken" onClick={() => setPalette(v => !v)}><IconLayoutSidebarRight size={11} stroke={1.8} /></IconBtn>
            </span>
          </div>
          <div style={{ display: 'flex', minHeight: 230 }}>
            <div style={{ flex: 1, minWidth: 0, padding: '14px 18px', maxHeight: 260, overflow: 'auto' }}>
              <div style={{ fontFamily: MONO, fontSize: TEXT.meta, letterSpacing: 1.2, textTransform: 'uppercase', color: P.faint, marginBottom: 10 }}>Seite {page + 1} von {DOC_PAGES.length}</div>
              {lines.map((l, i) => (
                <motion.div key={`${l.mark}-${l.body}`} initial={{ opacity: 0, y: 5 }} animate={{ opacity: 1, y: 0 }} transition={{ ...SPRING, delay: 0.03 + i * 0.05 }} style={{ marginBottom: 10 }}>
                  {l.mark && <div data-docmark style={{ fontFamily: SANS, fontSize: 12.5, fontWeight: 600, color: P.ink, marginBottom: 3 }}>{markiere(l.mark, q, P)}</div>}
                  <div style={{ fontFamily: SANS, fontSize: 12, lineHeight: 1.7, color: P.sub }}>{markiere(l.body, q, P)}</div>
                </motion.div>
              ))}
            </div>
            <AnimatePresence initial={false}>
              {palette && (
                <motion.aside key="marks" data-marks initial={{ width: 0, opacity: 0 }} animate={{ width: 176, opacity: 1 }} exit={{ width: 0, opacity: 0 }} transition={SPRING}
                  style={{ overflow: 'hidden', borderLeft: `1px solid ${P.line}`, background: P.bg }}>
                  <div style={{ width: 176, padding: 12 }}>
                    <div style={{ fontFamily: MONO, fontSize: TEXT.meta, letterSpacing: 1.2, textTransform: 'uppercase', color: P.faint, marginBottom: 8 }}>Abschnitte</div>
                    {DOC_PAGES.flatMap((pg, pi) => pg.filter(l => l.mark).map(l => ({ ...l, pi }))).map((l, i) => (
                      <motion.div key={`${l.pi}-${l.mark}`} data-markrow initial={{ opacity: 0, x: 6 }} animate={{ opacity: 1, x: 0 }} transition={{ ...SPRING, delay: i * 0.04 }}
                        onClick={() => setPage(l.pi)}
                        style={{ fontFamily: SANS, fontSize: 11.5, color: page === l.pi ? P.ink : P.sub, padding: '4px 0', cursor: 'pointer',
                          borderBottom: `1px solid ${P.hair}` }}>{l.mark}</motion.div>
                    ))}
                  </div>
                </motion.aside>
              )}
            </AnimatePresence>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 12px', borderTop: `1px solid ${P.hair}` }}>
            <IconBtn P={P} label="Vorherige Seite" onClick={() => setPage(p => Math.max(0, p - 1))}><IconChevronLeft size={12} stroke={2.2} /></IconBtn>
            <IconBtn P={P} label="Naechste Seite" onClick={() => setPage(p => Math.min(DOC_PAGES.length - 1, p + 1))}><IconChevronRight size={12} stroke={2.2} /></IconBtn>
            <span style={{ display: 'flex', gap: 4 }}>
              {DOC_PAGES.map((_, i) => (
                <motion.span key={i /* rein statische Anzeige: feste Seitenzahl, nie umsortiert */} animate={{ opacity: i === page ? 1 : 0.4 }} transition={SPRING}
                  style={{ width: 14, height: 3, background: P.ink, display: 'inline-block' }} />
              ))}
            </span>
            <span style={{ marginLeft: 'auto', fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>{treffer > 0 ? `${treffer} Treffer im ganzen Dokument` : 'Suche markiert im Text, nicht in den Marken'}</span>
          </div>
        </Frame>
      )} />
  )
}

/* ============================================================ 06 · Bottom-Sheet */

const CHAT = [
  { von: 'du', t: 'zeig mir den neuen Routenplan' },
  { von: 'agent', t: 'berichte/2026-routenplan.md · 18 KB · ich oeffne die Vorschau unten.' },
  { von: 'du', t: 'zieht sich das Blatt auf?' }
]

export function V06() {
  const [open, setOpen] = useState(true)
  const [gross, setGross] = useState(false)
  return (
    <Managed name="06 · Vorschau als unteres Blatt ueber dem Chat" note="Bottom-Sheet: ziehen am Griff vergroessert, Griff tippen klappt auf/zu"
      render={({ P }) => (
        <Frame P={P} style={{ height: 396, position: 'relative', overflow: 'hidden', background: P.bg }}>
          <div style={{ position: 'absolute', inset: 0, padding: '14px 16px', display: 'flex', flexDirection: 'column', gap: 10 }}>
            <span style={{ fontFamily: MONO, fontSize: TEXT.meta, letterSpacing: 1.2, textTransform: 'uppercase', color: P.faint }}>Chat</span>
            {CHAT.map((c, i) => (
              <motion.div key={`${c.von}-${c.t}`} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ ...SPRING, delay: i * 0.06 }}
                style={{ alignSelf: c.von === 'du' ? 'flex-end' : 'flex-start', maxWidth: '68%', padding: '7px 10px',
                  border: `1px solid ${P.line}`, background: c.von === 'du' ? P.ink : P.surface, color: c.von === 'du' ? P.bg : P.sub,
                  fontFamily: SANS, fontSize: 12.5 }}>
                {c.t}
              </motion.div>
            ))}
            <Btn P={P} filled onClick={() => setOpen(true)} lead={<IconLayoutBottombar size={11} stroke={1.8} />}>Vorschau unten oeffnen</Btn>
          </div>
          <AnimatePresence>
            {open && (
              <motion.div key="sheet" data-sheet
                initial={{ y: 260 }} animate={{ y: gross ? -140 : 0 }} exit={{ y: 260 }}
                drag="y" dragMomentum={false} dragElastic={0.18} dragConstraints={{ top: 0, bottom: 0 }}
                onDragEnd={(e, info) => { if (info.offset.y < -60) setGross(true); else if (info.offset.y > 50) setGross(false) }}
                transition={SPRING}
                style={{ position: 'absolute', left: 0, right: 0, bottom: 0, height: 300, background: P.surface,
                  borderTop: `1px solid ${P.line}`, boxShadow: '0 -12px 34px rgba(0,0,0,0.16)', display: 'flex', flexDirection: 'column', touchAction: 'none' }}>
                <div onClick={() => setGross(v => !v)} title="ziehen oder tippen"
                  style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '7px 12px', cursor: 'grab', borderBottom: `1px solid ${P.hair}` }}>
                  <span style={{ width: 34, height: 3, background: P.faint, borderRadius: RADIUS.pill }} />
                  <IconFileText size={12} stroke={1.8} color={P.sub} />
                  <Path P={P}>2026-routenplan.md</Path>
                  <Chip P={P}>18 KB</Chip>
                  <span style={{ marginLeft: 'auto', display: 'inline-flex', gap: 6 }}>
                    <IconBtn P={P} label={gross ? 'verkleinern' : 'vergroessern'}><IconChevronDown size={11} stroke={2} /></IconBtn>
                    <IconBtn P={P} label="schliessen" onClick={(e) => { e.stopPropagation(); setOpen(false) }}><IconX size={11} stroke={2} /></IconBtn>
                  </span>
                </div>
                <div style={{ flex: 1, minHeight: 0, overflow: 'auto', padding: '10px 12px' }}>
                  {FILE_LINES.slice(0, gross ? FILE_LINES.length : 4).map((t, i) => (
                    <div key={`${i}-${t || 'leer'}`} style={{ fontFamily: MONO, fontSize: 11, lineHeight: 1.75, color: t.startsWith('#') ? P.ink : P.sub }}>{t || ' '}</div>
                  ))}
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </Frame>
      )} />
  )
}

/* ============================================================ 07 · rechte Spalte */

export function V07() {
  const [w, setW] = useState(300)
  const start = useRef(300)
  const clamp = (v) => Math.min(520, Math.max(200, v))
  return (
    <Managed name="07 · Vorschau als rechte Spalte" note="Griff an der Kante ziehen aendert die Breite wirklich (Zustand, nicht nur Optik)"
      render={({ P }) => (
        <Frame P={P} style={{ height: 320, display: 'flex', overflow: 'hidden' }}>
          <div style={{ flex: 1, minWidth: 0, padding: 14, display: 'flex', flexDirection: 'column', gap: 8 }}>
            <span style={{ fontFamily: MONO, fontSize: TEXT.meta, letterSpacing: 1.2, textTransform: 'uppercase', color: P.faint }}>Arbeitsflaeche</span>
            <div style={{ flex: 1, border: `1px dashed ${P.line}`, display: 'grid', placeItems: 'center' }}>
              <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>Chat bleibt hier · {w} px fuer die Vorschau</span>
            </div>
          </div>
          <motion.div data-previewpane animate={{ width: w }} transition={SPRING}
            style={{ flexShrink: 0, borderLeft: `1px solid ${P.line}`, background: P.bg, display: 'flex', minWidth: 0 }}>
            <motion.span data-resize
              drag="x" dragMomentum={false} dragElastic={0} dragConstraints={{ left: 0, right: 0 }}
              onDragStart={() => { start.current = w }}
              onDrag={(e, info) => setW(clamp(start.current - info.offset.x))}
              whileHover={{ backgroundColor: P.line }} transition={SPRING} title="Breite ziehen"
              style={{ width: 7, cursor: 'col-resize', background: P.hair, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', touchAction: 'none' }}>
              <span style={{ width: 1, height: 22, background: P.faint }} />
            </motion.span>
            <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '7px 10px', borderBottom: `1px solid ${P.hair}` }}>
                <IconLayoutSidebarRight size={12} stroke={1.8} color={P.sub} />
                <Path P={P}>routenplan.md</Path>
                <span style={{ marginLeft: 'auto', fontFamily: MONO, fontSize: TEXT.meta, color: P.faint, fontVariantNumeric: 'tabular-nums' }}>{w} px</span>
                <IconBtn P={P} label="schliessen"><IconX size={11} stroke={2} /></IconBtn>
              </div>
              <div style={{ flex: 1, minHeight: 0, overflow: 'auto', padding: 10 }}>
                {FILE_LINES.map((t, i) => (
                  <motion.div key={`${i}-${t || 'leer'}`} initial={{ opacity: 0, x: 5 }} animate={{ opacity: 1, x: 0 }} transition={{ ...SPRING, delay: 0.03 + i * 0.03 }}
                    style={{ fontFamily: MONO, fontSize: TEXT.meta, lineHeight: 1.75, color: t.startsWith('#') ? P.ink : P.sub,
                      whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{t || ' '}</motion.div>
                ))}
              </div>
            </div>
          </motion.div>
        </Frame>
      )} />
  )
}

/* ============================================================ 08 · eigener Reiter */

const TABS = [
  { id: 'md', name: 'routenplan.md', icon: IconFileText, kind: 'TEXT', meta: '18 KB' },
  { id: 'diff', name: 'plan.diff', icon: IconColumns2, kind: 'DIFF', meta: '+5 −3' },
  { id: 'png', name: 'route-01.png', icon: IconFileTypePng, kind: 'BILD', meta: '1920 x 1080' },
  { id: 'csv', name: 'etappen.csv', icon: IconFileSpreadsheet, kind: 'TABELLE', meta: '5 Zeilen' }
]

export function V08() {
  const [tab, setTab] = useState('md')
  const aktiv = TABS.find(t => t.id === tab)
  return (
    <Managed name="08 · Vorschau im eigenen Reiter" note="Reiterleiste mit mehreren Artefakten · Inhalt wechselt mit dem Reiter (useState)"
      render={({ P }) => (
        <Frame P={P} style={{ overflow: 'hidden' }}>
          <div style={{ display: 'flex', alignItems: 'stretch', borderBottom: `1px solid ${P.line}`, background: P.bg, overflow: 'hidden' }}>
            {TABS.map(t => {
              const Ico = t.icon
              const on = tab === t.id
              return (
                <motion.span key={t.id} data-tab={t.id} onClick={() => setTab(t.id)} whileTap={{ scale: 0.97 }} transition={SPRING}
                  animate={{ backgroundColor: on ? P.surface : 'transparent', color: on ? P.ink : P.sub }}
                  style={{ position: 'relative', display: 'inline-flex', alignItems: 'center', gap: 6, padding: '8px 12px', cursor: 'pointer',
                    fontFamily: MONO, fontSize: TEXT.meta, borderRight: `1px solid ${P.hair}`, whiteSpace: 'nowrap' }}>
                  <Ico size={11} strokeWidth={1.8} />
                  {t.name}
                  {on && <motion.span layoutId="tabline" style={{ position: 'absolute', left: 0, right: 0, bottom: 0, height: 2, background: P.ink }} />}
                </motion.span>
              )
            })}
            <span style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 6, padding: '0 10px' }}>
              <Chip P={P}>{aktiv.meta}</Chip>
              <IconBtn P={P} label="Alle schliessen"><IconX size={11} stroke={2} /></IconBtn>
            </span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 12px', borderBottom: `1px solid ${P.hair}` }}>
            <IconEye size={11} stroke={1.8} color={P.faint} />
            <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>Vorschauflaeche · {aktiv.kind}</span>
            <span style={{ marginLeft: 'auto', display: 'inline-flex', gap: 6 }}>
              <IconBtn P={P} label="Kopieren"><IconCopy size={11} stroke={1.8} /></IconBtn>
              <IconBtn P={P} label="Herunterladen"><IconDownload size={11} stroke={1.8} /></IconBtn>
              <Btn P={P} lead={<IconFolderOpen size={11} stroke={1.8} />}>im Ordner zeigen</Btn>
            </span>
          </div>
          <div style={{ minHeight: 224, position: 'relative' }}>
            <AnimatePresence mode="wait" initial={false}>
              <motion.div key={tab} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -6 }} transition={SPRING} style={{ padding: 12 }}>
                {tab === 'md' && FILE_LINES.slice(0, 8).map((t, i) => (
                  <div key={`${i}-${t || 'leer'}`} style={{ fontFamily: MONO, fontSize: 11, lineHeight: 1.75, color: t.startsWith('#') ? P.ink : P.sub }}>{t || ' '}</div>
                ))}
                {tab === 'diff' && DIFF_ROWS.filter(r => r.k !== 'ctx').map((r, i) => (
                  <div key={`${r.k}-${r.l}-${r.r}`} style={{ display: 'flex', gap: 8, fontFamily: MONO, fontSize: 11, lineHeight: 1.9 }}>
                    <span style={{ color: P.faint, textDecoration: 'line-through' }}>{r.l || ' '}</span>
                    <span style={{ color: P.ink }}>→ {r.r}</span>
                  </div>
                ))}
                {tab === 'png' && <Bildflaeche P={P} zoom={100} grid={true} />}
                {tab === 'csv' && (
                  <div style={{ border: `1px solid ${P.hair}` }}>
                    {[['Etappe', 'Ort', 'Naechte'], ['1', 'Freiburg', '1'], ['2', 'Genf', '2'], ['3', 'Lyon', '1'], ['4', 'Porto', '1']].map((row, i) => (
                      <div key={row.join('|')} style={{ display: 'grid', gridTemplateColumns: '1fr 2fr 1fr', borderBottom: i === 4 ? 'none' : `1px solid ${P.hair}`, background: i === 0 ? P.hair : 'transparent' }}>
                        {row.map((c, j) => <span key={`${c}-${j}`} style={{ padding: '4px 8px', fontFamily: MONO, fontSize: TEXT.meta, color: i === 0 ? P.sub : P.ink }}>{c}</span>)}
                      </div>
                    ))}
                  </div>
                )}
              </motion.div>
            </AnimatePresence>
          </div>
          <Hint P={P}>Der Reiterwechsel ist echtes useState — der Inhalt, nicht nur die Farbe, wechselt.</Hint>
        </Frame>
      )} />
  )
}

/* ============================================================ 09 · Kompakt */

export function V09() {
  const [auf, setAuf] = useState(false)
  return (
    <Managed name="09 · sehr kompakte Vorschauzeile" note="eine Zeile „bericht.md · 18 KB · Vorschau“ zum Aufklappen"
      render={({ P }) => (
        <Frame P={P} style={{ overflow: 'hidden' }}>
          <motion.div data-compactrow onClick={() => setAuf(v => !v)} whileHover={{ backgroundColor: P.bg }} transition={{ duration: 0.15 }}
            style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '7px 10px', cursor: 'pointer' }}>
            <motion.span animate={{ rotate: auf ? 90 : 0 }} transition={SPRING} style={{ display: 'inline-flex', color: P.faint }}>
              <IconChevronRight size={12} stroke={2.2} />
            </motion.span>
            <IconFileText size={12} stroke={1.8} color={P.sub} />
            <span style={{ fontFamily: MONO, fontSize: 11, color: P.ink }}>bericht.md</span>
            <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>18 KB</span>
            <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, textDecoration: 'underline' }}>Vorschau</span>
            <span style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 6 }}>
              <motion.span animate={{ opacity: auf ? 1 : 0.55 }} transition={SPRING} style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>{auf ? 'offen' : 'zugeklappt'}</motion.span>
              <IconMessage size={11} stroke={1.8} color={P.faint} />
            </span>
          </motion.div>
          <AnimatePresence initial={false}>
            {auf && (
              <motion.div key="klappe" data-compactbody initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }} transition={SPRING}
                style={{ overflow: 'hidden', borderTop: `1px solid ${P.hair}` }}>
                <div style={{ padding: '10px 12px' }}>
                  {FILE_LINES.slice(0, 5).map((t, i) => (
                    <motion.div key={`${i}-${t || 'leer'}`} initial={{ opacity: 0, x: -4 }} animate={{ opacity: 1, x: 0 }} transition={{ ...SPRING, delay: i * 0.045 }}
                      style={{ fontFamily: MONO, fontSize: TEXT.meta, lineHeight: 1.75, color: t.startsWith('#') ? P.ink : P.sub }}>{t || ' '}</motion.div>
                  ))}
                  <div style={{ marginTop: 8, display: 'inline-flex', gap: 6 }}>
                    <Btn P={P} lead={<IconEye size={11} stroke={1.8} />}>ganze Vorschau oeffnen</Btn>
                  </div>
                </div>
              </motion.div>
            )}
          </AnimatePresence>
          <Hint P={P}>Guertel und Hosentraeger: die Zeile bleibt im Chat stehen, die Flaeche kommt nur auf Wunsch.</Hint>
        </Frame>
      )} />
  )
}

/* ============================================================ 10 · dunkle Vollbild-Fassung */

export function V10() {
  const [tab, setTab] = useState('text')
  return (
    <Managed name="10 · dunkle Fassung des Favoriten (Vollbild)" note="Vollbild dunkel · Reiter Text/Quelle · sonst identisch zur hellen Fassung"
      dark0={true}
      render={({ P }) => (
        <div data-fullscreen style={{ border: `1px solid ${P.line}`, background: P.bg, height: 420, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '9px 14px', borderBottom: `1px solid ${P.line}` }}>
            <IconFileText size={13} stroke={1.8} color={P.sub} />
            <Path P={P}>berichte/2026-routenplan.md</Path>
            <Chip P={P}>MD</Chip>
            <Chip P={P}>18 KB</Chip>
            <span style={{ display: 'inline-flex', border: `1px solid ${P.line}`, padding: 2, marginLeft: 10 }}>
              {['text', 'quelle'].map(k => (
                <motion.span key={k} data-fulltab={k} onClick={() => setTab(k)} whileTap={{ scale: 0.94 }} transition={SPRING}
                  animate={{ backgroundColor: tab === k ? P.ink : 'transparent', color: tab === k ? P.bg : P.sub }}
                  style={{ padding: '2px 9px', fontFamily: MONO, fontSize: TEXT.meta, fontWeight: 700, cursor: 'pointer', textTransform: 'uppercase' }}>{k}</motion.span>
              ))}
            </span>
            <span style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 6 }}>
              <IconBtn P={P} label="Kopieren"><IconCopy size={11} stroke={1.8} /></IconBtn>
              <IconBtn P={P} label="Herunterladen"><IconDownload size={11} stroke={1.8} /></IconBtn>
              <Btn P={P} lead={<IconFolderOpen size={11} stroke={1.8} />}>im Ordner zeigen</Btn>
              <IconBtn P={P} label="Vollbild verlassen"><IconX size={11} stroke={2} /></IconBtn>
            </span>
          </div>
          <div style={{ flex: 1, minHeight: 0, position: 'relative' }}>
            <AnimatePresence mode="wait" initial={false}>
              <motion.div key={tab} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }} transition={SPRING}
                style={{ position: 'absolute', inset: 0, overflow: 'auto', padding: '14px 18px' }}>
                {tab === 'text'
                  ? FILE_LINES.map((t, i) => (
                    <motion.div key={`${i}-${t || 'leer'}`} initial={{ opacity: 0, x: -5 }} animate={{ opacity: 1, x: 0 }} transition={{ ...SPRING, delay: i * 0.035 }}
                      style={{ display: 'flex', gap: 12 }}>
                      <span style={{ width: 22, flexShrink: 0, textAlign: 'right', fontFamily: MONO, fontSize: 11, color: P.faint, fontVariantNumeric: 'tabular-nums' }}>{i + 1}</span>
                      <span style={{ fontFamily: MONO, fontSize: 12, lineHeight: 1.85, color: t.startsWith('#') ? P.ink : P.sub, fontWeight: t.startsWith('#') ? 600 : 400 }}>{t || ' '}</span>
                    </motion.div>
                  ))
                  : (
                    <pre style={{ margin: 0, fontFamily: MONO, fontSize: 11.5, lineHeight: 1.8, color: P.sub, whiteSpace: 'pre-wrap' }}>
                      {'---\ntitel: Routenplan — Sommer\ndatum: 2026-09-14\netappen: 5\n---\n# Routenplan — Sommer\n\n## Abschnitt 1 — Vorbereitung\nStart: Freiburg · Ziel: Lissabon'}
                    </pre>
                  )}
              </motion.div>
            </AnimatePresence>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '7px 14px', borderTop: `1px solid ${P.line}` }}>
            <IconFileCode size={11} stroke={1.8} color={P.faint} />
            <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>Vollbild dunkel · dieselbe Flaeche, dieselben Bausteine — nur die Farbwerte wechseln</span>
            <span style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 6, fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>
              <IconMaximize size={11} stroke={1.8} />Vollbild
            </span>
          </div>
        </div>
      )} />
  )
}

/* ------------------------------------------------------------ Exportregister */

export default {
  '01 · Datei-Vorschau als geoeffnete Flaeche': () => <V01 />,
  '02 · Bild-Vorschau': () => <V02 />,
  '03 · Diff-Ansicht': () => <V03 />,
  '04 · Medien-Vorschau (Video/Audio)': () => <V04 />,
  '05 · Dokument-Vorschau': () => <V05 />,
  '06 · Vorschau als unteres Blatt ueber dem Chat': () => <V06 />,
  '07 · Vorschau als rechte Spalte mit Griff': () => <V07 />,
  '08 · Vorschau im eigenen Reiter': () => <V08 />,
  '09 · sehr kompakte Vorschauzeile': () => <V09 />,
  '10 · dunkle Fassung des Favoriten (Vollbild)': () => <V10 />
}
