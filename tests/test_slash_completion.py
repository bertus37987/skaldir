"""Autocompletion for the ``/`` surface — verbs, flags, **values** and ``@`` paths.

What these tests hold down, and why each one is a real store read rather than a stub:

* a slash verb completes to itself and its flags complete from the build's own parser
  (``/resume --sess<Tab>`` → ``--session``) — the parser is the only source;
* a *value* slot completes from the store that really holds it: the session store for
  ``--session``, the checkpoint manifests for ``checkpoint restore <id>``, the provider
  catalog for ``--main``/``--model``, the run transcripts for ``agent show <run>``. A store
  that is absent yields an **empty menu**, never a plausible-looking id;
* ``@path`` completes from the working tree through the codectx walker's own ignore rules,
  so a ``.gitignore``d file is offered by neither;
* the live palette filters as you type (one engine, fuzzy-ranked — the same ranking the
  palette and the menu use).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from skaldir import checkpoints, cli, codectx, complete, session_store, slash

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def tree():
    return complete.build_tree(cli.build_parser())


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    """A private runtime: every store read goes to ``tmp_path``, never the owner's state."""
    monkeypatch.setenv("SKALDIR_HOME", str(tmp_path))
    monkeypatch.setenv("GODSV_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_HOME", raising=False)
    complete._RULES_CACHE.clear()
    complete._DIR_CACHE.clear()
    return tmp_path


def _complete(tree, line, *, limit=None):
    return complete.complete_line(
        tree, line, len(line), slash=lambda query: slash.suggestions(query, tree), limit=limit
    )


def _texts(tree, line, *, limit=None):
    return [item.text for item in _complete(tree, line, limit=limit).suggestions]


# --------------------------------------------------------------------------- #
# verbs and flags
# --------------------------------------------------------------------------- #


def test_a_slash_verb_completes_and_its_flags_come_from_the_parser(tree):
    assert _complete(tree, "/resu").insert == "/resume"
    assert _complete(tree, "/sess").insert == "/sessions"
    assert _complete(tree, "/exec").insert == "/exec"
    # a bare `/` opens the menu and commits to nothing
    bare = _complete(tree, "/")
    assert bare.insert == ""                       # a bare `/` commits to nothing
    assert {item.text for item in bare.suggestions} == {
        command.slash for command in slash.SLASH_COMMANDS
    }


def test_a_flag_after_a_slash_verb_completes_from_the_real_parser(tree):
    assert _complete(tree, "/resume --sess").insert == "--session"
    assert "--session" in _texts(tree, "/resume --s")
    # `/model` names `skaldir model` (the picker's engine), so its flags are that verb's:
    # `--small/--mid/--main` belong to `provider tiers`, which /model no longer maps onto.
    assert "--model" in _texts(tree, "/model --mo")
    assert "--small" in _texts(tree, "provider tiers --sm")
    # a flag's argparse choices are the parser's own vocabulary
    assert set(_texts(tree, "/export --as ")) >= {"json", "jsonl", "markdown", "text"}


def test_the_completion_slot_of_every_verb_is_a_real_argument_of_its_command(tree):
    for command in slash.SLASH_COMMANDS:
        if not command.values or not command.argv:
            continue
        node, _left = complete.node_for(tree, list(command.argv))
        available = {option.name for option in node.options} | {
            positional.dest for positional in node.positionals
        }
        covered = any(
            complete.value_kind_for(node.path, name) == command.values for name in available
        )
        assert covered, (
            f"/{command.name} declares values={command.values} but {node.path} has no such slot"
        )


# --------------------------------------------------------------------------- #
# values: sessions, checkpoints, models, providers, runs
# --------------------------------------------------------------------------- #


def test_session_ids_come_from_the_real_session_store(tree, runtime):
    assert _texts(tree, "/resume --session ") == []       # empty store: empty menu
    store = session_store.open_store(runtime=str(runtime))
    created = store.create_session(title="the proof session")
    store.close()

    values = dict(complete.session_values())
    assert created.session_id in values
    assert "proof session" in values[created.session_id]

    # the slash slot and the real flag both complete the id that exists
    assert created.session_id in _texts(tree, "/resume --session ")
    assert created.session_id in _texts(tree, "/resume ")
    # a unique prefix completes to the whole id, not to a longer common prefix
    completion = _complete(tree, f"/resume --session {created.session_id[:6]}")
    assert completion.insert == created.session_id
    # …and so does the plain CLI line for the same command
    assert created.session_id in _texts(tree, "session resume --session ")


