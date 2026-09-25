# Skaldir — vollständiger Produkt- und Implementierungshandoff

Stand: 24. September 2026. Für MiMo und nachfolgende Implementierungssitzungen.

## 0. Verbindlicher Auftrag und Beleggrenze

Dieser Handoff präzisiert den Nutzerwunsch und ersetzt die zu enge Betrachtung der letzten Browserfehler. Skaldir soll ein vollständiger, schneller und angenehm bedienbarer Agent werden: Coding, Recherche, Dokumentarbeit, Linux-Computer-Use, wiederverwendbare Subtools, allgemeiner und Docker-basierter Tool-Bau, The Voice mit JEV sowie persistentes Wissen und Lernen.

**MiMo soll den aktuellen Code vergleichen, funktionierende Mechanismen erhalten, sinnvolle Lücken implementieren, im aktiven Nutzerpfad integrieren und passend testen.** Keine Neuimplementierung nur wegen anderer Namen. Unpassende technische Vorschläge darf MiMo begründet ersetzen; die gewünschte Fähigkeit bleibt zu erfüllen.

Diese Überarbeitung basiert auf dem vom Nutzer gelieferten MiMo-Handoff, den beobachteten Logs und den gemeinsam erarbeiteten Plänen. Der Skaldir-Quellcode wurde für diese Überarbeitung nicht unabhängig geprüft. Alle nachfolgend als „berichtet“ bezeichneten Codepfade, Tests, Ursachen und Statusangaben stammen aus dem gelieferten Handoff und müssen am aktuellen Arbeitsbaum bestätigt werden. Soll-Anforderungen sind keine Behauptungen über vorhandenen Code.

Nicht übernehmen: die pauschale Schlussaussage des alten Handoffs „Alle Angaben gegen den tatsächlichen Code verifiziert“. Ebenso wenig beweisen Tausende Tests die vollständige Integration aller Nutzerabläufe.

## 1. Was der Nutzer tatsächlich möchte

### 1.1 Produktversprechen

Der Nutzer gibt einen Auftrag. Skaldir findet die erforderlichen Fähigkeiten, beschafft fehlendes Wissen, führt die Arbeit aus, prüft den fachlichen Endzustand und liefert ein nutzbares Ergebnis. Bei einem neuen Problem soll es vorhandene Tools kombinieren oder ein fehlendes Tool bauen können. Der Nutzer soll nicht jede Funktionslücke durch weitere Prompts und manuelle Shell-Arbeit überbrücken müssen.

Beispiele:

- „Behebe diesen Fehler im Projekt“ → Code verstehen, Reproducer, gezielter Patch, passende Tests, nachvollziehbarer Diff.
- „Recherchiere Rechenzentren im All und schreibe ein Paper in LibreOffice“ → Quellen tatsächlich lesen, Aussagen belegen, Dokument erstellen, in LibreOffice prüfen und gespeichert übergeben.
- „Öffne Zen und recherchiere“ → Zen einmal öffnen beziehungsweise wiederverwenden, recherchieren, keine Fensterflut und keine falschen Beobachtungsbehauptungen.
- „Baue mir ein Docker-Tool für Aktien- und X-Recherche“ → Voraussetzungen prüfen, Tool erzeugen, bauen, testen, registrieren und tatsächlich aufrufen; fehlende externe Zugänge präzise ausweisen.
- „Erledige eine längere Aufgabe“ → Ziel über Kontextwechsel erhalten, überprüfbar vorankommen, bei Unterbrechung sicher fortsetzen.

### 1.2 Nicht verhandelbare Nutzerpräferenzen

- **Standard-Agent-Funktionen müssen zuerst verlässlich verfügbar sein.** Codeausführung darf nicht erst nach 26 Schreibschritten als fehlend auffallen.
- **Geschwindigkeit ist ein Produktmerkmal.** Weniger unnötige Modellrunden, keine reflexhaften Kompletttests nach jedem Edit, keine endlosen Schleifen.
- **Computer Use ist eine Kernfähigkeit mit hoher Priorität.** Vollständige Browser- und Desktop-Arbeit einschließlich Wahrnehmung, Maus, Tastatur und Verifikation.
- **Subtools sollen alltägliche Arbeit sehr schnell erledigen.** Bewährte Abläufe brauchen nicht für jeden Klick einen neuen großen Modellaufruf.
- **Tool Builder und Docker Tool Builder müssen tatsächlich nutzbar sein.** Dateigenerierung allein ist kein fertiges Tool.
- **The Voice mit JEV ist eine prioritäre integrierte Kontrollinstanz.** Sie soll Abweichungen erkennen und wirksam korrigieren, ohne jeden Schritt teuer zu kommentieren.
- **Ruhige Hintergrundarbeit:** vorhandene Sitzungen wiederverwenden; keine ungewollten Fenster, Fokuswechsel oder Eingaben in Nutzeranwendungen.
- **Polierte CLI:** erkennbarer Skaldir-Stil, dezenter Humor, klare Zustände, strukturierte Ergebnisse und abrufbare Details.
- **Memory und Informationszugriff:** kleine versionierte Markdown-Dateien bei Bedarf wie Tools abfragen; konkrete Zeilen statt ständig die gesamte Wissensbasis laden.
- **Lernen und RSI:** Fehlerwissen und getestete Skills verbessern die Arbeit; weitergehende Selbstoptimierung benötigt stabile Grundlagen und unabhängige Bewertung.

„Komplett“ bedeutet hier vollständige Ende-zu-Ende-Fähigkeiten in ausdrücklich unterstützten Umgebungen. Es bedeutet keine unbelegte Behauptung, jede Linux-Desktopvariante oder Anwendung funktioniere bereits.

## 2. Prioritäten und Arbeitsreihenfolge

Die folgende Reihenfolge respektiert die hohen Produktprioritäten und ihre technischen Abhängigkeiten. Memory und UI werden nicht bis nach experimenteller RSI verschoben.

| Stufe | Inhalt | Warum jetzt? | Abschlussbedingung |
|---|---|---|---|
| P0-A | Standard-Agent-Kern, echter Executor, Discovery→Dispatch, unverfälschte Ergebnisse | Grundlage aller weiteren Fähigkeiten | Coding-Auftrag und Tool-Aufruf funktionieren über die echte CLI |
| P0-B | Computer Use vollständig; Browserlebenszyklus, Beobachtung, Eingabe, Recovery | Höchste sichtbare Produktlücke; Fensterflut verhindern | Browser- und LibreOffice-Ablauf bestehen mit passenden Belegen |
| P0-C | Schnelle Subtools und COMPOSE | Wiederholte GUI-/Tool-Arbeit zuverlässig beschleunigen | Wiederverwendeter Ablauf spart Modellrunden bei gleicher Erfolgsqualität |
| P0-D | Allgemeiner Tool Builder und Docker Tool Builder | Fehlende Fähigkeiten eigenständig ergänzen | Build→Test→Registry→echter Aufruf einschließlich Wiederverwendung |
| P0-E | The Voice + JEV im aktiven Loop | Schleifen und falsche Fertigmeldungen verhindern | Nachweisbarer Eingriff im Fehlerfall; geringer Leerlaufaufwand |
| P1-A | Geschwindigkeit und Tokenökonomie über alle P0-Pfade | Muss bei jedem Ausbau mitgemessen werden | Baseline und Vergleich je Nutzerablauf; keine versteckte Qualitätsverschlechterung |
| P1-B | CLI-UI und UX-Polish | Zustände, Fehler und Ergebnisse verständlich machen | CLI-01 bis CLI-12 erfüllt, echte Terminalprüfung |
| P1-C | Memory, Knowledge, Kontextkontinuität, Skill-Lernen | Arbeit über Sitzungen hinweg verbessern | Relevante Erinnerung korrekt abrufbar; Quellen und offene Aufgaben erhalten |
| P2 | Kontrollierte Prompt-/Harness-Optimierung und RSI | Erst auf verlässlicher Evaluation sinnvoll | Kandidaten separat getestet, Rückweg vorhanden, kein Verlust von Kontrollgrenzen |

P0-B bis P0-E sind **alle hoch priorisiert**, keine optionalen Forschungsfeatures. Die Buchstaben bilden Abhängigkeiten ab, nicht eine Aufforderung, Voice oder Tool Builder auf unbestimmte Zeit zu vertagen. Gemeinsame Dispatcher-, Event- und Zustandsverträge einmal implementieren und in allen Pfaden verwenden. UI-Lesbarkeit und Messinstrumentierung können parallel zur jeweiligen Funktionsreparatur verbessert werden.

## 3. Gemeldeter Ist-Zustand: erhalten, aber erneut prüfen

### 3.1 Repository und historische Evidenz

