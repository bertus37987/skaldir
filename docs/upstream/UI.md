# Skaldir Agent — UI-Integration der nativen Features

Alle nativen Skaldir Agent-Features und ihr Ort in der Oberfläche. Zwei Surface-Bausteine
tragen die Marke, der Rest ist die offizielle Hermes-Designsprache (Theme-Tokens,
Plattform-Komponenten): **kein eigener Look, nur erweitert**.

## Branding (Markenidentität)

| Baustein | Wirkt wo | Mechanik |
|---|---|---|
| **Skin `gods-voice`** (gold) | CLI, TUI **und** Desktop-GUI | Eine YAML in `<hermes-home>/skins/gods-voice.yaml`, aktiviert über `display.skin`. Färbt alle Surfaces; `branding.agent_name` ersetzt „Hermes Agent". |
| **App-Instanz** | Desktop-Fenster, Taskbar, App-Menü | Eigener `userData` (eigener Single-Instance-Lock), `HERMES_DESKTOP_APP_NAME`, `--class=SkaldirAgent`, eigenes Icon + `.desktop` |
| **CLI-Welcome** | `godsvoice`-Start | Logo-Artwork (eigene Farben), Titel/Subtitle, Befehlsübersicht |
| **Chat-Branding** | Empty-Chat der App | `CHAT_EMPTY_AREA`: Wordmark „SKALDIR AGENT" (Skin-Farbe) + Feature-Karten + Panel-Hinweis |

## Native Features → UI

| # | Feature | UI-Ort | Inhalt | Backend |
|---|---|---|---|---|
| 1 | **Live-Steering (Pausen)** | Panel → *Live* + *Pausen* | KPIs (aktiv/zugestellt), Pausen-Historie (Session, Status, Versuche, TTL), Erklärkette Pause→Research→Redirect→Resume | `/summary` |
| 2 | **Completion-Guards** | Panel → *Live* + *Pausen* | Guard-Check-Zähler, Claim-Zähler, gefilterter Ereignis-Kanal | `/summary` |
| 3 | **Interventionen / Redirects** | Panel → *Pausen* | Letzte Pausen-/Guard-/Redirect-Ereignisse mit Payload-Auszug | `/summary` |
| 4 | **Memory — Incidents** | Panel → *Memory* | Fehlerklassen (signiert, dedupliziert) mit Zähler, Status, Confidence | `/memory` |
| 5 | **Memory — Notizen** | Panel → *Memory* | Recherche-Aussagen mit Quelle, Trust, Status | `/memory` |
| 6 | **Skill-Mining** | Panel → *Skills* | Kandidaten mit Lifecycle-Status, Wiederholungen/Sessions, Anwendungs-/Erfolgszähler | `/skills` |
| 7 | **Treasury — Spends** | Panel → *Treasury* | Testgeld-Buchungen (Betrag, Empfänger, Zweck, Status) — kein Zahlungspfad | `/treasury` |
| 8 | **Treasury — Grants** | Panel → *Treasury* | `secret://`-Handles mit Status, Tool-Bindung, **Revoke** — nie Roh-Secrets | `/treasury`, `/treasury/grants` |
| 9 | **Allowance** | Panel → *Live* + *Treasury* | Freigabestatus-Aggregat | `/summary` |
| 10 | **System / Grenzen** | Panel → *System* | Runtime-Pfad, Snapshot-Zeit, „read-only"-Grenzen, Engine-Hinweis | `/summary` |
| 11 | **Event-Stream** | Panel → *Live* | Letzte Ereignisse typisiert + formatiert | `/summary` |
| 12 | **Deployment-Status** | CLI `godsvoice desktop status` | Launcher, Icons, Skin, Instanz, Panel, CLI | lokal |
| 13 | **Model Selection** | Panel → *Models* | Hermes-Herkunft (Default + Fallback, read-only), Rollen Watcher/Investigator/Judge/Fallback mit Provider-/Modell-Dropdowns (Hermes-Registry), **Test model** (auth/response/tool-call/Latenz), **Speichern** → `models.yaml` | `/models`, `/models/roles`, `/models/test` |
| 14 | **Tool-Registry** | Panel → *Tools* | Installed (Typ/Status/Herkunft/letzter Test/Handles/Risiko), Build Queue (echte Stufen), MCP-Server; Klick → Detail | `/tools`, `/tools/{name}` |
| 15 | **Tool-Detail & Aktionen** | Panel → *Tools* → Detail | Manifest (Secrets als Handles, Netzwerk, Dependencies, Tests, Risiko), Grants, Build-Historie; Aktionen **Test / Enable / Disable / Gates-Recheck / Grant / Revoke** (audit-geloggt) | `/tools/{name}/action`, `/treasury/grants*` |
| 16 | **Credentials anlegen** | Panel → *Treasury* → „+ Add credential" | Service/Name/Wert → Handle + Maske; Wert danach nie lesbar; „entfernen"; Vault-Sperre wird erklärt, wenn `GODSV_VAULT_KEY` fehlt | `/treasury/credentials*` |
| 17 | **Strict Completion Supervisor** | Panel → *Completion* | Goal + Requirement-Checkliste ✓/✗, Produkt-Status-Grid (8 Gates), **Verify now**, „Begin goal", **User-Override** (nie VERIFIED), Completion-Timeline (Requests/Rejections/Verdicts), Metrics | `/completion`, `/completion/verify`, `/completion/goal`, `/completion/override` |
| 18 | **Capability-Mining** | CLI `godsvoice tools mine / candidates / promote` | Wiederkehrende Capabilities → Kandidaten (skill, cli, mcp, testing_tool) mit occurrences/sessions/workaround/secrets; Promotion in die bestehende Gates-Pipeline; Testing-Tool-Negativtest | lokal (JSONL) |

