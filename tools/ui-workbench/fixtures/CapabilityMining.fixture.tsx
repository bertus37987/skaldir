/**
 * Capability-Mining — Kategorie G: KANDIDATEN & BELEGE.
 *
 * Wiederkehrende Arbeit wird Werkzeug: die Sicht zeigt, WELCHE Arbeit sich
 * wiederholt, WIE OFT sie beobachtet wurde (Belege mit Zeitstempel) und was
 * daraus entstehen würde. Bisher war das nur über die Kommandozeile sichtbar.
 *
 * 10 animierte Varianten: Kandidatenliste, aufgeklappter Kandidat mit
 * Arbeitsschritten, Beleg-Häufigkeit als Balken gegen die Schwelle, Vorschau
 * des entstehenden Werkzeugs, laufendes Anlegen (3 Schritte → angelegt),
 * Ablehnen mit Grund und „nicht wieder vorschlagen", Verwerfen mit
 * layout-Animation, Vergleich zweier Kandidaten, Kompaktzeile zum Aufklappen,
 * dunkle Favoriten-Fassung.
 *
 * Bewegung (Motion): Zeilen rasen versetzt ein, Balken füllen sich von 0,
 * Kopfzahlen springen bei Layout-Änderung, Auf-/Zuklappen läuft als Feder.
 * Der Endzustand „angelegt" ist bewusst STATISCH (kein Motion, nur Häkchen).
 * Zustände sind echt (useState): Anlegen/Ablehnen/Verwerfen wirken, die Liste
 * wächst und schrumpft sichtbar. Nur Schwarz/Weiß/Grau (Palette aus ./_tokens),
 * Symbole aus @tabler/icons-react. Reduced Motion und Tastatur: useBarrierefrei() (A1/A5).
 */
import { motion, AnimatePresence, animate } from 'motion/react'
import React, { useEffect, useRef, useState } from 'react'
import { IconAlertCircle, IconArrowRight, IconBan, IconChartBar, IconCheck, IconChevronDown, IconChevronRight, IconClock, IconEye, IconEyeOff, IconFlask, IconHammer, IconListCheck, IconPlug, IconPlus, IconRefresh, IconSparkles, IconTarget, IconTerminal, IconX } from '@tabler/icons-react'

// @ts-expect-error — Laufzeit-Alias (siehe vite.config.ts)
import { ThinkingOrb } from 'thinking-orbs'
import { LIGHT, DARK, MONO, SANS, SPRING, RADIUS, TEXT } from './_tokens'
import { useBarrierefrei } from './_barrierefrei'

/* Farben, Schriften, Federn, Radien und Schriftgroessen kommen aus ./_tokens (eine Wahrheit) */

/* ---------- Fachliche Konstanten der Mining-Sicht ---------- */
const THRESH = 5    // Belege je Woche, ab denen ein Kandidat über der Schwelle liegt
const MAXREF = 10   // Bezugsgröße der Balken

const KIND_ICON: Record<string, React.ComponentType<{ size?: number; strokeWidth?: number }>> = {
  Skill: IconSparkles, CLI: IconTerminal, MCP: IconPlug, Testwerkzeug: IconFlask,
}

type C = { id: string; kind: string; name: string; freq: number; last: string; note: string }

const CANDS: C[] = [
  { id: 'skill', kind: 'Skill',        name: 'Wöchentlicher Repo-Abgleich', freq: 9, last: 'vor 2 h',     note: 'Atlas lesen, Stand prüfen, Kurzbericht schreiben' },
  { id: 'test',  kind: 'Testwerkzeug', name: 'Overlay-Anker prüfen',        freq: 7, last: 'vor 1 h',     note: 'Anker gegen jede Route abgleichen' },
  { id: 'cli',   kind: 'CLI',          name: 'Kontrastmessung Vollbild',    freq: 6, last: 'gestern',     note: 'Kontrast je Bildschirmgröße messen' },
  { id: 'mcp',   kind: 'MCP',          name: 'Berichts-Index abfragen',     freq: 3, last: 'vor 4 Tagen', note: 'Noch unter der Schwelle' },
]

/** Belegte Arbeitsschritte eines Kandidaten: Zeit, Tätigkeit, Dauer. */
const BEATS: [string, string, string][] = [
  ['Mo 09:12', 'Repos-Atlas geöffnet, 24 Einträge gezählt', '0:41'],
  ['Mo 09:13', 'Stand je Repo geprüft (letzter Commit)', '1:52'],
  ['Mo 09:15', 'Drei Abweichungen notiert, keine davon neu', '0:58'],
  ['Di 08:40', 'Dieselbe Folge erneut — identische Reihenfolge', '3:05'],
  ['Di 08:44', 'Kurzbericht als Markdown geschrieben', '2:11'],
  ['Mi 09:02', 'Wieder Atlas → Stand → Bericht, dritte Wiederholung', '2:59'],
]

/* ---------- Kopfzeile und Rahmen jeder Bühne ---------- */
function StageHead({ name, note, dark, setDark, noToggle }: { name: string; note?: string; dark: boolean; setDark?: (d: boolean) => void; noToggle?: boolean }) {
  const P = dark ? DARK : LIGHT
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
      <div>
        <div style={{ fontFamily: MONO, fontSize: 12, fontWeight: 600, color: P.ink }}>{name}</div>
        {note && <div style={{ fontFamily: MONO, fontSize: 11, color: P.sub, marginTop: 2 }}>{note}</div>}
      </div>
      {!noToggle && (
        <motion.button onClick={() => setDark && setDark(!dark)} whileHover={{ scale: 1.04 }} whileTap={{ scale: 0.95 }} transition={SPRING}
          data-theme-toggle
          style={{ marginLeft: 'auto', fontFamily: MONO, fontSize: 11, padding: '4px 10px', cursor: 'pointer',
                   background: 'transparent', border: `1px solid ${P.line}`, color: P.ink }}>
          {dark ? '→ hell' : '→ dunkel'}
        </motion.button>
      )}
    </div>
  )
}

function Stage({ dark, setDark, name, note, noToggle, maxWidth = 700, children }:
  { dark: boolean; setDark?: (d: boolean) => void; name: string; note?: string; noToggle?: boolean; maxWidth?: number; children: React.ReactNode }) {
  useBarrierefrei()   // A1 reduzierte Bewegung, A5 Tastatur (Befund 1+2)
  const P = dark ? DARK : LIGHT
  return (
    <div data-stage style={{ padding: 20, background: P.bg, fontFamily: SANS, width: '100%', maxWidth, boxSizing: 'border-box' }}>
      <StageHead name={name} note={note} dark={dark} setDark={setDark} noToggle={noToggle} />
      <div data-stagebody>{children}</div>
    </div>
  )
}

