/**
 * Source-Cards — Kategorie B: QUELLEN & RECHERCHE (Sources).
 *
 * 10 animierte Variationen: Quellenliste, Recherche-Karte, Zitat-Chips,
 * Nachweiszeilen, Belegzaehler, gruppierte Quellen, Quellen-Vorschau,
 * Lade-Zustand mit Orb und Uhr, Mini-Kompaktzeile, dunkle Favoriten-Fassung.
 *
 * Bewegung (Motion): Aus- und Einklappen laufen als Feder (Hoehe + Opazitaet),
 * Zeilen rasen versetzt ein, der Beleg-Balken fuellt sich von 0, der Lade-Orb
 * dreht unaufhoerlich und die Uhr zählt live in Sekunden hoch.
 * Nur Schwarz/Weiss/Grau, Symbole allein aus @tabler/icons-react.
 */
import { motion, AnimatePresence, animate } from 'motion/react'
import React, { useEffect, useRef, useState } from 'react'
import { IconChevronDown, IconChevronRight, IconExternalLink } from '@tabler/icons-react'

// @ts-expect-error — Laufzeit-Alias (siehe vite.config.ts)
import { ThinkingOrb } from 'thinking-orbs'
import { useBarrierefrei } from './_barrierefrei'
import { LIGHT, DARK, MONO, SANS, SPRING, RADIUS, TEXT } from './_tokens'

/** Beispiel-Quellen: Titel, Domain, Monospace-Pfad, Nummer. */
const SOURCES = [
  { n: 1, title: 'UI-Contract der Agentur', domain: 'docs.godsv.local', url: 'designs/GEWAEHLT/ui_contract.md', kind: 'Datei' },
  { n: 2, title: 'Repos-Atlas der Skaldir', domain: 'github.com/skaldir', url: 'atlas/repos_enumeration.md', kind: 'Web' },
  { n: 3, title: 'Terminal-Protokoll der Läufe', domain: 'lauf.godsv.local', url: 'logs/runs/2026-09-18_apps.txt', kind: 'Terminal' },
  { n: 4, title: 'Bericht zur UI-Ernte', domain: 'agent.godsv.local', url: 'reports/ui_harvest_2026-09.pdf', kind: 'Bericht' }
]

/** Zeile einer Quellenliste: Nummer, Titel, Domain,_pfad/URL. */
function SourceRow({ s, P, i = 0 }) {
  return (
    <motion.div initial={{ opacity: 0, x: 6 }} animate={{ opacity: 1, x: 0 }} transition={{ ...SPRING, delay: 0.05 + i * 0.05 }}
      whileHover={{ backgroundColor: P.hair }}
      style={{ display: 'flex', alignItems: 'baseline', gap: 8, padding: '5px 12px', cursor: 'pointer', borderBottom: `1px solid ${P.hair}` }}>
      <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, width: 14, flex: '0 0 auto' }}>{s.n}</span>
      <span style={{ fontFamily: SANS, fontSize: 12.5, fontWeight: 600, color: P.ink, flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.title}</span>
      <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint, flex: '0 0 auto' }}>{s.domain}</span>
    </motion.div>
  )
}

/** Monospace-Pfad-Zeile unterhalb der Liste (eine je Quelle). */
function PathRow({ s, P, i = 0 }) {
  return (
    <motion.div key={s.n} initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} transition={{ ...SPRING, delay: 0.1 + i * 0.05 }}
      style={{ padding: '3px 12px 3px 34px', borderBottom: `1px solid ${P.hair}` }}>
      <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', display: 'block' }}>└─ {s.url}</span>
    </motion.div>
  )
}

/** Titelzeile jeder Bühne: Name + Umschalter hell/dunkel. */
function StageHead({ name, note, dark, setDark, noToggle }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
      <div>
        <div style={{ fontFamily: MONO, fontSize: 12, fontWeight: 600, color: dark ? DARK.ink : LIGHT.ink }}>{name}</div>
        {note && <div style={{ fontFamily: MONO, fontSize: TEXT.meta, color: dark ? DARK.sub : LIGHT.sub, marginTop: 2 }}>{note}</div>}
      </div>
      {!noToggle && (
        <motion.button data-themetoggle onClick={() => setDark(d => !d)} whileHover={{ scale: 1.04 }} whileTap={{ scale: 0.95 }} transition={SPRING}
          style={{ marginLeft: 'auto', fontFamily: MONO, fontSize: TEXT.meta, padding: '4px 10px', cursor: 'pointer',
            background: 'transparent', border: `1px solid ${dark ? DARK.line : LIGHT.line}`, color: dark ? DARK.ink : LIGHT.ink }}>
          {dark ? '→ hell' : '→ dunkel'}
        </motion.button>
      )}
    </div>
  )
}

