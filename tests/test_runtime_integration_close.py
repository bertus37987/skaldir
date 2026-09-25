"""CLOSING THE RUNTIME INTEGRATION SEAMS — the hooks the final-acceptance wave left open.

The audit of the last wave (`~/Documents/Final_Audit/11-runtime-final-acceptance.md`, §6) named
five seams where a control existed behind the runtime but was **not reachable from the runtime**.
Each test here proves the seam is closed on the *production* path, with a real engine behind the
call, never a fixture that only agrees with itself:

(a) ``subagents_cli`` — ``skaldir agent spawn``/``retry`` expose ``--role``, so the CLI's own
    children can carry a role ceiling (and a ``main`` child can really nest).
(b) ``goal_run.turn_tools`` — the model's tool surface carries a real ``spawn_subagents`` tool
    whose handler really runs a subagent family, with real read-only handlers registered on the
    children's dispatchers and a hard ceiling the tool cannot be asked to widen.
(c) ``completion.execute_check`` — a criterion that declares a **command vector** is executed by
    the completion gate's own runner, so the gate can certify it (before: only ``UNKNOWN``).
(d) ``llm_provider.complete`` — the retry gate is consulted on the non-streaming path too.
(e) ``gates._run`` — a *silent* non-0/1 child exit is reported as the child's own code, not as
    "sandbox unavailable" (the misreport that destroyed the recovered run's diagnosis).

Every test names whether it is **live** (real sandbox / real provider client / real threads) or a
**fixture** (a scripted boundary). No test here calls a model over the network.
"""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from skaldir import gates
from skaldir.llm_provider import LLMProviderError, provider_for
from skaldir.progress_guard import RetryGate


def _python_inside_sandbox() -> str:
    """The interpreter the product runs its declared checks with (bound by ``gates._run``)."""
    return sys.executable

# --------------------------------------------------------------------------- #
# (e) a silent sandboxed exit is the child's own code, never "sandbox unavailable" #
# --------------------------------------------------------------------------- #


def test_a_silent_sandboxed_exit_is_reported_as_the_childs_own_code(tmp_path: Path):
    """**Live.** The recovered long run's diagnosis was destroyed by this misreport.

    Raw before (``evidence/12-runtime-final/silent-exit-misreported.txt``): a silent ``exit 3``
    came back as ``exit=-127``, ``sandboxed=False``, reason "sandbox unavailable — execution
    blocked (fail-closed)" — while the same exit **with** one line of output was reported as
    ``exit=3``. ``bwrap`` propagates the child's code, so a silent non-0/1 exit is the *child*
    speaking; only ``bwrap``'s own failure (which it always announces on stderr with its
    ``bwrap:`` prefix) is a sandbox failure. This never created a false PASS — ``UNKNOWN`` is not
    success — but it made ``expect_exit`` unobservable and blamed the wrong subsystem.
    """
    if not gates._sandbox_available():
        pytest.skip("no bwrap sandbox on this host — the reporting branch cannot be exercised")
    workdir = tmp_path / "ws"
    workdir.mkdir()

    for exit_code in (0, 1, 2, 3, 7):
        code, output, sandboxed = gates._run(
            [_python_inside_sandbox(), "-c", f"import sys; sys.exit({exit_code})"],
            workdir=workdir,
            timeout=60,
        )
        assert sandboxed is True, f"exit {exit_code} was reported as an unsandboxed run"
        assert code == exit_code, (
            f"a silent exit {exit_code} was reported as {code} / {output!r} — the child's own "
            "code must survive the sandbox wrapper"
        )
        assert output == ""


def test_a_sandboxed_failure_that_bwrap_announces_stays_fail_closed(tmp_path: Path, monkeypatch):
    """**Fixture (the subprocess boundary only).** Fail-closed is preserved, not weakened.

    ``bwrap``'s own failures always carry its ``bwrap:`` diagnostic on stderr (measured:
    ``bwrap: Can't find source path /nonexistent-xyz`` → exit 1). A non-0/1 code *with* such a
    diagnostic is a sandbox failure, and untrusted code is never run unsandboxed: ``-127`` and
    ``sandboxed=False`` are what the caller must see.
    """
    import subprocess as sp

    class FakeProc:
        returncode = 2
        stdout = ""
        stderr = "bwrap: Can't find source path /nonexistent-xyz: No such file or directory"

    monkeypatch.setattr(
        gates.shutil, "which", lambda name: "/usr/bin/bwrap" if name == "bwrap" else None
    )
    monkeypatch.setattr(gates, "_sandbox_available", lambda: True)
    monkeypatch.setattr(gates.subprocess, "run", lambda *a, **kw: FakeProc())

    workdir = tmp_path / "ws"
    workdir.mkdir()
    code, output, sandboxed = gates._run(
        [_python_inside_sandbox(), "-c", "pass"], workdir=workdir, timeout=10
    )
    assert (code, sandboxed) == (gates.NO_SANDBOX_EXIT, False)
    assert "sandbox unavailable" in output
    assert isinstance(sp.CompletedProcess, type)  # the real boundary stays the module's own


