# CLI-Envelope-Evidenz: Char-Split-Defekt (VAL-UI-003)

Datum: 2026-10-06/07 · Feature: `cli-evidence-envelope-integrity` · Commit: `ba5a489`

## Befund (Messung 2026-10-06, vor dem Fix)

31 Befehle, die über `surface_cli.contract_entries()` registriert werden
(`tui.*`, `stop.*`, `browser.*`, `desk.*`, `schedule.*`, `sources.*`, `voice.rebrief`),
übergaben in `surface_cli.entry()` den Wert `COMMAND_EVIDENCE[path]` — eine **nackte
Zeichenkette** (`COMMAND_EVIDENCE: dict[str, str]`) — als `CommandSpec.evidence`
(deklariert als `tuple[dict, ...]`). `run_command()` (cli_contract.py:2087) macht daraus
`list(spec.evidence)`, wodurch die `--json`-Envelope-Evidenz **zeichenweise** zerfiel
(`["s", "r", "c", "/", ...]`); `skaldir contract dump --json` → `default_evidence` ebenso.

Statischer Audit über `cli_contract.COMMANDS` (nach `cli.build_parser()`):
309 Einträge → **31 fehlerhaft / 278 korrekt** (Elemente sind `{ref, kind, note}`-Dictionaries).

Live-Belege (Repository-Einstieg `uv run skaldir`, Rohdateien unter
`logs/cli-evidence-envelope/before/`): `browser tabs`, `desk status`, `schedule list`,
`sources report` lieferten `evidence` als Zeichenliste (z. B. 47 Zeichen-Einträge),
`contract dump` 31 betroffene `default_evidence`.

## Fix (Commit `ba5a489`)

- **Wurzel:** `surface_cli.COMMAND_EVIDENCE` ist jetzt `dict[str, tuple[dict, ...]]` in
  `CommandSpec`-Form (wie `act_cli`/`host_cli`); `entry()` und alle ~30
  `report_envelope`-Aufrufstellen übergeben die Dictionaries direkt (Notizen erhalten).
- **Defence in depth:** `run_command()` verweigert einen Spec-Eintrag mit nackter
  Zeichenkette **vor dem Handlerlauf** (Exit 7, klare interne Fehlermeldung);
  `build_envelope()` wirft einen `ValueError` (mit Hinweis auf den Char-Split) bei
  `evidence`-Argument vom Typ `str`.
- **Invariantentest bleibt in der Suite:** `tests/test_cli_contract_dump.py`
  (`test_every_command_declares_evidence_as_ref_kind_note_objects`,
  `test_contract_dump_reports_object_evidence_for_every_command`) — fehlgeschlagen-zuerst
  gegen exakt die 31 `surface_cli`-Befehle; Schutzwerte in
  `tests/test_cli_contract_envelope.py` (String-Evidenz verweigert Handler vorab;
  `build_envelope` verweigert `str`).
- **Kein Verhaltenswandel darüber hinaus:** `data`, `warnings`, `next_actions`,
  `exit_code`, `ok` sind vor/nach identisch (Vergleich der Roh-Envelopes unter
  `logs/cli-evidence-envelope/{before,after}/`).

## Beleg nach dem Fix (Repository-Einstieg `uv run skaldir`, 2026-10-07)

| Befehl | rc | `evidence[0]` |
| --- | --- | --- |
| `browser discover --json` | 0 | `{'ref': 'src/skaldir/browser_layer.py:SessionManager.discover', 'kind': 'code', 'note': ''}` |
| `browser tabs --json` | 0 | `{'ref': 'src/skaldir/browser_layer.py:BrowserLayer.tabs', 'kind': 'code', 'note': ''}` |
| `desk status --json` | 0 | `{'ref': 'src/skaldir/desktop_lease.py:DesktopLeaseStore', 'kind': 'code', 'note': ''}` |
| `schedule list --json` | 0 | `{'ref': 'src/skaldir/scheduler.py:ScheduleStore.all', 'kind': 'code', 'note': ''}` |
| `sources report --tool research --json` | 0 | `{'ref': 'src/skaldir/data_sources.py:SourceRegistry.report', 'kind': 'code', 'note': ''}` |
| `contract dump --json` | 0 | 309 Befehle, `default_evidence` überall Objektlisten (0 fehlerhaft) |

Rohdateien: `logs/cli-evidence-envelope/after/`.

## Launcher

- **Zuerst der Repo-Einstieg belegt:** die Vorher-/Nachher-Messungen oben liefen über
  `uv run skaldir` im Repo (`/home/willy/Agentic_Projects/Skaldir_Agent`, Commit `ba5a489`).
- **Backup:** Snapshot `~/.local/share/uv/tools/skaldir-agent/` (Stand vor der Neuinstallation)
  gesichert nach `logs/launcher-backups/skaldir-agent-snapshot-20261007-123322/` (gitignored).
- **Neuinstallation:** `uv tool install --force /home/willy/Agentic_Projects/Skaldir_Agent`
  → Exit 0, `skaldir 1.5.0` (Exit 0).
- **Nachweis durch den installierten Einstieg** (`skaldir`, 2026-10-07): `desk status`,
  `schedule list`, `browser tabs`, `browser discover`, `sources report --tool research` und
  `contract dump --json` → alle rc 0, `evidence[0]` überall ein `{ref, kind, note}`-Objekt;
  `contract dump`: 309 Befehle, 0 fehlerhafte `default_evidence`. Rohdateien unter
  `logs/cli-evidence-envelope/after-launcher/`.

## Suite-Gate (2026-10-07)

`uv run python -m pytest tests/ -q` (Kommando `test` aus `services.yaml`), Exit 0:
8412 bestanden, 33 übersprungen. Vier echte-PTY-Abbruchtests
(`test_chatbar.py::test_a_real_pty_shows_the_two_bars_and_survives_a_ctrl_c`,
`test_cli_feel.py::test_real_pty_session_at_80x24`,
`test_cli_feel.py::test_real_pty_cancel_keeps_the_session_and_the_raw_capture_is_readable`,
`test_repl_shell.py::test_real_sigint_cancels_the_running_command`) fielen im
belasteten Volllauf zeitkritisch aus und sind einzeln erneut gelaufen → **4/4 bestanden**
(Exit 0) — die unter Mission-AGENTS.md gemessenen Spurious-PTY-Fehler unter Last, kein
zusammenhang mit diesem Fix. Vollprotokoll: `logs/cli-evidence-envelope/full-suite.log`.
`uv run ruff check src tests` → Exit 0; `uv run python -m compileall -q src` → Exit 0.

