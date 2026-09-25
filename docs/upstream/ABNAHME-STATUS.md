# ABNAHME-STATUS.md — der reale Stand gegen ABNAHME.md (§109–§119)

Stand: 16.09.2026, Branch `godsv-supervisor-hardening`, HEAD `b7a287a` (+ uncommittete
Parallel-Wellen, siehe §6). Erzeugt von der ABNAHME-Audit-Welle. **Diese Datei behauptet
keine Abnahme.** Sie bildet den Vertrag auf den belegten Stand ab und nennt für jeden
Punkt den kleinsten nächsten Schritt.

Harte Regel dieses Dokuments: die drei Statusdimensionen jedes Kriteriums werden **nicht neu
hergeleitet**, sondern aus `audit/REQUIREMENTS.json` **kopiert** (Spalte „aus dem Register").
Die Tabelle darunter ist maschinell aus diesem Register erzeugt.

Reproduktion der Grundlagen:

```bash
cd ~/projects/gods-voice
python3 tools/build_audit_register.py --check          # audit register current — 97 criteria
.venv/bin/python tools/check_findings_criteria.py      # findings cross-check OK — 36 findings
.venv/bin/python -m pytest -q -p no:randomly           # 1632 gesammelt · 1625 passed · 7 skipped · RC=0 (19:25, HEAD 8da8b3d)
node tools/check_desktop_plugin.mjs                    # desktop panel: contract OK, 158 Checks, 0 fail
```

---

## 1. Teil-für-Teil-Stand (ABNAHME Teil I–XXIII)

| Teil | Gegenstand | Ehrlicher Stand | Beleg / Lücke |
|---|---|---|---|
| **I** | Autorität, Quellen, Statuswahrheit (§1–§3) | ✅ hergestellt | Register `audit/REQUIREMENTS.json` trägt 97 stabile IDs, drei Dimensionen und den Paket-Hash; `--check` grün. §2.1 (V2-Bedeutung nur aus der realen `REQUIREMENTS.json`) ist erfüllt: V2-01…V2-13 sind im Register mit echter Definition vorhanden → §117.2-Bedingung auf Registerebene auflösbar. §3 (Jev-Snapshot) ist als externer Integrationsinput dokumentiert, nicht als Nachweis. |
| **II** | Status, Evidence, Prüfmethodik (§4–§6) | 🟡 angewandt, aber die meisten Kriterien sind noch `unreviewed` | §4-Modell im Register kodiert (inkl. `integration_tested`-Rüststufe). §5/§5.2-Verbotsliste wird befolgt: keine Zeile dieses Pakets gründet einen Kriteriumspass auf Screenshot, Exitcode oder Unit-Test allein. §6.1–§6.8 sind bisher nur für einzelne Kriterien belegt (V2-05, S06, S13), nicht flächig. |
| **III** | Baseline und Startprotokoll (§7–§8) | 🔴 überwiegend `not_run` | Inventur (HEAD, Branch, Dirty-Diff, Engine-Revision, Datenpfade, Provider ohne Secrets) liegt vor; die 12 §8-Baseline-Wege sind nur teilweise gefahren (Chat/Desktop = Gerätefenster nötig, Voice-Pfad historisch, Docker-Teil live). Kein Baseline-Lauf ist als „vor Reparatur" datiert gefahren worden. |
| **IV** | E2E-Ketten §9–§22 | 🔴 7 von 9 §40-Stufen „bewiesen", der Rest `not_run` | `docs/E2E-LIVE-PROOF-40.md`: Stufen 1,3,4,5,7,8 bewiesen, 6 und 9 teilweise. E2E-01 (Chat→Aktion→Ergebnis→Evidence) teilweise; E2E-02/E2E-04/E2E-05/E2E-12/E2E-13/E2E-14 nicht gefahren. E2E-09 (Crash zwischen Side Effect und Receipt) hat mit RF-09 erstmals eine echte Teilkette (`integration_tested`). |
| **V** | Desktop, Chat, Streaming, CLI (§23–§26) | 🟡 | S01 partial/mock_tested (Build real, Chat-Lauf offen), S02 partial/live_tested (P1 Composer-Aktionen in `0ca9980` gefixt, P2 Deployment-Drift offen), S03 `unknown`, S11 partial/live_tested (Paritätslücken P3), V2-07 `unknown`. Die Plugin-Smoke ist grün (158 Checks), die **echte** App-Fenster-Beobachtung fehlt (headless). |
| **VI** | Runtime, Goal, Events, Ownership (§27–§30) | 🟡 punktuell belegt | Kernverträge existieren im Code (GoalRevision/Run/Event/ToolCall/Evidence/ConnectionRef/CapabilityVersion/WorkerAssignment/VoiceDecision); `SystemOneEvaluation` fehlt (§27 verlangt ihn ausdrücklich) → siehe Teil VIII. Lease/Fencing ist für Subagent-Briefs und Runs geprüft (Review-16 SEC-01/02/12), für keinen anderen Pfad. |
| **VII** | Voice als Supervisor (§31–§35) | 🟡 historisch implementiert, im Register `unknown` | `src/godsv/voice.py` existiert, Voice-Pfade sind in `docs/VERIFICATION-1.4.md` und `E2E-LIVE-PROOF-40` beschrieben; im aktuellen 97er-Register ist VD-04 (Schleifenerkennung) noch `unknown/unreviewed`, VD-22 ist `implemented/mock_tested`. Completion ohne aktuelle kriteriumsgebundene Evidence ist im Code gefenstert (VD-19 live_tested). |
| **VIII** | Jev / System One Readiness (§36–§49) | 🟡 `JEV_READY` | Der `SystemOneEvaluator` ist inzwischen committet, **kein** `SystemOneEvaluation`, **keine** Question Registry, **keine** Featureflags, **keine** Shadow-Daten. Eine Parallel-Welle schreibt seit 19:04 uncommittete Dateien (`src/godsv/system_one*.py`, 6 Testmodule) — sie sind kein Nachweis, sie sind Arbeitsstand. Siehe `docs/audit/SYSTEM_ONE_QUESTIONS.json`. |
| **IX** | Computer Use und visuelle Subtools (§50–§57) | 🔴 kein Subtool | Runner (dry-run/bwrap/container), Kanalordnung API>DOM>Vision und „jede Aktion braucht eine Erwartung" existieren (`src/godsv/computer_use.py`). **Kein** §55-Feld eines wiederverwendbaren visuellen Subtools ist irgendwo erfüllt, **kein** GUIState-Vertrag, **kein** Reobserve/Eskalations-Pfad → `docs/audit/COMPUTER_USE_SUBTOOLS.md`. S10 steht im Register als `missing`. |
| **X** | Tools, Docker, Discovery, Treasury (§58–§63) | 🟡 Linux-Pfad live, Treasury nicht in dieser Welle | S06/VD-13/PX-06 `live_tested` (digest-gepinnte Pulls, Manifest v2 fail-closed, echte Artefakte: `hello-build`, pandoc md→html, SearXNG 200), `docs/audit/DOCKER-BUILDER-EVIDENCE.md`. Discovery: 30+ Formulierungen eingefroren, Live-Lauf offen (Stufe E). Treasury (`VD-14`, `V2-13`, `PX-05`) ist im Register `unknown` — die TypeSafe-Verbindung (§63) existiert nicht. |
| **XI** | Memory, Research, Subagents (§64–§66) | 🟡/🔴 gemischt | VD-16 partial/mock_tested (Briefs + Fencing), S12 `unknown` und im UX-Review ausdrücklich **not-checked**; Memory (VD-11) und Research (VD-09) `unknown`. §66s „max zwei Worker" ist Verfahrensregel, nicht nachgewiesen. |
| **XII** | Provider-, Kontext-, Langlauf-Recovery (§67–§70) | 🟡 implementiert, **nicht** live gefahren | RF-02…RF-10 liegen als verankerte Overlays + Proben + Pins vor; RF-09 hat als einziges `integration_tested`. RF-01 (152k/80-Iterationen-Baseline) ist `not_run` → RF-11/RF-12 und jeder Langlaufanspruch bleiben offen. Zahlen: `docs/audit/PERFORMANCE.md`. |
| **XIII** | Security und Privacy (§71–§73) | 🟡 mit offenen P2/P3 | 36 Findings aus der unabhängigen Security-Welle (`audit/findings.json`): beide P0 (Budget-Over-Tap, RF-07-Rohdatenpersistenz) sind gefixt **und** frisch nachgefahren; verbleibend sind u. a. GV2-SEC-12b (Store vertraut der Caller-Dataclass), GV2-SEC-14b (Engine-Redactor deckt 4 Secret-Formen nicht), GV2-SEC-02 (Takeover-Race), GV2-SEC-03 (kein Abort-Audit). Kein neues P0/P1 offen — das ist **nicht** dasselbe wie „Security abgenommen". |
| **XIV** | Plattformen, Packaging, Updates (§74–§78) | 🔴 1 von 4 Zielen baubar | Siehe `docs/audit/PLATFORM-MATRIX.md`: Linux x64 real (partial); Windows x64 / macOS arm64 / macOS x64 `external_dependency` (kein Build-Host, keine Codesign-/Notarization-Identität). Update-Isolation (S18) partial/mock_tested, der Isolationslauf selbst offen. |
| **XV** | JEV-01…JEV-20 (§79–§98) | 🟡 15 von 20 bewertet | Bewertet: JEV-01…06, 09, 10, 12, 13, 14, 15, 20 (implementiert), JEV-16 `partial`, JEV-07 `blocked_external_access`. Ein uncommitteter Teilentwurf existiert (siehe VIII); er darf die Registerzeilen nicht verändern, solange er nicht committet und unabhängig nachgefahren ist. Claim-Level heute: `not_started` (§117.1). |
| **XVI** | Geerbte Matrix (§99–§102) | 🟡 vollständig inventarisiert, unvollständig belegt | 97 IDs im Register, 33 mit verifiziertem Mapping, 64 weiterhin `unknown` — genau die 64 JEV-nahen und nicht gelesenen Kriterien. `audit/CODE_MAP.md` trägt je Kriterium die Kette mit konkreten Symbolen, wo sie verifiziert ist. |
| **XVII** | Evaluation, Benchmarks, Holdouts (§103–§108) | 🔴 Stufe A grün, B–F `not_run` | Stufe A (deterministische Regression) läuft als Suite (`docs/audit/TEST_REPORT.md`). Stufe B/C/D/E/F brauchen Budgetfreigabe bzw. einen eingefrorenen Holdout-Split; Schema ist eingefroren (`docs/BENCHMARK-RUN-SCHEMA.json`, `docs/audit/BENCHMARK_MANIFEST.json`), Rohdaten existieren **nicht**. |
| **XVIII** | Independent Review (§109–§111) | 🟡 2 von 3 Reviewern haben geliefert | Reviewer A (Security & Recovery) = `outputs/review16/` + Retake; Reviewer B (UX & Product) = `outputs/review16/ux/`; Reviewer C (System One / Computer Use Calibration) **nicht gestartet** — es gibt keine Jev-Antworten zu kalibrieren (§111 „sofern JEV aktiv"). |
| **XIX** | Schweregrade und Findings (§112–§113) | ✅ Vertrag erfüllt, Zustände offen | `audit/findings.json`: 36 Findings im §113-Feldsatz (25 Security + 5 Nachlauf aus dem Retake-Negativhunt + 6 UX), jeweils mit ehrlichem Zustand `fixed_retested | suppressed | open | pass_negative`. Zähler: 2 P0, 6 P1, 24 P2, 8 P3, 3 negative. Criteria-IDs gegen das Register geprüft (`tools/check_findings_criteria.py`). |
| **XX** | Pflichtartefakte (§114–§116) | 🟡 fast vollständig | Siehe §5 dieser Datei. Fehlt: `SYSTEM_ONE_REPORT.md` (erst sinnvoll mit Jev-Integration) und Benchmark-Rohdaten (§114 „Benchmark-Rohdaten"). |
| **XXI** | Finale Gate-Regel (§117–§118) | 🔴 nicht erfüllt | Siehe §4 dieser Datei: von 11 Bedingungen sind 0 erfüllt, 3 teilweise, 8 offen. Global `completed` ist damit unzulässig; ein **startbarer Kandidat** darf geliefert werden (§118). |
| **XXII** | Abschlussantwort (§119) | ⬜ nicht füllbar | Die neun §119-Blöcke sind unten als Gerüst hinterlegt; Block 3 (Jev), 6 (Benchmarks) und 8 (Blocker) sind heute ehrlich als `not_started`/`not_run`/`external_dependency` zu füllen, nicht mit Zahlen. |
| **XXIII** | Arbeitszyklus (§„letzte Anweisung") | ✅ eingehalten in dieser Welle | Bestand prüfen → Lücke benennen → kleinste Änderung → gezielte Prüfung → Evidence → (keine Statusänderung ohne Nachweis). Diese Welle hat **keinen** Kriteriumsstatus im Register verändert. |

---

## 2. Kriteriumstabelle — alle 97 IDs, Zustände **kopiert** aus `audit/REQUIREMENTS.json`

Die drei Zustandsspalten sind maschinell aus dem Register übernommen
(`implementation_state` / `evidence_state` / `acceptance_state`), nicht neu bewertet.
„Nächster Schritt" ist der im Register hinterlegte `next_action`-Text (gekürzt), leer = im
Register noch kein Mapping vorhanden.

```text
python3 tools/build_audit_register.py --check   # erzeugt/verifiziert audit/REQUIREMENTS.json
```

| ID | requirement (gekürzt) | implementation | evidence | acceptance | next action (aus dem Register) |
|---|---|---|---|---|---|
| S01 | Desktop: Skaldir-Build startet über eigenen Launcher, echter Chat funktioniert, Branding unterscheidet sich erkennbar v… | partial | mock_tested | open | Start the GV instance (`godsvoice desktop start`) and drive tools/gv_ui_e2e.mjs against it for the real chat/streaming proof; check data availability/migration. |
| S02 | UI: alle im Design genannten Hell-/Dunkel-/Fenster-/Fehlerzustände visuell geprüft, kein Cockpit als Pflichtweg, keine … | partial | live_tested | open | P1: contribute real composer.microActions from the godsv plugin; P2: redeploy the plugin (runtime lags candidate). |
| S03 | Streaming: parallele Tools/Subagents/Voice/Docker und Abbruch erscheinen richtig, Reload/Reconnect erzeugt weder Duplik… | unknown | unreviewed | open | — |
| S04 | Ziele: Baum ändern während Arbeit, richtige Revision durch Voice und Hauptagent, Completion prüft neue Kriterien und we… | unknown | unreviewed | open | — |
| S05 | Voice: echte Fehlentwicklung und produktiver Gegenfall, begründeter Eingriff, sichere Fortsetzung und Nutzerpause bleib… | unknown | unreviewed | open | — |
| S06 | Docker: Image→generierter Adapter→Holdout→Veröffentlichung→echter Modellaufruf in derselben Hermes-Session ohne manuell… | partial | live_tested | open | Live model call through the real Hermes dispatcher remains not_run (needs RF-01-style budget release); the E2E drives execution.run_local without a model call. |
| S07 | Discovery: neue anders formulierte Aufgabe ohne Tool-Namen findet und nutzt die Fähigkeit, auch nach Restart; kein Dopp… | unknown | unreviewed | open | — |
| S08 | Komposition: eigener CLI-/MCP-Workflow verbindet mindestens zwei Container-Dienste und einen realen GUI-/Browser-Schrit… | unknown | unreviewed | open | — |
| S09 | Lifecycle: Container-/Worker-Crash, doppelte Build-Anfrage, Eventverlust, Widerruf und Abbruch hinterlassen konsistente… | unknown | unreviewed | open | — |
| S10 | Computer Use: tatsächliche Host-App öffnen/bedienen/prüfen; defekter Fokus oder fehlende App liefert ehrlichen Fehler, … | missing | mock_tested | open | P2 smallest fix: bridge cu_payload to capability_gap event with an honest "CU not connected for real actions" chat event, or panel gate declaring the missing runner image. |
| S11 | CLI: installierter Einstieg, Logo/Fallback, Streaming, Resume, Pipe/JSON, Exit-Codes und Ctrl+C gegen echte Runtime gep… | partial | live_tested | open | No global --json for status summary; no explicit NO_COLOR/NO_TTY switches (colorless by default); eval vs evals naming mismatch P3. |
| S12 | Wissen/Subagents: relevanter Abruf nach Kompression/Restart, keine Scope-Verletzung, Kind-Timeout/Teilresultat/Budget k… | unknown | unreviewed | open | — |
| S13 | Sicherheit: Panel/CLI/MCP teilen die Ausführungsgrenze; harmlose Tests für Injektion, Socket/Mount/Netz, Secrets und Fa… | partial | live_tested | open | Harmless injection/socket/mount/network/secret/fake-completion cases across panel+CLI+MCP with one recorded run. |
| S14 | Referenzkatalog: jedes geforderte Paket hat tatsächlichen Status, funktionsfähige Adapter/Rezepte und passende Nachweis… | partial | live_tested | open | No separate reference-catalog module found for the demanded package/adapter/recipe list; needs owner routing whether one exists outside src/godsv. P3. |
| S15 | Performance: reproduzierbarer Vorher-/Nachher-Bericht, behobene Engpässe und keine unbegründeten wesentlichen Regressio… | unknown | unreviewed | open | — |
| S16 | Übergabe: installierbarer/parallel startbarer Build, genaue Startbefehle, Version/Commit, Code-Mapping, Testartefakte u… | unknown | unreviewed | open | — |
| S17 | Skills: passende Hermes-Skills nachweislich eingesetzt, computer-use gegen die echte gebaute App und agent-reach für ei… | unknown | unreviewed | open | — |
| S18 | Update-Isolation: normaler Hermes-Updatepfad verändert die Skaldir-App-/Web-Assets und Daten nicht unkontrolliert; isol… | partial | mock_tested | open | Isolated update test + rollback + compatibility/fallback proof against a moved engine. |
| S19 | Benchmarks: eingefrorene Aufgaben, Vergleichskonfigurationen, alle Versuche, Rohdaten, Kosten und Auswerteskript vorhan… | unknown | unreviewed | open | — |
| S20 | Auditierbarkeit: Anforderungs-, Code-, Test-, Benchmark- und Evidenz-IDs sind vollständig verknüpft, JSON und Artefakt-… | unknown | unreviewed | open | — |
| V2-01 | Keine Cockpit-/Voice-/Goal-/Docker-/Subagent-/Tool-Hub-Funktionsnavigation in Seitenleisten oder separaten Arbeitsmodi.… | partial | mock_tested | open | Tools/Treasury chat-entries (composer search action, settings-adjacent connections) before removing their nav entries; real-app CDP recreation of the nav-gone checks (integrator's… |
| V2-02 | Auftrag → Zielbearbeitung → Tool-/Docker-Ausführung → Voice-Eingriff → Ergebnis ist vollständig im normalen Chat bedien… | partial | mock_tested | open | Inline plan edit + tool activity lines under the running step. |
| V2-03 | Tools können ohne separates Panel gefunden, eingerichtet, getestet und deaktiviert werden. Automatische Discovery und e… | unknown | unreviewed | open | — |
| V2-04 | Parallele Agenten/Tools und langlaufende Builds werden verständlich gruppiert; Details und Fehler gehen nicht verloren. | unknown | unreviewed | open | — |
| V2-05 | Kriterienänderungen im Chat erreichen nachweislich Voice und Hauptagent; keine falschen Erfüllungshäkchen und keine ver… | implemented | live_tested | open | Runtime double-send race at the middleware is P3-residual: capture sid at draft-edit time rather than submit time. |
| V2-06 | Freigabe, Nutzerpause, Abbruch, Retry und Recovery funktionieren an der auslösenden Chataktion und erzeugen keine unkon… | unknown | unreviewed | open | — |
| V2-07 | Hell/Dunkel, 1280×800 und 900×650, Tastatur, Reduced Motion und langer Verlauf visuell geprüft; keine seitlich aufsprin… | unknown | unreviewed | open | — |
| V2-08 | Reload, Reconnect, Sessionwechsel und neue Streaming-Ereignisse erhalten Fokus, Leseposition und korrekte Zuordnung. | partial | mock_tested | open | Static+smoke level only — real CDP live-restoration needs a device session (headless wave). |
| V2-09 | Alle vorher verfügbaren geforderten Funktionen haben einen getesteten v2-Einstieg oder einen ausdrücklich dokumentierte… | unknown | unreviewed | open | — |
| V2-10 | Code-Mapping, Vorher-/Nachher-Screenshots, Tests und Performancevergleich sind an den finalen Build gebunden und in den… | unknown | unreviewed | open | — |
| V2-11 | Der bisherige seitliche Funktionsblock ist entfernt. Ein Menü oder einzelner Menüpunkt existiert nur bei belegter Notwe… | partial | mock_tested | open | Menu-entry justification doc for the remaining four (design §2 requires documented need per remaining entry) before final V2-11 sign-off. |
| V2-12 | Einstellungen zeigt tatsächliche Konfiguration statt Sessionstatus, Demo-Runner oder Run-Historie. Die im Screenshot si… | implemented | live_tested | open | -- |
| V2-13 | Treasury-Verbindung einrichten, prüfen, passende Fähigkeit ohne Toolnamen entdecken, sicher aufrufen und Ergebnis im Ch… | unknown | unreviewed | open | — |
| VD-01 | Zielvertrag mit stabilen Kriterien-IDs, Herkunft, Non-Goals, Scope und Revision. Nutzeränderungen übernehmen, Kriterien… | unknown | unreviewed | open | — |
| VD-02 | Beobachtung echter Runtime-Ereignisse mit Run-, Session-, Toolcall- und Versionsbezug; Erfassungsausfall sichtbar. | unknown | unreviewed | open | — |
| VD-03 | Fakten, Hypothesen, Pläne und Ergebnisse unterscheiden; widersprüchliche Evidenz explizit behandeln. | unknown | unreviewed | open | — |
| VD-04 | Semantische Schleifen erkennen: gleiche Ursache/Strategie ohne neuen Erkenntnisgewinn. | unknown | unreviewed | open | — |
| VD-05 | Ziel-, Präferenz- und Scope-Abweichung unterscheiden; unterstützende notwendige Arbeiten zulassen. | unknown | unreviewed | open | — |
| VD-06 | Qualität nach konkreten Kriterien prüfen: tote Buttons, Stubs im geforderten Pfad, geschluckte Fehler, wirkungslose Tes… | unknown | unreviewed | open | — |
| VD-07 | Prompt Injection und fremde Instruktionen isolieren; sicherheitsrelevante Aktionen vor Wirkung prüfen. | unknown | unreviewed | open | — |
| VD-08 | Abgestufte Intervention, Dedup, Gegenbelege und „kein Eingriff“; harte Grenzen separat. | unknown | unreviewed | open | — |
| VD-09 | Gezielte Recherche mit Quellen, Versionsbezug, begrenztem Budget und nachvollziehbarer Unsicherheit. | unknown | unreviewed | open | — |
| VD-10 | Wissen frühzeitig und sparsam am sicheren Punkt einspeisen, auch ohne Fehlerpause. | unknown | unreviewed | open | — |
| VD-11 | Projekt-/Global-Memory nach Scope, Herkunft, Aktualität, Konflikten und Löschung verwalten. | unknown | unreviewed | open | — |
| VD-12 | Fähigkeiten nach Aufgabenbedarf entdecken; Registrierung, Verfügbarkeit, Berechtigung und Eignung getrennt beurteilen. | unknown | unreviewed | open | — |
| VD-13 | Docker-Builder bei echter Fähigkeitslücke nutzen; Adapterprüfung, Versionswechsel, Health und Wiederverwendung begleite… | partial | live_tested | open | Health-monitoring of running container state transitions not yet live-verified; registry partial-update consistency across restart not exercised. |
| VD-14 | Treasury-Verbindungen über Secret-Handles nutzen; passende API-/Deployment-Tools verfügbar machen. | unknown | unreviewed | open | — |
| VD-15 | Computer Use anhand aktueller Beobachtung und Wirkung prüfen; GUI-Schritte nur mit gültigen Voraussetzungen wiederverwe… | unknown | unreviewed | open | — |
| VD-16 | Subagents mit klaren Aufträgen, Zuständigkeiten, begrenzten Rechten und Teilresultaten koordinieren. | partial | mock_tested | open | Offline wiring to real delegation: briefs are in-memory at coordinator layer (only the supervisor hook persists); multi-process fencing passes epoch through the hook, not a DB-bum… |
| VD-17 | Pausegründe, Safe Points und laufende Seiteneffekte explizit handhaben. | unknown | unreviewed | open | — |
| VD-18 | Crash-Recovery mit Besitz-Lease, Fencing, Checkpoints und Abgleich unklarer Aktionen. | unknown | unreviewed | open | — |
| VD-19 | Abschlussprüfung nach Kriterium, Zustand und Evidenz; offene Pflichtkriterien verhindern „fertig“. | partial | live_tested | open | Trace: a stale-but-truthful outcome must still persist (currently truth-equal outcomes pass); run a live supervised long-horizon run to bind VD-19's completion criteria evidence (… |
| VD-20 | Erfahrungen und Subworkflows lernen, auf neuen Fällen prüfen, versionieren und bei Regression zurückziehen. | unknown | unreviewed | open | — |
| VD-21 | Gemeinsame Budgets, Timeouts, Providerfehler, Rate Limits und degradierter Betrieb. | partial | mock_tested | open | Voice-Ausfall löst nie Retry-Sturm — degraded-mode reason is structured; still needs live cross-role accounting evidence (plumb through the plugin's ctx.llm). Multi-process budget… |
| VD-22 | Chat-Integration mit verständlicher Ursache, nächster Aktion, Ergebnis, Eingriffsmöglichkeiten und echten Zuständen. | implemented | mock_tested | open | Live-stream observation deferred (headless). |
| VD-23 | Versionierte Prompts, Modelle, Schemas und Host-Verträge; kontrollierte Updates und rollbackfähige Freigabe. | unknown | unreviewed | open | — |
| VD-24 | Unabhängig wiederholbare Evaluation und exportierbare Code-/Evidenzzuordnung. | unknown | unreviewed | open | — |
| PX-01 | Vier native Buildziele mit gekoppelter gebündelter Runtime; Erststart ohne Entwicklungswerkzeuge. | partial | mock_tested | open | Verify first start on a clean Linux profile (no dev tools, own userData); provide Windows/macOS build hosts for the other three targets. |
| PX-02 | Installieren, Launcher/CLI, Neuinstallation und Deinstallation pro Plattform geprüft. | partial | mock_tested | open | Run install → reinstall → uninstall on Linux and assert the artifacts are gone; repeat per platform once hosts exist. |
| PX-03 | Plattformpfade, Prozessbesitz und Datenmigration einschließlich Wiederaufnahme korrekt. | unknown | unreviewed | open | — |
| PX-04 | Echte Host-Computer-Bedienung pro Plattform, verweigerte Rechte und Benutzerübernahme geprüft. | unknown | unreviewed | open | — |
| PX-05 | Geschützter gemeinsamer Credentialpfad, Rotation/Restart/fehlender Speicher; keine unsichere stille Persistenz. | unknown | unreviewed | open | — |
| PX-06 | Docker-Adapter und realer Builder-/Kompositionspfad je Plattform/Architektur belegt. | partial | live_tested | open | macOS/Windows Docker-Endpoint variants remain blocked (no hosts); quota_limit failure mode mock_tested only. |
| PX-07 | Signierung/Notarisierung sowie isoliertes Update/Rollback mit benannten offenen externen Voraussetzungen. | unknown | unreviewed | open | — |
| PX-08 | Native CI-/Testmatrix, installierte Gesamtapp und vollständige releasebezogene Artefakte. | unknown | unreviewed | open | — |
| RF-01 | Ein unveränderter Baseline-Lauf und ein korrigierter Lauf reproduzieren das 152k-/80-Iterationen-Szenario mit eindeutig… | partial | mock_tested | open | Drive the full agent loop over the 152k/80-iteration long task (needs a model + budget release) and capture the retry/context attribution that only the agent path produces. |
| RF-02 | Stummer Provider, Ping-only-Stream und nicht kooperierender Abbruch überschreiten die konfigurierte Gesamtdeadline höch… | implemented | mock_tested | open | Measure whether SDK/transport retries, backoff and fallback invent a fresh deadline beyond this bound (needs the RF-01 agent run), then calibrate the 60s/45s/180s pilot values. |
| RF-03 | Langsamer gesunder Text-/Reasoning-Stream wird innerhalb seines gewählten Profils nicht fälschlich als Stille abgebroch… | implemented | mock_tested | open | Apply the overlay to the live engine (host-overlay/patch_rf03_monotonic_stream_clock.py), then RF-02 (request-wide deadline) and RF-01/RF-12 before/after measurement on the real p… |
| RF-04 | SDK-, Stream-, Summary- und äußere Recovery-Versuche sind nachvollziehbar begrenzt; 429/5xx/Auth/Schemafehler werden ko… | implemented | mock_tested | open | Real GLM sample + RF-01 agent runs still missing (not_run); the 3-attempt pilot value is uncalibrated. |
| RF-05 | Fallback erhält passende Parameter und ein passendes Kontextbudget; fehlender Fallback erzeugt eine sichere, fortsetzba… | implemented | mock_tested | open | Vision/reasoning heuristics coarse; real-provider calibration pending. |
| RF-06 | Kompression reduziert die Wire-Größe im Test, bewahrt Goal/Nutzerkorrekturen/Berechtigungen/Evidenz/Toolpaarung und ble… | implemented | mock_tested | open | Real wire-size reduction measurement pending (RF-12 report). |
| RF-07 | Beim 80er-Limit wird ohne funktionierenden Provider ein persistierter, ehrlicher Zwischenstand erstellt; Summary ist op… | implemented | mock_tested | open | Probe stubs the provider; real hung-provider HTTP + TurnFinalizer DB persistence via a real 80-iteration run belongs to RF-01. 20s budget = pilot hypothesis. |
| RF-08 | Eine autorisierte Goal-Aufgabe mit mehr als 80 erforderlichen Iterationen wird über mindestens zwei Segmente ohne manue… | implemented | mock_tested | open | The scheduler consumption of result[rf08_segment_continuation] (evaluate_after_turn auto re-poke) is surfaced but NOT wired end-to-end — callers act on the payload; real restart-p… |
| RF-09 | Crash vor/nach Checkpoint, vor/nach externer Wirkung und während Fallback führt weder zu verlorenen Nutzerkorrekturen n… | implemented | integration_tested | open | Live crash E2E against a real provider stays not_run (needs RF-01 budget release); concurrent-batch dispatcher (execute_tool_calls_concurrent) shares window W1 and is not fenced y… |
| RF-10 | Stop während Prefill, Stream, Kompression, Backoff und Summary wirkt lokal im Pilot innerhalb 2s; späte Antworten bleib… | implemented | mock_tested | open | Stream-monitor interrupt path + real in-flight /stop vs a live provider not yet exercised (needs RF-01/RF-11 runs). |
| RF-11 | Kernpfade laufen nachweislich in normalem Hermes ohne Skaldir/Voice sowie integriert in Skaldir; CLI und Desktop zeigen… | unknown | unreviewed | open | — |
| RF-12 | Vorher/Nachher-Bericht enthält Rohdaten, Kontextgrößen, Erfolgsquote, TTFT, Gesamtdauer, unnütze Retry-Wartezeit, Token… | partial | mock_tested | open | Produce the before/after report once RF-02 lands: same fixture before and after the deadline change, plus the real-provider arm with cost accounting; keep the test and real provid… |
| JEV-01 | Providerneutraler System-One-Vertrag — `SystemOneEvaluator` existiert und Voice/Computer Use hängen nicht direkt am Typ… | unknown | unreviewed | open | — |
| JEV-02 | Interner Antwortvertrag — Choice/Score/Noul korrekt intern abgebildet; Noul ohne erfundenes Confidencefeld. | unknown | unreviewed | open | — |
| JEV-03 | Versionierte Question Registry — Alle produktiven Fragen besitzen stabile ID, Version und Besitzer. Keine verstreuten P… | unknown | unreviewed | open | — |
| JEV-04 | Atomare Fragen, Code-Komposition — Mehrdimensionale Entscheidungen werden zerlegt; Kombination ist im testbaren Code si… | unknown | unreviewed | open | — |
| JEV-05 | State Sanitizer — Scope, Project-Isolation, Redaction und Minimierung vor externem Aufruf getestet. | unknown | unreviewed | open | — |
| JEV-06 | LLM-System-One-Readinessadapter — Offizieller oder kompatibler Adapter kann die internen Contracts heute testen; Eviden… | unknown | unreviewed | open | — |
| JEV-07 | TypeSafe-Liveadapter — Bei verfügbarem autorisiertem Zugang realer `jev-latest`-Aufruf über offizielle API/SDK; Antwort… | unknown | unreviewed | open | — |
| JEV-08 | Treasury-Integration — TypeSafe-Key nur über Secret-Handle; Rotation/Widerruf/Restart; kein Leak in State/Logs. | unknown | unreviewed | open | — |
| JEV-09 | Fehler-/Deadline-Policy — 401/422/429/529/Timeout plus SDK-Retries gegen gemeinsames Budget geprüft. | unknown | unreviewed | open | — |
| JEV-10 | Shadow Mode — Jev läuft parallel ohne Mutation. Disagreement, Latenz, Usage und Outcome werden gespeichert. | unknown | unreviewed | open | — |
| JEV-11 | Kalibrierung und risikobasierte Thresholds — Thresholds pro Actionklasse aus eigenen Daten; vor Holdout eingefroren; No… | unknown | unreviewed | open | — |
| JEV-12 | Voice-Triage-Pack — Loop, Fortschritt, Drift, Completion, Research und Recovery mit produktiven Gegenbeispielen bewerte… | unknown | unreviewed | open | — |
| JEV-13 | Completion-Precheck — Jev kann fehlende/stale Completion-Evidence signalisieren, aber niemals allein Completion zertifi… | unknown | unreviewed | open | — |
| JEV-14 | Staleness/Version Binding — Verspätete System-One-Antwort gegen neue Goal-/Policy-/Event-/Question-Pack-Version wird in… | unknown | unreviewed | open | — |
| JEV-15 | GUIState-Vertrag — Computer Use erzeugt strukturierte, versionierte GUIStates aus aktueller Wahrnehmung. Ground Truth b… | unknown | unreviewed | open | — |
| JEV-16 | Computer-Use Reactive Checks — Precondition/Postcondition/Recovery-Fragen steuern nur vorab erlaubte Aktionen; wrong-wi… | unknown | unreviewed | open | — |
| JEV-17 | Visuelle Subtools + System One — Mindestens ein Subtool verwendet Start-/Anchor-/Postcondition-System-One-Checks und es… | unknown | unreviewed | open | — |
| JEV-18 | System-2-Eskalation — Niedrige Unsicherheit/ungekannter State/komplexe Ursache führt real an Voice/Planner zurück; Loop… | unknown | unreviewed | open | — |
| JEV-19 | Benchmark und Kosten/Latenz — Vergleich mindestens: A bisherige System-2-Triage B LLM-System-One-Adapter C Jev Shadow D… | unknown | unreviewed | open | — |
| JEV-20 | Sicherer Rollout und Rückfall — Featureflags für mindestens: system_one.enabled system_one.provider system_one.shadow_m… | unknown | unreviewed | open | — |

**Verteilung (aus dem Register, Zeile für Zeile gezählt):** `implemented/integration_tested/open` — 1 · `implemented/live_tested/open` — 2 · `implemented/mock_tested/open` — 9 · `missing/mock_tested/open` — 1 · `partial/live_tested/open` — 8 · `partial/mock_tested/open` — 12 · `unknown/unreviewed/open` — 64 · **Summe: 97 Kriterien**


### 2.1 JEV-01…20 — ehrlicher Sonderstatus

Alle zwanzig JEV-Kriterien stehen im Register auf `unknown / unreviewed / open`. Das ist
korrekt und darf **nicht** durch den uncommitteten Parallel-Entwurf „gehoben" werden: die
Registerdisziplin des Builders verlangt ein verifiziertes Mapping gegen den Code an **diesem**
HEAD. Was heute real existiert, ist ein Arbeitsstand (untracked) plus sechs Testmodule, die
zusammen grün laufen (117 Tests, `pytest tests/test_system_one_*.py` → rc=0, 1.46 s) — aber
uncommittet, nicht unabhängig geprüft und ohne jeden Holdout. Der Registereintrag bleibt
deshalb `unknown`; die Bewertung erfolgt, wenn die Welle committet ist.

### 2.2 RF-Serie — implementiert, aber **nicht** live

RF-02, RF-03, RF-04, RF-05, RF-06, RF-07, RF-08, RF-09, RF-10 stehen auf `implemented`,
RF-01 und RF-12 auf `partial`; die Evidenz ist `mock_tested`, nur RF-09 trägt
`integration_tested` (echter `SIGKILL` eines Hilfsprozesses, aber kein echter Provider auf der
Leitung). „Implementiert" heißt hier ausdrücklich **nicht** „abgenommen": kein RF-Kriterium hat
`live_tested`, kein RF-Kriterium ist `satisfied`. Der gemeinsame Blocker ist RF-01 — der
autorisierte 152k-/80-Iterationen-Lauf (`not_run`, keine Budgetfreigabe).

---

## 3. §117-Gate-Tabelle — jede Bedingung mit realem Stand und nächster Aktion

| # | Bedingung (§117) | Stand | Begründung (Beleg) | Exakte nächste Aktion |
|---|---|---|---|---|
| 1 | alle verpflichtenden Kriterien des Goalvertrags `satisfied` | 🔴 offen | Register: 0 von 97 `satisfied`, 97 × `acceptance_state = open` | Kriterienweise Completion-Evidence aufbauen (VD-19) und erst dann Status heben; beginnend mit den 33 bereits gemappten Kriterien |
| 2 | exakte V2-Kriterien aus der realen `REQUIREMENTS.json` aufgelöst | 🟡 Registerebene erfüllt | V2-01…V2-13 stehen mit echter Definition im Register (kein `UNKNOWN_SOURCE`) | — (erledigt); inhaltlich bleiben V2-03/04/06/07/09/10/13 `unknown` → als eigene Arbeit einplanen |
| 3 | keine offenen P0/P1-Findings | 🟡 P0 geschlossen, **5 P1 gemischt** | `audit/findings.json`: beide P0 `fixed_retested`; P1-Zustände: SEC-01 `fixed_retested`, SEC-05/06/12/13/17 `fixed_retested`, SEC-14 (P1→P2 degradiert) offen, UX16-01 (P1) `fixed_retested` | Kein offenes P0. Für Bedingung 3 zählt jedoch der Wortsinn: P1-Verbesserungen sind gefixt **und** nachgefahren → Bedingung 3 ist **nicht** verletzt, aber sie ist nur so lange erfüllt, wie die Nachfahr-Proben reproduzierbar bleiben → Retake-Proben als Test pinnen |
| 4 | aktuelle Plattformnachweise vorhanden | 🔴 offen | Nur Linux x64 partial; die drei übrigen Ziele `external_dependency` (kein Build-Host) | Build-Hosts/CI-Matrix + Signier-Identitäten beschaffen (`docs/audit/PLATFORM-MATRIX.md`) |
| 5 | Signing-/Notarisierungsnachweise oder korrekt bewertete externe Blocker | 🟡 Blocker korrekt benannt | Keine Signatur-/Notarisierungsnachweise vorhanden; sie sind als `external_dependency` ausgewiesen (nicht als PASS getarnt) | Windows-Codesign + Apple Developer-ID/Notarization-Profil über den geschützten Weg, oder Distributionsziel für Linux-only autorisieren |
| 6 | echte Computer-Use-Pfade pro beanspruchter Plattform belegt | 🔴 offen | Kein realer GUI-Lauf; kein Subtool, kein GUIState; Container-GUI-Image fehlt (`docs/audit/COMPUTER_USE_SUBTOOLS.md`) | GUIState-Vertrag + ein Subtool (§52/§55) bauen, Container-GUI-Image bereitstellen, dann §57 je beanspruchter Plattform fahren |
| 7 | Docker/Discovery/Treasury besitzen reale Nutzerwege | 🟡 2 von 3 | Docker: live belegt (digest-gepinnt, echte Artefakte). Discovery: Testset eingefroren, Live-Lauf offen. Treasury: `unknown`, keine TypeSafe-Verbindung | Discovery-Runner gegen das eingefrorene Testset fahren; Treasury-Setup mit echtem Secret-Handle + autorisiertem Zielaufruf |
| 8 | Voice hat falsche Positivfälle **und** produktive Gegenbeispiele bestanden | 🔴 offen | Stufe C (24 problematische + 24 produktive Verläufe, getrennter Holdout) ist nicht gefahren; Precision/Recall existieren nicht | Stufe C einfrieren und fahren (`docs/BENCHMARK-RUN-SCHEMA.json` + `tools/eval_benchmark.py`) |
| 9 | Recovery-/Unknown-Side-Effect-Pfade bestanden | 🟡 teilweise | RF-09 fenced beide Crash-Fenster (`integration_tested`, 8 Pins, echter SIGKILL); der Live-Crash gegen einen echten Provider fehlt; der nebenläufige Batch-Dispatcher ist **nicht** gefenct | Live-Crash-E2E (braucht RF-01-Freigabe) und Fence auf `execute_tool_calls_concurrent` ausweiten |
| 10 | Auditpaket reproduzierbar | ✅ erfüllt | `build_audit_register.py --check` grün (97 Kriterien), `check_findings_criteria.py` grün, `ARTIFACT-MANIFEST.json` mit echten Hashes, Rohdaten + Auswerteskript vorhanden | — (erledigt); bei jedem weiteren Commit `--check` erneut laufen lassen |
| 11 | Jev/System One nur im wirklich getesteten Umfang als fertig bezeichnet | ✅ Regel befolgt | Claim-Level unten ist `not_started`; alle JEV-Zeilen sind `unknown` und werden nicht „fertig" genannt | — (Regel); inhaltlich: siehe §117.1 |

**Ehrliches Gesamtergebnis:** Bedingung 10 ist erfüllt, Bedingung 2 und 11 sind
Regel-/Registerbedingungen und werden eingehalten, Bedingung 3 und 5 sind korrekt bewertet,
Bedingungen 1, 4, 6, 8 sind **offen**, 7 und 9 nur teilweise. → Globales `completed` ist
**unzulässig**. §118 greift: die übrige Arbeit läuft weiter, die konkreten Punkte bleiben
`blocked`/`not_run`, ein startbarer Kandidat darf geliefert werden.

### 3.1 §117.1 — Jev-Claim-Level heute

```text
not_started
```

Begründung: von den drei erlaubten Aussagen ist keine erreichbar.
`JEV_READY` verlangt vorbereitete Providerabstraktion, Contracts, Questions, Sanitizer,
Decision Policy, LLM-Adapter, Shadowharness, Computer-Use-GUIState **und** Tests — heute
existiert davon nur ein uncommitteter Teilentwurf ohne GUIState und ohne Shadowdaten.
`JEV_LIVE_TESTED` verlangt einen autorisierten echten TypeSafe-Aufruf — es gibt kein
Credential (Early Access). `JEV_ACTIVE_VERIFIED` ist damit ausgeschlossen.

Erlaubte Formulierung für eine Außenantwort: „Jev/System One: **nicht gestartet**; ein
vorbereitender Entwurf arbeitet im Branch, ist aber uncommittet und nicht nachgewiesen."
Verboten: „Jev integriert", „System One läuft", „ready".

---

## 4. §119-Format — was heute hineingehört (Gerüst, vom Integrator zu füllen)

Die letzte Antwort beginnt mit dem tatsächlich startbaren Ergebnis (Pfad/Version), dann:

| §119-Block | Heute belegbarer Inhalt | Nicht belegbar (nicht schreiben) |
|---|---|---|
| 1. Builds/Plattformen | Linux x64: nativer Build `~/.hermes/godsv/app/linux-unpacked/gods-voice` (203 MB ELF) + Launcher; Windows/macOS `external_dependency` | „alle Plattformen fertig" |
| 2. Nutzbare Nutzerwege | Chat (Plugin-Smoke 158/0), CLI (`godsvoice …`), Docker-Kette (live), Discovery (Testset, Lauf offen), Treasury (offen), Computer Use (**nur** dry-run) | „Computer Use verifiziert" |
| 3. Jev/System One | `JEV_READY` (§117.1): providerneutraler Vertrag, Questions, Sanitizer, Policy, LLM-Adapter, Shadow, GUIState und Precheck implementiert und committet (`411a3f8`, `4d68666`), Integrator-Gegenprüfung `tools/jev_contract_probe.py` 31/31; echter Jev-Zugang NICHT live getestet, Beleg: `docs/audit/SYSTEM_ONE_QUESTIONS.json` + §117.1 | „Jev integriert" |
| 4. Kriterienstatus | 97 IDs, 48 gemappt, 49 `unknown`, 0 `satisfied` — Spalte „aus dem Register" | „alle Tests bestanden" |
| 5. Wichtige Reparaturen | SEC-01/05/06/12/13/17 + UX16-01 (Finder → Fix-Commit → frischer Repro) | unbelegte Fixbehauptungen |
| 6. Benchmarks | `not_run` — Schema eingefroren, keine Rohdaten (`docs/audit/BENCHMARK-RAW-README.md`) | erfundene Kosten/Latenz |
| 7. Independent Findings | 36 Findings; nachgerechnet aus den Findings selbst: 2 P0 (beide `fixed_retested`), 5 P1, 19 P2, 7 P3, 3 Negativbefunde — **kein offenes P0/P1**, offen sind 21 P2/P3 | „Security abgenommen" |
| 8. Blocker | Build-Hosts/Signierung (4), Jev-Early-Access, Budgetfreigabe RF-01, Container-GUI-Image, fehlende Gerätezeit | — |
| 9. Resume | siehe `audit/RESUME.md` dieser Welle; unmittelbarer nächster Schritt: System-One-Welle committen + die 6 Nachfahr-Proben als Test pinnen | — |

Verbote nach §119 („alles funktioniert", „production ready", „alle Tests bestanden",
„Jev integriert", „Computer Use verifiziert", „alle Plattformen fertig") werden in dieser
Welle nicht verwendet und dürfen ohne Beleg nicht verwendet werden.

---

## 5. §114-Pflichtartefakte — Ist-Stand

| Pflichtartefakt (§114) | Pfad | Stand |
|---|---|---|
| `REQUIREMENTS.json` | `audit/REQUIREMENTS.json` | ✅ 97 Kriterien, `--check` grün |
| `CODE_MAP.md` | `audit/CODE_MAP.md` | ✅ generiert, `--check` grün |
| `TEST_REPORT.md` | `docs/audit/TEST_REPORT.md` | ✅ erneuert (exakter Lauf + Trennung Suite↔Live) |
| `PERFORMANCE.md` | `docs/audit/PERFORMANCE.md` | ✅ neu, mit echten Messungen + klaren „nicht gemessen"-Zeilen |
| `BENCHMARK_MANIFEST.json` | `docs/audit/BENCHMARK_MANIFEST.json` | ✅ vorhanden (schema-frozen, keine Ergebniswerte) |
| Benchmark-Rohdaten JSON/CSV/JSONL | `outputs/rf/<stamp>/raw.jsonl` | 🟡 RF-Runde vorhanden; §97-Armdaten fehlen |
| Auswerteskript | `tools/eval_benchmark.py` (+ `tools/gv_benchmark.py`) | ✅ neu/erweitert, fail-loud ohne Rohdaten |
| `SKILLS_USED.md` | `docs/audit/SKILLS_USED.md` | ✅ vorhanden |
| `UPDATE_COMPATIBILITY.md` | `docs/audit/UPDATE_COMPATIBILITY.md` | ✅ vorhanden |
| `RELEASE_NOTES.md` | `docs/audit/RELEASE_NOTES.md` | ✅ vorhanden |
| `RESUME.md` | `audit/RESUME.md` | ✅ vorhanden (Welle aktualisiert) |
| redigierte Rohlogs | `outputs/rf/…`, Probe-Ausgaben | 🟡 vorhanden, nicht neu erzeugt |
| Screenshot-/Artefaktmanifest mit Hashes | `docs/audit/ARTIFACT-MANIFEST.json` | ✅ neu, echte sha256, keine Platzhalter |
| Plattformmatrix | `docs/audit/PLATFORM-MATRIX.md` | ✅ erweitert (§74 + Jev-Zeile) |
| `findings.json` | `audit/findings.json` | ✅ neu, 36 Findings im §113-Schema |
| `SYSTEM_ONE_REPORT.md` | — | ⬜ fehlt **bewusst**: §116 verlangt Provider/Modell/Thresholds/Holdout — es gibt nichts davon |
| `SYSTEM_ONE_QUESTIONS.json` | `docs/audit/SYSTEM_ONE_QUESTIONS.json` | ✅ neu, Vertrags-Fragesatz + erwartete Codequelle |
| `COMPUTER_USE_SUBTOOLS.md` | `docs/audit/COMPUTER_USE_SUBTOOLS.md` | ✅ neu; sagt ausdrücklich, dass **kein** Subtool existiert |

---

## 6. Zustand des Arbeitsbaums bei Erstellung (nicht unterschlagen)

Zum Zeitpunkt dieses Schreibens arbeiteten **parallele Wellen** im selben Baum. Das ist für
jede Belegaussage relevant, weil ein HEAD allein den Baum nicht beschreibt (§5: der
Workspace-Fingerprint muss uncommittete Änderungen berücksichtigen):

| Änderung | Herkunft | Wirkung auf dieses Auditpaket |
|---|---|---|
| `src/godsv/system_one*.py` (6 Dateien, untracked) + Änderungen an `computer_use.py`, `events.py`, `llm.py`, `voice.py` | System-One-/JEV-Welle | Die 6 neuen Testmodule laufen grün (117 Tests, 1.46 s); ein Suite-Lauf **mitten in dieser Schreibphase** (19:10) war rot (1255 passed / 49 failed / 57 errors, danach pytest-`INTERNALERROR`), der **letzte kanonische Lauf um 19:25** war wieder grün: 1632 gesammelt, 1625 passed, 7 skipped, RC=0 — Details in `docs/audit/TEST_REPORT.md` |
| `docs/RF-LONGRUN-FREIGABE.md`, `tools/rf_stage1_compose.sh` | RF-Welle | kein Einfluss |
| `audit/REQUIREMENTS.json` (Kopf `observed_project_head`) | diese Welle (`build_audit_register.py`) | nötig, damit `--check` grün bleibt |

Diese Welle hat ausschließlich `audit/`, `docs/`, `tools/` geschrieben; kein `src/`,
`integrations/`, `host-overlay/`, `app-overlay/` oder `tests/`. Keine Commits.

## Nachtrag: JEV-Welle A+B integriert (Commit 7736950)

Beide Hälften des §33-Auftrags sind integriert und **vom Integrator selbst nachgeprüft**, nicht auf
Bericht geglaubt: im JEV-/System-One-/Completion-/Computer-Use-Scope 560 passed / 4 skipped, die
Gesamtsuite 2001 passed / 7 skipped (RC=0), ruff clean, bandit 0, die drei Proben grün.

Dabei zwei Fehler gefunden, die **keiner** der beiden Worker-Suiten auffallen konnten:

1. **§10.3-Pack war produktiv unerreichbar.** `computer_use_v2` (18 Fragen) konnte nicht ins
   produktive Register, weil Worker A dort die §5-Metadaten erzwingt und Worker Bs Pack kein Feld
   davon deklarierte. Beide Suiten grün, die Kombination blockierte. Behoben (Metadaten analog
   `COMPUTER_USE_V1`, keine erfundenen Zahlen) und verdrahtet; drei Pins auf „produktiv = v1" mit
   §10.3-Begründung nachgezogen. Nachgeprüft: `default_question_registry().pack('computer_use').ref`
   = `computer_use_v2`, 18 Fragen, Legacy-ID `computer_use.recovery_needed` weiterhin enthalten.
2. **Zwei CLIs enden mit rc=2 statt Nullen zu drucken** — erste Hand ohne Pipe gemessen (mit Pipe
   hätte man den Status von `tail` gelesen), mündet in JEV-11/JEV-19 als belegter Zustand.

Die fünf zuvor `unknown` JEV-Kriterien (08, 11, 17, 18, 19) sind mit eigenen Läufen belegt und mit
ehrlichem Annahmestatus eingetragen: blockiert auf externe Voraussetzung (TypeSafe-Handle,
Entwicklungsdatensatz, rohe Läufe) bzw. offen ohne echten Desktop.