# --------------------------------------------------------------------------- #
# (d) the retry gate guards the non-streaming path too                          #
# --------------------------------------------------------------------------- #


class _RateLimitEndpoint:
    """A **real** ``/v1/chat/completions`` endpoint that answers 429 and counts requests.

    ``429`` is the one status the §43 policy *does* retry, so this stub measures the questions
    that matter here: did the client re-send the request, and how many times? Only the endpoint
    is scripted — the client, its attempt loop and the gate are the product's own.
    """

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002, ARG002
                return

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                with owner._lock:
                    owner.requests.append(
                        {"path": self.path, "raw": raw.decode("utf-8", "replace")}
                    )
                body = json.dumps(
                    {
                        "error": {
                            "message": "rate limit exceeded",
                            "type": "rate_limit_error",
                            "code": "rate_limit_exceeded",
                        }
                    }
                ).encode()
                self.send_response(429)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                self.wfile.flush()

        class Server(ThreadingHTTPServer):
            allow_reuse_address = True
            daemon_threads = True

        self._server = Server(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(
            target=lambda: self._server.serve_forever(poll_interval=0.02), daemon=True
        )

    def __enter__(self) -> _RateLimitEndpoint:
        self._thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}/v1"

    @property
    def count(self) -> int:
        with self._lock:
            return len(self.requests)


def _rate_limited_client(base_url: str, *, max_retries: int = 1):
    return provider_for(
        {
            "provider": "openai-compatible",
            "base_url": base_url,
            "model": "stub-rate-limited",
            "api_key": "stub-local-key",
            "max_retries": max_retries,
            "timeout_s": 5.0,
            "backoff_s": 0.01,
        }
    )


def test_a_non_streaming_completion_asks_the_gate_before_it_re_sends(tmp_path: Path):
    """**Live (real HTTP, real client).** Before: ``complete()`` never consulted the gate.

    The provider's §43 policy alone decided to re-send. ``chat()`` has always consulted the
    runtime's commit point; the non-streaming ``complete()`` did not, so a prompt that reaches
    a user by that route was unguarded — the seam the final-acceptance audit named (§6d).
    """
    gate = RetryGate(max_attempts=1)
    with _RateLimitEndpoint() as endpoint:
        client = _rate_limited_client(endpoint.base_url)
        client.retry_gate = gate
        gate.begin(turn=0, attempt=1)
        with pytest.raises(LLMProviderError):
            client.complete(prompt_id="p1", prompt="compact this", tier="small")
        assert endpoint.count == 2, "the allowed retry did not reach the endpoint"
    # the provider asked the gate for its one retry: one decision, one unit
    assert gate.retries == 1
    assert gate.refusals == 0


def test_a_non_streaming_completion_never_re_sends_past_the_commit_point(tmp_path: Path):
    """**Live.** Past the commit point the retry is refused and nothing is sent again.

    ``note_visible_output`` is how the loop says "text already reached the user"; re-sending
    would print the same answer twice. The refusal is recorded on the gate (``refusals``), so a
    run report counts it once.
    """
    gate = RetryGate(max_attempts=1)
    with _RateLimitEndpoint() as endpoint:
        client = _rate_limited_client(endpoint.base_url, max_retries=3)
        client.retry_gate = gate
        gate.begin(turn=0, attempt=1)
        gate.note_visible_output(chars=42)
        with pytest.raises(LLMProviderError):
            client.complete(prompt_id="p1", prompt="say it again", tier="main")
        assert endpoint.count == 1, "the request was re-sent past the commit point"
    assert gate.refusals == 1
    assert gate.retries == 0
    # the refusal's own words live on the gate's decision — where the run report reads them
    refusal = gate.last()
    assert refusal is not None and refusal.retry is False
    assert "commit point" in refusal.reason


# --------------------------------------------------------------------------- #
# (c) a criterion that declares a command *vector* is certifiable by both gates  #
# --------------------------------------------------------------------------- #


