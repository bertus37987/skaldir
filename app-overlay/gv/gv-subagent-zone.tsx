/**
 * Die Subagenten-Zone — zweite Zone der linken Spalte, unter der Sitzungsliste.
 *
 * Auftrag (Owner, 18.09.): „das subagent ueberwachpanel links an die bar wo auch die chatSessions
 * sind befestigen, man soll es auch verschieben koennen". Ort und Bauart sind belegt:
 * HERMES-ABGLEICH/04-panels.md §D.2 und AUFBAU/04 §2 (Abnahme 4: „eigene Zone in der linken Spalte
 * unter der Sitzungsliste, nicht im Scrollbereich der Liste").
 *
 * Fakten, die diese Datei einhält:
 *
 *  * **Ort kommt vom Andock-Hinweis, nicht vom Preset.** `dock: { pane: 'sessions', pos: 'bottom' }`
 *    lässt `adoptContributedPanes` (tree/store.ts:1465-1506) eine Column-Split neben die
 *    Sitzungsliste setzen (model.ts:288-294). Kein Eintrag in layout-presets.ts.
 *  * **Beide Zustände sind Kachel-Eigenschaften, kein Sondercode.** Angedockt = `placement: 'left'`,
 *    frei = `placement: 'floating'` (renderer/floating-panes.tsx:29-40: Kopf ist der Griff,
 *    Position persistiert). Der Pin im Kopf ist ein POSITIONS-Schalter — kein Sichtbarkeitsschalter
 *    für Laufgebundenes (AUFBAU/04:269-271, 04-panels.md:266-268).
 *  * **Eine Datenquelle, kein zweiter Abfrager.** Gelesen wird `$subagentsBySession`; `subagent.list`
 *    ruft weiterhin NUR use-subagent-snapshot.ts:37 (ein Aufruf in status-stack/index.tsx:100).
 *    Gezählt wird mit `activeSubagentCount` — derselbe Zähler wie die Statuszeilen-Pille
 *    (use-statusbar-items.tsx:135-137), also keine zweite Wahrheit.
 *  * **Nur offene Zeilen, kein Archiv.** Zeilen-Maßstab (Owner-Entscheidung 18.09.): ausschließlich
 *    laufende/wartende Kinder (`running` | `queued`) — was fertig ist, verschwindet; die Fläche zeigt
 *    den laufenden Zustand, nicht die Historie (AUFBAU/12 §1.2 E11: „nur offene Zeilen, `−` nimmt
 *    heraus, ohne Wiederholungsliste").
 *  * **Startzustand = eingeklappt im Sinne von „nur Kopf".** Die Zone startet sichtbar, aber ohne
 *    Inhalt: Kopf + genau eine Leerzeile (`no subagent open`), kein Archiv, keine Scheinzeile.
 *    Wer stattdessen die 28-px-Leiste als Startzustand will, setzt in `SUBAGENT_ZONE_DOCK` EIN Wort
 *    mehr: `defaultCollapsed: true` (store.ts:1509-1516 wendet es beim Andocken an).
 *  * **Read-only.** Kein Steuern, kein Unterbrechen, kein Text an ein Kind — das bleibt im
 *    Composer-Detail (subagent-controls.tsx:21-49; INV:26 „Pausieren/Redirect nie aus der UI").
 *  * **Kein Orb ohne Lauf.** Der Leerzustand rendert `GvOrb` nicht (AUFBAU/04:257).
 *  * **Keine neuen i18n-Tasten.** Zeilen-Arien kommen aus `t.agents.*` (i18n/en.ts:1846-1885);
 *    die zwei Flächenwörter sind die der Vorlage (LeerPanels.tsx:166,204) und jetzt ENGLISCH
 *    (Owner-Vorgabe: Englisch als App-Sprache). Welle 2 (D2) — deutsche Wörter hinter
 *    englischen Schlüsseln — ist damit zurückgenommen; eine `de`-Locale darf nicht entstehen.
 *  * **Keine neuen Zeichen-Importe.** Griff = `Codicon name="gripper"` (Vorbild tree-group.tsx:787),
 *    Einklappen = `Codicon name="chevron-down"` (Vorbild tree-group.tsx:552), Pin = `Pin` aus der
 *    kuratierten Einzelquelle (`lib/icons.ts:96,229`). Kein Lucide, kein zusätzliches Paket.
 *  * **`−` bricht nichts ab.** Es blendet die ZEILE aus (nur Anzeigezustand, nicht persistiert).
 */
import { useStore } from '@nanostores/react'
import { useEffect, useMemo, useState } from 'react'

