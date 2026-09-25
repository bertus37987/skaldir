/**
 * Skaldir Agent — LEERES App-Mockup (Stand 18.09.2026, zweite Runde).
 *
 * Owner-Vorgaben dieser Runde:
 *   1. Subagenten-Ueberwachungspanel links an die Session-Leiste, verschiebbar (andocken/frei schweben).
 *   2. Projektordner ausklappbar, mehrere Sitzungen je Projekt.
 *   3. Goal-Bar ueber dem Chat; leer zeigt sie in beiden Zustaenden den @goal-Satz.
 *
 * Weiterhin gilt: keine Fake-Prozesse, keine Uhren, keine Tokens, kein Computer-Use-Overlay.
 * Bausteine ausschliesslich aus dem vorhandenen Bestand (ChatBarApp, LeerPanels, Original-Huelle).
 */
import React, { useRef, useState } from 'react'
import { motion, AnimatePresence } from 'motion/react'
import {
  IconMessage, IconFolderOpen, IconFiles, IconGitBranch, IconTerminal2, IconCpu, IconSettings,
  IconSearch, IconPlus, IconX, IconLayoutGrid, IconLayoutSidebar, IconDownload,
  IconUpload, IconTrash, IconChevronRight, IconChevronDown, IconScript, IconCheck, IconGripVertical,
  IconPin, IconPinFilled
} from '@tabler/icons-react'
import { ChatBarApp } from './skaldir/app/ChatBarApp'
import { LeererChat, KontextKapsel, SubagentenPanel } from './skaldir/app/LeerPanels'

const T = {
  bg: 'var(--background)', surface: 'var(--card)', ink: 'var(--foreground)', sub: 'var(--muted-foreground)',
  faint: 'var(--faint)', line: 'var(--border)', hair: 'var(--hair)', soft: 'var(--secondary)'
}
const MONO = 'var(--font-mono, ui-monospace), monospace'
const SANS = "'Geist Sans', system-ui, sans-serif"

/** Der Satz der leeren Goal-Bar — Vorgabe des Owners. */
const ZIEL_LEER = '@goal define a goal and i actually get it done.'

const RAIL = [
  { id: 'chat', Icon: IconMessage, label: 'Chat' },
  { id: 'dateien', Icon: IconFiles, label: 'Dateien' },
  { id: 'projekte', Icon: IconFolderOpen, label: 'Projekte' },
  { id: 'review', Icon: IconGitBranch, label: 'Review' },
  { id: 'terminal', Icon: IconTerminal2, label: 'Terminal' },
  { id: 'jobs', Icon: IconCpu, label: 'Jobs' }
] as const
/* Einstellungsbereiche nach dem Hermes-Dialog (18 Bereiche, zweite Reiter-Ebene, Suchpille).
   Jeder Bereich zeigt seinen ehrlichen Leerzustand — keine erfundenen Werte. */
const EINSTELLUNGEN = [
  { id: 'modell', label: 'Modell', text: 'kein Modell gewaehlt — Hauptmodell, Vorgaben und Hilfsmodelle werden hier gesetzt.' },
  { id: 'anbieter', label: 'Anbieter', text: 'keine Anbindung eingerichtet.', unter: ['Konten', 'API-Schluessel', 'Eigene Endpunkte', 'Lokale Modelle'] },
  { id: 'werkzeuge', label: 'Werkzeuge & Schluessel', text: 'keine Werkzeuge verbunden.', unter: ['Werkzeuge', 'Schluessel'] },
  { id: 'gateways', label: 'Gateways', text: 'keine Verbindung eingerichtet — lokal, remote, SSH oder Cloud.' },
  { id: 'mitteilungen', label: 'Mitteilungen', text: 'Hinweise sind eingeschaltet (Vorgabe an).' },
  { id: 'darstellung', label: 'Darstellung', text: 'Helligkeit, Schrift und Oberflaechengroesse.' },
  { id: 'kuerzel', label: 'Tastenkuerzel', text: 'Kuerzel: 76 Aktionen sind aenderbar — in der App an einer Einzelquelle, hier noch nicht verdrahtet.' },
  { id: 'archiv', label: 'Archivierte Chats', text: 'keine archivierten Chats.' },
  { id: 'abrechnung', label: 'Abrechnung', text: 'kein Konto verbunden — Abrechnung erscheint nach der Anmeldung.' },
  { id: 'ueber', label: 'Ueber', text: 'Skaldir Agent' }
] as const

/* Die 17 Zeilen des Panel-Kastens — Bauart und Reihenfolge wie in der App: links vor rechts,
   Zeilenreihenfolge der Leiste. Quelle: AUFBAU/10 §2.2 (19 Elemente minus gateway-switcher und
   profile-switcher, die kein `toggleLabel` haben). gesperrt = Zustand, keine Vorliebe
   (App: lockedVisible, 3 Zeilen). aus = startet versteckt (App: STATUSBAR_HIDDEN_BY_DEFAULT, 10 Zeilen).
   Bewusst KEINE Farben, kein zweiter Zustand: der Kasten liest, was die Leiste zeigt. */
