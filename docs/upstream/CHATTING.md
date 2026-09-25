# Chatting with Skaldir

The one page you need to go from *installed* to *talking to it* — the two keys, the three ways in,
what a session actually looks like, and the eight traps that eat an afternoon.

**Measured on** `skaldir 1.5.0` (contract `schema_version 1.0`), **2026-09-18 23:41 – 2026-09-19 00:11
CEST**, on this machine: branch `godsv-supervisor-hardening`, HEAD `eff149d`. Two capture rounds are
folded in: runtime `SKALDIR_HOME=/home/willy/gv-fr/rt` (workspace `/home/willy/gv-fr/ws`) and the
throwaway runtimes `/home/willy/gv-c4/rt*` (workspaces `/home/willy/gv-c4/{ws,goalws}`). Every output
block is a raw capture — the capture file is named under each one. **The CLI grows while sibling blocks
work**: `contract dump --json` was `56 root / 222 leaf` at 23:43 and `60 root / 256 leaf / 0
unregistered` at 00:11. When a block in this document disagrees with your build, the build wins: re-run
the command.

Legend: **WIRED** = in `skaldir contract dump --json` today and executed for this document ·
**PLANNED** = the code or the design exists, the CLI does not accept it yet · **BROKEN** = it runs, but
its measured behaviour contradicts its own help text · **UNREGISTERED** = the parser accepts it and it
runs, but the command has no `contract dump` entry (today: `chat`, §2.3).

---

## 1. Setup — two keys, and no secret in a file

Nothing that talks to a model works before this. Everything else does (see §6, trap 1).

```bash
skaldir setup --catalog          # 5 providers, each row with the URL + default model PRE-FILLED
skaldir setup --llm-catalog deepseek
skaldir setup --state --format text   # what is already configured; writes nothing
skaldir setup --check                 # re-validate; exit 0 only when BOTH keys answer
```

There are **exactly two mandatory keys** and no third:

| role | what it is | the flags |
|---|---|---|
| **TypeSafe / Jev** | the endpoint the decision layer (ROUTE) evaluates through — always armed | `--jev-base-url` · `--jev-model` · `--jev-api-key-env` |
| **LLM** | the general model (planning, coding, answers) | `--llm-base-url` · `--llm-model` · `--llm-api-key-env`, or `--llm-catalog <id>` |

Pick the LLM provider from the catalog instead of spelling it out — the row carries the endpoint, so you
accept a URL you never retype (`gateway`, `catalog_listing()` in `src/skaldir/setup_flow.py`). Measured
(`skaldir setup --catalog`, 5 entries): `deepseek` (`https://api.deepseek.com/v1`, `deepseek-chat`,
`DEEPSEEK_API_KEY`) · `openrouter` (`https://openrouter.ai/api/v1`) · `openai`
(`https://api.openai.com/v1`, `gpt-4o-mini`) · `local` (`http://127.0.0.1:8080/v1`, no key) ·
`anthropic` (`https://api.anthropic.com/v1`). The `--llm-catalog` flag's own help names the first four.

**The key itself never goes through the CLI, and never into the config.** `skaldir setup` writes
`godsv.llm` / `godsv.jev` with `api_key_env: <NAME>` and nothing else; the value is read from the
environment:

```bash
export DEEPSEEK_API_KEY=…        # the variable the config names
```

Two measured behaviours around that rule:

* a config file that carries a **literal** `api_key:` value is refused, not rewritten —
  `skaldir provider list --json` → exit **3**, `error.code: blocked`,
  *"config file … carries a literal 'api_key:' value in godsv.llm — a config file is not a secret store."*
  (capture `/home/willy/gv-fr/cap-literal-key.out`).
