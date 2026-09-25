# Browser-Upgrade: Umsetzungsstand zu `docs/masterplan/07-BROWSER-UPGRADE.md`

Stand: 25.09.2026. Der Plan ist in `STARTPROMPT.md` als **bestätigte Empfehlung** an Hermes
beauftragt. Dieser Stand beschreibt, was davon implementiert und was offen ist.

## 1. Inventar (§9.1)

Vorhandene Bausteine, geprüft und **wiederverwendet** statt neu gebaut:

| Baustein | Zeilen | Verwendung |
|---|---|---|
| `src/skaldir/adapter_mcp.py` | 2373 | MCP-Client, Schema-Validierung, Toolaufruf — **keine zweite MCP-Schicht** (§12) |
| `src/skaldir/tool_archive.py` | 357 | Archivierung großer Browserergebnisse (§12) |
| `src/skaldir/act_service.py` | 3448 | vorhandener ACT-/Computer-Use-Pfad, `BROWSER_TITLE_RE`, `BROWSER_LAUNCH` |
| `src/skaldir/cu_host_session.py` | 1656 | Host-Eingabe, `_child_env` |
| `src/skaldir/verify_flows.py` | 2052 | Playwright-Prüfpfad (isolierter Chromium-Context) |
| `src/skaldir/desktop_lease.py` | neu | exklusive Desktopkontrolle, die §8 für Browseraktionen verlangt |
| `src/skaldir/stop_signal.py` | neu | Doppel-Escape, der §8 für Browseraktionen verlangt |

## 2. Setup (§9.2/§9.3/§9.4) — durchgeführt und gemessen

| Prüfung | Ergebnis |
|---|---|
| Werkzeug | `agent-browser` (Vercel), Lizenz **Apache-2.0** (aus `npm view` + `package.json` des Pakets) |
| Version | **`0.38.1`, exakt gepinnt** als `devDependencies` in `package.json` (kein `^`, kein `latest`) |
| Installation | **projektlokal** unter `node_modules/.bin/agent-browser` (§9.3 „isoliert zum Projekt“) |
| Node | v26.8.1, npm 11.19.0 |
| Fedora/Architektur | x86_64, glibc 2.43 → Paket `agent-browser-linux-x64` passt; ELF 64-bit LSB pie, `/lib64/ld-linux-x86-64.so.2` |
| Capabilities | `--version` antwortet; `--help` zeigt `open`, `read`, `click`, `dblclick`, `skills`, Sessions, Snapshot, Batch |
| Fedora-Pakete | keine Zusatzpakete nötig — das native Binary ist statisch in der Distribution |

### Befund zum Install-Skript (ehrlich festgehalten)

`agent-browser@0.38.1` bringt ein **`postinstall`-Skript** mit (321 Zeilen), das ein
plattformabhängiges natives Binary **aus dem Netz lädt**. npm hat es standardmäßig nicht
ausgeführt (`allowScripts` nicht freigegeben).

Der Befund entschärft den Fall: **die nativen Binaries sind bereits im npm-Tarball
enthalten** — `bin/` enthält `agent-browser-{darwin-arm64,darwin-x64,linux-arm64,linux-x64,
linux-musl-arm64,linux-musl-x64}`. Der Download im Postinstall ist ein Fallback für
Installationen, denen das fehlt. Auf diesem Fedora ist das richtige Binary
(`agent-browser-linux-x64`, 18 MB, ausführbar) also **ohne** Netzwerk-Nachzug vorhanden.

**Entscheidung:** Das Postinstall-Skript wurde **nicht** freigegeben. Der Zustand reicht für
den Betrieb. Wer es freigeben will, sollte zuerst prüfen, ob der Download einem Hash-Check
unterliegt. Das ist als offener Punkt geführt, nicht still erledigt.

## 3. Implementiert

`src/skaldir/browser_layer.py` (neu):

