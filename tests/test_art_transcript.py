"""ART — the vocabulary on a **real** run: glyphs in sequence, from measured events.

This is the proof the round asks for. It does not draw the vocabulary from invented data:
it starts the local OpenAI-compatible stub endpoint (``tests/test_agent_loop.StubEndpoint`` —
no credential, no network), lets the **real** ``agent run`` loop execute **real** tools in a
temporary workspace, reads the run's own transcript back with
``agent_loop.read_transcript()`` and renders the activity lines from those events:

* ``thinking`` — the frame is chosen from the **measured** time between ``run.started`` and
  the first ``model.response`` (the time the model really took), not from a constant;
* ``read`` / ``write`` — one line per real ``tool.call`` event, carrying the ``duration_ms``
  the loop measured and the path the model really asked for;
* ``verify`` — the run's own ``status`` decides the mark (``answered`` verifies); a bound
  (``max_steps``) becomes ``UNKNOWN``, never a pass.

The last test prints the capture that is committed under ``outputs/art-activity/`` and quoted
in ``MVP/67-activity-art.md``; it is the same data the assertions above use.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from test_agent_loop import StubEndpoint, answer, tool_step

from skaldir import agent_loop_cli, art, render
from skaldir import theme as T
from skaldir.agent_loop import read_transcript

TASK = "read hello.txt, record what it says, and answer with the digest"


def rich() -> T.Theme:
    return T.Theme(mode=T.MODE_RICH, depth=T.DEPTH_256, glyphs=T.GLYPHS_UNICODE,
                   width=80, height=24, source="explicit")


def plain() -> T.Theme:
    return T.Theme(mode=T.MODE_PLAIN, depth=T.DEPTH_NONE, glyphs=T.GLYPHS_ASCII,
                   width=80, height=24, source="explicit")


def bare(text: str) -> str:
    return render.strip_ansi(text)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    directory = tmp_path / "workspace"
    directory.mkdir()
    (directory / "hello.txt").write_text("hello world\n", encoding="utf-8")
    # a file big enough that the tools' own durations are measurable (a few ms, not zero):
    # the vocabulary prints the measurement, so the capture should not be all zeros.
    (directory / "notes.md").write_text(NOTES, encoding="utf-8")
    return directory


@pytest.fixture
def runtime(tmp_path: Path) -> Path:
    directory = tmp_path / "runtime"
    directory.mkdir()
    return directory


#: ~8 MB of text, so ``sha256_file`` takes a measurable handful of milliseconds.
NOTES = "skaldir activity vocabulary\n" * (12_000 * 24)

#: The scripted "model": read the workspace, hash the file, then answer.
DEFAULT_STEPS: list[dict[str, Any]] = [
    tool_step(("call_1", "read_text", {"path": "hello.txt"}), text="reading it first"),
    tool_step(("call_2", "sha256_file", {"path": "notes.md"}), text="then hashing the notes"),
    answer("hello.txt holds 'hello world'; notes.md is hashed"),
]


def real_run(workspace: Path, runtime: Path, capsys: Any, *,
             steps: list[dict[str, Any]] | None = None,
             max_steps: int | None = None, minimum_tools: int = 2
             ) -> tuple[dict[str, Any], dict[str, Any]]:
    """One real run: the printed envelope plus the transcript read back from disk."""
    scripted = steps if steps is not None else list(DEFAULT_STEPS)
    argv = ["run", TASK, "--json", "--workspace", str(workspace), "--runtime", str(runtime),
            "--tools", "builtin", "--stream"]
    if max_steps is not None:
        argv += ["--max-steps", str(max_steps)]
    with StubEndpoint(scripted) as stub:
        code = agent_loop_cli.main([*argv, "--base-url", stub.base_url, "--model", "stub-model"])
        captured = capsys.readouterr().out
    # 0 = answered, 3 = a bound stopped the loop (the product's own exit for incomplete)
    assert code in {0, 3}, captured
    envelope = json.loads(captured)
    transcript = read_transcript(envelope["data"]["transcript_path"])
    assert transcript["tool_call_count"] == len(transcript["tool_calls"]) >= minimum_tools
    return envelope, transcript


def activity_records(transcript: dict[str, Any]) -> list[dict[str, Any]]:
    """The run's real events turned into vocabulary records (the one mapping, in one place).

    ``run.started`` + the first ``model.response`` → the thinking line, whose frame comes from
    the *measured* wait; every ``tool.call`` → a tool/read/write/edit line carrying the
    duration the loop measured; ``run.finished`` → the verify line, whose mark is the run's own
    status.
    """
    header = next((event for event in transcript["events"] if event.get("kind") == "run.started"),
                  {})
    started = _stamp(header.get("at"))
    first_answer = next((event for event in transcript["events"]
                         if event.get("kind") == "model.response"), {})
    thinking_ms = max(0, _stamp(first_answer.get("at")) - started) if first_answer else 0
    records: list[dict[str, Any]] = [
        {"activity": "thinking", "text": "reading the workspace", "elapsed_ms": thinking_ms},
    ]
    for event in transcript["tool_calls"]:
        record = dict(event)
        record["activity"] = art.activity_for(str(event.get("name") or ""))
        record["ms"] = event.get("duration_ms")
        records.append(record)
    status = str(transcript.get("status") or "")
    if status == "answered":
        records.append({"activity": "verify", "status": "ok",
                        "reason": transcript.get("answer") or ""})
    else:
        records.append({"activity": "verify", "status": "unknown",
                        "text": transcript.get("reason") or status,
                        "reason": transcript.get("answer") or ""})
    return records


def _stamp(value: Any) -> int:
    """An event's ISO timestamp in milliseconds (``0`` when there is none)."""
    text = str(value or "")
    if not text:
        return 0
    try:
        return int(datetime.fromisoformat(text).timestamp() * 1000)
    except ValueError:
        return 0