* the flow has a **credential-file path** for a key handed over once: `<config dir>/.env`
  (mode `0600`, the file the config references; `SKALDIR_SETUP_LLM_API_KEY` /
  `SKALDIR_SETUP_JEV_API_KEY` are the value-carrying variables, `run_setup(key_values=…)` writes it).
  `skaldir setup --state --json` reports that file as `credentials_file` with
  `values: "redacted"` and the hint `set -a; . <runtime>/.env; set +a`. **Not re-verified live in this
  round** (no provider key was in this shell's environment): measured only the negative — an invalid
  key value exits **4** and writes **nothing at all** (no `config.yaml`, no `.env` — capture
  `/home/willy/gv-fr/setup-bogus.json`). The live two-key success is measured in
  `/home/willy/gv-c4/cap1-setup-text.txt` (sibling block, live keys):

```
-- keys ---------------------------------------------------
[ok  ] TypeSafe / Jev provider (the decision layer)
         model echo deepseek-flash · 793 ms · streaming yes · structured json_object
[ok  ] LLM provider (the general model)
         model echo deepseek-flash · 729 ms · streaming yes · structured json_object

-- verified with two real calls ---------------------------
completion:   ok (deepseek-flash)
jev decision: ok (ok · tool_routing · 6148 ms)

-- written ------------------------------------------------
path:         <runtime>/config.yaml (3136 bytes)
backup:       none (new file)
secret scan:  clean
verdict: configured and verified: one real decision through Jev, one real completion
```

And what a fresh install looks like **before** that (real, `skaldir setup --yes --format text`, exit **3**;
capture `/home/willy/gv-fr/setup-first-run.txt`):

```
skaldir setup (setup)
==========================================================
config file: /home/willy/gv-fr/rt/config.yaml

-- detected ------------------------------------------------
runtime dir:  /home/willy/gv-fr/rt (runtime default ($SKALDIR_HOME))
config:       absent
providers:    0 configured
tools:        0 registered (no tools.db yet)
docker:       available
system-one:   off (no settings.yaml)

-- keys ---------------------------------------------------
[FAIL] TypeSafe / Jev provider (the decision layer)
         not configured
[FAIL] LLM provider (the general model)
         not configured
verdict: not written — a mandatory key did not validate
next:    skaldir setup --jev-base-url <URL> --jev-model <MODEL> --jev-api-key-env <NAME> --llm-…
```

**Exit codes** (`skaldir setup --help` + measured): `0` both keys answer · `1` usage · `2` a key is
configured but does not work · `3` a mandatory key is missing · `4` a remote endpoint has no credential
(also: a handed-over key value that failed validation) · `7` internal · `8` no answer in time.
`--check` and `--state` write nothing. Human text needs `--format text`; without it you get JSON.

### 1.1 What the validation really is, and where the file goes

A real two-key first run (executed 2026-09-18 23:47 CEST into a fresh `SKALDIR_HOME=/home/willy/gv-c4/rt2`,
key read from `~/.hermes/.env`; capture `/home/willy/gv-c4/cap1-setup-text.txt`, exit **0**):

```
$ skaldir setup --llm-catalog deepseek --llm-api-key-env DEEPSEEK_API_KEY \
      --jev-base-url https://api.deepseek.com/v1 --jev-model deepseek-chat \
      --jev-api-key-env DEEPSEEK_API_KEY --yes --format text
skaldir setup (setup)
==========================================================
config file: /home/willy/gv-c4/rt2/config.yaml

-- detected ------------------------------------------------
runtime dir:  /home/willy/gv-c4/rt2 (runtime default ($SKALDIR_HOME))
config:       absent
providers:    0 configured
tools:        0 registered (no tools.db yet)
docker:       available
system-one:   off (no settings.yaml)

-- keys ---------------------------------------------------
[ok  ] TypeSafe / Jev provider (the decision layer)
         model echo deepseek-flash · 793 ms · streaming yes · structured json_object
[ok  ] LLM provider (the general model)
         model echo deepseek-flash · 729 ms · streaming yes · structured json_object

-- verified with two real calls ---------------------------
completion:   ok (deepseek-flash)
jev decision: ok (ok · tool_routing · 6148 ms)

-- written ------------------------------------------------
path:         /home/willy/gv-c4/rt2/config.yaml (3136 bytes)
backup:       none (new file)
secret scan:  clean
verdict: configured and verified: one real decision through Jev, one real completion
```

**What it validates** — not a ping. Per role it makes a real completion (`answer_preview: "pong"`, model
echo, latency) **plus** a streaming probe (`first_byte_ms`) **plus** a structured-output probe; when the
role is the decision layer it also runs one real Jev decision (`kind: tool_routing`, `question_pack_id:
routing`, confidence, latency). After writing it re-reads the file and re-verifies with one real Jev
decision and one real completion — hence `--no-streaming-probe` (one request fewer per key) and hence
~4 API calls for a first run.

**Where it writes**: `<runtime>/config.yaml`, atomically, with a backup of any existing file
(`backup: null` on a first run), `secret_scan: clean`, `top_level_keys: ["godsv"]` — the same file the
provider layer reads. Runtime resolution is one rule, `$SKALDIR_HOME` → `$HERMES_HOME/skaldir` →
`~/.hermes/skaldir`, and an existing pre-rename `~/.hermes/godsv` still wins; the report always prints
`runtime_reason` with the variable that decided it (here: `runtime default ($SKALDIR_HOME)`).

A second real check, later, against the same runtime (`skaldir setup --check`, exit **0**):

```
-- keys ---------------------------------------------------
[ok  ] TypeSafe / Jev provider (the decision layer)
         model echo deepseek-flash · 592 ms · streaming yes · structured json_object
[ok  ] LLM provider (the general model)
         model echo deepseek-flash · 720 ms · streaming yes · structured json_object
verdict: both mandatory keys answered a real call
```

### 1.2 What the CLI says about degraded mode

`setup` states the policy itself, verbatim, in its `data.degraded` block:

```json
"works_now": ["skaldir --version", "skaldir --help / the command tree",
              "skaldir contract dump --json",
              "skaldir provider list --json (the honest empty registry)",
              "skaldir setup --check (says exactly which key is missing)"],
"blocked_until_setup": ["skaldir agent run … (needs the LLM provider)",
                        "skaldir jev decide … (needs the TypeSafe/Jev provider)",
                        "any command with requires_credentials=true"]
```

### 1.3 Degraded mode, measured command by command

In a runtime with no `config.yaml` and no key in the environment
(`SKALDIR_HOME=/home/willy/gv-c4/rt-degraded`, capture `/home/willy/gv-c4/cap-degraded.txt`):

| Command | Result |
|---|---|
| `skaldir --version` | exit **0** — `skaldir 1.5.0` |
| `skaldir --help` / the command tree | exit **0** |
| `skaldir contract dump --json` | exit **0** — 60 root / 256 leaf / 307 nodes, 0 unregistered |
| `skaldir setup --check` | exit **3** — `a mandatory key is not configured — run \`skaldir setup\`` |
| `skaldir doctor`, `skaldir selftest --json` | exit **0**, with the unreachable layers named |

**Exit 4 is not the only wall, and it is not the one you would guess.** Measured one by one in that
runtime:

| Command (no key, no config) | exit | `error.code` | what it says |
|---|---|---|---|
| `skaldir agent run hello` | **4** | `needs_credentials` | `no LLM provider configured: set base_url + model (+ api_key for remote endpoints)` |
| `skaldir provider test` | 3 | `blocked` | `no provider configured (config file …/config.yaml) — add one, e.g. …` |
| `skaldir vision capabilities` | 3 | `blocked` | `the call could not be made — no LLM provider configured: …` |
| `skaldir verify run` | 3 | `blocked` | `no criteria to verify` |
| `skaldir setup --check` | 3 | `blocked` | a mandatory key is not configured |
| `skaldir jev decide --kind tool_routing --request '{}'` | 1 | `usage` | `USAGE` (the request document is checked before the key) |
| `skaldir vault list` | 1 | `usage` | `SKALDIR_VAULT_KEY not set (master secret — never put it in argv/history)` |

`agent.run` is the one that exits 4 without setup, **and it names the exact exports**:

```
$ skaldir agent run hello --json   # (no key configured)
exit_code 4  error.code needs_credentials
no LLM provider configured: set base_url + model (+ api_key for remote endpoints):
export SKALDIR_LLM_BASE_URL=https://api.openai.com/v1
export SKALDIR_LLM_MODEL=gpt-4o-mini
export SKALDIR_LLM_API_KEY=<your key>        # never commit this
hint: export SKALDIR_LLM_BASE_URL + SKALDIR_LLM_MODEL (+ SKALDIR_LLM_API_KEY), or run: skaldir provider add
```

Eleven contract entries declare `requires_credentials=true` — the set that *may* answer exit 4:
`setup`, `jev.decide`, `provider.test`, `vault.add|describe|list|revoke`,
`vision.capabilities|describe|ocr|screenshot`.

---

## 2. The three ways to talk to it

### 2.1 One shot — `skaldir run "<goal>"` (WIRED)

The goal-locked loop: plan → work through the agent loop with the workspace's own tools → run the
**declared** checks → the completion gate decides → keep working on failure. Exit **0 only on a VERIFY
PASS**.

```bash
skaldir run "make the test suite pass"                      # --max-turns 6, --max-steps 14 by default
skaldir run "add a /health route" --criteria criteria.json  # your own criteria document
skaldir run --resume <goal-id>                              # continue from the durable checkpoint
skaldir run "…" --max-turns 3 --wall-clock-s 600 --tier main
skaldir run "…" --json                                      # one envelope; the human lines move to stderr
```

Useful knobs (all in `skaldir run --help`): `--criteria`, `--workspace`, `--session`, `--max-turns`,
`--max-steps`, `--wall-clock-s`, `--check-timeout`, `--no-baseline`, `--no-snapshot`, `--context-budget`,
`--context-trigger`, `--preserve-recent`, `--compaction-asker`, `--tier`, `--model`, `--base-url`,
`--state`. With nothing configured it refuses honestly — exit **4**,
`needs_credentials` (capture `/home/willy/gv-fr/cap-agent-run.err`, same refusal from `agent run`).

Two gotchas of this verb, measured on the second round's tree
(`/home/willy/gv-c4/cap-run3.txt`, live tree 2026-09-19 00:02):

* **A `.godsv/verify.json` in the workspace is not picked up implicitly.**
  `skaldir run "…" --workspace <ws>` exits **1** with `a goal-locked run needs at least one criterion`
  although the file is right there; pass `--criteria <ws>/.godsv/verify.json` (its shape already *is* a
  criteria document) and the same plan is accepted (`source: "argument"`, `plan_file_reason: ""`).
* **In `--text` mode an internal failure prints nothing at all** — a bare `exit=7` and an empty
  transcript on both stdout and stderr; the same call with `--json` carries
  `error.code: internal` with the underlying `AttributeError` text. When a `run` looks silent, re-run it
  with `--json` before concluding anything.

A single question with tools but no goal contract is the other one-shot:

```bash
skaldir agent run "what does this repo do?" --max-steps 4 --tools builtin
skaldir agent transcript <run-id>          # read the run back: requests, answers, tool calls, digests
```

### 2.2 One shot, tool loop — `skaldir agent run "<task>" --max-steps 8` (WIRED)

The difference in one line: **`agent run` runs one bounded tool-calling task and reports what the model
answered — it does not verify anything and cannot declare a goal done.** A bound (steps, tokens, cost,
wall clock, cancel) ends it as `incomplete`/`blocked` with `answer: null`, never as a manufactured
success.

```
$ skaldir agent run "Read calc.py in this workspace, find the bug in add(), fix it with a patch, \
      and say in one sentence what was wrong." --workspace /home/willy/gv-c4/ws --tools builtin \
      --max-steps 8 --run-id c4-demo1
run c4-demo1 · answered
  steps=4 tool_calls=3 executed=3 duration_ms=3736
  usage={"completion_tokens": 296, "prompt_tokens": 3515, "total_tokens": 3811}
  answer:
    `add(a, b)` returned `a - b` instead of `a + b`, so it subtracted rather than added; I rewrote the
    file with the operator corrected to `+` (calc.py now reads `return a + b`, with `divide` unchanged).
    …
  transcript: /home/willy/gv-c4/rt2/agent/runs/c4-demo1.jsonl
```
exit **0**, 4.3 s wall (capture `/home/willy/gv-c4/cap2-agent.out`). The transcript is one JSONL line per
fact — every request body with its `tools` array, every answer with usage, every tool call with its
arguments/duration/result digest; read it back with `skaldir agent transcript c4-demo1` (**WIRED**).
With nothing configured this is the command that exits **4** and names the exports (§1.3).

One measured trap before you write task prompts: with `--tools builtin` the loop's tool set is
`read_text`, `list_dir`, `sha256_file`, `write_text` — **not** the `terminal` tool that
`skaldir tools-core list` shows. The model above handled it honestly ("I don't have a terminal tool
available … I used `list_dir` instead"), but a prompt that assumes `terminal` exists produces an
apologetic answer instead of an action. Use `--tools both` (or a registry tool set) when you want the
registry tools declared in the tool registry.

### 2.3 Interactive — the chat: bare `skaldir` (WIRED)

The taught entry point is the **bare call** (`skaldir --help` teaches five entries; see
[`docs/SURFACE.md`](SURFACE.md)). `skaldir chat` and `skaldir shell` are the same two-bar surface and
still work — they are simply no longer *taught* at the top level.

```bash
skaldir                       # the chat: session state, slash palette, completion, streaming
skaldir chat --resume         # the same surface, on the `chat` root: restore the last session
skaldir shell --json          # scripted mode: no prompt, no footer, no approval prompt
```

The measured defect that made the chat a *surface* and not a one-shot: a prose line used to be an
independent `agent run` with an empty `previous_transcript`, so nothing accumulated. `route_plain`
now hands the next turn the previous transcript (`--previous <path>`), and the frame shows it:
`⏺ skaldir agent run … --previous ~/.hermes/godsv/agent/runs/run-….jsonl`.

What starts is the **two-bar** bar plus the prompt (real capture, `NO_COLOR=1`,
`/home/willy/gv-fr/first-start-no-color-raw.txt`):

```
  Skaldir v1.5.0 - interactive shell
  runtime ~/gv-fr/rt
  ready: /help /status /quit /init /diff /provider /verify /act /tools
  flagged: /goal (PERSIST flagged by selftest), /runs (PERSIST flagged by selftest
  selftest: 8 layer(s), 4 not reachable
  /help the palette | Tab completes | ? help | Esc dismisses | Ctrl-C cancels a command | /quit leaves

~/gv-fr/ws | goal:none | provider:none
/init writes AGENTS.md | 0 commands | 0 failed
skaldir >
```

* **bar 1** is live state: `workspace · goal:… · provider:…` (clipped to 80 columns by dropping the least
  relevant fact, re-printed only when it changed);
* **bar 2** is what this session did: `/init writes AGENTS.md · N commands · M failed` (and `N approvals`
  once you answered one);
* on a colour terminal the separators are `·`, and the input line is the **bar's own composer row** —
  `  ❯ <what you type>` between the two rules. The bare `skaldir ❯` prompt is painted nowhere there
  (real 80×24 colour capture: `outputs/chat-ux/c1-first-paint-80x24.raw`; asserted by
  `tests/test_cli_feel.py`);
* `skaldir ❯ ` is the **fallback** prompt, and it is what you get when the bar is out of the picture:
  `--frame off` or `SKALDIR_CHATBAR=0` on a colour terminal (measured). A pipe gets the ASCII form
  above, `skaldir > ` (`outputs/chat-ux/c7-pipe-clean.raw`);
* `NO_COLOR=1` / `--no-color` drops the paint, not the frame: the rules and the composer row stay, the
  separators become `|` (measured) — colour is never the information.

The same screen as a real colour PTY capture (120 columns, `--runtime ~/gv-c4/rt2`, capture
`/home/willy/gv-c4/log-t2.txt`):

```
  Skaldir v1.5.0 — interactive shell
  runtime ~/gv-c4/rt2
  ready: /help /status /quit /init /diff /provider /approve /
  flagged: /undo (PERSIST flagged by selftest), /compact (ROUTE
  selftest: 8 layer(s), 4 not reachable
  /help the palette · Tab completes · ? help · Esc dismisses · Ctrl-C cancels a command · /quit leaves

~/gv-c4/ws · goal:none · provider:llm (not ready)
/init writes AGENTS.md · 0 commands · 0 failed
skaldir ❯ 
```

and the `?` overlay from the same session, verbatim — the layer line is the real selftest answer:

```
? help
  runtime   ~/gv-c4/rt2 — /home/willy/gv-c4/rt2
  goal      none — no run store at ~/gv-c4/rt2/runs.db
  provider  llm (not ready) — llm · openai-compatible
  warm      off — no socket at /run/user/1000/skaldir/warm.sock
  layers   reachable: EXPAND, ACT, ENFORCE, VERIFY
           not reachable: COMPOSE, ROUTE, SUPERVISE, PERSIST
```

Keys and lines: **Tab** completes commands/options/values and the `/` palette · **`?`** opens the inline
overlay (runtime, goal, provider, layer reachability) and `? <command>` shows that command's own help ·
**Esc** dismisses the palette · **Ctrl-C** cancels the running command and keeps the session ·
**Ctrl-D** or `/quit` leaves. A line starting with `/` is a slash command (§5). **Any other line is the
`skaldir` argv it looks like** — `tools list` runs `skaldir tools list`, and `?` says so verbatim: *any
line without a leading `/` runs as `skaldir <line>`*. That is why a plain sentence typed into the shell is
an unknown command, not a model call: use `agent run "…"` (or `run "…"`) for a one-shot question.
**`skaldir chat` is the exception** — there a line without a leading `/` is prose and goes straight to the
agent loop (below). History lives in `<runtime>/shell/history.jsonl`.

#### `skaldir chat` — the chat bar, measured (reachable; **not** in the contract dump yet)

`chat` did not exist at 23:54 (`skaldir chat --json` → exit **1**, *invalid choice*, capture
`/home/willy/gv-fr/cap-chat.out`) and **was added while this document was being written**. Measured at
00:18 CEST on the live tree:

```
$ skaldir chat --help
usage: skaldir chat [-h] [--frame {auto,on,off}] [--width WIDTH] [--runtime RUNTIME] [--resume]
                    [--limit LIMIT] [--transcript TRANSCRIPT] [--color] [--no-color] [--no-probe]
                    [--no-activity] [--session SESSION] [--json] …

The Skaldir chat bar. The input line is framed by two full-width bars; the palette opens on `/` and is
filtered as you type (Tab completes, Enter runs, Esc dismisses); the row under the bars carries the
model, the context, the speed and the clock on the left and the durable goal plus /help on the right.
A line without a leading / is prose and goes to the agent loop.
```

It is the closest thing to a chat UI in this build, and it is honest about what it cannot measure. Real
PTY capture (`/home/willy/gv-c4/log-chat2.txt`, 120 columns, live tree 00:18):

```
  ready: /help /status /verify /tools /model /provider /appro
  planned (not built): /plan
  flagged: /goal (PERSIST flagged by selftest), /runs (PERSIST
  selftest: 8 layer(s), 4 not reachable
  /help the palette · Tab completes · ? help · Esc dismisses · Ctrl-C cancels a command · /quit leaves

~/gv-c4/ws · goal:cli:gcba WORKING · provider:llm (not ready)
last edit 20260918-235349… · /diff shows it · 0 commands · 0 failed
  ────────────────────────────────────────────────────────────────────────────────────────────────

  ❯

  model deepseek-chat · ctx n/a · tok/s n/a · 00:00            goal: cli:gcba WORKING · /help
  ────────────────────────────────────────────────────────────────────────────────────────────────
```

A **prose line goes to the agent loop** (no `/`), and the row under the bars is the live footer the
question "what does a session look like" is really asking about. Typing
`Reply with exactly the word pong and nothing else.` and pressing Enter:

```
  ❯ Reply with exactly the word pong and nothing else.
  model deepseek-chat · ctx n/a · tok/s n/a · 00:00            goal: cli:gcba WORKING · /helpworking…
  model deepseek-chat · ctx n/a · tok/s n/a · 00:00            goal: cli:gcba WORKING · /help⏺ skaldir agent run Reply with exactly the word pong and nothing else.
  model deepseek-chat · ctx n/a · tok/s n/a · 00:00            goal: cli:gcba WORKING · /help  ›  working · agent run · "Reply with exactly the word pong and nothi…
  model deepseek-chat · ctx n/a · ~606 tok/s · 00:00           goal: cli:gcba WORKING · /helpp
  model deepseek-chat · ctx n/a · ~59 tok/s · 00:00            goal: cli:gcba WORKING · /helppong
  model deepseek-chat · ctx n/a · ~56 tok/s · 00:00            goal: cli:gcba WORKING · /helppong
run run-20260918T221748Z-f68ab1 · answered
  model deepseek-chat · ctx n/a · ~281 tok/s · 00:00           goal: cli:gcba WORKING · /help  ⎿ exit 0 (OK) · first output at 0 ms · 805 ms · 798 B streamed
  model deepseek-chat · ctx n/a · ~281 tok/s · 00:00           goal: cli:gcba WORKING · /help
```

Read the row: `model deepseek-chat` · `ctx n/a` · `~281 tok/s` · `00:00`, then the right-hand half
`goal: cli:gcba WORKING · /help`. The speed is a **streaming estimate marked `~`** and shows `n/a` before
a turn; **`ctx` is `n/a` in this build** — the context percentage is not measured yet, and the bar says
so instead of printing `0%`. The turn itself is the familiar shape (`⏺` action line, `› working`
progress, streamed answer, `run … · answered`, `⎿ exit 0 (OK) · first output at 0 ms · 805 ms · 798 B
streamed`).

Honest caveat: `chat` prints in `contract dump --json`'s `unregistered` list — it is reachable in the
parser but has **no `cli_contract.COMMANDS` entry yet** (at 00:11 the same dump reported
`unregistered: []`). Treat it as the newest surface, not as a settled contract. `skaldir shell` remains
the settled one.

### 2.4 The API — `skaldir serve --http` (WIRED)

```bash
export SKALDIR_API_TOKEN=$(python -c "import secrets;print(secrets.token_urlsafe(32))")
skaldir serve --http --port 8731              # loopback only unless --allow-remote
curl -s http://127.0.0.1:8731/health
curl -s http://127.0.0.1:8731/cli/provider/list | head -c 200
curl -s -X POST -H "authorization: Bearer $SKALDIR_API_TOKEN" \
     -H 'content-type: application/json' \
     -d '{"task":"what does this repo do?","max_steps":4}' \
     http://127.0.0.1:8731/cli/agent/run
```

Real capture (this machine, 23:53; log `/home/willy/gv-fr/serve.log`):

```
$ skaldir serve --http --port 8731
skaldir: Skaldir API listening on http://127.0.0.1:8731 (v1.5.0, 253 command routes, 106 mutating)
skaldir: OpenAPI document http://127.0.0.1:8731/openapi.json (browser UI: http://127.0.0.1:8731/docs)
skaldir: read-only routes need no token; mutating routes need Authorization: Bearer ***
skaldir: API token loaded from SKALDIR_API_TOKEN (not printed)
skaldir: transport=auto (in-process allowlist 26 commands, warm session not running), SSE sse-1

$ curl -s http://127.0.0.1:8731/health
{"schema_version":"1.0","ok":true,"command":"api.health","exit_code":0,
 "data":{"status":"ok","routes":253,"mutating_routes":106,"token_required_for_mutations":true,
 "http_status_by_exit":{"0":200,"1":400,"2":422,"3":409,"4":401,"5":404,"6":409,"7":500,"8":504}}}

$ curl -s http://127.0.0.1:8731/cli/doctor          # read-only: no token
{"ok":false,"command":"doctor","exit_code":2, …}
```

Rules worth knowing: every route is `/cli/<group>/<verb>` (the dotted contract path with dots → slashes),
**one route per CLI command** and the same exit codes — mapped to HTTP status by
`http_status_by_exit` (`2 → 422`, `3 → 409`, `4 → 401`, `5 → 404`, `8 → 504`). Read-only routes answer GET
and POST without a token; **mutating** routes are POST-only and need `Authorization: Bearer <token>`
(`x-godsv-token` also works; the token is never accepted in a query string). The request body carries the
command's arguments by their **dest** name (`{"task": …, "max_steps": 4}`), and only what you send is
passed — no default is replayed from the dump. Measured on the same run
(`/home/willy/gv-fr/post-no-token.json`, `post-with-token.json`):

```
POST /cli/agent/run            without a token   → 401  needs_credentials "agent.run changes state and needs the API token"
POST /cli/agent/run            with the token    → 401  exit_code 4 "no LLM provider configured …"   (honest, not a transport bug)
GET  /cli/agent/run            (mutating route)  → 400  "GET is not allowed on /cli/agent/run"
GET  /openapi.json                               → 200  openapi 3.1.0, one path per route
```

Streams are NDJSON by default and SSE (`begin`/`progress`/`end`, heartbeat 10 s) with
`Accept: text/event-stream`. Six commands have no route and say why in `x-godsv-excluded`
(`serve`, `daemon.*`, `mcp.serve`).

**A second, independent API round** (2026-09-18 23:56, `serve --http --port 8796 --token-file …
--workdir … --transport inprocess`; openapi 3.1.0, 253 routes, `x-godsv-token-required: true`;
captures `/home/willy/gv-c4/{cap-api-ab.txt,cap-api-c.txt,sse-raw2.txt,openapi-mine.json}`) adds the two
mechanics you actually type:

* **The token from a file** — `--token-file <f>` reads it (0600 recommended) and the banner says
  `API token loaded from --token-file (not printed)`. Either header names it: the 401's own hint is
  ``send Authorization: Bearer <token> (the token the server printed once on start)``, while the code
  constant is `x-godsv-token`. Both measured: `-H "x-godsv-token: $TOKEN"` → **200**;
  `-H "Authorization: Bearer $TOKEN"` → authenticates (the call then failed on its own arguments with
  **400 `usage`**, not on auth). The refusal, verbatim:

```
$ curl -sS -i -X POST http://127.0.0.1:8796/cli/runs/start \
      -H 'content-type: application/json' -d '{"goal":"demo goal"}'
HTTP/1.1 401 Unauthorized
x-godsv-command: runs.start
x-godsv-exit-code: 4
www-authenticate: Bearer

{"ok":false,"exit_code":4,
 "error":{"code":"needs_credentials",
          "message":"runs.start changes state and needs the API token",
          "hint":"send Authorization: Bearer <token> (the token the server printed once on start)",
          "details":{"reason":"missing_token","mutates":true,"auth":"bearer"}}}
```

* **A streamed run** — `Accept: text/event-stream` (also `?sse=1`, or `{"_sse": true}` in the body)
  returns the `sse-1` contract with `id:`/`retry:` frames and `: keep-alive <iso>` comments:

```
$ curl -sS -N -X POST http://127.0.0.1:8796/cli/agent/run \
      -H 'content-type: application/json' -H "x-godsv-token: $TOKEN" \
      -H 'Accept: text/event-stream' \
      -d '{"task":"Reply with exactly the word pong and nothing else.","max_steps":2,"tools":"builtin","run_id":"c4-sse-2"}'
id: 0
retry: 3000
event: begin
data: {"stream_contract": "sse-1", "stream_id": "agent.run-1789768606999", "command": "agent.run",
       "route": "/cli/agent/run", "transport": "spawn",
       "transport_note": "streaming runs in a child process: it needs bytes as they are produced",
       "heartbeat_s": 10.0, "events": ["begin", "progress", "end"], …}

id: 1
event: progress
data: { … "exit_code": 0, "data": {"run_id": "c4-sse-2", "status": "answered", "steps": 1,
       "duration_ms": 772, "usage": {"prompt_tokens": 636, "completion_tokens": 2, "total_tokens": 638},
       "transcript_path": "…/agent/runs/c4-sse-2.jsonl", …}}

id: 2
event: end
data: { …the same final envelope… }
```
exit 0. Note `"transport": "spawn"` — a streamed run goes through a child process that **inherits the
server's environment**: a server started with a stale/garbage key *streams a credential failure* instead
of an answer, measured as `provider refused the credential (HTTP 401, … Your api key: x is invalid)` in
the `progress` and `end` payloads. Restart the server after you fix the key — the config stores the
variable *name*, so the process is what must carry the value.

---

## 3. What a session looks like

**Two turns with a tool call** — the real PTY session (`skaldir shell --color`, 120 columns,
`--runtime ~/gv-c4/rt2`, cwd `/home/willy/gv-c4/ws`; whole log `/home/willy/gv-c4/log-t2.txt`, ANSI
stripped, the long lines the terminal rewrapped are joined below). Turn 1 — the step line says what runs,
the progress lines say what the tools did, the result line closes the turn:

```
skaldir ❯ agent run "Use the terminal tool to list the files in this workspace, then write a file
NOTES.md listing them with one short sentence about the workspace." --workspace /home/willy/gv-c4/ws
--tools builtin --max-steps 8
⏺ skaldir agent run Use the terminal tool to list the files … --max-steps 8

✳ running skaldir agent run Use the terminal tool to list the files in t… (0.0s)
  ›  working · agent run · "Use the terminal tool to list the files in this workspace, then write a file NO…"
I don't have a terminal tool available — the tools I can actually call are `read_text`, `list_dir`,
`sha256_file`, and `write_text`. …
  ›  answer 1355 ms · 1 tool call · 289 chars · first token 0.77 s · 73 deltas · 273 chars · ~68 tok (est. chars/4) · +1 event: model call
  ›  answer 1002 ms · 1 tool call · 89 chars · first token 0.37 s · 19 deltas · 89 chars · ~22 tok (est. chars/4) · +3 events: list_dir, model call
  ›  answer 1499 ms · 980 chars · first token 0.57 s · 249 deltas · 892 chars · ~223 tok (est. chars/4) · +3 events: write_text, model call
  ✓  4 step(s) · 3 tool call(s) · 4562 ms
    ✓  streamed first token at 0.00 s · 372 deltas · ~368 tok (chars/4) · 26 updates (0.0/s) · 1472 chars · 1476 B written
run run-20260918T215342Z-b78c68 · answered
  steps=4 tool_calls=3 executed=3 duration_ms=4562
  usage={"completion_tokens": 611, "prompt_tokens": 3714, "total_tokens": 4325}
  answer: streamed above (980 chars, 372 deltas)
  transcript: /home/willy/gv-c4/rt2/agent/runs/run-20260918T215342Z-b78c68.jsonl
  ⎿ exit 0 (OK) · first output at 0 ms · 4605 ms · 2.8 kB streamed

skaldir ❯ 
```

Turn 2 — the follow-up in the same session; the model reads back the file it had just written:

```
skaldir ❯ agent run "Read NOTES.md and tell me how many lines it has." --workspace /home/willy/gv-c4/ws
--tools builtin --max-steps 6
⏺ skaldir agent run Read NOTES.md and tell me how many lines it has. --workspace /home/willy/gv-c4/ws --tools builtin --max-steps 6
✳ running skaldir agent run Read NOTES.md and tell me how many lines it… (0.0s)
I'll  ›  answer 811 ms · 1 tool call · 25 chars · first token 0.64 s · 7 deltas … · +1 event: model call
 read the file first.NOTES.md has **20 lines**. …
  ✓  2 step(s) · 1 tool call(s) · 2169 ms
run run-20260918T215346Z-66e8bb · answered
  steps=2 tool_calls=1 executed=1 duration_ms=2169
  usage={"completion_tokens": 163, "prompt_tokens": 1499, "total_tokens": 1662}
  ⎿ exit 0 (OK) · first output at 0 ms · 2266 ms · 1.5 kB streamed

skaldir ❯ 
```

A conversation is *repeated commands in one session* — the shell keeps the history, the runtime state and
the counters; the two `agent run` turns above are two independent bounded runs. The footer after an edit
links them: `last edit 20260918-235349… · /diff shows it · 3 commands · 0 failed`.

**The footer** under every turn — exit code, wall time, streamed bytes (real, from the shell capture):

```
  ⎿ exit 0 (OK) · 30 ms · 309 B streamed
  ⎿ exit 2 (VERIFICATION_FAILED) · first output at 0 ms · 325 ms · 4.6 kB streamed
```

`first output at N ms` is the time to the first byte the command produced — measured, and `n/a` when the
command produced nothing. A cancelled command gets its own line (`^C cancelled … · the session is still
here`) and is counted as cancelled, not failed.

**What the footer does *not* print — in the shell.** The `model · ctx 34% · 1.2k tok/s · 02:14` footer
that `src/skaldir/telemetry.py:footer_line` describes is **PLANNED in `skaldir shell`**: `skaldir
telemetry show|live|export` is wired as its own command group, but the shell does not render that line
yet (no `tok/s` or `ctx %` appears anywhere in `/home/willy/gv-c4/log-t2.txt`). The **chat bar does**
render it (`skaldir chat`, measured 00:18): `model deepseek-chat · ctx n/a · ~281 tok/s · 00:00` — with
`~` marking the speed as a streaming estimate, `n/a` before a turn, and **`ctx n/a` because the context
percentage is not measured yet**. The token numbers the shell shows are the labelled
`~N tok (est. chars/4)` estimates on the progress lines and `tokens:N (reported)` when the provider
reported a count. Do not quote a context percentage; quote a `~N tok/s` only from the chat bar.

**The approval prompt** — one gate in front of a gated invocation, the ladder's own verdict printed as
`key value` rows (real capture, `outputs/45-codex-ux/pty-clean.txt`, lines 83–99):

```
⏺ approval needed · approval.run
  command  echo hello
  because  prompt — no rule and no grant covers 'echo hello' — silence is not
           consent, so the user decides
  rule     (none)
  profile  default · sha256 b321e97037ce
  answers  [a] allow once · [s] allow for this session · [r] allow and make it policy · [d] deny with a reason · [Enter] deny
▶ answer [a/s/r/d] › a
  ⎿ allowed once · one-shot grant shell-4130630-once-60865947 · approval-events.jsonl:2
```

The answer is recorded through the real `skaldir approval allow` / `approval deny`, so it is evidence, not
a keystroke; `allow once` is a **one-shot grant** that the shell removes when the turn settles, and a
denial carries your reason back to the agent. A **non-interactive** session never prompts: the gated call
keeps its own blocked exit code (and `--json` turns both gates off).

The honest edge of that ladder, measured on the second round's tree (`/home/willy/gv-c4/log-t2.txt`) — a
**banned prefix** can never be granted the easy way:

```
skaldir ❯ approval run bash -c "echo hello"
⏺ approval needed · approval.run
  command  bash -c echo hello
  because  prompt — 'bash' is on the banned-prefix list (on the banned-prefix
           list: an approved rule for it would allow arbitrary or destructive
           work, so it can never be auto-approved) — it can never be
           auto-approved
  rule     (none)
  profile  default · sha256 e6d89422ec20
  answers  [a] allow once · [s] allow for this session · [r] allow and make it policy · [d] deny with a reason · [Enter] deny
▶ answer [a/s/r/d] › a
  the approval was refused (3): {"schema_version": "1.0", "ok": false, "command": "approval.allow", …

$ skaldir approval allow --scope turn bash -c "echo hello"
skaldir: approval allow: refusing a turn grant for 'bash -c echo hello': on the banned-prefix list: an
approved rule for it would allow arbitrary or destructive work, so it can never be auto-approved —
banned prefixes can never be auto-approved
  hint: banned prefixes can only be approved in the foreground, one command at a time; run it yourself
        or rewrite the command
```

For a command that is *not* banned the ladder behaves normally: `approval check --json ls -la` answers
`"decision": "prompt", "blocked": true, "exit_code": 3, "rule_eligible": true` with four `would_allow`
spellings (`--scope turn`, `--scope session`, `--rule`, or `deny --reason '<what to do instead>'`), and
`banned_prefix: ""` says why this one is grantable.

**The diff preview** — `edit apply` is planned first (`edit dry-run`) and shown before anything is written
(real capture, same file, lines 148–168):

```
⏺ diff preview · dry run — nothing is written yet
  files 1 · +1 -1 · verdict applicable · check none
  M notes.txt  +1 -1
    @@ -1,2 +1,2 @@
     one
    -two
    +two changed
▶ apply this transaction? [y/N] › y
⏺ skaldir edit apply --tx …/tx.json --workspace … --state …
edit 20260918-212115-188785-ec5c91 — applied_unverified
  reason: the PTY proof transaction
    ok notes.txt — exact match, 1 occurrence(s), 8→16 bytes
  ⎿ exit 0 (OK) · 30 ms · 309 B streamed
```

`/diff` shows the last transaction again later; `/undo` restores it byte-identically (`skaldir edit undo`).
A dry run whose verdict is not `applicable` is reported as refused and the real command still runs, so its
own answer and exit code count.

---

## 4. Goals that finish themselves

`skaldir run "<goal>"` is the long loop, and the point of it is that **the model does not get to declare
victory**:

1. **goal + criteria** — the durable goal record (criteria, budget rows, first checkpoint) or `--resume`.
2. **plan** — the first turn asks for a numbered plan; the runtime *separately* derives the real check for
   every criterion (`verify_service.build_plan`) and, unless `--no-baseline`, runs them once so the
   starting state is measured rather than assumed.
3. **turns** — a fresh `AgentLoop` per turn over the workspace's tools plus two adapters
   (`edit_apply`, `run_tests`); a fresh context manager per turn is what "refreshed context" means.
4. **gate** — an answer is a *completion request*. The checks run for real and `GateRunner` decides;
   only `VERIFIED_COMPLETE` means done, a rejection becomes `REDIRECT` with the blockers travelling into
   the next turn, and a durable checkpoint is written.
5. **continue** — on FAIL/UNKNOWN it keeps going: new turn, refreshed context, or
   `skaldir run --resume <goal-id>` after a crash. Bounded by `--max-turns` / `--max-steps` /
   `--wall-clock-s`; exhausting the bound with nothing accepted is `BLOCKED_EXTERNAL` (exit 3) with the
   blocking criterion named — never a pass.
6. **final verdict** — `verify_service.run_plan` over the workspace's declared plan. Exit **0 only on
   PASS**; and a PASS is reported stale (`citable: false`) if the workspace changed after the checks ran.

**The whole loop in one real capture.** Live tree, 2026-09-19 00:02, runtime `~/gv-c4/rt9`, workspace
`~/gv-c4/goalws` — a two-line project whose `add()` subtracts while `tests/test_calc.py` asserts
`add(2,3) == 5` (capture `/home/willy/gv-c4/cap-run6-text.txt`):

```
$ cat .godsv/verify.json
{"goal": "calc.add is correct and the suite is green",
 "criteria": [{"criterion": "the project suite passes", "check": "pytest", "blocking": true},
              {"criterion": "calc.py contains the corrected add",
               "argv": ["grep", "-q", "return a + b", "calc.py"], "blocking": true}]}

$ skaldir run "make calc.add add correctly and keep the project's tests green" \
      --workspace /home/willy/gv-c4/goalws \
      --criteria /home/willy/gv-c4/goalws/.godsv/verify.json --max-turns 3 --max-steps 8 --text
run goal-run:gd31e892ad92e · VERIFIED_COMPLETE · verify PASS
  goal: make calc.add add correctly and keep the project's tests green
  workspace=/home/willy/gv-c4/goalws session=goal-run
  criteria=0 turns=3 completion attempts=3 accepted=0 compactions=0
  baseline: FAIL ({"blocking": 2, "criteria": 2, "failed": 2, "passed": 0, "unknown": 0})
  t1-plan: completion INCOMPLETE (rejected)
    BLOCKER: evidence: only failing checks were provided as evidence
    BLOCKER: tests: test run failed: python -m pytest -q
    BLOCKER: dod: definition-of-done not verified: the project suite passes
  t2-work: completion INCOMPLETE (rejected)
    BLOCKER: dod: definition-of-done not verified: calc.py contains the corrected add
  t3-work: completion INCOMPLETE (rejected)
    BLOCKER: dod: definition-of-done not verified: calc.py contains the corrected add
  verify PASS: {"blocking": 2, "criteria": 2, "failed": 0, "passed": 2, "unknown": 0} — all 2 criterion/criteria verified against the real workspace
    [pytest] passed · the project suite passes · exit=0 · sha256=0914f3da453a832f…
    [command] passed · calc.py contains the corrected add · exit=0 · sha256=8d2fc5bbbad4893a…
  checkpoint t1-plan workspace cp-20260919-0002… ()
  checkpoint t1-plan run cp-goal-run-2-84… ()
  checkpoint t2-work workspace cp-20260919-0003… ()
  checkpoint t2-work run cp-goal-run-3-bc… ()
  checkpoint t3-work workspace cp-20260919-0003… ()
  checkpoint t3-work run cp-goal-run-4-aa… ()
  report: /home/willy/gv-c4/rt9/goal_run/goal-run:gd31e892ad92e.json
```

exit **0**, 22.3 s, 3 turns. Read the shape of it: the criteria came from a document, a **baseline ran
first** (FAIL, 0/2), every model completion was **rejected** — including the ones the model believed —
and the run only closed because the independent verifier executed both checks against the real workspace
and both passed. `criteria=0 turns=3 completion attempts=3 accepted=0` is the honest summary: no
completion was accepted, the checks were. The JSON envelope carries the same as data
(`/home/willy/gv-c4/cap-run-live.json.out`): `terminal_state: VERIFIED_COMPLETE`, `turns_run: 3`,
`verify.verdict: PASS`, `counts {criteria: 2, passed: 2, failed: 0, unknown: 0, blocking: 2}`,
`lease {acquired: true, action: "acquired"}`, six checkpoints (`kind: workspace` and `kind: run` per
turn) and `report_path`.

### 4.1 Resume after a crash — WIRED

Real crash test: start the same goal, `kill -9` the process mid-flight, then resume it (captures
`/home/willy/gv-c4/cap-crash.txt`, `cap-resume.{json,err}`):

```
$ skaldir run "make calc.add add correctly …" --workspace … --criteria … --max-turns 6 &
… 477487 Killed        ← SIGKILL mid-flight

$ skaldir runs status --json
{"store": "…/rt10/runs.db", "exists": true, "counts": …,
 "goals": [{"goal_id": "goal-run:gbda489f88bfb", "revision": 1, "state": "WORKING",
            "session_id": "goal-run", "goal": "make calc.add add correctly and keep the project's tests green",
            "open_criteria": ["c1", "c2"], "owner": "pid=479037:boot=dda6edd6-…", "corrupt": false}]}

$ skaldir runs resume --json          # the store's own R0-R3 protocol
{"goal_id": "goal-run:gbda489f88bfb", "state": "WORKING", "revision": 1,
 "criteria": [{"criterion_id": "c1", "text": "the project suite passes", "blocking": true,
               "verification_plan": "pytest", "status": "failed", "last_failure": "pytest",
               "evidence_refs": [{"ref": "verify:vr-…:c1:8592259b8393d619", "kind": "other",
                                  "check_id": "pytest", "cmd": "python -m pytest -q",
                                  "exit_code": 1, "outcome": "failed", …}]}, …]}

$ skaldir run --resume goal-run:gbda489f88bfb --workspace /home/willy/gv-c4/goalws \
      --criteria /home/willy/gv-c4/goalws/.godsv/verify.json --max-turns 6 --max-steps 8 --json
run goal-run:gbda489f88bfb · VERIFIED_COMPLETE · verify PASS
  criteria=0 turns=6 completion attempts=4 accepted=0 compactions=0
  verify PASS: {"blocking": 2, "criteria": 2, "failed": 0, "passed": 2, "unknown": 0} — all 2 criterion/criteria verified against the real workspace
  report: /home/willy/gv-c4/rt10/goal_run/goal-run:gbda489f88bfb.json
exit 0   |   "resumed": true | "terminal_state": "VERIFIED_COMPLETE"
lease: {"acquired": true, "action": "takeover", …}      # the killed run's writer lease, taken over
```

The lease is the proof it was a resume and not a restart: the first process died holding it, and the
resumed process reports `action: "takeover"` — the store's compare-and-swap, not a flag. Turn numbering
continues (`t1-plan`, `t2-work` came from the killed run; the resume added `t3…t6`). With `--json` the
human summary moves to **stderr** — read stdout as one JSON document.

Where the goal lives: `runs.db` in the runtime dir. Read it without touching the run:

```bash
skaldir runs status            # store, goals, in-flight operations, budget, supervisor snapshot
skaldir goal show              # revision, state, criteria, evidence (exit 5 while no goal exists)
skaldir verify status          # recorded verification runs + whether the verdict is still fresh
skaldir run --resume <id>      # adopt the durable record and continue
```

Exit codes for the whole family: `0` PASS · `2` verification FAILED · `3` blocked/UNKNOWN · `4` needs
credentials · `5` not found (nothing recorded) · `7` internal.

---

## 5. The slash reference table

Every standard verb, what it does, and the real command behind it. **Measured from the build** —
`slash.mapping()` over the real parser tree, each `real` row cross-checked against
`skaldir contract dump --json`. Counts on 2026-09-19: **100 verbs — 87 real · 8 built-in ·
5 planned · 0 not-wired**, over **65 root / 267 leaf / 0 unregistered** commands.

The full grouped table lives in [`docs/SURFACE.md`](SURFACE.md) §2 — one place, so a table cannot
go stale in two documents at once. The live answer is always one command away:

```bash
skaldir                        # then: /help
skaldir chat verbs --json      # every verb + the real command behind it + its state
skaldir ? <command>            # one command's usage, flags and exit codes
```

Three rules make that table trustworthy rather than decorative:

1. **A slash command is an alias plus a default verb** — same handler, same envelope, same exit
   codes. Anything after it is appended verbatim: `/tools show NAME`, `/verify run --json`,
   `/jev decide …`; and a first word that names a real sibling *becomes* the verb
   (`/verify plan` → `verify plan`).
2. **A verb that does not exist is marked `planned` and refused** — never mapped onto a command
   that would do something else. Five verbs are planned today (`/plan`, `/review`, `/rename`,
   `/newtask`, `/debug`): the palette prints `planned`, `/help <verb>` names the reason and the
   nearest real commands, and the shell refuses to run it.
3. **A group this build does not carry shows `not wired here`** and is refused with the closest
   real command (that set is empty today); a layer the selftest reports unreachable marks its
   verbs (`selftest: PERSIST not reachable`) while still running them — the mark is a fact, not a
   lock.

A fourth rule was added by the reachability round (2026-09-19): **a verb that needs an argument
says so.** Twenty verbs declare `needs`/`needs_forms`; `/spawn` alone prints

```
/spawn runs `skaldir agent spawn`, and that needs an assignment — the task text (or --task-file PATH).
```

instead of a bare `exit 1 (USAGE)` with argparse's text on stderr. Measured in a real 80×24 pty;
the ten verbs that lacked the declaration are listed in
`~/Documents/Final_Audit/08-reachability.md` §7 F6.

Fourteen of the 65 roots carry **no** `/` verb on purpose (26 leaves), each with its reason — the
table is in [`docs/SURFACE.md`](SURFACE.md) §2. Every one of them is still reachable in the chat by
its **bare name** (a line whose first word is a real command runs `skaldir <line>`).

---

## 6. Troubleshooting — the eight traps that are real

Each trap is measured on this tree; the capture path is given where one exists.

### 6.1 The eight traps that eat an afternoon

**1 · "It says my tools DB / database is missing — but I have one."**
A stray `SKALDIR_HOME` (or `HERMES_HOME` / legacy `GODSV_HOME`) in the shell points the whole CLI at
another runtime, and missing databases are then *invented* as findings. Resolution order is one rule:
`$SKALDIR_HOME` → `$HERMES_HOME/skaldir` → `~/.hermes/skaldir`. Ask the runtime itself which variable won:

```bash
env | grep -E '^(SKALDIR_HOME|GODSV_HOME|HERMES_HOME)='
skaldir setup --state --json | …        # data.runtime_reason names the variable that supplied it
skaldir registry status                 # runtime / tools db / selection
```
Measured: with `SKALDIR_HOME=/home/willy/gv-fr/rt` every probe reports *that* directory, and
`runtime_reason: "runtime default ($SKALDIR_HOME)"`.

**2 · "My fix has no effect / `AttributeError` on an attribute I just added."**
`PYTHONPATH` beats the editable install, so `import skaldir` can resolve to a *sibling checkout or scratch
copy*. Measured: `PYTHONPATH=/home/willy/gv-fr/shadow/src .venv/bin/python -c "import skaldir"` imports the
shadow (`…/shadow/src/skaldir/__init__.py`), and the console script then dies with
`ModuleNotFoundError: No module named 'skaldir.perf_lazy'`. One-line check:
`env -u PYTHONPATH .venv/bin/python -c "import skaldir; print(skaldir.__file__)"`.

**3 · `skaldir selftest` exits 2.** That is not a crash — it is the answer
(`0` ok, `2` a layer is not reachable, `7` internal). Read the `blocked_reason` per layer, don't guess:
`skaldir selftest --json`, or `skaldir selftest --layer ACT`. A fresh runtime reports
`8 layer(s) · 23 probe(s) · 4 not reachable` because the run store, memory, session store and events
databases do not exist yet — a brand-new install is *supposed* to look like that.

**4 · A pipe prints nothing, only an exit code.** Human lines are TTY-gated: measured,
`skaldir goal show > file` in a pipe wrote **0 bytes** and exited **5**; the same command with `--json`
carries the whole story (`error.code: not_found`, `hint: start a goal first: skaldir runs start --goal`).
In a script: always `--json`, then read `data` — **the payload lives under `data`**, never at the top
level (`{schema_version, ok, command, exit_code, data, error, evidence, warnings, next_actions}`).

**5 · "The key is in my config and it still refuses."**
It refuses *because* it is in the config. A literal `api_key:` is a secret in a file: exit **3**,
`blocked`, *"a config file is not a secret store. Remove the value, keep only `api_key_env: <NAME>`, and
export the credential in the environment."* Set the variable the config names
(`export DEEPSEEK_API_KEY=…`) and nothing else changes.

**6 · Every model-backed command exits 4.** That is `needs_credentials`, and it is the honest answer of an
unconfigured install: `agent run` / `run` print their own repair (`export SKALDIR_LLM_BASE_URL …`, *or run:
skaldir provider add*). Fix it with `skaldir setup` (§1), then confirm with `skaldir setup --check`
(exit 0 only when **both** keys answer) — or `skaldir provider test NAME` for one provider.

**7 · `uv tool install --from . skaldir` fails.** Install the **distribution**, run the **script**:
`uv tool install --from . skaldir-agent` exposes `skaldir`. Measured refusal:
*"Package name (`skaldir-agent`) provided with `--from` does not match install request (`skaldir`)"* —
`skaldir` is the executable, not a package. On a wheel install you also need a one-time
`SKALDIR_PROMPTS=<…>/prompts` (the resolver looks for `<repo>/prompts`; without it `doctor`,
`prompts list` and the Voice prompts die with exit 7) — see `docs/INSTALL.md`.

**8 · `skaldir setup --state` prints JSON even on a terminal.** The human report must be asked for:
`skaldir setup --state --format text`. Same for `--check` and the full run. Measured in a real pty both
`setup --state` and `setup --yes` printed the JSON payload; with `--format text` they print the
`-- detected --` / `-- keys --` / `verdict:` block quoted in §1.

### 6.2 More traps, measured on the second round's tree

**9 · "docker is down and the tool builder says the image does not exist."**
With the daemon up, `skaldir tools mine --image python:3.12-alpine --capability "…"` exits **0**. With
`DOCKER_HOST=unix:///tmp/nope.sock` the *same* command exits **3**:
`image 'python:3.12-alpine' is not local and no pinned digest is known — run with --pull (or pin a digest)
instead of building from a tag`. The local image simply becomes invisible to that process; "not local" is
not "does not exist". Pin a digest once (`image pin`) and a build stops depending on the daemon.

**10 · `$TMPDIR` is not passed into a declared check, and `/tmp` is a 16 GB tmpfs.**
A declared check that reads `$TMPDIR` fails with `KeyError: 'TMPDIR'` — the sandbox **never passes
TMPDIR** to the check; identical result with `TMPDIR=/tmp/gv-c4-tmp` and with `TMPDIR=~/gv-c4/goalws/tmp`
(the check's own `output_tail` is in `/home/willy/gv-c4/rt9/verify6/runs/*.json`). Pass an explicit path
instead of relying on `$TMPDIR`. Separately, `/tmp` is a quota-limited tmpfs (`df -h /tmp` →
`16G … 18%` when measured): a full pytest run under a busy `/tmp` dies with
`OSError(122, 'Disk quota exceeded')` + `INTERNALERROR` and a storm of `E` lines, which reads like a code
regression. For your own runs, `TMPDIR=/home/willy/gv-tmp-verify` puts pytest's temp dirs on the 475 GB
disk.

**11 · An unregistered command named by the product's own prose.**
`skaldir setup`'s `next_steps` says ``System-One (the Jev layer) is off in settings.yaml — run
`skaldir jev enable` ``, and `skaldir jev enable` exits **1**:
`invalid argument jev_command: invalid choice: 'enable' (choose from kinds, packs, selftest, decide,
history)`. Trust the parser, not the prose: `skaldir <group> --help` / `contract dump --json` is the
inventory. (`checkpoint.prune` was the one unregistered parser node at 23:54; the live 00:11 dump reports
`unregistered: []`.)

**12 · A warm session gone stale.**
`warm status --json` after a `kill -9` of the warm pid: exit **5**, `"state": "stale"`,
`"note": "cannot connect to /run/user/1000/skaldir/warm.sock: Connection refused"` — the socket file is
still there, nothing answers. `warm start` again (it recreates the socket) or `warm stop`; the CLI itself
is unaffected (`--version` still exits 0). The socket lives in `$XDG_RUNTIME_DIR`, not under `$HOME`.

**13 · A checkpoint refusal on drift.**
`checkpoint restore <id>` exits 0 but writes **nothing**: `(confirmation_required)`,
`delete demo.md (unexpected_present, 70 bytes)`, `nothing was written: pass --yes to confirm the restore`
(`/home/willy/gv-c4/log-t2.txt` round). The drift *is* the point — read the per-file classification
(`keep` / `delete (unexpected_present)`) before confirming, then `checkpoint restore <id> --yes`;
`--force` records what it walked over in `forced_drift[]`.

**14 · A provider that refuses native structured output.**
Setup's own probe, on DeepSeek: mode `native` (`response_format: {type: json_schema}`) is `skipped` with
`detail: "response_format type is unavailable"` (its HTTP 400), then `json_object` answers
(`mode_used: "json_object"`, `validator: "jsonschema+builtin"`). Nothing to fix in Skaldir — the chain
falls back and **says so** in `mode_trail`. If your provider is documented as `no` for native schemas,
expect `--structured` to answer through `json_object`; an answer that breaks the schema exits **2**, a
*mode* refusal is only a warning.

*Two more, cheap to know:* `python -m skaldir.cli` prints **nothing** (there is no `__main__` guard —
that is documented behaviour; `python -m skaldir` is the entry point), and `skaldir --json` with no
sub-command prints the root envelope (the machine-readable "what is this and how do I start").

---

## 7. Command index — every command cited here, checked against `contract dump --json`

Check performed 2026-09-19T00:11+02:00 on the live tree (`eff149d`), with the same interpreter the CLI
uses:

```
$ skaldir contract dump --json > contract-live.json     # exit 0
$ python3 - <<'EOF'
import json
d = json.load(open('contract-live.json'))['data']
names = {c['command'] for c in d['commands']}
cited = ["setup","shell","serve","run","agent.run","agent.transcript","verify.plan","verify.run",
         "verify.status","verify.evidence","runs.start","runs.status","runs.resume","runs.checkpoint",
         "goal.show","goal.criteria","completion.status","completion.begin","session.resume",
         "warm.start","warm.status","warm.stop","contract.dump","contract.list","contract.show",
         "selftest","doctor","approval.check","approval.run","approval.allow","approval.deny",
         "approval.list","edit.apply","edit.dry-run","edit.show","edit.undo","checkpoint.create",
         "checkpoint.list","checkpoint.show","checkpoint.restore","checkpoint.resume","checkpoint.prune",
         "jev.decide","jev.selftest","provider.list","provider.test","provider.catalog","tools.mine",
         "image.list","telemetry.show","daemon.serve","permission.show"]
print('missing:', [c for c in cited if c not in names])   # []
print(len(cited), 'cited,', len(names), 'in the dump')
EOF
missing: []
52 cited, 256 in the dump
```

**52 commands cited in this guide — all 52 in the contract dump, 0 missing.** The counts printed by the
CLI itself at the 00:11 check: 60 root commands, 256 leaf commands, 307 parser nodes, `unregistered: []`.
A second reading at 00:22 CEST, before this file was finalised, shows the ground moving under it —
**61 root / 257 leaf / 308 parser nodes, `unregistered: ["chat"]`**: the chat bar was added while the
document was being written (§2.3). Two commands the guide names are therefore *not* in the dump:
`skaldir jev enable` (trap 11 — it does not exist at all) and `skaldir chat` (it exists and runs; its
`COMMANDS` entry is the thing that is missing). Re-run the check above before trusting any count.

---

## 8. What is not real yet (so a doc cannot lie by omission)

| thing | state | evidence |
|---|---|---|
| `skaldir chat` | reached in this round (did not exist at 23:54) but has **no `contract dump` entry yet** — `unregistered: ["chat"]` at 00:22 | `skaldir chat --help` and the PTY capture `/home/willy/gv-c4/log-chat2.txt`; the earlier refusal `/home/willy/gv-fr/cap-chat.out` |
| `/plan` | PLANNED — refused with the reason and the nearest real commands | `slash.mapping()` row `kind: planned` (`/home/willy/gv-fr/slash-table.json`) |
| a plain sentence as a model call — in `skaldir shell` | PLANNED there (a bare line is `skaldir <line>`); **`skaldir chat` does the opposite**: a line without a leading `/` is prose and goes to the agent loop | `?` overlay, `/home/willy/gv-fr/first-start-no-color-raw.txt`; `/home/willy/gv-c4/log-chat2.txt` |
| the logo + the "no provider configured — run `skaldir setup`" line on the shell's first screen | PLANNED (the detection and the logo both exist) | `MVP/64-first-run.md` §3 |
| the credential-file write (`.env`) | code path measured only negatively (nothing is written for an invalid key) | `/home/willy/gv-fr/setup-bogus.json` |
| the HTTP tier under the property-test suite (Schemathesis) | still a test-only shim, not this `serve` | `README.md`, honest-not-done-yet |
| the shell footer `model · ctx % · tok/s · duration` | PLANNED in the shell — `telemetry show\|live\|export` is wired as its own group, the shell does not render that line yet | `src/skaldir/telemetry.py:footer_line`; no such line in `/home/willy/gv-c4/log-t2.txt` |
| `skaldir jev enable` | does not exist, although `setup`'s own `next_steps` names it | trap 11 |
| a `.godsv/verify.json` picked up implicitly by `run` | not implemented — `--criteria` is required | `/home/willy/gv-c4/cap-run3.txt` |
| `--text` as a survivable summary of a *failing* `run` | not implemented — an internal error prints nothing at all, exit 7 | `/home/willy/gv-c4/cap-run4.json.err` + `cap-run4.json.out` |

Re-verify any block in this document with the command that produced it; the capture files under
`/home/willy/gv-fr/` and `/home/willy/gv-c4/` (this round) and `outputs/45-codex-ux/` (sibling blocks)
are the raw evidence.

---

## 9. Reproducing these captures

- Working tree: `/home/willy/projects/gods-voice`, branch `godsv-supervisor-hardening`, HEAD `eff149d`.
- Interpreter: `/home/willy/projects/gods-voice/.venv/bin/python`, pinned with `PYTHONPATH=<repo>/src`
  (a leaked `PYTHONPATH` silently resolves `import skaldir` to a *sibling* checkout — trap 3).
- **Frozen vs live tree.** The shell and API captures of the second round were taken against a frozen
  copy of the tree (`/home/willy/gv-c4/tree`, freeze 2026-09-18T23:52:58+02:00, `repl.py` sha256
  `663f5399…`) while sibling blocks were mid-write — the first attempt died with
  `IndentationError: unexpected indent` from `slash.py:221` because that file was being written at that
  moment. The `run` and resume captures were taken against the **live** tree at 00:02–00:04.
- Runtimes: throwaway dirs `~/gv-c4/rt2…rt10` and `~/gv-fr/rt`, each with a copy of a configured
  `config.yaml`. The provider key came from `~/.hermes/.env` and never appears in a capture.
- The PTY harness: `/home/willy/gv-c4/drive_shell.py <log> <script.json>` — a real PTY (`os.openpty`,
  45×120, `start_new_session`), a script of `{wait_for, timeout, send}` steps so the next line is typed
  only after the previous turn settled (the wait is matched against output produced *after* the previous
  send, never against a stale prompt), and a log with the ANSI-stripped transcript plus the raw bytes.
  Write that log incrementally: a version that wrote only at exit left no evidence while a run hung.
- One port note: `8791` and `8765` were taken by other blocks on this shared host during the round;
  pick a port and confirm with `ss -ltnp | grep <port>` that the listener is yours before writing down a
  capture.
- The engineering record — exact commands, per-trap exit codes, hashes, and the defects found while
  writing this — is `MVP/60-chat-guide.md`.
