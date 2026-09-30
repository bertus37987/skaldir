# Abnahme-Index — welches Dokument worüber Auskunft gibt

Stand: 2026-09-27. Arbeitsverzeichnis `/home/willy/Agentic_Projects/Skaldir_Agent`.

## Für die Abnahme zuerst lesen

| Dokument | Inhalt |
|---|---|
| **`ABNAHME-A-LIVE-EINGABE-UND-CALC.md`** | **Der Abnahmebericht.** Codeänderung, Mock-Test, Live-Eingabenachweis, Calc-Abnahme — getrennt und mit Belegen. Wenn nur ein Dokument gelesen wird: dieses. |
| `CALC-ABNAHME-A-STAND.md` | Calc-Abnahme A im Detail; enthält eine **Korrektur** der früheren Fokus-Deutung. |
| `CALC-FENSTER-HYPOTHESEN.md` | Hypothesentabelle zu „Calc zeigt kein Fenster" — bestätigt/widerlegt/offen. |
| `BACKEND-ENTSCHEIDUNG-TASTATUR.md` | Warum der Live-Pfad Tastatureingaben verweigert, bis ein Backend zustellt. |
| `POPUP-STOPP-UND-CALC-ISOLIERUNG.md` | Rücknahme der Pop-up-Unterdrückung; welche Änderungen entfernt und welche behalten sind. |
| `QUELLENPRUEFUNG-AMD.md` | Abnahme B: Kurs `$630.63` unabhängig verifiziert, inkl. zweier Abweichungen. |

## Status in einem Sat je Ebene

| Ebene | Status |
|---|---|
| **Codeänderung** | fertig — `src/skaldir/input_backend.py` (neu), `cu_host_session.py` (Backend-Tor + Schnittstellenkorrekturen) |
| **Mock-Test** | grün, **33/33** in drei Dateien (`test_input_backend.py`, `test_live_keyboard_backend_gate.py`, `test_live_desktop_launch_env.py`) |
| **Live-Eingabenachweis** | **blockiert** — Probe korrekt eingerichtet, Fokus über den X-Server bestätigt, Kernel-Event bewiesen, **0 Events am Ziel** |
| **Vollständige Calc-Abnahme** | **teilweise** — Fenster, Inhalt, Typen, Screenshot und Datei bewiesen; der Eingabeteil ist am selben Blocker blockiert |
| **Quellenprüfung B** | **erledigt** — `$630.63` bestätigt als letzter Marktpreis 2026-09-25 16:00 EDT; zwei Felder weichen ab, Herkunft offen |

## Zwei Sätze, die nicht durcheinandergehen dürfen

- **Ein Kernel-Event ist keine Zustellung.** `ydotool key` erzeugt nachweislich
  `type=1 code=38` am Gerät — und das Fenster bekommt trotzdem nichts.
- **`rc=0` ist Versand, nicht Ankommen.** An fünf Wegen gemessen; XTEST selbst
  funktioniert (bewiesen durch `BadWindow` auf ein nicht existentes Fenster).

## Offener Blockierer mit den drei Kandidaten

Der Blocker ist die **Zustellung** zwischen Kernel/GNOME und dem Xwayland-Fenster.
Nicht bestätigt, deshalb nicht behauptet:

1. gnome-shell akzeptiert das virtuelle Gerät nicht als Eingabequelle.
2. GNOME erwartet für synthetische Eingaben den **Portal-Weg** (`libei`/EIS) — die
   Bibliotheken sind installiert, ein Portal-Frontend läuft nicht. Die Zustimmung des
   Nutzers dazu ist transparent einzuholen, nicht zu umgehen.
3. Die Session, in der `ydotoold` läuft, gehört nicht zur aktiven Desktop-Sitzung.

## Was nicht gemacht wurde

Kein Kernel-, Modul- oder udept-Eingriff. Kein zweiter Fenstermanager. Kein fremder
Prozess beendet, keine fremden Sperren oder Recovery-Daten gelöscht. Kein automatisches
Schließen eines Freigabedialogs — dafür gibt es keinen Code mehr, und ein Test lässt
eine Wiederkehr scheitern.

## Reproder-Befehle

```bash
# Mock-Tests
cd /home/willy/Agentic_Projects/Skaldir_Agent
env -u HERMES_HOME -u GODSV_HOME -u SKALDIR_HOME -u GODSV_LLM_API_KEY -u GODSV_LLM_BASE_URL \
    PYTHONPATH=$PWD/src TMPDIR=/tmp .venv/bin/python -m pytest \
    tests/test_input_backend.py tests/test_live_keyboard_backend_gate.py \
    tests/test_live_desktop_launch_env.py -o addopts="" -p no:cacheprovider -p no:randomly

# Live-Eingabe-Probe (eigenes Fenster, eigener Empfänger, bestätigter Fokus)
export YDOTOOL_SOCKET=/tmp/skaldir-ydotoold/ydotoold.sock
DISPLAY=:0 PYTHONPATH=$PWD/src .venv/bin/python \
    logs/calc-acceptance/probe_input.py --run R1 --backends ydotool,xdotool --text PROBE_4711
```

`ydotoold` muss dazu laufen; wie es gestartet wird, steht in
`ABNAHME-A-LIVE-EINGABE-UND-CALC.md` Abschnitt 7.

## Nachtrag (2026-09-30, bei der Git-Adoptierung)

- Die Zeile „Mock-Test grün, 33/33" beschreibt den Übernahme-Stand. Nach den
  Input-Commits (`69acba2`, `715447d`, `f667366`, 2026-09-28) enthalten die drei
  Dateien **24 / 22 / 11** Tests (zusammen 57); `tests/test_live_desktop_launch_env.py`
  allein: **11 passed, Exit 0** (gemessen 2026-09-30).
- Die übrigen Statuszeilen (Live-Eingabe blockiert, Calc teilweise, Quellenprüfung
  erledigt) sind historische Messungen dieses Dokuments vom 2026-09-27 und hier nicht
  neu gemessen; der aktuelle Stand steht in den DROID-Dokumenten der Mission.
