# Computer Use: realer GUI-End-to-End-Nachweis (Linux, isoliertes Image)

## Was vorher fehlte

`godsvoice cu check` meldete `container GUI (godsv-cu-x11): nicht verfügbar (Image fehlt:
localhost/godsv-cu-x11:latest)`. Es gab **kein** Build-Rezept im Repo. Damit war jeder GUI-Schritt
unmoeglich: `observe()` konnte nichts wahrnehmen, GUIStates trugen nur deklarierte Daten, und die
§56-Pflichtfaelle blieben `not_run`.

## Was jetzt existiert

* `docker/cu-x11.Containerfile` — Alpine + Xvfb, xdotool, xmessage, xterm, **xwd**, Fonts; der
  Build verifiziert die fuenf Binaries im selben RUN und **bricht ab**, wenn eine fehlt.
* `tools/cu_gui_e2e_probe.py` — der reale Pfad, ausgefuehrt und belegt.

```bash
podman build -t localhost/godsv-cu-x11:latest -f docker/cu-x11.Containerfile docker/   # rc=0
podman run --rm --network none localhost/godsv-cu-x11:latest sh -c 'for b in Xvfb xdotool xmessage xterm xwd; do command -v $b; done'
# /usr/bin/Xvfb /usr/bin/xdotool /usr/bin/xmessage /usr/bin/xterm /usr/bin/xwd
.venv/bin/python -m godsv cu check      # container GUI (godsv-cu-x11): verfuegbar
.venv/bin/python tools/cu_gui_e2e_probe.py   # 18/18 checks held, rc=0
```

## Die Kette, die die Probe wirklich faehrt

`ContainerRunner(gui=True)` -> Container + `Xvfb :90 800x600x24` -> **echte App** (xterm, das seinen
stdin in eine Datei schreibt) -> `observe()` liest den Fenstertitel **vom Display** -> GUIState mit
Kanal `window_metadata` und Herkunft `perception` -> echter Screenshot (`xwd -root`, md5) ->
`windowfocus` + `type_text` + `key Return` **ueber die GUI-Kinds des Runners** -> `cat /tmp/typed.txt`
enthaelt genau `godsv-e2e-ok` (**Wirkung im App-eigenen Zustand**) -> Screenshot danach hat anderen
md5 -> `ctrl+d` -> `observe()` leer + `pgrep` rc=1 (**App wirklich weg**).

Beobachtete Rohwerte (Auszug): Fenstertitel `'GODSV-CU-PROBE'`; Screenshot
`534422216b07ff65097e8c1a361db413` -> `d8220584cf40eec4444660ae4ad74faa`; Readback `'godsv-e2e-ok'`.

## Gegenfaelle (alle gefahren)

| Gegenfall | Beobachtetes Resultat |
| --- | --- |
| Aktion auf toten Fenster-Anker | `xdotool` rc=1, kein stiller No-Op |
| Blind-Replay des gespeicherten Makros | `BlindReplayRefused` (Startbedingung/Anker nicht beobachtet) |
| Positivkontrolle dazu | mit beobachteten Ankern erlaubt (`replay_allowed=True`) — der Waechter ist nicht vakuum-gruen |
| `live_display=True` auf Kanal `window_metadata` | Vertragsfehler: „is not a live GUI channel" |
| Nicht wahrgenommenes Feld | `value=None`, `perceived=False`, in `unperceived` gelistet |
| State zertifiziert Wirkung | `certifies_effect=False`, `ground_truth='app_effect'` |
| Unbekannte App-Version | `applicable=False`; unterstuetzte Version `applicable=True` |

## Fallen (echte, reproduzierte Fehlschlaege — nicht Theorie)

1. **`|| true` nach `apk add` baut ein leeres Image als „Erfolg".** Der erste Rebuild lief mit
   `REBUILD_RC=0` durch, enthielt aber weder `xdotool` noch `xmessage`; das Symptom war nur ein
   „(keine Fenster)" in der Probe. Der Install bricht jetzt ohne `|| true` ab und prueft die
   Binaries selbst.
2. **`packagesize`-Falle: `x11-apps` existiert in Alpine nicht**, `xwd` schon (`xwd-1.0.9-r2`) —
   der fehlgeschlagene Install wurde von `|| true` verschluckt (siehe 1).
3. **`podman exec` killt die Prozessgruppe beim Ende des Execs.** `act(cli, ["sh","-c","app &"])`
   hinterlaesst deshalb **kein** Fenster; eine langlebige App braucht `podman exec -d`. Das ist eine
   echte Grenze des `act()`-Pfads und steht als Kommentar in der Probe.
4. **Ohne Fenstermanager gibt es keine Aktivierung:** `xdotool windowactivate` endet rc=1, und ein
   synthetisches Tastenereignis per `--window` erreicht Xt-Apps nicht. Wirkung braucht XTEST
   (`windowfocus` + `key`/`type` **ohne** `--window`).
5. **Das PTY ist kanonisch gepuffert:** getippter Text erreicht die App erst mit `Return`. Ein
   Readback direkt nach dem Tippen zeigt eine leere Datei — das ist kein Fehlschlag der App.

## Was hier NICHT belegt ist

Kein Windows-/macOS-Desktop, keine echte Host-Anwendung (die App laeuft im isolierten Xvfb, nie auf
Wilhelms Display), kein `screenshot`-Kanal mit Bildverstehen (der `xwd`-Kanal ist real, aber es gibt
kein OCR/Vision), und keine Jev-live-Bewertung dieser GUIStates (kein TypeSafe-Zugang). Die
§56-Pflichtfaelle sind damit **anfahrbar**, aber noch nicht alle gefahren.