| Angabe aus dem gelieferten Handoff | Einordnung für die nächste Sitzung |
|---|---|
| Repository `/home/willy/projects/gods-voice` | Gemeldeter Projektpfad; lokal bestätigen |
| Branch `godsv-supervisor-hardening`, HEAD `a8fcaa1` | Historischer Stand; aktuellen HEAD und Arbeitsbaum lesen |
| 166 geänderte Dateien, +34.724 / −5.969 Zeilen | Umfang laut altem Handoff; Herkunft und aktuellen Diff erneut bestimmen |
| `/tmp/gv-bundled-tests.log`: 234 passed | Berichteter gezielter Lauf nach P0-Änderungen; Log, Umfang und getesteten Stand prüfen |
| 7530 passed / 31 skipped / 0 failed | Früherer Fullsuite-Lauf vor letzten Fixes; keine aktuelle Komplettabnahme |
| 7527 passed / 31 skipped / 3 failed | Anderer historischer Lauf; Behebung und tatsächlichen Wiederholungslauf zuordnen |
| Memory prec@1 0.9231 | Gemeldete Einzelmetrik ohne hier verfügbares vollständiges Versuchsprotokoll; keine allgemeine Qualitätsgarantie |
| PID 763718, Test-Lock „frei“ | Veraltet sein können: nicht als aktuellen Zustand oder stabile Prozessidentität verwenden |

Der alte Handoff beschreibt den uncommitted Umfang einmal als „alle Sitzungen dieses Branches“, später als „alle Branches“. Diese Zuordnung ist ungeklärt. Nicht fremde Änderungen als eigene ausgeben.

### 3.2 Architektur laut Handoff

Gemeldete Layer: EXPAND → ACT → COMPOSE → ROUTE → SUPERVISE → VERIFY → ENFORCE → PERSIST. Diese Namen sind Orientierung, kein Anlass, eine zweite gleichnamige Architektur aufzubauen.

| Bereich | Gemeldete Dateien / Symbole | Zu bestätigender aktiver Pfad |
|---|---|---|
| Einstieg | `src/skaldir/cli.py:main`, `agent_loop_cli.py:cmd_run`, `repl.py:Shell` | Nutzerauftrag erreicht aktuellen Agent-Loop |
| Wrapper | `/home/willy/.local/bin/skaldir` | Verwendet wirklich gewünschte venv und diesen Quellstand |
| Hauptagent | `agent_loop.py`, `AgentTool`, `ToolSet`, `_execute`, `_run_one` | Tool-Schema, Provider, Dispatch und Rückgabe konsistent |
| Layer-Tools | `agent_toolset.py` | Sichtbarkeit, Registrierung und erlaubte Aufrufwege |
| ACT | `act_service.py:run_action_list`, `check_end_state`, `action_error` | Aktion und Verifikation korrekt getrennt |
| Executor | `computer_use.py`, `CURunResult`, `ActionResult`, `LocalSandboxRunner`, `ContainerRunner` | Tatsächliche stdout/stderr/Exitcodes, Abbruch und Nebenwirkungen |
| Linux-Desktop | `cu_host_session.py`, `cu_platform.py` | Aktive Sitzung, echtes Backend, Beobachtung und Eingabe |
| Knowledge | `knowledge.py`, `knowledge_cli.py` | Markdown/FTS5/Zeilenabruf durch echte AgentTools und CLI |
| Kontext | `ledger.py`, `tool_archive.py` | Quellenreferenzen, offene Aktionen, Wiederaufnahme |
| Memory/Skills | `memory_store.py`, `skills.py` | Retrieval, Konsolidierung, Promotion und Nutzung |
| Laufzustand | `runs_store.py`, `events.py` | Goal, Kriterien, Evidence und Ereignisse tatsächlich verknüpft |
| Modelle | `llm_provider.py` | Fähigkeiten korrekt, Konfiguration aktiv, Accounting verfügbar |
| Worker | `subagents.py`, `subagent_roles.py` | Delegation, gemeinsames Budget, Integration und Abbruch |
| CLI-UI | `chatbar.py`, `agent_loop_cli.py` | Darstellung echter Zustände, kein eigener Wahrheitsbestand |
| Builder | `toolbuilder.py`, `docker_ensure_cli.py`, `skill_expand_build` | Konfigurierter Client, Executor, Test und Registry |

Für The Voice und JEV nennt der Handoff keine hinreichend präzisen aktiven Codepfade. Diese zuerst suchen und vom Trigger bis zum tatsächlichen Eingriff beziehungsweise Abschluss-Gate verfolgen. Nicht aus dem Namen SUPERVISE oder VERIFY ableiten, dass die Integration erledigt ist.

### 3.3 Berichtete Änderungen dieser MiMo-Runde

- Knowledge-Store mit Markdown, SQLite FTS5, Typ/Scope/Status/Herkunft, Hashrevision, Freshness und `STALE_REFERENCE`; gemeldet: 23 Tests.
- `knowledge list/show/search/add` und Contract-Registrierung.
- `tool_search` mit `input_schema`, `knowledge_search`, `knowledge_read` als AgentTools.
- `evidence_refs` und `knowledge_refs` im Ledger; Read-Dedup in `_execute` pro Step.
- `end_state.channel="stdout"`, zusätzliches `CURunResult.stdout`, Weitergabe von `outcome.stdout`; drei gemeldete Regressionstests.
- ACT-Schema mit `window_title`, `stdout`, `app_state` und Nutzungshinweisen.
- HTTP-`api_call` soll `payload.url` statt `argv` verlangen.
- Chatdarstellung, Cold-Start-Zahlen und Lazy-Import-Verhalten geändert.
- Live-Desktop-Opt-in laut Änderungsbericht von Switch+Target auf Switch allein angepasst. Der Umgebungsabschnitt behauptet weiterhin Switch+Target: Widerspruch am Code und am tatsächlich autorisierten Produktverhalten klären, nicht still eine Seite übernehmen.
- Audit-, Entscheidungs- und Validierungsdateien unter `docs/skaldir-upgrade/` gemeldet.

**Status:** Berichtete Implementierungen und Tests, keine durch dieses Dokument bestätigte Live-Abnahme. Schemafelder, Registryeinträge und Warntexte allein beheben keine fehlende Laufzeitverbindung.

## 4. Standard-Agent-Funktionen: vollständige Grundlage

Übernehme die 24 Anforderungen BASE-01 bis BASE-24 aus [09-CODING-AGENT-GRUNDLAGEN.md](09-CODING-AGENT-GRUNDLAGEN.md). Besonders wichtig:

- Auftrag, Scope und Fertigkriterien erfassen; Nutzerkorrekturen im laufenden Run berücksichtigen.
- Repo-Regeln, aktive Pakete, Tests und vorhandene Änderungen verstehen.
- Dateien finden, begrenzt und versionskorrekt lesen, zielgerichtet ändern; fremde Änderungen erhalten.
- Code und Befehle tatsächlich ausführen: cwd, Laufzeitumgebung, stdout, stderr, Exitcode, Timeout und Abbruch.
- Tool-Call-Ergebnisse korrekt zuordnen; aus Fehlern nächste Schritte ableiten.
- Build-/Import-/Testfehler reproduzieren und beheben; keine unbelegte Fertigmeldung.
- Aktuelle Dokumentation gezielt abrufen, wenn lokale Information nicht reicht.
- Ziele, offene Aktionen und Evidence über Kontextwechsel und Neustarts erhalten.
- Funktionsfähigkeit und Grenzen früh anzeigen; keine 26 Schritte Codegenerierung ohne erreichbaren Executor.

„Immer Code ausführen können“ bedeutet im Produkt: Ein normaler autorisierter Coding-Auftrag hat einen funktionierenden, angemessen begrenzten Executor. Es bedeutet nicht, Berechtigungen zu umgehen oder eine nicht vorhandene Umgebung als funktionsfähig darzustellen.

**Abnahme:** Ein echter Bugfix und eine Änderung über mehrere Dateien aus der normalen CLI heraus, mit vorher/nachher geprüftem Verhalten und nachvollziehbarem Diff.

## 5. HIGH PRIORITY: Computer Use vollständig und störungsarm

### 5.1 Drei ausdrücklich unterscheidbare Ausführungsmodi

| Modus | Zweck | Verhalten |
|---|---|---|
| Hintergrund über APIs/DOM/CLI | Recherche und strukturierte Arbeit ohne nötige Sichtbarkeit | Schnell, keine Eingriffe in den Nutzerdesktop |
| Isolierter Desktop/Browser | Visuelle Arbeit ohne Störung der laufenden Nutzersitzung | Eigene Sitzung und eigener Zustand; auf Wunsch einsehbar |
| Aktiver Nutzerdesktop | Explizit gewünschte App, bestehende Sitzung oder zugängliche Nutzerdatei | Aktuelle Berechtigung, eindeutiges Ziel, Fokus-/Eingabekontrolle |