/* ---------------------------------------------------------------- 01 · Quellenliste */

function Case01() {
  useBarrierefrei()
  const [dark, setDark] = useState(false)
  const P = dark ? DARK : LIGHT
  const [open, setOpen] = useState(true)
  return (
    <div data-stage style={{ padding: 20, background: P.bg, fontFamily: SANS, width: '100%', maxWidth: 640, boxSizing: 'border-box' }}>
      <StageHead name="01 · Quellenliste (4 Eintraege)" note="aufklappbar, Nummer · Titel · Domain · Monospace-Pfad" dark={dark} setDark={setDark} />
      <div style={{ border: `1px solid ${P.line}`, borderRadius: RADIUS.module, background: P.surface, overflow: 'hidden', maxWidth: 420 }}>
        <motion.div onClick={() => setOpen(o => !o)} whileHover={{ backgroundColor: P.hair }} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '7px 12px', cursor: 'pointer' }}>
          <span style={{ fontFamily: SANS, fontSize: 12.5, fontWeight: 600, color: P.ink }}>Quellen</span>
          <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>4</span>
          <span style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center' }}>
            <motion.span animate={{ rotate: open ? 0 : -90 }} transition={SPRING} style={{ display: 'inline-flex', color: P.sub }}>
              <IconChevronDown size={12} stroke={2} />
            </motion.span>
          </span>
        </motion.div>
        <AnimatePresence initial={false}>
          {open && (
            <motion.div key="liste" initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }}
              transition={SPRING} style={{ overflow: 'hidden' }}>
              {SOURCES.map((s, i) => <SourceRow key={s.n} s={s} P={P} i={i} />)}
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  )
}

/* ---------------------------------------------------------------- 02 · Recherche-Karte */

function Case02() {
  useBarrierefrei()
  const [dark, setDark] = useState(false)
  const P = dark ? DARK : LIGHT
  const [open, setOpen] = useState(true)
  return (
    <div data-stage style={{ padding: 20, background: P.bg, fontFamily: SANS, width: '100%', maxWidth: 640, boxSizing: 'border-box' }}>
      <StageHead name="02 · Recherche-Karte" note="Anfrage oben, Trefferzahl, dann die Liste" dark={dark} setDark={setDark} />
      <div style={{ border: `1px solid ${P.line}`, borderRadius: RADIUS.module, background: P.surface, overflow: 'hidden', maxWidth: 440 }}>
        <div style={{ padding: '9px 12px', borderBottom: `1px solid ${P.hair}` }}>
          <div style={{ fontFamily: MONO, fontSize: TEXT.meta, letterSpacing: 1, color: P.faint, marginBottom: 3 }}>ANFRAGE</div>
          <div style={{ fontFamily: MONO, fontSize: 11.5, color: P.ink }}>skaldir agent ui zustaende quellen</div>
          <div style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, marginTop: 3 }}>
            <motion.span initial={{ opacity: 0, scale: 0.8 }} animate={{ opacity: 1, scale: 1 }} transition={{ ...SPRING, delay: 0.2 }}
              style={{ display: 'inline-block', padding: '1px 5px', border: `1px solid ${P.line}`, borderRadius: RADIUS.pill }}>28 Treffer</motion.span>
          </div>
        </div>
        <motion.div onClick={() => setOpen(o => !o)} whileHover={{ backgroundColor: P.hair }} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '5px 12px', cursor: 'pointer' }}>
          <motion.span animate={{ rotate: open ? 90 : 0 }} transition={SPRING} style={{ display: 'inline-flex', color: P.sub }}>
            <IconChevronRight size={11} stroke={2} />
          </motion.span>
          <span style={{ fontFamily: SANS, fontSize: 11.5, color: P.ink }}>Trefferliste</span>
        </motion.div>
        <AnimatePresence initial={false}>
          {open && (
            <motion.div key="treffer" initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }}
              transition={SPRING} style={{ overflow: 'hidden' }}>
              {SOURCES.map((s, i) => <SourceRow key={s.n} s={s} P={P} i={i} />)}
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  )
}

/* ---------------------------------------------------------------- 03 · Zitat-Chips */

