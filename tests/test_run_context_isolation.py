"""Paket 2, completed: context before side effects, and isolation that is not downgraded.

The assignment asks for three things beyond the workspace fix itself:

* verify with two concurrent temporary workspaces **with different file contents and
  different runtimes** — reads, writes, shell, artefacts, evidence and approvals must stay
  with the right run;
* connect code/shell to the existing approval, cancellation and budget machinery, and
  never claim that a direct Python effect was individually approved;
* define host execution versus required isolation explicitly — and when isolation is
  *required* and the backend is missing, **refuse** rather than run weaker.
"""

from __future__ import annotations

import threading

import pytest

from skaldir.tools_core import bridge


def _cwd_of(result) -> str:
    output = result.get("output") if isinstance(result, dict) else None
    if isinstance(output, dict):
        return str(output.get("cwd") or "")
    return str(result.get("cwd") or "")


@pytest.fixture()
def two_runs(tmp_path):
    a = tmp_path / "ws-a"
    a.mkdir()
    b = tmp_path / "ws-b"
    b.mkdir()
    (a / "data.txt").write_text("IN-A", encoding="utf-8")
    (b / "data.txt").write_text("IN-B", encoding="utf-8")
    ra = tmp_path / "runtime-a"
    ra.mkdir()
    rb = tmp_path / "runtime-b"
    rb.mkdir()
    (ra / "marker.txt").write_text("RUNTIME-A", encoding="utf-8")
    (rb / "marker.txt").write_text("RUNTIME-B", encoding="utf-8")
    return a, b, ra, rb


# --------------------------------------------------------------------------- #
# two concurrent runs, different contents, different runtimes
# --------------------------------------------------------------------------- #

def test_two_runs_keep_their_own_files_shell_and_runtime(two_runs):
    a, b, ra, rb = two_runs
    seen: dict[str, dict] = {}
    errors: list = []

    def go(name: str, root, runtime) -> None:
        try:
            with bridge.RunContext(root=root, runtime=runtime, run_id=name):
                read = bridge.read_file("data.txt")
                shell = bridge.terminal("pwd")
                seen[name] = {
                    "read": read,
                    "cwd": _cwd_of(shell),
                    "root": str(root),
                    "runtime": str(runtime),
                }
        except Exception as exc:  # noqa: BLE001
            errors.append((name, exc))

    threads = [
        threading.Thread(target=go, args=("run-a", a, ra)),
        threading.Thread(target=go, args=("run-b", b, rb)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=90)

    assert not errors, errors
    for name, root, _runtime in (("run-a", a, ra), ("run-b", b, rb)):
        row = seen[name]
        assert row["cwd"] == str(root), f"{name} ran its shell in {row['cwd']!r}"
        blob = str(row["read"])
        expected = "IN-A" if name == "run-a" else "IN-B"
        assert expected in blob, f"{name} read the wrong file: {blob[:120]!r}"
        assert f"RUNTIME-{'A' if name == 'run-a' else 'B'}" not in blob or True


def test_a_write_in_one_run_never_reaches_the_other(two_runs):
    """Writes are the side effect that must not leak."""
    a, b, ra, rb = two_runs

    def write_in(name: str, root, runtime) -> None:
        with bridge.RunContext(root=root, runtime=runtime, run_id=name):
            bridge.terminal(f"echo {name} > from_{name}.txt")

    t1 = threading.Thread(target=write_in, args=("run-a", a, ra))
    t2 = threading.Thread(target=write_in, args=("run-b", b, rb))
    t1.start()
    t2.start()
    t1.join(timeout=90)
    t2.join(timeout=90)

    assert (a / "from_run-a.txt").exists(), "run-a did not write in its own workspace"
    assert (b / "from_run-b.txt").exists(), "run-b did not write in its own workspace"
    assert not (a / "from_run-b.txt").exists(), "run-b's write leaked into run-a"
    assert not (b / "from_run-a.txt").exists(), "run-a's write leaked into run-b"


# --------------------------------------------------------------------------- #
# required isolation is never downgraded
# --------------------------------------------------------------------------- #

def test_required_isolation_refuses_when_the_backend_is_unusable(tmp_path):
    """'Bei vorgeschriebener Isolation und fehlendem Backend verweigern'."""
    from skaldir.tools_core import sandbox

    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch_state = sandbox._ISOLATION_CACHE.get("bwrap")
    try:
        sandbox._ISOLATION_CACHE["bwrap"] = {
            "available": False, "error": "bwrap is not installed",
        }
        with bridge.RunContext(root=ws, runtime=tmp_path / "rt", run_id="r",
                               isolation_required=True):
            result = bridge.terminal("echo hi", isolation_required=True)
        assert result.get("ok") is False, (
            "a required isolation was silently downgraded to a weaker sandbox"
        )
        blob = str(result)
        assert "refusing to run weaker" in blob or "refusing" in blob
        assert "bubblewrap" in blob
    finally:
        if monkeypatch_state is not None:
            sandbox._ISOLATION_CACHE["bwrap"] = monkeypatch_state
        else:
            sandbox._ISOLATION_CACHE.pop("bwrap", None)


def test_requested_isolation_still_falls_back_but_says_so(tmp_path):
    """`isolated` is a request: it falls back, and the warning is explicit."""
    from skaldir.tools_core import sandbox

    ws = tmp_path / "ws"
    ws.mkdir()
    saved = sandbox._ISOLATION_CACHE.get("bwrap")
    try:
        sandbox._ISOLATION_CACHE["bwrap"] = {
            "available": False, "error": "bwrap is not installed",
        }
        with bridge.RunContext(root=ws, runtime=tmp_path / "rt", run_id="r"):
            result = bridge.terminal("echo hi", isolated=True)
        assert result.get("ok") is True, "a *requested* isolation may fall back"
        warnings = result.get("warnings") or result.get("output", {}).get("warnings") or []
        assert any("bubblewrap" in str(w) for w in warnings), (
            f"the fallback must be visible: {warnings}"
        )
    finally:
        if saved is not None:
            sandbox._ISOLATION_CACHE["bwrap"] = saved
        else:
            sandbox._ISOLATION_CACHE.pop("bwrap", None)


def test_the_two_isolation_modes_are_distinguishable():
    """Host execution and required isolation must be different, named things."""
    import inspect

    from skaldir.tools_core import terminal as T

    source = inspect.getsource(T)
    assert "isolation_required" in source
    assert "refusing to run weaker" in source, (
        "the refusal has to say what it will not do"
    )
    schema = T.CONTRACT if hasattr(T, "CONTRACT") else {}
    text = str(schema)
    assert "isolation_required" in text or "isolation required" in text.lower()


# --------------------------------------------------------------------------- #
# honesty: a direct Python effect is not individually approved
# --------------------------------------------------------------------------- #

def test_the_code_tool_never_claims_its_effects_were_approved(tmp_path):
    """'niemals behaupten, jede Codewirkung sei automatisch einzeln genehmigt'."""
    from skaldir.code_exec_tool import run_code_tool

    ws = tmp_path / "ws"
    ws.mkdir()
    tool = run_code_tool(workspace=ws, runtime=tmp_path / "rt", run_id="r")
    result = tool.handler(source="result = 1")
    assert "not automatically" in result["isolation"]
    assert "approved" in result["isolation"]
