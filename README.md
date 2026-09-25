# Skaldir Agent

> **Arbeitsbereich:** `/home/willy/Agentic_Projects/Skaldir_Agent` — bereinigter Projekttransfer des bisherigen
> Skaldir-Projekts. Einstieg für Coding-Agenten: [AGENTS.md](AGENTS.md) →
> [TRANSFER-UND-IMPLEMENTIERUNG.md](TRANSFER-UND-IMPLEMENTIERUNG.md) → [Masterplan](docs/masterplan/00-START-HIER.md).
> Migrationsprotokoll: [docs/audit/MIGRATION-MANIFEST.md](docs/audit/MIGRATION-MANIFEST.md).
> Das Planpaket selbst liegt unter `docs/masterplan/`; die historische Vorbereitungsnote ist als
> [`docs/upstream/README-PLANPAKET.md`](docs/upstream/README-PLANPAKET.md) erhalten.

---

# Skaldir Agent

Self-learning agent runtime: it supervises a running agent, remembers incidents, turns repeated work
into skills and tools, drives isolated desktops and containers, and keeps credentials behind handles.

Distribution `skaldir-agent` · executables `skaldir` (and `godsvoice`, the same entry point under the
deprecated name) · `skaldir 1.5.0`, contract `schema_version 1.0`.

## The surface: five taught entries

`skaldir --help` (exit 0) prints exactly what a session is taught, and nothing else — the rest of
the product lives in the chat behind `/`:

```
skaldir 1.5.0 - the Skaldir CLI (65 root commands, 267 leaf commands in this build)

  the commands a session is taught
    skaldir                  the chat: the mark, the band, the two bars, the palette
    skaldir setup            one-time setup: the two keys, each proved by a call
    skaldir run "<goal>"     sets the goal and opens the chat attached to it
    skaldir serve            the API surface for editors and GUIs (--http)
    --help                   this screen; --version; --all-commands

  everything else lives in the chat
    /help                    every slash verb, grouped, with its command
    /sessions /resume /goal /run /steer /queue /stop /status
    /verify /runs /checkpoint /context /model /provider /tools
    /memory /agents /jev /host /docker   (all of them: /help)
    skaldir chat verbs --json      each slash verb + the command it runs

  the full surfaces
    skaldir --all-commands        every root command this build carries
    skaldir ? <command>          the overlay: usage, flags, exit codes
    skaldir <command> --help     that command's own parser text
    skaldir contract dump --json the whole inventory + a JSON schema each
    (62 more root command(s) stay reachable - nothing was removed from this build;
     they are / verbs in the chat)
```

**Nothing was removed to make that list short.** All 65 root commands are still in the parser,
still contracted (`unregistered: []`) and still reachable — as a `/` verb in the chat, or by their
bare name on a line of the chat. Measured 2026-09-19: **100 slash verbs (87 real, 8 built-in,
5 planned, 0 not-wired)**, and **51 of the 65 roots carry a `/` verb** — 241 of the 267 leaf
commands are reachable through the palette; the other 14 roots (26 leaves) are CLI-only on
purpose, each with its reason in [`docs/SURFACE.md`](docs/SURFACE.md). That file is the whole
surface in one place: the five entries, the grouped verb table, the picker rules, and the command
that re-measures each number.

## What it is

- **Eight named layers, one CLI group each** — EXPAND, ACT, COMPOSE, ROUTE, SUPERVISE, ENFORCE,
  VERIFY, PERSIST. `skaldir selftest --json` probes every one read-only and reports which is
  reachable right now.
- **One JSON envelope per command.** `--json` prints exactly one document on stdout
  (`schema_version, ok, command, godsvoice_version, started_at, duration_ms, exit_code, data, error,
  evidence, warnings, next_actions`); the human text moves to stderr.
- **Nine exit codes** (0 ok · 1 usage · 2 verification failed · 3 blocked · 4 needs credentials ·
  5 not found · 6 conflict · 7 internal · 8 unknown), so a caller can tell "blocked" from "failed"
  without parsing text.
- **No success without evidence.** A handler that reports `ok: true` with an empty evidence list is
  downgraded to exit 7.
- **Self-describing.** `skaldir contract dump --json` is the machine-readable inventory (args +
  JSON Schema per command) — 65 root / 267 leaf / 321 parser nodes, 0 unregistered (measured
  2026-09-19 on `a8fcaa1` plus this wave's uncommitted edits; the number grows while blocks work, so
  re-measure rather than quote it). The CLI reference and the property-test run are generated from it.
- **One registry truth.** Runtime dir, tools DB, generated-tools dir and project id come from
  `src/skaldir/registry.py` only — `$SKALDIR_HOME` → `$HERMES_HOME/skaldir` → `~/.hermes/skaldir`.
