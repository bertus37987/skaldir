# Skaldir — verbindlicher Produkt- und Architekturplan

Stand: 25. September 2026. Grundlage: ausführliche Nutzerklärung, frühere Logs, Handoff und lesender Slash-Audit. Dieser Plan beschreibt das Soll. Genannte Codepfade sind Integrationskandidaten; ihre aktuelle Funktion muss geprüft werden.

## 1. Ziel und Prioritäten

Skaldir ist ein schneller Hauptagent, der normale Wissens-, Recherche- und Coding-Aufgaben erledigt und den echten Computer umfassend bedienen kann. Er führt unabhängige Tools parallel aus, nutzt Vision gezielt, lernt getestete GUI-Abläufe als Subtools, baut neue Tools und kombiniert vorhandene Fähigkeiten zu Workflows. Eine Vollbild-TUI zeigt den Fortschritt live. Ziele überleben Kontextwechsel und technische Unterbrechungen; Erfolg erfordert passende Verifikation.

„Schnell“ bedeutet geringe vermeidbare Verzögerung, kurze Toolwege und sichtbare Reaktion. Eine sachlich notwendige neun Stunden lange Arbeit darf neun Stunden dauern. Keine künstliche Kurzzeitgrenze, keine aufgeblähte Denk-/Supervisor-/Tests-Schleife.

### 1.1 Prioritäten

| Priorität | Umfang |
|---|---|
| P0 Grundlage | Standard-Agent-Funktionen, zuverlässige Ausführung, korrektes Tool-Dispatch, Zustände und echte Ergebnisse |
| P0 Produkt | Vollständiges Host Computer Use, globaler Notstopp, schnelle Subtools, allgemeiner und Docker-Tool-Builder, kombinierbare Workflows, Jev/Voice/VERIFY |
| P1 durchgehend | Geschwindigkeit, Streaming, Kontextökonomie, nachvollziehbare Approvals, dauerhafte Runtime |
| P1 Produkt | Vollbild-TUI-Polish, selektives Memory, Skills, Scheduler und langfristige Kontinuität |
| P2 später | Kontrollierte RSI-/Harness-Optimierung; API-Fernzugriff und weitere Betriebssysteme |

P1 ist kein unverbindlicher Wunsch. Die Stufen bezeichnen Abhängigkeiten; der volle Auftrag umfasst P0 und P1. Messung und verständliche Statusdarstellung beginnen bereits in P0.

### 1.2 Produktgrenzen

Erste unterstützte Plattform: Fedora, X11 und Wayland. Tatsächlichen Desktop/Compositor, Version und verfügbare Sitzung zuerst feststellen. Spätere Arch-basierte Plattform erst nach konkreter Zieldefinition. Der Hauptagent nutzt den echten Host, vorhandene Browserfenster und angemeldete Konten. Container sind Werkzeuge und isolierte Workerumgebungen, kein stillschweigender Ersatz für die angefragte Hostaktion.

„Alles, was ein Mensch am Computer kann“ ist die Entwicklungsrichtung. Ein Release nennt präzise geprüfte Anwendungen, Backends und bekannte Grenzen. Kein universeller Fähigkeitsclaim ohne Nachweis.

## 2. Verbindliche Bestandsaufnahme und Migration

**AUD-01:** Einstieg vom installierten `skaldir`-Wrapper bis zu CLI/TUI, Agent-Loop, Toolset, Provider, Dispatcher, Executor und Persistenz verfolgen. Prüfen, dass der Nutzer tatsächlich den bearbeiteten Quellstand ausführt.

**AUD-02:** Eine gemeinsame Gap-Matrix führen. Jede ID hier mit aktivem Code und Test verbinden; gleichwertige vorhandene Lösungen gelten. Keine zweite Registry, kein konkurrierender Goal-/Memory-/Approval-Store.

**AUD-03:** Datenmigrationen versionieren, wiederholbar gestalten und Rückweg definieren. Bestehende Goals, Transkripte, Skills, Tools und Workflows nicht verlieren. Unbekannte alte Evidence nicht rückwirkend als verifiziert ausgeben.

### 2.1 Zu untersuchende bestehende Komponenten

| Kandidaten unter `src/skaldir/` | Prüfauftrag |
|---|---|
| `cli.py`, `agent_loop_cli.py`, `repl.py`, `chatbar.py` | Aktiver Einstieg, Streaming, UI/Runtime-Trennung |
| `agent_loop.py`, `agent_toolset.py`, `discovery.py`, `tools_core/` | Discovery, Schemaaufnahme, Dispatch, parallelisierte Ausführung |
| `act_service.py`, `computer_use.py`, `cu_host_session.py`, `cu_platform.py` | Host-Backend, Beobachtung, Eingabe, Sessionlebenszyklus |
| `toolbuilder.py`, `docker_ensure_cli.py`, `skills.py` | Generierung, Client-Injektion, Tests, Registry, Promotion |
| `runs_store.py`, `events.py`, `ledger.py`, `tool_archive.py` | Goals, Operationszustände, Events, Recovery und Artefakte |
| `knowledge.py`, `knowledge_cli.py`, `memory_store.py` | Dateiwissen, Suche, Scopes, Quellen, Revisionen |
| `llm_provider.py`, `subagents.py`, `subagent_roles.py` | Fähigkeitsrouting, Usage, Workerrollen und gemeinsame Budgets |
| `slash.py`, `complete.py`, `cli_contract.py` | Tatsächliche Auflösungen, Pflichtargumente und Registrierung |

Die Pfade von Jev, Voice, VERIFY, COMPOSE, Approval und Scheduler anhand ihrer aktiven Aufrufer ermitteln. Ein Modulname oder erreichbarer Selftest allein genügt nicht.

### 2.2 Bekannte Fehler zuerst in Regressionen überführen

- Mehrfacher Zen-Start nach fehlender Fensterbeobachtung; Nutzer meldete 12+ Fenster.
- stdout wurde als `verified` statt tatsächlicher Ausgabe zurückgegeben; behaupteter Fix live nachweisen.
- `web_search` gefunden, dann irrtümlich als Shell-Binary gestartet.
- CLI-Diagnose abhängig von GUI-Endzustand; Schema-/Payload-Widersprüche.
- Builder ohne konfigurierten Modellclient, obwohl Hauptagent einen Provider besitzt.
- COMPOSE registriert Dateien, aber tatsächlicher Aufrufweg unklar.
- Erfolgreiches Dateischreiben mit `created:false` wird als Fehler gedeutet.
- Lange wiederholte Testbefehle ohne sichtbaren Fortschritt.

Diese Punkte anhand aktueller Reproducer prüfen: Sie können inzwischen repariert sein. Keine bereits behobene Lücke erneut implementieren.

