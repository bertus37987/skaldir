"""Invariant tests for the four defects this block fixed — each with its before/after.

Every test here is written so that **reverting the fix makes it fail**:

* the injection test *executes* the argv the ACT layer produces (with a stub
  ``xdotool`` on ``PATH``) and fails if the injected command runs — and it first
  proves the old expression *did* run it, so the finding is not theoretical;
* the timeout test points the CDP probe at a socket that accepts and never answers,
  and fails if the call does not return;
* the vault test observes the temporary file's mode at the moment of the rename,
  and fails if the mode is not ``0600`` already;
* the traversal test creates the manifest the old path would have reached, and fails
  if ``build_hub_manifest`` (or ``skaldir tools hub``) reads it instead of refusing.

The last test is the cross-file guard: it runs the shipped audit over this tree and
fails if any of the four defects reappears — so a sibling block that rewrites one of
these files without the fix cannot merge it silently.
"""

from __future__ import annotations

import json
import os
import socket
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from skaldir import security_audit as A
from skaldir.act_service import _xdotool_window_argv
from skaldir.toolhub import build_hub_manifest, tool_dir
from skaldir.treasury import Vault

REPO = Path(A.__file__).resolve().parents[2]


# --------------------------------------------------------------------------- #
# 1. command injection — the window title spliced into `sh -c`                  #
# --------------------------------------------------------------------------- #


def _shim_dir(tmp_path: Path) -> Path:
    """A ``PATH`` with a stub ``xdotool``, so the generated argv can really run."""
    binary = tmp_path / "bin"
    binary.mkdir(exist_ok=True)
    stub = binary / "xdotool"
    stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    stub.chmod(0o755)
    return binary


