# Hermes: nächster /goal-Auftrag für Skaldir

Diesen Auftrag als Goal-Text in Hermes verwenden. Die genaue Hermes-/goal-Eingabeform aus dessen aktiver Hilfe übernehmen. Nicht mit Skaldirs eigener Slash-Palette verwechseln: Dort zeigt `/goal` im derzeit gelesenen Code einen Goal-Datensatz an; der ausführende Pfad wird separat über `/run` beziehungsweise `/locking` angeboten.

## Goal-Text zum Kopieren

Vervollständige Skaldir in `/home/willy/Agentic_Projects/Skaldir_Agent` auf der bestehenden Architektur gemäß meinen bestätigten Plänen. Dein Hauptauftrag ist jetzt die Integration in den echten Agentenlauf: funktionierendes Computer Use, zuverlässige Browsersteuerung, wirksame Approvals/Notstopp, die echte Vollbild-TUI, schnelle Toolausführung und wiederverwendbare Tools/Workflows. Klassen, Dokumentation, registrierte Commands und grüne Unit-Tests allein erfüllen das Goal nicht.

### 1. Einstieg und Arbeitsgrenzen

Lies zuerst:

- `AGENTS.md` und `docs/masterplan/00-START-HIER.md`.
- `docs/audit/GOAL-REVIEW-2026-09-25.md`.
- `docs/audit/IMPLEMENTATION-AUDIT.md`, einschließlich Detailanhängen UND späteren Korrekturen.
- `docs/audit/VALIDATION.md`, `HANDOFF.md` und relevante aktuelle WORKLOG-Einträge.
- `docs/masterplan/01-MASTERPLAN.md`, `02-ABNAHME.md`, `03-TUI-ENTSCHEIDUNGEN.md` und `07-BROWSER-UPGRADE.md`.

Prüfe HEAD, Arbeitsbaum und laufende Arbeiten zuerst. Der zuletzt geprüfte Stand war `89b02a7` nach `2c358c6`, mit uncommitteten Desktop-/Indikatoränderungen und neuen Dateien. Das ist eine Momentaufnahme. Bewahre diese Arbeit, untersuche ihre tatsächliche Verdrahtung und ergänze sie. Keine Resets, kein blindes Überschreiben, keine zweite Implementierung desselben Mechanismus. Migration und privates GitHub-Repo existieren bereits; nicht nochmals migrieren oder ein Repository anlegen. Altes Quellprojekt und fremde Hermes-Runtime unangetastet lassen. Commit-/Push-Regeln aus AGENTS beachten; dieser Auftrag ist keine zusätzliche pauschale Veröffentlichungsfreigabe.

Alle Codearbeit durch dich als Hauptagenten. Arbeite in überprüfbaren Paketen weiter, ohne nach jedem Paket zu fragen, welchen Weg du nehmen sollst. Halte das primäre Goal, Nichtziele, Kriterien, aktuelle Etappe und konkrete Blocker dauerhaft fest.

### 2. Priorität P0: Desktopkontrolle tatsächlich durchsetzen

Verbinde DesktopLeaseStore mit den realen Host-Aktionspfaden aus Agent, Goal-Run, Subtool und Workflow. Prüfe konkrete Caller bis zur Eingabe. Kein optional ungebundener Hostpfad darf die zugesagte Kontrolle umgehen. Beobachtung und isolierte Containersteuerung brauchen nicht unnötig dieselbe Host-Lease.

Prüfe besonders die lokale Arbeit in act_service.py: Lease-Parameter, `_bind_desktop_fence`, `on_runner` und Aufruf von `_execute_action_list` müssen zusammenhängen. Fence-Bindefehler für Hosteingaben müssen die Aktion verhindern. Ein Test, der den Helper direkt aufruft, beweist diese Verdrahtung nicht.

Sichere Besitz atomar über Prozesse und Store-Instanzen hinweg. Prüfe die bisherige RLock-/Dateiimplementierung gegen gleichzeitige Acquires, veraltete Generationen, beschädigten State und Crash. Verwende einen vorhandenen transaktionalen Store oder eine korrekt implementierte Prozesssperre; ein Docstring über fcntl reicht nicht. Queue, verschachtelte Subtools, Freigabe und Recovery müssen denselben Besitzer respektieren. Keine Freigabe durch einen veralteten Teardown nach einem Besitzerwechsel.

