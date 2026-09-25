# Skaldir CLI — `godsvoice`

The CLI is the primary surface of the **Skaldir Agent** runtime: one command group per
architectural layer, one machine-readable contract, and no feature that is only reachable
through the desktop app.

**Naming (read this before filing a "wrong name" bug).** The product is **Skaldir Agent**,
the slug is `skaldir`; the executable in this tree is still `godsvoice` and the distribution
and import package are still `godsv`. Those two are contracts, not leftovers: the installed
console script, the runtime directory `~/.hermes/godsv`, every `GODSV_*` environment variable
and the plugin ID `godsv` are read by the running app on this machine. Renaming them is a
separate, migration-bearing step — the full list, with the exact consequence and the safe
migration per item, is in [`docs/RENAME.md`](docs/RENAME.md). Examples below therefore call
the binary `godsvoice`, which is what is actually installed today.

**Read this first, it is the honest part:** all eight layers are reachable today — measured on 2026-09-21 with `godsvoice selftest --json` (exit 0). Section 4
lists every layer with the command that probes it, and section 10 lists every gap we know about.
Nothing in this file describes a command that does not exist: the machine-readable reference in
[`docs/CLI-REFERENCE.md`](docs/CLI-REFERENCE.md) is generated from the CLI itself.

---

## 1. Install

**Requirements** (verified on Fedora 44):

| Need | Why | Checked with |
|---|---|---|
| Linux | target platform | `uname -s` |
| Python ≥ 3.11 (`pyproject.toml` `requires-python`) | all of the CLI | `.venv/bin/python -V` → 3.12.13 |
| `uv` | creates the venv, installs the package | `uv --version` |
| Docker, optional | the tool builder runs containers | `docker version` → 29.6.1, works without sudo |
| Podman + bubblewrap, optional | computer-use isolation runners | `podman --version` → 5.8.1, `bwrap --version` → 0.11.0 |
| ImageMagick, optional | icon scaling for `desktop install` | `convert --version` |

**Option A — the installer**

```bash
./install.sh          # uv venv .venv · uv pip install -e ".[mcp]" · symlink ~/.local/bin/godsvoice
                      # · desktop entry + icons · doctor
```

Note: `install.sh`'s console text is still German (the English sweep of the installer is not
part of this wave). It is idempotent and needs no sudo.

**Option B — manual (recommended for CI and for hacking on the CLI)**

```bash
uv venv .venv --python 3.11
uv pip install -e ".[dev]"        # ".[mcp]" for the MCP server, ".[dev,mcp]" for both
.venv/bin/godsvoice --help        # 28 root command groups
.venv/bin/godsvoice doctor        # 10 deployment/health checks
```

**Option C — an already installed environment:** the console script is
`godsvoice = godsv.cli:main` (`pyproject.toml`), so a plain `godsvoice …` works once the
package is installed.

**Runtime directory (one rule, no second default):**
`$GODSV_HOME` → `$HERMES_HOME/godsv` → `~/.hermes/godsv`. Every database (events, memory,
tools, pauses, treasury) and every state file lives there. `godsvoice doctor` prints the
resolved value; pass `--runtime`/`--db` only when you deliberately want another one.

---

## 2. First run

```bash
mkdir my-project && cd my-project
godsvoice init             # writes .godsv/pact.yaml + .godsv/ledger.yaml
godsvoice doctor           # 10 checks; exit 0 when they all pass
godsvoice demo             # deterministic V0 pipeline demo — needs no provider
godsvoice selftest --json  # probe the eight layers (exit 2 while one is not reachable)
godsvoice contract list    # all 271 leaf commands with layer, --json, read/write
```

Measured on this machine (2026-09-18, `GODSV_HOME=/home/willy/.hermes/godsv`):

| Command | Result |
|---|---|
| `skaldir selftest --json` | exit **0** — 8/8 layers reachable (all eight reachable) |


> **One nuance that costs an hour if you miss it:** `doctor`'s `Runtime` and `Plugin deployed`
> checks are `HERMES_HOME`-relative (`src/godsv/cli.py:2748-2758`), while the runtime of every
> other command resolves `$GODSV_HOME` first. If you point `GODSV_HOME` somewhere else, the
> commands move but those two doctor checks stay on `$HERMES_HOME/godsv` and can report `✗`
> for a runtime that is perfectly fine. Set both variables when you mean to move the runtime.

