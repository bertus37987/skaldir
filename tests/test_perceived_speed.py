"""The perceived-speed audit and the progress renderer it fixes (``MVP/57``).

Three things are proved here:

1. **The metrics mean what they say.** ``first_feedback_ms`` / ``first_result_ms`` /
   ``first_meaningful_ms`` / ``completion_ms`` / ``updates`` / ``longest_silence_ms`` are
   derived from a synthetic capture with hand-computed expectations — including the
   *frozen* shape (one write at the very end) and the *noisy* shape (a burst).
2. **The renderer's bounds hold** — first line immediate, one render per interval, folded
   events counted, one keep-alive per window, a ``stalled`` label after
   :data:`skaldir.streaming.STALL_S`, nothing at all when disabled. All with an injected
   clock, so no test sleeps.
3. **The wiring is real**: ``AgentLoop(progress=…)`` reports the four event kinds of a
   two-round run, a renderer that raises cannot change the run, and ``agent run`` writes the
   immediate line to **stderr** while stdout stays exactly one JSON envelope.
"""

from __future__ import annotations

import io
import json
import time
from pathlib import Path
from typing import Any

import pytest

from skaldir import theme as theme_module
from skaldir.agent_loop import STATUS_ANSWERED, AgentLoop, LoopLimits, builtin_tools
from skaldir.llm_provider import ChatResult, ToolCall
from skaldir.progress import RunProgress, for_args
from tools.gv_perceived_speed import (  # noqa: PLC2701 — the audit is a tool, not a package
    Capture,
    Marks,
    budget_violations,
    metrics_for,
    run_pty,
    visible,
    visible_lines,
)

RICH = theme_module.Theme(mode="rich", depth="none", glyphs="unicode", source="test")
PLAIN = theme_module.Theme(mode="plain", depth="none", glyphs="ascii", source="test")


# --------------------------------------------------------------------------- #
# 1. the metrics                                                              #
# --------------------------------------------------------------------------- #


def _capture(*events: tuple[float, str]) -> Capture:
    capture = Capture()
    capture.events = [(at, text.encode()) for at, text in events]
    capture.exit_code = 0
    capture.total_ms = events[-1][0] if events else 0.0
    return capture


def test_the_frozen_shape_is_measured_as_frozen():
    """One write at the very end: first feedback *is* completion, updates 1."""
    capture = _capture((1200.0, "run run-1 · answered\n  answer:\n    done\n"))
    metrics = metrics_for(capture, Marks(result=r"^run", meaningful=r"done"))
    assert metrics["first_feedback_ms"] == 1200.0
    assert metrics["first_result_ms"] == 1200.0
    assert metrics["first_meaningful_ms"] == 1200.0
    assert metrics["completion_ms"] == 1200.0
    assert metrics["updates"] == 1
    assert metrics["longest_silence_ms"] == 1200.0
    assert metrics["exit_code"] == 0


def test_the_incremental_shape_separates_feedback_result_and_completion():
    capture = _capture(
        (60.0, "  working · agent run · \"x\"\n"),
        (900.0, "  model call · step 1\n"),
        (1500.0, "run run-1 · answered\n  answer:\n    three files\n"),
    )
    metrics = metrics_for(capture, Marks(result=r"^run ", meaningful=r"three files"))
    assert metrics["first_feedback_ms"] == 60.0
    assert metrics["first_result_ms"] == 1500.0
    assert metrics["first_meaningful_ms"] == 1500.0
    assert metrics["completion_ms"] == 1500.0
    assert metrics["updates"] == 3
    assert metrics["longest_silence_ms"] == 840.0  # the 60 ms -> 900 ms gap
    assert metrics["lines"] == 5  # working · model call · run · answer: · three files


def test_ansi_and_carriage_returns_do_not_fake_content():
    """A colour escape is not content and an in-place rewrite is not a new line."""
    assert visible("\x1b[38;5;231;1m✓\x1b[0m answered\r\n") == "✓ answered\n"
    assert visible("working 1.0 s\rworking 2.0 s\r") == "working 1.0 s\nworking 2.0 s\n"
    assert visible_lines("\x1b[2mworking\x1b[0m\n\n   \n") == ["working"]


def test_a_meaningful_pattern_is_matched_across_write_boundaries():
    """The answer can be split by the terminal's own buffering — it still counts once."""
    capture = _capture(
        (500.0, "  answer:\n    three fi"),
        (520.0, "les: sample.py\n"),
    )
    metrics = metrics_for(capture, Marks(result=r"^run", meaningful=r"three files"))
    assert metrics["first_meaningful_ms"] == 520.0
    assert metrics["first_feedback_ms"] == 500.0
    assert metrics["updates"] == 2


