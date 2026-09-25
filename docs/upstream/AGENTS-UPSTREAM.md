# Skaldir — feature standard (read me first)

## Role

This file is the **standard** every wave is measured against and is injected into the agent's own project context.
Skaldir is a runtime that *finishes* work and *proves* it — not a chat wrapper. The skill `gods-voice` says *how* we
work (proofs, patch discipline, ownership); this file says *what* the product must be.
If a plan and this file disagree, this file wins; if the code and this file disagree, fix one of them in the same change.

## Rules

The eight core layers, in order — **EXPAND → ACT → COMPOSE → ROUTE → SUPERVISE → VERIFY → ENFORCE → PERSIST**:

1. **EXPAND — self-expanding tool system.** Build tools from Docker/CLI/HTTP/MCP capabilities:
   mining → adapter → test → registry → health → live call → rollback. The agent safely extends its own toolbox.
2. **ACT — computer use + GUI sub-tools.** Drive the real desktop and verify against the actual state. Repeated GUI
   sequences become reusable sub-tools (`open_browser`, `open_project`, …). Schema: **preconditions → UI anchors →
   actions → end-state check**.
3. **COMPOSE — learned composite tools.** Combine several tools into one higher capability; Docker tools, APIs, GUI
   sub-tools and normal tools cooperate. Validated workflows are reusable later like a single tool.
4. **ROUTE — always-armed Jev decision layer.** A fast System-One layer attached to runtime events, for tool/sub-tool
   routing · memory injection · voice triage · context compaction · capability selection.
5. **SUPERVISE — voice supervisor.** Watches for loops, goal drift, missed errors, false completion; intervenes
   **only** when the agent is not already handling the problem sensibly. Jev is the cheap triage gate.
6. **VERIFY — real-world verification.** Verify the outcome, not the code: build, inspect artifacts, run real user
   flows with computer use. Verdict is exactly `PASS` / `FAIL` / `UNKNOWN`, and **`UNKNOWN` never counts as success**.
7. **ENFORCE — goal-locked execution.** A goal may not end before verification returns `PASS`. On `FAIL`/`UNKNOWN`:
   keep working, new turn, refresh context, restart, or resume from a checkpoint. A **runtime invariant, not a prompt**.
8. **PERSIST — long-run agent + persistent learning.** Carry goals across long time, context limits, provider trouble
   and restarts: goal, criteria, evidence, checkpoints, budgets, progress; learn reusable memories, sub-tools, workflows.

The Hermes-parity core, always on top: **H1 skills · H2 tools · H3 short-term memory · H4 long-term memory ·
H5 session history · H6 fast subagents · H7 file editing · H8 programmatic tool calling / code execution.**

Working rules that are not negotiable: **English only** in anything the product shows or ships · **prove, don't
claim** (every step ends with a measured number; a screenshot or a summary alone is not proof) · **a feature counts as
done only when it is wired into the CLI, proven by raw output, and reachable in `skaldir selftest --json`** ·
reproduce a failure yourself before relaying it · never report a fake success (blocked ⇒ exit 3 with the reason).

The product is the **CLI and the API**, not a desktop app: the desktop surface is parked on the owner's instruction and
must not drive priorities. Two keys are mandatory and nothing else: a **TypeSafe provider** (the decision layer) and
**one LLM provider** — a first run configures exactly those two, validates both live, and never stores a literal key.
**Performance reference is Cline** (responsiveness, streaming feel, checkpoints, prompt caching), not Codex — Codex is
the reference for **security design** (adopted nearly 1:1 as a design; never its system prompt, never its code).
Standard displays a user expects from a coding agent: **session duration, tokens per second, context usage** — labelled
as measured or estimated, never invented.

## Constraints

Never write into `~/.hermes/godsv/app-src` (hardlink mirror of the installed app) · never `kill -9` the desktop main
process · never `git checkout -- .` on this dirty tree · no `sudo` (system packages go to the owner as an exact
command) · no paid sources, free/open source only · no commit or push without the owner's decision · never replace a
real security control with a test convenience · never present a host-native (Hermes) behaviour as our own work.

## Conventions

Package `src/skaldir/` (import `skaldir`; `godsv` is a deprecated alias) · distribution **`skaldir-agent`** ·
console script **`skaldir`** (`godsvoice` is the deprecated alias) · env `SKALDIR_*` with `GODSV_*` accepted ·
runtime dir `~/.hermes/skaldir` (falls back to `~/.hermes/godsv`) · CLI output is one JSON envelope per command with
fixed exit codes 0 ok / 1 usage / 2 verification failed / 3 blocked / 4 needs-credentials / 5 not-found / 6 conflict /
7 internal / 8 timeout · black/white only, bars as separators, no colour as information.

