"""The subagent harness core: parallelism, isolation, budgets, cancel and timing.

Every test runs the harness against the deterministic offline provider
(:class:`skaldir.subagents.StubProvider`) — no model, no network — and measures
what the harness itself does: which thread each child ran on, what its context
contained, when its result landed and which stop condition fired.
"""

from __future__ import annotations

import builtins
import json
import os
import select
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from skaldir import subagents as S


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.delenv("SKALDIR_SUBAGENTS_DIR", raising=False)
    monkeypatch.delenv("GODSV_SUBAGENTS_DIR", raising=False)
    return tmp_path / "subagents"


def _spawn(tasks, state, *, provider=None, **kwargs):
    provider = provider or S.StubProvider(latency_s=0.05)
    return S.spawn(
        tasks,
        provider=provider,
        state_dir=state,
        provider_summary={"mode": "stub"},
        **kwargs,
    )


class _Recorder:
    """Wraps a provider and keeps every prompt a child actually sent."""

    def __init__(self, inner):
        self.inner = inner
        self.prompts: list[tuple[str, str]] = []

    def complete(self, *, prompt_id: str, prompt: str, tier: str) -> str:
        self.prompts.append((prompt_id, prompt))
        return self.inner.complete(prompt_id=prompt_id, prompt=prompt, tier=tier)


# --------------------------------------------------------------------------- #
# parallelism, isolation, streaming
# --------------------------------------------------------------------------- #


def test_three_children_run_in_parallel_and_each_streams_its_own_result(state):
    provider = S.StubProvider(latency_s=0.06, default_turns=2)
    record = _spawn(["alpha", "beta", "gamma"], state, provider=provider, parallel=0)

    assert [child.status for child in record.children] == [S.STATUS_DELIVERED] * 3
    assert record.status == S.RUN_COMPLETE
    assert record.partial is False

    # Parallel, not serial: the wall clock is far below the sum of the children.
    wall_ms = record.duration_ms
    serial_ms = sum(child.duration_ms for child in record.children)
    assert wall_ms < serial_ms * 0.6, (wall_ms, serial_ms)

    # One thread per child — this is the "in one process" claim.
    threads = {call["thread"] for call in provider.calls}
    assert len(threads) == 3
    assert all(name.startswith("subagent-") for name in threads)

    # Each child owned its own session id and prompt id.
    sessions = {child.context["session_id"] for child in record.children}
    assert sessions == {f"{record.run_id}:c1", f"{record.run_id}:c2", f"{record.run_id}:c3"}
    prompt_ids = [call["prompt_id"] for call in provider.calls]
    assert len(set(prompt_ids)) == len(prompt_ids)
    assert all(pid.startswith(f"subagent.{record.run_id}.") for pid in prompt_ids)


def test_results_stream_instead_of_arriving_together(state):
    """The first child's result is written before the last child is finished."""
    store = S.RunStore(state)
    run = S.SubagentRun(
        provider=S.StubProvider(latency_s=0.12, default_turns=1),
        briefs=S.briefs_from_tasks(["fast", "slow [[latency:0.5]]"]),
        state_dir=state,
        run_id="streaming",
        provider_summary={"mode": "stub"},
    )
    thread = threading.Thread(target=run.run, daemon=True)
    thread.start()
    seen: dict = {}
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        record = store.load("streaming")
        children = {child["child_id"]: child for child in (record or {}).get("children", [])}
        if children.get("c1", {}).get("finished_at"):
            seen = children
            break
        time.sleep(0.01)
    thread.join(timeout=10)
    assert seen, "the first child's result never appeared in the run record"
    # c1 was already finished while c2 was still running: results streamed.
    assert seen["c1"]["status"] == S.STATUS_DELIVERED
    assert seen["c2"]["status"] in (S.STATUS_RUNNING, S.STATUS_QUEUED)
    assert store.events("streaming", child_id="c1"), "no streamed events for c1"


def test_a_child_only_ever_sees_its_own_context(state):
    recorder = _Recorder(S.StubProvider(latency_s=0.0, default_turns=1))
    record = _spawn(
        ["ALPHA-TASK-ONLY", "BETA-TASK-ONLY"], state, provider=recorder, parallel=2
    )
    assert [child.status for child in record.children] == [S.STATUS_DELIVERED] * 2
    for prompt_id, prompt in recorder.prompts:
        child = prompt_id.split(".")[2]
        own, other = ("ALPHA-TASK-ONLY", "BETA-TASK-ONLY")
        if child == "c2":
            own, other = other, own
        assert own in prompt
        assert other not in prompt, f"{child} saw its sibling's task"
        assert f"Session id: {record.run_id}:{child}" in prompt


def test_each_child_gets_its_own_working_directory_and_transcript(state):
    record = _spawn(["a", "b"], state)
    dirs = set()
    for child in record.children:
        context = child.context
        workdir = Path(context["workdir"])
        assert workdir.is_dir()
        assert workdir.name == child.child_id
        dirs.add(str(workdir))
        assert json.loads((workdir / ".subagent.json").read_text())["session_id"] == (
            context["session_id"]
        )
        turns = [
            json.loads(line)
            for line in Path(context["transcript"]).read_text().splitlines()
            if line.strip()
        ]
        assert turns, "the child's own transcript is empty"
        assert all(turn["session_id"] == context["session_id"] for turn in turns)
    assert len(dirs) == 2