function Case03() {
  useBarrierefrei()
  const [dark, setDark] = useState(false)
  const P = dark ? DARK : LIGHT
  const [hover, setHover] = useState(null)
  return (
    <div data-stage style={{ padding: 20, background: P.bg, fontFamily: SANS, width: '100%', maxWidth: 640, boxSizing: 'border-box' }}>
      <StageHead name="03 · Zitat-Chips an der Antwortzeile" note="kleine nummerierte Chips, Hover hebt die Quelle" dark={dark} setDark={setDark} />
      <div style={{ border: `1px solid ${P.line}`, borderRadius: RADIUS.module, background: P.surface, overflow: 'hidden', maxWidth: 480 }}>
        <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={SPRING}
          style={{ display: 'flex', padding: '8px 12px', borderBottom: `1px solid ${P.hair}` }}>
          <span style={{ display: 'flex', flex: '0 0 18px', alignItems: 'center', justifyContent: 'center', fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>sk</span>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontFamily: MONO, fontSize: 11.5, color: P.ink, lineHeight: 1.55 }}>
              Der Agent laeuft mit eigener Queue und haelt die Offer-Slots sauber offen.
              <span style={{ marginLeft: 5, display: 'inline-flex', gap: 3 }}>
                {[1, 2, 3].map(n => (
                  <motion.span key={n} whileHover={{ scale: 1.25, y: -2 }} transition={SPRING}
                    onMouseEnter={() => setHover(n)} onMouseLeave={() => setHover(null)}
                    style={{ display: 'inline-block', fontFamily: MONO, fontSize: TEXT.meta, padding: '0 4px', borderRadius: RADIUS.micro, cursor: 'pointer',
                      border: `1px solid ${hover === n ? P.ink : P.line}`, color: hover ? P.ink : P.sub, background: hover === n ? P.hair : 'transparent' }}>[{n}]</motion.span>
                ))}
              </span>
            </div>
          </div>
        </motion.div>
        <AnimatePresence>
          {hover && (
            <motion.div key={hover} initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 4 }} transition={SPRING}
              style={{ padding: '6px 12px 6px 30px', display: 'flex', alignItems: 'baseline', gap: 6 }}>
              <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>[{hover}]</span>
              <span style={{ fontFamily: SANS, fontSize: TEXT.meta, fontWeight: 600, color: P.ink }}>{SOURCES[hover - 1].title}</span>
              <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{SOURCES[hover - 1].url}</span>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  )
}

/* ---------------------------------------------------------------- 04 · Nachweiszeilen */

const PROOFS = [
  ['Anker-Pruefung', '28/28 ✓', 'tools/check_ui_overlay_anchors.py'],
  ['Orb zustaende', '9 zustaende', 'thinking-orbs/dist/types.d.ts'],
  ['Kontrast AA', '7,4:1', 'messungen/kontrast_1280x800.json'],
  ['E2E laeuft', '17 Durchlaeufe', 'outputs/ui-audit/E2E-AUDIT-2026-09.md']
]

function Case04() {
  useBarrierefrei()
  const [dark, setDark] = useState(false)
  const P = dark ? DARK : LIGHT
  return (
    <div data-stage style={{ padding: 20, background: P.bg, fontFamily: SANS, width: '100%', maxWidth: 640, boxSizing: 'border-box' }}>
      <StageHead name="04 · Nachweiszeilen (kompakt)" note="Kriterium + Beleg + Ort + ✓, fuer Pruefberichte" dark={dark} setDark={setDark} />
      <div style={{ border: `1px solid ${P.line}`, borderRadius: RADIUS.module, background: P.surface, overflow: 'hidden', maxWidth: 560 }}>
        {PROOFS.map((p, i) => (
          <motion.div key={p[0]} initial={{ opacity: 0, x: 6 }} animate={{ opacity: 1, x: 0 }} transition={{ ...SPRING, delay: 0.05 + i * 0.05 }}
            whileHover={{ backgroundColor: P.hair }} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '4px 12px', borderBottom: `1px solid ${P.hair}` }}>
            <span style={{ fontFamily: SANS, fontSize: 11, fontWeight: 600, color: P.ink, width: 110, flex: '0 0 auto' }}>{p[0]}</span>
            <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, width: 84, flex: '0 0 auto' }}>{p[1]}</span>
            <motion.span initial={{ opacity: 0, scale: 0.4 }} animate={{ opacity: 1, scale: 1 }} transition={{ ...SPRING, delay: 0.2 + i * 0.05 }}
              style={{ display: 'inline-block' }}>
              <motion.span animate={{ scale: [0.6, 1.15, 1] }} transition={{ duration: 0.35, delay: 0.3 + i * 0.05 }} style={{ display: 'inline-block' }}>
                <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.ink }}>✓</span>
              </motion.span>
            </motion.span>
            <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{p[2]}</span>
          </motion.div>
        ))}
      </div>
    </div>
  )
}