Binde den neuen Hostindikator an diesen Lifecycle. Er muss während tatsächlicher Desktopkontrolle sichtbar bleiben, Task und Stop-Hinweis anzeigen, Zustandswechsel übernehmen und nach Freigabe verschwinden. CLI-Demo und TUI-Text sind keine Hostabnahme. Prüfe Fokusverlust, Skalierung und tatsächliche Sichtbarkeit unter Fedora; XWayland-Overlay nicht als native Wayland-Steuerung ausgeben.

### 3. Priorität P0: globaler Notstopp und persistente Pause

Implementiere die vollständige Kette: zulässige physische Tastaturquelle → Doppel-Escape-Detektor → Input-Dispatcher/Queue → Fence-Entwertung → sicherer Abbruch weiterer Eingaben → dauerhaft pausierter Run → sichtbarer Status. Agent-generierte Escapes und Autorepeat dürfen keinen Nutzerstopp imitieren. Gehaltene Tasten/Dragzustände kontrolliert lösen. Pause darf durch Voice, Scheduler oder Neustart nicht eigenständig aufgehoben werden.

Der Gruppenwechsel `sudo usermod -aG input willy` mit anschließendem Neu-Login ist höchstens ein möglicher externer Freigabeschritt für ein gewähltes Backend. Prüfe erst die tatsächlich nötige Zugriffsart. Führe Systemberechtigungsänderungen nicht eigenmächtig aus. Implementiere und teste Listener-/Dispatcher-Verträge und die übrige Integration unabhängig davon. Melde eine nötige Nutzeraktion einmal klar; behandle sie nicht als Blockade des ganzen Projekts.

Die Host-Freigabe muss einen aktuellen Nachweis des Stop-Pfads konsumieren und bei Backend-/Sitzungswechsel erneut prüfen. Fehlender Nachweis bleibt unbekannt/blockiert. Ein bloß erzeugter Report reicht nicht. Keine unbeaufsichtigten Host-Eingaben bei fehlendem erforderlichem Stop-Pfad; isolierte Tests, Browser-Adapterentwicklung und übrige Implementierung weiterführen. X11 und Wayland separat abnehmen; XWayland ist kein Ersatz für beide Nachweise.

### 4. Priorität P0/P1: Approvals und echte Terminal-TUI

Verbinde `approval_modes.policy_for` mit der vorhandenen Approval-Engine und dem tatsächlichen Tool-Dispatch, einschließlich Batches, GUI-/Docker-Subtools und Workflows. Ask Everything, Approve for me und Approve Everything müssen wirksam verschieden sein. Geld und irreversible Schäden erfordern weiterhin konkrete Zustimmung. Reversible Projektarbeit darf innerhalb des gewählten Modus flüssig laufen; Git allein ist kein Nachweis der Wiederherstellbarkeit beliebiger Dateien.

Binde Freigaben an Wirkung, Ziel, Parameter und relevante Tool-/Workflowversion. Neuprüfung wartender Aktionen bei geänderter Freigabelage. Ein Modiwechsel ist keine pauschale rückwirkende Einzelfreigabe. Default für neue Runs ausdrücklich und nachvollziehbar ändern/persistieren; keine stille Eskalation bestehender Runs. APR-05 nicht allein durch korrigierte Kommentare abschließen.

`skaldir` ohne Unterbefehl muss im unterstützten interaktiven Terminal die bestätigte Vollbild-TUI öffnen: großer Chat links, Tools oben rechts, Goal unten rechts. Vorhandenen Renderer an echte Run-/Tool-Events anschließen. Vorschau und aufklappbare Details, funktionierender Live-Cursor/Reattach, Resize und Scrollback, dezente Figur und rote textlich erkennbare Fehler. Tab im leeren Eingabefeld ohne Completion-Menü wechselt den wirksamen Approval-Modus; aktive Completion behält Tab. Keine zweite Runtime im UI. Nichtinteraktive CLI/JSON-Ausgabe erhalten.

