# Browser-Upgrade für Skaldir

Stand: 25. September 2026. Recherche und Umsetzungsvorschlag, keine bestätigte Installation oder Laufzeitabnahme. Ergänzt den Masterplan; verändert weder den Wunsch nach dem echten Host-Desktop noch die Approval-Regeln. Gegen bestehende Implementierung abgleichen, funktionierende Komponenten behalten.

## 1. Von Willy bestätigte technische Empfehlung

**Empfehlungsgeber:** Codex als technischer Architekturberater auf Grundlage der oben dokumentierten Recherche; von Willy ausdrücklich bestätigt und als gemeinsame Empfehlung an Hermes beauftragt. Dies bezeichnet die fachliche Empfehlung im Projekt, keine externe Zertifizierung.

**An Hermes:** Behandle diese Empfehlung als bevorzugte Umsetzungsrichtung: agent-browser lokal, versioniert und beim Setup mitinstalliert integrieren; den bestehenden ACT-/Computer-Use-Pfad für Zen und native Dialoge erhalten. Prüfe den vorhandenen Code und implementiere die Integration gemäß den folgenden Paketen und Abnahmen. Weiche nur bei einem konkret belegten technischen Nachteil oder einer besseren vorhandenen Lösung ab und dokumentiere Begründung und gleichwertige Erfüllung der Nutzeranforderungen. Zen-Kompatibilität und Fedora-Lauffähigkeit weiterhin tatsächlich testen.

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

## 12. Konkrete Prüfpunkte im inzwischen vorhandenen Zielcode

Lesender Stichprobenaudit am 25. September 2026, HEAD `89b02a7209a6b1bae0b2e1fdb0eea363527925ad`. Arbeitsbaum kann zusätzliche Änderungen enthalten. Zeilen sind Orientierung für diese Momentaufnahme, keine unveränderlichen Referenzen. Kein vollständiger Laufzeitaudit; insbesondere keine Live-Desktop-Tests in dieser Recherche ausgeführt.

| Beobachteter Code | Bedeutung und Auftrag |
|---|---|
| `src/skaldir/act_service.py:148–156`, `BROWSER_TITLE_RE`, `BROWSER_LAUNCH` | Breite Titel-/Prozessmuster und `xdg-open` sind vorhanden. Sie identifizieren weder zuverlässig eine bestimmte Nutzersitzung noch beweist URL-Öffnen Idempotenz: auch eine zusätzliche Registerkarte ist ein Seiteneffekt. `ensure_ready` von `navigate` trennen; Launch nicht mit erneutem Öffnen von `about:blank` gleichsetzen. |
| `src/skaldir/act_service.py:2028–2082`, eingebautes `open_browser` | Vorbedingung `browser_absent`; danach Shell-Start, feste Wartezeit, Ausgabe `LAUNCHED`, Fokusprüfung. Der Flow ist damit kein allgemeines „vorhandenen Browser sicher bereitstellen“. Aufrufer und Vorbedingungsbehandlung verfolgen; vorhandene Instanz muss einen eigenen erfolgreichen Attach-/Reuse-Pfad haben. |
| `src/skaldir/act_service.py:2058` | Hintergrundstart und anschließendes `echo LAUNCHED` belegen nur die Shell-Ausgabe. Startfehler und Bereitschaft unabhängig prüfen; Prozess-/Verbindungsnachweis und Ergebnisprüfung getrennt halten. Nicht behaupten, die spätere Endzustandsprüfung existiere nicht. |
| `src/skaldir/cu_host_session.py:1368–1400`, `_child_env` | Entfernt `WAYLAND_DISPLAY` und setzt unter anderem `MOZ_ENABLE_WAYLAND=0`, `GDK_BACKEND=x11`. Dies beeinflusst gestartete Kinder; es macht einen bereits laufenden nativen Wayland-Browser nicht nachträglich per X11 steuerbar. Bestehende native Sitzung separat abnehmen. Kommentare über Umgehung von Portaldialogen sind kein Nachweis vollständiger Wayland-Unterstützung. |
| `src/skaldir/verify_flows.py:850–900`, `browser_availability`, `_BrowserSession` | Vorhandener Playwright-Prüfpfad startet Chromium mit neuem Context. Für isolierte UI-Regressionsprüfungen wiederverwenden; nicht als Nachweis einer persönlichen Host-Sitzung darstellen. Verfügbarkeit per Import ist schwächer als ein erfolgreicher Start. |
| `src/skaldir/adapter_mcp.py`, `MCPClient`, `validate_payload`, `call_mapped_tool` | Vorhandener MCP-Client, Schemaverarbeitung und Toolaufruf sind Integrationskandidaten. Erst prüfen, dann erweitern; keine zweite vollständige MCP-Schicht bauen. |
| `src/skaldir/tool_archive.py`, `ToolArchive`, `ArchivedResult` | Vorhandene Archivierung für große Browserergebnisse prüfen und möglichst verwenden. Keine parallele Browser-eigene Memory-Datenbank ohne begründete Lücke. |