Backend-Wahl am Auftrag und an verfügbaren Fähigkeiten ausrichten. „Öffne Zen“ respektiert Zen; ein isolierter Ersatzbrowser ist kein stillschweigender Erfolg. Headless oder Xvfb ist kein Beweis für korrektes Verhalten auf dem aktiven Wayland-Desktop. Ein verstecktes Fenster schützt nicht vor globaler Eingabe.

### 5.2 Vollständiger Funktionsumfang

1. Umgebung erkennen: Desktop-Sitzung, Backend, unterstützte Beobachtung/Eingabe, Skalierung, Monitore und relevante Zugriffsgrenzen.
2. Anwendungen zuverlässig finden, öffnen, wiederverwenden, fokussieren nur wenn nötig und kontrolliert schließen.
3. Fenster und Oberfläche beobachten: strukturierte App-/Accessibility-Daten, Screenshots und visuelle Lokalisierung, jeweils mit Aktualität und Zielbindung.
4. Maus: bewegen, klicken, doppelklicken, Rechtsklick, Scrollen, Drag-and-drop; Koordinaten müssen zur aktuellen Beobachtung gehören.
5. Tastatur: Tasten, Kombinationen, Text und mehrzeilige Eingabe; Sonderzeichen und Layout berücksichtigen.
6. Browser: Tabs/Sitzungen verwalten, navigieren, Seite lesen, Formulare bedienen, Downloads/Uploads und relevante Dialoge bewältigen.
7. Office/Dateien: Dokument öffnen/erzeugen, Inhalte und Format bearbeiten, Speichern/Export, korrekten Pfad und lesbares Ergebnis prüfen.
8. Veränderungen erkennen: Ladezustände, Modals, Fokuswechsel, verschobene Fenster, Fehlerseiten und unerwartete Appzustände.
9. Wiederaufnahme und Abbruch: keine losgelösten Eingaben oder verspäteten Starts nach Nutzerstopp.
10. Fachliche Verifikation: richtige Seite, richtiger Text, richtig gespeichertes Dokument; kein Erfolg nur wegen Fenstername oder Exit 0.

X11-Werkzeuge nicht pauschal als Lösung für Wayland einsetzen. Backend-spezifische Verträge und tatsächliche Unterstützung prüfen; vollständige Linux-Unterstützung als explizite Matrix dokumentieren.

### 5.3 Browserlebenszyklus: Fensterflut dauerhaft verhindern

Die Nutzerbeobachtung von 12+ Zen-Fenstern hat Vorrang vor der unbelegten Agentenbehauptung, kein Fenster sei geöffnet worden. Wiederholte Startversuche sind im Verlauf sichtbar; die genaue technische Ursache des Beobachtungsfehlers ist weiterhin zu prüfen.

Ein zentraler Browser-/Sitzungsmanager benötigt:

- Session-/Profil-/Zielzuordnung und bekannte Besitzverhältnisse.
- Zusammenführung paralleler Startanforderungen für dieselbe Sitzung.
- Zustände wie `absent`, `starting`, `ready`, `unconfirmed`, `failed`, `closing`.
- Wiederverwendung statt erneutem Start; begrenzte Bereitschaftsprüfung.
- Reconciliation nach Timeout oder Prozessverlust: fehlende Evidence löst keinen blinden Neustart aus.
- Legitimer Neustart nach bestätigtem Fehlschlag; kein starrer ewiger „bereits gestartet“-Marker.
- Gleiche Regeln für ACT, Subtools, direkte Launcher und COMPOSE. Keine bloße Erkennung eines einzelnen Befehlsstrings.
- Keine fremden Browserfenster schließen; vom Nutzer ausdrücklich gewünschtes Fenster bei Abschluss erhalten.

Ein 30-Sekunden-Flow-Lock allein genügt nicht: Die Browser-Sitzung kann länger existieren, und leicht veränderte Argumente dürfen keine neue Startschleife erzeugen.

### 5.4 Computer-Use-Abnahme

- Zen vorhanden → wiederverwenden; Zen fehlt → höchstens ein nötiger Start.
- Beobachtung fällt nach Start aus → unbestätigt melden und diagnostizieren, keine Fensterflut.
- Tatsächliche Navigation, Klick, Texteingabe, Scroll und Drag auf unterstütztem Backend geprüft.
- Nutzer bedient andere Anwendung → Hintergrundmodus verursacht keine Fokus-/Eingabekollision.
- LibreOffice-Dokument mit Rechercheinhalt erstellen, speichern, erneut öffnen und Inhalt prüfen.
- Nach einem Nutzerabbruch keine neuen Aktionen; eigene temporäre Ressourcen sauber behandeln.

## 6. HIGH PRIORITY: schnelle Subtools und COMPOSE

### 6.1 Begriffe sauber trennen

- **Tool:** eine registrierte Fähigkeit mit Schema, Ausführungsvertrag und Ergebnis.
- **Subtool:** ein kleiner, zielgerichteter wiederverwendbarer Ablauf, häufig aus mehreren elementaren Aktionen.
- **Composite/Flow:** explizite Zusammensetzung mit Abhängigkeiten, Übergaben und Prüfpunkten.
- **Subagent:** eigener Modellkontext für eine eigenständige Teilaufgabe; nicht nötig für jeden Subtool-Aufruf.

Die lokalen Namen können abweichen. Für diese Produktanforderung bedeutet „Ultrafast Subtools“: bewährte Abläufe mit wenig unnötiger Inferenz und kurzer Werkzeuglatenz ausführen, ohne Beobachtung, Fehlerbehandlung oder Evidenz zu verlieren.

### 6.2 Ausführbare Subtools statt bloßer Promptvorlagen

Geeignete erste Kandidaten, an vorhandene Registry anpassen:

- Browser sicher bereitstellen und bekannten Tab wiederverwenden.
- URL öffnen, Bereitschaft prüfen und lesbaren Inhalt liefern.
- Element finden und klicken; Feld gezielt befüllen und Ergebnis prüfen.
- In-App-Suche oder verifizierten Speicherdialog ausführen.
- Quellen parallel abrufen, deterministisch extrahieren und belegt zusammenfassen lassen.
- Dokument speichern/exportieren und Datei-/Inhaltsprüfung durchführen.

Jeder Vertrag enthält ID/Version, Input-/Output-Schema, unterstützte Backends, Preconditions, Wirkung, Rechte, Timeout, Abbruch, Evidenz, Wiederholungsregeln und Fehlerklassen. Der Aufrufer erhält brauchbare Resultate statt nur `verified`.

### 6.3 Schneller Pfad mit kontrolliertem Fallback

- Bekannter Zustand und getesteter Ablauf → direkt deterministisch ausführen.
- Wartebedingungen am App-/DOM-/Prozesszustand orientieren, nicht feste lange Sleeps aneinanderreihen.
- Unabhängige Leseoperationen begrenzt parallelisieren; Eingaben in dieselbe Sitzung sequenziell.
- Beobachtung an entscheidenden Übergängen und finaler Postcondition; keine unkontrollierte Kette alter Koordinaten.
- Bei geänderter UI oder fehlschlagender Precondition neu beobachten und gezielt neu planen.
- Caches nach Revision, Sitzung, Berechtigung und Freshness invalidieren.
- Laufende Sessions oder Executor-Warmstarts nur dort wiederverwenden, wo Zustand und Isolation definiert sind.

**Abnahme:** Derselbe repräsentative Ablauf mit und ohne Subtool wird verglichen: vollständiger Erfolg, Modellrunden, p50/p95-Latenz, Tokens und Recovery. Kein Geschwindigkeitsgewinn durch Weglassen der Prüfung.

## 7. HIGH PRIORITY: allgemeiner Tool Builder

### 7.1 Erwarteter Lebenszyklus

Bedarf erkennen → vorhandene Tools suchen → fehlende Fähigkeit präzisieren → Kandidat erzeugen → statisch prüfen → isoliert ausführen → funktional testen → versionieren/registrieren → Discovery und Aufruf testen → bei Drift reparieren oder deaktivieren.

Vorhandene CLI-, HTTP-, Python-/Code- oder MCP-Integrationen je nach Aufgabe wiederverwenden. Kein Pflicht-Docker für jedes kleine Tool. Ein `skill_expand`-Erfolg ist nur dann „Tool verfügbar“, wenn ein echter anschließender Aufruf funktioniert.

### 7.2 Konkrete Integrationslücken aus dem Verlauf