def test_checkpoint_ids_come_from_the_real_state_dir(tree, runtime):
    # `last` is a documented literal the command accepts, so it is always offered; a real
    # id appears only once a checkpoint really exists
    assert [t for t in _texts(tree, "/restore ") if not t.startswith("-")] == ["last"]
    workspace = runtime / "ws"
    workspace.mkdir()
    (workspace / "hello.txt").write_text("hello\n", encoding="utf-8")
    report = checkpoints.create_checkpoint(
        workspace=workspace,
        state_dir=runtime / checkpoints.CHECKPOINT_DIRNAME,
        files=["hello.txt"],
        label="the proof checkpoint",
    )
    assert report["ok"], report
    checkpoint_id = report["checkpoint_id"]

    assert checkpoint_id in dict(complete.checkpoint_values())
    assert checkpoint_id in _texts(tree, "/restore ")
    assert _complete(tree, f"/restore {checkpoint_id[:8]}").insert == checkpoint_id
    assert checkpoint_id in _texts(tree, "checkpoint show ")


def test_model_ids_come_from_the_provider_catalog(tree):
    from skaldir import provider_catalog

    models = dict(complete.model_values())
    for entry_id, entry in provider_catalog.CATALOG.items():
        # a catalog entry without a declared default (openrouter) contributes no id
        if entry.default_model:
            assert entry.default_model in models, entry_id
    # the flags that carry model ids complete them: `--model` on the picker's verb (what
    # `/model` maps onto) and `--main` on `provider tiers` (the tier → model mapping)
    assert _complete(tree, "/model --model dee").insert == "deepseek-"
    # the ids the flags complete to are the catalog's own defaults, read not retyped
    assert provider_catalog.CATALOG["deepseek"].default_model in _texts(tree, "/model --model ")
    assert provider_catalog.CATALOG["deepseek"].default_model in _texts(
        tree, "provider tiers --main "
    )


def test_provider_ids_come_from_the_catalog_and_the_config(tree):
    providers = dict(complete.provider_values())
    assert "deepseek" in providers
    assert "deepseek" in _texts(tree, "/model dee")
    assert "provider ids" in slash.help_text(slash.command_for("model"), tree)


def test_run_ids_come_from_the_run_transcripts(tree, runtime):
    """A real transcript (the loop writes it) is the only thing that makes a run id real."""
    assert complete.run_values() == []
    path = runtime / "runs"
    path.mkdir()
    # the transcript the loop writes, in its own shape: header first, then events
    transcript = path / "run-proof-1.jsonl"
    # the events the loop really writes (read by the loop's own `read_transcript`)
    started = {
        "kind": "run.started",
        "run_id": "run-proof-1",
        "task": "say hello",
        "at": "2026-09-19T00:00:00+00:00",
        "tools": [],
        "limits": {},
        "workspace": str(runtime),
    }
    finished = {
        "kind": "run.finished",
        "status": "answered",
        "reason": "the model answered",
        "answer": "hello",
        "steps": 1,
        "duration_ms": 4,
        "usage": {},
    }
    transcript.write_text(
        "\n".join(json.dumps(event) for event in (started, finished)) + "\n",
        encoding="utf-8",
    )
    assert "run-proof-1" in dict(complete.run_values())
    assert "run-proof-1" in _texts(tree, "agent show ")
    assert "run-proof-1" in _texts(tree, "/agent show ")


def test_a_verb_with_a_run_slot_completes_it_from_the_transcripts(tree, runtime):
    # `/agent show <run>` is the sub-verb shape: the substitution happens in resolve(), the
    # completion reads the same node
    assert complete.value_kind_for("agent.show", "run_id") == complete.VALUE_RUN
    assert complete.VALUE_SOURCES[complete.VALUE_RUN] is complete.run_values
    assert complete.VALUE_SOURCES[complete.VALUE_SESSION] is complete.session_values
    assert complete.VALUE_SOURCES[complete.VALUE_CHECKPOINT] is complete.checkpoint_values
    assert complete.VALUE_SOURCES[complete.VALUE_GOAL] is complete.goal_values


