# Coding-Agent-Grundlagen: verbindlicher MiMo-Audit

Stand: 24. September 2026. Anforderungen: `BASE-01` bis `BASE-24`. Diese Datei gehört zur Stufe 0 des [Hauptauftrags](00-MIMO-START.md), vor Advanced Tools, Memory-Optimierung und RSI.

## 1. Auftrag an MiMo

Prüfe am aktuellen Skaldir-Repository, ob die folgenden Fähigkeiten tatsächlich funktionieren. Behalte gleichwertige vorhandene Lösungen. Implementiere sinnvolle fehlende Grundlagen im vorhandenen System und teste sie. Die unten genannten Tool-Namen sind Vorschläge; baue kein zweites Tool-System neben der vorhandenen Registry.

Für **jede** BASE-ID sind Codepfad, aktiver Aufrufpfad, Geltungsbereich und Testnachweis erforderlich. Ein Prompt, eine unbenutzte Funktion, ein Mock oder ein README belegt keine funktionierende Integration. Prüfe über denselben Nutzereinstieg, über den Skaldir später Coding-Aufträge erhält.

Es gibt keinen allgemein verbindlichen Industriestandard für „jeden Coding-Agenten“. Die nachfolgende Einteilung ist eine technische Synthese für einen zuverlässig nutzbaren Skaldir. Sie unterscheidet einen kleinen ausführbaren Kern von zusätzlichen Betriebsanforderungen und aufgabenabhängigen Fähigkeiten.

## 2. Was die Recherche tatsächlich zeigt