## 3. Architektur: eine Runtime, mehrere Oberflächen

**ARCH-01:** Das bestehende Schichtenmodell beibehalten und klar zuordnen:

```text
TUI / bestehende CLI / später API
             ↓ Aufträge, Steuerung, Approvals
Persistente Run-Runtime + Goal-/Operationszustand
             ↓
Hauptagent ↔ Kontext/Memory/Skill-Lader
             ↓
Jev: Routing, Relevanz, Triage
             ↓
Gemeinsamer Dispatcher + Approval/Policy + Scheduler
  ├─ Dateien / Code / Terminal / Web / MCP
  ├─ ACT Host-Desktop + Browser + Vision
  ├─ getestete GUI-Subtools
  ├─ Container-/Docker-Tools
  └─ COMPOSE / Workflows / eingeschränkte Worker
             ↓ tatsächliche Ergebnisse und Events
Voice: ereignisgetriebene Supervision
VERIFY: fachliche Ergebnisprüfung
ENFORCE: verbindlicher Abschlusszustand
PERSIST: Checkpoints, Artefakte, Wiederaufnahme
```

Kein Modell darf direkt über einen alternativen Executor die gemeinsamen Rechte, Desktopzuordnung, Cancellation oder Accounting umgehen.

**ARCH-02:** Ausführungszustand unabhängig vom TUI-Prozess halten. Ein vorhandener Worker-/Servicepfad ist zu bevorzugen; sonst einen benutzerbezogenen Runtimeprozess mit lokalem IPC ergänzen. TUI hängt sich an vorhandene Runs an. API später auf dieselben Commands/Events setzen. Jetzt keinen ungeschützten Netzwerkserver nur für Architekturkosmetik bauen.

**ARCH-03:** Versionierte interne Commands für Run starten, pausieren, fortsetzen, abbrechen, ansteuern, Approval beantworten, Events abonnieren und Artefakte abrufen. Bestehende CLI-Ausgabeverträge erhalten; Streamingformat gesondert versionieren.

## 4. Alle normalen Agentenfunktionen sind Pflicht

| ID | Fähigkeit | Mindestverhalten |
|---|---|---|
| CORE-01 | Auftrag und Planung | Ziel, Scope, Grenzen, offene Kriterien; kurze Aufgaben ohne unnötigen Planapparat |
| CORE-02 | Repositoryorientierung | Regeln, Stack, Paketmanager, Einstieg, Dirty Tree, Build-/Testbefehle |
| CORE-03 | Dateisuche und Lesen | Namen/Text suchen, exakte Bereiche, Revision, große Dateien begrenzt lesen |
| CORE-04 | Änderungen | Erstellen, präzise patchen, umbenennen, erlaubtes Löschen; fremde Arbeit bewahren |
| CORE-05 | Terminal | cwd, Argumente/Umgebung, stdout/stderr, Exitcode, Timeout, eigene Prozesse verwalten |
| CORE-06 | Code Execution | Python oder vorhandene Laufzeit, begrenzte Ressourcen, echte Ergebnisse, Artefakte |
| CORE-07 | Debugging | Reproducer, Ursachenprüfung, gezielter Fix und passende Regression |
| CORE-08 | Tests und Build | Tatsächlichen Projektrunner nutzen; null Tests und alte Fehler unterscheiden |
| CORE-09 | Git/Recovery | Ausgangsstatus, eigener Diff, konfliktsensible Rücknahme, unversionierte Änderungen schützen |
| CORE-10 | Web Search/Fetch | Kostenlos nutzbarer Suchpfad, Seiten abrufen, Quellen und Fehler korrekt zurückgeben |
| CORE-11 | Browser | Seiten/Tabs lesen und bedienen, Downloads/Uploads, dynamische Seiten |
| CORE-12 | Tool Discovery/MCP | Verfügbarkeit, Schema, gültiger Call, Ergebnis; keine bloße Namensliste |
| CORE-13 | Dateien/Medien | Dokumente, Bilder und vorhandene geeignete Medienwerkzeuge nutzen; Artefakte lesbar übergeben |
| CORE-14 | Modell-/Providerfehler | Schemafehler, 429, Auth, Timeout, Streamingabbruch; begrenzte zustandsbewusste Recovery |
| CORE-15 | Nutzersteuerung | Laufende Korrektur, Pause, Stop, Details, Resume, präzise Blocker |
| CORE-16 | Evidenz und Telemetrie | Run/Call/Artefakte zuordenbar, tatsächliche Usage oder explizit unbekannt |

Vorhandene BASE-01 bis BASE-24 aus früheren Plänen funktional abgleichen; diese neue CORE-Gruppe ersetzt keine dort bereits notwendige Fehlerbehandlung. Keine 24 parallelen Implementierungsbaustellen aufmachen.

### 4.1 Kostenloser Webzugang

**WEB-01:** Mindestens einen tatsächlich nutzbaren Pfad ohne zusätzlichen kostenpflichtigen Such-API-Schlüssel bereitstellen. Nicht pauschal behaupten, kostenlose öffentliche Endpunkte seien immer verfügbar.