- `ToolBuilder(llm=...)` war laut Log nicht konfiguriert, obwohl der Hauptagent einen Provider hat. Dependency Injection und tatsächlichen Builderpfad prüfen; kein zweiter versteckter Provider mit eigener Kostenrechnung.
- `skill_compose` bekam unbekannte Felder und später `end_state="always"`. Schema vor Ausführung bereitstellen; ein stets wahrer Marker ist keine fachliche Abnahme.
- Registryeinträge ohne aktiven Aufrufweg reparieren. Tool-Suche muss Version, Vertrag, Verfügbarkeit und nutzbaren Dispatcherweg liefern.
- Codegenerierung und Ausführung gehören zum selben überprüfbaren Auftrag. Dateien schreiben und Testscript erzeugen reichen nicht.

### 7.3 Abnahme

Ein kleines fehlendes Tool aus einer natürlichen Anfrage bauen, mit gültigen und ungültigen Eingaben testen, registrieren, im selben und in einem neuen Run finden und tatsächlich nutzen. Bei vorhandenem gleichwertigem Tool wird es wiederverwendet. Fehlerhafte Kandidaten werden nicht als produktionsbereit ausgegeben.

## 8. HIGH PRIORITY: Docker Tool Builder

Der Docker-Pfad ist eine konkrete produktive Ausführungsoption des Tool Builders, kein separater unverbundener Agent.

- Docker-/Podman-Verfügbarkeit und die tatsächlich unterstützte Engine früh prüfen. Ein vorhandener Podman-Pfad muss nicht allein wegen des Begriffs Docker ersetzt werden; Kompatibilität und Nutzeranforderung offen darstellen.
- Buildkontext, Entry Point, Input/Output, Laufzeitabhängigkeiten und relevante Versionsbindung definieren.
- Image bauen, Build-Log und Exitstatus erhalten, Imageversion/Digest nachvollziehbar zuordnen.
- Container-Smoke-Test plus fachliche Fixture-Prüfung ausführen; Start allein genügt nicht.
- Netzwerk, Mounts, Ressourcen, Nutzer, Credentials und Ausgabeordner passend zum Auftrag definieren. Keine Secrets in Image/Code/Build-Logs einbetten.
- Container-Tool mit Schema und Runtime-Anforderungen registrieren; aus der normalen Agentenschleife aufrufen.
- Reproduzierbare Builds und Caches verwenden, soweit sinnvoll; nicht bei jedem Aufruf unveränderte Images neu bauen.
- Prozess-/Container-Abbruch, temporäre Ressourcen und unbekannte Seiteneffekte sauber behandeln.
- Providerfehler, fehlende API-Zugänge oder Rate Limits als konkrete Teilgrenzen melden.

**Abnahme:** Ein netzwerkfreies Fixture-Tool zuerst bauen und nutzen. Danach Stock-/Recherche-Beispiel mit kontrollierten Daten und, soweit verfügbar, echten Zugängen prüfen. X-Sentiment per Schlüsselwortzählung ehrlich als Heuristik kennzeichnen; es ersetzt keine tiefe Recherche. Ein fehlender X-Zugang muss nicht lokale Tests oder den Stock-Datenpfad blockieren.

## 9. HIGH PRIORITY: The Voice mit JEV

### 9.1 Gewünschte Rolle

The Voice ist hier die begleitende Supervision des Agenten, nicht bloß Text-to-Speech und keine dekorative innere Stimme. Eine zusätzliche Audiooberfläche kann später bestehen, erfüllt diesen Auftrag aber nicht.

Sie soll erkennen, ob Skaldir noch am Nutzerziel arbeitet, Fortschritt macht, wiederholt Fehler reproduziert oder einen unbelegten Abschluss vorbereitet. JEV bindet die Bewertung an Kriterien und tatsächliche Evidence. Die vorhandene Bedeutung/Implementierung von JEV im Repository übernehmen; keine Abkürzungsdefinition erfinden.

### 9.2 Zuständigkeiten

| Komponente | Verantwortung |
|---|---|
| Hauptagent | Plant und führt aus; berücksichtigt Nutzersteuerung |
| The Voice | Erkennt Abweichungen, fordert gezielte Prüfung oder Umplanung an |
| JEV / Verifier | Prüft Kriterien gegen geeignete, aktuelle Belege; bestätigt oder widerspricht |
| Runtime / Policy | Setzt Abbruch, Rechte, Ausführungszustand und verbindliche Abschlussregeln durch |

Voice-Kommentare allein zählen nicht als Integration. Eine berechtigte Intervention muss den nächsten Schritt oder den Abschlussstatus tatsächlich beeinflussen. Voice darf Nutzerziele, Rechte oder Budgets nicht eigenmächtig erweitern.

### 9.3 Trigger und Rückkopplung

- Wiederholter Browserstart ohne Zustandsgewinn → weitere Starts im gemeinsamen Runtimepfad unterbinden, Zustand neu erheben.
- Mehrfach dieselbe Schema-/Dispatch-Fehlerklasse → gültigen Vertrag laden oder Routing reparieren; keine zufälligen Varianten.
- Code wird nur geschrieben, Ausführung fehlt → Capability-/Testlücke sichtbar machen und passenden Executor verwenden.
- Abschluss geplant, notwendige Evidence fehlt → unbestätigt/teilweise, gezielte Verifikation anstoßen.
- Quellenbehauptung ohne gelesene Quelle → Recherchebeleg nachladen.
- Lange Phase ohne Fortschritt oder knappes Budget → kleinere nächste Schritte, zulässige Eskalation oder präziser Blocker.
- Nutzer korrigiert Ziel → laufende Planung und Kriterien konsistent aktualisieren.

Voice erhält ein kleines Zustandspaket: Goal-Revision, aktuelle Phase, letzte relevante Events, Fehlergruppen, offene Kriterien und Evidence-Referenzen. Details nur bei Bedarf abrufen. Deterministische Wächter zuerst; Modellaufrufe ereignisgetrieben, mit Cooldown und eigenem Anteil am gemeinsamen Run-Budget. Keine teure zweite Vollkonversation nach jedem Tool.

### 9.4 Evidence-Verträge

Belegtyp, Herkunft, Zeitpunkt und relevante Revision erhalten: z. B. Testlauf auf Codezustand, Screenshot einer Sitzung, gespeicherte Dokumentversion oder HTTP-Antwort. Unpassende oder alte Evidence nicht als aktuellen Erfolg verwenden.

Prozess vorhanden ≠ Fenster sichtbar ≠ richtige Seite geladen. Dokumentdatei vorhanden ≠ lesbares Paper mit Quellen. Tool registriert ≠ Tool aufrufbar. JEV muss diese Unterschiede im aktiven Abschlussweg erhalten.

**Abnahme:** Fehler-Replays mit/ohne Voice/JEV vergleichen; weniger Schleifen und falsche Abschlüsse, keine unnötige Blockade einfacher erfolgreicher Tasks. Eingriff, Wirkung und zusätzliche Tokens/Latenz erfassen.

## 10. Geschwindigkeit und Token-Effizienz

### 10.1 Messung statt „ultrafast“ als Behauptung

Pro repräsentativer Aufgabe Baseline erfassen: vollständiger Erfolg, Gesamtzeit, Zeit bis zur ersten sinnvollen Aktion, Modellrunden, Providerzeit, Toolzeit, Wartezeit, Tokens je Rolle, Cache-Nutzung, Kosten soweit verfügbar und notwendige Nutzereingriffe.

Hauptagent, Voice, JEV, Builder, Subagents und Memory-Jobs gehören in dieselbe Gesamtbilanz. Fehlende Usage ist unbekannt, nicht null. Werte nach Maschine, Modell, Backend, warmem/kaltem Start und Aufgabentyp trennen. Für Latenzvergleiche mehrere Wiederholungen und p50/p95 verwenden; Stichprobengröße angeben.

### 10.2 Optimierungsreihenfolge

1. Fehler- und Wiederholungsschleifen beseitigen; größte Verschwendung zuerst.
2. Tool-Aufrufpfade direkt erreichbar machen; keine Suchrunde für bereits bekannte Grundfähigkeiten.
3. Kleine Schemas und gezielte Ergebnisse statt Gesamtkatalog und riesigen Rohlogs laden.
4. Bewährte Subtools und deterministische Datenverarbeitung einsetzen.
5. Unabhängige Lese-/Recherchearbeit begrenzt parallelisieren.
6. Gültige Read-/Build-/Session-Caches nutzen; Änderungen und Rechte beachten.
7. Kontext gezielt zusammenstellen und alte Daten auslagern; wichtige Fehler und offene Kriterien erhalten.
8. Modellrouting nur mit nachgewiesener Eignung; schwierige Fälle gezielt eskalieren.
9. Testauswahl auf Änderungen beziehen; breite Checks am Integrationsabschluss.

