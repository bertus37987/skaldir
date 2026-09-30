# Calc-Abnahme A — Stand nach der Ursachenisolierung

Datum: 2026-09-26. Display `:0` (Xwayland unter GNOME/Wayland).
Skripte unter `logs/calc-acceptance/` (gitignoriert), Belege in `cu-screenshots/`.

## Was jetzt funktioniert (bewiesen)

| Schritt | Beweis |
|---|---|
| GUI-App startet und zeigt Fenster | `TESTDATA_kurs_abnahme.ods — LibreOffice Calc`, `WM_CLASS = "libreoffice", "libreoffice-calc"`, maximiert |
| Fenster ist erfassbar | `import -silent -window 18874404` → **80329 Bytes, 1920×1011**, dekodierbares PNG |
| Sichtbarer Inhalt korrekt | Kopfzeile `Ticker \| Wert \| Datum \| Anzahl`, Zeilen `AAA 12.5 46289 100`, `BBB 3.75 46290 250`, `CCC 100 46290 1`, Name-Box `A1`, `Sheet 1 of 2` |
| Fensterzuordnung möglich | `xprop -id <wid> _NET_WM_PID` liefert die echte App-PID (z. B. 84158) |

Voraussetzung dafür: **gültige ODS + frisches Profil**. Ein defektes Fixture führt zu
`FRISCH_abnahme.ods (repaired document)` und einem nicht erfassbaren Hauptfenster.

## Was blockiert — isolierte Ursache (KORRIGIERT 2026-09-27)

**Tastatureingaben erreichen das Xwayland-Fenster nicht.**

> **Korrektur zu dem, was hier vorher stand:** Die Deutung „der Fokus liegt auf einem
> Shell-internen Fenster (4194307), deshalb gehen die Tasten dorthin" war **falsch**.
> `xdotool getwindowfocus` liest eher das aktive WM-Fenster als den X-Fokus. Der
> **X-Server** hat über die ganze Zeit das eigene Zielfenster im Fokus geführt
> (Xlib `get_input_focus()` → `0xa000d2` = Ziel). Der Fokus war korrekt; die Blockade
> liegt in der **Zustellung**, nicht im Fokus.

Bewiesene Kette (vollständig in `ABNAHME-A-LIVE-EINGABE-UND-CALC.md`):

1. `ydotool key 38:1 38:0` erzeugt einen **echten Kernel-Event**
   (`type=1 code=38 value=1`, am Gerät `event257` mitgelesen);
2. **gnome-shell (PID 4189) liest das Gerät** (`/proc/4189/fd`);
3. das Gerät ist eine vollwertige Tastatur (`B: KEY=…`, `B: REL=…`);
4. X-Server-Fokus sitzt bestätigt auf dem Ziel;
5. das Ziel meldet **0 Events**;
6. `ydotool mousemove -a 100 100` bewegt die Maus **nicht** (vorher/nachher identisch).

Kontrollversuch, der XTEST entlastet: `xdotool key --window <nicht existent>` liefert
`BadWindow` — XTEST funktioniert, die Zustellung an ein Xwayland-Fenster bleibt aus.

**Was damit erreicht ist:** Abnahme A ist **nach** dem Fensterschritt blockiert — Zelle
auswählen → Wert eingeben → prüfen → speichern → erneut öffnen scheitert am 2. Schritt.
Der Blocker ist präzise benannt (Zustellung, nicht Fokus, nicht die Anwendung).

## Hypothesentabelle (Kurzfassung, Details: siehe CALC-FENSTER-HYPOTHESEN.md)

| # | Hypothese | Tatsächlich | Status |
|---|---|---|---|
| K0 | bewährtes Verfahren zeigt Fenster | ja | bestätigt |
| H1 | separates Profil zeigt Fenster | ja | bestätigt |
| H2 | Gegenversuch ohne Profil | ebenfalls Fenster | **widerlegt** |
| H3 | Titel „LibreOffice 26.2" = Hauptfenster | 200×200-Fenster; Hauptfenster heißt „VCL ImplGetDefaultWindow" | **widerlegt** |
| H4 | Titel „TESTDATA…" ist suchbar | mit gültiger Datei ja | bestätigt |
| H5 | `import -window` erfasst jedes Fenster | nur gemappte | **widerlegt** |
| H6 | Ursache ist `WM_STATE` | zenity Normal ✓ / Calc Withdrawn ✗ | bestätigt (für die alte, defekte Datei) |
| H7 | `windowmap` hebt Withdrawn auf | nein | **widerlegt** |
| H8 | ~~Tastaturfokus unerreichbar~~ | Fokus war korrekt; die **Zustellung** fehlt | **korrigiert** (Deutung war falsch) |

## Widerlegte frühere Annahmen

- verwaiste LibreOffice-Profil-Sperre
- separates `-env:UserInstallation`-Profil als Variable
- „Calc startet als Wayland-Client"
- Titelzuordnung über `getwindowpid` (liefert unter Xwayland den compositor, nicht die App)
- **„Fokus unter Wayland unmöglich"** — der Server-Fokus saß richtig (Xlib
  `get_input_focus()`), das Problem ist die Zustellung

## Nächster Schritt

Der Zustellungsweg ist die Blockade. Zwei zu prüfende Wege, **ohne** einen zweiten
Fenstermanager auf dem laufenden Desktop zu starten:

1. Prüfen, ob ein `xdg-desktop-portal` mit **Eingabe-Portal** (`libei`/EIS) läuft — die
   Bibliotheken sind installiert (`/usr/lib64/libei.so.1`, `libeis.so.1`), aber kein
   Portal-Frontend. Synthetische Eingaben laufen unter GNOME/Wayland darüber, und die
   Zustimmung des Nutzers zu diesem Portal ist **transparent** einzuholen — nicht zu
   umgehen.
2. Prüfen, ob gnome-shell das virtuelle Gerät zwar liest, aber nicht als Eingabequelle
   akzeptiert.

Bis dahin gilt Abnahme A als **blockiert** — nicht als bestanden.
