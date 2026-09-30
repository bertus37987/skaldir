# Abnahme A — Live-Eingabenachweis und Calc-Status

Datum: 2026-09-27. Host: Fedora, GNOME/Wayland, Xwayland `:0`.
Reihenfolge der Lieferung: Codeänderung → Mock-Test → Live-Eingabenachweis → Calc-Abnahme.

---

## 1. Codeänderung

### Neu: `src/skaldir/input_backend.py`

Wählt das Tastatur-Backend nach **tatsächlicher Zustellung**, nicht nach `rc`.

| Funktion | Zweck |
|---|---|
| `backend_available(name)` | Voraussetzung (installiert / Socket erreichbar), mit Grund |
| `probe(name, probe_log, marker)` | Sendet einen Tastendruck, wartet, ob das **Ziel** ein Event meldet |
| `choose(probe_log, marker)` | Erstes zustellendes Backend, sonst ehrlich `none` |

`BACKENDS = ("ydotool", "xdotool")` — uinput zuerst, weil ein Compositor, der XTEST für
Xwayland-Clients nicht zustellt, einen echten Geräte-Event dennoch sieht.

### Geändert: `src/skaldir/cu_host_session.py`

| Stelle | Änderung |
|---|---|
| `_delivering_input_backend()` | Tor im Live-Pfad: ohne zugestelltes Backend wird `key`/`type_text` **verweigert** — mit der Messung im Text, nicht bloß „abgelehnt" |
| `_live_window_argv()` | Baut je Backend die richtige argv |
| `_keycode_for()` | Liest den Keycode aus der echten Keymap (`xmodmap -pke`), 0 wenn nicht vorhanden |

### Korrigierte Schnittstellenfehler (neu in dieser Runde)

| Fehler | Vorher | Nachher | Quelle der Korrektur |
|---|---|---|---|
| `ydotool type` mit Keycodes | `["ydotool","type","38:1","38:0"]` — hätte den Text `38:1 38:0` getippt | `["ydotool","type","--delay","40","--key-hold","20",text]` | `ydotool type --help` → `[STRINGS]...` |
| `WM_STATE` als Einrichtungsbedingung | Fenster ohne `WM_STATE` galt als „nicht gemappt" | Nur `Withdrawn` ist ein Problem; ein rohes X-Fenster hat gar keinen | Messung: echtes 460×200-Fenster hat keinen, der Rahmen darum herum schon |
| Fensterzuordnung über `_NET_WM_PID` | Fenster ohne diese Eigenschaft wurden verworfen | Fläche entscheidet; PID nur als Vorzug | Messung: `_NET_WM_PID` fehlt am echten Fenster, steht am Rahmen (mit falscher PID) |

**Rückgenommene, unbegründete Änderungen bleiben zurückgenommen:** D-Bus-Entzug,
AT-SPI-Entzug, `GTK_USE_PORTAL=0`, und jegliches Schließen von Fenstern anhand
Titel/Prozessname. Dazu hält `tests/test_live_desktop_launch_env.py::
test_no_run_closes_a_window_by_title_or_process` dauerhaft fest.

---

## 2. Mock-Test

| Datei | Tests | Ergebnis |
|---|---|---|
| `tests/test_input_backend.py` | 11 | **11 passed** |
| `tests/test_live_keyboard_backend_gate.py` | 12 | **12 passed** |
| `tests/test_live_desktop_launch_env.py` | 10 | **10 passed** |

Befehl:

```
env -u HERMES_HOME -u GODSV_HOME -u SKALDIR_HOME -u GODSV_LLM_API_KEY -u GODSV_LLM_BASE_URL \
    PYTHONPATH=$PWD/src TMPDIR=/tmp .venv/bin/python -m pytest \
    tests/test_input_backend.py tests/test_live_keyboard_backend_gate.py \
    tests/test_live_desktop_launch_env.py -o addopts="" -p no:cacheprovider -p no:randomly
```

Die Tests prüfen das **Verhalten**, nicht die Implementierung nach:
`test_rc_0_allein_ist_keine_zustellung` hält fest, dass ein rc=0 ohne Event am Ziel
keine Zustellung ist; `test_ydotool_type_bekommt_text_nicht_keycodes` hält den
Schnittstellenfehler fest; `test_no_run_closes_a_window_by_title_or_process` lässt eine
Wiederkehr der Titel-basierten Schließroutine scheitern.

---

## 3. Live-Eingabenachweis

Probe: `logs/calc-acceptance/probe_input.py` + `probe_target.py`
(Eigenes Fenster, eigener Empfänger, Run-Kennung, bestätigter Fokus über die
**Server**-Meinung `get_input_focus`).

Lauf `live3`, Auszug:

