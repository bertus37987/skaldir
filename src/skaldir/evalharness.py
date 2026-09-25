"""Deterministic eval harness — canned episodes through the V0 pipeline.

Runs monitor + voice over scripted event sequences (no LLM, no network, no
randomness) and checks expectations per episode — critically including
**silence on healthy work** (precision over recall, CORE-PLAN §1/§6).

``skaldir eval`` prints a per-episode PASS/FAIL report and exits non-zero
when an episode fails; the pytest suite runs the same harness, so CI covers
precision *and* recall of the step detectors.
"""

from __future__ import annotations

import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from .context import ContextBuilder
from .demo import SCRIPT as DEMO_SCRIPT
from .events import EventStore
from .guard import evaluate_completion, find_contradiction
from .monitor import Monitor, Signal
from .pact import Pact
from .pause import PauseStore, VoicePause
from .research import ResearchResult
from .voice import Voice

RawEvent = tuple[str, dict]

EpisodeEvents = list[RawEvent]


def _tc(name: str, **args) -> RawEvent:
    return ("tool_call", {"name": name, "args": args})


def _tr(**payload) -> RawEvent:
    return ("tool_result", payload)


def _edit(path: str, added: str) -> RawEvent:
    return ("file_edit", {"path": path, "added": added})


@dataclass
class Episode:
    name: str
    events: EpisodeEvents
    expect_signals: list[str] = field(default_factory=list)
    min_hints: int = 0
    silent: bool = False
    scope_paths: list[str] | None = None
    example_pact: bool = False
    exact_signals: int | None = None
    completion_text: str | None = None
    expect_guard: str | None = None
    expect_contradiction: bool = False
    expect_pause_delivered: bool = False
    context_budget: int | None = None


