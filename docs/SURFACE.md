# The Skaldir surface — five taught entries, everything else behind `/`

**Authority:** the build itself. Every number and every line below was measured on
`skaldir 1.5.0` (2026-09-19, repo `/home/willy/projects/gods-voice`, branch
`godsv-supervisor-hardening`, venv Python 3.12.13) with the commands named next to it. The
taught list comes from `skaldir --help` (the *curated* action, `slash.curated_help`, asserted by
`tests/test_slash_palette.py::…curated…` to be exactly five entries); the verb table comes from
`skaldir chat verbs --json` (`slash.mapping()` over the real parser tree).

> The surface changed on 2026-09-19 by owner decision: **the terminal teaches five entries and
> everything else lives in the chat as a `/` verb.** Nothing was removed — every root command is
> still in the parser, still contracted and still reachable. Only the *view* is curated.

---

## 1. The five taught entries

Measured: `skaldir --help` (exit 0), verbatim on 2026-09-19.

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

The five, and what each one is for:

| taught entry | what it does | the honest detail |
|---|---|---|
| `skaldir` (no argument) | the chat | the mark, the status band, the two bars, the input line, the `/` palette. `skaldir chat` and `skaldir shell` are the same surface and stay reachable; they are no longer *taught* |
| `skaldir setup` | one-time setup | the two keys, each proved by a real call. `--check` writes nothing and exits 0 only when both keys answer; the wizard needs a real tty |
| `skaldir run "<goal>"` | the goal-locked run | plan → agent loop → the declared checks → the completion gate. On a terminal it hands the screen to the chat **attached to that goal** (the band shows the real goal; `/steer` and `/queue` deliver to it) |
| `skaldir serve [--http]` | the API surface | stdio MCP by default, `--http` for the HTTP/OpenAPI tier, for editors and GUIs |
| `--help` / `--version` | the screen you are reading | `--all-commands` prints every root command; `skaldir ? <command>` is the overlay with usage, flags and exit codes |

Two of the five are **views**, not commands: the bare call and the root flags. They are named in
the taught list because a taught list that omits the entry point of the build would be a worse
lie than a list with two non-command lines in it (`slash.CURATED_VIEWS`).

## 2. Everything else: the `/` surface

Measured: `skaldir chat verbs --json` → **100 verbs — 87 `real`, 8 built-in, 5 planned,
0 not-wired** (2026-09-19, after the reachability round closed the twenty verb-less roots of
`~/Documents/Final_Audit/08-reachability.md` §4).

* A slash verb is an **alias plus a default verb** for a real `skaldir` command — same handler,
  same envelope, same exit codes. `/verify` runs `skaldir verify status`; `/runs status` runs
  `skaldir runs status`.
* **A sub-verb replaces the default**: everything typed after the verb is passed through, and a
  first word that names a real sibling *becomes* the verb (`/verify plan` → `verify plan`).
* **A verb that does not exist is `planned` and refused**, never mapped onto a command that would
  do something else: `/plan`, `/review`, `/rename`, `/newtask`, `/debug`, each naming its nearest
  real commands.
* **A verb whose command this build does not carry** shows `not wired here` and is refused with
  the closest real name. Today that set is empty.
* A `needs`/`needs_forms` declaration (20 verbs) means the shell prints what the verb needs and
  the real command forms that satisfy it, instead of letting the user stare at a bare
  `exit 1 (USAGE)`.

The whole table, grouped the way the palette groups it (regenerate with `skaldir /help` or
`skaldir chat verbs --json`):

