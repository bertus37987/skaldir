/**
 * Freigaben & Bestätigungen (Approval / Confirmation) — 10 animierte Variationen.
 *
 * Kategorie C des UI-Workbench-Labors ('God's Voice / Skaldir Agent').
 * Jede Karte beantwortet drei Fragen: Was genau passiert? Welche Wege bleiben?
 * Wie viel Restzeit? Nur Schwarz/Weiß/Grau, Geometrie bevorzugt, Symbole nur
 * aus @tabler/icons-react.
 *
 * Bewegung (Motion): Karte hängt mit Feder ein, die Wege (Schaltflächen)
 * rutschen versetzt nach, Detailblöcke klappen auf/zu, Häkchen springt auf,
 * der Restzeit-Balken läuft animiert gegen 0. Variante 09 ist der Endzustand
 * und bleibt bewusst STILL (keine Animation). Umschalter hell/dunkel im Kopf
 * der Bühne bei 01–08.
 */
import { motion, AnimatePresence, animate } from 'motion/react'
import React, { useState, useRef, useEffect } from 'react'
import { IconAlertTriangle, IconCheck, IconChevronDown, IconClock, IconCurrencyEuro, IconMessageCircleQuestion, IconPackage, IconPencil, IconRotateClockwise, IconStack2, IconTerminal } from '@tabler/icons-react'
import { useBarrierefrei } from './_barrierefrei'
import { LIGHT, DARK, MONO, SANS, SPRING, RADIUS, TEXT } from './_tokens'

/* ------------------------------------------------------------- Konstanten */

type Palette = typeof LIGHT

/* ------------------------------------------------------------- Bausteine */

/** Restzeit: Balken läuft von 100 % auf 0, die Sekundenzahl zählt mit. */
function Countdown({ seconds = 20, P }: { seconds?: number; P: Palette }) {
  const [rest, setRest] = useState(seconds)
  const balken = useRef<HTMLSpanElement>(null)
  useEffect(() => {
    if (!balken.current) return
    balken.current.style.width = '100%'
    const start = Date.now()
    const ctrl = animate(balken.current as unknown as HTMLElement /* DOM-Knoten tweenen */,
      { width: '0%' }, { duration: seconds, ease: 'linear' })
    const tick = setInterval(() => setRest(Math.max(0, seconds - Math.floor((Date.now() - start) / 1000))), 250)
    return () => { ctrl.stop(); clearInterval(tick) }
  }, [seconds])
  return (
    <div data-countdown style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <span style={{ fontFamily: SANS, fontSize: TEXT.meta, color: P.faint, whiteSpace: 'nowrap' }}>Restzeit</span>
      <span style={{ flex: 1, height: 3, background: P.hair, border: `1px solid ${P.line}`, overflow: 'hidden' }}>
        <span ref={balken} data-countdownbar style={{ display: 'block', height: '100%', width: '100%', background: P.ink }} />
      </span>
      <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, minWidth: 28, textAlign: 'right' }}>{rest}&thinsp;s</span>
    </div>
  )
}

/** Warnmarke: Ausrufezeichen im Viereck, rein typografisch. */
const WarnGlyph = ({ P }: { P: string }) => (
  <span style={{
    fontFamily: MONO, fontSize: TEXT.meta, width: 13, height: 13, flex: '0 0 auto',
    border: `1px solid ${P}`, color: P, lineHeight: '11px', textAlign: 'center'
  }}>!</span>
)

/** Kopfzeile der Karte: Zeichenfläche, Titel, Erklärung. */
const CardHead = ({ icon, title, sub, P }: { icon: React.ReactNode; title: string; sub: string; P: Palette }) => (
  <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
    <motion.span initial={{ opacity: 0, scale: 0.7 }} animate={{ opacity: 1, scale: 1 }} transition={SPRING}
      style={{ width: 26, height: 26, border: `1px solid ${P.line}`, background: P.hair, color: P.ink, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', flex: '0 0 auto' }}>
      {icon}
    </motion.span>
    <div style={{ flex: 1, minWidth: 0 }}>
      <div style={{ fontFamily: SANS, fontSize: 13.5, fontWeight: 600, color: P.ink }}>{title}</div>
      <div style={{ fontFamily: SANS, fontSize: 11.5, color: P.sub, margin: '2px 0 0', lineHeight: 1.5 }}>{sub}</div>
    </div>
  </div>
)

/** Zeile: Beschriftung links, Wert rechts in Monospace. */
const Row = ({ k, v, P }: { k: string; v: string; P: Palette }) => (
  <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, padding: '3.5px 0', borderBottom: `1px solid ${P.hair}` }}>
    <span style={{ fontFamily: SANS, fontSize: TEXT.meta, color: P.sub, flex: '0 0 auto' }}>{k}</span>
    <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.ink, textAlign: 'right', wordBreak: 'break-all' }}>{v}</span>
  </div>
)

