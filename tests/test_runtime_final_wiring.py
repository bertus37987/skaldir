"""PRODUCTION RUNTIME WIRING (final acceptance) — the hooks that were declared but dead.

Each test here names a control the runtime *claims* to have and proves it is really
reached on the production path (``skaldir run`` / ``skaldir agent run``), not only in a
unit fixture:

1. ``context_manager.CompactingClient`` — the loop writes ``on_delta`` (streaming) and
   ``retry_gate`` (§94 commit point) on its client; a wrapper that swallowed those
   writes left the real provider with neither, silently.
2. ``tool_archive`` — the archived payload must be *retrievable* by the agent that was
   told the ``tool_archive:<sha>`` ref exists.
3. ``subagent_roles`` — the ceiling is over the product's **real** tool vocabulary and a
   child that was not explicitly declared unroled gets a canonical role.
4. ``verify_service.semantic_gate`` — wired into the promotion point of a goal run, and
   never a silent participant: the record always names what the gate did.
5. ``progress_guard`` / retry — one budget, one unit, and a durable stall survives a
   resume.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_agent_loop import StubEndpoint, answer, tool_step

from skaldir.agent_loop import AgentLoop, AgentTool, LoopLimits, ToolSet
from skaldir.context_manager import CompactingClient, ContextManager
from skaldir.llm_provider import provider_for
from skaldir.progress_guard import RetryGate


def _streaming_client(base_url: str):
    """A **real** provider client against the loopback stub, asking for SSE."""
    return provider_for(
        {
            "provider": "openai-compatible",
            "base_url": base_url,
            "model": "stub-runtime-wiring",
            "api_key": "stub-local-key",
            "stream": True,
        }
    )


def _one_tool() -> AgentTool:
    """The loop refuses to run without tools — one harmless, real tool is enough."""

    def look(path: str = ".") -> dict:
        return {"ok": True, "path": path}

    return AgentTool(
        name="look",
        description="look at something",
        handler=look,
        input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
    )


class HookRecorder:
    """The client the loop's two hooks must really land on.

    It behaves like a streaming provider (it calls ``on_delta`` for a delta as it
    arrives) and records, **at call time**, which hooks it was given. That is the
    measurement: a wrapper that forwards ``on_delta``/``retry_gate`` only on *reads*
    leaves this object with ``None`` for both, and the run looks fine.
    """

    def __init__(self, inner) -> None:
        self.inner = inner
        self.seen: dict = {}
        self.calls = 0
        self.on_delta = None

    def chat(self, messages, *, tier="main", tools=None, tool_choice=None, stream=None):
        self.calls += 1
        self.seen = {
            "on_delta": getattr(self, "on_delta", None),
            "retry_gate": getattr(self, "retry_gate", None),
            "stream": stream,
        }
        sink = self.seen["on_delta"]
        if sink is not None:
            sink("(partial) ")
        return self.inner.chat(
            messages, tier=tier, tools=tools, tool_choice=tool_choice, stream=stream
        )


# --------------------------------------------------------------------------- #
# 1 — the compaction wrapper must not swallow the loop's runtime hooks          #
# --------------------------------------------------------------------------- #


def test_the_compaction_wrapper_forwards_the_loops_two_hooks(tmp_path: Path):
    """``on_delta`` and ``retry_gate`` must reach the client that really streams."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    with StubEndpoint([answer("the real answer")]) as endpoint:
        real = _streaming_client(endpoint.base_url)
        recorder = HookRecorder(real)
        wrapper = CompactingClient(recorder, ContextManager())
        gate = RetryGate(max_attempts=2)
        deltas: list[str] = []
        loop = AgentLoop(
            wrapper,
            ToolSet.of([_one_tool()]),
            state_dir=tmp_path / "state",
            workspace=workspace,
            limits=LoopLimits(max_steps=3),
            retry_gate=gate,
            on_delta=deltas.append,
        )
        result = loop.run("answer me")

    assert recorder.seen["on_delta"] is not None, (
        "the content sink never reached the client that streams — the run would "
        "report deltas while the user watched nothing"
    )
    assert recorder.seen["retry_gate"] is gate, (
        "the §94 commit-point gate never reached the provider — the retry guard is dead"
    )
    assert deltas and "(partial)" in "".join(deltas), deltas
    assert result.status == "answered", result.message