Keine pauschale Prozentgarantie festschreiben, bevor die Baseline existiert. Ziele je Ablauf danach festlegen. Vollständiger Erfolg und unveränderte Eingriffsgrenzen bleiben Abnahmebedingungen.

## 11. UI-Polish: Skaldir erkennbar und verständlich

Implementiere [10-CLI-UI-UX-UPGRADE.md](10-CLI-UI-UX-UPGRADE.md), einschließlich CLI-01 bis CLI-12.

- Figur als wiedererkennbares Markenzeichen, einmaliger kompakter Start.
- `calm`, `skald`, `plain`; humorvolle Warteakzente optional und sparsam.
- Eine dezente Animation für aktive Arbeit, kein Log-Spam und keine erfundene Prozentanzeige.
- Übersichtlicher Auftrag, aktuelle Aktivität, wenige wichtige Ereignisse und Ergebnis.
- Aufklappbare Argumente, echte stdout/stderr, Exitstatus und vollständige Artefaktreferenzen.
- `FAILED ALLOW` durch verständliche getrennte Ausführungs-/Prüfzustände ersetzen.
- `created:false` als erfolgreiche Aktualisierung darstellen, wenn der Schreibvertrag Erfolg meldet.
- Keine dauerhaften leeren Statuswerte wie `goal:none`; unbekannte Messwerte in Details ehrlich kennzeichnen.
- Eingabe während Arbeit, Paste, Stop, Resume und Korrekturen funktionieren ohne Cursorchaos.
- Narrow terminal, Resize, ASCII, Motion-off und JSONL/Pipe-Modus unterstützen.
- Abschluss zeigt geprüft, teilweise, blockiert oder abgebrochen statt pauschal „done“.

Polish umfasst Information und Bedienung, nicht nur neue Farben und Spinner. Backenddefekte dürfen nicht durch freundlichere Texte unsichtbar werden.

## 12. Memory, Knowledge und Kontext

### 12.1 Informationen wie Tools nutzen

Die zentrale Nutzeridee bleibt: Wissen in überschaubaren Markdown-Dateien; suchen, relevante Treffer auswählen und konkrete Zeilenbereiche lesen. Kein dauerhafter Vollimport aller Notizen in den Systemprompt.

- Stabile Dokument-ID, Typ, Scope, Status, Herkunft, Revision und gegebenenfalls Gültigkeit.
- `search` liefert kurze Treffer und referenzierbare Bereiche.
- `read` liefert exakte physische 1-basierte inklusive Zeilen mit passender Revision.
- Geänderte Datei → `STALE_REFERENCE` beziehungsweise expliziter Neuabruf; keine still falschen Belege.
- Begrenzte Ausgabe und klare Fortsetzung; überlange Zeilen, Unicode, CRLF, Leerdateien und historische Versionen definiert behandeln.
- Markdown als lesbarer Wissensbestand; vorhandene FTS-/Datenbankindizes als abgeleitete Suche. Keine konkurrierenden Wahrheiten erzeugen.

### 12.2 Gedächtnisarten und Lebenszyklus

Arbeitszustand, Episoden, verifizierte Fakten, Nutzerpräferenzen, Entscheidungen, Fehlermuster und getestete Skills getrennt behandeln. Nutzeranweisung, fremdes Dokument und Agentenhypothese haben unterschiedliche Autorität.

Neue Erinnerung mit Quelle und Scope erfassen; Duplikate konsolidieren, Widersprüche und zeitliche Gültigkeit behandeln, veraltete Inhalte markieren. Korrektur und Löschung müssen auch abgeleitete Indizes und Zusammenfassungen erreichen. Keine Secrets als Memory speichern.

Fehlerwissen soll konkret helfen: „Fenster nicht beobachtet ist kein Beweis für gescheiterten Start“ kann ein geprüftes allgemeines Muster werden. Ein einmaliger DNS-Fehler darf nicht zum dauerhaften Fakt „dieser Host hat kein Internet“ werden.

### 12.3 Kontinuität

Nach Kompaktierung oder Resume müssen Goal-Revision, offene Kriterien, ungeklärte Nebenwirkungen, aktuelle Artefakte, nächste Schritte und Evidence-Referenzen erhalten sein. Ledger-Felder allein reichen nicht: Schreib-/Lese-/Wiederaufnahmepfad testen.

**Abnahme:** Eine später korrigierte Information in neuer Sitzung richtig abrufen; alte Revision nicht als aktuell verwenden; offenen Auftrag nach Kontextwechsel ohne Zielverlust fortsetzen. Genauigkeit und Effizienz mit tatsächlichen Skaldir-Aufgaben vergleichen.

## 13. Lernen, Skills und kontrollierte RSI

Erst funktionierende Arbeit, dann sinnvolle Erfahrungen extrahieren. Skills/Subtools durchlaufen Kandidat → Test → aktive Version → Überwachung → Reparatur/Rollback. Nicht jede erfolgreiche Antwort wird ein Skill.

Spätere Optimierung betrifft begrenzte Prompts, Retrieval, Routing, Subtools oder Harness-Komponenten. Kandidaten isoliert bearbeiten, auf getrennten Aufgaben evaluieren, Kosten und Komplexität einbeziehen und nur bei belastbarem Nutzen aktivieren.

Keine freie Veränderung des laufenden Systems, der Evaluationskriterien oder der Rechte durch einen Optimierer. Eigene Modellgewichte zu trainieren ist keine Voraussetzung für dieses Produktziel. Details: [05-LERNEN-UND-RSI.md](05-LERNEN-UND-RSI.md).

## 14. Weitere gemeinsam besprochene Fähigkeiten

- **Persistente Goals:** Mehrteilige Aufträge und Nutzerkorrekturen versioniert erhalten; „fertig“ an Kriterien binden.
- **Recovery:** Toolausführung und Ergebniszustellung können auseinanderfallen. Nach Neustart Zustand abgleichen; keine universelle Exactly-once-Garantie behaupten.
- **Programmatic Tool Calling:** Mehrere bekannte Schritte in einem begrenzten Programm ausführen; derselbe Dispatcher, Rechte, Accounting und Abbruch wie bei Einzelcalls.
- **Subagents:** Nur für sinnvoll unabhängige Arbeit; klare Aufgabe, Budget, Stopkriterium und belegte Rückgabe. Desktopzugriff und gemeinsame Änderungen koordinieren.
- **Treasury/Secrets:** Bestehenden Broker nutzen; Credentials nicht in Code, Prompts, Images oder Standardlogs kopieren.
- **Research:** Quellen tatsächlich lesen, Veröffentlichungs-/Abrufdatum und Unsicherheiten berücksichtigen; Rechercheplan, Abgleich, Synthese und nutzbares Artefakt statt nur Linkliste.
- **Provider-Austauschbarkeit:** MiMo als Implementierungswerkzeug nicht mit Skaldirs Laufzeitmodell verwechseln; Adapter-Fähigkeiten und konfiguriertes Modell separat testen.
- **Wartbarkeit:** Kein neues Framework oder zusätzlicher Memory-Store ohne konkrete Lücke, Nutzenmessung und Migrationsplan.

## 15. Offene Fehler: präzise Diagnose statt Schuldzuweisung ans Modell

| Problem | Belegstand | Nächster Nachweis |
|---|---|---|
| Fensterflut | Nutzer meldet 12+ Fenster; wiederholte Starts im Log | Launcherpfade, Sessionzustand und Retryentscheidung verfolgen |
| Beobachtung unvollständig | Log enthält leere oder nur andere Fenster | Tatsächliche Sitzung/Backend/Filter mit bekanntem Fenster vergleichen |
| stdout = `verified` | Im Log sichtbar; im Handoff als repariert gemeldet | Unverfälschte Werte durch realen Agent-Call nachweisen |
| Discovery findet Tool, Aufruf scheitert | Im Log sichtbar | Nach Suche tatsächlich verfügbare Provider-Schemas und Dispatch prüfen |
| `api_call` verlangt falsche Form | Früher im Log; HTTP-Fix gemeldet | Runtime-Tool und HTTP-Aufruf sauber auseinanderhalten |
| Builder ohne Modellclient | Expliziter Fehler im Log | Aktiven Builder mit konfiguriertem Client/Executor ausführen |
| Ungültige COMPOSE-Felder | Explizite Schemafehler im Log | Vertrag laden, erstellen und ausführbaren Flow tatsächlich starten |
| DNS-Auflösung scheitert | `curl (6)` belegt | Host/Sandbox im passenden Netzwerkprofil vergleichen |
| Launch-Payload widersprüchlich | Im Handoff berichtet, vollständiger Call fehlt | Originalpayload und Validator vor Ausführung prüfen |
| Maus/Tastatur nicht nutzbar | Im Handoff berichtet | Unterstützten Backendpfad anhand echter Eingabe prüfen |