def test_the_pty_runner_records_timestamps_and_the_exit_code(tmp_path: Path):
    """The one place the audit talks to a real terminal: prove it captures, in order."""
    capture = run_pty(
        ["/bin/sh", "-c", "printf 'first\\n'; sleep 0.15; printf 'second\\n'"],
        cwd=tmp_path,
        env={"PATH": "/usr/bin:/bin", "TERM": "dumb"},
        timeout_s=20,
    )
    assert capture.exit_code == 0
    assert not capture.timed_out
    assert [at for at, _ in capture.events] == sorted(at for at, _ in capture.events)
    text = visible(capture.text)
    assert "first" in text and "second" in text
    timings = metrics_for(capture, Marks(result=r"second", meaningful=r"second"))
    assert timings["first_feedback_ms"] is not None
    assert timings["first_feedback_ms"] < timings["first_meaningful_ms"] or (
        timings["first_feedback_ms"] == timings["first_meaningful_ms"]
    )


# --------------------------------------------------------------------------- #
# 2. the renderer's bounds                                                    #
# --------------------------------------------------------------------------- #


class Clock:
    """A hand-wound clock — the tests never sleep."""

    def __init__(self, now: float = 1000.0) -> None:
        self.now = float(now)

    def __call__(self) -> float:
        return self.now

    def tick(self, seconds: float) -> None:
        self.now += float(seconds)


def _renderer(clock: Clock, *, enabled: bool = True, theme: Any = PLAIN,
              **kwargs: Any) -> tuple[RunProgress, io.StringIO]:
    stream = io.StringIO()
    progress = RunProgress(
        stream, enabled=enabled, theme=theme, clock=clock, keepalive_thread=False, **kwargs
    )
    return progress, stream


def test_the_acknowledgment_line_is_immediate_and_unbounded():
    """Rule 1: the first line is written at once, even with 30 events in the same instant."""
    clock = Clock()
    progress, stream = _renderer(clock)
    progress.begin("agent run · \"do the thing\"")
    for index in range(30):
        progress.event("tool.start", name=f"tool_{index}", arguments={"path": f"f{index}"})
    progress.close()
    written = stream.getvalue()
    assert "working" in written and "do the thing" in written
    assert written.splitlines()[0].startswith("  >  working")  # plain mode, ASCII only
    assert progress.timeline.entries[0][0] == 0.0  # the first line cost no clock at all
    # rule 3: 31 events inside one interval cannot render 31 lines
    assert len(progress.timeline.entries) <= 3


def test_the_felt_latency_budget_catches_a_frozen_run_only():
    """The gate exists to catch "silent again", not a slow machine: only the owned journeys."""
    def summary(**kwargs: Any) -> dict[str, Any]:
        return {name: {"p50": value} for name, value in kwargs.items()}

    document = {
        "journeys": {
            "session-start": {"summary": summary(first_feedback_ms=9999, longest_silence_ms=9999)},
            "question": {"summary": summary(first_feedback_ms=445.0, longest_silence_ms=445.0)},
            "edit": {"summary": summary(first_feedback_ms=445.0, longest_silence_ms=445.0)},
            "test-fix": {"summary": summary(first_feedback_ms=409.0, longest_silence_ms=596.0)},
        }
    }
    assert budget_violations(document) == []
    # the pre-fix shape: first feedback at the very end of a 3.5 s run
    document["journeys"]["test-fix"]["summary"] = summary(
        first_feedback_ms=3539.0, longest_silence_ms=3539.0
    )
    breaches = budget_violations(document)
    assert {item["metric"] for item in breaches} == {"first_feedback_ms", "longest_silence_ms"}
    assert all(item["journey"] == "test-fix" for item in breaches)
    assert document["journeys"]["session-start"]["summary"]["first_feedback_ms"]["p50"] == 9999


def test_a_folded_burst_still_names_what_was_folded():
    """The collapse keeps the identities, deduplicated and capped — never just a count."""
    clock = Clock()
    progress, stream = _renderer(clock, min_interval_ms=100)
    progress.begin("work")
    for name in ("terminal", "terminal", "patch", "read_file", "search_files", "write_text"):
        progress.event("tool.start", name=name)
    clock.tick(0.101)
    progress.event("tool.start", name="last")
    text = "\n".join(line for _, line in progress.timeline.entries)
    assert "+6 events: terminal, patch, read_file, +2 more" in text
    progress.close()
    assert "terminal" in stream.getvalue()


