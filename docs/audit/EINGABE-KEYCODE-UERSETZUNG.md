# Keycode-Übersetzung X → evdev (Feature `input-keycode-translation`, VAL-INPUT-003)

Sanitized evidence for P0 defect 4. Audit document, therefore German; no raw transcripts, no
secrets. All commands from the repo root, `.venv` interpreter; a fixture/private-display result
is labelled as such and never as a host-live result.

## Der Defekt (gemessen, nicht vermutet)

Der Zustellpfad übergab dem uinput-Backend **X-Keycodes** unübersetzt:

| Frage | Antwort auf diesem Host | Quelle |
|---|---|---|
| X-Keycode von 'a' | **38** | `xmodmap -pke` → `keycode 38 = a A a A a A a A` |
| evdev-Code von 'a' | **30** (`KEY_A`) | `/usr/include/linux/input-event-codes.h` |
| evdev-Code 38 | **`KEY_L`** | dieselbe Header-Datei |
| X-Keycode von Return/Control_L/Shift_L/space | 36 / 37 / 50 / 65 → evdev 28 / 29 / 42 / 57 | `xmodmap -pke`, gemessen |

`ydotool key --help` bestätigt die Richtung selbst: "we're using raw keycodes now … See
`/usr/include/linux/input-event-codes.h' for available key codes (KEY_*)". Das alte
`ydotool key 38:1 38:0` für 'a' drückte also `KEY_L`. Der Offset X − 8 = evdev ist auf jeder
gemessenen Zeile der Keymap 8.

Zweiter Fehler in derselben Zeile: `ydotool type` wurde mit `--delay 40` gerufen. Flag-Messung
gegen den laufenden Mission-Daemon (2026-09-28):

| Aufruf | Antwort | Rückgabewert |
|---|---|---|
| `ydotool type --delay 40` | `type: unrecognized option '--delay'` | **0** |
| `ydotool type --key-delay 40` | Usage (keine STRINGS angegeben) | 0 |
| `ydotool type --help` | `-d, --key-delay=N` (Vorgabe 20 ms), `-H, --key-hold=N` (Vorgabe 20 ms), `-D, --next-delay`, `-f, --file`, `-e, --escape` | 0 |

`--key-hold` und der Text selbst waren also immer korrekt; nur das Delay-Flag war es nicht —
und ydotool meldet einen unbekannten Parameter mit **rc 0**, weshalb ein falsches Flag wie
Erfolg aussieht.

## Was jetzt gilt

* **Neues Modul `src/skaldir/evdev_keys.py`** — die eine Stelle der Übersetzung:
  `EVDEV_KEYS` (Name → evdev-Code aus dem Kernel-Header), `MODIFIER_KEYS`,
  `X_KEYCODE_OFFSET = 8` (auf diesem Host gemessen), `UnknownKeyError`,
  `translate()`/`key_event_argv()` (Press-Reihenfolge: Modifier zuerst, Lösen in umgekehrter
  Reihenfolge; Großbuchstabe = Shift-Halt; `ctrl+c` → `29:1 46:1 46:0 29:0`) und
  `parse_x_keymap()` (nur die **Unshifted**-Spalte der Keymap, damit die Shift-Spalte nie als
  eigener Key durchgeht). Ein Name, den keine Quelle kennt, wird **verweigert**.
* **`cu_host_session._input_argv_for`** baut den Tastenweg für `ydotool` aus dem Übersetzer
  (`_evdev_key_pairs`, `_x_keymap` liest `xmodmap -pke` einmal pro Sitzung über die
  Prozessnaht `_run`) und weigert sich mit benanntem Grund, wenn ein Name nicht übersetzbar
  ist. `_keycode_for` liefert jetzt den evdev-Code (0 = unbekannt).
* **Textweg**: genau EIN String-Argument an `ydotool type`, mit den echten Flags
  `--key-delay`/`--key-hold`. Kein Keycode-Paar betritt je eine `type`-argv
  (`input_backend.send_argv` ebenfalls korrigiert).
* **Socket-Erkennung** (aus `input-probe-rewrite`): `YDOTOOL_SOCKET` →
  `/tmp/skaldir-ydotoold/ydotoold.sock` → `/run/user/1000/.ydotool_socket`; „Datei existiert,
  connect wird verweigert" bleibt `BACKEND_MISSING`. Die Tests bestehen unverändert.
* **Falscher Kommentar/Wert in der Probe korrigiert**: `logs/calc-acceptance/probe_input.py`
  sendete 'a' als `38:1 38:0` und dokumentierte `--delay`; jetzt `30:1 30:0` bzw.
  `--key-delay`. Die Kernel-Beobachter-Notiz in `input_backend.py` sagt ausdrücklich, dass das
  gemessene `code=38` der unübersetzte X-Code war.

## Verifikation (tatsächlich gelaufen)

| Befehl | Exit | Beobachtung |
|---|---|---|
| `uv run pytest tests/test_evdev_keys.py -q` | 0 | **36 passed** — u. a. `a`→30 (≠38), `l`→38, `A`→`42:1 30:1 30:0 42:0`, `ctrl+c`→`29:1 46:1 46:0 29:0`, `Return`→`28:1 28:0`, 13 verweigerte Namen, jede Tabellenzahl gegen den Header geprüft |
| `uv run pytest tests/test_live_keyboard_backend_gate.py tests/test_input_backend.py tests/test_input_probe_bounded.py tests/test_input_probe_owned_display.py tests/test_evdev_keys.py -q` | 0 | **158 passed** (die beiden roten `tests/test_cu_live_desktop.py`-Fälle laufen hier nicht mit) |
| dieselbe Auswahl + `tests/test_act_service.py test_capability_flow.py test_dependency_declarations.py test_cu_platform.py` | 0 | **210 passed** |
| `uv run ruff check` auf die geänderten Dateien | 1 | nur der **vorbestehende** `I001` in `cu_host_session.py:57` (Takeover-Importblock, Feature `fix-milestone-gate-blockers`); neue Dateien sauber |
| `uv run python -m compileall -q src` | 0 | sauber |

Zwei Tests hielten den alten Fehler fest und sind korrigiert:
`test_ydotool_uebersetzt_key_namen_ueber_die_keymap` / `test_ydotool_key_hat_press_und_release`
verlangten `38:1` für 'a' — jetzt `30:1 30:0` (und „kein X-Code erreicht uinput").
`test_keymap_ohne_xmodmap_liefert_null` ist durch „Tabelle ohne Keymap + unbekannter Name = 0"
ersetzt; VAL-INPUT-006 (kein Test behauptet `38:1`) ist damit erfüllt.

### Produktiver Nachweis (privates Display, nicht der Host-Desktop)

`logs/input-keycode-proof/proof_productive_path.py` fährt die **wirkliche** Kette
(`HostSessionRunner.act()` → `_full_action_argv` → `_input_argv_for` → echter
`xmodmap`/`xdotool`-Kindprozess) auf einem eigenen Xvfb `:91` + openbox mit eigenem
Empfängerfenster (`python -m skaldir.probe_target`). Der Mission-Daemon war gestoppt, der
nutzereigene `ydotoold` (`/tmp/.ydotool_socket`, pid 998) steht nicht in der Erkennungsreihenfolge,
also konnte kein uinput-Event den Host erreichen.

| Aktionsaufruf (produktiv) | `act()` | am eigenen Zielfenster angekommen (X-Codes → evdev) |
|---|---|---|
| `key ctrl+c` | ok, exit 0 | `37:1 54:1 37:0 54:0` → `29:1 46:1 29:0 46:0` |
| `key A` | ok, exit 0 | `50:1 38:1 50:0 38:0` → `42:1 30:1 42:0 30:0` |
| `key Return` | ok, exit 0 | `36:1 36:0` → `28:1 28:0` |
| `type_text "ä→✓ 7"` | ok, exit 0 | space + '7'; die drei Nicht-Latin-1-Zeichen tippt xdotool per temporärem Keycode-Remap — der Text gehört in den `ydotool type`-Pfad (ein String-Argument), was der uinput-Bau unten zeigt |

Dieselben Aktionen über den uinput-Bau (reale Übersetzung, keine Sendung):

| Aktion | gebaute argv |
|---|---|
| `key ctrl+c` | `ydotool key 29:1 46:1 46:0 29:0` |
| `key A` | `ydotool key 42:1 30:1 30:0 42:0` |
| `key Return` | `ydotool key 28:1 28:0` |
| `type_text "ä→✓ 7"` | `ydotool type --key-delay 40 --key-hold 20 "ä→✓ 7"` |

Der Probe-Bericht derselben Sitzung: `backend=xdotool states=[INPUT_OBSERVED_AT_TARGET]
delivers=True` — gemessen auf dem **privaten** Display. Das ist kein Host-Live-Ergebnis; die
Zustellung an ein Xwayland-Fenster des Nutzers bleibt VAL-INPUT-007 (`input-live-proof`).

## Offen

* **VAL-INPUT-007** (Live-Zustellung am echten Host) gehört `input-live-proof`; der gemessene
  Blocker (fünf Wege rc 0, 0 Events am Ziel) steht in
  `docs/audit/BACKEND-ENTSCHEIDUNG-TASTATUR.md`.
* Der nutzereigene `ydotoold` (pid 998, Socket `/tmp/.ydotool_socket`) steht **nicht** in der
  Erkennungsreihenfolge der Mission. Wer ihn benutzen will, muss `YDOTOOL_SOCKET` setzen.