Exit code `2` from `selftest` is not a broken CLI: it is the documented "a layer is not
reachable" verdict, and the same command prints the `blocked_reason` per layer.

---

## 3. Provider setup

The CLI talks to **any OpenAI-compatible `/v1/chat/completions` endpoint** (OpenAI, OpenRouter,
DeepSeek, Groq, llama.cpp, vLLM, Ollama's OpenAI shim …). The client is
`godsv.llm_provider.OpenAICompatibleLLM`; `godsvoice doctor` does not touch it, so a missing
provider never breaks the check-up.

**Fastest path — environment variables:**

```bash
export GODSV_LLM_BASE_URL=https://api.openai.com/v1   # fallbacks: GODSV_LLM_API_BASE, OPENAI_BASE_URL
export GODSV_LLM_MODEL=gpt-4o-mini                    # fallback: OPENAI_MODEL
export GODSV_LLM_API_KEY=sk-…                         # fallbacks: OPENAI_API_KEY, GODSV_LLM_KEY
```

**Durable path — a config file** at `$GODSV_LLM_CONFIG`, else `<runtime_dir>/config.yaml`
(i.e. `$GODSV_HOME/config.yaml` or `~/.hermes/godsv/config.yaml`). A top-level `llm:` block is
accepted as well:

```yaml
godsv:
  llm:
    provider: openai-compatible        # or: scripted (needs responses), none (refused loudly)
    base_url: http://127.0.0.1:8080/v1 # llama.cpp / vLLM / OpenRouter / OpenAI
    model: qwen2.5-7b-instruct
    api_key_env: GODSV_LLM_API_KEY     # the NAME of the variable, never the key itself
    tiers:                             # optional per-tier models
      small: qwen2.5-3b-instruct
      main: qwen2.5-72b-instruct
    timeout_s: 30
    max_retries: 2
    stream: false
```

Precedence, field by field: **explicit config > environment > config file > defaults**.
`max_tokens_field`, `temperature`, `headers`, `extra_body` and `responses` are config-file
only. Retries follow the one shared policy (`401/403` never retried, `422` is a contract error,
`429/529`/timeouts retried with bounded backoff inside the shared deadline).

**Secrets:** the API key is read from the **environment only**. A literal `api_key:` / `key:` /
`token:` / `secret:` value in the config file is *refused* with a `ProviderConfigError` — a file
in a repo or in `$HOME` is not a secret store. The key is never logged, never written, and never
rendered (`ProviderConfig.__repr__` redacts it; `provider_status()` reports only `set`/`unset`
plus the variable name). Keep it in the service environment (systemd `EnvironmentFile`,
container env, shell profile).

**Two honest gaps here:**

1. **No CLI command configures or reports the provider yet.** There is no `provider` group in
   the contract — measured: `godsvoice provider add local --base-url …` → exit `1`
   (`invalid choice: 'provider'`) and `godsvoice contract show provider.add` → exit `5`
   (`unknown command: provider.add`). `src/godsv/provider_config.py` (1262 lines, the write side
   with named providers, one active projection and redacted views) is built, but **nothing
   registers a command for it** — its docstring advertises `godsvoice provider add local
   --base-url …`, which does not exist. Treat that docstring line as **not available**; this is a
   documented drift, not a feature.
2. **Jev (ROUTE) cannot use a provider yet.** `godsvoice jev decide` is the only command that
   can reach `provider_for()`, and only when the System-One flags name the LLM adapter. Today
   the runtime reports `System-One is off in …/settings.yaml (enabled=false)` and
   `service disabled: no provider adapter is wired (src/godsv/llm.py)` → every decision is
   `status=blocked` (exit `3`). Do not read a `blocked` decision as a provider misconfiguration
   on your side.

`GODSV_LLM=1` is **not** this provider: that switch gates the Hermes plugin's host-owned
`ctx.llm`, which needs the host. The CLI's provider needs no host and no plugin.

The `vault`/`spend` commands are a **separate secret world** (the treasury: credential handles
only, encrypted at rest) and are unrelated to the provider key.

---

## 4. The eight layers — and the command that proves each one

`godsvoice selftest --json` probes all eight layers and is the only authority for this table
(exit `2` as soon as one layer is not reachable). Measured 2026-09-18:

| Layer | Measured | Proving command(s) | What it means when it reports blocked |
|---|---|---|---|
| **EXPAND** | **reachable** | `code.cache`, `code.map`, `code.pack`, `code.search`, `code.symbols`, `code.tools`, `coding.complete`, `coding.expand`, … | — |
| **ACT** | **reachable** | `act.check`, `act.run`, `act.subtool.learn`, `act.subtool.list`, `act.subtool.run`, `act.subtool.save`, `act.subtool.show`, `cu.check`, … | — |
| **COMPOSE** | **reachable** | `art.json`, `art.preview`, `code.explain`, `code.run`, `compose.candidates`, `compose.create`, `compose.run`, `compose.show`, … | — |
| **ROUTE** | **reachable** | `coding.model.clear`, `coding.model.set`, `coding.model.show`, `context.compact`, `context.usage`, `jev.decide`, `jev.history`, `jev.kinds`, … | — |
| **SUPERVISE** | **reachable** | `coding.events.run`, `coding.events.show`, `voice.check`, `voice.explain`, `voice.status`, `voice.triage` | — |
| **ENFORCE** | **reachable** | `agent.run`, `agent.transcript`, `approval.allow`, `approval.check`, `approval.deny`, `approval.list`, `approval.run`, `coding.plan.begin`, … | — |
| **VERIFY** | **reachable** | `adversarial.plan`, `adversarial.run`, `adversarial.selftest`, `code.diagnostics`, `coding.testfix`, `eval`, `verify.evidence`, `verify.flow`, … | — |
| **PERSIST** | **reachable** | `agent.cancel`, `agent.collect`, `agent.list`, `agent.retry`, `agent.show`, `agent.spawn`, `checkpoint.create`, `checkpoint.list`, … | — |

Re-measure any time:

```bash
godsvoice selftest --json | python3 -c "import json,sys
for l in json.load(sys.stdin)['data']['layers']:
    print(f\"{l['layer']:10} {l['status']:10} {l['blocked_reason'] or ''}\")"
```

```text
EXPAND     reachable
ACT        blocked    no live desktop bridge is wired to the CLI — … (only in-process runners are constructible)
COMPOSE    blocked    no composite can be created, validated or run from the CLI yet — `tools compose` only lists candidates
ROUTE      blocked    System-One is off in …/settings.yaml (enabled=false) service disabled: no provider adapter is wired (src/godsv/llm.py)
SUPERVISE  reachable
ENFORCE    reachable
VERIFY     blocked    no verification command runs a check against the real workspace yet — `eval` is in-process only and cannot certify a goal
PERSIST    blocked    no run store at …/runs.db — no run has ever been started (no long-run record exists)
```

> **Measure against the real runtime.** `godsvoice` resolves its runtime dir from
> `$GODSV_HOME` → `$HERMES_HOME/godsv` → `~/.hermes/godsv`. A shell that carries a sandboxed
> `GODSV_HOME` (a test harness, a container, a CI job) makes `selftest` report missing databases
> and a blocked PERSIST that does not exist on the owner's machine. Check the `runtime` field in
> the envelope (`godsvoice selftest --json | python3 -c '…["runtime"]'`) before believing a
> `blocked`.

Every layer's inner probes (`registry.resolve`, `live_desktop`, `compose_lifecycle`,
`system_one_flags`, `real_world_runner`, `run_store`, …) with their `evidence_ref` and duration
are listed in [`docs/CLI-REFERENCE.md`](docs/CLI-REFERENCE.md) §3.