/** Befehlsblock in Monospace, mit $-Prompt davor. */
const CmdBlock = ({ cmd, P }: { cmd: string; P: Palette }) => (
  <motion.div data-cmdblock initial={{ opacity: 0, x: -5 }} animate={{ opacity: 1, x: 0 }} transition={{ ...SPRING, delay: 0.08 }}
    style={{ padding: '8px 10px', background: P.hair, border: `1px solid ${P.line}`, fontFamily: MONO, fontSize: 11, color: P.ink, wordBreak: 'break-all' }}>
    <span style={{ color: P.faint }}>$&nbsp;</span>{cmd}
  </motion.div>
)

/* ------------------------------------------------------------- die Karte */

type Weg = {
  label: string
  note?: string
  primary?: boolean
  danger?: boolean
  detail?: string
}

function ApprovalCard({
  P, seconds, icon, title, sub, warnLine, rows, cmd, preview, footer, options
}: {
  P: Palette
  seconds?: number
  icon: React.ReactNode
  title: string
  sub: string
  warnLine?: string
  rows?: { k: string; v: string }[]
  cmd?: string
  preview?: string[]
  footer?: string
  options: Weg[]
}) {
  const [openIdx, setOpenIdx] = useState<number | null>(null)
  const [gewählt, setGewählt] = useState<string | null>(null)
  return (
    <motion.div data-approvalcard initial={{ opacity: 0, y: 16, scale: 0.985 }} animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ ...SPRING, delay: 0.05 }}
      style={{ width: 470, maxWidth: '100%', background: P.surface, border: `1px solid ${P.line}`, padding: 14, boxSizing: 'border-box' }}>
      <CardHead icon={icon} title={title} sub={sub} P={P} />

      {warnLine && (
        <motion.div data-warnline initial={{ opacity: 0, x: -4 }} animate={{ opacity: 1, x: 0 }} transition={{ ...SPRING, delay: 0.1 }}
          style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 10, padding: '7px 9px', border: `1.5px solid ${P.ink}`, background: P.hair }}>
          <WarnGlyph P={P.ink} />
          <span style={{ fontFamily: SANS, fontSize: 11.5, fontWeight: 600, color: P.ink, lineHeight: 1.4 }}>{warnLine}</span>
        </motion.div>
      )}

      {rows && <div style={{ marginTop: 10 }}>{rows.map(r => <Row key={r.k} k={r.k} v={r.v} P={P} />)}</div>}

      {cmd && <div style={{ marginTop: 10 }}><CmdBlock cmd={cmd} P={P} /></div>}

      {preview && (
        <motion.div data-preview initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.15 }}
          style={{ marginTop: 10, padding: '7px 10px', border: `1px solid ${P.line}`, background: P.hair }}>
          <div style={{ fontFamily: MONO, fontSize: TEXT.meta, letterSpacing: 1, color: P.faint, marginBottom: 4 }}>VORSCHAU · ERSTE ZEILEN</div>
          {preview.map((l, i) => (
            // statisch: Indexschluessel ok
            <div key={i} style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.ink, whiteSpace: 'pre-wrap' }}>
              <span style={{ color: P.faint }}>{String(i + 1).padStart(2, '0')}&nbsp;</span>{l}
            </div>
          ))}
        </motion.div>
      )}

      {seconds !== undefined && !gewählt && (
        <div style={{ marginTop: 11 }}><Countdown seconds={seconds} P={P} /></div>
      )}

      {gewählt ? (
        <motion.div data-acknowledged initial={{ opacity: 0, scale: 0.96 }} animate={{ opacity: 1, scale: 1 }} transition={SPRING}
          style={{ marginTop: 12, padding: '9px 11px', border: `1px solid ${P.ink}`, display: 'flex', alignItems: 'center', gap: 9 }}>
          <motion.span initial={{ scale: 0.3, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} transition={{ ...SPRING, delay: 0.06 }}
            style={{ width: 16, height: 16, border: `1.3px solid ${P.ink}`, color: P.ink, fontSize: TEXT.meta, lineHeight: '14px', textAlign: 'center' }}>✓</motion.span>
          <span style={{ fontFamily: SANS, fontSize: 11.5, fontWeight: 600, color: P.ink }}>gewählt: {gewählt}</span>
          <span style={{ marginLeft: 'auto', fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>Sitzung simuliert</span>
        </motion.div>
      ) : (
        <div data-options style={{ marginTop: 12, display: 'flex', flexDirection: 'column', gap: 6 }}>
          {options.map((o, i) => (
            <motion.button key={o.label} data-option onClick={() => { setGewählt(o.label); setOpenIdx(i) }}
              initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }} transition={{ ...SPRING, delay: 0.12 + i * 0.07 }}
              whileHover={{ scale: 1.015 }} whileTap={{ scale: 0.97 }}
              style={{
                display: 'flex', alignItems: 'center', gap: 9, padding: '9px 11px', cursor: 'pointer', textAlign: 'left',
                border: (o.primary) ? `1.5px solid ${P.ink}` : `1px solid ${P.line}`,
                background: o.primary ? P.ink : P.surface,
                color: o.primary ? P.surface : P.ink
              }}>
              {o.danger ? <WarnGlyph P={o.primary ? P.surface : P.ink} /> : (
                <motion.span animate={{ rotate: openIdx === i ? 90 : 0 }} transition={SPRING} style={{ display: 'inline-flex', flex: '0 0 auto' }}>
                  <IconChevronDown size={12} stroke={2} color={o.primary ? P.surface : P.faint} style={{ transform: 'rotate(-90deg)' }} />
                </motion.span>
              )}
              <span style={{ fontFamily: SANS, fontSize: 12.5, fontWeight: o.primary || o.danger ? 600 : 400 }}>{o.label}</span>
              {o.note && <span style={{ marginLeft: 'auto', fontFamily: MONO, fontSize: TEXT.meta, color: o.primary ? P.surface : P.faint, opacity: o.primary ? 0.62 : 1 }}>{o.note}</span>}
            </motion.button>
          ))}
        </div>
      )}

      {openIdx !== null && options[openIdx]?.detail && (
        <motion.div data-optiondetail key={options[openIdx]?.label ?? 'detail'} initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: 'auto' }}
          transition={SPRING} style={{ overflow: 'hidden' }}>
          <div style={{ margin: '8px 0 0 15px', padding: '2px 0 2px 11px', borderLeft: `2px solid ${P.line}`, fontFamily: SANS, fontSize: TEXT.meta, color: P.sub, lineHeight: 1.5 }}>
            {options[openIdx].detail}
          </div>
        </motion.div>
      )}

      {footer && <div style={{ marginTop: 10, fontFamily: MONO, fontSize: TEXT.meta, color: P.faint, lineHeight: 1.5 }}>{footer}</div>}
    </motion.div>
  )
}