/* ---------- Bausteine ---------- */

/** Balken: füllt sich von 0 auf pct — echter animate()-Lauf, kein CSS-Übergang. */
function Bar({ pct, P, delay = 0, h = 6 }: { pct: number; P: typeof LIGHT; delay?: number; h?: number }) {
  const ref = useRef<HTMLSpanElement | null>(null)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    el.style.width = '0%'
    const c = animate(el, { width: pct + '%' }, { duration: 1.1, ease: 'easeInOut', delay })
    return () => c.stop()
  }, [pct, delay])
  return (
    <span style={{ display: 'block', height: h, background: P.hair, overflow: 'hidden' }}>
      <span ref={ref} data-bar style={{ display: 'block', height: h, width: 0, background: P.ink }} />
    </span>
  )
}

/** Zahl, die bei Layout-Änderung im Kopf springt (popLayout). */
function JumpNumber({ value, P }: { value: number; P: typeof LIGHT }) {
  return (
    <span style={{ position: 'relative', display: 'inline-block', minWidth: 10, height: 14, verticalAlign: 'baseline' }}>
      <AnimatePresence mode="popLayout" initial={false}>
        <motion.span key={value} data-count initial={{ y: 9, opacity: 0 }} animate={{ y: 0, opacity: 1 }}
          exit={{ y: -9, opacity: 0 }} transition={SPRING}
          style={{ position: 'absolute', left: 0, top: 0, fontFamily: MONO, fontSize: 12, fontWeight: 600, color: P.ink }}>
          {value}
        </motion.span>
      </AnimatePresence>
    </span>
  )
}

