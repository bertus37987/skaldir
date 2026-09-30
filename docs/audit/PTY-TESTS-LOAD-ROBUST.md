# PTY-Tests load-robust — was `SUITE-GREEN-FOLLOWUP.md` blocking, now fixed at the root

Feature: `pty-tests-load-robust` · milestone `m1-eingabe` · skill `tui-worker`.
Revision: working tree on top of `77aac5e`. Raw logs: `logs/pty-load-robust-run-*.log`,
`logs/pty-tests-load-robust-full-suite.log` (gitignored). This file is the sanitized summary.

## Outcome

The four real-terminal tests that `SUITE-GREEN-FOLLOWUP.md` left blocking are green: **13 consecutive
targeted runs under synthetic CPU load (load average 18–21 on a 12-core host) and one clean full
suite run with 0 failures** — the first fully green full run on this host. No test was skipped,
xfailed or weakened; every asserted output string is unchanged.

## The failures were reproduced first (real exit codes)

`uv run python -m pytest <the 4 node ids> -q` under 16 busy loops on 12 cores → **exit 1**:

```
.F.F
FAILED tests/test_cli_feel.py::test_real_pty_session_at_80x24
    assert "cancelled `skaldir scan -`" in text      → capture carried "^C input cleared"
FAILED tests/test_repl_shell.py::test_real_sigint_cancels_the_running_command
    BrokenPipeError: [Errno 32] Broken pipe
```

## Three measured root causes (not one)

Each was measured before being fixed. The first is a **product defect**, the other two are test
harness defects.

### 1. The frame deleted what the user had already typed (product, `src/skaldir/chatbar.py`)

`ChatBar.read_line` called `tty.setcbreak(fd)`, whose **default is `TCSAFLUSH`** — it discards the
terminal's *pending* input on every call. The frame paints the logo, the status band and four rows
and only then calls `select()`, so everything typed during that paint was already queued and got
deleted. Under load the paint window widens and the loss becomes total.

Instrumented key loop, loaded host, between `ChatBar.read_line ENTER` and the `^C`: **59 `select()`
calls returned "not ready" over 14.5 s and not one byte of `scan -\r` ever arrived**; the capture
showed `^C input cleared — nothing was running` and no `scan -` command was ever submitted.

Fix: `_cbreak_keep_input()` uses `TCSADRAIN`. `ISIG` stays **on**, so a real `^C` still arrives as
`SIGINT` and cancels the *running command* — what the frame stops losing is input, not a signal.
Regression test: `tests/test_chatbar.py::test_the_frame_does_not_flush_what_was_already_typed`
(fails on the old code with `assert ready, "the frame flushed a character that was already typed"`,
passes on the new — verified both ways).

### 2. The SIGINT was sent to a shell that was still starting (test, `tests/test_repl_shell.py`)

The fixed `time.sleep(1.0)` is a guess; on a loaded host the child interpreter is still importing at
1.0 s, so the SIGINT had no session to cancel and killed the process instead. Measured over 14 runs
of the test's exact sequence: **13 of 14 children died with `rc=-2`** and a `KeyboardInterrupt`
traceback out of `dataclasses.py` — the import chain was still running. One run survived and then
still failed, because the SIGINT landed in the shell's `prepare()` gates, so `scan -` never ran
(`misplaced_or_failed=1`).

Fix: readiness waits only — the plain status line, then the action line, then the cancel line, then
the history table. The SIGINT is still sent from the same place in the journey (after the command has
really started). A reader thread drains stdout, because an unread pipe is a second clock.

### 3. The prompt mark is not a readiness signal (test, `tests/test_cli_feel.py`)

`until(PROMPT_BYTES)` after a command is sent is satisfied by **the echo of the keystrokes the test
just wrote**: the mark is painted on the composer row while a line is being typed, and under load
the whole line echoes in pieces. Measured: the wait matched in **0.20 s, on `  ❯ s`** — the first
echoed character — while the command was still running and its result line arrived seconds later.

Fix: a step that means *"this command has finished"* waits for that command's own verdict
(`RESULT_LINE`, `⎿ exit N (…)`), the cancel step waits for the shell's own cancel line
(`CANCELLED_BYTES`), and the history step waits for the history table (`HISTORY_BYTES`). A step that
only means "the frame drew a row" keeps the prompt mark. The *bounds* were raised to cover a slow
**start** (measured: first prompt at 1.6 s idle, 13–40 s with the cores saturated) — a bound, never
a substitute for waiting: every wait still returns as soon as its condition holds.

## Verification (real exit codes)

| Command | Exit | Observation |
|---|---|---|
| `uv run python -m pytest <4 node ids> -q` under 16 busy loops | **1** | reproduction: 2/4 failed (before any change) |
| same, 13 consecutive runs, load 18–21 | **0** ×13 | 13/13 green, all 4 every time |
| `uv run python -m pytest tests/ -q` (clean host) | **0** | **8222 passed, 33 skipped, 0 failed, 0 errors** |
| `uv run python -m pytest <4 node ids> -v` | **0** | `4 passed` — none skipped |
| `uv run ruff check src tests` | 0 | clean |
| `uv run python -m compileall -q src` | 0 | clean |
| `printf '/quit\n' \| uv run skaldir chat --no-probe` | 0 | chat fallback intact |
| `printf 'selftest --layer ACT\n/quit\n' \| uv run skaldir chat --format jsonl` | 0 | non-interactive JSONL envelope intact |
| `uv run skaldir agent run "…PONG…" --json` | 0 | live non-interactive path, real answer `PONG` |

## Nothing was weakened

- **No** skip, xfail or `xfail`-marker added to any of the four tests; the only new
  `pytest.skip` is the pre-existing "no pty on this platform" guard on the new regression test.
- Every original assertion is byte-identical. In `test_real_sigint_cancels_the_running_command` the
  four product assertions (`child.returncode == 0`, `cancelled \`skaldir scan -\``,
  `the session is still here`, `cancelled` in the history) are unchanged; only readiness waits were
  added. The `⎿ exit 0 (OK)` and `⏺ skaldir selftest --layer ACT` assertions in `test_cli_feel` are
  unchanged.
- The two new markers make the waits *stricter* than the prompt mark they replace: the test now
  proves the command really finished, instead of accepting a prompt that could equally well belong
  to a command that never started.

## Honest limits

- The reproduction used **synthetic CPU load** (16 busy loops), because the full suite's own load is
  what the original report used. It reproduces the same failure at the same place.
- The load runs are the strongest available evidence on this host, not a proof of immunity: 13 runs
  at load 18–21 plus a green full suite, and a production fix that removes the mechanism by which the
  keystrokes were being lost at all.