Terminal schließen bedeutet Detach, ausdrücklicher Stop bleibt persistent. PTY-Integration mit realem Einstieg und Testprovider prüfen, danach sichtbaren Produktlauf. Preview-/Renderertests allein reichen nicht.

### 5. Priorität P1: Browser-Upgrade und schneller Agentenlauf

Willy und Codex als technischer Architekturberater empfehlen `agent-browser` als lokal mitinstalliertes, gepinntes Werkzeug für unterstützte Browser plus vorhandenes ACT/Computer Use für Zen und native Dialoge. Setze `07-BROWSER-UPGRADE.md` um; begründe Abweichungen anhand konkreter Befunde. Keine ungeprüfte Zen-Kompatibilität behaupten und „mein Zen“ nicht still durch einen isolierten Chromium ersetzen.

Zuerst zentralen Browserlebenszyklus schließen: erkennen, wiederverwenden, verbinden, Bereitschaft prüfen. Fehlende Beobachtung bedeutet nicht fehlender Browser. Kein erneuter Start pro Modellrunde und keine Flut zusätzlicher leerer Tabs. Vorhandene Nutzerprofile und fremde Tabs erhalten.

Toolprozesse/Sitzungen wiederverwenden; strukturierte kurze Ergebnisse, selektive Seiteninhalte und Vision bei tatsächlichem Bedarf. Unabhängige Tools parallel; abhängige Schritte und konkurrierende Tab-/Hostaktionen koordinieren. Batches mit Einzelergebnissen, Approval- und Stop-Punkten. Veraltete Zielreferenzen erneuern; ungewisse folgenschwere Aktionen nicht blind wiederholen. Start, Fortschritt und Resultate live im TUI anzeigen.

Abnahmen BR-01 bis BR-15 aus dem Browserplan der bestehenden Matrix hinzufügen. Baseline und Ergebnis mit identischen Aufgaben vergleichen: kalte/warme Tool-Latenz, Taskzeit, Modellaufrufe, Tokens, Erfolgsquote und überflüssige Aktionen. Keine Geschwindigkeit durch ausgelassene Prüfung gewinnen.

### 6. Priorität P1/P2: Tool Builder, Subtools, Workflows und Scheduler

Beweise den normalen Prompt-Pfad bis zur Toolnutzung: Bedarf → vorhandenes Tool finden oder bauen → validieren/testen → registrieren → im Agenten aufrufen → später wiederverwenden. Allgemeiner und Docker-Builder müssen den konfigurierten Modellclient wirklich erhalten. Ein geschriebener Tool-Quelltext ist kein fertiges Tool.

Gelernte Browser-/GUI-Subtools parametrisieren, unnötige Wiederholungen entfernen, notwendige Wartebedingungen behalten und automatisch wiederverwenden. GUI, MCP, Skills, Docker-Subtools und normale Tools gemeinsam komponieren. Natursprachlicher Workflowstart, Parameterergänzung und gezielte Klärung bei Mehrdeutigkeit müssen funktionieren. Fehler-/Replan-Grenzen explizit halten.

Danach den tatsächlich fehlenden Scheduler als eigene Etappe implementieren: persistente Definition und Aktivierung getrennt; Zeitzone einschließlich DST, Workflowversion, Parameter und Policy; atomare Ausführungsidentität, Doppelzustellung, Überlappung, verpasste Termine und Crash-Recovery. TUI-unabhängiger Lauf über vorhandene Runtime. Desktop-/Approval-Wartezustände sichtbar. Geplante Ausführung erteilt keine Geldfreigabe. Testpläne in isolierter Runtime prüfen; keine unbestellten produktiven Zeitpläne aktivieren.

### 7. Restliche Kernfunktionen, Jev/Voice und Memory

Schließe anschließend die übrigen realen Lücken der 134 Masterplan-IDs, statt bereits bestandene Grundlagen neu zu bauen. Normale Datei-/Code-/Shell-/Suchfunktionen bleiben Pflicht. Kostenlos nutzbarer Suchpfad ohne obligatorischen bezahlten Suchschlüssel; Quellen und Datenlücken ehrlich ausgeben.