/* ------------------------------------------------------------- Bühne (hell/dunkel) */

/** Bühne: Titelzeile 'NN · …' + Hinweis, darunter zentriert die Karte; Umschalter hell/dunkel. */
function Stage({ n, note, nurDunkel, alleinanzeige, children }: {
  n: string; note: string; nurDunkel?: boolean; alleinanzeige?: boolean; children: (dunkel: boolean, P: Palette) => React.ReactNode
}) {
  useBarrierefrei()
  const [dunkel, setDunkel] = useState(!!nurDunkel)
  const P = dunkel ? DARK : LIGHT
  return (
    <div data-stage style={{
      padding: 22, background: P.bg, color: P.ink, fontFamily: SANS, width: '100%',
      maxWidth: alleinanzeige ? 840 : 560, boxSizing: 'border-box', margin: '0 auto',
      transition: 'background .25s ease, color .25s ease',
      ...(alleinanzeige ? { minHeight: 560, display: 'flex', flexDirection: 'column', justifyContent: 'center' } : {})
    }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, width: '100%' }}>
        <span style={{ fontFamily: MONO, fontSize: 12.5, fontWeight: 600, color: P.ink, whiteSpace: 'nowrap' }}>{n}</span>
        <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{note}</span>
        <motion.span data-themeswitch onClick={() => setDunkel(d => !d)} whileTap={{ scale: 0.9 }} transition={SPRING}
          style={{ marginLeft: 'auto', fontFamily: MONO, fontSize: TEXT.meta, padding: '3px 9px', border: `1px solid ${P.line}`, color: P.sub, cursor: 'pointer', flex: '0 0 auto' }}>
          {dunkel ? 'dunkel' : 'hell'}
        </motion.span>
      </div>
      <div style={{ marginTop: 14, display: 'flex', justifyContent: 'center', width: '100%' }}>{children(dunkel, P)}</div>
    </div>
  )
}

