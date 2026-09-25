# Neuer Hermes-/goal-Prompt: Skaldirs Grundfähigkeiten vollständig integrieren

Den folgenden Text als Goal in Hermes verwenden. Dieser Auftrag konkretisiert den bisherigen Masterplan anhand des aktuellen Audits; kein Rewrite und keine erneute Migration.

## Goal-Text

Arbeite in `/home/willy/Agentic_Projects/Skaldir_Agent`. Mache Skaldir zu einem tatsächlich benutzbaren Coding-Agenten: Code ausführen, Shell-Kommandos und Prozesse steuern, Dateien gezielt lesen/suchen/ändern, Projekttests und Builds starten, Git prüfen, Web recherchieren und Browser bedienen — aus dem normalen Chat und dem Goal-Run, mit gemeinsamen wirksamen Freigaben, Streaming und nachvollziehbaren Ergebnissen.

Lies zuerst AGENTS.md, `docs/audit/CORE-FUNCTIONS-AUDIT-2026-09-25.md`, die aktuellen Implementierungs-/Validierungsberichte, `docs/masterplan/00-START-HIER.md`, den Masterplan und `07-BROWSER-UPGRADE.md`. HEAD beim Audit war `225e7c0`; prüfe den aktuellen Stand, bevor du Befunde übernimmst. Untracked Video-/Blender-Fixtures erhalten. Fortschritte seit dem vorigen Goal bewahren, besonders Desktop-Locks, Stop, Browserlayer und Zeitplanmodell. Keine fremden Sessions ändern oder alte Projektkopien bearbeiten.

### Ziel und Arbeitsweise

Nicht weitere Grundfunktionen auf Verdacht neu bauen. Bestehende Engines benutzen und ihre realen Aufrufpfade schließen. Prüfe jeden Einstieg vom Nutzerprompt über tatsächliche Modell-Tooldefinition, Argumentvalidierung, Approval und Dispatcher bis zum Executor und Resultat. Fehlende Adapter sind Implementierungslücken, obwohl die Engine separat funktioniert.

Arbeite in der unten stehenden Reihenfolge weiter. Aktualisiere Kriterien und Status pro Paket; frage nicht nach jedem Paket nach dem nächsten Weg. Persönliche Konten und Host-Liveprüfungen sind für die meisten dieser lokalen Korrekturen keine Voraussetzung. Codearbeit bleibt beim Hauptagenten.

### Paket 1 — Gemeinsame Grundfähigkeiten für alle Agenteneinstiege

Inventarisiere Bare-Chat, `agent run`, Computer-Use-Chat und Goal-/Run-Modus mit ihren tatsächlichen Toolarrays. Der Audit fand unterschiedliche Listen: frische allgemeine Factory ohne Core-Tools und Goal mit nur Datei-Builtins, edit_apply und festem pytest-Tool.

Baue eine gemeinsame kontextgebundene Capability-Zusammenstellung über den vorhandenen Code. Kernfähigkeiten müssen ab einem unterstützten frischen Setup erreichbar sein, ohne dass der Nutzer versteckte Registry-Befehle kennt. Registry/Discovery beibehalten, aber bloß auffindbare Beschreibungen nicht als ausführbare Tools ausgeben. Fehlende Installation/Policy präzise anzeigen. Gleiche Fähigkeit nicht gleichzeitig als list_dir/list_dir_2 und ähnliche Duplikate anbieten.

Ergänze einen typisierten Modelladapter zum vorhandenen `code_exec.run_code`: Python-Code, Workspace, Zeit-/Outputbudget, Ergebnis, stdout/stderr, Fehler/Traceback und Artefaktreferenzen. Kein zweiter Python-Runner. Vorhandene weitere Laufzeiten können über den Terminalexecutor ausgeführt werden. Eine persistente Notebook-Runtime ist für diese erste Abnahme kein Muss.

Die normalen Terminal-, Such-, Patch-, Web- und Browserfähigkeiten gehören auch in den Goal-Run, soweit dessen Policy sie erlaubt. Ein Goal darf nicht durch eine abweichende fest codierte Minimaltoolbox praktisch handlungsunfähig sein. Werkzeuge bei Bedarf nachladen ist erlaubt, wenn der gesamte Discovery→Call-Pfad funktioniert.

### Paket 2 — Workspace/Runtime und Policy vor Seiteneffekten sichern