**session** (27) — `/status` → `skaldir selftest` · `/doctor` → `skaldir doctor` · `/new` → `skaldir session new` · `/sessions` → `skaldir session list` · `/resume` → `skaldir session resume` · `/search` → `skaldir session search` · `/export` → `skaldir session export` · `/history` → `skaldir session show` · `/fork` → `skaldir session fork` · `/rename` → `planned` · `/newtask` → `planned` · `/clear` → `built-in` · `/quit` → `built-in` · `/diff` → `built-in` · `/init` → `built-in` · `/permission` → `skaldir permission show` · `/completion` → `skaldir completion status` · `/spend` → `skaldir spend list` · `/stream` → `skaldir stream selfcheck` · `/registry` → `skaldir registry status` · `/metrics` → `skaldir metrics` · `/voice` → `skaldir voice status` · `/warm` → `skaldir warm status` · `/style` → `skaldir style logo` · `/prompts` → `skaldir prompts list` · `/ledger-show` → `skaldir ledger-show` · `/pact-show` → `skaldir pact-show`

**goal & work** (19) — `/goal` → `skaldir goal show` · `/plan` → `planned` · `/verify` → `skaldir verify status` · `/runs` → `skaldir runs status` · `/workflows` → `skaldir workflows run` · `/stop` → `skaldir agent cancel` · `/retry` → `skaldir agent retry` · `/review` → `planned` · `/locking` → `skaldir run --progress` · `/checkpoint` → `skaldir runs checkpoint` · `/snapshots` → `skaldir checkpoint list` · `/restore` → `skaldir checkpoint restore` · `/approve` → `skaldir approval allow` · `/deny` → `skaldir approval deny` · `/telemetry` → `skaldir telemetry show` · `/run` → `skaldir run` · `/criteria` → `skaldir goal criteria` · `/steer` → `built-in` · `/queue` → `built-in`

**model & context** (9) — `/model` → `skaldir model` · `/provider` → `skaldir provider list` · `/context` → `skaldir context usage --text` · `/compact` → `skaldir context compact --text` · `/setup` → `skaldir setup` · `/cost` → `skaldir coding status --text` · `/limit` → `skaldir context usage --text --history` · `/memory` → `skaldir memory scope` · `/prompt` → `skaldir prompt explain`

**code** (11) — `/tools` → `skaldir tools list` · `/skills` → `skaldir skills list` · `/scan` → `skaldir security audit` · `/undo` → `skaldir edit undo` · `/exec` → `skaldir code run` · `/apply` → `skaldir edit apply` · `/map` → `skaldir code map` · `/find` → `skaldir code search` · `/symbols` → `skaldir code symbols` · `/diagnostics` → `skaldir code diagnostics` · `/testfix` → `skaldir coding testfix`

**tools & EXPAND** (7) — `/mine` → `skaldir tools mine` · `/register` → `skaldir tools register` · `/health` → `skaldir tools health` · `/rollback` → `skaldir tools rollback` · `/mcp` → `skaldir mcp map` · `/tools-core` → `skaldir tools-core list` · `/tools-hub` → `skaldir tools-hub`

**quality** (4) — `/selftest` → `skaldir selftest` · `/flows` → `skaldir verify flows` · `/adversarial` → `skaldir adversarial selftest` · `/audit` → `skaldir security audit`

**GUI & vision** (1) — `/vision` → `skaldir vision capabilities`

**memory** (7) — `/remember` → `skaldir memory remember` · `/recall` → `skaldir memory recall` · `/forget` → `skaldir memory forget` · `/memory-stats` → `skaldir memory-stats` · `/memory-search` → `skaldir memory-search` · `/memory-audit` → `skaldir memory-audit` · `/memory-retrieval-check` → `skaldir memory-retrieval-check`

**subagents** (4) — `/agents` → `skaldir agent list` · `/spawn` → `skaldir agent spawn` · `/collect` → `skaldir agent collect` · `/cancel` → `skaldir agent cancel`

**the decision layer** (1) — `/jev` → `skaldir jev selftest`

**host** (1) — `/host` → `skaldir host status`

**containers** (2) — `/docker` → `skaldir docker ensure` · `/image` → `skaldir image list`

**the eight layers** (4) — `/agent` → `skaldir agent list` · `/act` → `skaldir act check` · `/computer-use` → `skaldir act check` · `/compose` → `skaldir compose show`