```
eigenes Fenster: 10485970 | WM_STATE=(keiner) | _NET_WM_STATE=_NET_WM_STATE_FOCUSED
Fokus bestätigt: Server=0xa000d2 Ziel=0xa000d2 → JA
PROBE EINGERICHTET
BACKEND ydotool: key_rc=0 key_events=0 text_rc=0 text_angekommen=False
BACKEND xdotool: key_rc=0 key_events=0 text_rc=0 text_angekommen=False
verdict: blocked
```

**Die Probe war korrekt eingerichtet.** Fokus, Fenster und Zuordnung sind bestätigt.
Trotzdem meldet **keines** der beiden Backends ein Event am Ziel.

### Die gemessene Kette, Glied für Glied

| # | Glied | Messung | Ergebnis |
|---|---|---|---|
| 1 | Kernel-Event entsteht | `ydotool key 38:1 38:0` neben `/dev/input/event257` gelesen | **`type=1 code=38 value=1`** — echter Kernel-Event |
| 2 | GNOME liest das Gerät | `/proc/<pid>/fd` nach dem Event-Node | **gnome-shell (4189)** hält beide ydotool-Geräte offen |
| 3 | Gerät ist eine echte Tastatur | `/proc/bus/input/devices` → `B: KEY=…`, `B: REL=…` | vollständig |
| 4 | X-Server-Fokus sitzt | Xlib `get_input_focus()` | **= Zielfenster** |
| 5 | Ziel ist empfangsbereit | eigenes Fenster, eigener Empfänger, Protokoll | bereit |
| 6 | Event kommt an | eigenes Protokoll des Ziels | **0 Events** |
| 7 | Maus erreicht den Desktop | `ydotool mousemove -a 100 100` → `getmouselocation` vorher/nachher | **identisch** — bewegt sich nicht |

### Bewertung

`rc=0` bedeutet hier **Versand**, nicht **Zustellung**. Das ist an fünf Wegen gemessen
(`xdotool key`, `xdotool key --window`, `xdotool type`, XTEST per Xlib, `ydotool key`)
und durch einen Kontrollversuch abgesichert: `xdotool key --window <nicht existent>`
liefert `BadWindow` — XTEST funktioniert also, nur die Zustellung an ein Xwayland-Fenster
bleibt aus.

**Was bewiesen ist:** die Lücke zwischen Glied 3 und Glied 6. Kernel-Event entsteht,
GNOME liest das Gerät, X-Fokus sitzt — und beim Fenster kommt nichts an. Auch die Maus
erreicht den Desktop nicht.

**Was NICHT bewiesen ist:** die Ursache dieser Lücke. Drei Kandidaten bleiben offen und
werden nicht als Wahrheit behauptet:

1. gnome-shell akzeptiert das virtuelle Gerät nicht als Eingabequelle (liest es, nutzt
   es aber nicht).
2. GNOME erwartet für synthetische Eingaben den **Portal-Weg** (`libei`/EIS) — die
   Bibliotheken sind installiert (`/usr/lib64/libei.so.1`, `libeis.so.1`), aber kein
   Portal-Frontend läuft.
3. Die Session, in der `ydotoold` läuft, gehört nicht zur aktiven Desktop-Sitzung.

---

## 4. Calc-Abnahme

### Erreicht und bewiesen (Fenster-Teil)

| Prüfpunkt | Beweis |
|---|---|
| richtige Datei geöffnet | Titel `TESTDATA_kurs_abnahme.ods — LibreOffice Calc` |
| Anwendung identifiziert | `WM_CLASS = "libreoffice", "libreoffice-calc"` |
| Zahlenformat | `12.5`, `3.75`, `100` rechtsbündig als Zahlen |
| Datumsformat | `46289`, `46290` (Calc-Seriennummern) |
| Tabellenblätter | `Sheet 1 of 2`, Blätterregister sichtbar |
| Formel | Blatt 2 „Formeln", Summenzeile vorhanden |
| Ausgewählte Zelle | Name-Box `A1` |
| Sichtbeleg | `logs/calc-acceptance/ACCEPTANCE_fenster.png`, **80329 Bytes, 1920×1011**, dekodierbares PNG |
| Dateiinhalt maschinenlesbar | `content.xml`: `value="12.5"` vorhanden, 2 Blätter, Formel vorhanden |

Voraussetzung dafür: **gültige ODS + frisches Profil**. Ein defektes Fixture führt zu
`(repaired document)` und einem nicht erfassbaren Hauptfenster — das war die erste
Fehlspur und ist widerlegt (`CALC-FENSTER-HYPOTHESEN.md`, H3/H6).

### NICHT erreicht (Eingabe-Teil)

Zelle ändern → sichtbar prüfen → speichern → erneut öffnen → Wert nachweisen.

**Blockiert an derselben Stelle wie Abschnitt 3:** Tastatureingaben erreichen kein
Xwayland-Fenster dieser Sitzung. Der Nachweis über `xdotool`-`rc=0` wäre kein Nachweis.

---

## 5. Quellenprüfung B (`$630.63`)