Vorhandene Tests als Ausgangspunkt: `tests/test_act_service.py`, `tests/test_act_service_container.py`, `tests/test_adapter_mcp.py`, `tests/test_adapter_mcp_cli.py`, `tests/test_verify_flows.py`. Existenz dieser Dateien bedeutet nicht, dass die neuen Anforderungen bereits getestet sind.

## 13. Minimale Session- und Auftragsverträge

Die folgenden Felder sind ein Zielvertrag. Bestehende gleichwertige Datenmodelle bevorzugen und auf diese Semantik abbilden.

### Session

- `session_id`: stabile Skaldir-ID; nicht allein ein Fenstertitel.
- `backend`: tatsächlich benutzter Adapter, beispielsweise `agent_browser` oder `host_gui`.
- `browser_identity`: Browserfamilie, identifizierte Instanz und lokal verwaltete Profilreferenz; keine Cookies oder Secrets.
- `ownership`: `user_owned` oder `agent_owned`; zusätzlich Besitzer jedes Tabs getrennt erfassen.
- `scope`: `host_existing` oder `managed`; für Nutzer sichtbar.
- `connection_state`: `discovered`, `attaching`, `ready`, `degraded`, `disconnected`.
- `capabilities`: einzeln bestätigte Funktionen, etwa `dom_read`, `screenshot`, `tab_targeting`, `native_input`, `download`.
- `last_observed_at`, `connection_generation`: Zeit und Verbindungsrevision; nach Neuverbindung alte Referenzen invalidieren.

Laufzustand separat halten: Ein Run kann pausiert sein, während der Browser weiterhin verbunden ist. Pausieren darf weder das Nutzerfenster schließen noch einen neuen Run automatisch starten.

### Einzelaktion

```json
{
  "run_id": "run-example",
  "action_id": "action-0042",
  "session_id": "session-existing",
  "tab_id": "tab-source-a",
  "operation": "read",
  "arguments": {"scope": "article", "max_chars": 12000},
  "observation_revision": "obs-0017",
  "timeout_ms": 15000
}
```

Beispielwerte, keine vorgeschriebenen globalen Limits. Der Executor ergänzt vertrauenswürdig Ressourcenbedarf, Effektklasse und Approval-Entscheidung; diese Eigenschaften nicht ungeprüft aus Modellargumenten übernehmen.

Ergebnisfelder: `action_id`, `status`, `effect_state`, `summary`, `evidence_refs`, `duration_ms`, `error`. Statuswerte mindestens `succeeded`, `failed`, `blocked`, `cancelled`, `unknown`. `effect_state` unterscheidet `not_started`, `confirmed`, `uncertain`; ein fehlgeschlagener Transport kann eine dennoch ausgeführte Aktion bedeuten. Inhaltliche Zielerreichung wird zusätzlich von VERIFY bewertet.

### Batch