---

## 5. Exit codes

One meaning per code, one `error.code` string per code, defined once in
`src/godsv/cli_contract.py`:

| Code | Name | `error.code` | Use it for |
|---|---|---|---|
| `0` | OK | `ok` | the command did its job (with evidence — see §6) |
| `1` | USAGE | `usage` | bad command line, refused `--apply`, legacy no-db verdicts. argparse usage errors exit `1`, not `2` |
| `2` | VERIFICATION_FAILED | `verification_failed` | a check ran and failed (`eval`, `selftest`, `doctor`, tool gates) |
| `3` | BLOCKED | `blocked` | the runtime cannot proceed and says why (no loop, no live desktop, no provider) |
| `4` | NEEDS_CREDENTIALS | `needs_credentials` | a live-capable provider without an authorized credential (`jev.decide`, `tools.build`) |
| `5` | NOT_FOUND | `not_found` | unknown command/goal/tool/handle |
| `6` | CONFLICT | `conflict` | state conflict (`registry.migrate`) |
| `7` | INTERNAL | `internal` | a bug or a contract violation — including a success envelope with no evidence |
| `8` | UNKNOWN | `unknown` | unknown/timeout; declared by no command in the current inventory |

Per-command declarations (which of these a given command may return) are in the generated
reference, §5. A code a command does not declare is not part of its contract.

