# Harness: Chat-Standardtest live (VAL-HARNESS-001) + drei begrenzte Fixes

Feature `harness-chat-standardtest`, 2026-10-04. Rohbelege (gitignored):
`logs/harness-chat-standardtest/`, Lauf-Transkript
`logs/harness-chat-standardtest/run-transcript.jsonl` (Kopie von
`~/.hermes/godsv/agent/runs/run-20261004T131456Z-5ce410.jsonl`), Sitzungssnapshot
`logs/harness-chat-standardtest/session-final-snapshot.txt`.

## 1. Fix 1 — Chat-Eintrag bindet die Run-Runtime (F03-Klasse)

**Defekt (gemessen, Stand 6c309ad-Vorfahr):** `tool_set_for` nahm `runtime`/`project_id`
entgegen, rief aber `core_capability_tools(workspace, exclude)` **ohne** sie auf — der
Chat-/Agent-Run-Eintrag (`skaldir agent run` → `agent_loop_cli.py`) fiel still auf
`default_runtime_dir` zurück, während `turn_tools` und `build_agent_toolset` den
Run-State-Dir explizit banden.

**Fix:** `src/skaldir/agent_loop.py` `tool_set_for` reicht
`runtime=runtime, run_id=str(project_id or "")` an `core_capability_tools` durch.

**Test (vorher rot, nachher grün):**
`tests/test_tool_search_integration.py::test_tool_set_for_threads_the_run_binding_into_the_shared_chain`
— baut `tool_set_for(kind="builtin", runtime=..., project_id="chat-run-1")` und ruft das
`run_code`-Werkzeug echt auf; das Ergebnis trägt `run_id == "chat-run-1"`.

## 2. Fix 2 — Produkt-CLI schreibt nichts für tools-core-Verben

**Defekt (reproduziert):** `uv run skaldir --json tools-core list` → **0 Byte, Exit 0**;
auch `skaldir tools-core list` (menschlich) → 0 Byte. Diagnose: Pythons Subparser parsen
in einen frischen Namespace und kopieren ihre eigenen Flag-Defaults über die
Root-geparsten Werte — ein VOR dem Subcommand stehendes `--json` (der Contract sagt:
jedes Kommando nimmt `--json`) ging auf dem Weg nach unten verloren
(`json_requested(args)` → False), der Verb lief im Textmodus und druckte nichts, weil
`_human` menschlichen Text nur bei explizitem `--format text` erlaubte. `_report` setzte
die Daten zwar in den Envelope (`_CONTRACT.set_data`), aber `run_command` entschied anhand
des verlorenen Flags, dass kein Envelope auszugeben sei.

**Fixe:**
- `src/skaldir/cli.py` `main()`: der Dispatch-Pfad prüft zusätzlich das argv mit dem
  vorhandenen `_json_requested_in_argv` (gleiche Regel wie der Parse-Fehler-Pfad); ein
  explizites `--format text` gewinnt weiter.
- `src/skaldir/tools_core/cli.py` `_human`: menschlicher Text ist jetzt der Default, wenn
  der Aufrufer kein JSON verlangt; unter `--json` bleibt stdout genau ein JSON-Dokument
  (Menschzeilen sind Diagnostik auf stderr).

**Beleg (echte Befehle, Repo-Venv):**

| Befehl | Exit | Ergebnis |
|---|---|---|
| `uv run skaldir --json tools-core list` | 0 | 7130 Byte getypter Envelope, `data.count == 9`, `exit_code 0` (`logs/harness-chat-standardtest/tools-core-list.json`) |
| `uv run skaldir tools-core list` | 0 | 9 menschliche Zeilen `[registered] terminal …` (`tools-core-list-human.txt`) |
| `uv run skaldir --json doctor` | 0 | 1246 Byte Envelope, `command == "doctor"` |

## 3. Fix 3 — doppelte Wire-Namen in `build_agent_toolset(kind='both')` (F03-Klasse)

**Defekt (gemessen, Stand 9362249):** eine Runtime mit registriertem Core-Toolset machte
`build_agent_toolset(kind='both')` doppelte Wire-Namen angeboten bekommen
(`read_file_2`, `terminal_2`, `patch_2`, `web_search_2`, `search_files_2`, `list_dir_2`,
`web_fetch_2`, `write_file_2`, `docker.ensure_2`), weil `registry_tools` ungefiltert
angehängt wurde und `ToolSet.of` die Kollision umbenannte statt sie zu lassen; die
Registry-Kopie war zudem nicht die an den Run-Kontext gebundene.

**Fixe:** in `agent_toolset.build_agent_toolset` und `agent_loop.tool_set_for` wird die
Registry-Variante gegen die bereits gebundenen Namen dedupliziert — das gebundene
(`source="core"`, Run-Kontext) Werkzeug gewinnt, echt neue Registry-Namen gehen durch.

**Tests (vorher rot — die Rot-Messung druckte exakt die obige `_2`-Liste —, nachher grün):**
- `test_a_registered_core_toolset_yields_no_duplicate_wire_names` (registrierte Runtime +
  Core-Toolset; behauptet zusätzlich `source == "core"` für das überlebende `terminal`)