# --------------------------------------------------------------------------- #
# the run itself                                                               #
# --------------------------------------------------------------------------- #


def test_the_real_run_calls_the_tools_the_vocabulary_knows(workspace, runtime, capsys):
    envelope, transcript = real_run(workspace, runtime, capsys)
    assert envelope["ok"] is True and envelope["command"] == "agent.run"
    assert transcript["status"] == "answered"
    assert transcript["tool_call_count"] == 2
    assert [event["name"] for event in transcript["tool_calls"]] == ["read_text", "sha256_file"]
    assert all(event["duration_ms"] >= 0 for event in transcript["tool_calls"])
    assert transcript["tool_calls"][1]["ok"] is True


def test_three_glyphs_appear_in_sequence_from_measured_events(workspace, runtime, capsys):
    """thinking → read → the boxed tool mark → verify: four families, in the run's own order."""
    _envelope, transcript = real_run(workspace, runtime, capsys)
    lines = art.transcript(activity_records(transcript), theme=plain()).split("\n")
    assert len(lines) == 4                       # thinking, read, tool, verify
    heads = [line.strip().split("  ")[0] for line in lines]
    assert heads == [".", "[=]", "[>_]", "+"]
    assert lines[0].endswith("reading the workspace")
    assert lines[1].startswith("  [=]  hello.txt")
    assert lines[2].startswith("  [>_]  sha256_file")
    assert lines[3].startswith("  +  verified") and "hello world" in lines[3]


def test_the_durations_in_the_lines_are_the_durations_measured(workspace, runtime, capsys):
    _envelope, transcript = real_run(workspace, runtime, capsys)
    lines = art.transcript(activity_records(transcript), theme=plain()).split("\n")
    for line, event in zip(lines[1:3], transcript["tool_calls"], strict=True):
        assert line.endswith(f"{event['duration_ms']} ms")


def test_the_thinking_frame_comes_from_the_measured_wait(workspace, runtime, capsys):
    _envelope, transcript = real_run(workspace, runtime, capsys)
    waited = activity_records(transcript)[0]["elapsed_ms"]
    assert waited >= 0
    # the frame is a function of the measurement, and the whole strip is reachable
    assert art.frame_index("thinking", waited) < 4
    assert [art.frame("thinking", index, theme=plain()) for index in range(4)] == \
        [".", "o", "O", "o"]
    assert bare(art.thinking("reading the workspace", elapsed_ms=waited, theme=rich())) \
        .startswith(art.frames("thinking", theme=rich())[art.frame_index("thinking", waited)])


def test_a_bound_is_unknown_and_never_a_pass(workspace, runtime, capsys):
    """The loop's own verdict decides the mark: incomplete is UNKNOWN, not a check."""
    steps = [tool_step(("call_1", "read_text", {"path": "hello.txt"}))]
    _envelope, transcript = real_run(workspace, runtime, capsys, steps=steps, max_steps=1,
                                     minimum_tools=1)
    assert transcript["status"] == "incomplete"
    lines = art.transcript(activity_records(transcript), theme=plain()).split("\n")
    assert lines[-1].strip().startswith("?  UNKNOWN")
    assert "verified" not in lines[-1]


def test_the_transcript_renders_in_unicode_and_in_ascii(workspace, runtime, capsys):
    _envelope, transcript = real_run(workspace, runtime, capsys)
    records = activity_records(transcript)
    unicode_lines = art.transcript(records, theme=rich()).split("\n")
    ascii_lines = art.transcript(records, theme=plain()).split("\n")
    for drawn in unicode_lines + ascii_lines:
        assert render.width(drawn) <= art.LINE_BUDGET
    for drawn in ascii_lines:
        assert drawn.isascii() and "\x1b" not in drawn
    assert bare(unicode_lines[1].strip()).split("  ")[0] == art.mark("read", theme=rich())
    assert ascii_lines[1].strip().split("  ")[0] == art.mark("read", theme=plain())


def test_the_real_activity_transcript_is_printed_for_the_record(workspace, runtime, capsys):
    """The capture quoted in ``MVP/67-activity-art.md`` — this test *is* the raw output."""
    envelope, transcript = real_run(workspace, runtime, capsys)
    records = activity_records(transcript)
    print("")
    print("--- the real events this transcript is drawn from (run "
          f"{envelope['data']['run_id']}) ---")
    for record in records:
        shown = {key: value for key, value in record.items()
                 if key not in {"arguments", "result_preview", "policy_reasons"}}
        print(json.dumps(shown, ensure_ascii=False))
    print("")
    print("--- the vocabulary rendering them, unicode ---")
    print(art.transcript(records, theme=rich()))
    print("")
    print("--- the same lines, plain ASCII ---")
    print(art.transcript(records, theme=plain()))
    print("")
    print(f"run {envelope['data']['run_id']}: status={transcript['status']} "
          f"steps={transcript['steps']} tool_calls={transcript['tool_call_count']} "
          f"run_duration={transcript['duration_ms']} ms")
    assert not re.search(r"[\u25c7\u25c8\u25c6]", art.transcript(records, theme=plain()))