/* ============================================================ Varianten */

/* -------------------------- 01 · Standardfreigabe -------------------------- */
/** Titel + was genau passiert + drei Wege + 20 s Restzeit mit laufendem Balken. */
const Buehne01 = () => (
  <Stage n="01 · Standardfreigabe" note="was passiert · drei Wege · 20 s Restzeit">
    {(dunkel, P) => (
      <ApprovalCard P={P} seconds={20}
        icon={<IconClock size={13} stroke={2} />}
        title="Sitzung neu laden?"
        sub="Skaldir will die Session neu starten, damit seine Änderung sichtbar wird. Im Transkript bleibt alles erhalten."
        rows={[
          { k: 'Was passiert', v: 'Session neu laden, Inhalt bleibt' },
          { k: 'Betrifft', v: 'nur die laufende Sitzung' },
          { k: 'Risiko', v: 'keines bekannt' }
        ]}
        options={[
          { label: 'zulassen', note: 'empfohlen', primary: true, detail: 'Lädt die Sitzung sofort neu. Im Transkript bleibt alles, die Antwort läuft danach weiter.' },
          { label: 'einmalig', note: 'nur dieses eine Mal', detail: 'Neuladen dieses Mal erlaubt; beim nächsten Mal fragt der Agent wieder nach.' },
          { label: 'ablehnen', note: 'nichts tut sich', detail: 'Der Agent hält an und wartet. Ohne Neuladen bleibt die Änderung unsichtbar.' }
        ]}
        footer="fuer alle drei Wege gilt: Esc beendet die Wartezeit sofort."
      />
    )}
  </Stage>
)

/* ------------------- 02 · gefährliche Aktion (löschen) --------------------- */
/** Warnzeile deutlich, 'ablehnen' als Standardschaltfläche hervorgehoben. */
const Buehne02 = () => (
  <Stage n="02 · Gefährliche Aktion" note="Datei löschen · ablehnen hervorgehoben">
    {(_dunkel, P) => (
      <ApprovalCard P={P}
        icon={<IconAlertTriangle size={13} stroke={2} />}
        title="Datei überschreiben?"
        sub="Der Agent will eine bestehende Render-Datei neu schreiben. Die alte Fassung wäre weg."
        warnLine="~/Documents/render/cat.json wird überschrieben — ohne Backup ist das unwiderruflich."
        rows={[
          { k: 'Originalgröße', v: '12 004 B' },
          { k: 'Neugröße', v: '12 884 B (44 Zeilen)' },
          { k: 'Backup', v: 'nein' }
        ]}
        options={[
          { label: 'ablehnen', note: 'empfohlen · Standard', danger: true, detail: 'Nichts passiert. Der Agent fasst im Chat zusammen, was er vorhatte, und wartet auf Deine Entscheidung.' },
          { label: 'überschreiben', note: 'Datei geht verloren', detail: 'Ersetzt cat.json durch die neue Fassung. Old State ist dann unwiederbringlich weg.' },
          { label: 'Backup zuerst', note: 'cat.json.bak anlegen', detail: 'Kopiert die Datei zu cat.json.bak und fragt dann von neu nach.' }
        ]}
      />
    )}
  </Stage>
)

/* ---------------------- 03 · Befehl im Terminal ---------------------------- */
const Buehne03 = () => (
  <Stage n="03 · Befehl im Terminal" note="Monospace · Arbeitsordner · läuft als willy">
    {(_dunkel, P) => (
      <ApprovalCard P={P} seconds={20}
        icon={<IconTerminal size={13} stroke={2} />}
        title="Terminal-Befehl ausführen?"
        sub="Der Agent will im Arbeitsordner einen Befehl absetzen — er läuft unter Deinem Benutzernamen."
        rows={[
          { k: 'Befehl', v: 'npm run build -- --watch' },
          { k: 'Arbeitsordner', v: '~/projects/skaldir-app' },
          { k: 'läuft als', v: 'willy' },
          { k: 'dauerhaft?', v: 'nein, nur solange die Sitzung läuft' }
        ]}
        cmd="npm run build -- --watch"
        options={[
          { label: 'zulassen', note: 'einmal starten', primary: true, detail: 'Startet den Build-Watcher im Arbeitsordner, unter Deinem Namen. Beenden geht über die Session immer.' },
          { label: 'einmalig', note: 'dieses Mal', detail: 'Genau dieser Befehl einmalig erlaubt; der nächste Terminal-Befehl wird wieder gefragt.' },
          { label: 'ablehnen', note: 'kein Befehl', detail: 'Nichts läuft. Im Chat erklärt der Agent den Zweck und schlägt Alternativen vor.' }
        ]}
      />
    )}
  </Stage>
)

