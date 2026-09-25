"""DELEGATED WORKERS REALLY WORK — writes and tests through the product's own engines.

The final-acceptance audit's §6b seam shipped the delegation path **read-only**: the model could
call ``spawn_subagents`` and a real family ran, but a ``worker`` child's ``write_file`` had *no*
handler, so a call came back ``mode: "declared"`` / ``executed: false`` — the right existed and
nothing happened. A delegation that cannot produce anything is a survey tool, not a worker
(audit §6b, "honest limit"); the user's target is *worker edit + test, explore read-only,
verifier read + tests with no edits*.

What these tests pin (each names whether it is **live** — real sandbox, real threads, real
provider client — or a **fixture**, i.e. a scripted model boundary):

* a ``worker`` child's write directive really creates the file, through the product's core
  toolset (``skaldir.tools_core``: workspace-scoped, sha256-verified) and the atomic edit
  transaction engine;
* a ``worker``/``verifier`` child's test directive really runs the workspace's **declared
  check** in the ``bwrap`` sandbox (fail-closed), and the isolation is *measured*: the check can
  write inside the workspace and cannot leave a trace outside it;
* an ``explore`` child cannot write, a ``verifier`` cannot edit, and neither is "declared"
  into a success — the refusal is named (``role_ceiling.tool_not_granted``) and no file appears;
* a child cannot delegate further (its own role names ``role_ceiling.delegation_forbidden``, an
  unknown spawn tool is refused, and a declared ``[[spawn:…]]`` creates no nested run);
* a granted tool with **no** handler (``terminal``/``exec``) is reported ``declared`` and
  executed by nobody: no delegated child gets a free shell;
* the whole path works from ``skaldir run``'s own surface: a real goal turn, a real provider
  request over the loopback, a real child that changes the run's workspace.

Only the model boundary is scripted. Every filesystem effect, every test execution and every
refusal below is produced by the product's own code.
"""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path
from typing import Any

import pytest

from skaldir import gates, subagents
from skaldir import runtime_delegation as rd
from skaldir import subagent_roles as roles

# --------------------------------------------------------------------------- #
# helpers                                                                      #
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _no_declared_check_env(monkeypatch):
    """A declared ``SKALDIR_CHECK_*_CMD`` in the ambient environment must not leak in.

    The runtime reads the workspace declaration *and* that variable (one reader,
    ``verify_service.declared_command``); a test that asserts "the workspace's declared check
    ran" would be measuring the wrong declaration.
    """
    import os

    for name in list(os.environ):
        if name.startswith("SKALDIR_CHECK_") or name.startswith("GODSV_CHECK_"):
            monkeypatch.delenv(name, raising=False)


def _scratch_project(tmp_path: Path, *, check_script: str = "") -> Path:
    """A real little project with a real test suite and a real declared check.

    ``.godsv/checks.json`` declares the suite the way a workspace does, so the child's test tool
    runs *the workspace's* command instead of one this test invented.
    """
    workspace = tmp_path / "project"
    (workspace / "tests").mkdir(parents=True)
    (workspace / "tests" / "test_truth.py").write_text(
        "def test_truth():\n    assert 2 + 2 == 4\n", encoding="utf-8"
    )
    (workspace / ".godsv").mkdir()
    (workspace / ".godsv" / "checks.json").write_text(
        json.dumps({"pytest": {"argv": [sys.executable, "-m", "pytest", "-q"]}}),
        encoding="utf-8",
    )
    script = check_script or (
        "import pathlib\n"
        "pathlib.Path('inside.txt').write_text('the check ran in the workspace')\n"
        "pathlib.Path('/tmp/gv-delegated-child-escape.txt').write_text('escaped')\n"
    )
    (workspace / "declared_check.py").write_text(script, encoding="utf-8")
    return workspace


class ScriptedModel:
    """A **scripted model boundary**: one answer per child turn, keyed by session id.

    The product calls ``provider.complete(prompt_id, prompt, tier)`` and reads text — so this
    stands exactly where a model stands, and nothing else about the run is faked: the child
    loop, the role dispatcher, the handlers, the sandbox and the run directory are the
    product's own. The prompt's own ``Session id:`` line is how the child is identified
    (:meth:`skaldir.subagents.SubagentRun` writes it).
    """

    name = "scripted"

    def __init__(self, script: dict[str, list[str]] | list[str]) -> None:
        self.script = script if isinstance(script, dict) else {"_default": list(script)}
        self.prompts: list[str] = []
        self._lock = threading.Lock()

    def complete(self, *, prompt_id: str, prompt: str, tier: str) -> str:
        with self._lock:
            self.prompts.append(prompt)
            session = ""
            for line in str(prompt).splitlines():
                if line.startswith("Session id: "):
                    session = line.split(": ", 1)[1].strip()
                    break
            child = session.split(":")[-1] if session else "_default"
            steps = self.script.get(child) or self.script.get("_default")
            steps = steps or ["FINAL: nothing to do"]
            index = sum(1 for seen in self.prompts[:-1] if f"Session id: {session}" in seen)
        return steps[min(index, len(steps) - 1)]