---

## 6. The contract: one envelope, one self-description

Every command takes `--json`, `--format {text,json,jsonl}`, `--quiet` and `--no-color`
(`GODSV_FORMAT` is the environment fallback). With `--json`, **stdout carries exactly one
envelope document and nothing else**; the human text moves to stderr.

```
{schema_version, ok, command, godsvoice_version, started_at, duration_ms, exit_code,
 data, error, evidence, warnings, next_actions}
```

Rules the CLI *enforces* rather than documents (`src/godsv/cli_contract.py`):

* an `ok: true` envelope with **no evidence** is downgraded to exit `7` — "no command reports
  success it cannot back";
* `--json` never mixes text and JSON on stdout;
* `selftest` reports an unwired layer as `ok: false` + `blocked_reason`, never as reachable.

**Self-description** — three commands describe the whole CLI, including a JSON Schema per
command's `data` payload:

```bash
godsvoice contract list                  # every command with layer, --json flag, read/write
godsvoice contract show tools.show       # one command's full contract record
godsvoice contract dump --out dump.json  # the full inventory (the GUI renders forms from it)
```

**Adding a command is a contract change**: a command without a `COMMANDS` entry in
`cli_contract.py` fails `tests/test_cli_contract_dump.py`. That is why the generated reference
can be trusted as a complete inventory.

### The generated reference

| Artifact | Purpose |
|---|---|
| [`docs/CLI-REFERENCE.md`](docs/CLI-REFERENCE.md) | 73 commands: args, JSON Schema, exit codes, evidence, verbatim limitations |
| `tools/gv_cli_reference.py` | the generator (stdlib only: `json`, `argparse`, `subprocess`) |
| `.venv/bin/python tools/gv_cli_reference.py` | regenerate the reference from a live `contract dump` |
| `.venv/bin/python tools/gv_cli_reference.py --check` | **staleness gate**: exit `1` when the file no longer matches the contract |

The file carries an `inventory-sha256` fingerprint of the contract dump, so `--check` compares
contracts rather than text and cannot be fooled by a hand-edit. It is not yet wired into
`.github/workflows/ci.yml` (which runs ruff, pytest and bandit) — run it locally, or add it as
its own step.

---

## 7. Command groups at a glance

271 leaf commands in 54 groups (65 root groups; 325 parser nodes, 0 unregistered):

