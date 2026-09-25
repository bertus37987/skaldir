# Audit: Grundfunktionen und ihre Erreichbarkeit im Agenten

Stand: 25. September 2026. Geprüfter HEAD: `225e7c0`. Untracked Nutzerarbeit: `tests/fixtures/blender/`, `tests/fixtures/video/`; nicht verändert. Lokaler Code- und Dokumentaudit mit isolierten Ausführungsproben. Kein bezahlter Modelllauf, keine persönlichen Browseraktionen und kein neuer physischer Notstopp-Test.

## Ergebnis

**Code Execution existiert als funktionierender Python-Runner. Sie fehlt jedoch als eigenes Werkzeug in den untersuchten Modell-Toolsets.** Shell und weitere Kernwerkzeuge sind ebenfalls vorhanden, werden aber abhängig von Einstieg und vorheriger Registry-Einrichtung angeboten. Der Goal-Run verwendet eine besonders schmale separate Werkzeugliste. Damit ist die Sorge über fehlende Grundfunktionen auf Produktebene berechtigt, obwohl viele Module und Komponententests existieren.

Der frühere Audit darf nicht unverändert wiederholt werden: Desktop-Locks/Fencing, Stop-Komponenten, Browseradapter und Zeitplandaten wurden seitdem erweitert. Das aktuelle Problem ist weiterhin die vollständige Integration. Behauptungen wie „letzte lokal implementierbare Lücke“ sind mit den unten reproduzierten Befunden nicht vereinbar.

## Nachweise und Grenzen

1. Tatsächlich ausgeführt: `env -u SKALDIR_HOME -u HERMES_HOME -u GODSV_HOME PYTHONPATH=src .venv/bin/python -m pytest tests/test_code_exec_tools.py tests/test_code_exec_sandbox.py tests/test_tools_core_tools.py tests/test_agent_toolset.py -q -o addopts= -p no:randomly`.
2. Ergebnis: **99 passed in 14.37s**, Exit 0. Diese Auswahl prüft Komponenten, nicht den vollständigen natürlichen Chat-/Goal-Auftrag.
3. Isolierte Probe gegen echte Toolset-Konstruktoren, echte Core-Registry und echte Python-/Shell-Ausführung: `logs/basic-agent-audit-probe.json`. Temporäre Workspace-/Runtime-Verzeichnisse; Registry-Anlage ohne Netzwerkprobe. Reproduktionsskript: `logs/basic-agent-audit-probe.py`.
4. Separate harmlose `pwd`-Probe über den registrierten AgentTool-Handler bestätigt den Workspaceverlust. Konfigurierter Workspace war `/tmp/skaldir-context-audit-…/workspace`; Ausgabe/cwd waren `/home/willy/Agentic_Projects/Skaldir_Agent`.
5. Ein bestehender Agent-Toolset-Test schrieb trotz temporärer Testfixtures drei `agent-toolset`-Events und einen ACT-Usage-Eintrag in getrackte Repository-Laufzeitdateien. Die ausschließlich diesem Testlauf zugeordneten Ergänzungen wurden geprüft, unter `logs/core-audit-test-side-effect-*` gesichert und die Dateien auf ihren vorherigen Inhalt zurückgesetzt. Testisolation sollte ebenfalls korrigiert werden.
6. Globale installierte persönliche Runtime und deren Registry wurden nicht inventarisiert. Eine dort bereits registrierte Shell kann im passenden Einstieg nutzbar sein. Dies widerlegt weder den frischen-Setup-Befund noch die gemessene separate Goal-Werkzeugliste.

## F01 — P0: unterschiedliche Einstiege bieten unterschiedliche Grundfähigkeiten

Code: `agent_loop.py:builtin_tools`, `agent_toolset.py:build_agent_toolset`, `agent_loop_cli.py:549–563,774–778`, `goal_run.py:turn_tools`.

Gemessene Listen:

- Frisches `build_agent_toolset` mit Default sowie mit `kind=both`: `read_text`, `list_dir`, `sha256_file`, `write_text`, `tool_search`, `knowledge_search`, `knowledge_read`, `skill_expand`, `skill_expand_build`, `skill_act`, `skill_act_check`, `skill_compose`, `skill_verify`, `skill_verify_flows`.
- Goal `turn_tools` ohne Delegationsprovider: **`read_text`, `list_dir`, `sha256_file`, `write_text`, `edit_apply`, `run_tests`**. Mit Provider wird zusätzlich Delegation angeboten; das ersetzt die fehlenden Hauptagentenwerkzeuge nicht.
- Nach ausdrücklicher Core-Registrierung erscheinen in `kind=both` zusätzlich Terminal, Patch, Dateisuche, Webzugriff und Docker-Ensure.
- `agent run` hat den Parserdefault `--tools builtin`. Der Computer-Use-Slashpfad setzt `--agent-tools`, aber in seinem `task_argv` kein `--tools both`.

Folge: Der Agent hat nicht in allen normalen Einstiegen dieselben Basisfähigkeiten. Besonders ein Goal kann weder den neuen Browseradapter noch die registrierte Shell über reguläre eigene Tools verwenden. CLI-/Edit-/ACT-Umwege sind keine konsistente Tooloberfläche. Eine Korrektur muss den tatsächlichen Modellrequest und Dispatcher jedes Einstiegs erfassen, nicht nur den Factorydefault ändern.

## F02 — P1: Python-Codeausführung funktioniert, ist aber nicht angeboten

Code: `code_exec.py:run_code`, `code_exec_cli.py`, `tools_core/registry.py:TOOL_MODULES`, `agent_toolset.py:build_agent_toolset`, `goal_run.py:turn_tools`.

Reale Probe: `print('code-ok'); result = sum([2,3,5])` ergibt `ok=true`, `value=10`, `stdout='code-ok\n'`, Exit 0. Die Tests prüfen unter anderem Fehler, Toolverkettung, Budget und Timeout.

Weder die neun Core-Contracts noch die gemessenen Agent-/Goal-Toolsets enthalten einen Code-Runner. Die CLI `skaldir code run` ist vorhanden. Benötigt wird ein dünner, typisierter Adapter mit vorhandener Policy/Run-ID und Artefakten. Kein zweiter Code-Interpreter erforderlich.

Der bestehende Runner unterstützt Python, ist pro Aufruf neu und hat maximal 120 Sekunden. Terminal kann grundsätzlich andere vorhandene Interpreter ausführen. Eine langlebige Python-/Node-Session ist ein eigener Ausbau, nicht Voraussetzung, um CORE-06 zunächst sinnvoll zu erfüllen.

## F03 — P0: registrierte Core-Tools verlieren den expliziten Workspace

Code: `agent_loop.py:registry_tools/_call_registry_tool`, `tools_core/bridge.py:_call`, `tools_core/sandbox.py:resolve_root`.

`build_agent_toolset(workspace=W, runtime=R, kind='both')` bindet die Registry-Handler nicht an W. Die Core-Bridge ruft `registry.call(..., runtime=default_runtime_dir())` ohne `root` auf. `resolve_root(None)` nimmt das Prozess-cwd.

**Reproduziert:** `terminal.handler(command='pwd')` lief im Repository-cwd statt im separat konfigurierten temporären Workspace. Auch Runtime wird in dieser Bridge erneut global aufgelöst; die Runtime-Abweichung wurde hier statisch festgestellt, nicht durch Schreiben in fremde Runtime getestet.

Kontext explizit durchreichen: Workspace, Runtime, Run/Session, Approval, Cancellation. Kein globales `os.chdir` oder Umgebungsvariablen-Umschalten pro parallelem Toolcall. Zwei Runs müssen gleichzeitig unterschiedliche Workspaces behalten.

## F04 — P1: Terminal ist ein synchrones Kommando, keine vollständige Prozessverwaltung

Code: `tools_core/terminal.py:_run/run`, Inputschema und Timeoutvertrag.

