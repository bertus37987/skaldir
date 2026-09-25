# Skaldir CLI — Runbook

**Audience:** whoever operates this machine (the owner, or an agent working on his behalf).
**Companion:** `docs/QUICKSTART.md` (install + provider). This file is the *what is broken, what do
I run, what does the output mean* document.

**Measured on:** 2026-09-18 18:35–18:41 CEST, `/home/willy/projects/gods-voice`, `godsvoice 1.5.0`,
runtime `GODSV_HOME=/home/willy/.hermes/godsv`, Docker 29.6.1 (works without sudo). Every blocked
reason, exit code and raw block below was copied from a run on that tree. Anything that is *not* in
`godsvoice contract dump --json` is marked **PLANNED**.

Every command in this file is documented in the contract dump (`28 root`, `77 leaf`, `unregistered:
[]`), except where explicitly marked PLANNED. There are **33 PLANNED leaf commands** (see §8).

---

## 1. Two questions before you run anything

### 1.1 Which source tree is `godsvoice` executing?

The venv uses an editable install, and a parallel block can re-point it. Measured during this run:
between 18:35 and 18:39 `.venv/bin/godsvoice` imported `/home/willy/gv-mcp-wiring/src/godsv/` and
`doctor` died with `FileNotFoundError: prompts directory not found: /home/willy/gv-mcp-wiring/prompts`.

```bash
cd ~/projects/gods-voice
.venv/bin/python -c "import godsv; print(godsv.__file__)"
# expect: /home/willy/projects/gods-voice/src/godsv/__init__.py
```

Force the repo tree when it disagrees (do **not** re-run `pip install -e` while other work is live —
that rewrites the same shared `.pth`):

```bash
PYTHONPATH=/home/willy/projects/gods-voice/src .venv/bin/godsvoice <command>
```

### 1.2 Which runtime am I measuring?

Resolution order, one rule project-wide: `$GODSV_HOME` → `$HERMES_HOME/godsv` → `~/.hermes/godsv`.

```bash
export GODSV_HOME=/home/willy/.hermes/godsv
godsvoice registry status          # prints "runtime:", "tools db:", the selection reason
```

A stray `GODSV_HOME`/`HERMES_HOME` from a test or a wave makes the CLI report databases that do not
exist. Measured: with `$GODSV_HOME` unset and a leaked `$HERMES_HOME`, the same binary reported

```
runtime: /home/willy/gv-tmp-verify/tmp.d9LZM1lpu6/home/godsv ok: False exit: 2
  SUPERVISE blocked :: no events db at /home/willy/gv-tmp-verify/tmp.d9LZM1lpu6/home/godsv/events.db — no trajectory to supervise no pause store at …/pauses.db — no session has been paused yet
  PERSIST   blocked :: no events db at …/events.db no memory db at …/memory.db no run store at …/runs.db
```

SUPERVISE goes from `reachable` to `blocked` with invented missing-DB reasons. **Always state which
runtime you measured.** Prefer `--runtime`/`$GODSV_HOME` over ambient environment.

---

## 2. Layer by layer: what to run, what the output means

`godsvoice selftest --json` is the single source of truth. Drill into one layer with
`godsvoice selftest --layer <LAYER> --json` (same exit code, only the one layer in `data.layers`) —
that is exactly what its own `next_actions` suggests.

Exit code of `selftest`: **0** all eight reachable · **2** at least one not reachable · **7** internal.

| Layer | Reachable today | CLI surface to use | If it is not reachable |
|---|---|---|---|
| **EXPAND** | ✅ | `registry status`, `registry migrate`, `tools-hub`, `tools list/show/scan/candidates/build/builds/mine/request/fulfill/approve/promote/promote-global/propose/revoke/compose`, `tools adapter show`, `tools register`, `tools health`, `tools rollback` | the tool store or the registry cannot be read — see §5/§6 |
| **ACT** | ❌ blocked | `cu check`, `cu demo` | a live desktop is not wired; `cu demo` is an **in-process fixture** (`data.scope: "in-process fixture"`), never a real action |
| **COMPOSE** | ❌ blocked | `tools compose` (lists candidates only), `skills …` | no composite can be created/validated/run |
| **ROUTE** | ⚠️ exit 3 | `jev kinds/packs/selftest/decide/history` | see §2.1 — the CLI edge is live even while the runtime path is not |
| **SUPERVISE** | ✅ | (no dedicated command; probes `voice.py`, `guard.py`, `pauses.db`) | events/pause store unreadable |
| **ENFORCE** | ✅ | `completion begin/status/history/override` | the goal store is unreadable |
| **VERIFY** | ❌ blocked | `eval` (in-process only) | no command checks the real workspace — `eval` can never certify a goal |
| **PERSIST** | ❌ blocked | `memory-stats/search/audit/retrieval-check`, `metrics`, `nightshift` | no run store; see §4 |

