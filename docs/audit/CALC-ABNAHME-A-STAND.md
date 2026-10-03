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

## Nachtrag (2026-10-02) — Live-Kette erstmals vollständig bestanden, über den produktiven Computer-Use-Pfad

Die Zustellungsblockade der Tastatur ist seit der Eingabe-Live-Abnahme (2026-09-30,
`ABNAHME-EINGABE-LIVE.md`) behoben: ydotool/uinput stellt an Xwayland-Fenstern wirklich zu.
Damit lief die AMD→Calc-Kette (VAL-CALC-003/004/005) erstmals **vollständig auf dem echten
Host-Desktop** durch. Rohe Belege: `logs/calc-acceptance/` (gitignoriert; Stage-Reports
`chain-*.json`, Screenshots, OCR-Texte, Skript `calc_chain_20261002.py`).

### Rahmen

- **Datei:** `/home/willy/AMD_Latest_Stock_20261002-132318.ods` (neues Testartikel aus
  amd-ods-builder, Start-Hash `c2c9a8405e851137…`). `/home/willy/AMD_latest_data.ods` wurde
  **nicht** angefasst — SHA-256 vor/nach identisch
  `eb54f9ff9c3b8a153deb8fa81f1fe745c56e0758d8d5cfe5e5e584475d4c0fac`.
- **Eigene Instanz:** frisches Profil `-env:UserInstallation=file:///tmp/skaldir-calc-test/profile`,
  `--norestore`, cwd `/tmp/skaldir-calc-test/`; nie fremdes LibreOffice berührt, nur die von uns
  aufgezeichneten PIDs/Fenster geschlossen. Keine Lock-/Recovery-Daten angefasst.
- **Desktop-Lease + DESK-03-Indikator** in jeder Eingabe-Etappe; DESK-02-Fence an
  `bind_input_fence` gebunden (genau wie `act_service._bind_desktop_fence` es verdrahtet).
- **Fokusbindung über `windowactivate --sync <wid>`, nicht `windowfocus`** (gemessene
  Dispatcher-Lücke); nach jeder Aktivierung mit `xdotool getactivewindow` verifiziert, dass der
  eigene Zielfenster-ID aktiv ist, bevor irgendeine Taste gesendet wurde.
- **Backend-Probe am Etappen-Start** (begrenzte Probe des Runners selbst,
  `_delivering_input_backend()`): in **allen 8 Etappen** lieferte **ydotool**
  (`INPUT_OBSERVED_AT_TARGET`, Marker-Korrelation am eigenen Probe-Fenster). Marker:
  open `SKP077884e99833` · edit `SKP0b2a21e0eb20` · fix `SKPd50d85eac0ed` · save `SKPf8bb2828842d`
  · close `SKP2ec7b53a61b0` · reopen `SKPbdbec56ca98c` · reopen-close `SKP8fc483f3de01`.
  xdotool/portal wurden in dieser Kette nicht erneut gemessen (der Runner stoppt beim ersten
  liefernden Backend — der Flip zwischen Läufen bleibt als bekannter Restdefekt stehen).

### Kette (jeder Schritt beobachtet, rc + Zustand)

| Schritt | Befehl (produktiver Pfad) | rc | Beobachtung |
|---|---|---|---|
| Öffnen | `soffice --norestore --calc <ODS> -env:UserInstallation=…` | 0 | Fenster wid 12583050, Titel `AMD_Latest_Stock_20261002-132318.ods — LibreOffice Calc`, WM_CLASS `"libreoffice","libreoffice-calc"`, App-PID 142270 via `_NET_WM_PID`, 1920×1011, maximiert |
| Aktivieren | `runner.act(cli: xdotool windowactivate --sync 12583050)` | 0 | `getactivewindow` == 12583050 |
| Screenshot | `HostSessionRunner.screenshot()` (python-xlib GetImage) | 0 | `open_check.png`, 1920×1011, 152228 B |
| Sichtbarer Inhalt | OCR (tesseract.js WASM, lokales `eng.traineddata`, offline) | 0 | Werte unten |
| Zelle wählen | `runner.act(key ctrl+home)` → `down` → `right` | 0 je | Screenshots: Name-Box `A1` → `B2` (nur durch die Pfeiltasten erreicht — Zustellung beobachtet) |
| Formel tippen | `runner.act(type_text "=2+2")` | 0 | **kam als `22` an** (Befund 1) |
| Korrektur | `runner.act(key delete)` → `type_text "4"` → `enter` | 0 je | Name-Box B2→B3, B2 zeigt `4` |
| Speichern | `runner.act(key ctrl+s)` | 0 | Datei-Hash ändert sich `c2c9a840…`→`27db7c31…`; content.xml: B2 `office:value-type="float" office:value="4"` |
| Schließen | `runner.act(key ctrl+q)` | 0 | aufgezeichneter App-PID 142270 beendet (nur beobachtet, nie gekillt); kein soffice-Prozess mehr |
| Wiederöffnen | gleicher Launch-Befehl | 0 | neues Fenster wid 14680100, App-PID 189464, gleicher Titel/WM_CLASS |
| Wertnachweis | Screenshot + `unzip -p … content.xml` | 0 | `4` **sichtbar** in B2 im Wiederöffnen-Screenshot (`reopen.png`) **und** maschinell in content.xml (`office:value="4"`); Endinstanz per `ctrl+q` sauber geschlossen, PID 189464 beendet |

