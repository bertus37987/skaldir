# Backend-Entscheidung: Tastatureingaben auf dem Host-Desktop

Datum: 2026-09-27. Host: Fedora, GNOME/Wayland, Xwayland auf `:0`.
**Aktuelle Fassung mit der vollständigen Kette: `ABNAHME-A-LIVE-EINGABE-UND-CALC.md`.**
Hier bleibt die Entscheidung selbst mit ihrer Begründung.

**Korrektur gegenüber der ersten Fassung dieses Dokuments:** Der Satz „X-Fokus,
WM_STATE und Aktivierung sind alle korrekt" ist bestätigt, aber unvollständig. Der
Fokus wird inzwischen über die **Server**-Meinung (Xlib `get_input_focus()`) bestätigt,
nicht über `xdotool getwindowfocus`, das eher das aktive WM-Fenster liest und divergieren
kann. Die frühere Behauptung „Fokus unter Wayland unmöglich" ist **widerlegt** und wird
nicht wiederholt.

## Die Frage

Welcher Eingabepfad stellt auf dieser Sitzung Tastaturevents **tatsächlich** zu — nicht
welcher liefert `rc=0`?

## Gemessene Kette, Schritt für Schritt

Jeder Schritt wurde einzeln gemessen; ein Schritt gilt nur mit eigenem Beweis.

| # | Schritt | Befehl / Messung | Ergebnis |
|---|---|---|---|
| 1 | Kernel-Event entsteht | `ydotool key 38:1 38:0` und gleichzeitig `/dev/input/event257` gelesen | **`type=1 code=38 value=1`** — echter Kernel-Event |
| 2 | GNOME liest das Gerät | `/proc/<pid>/fd` nach `event257` durchsucht | **gnome-shell (PID 4189)** hält beide ydotool-Geräte offen |
| 3 | Gerät ist eine vollwertige Tastatur | `/proc/bus/input/devices` → `B: KEY=…`, `B: REL=…` | volle Tastatur- und Maus-Fähigkeiten |
| 4 | X-Fokus sitzt richtig | Xlib `get_input_focus()` | **`0x1000000`** = das Zielfenster |
| 5 | Fenster ist gemappt | `xprop -id <wid> WM_STATE` | **Normal** |
| 6 | WM hält es für fokussiert | `_NET_WM_STATE` | `_NET_WM_STATE_FOCUSED` |
| 7 | Xwayland-Fenster empfängt | Xlib-Zähler mit `KeyPress`-Protokoll | **0 Events** |
| 8 | Maus erreicht den Desktop | `ydotool mousemove 500 500` → `xdotool getmouselocation` | **unverändert** (Maus bewegt sich nicht) |

## Die fünf gemessenen Eingabewege

| Weg | Befehl | rc | Event am Ziel |
|---|---|---|---|
| XTEST über Fokus | `xdotool key a` | 0 | **keins** |
| XTEST über Fensteradresse | `xdotool key --window <wid> b` | 0 | **keins** |
| XTEST-Text | `xdotool type -- text` | 0 | **keins** |
| XTEST per Xlib | `xtest.fake_input(KeyPress, 38)` | 0 | **keins** |
| uinput | `ydotool key 38:1 38:0` | 0 | **keins** |

**Kontrollversuch, der XTEST selbst entlastet:** `xdotool key --window <nicht existent> a`
liefert `BadWindow`. XTEST funktioniert also — die Zustellung an ein Xwayland-Fenster
bleibt aus.

## Was damit bewiesen ist

Bewiesen ist die **Lücke zwischen Schritt 3 und Schritt 7**: Das Kernel-Event entsteht,
GNOME liest das Gerät — und beim Xwayland-Fenster kommt nichts an. Auch die Maus
(Schritt 8) erreicht den Desktop nicht, obwohl `ydotool mousemove` rc=0 liefert.

**Nicht** bewiesen ist die Ursache dieser letzten Lücke. Drei Kandidaten bleiben offen,
und keiner darf als wahr behauptet werden:

1. gnome-shell lehnt das virtuelle Gerät als Eingabequelle ab (es wird gelesen, aber
   nicht als Tastatur *benutzt*).
2. GNOME erwartet für synthetische Eingaben den **Portal-Weg** (`libei`/EIS), nicht ein
   zweites physisches Gerät — die Bibliotheken sind installiert
   (`libei.so.1`, `libeis.so.1`), aber kein Portal-Frontend läuft.
3. Die Session, in der `ydotoold` läuft, gehört nicht zur aktiven Desktop-Sitzung.

## Die Entscheidung

**Kein Backend dieser Sitzung ist gemessen zustellend.** Deshalb gilt:

> Der Live-Tastaturpfad verweigert eine `key`/`type_text`-Aktion, solange kein Backend
> ein Key-Event am Ziel nachgewiesen hat. Ein rc=0 wird nicht als Zustellung gelesen.

Das ist **keine** neue Schutzregel auf Verdacht, sondern die Konsequenz aus Schritt 7:
Ein Tastendruck, der nicht ankommt, darf nicht als erledigt gelten (ENFORCE behandelt
UNKNOWN nicht als Erfolg).

Umsetzung: `src/skaldir/input_backend.py` (Wahl per Zustellung) und
`HostSessionRunner._delivering_input_backend()` (Tor im Live-Pfad). Der uinput-Pfad
übersetzt Key-Namen über die echte Keymap (`xmodmap -pke`), ein Zeichen ohne Keycode
wird abgelehnt.

## Was NICHT getan wurde

- Kein Kernel-, Modul- oder udept-param-Eingriff.
- Kein zweiter Fenstermanager.
- Kein fremder Prozess beendet, keine fremden Sperren oder Recovery-Daten gelöscht.
- Kein automatisches Schließen eines Freigabedialogs. Wenn der Portal-Weg (Kandidat 2)
   der richtige ist, ist die Zustimmung des Nutzers **transparent** einzuholen — das
   Portal-Fenster bleibt für den Nutzer sichtbar.

## Nächster Schritt (offen, beauftragt)

Den Portal-Weg prüfen: Läuft ein `xdg-desktop-portal` mit einem Eingabe-Portal
(`libei`/EIS) in dieser Sitzung, und würde es synthetische Tastatureingaben an das
Xwayland-Fenster zustellen? Dafür braucht es die Zustimmung des Nutzers zum Portal —
die ist **nicht** vorausgenommen und wird nicht umgangen.

## Nachtrag (2026-09-30, bei der Git-Adoptierung)

Der offene „Nächste Schritt" ist in Code überführt: `BACKENDS` in
`src/skaldir/input_backend.py` (Zeile 73) enthält jetzt neben `ydotool` und `xdotool`
auch `"portal"` — der Portal-/EIS-Weg (Kandidat 2) ist als drittes Backend angelegt
(Commits `69acba2`, `715447d`, `f667366`, 2026-09-28). Diese Nachtrag ändert nichts an
der Entscheidung selbst und behauptet **nicht**, dass das Portal-Backend live zustellt.
