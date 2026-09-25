# Browser-Upgrade für Skaldir

Stand: 25. September 2026. Recherche und Umsetzungsvorschlag, keine bestätigte Installation oder Laufzeitabnahme. Ergänzt den Masterplan; verändert weder den Wunsch nach dem echten Host-Desktop noch die Approval-Regeln. Gegen bestehende Implementierung abgleichen, funktionierende Komponenten behalten.

## 1. Entscheidungsvorschlag

**agent-browser als beim Setup mitinstalliertes, versioniertes Standardwerkzeug für unterstützte Browser integrieren.** Skaldir bleibt der entscheidende Hauptagent. Ein gemeinsamer Adapter verbindet strukturierte Browseraktionen mit vorhandenem ACT/Computer Use. Zen im echten Nutzerprofil bleibt ein eigenes verpflichtendes Abnahmeziel; Chromium-Erfolg erfüllt dieses Ziel nicht.

Nicht vier Browser-Frameworks gleichzeitig installieren. Zuerst agent-browser mit dem vorhandenen Browser-/ACT-Pfad integrieren. Browser Harness als gezielten Vergleichskandidaten evaluieren, wenn direkte Sitzungskontrolle oder das Lernen von Helfern damit nachweislich einfacher wird. Playwright MCP bleibt eine Alternative für die vorhandene MCP-Infrastruktur.

Diese Auswahl ist eine Architekturentscheidung anhand der dokumentierten Funktionen, kein gemessener Geschwindigkeitsvergleich auf Willys Fedora-System.

## 2. Geprüfte Kandidaten

