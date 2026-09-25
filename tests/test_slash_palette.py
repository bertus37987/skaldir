"""The ``/`` palette: every slash command names a real command group, honestly marked.

What these tests hold down:

* the registry maps the eleven slash commands onto **existing** command paths
  (``/jev`` → ``jev selftest``, ``/runs`` → ``runs status``, …) and nothing here
  re-implements a command;
* the palette's state comes from the real parser tree and the real ``selftest``
  report: a group that is not wired in this build is ``unwired`` (and refused), a
  layer the probe flags is ``blocked`` (warned, still runnable), and a parser that
  could not be loaded marks everything ``unverified`` — never "ready";
* the fuzzy ``/`` menu ranks like the completion engine (one implementation);
* the wiring contract holds: ``register_cli`` on the **real** parser adds ``shell``,
  registers its ``cli_contract.COMMANDS`` entry and is safe to call twice.
"""

from __future__ import annotations

import argparse
import functools
import io
from pathlib import Path

import pytest

from skaldir import cli, cli_contract, complete, repl, slash

#: the slash commands this build carries, and the real command path behind each.
#: Every entry that carries an argv must be a path that exists in this build's parser —
#: ``/plan`` is the one verb with no command behind it and it is marked ``planned``.
#: ``help``/``quit``/``keys``/``clear``/``diff``/``init`` are the shell's own (built-in):
#: a built-in has no argv because there is no command to run (``mapping()`` says ``builtin``).
EXPECTED = {
    "help": (),
    "new": ("session", "new"),
    "sessions": ("session", "list"),
    "resume": ("session", "resume"),
    "search": ("session", "search"),
    "export": ("session", "export"),
    "history": ("session", "show"),
    "fork": ("session", "fork"),
    "status": ("selftest",),
    "doctor": ("doctor",),
    "clear": (),
    "quit": (),
    "goal": ("goal", "show"),
    "locking": ("run",),
    "plan": (),  # planned: no argv, and `entries()` marks it instead of faking one
    "rename": (),  # planned: the store can retitle, no verb exposes it yet
    "newtask": (),  # planned: `session new --parent` + `context compact`, no one verb
    "verify": ("verify", "status"),
    "runs": ("runs", "status"),
    "workflows": ("workflows", "run"),
    "stop": ("agent", "cancel"),
    "retry": ("agent", "retry"),
    "review": ("voice", "check"),  # real verb now: one SUPERVISE pass over a trajectory
    "checkpoint": ("runs", "checkpoint"),
    "snapshots": ("checkpoint", "list"),
    "approve": ("approval", "allow"),
    "deny": ("approval", "deny"),
    "restore": ("checkpoint", "restore"),
    # the picker's own verb (the model_picker hook answers /model; it used to map
    # onto `provider tiers`)
    "model": ("model",),
    "provider": ("provider", "list"),
    "setup": ("setup",),
    "cost": ("coding", "status"),
    "memory": ("memory", "scope"),
    "context": ("context", "usage"),
    "compact": ("context", "compact"),
    "limit": ("context", "usage"),
    "diff": (),
    "undo": ("edit", "undo"),
    "init": (),
    "tools": ("tools", "list"),
    "skills": ("skills", "list"),
    "skills-promote": ("skills", "promote"),
    "scan": ("security", "audit"),
    "exec": ("code", "run"),
    "jev": ("jev", "selftest"),
    "act": ("act", "check"),
    "computer-use": ("act", "check"),
    "compose": ("compose", "show"),
    "agent": ("agent", "list"),
    "telemetry": ("telemetry", "show"),
    "keys": (),
    "debug": (),  # planned: nothing uploads a bundle; doctor/telemetry/contract stay local
    # -- the owner's redesign (2026-09-19): the features, in the chat ------- #
    "run": ("run",),
    "criteria": ("goal", "criteria"),
    "steer": (),   # builtin: the durable message channel into the attached run
    "queue": (),   # builtin: the same channel, drained in order
    "selftest": ("selftest",),
    "prompt": ("prompt", "explain"),
    "apply": ("edit", "apply"),
    "map": ("code", "map"),
    "find": ("code", "search"),
    "symbols": ("code", "symbols"),
    "diagnostics": ("code", "diagnostics"),
    "testfix": ("coding", "testfix"),
    "mine": ("tools", "mine"),
    "register": ("tools", "register"),
    "health": ("tools", "health"),
    "rollback": ("tools", "rollback"),
    "flows": ("verify", "flows"),
    "adversarial": ("adversarial", "selftest"),
    "audit": ("security", "audit"),
    "vision": ("vision", "capabilities"),
    "remember": ("memory", "remember"),
    "recall": ("memory", "recall"),
    "forget": ("memory", "forget"),
    "agents": ("agent", "list"),
    "spawn": ("agent", "spawn"),
    "collect": ("agent", "collect"),
    "cancel": ("agent", "cancel"),
    "host": ("host", "status"),
    "docker": ("docker", "ensure"),
    # -- the reachability round (2026-09-19): the twenty roots the audit found -------- #
    # (findings F5/F6 of `08-reachability.md`): each of these mapped onto the real verb
    # resolved above, never a rename and never a guess.
    "image": ("image", "list"),
    "mcp": ("mcp", "map"),
    "tools-core": ("tools-core", "list"),
    "tools-hub": ("tools-hub",),
    "permission": ("permission", "show"),
    "completion": ("completion", "status"),
    "spend": ("spend", "list"),
    "stream": ("stream", "selfcheck"),
    "registry": ("registry", "status"),
    "memory-stats": ("memory-stats",),
    "memory-search": ("memory-search",),
    "memory-audit": ("memory-audit",),
    "memory-retrieval-check": ("memory-retrieval-check",),
    "metrics": ("metrics",),
    "voice": ("voice", "status"),
    "warm": ("warm", "status"),
    "style": ("style", "logo"),
    "prompts": ("prompts", "list"),
    "ledger-show": ("ledger-show",),
    "pact-show": ("pact-show",),
}