Jev als schnelle Auswahl-/Relevanzebene in echten Aufrufen prüfen; Voice nur bei begründetem Eingriff, nicht als zusätzliche Modellrunde nach jedem Tool. VERIFY bleibt unabhängig. Memory gezielt nach Dokument/Abschnitt mit Originalreferenzen lesen. Kompaktierung erhält Primärgoal, offene Kriterien, Nutzerkorrekturen, ungeklärte Wirkungen und wichtige Belege. Große Historien archivieren und selektiv abrufbar machen. Live-Provider-Erfolg getrennt von Stubtests ausweisen.

### 8. Produktabnahmen und offene Nutzervorgaben

Browserrecherche B zuerst. Für C–F vorhandene Programme und Testmöglichkeiten ermitteln; fehlende Auswahl einmal gebündelt klären. Folgende konkrete Vorschläge vorlegen, nicht als bereits bestätigt ausgeben:

- C: AAPL/MSFT/NVDA, zwölf abgeschlossene Monate; kostenlos zugängliche reale Quellen plus eindeutig markierte Fixtures für reproduzierbare Rechen-/Fehlerprüfungen; interaktives localhost-Dashboard, keine Trades.
- D: vorhandenen Videoeditor bevorzugen; drei selbst erzeugte Clips, Trim, Titel, Ton, Export und Projekt erneut öffnen.
- E: Blender, stilisierte Tischlampe mit prüfbaren Einzelteilen, Materialien, Licht/Kamera, gespeicherter Szene und Render.
- F: vorhandenes Vektor-/Bildprogramm; Skaldir-Poster mit Text, Formen/Ebenen, nativer Datei und Export.

Vorbereitende Fixtures, Testadapter und Skripte darfst du unabhängig erstellen. Fehlende Appwahl oder Hostberechtigung blockiert nur die betroffene Live-Abnahme. Keine kostenpflichtigen Assets oder Quellen beschaffen, keine echten Käufe/Nachrichten zum Testen duplizieren. Jede Abnahme prüft das tatsächliche Ergebnis inklusive Fehlerfällen und Wiederöffnen/Neustart, nicht bloß eine erfolgreiche Toolantwort.

### 9. Verifikation, Dokumentation und Abschluss

Führe pro Änderung aussagekräftige gezielte Regressionen aus, nach zusammenhängenden Änderungen Integration und vor Abschluss die vorgeschriebenen Projektchecks. Testumgebung isolieren; Secrets/Live-Runtime nicht versehentlich verwenden. Exitcodes vollständig erhalten, Rohlogs lokal redigiert ablegen. Identische Fullsuite-Läufe nicht mehrfach starten. Historische 247 oder 7648 bestandene Tests sind keine Abnahme späterer Änderungen.

Führe eine eindeutige aktuelle Statusübersicht für alle 134 IDs plus BR-01…15. Trenne Erfüllungsgrad von Nachweisstufe: Komponententest bestanden kann mit Produktintegration offen zusammenfallen. Historische Anhänge bewahren, überholte Aussagen klar kennzeichnen. HANDOFF, IMPLEMENTATION-AUDIT und VALIDATION dürfen beim aktuellen Status nicht widersprechen.

Arbeite weiter, bis alle lokal umsetzbaren Anforderungen integriert und geprüft sind. Unabhängige Arbeit bei Blockaden fortsetzen; nicht bloß „Welchen Weg soll ich gehen?“ fragen. Bei tatsächlich nötigem Nutzerinput konkret benennen: fehlende Entscheidung/Berechtigung, betroffene Abnahme, erhaltene Arbeit und nächster Schritt.

Das Gesamtgoal nur als erfüllt melden, wenn die anwendbaren Abschlusskriterien tatsächlich belegt sind. Bei verbleibenden externen Live-Blockaden wahrheitsgemäß teilweise/blockiert übergeben, nicht durch Umbenennen zu optionalen Kriterien grün machen. Abschluss enthält Codeänderungen, tatsächlich ausgeführte Tests mit Revision, aktive Einstiegspfade, Messergebnisse, offene Blockaden, laufende Ressourcen und nächste Nutzeraktion.
