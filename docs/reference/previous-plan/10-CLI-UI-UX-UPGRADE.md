# Skaldir CLI: UI- und UX-Upgrade für MiMo

Stand: 24. September 2026. Status: Design- und Implementierungsauftrag; aktuelle UI-Implementierung nicht geprüft. Anforderungen `CLI-01` bis `CLI-12`.

## 1. Auftrag und Zielbild

MiMo: Vergleiche diesen Plan zuerst mit der aktiven Skaldir-CLI. Nutze die vorhandene Terminalbibliothek und Eventarchitektur, wenn sie die Anforderungen erfüllen. Ergänze fehlende Funktionen schrittweise und teste das echte Terminalverhalten. Kein Framework-Wechsel nur für Animationen. Gute vorhandene Funktionen erhalten; unpassende Details mit Begründung anpassen.

Skaldir soll wie ein ruhiger, kompetenter Begleiter mit etwas nordischem Humor wirken. Die kleine bestehende Figur bleibt das Markenzeichen. Animationen verleihen ihr Leben, aber verbergen weder Fehler noch fehlende Fähigkeiten. Jede Anzeige beantwortet: Woran arbeitet Skaldir? Was passiert wirklich? Braucht es mich? Was wurde nachweislich geschafft?

**Wichtigster Umbau:** Die aktuelle Folge aus `thinking (step N)`, JSON-Ausschnitten und `FAILED ALLOW` wird durch eine stabile Aktivitätsanzeige und wenige aussagekräftige Ereignisse ersetzt. Vollständige technische Details bleiben gezielt erreichbar.

## 2. Visuelle Sprache

- Dunkle und helle Terminalthemen respektieren; Standardhintergrund des Terminals verwenden.
- Akzent: gedämpftes Cyan für Skaldir und aktive Arbeit. Grün für belegten Erfolg, Gelb für Einschränkungen, Rot für Fehler. Farben immer mit Text und Symbol kombinieren.
- Normale Texte in Terminal-Vordergrundfarbe; sekundäre Details etwas zurücknehmen, aber lesbar halten.
- Keine Regenbogenanimation, großflächige Hintergrundflächen oder ständig wechselnden Werbesprüche.
- Unicode ist eine Verbesserung, keine Voraussetzung. ASCII-Modus besitzt dieselben Informationen.
- Nutzertexte und Antworten dürfen Deutsch oder Englisch sein. UI-Sprache zentral konfigurieren; keine zufällige Sprachmischung durch einzelne Komponenten.
- Terminalschrift und Emojis nicht voraussetzen. Figur und Symbole nach Zellbreite rendern; Unicode-Rahmen abschaltbar.

Das vorhandene Logo einmal beim interaktiven Start zeigen; kompakt oder ausblendbar. Keine Logos vor jeder Antwort, im Pipe-Modus oder in maschinenlesbaren Ausgaben.

## 3. Hauptansichten

### 3.1 Start: nützlicher Kontext statt interner Platzhalter

```text
   ▄▄██████▄
  ████████▄██
  █████████▀▀    ▀     SKALDIR
  ▀█████████▄  ▀▀▀     Bereit für den nächsten Auftrag.

  ~/projects/gods-voice · main* · glm-5.3-flash
  Computer Use: verfügbar · Codeausführung: verfügbar

  › Was soll ich erledigen?
```

Alle hier gezeigten Fähigkeiten und Modellnamen sind Beispieldaten. Tatsächliche Werte aus Runtime und Capability-Prüfung beziehen. `main*` nur bei Git-Repository mit Änderungen; fehlendes Git ist kein Fehler. Teure oder invasive Checks nicht synchron bei jedem Prompt ausführen. Während einer noch laufenden Prüfung `wird geprüft` anzeigen.

`goal:none`, `provider:llm`, `ctx n/a` und `$ n/a` nicht dauerhaft als technische Leerwerte präsentieren. Ohne aktiven Auftrag reicht der Prompt. In Detailansichten heißen fehlende Messwerte ausdrücklich `unbekannt` oder `nicht gemeldet`. Unbekannte Kosten niemals als kostenlos darstellen.

### 3.2 Während eines Computer-Use-Auftrags

