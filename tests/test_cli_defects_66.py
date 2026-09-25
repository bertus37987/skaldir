"""66 — the five CLI defects of audit 62 (`62-docker-toolbuilder-cli-research.md`),
each pinned by the command that exposed it, TDD-first:

* **#6 `tools health` on a kind=python tool** — was ``exit 5: no staged container
  adapter``; the verb now dispatches per kind and reports the capability-health
  ledger (scan/import/test/determinism) for a non-container tool.
* **#14 `tools call` on a kind=container tool** — was ``exit 3 not_exported``;
  container specs now resolve through their staged ``bin/`` wrapper, the exact
  call path ``tools register`` prints, after the same health gate.
* **#17 the exit-code contract for argparse misuse** — a refusal now carries the
  code the CONTRACT declares for the command being parsed: ``EXIT_USAGE`` (1)
  where it is declared (docs CLI-REFERENCE §2: *argparse usage errors exit 1,
  not 2*), ``EXIT_VERIFICATION_FAILED`` (2) where the contract declares that and
  no usage code (``tools.build`` etc. — their misuse used to exit an undeclared
  1), and the documented default 1 for a group (``verify``) or an unknown name.
* **#8 `docker ensure` reports ``podman_bin: ""``** while podman is installed —
  the winning docker probe no longer erases the resolved podman path.
* **#18 `python3 -m skaldir` on a bare interpreter** — was a raw
  ``ModuleNotFoundError: cryptography`` traceback; the entry point now degrades
  to the venv hint and exit 1.

The builder-level compose-before-create TDD (audit #7: "Adler-32 checksum" must
reach the draft/no-client path, a true duplicate still refuses) lives beside its
siblings in ``tests/test_toolbuilder.py::TestComposeBeforeCreateInBuilder``.

No real model endpoint is contacted anywhere in this file; every CLI process is
the product front door (``python -m skaldir``) in the project venv.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _product_cli(*argv: str, timeout: int = 180) -> tuple[int, dict, str]:
    """``(exit, envelope, stdout)`` of one real ``skaldir …`` subprocess."""
    proc = subprocess.run(  # nosec B603 — fixed argv, the product CLI itself
        [sys.executable, "-m", "skaldir", *argv],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(REPO),
    )
    out = proc.stdout.strip()
    envelope: dict = {}
    if out:
        try:
            envelope = json.loads(out.splitlines()[-1])
        except ValueError:
            envelope = {}
    return proc.returncode, envelope, proc.stdout


# --------------------------------------------------------------------------- #
# #6 — `tools health` serves kind=python from the capability-health ledger      #
# --------------------------------------------------------------------------- #


def test_tools_health_serves_a_python_tool_from_its_ledger(tmp_path, capsys):
    """A built python tool's health is its test status — not exit 5 (audit 62 #6)."""
    from skaldir import cli
    from skaldir.toolbuilder import ToolBuildRequest, ToolBuilder

    name = "gv_health_probe_66"
    result = ToolBuilder(tmp_path).build(
        ToolBuildRequest(
            capability="report the health ledger state of a built python tool",
            name=name,
            source_code="def main(x: int = 0) -> dict:\n    return {'x_seen': x}\n",
            module=name,
            entrypoint="main",
            sample_input={"x": 1},
        )
    )
    assert result.get("ok") is True, result

    code = cli.main(["tools", "health", name, "--runtime", str(tmp_path), "--json"])
    captured = capsys.readouterr()
    envelope = json.loads(captured.out.strip().splitlines()[-1])

    assert code == 0, envelope  # was 5, "no staged container adapter"
    data = envelope["data"]
    assert data["name"] == name
    assert data["kind"] == "python"
    assert data["health"]["state"], data  # the folded ledger state
    # the gates that produced it are reachable, verbatim (audit row 3's record)
    assert "gates:" in data["verification"]["detail"]
    assert data["verification"]["ok"] is True
    assert "python tool: health is its test status" in captured.err


def test_tools_health_still_refuses_a_name_it_has_never_seen(tmp_path, capsys):
    """The kind dispatch must not soften the honest not-found (exit 5)."""
    from skaldir import cli

    code = cli.main(["tools", "health", "gv_never_built_66", "--runtime", str(tmp_path), "--json"])
    captured = capsys.readouterr()
    envelope = json.loads(captured.out.strip().splitlines()[-1])
    assert code == 5, envelope
    assert envelope["error"]["code"] == "not_found"


