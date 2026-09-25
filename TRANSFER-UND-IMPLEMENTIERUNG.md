# Bereinigter Projekttransfer und anschließende vollständige Umsetzung

Stand: 25. September 2026. Auftrag für den ausführenden Coding-Agenten.

## 1. Gewünschtes Ergebnis

Das vorhandene Skaldir wird bereinigt nach `/home/willy/Agentic_Projects/Skaldir_Agent` übernommen und dort anhand des aktuellen Masterplans fertiggestellt. Ein neues privates GitHub-Repository wird angelegt. Die Arbeit ist nachvollziehbar protokolliert. Aktiver Quellcode, notwendige Tests und funktionierende Architektur bleiben erhalten; alte Entwicklungsreste werden nicht blind kopiert.

Der Nutzer hat den Transfer, die Erstellung des privaten Repositories und die anschließende Implementierung beauftragt. Dieser Auftrag ist keine Aufforderung, den alten Quellordner zu löschen oder alle Dateien aus ihm zu veröffentlichen.

## 2. Zuerst lesen

1. `AGENTS.md` im Ziel.
2. `docs/masterplan/00-START-HIER.md` und `01-MASTERPLAN.md`.
3. `docs/masterplan/02-ABNAHME.md` und `03-TUI-ENTSCHEIDUNGEN.md`.
4. `docs/masterplan/04-AUDIT-UND-UEBERGABE.md` und `05-ANFORDERUNGSMATRIX.md`.
5. Historische Pläne/Audit unter `docs/reference/` nur bei Bedarf.

Der Masterplan ist bereits vorhanden. Nicht erneut nur einen Plan produzieren: vergleichen, übernehmen, implementieren, testen und dokumentieren.

## 3. Herkunft und Git-Inventur

Quelle: `/home/willy/projects/gods-voice`. Ziel: `/home/willy/Agentic_Projects/Skaldir_Agent`.

- Aktuellen Branch/HEAD, tracked/untracked/ignored Dateien und Dirty-Tree-Zustand der Quelle erfassen, ohne Inhalte von Secrets auszugeben.
- Relevante Projektregeln lesen, aktive Einstiegspunkte und vorhandene Packaging-/Testbefehle feststellen.
- Quell-HEAD allein reicht nicht: übernommene Dateiinhalte mit SHA-256 und Herkunftspfad im Manifest erfassen.
- Quelle kann parallel bearbeitet werden. Konsistenten Kopierstand erstellen und Änderungen während des Kopierens erkennen; keine gemischte Version still akzeptieren.
- Bereits vorhandene Zieldateien zuerst lesen. Planpaket und neue AGENTS.md nicht mit alten Varianten überschreiben.
- Kein Git-Reset, Checkout oder Cleanup im Quellprojekt. Die alten uncommitted Änderungen gehören weiterhin dem Nutzer.

## 4. Auswahl statt Altlasten kopieren

### Normalerweise übernehmen, nach Abhängigkeitsprüfung

Aktiver Quellcode, erforderliche Build-/Paketdateien und Lockfiles, relevante Tests/Fixtures, aktive Prompts/Skills, notwendige Ressourcen/Assets, Docker-/Tool-Definitionen, produktive Skripte, CI-Konfiguration, Lizenzen und Attribution. Nicht allein anhand des Verzeichnisnamens bestimmen, ob etwas Altlast ist.

### Normalerweise nicht übernehmen

- `.git`, alte Remotes, `.venv`, `node_modules`, Python-/Test-/Build-Caches.
- Lokale `.env`/Credentials, Tokens, Cookies, Browserprofile, persönliche SSH-Konfiguration.
- Aktive Runtime-Datenbanken, private Memory-Inhalte, Maildaten, Runlogs, Checkpoints und private generierte Reports.
- Große generierte Testoutputs, Aufzeichnungen, Screenshots, alte Releasepakete und temporäre Diagnoseskripte ohne aktive Abhängigkeit.
- Verwaiste Experimente, nicht mehr genutzte Overlays/Frontends oder Duplikate, wenn fehlende Nutzung tatsächlich nachgewiesen ist.

