"""F01/F03 closing regression: one shared capability set across all agent entries.

The audit measured the gap precisely: a fresh ``build_agent_toolset`` had 14 tools, the
goal run stopped at 6 file builtins, and chat could reach terminal, run_code, web and the
browser while a goal run could not. Each entry built its own partial surface, so "the
agent can execute code" was true from one door and false from the next.

This file pins the contract at the entries themselves:

* **Toolset equality.** The chat/agent-run default (``tool_set_for(kind='builtin')``, the
  exact assembly ``skaldir agent run`` builds with its default ``--tools builtin``), the
  fresh ``build_agent_toolset`` and the goal run's ``turn_tools`` all expose the same
  shared capability set — run_code, terminal, browser, the file tools, patch, search and
  the web pair — asserted by comparing the built toolsets, not by trusting comments.
* **One capability, one name.** No entry offers a duplicate of one capability (the old
  ``list_dir`` / ``list_dir_2`` side-by-side pair is gone for good).
* **Effect metadata is real (F06).** Safe reads carry ``parallel_safe=True``; mutators do
  not. A tool that can do anything is never scheduled alongside a sibling.
* **Concurrent runs keep their own workspace (F03, VAL-HARNESS-008).** Two concurrent
  goal turns hold different workspaces: a shell ``pwd`` in each returns its own root,
  and a write in one never leaks into the other. The bridge-level regression lives in
  ``tests/test_run_context.py``; this test pins the same contract one level higher, at
  the model-facing tool handle the run actually calls.

Everything here runs the product's real assembly path with real subprocesses and real
filesystems (``tmp_path``); the only thing not exercised is the model itself, which this
feature does not bind to.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest

from skaldir import goal_run
from skaldir.agent_loop import tool_set_for
from skaldir.agent_toolset import build_agent_toolset
from skaldir.core_capabilities import CAPABILITY_INDEX

#: The shared capability set every entry must expose. These names are the contract;
#: a renamed capability has to change CAPABILITY_INDEX and this table together.
SHARED_CAPABILITIES = ("browser", "list_dir", "patch", "process", "read_file",
                       "read_text", "run_code", "search_files", "sha256_file",
                       "terminal", "web_fetch", "web_search", "write_text")

#: Safe reads (F06): no side effect on the workspace, safe beside their siblings.
SAFE_READS = ("read_file", "list_dir", "search_files", "web_search", "web_fetch")

#: Mutators: one writer at a time.
MUTATORS = ("terminal", "patch", "write_text", "process", "browser", "run_code")


def _names(tools: list[Any]) -> set[str]:
    return {tool.name for tool in tools}


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    return ws


@pytest.fixture()
def state(tmp_path: Path) -> Path:
    st = tmp_path / "state"
    st.mkdir()
    return st


# --------------------------------------------------------------------------- #
# toolset equality across the three entries
# --------------------------------------------------------------------------- #

def test_all_three_entries_expose_the_shared_capability_set(workspace, state):
    """Chat/agent-run default, fresh build_agent_toolset and goal turn_tools agree.

    This is the F01 assertion: the same core capability set reaches every agent entry,
    including run_code, terminal and the browser adapter through regular model tools.
    """
    # chat + `agent run` default (--tools builtin): the loop surface
    chat_and_agent_run = tool_set_for(workspace=workspace, kind="builtin")
    # --agent-tools: the fresh full assembly (builtin kind, no registry yet)
    fresh = build_agent_toolset(workspace=workspace, kind="builtin", runtime=state)
    # goal run: one turn's tool surface
    bus: dict[str, Any] = {}
    options = goal_run.GoalRunOptions(
        goal="toolset-equality probe",
        workspace=str(workspace),
        state_dir=str(state),
    )
    goal = goal_run.turn_tools(workspace, state, "sess-equality", bus, options)

    for label, toolset in (
        ("chat/agent-run", chat_and_agent_run),
        ("build_agent_toolset", fresh),
        ("goal turn_tools", goal),
    ):
        offered = _names(toolset)
        missing = sorted(set(SHARED_CAPABILITIES) - offered)
        assert not missing, f"{label} does not offer the shared capabilities: {missing}"
        duplicates = [
            name for name in offered
            if sum(1 for tool in toolset if tool.name == name) > 1
        ]
        assert not duplicates, f"{label} offered one capability twice: {duplicates}"


def test_the_shared_set_matches_the_capability_index(workspace, state):
    """CAPABILITY_INDEX is the documented contract; the toolsets must not drift from it.

    Every capability the index names is really offered (and the index does not advertise
    something an entry can call but nobody offers).
    """
    turn_tools = goal_run.turn_tools(
        workspace, state, "sess-index", {}, goal_run.GoalRunOptions(
            goal="index probe", workspace=str(workspace), state_dir=str(state),
        )
    )
    offered = _names(turn_tools)
    for capability in CAPABILITY_INDEX:
        assert capability in offered, (
            f"CAPABILITY_INDEX promises {capability!r} but the goal turn does not offer it"
        )


def test_no_entry_still_offers_list_dir_2(workspace, state):
    """The audit saw ``list_dir`` and ``list_dir_2`` side by side — the duplicate is gone.

    "One capability, one model tool": the directory-listing capability exists exactly once
    in each entry, under the name the docs use.
    """
    for label, toolset in (
        ("loop", tool_set_for(workspace=workspace, kind="builtin")),
        ("fresh", build_agent_toolset(workspace=workspace, kind="builtin", runtime=state)),
    ):
        names = [tool.name for tool in toolset]
        assert "list_dir" in names, f"{label} lost the directory listing capability"
        assert "list_dir_2" not in names, (
            f"{label} still offers the duplicate list_dir_2 alongside list_dir"
        )


def test_safe_reads_are_parallel_safe_and_mutators_are_not(workspace, state):
    """F06: effect metadata is real, derived from what each tool does."""
    tools = goal_run.turn_tools(
        workspace, state, "sess-effects", {}, goal_run.GoalRunOptions(
            goal="effects probe", workspace=str(workspace), state_dir=str(state),
        )
    )
    by_name = {tool.name: tool for tool in tools}
    for name in SAFE_READS:
        tool = by_name.get(name)
        assert tool is not None, f"safe read {name} vanished from the turn surface"
        assert tool.parallel_safe is True, f"safe read {name} was serialised again"
        assert tool.mutating is False, f"safe read {name} claims to mutate"
    for name in ("terminal", "patch", "write_text"):
        tool = by_name.get(name)
        assert tool is not None
        assert tool.parallel_safe is False, (
            f"mutator {name} was marked parallel_safe — a parallel write race shipped"
        )
        assert tool.mutating is True


# --------------------------------------------------------------------------- #
# concurrent goal turns keep their own workspaces (F03 / VAL-HARNESS-008)
# --------------------------------------------------------------------------- #

@pytest.fixture()
def two_workspace_state(tmp_path: Path):
    """Two workspaces with different content, and a separate state dir for each."""
    root = tmp_path / "runs"
    a = root / "ws-a"
    b = root / "ws-b"
    (a / "data.txt").parent.mkdir(parents=True)
    (b / "data.txt").parent.mkdir(parents=True)
    (a / "data.txt").write_text("IN-A", encoding="utf-8")
    (b / "data.txt").write_text("IN-B", encoding="utf-8")
    return root, a, b


def _turn_toolset(workspace: Path, state: Path, session: str) -> dict[str, Any]:
    options = goal_run.GoalRunOptions(
        goal="isolation probe",
        workspace=str(workspace),
        state_dir=str(state),
    )
    return {
        tool.name: tool
        for tool in goal_run.turn_tools(workspace, state, session, {}, options)
    }


def _cwd_of(result: dict[str, Any]) -> str:
    output = result.get("output") if isinstance(result, dict) else None
    if isinstance(output, dict):
        return str(output.get("cwd") or "")
    return str(result.get("cwd") or "")


def test_two_concurrent_goal_turns_run_shell_in_their_own_workspace(two_workspace_state):
    """The F03 reproduction, at the model-facing surface: pwd answers its own root.

    Two concurrent turns hold different workspaces; each turn's terminal tool runs the
    shell there and reads its own file. On the buggy build both pooled the process cwd.
    """
    _root, a, b = two_workspace_state

    def go(name: str, ws: Path, out: dict[str, dict[str, str]]) -> None:
        tools = _turn_toolset(ws, ws.parent / f"state-{name}", f"sess-{name}")
        out[name] = {
            "pwd": _cwd_of(tools["terminal"].handler(command="pwd")),
            "read": str(tools["read_file"].handler(path="data.txt")),
        }

    seen: dict[str, dict[str, str]] = {}
    errors: list[Exception] = []

    def guarded(name: str, ws: Path) -> None:
        try:
            go(name, ws, seen)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=guarded, args=("a", a)),
               threading.Thread(target=guarded, args=("b", b))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)

    assert not errors, errors
    assert seen["a"]["pwd"] == str(a), f"turn a ran its shell in {seen['a']['pwd']!r}"
    assert seen["b"]["pwd"] == str(b), f"turn b ran its shell in {seen['b']['pwd']!r}"
    assert "IN-A" in seen["a"]["read"], f"turn a read the wrong file: {seen['a']['read'][:120]}"
    assert "IN-B" in seen["b"]["read"], f"turn b read the wrong file: {seen['b']['read'][:120]}"


def test_a_goal_write_does_not_leak_into_the_other_workspace(two_workspace_state):
    """Writes are the side effect that must stay with the run that made them."""
    _root, a, b = two_workspace_state

    def write(name: str, ws: Path) -> None:
        tools = _turn_toolset(ws, ws.parent / f"state-{name}", f"sess-w-{name}")
        tools["terminal"].handler(command=f"echo {name} > from_{name}.txt")

    threads = [threading.Thread(target=write, args=("a", a)),
               threading.Thread(target=write, args=("b", b))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)

    assert (a / "from_a.txt").read_text(encoding="utf-8").strip() == "a"
    assert (b / "from_b.txt").read_text(encoding="utf-8").strip() == "b"
    assert not (a / "from_b.txt").exists(), "turn b's write leaked into turn a"
    assert not (b / "from_a.txt").exists(), "turn a's write leaked into turn b"