def test_the_production_shape_really_streams_through_the_wrapper(tmp_path: Path):
    """A real provider + real SSE behind the wrapper: the deltas reach the renderer."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    with StubEndpoint([answer("streamed through the wrapper")]) as endpoint:
        real = _streaming_client(endpoint.base_url)
        wrapper = CompactingClient(real, ContextManager())
        deltas: list[str] = []
        loop = AgentLoop(
            wrapper,
            ToolSet.of([_one_tool()]),
            state_dir=tmp_path / "state",
            workspace=workspace,
            limits=LoopLimits(max_steps=3),
            on_delta=deltas.append,
        )
        result = loop.run("answer me")

    assert deltas, "no delta reached the renderer through the compaction wrapper"
    assert "".join(deltas).strip(), deltas
    assert result.status == "answered", result.message
    # the wrapper's own account of where the hooks landed
    hooks = wrapper.hook_report()
    assert hooks["forwarded"] == ["on_delta", "retry_gate"]
    assert hooks["client"] == type(real).__name__


# --------------------------------------------------------------------------- #
# 2 — the archive read half: a ref the agent can really resolve                 #
# --------------------------------------------------------------------------- #


def test_an_archived_tool_result_can_really_be_read_back(tmp_path: Path):
    """The hot form names ``tool_archive:<sha>``; the agent must be able to ask for it."""
    from skaldir.tool_archive import RETRIEVE_TOOL_NAME, ToolArchive

    workspace = tmp_path / "ws"
    workspace.mkdir()
    body = "x" * 40000

    def dump_dataset() -> dict:
        return {"dump": body}

    tool = AgentTool(
        name="dump_dataset",
        description="dump a big dataset",
        handler=dump_dataset,
        input_schema={"type": "object", "properties": {}},
    )
    archive = ToolArchive(tmp_path / "state")

    # --- run 1: the big result is archived, not inlined ---------------------
    with StubEndpoint([tool_step(("c1", "dump_dataset", {})), answer("dumped")]) as endpoint:
        client = provider_for(
            {
                "provider": "openai-compatible",
                "base_url": endpoint.base_url,
                "model": "stub-archive",
                "api_key": "stub-local-key",
            }
        )
        loop = AgentLoop(
            client,
            ToolSet.of([tool]),
            state_dir=tmp_path / "state",
            workspace=workspace,
            limits=LoopLimits(max_steps=4),
            archive=archive,
        )
        loop.run("dump the dataset")
        hot = json.dumps(endpoint.requests[-1]["body"].get("messages") or [])

    assert archive.learned, "the oversized result was not archived at all"
    entry = archive.learned[0]
    ref = entry["ref"]
    artifact_bytes = Path(entry["path"]).read_bytes()
    assert entry["chars"] > 6000
    assert f"tool_archive:{entry['sha256']}" in hot, "the hot context never named the ref"
    assert body not in hot, "the whole payload was still inlined — the archive saved nothing"

    # --- run 2: the model reads the ref back through the offered tool -------
    with StubEndpoint(
        [tool_step(("c2", RETRIEVE_TOOL_NAME, {"ref": ref})), answer("read it back")]
    ) as endpoint:
        client = provider_for(
            {
                "provider": "openai-compatible",
                "base_url": endpoint.base_url,
                "model": "stub-archive",
                "api_key": "stub-local-key",
            }
        )
        loop = AgentLoop(
            client,
            ToolSet.of([tool]),
            state_dir=tmp_path / "state",
            workspace=workspace,
            limits=LoopLimits(max_steps=4),
            max_result_chars=90000,
            archive=archive,
        )
        assert RETRIEVE_TOOL_NAME in loop.tools.names(), (
            "the loop did not offer the read half of the archive"
        )
        result = loop.run("read the archived dump back")
        messages = endpoint.requests[-1]["body"].get("messages") or []
    last_tool = [item for item in messages if item.get("role") == "tool"][-1]["content"]

    assert result.status == "answered", result.message
    # the payload itself came back, not a summary of it
    assert body[:2000] in last_tool, last_tool[:400]
    # bounded and honest about it, and NOT re-archived into the ref the model asked for
    assert "truncated" in last_tool
    assert "archived" not in last_tool, (
        "the retrieval was archived again — the model would get the same ref back"
    )
    assert artifact_bytes, "the artifact the ref names is the one that was read"

    # a ref the archive cannot resolve is a reported error, never invented content
    with StubEndpoint(
        [tool_step(("c3", RETRIEVE_TOOL_NAME, {"ref": "tool_archive:deadbeef"})), answer("tried")]
    ) as endpoint:
        client = provider_for(
            {
                "provider": "openai-compatible",
                "base_url": endpoint.base_url,
                "model": "stub-archive",
                "api_key": "stub-local-key",
            }
        )
        loop = AgentLoop(
            client,
            ToolSet.of([tool]),
            state_dir=tmp_path / "state",
            workspace=workspace,
            limits=LoopLimits(max_steps=4),
            archive=archive,
        )
        loop.run("read a ref that does not exist")
        refused = json.dumps(endpoint.requests[-1]["body"].get("messages") or [])
    assert "ArchiveError" in refused


# --------------------------------------------------------------------------- #
# 3 — the semantic gate on the promotion path (never silent, never a promotion) #
# --------------------------------------------------------------------------- #


def _green_goal_project(tmp_path: Path):
    """A workspace whose three declared checks really pass (the achievable fixture)."""
    from test_goal_run_cli import FIXED_SLUG, GOAL, _workspace, _write_criteria

    project = _workspace(tmp_path, "achievable")
    (project / "textkit" / "slug.py").write_text(FIXED_SLUG, encoding="utf-8")
    return project, _write_criteria(project), GOAL


def _goal_options(project: Path, state: Path, criteria: Path, goal: str, **extra):
    from skaldir import goal_run

    return goal_run.GoalRunOptions(
        goal=goal,
        workspace=str(project),
        state_dir=str(state),
        session="goal-run-semantic",
        criteria=goal_run.load_criteria_file(criteria),
        max_turns=1,
        max_steps=6,
        check_timeout=120.0,
        **extra,
    )


def _goal_client(base_url: str):
    return provider_for(
        {
            "provider": "openai-compatible",
            "base_url": base_url,
            "model": "stub-semantic",
            "api_key": "stub-local-key",
        }
    )


def _live_verdict(support: str):
    """A **live** Jev semantic-verification decision, in the shape the gate reads."""
    from types import SimpleNamespace

    answer = SimpleNamespace(
        support=support,
        reasons=("the brief does not show the criterion the claim rests on",),
        evidence_gaps=("no executed check for the packaged artifact",),
        unverifiable=(),
        checks_run=(),
        provider_choice="deepseek-reasoner",
    )
    return SimpleNamespace(
        decision_id="jev-semantic-1",
        status="ok",
        provider_mode="deepseek",
        authority="advisory",
        certifies=False,
        live_jev=True,
        confidence=0.7,
        question_pack_id="semantic_verification",
        question_pack_version="semantic_verification_v1",
        answer=answer,
        refusal_reason="",
    )


def test_a_green_goal_is_not_promoted_when_a_live_verifier_refuses_the_claim(
    tmp_path: Path, monkeypatch
):
    """The deterministic PASS stays; the *promotion* does not happen."""
    from skaldir import goal_run, jev

    project, criteria, goal = _green_goal_project(tmp_path)
    state = tmp_path / "state"
    monkeypatch.setattr(jev, "decide", lambda *a, **kw: _live_verdict("unsupported"))

    with StubEndpoint([answer("All three criteria pass; completion requested.")]) as stub:
        report = goal_run.run_goal(
            _goal_options(project, state, criteria, goal),
            client=_goal_client(stub.base_url),
        )

    assert report["verify"]["verdict"] == "PASS", "the deterministic verdict was weakened"
    assert report["exit_code"] == goal_run.EXIT_BLOCKED
    assert report["terminal_state"] == "BLOCKED_EXTERNAL"

    gate = report["semantic_gate"]
    assert gate["state"] == "refused"
    assert gate["promotion"] == "blocked" and gate["blocking"] is True
    assert gate["deterministic_verdict"] == "PASS"
    assert gate["verdict_after_gate"] == "PASS", "the gate replaced the verdict"
    assert gate["certifies"] is False
    # the gate ran with a real, read-only verifier role - not a fixture dispatcher
    assert gate["verifier"]["role"] == "verifier"
    assert gate["verifier"]["read_only"] is True
    assert gate["verifier"]["source"] == "skaldir.subagent_roles.verifier_dispatcher()"
    assert "terminal" not in gate["verifier"]["tools_offered"]
    # the reason travels, and the goal is not closed
    assert report["blocked_reason"]
    assert "semantic gate blocked" in str(report["blocked_reason"])
    assert report["verify"]["semantic_gate"] is gate


def test_a_green_goal_closes_but_the_record_names_an_unread_claim(tmp_path: Path):
    """No live verifier ⇒ the deterministic PASS stands *and the record says so*."""
    from skaldir import goal_run

    project, criteria, goal = _green_goal_project(tmp_path)
    state = tmp_path / "state"

    with StubEndpoint([answer("All three criteria pass; completion requested.")]) as stub:
        report = goal_run.run_goal(
            _goal_options(project, state, criteria, goal),
            client=_goal_client(stub.base_url),
        )

    assert report["exit_code"] == goal_run.EXIT_OK
    assert report["terminal_state"] == "VERIFIED_COMPLETE"
    gate = report["semantic_gate"]
    assert gate["state"] == "no_advisory"
    assert gate["blocking"] is False
    assert gate["authority"] == "advisory" and gate["certifies"] is False
    assert "not to be read as having been checked semantically" in gate["promotion_reason"]
    assert gate["live_jev"] is False


def test_the_disabled_gate_is_named_and_never_silently_absent(tmp_path: Path):
    """Turning the gate off is allowed — pretending it ran is not."""
    from skaldir import goal_run

    project, criteria, goal = _green_goal_project(tmp_path)
    state = tmp_path / "state"

    with StubEndpoint([answer("All three criteria pass; completion requested.")]) as stub:
        report = goal_run.run_goal(
            _goal_options(project, state, criteria, goal, semantic_gate=False),
            client=_goal_client(stub.base_url),
        )

    assert report["exit_code"] == goal_run.EXIT_OK
    gate = report["semantic_gate"]
    assert gate["state"] == "skipped" and gate["disabled"] is True
    assert "turned off" in gate["promotion_reason"]
    assert gate["certifies"] is False and gate["verifier"]["role"] == ""


# --------------------------------------------------------------------------- #
# 4 — one retry budget, one unit, one decision                                   #
# --------------------------------------------------------------------------- #


def test_one_provider_failure_leaves_one_retry_decision_not_two(tmp_path: Path):
    """The loop must not re-record the provider's refusal as an allowance."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    client = provider_for(
        {
            "provider": "openai-compatible",
            "base_url": "http://127.0.0.1:9/v1",  # nothing listens: a real failure
            "model": "stub-unreachable",
            "api_key": "stub-local-key",
            "max_retries": 1,
            "timeout_s": 1.0,
        }
    )
    gate = RetryGate(max_attempts=1)
    loop = AgentLoop(
        client,
        ToolSet.of([_one_tool()]),
        state_dir=tmp_path / "state",
        workspace=workspace,
        limits=LoopLimits(max_steps=2),
        retry_gate=gate,
    )
    result = loop.run("do the thing")

    assert result.status == "failed", result.message
    # exactly one decision for one failure — never "refused" and "allowed" for the same call
    assert len(result.retry) == 1, result.retry
    recorded = result.retry[-1]
    assert recorded["retry"] is False
    assert recorded["action"] == "refuse"
    assert "attempt loop already ended" in recorded["reason"]
    # the gate's own counters agree with what happened (1 retry allowed, 1 refusal)
    assert gate.retries == 1, gate.to_dict()
    assert gate.refusals == 1, gate.to_dict()