Nicht übernommene Runtime-Daten werden **nicht gelöscht**. Wenn bestehendes Wissen später migriert werden soll, separat, versioniert und außerhalb des Git-Repositories vorgehen. Neues Entwicklungssetup zunächst mit isoliertem Runtime-Verzeichnis testen.

### Manifest

`docs/audit/MIGRATION-MANIFEST.md`: Pfad, übernehmen/ausschließen/zurückstellen, Begründung, Abhängigkeiten, Quellhash, Zielhash und ggf. Ersatz. Eine konkrete Ausschlussentscheidung für UI-/Overlay-/Tooldateien erst nach Prüfung ihrer Aufrufer treffen. Alte kaputte Tests nicht als „Müll“ wegwerfen, um grün zu werden.

## 5. Neues lokales Setup

- Neue virtuelle Umgebung im Ziel erstellen, vorhandenen Paketmanager/Lockfile respektieren und Installation aus dem Ziel prüfen.
- Keine absolute Referenz auf die alte venv oder alte source checkout in Wrappern, CI oder Laufzeit benötigen.
- Umbenennung auf `Skylia-Agent` betrifft vorerst nur GitHub. Importpaket, Distribution und `skaldir`-Command bleiben kompatibel, sofern keine begründete Migration erforderlich ist.
- Durch `--version`, `--help`, Importpfad und passenden Smoke-Test nachweisen, dass die neue Umgebung den Zielcode benutzt.
- Datenmigration und Installation getrennt halten. Keine alte Live-Session mit neuen Schemas überraschend bearbeiten.
- Bestehenden globalen Launcher erst nach funktionierendem Zielsetup kontrolliert umstellen, mit Backup und dokumentiertem Rückweg. Nicht mehrere Shellprofile ungeprüft ändern.
- Danach `skaldir` im Terminal als Einstieg zur neuen TUI verifizieren, sobald deren Implementierung bereit ist. Bei nichtinteraktiver Nutzung definierte CLI-Ausgabe beibehalten.

## 6. Privates GitHub-Repository

Gewünschter Titel laut Nutzer: „Skylia Agent“. Für den Repository-Slug **`Skylia-Agent`** verwenden. Lokaler Ordner bleibt wie angegeben. Produktbefehl bleibt `skaldir`. Diese drei Namen nicht durch Massenersetzung angleichen.

1. Mit installierter `gh`-CLI Authentifizierung und ausgewählten Account prüfen; keine Zugangsdaten ausgeben.
2. Im persönlichen Namespace des aktiven Accounts ein neues privates Repo planen. Bei mehreren uneindeutigen Accounts oder gewünschter Organisation gezielt klären.
3. Prüfen, ob `<owner>/Skylia-Agent` schon existiert. Bestehendes Repo nicht überschreiben, nicht dessen Sichtbarkeit ändern oder ungeprüft als Origin verwenden. Bei Kollision gezielt nach alternativem Namen fragen; unabhängige lokale Arbeit fortsetzen.
4. Neues lokales Git im Ziel initialisieren, üblicherweise Branch `main`; bereits vorhandenes `.git` zuerst prüfen statt neu konfigurieren.
5. `.gitignore` und aufzunehmende Dateien prüfen. Secret-/Privatdatenprüfung für den **tatsächlich zu committenden** Bestand durchführen; nur „privat“ verhindert keinen versehentlichen Secret-Upload.
6. Neue Repo-Erstellung beispielsweise über `gh repo create <owner>/Skylia-Agent --private --source . --remote origin`; genaue Flags an tatsächlichen Zustand anpassen. Kein blindes `git add .` vor Bestandsprüfung.
7. Initialen nachvollziehbaren Commit aus bereinigter Quelle, Regeln und Plänen erstellen und initial pushen. Dieser erste Transfer-Commit/Push ist Bestandteil des Nutzerauftrags.
8. Origin, tatsächlichen Owner und PRIVATE-Sichtbarkeit nach Erstellung prüfen und im Handoff verlinken. Bei Netzwerk-/Authfehlern Status ehrlich melden, keine öffentliche Ersatzablage.