def _spawn(tmp_path: Path, workspace: Path, role: str, steps: list[str], **kwargs: Any):
    """One real delegated family, driven by a scripted model."""
    provider = ScriptedModel({"c1": steps})
    family = rd.spawn_family(
        tasks=[kwargs.pop("task", "do the delegated job")],
        role=role,
        provider=provider,
        workspace=workspace,
        state_dir=tmp_path / "state",
        session="s1",
        **kwargs,
    )
    return family, provider


def _calls(row: dict[str, Any]) -> list[tuple[str, bool, str, str]]:
    return [
        (call.get("tool"), bool(call.get("executed")), str(call.get("mode")), str(call.get("code")))
        for call in row.get("tool_calls") or []
    ]


def _run_requires_sandbox() -> None:
    if not gates._sandbox_available():
        pytest.skip("no bwrap sandbox on this host — the declared check could not be executed")


# --------------------------------------------------------------------------- #
# a worker really writes                                                       #
# --------------------------------------------------------------------------- #


def test_a_worker_child_really_writes_a_file_through_the_core_toolset(tmp_path: Path):
    """**Live.** Before: the write tool was *declared* and the workspace never changed.

    The directive comes from the model boundary; the write itself is the product's core
    ``write_file`` contract (workspace-scoped path resolution, a re-read sha256 so ``verified``
    is measured) and the run's own record carries the result.
    """
    workspace = _scratch_project(tmp_path)
    family, _provider = _spawn(
        tmp_path,
        workspace,
        "worker",
        [
            'writing the module\n[[tool:write_file]] {"path": "textkit/stamp.py", '
            '"content": "STAMP = 7\\n", "make_parents": true}',
            "done\nFINAL: textkit/stamp.py written",
        ],
    )
    payload = family.payload()
    written = workspace / "textkit" / "stamp.py"

    assert written.is_file(), "the worker's write directive did not create the file"
    assert written.read_text(encoding="utf-8") == "STAMP = 7\n"
    row = payload["children"][0]
    assert row["status"] == "delivered" and row["delivered"] is True
    assert _calls(row)[0][:2] == ("write_file", True)
    assert row["tools_executed"] == 1
    # the result the model read back is the core tool's own output (verified write, real hash)
    result = family.record.as_dict()["children"][0]["tool_calls"][0]["result"]
    assert result["tool"] == "write_file"
    assert result["output"]["verified"] is True
    assert result["output"]["created"] is True
    assert len(result["output"]["sha256"]) == 64
    # the child was told, in its own prompt, that this tool really executes here
    assert "write_file" in payload["limits"]["implemented_tools"]
    assert payload["limits"]["may_write"] is True
    assert payload["mode"] == rd.MODE_WRITE_TESTS


def test_a_worker_child_cannot_write_outside_the_run_workspace(tmp_path: Path):
    """**Live.** The core toolset's fence is the workspace — recorded, and *kept*.

    A "worker may write" must not mean "a worker may write anywhere": an escaping path is
    refused by the tool contract itself, the child reads that refusal, and nothing lands on
    disk outside the run's workspace.
    """
    workspace = _scratch_project(tmp_path)
    outside = tmp_path / "outside.txt"
    family, _provider = _spawn(
        tmp_path,
        workspace,
        "worker",
        [
            '[[tool:write_file]] {"path": "../outside.txt", "content": "escaped"}',
            f'[[tool:write_file]] {{"path": "{outside}", "content": "escaped"}}',
            "done\nFINAL: tried to escape the workspace",
        ],
    )
    row = family.payload()["children"][0]
    assert not outside.exists(), "a delegated child wrote outside the run's workspace"
    assert not (tmp_path / "outside.txt").exists()
    escaping = [call for call in row["tool_calls"] if call["tool"] == "write_file"]
    assert len(escaping) == 2
    assert all(call["ok"] is False for call in escaping), escaping
    # the refusals are the tool's own (it ran and refused), not the role ceiling's
    details = json.dumps(row["tool_calls"])
    assert "blocked" in details or "escape" in details.lower(), details
    record = family.record.as_dict()["children"][0]
    assert all(
        "escapes the declared root" in json.dumps(call["result"])
        for call in record["tool_calls"]
    ), record["tool_calls"]