#: The verbs the owner's redesign names, grouped the way his list groups them. Every one of them
#: must be in the palette — either with a real command behind it or (only where no real command
#: exists) marked ``planned`` with its nearest real commands. The surface is measured against this
#: list, not against whatever happens to be registered.
REQUESTED_REDESIGN = {
    "session": ("new", "sessions", "resume", "fork", "rename", "export", "search", "clear",
                "quit", "help", "keys"),
    "goal/work": ("goal", "criteria", "run", "steer", "queue", "stop", "status", "runs",
                  "verify", "checkpoint"),
    "model": ("model", "provider", "setup", "cost", "telemetry"),
    "context": ("context", "compact", "limit", "prompt"),
    "code": ("diff", "undo", "apply", "init", "map", "find", "symbols", "diagnostics",
             "testfix", "exec"),
    "tools": ("tools", "mine", "register", "health", "rollback", "compose", "skills"),
    "quality": ("verify", "flows", "adversarial", "audit", "selftest"),
    "gui": ("computer-use", "vision"),
    "memory": ("memory", "remember", "recall", "forget"),
    "subagents": ("agents", "spawn", "collect", "cancel"),
    "decision": ("jev",),
    "host": ("host",),
    "containers": ("docker",),
}

#: The verbs the owner's ask names, grouped the way the round's plan groups them. Every one
#: of them must be in the palette — the surface is measured against this list, not against
#: whatever happens to be registered.
REQUESTED = {
    "session/memory": ("new", "sessions", "resume", "search", "export", "rename"),
    "model/provider": ("model", "provider", "setup", "cost"),
    "goal/work": ("goal", "plan", "status", "runs", "checkpoint", "verify", "stop"),
    "context": ("context", "compact", "limit"),
    "code": ("diff", "undo", "init", "tools", "exec"),
    "help": ("help", "keys", "quit"),
    # the runtime's own verbs of this round: the goal lock, the isolated desktop, the
    # security audit, one run's telemetry record, the deployment check
    "runtime": ("locking", "computer-use", "scan", "telemetry", "doctor"),
}

#: The Hermes/Cline spellings this round folded onto the same real verb (one command, more
#: than one name): a spelling that resolves to the right verb is the whole point of the alias.
ALIASES = {
    "/compress": "compact",     # Hermes /compress
    "/smol": "compact",         # Cline /smol
    "/branch": "fork",          # Hermes /branch
    "/snapshot": "snapshots",   # Hermes /snapshot
    "/title": "rename",         # Hermes /title
    "/deep-planning": "plan",   # Cline /deep-planning
    "/reportbug": "debug",      # Cline /reportbug
    "/cu": "computer-use",      # the CLI's own `cu` group
    "/ls": "sessions",          # the session list, both spellings
    # NOT `/rollback` any more: the owner's redesign moved that name to the capability layer
    # (`tools rollback`), and `/restore` (checkpoint restore) is its own verb
}


@pytest.fixture(scope="module")
def tree():
    return tree_of()


@functools.lru_cache(maxsize=1)
def tree_of():
    """The real parser tree, built once for this module (the surface's ground truth)."""
    return complete.build_tree(cli.build_parser())


def _report(ok_layers: set[str], failed: set[str] | None = None) -> dict:
    layers = []
    for layer in cli_contract.LAYERS:
        ok = layer in ok_layers
        layers.append(
            {
                "layer": layer,
                "ok": ok,
                "blocked_reason": None if ok else f"{layer} probe failed",
            }
        )
    return {"layers": layers, "failed": sum(1 for layer in layers if not layer["ok"])}


# --------------------------------------------------------------------------- #
# the registry maps onto real commands
# --------------------------------------------------------------------------- #


def test_every_requested_slash_command_exists():
    assert set(slash.names()) == set(EXPECTED)


def test_the_whole_vocabulary_the_ask_names_is_present():
    """The six groups of the owner's ask, verb by verb (a missing verb fails by name)."""
    names = set(slash.names())
    missing = {
        group: [verb for verb in verbs if verb not in names]
        for group, verbs in REQUESTED.items()
    }
    assert {group: verbs for group, verbs in missing.items() if verbs} == {}


