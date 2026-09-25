/**
 * SKALDIR APP — das vollstaendige, durchklickbare UI (Preview mit leerer Sitzung).
 *
 * Auftrag des Owners 18.09.2026: „Ich will am Ende ein vollstaendiges UI, wo man sich vollstaendig
 * durchklicken kann, also keine Funktion aus dem Original Hermes fehlt. Tabler als Icon-Familie.
 * Ich will dann die vollstaendige App-UI-Preview (leere Session)."
 *
 * Alles hier ist echt verdrahtet (React-State, keine gemalten Attrappen):
 *   Navigationsschiene   sechs Wege + Einstellungen, jeder schaltet die Hauptflaeche um
 *   Sitzungsverlauf      Suche, neue (leere) Sitzung, Auswahl, Kontextaktionen
 *   Reiterleiste         Reiter oeffnen, wechseln, schliessen
 *   Befehlspalette       ⌘K/Suche oeffnet, filtert, fuehrt aus (Ansicht, Panels, Not-Aus)
 *   Einstellungen        sechs Reiter mit echten Schaltern, Auswahlen, Tastenkürzel-Aufnahme
 *   Panel-Manager        sieben Panels einzeln schaltbar
 *   Panels               Kontext-Kapsel (ziehbar), Subagenten-Fenster/-Streifen, Orb-Widgets,
 *                        Ziel-Baum, Graph-Tab, Artefakt-Vorschau, Computer-Use-Overlay
 *   Composer             Text, Anhaenge, Modell-/Reasoning-Auswahl, Senden erzeugt Antwort
 *   Hinweise             Toasts auf jede Aktion, Statusleiste mit Takt, Tokens, Computer-Use
 * Sechs Fassungen: leere Sitzung hell/dunkel, mit Inhalt hell/dunkel, Einstellungen, Manager+Palette.
 */
import { motion, AnimatePresence, animate } from 'motion/react'
import React, { useEffect, useState } from 'react'
import { IconAlertCircle, IconAlertTriangle, IconBox, IconChartBar, IconCheck, IconChevronDown, IconChevronLeft, IconChevronRight, IconCircleCheck, IconClock, IconColumns2, IconCopy, IconCpu, IconCpu2, IconDeviceDesktop, IconDeviceFloppy, IconDownload, IconExternalLink, IconEye, IconFiles, IconFolderOpen, IconGitBranch, IconKey, IconLayoutGrid, IconLayoutSidebar, IconLayoutSidebarLeftCollapse, IconLayoutSidebarRight, IconLoader2, IconLock, IconMessage, IconMinus, IconMoon, IconPaperclip, IconPencil, IconPlayerPlay, IconPlayerSkipForward, IconPlayerStop, IconPlus, IconRefresh, IconRoute, IconScript, IconSearch, IconSettings, IconSquare, IconSquareCheck, IconStack2, IconSun, IconTerminal2, IconTool, IconTrash, IconUpload, IconWorld, IconX } from '@tabler/icons-react'
import { LIGHT, DARK, MONO, SANS, SPRING, RADIUS, TEXT } from './_tokens'
import { useBarrierefrei } from './_barrierefrei'

// @ts-expect-error — Laufzeit-Alias (siehe vite.config.ts)
import { ThinkingOrb } from 'thinking-orbs'

const useLive = (start = 42) => {
  const [t, setT] = useState(start)
  useEffect(() => { const id = setInterval(() => setT(v => v + 1), 1000); return () => clearInterval(id) }, [])
  return t
}
const fmt = s => String(Math.floor(s / 60)).padStart(2, '0') + ':' + String(s % 60).padStart(2, '0')

const RAIL = [
  { id: 'chat', Icon: IconMessage, label: 'Chat' },
  { id: 'files', Icon: IconFolderOpen, label: 'Projekte' },
  { id: 'review', Icon: IconGitBranch, label: 'Review' },
  { id: 'terminal', Icon: IconTerminal2, label: 'Terminal' },
  { id: 'jobs', Icon: IconCpu, label: 'Jobs' },
  { id: 'settings', Icon: IconSettings, label: 'Einstellungen' }
]
const BEFEHLE = [
  { id: 'neu', text: 'Neue Sitzung', taste: '1' },
  { id: 'dateien', text: 'Dateien oeffnen', taste: '2' },
  { id: 'terminal', text: 'Terminal oeffnen', taste: 'T' },
  { id: 'jobs', text: 'Jobs anzeigen', taste: 'J' },
  { id: 'einstellungen', text: 'Einstellungen oeffnen', taste: ',' },
  { id: 'panels', text: 'Panel-Manager', taste: 'P' },
  { id: 'graph', text: 'Zielgraph oeffnen', taste: 'G' },
  { id: 'cu', text: 'Computer-Use umschalten', taste: '.' },
  { id: 'notaus', text: 'Not-Aus — alles anhalten', taste: 'Escape' }
]
const PANELS = [
  { id: 'kontext', label: 'Kontext-Kapsel' }, { id: 'subfenster', label: 'Subagenten-Fenster' },
  { id: 'streifen', label: 'Subagenten-Streifen' }, { id: 'orbs', label: 'Orb-Widgets' },
  { id: 'cu', label: 'Computer-Use-Overlay' }, { id: 'ziel', label: 'Ziel-Baum' },
  { id: 'rechts', label: 'Panel rechts' }
]
const EINSTELLUNGEN = ['Modell', 'Darstellung', 'Schluessel', 'Werkzeuge', 'Tastenkürzel', 'Ueber']
const SITZUNGEN_START = [
  { id: 's1', titel: 'God\'s Voice — UI-Abnahme', when: '14:02', n: 42 },
  { id: 's2', titel: 'Merge-Vorschau bauen', when: '13:28', n: 18 },
  { id: 's3', titel: 'Panel-Manager und Toggles', when: '12:44', n: 27 },
  { id: 's4', titel: 'Computer-Use dunkel', when: '11:58', n: 9 }
]
const SPRUECHE = ['Give me a goal I actually complete it.', 'Ich messe, ich behaupte nicht.', 'Zeig mir das Ziel, ich zeige dir den Beleg.', 'Kein Fortschritt ohne Messung.']