**help** (3) — `/help` → `built-in` · `/keys` → `built-in` · `/debug` → `planned`

### What is *not* behind a `/` verb, and why

`skaldir contract dump --json` → 65 roots / 267 leaves. **51 of the 65 roots carry a `/` verb**
(241 of the 267 leaves reachable through the palette); the other **14 roots (26 leaves)** are
CLI-only on purpose, each with its reason:

| root | why it is not a chat verb |
|---|---|
| `serve` | a long-running server: it must own a terminal |
| `daemon` | the same server as a service (`daemon status` is a host probe) |
| `desktop` | installs/starts/builds the desktop app on the host — a build step, not a turn |
| `shell` | the shell itself; a verb would recurse |
| `chat` | the chat itself (`skaldir chat verbs` is reachable bare and prints the table) |
| `init` | the shell has its own `/init` built-in for the same file |
| `scan` | the single-file secret scanner; `/scan` deliberately means `security audit` |
| `vault` | credentials must never pass through argv/chat/history — by design |
| `nightshift` | a long mutating batch that needs `--session` |
| `eval` | a benchmark harness, not a turn |
| `cu` | the same report as `/computer-use` under its short root name |
| `art` | the renderer's own surface; the chat draws its own marks |
| `demo` | a showcase transcript |
| `contract` | the GUI's machine-facing source of truth (`dump` is a 1.1 MB document) |

Every one of those roots is still reachable in the chat by its **bare name** — a line whose first
word is a real command runs `skaldir <line>` (the `/help` footer says so).

## 3. The picker rules (one contract, every chooser)

Three surfaces ask a human to choose: the model picker (`/model`, `setup`), the session picker
(`/sessions`, `/resume`) and the value menus of a slash verb. They follow **one** rule set:

* **rows, not questions**: every candidate is a row with its facts on the line (a provider row
  carries its base URL and model; a session row its title and message count). A row is never a
  menu item that hides what it would do;
* **a typed filter narrows as you type**, and the header says how many candidates there are;
* **the page is bounded** (`… n more`), so a 40-candidate slot cannot push the bars off a 24-line
  terminal;
* **`Esc` cancels and writes nothing** — the refusal is printed ("nothing was chosen") and the
  envelope says `refused`. `Ctrl-C` is the same cancellation;
* **`Enter` accepts the highlighted row**, and a pre-filled default is a *real* value the model
  would accept, never a placeholder.

`skaldir.picker` is the **one widget for every list in the chat** (`repl`'s own words), and
`picker.select()` is its one-question form: the model picker calls it through
`model_picker`'s shared import, the session picker runs `picker.Picker` inside the frame, and
the plain fallback (a pipe, `TERM=dumb`, `NO_COLOR`, `SKALDIR_PLAIN`, no `termios`) prints the
same rows as a numbered list and reads one line. So the model picker, the session picker and the
non-tty path cannot drift into three different widgets. Measured on a real pty 2026-09-19 (the
SURFACE-CLOSE captures in `MVP/artifacts-89-cli-surface/`): the widget is drawn at once (10 rows
+ `… 6 more`), the filter narrows it (`deep` → the DeepSeek row), `Esc` writes nothing
(`cancelled: nothing was chosen`, `refused: nothing was written`), `Ctrl-C` answers the same way,
and the plain path asks the same question as a numbered list with the endpoint on the line.

## 4. How to re-measure any of this

```bash
skaldir --help                       # the five taught entries (curated view)
skaldir --all-commands               # every root command this build carries
skaldir chat verbs --json            # every / verb + the real command behind it + its state
skaldir contract dump --json         # 65 roots / 267 leaves / parser nodes / unregistered: []
skaldir selftest --json              # 8 layers, one honest verdict each (exit 2 = one is not)
skaldir ? <command>                  # the overlay: usage, flags, contract facts
```

The counts in this document are the ones those commands printed on 2026-09-19. They move while
blocks work — **re-measure rather than quote**, and put the date next to any number you copy.
