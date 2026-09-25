# Architecture

Skaldir is a CLI-first runtime that wraps a running agent. It owns the decision, enforcement,
verification and persistence layers; the host (Hermes Agent) owns the model loop, the desktop shell
and the session store. Everything below was read from this tree — `HEAD a8fcaa1` plus the wave's
uncommitted edits — and from `skaldir selftest --json` and `skaldir contract dump --json` on
`2026-09-19` (CEST) (`skaldir 1.5.0`, contract `schema_version 1.0`).

| | Value |
|---|---|
| Distribution | `skaldir-agent` |
| Command | `skaldir` (`godsvoice` is the same entry point under the deprecated name) |
| Python package | `skaldir` (`src/skaldir/`); `src/godsv/` is a deprecation alias shim — `import godsv is import skaldir`, so module-level state and `isinstance` checks stay shared |
| Runtime directory | `~/.hermes/skaldir`, falling back to the pre-rename `~/.hermes/godsv` when that one already exists (see *Where state lives*) |
| Measured on this machine | `selftest`: 8 layers · 24 probes · **0 not reachable** · 2996 ms, runtime `/home/willy/.hermes/godsv`, exit 0 |
| Contract | 65 root · 267 leaf commands · 321 parser nodes · `unregistered: []` |

---

## The eight layers

Each layer is a responsibility, not a directory. `skaldir selftest --json` probes every one
read-only and reports `reachable` or `blocked` **with the reason** — never a bare "ok". The module
column names the modules the product itself points at as evidence for that layer's commands and
probes; the full measured inventory is in *The module set* below.