const STATUSBAR_ZEILEN = [
  { id: 'command-center', wort: 'Kommandozentrale', gesperrt: true, aus: false },
  { id: 'gateway-health', wort: 'Gateway', gesperrt: false, aus: false },
  { id: 'free-tier', wort: 'Nous (Freistufe)', gesperrt: false, aus: false },
  { id: 'workspace-cwd', wort: 'Arbeitsordner', gesperrt: false, aus: false },
  { id: 'agents', wort: 'Agenten', gesperrt: false, aus: true },
  { id: 'cron', wort: 'Cron', gesperrt: false, aus: true },
  { id: 'webhooks', wort: 'Webhooks', gesperrt: false, aus: true },
  { id: 'running-timer', wort: 'Laufzeit', gesperrt: false, aus: true },
  { id: 'context-usage', wort: 'Kontext-Auslastung', gesperrt: false, aus: true },
  { id: 'cache-hit-rate', wort: 'Cache-Trefferquote', gesperrt: false, aus: true },
  { id: 'tokens-per-second', wort: 'Token pro Sekunde', gesperrt: false, aus: true },
  { id: 'session-timer', wort: 'Sitzungsdauer', gesperrt: false, aus: true },
  { id: 'system-resources', wort: 'System-Ressourcen', gesperrt: false, aus: true },
  { id: 'approval-mode', wort: 'Freigabemodus', gesperrt: false, aus: false },
  { id: 'terminal', wort: 'Terminal', gesperrt: false, aus: true },
  { id: 'version-client', wort: 'Version & Updates', gesperrt: true, aus: false },
  { id: 'version-backend', wort: 'Backend-Version', gesperrt: true, aus: false }
] as const

/* Der Kasten fuehrt KEINEN eigenen Zustand — er liest, was die Leiste zeigt (AUFBAU/11 §3.4:
   "Ein Zustand, zwei Flaechen"). Ein zweiter Speicher waere genau der Fehler, den es zu
   vermeiden gilt; in der App sind es `$statusbarHiddenIds` + `setStatusbarItemVisible`. */
type Sitzung = { id: string; titel: string; when: string; n: number }
type Projekt = { id: string; name: string; offen: boolean; sitzungen: Sitzung[] }

const PROJEKTE_START: Projekt[] = [
  { id: 'p1', name: 'gods-voice', offen: true, sitzungen: [
    { id: 's1', titel: 'UI-Abnahme', when: 'heute', n: 0 },
    { id: 's2', titel: 'Werkbank-Fixtures', when: 'gestern', n: 0 }
  ] },
  { id: 'p2', name: 'ui-demo', offen: false, sitzungen: [
    { id: 's3', titel: 'Leeres Mockup', when: 'heute', n: 0 }
  ] }
]
const DATEIBAUM = [
  { ordner: 'designs/GEWAEHLT', dateien: ['README.md', 'chatbar.tsx'] },
  { ordner: 'fixtures', dateien: ['SkaldirApp.fixture.tsx', '_tokens.ts'] },
  { ordner: 'docs', dateien: ['UI-CONTRACT.md'] }
]