# --------------------------------------------------------------------------- #
# a worker/verifier really runs the declared check                             #
# --------------------------------------------------------------------------- #


def test_a_worker_child_really_runs_the_workspace_declared_check(tmp_path: Path):
    """**Live (real sandbox).** The test tool executes the workspace's *own* declared command.

    The declaration is read by the product's one reader (``verify_service.declared_command``)
    and executed by the runtime's one judge (``run_declared_criterion`` → ``gates.
    run_declared_check`` → ``bwrap``). The check's own isolation is measured here: it writes
    inside the workspace (the writable bind) and cannot leave ``/tmp`` (the sandbox's tmpfs).
    """
    _run_requires_sandbox()
    workspace = _scratch_project(tmp_path)
    (workspace / ".godsv" / "checks.json").write_text(
        json.dumps(
            {
                "pytest": {
                    "argv": [sys.executable, str(workspace / "declared_check.py")],
                }
            }
        ),
        encoding="utf-8",
    )
    escaped = Path("/tmp/gv-delegated-child-escape.txt")
    if escaped.exists():  # pragma: no cover - a leftover from a manual run
        escaped.unlink()
    family, _provider = _spawn(
        tmp_path,
        workspace,
        "worker",
        ["[[tool:run_tests]] {}", "checked\nFINAL: the declared check ran"],
    )
    row = family.payload()["children"][0]
    assert _calls(row)[0][:2] == ("run_tests", True)
    record = family.record.as_dict()["children"][0]
    result = record["tool_calls"][0]["result"]
    assert result["status"] == "passed", result
    assert result["exit"] == 0
    assert "declared" in result["source"]
    assert str(workspace / "declared_check.py") in result["cmd"]
    # the check really ran, in the workspace, inside the sandbox
    assert (workspace / "inside.txt").read_text(encoding="utf-8").startswith("the check ran")
    assert not escaped.exists(), "the sandboxed check left a trace outside the workspace"


def test_a_failing_declared_check_is_reported_as_failed_not_as_a_pass(tmp_path: Path):
    """**Live.** A red suite is a real verdict — the tool may not round it up.

    Fail-closed reporting is the whole point of routing a child's test through the declared
    check: "did not run" (``unknown``) and "ran and failed" (``failed``) are different facts.
    """
    _run_requires_sandbox()
    workspace = _scratch_project(tmp_path)
    (workspace / "tests" / "test_truth.py").write_text(
        "def test_truth():\n    assert 2 + 2 == 5\n", encoding="utf-8"
    )
    family, _provider = _spawn(
        tmp_path,
        workspace,
        "worker",
        ["[[tool:run_tests]] {}", "done\nFINAL: the suite is red"],
    )
    record = family.record.as_dict()["children"][0]
    result = record["tool_calls"][0]["result"]
    assert result["ok"] is False
    assert result["status"] == "failed"
    assert result["exit"] not in (0, None)
    assert "1 failed" in result["detail"] or "failed" in result["detail"]


# --------------------------------------------------------------------------- #
# the ceilings: explore read-only, verifier read+tests, nobody may spawn        #
# --------------------------------------------------------------------------- #


def test_an_explore_child_cannot_write_through_the_real_path(tmp_path: Path):
    """**Live.** The refusal is the role's, it is named, and the workspace is untouched.

    The directive is handed to the dispatcher exactly as a model's would be; ``explore`` holds
    no write tool, so the handler is never even looked up — and the child is told the code.
    """
    workspace = _scratch_project(tmp_path)
    family, _provider = _spawn(
        tmp_path,
        workspace,
        "explore",
        [
            '[[tool:write_file]] {"path": "explore-sneaky.txt", "content": "x"}',
            '[[tool:read_file]] {"path": "declared_check.py"}',
            "mapped it\nFINAL: read-only survey done",
        ],
    )
    payload = family.payload()
    row = payload["children"][0]
    assert not (workspace / "explore-sneaky.txt").exists()
    tool, executed, mode, code = _calls(row)[0]
    assert tool == "write_file" and executed is False and mode == "refused"
    assert code == roles.REFUSAL_TOOL_NOT_GRANTED
    assert row["tool_refusals"], row
    assert row["tool_refusals"][0]["tool"] == "write_file"
    # ... and it really read: the granted half of the ceiling executes
    assert _calls(row)[1][:2] == ("read_file", True)
    assert payload["mode"] == rd.MODE_READ_ONLY
    assert payload["limits"]["may_write"] is False
    assert payload["limits"]["may_test"] is False
    assert "write_file" not in payload["limits"]["implemented_tools"]


