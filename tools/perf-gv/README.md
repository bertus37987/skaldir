# Perf-Harness — God's-Voice-Kopie (`tools/perf-gv`)

Kopie des Hermes-Desktop-Perf-Harness (`~/.hermes/hermes-agent/apps/desktop/scripts/perf/`,
Stand 2026-09-13, byte-identisch bis auf die unten gelisteten Änderungen) plus die
GV-Erweiterungen **P1–P3**. Der Stock-Harness gehört zum geteilten Hermes-Repo und
wird dort **nicht** angefasst — diese Kopie lebt im gods-voice-Repo und überlebt
jeden `godsvoice desktop build-app`-Rsync (der nur den Mirror
`$HERMES_HOME/godsv/app-src` überschreibt).

```bash
node tools/perf-gv/run.mjs --help
```

## Was anders ist als im Stock (P1–P3 + GV-Anpassungen)

| Datei | Änderung |
|---|---|
| `run.mjs` | **`--help`** (Usage, Exit 0, ohne Build/Spawn) · **P2** `--match <substr>` und `--prod` **ohne** `--spawn` (Attach auf eine laufende Probe-Instanz; baut nichts) · **P3** `--baseline <pfad>` (Default: dieses Verzeichnis) · `PERF_APP_BINARY` setzt den Renderer-Build beim Spawn aus |
| `lib/launch.mjs` | **P1** `PERF_APP_BINARY` → spawn statt `electron .`; `resolveAppCommand()`/`appOverride()`; `childEnv()` entfernt ambientes `HERMES_DESKTOP_USER_DATA_DIR`/`HERMES_DESKTOP_BOOT_FAKE`/`HERMES_DESKTOP_DEV_SERVER`; App-Baum + Vite/Electron-Auflösung über `lib/paths.mjs` |
| `lib/paths.mjs` | **neu**: `DESKTOP_DIR` = `$PERF_DESKTOP_DIR` ?? `$HERMES_HOME/godsv/app-src/apps/desktop` (Stock: „drei Ebenen hoch“ — existiert im GV-Repo nicht) |
| `scenarios/right-pane.mjs` | Default-`cwd` = `DESKTOP_DIR` statt Repo-Wurzel |
| `serve.mjs` | verweigert `PERF_APP_BINARY` (serve ist der Dev-Instanz-Pfad; Exit 2 mit Hinweis) |
| `lib/cdp.mjs`, `lib/stats.mjs`, `lib/baseline.mjs`, `scenarios/*` (übrige) | unverändert übernommen |
| `baseline.json` | **Seed** = Stock-Baseline (darwin-arm64, Median aus 5, `--spawn --prod`). Vor dem ersten Gate auf Fedora einmal `--update-baseline` fahren — das ersetzt die Metriken pro Szenario, die Toleranzblöcke bleiben erhalten |
| `gateway_attach_bench.py`, `image-attach-bench.mjs` | **nicht** kopiert (eigenständige Einzel-Benchmarks, nicht Teil von `run.mjs`) |

## P1 — die gepackte App messen

```bash
PERF_APP_BINARY=$HOME/.hermes/godsv/app/linux-unpacked/gods-voice \
  node tools/perf-gv/run.mjs cold-start --spawn --prod --runs 5 --port 9400
```

- Ohne `PERF_APP_BINARY` bleibt es beim Stock-Verhalten (`electron .` aus dem Baum).
- Mit Override werden `vite build`/`bundle-electron-main` **übersprungen** — das
  Binary bringt seinen Renderer selbst mit. `--prod` heißt hier: kein Dev-Server
  (`HERMES_DESKTOP_DEV_SERVER` wird aus der Kind-Env entfernt), Dist laden.
- Voraussetzung: das Binary ist ein **Probe-Build**
  (`GODSV_PERF_PROBE=1 .venv/bin/godsvoice desktop build-app` bzw. `--perf-probe`),
  sonst fehlt `window.__PERF_DRIVE__` und der Spawn läuft in den Timeout.

## P2 — attach auf eine laufende (gepackte) Instanz

```bash
# ohne Probe bricht requireDriver ab; Probe-Instanz z.B. so starten:
$HOME/.hermes/godsv/app/linux-unpacked/gods-voice \
  --user-data-dir=/tmp/gv-perf-ud --remote-debugging-port=9400 &

node tools/perf-gv/run.mjs stream keystroke transcript --prod --port 9400
node tools/perf-gv/run.mjs stream keystroke --prod --port 9400 --match file://
```

- `--prod` ohne `--spawn` baut nichts und matcht nicht auf den Dev-Port;
  `--match <substr>` wählt ein Page-Target per URL-Substring (nur Attach-Modus).
- Die attachbare Instanz muss ein **Probe**-Renderer sein (`__PERF_DRIVE__`).

## P3 — eigene Baseline

```bash
node tools/perf-gv/run.mjs cold-start --spawn --prod --runs 5 --port 9400 \
  --update-baseline --baseline tools/perf-gv/baseline.json
```

Default ist bereits `tools/perf-gv/baseline.json` (Repos-seitig, überlebt den
Mirror-Rsync). `--baseline` erlaubt einen anderen Ort, z.B.
`docs/perf/baseline-linux-x64.json` (Vorschlag aus dem Plan, nicht entschieden).

## Env-Fallen

- **`HERMES_DESKTOP_USER_DATA_DIR` nicht exportieren.** Sie überschreibt in
  `electron/main.ts` `app.setPath('userData')` *vor* `--user-data-dir` → die
  isolierte Instanz landet auf dem Profil der laufenden GV-App und reicht wegen
  des Single-Instance-Locks nur weiter (Harness-Timeout). Die Kopie **entfernt**
  die Variable aus der Kind-Env und warnt; im Parent-Shell trotzdem weglassen.
- `HERMES_DESKTOP_BOOT_FAKE` würde den Kaltstart künstlich strecken — wird
  ebenfalls entfernt.
- `HERMES_HOME` darf ambient gesetzt bleiben (das Kind bekommt ein Temp-Home).
- `HERMES_DESKTOP_CDP_PORT` wird pro Lauf explizit für das Kind gesetzt, damit
  `--port` gewinnt.
- Messläufe nur auf ruhiger Maschine; laufende GV-App kostet CPU/GPU.

## Provenienz / Sync

Stock unter `~/.hermes/hermes-agent/apps/desktop/scripts/perf/`. Beim Hermes-Update
einmal `diff -rq` gegen diese Kopie fahren und die Patches nachziehen:

```bash
diff -rq ~/.hermes/hermes-agent/apps/desktop/scripts/perf tools/perf-gv | grep -v "^Only in"
```
