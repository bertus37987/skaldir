"""Computer Use: policy enforcement, channel ordering, verified fallback, real runners.

Sandbox tests run real bwrap processes (fallback works without); container
tests run real rootless-podman runs and are skipped when image/podman are
missing (e.g. CI runners).
"""

import json
import shutil
import subprocess

import pytest

from skaldir.computer_use import (
    Action,
    ActionResult,
    Attempt,
    ContainerRunner,
    CUExecutor,
    CUPolicy,
    DryRunRunner,
    LocalSandboxRunner,
    Observation,
    PolicyViolation,
    Verification,
    order_attempts,
    plan_cu_attempts,
    runner_availability,
)
from skaldir.events import EventStore

ALPINE = "docker.io/library/alpine:latest"
GUI_IMAGE = ContainerRunner.DEFAULT_IMAGE


class LiveRunner:
    """A runner that would touch the live system (isolation 'none')."""

    isolation = "none"
    is_isolated = False

    def __init__(self) -> None:
        self.acted = False

    def act(self, action):
        self.acted = True
        return ActionResult(ok=True, stdout="live")

    def observe(self):
        return Observation(text="live")

    def close(self) -> None:
        pass


def _attempt(
    channel,
    *,
    kind="cli",
    argv=None,
    payload=None,
    verify_stdout: "str | None" = "ok",
    verify_observe=None,
    description="",
):
    return Attempt(
        channel=channel,
        action=Action(
            kind=kind,
            argv=argv or ["/usr/bin/echo", "ok"],
            payload=payload or {},
            description=description,
        ),
        verify=Verification(
            expect_stdout_contains=verify_stdout, expect_observe_contains=verify_observe
        ),
    )


class TestOrdering:
    def test_api_dom_vision_order_is_enforced(self):
        attempts = [_attempt("vision"), _attempt("api"), _attempt("dom")]
        ordered = order_attempts(attempts)
        assert [attempt.channel for attempt in ordered] == ["api", "dom", "vision"]

    def test_stable_within_channel(self):
        first = _attempt("api", description="first")
        second = _attempt("api", description="second")
        ordered = order_attempts([second, first])
        assert [attempt.action.description for attempt in ordered] == ["second", "first"]


class TestPolicy:
    def test_live_runner_is_rejected(self, tmp_path):
        executor = CUExecutor(CUPolicy(), events=EventStore(tmp_path / "events.db"))
        live = LiveRunner()
        with pytest.raises(PolicyViolation):
            executor.run("s", [_attempt("api")], live)
        assert not live.acted

    def test_dry_run_policy_rejects_sandbox_runner(self, tmp_path):
        executor = CUExecutor(CUPolicy(dry_run=True))
        with pytest.raises(PolicyViolation):
            executor.run("s", [_attempt("api")], LocalSandboxRunner(tmp_path))

    def test_disallowed_kind_blocks_without_acting(self, tmp_path):
        events = EventStore(tmp_path / "events.db")
        executor = CUExecutor(CUPolicy(allowed_kinds=("cli",)), events=events)
        runner = DryRunRunner()
        with pytest.raises(PolicyViolation):
            executor.run("s", [_attempt("dom", kind="dom_click")], runner)
        assert runner.actions == []
        assert "cu_blocked" in [event.kind for event in events.iter_session("cu")]

    def test_attempt_without_verification_is_refused(self):
        executor = CUExecutor(CUPolicy())
        attempt = Attempt(
            channel="api", action=Action(kind="cli", argv=["/usr/bin/echo"]),
            verify=Verification(),
        )
        with pytest.raises(PolicyViolation):
            executor.run("s", [attempt], DryRunRunner())


