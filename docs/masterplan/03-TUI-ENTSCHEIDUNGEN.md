# Vollbild-TUI: bestätigtes Design und offene Details

Stand: 25. September 2026. Diese Datei enthält bewusst getrennt Nutzerentscheidungen und Vorschläge. Sie ergänzt UI-01 bis UI-08 des Masterplans. Offene optische Entscheidungen blockieren nicht die technische Umsetzung.

## 1. Vom Nutzer bestätigt

- Vollbild-TUI mit getrennten Bereichen für **Chat, Tools und Goal**.
- Live sichtbarer, fortlaufender Feed mit echten Tool Calls und Ergebnissen.
- Schnelle, saubere Ausführung und kurze Gesamtzeit; längeres sinnvolles Reasoning erlaubt.
- **Dezente animierte Figur, gelegentlicher Humor; überwiegend monochrom, Fehler rot.**
- Approval-Modi im Chat per Tab umschaltbar.
- Klarer roter Error-/Blockerhinweis mit verständlichem Grund und nächster nötiger Handlung.
- Sichtbare Host-Desktop-Steuerung auch außerhalb der TUI.
- Globaler Doppel-Escape pausiert die Desktoparbeit auch bei anderer fokussierter Anwendung.
- Schließen der TUI ist nicht Stop; aktive Hintergrundarbeit und Reattach sichtbar.

Der neu bestätigte Farbwunsch präzisiert die ältere monochrome Projektregel: überwiegend monochrom, Rot für Fehler. Farbe niemals als einziges Signal. Bestehende englische Produkttexte vorerst beibehalten; deutsche Planprosa ist davon unabhängig.

## 2. Bestätigte Tool-Detailtiefe

**Bestätigt:** Jeder Call zeigt eine kurze Ergebnisvorschau; Argumente und vollständige Ausgabe sind aufklappbar. Die Varianten unten sind optionale Ansichten, keine offene Grundsatzfrage:

A. Jeder Call mit kurzer Ergebnisvorschau; Argumente und vollständige Ausgabe aufklappbar.
B. Alle Argumente und Ergebnisse direkt im Verlauf.
C. Batches zusammengefasst; einzelne Calls in Details.

A ist die bestätigte Standarddarstellung. B und C können optional angeboten werden. Große Ausgaben benötigen in jedem Modus Pagination/Artefaktzugriff statt unbeschränktem Rendern.

## 3. Layoutvorschlag

Bestätigt für breite Terminals: links großer Hauptchat, rechts oben Tools, rechts unten Goal. Unten feste Eingabe und Statusleiste. Größen an Terminal und Nutzerkonfiguration anpassen.

```text
┌ SKALDIR · project · run #42 ──────────────────────────────────────┐
│ CHAT                                  │ TOOLS                   │
│ You: Research orbital data centers    │ #18 web_search      ✓   │
│                                      │ #19 page.read       ◐   │
│ Skaldir: Comparing sources …          │ #20 page.read       ✓   │
│                                      │ Batch 2/3 complete      │
│ [streaming answer]                    ├ GOAL ────────────────────┤
│                                      │ ✓ Sources visited       │
│                                      │ ○ Compare claims        │
│                                      │ ○ Verify report         │
├──────────────────────────────────────┴──────────────────────────┤
│ › Your message …                                                │
├ Approval: Approve for me · Desktop: run #42 · elapsed 01:24 ──────┤
└─────────────────────────────────────────────────────────────────┘
```

Beispieldaten, kein Laufzeitnachweis. Bei schmalem Terminal gestapelte oder wechselbare Bereiche mit denselben Daten. Unter einer definierten Mindestgröße kompakte Ansicht statt defekter Rahmen. Plain-Modus für Pipes/Screenreader bleibt erhalten.

### Chat

Nutzeraufträge, knappe Arbeitsupdates und Antworten als lesbares Markdown. Große Tool-Rohdaten nicht zwangsläufig in Chat duplizieren. Anhänge/Artefakte mit tatsächlichem Pfad oder Referenz erreichbar machen. Lange Texte umbrechen, Code kopierbar halten.

### Goal

Originalziel oder kurze eindeutig zugeordnete Anzeige, aktuelle Phase, Abschlusskriterien, offene Punkte, Blocker und Runstatus. Aufklappbar für Details/Revision. Kein Erfolgssymbol, bevor VERIFY/ENFORCE Erfolg bestätigt.

### Tools

Stabile Call-ID, verständliche Aktion, Ziel, laufender/abgeschlossener Zustand, Dauer, Resultat und Zugriff auf Details. Batch als Elternzeile, Einzelergebnisse sofort anzeigen. Subtool als zusammengefasster Lauf mit aufklappbaren Schritten und fachlichem Endresultat. Fehlende/ausgelassene Ausgabe klar markieren.

## 4. Status und Interaktion

Ein einziges Eventmodell speist alle Bereiche. Reattach setzt mit Sequenzcursor fort; doppelte Events duplizieren keine Calls. Darstellung darf nie den fachlichen Zustand selbst erfinden.