import { adoptContributedPanes, registerPaneCloser, removeTreePane, setPaneCollapsed } from '@/components/pane-shell/tree/store'
import { startPaneDrag } from '@/components/pane-shell/tree/renderer/drag-session'
import { ActivityTimerText } from '@/components/chat/activity-timer-text'
import { Codicon } from '@/components/ui/codicon'
import { GvOrb } from '@/components/ui/gv-orb'
import { registry } from '@/contrib/registry'
import { useViewedInterval } from '@/hooks/use-viewed-interval'
import { useI18n } from '@/i18n'
import { Pin } from '@/lib/icons'
import { readKey, writeKey } from '@/lib/storage'
import { cn } from '@/lib/utils'
import { $subagentsBySession, activeSubagentCount, allSubagents, type SubagentProgress } from '@/store/subagents'

/** Kachel-ID. EINE Quelle: die Registrierung (controller.tsx) und der Pin-Schalter hier lesen sie. */
export const SUBAGENTS_PANE_ID = 'subagents'
export const SUBAGENTS_ZONE_TITLE = 'Subagents'

/**
 * Der Angedockt-Zustand. `width/minWidth/maxWidth` sind ABSICHTLICH dieselben Zahlen wie die
 * Sitzungskachel (controller.tsx:177-179, store/layout.ts:20-21): die Klammer der Spalte wird aus
 * ihren Kindern gelesen (tree-split.tsx:230-234) — zwei verschiedene Werte ergäben je nachdem,
 * welche Zone den Spaltenrand hält, zwei verschiedene Zieh-Grenzen.
 * `height/maxHeight` sind die Zahlen des Terminal-Decks (controller.tsx:212-213).
 */
export const SUBAGENT_ZONE_DOCK = {
  placement: 'left',
  collapsible: true,
  dock: { pane: 'sessions', pos: 'bottom' },
  height: '20vh',
  maxHeight: '80vh',
  width: '237px',
  minWidth: '237px',
  maxWidth: '360px'
}

/** Der Frei-Zustand: Position/Einklappen persistiert floating-panes.tsx:37,76-81. */
const SUBAGENT_ZONE_FLOAT = { placement: 'floating', anchor: 'top-right', width: '236px', height: '220px' }

const FREI_KEY = 'hermes.desktop.godsv.subagentsFloating'

const istOffen = (item: SubagentProgress) => item.status === 'running' || item.status === 'queued'

/* „Ein Zustand, zwei Flächen" — der Pin schreibt in DIESELBE Kachel-Eigenschaft, die die Aufnahme
   liest (registry.put ersetzt gleiche IDs an Ort und Stelle, registry.ts:105-108). Der Modus wird
   NICHT beim Abnehmen (unmount) zurückgesetzt: er ist ein stehender Zustand, kein Renderzustand. */
let modusGesetzt = false

function setzeModus(frei: boolean) {
  modusGesetzt = true
  registry.register({
    id: SUBAGENTS_PANE_ID,
    area: 'panes',
    title: SUBAGENTS_ZONE_TITLE,
    data: frei ? SUBAGENT_ZONE_FLOAT : SUBAGENT_ZONE_DOCK,
    render: () => <SubagentZonePane />
  })

  if (frei) {
    // ohne Abschieds-Vermerk: der nächste Andock-Vorgang soll sauber aufnehmen
    // (tree/store.ts:858-865 — für Flächen, deren Lebenszyklus ein Besitzer steuert)
    removeTreePane(SUBAGENTS_PANE_ID)
  } else {
    adoptContributedPanes()
  }
}

/* Die stehende Fläche hat kein „weg": die Schließwege (⌘W/✕) klappen ein statt zu entfernen.
   Vorbild: logs, controller.tsx:667-669. */
registerPaneCloser(SUBAGENTS_PANE_ID, () => setPaneCollapsed(SUBAGENTS_PANE_ID, true))

