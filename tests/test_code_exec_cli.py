"""``skaldir code …``: the two verbs, their contract entries and the wiring hook.

The group is not wired into ``cli.py`` yet (the CLI owner adds the two lines — see the
module docstring of :mod:`skaldir.code_exec_cli`), so the interesting assertions are:

* the standalone parser really runs both verbs and returns the documented exit codes;
* ``register_cli`` installs its own ``cli_contract.COMMANDS`` entries and is idempotent
  (the "conflicting subparser" trap that bit the ``jev`` group);
* the hand-over works **against the real product parser** — proven in a subprocess, so
  the global ``COMMANDS``/parser mutation cannot leak into the rest of the suite.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import pytest

from skaldir import cli_contract, code_exec, code_exec_cli

REPO = Path(__file__).resolve().parents[1]
CONTRACT_PATHS = {"code.run", "code.explain"}


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "notes.txt").write_text("alpha\nTODO: fix\n", encoding="utf-8")
    return ws


def _argv(*parts: str, workspace: Path) -> list[str]:
    return [*parts, "--workspace", str(workspace)]


# --------------------------------------------------------------- the group


def test_the_standalone_parser_exposes_both_verbs() -> None:
    parser = code_exec_cli.build_parser()
    action = next(
        item for item in parser._actions if isinstance(item, argparse._SubParsersAction)
    )
    assert set(action.choices) == {"run", "explain"}
    for name in ("run", "explain"):
        leaf = action.choices[name]
        options = {option for item in leaf._actions for option in item.option_strings}
        assert {"--json", "--format", "--quiet", "--no-color"} <= options
        assert "--workspace" in options


def test_register_cli_installs_its_own_contract_entries(monkeypatch) -> None:
    """The group carries its ``COMMANDS`` half, and re-registering changes nothing."""
    class StubContract:
        """Stands in for ``cli_contract`` so this test never edits the real table."""

        COMMANDS: dict = {}
        CommandSpec = cli_contract.CommandSpec

        @staticmethod
        def add_common_flags(parser: argparse.ArgumentParser) -> None:
            cli_contract.add_common_flags(parser)

    monkeypatch.setattr(code_exec_cli, "_CONTRACT", StubContract)

    parser = argparse.ArgumentParser(prog="skaldir")
    sub = parser.add_subparsers(dest="command")
    parent = code_exec_cli.register_cli(sub)

    assert set(StubContract.COMMANDS) == CONTRACT_PATHS, "install_contract_entries ran"
    entries = code_exec_cli.proposed_contract_entries()
    for path, spec in entries.items():
        assert path in StubContract.COMMANDS
        assert spec.layer in cli_contract.LAYERS
        assert spec.exit_codes and all(code in range(9) for code in spec.exit_codes)
        assert spec.data_schema and spec.data_schema["type"] == "object"
        assert spec.evidence, f"{path} has no default evidence"
    assert entries["code.run"].mutates is True
    assert 8 in entries["code.run"].exit_codes, "a timeout kill is exit 8"
    assert set(entries["code.explain"].exit_codes) == {0, 1, 3, 7}

    assert parent.prog == "skaldir code"
    verbs = next(item for item in parent._actions if isinstance(item, argparse._SubParsersAction))
    assert set(verbs.choices) == {"run", "explain"}
    assert code_exec_cli.register_cli(sub) is parent, "a second registration is a no-op"
    assert len(StubContract.COMMANDS) == len(CONTRACT_PATHS)


def test_my_verbs_are_reachable_on_the_real_product_parser(workspace: Path) -> None:
    """In a subprocess: the real ``cli.build_parser()`` reaches ``code.run``/``code.explain``.

    Run out of process on purpose — the wiring mutates the product parser and
    ``cli_contract.COMMANDS``, which must not leak into the rest of the suite. The group
    ``code`` is shared with the repo-map block (``code.map``, ``code.search``, …), so
    this asserts *membership* of my two verbs, not that they are the only ones — and it
    runs a real snippet through the product driver, which is the only proof that counts.
    """
    script = (
        "import argparse, json, sys\n"
        "from skaldir import cli, cli_contract\n"
        "parser = cli.build_parser()\n"
        "sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))\n"
        "group = cli_contract.leaf_commands(parser)\n"
        "leaves = sorted(path for path, _p in group if path.startswith('code.'))\n"
        "registered = {p: cli_contract.registered(p) for p in ('code.run', 'code.explain')}\n"
        "inventory = cli_contract.dump(parser)\n"
        "snippet = 'import skaldir\\nr = skaldir.call(\"list_dir\", '\n"
        "snippet += \"path='.', limit=5)\\nresult = {'ok': r['ok'], '\"\n"
        "snippet += \"'entries': r['output']['count']}\\n\"\n"
        "import io\n"
        "sys.stdin = io.StringIO(snippet)\n"
        "code = cli.main(['code', 'run', '--stdin', '--workspace', "
        "\"" + str(workspace) + "\", '--json'])\n"
        "print('__REPORT__' + json.dumps({'leaves': leaves, 'registered': registered,\n"
        "  'unregistered': inventory['unregistered'], 'exit_code': code,\n"
        "  'run_layer': next(i['layer'] for i in inventory['commands']\n"
        "                    if i['command'] == 'code.run')}))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    reports = [
        json.loads(line[len("__REPORT__") :])
        for line in completed.stdout.splitlines()
        if line.startswith("__REPORT__")
    ]
    assert completed.returncode == 0, completed.stderr[-3000:]
    assert len(reports) == 1, completed.stdout[-2000:] + completed.stderr[-2000:]
    payload = reports[0]
    assert {"code.run", "code.explain"} <= set(payload["leaves"])
    assert payload["registered"] == {"code.run": True, "code.explain": True}
    assert payload["unregistered"] == [], "the dump must not report an unregistered leaf"
    assert payload["run_layer"] in cli_contract.LAYERS
    assert payload["exit_code"] == 0, completed.stderr[-2000:]


def test_the_documented_hand_over_matches_the_module() -> None:
    """The wiring the docstring shows is the wiring ``cli.py`` uses (shared group)."""
    doc = code_exec_cli.__doc__ or ""
    assert "from .code_exec_cli import register_cli as _register_code_exec" in doc
    assert "_register_code_exec(sub)" in doc
    assert "create-or-extend" in doc, "the shared `code` group must be documented"


def test_register_cli_extends_a_group_that_already_exists() -> None:
    """Sharing the ``code`` name must never drop my verbs (the repo-map block owns it)."""
    parser = argparse.ArgumentParser(prog="skaldir")
    sub = parser.add_subparsers(dest="command")
    existing = sub.add_parser("code", help="the repo map")
    other_verbs = existing.add_subparsers(dest="code_command")
    other_verbs.add_parser("map", help="the repo map")

    parent = code_exec_cli.register_cli(sub)
    assert parent is existing, "the existing group is extended, not replaced"
    verbs = code_exec_cli._verb_action(parent)
    assert {"map", "run", "explain"} <= set(verbs.choices)
    assert code_exec_cli.register_cli(sub) is existing
    assert set(code_exec_cli._verb_action(parent).choices) == {"map", "run", "explain"}


# --------------------------------------------------------------------- run


def test_code_run_stdin_prints_the_report(workspace: Path, capsys, monkeypatch) -> None:
    monkeypatch.setattr(
        sys,
        "stdin",
        _Pipe("import skaldir\nr = skaldir.call('read_file', path='notes.txt', limit=1)\n"
              "result = {'ok': r['ok'], 'first': r['output']['content']}\n"),
    )
    code = code_exec_cli.main(_argv("run", "--stdin", workspace=workspace))
    out = capsys.readouterr().out
    assert code == 0
    report = json.loads(out)
    assert report["ok"] is True and report["status"] == "ok"
    assert report["value"]["ok"] is True
    assert report["value"]["first"].startswith("1|alpha")
    assert report["tool_calls"][0]["tool"] == "read_file"
    assert report["tool_calls"][0]["policy_classification"] == "ALLOW"
    assert report["workspace_unchanged"] is True
    assert report["origin"] == "<stdin>"
    assert report["warnings"], "the environment scrub is reported, not hidden"


def test_code_run_from_a_file_uses_the_file_as_the_snippet(
    workspace: Path, capsys, tmp_path
) -> None:
    snippet = tmp_path / "snippet.py"
    snippet.write_text("result = {'from': 'file'}\n", encoding="utf-8")
    code = code_exec_cli.main(_argv("run", "--file", str(snippet), workspace=workspace))
    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["value"] == {"from": "file"}
    assert report["origin"] == str(snippet)


def test_code_run_text_mode_is_the_human_view(workspace: Path, capsys) -> None:
    code = code_exec_cli.main(
        _argv("run", "--file", str(_snippet(workspace)), "--text", workspace=workspace)
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "status: ok" in out
    assert "tool calls: 1/25" in out
    with pytest.raises(json.JSONDecodeError):
        json.loads(out)


def test_code_run_exit_codes(workspace: Path, capsys, monkeypatch) -> None:
    """Missing file 5, empty snippet 1, a raising snippet 0 (measured) or 2 with the flag."""
    assert code_exec_cli.main(_argv("run", "--file", "/nope.py", workspace=workspace)) == 5
    capsys.readouterr()

    assert code_exec_cli.main([]) == 1, "no verb at all is a usage error"
    capsys.readouterr()

    monkeypatch.setattr(sys, "stdin", _Pipe("   \n"))
    assert code_exec_cli.main(_argv("run", "--stdin", workspace=workspace)) == 1
    capsys.readouterr()

    monkeypatch.setattr(sys, "stdin", _Pipe("raise SystemExit(3)\n"))
    assert code_exec_cli.main(_argv("run", "--stdin", workspace=workspace)) == 0, (
        "a raising snippet is a measured result"
    )
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "error" and report["error_type"] == "SystemExit"
    assert code_exec_cli.main(
        _argv("run", "--stdin", "--fail-on-error", workspace=workspace)
    ) == 2
    capsys.readouterr()

    monkeypatch.setattr(sys, "stdin", _Pipe("import time\ntime.sleep(60)\n"))
    assert code_exec_cli.main(
        _argv("run", "--stdin", "--timeout", "1", workspace=workspace)
    ) == 8, "a killed runaway is exit 8"
    capsys.readouterr()


def test_code_run_refuses_a_sandbox_it_cannot_honour(workspace: Path, capsys) -> None:
    """A deny list covering every core tool is a refusal (exit 3), not a run.

    The list is **derived** (see the sandbox twin in ``test_code_exec_sandbox.py``): the
    hand-written 8 names kept ``docker.ensure`` allowed, so the run legitimately succeeded
    and this test failed on ``assert 0 == 3``. The size assertion makes a tenth tool fail
    loudly here instead of quietly.
    """
    from skaldir.tools_core import CORE_TOOL_NAMES

    every_tool = code_exec._core_names()
    assert set(every_tool) == set(CORE_TOOL_NAMES), (
        "the sandbox's names and the toolset's declared names disagree: "
        f"{every_tool} vs {CORE_TOOL_NAMES}"
    )
    assert len(every_tool) == 9, (
        f"the core toolset changed size ({len(every_tool)}): {every_tool} — "
        "deny every name this test now sees, and update the count"
    )
    empty_allowlist: list[str] = []
    for name in every_tool:
        empty_allowlist += ["--deny-tool", name]
    code = code_exec_cli.main(
        [*_argv("run", "--file", str(_snippet(workspace)), workspace=workspace), *empty_allowlist]
    )
    assert code == 3
    assert "no tool is allowed" in capsys.readouterr().err


# ----------------------------------------------------------------- explain


def test_code_explain_documents_the_policy(workspace: Path, capsys) -> None:
    code = code_exec_cli.main(_argv("explain", workspace=workspace))
    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["policy"]["workspace"] == str(workspace)
    assert report["limits"]["wall_clock_s"] == 10
    assert report["limits"]["tool_calls"] == 25
    assert report["tools"]["allowed"] and "read_file" in report["tools"]["allowed"]
    refused = {row["name"]: row["reason"] for row in report["tools"]["refused"]}
    assert "network is not declared" in refused["web_fetch"]
    assert report["environment"]["scrub"]["available"] is True
    assert report["environment"]["imports"] == "stdlib only"
    assert report["filesystem"]["workspace_digest"]["files"] >= 1
    assert len(report["limits_not_claimed"]) >= 4


def test_code_explain_can_show_the_child_runner(workspace: Path, capsys) -> None:
    assert code_exec_cli.main(_argv("explain", "--runner", workspace=workspace)) == 0
    report = json.loads(capsys.readouterr().out)
    source = report["runner_source"]
    assert "class HostAPI" in source
    assert "skaldir.call(name, **arguments)" in source or "def call(self, name" in source


def test_code_explain_text_mode_prints_the_limits(workspace: Path, capsys) -> None:
    assert code_exec_cli.main(_argv("explain", "--text", workspace=workspace)) == 0
    out = capsys.readouterr().out
    assert "sandbox workspace:" in out
    assert "SIGKILL to the whole process group" in out
    assert "not claimed:" in out


# ------------------------------------------------------------------ helpers


class _Pipe:
    """A minimal stdin stand-in (``monkeypatch.setattr(sys, 'stdin', …)``)."""

    def __init__(self, text: str) -> None:
        self._text = text

    def read(self, *_args) -> str:
        return self._text


def _snippet(workspace: Path) -> Path:
    path = workspace / "snippet.py"
    path.write_text(
        "import skaldir\nr = skaldir.call('list_dir', path='.', limit=4)\nresult = r['ok']\n",
        encoding="utf-8",
    )
    return path
