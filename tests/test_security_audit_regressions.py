"""Regression tests for the two defects the audit found, each with its before/after.

Both defects are *code-generation / text-splicing* defects, so every test here does
two things: it proves the pre-fix expression really carried the defect, and it
**executes** what the fix produces (with stubs on ``PATH``) to prove the injected
value is now data that nothing runs.

* the MCP server built a tool callable by compiling a generated ``def`` expression
  and ``exec``-ing it; the fix builds the same typed signature as an
  :class:`inspect.Signature` (plus the annotations pydantic reads), so there is no
  generated source anywhere — and the callable refuses the arguments a real
  signature would refuse;
* the container capture commands spliced the display / window id / tool names into
  the text after ``sh -c`` (the shell re-parses it); the fix keeps the script text
  literal and passes every value as a positional argument.

Reverting either fix makes the corresponding test here fail.
"""

from __future__ import annotations

import ast
import base64
import inspect
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from skaldir import security_audit as A

REPO = Path(A.__file__).resolve().parents[2]
SH = shutil.which("sh") or "/bin/sh"


def _shim_dir(tmp_path: Path, *tools: str) -> Path:
    """A ``PATH`` entry holding stub binaries, so a generated argv can really run."""
    binary = tmp_path / "shim"
    binary.mkdir(exist_ok=True)
    for tool in tools:
        stub = binary / tool
        stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        stub.chmod(0o755)
    return binary


def _run_argv(
    argv: list[str], *, path: str | None = None, timeout: float = 30
) -> subprocess.CompletedProcess:
    """Run one argv with ``sh`` resolved absolutely (the child's ``PATH`` is the test's)."""
    env = dict(os.environ)
    if path is not None:
        env["PATH"] = path
    return subprocess.run(
        [SH, *argv[1:]], capture_output=True, text=True, timeout=timeout, env=env
    )


# --------------------------------------------------------------------------- #
# 1. the MCP server: no generated source, and a signature that refuses junk     #
# --------------------------------------------------------------------------- #


def test_the_mcp_module_never_compiles_or_executes_generated_source() -> None:
    """The pre-fix defect was ``exec(compile(source, …))``; the fix builds a signature."""
    tree = ast.parse((REPO / "src" / "skaldir" / "mcpserver.py").read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and ast.unparse(node.func).split(".")[-1] in (
            "eval",
            "exec",
            "compile",
        )
    ]
    assert calls == [], [ast.unparse(node) for node in calls]


def test_the_typed_mcp_callable_refuses_malformed_arguments(monkeypatch) -> None:
    """A declared tool is a function of its declared parameters — and of nothing else."""
    from skaldir import mcpserver as ms
    from skaldir.tools import ToolSpec

    declared = {
        "input": {
            "type": "object",
            "properties": {
                "alpha": {"type": "string"},
                "beta": {"type": "integer"},
            },
            "required": ["beta"],
            "additionalProperties": False,
        },
        "output": {},
        "source": "test",
        "descriptor": "",
    }
    monkeypatch.setattr(ms, "declared_schemas", lambda spec, base_dir=None: dict(declared))
    surface = ms.tool_surface(ToolSpec(name="fixture", title="F", module="m", entrypoint="f"))
    function = surface["function"]

    parameters = inspect.signature(function).parameters
    assert list(parameters) == ["beta", "alpha"]  # required first
    assert parameters["beta"].annotation is int
    assert parameters["alpha"].annotation == (str | None)
    assert parameters["beta"].default is inspect.Parameter.empty
    assert parameters["alpha"].default is None
    # the annotations pydantic reads are the same real objects, not strings
    assert function.__annotations__ == {"beta": int, "alpha": str | None}

    with pytest.raises(TypeError):
        function(alpha="only-alpha")  # beta is required
    with pytest.raises(TypeError):
        function(beta=1, gamma="never declared")  # a tool has no such parameter
    with pytest.raises(TypeError):
        function(1, "two", "three")  # more positionals than declared parameters
    with pytest.raises(TypeError, match="multiple values"):
        function(1, beta=2)  # the same declared argument cannot be supplied twice