def test_the_new_verbs_complete_their_own_value_slots(tree, runtime):
    """`/stop`/`/retry` offer the run ids, `/history`/`/fork` the session ids — from the stores."""
    def values(line):
        return [text for text in _texts(tree, line) if not text.startswith("-")]

    # an empty store offers the honest "nothing saved yet" hint — never an invented id
    assert values("/stop ") == ["(no saved run yet)"]
    assert values("/history ") == ["(no saved session yet)"]
    store = session_store.open_store(runtime=str(runtime))
    created = store.create_session(title="the alias session")
    store.close()
    runs = runtime / "runs"
    runs.mkdir()
    started = {
        "kind": "run.started",
        "run_id": "run-proof-2",
        "task": "say hello",
        "at": "2026-09-19T00:00:00+00:00",
        "tools": [],
        "limits": {},
        "workspace": str(runtime),
    }
    (runs / "run-proof-2.jsonl").write_text(json.dumps(started) + "\n", encoding="utf-8")

    assert values("/stop ") == ["run-proof-2"]
    assert values("/retry ") == ["run-proof-2"]
    assert values("/history ") == [created.session_id]
    assert values("/fork ") == [created.session_id]


def test_workflow_ids_come_from_the_declared_contracts(tree):
    """`/workflows <Tab>` offers the names `skaldir workflows list` prints — and nothing else."""
    from skaldir import workflows

    declared = {
        str(contract.get("name"))
        for contract in workflows.workflow_contracts()
        if contract.get("name")
    }
    assert declared, "this build declares workflow contracts"
    assert declared <= {value for value, _note in complete.workflow_values()}
    assert complete.value_kind_for("workflows.run", "workflow_id") == complete.VALUE_WORKFLOW
    assert complete.VALUE_SOURCES[complete.VALUE_WORKFLOW] is complete.workflow_values
    offered = set(_texts(tree, "/workflows "))
    assert declared <= offered, sorted(declared - offered)


# --------------------------------------------------------------------------- #
# @files and @folders through the codectx walker
# --------------------------------------------------------------------------- #


def test_at_paths_complete_files_and_folders_from_the_tree(tree, tmp_path, monkeypatch):
    workspace = tmp_path / "ws"
    (workspace / "src" / "skaldir").mkdir(parents=True)
    (workspace / "src" / "skaldir" / "slash.py").write_text("x = 1\n", encoding="utf-8")
    (workspace / "src" / "skaldir" / "complete.py").write_text("y = 2\n", encoding="utf-8")
    (workspace / "README.md").write_text("hi\n", encoding="utf-8")
    (workspace / "build").mkdir()
    (workspace / "build" / "junk.py").write_text("no\n", encoding="utf-8")
    (workspace / ".gitignore").write_text("build/\n*.log\n", encoding="utf-8")
    (workspace / "debug.log").write_text("no\n", encoding="utf-8")
    monkeypatch.chdir(workspace)
    complete._RULES_CACHE.clear()
    complete._DIR_CACHE.clear()

    assert _complete(tree, "@README").insert == "@README.md"
    # a directory is offered with the slash, so the next Tab descends into it
    assert _complete(tree, "@sr").insert == "@src/"
    assert "@src/skaldir/" in _texts(tree, "@src/")
    assert _complete(tree, "@src/skaldir/sl").insert == "@src/skaldir/slash.py"
    # the codectx ignore rules apply: an ignored directory and file are never offered
    offered = _texts(tree, "@")
    assert "build/" not in offered and "debug.log" not in offered
    assert "@src/skaldir/complete.py" in _texts(tree, "@src/skaldir/c")


def test_the_ignore_rules_are_the_codectx_walkers_own(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / ".gitignore").write_text("*.tmp\n", encoding="utf-8")
    (workspace / "keep.txt").write_text("keep\n", encoding="utf-8")
    (workspace / "drop.tmp").write_text("drop\n", encoding="utf-8")
    rules = codectx.load_ignore_rules(workspace)
    assert rules.decide("drop.tmp", is_dir=False)[0] is True
    assert rules.decide("keep.txt", is_dir=False)[0] is False
    names = [item.text for item in complete.file_suggestions("@", root=workspace)]
    assert "@keep.txt" in names and "@drop.tmp" not in names


def test_a_file_slot_completes_even_without_the_at_marker(tree, tmp_path, monkeypatch):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "script.py").write_text("print(1)\n", encoding="utf-8")
    monkeypatch.chdir(workspace)
    complete._DIR_CACHE.clear()
    # `code run --file` is a declared path slot: `@` is not required there
    assert "script.py" in _texts(tree, "code run --file scr")
    assert complete.value_kind_for("code.run", "--file") == complete.VALUE_FILE


# --------------------------------------------------------------------------- #
# the live palette: filters as you type, refuses what does not exist
# --------------------------------------------------------------------------- #


