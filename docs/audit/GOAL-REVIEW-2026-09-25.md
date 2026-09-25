# Prüfung für den nächsten Hermes-Goal

Stand: 25. September 2026. Lesender Dokument-/Codestichprobenaudit; keine neue Runtime-Abnahme und keine Ausführung der gemeldeten 247 oder 7648 Tests. Basis: HEAD `89b02a7`, enthält `2c358c6`; zusätzliche lokale Änderungen vorhanden. Kein Pushstatus eigenständig geprüft.

## Wesentliche Schlussfolgerung

Vorhandene Bausteine jetzt in den normalen Produktlauf integrieren. Weitere Klassen, CLI-Demos oder Auditanhänge allein schließen die Anforderungen nicht. Nutzerpriorität bleibt: normale Agentenfunktionen, Geschwindigkeit und TUI; besonders Computer Use, Browser, Subtools und Tool Builder. Scheduler ist eine echte Lücke, aber seine Größe allein macht ihn nicht zum ersten Arbeitspaket.

## Gelesene Quellen

AGENTS.md, HANDOFF.md, STARTPROMPT.md, docs/audit/IMPLEMENTATION-AUDIT.md einschließlich C3/C4/C5/C6 und Korrekturen, VALIDATION.md; relevante Masterplan-/Abnahmeabschnitte und Browser-Upgrade. Codeproben: act_service.py, cu_host_session.py, desktop_lease.py, stop_signal.py, approval_modes.py, surface_cli.py, desktop_indicator.py sowie CLI/REPL-Einstieg und zugehörige Testdateien.

## Befunde für den nächsten Implementierer

1. **Arbeitsbaum weiter als der zitierte Commit.** Lokale Änderungen an `act_service.py`, `surface_cli.py`, `tests/test_desktop_lease.py`; neue `desktop_indicator.py` und `tests/test_desktop_indicator.py`. Dazu lokale Planänderungen. Vor Arbeit erneut prüfen und übernehmen, keinen Reset oder parallelen Neubau starten.
2. **DESK-02 braucht den gesamten Aufrufpfad.** Der Adapter prüft einen gebundenen Fence. Im gelesenen lokalen Zwischenstand existieren `_bind_desktop_fence` und `_execute_action_list(on_runner=...)`, aber `run_action_list` übergibt den Hook nicht. Der neue Parameter `desktop_lease` wird in den gefundenen Aufrufen nur von Tests übergeben. Damit ist die automatische Produktionsbindung in dieser Momentaufnahme nicht belegt. Hookfehler werden außerdem nur als `hook_error` notiert; eine zwingende Kontrollbedingung darf nicht fehlertolerant übergangen werden. Erneut prüfen, da aktive Änderungen möglich sind.
3. **Prozessübergreifende Lease-Sperre nicht belegt.** `desktop_lease.py:175ff` behauptet im Docstring einen fcntl-Lock; der gelesene Code nutzt `threading.RLock`, ohne fcntl-Aufruf. Das schützt nicht zwei Prozesse oder unabhängige Store-Instanzen. Atomarer Dateiersatz allein schützt kein Read-modify-write. Multiprozess-Konkurrenztest und tatsächliche transaktionale Sperre erforderlich. Beschädigtes JSON wird als leer behandelt: auch diesen Fall auf unzulässige Eigentumsübernahme prüfen.
4. **Indikator teilweise weitergebaut.** Der neue X11-/XWayland-Renderer und CLI-Show/Hide existieren lokal. Automatische Bindung an Desktop-Lease/Run, dauerhafte Aktualisierung und tatsächliche Sichtbarkeit auf dem Compositor sind nicht nachgewiesen. Show/Hide nutzen pro Aufruf ein neues Objekt; ein Hide in einem anderen Prozess erreicht laut Code die frühere Instanz nicht. Lifecycle eindeutig dem Run/Service zuordnen.
5. **STOP ist mehr als eine Betriebssystemberechtigung.** `stop_signal.py` enthält Probes und Detektor, aber in den untersuchten Pfaden keinen laufenden physischen Listener. Gruppenaufnahme kann einen Backendzugriff ermöglichen, ersetzt Listener, Herkunftsprüfung, Pausierung, Queue-Verwerfung und Release-Gate aber nicht. `write_report` meldet bisher ausdrücklich unbewiesen. Ein tatsächlicher Test muss die spätere Freigabe beeinflussen.
6. **Approval-Modi noch nicht wirksam belegt.** `approval_modes.policy_for` wird in der untersuchten Produktfläche für CLI-Ausgabe verwendet; eine Verbindung zur echten Approval-Engine fehlt weiterhin. Gleichnamige Funktionen aus `subagent_roles` sind kein Gegenbeleg. Warteaktionen, Moduswechsel und versionsgebundene Freigaben müssen im realen Dispatch geprüft werden.
7. **TUI-Renderer ist kein fertiger Einstieg.** Bare CLI führt über REPL zu Chatbar. Audit C4 nennt selbst offene Live-Events, Event-Cursor und Tastaturbindung. Der neue Renderer kann wiederverwendet werden; maßgeblich ist, was beim tatsächlichen `skaldir` erscheint und während realer Toolaufrufe bedienbar bleibt.
8. **Dokumentation enthält historische Widersprüche.** Frühe Tabellen bezeichnen Scheduler als teilweise vorhanden (`nightshift.py`), C3 stellt das Fehlen der Zeitplan-Entität fest. C6 enthält alte Befunde zu DESK-02/APR, die unten korrigiert werden. Historie erhalten, aber eine eindeutige aktuelle Statusübersicht voranstellen. Teststufe und Erfüllungsstatus getrennt führen.
9. **APR-05 nicht durch Docstringkorrektur vollständig erfüllt.** Die Masterplanforderung erlaubt einen konfigurierbaren Default für neue Runs und untersagt stille globale Eskalation. Das ist vereinbar durch explizite Änderung mit Scope, Persistenz und Audit. Ein falscher Docstring wurde korrigiert; Persistenz/Produktintegration trotzdem prüfen.
10. **Produktabnahmen C–F blockieren nicht die Entwicklung.** Anwendungs-/Objektwahl gezielt klären; Testplan, lokale Fixtures und Infrastruktur können unabhängig entstehen. Bestätigte Vorgaben nicht durch ausgedachte Nutzerentscheidungen ersetzen. Keine kostenpflichtigen Quellen oder echten Käufe für Testläufe voraussetzen.

## Empfehlung

Siehe `../../GOAL-PROMPT-HERMES.md`. Ein zusammenhängender Goal mit klaren Etappen, ehrlicher partieller Blockade und messbarer Produktabnahme. Keine neue Migration oder Repo-Erstellung; diese sind laut vorliegenden Projektunterlagen erledigt. Keine Tests wiederholen, nur weil ältere Logs erneut auftauchen; Resultate an Revision und Zeitpunkt binden.