export default function App() {
  const [view, setView] = useState<string>('chat')
  const [einst, setEinst] = useState<string>('modell')
  const [unter, setUnter] = useState<number>(0)
  const [einstSuche, setEinstSuche] = useState('')
  const [mitteilungen, setMitteilungen] = useState({ hintergrund: true, fertig: true, fehler: true, freigabe: true })
  const [schrift, setSchrift] = useState('Geist Sans')
  const [skala, setSkala] = useState('90 %')
  const [einstOffen, setEinstOffen] = useState(false)
  const [projekte, setProjekte] = useState<Projekt[]>(PROJEKTE_START)
  const [ohneProjekt, setOhneProjekt] = useState<Sitzung[]>([])
  const [aktiv, setAktiv] = useState<string | null>(null)
  const [suche, setSuche] = useState('')
  const [verlauf, setVerlauf] = useState<{ id: number; text: string }[]>([])
  const [hinweis, setHinweis] = useState<{ id: number; t: string }[]>([])
  const [panelAn, setPanelAn] = useState({ kontext: true, rechts: true })
  const [manager, setManager] = useState(false)
  const [dark, setDark] = useState(false)
  const [dateiOffen, setDateiOffen] = useState<string | null>(null)
  /** Subagenten-Panel: angedockt links oder frei schwebend. */
  const [subFrei, setSubFrei] = useState(false)
  const [subPos, setSubPos] = useState({ x: 300, y: 120 })
  const boxRef = useRef<HTMLDivElement | null>(null)
  const zieh = useRef<{ dx: number; dy: number } | null>(null)

  React.useEffect(() => { document.documentElement.classList.toggle('dark', dark) }, [dark])
  const melden = (t: string) => { const id = Date.now() + Math.random(); setHinweis(x => [...x, { id, t }]); setTimeout(() => setHinweis(x => x.filter(y => y.id !== id)), 3200) }
  const neueSitzung = () => {
    const s: Sitzung = { id: 's' + Date.now(), titel: 'Neue Sitzung', when: 'gerade', n: 0 }
    setOhneProjekt(l => [s, ...l]); setAktiv(s.id); setVerlauf([]); setView('chat'); melden('Neue Sitzung angelegt')
  }
  const senden = (text: string) => { setVerlauf(v => [...v, { id: Date.now(), text }]); melden('Nachricht steht im Verlauf — im Mockup antwortet kein Modell') }
  const projektAuf = (id: string) => setProjekte(p => p.map(x => x.id === id ? { ...x, offen: !x.offen } : x))
  const alleSitzungen = [...projekte.flatMap(p => p.sitzungen), ...ohneProjekt]
  const aktivTitel = alleSitzungen.find(s => s.id === aktiv)?.titel

  const alleAuf = () => setProjekte(p => p.map(x => ({ ...x, offen: true })))
  const alleZu = () => setProjekte(p => p.map(x => ({ ...x, offen: false })))

  /* Ziehen: direkte Pointer-Fuehrung ueber Fenster-Ereignisse.
     Grund fuer den Umbau: beim Losreissen des angedockten Panels verschwindet der Griff aus dem DOM,
     damit riss die Zeigerbindung ab und das Panel folgte dem Zeiger nicht mehr. Ausserdem wird die
     Startposition jetzt ebenfalls begrenzt (vorher hing das Panel unten aus dem Fenster). */
  const begrenzen = (x: number, y: number) => {
    const b = boxRef.current?.getBoundingClientRect()
    const w = b?.width ?? 244, h = b?.height ?? 160
    return { x: Math.max(8, Math.min(window.innerWidth - w - 8, x)), y: Math.max(8, Math.min(window.innerHeight - h - 8, y)) }
  }
  const ziehStart = (e: React.PointerEvent) => {
    const box = boxRef.current?.getBoundingClientRect()
    const griff = (e.currentTarget as HTMLElement).getBoundingClientRect()
    const dx = box ? e.clientX - box.left : Math.min(griff.width - 8, 60)
    const dy = box ? e.clientY - box.top : griff.height / 2
    if (!subFrei) setSubFrei(true)
    let letzte = begrenzen(e.clientX - dx, e.clientY - dy)
    setSubPos(letzte)
    const bewegen = (ev: PointerEvent) => {
      letzte = begrenzen(ev.clientX - dx, ev.clientY - dy)
      if (boxRef.current) boxRef.current.style.transform = `translate3d(${letzte.x}px, ${letzte.y}px, 0)`
    }
    const loslassen = () => {
      window.removeEventListener('pointermove', bewegen)
      window.removeEventListener('pointerup', loslassen)
      setSubPos(letzte)
      melden('Subagenten-Panel frei gestellt — mit dem Griff weiterziehen')
    }
    window.addEventListener('pointermove', bewegen)
    window.addEventListener('pointerup', loslassen)
    e.preventDefault()
  }

  const Klick = ({ label, onClick, aktiv: istAktiv, children, breit }: {
    label: string; onClick: () => void; aktiv?: boolean; children?: React.ReactNode; breit?: boolean
  }) => (
    <button type="button" title={label} aria-label={label} aria-pressed={istAktiv} onClick={onClick}
      className="inline-flex items-center gap-1.5 rounded"
      style={{ width: breit ? '100%' : undefined, height: 24, padding: breit ? '0 8px' : 0, justifyContent: breit ? 'flex-start' : 'center',
               fontSize: 11, border: `1px solid ${istAktiv ? T.line : 'transparent'}`, background: istAktiv ? T.soft : 'transparent', color: T.ink }}>
      {children}
    </button>
  )
  const Leerzeile = ({ text }: { text: string }) => <p style={{ fontFamily: MONO, fontSize: 11, color: T.faint, padding: '10px 2px' }}>{text}</p>

  const SitzungsKnopf = ({ s }: { s: Sitzung }) => (
    <button type="button" onClick={() => { setAktiv(s.id); setView('chat') }}
      className="flex w-full items-center gap-1.5 rounded py-1 pl-6 pr-2 text-left"
      style={{ background: aktiv === s.id ? T.soft : 'transparent', fontSize: 11 }}>
      <IconMessage size={11} stroke={1.8} color={T.faint} />
      <span className="truncate" style={{ flex: 1 }}>{s.titel}</span>
      <span style={{ fontFamily: MONO, fontSize: 11, color: T.faint }}>{s.n}</span>
    </button>
  )

  const SubPanel = () => (
    <div style={{ width: 236 }}>
      <div className="mb-1 flex items-center gap-1">
        <button type="button" aria-label="Subagenten-Panel verschieben" title="Ziehen: Panel frei stellen · Andocken: zurueck an die Leiste"
          onPointerDown={ziehStart}
          className="flex cursor-grab items-center justify-center rounded"
          style={{ width: 22, height: 18, border: `1px solid ${T.line}`, touchAction: 'none' }}>
          <IconGripVertical size={12} stroke={1.8} color={T.faint} />
        </button>
        <span style={{ fontFamily: MONO, fontSize: 11, color: T.faint }}>{subFrei ? 'frei — ziehen' : 'an der Leiste'}</span>
        <button type="button" aria-label={subFrei ? 'Subagenten-Panel andocken' : 'Subagenten-Panel frei stellen'}
          onClick={() => { if (subFrei) { setSubFrei(false); melden('Subagenten-Panel wieder an der Leiste') } else { setSubFrei(true); setSubPos({ x: 300, y: 140 }); melden('Subagenten-Panel frei gestellt — mit dem Griff ziehen') } }}
          className="ml-auto flex items-center justify-center rounded" style={{ width: 22, height: 18, border: `1px solid ${T.line}` }}>
          {subFrei ? <IconPinFilled size={12} stroke={1.8} color={T.ink} /> : <IconPin size={12} stroke={1.8} color={T.faint} />}
        </button>
      </div>
      <SubagentenPanel />
    </div>
  )

  return (
    <div className="flex h-full flex-col" style={{ background: T.bg, color: T.ink, fontFamily: SANS }}>
      <header className="flex shrink-0 items-center gap-2.5 px-3.5" style={{ height: 34, background: T.surface, borderBottom: `1px solid ${T.line}` }}>
        <span style={{ width: 12, height: 12, borderRadius: 999, border: `1px solid ${T.line}` }} aria-hidden />
        <span style={{ fontSize: 11.5, color: T.sub }}>Skaldir Agent</span>
        <nav className="ml-1 flex gap-1" aria-label="Reiter">
          {RAIL.slice(0, 3).map(r => (
            <button key={r.id} type="button" onClick={() => setView(r.id)} className="rounded px-2 py-0.5"
              style={{ fontSize: 11.5, border: `1px solid ${view === r.id ? T.line : 'transparent'}`, background: view === r.id ? T.soft : 'transparent' }}>{r.label}</button>
          ))}
        </nav>
        <div className="ml-auto flex items-center gap-1">
          <Klick label="Panel-Manager" aktiv={manager} onClick={() => setManager(m => !m)}><IconLayoutSidebar size={14} stroke={1.7} /></Klick>
          <Klick label="Layout" aktiv={manager} onClick={() => setManager(m => !m)}><IconLayoutGrid size={14} stroke={1.7} /></Klick>
          <Klick label="Einstellungen" aktiv={einstOffen} onClick={() => setEinstOffen(true)}><IconSettings size={14} stroke={1.7} /></Klick>
          <button type="button" onClick={() => setDark(d => !d)} aria-label={dark ? 'hell' : 'dunkel'} className="rounded px-2 py-0.5"
            style={{ fontSize: 11, border: `1px solid ${T.line}` }}>{dark ? 'hell' : 'dunkel'}</button>
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        <nav aria-label="Hauptnavigation" className="flex w-14 shrink-0 flex-col items-center gap-0.5 py-2" style={{ background: T.surface, borderRight: `1px solid ${T.line}` }}>
          {RAIL.map(r => (
            <button key={r.id} type="button" title={r.label} aria-label={r.label} aria-current={view === r.id} onClick={() => setView(r.id)}
              className="flex items-center justify-center rounded" style={{ width: 40, height: 34, border: `1px solid ${view === r.id ? T.line : 'transparent'}`, background: view === r.id ? T.soft : 'transparent' }}>
              <r.Icon size={16} stroke={1.8} color={view === r.id ? T.ink : T.faint} />
            </button>
          ))}
        </nav>

        {/* Sitzungen + Projektordner + angedocktes Subagenten-Panel — 260 px */}
        <aside className="flex w-[260px] shrink-0 flex-col" style={{ background: T.surface, borderRight: `1px solid ${T.line}` }}>
          <div className="flex items-center gap-1 p-2">
            <button type="button" onClick={neueSitzung} className="flex flex-1 items-center gap-1.5 rounded px-2 py-1"
              style={{ fontSize: 11.5, border: `1px solid ${T.line}`, background: T.soft }}>
              <IconPlus size={12} stroke={2} />Neue Sitzung
            </button>
            <Klick label="Alle Projekte ausklappen" onClick={() => { alleAuf(); melden('Alle Projekte ausgeklappt') }}><IconChevronDown size={13} stroke={1.8} /></Klick>
            <Klick label="Alle Projekte einklappen" onClick={() => { alleZu(); melden('Alle Projekte eingeklappt') }}><IconChevronRight size={13} stroke={1.8} /></Klick>
          </div>
          <div className="mx-2 mb-1.5 flex h-6 items-center gap-1.5 px-1" style={{ borderBottom: `1px solid ${T.line}` }}>
            <IconSearch size={12} stroke={1.8} color={T.faint} />
            <input value={suche} onChange={e => setSuche(e.target.value)} placeholder="Sitzungen durchsuchen" aria-label="Sitzungen durchsuchen"
              className="w-full bg-transparent outline-none" style={{ fontSize: 11, color: T.ink }} />
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto px-1">
            <p style={{ fontFamily: MONO, fontSize: 11, color: T.faint, padding: '4px 6px 2px' }}>PROJEKTE</p>
            {projekte.map(p => (
              <div key={p.id}>
                <button type="button" onClick={() => projektAuf(p.id)} aria-expanded={p.offen} aria-label={(p.offen ? 'Projekt einklappen: ' : 'Projekt ausklappen: ') + p.name}
                  className="flex w-full items-center gap-1.5 rounded px-1.5 py-1 text-left" style={{ fontSize: 11.5 }}>
                  {p.offen ? <IconChevronDown size={12} stroke={1.8} color={T.sub} /> : <IconChevronRight size={12} stroke={1.8} color={T.sub} />}
                  <IconFolderOpen size={13} stroke={1.8} color={T.sub} />
                  <span className="truncate" style={{ flex: 1 }}>{p.name}</span>
                  <span style={{ fontFamily: MONO, fontSize: 11, color: T.faint }}>
                    {p.sitzungen.length === 0 ? 'leer' : p.sitzungen.length}
                  </span>
                </button>
                {p.offen && (
                  p.sitzungen.length === 0
                    ? <p style={{ fontFamily: MONO, fontSize: 11, color: T.faint, padding: '2px 6px 4px 30px' }}>keine Sitzung in diesem Projekt</p>
                    : p.sitzungen.filter(s => s.titel.toLowerCase().includes(suche.toLowerCase())).map(s => <SitzungsKnopf key={s.id} s={s} />)
                )}
              </div>
            ))}
            <p style={{ fontFamily: MONO, fontSize: 11, color: T.faint, padding: '8px 6px 2px' }}>OHNE PROJEKT</p>
            {ohneProjekt.length === 0
              ? <p style={{ fontFamily: MONO, fontSize: 11, color: T.faint, padding: '2px 6px 4px' }}>noch keine Sitzung — „Neue Sitzung" anfangen</p>
              : ohneProjekt.filter(s => s.titel.toLowerCase().includes(suche.toLowerCase())).map(s => <SitzungsKnopf key={s.id} s={s} />)}
          </div>

          <div className="flex gap-1 px-2 py-1.5" style={{ borderTop: `1px solid ${T.hair}` }}>
            <Klick label="Verlauf exportieren" onClick={() => melden('Verlauf exportiert')}><IconDownload size={13} stroke={1.8} /></Klick>
            <Klick label="Verlauf importieren" onClick={() => melden('Verlauf importiert')}><IconUpload size={13} stroke={1.8} /></Klick>
            <Klick label="Sitzung loeschen" onClick={() => { if (aktiv) { setProjekte(p => p.map(x => ({ ...x, sitzungen: x.sitzungen.filter(y => y.id !== aktiv) }))); setOhneProjekt(l => l.filter(y => y.id !== aktiv)); setAktiv(null); melden('Sitzung geloescht') } else melden('Keine Sitzung gewaehlt') }}><IconTrash size={13} stroke={1.8} /></Klick>
          </div>

          {/* Subagenten-Ueberwachung — angedockt unten an der Session-Leiste */}
          {!subFrei && (
            <div className="p-2" style={{ borderTop: `1px solid ${T.line}` }}>
              <SubPanel />
            </div>
          )}
        </aside>

        <main className="flex min-w-0 flex-1 flex-col">
          <div className="flex min-h-0 flex-1">
            <div className="relative flex min-w-0 flex-1 flex-col">
              <div className="min-h-0 flex-1 overflow-y-auto" style={{ padding: 14 }}>
                {view === 'chat' && (
                  verlauf.length === 0
                    ? <LeererChat />
                    : (
                      <div className="flex flex-col gap-2">
                        {verlauf.map(m => (
                          <motion.p key={m.id} initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }}
                            className="ml-auto" style={{ maxWidth: '62%', background: T.ink, color: T.surface, borderRadius: 12, padding: '6px 10px', fontFamily: MONO, fontSize: 13 }}>{m.text}</motion.p>
                        ))}
                      </div>
                    )
                )}
                {view === 'dateien' && (
                  <div className="flex gap-3">
                    <div className="w-72 rounded-lg p-2" style={{ border: `1px solid ${T.line}`, background: T.surface }}>
                      {DATEIBAUM.map(p => (
                        <div key={p.ordner}>
                          <p className="flex items-center gap-1.5 py-0.5" style={{ fontSize: 11 }}><IconFolderOpen size={12} stroke={1.8} />{p.ordner}</p>
                          {p.dateien.map(d => (
                            <button key={d} type="button" onClick={() => { setDateiOffen(p.ordner + '/' + d); melden('Datei gewaehlt: ' + d) }}
                              className="flex w-full items-center gap-1.5 py-0.5 pl-5 text-left" style={{ fontFamily: MONO, fontSize: 11, color: dateiOffen === p.ordner + '/' + d ? T.ink : T.sub }}>
                              <IconScript size={11} stroke={1.8} />{d}
                            </button>
                          ))}
                        </div>
                      ))}
                    </div>
                    <div className="min-w-0 flex-1 rounded-lg p-3" style={{ border: `1px solid ${T.line}`, background: T.surface }}>
                      {dateiOffen
                        ? <><p style={{ fontFamily: MONO, fontSize: 11 }}>{dateiOffen}</p>
                            <Leerzeile text="Vorschau erscheint hier, sobald ein Modell oder Werkzeug die Datei liefert." /></>
                        : <Leerzeile text="keine Datei gewaehlt — links eine Datei anklicken." />}
                    </div>
                  </div>
                )}
                {view === 'projekte' && (
                  <div style={{ maxWidth: 620 }}>
                    <p style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>Projekte</p>
                    {projekte.map(p => (
                      <div key={p.id} className="py-1.5" style={{ borderBottom: `1px solid ${T.hair}` }}>
                        <button type="button" onClick={() => projektAuf(p.id)} className="flex w-full items-center gap-2 text-left" style={{ fontFamily: MONO, fontSize: 11 }}>
                          {p.offen ? <IconChevronDown size={12} stroke={1.8} /> : <IconChevronRight size={12} stroke={1.8} />}
                          <IconFolderOpen size={13} stroke={1.8} color={T.sub} />{p.name}
                          <span className="ml-auto" style={{ color: T.faint }}>{p.sitzungen.length} Sitzungen</span>
                        </button>
                        {p.offen && p.sitzungen.map(s => <SitzungsKnopf key={s.id} s={s} />)}
                      </div>
                    ))}
                  </div>
                )}
                {view === 'review' && <div style={{ maxWidth: 620 }}><p style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>Review</p>
                  <Leerzeile text="keine Aenderungen — sobald ein Werkzeug etwas bearbeitet, erscheinen hier die Diffs." /></div>}
                {view === 'terminal' && (
                  <div style={{ maxWidth: 720, border: `1px solid ${T.line}`, borderRadius: 12, background: T.surface, padding: 10 }}>
                    <Leerzeile text="Terminal — noch keine Ausgabe. Ein Befehl wird erst ausgefuehrt, wenn ein Lauf ihn startet." />
                    <div className="flex items-center gap-1.5" style={{ borderTop: `1px solid ${T.hair}`, paddingTop: 8 }}>
                      <span style={{ fontFamily: MONO, fontSize: 11, color: T.sub }}>$</span>
                      <input aria-label="Terminalbefehl" placeholder="Befehl hier eingeben (Mockup — wird nicht ausgefuehrt)"
                        className="flex-1 bg-transparent outline-none" style={{ fontFamily: MONO, fontSize: 11, color: T.ink }}
                        onKeyDown={e => { if (e.key === 'Enter' && e.currentTarget.value.trim()) { melden('Im Mockup wird nichts ausgefuehrt: ' + e.currentTarget.value); e.currentTarget.value = '' } }} />
                    </div>
                  </div>
                )}
                {view === 'jobs' && (
                  <div style={{ maxWidth: 620 }}>
                    <div className="mb-2 flex items-center gap-2">
                      <p style={{ fontSize: 12, fontWeight: 600 }}>Geplante Aufgaben</p>
                      <button type="button" onClick={() => melden('Aufgabe anlegen — Formular folgt')} className="ml-auto flex items-center gap-1 rounded px-2 py-0.5"
                        style={{ fontSize: 11, border: `1px solid ${T.line}` }}><IconPlus size={12} stroke={2} />neu</button>
                    </div>
                    <Leerzeile text="keine Aufgabe geplant — Zeitplaene legt man hier an, wenn der Agent regelmaessig arbeiten soll." />
                  </div>
                )}
              </div>

              {/* Kapsel ueber der Chat-Bar — Abstand gemessen, keine Ueberlappung */}
              {panelAn.kontext && (
                <div className="absolute z-10" style={{ bottom: 150, right: 16 }}>
                  <KontextKapsel onSchliessen={() => { setPanelAn(p => ({ ...p, kontext: false })); melden('Kontext-Kapsel eingeklappt') }} />
                </div>
              )}
              {/* Goal-Bar ueber dem Chat: leer zeigt sie den @goal-Satz (eingeklappt wie ausgeklappt) */}
              <div className="px-3.5 pb-3">
                <ChatBarApp ziel={{ titel: ZIEL_LEER, kriterien: [] }} onSenden={senden} onDatei={() => melden('Datei anhaengen — Auswahl folgt')} />
              </div>
            </div>

            {panelAn.rechts && (
              <aside className="w-72 shrink-0 overflow-y-auto p-2.5" style={{ background: T.surface, borderLeft: `1px solid ${T.line}` }}>
                <h2 style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>Uebersicht</h2>
                {[['Ziel', 'kein Ziel gesetzt'], ['Sitzung', aktivTitel ?? 'keine'], ['Projekt', projekte.find(p => p.sitzungen.some(s => s.id === aktiv))?.name ?? 'ohne Projekt'], ['Nachweise', 'keine'], ['Werkzeuge', 'keine verbunden']].map(([k, v]) => (
                  <div key={k} className="flex gap-2 py-1" style={{ borderBottom: `1px solid ${T.hair}` }}>
                    <span style={{ fontFamily: MONO, fontSize: 11, color: T.faint, minWidth: 82 }}>{k}</span>
                    <span style={{ fontFamily: MONO, fontSize: 11 }}>{v}</span>
                  </div>
                ))}
                <button type="button" onClick={() => melden('Bericht braucht einen Lauf')} className="mt-3 flex items-center gap-1.5 rounded px-2 py-1"
                  style={{ fontSize: 11, border: `1px solid ${T.line}` }}><IconCheck size={12} stroke={2} />Bericht</button>
              </aside>
            )}
          </div>

          <footer className="flex shrink-0 items-center gap-3.5 px-3" style={{ height: 26, background: T.surface, borderTop: `1px solid ${T.line}`, fontFamily: MONO, fontSize: 11, color: T.sub }}>
            <span className="flex items-center gap-1.5"><span style={{ width: 6, height: 6, borderRadius: 999, background: T.faint }} />bereit</span>
            <span>kein Modell gewaehlt</span>
            <span>kein Lauf</span>
            <span className="ml-auto">{subFrei ? 'Subagenten frei' : 'Subagenten an der Leiste'} · {panelAn.kontext ? 'Kontext sichtbar' : 'Kontext eingeklappt'}</span>
          </footer>
        </main>
      </div>

      {/* Frei schwebendes Subagenten-Panel */}
      {subFrei && (
        <div ref={boxRef} className="fixed z-40 rounded-lg p-2"
          style={{ left: 0, top: 0, transform: `translate3d(${subPos.x}px, ${subPos.y}px, 0)`, willChange: 'transform',
                   width: 244, background: T.surface, border: `1px solid ${T.line}`, boxShadow: '0 14px 34px rgba(0,0,0,0.24)' }}>
          <SubPanel />
        </div>
      )}

      {manager && (
        <div data-slot="panel-manager" className="fixed bottom-10 right-4 z-50 w-64 rounded-lg" style={{ background: T.surface, border: `1px solid ${T.line}`, boxShadow: '0 14px 34px rgba(0,0,0,0.24)' }}>
          <p className="px-2.5 py-1.5" style={{ fontFamily: MONO, fontSize: 11, color: T.sub, borderBottom: `1px solid ${T.line}` }}>
            PANELS · {Object.values(panelAn).filter(Boolean).length + (subFrei ? 0 : 1)} VON 3 AN
          </p>
          <div className="p-1.5">
            {[['kontext', 'Kontext-Kapsel'], ['rechts', 'Uebersicht rechts']].map(([id, label]) => (
              <label key={id} className="flex items-center gap-2 px-1.5 py-1" style={{ fontSize: 11 }}>
                <input type="checkbox" checked={!!panelAn[id]} onChange={() => { setPanelAn(p => ({ ...p, [id]: !p[id] })); melden(label + ': ' + (!panelAn[id] ? 'an' : 'aus')) }} />
                <span className="flex-1">{label}</span>
              </label>
            ))}
            <label className="flex items-center gap-2 px-1.5 py-1" style={{ fontSize: 11 }}>
              <input type="checkbox" checked={!subFrei} onChange={() => setSubFrei(f => !f)} />
              <span className="flex-1">Subagenten an der Leiste</span>
            </label>
            <p className="px-1.5 pt-1.5" style={{ fontFamily: MONO, fontSize: 11, color: T.faint, borderTop: `1px solid ${T.line}`, marginTop: 4 }}>
              IN DER STATUSZEILE ZEIGEN · {STATUSBAR_ZEILEN.filter(z => !z.aus).length} VON {STATUSBAR_ZEILEN.length} AN
            </p>
            {STATUSBAR_ZEILEN.map(z => (
              <label key={z.id} className="flex items-center gap-2 px-1.5 py-1" style={{ fontSize: 11, opacity: z.gesperrt ? 0.55 : 1 }}>
                <input type="checkbox" defaultChecked={!z.aus} disabled={z.gesperrt} aria-disabled={z.gesperrt || undefined}
                  onChange={() => melden(z.wort + ': ' + (z.aus ? 'an' : 'aus'))} />
                <span className="flex-1">{z.wort}</span>
              </label>
            ))}
            <p className="px-1.5 pt-1" style={{ fontFamily: MONO, fontSize: 11, color: T.faint }}>
              Subagenten-Fenster und Computer-Use erscheinen erst, wenn sie wirklich laufen.
            </p>
          </div>
        </div>
      )}

      <AnimatePresence>
        {einstOffen && (
          <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="fixed inset-0 z-50 grid place-items-center bg-black/25">
            <motion.div initial={{ scale: 0.97 }} animate={{ scale: 1 }} className="w-[720px] overflow-hidden rounded-xl"
              style={{ background: T.surface, border: `1px solid ${T.line}`, boxShadow: '0 22px 60px rgba(0,0,0,0.34)' }}>
              <div className="flex items-center gap-2 px-3.5 py-3" style={{ borderBottom: `1px solid ${T.hair}` }}>
                <h2 style={{ fontSize: 15, fontWeight: 600 }}>Einstellungen</h2>
                <span className="ml-2 flex h-6 items-center gap-1.5 px-2" style={{ border: `1px solid ${T.line}`, borderRadius: 999 }}>
                  <IconSearch size={12} stroke={1.8} color={T.faint} />
                  <input value={einstSuche} onChange={e => setEinstSuche(e.target.value)} aria-label="Einstellungen durchsuchen" placeholder="Einstellungen durchsuchen"
                    className="w-44 bg-transparent outline-none" style={{ fontSize: 11, color: T.ink }} />
                </span>
                <button type="button" aria-label="schliessen" onClick={() => setEinstOffen(false)} className="ml-auto rounded"
                  style={{ width: 24, height: 24, border: `1px solid ${T.line}` }}><IconX size={13} stroke={2} /></button>
              </div>
              <div className="flex gap-3.5 p-3.5">
                <div className="w-48 shrink-0 overflow-y-auto" role="tablist" aria-label="Bereiche" style={{ maxHeight: 340 }}>
                  {EINSTELLUNGEN.filter(b => b.label.toLowerCase().includes(einstSuche.toLowerCase())).map(b => (
                    <div key={b.id}>
                      <button type="button" role="tab" aria-selected={einst === b.id}
                        onClick={() => { setEinst(b.id); setUnter(0); if (b.id === 'mitteilungen') melden('Mitteilungen: alle Arten an (Vorgabe)') }}
                        className="block w-full rounded px-2 py-1 text-left"
                        style={{ fontSize: 11, background: einst === b.id ? T.soft : 'transparent', fontWeight: einst === b.id ? 600 : 400, color: einst === b.id ? T.ink : T.sub }}>{b.label}</button>
                      {einst === b.id && 'unter' in b && (b as any).unter.map((u: string, i: number) => (
                        <button key={u} type="button" role="tab" aria-selected={unter === i} onClick={() => setUnter(i)}
                          className="block w-full rounded py-0.5 pl-6 pr-2 text-left" style={{ fontSize: 11, color: unter === i ? T.ink : T.faint }}>{u}</button>
                      ))}
                    </div>
                  ))}
                </div>
                <div className="min-w-0 flex-1" style={{ fontSize: 11, maxHeight: 340, overflowY: 'auto' }}>
                  {(EINSTELLUNGEN.find(b => b.id === einst) as any)?.unter && (
                    <p style={{ fontFamily: MONO, fontSize: 11, color: T.faint, marginBottom: 8 }}>
                      {(EINSTELLUNGEN.find(b => b.id === einst) as any).unter[unter]}
                    </p>
                  )}
                  {einst === 'darstellung' ? (
                    <>
                      <p style={{ fontFamily: MONO, fontSize: 11, color: T.faint, marginBottom: 6 }}>Helligkeit</p>
                      <button type="button" onClick={() => setDark(d => !d)} className="mb-3 rounded px-2 py-1" style={{ fontSize: 11, border: `1px solid ${T.line}` }}>
                        {dark ? 'dunkel → hell' : 'hell → dunkel'}
                      </button>
                      <p style={{ fontFamily: MONO, fontSize: 11, color: T.faint, marginBottom: 6 }}>Schrift</p>
                      <select value={schrift} onChange={e => setSchrift(e.target.value)} aria-label="Schrift"
                        className="mb-3 rounded bg-transparent px-2 py-1" style={{ fontSize: 11, border: `1px solid ${T.line}`, color: T.ink }}>
                        {['Geist Sans', 'Geist Mono'].map(f => <option key={f} value={f}>{f}</option>)}
                      </select>
                      <p style={{ fontFamily: MONO, fontSize: 11, color: T.faint, marginBottom: 6 }}>Oberflaeche</p>
                      <select value={skala} onChange={e => setSkala(e.target.value)} aria-label="Oberflaechengroesse"
                        className="rounded bg-transparent px-2 py-1" style={{ fontSize: 11, border: `1px solid ${T.line}`, color: T.ink }}>
                        {['90 %', '100 %', '110 %', '125 %', '150 %'].map(v => <option key={v} value={v}>{v}</option>)}
                      </select>
                    </>
                  ) : einst === 'mitteilungen' ? (
                    <>
                      {[['hintergrund', 'nur im Hintergrund'], ['fertig', 'Fertig-Meldungen'], ['fehler', 'Fehler'], ['freigabe', 'Freigaben']].map(([k, l]) => (
                        <label key={k} className="flex items-center gap-2 py-1" style={{ fontSize: 11 }}>
                          <input type="checkbox" checked={(mitteilungen as any)[k]} onChange={() => setMitteilungen(m => ({ ...m, [k]: !(m as any)[k] }))} />
                          <span>{l}</span>
                        </label>
                      ))}
                      <button type="button" onClick={() => melden('Testhinweis ausgeloest')} className="mt-2 rounded px-2 py-1" style={{ fontSize: 11, border: `1px solid ${T.line}` }}>Testhinweis</button>
                    </>
                  ) : einst === 'kuerzel' ? (
                    <table style={{ fontFamily: MONO, fontSize: 11, borderCollapse: 'collapse', width: '100%' }}>
                      <tbody>
                        {[['Befehlspalette', '⌘K'], ['senden', '⏎'], ['neue Sitzung', '⌘N'], ['abbrechen', 'Esc'], ['Spalten tauschen', '⌘\\'], ['einklappen', '⌘B']].map(([a, k]) => (
                          <tr key={a}><td style={{ padding: '3px 8px 3px 0', color: T.sub }}>{a}</td><td style={{ padding: '3px 0' }}>{k}</td></tr>
                        ))}
                      </tbody>
                    </table>
                  ) : einst === 'ueber' ? (
                    <>
                      <Leerzeile text="Skaldir Agent · Oberflaeche v1.4.0 · Tailwind 4 · Radix · Tabler · Geist · Motion" />
                      <div className="flex gap-1.5">
                        <button type="button" onClick={() => melden('Update pruefen braucht eine Verbindung')} className="rounded px-2 py-1" style={{ fontSize: 11, border: `1px solid ${T.line}` }}>Update pruefen</button>
                        <button type="button" onClick={() => melden('Protokolle liegen unter ~/.hermes/logs')} className="rounded px-2 py-1" style={{ fontSize: 11, border: `1px solid ${T.line}` }}>Protokolle</button>
                      </div>
                    </>
                  ) : (
                    <Leerzeile text={(EINSTELLUNGEN.find(b => b.id === einst) as any)?.text ?? ''} />
                  )}
                </div>
              </div>
              <div className="flex items-center gap-2 px-3.5 py-2.5" style={{ borderTop: `1px solid ${T.hair}`, fontFamily: MONO, fontSize: 11, color: T.faint }}>
                <span>{EINSTELLUNGEN.length} Bereiche</span>
                <span className="ml-auto">Aenderungen gelten fuer die naechste Nachricht</span>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>

      <div className="pointer-events-none fixed bottom-9 left-4 z-[60] flex w-80 flex-col gap-2">
        {hinweis.map(h => (
          <motion.output key={h.id} initial={{ opacity: 0, x: -16 }} animate={{ opacity: 1, x: 0 }}
            className="rounded-lg px-2.5 py-2" style={{ background: T.surface, border: `1px solid ${T.line}`, fontSize: 11, boxShadow: '0 10px 26px rgba(0,0,0,0.18)' }}>{h.t}</motion.output>
        ))}
      </div>
    </div>
  )
}