Behebe den reproduzierten Kontextverlust: registrierte Core-Handler müssen den konfigurierten Workspace und die Run-Runtime erhalten. `bridge._call` darf dafür nicht still Prozess-cwd und globale Default-Runtime verwenden. Kontext explizit und pro Run binden; kein pro Aufruf wechselndes globales `os.chdir` oder globale Env-Mutation.

Verifiziere mit zwei gleichzeitig laufenden temporären Workspaces, unterschiedlichen Dateiinhalten und unterschiedlichen Runtimes. Reads, Writes, Shell, Artefakte, Evidence und Approvals müssen beim richtigen Run bleiben.

Verbinde Code/Shell und verschachtelte Code-Toolaufrufe mit der vorhandenen Approval-Engine, Abbruch und Budget. Direkte Python-Operationen können Wirkungen außerhalb von `skaldir.call` haben: niemals behaupten, jede Codewirkung sei deshalb automatisch einzeln genehmigt. Definiere explizit Hostausführung versus erforderliche Isolation. Bei vorgeschriebener Isolation und fehlendem Backend verweigern, nicht still schwächer ausführen. Für absichtlich autorisierte normale Host-Projektarbeit keine unnötige neue Approval-Schleife einführen.

### Paket 3 — Brauchbarer Terminal-/Prozessvertrag

Behalte schnelle synchrone Kommandos. Ergänze über vorhandene Prozessinfrastruktur einen betreuten Langlaufpfad: stabile Prozess-ID, inkrementelle stdout/stderr-Ausgabe, Poll, Timeout, Cancel, eigenes Prozessgruppen-Cleanup und bei erforderlichen interaktiven Programmen PTY/stdin. Ein Devserver darf die Agentenrunde nicht bis zum Timeout blockieren; der Agent muss ihn starten, auf Bereitschaft prüfen, nutzen und seine eigene Instanz beenden können.

Ausgabe während der Erfassung begrenzen/spillen; `communicate` plus nachträgliche Kürzung ist kein RAM-Limit. Prozesszustand von Taskerfolg unterscheiden. Bestehendes `terminal.ok=true` bei Command-Exit ungleich null ist der dokumentierte Toolvertrag: Command-Exit im Agenten-/Verifikationspfad auswerten, nicht unüberlegt den gesamten Vertrag brechen.

Projekttests und Builds anhand AGENTS/Metadaten/üblicher Projektbefehle bestimmen. Ersetze die alleinige feste pytest-Auswahl im Goal durch passende Ausführung einschließlich Node-Projekten. Keine gefundenen Tests, fehlender Runner, Timeout und fehlgeschlagener Test bleiben unterschiedliche Resultate.

### Paket 4 — Geschwindigkeit und tatsächliche Agent-Integration

Nutze verifizierte Core-Effekt-/Ressourcenmetadaten statt alle Registry-Tools pauschal mutating und nicht parallel zu markieren. Unabhängige Reads/Suchen parallel, konkurrierende Writes und gemeinsame Browser-/Hostressourcen korrekt serialisiert. Fremde Tools bleiben ohne belastbaren Vertrag konservativ.

Binde den vorhandenen BrowserLayer mit typisierten Tools an denselben Agenten-Dispatcher. Bestehende agent-browser-Empfehlung, Sitzungswiederverwendung und Zen-Grenzen gelten. Ein CLI-Command oder separat konstruierter Backend-Test ersetzt diese Anbindung nicht.

Injiziere Run-Provider und Budget in `skill_expand_build` → ToolBuilder. Ein Build aus Bedarf ohne vorgegebenen Quelltext muss den konfigurierten Client tatsächlich erreichen. Prüfe danach Validieren, Registrieren, Agent-Aufruf und Wiederverwendung als zusammenhängenden Lauf.

### Paket 5 — Fertigbehauptungen der neueren Features nachprüfen

Prüfe den neuen Scheduler bis zum tatsächlichen zeitgesteuerten Workflowstart. ScheduleStore, claim/due und CLI-Verwaltung sind noch kein laufender Schedulerdienst. Verbinde vorhandenen Supervisor mit einem fällige Aufgaben ausführenden Worker, sofern dieser noch fehlt. Teste bei geschlossener TUI und nach Neustart mit isolierten kurzen Testterminen. Keine produktiven Zeitpläne ohne Auftrag aktivieren.

Prüfe den echten `skaldir`-Terminalstart: der neue TUI-Feed muss im laufenden interaktiven Event-/Renderloop arbeiten, nicht nur eine Transcript-Preview erzeugen. Chat links, Tools oben rechts, Goal unten rechts, wirksamer Tab-Moduswechsel, Resultatdetails, Resize, Scrollback und Reattach. Keine zweite Ausführungsengine in der Oberfläche.