| Layer | Verb | Responsibility | Where it lives | CLI group | Status (measured 2026-09-19) |
|---|---|---|---|---|---|
| Self-Expanding Tool System | **EXPAND** | find an existing capability first, else build a typed adapter from a container (CLI/API/MCP), test it, register it live with version + health + permissions + rollback | `capability.py`, `capability_mining.py`, `capability_health.py`, `discovery.py`, `liveinsert.py`, `toolbuilder.py`, `toolhub.py`, `docker_runtime.py`, `docker_ensure.py`, `image_pin.py`, `image_inspect.py`, `adapter_http.py`, `adapter_mcp.py`, `toolscan.py`, `mcpserver.py`, `tools_core/**` | `tools`, `tools-hub`, `tools-core`, `registry`, `image`, `docker`, `mcp`, `code`, `coding`, `vision` — 63 leaves | **reachable** — runtime + `tools.db` present, 10 generated tools with manifest facts, 14 health states, docker 29.6.1 via probe (overlayfs) |
| Computer Use + GUI Sub-Tools | **ACT** | drive a real desktop (focus, click, type, scroll, drag) and verify against actual state; save repeated flows as sub-tools with preconditions and an end-state check | `computer_use.py`, `act_service.py`, `act_cli.py`, `vision_service.py`, `vision_cli.py`, `system_one_subtools.py` | `act`, `cu`, `vision` — 10 leaves | **reachable** — isolation levels `dry_run`/`sandbox_bwrap`/`container_podman` all true; in-process executor `verified`; live desktop verdict `isolated-only via container` (policy, not a missing bridge) |
| Learned Composite Tools | **COMPOSE** | combine tools into one reusable capability, validated then callable as a single tool | `compose_service.py`, `compose_cli.py`, `step_runners.py`, `skills.py`, `code_exec.py`, `art.py`, `streaming.py`, `tools_core/contract.py`, `tools_core/registry.py`, `tools_core/sandbox.py` | `compose`, `skills`, `tools`, `art`, `code`, `stream` — 19 leaves | **reachable** — 1 ranked candidate; lifecycle wired (create/validate/run/show over 5 verbs); 4 step kinds (`api`, `docker`, `gui`, `tool`), 4 runnable here |
| Jev Decision Layer | **ROUTE** | fast decisions *below* the agent: memory relevance, tool/sub-tool routing, capability ranking, context compaction, voice triage | `jev/**` (façade `jev/decide.py`, subscription `jev/wiring.py`), `jevcomp/**`, `system_one_service.py`, `system_one_adapters.py`, `system_one_flags.py`, `system_one_questions.py`, `system_one.py`, `context.py`, `context_manager.py`, `embedding.py`, `modelroles.py` | `jev`, `context`, `coding` — 10 leaves | **reachable** — flags loaded (`provider=llm_system_one_adapter`, `shadow=False`); `advise()` answered; 2458 ms (the slowest probe) |
| Voice Supervisor | **SUPERVISE** | independent supervisor for semantic loops, goal drift, missed errors, false completion claims, bad tool use, missing verification — and must *not* interfere when the agent already sees the problem | `voice.py`, `voice_service.py`, `voice_cli.py`, `synthesis.py`, `monitor.py`, `run_supervisor.py`, `guard.py`, `pause.py`, `patterns.py`, `inject.py`, `research.py` | `voice`, `coding` — 6 leaves; *no CLI group of its own* (the plugin feeds it) | **reachable** — 11 sessions readable; guard wired; pause store readable (61 leases) |
| Goal-Locked Execution | **ENFORCE** | completion is controlled by the runtime, not the model's claim: verify → PASS ⇒ done; FAIL/UNKNOWN ⇒ keep working, new turn, new context, restart or resume | `completion.py`, `complete.py`, `gates.py`, `execution.py`, `budget.py`, `agent_loop.py`, `agent_loop_cli.py`, `approval.py`, `permission_profile.py`, `security_audit.py`, `project_context.py`, `goal_run.py`, `edit_tx.py` | `completion`, `agent`, `run`, `approval`, `permission`, `security`, `goal`, `edit`, `context`, `coding` — 27 leaves | **reachable** — completion store `state=WORKING`; 6 terminal states known |
| Real-World Verification | **VERIFY** | not "does the code exist" but "can a user actually use it": build → open → run the real flow → check → reload → evaluate criteria. Only this may deliver a final PASS | `verify_service.py`, `verify_cli.py`, `verify_flows.py`, `evalharness.py`, `adversarial.py`, `adversarial_cli.py`, `computer_use.py`, `codectx.py` | `verify`, `eval`, `adversarial`, `code`, `coding` — 12 leaves | **reachable** — `plan_verification` → pytest; gate runner present; `verify run`/`verify flow`/`verify flows` on the real workspace, verdict PASS/FAIL/UNKNOWN (`UNKNOWN` exits 3, never PASS) |
| Long-Run Agent + Learning | **PERSIST** | persist goal + criteria, decisions, evidence, checkpoints, budgets; survive context limits, provider stalls and crashes; reuse project memory, incidents and tool chains | `events.py`, `memory.py`, `memory_store.py`, `runs_store.py`, `runs_cli.py`, `ledger.py`, `checkpoints.py`, `session_store.py`, `session_cli.py`, `incidents.py`, `notes.py`, `retrieval.py`, `telemetry.py`, `metrics.py`, `subagents.py`, `prompt_cache.py`, `prompt_builder.py`, `nightshift.py`, `streaming.py` | `runs`, `goal`, `session`, `checkpoint`, `memory-*`, `metrics`, `nightshift`, `agent`, `edit`, `prompt`, `stream`, `telemetry`, … — 66 leaves | **reachable** — 3249 events in 11 sessions; `runs.db` present (0 runs); `sessions.db` present (2 sessions, FTS5) |

A ninth group lives in the contract: `kernel` — 58 leaf commands over 24 roots (`chat`, `contract`,
`daemon`, `demo`, `desktop`, `doctor`, `host`, `init`, `ledger-show`, `mcp`, `model`, `pact-show`,
`prompts`, `provider`, `scan`, `selftest`, `serve`, `setup`, `shell`, `spend`, `style`, `vault`,
`warm`, `workflows`). It is the shared surface (CLI envelope, registry, provider config, treasury,
setup, the interactive shell) rather than a layer of its own.

### The three rules that shape every layer

- **No success without evidence.** `cli_contract.build_envelope` downgrades a command that reports
  `ok: true` with an empty evidence list to exit 7. A layer that is not wired reports `ok: false`
  plus a `blocked_reason`, never a green tick.
- **The façade rule (Jev).** Only `jev/decide.py` may import the substrate service; every other Jev
  module talks to the substrate's *contract* modules. A test enforces it — a second provider edge is
  how a façade silently becomes a second stack.