def test_the_render_rate_is_bounded_and_the_folded_events_are_named():
    clock = Clock()
    progress, stream = _renderer(clock, min_interval_ms=100)
    progress.begin("work")
    rendered_first = len(progress.timeline.entries)
    for index in range(50):
        progress.event("tool.start", name=f"t{index}")
    # nothing inside the interval reached the terminal, and the folds were counted
    assert len(progress.timeline.entries) == rendered_first
    assert progress.timeline.suppressed == 50
    clock.tick(0.101)
    progress.event("tool.start", name="after_the_interval")
    assert len(progress.timeline.entries) == rendered_first + 1
    assert "+50 events" in progress.timeline.entries[-1][1]
    progress.close()
    assert "after_the_interval" in stream.getvalue()


def test_a_plain_stream_never_carries_an_escape_sequence():
    clock = Clock()
    progress, stream = _renderer(clock, theme=PLAIN)
    progress.begin("work")
    clock.tick(1)
    progress.event("tool.end", name="terminal", duration_ms=12, ok=True)
    clock.tick(1)
    progress.settle("answered", "answered · 900 ms")
    progress.close()
    assert "\x1b" not in stream.getvalue()


def test_a_rich_stream_uses_the_marks_and_rewrites_the_keep_alive_in_place():
    clock = Clock()
    progress, stream = _renderer(clock, theme=RICH, keepalive_s=2.0)
    progress.begin("work")
    clock.tick(1)
    progress.event("tool.start", name="terminal", arguments={"command": "pytest -q"})
    clock.tick(2.5)
    first = progress.tick()
    assert "still working" in first
    assert "\r" in stream.getvalue()  # in place on a rich stream, never a second line
    progress.close()


def test_the_keep_alive_is_bounded_and_labelled_stalled():
    """Rule 4: at most one line per window, and the label tells the truth about silence."""
    from skaldir.streaming import STALL_S

    clock = Clock()
    progress, stream = _renderer(clock, keepalive_s=0.5)
    progress.begin("work")
    progress.event("tool.start", name="terminal")
    clock.tick(0.4)
    assert progress.tick() == ""  # inside the window: nothing at all
    clock.tick(0.2)
    assert "still working" in progress.tick()  # inside the stall window: a plain keep-alive
    assert progress.timeline.stalled_lines == 0
    rendered = len(progress.timeline.entries)
    clock.tick(0.4)
    assert progress.tick() == ""  # again inside the window, counted from the last render
    clock.tick(STALL_S)  # no real event for longer than the stall threshold
    stalled = progress.tick()
    assert "no new event" in stalled
    assert progress.timeline.stalled_lines == 1
    assert len(progress.timeline.entries) > rendered
    # a real event resets the silence and therefore the label
    clock.tick(0.6)
    progress.event("tool.end", name="terminal", duration_ms=5, ok=True)
    clock.tick(0.2)
    assert "no new event" not in progress.tick()
    progress.close()


def test_a_disabled_renderer_writes_nothing_at_all():
    clock = Clock()
    progress, stream = _renderer(clock, enabled=False)
    progress.begin("work")
    progress.event("tool.start", name="x")
    clock.tick(5)
    progress.tick()
    progress.settle("answered")
    counters = progress.close()
    assert stream.getvalue() == ""
    assert counters["rendered"] == 0
    # still counted: the audit reads these even when nothing may be written
    assert counters["suppressed"] >= 3


def test_close_flushes_the_folded_tail_and_adds_no_poll_interval():
    clock = Clock()
    progress, stream = _renderer(clock, min_interval_ms=500)
    progress.begin("work")
    progress.event("tool.end", name="terminal", duration_ms=1, ok=True)
    progress.settle("answered", "answered · 1 ms")
    progress.close()
    written = stream.getvalue()
    assert "answered · 1 ms" in written  # the verdict is never folded away
    assert progress.timeline.folded >= 1