```text
  › /computer-use open my zen browser, and research about datacenters in space

  Auftrag  Browser öffnen und Rechenzentren im All recherchieren

  ✓ Browserprogramm gefunden                    0,2 s
  ✓ Startbefehl beendet                         0,4 s
  ◌ Browserfenster prüfen                       3,1 s
    Noch kein Fenster beobachtet.

  ◐ Skaldir prüft die Desktop-Verbindung …      00:08
  Esc Details · Ctrl+C Stoppen
```

Die Zeilen sind ein Layoutbeispiel, keine Vorhersage tatsächlicher Aktionen. Wenn der Browserprozess noch läuft, lautet sein Zustand `läuft`, nicht `Startbefehl beendet`. Ein erfolgreicher Start belegt weder ein sichtbares Fenster noch eine geladene Seite.

### 3.3 Fehler verständlich und handlungsfähig darstellen

```text
  ! Browseröffnung noch nicht bestätigt
    Der Startbefehl wurde ausgeführt. Die Desktop-Beobachtung
    liefert derzeit keine Anwendungsfenster.

    Nächster Schritt: Desktop-Zugriff und Sitzung prüfen.

  ▸ Details: Beobachtungsbackend · stderr · Aufrufargumente
```

Die Diagnose aus dem Log darf nicht als endgültig bekannte Ursache vorgetäuscht werden. `Keine Fenster beobachtet`, `Beobachtung nicht verfügbar` und `Zugriff verweigert` sind unterschiedliche Zustände. Ohne Backend-Evidenz keinen Wayland-Fehler behaupten.

Nach wiederholt identischem Fehler ein Ereignis zusammenfassen: `Fensterprüfung dreimal ohne neuen Befund`. Kein endloses Wiederholen animierter Fehlermeldungen. Die UI darf Wiederholungen verdichten, aber nicht löschen; Runtime muss zusätzlich ihre Wiederholungsstrategie korrigieren.

### 3.4 Tool-Ergebnisse kurz und bei Bedarf vollständig

```text
  ✓ Datei aktualisiert  stock-research/main.py
    42 Zeilen · 1.204 Bytes geschrieben

  ✗ Tests fehlgeschlagen  tests/test_parser.py
    1 fehlgeschlagen · 8 bestanden · Exit 1 · 0,7 s

  ▸ Details zu #12
```

Zeilen-/Bytezahlen und Testzählungen nur aus echten Daten anzeigen. Ist die Testausgabe nicht zuverlässig parsebar, Prüfkommando und Exitcode ohne erfundene Testzahlen zeigen. `created:false` wird zu `Datei aktualisiert`, sofern der Schreibvertrag Erfolg bestätigt. Kein vermeintlicher Fehler wegen einer bereits existierenden Datei.

Standardmäßig zeigen: verständlicher Tool-Titel, relevantes Ziel, Status, kurze Ergebniszusammenfassung und Dauer. Vollständiges JSON, IDs und Berechtigungsdetails gehören in die Detailansicht.

### 3.5 Abschluss

```text
  ◇ Code erstellt — Ausführung noch ungeprüft

  Erstellt
    stock-research/ mit CLI, Dockerfile und Dokumentation.

  Prüfung
    ✓ Dateien gespeichert und zurückgelesen
    – Docker-Build nicht ausgeführt: Executor nicht verfügbar
    – X-Abfrage nicht geprüft: Zugang fehlt

  Offen
    Image bauen und Container-Smoke-Test ausführen.

  28 Schritte · 02:14 · Kosten nicht gemeldet
  ›
```

Auch hier sind Werte nur Beispieldaten. Keine Überschrift `done`, wenn erforderliche Prüfungen fehlen. Abschluss kennt `erledigt und geprüft`, `teilweise erledigt`, `blockiert`, `abgebrochen` und `fehlgeschlagen`. Status aus Zielkriterien und Evidence ableiten; UI darf ihn nicht aus der letzten Tool-Farbe erraten. Bei einfachen Antworten keine überladene Abschlusskarte erzwingen.

## 4. Animationen mit Persönlichkeit

### 4.1 Drei auswählbare Stile