# --------------------------------------------------------------------------- #
# #14 — `tools call` invokes kind=container through its staged bin wrapper      #
# --------------------------------------------------------------------------- #


def test_tools_call_invokes_a_container_tool_through_its_wrapper(tmp_path):
    """approved + health gate + staged wrapper = callable (was exit 3 not_exported)."""
    from skaldir import tools_call_cli as C
    from skaldir.tools import STATUS_APPROVED, ToolSpec, ToolStore

    name = "gv_cont_probe_66"
    wrapper = tmp_path / "generated_tools" / name / "bin" / name
    wrapper.parent.mkdir(parents=True)
    # the wrapper contract of `tools register`: argv = one JSON object,
    # stdout = one JSON document (here it echoes the arguments it received)
    wrapper.write_text(
        "#!/bin/sh\nprintf '%s\\n' '{\"sha256\":\"probe\",\"echo\":'$1'}'\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    store = ToolStore(str(tmp_path / "tools.db"))
    try:
        store.upsert(
            ToolSpec(
                name=name,
                title="sha-256 of a string (container)",
                kind="container",
                status=STATUS_APPROVED,
            )
        )
    finally:
        store.close()

    out = StringIO()
    with redirect_stdout(out):
        code = C.main([name, "--json-args", '{"text":"halloween"}', "--runtime", str(tmp_path)])
    payload = json.loads(out.getvalue())

    assert code == 0, payload  # was 3, "not on the executable surface: kind='container'"
    assert payload["ok"] is True
    assert payload["kind"] == "container"
    assert payload["result"]["echo"] == {"text": "halloween"}
    # the evidence names the wrapper that really ran, not the python seam
    assert any(name in str(item.get("ref", "")) for item in payload["evidence"])


def test_a_container_tool_without_a_staged_wrapper_refuses_honestly(tmp_path):
    """No certified call path ⇒ a refusal that says exactly that (exit 3)."""
    from skaldir import tools_call_cli as C
    from skaldir.tools import STATUS_APPROVED, ToolSpec, ToolStore

    name = "gv_cont_unstaged_66"
    store = ToolStore(str(tmp_path / "tools.db"))
    try:
        store.upsert(
            ToolSpec(name=name, title="unstaged container", kind="container",
                     status=STATUS_APPROVED)
        )
    finally:
        store.close()

    out = StringIO()
    with redirect_stdout(out):
        code = C.main([name, "--json-args", "{}", "--runtime", str(tmp_path)])
    payload = json.loads(out.getvalue())

    assert code == 3, payload
    assert payload["failure_class"] == "not_exported"
    assert "staged wrapper is missing" in payload["refusal"]


# --------------------------------------------------------------------------- #
# #17 — the exit-code contract for argparse misuse                              #
# --------------------------------------------------------------------------- #


def test_prompt_build_misuse_exits_the_declared_usage_code():
    """`prompt build --text hi` (--text is a FLAG): exit 1, declared by prompt.build."""
    from skaldir import cli_contract

    spec = cli_contract.COMMANDS["prompt.build"]
    assert cli_contract.EXIT_USAGE in spec.exit_codes  # the documented contract

    code, _envelope, _out = _product_cli("prompt", "build", "--text", "hi")
    assert code == cli_contract.EXIT_USAGE == 1
    assert code in spec.exit_codes


def test_verify_missing_verb_exits_one_documented_usage_envelope():
    """`verify --json` (no verb): one JSON envelope, exit 1 — docs CLI-REFERENCE §2:
    *argparse usage errors exit 1 (ContractArgumentParser.error), not 2*; the
    `verify` GROUP has no leaf entry, so the documented default applies."""
    from skaldir import cli_contract

    code, envelope, _out = _product_cli("verify", "--json")
    assert code == cli_contract.EXIT_USAGE == 1
    assert envelope["exit_code"] == 1
    assert envelope["error"]["code"] == "usage"


def test_misuse_of_a_command_that_declares_no_usage_code_exits_its_declared_2():
    """`tools build` declares (0,2,3,4,6,7) — no 1. A code the command does not
    list is not part of its contract (docs §2), so its argparse misuse answers
    with the contract's own EXIT_VERIFICATION_FAILED (2), not an undeclared 1."""
    from skaldir import cli_contract

    spec = cli_contract.COMMANDS["tools.build"]
    assert cli_contract.EXIT_USAGE not in spec.exit_codes
    assert cli_contract.EXIT_VERIFICATION_FAILED in spec.exit_codes

    code, envelope, _out = _product_cli(
        "tools", "build", "--capability", "x", "--not-a-real-flag", "--json"
    )
    assert code == cli_contract.EXIT_VERIFICATION_FAILED == 2
    assert envelope["exit_code"] == 2
    assert envelope["error"]["code"] == "verification_failed"


def test_usage_exit_code_maps_longest_prefix_and_defaults_to_usage():
    from skaldir import cli_contract as cc

    # a stray flag VALUE never masks the command being parsed
    assert cc.usage_exit_code(["prompt", "build", "--text", "hi"]) == cc.EXIT_USAGE
    # declared verification_failed and no usage code → the contract's 2
    assert cc.usage_exit_code(["tools", "build", "--oops"]) == cc.EXIT_VERIFICATION_FAILED
    # a group, an unknown name or nothing at all → the documented default
    assert cc.usage_exit_code(["verify", "--json"]) == cc.EXIT_USAGE
    assert cc.usage_exit_code(["nope"]) == cc.EXIT_USAGE
    assert cc.usage_exit_code([]) == cc.EXIT_USAGE


# --------------------------------------------------------------------------- #
# #8 — `docker ensure` reports the installed podman even when docker wins       #
# --------------------------------------------------------------------------- #


def test_a_winning_docker_probe_still_reports_the_installed_podman(monkeypatch):
    from skaldir import docker_ensure as DE

    monkeypatch.delenv("SKALDIR_PODMAN_BIN", raising=False)
    monkeypatch.setattr(
        DE.shutil,
        "which",
        lambda cmd, *a, **k: "/usr/bin/podman" if cmd == "podman" else None,
    )
    docker_up = DE.RuntimeProbe(
        provider=DE.PROVIDER_DOCKER,
        reachable=True,
        detail="docker 29.6.1 answered (storage driver overlayfs)",
        binary="/usr/bin/docker",
        server_version="29.6.1",
        storage_driver="overlayfs",
        rootless=None,
        commands=("/usr/bin/docker info --format '{json}'",),
    )
    report = DE.ensure(prober=lambda **_kw: docker_up, paths=())
    assert report.provider == DE.PROVIDER_DOCKER
    assert report.exit_code == 0
    # was "" — the docker short-circuit erased the installed podman (audit 62 #8)
    assert report.podman_bin == "/usr/bin/podman"
    assert report.to_dict()["podman_bin"] == "/usr/bin/podman"

    # honest on a host without podman: a resolved fact, never a bare guess
    monkeypatch.setattr(DE.shutil, "which", lambda cmd, *a, **k: None)
    report2 = DE.ensure(prober=lambda **_kw: docker_up, paths=())
    assert report2.podman_bin == ""


# --------------------------------------------------------------------------- #
# #18 — bare `python3 -m skaldir` degrades to the venv hint, not a traceback    #
# --------------------------------------------------------------------------- #

VENV_HINT = (
    "this build needs the project venv: source .venv/bin/activate "
    "(or use the skaldir launcher)"
)


def test_bare_python_degrades_with_the_venv_hint_and_exit_1(tmp_path):
    probe = subprocess.run(  # nosec B603 — fixed argv, an import probe
        ["/usr/bin/python3", "-c", "import cryptography"], capture_output=True
    )
    if probe.returncode == 0:
        pytest.skip(
            "/usr/bin/python3 has cryptography — the bare-interpreter defect cannot reproduce here"
        )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO / "src")
    env["PYTHONNOUSERSITE"] = "1"
    proc = subprocess.run(  # nosec B603 — the entry point under the system interpreter
        ["/usr/bin/python3", "-m", "skaldir", "--version"],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(tmp_path),
        timeout=180,
    )
    # exit 1 was already true by accident (a traceback also ends at 1); the
    # defect was HOW: a raw ModuleNotFoundError. Both are asserted now.
    assert proc.returncode == 1
    assert VENV_HINT in proc.stderr
    assert "Traceback" not in proc.stderr
    assert "ModuleNotFoundError" not in proc.stderr