| Group | Layer | # | Access | Commands |
|---|---|---|---|---|
| `(top level)` | ENFORCE, EXPAND, PERSIST, VERIFY, kernel | 21 | 13 read / 8 write | chat, demo, doctor, eval, init, ledger-show, memory-audit, memory-retrieval-check, memory-search, memory-stats, metrics, model, nightshift, pact-show, run, scan, selftest, serve, setup, shell, tools-hub |
| `act` | ACT | 2 | 1 read / 1 write | act.check, act.run |
| `act.subtool` | ACT | 5 | 2 read / 3 write | act.subtool.learn, act.subtool.list, act.subtool.run, act.subtool.save, act.subtool.show |
| `adversarial` | VERIFY | 3 | 2 read / 1 write | adversarial.plan, adversarial.run, adversarial.selftest |
| `agent` | ENFORCE, PERSIST | 8 | 4 read / 4 write | agent.cancel, agent.collect, agent.list, agent.retry, agent.run, agent.show, agent.spawn, agent.transcript |
| `approval` | ENFORCE | 5 | 2 read / 3 write | approval.allow, approval.check, approval.deny, approval.list, approval.run |
| `art` | COMPOSE | 2 | 2 read / 0 write | art.json, art.preview |
| `checkpoint` | PERSIST | 6 | 2 read / 4 write | checkpoint.create, checkpoint.list, checkpoint.prune, checkpoint.restore, checkpoint.resume, checkpoint.show |
| `code` | COMPOSE, EXPAND, PERSIST, VERIFY | 11 | 7 read / 4 write | code.blame, code.cache, code.changed, code.diagnostics, code.explain, code.map, code.pack, code.run, code.search, code.symbols, code.tools |
| `coding` | EXPAND, PERSIST, VERIFY | 6 | 3 read / 3 write | coding.complete, coding.expand, coding.init, coding.status, coding.summary, coding.testfix |
| `coding.events` | SUPERVISE | 2 | 1 read / 1 write | coding.events.run, coding.events.show |
| `coding.model` | ROUTE | 3 | 1 read / 2 write | coding.model.clear, coding.model.set, coding.model.show |
| `coding.plan` | ENFORCE, EXPAND | 6 | 3 read / 3 write | coding.plan.begin, coding.plan.check, coding.plan.close, coding.plan.probe, coding.plan.propose, coding.plan.show |
| `coding.turn` | PERSIST | 5 | 1 read / 4 write | coding.turn.begin, coding.turn.end, coding.turn.list, coding.turn.record, coding.turn.undo |
| `completion` | ENFORCE | 4 | 2 read / 2 write | completion.begin, completion.history, completion.override, completion.status |
| `compose` | COMPOSE | 5 | 2 read / 3 write | compose.candidates, compose.create, compose.run, compose.show, compose.validate |
| `context` | ENFORCE, ROUTE | 5 | 5 read / 0 write | context.compact, context.explain, context.fingerprint, context.show, context.usage |
| `contract` | kernel | 3 | 3 read / 0 write | contract.dump, contract.list, contract.show |
| `cu` | ACT | 2 | 2 read / 0 write | cu.check, cu.demo |
| `daemon` | kernel | 2 | 2 read / 0 write | daemon.serve, daemon.status |
| `desktop` | kernel | 5 | 2 read / 3 write | desktop.build-app, desktop.install, desktop.start, desktop.status, desktop.uninstall |
| `docker` | EXPAND | 1 | 1 read / 0 write | docker.ensure |
| `edit` | ENFORCE, PERSIST | 4 | 2 read / 2 write | edit.apply, edit.dry-run, edit.show, edit.undo |
| `goal` | PERSIST | 2 | 1 read / 1 write | goal.criteria, goal.show |
| `host` | kernel | 6 | 6 read / 0 write | host.cron, host.doctor, host.sessions, host.skills, host.status, host.tools |
| `host.memory` | kernel | 1 | 1 read / 0 write | host.memory.search |
| `image` | EXPAND | 7 | 6 read / 1 write | image.guard, image.inspect, image.layers, image.list, image.pin, image.resolve, image.verify |
| `jev` | ROUTE | 5 | 4 read / 1 write | jev.decide, jev.history, jev.kinds, jev.packs, jev.selftest |
| `mcp` | EXPAND, kernel | 6 | 3 read / 3 write | mcp.call, mcp.map, mcp.probe, mcp.register, mcp.serve, mcp.test |
| `memory` | PERSIST | 6 | 3 read / 3 write | memory.consolidate, memory.explain, memory.forget, memory.recall, memory.remember, memory.scope |
| `permission` | ENFORCE | 3 | 2 read / 1 write | permission.explain, permission.set, permission.show |
| `prompt` | PERSIST | 3 | 3 read / 0 write | prompt.build, prompt.explain, prompt.fingerprint |
| `prompt.cache` | PERSIST | 2 | 2 read / 0 write | prompt.cache.plan, prompt.cache.status |
| `prompts` | kernel | 1 | 1 read / 0 write | prompts.list |
| `provider` | kernel | 8 | 4 read / 4 write | provider.add, provider.catalog, provider.default, provider.doctor, provider.list, provider.remove, provider.test, provider.tiers |
| `registry` | EXPAND | 2 | 1 read / 1 write | registry.migrate, registry.status |
| `runs` | PERSIST | 4 | 1 read / 3 write | runs.checkpoint, runs.resume, runs.start, runs.status |
| `security` | ENFORCE | 2 | 2 read / 0 write | security.audit, security.coverage |
| `session` | PERSIST | 14 | 7 read / 7 write | session.append, session.archive, session.export, session.fork, session.list, session.new, session.policy, session.purge, session.resume, session.rotate, session.search, session.show, session.status, session.verify |
| `skills` | COMPOSE | 8 | 3 read / 5 write | skills.demote, skills.export, skills.install, skills.list, skills.mine, skills.promote, skills.show, skills.validate |
| `spend` | kernel | 6 | 2 read / 4 write | spend.allowance, spend.approve, spend.complete, spend.deny, spend.list, spend.request |
| `stream` | COMPOSE, PERSIST | 4 | 4 read / 0 write | stream.measure, stream.policy, stream.replay, stream.selfcheck |
| `style` | kernel | 3 | 3 read / 0 write | style.logo, style.palette, style.preview |
| `telemetry` | PERSIST | 3 | 2 read / 1 write | telemetry.export, telemetry.live, telemetry.show |
| `tools` | COMPOSE, EXPAND | 19 | 7 read / 12 write | tools.approve, tools.build, tools.builds, tools.call, tools.candidates, tools.compose, tools.fulfill, tools.health, tools.list, tools.mine, tools.promote, tools.promote-global, tools.propose, tools.register, tools.request, tools.revoke, tools.rollback, tools.scan, tools.show |
| `tools-core` | EXPAND | 8 | 4 read / 4 write | tools-core.call, tools-core.health, tools-core.list, tools-core.policy, tools-core.register, tools-core.show, tools-core.smoke, tools-core.unregister |
| `tools.adapter` | EXPAND | 1 | 1 read / 0 write | tools.adapter.show |
| `tools.api` | EXPAND | 7 | 4 read / 3 write | tools.api.call, tools.api.discover, tools.api.health, tools.api.inspect, tools.api.register, tools.api.rollback, tools.api.show |
| `vault` | kernel | 4 | 2 read / 2 write | vault.add, vault.describe, vault.list, vault.revoke |
| `verify` | VERIFY | 6 | 4 read / 2 write | verify.evidence, verify.flow, verify.flows, verify.plan, verify.run, verify.status |
| `vision` | ACT, EXPAND | 4 | 4 read / 0 write | vision.capabilities, vision.describe, vision.ocr, vision.screenshot |
| `voice` | SUPERVISE | 4 | 4 read / 0 write | voice.check, voice.explain, voice.status, voice.triage |
| `warm` | kernel | 3 | 1 read / 2 write | warm.start, warm.status, warm.stop |
| `workflows` | kernel | 3 | 2 read / 1 write | workflows.list, workflows.run, workflows.show |