def test_the_two_retry_budgets_are_reconciled_and_the_gate_only_narrows():
    """§43 (provider) vs ``max_provider_retries`` (runtime): one stated outcome."""
    narrower = RetryGate(max_attempts=1).effective_attempts(3)
    assert narrower["effective_attempts"] == 2
    assert narrower["narrowed_by"] == "the runtime's retry gate"

    provider_bound = RetryGate(max_attempts=9).effective_attempts(2)
    assert provider_bound["effective_attempts"] == 2
    assert provider_bound["narrowed_by"] == "the provider's §43 policy"

    # the gate can never widen what the provider's own policy allows
    assert RetryGate(max_attempts=99).effective_attempts(3)["effective_attempts"] == 3


# --------------------------------------------------------------------------- #
# 5 — a durable stall survives the process (PERSIST)                             #
# --------------------------------------------------------------------------- #


def test_a_resumed_run_knows_it_was_already_told_to_change_strategy(tmp_path: Path):
    from skaldir.progress_guard import (
        ACTION_CONTINUE,
        ACTION_STOP,
        ACTION_STRATEGY_CHANGE,
        ProgressGuard,
        TurnObservation,
    )
    from skaldir.runs_store import RunsStore

    store = RunsStore(str(tmp_path / "runs.db"))
    try:
        supervisor = store.supervisor()
        assert supervisor is not None
        run = supervisor.create_run("s-resume", state="ACTIVE")

        guard = ProgressGuard()
        first = guard.observe(TurnObservation(turn=1, answer_text="nothing changed"))
        assert first.action == ACTION_CONTINUE
        second = guard.observe(TurnObservation(turn=2, answer_text="nothing changed"))
        assert second.action == ACTION_STRATEGY_CHANGE, second.reason
        assert store.record_progress(run.run_id, decision=second) is True

        # a NEW process reads the durable signals back
        durable = store.progress(run.run_id)
        assert durable is not None and durable.turn == 2
        fresh = ProgressGuard()
        payload = fresh.restore(durable)
        assert payload["restored"] is True
        assert payload["turn"] == 2 and payload["no_progress_streak"] >= 1
        assert payload["demands_restored"] >= 1

        # the identical answer again STOPS instead of being nudged a second time...
        again = fresh.observe(TurnObservation(turn=3, answer_text="Nothing   changed "))
        assert again.action == ACTION_STOP, again.reason
        # ...and a turn that really did something still clears the stall
        fresh2 = ProgressGuard()
        fresh2.restore(durable)
        moved = fresh2.observe(
            TurnObservation(turn=3, answer_text="a different answer", tool_calls=("read_file",))
        )
        assert moved.action == ACTION_CONTINUE
    finally:
        store.close()


