# Hypothesentabelle: „Calc zeigt auf :0 kein erfassbares Fenster"

Host: Fedora 44, GNOME/Wayland, Xwayland `:0`. Datum: 2026-09-26.
Methode: `python3 -m py_compile`-loses Diagnoseskript
`logs/calc-acceptance/hypotheses.py`, ein Läuft über
`DISPLAY=:0 PYTHONPATH=src python3 logs/calc-acceptance/hypotheses.py`.
**Genau eine Variable pro Vergleich. Kein fremder Prozess beendet, keine fremde
Sperre/Recovery-Daten gelöscht.** Eigene Testinstanz mit separater Testdatei
(`/tmp/skaldir-calc-test/TESTDATA_kurs_abnahme.ods`) und separatem Testprofil
(`/tmp/skaldir-calc-test/lo_profile`).

## Ergebnis

| # | Hypothese | Kontrollierter Test | Erwartet | Tatsächlich | Status |
|---|---|---|---|---|---|
| K0 | Das bewährte Verfahren zeigt ein Fenster | `zenity --info`, Fenstersuche über `xdotool` | Fenster | Fenster `6291460` „Information" | **bestätigt** |
| H1 | Separierendes Profil (`-env:UserInstallation`) zeigt ein Fenster | Calc mit eigenem Profil, Fenster über **eigene soffice.pid** | Fenster | Fenster `12582915`/`14680067` | **bestätigt** |
| H2 | **Gegenversuch:** ohne separates Profil (alles andere identisch) | gleicher Start, Profil-Argument entfernt | anders als H1 | Fenster `14680067` — ebenfalls vorhanden | **widerlegt** (das Profil ist **keine** Variable) |
| H3 | Die Fenster mit Titel „LibreOffice 26.2" sind das Calc-Hauptfenster | Geometrie aller Fenster je eigener PID prüfen | Hauptfenster 1805×980 unter diesem Titel | Titel „LibreOffice 26.2" = 200×200 und 10×10; Hauptfenster heißt **„VCL ImplGetDefaultWindow"** | **widerlegt** (Titelzuordnung war falsch) |
| H4 | „TESTDATA…Calc" ist als Ziel titel suchbar | `xdotool search --name TESTDATA_kurs_abnahme` nach Fensterstart | Treffer | **kein Treffer** | **bestätigt** |
| H5 | `import -window` erfassst jedes gemappte Fenster | zenity-Fenster vs. Calc-Hauptfenster vs. `root` | alle erfassbar | zenity 3429 B ✓, `root` ✗, Calc ✗ | **widerlegt** |
| H6 | Ursache ist `WM_STATE` | `xprop -id <window> \| WM_STATE` beider Fenster | Normal bei sichtbar | zenity = **Normal** ✓, Calc-Hauptfenster = **Withdrawn** ✗ | **bestätigt** |
| H7 | `xdotool windowmap/windowraise` heben Withdrawn auf | map+raise, dann `import` und erneutes `xprop` | Normal | map rc=0, `WM_STATE` bleibt **Withdrawn**, `import` weiter rc=1 | **widerlegt** |

## Schlussfolgerung

**Bewiesene Ursache:** Die Calc-Hauptfenster der eigenen Testinstanzen stehen beim
Fenstermanager im Zustand **Withdrawn** — sie sind nicht gemappt. Ein nicht gemapptes
Fenster kann ImageMagick nicht lesen (`missing an image filename` ist der irreführende
Text dafür; das Fenster wird vorher von `XGetImage` abgelehnt).

Das ist **nicht** das „kein Fenster"-Bild von außen: Der Prozess läuft, die Datei ist
geöffnet, die Fenster-IDs existieren (1805×980). Der Fensterinhalt ist aber nicht
erfassbar und wird nicht angezeigt. Das entspricht genau der Nutzerbeobachtung
„ich muss jedes Mal recover selected drücken": LibreOffice hält das Dokumentfenster
zurück, solange es eine Wiederherstellungsentscheidung erwartet.

**Widerlegte frühere Annahmen (nicht erneut behaupten):**
- verwaiste LibreOffice-Profil-Sperre — H2 zeigt Fenster mit und ohne.
- separates `-env:UserInstallation`-Profil — ist für das Fenster **keine** Variable.
- Fenstertitel „LibreOffice 26.2" = Hauptfenster — ist ein schwebendes 200×200-Fenster.
- „Calc startet als Wayland-Client" — unter `child_env()` ist es XWayland.

## Offene Punkte

1. **Was genau löst Withdrawn aus?** Verdacht: das LibreOffice-
   Wiederherstellungs-/RegisterPopup. Kontrollversuch: `--norestore` mit einem
   *frischen* Profil und einer *neuen* Datei. Status: **offen**.
2. Ist Withdrawn bei **jeder** Calc-Instanz so oder nur bei parallelen? Nur eine
   eigene Instanz gleichzeitig starten. Status: **offen**.
3. Die GUI-Interaktion (Zelle auswählen → Wert → speichern → erneut öffnen) setzt
   ein **gemapptes** Fenster voraus und bleibt blockiert, solange Punkt 1 offen ist.

## Nachtrag (2026-09-30, bei der Git-Adoptierung — Stand der offenen Punkte)

- **Punkt 1 ist geklärt:** mit **gültiger ODS + frischem Profil** mappt das
  Calc-Hauptfenster und ist erfassbar (belegt in
  `ABNAHME-A-LIVE-EINGABE-UND-CALC.md` Abschnitt 4 und
  `CALC-ABNAHME-A-STAND.md`); das Withdrawn-Bild galt für die defekte bzw. auf
  Wiederherstellung wartende Fixture (H3/H6-Kontext). Die Hypothesentabelle selbst
  bleibt als historische Messung vom 2026-09-26 unverändert.
- **Punkt 3 bleibt am Eingabe-Blocker hängen** (Zustellung an Xwayland-Fenster,
  siehe `ABNAHME-A-LIVE-EINGABE-UND-CALC.md` Abschnitt 3); hier nicht neu gemessen.