„Das Modell versteht es nicht“ ist ohne Prüfung der tatsächlichen Schemas und erreichbaren Tools keine ausreichende Ursache. Ebenso wenig wird ein Desktopproblem durch Wahl eines anderen Fenstertitelstrings repariert.

## 16. Arbeits- und Testverfahren für MiMo

1. Projektregeln lesen; HEAD, Arbeitsbaum, Wrapper, aktive Runtime und vorhandene Auditdateien feststellen.
2. Berichtete Änderungen und Tests gegen aktuellen Code zuordnen. Alte PIDs, Locks und Logs nicht blind übernehmen.
3. Eine gemeinsame Gap-Matrix führen: ID, Nutzeranforderung, Ist-Beleg, Lücke, Priorität, Änderung, Test und Nachweisgrad.
4. Zentralen Fehler zuerst mit kleinem Fixture reproduzieren; danach kleinste konsistente Reparatur über alle aktiven Pfade.
5. Nach einzelnen Fixes gezielt testen. Zusammenhängende Änderungen mit passenden Integrationstests abschließen.
6. Vorgeschriebene Projektchecks vollständig ausführen; keinen unveränderten breiten Testblock ohne Anlass wiederholen.
7. Bestehende Testläufe erst prüfen; keine Warteschlange identischer flock-Prozesse erzeugen. Begrenzte Lock-Wartezeit und sichtbarer Status.
8. Vollständige Ausgabe erhalten, pytest-Exitcode korrekt weitergeben, nicht ausschließlich `tail -15` anzeigen.
9. Deterministische Tests mit wenigen kontrollierten echten Agentenläufen ergänzen. Browserflut zuerst per Fixture reproduzieren, nicht auf dem Nutzerdesktop.
10. Nach jeder abgeschlossenen Arbeitsphase Handoff und Gap-Matrix anpassen; unabhängige Arbeiten bei externem Blocker fortsetzen.

Keine Tests schwächen, nur um grün zu werden. Eine Änderung von Erwartungen ist legitim, wenn das gewünschte Produktverhalten sie begründet und der neue Vertrag vollständig geprüft wird.

## 17. Verbindliche Produktabnahme

| ID | Nutzerablauf | Erforderlicher Nachweis |
|---|---|---|
| E2E-01 | Coding-Auftrag | Reproducer vor Fix, passende Änderung, Tests nach Fix, fremde Änderungen erhalten |
| E2E-02 | Dynamische Tool-Nutzung | Suche→gültiges Schema→aktiver Call→fachliches Resultat |
| E2E-03 | Zen und Recherche | Richtiger Browser, keine Fensterflut, echte Quellen und nachvollziehbare Antwort |
| E2E-04 | Vollständiger Linux-Desktop-Ablauf | Beobachtung, Klick, Text, Scroll/Drag, Fokusbindung und Postcondition |
| E2E-05 | Recherchepaper in LibreOffice | Quellen, Dokumentinhalt, korrekter Speicherpfad, erneutes Öffnen und Prüfung |
| E2E-06 | Allgemeines neues Tool | Kandidat→Test→Registry→Aufruf→Wiederverwendung im neuen Run |
| E2E-07 | Docker-Tool | Echter Build, Containerlauf, fachlicher Test, registrierter erneuter Aufruf |
| E2E-08 | Schnelles Subtool | Wiederholbarer Ablauf mit weniger Inferenz; keine Erfolgsverschlechterung |
| E2E-09 | Voice/JEV verhindert Fehlverhalten | Wiederholung oder falscher Abschluss wird erkannt und wirksam korrigiert |
| E2E-10 | Memory/Kontext | Korrekte spätere Erinnerung, gültige Quellenrevision, erhaltene offene Kriterien |
| E2E-11 | Unterbrechung/Resume | Kein doppelter Seiteneffekt, aktueller Zustand abgeglichen, Nutzerarbeit erhalten |
| E2E-12 | Polierte CLI | Klare Zustände, stabile Eingabe/Stop, Details, Plain/JSONL, echte Terminalprüfung |

Jedes Ergebnis erhält Codezustand, Konfiguration, Umgebung, Testdatum und Artefakt-/Logreferenz. Historische Tests mit aktuellen Änderungen nicht vermischen. Teilweise geprüfte Anforderungen als solche ausweisen.

## 18. Konkreter Start der nächsten Sitzung

1. Aktiven Toolpfad rekonstruieren und beweisen: Discovery, Provider-Toolliste, Dispatcher, Executor und Ergebnisdaten. stdout-Sentinel-Test und echte registrierte Tool-Nutzung.
2. Browserstarts zentral koordinieren; fehlgeschlagene Beobachtung erzeugt keine weiteren Starts. Danach echte Sitzung und Eingabe backendgerecht diagnostizieren.
3. Einen kurzen vollständigen Computer-Use-Ablauf real ausführen und als erstes versioniertes Subtool wiederverwendbar machen.
4. Builder-Client/Executor verdrahten und einen allgemeinen sowie einen Container-Tool-Lebenszyklus abschließen.
5. Voice/JEV an dieselben Events und Kriterien binden, Fehler-Replays abnehmen; gleichzeitig Baseline messen und verständliche CLI-Zustände liefern.

Danach P1-Themen vertiefen und erst bei tragfähiger Evaluation experimentelle RSI starten. Diese Liste ist ein Einstieg, keine Beschränkung auf fünf kleine Fixes.

## 19. Dokumente dieses Pakets

| Datei | Vertiefung |
|---|---|
| [00-MIMO-START.md](00-MIMO-START.md) | Ursprünglicher Implementierungsauftrag und Anforderungs-IDs |
| [01-IST-ABGLEICH.md](01-IST-ABGLEICH.md) | Codeaudit, Entscheidungen und Migration |
| [02-INFORMATION-UND-MEMORY.md](02-INFORMATION-UND-MEMORY.md) | Exakte Informations- und Memory-Verträge |
| [03-TOOLS-UND-KONTEXT.md](03-TOOLS-UND-KONTEXT.md) | Effiziente Tools, Kontext, Routing und Budgets |
| [04-LINUX-COMPUTER-USE.md](04-LINUX-COMPUTER-USE.md) | Backend-, Beobachtungs-, Aktions- und Recovery-Verträge |
| [05-LERNEN-UND-RSI.md](05-LERNEN-UND-RSI.md) | Skills und kontrollierte Verbesserung |
| [06-TESTS-UND-ABNAHME.md](06-TESTS-UND-ABNAHME.md) | Technische Tests und Vergleichsverfahren |
| [07-QUELLEN-UND-ENTSCHEIDUNGEN.md](07-QUELLEN-UND-ENTSCHEIDUNGEN.md) | Frühere Recherche; aktuelle Integration separat verifizieren |
| [08-LESEINDEX.md](08-LESEINDEX.md) | Versionierte Zeilenbereiche des gelieferten Pakets |
| [09-CODING-AGENT-GRUNDLAGEN.md](09-CODING-AGENT-GRUNDLAGEN.md) | 24 Grundlagen und 16 Prüfszenarien |
| [10-CLI-UI-UX-UPGRADE.md](10-CLI-UI-UX-UPGRADE.md) | Ansichten, Animationen, Interaktion und 12 UI-Abnahmen |

Bei Prioritätskonflikten gilt die jüngste Nutzerpräzisierung in diesem Handoff: vollständiges Computer Use, schnelle Subtools, beide Tool Builder und Voice/JEV sind hoch priorisierte Produktarbeit. Die Standardfunktionen tragen diese Arbeit; Geschwindigkeit und UI-Qualität begleiten sie. Technische Detailvorschläge bleiben an tatsächlichen Code und belegte Ergebnisse anpassbar.

## 20. Anhang: die `/`-Befehle im Chat (Slash-Vokabular)

Stand: 25. September 2026, aus dem Arbeitsbaum `godsv-supervisor-hardening` generiert. Quelle ist die Registry `SLASH_COMMANDS` in `src/skaldir/slash.py` — die Palette des Chat-Eingabefelds (`skaldir chat`, Repl). Jeder Eintrag faltet auf einen echten CLI-Befehl ab (`skaldir …`): gleicher Vertrag, gleiche Exit-Codes, gleiche JSON-Hülle. Dieser Anhang ist eine Momentaufnahme dieser Registry; verbindlich bleibt der Code.

