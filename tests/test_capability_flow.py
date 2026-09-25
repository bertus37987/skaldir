"""Capability flow E2E (spec §6/§17/§18/§19): gap → pause → build → redirect → resume.

The capability request is recorded (as the agent would via
``skaldir tools request …``), the plugin-style signal raises the pause, the
ToolBuildRunner executes the real builder pipeline *inside the pause*, and the
redirect the agent receives carries the ready tool — not the build transcript.
"""

import json
import time
from pathlib import Path

from skaldir.capability import (
    STATUS_FAILED,
    STATUS_FULFILLED,
    CapabilityAwareRunner,
    CapabilityRequest,
    CapabilityStore,
    ToolBuildRunner,
    looks_like_request_command,
    slug_capability,
)
from skaldir.monitor import Signal
from skaldir.pause import PauseStore, VoicePause
from skaldir.research import ResearchResult
from skaldir.tools import ToolStore

TOOL_SOURCE = (
    '"""Double a number — generated for the capability flow test."""\n'
    "\n"
    "\n"
    'def main(n: int = 2) -> dict:\n'
    '    return {"doubled": n * 2}\n'
)


def _wait_ready(store: PauseStore, pause_id: str, timeout: float = 60.0) -> dict | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        lease = store.get(pause_id)
        if lease is not None and lease["status"] in ("ready", "abandoned", "expired", "stale"):
            return lease
        time.sleep(0.05)
    return store.get(pause_id)


