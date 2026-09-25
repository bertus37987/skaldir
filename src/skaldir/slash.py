"""The ``/`` palette — slash commands that map onto the EXISTING command groups.

A slash command is an **alias plus a default verb** for a real ``skaldir`` command;
nothing is re-implemented here. ``/jev`` runs ``skaldir jev selftest``, ``/runs`` runs
``skaldir runs status`` — the same handler, the same envelope, the same exit codes.

Six rules make the surface honest and usable:

* **every verb names the real argv it runs.** The mapping is checked against the build's own
  parser (:func:`entries`); a command group this build does not carry is shown as
  ``not wired here`` and refused (with the reason and the closest real command), never
  executed against a guessed verb;
* **a verb whose real command does not exist** carries ``planned`` — the palette prints
  ``planned``, ``/help`` names the reason and the nearest real commands, and the shell
  refuses to run it. A planned verb is never mapped onto a command that would do something
  else. (In this build five verbs are planned: ``/plan``, ``/review``, ``/rename``,
  ``/newtask`` and ``/debug`` — each one names the real commands that cover its ground.)
* **a sub-verb replaces the default verb.** Everything typed after the slash command is
  passed through, and when the first word names a real sibling of the mapped command it
  *becomes* the verb: ``/verify run`` runs ``skaldir verify run`` (not
  ``verify status run``), ``/tools show cap`` runs ``skaldir tools show cap``. Without this
  the advertised usages were unrunnable. A word that is not a sibling is appended verbatim,
  so ``/tools cap-x --json`` is still ``tools list cap-x --json``;
* **a declared value slot takes the real flag.** ``/resume`` declares ``values="session"``
  and ``value_flag="--session"``, so ``/resume s-1234`` runs
  ``skaldir session resume --session s-1234`` — the transformation is declared on the verb,
  printed by ``usage``/``/help``, and reported by :func:`mapping`, never hidden in the shell.
* **a declared rest slot takes the rest of the line as ONE value.** ``/locking`` declares
  ``value_rest=True`` because the command behind it takes a *sentence* (``skaldir run
  "<goal>"``): folding one word into a flag would hand the runtime a truncated goal. The
  words up to the first flag are the value — quotes are honoured — and what follows stays
  flags: ``/locking fix the parser --max-turns 2`` runs ``skaldir run "fix the parser"
  --max-turns 2``. Declared on the verb, printed by ``usage``/``/help``, reported by
  :func:`mapping`.
* **a declared task argv turns free text into a task, not arguments.** ``/computer-use``
  declares ``task_argv=("agent", "run", "--chat-output", "--agent-tools")``: with no
  argument it still maps onto ``act check`` (the honest report), a sibling word still
  becomes the verb (``/computer-use run <list>`` → ``act run``), but free text that names
  no sibling is ONE task for the agent loop — ``/computer-use open chrome`` runs
  ``skaldir agent run --chat-output --agent-tools "open chrome"`` and *starts acting* on
  the GUI instead of printing a report about it. The declaration is on the verb, like the
  slots above; nothing in the shell invents a third mapping.
* **a declared precondition is printed, never assumed.** ``/computer-use`` carries
  ``precondition``: the fact about this host that decides whether the verb can do anything
  at all. ``/help``, the palette's detail view and :func:`mapping` print it verbatim, and
  the shell prints it before running the verb (the snippet is in the round's record) — a
  verb whose environment is missing says so *before* it runs, not after it fails.

The palette's ``ready`` / ``blocked`` marks come from the real ``selftest`` probe — a layer
that reports unreachable marks its slash commands as blocked.

The standard vocabulary is listed in :data:`SLASH_COMMANDS`; :func:`mapping` renders the
whole table as data (``real`` / ``builtin`` / ``planned``, with the completion slot of each
verb) so a doc, a test or ``skaldir chat verbs --json`` can quote the same facts without
re-deriving them.

This module also owns the **top-level help** (:func:`curated_help`, :func:`full_help`): the
``skaldir -h`` screen shows the dozen commands a user actually types and the two ways to see
everything (``skaldir --all-commands``, ``skaldir ? <command>``), while the full root list
stays in the parser and reachable. Curating the *view* is not removing a command.

Usage from Python::

    from skaldir import slash, complete

    tree = complete.build_tree(cli.build_parser())
    palette = slash.entries(tree, reachable={"ACT": True, "VERIFY": False})
    print(slash.render(palette))
    invocation = slash.resolve("/verify status")   # -> SlashInvocation(argv=(...))
"""

from __future__ import annotations

import argparse
import shlex
import textwrap
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

try:  # the product contract carries the layer per command path
    from . import cli_contract as _CONTRACT
except Exception:  # pragma: no cover — standalone builds
    _CONTRACT = None

#: suggestions and fuzzy ranking come from the ONE completion engine (``complete.py``),
#: so the palette and the tab completion rank identically and never drift apart.
from .complete import Suggestion, fuzzy_filter  # noqa: E402

SLASH = "/"

#: how a palette entry is marked (a state, never a promise).
STATE_READY = "ready"
STATE_BLOCKED = "blocked"
STATE_UNWIRED = "unwired"
#: the parser itself could not be loaded, so no command can be checked against it
STATE_UNVERIFIED = "unverified"
#: the verb does not exist in this build yet — reported, never faked
STATE_PLANNED = "planned"


@dataclass(frozen=True)
class SlashCommand:
    """One ``/`` command: a name, the real argv behind it, and where it belongs."""

    name: str
    title: str
    argv: tuple[str, ...] = ()
    usage: str = ""
    group: str = "session"
    aliases: tuple[str, ...] = ()
    detail: str = ""
    builtin: bool = False
    #: the layer the command belongs to, for the honest ready/blocked mark
    layer: str = ""
    #: when set, the verb is **not built**: this is why, in the product's own words. A
    #: planned command has no ``argv`` (there is nothing to run) and is never executed.
    planned: str = ""
    #: what to run *instead* while the verb is planned — real commands, never a guess
    nearest: tuple[str, ...] = ()
    #: the argument this verb must have before its default argv does anything at all
    #: (``/context`` measures one transcript; the bare command exits 1 and prints nothing, so
    #: the shell says what it needs instead of letting the user stare at an empty result).
    needs: str = ""
    #: the real command forms that satisfy ``needs`` — printed verbatim, never invented
    needs_forms: tuple[str, ...] = ()
    #: flags the *shell* adds when it runs this verb, because the prompt is a human surface:
    #: ``context usage`` prints a JSON envelope by default, and the palette is not a JSON
    #: consumer. The command path (and so the mapping) is unchanged — only the form differs.
    flags: tuple[str, ...] = ()
    #: the completion slot of this verb: a :mod:`skaldir.complete` value kind (``session``,
    #: ``checkpoint``, ``model``, ``provider``, ``run``, ``goal``, ``file``) or ``""``.
    values: str = ""
    #: the real flag that carries ``values``. A bare first argument becomes this flag, so
    #: ``/resume s-1234`` runs ``session resume --session s-1234`` instead of failing on an
    #: argument the command does not have. Never invented: it is a flag of the mapped verb.
    value_flag: str = ""
    #: the mapped command's own positional takes the **whole rest of the line** as one value
    #: (``/locking "<a goal in one sentence>"`` → ``run "<a goal in one sentence>"``). Declared
    #: for the verbs whose value is prose: the one-word slot would have truncated it, and a
    #: verb that silently drops half of what the user typed is worse than no verb at all.
    #: The value ends at the first flag of the verb (``--resume``, ``--criteria``, …), which
    #: is passed through as the flag it is; quotes in the goal are honoured.
    value_rest: bool = False
    #: the fact about this host that decides whether the verb can do anything at all — a
    #: statement to print, never an assumption to make. ``/computer-use`` needs a desktop
    #: session it is allowed to drive; the answer comes from the real ACT probe, not from a
    #: guess, and the verb refuses rather than pretend.
    precondition: str = ""
    #: the third declared shape of a line: when words that are not flags and do not name a
    #: sibling of the mapped command follow the verb, the words up to the first flag are
    #: ONE free-form **task** for this argv prefix instead of arguments the default argv
    #: cannot use. ``/computer-use open chrome`` → ``agent run --chat-output
    #: --agent-tools "open chrome"`` (the agent loop acts on the GUI); the bare verb still
    #: maps onto ``act check``. Declared on the verb, reported in ``mode == "task"`` —
    #: never invented by the shell.
    task_argv: tuple[str, ...] = ()

    @property
    def slash(self) -> str:
        return SLASH + self.name

    @property
    def command_path(self) -> str:
        """The dotted contract path of the real command (``provider.list``)."""
        return ".".join(self.argv) if self.argv else ""

    @property
    def spelling(self) -> str:
        return "skaldir " + " ".join(self.argv + self.flags) if self.argv else ""

    def matches(self, name: str) -> bool:
        return str(name).lower() in (self.name, *(alias.lower() for alias in self.aliases))


# --------------------------------------------------------------------------- #
# the registry — every entry names the real command it runs
# --------------------------------------------------------------------------- #