Insgesamt **101 Einträge**: 97 führbar (davon 0 mit Pfad ohne Contract-Eintrag), 4 ehrlich als *geplant – nicht gebaut* markiert. Die Palette zeigt zusätzlich Live-Zustände (`ready`, `blocked`, `unwired`, `unverified`, `planned`), die zur Laufzeit gegen den Parser geprüft werden; sie sind hier nicht vorweggenommen.

| Befehl | Aliasse | Was er tut | Führt aus | Benötigt | Status |
|---|---|---|---|---|---|
| `/help` | `/h` | Diese Palette; `/help <befehl>` zeigt die eigene `--help` des Befehls | — | — | Builtin der Palette |
| `/goal` | — | Das dauerhafte Ziel-Protokoll (Revision, Kriterien, Evidence) | `skaldir goal show` | — | bereit |
| `/status` | — | Der echte Selftest: ein ehrliches Verdikt je Layer | `skaldir selftest` | — | bereit |
| `/plan` | `/deep-planning` | Schrittplan zum aktuellen Ziel | — | — | geplant – nicht gebaut |
| `/verify` | — | Gespeicherte Verifikationsläufe: Verdikt und Freshness | `skaldir verify status` | — | bereit |
| `/tools` | — | Die Tool-Registry: Specs, Builds, Health | `skaldir tools list` | — | bereit |
| `/skills` | — | Die vom Runtime geminten Skill-Kandidaten und ihr Status | `skaldir skills list` | — | bereit |
| `/scan` | — | Sicherheitsaudit dieses Trees: jede deklarierte Defektklasse, mit `file:line` und Fix | `skaldir security audit` | — | bereit |
| `/runs` | — | Der Run-Store: Ziele, laufende Operationen, Budget | `skaldir runs status` | — | bereit |
| `/workflows` | — | Einen deklarierten Workflow nach Namen ausführen | `skaldir workflows run` | `a workflow id` | bereit |
| `/stop` | — | Einen laufenden Agent-Run abbrechen | `skaldir agent cancel` | `a RUN_ID — the run to cancel` | bereit |
| `/retry` | — | Einen früheren Agent-Run mit eigenem Auftrag erneut ausführen | `skaldir agent retry` | `a RUN_ID — the earlier run to retry` | bereit |
| `/review` | `/review-session` | Ein SUPERVISE-Durchlauf über die neueste (oder `--session`) Trajektorie — Findings und Verdikt | `skaldir voice check` | `a stored trajectory (events.db) — the pass refuses honestly without one` | bereit |
| `/skills-promote` | — | Einen geminten Skill-Kandidaten nach seiner Validierung befördern (propose-first, gated) | `skaldir skills promote` | a skill NAME — the candidate to promote (see `skaldir skills list`) | bereit |
| `/locking` | — | Das goal-locked Runtime: Ziel, deklarierte Kriterien, echtes Gate, kein Exit auf einen Claim | `skaldir run --progress` | a goal in one sentence — the text after the verb (or `--resume <goal-id>`) | bereit |
| `/doctor` | — | Deployment- und Health-Check: was installiert ist, was fehlt | `skaldir doctor` | — | bereit |
| `/model` | `/models` | Provider und Modell hinter der Jev- oder der LLM-Rolle wählen | `skaldir model` | — | bereit |
| `/provider` | — | Konfigurierte LLM-Provider, redigiert | `skaldir provider list` | — | bereit |
| `/context` | — | Das Kontextbudget eines Transkripts: gemessene Fakten, beschriftete Schätzungen | `skaldir context usage --text` | `--transcript (a message file or a run transcript) or --run-id` | bereit |
| `/compact` | `/compress`, `/smol` | Die Tool-Historie dieses Runs pro Call bewerten und Kontext freigeben | `skaldir context compact --text` | `--transcript (a message file or a run transcript) or --run-id` | bereit |
| `/checkpoint` | — | Der self-sufficient Snapshot: Datei, Indexzeile und Cursor | `skaldir runs checkpoint` | — | bereit |
| `/snapshots` | `/snapshot` | Die Checkpoints dieses Runtime und was sie kosten | `skaldir checkpoint list` | — | bereit |
| `/new` | — | Eine neue Session starten (oder mit `--parent` forken) | `skaldir session new` | — | bereit |
| `/sessions` | `/ls`, `/list` | Die Sessions dieses Runtime: Titel, Nachrichten, letzte Aktivität | `skaldir session list` | — | bereit |
| `/resume` | — | Eine Session wieder öffnen: Vorhistorie, Hash-Prüfung, nächste legale Rolle | `skaldir session resume` | — | bereit |
| `/search` | — | Nachrichteninhalt über Sessions suchen (begrenzt, gerankt, FTS5) | `skaldir session search` | `a query` | bereit |
| `/export` | — | Eine Session als json / jsonl / markdown / text exportieren | `skaldir session export` | — | bereit |
| `/history` | — | Das Nachrichtenfenster einer Session: Header, Hash-Verdikt, Audit | `skaldir session show` | — | bereit |
| `/fork` | `/branch` | Eine Session in ein verknüpftes Kind abzweigen und dort weiterarbeiten | `skaldir session fork` | — | bereit |
| `/rename` | `/title` | Eine bestehende Session umbenennen | — | — | geplant – nicht gebaut |
| `/newtask` | — | Eine frische Session mit destilliertem Kontext aus dieser | — | — | geplant – nicht gebaut |
| `/clear` | `/cls` | Bildschirm löschen und das Logo neu zeichnen (nur framed chat) | — | — | Builtin der Palette |
| `/quit` | `/q`, `/exit` | Die Shell verlassen (Ctrl-D tut dasselbe) | — | — | Builtin der Palette |
| `/restore` | — | Den Workspace aus einem Checkpoint wiederherstellen | `skaldir checkpoint restore` | — | bereit |
| `/approve` | — | Eine offene Approval-Rückfrage mit Ja beantworten, für einen Scope | `skaldir approval allow` | — | bereit |
| `/deny` | — | Eine offene Approval-Rückfrage mit Nein beantworten, mit einem Grund, an dem der Agent handeln kann | `skaldir approval deny` | — | bereit |
| `/telemetry` | — | Der Messprotokoll-Eintrag eines Runs: Turns, Tool-Calls, Tokens, Durchsatz, Kosten | `skaldir telemetry show` | `a RUN_ID — the run whose record you want` | bereit |
| `/diff` | — | Diff der letzten Edit-Transaction, kompakt gerendert | — | — | Builtin der Palette |
| `/undo` | — | Die Dateien einer Edit-Transaction byte-identisch wiederherstellen | `skaldir edit undo` | — | bereit |
| `/init` | — | Eine Projekt-AGENTS.md aus diesem Repository schreiben (überschreibt nie eine) | — | — | Builtin der Palette |
| `/exec` | — | Ein Code-Snippet im Sandbox-Prozess ausführen | `skaldir code run` | `--file PATH (or --stdin)` | bereit |
| `/setup` | — | First-Run-Setup: die zwei Pflichtschlüssel, jeder mit einem echten Call bewiesen | `skaldir setup` | — | bereit |
| `/cost` | — | Usage dieser Session: Nachrichten nach Rolle, Token-Schätzung, Turns, Modell | `skaldir coding status --text` | — | bereit |
| `/limit` | — | Das Kontextlimit und seine Historie: Budget, Trigger, was verworfen wurde | `skaldir context usage --text --history` | `--transcript (a message file or a run transcript) or --run-id` | bereit |
| `/memory` | — | Das Memory-System: Scopes, Kinds, Decay-Policy, Journal-Integrität | `skaldir memory scope` | — | bereit |
| `/agent` | — | Die begrenzte Agent-Loop: Runs, Transkripte, Retry | `skaldir agent list` | — | bereit |
| `/keys` | — | Die Tasten, die diese Shell bindet, und was sie tun | — | — | Builtin der Palette |
| `/debug` | `/reportbug` | Lokale Diagnosen für einen Bug-Report sammeln | — | — | geplant – nicht gebaut |
| `/selftest` | — | Derselbe Selftest, gesprochen wie der Befehl, den er ausführt | `skaldir selftest` | — | bereit |
| `/jev` | — | Die Entscheidungsebene: Status, Kinds, Packs, History, Decide | `skaldir jev selftest` | — | bereit |
| `/act` | — | Was dieser Host wirklich kann (ACT) — und was fehlt | `skaldir act check` | — | bereit |
| `/computer-use` | `/cu` | Der isolierte Desktop: eine GUI-Aufgabe ausführen, oder melden, was wirklich steuerbar ist | `skaldir act check` | — | bereit |
| `/compose` | — | Gespeicherte Composite-Tools mit Ausführbarkeit je Schritt | `skaldir compose show` | — | bereit |
| `/run` | — | Ein Ziel bis zur Verifikation treiben (der ENFORCE-Pfad) — der Chat ist dessen Oberfläche | `skaldir run` | a goal in one sentence — the text after the verb (or `--resume <goal-id>`) | bereit |
| `/criteria` | — | Die Definition-of-Done des Ziels: Kriterien, Checks, Evidence | `skaldir goal criteria` | — | bereit |
| `/steer` | — | Den angehängten Run steuern: die Nachricht wird an der nächsten Turn-Grenze aufgenommen | — | — | Builtin der Palette |
| `/queue` | — | Eine Nachricht an die Queue des angehängten Runs anhängen (der Reihe nach, eine pro Turn) | — | — | Builtin der Palette |
| `/prompt` | — | Wie das Prompt dieser Session zusammengesetzt, gecacht und gefingerprintet wird | `skaldir prompt explain` | — | bereit |
| `/apply` | — | Eine Edit-Transaction anwenden (zuerst als Vorschau per Diff-Gate) | `skaldir edit apply` | `--tx <tx-id> (or --tx-json <file>) — the transaction to apply` | bereit |
| `/map` | — | Die Code-Map dieses Workspace | `skaldir code map` | — | bereit |
| `/find` | — | Im Codebase suchen (die gefaltete Schreibweise von `/search`, gleiche Aktion) | `skaldir code search` | `a query — the pattern to search for` | bereit |
| `/symbols` | — | Symbole einer Datei oder des Workspace | `skaldir code symbols` | — | bereit |
| `/diagnostics` | — | Die Language-Server-Diagnosen dieses Workspace | `skaldir code diagnostics` | — | bereit |
| `/testfix` | — | Der Test-Fix-Durchlauf der Coding-Loop zum aktuellen Fehler | `skaldir coding testfix` | the test command to fix, after `--` | bereit |
| `/mine` | — | Einen Container oder ein Image in ein typisiertes Tool minen (EXPAND) | `skaldir tools mine` | — | bereit |
| `/register` | — | Ein gebautes Tool in der Registry registrieren | `skaldir tools register` | `a tool NAME — the built tool to register` | bereit |
| `/health` | — | Die Health der Tool-Registry, pro Tool | `skaldir tools health` | `a tool NAME — the tool to probe` | bereit |
| `/rollback` | — | Ein registriertes Tool auf einen früheren Build zurückrollen | `skaldir tools rollback` | `a tool NAME — the tool to roll back` | bereit |
| `/flows` | — | Die Verifikationsflüsse dieses Workspace und ihre Verdikte | `skaldir verify flows` | — | bereit |
| `/adversarial` | — | Die adversarial Release-Tests (sie versuchen, einen echten User-Flow zu brechen) | `skaldir adversarial selftest` | — | bereit |
| `/audit` | — | Das Sicherheitsaudit über diesen Workspace | `skaldir security audit` | — | bereit |
| `/vision` | — | Was dieser Build ehrlich mit einem Bild kann, und die OCR-/Screenshot-Türen | `skaldir vision capabilities` | — | bereit |
| `/remember` | — | Eine Memory mit Scope und Kind speichern | `skaldir memory remember` | — | bereit |
| `/recall` | — | Gespeicherte Memories gegen eine Query ranken, mit ihren Erklärungen | `skaldir memory recall` | — | bereit |
| `/forget` | — | Eine Memory (nach ID) oder einen ganzen Scope verwerfen | `skaldir memory forget` | — | bereit |
| `/agents` | — | Die Subagents, die dieses Runtime gespawnt hat, mit ihrem Zustand | `skaldir agent list` | — | bereit |
| `/spawn` | — | Einen Subagent für eine Aufgabe spawnen | `skaldir agent spawn` | `an assignment — the task text (or --task-file PATH)` | bereit |
| `/collect` | — | Das Ergebnis eines fertigen Subagents einsammeln | `skaldir agent collect` | `a RUN_ID — the run whose result to collect` | bereit |
| `/cancel` | — | Einen laufenden Agent-Run abbrechen (der gefaltete Name von `/stop` für einen Run) | `skaldir agent cancel` | `a RUN_ID — the run to cancel` | bereit |
| `/host` | — | Der Host, auf dem dieses Runtime sitzt: Status, Sessions, Skills, Cron, Tools | `skaldir host status` | — | bereit |
| `/docker` | — | Die Container-Abhängigkeit: ist ein nutzbarer Docker-Daemon erreichbar | `skaldir docker ensure` | — | bereit |
| `/image` | — | Lesen, was ein Image wirklich ist: Config, Layers, Pins, Guards | `skaldir image list` | — | bereit |
| `/mcp` | — | Die MCP-Naht: was verlinkt ist, was es anbietet, was es antwortet | `skaldir mcp map` | — | bereit |
| `/tools-core` | — | Die Registry der generierten Tools selbst: list, show, call, health, policy | `skaldir tools-core list` | — | bereit |
| `/tools-hub` | — | Tools, Skills und MCP-Server, die neben dem Runtime gefunden wurden | `skaldir tools-hub` | — | bereit |
| `/permission` | — | Das Permission-Profil zweier Konsumenten: Sandbox und Agent | `skaldir permission show` | — | bereit |
| `/completion` | — | Das Completion-Gate: Zustand, Metriken, Historie, ein Override | `skaldir completion status` | — | bereit |
| `/spend` | — | Das Treasury: Anfragen, Freigaben, was übrig ist | `skaldir spend list` | — | bereit |
| `/stream` | — | Der Streaming-Pfad selbst: selfcheck, measure, replay, policy | `skaldir stream selfcheck` | — | bereit |
| `/registry` | — | Das Runtime-Registry: wo der Zustand liegt, was eine Migration tun würde | `skaldir registry status` | — | bereit |
| `/memory-stats` | — | Memory: wie viele Records, welche Scopes, wie alt | `skaldir memory-stats` | — | bereit |
| `/memory-search` | — | Den Memory-Store durchsuchen | `skaldir memory-search` | — | bereit |
| `/memory-audit` | — | Memory: die Records, die eine Owner-Entscheidung brauchen | `skaldir memory-audit` | — | bereit |
| `/memory-retrieval-check` | — | Memory: antwortet Retrieval für diesen Store noch | `skaldir memory-retrieval-check` | — | bereit |
| `/metrics` | — | Die lokalen Metriken dieses Runtime | `skaldir metrics` | — | bereit |
| `/voice` | — | Voice: was dieser Build kann, was er braucht, Triage eines Fehlers | `skaldir voice status` | — | bereit |
| `/warm` | — | Die warme Session: starten, läuft sie, stoppen | `skaldir warm status` | — | bereit |
| `/style` | — | Die monochrome Sprache: die Marke, die Palette, eine Vorschau | `skaldir style logo` | — | bereit |
| `/prompts` | — | Die mitgelieferten Prompt-Templates | `skaldir prompts list` | — | bereit |
| `/ledger-show` | — | Das Ledger eines State-Verzeichnisses, zurückgelesen | `skaldir ledger-show` | — | bereit |
| `/pact-show` | — | Der Pact eines State-Verzeichnisses, zurückgelesen | `skaldir pact-show` | — | bereit |