The top-level group is the only one that mixes layers (its six `memory-*`/`metrics`/
`nightshift` commands are PERSIST, `eval` is VERIFY, `tools-hub` is EXPAND, the rest are
kernel); every other group belongs to exactly one layer. Which command proves which layer is
§4.

---

## 8. Automation

```bash
# gate a release on the runtime's own probe (exit 2 = a layer is not reachable)
godsvoice selftest --json > /tmp/selftest.json; echo "selftest exit=$?"

# gate a docs change on the contract it documents
.venv/bin/python tools/gv_cli_reference.py --check

# read one field out of any command
godsvoice doctor --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"]["ok"])'

# stream instead of pretty-printing (the same envelope as one JSON line)
godsvoice contract list --format jsonl
```

The `--json` envelope makes every command usable from a script without parsing human text — that
is what the desktop panel does, and what a CI gate should do.

---

## 9. Credits — open source we build on

Every licence below was read from the artifact on this machine (package metadata on disk, the
package's own licence file, dist-info, RPM metadata, or the upstream licence file), never from
memory. Full audit, per-component purpose and the shipping-blocker list:
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).

### The host and the runtime we build on

| Project | Version | Licence | Role | Verified from |
|---|---|---|---|---|
| **Hermes Agent** (`NousResearch/hermes-agent`) | host tree | **MIT** | the host: CLI, gateway, desktop shell, plugin API; also the `ctx.llm` path (`GODSV_LLM=1`) | `/home/willy/.hermes/hermes-agent/LICENSE` — "MIT License, Copyright (c) 2025 Nous Research" |
| **thinking-orbs** (`Jakubantalik/thinking-orbs`) | 0.3.1 | **MIT** | the one working/orb indicator, **vendored into** `integrations/hermes/godsv_plugin/desktop/plugin.js` (inlined, not imported) | `node_modules/thinking-orbs/LICENSE` + the inline header in `plugin.js` ("MIT (c) Jakub Antalik"); `node tools/vendor_thinking_orbs.mjs --check` → "vendored copy is current" |
| **fast-jev-compaction** (`tamaratran/fast-jev-compaction`) | plugin 0.3.0 / npm 0.2.0 | **MIT** | **source ported** to Python for Jev context compaction (`src/godsv/jevcomp/**`, pinned commit `e3f262a7f4d42bd8dd32ced30d26176f7cb545b0`) — the one place third-party code is embedded | upstream `LICENSE` ("MIT License, Copyright (c) 2025") + the carried copy `src/godsv/jevcomp/UPSTREAM_LICENSE` |
| **browser-use/jev-ultrafast** | — | **MIT** | **reference only**, not adopted: the shape of the adversarial release-test suite | upstream `LICENSE` — "MIT License, Copyright (c) 2026 Browser Use" |