- `test_the_loop_surface_dedupes_the_registry_variant_too`

## 4. Der Standardtest im echten Chat (VAL-HARNESS-001)

**Aufbau:** echte PTY-Sitzung (tuistory 0.11.0, 110×32), bare `skaldir` (Chat-Eintrag)
aus dem eigenen Arbeitsverzeichnis `/tmp/skaldir-chat-hello`, Repo-Venv
(`.venv/bin/skaldir`, entwicklungsnah), echter Provider (`llm`, glm-5.3-flash;
`skaldir provider test` → ok, 1875 ms, Exit 0 — Vorbedingung erfüllt).

**Ablauf (Sitzungssnapshot, ungekürzt in logs/):**

1. Prompt: „Create a Python file that prints Hello World, run it and show me the actual
   output."
2. Werkzeugaufruf `write_text` — `{"path": "hello.py", "text": "print(\"Hello World\")\n"}`
   → `ok`, Datei `created: true`, 21 Byte.
3. Werkzeugaufruf `terminal` — `{"command": "python3 hello.py"}` → `ok`, 19 ms.
4. Antwort zitiert die echte Ausgabe; Transkript trägt das Rohresultat:
   `output.stdout == "Hello World\n"`, `output.exit_code == 0`,
   `output.stderr == ""`, `output.cwd == "/tmp/skaldir-chat-hello"` (Run-Workspace,
   **nicht** das Prozess-cwd), `timed_out: false`. `run.finished status: "answered"`.

**Maschinelle Nachweise:**

| Prüfung | Befehl | Ergebnis |
|---|---|---|
| Datei existiert | `ls -la /tmp/skaldir-chat-hello/` | `hello.py`, 21 Byte |
| Inhalt | `cat /tmp/skaldir-chat-hello/hello.py` | `print("Hello World")` |
| Unabhängige Wiederholung | `python3 hello.py` | stdout `Hello World`, Exit 0 |
| Hash | `sha256sum hello.py` | `db63149b…70952` (`hello-py-sha256.txt`) |
| Echtes stdout im Transkript | `output.stdout` aus `run-transcript.jsonl`, seq 7 | `"Hello World\n"` |
| Repo-Root unberührt | `git status` | keine neue Datei im Repo-Root (der Write landete im Run-Workspace — F03-Gegenprobe) |

## 5. VAL-HARNESS-007 — Streaming-Nachweis pro Call in der echten Sitzung

Der `on_result`-Haken ist dispatcher-seitig belegt (`tests/test_harness_batching.py`,
Feature `harness-batching-streaming`). Die Chat-Sitzung zeigt die Auflösung je Call im
echten Eintrag: der Snapshot streamt `→ write_text … ← write_text ok · 0 ms` und
`→ terminal … ← terminal ok · 19 ms` als einzelne Abschlüsse im Lauf, und die Chatbar
stempelt je **abgeschlossenem** Call eine eigene Zeile (`[+] hello.py 0 ms`,
`[>_] terminal 19 ms`) aus den `tool_calls` des Lauf-Transkripts
(`chatbar._stamp_tool_rows`). Ehrliche Randnotiz: die Provider-Antwort von Schritt 2
trug keine Content-Deltas (`finish_reason: tool_calls`,
`streaming.reason: "the stream was requested but carried no content delta"`) — Token-Streaming
war an dieser Stelle vom Provider her nicht beobachtbar; die Call-Abschluss-Streams sind
es unabhängig davon.

## 6. Regressionen

- Rot-Grün-Nachweis: `git stash push` der drei Fix-Dateien → die drei neuen Toolset-Tests
  schlagen fehl (doppelte `_2`-Namen, fehlender `run_id`), `git stash pop` → grün.
- Gezielte Läufe nach allen Fixes (Exit 0, 0 F/E):
  `tests/test_tool_search_integration.py` (11), `tests/test_agent_loop.py` +
  `tests/test_agent_toolset.py` + `tests/test_toolset_equality.py` +
  `tests/test_run_code_argument_contract.py` (98), `tests/test_cli_*` (145),
  `tests/test_tools_core_*` (102), kombinierter Lauf über alle berührten Flächen (283).
- Gates: `uv run ruff check src tests` → „All checks passed!", Exit 0;
  `uv run python -m compileall -q src` → Exit 0.

## 7. Rest offen (ehrlich)

- Prüfung A (Matrix §5), GOAL-CORE-Szenario A und Brief-Bereich A bleiben
  `extern-blockiert` in ihrem **vollen** Umfang (Fixture-Repo-Aufgabe, CSV-Szenario):
  der Hello-World-Standardtest (VAL-HARNESS-001) ist jetzt live belegt, die übrigen
  Anteile dieser Zeilen stehen weiterhin aus. Die GAP-Zellen der Matrix verweisen auf
  diesen Nachweis.
- Der `--text`-Kurzflag existiert nicht (`--format text` ist die gültige Schreibweise);
  nicht Teil des Auftrags, unverändert.