def _run(argv: list[str], shim: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PATH"] = f"{shim}:{env.get('PATH', '')}"
    return subprocess.run(argv, capture_output=True, text=True, timeout=30, env=env)


def test_the_old_expression_really_executed_the_injected_command(tmp_path: Path) -> None:
    """The finding is not theoretical: the pre-fix string runs the injected command."""
    marker = tmp_path / "pwned"
    hostile = f"x'; touch {marker}; echo '"
    old_script = (
        f"wid=$(xdotool search --name '{hostile}' | head -1); "
        'test -n "$wid" && xdotool windowfocus --sync $wid && echo FOCUSED'
    )
    _run(["sh", "-c", old_script], _shim_dir(tmp_path))
    assert marker.exists(), (
        "the pre-fix expression did not execute the payload — re-derive the finding"
    )


def test_the_window_focus_argv_no_longer_executes_a_hostile_title(tmp_path: Path) -> None:
    marker = tmp_path / "pwned"
    hostile = f"x'; touch {marker}; echo '"
    argv = _xdotool_window_argv({"op": "focus", "title": hostile})
    assert argv[0] == "sh" and argv[1] == "-c"
    result = _run(argv, _shim_dir(tmp_path))
    assert not marker.exists(), f"command injection is back: {argv[2]!r}"
    assert "FOCUSED" not in result.stdout  # nothing to focus, but nothing ran either


def test_the_window_focus_argv_still_quotes_the_ordinary_case() -> None:
    """The fix must not change the working call: a plain title stays a plain pattern."""
    argv = _xdotool_window_argv({"op": "focus", "title": "Browser"})
    assert "--name Browser" in argv[2]
    assert _xdotool_window_argv({"op": "list", "title": "Browser"})[3] == "Browser"


def test_the_audit_reports_no_shell_interpolation_anywhere_in_the_tree() -> None:
    report = A.audit([REPO / "src"], base=REPO, live=False)
    hits = [item for item in report["findings"] if item["rule"] == "SHELL-C-INTERPOLATION"]
    assert hits == [], hits


# --------------------------------------------------------------------------- #
# 2. resource limit — a declared timeout that was never passed                  #
# --------------------------------------------------------------------------- #


def _silent_server() -> tuple[socket.socket, int]:
    """A listening socket that accepts a connection and then says nothing."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(4)
    return server, int(server.getsockname()[1])


def test_cdp_targets_honours_the_timeout_it_declares() -> None:
    from skaldir.workflows import cdp_targets

    server, port = _silent_server()
    try:
        started = time.monotonic()
        targets = cdp_targets(port, timeout=0.5)
        elapsed = time.monotonic() - started
    finally:
        server.close()
    assert targets == []
    assert elapsed < 5.0, f"the probe blocked for {elapsed:.1f}s despite timeout=0.5"


def test_the_pre_fix_call_would_have_blocked_forever() -> None:
    """Control: ``urlopen(url)`` with no timeout never returns from a silent peer."""
    import urllib.request

    server, port = _silent_server()
    done = threading.Event()

    def probe() -> None:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/json").read()
        except Exception:  # noqa: BLE001 — the point is that it never gets here in time
            pass
        finally:
            done.set()

    thread = threading.Thread(target=probe, daemon=True)
    thread.start()
    blocked = not done.wait(2.0)
    server.close()
    assert blocked, "the un-timeouted call returned — the control no longer holds"


def test_the_audit_reports_no_unused_declared_timeout_in_workflows() -> None:
    report = A.audit([REPO / "src"], base=REPO, live=False)
    hits = [
        item for item in report["findings"]
        if item["rule"] == "DECLARED-TIMEOUT-UNUSED" and item["path"].endswith("workflows.py")
    ]
    assert hits == [], hits


# --------------------------------------------------------------------------- #
# 3. permissions — the vault's write-then-chmod window                          #
# --------------------------------------------------------------------------- #


def test_the_old_two_step_write_left_the_vault_world_readable(tmp_path: Path) -> None:
    """Measured, with the umask a normal session has: 0644 between write and chmod."""
    tmp = tmp_path / "vault.tmp"
    previous = os.umask(0o022)
    try:
        tmp.write_text('{"version": 1, "entries": {}}', encoding="utf-8")
        mode_between = stat.S_IMODE(tmp.stat().st_mode)
        os.chmod(tmp, 0o600)
    finally:
        os.umask(previous)
    assert mode_between == 0o644, "the pre-fix sequence no longer shows the window"
    assert stat.S_IMODE(tmp.stat().st_mode) == 0o600


def test_the_vault_is_private_from_the_moment_it_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed: dict[str, int] = {}
    real_replace = os.replace

    def spy(src, dst, *args, **kwargs):  # type: ignore[no-untyped-def]
        observed["mode"] = stat.S_IMODE(Path(src).stat().st_mode)
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "replace", spy)
    path = tmp_path / "godsv" / "vault.json"
    Vault(path, "test-master-key")
    assert observed["mode"] == 0o600, "the temporary file was readable before the rename"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_the_hardened_vault_still_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "godsv" / "vault.json"
    vault = Vault(path, "test-master-key")
    handle = vault.add(service="demo", name="token", secret="s3cr3t-value", scopes=["use"])
    assert handle == "secret://demo/token"
    info = vault.describe(handle)
    assert info is not None and info.service == "demo"
    assert "s3cr3t-value" not in path.read_text(encoding="utf-8")  # ciphertext at rest
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 1
    reopened = Vault(path, "test-master-key")
    assert reopened._decrypt(handle)["secret"] == "s3cr3t-value"


def test_the_audit_reports_no_write_then_chmod_in_treasury() -> None:
    report = A.audit([REPO / "src"], base=REPO, live=False)
    hits = [
        item for item in report["findings"]
        if item["rule"] == "WRITE-THEN-CHMOD" and item["path"].endswith("treasury.py")
    ]
    assert hits == [], hits


# --------------------------------------------------------------------------- #
# 4. path traversal — `tools hub --manifest ../..`                              #
# --------------------------------------------------------------------------- #


def _tool_tree(tmp_path: Path) -> tuple[Path, Path]:
    """A runtime whose neighbour directory holds a 'ready' manifest."""
    runtime = tmp_path / "godsv"
    (runtime / "generated_tools" / "good_tool").mkdir(parents=True)
    (runtime / "generated_tools" / "good_tool" / "manifest.yaml").write_text(
        "name: good_tool\nversion: 1.0.0\ntype: cli\npurpose: fine\nstatus: ready\n",
        encoding="utf-8",
    )
    outside = runtime / "evil"
    outside.mkdir()
    (outside / "manifest.yaml").write_text(
        "name: evil\nversion: 9.9.9\ntype: cli\npurpose: read me\nstatus: ready\n",
        encoding="utf-8",
    )
    return runtime, outside / "manifest.yaml"


def test_the_old_join_reached_a_manifest_outside_the_tool_tree(tmp_path: Path) -> None:
    """The pre-fix expression resolves to a real file outside ``generated_tools``."""
    runtime, outside_manifest = _tool_tree(tmp_path)
    prefix = Path("generated_tools")
    legacy_path = runtime / prefix / "../evil" / "manifest.yaml"  # exactly what the audit flagged
    assert legacy_path.is_file(), "the old path no longer reaches outside — re-derive the finding"
    assert legacy_path.resolve() == outside_manifest.resolve()
    assert prefix.name not in legacy_path.resolve().parts  # it left the tool tree


def test_build_hub_manifest_refuses_to_leave_the_tool_tree(tmp_path: Path) -> None:
    runtime, _outside = _tool_tree(tmp_path)
    for hostile in ("../evil", "../../etc", "..", "evil/../../evil", "/etc", "a/b", ""):
        with pytest.raises(ValueError):
            build_hub_manifest(runtime, hostile)
    assert build_hub_manifest(runtime, "good_tool")["name"] == "good_tool"


def test_a_symlinked_tool_entry_is_refused(tmp_path: Path) -> None:
    runtime, outside_manifest = _tool_tree(tmp_path)
    link = runtime / "generated_tools" / "linked"
    link.symlink_to(outside_manifest.parent)
    with pytest.raises(ValueError):
        tool_dir(runtime, "linked")


def test_the_cli_verb_refuses_a_hostile_manifest_name(tmp_path: Path, capsys) -> None:
    """``skaldir tools hub --manifest ../evil`` exits 1 with a refusal, not a leak."""
    import argparse

    from skaldir.cli import _cmd_tools_hub

    runtime, _outside = _tool_tree(tmp_path)
    args = argparse.Namespace(runtime=str(runtime), manifest="../evil")
    code = _cmd_tools_hub(args)
    assert code == 1
    assert "refused" in capsys.readouterr().out


def test_the_audit_reports_no_untracked_name_join_in_toolhub() -> None:
    report = A.audit([REPO / "src"], base=REPO, live=False)
    hits = [
        item for item in report["findings"]
        if item["rule"] == "PATH-JOIN-UNTRUSTED" and item["path"].endswith("toolhub.py")
    ]
    assert hits == [], hits


# --------------------------------------------------------------------------- #
# the cross-file guard: the audit itself is the regression test                 #
# --------------------------------------------------------------------------- #


def test_the_shipped_audit_finds_none_of_the_four_fixed_defects() -> None:
    """Run the audit the way the CLI runs it; a reverted fix fails this test."""
    result = subprocess.run(
        [sys.executable, "-m", "godsv.security_audit_cli", "audit", "--json", "--path", "src"],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=300,
        env={**os.environ, "PYTHONPATH": "", "GODSV_HOME": "", "HERMES_HOME": ""},
    )
    assert result.stdout.strip(), result.stderr[-2000:]
    envelope = json.loads(result.stdout)
    findings = envelope["data"]["findings"]
    fixed = {
        ("SHELL-C-INTERPOLATION", "act_service.py"),
        ("WRITE-THEN-CHMOD", "treasury.py"),
        ("DECLARED-TIMEOUT-UNUSED", "workflows.py"),
        ("PATH-JOIN-UNTRUSTED", "toolhub.py"),
    }
    for rule, filename in sorted(fixed):
        assert not [
            item for item in findings
            if item["rule"] == rule and item["path"].endswith(filename)
        ], f"{rule} is back in {filename}"
    assert envelope["exit_code"] == 0, envelope.get("error")
