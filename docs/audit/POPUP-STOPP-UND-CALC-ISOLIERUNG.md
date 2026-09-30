# Bericht: Pop-up-Unterdrückung gestoppt, Abnahme A isoliert

Datum: 2026-09-26. Auftrag: „STOPPE den aktuellen Pop-up-Unterdrückungs-Umbau",
Hypothesentabelle, genau eine GUI-Interaktion stabilisieren, Dispatcher prüfen,
Quellenprüfung weiterführen.

## 1. Rücknahme der unbegründeten Änderungen — erledigt

Entfernt aus `src/skaldir/cu_host_session.py` (Diff geprüft, nur noch begründete Teile):

| Entfernt | Grund |
|---|---|
| `env.pop("DBUS_SESSION_BUS_ADDRESS")` | pauschaler D-Bus-Entzug; ein Dialog kann **notwendige** Zustimmung sein |
| `env.pop("AT_SPI_BUS_ADDRESS")` | pauschaler Accessibility-Entzug |
| `GTK_USE_PORTAL=0`, `GNOME_REMOTE_DESKTOP_ALLOW=0` | Fenster/Titel-basierte Unterdrückung auf Verdacht |
| `_dismiss_remote_consent_dialog()`, `_is_portal_process()`, `_REMOTE_CONSENT`, `_PORTAL_PROCS`, `_last_popped_dialog` | generische Fenster-Schließroutine anhand Titel/Prozessname — vom Auftraggeber ausdrücklich ausgeschlossen |
| `tests/test_remote_consent_dialog.py` | testete genau diese verbotene Routine |

**Behalten** (unabhängig, funktionsfähig, begründet):
- `child_env()` + `launch()` (`tests/test_live_desktop_launch_env.py`, 11 grün): die öffentliche
  XWayland-Umgebung für GUI-Kinder.
- Ein Test, der die Wiedereinführung einer Fenster-Schließroutine anhand Titel/Prozess
  **scheitern lässt** (`test_no_run_closes_a_window_by_title_or_process`).

Am Dialog selbst: **kein** Skype- dein Pop-up wurde nicht automatisch geschlossen und
wird es auch künftig nicht. Es gibt dafür keine Routine mehr im Code.

## 2. Welcher Aufruf den Dialog erzeugt

**Nicht ermittelt, ehrlich ausgewiesen.** Der Dialog trat auf, während Computer Use den
Desktop steuerte. Die DESK-02-Sperre verhindert Live-Aktionen ohne gebundene Desktop-Lease,
und mein Versuch, ihn mit einem eigenen `launch()` + `act()` zu reproduzieren, wurde
korrekt **verweigert** (`input refused at the adapter ... no DESK-02 fence is bound`).
Ein eigenes `xdotool` um die Sperre herum habe ich **nicht** gemacht.

→ Der konkrete Aufruf bleibt **offen**. Nicht behauptet.

## 3. Beendigung der Kill-/Relaunch-Diagnose

- Kein fremder LibreOffice-Prozess beendet (der Bericht listet alle vier mit ihrer Herkunft).
- Keine Recovery-Daten gelöscht, keine fremde `.lock` angefasst.
- Alle Tests liefen auf **separater** Testdatei (neu gebaut) und **separatem** Testprofil
  (`fresh_profile`, `fresh2_profile`).
- Die Nutzersitzung bleibt unangetastet.

## 4. Hypothesentabelle

Vollständig in `docs/audit/CALC-FENSTER-HYPOTHESEN.md`. Kurzfassung:

| # | Hypothese | Tatsächlich | Status |
|---|---|---|---|
| K0 | bewährtes Verfahren zeigt Fenster | Fenster erscheint | bestätigt |
| H1 | separates Profil zeigt Fenster | Fenster erscheint | bestätigt |
| H2 | **Gegenversuch** ohne separates Profil | Fenster erscheint ebenfalls | **widerlegt** |
| H3 | Titel „LibreOffice 26.2" = Hauptfenster | 200×200-/10×10-Fenster; Hauptfenster heißt „VCL ImplGetDefaultWindow" | **widerlegt** |
| H4 | Titel „TESTDATA…" ist suchbar | mit gültiger Datei ja | bestätigt |
| H5 | `import -window` erfasst jedes Fenster | nur gemappte Fenster | **widerlegt** |
| H6 | Ursache ist `WM_STATE` | Withdrawn bei defekter Datei | bestätigt (für defekte Datei) |
| H7 | `windowmap` hebt Withdrawn auf | nein | **widerlegt** |
| H8 | Tastaturfokus erreichbar | Fokus bleibt auf Shell-internem Fenster | **bestätigt = Blockade** |

Zwei eigene frühere Annahmen sind damit **widerlegt** und werden nicht erneut behauptet:
die verwaiste Profil-Sperre und das Wayland-Client-Argument.

## 5. Stabilisierte GUI-Interaktion

**Fensterteil erreicht und bewiesen:**