const nowort = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`

/** Eine Kandidatenzeile: Art, Name, Häufigkeit, letzte Beobachtung, „anlegen". */
function CandidateRow({ c, P, dark, created, onAnlegen, delay = 0 }:
  { c: C; P: typeof LIGHT; dark: boolean; created: boolean; onAnlegen: () => void; delay?: number }) {
  const Icon = KIND_ICON[c.kind] || IconHammer
  const over = c.freq >= THRESH
  return (
    <motion.div layout initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, x: -14 }}
      transition={{ ...SPRING, delay }} data-candidate={c.id} data-created={created ? 'true' : 'false'}
      whileHover={{ backgroundColor: P.hair }}
      style={{ display: 'flex', alignItems: 'center', gap: 9, padding: '7px 11px', borderBottom: `1px solid ${P.hair}`, background: P.surface }}>
      <span style={{ width: 16, display: 'inline-flex', justifyContent: 'center', color: P.ink, flex: '0 0 auto' }}>
        <Icon size={13} stroke={2} />
      </span>
      <span style={{ width: 92, flex: '0 0 auto', fontFamily: MONO, fontSize: 11, color: P.sub }}>{c.kind}</span>
      <span style={{ flex: 1, minWidth: 0, fontSize: 12, fontWeight: 600, color: P.ink, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.name}</span>
      <span title="Belege in 7 Tagen" style={{ width: 46, flex: '0 0 auto', textAlign: 'right', fontFamily: MONO, fontSize: 11, color: over ? P.ink : P.faint }}>
        {c.freq}/{MAXREF}
      </span>
      {over
        ? <span style={{ width: 8, flex: '0 0 auto', display: 'inline-flex', justifyContent: 'center', color: P.ink }}><IconCheck size={11} stroke={2.6} /></span>
        : <span style={{ width: 8, flex: '0 0 auto', display: 'inline-flex', justifyContent: 'center', color: P.faint }}><IconClock size={11} stroke={2} /></span>}
      <span style={{ width: 86, flex: '0 0 auto', fontFamily: MONO, fontSize: 11, color: P.faint, textAlign: 'right' }}>{c.last}</span>
      <AnimatePresence initial={false} mode="popLayout">
        {created ? (
          <motion.span key="ok" initial={{ opacity: 0, scale: 0.8 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0 }} transition={SPRING}
            data-created-label
            style={{ flex: '0 0 auto', width: 88, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: 4,
                     fontFamily: MONO, fontSize: 11, color: P.ink }}>
            <IconCheck size={11} stroke={2.6} /> angelegt
          </motion.span>
        ) : (
          <motion.button key="an" onClick={onAnlegen} whileHover={{ scale: 1.04 }} whileTap={{ scale: 0.94 }} transition={SPRING}
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            style={{ flex: '0 0 auto', width: 88, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: 4,
                     padding: '4px 0', cursor: 'pointer', background: 'transparent', border: `1px solid ${P.line}`,
                     color: P.ink, fontFamily: MONO, fontSize: 11 }}>
            <IconPlus size={11} stroke={2} /> anlegen
          </motion.button>
        )}
      </AnimatePresence>
    </motion.div>
  )
}

/* ---------------------------------------------------------------- 01 · Kandidatenliste */

function CandidateList({ dark, restrict }: { dark: boolean; restrict?: string[] }) {
  const P = dark ? DARK : LIGHT
  const rows = restrict ? CANDS.filter(c => restrict.indexOf(c.id) >= 0) : CANDS
  const [created, setCreated] = useState<Record<string, boolean>>({})
  const n = rows.filter(r => created[r.id]).length
  return (
    <div style={{ border: `1px solid ${P.line}`, borderRadius: RADIUS.row, background: P.surface, overflow: 'hidden' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '7px 11px', borderBottom: `1px solid ${P.line}` }}>
        <IconListCheck size={12} stroke={2} color={P.sub} />
        <span style={{ fontSize: 12.5, fontWeight: 600, color: P.ink }}>Mining-Kandidaten</span>
        <span style={{ fontFamily: MONO, fontSize: 11, color: P.faint }}>{nowort(rows.length, 'Kandidat', 'Kandidaten')}</span>
        <span style={{ marginLeft: 'auto', fontFamily: MONO, fontSize: 11, color: P.sub }}>
          Schwelle {THRESH} Belege/7 Tage
        </span>
      </div>
      {rows.map((c, i) => (
        <CandidateRow key={c.id} c={c} P={P} dark={dark} delay={0.05 + i * 0.05}
          created={!!created[c.id]} onAnlegen={() => setCreated(s => ({ ...s, [c.id]: true }))} />
      ))}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 11px', fontFamily: MONO, fontSize: 11, color: P.faint }}>
        <span data-created-total>angelegt: {n} von {rows.length}</span>
        <span style={{ marginLeft: 'auto' }}>Arbeit, die sich wiederholt, wird Werkzeug — nichts ohne Belege</span>
      </div>
    </div>
  )
}

function Case01() {
  const [dark, setDark] = useState(false)
  return (
    <Stage dark={dark} setDark={setDark} name="01 · Kandidatenliste (4 Kandidaten)"
      note="Art · Name · Häufigkeit · letzte Beobachtung · „anlegen“ — der Knopf wirkt, die Zeile zeigt danach „angelegt“">
      <CandidateList dark={dark} />
    </Stage>
  )
}

/* ---------------------------------------------------------------- 02 · Kandidat aufgeklappt */

function Case02() {
  const [dark, setDark] = useState(false)
  const P = dark ? DARK : LIGHT
  const [open, setOpen] = useState(true)
  const c = CANDS[0]
  const Icon = KIND_ICON[c.kind]
  return (
    <Stage dark={dark} setDark={setDark} name="02 · Kandidat aufgeklappt: die beobachteten Arbeitsschritte"
      note="sechs Belege mit Zeitstempel und Dauer — genau diese Wiederholung begründet den Kandidaten">
      <div style={{ border: `1px solid ${P.line}`, borderRadius: RADIUS.row, background: P.surface, overflow: 'hidden' }}>
        <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={SPRING}
          onClick={() => setOpen(o => !o)}
          style={{ display: 'flex', alignItems: 'center', gap: 9, padding: '9px 11px', cursor: 'pointer', borderBottom: open ? `1px solid ${P.hair}` : 'none' }}>
          <motion.span animate={{ rotate: open ? 90 : 0 }} transition={SPRING} style={{ display: 'inline-flex', color: P.sub }}>
            <IconChevronRight size={12} stroke={2} />
          </motion.span>
          <span style={{ display: 'inline-flex', color: P.ink }}><Icon size={13} stroke={2} /></span>
          <span style={{ fontSize: 12.5, fontWeight: 600, color: P.ink }}>{c.name}</span>
          <span style={{ fontFamily: MONO, fontSize: 11, color: P.faint }}>{c.kind}</span>
          <span style={{ flex: 1 }} />
          <span style={{ fontFamily: MONO, fontSize: 11, color: P.sub }}>{nowort(c.freq, 'Beleg', 'Belege')} · {c.last}</span>
        </motion.div>
        <AnimatePresence initial={false}>
          {open && (
            <motion.div key="beats" data-beats initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }}
              exit={{ height: 0, opacity: 0 }} transition={SPRING} style={{ overflow: 'hidden' }}>
              {BEATS.map((b, i) => (
                <motion.div key={b[1]} data-beat initial={{ opacity: 0, x: 6 }} animate={{ opacity: 1, x: 0 }}
                  transition={{ ...SPRING, delay: 0.06 + i * 0.045 }}
                  style={{ display: 'flex', alignItems: 'baseline', gap: 10, padding: '5px 11px 5px 30px', borderBottom: `1px solid ${P.hair}` }}>
                  <span style={{ width: 62, flex: '0 0 auto', fontFamily: MONO, fontSize: 11, color: P.sub }}>{b[0]}</span>
                  <span style={{ width: 14, flex: '0 0 auto', fontFamily: MONO, fontSize: 11, color: P.faint }}>{i + 1}</span>
                  <span style={{ flex: 1, fontSize: 11.5, color: P.ink }}>{b[1]}</span>
                  <span style={{ fontFamily: MONO, fontSize: 11, color: P.faint }}>{b[2]}</span>
                </motion.div>
              ))}
              <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.4 }}
                style={{ display: 'flex', alignItems: 'center', gap: 7, padding: '7px 11px', fontFamily: MONO, fontSize: 11, color: P.sub }}>
                <IconAlertCircle size={11} stroke={2} />
                <span>Gleiche Reihenfolge in allen sechs Belegen — deshalb ein Kandidat, nicht Zufall</span>
                <span style={{ marginLeft: 'auto' }}>{c.note}</span>
              </motion.div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </Stage>
  )
}

/* ---------------------------------------------------------------- 03 · Beleg-Häufigkeit als Balken */

function Case03() {
  const [dark, setDark] = useState(false)
  const P = dark ? DARK : LIGHT
  const [run, setRun] = useState(0)
  const over = CANDS.filter(c => c.freq >= THRESH).length
  return (
    <Stage dark={dark} setDark={setDark} name="03 · Beleg-Häufigkeit gegen die Schwelle"
      note="Balken füllen sich von 0; die gestrichelte Linie ist die Schwelle von 5 Belegen je Woche">
      <div style={{ border: `1px solid ${P.line}`, borderRadius: RADIUS.row, background: P.surface, overflow: 'hidden' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '7px 11px', borderBottom: `1px solid ${P.line}` }}>
          <IconChartBar size={12} stroke={2} color={P.sub} />
          <span style={{ fontSize: 12.5, fontWeight: 600, color: P.ink }}>Belege je Kandidat</span>
          <span style={{ fontFamily: MONO, fontSize: 11, color: P.faint }}>Zeitraum 7 Tage</span>
          <motion.button onClick={() => setRun(r => r + 1)} whileTap={{ scale: 0.95 }} transition={SPRING}
            style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 5, background: 'transparent', cursor: 'pointer',
                     border: `1px solid ${P.line}`, color: P.ink, fontFamily: MONO, fontSize: 11, padding: '4px 10px' }}>
            <IconRefresh size={11} stroke={2} /> neu zählen
          </motion.button>
        </div>
        <div key={run} style={{ padding: '10px 11px 4px' }}>
          {CANDS.map((c, i) => {
            const ok = c.freq >= THRESH
            return (
              <div key={c.id} data-evidence={c.id} style={{ marginBottom: 11 }}>
                <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 4 }}>
                  <span style={{ fontSize: 11.5, fontWeight: 600, color: P.ink }}>{c.name}</span>
                  <span style={{ fontFamily: MONO, fontSize: 11, color: P.faint }}>{c.kind}</span>
                  <span style={{ flex: 1 }} />
                  {ok && (
                    <motion.span initial={{ opacity: 0, scale: 0.85 }} animate={{ opacity: 1, scale: 1 }}
                      transition={{ ...SPRING, delay: 0.5 + i * 0.08 }}
                      data-threshold-reached
                      style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontFamily: MONO, fontSize: 11, color: P.ink }}>
                      <IconCheck size={10} stroke={2.6} /> Schwelle erreicht
                    </motion.span>
                  )}
                  <span style={{ width: 42, textAlign: 'right', fontFamily: MONO, fontSize: 11, color: ok ? P.ink : P.faint }}>{c.freq}/10</span>
                </div>
                <div style={{ position: 'relative' }}>
                  <Bar pct={(c.freq / MAXREF) * 100} P={{ ...P, ink: ok ? P.ink : P.sub }} delay={0.08 + i * 0.1} h={6} />
                  <span style={{ position: 'absolute', left: `${(THRESH / MAXREF) * 100}%`, top: -2, bottom: -2, borderLeft: `1px dashed ${P.sub}` }} />
                </div>
              </div>
            )
          })}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '7px 11px', borderTop: `1px solid ${P.hair}`, fontFamily: MONO, fontSize: 11, color: P.sub }}>
          <IconTarget size={11} stroke={2} />
          <span data-over-threshold>{over} von {CANDS.length} über der Schwelle — nur diese werden zum Anlegen angeboten</span>
          <span style={{ marginLeft: 'auto', color: P.faint }}>Belege: Läufe mit identischer Arbeitsschritt-Folge</span>
        </div>
      </div>
    </Stage>
  )
}

/* ---------------------------------------------------------------- 04 · Vorschau des Werkzeugs */

const PREVIEW: Record<string, { name: string; inputs: string[]; outputs: string[]; call: string; file: string }> = {
  Skill: {
    name: 'repo-abgleich',
    inputs: ['Zeitraum (Standard 7 Tage)', 'Repo-Liste (Atlas oder explizit)', 'Berichtsziel (Pfad)'],
    outputs: ['Bericht markdown, eine Zeile je Abweichung', 'Abweichungsliste als Tabelle'],
    call: 'skaldir skill run repo-abgleich --zeitraum 7d --ziel reports/abgleich.md',
    file: 'skills/repo-abgleich/SKILL.md',
  },
  CLI: {
    name: 'repo-abgleich',
    inputs: ['--zeitraum Tage', '--repos <liste>', '--ziel <pfad>'],
    outputs: ['Exit 0/1 · Abweichungen ja/nein', 'Markdown auf stdout'],
    call: 'skaldir repo-abgleich --zeitraum 7d > reports/abgleich.md',
    file: 'tools/repo-abgleich/main.rs',
  },
}

function Case04() {
  const [dark, setDark] = useState(false)
  const P = dark ? DARK : LIGHT
  const [kind, setKind] = useState<'Skill' | 'CLI'>('Skill')
  const v = PREVIEW[kind]
  return (
    <Stage dark={dark} setDark={setDark} name="04 · Vorschau: was würde entstehen?"
      note="Name, Eingaben, Ausgaben, Beispielaufruf und der Ort, an dem das Werkzeug landet">
      <div style={{ border: `1px solid ${P.line}`, borderRadius: RADIUS.row, background: P.surface, overflow: 'hidden' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 11px', borderBottom: `1px solid ${P.hair}` }}>
          <span style={{ fontSize: 12.5, fontWeight: 600, color: P.ink }}>{v.name}</span>
          <span style={{ fontFamily: MONO, fontSize: 11, color: P.faint }}>geplant aus 9 Belegen</span>
          <span style={{ marginLeft: 'auto', display: 'inline-flex', border: `1px solid ${P.line}` }}>
            {(['Skill', 'CLI'] as const).map(k => {
              const on = k === kind
              return (
                <button key={k} onClick={() => setKind(k)} data-kind={k}
                  style={{ padding: '4px 10px', border: 'none', cursor: 'pointer', fontFamily: MONO, fontSize: 11,
                           background: on ? P.ink : 'transparent', color: on ? P.bg : P.sub }}>{k}</button>
              )
            })}
          </span>
        </div>
        <AnimatePresence mode="wait" initial={false}>
          <motion.div key={kind} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -6 }} transition={SPRING}>
            {([['Eingaben', v.inputs], ['Ausgaben', v.outputs]] as [string, string[]][]).map(([label, list]) => (
              <div key={label} style={{ display: 'flex', gap: 12, padding: '8px 11px', borderBottom: `1px solid ${P.hair}` }}>
                <span style={{ width: 78, flex: '0 0 auto', fontFamily: MONO, fontSize: 11, letterSpacing: 0.6, color: P.faint, textTransform: 'uppercase', paddingTop: 2 }}>{label}</span>
                <div style={{ flex: 1 }}>
                  {list.map((x, i) => (
                    <motion.div key={x} initial={{ opacity: 0, x: 5 }} animate={{ opacity: 1, x: 0 }} transition={{ ...SPRING, delay: 0.05 + i * 0.05 }}
                      style={{ display: 'flex', alignItems: 'baseline', gap: 7, padding: '2px 0' }}>
                      <IconArrowRight size={10} stroke={2} color={P.faint} />
                      <span style={{ fontSize: 11.5, color: P.ink }}>{x}</span>
                    </motion.div>
                  ))}
                </div>
              </div>
            ))}
            <div style={{ padding: '9px 11px', borderBottom: `1px solid ${P.hair}` }}>
              <div style={{ fontFamily: MONO, fontSize: 11, letterSpacing: 0.6, color: P.faint, textTransform: 'uppercase', marginBottom: 5 }}>Beispielaufruf</div>
              <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.35, delay: 0.2 }}
                style={{ fontFamily: MONO, fontSize: 11, color: P.ink, background: P.bg, border: `1px solid ${P.hair}`, padding: '6px 8px', whiteSpace: 'pre-wrap' }}>
                {v.call}
              </motion.div>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '7px 11px', fontFamily: MONO, fontSize: 11, color: P.sub }}>
              <span>Landet in</span>
              <span style={{ color: P.ink }}>{v.file}</span>
              <span style={{ marginLeft: 'auto', color: P.faint }}>nichts wird angelegt, bevor du zustimmst</span>
            </div>
          </motion.div>
        </AnimatePresence>
      </div>
    </Stage>
  )
}

/* ---------------------------------------------------------------- 05 · Anlegen läuft */

const STEPS = ['Kandidat prüfen: Belege reichen?', 'Werkzeug erzeugen', 'Registrieren und einmal testen']

function Case05() {
  const [dark, setDark] = useState(false)
  const P = dark ? DARK : LIGHT
  const [step, setStep] = useState(-1)   // -1 ruht · 0..2 läuft · 3 angelegt
  useEffect(() => {
    if (step < 0 || step > 2) return
    const id = setTimeout(() => setStep(s => s + 1), 900)
    return () => clearTimeout(id)
  }, [step])
  const done = step >= 3
  const running = step >= 0 && step <= 2
  return (
    <Stage dark={dark} setDark={setDark} name="05 · Anlegen läuft — drei Schritte, dann „angelegt“"
      note="Endzustand ohne Animation: statisches Häkchen, kein Orb, kein Puls — Fertig ist ein Statussymbol">
      <div style={{ border: `1px solid ${P.line}`, borderRadius: RADIUS.row, background: P.surface, overflow: 'hidden' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 11px', borderBottom: `1px solid ${P.hair}` }}>
          <span style={{ width: 20, display: 'inline-flex', justifyContent: 'center' }}>
            {running && <ThinkingOrb state="working" size={20} theme={dark ? 'dark' : 'light'} />}
            {done && <IconCheck size={16} stroke={2.6} color={P.ink} />}
            {step === -1 && <span style={{ color: P.faint, display: 'inline-flex' }}><IconHammer size={14} stroke={2} /></span>}
          </span>
          <span style={{ fontSize: 12.5, fontWeight: 600, color: P.ink }} data-status>
            {step === -1 ? 'Wöchentlicher Repo-Abgleich · Skill' : done ? 'angelegt' : 'wird angelegt …'}
          </span>
          <span style={{ flex: 1 }} />
          <span style={{ fontFamily: MONO, fontSize: 11, color: P.faint }}>
            {step === -1 ? 'bereit' : done ? 'skills/repo-abgleich/ · registriert' : STEPS[step]}
          </span>
        </div>
        <div style={{ padding: '10px 11px 6px' }}>
          <Bar pct={done ? 100 : running ? ((step + 0.5) / 3) * 100 : 0} P={P} delay={0} h={5} />
          <div style={{ marginTop: 9 }}>
            {STEPS.map((s, i) => {
              const state = step > i ? 'ok' : step === i ? 'now' : 'wait'
              return (
                <div key={s} data-step={s} data-step-state={state}
                  style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '4px 0', borderBottom: i === STEPS.length - 1 ? 'none' : `1px solid ${P.hair}` }}>
                  <span style={{ width: 14, display: 'inline-flex', justifyContent: 'center', color: state === 'wait' ? P.faint : P.ink }}>
                    {state === 'ok' ? <IconCheck size={12} stroke={2.6} /> : state === 'now' ? <IconClock size={11} stroke={2} /> : <span style={{ fontSize: 11, fontFamily: MONO }}>·</span>}
                  </span>
                  <span style={{ flex: 1, fontSize: 11.5, color: state === 'wait' ? P.faint : P.ink }}>{s}</span>
                  {state === 'now' && (
                    <motion.span animate={{ opacity: [1, 0.4, 1] }} transition={{ duration: 1.1, repeat: Infinity, ease: 'easeInOut' }}
                      style={{ fontFamily: MONO, fontSize: 11, color: P.sub }}>läuft</motion.span>
                  )}
                  {state === 'ok' && <motion.span initial={{ opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }} transition={SPRING} style={{ fontFamily: MONO, fontSize: 11, color: P.faint }}>fertig</motion.span>}
                </div>
              )
            })}
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 11px', borderTop: `1px solid ${P.hair}` }}>
          {!running && (
            <motion.button onClick={() => setStep(done ? -1 : 0)} whileHover={{ scale: 1.03 }} whileTap={{ scale: 0.95 }} transition={SPRING}
              style={{ display: 'inline-flex', alignItems: 'center', gap: 5, padding: '4px 10px', cursor: 'pointer', background: 'transparent',
                       border: `1px solid ${P.line}`, color: P.ink, fontFamily: MONO, fontSize: 11, padding: '5px 11px' }}>
              {done ? <IconRefresh size={11} stroke={2} /> : <IconPlus size={11} stroke={2} />}
              {done ? 'noch einmal ansehen' : 'anlegen starten'}
            </motion.button>
          )}
          <span style={{ fontFamily: MONO, fontSize: 11, color: P.faint }} data-hint>
            {done ? 'angelegt · 1 Testlauf grün · ab jetzt als Werkzeug verfügbar' : running ? 'bitte warten — drei Schritte, kein Abbruch nötig' : 'noch nichts angelegt'}
          </span>
        </div>
      </div>
    </Stage>
  )
}

/* ---------------------------------------------------------------- 06 · Abgelehnt */

const REASONS = [
  'Zu selten — unter der Schwelle',
  'Aufwand lohnt sich nicht',
  'Geht von Hand schneller',
  'Ein vorhandenes Werkzeug deckt das ab',
]

function Case06() {
  const [dark, setDark] = useState(false)
  const P = dark ? DARK : LIGHT
  const [reason, setReason] = useState<string | null>(null)
  const [never, setNever] = useState(false)
  const [done, setDone] = useState(false)
  return (
    <Stage dark={dark} setDark={setDark} name="06 · Ablehnen: Grund nennen, „nicht wieder vorschlagen“"
      note="Der Grund wird gewählt, nicht getippt; erst mit Grund ist die Ablehnung gültig">
      <div style={{ border: `1px solid ${P.line}`, borderRadius: RADIUS.row, background: P.surface, overflow: 'hidden' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 11px', borderBottom: `1px solid ${P.hair}` }}>
          <span style={{ display: 'inline-flex', color: P.ink }}><IconPlug size={13} stroke={2} /></span>
          <span style={{ fontSize: 12.5, fontWeight: 600, color: P.ink }}>Berichts-Index abfragen</span>
          <span style={{ fontFamily: MONO, fontSize: 11, color: P.faint }}>MCP · 3 Belege · vor 4 Tagen</span>
          <span style={{ flex: 1 }} />
          <span style={{ fontFamily: MONO, fontSize: 11, color: P.sub }}>{done ? 'abgelehnt' : 'offen'}</span>
        </div>
        <AnimatePresence mode="wait" initial={false}>
          {!done ? (
            <motion.div key="form" initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -6 }} transition={SPRING}>
              <div style={{ padding: '9px 11px 4px' }}>
                <div style={{ fontFamily: MONO, fontSize: 11, letterSpacing: 0.6, color: P.faint, textTransform: 'uppercase', marginBottom: 6 }}>Grund</div>
                {REASONS.map((r, i) => {
                  const on = reason === r
                  return (
                    <motion.div key={r} initial={{ opacity: 0, x: -5 }} animate={{ opacity: 1, x: 0 }} transition={{ ...SPRING, delay: 0.04 + i * 0.04 }}
                      onClick={() => setReason(r)} data-reason={r} data-selected={on ? 'true' : 'false'}
                      style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '5px 0', cursor: 'pointer', borderBottom: `1px solid ${P.hair}` }}>
                      <span style={{ width: 14, height: 14, flex: '0 0 auto', border: `1px solid ${on ? P.ink : P.line}`, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', background: on ? P.ink : 'transparent' }}>
                        {on && <IconCheck size={9} stroke={3} color={P.bg} />}
                      </span>
                      <span style={{ fontSize: 11.5, color: on ? P.ink : P.sub }}>{r}</span>
                    </motion.div>
                  )
                })}
              </div>
              <div onClick={() => setNever(n => !n)} data-never={never ? 'true' : 'false'}
                style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 11px', cursor: 'pointer', borderTop: `1px solid ${P.hair}` }}>
                <span style={{ width: 26, height: 14, flex: '0 0 auto', border: `1px solid ${never ? P.ink : P.line}`, position: 'relative', background: never ? P.ink : 'transparent' }}>
                  <motion.span animate={{ x: never ? 12 : 0 }} transition={SPRING}
                    style={{ position: 'absolute', top: 1, left: 1, width: 10, height: 10, background: never ? P.bg : P.line }} />
                </span>
                <span style={{ fontSize: 11.5, color: P.ink }}>nicht wieder vorschlagen</span>
                <span style={{ fontFamily: MONO, fontSize: 11, color: P.faint }}>{never ? 'gilt für „Berichts-Index abfragen“ — Muster bleibt sonst beobachtet' : 'ohne Haken kann derselbe Kandidat wieder auftauchen'}</span>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 11px', borderTop: `1px solid ${P.hair}` }}>
                <motion.button disabled={!reason} onClick={() => setDone(true)} whileHover={reason ? { scale: 1.03 } : undefined} whileTap={reason ? { scale: 0.95 } : undefined} transition={SPRING}
                  data-submit style={{ display: 'inline-flex', alignItems: 'center', gap: 5, padding: '4px 10px', cursor: reason ? 'pointer' : 'not-allowed',
                    background: reason ? P.ink : 'transparent', border: `1px solid ${reason ? P.ink : P.line}`, color: reason ? P.bg : P.faint, fontFamily: MONO, fontSize: 11, padding: '5px 11px' }}>
                  <IconBan size={11} stroke={2} /> Ablehnung festhalten
                </motion.button>
                <span style={{ fontFamily: MONO, fontSize: 11, color: reason ? P.sub : P.faint }}>
                  {reason ? 'Grund: „' + reason + '“' : 'ohne Grund bleibt der Knopf gesperrt'}
                </span>
              </div>
            </motion.div>
          ) : (
            <motion.div key="done" initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={SPRING} data-rejected style={{ padding: '10px 11px' }}>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
                <IconX size={13} stroke={2.4} color={P.ink} />
                <span style={{ fontSize: 12.5, fontWeight: 600, color: P.ink }}>abgelehnt — gemerkt</span>
                <span style={{ flex: 1 }} />
                <span style={{ fontFamily: MONO, fontSize: 11, color: P.faint }}>{never ? 'nicht wieder vorschlagen: ja' : 'nicht wieder vorschlagen: nein'}</span>
              </div>
              <div style={{ marginTop: 6, fontSize: 11.5, color: P.sub, lineHeight: 1.5 }}>
                Grund: <span style={{ color: P.ink }}>{reason}</span>. Der Belegzähler läuft weiter, der Kandidat verschwindet aus der Anlege-Liste.
              </div>
              <motion.button onClick={() => { setDone(false); setReason(null); setNever(false) }} whileTap={{ scale: 0.95 }} transition={SPRING}
                style={{ marginTop: 8, display: 'inline-flex', alignItems: 'center', gap: 5, padding: '3px 8px', cursor: 'pointer', background: 'transparent',
                         border: `1px solid ${P.line}`, color: P.ink, fontFamily: MONO, fontSize: 11, padding: '4px 10px' }}>
                <IconRefresh size={10} stroke={2} /> zurücknehmen
              </motion.button>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </Stage>
  )
}

/* ---------------------------------------------------------------- 07 · Verwerfen mit Layout-Animation */

function Case07() {
  const [dark, setDark] = useState(false)
  const P = dark ? DARK : LIGHT
  const [rows, setRows] = useState<C[]>(CANDS.slice(0, 3))
  const over = rows.filter(r => r.freq >= THRESH).length
  return (
    <Stage dark={dark} setDark={setDark} name="07 · Kandidat verworfen: er verschwindet, die Kopfzahl springt"
      note="layout-Animation: die Zeile fällt heraus, die Zahl im Kopf hüpft auf den neuen Stand">
      <div style={{ border: `1px solid ${P.line}`, borderRadius: RADIUS.row, background: P.surface, overflow: 'hidden' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '7px 11px', borderBottom: `1px solid ${P.line}` }}>
          <IconListCheck size={12} stroke={2} color={P.sub} />
          <span style={{ display: 'inline-flex', alignItems: 'baseline', gap: 5, fontSize: 12.5, fontWeight: 600, color: P.ink }}>
            <JumpNumber value={rows.length} P={P} />
            <span>{rows.length === 1 ? 'Kandidat' : 'Kandidaten'}</span>
          </span>
          <span style={{ fontFamily: MONO, fontSize: 11, color: P.faint }} data-over>{over} über der Schwelle</span>
          <span style={{ marginLeft: 'auto' }}>
            <motion.button onClick={() => setRows(CANDS.slice(0, 3))} whileTap={{ scale: 0.95 }} transition={SPRING}
              style={{ display: 'inline-flex', alignItems: 'center', gap: 5, padding: '3px 8px', cursor: 'pointer', background: 'transparent',
                       border: `1px solid ${P.line}`, color: P.ink, fontFamily: MONO, fontSize: 11, padding: '4px 10px' }}>
              <IconRefresh size={10} stroke={2} /> zurücksetzen
            </motion.button>
          </span>
        </div>
        <motion.div layout style={{ padding: 0 }}>
          <AnimatePresence initial={false}>
            {rows.map((c, i) => {
              const Icon = KIND_ICON[c.kind]
              return (
                <motion.div key={c.id} layout data-verwerfbar={c.id}
                  initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, x: -20, height: 0 }}
                  transition={{ ...SPRING, delay: 0 }}
                  whileHover={{ backgroundColor: P.hair }}
                  style={{ display: 'flex', alignItems: 'center', gap: 9, padding: '7px 11px', borderBottom: `1px solid ${P.hair}`, overflow: 'hidden' }}>
                  <span style={{ display: 'inline-flex', color: P.ink }}><Icon size={13} stroke={2} /></span>
                  <span style={{ width: 92, fontFamily: MONO, fontSize: 11, color: P.sub }}>{c.kind}</span>
                  <span style={{ flex: 1, fontSize: 12, fontWeight: 600, color: P.ink }}>{c.name}</span>
                  <span style={{ fontFamily: MONO, fontSize: 11, color: c.freq >= THRESH ? P.ink : P.faint }}>{c.freq}/10</span>
                  <motion.button onClick={() => setRows(rs => rs.filter(r => r.id !== c.id))} whileHover={{ scale: 1.12 }} whileTap={{ scale: 0.9 }} transition={SPRING}
                    data-verwerfen={c.id}
                    style={{ display: 'inline-flex', alignItems: 'center', gap: 4, padding: '4px 9px', cursor: 'pointer', background: 'transparent',
                             border: `1px solid ${P.line}`, color: P.sub, fontFamily: MONO, fontSize: 11 }}>
                    <IconX size={10} stroke={2.2} /> verwerfen
                  </motion.button>
                </motion.div>
              )
            })}
          </AnimatePresence>
        </motion.div>
        <div style={{ padding: '6px 11px', fontFamily: MONO, fontSize: 11, color: P.faint }}>
          {rows.length === 0 ? 'Liste leer — „zurücksetzen“ holt die drei Kandidaten zurück' : 'verworfen heißt: aus der Anlege-Liste heraus, Belege bleiben gespeichert'}
        </div>
      </div>
    </Stage>
  )
}

/* ---------------------------------------------------------------- 08 · Vergleich zweier Kandidaten */

const METRICS = [
  { key: 'Belege in 7 Tagen', unit: 'von 10', a: 9, b: 6 },
  { key: 'Aufwand je Lauf', unit: 'Minuten', a: 3, b: 2, invert: true },
  { key: 'Nutzen danach', unit: 'von 5', a: 4, b: 3 },
]

function Case08() {
  const [dark, setDark] = useState(false)
  const P = dark ? DARK : LIGHT
  const [pick, setPick] = useState<'a' | 'b' | null>(null)
  const A = CANDS[0], B = CANDS[2]
  return (
    <Stage dark={dark} setDark={setDark} name="08 · Zwei Kandidaten nebeneinander" maxWidth={760}
      note="Häufigkeit, Aufwand und Nutzen je Spalte — die Balken laufen von 0, die Auswahl ist echt">
      <div style={{ border: `1px solid ${P.line}`, borderRadius: RADIUS.row, background: P.surface, overflow: 'hidden' }}>
        <div style={{ display: 'grid', gridTemplateColumns: '150px 1fr 1fr', borderBottom: `1px solid ${P.line}` }}>
          <div style={{ padding: '8px 11px', fontFamily: MONO, fontSize: 11, letterSpacing: 0.6, color: P.faint, textTransform: 'uppercase' }}>Kennzahl</div>
          {[A, B].map(c => {
            const Icon = KIND_ICON[c.kind]
            const on = pick === (c === A ? 'a' : 'b')
            return (
              <motion.div key={c.id} onClick={() => setPick(on ? null : (c === A ? 'a' : 'b'))} whileTap={{ scale: 0.99 }} transition={SPRING}
                data-column={c.id} data-picked={on ? 'true' : 'false'}
                style={{ display: 'flex', alignItems: 'center', gap: 7, padding: '8px 11px', cursor: 'pointer',
                         borderLeft: `1px solid ${P.hair}`, background: on ? P.hair : 'transparent' }}>
                <span style={{ display: 'inline-flex', color: P.ink }}><Icon size={12} stroke={2} /></span>
                <span style={{ fontSize: 11.5, fontWeight: 600, color: P.ink }}>{c.name}</span>
                {on && <IconCheck size={11} stroke={2.6} color={P.ink} />}
              </motion.div>
            )
          })}
        </div>
        {METRICS.map((m, i) => (
          <div key={m.key} data-metric={m.key} style={{ display: 'grid', gridTemplateColumns: '150px 1fr 1fr', borderBottom: `1px solid ${P.hair}`, padding: '0' }}>
            <div style={{ padding: '9px 11px', fontSize: 11, color: P.sub }}>{m.key}</div>
            {[m.a, m.b].map((val, k) => {
              const best = m.invert ? Math.min(m.a, m.b) === val : Math.max(m.a, m.b) === val
              const pct = m.invert ? (val / 5) * 100 : (val / 10) * 100
              const seite = k === 0 ? 'a' : 'b'   // feste Kennung je Spalte statt Index (Befund C4)
              return (
                <div key={seite} style={{ padding: '9px 11px', borderLeft: `1px solid ${P.hair}` }}>
                  <div style={{ display: 'flex', alignItems: 'baseline', gap: 6, marginBottom: 5 }}>
                    <motion.span initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.3 + i * 0.1 }}
                      style={{ fontFamily: MONO, fontSize: 11, color: best ? P.ink : P.sub }}>{val} {m.unit}</motion.span>
                    {best && <motion.span initial={{ opacity: 0, scale: 0.8 }} animate={{ opacity: 1, scale: 1 }} transition={{ ...SPRING, delay: 0.6 + i * 0.1 }}
                      style={{ fontFamily: MONO, fontSize: 11, color: P.ink }}>vorn</motion.span>}
                  </div>
                  <Bar pct={pct} P={{ ...P, ink: best ? P.ink : P.sub }} delay={0.1 + i * 0.1 + k * 0.08} h={5} />
                </div>
              )
            })}
          </div>
        ))}
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 11px' }}>
          <IconEye size={11} stroke={2} color={P.sub} />
          <span style={{ fontSize: 11.5, color: P.ink }} data-verdict>
            {pick === 'a' ? 'gewählt: Wöchentlicher Repo-Abgleich — mehr Belege, mehr Nutzen je Lauf'
              : pick === 'b' ? 'gewählt: Kontrastmessung Vollbild — geringerer Aufwand, aber weniger Nutzen'
                : 'Spalte anklicken, um sie zu wählen — beide liegen über der Schwelle'}
          </span>
          <span style={{ marginLeft: 'auto', fontFamily: MONO, fontSize: 11, color: P.faint }}>
            Aufwand: weniger ist besser · sonst gilt: mehr ist besser
          </span>
        </div>
      </div>
    </Stage>
  )
}

/* ---------------------------------------------------------------- 09 · Kompaktzeile */

const TWO = [CANDS[2], CANDS[3]]  // 6 Belege (über Schwelle) und 3 (unter Schwelle)

function Case09() {
  const [dark, setDark] = useState(false)
  const P = dark ? DARK : LIGHT
  const [open, setOpen] = useState(false)
  const over = TWO.filter(c => c.freq >= THRESH).length
  return (
    <Stage dark={dark} setDark={setDark} name="09 · sehr kompakt: eine Zeile zum Aufklappen" maxWidth={560}
      note="„2 Kandidaten · 1 über der Schwelle“ — zugeklappt eine Zeile, aufgeklappt die Belege">
      <div style={{ border: `1px solid ${P.line}`, borderRadius: RADIUS.row, background: P.surface, overflow: 'hidden' }}>
        <motion.div onClick={() => setOpen(o => !o)} whileHover={{ backgroundColor: P.hair }} transition={SPRING}
          data-compact style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 11px', cursor: 'pointer' }}>
          <span style={{ fontFamily: MONO, fontSize: 11, color: P.ink }}>
            {TWO.length} Kandidaten · {over} über der Schwelle
          </span>
          <span style={{ fontFamily: MONO, fontSize: 11, color: P.faint }}>{open ? 'Belege sichtbar' : 'Mining'}</span>
          <motion.span animate={{ rotate: open ? 180 : 0 }} transition={SPRING} style={{ marginLeft: 'auto', display: 'inline-flex', color: P.sub }}>
            <IconChevronDown size={11} stroke={2} />
          </motion.span>
        </motion.div>
        <AnimatePresence initial={false}>
          {open && (
            <motion.div key="mini" initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }}
              transition={SPRING} style={{ overflow: 'hidden' }}>
              {TWO.map((c, i) => (
                <motion.div key={c.id} data-mini={c.id} initial={{ opacity: 0, x: 5 }} animate={{ opacity: 1, x: 0 }}
                  transition={{ ...SPRING, delay: 0.05 + i * 0.06 }}
                  style={{ display: 'flex', alignItems: 'baseline', gap: 8, padding: '5px 11px', borderTop: `1px solid ${P.hair}` }}>
                  <span style={{ width: 84, fontFamily: MONO, fontSize: 11, color: P.faint }}>{c.kind}</span>
                  <span style={{ flex: 1, fontSize: 11.5, color: P.ink }}>{c.name}</span>
                  <span style={{ fontFamily: MONO, fontSize: 11, color: c.freq >= THRESH ? P.ink : P.faint }}>{c.freq}/10</span>
                  <span style={{ width: 84, textAlign: 'right', fontFamily: MONO, fontSize: 11, color: P.faint }}>{c.last}</span>
                </motion.div>
              ))}
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '6px 11px', borderTop: `1px solid ${P.hair}`, fontFamily: MONO, fontSize: 11, color: P.sub }}>
                <IconAlertCircle size={10} stroke={2} />
                <span>die dritte Beobachtung liegt noch unter der Schwelle — weiter beobachten, nichts anlegen</span>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </Stage>
  )
}

/* ---------------------------------------------------------------- 10 · Favorit dunkel (Vollbild) */

function Case10() {
  const [dark, setDark] = useState(true)   // Favorit startet dunkel (Vollbild)
  const P = dark ? DARK : LIGHT
  return (
    <Stage dark={dark} setDark={setDark} maxWidth={760} name="10 · Favorit DUNKEL: Kandidatenliste"
      note="Vollbild dunkel, Fassung 01 — dieselben Zeilen, dieselben Federn, dunkle Tinte; eine Zeile sammelt noch Belege">
      <CandidateList dark={dark} />
      <div style={{ marginTop: 10, border: `1px solid ${P.line}`, borderRadius: RADIUS.row, background: P.surface, overflow: 'hidden' }}>
        <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={SPRING}
          style={{ display: 'flex', alignItems: 'center', gap: 9, padding: '8px 11px' }}>
          <span style={{ width: 20, display: 'inline-flex', justifyContent: 'center' }}>
            <ThinkingOrb state="breathing" size={20} theme={dark ? 'dark' : 'light'} />
          </span>
          <span style={{ fontSize: 12, fontWeight: 600, color: P.ink }}>Berichts-Index abfragen</span>
          <span style={{ fontFamily: MONO, fontSize: 11, color: P.sub }}>wird weiter beobachtet · 3 von 5 Belegen</span>
          <span style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 5, fontFamily: MONO, fontSize: 11, color: P.faint }}>
            <IconEyeOff size={11} stroke={2} /> noch kein Kandidat zum Anlegen
          </span>
        </motion.div>
      </div>
    </Stage>
  )
}

/* ---------------------------------------------------------------- Export */

export default {
  '01 · Kandidatenliste: 4 Kandidaten mit Häufigkeit und „anlegen“': () => <Case01 />,
  '02 · Kandidat aufgeklappt: beobachtete Arbeitsschritte mit Zeitstempeln': () => <Case02 />,
  '03 · Beleg-Häufigkeit als Balken, Schwelle markiert': () => <Case03 />,
  '04 · Vorschau des entstehenden Werkzeugs: Name, Ein-/Ausgaben, Aufruf': () => <Case04 />,
  '05 · Anlegen läuft: drei Schritte, danach „angelegt“ (statisch)': () => <Case05 />,
  '06 · Abgelehnt: Grund angeben, „nicht wieder vorschlagen“': () => <Case06 />,
  '07 · Kandidat verworfen: verschwindet, Kopfzahl springt mit': () => <Case07 />,
  '08 · Vergleich zweier Kandidaten nebeneinander': () => <Case08 />,
  '09 · sehr kompakt: eine Zeile zum Aufklappen': () => <Case09 />,
  '10 · dunkle Fassung des Favoriten (Vollbild dunkel)': () => <Case10 />,
} as const