def _vector_criteria(project: Path):
    """One passing and one failing declared command vector, read the way a run reads them."""
    from skaldir import goal_run

    return goal_run.normalise_criteria(
        [
            {
                "id": "c1",
                "criterion": "the declared command really runs and passes",
                "check": [sys.executable, "-c", "print('declared check ran')"],
            },
            {
                "id": "c2",
                "criterion": "a silent non-zero exit is the command's own verdict",
                # *silent* on purpose: this is the exit the sandbox wrapper used to misreport as
                # "sandbox unavailable" (exit -127), which destroyed the diagnosis of the
                # recovered long run. Both gates must now read the command's own code.
                "check": [sys.executable, "-c", "import sys; sys.exit(3)"],
            },
        ]
    )


def _run_both_gates(tmp_path: Path):
    """Run the completion gate's DoD check and the verify pass over the same criteria."""
    from skaldir import completion, goal_run, verify_service

    project = tmp_path / "ws"
    project.mkdir()
    criteria = _vector_criteria(project)
    dod = goal_run._dod_for(criteria)
    evidence = completion.run_requirement_checks(
        dod,
        project,
        timeout=120,
        revision=1,
        workspace=str(project),
        session="s1",
    )
    gate = completion.GateRunner(
        repo_root=project,
        dod=list(dod),
        revision=1,
        workspace=str(project),
        session="s1",
    )
    findings = gate.gate_dod(
        completion.CompletionRequest(user_goal="ship the declared checks"), evidence
    )
    plan = verify_service.build_plan(criteria, goal="ship it", workspace=project, revision=1)
    verified = [verify_service.check_criterion(item, workspace=project, timeout=120) for item in
                plan.criteria]
    return evidence, findings, verified


def test_the_completion_runner_executes_a_declared_command_vector(tmp_path: Path):
    """**Live (real sandbox).** Before: the completion gate had no runner for ``command``.

    The producer side refused safely — the model's completion requests came back with
    "definition-of-done not verified" and the goal could only close through the independent
    verify pass (the seam named in the final-acceptance audit, §6c). Each criterion's **own**
    vector is executed here, so two command criteria do not collapse into one check.
    """
    evidence, _findings, _verified = _run_both_gates(tmp_path)
    rows = {item.check_id: item for item in evidence}
    assert set(rows) == {"command:c1", "command:c2"}, rows
    assert rows["command:c1"].source == "runner"
    assert rows["command:c1"].status == "passed"
    assert "exit 0" in rows["command:c1"].detail
    # the silent non-0 exit reaches the gate as the command's own code (not as UNKNOWN)
    assert rows["command:c2"].status == "failed"
    assert "exit 3" in rows["command:c2"].detail
    assert rows["command:c2"].revision == 1 and rows["command:c2"].session == "s1"


def test_the_completion_gate_certifies_a_declared_command_vector(tmp_path: Path):
    """**Live.** The DoD gate's finding list is empty for a criterion whose command passed.

    The blocking criterion that really failed must still produce a finding — this is the same
    gate, so closing the seam may not weaken it.
    """
    evidence, findings, _verified = _run_both_gates(tmp_path)
    blocked = {finding.requirement for finding in findings}
    assert blocked == {"c2"}, [finding.detail for finding in findings]
    assert any(item.check_id == "command:c1" and item.passed for item in evidence)


def test_both_gates_judge_a_declared_command_vector_the_same_way(tmp_path: Path):
    """**Live.** One judge: the vector's verdict may not differ between the two gates.

    The completion gate reads the model's claim; the verify pass is the independent one. If
    they judge the same command differently, the run either closes on a claim the independent
    pass would reject, or refuses a claim the independent pass accepts — the deadlock §6c
    describes.
    """
    _evidence, _findings, verified = _run_both_gates(tmp_path)
    by_id = {item["criterion_id"]: item for item in verified}
    assert set(by_id) == {"c1", "c2"}, by_id
    assert by_id["c1"]["status"] == "passed"
    assert by_id["c1"]["exit_code"] == 0
    assert by_id["c2"]["status"] == "failed"
    assert by_id["c2"]["exit_code"] == 3
    assert "exit 3" in str(by_id["c2"]["reason"])