- **A module without a caller is not a feature.** A file that exists, has tests and is documented
  but that nothing in the product imports is a feature nobody can reach.
  `tests/test_no_orphan_modules.py` computes reachability from the entry points (console script,
  every `__main__`, and the host plugin in `integrations/`) over static imports *and* dotted
  module strings, and fails for any module that is neither reachable nor explicitly allow-listed
  with the owner of its missing caller.

---

## The module set (measured)

`src/skaldir/` is 202 modules: 172 directly under it, 12 in `tools_core/`, 12 in `jev/`, 6 in
`jevcomp/`. The two lists below are generated, not remembered: the first groups modules by the
evidence the layer's own commands and probes cite (`contract dump --json` → `default_evidence`,
`selftest --json` → probe `evidence`); the second is what that mapping does not name.

#### Named by the layer's own evidence (contract dump + selftest probes)

- **ACT** (5): `act_service`, `computer_use`, `system_one_subtools`, `vision_service`, `workflows`
- **COMPOSE** (9): `art`, `code_exec`, `compose_service`, `skills`, `streaming`, `tools`, `tools_core.contract`, `tools_core.registry`, `tools_core.sandbox`
- **ENFORCE** (15): `agent_loop`, `agent_loop_cli`, `approval`, `approval_cli`, `coding_features`, `completion`, `edit_cli`, `edit_tx`, `goal_run`, `permission_profile`, `project_context`, `runs_store`, `security_audit`, `tools_core.patch_tool`, `verify_service`
- **EXPAND** (23): `adapter_http`, `adapter_http_cli`, `adapter_mcp`, `capability`, `capability_health`, `capability_mining`, `codectx`, `codectx_cli`, `coding_features`, `docker_ensure`, `gates`, `image_inspect`, `image_pin`, `llm_provider`, `registry`, `toolbuilder`, `toolhub`, `tools`, `tools_core.contract`, `tools_core.docker`, `tools_core.registry`, `toolscan`, `vision_service`
- **PERSIST** (25): `agent_loop`, `checkpoints`, `checkpoints_cli`, `codectx`, `coding_features`, `edit_cli`, `edit_tx`, `incidents`, `llm_provider`, `memory`, `memory_store`, `metrics`, `nightshift`, `notes`, `prompt_builder`, `prompt_builder_cli`, `prompt_cache`, `prompt_cache_cli`, `retrieval`, `runs_store`, `session_cli`, `session_store`, `streaming`, `subagents`, `telemetry`
- **ROUTE** (12): `coding_features`, `context_manager`, `jev.contracts`, `jev.decide`, `jev.kinds`, `jev.ledger`, `jev.packs`, `jev.rules`, `jev.selftest`, `jevcomp.compact`, `jevcomp.state`, `system_one_questions`
- **SUPERVISE** (9): `coding_features`, `events`, `guard`, `jev.decide`, `monitor`, `patterns`, `pause`, `voice`, `voice_service`
- **VERIFY** (8): `adversarial`, `codectx`, `coding_features`, `computer_use`, `evalharness`, `jev.decide`, `verify_flows`, `verify_service`
- **kernel** (30): `api_server`, `appbuild`, `chatbar`, `cli`, `cli_contract`, `demo`, `desktop`, `host_bridge`, `jev.runtime`, `ledger`, `llm`, `llm_provider`, `logo`, `mcpserver`, `model_picker`, `pact`, `prompts`, `provider_catalog`, `provider_config`, `provider_registry_data`, `repl`, `secretscan`, `setup_flow`, `slash`, `structured`, `system_one_policy`, `theme`, `treasury`, `warm_session`, `workflows`

#### Off-layer modules (`src/skaldir/`, 74 of 172)

Modules no layer's command or probe evidence names — the shared kernel, the CLI wiring, the perf
seams and the System-One substrate:

`__main__`, `act_cli`, `adapter_mcp_cli`, `adversarial_cli`, `api_server_cli`, `budget`, `buildflow`, `chat_screen`, `cli_shared`, `code_exec_cli`, `coding_features_cli`, `complete`, `compose_cli`, `context`, `deprecation`, `discovery`, `docker_ensure_cli`, `docker_runtime`, `embedding`, `env`, `execution`, `first_run`, `goal_run_cli`, `host_cli`, `image_inspect_cli`, `image_pin_cli`, `inject`, `liveinsert`, `mcp_link`, `memory_upgrade_cli`, `model_cli`, `modelroles`, `perf_lazy`, `perf_progress`, `perf_runtime`, `progress`, `project_context_cli`, `promptrun`, `provider_cli`, `render`, `research`, `run_supervisor`, `runs_cli`, `security_audit_cli`, `security_linux`, `settings_service`, `setup_cli`, `spotlight`, `step_runners`, `stream_render`, `subagent_briefs`, `subagents_cli`, `synthesis`, `system_one`, `system_one_adapters`, `system_one_cache`, `system_one_calibration`, `system_one_canary`, `system_one_flags`, `system_one_guistate`, `system_one_metrics`, `system_one_sanitizer`, `system_one_service`, `system_one_shadow`, `system_one_treasury`, `telemetry_cli`, `toolbuilder_cli`, `toolcall`, `toolusage`, `untrusted`, `verify_cli`, `vision_cli`, `voice_cli`, `warm_cli`

#### Sub-packages

- `tools_core/` (12): `__main__`, `bridge`, `cli`, `contract`, `docker`, `files`, `patch_tool`, `registry`, `sandbox`, `search`, `terminal`, `web`
- `jev/` (12): `__main__`, `cli`, `contracts`, `decide`, `fixture`, `kinds`, `ledger`, `packs`, `rules`, `runtime`, `selftest`, `wiring`
- `jevcomp/` (6): `compact`, `errors`, `fixture`, `request`, `state`, `types`

Five of those modules have no product caller today and are registered as open gaps (with the owner
of the missing caller) inside `tests/test_no_orphan_modules.py`: `perf_progress`,
`system_one_calibration`, `system_one_canary`, `system_one_shadow`, `system_one_treasury`. Everything
else in the list above is reached from the CLI, the host plugin, or a module that is.

---

## The seams to Hermes

The core is independent: CLI, provider client, Jev, compaction, Docker runtime, verification and the
run store all live in `src/skaldir/**`. Hermes appears in **three** places, and one optional read.

**1. We drive the host.** `desktop.build-app` / `install` / `start` / `status` / `uninstall`
(`desktop.py`, `appbuild.py`, `buildflow.py`). The desktop app *is* the Hermes shell plus
`app-overlay/` patches, which is why the installed tree `~/.hermes/<runtime>/app-src` is a hardlink
mirror and must stay read-only. UI changes go through anchored overlay patches, never by editing the
installed tree.

**2. The host calls us.** `mcp.serve` exposes the MCP surface, and `mcp_link.py` binds it into the
host config **through the host's own commands** (`hermes mcp add` / `hermes mcp remove`, fixed argv,
no shell), idempotent, merging, with a backup. `~/.hermes/config.yaml` is never hand-edited.

**3. The host mirrors into us.** `integrations/hermes/godsv_plugin/` registers the hooks
(`on_session_start`, `post_tool_call`, `post_llm_call`, `on_session_end`, `transform_tool_result`)
and feeds the live trajectory into our event store. That mirror is what makes SUPERVISE reachable
today — it has no CLI group of its own — and it is the only caller of three modules that no CLI
verb touches: `discovery` (§4c capability discovery at the chat boundary), `liveinsert` (the
native-tool surface of a verified capability) and `synthesis` (LLM triage/synthesis on top of
retrieval evidence). A reachability check that ignores `integrations/` reports those three as
orphans; `tests/test_no_orphan_modules.py` counts the plugin as the product surface it is.

**One optional read:** `modelroles.py` lazily imports `hermes_cli.config.load_config_readonly` to
read the provider/model registry. It is wrapped in a try block, so the package runs without Hermes
installed.

Everything else — platform adapters, dashboards, the TUI, the skills catalogue — is deliberately
**not** reimplemented. If the host has no machine-readable surface for something, Skaldir reports
*not reachable* instead of scraping human text.

---

## Where state lives

### The runtime directory (one resolver)

`registry.py` is the single source of truth; no surface derives a default of its own. Resolution,
first match wins:

1. an explicit `--runtime` argument;
2. an explicit `--db` argument → runtime = that DB's directory;
3. `$SKALDIR_HOME` (fallback `$GODSV_HOME`) → that directory;
4. `$HERMES_HOME/skaldir`, fallback `~/.hermes/skaldir` — or the pre-rename `godsv` directory when
   that one already exists and the new one does not (reported as
   *"godsv: pre-rename directory, still in use"*).

