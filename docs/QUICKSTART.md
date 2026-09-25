# Skaldir CLI — Quickstart

**Audience:** the operator who wants to connect a provider and use the CLI today.
**Version documented:** `skaldir 1.5.0` (the `godsvoice` spelling below is the deprecated alias for
the same entry point — every command still runs), contract `schema_version 1.0`.
**The counts and the taught surface were re-measured on 2026-09-19** (see §0); the step-by-step
captures below are the 2026-09-18 runs and are kept verbatim, with the stale spellings intact.
**The taught surface today is five entries** — the bare `skaldir` (the chat), `skaldir setup`,
`skaldir run "<goal>"`, `skaldir serve [--http]`, `--help`/`--version`; everything else is a `/` verb
inside the chat. The full table is in [`docs/SURFACE.md`](SURFACE.md).
**Everything below was executed on 2026-09-18 between 18:35 and 18:41 CEST** on this machine
(`/home/willy/projects/gods-voice`, branch `godsv-supervisor-hardening`, venv Python 3.12.13,
runtime `GODSV_HOME=/home/willy/.hermes/godsv`). Every output block is copied verbatim; nothing
in this file is planned behaviour presented as real.

**Legend used throughout:**

| Mark | Meaning |
|---|---|
| **WIRED** | the command exists in `godsvoice contract dump --json` today and was run for this document |
| **PLANNED** | the command is *not* in the contract dump — the code exists, the CLI does not accept it yet. Documented so you do not hunt for it. |

---

## 0. What works today, and what does not

