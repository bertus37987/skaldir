# IMPLEMENTATION-PLAN — Tool-Builder & Docker-Etappe (Auftrag vom 14.09.2026)

**Quelle:** Owner-Masterprompt „Implementierungsauftrag für Hermes" (14.09.2026) — Tool-Builder,
der containerisierte Software in geprüfte, in der laufenden Hermes-Session nutzbare Fähigkeiten
verwandelt. Kette: Nutzeraufgabe → Bedarf erkennen → Bestand suchen → Container-Software
erschließen → CLI-/MCP-Adapter erzeugen → echte Funktion prüfen → automatisch bereitstellen →
im Auftrag verwenden → wiederverwenden.

**Status dieses Plans:** lebendes Dokument der Zentrale (nur Hauptthread schreibt).

---

## 0. Ausgangsstand (eigene Prüfung 14.09.2026)

- HEAD `a5b0726`, Branch `godsv-supervisor-hardening` = origin. Arbeitsbaum sauber bis auf
  untracked `outputs/e2e40/*` (Vorrunden-Artefakte, **unangetastet** — fremde Änderungen erhalten).
- **Docker fehlt** auf dem Rechner (kein Binary, kein Socket); Podman 5.8.4 vorhanden.
  Owner installiert `moby-engine` + `docker-compose` selbst (Freigabe via clarify 14.09., Option 1).
  Bis dahin: alle Docker-Live-Beweise **blockiert**, alles andere wird gebaut.
- Die §1-Lücken des Auftrags decken sich mit dem offenen Register aus dem Tools-Statusbericht
  (Panel-test unsandboxed; Registry-DB-Zwiespalt; Host-Live-Insertion; MCP-Auto-Config; Trace-Mining;
  Health ohne Event/Selektion; Compose-before-create; `tools draft` tot; `tools list` ohne Scope;
  CU/Runner-Dry-Runs).

## 1. Etappen (Auftrags-§0.9)

1. **Lücken schließen (§1)** — Welle 1 (B1–B5) + Welle 2 (§1.10 CU/Runner + Restverdrahtung).
2. **DockerRuntimeManager + Manifest v2 + Executor-Registry (§2/§3)** — Welle 2 (sobald Docker läuft).
3. **Automatischer Builder §4/§4a/§4b + Discovery §4c + Host-Kette §4d** — Welle 2–3.
4. **Referenztools §6** (SearXNG web_search, Pandoc-CLI, research_topic) — Welle 3.
5. **Bedienung §7 + Abnahme §8 (inkl. Discovery-Testset ≥30)** — Welle 3–4, Abschlussbericht.

## 2. Welle 1 — Blöcke, Auftragspunkte, Datei-Eigentum (exklusiv!)

| Block | Auftrag | Dateien (Eigentum) | Block-Doc |
|---|---|---|---|
| **B1** | §1.1 gemeinsame Ausführungs-/Berechtigungsgrenze; Panel-`test`-Sandbox | NEU `src/godsv/execution.py`; `integrations/hermes/godsv_plugin/dashboard/plugin_api.py`; `src/godsv/capability.py`; `src/godsv/gates.py` (nur wenn zwingend); NEU `tests/test_execution_boundary.py` | `docs/TOOLS-V2-EXECUTION-BOUNDARY.md` |
| **B2** | §1.5 Trace-Mining aus Live-Events (Dedupe, Cursor, Replay); §1.6 Health-Events + Selektions-API | `src/godsv/capability_mining.py`; `src/godsv/capability_health.py`; `src/godsv/events.py`; `src/godsv/nightshift.py`; NEU `tests/test_trace_mining.py`, `tests/test_health_events.py`; `tests/test_capability_mining.py` | `docs/TOOLS-V2-MINING-HEALTH.md` |
| **B3** | §1.7 Compose-before-create im Builder; §1.8 `draft_tool_spec` an vorhandenen Modellclient + Budget | `src/godsv/toolbuilder.py`; `src/godsv/tools.py`; `tests/test_toolbuilder.py`; `tests/test_tools_store.py` | `docs/TOOLS-V2-BUILDER-GAPS.md` |
| **B4** | §1.2 eine Registry-Wahrheit + nachvollziehbare Migration; §1.9 Scope/Version in CLI + Promotion-Prüfung | NEU `src/godsv/registry.py`; `src/godsv/cli.py`; NEU `tests/test_registry_context.py`; `tests/test_tool_scopes.py` (Ergänzung) | `docs/TOOLS-V2-REGISTRY.md` |
| **B5** | §1.3+§4d Live-Insertion an echter Host-Grenze; §1.4 MCP-Konfig-Automatik; §1.6 Health wirkt auf Auswahl(MCP-Surface) | `src/godsv/liveinsert.py`; `src/godsv/mcpserver.py`; `integrations/hermes/godsv_plugin/__init__.py`; `host-overlay/**`; `tests/test_capability_learning.py` (Liveinsert-Teil); NEU `tests/test_mcp_link.py` | `docs/TOOLS-V2-HOST-WIRING.md` |