### Nicht gebaute Befehle (ehrlich gemeldet, nie ausgeführt)

| Befehl | Warum noch nicht gebaut | Stattdessen real vorhanden |
|---|---|---|
| `/plan` | there is no goal-level planner verb in this build | `coding plan show`, `coding plan begin`, `completion begin`, `verify plan` |
| `/rename` | the session store can retitle a session in place (`session_store.retitle`), but no CLI verb exposes it: `session new --title`, `session fork --title` and `se… | `session fork --session <id> --title <new title>`, `session new --title <title>`, `session list` |
| `/newtask` | both halves are real separately — `session new` (and `session new --parent`, which starts a fresh session linked to this one) and `context compact` (which su… | `session new --parent <session> --title <title>`, `context compact --transcript <a message file>`, `session fork --session <id> --title <title>` |
| `/debug` | there is no upload or report verb in this build — nothing sends a bundle anywhere | `doctor`, `telemetry show <run-id>`, `contract dump --json` |

Ablage und Nachprüfung: `SLASH_COMMANDS` in `src/skaldir/slash.py`; die Zuordnung `/befehl` → CLI-Verb steht dort als `argv`/`flags`; die Argumentvervollständigung kommt aus `src/skaldir/complete.py`. Der Status „bereit“ bedeutet: der gemappte CLI-Pfad existiert im Contract (`cli_contract.COMMANDS`) und wird real ausgeführt. „geplant“ bedeutet: kein `argv`, keine Ausführung — die Palette nennt stattdessen die real vorhandenen Nachbarbefehle.