# --------------------------------------------------------------------------- #
# 2. the capture commands: the values are arguments, not shell text             #
# --------------------------------------------------------------------------- #


def test_the_old_capture_expression_really_executed_the_injected_display(tmp_path: Path) -> None:
    """The finding is not theoretical: the pre-fix string runs the injected command."""
    marker = tmp_path / "pwned"
    hostile = f":90; touch {marker}; echo"
    old_script = f"xwd -root -display {hostile} -silent | base64 -w0"
    _run_argv(["sh", "-c", old_script], path=os.environ.get("PATH", ""))
    assert marker.exists(), (
        "the pre-fix expression did not execute the payload — re-derive the finding"
    )


def test_the_container_capture_argv_passes_a_hostile_display_as_an_argument(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from skaldir import computer_use as cu

    captured: list[list[str]] = []

    def fake_run_bytes(cmd, *, timeout):
        captured.append(list(cmd))
        return 1, b"", "stub"

    monkeypatch.setattr(cu, "_run_cmd_bytes", fake_run_bytes)
    monkeypatch.setattr(cu.ContainerRunner, "start", lambda self: (True, "stub"))

    marker = tmp_path / "pwned"
    hostile = f":90; touch {marker}"
    runner = cu.ContainerRunner(name="cu-capture-fixture", display=hostile)
    capture, why = runner.screenshot(tmp_path / "shot.png", window_id="12; touch ignored")
    assert capture is None and why

    cmd = captured[0]
    script = cmd[cmd.index("-c") + 1]
    assert hostile not in script and "-display" not in script
    assert '"$@"' in script  # the selector and the display arrive as arguments
    tail = cmd[cmd.index(runner.name) + 1 :]
    assert tail[-1] == hostile and hostile not in tail[3]

    # the argv really is the pipeline: a stub xwd's bytes come back base64-encoded,
    # and the hostile display is one of its arguments (nothing runs)
    output = _run_argv(tail, path=f"{_shim_dir(tmp_path, 'xwd')}:{os.environ['PATH']}")
    assert not marker.exists(), f"command injection is back: {tail!r}"
    assert output.returncode == 0, output.stderr


def test_the_capture_probe_keeps_the_tool_names_out_of_the_script_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The probe is a literal loop over ``"$@"`` — and it still reports what is missing."""
    from skaldir import computer_use as cu

    captured: list[list[str]] = []

    def fake_run(cmd, *, timeout):
        captured.append(list(cmd))
        return 0, "CAPTURE-TOOLS-OK"

    monkeypatch.setattr(cu, "_run_cmd", fake_run)
    monkeypatch.setattr(cu.ContainerRunner, "start", lambda self: (True, "stub"))
    runner = cu.ContainerRunner(name="cu-probe-fixture")
    ok, why = runner.screenshot_available()
    assert ok and "xwd" in why

    cmd = captured[0]
    script = cmd[cmd.index("-c") + 1]
    assert "xwd" not in script and "base64" not in script  # names travel as arguments
    tail = cmd[cmd.index(runner.name) + 1 :]
    assert list(tail[4:]) == ["xwd", "base64"] and tail[3] == "sh"

    # run the probe for real against an empty image: the missing tool is named
    def real_run(cmd, *, timeout):
        empty = tmp_path / "empty"
        empty.mkdir(exist_ok=True)
        proc = _run_argv(cmd[cmd.index(runner.name) + 1 :], path=str(empty))
        return proc.returncode, proc.stdout + proc.stderr

    monkeypatch.setattr(cu, "_run_cmd", real_run)
    ok, why = runner._probe_capture_tools()
    assert ok is False and "missing xwd" in why


def test_the_xvfb_prologue_passes_the_display_as_an_argument(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from skaldir import computer_use as cu

    calls: list[list[str]] = []

    def fake_run(cmd, *, timeout):
        calls.append(list(cmd))
        return (0, "XVFB_READY") if list(cmd[:2]) == ["podman", "exec"] else (0, "")

    monkeypatch.setattr(cu, "_run_cmd", fake_run)
    monkeypatch.setattr(cu.ContainerRunner, "available", lambda self: (True, "stub"))

    marker = tmp_path / "pwned"
    hostile = f":90; touch {marker}"
    runner = cu.ContainerRunner(name="cu-xvfb-fixture", display=hostile)
    ok, detail = runner.start()
    assert ok, detail

    xvfb = next(cmd for cmd in calls if list(cmd[3:5]) == ["sh", "-c"])
    script = xvfb[5]
    assert hostile not in script and "Xvfb " in script and '"$1"' in script
    tail = xvfb[xvfb.index(runner.name) + 1 :]
    assert tail[-1] == hostile

    shim = _shim_dir(tmp_path, "Xvfb", "sleep")
    _run_argv(tail, path=f"{shim}:{os.environ['PATH']}")
    assert not marker.exists(), f"command injection is back: {tail!r}"


def test_the_desktop_fallback_argv_passes_a_hostile_display_as_an_argument(
    tmp_path: Path,
) -> None:
    """The verify-flows capture fallback builds the same literal pipeline."""
    from skaldir import verify_flows as V

    class _Runner:
        isolation = "container"

        def __init__(self, display: str) -> None:
            self.display = display
            self.argv: list[str] = []

        def screenshot(self, path, *, label=""):
            return None, "stub: no capture method"

        def act(self, action):
            self.argv = list(action.argv)
            return SimpleNamespace(ok=False, detail="stub")

    marker = tmp_path / "pwned"
    hostile = f":90; touch {marker}"
    runner = _Runner(hostile)
    capture, why = V._capture_desktop(
        runner, "step", 1, tmp_path / "shots", "flow-1", label="opened"
    )
    assert capture is None and why

    argv = runner.argv
    script = argv[argv.index("-c") + 1]
    assert hostile not in script and '"$1"' in script
    assert argv[-1] == hostile

    shim = _shim_dir(tmp_path, "xwd")
    _run_argv(argv, path=f"{shim}:{os.environ['PATH']}")
    assert not marker.exists(), f"command injection is back: {argv!r}"


# --------------------------------------------------------------------------- #
# 3. the toolbuilder scratch prologue: literal script, refused scratch sets     #
# --------------------------------------------------------------------------- #


def test_the_scratch_runner_refuses_a_scratch_set_it_has_no_literal_for() -> None:
    from skaldir import toolbuilder as tb

    with pytest.raises(tb.AdapterRefusal):
        tb._scratch_runner(
            {"_in": "/tmp/evil.png", "_out": "/dev/shm/godsv_out"}, [], "image"
        )
    with pytest.raises(tb.AdapterRefusal):
        tb._scratch_runner(dict(tb._MAGICK_SCRATCH), ["export TMPDIR=/dev/shm"], "image")


def test_the_scratch_prologue_is_literal_and_still_runs_the_mapped_command(
    tmp_path: Path,
) -> None:
    """Real execution: ``$0`` is decoded into the scratch file, ``$@`` is the command."""
    from skaldir import toolbuilder as tb

    argv = tb._scratch_runner(
        dict(tb._MAGICK_SCRATCH),
        ["export TMPDIR=/dev/shm MAGICK_TEMPORARY_PATH=/dev/shm"],
        "image",
    )
    assert argv[0:2] == ["sh", "-c"]
    script = argv[2]
    assert "/dev/shm/godsv_in.png" in script and "/dev/shm/godsv_out" in script
    assert "$@" in script and 'printf %s "$0"' in script and "{" not in script

    # Remap only the two fixed paths into this test's private directory. Production
    # runs this prologue in a container; executing its /dev/shm paths on the host
    # would overwrite (and then delete) files the test does not own.
    scratch = tmp_path / "input.png"
    output = tmp_path / "output"
    script = script.replace("/dev/shm/godsv_in.png", str(scratch))
    script = script.replace("/dev/shm/godsv_out", str(output))
    payload = base64.b64encode(b"source-bytes").decode("ascii")
    command = [SH, "-c", 'cat "$1"; printf out > "$2"', "sh", str(scratch), str(output)]
    proc = _run_argv([*argv[:2], script, payload, *command])
    assert proc.returncode == 0, proc.stderr
    expected = base64.b64encode(b"out").decode("ascii")
    assert f"image@b64={expected}" in proc.stdout