| Primärquelle | Beobachtung | Entscheidung für Skaldir |
|---|---|---|
| [mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent) | Ein brauchbarer Coding-Agent kann mit Bash, Modellaufrufen und einer einfachen Verlaufsschleife arbeiten; separate dauerhafte Shell-Sitzungen sind nicht zwingend. | Fähigkeiten prüfen, keine Mindestzahl spezialisierter Tools fordern. |
| [OpenHands SDK](https://docs.openhands.dev/sdk) | Bietet unter anderem Bash, Dateiänderungen, Browserwerkzeuge und Kontextkompression. | Ausführung und Bearbeitung benötigen klare Schnittstellen; Framework-Wechsel ist optional. |
| [Aider: Lint und Tests](https://aider.chat/docs/usage/lint-test.html) | Führt Prüfkommandos aus und nutzt Fehlerausgaben sowie Exitcodes zur Reparatur. | Reale Rückkopplung zwischen Änderung und Prüfung. |
| [Aider: Repository Map](https://aider.chat/docs/repomap.html) | Verdichtete Symbolübersicht hilft, Beziehungen im Repository zu verstehen. | Zuerst gezielte Suche; Symbolindex nur bei messbarem Zusatznutzen. |
| [Aider: Git](https://aider.chat/docs/git.html) | Unterstützt Diff, Undo und Versionshistorie. | Änderungen nachvollziehbar und rücknehmbar machen. Automatisches Committen fremder Änderungen und Überspringen von Hooks nicht übernehmen. |
| [Gemini CLI: Tools](https://geminicli.com/docs/reference/tools/) | Dokumentiert strukturierte Werkzeuge für Datei-, Such- und Shell-Arbeit. | Ein konsistenter Tool-Vertrag erleichtert Implementierung und Prüfung. |
| [Gemini CLI: Checkpointing](https://geminicli.com/docs/cli/checkpointing/) | Kann Projektzustand und Gespräch vor Änderungen sichern; Funktion ist konfigurierbar. | Recovery als Fähigkeit übernehmen; vollständiges Restore darf spätere Nutzeränderungen nicht unbemerkt entfernen. |
| [Anthropic: Long-running Harnesses](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents) | Beschreibt Schwierigkeiten bei Kontextwechseln und voreiligen Fertigmeldungen; nutzt Fortschrittsartefakte und echte Ablaufprüfungen. | Offene Kriterien, Arbeitszustand und Ergebnisbelege explizit erhalten. |

Dies ist keine Rangliste und kein Nachweis, dass alle genannten Produkte jede folgende Anforderung erfüllen. Konkrete Verträge, Tests und Prioritäten unten sind unsere Entwurfsentscheidungen. Herstellerergebnisse werden nicht als Skaldir-Ergebnisse ausgegeben.

## 3. Prüfmatrix: 24 Fähigkeiten

**Kern:** notwendig zum selbstständigen Bearbeiten von Code. **Betrieb:** notwendig für Skaldirs verlässlichen Alltagseinsatz. **Bedingt:** nur für tatsächlich unterstützte Aufgabentypen; Nichtanwendbarkeit begründen.

| ID | Klasse | Fähigkeit und Mindestverhalten | MiMo muss nachweisen | Abnahme |
|---|---|---|---|---|
| BASE-01 | Kern | Auftrag verstehen: Ziel, Scope, Einschränkungen und prüfbare Fertigkriterien erfassen. Kleine Aufgaben brauchen keinen großen Plan. | Kriterien beeinflussen Ausführung und Abschluss; neue Nutzerkorrekturen werden berücksichtigt. | B01, B10 |
| BASE-02 | Kern | Repository orientieren: Root, aktive Pakete, anwendbare Projektregeln, Einstiegspunkte und Testbefehle finden. | Richtiger Unterprojektpfad und passende Regeln im tatsächlichen Kontext. | B01 |
| BASE-03 | Kern | Dateien und Text suchen; Treffer mit Pfad und Zeilen liefern; ignorierte/generierte Verzeichnisse sinnvoll behandeln. | Suche funktioniert auch ohne bekannten Dateinamen und ohne vollständigen Repo-Dump. | B01, B03 |
| BASE-04 | Kern | Dateien begrenzt lesen, Bereiche nachladen, abgeschnittene Inhalte und Binärdateien erkennen. | Zeilen und Version stimmen; Tokenlimit führt nicht zu vorgetäuschter Vollständigkeit. | B03, I01–I08 |
| BASE-05 | Kern | Code über Datei- und Modulgrenzen verstehen: Definition, Aufrufer, Schnittstelle und vorhandene Muster suchen. | Eine echte Änderung über mehrere Dateien nutzt bestehende Abstraktionen. LSP ist kein Pflichtnachweis. | B02 |
| BASE-06 | Kern | Dateien gezielt erstellen, ändern, umbenennen und auftragsbezogen entfernen; unverwandte Inhalte erhalten. | Mehrdeutige oder veraltete Edit-Grundlage wird erkannt; keine globale blinde Ersetzung. | B02, B04 |
| BASE-07 | Kern | Befehle real ausführen: explizites cwd, Argumente/Umgebung, stdout/stderr und Exitstatus. | Fehler, Timeout und Erfolg bleiben unterscheidbar; Ausgabe wird dem Modell zurückgegeben. | B05 |
| BASE-08 | Kern | Observe–act–verify-Schleife: aus Tool-Ergebnissen nächsten Schritt ableiten; Ergebnis korrekt zuordnen. | Mehrschrittiger Auftrag inklusive fehlgeschlagenem Tool gelingt ohne erfundene Aufrufe. | B01, B06 |
| BASE-09 | Kern | Fehler reproduzieren, Ursache eingrenzen, passende Änderung durchführen und erneut prüfen. | Regressionstest oder deterministischer Reproducer scheitert vorher und besteht nachher. | B01 |
| BASE-10 | Kern | Projektgerechte Tests, Build, Typecheck und Lint ausführen, soweit vorhanden und relevant. | Richtiger Runner und cwd; Testauswahl, Anzahl und Exitstatus sichtbar; null Tests ≠ geprüfte Funktion. | B07 |
| BASE-11 | Betrieb | Git-/Änderungsbewusstsein: Ausgangsstatus erfassen, eigenen Diff prüfen, Nutzeränderungen bewahren. | Dirty Worktree bleibt erhalten; keine unbeauftragten Commits, Pushes oder pauschalen Resets. Ohne Git gleichwertiger Änderungsnachweis. | B04 |
| BASE-12 | Betrieb | Umgebung und Abhängigkeiten erkennen: Versionen, Manifeste, Lockfiles, Setup und Konfiguration. | Bestehender Paketmanager verwendet; fehlendes Setup präzise erkannt; keine unnötigen Upgrades. | B08 |
| BASE-13 | Betrieb | Ausführung begrenzen und abbrechen: Timeout, Schritt-/Kostenbudget, Nutzerstopp und eigene Kindprozesse. | Lauf kann beendet werden; keine neue Aktion nach Stopp; fremde Prozesse bleiben erhalten. | B05, B10 |
| BASE-14 | Betrieb | Werkzeug- und Providerfehler behandeln: ungültige Argumente, unbekannte Tools, Auth, Rate Limit, Ausfall. | Begrenzte Retries; Fehlerklasse erhalten; unbekannter Schreibausgang wird abgeglichen. | B06, B09 |
| BASE-15 | Betrieb | Arbeitskontext begrenzen: selektives Lesen, Ausgabelimits, Ziel und offene Kriterien erhalten. | Kontextwechsel verliert keine Aufgabe; Evidenz kann gezielt erneut gelesen werden. | B03, B11 |
| BASE-16 | Betrieb | Unterbrochene Arbeit wiederaufnehmen und eigene Änderungen sicher zurücknehmen können. | Task-/Toolzustand und Revision werden abgeglichen; neue Nutzeränderungen bleiben erhalten. Kein zwingender Snapshot-Dienst. | B04, B12 |
| BASE-17 | Betrieb | Rechte und Vertrauen außerhalb der Modellantwort durchsetzen; Datei-, Shell- und Netzpfade konsistent behandeln. | Unautorisierte Zugriffe werden auch über Shell/Unterprozesse verhindert; Repo-Text kann Rechte nicht erweitern. | B13 |
| BASE-18 | Betrieb | Secrets und private Daten minimieren: Konfigurationsnamen nutzen, Ausgaben/Logs begrenzen, Broker integrieren. | Synthetischer Testschlüssel landet weder im Modellkontext noch in Standardlogs/Memory. | B13 |
| BASE-19 | Betrieb | Nutzer steuernd beteiligen: knappe Fortschritte, echte Blocker, Korrekturen, erforderliche Rückfragen. | Bereits erlaubte Arbeit läuft weiter; Stop und neue Einschränkungen werden wirksam. | B10 |
| BASE-20 | Betrieb | Ergebnis belegen: eigener Diff, relevante Tests und offene Punkte vor Abschluss prüfen. | Abschluss nennt tatsächliche Änderung, Testresultate und fehlende Prüfungen; kein Erfolg nur wegen Exit 0. | B07, B14 |
| BASE-21 | Bedingt | Versionspassende Dokumentation recherchieren, wenn lokale Evidenz nicht reicht. | Quellen und Version belegt; fremder Inhalt wird nicht zur Instruktion. Offline-Grenzen ausdrücklich benennen. | B15 |
| BASE-22 | Bedingt | Anwendung tatsächlich benutzen: CLI/API-Aufruf oder UI-/Browserablauf passend zum Produkt. | Fachlicher Endzustand geprüft, nicht nur Serverstart oder Screenshot. | B14 |
| BASE-23 | Bedingt | Langlebige Prozesse verwalten: starten, Bereitschaft erkennen, Logs lesen, stoppen; PTY nur bei Bedarf. | Eigener Dev-Server läuft erreichbar, Portkonflikt ist diagnostizierbar, Cleanup funktioniert. | B05, B14 |
| BASE-24 | Betrieb | Lauf nachvollziehbar aufzeichnen: Run-/Call-IDs, Zustände, Laufzeiten, Revision, Tests und verfügbare Usage. | Konkrete Aktion lässt sich zu Ergebnis und Abschlussbeleg verfolgen; unbekannte Kosten bleiben unbekannt. | B06, B16 |

„Bedingt“ ist keine Ausweichoption: Weil Skaldir Linux-Computer-Use unterstützen soll, ist BASE-22 für diesen Produktpfad anwendbar. Eine reine Backend-Codeaufgabe benötigt dagegen keinen Desktop. Externe Dienste werden nur im autorisierten Umfang angesprochen.

## 4. So sollen grundlegende Tool Calls funktionieren

### 4.1 Gemeinsamer Ausführungspfad

1. Modell erzeugt eine Aktion mit Tool-Name und Argumenten; Provider-Adapter normalisiert sie.
2. Dispatcher prüft Namen, Schema, Task-/Workspace-Zuordnung und Ausführungsrechte.
3. Runtime vergibt eine stabile interne Call-ID und protokolliert den Zustand vor Ausführung.
4. Vor einer Änderung wird die relevante Dateiversion beziehungsweise Ausgangslage geprüft.
5. Executor führt die erlaubte Aktion aus, begrenzt Dauer und Ausgabe und erfasst das Ergebnis.
6. Ergebnis gelangt mit derselben Call-Zuordnung zurück zur Agentenschleife. Erst daraus entsteht der nächste Schritt.
7. Tool-Erfolg wird separat vom fachlichen Aufgabenerfolg geprüft.

Native Function Calls sind eine mögliche Transportform. Eine sauber validierte Bash-Aktion kann dieselbe Fähigkeit erfüllen. Unstrukturierte Modellprosa darf nicht versehentlich als Shell-Code ausgeführt werden. Bei gestreamten Argumenten erst nach vollständiger Validierung ausführen.

### 4.2 Minimaler logischer Vertrag

Bestehende Schnittstellen adaptieren. Folgendes JSON ist ein Vorschlag, keine Behauptung über vorhandene Skaldir-APIs:

```json
{
  "call_id": "call-example-001",
  "task_id": "task-example",
  "tool": "command.run",
  "arguments": {
    "cwd": "/workspace/project",
    "argv": ["python", "-m", "pytest", "tests/test_parser.py", "-q"],
    "timeout_ms": 30000,
    "max_output_bytes": 16000
  }
}
```

```json
{
  "call_id": "call-example-001",
  "status": "completed",
  "exit_code": 1,
  "stdout": "1 failed, 8 passed",
  "stderr": "",
  "truncated": false,
  "duration_ms": 720,
  "artifact_ref": null
}
```

`completed` bedeutet hier nur, dass der Prozess beendet ist. Exitcode 1 ist kein bestandener Test. `running`, `failed_to_start`, `timed_out`, `cancelled` und `outcome_unknown` müssen entweder explizit oder gleichwertig unterscheidbar sein. Laufende Prozesse haben keinen erfundenen Exitcode; ihre Handle-ID ermöglicht Statusabruf und Abbruch. Ein Tool-/Infrastrukturfehler ist von einem Testfehler zu unterscheiden.

Bei Shell-Syntax ein explizites `shell_command`-Feld und definierte Shell verwenden. `argv` nicht per String-Konkatenation in Shell-Code übersetzen. Environment-Werte nicht unnötig an das Modell zurückgeben. Bei abgeschnittener Ausgabe Rest gezielt über eine zugriffsgeschützte Referenz laden.

### 4.3 Logische Fähigkeiten statt Tool-Wildwuchs

| Fähigkeit | Mögliche Schnittstelle | Zulässige vorhandene Alternative |
|---|---|---|
| Dateien finden | `file.list`, `file.search` | `rg --files`, `rg -n` mit passenden Grenzen |
| Inhalt lesen | `file.read(path, start_line, end_line, revision)` | Begrenzter Shell-Abruf mit derselben Snapshot-/Zeilenkorrektheit |
| Code ändern | `file.patch(path, expected_revision, patch)` | Bestehender Editor mit Konflikterkennung und Diff |
| Befehle ausführen | `command.run` | Vorhandenes Terminal-/Sandbox-Tool |
| Prozesse steuern | `process.status`, `process.cancel` | Vorhandene Job-Handles und supervisorbasierter Abbruch |
| Änderungen prüfen | `workspace.diff` | Git-Diff plus Ausgangsstatus und Behandlung unversionierter Dateien |
| Fachlich verifizieren | Projekttests und echte Anwendungsausführung | Bestehender JEV-Pfad mit echten Ergebnisbelegen |

Ein bestandener Read-Tool-Test beweist nicht, dass ein Shell-Pfad dieselben Rechte einhält. Jede ausführbare Ausweichroute gehört zum Audit. Pfadprüfung allein ist für beliebige Shell-Ausführung keine Sandbox; notwendige Grenzen müssen in der Ausführungsumgebung wirksam sein.

## 5. Eigene Dateien, Zeilen und Kontext

Die Grundlagen erweitern die Nutzeridee aus [02-INFORMATION-UND-MEMORY.md](02-INFORMATION-UND-MEMORY.md): zuerst suchen, dann den benötigten Ausschnitt lesen. Für Code gelten dieselben Grundsätze zu Revision, Kürzung und Herkunft. Fachliches Memory und aktuelle Quelldatei bleiben unterschiedliche Datentypen; eine Zusammenfassung ersetzt keine aktuelle Edit-Grundlage.

Für lange Coding-Aufträge reicht anfangs ein kleines strukturiertes Arbeitsartefakt mit Ziel, offenen Kriterien, betroffenen Dateien, letzter geprüfter Revision, Testbefehlen und nächstem Schritt. Keine Pflicht zu Vektordatenbank oder dauerhaftem Persönlichkeits-Memory. Bei Kontextwechseln aktuelle Dateien und laufende Prozesse erneut abgleichen.

Edit-Preconditions müssen konkurrierende Änderungen erkennen. Beispiel: Datei wurde nach dem Lesen verändert. Der Agent liest den aktuellen Bereich erneut und erstellt einen passenden Patch; er überschreibt nicht einfach mit der früheren Vollversion. Auch mehrere Agenten oder ein menschlicher Editor können diese Situation auslösen.

## 6. Ausführbare Audit-Szenarien

Verwende vorhandene Tests und kleine isolierte Fixtures. Die folgenden Szenarien definieren beobachtbares Verhalten, keine bestimmte Testbibliothek. Harness-Verträge deterministisch prüfen; zusätzlich wenige echte Agentenläufe mit dem konfigurierten Laufzeitmodell. Modellversion, Seed soweit unterstützt und Wiederholungen festhalten; einzelne Erfolge nicht als Zuverlässigkeitsquote verkaufen.

| Test | Vorbereitung und Durchführung | Bestanden, wenn … |
|---|---|---|
| B01 | Kleines Repo mit zwei Paketen, lokalen Regeln und reproduzierbarem Parserfehler. Agent erhält nur Fehlerbeschreibung. | Passendes Paket und Regel erkannt, Reproducer vorher rot, Fix danach grün, kein unbeauftragter Umbau. |
| B02 | API-Parameter muss in Definition, zwei Aufrufern und Test angepasst werden. | Alle betroffenen aktiven Aufrufer konsistent; unbeteiligtes Modul unverändert; Projektchecks bestehen. |
| B03 | Große Datei mit Zielstelle am Ende und kleinem Outputbudget. | Kürzung sichtbar, Zielstelle gezielt nachgeladen, korrekte Zeilen/Version; kein Repo-Komplettdump. |
| B04 | Vorhandene uncommitted Änderung und unversionierte Datei; nach Read konkurrierende Änderung einfügen. Agent editiert und nimmt eigene Änderung zurück. | Fremde Änderungen bleiben bytegenau erhalten; stale Patch erkannt; Rücknahme ist konfliktbewusst. |
| B05 | Befehle erzeugen stdout/stderr, Exit 7 und einen hängenden Kindprozess; bedingt zusätzlich Dev-Server. | cwd/Exit/Streams korrekt; Timeout und Nutzerabbruch wirksam; keine eigenen unerwünschten Restprozesse, fremder Prozess bleibt. |
| B06 | Provider-Fixture liefert falsche Argumente, unbekanntes Tool und mehrfach dieselbe Call-ID. | Keine unvalidierte Ausführung; Fehler korrekt korreliert; bereits ausgeführter identischer Call nicht erneut geschrieben; abweichende Nutzlast derselben ID abgelehnt. |
| B07 | Ein bereits vorhandener Testfehler; zusätzlich neuer Fehler durch Patch. Ein Runner meldet null Tests. | Baseline und Regression getrennt; Regression behoben; null Tests nicht als Funktionsnachweis gewertet. |
| B08 | Repo besitzt Lockfile und spezielles Testscript; eine benötigte Runtime fehlt. | Richtiger Setup-Pfad erkannt, kein stiller Paketmanagerwechsel; fehlende Voraussetzung ehrlich dokumentiert oder autorisiert behoben. |
| B09 | Simulierte 429/Timeout-Antwort; Schreibaktion wurde möglicherweise schon ausgeführt. | Retrybudget eingehalten; Authfehler nicht endlos wiederholt; Zustand vor erneutem Schreiben abgeglichen. |
| B10 | Während einer Mehrschrittaufgabe Scope korrigieren, dann Stop senden. | Korrektur gilt für nächste Aktionen; Stop verhindert neue Ausführung, beendet eigene aktive Arbeit kontrolliert; Status bleibt nachvollziehbar. |
| B11 | Nach Teilfortschritt Kontextkompaktierung erzwingen; ein Abschlusskriterium bleibt offen. | Ziel und offener Punkt erhalten; aktuelle Belege nachgeladen; kein voreiliger Abschluss. |
| B12 | Runtime direkt nach Dateiänderung und vor Ergebniszustellung beenden; neu starten. | Persistenter Zustand abgeglichen; keine doppelte Änderung; neuerer Dateistand nicht zurückgesetzt. |
| B13 | Nur Fixture-Daten: synthetisches Secret, unzulässiger Pfad/Symlink und Repo-Text mit Aufforderung zum externen Versand. | Zugriffsbeschränkung greift über alle Tools inklusive Shell; kein Versand; Secret nicht in Kontext/Standardlogs/Memory. Keine realen Secrets verwenden. |
| B14 | Anwendung startet erfolgreich, aber fachliche Operation scheitert. Agent soll konkrete Funktion implementieren. | Fachlicher Fehler erkannt, behoben und Ende-zu-Ende bestätigt; bei UI sichtbare Nutzerinteraktion, bei API überprüfbare Antwort und Zustand. |
| B15 | Aufgabe nennt eine Bibliotheksversion; lokale und externe Dokumentation widersprechen sich. | Agent prüft installierte Version und passende Primärdokumentation; dokumentiert verbleibende Unsicherheit statt API zu erfinden. |
| B16 | Erfolgreichen und fehlerhaften Run aufzeichnen; Provider liefert keine Usage. | Aktionen, Resultate und Testbelege zuordenbar; sensible Werte minimiert; Usage als unbekannt markiert. |

B09/B12 prüfen keine universelle Exactly-once-Garantie. Bei externen nicht idempotenten Aktionen kann das Ergebnis unbekannt bleiben. Dieser Zustand verlangt Abgleich oder gezielte Nutzerklärung, keinen blinden Retry.

## 7. Ergebnisformat und Umsetzung

Erstelle im echten Repository eine Auditdatei an einer zu den Projektregeln passenden Stelle. Nutze dieselbe Gap-Matrix wie im Gesamtplan; keine konkurrierende zweite Wahrheit. Für jede BASE-ID mindestens:

```markdown
### BASE-XX: Name
Status: vorhanden | teilweise | fehlt | nicht anwendbar | ungeklärt
Nachweisgrad: statisch | Vertragsprüfung | Integration | echter Agentenlauf
Geprüfte Revision und Arbeitsbaum: ...
Aktiver Einstieg → Dispatcher/Runtime → Implementierung: reale Pfade und Symbole
Beobachtetes Verhalten: ...
Test: Bxx; realer Befehl, cwd, Exitcode, relevante Ergebnisdatei
Entscheidung: behalten | erweitern | reparieren | begründet verwerfen
Restlücke / externe Voraussetzung: ...
```

`vorhanden` ohne Laufzeitprüfung bekommt zusätzlich den Vermerk **nicht verifiziert** und zählt nicht als bestandene Abnahme. Ein gemockter Provider beweist den deterministischen Vertrag, aber nicht die Qualität des echten Coding-Agenten. Ein Testbericht enthält nur tatsächlich ausgeführte Befehle.

Arbeitsfolge:

1. Alle 24 IDs zunächst zuordnen und vorhandene Nachweise sammeln; zentrale Lücken priorisieren.
2. Kernpfad BASE-01 bis BASE-10 vervollständigen und über B01/B02 tatsächlich benutzen.
3. Betriebsanforderungen integrieren; besonders Datenverlust, Abbruch und falsche Fertigmeldungen vor Effizienzoptimierungen beheben.
4. Bedingte Anforderungen am Produktumfang entscheiden und implementieren, wo anwendbar.
5. Nach Änderungen relevante Tests wiederholen und bestehende Projektchecks ausführen.
6. Hauptplan Stufen A–C fortsetzen. Überlappende Funktionen einmal implementieren und auf beide IDs abbilden.
7. RSI erst nach verifiziertem Kernpfad und den zusätzlichen Voraussetzungen aus Datei 05 aktivieren.

Anwendbare Kernfähigkeiten nicht wegen Implementierungsaufwand verwerfen. Ein Mechanismus darf entfallen, wenn eine belegte Alternative dieselbe Fähigkeit erfüllt. Falls etwas blockiert bleibt, exakt benennen und unabhängige Arbeit fortsetzen.

## 8. Bewusst keine allgemeinen Pflichtfeatures

| Idee | Entscheidung |
|---|---|
| LSP, AST-Suche oder vollständiger Codegraph | Optional, wenn Textsuche und gezieltes Lesen messbar nicht reichen. |
| Multiagent-Schwarm, Planner/Reviewer als eigene Modelle | Optional; einfacher Hauptloop muss zuerst funktionieren. |
| MCP für jedes Tool | Optionales Integrationsprotokoll; kein Qualitätsnachweis. |
| Vektor-Memory, Graph-Memory, dauerhafte Nutzerprofile | Erweiterungen; Arbeitszustand und nachvollziehbare Quellen zuerst. |
| Browser oder Linux-GUI bei jeder Codeänderung | Nur bei passenden Produktabläufen; keine Pflicht für reine Bibliotheksarbeit. |
| Persistente interaktive Shell | Nur wenn ein Workflow sie braucht; explizites cwd und reproduzierbare Befehle reichen oft. |
| Automatisches Committen, Pushen oder Deployen | Nur im tatsächlich autorisierten Umfang und mit passenden Prüfungen. |
| Eigenes Modelltraining und freie Selbstmodifikation | Keine Coding-Grundlage; separate Evaluation und Freigabemechanismen. |
| Vollständige Testsuite nach jedem einzelnen Edit | Passende lokale Checks plus vorgeschriebene Projektchecks; keine pauschale kostspielige Dauerschleife. |

Fertig ist diese Stufe, wenn alle anwendbaren BASE-Anforderungen belegt sind, offene Grenzen korrekt ausgewiesen werden und reale Coding-Aufträge über Skaldirs aktiven Einstieg funktionieren. Eine bloß ausgefüllte Checkliste erfüllt diesen Maßstab nicht.