def test_the_loop_callback_protocol_is_the_mapping_shape():
    """``progress(kind, fields)`` — the shape ``AgentLoop._note`` sends."""
    clock = Clock()
    progress, _ = _renderer(clock)
    kinds: list[str] = []

    class Sink:
        def __call__(self, kind: str, fields: dict[str, Any]) -> None:
            kinds.append(kind)
            progress.event(kind, fields)

    progress.begin("work")
    sink = Sink()
    sink("model.request", {"step": 1, "messages": 2, "tool_names": ["read_text"]})
    sink("tool.start", {"name": "read_text", "arguments": {"path": "x"}})
    clock.tick(1)
    sink("tool.end", {"name": "read_text", "duration_ms": 3, "ok": True})
    sink("model.response", {"step": 1, "duration_ms": 5, "tool_calls": 0, "text_chars": 4})
    progress.close()
    assert kinds == ["model.request", "tool.start", "tool.end", "model.response"]


def test_for_args_decides_from_the_stream_and_the_flags():
    quiet = type("A", (), {"quiet": True, "progress": True})()
    assert for_args(quiet, stream=io.StringIO()).enabled is False
    forced = type("A", (), {"quiet": False, "progress": True})()
    assert for_args(forced, stream=io.StringIO()).enabled is True
    auto_pipe = type("A", (), {"quiet": False, "progress": None})()
    assert for_args(auto_pipe, stream=io.StringIO()).enabled is False