| Stil | Verhalten | Verwendung |
|---|---|---|
| `calm` | Kleiner ruhiger Spinner, keine Sprüche | Standard für tägliche Arbeit |
| `skald` | Spinner plus seltene spielerische Akzente | Nutzerwahl für mehr Charakter |
| `plain` | Keine Animation oder Cursorbewegung | Screenreader, Logs, eingeschränkte Terminals |

Zusätzlich `motion=off` als unabhängige Einstellung. `NO_COLOR` betrifft Farben, nicht automatisch Bewegung; beide Präferenzen getrennt behandeln. Vorgeschlagene Optionsnamen an bestehende CLI-Konventionen anpassen.

### 4.2 Animationsmotive

| Zustand | Vorschlag | Wahrheitsbedingung |
|---|---|---|
| Modell antwortet noch nicht | `◐ ◓ ◑ ◒` neben `Skaldir denkt …` | Provider-Aufruf tatsächlich aktiv |
| Tools werden gesucht | `·  ›  »  ›` neben `Werkzeug auswählen …` | Discovery tatsächlich aktiv |
| Code/Befehl läuft | `⠋ ⠙ ⠹ ⠸ ⠼ ⠴` neben `Befehl läuft …` | Prozess aktiv, kein erfundener Fortschritt |
| Hintergrundjob wartet | Ruhiger einzelner Punkt oder statischer Status | Wartestatus mit Grund bekannt |
| Abschluss | Einmaliger Übergang zu `✓` oder `◇` | Verifizierter Abschlussstatus |
| Fehler | Sofort statisches `!` oder `✗` | Keine lustige Fehleranimation |

ASCII-Fallback: `| / - \\` oder schlichter Statuspunkt. Das Motiv darf nicht auf Erfolg hindeuten, solange nur eine Prüfung läuft. Kein Prozentbalken ohne messbare Gesamtmenge.

Im Stil `skald` darf die Startfigur einmal kurz blinzeln; danach bleibt sie still. Spielerische Alternativen für allgemeine Wartephasen: `Runen werden sortiert …`, `Der Rabe sammelt Gedanken …`, `Ein Gedanke nimmt Form an …`. Höchstens ein solcher Akzent pro längerer Wartephase; nicht im Sekundentakt wechseln. Sachlichen Status weiterhin sichtbar lassen. Keine erfundenen Fachaktivitäten wie `Tests bestehen gleich …`.

### 4.3 Bewegungsbudget

- Nur eine sichtbare Animation pro Sitzung; parallele Tools werden als statische Zeilen mit gemeinsamem Aktivitätsindikator dargestellt.
- Zielwert 4–8 Frames/s, ohne Modellaufrufe oder Persistenz pro Frame. Bei Inaktivität keinen dauerhaften Render-Timer laufen lassen.
- Kurze Aktionen nicht mit blinkenden Übergängen überladen; Aktivitätsanzeige beispielsweise erst nach 150–250 ms zeigen. Semantische Ereignisse trotzdem vollständig erfassen.
- Beim Tippen bleibt der Eingabecursor stabil. Ausgabe darf eingegebenen Text nicht überschreiben.
- Kein Glockenton, Bildschirmblitz oder erzwungenes Scrollen zurück nach unten, wenn der Nutzer ältere Ausgaben betrachtet.

## 5. Strukturierter Output und Interaktion

### 5.1 Drei Informationsebenen

1. **Standard:** Auftrag, aktuelle Aktivität, wichtige abgeschlossene Ereignisse und Antwort.
2. **Details:** pro Tool Argumente, gekürzte Vorschau, stdout/stderr, Exitcode, Ausführungs- und Prüfstatus; Vollausgabe gezielt nachladen.
3. **Debug/Export:** redigierte strukturierte Ereignisse mit IDs und Zeitstempeln für Fehleranalyse.

Viele gleichartige Dateioperationen zusammenfassen, etwa `5 Dateien aktualisiert`, mit aufklappbarer Liste. Fehler, Freigaben und fachliche Meilensteine nie in einer Erfolgsgruppe verstecken. Maximal etwa sechs abgeschlossene Tool-Zeilen im aktiven Bereich; ältere Ereignisse bleiben im Verlauf/Export erreichbar. Beim Abschluss eine kompakte dauerhafte Zusammenfassung in den Scrollback schreiben.