- Auto-Follow nur solange Nutzer am Ende ist. Beim Scrollen nach oben „N neue Events“ und Rückkehr zu Live anbieten.
- Eingabetext bleibt bei Streaming, Resize, Fehler und Panewechsel erhalten.
- Mehrzeiliges Paste als Textblock, kein versehentliches Abschicken einzelner Zeilen.
- Tool-Argumente, stdout, stderr, Exitcode und Verifikation getrennt in Details.
- Fehlertext zeigt Ursache oder ehrlich Hypothese; kein „Backendfehler“ ohne Beleg.
- Footer enthält sinnvolle verfügbare Werte; fehlende Kosten/Context-Messung ausdrücklich unbekannt, nicht 0.
- Hintergrundrun beim Reattach eindeutig identifizieren; mehrere Runs auswählbar.

### Bestätigte Approval-Bedienung

Bestätigt: Tab bei leerem Eingabefeld wechselt den Approval-Modus; bei aktiver Vervollständigung bedient Tab die Vorschläge.

Tab bei leerem Prompt und geschlossenem Menü wechselt den sichtbaren Modus; während aktiver Completion bedient Tab die Completion. Ein eigener fokussierbarer Modusschalter bietet eine eindeutige Alternative. Panewechsel erhält andere dokumentierte Tasten. Keine überraschende Policyänderung durch Completion-Tastendruck.

Ask Everything, Approve for me und Approve Everything vollständig ausschreiben oder eindeutig abkürzen. Beim Moduswechsel weiterhin feste Geld-/Irreversibilitätsausnahmen sichtbar erreichbar machen. Keine Bestätigungsdialoge allein für einen gewöhnlichen erlaubten UI-Moduswechsel erfinden.

### Stop und Detach

- Doppel-Escape: globaler Desktop-Notstopp, Run pausiert dauerhaft, auch ohne TUI-Fokus.
- Explizite Pause/Stop-Schaltfläche: betroffenen Run anhalten.
- Detach/Quit TUI: eindeutig sagen, dass der Run weiterläuft; kein versteckter Cancel.
- Resume: bewusste Nutzeraktion nach Stop, Zustand/Preconditions erneut prüfen.
- Ausführungsabbruch erst als abgeschlossen anzeigen, wenn bestätigt; bis dahin „Stopping“.

## 5. Figur und Bewegung

Bestätigter Stil ist dezent. Bestehende Skaldir-Figur weiterentwickeln, nicht beliebig ersetzen.

- Kleiner aktiver Avatar oder Spinner; gelegentliches Blinzeln/leichte Bewegung.
- Kurze humorvolle Warteakzente selten und nur bei normaler Arbeit.
- Fehler, Approval und Datenverlusthinweise sachlich formulieren.
- Eine globale aktive Animation statt eigener Spinner für hunderte Calls.
- Vorschlag 4–8 Frames/s; keine Modellaufrufe pro Animationsframe.
- Keine blinkenden Vollbildfarben, Glockentöne oder Fake-Fortschrittsprozente.
- Motion-off und ASCII-Fallback anbieten. NO_COLOR deaktiviert Farbe, nicht automatisch Bewegung.

## 6. Desktop-Indikator außerhalb der TUI

Kleiner Host-Indikator während tatsächlicher exklusiver Steuerung:

```text
Skaldir controls the desktop
Task: Edit video
Esc, Esc to pause
```

Bestätigte Position/Darstellung: kleine Statusleiste in einer Ecke. Die konkrete Hostintegration an Fedora-Backend und Skalierung anpassen. Indikator soll Zielanwendung und wichtige UI-Elemente möglichst nicht verdecken, keinen Fokus stehlen und zum Backend passen. Ein Terminalfooter allein erfüllt den Wunsch nicht. Zustände: steuert / wartet / pausiert; „steuert“ nur bei gültiger Lease.

## 7. TUI-Abnahme

- Echte Fedora-Terminalprüfung bei mehreren Größen; X11/Wayland-Indikator getrennt.
- Zwei parallele Tools und laufender Textstream ohne Eingabeverlust.
- Langsame Modellantwort mit ehrlichem Status, kein vorgetäuschter Toolfortschritt.
- Subtool mit zehn internen Schritten übersichtlich und vollständig inspizierbar.
- Ein Fehler zwischen erfolgreichen Calls bleibt sichtbar, nicht durch grüne Batchfarbe verborgen.
- Stop-/Detach-/Resumeverhalten eindeutig; Doppel-Escape außerhalb TUI geprüft.
- Secret-/Control-Sequence-Redaktion, Plain/JSONL-Ausgabe und begrenzte Puffer.
- Snapshot-/Fake-Clock-Tests für Renderer; echte PTY-Integration für Eingabe und Resize.

## 8. Bestätigung vom 25. September 2026

Alle vier zuletzt gestellten UI-Fragen sind beantwortet: Vorschau mit aufklappbaren Details; Chat links, Tools oben rechts, Goal unten rechts; Tab im Leerfeld für Approval-Modi und bei Completion für Vorschläge; kleine Statusleiste in einer Ecke.

Dies ist eine Terminaloberfläche: Ein normaler Aufruf von `skaldir` öffnet die Vollbild-TUI. Kein separates grafisches Hauptfenster. Nichtinteraktive CLI-Commands, Hilfe, JSON und Pipe-Ausgabe bleiben erreichbar. Vorhandene Shell/Chat-Einstiege dürfen kompatible Aliasse bleiben.