/* ------------------ 04 · Systemänderung (Paket) ---------------------------- */
const Buehne04 = () => (
  <Stage n="04 · Systemänderung" note="Paket installieren · System fragt nach Passwort">
    {(_dunkel, P) => (
      <ApprovalCard P={P}
        icon={<IconPackage size={13} stroke={2} />}
        title="System-Paket installieren?"
        sub="Das ändert das System selbst, also die doppelte Abfrage. Nach der Freigabe fragt sudo selbst nach dem Passwort — im Terminal, nie im Chat."
        rows={[
          { k: 'Paket', v: 'jq 1.17.1' },
          { k: 'Quelle', v: 'fedora-updates' },
          { k: 'Betroffene Dienste', v: 'keine' },
          { k: 'Passwortabfrage', v: 'ja — vom System, im Terminal' }
        ]}
        cmd="dnf install -y jq"
        footer="Die System-Passwortabfrage gehört zum System selbst. Nie ins Chat-Fenster eingeben."
        options={[
          { label: 'zulassen', note: 'jetzt installieren', primary: true, detail: 'dnf läuft einmal; Linux fragt dann selbst nach dem System-Passwort. Das Chat-Fenster bleibt unberührt.' },
          { label: 'nicht jetzt', note: 'später erneut fragen', detail: 'Keine Installation in diesem Durchlauf. Der Agent notiert den Ausstand und verfährt ohne jq.' },
          { label: 'ablehnen', note: 'gar nicht', detail: 'Der Agent schlägt einen Weg ohne System-Änderung vor (z. B. ein kleines Skript).' }
        ]}
      />
    )}
  </Stage>
)

/* ----------------------- 05 · Kostenfreigabe ------------------------------ */
const Buehne05 = () => (
  <Stage n="05 · Kostenfreigabe" note="Anbieter · Kosten · Obergrenze">
    {(dunkel, P) => (
      <ApprovalCard P={P} seconds={20}
        icon={<IconCurrencyEuro size={13} stroke={2} />}
        title="Kosten freigeben"
        sub="Der nächste Schritt läuft über einen gebührenpflichtigen Anbieter. Der Restzeit-Balken läuft animiert."
        rows={[
          { k: 'Anbieter', v: 'opencode-go' },
          { k: 'Modell', v: 'glm-5.3-flash' },
          { k: 'Geschätzte Kosten', v: 'ca. 4,20 €' },
          { k: 'Obergrenze', v: '6,00 € — dann hält der Agent an' }
        ]}
        options={[
          { label: 'Fortsetzen', note: 'aus den Gebühren', primary: true, detail: 'Schicket den Auftrag an das Modell. Fallen die Kosten aus dem Rahmen, hält der Agent automatisch an.' },
          { label: 'Abbrechen', note: 'keine Euro', detail: 'Es fällt keine Gebühr an. Der Agent läuft lokal weiter oder wartet.' }
        ]}
      />
    )}
  </Stage>
)

/* ---------------------- 06 · Datei schreiben ------------------------------ */
const Buehne06 = () => (
  <Stage n="06 · Datei schreiben" note="Pfad · Größe · überschreiben? · Vorschau">
    {(_dunkel, P) => (
      <ApprovalCard P={P}
        icon={<IconPencil size={13} stroke={2} />}
        title="Datei schreiben"
        sub="Der Agent will eine Datei anlegen bzw. überschreiben. Unten die ersten Zeilen als Vorschau."
        rows={[
          { k: 'Pfad', v: '~/projects/fastapi/README.md' },
          { k: 'Größe', v: '2 160 B (48 Zeilen)' },
          { k: 'überschreibt', v: 'ja — alte Fassung 48 B' }
        ]}
        preview={['# FastAPI-Demo', '', 'Kleines Projekt, drei Endpunkte,', 'keine Datenbank, kein Docker.']}
        options={[
          { label: 'schreiben', note: 'überschreibt README.md', primary: true, detail: 'Schreibt die neue Fassung in README.md. Die alte Datei ist dann ersetzt.' },
          { label: 'neu benennen', note: 'README-neu.md', detail: 'Legt eine neue Datei an, die alte bleibt unverändert liegen.' },
          { label: 'ablehnen', note: 'nichts schreiben', detail: 'Keine Zeile wird geschrieben. Der Agent fragt im Chat, was stattdessen gewünscht ist.' }
        ]}
      />
    )}
  </Stage>
)