def test_a_verifier_runs_the_declared_check_and_may_change_no_file(tmp_path: Path):
    """**Live (real sandbox).** The role the user asked for: read + tests, no edits.

    A verifier that could edit is not a gate. Its ceiling is *not* weakened to make the test
    tool reachable: the check runs through the declared-check runner, which takes no free-form
    command line at all.
    """
    _run_requires_sandbox()
    workspace = _scratch_project(tmp_path)
    target = workspace / "declared_check.py"
    before = target.read_text(encoding="utf-8")
    family, _provider = _spawn(
        tmp_path,
        workspace,
        "verifier",
        [
            '[[tool:patch]] {"path": "declared_check.py", "old_string": "escaped", '
            '"new_string": "changed"}',
            '[[tool:edit_apply]] {"reason": "tidy it up", "files": [{"path": "declared_check.py", '
            '"edits": [{"old": null, "new": "print(1)\\n"}]}]}',
            "[[tool:run_tests]] {}",
            "verified by reading and running the check\nFINAL: checked, changed nothing",
        ],
    )
    payload = family.payload()
    row = payload["children"][0]
    calls = _calls(row)
    assert calls[0][:4] == ("patch", False, "refused", roles.REFUSAL_TOOL_NOT_GRANTED)
    assert calls[1][:4] == ("edit_apply", False, "refused", roles.REFUSAL_TOOL_NOT_GRANTED)
    assert calls[2][:2] == ("run_tests", True)
    assert target.read_text(encoding="utf-8") == before, "a verifier changed a project file"
    assert payload["mode"] == rd.MODE_READ_TESTS
    assert payload["limits"]["may_write"] is False
    assert payload["limits"]["may_test"] is True
    assert "write_file" not in payload["limits"]["implemented_tools"]
    assert "run_tests" in payload["limits"]["implemented_tools"]


def test_no_delegated_child_can_spawn_another_child(tmp_path: Path):
    """**Live.** Three ways to try, three named refusals, and no nested run on disk.

    A delegation that can delegate is an unbounded tree; the seam is depth 1, and every
    canonical child role's own table says ``may_spawn: false`` (two independent ceilings).
    """
    workspace = _scratch_project(tmp_path)
    family, _provider = _spawn(
        tmp_path,
        workspace,
        "worker",
        [
            '[[tool:spawn_subagents]] {"tasks": ["a grandchild"]}',
            '[[tool:spawn]] {"tasks": ["a grandchild"]}',
            "tried\nFINAL: could not delegate further",
        ],
        task="do the job [[spawn:work no child should ever do]]",
    )
    payload = family.payload()
    row = payload["children"][0]
    calls = _calls(row)
    assert calls[0][0] == "spawn_subagents" and calls[0][1] is False
    assert calls[0][3] == roles.REFUSAL_TOOL_NOT_GRANTED  # not a tool any role holds
    assert calls[1][:4] == ("spawn", False, "refused", roles.REFUSAL_DELEGATION_FORBIDDEN)
    # the brief's declared nesting is refused by the role ceiling too, and leaves nothing behind
    refused = payload["nesting"]["refused"]
    assert refused and refused[0]["code"] == roles.REFUSAL_DELEGATION_FORBIDDEN
    assert payload["nesting"]["spawned"] == []
    run_dir = Path(payload["run_dir"])
    assert not list(run_dir.rglob("nested/*")), "a refused nesting created a directory"
    assert payload["limits"]["may_spawn"] is False and payload["limits"]["max_depth"] == 1
    assert not any(child["role"] == "worker" for child in payload["children"][1:])