def test_the_goal_run_reads_the_durable_progress_back(tmp_path: Path):
    """The restore is on the production path and the report says what it read."""
    from skaldir import goal_run

    project, criteria, goal = _green_goal_project(tmp_path)
    state = tmp_path / "state"

    with StubEndpoint([answer("All three criteria pass; completion requested.")]) as stub:
        report = goal_run.run_goal(
            _goal_options(project, state, criteria, goal),
            client=_goal_client(stub.base_url),
        )

    restored = report["progress"]["restored"]
    assert restored, "the run must report what it read back from its own record"
    assert restored.get("restored") is False
    assert "no turn yet" in restored.get("reason", "")
    # the two retry budgets are on the record, with the unit stated
    budgets = report["retry"]["budgets"]
    assert budgets["unit"].startswith("attempts made on one step")
    assert budgets["effective_attempts"] >= 1
    assert budgets["commit_point_blocks_every_retry"] is True


# --------------------------------------------------------------------------- #
# 6 — a declared check that is a COMMAND VECTOR is run, not renamed              #
# --------------------------------------------------------------------------- #


def test_a_criteria_document_may_declare_a_check_as_a_command_vector(tmp_path: Path):
    """The blocker the interrupted long run really hit (see the audit report).

    ``spec/criteria.json`` declares ``"check": ["<python>", "spec/check_api.py"]``. The
    reader ran ``str()`` over that vector, so the check id became the *list repr* with an
    empty argv: five criteria came back ``UNKNOWN`` with ``duration_ms: 0`` and the run
    never got past its baseline. A vector must be a vector, and it must really run.
    """
    import sys

    from skaldir import goal_run

    project, criteria, goal = _green_goal_project(tmp_path)
    state = tmp_path / "state"
    document = tmp_path / "criteria.json"
    document.write_text(
        json.dumps(
            [
                {
                    "id": "c1",
                    "criterion": "the declared command really runs and passes",
                    "check": [sys.executable, "-c", "print('declared check ran')"],
                },
                {
                    "id": "c2",
                    "criterion": "the declared command really runs and is honest about failing",
                    # NB: the command prints before failing on purpose — a *silent*
                    # non-0/1 exit is currently misreported by the sandbox wrapper as
                    # "sandbox unavailable" (see the audit report and
                    # evidence/12-runtime-final/silent-exit-misreported.txt)
                    "check": [sys.executable, "-c", "print('not done'); raise SystemExit(3)"],
                    "blocking": True,
                },
            ]
        ),
        encoding="utf-8",
    )

    options = _goal_options(project, state, document, goal)
    with StubEndpoint([answer("Completion requested.")]) as stub:
        report = goal_run.run_goal(options, client=_goal_client(stub.base_url))

    planned = report["plan"]["criteria"]
    assert [item["kind"] for item in planned] == ["command", "command"], planned
    for item in planned:
        assert item["argv"], "the declared command vector was lost"
        assert "[" not in item["check_id"], item["check_id"]

    rows = {row["criterion_id"]: row for row in report["verify"]["criteria"]}
    # the checks were EXECUTED (not "unknown" with a null exit code)
    assert rows["c1"]["exit_code"] == 0
    assert rows["c1"]["status"] == "passed"
    assert rows["c2"]["exit_code"] == 3
    assert rows["c2"]["status"] != "passed"
    assert report["verify"]["counts"]["passed"] == 1
    # one blocking criterion genuinely failed ⇒ the goal does not close (the product's
    # pinned mapping: a red workspace parks the goal BLOCKED_EXTERNAL and exits 3, and
    # never VERIFIED_COMPLETE)
    assert report["verify"]["verdict"] == "FAIL"
    assert report["terminal_state"] == "BLOCKED_EXTERNAL"
    assert report["exit_code"] == goal_run.EXIT_BLOCKED
    assert "c2" in str(report.get("blocked_criteria") or "") or report["blocked_reason"]