def test_the_live_palette_filters_as_you_type(tree):
    menu = slash.PaletteMenu()
    palette = slash.entries(tree)
    assert len(menu.update("/", palette)) == len(palette)      # everything
    assert [entry.command.name for entry in menu.update("/res", palette)] == [
        "resume", "restore", "registry", "register",
    ]
    assert [entry.command.name for entry in menu.update("/resu", palette)] == ["resume"]
    assert [entry.command.name for entry in menu.update("/sess", palette)] == ["sessions"]
    assert [entry.command.name for entry in menu.update("/cost", palette)] == ["cost"]
    # a query that matches nothing closes the menu instead of showing a lie
    assert menu.update("/zzz", palette) == []
    assert menu.open is False
    # ESC forgets everything
    menu.update("/re", palette)
    assert menu.dismiss() is True
    assert menu.query == "" and menu.entries == [] and menu.open is False


def test_the_typed_line_filters_the_same_way_as_the_palette(tree):
    """One engine: the palette and the completion menu rank identically (no drift)."""
    typed = {
        probe: [item.text for item in _complete(tree, probe).suggestions]
        for probe in ("/res", "/resu", "/mo", "/ex")
    }
    assert typed["/resu"][0] == "/resume"
    assert typed["/mo"][0] == "/model"
    assert typed["/ex"][0] == "/exec"
    assert set(typed["/res"][:2]) == {"/resume", "/restore"}


def test_a_planned_verb_shows_its_nearest_real_commands_and_inserts_nothing(tree):
    completion = _complete(tree, "/plan ")
    texts = [item.text for item in completion.suggestions]
    assert texts == list(slash.command_for("plan").nearest)
    assert all(item.insertable is False for item in completion.suggestions)
    assert completion.insert == ""
    assert all("planned" in item.description for item in completion.suggestions)


def test_an_unknown_verb_is_not_completed(tree):
    # the palette path still answers a partial verb name (the closest real rows), but a
    # token after an unknown verb yields nothing at all
    assert complete.slash_args(tree, ["/nope"], "") is None
    assert complete.slash_args(tree, ["tools", "list"], "x") is None
    assert _complete(tree, "/nope ").suggestions == ()


def test_completion_never_raises_without_a_store(runtime, monkeypatch, tree):
    """Every store read is guarded: a broken store is an empty menu, not a traceback."""
    monkeypatch.setattr(complete, "runtime_dir", lambda: Path("/nonexistent/runtime"))
    assert complete.session_values() == []
    # `last` is a literal the command accepts, so it is always offered — and nothing else
    assert [value for value, _note in complete.checkpoint_values()] == ["last"]
    assert complete.run_values() == []
    assert complete.goal_values() == []
    # …and the menu is then simply empty for a value slot
    assert _texts(tree, "/resume --session ") == []


def test_the_value_table_only_names_paths_that_exist_in_this_build(tree):
    known = set(complete.known_paths(tree))
    for path, argument in complete.VALUE_TABLE:
        assert path in known, f"{path} is not a command of this build"
        node = next(
            (item for item in _nodes(tree) if item.path == path), None
        )
        assert node is not None
        available = {option.name for option in node.options} | {
            positional.dest for positional in node.positionals
        }
        assert argument in available, f"{path} has no argument {argument}"


def _nodes(node):
    yield node
    for child in node.children.values():
        yield from _nodes(child)


# --------------------------------------------------------------------------- #
# the shell's own Tab path (scripted, no PTY): completion -> argv -> the command
# --------------------------------------------------------------------------- #


def test_the_shells_scripted_tab_completes_a_value_and_runs_the_real_flag(runtime):
    """`/resume --session <Tab>` in a scripted session: the id is inserted, then resumed.

    This is the end-to-end claim the other tests build up to: the value comes from the store,
    Tab inserts it, the resolved argv carries the real `--session` flag, and the real command
    runs — one table, from the store to the exit code.
    """
    import io

    from skaldir import repl

    store = session_store.open_store(runtime=str(runtime))
    created = store.create_session(title="scripted menu proof")
    store.close()

    seen: list[list[str]] = []
    out, err = io.StringIO(), io.StringIO()
    session = repl.Session(
        runtime=str(runtime),
        runner=lambda argv: (seen.append(list(argv)), 0)[1],
        out=out, err=err,
    )
    shell = repl.Shell(
        repl.ShellOptions(probe=False, quiet=True),
        session=session, out=out, err=err,
        stdin=io.StringIO("/resume --session \t\n/quit\n"),
    )
    shell.run()
    text = out.getvalue()
    assert created.session_id in text                       # the menu named the real id
    assert "the only candidate" in text                     # …and Tab inserted it
    assert seen and seen[-1] == ["session", "resume", "--session", created.session_id]

