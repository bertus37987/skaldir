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

**Read this first, it is the honest part:** of the eight layers, **all eight are reachable today**,
measured with `godsvoice selftest --json` (exit 0, 26 probes). Section 4
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
godsvoice contract list    # all 73 leaf commands with layer, --json, read/write
```

Measured on this machine (2026-09-18, `GODSV_HOME=/home/willy/.hermes/godsv`):

| Command | Result |
|---|---|
| `godsvoice init` | exit 0 — wrote `pact.yaml` (419 B) + `ledger.yaml` (402 B) |
| `godsvoice doctor` | exit 0 — 10/10 checks ok (measured with `GODSV_HOME=/home/willy/.hermes/godsv` and `HERMES_HOME=/home/willy/.hermes`) |
| `godsvoice demo` | exit 0 |
| `godsvoice selftest --json` | exit **0** — all eight layers reachable (26 probes, see §4) |
| `godsvoice completion begin --goal "…"` | exit **3** — the goal is recorded, nothing continues (no execution loop is wired) |

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
| **EXPAND** — self-expanding tool system | **reachable** | `registry.migrate`, `registry.status`, `tools-hub`, `tools.approve`, `tools.build`, `tools.builds`, `tools.candidates`, `tools.fulfill`, `tools.list`, `tools.mine`, `tools.promote`, `tools.promote-global`, `tools.propose`, `tools.request`, `tools.revoke`, `tools.scan`, `tools.show` | the registry, the tools DB and the health states resolve |
| **ACT** — computer use | **reachable** | `act.check`, `act.run`, `act.subtool …`, `cu.check`, `cu.demo` | the isolated bridges (container display, bwrap sandbox, an owned host Xvfb) drive real windows/clicks/keystrokes; the *live desktop* (the user's own session) is refused by policy unless the opt-in (`--allow-live-desktop --live-target <window>`, or `SKALDIR_CU_ALLOW_LIVE_DESKTOP=1` + `SKALDIR_CU_LIVE_TARGET`) is set. `act check` reports the verdict `isolated-only`; `cu.demo` is an in-process fixture and declares `data.scope: "in-process fixture"` plus a warning |
| **COMPOSE** — learned composite tools | `blocked` (exit 3) | `skills.demote`, `skills.export`, `skills.list`, `skills.mine`, `skills.promote`, `skills.show`, `skills.validate`, `tools.compose` | "no composite can be created, validated or run from the CLI yet — `tools compose` only lists candidates" |
| **ROUTE** — Jev decision layer | `blocked` (exit 3) | `jev.decide`, `jev.history`, `jev.kinds`, `jev.packs`, `jev.selftest` | "System-One is off … (enabled=false) service disabled: no provider adapter is wired (src/godsv/llm.py)" |
| **SUPERVISE** — voice supervisor | **reachable**, but **no command of its own** | — (probes only: `voice.read_trajectory_health`, `guard`, `pause_store`) | the supervisor runs inside the host plugin; the CLI can read its health, not drive it |
| **ENFORCE** — goal-locked execution | **reachable** | `completion.begin`, `completion.status`, `completion.history`, `completion.override` | `completion.begin` records the goal and exits `3` while no loop is wired: the goal exists, nothing continues |
| **VERIFY** — real-world verification | `blocked` (exit 3) | `eval` | "no verification command runs a check against the real workspace yet — `eval` is in-process only and cannot certify a goal" |
| **PERSIST** — long-run persistence | `blocked` (exit 3) | `memory-audit`, `memory-retrieval-check`, `memory-search`, `memory-stats`, `metrics`, `nightshift` | "no run store at …/runs.db — no run has ever been started (no long-run record exists)"; events + memory themselves are reachable |

Re-measure any time:

```bash
godsvoice selftest --json | python3 -c "import json,sys
for l in json.load(sys.stdin)['data']['layers']:
    print(f\"{l['layer']:10} {l['status']:10} {l['blocked_reason'] or ''}\")"
```

```text
EXPAND     reachable
ACT        reachable
COMPOSE    reachable
ROUTE      reachable
SUPERVISE  reachable
ENFORCE    reachable
VERIFY     reachable
PERSIST    reachable
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

73 leaf commands in 14 groups (28 root groups; 87 parser nodes, 0 unregistered):

| Group | Layer | # | Access | Commands |
|---|---|---|---|---|
| `registry` | EXPAND | 2 | 1 read / 1 write | `registry.migrate`, `registry.status` |
| `tools` | COMPOSE, EXPAND | 15 | 6 read / 9 write | `tools.approve`, `tools.build`, `tools.builds`, `tools.candidates`, `tools.compose`, `tools.fulfill`, `tools.list`, `tools.mine`, `tools.promote`, `tools.promote-global`, `tools.propose`, `tools.request`, `tools.revoke`, `tools.scan`, `tools.show` |
| `cu` | ACT | 2 | 2 read | `cu.check`, `cu.demo` |
| `skills` | COMPOSE | 7 | 3 read / 4 write | `skills.demote`, `skills.export`, `skills.list`, `skills.mine`, `skills.promote`, `skills.show`, `skills.validate` |
| `jev` | ROUTE | 5 | 4 read / 1 write | `jev.decide`, `jev.history`, `jev.kinds`, `jev.packs`, `jev.selftest` |
| `completion` | ENFORCE | 4 | 2 read / 2 write | `completion.begin`, `completion.history`, `completion.override`, `completion.status` |
| `contract` | kernel | 3 | 3 read | `contract.dump`, `contract.list`, `contract.show` |
| `desktop` | kernel | 5 | 2 read / 3 write | `desktop.build-app`, `desktop.install`, `desktop.start`, `desktop.status`, `desktop.uninstall` |
| `mcp` | kernel | 1 | 1 read | `mcp.serve` |
| `prompts` | kernel | 1 | 1 read | `prompts.list` |
| `spend` | kernel | 6 | 2 read / 4 write | `spend.allowance`, `spend.approve`, `spend.complete`, `spend.deny`, `spend.list`, `spend.request` |
| `vault` | kernel | 4 | 2 read / 2 write | `vault.add`, `vault.describe`, `vault.list`, `vault.revoke` |
| `workflows` | kernel | 3 | 2 read / 1 write | `workflows.list`, `workflows.run`, `workflows.show` |
| `(top level)` | EXPAND, PERSIST, VERIFY, kernel | 15 | 12 read / 3 write | `demo`, `doctor`, `eval`, `init`, `ledger-show`, `memory-audit`, `memory-retrieval-check`, `memory-search`, `memory-stats`, `metrics`, `nightshift`, `pact-show`, `scan`, `selftest`, `tools-hub` |

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

**Layers that report blocked (measured, `selftest --json` exit 2):**

* **ACT live desktop** — the isolated bridges drive real windows today, but the *live desktop*
  (the user's own session) is refused by policy unless it is explicitly opted in and
  target-scoped (`--allow-live-desktop --live-target <window>`). `cu.demo` is an in-process
  fixture (it says so in `data.scope` and in a warning).
* **COMPOSE** — no composite tool can be created, validated or run from the CLI;
  `tools compose` only ranks candidates.
* **ROUTE** — Jev cannot decide: System-One is disabled in the runtime settings and no provider
  adapter is wired into the service. `godsvoice jev selftest` also exits `1` by design while a
  decision kind is unreachable.
* **VERIFY** — no command verifies against the real workspace. `eval` runs in-process episodes
  and its own note says it is "never a PASS authority".
* **PERSIST** — no run store (`runs.db`) has ever been written: no long-run record exists to
  resume from. Events and memory are reachable, the run lifecycle is not.

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