Neu dokumentierten Wayland-libinput-Stopnachweis anerkennen; vollständige aktuelle Produktionsanbindung überprüfen. X11 bleibt separat abzunehmen. Nicht erneut pauschal einen längst überholten Gruppenberechtigungsblocker melden.

### Verbindliche neue Abnahmeszenarien

A. Frische isolierte Runtime, normaler Prompt: „Lies die CSV, berechne Kennzahlen per Python und speichere result.json.“ Tatsächlicher Code-Toolcall, richtige Daten/Artefakte und echte Fehlerbehandlung; keine von Hand vorab erzeugte Ergebnisdatei.

B. Derselbe Auftrag im Goal-Run. Der tatsächliche Modellrequest trägt passende Werkzeuge; kein Ausweichen auf Code schreibende Subagents oder GUI-Shell-Umwege.

C. Zwei parallele Workspaces mit gleichnamigen Dateien: jede Aktion liest/schreibt im eigenen Workspace und protokolliert in der eigenen Runtime. Fremdes Prozess-cwd bleibt ohne Einfluss.

D. Kleines Python- und Node-Projekt: Bug reproduzieren, gezielt patchen, jeweiligen echten Test-/Buildbefehl ausführen und Ergebnis auswerten. Exit 7, keine Tests und Timeout dürfen nicht als bestanden erscheinen.

E. Lokalen Testserver starten, frühe Ausgabe streamen, Bereitschaft prüfen, per HTTP/Browser kontrollieren und eigenen Prozess beenden. Abbruch/Timeout räumt nur eigene Prozesse auf.

F. Drei unabhängige Lese-/Suchcalls über den echten Agent-Dispatcher parallel; Abhängigkeiten und konkurrierende Writes bleiben korrekt geordnet. Zeiten und Zuordnung belegen.

G. Einfaches neues Tool allein aus einem Bedarf bauen lassen, testen, registrieren, im Agenten aufrufen und beim zweiten Bedarf wiederverwenden. Kein vorab erzeugter Toolordner als Beweis dieses Ablaufs.

H. Isolierten Workflow zeitgesteuert bei geschlossener TUI ausführen, nach Restart keine ungewollte Doppelwirkung. Falls der Ausführungsdienst fehlt, implementieren.

I. Tatsächlicher Bare-Start in PTY mit Toollauf: bestätigtes Vollbildlayout, Live-Events, Details, Resize und wirksamer Approval-Wechsel. Preview-Screenshot allein reicht nicht.

Für reproduzierbare Dispatcherprüfungen darf ein deterministischer Testprovider Toolcalls liefern; als solchen kennzeichnen. Mindestens ein echter konfigurierter Modelllauf belegt zusätzlich natürliche Auswahl und Nutzung, sofern Providerzugang besteht. Kein externer Modellzugang blockiert die unabhängigen lokalen Implementierungen. Keine Host-/Kontoeffekte für diese lokalen Tests nötig.

### Abschluss und Dokumentation

Der Ausgangsaudit hat 99 gezielte Tests erfolgreich ausgeführt und dennoch Integrationsfehler reproduziert. Füge Tests hinzu, die genau diese Aufrufpfade treffen; nicht nur weitere Engine-Tests. Passende Regressionen pro Paket, erforderliche Projektchecks vor Abschluss, keine identisch wiederholten Fullsuite-Schleifen.

Aktualisiere eine eindeutige aktuelle Statusübersicht, HANDOFF und VALIDATION mit Revision, tatsächlichem Einstieg, Testkommando, Ergebnis und verbleibender Lücke. Bewahre historische Belege, kennzeichne sie als historisch. Abnahme-IDs bleiben unverändert: BR-08 ist Notstopp, kein Installations-/Lizenztest. Unterschiedliche Tests nicht unter derselben ID als Erfüllung melden.

Nicht mit „alle lokal implementierbaren Lücken geschlossen“ enden, solange Kernwerkzeuge im normalen Chat/Goal fehlen, ein falscher Workspace verwendet wird oder die genannten Produktpfade nur Komponentenproben sind. Arbeite weiter, bis die lokal ausführbaren Kriterien bestanden sind. Externe Blockaden konkret und begrenzt melden; Gesamtgoal nicht als erfüllt ausgeben, solange verpflichtende Nachweise offen sind.