**Nicht angefasst in Welle 1 (Eigentum anderer Blöcke/Etappen):**
`completion.py`, `computer_use.py`, `workflows.py` (→ §1.10, Welle 2) · `desktop.py`/`appbuild.py`/
app-overlay (→ Build-Themen) · `plugin.js` (Panel-UI nur wenn Welle-2-Bedarf) · `docs/`-Bestandsdateien
(PROJECT-STATE/FEATURE-MAP aktualisiert die Zentrale am Rundenende) · `~/.hermes/config.yaml` (nur über
die vorhandenen Mechanismen, nie direkt) · normale Hermes-Desktop-Instanz & GV-App (nie killen/neustarten).

## 3. Gemeinsame Regeln für alle Blöcke

- **Rot-first**, wo ein Fix das hergibt; Tests: `.venv/bin/python -m pytest <dateien> -p no:randomly`
  (pyproject setzt `-q` — **kein** zweites `-q`, sonst fehlt die Zählzeile); Zahl aus der Ausgabe zitieren.
  Lint: `.venv/bin/ruff check <geänderte Dateien>`.
- **Commits** klein und aussagekräftig, Präfix `feat(gapN)`/`fix(gapN)`; vor jedem Push `git pull --rebase`.
- **Datei-Eigentum strikt**: keine fremden Dateien editieren; Testdateien anderer Blöcke nicht anfassen.
- **Beweis-Standard:** jede Behauptung mit `Datei:Zeile` oder Testlauf-Ausgabe; Zahlen selbst zählen
  (grep), nie aus dem Gedächtnis; Unverifiziertes als Vorschlag markieren.
- **Live-Plugin-Guards dieser Maschine:** ein `[voice·pause]`-Block trifft genau EINEN Call →
  identischen Call erneut senden; `pre_tool_call plugin callback timed out` → ~60 s warten, dann
  identisches Kommando erneut; nie `2>/dev/null` bzw. `>` auf `/dev/*`; File-Tools bevorzugen.
- **Langläufe** als Hintergrundprozess starten und pollen (`process_manage`), nicht blockierend warten.
- **Docs** je Block: `docs/TOOLS-V2-<BLOCK>.md` — erst Skelett schreiben, dann abschnittsweise füllen
  (nie ein riesiger Schluss-Block); enthält Datei:Zeile, Testzahlen, Grenzen, Restpunkte.
- Kein Push/Publish von Tool-Inhalten nach außen; keine zweite Provider-Konfiguration; keine zweite
  Tool-Plattform; keine unbegrenzte rekursive Tool-Erzeugung.

## 4. Welle 2 — Blöcke (dispatcht 14.09. 19:33 als `deleg_189d8a3f`)

| Block | Auftrag | Datei-Eigentum (exklusiv) | Status |
|---|---|---|---|
| **B7** | §4a/§4b Builder-Zustandsmaschine + Reparatur + Evidenz; §4d-Registry-Seite (transaktionale Outbox `publish_outbox`, Event `tool_published`) | `toolbuilder.py`, `tools.py`, `events.py` (nur Kinds), NEU `buildflow.py`, `tests/test_buildflow.py` (NEU), `tests/test_toolbuilder.py`, `tests/test_tools_store.py` | läuft (task-0) |
| **B9** | §4c Discovery: reiche Metadaten, lexikalische/optionale semantische Suche, Eignung/Ranking, Trigger+Cache, Host-Brücke, Eval-Runner auf eingefrorenem Testset | `capability_mining.py`, NEU `discovery.py`, Plugin `__init__.py`, NEU `tools/gv_discovery_eval.py`, `tests/test_discovery.py` (NEU) | läuft (task-1) |
| **B8** | §1.10 echte CU-Pfade + ui_e2e-/persistence_restart-Runner (echter Start + Verifikation; ehrlich `not-certifiable` ohne Infrastruktur) | `computer_use.py`, `workflows.py`, `completion.py`, `tools/**`, `tests/test_verification_closure.py` | läuft (task-2) |

**Blockiert / später:** **B6** DockerRuntimeManager + Manifest v2 + Executor-Registry — startet erst, wenn `docker info` als willy funktioniert (Docker auf der Maschine weiterhin nicht installiert). **B10** Referenztools §6A/§6B (SearXNG/Pandoc-Container) hängt an B6; §6C-Komposition (research_topic) unabhängig vorbereitbar. **B11** §7 Bedienung (Chat/CLI/Stream) + §8-Abnahmen + Abschlussbericht — Welle 3.

**Interface B7↔B9 (fixiert):** B7 schreibt `tool_published` (EventStore, Kind in `KINDS`) + `publish_outbox` in derselben SQLite-Transaktion wie den Registry-Write; B9 konsumiert `tool_published`/Registry-Revision für Cache-Invalidierung und ruft die Outbox-Drain-API nur defensiv. **Interface B5↔B9:** Live-Surface/`apply_pending` (B5, fertig) wird von B9 für Publikations-Relevanz getriggert, nicht umgebaut.

---

## 5. Abnahme-Mapping (Auftrags-§8 → Etappe)