def test_the_redesign_vocabulary_is_present_verb_by_verb():
    """The owner's redesign (2026-09-19): every named verb, in every group he named."""
    names = set(slash.names())
    missing = {
        group: [verb for verb in verbs if verb not in names]
        for group, verbs in REQUESTED_REDESIGN.items()
    }
    assert {group: verbs for group, verbs in missing.items() if verbs} == {}
    # and each of them either runs a real command, or is a shell builtin, or is planned with
    # its nearest real commands named — never an empty verb that pretends to do something
    for group, verbs in REQUESTED_REDESIGN.items():
        for verb in verbs:
            command = slash.command_for(verb)
            assert command is not None, f"{group}: /{verb} is missing"
            if command.planned:
                assert command.nearest, f"/{verb} is planned without naming a real command"
            elif command.argv:
                assert command.command_path in set(
                    complete.known_paths(tree_of())
                ), f"/{verb} claims {command.command_path}, which is not wired"
            else:
                assert command.builtin or command.planned, (
                    f"/{verb} has no argv and is neither a builtin nor planned"
                )


@pytest.mark.parametrize("name,argv", sorted(EXPECTED.items()))
def test_slash_command_maps_onto_the_existing_group(name, argv, tree):
    command = slash.command_for(name)
    assert command is not None, name
    assert command.argv == argv
    if command.planned:  # a planned verb carries no argv and says why
        assert argv == ()
        assert command.planned
        assert command.nearest
        return
    if not argv:  # the built-ins (help/quit/keys/clear/diff/init) live in the shell itself
        assert command.builtin
        return
    # the argv it names is a real path in the real parser (nothing invented)
    path = command.command_path
    assert path in set(complete.known_paths(tree)), f"/{name} claims {path}, which is not wired"
    node, leftovers = complete.node_for(tree, list(argv))
    assert node.path == path
    assert not leftovers


def test_a_declared_value_slot_names_a_real_flag_of_the_mapped_verb():
    """``value_flag``/``values`` are claims about the real command: they are checked too."""
    for command in slash.SLASH_COMMANDS:
        if not command.value_flag:
            continue
        node, _left = complete.node_for(tree_of(), list(command.argv))
        option = node.option(command.value_flag)
        assert option is not None, (
            f"/{command.name} declares {command.value_flag}, not a flag of {node.path}"
        )
        assert option.takes_value


def test_resolve_passes_arguments_through_and_folds_declared_slots():
    """Three declared shapes: the default verb, a sub-verb, and a value folded into its flag."""
    # 1. the default verb + pass-through arguments
    plain = slash.resolve("/sessions --limit 3")
    assert plain.argv == ("session", "list", "--limit", "3")
    assert plain.mode == "default"
    # 2. a word that names a real sub-verb *becomes* the verb (the usage strings advertise
    #    `/verify run`, `/tools show NAME` — appending would have made them unrunnable)
    invoked = slash.resolve("/verify run --json")
    assert invoked.argv == ("verify", "run", "--json")
    assert invoked.mode == "verb"
    assert slash.resolve("/tools show cap-x --json").argv == ("tools", "show", "cap-x", "--json")
    assert slash.resolve("/runs checkpoint").argv == ("runs", "checkpoint")
    # a word that is NOT a sub-verb is appended verbatim, exactly as before
    assert slash.resolve("/tools cap-x --json").argv == (
        "tools", "list", "cap-x", "--json",
    )
    # 3. a bare value takes the verb's declared flag
    resumed = slash.resolve("/resume s-1234")
    assert resumed.argv == ("session", "resume", "--session", "s-1234")
    assert resumed.mode == "value" and resumed.value_flag == "--session"
    assert slash.resolve("/exec src/x.py").argv == ("code", "run", "--file", "src/x.py")
    # an explicit flag is never double-folded
    assert slash.resolve("/resume --session s-1").argv == (
        "session", "resume", "--session", "s-1",
    )


def test_the_rest_slot_takes_the_whole_goal_and_a_flag_ends_it():
    """/locking's value is a *sentence*: the words up to the first flag become ONE token.

    The chat's one-word slot would have handed `skaldir run` the word "fix" (the `/review`
    rejection, one round later). The rest slot is declared on the verb and reported by
    ``resolve`` as ``mode == "rest"``, so the substitution is visible, not silent.
    """
    plain = slash.resolve("/locking fix the parser")
    assert plain.argv == ("run", "fix the parser", "--progress")
    assert plain.mode == "rest"
    # quotes are honoured (shlex), and a flag ends the goal instead of joining it
    quoted = slash.resolve('/locking "fix the parser" --max-turns 2')
    assert quoted.argv == ("run", "fix the parser", "--progress", "--max-turns", "2")
    assert quoted.mode == "rest"
    scoped = slash.resolve("/locking make the tree clean --criteria /tmp/c.json")
    assert scoped.argv == (
        "run", "make the tree clean", "--progress", "--criteria", "/tmp/c.json",
    )
    # a line that starts with a flag is a flag line, not a goal: --resume continues a goal
    resumed = slash.resolve("/locking --resume g-1a2b")
    assert resumed.argv == ("run", "--progress", "--resume", "g-1a2b")
    assert resumed.mode == "default"
    # the rest slot is not a second way to spell the value slot: the prose verbs declare it
    # (/locking, and the two the redesign added: /run for a goal, /spawn for an assignment)
    assert [item.name for item in slash.SLASH_COMMANDS if item.value_rest] == [
        "locking", "run", "spawn",
    ]
    # …and the verb it maps onto really has that positional
    node, leftovers = complete.node_for(tree_of(), ["run"])
    assert not leftovers and [item.dest for item in node.positionals] == ["goal"]


