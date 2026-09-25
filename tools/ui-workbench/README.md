# Skaldir UI Workbench (React Cosmos)

Vollständige Widget-Sammlung der Skaldir-Agent-Oberfläche zum isolierten Ansehen —
ohne die App zu starten. Soll-Quelle: `Documents/Hermes_Skaldir_Agent_Docs/UI-CONTRACT.md`
(Owner-Matrix) + `Design_Docs/Skaldir_Release_Paket/01_PRODUCT_UX_DESIGN.md` (Tokens).

## Starten

```bash
cd ~/projects/gods-voice/tools/ui-workbench
./node_modules/.bin/cosmos --port 5001 --hostname 127.0.0.1
```

Dann **http://127.0.0.1:5001** öffnen (Playground); der Renderer läuft automatisch auf
Port 5050. Beenden mit `Ctrl+C`.

## Die sieben Fixtures

| Fixture | Inhalt |
|---|---|
| `ThinkingOrbs` | alle **neun Verben** mit dem **echten** Orb aus `thinking-orbs@0.3.1` — Light, Dark, Größen 16/22/32, plus „Endzustände OHNE Orb" |
| `GvOrb` | die **Host-Tür des Produkts** (`app-overlay/gv/gv-orb.tsx`): Fallback ohne Plugin-Runtime = statischer Punkt; Endzustände, die nichts rendern dürfen |
| `Primitives` | shadcn/ui aus dem App-Spiegel: Button-Varianten/-Größen, `ErrorState` in der F-07-Form |
| `Widgets-Tokens` | Farben hell+dunkel, Radien 10/18/16, Typo-Skala mit 11-px-Untergrenze, Fokus-Regeln (Ring an Controls, keiner am Editor) |
| `Widgets-Chat` | die **16 Zeilen der Owner-Matrix** als Widgets (Orb nur wo vorgeschrieben), Fehler-/Warte-/Abschlusszustände im Detail, **F-07-Absturzfläche** isoliert |
| `Widgets-Composer` | **acht Composer-Zustände** (Ruhe, Fokus, mehrzeilig, Anhänge, Lauf mit Stop, Warten, abgedockt 19,5 rem, ohne Provider) + Anhang-Chips |
| `Widgets-Shell` | Goal-/Plan-Zeile in vier Zuständen (inkl. **Goal-Wechsel während der Arbeit** und React-Flow-Zielbild), Verification mit kompakten Ergebniszeilen, Navigation, Suche, Session-Zeilen, Statusleiste in drei Varianten, Dark-Gegenprobe |

## Grenzen — ehrlich

* **Echt** sind: Orb (`thinking-orbs`), Host-Tür (`gv-orb`), shadcn/ui-Komponenten (`button`, `error-state`) — sie werden direkt aus Paket bzw. Spiegel importiert.
* **Mockups** sind die Chat-/Shell-/Composer-Widgets: die Originale brauchen den
  `@assistant-ui`-Runtime-Kontext der App. Sie bilden die dokumentierte Form nach, sind also
  Design-Referenz, **kein** Laufzeitbeweis. Laufzeitbeweise liefert weiterhin der
  CDP-/Playwright-Weg des E2E-Audits.
* Read-only: es wird **nichts** in `~/.hermes/godsv/app-src` geschrieben (Hartlink-Baum).
  Alias-Ziele siehe `vite.config.ts`.

## Herkunft

Aufgesetzt am 17.09.2026 als Ersatz für den blockierten Storybook-MCP (kein Storybook im
Projekt, `@storybook/mcp` ist eine Bibliothek ohne Executable). Verifiziert: alle sieben
Fixtures werden vom Renderer mit HTTP 200 transformiert (8 089 / 7 684 / 18 121 / 39 974 /
37 846 / 49 046 / 23 083 Bytes).