def test_a_declared_command_vector_that_passes_closes_the_goal(tmp_path: Path):
    import sys

    from skaldir import goal_run

    project, criteria, goal = _green_goal_project(tmp_path)
    state = tmp_path / "state"
    document = tmp_path / "criteria.json"
    document.write_text(
        json.dumps(
            [
                {
                    "id": "c1",
                    "criterion": "the declared command runs and passes",
                    "check": [sys.executable, "-c", "print('ok')"],
                }
            ]
        ),
        encoding="utf-8",
    )
    options = _goal_options(project, state, document, goal)
    with StubEndpoint([answer("Completion requested.")]) as stub:
        report = goal_run.run_goal(options, client=_goal_client(stub.base_url))

    assert report["verify"]["verdict"] == "PASS", report["verify"]["verdict_reason"]
    assert report["exit_code"] == goal_run.EXIT_OK
    assert report["terminal_state"] == "VERIFIED_COMPLETE"


def test_an_empty_declared_command_is_refused_instead_of_renamed(tmp_path: Path):
    from skaldir import goal_run

    with pytest.raises(goal_run.GoalRunError) as excinfo:
        goal_run.normalise_criteria([{"id": "c1", "text": "x", "check": []}])
    assert "empty check command" in str(excinfo.value)

    normalised = goal_run.normalise_criteria(
        [{"id": "c1", "text": "x", "check": ["python", "spec/check_api.py"]}]
    )[0]
    assert normalised["check"] == "command"
    assert normalised["check_argv"] == ["python", "spec/check_api.py"]