def test_the_goal_locked_verb_refuses_without_a_goal_and_names_the_real_forms():
    """No goal → refused, with the real commands that carry a goal, a criterion and a gate."""
    command = slash.command_for("locking")
    assert command.argv == ("run",)
    assert command.needs and "goal" in command.needs
    assert command.needs_forms
    for form in command.needs_forms:
        assert form.startswith("skaldir "), form
    text = slash.help_text(command, tree_of())
    assert "skaldir run" in text and "skaldir goal criteria" in text
    assert "skaldir completion status" in text
    # the real command behind it exists, is registered and declares the ENFORCE layer
    assert command.command_path == "run"
    assert command.command_path in set(complete.known_paths(tree_of()))
    assert cli_contract.COMMANDS["run"].layer == "ENFORCE"
    # it runs in the foreground with the live progress lines on (the frame is not a TTY)
    assert command.flags == ("--progress",)
    # …and it says what really stops it: `agent cancel` takes agent run ids, a goal run
    # writes none (measured), so the interrupt is the stop — no invented handle
    assert "^C" in command.detail and "/stop" not in command.detail
    bare = slash.resolve("/locking")
    assert bare.argv == ("run", "--progress")
    assert bare.extra == ()


def test_the_computer_use_verb_states_its_precondition_and_cannot_pretend():
    """/computer-use reports the real ACT surface and says what a live desktop would need."""
    command = slash.command_for("computer-use")
    assert command is not None and command.argv == ("act", "check")
    assert slash.command_for("cu") is command, "the CLI's `cu` spelling is the same verb"
    # the precondition is a statement, and it is the one the ACT probe measures
    assert command.precondition
    lowered = command.precondition.lower()
    for needle in ("live desktop", "isolated-only", "forbidden_by_policy", "never driven"):
        assert needle in lowered, needle
    text = slash.help_text(command, tree_of())
    assert "precondition:" in text
    # the real ACT path is reachable: `run`/`subtool` are siblings of `check` under `act`
    assert slash.resolve("/computer-use run actions.json").argv == ("act", "run", "actions.json")
    assert slash.resolve("/computer-use subtool list").argv == ("act", "subtool", "list")
    assert slash.resolve("/computer-use run a.json").mode == "verb"
    # …and the mapping carries the precondition verbatim, so a client prints it too
    row = next(item for item in slash.mapping(tree_of()) if item["slash"] == "/computer-use")
    assert row["precondition"] == command.precondition
    assert row["value_rest"] is False and row["kind"] == "real"
    # the report the verb runs is the real ACT probe (its own verdict, never ours)
    assert "act.check" in set(complete.known_paths(tree_of()))


def test_resolve_aliases_and_unknown_names():
    assert slash.resolve("/q").is_quit
    assert slash.resolve("/exit").is_quit
    assert slash.resolve("/h").command.name == "help"
    assert slash.resolve("/nope") is None
    assert slash.close_names("/verif")[0] == "/verify"
    assert slash.resolve("tools list") is None  # not a slash line


def test_a_hermes_or_cline_spelling_lands_on_the_same_real_verb():
    """An alias is a spelling, not a second implementation: it resolves to the same verb."""
    for spelling, name in ALIASES.items():
        invocation = slash.resolve(spelling)
        assert invocation is not None, spelling
        assert invocation.command.name == name, spelling
        # the alias runs exactly what the canonical spelling runs
        assert invocation.argv == slash.resolve("/" + name).argv, spelling
        assert spelling in slash.spellings()


def test_the_new_verbs_take_the_run_slot_a_bare_id_folds_into():
    """`/stop r-1` reaches `agent cancel r-1`: the id is a positional, so it passes through."""
    for spelling in ("/stop", "/retry"):
        invocation = slash.resolve(f"{spelling} run-proof-1")
        assert invocation.command.values == "run", spelling
        assert invocation.argv[:2] == ("agent", invocation.command.argv[1]), spelling
        assert invocation.argv[-1] == "run-proof-1", spelling
        assert invocation.mode == "default", spelling
    # `/history s-1` and `/fork s-1` fold a bare id into the verb's own --session flag
    for spelling in ("/history", "/fork"):
        invocation = slash.resolve(f"{spelling} s-1234")
        assert invocation.argv == ("session", invocation.command.argv[1], "--session", "s-1234")
        assert invocation.mode == "value" and invocation.value_flag == "--session", spelling
    # a sub-verb still wins over the value fold: `/workflows list` lists, it does not run
    assert slash.resolve("/workflows list").argv == ("workflows", "list")
    assert slash.resolve("/workflows list").mode == "verb"


def test_every_planned_verb_names_its_reason_and_the_real_commands():
    """A planned verb is refused with the why *and* the nearest real commands — never faked."""
    planned = [command for command in slash.SLASH_COMMANDS if command.planned]
    assert [command.name for command in planned] == [
        "plan", "rename", "newtask", "debug",
    ]
    for command in planned:
        assert command.argv == (), command.name
        assert command.nearest, command.name
        bare = [item for item in command.nearest if item and not item.startswith("skaldir ")]
        assert len(bare) == len(command.nearest), command.name
        text = slash.help_text(command, tree_of())
        assert "not built yet" in text or "planned" in text, command.name
        assert "instead:" in text, command.name
        entry = next(item for item in slash.entries(tree_of()) if item.command.name == command.name)
        assert entry.state == slash.STATE_PLANNED and entry.runnable is False