Eine Liste von Aktionen mit `depends_on`, stabilen IDs und expliziten Ressourcen. Der Scheduler leitet erlaubte Parallelität aus Backendfähigkeiten und Konflikten ab. Das LLM darf sie vorschlagen, aber keine Locks ausschalten.

Ein fehlerhafter Pflichtschritt verhindert seine abhängigen Folgeschritte. Unabhängige Reads können nach Run-Policy weiterlaufen. Stop pausiert den gesamten betroffenen Run. Vor folgenreichen Schritten zwingender Approval- und Abbruchcheckpoint. Wenn der externe Batchmodus solche Unterbrechungen nicht ermöglicht, in kleinere kontrollierte Aufrufe zerlegen.

Ein lokaler `action_id` verhindert versehentliche erneute Einplanung. Er ist keine Exactly-once-Garantie für fremde Webseiten. Bei ungewissem Ausgang prüfen oder gezielt nachfragen statt Wiederholung erzwingen.

## 14. Fehlerklassen und erlaubte Reaktionen

| Fehlerklasse | Reaktion |
|---|---|
| `CAPABILITY_UNAVAILABLE` | Fehlende Fähigkeit benennen; passenden vorhandenen Backendpfad auswählen, wenn Browser-/Profilwahl gewahrt bleibt. |
| `OBSERVATION_UNAVAILABLE` | Beobachtung diagnostizieren, Session erhalten; nicht aus fehlender Sichtbarkeit Abwesenheit ableiten. |
| `TARGET_STALE` | Neu beobachten und Ziel neu auflösen; begrenzter Wiederholungsversuch. |
| `SESSION_DISCONNECTED` | Bekannte Session wiederverbinden und Zustand abgleichen; kein automatischer neuer Browser. |
| `NAVIGATION_TIMEOUT` | Aktuelle URL und Lade-/Fehlerzustand lesen; mögliche Teilwirkung feststellen. |
| `AUTH_REQUIRED` | Login-/2FA-Bedarf benennen; vorhandenen zulässigen Authentifizierungspfad verwenden, andernfalls User-Aufgabe anzeigen. |
| `HUMAN_INTERFERENCE` | Bei konkurrierender Navigation/Eingabe stoppen und neu beobachten; gegebenenfalls Desktopkontrolle übernehmen. |
| `APPROVAL_REQUIRED` | Aktion nicht ausführen; konkrete Wirkung und Ziel anzeigen. |
| `EFFECT_UNCERTAIN` | Ergebnis abgleichen; keine automatische Wiederholung folgenreicher Aktion. |
| `USER_STOP` | Neue Aktionen sperren, Queue pausieren und Stop persistent speichern. |

Retries pro Aktion und Wiederholung gleicher Fehler über den Run hinweg zählen. Ein neues Tool-Call-ID darf das Wiederholungslimit nicht zurücksetzen. Gleiche Aktion bei gleichem Zustand ohne neue Evidenz als Schleife melden.

## 15. Umsetzung in überprüfbaren Paketen

### Paket A: Inventar und Fensterlebenszyklus

1. Aktive Aufrufpfade vom Chat über Tool-Registry bis Browserstart verfolgen; auch Subtools, Workflows und CLI einbeziehen.
2. Bestehenden Browserstatus von „Browser öffnen“ trennen. Explizite Fälle: vorhanden, nachweislich abwesend, unbekannt.
3. Start-Lock und begrenzte Bereitschaftsprüfung integrieren. Bei unbekanntem Zustand keinen Relaunch.
4. Regressionen BR-01, BR-02 und BR-13 zunächst kontrolliert, anschließend am echten Host prüfen.

Abnahme: Bereits geöffneter Zen wird wiederverwendet. Bei absichtlich fehlender Beobachtung entstehen keine zusätzlichen Browserfenster oder leeren Tabs. Kann der Hostpfad das noch nicht leisten, Blockade dokumentieren und Paket B unabhängig bearbeiten.

### Paket B: Ein vertikaler strukturierter Browserpfad