- **Isolation by default.** Computer use and capability mining run in bubblewrap, rootless podman or
  Docker runners; ACT refuses the live desktop.
- **Credentials as handles.** Only handles leave the vault; secrets are encrypted at rest (Fernet,
  0600/0700) and injected into an isolated runner, never printed.

## Install

Requires **Python ≥ 3.11**. The paths below were run on 2026-09-18 (Fedora 44, `uv 0.11.29`, Python
3.12.13, `skaldir 1.5.0`), each into an isolated prefix — never a real tool directory.

### uv (verified)

```bash
uv tool install --from /path/to/gods-voice skaldir-agent
skaldir --version              # skaldir 1.5.0
godsvoice --version            # skaldir 1.5.0 — deprecated alias, same entry point
```

### pipx (NOT verified — pipx is not installed on this machine)

```bash
pipx install /path/to/gods-voice
```

### From source (verified)

```bash
python3 -m venv .venv
.venv/bin/pip install /path/to/gods-voice
.venv/bin/skaldir --version    # skaldir 1.5.0
```

### Two things that will bite you

- **Install the distribution, run the script.** The distribution is `skaldir-agent`, the command is
  `skaldir`. Installing the script name fails on purpose:
  `uv tool install --from . skaldir` → *"Package name (`skaldir-agent`) provided with `--from` does
  not match install request (`skaldir`)"*.
- **A wheel install needs `SKALDIR_PROMPTS` once.** The prompts ship at
  `site-packages/skaldir/prompts`, but the resolver looks for `<repo>/prompts`, so `doctor`,
  `prompts list` and everything Voice-shaped dies with `FileNotFoundError` (exit 7):

  ```bash
  export SKALDIR_PROMPTS="$(find "${UV_TOOL_DIR:-$HOME/.local/share/uv/tools}/skaldir-agent" -maxdepth 7 -type d -name prompts | head -1)"
  skaldir prompts list           # 16 prompt files, 13 required ids loaded
  ```

Requirements, Docker, the runtime directory, migration and troubleshooting: **`docs/INSTALL.md`**.

## Quickstart

```bash
# 1. get the code (the published repo; the local clone's remote is still `gods-voice`)
git clone https://github.com/bertus37987/skaldir-agent.git
cd skaldir-agent

# 2. install (uv recommended; pip works too)
uv venv && source .venv/bin/activate
uv pip install -e ".[mcp]"        # editable; drop ".[mcp]" to skip the MCP server extra

# 3. the two keys, each proved by a real call — the ONE setup step
skaldir setup                     # writes <runtime>/config.yaml; the keys stay in the env
skaldir setup --check             # exit 0 only when BOTH keys answer (writes nothing)

# 4. use it — five taught entries, nothing else to learn at the top level
skaldir                           # the chat: mark, status band, two bars, `/` palette
skaldir run "<your goal>"         # drive one goal to verification; exit 0 only on VERIFY PASS
skaldir serve --http              # the same commands over HTTP + an OpenAPI document
skaldir --help                    # the five; --version; --all-commands
```

Everything else is a `/` verb inside the chat:

```bash
skaldir                           # then, at the prompt:
/sessions  /resume s-1234  /goal  /runs status  /steer "closer to the failing check"
/verify    /checkpoint     /context usage --run-id <id>   /model   /provider list
/memory scope   /agents   /jev selftest   /host status   /image list   /help
```

`skaldir setup` writes `<runtime>/config.yaml` with `godsv.llm` + `godsv.jev`; the key itself stays in the
environment (`api_key_env` names the variable, a literal key in the file is refused). `skaldir setup
--catalog` lists the LLM providers a first run may pick from with endpoint and model pre-filled;
`skaldir setup --state --format text` says what is still missing (exit 3 while it is).

The chat is a **two-bar** surface over a real session: one bar of live state, one bar of what this
session did, then the input line. Tab completes, `?` opens the overlay, `/` opens the slash palette
(`/help` lists it), Esc dismisses it, Ctrl-C cancels a command without leaving, Ctrl-D or `/quit`
leaves. A prose line goes to the agent loop and the session carries it: the next turn is sent with
the previous exchange (`--previous <transcript>`), so the conversation accumulates instead of every
line being an independent run.

```
$ skaldir                      # a real 100x30 pty, 2026-09-19, against a local stub endpoint
  ⏺ skaldir agent run say hello in one line
  ⏺ skaldir agent run and now say the same thing again, in two lines --previous /tmp/qw/rt-d3/agent/runs/run-20260919T135627Z-c7feca.jsonl
  ⠋ thinking — agent run — 0.1s
  [▸_]  agent run  119 ms
  ❯ /locking "make sure sample.py exists" --workspace /tmp/qw/ws --criteria /tmp/qw/criteria.json
  ⎿ exit 3 (BLOCKED) · first output at 0 ms · 260 ms · 1.8 kB streamed
```