SLASH_COMMANDS: tuple[SlashCommand, ...] = (
    SlashCommand(
        "help",
        "this palette; `/help <command>` shows that command's own --help",
        group="help",
        aliases=("h",),
        usage="/help [command]",
        builtin=True,
        detail="without an argument: the palette, the hints and the session state. "
               "`/help <name>` describes a slash verb, `/help <command> <verb>` runs the "
               "real command's own --help",
    ),
    # -- goal & work ------------------------------------------------------ #
    SlashCommand(
        "goal",
        "the durable goal record (revision, criteria, evidence)",
        argv=("goal", "show"),
        group="work",
        usage="/goal [<goal-id>] [--all]",
        layer="PERSIST",
        values="goal",
        value_flag="--goal-id",
        detail="maps onto `skaldir goal show`",
    ),
    SlashCommand(
        "status",
        "the real selftest: one honest verdict per layer",
        argv=("selftest",),
        group="session",
        usage="/status",
        detail="runs `skaldir selftest`; a layer that is not wired says so",
    ),
    SlashCommand(
        "plan",
        "a step plan for the current goal — not built yet",
        group="work",
        usage="/plan [goal]",
        aliases=("deep-planning",),
        planned=(
            "there is no goal-level planner verb in this build. `skaldir coding plan "
            "show|begin|propose` plans a *coding* task, the checks that would certify a "
            "goal come from `skaldir verify plan`, `skaldir completion begin` records the "
            "goal record, and the model-driven loop is `skaldir agent run` — a `plan` "
            "command that proposes the goal's step list does not exist"
        ),
        nearest=("coding plan show", "coding plan begin", "completion begin", "verify plan"),
        detail="reported as planned; the shell refuses it instead of running another verb. "
               "`/deep-planning` (Cline's spelling) is the same verb and the same refusal",
    ),
    SlashCommand(
        "verify",
        "recorded verification runs, verdict and freshness",
        argv=("verify", "status"),
        group="work",
        usage="/verify [status|plan|run|evidence]",
        layer="VERIFY",
        detail="maps onto `skaldir verify …`; `/verify run` runs the real `verify run`",
    ),
    SlashCommand(
        "tools",
        "the tool registry: specs, builds, health",
        argv=("tools", "list"),
        group="code",
        usage="/tools [list|show NAME|builds|health NAME]",
        layer="EXPAND",
        detail="maps onto `skaldir tools …`; `/tools show NAME` runs the real `tools show`",
    ),
    SlashCommand(
        "skills",
        "the skill candidates this runtime mined, and their status",
        argv=("skills", "list"),
        group="code",
        usage="/skills [list|install NAME|show NAME|validate NAME|promote NAME|export NAME]",
        detail="maps onto `skaldir skills …`: the candidates the runtime mined from its own "
               "tool events, plus preinstalled skills (`install <builtin|owner/repo>`). "
               "`validate` re-verifies against live events and `promote` writes "
               "the SKILL.md — never the other way round",
    ),
    SlashCommand(
        "scan",
        "a security audit of this tree: every declared defect class, with file:line and the fix",
        argv=("security", "audit"),
        group="code",
        usage="/scan [<path>] [--class CLASS] [--min-severity LEVEL]",
        layer="ENFORCE",
        values="file",
        value_flag="--path",
        detail="maps onto `skaldir security audit` (read-only): command injection, path "
               "traversal, secrets, unsafe deserialisation, permission bypass, TOCTOU, "
               "unbounded resources and error leakage, each with the file, the line and the "
               "fix. `/scan <path>` becomes `--path <path>` and Tab completes the tree; "
               "`--min-severity high` and `--class secrets` narrow it, `--list-rules` prints "
               "the rule set. A single file's secret scan is the root command `skaldir scan "
               "<file>` — named here because it is a *different* verb, not folded into this "
               "one",
    ),
    SlashCommand(
        "runs",
        "the run store: goals, in-flight operations, budget",
        argv=("runs", "status"),
        group="work",
        usage="/runs [status|list|start|resume|checkpoint]",
        layer="PERSIST",
        detail="maps onto `skaldir runs …`",
    ),
    SlashCommand(
        "workflows",
        "run a declared workflow by name",
        argv=("workflows", "run"),
        group="work",
        usage="/workflows [<workflow-id>|list|show <workflow-id>]",
        layer="kernel",
        values="workflow",
        needs="a workflow id",
        needs_forms=(
            "skaldir workflows list            (every declared workflow)",
            "skaldir workflows run <workflow-id> [--param key=value]",
        ),
        detail="maps onto `skaldir workflows run <workflow-id>` — the Cline shape, one "
               "declared name and one command. Tab completes the names `skaldir workflows "
               "list` prints; `/workflows list` shows them with their success criterion",
    ),
    SlashCommand(
        "stop",
        "cancel one running agent run",
        argv=("agent", "cancel"),
        group="work",
        usage="/stop <run-id> [--reason WHY] [--wait SECONDS]",
        layer="SUPERVISE",
        values="run",
        needs="a RUN_ID — the run to cancel",
        needs_forms=(
            "skaldir agent cancel <run-id> [--wait SECONDS]",
            "skaldir agent list --json      (the run ids that exist)",
        ),
        detail="maps onto `skaldir agent cancel <run-id>`: it cancels ONE run, and Tab "
               "completes the run ids this runtime wrote. This build has no 'kill every "
               "background process' verb, and `/agent list` is where the ids come from",
    ),
    SlashCommand(
        "retry",
        "re-run an earlier agent run with its own task",
        argv=("agent", "retry"),
        group="work",
        usage="/retry <run-id> [--model-tier small|main] [--max-turns N]",
        layer="SUPERVISE",
        values="run",
        needs="a RUN_ID — the earlier run to retry",
        needs_forms=(
            "skaldir agent retry <run-id> [--max-turns N]",
            "skaldir agent list --json      (the run ids that exist)",
        ),
        detail="maps onto `skaldir agent retry <run-id>` — the same bounded loop replays that "
               "run's task and writes a new transcript",
    ),
    SlashCommand(
        "review",
        "one SUPERVISE pass over the newest (or --session) trajectory — findings + verdict",
        argv=("voice", "check"),
        group="work",
        usage="/review [--session <session-id>] [--limit N] [--text]",
        aliases=("review-session",),
        layer="SUPERVISE",
        values="session",
        value_flag="--session",
        value_rest=False,
        needs="a stored trajectory (events.db) — the pass refuses honestly without one",
        needs_forms=(
            "skaldir voice check --session <id> [--limit N]",
            "skaldir voice status --json       (which sessions exist)",
        ),
        detail="maps onto `skaldir voice check` (SUPERVISE): the six finding categories, each "
               "with evidence from the stored trajectory, and the verdict "
               "(clean / attention + exit 2). A goal-research redirect is the Voice's "
               "own flow, not this verb",
    ),
    SlashCommand(
        "skills-promote",
        "promote a mined skill candidate after its validation pass (propose-first, gated)",
        argv=("skills", "promote"),
        group="code",
        usage="/skills-promote <name> [--force]",
        layer="COMPOSE",
        values="skill",
        needs="a skill NAME — the candidate to promote (see `skaldir skills list`)",
        needs_forms=(
            "skaldir skills promote <name> [--events <db>] [--force]",
            "skaldir skills list --json           (the candidates that exist)",
            "skaldir skills validate <name>       (the checks promote will re-run)",
        ),
        detail="maps onto `skaldir skills promote`: the candidate is validated against the "
               "live events AGAIN by the same handler the CLI runs, a failed validation "
               "refuses (exit 1, `--force` is the explicit override) and an unknown name "
               "exits 5 with `skills list` as the way out",
    ),
    SlashCommand(
        "locking",
        "the goal-locked runtime: a goal, declared criteria, a real gate, no exit on a claim",
        argv=("run",),
        group="work",
        usage='/locking "<the goal, in one sentence>" [--resume <goal-id>] [flags]',
        layer="ENFORCE",
        value_rest=True,
        flags=("--progress",),
        needs="a goal in one sentence — the text after the verb (or `--resume <goal-id>`)",
        needs_forms=(
            'skaldir run "<the goal, in one sentence>" [--criteria FILE] [--workspace DIR]',
            "skaldir run --resume <goal-id>            (continue a stored goal)",
            "skaldir goal criteria --goal-id <id>      (the criteria the goal is judged by)",
            "skaldir completion status                 (the gate: state, metrics, history)",
        ),
        detail="maps onto `skaldir run` (layer ENFORCE). What the chat gets is the whole "
               "runtime, not a promise: the goal and its criteria are recorded durably "
               "before the first model call, every turn is a bounded agent loop, the model "
               "may only *request* completion — the declared checks really run and the gate "
               "decides — and exit 0 happens only on VERIFY PASS. So the run never ends "
               "because the model says it is done, and it continues on failure. It runs in "
               "the foreground: the frame shows the turns, and `^C` cancels the command "
               "while the session and the durable goal record stay (`agent cancel` takes "
               "*agent* run ids and a goal run writes none, so `^C` is the stop). The whole "
               "rest of the line is the goal; a "
               "flag ends it (`/locking two words --max-turns 2`), a workspace without a "
               "declared plan needs `--criteria FILE` or `.godsv/verify.json`, and without "
               "a goal the verb is refused with the real forms above",
    ),
    SlashCommand(
        "doctor",
        "deployment and health check: what is installed, what is missing",
        argv=("doctor",),
        group="session",
        usage="/doctor",
        detail="maps onto `skaldir doctor` (read-only): the runtime, the interpreter, the "
               "prompt set, the isolation tools and every dependency this deployment really "
               "needs, each marked ✓ or ✗ with the path it found. `/status` is the layer "
               "verdicts; `/debug` is the bug report that has no verb",
    ),
    # -- model & context -------------------------------------------------- #
    SlashCommand(
        "model",
        "pick the provider and the model behind the Jev or the LLM role",
        argv=("model",),
        group="model",
        usage="/model [<provider> [<model>]]",
        aliases=("models",),
        layer="kernel",
        values="provider",
        detail="opens the picker inside the frame (the same engine as `skaldir model`); "
               "`/model <provider> [<model>]` applies it without the picker",
    ),
    SlashCommand(
        "provider",
        "configured LLM providers, redacted",
        argv=("provider", "list"),
        group="model",
        usage="/provider [list|doctor|tiers|catalog|test <name>]",
        layer="kernel",
        detail="maps onto `skaldir provider …`",
    ),
    SlashCommand(
        "context",
        "the context budget of a transcript: measured facts, labelled estimates",
        argv=("context", "usage"),
        group="model",
        usage="/context [usage|show|explain]",
        layer="ROUTE",
        detail="maps onto `skaldir context usage` (ROUTE)",
        flags=("--text",),
        needs="--transcript (a message file or a run transcript) or --run-id",
        needs_forms=(
            "skaldir context usage --transcript <a message file or a run transcript>",
            "skaldir context usage --run-id <a run of the state dir>",
        ),
    ),
    SlashCommand(
        "compact",
        "judge this run's tool history per call and free context",
        argv=("context", "compact"),
        group="model",
        usage="/compact [--dry-run]",
        aliases=("compress", "smol"),
        layer="ROUTE",
        detail="maps onto `skaldir context compact`; it compacts one transcript",
        flags=("--text",),
        needs="--transcript (a message file or a run transcript) or --run-id",
        needs_forms=(
            "skaldir context compact --transcript <a message file or a run transcript> --dry-run",
            "skaldir context compact --run-id <a run of the state dir>",
        ),
    ),
    SlashCommand(
        "checkpoint",
        "the self-sufficient snapshot: file, index row and cursor",
        argv=("runs", "checkpoint"),
        group="work",
        usage="/checkpoint [<goal-id>] [--directory DIR]",
        layer="PERSIST",
        values="goal",
        value_flag="--goal-id",
        detail="maps onto `skaldir runs checkpoint` (it *creates* one); the restore side is "
               "`skaldir checkpoint show <id>` / `checkpoint restore <id>`, whose ids Tab "
               "completes",
    ),
    SlashCommand(
        "snapshots",
        "the checkpoints of this runtime and what they cost",
        argv=("checkpoint", "list"),
        group="work",
        usage="/snapshots [--limit N]",
        aliases=("snapshot",),
        layer="PERSIST",
        detail="maps onto `skaldir checkpoint list` (read-only): the file and message counts "
               "of every checkpoint, so `/restore <Tab>` has something to choose from. "
               "`/checkpoint` creates one via the run store",
    ),
    # -- session ---------------------------------------------------------- #
    SlashCommand(
        "new",
        "start a new session (or fork one with --parent)",
        argv=("session", "new"),
        group="session",
        usage="/new [<title>] [--parent <session>]",
        layer="PERSIST",
        value_flag="--title",
        detail="maps onto `skaldir session new`; a bare word becomes `--title <word>`, "
               "`--parent <session>` forks it (Tab completes the session ids)",
    ),
    SlashCommand(
        "sessions",
        "the sessions of this runtime: title, messages, last activity",
        argv=("session", "list"),
        group="session",
        usage="/sessions [--limit N] [--all] [--order recent|created]",
        aliases=("ls", "list"),
        layer="PERSIST",
        detail="maps onto `skaldir session list`; the **bare** `/sessions` opens the picker "
               "(↑/↓ move, type to filter, Enter selects, Esc cancels — the session survives) and "
               "shows the chosen session, while any flag goes to the real command unchanged",
    ),
    SlashCommand(
        "resume",
        "reopen a session: prior history, hash check, next legal role",
        argv=("session", "resume"),
        group="session",
        usage="/resume [<session-id>|last]",
        layer="PERSIST",
        values="session",
        value_flag="--session",
        detail="maps onto `skaldir session resume`; the **bare** `/resume` opens the picker and "
               "runs `session resume --session <id>` on the choice, a bare id becomes "
               "`--session <id>`, and Tab completes the ids that really exist in the store",
    ),
    SlashCommand(
        "search",
        "search message content across sessions (bounded, ranked, FTS5)",
        argv=("session", "search"),
        group="session",
        usage="/search <query> [--session ID] [--role ROLE]",
        layer="PERSIST",
        needs="a query",
        needs_forms=("skaldir session search <query>",),
        detail="maps onto `skaldir session search`",
    ),
    SlashCommand(
        "export",
        "export a session as json / jsonl / markdown / text",
        argv=("session", "export"),
        group="session",
        usage="/export [<session-id>] [--as json|jsonl|markdown|text] [--out FILE]",
        layer="PERSIST",
        values="session",
        value_flag="--session",
        detail="maps onto `skaldir session export`",
    ),
    SlashCommand(
        "history",
        "the message window of one session: header, hash verdict, audit",
        argv=("session", "show"),
        group="session",
        usage="/history [<session-id>] [--limit N] [--last]",
        layer="PERSIST",
        values="session",
        value_flag="--session",
        detail="maps onto `skaldir session show`; a bare id becomes `--session <id>` and Tab "
               "completes the ids of the session store. `/sessions` is the list, `/resume` "
               "continues it",
    ),
    SlashCommand(
        "fork",
        "branch a session into a linked child and continue there",
        argv=("session", "fork"),
        group="session",
        usage="/fork [<session-id>] [--title TITLE] [--label LABEL]",
        aliases=("branch",),
        layer="PERSIST",
        values="session",
        value_flag="--session",
        detail="maps onto `skaldir session fork`: the parent link and the copied window are "
               "the store's own fork, not a re-import",
    ),
    SlashCommand(
        "rename",
        "retitle an existing session — not built yet",
        group="session",
        usage="/rename [<session-id>] <new title>",
        aliases=("title",),
        planned=(
            "the session store can retitle a session in place (`session_store.retitle`), but "
            "no CLI verb exposes it: `session new --title`, `session fork --title` and "
            "`session rotate --title` set a title only when they *create* a session. A "
            "`/rename` folded onto `session fork` would change the session's identity while "
            "looking like a label edit, which is exactly the substitution this palette "
            "forbids"
        ),
        nearest=(
            "session fork --session <id> --title <new title>",
            "session new --title <title>",
            "session list",
        ),
        detail="reported as planned. The hook for the real verb is in the round's record: a "
               "`session rename` leaf over `session_store.retitle`, which already exists",
    ),
    SlashCommand(
        "newtask",
        "a fresh session with distilled context from this one — not built yet",
        group="session",
        usage="/newtask [<title>]",
        planned=(
            "both halves are real separately — `session new` (and `session new --parent`, "
            "which starts a fresh session linked to this one) and `context compact` (which "
            "summarises a transcript) — but no verb distils a conversation into a fresh "
            "session in one step: there is no `session distil` and no hand-off prompt"
        ),
        nearest=(
            "session new --parent <session> --title <title>",
            "context compact --transcript <a message file>",
            "session fork --session <id> --title <title>",
        ),
        detail="reported as planned; its two real halves are `/new` and `/compact`",
    ),
    SlashCommand(
        "clear",
        "wipe the screen and draw the logo again (framed chat only)",
        group="session",
        aliases=("cls",),
        usage="/clear",
        builtin=True,
        detail="answered by the shell itself (`chatbar.handle_clear`): with a frame up it "
               "wipes and redraws, without one it says nothing was cleared",
    ),
    SlashCommand(
        "quit",
        "leave the shell (Ctrl-D does the same)",
        group="session",
        aliases=("q", "exit"),
        usage="/quit",
        builtin=True,
    ),
    SlashCommand(
        "restore",
        "restore the workspace from a checkpoint",
        argv=("checkpoint", "restore"),
        group="work",
        usage="/restore [<checkpoint-id>|last]",
        layer="PERSIST",
        values="checkpoint",
        detail="maps onto `skaldir checkpoint restore`; Tab completes the checkpoint ids of "
               "this runtime (the `rollback` spelling now belongs to `/rollback` → "
               "`tools rollback`, the capability layer — the owner's redesign)",
    ),
    SlashCommand(
        "approve",
        "answer a pending approval prompt with yes, for a scope",
        argv=("approval", "allow"),
        group="work",
        usage="/approve <command…> [--scope turn|session] [--rule]",
        layer="ENFORCE",
        detail=(
            "maps onto `skaldir approval allow`; the approved string is the command or "
            "prefix, not a request id — `skaldir approval list` shows what is pending and "
            "`skaldir approval check <command>` answers for one command"
        ),
    ),
    SlashCommand(
        "deny",
        "answer a pending approval prompt with no, for a scope",
        argv=("approval", "deny"),
        group="work",
        usage="/deny <command…> [--reason WHY] [--rule] [--abort]",
        layer="ENFORCE",
        detail="maps onto `skaldir approval deny`: the same ladder as `/approve`, on the deny "
               "side. The denied string is the command or prefix, `--reason` is handed to the "
               "agent, `--rule` persists a forbid rule and `--abort` stops the turn",
    ),
    SlashCommand(
        "telemetry",
        "one run's measured record: turns, tool calls, tokens, throughput, cost",
        argv=("telemetry", "show"),
        group="work",
        usage="/telemetry <run-id> [--transcript FILE]",
        layer="PERSIST",
        values="run",
        needs="a RUN_ID — the run whose record you want",
        needs_forms=(
            "skaldir telemetry show <run-id>          (rebuilt from the transcript, then filed)",
            "skaldir telemetry live <run-id>          (the per-turn lines of a run, live)",
            "skaldir telemetry export <run-id> --out DIR",
            "skaldir agent list                       (the run ids of this runtime)",
        ),
        detail="maps onto `skaldir telemetry show <run-id>`: duration, turns, every tool call "
               "with its duration, prompt/completion tokens (the provider's own number when "
               "it reported one, otherwise a labelled chars/4 estimate), tokens per second "
               "and the context usage with its compaction trigger. `/cost` is this session's "
               "usage, `/limit` the context budget; this is one run's record, and the id "
               "comes from `/agent list`",
    ),
    # -- code ------------------------------------------------------------- #
    SlashCommand(
        "diff",
        "the last edit transaction's diff, rendered compactly",
        group="session",
        usage="/diff [tx_id|last|--list]",
        builtin=True,
        detail="reads the real `skaldir edit show` record for this runtime's edit state",
    ),
    SlashCommand(
        "undo",
        "restore the files of an edit transaction, byte-identically",
        argv=("edit", "undo"),
        group="code",
        usage="/undo [tx_id|last] [--dry-run]",
        layer="PERSIST",
        detail="maps onto `skaldir edit undo` — the same snapshot restore the CLI does",
    ),
    SlashCommand(
        "init",
        "write a project AGENTS.md from this repository (never overwrites one)",
        group="session",
        usage="/init [--force]",
        builtin=True,
        detail="reads the layout, the declared commands, the test runner and the branch; "
               "`--force` replaces an existing AGENTS.md",
    ),
    SlashCommand(
        "exec",
        "run one code snippet in the sandboxed process",
        argv=("code", "run"),
        group="code",
        usage="/exec <file> [--workspace DIR] [--allow-tool NAME]",
        layer="COMPOSE",
        values="file",
        value_flag="--file",
        detail="maps onto `skaldir code run --file <path>`; a bare path becomes `--file`. "
               "`code run` needs --file or --stdin, so `/exec` without one is refused with "
               "the real forms",
        needs="--file PATH (or --stdin)",
        needs_forms=(
            "skaldir code run --file <a script> --json",
            "skaldir code run --stdin < <a script>",
        ),
    ),
    SlashCommand(
        "setup",
        "first-run setup: the two mandatory keys, each proved by a real call",
        argv=("setup",),
        group="model",
        usage="/setup",
        layer="kernel",
        detail="maps onto `skaldir setup`; it is interactive and needs the two credentials",
    ),
    SlashCommand(
        "cost",
        "this session's usage: messages by role, token estimate, turns, model",
        argv=("coding", "status"),
        group="model",
        usage="/cost",
        flags=("--text",),
        layer="PERSIST",
        detail="maps onto `skaldir coding status --text`. This build declares no price "
               "table for the chat surface, so `cost` is the *measured* usage and says "
               "`unset` where nothing was declared; the treasury ledger is "
               "`skaldir spend list`",
    ),
    SlashCommand(
        "limit",
        "the context limit and its history: budget, trigger, what was dropped",
        argv=("context", "usage"),
        group="model",
        usage="/limit [--transcript FILE|--run-id RUN] [--budget N] [--history]",
        layer="ROUTE",
        detail="maps onto `skaldir context usage --history`: the same measured budget state "
               "as `/context`, plus the trigger history. The injected-block budget is "
               "`skaldir context explain --max-chars`",
        flags=("--text", "--history"),
        needs="--transcript (a message file or a run transcript) or --run-id",
        needs_forms=(
            "skaldir context usage --transcript <a message file> --history",
            "skaldir context usage --run-id <a run of the state dir> --history",
        ),
    ),
    SlashCommand(
        "memory",
        "the memory system: scopes, kinds, decay policy, journal integrity",
        argv=("memory", "scope"),
        group="model",
        usage="/memory [scope|recall <query>|explain <id>|consolidate]",
        layer="PERSIST",
        detail="maps onto `skaldir memory scope` (read-only). `/memory recall <query>` ranks "
               "the stored memories with their explanations, `/memory explain <id>` shows one "
               "in full — retrieval is a query, never a dump",
    ),
    SlashCommand(
        "agent",
        "the bounded agent loop: runs, transcripts, retry",
        argv=("agent", "list"),
        group="layers",
        usage="/agent [list|show RUN|transcript RUN|retry RUN]",
        layer="SUPERVISE",
        detail="maps onto `skaldir agent …`; Tab completes the run ids of this runtime",
    ),
    SlashCommand(
        "keys",
        "the keys this shell binds, and what each one does",
        group="help",
        usage="/keys",
        builtin=True,
        detail="answered by the shell itself: the bindings that are really installed "
               "(`complete.install_readline`) plus the shell's own keys",
    ),
    SlashCommand(
        "debug",
        "collect the local diagnostics for a bug report — not built yet",
        group="help",
        usage="/debug",
        aliases=("reportbug",),
        planned=(
            "there is no upload or report verb in this build — nothing sends a bundle "
            "anywhere. The diagnostics that do exist are local and each one is real: "
            "`doctor` (deployment health), `telemetry show <run-id>` (one run's record) and "
            "`contract dump --json` (this CLI's own inventory)"
        ),
        nearest=("doctor", "telemetry show <run-id>", "contract dump --json"),
        detail="reported as planned; the shell refuses it and names the three local forms",
    ),
    # -- the eight layers ------------------------------------------------- #
    SlashCommand(
        "selftest",
        "the same selftest, spelled like the command it runs",
        argv=("selftest",),
        group="quality",
        usage="/selftest",
        detail="maps onto `skaldir selftest`; `/status` is the same command (the folded "
               "spelling)",
    ),
    SlashCommand(
        "jev",
        "the decision layer: status, kinds, packs, history, decide",
        argv=("jev", "selftest"),
        group="decide",
        usage="/jev [status|kinds|packs|history|decide]",
        layer="ROUTE",
        detail="maps onto `skaldir jev …` — the bare verb is the façade's own selftest (its "
               "status), because `decide` needs a question",
    ),
    SlashCommand(
        "act",
        "what this host can really do (ACT) — and what is missing",
        argv=("act", "check"),
        group="layers",
        usage="/act [check|run|subtool list]",
        layer="ACT",
        detail="maps onto `skaldir act check`",
    ),
    SlashCommand(
        "computer-use",
        "the isolated desktop: act on a GUI task, or report what can really be driven",
        argv=("act", "check"),
        task_argv=("agent", "run", "--chat-output", "--agent-tools"),
        group="layers",
        usage="/computer-use [<free-text task>|run <action-list>|subtool …] [--probe-image]",
        aliases=("cu",),
        layer="ACT",
        precondition=(
            "a desktop session this product is allowed to drive. Measured on this host by "
            "the verb itself: `skaldir act check` reports `live_desktop: false` and "
            "`verdict: \"isolated-only\"` — the live desktop bridge is `forbidden_by_policy` "
            "(CUPolicy.require_isolation rejects a runner whose isolation is 'none'), so "
            "your own screen is never driven. What really runs is the isolated X display "
            ":90 inside rootless podman (image localhost/godsv-cu-x11), plus the bwrap "
            "sandbox and the dry-run fixture. `/computer-use` with no argument therefore "
            "reports instead of pretending, `/computer-use run <action-list.json>` is the "
            "real ACT runner (`skaldir act run`) on that isolated display, and free text "
            "after the verb starts the agent loop on the task — the isolated display is "
            "the only screen either path is ever allowed to touch"
        ),
        detail="the bare verb maps onto `skaldir act check` — the honest capability report, "
               "not a promise. A word that names a sibling becomes the verb, so "
               "`/computer-use run <action-list>` is `skaldir act run <action-list>` and "
               "`/computer-use subtool list` is `skaldir act subtool list`. Free text that "
               "names no sibling is a TASK: `/computer-use open chrome` starts the agent "
               "loop (`skaldir agent run --chat-output --agent-tools \"open chrome\"`) with "
               "the layer tools, so the agent acts on the GUI instead of a report being "
               "printed. Nothing here drives the live desktop: the report says which "
               "bridge is real, which is a fixture, and which is refused by policy",
    ),
    SlashCommand(
        "compose",
        "stored composite tools with per-step executability",
        argv=("compose", "show"),
        group="layers",
        usage="/compose [candidates|show|validate|run]",
        layer="COMPOSE",
        detail="maps onto `skaldir compose …`",
    ),
    # -- the owner's redesign (2026-09-19): the features belong in the chat ---- #
    # Every entry below either names a real command of this build or is *planned* with the real
    # commands that cover its ground — a verb is never faked. Spelling variants of one action
    # folded onto one verb (e.g. `/find` → `code search`, `/rollback` → `tools rollback`).
    SlashCommand(
        "run",
        "drive one goal to verification (the ENFORCE path), then the chat is its surface",
        argv=("run",),
        group="work",
        usage='/run "<a goal in one sentence>"',
        value_rest=True,
        layer="ENFORCE",
        needs="a goal in one sentence — the text after the verb (or `--resume <goal-id>`)",
        needs_forms=(
            'skaldir run "<the goal, in one sentence>" [--criteria FILE] [--workspace DIR]',
            "skaldir run --resume <goal-id>            (continue a stored goal)",
        ),
        detail="maps onto `skaldir run \"<goal>\"`: sets the goal durably and opens the chat "
               "attached to that run, where /steer and /queue deliver to it",
    ),
    SlashCommand(
        "criteria",
        "the goal's definition of done: criteria, checks, evidence",
        argv=("goal", "criteria"),
        group="work",
        usage="/criteria [<goal-id>] [--session <id>]",
        layer="ENFORCE",
        detail="maps onto `skaldir goal criteria` (it takes `--goal-id`, so the bare verb lists "
               "the newest goal's criteria)",
    ),
    SlashCommand(
        "steer",
        "steer the attached run: the message is picked up at the next turn boundary",
        group="work",
        usage='/steer "<what to change>"',
        builtin=True,
        layer="ENFORCE",
        detail="answered by the shell: the message is written to the run's durable message "
               "table (runs.db) and consumed at the next turn boundary, where the record shows "
               "which turn took it; without a run it refuses with the reason `no-run`",
    ),
    SlashCommand(
        "queue",
        "append a message to the attached run's queue (drained in order, one per turn)",
        group="work",
        usage='/queue "<what to do later>"',
        builtin=True,
        layer="ENFORCE",
        detail="answered by the shell: durable, ordered by id, one message per turn boundary; "
               "an empty message, a finished run or a full queue refuses by name",
    ),
    SlashCommand(
        "prompt",
        "how this session's prompt is assembled, cached and fingerprinted",
        argv=("prompt", "explain"),
        group="model",
        usage="/prompt [build|cache|explain|fingerprint]",
        layer="EXPAND",
        detail="maps onto `skaldir prompt …` (read-only)",
    ),
    SlashCommand(
        "apply",
        "apply an edit transaction (previewed first by the diff gate)",
        argv=("edit", "apply"),
        group="code",
        usage="/apply [--tx <tx-id>|--tx-json <file>]",
        layer="ENFORCE",
        needs="--tx <tx-id> (or --tx-json <file>) — the transaction to apply",
        needs_forms=(
            "skaldir edit apply --tx <tx-id> [--workspace DIR]",
            "skaldir edit apply --tx-json <file.json>",
            "skaldir edit undo [<tx-id>] [--force]  (the other half: take one back)",
        ),
        detail="maps onto `skaldir edit apply`, which the diff gate previews before it applies",
    ),
    SlashCommand(
        "map",
        "the code map of this workspace",
        argv=("code", "map"),
        group="code",
        usage="/map [--path <dir>]",
        layer="EXPAND",
        detail="maps onto `skaldir code map` (it takes `--path`, not a positional)",
    ),
    SlashCommand(
        "find",
        "search the codebase (the folded spelling of `/search`. Same action)",
        argv=("code", "search"),
        group="code",
        usage="/find <query> [--mode files_only]",
        layer="EXPAND",
        needs="a query — the pattern to search for",
        needs_forms=(
            "skaldir code search <pattern> [--mode files_only] [--path DIR]",
            "skaldir code search --target files <name>   (search file names)",
        ),
        detail="maps onto `skaldir code search` — the same command `/search` runs for code",
    ),
    SlashCommand(
        "symbols",
        "symbols of a file or the workspace",
        argv=("code", "symbols"),
        group="code",
        usage="/symbols [--path <file>]",
        layer="EXPAND",
        detail="maps onto `skaldir code symbols`",
    ),
    SlashCommand(
        "diagnostics",
        "the language-server diagnostics for this workspace",
        argv=("code", "diagnostics"),
        group="code",
        usage="/diagnostics [--path <file>]",
        layer="VERIFY",
        detail="maps onto `skaldir code diagnostics`",
    ),
    SlashCommand(
        "testfix",
        "the coding loop's test-fix pass on the current failure",
        argv=("coding", "testfix"),
        group="code",
        usage="/testfix [--run <test run id>]",
        layer="VERIFY",
        needs="the test command to fix, after `--`",
        needs_forms=(
            "skaldir coding testfix -- <test command> [--max-rounds N]",
            "skaldir coding testfix --fix-cmd \"<cmd>\" [--workspace DIR]",
        ),
        detail="maps onto `skaldir coding testfix`",
    ),
    SlashCommand(
        "mine",
        "mine a container/image into a typed tool (EXPAND)",
        argv=("tools", "mine"),
        group="tools",
        usage="/mine <image|container>",
        layer="EXPAND",
        detail="maps onto `skaldir tools mine` (a real Docker probe; refused without docker)",
    ),
    SlashCommand(
        "register",
        "register a built tool in the registry",
        argv=("tools", "register"),
        group="tools",
        usage="/register [--build-id <id>]",
        layer="EXPAND",
        needs="a tool NAME — the built tool to register",
        needs_forms=(
            "skaldir tools register <name> [--version V]",
            "skaldir tools list --json      (the tools that exist)",
        ),
        detail="maps onto `skaldir tools register`",
    ),
    SlashCommand(
        "health",
        "the tool registry's health, per tool",
        argv=("tools", "health"),
        group="tools",
        usage="/health [--json]",
        layer="EXPAND",
        needs="a tool NAME — the tool to probe",
        needs_forms=(
            "skaldir tools health <name> [--probe]",
            "skaldir tools list --json      (the tools that exist)",
        ),
        detail="maps onto `skaldir tools health`",
    ),
    SlashCommand(
        "rollback",
        "roll a registered tool back to a previous build",
        argv=("tools", "rollback"),
        group="tools",
        usage="/rollback <tool> [--to <build-id>]",
        layer="EXPAND",
        needs="a tool NAME — the tool to roll back",
        needs_forms=(
            "skaldir tools rollback <name> [--reason WHY]",
            "skaldir tools show <name> --json   (its builds)",
        ),
        detail="maps onto `skaldir tools rollback` (the folded spelling — the edit-undo verb "
               "is `/undo`, and this name belongs to the capability layer)",
    ),
    SlashCommand(
        "flows",
        "the verification flows of this workspace and their verdicts",
        argv=("verify", "flows"),
        group="quality",
        usage="/flows [list|<flow-id>]",
        layer="VERIFY",
        detail="maps onto `skaldir verify flows`",
    ),
    SlashCommand(
        "adversarial",
        "the adversarial release tests (they try to break a real user flow)",
        argv=("adversarial", "selftest"),
        group="quality",
        usage="/adversarial [plan|run|selftest]",
        layer="VERIFY",
        detail="maps onto `skaldir adversarial …`; `run` needs a target, so the bare verb "
               "shows the self-check",
    ),
    SlashCommand(
        "audit",
        "the security audit over this workspace",
        argv=("security", "audit"),
        group="quality",
        usage="/audit [--min-severity high]",
        layer="ENFORCE",
        detail="maps onto `skaldir security audit`",
    ),
    SlashCommand(
        "vision",
        "what this build can honestly do with an image, and the OCR/screenshot doors",
        argv=("vision", "capabilities"),
        group="gui",
        usage="/vision [capabilities|describe <file>|ocr <file>|screenshot]",
        layer="ACT",
        detail="maps onto `skaldir vision …`; the bare verb reports the capabilities, because "
               "describe/ocr need an image",
    ),
    SlashCommand(
        "remember",
        "store one memory with its scope and kind",
        argv=("memory", "remember"),
        group="memory",
        usage='/remember "<fact>" [--scope project]',
        layer="PERSIST",
        detail="maps onto `skaldir memory remember`",
    ),
    SlashCommand(
        "recall",
        "rank the stored memories against a query, with their explanations",
        argv=("memory", "recall"),
        group="memory",
        usage="/recall <query>",
        layer="PERSIST",
        detail="maps onto `skaldir memory recall` — retrieval is a query, never a dump",
    ),
    SlashCommand(
        "forget",
        "drop one memory (by id) or a whole scope",
        argv=("memory", "forget"),
        group="memory",
        usage="/forget <memory-id|--scope <scope>>",
        layer="PERSIST",
        detail="maps onto `skaldir memory forget`",
    ),
    SlashCommand(
        "agents",
        "the subagents this runtime has spawned, with their state",
        argv=("agent", "list"),
        group="agents",
        usage="/agents [list|show RUN|transcript RUN]",
        layer="SUPERVISE",
        detail="maps onto `skaldir agent …`",
    ),
    SlashCommand(
        "spawn",
        "spawn a subagent for one assignment",
        argv=("agent", "spawn"),
        group="agents",
        usage='/spawn "<assignment>" [--run <id>]',
        value_rest=True,
        layer="SUPERVISE",
        needs="an assignment — the task text (or --task-file PATH)",
        needs_forms=(
            'skaldir agent spawn --task "<the assignment>" [--max-turns N] [--parallel N]',
            "skaldir agent spawn --task-file <file>",
        ),
        detail="maps onto `skaldir agent spawn`",
    ),
    SlashCommand(
        "collect",
        "collect a finished subagent's result",
        argv=("agent", "collect"),
        group="agents",
        usage="/collect <assignment-id>",
        layer="SUPERVISE",
        values="run",
        needs="a RUN_ID — the run whose result to collect",
        needs_forms=(
            "skaldir agent collect <run-id> [--wait]",
            "skaldir agent list --json      (the run ids that exist)",
        ),
        detail="maps onto `skaldir agent collect`",
    ),
    SlashCommand(
        "cancel",
        "cancel a running agent run (the folded name of `/stop` for one run)",
        argv=("agent", "cancel"),
        group="agents",
        usage="/cancel [--reason <why>]",
        layer="SUPERVISE",
        values="run",
        needs="a RUN_ID — the run to cancel",
        needs_forms=(
            "skaldir agent cancel <run-id> [--reason WHY] [--wait SECONDS]",
            "skaldir agent list --json      (the run ids that exist)",
        ),
        detail="maps onto `skaldir agent cancel`",
    ),
    SlashCommand(
        "host",
        "the host this runtime is sitting in: status, sessions, skills, cron, tools",
        argv=("host", "status"),
        group="host",
        usage="/host [status|sessions|skills|cron|tools|doctor|memory]",
        layer="SUPERVISE",
        detail="maps onto `skaldir host …` — the honest seam to the host, never presented as "
               "our own behaviour",
    ),
    SlashCommand(
        "docker",
        "the container dependency: is a usable docker daemon reachable",
        argv=("docker", "ensure"),
        group="containers",
        usage="/docker [ensure]",
        layer="EXPAND",
        detail="maps onto `skaldir docker ensure`; the tool-side list and mining live in "
               "`/tools list` and `/mine`",
    ),
    # -- the reachability-round closure (2026-09-19): the twenty roots the audit found ----
    # Every `argv` below was resolved against this build's own parser and the contract dump
    # (`skaldir contract dump --json`, 65 roots / 267 leaves, `unregistered: []`) before it was
    # written down: none of them is a guess, and the sub-verbs the `usage` advertises are the
    # real siblings of the mapped command (the sub-verb rule of :func:`resolve`).
    SlashCommand(
        "image",
        "read what an image really is: config, layers, pins, guards",
        argv=("image", "list"),
        group="containers",
        usage="/image [list|inspect NAME|layers NAME|verify NAME|guard NAME|pin NAME]",
        layer="EXPAND",
        detail="maps onto `skaldir image …`",
    ),
    SlashCommand(
        "mcp",
        "the MCP seam: what is linked, what it offers, what it answers",
        argv=("mcp", "map"),
        group="tools",
        usage="/mcp [map|probe [NAME]|test NAME|register …|call NAME]",
        layer="EXPAND",
        detail="maps onto `skaldir mcp …`; `map` reports what is really linked — an empty "
               "seam answers `BLOCKED` with its reasons, never a green row",
    ),
    SlashCommand(
        "tools-core",
        "the generated-tool registry itself: list, show, call, health, policy",
        argv=("tools-core", "list"),
        group="tools",
        usage="/tools-core [list|show NAME|health NAME|call NAME|policy|smoke]",
        layer="EXPAND",
        detail="maps onto `skaldir tools-core …` (distinct from /tools: the registry, not the "
               "catalog)",
    ),
    SlashCommand(
        "tools-hub",
        "tools, skills and MCP servers found next to the runtime",
        argv=("tools-hub",),
        group="tools",
        usage="/tools-hub [--search TEXT]",
        layer="EXPAND",
        detail="maps onto `skaldir tools-hub` (read-only discovery, no install)",
    ),
    SlashCommand(
        "permission",
        "the permission profile two consumers obey: the sandbox and the agent",
        argv=("permission", "show"),
        group="session",
        usage="/permission [show|explain NAME|set …]",
        layer="ENFORCE",
        detail="maps onto `skaldir permission …`",
    ),
    SlashCommand(
        "completion",
        "the completion gate: state, metrics, history, an override",
        argv=("completion", "status"),
        group="session",
        usage="/completion [status|history|begin|override]",
        layer="ENFORCE",
        detail="maps onto `skaldir completion …`; this is the gate that decides whether a run "
               "may end, so `/status` and this verb answer different questions",
    ),
    SlashCommand(
        "spend",
        "the treasury: requests, approvals, what is left",
        argv=("spend", "list"),
        group="session",
        usage="/spend [list|allowance|request …|approve ID|deny ID|complete ID]",
        layer="kernel",
        detail="maps onto `skaldir spend …`",
    ),
    SlashCommand(
        "stream",
        "the streaming path itself: selfcheck, measure, replay, policy",
        argv=("stream", "selfcheck"),
        group="session",
        usage="/stream [selfcheck|measure|replay|policy --tool NAME[:MODE]]",
        layer="PERSIST",
        detail="maps onto `skaldir stream …`; the bare verb runs the byte-identity selfcheck "
               "(exit 0), while `stream policy` needs at least one `--tool NAME[:MODE]` — so "
               "that sub-verb is declared here rather than run into a usage verdict",
    ),
    SlashCommand(
        "registry",
        "the runtime registry: where the state lives, what a migration would do",
        argv=("registry", "status"),
        group="session",
        usage="/registry [status|migrate --dry-run]",
        layer="EXPAND",
        detail="maps onto `skaldir registry …` (read-only; `migrate` writes)",
    ),
    SlashCommand(
        "memory-stats",
        "memory: how many records, which scopes, how old",
        argv=("memory-stats",),
        group="memory",
        usage="/memory-stats",
        layer="PERSIST",
        detail="maps onto `skaldir memory-stats`; a store that does not exist says so by name "
               "instead of printing zeros",
    ),
    SlashCommand(
        "memory-search",
        "search the memory store",
        argv=("memory-search",),
        group="memory",
        usage="/memory-search <query>",
        layer="PERSIST",
        detail="maps onto `skaldir memory-search <query>`",
    ),
    SlashCommand(
        "memory-audit",
        "memory: the records that need an owner decision",
        argv=("memory-audit",),
        group="memory",
        usage="/memory-audit [--max-age-days N]",
        layer="PERSIST",
        detail="maps onto `skaldir memory-audit`",
    ),
    SlashCommand(
        "memory-retrieval-check",
        "memory: does retrieval still answer for this store",
        argv=("memory-retrieval-check",),
        group="memory",
        usage="/memory-retrieval-check",
        layer="PERSIST",
        detail="maps onto `skaldir memory-retrieval-check`",
    ),
    SlashCommand(
        "metrics",
        "the local metrics of this runtime",
        argv=("metrics",),
        group="session",
        usage="/metrics [--session-id ID]",
        layer="PERSIST",
        detail="maps onto `skaldir metrics`",
    ),
    SlashCommand(
        "voice",
        "voice: what this build can do, what it needs, triage of a failure",
        argv=("voice", "status"),
        group="session",
        usage="/voice [status|check|explain|triage]",
        layer="SUPERVISE",
        detail="maps onto `skaldir voice …`",
    ),
    SlashCommand(
        "warm",
        "the warm session: start it, is it up, stop it",
        argv=("warm", "status"),
        group="session",
        usage="/warm [status|start|stop]",
        layer="kernel",
        detail="maps onto `skaldir warm …`; without a warm session the status is `not_running` "
               "with the socket path it looked for",
    ),
    SlashCommand(
        "style",
        "the monochrome language: the mark, the palette, a preview",
        argv=("style", "logo"),
        group="session",
        usage="/style [logo|palette|preview]",
        layer="kernel",
        detail="maps onto `skaldir style …` (the same mark the chat draws)",
    ),
    SlashCommand(
        "prompts",
        "the shipped prompt templates",
        argv=("prompts", "list"),
        group="session",
        usage="/prompts list",
        layer="kernel",
        detail="maps onto `skaldir prompts list` — the templates this build really ships, not "
               "a promise about them",
    ),
    SlashCommand(
        "ledger-show",
        "the ledger of one state directory, read back",
        argv=("ledger-show",),
        group="session",
        usage="/ledger-show [--dir DIR]",
        layer="kernel",
        detail="maps onto `skaldir ledger-show` (read-only; the writer is `run`)",
    ),
    SlashCommand(
        "pact-show",
        "the pact of one state directory, read back",
        argv=("pact-show",),
        group="session",
        usage="/pact-show [--dir DIR]",
        layer="kernel",
        detail="maps onto `skaldir pact-show` (read-only; the writer is `init`)",
    ),
)
#: palette sections in display order (a slash command's ``group`` picks one).
#:
#: The groups follow the owner's redesign (2026-09-19): the features a session reaches for, in
#: the order a session learns them — session, work, model, code, tools, quality, GUI, memory,
#: subagents, the decision layer, the host and containers — with the eight layers and help last.
GROUPS: tuple[tuple[str, str], ...] = (
    ("session", "session"),
    ("work", "goal & work"),
    ("model", "model & context"),
    ("code", "code"),
    ("tools", "tools & EXPAND"),
    ("quality", "quality"),
    ("gui", "GUI & vision"),
    ("memory", "memory"),
    ("agents", "subagents"),
    ("decide", "the decision layer"),
    ("host", "host"),
    ("containers", "containers"),
    ("layers", "the eight layers"),
    ("help", "help"),
)