## Commands

```
skaldir --version                     # version
skaldir selftest --json               # the eight layers, honestly
skaldir contract dump --json           # every command + its JSON schema (unregistered must be [])
skaldir tools list --json              # the tool registry
skaldir agent run "<task>" --max-steps 8 --json
skaldir shell                          # interactive shell (slash palette, Ctrl-C cancels a command)
```
Test runner: `.venv/bin/python -m pytest tests -q -o addopts=""` with `HERMES_HOME`/`GODSV_HOME`/`GODSV_LLM_*` unset,
`PYTHONPATH=<repo>/src` pinned and `TMPDIR=/tmp` (a TMPDIR inside `$HOME` breaks the sandbox allowlist).

## Status matrix — measured 2026-09-19, integration + chat-UX round (self-measured where marked ✓)

| Feature | Status | Evidence / gap |
|---|---|---|
| **All eight layers reachable** | **HAVE** ✓ | `skaldir selftest --json` → `ok true, exit 0, 26 probes, failed []` — EXPAND · ACT · COMPOSE · ROUTE · SUPERVISE · ENFORCE · VERIFY · PERSIST |
| **Contract complete** | **HAVE** ✓ | `contract dump --json` → **65 roots / 271 leaves / 325 parser nodes** / `unregistered: []` (was 55/220) |
| **Core toolset** | **HAVE** ✓ | `tools list` → **21 tool rows (9 core tools HEALTHY)** (was 0): terminal, read_file, write_file, patch, search_files, list_dir, web_fetch, web_search, **docker.ensure** — schemas, permissions, timeouts, truncation, sandbox (outside write `EROFS`, network off, secrets stripped) |
| **Agent loop (model-driven tool calling)** | **HAVE** | real DeepSeek run: 2 steps, 3 parallel tool calls, usage 1886, sha256 verified locally; bound hit ⇒ `incomplete`, `answer: null`, exit 3 |
| **EXPAND (mining → registry → call → rollback)** | **HAVE** | real container: 311 verbs, independent oracle `ed4e0c9c…`, registered HEALTHY, called, rolled back; HTTP (OpenAPI) + MCP (9 tools) + distroless + **digest guards 2/6/3** |
| **ACT (computer use + sub-tools)** | **HAVE** | 5/5 actions verified on a container display; 5 sub-tools with the required schema; screenshot available via `xwd`, app-state honestly unavailable; live desktop refused by policy unless the target-scoped opt-in is set |
| **COMPOSE** | **HAVE** | `api→gui→api` chain `verified`, exit 0; an unsupported kind refuses with exit 3 naming it |
| **ROUTE / Jev** | **HAVE** | System-One enabled, provider wired, live decision ~1.05 s; four prohibitions enforced in three layers |
| **SUPERVISE** | **HAVE** | real trajectory check: 459 findings; triage **stayed silent** for an already-handled case (424 findings in the window) |
| **VERIFY** | **HAVE** | real run returned `FAIL` because the workspace suite was genuinely red; `UNKNOWN` ⇒ exit 3, never PASS |
| **ENFORCE (goal lock)** | **HAVE** | long-project E2E: model claimed success → gate `INCOMPLETE` with named blockers → redirect → **VERIFY PASS 3/3**; impossible criterion ⇒ `BLOCKED_EXTERNAL`, 0 accepted completions |
| **PERSIST** | **HAVE** | `runs.db` + resume; **SIGKILL proof**: budget and goal survived, parked op not retried; checkpoints survive kills mid-create/mid-restore with 0 torn files |
| **H1 Skills** | **HAVE** | own pipeline (`mine → validate → promote/demote → export`) |
| **H2 Tools** | **HAVE** | see core toolset above |
| **H3 Short-term memory (prompt assembly)** | **HAVE** | 7 sections, budgets, **byte-stable fingerprint** (`prompt build --text \| sha256sum` == `prompt fingerprint`), cache-break reported (exit 6) |
| **H4 Long-term memory** | **HAVE** | typed store + consolidation + contradictions; retrieval **prec@1 0.9231** vs flat list 0.2308 |
| **H5 Session history** | **HAVE** | message store with role-alternation invariants in DB triggers; **SIGKILL** ⇒ resume returns an identical history hash |
| **H6 Subagents** | **HAVE** | spawn **p50 1.96 ms** vs 91.0 ms per process (**46×**); queue, cancel (incl. real SIGINT), isolation proof, nesting depth 1 |
| **H7 File editing** | **HAVE** | atomic multi-file transactions: dry-run, verification, **byte-identical undo**, ambiguity refused, caps stated |
| **H8 Programmatic tool calling** | **HAVE** | sandboxed snippets call tools over JSON-RPC to the host; runaway killed, tree byte-identical, secrets scrubbed, bypass closed |
| Context compaction wired | **HAVE** | real loop run: 79 272 → 10 047 tokens, preserved set verified, fallback proven |
| Adversarial release suite | **HAVE** | 16 cases in 5.6 s; canary broken ⇒ **FAIL 8/8** with 8 defect classes, canary fixed ⇒ **PASS 9/9**; release-blocking exit codes |
| Permissions / sandbox profile | **HAVE** | one object, one hash in argv **and** prompt (proven in a real bwrap run); Landlock ABI 10 + always `no_new_privs` + **stdlib seccomp**; cgroup `pids.max`; 27 policy cases, 0 divergences |
| Provider breadth | **HAVE** | 16-entry catalog: **3 measured / 13 documented / 0 unverified**; DeepSeek refuses native `json_schema` (verbatim) → honest `json_object` fallback |
| Two-key setup | **HAVE** | `skaldir setup`: 41/41 checks; both keys validated live; literal keys refused; no key in report or config |
| Telemetry (duration · tok/s · context) | **HAVE** | footer + per-turn + export; estimates marked `~`, unpriced cost is `null`, 3 mutations caught by tests |
| **Chat UX wired (screen · art · markdown · slash)** | **HAVE** ✓ | real 80×24 pty, stub provider: live row `⠋ thinking — agent run — 0.4s`, art row `[▸_]  agent run  783 ms` above the badge `│ ✔ run … · exit 0 · 1.5 kB · 0.78 s`, the answer streamed as markdown, band `model gv-stub · ctx n/a · ~186 tok/s · $ n/a · 00:00`; pipe: 0 escapes, ASCII only. Slash surface 46 verbs / 61 spellings (`skaldir chat verbs`); palette + value completion incl. session/checkpoint ids and `@file` |
| Performance | **HAVE** ✓ | `--version` **0.03 s / 19 MB**; tool round-trip −72.6 %, connections/call −93 %, 100 turns −68.9 %; prompt cache **98.3 % hit**; API first frame **8.5 ms**; first user feedback up to **−88 %** |

