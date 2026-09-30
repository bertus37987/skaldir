# Suite green follow-up — what was fixed and what still blocks a fully green full run

Revision under test: working tree on top of `7ad2d66`.
Host note: 12-core Fedora, but this session runs other projects (desktop-mcp, VS Code).
Raw logs stay under ignored `logs/`; this is the sanitized summary.

## (a) `uv run pytest tests/ -q` collection — FIXED
- Root cause (measured): `[tool.pytest.ini_options] pythonpath = ["src", "integrations/hermes"]`
  lacked the repo root. The console-script `pytest` (rootdir = cwd = repo) resolved `tests.<mod>`
  differently from `python -m pytest`, so `from tests.test_...` / `from tools.gv_...` raised
  `ModuleNotFoundError: 'tests' / 'tools'` (5 collection errors, exit 2).
- Fix: `pythonpath = [".", "src", "integrations/hermes"]`.
- Verified: `uv run pytest tests/ --collect-only -q` → exit 0 (was exit 2); both invocation forms
  now collect and run.

## (b) env-leak / rename guards — FIXED, guards UNCHANGED
- Root cause: `src/skaldir/act_service.py` `_default_desktop_store` read
  `os.environ.get("SKALDIR_HOME")`/`("GODSV_HOME")` directly; `src/skaldir/llm_provider.py`
  `_announce` read `os.environ.get("SKALDIR_QUIET")` directly. The guard scanners
  (`test_env_leak_guard.py`, `test_rename_compat.py`) forbid direct reads of the runtime spellings.
- Fix: route the reads through `skaldir.env` (`env.getenv`, legacy-alias fallback preserved).
  Guards were NOT changed.
- Verified: both guard files → exit 0 (23 passed).

## (c) subagent import-order test — GREEN in a clean full run (was a contention artifact)
- Measured failure seen earlier: `test_no_import_happens_while_the_children_run` reported
  `seen = ['', 'registry', 'perf_runtime']`.
- Root cause: an artifact of **two full-suite pytest runs competing on one host** (measured in this
  session). In a single clean `uv run python -m pytest tests/ -q` run the test PASSES. The main+
  worker path of a `StubProvider` run imports nothing (verified by reading `spawn`/`run`/`_worker`
  /`_child_loop`/`_finish`/`_emit`). No production change was needed; instrumentation added for
  diagnosis was reverted (`tests/test_subagents.py` is byte-identical to HEAD).
- Verified: clean full run — `test_subagents.py` all green.

## (d) browser real read — FIXED (deterministic, no weakening)
- Root cause: the test read `https://example.com` and asserted the body contains `"Example Domain"`;
  example.com now only carries that string in `<title>`, not the body, so the real read could not
  match (env-dependent external content).
- Fix (no skip, no weakened assertion): the test now reads a **self-owned deterministic page**
  (an ephemeral `127.0.0.1` `ThreadingHTTPServer` fixture serving a strong marker) through the
  real `agent-browser` binary against a real URL, and asserts the marker, that the JSON envelope
  was unwrapped (`not content.lstrip().startswith("{")`), and the URL/source/latency fields.
- Verified: `tests/test_browser_backend.py` → exit 0 (10 passed).

## (e) scheduler catch-up race — FIXED at the measured root
- Root cause (measured at `src/skaldir/scheduler.py` `ScheduleStore.due()`): claimed occurrences were
  filtered **before** the `CatchUp.ONCE` selection, so a concurrent worker saw the newest term as
  claimed and fired the next older missed term instead.
- Fix: for `CatchUp.ONCE`, select the newest missed occurrence from the **full** list, then let the
  atomic `claim()` (flock) be the sole dedup guard (`if newest.key in claimed: continue`).
  `SKIP`/`ALL` behaviour unchanged.
- Verified: `test_concurrent_workers_race_and_only_one_wins` green **12 consecutive runs**;
  both scheduler files → exit 0 (32 passed).

## (f) CI — FIXED
- `.github/workflows/ci.yml` ran the broken bare `pytest`; now runs `python -m pytest tests/ -q`
  (same working form as `services.yaml`).

## STILL BLOCKING a fully-green full run (NOT part of a–f; environmental/timing)

**RESOLVED** — see `docs/audit/PTY-TESTS-LOAD-ROBUST.md` (feature `pty-tests-load-robust`). The four
failures below were reproduced under synthetic CPU load and fixed at their measured root: the frame's
`tty.setcbreak` default (`TCSAFLUSH`) was discarding already-typed input, the SIGINT was sent to a
child still importing, and the prompt mark was matching the echo of the test's own keystrokes. The
wait is now readiness-polled. A clean full run is green (8222 passed, 0 failed) and the four are
green in 13 consecutive targeted runs under load 18–21 on this 12-core host. The original findings
are kept below as the record they were.

On THIS host the full suite reported exactly 4 failures, all real-terminal PTY/real-subprocess tests
that pass at idle and fail only under the full suite's own CPU load (proven by a synthetic CPU-load
reproduction, no earlier tests involved; therefore NOT related to a–f):

- `tests/test_chatbar.py::test_a_real_pty_shows_the_two_bars_and_survives_a_ctrl_c`
- `tests/test_cli_feel.py::test_real_pty_session_at_80x24`
- `tests/test_cli_feel.py::test_real_pty_cancel_keeps_the_session_and_the_raw_capture_is_readable`
- `tests/test_repl_shell.py::test_real_sigint_cancels_the_running_command`

They are timing-sensitive around `skaldir scan -` (a stdin-blocking command) + Ctrl-C/SIGINT and the
child's foreground-process-group timing. `pty_session`'s `until()` already polls for readiness and
its docstring names the under-load hazard; `test_repl_shell` still uses a fixed `time.sleep(1.0)`
before SIGINT (no readiness wait, and `communicate()` blocks). Every one of these asserts real
terminal behaviour (unchanged output strings), so the fix is to make the waits load-robust
(poll for readiness, same idiom `pty_session.until()` already uses), which is tui-worker territory.
No test was weakened and none was modified here.

## Verification commands (this session)
- `uv run python -m pytest tests/ -q` → clean full run: **4 failed (the PTY set above), 0 error,
  everything else passed** (collection worked; env/rename guards, scheduler incl. race, browser,
  subagents incl. import-order all green).
- Targeted re-confirmation after the instrumentation revert:
  `uv run python -m pytest tests/test_env_leak_guard.py tests/test_rename_compat.py
  tests/test_scheduler_worker.py tests/test_browser_backend.py tests/test_subagents.py -q`
  → exit 0 (82 passed).
- Both invocation forms collect identically after (a) (`--collect-only` exit 0 for the console-script
  form; full run for the `-m pytest` form). The test set is identical, so the console-script form
  shows the same 4 load-flaky PTY failures on this host only.