BY_NAME: dict[str, SlashCommand] = {command.name: command for command in SLASH_COMMANDS}


def command_for(name: str) -> SlashCommand | None:
    """The slash command (or alias) with this name, without the leading ``/``."""
    wanted = str(name).lstrip(SLASH).strip()
    for command in SLASH_COMMANDS:
        if command.matches(wanted):
            return command
    return None


def names() -> list[str]:
    return [command.name for command in SLASH_COMMANDS]


def spellings() -> list[str]:
    """Every accepted spelling, primary names first (``/help``, ``/h``, …)."""
    out: list[str] = []
    for command in SLASH_COMMANDS:
        out.append(command.slash)
        out.extend(SLASH + alias for alias in command.aliases)
    return out


@dataclass(frozen=True)
class SlashInvocation:
    """A resolved ``/`` line: the command plus the argv the CLI should see."""

    command: SlashCommand
    argv: tuple[str, ...]
    extra: tuple[str, ...] = ()
    raw: str = ""
    #: how the words became ``argv``: ``"default"`` (the verb's own form), ``"verb"`` (the
    #: first word replaced the default sub-verb), ``"value"`` (a bare value took the
    #: verb's declared ``value_flag``), ``"rest"`` (the words up to the first flag became
    #: ONE value for the verb's own positional) or ``"task"`` (the words up to the first
    #: flag are ONE free-form task for the verb's declared ``task_argv`` — the agent loop
    #: runs them). Reported so the shell can *show* the substitution instead of doing
    #: something invisible.
    mode: str = "default"
    #: the flag a bare value was folded into (``--session``), when ``mode == "value"``
    value_flag: str = ""
    #: the command path *after* a sub-verb substitution — what a caller must walk to find
    #: the real parser node of this line (``("agent","show")`` for ``/agent show …``).
    #: ``()`` means "the command's own ``argv``, unchanged".
    verb_argv: tuple[str, ...] = ()

    @property
    def builtin(self) -> bool:
        return self.command.builtin

    @property
    def is_quit(self) -> bool:
        return self.command.name == "quit"

    def as_line(self) -> str:
        return "skaldir " + " ".join(self.argv)