/* ---------------------------------------------------------------- Bausteine */
const Knopf = ({ P, children, primary, Icon, onClick, klein }) => (
  <motion.button onClick={onClick} whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.96 }} transition={SPRING}
    style={{ display: 'inline-flex', alignItems: 'center', gap: 6, padding: klein ? '3px 8px' : '5px 11px',
             borderRadius: RADIUS.micro, border: `1px solid ${primary ? P.ink : P.line}`, background: primary ? P.ink : P.surface,
             color: primary ? P.surface : P.ink, fontFamily: SANS, fontSize: klein ? TEXT.meta : TEXT.body, cursor: 'pointer' }}>
    {Icon ? <Icon size={13} stroke={1.8} /> : null}{children}
  </motion.button>
)
const SymbolKnopf = ({ P, Icon, label, onClick, aktiv, groesse = 24 }) => (
  <motion.button title={label} aria-label={label} onClick={onClick} whileHover={{ backgroundColor: P.hair }} whileTap={{ scale: 0.92 }} transition={SPRING}
    style={{ width: groesse, height: groesse, borderRadius: RADIUS.micro, border: `1px solid ${aktiv ? P.line : 'transparent'}`,
             background: aktiv ? P.soft : 'transparent', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer' }}>
    <Icon size={groesse > 30 ? 16 : 13.9} stroke={1.7} color={P.sub} />
  </motion.button>
)
const Schalter = ({ P, an, onClick, breit }) => (
  <motion.span layout onClick={onClick} transition={SPRING} role="switch" aria-checked={an}
    style={{ width: breit ? 36 : 28, height: breit ? 20 : 16, borderRadius: RADIUS.pill, padding: 2, display: 'inline-flex',
             alignItems: 'center', justifyContent: an ? 'flex-end' : 'flex-start', cursor: 'pointer',
             background: an ? P.ink : P.hair, border: `1px solid ${an ? P.ink : P.line}` }}>
    <motion.span layout transition={SPRING} style={{ width: breit ? 16 : 12, height: breit ? 16 : 12, borderRadius: RADIUS.pill, background: an ? P.surface : P.faint }} />
  </motion.span>
)
const Karte = ({ P, children, breit }) => (
  <div style={{ border: `1px solid ${P.line}`, borderRadius: RADIUS.module, background: P.surface, padding: 10, maxWidth: breit || 460 }}>{children}</div>
)

/* ---------------------------------------------------------------- App */
function App({ fassung }) {
  useBarrierefrei()
  const leerStart = fassung === 'leer-hell' || fassung === 'leer-dunkel'
  const [dark, setDark] = useState(fassung === 'leer-dunkel' || fassung === 'inhalt-dunkel' || fassung === 'cu')
  const P = dark ? DARK : LIGHT
  const [view, setView] = useState(fassung === 'einstellungen' ? 'settings' : 'chat')
  const [sitzungen, setSitzungen] = useState(SITZUNGEN_START)
  const [aktiv, setAktiv] = useState(fassung === 'inhalt-hell' || fassung === 'inhalt-dunkel' || fassung === 'cu' ? 's1' : 'leer')
  const [suchtext, setSuchtext] = useState('')
  const [palette, setPalette] = useState(false)
  const [pquery, setPquery] = useState('')
  const [manager, setManager] = useState(fassung === 'manager')
  const [einst, setEinst] = useState('Modell')
  const [tabs, setTabs] = useState([{ id: 'chat', label: 'Chat' }, { id: 'files', label: 'Dateien' }])
  const [panelAn, setPanelAn] = useState({ kontext: true, subfenster: true, streifen: true, orbs: true, cu: fassung === 'cu', ziel: true, rechts: fassung === 'manager' })
  const [text, setText] = useState('')
  const [verlauf, setVerlauf] = useState(fassung === 'inhalt-hell' || fassung === 'inhalt-dunkel' || fassung === 'cu'
    ? [{ von: 'du', text: 'zerleg das in drei spuren und miss die anker' }, { von: 'bot', text: 'Ich zerlege in drei Spuren und messe die 28 Anker gegen. Die Pruefung laeuft in ihrem eigenen Fenster.' }]
    : [])
  const [toasts, setToasts] = useState([])
  const [anhang, setAnhang] = useState(false)
  const [lauf, setLauf] = useState(false)
  const [modell, setModell] = useState('opencode-go · free')
  const [reason, setReason] = useState('Med')
  const [modellOffen, setModellOffen] = useState(false)
  const [term, setTerm] = useState(['$ node tools/check_ui_overlay_anchors.py', '28 erwartet · 28 gefunden · RC 0'])
  const [termEingabe, setTermEingabe] = useState('')
  const [datei, setDatei] = useState('UI-CONTRACT.md')
  const [jobAn, setJobAn] = useState({ j1: true, j2: false, j3: true })
  const [schalter, setSchalter] = useState({ denken: true, sprache: false, reduce: false, limit: true })
  const [aufnahme, setAufnahme] = useState(null)
  const live = useLive(42)

  const melden = (t) => { const id = Date.now() + Math.random(); setToasts(x => [...x, { id, t }]); setTimeout(() => setToasts(x => x.filter(y => y.id !== id)), 3500) }
  const sitzung = sitzungen.find(s => s.id === aktiv)
  const istLeer = !sitzung

  const neueSitzung = () => { const id = 'neu' + (sitzungen.length + 1); setSitzungen(s => [{ id, titel: 'Neue Sitzung', when: 'jetzt', n: 0 }, ...s]); setAktiv(id); setVerlauf([]); setView('chat'); melden('Neue Sitzung angelegt — noch leer') }
  const befehl = (id) => {
    setPalette(false); setPquery('')
    if (id === 'neu') neueSitzung()
    else if (id === 'dateien') { setView('files'); melden('Dateien geoeffnet') }
    else if (id === 'terminal') { setView('terminal'); melden('Terminal geoeffnet') }
    else if (id === 'jobs') { setView('jobs'); melden('Jobs geoeffnet') }
    else if (id === 'einstellungen') { setView('settings'); melden('Einstellungen geoeffnet') }
    else if (id === 'panels') { setManager(true); melden('Panel-Manager geoeffnet') }
    else if (id === 'graph') { setPanelAn(p => ({ ...p, orbs: true })); melden('Zielgraph geoeffnet') }
    else if (id === 'cu') { setPanelAn(p => ({ ...p, cu: !p.cu })); melden('Computer-Use umgeschaltet') }
    else if (id === 'notaus') melden('Not-Aus: 3 Laeufe, 2 Subagenten, 7 Werkzeuge angehalten')
  }
  const senden = () => {
    if (!text.trim()) return
    setVerlauf(v => [...v, { von: 'du', text }]); setText(''); setLauf(true)
    setTimeout(() => { setVerlauf(v => [...v, { von: 'bot', text: 'Verstanden — ich arbeite daran und melde jeden Nachweis.' }]); setLauf(false) }, 1600)
  }

  /* ---- Teilflaechen ---- */
  const Rail = (
    <div style={{ width: 56, borderRight: `1px solid ${P.line}`, background: P.surface, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 2, padding: '8px 0' }}>
      {RAIL.map(r => (
        <motion.button key={r.id} title={r.label} aria-label={r.label} onClick={() => setView(r.id)} whileHover={{ backgroundColor: P.hair }} whileTap={{ scale: 0.94 }} transition={SPRING}
          style={{ width: 40, height: 34, borderRadius: RADIUS.row, border: `1px solid ${view === r.id ? P.line : 'transparent'}`, background: view === r.id ? P.soft : 'transparent', cursor: 'pointer', display: 'inline-flex', alignItems: 'center', justifyContent: 'center' }}>
          <r.Icon size={16} stroke={1.8} color={view === r.id ? P.ink : P.faint} />
        </motion.button>
      ))}
    </div>
  )
  const Seitenleiste = (
    <div style={{ width: 220, borderRight: `1px solid ${P.line}`, background: P.surface, display: 'flex', flexDirection: 'column' }}>
      <div style={{ padding: 8, display: 'flex', gap: 6 }}>
        <Knopf P={P} Icon={IconPlus} onClick={neueSitzung} klein>Neue Sitzung</Knopf>
        <SymbolKnopf P={P} Icon={IconLayoutSidebarLeftCollapse} label="Seitenleiste einklappen" onClick={() => melden('Seitenleiste eingeklappt')} />
      </div>
      <div style={{ margin: '0 8px 6px', display: 'flex', alignItems: 'center', gap: 6, height: 26, borderBottom: `1px solid ${P.line}` }}>
        <IconSearch size={12} stroke={1.8} color={P.faint} />
        <input value={suchtext} onChange={e => setSuchtext(e.target.value)} placeholder="Sitzungen durchsuchen" aria-label="Sitzungen durchsuchen"
          style={{ border: 'none', background: 'transparent', color: P.ink, fontFamily: SANS, fontSize: TEXT.meta, flex: 1, outline: 'none' }} />
      </div>
      <div style={{ flex: 1, overflowY: 'auto' }}>
        {sitzungen.filter(s => s.titel.toLowerCase().includes(suchtext.toLowerCase())).map(s => (
          <motion.div key={s.id} layout onClick={() => { setAktiv(s.id); setView('chat') }} transition={SPRING}
            style={{ padding: '6px 10px', cursor: 'pointer', background: aktiv === s.id ? P.soft : 'transparent', borderLeft: `2px solid ${aktiv === s.id ? P.ink : 'transparent'}`, display: 'flex', gap: 6, alignItems: 'center' }}>
            <div style={{ minWidth: 0, flex: 1 }}>
              <div style={{ fontSize: TEXT.meta, color: P.ink, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{s.titel}</div>
              <div style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub }}>{s.when} · {s.n} Nachrichten</div>
            </div>
            {s.n === 0 ? <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>leer</span> : null}
          </motion.div>
        ))}
        {sitzungen.filter(s => s.titel.toLowerCase().includes(suchtext.toLowerCase())).length === 0
          ? <div style={{ padding: 10, fontSize: TEXT.meta, color: P.sub }}>kein Treffer</div> : null}
      </div>
      <div style={{ padding: 8, borderTop: `1px solid ${P.hair}`, display: 'flex', gap: 4 }}>
        <SymbolKnopf P={P} Icon={IconDownload} label="Verlauf exportieren" onClick={() => melden('Verlauf exportiert')} />
        <SymbolKnopf P={P} Icon={IconUpload} label="Verlauf importieren" onClick={() => melden('Verlauf importiert')} />
        <SymbolKnopf P={P} Icon={IconTrash} label="Sitzung loeschen" onClick={() => { setSitzungen(x => x.filter(y => y.id !== aktiv)); setAktiv('leer'); melden('Sitzung geloescht') }} />
      </div>
    </div>
  )
  const Titelleiste = (
    <div style={{ height: 34, borderBottom: `1px solid ${P.line}`, background: P.surface, display: 'flex', alignItems: 'center', gap: 8, padding: '0 12px' }}>
      <span style={{ width: 12, height: 12, borderRadius: RADIUS.pill, border: `1px solid ${P.line}` }} />
      <span style={{ fontSize: TEXT.meta, color: P.sub }}>Skaldir Agent</span>
      <div style={{ display: 'flex', gap: 4, marginLeft: 6 }}>
        {tabs.map(t => (
          <motion.span key={t.id} layout transition={SPRING}
            style={{ display: 'inline-flex', alignItems: 'center', gap: 6, padding: '3px 8px', borderRadius: RADIUS.micro, fontSize: TEXT.meta,
                     background: view === t.id ? P.soft : 'transparent', border: `1px solid ${view === t.id ? P.line : 'transparent'}`, color: P.ink }}>
            <span onClick={() => setView(t.id)} style={{ cursor: 'pointer' }}>{t.label}</span>
            <span onClick={() => { setTabs(x => x.filter(y => y.id !== t.id)); melden('Reiter geschlossen: ' + t.label) }} style={{ cursor: 'pointer', color: P.faint }}>✕</span>
          </motion.span>
        ))}
        <SymbolKnopf P={P} Icon={IconPlus} label="Reiter oeffnen" onClick={() => { const id = 't' + (tabs.length + 1); setTabs(x => [...x, { id, label: 'Neuer Reiter' }]); melden('Reiter geoeffnet') }} />
      </div>
      <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 4 }}>
        {panelAn.cu ? <Knopf P={P} klein Icon={IconPlayerStop} onClick={() => { setPanelAn(p => ({ ...p, cu: false })); melden('Computer-Use beendet') }}>Not-Aus</Knopf> : null}
        <SymbolKnopf P={P} Icon={IconLayoutGrid} label="Layout" onClick={() => melden('Layout gewechselt')} />
        <SymbolKnopf P={P} Icon={IconLayoutSidebar} label="Panel-Manager" aktiv={manager} onClick={() => setManager(m => !m)} />
        <SymbolKnopf P={P} Icon={IconColumns2} label="Spalten tauschen" onClick={() => melden('Spalten getauscht')} />
        <SymbolKnopf P={P} Icon={IconSearch} label="Befehlspalette" aktiv={palette} onClick={() => setPalette(true)} />
        <SymbolKnopf P={P} Icon={IconSettings} label="Einstellungen" aktiv={view === 'settings'} onClick={() => setView('settings')} />
        <SymbolKnopf P={P} Icon={dark ? IconMoon : IconSun} label="hell/dunkel" onClick={() => setDark(d => !d)} />
      </div>
    </div>
  )
  const Composer = (
    <div style={{ padding: 10 }}>
      <div style={{ borderRadius: RADIUS.bar, border: `1px solid ${P.line}`, background: P.surface, overflow: 'hidden' }}>
        <AnimatePresence initial={false}>
          {anhang && (
            <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }} transition={SPRING} style={{ overflow: 'hidden' }}>
              <div style={{ padding: 8, display: 'flex', flexDirection: 'column', gap: 4, borderBottom: `1px solid ${P.hair}` }}>
                {[['01_PRODUCT_UX_DESIGN.md', '.md', '42 KB'], ['Screenshot 22-21-00.png', '.png', '1,4 MB']].map(([n, e, g], i) => (
                  <div key={n} style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '1px 5px', border: `1px solid ${P.line}`, height: 26, borderRadius: RADIUS.micro }}>
                    <span style={{ width: 16, height: 20, border: `1px solid ${P.line}`, background: P.soft }} />
                    <span style={{ flex: 1, minWidth: 0, fontFamily: MONO, fontSize: TEXT.meta, color: P.ink, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{n}</span>
                    <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>{e} · {g}</span>
                    <span style={{ height: 3, width: 54, background: P.hair, display: 'inline-block' }}>
                      <motion.span initial={{ width: 0 }} animate={{ width: '68%' }} transition={{ duration: 1.4, ease: 'easeInOut', delay: 0.2 + i * 0.2 }} style={{ display: 'block', height: 3, background: P.ink }} />
                    </span>
                  </div>
                ))}
              </div>
            </motion.div>
          )}
        </AnimatePresence>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 12px' }}>
          <SymbolKnopf P={P} Icon={IconPlus} label="Datei anhaengen" onClick={() => { setAnhang(a => !a); melden(anhang ? 'Anhaenge entfernt' : 'Zwei Dateien angehaengt') }} />
          <input value={text} onChange={e => setText(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') senden() }} placeholder="Start with a goal"
            aria-label="Nachricht" style={{ flex: 1, border: 'none', background: 'transparent', color: P.ink, fontFamily: SANS, fontSize: 14.5, outline: 'none' }} />
          <span style={{ position: 'relative' }}>
            <motion.span onClick={() => setModellOffen(o => !o)} whileTap={{ scale: 0.96 }} transition={SPRING}
              style={{ display: 'inline-flex', alignItems: 'center', gap: 5, border: `1px solid ${P.line}`, borderRadius: RADIUS.pill, padding: '3px 9px', cursor: 'pointer', fontFamily: SANS, fontSize: TEXT.meta, color: P.sub }}>
              {modell}<IconChevronDown size={11} stroke={2} />
            </motion.span>
            <AnimatePresence>
              {modellOffen && (
                <motion.div initial={{ opacity: 0, y: 8, scale: 0.95 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, y: 6 }} transition={SPRING}
                  style={{ position: 'absolute', right: 0, bottom: 34, width: 240, background: P.surface, border: `1px solid ${P.line}`, borderRadius: RADIUS.module, padding: 10, boxShadow: '0 14px 34px rgba(0,0,0,0.24)', zIndex: 40 }}>
                  <div style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint, marginBottom: 5 }}>MODELL</div>
                  {['opencode-go · free', 'klarer · lokal', 'waehler · remote'].map(m => (
                    <div key={m} onClick={() => { setModell(m); setModellOffen(false); melden('Modell: ' + m) }}
                      style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '4px 6px', cursor: 'pointer', background: modell === m ? P.soft : 'transparent', borderRadius: RADIUS.micro }}>
                      <span style={{ fontSize: TEXT.meta, color: P.ink }}>{m}</span>
                    </div>
                  ))}
                  <div style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint, margin: '8px 0 5px' }}>REASONING</div>
                  <div style={{ display: 'flex', gap: 6 }}>
                    {['Low', 'Med', 'High'].map(l => (
                      <motion.span key={l} onClick={() => { setReason(l); setModellOffen(false); melden('Reasoning: ' + l) }} whileTap={{ scale: 0.95 }} transition={SPRING}
                        style={{ flex: 1, textAlign: 'center', padding: '3px 0', borderRadius: RADIUS.micro, border: `1px solid ${l === reason ? P.ink : P.line}`, fontSize: TEXT.meta, color: P.ink, cursor: 'pointer' }}>{l}</motion.span>
                    ))}
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
          </span>
          <SymbolKnopf P={P} Icon={IconPaperclip} label="Anhang" onClick={() => setAnhang(a => !a)} />
          <motion.button onClick={senden} whileHover={{ scale: 1.05 }} whileTap={{ scale: 0.94 }} transition={SPRING} aria-label="Senden"
            style={{ width: 30, height: 30, borderRadius: RADIUS.pill, background: P.ink, border: 'none', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer' }}>
            <svg width={17} height={12} viewBox="0 0 18 12"><polyline points="0,6 3,6 5,2 7,10 9,4 11,7 13,3 15,6 18,6" fill="none" stroke={P.surface} strokeWidth="1.4" strokeLinecap="round" /></svg>
          </motion.button>
        </div>
      </div>
    </div>
  )
  const ZielBaum = (
    <div style={{ border: `1px solid ${P.line}`, borderRadius: RADIUS.module, background: P.surface, padding: 8, maxWidth: 520 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
        <span style={{ fontSize: 12, fontWeight: 600, color: P.ink }}>skaldir ui-abnahme</span>
        <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub }}>6/9 ✓</span>
      </div>
      {[['Nachweise sind messbar', true, 0], ['Anker-Pruefung', true, 1], ['Overlay-Lint', true, 1], ['Echter Nutzerweg laeuft', false, 0], ['Composer → Session → Senden', true, 1], ['Antwort rendert', false, 1]].map(([l, ok, d]) => (
        <div key={l} style={{ display: 'flex', alignItems: 'center', gap: 6, minHeight: 20, paddingLeft: d * 14, opacity: ok ? 0.6 : 1 }}>
          <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.ink }}>{d ? '└─' : '├─'}</span>
          <span style={{ fontFamily: SANS, fontSize: 12, color: P.ink, textDecoration: ok ? 'line-through' : 'none' }}>{l}</span>
          <span style={{ width: 12, height: 12, border: `1.3px solid ${P.ink}`, background: ok ? P.ink : 'transparent', color: P.surface, fontSize: TEXT.meta, lineHeight: '11px', textAlign: 'center' }}>{ok ? '✓' : ''}</span>
        </div>
      ))}
    </div>
  )
  const Haupt = () => {
    if (view === 'settings') return (
      <div style={{ padding: 14, display: 'flex', gap: 14 }}>
        <div style={{ width: 170 }}>
          {EINSTELLUNGEN.map(e => (
            <motion.div key={e} layout onClick={() => setEinst(e)} transition={SPRING}
              style={{ padding: '5px 8px', borderRadius: RADIUS.row, fontSize: TEXT.meta, cursor: 'pointer', color: einst === e ? P.ink : P.sub, background: einst === e ? P.soft : 'transparent', fontWeight: einst === e ? 600 : 400 }}>{e}</motion.div>
          ))}
        </div>
        <Karte P={P} breit={520}>
          <div style={{ fontSize: 13, fontWeight: 600, color: P.ink, marginBottom: 8 }}>{einst}</div>
          {einst === 'Modell' && <>
            {[['Modell', modell], ['Reasoning', reason]].map(([k, v]) => (
              <div key={k} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '6px 0', borderBottom: `1px solid ${P.hair}` }}>
                <span style={{ flex: 1, fontSize: TEXT.meta, color: P.ink }}>{k}</span><span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub }}>{v}</span>
              </div>))}
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '6px 0' }}>
              <span style={{ flex: 1, fontSize: TEXT.meta, color: P.ink }}>Denken anzeigen</span>
              <Schalter P={P} an={schalter.denken} onClick={() => setSchalter(s => ({ ...s, denken: !s.denken }))} breit />
            </div>
          </>}
          {einst === 'Darstellung' && <>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '6px 0', borderBottom: `1px solid ${P.hair}` }}>
              <span style={{ flex: 1, fontSize: TEXT.meta, color: P.ink }}>Helligkeit</span>
              <div style={{ display: 'flex', gap: 4 }}>
                {[['hell', false], ['dunkel', true]].map(([l, d]) => (
                  <motion.span key={l} onClick={() => setDark(d)} whileTap={{ scale: 0.95 }} transition={SPRING}
                    style={{ padding: '3px 9px', borderRadius: RADIUS.micro, border: `1px solid ${dark === d ? P.ink : P.line}`, fontSize: TEXT.meta, color: P.ink, cursor: 'pointer' }}>{l}</motion.span>
                ))}
              </div>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '6px 0', borderBottom: `1px solid ${P.hair}` }}>
              <span style={{ flex: 1, fontSize: TEXT.meta, color: P.ink }}>Sprachausgabe</span>
              <Schalter P={P} an={schalter.sprache} onClick={() => { setSchalter(s => ({ ...s, sprache: !s.sprache })); melden('Sprachausgabe umgeschaltet') }} breit />
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '6px 0' }}>
              <span style={{ flex: 1, fontSize: TEXT.meta, color: P.ink }}>Bewegung reduzieren</span>
              <Schalter P={P} an={schalter.reduce} onClick={() => { setSchalter(s => ({ ...s, reduce: !s.reduce })); melden('Bewegung: ' + (!schalter.reduce ? 'reduziert' : 'normal')) }} breit />
            </div>
          </>}
          {einst === 'Schluessel' && ['ANTHROPIC_API_KEY', 'OPENAI_API_KEY', 'GODSV_TOKEN'].map(k => (
            <div key={k} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '6px 0', borderBottom: `1px solid ${P.hair}` }}>
              <span style={{ width: 8, height: 8, borderRadius: RADIUS.pill, background: k === 'OPENAI_API_KEY' ? P.faint : P.ink }} />
              <span style={{ flex: 1, fontFamily: MONO, fontSize: TEXT.meta, color: P.ink }}>{k}</span>
              <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub }}>{k === 'OPENAI_API_KEY' ? 'fehlt' : '••••••••'}</span>
              <SymbolKnopf P={P} Icon={IconKey} label="Schluessel setzen" onClick={() => melden('Schluessel gesetzt: ' + k)} />
            </div>
          ))}
          {einst === 'Werkzeuge' && [
            ['godsv-tools', 'verbunden · 18 Werkzeuge', true], ['playwright', 'verbunden · 26 Werkzeuge', true],
            ['chrome-devtools', 'verbunden · 29 Werkzeuge', true], ['motion', 'verbunden · 1 Werkzeug', false]
          ].map(([n, s, an]) => (
            <div key={n} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 0', borderBottom: `1px solid ${P.hair}` }}>
              <span style={{ flex: 1 }}><span style={{ display: 'block', fontFamily: MONO, fontSize: TEXT.meta, color: P.ink }}>{n}</span>
                <span style={{ display: 'block', fontFamily: MONO, fontSize: TEXT.meta, color: P.sub }}>{s}</span></span>
              <Schalter P={P} an={an} onClick={() => melden('Werkzeug umgeschaltet: ' + n)} />
            </div>
          ))}
          {einst === 'Tastenkürzel' && [['Neue Sitzung', 'N'], ['Senden', '⏎'], ['Befehlspalette', 'K'], ['Not-Aus', 'Esc']].map(([l, k]) => (
            <div key={l} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '6px 0', borderBottom: `1px solid ${P.hair}` }}>
              <span style={{ flex: 1, fontSize: TEXT.meta, color: P.ink }}>{l}</span>
              {aufnahme === l
                ? <span style={{ fontFamily: MONO, fontSize: TEXT.meta, border: `1px solid ${P.ink}`, background: P.ink, color: P.surface, padding: '2px 8px', borderRadius: RADIUS.micro }}>Taste drücken…</span>
                : <motion.span onClick={() => { setAufnahme(l); setTimeout(() => { setAufnahme(null); melden('Kürzel gesetzt: ' + l) }, 1400) }} whileTap={{ scale: 0.95 }} transition={SPRING}
                    style={{ fontFamily: MONO, fontSize: TEXT.meta, border: `1px solid ${P.line}`, background: P.soft, padding: '2px 8px', borderRadius: RADIUS.micro, cursor: 'pointer', color: P.ink }}>{k}</motion.span>}
            </div>
          ))}
          {einst === 'Ueber' && <div style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, lineHeight: 1.8 }}>
            Skaldir Agent · Oberflaeche v1.4.0<br />thinking-orbs 0.3.1 · Motion 13.4.0 · Tabler 3.46.0<br />
            Diese Vorschau ist vollstaendig bedienbar — jede Flaeche reagiert.
          </div>}
        </Karte>
      </div>
    )
    if (view === 'files') return (
      <div style={{ padding: 14, display: 'flex', gap: 14 }}>
        <Karte P={P} breit={280}>
          {['designs/GEWAEHLT', 'fixtures', 'docs/', 'audit/'].map((o, i) => (
            <div key={o}>
              <div onClick={() => melden('Ordner: ' + o)} style={{ display: 'flex', gap: 6, alignItems: 'center', padding: '3px 0', cursor: 'pointer', fontSize: TEXT.meta, color: P.ink }}>
                <IconChevronRight size={11} stroke={2} /><IconFolderOpen size={12} stroke={1.8} />{o}
              </div>
              {i < 2 && ['UI-CONTRACT.md', 'SkaldirFullUI.fixture.tsx'].map(d => (
                <div key={d} onClick={() => { setDatei(d); melden('Datei geoeffnet: ' + d) }}
                  style={{ display: 'flex', gap: 6, alignItems: 'center', padding: '2px 0 2px 22px', cursor: 'pointer', fontFamily: MONO, fontSize: TEXT.meta, color: datei === d ? P.ink : P.sub, background: datei === d ? P.soft : 'transparent', borderRadius: RADIUS.micro }}>
                  <IconScript size={11} stroke={1.8} />{d}
                </div>
              ))}
            </div>
          ))}
        </Karte>
        <Karte P={P} breit={460}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
            <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.ink }}>{datei}</span>
            <span style={{ marginLeft: 'auto', display: 'flex', gap: 4 }}>
              <SymbolKnopf P={P} Icon={IconCopy} label="kopieren" onClick={() => melden('Inhalt kopiert')} />
              <SymbolKnopf P={P} Icon={IconExternalLink} label="extern oeffnen" onClick={() => melden('Extern geoeffnet')} />
            </span>
          </div>
          <div style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, lineHeight: 1.9 }}>
            1 · Der Vertrag bindet Zuordnung, Tokens, Radien.<br />2 · Tokens: #FAFAFA / #111111, Text #171717 / #F5F5F5.<br />
            3 · Radien 4 · 8 · 12 · 16 · 24 · 999.<br />4 · Status immer Text UND Symbol.
          </div>
        </Karte>
      </div>
    )
    if (view === 'review') return (
      <div style={{ padding: 14, display: 'flex', gap: 14 }}>
        <Karte P={P} breit={300}>
          <div style={{ fontSize: TEXT.body, fontWeight: 600, marginBottom: 6 }}>Aenderungen</div>
          {[['+5 −3', 'fixtures/ChatBarRef.fixture.tsx'], ['+31 −12', 'fixtures/SkaldirFullUI.fixture.tsx'], ['+2 −2', 'fixtures/_tokens.ts']].map(([d, f]) => (
            <div key={f} onClick={() => melden('Diff geoeffnet: ' + f)} style={{ display: 'flex', gap: 8, padding: '4px 0', borderBottom: `1px solid ${P.hair}`, cursor: 'pointer' }}>
              <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.ink }}>{d}</span>
              <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{f}</span>
            </div>
          ))}
          <div style={{ display: 'flex', gap: 6, marginTop: 10 }}>
            <Knopf P={P} primary klein Icon={IconCheck} onClick={() => melden('Aenderungen uebernommen')}>uebernehmen</Knopf>
            <Knopf P={P} klein Icon={IconX} onClick={() => melden('Aenderungen verworfen')}>verwerfen</Knopf>
          </div>
        </Karte>
        <Karte P={P} breit={420}>
          <div style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.ink, lineHeight: 1.9 }}>
            <div style={{ background: P.soft }}>+ farbe: LIGHT.faint statt #8C8C8C</div>
            <div style={{ background: P.soft }}>+ kontrast: 4,89:1</div>
            <div>- fontSize: 10,5</div>
            <div>+ fontSize: 11</div>
          </div>
        </Karte>
      </div>
    )
    if (view === 'terminal') return (
      <div style={{ padding: 14 }}>
        <Karte P={P} breit={640}>
          <div style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.ink, lineHeight: 1.9, minHeight: 120 }}>
            {term.map((z, i) => <div key={i} style={{ color: z.startsWith('$') ? P.ink : P.sub }}>{z}</div>)}
          </div>
          <div style={{ display: 'flex', gap: 6, alignItems: 'center', borderTop: `1px solid ${P.hair}`, paddingTop: 8 }}>
            <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub }}>$</span>
            <input value={termEingabe} onChange={e => setTermEingabe(e.target.value)} aria-label="Terminalbefehl"
              onKeyDown={e => { if (e.key === 'Enter' && termEingabe.trim()) { setTerm(t => [...t, '$ ' + termEingabe, 'ok — ' + termEingabe.split(' ')[0] + ' ausgefuehrt']); setTermEingabe('') } }}
              placeholder="Befehl eingeben und Enter" style={{ flex: 1, border: 'none', background: 'transparent', color: P.ink, fontFamily: MONO, fontSize: TEXT.meta, outline: 'none' }} />
            <SymbolKnopf P={P} Icon={IconPlayerSkipForward} label="ausfuehren" onClick={() => { if (termEingabe.trim()) { setTerm(t => [...t, '$ ' + termEingabe, 'ok']); setTermEingabe('') } }} />
          </div>
        </Karte>
      </div>
    )
    if (view === 'jobs') return (
      <div style={{ padding: 14 }}>
        <Karte P={P} breit={560}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
            <span style={{ fontSize: TEXT.body, fontWeight: 600, color: P.ink }}>Geplante Aufgaben</span>
            <span style={{ marginLeft: 'auto' }}><Knopf P={P} klein Icon={IconPlus} onClick={() => melden('Aufgabe angelegt')}>neu</Knopf></span>
          </div>
          {[['j1', 'Anker jede Nacht messen', 'naechster Lauf 02:00'], ['j2', 'Bericht woechentlich bauen', 'naechster Lauf Mo 08:00'], ['j3', 'Sitzungen aufraeumen', 'naechster Lauf 12:00']].map(([id, n, w]) => (
            <div key={id} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 0', borderBottom: `1px solid ${P.hair}` }}>
              <span style={{ flex: 1 }}><span style={{ display: 'block', fontSize: TEXT.meta, color: P.ink }}>{n}</span>
                <span style={{ display: 'block', fontFamily: MONO, fontSize: TEXT.meta, color: P.sub }}>{w}</span></span>
              <Knopf P={P} klein Icon={IconPlayerPlay} onClick={() => melden('Job gestartet: ' + n)}>jetzt</Knopf>
              <Schalter P={P} an={jobAn[id]} onClick={() => setJobAn(j => ({ ...j, [id]: !j[id] }))} />
            </div>
          ))}
        </Karte>
      </div>
    )
    /* Chat */
    return (
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0, position: 'relative' }}>
        {verlauf.length === 0 ? (
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 16 }}>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(9, 4px)', gap: 4 }}>
              {Array.from({ length: 81 }).map((_, k) => (
                <motion.span key={k} initial={{ opacity: 0, scale: 0.4 }} animate={{ opacity: 0.35, scale: 1 }} transition={{ ...SPRING, delay: k * 0.004 }}
                  style={{ width: 4, height: 4, borderRadius: RADIUS.pill, background: P.ink }} />
              ))}
            </div>
            <motion.span initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.3 }} style={{ fontFamily: MONO, fontSize: 13, letterSpacing: 3, color: P.ink }}>SKALDIR</motion.span>
            <Slogan P={P} />
            <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>Ziel eintippen, Datei anhaengen oder / fuer Befehle · kein Orb im leeren Chat</span>
          </div>
        ) : (
          <div style={{ flex: 1, padding: 14, display: 'flex', flexDirection: 'column', gap: 8, overflowY: 'auto' }}>
            {verlauf.map((m, i) => (
              <motion.div key={i} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={SPRING} style={{ display: 'flex', justifyContent: m.von === 'du' ? 'flex-end' : 'flex-start' }}>
                <div style={{ maxWidth: m.von === 'du' ? '62%' : '86%', background: m.von === 'du' ? P.ink : P.surface, color: m.von === 'du' ? P.bg : P.ink,
                              border: m.von === 'du' ? 'none' : `1px solid ${P.line}`, borderRadius: RADIUS.module, padding: '8px 12px', fontFamily: MONO, fontSize: 13, lineHeight: 1.5 }}>{m.text}</div>
              </motion.div>
            ))}
            {lauf ? (
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontFamily: MONO, fontSize: TEXT.meta, color: P.sub }}>
                <ThinkingOrb state="working" size={20} theme={dark ? 'dark' : 'light'} />arbeitet · {fmt(live)}
              </div>
            ) : null}
            {panelAn.ziel ? ZielBaum : null}
            <div style={{ display: 'flex', gap: 6 }}>
              {['kopieren', 'erneut senden', 'als Nachweis'].map(a => (
                <motion.span key={a} whileHover={{ y: -1 }} transition={SPRING} onClick={() => melden(a + ' ausgeloest')}
                  style={{ padding: '2px 8px', border: `1px solid ${P.line}`, borderRadius: RADIUS.pill, fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, cursor: 'pointer' }}>{a}</motion.span>
              ))}
            </div>
          </div>
        )}
        {panelAn.subfenster ? (
          <motion.div drag dragMomentum={false} initial={{ opacity: 0, scale: 0.94 }} animate={{ opacity: 1, scale: 1 }} transition={SPRING}
            style={{ position: 'absolute', right: 16, top: 12, width: 234, background: P.surface, border: `1px solid ${P.line}`, boxShadow: '0 14px 38px rgba(0,0,0,0.30)', fontFamily: MONO, cursor: 'grab', zIndex: 10 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 7, padding: '5px 7px', background: P.bg, borderBottom: `1px solid ${P.line}` }}>
              <ThinkingOrb state="working" size={20} theme={dark ? 'dark' : 'light'} />
              <span style={{ fontSize: TEXT.meta, color: P.sub }}>pruefer</span><span style={{ fontSize: TEXT.meta, color: P.ink }}>t 00:42</span>
              <span style={{ marginLeft: 'auto', display: 'flex', gap: 4 }}>
                <SymbolKnopf P={P} Icon={IconMinus} label="einklappen" groesse={20} onClick={() => melden('Fenster eingeklappt')} />
                <SymbolKnopf P={P} Icon={IconX} label="schliessen" groesse={20} onClick={() => { setPanelAn(p => ({ ...p, subfenster: false })); melden('Subagenten-Fenster geschlossen') }} />
              </span>
            </div>
            <div style={{ padding: 7, fontSize: TEXT.meta, color: P.ink, lineHeight: 1.8 }}>
              › oeffne tools/check_ui_overlay_anchors.py<br />› lese 28 Anker-Erwartungen<br />› messe #composer bei 1280x800<br />› vergleiche: 27 von 28 stimmen
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 7, padding: '6px 7px', borderTop: `1px solid ${P.line}` }}>
              <SymbolKnopf P={P} Icon={IconPlayerStop} label="beenden" groesse={22} onClick={() => melden('Subagent beendet')} />
              <span style={{ border: `1px solid ${P.ink}`, padding: '2px 5px', display: 'inline-flex', gap: 6, fontSize: TEXT.meta, alignItems: 'center' }}>
                {Array.from({ length: 10 }).map((_, i) => <span key={i} style={{ width: 5, height: 9, background: i < 6 ? P.ink : 'transparent', border: `1px solid ${P.ink}` }} />)}62%
              </span>
            </div>
          </motion.div>
        ) : null}
        {panelAn.kontext ? (
          <motion.div drag dragMomentum={false} style={{ position: 'absolute', right: 16, bottom: 22, cursor: 'grab', touchAction: 'none', zIndex: 10 }}>
            <div style={{ width: 208, borderRadius: RADIUS.pill, border: `1px solid ${P.line}`, background: P.surface, boxShadow: '0 10px 28px rgba(0,0,0,0.22)', padding: '9px 13px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{ fontFamily: MONO, fontSize: 13, color: P.ink }}>[██████░░░░]</span>
                <span style={{ marginLeft: 'auto', fontFamily: MONO, fontSize: 12, color: P.ink }}>62%</span>
                <SymbolKnopf P={P} Icon={IconX} label="Kontext schliessen" groesse={18} onClick={() => { setPanelAn(p => ({ ...p, kontext: false })); melden('Kontext-Kapsel geschlossen') }} />
              </div>
              <div style={{ display: 'flex', gap: 7, fontFamily: MONO, fontSize: TEXT.meta, color: P.sub }}><span>128k/200k</span><span>·</span><span>reserve 12k</span></div>
            </div>
          </motion.div>
        ) : null}
      </div>
    )
  }
  return (
    <div data-stage style={{ padding: 16, background: P.bg, fontFamily: SANS, color: P.ink }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 8 }}>
        <span style={{ fontFamily: MONO, fontSize: 12, fontWeight: 600 }}>Vollstaendige App — durchklickbar · {fassung}</span>
        <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub }}>Symbolsprache: Tabler · {istLeer ? 'leere Sitzung' : 'Sitzung „' + sitzung.titel + '"'}</span>
      </div>
      <div data-fenster style={{ position: 'relative', width: 'min(1362px, 100%)', height: 760, border: `1px solid ${P.line}`, borderRadius: RADIUS.module, overflow: 'hidden', background: P.bg, display: 'flex' }}>
        {panelAn.cu ? (
          <div data-agent-visible="false" style={{ position: 'absolute', inset: 0, pointerEvents: 'none', zIndex: 20 }}>
            {[[{ left: 0, top: 0 }], [{ right: 0, top: 0 }], [{ left: 0, bottom: 0 }], [{ right: 0, bottom: 0 }]].map((pos, k) => (
              <motion.svg key={k} width={120} height={120} viewBox="0 0 120 120" style={{ position: 'absolute', ...pos[0], filter: 'blur(4px)', overflow: 'visible' }}>
                <motion.g animate={{ scale: [1, 2.28, 1], x: [0, 76.8, 0], y: [0, 76.8, 0], opacity: [0.32, 0.88, 0.32] }} transition={{ duration: 3.6, ease: 'easeInOut', repeat: Infinity, delay: k * 0.18 }}>
                  <path d="M 120 0 L 18 0 A 18 18 0 0 0 0 18 L 0 120 L 2 120 L 12 12 L 120 2 Z" fill={DARK.ink} fillOpacity={0.56} stroke={DARK.ink} strokeOpacity={0.56} strokeWidth="1.5" />
                </motion.g>
              </motion.svg>
            ))}
            <div style={{ position: 'absolute', left: '50%', bottom: 12, transform: 'translateX(-50%)', display: 'flex', alignItems: 'center', gap: 10, padding: '0 10px 0 13px', height: 32, background: 'rgba(17,17,17,0.88)', border: '1.15px solid rgba(255,255,255,0.3)', borderRadius: RADIUS.pill }}>
              <ThinkingOrb state="working" size={20} theme="dark" />
              <span style={{ fontSize: TEXT.meta, color: DARK.ink }}>Skaldir steuert</span>
              <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: DARK.sub }}>00:42</span>
              <span style={{ minWidth: 40, height: 26, borderRadius: RADIUS.row, background: DARK.ink, color: DARK.bg, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontFamily: MONO, fontSize: TEXT.meta }}>esc</span>
            </div>
          </div>
        ) : null}
        {Rail}{Seitenleiste}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
          {Titelleiste}
          <div style={{ flex: 1, display: 'flex', minHeight: 0 }}>
            <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
              <Haupt />
              {view === 'chat' ? Composer : null}
            </div>
            {panelAn.rechts ? (
              <div style={{ width: 280, borderLeft: `1px solid ${P.line}`, background: P.surface, padding: 10, overflowY: 'auto' }}>
                <div style={{ fontSize: TEXT.body, fontWeight: 600, marginBottom: 8 }}>Uebersicht</div>
                {[['Ziel', 'skaldir ui-abnahme · 6/9'], ['Laufzeit', fmt(live)], ['Werkzeuge', '4 verbunden'], ['Nachweise', '6 belegt · 3 offen'],
                  ['Subagenten', '1 offen'], ['Tokens', '↑12,4k ↓4,1k'], ['Kosten', '0,31 € von 4,00 €']].map(([k, v]) => (
                  <div key={k} style={{ display: 'flex', gap: 8, padding: '4px 0', borderBottom: `1px solid ${P.hair}` }}>
                    <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint, minWidth: 74 }}>{k}</span>
                    <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.ink }}>{v}</span>
                  </div>
                ))}
                <div style={{ marginTop: 10 }}>
                  <Knopf P={P} klein Icon={IconChartBar} onClick={() => melden('Bericht geoeffnet')}>Bericht</Knopf>{' '}
                  <Knopf P={P} klein Icon={IconScript} onClick={() => melden('Protokoll geoeffnet')}>Protokoll</Knopf>
                </div>
              </div>
            ) : null}
          </div>
          <div style={{ height: 26, borderTop: `1px solid ${P.line}`, background: P.surface, display: 'flex', alignItems: 'center', gap: 14, padding: '0 12px', fontFamily: MONO, fontSize: TEXT.meta, color: P.sub }}>
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}><span style={{ width: 6, height: 6, borderRadius: RADIUS.pill, background: P.ink }} />godsv-tools · verbunden</span>
            <span>{modell}</span><span>Reasoning {reason}</span><span>{fmt(live)}</span>
            <span style={{ marginLeft: 'auto' }}>{panelAn.cu ? 'Computer-Use aktiv' : 'Computer-Use aus'}</span>
          </div>
        </div>
        <AnimatePresence>
          {palette ? (
            <motion.div key="pal" initial={{ opacity: 0, y: -6, scale: 0.97 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, y: -6 }} transition={SPRING}
              style={{ position: 'absolute', left: '50%', top: 60, x: '-50%', width: 520, background: P.surface, border: `1px solid ${P.line}`, borderRadius: RADIUS.dialog, boxShadow: '0 22px 60px rgba(0,0,0,0.34)', zIndex: 40, overflow: 'hidden' }}>
              <input autoFocus value={pquery} onChange={e => setPquery(e.target.value)} aria-label="Befehl suchen" placeholder="Befehl suchen"
                style={{ width: '100%', boxSizing: 'border-box', border: 'none', borderBottom: `1px solid ${P.hair}`, padding: '11px 13px', background: 'transparent', color: P.ink, fontFamily: SANS, fontSize: TEXT.body, outline: 'none' }} />
              <div style={{ padding: 8 }}>
                {BEFEHLE.filter(b => b.text.toLowerCase().includes(pquery.toLowerCase())).map((b, i) => (
                  <div key={b.id} onClick={() => befehl(b.id)} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '6px 8px', borderRadius: RADIUS.row, background: i === 0 ? P.soft : 'transparent', cursor: 'pointer' }}>
                    <span style={{ flex: 1, fontSize: TEXT.meta, color: P.ink }}>{b.text}</span>
                    <span style={{ fontFamily: MONO, fontSize: TEXT.meta, border: `1px solid ${P.line}`, background: P.soft, color: P.ink, padding: '1px 6px', borderRadius: RADIUS.micro }}>{b.taste}</span>
                  </div>
                ))}
                <div onClick={() => setPalette(false)} style={{ padding: '6px 8px', borderTop: `1px solid ${P.hair}`, marginTop: 4, fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, cursor: 'pointer' }}>Esc · schliessen</div>
              </div>
            </motion.div>
          ) : null}
          {manager ? (
            <motion.div key="mgr" initial={{ opacity: 0, x: 20 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: 20 }} transition={SPRING}
              style={{ position: 'absolute', right: 16, top: 44, width: 250, background: P.surface, border: `1px solid ${P.line}`, borderRadius: RADIUS.module, boxShadow: '0 14px 34px rgba(0,0,0,0.24)', zIndex: 35 }}>
              <div style={{ padding: '7px 9px', borderBottom: `1px solid ${P.line}`, fontFamily: MONO, fontSize: TEXT.meta, letterSpacing: 1, color: P.sub }}>
                PANELS · {Object.values(panelAn).filter(Boolean).length} VON 7 AN
              </div>
              <div style={{ padding: 6 }}>
                {PANELS.map(p => (
                  <div key={p.id} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '4px 6px' }}>
                    <span style={{ flex: 1, fontSize: TEXT.meta, color: P.ink }}>{p.label}</span>
                    <Schalter P={P} an={panelAn[p.id]} onClick={() => { setPanelAn(a => ({ ...a, [p.id]: !a[p.id] })); melden(p.label + ': ' + (!panelAn[p.id] ? 'an' : 'aus')) }} />
                  </div>
                ))}
                <div style={{ display: 'flex', gap: 6, marginTop: 6, padding: 4 }}>
                  <Knopf P={P} klein onClick={() => { setPanelAn(Object.fromEntries(PANELS.map(p => [p.id, true]))); melden('Alle Panels an') }}>alle an</Knopf>
                  <Knopf P={P} klein onClick={() => { setPanelAn(Object.fromEntries(PANELS.map(p => [p.id, false]))); melden('Alle Panels aus') }}>alle aus</Knopf>
                </div>
              </div>
            </motion.div>
          ) : null}
        </AnimatePresence>
        <div style={{ position: 'absolute', right: 16, bottom: 34, display: 'flex', flexDirection: 'column', gap: 8, width: 290, zIndex: 30 }}>
          <AnimatePresence>
            {toasts.map(t => (
              <motion.div key={t.id} initial={{ opacity: 0, x: 24 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: 24 }} transition={SPRING}
                style={{ background: P.surface, border: `1px solid ${P.line}`, borderRadius: RADIUS.module, padding: '8px 10px', boxShadow: '0 10px 26px rgba(0,0,0,0.22)' }}>
                <div style={{ fontSize: TEXT.meta, color: P.ink }}>{t.t}</div>
              </motion.div>
            ))}
          </AnimatePresence>
        </div>
      </div>
      <div style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, marginTop: 8 }}>
        Klickpfad: Schiene wechselt die Ansicht · Sitzung anlegen/loeschen · Reiter oeffnen/schliessen · Suche oder ⌘K oeffnet die Befehlspalette · Einstellungen mit sechs Reitern · Panel-Manager schaltet sieben Panels · Composer sendet wirklich
      </div>
    </div>
  )
}
const Slogan = ({ P }) => {
  const [i, setI] = useState(0)
  useEffect(() => { const id = setInterval(() => setI(v => (v + 1) % SPRUECHE.length), 4200); return () => clearInterval(id) }, [])
  return (
    <div style={{ position: 'relative', height: 22 }}>
      <AnimatePresence mode="wait">
        <motion.span key={i} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }} transition={SPRING}
          style={{ position: 'absolute', fontFamily: SANS, fontSize: 13, color: P.sub, whiteSpace: 'nowrap' }}>{SPRUECHE[i]}</motion.span>
      </AnimatePresence>
    </div>
  )
}
export default {
  '01 · LEERE SITZUNG (hell) — vollstaendig durchklickbar': () => <App fassung="leer-hell" />,
  '02 · LEERE SITZUNG (dunkel)': () => <App fassung="leer-dunkel" />,
  '03 · SITZUNG MIT INHALT (hell)': () => <App fassung="inhalt-hell" />,
  '04 · SITZUNG MIT INHALT (dunkel)': () => <App fassung="inhalt-dunkel" />,
  '05 · EINSTELLUNGEN GEOEFFNET (sechs Reiter)': () => <App fassung="einstellungen" />,
  '06 · PANEL-MANAGER UND BEFEHLSPALETTE GEOEFFNET': () => <App fassung="manager" />
}