| Werkzeug | Dokumentierte Fähigkeiten | Eignung für Skaldir |
|---|---|---|
| [Vercel agent-browser](https://github.com/vercel-labs/agent-browser) | Apache-2.0; native CLI; persistente Sitzungen; kompakte Accessibility-Snapshots mit Elementreferenzen; JSON; Batch-Aufrufe; Tabs; Screenshots; CDP-Anbindung | Bevorzugter schlanker Ausführer. Kein zusätzlicher autonomer Agentenloop nötig. Batch reduziert Aufrufaufwand; Parallelität muss Skaldir weiterhin koordinieren. |
| [Microsoft Playwright MCP](https://github.com/microsoft/playwright-mcp) | Apache-2.0; MCP-Werkzeuge; strukturierte Browsersteuerung; persistente oder isolierte Profile; Extension für bestehende Chrome-/Edge-Sitzungen | Sinnvoll, wenn Skaldirs bestehende MCP-Integration bereits stabiler ist als seine CLI-Adapter. Kein Nachweis für vorhandene Zen-Sitzungen. |
| [Browser Use Browser Harness](https://github.com/browser-use/browser-harness) | MIT; direkte CDP-Verbindung zum echten Browser; Python-Helfer im Agentenworkspace; Skill; MCP über stdio | Besonders interessant als Vorbild oder Alternative für gelernte Browser-Subtools. Dokumentierter lokaler Anschluss verwendet Chrome Remote Debugging. Kein belegter Zen-Anschluss. |
| [Browser Use Agent](https://github.com/browser-use/browser-use) | Lokaler Python-Agent mit Modellwahl und lokalem oder Cloud-Browser; CLI wird ebenfalls angeboten | Für einen eigenständigen Browser-Agenten geeignet. Für Skaldirs Standardpfad bevorzugt direkte Werkzeuge/Harness verwenden, statt einen zweiten autonomen Entscheidungsloop einzubetten. |

Open Source bedeutet hier keine verpflichtende Cloud-Browser-Gebühr für den lokalen Werkzeugpfad. Modellaufrufe, optionale Cloud-Dienste und Betrieb können trotzdem Kosten verursachen. Keine bezahlte Cloud-Abhängigkeit als Voraussetzung einführen.

## 3. Entscheidende Grenze: Zen und bestehende Sitzungen

Playwright dokumentiert ausdrücklich, dass seine Firefox-Unterstützung gepatchte Builds benötigt; gewöhnliches Firefox wird dadurch nicht automatisch steuerbar. Daraus folgt: Die Behauptung „Firefox unterstützt, also vorhandenes Zen funktioniert“ ist unbelegt. Quelle: [Playwright Browser](https://playwright.dev/docs/browsers).

agent-browser dokumentiert CDP und das Finden laufender Chrome-Instanzen. Playwright MCP bezeichnet die Verbindung über seine Extension als Chrome-/Edge-Funktion. Browser Harness beschreibt ebenfalls einen Chrome-Debugging-Anschluss. Keines dieser dokumentierten Verfahren belegt die Steuerung von Willys bereits geöffnetem Zen.

Deshalb zwei explizite Ausführungswege:

1. **Vorhandenes Zen / persönliche Host-Sitzung:** vorhandene ACT-Backends nutzen, Fenster eindeutig identifizieren, Accessibility prüfen, bei Bedarf Screenshots/Vision und reale Eingaben einsetzen. Erst ein eigener erfolgreicher Anschlussversuch rechtfertigt zusätzliche strukturierte Zen-Unterstützung. Eine Firefox-/Zen-Erweiterungsbrücke wäre ein eigener späterer Machbarkeitsschritt, kein bereits verfügbares Feature.
2. **Strukturiert unterstützter Browser:** vorhandene kompatible Sitzung gezielt verbinden; alternativ eine von Skaldir verwaltete Browserinstanz für ausdrücklich passende Recherche-/Testaufgaben verwenden. Browser und Profil müssen sichtbar benannt sein. Eine Anfrage „in meinem Zen“ darf nicht still in einem anderen Browser ausgeführt werden.

Bestehende Profile nicht während der Benutzung kopieren, keine Login-Daten als gewöhnliche Logs exportieren. Bei fehlendem Anschluss nicht beliebig Browserprozesse nachstarten. Backendwechsel darf keine unbekannte vorherige Aktion erneut auslösen.

## 4. Browser-Layer statt lose Shell-Befehle

Vor Implementierung vorhandene Tool-Registry, Computer Use, Browserlebenszyklus, Events, Approval, Run-Store und Subtools untersuchen. Die folgenden Namen beschreiben eine interne Schnittstelle und sind KEINE Behauptung über vorhandene CLI-Commands:

- `browser.discover`: verfügbare Backends, Sitzungen und Fähigkeiten ermitteln.
- `browser.attach`: gezielte vorhandene Sitzung verbinden; keine implizite Neuanlage.
- `browser.tabs`: Tabs mit stabilen IDs, URL, Besitzer und Run-Zuordnung auflisten.
- `browser.observe`: kompakter DOM-/Accessibility-Ausschnitt oder Screenshot; Beobachtungsrevision zurückgeben.
- `browser.act`: typisierte Navigation, Klick, Eingabe, Scrollen, Auswahl, Drag-and-drop, Upload/Download und Warten auf konkrete Zustände.
- `browser.read`: relevante gerenderte Inhalte mit URL, Zeit und Quellenbezug ausgeben.
- `browser.batch`: geprüfte Folge mit Abhängigkeiten, Abbruchpunkten und Einzelergebnissen ausführen.
- `browser.verify`: erwarteten Seiten-/Anwendungszustand unabhängig vom Exitcode prüfen.
- `browser.detach`: Verbindung lösen; fremde Browser nicht schließen.

Nur tatsächlich unterstützte Operationen anbieten. Jeder Aufruf enthält Run-ID, Session-ID, Tab-ID, Deadline und Abbruchsignal. Ergebnisse enthalten Status, tatsächliches Backend, Dauer, kurze Vorschau, Belegreferenzen und gegebenenfalls Fehlerklasse. Große Inhalte als lokales Artefakt mit selektiv lesbaren Abschnitten ablegen.

Tool-Argumente strukturiert übergeben; keine ungeprüften Modelltexte in Shell-Kommandos interpolieren. Batch-Aktionen einzeln klassifizieren und genehmigen. Skills erklären die Bedienung, ersetzen aber keine Laufzeitprüfung.

## 5. Geschwindigkeit und korrektes Batching

- Browserverbindung und Werkzeugprozess zwischen Aufrufen wiederverwenden.
- Standardmäßig kleine, relevante Snapshots anfordern. Interaktive Elemente für Bedienung; lesbarer Artikeltext für Recherche. Ein reiner interaktiver Snapshot enthält nicht automatisch alle fachlichen Inhalte.
- Referenzen immer an Session, Tab und Beobachtungsrevision binden. Nach Navigation oder relevanter DOM-Veränderung neu beobachten. Keine Elementnummern aus einem früheren Lauf hardcoden.
- Vision beim Einstieg, bei visuellen Unklarheiten, Canvas, nativen Dialogen und visuellen Abnahmen einsetzen; nicht nach jedem einfachen DOM-Lesezugriff.
- Bereits geprüfte unabhängige Leseaufgaben parallel ausführen. Aktionen im gleichen Tab serialisieren. Bei Backends mit gemeinsamem aktivem Tab auch Tabwechsel und Folgeaktion gemeinsam sperren.
- Ein CLI-Batch ist keine Garantie paralleler Ausführung. Abhängige Schritte bleiben geordnet. Ein Snapshot kann nicht sinnvoll eine vorab geratene Klickreferenz legitimieren.
- Lange Aufgaben streamen Start, Fortschritt und Resultate jedes Teilschritts. Der Batch darf für TUI, Stop und Approval keine Blackbox sein.
- Wiederholungen nur bei definierten temporären Fehlern und mit begrenztem Budget. Nach unbekanntem Ausgang erst Zustand prüfen; irreversible Aktionen nicht blind wiederholen.

Performance messen: Kaltstart und warme Aufrufe getrennt; p50/p95 Tool-Latenz, Task-Gesamtzeit, Modellaufrufe, Kontextgröße, erfolgreiche Aufgaben und unnötige Aktionen. Zunächst Baseline erfassen. Keine ungemessenen „10x schneller“-Versprechen.

## 6. Fensterflut zuverlässig verhindern

Eine Sessionverwaltung verwaltet Browserprozess, Hostfenster, Tab, Backend und Eigentümer getrennt. „Fenster nicht beobachtbar“ ist nicht gleich „Browser existiert nicht“.

Zustandsfolge: entdecken → eindeutig verbinden → Bereitschaft prüfen → Aufgabe ausführen. Nur wenn keine passende Instanz existiert und Neuanlage zur Aufgabe passt, einmal starten. Während des Starts verhindert ein Lock parallele Starts. Ein Starttimeout führt zur Diagnose und Zustandsabstimmung, nicht zum nächsten identischen Launch.

Nach Tool-/Daemon-Neustart vorhandene Ressourcen neu zuordnen, bevor neue erzeugt werden. Vom Nutzer geöffnete Tabs standardmäßig erhalten. Vom Agenten erzeugte Tabs markieren; nur entsprechend ihrer tatsächlichen Nutzung und Aufgabe schließen. Kein globales `close --all` als allgemeines Cleanup.

## 7. Subtools und gemischte Workflows

Erfolgreiche Abläufe in parametrisierte Subtools überführen: Eingabeschema, erforderliches Backend/Profil, Vorbedingungen, Schritte, Postbedingungen, Effekte, Approval-Bedarf, Testbelege und Version.

Beispiel „neueste Proton-Mails lesen“: passende vorhandene Sitzung finden → bereits offenen passenden Tab wiederverwenden → Loginzustand feststellen → Inbox öffnen → relevante Mails strukturiert lesen oder GUI verwenden → Ergebnis verifizieren. Das Subtool gibt eine kompakte Antwort und Belege zurück. Es startet nicht jedes Mal einen Browser und durchläuft keine überflüssigen Fehlversuche.

Nicht rohe Klickkoordinaten als dauerhafte Wahrheit speichern. Semantische Ziele zur Laufzeit neu auflösen. Wichtige Wartebedingungen bleiben erhalten. Veränderte Seiten führen zur kontrollierten Reparatur mit erneuter Prüfung. Helfercode darf automatisch entstehen, wird aber vor produktiver Registrierung getestet und versioniert. Keine realen Käufe oder Nachrichten zum Testen duplizieren.

COMPOSE darf Browser-Subtools mit Docker, MCP und normalen Tools verbinden. Ein Stock-Workflow kann Quellen lesen, Daten im Container berechnen und sein localhost-Dashboard im Browser testen. Alle Schritte verwenden dieselben Run-, Approval- und Evidence-Regeln.

## 8. Kontrolle, UI und Verifikation

- Host-GUI-Eingaben brauchen die vorhandene exklusive Desktopkontrolle samt Warteschlange und sichtbarem Indikator. DOM-Zugriffe benötigen mindestens passende Session-/Tab-Sperren und dürfen mit menschlichen Änderungen nicht unbemerkt konkurrieren.
- Globales Doppel-Escape stoppt weitere Browser-/GUI-Aktionen des betroffenen Runs, verwirft wartende Folgeaktionen und hält ihn pausiert. Bereits ausgelöste Netzwerkwirkungen lassen sich dadurch nicht garantiert zurücknehmen; anschließend Zustand feststellen.
- Ask Everything, Approve for me und Approve Everything gelten gleichermaßen für Browser, Batches und gelernte Subtools. Geld und irreversible Schäden behalten die vereinbarten Approval-Grenzen.
- Webseiten, DOM, WebMCP-Metadaten und Tool-Ergebnisse sind untrusted Input, keine Nutzerfreigabe. Ein öffentlich angebotenes Seitentool darf Approval nicht selbst umgehen.
- CLI zeigt Backend/Session, kurze Aktion, Dauer, Ergebnisvorschau und aufklappbare Belege. Beispiel: `browser.read · Zen / tab … · 3 sources extracted`. Fehlende Fähigkeit klar rot mit Text melden.
- VERIFY prüft den konkreten Zustand: gelesene richtige Seite, abgeschlossener Download, tatsächlich sichtbare UI-Funktion. Exitcode 0 oder ein vorhandener Tab beweist keine erledigte Aufgabe.
- Jev hilft beim Routing und Kompaktieren. Voice wird bei echter Abweichung/Blockade tätig; keine zusätzliche Supervisor-Modellrunde pro Klick.

## 9. Setup-Auftrag an Hermes

1. Aktuellen Stand inventarisieren; Anforderungen dieses Dokuments mit Codepfaden und Tests abgleichen.
2. Geeignete agent-browser-Version und Browserruntime auswählen, exact pinnen und Lizenz/Version dokumentieren. Keine unversionierte `latest`-Installation bei jedem Start.
3. Abhängigkeiten isoliert zum Projekt installieren; Fedora-Pakete und Architektur tatsächlich prüfen. Nicht annehmen, dass ein Linux-Installer automatisch Fedora vollständig unterstützt.
4. Capability-Probe für Sessionanschluss, JSON-Ausgabe, Snapshots, Batch-Abbruch und ownership-sicheres Detach implementieren. Setup-Fehler sichtbar ausgeben, keine stillen Download-/Startschleifen.
5. Nur den nötigen Werkzeugumfang registrieren; Browserskills bei Bedarf laden. Bestehende Registry und Events nutzen.
6. Host-Zen-Pfad separat prüfen. Unverifizierte Fähigkeiten als unbekannt oder blockiert markieren.
7. Nach vertikalem Prototyp entscheiden, ob Browser Harness einen konkreten Vorteil bringt. Entscheidung mit Aufwand, Testresultaten und gemessenen Laufzeiten begründen; keinen zweiten Stack ohne Nutzen hinzufügen.

Die Recherche hat nichts installiert und keine persönlichen Browser-Debugging-Einstellungen verändert. Diese Schritte sind der Implementierungsauftrag, kein Bericht bereits erfolgter Arbeit.

## 10. Abnahmetests

| ID | Szenario | Erwartetes Ergebnis |
|---|---|---|
| BR-01 | Zen bereits offen; zehn wiederholte „Browser bereitstellen“-Aufrufe | Keine zusätzlichen Browserfenster; vorhandene Instanz korrekt wiederverwendet. |
| BR-02 | Fensterbeobachtung fällt aus | Diagnose statt erneuter Launch-Serie; kein falscher Erfolgsstatus. |
| BR-03 | Strukturierte Recherche auf mehreren Seiten | Tabs korrekt zugeordnet, Inhalte und Quellen gelesen, Scroll-/Navigationsaktionen überprüft. |
| BR-04 | Loginaufgabe ausdrücklich in vorhandenem Zen | Echte Sitzung verwendet; kein stiller isolierter Ersatz; Login-Daten nicht geloggt. |
| BR-05 | Zwei parallele Tab-Aufgaben | Kein Klick oder Navigation im falschen Tab; Konflikte sichtbar serialisiert. |
| BR-06 | Veraltete Elementreferenz | Frische Beobachtung oder verständlicher Fehler, kein blindes Wiederholen. |
| BR-07 | Batch scheitert im mittleren Schritt | Ausgeführte, fehlgeschlagene und nicht gestartete Schritte getrennt gemeldet. |
| BR-08 | Doppel-Escape außerhalb der TUI während Batch/GUI | Weitere Aktionen stoppen; Run pausiert; kein automatischer Resume. |
| BR-09 | Daemon/CLI-Abbruch nach Aktion mit unklarem Resultat | Zustand abgestimmt; keine doppelte Bestellung/Nachricht und keine neue Fensterflut. |
| BR-10 | Gelerntes Subtool bei verändertem Layout | Ziele neu aufgelöst, Ergebnis geprüft; Reparatur oder ehrlicher Blocker. |
| BR-11 | Upload/Download, Dialog, Drag-and-drop, Canvas | Passender strukturierter oder Host-/Vision-Pfad; tatsächliches Ergebnis belegt. |
| BR-12 | Gesperrte kostenauslösende Aktion innerhalb Subtool | Run wartet auf konkrete Approval; Batch umgeht sie nicht. |
| BR-13 | Beenden einer angehängten Sitzung | Nutzerbrowser und fremde Tabs bleiben offen. |
| BR-14 | Browser → Docker-Auswertung → localhost-Dashboard | Gesamter Workflow samt visueller UI-Abnahme belegt. |
| BR-15 | Wiederholbare einfache Aufgabe alt/neu | Zeit, Tokens, Aufrufe und Erfolgsquote dokumentiert; kein Geschwindigkeitsgewinn durch ausgelassene Verifikation. |

Hosttests unter Fedora X11 und Wayland getrennt protokollieren. Chromium-Container-Tests ersetzen weder Zen- noch Hosttests. Passende Regressionen nach Änderungen; keine endlos identisch wiederholten Gesamtsuiten.

## 11. Primärquellen und Nachweisgrenzen

Am Recherchetag mit dem Browser-Plugin gelesene Originaldokumentation:

- https://github.com/vercel-labs/agent-browser — Installation, CLI, Batch, Sessions, CDP, JSON und Lizenz.
- https://github.com/microsoft/playwright-mcp — MCP, Profile und Chrome-/Edge-Extension.
- https://playwright.dev/docs/browsers — gepatchte Firefox-Version und Browsergrenzen.
- https://github.com/browser-use/browser-harness — lokale CDP-Verbindung, Helfer, Skill, MCP und MIT-Lizenz.
- https://github.com/browser-use/browser-use — Unterscheidung CLI, lokalem Agenten und Cloud-Angeboten.

README-Funktionen sind Angaben der Projekte. Versionen, tatsächliche Fedora-Kompatibilität, Durchsatz, Stop-Verhalten und Zen-Anbindung müssen vor einer Produktzusage lokal bestätigt werden.