#: ``None``-until-built cache for the tree :func:`resolve` needs to recognise a sub-verb.
_TREE_CACHE: list[Any] = []


def _default_tree() -> Any:
    """The build's own parser tree (built once, on the first line that needs it).

    ``resolve`` is pure text handling for the common case; the *only* thing that needs the
    parser is recognising that ``/verify run`` names a real sibling of ``verify status``.
    Callers that already hold a tree (the shell, ``complete``) pass theirs in.
    """
    if _TREE_CACHE:
        return _TREE_CACHE[0]
    tree: Any = None
    try:
        from .cli import build_parser
        from .complete import build_tree

        tree = build_tree(build_parser())
    except Exception:  # noqa: BLE001 — without a tree nothing is substituted, never invented
        tree = None
    _TREE_CACHE.append(tree)
    return tree


def _sibling(tree: Any, argv: Sequence[str], word: str) -> bool:
    """Is ``word`` a real sibling sub-command of ``argv``'s own command?

    ``/verify run`` maps onto ``verify status``; the sibling to look for is ``run`` under
    ``verify`` — the parent of the default verb, not under the default verb itself.
    """
    if tree is None or not argv:
        return False
    node = tree
    for token in list(argv)[:-1]:
        if str(token).startswith("-"):
            continue
        node = getattr(node, "children", {}).get(str(token))
        if node is None:
            return False
    children = getattr(node, "children", {}) or {}
    return str(word) in children and children[str(word)] is not None