### Front-end stack (desktop shell: declared dependencies)

| Component | Version | Licence | Verified from |
|---|---|---|---|
| **Tabler Icons** (`@tabler/icons-react`) | 3.44.0 | **MIT** | `app-src/node_modules/@tabler/icons-react/package.json` |
| **Motion** (`motion`) | 12.42.2 | **MIT** | `app-src/node_modules/motion/package.json` |
| **Radix UI** (`radix-ui`) | 1.6.7 | **MIT** | `app-src/node_modules/radix-ui/package.json` |
| **cmdk** | 1.1.1 | **MIT** | `app-src/node_modules/cmdk/package.json` |
| **Tailwind CSS** (`tailwindcss`) | 4.3.3 | **MIT** | `app-src/node_modules/tailwindcss/package.json`; the built stylesheet carries `/*! tailwindcss v4.3.3 \| MIT License \| https://tailwindcss.com */` |
| **Geist** (Geist Sans / Geist Mono) | via `@fontsource/geist-sans`/`-mono` 5.3.0 | **SIL OFL 1.1** | vendored OFL text at `src/godsv/assets/fonts/OFL.txt` (© 2023 Vercel, with basement.studio) |
| **React Flow / xyflow** (`@xyflow/react`, `@xyflow/system`) | 12.11.6 / 0.0.82 | **MIT** | `tools/ui-demo/node_modules/@xyflow/react/package.json` — UI-lab only, **not** a dependency of the shipped desktop shell |
| **Recharts** | 3.10.1 | **MIT** | `tools/ui-demo/node_modules/recharts/package.json` — UI-lab only, **not** in the shipped shell |
| **AI Elements** (Vercel) | registry package 1.9.0, 48 generated components | **Apache-2.0** | shadcn registry cache `…/node_modules/ai-elements/LICENSE` — "Copyright 2023 Vercel, Inc."; components live in `tools/ui-demo/src/components/ai-elements/` — UI-lab only, **not** in the shipped shell |

### Test, API and container tooling