### 2.1 The five real `blocked_reason` strings

Copied field by field from `selftest --json` (`layer`, `status`, `blocked_reason`). The strings are
verbatim; only the one-line layout is a reading aid.

```
ACT      blocked    no live desktop bridge is wired to the CLI — no command drives a real window/click/keystroke yet (only in-process runners are constructible)
COMPOSE  blocked    no composite can be created, validated or run from the CLI yet — `tools compose` only lists candidates
ROUTE    reachable  probe failed
VERIFY   blocked    no verification command runs a check against the real workspace yet — `eval` is in-process only and cannot certify a goal
PERSIST  blocked    no run store at /home/willy/.hermes/godsv/runs.db — no run has ever been started (no long-run record exists)
```

`blocked_reason` is `null` for EXPAND, SUPERVISE and ENFORCE (reachable).

**`ROUTE` deserves its own paragraph.** It reports `status: "reachable"` **with** `ok: false` and
`blocked_reason: "probe failed"` (exit 3) — the layer's own probes both pass, but the aggregate is
not a pass. The two probes:

```
system_one_flags  reachable  flags loaded: provider=llm_system_one_adapter shadow=False   (/home/willy/.hermes/godsv/settings.yaml)
advise            reachable  advise() returned choice=? provider_mode=disabled            (src/godsv/system_one_service.py:165)
```

`choice=?` with `provider_mode=disabled` means the **runtime path** (settings → `service.advise()`)
did not produce an answer, while the **CLI's explicit edge** does work: the same tree returned a real
live advisory in 1.25 s with `godsvoice jev decide … --provider llm_system_one_adapter`. Do not read
`ROUTE blocked` as "no provider works" — check §3.4 of the QUICKSTART first. Enabling the runtime
path is a settings/provider-wiring job (`~/.hermes/godsv/settings.yaml`, `system_one.*` flags), not a
CLI bug.

### 2.2 First move per blocked layer (the work list, not a wish list)

| Layer | First move (measured starting point) |
|---|---|
| ACT | wire `act check/run` + `act subtool …` (**PLANNED**) to the existing Computer Use module; the runner isolation levels are already reachable (`{'dry_run': True, 'sandbox_bwrap': True, 'container_podman': True}`) |
| COMPOSE | wire `compose create/validate/run` (**PLANNED**); `compose_candidates` already returns a real ranked list |
| ROUTE | make the runtime path answer: provider flags in `settings.yaml` + the provider adapter on the service path, then re-run `selftest --layer ROUTE --json` |
| VERIFY | wire `verify plan/run/status/evidence` (**PLANNED**); `plan_verification('the test suite passes') -> pytest` already plans correctly, only the real-world runner is missing |
| PERSIST | create the run store (`runs.db`) — see §4; until then `runs status/start/resume/checkpoint` and `goal show/criteria` stay PLANNED |

---

## 3. Exit codes and the envelope

Every command with `--json` prints **exactly one** JSON envelope on stdout; the human text moves to
stderr. The vocabulary (one meaning per code, one `error.code` per code):

| Code | Name | `error.code` | Meaning |
|---|---|---|---|
| `0` | OK | `ok` | done, evidence attached |
| `1` | USAGE | `usage` | bad flag/name/command line — nothing happened |
| `2` | VERIFICATION_FAILED | `verification_failed` | a check ran and failed |
| `3` | BLOCKED | `blocked` | the runtime cannot do it as asked (not wired / disabled / unreachable) |
| `4` | NEEDS_CREDENTIALS | `needs_credentials` | a live endpoint refused the credential or none was found |
| `5` | NOT_FOUND | `not_found` | no such tool/goal/run/prompt |
| `6` | CONFLICT | `conflict` | already exists (e.g. the tool already is a capability) |
| `7` | INTERNAL | `internal` | our own failure — the one to file |
| `8` | UNKNOWN | `unknown` | timeout/unknown (declared by no command in this inventory) |

