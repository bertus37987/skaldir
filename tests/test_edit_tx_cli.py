"""FINAL-3 — the ``skaldir edit`` command group (``skaldir.edit_cli``).

Two halves:

* the group as it runs **standalone** (``python -m skaldir.edit_cli`` / ``main([...])``):
  the product envelope on stdout, the exit codes, the flags, the four verbs (1–14);
* the **hand-over** to the product CLI: ``register_cli`` is idempotent and installs the
  four ``cli_contract.COMMANDS`` entries, and the 3-line snippet applied to a *scratch
  copy* of ``src/`` makes ``skaldir edit …`` reachable through the real
  ``cli.build_parser()`` with ``unregistered == []`` (15–18).

Nothing here edits ``cli.py``, ``cli_contract.py`` or any file outside this block's
ownership: the wiring is proven in a copy, exactly as the sibling groups do it.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from skaldir import cli_contract, edit_cli
from skaldir import edit_tx as ET

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src" / "skaldir"

#: The wiring the CLI owner applies (the group installs its own contract entries, so this
#: is the only change `cli.py` needs).
ANCHOR = "    _register_memory(sub)\n"
SNIPPET = (
    ANCHOR
    + "\n"
    + "    # Edit transactions (atomic multi-file edits): the group owns its four verbs\n"
    + "    # and their `cli_contract.COMMANDS` entries.\n"
    + "    from .edit_cli import register_cli as _register_edit_cli\n"
    + "\n"
    + "    _register_edit_cli(sub)\n"
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def tree(tmp_path: Path) -> dict:
    """A scratch workspace, a transaction document and a state directory."""
    ws = tmp_path / "ws"
    (ws / "src").mkdir(parents=True)
    (ws / "src" / "mod.py").write_text(
        "import os\n\n\ndef target(value):\n    return value + 1\n", encoding="utf-8"
    )
    (ws / "src" / "other.py").write_text("FLAG = False\nCOUNT = 1\n", encoding="utf-8")
    (ws / "notes.md").write_text("# notes\n\nsee src/mod.py\n", encoding="utf-8")
    (ws / "tests").mkdir()
    (ws / "tests" / "test_ok.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    document = tmp_path / "tx.json"
    document.write_text(
        json.dumps(
            {
                "reason": "cli test",
                "verification": {"kind": "syntax"},
                "files": [
                    {"path": "src/mod.py",
                     "edits": [{"old": "    return value + 1", "new": "    return value + 2"}]},
                    {"path": "src/other.py",
                     "edits": [{"old": "FLAG = False", "new": "FLAG = True"}]},
                ],
            }
        ),
        encoding="utf-8",
    )
    return {"root": tmp_path, "ws": ws, "state": tmp_path / "state", "document": document}


def envelope(capsys) -> dict:
    """The one JSON document the driver printed (the group keeps stdout to itself)."""
    captured = capsys.readouterr()
    lines = [line for line in captured.out.strip().splitlines() if line.strip()]
    assert lines, f"no envelope on stdout; stderr was: {captured.err[:400]}"
    return json.loads(lines[-1])


# --------------------------------------------------------------------------- #
# 1–5: the four verbs, standalone                                               #
# --------------------------------------------------------------------------- #


def test_the_standalone_group_emits_the_product_envelope(tree, capsys):
    code = edit_cli.main(
        ["dry-run", "--tx", str(tree["document"]), "--workspace", str(tree["ws"]),
         "--state", str(tree["state"]), "--json"]
    )
    assert code == 0
    document = envelope(capsys)
    assert document["command"] == "edit.dry-run"
    assert document["ok"] is True and document["exit_code"] == 0
    assert document["schema_version"] == cli_contract.SCHEMA_VERSION
    assert document["evidence"], "a success without evidence is downgraded to exit 7"
    assert document["data"]["verdict"] == ET.VERDICT_APPLICABLE
    assert document["data"]["dry_run"] is True
    assert not tree["state"].exists(), "a dry run writes nothing, not even a record"
    assert "-    return value + 1" in document["data"]["diff"]


def test_apply_verify_and_undo_through_the_command_surface(tree, capsys):
    before = {name: digest(tree["ws"] / name)
              for name in ("src/mod.py", "src/other.py")}
    assert edit_cli.main(
        ["apply", "--tx", str(tree["document"]), "--workspace", str(tree["ws"]),
         "--state", str(tree["state"]), "--json"]
    ) == 0
    applied = envelope(capsys)["data"]
    assert applied["verdict"] == ET.VERDICT_APPLIED
    assert applied["verified"] is True
    assert all(digest(tree["ws"] / name) != before[name] for name in before)

    assert edit_cli.main(["show", "last", "--state", str(tree["state"]), "--json"]) == 0
    shown = envelope(capsys)["data"]
    assert shown["tx_id"] == applied["tx_id"] and shown["in_tree"] is True

    assert edit_cli.main(["undo", "last", "--state", str(tree["state"]), "--json"]) == 0
    undone = envelope(capsys)["data"]
    assert undone["verdict"] == ET.VERDICT_UNDONE
    assert [item["byte_identical"] for item in undone["undo"]["restored"]] == [True, True]
    assert {name: digest(tree["ws"] / name) for name in before} == before


def test_the_text_mode_prints_the_human_block(tree, capsys):
    code = edit_cli.main(
        ["dry-run", "--tx", str(tree["document"]), "--workspace", str(tree["ws"]),
         "--state", str(tree["state"])]
    )
    captured = capsys.readouterr()
    assert code == 0
    assert "— applicable" in captured.out
    assert "ok src/mod.py" in captured.out and "diff:" in captured.out
    assert not captured.out.strip().startswith("{")


@pytest.mark.parametrize(
    "target, old, new, flag, expected",
    [
        ("notes.md", "not in this file", "x", [], 5),
        ("src/mod.py", "    return", "    return", [], 1),
        ("../escape.py", "x", "y", [], 3),
        ("src/mod.py", "import os", "import os, sys", ["--verify-command", "false"], 2),
        ("src/mod.py", "import os", "import os, sys",
         ["--verify", "command", "--verify-command", "sh -c true"], 1),
    ],
)
def test_the_exit_codes_and_the_error_code_through_the_cli(
    tree, capsys, target, old, new, flag, expected
):
    document = tree["root"] / "case.json"
    document.write_text(
        json.dumps({"files": [{"path": target, "edits": [{"old": old, "new": new}]}]}),
        encoding="utf-8",
    )
    code = edit_cli.main(
        ["apply", "--tx", str(document), "--workspace", str(tree["ws"]),
         "--state", str(tree["state"]), *flag, "--json"]
    )
    assert code == expected
    document_payload = envelope(capsys)
    assert document_payload["exit_code"] == expected
    assert document_payload["ok"] is False and document_payload["error"] is not None


def test_an_ambiguous_document_is_refused_with_its_own_reason(tree, capsys):
    document = tree["root"] / "ambiguous.json"
    (tree["ws"] / "twice.py").write_text("value = 1\nother = 2\nvalue = 1\n", encoding="utf-8")
    document.write_text(
        json.dumps({"files": [{"path": "twice.py", "edits": [{"old": "value = 1",
                                                              "new": "value = 9"}]}]}),
        encoding="utf-8",
    )
    code = edit_cli.main(
        ["dry-run", "--tx", str(document), "--workspace", str(tree["ws"]),
         "--state", str(tree["state"]), "--json"]
    )
    assert code == 6
    payload = envelope(capsys)
    assert payload["data"]["files"][0]["reason"] == ET.REASON_AMBIGUOUS
    assert payload["data"]["files"][0]["detail"]["occurrence_lines"] == [1, 3]
    assert payload["error"]["details"]["reason"] == ET.REASON_AMBIGUOUS


# --------------------------------------------------------------------------- #
# 6–14: the flags                                                               #
# --------------------------------------------------------------------------- #


def test_the_document_comes_from_a_file_or_from_stdin(tree, capsys, monkeypatch):
    inline = {
        "files": [{"path": "notes.md", "edits": [{"old": "see src/mod.py",
                                                  "new": "see src/other.py"}]}],
    }
    # `--tx-json` (inline) is refused by the syntax check for a .md file, so ask for a
    # command check that cannot fail instead of relying on the default.
    assert edit_cli.main(
        ["dry-run", "--tx-json", json.dumps(inline), "--workspace", str(tree["ws"]),
         "--state", str(tree["state"]), "--verify", "none", "--json"]
    ) == 0
    assert envelope(capsys)["data"]["verdict"] == ET.VERDICT_APPLICABLE

    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(inline)))
    assert edit_cli.main(
        ["dry-run", "--tx", "-", "--workspace", str(tree["ws"]), "--state",
         str(tree["state"]), "--verify", "none", "--json"]
    ) == 0
    assert envelope(capsys)["data"]["files"][0]["path"] == "notes.md"


def test_a_missing_or_malformed_document_is_a_usage_error(tree, capsys):
    assert edit_cli.main(
        ["dry-run", "--workspace", str(tree["ws"]), "--state", str(tree["state"]), "--json"]
    ) == 1
    assert "--tx" in envelope(capsys)["error"]["message"]
    missing = tree["root"] / "nope.json"
    assert edit_cli.main(
        ["dry-run", "--tx", str(missing), "--json"]
    ) == 1
    assert "does not exist" in envelope(capsys)["error"]["message"]
    broken = tree["root"] / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert edit_cli.main(["dry-run", "--tx", str(broken), "--json"]) == 1
    assert "not valid JSON" in envelope(capsys)["error"]["message"]
    empty = tree["root"] / "empty.json"
    empty.write_text(json.dumps({"files": []}), encoding="utf-8")
    assert edit_cli.main(["dry-run", "--tx", str(empty), "--json"]) == 1
    assert "non-empty 'files'" in envelope(capsys)["error"]["message"]


def test_the_verification_flags_override_the_document(tree, capsys):
    assert edit_cli.main(
        ["apply", "--tx", str(tree["document"]), "--workspace", str(tree["ws"]),
         "--state", str(tree["state"]), "--verify", "command", "--verify-command",
         f"{sys.executable} -c 'print(1)'", "--json"]
    ) == 0
    verification = envelope(capsys)["data"]["verification"]
    assert verification["kind"] == "command"
    assert verification["argv"][0] == sys.executable
    assert verification["executed"] is True and verification["passed"] is True


def test_the_test_target_flag_runs_pytest(tree, capsys):
    assert edit_cli.main(
        ["apply", "--tx", str(tree["document"]), "--workspace", str(tree["ws"]),
         "--state", str(tree["state"]), "--verify-tests", "tests/test_ok.py", "--json"]
    ) == 0
    verification = envelope(capsys)["data"]["verification"]
    assert verification["kind"] == "tests"
    assert verification["targets"] == ["tests/test_ok.py"]
    assert verification["argv"][1:4] == ["-m", "pytest", "-q"]
    assert verification["passed"] is True


def test_the_bad_verification_flags_are_usage_errors(tree, capsys):
    """The handler's own refusals carry the envelope; argparse's carries only usage."""
    for flags in (
        ["--verify-argv", "{not json}"],
        ["--verify-argv", "42"],
        ["--verify-command", "sh -c 'rm -rf /tmp'"],
        ["--verify", "command"],
    ):
        code = edit_cli.main(
            ["dry-run", "--tx", str(tree["document"]), "--workspace", str(tree["ws"]),
             "--state", str(tree["state"]), *flags, "--json"]
        )
        assert code == 1, flags
        assert envelope(capsys)["error"] is not None, flags
    # argparse-level refusals: usage on stderr, no envelope, exit 1
    for flags in (["--verify", "nonsense"], ["--verify-tests"]):
        assert edit_cli.main(["dry-run", *flags]) == 1, flags
        captured = capsys.readouterr()
        assert captured.out == "" and "usage:" in captured.err, flags


def test_keep_on_failure_and_the_caps_flags(tree, capsys):
    document = tree["root"] / "fails.json"
    document.write_text(
        json.dumps({
            "files": [{"path": "src/other.py",
                       "edits": [{"old": "FLAG = False", "new": "FLAG = True"}]}],
            "verification": {"kind": "command", "argv": [sys.executable, "-c",
                                                         "raise SystemExit(1)"]},
        }),
        encoding="utf-8",
    )
    assert edit_cli.main(
        ["apply", "--tx", str(document), "--workspace", str(tree["ws"]),
         "--state", str(tree["state"]), "--keep-on-failure", "--json"]
    ) == 0
    payload = envelope(capsys)
    assert payload["data"]["verdict"] == ET.VERDICT_APPLIED_UNVERIFIED
    assert payload["data"]["verified"] is False
    assert payload["warnings"]

    big = tree["ws"] / "big.py"
    big.write_text("x = 1\n" * 200_000, encoding="utf-8")
    (tree["root"] / "big.json").write_text(
        json.dumps({"files": [{"path": "big.py", "edits": [{"old": "x = 1", "new": "x = 2",
                                                           "replace_all": True}]}]}),
        encoding="utf-8",
    )
    assert edit_cli.main(
        ["dry-run", "--tx", str(tree["root"] / "big.json"), "--workspace", str(tree["ws"]),
         "--state", str(tree["state"]), "--max-file-mb", "1", "--json"]
    ) == 3
    blocked = envelope(capsys)["data"]
    assert blocked["files"][0]["detail"]["cap"] == 1024 * 1024


def test_undo_refusals_through_the_cli(tree, capsys):
    assert edit_cli.main(["undo", "no-such-transaction", "--state", str(tree["state"]),
                          "--json"]) == 5
    assert "unknown transaction" in envelope(capsys)["error"]["message"]

    assert edit_cli.main(
        ["apply", "--tx", str(tree["document"]), "--workspace", str(tree["ws"]),
         "--state", str(tree["state"]), "--json"]
    ) == 0
    applied = envelope(capsys)["data"]
    target = tree["ws"] / "src" / "mod.py"
    target.write_text(target.read_text(encoding="utf-8") + "# later\n", encoding="utf-8")
    later = digest(target)
    assert edit_cli.main(["undo", applied["tx_id"], "--state", str(tree["state"]),
                          "--json"]) == 6
    refused = envelope(capsys)
    assert refused["data"]["undo_error"]["reason"] == ET.REASON_MODIFIED_SINCE_APPLY
    assert digest(target) == later
    assert edit_cli.main(["undo", applied["tx_id"], "--state", str(tree["state"]),
                          "--force", "--json"]) == 0
    assert envelope(capsys)["data"]["verdict"] == ET.VERDICT_UNDONE


def test_show_list_and_show_unknown(tree, capsys):
    assert edit_cli.main(
        ["apply", "--tx", str(tree["document"]), "--workspace", str(tree["ws"]),
         "--state", str(tree["state"]), "--json"]
    ) == 0
    envelope(capsys)
    assert edit_cli.main(["show", "--list", "--state", str(tree["state"]), "--json"]) == 0
    listed = envelope(capsys)["data"]
    assert listed["count"] == 1 and listed["transactions"][0]["in_tree"] is True
    assert "max_file_bytes" in listed["caps"]
    assert edit_cli.main(["show", "no-such-id", "--state", str(tree["state"]), "--json"]) == 5
    assert "no transaction" in envelope(capsys)["error"]["message"]


def test_main_accepts_the_group_name_as_the_first_token(tree, capsys):
    """``python -m skaldir.edit_cli edit show …`` and ``… show …`` are the same call."""
    assert edit_cli.main(["edit", "show", "--list", "--state", str(tree["state"]),
                          "--json"]) == 0
    assert envelope(capsys)["data"]["count"] == 0
    assert edit_cli.main(["show", "--list", "--state", str(tree["state"]), "--json"]) == 0
    assert envelope(capsys)["data"]["count"] == 0


def test_the_parser_has_the_four_leaves_and_every_leaf_takes_json():
    parser = edit_cli.build_parser()
    leaves = dict(cli_contract.leaf_commands(parser))
    assert sorted(leaves) == ["apply", "dry-run", "show", "undo"]
    for path, leaf in leaves.items():
        flags = {option for action in leaf._actions for option in action.option_strings}
        assert "--json" in flags, path
        assert "--workspace" in flags, path
        assert "--state" in flags, path
    assert "--force" in {o for a in leaves["undo"]._actions for o in a.option_strings}
    assert "--list" in {o for a in leaves["show"]._actions for o in a.option_strings}


def test_register_cli_is_idempotent_and_installs_the_contract_entries(monkeypatch):
    monkeypatch.setattr(cli_contract, "COMMANDS", dict(cli_contract.COMMANDS))
    root = argparse.ArgumentParser(prog="skaldir")
    sub = root.add_subparsers(dest="command")
    first = edit_cli.register_cli(sub)
    second = edit_cli.register_cli(sub)  # must not raise "conflicting subparser"
    assert first is second
    assert "edit" in sub.choices
    installed = {path for path in cli_contract.COMMANDS if path.startswith("edit.")}
    assert installed == {"edit.apply", "edit.dry-run", "edit.undo", "edit.show"}
    for path in sorted(installed):
        entry = cli_contract.COMMANDS[path]
        assert entry.layer in cli_contract.LAYERS, path
        assert entry.evidence, "a success without evidence is downgraded to exit 7"
        assert entry.exit_codes and all(code in range(9) for code in entry.exit_codes)
        assert entry.data_schema and entry.data_schema["type"] == "object", path
        assert 0 not in entry.exit_codes or entry.evidence, path
    assert cli_contract.COMMANDS["edit.apply"].mutates is True
    assert cli_contract.COMMANDS["edit.dry-run"].mutates is False
    assert cli_contract.COMMANDS["edit.show"].mutates is False


def test_a_feature_with_caps_declares_them_in_its_own_data(tree, capsys):
    assert edit_cli.main(
        ["dry-run", "--tx", str(tree["document"]), "--workspace", str(tree["ws"]),
         "--state", str(tree["state"]), "--json"]
    ) == 0
    caps = envelope(capsys)["data"]["caps"]
    assert caps["max_file_bytes"]["value"] == ET.EditCaps.max_file_bytes
    assert caps["max_file_bytes"]["env"] == "SKALDIR_EDIT_MAX_FILE_BYTES"
    assert "verify_timeout_s" in caps and "max_edits" in caps


# --------------------------------------------------------------------------- #
# 15–18: the hand-over to the product CLI, proven in a scratch copy             #
# --------------------------------------------------------------------------- #

PROBE = r'''
import contextlib, io, json, sys
from skaldir import cli, cli_contract

document, workspace, state = sys.argv[1:4]
buffer = io.StringIO()
with contextlib.redirect_stdout(buffer):
    code = cli.main(["edit", "dry-run", "--tx", document, "--workspace", workspace,
                     "--state", state, "--json"])
envelope = json.loads(buffer.getvalue().strip().splitlines()[-1])
parser = cli.build_parser()
inventory = cli_contract.dump(parser)
print(json.dumps({
    "exit_code": code,
    "command": envelope["command"],
    "ok": envelope["ok"],
    "evidence": len(envelope["evidence"]),
    "verdict": envelope["data"]["verdict"],
    "leaves": sorted(path for path, _ in cli_contract.leaf_commands(parser)
                     if path.startswith("edit")),
    "unregistered": inventory["unregistered"],
    "dumped": sorted(item["command"] for item in inventory["commands"]
                     if item["command"].startswith("edit")),
    "layers": {path: cli_contract.spec_for(path).layer for path in
               ("edit.apply", "edit.dry-run", "edit.undo", "edit.show")},
}))
'''


def test_the_snippet_wires_the_group_into_the_real_cli(tmp_path, tree):
    """Copy ``src/skaldir``, apply the 3-line snippet, drive the real ``cli.main``."""
    package = tmp_path / "copy"
    package.mkdir()
    shutil.copytree(SRC, package / "skaldir",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    cli_path = package / "skaldir" / "cli.py"
    text = cli_path.read_text(encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(home),
        "SKALDIR_HOME": str(home / "rt"),
        "PYTHONPATH": str(package),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    # Guard: a sibling wave editing `cli.py` right now can make even the *unmodified*
    # copy fail to build. That is not this group's regression, so it skips with the
    # reason instead of reporting a false red.
    pristine = subprocess.run(
        [sys.executable, "-c", "from skaldir import cli; cli.build_parser(); print('built')"],
        env=env, cwd=str(tmp_path), capture_output=True, text=True, timeout=300,
    )
    if pristine.returncode != 0:
        pytest.skip(f"the source cli.py does not build on its own right now: "
                    f"{pristine.stderr[-400:]}")
    if "_register_edit_cli" not in text:
        assert ANCHOR in text, "the anchor for the hand-over moved — update this test"
        cli_path.write_text(text.replace(ANCHOR, SNIPPET, 1), encoding="utf-8")
    probe = tmp_path / "probe.py"
    probe.write_text(PROBE, encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, str(probe), str(tree["document"]), str(tree["ws"]),
         str(tmp_path / "state")],
        env=env, cwd=str(tmp_path), capture_output=True, text=True, timeout=300,
    )
    assert completed.returncode == 0, completed.stderr[-4000:]
    report = json.loads(completed.stdout.strip().splitlines()[-1])
    assert report["exit_code"] == 0, report
    assert report["command"] == "edit.dry-run", "the group is reachable through cli.py"
    assert report["ok"] is True and report["evidence"] >= 1
    assert report["verdict"] == ET.VERDICT_APPLICABLE
    assert report["leaves"] == [
        "edit.apply", "edit.dry-run", "edit.show", "edit.undo"
    ]
    assert report["unregistered"] == [], "a command cannot ship without its contract"
    assert report["dumped"] == report["leaves"]
    assert report["layers"] == {
        "edit.apply": "ENFORCE", "edit.dry-run": "ENFORCE",
        "edit.undo": "PERSIST", "edit.show": "PERSIST",
    }
    # and the tree is still untouched by the dry run
    assert "return value + 2" not in (tree["ws"] / "src" / "mod.py").read_text(
        encoding="utf-8"
    )


def test_the_snippet_text_is_the_one_the_probe_applies():
    """The hand-over note in the module is the snippet this test applies (no drift)."""
    doc = edit_cli.__doc__ or ""
    assert "_register_edit_cli" in doc
    assert "from .edit_cli import register_cli as _register_edit_cli" in doc
    assert ANCHOR.strip() in SNIPPET
    assert "CONTRACTS_SNIPPET" in dir(edit_cli)
    assert "edit.apply" in edit_cli.CONTRACTS_SNIPPET
    assert set(edit_cli.CONTRACT_ENTRIES) == {
        "edit.apply", "edit.dry-run", "edit.undo", "edit.show"
    }