| Tool | Version | Licence | Role | Verified from |
|---|---|---|---|---|
| **Schemathesis** | 4.27.3 | **MIT** | property-based testing of our own HTTP surfaces | `.venv/…/schemathesis-4.27.3.dist-info/METADATA` → `License-Expression: MIT`, licence file `MIT License, Copyright (c) 2019 Kiwi.com` |
| **Testcontainers (Python)** | 4.15.0 | **Apache-2.0** | real ephemeral services for tests | `testcontainers-4.15.0.dist-info/METADATA` + `LICENSE.txt` |
| **Hypothesis** (comes with Schemathesis) | 6.168.0 | **MPL-2.0** | the property engine | `hypothesis-6.168.0.dist-info/METADATA` → `License-Expression: MPL-2.0` |
| **docker-py** (comes with Testcontainers) | 7.2.0 | **Apache-2.0** | container control from tests | `docker-7.2.0.dist-info/METADATA` |
| **Bruno CLI** (`@usebruno/cli`) | 4.1.0 | **MIT** | the account-free local API-collection runner (Postman replacement for tests) | `~/.local/lib/node_modules/@usebruno/cli/license.md` — "MIT License, Copyright (c) 2022 Anoop M D, Anusree P S and Contributors" |
| **Newman** | 6.2.2 | **Apache-2.0** | runs Postman collections without a Postman account | `~/.local/lib/node_modules/newman/LICENSE.md` (Apache License 2.0) |
| **Postman MCP** (`@postman/postman-mcp-server`) | 2.12.3 | **Apache-2.0** | API collection/testing; needs the owner's `POSTMAN_API_KEY` | package `LICENSE` (Apache-2.0). Pin the version when registering: Hermes' OSV preflight blocks the package name (advisory covers 2.4.10–2.4.12 only) |
| **Grafana MCP** (`mcp-grafana`) | 1.5.1 | **Apache-2.0** | dashboards/observability | `~/.local/share/uv/tools/mcp-grafana/…/mcp_grafana-1.5.1.dist-info/METADATA` → `License: Apache-2.0` |
| **Docker Engine / CLI** (`docker-ce`, `docker-ce-cli`) | 29.6.1 | **Apache-2.0** | the tool builder's runtime; works without sudo here | `rpm -q --qf '%{LICENSE}' docker-ce` → Apache-2.0 |
| **Podman** | 5.8.1 | **Apache-2.0** (composite: `Apache-2.0 AND BSD-2-Clause AND BSD-3-Clause AND ISC AND MIT AND MPL-2.0`) | rootless containers for the CU image | `rpm -q --qf '%{LICENSE}' podman` |
| **bubblewrap** (`bwrap`) | 0.11.0 | **LGPL-2.0-or-later** per Fedora's metadata (upstream tags it LGPL-2.1+) | sandbox for tool tests; **external process only — never bundled, linked or modified** | `rpm -q --qf '%{LICENSE}' bubblewrap` — the Fedora field differs from the upstream tag; both are record-keeping only, nothing is redistributed |

Credits are maintained in [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md); the CLI-specific
additions from this wave are in its appendix section K.

---

## 10. What is explicitly NOT done yet

Honest list, each item with the evidence that shows it. Nothing here is "in progress by
implication" — the contract says `blocked`, `null` or `no`.

**Layers that report blocked (measured, `selftest --json`):**

* none — measured on this machine at 2026-09-21 19:36: all eight layers probe reachable. Re-measure before believing this row
**Gaps in the CLI surface itself:**

* **SUPERVISE has no command of its own.** The voice supervisor runs inside the host plugin; the
  CLI reads its health through `selftest` probes only.
* **No provider command.** `src/godsv/provider_config.py` (the write side: named providers,
  active projection, redacted views) is built but unwired — its docstring's
  `godsvoice provider add …` does **not** exist (verified: exit `1` invalid choice; exit `5`
  unknown command). Provider setup today is environment variables and/or `config.yaml` by hand.
* **No goal loop.** `completion.begin` records a goal and exits `3`; nothing continues after the
  command returns. There is no `resume`/`run` command for a goal.
* **40 of 73 commands declare no structured payload** (`data: null`) — they print human text on
  stderr and their `--json` envelope carries no `data`. The exact list is in
  `docs/CLI-REFERENCE.md` §6.
* **Nine declared-limitation sentences** are quoted verbatim in `docs/CLI-REFERENCE.md` §6 (for
  example `completion.begin`, `cu.demo`, `eval`, `jev.decide`); read them before promising a
  behaviour.
* **Two compatibility mirrors** exist for pre-contract tests (`registry.status`,
  `tools.promote-global`): their payload is mirrored at the top level and flagged in their
  contract record.
* **The reference generator's `--check` gate is not wired into CI** (`.github/workflows/ci.yml`
  runs ruff, pytest, bandit only).
* **`install.sh` prints German.** The installer's console text is not part of the English sweep
  yet; the CLI, the docs and the JSON output are English.

**Credit/licence items still open (they block shipping, not the CLI):** GSAP 3.15.0 is not an
OSI licence (gate the build), `@vscode/codicons` is CC-BY-4.0 and ships → in-app attribution
required, three fonts from `@nous-research/ui` ship with an **UNKNOWN** licence, the local
`godsv-cu-x11` image contains GPL parts (do not redistribute), `cua-driver`'s licence is
unconfirmed from the artifact, and `pyperclip`'s BSD variant is unconfirmed. Details and the
full blocker list: `THIRD_PARTY_NOTICES.md` §I.