Vorhanden: Shell, cwd, bereinigte Umgebung, vorab übergebene stdin-Daten, stdout/stderr, Signal/Exitcode, Timeout bis 600 Sekunden. Nicht im untersuchten Terminalvertrag vorhanden: Session-ID, Poll/Weiterlesen, nachträgliche stdin-Eingabe, PTY, kontrollierte langlebige Prozesse. `_run` wartet über `communicate`; Ausgabe wird erst danach gekürzt. Der Rückgabecap ist deshalb kein echter RAM-Cap während laufender Ausgabe.

Für Devserver, längere Builds und interaktive Werkzeuge einen betreuten Prozessvertrag ergänzen oder einen vorhandenen geeigneten Prozessmanager integrieren. Bestehende andere Runtime-Prozessmanager vorher untersuchen; nicht behaupten, im gesamten Projekt existiere keinerlei Prozesssteuerung.

Wichtig: `terminal` meldet bei erfolgreicher Ausführung des Tools auch für Command-Exit 7 `ok=true`, und `output.exit_code=7`. Das ist ausdrücklich dokumentierter Transport-/Beobachtungsvertrag, kein eigener Bug. Übergeordnete Taskverifikation muss den Command-Exit lesen.

## F05 — P1: Projekttests im Goal sind auf pytest festgelegt

Code: `goal_run.py:turn_tools.run_tests`, Aufruf `execute_check('pytest', ...)`; leeres Inputschema.

Der Goal-Agent kann dort keinen npm-/Node-/Rust-/anderen Projektrunner auswählen. Er benötigt eine projektbezogene Tests-/Buildausführung über den allgemeinen Executor mit strukturierten Resultaten. Runner anhand Projektregeln/Metadaten ermitteln, nicht beliebig ausführen. Keine Tests gefunden bleibt ein eigener Zustand.

## F06 — P1: sichere Lese-Tools werden pauschal serialisiert

Code: `agent_loop.py:registry_tools` setzt für alle Registry-Tools `parallel_safe=False`, `mutating=True`.

Gemessen nach Core-Registrierung: `read_file`, `search_files`, `web_fetch`, `web_search` alle `parallel_safe=False`. Das widerspricht dem gewünschten schnellen unabhängigen Batching. Für vertrauenswürdige Core-Contracts Effekte/Ressourcen ableiten; unbekannte generierte Tools weiterhin konservativ behandeln. Nicht pauschal alles parallel schalten.

Nebenbefund: `list_dir` und `list_dir_2` werden gleichzeitig angeboten (Builtin/Registry-Duplikat). Gemeinsame Capability-Identität und Adapter statt doppelter Modellwerkzeuge bevorzugen.

## F07 — P1: Tool Builder im Agenten bekommt weiter keinen Modellclient

Code: `agent_toolset.py:skill_expand_build` baut `ToolBuilder(..., store=..., session_id=...)` ohne `llm`; `toolbuilder.py:ToolBuilder.__init__` speichert das übergebene `llm`, Default None.

Builds mit geliefertem Quellcode können funktionieren. Selbstständiges Drafting aus einem Bedarf ist über diesen Adapter nicht belegt und bleibt ohne weitere Quelle am fehlenden Client hängen. Vorhandenen Run-Provider/Budget korrekt injizieren. `tests/test_toolchain_end_to_end.py` prüft einzelne Stationen und vorbereitete Artefakte; es beweist keinen vollständigen Prompt→Modell→Build→Registrierung→Agent-Aufruf-Lauf.

## F08 — P1: Browseradapter vorhanden, reguläre Agent-Toolbindung fehlt

Code: `browser_layer.py:BrowserLayer`, `surface_cli.py` Browser-Commands, Agent-Toollisten.

Browserlayer und Backendaktionen existieren inzwischen. In den gemessenen Toolsets fehlen `browser.*`-Adapter; die im Quellcode gefundenen produktiven Referenzen konzentrieren sich auf die CLI. `skill_act` bietet einen anderen Weg, belegt aber nicht die Integration des neuen Layers. Typisierte Browserwerkzeuge in denselben Dispatcher einhängen; ursprüngliche Zen-/Hostanforderung bleibt.

## F09 — P1: Zeitplandaten sind noch kein ausführender Scheduler