(Verbatim rows from the re-runnable capture `MVP/96-quick-wins.md` §"artefacts" — the process is
`skaldir.chatbar` in a real pty, the runtime is a throwaway `/tmp/qw/rt-d3`, and the model is the
local stub, so no credential is quoted. The second prose line carries `--previous`: that is the
session, not a re-run. `⎿ exit 3 (BLOCKED)` is the run's own verdict stamped in the frame, not a
timeout.)

Command by command:

```bash
skaldir selftest --json       # which of the eight layers is reachable (exit 2 = one is not)
skaldir contract dump --json  # every command, its args and its JSON Schema (65 roots / 267 leaves)
skaldir chat verbs --json     # every / verb + the real command behind it + its state (100 verbs)
skaldir run "<goal>"          # goal-locked: plan, work, run the declared checks, exit 0 only on PASS
skaldir serve --http          # the same commands over HTTP + a generated OpenAPI document
```

`skaldir doctor` adds a 10-point check (Python, package, prompts, Hermes CLI, plugin, desktop entry,
runtime, bwrap, podman, ImageMagick) and exits 2 when it finds a problem. The package line resolves
the distribution the product's own loader resolves (`skaldir-agent`, then its underscore form) and
only then falls back to pre-rename `godsv` metadata — and says so in the detail when it had to.

More: [`docs/SURFACE.md`](docs/SURFACE.md) (the whole surface: the five taught entries, the grouped
`/` verb table, the picker rules) · [`docs/CHATTING.md`](docs/CHATTING.md) (setup, the ways to chat, a
session's footers and prompts, the trap list) · [`docs/QUICKSTART.md`](docs/QUICKSTART.md)
(provider connect, first real run) · [`docs/CLI-REFERENCE.md`](docs/CLI-REFERENCE.md) (per-command
reference) · [`docs/RUNBOOK.md`](docs/RUNBOOK.md) (operations) ·
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) (layers, seams, state).

## The eight layers

`skaldir selftest --json` is the authority for the last column — it probes each layer read-only and
reports `reachable` or `blocked` **with the reason**, never a bare "ok".
Status measured 2026-09-19 on `skaldir 1.5.0`, runtime `~/.hermes/godsv` (pre-rename directory,
still in use; see `docs/INSTALL.md`): 8 layer(s) · **25 probe(s)** · 0 not reachable. The CLI groups are
the layer's own leaf commands in `skaldir contract dump --json` (unchanged by the surface round:
the `/` verbs are shell-side aliases, they add no contract entry).

| Layer | Verb | What it does | CLI group (leaf commands) | Status today |
|---|---|---|---|---|
| Self-Expanding Tool System | EXPAND | find existing capability first, else build a typed adapter from a container, test, register with version + health + rollback | `tools`, `tools-hub`, `tools-core`, `registry`, `image`, `docker`, `mcp`, `code`, `coding`, `vision` (62) | reachable — `tools.db` present, 10 generated tools, 14 health states, docker 29.6.1 via probe |
| Computer Use + GUI Sub-Tools | ACT | drive a real desktop and verify against actual state; save repeated flows as sub-tools | `act`, `cu`, `vision` (9) | reachable — `dry_run`/`sandbox_bwrap`/`container_podman` all available; `act check` reports `live_desktop: false`, verdict `isolated-only` |
| Learned Composite Tools | COMPOSE | combine tools into one reusable capability, validated then callable as a single tool | `compose`, `skills`, `tools`, `art`, `code`, `stream` (18) | reachable — 1 ranked candidate; lifecycle wired over 5 verbs; 4 step kinds (`api`, `docker`, `gui`, `tool`) |
| Jev Decision Layer | ROUTE | fast decisions below the agent: memory relevance, routing, capability ranking, compaction, voice triage | `jev`, `context`, `coding` (10) | reachable — flags loaded (`provider=llm_system_one_adapter`, `shadow=False`), `advise()` answered |
| Voice Supervisor | SUPERVISE | independent supervisor: semantic loops, goal drift, false completion claims, missing verification | `voice`, `coding` (6) | reachable — 11 sessions readable; guard wired; pause store readable (61 leases) |
| Goal-Locked Execution | ENFORCE | completion is controlled by the runtime, not the model's claim: verify → PASS ⇒ done | `completion`, `agent`, `run`, `approval`, `permission`, `security`, `goal`, `edit`, `context`, `coding` (26) | reachable — completion store `state=WORKING`; 6 terminal states known |
| Real-World Verification | VERIFY | not "does the code exist" but "can a user use it": run the real flow, check, evaluate criteria | `verify`, `eval`, `adversarial`, `code`, `coding` (12) | reachable — `verify run`/`verify flow`/`verify flows` execute against the real workspace; UNKNOWN exits 3 |
| Long-Run Agent + Learning | PERSIST | persist goal, criteria, decisions, evidence, checkpoints, budgets; survive restarts and reuse project memory | `runs`, `goal`, `session`, `checkpoint`, `memory-*`, `metrics`, `nightshift`, `agent`, `edit`, `prompt`, `stream`, `telemetry` (66) | reachable — 3249 events in 11 sessions; `runs.db` (0 runs) and `sessions.db` (2 sessions) exist |