Sichtbarer Inhalt beim Öffnen (OCR gegen Datei-Daten abgeglichen): Kopfzeile
`AMD — Latest Stock Summary (fetched 2026-10-02T13:02:22+02:00 Europe/Berlin)`, Feld/Value-Paar
AMD · Advanced Micro Devices, Inc. Common Stock · NASDAQ-GS · USD · Pre-Market · 624.35 ·
615.73 · 8.62 · +1.40% · 624.25 · 624.51 · `163.14 - 639.00`, Quellen-/Abrufzeit-Zeilen
(Nasdaq free public API, URLs, Europe/Berlin), Tab-Leiste `Summary`/`daily OHLCV`,
`Sheet 1 of 2`. Kein abweichender Wert zwischen Datei und Bild.

### Befunde (ehrlich, mit Ursache)

1. **P2 — `ydotool type` verwirft nicht abbildbare Satzzeichen still:** `type_text "=2+2"`
   kam am Ziel als `22` an (Eingabezeile UND Zelle zeigten `22`); rc war 0, kein Fehler. Auf
   dieser Host-Konfiguration lieferten Ziffern zuverlässig, `=` und `+` wurden lautlos
   übersprungen. Zustellungsmodell korrekt angewandt: das wäre ohne Ziel-Beobachtung als
   falsches "rc 0 = Erfolg" durchgerutscht. Nächster Schritt: ydotoold-Keymap prüfen/setzen
   bzw. zeichenweise Verifikation in der Eingabe-Schicht.
2. **P2 — die gebaute ODS hat keine expliziten Spaltenbreiten:** die typisierten Datumszellen
   (B10/B13/B24/B25 im Summary; Datumsspalten in Daily OHLCV) rendern als `###`, weil die
   Spalte zu schmal für das Datumsformat ist. Die Datumswerte selbst sind korrekt getippt
   (content.xml: `office:date-value="2026-10-02T07:02:00"` u. a.); im Speicher-Ergebnis von
   Calc steht der Anzeigetext `2026-10-02 07:02`. Nächster Schritt: `tools/amd_ods_build.py`
   um Spaltenbreiten (z. B. 2.4 cm für Datums-/Datetime-Spalten) erweitern.
3. Anmerkung zur Methode: dieses Browsing-Modell kann die Screenshots nicht direkt sehen;
   die Sichtbarkeits-Belege sind maschinell aus den Fenstern-Pixeln gewonnen (python-xlib
   GetImage → OCR/Glyph-Rendering), nicht durch bloße Datei-Existenz.

### Stand