def resolve(line: str, tree: Any = None) -> SlashInvocation | None:
    """Parse ``/name rest…``; ``None`` when the line is not a slash command.

    An unknown name is not a resolution failure to hide: it returns ``None`` and
    :func:`close_names` explains it, so the caller can print the closest real names.

    Two **declared** transformations happen here (and nowhere else, so the shell cannot
    invent a third one):

    * **sub-verb** — when the first word after the verb names a real sibling of the mapped
      command it *becomes* the verb (``/verify run`` → ``verify run``, not
      ``verify status run``). Checked against the real parser, so an unknown word is
      appended verbatim like every other argument — unless the verb declares a
      ``task_argv`` (below);
    * **value slot** — when the verb declares ``value_flag`` and the first word is not a
      flag and not a sibling, it is folded into that flag (``/resume s-1`` →
      ``session resume --session s-1``).

    A third shape is the **rest slot** (``value_rest``), and it is declared the same way:
    the words up to the first flag become ONE value for the mapped command's own
    positional, flags after it stay flags, and quotes are honoured —
    ``/locking fix the parser --max-turns 2`` → ``run "fix the parser" --max-turns 2``.
    A fourth is the **task slot** (``task_argv``): when no sibling matched and the verb
    declares a task argv, the words up to the first flag are ONE free-form task for that
    argv — ``/computer-use open chrome`` → ``agent run --chat-output --agent-tools
    "open chrome"`` (the agent loop acts), while the bare verb still maps onto
    ``act check`` and flags alone still pass to it.
    ``mode`` reports which of the four fired
    (``default``/``verb``/``value``/``rest``/``task``).
    """
    text = str(line or "").strip()
    if not text.startswith(SLASH):
        return None
    body = text[len(SLASH) :]
    # Tokenise with ``shlex``, not ``split()``: a quoted value is ONE token, which is what the
    # CLI receives when the same words are typed without the slash. ``split()`` left the quote
    # characters in place for every verb except the ones with a declared rest slot, so
    # ``/spawn --task "hi there"`` reached argparse as ``--task hi there`` and died with
    # ``unrecognized arguments: there`` — while the action row joined the argv with spaces and
    # printed it looking well formed. ``shlex_split`` never raises (an unbalanced quote falls
    # back to ``split()``), so a half-typed line still resolves.
    parts = shlex_split(body)
    if not parts:
        return None
    command = command_for(parts[0])
    if command is None:
        return None
    extra = list(parts[1:])
    verb = tuple(command.argv)
    folded: tuple[str, ...] = ()
    mode = "default"
    used_flag = ""
    if extra and verb and not command.builtin and not str(extra[0]).startswith("-"):
        known = tree if tree is not None else _default_tree()
        if _sibling(known, verb, str(extra[0])):
            verb = tuple(command.argv[:-1]) + (str(extra[0]),)
            extra = extra[1:]
            mode = "verb"
        elif command.task_argv:
            # the task slot: the words up to the first flag are ONE free-form task for
            # the verb's declared task argv (the agent loop's own command), flags after
            # them pass through as flags of that command. It sits behind the sibling
            # check on purpose: `/computer-use run x.json` is `act run x.json`, and only
            # free text that names no sibling starts the agent on the task.
            task_words = [str(item) for item in extra]
            task_head: list[str] = []
            while task_words and not str(task_words[0]).startswith("-"):
                task_head.append(str(task_words.pop(0)))
            if task_head:
                folded = (" ".join(task_head),)
                extra = task_words
                verb = command.task_argv
                mode = "task"
        elif command.value_rest:
            # the rest slot: the words up to the first flag are ONE value for the
            # positional the mapped command already declares. The tokens are already
            # shlexed above, so a quoted goal ("fix the parser") is one word here too.
            words = [str(item) for item in extra]
            head: list[str] = []
            while words and not str(words[0]).startswith("-"):
                head.append(str(words.pop(0)))
            if head:
                folded = (" ".join(head),)
                extra = words
                mode = "rest"
        elif command.value_flag:
            folded = (command.value_flag, str(extra[0]))
            extra = extra[1:]
            mode = "value"
            used_flag = command.value_flag
    argv = verb + folded + tuple(command.flags) + tuple(extra)
    return SlashInvocation(
        command=command,
        argv=argv,
        extra=tuple(parts[1:]),
        raw=text,
        mode=mode,
        value_flag=used_flag,
        verb_argv=verb,
    )