def test_no_import_happens_while_the_children_run(state):
    """Nothing is re-imported per child — the import graph is paid once."""
    S.spawn(
        ["warm"],
        provider=S.StubProvider(latency_s=0.0, default_turns=1),
        state_dir=state,
        run_id="warmup",
        provider_summary={"mode": "stub"},
    )
    real_import = builtins.__import__
    seen: list[str] = []

    def counting(name, *args, **kwargs):
        seen.append(str(name))
        return real_import(name, *args, **kwargs)

    builtins.__import__ = counting
    try:
        record = _spawn(["a", "b", "c [[fail]]"], state / "second")
    finally:
        builtins.__import__ = real_import
    assert record.children[2].status == S.STATUS_FAILED  # the failure path ran too
    assert seen == [], f"modules imported while the children ran: {sorted(set(seen))}"


# --------------------------------------------------------------------------- #
# per-child budget / turns / deadline
# --------------------------------------------------------------------------- #


def test_a_child_stops_before_it_overspends_its_own_budget(state):
    # role=unroled on purpose: this test measures the *token* guard against the same
    # prompt the pre-role harness sent. A role-bearing child carries its role and tool
    # ceiling in the prompt, so the same 200-token budget is spent by the prompt itself.
    record = _spawn(
        ["keep going [[infinite]]"],
        state,
        budget_tokens=200,
        max_turns=5,
        role=S.UNROLED,
    )
    child = record.children[0]
    assert child.status == S.STATUS_BUDGET_EXHAUSTED
    assert child.spent_tokens <= 200 + 200  # guard, not accounting: it never sent turn 3
    assert child.turns == 1
    assert "token budget" in (child.stop_reason or "")


def test_max_turns_is_a_hard_cap(state):
    record = _spawn(["never finish [[infinite]]"], state, max_turns=2)
    child = record.children[0]
    assert child.status == S.STATUS_MAX_TURNS
    assert child.turns == 2
    assert "turn budget" in (child.stop_reason or "")


def test_the_run_budget_stops_the_whole_family(state):
    record = _spawn(
        ["a [[infinite]]", "b [[infinite]]"],
        state,
        provider=S.StubProvider(latency_s=0.02),
        run_budget_tokens=150,
        max_turns=50,
    )
    assert record.status == S.RUN_CANCELLED
    assert "run budget exhausted" in record.cancel_reason
    assert [child.status for child in record.children] == [S.STATUS_CANCELLED] * 2


def test_a_deadline_marks_the_child_timed_out_without_lying(state):
    started = time.monotonic()
    record = _run_with_timeout(state)
    elapsed = time.monotonic() - started
    child = record.children[0]
    assert child.status == S.STATUS_TIMED_OUT
    assert child.thread_alive is True
    assert "cannot be interrupted" in (child.stop_reason or "")
    assert elapsed < 5.0
    assert any("still alive" in note for note in record.notes)


def _run_with_timeout(state):
    return _spawn(
        ["never finish [[infinite]]"],
        state,
        provider=S.StubProvider(latency_s=0.4),
        timeout_s=0.2,
        max_turns=2,
    )


# --------------------------------------------------------------------------- #
# cancel (in-process + the other-process path)
# --------------------------------------------------------------------------- #


def test_cancelling_a_live_run_stops_its_children(state):
    store = S.RunStore(state)
    run = S.SubagentRun(
        provider=S.StubProvider(latency_s=0.1),
        briefs=S.briefs_from_tasks(["a [[infinite]]", "b [[infinite]]"], max_turns=100),
        state_dir=state,
        run_id="cancel-live",
        provider_summary={"mode": "stub"},
    )
    thread = threading.Thread(target=run.run, daemon=True)
    thread.start()
    _wait_until(lambda: (store.load("cancel-live") or {}).get("status") == S.RUN_RUNNING)
    outcome = store.request_cancel("cancel-live", reason="proof", by="test")
    assert outcome.action == "signalled"
    assert outcome.pid_alive is True
    thread.join(timeout=15)
    record = store.load("cancel-live")
    assert record["status"] == S.RUN_CANCELLED
    assert record["cancel_reason"] == "proof"
    assert [child["status"] for child in record["children"]] == [S.STATUS_CANCELLED] * 2
    # The request is durable evidence, not just an in-memory flag.
    request = json.loads((store.run_dir("cancel-live") / S.CANCEL_FILE).read_text())
    assert request["reason"] == "proof"
    assert request["run_id"] == "cancel-live"


def test_cancelling_a_finished_run_is_a_noop(state):
    record = _spawn(["done"], state)
    outcome = S.RunStore(state).request_cancel(record.run_id, reason="too late")
    assert outcome.action == "noop"
    assert outcome.status == S.RUN_COMPLETE
    assert S.RunStore(state).load(record.run_id)["status"] == S.RUN_COMPLETE


def test_cancelling_a_run_whose_process_is_gone_reconciles_instead_of_faking(state):
    """A dead pid cannot be cancelled: the record says so instead of pretending."""
    store = S.RunStore(state / "dead")
    run_dir = store.run_dir("deadrun")
    run_dir.mkdir(parents=True)
    dead = subprocess.Popen([sys.executable, "-c", ""])
    dead.wait()
    (run_dir / S.RUN_FILE).write_text(
        json.dumps(
            {
                "schema_version": S.SCHEMA_VERSION,
                "run_id": "deadrun",
                "status": S.RUN_RUNNING,
                "pid": dead.pid,
                "children": [],
                "started_at": "2026-09-18T00:00:00.000+00:00",
            }
        )
    )
    outcome = store.request_cancel("deadrun", reason="owner went away")
    assert outcome.action == "reconciled"
    assert outcome.pid_alive is False
    rewritten = store.load("deadrun")
    assert rewritten["status"] == S.RUN_ABORTED
    assert "pid" in rewritten["cancel_reason"]