def test_every_slash_spelling_resolves():
    for spelling in slash.spellings():
        assert slash.resolve(spelling) is not None, spelling


def test_shlex_split_survives_unbalanced_quotes():
    # an unbalanced quote must not raise: the line is split plainly (and the command
    # itself will report the usage error)
    assert slash.shlex_split('/tools show "a b') == ["/tools", "show", '"a', "b"]
    assert slash.shlex_split("tools list") == ["tools", "list"]


# --------------------------------------------------------------------------- #
# fuzzy matches ("just type /je")
# --------------------------------------------------------------------------- #


def test_fuzzy_palette_ranking(tree):
    assert [item.command.name for item in slash.matched("/je", tree)] == ["jev"]
    assert slash.matched("/go", tree)[0].command.name == "goal"
    # `/ru` is a prefix of two real verbs now (`/run` and `/runs`): the ranking puts the shorter
    # exact prefix first, and both are reachable — the exact spellings are what resolve
    assert [item.command.name for item in slash.matched("/ru", tree)][:2] == ["run", "runs"]
    resolved_runs = slash.resolve("/runs")
    resolved_run = slash.resolve("/run")
    assert resolved_runs is not None and resolved_runs.argv == ("runs", "status")
    assert resolved_run is not None and resolved_run.command.name == "run"
    # an empty query lists everything
    assert len(slash.matched("/", tree)) == len(slash.SLASH_COMMANDS)


def test_palette_suggestions_are_completion_entries(tree):
    suggestions = slash.suggestions("/compose", tree)
    assert suggestions[0].text == "/compose"
    assert "skaldir compose show" in suggestions[0].description
    assert suggestions[0].kind == "slash"
    # the palette is fuzzy-ranked like every other menu: four letters are enough
    assert slash.suggestions("/resu", tree)[0].text == "/resume"
    assert slash.suggestions("/ses", tree)[0].text == "/sessions"
    assert slash.suggestions("/cost", tree)[0].text == "/cost"
    # …and a planned verb says so instead of claiming a command
    planned = next(item for item in slash.suggestions("/plan", tree) if item.text == "/plan")
    assert "planned" in planned.description


# --------------------------------------------------------------------------- #
# states come from the real parser and the real probe
# --------------------------------------------------------------------------- #


def test_palette_marks_from_the_real_build(tree):
    entries = {entry.command.name: entry for entry in slash.entries(tree)}
    # every slash command is either ready or explicitly refused — never both
    for entry in entries.values():
        assert entry.state in (
            slash.STATE_READY,
            slash.STATE_BLOCKED,
            slash.STATE_UNWIRED,
            slash.STATE_UNVERIFIED,
            slash.STATE_PLANNED,
        )
    # `selftest` is wired in every build that has the contract group
    assert entries["status"].ready
    # the built-ins never claim to run a command
    assert entries["help"].reason == "built-in"
    # the one verb this build does not carry is marked planned and is not runnable
    assert entries["plan"].state == slash.STATE_PLANNED
    assert entries["plan"].runnable is False


def test_an_unwired_group_is_marked_and_refused(tree):
    entry = next(
        item for item in slash.entries(tree) if item.command.name == "provider"
    )
    if entry.state == slash.STATE_UNWIRED:
        assert not entry.runnable
        assert "not wired" in entry.mark()
    else:  # a build where another block already wired `provider`
        assert entry.runnable


def test_a_flagged_layer_is_blocked_not_refused(tree):
    report = _report(ok_layers=set(), failed=set(cli_contract.LAYERS))
    entries = {
        item.command.name: item for item in slash.entries(tree, reachable={
            layer: False for layer in cli_contract.LAYERS
        }, report=report)
    }
    flagged = [item for item in entries.values() if item.state == slash.STATE_BLOCKED]
    assert flagged, "every wired group of a flagged layer must be marked"
    for entry in flagged:
        assert entry.runnable, "a flagged layer must still run (its own verdict counts)"
        assert "selftest" in entry.mark()
        assert entry.detail  # the probe's own reason
    # no *command group* claims to be ready while its layer is flagged
    covered = {str(layer).upper() for layer in cli_contract.LAYERS}
    commanded_ready = [
        item.command.name
        for item in entries.values()
        if item.state == slash.STATE_READY and item.command.argv
    ]
    for item in entries.values():
        if item.state == slash.STATE_READY and item.command.argv:
            assert str(item.layer).upper() not in covered, (
                f"/{item.command.name} claims ready while its layer {item.layer} is flagged"
            )
    # `status`, `model`, `provider` and `setup` map onto the kernel contract layer, which no
    # layer probe covers — so they are the only command groups that may stay ready here.
    # `workflows` is the fifth: its contract layer is `kernel` too (`workflows.list|run` are
    # the workflow registry, not one of the eight probes). `doctor` is the sixth: deployment
    # health is measured by the command itself, not by the eight-layer probe. `selftest` (the
    # redesign's spelling of `status`) is the seventh and the same kind of case: it *is* the
    # probe runner, so no eight-layer verdict can flag it.
    #
    # The reachability round (2026-09-19) added six more kernel-layer verbs — `spend`, `warm`,
    # `style`, `prompts`, `ledger-show`, `pact-show` — each verified against
    # `skaldir contract dump --json` to carry `layer: kernel`, exactly like the seven above.
    # They are in this list for the same reason, not because the rule was relaxed: any verb
    # whose layer the probe covers must be `blocked` here, and still is.
    assert sorted(commanded_ready) == [
        "doctor", "ledger-show", "model", "pact-show", "prompts", "provider", "selftest",
        "setup", "spend", "status", "style", "warm", "workflows",
    ]