# --------------------------------------------------------------------------- #
# 7 — the durable run record is kept as current as the checkpoint beside it      #
# --------------------------------------------------------------------------- #


def test_a_run_that_ran_a_turn_says_so_in_its_run_record(tmp_path: Path):
    """The recovered long run left ``turn: 0`` + a 5-minute-stale heartbeat.

    A supervisor reading that row cannot tell "nothing started" from "a turn ran and the
    provider failed"; a resumed process reads the same row first.
    """
    from skaldir import goal_run
    from skaldir.runs_store import RunsStore

    project, criteria, goal = _green_goal_project(tmp_path)
    state = tmp_path / "state"

    with StubEndpoint([answer("All three criteria pass; completion requested.")]) as stub:
        report = goal_run.run_goal(
            _goal_options(project, state, criteria, goal),
            client=_goal_client(stub.base_url),
        )

    touches = report["run_record_touches"]
    assert touches and touches[0]["updated"] is True, touches

    store = RunsStore(str(state / "runs.db"), create=False)
    try:
        supervisor = store.supervisor()
        record = supervisor.get(report["run_record_id"]).to_dict()
    finally:
        store.close()

    assert int(record["turn"]) >= 1, record  # the recovered run said 0 after a real turn
    assert float(record["heartbeat_at"]) > 0
    assert str(record["last_verification"]) == "answered"