Antworten als Terminal-Markdown rendern: klare Absätze, Listen, Codeblöcke und Tabellen. Breite Tabellen auf schmalen Terminals als einzelne Datensätze ausgeben. Code kopierbar halten; keine animierten oder dekorativen Zeichen in kopierbaren Befehlen. Sehr große Toolausgaben paginieren und ausdrücklich als Vorschau markieren.

### 5.2 Eingabe und Tastatur

| Interaktion | Verhalten |
|---|---|
| Enter | Vollständige Eingabe absenden |
| Mehrzeiliges Einfügen | Als zusammenhängenden Text behandeln; kein versehentliches Ausführen einzelner Zeilen |
| Mehrzeilige Bearbeitung | Terminalabhängige Taste unterstützen; alternativ dokumentierter Editor-/Paste-Modus |
| Ctrl+C während Run | Abbruch anfordern, sofort `Wird gestoppt …`; erst nach Bestätigung `Abgebrochen` |
| Erneutes Ctrl+C bei hängendem Abbruch | Bestehenden dokumentierten Eskalationspfad nutzen; keine fremden Prozesse beenden |
| Ctrl+C ohne Run, mit Text | Eingabe löschen; kurze Rückmeldung |
| Ctrl+C ohne Run und ohne Text | Konsistente dokumentierte Standardaktion; keine wiederholte lange Meldung |
| Esc bei geschlossenem Menü | Werkzeugdetails öffnen, sofern keine bestehende wichtige Bindung kollidiert |
| Esc in Menü/Detailansicht | Ansicht schließen, nicht Auftrag abbrechen |
| `/status` | Auftrag, Fähigkeiten, laufende Prozesse und verfügbare Messwerte |
| `/tools` | Verfügbare und nicht verfügbare Fähigkeiten mit kurzem Grund |
| `/details <id>` | Konkreten Aufruf prüfen; auch nach Abschluss |
| `/theme` | Vorschau und Auswahl der Stile, ohne den aktuellen Run zu verändern |

Neue Commands und Bindungen nur ergänzen, wenn sie nicht schon gleichwertig existieren. Hilfetext aus derselben Registry erzeugen. Während eines Runs Nutzereingaben als Korrektur/Anweisung sichtbar quittieren; kein stilles Verlieren oder unbeabsichtigtes Starten eines zweiten konkurrierenden Runs.

## 6. Datenvertrag: UI darf keine Zustände erfinden

MiMo soll vorhandene Events normalisieren. Logische Ereignisse: `run.started`, `phase.changed`, `tool.started`, `tool.output`, `tool.finished`, `verification.finished`, `run.waiting`, `run.cancel_requested`, `run.finished`. Dies sind Vorschläge, keine Pflichtnamen.

Jedes relevante Event benötigt Run-ID, stabile Sequenz/Ordnung und Zeitstempel; Tool-Events zusätzlich Call-ID. Prozesslaufzeit nach Möglichkeit monoton messen. Wiederholte Zustellung darf keine doppelten Zeilen erzeugen. Späte Ergebnisse eines alten Runs nicht unter einem neuen Auftrag anzeigen.

Getrennte Felder für:

- Berechtigung: erlaubt / abgelehnt / ausstehend.
- Ausführung: nicht gestartet / läuft / beendet / Startfehler / Timeout / Abbruch / Ausgang unbekannt.
- Exitcode: nur wenn tatsächlich vorhanden.
- Verifikation: nicht erforderlich / ausstehend / bestätigt / nicht bestätigt / nicht möglich.
- Ergebnisdaten: kurze Zusammenfassung, Referenz auf vollständige Ausgabe und Kürzungsstatus.
- Usage: gemeldet / geschätzt / unbekannt; Kosten entsprechend kennzeichnen.

`FAILED ALLOW` wird dadurch zu einer eindeutigen Aussage wie `Ausgeführt · Fenster nicht bestätigt`. `Erlaubt` ist im Normalfall nur ein Detail, kein konkurrierender Erfolgsstatus. stderr allein bedeutet nicht Fehler; Exitcode 0 allein bedeutet nicht fachlichen Erfolg.