def test_an_unloadable_parser_is_unverified_never_ready():
    entries = slash.entries(None, tree_error="ImportError: boom")
    commanded = [item for item in entries if item.command.argv]
    assert commanded
    for entry in commanded:
        assert entry.state == slash.STATE_UNVERIFIED
        assert entry.state != slash.STATE_READY
        assert "ImportError: boom" in entry.mark()


def test_hint_line_says_when_the_probe_ran_and_what_it_found(tree):
    report = _report(ok_layers={"ACT", "EXPAND"})
    line = slash.hint_line(tree, report)
    assert "flagged:" in line
    assert "selftest: 8 layer(s), 6 not reachable" in line
    # the kernel commands are ready because they exist, not because a layer passed, and the
    # built-ins are ready because the shell answers them; every one of them is named
    ready = line.split("ready: ", 1)[1].split("\n", 1)[0]
    for spelling in ("/help", "/quit", "/status", "/model", "/provider"):
        assert spelling in ready, spelling
    # and the verb this build does not carry is reported, never silently dropped
    assert "planned (not built): /plan" in line

    off = slash.hint_line(tree, None)
    assert "capability probe did not run" in off

    broken = slash.hint_line(tree, report, tree_error="RuntimeError: x")
    assert "did not load" in broken
    assert "ready:" not in broken


# --------------------------------------------------------------------------- #
# rendering + help text
# --------------------------------------------------------------------------- #


def test_render_shows_states_and_the_real_command(tree):
    """One aligned row per command: the spelling, the purpose, the honest state."""
    palette = slash.entries(tree, reachable={"ACT": False}, report=_report(ok_layers=set()))
    text = slash.render(palette)
    assert "/jev" in text
    assert "the eight layers" in text
    for line in text.splitlines():
        assert len(line) <= 80, line
    # the default look is one line per command (no per-entry usage/why sub-lines)
    assert len(text.splitlines()) <= len(palette) + len(slash.GROUPS) + 1
    # the real command behind a slash command is one keystroke away, not the default
    assert "skaldir jev selftest" in slash.render(palette, show_detail=True)


def test_render_keeps_the_state_column_aligned(tree):
    palette = slash.entries(tree)
    text = slash.render(palette)
    offsets = set()
    for entry in palette:
        row = next(line for line in text.splitlines()
                   if line.strip().startswith(entry.command.slash))
        offsets.add(row.index(entry.mark()))
    assert len(offsets) == 1, offsets


def test_help_text_names_the_command_it_runs(tree):
    text = slash.help_text(slash.command_for("runs"), tree)
    assert "runs:  skaldir runs status" in text
    assert "/runs" in text


# --------------------------------------------------------------------------- #
# the wiring contract: register_cli on the REAL parser
# --------------------------------------------------------------------------- #


def _subparsers():
    parser = cli.build_parser()
    return [
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    ][0]


def test_register_cli_adds_the_shell_group_and_is_idempotent():
    sub = _subparsers()
    if "shell" not in sub.choices:
        repl.register_cli(sub)
    parser = repl.register_cli(sub)  # a second call must not raise
    assert parser is sub.choices["shell"]
    assert "shell" in cli_contract.COMMANDS
    spec = cli_contract.spec_for("shell")
    assert spec.layer  # a layer is declared, whatever it is
    assert spec.mutates is True
    assert spec.evidence  # success without evidence is an internal error


def test_annotate_gives_the_shell_command_its_contract_path():
    # the CLI owner's snippet runs BEFORE cli_contract.annotate(parser); registering on
    # a fresh tree and annotating afterwards proves the leaf gets its dotted path (and
    # the common flags) — the exact order the snippet relies on
    parser = cli.build_parser()
    sub = [
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    ][0]
    if "shell" not in sub.choices:
        repl.register_cli(sub)
        cli_contract.annotate(parser)
    args = parser.parse_args(["shell", "--json", "--limit", "0"])
    assert args.command_path == "shell"
    assert args.json is True


def _parser_with_shell(original):
    """``build_parser()`` plus the owner's snippet — what `cli.py` will look like."""

    def build():
        parser = original()
        sub = [
            action
            for action in parser._actions
            if isinstance(action, argparse._SubParsersAction)
        ][0]
        if "shell" not in sub.choices:
            repl.register_cli(sub)
            cli_contract.annotate(parser)
        return parser

    return build


