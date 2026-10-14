# Abnahme VAL-TOOL-003: Multi-Tool Composite Workflow (Mixed Tools)

- **Feature:** `workflow-mixed-tools` (VAL-TOOL-003)
- **Status:** PASS
- **Datum:** 2026-10-14
- **Host / Environment:** Fedora Linux, Docker daemon active, hermes runtime at `~/.hermes/godsv`

## 1. Übersicht

Nachweis, dass Skaldir über die `compose`-Engine heterogene Tool-Arten (`docker` und `tool` / in-process Python registry tool) in einer Kette ausführt, Ein- und Ausgaben bindet und Endzustände verifiziert.

## 2. Definierte Composite Spec

Datei: `~/.hermes/godsv/composites/mixed_docker_tool.json`
- **Schritt 1 (docker):** Image `docker.io/library/alpine:3.20`, führt Shell-Kommando aus, liefert `stdout` und `exit_code`.
- **Schritt 2 (tool):** Lokales Registry-Tool `list_dir`, liefert Verzeichnisinhalt (`output`).
- **End State:** `$steps.s1.exit_code == 0 and $steps.s2.output.count > 0`

## 3. Ausführung und Ergebnis

Befehl:
```bash
uv run skaldir compose run --name mixed_docker_tool --json
```

- **Exit Code:** `0`
- **Status:** `ok`
- **Verifizierter Endzustand:** `$steps.s1.exit_code == 0 and $steps.s2.output.count > 0` held after 2 step(s)
- **Step s1:** Docker run `alpine:3.20` -> Exit 0, stdout: `"hello from docker container"` (duration: 389ms)
- **Step s2:** Registry tool `list_dir` via `mcpserver._wrapper_for` -> Exit 0, 34 entries listed (duration: 2ms)

## 4. Evidenzen

- Log: `logs/compose-live/20261014-215931.json`
- Composite Definition: `~/.hermes/godsv/composites/mixed_docker_tool.json` (sha256: `56925d9b6f2f9d758efb25f7dd01230e143bec3f2c9f672fd3277532202a6a72`)