Code: `scheduler.py:ScheduleStore` und `surface_cli.py:schedule.*`.

Vorhanden: Definition, Termine/DST, Anspruch, Abschluss, Recovery und `due`. In der untersuchten Produktionssuche kein Dienst/Worker gefunden, der fällige Termine regelmäßig abholt und tatsächlich einen Workflow startet. Die CLI bietet define/activate/pause/delete/list/due. Aussage „läuft bei geschlossener TUI“ braucht einen realen Worker- und Neustartnachweis. Vor Neubau auch vorhandenen Run-Supervisor untersuchen.

## F10 — P1: TUI-Eventfeed ist bislang vor allem Transcript-Preview

Code: `tui_feed.py`, `surface_cli.py:_tui_state`, `repl.py:launch_from_bare_call`.

Die produktive Referenz auf `state_from_transcript` liegt in der Preview-Fläche; der Bare-Start delegiert weiter an Chatbar. Ein Transkript in den neuen Renderer einzulesen beweist keine interaktive Vollbild-TUI im echten Standardpfad. Live-Input, Renderloop, Moduswechsel, Tooldetails, Reattach und Resize über den echten Einstieg abnehmen. Vorhandene Chatbar-Funktionen nicht mit vollständigem neuem TUI-Nachweis gleichsetzen.

## F11 — P2: Dokumente und Nachweis-IDs widersprechen sich

`HANDOFF.md` beschreibt weiterhin alten Launcher, blockierten Stop und fehlende Schedulerteile. `docs/audit/STATUS-2026-09-25.md` ergänzt spätere Änderungen, behält aber alte Gesamtzahlen und Blocker. Die Browser-Tabelle verwendet BR-08 für Installation/Lizenz, während der verbindliche Browserplan BR-08 als globalen Doppel-Escape-Test definiert; weitere Zuordnungen wurden ebenfalls verschoben. Dadurch erscheint eine andere Prüfung unter derselben ID grün.

Neuer Beleg `docs/audit/STOP-05-LIVE-PROOF.md` dokumentiert physischen Doppel-Escape via libinput unter Wayland, X11 weiterhin offen. Diesen Fortschritt anerkennen; nicht erneut pauschal „input-Gruppe fehlt“ behaupten. Der vorliegende Audit hat den physischen Test nicht wiederholt und bewertet die komplette dauerhafte Produktionsanbindung damit nicht automatisch als bestanden.

## Weitere Grundlagen: Bestand statt Pauschalurteil

- Dateien lesen/schreiben und Patch/Suche existieren. Im Goal fehlen jedoch die Core-Such-/Patchoberflächen zugunsten begrenzter Builtins und edit_apply; Teilbereichslesen und stabile Revisionen über den tatsächlichen Einstieg prüfen.
- Web-Fetch und kostenlose Suchbackends existieren. DuckDuckGo ist im Code ausdrücklich Instant Answer, Marginalia ein eigener Index. Keine universelle Vollständigkeit oder aktuelle Verfügbarkeit daraus ableiten; in diesem Audit keine Live-Websuche durchgeführt.
- Git kann nach korrekter Shellintegration über tatsächliche Git-Kommandos bedient werden; ein eigenes Git-Tool ist keine notwendige Neuentwicklung. Eigene Änderungen und Nutzerarbeit unterscheiden.
- Codeausführung ist keine garantierte Sicherheitsisolation: Runner dokumentiert fehlenden OS-Dateisystemkäfig. Direkte Python-Datei-/Prozessoperationen passieren nicht automatisch die Tool-API. `-I/-S` und cwd sind keine Sandbox. Terminal fällt bei nicht verfügbarem angefordertem bubblewrap auf Prozessmodus zurück. Wenn Isolation Pflicht ist, verweigern statt abschwächen; bewusst erlaubte Hostausführung getrennt kennzeichnen.

## Neuer Auftrag

`../../GOAL-PROMPT-HERMES-CORE.md` priorisiert Erreichbarkeit, Kontextbindung und vollständige Produktabläufe. Bestehende Engine wiederverwenden. Keine neue Runde reiner Statuspflege als Ersatz für die Implementierung.