def test_shell_command_runs_through_the_product_cli(capsys, monkeypatch, tmp_path):
    """The real command path, scripted: `cli.main(["shell", …])` with piped input."""
    monkeypatch.setenv("GODSV_HOME", str(tmp_path))
    # `env.py` prefers SKALDIR_HOME over the legacy GODSV_HOME; a shell that once ran
    # `setup` leaks it into the session (the hazard env.py documents), and the runtime
    # — history included — would land outside tmp_path. The test names its own runtime.
    monkeypatch.delenv("SKALDIR_HOME", raising=False)
    monkeypatch.setattr(cli, "build_parser", _parser_with_shell(cli.build_parser))
    monkeypatch.setattr("sys.stdin", io.StringIO("selftest --layer ACT\n/quit\n"))
    code = cli.main(["shell", "--no-probe", "--limit", "5"])
    captured = capsys.readouterr()
    assert code in (cli_contract.EXIT_OK, cli_contract.EXIT_BLOCKED)
    assert "Skaldir" in captured.out
    assert "selftest --layer ACT" in captured.out
    history = (tmp_path / "shell" / "history.jsonl").read_text(encoding="utf-8")
    assert "selftest --layer ACT" in history


def test_shell_json_emits_one_envelope_with_evidence(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("GODSV_HOME", str(tmp_path))
    monkeypatch.delenv("SKALDIR_HOME", raising=False)  # see the test above: env precedence
    monkeypatch.setattr(cli, "build_parser", _parser_with_shell(cli.build_parser))
    monkeypatch.setattr("sys.stdin", io.StringIO("--version\n"))
    code = cli.main(["shell", "--no-probe", "--json"])
    captured = capsys.readouterr()
    import json

    document = json.loads(captured.out.strip())
    assert document["command"] == "shell"
    assert document["ok"] is True
    assert document["exit_code"] == code == 0
    assert document["evidence"]
    assert document["data"]["commands"] == 1
    assert document["data"]["history_file"].endswith("history.jsonl")


# --------------------------------------------------------------------------- #
# the top-level help: curated by default, complete on request
# --------------------------------------------------------------------------- #


def test_curated_help_names_the_taught_five_and_both_escape_hatches(tree):
    """The taught surface is five lines; every *command* in it is a real root of this build."""
    text = slash.curated_help(tree, width=120)
    for name, _note in slash.CURATED:
        if name in slash.CURATED_VIEWS:
            assert "--help" in text if name == "--help" else "\n    skaldir " in text, name
            continue
        # every curated command is a real root command of THIS build (no invented entry)
        assert name in tree.children, name
        assert f"\n    skaldir {name}" in text, name
    assert "--all-commands" in text
    assert "skaldir ? <command>" in text
    roots, leaves = len(tree.children), sum(
        slash._leaf_count(child) for child in tree.children.values()
    )
    assert f"{roots} root commands, {leaves} leaf commands" in text
    # the taught list is much shorter than the build: that is the whole point of the redesign
    assert len(slash.CURATED) < roots
    assert len(slash.CURATED) == 5
    # the rest is not hidden, it is *moved*: the screen says where everything else lives
    assert "everything else lives in the chat" in text
    assert "/help" in text
    # ASCII only (it is printed on a pipe and pasted into docs)
    assert all(ord(char) < 128 for char in text)


def test_full_help_lists_every_root_command_and_the_leaf_total(tree):
    text = slash.full_help(tree, width=120, per_group=True)
    for name in tree.children:
        assert f"\n    {name}" in text, name
    leaves = sum(slash._leaf_count(child) for child in tree.children.values())
    assert f"{leaves} leaf command(s) in total" in text


def test_the_cli_front_door_is_curated_and_the_full_list_stays_reachable(capsys, tree):
    """`-h` is the taught screen; `--all-commands` and `?` are the full one — both real."""
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["-h"])
    assert excinfo.value.code == 0
    curated = capsys.readouterr().out
    assert "the commands a session is taught" in curated
    assert "everything else lives in the chat" in curated
    assert "--all-commands" in curated
    # the taught screen names the five and does NOT print the whole root list
    for name in ("setup", "run", "serve"):
        assert f"    skaldir {name}" in curated, name
    assert "nightshift" not in curated

    code = cli.main(["--all-commands"])
    assert code == 0
    full = capsys.readouterr().out
    for name in tree.children:
        assert f"    {name}" in full, name
    assert "nightshift" in full  # every command is still named here

    assert cli.main(["?"]) == 0
    questioned = capsys.readouterr().out
    assert "every root command" in questioned

    # `? <command>` is the overlay for one command (usage + flags), not a new verb
    assert cli.main(["?", "tools", "list"]) == 0
    overlay = capsys.readouterr().out
    assert "tools.list" in overlay and "usage:" in overlay


def test_the_two_help_hatches_do_not_change_the_contract(tree):
    """The curated view may not cost a single command: leaves reachable, nothing unregistered."""
    parser = cli.build_parser()
    inventory = cli_contract.dump(parser)
    assert inventory["unregistered"] == []
    known = set(complete.known_paths(build_tree_of(parser)))
    dumped = {row["command"] for row in inventory["commands"]}
    assert dumped <= known
    # `--all-commands` and `?` are root options/arguments, never leaves
    assert "--all-commands" not in dumped and "?" not in dumped
    flags = {
        action.dest
        for action in parser._actions
        if isinstance(action, argparse.Action)
    }
    assert "all_commands" in flags


@functools.lru_cache(maxsize=2)
def build_tree_of(parser):
    return complete.build_tree(parser)