## What is honest-not-done-yet

Read from `selftest`, `contract dump`, the wiring audit and the module sweep on this tree — not from
a plan:

- **0 of the leaf commands are unwired.** Every module that ships a `register_cli` hook is
  registered in `cli.build_parser()` and every leaf carries a `cli_contract.COMMANDS` entry
  (`unregistered: []`). Measured 2026-09-19: **267 leaves over 65 roots** (321 parser nodes) — quote
  the number only with the moment it was measured, or re-run `skaldir contract dump --json`; sibling
  blocks were still landing verbs in this same tree while it was counted (220 leaves at 23:47 and
  256 at 23:54 on 2026-09-18). The number this README once quoted (33, later 59) was the count while
  `verify`, `voice`, `runs`/`goal`, `compose`, `act`, `image`, `serve`/`daemon`, `provider`, `host`,
  `warm`, `session`, `code`, `edit`, `context`, `agent`, `prompt`, `vision`, `adversarial` and
  `permission`/`approval` were still sitting in code only.
- **A module nothing imports is still a gap, and five of them remain.** `tests/test_no_orphan_modules.py`
  computes reachability from the real entry points (console script, every `__main__`, and the Hermes
  plugin under `integrations/`) over static imports *and* dotted module strings, and fails for any new
  module that no product file can reach. The 2026-09-19 sweep found **5** modules with zero product
  importers — `perf_progress` and the four `system_one_*` substrate modules (`calibration`, `canary`,
  `shadow`, `treasury`) — each allow-listed in that test with the owner of its missing caller. The
  sweep also cleared the pattern's famous instance, `verify_flows.py` (imported by `verify_cli.py`,
  surfaced as `verify flow` / `verify flows`), and showed that `discovery`, `liveinsert` and
  `synthesis` are reached only through `integrations/hermes/godsv_plugin/` — a scan that ignores the
  host layer reports them as orphans by mistake.
- **The taught entry point is the bare call.** `skaldir` (no argument) opens the chat — the mark,
  the status band, the two bars, the `/` palette. `skaldir chat` and `skaldir shell` are the same
  surface and stay reachable and contracted, but they are no longer *taught*: `skaldir --help`
  prints five entries and everything else lives behind `/` (see
  [`docs/SURFACE.md`](docs/SURFACE.md)). Curating the view removed nothing — 65 roots and 267 leaf
  commands are still there, and 51 of the roots carry a `/` verb so 241 leaves are reachable from
  the palette.
- **No HTTP/API surface has been *proven* end to end.** `serve` (stdio MCP) and
  `daemon serve --http` are wired now, but the Schemathesis/Bruno tier still targets a test-only
  shim in `tools/` — the transport of the *product* HTTP tier is not yet covered by a live proof.
- **ACT drives isolated bridges only.** `act check` reports `live_desktop: false` by policy; the
  container GUI bridge is what the subtool proofs use.
- **COMPOSE cannot run `api`/`gui` steps.** `compose run` executes `tool` and `docker` steps; the
  other two kinds report `no runner yet` instead of faking a run.
- **Only the hard completion gate may declare a goal PASS.** `eval` remains in-process episodes, and
  `verify run` can return FAIL or UNKNOWN — `UNKNOWN` exits 3 and never becomes PASS.
- **The desktop app is paused** on purpose: features first, UI after the CLI/API MVP stands.

## Repository layout

```
src/skaldir/      the package — CLI, contract, registry, the eight layers, JEV, treasury
src/godsv/        deprecation alias shim (import godsv is import skaldir)
prompts/          versioned system prompts (data, not code)
tests/            pytest suite (321 test modules)
docs/             INSTALL.md, ARCHITECTURE.md, CLI-REFERENCE.md, QUICKSTART.md, RUNBOOK.md, …
app-overlay/      the desktop app patches (anchored, byte-exact)
integrations/     host integrations (Hermes plugin adapter)
tools/            generators and audit/probe scripts
```

## Credits

Open source Skaldir builds on — Hermes Agent, thinking-orbs, Tabler Icons, Motion, Tailwind CSS,
Radix UI, Geist, Recharts, cmdk, AI Elements, React Flow, fast-jev-compaction, Schemathesis,
Testcontainers, Docker — is named in [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).

## License

Apache-2.0 — see [`LICENSE`](LICENSE).