class TestExecutor:
    def _executor(self, tmp_path) -> CUExecutor:
        return CUExecutor(CUPolicy(), events=EventStore(tmp_path / "events.db"))

    def test_fallback_to_next_channel(self, tmp_path):
        executor = self._executor(tmp_path)
        runner = DryRunRunner(
            behaviors={
                "api deploy": ActionResult(ok=False, detail="HTTP 500"),
                "dom close": ActionResult(ok=True, stdout="window closed"),
            }
        )
        attempts = [
            _attempt(
                "dom", kind="dom_click", verify_stdout="window closed", description="dom close"
            ),
            _attempt("api", verify_stdout="deployed", description="api deploy"),
        ]
        result = executor.run("deploy", attempts, runner)
        assert result.status == "verified"
        assert result.channel == "dom"
        assert result.actions == 2 and result.fallbacks == 1
        kinds = [event.kind for event in executor.events.iter_session("cu")]
        assert kinds == [
            "cu_attempt",
            "cu_action",
            "cu_failed",
            "cu_attempt",
            "cu_action",
            "cu_verified",
        ]

    def test_observe_based_verification(self, tmp_path):
        executor = self._executor(tmp_path)
        runner = DryRunRunner(observations=["fenster: geschlossen"])
        attempt = _attempt(
            "vision",
            kind="mouse",
            payload={"x": 1, "y": 1},
            verify_stdout=None,
            verify_observe="geschlossen",
        )
        result = executor.run("click", [attempt], runner)
        assert result.status == "verified" and result.channel == "vision"

    def test_max_fallbacks_aborts(self, tmp_path):
        executor = CUExecutor(CUPolicy(max_fallbacks=1), events=EventStore(tmp_path / "events.db"))
        runner = DryRunRunner(behaviors={"api x": ActionResult(ok=True, stdout="nope")})
        attempts = [
            _attempt("api", verify_stdout="missing", description="api x"),
            _attempt("dom", kind="dom_click", verify_stdout="missing2", description="dom x"),
        ]
        result = executor.run("s", attempts, runner)
        assert result.status == "aborted" and result.actions == 1 and result.fallbacks == 1
        kinds = [event.kind for event in executor.events.iter_session("cu")]
        assert kinds[-1] == "cu_aborted"

    def test_max_actions_stops_before_acting(self, tmp_path):
        executor = CUExecutor(CUPolicy(max_actions=0), events=EventStore(tmp_path / "events.db"))
        runner = DryRunRunner()
        result = executor.run("s", [_attempt("api")], runner)
        assert result.status == "aborted" and result.actions == 0
        assert runner.actions == []


class TestSandboxRunner:
    def test_cli_action_runs_and_verifies(self, tmp_path):
        runner = LocalSandboxRunner(tmp_path)
        attempt = _attempt(
            "api", argv=["/usr/bin/echo", "hallo-welt"], verify_stdout="hallo-welt"
        )
        result = CUExecutor(CUPolicy()).run("cli", [attempt], runner)
        assert result.status == "verified"

    def test_failing_command_falls_through(self, tmp_path):
        runner = LocalSandboxRunner(tmp_path)
        attempts = [
            _attempt("api", argv=["/usr/bin/false"], verify_stdout="never", description="fails"),
            _attempt("dom", argv=["/usr/bin/echo", "fallback ok"], verify_stdout="fallback ok"),
        ]
        result = CUExecutor(CUPolicy()).run("cli", attempts, runner)
        assert result.status == "verified" and result.channel == "dom"

    def test_observe_lists_workdir(self, tmp_path):
        (tmp_path / "marker.txt").write_text("x", encoding="utf-8")
        observation = LocalSandboxRunner(tmp_path).observe()
        assert "marker.txt" in observation.text

    def test_gui_kind_unsupported_in_sandbox(self, tmp_path):
        result = LocalSandboxRunner(tmp_path).act(Action(kind="mouse", payload={"x": 1, "y": 1}))
        assert not result.ok


def _podman(*args: str) -> tuple[int, str]:
    proc = subprocess.run(["podman", *args], capture_output=True, text=True, timeout=60)
    return proc.returncode, (proc.stdout or proc.stderr or "").strip()


PODMAN = shutil.which("podman") is not None


def _image_present(image: str) -> bool:
    return PODMAN and ContainerRunner(image=image, gui=False).available()[0]