/* ----------------------- 07 · Sammelfreigabe ------------------------------ */
/** 3 Anfragen auf einmal, einzeln abhaken, 'alle zulassen' / 'keine'. */
function SammelKarte({ P }: { P: Palette }) {
  const [haekchen, setHaekchen] = useState<boolean[]>([true, true, false])
  const anfragen = ['rm build/cache.lock', 'npm ci', 'git push -f origin main']
  const anzahl = haekchen.filter(Boolean).length
  return (
    <motion.div data-sammelkarte initial={{ opacity: 0, y: 16, scale: 0.985 }} animate={{ opacity: 1, y: 0, scale: 1 }} transition={{ ...SPRING, delay: 0.05 }}
      style={{ width: 470, maxWidth: '100%', background: P.surface, border: `1px solid ${P.line}`, padding: 14, boxSizing: 'border-box' }}>
      <CardHead icon={<IconStack2 size={13} stroke={2} />} title="3 Anfragen auf einmal"
        sub="Drei Schritte in einem Vorhaben — Du entscheidest jede Zeile einzeln oder für alle." P={P} />

      <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column', gap: 5 }}>
        {anfragen.map((a, i) => (
          <motion.div key={a} data-batchitem initial={{ opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }} transition={{ ...SPRING, delay: 0.1 + i * 0.06 }}
            onClick={() => setHaekchen(h => h.map((v, j) => j === i ? !v : v))}
            whileHover={{ x: 2 }} style={{ display: 'flex', alignItems: 'center', gap: 9, padding: '7px 9px', border: `1px solid ${P.line}`, background: P.hair, cursor: 'pointer' }}>
            <motion.span animate={{ backgroundColor: haekchen[i] ? P.ink : P.surface, scale: haekchen[i] ? 1 : 0.9 }} transition={SPRING}
              style={{ width: 13, height: 13, border: `1.3px solid ${P.ink}`, color: P.surface, fontFamily: MONO, fontSize: TEXT.meta, lineHeight: '11px', textAlign: 'center', flex: '0 0 auto' }}>
              {haekchen[i] ? '✓' : ''}
            </motion.span>
            <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.ink, flex: 1 }}>{a}</span>
            <span style={{ fontFamily: SANS, fontSize: TEXT.meta, color: haekchen[i] ? P.sub : P.faint }}>{haekchen[i] ? 'erlaubt' : 'wartet'}</span>
          </motion.div>
        ))}
      </div>

      <div style={{ marginTop: 11, fontFamily: MONO, fontSize: TEXT.meta, color: P.sub }}>{anzahl} von {anfragen.length} abgehakt</div>

      <div style={{ marginTop: 10, display: 'flex', gap: 6 }}>
        <motion.span whileTap={{ scale: 0.95 }} transition={SPRING} onClick={() => setHaekchen([true, true, true])}
          style={{ flex: 1, fontFamily: SANS, fontSize: 11.5, fontWeight: 600, textAlign: 'center', padding: '8px 0', background: P.ink, color: P.surface, cursor: 'pointer' }}>
          alle zulassen
        </motion.span>
        <motion.span whileTap={{ scale: 0.95 }} transition={SPRING} onClick={() => setHaekchen([false, false, false])}
          style={{ flex: 1, fontFamily: SANS, fontSize: 11.5, textAlign: 'center', padding: '8px 0', border: `1px solid ${P.ink}`, color: P.ink, cursor: 'pointer' }}>
          keine
        </motion.span>
      </div>
      <div style={{ marginTop: 9, fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>Nach dem Abhaken wird noch einmal nachgefragt, was gestartet werden soll.</div>
    </motion.div>
  )
}
const Buehne07 = () => (
  <Stage n="07 · Sammelfreigabe" note="3 Anfragen auf einmal · einzeln abhaken · 'alle zulassen' / 'keine'">
    {(_dunkel, P) => <SammelKarte P={P} />}
  </Stage>
)