**Mutations-Regeln (Panel):** administrative Skaldir Agent-Aktionen (Modelle, Tool-
Enable/Disable/Test/Recheck, Credential anlegen/entfernen, Grants) sind erlaubt,
aber: eindeutiger POST-Endpunkt pro Aktion, Origin-Guard (nur lokal), serverseitige
Validierung, Confirmation bei riskanten Aktionen, vollständiges Audit-Event,
**niemals Secret-Werte zurück an die UI**, keine Shell-Strings aus der UI
(argv-fix, Name serverseitig validiert). Alles übrige bleibt read-only — Pausieren,
Redirect, Spend-Entscheidungen laufen nie aus der UI.

## Thinking-Orbs — der verbindliche Working-Indikator

**Es gibt genau einen sichtbaren Thinking-/Working-Indikator: `thinking-orbs`.** Kein
eigener Spinner, keine zweite Animation, keine konkurrierende Status-Grafik — in Chat
*und* Panel. Die alten `●`/`◉`/`…`-Working-Glyphen sind entfernt.

Der Indikator ist eine **Projektion**, kein zweites Statusmodell: er liest Laufzeit-Fakten,
die die App ohnehin erzeugt, und bildet sie auf die neun Bibliotheks-Verben ab. Es wird
kein Zustand erfunden und **keine private Chain-of-Thought gezeigt** — die Beschriftungen
benennen beobachtbare Arbeit („Working on the task"), nie einen Denkverlauf.

| Laufzeit-Fakt (Quelle) | Orb-Verb |
|---|---|
| `state == WORKING` (Completion-Record) | `working` — Tool-Ausführung/allgemeine Arbeit |
| `state == WORKING` **und** `streaming` (Transkript) | `composing` — Antwortgenerierung |
| `state == VERIFYING` | `solving` — Prüfung/Analyse |
| Build-Stufe `gates`, `dependency_inspection`, `verify`, `validate`, `decide` | `solving` |
| Build-Stufe `researching`, `search`, `inspect` (und `state == RESEARCHING`) | `searching` — Research/Retrieval/Memory |
| Build-Stufe `generating`, `manifest`, `contract` | `shaping` — Artefakt/Design entsteht |
| Build-Stufe `adapter`, `publish`, `bind`, `register` | `connecting` — Capability/API verdrahten |
| Build-Stufe `observe` | `working` |
| Build-Stufe `requested`, `need`; `state == BLOCKED_USER/BLOCKED_EXTERNAL`; aktive Pause; kein Record | `breathing` — Warten/Queue/Recovery |
| parallele Pfade zusammengeführt (Run-/Agent-Merges) | `weaving` (reserviert, sobald sichtbar) |
| `VERIFIED_COMPLETE`, `INCOMPLETE`, `FAILED_UNRECOVERABLE`, `ready`, `failed`, … | **kein Orb** — Endzustand, statisches ✓/✕ |
| unbekannter `state` | **kein Orb** — nicht „working" raten (Owner-Regel: nie working zeigen, wenn nicht gearbeitet wird) |
| kein Goal (`NO GOAL`, `VOICE`) | **kein Orb** — es gibt keine Phase zu projizieren |

Flächen, die denselben Indikator tragen (ein Baustein `GvOrb`, eine Projektionsfunktion
`gvOrbState`/`gvOrbBuildStage`): Live-Karte im Transkript, Composer-Strip, Panel-Kopfzeile,
Gate-Karte, Capability-Request-Karten und die Build-Queue. Größen: **20 px inline** ist der
Standard; **64 px** nur für die wirklich zentrale, lange Phase (Live-Karte ohne Goal — dort
*ist* die Karte die Arbeit). Light/Dark löst die Bibliothek selbst auf (`theme="auto"`);
`prefers-reduced-motion` rendert ein statisches Standbild.

**Herkunft/Regeneration.** `thinking-orbs` (MIT) wird als `dependency` geführt
(`package.json`), aber in `integrations/hermes/godsv_plugin/desktop/plugin.js` **inline
vendored**: der Runtime-Plugin-Loader evaluiert `plugin.js` als Blob-Modul, und eine
Blob-URL ist keine hierarchische Basis — ein relativer Import kann dort nicht auflösen
(genau deshalb ist `plugin.js` eine einzige Datei); npm ist im Renderer ebenfalls nicht
erreichbar. Erzeugt wird der Block ausschließlich maschinell:

```bash
npm install && node tools/vendor_thinking_orbs.mjs          # Block neu erzeugen
node tools/vendor_thinking_orbs.mjs --check                 # veraltet? → Exit 1
```

**Nachweis.** `node tools/check_desktop_plugin.mjs` pinnt das Verhalten: pro Laufzeit-Zustand
genau ein Orb mit dem erwarteten Verb (WORKING→`working`, streaming→`composing`,
VERIFYING→`solving`, BLOCKED_USER→`breathing`), Endzustände und unbekannte Zustände **ohne**
Orb, Label ohne CoT-Vokabular, sowie „kein Legacy-Glyphe (`●`/`◉`) mehr gerendert".


| Ersetzt durch „Skaldir Agent" | Bleibt „Hermes" (begründet) |
|---|---|
| `branding.agent_name` (TUI/CLI-Header, Skins) | „Hermes Engine" in Panel-Footer, System-Tab und Chat-Branding (technische Herkunft) |
| Desktop `APP_NAME` → Fenstertitel/Menü/About | Plugin-Settings-Seite (läuft in der Hermes-App: Settings → Plugins) |
| Panel-Titel, Sidebar-Label, ⌘K-Palette | Doku/Repo: `integrations/hermes/…`, Env-Präfixe `HERMES_*` |
| CLI-Welcome (Titel/Subtitle) | Logs/Pfade (`hermes desktop --build-only`, `~/.hermes/…`) |
| Chat-Branding (Wordmark) | — |