/* ---------------------------------------------------------------- 05 · Beleg-Balken */

const ProgressBar = ({ pct, P }) => {
  const ref = useRef(null)
  useEffect(() => {
    if (!ref.current) return
    ref.current.style.width = '0%'
    const ctrl = animate(ref.current, { width: `${pct}%` }, { duration: 1.4, ease: 'easeInOut', delay: 0.2 })
    return () => ctrl.stop()
  }, [pct])
  return <span ref={ref} data-progress style={{ display: 'block', height: 4, width: 0, background: P.ink }} />
}

function Case05() {
  useBarrierefrei()
  const [dark, setDark] = useState(false)
  const P = dark ? DARK : LIGHT
  const pct = 60
  return (
    <div data-stage style={{ padding: 20, background: P.bg, fontFamily: SANS, width: '100%', maxWidth: 640, boxSizing: 'border-box' }}>
      <StageHead name="05 · Beleg-Zaehler" note="6 von 10 belegt, Balken fuellt sich von 0" dark={dark} setDark={setDark} />
      <div style={{ border: `1px solid ${P.line}`, borderRadius: RADIUS.module, background: P.surface, overflow: 'hidden', maxWidth: 420 }}>
        <div style={{ padding: '10px 12px' }}>
          <motion.div initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} transition={SPRING}
            style={{ fontSize: 12.5, fontWeight: 600, color: P.ink, marginBottom: 6 }}>
            <motion.span initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.6, delay: 0.4 }}>6</motion.span>
            {' von 10 belegt'}
            <span style={{ marginLeft: 8, fontFamily: MONO, fontSize: TEXT.meta, fontWeight: 400, color: P.sub }}>60 %</span>
          </motion.div>
          <div style={{ height: 4, background: P.hair, border: `1px solid ${P.line}`, overflow: 'hidden' }}>
            <ProgressBar pct={pct} P={P} />
          </div>
          <div style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint, marginTop: 5 }}>unbelegt: 4 Kriterien · offeneste zuerst</div>
        </div>
      </div>
    </div>
  )
}

/* ---------------------------------------------------------------- 06 · Gruppierte Quellen */

function Case06() {
  useBarrierefrei()
  const [dark, setDark] = useState(false)
  const P = dark ? DARK : LIGHT
  const [open, setOpen] = useState(true)
  const GROUPS = [
    ['Datei', 2], ['Web', 1], ['Terminal', 1], ['Bericht', 1]
  ]
  return (
    <div data-stage style={{ padding: 20, background: P.bg, fontFamily: SANS, width: '100%', maxWidth: 640, boxSizing: 'border-box' }}>
      <StageHead name="06 · Quellen nach Art gruppiert" note="Datei · Web · Terminal · Bericht, Zaehler je Gruppe" dark={dark} setDark={setDark} />
      <div style={{ border: `1px solid ${P.line}`, borderRadius: RADIUS.module, background: P.surface, overflow: 'hidden', maxWidth: 420 }}>
        <motion.div onClick={() => setOpen(o => !o)} whileHover={{ backgroundColor: P.hair }} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '7px 12px', cursor: 'pointer' }}>
          <span style={{ fontFamily: SANS, fontSize: 12.5, fontWeight: 600, color: P.ink }}>Quellen</span>
          <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>5</span>
          <motion.span animate={{ rotate: open ? 0 : -90 }} transition={SPRING} style={{ marginLeft: 'auto', display: 'inline-flex', color: P.sub }}>
            <IconChevronDown size={12} stroke={2} />
          </motion.span>
        </motion.div>
        <AnimatePresence initial={false}>
          {open && (
            <motion.div key="gruppen" initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }}
              transition={SPRING} style={{ overflow: 'hidden' }}>
              {GROUPS.map((g, i) => (
                <motion.div key={g[0]} initial={{ opacity: 0, x: 6 }} animate={{ opacity: 1, x: 0 }} transition={{ ...SPRING, delay: 0.05 + i * 0.05 }}
                  whileHover={{ backgroundColor: P.hair }} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '4px 12px', borderBottom: `1px solid ${P.hair}` }}>
                  <span style={{ fontFamily: SANS, fontSize: 11.5, color: P.ink, width: 72, flex: '0 0 auto' }}>{g[0]}</span>
                  <span style={{ height: 8, flex: 1, display: 'inline-block', borderLeft: `1px solid ${P.hair}` }} />
                  <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub }}>{g[1]}</span>
                </motion.div>
              ))}
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  )
}