/* ------------------ 08 · Rückfrage mit Eingabe ---------------------------- */
const Buehne08 = () => {
  useBarrierefrei()
  const [dunkel, setDunkel] = useState(false)
  const P = dunkel ? DARK : LIGHT
  const [text, setText] = useState('')
  return (
    <div data-stage style={{
      padding: 22, background: P.bg, color: P.ink, fontFamily: SANS, width: '100%', maxWidth: 560,
      boxSizing: 'border-box', margin: '0 auto', transition: 'background .25s ease, color .25s ease'
    }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
        <span style={{ fontFamily: MONO, fontSize: 12.5, fontWeight: 600, color: P.ink, whiteSpace: 'nowrap' }}>08 · Rückfrage mit Eingabe</span>
        <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.sub, flex: 1, overflow: 'hidden', whiteSpace: 'nowrap', textOverflow: 'ellipsis' }}>Frage des Agenten + Textfeld + Senden</span>
        <motion.span data-themeswitch onClick={() => setDunkel(d => !d)} whileTap={{ scale: 0.9 }} transition={SPRING}
          style={{ fontFamily: MONO, fontSize: TEXT.meta, padding: '3px 9px', border: `1px solid ${P.line}`, color: P.sub, cursor: 'pointer' }}>
          {dunkel ? 'dunkel' : 'hell'}
        </motion.span>
      </div>

      <div style={{ marginTop: 14, display: 'flex', justifyContent: 'center' }}>
        <motion.div data-approvalcard initial={{ opacity: 0, y: 16, scale: 0.985 }} animate={{ opacity: 1, y: 0, scale: 1 }} transition={{ ...SPRING, delay: 0.05 }}
          style={{ width: 470, background: P.surface, border: `1px solid ${P.line}`, padding: 14, boxSizing: 'border-box' }}>
          <CardHead icon={<IconMessageCircleQuestion size={13} stroke={2} />} title="Der Agent fragt nach" sub="Er kommt ohne Deine Antwort nicht weiter:" P={P} />
          <div style={{ margin: '10px 0' }}>
            <Row k="Frage" v="welchen Ordner?" P={P} />
            <Row k="Vorschlag" v="~/projects/skaldir-app" P={P} />
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <input data-answer value={text} onChange={e => setText(e.target.value)}
              placeholder="welchen Ordner?"
              style={{ flex: 1, fontFamily: MONO, fontSize: 11, padding: '8px 10px', border: `1px solid ${P.ink}`, background: P.surface, color: P.ink, outline: 'none' }} />
            <motion.span whileHover={text.trim() ? { scale: 1.03 } : undefined} whileTap={text.trim() ? { scale: 0.95 } : undefined} transition={SPRING}
              style={{ fontFamily: SANS, fontSize: 12, fontWeight: 600, padding: '8px 14px', background: text.trim() ? P.ink : P.hair, border: `1px solid ${text.trim() ? P.ink : P.line}`, color: text.trim() ? P.surface : P.faint, cursor: text.trim() ? 'pointer' : 'default' }}>
              Senden
            </motion.span>
          </div>
          <div style={{ marginTop: 9, fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>Im echten Ablauf ersetzt Enter das Klicken von Senden.</div>
        </motion.div>
      </div>
      <div style={{ marginTop: 10, fontFamily: SANS, fontSize: TEXT.meta, color: P.faint }}>
        Demo — die Antwort wird nicht wirklich gesendet.
      </div>
    </div>
  )
}

/* --------------------- 09 · Freigabe erteilt ------------------------------ */
const Buehne09 = () => (
  <Stage n="09 · Freigabe erteilt" note="Endzustand · keine Animation">
    {(_dunkel, P) => (
      /* Bewusst statisch: Eine bereits bestätigte Praxis ruht — kein motion hier. */
      <div data-approvalcard data-done style={{ width: 470, background: P.surface, border: `1px solid ${P.line}`, padding: 14, boxSizing: 'border-box' }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
          <span style={{ width: 22, height: 22, border: `1.5px solid ${P.ink}`, background: P.ink, color: P.surface, fontFamily: MONO, fontSize: 12, lineHeight: '20px', textAlign: 'center', flex: '0 0 auto' }}>
            <IconCheck size={13} stroke={2.5} />
          </span>
          <div style={{ flex: 1 }}>
            <div style={{ fontFamily: SANS, fontSize: 13.5, fontWeight: 600, color: P.ink }}>Erlaubt und ausgeführt</div>
            <div style={{ fontFamily: SANS, fontSize: 11.5, color: P.sub }}>Sitzung neu laden — dieselbe Karte wie in 01.</div>
          </div>
          <span style={{ fontFamily: MONO, fontSize: TEXT.meta, color: P.faint, whiteSpace: 'nowrap' }}>heute · 14:32</span>
        </div>

        <div style={{ marginTop: 10 }}>
          <Row k="Fertig" v="Session neu laden, Inhalt bleibt" P={P} />
          <Row k="Dauer" v="1,8 s" P={P} />
          <Row k="Rücknahme möglich bis" v="18:04" P={P} />
        </div>

        <div style={{ marginTop: 12, display: 'flex', gap: 6 }}>
          <motion.span data-undo whileHover={{ scale: 1.02 }} transition={SPRING}
            style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontFamily: SANS, fontSize: 11.5, fontWeight: 600, padding: '7px 13px', border: `1.5px solid ${P.ink}`, color: P.ink, cursor: 'pointer' }}>
            <IconRotateClockwise size={12} stroke={2} /> rückgängig
          </motion.span>
          <span style={{ fontFamily: SANS, fontSize: 11.5, padding: '7px 13px', border: `1px solid ${P.line}`, color: P.faint }}>schließen</span>
        </div>
        <div style={{ marginTop: 9, fontFamily: MONO, fontSize: TEXT.meta, color: P.faint }}>Der Endzustand bewegt sich nicht mehr; nur der Zeiger auf der Rückgabefrage.</div>
      </div>
    )}
  </Stage>
)