**Bestätigt.** Unabhängige Quelle: Yahoo Finance
(`query1.finance.yahoo.com/v8/finance/chart/AMD`, HTTP 200, gültiges JSON).

| Feld | Yahoo | `/home/willy/AMD_latest_data.ods` | Urteil |
|---|---|---|---|
| `regularMarketPrice` | 630.63 | 630.63 | **identisch** |
| `regularMarketChangePercent` | 0.218 | 0.218 | **identisch** |
| `regularMarketDayHigh` / `Low` | 638.95 / 625.52 | 638.95 / 625.52 | **identisch** |
| `fiftyTwoWeekHigh` | 638.95 | 638.95 | **identisch** |
| `fulldayChange` / `Percent` | 1.37 / 0.218 | 1.37 / 0.218 | **identisch** |
| Währung / Börse | USD / NasdaqGS | USD / NasdaqGS | **identisch** |
| `fiftyTwoWeekLow` | **159.33** | **157.05** | **weicht ab** |
| `regularMarketVolume` | **17516535** | **17011780** | **weicht ab** |

Kursart korrekt benannt: `regularMarketPrice = 630.63` ist der **letzte Marktpreis**.
Der reguläre Schlusskurs ist ein eigenes Feld: `fulldayPrice = 630.99`.
Zeitpunkt: `regularMarketTime` → 2026-09-25 16:00:01 EDT (Börsenschluss NY).

Zwei Werte weichen ab, die Herkunft dieser Abweichung bleibt **ungeklärt**. Die Datei
speichert alle Werte als Text und enthält keine Quell-, Zeit- oder Zeitzonenangabe.

---

## 6. Zusammenfassung in vier getrennten Feststellungen

| Ebene | Status | Beleg |
|---|---|---|
| **Codeänderung** | **fertig** | `input_backend.py` neu; `cu_host_session.py` Tor + Korrekturen; 33 Mock-Tests grün |
| **Mock-Test** | **grün, 33/33** | drei Testdateien, Läufe oben |
| **Live-Eingabenachweis** | **blockiert, mit Kette** | Probe korrekt eingerichtet, Fokus bestätigt, 0 Events; Kernel-Event bewiesen, Zustellung nicht |
| **Vollständige Calc-Abnahme** | **teilweise** | Fenster/Inhalt/Datei bewiesen; Eingabe-Teil blockiert |

**Keine** der vier Ebenen darf für eine andere genommen werden. Insbesondere: Die
grünen Mock-Tests beweisen die Logik, nicht die Zustellung auf dem Host; und der
bewiesene Kernel-Event beweist die Erzeugung, nicht die Ankunft.

## 7. Laufende Ressourcen

| Ressource | Zustand |
|---|---|
| `ydotoold --socket-path /tmp/skaldir-ydotoold/ydotoold.sock` | läuft (PID 82163), Gerät `event257` |
| Calc-Testinstanzen (eigene, sep. Profil) | mehrere, nicht beendet |
| Fremde LibreOffice-Instanzen | **nicht** beendet, Sperren/Recovery unberührt |
| GNOME-Shell, Fenstermanager | unverändert; kein zweiter WM gestartet |

---

## 8. Nachtrag (2026-09-30, bei der Git-Adoptierung — Korrekturen an veralteten Aussagen, Inhalt bleibt unverändert)

- **Mock-Test-Zahlen (Abschnitt 2) beschreiben den Übernahme-Zustand, nicht den heutigen.**
  Die Eingabe-Arbeit wurde seitdem committet (`69acba2`, `715447d`, `f667366`, 2026-09-28);
  die drei Testdateien enthalten jetzt **24 / 22 / 11** Tests (zusammen 57 statt 33).
  Gemessen 2026-09-30: `uv run python -m pytest tests/test_live_desktop_launch_env.py -q`
  → **11 passed**, Exit 0. Die oben notierten 11/12/10 (= 33) waren beim Schreiben korrekt.
- **`BACKENDS = ("ydotool", "xdotool")` (Abschnitt 1) ist veraltet:** die Liste enthält
  jetzt auch `"portal"` (`src/skaldir/input_backend.py`, Zeile 73) — der Portal-/EIS-Weg
  (Kandidat 2 in Abschnitt 3) ist als drittes Backend im Code angelegt. Ob es live
  zustellt, ist hier **nicht** geändert und nicht neu behauptet.
- **Korrektur zu Abschnitt 5:** Die Aussage „Die Datei speichert alle Werte als Text"
  war ein Fehlles. Die Zahlen stehen als **typisierte Floats** (`office:value="630.63"`
  usw. in `content.xml`); „Textwerte" trifft nur auf Datumsfelder zu. Geprüft
  2026-09-30, SHA-256 der Nutzerdatei unverändert (`eb54f9ff…c0fac`); siehe auch die
  Korrektur in `DROID-TAKEOVER.md` Abschnitt 2.3.