Measured on this machine today: `runtime_dir = /home/willy/.hermes/godsv`, selected with exactly
that pre-rename reason — `~/.hermes/skaldir` does not exist yet.

`registry.status --json` always reports what it picked and **why**. Legacy cwd-relative
`.godsv/tools.db` databases are detected and *reported*, never read for selection.

### What sits in the runtime directory

Measured by listing `/home/willy/.hermes/godsv` on 2026-09-19 (sizes and counts as observed):

| Path | Contents |
|---|---|
| `events.db` | append-only event store (SQLite, WAL) — the session trajectory; 3249 events in 11 sessions |
| `memory.db` | incidents and research notes (FTS5 + embeddings) |
| `tools.db` | tool specs and the capability registry (10 generated tools) |
| `treasury.db`, `vault.json` | credential handles and encrypted secrets (Fernet, 0600/0700) |
| `pauses.db` | pause/redirect leases (TTL, heartbeat, watchdog); 61 leases |
| `sessions.db` | the session store (2 sessions, 0 messages, FTS5 search) |
| `runs.db` | the long-run store — present, 0 runs so far (176 KB) |
| `generated_tools/` | generated adapters, each with `manifest.yaml`, `adapter.json`, `surface.json`, tests |
| `container_rollbacks/`, `mcp_rollbacks/` | rollback snapshots of registered container/MCP capabilities |
| `verify/` | `runs/<run_id>.json` + one `evidence.jsonl` line per criterion — created by the first `verify` run (not present before one has run) |
| `adversarial/` | adversarial run records: `<run_id>.json`, `.plan.json`, `.decisions.jsonl` |
| `workflow_runs/`, `composites/`, `checkpoints/`, `act_subtools/` | recorded workflow runs, validated composites, checkpoints, saved GUI sub-tools |
| `jevlive/` | the live-Jev path's own state (deadport / live / offline markers) |
| `approval/events.jsonl`, `completion_history.jsonl`, `cu_runs.jsonl`, `tool_usage.jsonl`, `tool_builds.jsonl`, `capability_*.jsonl`, `inbox.jsonl` | the append-only journals (approvals, completions, computer-use runs, tool usage/builds, capability mining/health/requests, the host inbox) |
| `settings.yaml` | runtime settings (e.g. whether the System-One substrate is on) |
| `app/`, `app-src/`, `app-backup-*`, `*userdata*` | the desktop app build and its isolated Electron profiles |
| `research/`, `vision/`, `shell/history.jsonl`, `receipts/` | research notes, screenshots, shell history, build receipts |

`prompts/` is **not** here: prompts resolve to `<repo>/prompts` or `$SKALDIR_PROMPTS`. On a wheel
install the files ship inside the package as `skaldir/prompts/` and that env var has to point at
them (see `INSTALL.md`).

### The workspace, not the runtime

Per-project state lives in the project's own `.godsv/` directory, created by `skaldir init`:
`pact.yaml` (goal, non-goals, acceptance, budget, scope), `ledger.yaml` (continuity state that
survives context boundaries), and `verify.json` / `checks.json` for declared verification checks.
`.godsv/` is git-ignored.

---

## The CLI contract

Every command with `--json` prints **exactly one** envelope on stdout; the human text moves to
stderr.

```
{schema_version, ok, command, godsvoice_version, started_at, duration_ms,
 exit_code, data, error, evidence, warnings, next_actions}
```

Nine exit codes, so callers never parse text: `0` ok · `1` usage · `2` verification failed ·
`3` blocked · `4` needs credentials · `5` not found · `6` conflict · `7` internal · `8` unknown.

`skaldir contract dump --json` is the machine-readable inventory (arguments + JSON Schema per
command, measured 2026-09-19: **65 root / 267 leaf / 321 parser nodes / 0 unregistered**) and is the
source both the CLI reference (`docs/CLI-REFERENCE.md`, regenerated from it by
`tools/gv_cli_reference.py`) and the property-test run are generated from. Adding a command without
a `cli_contract.COMMANDS` entry fails the suite — a command cannot ship without a contract.