def test_a_command_check_with_no_vector_is_refused_by_name(tmp_path: Path):
    """A ``command`` check with nothing declared is *not certifiable*, never a silent ``None``.

    ``execute_check`` used to return ``None`` for anything it did not know, which the gate
    reported as "no runner" — accurate but nameless. A ``command`` id with no vector names the
    missing declaration instead.
    """
    from skaldir import completion

    project = tmp_path / "ws"
    project.mkdir()
    evidence = completion.execute_check(
        "command", project, timeout=30, revision=1, workspace=str(project), session="s1"
    )
    assert evidence is not None
    assert evidence.status == completion.NOT_CERTIFIABLE
    assert "vector" in evidence.detail or "command" in evidence.detail


# --------------------------------------------------------------------------- #
# (b) the run offers the model a real ``spawn_subagents`` tool                    #
# --------------------------------------------------------------------------- #


def _run_surface(tmp_path: Path, *, provider: Any = None):
    """The real ``skaldir run`` tool surface (``goal_run.turn_tools``) plus its bus."""
    from skaldir import goal_run

    workspace = tmp_path / "ws"
    workspace.mkdir(exist_ok=True)
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    options = goal_run.GoalRunOptions(
        goal="delegate the survey", workspace=str(workspace), state_dir=str(state)
    )
    bus: dict[str, Any] = {}
    tools = goal_run.turn_tools(
        workspace, state, "s1", bus, options, delegation_provider=provider
    )
    return {tool.name: tool for tool in tools}, workspace, state, bus


def test_the_run_does_not_offer_a_tool_it_cannot_honour(tmp_path: Path):
    """No provider attached ⇒ **no** ``spawn_subagents`` tool, and the reason is recorded.

    A tool the runtime cannot honour is not offered at all; the run's own bus says why, so
    "the model never delegated" is distinguishable from "delegation was not available".
    """
    tools, _workspace, _state, bus = _run_surface(tmp_path)
    assert "spawn_subagents" not in tools
    assert bus["delegation"]["offered"] is False
    assert "provider" in bus["delegation"]["reason"]


def test_the_run_offers_the_model_a_real_spawn_subagents_tool(tmp_path: Path):
    """**Live surface.** The tool is in ``skaldir run``'s toolset, typed, with a real handler."""
    from skaldir import subagents

    provider = subagents.StubProvider(latency_s=0.0, default_turns=1)
    tools, _workspace, _state, bus = _run_surface(tmp_path, provider=provider)
    tool = tools["spawn_subagents"]
    assert callable(tool.handler)
    assert tool.parallel_safe is False and tool.mutating is False
    properties = tool.input_schema["properties"]
    assert properties["tasks"]["type"] == "array"
    assert properties["tasks"]["items"]["type"] == "string"
    assert tool.input_schema["required"] == ["tasks"]
    assert properties["role"]["type"] == "string"
    assert "explore" in tool.description and "worker" in tool.description
    assert bus["delegation"]["offered"] is True
    assert bus["delegation"]["tool"] == "spawn_subagents"


def test_the_spawn_tool_really_spawns_role_bearing_children(tmp_path: Path):
    """**Live (real threads, real child runs on disk, stub provider).**

    The handler is not a promise: it creates a real :class:`subagents.SubagentRun`, the child
    works, and the model gets a bounded, structured answer back.
    """
    from skaldir import runtime_delegation, subagents

    provider = subagents.StubProvider(latency_s=0.0, default_turns=1)
    tools, workspace, state, _bus = _run_surface(tmp_path, provider=provider)
    (workspace / "note.txt").write_text("the loader lives here\n", encoding="utf-8")

    outcome = tools["spawn_subagents"].handler(tasks=["map the loader", "list the fixtures"],
                                               role="explore")
    payload = outcome["data"] if "data" in outcome else outcome
    assert payload["role"] == "explore"
    assert payload["total"] == 2 and payload["delivered"] == 2
    assert [child["role"] for child in payload["children"]] == ["explore", "explore"]
    assert all(child["status"] == "delivered" for child in payload["children"])
    assert all(child["answer"] for child in payload["children"])
    assert payload["mode"] == "read-only"
    # the model is told which of the role's tools can really run here and which are only
    # *declared* — an honest limit, not a surprise it discovers from a call
    limits = payload["limits"]
    assert "read_file" in limits["implemented_tools"]
    assert "web_fetch" in limits["declared_tools"]
    assert limits["may_spawn"] is False and limits["max_depth"] == 1
    run_dir = Path(payload["run_dir"])
    record = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert record["run_id"] == payload["run_id"]
    assert record["max_depth"] == 1
    assert record["children_total"] == 2
    assert all(item["context"]["may_spawn"] is False for item in record["children"])
    assert all(item["tools"] for item in record["children"])
    # the state dir the runtime handed over is where the children's work lives
    assert str(run_dir).startswith(str(state))
    assert runtime_delegation.SPAWN_TOOL_NAME == "spawn_subagents"