def close_names(name: str, limit: int = 4) -> list[str]:
    """The slash commands closest to an unknown ``name`` (honest suggestion list)."""
    ranked = fuzzy_filter(
        str(name).lstrip(SLASH), SLASH_COMMANDS, key=lambda item: item.name, limit=limit
    )
    return [item.slash for item in ranked]


# --------------------------------------------------------------------------- #
# availability: read from the real parser and the real selftest report
# --------------------------------------------------------------------------- #


def _wired(tree: Any, argv: Sequence[str]) -> bool:
    """Does this argv resolve against the real parser tree?"""
    if tree is None:
        return True  # no tree to check against: claim nothing, block nothing
    node = tree
    for token in argv:
        if str(token).startswith("-"):
            continue
        child = getattr(node, "children", {}).get(str(token))
        if child is None:
            return False
        node = child
    return True


@dataclass(frozen=True)
class PaletteEntry:
    """One palette row: the command, its state and why."""

    command: SlashCommand
    state: str = STATE_READY
    reason: str = ""
    layer: str = ""
    detail: str = ""

    @property
    def ready(self) -> bool:
        return self.state == STATE_READY

    @property
    def runnable(self) -> bool:
        """An unwired or a planned command is refused — a flagged layer still runs.

        ``planned`` means the verb does not exist in this build: there is nothing honest
        to execute, so the row says so instead of running a verb that would do something
        else.
        """
        return self.state not in (STATE_UNWIRED, STATE_PLANNED)

    def mark(self) -> str:
        if self.state == STATE_READY:
            return "ready"
        if self.state == STATE_BLOCKED:
            return f"selftest: {self.reason}"
        if self.state == STATE_UNVERIFIED:
            return f"unverified — {self.reason}"
        if self.state == STATE_PLANNED:
            return "planned"
        return f"{self.reason}"


def _layer_of(command: SlashCommand) -> str:
    if command.layer:
        return command.layer
    path = command.command_path
    if _CONTRACT is None or not path:
        return ""
    try:
        spec = _CONTRACT.spec_for(path)
    except Exception:  # noqa: BLE001
        return ""
    return "" if spec.layer == "(unregistered)" else str(spec.layer)


def entries(
    tree: Any = None,
    *,
    reachable: dict[str, bool] | None = None,
    report: dict | None = None,
    groups: Sequence[str] | None = None,
    tree_error: str = "",
) -> list[PaletteEntry]:
    """The palette, each row marked ready / blocked / not-wired from real state.

    ``reachable`` is ``{layer: ok}`` straight out of :func:`cli_contract.run_selftest` —
    omitted, no layer is claimed reachable and the marks stay neutral ("no probe").
    ``report`` (the same call's full dict) supplies each layer's ``blocked_reason``.
    ``tree_error`` says the CLI parser itself could not be loaded: then nothing can be
    checked against it and every row is marked unverified — never "ready".
    """
    reasons: dict[str, str] = {}
    for record in (report or {}).get("layers", []):
        name = str(record.get("layer", "")).upper()
        reason = str(record.get("blocked_reason") or "")
        if name and reason:
            reasons[name] = reason
    out: list[PaletteEntry] = []
    for command in SLASH_COMMANDS:
        if groups is not None and command.group not in groups:
            continue
        if command.planned:
            # a verb that does not exist yet: named, explained and refused — never run
            out.append(
                PaletteEntry(command, STATE_PLANNED, "not built yet", "", command.planned)
            )
            continue
        if not command.argv:
            out.append(PaletteEntry(command, STATE_READY, "built-in", ""))
            continue
        if tree_error:
            out.append(
                PaletteEntry(
                    command,
                    STATE_UNVERIFIED,
                    f"the CLI parser did not load ({_short(tree_error, 60)})",
                    _layer_of(command),
                )
            )
            continue
        if not _wired(tree, command.argv):
            out.append(
                PaletteEntry(command, STATE_UNWIRED, "not wired here", _layer_of(command))
            )
            continue
        layer = _layer_of(command)
        if reachable is not None and layer and layer.upper() in {
            key.upper() for key in reachable
        }:
            if not reachable[layer.upper()]:
                out.append(
                    PaletteEntry(
                        command,
                        STATE_BLOCKED,
                        f"{layer} not reachable",
                        layer,
                        _short(reasons.get(layer.upper(), ""), 88),
                    )
                )
                continue
        out.append(PaletteEntry(command, STATE_READY, "wired", layer))
    return out


def matched(
    query: str,
    tree: Any = None,
    *,
    reachable: dict[str, bool] | None = None,
    report: dict | None = None,
    tree_error: str = "",
    limit: int | None = None,
) -> list[PaletteEntry]:
    """The palette filtered by what has been typed after the ``/`` (fuzzy, ranked)."""
    pool = entries(tree, reachable=reachable, report=report, tree_error=tree_error)
    if not query or query == SLASH:
        return pool[:limit] if limit else pool
    wanted = str(query).lstrip(SLASH)
    ranked = fuzzy_filter(wanted, pool, key=lambda item: item.command.name, limit=limit)
    return ranked


def suggestions(
    query: str,
    tree: Any = None,
    *,
    reachable: dict[str, bool] | None = None,
    report: dict | None = None,
    tree_error: str = "",
    limit: int | None = None,
) -> list[Suggestion]:
    """Palette rows as completion suggestions (this is what ``/``+Tab shows).

    ``limit=None`` returns every matching row: the *display* is paged by whoever draws the
    menu (the shell shows one page and says "… n more"), and a candidate list that silently
    stops at twelve would make Tab look broken for a verb that exists.

    One line per candidate: what it does, the real command behind it, and — only when it
    is *not* ready — the honest state. A row that says ``ready`` twice is noise; a row
    that hides ``not wired`` would be a lie.
    """
    out: list[Suggestion] = []
    for entry in matched(
        query, tree, reachable=reachable, report=report, tree_error=tree_error, limit=limit
    ):
        command = entry.command
        parts = [command.title]
        if command.planned:
            # never "runs skaldir …": there is no command behind a planned verb
            parts.append("planned — not built in this build")
        else:
            parts.append(f"runs {command.spelling}" if command.spelling else "built-in")
        if entry.state != STATE_READY:
            parts.append(entry.mark())
        out.append(Suggestion(command.slash, " · ".join(parts), "slash"))
    return out


# --------------------------------------------------------------------------- #
# the palette as it is being typed (fuzzy filter, ESC to dismiss)
# --------------------------------------------------------------------------- #


@dataclass
class PaletteMenu:
    """The palette while it is being typed: filtered as you type, dismissed with ESC.

    The shell owns this state so ``/`` + Tab, typing more letters and Esc behave like one
    widget instead of three unrelated prints. It is deliberately dumb: it stores what was
    drawn, filters the rows the shell hands it, and forgets everything on ``dismiss()``.
    """

    query: str = ""
    entries: list[PaletteEntry] = field(default_factory=list)
    open: bool = False
    drawn: int = 0
    dismissed: str = ""
    #: how many rows matched before the page limit — "… n more" is this minus what is shown
    matched: int = 0

    def update(
        self,
        query: str,
        palette: Sequence[PaletteEntry],
        *,
        limit: int | None = None,
    ) -> list[PaletteEntry]:
        """Filter ``palette`` by what has been typed after the ``/``; returns the rows."""
        self.query = str(query or "")
        wanted = self.query.lstrip(SLASH).strip()
        pool = list(palette)
        if wanted:
            ranked = fuzzy_filter(wanted, pool, key=lambda item: item.command.name,
                                  limit=None)
            self.matched = len(ranked)
            rows = ranked[:limit] if limit else ranked
        else:
            self.matched = len(pool)
            rows = pool[:limit] if limit else pool
        self.entries = list(rows)
        self.open = bool(self.entries)
        self.drawn += 1
        return self.entries

    @property
    def hidden(self) -> int:
        """Rows the page does not show (never silently dropped: the palette names them)."""
        return max(0, self.matched - len(self.entries))

    def dismiss(self) -> bool:
        """ESC: close the palette without running anything. ``True`` when it was open."""
        was = self.open or self.drawn > 0
        self.dismissed = self.query
        self.query = ""
        self.entries = []
        self.open = False
        self.drawn = 0
        return was

    def render(
        self,
        *,
        paint: Callable[[str, str], str] | None = None,
        width: int = 80,
        title: str = "",
        ellipsis: str = "…",
    ) -> str:
        """The rows this menu currently holds, drawn by :func:`render`."""
        text = render(
            self.entries, paint=paint, width=width, title=title, ellipsis=ellipsis
        )
        if self.hidden:
            more = f"    {ellipsis} {self.hidden} more  {SLASH_ARROW}  type more letters, " \
                   f"or /help for the whole table"
            style = paint or _IDENTITY
            text += "\n" + style(more, "dim")
        return text


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #

def _IDENTITY(text: str, role: str = "") -> str:
    """The no-colour painter: text goes through unchanged."""
    return text


def short_text(text: str, limit: int, *, ellipsis: str = "…") -> str:
    """One line, clipped to ``limit`` characters with an ellipsis (never mid-word noise).

    The ellipsis is a parameter because the *plain* CLI mode is ASCII-only: the shell
    passes ``"..."`` when colour/glyphs are off.
    """
    text = " ".join(str(text or "").split())
    if limit <= 0 or len(text) <= limit:
        return text
    if limit <= len(ellipsis):
        return text[:limit]
    return text[: limit - len(ellipsis)].rstrip() + ellipsis


#: Typographic characters plain mode must not emit (``theme.py``'s contract: plain output
#: is ASCII-only). The palette's prose carries em dashes and the separators are dots — one
#: table, one place, so a pipe, a log or a diff reads the same everywhere.
PLAIN_TRANSLATION = str.maketrans({
    "—": "-",
    "–": "-",
    "…": "...",
    "·": "-",
    "•": "-",
    "→": "->",
    "“": '"',
    "”": '"',
    "‘": "'",
    "’": "'",
    "×": "x",
    "≥": ">=",
    "≤": "<=",
    "\u00a0": " ",
})


def ascii_safe(text: str) -> str:
    """Transliterate the few typographic characters plain mode must not carry.

    Two steps, both deterministic: NFKD + drop the combining marks (so ``façade`` becomes
    ``facade``) and then the explicit table above (so ``—`` becomes ``-``).
    """
    folded = unicodedata.normalize("NFKD", str(text))
    stripped = "".join(char for char in folded if not unicodedata.combining(char))
    return stripped.translate(PLAIN_TRANSLATION)