def test_mapping_reports_the_kind_and_the_completion_slot_of_every_verb(tree):
    rows = slash.mapping(tree)
    assert {row["slash"] for row in rows} == {f"/{name}" for name in slash.names()}
    kinds = {row["kind"] for row in rows}
    assert kinds <= {"real", "builtin", "planned", "unwired", "unverified"}
    # every verb reports the slot a client may complete after it (empty is a real answer)
    for row in rows:
        assert "values" in row and "value_flag" in row
    slots = {(row["slash"], row["values"]) for row in rows if row["values"]}
    assert ("/resume", "session") in slots
    assert ("/restore", "checkpoint") in slots
    assert ("/model", "provider") in slots
    assert ("/exec", "file") in slots


def test_a_builtin_is_exactly_a_verb_with_no_argv(tree):
    rows = {row["slash"]: row for row in slash.mapping(tree)}
    for row in rows.values():
        if row["kind"] == "builtin":
            assert row["path"] == "" and row["command"] == ""
        if row["kind"] == "real":
            assert row["command"].startswith("skaldir ")


def test_keys_text_names_only_bindings_this_build_sets(tree):
    text = slash.keys_text(width=100, history_file="/tmp/history.jsonl")
    for token in ("Tab", "Esc", "Ctrl-C", "Ctrl-D", "↑", "?", "/tmp/history.jsonl"):
        assert token in text, token
    # the readline bindings it claims are the ones install_readline really sets
    source = Path(complete.__file__).read_text(encoding="utf-8")
    for binding in ("show-all-if-ambiguous", "completion-ignore-case", "tab: complete"):
        assert binding in source, binding
    # …and the shell keys it names are the ones repl handles
    shell = Path(repl.__file__).read_text(encoding="utf-8")
    assert "dismiss_menu" in shell and "handle_builtin" in shell


# --------------------------------------------------------------------------- #
# the reachability round (2026-09-19): the twenty verb-less roots, closed
# --------------------------------------------------------------------------- #

#: the twenty roots `08-reachability.md` found with no `/` verb (F5), and the real verb each
#: one was resolved onto. The right-hand side is checked against the parser below, so this
#: list can never turn into a rename of the truth.
REACHABILITY_VERBS = {
    "image": ("image", "list"),
    "mcp": ("mcp", "map"),
    "tools-core": ("tools-core", "list"),
    "tools-hub": ("tools-hub",),
    "permission": ("permission", "show"),
    "completion": ("completion", "status"),
    "spend": ("spend", "list"),
    "stream": ("stream", "selfcheck"),
    "registry": ("registry", "status"),
    "memory-stats": ("memory-stats",),
    "memory-search": ("memory-search",),
    "memory-audit": ("memory-audit",),
    "memory-retrieval-check": ("memory-retrieval-check",),
    "metrics": ("metrics",),
    "voice": ("voice", "status"),
    "warm": ("warm", "status"),
    "style": ("style", "logo"),
    "prompts": ("prompts", "list"),
    "ledger-show": ("ledger-show",),
    "pact-show": ("pact-show",),
}

#: the verbs F6 named as not declaring what they need (they exited 1 into argparse's usage
#: text instead of saying what they wanted).
NEEDS_DECLARING = (
    "collect", "cancel", "spawn", "find", "register", "health", "rollback", "testfix",
    "run", "apply",
)


def test_every_reachability_verb_resolves_to_a_real_command(tree):
    """Each of the twenty maps onto a verb this build really carries (never a rename)."""
    known = set(complete.known_paths(tree))
    for name, argv in REACHABILITY_VERBS.items():
        command = slash.command_for(name)
        assert command is not None, name
        assert command.argv == argv, name
        assert not command.planned, name
        assert ".".join(argv) in known, f"/{name} claims {' '.join(argv)}"
    # the verb table grew by exactly these twenty: the surface stayed additive
    assert len(slash.SLASH_COMMANDS) == len(EXPECTED)


def test_a_declared_need_names_a_command_this_build_really_has(tree):
    """``needs_forms`` is quoted verbatim by the shell — so every form is a real command.

    The ten verbs of F6 declared nothing, so a user who typed one got argparse's usage text
    on stderr; the fix declares the need *and* the real forms that satisfy it. This test is
    what keeps those forms honest: the first word after ``skaldir`` is a root command of this
    build, and a second word that is not a flag is one of that root's real sub-commands.
    """
    roots = {name: child for name, child in tree.children.items()}
    checked = 0
    for name in NEEDS_DECLARING:
        command = slash.command_for(name)
        assert command is not None, name
        assert command.needs, f"/{name} must say what it needs"
        assert command.needs_forms, f"/{name} must quote the real forms"
        for form in command.needs_forms:
            head = form.split("#", 1)[0].strip()
            assert head.startswith("skaldir "), form
            words = [word for word in head[len("skaldir "):].split() if word]
            root = roots.get(words[0])
            assert root is not None, f"/{name} quotes a command this build has not: {form}"
            if len(words) > 1:
                second = words[1].lstrip("\"'")
                if not second.startswith(("-", "<", "[")):
                    assert second in root.children, f"/{name} quotes {words[0]} {second}"
            checked += 1
    assert checked >= 2 * len(NEEDS_DECLARING)