@dataclass
class EpisodeResult:
    name: str
    passed: bool
    signal_kinds: list[str]
    hint_kinds: list[str]
    notes: list[str] = field(default_factory=list)

    def render(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        detail = ", ".join(self.notes) if self.notes else f"{len(self.hint_kinds)} Hints"
        return f"[{status}] {self.name} — {detail}"


def _healthy_episode() -> Episode:
    events: EpisodeEvents = []
    for command in ("ls", "pytest -q", "ruff check .", "git status", "pytest tests/test_a.py"):
        events.append(_tc("terminal", command=command))
        events.append(_tr(exit_code=0, stdout="ok"))
    events.append(_edit("src/skaldir/events.py", "def helper():\n    return 1\n"))
    return Episode("healthy_work", events, silent=True)


def _loop_episode() -> Episode:
    events: EpisodeEvents = []
    for _ in range(4):
        events.append(_tc("terminal", command="pytest -q"))
        events.append(_tr(exit_code=0, stdout="ok"))
    return Episode("busy_loop", events, expect_signals=["loop"], min_hints=1)


def _error_episode() -> Episode:
    events: EpisodeEvents = []
    for _ in range(3):
        events.append(_tc("terminal", command="python train.py"))
        events.append(_tr(exit_code=1, stderr="RuntimeError: CUDA out of memory"))
    return Episode("error_burst", events, expect_signals=["error_burst"], min_hints=1)


def _slop_episode() -> Episode:
    events: EpisodeEvents = [
        _edit("src/a.py", "x = 1  # TODO: implementieren\n"),
        _edit("src/b.py", "try:\n    y = 1\nexcept:\n    pass\n"),
        _edit("tests/test_c.py", "def test_c():\n    assert True\n"),
    ]
    return Episode(
        "slop_edits",
        events,
        expect_signals=["slop_todo_placeholder", "slop_bare_except", "slop_assert_true"],
        min_hints=2,
    )


def _scope_episode() -> Episode:
    events: EpisodeEvents = [
        _edit("src/ok.py", "def ok():\n    return 1\n"),
        _edit("docs/notes.md", "# nebenbei Notizen\n"),
    ]
    return Episode(
        "scope_drift",
        events,
        expect_signals=["scope_drift"],
        min_hints=1,
        scope_paths=["src/*", "tests/*"],
    )


def _demo_episode() -> Episode:
    return Episode(
        "demo_script",
        [(kind, payload) for _step, kind, payload in DEMO_SCRIPT],
        expect_signals=["error_burst", "loop", "hallucinated_api", "strategy_repeat"],
        min_hints=4,
        example_pact=True,  # demo.py runs Monitor(Pact.example()) — same conditions
        exact_signals=10,  # 9 + strategy_repeat (same error after the same strategy)
    )


def _false_completion_episode() -> Episode:
    events: EpisodeEvents = [_edit("src/app.py", "def f():\n    return 1\n")]
    return Episode(
        "false_completion",
        events,
        completion_text="Ich bin fertig — alles implementiert und getestet.",
        expect_guard="unsupported",
    )


def _unsupported_claim_episode() -> Episode:
    return Episode(
        "unsupported_claim_no_actions",
        [],
        completion_text="Erledigt, die Funktion läuft.",
        expect_guard="unsupported",
    )


def _contradiction_episode() -> Episode:
    error = {"tool": "terminal", "error": "ImportError: cannot import name 'cv_split'"}
    events: EpisodeEvents = [
        _tc("terminal", command="python app.py"),
        _tr(**error),
        ("claim", {"text": "Alles läuft jetzt rund.", "categories": ["works"]}),
        _tc("terminal", command="python app.py"),
        _tr(**error),
    ]
    return Episode("contradiction", events, expect_contradiction=True)


def _tool_error_class_episode() -> Episode:
    events: EpisodeEvents = [
        _tc("terminal", command="curl https://api.example.com"),
        _tr(tool="terminal", error="HTTP 401 Unauthorized: invalid api key"),
    ]
    return Episode(
        "tool_error_classes", events, expect_signals=["tool_error_auth"], min_hints=1
    )


def _stall_episode() -> Episode:
    events: EpisodeEvents = []
    for index in range(14):
        events.append(_tc("terminal", command=f"probe-{index}"))
        events.append(_tr(tool="terminal", error=f"boom {index}"))
    return Episode(
        "progress_stall", events, expect_signals=["stall"], min_hints=1, exact_signals=None
    )


def _dependency_episode() -> Episode:
    events: EpisodeEvents = [_edit("pyproject.toml", 'dependencies = ["newdep>=1"]\n')]
    return Episode(
        "dependency_drift", events, expect_signals=["dependency_change"], min_hints=1
    )


def _revert_episode() -> Episode:
    events: EpisodeEvents = [
        _tc("terminal", command="git checkout -- src/app.py"),
        _tr(tool="terminal", exit_code=0, stdout=""),
    ]
    return Episode("revert_ops", events, expect_signals=["revert"], min_hints=1)


def _pause_episode() -> Episode:
    events: EpisodeEvents = []
    for _ in range(4):
        events.append(_tc("terminal", command="pytest -q"))
        events.append(_tr(tool="terminal", error="AssertionError: 1 != 2"))
    return Episode("pause_flow", events, expect_pause_delivered=True)


def _long_context_episode() -> Episode:
    events: EpisodeEvents = []
    for index in range(40):
        events.append(_tc("terminal", command=f"step-{index}"))
        events.append(_tr(tool="terminal", exit_code=0, stdout=f"ok {index} - {'x' * 80}"))
        events.append(_edit(f"src/mod_{index}.py", "def f():\n    return 1\n" + "y = 2\n" * 10))
    return Episode("long_task_bounded_context", events, context_budget=1500)


class _InstantResearch:
    """Deterministic research double for the pause-flow episode."""

    def run(self, *, session_id, base_step, trigger_kind, trigger_fp, problem):
        return ResearchResult(
            research_id="eval-research",
            session_id=session_id,
            base_step=base_step,
            trigger_kind=trigger_kind,
            trigger_fp=trigger_fp,
            problem=problem,
            direction="Ansatz wechseln",
            avoid="repetition",
            uncertainty="eval",
            status="ok",
        )


def _run_pause_flow(
    store: EventStore, all_signals: list[tuple[int, Signal]], *, last_step: int
) -> bool:
    """Drive the real pause machinery with instant research; deliver once."""
    directory = Path(tempfile.mkdtemp(prefix="godsv_eval_pause_"))
    pause_store = PauseStore(directory / "pauses.db")
    pause = VoicePause(pause_store, "eval-1", research_runner=_InstantResearch(), ttl=30.0)
    try:
        for step, signal in all_signals:
            pause.consider(step=step, signals=[signal], problem=signal.message)
        deadline = time.monotonic() + 2.0
        lease = pause_store.current("eval-1")
        while (
            lease is not None
            and lease["status"] != "ready"
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)
            lease = pause_store.current("eval-1")
        delivered = pause.deliverable(step=last_step + 1, wait=1.0)
        delivered_again = pause.deliverable(step=last_step + 2, wait=0.0)
        return (
            delivered is not None
            and delivered_again is None
            and "voice·pause" in delivered["redirect"]
        )
    finally:
        pause.stop()
        pause_store.close()


def episodes() -> list[Episode]:
    return [
        _healthy_episode(),
        _loop_episode(),
        _error_episode(),
        _slop_episode(),
        _scope_episode(),
        _demo_episode(),
        _false_completion_episode(),
        _unsupported_claim_episode(),
        _contradiction_episode(),
        _tool_error_class_episode(),
        _stall_episode(),
        _dependency_episode(),
        _revert_episode(),
        _pause_episode(),
        _long_context_episode(),
    ]


def run_episode(episode: Episode) -> EpisodeResult:
    """Run one scripted episode through monitor + voice (temp store, no LLM)."""
    handle = tempfile.NamedTemporaryFile(prefix="godsv_eval_", suffix=".db", delete=False)
    handle.close()
    pact = None
    if episode.scope_paths is not None:
        pact = Pact.example()
        pact.scope_paths = list(episode.scope_paths)
    elif episode.example_pact:
        pact = Pact.example()
    monitor = Monitor(pact)
    voice = Voice(budget=5, min_gap=1)
    signal_kinds: list[str] = []
    hint_kinds: list[str] = []
    all_signals: list[tuple[int, Signal]] = []
    notes: list[str] = []
    store = EventStore(handle.name)
    try:
        for step, (kind, payload) in enumerate(episode.events, start=1):
            event = store.append("eval-1", step, kind, payload, ts=float(step))
            signals = monitor.ingest(event)
            signal_kinds.extend(signal.kind for signal in signals)
            all_signals.extend((step, signal) for signal in signals)
            hint = voice.consider(signals, step)
            if hint is not None:
                hint_kinds.append(hint.kind)
        last_step = max(len(episode.events), 1)
        if episode.completion_text is not None:
            verdict = evaluate_completion(store, "eval-1", episode.completion_text, pact=pact)
            if episode.expect_guard is not None and verdict.status != episode.expect_guard:
                notes.append(f"Guard={verdict.status} (expected {episode.expect_guard})")
        if episode.expect_contradiction and find_contradiction(store, "eval-1") is None:
            notes.append("no contradiction detected")
        if episode.expect_pause_delivered and not _run_pause_flow(
            store, all_signals, last_step=last_step
        ):
            notes.append("no pause delivery (exactly once)")
        if episode.context_budget is not None:
            context = ContextBuilder(total_budget=episode.context_budget).build(
                store, "eval-1", step=last_step
            )
            if context.total_chars > episode.context_budget + 120:
                notes.append(f"Kontext {context.total_chars} > Budget {episode.context_budget}")
    finally:
        store.close()
        Path(handle.name).unlink(missing_ok=True)
    missing = [kind for kind in episode.expect_signals if kind not in signal_kinds]
    if missing:
        notes.append("fehlende Signale: " + ", ".join(missing))
    if episode.exact_signals is not None and len(signal_kinds) != episode.exact_signals:
        notes.append(f"signals={len(signal_kinds)} (expected {episode.exact_signals})")
    if episode.silent:
        if hint_kinds:
            notes.append(f"{len(hint_kinds)} hints (expected: silent)")
    elif len(hint_kinds) < episode.min_hints:
        notes.append(f"{len(hint_kinds)} hints (expected ≥{episode.min_hints})")
    return EpisodeResult(
        name=episode.name,
        passed=not notes,
        signal_kinds=signal_kinds,
        hint_kinds=hint_kinds,
        notes=notes,
    )


def run_eval() -> list[EpisodeResult]:
    return [run_episode(episode) for episode in episodes()]


def render_report(results: list[EpisodeResult]) -> str:
    lines = [result.render() for result in results]
    passed = sum(1 for result in results if result.passed)
    lines.append(f"{passed}/{len(results)} Episoden bestanden")
    return "\n".join(lines)