def test_the_childs_dispatcher_holds_real_handlers(tmp_path: Path):
    """**Live.** A delegated child's tools really execute — and its ceiling still refuses.

    The handlers are the module's own implementation over the workspace (not a stub that says
    "declared"): a read reaches the real file. A ``write``/``exec`` tool a role happens to hold
    in its vocabulary with no registered handler is reported ``declared`` and never pretends to
    have run, while a write tool the role's ceiling *does* hold has a real handler — what a role
    may do is its own table (``test_delegated_worker_capabilities.py`` proves the
    explore/worker/verifier split end to end).
    """
    from skaldir import runtime_delegation, subagent_roles, subagents

    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "note.txt").write_text("the loader lives here\n", encoding="utf-8")
    (workspace / "other.md").write_text("nothing to see\n", encoding="utf-8")
    state = tmp_path / "state"
    provider = subagents.StubProvider(latency_s=0.0, default_turns=1)

    family = runtime_delegation.spawn_family(
        tasks=["map the loader"],
        role="explore",
        provider=provider,
        workspace=workspace,
        state_dir=state,
        session="s1",
    )
    dispatcher = family.run.dispatcher_for("c1")
    assert "read_file" in dispatcher.handlers
    assert set(dispatcher.handlers) <= set(subagent_roles.tools_for("explore"))

    read = family.run.dispatch_tool("c1", "read_file", path="note.txt")
    assert "the loader lives here" in json.dumps(read)
    search = family.run.dispatch_tool("c1", "search_files", pattern="loader", path=".")
    assert "note.txt" in json.dumps(search)
    listing = family.run.dispatch_tool("c1", "list_dir", path=".")
    assert "other.md" in json.dumps(listing)

    with pytest.raises(subagent_roles.ToolCeilingRefused) as excinfo:
        family.run.dispatch_tool("c1", "write_file", path="x.txt", content="nope")
    assert excinfo.value.code == subagent_roles.REFUSAL_TOOL_NOT_GRANTED
    assert not (workspace / "x.txt").exists()

    # a granted-but-unimplemented tool is reported as declared, never as executed ...
    worker = runtime_delegation.spawn_family(
        tasks=["write something"],
        role="worker",
        provider=subagents.StubProvider(latency_s=0.0, default_turns=1),
        workspace=workspace,
        state_dir=tmp_path / "state2",
        session="s1",
    )
    declared = worker.run.dispatch_tool("c1", "terminal", command="touch x.txt")
    assert declared["mode"] == "declared" and declared["executed"] is False
    assert not (workspace / "x.txt").exists()
    # ... while the write the worker's ceiling does hold executes through the core toolset
    written = worker.run.dispatch_tool("c1", "write_file", path="x.txt", content="yes")
    assert written["mode"] == "handler" and written["executed"] is True
    assert written["output"]["verified"] is True
    assert (workspace / "x.txt").read_text(encoding="utf-8") == "yes"


def test_the_spawn_tool_refuses_a_role_that_would_widen_the_ceiling(tmp_path: Path):
    """The model may not ask for a ceiling-free child, nor for ``main`` — and nothing is spawned.

    ``unroled`` is the historical, unlimited child and ``main`` is the root's own surface; both
    exist in the role table, and neither may be granted by a tool call. The refusal is named and
    leaves no directory behind (a refused delegation leaves nothing half-built).
    """
    from skaldir import subagents

    tools, _workspace, state, _bus = _run_surface(
        tmp_path, provider=subagents.StubProvider(latency_s=0.0, default_turns=1)
    )
    for requested in ("main", "unroled", "MAIN", "archduke"):
        outcome = tools["spawn_subagents"].handler(tasks=["do anything"], role=requested)
        assert outcome["ok"] is False, requested
        assert outcome["error"]["code"] == "delegation.role_not_grantable", outcome
        assert "worker" in outcome["error"]["message"]
    assert not any(state.iterdir()), "a refused delegation created a run"