| You want to … | Today |
|---|---|
| see whether the eight layers are reachable | **WIRED** — `godsvoice selftest --json` |
| see whether the install is healthy | **WIRED** — `godsvoice doctor --json` |
| make a real LLM decision (memory injection, routing, compaction, voice triage) | **WIRED** — `godsvoice jev decide … --provider llm_system_one_adapter` |
| point the CLI at your provider | **WIRED via environment/config file** (the adapter is real, see §3) |
| manage providers from the CLI (`provider add/test/…`) | **WIRED** — `skaldir provider list/add/test/remove/default/tiers/doctor` |
| roll a goal forward after a crash | **WIRED** — `runs status/start/resume/checkpoint` + `goal show/criteria` over the durable `runs.db`; the execution loop is `agent run` |
| register / roll back a container capability | **WIRED** — `tools mine` / `adapter show` / `register` / `health` / `rollback` |
| verify a goal against the real workspace | **WIRED** — `verify plan/run/status/evidence` (verdict `PASS`/`FAIL`/`UNKNOWN`; `UNKNOWN` exits 3) |
| resume a run from the run store | **WIRED** — `runs status/start/resume/checkpoint`, `goal show/criteria` |
| drive the real desktop | **WIRED for isolated bridges** — `act check/run`, `act subtool …`; a live desktop is refused by policy |
| serve HTTP / OpenAPI | **WIRED** — `serve`, `daemon serve/status` (the HTTP tier's live proof is still a shim, see the README) |

Counts measured from the contract dump on the tree above:

| Inventory | Count |
|---|---|
| root commands | **65** |
| leaf commands in `contract dump --json` (documented) | **267** |
| parser nodes | **321** |
| unregistered parser nodes | **0** |
| slash verbs in the chat (`skaldir chat verbs --json`) | **100** (87 real · 8 built-in · 5 planned · 0 not-wired) |
| roots that carry a `/` verb | **51 of 65** (241 of the 267 leaves reachable from the palette) |

> **Re-measured 2026-09-19** on this branch with the build in this checkout:
> `skaldir contract dump --json` → `data.{root_commands, leaf_commands, parser_nodes, unregistered}` =
> 65 / 267 / 321 / `[]`; `skaldir chat verbs --json` → 100 verbs. The numbers above this block
> (55 / 220) and in the paragraph below were the state on 2026-09-18 and are kept only as history.
> Re-run the two commands instead of quoting any number from any document — sibling blocks add
> commands while a doc is being written.

> **The taught surface is five entries** (`skaldir --help`, measured 2026-09-19): the bare call
> (the chat), `skaldir setup`, `skaldir run "<goal>"`, `skaldir serve [--http]`, and
> `--help`/`--version`. Everything else — `selftest`, `doctor`, `contract`, `jev`, `provider`,
> `runs`, `goal`, `tools`, `memory`, `verify`, … — is a `/` verb inside the chat (`/status`,
> `/doctor`, `/jev`, `/provider list`, `/tools`, …). The commands below still work exactly as
> written (the root commands are all still there); they are simply no longer *taught* at the top
> level. The whole surface, grouped, is in [`docs/SURFACE.md`](SURFACE.md).

> For *how to actually talk to it* — setup in detail, the ways in (one-shot `run`, `agent run`, the
> chat, the API), a full session capture, and the measured traps — see
> [`docs/CHATTING.md`](CHATTING.md). This file stays the provider-connect + first-real-run walkthrough.

> `docs/CLI-REFERENCE.md` is **regenerated** against this tree. It was written when the dump carried
> 220 commands; the tree carries 267 leaves today, so re-run
> `.venv/bin/python tools/gv_cli_reference.py --out docs/CLI-REFERENCE.md` (its own staleness gate is
> `.venv/bin/python tools/gv_cli_reference.py --check`) before quoting it as current.

---

## 1. Run it

The venv and the console script already exist in this checkout:

```bash
cd ~/projects/gods-voice
.venv/bin/godsvoice --version
```
```
godsvoice 1.5.0
```

Pick the runtime explicitly. **The most common support case is a stray `HERMES_HOME`/`GODSV_HOME`
in the shell** (a test or a wave leaves one behind) — the CLI then measures a throwaway runtime and
invents missing databases. Always say where you want to look:

```bash
export GODSV_HOME=/home/willy/.hermes/godsv          # the real runtime
.venv/bin/godsvoice registry status
```
```
runtime:    /home/willy/.hermes/godsv
tools db:   /home/willy/.hermes/godsv/tools.db (exists, 0 spec(s))
selection:  runtime default ($GODSV_HOME)
generated:  /home/willy/.hermes/godsv/generated_tools (0 tool(s))
project:    remote:github.com/bertus37987/gods-voice
legacy:     /home/willy/projects/gods-voice/.godsv/tools.db — not present
```

Runtime resolution order (one rule, project-wide): `$GODSV_HOME` → `$HERMES_HOME/godsv` →
`~/.hermes/godsv`.

**Fresh-install path** (from `docs/DEV.md`; not re-run here — reinstalling would re-point the shared
editable install that concurrent work in this repo is using):

```bash
uv venv .venv --python 3.11 && source .venv/bin/activate
pip install -e ".[dev]"
godsvoice init        # writes .godsv/pact.yaml + .godsv/ledger.yaml
```

`godsvoice init` in an empty directory was run for this document:

```
$ godsvoice init
created .godsv/pact.yaml
created .godsv/ledger.yaml
```

**Before you believe any run, check which source tree you are actually executing.** This venv uses an
editable install (`.venv/lib/python3.12/site-packages/_editable_impl_godsv.pth`) that a parallel
block can re-point at its own scratch copy. Measured during this documentation run: between 18:35
and 18:39 the same `.venv/bin/godsvoice` imported `/home/willy/gv-mcp-wiring/src/godsv/__init__.py`
instead of the repo tree, and `doctor` then died with
`FileNotFoundError: prompts directory not found: /home/willy/gv-mcp-wiring/prompts`.

```bash
.venv/bin/python -c "import godsv; print(godsv.__file__)"
# expect: /home/willy/projects/gods-voice/src/godsv/__init__.py
```
If it points somewhere else, prefix the run with the real tree:
`PYTHONPATH=/home/willy/projects/gods-voice/src .venv/bin/godsvoice …`

---

## 2. The five commands that earn their keep

### 2.1 `godsvoice selftest` — are the eight layers reachable? (exit 2 if not)

```bash
godsvoice selftest --json          # machine-readable; every layer carries a blocked_reason
```
Text output (real run, 2026-09-18 18:39, `GODSV_HOME=/home/willy/.hermes/godsv`, exit **2**). The
eleven layer lines and the closing summary are verbatim; only the per-layer probe detail lines after
EXPAND are elided for width — run the command, or `selftest --json`, for the full block; the complete
verbatim block is `docs/RUNBOOK.md` §10 Appendix A:

```
✓ EXPAND
    ✓ registry.resolve: runtime=/home/willy/.hermes/godsv tools_db=/home/willy/.hermes/godsv/tools.db
    ✓ capability.load_tool_entries: 0 generated tool(s) with manifest facts
    ✓ capability_health.all_states: 0 tool(s) with a health state
    ✓ tools_db: tools db present
✗ ACT — no live desktop bridge is wired to the CLI — no command drives a real window/click/keystroke yet (only in-process runners are constructible)
✗ COMPOSE — no composite can be created, validated or run from the CLI yet — `tools compose` only lists candidates
✗ ROUTE — probe failed
✓ SUPERVISE
✓ ENFORCE
✗ VERIFY — no verification command runs a check against the real workspace yet — `eval` is in-process only and cannot certify a goal
✗ PERSIST — no run store at /home/willy/.hermes/godsv/runs.db — no run has ever been started (no long-run record exists)
8 layer(s), 22 probe(s), 5 not reachable · 51ms
```

A layer that is not wired reports `ok: false` + `blocked_reason` — **never** `reachable`. Read the
blocked reasons (full table in `docs/RUNBOOK.md` §2) instead of guessing.

### 2.2 `godsvoice doctor` — is the install healthy?

Run it with a clean environment (see the `HERMES_HOME` trap in §1). Real run, exit **0**:

```
✓ Python ≥ 3.11 — 3.12.13
✓ godsv package — 1.5.0
✓ Prompts (13 required) — 13 loaded
✓ Hermes CLI in PATH — /home/willy/.local/bin/hermes
✓ Plugin deployed — /home/willy/.hermes/plugins/godsv
✓ Desktop entry
✓ Runtime — /home/willy/.hermes/godsv
✓ bwrap — /usr/bin/bwrap
✓ podman — /usr/bin/podman
✓ convert — /usr/bin/convert

Deployment OK.
```

### 2.3 `godsvoice jev decide` — one real decision through your provider

This is the command that proves "providers connected and it works". With a provider configured
(§3), a real call was made and returned a real advisory in **1.25 s**:

```bash
export GODSV_HOME=/home/willy/.hermes/godsv
godsvoice jev decide --kind memory_injection \
  --request /home/willy/gv-docs-scratch/jev_memory_request.json \
  --provider llm_system_one_adapter --dry-run --json
```
```
exit 0 · status ok · provider_mode llm_system_one_adapter · latency_ms 1256 · confidence 0.8
answer.chosen = "inject"   answer.injected = ["incident#812"]
usage {"prompt_chars": 1844, "response_chars": 448, "tier": "mid", "client_attempts": 1, "sdk_retries": 0}
```

`--dry-run` keeps the decision out of the ledger. The same command with `--provider disabled`
(the default) is honest about it — exit **3**, `status: "blocked"`, `refusal_reason:
"provider_disabled"`, and a named fallback:
`"keep the deterministic retrieval order and the existing token budget"`.

`--kind` accepts `memory_injection`, `tool_routing`, `capability_ranking`, `context_compaction`,
`voice_triage`, `adversarial_test_planning`. `jev decide` never certifies a goal and never grants a
permission — it is an advisor.

### 2.4 `godsvoice contract dump --json` — the CLI describing itself

The source of truth for every command, argument and JSON schema (and what the desktop renders forms
from). Real run: `exit 0`, `28 root`, `77 leaf`, `92 parser nodes`, `"unregistered": []`.

```bash
godsvoice contract list                                  # names only
godsvoice contract show jev.decide --json                # one command's args + schema + limits
godsvoice contract dump --json > /tmp/contract.json      # the whole surface
```

### 2.5 `godsvoice completion status` — what goal is the runtime tracking?

```
$ godsvoice completion status
state:    WORKING
goal:     (none)
last:     -
metrics:  requests=0 rejections=0 verified=0 false_completion_prevented=0
```

This is the record that survives a crash (see `docs/RUNBOOK.md` §4). Field meaning and the
`verified`-vs-`last_verdict` trap are in the runbook — read it before you trust the line.

**Honourable mentions** (all WIRED, all cheap): `godsvoice tools list`, `tools health <name>`,
`registry status`, `memory-stats`, `metrics`, `scan FILE`, `spend allowance`, `vault list`,
`jev selftest --json`, `jev kinds --json`, `workflows list`.

---

## 3. Provider setup — the real contract

### 3.1 What is wired today

The provider adapter **is real and reachable from the CLI today**: `godsvoice jev decide --provider
llm_system_one_adapter` calls `godsv.llm_provider.provider_for()` (src/godsv/llm_provider.py) and makes
a live HTTPS call. That was measured (2.3). Settings resolve **field by field, highest precedence
first**:

1. **explicit** — the `config` argument of `provider_for()` / `resolve_provider_config()`;
2. **environment** — the variables below;
3. **config file** — the `godsv.llm` block of YAML: `$GODSV_LLM_CONFIG` if set, else
   `<runtime_dir>/config.yaml` (runtime = `$GODSV_HOME` → `$HERMES_HOME/godsv` → `~/.hermes/godsv`);
   a top-level `llm:` block and a host `~/.hermes/config.yaml` (pointed at via `$GODSV_LLM_CONFIG`)
   are accepted too;
4. **defaults** — the `DEFAULT_*` constants of `llm_provider.py`.

### 3.2 The environment contract (the fastest path)

```bash
export GODSV_LLM_BASE_URL=https://api.deepseek.com/v1   # any OpenAI-compatible endpoint
export GODSV_LLM_MODEL=deepseek-chat
export GODSV_LLM_API_KEY=sk-...                          # environment ONLY — never in a file
```

| config key (`godsv.llm`) | environment variable |
|---|---|
| `provider` | `GODSV_LLM_PROVIDER` (`openai-compatible` default, `scripted`, `none`) |
| `base_url` | `GODSV_LLM_BASE_URL` (fallbacks `GODSV_LLM_API_BASE`, `OPENAI_BASE_URL`) |
| `api_key` (env-only) | `GODSV_LLM_API_KEY` (fallbacks `OPENAI_API_KEY`, `GODSV_LLM_KEY`) |
| `api_key_env` (a *name*) | — |
| `model` | `GODSV_LLM_MODEL` (fallback `OPENAI_MODEL`) |
| `tiers.<tier>` | `GODSV_LLM_TIER_MODELS` (`"small=m1,main=m2"`) |
| `timeout_s` | `GODSV_LLM_TIMEOUT` (default 30) |
| `total_timeout_s` | `GODSV_LLM_TOTAL_TIMEOUT` (default `timeout_s * (max_retries+1)`) |
| `max_retries` | `GODSV_LLM_MAX_RETRIES` (default 2) |
| `backoff_s` | `GODSV_LLM_BACKOFF_S` (default 0.25) |
| `max_tokens` | `GODSV_LLM_MAX_TOKENS` (default 900) |
| `stream` | `GODSV_LLM_STREAM` (`0`/`1`) |
| — (file path) | `GODSV_LLM_CONFIG` |

`max_tokens_field`, `temperature`, `headers`, `extra_body` and `responses` are config-file/argument
only (no environment form).

### 3.3 The config-file form

`<runtime_dir>/config.yaml` — e.g. `~/.hermes/godsv/config.yaml`:

```yaml
godsv:
  llm:
    provider: openai-compatible
    base_url: http://127.0.0.1:8080/v1     # llama.cpp / vLLM / OpenRouter / OpenAI
    model: qwen2.5-7b-instruct
    api_key_env: GODSV_LLM_API_KEY         # the NAME of the variable, not the key
    tiers:
      small: qwen2.5-3b-instruct
      main: qwen2.5-72b-instruct
    timeout_s: 30
    max_retries: 2
    stream: false
```

**Secrets:** the key is read from the environment only. A literal `api_key:` / `key:` / `token:` /
`secret:` value inside the config file is **refused** (`ProviderConfigError`). The key is never
logged, never written and never rendered — `ProviderConfig` redacts it in `repr`, `provider_status()`
reports only `"set"`/`"unset"` plus the variable name, and provider error text never carries request
headers. Measured with a deliberately wrong key: the refusal names the variable and redacts the key —
```
provider refused the credential (HTTP 401, message: Authentication Fails, Your api key: ****-key is invalid) — set GODSV_…
```

### 3.4 Verify your provider in one command

```bash
godsvoice jev decide --kind memory_injection --request REQUEST.json \
  --provider llm_system_one_adapter --dry-run --json
```

| exit | `error.code` | what it means | what to do |
|---|---|---|---|
| `0` | — | a live answer came back | you are connected |
| `3` | `blocked` | `--provider disabled`, provider stalled, or the runtime has no wiring | pass `--provider llm_system_one_adapter`, check the `refusal_reason` |
| `4` | `needs_credentials` | the endpoint refused the credential (HTTP 401/403), or a remote endpoint's `api_key_env` is unset | export the variable named in the refusal, then re-run |
| `7` | `internal` | malformed request, kind mismatch, or a hard-rule violation | fix the request document |

Measured cases: **no** `GODSV_LLM_API_KEY` → exit **4** with
`refusal_reason: "provider refused the credential (HTTP 401, …) — set GODSV_LLM_API_KEY in the environ"`;
wrong key → exit **4**; correct key → exit **0** in 1.25 s.

### 3.5 Provider management from the CLI — **PLANNED**

`src/godsv/provider_cli.py` (read side: `src/godsv/provider_config.py`) implements seven verbs:

| PLANNED command | what it will do |
|---|---|
| `provider list` | configured providers, redacted (default marked) |
| `provider add` | name, kind, base_url, model, `api_key_env` NAME |
| `provider test` | one real call: latency, model echo, answer |
| `provider remove` | drop one provider (removing the default clears the active projection — loudly) |
| `provider default` | which provider the runtime uses (`godsv.llm`) |
| `provider tiers` | the small/mid/main → model mapping (show or set) |
| `provider doctor` | what is missing for each configured provider |

It is **not reachable today**. Measured (the two long lines are elided with `…` at the point shown;
the exit code and the `error.code` are exact):

```
$ godsvoice provider list --json
godsvoice: error: argument command: invalid choice: 'provider' (choose from jev, init, pact-show, …
{"schema_version": "1.0", "ok": false, "command": "provider", … "exit_code": 1, "error": {"code": "usage", …}}
EXIT=1
```

Until the group is wired (three lines in `cli.py` + seven `cli_contract.COMMANDS` entries, per its
own docstring), use §3.2/§3.3. The write side already exists and is defensive: it round-trips the
file, backs the previous file up to `<name>.pre-provider-<utc>.bak`, refuses a literal key, runs the
serialized subtree through `godsvoice scan`'s `secretscan` before writing, and uses temp-file +
`os.replace`.

---

## 4. Where to go next

- `docs/RUNBOOK.md` — per-layer what-to-run, blocked reasons, resume, rollback, container teardown,
  troubleshooting table.
- `docs/CLI-REFERENCE.md` — the full generated command reference (currently stale by 4 commands,
  see §0).
- `godsvoice contract show <path> --json` — the authoritative arguments and limits of any command
  in this document.