/* --------- 10 · Favorit dunkel (Vollbild dunkel) ------------------------- */
const Buehne10 = () => (
  <Stage n="10 · Favorit dunkel" note="04 als Vollbild — exakt gleiche Geometrie, invertiert" nurDunkel alleinanzeige>
    {(_dunkel, P) => (
      <ApprovalCard P={P}
        icon={<IconPackage size={13} stroke={2} />}
        title="System-Paket installieren?"
        sub="Systemänderung, doppelt abgefragt. Nach der Freigabe fragt sudo selbst nach dem Passwort — im Terminal, nie im Chat."
        rows={[
          { k: 'Paket', v: 'jq 1.17.1' },
          { k: 'Quelle', v: 'fedora-updates' },
          { k: 'Betroffene Dienste', v: 'keine' },
          { k: 'Passwortabfrage', v: 'ja — vom System, im Terminal' }
        ]}
        cmd="dnf install -y jq"
        footer="Weg bearbeiten und dann neu fragen — oder im Chat eine Alternative ohne Installation anfragen."
        options={[
          { label: 'zulassen', note: 'jetzt installieren', primary: true, detail: 'dnf läuft einmal, im dunklen Vollbild; danach fragt das System selbst nach dem Passwort.' },
          { label: 'nicht jetzt', note: 'später erneut fragen', detail: 'Keine Installation; der Agent notiert den Ausstand und verfährt ohne jq.' },
          { label: 'ablehnen', note: 'gar nicht', detail: 'Weg ohne System-Änderung vorgeschlagen, z. B. ein kleines Eigen-Skript.' }
        ]}
      />
    )}
  </Stage>
)

/* ------------------------------------------------------------ Export */

export default {
  '01 · Standardfreigabe: Titel, drei Wege, 20 s Restzeit mit Balken': () => <Buehne01 />,
  '02 · Gefährliche Aktion: Datei überschreiben; ablehnen hervorgehoben': () => <Buehne02 />,
  '03 · Befehl im Terminal: Monospace-Befehl, Arbeitsordner, läuft als willy': () => <Buehne03 />,
  '04 · Systemänderung: Paket installieren; Passwortabfrage, nicht jetzt': () => <Buehne04 />,
  '05 · Kostenfreigabe: Anbieter, Kosten, Obergrenze; Fortsetzen/Abbrechen': () => <Buehne05 />,
  '06 · Datei schreiben: Pfad, Größe, überschreiben, Vorschau': () => <Buehne06 />,
  '07 · Sammelfreigabe: 3 Anfragen; einzeln abhaken; alle zulassen / keine': () => <Buehne07 />,
  '08 · Rückfrage mit Eingabe: Frage, Textfeld, Senden': () => <Buehne08 />,
  '09 · Freigabe erteilt: Endzustand mit Häkchen und rückgängig (keine Animation)': () => <Buehne09 />,
  '10 · Favorit dunkel: 04 im Vollbild dunkel': () => <Buehne10 />
}