def test_a_granted_tool_with_no_handler_is_declared_and_executed_by_nobody(tmp_path: Path):
    """**Live.** A worker holds ``terminal``/``exec`` in its vocabulary — and gets no shell.

    "Declared" has to stay a *report*, not a polite lie: the directive is answered with
    ``mode: declared`` / ``executed: false``, the outcome row says which mode it was, and no
    command ever runs (the marker file the shell command would have created does not exist).
    """
    workspace = _scratch_project(tmp_path)
    family, _provider = _spawn(
        tmp_path,
        workspace,
        "worker",
        [
            f'[[tool:terminal]] {{"command": "touch {tmp_path / "shell-ran.txt"}"}}',
            f'[[tool:exec]] {{"command": "touch {tmp_path / "exec-ran.txt"}"}}',
            "done\nFINAL: nothing else I can do",
        ],
    )
    row = family.payload()["children"][0]
    for call in row["tool_calls"]:
        assert call["executed"] is False, call
        assert call["mode"] == "declared", call
    assert not (tmp_path / "shell-ran.txt").exists()
    assert not (tmp_path / "exec-ran.txt").exists()
    limits = family.payload()["limits"]
    assert "terminal" in limits["declared_tools"] and "terminal" not in limits["implemented_tools"]


def test_the_old_read_only_seam_would_have_left_the_file_absent(tmp_path: Path):
    """**The regression this wave fixes, in one process.** Same directive, two seams.

    ``child_handlers(workspace)`` with no role is exactly the previous wave's handler map (read
    tools only). Through it the write is ``declared`` and the file never appears; the same
    directive on a role-carrying map writes it. That is the difference the audit named.
    """
    workspace = _scratch_project(tmp_path)
    directive = '[[tool:write_file]] {"path": "from-directive.txt", "content": "hello"}'

    read_only = ScriptedModel([directive + "\nFINAL: reported"])
    old_run = subagents.SubagentRun(
        provider=read_only,
        briefs=subagents.briefs_from_tasks(["write it"], depth=1, role="worker"),
        state_dir=tmp_path / "state-old",
        run_id="old-seam",
        tool_handlers=rd.child_handlers(workspace),  # no role: the previous wave's map
    )
    old_record = old_run.run()
    assert not (workspace / "from-directive.txt").exists()
    old_row = old_record.as_dict()["children"][0]
    assert old_row["tool_calls"][0]["mode"] == "declared"
    assert old_row["tool_calls"][0]["executed"] is False

    family, _provider = _spawn(
        tmp_path,
        workspace,
        "worker",
        [directive, "done\nFINAL: wrote it"],
    )
    assert (workspace / "from-directive.txt").read_text(encoding="utf-8") == "hello"
    assert family.payload()["children"][0]["tools_executed"] == 1


# --------------------------------------------------------------------------- #
# the tables agree, and the whole thing works from skaldir run's own surface     #
# --------------------------------------------------------------------------- #


def test_every_handler_a_delegation_registers_is_inside_the_roles_own_ceiling(tmp_path: Path):
    """The registrations and the ceiling come from one table — asserted, role by role."""
    workspace = _scratch_project(tmp_path)
    for role in rd.GRANTABLE_ROLES:
        handlers = set(rd.child_handlers(workspace, role=role, state_dir=tmp_path / "state"))
        ceiling = set(roles.tools_for(role))
        assert handlers <= ceiling, (role, sorted(handlers - ceiling))
        assert "write_file" in handlers if role in ("worker", "implementer") else "write_file" not in handlers
        assert ("run_tests" in handlers) is (
            role in ("worker", "verifier", "debugger", "evidence_collector", "implementer")
        )
        assert rd.role_mode(role) == {
            "explore": rd.MODE_READ_ONLY,
            "worker": rd.MODE_WRITE_TESTS,
            "verifier": rd.MODE_READ_TESTS,
            **{
                name: rd.role_mode(name)
                for name in rd.GRANTABLE_ROLES
                if name not in ("explore", "worker", "verifier")
            },
        }[role]


def test_an_unroled_map_is_still_read_only(tmp_path: Path):
    """The legacy ceiling-free child gets **no new right** from this seam (deliberate)."""
    workspace = _scratch_project(tmp_path)
    handlers = set(rd.child_handlers(workspace))
    assert "write_file" not in handlers and "run_tests" not in handlers
    assert rd.role_mode("") == rd.MODE_READ_ONLY