1. Gepinnte agent-browser-Version im Zielprojekt bereitstellen.
2. Eine zulässige Session verbinden oder explizit verwaltet starten.
3. Eine lokale Testseite öffnen, Formular ausfüllen, navigieren, Text lesen, Screenshot erstellen und Ergebnis überprüfen.
4. Jede Aktion durch Skaldirs Registry, Approval, Events und Archiv führen. Keine separate Demo außerhalb des eigentlichen Agentenpfads als Integration verkaufen.
5. Abbruch und Detach testen, bevor weitere Bedienfunktionen erweitert werden.

Abnahme: Nutzerprompt startet den kompletten Ablauf im normalen Skaldir; TUI zeigt Einzelschritte und Belege; kein zusätzlicher autonomer Browser-Agent nötig.

### Paket C: Tabs, Batches, Lesepfad und Performance

Drei unabhängige Quellen lesen, schrittweise Resultate streamen und einen absichtlich veralteten Selektor behandeln. Aktiver Tab darf keine falsche Zielseite verursachen. Leseumfang begrenzen, Originalbelege selektiv abrufbar halten. Kalte und warme Laufzeiten vergleichen; Erfolgsquote mindestens bewahren.

### Paket D: Host/DOM-Wechsel und Wiederaufnahme

Native Dialoge und Canvas zum Host-/Vision-Pfad leiten. Browser-/Tabidentität beim Wechsel bewahren. Unter X11 und Wayland separat testen. Crash nach Absenden einer harmlosen Testformularaktion simulieren; unbekannte Wirkung feststellen statt zweimal absenden. Globalen Notstopp während langer Aktionen prüfen.

### Paket E: Ein gelerntes Browser-Subtool

Aus dem verifizierten lokalen Formular-/Rechercheablauf ein Subtool erstellen, unnötige Wiederholungen entfernen, Eingaben parametrisieren und mit geänderter Testseite prüfen. Automatische Wiederverwendung aus einem normalen Prompt nachweisen. Persönliche E-Mails erst danach als reale Aufgabe bearbeiten; keine Inhalte in Testfixtures oder Git übernehmen.

### Paket F: Produktabnahme

Browserrecherche auf dem gewünschten Hostbrowser, anschließend Stock-Research-Workflow mit Containerverarbeitung und localhost-Dashboard. Prüfbericht trennt strukturierte Browserabnahme, Zen-Hostabnahme, X11 und Wayland. Fehlende Teilabnahme bleibt offen, auch wenn die Gesamt-Testsuite grün ist.

## 16. Copy-paste-Auftrag für Hermes

> Lies AGENTS.md, den Transferauftrag und docs/masterplan/00-START-HIER.md. Setze anschließend docs/masterplan/07-BROWSER-UPGRADE.md auf der bestehenden Implementierung um. Beginne mit dem tatsächlichen Codeaudit der in Abschnitt 12 genannten Pfade. Erstelle eine kurze Gap-Matrix für BR-01 bis BR-15 mit Codepfad, Nachweis und Restlücke. Verwende vorhandene Registry, ACT, MCP, Events und ToolArchive. Implementiere die Pakete A–F in überprüfbaren Schritten; vermeide einen Rewrite. Bevorzuge agent-browser als gepinntes lokales Werkzeug für unterstützte Browser, aber behaupte keine bestehende Zen-Anbindung ohne Live-Nachweis. Verhindere zuerst wiederholte Browserstarts, integriere danach einen vollständigen vertikalen Toolpfad mit Abbruch und Ergebnisprüfung. Beachte bestehende Browserprofile, globale Doppel-Escape-Pause, Approval-Grenzen und genaue Browserwahl. Teste passende Regressionen pro Änderung und die echten Produktabläufe nach Integration. Fahre bei isolierten Blockaden mit unabhängiger Arbeit fort. Dokumentiere den tatsächlich getesteten Stand, offene Grenzen, Laufzeiten und nächste Schritte; die bloße Erstellung dieser Dokumentation erfüllt den Implementierungsauftrag nicht.