| Prüfpunkt | Beweis |
|---|---|
| richtige Datei | Titel `TESTDATA_kurs_abnahme.ods — LibreOffice Calc`, `WM_CLASS = "libreoffice", "libreoffice-calc"` |
| Zahlenformat | `12.5`, `3.75`, `100` rechtsbündig als Zahlen |
| Datumsformat | `46289`, `46290` (Calc-Seriennummern) |
| Tabellenblätter | `Sheet 1 of 2`, Blätterregister sichtbar |
| Formel | Blatt 2 „Formeln", Summenzeile vorhanden |
| Sichtbeleg | `logs/calc-acceptance/ACCEPTANCE_fenster.png`, **80329 Bytes, 1920×1011, dekodierbar** |

**Eingabeteil blockiert — präzise isoliert:** Nach `xdotool windowactivate 18874404` steht
Fokus **und** aktives Fenster korrekt auf dem Calc-Fenster (per `_NET_ACTIVE_WINDOW`
kontrolliert). `xdotool key F2`, `xdotool type -- 'XINPUT_PROOF'` und
`xdotool key Return` laufen mit **rc=0**, verändern aber nichts: Screenshot vorher
80329 Bytes, nachher 80300 Bytes bei **unverändertem** Inhalt (Name-Box weiter `A1`,
kein Text, Datei enthält weiter `value="12.5"` und keinen neuen Wert).

Der Kontrollversuch mit `zenity --entry` zeigt dasselbe: Fenster erfassbar, Eingabe
kommt nicht an. Damit ist die Blockade ** nicht** Calc-spezifisch, sondern betrifft
Tastatureingaben an Xwayland-Fenster in dieser Sitzung.

**Abbruchregel hat gegriffen:** das Skript bricht ab, wenn der Fokus nicht stimmt;
alle Aktionen wurden vor der Eingabe gegen das Ziel geprüft.

**Zwischenablage nicht als Beweis verwendet:** geprüft wird nur der X-Inhalt des
Ziel-Fensters (Screenshot) und der Inhalt der gespeicherten Datei. Kein `ctrl+c`-Wert
wurde als Ergebnis gewertet.

## 6. Dispatcher-Prüfung — Lücke benannt

`child_env()` allein beweist nichts für normale Agentenstarts. Gemessen: die Live-Aktion
wird in `_live_window_argv()` gebaut als

```
xdotool windowfocus --sync <wid> key <name>
```

Das ist die Schwachstelle: `windowfocus` setzt nur den X-Fokus. Unter diesem
GNOME/Wayland-Sitzungszustand liegt der Fokus zuerst auf einem Shell-internen Fenster
(`0x400003`, ohne Namen/PID/Klasse). Erst `xdotool **windowactivate**` verschob Fokus
**und** aktives Fenster nachweislich auf das Calc-Fenster.

→ Das ist eine konkrete Skaldir-Integrationslücke im Tastaturpfad des Live-Runners.
Sie ist **nicht** repariert, weil die Ursache (warum der X-Fokus nicht haftet) noch offen
ist und eine Änderung auf Verdacht genau das wäre, was der Auftrag ausschließt.

## 7. Quellenprüfung (Paket B)

Nicht in dieser Riehtung bearbeitet. Offen bleibt: Rohantworten, Ticker, Börse, Währung,
Kursart, Zeitstempel, Zeitzone und Herkunft der lokalen JSON. Der Kurs `$630.63` bleibt
**unbestätigt** und wird nicht als Referenz benutzt.

## Zusammenfassung für den Auftraggeber

**Reproduzierbar funktionierender Interaktionspfad:** bis zum Fenster ja (mit Sichtbeweis
und Datei-/Strukturprüfung), ab der Tastatureingabe nein.

**Präzise isolierter Fehler mit Belegen:** Tastatureingaben erreichen Xwayland-Fenster
nicht, obwohl Fokus und aktives Fenster korrekt gesetzt sind; `xdotool` meldet rc=0, der
Fensterinhalt ändert sich nicht. Kontrolle mit GTK zeigt denselben Fehler.

**Keine** neuen Schutzmechanismen auf Verdacht. Die Pop-up-Unterdrückung ist vollständig
zurückgenommen, notwendige Freigaben bleiben transparent und werden nicht automatisch
bestätigt.

## Nachtrag (2026-09-30, bei der Git-Adoptierung — zwei überholte Aussagen korrigiert)

- **Abschnitt 4, Zeile H8** („Fokus bleibt auf Shell-internem Fenster — bestätigt =
  Blockade") ist **überholt**: die spätere Messung (Korrektur vom 2026-09-27 in
  `CALC-ABNAHME-A-STAND.md`) zeigt, dass der X-Server-Fokus die ganze Zeit korrekt
  auf dem Zielfenster saß; die Blockade liegt in der **Zustellung**, nicht im Fokus.
- **Abschnitt 7** („Der Kurs `$630.63` bleibt unbestätigt") ist **überholt**:
  `QUELLENPRUEFUNG-AMD.md` (2026-09-27) bestätigt 630.63 als letzter Marktpreis
  (Yahoo Finance, identische Felder); zwei abweichende Werte bleiben ungeklärt.