- **§4 — getypte Schnittstelle**: `BrowserLayer` mit den neun Operationen
  `browser.discover|attach|tabs|observe|act|read|batch|verify|detach`. Jeder Aufruf trägt
  Run-ID, Session-ID, Tab-ID, Deadline und Abbruchsignal; jedes Ergebnis Status,
  **tatsächliches Backend**, Dauer, Vorschau, Belegreferenzen und eine Fehlerklasse
  (`ErrorClass` als Enum, nie ein Freitext). Nur tatsächlich unterstützte Operationen
  werden angeboten — der Rest verweigert **namentlich**.
- **§6 — Fensterflut**: `SessionManager` mit der Zustandsfolge *discover → attach → ready →
  act*. Ein **interprozessuales Lock** (`fcntl.flock`) serialisiert Starts; `ensure` startet
  nur, wenn keine passende Instanz existiert **und** Neuanlage zur Aufgabe passt. Ein
  Starttimeout führt zu `unconfirmed` und Diagnose — nie zum nächsten identischen Launch.
  „Fenster nicht beobachtbar“ ≠ „Browser existiert nicht“. Tabs tragen `owner`
  (`user|agent`); `close_agent_tabs` schließt **nie** einen Nutzer-Tab, und es gibt
  bewusst **kein `close --all`**.
- **§5 — Referenzen**: `Observation` ist an (Session, Tab, Revision) gebunden.
- **§9.2/9.4 — Probe**: `agent_browser_probe()` ist messend, installiert **nichts**, findet
  die projektlokale gepinnte Installation vor PATH und verweigert einen Eintrag, der seine
  Version nicht nennen will (§9.2: unversionierte Installation ist genau das Verbotene).

CLI: `skaldir browser probe|discover|ensure|tabs|detach` mit `cli_contract`-Einträgen.

## 4. Getestet

`tests/test_browser_layer.py` — 25 Tests. Davon Abnahmen des Plans:

| Plan-Test | Was hier geprüft wird | Stufe |
|---|---|---|
| **BR-01** | zehn „Browser bereitstellen“-Aufrufe → **genau ein** Start, neun `reused` | Vertrag |
| **BR-02** | `unconfirmed` löst keinen weiteren Start aus, sondern Diagnose | Vertrag |
| **BR-13** | `close_agent_tabs` lässt Nutzer-Tab stehen; kein `close --all` | Vertrag |
| §4 | Ergebnisvertrag vollständig; Fehlerklasse als Enum; Operationsvokabular | Vertrag |
| §5 | Beobachtung an (Session, Tab, Revision) | Vertrag |
| §9.2/9.4 | fehlendes Werkzeug → exakter Installbefehl; Probe führt nichts aus; unversioniert → verweigert | Vertrag |

Plus Regression: `tests/test_desktop_lease.py`, `test_desktop_indicator.py`,
`test_tui_layout.py`, `test_stop_signal.py`, `test_no_orphan_modules.py`.

## 5. Offen — nicht als erledigt gemeldet

| Punkt | Warum |
|---|---|
| **BR-03 … BR-12, BR-14, BR-15** | brauchen einen echten Browserlauf bzw. echte Zen-Sitzung. `BrowserLayer.act/read/verify` verweigern **bewusst** mit `unsupported`, statt etwas zu tun, das nicht geprüft ist. |
| **§3 Zen** | „Vorhandenes Zen im echten Nutzerprofil“ ist ein **eigenes verpflichtendes Abnahmeziel**. Chromium-Erfolg erfüllt es nicht. Ein Anschlussversuch an Willys laufendes Zen steht aus. |
| **Transport** | `AgentBrowserBackend.launch` meldet ehrlich „no launch verb is wired yet“ und lässt die Sitzung `unconfirmed`, statt einen Start zu erfinden. |
| **§9.7 Browser Harness** | bewusst zurückgestellt bis nach einem vertikalen Prototyp (Plan-Vorgabe). |
| **Postinstall-Skript** | nicht freigegeben, siehe §2. |