@pytest.mark.skipif(not _image_present(ALPINE), reason="podman/alpine image not available")
class TestContainerRunner:
    def test_cli_action_roundtrip(self):
        runner = ContainerRunner(image=ALPINE, gui=False)
        try:
            attempt = _attempt(
                "api", argv=["echo", "container-hallo"], verify_stdout="container-hallo"
            )
            result = CUExecutor(CUPolicy()).run("container-cli", [attempt], runner)
            assert result.status == "verified"
        finally:
            runner.close()

    def test_close_removes_container(self):
        runner = ContainerRunner(image=ALPINE, gui=False)
        ok, _detail = runner.start()
        assert ok
        runner.close()
        code, _output = _podman("container", "exists", runner.name)
        assert code != 0


@pytest.mark.skipif(not _image_present(GUI_IMAGE), reason="godsv-cu-x11 image not available")
class TestContainerGui:
    def test_gui_geometry_and_window_flow(self):
        runner = ContainerRunner()
        try:
            ok, detail = runner.start()
            assert ok, detail
            geometry = runner.act(Action(kind="cli", argv=["xdotool", "getdisplaygeometry"]))
            assert geometry.ok and "800 600" in geometry.stdout
            opened = runner.act(
                Action(
                    kind="cli",
                    argv=[
                        "sh",
                        "-c",
                        "(xmessage -name godsvtest42 hallo >/dev/null 2>&1 &); "
                        "sleep 1; echo opened",
                    ],
                )
            )
            assert "opened" in opened.stdout
            mouse = runner.act(Action(kind="mouse", payload={"x": 50, "y": 50}))
            assert mouse.ok
            observation = runner.observe()
            assert "godsvtest42" in observation.text
        finally:
            runner.close()

    def test_executor_step_verifies_via_observe(self):
        runner = ContainerRunner()
        try:
            runner.act(
                Action(
                    kind="cli",
                    argv=[
                        "sh",
                        "-c",
                        "(xmessage -name godsvcheck hallo >/dev/null 2>&1 &); sleep 1; echo ok",
                    ],
                )
            )
            attempt = Attempt(
                channel="dom",
                action=Action(kind="mouse", payload={"x": 20, "y": 20}),
                verify=Verification(expect_observe_contains="godsvcheck"),
            )
            result = CUExecutor(CUPolicy()).run("gui-step", [attempt], runner)
            assert result.status == "verified" and result.channel == "dom"
        finally:
            runner.close()


class TestLlmPlan:
    def test_plan_is_ordered(self):
        from skaldir.llm import ScriptedLLM
        from skaldir.prompts import PromptRegistry

        payload = json.dumps(
            {
                "attempts": [
                    {
                        "channel": "vision",
                        "action": {"kind": "mouse", "payload": {"x": 1, "y": 1}},
                        "verify": {"expect_observe_contains": "x"},
                        "description": "vision attempt",
                    },
                    {
                        "channel": "api",
                        "action": {"kind": "cli", "argv": ["git", "status"]},
                        "verify": {"expect_stdout_contains": "clean"},
                        "description": "api attempt",
                    },
                ]
            }
        )
        client = ScriptedLLM(responses={"cu_step_plan": payload})
        attempts = plan_cu_attempts(client, PromptRegistry(), goal="check repo")
        assert attempts is not None
        assert [attempt.channel for attempt in attempts] == ["api", "vision"]

    def test_invalid_plan_returns_none(self):
        from skaldir.llm import ScriptedLLM
        from skaldir.prompts import PromptRegistry

        client = ScriptedLLM(responses={"cu_step_plan": "kein json"})
        assert plan_cu_attempts(client, PromptRegistry(), goal="x") is None


class TestAvailabilityAndCli:
    def test_availability_reports_dry_run(self):
        report = runner_availability()
        assert report["dry-run"] == "available"
        assert "sandbox (bwrap)" in report
        # every reported level speaks English (the product ships English only), and every
        # level that is available says *with what* (the host-session entry names its tools)
        assert all(value in ("available", "unavailable")
                   or value.startswith(("unavailable (", "available (",
                                        "podman is not installed"))
                   for value in report.values()), report

    def test_cu_demo_cli(self, capsys):
        from skaldir.cli import main

        assert main(["cu", "demo"]) == 0
        out = capsys.readouterr().out
        assert "verified" in out and "dom" in out

    def test_cu_check_cli(self, capsys):
        from skaldir.cli import main

        assert main(["cu", "check"]) == 0
        assert "dry-run" in capsys.readouterr().out