Two rules worth remembering: a success **without evidence** is downgraded to exit `7`
(`build_envelope`); and `registry.status` / `tools.promote-global` are the only commands that
additionally mirror their payload as JSON on stderr (measure it, don't be surprised).

Useful reading trick for long envelopes:

```bash
godsvoice selftest --json | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["exit_code"]); [print(l["layer"], l["status"], "::", l["blocked_reason"]) for l in d["data"]["layers"] if not l["ok"]]'
```

---

## 4. Resume a goal after a crash

**What exists today (WIRED):** the goal record is durable and survives a process death. Measured in a
throwaway runtime (`GODSV_HOME=/home/willy/gv-docs-scratch/rt-resume`) — first process:

```
$ godsvoice completion begin --goal "the quickstart example goal" --criterion "docs/RUNBOOK.md exists" --json
EXIT=3
{"ok": false, "exit_code": 3, "data": {"goal": "the quickstart example goal", "state": "WORKING",
 "revision": 1, "criteria": [{"id": "c1", "text": "docs/RUNBOOK.md exists", "blocking": true}],
 "loop_wired": false, …},
 "error": {"code": "blocked", "message": "the goal was recorded, but no execution loop is wired:
 nothing keeps working after this command returns",
 "hint": "`godsvoice completion status --json` shows the record; a run lifecycle is not wired to the CLI yet"}}
```

**Second process, same runtime** (this is the resume evidence):

```
$ godsvoice completion status --json
{"state": "WORKING", "terminal": false, "goal": "the quickstart example goal", "revision": 1,
 "verified": 1, "total": 1, "last_verdict": null,
 "criteria": [{"text": "docs/RUNBOOK.md exists", "blocking": true}],
 "runtime": "/home/willy/gv-docs-scratch/rt-resume"}
```

So the goal, its revision and its criteria are durable; **the loop is not**. `completion begin` exits
**3** on purpose: exit 0 would claim work that never started.

### 4.1 The `verified` field is not a verification claim — read `last_verdict`

Measured above: `verified: 1, total: 1` while `last_verdict: null` and no verification ever ran.
`src/godsv/cli.py:1465` computes `verified` as *"criteria whose text is not named in the last
verification's failure blob"* — with no verification row the blob is empty, so every criterion counts
as "verified". **Read `last_verdict`** (its `status`, `blocking_failures`, `major_failures`) to know
whether anything was actually checked; `verified/total` alone reads like a pass and is not one.

### 4.2 The procedure

```bash
export GODSV_HOME=/home/willy/.hermes/godsv
godsvoice completion status --json      # state, goal, revision, criteria, last_verdict
godsvoice completion history --json     # the timeline (use --limit N)
ls -la "$GODSV_HOME/completion/"        # the raw records: <session>.json + <session>.jsonl
```

- **Goal still `WORKING` with criteria and a revision** → the record survived; re-issue the work and
  let it try again. Nothing needs to be re-created.
- **`state` in a terminal state** → `terminal: true`; a new `completion begin` starts a new record.
- **`goal: null`** → no goal was ever recorded in *this* runtime. Check §1.2 before concluding
  anything (a stray `GODSV_HOME` is the usual cause).
- **`last_verdict` is non-null** → a verification ran. Its `status` is the explicit verdict from
  `src/godsv/completion.py:207` — `passed` | `failed` | `unknown`, never "probably fine". Only
  `passed` (with an empty `blocking_failures`) means the goal is certified; `null`, `failed` or
  `unknown` means it is not done, no matter what a model claims.
- `completion override` exists and exits 0/1; use it only deliberately — it is the escape hatch, not
  the path.

### 4.3 What resume will look like when the long-run store lands (**PLANNED**)

`src/godsv/runs_cli.py` implements the R0–R3 resume order and the durable record. Not reachable today
(`runs` is not a root command):

| PLANNED command | purpose |
|---|---|
| `runs status` | read-only snapshot of the durable store |
| `runs start` | start a durable run |
| `runs resume` | R0–R3: take ownership and classify in-flight work |
| `runs checkpoint` | write the self-sufficient snapshot |
| `goal show` | the durable goal record |
| `goal criteria` | the criteria table (optionally verify one) |

Until then, "resume" means: read `completion status`, read the session trajectory, re-issue the work.
There is **no `runs.db`** in the real runtime (measured: `no run store at
/home/willy/.hermes/godsv/runs.db — no run has ever been started`).

---

## 5. Roll back a capability

EXPAND is the one layer with a complete lifecycle today.

```bash
godsvoice tools list --json                                   # what is registered ("no tool specs" = nothing)
godsvoice tools show <name> --json                            # one spec
godsvoice tools adapter show <name> --json                    # the typed adapter + its evidence
godsvoice tools health <name> [--probe] --json                # ledger state + live container facts
godsvoice tools rollback <name> [--reason TEXT] --json        # the rollback
```

`tools rollback` is **label-scoped**, not name-scoped: it removes containers carrying
`godsv.capability=<name>`, restores the previous spec from the newest rollback snapshot, or marks the
current spec `revoked` when there is no snapshot, and quarantines the adapter files. Measured on a
name that does not exist (nothing was touched, exit **5**):

```
$ godsvoice tools rollback nosuchtool --json
EXIT=5
{"ok": false, "exit_code": 5, "data": {"name": null, "rolled_back": false,
 "reason": "no staged container adapter for 'nosuchtool'", "quarantine": null,
 "containers_removed": null, "teardown_scope": null, "teardown_ok": null, "teardown_detail": null,
 "health": null, "removed_registration": null, "restored_spec": null, "failure_class": "not_found"},
 "error": {"code": "not_found", "message": "no staged container adapter for 'nosuchtool'"}}
```

Read the rollback result field by field — `rolled_back`, `teardown_ok`, `teardown_scope`,
`containers_removed`, `restored_spec`, `quarantine`, `failure_class`. A rollback that reports
`rolled_back: true` with `teardown_ok: false` removed registry/files but did **not** clean containers.

Registering is the other half, and it is evidence-gated: `tools register` exits **2**
(`verification_failed`) when the adapter test is missing, failed, or no longer covers the adapter
bytes — a capability is never registered on a green story. Full pipeline:

```
tools mine --image <img> --capability "<what it must do>"     # search → inspect → generate adapter → TEST it
tools adapter show <name>                                     # look at the adapter, its permissions, its evidence
tools register <name> [--version X.Y.Z]                       # only with accepted evidence
tools health <name> --probe                                   # real call + live container facts
tools rollback <name>                                         # take it back
```

Measured note: `tools mine --name demo.sha` is recorded as **`demo_sha`**. `_slug()`
(`src/godsv/toolbuilder.py:282`) lowercases the name and turns every run of characters outside
`[a-z0-9]` into `_`, capped at 40 characters — pass the slug to every later verb.

---

## 6. Tear down containers by label

**Golden rule (measured bug, still the trap):** `docker --filter label=a=1,b=2` is **not** ANDed by
Docker 29.6.1 — it matches nothing and still exits 0, so a label-scoped teardown silently removes
nothing while reporting success. **One `--filter` per label.** The runtime assembles this in one
helper (`godsv.docker_runtime.label_filter_args`) and never by joining with commas.

Reproduced on the live daemon (2026-09-18, Docker 29.6.1) with a container of my own, non-destructive:

```bash
docker run -d --label godsv.owner=skaldir --label godsv.capability=docsdemo \
  --name gv-docs-demo alpine:3.20 sleep 300

# correct: one --filter per label  → the container is found
$ docker ps -a --filter label=godsv.owner=skaldir --filter label=godsv.capability=docsdemo --format '{{.ID}} {{.Names}} {{.Labels}}'
b5f92d80fcc4 gv-docs-demo godsv.capability=docsdemo,godsv.owner=skaldir

# wrong: the comma form       → EMPTY list, exit 0  (the silent no-op)
$ docker ps -a --filter label=godsv.owner=skaldir,godsv.capability=docsdemo --format '{{.ID}} {{.Names}}'
(no output)   exit 0

# teardown by OUR labels only
$ docker rm -f $(docker ps -aq --filter label=godsv.owner=skaldir --filter label=godsv.capability=docsdemo)
b5f92d80fcc4
# after: 0 matching containers — foreign `penpot-local` containers: 7 before, 7 after
```

**Rules of engagement on this daemon:**

1. The daemon is **shared** with foreign stacks (7 `penpot-local` containers were live during this
   run). Never `docker system prune`, never `docker rm` by name, never a bare `docker ps -aq | xargs`.
2. Scope teardown with the ownership labels: `godsv.owner=skaldir` **and** the narrowing label
   (`godsv.capability=<name>` / `godsv.build_id=<id>`). The runtime refuses a teardown without its
   labels (`policy: "label-scope-required"` — *"teardown without our labels — refusing to remove a
   foreign container"*).
3. **Prefer the product path** (WIRED): `godsvoice tools rollback <name>` performs exactly this
   scoped teardown and reports `teardown_scope` + `containers_removed`. The raw `docker …` commands
   above are **docker**, not `godsvoice` — they exist in no contract dump and are listed here only as
   the manual fallback.
4. Confirmed by hand: a real leftovers sweep on 2026-09-18 found one labelled container from a spike
   (`godsv.owner=skaldir, godsv.capability=spike, godsv.purpose=test`). The listing works; the
   removal is a decision, not an automatic step — check `docker inspect` before removing someone
   else's spike.

---

## 7. Troubleshooting table (the known traps)

| Symptom | Cause (measured) | Do this |
|---|---|---|
| `OSError(122, 'Disk quota exceeded')`, `INTERNALERROR`, a storm of `E`/`F` in pytest; a suite that "regressed" with no code change | `/tmp` is a **16 GB tmpfs with a per-user block quota** (`quota -u willy`: `quota 12774566` ≈ 12.2 GiB). Parallel pytest runs write their scratch there; `df -h /tmp` showed 3.5 G used / 12 G free at documentation time. **Signature taken from the recorded incidents (skill + `MVP/12`), not re-triggered in this pass** — filling the quota on purpose would hurt sibling work | `TMPDIR=/home/willy/gv-tmp-verify .venv/bin/python -m pytest tests -q -o addopts=""` — verified: `11 passed in 0.75s` on `tests/test_cli_contract_dump.py`. Clean scratch afterwards; keep test trees small |
| Selftest reports missing DBs that obviously exist (`no events db …`, `no memory db …`, `no run store …`) | a sandboxed `GODSV_HOME` (or a leaked `HERMES_HOME`) — the run measured a throwaway runtime. **Measured**: `runtime: /home/willy/gv-tmp-verify/tmp.d9LZM1lpu6/home/godsv`, SUPERVISE flipped from reachable to blocked with two invented reasons | measure with `GODSV_HOME=/home/willy/.hermes/godsv`, say which runtime you used, then re-run `selftest --json` |
| `doctor` fails on "Plugin deployed" + "Runtime" (exit 2) or dies with `prompts directory not found` | a stray `HERMES_HOME` (or an editable install pointing at another checkout). **Measured**: `✗ Plugin deployed — /home/willy/gv-tmp-verify/tmp.wYuiopQEMH/home/plugins/godsv`, `✗ Runtime — …/home/godsv`, exit 2 — and with a clean env the same command printed `Deployment OK.` and exited **0** | `env -u HERMES_HOME -u TMPDIR GODSV_HOME=/home/willy/.hermes/godsv godsvoice doctor` |
| `tools mine`/`tools register`/`tools health` report `failure_class: "docker_unreachable"`, or `tools mine` says the image is not local | the Docker daemon is down or `DOCKER_HOST` is wrong. **Measured** with a bogus socket: `available -> (False, 'failed to connect to the docker API at unix://…/no-such.sock; check if the path is correct and if the daemon is running')` and `failure_class: 'docker_unreachable'`, `filter_args: ['--filter', 'label=godsv.owner=skaldir']`; with the daemon up `available -> (True, 'Server Version 29.6.1 (x86_64)')` | `docker info` first. It works **without sudo** on this machine (user in the `docker` group). Do not add `sudo`; if the daemon is really down, that is an owner action. Note `tools mine` also refuses a bare tag **by design** — `image 'alpine:3.20' is not local and no pinned digest is known — run with --pull (or pin a digest)` (exit 3, `failure_class: image_missing`) |
| `jev decide --provider llm_system_one_adapter` exits **4** | the endpoint refused the credential, or a remote `api_key_env` is unset. **Measured** (key unset): `refusal_reason: "provider refused the credential (HTTP 401, …) — set GODSV_LLM_API_KEY in the environ"`, `error.code: needs_credentials`; with a wrong key the message still names the variable and redacts the key | `export GODSV_LLM_API_KEY=…` (or the variable named in the refusal) and re-run. Exit 4 is not a bug and not retryable — fix the credential |
| exit **3** with `refusal_reason: "provider_disabled"` | you are on the default (`--provider disabled`), or the runtime path is off. **Measured**: `status: "blocked"`, `fallback: "keep the deterministic retrieval order and the existing token budget"` | pass `--provider llm_system_one_adapter` explicitly, or fix the runtime path (§2.1) |
| an unknown command exits **1** with a JSON `usage` envelope | argparse rejected the command line before any work happened. **Measured**: `godsvoice provider list --json` → `invalid choice: 'provider' (choose from jev, init, …)`, exit 1, evidence ref `src/godsv/cli_contract.py` | check the name against `godsvoice contract list` / `contract dump --json`. `provider`, `verify`, `runs`, `goal`, `compose`, `act`, `image`, `serve`, `daemon` are **PLANNED** — see §8 |
| `doctor`/`selftest`/`jev` behaviour differs between runs with no code change | the shared editable install follows another block's scratch tree | §1.1 — print `godsv.__file__` before trusting a result |
| a green-looking `completion status` (`verified: 1/1`) with nothing verified | `verified` counts criteria *not named as failed*; with no verification row everything counts | read `last_verdict`, not the counter — §4.1 |

---

## 8. Documented vs. planned commands (the honest inventory)

Measured 2026-09-18 18:41, `godsvoice contract dump --json` on the repo tree:

| | Count |
|---|---|
| root commands | 28 |
| **documented** leaf commands (in the dump, `unregistered: []`) | **77** |
| **PLANNED** leaf commands (module + `register_cli` exist, not wired into `cli.py`) | **33** |
| documented ∩ planned | 0 |

The 33 PLANNED commands, by group (each measured by calling the module's `register_cli()` against a
fresh parser, so the names are what the wiring will actually add):

| Group | Count | Commands |
|---|---|---|
| `provider` | 7 | `list`, `add`, `test`, `remove`, `default`, `tiers`, `doctor` |
| `act` | 6 | `check`, `run`, `subtool save`, `subtool list`, `subtool show`, `subtool run` |
| `runs` | 4 | `status`, `start`, `resume`, `checkpoint` |
| `goal` | 2 | `show`, `criteria` |
| `verify` | 4 | `plan`, `run`, `status`, `evidence` |
| `compose` | 5 | `candidates`, `create`, `validate`, `run`, `show` |
| `image` | 2 | `inspect`, `layers` |
| `serve` / `daemon` | 3 | `serve`, `daemon serve`, `daemon status` |

None of these is accepted by the CLI today; `godsvoice <planned-root> …` exits **1** with a `usage`
envelope naming the commands that *do* exist.

`docs/CLI-REFERENCE.md` was generated at 2026-09-18T15:42Z with **73** leaves and is therefore stale
by the four `tools.*` commands added by the container-toolblock
(`tools.adapter.show`, `tools.register`, `tools.rollback`, `tools.health`). Regenerate it (do not
hand-edit):

```bash
$ .venv/bin/python tools/gv_cli_reference.py --check
STALE: /home/willy/projects/gods-voice/docs/CLI-REFERENCE.md documents fingerprint 7d6fc01cf19e…, the CLI contract now hashes to ae547e740252… (run the generator again)
CHECK_EXIT=1
# docs/CLI-REFERENCE.md sha256 unchanged by the check (bb5972a4114a85e4cd4489349b639c7779a1b5f3b0b50684e3dd241f63ddba12)
```

Regenerate it (do not hand-edit):

```bash
.venv/bin/python tools/gv_cli_reference.py --out docs/CLI-REFERENCE.md
```

---

## 9. How a claim in this file can be re-checked

| Claim | Re-run |
|---|---|
| the eight layers and their blocked reasons | `GODSV_HOME=/home/willy/.hermes/godsv godsvoice selftest --json` (exit 2 today) |
| one layer only | `godsvoice selftest --layer PERSIST --json` |
| the install is healthy | `env -u HERMES_HOME -u TMPDIR GODSV_HOME=/home/willy/.hermes/godsv godsvoice doctor` (exit 0) |
| the command inventory | `godsvoice contract dump --json` → `data.root_commands` / `data.leaf_commands` / `data.unregistered` |
| a live provider call | `godsvoice jev decide --kind memory_injection --request REQUEST.json --provider llm_system_one_adapter --dry-run --json` |
| the label AND semantics | §6 block, verbatim on this daemon |
| the goal record survives a restart | §4 block, in a throwaway `GODSV_HOME` |
| the `/tmp` quota | `quota -u willy`, `df -h /tmp` |

Observed while writing this file (not a defect of the CLI, reported for the record):
`godsvoice demo` prints `God's Voice — Demo (8 Events, 10 Signale)` — a German word and the old
product name in a product-path string (`src/godsv/demo.py`). It is the English/naming sweep's item,
not this document's.

---

## 10. Appendix A — the complete `selftest` block (verbatim)

`GODSV_HOME=/home/willy/.hermes/godsv godsvoice selftest`, 2026-09-18 18:39, exit **2**. This is the
baseline every other reading in this file is compared against:

```
✓ EXPAND
    ✓ registry.resolve: runtime=/home/willy/.hermes/godsv tools_db=/home/willy/.hermes/godsv/tools.db
    ✓ capability.load_tool_entries: 0 generated tool(s) with manifest facts
    ✓ capability_health.all_states: 0 tool(s) with a health state
    ✓ tools_db: tools db present
✗ ACT — no live desktop bridge is wired to the CLI — no command drives a real window/click/keystroke yet (only in-process runners are constructible)
    ✓ runner_availability: isolation levels: {'dry_run': True, 'sandbox_bwrap': True, 'container_podman': True}
    ✓ executor_in_process: in-process executor verdict: verified
    ✗ live_desktop: no live desktop bridge is wired to the CLI — no command drives a real window/click/keystroke yet (only in-process runners are constructible)
✗ COMPOSE — no composite can be created, validated or run from the CLI yet — `tools compose` only lists candidates
    ✓ compose_candidates: compose_candidates returned 0 ranked candidate(s)
    ✗ compose_lifecycle: no composite can be created, validated or run from the CLI yet — `tools compose` only lists candidates
✗ ROUTE — probe failed
    ✓ system_one_flags: flags loaded: provider=llm_system_one_adapter shadow=False
    ✓ advise: advise() returned choice=? provider_mode=disabled
✓ SUPERVISE
    ✓ read_trajectory_health: 7 session(s); newest=20260917_175818_61e746 window_steps=8
    ✓ guard: guard wired (evaluate_completion + EvidenceIndex importable)
    ✓ pause_store: pause store readable: 61 lease(s) {'abandoned': 4, 'delivered': 56, 'expired': 1}, strictness=standard
✓ ENFORCE
    ✓ completion_store: state=WORKING goal=(none) criteria=0 requests=0
    ✓ terminal_states: 6 terminal states known
✗ VERIFY — no verification command runs a check against the real workspace yet — `eval` is in-process only and cannot certify a goal
    ✓ plan_verification: plan_verification('the test suite passes') -> pytest
    ✓ gate_runner: gate runner present (execute_check + run_requirement_checks importable)
    ✗ real_world_runner: no verification command runs a check against the real workspace yet — `eval` is in-process only and cannot certify a goal
✗ PERSIST — no run store at /home/willy/.hermes/godsv/runs.db — no run has ever been started (no long-run record exists)
    ✓ events: 3179 event(s) in 7 session(s)
    ✓ memory: 0 incident(s), 0 note(s)
    ✗ run_store: no run store at /home/willy/.hermes/godsv/runs.db — no run has ever been started (no long-run record exists)
8 layer(s), 22 probe(s), 5 not reachable · 51ms
```

`next_actions` in `--json` mode lists one drill-down per unreachable layer, e.g.
`{"command": "godsvoice selftest --layer PERSIST --json", "reason": "PERSIST is not reachable — see its blocked_reason"}`.