export function SubagentZonePane() {
  const { t } = useI18n()
  const bySession = useStore($subagentsBySession)
  const [frei, setFrei] = useState(() => readKey(FREI_KEY) === '1')
  const [versteckt, setVersteckt] = useState<ReadonlySet<string>>(() => new Set<string>())
  const [nowMs, setNowMs] = useState(() => Date.now())

  // Übersicht = alle Sitzungen (Maßstab der Statuszeilen-Pille, use-statusbar-items.tsx:135-137),
  // aber nur OFFENE Kinder (Owner-Entscheidung 18.09.: kein Archiv).
  const offen = useMemo(() => allSubagents(bySession).filter(istOffen), [bySession])
  const sichtbar = useMemo(() => offen.filter(item => !versteckt.has(item.id)), [offen, versteckt])

  useViewedInterval(() => setNowMs(Date.now()), 1000, sichtbar.length > 0)

  // Ein gespeicherter Frei-Zustand wird beim Start genau EINMAL angewandt. Der Angedockt-Zustand
  // braucht keine Anwendung: die Host-Registrierung ist beim Start bereits angedockt.
  useEffect(() => {
    if (frei && !modusGesetzt) {
      setzeModus(true)
    }
  }, [frei])

  const umschalten = () => {
    const naechster = !frei

    writeKey(FREI_KEY, naechster ? '1' : '0')
    setFrei(naechster)
    setzeModus(naechster)
  }

  const zeile = (item: SubagentProgress) => (
    <div
      className="flex min-w-0 items-center gap-2 rounded px-1.5 py-1 hover:bg-(--ui-control-hover-background)"
      data-subagent-row={item.id}
      key={item.id}
    >
      <GvOrb
        ariaLabel={item.status === 'queued' ? t.agents.queued : t.agents.running}
        className="shrink-0 text-(--ui-purple)"
        size={16}
        state={item.status === 'queued' ? 'breathing' : 'weaving'}
      />
      <span className="min-w-0 flex-1">
        <span className="block truncate font-mono text-[0.6875rem] text-(--ui-text-primary)">{item.goal}</span>
        <span className="block truncate font-mono text-[0.6875rem] text-(--ui-text-tertiary)">
          {item.stream.at(-1)?.text || (item.status === 'queued' ? t.agents.queued : t.agents.waitingActivity)}
        </span>
      </span>
      <ActivityTimerText
        className="shrink-0 font-mono text-[0.6875rem]"
        seconds={Math.max(0, Math.floor((nowMs - item.startedAt) / 1000))}
      />
      <button
        aria-label={`${item.goal} remove`}
        className="shrink-0 rounded px-1 text-(--ui-text-quaternary) hover:text-(--ui-text-primary)"
        onClick={() => setVersteckt(vorher => new Set(vorher).add(item.id))}
        title="Hide row (display only — not an abort)"
        type="button"
      >
        −
      </button>
    </div>
  )

  return (
    <div className="flex h-full min-h-0 flex-col" data-slot="gv-subagent-zone" data-zone-mode={frei ? 'free' : 'bar'}>
      <div className="flex shrink-0 items-center gap-1 border-b border-border px-2 py-1">
        <button
          aria-label="Move subagents panel"
          className="grid size-5 shrink-0 cursor-grab place-items-center rounded text-(--ui-text-quaternary) hover:text-(--ui-text-primary)"
          data-slot="gv-subagent-zone-grip"
          onPointerDown={event => startPaneDrag(SUBAGENTS_PANE_ID, event, undefined, undefined, SUBAGENTS_ZONE_TITLE)}
          style={{ touchAction: 'none' }}
          title="Drag: undock the panel · Dock: back to the bar"
          type="button"
        >
          <Codicon name="gripper" size="0.8125rem" />
        </button>
        <span className="min-w-0 truncate font-mono text-[0.6875rem] tracking-[0.08em] text-(--ui-text-tertiary)">
          SUBAGENTS · {activeSubagentCount(offen)} OPEN
        </span>
        <button
          aria-label="Collapse zone"
          className="ml-auto grid size-5 shrink-0 place-items-center rounded text-(--ui-text-tertiary) hover:bg-(--ui-control-hover-background) hover:text-foreground"
          onClick={() => setPaneCollapsed(SUBAGENTS_PANE_ID, true)}
          title="Collapse zone"
          type="button"
        >
          <Codicon name="chevron-down" size="0.75rem" />
        </button>
        <button
          aria-label={frei ? 'Dock subagents panel' : 'Undock subagents panel'}
          className={cn(
            'grid size-5 shrink-0 place-items-center rounded hover:bg-(--ui-control-hover-background)',
            frei ? 'text-foreground' : 'text-(--ui-text-quaternary)'
          )}
          data-slot="gv-subagent-zone-pin"
          onClick={umschalten}
          title={frei ? 'Dock subagents panel' : 'Undock subagents panel'}
          type="button"
        >
          <Pin size="0.875rem" />
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-1" data-slot="gv-subagent-zone-body">
        {sichtbar.map(zeile)}
        {sichtbar.length === 0 && (
          <div className="px-2 py-2 font-mono text-[0.6875rem] text-(--ui-text-quaternary)">no subagent open</div>
        )}
      </div>
    </div>
  )
}
