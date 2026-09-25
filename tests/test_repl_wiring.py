"""The hand-over snippet, verified against the real parser in a copy — never claimed.

``cli.py`` belongs to the CLI owner, so this file proves the three lines by **applying
them** to a throwaway copy of ``src/skaldir`` and exercising the real thing there:

* ``build_parser()`` really gains the ``shell`` group (and ``annotate`` gives the leaf
  its dotted ``command_path`` and the common flags);
* ``cli_contract.dump()`` sees the leaf **and** its ``COMMANDS`` entry (the module
  registers its own contract entry — no edit to ``cli_contract.py``);
* ``cli.main(["shell", …])`` really runs the shell, verb by verb, through the product
  CLI's one entry point.

Once the owner wires ``cli.py`` for real, the copy step is skipped and the same
assertions run against the live parser.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"

ANCHOR = "    _register_jev_cli(sub)\n"
SNIPPET = ANCHOR + (
    "\n    from .repl import register_cli as _register_repl_cli\n"
    "\n"
    "    _register_repl_cli(sub)\n"
)

PROBE = r"""
import json
from skaldir import cli, cli_contract

parser = cli.build_parser()
sub = [a for a in parser._actions if type(a).__name__ == "_SubParsersAction"][0]
args = parser.parse_args(["shell", "--json", "--limit", "0"])

inventory = cli_contract.dump(parser)
leaf = next((c for c in inventory["commands"] if c["command"] == "shell"), None)