def _short(text: str, limit: int) -> str:
    return short_text(text, limit)


def render(
    palette: Sequence[PaletteEntry],
    *,
    paint: Callable[[str, str], str] | None = None,
    width: int = 80,
    title: str = "commands",
    show_detail: bool = False,
    ellipsis: str = "…",
) -> str:
    """The ``/`` menu as text — sections, **one aligned row per command**, an honest mark.

    Three columns and nothing else: the spelling, the command's one-line purpose, its
    state (``ready`` / ``not wired`` / ``flagged: ACT`` / ``unverified``). The purpose is
    clipped to whatever is left of ``width`` so the state column stays aligned; nothing is
    printed that is not in the palette. ``show_detail=True`` adds the target command and the
    probe's reason underneath — that is what ``/help <name>`` is for, not the default look.
    """
    style = paint or _IDENTITY
    rows = list(palette)
    lines: list[str] = []
    if title:
        lines.append(style(title, "bold"))
    if not rows:
        lines.append(style("  (no slash command matches that)", "dim"))
        return "\n".join(lines)
    by_group: dict[str, list[PaletteEntry]] = {}
    for entry in rows:
        by_group.setdefault(entry.command.group, []).append(entry)
    mark_width = max((len(e.mark()) for e in rows), default=0)
    # The name column fits the longest spelling in *this* palette, so the state column stays
    # aligned however long a verb name gets (`/memory-retrieval-check` is 23 characters — a
    # fixed 14 would push its mark 9 columns to the right of everyone else's). The cap is
    # arithmetic, not taste: it leaves room for the smallest honest purpose width and the
    # widest state mark inside ``width``.
    name_width = max(8, min(max(len(e.command.slash) for e in rows) + 2,
                            max(8, width - 4 - mark_width - 22)))
    purpose_width = max(20, width - 4 - name_width - mark_width - 2)
    for key, label in GROUPS:
        group_rows = by_group.get(key)
        if not group_rows:
            continue
        lines.append(style(f"  {label}", "dim"))
        for entry in group_rows:
            command = entry.command
            left = f"    {command.slash:<{name_width}}"
            purpose = short_text(command.title, purpose_width, ellipsis=ellipsis)
            lines.append(
                f"{left}{purpose:<{purpose_width}} "
                f"{style(entry.mark(), _state_role(entry.state))}".rstrip()
            )
            if show_detail:
                if command.usage:
                    lines.append(style(f"      {command.usage}  {SLASH_ARROW}  "
                                       f"{command.spelling}", "dim"))
                if entry.detail:
                    lines.append(style(f"      why: {entry.detail}", "dim"))
    for key, group_rows in by_group.items():
        if key in {group for group, _label in GROUPS}:
            continue
        lines.append(style(f"  {key}", "dim"))
        for entry in group_rows:
            lines.append(f"    {entry.command.slash:<{name_width}}"
                         f"{short_text(entry.command.title, purpose_width, ellipsis=ellipsis)}")
    return "\n".join(lines)


#: the arrow between a slash command and the real command behind it (ASCII-safe textual
#: arrow on purpose: the palette is text, not art)
SLASH_ARROW = "->"


def _state_role(state: str) -> str:
    """The paint token for a state (semantic names — the theme decides what they look like)."""
    return {
        STATE_READY: "ok",
        STATE_BLOCKED: "warn",
        STATE_UNVERIFIED: "warn",
        STATE_PLANNED: "warn",
        STATE_UNWIRED: "fail",
    }.get(state, "dim")


def mapping(
    tree: Any = None,
    *,
    reachable: dict[str, bool] | None = None,
    report: dict | None = None,
    tree_error: str = "",
) -> list[dict[str, Any]]:
    """The whole slash table as data: what is real, what is built-in, what is planned.

    One row per verb — ``kind`` is ``real`` (a command path that exists in this build's
    parser), ``builtin`` (answered by the shell itself), ``unwired`` (a command path this
    build does not carry) or ``planned`` (no verb exists yet, with the reason and the
    nearest real commands). This is what a doc, a test or ``skaldir chat verbs`` quotes,
    so the table cannot drift between them.
    """
    rows: list[dict[str, Any]] = []
    for entry in entries(tree, reachable=reachable, report=report, tree_error=tree_error):
        command = entry.command
        if command.planned:
            kind = STATE_PLANNED
        elif not command.argv:
            kind = "builtin"
        elif tree is None or tree_error:
            kind = "unverified"
        elif _wired(tree, command.argv):
            kind = "real"
        else:
            kind = STATE_UNWIRED
        rows.append(
            {
                "slash": command.slash,
                "kind": kind,
                "group": command.group,
                "layer": entry.layer,
                "command": command.spelling,
                "path": command.command_path,
                "usage": command.usage,
                "purpose": command.title,
                "state": entry.state,
                "reason": entry.reason,
                "planned_reason": command.planned,
                "nearest": list(command.nearest),
                "aliases": [SLASH + alias for alias in command.aliases],
                # the completion slot of this verb (what Tab offers after it) and the real
                # flag a bare value is folded into — declared, so a client can show both
                "values": command.values,
                "value_flag": command.value_flag,
                # the rest of the line is ONE value for the command's own positional
                # (/locking), and the environment fact the verb needs (a live desktop for
                # /computer-use) — both declared here, so a shell prints them instead of
                # guessing, and a test can hold them down
                "value_rest": bool(command.value_rest),
                "precondition": command.precondition,
            }
        )
    return rows


def hint_line(
    tree: Any = None,
    report: dict | None = None,
    *,
    paint: Callable[[str, str], str] | None = None,
    width: int = 80,
    tree_error: str = "",
) -> str:
    """The honest "what can I do from here" line, derived from the real selftest report.

    ``report`` is the dict :func:`cli_contract.run_selftest` returns. Without it the
    line says the probe did not run — it never invents a capability.
    """
    style = paint or _IDENTITY
    if tree_error:
        return style(
            f"the CLI parser did not load ({_short(tree_error, 80)}) — no command can be "
            "checked against it right now",
            "warn",
        )
    if report is None:
        return style(
            "capability probe did not run — /status runs the real selftest", "dim"
        )
    reachable: dict[str, bool] = {}
    for record in report.get("layers", []):
        reachable[str(record.get("layer", "")).upper()] = bool(record.get("ok"))
    palette = entries(tree, reachable=reachable, report=report)
    ready = [entry.command.slash for entry in palette if entry.state == STATE_READY]
    blocked = [
        f"{entry.command.slash} ({entry.layer or 'layer'} flagged by selftest)"
        for entry in palette
        if entry.state == STATE_BLOCKED and entry.command.argv
    ]
    unwired = [
        entry.command.slash
        for entry in palette
        if entry.state == STATE_UNWIRED
    ]
    planned = [entry.command.slash for entry in palette if entry.state == STATE_PLANNED]
    parts: list[str] = []
    if ready:
        parts.append(style("ready: " + " ".join(ready), "ok"))
    if planned:
        parts.append(style("planned (not built): " + " ".join(planned), "warn"))
    if blocked:
        parts.append(style("flagged: " + ", ".join(blocked), "warn"))
    if unwired:
        parts.append(style("not wired here: " + " ".join(unwired), "fail"))
    summary = style(
        f"selftest: {len(report.get('layers', []))} layer(s), "
        f"{report.get('failed', 0)} not reachable",
        "dim",
    )
    parts.append(summary)
    return "\n".join(part[:width] for part in parts)


#: help lines are printed verbatim by the shell, so prose is wrapped here (a statement like
#: a verb's precondition must not run off the terminal).
HELP_WIDTH = 88


def _wrap_help(text: str, width: int = HELP_WIDTH) -> list[str]:
    """Wrap one help statement; an empty text yields nothing (never a blank label)."""
    body = " ".join(str(text or "").split())
    if not body:
        return []
    return textwrap.wrap(body, width=max(20, width)) or [body]


def _values_label(kind: str) -> str:
    """A human label for a value kind (never a bare internal token)."""
    return {
        "session": "session ids (from the session store)",
        "checkpoint": "checkpoint ids (from the state dir)",
        "run": "run ids (from the run transcripts)",
        "goal": "goal ids (from runs.db)",
        "model": "model ids (from the provider catalog)",
        "provider": "provider ids (from the catalog and config.yaml)",
        "workflow": "workflow ids (from the declared workflow contracts)",
        "file": "file paths (from the working tree, @path)",
    }.get(str(kind), str(kind))


def _verb_children(tree: Any, argv: Sequence[str]) -> list[str]:
    """The sub-verbs of the mapped command that really exist here (``verify status`` → …)."""
    if tree is None or not argv:
        return []
    from .complete import node_for

    node, leftovers = node_for(
        tree, [str(token) for token in argv if not str(token).startswith("-")]
    )
    if leftovers:
        return []
    return sorted(getattr(node, "children", {}).keys())


def help_text(command: SlashCommand, tree: Any = None) -> str:
    """The ``/help <name>`` text for one slash command (itself derived, never invented)."""
    lines = [
        f"{command.slash} — {command.title}",
        f"  usage: {command.usage or command.slash}",
    ]
    if command.planned:
        lines.append("  runs:  nothing — this verb is not built yet")
        lines.append(f"  why:   {command.planned}")
        if command.nearest:
            instead = ", ".join(f"`skaldir {item}`" for item in command.nearest)
            lines.append(f"  instead: {instead}")
        lines.append(
            "  state: planned — the shell refuses it and never substitutes another verb"
        )
        if command.detail:
            lines.append(f"  note:  {command.detail}")
        return "\n".join(lines)
    if command.argv:
        lines.append(f"  runs:  {command.spelling}")
    else:
        lines.append("  runs:  in the shell itself (built-in)")
    if command.aliases:
        lines.append("  also:  " + ", ".join(SLASH + alias for alias in command.aliases))
    if command.value_flag:
        lines.append(
            f"  takes: a bare <value> becomes `{command.value_flag} <value>` "
            f"(/resume s-1234 -> skaldir session resume {command.value_flag} s-1234)"
            if command.name == "resume"
            else f"  takes: a bare <value> becomes `{command.value_flag} <value>`"
        )
    if command.value_rest:
        lines.append(
            "  takes: the whole rest of the line as ONE value — the mapped command's own "
            "positional"
        )
        lines.append(
            f"         `{command.slash} ` fix the parser  ->  skaldir "
            f"{' '.join(str(token) for token in command.argv)} \"fix the parser\""
            + (f" {' '.join(command.flags)}" if command.flags else "")
            + "; a flag ends the value, so `/locking fix it --max-turns 2` keeps the flag"
        )
    if command.values:
        lines.append(f"  Tab:   completes {_values_label(command.values)}")
    children = _verb_children(tree, command.argv)
    if children:
        lines.append(
            f"  verbs: {' · '.join(children)} — a word that names one becomes the verb "
            f"(`{command.slash} {children[0]}` -> skaldir "
            f"{' '.join(str(token) for token in command.argv[:-1])} {children[0]})"
        )
    if command.needs:
        lines.append(f"  needs: {command.needs}")
        for form in command.needs_forms:
            lines.append(f"         {form}")
    if command.precondition:
        # a precondition is a statement, not a line: it is wrapped here (the shell prints
        # help lines verbatim, so an unwrapped paragraph would run off any terminal)
        for index, row in enumerate(_wrap_help(command.precondition)):
            label = "  precondition: " if index == 0 else " " * 16
            lines.append(f"{label}{row}")
    if command.detail:
        lines.append(f"  note:  {command.detail}")
    if command.argv and not _wired(tree, command.argv):
        lines.append(
            "  state: not wired into this build — the palette marks it, the shell "
            "refuses it instead of guessing a verb"
        )
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# the top-level help: curated by default, complete on request
# --------------------------------------------------------------------------- #
#
# The owner's ask for this round was "fewer top-level commands, more `/` commands". The
# capability may not shrink for the sake of the list: every root command stays in the
# parser, keeps its handler and stays reachable — the *view* is what gets curated.
# ``skaldir -h`` shows the dozen commands a session actually types, and both
# ``skaldir --all-commands`` and ``skaldir ? <command>`` are one line away.