def test_a_real_run_turn_delegates_a_worker_that_really_changes_the_workspace(tmp_path: Path):
    """**Live end to end (real HTTP loopback, real run, real sandbox).**

    ``skaldir run``'s own surface: the parent's request offers ``spawn_subagents``, the model's
    tool call starts a ``worker`` family through the same provider client, the child's own
    directives (write + declared check) travel back over the wire, and the run's workspace
    really changes. Only the endpoint is scripted — the client, the loop, the dispatcher, the
    handlers, the sandbox and the record are the product's.
    """
    _run_requires_sandbox()
    from test_agent_loop import StubEndpoint, answer, tool_step
    from test_runtime_final_wiring import _goal_client, _goal_options, _green_goal_project

    from skaldir import goal_run

    project, criteria_file, goal = _green_goal_project(tmp_path)
    state = tmp_path / "state"
    seen = {"parent": 0, "child": 0}

    def policy(body: dict[str, Any]) -> dict[str, Any]:
        text = json.dumps(body)
        if "You are child agent" in text:  # the child's own prompt, over the same wire
            seen["child"] += 1
            if seen["child"] == 1:
                return answer(
                    "writing the stamp\n"
                    '[[tool:write_file]] {"path": "textkit/stamp.py", "content": "STAMP = 7\\n"}'
                )
            if seen["child"] == 2:
                return answer("[[tool:run_tests]] {}")
            return answer("wrote textkit/stamp.py; the declared check ran\nFINAL: stamp written")
        seen["parent"] += 1
        if seen["parent"] == 1:
            return tool_step(
                (
                    "call-1",
                    "spawn_subagents",
                    {
                        "tasks": [
                            "write textkit/stamp.py with STAMP = 7 and run the declared check"
                        ],
                        "role": "worker",
                    },
                )
            )
        return answer("All three criteria pass; completion requested.")

    with StubEndpoint([policy]) as stub:
        report = goal_run.run_goal(
            _goal_options(project, state, criteria_file, goal),
            client=_goal_client(stub.base_url),
        )

    stamp = project / "textkit" / "stamp.py"
    assert stamp.is_file(), "the delegated worker did not change the run's workspace"
    assert stamp.read_text(encoding="utf-8") == "STAMP = 7\n"

    offered = [item["function"]["name"] for item in (stub.bodies[0].get("tools") or [])]
    assert "spawn_subagents" in offered, offered
    assert seen["child"] >= 3, "the child never made its own turns"

    turn = report["turns"][0]
    assert turn["delegation"]["role_modes"]["worker"] == rd.MODE_WRITE_TESTS
    assert turn["delegation"]["role_modes"]["explore"] == rd.MODE_READ_ONLY
    assert turn["delegation"]["role_modes"]["verifier"] == rd.MODE_READ_TESTS
    row = turn["delegations"][0]
    assert row["role"] == "worker" and row["mode"] == rd.MODE_WRITE_TESTS
    assert row["delivered"] == 1

    record = json.loads(
        (Path(row["run_dir"]) / "run.json").read_text(encoding="utf-8")
    )
    child = record["children"][0]
    assert [call["tool"] for call in child["tool_calls"]] == ["write_file", "run_tests"]
    assert all(call["executed"] for call in child["tool_calls"])
    assert child["tool_calls"][1]["result"]["status"] == "passed"
    # the workspace's own declared check ran (the fixture project declares one), so the child
    # executed the project's command and not a command this test invented
    assert "declared" in child["tool_calls"][1]["result"]["source"]


def test_the_spawn_tool_description_states_what_each_role_may_really_do(tmp_path: Path):
    """The model reads what it is really getting: write, declared check, no shell."""
    from skaldir import goal_run
    from skaldir import subagents as harness

    workspace = _scratch_project(tmp_path)
    state = tmp_path / "state"
    options = goal_run.GoalRunOptions(
        goal="delegate", workspace=str(workspace), state_dir=str(state)
    )
    bus: dict[str, Any] = {}
    tools = goal_run.turn_tools(
        workspace,
        state,
        "s1",
        bus,
        options,
        delegation_provider=harness.StubProvider(latency_s=0.0, default_turns=1),
    )
    tool = {item.name: item for item in tools}["spawn_subagents"]
    text = tool.description.lower()
    assert "write" in text and "declared check" in text and "shell" in text
    # GRANTABLE_ROLES is 9 now (named research/production roles included) — the map
    # must cover every grantable role, not only the three legacy ones
    modes = bus["delegation"]["role_modes"]
    for name in rd.GRANTABLE_ROLES:
        assert name in modes, (name, sorted(modes))
    assert modes["explore"] == rd.MODE_READ_ONLY
    assert modes["worker"] == rd.MODE_WRITE_TESTS
    assert modes["verifier"] == rd.MODE_READ_TESTS