Thinking-Animation zeigt Warte-/Arbeitszustand, keine versteckten Modellgedanken. Fortschritt stammt aus freigegebenen Phasen und realen Tool-Ereignissen. Wenn Streaming zunächst keine sichtbaren Tokens liefert, ehrlich `Modell antwortet …` anzeigen. UI-Liveness ist kein Beleg für Backend-Fortschritt.

## 7. Terminaltechnik und Robustheit

- Vor Implementation aktive Renderer, Prompt-Handling, Streaming, stdout/stderr-Nutzung und Tests finden. Ein Renderer besitzt die Terminalausgabe; Worker liefern Events statt beliebig zu printen.
- Bevorzugt vorhandenen zeilenorientierten Modus um einen kleinen Live-Bereich ergänzen. Vollbild-TUI nur, wenn bestehende Architektur und Nutzerabläufe davon profitieren. Normaler Scrollback und Copy/Paste müssen erhalten bleiben.
- Bei Resize neu umbrechen; nie Cursorpositionen aus alter Breite weiterverwenden. Lange Pfade verkürzen, vollständigen Pfad in Details erhalten.
- Keine ungeprüften ANSI-/OSC-Steuersequenzen aus Toolausgaben, Dateinamen, Remote-Inhalten oder Modellantworten an das Terminal durchreichen. Unterstützte Formatierung kontrolliert selbst erzeugen.
- Secrets vor Rendering und Speicherung redigieren. Debugmodus ist keine Ausnahme. Details benötigen dieselben Zugriffsrechte wie die Originalausgabe.
- Bei Nicht-TTY, `TERM=dumb` oder gewähltem Plain-Modus keine Cursorbewegungen, Spinner, Alternate-Screen-Sequenzen oder Logos.
- Maschinenformat, falls bereits vorhanden, kompatibel halten; ansonsten expliziter JSONL-Modus. stdout enthält dort ausschließlich vollständige JSON-Ereignisse; keine menschlichen Statuszeilen dazwischen. Diagnose separat auf stderr.
- Terminalzustand bei Ausnahme und Abbruch wiederherstellen: Cursor sichtbar, Farben zurückgesetzt, Eingabemodus normal. Kein pauschales Löschen des Bildschirms.
- Große Ausgaben über begrenzte Puffer und Artefaktreferenzen behandeln. Rendering drosseln; wichtige Zustandswechsel und Abbruch nicht durch Logflut blockieren.

## 8. Umsetzung in vier Schritten

### Schritt A: Wahrheit und Lesbarkeit

Zuerst Events und Statusmapping korrigieren, `created:false` sinnvoll darstellen, Berechtigung/Ausführung/Verifikation trennen, vollständige Details erreichbar machen. Noch ohne aufwendige Animation. Executor- und Computer-Use-Probleme aus dem geposteten Verlauf bleiben Backend-Aufgaben; eine UI kaschiert sie nicht.

### Schritt B: Layout und Eingabe

Kompakter Header, stabiler Live-Bereich, Tool-Gruppierung, Markdown-Antworten, sauberes Abbruchfeedback und Capability-Anzeige. Eingabe, Resize und Scrollback früh in echten Terminals prüfen.

### Schritt C: Skaldir-Stil

`calm`, `skald`, `plain`, dezente Motive, Bewegungsabschaltung und ASCII-Fallback. Spielerische Texte konfigurierbar; Fehlertexte bleiben konkret.

### Schritt D: Betriebsfestigkeit

JSONL/Plain-Ausgabe, große Logs, Providerpausen, parallele Tool-Events, späte Events, Wiederaufnahme und Crash-Cleanup prüfen. Bestehende Startparameter und Skripte kompatibel halten. Bei Bedarf hinter einem UI-Featureflag einführen; alter Renderer bleibt zeitweise als Rückweg verfügbar.

## 9. Anforderungen und Abnahmetests