#: The commands a session actually types, in the order they are learned. This is the **taught**
#: surface (owner redesign, 2026-09-19): the chat, setup, the goal run, the API surface and the
#: help/version flags. Everything else stays reachable and contracted — it is simply no longer
#: taught at the top level, because it lives in the chat as a ``/`` verb.
#:
#: ``""`` and ``"--help"`` are *views*, not commands: the bare call (the chat) and the two root
#: flags. They are named here because a taught list that omits the entry point of this build
#: would be a worse lie than a list with two non-command lines in it; the two synthetic entries
#: are declared in :data:`CURATED_VIEWS` so a test can tell them apart from real commands.
CURATED: tuple[tuple[str, str], ...] = (
    ("", "the chat: the mark, the band, the two bars, the palette"),
    ("setup", "one-time setup: the two keys, each proved by a call"),
    ("run", 'sets the goal and opens the chat attached to it'),
    ("serve", "the API surface for editors and GUIs (--http)"),
    ("--help", "this screen; --version; --all-commands"),
)

#: The curated entries that are not root commands (the bare call and the root flags).
CURATED_VIEWS: tuple[str, ...] = ("", "--help")

#: How many root commands the chat's ``/`` verbs can reach. Measured from the real tree where a
#: tree is available; the curated screen never claims a number it did not measure.


def _leaf_count(node: Any) -> int:
    """How many leaf commands hang under ``node`` (a childless command counts as one)."""
    children = getattr(node, "children", {}) or {}
    if not children:
        return 1
    return sum(_leaf_count(child) for child in children.values())


def _root_names(tree: Any) -> list[str]:
    return sorted(getattr(tree, "children", {}) or {})


def _inventory(tree: Any) -> tuple[int, int]:
    """``(root commands, leaf commands)`` of this build, read from the real tree."""
    roots = list((getattr(tree, "children", {}) or {}).values())
    return len(roots), sum(_leaf_count(child) for child in roots)


def _version() -> str:
    try:
        from . import __version__

        return str(__version__)
    except Exception:  # noqa: BLE001
        return ""


def curated_help(
    tree: Any = None,
    *,
    width: int = 80,
    paint: Callable[[str, str], str] | None = None,
) -> str:
    """The default ``skaldir -h`` screen: the five lines a session is taught.

    ASCII on purpose (this text is printed on a pipe, into logs and into the README), and derived
    from the real tree where it states a count: ``65 root commands, 267 leaf commands`` is
    measured, not typed. Nothing was removed from the build to make this list short — every root
    command is still reachable and still behind ``--all-commands``; they live in the chat as
    ``/`` verbs, and ``/help`` lists them there.
    """
    style = paint or _IDENTITY
    version = _version()
    head = f"skaldir {version} - the Skaldir CLI".rstrip()
    roots = leaves = 0
    if tree is not None:
        roots, leaves = _inventory(tree)
        head += f" ({roots} root commands, {leaves} leaf commands in this build)"
    known = set(_root_names(tree)) if tree is not None else set()
    lines = [style(head, "bold"), ""]
    lines.append(style("  the commands a session is taught", "dim"))
    for name, note in CURATED:
        missing = ""
        if name not in CURATED_VIEWS and tree is not None and name not in known:
            missing = "  (not in this build)"
        shown = "skaldir" if not name else (name if name.startswith("-") else f"skaldir {name}")
        if name == "run":
            shown = 'skaldir run "<goal>"'
        lines.append(f"    {shown:<24} {note}{missing}")
    lines.append("")
    lines.append(style("  everything else lives in the chat", "dim"))
    lines.append("    /help                    every slash verb, grouped, with its command")
    lines.append("    /sessions /resume /goal /run /steer /queue /stop /status")
    lines.append("    /verify /runs /checkpoint /context /model /provider /tools")
    lines.append("    /memory /agents /jev /host /docker   (all of them: /help)")
    lines.append("    skaldir chat verbs --json      each slash verb + the command it runs")
    lines.append("")
    lines.append(style("  the full surfaces", "dim"))
    lines.append("    skaldir --all-commands        every root command this build carries")
    lines.append("    skaldir ? <command>          the overlay: usage, flags, exit codes")
    lines.append("    skaldir <command> --help     that command's own parser text")
    lines.append("    skaldir contract dump --json the whole inventory + a JSON schema each")
    if tree is not None:
        hidden = max(0, roots - len([1 for name, _note in CURATED if name not in CURATED_VIEWS]))
        lines.append(
            style(
                f"    ({hidden} more root command(s) stay reachable - nothing was removed from "
                "this build; they are / verbs in the chat)",
                "dim",
            )
        )
    lines.append("")
    lines.append(style(exit_vocabulary(), "dim"))
    return "\n".join(_clip(line, width) for line in lines)


def full_help(
    tree: Any = None,
    *,
    width: int = 80,
    paint: Callable[[str, str], str] | None = None,
    per_group: bool = False,
) -> str:
    """Every root command of this build with its one-liner — the ``--all-commands`` view.

    ``per_group`` (``?``) adds how many leaf commands hang under each root, so the list
    shows the depth that exists without printing all 257 leaves.
    """
    style = paint or _IDENTITY
    version = _version()
    lines: list[str] = []
    if tree is None:
        return style("the CLI parser did not load — no command list available here", "warn")
    roots, leaves = _inventory(tree)
    lines.append(style(f"skaldir {version} — every root command ({roots})".rstrip(), "bold"))
    lines.append("")
    width_name = max((len(name) for name in _root_names(tree)), default=8)
    for name in _root_names(tree):
        child = tree.children[name]
        note = short_text(child.summary or child.description, max(20, width - width_name - 24))
        count = _leaf_count(child)
        if per_group:
            lines.append(f"    {name:<{width_name}}  {count:>3} command(s)  {note}".rstrip())
        else:
            lines.append(f"    {name:<{width_name}}  {note}".rstrip())
    lines.append("")
    lines.append(
        style(
            f"    {leaves} leaf command(s) in total; every one of them runs "
            "`skaldir <command> …`",
            "dim",
        )
    )
    lines.append(style("    skaldir -h        back to the curated list", "dim"))
    lines.append(style("    skaldir ? <cmd>   the overlay for one command", "dim"))
    return "\n".join(_clip(line, width) for line in lines)


def _clip(line: str, width: int) -> str:
    """One help line, clipped to ``width`` with an ASCII ellipsis (never mid-word noise).

    Not :func:`short_text`: that collapses whitespace, which would flatten the indentation
    of the help tables. This one keeps the line byte-identical when it fits.
    """
    text = str(line)
    if width <= 0 or len(text) <= width:
        return text
    if width <= 3:
        return text[:width]
    return text[: width - 3].rstrip() + "..."


def terminal_width(default: int = 80) -> int:
    """The terminal's width, or ``default`` on a pipe (the help text never wraps badly)."""
    try:
        import shutil

        return max(40, int(shutil.get_terminal_size((default, 24)).columns))
    except Exception:  # noqa: BLE001
        return default


class CuratedHelpAction(argparse.Action):
    """``argparse`` action for the root ``-h``/``--help``: print the curated list, exit 0.

    Defined here (not in ``cli.py``) so the *curated text* and the *curated action* are one
    thing: ``cli.build_parser()`` adds this action and nothing else, and the wording, the
    counts and the two escape hatches stay with :func:`curated_help`.
    """

    def __init__(
        self,
        option_strings: Sequence[str],
        dest: str = "help",
        **kwargs: Any,
    ) -> None:
        kwargs.pop("nargs", None)
        super().__init__(option_strings, dest, nargs=0, **kwargs)

    def __call__(
        self, parser: Any, namespace: Any, values: Any, option_string: str | None = None
    ) -> None:
        from .complete import build_tree

        print(curated_help(build_tree(parser), width=terminal_width()))
        exit_ = getattr(parser, "exit", None)
        if callable(exit_):
            exit_(0)
        raise SystemExit(0)


def exit_vocabulary() -> str:
    """The exit-code line, read from the contract when it is importable."""
    names = getattr(_CONTRACT, "EXIT_NAMES", None)
    if isinstance(names, dict) and names:
        parts = " | ".join(f"{code} {str(names[code]).lower()}" for code in sorted(names))
        return "Exit codes: " + parts + "."
    return ("Exit codes: 0 ok | 1 usage | 2 verification failed | 3 blocked | "
            "4 needs credentials | 5 not found | 6 conflict | 7 internal | 8 unknown.")


def keys_text(
    *,
    width: int = 80,
    paint: Callable[[str, str], str] | None = None,
    history_file: str = "",
) -> str:
    """``/keys``: the bindings that are really installed, and what each one does.

    Two blocks, both facts: what :func:`skaldir.complete.install_readline` binds in
    ``readline`` (Tab completion, ambiguity, case), and the keys the *shell* handles itself
    (``repl``): ``?``, Esc, Ctrl-C, Ctrl-D, the arrows. Nothing is listed that this build
    does not implement.
    """
    style = paint or _IDENTITY
    lines = [style("keys", "bold"), ""]
    lines.append(style("  readline (installed by complete.install_readline)", "dim"))
    lines.append("    Tab                complete the token: commands, flags, values, @paths")
    lines.append("    Tab (twice)        show the menu when several candidates match")
    lines.append("    Shift-Tab          (not bound — the menu is a list, not a cycle)")
    lines.append("    show-all-if-ambiguous / completion-ignore-case are bound on")
    lines.append("    ↑ / ↓              history, kept in the session's history file")
    if history_file:
        lines.append(f"                       {history_file}")
    lines.append("")
    lines.append(style("  the shell itself (repl)", "dim"))
    lines.append("    ? [command]        the inline overlay: usage, flags, contract facts")
    lines.append("    Esc                dismiss the palette, run nothing")
    lines.append("    Ctrl-C             cancel the running command (the session stays)")
    lines.append("    Ctrl-D             leave the session (same as /quit)")
    lines.append("    Enter              run the line as `skaldir <line>`")
    lines.append("")
    lines.append(
        style(
            "  a plain line is a skaldir command; / opens the palette; prose goes to the "
            "agent loop",
            "dim",
        )
    )
    return "\n".join(_clip(line, width) for line in lines)


def shlex_split(line: str) -> list[str]:
    """Split a shell line the way the CLI will see it (quotes honoured, never raising)."""
    try:
        return shlex.split(str(line))
    except ValueError:
        return str(line).split()


def _probe_tree() -> Any:
    """The real parser tree for the standalone probes (``None`` when it will not load)."""
    try:
        return _default_tree()
    except Exception:  # noqa: BLE001
        return None


def main(argv: Sequence[str] | None = None) -> int:
    """``python -m skaldir.slash [query]`` — print the palette (read-only probe).

    ``--all-commands`` prints the full root list, ``--keys`` the key bindings, and a bare
    word is looked up as a slash verb (its ``/help`` text).
    """
    import sys

    args = list(sys.argv[1:] if argv is None else argv)
    tree = _probe_tree()
    if args[:1] == ["--all-commands"]:
        print(full_help(tree, per_group=True))
        return 0
    if args[:1] == ["--keys"]:
        print(keys_text())
        return 0
    if args[:1] == ["--curated"]:
        print(curated_help(tree))
        return 0
    query = args[0] if args else ""
    if not query:
        print(render(entries(tree)))
        return 0
    invocation = resolve(query, tree)
    if invocation is None:
        print(f"unknown: {query} — closest: {', '.join(close_names(query))}")
        return 1
    print(help_text(invocation.command, tree))
    return 0


__all__ = [
    "BY_NAME",
    "CURATED",
    "CURATED_VIEWS",
    "GROUPS",
    "SLASH",
    "SLASH_ARROW",
    "SLASH_COMMANDS",
    "STATE_BLOCKED",
    "STATE_PLANNED",
    "STATE_READY",
    "STATE_UNWIRED",
    "STATE_UNVERIFIED",
    "PaletteEntry",
    "PaletteMenu",
    "PLAIN_TRANSLATION",
    "SlashCommand",
    "SlashInvocation",
    "ascii_safe",
    "close_names",
    "command_for",
    "curated_help",
    "entries",
    "exit_vocabulary",
    "full_help",
    "hint_line",
    "help_text",
    "keys_text",
    "main",
    "mapping",
    "matched",
    "names",
    "render",
    "resolve",
    "shlex_split",
    "short_text",
    "spellings",
    "suggestions",
]


if __name__ == "__main__":  # pragma: no cover — a read-only probe, not a product path
    raise SystemExit(main())