Nicht „neues Repo angelegt“ melden, bevor GitHub es bestätigt hat. Keine ungefragte Force-Push-History, keine fremden Repos ändern. Historie der Quelle bleibt dort erhalten; Manifest stellt nachvollziehbare Herkunft für den neuen Start her.

## 7. Logging und Fortschritt

- `logs/` für lokale vollständige redigierte Befehls-/Testlogs; Git-ignored.
- `docs/audit/WORKLOG.md` für Zeit, Absicht, geänderte Bereiche, tatsächliche Ergebnisse und nächste Schritte.
- `docs/audit/IMPLEMENTATION-AUDIT.md` für Anforderungsabgleich.
- `docs/audit/VALIDATION.md` für Tests mit Codezustand, Umgebung und Ergebnis.
- `docs/audit/DECISIONS.md` für Architektur-/Transferentscheidungen.
- `HANDOFF.md` für jeweils aktuellen Einstieg der nächsten Sitzung.

„Alles loggen“ bedeutet nachvollziehbare Aktionen und Ergebnisse, nicht Secrets oder sämtliche privaten Inhalte veröffentlichen. Lange Originalausgaben als geschützte lokale Referenzen, kurze überprüfbare Zusammenfassungen versionieren. Keine erfundenen Zeit-/Testergebnisse.

## 8. Umsetzung im Ziel

Nach Transfer-Baseline die Etappen M0–M7 des Masterplans ausführen. Standard-Agent-Funktionen, direkte Toolausführung und echte Ergebnisse zuerst. Dann globale Notstopp-/Desktoparchitektur früh beweisen; darauf Computer Use, schnelle Subtools, Builder, COMPOSE, Jev/Voice, Memory und TUI integrieren.

Verbindliche UI-Antworten:

- `skaldir` öffnet die Terminal-Vollbild-TUI.
- Großer Chat links, **Tools oben rechts, Goal unten rechts**.
- Jeder Call mit Ergebnisvorschau, vollständige Details aufklappbar.
- Tab im leeren Prompt zum Approval-Wechsel; bei aktiver Completion für Vorschläge.
- Dezente animierte Figur, gelegentlicher Humor, überwiegend monochrom, Fehler rot.
- Desktopkontrolle durch kleine Statusleiste in einer Ecke erkennbar.

Kein Funktionsneubau allein wegen neuem Ordner. Erst unterscheiden: funktionierend / nur vorhanden / fehlerhaft / unverbunden / wirklich fehlend. Alle wesentlichen Nutzerabläufe promptbasiert erreichbar machen.

## 9. Tests und Abschluss

Vor umfangreichen Änderungen eine passende Ziel-Baseline etablieren. Nach einzelnen Fixes gezielt testen; keine Fullsuite nach jedem kleinen Patch. Projektseitige verbindliche Checks vor Abschluss durchführen. Vollständige Logs, sichtbarer Fortschritt, echter Exitcode; keine aneinandergereihten identischen flock-Läufe.

Die Abnahme umfasst normale Coding-/Webfunktionen, echte Browserrecherche, Docker-/allgemeinen Toolbau, Stock-Dashboard, Video, Blender und Kreativarbeit im im Masterplan beschriebenen Umfang. Fehlende konkrete Testanwendungen/Assets gezielt klären, während unabhängige Arbeit weiterläuft.

Am Ende liefern:

- Welche Dateien übernommen/ausgelassen wurden und warum.
- Privates GitHub-Repo, geprüfte Visibility und Initialcommit.
- Aktiver Ziel-Import-/Entrypoint und Installationshinweise.
- Implementierungsstatus je Anforderung, passende echte Tests und Live-Nachweise.
- Offene externe Blocker, bekannte Grenzen, Recovery-/Rollbackweg.
- Aktualisierte AGENTS.md nur soweit echte Projektbefehle/Verträge präzisiert werden, plus HANDOFF.md.

Nicht behaupten, Skaldir sei fertig, nur weil Transfer oder Repo-Erstellung erfolgreich war.