def test_an_unknown_run_id_is_reported_as_unknown(state):
    outcome = S.RunStore(state).request_cancel("nope")
    assert outcome.action == "noop"
    assert outcome.status == "unknown"
    assert S.RunStore(state).load("nope") is None


def test_a_run_id_cannot_escape_the_state_directory(state):
    with pytest.raises(S.SubagentError):
        S.briefs_from_tasks(["x"], child_ids=["../../etc"])
    with pytest.raises(S.SubagentError):
        _spawn(["x"], state, run_id="../escape")
    with pytest.raises(S.SubagentError):
        S.RunStore(state).load("../../etc/passwd")


# --------------------------------------------------------------------------- #
# store / timing / provider edge / validation
# --------------------------------------------------------------------------- #


def test_the_store_lists_newest_first_and_reads_one_run_back(state):
    _spawn(["one"], state, run_id="older")
    time.sleep(0.01)
    _spawn(["two"], state, run_id="newer")
    store = S.RunStore(state)
    assert [item["run_id"] for item in store.list()] == ["newer", "older"]
    assert store.list(limit=1)[0]["run_id"] == "newer"
    assert store.list(status=S.RUN_COMPLETE) != []
    assert store.list(status="no-such-status") == []
    assert store.list(limit=0) == []
    assert store.load("older")["children_total"] == 1
    assert store.events("older")[0]["kind"] == "run_created"


def test_percentile_is_interpolated(state):
    assert S.percentile([], 0.5) == 0.0
    assert S.percentile([7.0], 0.95) == 7.0
    assert S.percentile([1.0, 2.0, 3.0, 4.0], 0.5) == 2.5
    assert S.percentile([1.0, 2.0, 3.0, 4.0], 0.0) == 1.0
    assert S.percentile([1.0, 2.0, 3.0, 4.0], 1.0) == 4.0
    assert S.percentile([1.0, 2.0, 3.0, 4.0], 0.95) == pytest.approx(3.85)


def test_the_timing_summary_reports_spawn_latency_and_overhead(state):
    record = _spawn(["a", "b", "c"], state, provider=S.StubProvider(latency_s=0.03))
    timing = record.timing
    assert timing["spawn_samples"] == 3
    assert timing["spawn_p50_ms"] > 0.0
    assert timing["spawn_p95_ms"] >= timing["spawn_p50_ms"]
    assert timing["overhead_p50_ms"] >= 0.0
    assert timing["overhead_p50_ms"] < 500.0, "per-child harness overhead is not 0.5 s"
    assert timing["provider_total_ms"] > 0.0
    assert timing["first_result_ms"] <= timing["last_result_ms"]
    assert timing["run_total_ms"] <= timing["children_total_ms"] + 50.0


