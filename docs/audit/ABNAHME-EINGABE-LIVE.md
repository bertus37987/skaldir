# Abnahme Eingabe — LIVE-Zustellungsnachweis am Ziel (VAL-INPUT-007 / VAL-INPUT-008)

Datum: 2026-09-30. Host: Fedora, GNOME/Wayland, Xwayland auf `:0` (echte Desktop-Sitzung,
kein Xvfb, kein Fixture). Feature: `input-live-proof` (live-acceptance-worker).
Rohzeuge: `logs/input-live/` (gitignored). Sanitisiertes Protokoll, keine Rohtranskripte.

## Ergebnis in einem Satz

**Am 2026-09-30 wurde zum ersten Mal auf diesem Host ein Tastaturevent AM ZIELFENSTER
nachgewiesen**: `ydotool` (uinput) lieferte den Zufalls-Marker an das eigene, bestätigte
Probe-Fenster — Zustand **`INPUT_OBSERVED_AT_TARGET`**, zweimal mit unabhängigen Markern.
Das zustellende Backend nach belegter Fähigkeit ist damit **ydotool**; `xdotool` bleibt
ehrlich `INPUT_SENT` (rc 0, kein Event am Ziel), der Portal-/EIS-Weg ist
`CONSENT_MISSING` (Zustimmung der Nutzerin steht aus, wurde nie umgangen).

## 1. Diagnose der gemessenen ydotoold-Verweigerung (zuerst, wie beauftragt)

Der bekannte Zustand „Socket `/tmp/skaldir-ydotoold/ydotoold.sock` verweigert
Verbindungen" wurde neu gemessen und ist jetzt **vollständig erklärt**:

| # | Messung | Befehl/Beobachtung | Ergebnis |
|---|---------|--------------------|----------|
| 1 | Kaltstart über `services.yaml` (Fassung vor der Korrektur) | `ydotoold --socket-path /tmp/skaldir-ydotoold/ydotoold.sock …` | Daemon stirbt sofort: `failed to bind socket: No such file or directory` — ydotoold legt das **Elternverzeichnis nicht an**; `test -S` schlägt fehl (rc 1), keine Socket-Datei |
| 2 | Start nach `mkdir -p /tmp/skaldir-ydotoold` | gleiche argv, zweiter Lauf | Daemon läuft (PID 848486), Socket-Datei existiert (`srw-------` willy:willy), Daemon hält `/dev/uinput` offen (User in Gruppe `input`, ACL `user:willy:rw-`) |
| 3 | Verweigerung reproduziert | 5× connect gegen den laufenden Daemon | **`Errno 91` (EPROTOTYPE, „Protocol wrong type for socket") — stabil** |
| 4 | Ursache | `ss -xl \| grep ydotool` | Der Socket ist **`u_dgr` = SOCK_DGRAM** — ydotoold bietet einen **Datagramm**-Socket an |
| 5 | Gegenprobe | connect mit `SOCK_DGRAM` | **erfolgreich**; connect mit `SOCK_STREAM` → Errno 91 |

**Die gemessene Verweigerung war also nie eine Daemon-Störung:** Der Produkt-Check
(`skaldir.input_backend._unix_connect`) verband ausschließlich mit `SOCK_STREAM` gegen
einen `SOCK_DGRAM`-Socket → EPROTOTYPE → ein **lebender Daemon wurde als
`BACKEND_MISSING` klassifiziert** („Socket-Datei existiert, Verbinden verweigert").
Die Verweigerung trat exakt so auf, wie sie gemessen war — die Ursache lag im
Erreichbarkeits-Check, nicht im Daemon und nicht am Host/Compositor.

### Korrekturen (klein, mit Regressionstest)

1. `src/skaldir/input_backend.py::_unix_connect` — probiert jetzt `SOCK_STREAM` **und**
   `SOCK_DGRAM` (Reihenfolge im Fehlerbild benannt) und zählt nur einen echten connect
   als erreichbar. Eine nur noch liegende Socket-Datei täuscht nichts: spätestens die
   Sendung endet mit rc≠0 (Vorbedingung ist nie der Beweis).
2. Tests: `test_ein_lebender_datagram_daemon_ist_erreichbar` (zuerst rot gemessen),
   `test_ein_stream_daemon_bleibt_erreichbar` (der ältere Weg bleibt), und der
   bestehende Test `test_ein_socket_der_verbindungen_verweigert_ist_nicht_erreichbar`
   (Listener geschlossen → ECONNREFUSED) bleibt unverändert grün — nichts wurde
   abgeschwächt.
3. `services.yaml` `ydotoold.start`: `mkdir -p /tmp/skaldir-ydotoold` ergänzt (Kaltstart
   Defekt 1); `stop` auf die Klammer-Schreibweise `ydotool[d]` umgestellt, damit
   `pkill -f` nicht die eigene Shell trifft (gemessene Falle). Start/Healthcheck/Stop
   wurden danach einmal komplett gemessen: Start rc 0, `YDOTOOLD_OK`, Stop tötet nur den
   Mission-Daemon (PID-gezielt verifiziert), die eigene Shell überlebt, der private
   ydotoold der Nutzerin (PID 1001, `/tmp/.ydotool_socket`) blieb unberührt.

## 2. Live-Läufe (echter Desktop, exklusive Lease, sichtbarer Indikator)

Alle Läufe: Repo-Venv (`uv run`), `DISPLAY=:0`, das **echte** Lease-Store-File
(`~/.skaldir/state/desktop-lease.json`), DESK-02-Fence aus der echten Lease gebunden
(gleiche Verdrahtung wie `act_service._bind_desktop_fence`), Stop-Hook liest
`DesktopLeaseStore.current().stop_requested` — genau das Signal, das der physische
Doppel-Escape-Listener über `request_stop` setzt. Der DESK-03-Corner-Indikator war vor
der ersten Fokusberührung sichtbar („Skaldir controls the desktop / Task: … /
Esc, Esc to pause", Ecke unten rechts, 272×62 px).

### 2.1 Zustellungsnachweis (VAL-INPUT-007)

| Lauf | Backend | Zustand | Marker | wid / PID | Korrelation |
|------|---------|---------|--------|-----------|-------------|
| 21:43:14 | ydotool | **`INPUT_OBSERVED_AT_TARGET`** | `SKP0bb535ff7ddb` | 14680064 / 853858 | „the marker arrived at the probe window inside the send-time window" |
| 21:43:20 | xdotool | `INPUT_SENT` | `SKPeed30e46698b` | 14680064 / 853952 | „the target reported no event at all" (rc 0 = Versand, nicht Zustellung) |
| 21:43:20 | portal | `CONSENT_MISSING` | — | — | RemoteDesktop-Portal vorhanden, Zustimmung fehlt; es wurde **keine** Portal-Session erzeugt und **kein** Dialog angenommen/verworfen |
| 21:47:38 | ydotool (Wiederholung, frischer Marker) | **`INPUT_OBSERVED_AT_TARGET`** | `SKPea5822122fe2` | 14680064 / 864703 | wie oben |

Die Fenster-Bindung war jedes Mal bestätigt, **bevor** gesendet wurde — alle sechs
Prüfungen grün (aus dem Bericht):

```
window_on_display=true, title_is_ours=true, activate_rc=0, wm_state=Normal,
map_state=IsViewable, net_wm_pid=<probe pid>, server_focus=14680064
(focus_method: xlib get_input_focus — die Server-Meinung, nicht die des WM)
```

Der Empfänger des Zielfensters meldete die Marker-Zeichen einzeln — **mit korrekten
evdev-Codes** (X−8-Übersetzung live nachgewiesen, VAL-INPUT-003):

```
keycode 39 → keysym 83 'S'   (KEY_S = 39, NICHT der X-Code 47)
keycode 45 → keysym 75 'K'   (KEY_K = 45)
keycode 33 → keysym 80 'P'   (KEY_P = 33)
keycode 19 → keysym 48 '0'   (KEY_0 = 19)
```

Sichtbarkeitsnachweis der Fenster: `import` (ImageMagick 7.1.2-13) verweigert auf
diesem Host jede Aufrufform („missing an image filename"), `xwd` ist nicht installiert,
`gnome-screenshot` fällt auf X11 zurück und zeichnet nichts, und `ffmpeg x11grab`
sieht nur den Xwayland-Root, der auf Wayland keine komponierten Client-Inhalte trägt.
Der Sichtbarkeitsbeweis lief deshalb über **python-xlib `GetImage` am Fenster selbst**
(`logs/input-live/capture_window.py` → `indicator_content.png` (272×62, drei
Textzeilen), `probe_window_content.png` (510×287, gezeichnete Inhalte)) — das Fenster
ist mapped UND sein Inhalt ist aus dem X-Server gelesen.

### 2.2 Lease/Fence/Not-Aus und sauberes Aufräumen (VAL-INPUT-008)

Lease-Generationen über die Läufe: 23 → (Stopp 24) → 25 → (Stopp 27) → 28 → 29.

| Prüfung | Live-Beweis |
|---------|-------------|
| Fence vor **jedem** Senden | 4 Fence-Aufrufe protokolliert (ydotool-Probe ok, xdotool-Probe ok, Stale-Fence-Probe **verweigert**, Warteabbruch-Probe ok) |
| Stale-Fence bricht ab, **bevor** gesendet wird | `request_stop` (Reason „double-escape (driver: same hook the physical listener calls)") hob die Generation 23→24; die Probe meldete `aborted=True`, `sent=False`, `send_rc=None`, Detail „the DESK-02 input fence refused the probe before sending (StaleFence: generation 23 … 24: action dropped, not executed)" |
| Stop im Wartetakt innerhalb **eines** Poll-Ticks | Stop ausgelöst auf Generation 27 → die Schleife sah ihn nach **0,015 s** (< ein 0,05-s-Tick), die Probe kehrte **0,049 s** nach der Anfrage zurück (15 Poll-Ticks zuvor, Backend xdotool — das ehrliche `INPUT_SENT`, dessen Warteschleife polliert); Ergebnis `aborted=True` auf einer Sendung mit rc 0 — UNKNOWN, kein Erfolg |
| Cleanup nur eigener Ressourcen | Nach jedem Lauf: `xdotool search --name SKALDIR-PROBE-` leer, `/tmp/skaldir-input-probe-*` entfernt (nur das eigene mkdtemp des Probe), Fensterlisten-Delta vor/nach leer, kein Empfänger-Prozess übrig, Lease `released=True`, Indikator `hidden` |

Ehrlich vermerkt: Der erste Versuch von `visible_run.py` stürzte (fehlendes PIL im
Venv) und ließ Lease-Generation 28 als `controlling` zurück — durch denselben
`run_id`-bezogenen `release()` aufgeräumt, danach freigegeben und der Lauf grün
wiederholt. Ein Fehler dieses Drivers ist keine Eigentümlichkeit des Produkts; die
Lease blieb trotzdem aufräumbar.

### 2.3 Fokus-Nebenwirkung (transparent, wie beauftragt)

Die Probe aktiviert ihr eigenes Fenster (`windowactivate --sync`), um Zustellung messen
zu können — das entzieht dem aktuell fokussierten Fenster kurz den Fokus (~1 s pro
Probe-Lauf, ~30 s Gesamtfenster über alle Läufe). Gemessen: Das zuvor aktive Fenster
(6291459) war nach dem Lauf wieder dasselbe — GNOME gab den Fokus nach der Zerstörung
des Probe-Fensters zurück. Kontrolle während der Läufe: Indikator sichtbar, Lease
exklusiv, Abbruch über Esc-Esc-Physis-Pfad durch denselben Hook nachgewiesen.

## 3. Sechs-Zustände-Bilanz

| Zustand | Wer erreichte ihn (live) | Bedeutung |
|---------|--------------------------|-----------|
| `BACKEND_MISSING` | — (nicht erreicht: beide Sende-Backends waren benutzbar) | — |
| `CONSENT_MISSING` | **portal** | der offizielle Weg braucht die Zustimmung der Nutzerin — nächster Schritt: den `org.gnome.Mutter.RemoteDesktop`-Zustimmungsdialog bewusst erteilen, dann Re-Probe |
| `PROBE_MISCONFIGURED` | beide Abbruch-Läufe (bewusst, unbekannt) | Setup-Fehler/Abbruch ist UNKNOWN, nie ein Backend-Defekt |
| `INPUT_SENT` | **xdotool** | rc 0, kein Event am Ziel — Versand, nicht Zustellung |
| `INPUT_OBSERVED_AT_TARGET` | **ydotool** (2×, unabhängige Marker) | **Zustellung am eigenen Zielfenster bewiesen** |
| `APP_EFFECT_VERIFIED` | nicht beansprucht | eine Probe misst Zustellung, nie Anwendungswirkung (der einzige Weg dahin bleibt `promote_to_effect_verified` mit Beleg) |

**Backend-Wahl nach belegter Fähigkeit: `ydotool`** (Reihenfolge der Prüfung war
ydotool → xdotool → portal, wie `input_backend.BACKENDS`).

## 4. Nachweisführung (kommandos mit echten Exitcodes)

| Befehl | rc | Beobachtung |
|--------|----|-------------|
| `services.yaml` ydotoold start (alt) | 1 | Daemon tot, „failed to bind socket: No such file or directory" |
| `services.yaml` ydotoold start (mit mkdir -p) | 0 | `YDOTOOLD_OK`, PID 865736; danach Stop → nur Mission-Daemon weg |
| `uv run pytest tests/test_input_probe_bounded.py` | 0 | 52 passed (Regression vor der Korrektur) |
| `uv run pytest tests/test_input_backend.py … -q` (5 Dateien) | 0 | **146 passed** nach der Korrektur (inkl. 2 neue Socket-Tests) |
| `uv run ruff check src/skaldir/input_backend.py tests/test_input_backend.py` | 0 | „All checks passed!" |
| `DISPLAY=:0 uv run python logs/input-live/live_probe.py` | 0 | ydotool `INPUT_OBSERVED_AT_TARGET`; xdotool `INPUT_SENT`; portal `CONSENT_MISSING`; Cleanup sauber |
| `DISPLAY=:0 uv run python logs/input-live/wait_abort.py` | 0 | Stop im Wartetakt: 0,015 s bis gesehen, 0,049 s bis Rückkehr |
| `DISPLAY=:0 uv run python logs/input-live/visible_run.py` | 0 | zweite Zustellung `SKPea5822122fe2` + Fensterinhalte gelesen |

Rohberichte: `logs/input-live/report-20260930-214322.json`,
`wait-abort-report-20260930-214641.json`, `visual-report-20260930-214738.json`,
`run-stdout.log`.

## 5. Was damit für den Masterplan gilt

- **M1 „Live-Nachweis: Eingabe wird am eigenen Ziel-Fenster beobachtet"** — **erbracht**
  (zweifach, mit sichtbarer Kontrolle und Not-Aus-Nachweis).
- Die alte Messkette (2026-09-27, „5 Wege rc 0, 0 Events") bleibt historisch richtig,
  erhält aber eine Erklärungsnachricht: Der alte ydotool-Sendeweg benutzte
  unübersetzte X-Codes (`38:1` = `KEY_L` statt `KEY_A`) und/oder das von ydotool
  nicht gekannte Flag `--delay` (rc bleibt 0, Typing findet nicht statt), und der
  Empfangsprüfung fehlte der gemessene Empfangsweg — die Zustellung des heute gültigen
  Pfads (übersetzte evdev-Codes + gültige Flags + aktiver Xlib-Empfänger) war damit
  nie widerlegt. Für `xdotool`/XTEST bleibt die Lücke trotzdem real gemessen (rc 0,
  kein Event am Ziel, heute erneut).
- Offen (nächster Schritt, nicht Blocker der Eingabekette): Portal-/EIS-Zustimmung der
  Nutzerin erteilen und den dritten Weg live messen; `APP_EFFECT_VERIFIED` über den
  produktiven Pfad in einer echten Anwendung (Calc) — Gegenstand der AMD→Calc-Abnahme.

---

# Nachtrag 2026-09-30 — Screenshot-Fallback über python-xlib GetImage (Feature cu-screenshot-fallback)

Datum: 2026-09-30 (gleicher Tag, nachträglich gemessen). Feature: `cu-screenshot-fallback`
(backend-worker). Rohzeuge: `logs/cu-xlib-capture/` (gitignored), Referenzmuster:
`logs/input-live/capture_window.py`.

## Ergebnis in einem Satz

**Der produktive Capture-Pfad des Produkts liefert wieder echte Pixel**: Beide auf diesem
Host gemessenen Werkzeuge sind nachweislich defekt (siehe §2 der Originalabnahme) —
`HostSessionRunner.screenshot()` liest jetzt die Fensterelemente **primär direkt vom
X-Server** (python-xlib `GetImage`, neu `src/skaldir/xlib_capture.py`, Konverter-Name
`builtin-xlib`) und behält `xwd`/`import` als Rückfallebene für Hosts ohne python-xlib.

## 1. Was jetzt funktioniert (mit Marker-Nachweis)

- Testdatei `tests/test_cu_xlib_capture.py` (TDD, erst fehlschlagend: Sammlung brach mit
  `ModuleNotFoundError: No module named 'skaldir.xlib_capture'` ab, rc 2):
  - Xvfb-tauglicher Einheitsteil: eigenes Marker-Fenster (rot 120×80, blaues 20×20-Quadrat
    in der Mitte) auf einem eigenen Xvfb, PNG-Abmessungen == Fenstergeometrie, Marker-Pixel
    an Eckpunkt und Mitte verifiziert, Datei-Hash == `ScreenCapture.sha256`.
  - **Live-Beweis auf dem echten `:0`**: dasselbe eigene Fenster auf dem Real-Desktop,
    Ziel ist die Fenster-ID (der Live-Runner verweigert den Titel zu Recht — mutter
    benennt den Rahmen mit dem Client-Titel, gemessen: 2 Treffer), Abmessungen + Marker
    verifiziert; eigenes Fenster nur über seine gemessene ID zerstört.
  - Negativfälle: unbekannte Fenster-ID → `None` mit Grund (ID genannt), keine
    Stellvertreter-Datei; feindliche ID (`12; touch ignored`) wird **vor jedem X-Aufruf**
    als Argumentfehler abgewiesen; ohne python-xlib greift die alte Kette
    (`import`-Rückfall, Konverter `host-import`) — Kette bleibt intakt.
- Gemessene WM-Falle (für spätere eigene Fenster-Tests): ein um-parentender WM (openbox auf
  dem eigenen Xvfb, mutter auf `:0`) verwirft Inhalte, die **vor** dem Settle gezeichnet
  wurden — zeichnen erst nach `map_state == 2` plus kurzem Warten, sonst liest man Schwarz.

## 2. Nachweisführung (kommandos mit echten Exitcodes)

| Befehl | rc | Beobachtung |
|--------|----|-------------|
| `uv run pytest tests/test_cu_xlib_capture.py -q` (vor der Implementierung) | 2 | Sammlung bricht ab: `ModuleNotFoundError: skaldir.xlib_capture` (fehlschlagend-erst, kein Skip-als-grün) |
| `uv run pytest tests/test_cu_xlib_capture.py -v` (nach der Implementierung) | 0 | **6 passed** (inkl. Live-Beweis auf `:0`, 69.8 s) |
| `uv run pytest tests/test_live_desktop_launch_env.py tests/test_security_audit_regressions.py tests/test_cu_screenshot.py -q` | 0 | 34 passed (11+9+14) — Wächter `test_no_run_closes_a_window_by_title_or_process` grün, XWD-Decoder-Tests unverändert grün |
| `uv run pytest tests/test_cu_live_desktop.py -q` | 0 | 24 passed — Host-Session-Laufzeit, Live-Bereichsregeln unverändert |
| `uv run ruff check src tests` | 0 | „All checks passed!" (voller Satz, nicht nur eigene Dateien) |
| `uv run python -m compileall -q src` | 0 | Syntax-Gate sauber |
| `printf '/quit\n' \| uv run skaldir chat --no-probe` | 0 | Chat-Fallback intakt |
| `printf 'selftest --layer ACT\n/quit\n' \| uv run skaldir chat --format jsonl` | 0 | JSONL-Umschlag intakt (echtes Selbsttest-Ergebnis als JSON) |

Rohzeuge: `logs/cu-xlib-capture/tests-*.log`, `regressions*.log`, `ruff*.log`,
`cli-chat.log`, `cli-jsonl.log`.

## 3. Grenzen (ehrlich benannt)

- **Nicht angefasst:** `skaldir/workflows.py::screenshot` (Vision-/Verify-Fluss) ruft
  weiterhin das defekte `import` direkt auf — gleiche Host-Störung, anderes Executor-Niveau;
  der Fix dort (gleiche GetImage-Mechanik) ist ein eigener Schritt und nicht Teil dieses
  Features.
- Ein X-Server-GetImage-Capture beweist, dass das Fenster gemappt ist und was es zeichnet;
  auf Wayland bleibt der komposite Gesamtschirm (anderer Fensterinhalt um das eigene
  Fenster) damit **nicht** messbar — dafür gilt weiter: Portal-Zustimmung ist die Nutzerin
  zu erteilen, nie zu umgehen.
