"""F03: a run's workspace survives the tool bridge — measured, not assumed.

The audit reproduced the bug: ``terminal.handler(command='pwd')`` ran in the repository
cwd although a temporary workspace was configured, because ``bridge._call`` resolved the
root from the process cwd. Two concurrent runs shared one directory.

The fix binds workspace and runtime per run. These tests are the regression for it, and
they are written so a future change back to a global ``os.chdir`` or env mutation fails
loudly rather than quietly losing the workspace again.
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
def dirs(tmp_path):
    a = tmp_path / "workspace-a"
    a.mkdir()
    b = tmp_path / "workspace-b"
    b.mkdir()
    return a, b, tmp_path / "runtime"


def test_without_a_binding_the_process_cwd_is_used():
    """The old behaviour is unchanged for callers that never bind."""
    result = bridge.terminal("pwd")
    assert _cwd_of(result), "the result must say which directory it ran in"


def test_a_bound_run_runs_in_its_own_workspace(dirs):
    a, _b, runtime = dirs
    with bridge.RunContext(root=a, runtime=runtime, run_id="run-a"):
        result = bridge.terminal("pwd")
    assert _cwd_of(result) == str(a), (
        f"the bound workspace was ignored: ran in {_cwd_of(result)!r}"
    )


def test_two_concurrent_runs_keep_their_own_workspaces(dirs):
    """The actual regression: two runs, two directories, no cross-talk."""
    a, b, runtime = dirs
    seen: dict[str, str] = {}
    errors: list = []

    def go(name: str, root) -> None:
        try:
            with bridge.RunContext(root=root, runtime=runtime, run_id=name):
                seen[name] = _cwd_of(bridge.terminal("pwd"))
        except Exception as exc:  # noqa: BLE001
            errors.append((name, exc))

    threads = [threading.Thread(target=go, args=("run-a", a)),
               threading.Thread(target=go, args=("run-b", b))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert not errors, errors
    assert seen["run-a"] == str(a), f"run-a ran in {seen.get('run-a')!r}"
    assert seen["run-b"] == str(b), f"run-b ran in {seen.get('run-b')!r}"
    assert seen["run-a"] != seen["run-b"]


def test_the_binding_does_not_change_the_process_cwd(dirs):
    """No global ``os.chdir`` — that is what the assignment forbids."""
    a, _b, runtime = dirs
    before = __import__("os").getcwd()
    with bridge.RunContext(root=a, runtime=runtime, run_id="run-a"):
        pass
    assert __import__("os").getcwd() == before, (
        "binding a run context changed the process cwd"
    )


def test_the_binding_does_not_mutate_the_environment(dirs):
    """No global env switching either."""
    a, _b, runtime = dirs
    before = dict(__import__("os").environ)
    with bridge.RunContext(root=a, runtime=runtime, run_id="run-a"):
        pass
    assert dict(__import__("os").environ) == before, (
        "binding a run context mutated the environment"
    )


def test_the_binding_ends_with_the_block(dirs):
    """After the block the old context is back — not the run's workspace."""
    a, _b, runtime = dirs
    outside = _cwd_of(bridge.terminal("pwd"))
    with bridge.RunContext(root=a, runtime=runtime, run_id="run-a"):
        inside = _cwd_of(bridge.terminal("pwd"))
    after = _cwd_of(bridge.terminal("pwd"))
    assert inside == str(a)
    assert after == outside, "the binding leaked past its block"


def test_a_nested_binding_restores_the_outer_one(dirs):
    a, b, runtime = dirs
    with bridge.RunContext(root=a, runtime=runtime, run_id="outer"):
        outer = _cwd_of(bridge.terminal("pwd"))
        with bridge.RunContext(root=b, runtime=runtime, run_id="inner"):
            inner = _cwd_of(bridge.terminal("pwd"))
        restored = _cwd_of(bridge.terminal("pwd"))
    assert outer == str(a) and inner == str(b)
    assert restored == str(a), "the outer binding was not restored"


def test_the_scope_widening_rule_still_holds():
    """The root must never come from a tool argument.

    The bridge documents this on purpose: a model that could set its own root could widen
    its own scope. ``RunContext`` is host-side and not reachable from a tool call.
    """
    import inspect

    source = inspect.getsource(bridge)
    assert "never from an" in source and "argument" in source, (
        "the scope rule must stay documented where the code enforces it"
    )
    # and the public tool entrypoints do not take a root parameter
    for name in ("terminal", "patch", "read_file", "search_files", "web_fetch", "web_search"):
        sig = inspect.signature(getattr(bridge, name))
        assert "root" not in sig.parameters, (
            f"{name} exposes a root parameter — a model could widen its scope"
        )