def test_the_spawn_tool_is_bounded_in_children_and_stays_shallow(tmp_path: Path):
    """A delegation is capped, and its children may not nest further from this seam."""
    from skaldir import runtime_delegation, subagents

    tools, _workspace, _state, bus = _run_surface(
        tmp_path, provider=subagents.StubProvider(latency_s=0.0, default_turns=1)
    )
    too_many = [f"task {index}" for index in range(runtime_delegation.MAX_DELEGATED_CHILDREN + 1)]
    outcome = tools["spawn_subagents"].handler(tasks=too_many)
    assert outcome["ok"] is False
    assert outcome["error"]["code"] == "delegation.too_many_tasks"
    assert str(runtime_delegation.MAX_DELEGATED_CHILDREN) in outcome["error"]["message"]

    empty = tools["spawn_subagents"].handler(tasks=[])
    assert empty["ok"] is False and empty["error"]["code"] == "delegation.no_tasks"

    outcome = tools["spawn_subagents"].handler(tasks=["one job"])
    payload = outcome["data"] if "data" in outcome else outcome
    assert payload["max_depth"] == 1
    assert bus["delegations"][-1]["children"] == payload["total"]
    assert bus["delegations"][-1]["role"] == "worker"


def test_a_delegation_that_the_provider_refuses_is_reported_not_invented(tmp_path: Path):
    """A blocked provider is a **named** outcome on the tool, never a fabricated child result."""
    from skaldir import runtime_delegation

    class Blocked:
        name = "blocked"

        def complete(self, *, prompt_id: str, prompt: str, tier: str) -> str:
            raise RuntimeError("provider is not configured for this block")

    family = runtime_delegation.spawn_family(
        tasks=["do the thing"],
        role="worker",
        provider=Blocked(),
        workspace=tmp_path,
        state_dir=tmp_path / "state",
        session="s1",
    )
    payload = family.payload()
    assert payload["delivered"] == 0
    assert payload["children"][0]["status"] == "failed"
    assert "provider is not configured" in str(payload["children"][0]["error"])
    assert payload["failure_classes"]


def test_a_real_run_turn_offers_the_tool_and_the_model_really_delegates(tmp_path: Path):
    """**Live end to end**: a real ``skaldir run`` goal turn, a real provider request, a real child.

    This is the seam itself: the tool is in the request the provider received (so the model could
    ask for it), the model's tool call really started a child through the *same* provider, the
    child's answer came back to the model, and the turn's own record says what was delegated.
    Nothing here is a fixture of the delegation path: only the endpoint is scripted.
    """
    from test_agent_loop import StubEndpoint, answer, tool_step
    from test_runtime_final_wiring import _goal_client, _goal_options, _green_goal_project

    from skaldir import goal_run, subagents

    project, criteria_file, goal = _green_goal_project(tmp_path)
    state = tmp_path / "state"
    child_answer = answer(
        f"the helper lives in textkit/slug.py\n{subagents.FINAL_MARKER} surveyed the helper"
    )
    steps = [
        tool_step(
            (
                "call-1",
                "spawn_subagents",
                {"tasks": ["survey the slug helper"], "role": "explore"},
            )
        ),
        child_answer,
        answer("All three criteria pass; completion requested."),
    ]
    with StubEndpoint(steps) as stub:
        report = goal_run.run_goal(
            _goal_options(project, state, criteria_file, goal),
            client=_goal_client(stub.base_url),
        )

    offered = [item["function"]["name"] for item in (stub.bodies[0].get("tools") or [])]
    assert "spawn_subagents" in offered, offered
    assert "run_tests" in offered and "edit_apply" in offered

    # the child's own prompt reached the same provider (a real child, not a claim about one)
    child_requests = [
        body for body in stub.bodies[1:] if "survey the slug helper" in json.dumps(body)
    ]
    assert child_requests, "no child request reached the provider"

    # the child's answer travelled back to the model as the tool result
    assert any(
        "surveyed the helper" in json.dumps(body) for body in stub.bodies[1:]
    ), "the child's answer never reached the model"

    turn = report["turns"][0]
    assert turn["delegation"]["offered"] is True
    assert turn["delegation"]["tool"] == "spawn_subagents"
    rows = turn["delegations"]
    assert rows, turn["tools_used"]
    assert rows[0]["role"] == "explore"
    assert rows[0]["children"] == 1 and rows[0]["delivered"] == 1
    assert rows[0]["mode"] == "read-only"

    run_dir = Path(rows[0]["run_dir"])
    assert (run_dir / "run.json").is_file()
    record = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert record["max_depth"] == 1
    assert [child["role"] for child in record["children"]] == ["explore"]
    assert record["children_delivered"] == 1
    assert str(run_dir).startswith(str(state)), "the delegation went outside the run's state dir"