### Still open (honest)
1. **Full-suite number, measured** (2026-09-19, `.venv/bin/python -m pytest tests -q -o addopts="" -p no:randomly`, clean env,
   `TMPDIR=/tmp`, no `timeout`): **4 failed / 6406 passed / 30 skipped in 943.74 s** (`/home/willy/gv-final-suite3.log`).
   All four attributed; none is a product regression: two `test_code_exec*` (**TEST defect** — the deny list hand-wrote the 8 old
   tool names while `_core_names()` returns 9; now derived from it), one `test_subagents` (flaky by construction — asserted `"c1"`
   absent from a path whose random hex run id contained it; now exact identifiers), one `test_api_latency_stream` (load-sensitive,
   passes alone; the budget is being made honest — warm-up+median or a ratio — not widened).
2. **`ruff` is a CI gate, not hygiene**: `.github/workflows/ci.yml` runs `ruff check src tests`; measured **177 findings** for
   exactly that invocation (E501 128 · F401 17 · I001 13 · UP035 6 · B905 2 · …) — the lint step would fail today. Fix in flight.
3. **Jev is not yet proven live/always-armed**: the layer is reachable and the compaction **mechanics** are proven (a long-project
   run compacted 3× — trigger 457 tokens ≥ 270, 6→4 messages, 1162 chars freed, goal/criteria/system prompt unchanged), but with
   the live Jev provider gated off there is **no proof** that those decisions came from `fast-jev-compaction` + real Jev decisions,
   and the adversarial browser runs are **not** shown to be Jev-steered. `fast-jev-compaction` is not yet a declared, evidenced
   dependency. In flight (JEV-LIVE).
4. **Docker → API → GUI → Verify is not a real product path yet**: the `api→gui→api` chain was proven in a harness while
   `step_runners` has **zero product importers**. In flight (COMPOSE-WIRE); the real user-flow verification (browser suite +
   the Jev-steered adversarial cases) is in flight too (VERIFY-GUI).
5. **Open GUI criterion**: `act check` reports `isolated-only` / `live_desktop_session_present: false` — live GUI verification
   needs a real desktop session.
6. `tools/gv_schemathesis_run.py` still prints a stale `PRODUCT_GAP` claim (refreshed once — re-check before quoting).
7. Documentation: `~/Documents/Hermes_Skaldir_Agent_Docs/CORE-FEATURES.md` (54 features, CAN/CANNOT matrix, evidence register,
   27 deduplicated unverified topics) and the audit folder `~/Documents/Final_Audit/` — read their unverified lists before
   quoting any number from them.