/* ---------------------------------------------------------------- 07 · Geoeffnete Quelle */

function Case07() {
  useBarrierefrei()
  const [dark, setDark] = useState(false)
  const P = dark ? DARK : LIGHT
  return (
    <div data-stage style={{ padding: 20, background: P.bg, fontFamily: SANS, width: '100%', maxWidth: 640, boxSizing: 'border-box' }}>
      <StageHead name="07 · Geoeffnete Quelle (Vorschau)" note="Titel, Domain, zwei Textzeilen, im Browser oeffnen" dark={dark} setDark={setDark} />
      <div style={{ border: `1px solid ${P.line}`, borderRadius: RADIUS.module, background: P.surface, overflow: 'hidden', maxWidth: 460 }}>
        <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={SPRING} style={{ padding: '10px 12px 8px' }}>
          <div style={{ fontFamily: SANS, fontSize: TEXT.body, fontWeight: 600, color: P.ink }}>Repos-Atlas der Skaldir</div>
          <div style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint, marginTop: 2 }}>github.com/skaldir</div>
          <div style={{ fontFamily: MONO, fontSize: 11, color: P.sub, marginTop: 6, lineHeight: 1.55 }}>
            Der Atlas listet jede Ui-Komponente mit ihrem Pfad und ihrem okay-Zustand.
            Er belegt, dass die Recherche nicht im Leeren laeuft.
          </div>
          <motion.span whileHover={{ backgroundColor: P.hair }} whileTap={{ scale: 0.98 }} transition={SPRING}
            style={{ marginTop: 8, display: 'inline-flex', alignItems: 'center', gap: 5, fontFamily: MONO, fontSize: TEXT.meta,
              padding: '4px 8px', border: `1px solid ${P.line}`, color: P.ink, cursor: 'pointer', borderRadius: RADIUS.row }}>
            <IconExternalLink size={11} stroke={2} />
            im Browser oeffnen
          </motion.span>
        </motion.div>
      </div>
    </div>
  )
}

/* ---------------------------------------------------------------- 08 · Lade-Zustand */

function useLive(start) {
  const [t, setT] = useState(start)
  useEffect(() => {
    const id = setInterval(() => setT(v => v + 1), 1000)
    return () => clearInterval(id)
  }, [])
  return t
}
const fmt = (s) => String(Math.floor(s / 60)).padStart(2, '0') + ':' + String(s % 60).padStart(2, '0')

function Case08() {
  useBarrierefrei()
  const [dark, setDark] = useState(false)
  const P = dark ? DARK : LIGHT
  const t = useLive(0)
  return (
    <div data-stage style={{ padding: 20, background: P.bg, fontFamily: SANS, width: '100%', maxWidth: 640, boxSizing: 'border-box' }}>
      <StageHead name="08 · Lade-Zustand: Suche laeuft" note="Orb (searching, size 20) und live laufende Uhr" dark={dark} setDark={setDark} />
      <div style={{ border: `1px solid ${P.line}`, borderRadius: RADIUS.module, background: P.surface, overflow: 'hidden', maxWidth: 420 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 12px' }}>
          <ThinkingOrb state="searching" size={20} theme={dark ? 'dark' : 'light'} />
          <motion.span animate={{ opacity: [1, 0.45, 1] }} transition={{ duration: 1.6, repeat: Infinity, ease: 'easeInOut' }}
            style={{ fontFamily: SANS, fontSize: 12.5, fontWeight: 600, color: P.ink }}>Suche laeuft</motion.span>
          <span style={{ marginLeft: 'auto', fontFamily: MONO, fontSize: TEXT.meta, color: P.sub }}>{fmt(t)}</span>
        </div>
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.4, delay: 0.3 }}
          style={{ borderTop: `1px solid ${P.hair}`, padding: '6px 12px', fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>
          Quellen-Index anhand der Skaldir-Atlas-Treffer durchsuchen
        </motion.div>
      </div>
    </div>
  )
}

/* ---------------------------------------------------------------- 09 · Mini-Zeile */

function Case09() {
  useBarrierefrei()
  const [dark, setDark] = useState(false)
  const P = dark ? DARK : LIGHT
  const [open, setOpen] = useState(false)
  return (
    <div data-stage style={{ padding: 20, background: P.bg, fontFamily: SANS, width: '100%', maxWidth: 640, boxSizing: 'border-box' }}>
      <StageHead name="09 · Kompaktzeile zum Aufklappen" note="Quellen 6 · belegt 6/10, sehr kompakt" dark={dark} setDark={setDark} />
      <div style={{ border: `1px solid ${P.line}`, borderRadius: RADIUS.module, background: P.surface, overflow: 'hidden', maxWidth: 420 }}>
        <motion.div onClick={() => setOpen(o => !o)} whileHover={{ backgroundColor: P.hair }} transition={SPRING}
          style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 12px', cursor: 'pointer' }}>
          <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.ink }}>Quellen 6 · belegt 6/10</span>
          <motion.span animate={{ rotate: open ? 180 : 0 }} transition={SPRING} style={{ marginLeft: 'auto', display: 'inline-flex', color: P.sub }}>
            <IconChevronDown size={11} stroke={2} />
          </motion.span>
        </motion.div>
        <AnimatePresence initial={false}>
          {open && (
            <motion.div key="mini" initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }}
              transition={SPRING} style={{ overflow: 'hidden' }}>
              {SOURCES.map((s, i) => <SourceRow key={s.n} s={s} P={P} i={i} />)}
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  )
}

