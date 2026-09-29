# Lint-Debt-Sweep — baseline and final measurement

Measured with `uv run ruff check src tests --statistics` in
`/home/willy/Agentic_Projects/Skaldir_Agent`.

## Baseline (commit `892b4cc`, 2026-09-28)

372 findings in 97 files (200 in `src/`, 172 in `tests/`).

```
104	E501 	[ ] line-too-long
 82	F401 	[*] unused-import
 60	UP017	[*] datetime-timezone-utc
 31	I001 	[*] unsorted-imports
 18	UP035	[*] deprecated-import
 14	F841 	[ ] unused-variable
 13	E702 	[ ] multiple-statements-on-one-line-semicolon
 13	UP042	[ ] replace-str-enum
  8	E741 	[ ] ambiguous-variable-name
  5	F811 	[-] redefined-while-unused
  3	B017 	[ ] assert-raises-exception
  3	E731 	[ ] lambda-assignment
  3	B007 	[ ] unused-loop-control-variable
  2	F541 	[*] f-string-missing-placeholders
  2	UP031	[ ] printf-string-formatting
  2	UP037	[*] quoted-annotation
  2	F821 	[ ] undefined-name
  2	UP012	[*] unnecessary-encode-utf8
  1	UP034	[*] extraneous-parentheses
  1	B023 	[ ] function-uses-loop-variable
  1	B904 	[ ] raise-without-from-inside-except
  1	F822 	[ ] undefined-export
  1	B905 	[ ] zip-without-explicit-strict
Found 372 errors.
```

Raw log: `logs/lint-baseline-statistics-892b4cc.txt`,
full finding list: `logs/lint-baseline-892b4cc.txt` (gitignored).

## Final (after the sweep)

```
$ uv run ruff check src tests
All checks passed!
```

`uv run ruff check src tests` exits 0 with no `noqa` added for a finding and no
file excluded from the configured rule set. The only `# noqa` in the touched set is
the pre-existing re-export facade in `src/skaldir/toolbuilder.py`, which carries a
stated reason and now guards exactly the one name that has a real consumer.

## How the classes were paid down

| Class | Count | How |
|---|---|---|
| UP017, UP035, UP037, UP012, UP034 | 85 | `ruff check --select <RULE> --fix`, diff reviewed per class |
| I001 | 31 | `ruff check --select I001 --fix` (two passes: a second one opened up after F401 removals) |
| F401 | 82 | `ruff check --fix`, then each survivor re-checked for an external consumer |
| UP042 | 13 | `enum.StrEnum` conversion after measuring the `str()` delta on this interpreter and scanning the whole repo for a stringification of each class |
| E702 / E741 / F841 / F811 / E731 / B007 / B023 / B904 / B905 / B017 / UP031 | 45 | individual edits, each site read before editing |
| F821 / F822 | 3 | two real latent bugs and one broken public API surface (see below) |
| E501 | 104 | line reflow; string-literal identity proven by an AST comparison against HEAD for every touched file |

## The three findings that were real bugs, not style

* `src/skaldir/browser_layer.py` — `Mapping` was used in `act()`'s annotation but
  never imported. `from __future__ import annotations` keeps annotations lazy, so
  this only fires when something resolves them (`typing.get_type_hints`,
  `inspect.signature`); the module now imports `Mapping` from `collections.abc`.
* `src/skaldir/surface_cli.py` — the unknown-schedule-action branch called
  `usage(...)`, a name the module does not define. Replaced with the module's own
  pattern, `dump({"error": f"unknown schedule action {action!r}"})` followed by
  `return EXIT_USAGE`.
* `src/skaldir/stop_signal.py` — `__all__` exported `"Backend"`, which the module
  never defines (the class is `Availability`). `from skaldir.stop_signal import *`
  would have raised `AttributeError`. `__all__` now exports `"Availability"` and
  `"AVAILABILITY"`; the whole list was verified to resolve.

## Full-suite stage-boundary proof

`uv run python -m pytest tests/ -q`, run against the final tree: **8254 collected,
4 FAILED, exit 1**. Raw log `logs/pytest-full-sweep.log`.

| Test | Status |
|---|---|
| `tests/test_env_leak_guard.py::test_no_module_reads_a_runtime_variable_directly` | FAILED — pre-existing |
| `tests/test_rename_compat.py::test_no_module_reads_the_two_prefixes_directly_anymore` | FAILED — pre-existing |
| `tests/test_browser_backend.py::test_a_real_read_returns_the_page_content_not_the_json_envelope` | FAILED — pre-existing, environment-dependent (real read against the real tool) |
| `tests/test_scheduler_worker.py::test_concurrent_workers_race_and_only_one_wins` | FAILED — pre-existing |

None of the four is caused by this sweep. The first three are the same ones
`docs/audit/MILESTONE-GATE-BLOCKER.md` recorded at `HEAD` and belong to the separate
`suite-green-followup` feature (the two guards demand that `SKALDIR_*`/`GODSV_*` reads go
through `skaldir.env`; `src/skaldir/act_service.py:1550-1551` and
`src/skaldir/llm_provider.py:1023` still read them directly — untouched here, because fixing
them is a behaviour change, not a lint change).

The fourth is **proven pre-existing by reproduction**: a clean `git worktree` at
`892b4cc` (unmodified sources) fails on the same assertion, `assert 2 == 1, "2 workers ran the
same term"` — two workers claim the same term because `ScheduleStore.due()` filters on read
rather than claiming atomically. The test file changed in this sweep (an E702 semicolon split
and an E501 reflow, both semantics-preserving), but the failure reproduces identically without
any of those edits.

The suite count is unchanged at 8254: the F811 duplicate removed from
`tests/test_provider_catalog_breadth.py` was a second definition of a name that already
shadowed the first, so pytest collected one test either way.

Additional non-suite checks run against the final tree:

* `uv run python -m compileall -q src tests` — clean.
* all 250 `skaldir` submodules import; `skaldir --help`, `skaldir schedule list --json`,
  `skaldir schedule define --json`, `skaldir desk acquire/release --json` and
  `skaldir tui preview` all produce their normal output (exit 0).
* every reflowed payload re-checked against the emitted bytes: the SCHED-04 note, the
  `schedule.define`/`desk.acquire`/`desk.release`/`tui.preview` evidence objects and the
  `evidence` bookkeeping are byte-identical to before the rewrap.
* `typing.get_type_hints` on 1291 callables in the 44 changed source modules — no
  unresolved annotation (`mcpserver` resolves tool annotations at runtime, so this is the
  path the old `Mapping` F821 would have fired on).
* a static pass over all 97 changed files confirms every loaded name resolves, so the
  removed imports hide no `NameError`.
* `uv run bandit -q -r src -ll` — exit 0, no high-severity findings (unchanged from before).

## Scope discipline

Exactly the 97 files that carried a baseline finding are changed, and every file that carried
a finding is changed — the two file sets are identical (verified by comparing the file list in
`logs/lint-baseline-892b4cc.txt` against `git diff --name-only -- src tests`). No other source or
test file was touched. `cu_runs.jsonl` and `events.db` were modified by earlier runs, not by this
sweep; they are left untouched. The only non-`src`/`tests` edits are this summary and the addendum
in `docs/audit/MILESTONE-GATE-BLOCKER.md`.