Integrationsvorschlag: vorhandenes `web_search` reparieren; als kontrollierbaren Provider optional selbst gehostetes SearXNG, wenn passende Infrastruktur verfügbar ist. Dessen API unterstützt strukturierte Suche, öffentliche Instanzen können JSON jedoch deaktivieren. Konkrete Instanz testen und Health erfassen. Hosting, Netzwerk und Modellinferenz sind dadurch nicht automatisch kostenfrei. [SearXNG API](https://docs.searxng.org/dev/search_api.html)

**WEB-02:** Ein Providervertrag für Suchquery, Sprache/Zeitraum soweit unterstützt, Resultate mit URL/Titel/Snippet/Quelle sowie Rate-Limit-/Netzwerkfehler. Fallback auf erlaubte Browserrecherche, nicht unkontrolliert öffentliche Instanzen abklappern. Keine Umgehung von Zugriffssperren als Verfügbarkeitsstrategie.

**WEB-03:** Fetch-Ergebnis mit tatsächlicher URL nach Redirect, HTTP-Status, Inhaltstyp, Abrufzeit, Extraktionsstatus und Inhaltsreferenz. Dynamische Inhalte browserbasiert lesen. Quelleninhalt als Daten behandeln; Webseiten dürfen keine Systemregeln oder Approvals ändern.

**WEB-04:** Neue Quellen, Aussagen und Datum trennen. Suchsnippet ist kein vollständig gelesener Artikel. Für Recherche Synthese und Quellenbelege liefern; bei nicht erreichbaren Seiten die Lücke nennen.

## 5. Tool-Vertrag und schnelle parallele Ausführung

**TOOL-01:** Jede registrierte Fähigkeit besitzt ID, Version, Input-/Output-Schema, Fähigkeiten/Backend, Health, Rechte, Seiteneffektklasse, Timeout, Cancel-Verhalten und Ausführungsadapter. Ein Tool ist erst verfügbar, wenn es im aktuellen Run tatsächlich aufrufbar ist.

**TOOL-02:** `tool_search` liefert einen gültigen nächsten Aufrufweg. Dynamische Schemas entweder korrekt in den Providerkontext aufnehmen oder über einen vorhandenen generischen Dispatcher mit validierter Tool-ID zugänglich machen. Keine Runtime-Tool-ID als Shellprogramm ausgeben.

**TOOL-03:** Einheitliches Ergebnis mit getrennten Zuständen:

```text
call_id, tool_id, tool_version, run_id, goal_revision
permission: allowed | denied | awaiting_approval
execution: not_started | running | completed | failed_to_start |
           timed_out | cancelled | outcome_unknown
exit_code: int | null
stdout, stderr, structured_data
verification: not_required | pending | pass | fail | unknown
artifact_refs, truncated, continuation
started_at, finished_at, error_category, retry_hint
```

Konkrete Feldnamen an vorhandene Verträge anpassen. Fehlender Exitcode ist kein Nullcode. `completed` bedeutet nicht fachlich erfolgreich. `created:false` plus erfolgreicher Write bedeutet aktualisiert. Erst validieren, dann ausführen; auch unvollständige gestreamte Toolargumente niemals vorzeitig starten.

**BATCH-01:** Der Hauptagent darf mehrere unabhängige Calls in einer Runde planen. Scheduler startet sie begrenzt parallel; Ergebnisse streamen einzeln sofort, Endaggregation wartet nur wo nötig. Providerformat und Zuordnung von Tool-Call/Result-Paaren bleiben gültig.

**BATCH-02:** Abhängigkeiten und Ressourcen berücksichtigen. Zwei Reads dürfen parallel; Write→Read sequenziell; mehrere Mutationen derselben Datei und Eingaben am Host-Desktop benötigen Koordination. Zugriff auf denselben Browser-Tab kann auch per DOM mit GUI konkurrieren.

**BATCH-03:** Ein langsames Tool blockiert nicht sämtliche Statusanzeigen. Ein Fehler verwirft nicht alle erfolgreichen Ergebnisse. Beim Stop keine neuen Calls starten; laufende Calls nach Fähigkeit abbrechen oder unbekannten Zustand markieren. Kein erneuter Batchlauf mit bereits ausgeführten Seiteneffekten.

**BATCH-04:** Programmatic Tool Calling über denselben Dispatcher: bekannte Abfolgen, Filter und Aggregationen ohne zusätzliche Modellrunde pro Minioperation. Ressourcen-/Call-/Outputbudgets gelten gemeinsam. Keine Shell-Verkettung anstelle korrekter Argumentsicherheit.

## 6. Hauptagent und Workerrollen

**ROLE-01:** Der Hauptagent besitzt Plan, Projektänderungen und Integration. Codeentwicklung und abschließende Reparaturen liegen bei ihm. Builder-Codeerzeugung ist eine Toolfähigkeit unter seiner Verantwortung, kein unkontrollierter zweiter Projektbearbeiter.

**ROLE-02:** Subagents nur für größere unabhängige Recherche oder Codebase-Exploration. Günstige Modelle nach Konfiguration einsetzen; strukturierte Rückgabe mit Erkenntnissen, Quellen, Grenzen und Belegen. Kein automatischer Schwarm für jede Anfrage.

**ROLE-03:** Worker verändern nicht den gemeinsamen Projektarbeitsbaum. Falls Experimente nötig sind, isolierter Snapshot/Clone mit klarer Rückgabe; Hauptagent übernimmt bewusst. Worker-Computer-Use findet in eigenem Containerdesktop statt, nicht auf dem Host. Konten/Cookies nicht pauschal in Container kopieren.

**ROLE-04:** Gemeinsames Budget, Cancellation und Laufhierarchie. Keine losgelösten Worker nach Goal-Stop. Ein Worker-„done“ ist kein Gesamtabschluss.

## 7. Approval-Modi und Rücknehmbarkeit

**APR-01:** Drei explizite Modi, im Chat sichtbar per Tab umschaltbar. „Approve nichts“ ist ein Alias für Ask Everything, kein vierter Modus.

| Modus | Verhalten |
|---|---|
| Ask Everything | Vor ausführbaren Toolaktionen Zustimmung einholen; vollständig aufgelisteten Batch oder begrenzten Subtool-Auftrag gemeinsam freigeben möglich |
| Approve for me | Bekannte unkritische Aktionen automatisch; unklare oder folgenreiche Aktionen vorlegen |
| Approve Everything | Im Nutzerauftrag liegende Aktionen automatisch; feste Ausnahmen bleiben |

Reine Statusdarstellung und das Stoppen selbst benötigen keine Zustimmung. Ein vorab genehmigtes deterministisches Subtool braucht nicht bei jedem internen Klick erneut zu fragen, solange Umfang, Ziel und Bedingungen unverändert sind. Neue externe Wirkung oder Abweichung verlangt Neubewertung.

**APR-02:** In allen Modi Zustimmung für neue Geldausgaben und irreversible beziehungsweise dauerhaft schädliche Aktionen. Kritische Systemzerstörung ist kein normaler Agentenauftrag. Projektdateien bearbeiten/löschen ist automatisch möglich, wenn es zum Auftrag passt und ein tatsächlich nutzbarer Rückweg existiert. Git ignorierte/untracked Dateien, ungesicherte Nutzeränderungen und externe Effekte nicht als automatisch rücknehmbar behandeln.

**APR-03:** Nachrichtenversand und andere Außenwirkung im beauftragten Scope anhand des Modus behandeln; „alle Konten nutzen“ ist keine Erlaubnis für beliebige sachfremde Aktionen. Zahlungsapproval zeigt mindestens Empfänger/Anbieter, Betrag/Währung, Leistung und gegebenenfalls Wiederholung. Änderung dieser Daten macht eine alte Freigabe ungültig.

**APR-04:** Approvals an Run, Aktion/Workflowversion, Parameter beziehungsweise erlaubten Parameterbereich und Policyrevision binden. Modiwechsel aktualisiert nicht rückwirkend abgeschlossene Aktionen; wartende Aktionen vor Start mit aktueller Policy neu prüfen. Noch nicht freigegebene finanzielle oder irreversible Schritte bei Scheduling nicht automatisch übernehmen.

**APR-05:** Vorgeschlagener Default bei ungeklärter Persistenz: Modus pro Goal/Run, konfigurierbarer Default für neue Runs; Zeitworkflows mit expliziter eigener Policy. Kein stiller globaler Wechsel zu Approve Everything. Diese Wahl als Implementierungsentscheidung dokumentieren, nicht als bereits beantwortete Nutzerfrage ausgeben.

## 8. Vollständiges Computer Use auf dem echten Fedora-Host

### 8.1 Fähigkeiten

**CU-01:** Laufzeit-Capability-Matrix pro Fedora-Sitzung erfassen: X11/Wayland, Desktop/Compositor, Screenshot, Window/App-State, Accessibility, Fokus, Maus, Tastatur, Drag, Clipboard, Portal-/Backendzugang und globaler Notstopp. Unbekannt, blockiert und verfügbar auseinanderhalten.

**CU-02:** Beobachten→Aktion→Verifizieren. Jede Beobachtung trägt ID, Session, Ziel, Zeitpunkt, Geometrie/Skalierung und Gültigkeitsbedingungen. Screenshotkoordinaten nach Resize/Monitorwechsel nicht blind weiterverwenden.

**CU-03:** Unterstützen: Apps öffnen/wiederverwenden, Fenster/Tabs finden, klicken/doppelklicken/Rechtsklick, scrollen, Drag-and-drop, Text/Unicode, Shortcuts, gehaltene Tasten, Dialoge, Dateiöffnung/-speicherung, Upload/Download und Clipboard bei Bedarf.

**CU-04:** Strukturierte Schnittstellen (DOM, Accessibility, App-API) mit Vision/GUI kombinieren. Der Nutzer erlaubt gemischte Methoden. Bei Blender sind Skripte zulässig, aber Ergebnis in der echten Anwendung prüfen. Ein API-Test ersetzt nicht geforderte UI-Abnahme.

**CU-05:** Hauptmodell mit Vision nutzen, wenn unterstützt; optional separates nutzerkonfiguriertes Vision-Modell. Bilder zuschneiden/skalieren mit nachvollziehbarer Koordinatentransformation. Vision an unklaren oder veränderten Zuständen und relevanten Prüfpunkten, nicht blind nach jedem Tastendruck. Keine starre Quote, die notwendige Prüfung verhindert.

**CU-06:** Browserwahl und Konten respektieren. Zen nicht still durch Brave oder eine neue leere Browserumgebung ersetzen. DOM-Automation ist backendabhängig: Playwright dokumentiert unterstützte Browser und spezielle Firefox-Builds; daraus folgt keine beliebige Steuerbarkeit einer bestehenden Zen-Sitzung. Tatsächliche Verbindung prüfen, andernfalls Host-GUI nutzen. [Playwright Browserdokumentation](https://playwright.dev/python/docs/browsers)

### 8.2 Desktopkontrolle und Warteschlange

**DESK-01:** Host-Desktop erhält eine exklusive lease mit Besitzer-Run, Token/Generation, gültiger Sitzung und Kontrollstatus. Abhängige Subtools desselben Runs verwenden dieselbe Zuständigkeit. Scheduler-/andere Runs warten sichtbar. Unabhängige Container-/Webarbeit darf weiterlaufen.

**DESK-02:** Eigentümerwechsel entwertet alte Eingaben. Eine nach Pause/Crash verspätete Aktion darf die Maus nicht bewegen. Single-owner allein genügt nicht: Fencing beim tatsächlichen Eingabeadapter prüfen.

**DESK-03:** Sichtbarer Host-Indikator während Steuerung, auch ohne fokussierte TUI. Er zeigt verantwortlichen Auftrag und Notstopp-Hinweis. Dieser kleine Status-/Kontrollmechanismus ist erforderlich; keine zusätzliche allgemeine Desktop-App bauen.

**DESK-04:** Nutzer erlaubt längere exklusive Steuerung etwa für Video Editing. Nicht pauschal nach wenigen Sekunden unterbrechen. Bei reinem Warten Lease nur dann freigeben, wenn Wiederaufnahme und Zielzustand kontrolliert möglich sind. Andere Inputs nicht verdeckt blockieren; Notstopp immer erreichbar.

### 8.3 Globaler Doppel-Escape: eigener früher Machbarkeits- und Abnahmepunkt

**STOP-01:** Zwei physische Escape-Tastendrücke in kurzem Abstand pausieren den betroffenen Host-Goal-Run, stoppen neue Eingaben und geben die Kontrolle zurück. Wirkt auch bei fokussiertem Zen, Editor, Blender oder Vollbild-App. Vorgeschlagenes Intervall 500 ms konfigurierbar; Tastatur-Autorepeat zählt nicht als zwei bewusste Drücke.

**STOP-02:** Nicht von Modellrunde, Voice oder TUI-Eventloop abhängig machen. Eingabescheduler und Kontrollagent reagieren direkt. Laufende Drag-/Modifierzustände kontrolliert lösen, gepufferte Eingaben verwerfen, Lease entwerten. Schon ausgeführte Aktionen werden dadurch nicht rückgängig gemacht.

**STOP-03:** Agentensynthetische Escape-Eingaben dürfen nicht als Nutzer-Notstopp fehlinterpretiert werden. Ereignisherkunft und Backendfähigkeiten prüfen. Beim globalen Abgriff nur benötigte Tastensignale verarbeiten, keine allgemeine Tastaturaufzeichnung speichern. Auswirkung auf normales Escape-Verhalten der Ziel-App ausdrücklich testen.

**STOP-04:** Portal-/Compositor-Unterstützung prüfen, nicht voraussetzen. GlobalShortcuts bietet fokusunabhängige Shortcuts; daraus folgt nicht automatisch Unterstützung einer Sequenz aus zweimal unmodifiziertem Escape. X11 und Wayland benötigen möglicherweise verschiedene zugelassene Implementierungen. [GlobalShortcuts](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.GlobalShortcuts.html)

**STOP-05:** Wenn Doppel-Escape auf einem Backend nicht nachgewiesen funktioniert: diesen Host-Steuerungsmodus nicht als vollständig fertig freigeben. Konkreten Blocker benennen; alternative Stopptaste/Indikator ist eine zusätzliche Sicherung, kein stiller Ersatz für den Nutzerwunsch. Nicht eigenmächtig OS-Grenzen umgehen oder root-Zugriff voraussetzen.

### 8.4 Browserlebenszyklus

**BROWSER-01:** Zentral `ensure browser/session`, nicht unkoordinierte Starts. Vorhandene Session wiederverwenden, parallele Starts zusammenführen, Bereitschaft prüfen. Zustand `unconfirmed` ist kein Auftrag zum Neustart.

**BROWSER-02:** Fehlerkette aus Logs verhindern: Start erfolgreich/unklar + Fenster nicht erkannt → Beobachtung prüfen; kein erneutes `zen-browser` pro Modellrunde. Dasselbe gilt für direkte CLI-/Subtool-/Workflowwege.

**BROWSER-03:** Eigene Tabs/Fenster und bestehende Nutzerressourcen unterscheiden. Passende neue Tabs für Recherche sind erwünscht; zwölf unbeabsichtigte Fenster nicht. Cleanup nur innerhalb bekannter Besitzverhältnisse. Vom Nutzer gewünschte Fenster und fertige Artefakte erhalten.

**CU-07:** Unter Wayland aktuelle unterstützte RemoteDesktop-/Screenshot-/Accessibility-Pfade untersuchen. RemoteDesktop beschreibt sitzungsbezogene Eingabe; reale Backendunterstützung und Freigabe sind gesondert zu testen. XWayland-Erfolg ist keine vollständige Wayland-Abnahme. [RemoteDesktop](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.RemoteDesktop.html)

## 9. Gelernte GUI-Subtools

**SUB-01:** Ein Subtool ist ein ausführbarer, parametrisierter, getesteter Ablauf. Beispiel: vorhandenes Zen verwenden → Proton öffnen → Posteingang prüfen → neueste Nachrichten strukturiert zurückgeben. Hauptagent erhält das Ergebnis; interne Schritte bleiben im TUI-Toolbereich einsehbar.

**SUB-02:** Erfolgreiche Trajektorien automatisch auf wiederverwendbare Muster prüfen. Kandidaten speichern; unnötige Fehlversuche/Wiederholungen entfernen. Nicht einfach jede doppelte Aktion löschen: notwendiges Polling, Scrollen und Retry nach echter Änderung können erforderlich sein.

**SUB-03:** Bereinigten Kandidaten erneut auf Äquivalenz und Postconditions testen. Bei Kauf-/Sendeaktionen keinen zweiten echten Vorgang nur zur Qualifikation auslösen: Fixtures, Dry-run, Testkonten oder nicht auslösende Vorstufen verwenden. Fehlt ausreichende Validierung, Kandidat bleibt nicht aktiv.

**SUB-04:** Lebenszyklus candidate→validated→active→degraded/disabled→revalidated. Automatische Aktivierung nach erfüllten Kriterien und im erlaubten Scope ist gewünscht; Nutzerbestätigung optional, kein Ersatz für Test. Herkunft, Version und unterstützte Appzustände speichern.

**SUB-05:** Vertrag: Parameter, Preconditions, Zielressourcen, Anker/Selektoren, Aktionen, Outputschema, Evidence, Rechte, Timeout, Retry/Recovery. Keine bloßen Bildschirmkoordinaten ohne Zustandsbindung.

**SUB-06:** Wiederverwendung zuerst prüfen, bevor Hauptagent bewährte Abläufe neu plant. Bei UI-Drift neu beobachten, begrenzt reparieren und neue Version erneut qualifizieren. Kein teurer neuer Hauptmodellaufruf für jeden bekannten Klick.

## 10. Skills als bedarfsgeladene Arbeitsanleitungen

**SKILL-01:** Skills erklären Vorgehen; Tools führen Aktionen aus. Computer Use, Tool Creation, Recherche und projektspezifische Arbeitsweisen erhalten vorhandene oder ergänzte Skills. Keine ausführbare Fähigkeit durch eine Markdown-Anleitung vortäuschen.

**SKILL-02:** Registry enthält kompakte Beschreibung, Trigger, Version, Voraussetzungen und Inhaltsreferenz. Jev/Hauptagent lädt passende Anleitung gezielt vor der Arbeit. Volltexte nicht ständig global in den Prompt aufnehmen.

**SKILL-03:** Skills, GUI-Subtools, Docker-Tools, MCP und normale Tools frei komponierbar. Eine Anleitung kann einen Workflow orchestrieren, erweitert aber keine Rechte. Fremde Skills als versionierte überprüfbare Eingabe behandeln, nicht als höhere Nutzerautorität.

**SKILL-04:** Aktualisierung und Lernen auf erfolgreiche Evidence stützen; widersprüchliche oder veraltete Skills reparieren/deaktivieren. Rollen von AGENTS.md, SOUL.md und Tasks nicht vermischen.

## 11. Allgemeiner Tool Builder und Docker Tool Builder

### 11.1 Allgemeiner Builder

**BUILD-01:** Prompt→Bedarf→vorhandene Fähigkeiten suchen→gegebenenfalls generieren→validieren→ausführen→fachlich testen→registrieren→Discovery/erneuten Aufruf prüfen. Vorhandene passende Tools wiederverwenden.

**BUILD-02:** Tooltypen je nach Bedarf: Code-/CLI-Adapter, HTTP/OpenAPI, MCP-Anbindung, GUI-Subtool, Containeradapter oder Composite. Einheitlicher Lifecycle statt voneinander getrennter Inseln.

**BUILD-03:** Konfigurierten Modellclient, Executor und Broker korrekt injizieren; Fehler „no model client configured“ früh im Builder-Health zeigen. Nutzer soll nicht erst generierten Code manuell ausführen müssen.

**BUILD-04:** Input-/Outputschema, Version, Tests, Ressourcen-/Netzwerkbedarf und Rückweg sind Bestandteil des Tools. Ungültige Inputs, Timeouts und Ausfälle prüfen. Erst gebautes und tatsächlich aufrufbares Tool als verfügbar melden.

### 11.2 Containerwerkzeuge

**DOCKER-01:** Vorhandene Docker-/rootless-Podman-Infrastruktur nutzen, Kompatibilität prüfen. Engineverfügbarkeit vor Generierung klären. Nicht bei jedem Toolcall erneut unveränderte Images bauen.

**DOCKER-02:** Buildkontext, reproduzierbare Dependencies soweit sinnvoll, Entrypoint, Image-ID/Digest, Log und Exitcode erfassen. Container mit definierten Mounts, Outputpfad, Netzwerk, Ressourcen und Credentials-Handles ausführen.

**DOCKER-03:** Image/Container kann mehrere typisierte Subtools anbieten: etwa Datenabruf, Berechnung, Dashboarderzeugung oder Videooperationen. Kein neues Image für jeden kleinen Parameterwert nötig. Interne Tools besitzen dieselben Dispatch-/Approval-/Resultverträge.

**DOCKER-04:** End-to-End: bauen→starten→fachliches Ergebnis→registrieren→über Agent aufrufen→später wiederverwenden. Dockerfile + README ist nur ein Zwischenstand.

**DOCKER-05:** Stock Research, Editing-Suite, Medien-Download und weitere Tools sind mögliche Aufgaben. Konkrete Zugriffsrechte, Datenverfügbarkeit und jeweilige Nutzungsbedingungen berücksichtigen; keine universelle Onlineverfügbarkeit behaupten. Keine bezahlten Datenquellen stillschweigend hinzufügen.

## 12. COMPOSE, Workflows und Zeittrigger

**FLOW-01:** Alle Skaldir-Fähigkeiten per natürlichem Auftrag erreichbar: arbeiten, bauen, speichern, Workflow starten und planen. Slash-Commands bleiben zusätzliche Bedienwege, keine Pflichtsprache des Nutzers.

**FLOW-02:** Ein Workflow besteht aus versioniertem Ziel-/Prompttemplate, Parameterschema und ausführbaren Schritten beziehungsweise Abhängigkeiten. GUI-Subtool→Dockeranalyse→MCP/Dateioutput darf ein Ablauf sein. Bedingte Schritte, Schleifen mit Grenzen und Fehlerpfade explizit darstellen.

**FLOW-03:** Bekannte Schritte ohne erneute vollständige LLM-Planung ausführen. Bei neuer Situation klar definierte Replan-/Fallbackgrenze. Workflowregistrierung allein ist kein erfolgreicher Workflowlauf.

**FLOW-04:** „Starte meinen Stock-Research-Workflow“ anhand eindeutiger Identität erkennen, Parameter ergänzen und ausführen. Bei mehreren passenden Workflows gezielt klären statt zufällig wählen.

**SCHED-01:** Zeittrigger auch bei geschlossener TUI ausführen. Zeitplan mit Zeitzone, aktivierter Workflowversion, Parametern, Policy und Runs speichern. Vorschlag: Europe/Berlin für diesen Nutzer; Sommerzeit, ausgelassene und doppelte Uhrzeiten explizit definieren.

**SCHED-02:** Überlappung standardmäßig vermeiden: gleicher Workflow serialisiert; verpasste Ausführungen konfigurierbar überspringen oder einmal nachholen, nicht unkontrolliert alle nachholen. Singleton-Scheduler und persistente Ausführungs-ID verhindern doppelte Starts.

**SCHED-03:** Desktop belegt→sichtbare Warteschlange; Approval fehlt→waiting_approval; Dienst fehlt→präziser Blocker. Unabhängige zulässige Schritte dürfen weiterlaufen. Keine automatische Geldfreigabe aus Zeitplan ableiten.

**SCHED-04:** Workflowdefinition, Aktivierung, Pause und Löschung sind getrennte Aktionen. Der vorliegende Plan erstellt selbst keinen Zeitplan auf dem Nutzergerät.

## 13. Persistente Goals, Verifikation, Stop und Resume

**GOAL-01:** Goal separat vom Chat speichern: Originalauftrag, Revisionen, Scope, Abschlusskriterien, Nichtziele, Plan, offene Punkte und Evidence. Kontextkompaktierung verändert das Goal nicht. Nutzerkorrekturen nachvollziehbar übernehmen.

**GOAL-02:** Abschlusskriterien früh aus Auftrag ableiten, bei wichtiger Unklarheit gezielt fragen. Keine Zusatzfeatures nur zur Beschäftigung erfinden. Alle vereinbarten Features prüfen, nicht unendlich viele hypothetische Möglichkeiten.

**VERIFY-01:** Für Apps neben Codechecks echte Computer-Use-Abnahme: Hauptabläufe, relevante Fehlerfälle, Datenpersistenz, Neustart und Artefakte entsprechend dem Produkt. Browser-/GUI-Ergebnisse mit Zustand, Beobachtung und konkreten Kriterien verbinden. Screenshot allein kann unzureichend sein.

**VERIFY-02:** PASS/FAIL/UNKNOWN. UNKNOWN zählt nie als fertig. FAIL führt zur gezielten Reparatur; externer unlösbarer Blocker zu sichtbarer Pause. Validatoren nicht lockern, nur um PASS zu erreichen.

**VERIFY-03:** Fachliche Toolergebnisse und vollständiges Goal getrennt. Image gebaut, Prozess läuft, Fenster vorhanden oder Tests ohne Fälle sind keine universelle Erfolgsdefinition.

**RUN-01:** Vorgeschlagene Zustände: queued, running, waiting_desktop, waiting_approval, verifying, paused_user, blocked_external, recovering, completed, cancelled. Vorhandenes Modell adaptieren. Terminalverbindung ist ein davon unabhängiger Zustand.

**RUN-02:** TUI schließen/detachen→Runtime arbeitet weiter. Runtimeabsturz→Recovery prüft persistente Operationen und setzt erlaubte Arbeit fort. Rechnerneustart→nach nötiger Umgebung fortsetzen; Host-Desktop erst nach gültiger eingeloggter Sitzung und Notstopp-Verfügbarkeit. Kein GUI-Klicken am Login-/Sperrbildschirm vortäuschen.

**RUN-03:** Expliziter Stop und Doppel-Escape setzen einen dauerhaften Nutzer-Pausestatus für den betroffenen Run. Kein automatischer Restart darüber hinweg. Explizites Resume nötig. „Abbrechen“ kann als eigene terminale Aktion angeboten werden; Stop nicht mit Detach verwechseln.

**RUN-04:** Vor Side Effect Operationsabsicht persistieren, danach Ergebnis und Evidence. Bei Crash zwischen Aktion und Ergebnis zuerst externen Zustand abgleichen. Idempotenzschlüssel wo unterstützt; keine universelle Exactly-once-Garantie für GUI/Käufe/Versand.

**RUN-05:** Blocker in klarer roter Fehlermeldung plus Text: was passiert ist, was erhalten blieb, warum es nicht weitergeht und welche konkrete Nutzeraktion nötig ist. Keine Meldung „Erfolg“ bei angehaltenem unvollständigem Goal. Fortlaufende Hintergrundruns jederzeit sichtbar abrufbar.

## 14. Jev, The Voice und geringe Kontrollkosten

**JEV-01:** Jev ist schnelle Entscheidungsebene: Capability-/Toolwahl, Subtool-Wiederverwendung, Memory-Relevanz, Kontextbudget und Triage. Bestehenden Provider-/Packvertrag nutzen. Nicht mit VERIFY zusammenlegen.

**JEV-02:** Deterministisch lösbare Auswahl ohne Modellcall lösen. Unsichere semantische Entscheidung an Jev; bei unpassender Fähigkeit oder schwacher Evidenz an Hauptagent eskalieren. Jev erteilt keine Rechte und ersetzt keine Approval-Policy.

**VOICE-01:** Voice überwacht Zielabweichung, Wiederholungen ohne Fortschritt, ignorierte Fehler, unbelegten Abschluss und Ressourcenprobleme. Zuerst prüfen, ob Hauptagent den Fehler bereits sinnvoll bearbeitet. Dann schweigen statt redundanter Intervention.

**VOICE-02:** Ereignisgesteuert, kompakter Kontext, Cooldown und Deduplizierung pro Problem. Kein vollständiger Supervisor-Aufruf nach jedem Tool. Eingriff zunächst Hinweis; bei wichtigem unbehandeltem Problem pausieren und neu briefen. Pause muss dieselbe Runtimekontrolle benutzen.

**VOICE-03:** Rebrief nennt Befund, Evidence, betroffene Zielkriterien und konkreten nächsten Schritt. Kein vollständiges Neuschreiben des Goals, keine Rechteausweitung. Wiederaufnahme nach Voice-Pause nach dokumentierter Runtime-Regel; Nutzer-Stop bleibt ausschließlich nutzerfreigabepflichtig.

**VOICE-04:** Interventionen und Kosten messen: erkannte echte Probleme, unnötige Unterbrechungen, Zeitverlust, behobene Schleifen. Ziel ist weniger Fehlverhalten bei geringem Zusatzaufwand, nicht maximale Zahl von Warnungen.

## 15. Memory, Markdown und Kompaktierung

### 15.1 Dateiwissen

**MEM-01:** Große überschaubar gegliederte Markdown-Wissensbasis; Metadaten/Index durchsuchen statt Volltext bei jedem Run laden. Vorhandenen Knowledge-/Memory-Store weiterverwenden.

**MEM-02:** Projekt-AGENTS.md enthält Arbeitsregeln; SOUL.md Persönlichkeit und Nutzerpräferenzen. Weitere Dateien enthalten Fachwissen, Episoden, Entscheidungen, Fehlerwissen und Skillreferenzen. Globale und Projekt-Scopes trennen; relevante projektübergreifende Präferenzen gezielt laden.

**MEM-03:** Suche liefert ID, Titel, Typ, Scope, Revision, kurzen Treffer und Zeilenbereich. Lesen fordert begrenzten Ausschnitt an. Zusätzlich kann eine kurze Frage auf ausgewählte Dokumente beantwortet werden: Antwort mit Originalreferenzen, kein erneutes Einlesen der ganzen Datenbank.

**MEM-04:** Physische 1-basierte inklusive Zeilen, Snapshotkonsistenz, SHA-Revision, STALE_REFERENCE, Kürzungsmarker und Fortsetzung. Titel/Index nach Dateiänderung aktualisieren. Gültigkeit, Quellen, Konflikte, Korrektur und Löschung berücksichtigen.

**MEM-05:** Breites automatisches Erinnern ist gewünscht, aber gezielt und ressourcensparend. Relevante Fakten statt vollständiger privater Mailarchive duplizieren. Zugangsdaten bleiben im Broker. Ausgelagerte private Artefakte erhalten Zugriffsschutz und Aufbewahrungsregeln.

**MEM-06:** Vorgeschlagene Grenze: neue Erfahrungen automatisch speichern; grundlegende Regeln in AGENTS.md/SOUL.md nicht aus untrusted Inhalten überschreiben. Wesentliche Regeländerungen als nachvollziehbare Vorschläge führen. Das genaue automatische Änderungsrecht dieser beiden Dateien ist noch nicht ausdrücklich entschieden.

### 15.2 Kontext reduzieren, Originalbelege erhalten

**CTX-01:** Relevanz von Tool Calls, Ergebnissen und Arbeitszusammenfassungen anhand Goal und offenen Kriterien bewerten. Jev kann kurze Signale liefern; deterministische Budget-/Freshness-Regeln zuerst.

**CTX-02:** Große alte Toolverläufe aus aktivem Modellkontext entfernen; wichtige Ergebnisse kurz zusammenfassen, Quelle/Revision und ungelöste Fehler behalten. Primärtask separat im Goal, jüngste aktive Teilaufgaben im Arbeitszustand.

**CTX-03:** Originale bleiben über Artefakt-/History-Referenzen gezielt abrufbar innerhalb gültiger Aufbewahrung. Zusammenfassung ist kein Ersatz für aktuelle Datei-/GUI-Evidence. Keine unbelegte Behauptung „verlustfrei“, wenn Inhalt verdichtet wurde.

**CTX-04:** Keine ausstehenden Tool-Call/Result-Paare zerreißen; Providerverträge und gebundene Kontextblöcke beachten. Abbruch-/Approvalzustand, unknown Side Effects, Nutzerkorrekturen und Kriterien dürfen nicht verloren gehen.

**CTX-05:** Kontinuierlich begrenztes Kontextbudget mit Platz für nächste Antwort und Ergebnisse. Stabile Präfixe/Caching nutzen, sofern Provider unterstützt. Kompaktierung nicht so oft auslösen, dass sie mehr kostet als sie spart. Vorher/nachher Qualität und Gesamttokens messen.

## 16. Vollbild-TUI und später API

**UI-01:** Bestätigt: `skaldir` ohne Unterbefehl öffnet im interaktiven Terminal die Vollbild-TUI. Großer Chat links, Tools oben rechts, Goal unten rechts. Vorhandene Renderingbibliothek prüfen und erweitern; Ersatz nur bei belegter Grenze. Keine zweite Ausführungslogik im UI.

**UI-02:** Hauptbereich zeigt Gespräch/Antwortstream. Toolbereich zeigt Calls/Batches und tatsächliche Ergebnisse. Goalbereich zeigt Kriterien, offene Schritte, Status und Blocker. Desktopbesitzer, Queue und Approval-Modus bleiben sichtbar.

**UI-03:** Alle tatsächlichen Call-Events live verfolgen können; Batch-Ergebnisse kommen nach Fertigstellung mit stabiler Zuordnung. Wenn Nutzer zurückscrollt, Auto-Follow pausieren und neue Events zählen; bewusst zum Live-Ende zurückkehren. Keine Logflut, die Eingabe blockiert.

**UI-04:** Bestätigt: Tab bei leerem Prompt und geschlossenem Completion-Menü schaltet den Approval-Modus um; bei aktiver Completion bedient es Vorschläge. Panewechsel separat binden.

**UI-05:** Pflicht: roter Blockertext plus Symbol/Text, stabile Eingabe während Streaming, mehrzeiliges Paste, Details, Abbruch, Wiederanschließen und Größenänderung. ASCII, NO_COLOR und Plain-/JSONL-Modus außerhalb interaktiver TUI erhalten.

**UI-06:** Bestätigter Stil: dezente animierte Figur, gelegentlicher Humor, überwiegend monochrom und Fehler rot. Animationen stellen echte Zustände dar, keine erfundenen Gedanken oder Prozentwerte. Reasoning kann länger dauern; nutzerverständliche Arbeitszusammenfassungen streamen, keine Verpflichtung zur Anzeige verborgener interner Gedankengänge.

**UI-07:** Renderer verarbeitet begrenzten Eventstrom und paginierte Historie. Keine kompletten Gigabyte-Logs im Terminalspeicher. Control-Sequenzen aus Tooldaten nicht unkontrolliert ausgeben. Secret-Redaktion auch in Details und Export.

**UI-08:** Bestätigt: kurze Ergebnisvorschau pro Call, aufklappbare Argumente und vollständige Details; Hostindikator als kleine Statusleiste in einer Ecke. Panegrößen responsiv anpassen; weitere Layoutdetails anhand dieser Vorgaben entscheiden.

## 17. Geschwindigkeit und Observability

**PERF-01:** Pro Run/Phase/Rolle Modellzeit, Toolzeit, Wartedauer, erste sichtbare Rückmeldung, Tokens, Kosten soweit verfügbar, Fehler, Wiederholungen und vollständigen Erfolg erfassen. Jev/Voice/Worker/Builder gehören zum Gesamtbudget.

**PERF-02:** Baseline auf repräsentativen Aufgaben, kalt/warm unterscheiden. Mehrere Läufe, p50/p95 und Fallzahl angeben. Keine pauschalen „10×“-Claims aus einem Beispiel.

**PERF-03:** Optimierung zuerst gegen unnötige Schleifen und Dispatch-Umwege, dann Parallelität, Subtools, kleine Schemas, selektive Daten und Caches. Zuverlässigkeit darf nicht hinter Geschwindigkeit verschwinden.

**PERF-04:** Vorgeschlagene technische Zielwerte, vor Freigabe unter dokumentierter Last prüfen: lokale UI-Quittierung und Notstopp-Schedulerreaktion im Bereich unter 100 ms; keine feste End-to-End-Garantie für entfernte Prozesse. Notstopp-Zeit bis zur letzten unerwünschten Eingabe separat messen. Bei Nichterreichen Ursache dokumentieren und Ziel nicht still lockern.

**PERF-05:** Normale Tasks und lange Goals beide unterstützen. Zeit-/Kostenbudgets konfigurierbar, Default nicht unbemerkt unendlich; konkrete Nutzerlimits noch offen. Reales Weiterkommen wichtiger als bloße Schrittzahl.

## 18. Umsetzung in überprüfbaren Etappen

| Etappe | Umsetzung | Gate vor nächster Produktfreigabe |
|---|---|---|
| M0 | Audit, aktuelle Reproducer, Baseline, Architekturzuordnung | Bekannter aktiver Pfad und eindeutige Lücken |
| M1 | Core, Web, Dispatcher, echte Resultate, Batch/Cancellation | Coding und Discovery→Call real funktionsfähig |
| M2 | Runtimezustände, Approvals, Desktoplease, globaler Notstopp-Prototyp | Stop/Resume und Fedora-Machbarkeit nachgewiesen |
| M3 | Host-Computer-Use, Browserlebenszyklus, Vision, Eingaben | Browserrecherche ohne Fensterflut auf beiden Zielbackends |
| M4 | Subtool-Lernen, Skill-Lader, allgemeiner/Container-Builder | Getestete Fähigkeiten über normale Prompts wiederverwendbar |
| M5 | COMPOSE, Scheduler, Jev/Voice, Memory/Compaction | Workflow und Langlauf mit Recovery/Intervention |
| M6 | Vollbild-TUI-Polish und Performancevergleich | Bedienbare echte End-to-End-Demonstration |
| M7 | Stock-Dashboard, Video, Blender, Kreativ-Abnahme | Produktumfang mit Evidence bestätigt; Restgrenzen explizit |

Event-/TUI-Grundlage früh aufbauen; M6 ist die Fertigstellung, nicht der erste sichtbare Fortschritt. Große Etappen in kleine integrierte Changes teilen. Früh technische Blocker prüfen statt wochenlang auf unbewiesener Backendannahme entwickeln.

## 19. Tests, Migration und Abschluss

**QA-01:** Tests aus [02-ABNAHME.md](02-ABNAHME.md) umsetzen beziehungsweise vorhandenen zuordnen. Keine neue Testsuite, die nur Implementierungsdetails nachzeichnet.

**QA-02:** Einzelner Fix→gezielte Regression. Zusammenhängende Änderungen→Integration. Release→vorgeschriebene Projektchecks und reale Nutzerabläufe. Keine identischen breiten Testläufe parallel oder hinter flock aufstauen.

**QA-03:** Testlogs vollständig, Fortschritt sichtbar, echter Exitstatus erhalten. Keine alleinige `tail`-Pipeline als Nachweis. Fixtures kennzeichnen, nicht als Host-Livebeleg ausgeben.

**QA-04:** Nutzerdateien, Browserprofile und Kontoaktionen in Tests schützen. Für irreversible/finanzielle Testeffekte Fixtures oder ausdrücklich freigegebene Testumgebung; keine echten Käufe zur Wiederholungsprüfung.

**QA-05:** Neuer Code muss im normalen promptbasierten Einstieg erreichbar sein. Contract-Eintrag, Selftest und Slash-Palette zusätzlich prüfen, aber nicht mit Funktionserfolg verwechseln.

**QA-06:** Finaler Bericht pro Anforderung: Ist-Befund, tatsächliche Änderung, Testbefehl/-umgebung/-revision, Resultat und Restgrenze. Handoff, Migrations-/Rollbackhinweise und Performancevergleich beilegen. Kein globales „fertig“ für nicht erfüllte Pflichtabnahmen.

## 20. Ausdrücklich zurückgestellte Punkte

- Neues Framework oder vollständiger Rewrite ohne belegte Notwendigkeit.
- Mehrere codeändernde autonome Agenten im gemeinsamen Projekt.
- Zwang zu Vector-DB, Knowledge Graph oder permanentem kompletten Memoryprompt.
- Automatischer Hostwechsel oder Konto-/Cookie-Kopieren als Ersatz für Nutzerdesktop.
- Freie Selbstmodifikation von Runtime, Evaluation und Approvalgrenzen.
- Bezahlte Such-/Datenabos als zwingende Standardvoraussetzung.
- Vollständiger externer API-Server vor stabiler interner Runtime.

Kontrolliertes Skill-/Subtool-Lernen ist bereits Teil des Pflichtumfangs. Spätere RSI kann auf getrennten Aufgaben Prompt-/Routing-/Harness-Kandidaten evaluieren und versioniert aktivieren; sie ersetzt keinen der normalen Agentenpfade.