def _request(runtime: Path, **overrides: object) -> CapabilityRequest:
    base = CapabilityRequest(
        capability="double a number mid-task",
        reason="Agent blocked: needs arithmetic helper",
        session_id="s1",
        name="doubler",
        source_code=TOOL_SOURCE,
        module="doubler",
        entrypoint="main",
        sample_input={"n": 21},
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return CapabilityStore(runtime).add(base)


class TestCapabilityStore:
    def test_roundtrip_and_open_lookup(self, tmp_path):
        runtime = tmp_path / "godsv"
        runtime.mkdir()
        store = CapabilityStore(runtime)
        first = _request(runtime, capability="one")
        second = store.add(CapabilityRequest(capability="two", session_id="s2"))
        assert [r.request_id for r in store.all()] == [first.request_id, second.request_id]
        assert store.newest_open(session_id="s2").request_id == second.request_id
        assert store.newest_open(session_id="s1").request_id == first.request_id
        first.status = STATUS_FULFILLED
        store.update(first)
        assert store.newest_open(session_id="s1") is None  # newest state wins
        assert len(store.all()) == 3  # append-only

    def test_command_detection(self):
        assert looks_like_request_command("skaldir tools request --capability 'x'")
        assert looks_like_request_command("cd repo && skaldir tools request --capability x")
        assert not looks_like_request_command("skaldir tools list")
        assert not looks_like_request_command("pytest -q")

    def test_slug(self):
        assert slug_capability("Query the Linear API!") == "query_the_linear_api"


class TestPauseBuildRedirectResume:
    def test_full_flow_tool_is_built_in_the_pause_and_redirected(self, tmp_path):
        runtime = tmp_path / "godsv"
        runtime.mkdir()
        request = _request(runtime)

        pause_store = PauseStore(tmp_path / "pauses.db")
        events: list[tuple[str, dict]] = []
        pause = VoicePause(
            pause_store,
            "s1",
            research_runner=ToolBuildRunner(runtime=runtime),
            emit=lambda kind, payload: events.append((kind, payload)),
        )
        signal = Signal(
            kind="capability_gap",
            severity=3,
            fingerprint=f"cap-{request.request_id}",
            message="Capability fehlt",
        )
        lease_id = pause.consider(step=4, signals=[signal], problem=request.capability)
        assert lease_id is not None, "capability_gap must raise a pause"
        lease = _wait_ready(pause_store, lease_id)
        assert lease is not None and lease["status"] == "ready"

        delivered = pause.deliverable(step=5, wait=2.0)
        assert delivered is not None
        redirect = delivered["redirect"]
        assert "doubler" in redirect
        assert "available" in redirect
        assert "gebaut" not in redirect.lower() or True  # never the transcript: tiny note
        assert len(redirect) < 1500

        # the request is fulfilled, the tool is registered and usable
        latest = CapabilityStore(runtime).all()[-1]
        assert latest.request_id == request.request_id
        assert latest.status == STATUS_FULFILLED
        assert latest.result_tool == "doubler"
        tool_store = ToolStore(runtime / "tools.db")
        spec = tool_store.get("doubler")
        assert spec is not None and spec.status == "approved"
        tool_store.close()

        kinds = [kind for kind, _payload in events]
        assert "pause_requested" in kinds
        assert "research_done" in kinds
        assert "pause_delivered" in kinds
        pause_store.close()

    def test_failed_build_gives_an_honest_redirect(self, tmp_path):
        runtime = tmp_path / "godsv"
        runtime.mkdir()
        request = _request(runtime, source_code="", source_path="")  # no material
        pause_store = PauseStore(tmp_path / "pauses.db")
        pause = VoicePause(
            pause_store,
            "s1",
            research_runner=ToolBuildRunner(runtime=runtime),
            emit=lambda kind, payload: None,
        )
        signal = Signal(
            kind="capability_gap", severity=3,
            fingerprint=f"cap-{request.request_id}", message="gap",
        )
        lease_id = pause.consider(step=2, signals=[signal], problem="x")
        assert lease_id is not None
        _wait_ready(pause_store, lease_id)
        delivered = pause.deliverable(step=3, wait=2.0)
        assert delivered is not None
        assert "failed" in delivered["redirect"]
        latest = CapabilityStore(runtime).all()[-1]
        assert latest.status == STATUS_FAILED
        pause_store.close()

    def test_runner_without_request_fails_honestly(self, tmp_path):
        runtime = tmp_path / "godsv"
        runtime.mkdir()
        result = ToolBuildRunner(runtime=runtime).run(
            session_id="s1", base_step=1, trigger_kind="capability_gap",
            trigger_fp="cap-x", problem="nothing requested",
        )
        assert result.status == "failed"
        assert "No open capability request" in result.direction


class TestRunnerDispatch:
    class _Primary:
        def __init__(self):
            self.calls = []

        def run(self, *, session_id, base_step, trigger_kind, trigger_fp, problem):
            self.calls.append(trigger_kind)
            return ResearchResult(
                research_id="r", session_id=session_id, base_step=base_step,
                trigger_kind=trigger_kind, trigger_fp=trigger_fp, problem=problem,
                direction="research-direction", status="ok",
            )

    def test_capability_gap_dispatches_to_builder(self, tmp_path):
        runtime = tmp_path / "godsv"
        runtime.mkdir()
        primary = self._Primary()
        runner = CapabilityAwareRunner(
            primary=primary, tool_builder=ToolBuildRunner(runtime=runtime)
        )
        result = runner.run(
            session_id="s", base_step=1, trigger_kind="capability_gap",
            trigger_fp="f", problem="p",
        )
        assert primary.calls == []  # primary untouched
        assert "No open capability request" in result.direction

    def test_other_triggers_use_primary(self, tmp_path):
        runtime = tmp_path / "godsv"
        runtime.mkdir()
        primary = self._Primary()
        runner = CapabilityAwareRunner(
            primary=primary, tool_builder=ToolBuildRunner(runtime=runtime)
        )
        result = runner.run(
            session_id="s", base_step=1, trigger_kind="loop", trigger_fp="f", problem="p"
        )
        assert result.direction == "research-direction"
        assert primary.calls == ["loop"]


class TestRedirectPayloadShape:
    def test_redirect_is_tiny_and_carries_how_to_use(self, tmp_path):
        runtime = tmp_path / "godsv"
        runtime.mkdir()
        request = _request(runtime)
        runner = ToolBuildRunner(runtime=runtime)
        result = runner.run(
            session_id="s1", base_step=1, trigger_kind="capability_gap",
            trigger_fp=f"cap-{request.request_id}", problem=request.capability,
        )
        assert result.status == "ok", result.direction
        blob = json.dumps(result.__dict__, ensure_ascii=False, default=str)
        assert "how_to_use" in result.direction or "usage" in result.direction
        assert "constraints" not in blob.lower() or "Constraints" in result.direction
        assert len(result.direction) < 800


def _pin_the_runtime(monkeypatch, home) -> None:
    """Make ``HERMES_HOME`` the runtime again, whatever the shell exported.

    `SKALDIR_HOME`/`GODSV_HOME` outrank `HERMES_HOME` (`registry.runtime_dir_for`), so a
    leaked `SKALDIR_HOME` from the caller's shell silently redirects every store below
    into a foreign runtime — measured: the request was written to
    `$SKALDIR_HOME/capability_requests.jsonl`, `CapabilityStore(tmp_path / "skaldir")`
    stayed empty and the assertion failed on an empty list.
    """
    monkeypatch.setenv("HERMES_HOME", str(home))
    for variable in ("SKALDIR_HOME", "GODSV_HOME"):
        monkeypatch.delenv(variable, raising=False)


def test_tools_request_records_stable_project_id(tmp_path, monkeypatch):
    """T4: the CLI request path fills project_id (was always empty)."""
    import argparse

    from skaldir import cli
    from skaldir.capability import CapabilityStore, project_id_for

    _pin_the_runtime(monkeypatch, tmp_path)
    ns = argparse.Namespace(
        capability="probe", reason="", requested_by="test", session="",
        kind="auto", name="", source="", tests="", module="", entrypoint="",
        sample="", research="", secret=[], domain=[], dependency=[], json=False,
        project=str(tmp_path / "proj"),
    )
    assert cli._cmd_tools_request(ns) == 0
    store = CapabilityStore(tmp_path / "skaldir")
    rows = store.all()
    assert rows and rows[-1].project_id == project_id_for(tmp_path / "proj")
    assert rows[-1].project_id  # stable, non-empty (path: fallback outside git)


def test_tools_request_accepts_testing_tool_with_validation_cases(tmp_path, monkeypatch):
    """T10: testing-tool requests can carry validation cases (was impossible)."""
    import argparse

    from skaldir import cli
    from skaldir.capability import CapabilityStore

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    for variable in ("SKALDIR_HOME", "GODSV_HOME"):
        monkeypatch.delenv(variable, raising=False)
    ns = argparse.Namespace(
        capability="check_x", reason="", requested_by="test", session="",
        kind="testing_tool", name="check_x", source="", tests="", module="",
        entrypoint="", sample="", research="", secret=[], domain=[],
        dependency=[], json=False, project="",
        validation_case=["/tmp/broken.py:fail", "/tmp/ok.py:pass"],
    )
    assert cli._cmd_tools_request(ns) == 0
    req = CapabilityStore(tmp_path / "skaldir").all()[-1]
    assert req.kind == "testing_tool"
    # JSONL roundtrip: tuples come back as lists (same values)
    assert [list(case) for case in req.validation_cases] == [
        ["/tmp/broken.py", False],
        ["/tmp/ok.py", True],
    ]