/* ---------------------------------------------------------------- 10 · Favorit dunkel */

function Case10() {
  useBarrierefrei()
  return (
    <div data-stage style={{ padding: 20, background: DARK.bg, fontFamily: SANS, width: '100%', maxWidth: 640, boxSizing: 'border-box', minHeight: 300 }}>
      <StageHead name="10 · Favorit DUNKEL: Quellenliste" note="Vollbild dunkel, Fassung 01" dark noToggle />
      <div style={{ border: `1px solid ${DARK.line}`, borderRadius: RADIUS.module, background: DARK.surface, overflow: 'hidden', maxWidth: 420 }}>
        <motion.div initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} transition={SPRING}
          style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '7px 12px' }}>
          <span style={{ fontFamily: SANS, fontSize: 12.5, fontWeight: 600, color: DARK.ink }}>Quellen</span>
          <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: DARK.faint }}>4</span>
        </motion.div>
        {SOURCES.map((s, i) => (
          <motion.div key={s.n} initial={{ opacity: 0, x: 6 }} animate={{ opacity: 1, x: 0 }} transition={{ ...SPRING, delay: 0.05 + i * 0.05 }}
            whileHover={{ backgroundColor: DARK.hair }}
            style={{ display: 'flex', alignItems: 'baseline', gap: 8, padding: '5px 12px', cursor: 'pointer', borderTop: `1px solid ${DARK.hair}` }}>
            <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: DARK.sub, width: 14, flex: '0 0 auto' }}>{s.n}</span>
            <span style={{ fontFamily: SANS, fontSize: 12.5, fontWeight: 600, color: DARK.ink, flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.title}</span>
            <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: DARK.faint, flex: '0 0 auto' }}>{s.domain}</span>
          </motion.div>
        ))}
        {SOURCES.map((s, i) => <PathRow key={'p' + s.n} s={s} P={DARK} i={i} />)}
      </div>
    </div>
  )
}

/* ---------------------------------------------------------------- Export */

export default {
  '01 · Quellenliste: 4 Eintraege, aufklappbar': () => <Case01 />,
  '02 · Recherche-Karte: Anfrage, Treffer, Liste': () => <Case02 />,
  '03 · Zitat-Chips an der Antwortzeile': () => <Case03 />,
  '04 · Nachweiszeilen: Kriterium + Beleg + Ort + ✓': () => <Case04 />,
  '05 · Beleg-Zaehler: 6 von 10, Balken von 0': () => <Case05 />,
  '06 · Quellen nach Art gruppiert': () => <Case06 />,
  '07 · Geoeffnete Quelle (Vorschau)': () => <Case07 />,
  '08 · Lade-Zustand: Suche laeuft, Orb + Uhr': () => <Case08 />,
  '09 · Mini-Kompaktzeile zum Aufklappen': () => <Case09 />,
  '10 · Favorit DUNKEL: Quellenliste': () => <Case10 />
}
