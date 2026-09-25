# STOP-05 — Live-Nachweis des physischen Doppel-Escape

Stand: 25.09.2026, Revision folgt. **Status: BEWEIS ERBRACHT.**

## Was war blockiert

`docs/audit/STATUS-2026-09-25.md` führte STOP-05 als `extern-blockiert`: `/dev/input/event*`
war `root:input`, der Nutzer `willy` nicht in der Gruppe `input`. Der Bericht meldete
`double_escape_proven: false`.

## Was sich geändert hat

Der Nutzer hat `sudo usermod -aG input willy` ausgeführt und neu angemeldet. Geprüft:

```
$ id
groups=1000(willy),10(wheel),104(input),964(docker)
```

`skaldir stop report` meldet jetzt:

```
evdev                 available   "32 of 32 evdev node(s) readable"
libinput              available   "libinput list-devices ran"
xdg-global-shortcuts  available   "GlobalShortcuts portal reachable; BindShortcuts present"
x11-record            available   "X11 RECORD extension present"
agent-injection       available   "present: ydotool, xdotool, wtype"
```

## Der eigentliche Befund: Wayland gehört getrennt abgenommen

Die Anforderung sagt ausdrücklich: *„X11 und Wayland separat abnehmen; XWayland ist kein
Ersatz für beide Nachweise."* Genau das hat sich bewahrheitet, und zwar auf eine Weise, die
man nur durch Messen sieht:

```
$ echo $XDG_SESSION_TYPE
wayland
```

**Ein rohes Lesen von `/dev/input/event*` sieht keine einzige Taste**, obwohl die Rechte
stimmen:

```
listening on 32 nodes
key events captured: 0
ESC(1) events: []
```

Ursache: unter Wayland öffnet der Compositor die evdev-Geräte selbst und teilt den
Ereignisstrom nicht. Das ist **kein** Rechteproblem mehr — die Berechtigung ist da
(„32 of 32 readable"), aber der Strom woanders.

`libinput debug-events` dagegen sieht die Ereignisse. Beides gleichzeitig gemessen ist der
eigentliche Beweis dafür, dass die Aussage „Wayland ist ein anderer Fall" eine Messung ist
und keine Ausrede.

## Der Nachweis

`libinput debug-events --show-keycodes`, 100 s, während der Nutzer in einem **anderen**
Fenster (nicht im Chat) ESC drückte:

```
-event4   KEYBOARD_KEY   +38.676s   KEY_ESC (1) pressed
-event4   KEYBOARD_KEY   +38.871s   KEY_ESC (1) pressed
-event4   KEYBOARD_KEY   +39.568s   KEY_ESC (1) pressed
-event4   KEYBOARD_KEY   +39.741s   KEY_ESC (1) pressed
-event4   KEYBOARD_KEY   +39.906s   KEY_ESC (1) pressed
-event4   KEYBOARD_KEY   +41.623s   KEY_ESC (1) pressed
-event4   KEYBOARD_KEY   +41.811s   KEY_ESC (1) pressed
-event4   KEYBOARD_KEY   +42.306s   KEY_ESC (1) pressed
-event4   KEYBOARD_KEY   +42.501s   KEY_ESC (1) pressed
-event4   KEYBOARD_KEY   +43.011s   KEY_ESC (1) pressed
-event4   KEYBOARD_KEY   +43.198s   KEY_ESC (1) pressed
```

Quelle: `event4` = **Logitech USB Receiver**, also die physische Tastatur. Keine Injektion.

Auswertung mit Fenster 500 ms, Minimalabstand 30 ms — dieselben Werte, die der Detektor
verwendet:

| Doppel-Escape | Abstand |
|---|---|
| +38.676 → +38.871 | 195,0 ms |
| +39.568 → +39.741 | 173,0 ms |
| +39.741 → +39.906 | 165,0 ms |
| +41.623 → +41.811 | 188,0 ms |
| +41.811 → +42.306 | 495,0 ms |
| +42.306 → +42.501 | 195,0 ms |
| +43.011 → +43.198 | 187,0 ms |

**7 Doppel-Escapes erkannt.**

Ebenso wichtig: die übrigen Abstände wurden **richtig nicht** als Stopp gewertet —
697 ms, 1717 ms und 510 ms liegen außerhalb des Fensters. Ein Detektor, der jede Taste
doppelt zählt, wäre unbrauchbar; das Gegenbeispiel ist also Teil des Nachweises.

## Was daraus folgt

* **STOP-05 ist auf Wayland über `libinput` bewiesen.**
* Ein roher evdev-Lesezugriff ist auf dieser Sitzung **nicht** der Beweisweg. Der
  bisherige Code sah evdev für den Beweis vor — das ist korrigiert: der Bericht weist
  `libinput` als den bewiesenen Backend aus und nennt den Grund, warum evdev hier nicht
  trägt.
* **X11 ist weiterhin offen.** `x11-record` ist verfügbar, aber nicht bewiesen. Die
  Anforderung verlangt beide separat; dieser Nachweis ist der Wayland-Teil.
* `agent-injection` (ydotool/xdotool/wtype) zählt weiterhin **nicht** — synthetische
  Eingabe kann nicht zeigen, dass ein Nutzer anhalten kann.

## Rohdaten

`/tmp/libinput_capture.log` (7793 Zeilen, 100 s, unredigiert — wird nicht committed).
Auswertung reproduzierbar über die Zeitstempel oben.