| # | Abnahme | Etappe |
|---|---|---|
| 1 | Session fordert Container-Suchtool, ohne Restart nutzbar | Welle 2–3 (B6/B9 + B5) |
| 2 | Container-CLI-Builder erzeugt Artefakt aus eigener Schnittstellenanalyse | Welle 3 (B6/B9) |
| 3 | research_topic mit Subtool-Aufrufen, Dedupe, Bericht, Budgets | Welle 3 (B9) |
| 4 | Live-Mining → Kandidat → Generalisierung/Holdout → Insertion → Aufruf | Welle 3 (B2+B7+B5) |
| 5 | Projektisolation, globale Reuse, Deaktivierung | Welle 3 (B4+B7) |
| 6 | gemeinsame Ausführungsgrenze nicht umgehbar (Injektion/Mounts/Socket/Netz) | Welle 1–2 (B1) |
| 7 | Crash/Daemon/Restart/Port-Konflikt/Image-Update: kein falsches Grün | Welle 2–4 (B6/B7) |
| 8 | CU echt: Blender/Screenshot/Settings/Restore | Welle 2 (B8) |
| 9 | defektes Testing-Tool zertifiziert nicht; Evidenz revisioniert | Welle 1 (B1 prüft) + Suite |
| 10 | Auftrag ohne Tool-Namen → findet & nutzt vorhandenes Tool | Welle 3 (B7) |
| 11 | Fähigkeitslücke ohne Namen → baut innerhalb Budget, setzt Auftrag fort | Welle 3 (B7+B9) |
| 12 | anders formulierter Bedarf → Reuse statt Neubau (auch nach Neustart) | Welle 3–4 |
| 13 | gestoppter Container → Discovery + genau ein Start + 2 parallele Aufrufe | Welle 3–4 (B6/B7) |
| 14 | Publish während laufendem Aufruf → sichere Grenze; Widerruf-Fang | Welle 3 (B5/B7) |
| 15 | Outbox: Commit + Worker-Crash → Recovery ohne Doppel-Publish | Welle 3 (B7) |
| 16 | zwei gleiche Builds/abgebrochene Session → kein Doppelbau/keine Fehl-Insertion | Welle 3 (B7) |
| 17 | Holdout nicht schönfärbbar; Budget-Ende = vollständiger Fehlerzustand | Welle 2–3 (B7) |
| + | Discovery-Testset ≥30 Formulierungen, Recall@3, Scope-Leaks, Quarantäne | Welle 4 |

## 6. Nicht-Ziele (bindend)

- Keine zweite unabhängige Tool-Plattform; der bestehende Builder/Registry/Event-Pfad wird erweitert.
- Keine unbegrenzte rekursive Tool-Erzeugung; Builds immer budgetiert.
- Kein Auto-Publish nach außen; „global" = nutzerweit lokal.
- Keine Modell-Läufe ohne klaren Blocker-Fall, wenn kein Modellzugang konfiguriert ist.
- Bestehende Gates/Semantik werden nicht aufgeweicht, um eigene Zertifizierung zu ermöglichen.

## 7. Wellen-Log (nur Zentrale)

- **14.09. 17:46** — Welle 1 als Batch `deleg_be1ee16e` gestartet (B1–B5).
- **14.09. 17:55** — Neustart des normalen Hermes-Desktop-Hosts riss den Delegation-Owner mit
  („outcome unknown"); Kinder verloren — alle noch in Recon, keine Artefakte, Arbeitsbaum sauber.
- **14.09. 17:57** — Welle 1 identisch neu dispatcht als `deleg_48647f7e`; Kontexte um die
  Artefakt-Früh-Regel ergänzt (Host-Neustart-Überlebensregel). Alle 5 Kinder laufen.
- **14.09. 18:09** — Welle 1 in Recon (noch keine Schreibzugriffe); Docker weiterhin nicht installiert
  (B6-Kernblock §2/§3 und §6-Container-Referenztools bleiben bis zur Installation blockiert).
- **Beobachtung** — Der Live-Plugin-Guard (`pre_tool_call`) der Maschine läuft unter Last paralleler
  Blöcke in 30-s-Timeouts (fail-closed, danach 60-s-Suppression); Rezept „identischen Call erneut
  senden" wirkt, `execute_code`/`delegate_task` passieren zuverlässig. Kein Datenverlust.
- **14.09. 18:15** — Discovery-Testset v1.0.0 eingefroren (36 Formulierungen; `tests/fixtures/discovery_cases.json` + `docs/DISCOVERY-TESTSET.md`) — vor jeder Discovery-Optimierung, §8-Anforderung.
- **14.09. 19:15** — Welle 1: alle fünf Blöcke committet + gepusht (23 Commits seit Start); Suite 1316 passed/RC0 (Blocklauf); Zentrale-Nachläufe 96/93/87/20 grün; B5-Doc in Arbeit; Docker weiterhin blockiert.
- **14.09. 19:29** — Zentrale-Fix: `voice_intervention_assessment` + `run_sweep` in `events.KINDS` (live `ValueError`); Regressionstest + Rot-Beweis; Suite danach 1317 passed/RC0.