| ID | Anforderung | Konkrete Abnahme |
|---|---|---|
| CLI-01 | Einstieg mit echten Fähigkeiten | Mit/ohne Git, Executor und Desktop starten: korrekte Werte, keine Fake-Verfügbarkeit, kein `goal:none`-Ballast. |
| CLI-02 | Ehrliche Tool-Zustände | Exit 0 plus fehlende GUI-Evidence: `ausgeführt, nicht bestätigt`; Startfehler und Exit 7 getrennt; stdout erhalten. |
| CLI-03 | Eindeutiges Datei-Feedback | Neue und bestehende Datei schreiben: `erstellt` versus `aktualisiert`; `created:false` löst keine Fehlermeldung aus. |
| CLI-04 | Strukturierte Details | Große JSON-/Logausgabe: kompakte Vorschau, Kürzungsmarker und vollständiger abrufbarer Inhalt mit identischer Call-ID. |
| CLI-05 | Stabile Animation | Fake-Clock-Test der Frames; keine Modellaufrufe pro Frame; nur eine aktive Animation; Fehler stoppt sie. |
| CLI-06 | Eingabe und Abbruch | Während Streaming tippen/einfügen; Text bleibt erhalten. Ctrl+C quittiert sofort, Abschluss erst nach Backendbestätigung. |
| CLI-07 | Terminalvarianten | 40/80/120 Spalten, Resize im Run, hell/dunkel, Unicode/ASCII und Motion-off: lesbare Inhalte ohne Überlagerung. |
| CLI-08 | Plain und Maschinenformat | Pipe-Ausgabe ohne ANSI/Spinner; jede JSONL-Zeile parsebar; keine Logos oder Fortschrittssprüche in stdout. |
| CLI-09 | Geprüfter Abschluss | Dockerfile nur geschrieben, Build fehlt: Teilstatus. Vollständige Evidence: Erfolg. Abbruch: kein Erfolgsbanner. |
| CLI-10 | Event- und Fehlerrobustheit | Doppelte/späte/parallel eintreffende Events, Providerpause und Renderer-Ausnahme: richtige Zuordnung, sauberes Terminal danach. |
| CLI-11 | Sichere Darstellung | Synthetisches Secret und eingebettete Terminalsteuerzeichen in Toolausgabe: weder Secret-Leak noch ausgeführte Steuersequenz. |
| CLI-12 | Begrenzte Ausgabe und Last | Umfangreicher synthetischer Eventstrom: Speicher durch festgelegte Puffer begrenzt, Eingabe/Stop reaktionsfähig, historische Details abrufbar. |

Render-Snapshots für deterministische Zustände mit Fake-Clock verwenden; keine fragilen Echtzeit-Screenshots jeder Animationsphase. Zusätzlich PTY-Integrationstests und manuelle Prüfung im unterstützten echten Terminal. Zielwert: sichtbare Reaktion auf lokale Eingabe/Stop unter 100 ms unter dokumentierter Referenzlast; externe Prozessbeendigung separat messen. Hardware und Last im Testbericht nennen, keine universelle Latenzgarantie.

Zwei Pflicht-Replays aus dem Nutzerverlauf:

1. Browserstart ausgeführt, keine Fenster beobachtet, mehrere Diagnoseversuche: klare Trennung, vollständige Diagnoseausgaben, keine Behauptung einer geladenen Seite.
2. Stock-Tool-Dateien geschrieben, `created:false`, Executor fehlt: keine Trunkierungsbehauptung aus UI-Kürzung, früh sichtbare Fähigkeitsgrenze, Abschluss ausdrücklich ungeprüft.

## 10. Definition of Done und Bericht an den Nutzer

MiMo liefert Codebelege für CLI-01 bis CLI-12, Vorher/Nachher-Ansichten, ausgeführte Testbefehle, Resultate und begründete Abweichungen. Prüfe mindestens einen echten Coding-Run und einen echten Computer-Use-Run; Fixtures allein beweisen nicht, dass die aktive CLI die Events korrekt erhält. Fehlende Desktop-Umgebung als offene Validierung ausweisen.

Erfolg bedeutet: Skaldir ist erkennbar und angenehm, der Nutzer versteht den aktuellen Zustand, Details sind erreichbar und ungeprüfte Arbeit wird nicht als fertig dargestellt. Lustige Animationen sind der letzte Feinschliff auf einer verlässlichen Anzeige.