# --------------------------------------------------------------------------- #
# 3. the wiring                                                               #
# --------------------------------------------------------------------------- #


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "hello.txt").write_text("hello world\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def state(tmp_path: Path) -> Path:
    directory = tmp_path / "state"
    directory.mkdir()
    return directory


class FakeClient:
    """A model client that returns prepared ``ChatResult`` objects — no sockets.

    The suite's HTTP stub (``StubEndpoint``) is faithful but its blocking keep-alive
    handler makes ``__exit__`` hang in this environment; the loop is what is under test
    here, and it only ever calls ``client.chat(...)``. The real HTTP path is covered by
    the audit-tool test below, which drives the installed CLI in its own process.
    """

    def __init__(self, answers: list[ChatResult], *, delay_s: float = 0.0) -> None:
        self._answers = list(answers)
        self._delay_s = float(delay_s)
        self.requests: list[list[dict[str, Any]]] = []

    def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> ChatResult:
        self.requests.append([dict(message) for message in messages])
        if self._delay_s:
            time.sleep(self._delay_s)
        index = min(len(self.requests) - 1, len(self._answers) - 1)
        return self._answers[index]


def _call(call_id: str, name: str, arguments: dict[str, Any], index: int = 0) -> ChatResult:
    return ChatResult(
        text="",
        finish_reason="tool_calls",
        usage={"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
        tool_calls=[
            ToolCall(
                id=call_id,
                name=name,
                arguments_raw=json.dumps(arguments),
                arguments=dict(arguments),
                index=index,
            )
        ],
    )


def _answer(text: str) -> ChatResult:
    return ChatResult(
        text=text,
        finish_reason="stop",
        usage={"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
    )


def test_the_loop_reports_every_real_event_of_a_two_round_run(workspace: Path, state: Path):
    events: list[tuple[str, dict[str, Any]]] = []
    client = FakeClient(
        [
            _call("call_1", "read_text", {"path": "hello.txt"}),
            _answer("hello.txt says 'hello world'"),
        ]
    )
    loop = AgentLoop(
        client,
        builtin_tools(workspace),
        state_dir=state,
        workspace=workspace,
        limits=LoopLimits(max_steps=4),
        progress=lambda kind, fields: events.append((kind, dict(fields))),
    )
    result = loop.run("what does hello.txt say?")
    assert result.status == STATUS_ANSWERED
    assert [kind for kind, _ in events] == [
        "model.request",
        "model.response",
        "tool.start",
        "tool.end",
        "model.request",
        "model.response",
    ]
    first_request = dict(events[0][1])
    assert first_request["step"] == 1 and first_request["messages"] == 2
    assert first_request["tool_names"] == ["read_text", "list_dir", "sha256_file", "write_text"]
    tool_start = dict(events[2][1])
    assert tool_start["name"] == "read_text"
    assert tool_start["arguments"] == {"path": "hello.txt"}
    assert tool_start["index"] == 1 and tool_start["total"] == 1
    tool_end = dict(events[3][1])
    assert tool_end["ok"] is True and tool_end["duration_ms"] >= 0
    assert dict(events[-1][1])["text_chars"] == len("hello.txt says 'hello world'")
    # the transcript is untouched by the progress sink: the same events, in the same order
    first_line = Path(result.transcript_path).read_text(encoding="utf-8").splitlines()[0]
    transcript = json.loads(first_line)
    assert transcript["kind"] == "run.started"
    assert transcript["run_id"] == result.run_id


def test_a_raising_renderer_cannot_change_a_run(workspace: Path, state: Path):
    """A progress sink is observational: a broken one must not turn an answer into a failure."""

    def boom(kind: str, fields: dict[str, Any]) -> None:
        raise RuntimeError(f"renderer exploded on {kind}")

    client = FakeClient([_call("call_1", "list_dir", {"path": "."}), _answer("done")])
    loop = AgentLoop(
        client,
        builtin_tools(workspace),
        state_dir=state,
        workspace=workspace,
        limits=LoopLimits(max_steps=4),
        progress=boom,
    )
    result = loop.run("what is here?")
    assert result.status == STATUS_ANSWERED
    assert result.answer == "done"
    assert result.exit_code == 0
    assert result.tool_calls == 1


def test_agent_run_writes_progress_to_stderr_and_one_envelope_to_stdout(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    """The end-to-end contract of the fix: stdout stays byte-clean, stderr carries the lines."""
    from skaldir import agent_loop_cli, cli_contract

    # a deliberate delay per round: the render bound is 60 ms, so an instant fake answer
    # would legitimately fold every line into one (and the test would prove nothing)
    client = FakeClient(
        [_call("call_1", "list_dir", {"path": "."}), _answer("two files here")], delay_s=0.08
    )
    before = dict(cli_contract.COMMANDS)
    err = io.StringIO()
    try:
        monkeypatch.setattr(
            agent_loop_cli,
            "_client_and_status",
            lambda args: (client, {"ok": True, "model": "stub-model", "ready": True}),
        )
        monkeypatch.setattr(agent_loop_cli.sys, "stderr", err)
        code = agent_loop_cli.main(
            [
                "run", "what is here?",
                "--workspace", str(workspace),
                "--runtime", str(tmp_path / "runtime"),
                "--tools", "builtin",
                "--progress",
                "--json",
            ]
        )
    finally:
        cli_contract.COMMANDS.clear()
        cli_contract.COMMANDS.update(before)
    assert code == 0
    progress_lines = err.getvalue()
    assert "working" in progress_lines  # the immediate acknowledgment line
    assert "model call" in progress_lines  # one line per model round
    # the tool's own name survives even when its two events were folded into one line:
    # a collapse that only counted would hide *what* happened (the "+N events: names" tail)
    assert "list_dir" in progress_lines
    assert progress_lines.index("working") < progress_lines.index("list_dir")
    assert "answer:" in progress_lines  # the renderer's own result block also went to stderr
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["command"] == "agent.run"
    assert envelope["ok"] is True
    assert envelope["data"]["answer"] == "two files here"


def test_the_audit_tool_measures_a_real_journey_without_a_model(tmp_path: Path):
    """The tool itself, end to end, against the installed CLI and a local stub.

    This is the canary for the whole audit: if ``tools/gv_perceived_speed.py`` stops being
    able to drive the real CLI in a PTY, the numbers in ``MVP/57`` are worthless — so the
    journey is exercised here (one run, one journey, no API, a 50 ms stub).
    """
    import subprocess
    import sys

    repo = Path(__file__).resolve().parents[1]
    out = tmp_path / "evidence"
    completed = subprocess.run(  # noqa: S603 — the repo's own tool
        [
            sys.executable, str(repo / "tools" / "gv_perceived_speed.py"),
            "--label", "pytest", "--runs", "1", "--only", "question",
            "--no-api", "--delay-ms", "50", "--out", str(out),
        ],
        cwd=repo,
        env={
            "PATH": f"{repo / '.venv' / 'bin'}:/usr/bin:/bin",
            "HOME": str(tmp_path),
            "SKALDIR_HOME": str(tmp_path / "home"),
            "GODSV_HOME": str(tmp_path / "home"),
        },
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    document = json.loads((out / "metrics-pytest.json").read_text(encoding="utf-8"))
    entry = document["journeys"]["question"]
    summary = entry["summary"]
    assert summary["first_feedback_ms"]["p50"] is not None
    assert summary["completion_ms"]["p50"] is not None
    assert summary["updates"]["p50"] >= 1
    run = entry["runs"][0]
    assert run["exit_code"] == 0
    # the fix's own contract: the first feedback must arrive *before* the run is over
    assert run["first_feedback_ms"] < run["completion_ms"]
    assert run["first_meaningful_ms"] is not None
    assert entry["notes"][0]["workspace"]
    assert document["tree"]["module_file"].endswith("skaldir/__init__.py")
