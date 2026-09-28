# Eingabe-Probe: Grenzen statt Behauptung (Feature `input-probe-rewrite`)

Sanitized evidence for the bounded input probe (architecture §2, milestones
`m1-eingabe`, assertions **VAL-INPUT-001 / VAL-INPUT-002 / VAL-INPUT-005**). Private
planning/audit document, therefore German; no raw transcripts, no secrets.

## What was wrong (all measured before the change)

| # | Defect | Measured symptom |
|---|--------|------------------|
| 1 | The probe sent keystrokes into **whatever window happened to have focus** — no window of its own, no recorded pid/wid. | rc 0 while the *user's* focused window was the recipient |
| 2 | There was **no event receiver** at all: the probe read the tail of a log file and accepted **any** change in it as a delivery. | "the log grew" was reported as a delivered keystroke |
| 3 | `marker` was accepted as a parameter and **never evaluated**. | the correlation that would have proven delivery was dead code |

Two further defects followed from them: `ydotool key 38:1 38:0` (38 is X keycode 'a', not evdev
`KEY_A = 30`), and a docstring reference to a `xlib_zaehler.py` that does not exist in the repo.
Both are gone: the probe now sends the marker as **text**, and the log-tail-diff logic is deleted
with no replacement that reads "the log changed".

## What the probe is now

`src/skaldir/input_backend.py` (rewritten) + `src/skaldir/probe_target.py` (new). One bounded
probe, in this order:

1. **Voraussetzung** — no usable backend ⇒ `BACKEND_MISSING` (with the reason: not installed, or a
   socket that refuses connections, which is `ydotool`'s measured state on this host).
2. **Zustimmung** — the official portal/EIS path exists on this host but needs the user's dialog
   ⇒ `CONSENT_MISSING`. No session is created, no dialog is answered.
3. **Eigenes Fenster** — the probe spawns `python -m skaldir.probe_target`, which builds its own
   X window titled `SKALDIR-PROBE-<nonce>` and re-publishes it in its own `_NET_WM_PID`. The
   binding is only accepted after six checks: on the display, title is ours, `windowactivate
   --sync` rc 0, `WM_STATE` Normal, `IS_VIEWABLE`, `_NET_WM_PID` == the recorded probe pid, and
   the **server's** input focus == that wid. Anything less ⇒ `PROBE_MISCONFIGURED` and **nothing
   is sent**.
4. **Eingangssperre** — the DESK-02 fence runs before the send; a stale fence or a stop aborts
   the probe (`PROBE_MISCONFIGURED`, unknown, never a backend defect).
5. **Senden** — the marker goes out as text. `xdotool` focuses the probe's own window in the same
   argv; `ydotool` (kernel level, no window concept) gets the window focused immediately before.
   A marker must never land in a window that belongs to the user.
6. **Empfänger** — the probe's own receiver writes `(wid, t, keysym, char)` JSON lines; the probe
   polls them and a stop request in the same tick. An optional evdev observer counts kernel
   events and is reported **separately** — it never enters a decision.
7. **Korrelation** — a delivery counts only when the record matches **marker ∧ probe wid ∧
   [t_send, t_send + timeout]**. Everything else is not a delivery.

Six states, and their meanings in prose: `BACKEND_MISSING` (nothing to send with),
`CONSENT_MISSING` (the user's to give), `PROBE_MISCONFIGURED` (unknown, not a backend defect),
`INPUT_SENT` (rc 0 — dispatch, **not** delivery), `INPUT_OBSERVED_AT_TARGET` (the probe's own
window reported the marker), `APP_EFFECT_VERIFIED` (only via `promote_to_effect_verified`, with
evidence; a probe never claims it). UNKNOWN never counts as success.

Cleanup signals only the recorded pid (after re-reading `/proc/<pid>/cmdline` for the receiver's
module + run id) and removes only the probe's own temp dir. No `windowclose`, no `pkill`, no
kill by title — a window that survives is *reported*, not closed.

## Verification actually run

Command shape (all commands from the repo root, `.venv` interpreter, `PYTHONPATH=src`):

```
env -u HERMES_HOME -u GODSV_HOME -u SKALDIR_HOME -u GODSV_LLM_API_KEY -u GODSV_LLM_BASE_URL \
  PYTHONPATH=$PWD/src TMPDIR=/tmp timeout 900 .venv/bin/python -m pytest <files> \
  -o addopts="" -p no:cacheprovider -p no:randomly -q
```

| Run | Result |
|-----|--------|
| `tests/test_input_backend.py tests/test_live_keyboard_backend_gate.py tests/test_live_desktop_launch_env.py tests/test_input_probe_bounded.py tests/test_input_probe_owned_display.py` | **108 passed** |
| Host-Session-Umfeld (13 Dateien: die fünf Eingabedateien, `test_act_service.py`, `test_capability_flow.py`, `test_dependency_declarations.py`, `test_cu_live_desktop.py` u. a.) | **254 passed, 1 skipped, 2 vorher rote** |
| `tests/test_cu_live_desktop.py` | **2 failed, 39 passed** — **vorher schon rot**, siehe unten |
| `uv run ruff check` on the changed source/test files | clean, except `cu_host_session.py:57 I001` — **pre-existing** (identical finding on `HEAD`, from the uncommitted takeover import block) |

`tests/test_input_probe_owned_display.py` proves the whole chain on a **private Xvfb + openbox**
that the fixture starts and stops itself (`:91`–`:130`, never the user's session): the probe
window is confirmed bound, the marker is sent, the receiver reports it, and the result is
`INPUT_OBSERVED_AT_TARGET`. **This is an owned-display result, not a host-live one.**

Der produktive Weg (nicht nur Unit-Tests) wurde ebenfalls gefahren — auf einem **privaten**
Display, nie auf dem der Nutzerin: `HostSessionRunner.act()` mit gebundener DESK-02-Sperre,
eigenem Zielfenster, `window/focus` gefolgt von `key Return`. Der Empfänger des Zielfensters
protokollierte `KEY keycode=36 keysym=65293` (press + release), `act()` meldete exit 0, und
`input_probe_report()` nannte `INPUT_OBSERVED_AT_TARGET` für xdotool.

The unit evidence for the assertion trio: no marker → no hit, foreign wid → no hit,
marker + wid + time window → hit, plus a marker outside the window, a stale earlier run, a
release-only record, and a marker that is split by a shift press.

## What the proof caught in the implementation itself

1. **The send did not carry the probe's display.** `probe()` ran `xdotool …` without `env`, so
   it used `$DISPLAY` of the caller's shell — which for a runner that measures the owner's
   session points at the *host* desktop. Measured on the productive proof:
   `X_SetInputFocus BadWindow`. Had that window id existed there, the marker would have been
   typed on the user's desktop, which is precisely what this probe must never do. The display
   now travels with the send command (`send_env`), and a test pins it (`DISPLAY=:0` in the
   environment, probe on `:92` ⇒ the send sees `:92`).
2. **The same bug class in `confirm_binding`** (its questions about the window went to the
   wrong display): fixed by routing every check through the probe's own display.
3. The full-desktop keyboard path bypassed the gate entirely — closed with `_input_argv_for`,
   one argv builder behind both paths.

## Still open (deliberately, not forgotten)

- **VAL-INPUT-007** — delivery proof on the *real* host desktop. The measured blocker stays:
  `xdotool`'s five delivery shapes return rc 0 while an XWayland window reports no key event,
  and the ydotoold socket `/tmp/skaldir-ydotoold/ydotoold.sock` refuses connections
  ⇒ `BACKEND_MISSING`. The live acceptance belongs to the `live-acceptance-worker` feature.
- **VAL-INPUT-003** — keycode translation (X → evdev) is a separate feature; this probe
  deliberately sends text so the marker is a random token that no keymap has to translate.
- **VAL-INPUT-004** — the verdict lives per runner instance and is re-measured per new
  runner. A cross-run cache with invalidation is the `input-backend-cache` feature; the test
  `test_ein_vorrat_wird_nicht_erfunden` records that no answer may be carried over as if it
  had been measured here.
- **Pre-existing, not caused by this feature (measured on `HEAD~1` too):**
  `tests/test_cu_live_desktop.py::test_a_live_action_may_not_carry_its_own_argv` and
  `::test_the_live_pointer_path_clicks_inside_the_target_and_refuses_outside` fail with
  "input refused at the adapter: … no DESK-02 fence is bound". Both build a `live-desktop`
  runner and call `act()` without binding the fence that commit `20426dd` made mandatory.
  Verified identical before and after the change in this document.
