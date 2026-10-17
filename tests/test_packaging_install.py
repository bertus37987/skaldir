"""Install the CLI for real, then run the installed binary from another directory.

This is the shipping proof, not a metadata check:

* `uv tool install --from <repo> godsv` into a **throwaway** prefix
  (`UV_TOOL_DIR` / `UV_TOOL_BIN_DIR`), so the owner's real tool directory
  (`~/.local/share/uv/tools`) is never touched by a test run.
* the installed console script — not the checkout, not `python -m skaldir.cli` — answers
  `--version`, `contract dump --json` and `selftest --json` from an unrelated cwd, with
  `GODSV_HOME`/`HERMES_HOME` pointed at empty temp dirs so the result cannot depend on (or
  disturb) the live Skaldir runtime.
* `uv tool list` proves what got installed and which executable it exposes.
* teardown uninstalls it, which is also the uninstall path the shipping doc documents.

Naming trap worth remembering: the *distribution* is `godsv`, the *executable* is `skaldir`.
`uv tool install --from . skaldir` fails with "Package name (`godsv`) provided with `--from`
does not match install request (`skaldir`)" — install the distribution, run the script.

An environment failure (no `uv`, no network for the dependency wheels) skips; a *working
install that then cannot answer* fails, because that is a ship defect.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tomllib
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PYPROJECT = REPO / "pyproject.toml"
DIST = "skaldir-agent"
SCRIPT = "skaldir"
TIMEOUT = 900
LAYERS = {"EXPAND", "ACT", "COMPOSE", "ROUTE", "SUPERVISE", "ENFORCE", "VERIFY", "PERSIST"}


def _version() -> str:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["version"]


def _run(argv: list[str], env: dict, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(argv, env=env, cwd=cwd, capture_output=True, text=True, timeout=TIMEOUT)


def _field(text: str, name: str) -> str:
    match = re.search(rf"^{name} (.+)$", text, re.M)
    assert match, f"no {name!r} line in:\n{text}"
    return match.group(1)


@pytest.fixture(scope="module")
def tool(tmp_path_factory) -> Iterator[dict]:
    if not shutil.which("uv"):
        pytest.skip("uv is not installed — cannot prove the tool install path")
    root = tmp_path_factory.mktemp("gv-tool")
    env = dict(os.environ)
    env["UV_TOOL_DIR"] = str(root / "tools")
    env["UV_TOOL_BIN_DIR"] = str(root / "bin")
    env["GODSV_HOME"] = str(root / "runtime")
    env["HERMES_HOME"] = str(root / "hermes")
    # Measured trap: a `PYTHONPATH` inherited from the developer's shell makes the tool env's
    # interpreter import *that* tree instead of its own site-packages (here it resolved `godsv`
    # to `/home/willy/gv-mcp-wiring/src/godsv`, so `assets/` was not the installed one and
    # `PROMPTS` was 0). A real user does not have it; the proof must not either.
    for name in ("PYTHONPATH", "PYTHONHOME", "GODSV_PROMPTS"):
        env.pop(name, None)
    elsewhere = root / "elsewhere"
    elsewhere.mkdir()

    proc = _run(["uv", "tool", "install", "--from", str(REPO), DIST], env, elsewhere)
    raw = f"$ uv tool install --from {REPO} {DIST}\n{proc.stdout}{proc.stderr}"
    if proc.returncode != 0:
        if re.search(
            r"(failed to fetch|failed to download|offline|network|no such host|runtime not found)",
            raw,
            re.I,
        ):
            pytest.skip(f"dependencies are not available offline:\n{raw}")
        pytest.fail(f"the tool install failed — a ship defect (MVP/20-shipping.md §B):\n{raw}")

    binary = Path(env["UV_TOOL_BIN_DIR"]) / SCRIPT
    assert binary.exists(), f"no executable at {binary}\n{raw}"
    yield {"env": env, "cwd": elsewhere, "binary": binary, "raw": raw, "root": root}
    _run(["uv", "tool", "uninstall", DIST], env, elsewhere)


def test_uv_tool_list_names_the_installed_tool(tool):
    # `uv` >= 0.11 colorizes `tool list` even without a TTY when the environment forces
    # color (FORCE_COLOR/CLICOLOR_FORCE) — dependency drift measured 2026-10-03. Capture the
    # invocation colorless (`--color=never`) so the anchored assertion below stays on the
    # real installed-tool line; the name+version+script assertion itself is NOT weakened.
    out = _run(["uv", "tool", "list", "--color=never"], tool["env"], tool["cwd"])
    assert out.returncode == 0, out.stderr
    assert re.search(rf"^{DIST} v{re.escape(_version())}$", out.stdout, re.M), out.stdout
    assert re.search(rf"^-\s+{SCRIPT}$", out.stdout, re.M), out.stdout


def test_installed_binary_reports_the_real_version(tool):
    out = _run([str(tool["binary"]), "--version"], tool["env"], tool["cwd"])
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == f"{SCRIPT} {_version()}", out.stdout


def test_contract_dump_answers_from_another_cwd(tool):
    out = _run([str(tool["binary"]), "contract", "dump", "--json"], tool["env"], tool["cwd"])
    assert out.returncode == 0, out.stderr
    envelope = json.loads(out.stdout)  # stdout carries exactly one JSON document
    assert envelope["ok"] is True, envelope
    assert envelope["command"] == "contract.dump", envelope
    assert envelope["godsvoice_version"] == _version(), envelope
    assert envelope["data"]["leaf_commands"] > 0, envelope["data"]
    # `unregistered` must be *reported*, not silently empty: the CLI contract test owns the
    # "no command without a contract entry" rule, this one only checks the dump is usable.
    assert isinstance(envelope["data"]["unregistered"], list), envelope["data"]
    assert envelope["evidence"], "an envelope without evidence is not a success"


def test_selftest_answers_from_another_cwd(tool):
    out = _run([str(tool["binary"]), "selftest", "--json"], tool["env"], tool["cwd"])
    # exit 2 = at least one of the eight layers is honestly unreachable in this runtime.
    # Anything else (7 internal / 8 unknown) means the command itself broke.
    assert out.returncode in (0, 2), f"exit {out.returncode}\n{out.stdout}\n{out.stderr}"
    assert "Traceback" not in out.stderr, out.stderr
    envelope = json.loads(out.stdout)
    assert envelope["command"] == "selftest", envelope
    assert envelope["godsvoice_version"] == _version(), envelope
    layers = [entry["layer"] for entry in envelope["data"]["layers"]]
    assert set(layers) == LAYERS, layers
    for entry in envelope["data"]["layers"]:
        assert isinstance(entry["ok"], bool), entry
        if not entry["ok"]:
            assert entry.get("blocked_reason"), f"a failed layer must say why: {entry}"


def test_the_installed_package_does_not_need_the_checkout(tool):
    """The tool's own site-packages must be self-contained (data files, no repo paths)."""
    env = tool["env"]
    python = Path(env["UV_TOOL_DIR"]) / DIST / "bin" / "python"
    assert python.exists(), f"no interpreter in the tool env at {python}"
    out = _run(
        [
            str(python),
            "-c",
            "import skaldir, godsv, pathlib, sys\n"
            "assert godsv is skaldir, 'the deprecated name is the same package'\n"
            "root = pathlib.Path(skaldir.__file__).parent\n"
            "print('PACKAGE', root)\n"
            "print('ASSETS', sorted(p.name for p in (root / 'assets').iterdir()))\n"
            "print('PROMPTS', len(list((root / 'prompts').glob('*.md'))))\n"
            "print('SELF_CONTAINED', root.is_relative_to(sys.prefix))\n"
            "print('VERSION', skaldir.__version__)\n",
        ],
        env,
        tool["cwd"],
    )
    assert out.returncode == 0, out.stderr
    assert _field(out.stdout, "SELF_CONTAINED") == "True", out.stdout
    assert "logo.ansi" in _field(out.stdout, "ASSETS"), out.stdout
    assert "skaldir-logo.png" in _field(out.stdout, "ASSETS"), out.stdout
    assert int(_field(out.stdout, "PROMPTS")) >= 10, out.stdout
    assert _field(out.stdout, "VERSION") == _version(), out.stdout


def test_doctor_answers_from_another_cwd(tool):
    out = _run([str(tool["binary"]), "doctor", "--json"], tool["env"], tool["cwd"])
    assert out.returncode in (0, 2, 3), f"exit {out.returncode}\n{out.stdout}\n{out.stderr}"
    assert "Traceback" not in out.stderr, out.stderr
    envelope = json.loads(out.stdout)
    assert envelope["command"] == "doctor", envelope
    prompt_checks = [
        c for c in envelope["data"].get("checks", []) if "Prompts" in str(c.get("check", ""))
    ]
    assert prompt_checks and prompt_checks[0]["ok"] is True, envelope["data"]

