"""The ``skaldir checkpoint`` command group (``skaldir.checkpoints_cli``).

Two halves, like every other group:

* the group as it runs **standalone** (``python -m skaldir.checkpoints_cli`` /
  ``main([...])``): the product envelope on stdout, the exit codes, and the verbs the
  feature promises — ``create`` / ``list`` / ``show`` / ``restore`` / ``prune`` — plus
  ``resume``, the long-run protocol's step 0;
* the **hand-over** to the product CLI: ``register_cli`` is idempotent, installs the six
  ``cli_contract.COMMANDS`` entries, and the two-line snippet applied to a *scratch copy*
  of ``src/`` makes ``skaldir checkpoint …`` reachable through the real
  ``cli.build_parser()`` with ``unregistered == []``.

Nothing here edits ``cli.py`` or ``cli_contract.py``: the wiring is proven in a copy,
exactly as the sibling groups do it.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import re
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from skaldir import checkpoints as CP
from skaldir import checkpoints_cli, cli_contract
from skaldir.session_store import SessionStore

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src" / "skaldir"

SNIPPET = (
    "    # Unified checkpoints: workspace + conversation in one restorable point.\n"
    "    from .checkpoints_cli import register_cli as register_checkpoint_cli\n"
    "\n"
    "    register_checkpoint_cli(sub)\n"
)

PROBE = r'''
import contextlib, io, json, sys
from skaldir import cli, cli_contract

workspace = sys.argv[1]
buffer = io.StringIO()
with contextlib.redirect_stdout(buffer):
    created = cli.main(["checkpoint", "create", "--workspace", workspace,
                        "--file", "a.txt", "--json"])
envelope = json.loads(buffer.getvalue().strip().splitlines()[-1])
parser = cli.build_parser()
inventory = cli_contract.dump(parser)
print(json.dumps({
    "exit_code": created,
    "envelope_exit": envelope["exit_code"],
    "ok": envelope["ok"],
    "evidence": len(envelope["evidence"]),
    "checkpoint_id": envelope["data"]["checkpoint_id"],
    "files": [item["path"] for item in envelope["data"]["files"]],
    "leaves": sorted(path for path, _ in cli_contract.leaf_commands(parser)
                     if path.startswith("checkpoint")),
    "unregistered": inventory["unregistered"],
    "dumped": sorted(item["command"] for item in inventory["commands"]
                     if item["command"].startswith("checkpoint")),
    "layers": {path: cli_contract.spec_for(path).layer for path in
               ("checkpoint.create", "checkpoint.list", "checkpoint.show",
                "checkpoint.restore", "checkpoint.resume", "checkpoint.prune")},
}))
'''


def envelope(capsys) -> dict:
    """The one JSON document the standalone group printed."""
    line = capsys.readouterr().out.strip().splitlines()[-1]
    return json.loads(line)


def sha_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def scene(tmp_path: Path) -> Iterator[dict]:
    """A scratch tree, a runtime with a 50-message session and one applied edit."""
    from skaldir import edit_tx as ET

    ws = tmp_path / "ws"
    (ws / "src").mkdir(parents=True)
    (ws / "src" / "one.py").write_text("def one():\n    return 1\n", encoding="utf-8")
    (ws / "src" / "two.py").write_text("TWO = 2\n", encoding="utf-8")
    (ws / "notes.md").write_text("# notes\n", encoding="utf-8")
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    store = SessionStore(runtime / "sessions.db")
    session = store.create_session("cli scene")
    for turn in range(25):
        store.append_turn(session.session_id, f"user {turn}", f"assistant {turn}")
    edit_state = runtime / "edit"
    spec, problem = ET.parse_spec(
        {
            "reason": "cli scene",
            "verification": {"kind": "syntax"},
            "files": [
                {"path": "src/one.py", "edits": [{"old": "return 1", "new": "return 11"}]},
                {"path": "src/two.py", "edits": [{"old": "TWO = 2", "new": "TWO = 22"}]},
                {"path": "notes.md", "edits": [{"old": "# notes", "new": "# notes v2"}]},
            ],
        }
    )
    assert spec is not None, problem
    record = ET.run_transaction(spec, ws, state_dir=edit_state)
    assert record["verdict"] == ET.VERDICT_APPLIED
    yield {
        "tmp": tmp_path,
        "ws": ws,
        "runtime": runtime,
        "state": tmp_path / "checkpoint",
        "store": store,
        "session_id": session.session_id,
        "edit_state": edit_state,
    }
    store.close()


def run_cli(scene: dict, *argv: str) -> int:
    """The group through its own standalone entry point, with this scene's paths.

    Only the flags the named verb actually declares are appended — argparse is the first
    thing that would catch a flag the group does not own.
    """
    verb = str(argv[0]) if argv else ""
    common = ["--state", str(scene["state"]), "--runtime", str(scene["runtime"]), "--json"]
    if verb == "create":
        common += [
            "--workspace", str(scene["ws"]),
            "--session", scene["session_id"],
            "--session-store", str(scene["runtime"] / "sessions.db"),
            "--edit-state", str(scene["edit_state"]),
        ]
    elif verb in ("show", "restore", "resume"):
        common += [
            "--workspace", str(scene["ws"]),
            "--session-store", str(scene["runtime"] / "sessions.db"),
        ]
    return checkpoints_cli.main([*argv, *common])


# --------------------------------------------------------------------------- #
# the standalone group                                                          #
# --------------------------------------------------------------------------- #


def test_create_then_list_through_the_envelope(scene, capsys):
    assert run_cli(scene, "create", "--label", "cli point") == 0
    created = envelope(capsys)
    assert created["ok"] is True and created["exit_code"] == 0
    assert created["command"] == "checkpoint.create", "the envelope names the exact command"
    assert created["data"]["verdict"] == CP.VERDICT_CREATED
    assert created["data"]["stats"]["files"] == 3
    assert created["data"]["stats"]["messages"] == 50
    assert created["data"]["stats"]["manifest_bytes"] > 0
    assert created["evidence"], "a success without evidence is downgraded to exit 7"
    checkpoint_id = created["data"]["checkpoint_id"]

    assert run_cli(scene, "list") == 0
    listing = envelope(capsys)
    assert listing["data"]["count"] == 1
    row = listing["data"]["checkpoints"][0]
    assert row["checkpoint_id"] == checkpoint_id
    assert row["files"] == 3 and row["messages"] == 50
    assert listing["data"]["interrupted"] == []


def test_show_prints_the_preview_and_writes_nothing(scene, capsys):
    run_cli(scene, "create")
    capsys.readouterr()
    (scene["ws"] / "src" / "one.py").write_text("def one():\n    return 99\n", encoding="utf-8")
    before = sha_of(scene["ws"] / "src" / "two.py")
    assert run_cli(scene, "show", "last") == 0
    shown = envelope(capsys)
    assert shown["data"]["verdict"] == CP.VERDICT_PREVIEW
    files = {item["path"]: item for item in shown["data"]["preview"]["files"]}
    assert files["src/one.py"]["status"] == CP.FILE_CHANGED
    assert "+    return 11" in files["src/one.py"]["diff"]
    assert files["src/two.py"]["status"] == CP.FILE_IDENTICAL
    assert shown["data"]["preview"]["would_refuse"] == "drift"
    assert sha_of(scene["ws"] / "src" / "two.py") == before


def test_restore_asks_for_confirmation_and_then_refuses_to_overwrite_drift(scene, capsys):
    run_cli(scene, "create")
    capsys.readouterr()
    (scene["ws"] / "notes.md").write_text("# notes, changed later\n", encoding="utf-8")
    drifted = sha_of(scene["ws"] / "notes.md")

    assert run_cli(scene, "restore", "last") == CP.EXIT_USAGE
    asked = envelope(capsys)
    assert asked["data"]["verdict"] == CP.VERDICT_CONFIRMATION_REQUIRED
    assert asked["data"]["confirmation_required"] is True
    assert sha_of(scene["ws"] / "notes.md") == drifted, "nothing may be written yet"

    assert run_cli(scene, "restore", "last", "--yes") == CP.EXIT_CONFLICT
    refused = envelope(capsys)
    assert refused["data"]["verdict"] == CP.VERDICT_REFUSED_DRIFT
    assert refused["data"]["error"]["kind"] == "drift"
    assert sha_of(scene["ws"] / "notes.md") == drifted


def test_restore_with_force_recovers_the_files_and_the_conversation(scene, capsys):
    run_cli(scene, "create")
    created = envelope(capsys)["data"]
    capsys.readouterr()
    for name in ("src/one.py", "src/two.py", "notes.md"):
        (scene["ws"] / name).write_text("changed\n", encoding="utf-8")
    for turn in range(25, 30):
        scene["store"].append_turn(scene["session_id"], f"user {turn}", f"assistant {turn}")

    assert run_cli(scene, "restore", "last", "--yes", "--force") == 0
    answer = envelope(capsys)["data"]
    assert answer["verdict"] == CP.VERDICT_RESTORED and answer["complete"] is True
    assert all(item["byte_identical"] for item in answer["written"])
    for entry in created["files"]:
        assert sha_of(scene["ws"] / entry["path"]) == entry["sha256"]
    assert answer["conversation"]["identical"] is True
    assert (
        answer["conversation"]["conversation_sha256_after"]
        == created["conversation"]["conversation_sha256"]
    )
    assert [item["kind"] for item in answer["forced_drift"]] == [
        "file",
        "file",
        "file",
        "conversation",
    ]


def test_resume_is_step_zero_of_the_long_run_protocol(scene, capsys):
    run_cli(scene, "create")
    capsys.readouterr()
    for turn in range(25, 27):
        scene["store"].append_turn(scene["session_id"], f"user {turn}", f"assistant {turn}")
    (scene["ws"] / "src" / "one.py").write_text("def one():\n    return 0\n", encoding="utf-8")

    assert run_cli(scene, "resume", "last", "--yes", "--force") == 0
    answer = envelope(capsys)["data"]
    assert answer["verb"] == "resume"
    resume = answer["resume"]
    assert resume["step"] == 0
    assert resume["requested_by"] == "checkpoint resume"
    assert resume["session_id"] == answer["conversation"]["restore_session_id"]
    assert resume["next_role"] == "user" and resume["next_seq"] == 51
    assert resume["complete"] is True
    assert resume["continuation_command"].startswith("skaldir session append ")
    assert resume["store_resume"]["history"]["matches"] is True
    # the continuation is real: the next append lands at seq 51 in the restored session
    scene["store"].append_turn(resume["session_id"], "the next turn", "the answer")
    assert scene["store"].require_session(resume["session_id"]).message_count == 52


def test_an_unknown_checkpoint_exits_five(scene, capsys):
    assert run_cli(scene, "restore", "cp-20260101-000000-000000-nope", "--yes") == (
        CP.EXIT_NOT_FOUND
    )
    payload = envelope(capsys)
    assert payload["data"]["verdict"] == CP.VERDICT_NOT_FOUND
    assert payload["exit_code"] == CP.EXIT_NOT_FOUND


def test_the_human_view_is_plain_ascii_when_text_is_asked_for(scene, capsys):
    run_cli(scene, "create", "--label", "human")
    envelope(capsys)
    assert checkpoints_cli.main(
        ["list", "--state", str(scene["state"]), "--runtime", str(scene["runtime"]), "--text"]
    ) == 0
    text = capsys.readouterr().out
    assert "checkpoints in" in text
    assert text.isascii(), "the plain view must degrade to ASCII"
    assert "\x1b" not in text


def test_the_leaf_shapes_the_group_exposes():
    parser = checkpoints_cli.build_parser()
    sub = next(
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    assert sorted(sub.choices) == ["create", "list", "prune", "restore", "resume", "show"]
    for name, leaf in sub.choices.items():
        options = {item for action in leaf._actions for item in action.option_strings}
        assert {"--state", "--runtime", "--json", "--text", "--session-store"} <= options, name
        parsed = leaf.parse_args([])
        assert callable(getattr(parsed, "func", None)), name
        assert parsed.command_path == f"checkpoint.{name}", "the envelope names the leaf"
    restore = sub.choices["restore"]
    flags = {item for action in restore._actions for item in action.option_strings}
    assert {"--yes", "--force", "--dry-run", "--workspace", "--json"} <= flags
    shown = {item for action in sub.choices["show"]._actions for item in action.option_strings}
    assert "--no-diff" in shown and "--yes" not in shown
    resume = sub.choices["resume"]
    assert "--dry-run" not in {
        item for action in resume._actions for item in action.option_strings
    }, "resume restores as step 0: it has no preview-only mode"
    prune = {
        item for action in sub.choices["prune"]._actions for item in action.option_strings
    }
    assert {"--yes", "--force", "--limit"} <= prune
    assert "--dry-run" not in prune, (
        "prune is a dry run *by default* (--yes removes): a --dry-run flag would make "
        "the safe mode optional instead of the default"
    )


def test_register_cli_is_idempotent_and_installs_the_contract_entries(monkeypatch):
    """Two registrations, one group; and the six entries the layer claims are installed."""
    monkeypatch.setattr(cli_contract, "COMMANDS", dict(cli_contract.COMMANDS))
    root = argparse.ArgumentParser(prog="skaldir")
    sub = root.add_subparsers(dest="command")
    first = checkpoints_cli.register_cli(sub)
    second = checkpoints_cli.register_cli(sub)  # must not raise "conflicting subparser"
    assert first is second
    assert "checkpoint" in sub.choices
    installed = {path for path in cli_contract.COMMANDS if path.startswith("checkpoint.")}
    assert installed == {
        "checkpoint.create",
        "checkpoint.list",
        "checkpoint.show",
        "checkpoint.restore",
        "checkpoint.resume",
        "checkpoint.prune",
    }
    for path in sorted(installed):
        entry = cli_contract.COMMANDS[path]
        assert entry.layer == "PERSIST", path
        assert entry.evidence, "a success without evidence is downgraded to exit 7"
        assert entry.exit_codes and all(code in range(9) for code in entry.exit_codes)
        assert entry.data_schema and entry.data_schema["type"] == "object", path
    assert cli_contract.COMMANDS["checkpoint.create"].mutates is True
    assert cli_contract.COMMANDS["checkpoint.restore"].mutates is True
    assert cli_contract.COMMANDS["checkpoint.list"].mutates is False
    assert cli_contract.COMMANDS["checkpoint.show"].mutates is False
    assert cli_contract.COMMANDS["checkpoint.prune"].mutates is True
    assert CP.EXIT_CONFLICT in cli_contract.COMMANDS["checkpoint.restore"].exit_codes
    assert CP.EXIT_USAGE in cli_contract.COMMANDS["checkpoint.restore"].exit_codes


def test_the_contract_snippet_is_the_documentation_the_module_claims():
    doc = checkpoints_cli.__doc__ or ""
    assert "from .checkpoints_cli import register_cli as register_checkpoint_cli" in doc
    assert "register_checkpoint_cli(sub)" in doc
    assert "checkpoint.restore" in checkpoints_cli.CONTRACTS_SNIPPET
    assert set(checkpoints_cli.CONTRACT_ENTRIES) == {
        "checkpoint.create",
        "checkpoint.list",
        "checkpoint.show",
        "checkpoint.restore",
        "checkpoint.resume",
        "checkpoint.prune",
    }


# --------------------------------------------------------------------------- #
# the hand-over, proven in a scratch copy                                       #
# --------------------------------------------------------------------------- #


def _insert_snippet(text: str) -> tuple[str, str]:
    """Insert the snippet after the last ``_register_*(sub)`` line of ``build_parser``."""
    matches = list(re.finditer(r"^    _register_\w+\(sub\)\n", text, flags=re.MULTILINE))
    assert matches, "no register_cli call in build_parser — this test cannot place the snippet"
    anchor = matches[-1]
    return text[: anchor.end()] + SNIPPET + text[anchor.end() :], anchor.group(0)


def test_the_snippet_wires_the_group_into_the_real_cli(tmp_path, scene):
    package = tmp_path / "copy"
    package.mkdir()
    shutil.copytree(
        SRC, package / "skaldir", ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
    )
    cli_path = package / "skaldir" / "cli.py"
    text = cli_path.read_text(encoding="utf-8")
    wired, anchor = _insert_snippet(text)
    cli_path.write_text(wired, encoding="utf-8")
    assert anchor.strip() in wired

    home = tmp_path / "home"
    home.mkdir()
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(home),
        "SKALDIR_HOME": str(home / "rt"),
        "PYTHONPATH": str(package),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    pristine = subprocess.run(
        [sys.executable, "-c", "from skaldir import cli; cli.build_parser(); print('built')"],
        env=dict(env, PYTHONPATH=str(SRC.parent)),
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        timeout=300,
    )
    if pristine.returncode != 0:
        pytest.skip(
            f"the source cli.py does not build on its own right now: {pristine.stderr[-400:]}"
        )
    probe = tmp_path / "probe.py"
    probe.write_text(PROBE, encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, str(probe), str(scene["ws"])],
        env=env,
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert completed.returncode == 0, completed.stderr[-4000:]
    report = json.loads(completed.stdout.strip().splitlines()[-1])
    assert report["exit_code"] == 0 and report["envelope_exit"] == 0, report
    assert report["ok"] is True and report["evidence"] >= 1
    assert report["files"] == ["a.txt"], report
    assert report["leaves"] == [
        "checkpoint.create",
        "checkpoint.list",
        "checkpoint.prune",
        "checkpoint.restore",
        "checkpoint.resume",
        "checkpoint.show",
    ], report
    assert report["unregistered"] == [], "a command cannot ship without its contract"
    assert report["dumped"] == report["leaves"]
    assert set(report["layers"].values()) == {"PERSIST"}
    # the checkpoint really landed in the copy's own runtime, and only the one file
    manifest = next((tmp_path / "home" / "rt" / "checkpoint").glob("*/manifest.json"))
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert [item["path"] for item in payload["files"]] == ["a.txt"]
    assert payload["file_set"]["source"] == "none"


def test_the_group_output_contains_no_german_and_no_emitted_traceback(scene, capsys):
    run_cli(scene, "create")
    created = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    blob = json.dumps(created, ensure_ascii=False).lower()
    for umlaut in ("ä", "ö", "ü", "ß"):
        assert umlaut not in blob
    assert "traceback" not in blob


def test_a_create_without_a_session_records_the_files_only(scene, capsys):
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = checkpoints_cli.main(
            [
                "create",
                "--state", str(scene["state"]),
                "--runtime", str(scene["runtime"]),
                "--edit-state", str(scene["edit_state"]),
                "--workspace", str(scene["ws"]),
                "--json",
                "--session-store", str(scene["runtime"] / "sessions.db"),
            ]
        )
    payload = json.loads(buffer.getvalue().strip().splitlines()[-1])
    assert code == 0
    assert payload["data"]["conversation"] is None
    assert payload["data"]["stats"]["messages"] == 0
    assert payload["data"]["stats"]["files"] == 3


def test_prune_cli_is_a_dry_run_until_yes_and_records_the_evidence(scene, capsys):
    """The safe mode is the default; ``--yes`` is the only way anything is removed."""
    assert run_cli(scene, "create") == 0
    capsys.readouterr()
    orphan = CP.store_blob(scene["state"], b"an orphan, named by no manifest\n")

    assert run_cli(scene, "prune") == 0
    plan = envelope(capsys)
    assert plan["command"] == "checkpoint.prune"
    assert plan["data"]["verdict"] == CP.VERDICT_PRUNE_PLAN
    assert plan["data"]["dry_run"] is True
    assert [row["sha256"] for row in plan["data"]["candidates"]] == [orphan["sha256"]]
    assert plan["data"]["blobs"]["removed"] == 0
    assert CP.blob_path(scene["state"], orphan["sha256"]).is_file()
    assert plan["evidence"], "a success without evidence is downgraded to exit 7"

    assert run_cli(scene, "prune", "--yes") == 0
    done = envelope(capsys)
    assert done["data"]["verdict"] == CP.VERDICT_PRUNED
    assert done["data"]["blobs"]["removed"] == 1
    assert done["data"]["blobs"]["removed_bytes"] == len(b"an orphan, named by no manifest\n")
    assert not CP.blob_path(scene["state"], orphan["sha256"]).exists()
    assert done["evidence"]

    # the checkpoint is still restorable after the sweep
    assert run_cli(scene, "show", "last") == 0
    shown = envelope(capsys)
    assert shown["data"]["verdict"] == CP.VERDICT_PREVIEW
    assert shown["data"]["preview"]["would_refuse"] == ""


def test_prune_cli_refuses_with_exit_three_and_names_the_reason(scene, capsys):
    assert run_cli(scene, "create") == 0
    capsys.readouterr()
    broken = scene["state"] / "cp-broken-000000" / CP.MANIFEST_NAME
    broken.parent.mkdir(parents=True)
    broken.write_text("{ not json", encoding="utf-8")

    assert run_cli(scene, "prune", "--yes") == CP.EXIT_BLOCKED
    refused = envelope(capsys)
    assert refused["ok"] is False
    assert refused["error"]["code"] == "blocked"
    assert refused["data"]["checkpoints"]["unreadable"] == 1

    # the human view of the refusal (no --json: the contract redirects it to stderr in JSON mode)
    assert checkpoints_cli.main(
        ["prune", "--state", str(scene["state"]), "--runtime", str(scene["runtime"]), "--text"]
    ) == CP.EXIT_BLOCKED
    text = capsys.readouterr().out
    assert "checkpoint:" in text and "cannot be read" in text
    assert "\x1b" not in text and text.isascii(), repr(text)


def test_prune_cli_text_view_is_plain_ascii(scene, capsys):
    assert run_cli(scene, "create") == 0
    capsys.readouterr()
    CP.store_blob(scene["state"], b"orphan for the text view\n")
    assert checkpoints_cli.main(
        ["prune", "--state", str(scene["state"]), "--runtime", str(scene["runtime"]), "--text"]
    ) == 0
    text = capsys.readouterr().out
    assert "checkpoint prune (dry run)" in text
    assert "would remove: 1 blob(s)" in text
    assert "\x1b" not in text
    for umlaut in ("ä", "ö", "ü", "ß"):
        assert umlaut not in text