Abnahme A (Schritte 4–7) ist damit **live-verifiziert** über den produktiven Computer-Use-Pfad:
Öffnen mit korrektem sichtbaren Inhalt (mit der ###-Einschränkung der Datums-Spaltenbreite),
Testzelle B2 produktiv bearbeitet und beobachtet (`INPUT_OBSERVED_AT_TARGET` + sichtbarer
Wert + content.xml), speichern/schließen/wiederöffnen mit persistiertem Nachweis `4`.
Die alten Befunde dieser Datei (Fokus-/Zustellungs-Blockade vom 2026-09-26) sind durch den
Eingabe-Fix vom 2026-09-30 überholt und hier erstmals praktisch bestätigt behoben.

## Nachtrag (2026-10-02, Feature `ods-column-widths`) — Befund 2 (###-Spaltenbreiten) behoben

Befund 2 oben ist im Builder behoben: `tools/amd_ods_build.py` setzt jetzt explizite
Spaltenstile mit `style:column-width` (Datums-/Wertspalten 2,4/3,2 cm, Preise 2,2 cm,
Volumen 2,6 cm, Beschriftungsspalte 12 cm). Neues kanonisches Artefakt (nie überschrieben,
neuer Stamp): `/home/willy/AMD_Latest_Stock_20261002-141150.ods`
(sha256 `1aac31ef…3f61`), gebaut aus dem gesicherten Rohbeleg. Headless-PDF-Render:
**0 `###` auf allen 8 Seiten** (alt: 4 `###` auf Seite 1 — Reproduktion in
`logs/amd-ods-cw/old-pdf-page1.txt`, Fix-Beweis in `logs/amd-ods-cw/pdf-page1.txt`);
Zell-Semantik gegen den Alt-Builder: 1694 Zellen, 0 Unterschiede. Details und Grenzen
(URL-Abschnitt am PDF-Seitenrand) im Nachtrag in `docs/audit/QUELLENPRUEFUNG-AMD.md`.
Der ###-Einschränkungshinweis im Abschnitt „Stand" oben gilt für die Datei 132318
(Stand der Abnahme) — für weitere Calc-Läufe ist 141150 das Testartikel.

## Nachtrag (2026-10-03, Feature `input-type-charset-fix`) — Befund 1 (Zeichen-Drop) behoben

## Ursachen und Messreihe

Neue Vermessung (eigenes Probe-Fenster auf `:0`, Empfänger am Fenster selbst, rc überall 0):

| Zeichenklasse | `ydotool type` (alter String-Send) | Detail |
|---|---|---|
| Kleinbuchstaben, Großbuchstaben, Ziffern 0–9, Space, `-` | zugestellt | intakt; Shift setzt ydotool automatisch |
| ASCII-Satzzeichen `=+_.!:;',()[]{}<>/ ?@#~$%&|^"\*` | zugestellt (heute) | Shift-Halt automatisch; der 072b98f-Stand ('=2+2'→'22') reproduzierte heute NICHT — Zustände flippen zwischen Läufen, der String-Send bleibt unvertrauenswürdig |
| Umlauts/Ergänzungen `äöüÄÖÜß é è § µ °` | **STILL GEFALLEN** | rc 0, NICHTS angekommen — obwohl die Layout-Ebene 3 (AltGr) sie trägt |
| `€` | **STILL GEFALLEN** | rc 0; sitzt nur auf Ebene 5+ (zweite Gruppe, ohne Tastenfolge erreichbar) |
| `世 ж ة` | **STILL GEFALLEN** | rc 0 |

Ursache fixiert am Produkt: der String-Send verlässt sich auf ydotools eigene virtuelle
Tastatur, die Zeichen still verwirft, die sie nicht kennt. Behoben im Produkt (alle
Grenzen gemessen): `src/skaldir/text_keys.py` löst jedes Zeichen auf **der echten Keymap
dieser Session** (`xmodmap -pke`, Ebenen 1–4) auf und tippt evdev Press/Release mit
Shift-/AltGr-Halt (`ydotool key`, X−8); ein Zeichen, das keine Ebene trägt, wird VORAB
VERWEIGERT (`UnmappableTextError` → `TargetScopeRefused` benennt die betroffenen Zeichen)
statt still zu fallen. `ydotool type` selbst bleibt unverändert gemessen.

Live-Nachweis am eigenen Zielfenster (Sechs-Zustands-Modell, `INPUT_OBSERVED_AT_TARGET`
per Marker-Korrelation, niemals nur rc): `SKP7c21=2+2!`, `skp3f0;:@()`, `skp5d4äöüß`,
`skpH94a2z` intakt über den begrenzten Probe mit `keymap=`; über die echte
`HostSessionRunner.act()`-Kette (DESK-02-Fence gebunden, Lease erworben und freigegeben):
`'=2+2'`, `'0-1+4;:!=('`, `'?@'` intakt. Rohbefunde: `logs/input-charset-survey/`
(survey- und proof-Skripte, xmodmap-Stand 2026-10-03). Zusätzlich behoben dabei, alles
durch die Live-Läufe entdeckt: `ydotool`-Kinder müssen `YDOTOOL_SOCKET` auf den
Mission-Daemon sehen (`_child_env` pinnt ihn); der Probe-Empfänger dekodiert AltGr-Ebene
(`_keysym_index`); Probe-Titel sind deterministische ASCII-Slugs (nicht-ASCII-Läufe
machten den Probe `PROBE_MISCONFIGURED`); `run_command` dekodiert mit
`errors="replace"`.