def test_the_stub_is_labelled_and_a_missing_real_provider_blocks(
    state, tmp_path, monkeypatch
):
    stub = S.open_provider("stub")
    assert stub.mode == "stub"
    assert "no model was called" in stub.note
    assert stub.summary()["mode"] == "stub"

    # Both spellings: a real shell here carries the legacy `GODSV_LLM_*` names.
    for name in (
        "SKALDIR_LLM_BASE_URL",
        "GODSV_LLM_BASE_URL",
        "SKALDIR_LLM_API_BASE",
        "GODSV_LLM_API_BASE",
        "OPENAI_BASE_URL",
        "SKALDIR_LLM_MODEL",
        "GODSV_LLM_MODEL",
        "OPENAI_MODEL",
        "SKALDIR_LLM_API_KEY",
        "GODSV_LLM_API_KEY",
        "SKALDIR_LLM_KEY",
        "GODSV_LLM_KEY",
        "OPENAI_API_KEY",
        "SKALDIR_LLM_PROVIDER",
        "GODSV_LLM_PROVIDER",
        "SKALDIR_LLM_CONFIG",
        "GODSV_LLM_CONFIG",
        "SKALDIR_HOME",
        "GODSV_HOME",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    monkeypatch.setenv("SKALDIR_LLM_CONFIG", str(tmp_path / "absent.yaml"))
    with pytest.raises(S.ProviderBlocked) as excinfo:
        S.open_provider("auto")
    assert "SKALDIR_LLM" in str(excinfo.value)
    with pytest.raises(S.SubagentError):
        S.open_provider("nonsense")


def test_an_invalid_brief_is_refused_before_anything_runs(state):
    provider = S.StubProvider(latency_s=0.0)
    with pytest.raises(S.SubagentError):
        _spawn([], state, provider=provider)
    with pytest.raises(S.SubagentError):
        _spawn(["   "], state, provider=provider)
    with pytest.raises(S.SubagentError):
        _spawn(["x"], state, provider=provider, tier="huge")
    with pytest.raises(S.SubagentError):
        S.briefs_from_tasks(["a", "b"], child_ids=["only-one"])
    with pytest.raises(S.SubagentError):
        S.briefs_from_tasks(["a"], tier="main", max_turns=0)
    with pytest.raises(S.SubagentError):
        S.briefs_from_tasks(["a"], budget_tokens=0)
    with pytest.raises(S.SubagentError):
        S.briefs_from_tasks(["a"], timeout_s=0)


def test_a_run_refuses_to_start_when_its_state_directory_is_unwritable(state):
    if os.geteuid() == 0:
        pytest.skip("root ignores the directory mode")
    blocked = state / "readonly"
    blocked.mkdir(parents=True)
    blocked.chmod(0o500)
    try:
        with pytest.raises(OSError):
            _spawn(["a"], blocked)
    finally:
        blocked.chmod(0o700)


def test_a_second_run_with_the_same_id_is_refused(state):
    _spawn(["a"], state, run_id="duplicate")
    with pytest.raises(S.SubagentError):
        _spawn(["b"], state, run_id="duplicate")


def test_the_collect_payload_gives_one_structured_result_per_child(state):
    record = _spawn(["a", "b [[fail]]"], state)
    payload = S.collect_payload(record.as_dict())
    assert payload["delivered"] == 1
    assert payload["partial"] is True
    assert [item["child_id"] for item in payload["results"]] == ["c1", "c2"]
    assert payload["results"][0]["ok"] is True
    assert payload["results"][1]["ok"] is False
    assert payload["results"][0]["session_id"] == f"{record.run_id}:c1"
    only = S.collect_payload(record.as_dict(), child_id="c2")
    assert len(only["results"]) == 1
    assert only["delivered"] == 0


# --------------------------------------------------------------------------- #
# the queue: a configurable parallel cap with the rest of the family queued
# --------------------------------------------------------------------------- #


class _Concurrency:
    """Wraps a provider and keeps the peak number of simultaneous calls."""

    def __init__(self, inner):
        self.inner = inner
        self.live = 0
        self.peak = 0
        self._guard = threading.Lock()

    def complete(self, *, prompt_id: str, prompt: str, tier: str) -> str:
        with self._guard:
            self.live += 1
            self.peak = max(self.peak, self.live)
        try:
            return self.inner.complete(prompt_id=prompt_id, prompt=prompt, tier=tier)
        finally:
            with self._guard:
                self.live -= 1


def test_the_parallel_cap_queues_the_rest_and_is_never_exceeded(state):
    counted = _Concurrency(S.StubProvider(latency_s=0.15, default_turns=1))
    record = _spawn(["a", "b", "c", "d"], state, provider=counted, parallel=2, run_id="queued")

    assert [child.status for child in record.children] == [S.STATUS_DELIVERED] * 4
    assert counted.peak == 2, f"{counted.peak} children were running at once (cap 2)"
    assert record.queue_max == 2
    assert record.timing["parallel"] == 2
    assert record.timing["queue_max_concurrent"] == 2

    waited = sorted(child.child_id for child in record.children if child.queued)
    assert waited == ["c3", "c4"], waited
    assert sorted(record.timing["queue_waited_children"]) == waited
    assert all(child.queued_ms > 0.0 for child in record.children if child.queued)
    assert record.timing["queue_wait_p95_ms"] >= record.timing["queue_wait_p50_ms"] > 0.0
    assert record.timing["queue_wait_total_ms"] > 0.0

    # The queue is a real wait: two batches of 150 ms, not one batch of four.
    assert record.duration_ms >= 0.28
    serial = sum(child.duration_ms for child in record.children)
    assert record.duration_ms < serial * 0.9, (record.duration_ms, serial)


def test_without_a_cap_nothing_is_queued(state):
    record = _spawn(["a", "b", "c"], state, provider=S.StubProvider(latency_s=0.05))
    assert record.timing["parallel"] == 0
    assert record.timing["queue_max_concurrent"] == 3
    assert record.timing["queue_waited_children"] == []
    assert not any(child.queued for child in record.children)


# --------------------------------------------------------------------------- #
# isolation, proven from both sides
# --------------------------------------------------------------------------- #

PARENT_ONLY = "PARENT-CONVERSATION-ONLY-7a1f"
CHILD_ONE_ANSWER = "ANSWER-OF-CHILD-ONE-42zz"


class _StaggeredSentinelProvider:
    """c1 answers with its own sentinel at once; c2 is slower, so c2's **second**
    turn is built after c1 finished — exactly when a leak would become visible."""

    def __init__(self) -> None:
        self.seen: list[tuple[str, str]] = []
        self._counts: dict[str, int] = {}

    def complete(self, *, prompt_id: str, prompt: str, tier: str) -> str:
        self.seen.append((prompt_id, prompt))
        child = prompt_id.split(".")[2]
        index = self._counts.get(child, 0) + 1
        self._counts[child] = index
        if child == "c1":
            return f"{CHILD_ONE_ANSWER}\n{S.FINAL_MARKER} c1 done"
        time.sleep(0.12)
        if index >= 2:
            return f"c2 done\n{S.FINAL_MARKER} c2 done"
        return "c2 still working"


def test_a_child_cannot_see_the_parent_conversation_or_a_sibling(state):
    """The parent's conversation and a sibling's answer reach no child's prompt."""
    parent_conversation = f"the parent's own turn: {PARENT_ONLY} — never handed to a child"
    assert PARENT_ONLY in parent_conversation  # the parent holds it and nothing else does
    provider = _StaggeredSentinelProvider()
    record = _spawn(
        ["alpha task", "beta task"], state, provider=provider, parallel=2, max_turns=3
    )
    assert [child.status for child in record.children] == [S.STATUS_DELIVERED] * 2

    c1_prompts = [prompt for pid, prompt in provider.seen if ".c1." in pid]
    c2_prompts = [prompt for pid, prompt in provider.seen if ".c2." in pid]
    assert Path(record.children[0].context["transcript"]).is_file()
    assert CHILD_ONE_ANSWER in Path(record.children[0].context["transcript"]).read_text()
    assert len(c2_prompts) == 2, "c2 needs a second turn for this test to mean anything"

    for prompt in c1_prompts:
        assert PARENT_ONLY not in prompt, "a child saw the parent's conversation"
        assert CHILD_ONE_ANSWER not in prompt  # its own answer is not fed back to it

    for prompt in c2_prompts:
        assert PARENT_ONLY not in prompt, "a child saw the parent's conversation"
        assert CHILD_ONE_ANSWER not in prompt, "a child saw its sibling's answer"
        assert "beta task" in prompt and "alpha task" not in prompt
        assert "cannot see any sibling child's context" in prompt

    # c1's answer really is on disk by the time c2's second prompt is built, and it
    # is c1's own file — nothing about it appears in c2's context.
    assert Path(record.children[0].context["transcript"]).is_file()
    assert record.children[1].context["session_id"] == f"{record.run_id}:c2"
    c1_transcript = Path(record.children[0].context["transcript"])
    c2_transcript = Path(record.children[1].context["transcript"])
    # c2's transcript is c2's own file. Assert the *identifiers*, not an arbitrary
    # substring: the run id is random hex, so a run whose id happens to contain "c1"
    # made `"c1" not in <path>` fail for a path that was never c1's (flaky by
    # construction — nothing about c2's isolation changed in that run).
    assert c2_transcript != c1_transcript
    assert c1_transcript.parent.name == "c1", c1_transcript
    assert c2_transcript.parent.name == "c2", c2_transcript
    assert c1_transcript.parent != c2_transcript.parent

    # The parent's conversation was never an input: it is in no file under the run.
    for path in Path(state).rglob("*"):
        if path.is_file():
            assert PARENT_ONLY not in path.read_text(errors="ignore"), path


# --------------------------------------------------------------------------- #
# nesting: a child may spawn children only below the cap, and the cap is enforced
# --------------------------------------------------------------------------- #


def test_a_child_at_the_depth_cap_may_not_spawn_children(state):
    recorder = _Recorder(S.StubProvider(latency_s=0.0, default_turns=1))
    record = _spawn(
        ["work [[spawn:a grandchild task]] [[turns:1]]"],
        state,
        provider=recorder,
        run_id="cap-default",
        role=S.UNROLED,  # the depth cap is the ceiling under test, not the role
    )
    child = record.children[0]

    assert S.DEFAULT_MAX_DEPTH == 1 and record.max_depth == 1
    assert child.status == S.STATUS_DELIVERED, "a refused nesting is not a child failure"
    assert child.failure_class == ""
    assert child.context["depth"] == 1 and child.context["may_spawn"] is False
    assert record.nesting["spawned"] == []
    refused = record.nesting["refused"]
    assert len(refused) == 1
    assert refused[0]["child_id"] == "c1" and refused[0]["depth"] == 1
    assert "cap is reached" in refused[0]["reason"]
    assert any("nesting refused" in note for note in child.notes)
    assert any("nesting refused 1 time(s)" in note for note in record.notes)
    # Nothing was created: no nested run directory, no grandchild transcript.
    assert not Path(child.context["nested_dir"]).exists()
    # The child was told the truth about its own depth before it worked.
    assert "you may not spawn child agents" in recorder.prompts[0][1]
    # The refusal is durable evidence, not only a note on the result.
    events = S.RunStore(state).events("cap-default", child_id="c1")
    assert [event["kind"] for event in events].count("child_nesting_refused") == 1
    assert [event["kind"] for event in events].count("child_nesting_declared") == 1


def test_below_the_cap_a_child_spawns_grandchildren_and_their_answers_reach_it(state):
    recorder = _Recorder(S.StubProvider(latency_s=0.0, default_turns=1))
    record = S.spawn(
        ["parent work [[spawn:grandchild A]][[spawn:grandchild B]] [[turns:1]]"],
        provider=recorder,
        state_dir=state,
        run_id="nested-two",
        max_depth=2,
        role=S.UNROLED,  # the depth cap is the ceiling under test, not the role
        provider_summary={"mode": "stub"},
    )
    child = record.children[0]

    assert child.status == S.STATUS_DELIVERED
    assert record.nesting["refused"] == []
    spawned = record.nesting["spawned"]
    assert len(spawned) == 1
    assert spawned[0]["by"] == "c1" and spawned[0]["total"] == 2
    assert spawned[0]["delivered"] == 2

    nested_path = Path(spawned[0]["dir"])
    assert (nested_path / S.RUN_FILE).is_file(), "a nested run is addressable evidence"
    nested = json.loads((nested_path / S.RUN_FILE).read_text())
    assert nested["depth"] == 1 and nested["max_depth"] == 2
    assert nested["parent"] == {"run_id": "nested-two", "child_id": "c1"}
    assert [item["depth"] for item in nested["children"]] == [2, 2]
    assert all(item["context"]["may_spawn"] is False for item in nested["children"])
    for grandchild in nested["children"]:
        assert Path(grandchild["context"]["transcript"]).is_file()
        assert grandchild["context"]["session_id"].startswith(f"{nested['run_id']}:")

    # Their answers reached the child, and their cost is reported apart from its own.
    assert child.nested["run_id"] == nested["run_id"]
    assert child.nested_tokens == nested["spent_tokens"] > 0
    assert child.cost["nested_tokens"] == child.nested_tokens
    assert any("spawned 2 child(ren) below the cap" in note for note in child.notes)
    own_prompts = [
        prompt for pid, prompt in recorder.prompts if pid.startswith("subagent.nested-two.")
    ]
    assert own_prompts, "the child made no call of its own"
    assert "Children you spawned reported:" in own_prompts[0]
    assert "child reached its final answer on turn 1" in own_prompts[0]


def test_the_depth_cap_is_propagated_so_no_chain_can_exceed_it(state):
    provider = S.StubProvider(latency_s=0.0, default_turns=1)
    deep = S.SubagentRun(
        provider=provider,
        briefs=S.briefs_from_tasks(["grandchild at the cap"], depth=2, role=S.UNROLED),
        state_dir=state / "chain",
        run_id="grandchild-run",
        max_depth=2,
        depth=1,
        parent_run="parent-run",
        parent_child="c1",
        provider_summary={"mode": "stub"},
    )
    context = deep.context_of("c1")
    assert (context.depth, context.max_depth, context.may_spawn) == (2, 2, False)
    with pytest.raises(S.DepthCapReached) as excinfo:
        deep.nested_spawner("c1")(["a great-grandchild"])
    assert excinfo.value.depth == 2 and excinfo.value.max_depth == 2
    assert "cap is reached" in str(excinfo.value)
    assert not Path(context.nested_dir).exists(), "a refused nesting left files behind"

    # The cap is a cap, not a blanket ban: one level up the same call works.
    shallow = S.SubagentRun(
        provider=provider,
        briefs=S.briefs_from_tasks(["child below the cap"], depth=1, role=S.UNROLED),
        state_dir=state / "chain2",
        run_id="child-run",
        max_depth=2,
        depth=0,
        provider_summary={"mode": "stub"},
    )
    nested = shallow.nested_spawner("c1")(["a grandchild"], run_id="g1")
    assert nested.depth == 1 and nested.children[0].context["depth"] == 2
    assert (Path(state) / "chain2" / "child-run" / "children" / "c1" / "nested" / "g1").is_dir()


def test_a_configured_depth_outside_the_ceiling_is_refused(state):
    provider = S.StubProvider(latency_s=0.0)
    for bad in (-1, S.MAX_DEPTH_LIMIT + 1):
        with pytest.raises(S.SubagentError) as excinfo:
            S.SubagentRun(
                provider=provider,
                briefs=S.briefs_from_tasks(["a"]),
                state_dir=state,
                max_depth=bad,
            )
        assert "--max-depth" in str(excinfo.value)


def test_an_unknown_child_cannot_get_a_spawner(state):
    run = S.SubagentRun(
        provider=S.StubProvider(),
        briefs=S.briefs_from_tasks(["a"]),
        state_dir=state,
        max_depth=3,
        provider_summary={"mode": "stub"},
    )
    with pytest.raises(S.SubagentError):
        run.nested_spawner("nope")


# --------------------------------------------------------------------------- #
# a parent cancel stops the children — in-process, from a signal, and at the cap
# --------------------------------------------------------------------------- #


def test_a_parent_cancel_stops_every_child_and_finishes_the_run(state):
    store = S.RunStore(state)
    run = S.SubagentRun(
        provider=S.StubProvider(latency_s=0.1),
        briefs=S.briefs_from_tasks(["a [[infinite]]", "b [[infinite]]"], max_turns=500),
        state_dir=state,
        run_id="parent-cancel",
        parallel=1,  # b is still queued when the cancel lands
        provider_summary={"mode": "stub"},
    )
    thread = threading.Thread(target=run.run, daemon=True)
    thread.start()
    _wait_until(lambda: (store.load("parent-cancel") or {}).get("status") == S.RUN_RUNNING)

    assert run.cancel("the parent changed its mind") is True
    assert run.cancel("a second reason") is False, "the first reason wins"
    thread.join(timeout=15)
    assert not thread.is_alive()

    record = store.load("parent-cancel")
    assert record["status"] == S.RUN_CANCELLED
    assert record["cancel_reason"] == "the parent changed its mind"
    assert record["complete"] is True
    assert [child["status"] for child in record["children"]] == [S.STATUS_CANCELLED] * 2
    assert {child["failure_class"] for child in record["children"]} == {"parent_cancelled"}
    assert not any(child["thread_alive"] for child in record["children"])
    # Proven, not claimed: no worker thread of this run is still alive.
    assert [name for name, worker in run._threads.items() if worker.is_alive()] == []


_SIGINT_SCRIPT = '''
import threading
import time

from skaldir import subagents as S


def announce_later():
    time.sleep(0.4)
    print("READY", flush=True)


threading.Thread(target=announce_later, daemon=True).start()
run = S.SubagentRun(
    provider=S.StubProvider(latency_s=0.2),
    briefs=S.briefs_from_tasks(["a [[infinite]]", "b [[infinite]]"], max_turns=1000),
    state_dir=STATE,
    run_id="sigint-run",
    provider_summary={"mode": "stub"},
)
record = run.run()
print("DONE " + record.status, flush=True)
'''


def test_a_sigint_on_the_parent_stops_the_children_instead_of_abandoning_the_run(state):
    """Ctrl-C is a cancel: the record says cancelled and the children stopped."""
    script = state.parent / "sigint_parent.py"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(_SIGINT_SCRIPT.replace("STATE", repr(str(state))))
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    proc = subprocess.Popen(
        [sys.executable, str(script)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        ready, _, _ = select.select([proc.stdout], [], [], 30.0)
        assert ready, "the child process never announced its run"
        assert "READY" in proc.stdout.readline()
        time.sleep(0.15)
        proc.send_signal(signal.SIGINT)
        out, err = proc.communicate(timeout=30)
    finally:
        if proc.poll() is None:  # pragma: no cover - only on a hung child
            proc.kill()
            proc.communicate(timeout=10)
    assert proc.returncode == 0, (proc.returncode, out, err)
    assert "DONE cancelled" in out, (out, err)

    record = json.loads((state / "sigint-run" / S.RUN_FILE).read_text())
    assert record["status"] == S.RUN_CANCELLED
    assert "SIGINT" in record["cancel_reason"]
    assert [child["status"] for child in record["children"]] == [S.STATUS_CANCELLED] * 2
    assert {child["failure_class"] for child in record["children"]} == {"parent_cancelled"}
    events = S.RunStore(state).events("sigint-run")
    assert [event["kind"] for event in events].count("run_parent_cancelled") == 1
    assert any(event["kind"] == "run_finished" for event in events)


def test_the_stop_grace_settles_a_child_stuck_in_a_provider_call(state):
    """A cancel cannot interrupt a provider call — so the collector stops waiting.

    The provider signals **entry** into the call before it sleeps, and the cancel is issued only
    after that signal: cancelling while the child's thread had not yet reached its call made
    this test race its own setup (measured 6/15 on an idle machine — the child then ended at the
    turn boundary, ``thread_alive: false``, which is accurate and is not the interleaving this
    test is about).
    """
    store = S.RunStore(state)

    class SignallingStub(S.StubProvider):
        """The stub, plus one event that says the child is *inside* its provider call."""

        def __init__(self, **kwargs) -> None:
            super().__init__(**kwargs)
            self.entered = threading.Event()

        def complete(self, *, prompt_id: str, prompt: str, tier: str) -> str:
            self.entered.set()
            return super().complete(prompt_id=prompt_id, prompt=prompt, tier=tier)

    provider = SignallingStub(latency_s=3.0)
    run = S.SubagentRun(
        provider=provider,
        briefs=S.briefs_from_tasks(["stuck [[infinite]]"], max_turns=100),
        state_dir=state,
        run_id="grace",
        provider_summary={"mode": "stub"},
        stop_grace_s=0.2,
    )
    thread = threading.Thread(target=run.run, daemon=True)
    thread.start()
    _wait_until(lambda: (store.load("grace") or {}).get("status") == S.RUN_RUNNING)
    assert provider.entered.wait(timeout=10.0), "the child never reached its provider call"

    started = time.monotonic()
    run.cancel("parent cancelled the run")
    thread.join(timeout=10)
    elapsed = time.monotonic() - started

    assert not thread.is_alive()
    assert elapsed < 2.5, f"the collector waited {elapsed:.2f}s despite a 0.2s grace"
    record = store.load("grace")
    assert record["status"] == S.RUN_CANCELLED
    child = record["children"][0]
    assert child["status"] == S.STATUS_CANCELLED
    assert child["failure_class"] == "parent_cancelled"
    assert child["thread_alive"] is True, "the harness must not pretend the call stopped"
    assert any("still alive" in note for note in record["notes"])


# --------------------------------------------------------------------------- #
# failure classes, cost, retry, live transcripts
# --------------------------------------------------------------------------- #


def test_every_non_delivery_carries_a_machine_readable_failure_class(state):
    record = _spawn(
        ["fine [[turns:1]]", "boom [[fail]]", "hang [[infinite]]"],
        state,
        provider=S.StubProvider(latency_s=0.3),
        timeout_s=0.45,
        max_turns=5,
        run_id="classes",
    )
    classes = {child.child_id: child.failure_class for child in record.children}
    assert classes == {"c1": "", "c2": "provider_error", "c3": "timeout"}
    assert record.failure_classes == {"provider_error": ["c2"], "timeout": ["c3"]}
    payload = record.as_dict()
    assert payload["failure_classes"] == record.failure_classes
    assert payload["children"][0]["failure_class"] is None
    assert set(record.failure_classes) <= set(S.FAILURE_CLASSES)


def test_cost_reports_measured_units_and_never_a_money_figure(state):
    record = _spawn(
        ["a [[turns:1]]", "b [[turns:2]]"], state, provider=S.StubProvider(latency_s=0.02)
    )
    payload = record.as_dict()
    cost = payload["cost"]
    assert cost["tokens"] == payload["spent_tokens"] > 0
    assert cost["turns"] == 3
    assert cost["provider_calls"] == 3
    assert cost["children"] == 2
    assert cost["provider_ms"] > 0.0 and cost["wall_ms"] > 0.0
    assert cost["currency"] is None and "no price table" in cost["note"]
    for child in payload["children"]:
        assert child["cost"]["tokens"] == child["spent_tokens"]
        assert child["cost"]["budget_tokens"] == child["budget_tokens"]
        assert child["cost"]["turns"] == child["turns"]
        assert child["cost"]["currency"] is None


class _FlakyProvider:
    """Fails c2's *first* call and answers afterwards — a transient provider error."""

    def __init__(self) -> None:
        self.calls: dict[str, int] = {}
        self._guard = threading.Lock()

    def complete(self, *, prompt_id: str, prompt: str, tier: str) -> str:
        child = prompt_id.split(".")[2]
        with self._guard:
            count = self.calls.get(child, 0) + 1
            self.calls[child] = count
        if child == "c2" and count == 1:
            raise RuntimeError("transient provider error on the first attempt")
        return f"recovered on attempt {count}\n{S.FINAL_MARKER} answer for {child}"


def test_a_retry_run_repeats_only_the_unfinished_children_and_keeps_the_first_attempt(state):
    provider = _FlakyProvider()
    first = _spawn(["done [[turns:1]]", "flaky work"], state, provider=provider, run_id="first")
    assert [child.status for child in first.children] == [S.STATUS_DELIVERED, S.STATUS_FAILED]
    assert first.children[1].failure_class == "provider_error"

    briefs = S.retry_briefs(first.as_dict())
    assert [brief.child_id for brief in briefs] == ["c2"], "a delivered child is not re-run"
    retry = S.SubagentRun(
        provider=provider,
        briefs=briefs,
        state_dir=state,
        run_id="first-retry",
        retried_from="first",
        provider_summary={"mode": "stub"},
    ).run()
    payload = retry.as_dict()
    assert payload["retried_from"] == "first"
    assert payload["children_total"] == 1
    assert payload["children"][0]["child_id"] == "c2", "a retry keeps the child's own id"
    assert payload["children"][0]["status"] == S.STATUS_DELIVERED
    assert any("re-runs the unfinished children of first" in note for note in payload["notes"])

    # The first attempt is untouched and still addressable — a retry is not a resume.
    store = S.RunStore(state)
    original = store.load("first")
    assert original["children"][1]["status"] == S.STATUS_FAILED
    turns = store.transcript("first", "c2")
    assert turns and turns[0]["error"].startswith("RuntimeError: transient provider error")


def test_retry_briefs_carry_the_childrens_own_envelope_and_refuse_nonsense(state):
    record = _spawn(
        ["done [[turns:1]]", "boom [[fail]]", "never [[infinite]]"],
        state,
        provider=S.StubProvider(latency_s=0.05),
        max_turns=2,
        budget_tokens=5000,
        timeout_s=30.0,
        run_id="retry-source",
    )
    payload = record.as_dict()
    assert payload["children_delivered"] == 1
    briefs = S.retry_briefs(payload)
    assert [brief.child_id for brief in briefs] == ["c2", "c3"]
    assert [brief.task for brief in briefs] == ["boom [[fail]]", "never [[infinite]]"]
    envelopes = {
        (b.max_turns, b.budget_tokens, b.timeout_s, b.depth) for b in briefs
    }
    assert envelopes == {(2, 5000, 30.0, 1)}
    override = S.retry_briefs(payload, max_turns=9, budget_tokens=99, timeout_s=1.5, tier="main")
    overridden = {
        (b.max_turns, b.budget_tokens, b.timeout_s, b.tier) for b in override
    }
    assert overridden == {(9, 99, 1.5, "main")}
    assert [brief.child_id for brief in S.retry_briefs(payload, child_ids=["c1"])] == ["c1"]
    with pytest.raises(S.SubagentError):
        S.retry_briefs(payload, child_ids=["nope"])
    with pytest.raises(S.SubagentError):
        S.retry_briefs({"run_id": "all-done", "children": [{"child_id": "c1", "ok": True}]})


def test_a_childs_transcript_is_addressable_and_readable_while_it_is_still_running(state):
    store = S.RunStore(state)
    run = S.SubagentRun(
        provider=S.StubProvider(latency_s=0.15),
        briefs=S.briefs_from_tasks(["long task [[infinite]]"], max_turns=10),
        state_dir=state,
        run_id="live-reading",
        provider_summary={"mode": "stub"},
    )
    thread = threading.Thread(target=run.run, daemon=True)
    thread.start()
    try:
        turns: list[dict] = []
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            turns = store.transcript("live-reading", "c1")
            record = store.load("live-reading") or {}
            if turns and record.get("status") == S.RUN_RUNNING:
                break
            time.sleep(0.01)
        assert turns, "no turn was readable on disk while the run was still running"
        assert turns[0]["turn"] == 1
        assert turns[0]["session_id"] == "live-reading:c1"
        assert turns[0]["text"].startswith("stub:")
        # The path the run record reports is exactly the one the store addresses.
        assert Path(run.context_of("c1").transcript) == store.transcript_path("live-reading", "c1")
        assert store.children_of("live-reading") == ["c1"]
        assert store.transcript("live-reading", "nope") == []
        events = [event["kind"] for event in store.events("live-reading", child_id="c1")]
        assert events[:2] == ["child_turn_started", "child_text"]
    finally:
        run.cancel("test cleanup")
        thread.join(timeout=15)


def _wait_until(predicate, timeout_s: float = 10.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition never became true")