print(json.dumps({
    "in_choices": "shell" in sub.choices,
    "command_path": getattr(args, "command_path", None),
    "has_json": args.json,
    "leaf": None if leaf is None else {
        "registered": leaf["registered"],
        "layer": leaf["layer"],
        "mutates": leaf["mutates"],
        "evidence": len(leaf["default_evidence"]),
        "data_available": leaf["data_available"],
    },
    "unregistered": inventory["unregistered"],
}))
"""


def _arguments(**overrides) -> argparse.Namespace:
    args = {"command": "", "runtime": "", "resume": False, "transcript": "", "limit": 0,
            "color": False, "no_color": False, "probe": True, "echo": None, "quiet": False}
    args.update(overrides)
    return argparse.Namespace(**args)


def test_register_cli_is_idempotent_and_has_the_envelope_contract():
    from skaldir import cli_contract, repl

    class FakeSub:
        def __init__(self):
            self.choices: dict = {}

        def add_parser(self, name, **kwargs):
            import argparse

            parser = argparse.ArgumentParser(prog=f"skaldir {name}")
            self.choices[name] = parser
            return parser

    handle = FakeSub()
    first = repl.register_cli(handle)
    second = repl.register_cli(handle)  # must not raise "conflicting subparser"
    assert first is second is handle.choices["shell"]
    assert "shell" in cli_contract.COMMANDS
    entry = cli_contract.COMMANDS["shell"]
    assert entry.evidence, "a success without evidence is downgraded to exit 7"
    assert "history" in entry.notes


def test_cmd_shell_reports_blocked_when_every_command_fails(tmp_path, monkeypatch):
    from skaldir import repl

    monkeypatch.setenv("GODSV_HOME", str(tmp_path))

    class FailingSession(repl.Session):
        def execute(self, argv):
            return repl.RunResult(argv=tuple(argv), exit_code=1)

    class FailingShell(repl.Shell):
        def __init__(self, options):
            super().__init__(options, session=FailingSession(runtime=tmp_path))
            self.stdin = __import__("io").StringIO("selftest\n/quit\n")

    monkeypatch.setattr(repl, "Shell", FailingShell)
    assert repl._cmd_shell(_arguments(probe=False)) == repl.EXIT_BLOCKED


def _copy_with_snippet(tmp_path: Path) -> tuple[Path, str, bool]:
    """Copy ``src/skaldir`` (the product package — ``godsv`` is only an alias); apply
    the snippet unless ``cli.py`` already has it."""
    package = tmp_path / "skaldir"
    shutil.copytree(
        SRC / "skaldir",
        package,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    cli_path = package / "cli.py"
    text = cli_path.read_text(encoding="utf-8")
    if "register_repl_cli" in text or "_register_repl_cli(sub)" in text:
        return tmp_path, "already wired in cli.py", False
    assert ANCHOR in text, "the anchor for the hand-over moved — update this test"
    cli_path.write_text(text.replace(ANCHOR, SNIPPET, 1), encoding="utf-8")
    return tmp_path, "snippet applied to a copy", True


def test_the_snippet_wires_the_shell_into_the_real_parser(tmp_path):
    root, how, applied = _copy_with_snippet(tmp_path)
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path / "home"),
        "GODSV_HOME": str(tmp_path / "rt"),
        "PYTHONPATH": str(root),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    (tmp_path / "home").mkdir(exist_ok=True)
    result = subprocess.run(
        [sys.executable, "-c", PROBE],
        env=env,
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stderr
    import json

    report = json.loads(result.stdout.strip().splitlines()[-1])
    assert report["in_choices"] is True, how
    assert report["command_path"] == "shell"
    assert report["has_json"] is True
    assert report["leaf"] is not None
    assert report["leaf"]["registered"] is True, "a command cannot ship without a contract"
    assert report["leaf"]["mutates"] is True
    assert report["leaf"]["evidence"] >= 1
    assert report["leaf"]["data_available"] is True
    assert "shell" not in report["unregistered"]
    if applied:
        # the contract invariant test is the CLI's own gate: it must stay green
        tests = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-o", "addopts=", "--no-header",
             str(REPO / "tests/test_cli_contract_dump.py")],
            env={**env, "PYTHONPATH": f"{root}:{REPO / 'tests'}"},
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=600,
        )
        assert tests.returncode == 0, tests.stdout[-4000:] + tests.stderr[-2000:]


def test_the_snippet_reaches_the_product_entry_point(tmp_path):
    root, _how, _applied = _copy_with_snippet(tmp_path)
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path / "home"),
        "GODSV_HOME": str(tmp_path / "rt"),
        "PYTHONPATH": str(root),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    (tmp_path / "home").mkdir(exist_ok=True)
    program = (
        "import sys; from skaldir.cli import main; "
        "raise SystemExit(main(['shell', '--no-probe', '--limit', '2']))"
    )
    result = subprocess.run(
        [sys.executable, "-c", program],
        env=env,
        cwd=str(root),
        input="tools l\t\n/quit\n",
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert "interactive shell" in result.stdout
    assert "insert 'list'" in result.stdout  # completion through the product path
    assert "exit 0 (OK)" in result.stdout  # a real command ran
    assert (tmp_path / "rt" / "shell" / "history.jsonl").exists()


def test_launch_from_bare_call_keeps_a_pipe_on_the_welcome(monkeypatch, capsys, tmp_path):
    """A `skaldir` call without a TTY must not start a shell that waits for input."""
    from skaldir import repl

    monkeypatch.setenv("GODSV_HOME", str(tmp_path))
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(""))
    called: list[dict] = []

    def fake_welcome(*, no_color=False):
        called.append({"no_color": no_color})

    monkeypatch.setattr("skaldir.cli._print_welcome", fake_welcome)
    assert repl.launch_from_bare_call() == repl.EXIT_OK
    assert called == [{"no_color": False}]
    assert repl.launch_from_bare_call(no_color=True) == repl.EXIT_OK
    assert called[-1] == {"no_color": True}


def test_pytest_skips_cleanly_when_the_snippet_is_already_live():
    """A guard for the CLI owner: once cli.py is wired, this file keeps passing."""
    from skaldir import cli

    parser = cli.build_parser()
    sub = [a for a in parser._actions if type(a).__name__ == "_SubParsersAction"][0]
    if "shell" not in sub.choices:
        pytest.skip("not wired in this build yet — the copy test proves the snippet")
    args = parser.parse_args(["shell", "--limit", "0"])
    assert args.command_path == "shell"
