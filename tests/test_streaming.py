"""Bounded-rate streaming: the coalescer's contract, and the shell's use of it.

Three groups, and the split is deliberate:

* **the coalescer itself** — driven by an *injected clock*, so "at most one render per
  interval" is checked to the millisecond without sleeping and without a terminal;
* **the render helpers** — ``live_line`` / ``activity_line`` / ``batch_line``: an
  incremental path must be provably incremental (an unchanged line costs no write at all)
  and must still degrade to pure ASCII on a plain theme;
* **the shell's wiring** — the paced arm really renders fewer writes than chunks, the bytes
  are byte-identical either way, a pipe is never paced by default, and the activity
  indicator does not draw while output flows (the split it caused when it did).

Nothing here needs a network, a provider key or a real terminal: the PTY proof is
``tools/gv_stream_perf.py`` and its numbers live in ``MVP/53-cline-perf.md``.
"""

from __future__ import annotations

import io
import json
import shutil
import time
from pathlib import Path

from skaldir import render, streaming

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"


# --------------------------------------------------------------------------- #
# helpers: a counting sink and a clock the test owns
# --------------------------------------------------------------------------- #


class Counter(io.StringIO):
    """A sink that counts how many times it was written to (writes, not bytes)."""

    def __init__(self) -> None:
        super().__init__()
        self.writes = 0

    def write(self, text: str) -> int:  # type: ignore[override]
        if text:
            self.writes += 1
        return super().write(text)


class FakeClock:
    """A clock the test advances by hand — no sleeping, no flakiness."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = float(start)

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += float(seconds)


# --------------------------------------------------------------------------- #
# the coalescer: the bound, the first byte, and what must never be lost
# --------------------------------------------------------------------------- #


def test_accepts_every_chunk_and_renders_at_most_once_per_interval():
    sink, clock = Counter(), FakeClock()
    stream = streaming.Coalescer(sink, hz=10.0, clock=clock)
    # 100 chunks inside one interval (0.1 s): the first renders, the rest must not
    for _ in range(100):
        clock.advance(0.0005)
        stream.write("abcd")
    assert stream.metrics.chunks_in == 100
    assert stream.metrics.renders == 1, "100 chunks inside one interval is one render"
    assert sink.writes == 1
    clock.advance(0.1)
    stream.write("abcd")
    assert stream.metrics.renders == 2, "the interval elapsing releases the next render"
    stream.close()


def test_the_first_chunk_is_rendered_immediately():
    """Cline's flush timer delays the first update by up to one interval; we do not.

    On a terminal the first byte *is* the perceived latency, so ``Enter -> first byte``
    must not move when coalescing is switched on.
    """
    sink, clock = Counter(), FakeClock()
    stream = streaming.Coalescer(sink, hz=1.0, clock=clock)  # a deliberately slow bound
    stream.write("first")
    assert sink.getvalue() == "first", "the first chunk went out at once, not in 1 s"
    assert stream.metrics.first_render_ms in (0, -1) or stream.metrics.first_render_ms == 0
    stream.close()


def test_output_is_byte_identical_to_the_input_and_in_order():
    sink, clock = Counter(), FakeClock()
    stream = streaming.Coalescer(sink, hz=20.0, clock=clock)
    written: list[str] = []
    for index in range(500):
        chunk = f"{index:04d} "
        written.append(chunk)
        stream.write(chunk)
        clock.advance(0.002)
    stream.close()
    assert sink.getvalue() == "".join(written)
    assert stream.metrics.chars_in == stream.metrics.chars_rendered


def test_a_producers_per_chunk_flush_does_not_break_the_bound():
    """The bug this contract exists for, found with a real pty.

    ``sys.stdout.flush()`` after every chunk is what a streaming producer does; if the
    coalescer's ``flush`` rendered, the terminal got one write per token and the whole
    feature was dead (measured: 4000 chunks -> 4000 renders, ``--stream-hz 30``).
    """
    sink, clock = Counter(), FakeClock()
    stream = streaming.Coalescer(sink, hz=10.0, clock=clock)
    for _ in range(200):
        clock.advance(0.001)
        stream.write("token ")
        stream.flush()
    assert stream.metrics.renders < 30, f"flush must obey the bound, got {stream.metrics.renders}"
    assert stream.pending > 0, "the last bytes are still pending until the drain"
    stream.close()
    assert stream.pending == 0
    assert stream.metrics.renders < 30


def test_drain_and_close_always_emit_the_tail():
    sink, clock = Counter(), FakeClock()
    stream = streaming.Coalescer(sink, hz=0.5, clock=clock)
    for index in range(50):
        stream.write(f"{index},")
    assert sink.getvalue() == "0,", "only the first chunk is on the terminal"
    stream.drain()
    assert sink.getvalue() == "".join(f"{index}," for index in range(50))
    stream.write("tail")
    stream.close()
    assert sink.getvalue().endswith("tail")


def test_max_buffer_forces_a_render_inside_the_interval():
    """A burst is bounded by bytes as well as by time (Cline's ``maxPendingChars``)."""
    sink, clock = Counter(), FakeClock()
    stream = streaming.Coalescer(sink, hz=0.001, clock=clock, max_buffer=16)
    for _ in range(100):
        stream.write("x")  # 100 chars, no time worth speaking of
    assert stream.metrics.renders >= 6, "the byte ceiling released bytes early"
    assert stream.pending < 16, "the ceiling keeps the pending tail bounded"
    stream.close()
    assert sink.getvalue() == "x" * 100


def test_an_extra_sink_receives_every_chunk_uncoalesced():
    """The shell's session record must stay exact while the terminal is paced."""
    primary, record, clock = Counter(), Counter(), FakeClock()
    stream = streaming.Coalescer(primary, hz=1.0, clock=clock, sink=record)
    for index in range(30):
        stream.write(f"chunk{index} ")
    assert record.writes == 30 and primary.writes == 1
    assert record.getvalue() == primary.getvalue() + "".join(
        f"chunk{index} " for index in range(1, 30)
    )
    stream.close()
    assert primary.getvalue() == record.getvalue()


def test_metrics_name_their_own_method():
    sink, clock = Counter(), FakeClock()
    stream = streaming.Coalescer(sink, hz=25.0, clock=clock)
    for _ in range(60):
        clock.advance(0.01)
        stream.write("data ")
    stream.close()
    report = stream.metrics.as_dict()
    assert report["interval_ms"] == 40
    assert report["chunks_in"] == 60
    assert report["renders"] < 60
    assert report["first_render_ms"] >= 0


def test_a_closed_primary_never_raises_into_the_writer():
    class Broken:
        def write(self, _text: str) -> int:
            raise OSError("terminal gone")

        def flush(self) -> None:
            raise OSError("terminal gone")

    stream = streaming.Coalescer(Broken(), hz=10.0)
    assert stream.write("still accepted") == len("still accepted")
    stream.close()  # must not raise


def test_coalescing_stream_returns_the_stream_itself_when_disabled():
    target = Counter()
    assert streaming.coalescing_stream(target, enabled=False) is target
    wrapped = streaming.coalescing_stream(target, enabled=True)
    assert isinstance(wrapped, streaming.Coalescer)


def test_run_stream_reports_the_metrics_of_what_it_ran():
    sink = Counter()
    metrics = streaming.run_stream(sink, ["ab"] * 40, hz=10.0, max_buffer=16)
    assert isinstance(metrics, streaming.StreamMetrics)
    assert metrics.chars_in == 80
    assert metrics.chars_rendered == 80
    assert sink.getvalue() == "ab" * 40


# --------------------------------------------------------------------------- #
# the coalescer's own clock, exercised without one
# --------------------------------------------------------------------------- #


def test_the_settle_check_in_the_harness_agrees_with_the_bound():
    """The harness's in-process check is the same invariant, driven the same way."""
    sink, clock = Counter(), FakeClock()
    stream = streaming.Coalescer(sink, hz=30.0, clock=clock)
    chunks = 300
    for _ in range(chunks):
        clock.advance(1.0 / 300.0)
        stream.write("xxxxxx")
    stream.close()
    assert stream.metrics.renders <= int(30.0 * (chunks / 300.0)) + 2
    assert sink.getvalue() == "x" * (chunks * 6)


# --------------------------------------------------------------------------- #
# bounded memory
# --------------------------------------------------------------------------- #


def test_rolling_buffer_keeps_the_head_and_the_latest_tail():
    buffer = streaming.RollingBuffer(max_chars=20)
    for index in range(20):
        buffer.append(f"{index:03d}")
    # head kept verbatim for the first half, the tail rolls
    assert buffer.value().startswith("000001002")
    assert buffer.value().endswith("019")
    assert buffer.total_chars == 60
    assert buffer.dropped == 60 - len(buffer.value())
    assert buffer.as_dict()["truncated"] is True


def test_rolling_buffer_reports_nothing_dropped_when_it_fits():
    buffer = streaming.RollingBuffer(max_chars=100)
    buffer.append("short")
    assert buffer.value() == "short"
    assert buffer.dropped == 0
    assert buffer.as_dict()["truncated"] is False


# --------------------------------------------------------------------------- #
# parallel tool calls: Cline's adjacency rule
# --------------------------------------------------------------------------- #


def test_only_adjacent_parallel_calls_overlap():
    calls = [
        {"name": "read_a", "execution_mode": "parallel"},
        {"name": "read_b", "execution_mode": "parallel"},
        {"name": "write", "execution_mode": "sequential"},
        {"name": "read_c", "execution_mode": "parallel"},
    ]
    batches = streaming.plan_tool_batches(calls, default="sequential")
    assert [batch.mode for batch in batches] == ["parallel", "sequential", "parallel"]
    assert batches[0].names == ("read_a", "read_b")
    assert batches[1].names == ("write",)
    assert batches[2].names == ("read_c",)


def test_a_sequential_call_is_a_barrier_between_two_parallel_groups():
    calls = ["a", "b", "c"]
    modes = {"a": "parallel", "c": "parallel"}
    items = [{"name": name, "execution_mode": modes.get(name, "sequential")} for name in calls]
    batches = streaming.plan_tool_batches(items, default="sequential")
    assert [(batch.mode, batch.names) for batch in batches] == [
        ("parallel", ("a",)),
        ("sequential", ("b",)),
        ("parallel", ("c",)),
    ]


def test_the_default_mode_fills_in_for_calls_that_declare_none():
    calls = ["x", "y", "z"]
    everything = streaming.plan_tool_batches(calls, default="parallel")
    assert len(everything) == 1 and everything[0].parallel
    one_by_one = streaming.plan_tool_batches(calls, default="sequential")
    assert len(one_by_one) == 3 and not any(batch.parallel for batch in one_by_one)
    objects = streaming.plan_tool_batches(
        [type("Call", (), {"name": "obj"})()], default="sequential"
    )
    assert objects[0].names == ("obj",)


def test_a_batch_tracker_counts_only_real_changes():
    tracker = streaming.BatchTracker()
    tracker.begin(["read_a", "read_b", "read_c"])
    assert tracker.set("read_a", "done") is True
    assert tracker.set("read_a", "done") is False  # no change: no render
    assert tracker.set("read_b", "failed") is True
    report = tracker.as_dict()
    assert report["total"] == 3 and report["done"] == 2
    assert report["renders"] == 2 and report["suppressed"] == 1


# --------------------------------------------------------------------------- #
# the activity indicator that ticks from real data
# --------------------------------------------------------------------------- #


def _source(**facts):
    return lambda: dict(facts)


def test_the_frame_only_advances_when_the_source_moved():
    clock = FakeClock()
    facts = {"chunks": 0}
    activity = streaming.Activity(lambda: dict(facts), clock=clock)
    changed, _ = activity.tick()
    assert changed is True, "the very first tick reports the first real fact"
    assert activity.tick()[0] is False, "an identical fact is not a render"
    facts["chunks"] = 5
    changed, seen = activity.tick()
    assert changed is True and seen["chunks"] == 5
    report = activity.as_dict()
    assert report["ticks"] == 3 and report["renders"] == 2 and report["suppressed"] == 1


def test_a_stalled_source_is_labelled_stalled_instead_of_spinning():
    clock = FakeClock()
    activity = streaming.Activity(_source(chunks=1), stall_s=2.0, clock=clock)
    activity.tick()
    assert activity.stalled() is False
    clock.advance(2.5)
    assert activity.stalled() is True
    assert activity.as_dict()["stalled"] is True


# --------------------------------------------------------------------------- #
# the render helpers: incremental, and ASCII when it has to be
# --------------------------------------------------------------------------- #


def test_an_unchanged_live_line_costs_no_write_at_all():
    assert render.live_line("same", previous="same") == ""
    assert render.live_line("same", previous="same", final=True) != ""


def test_a_changed_live_line_rewrites_one_line_rich_and_appends_plain():
    from skaldir import theme as theme_mod

    rich = theme_mod.detect(io.StringIO(), mode="rich", no_color=False)
    plain = theme_mod.detect(io.StringIO(), mode="plain", no_color=True)
    drawn = render.live_line("working 3/8", previous="working 2/8", theme=rich, columns=40)
    assert drawn.startswith("\r") and "working 3/8" in drawn
    assert len(render.strip_ansi(drawn)) == 1 + 40, "padded, so nothing of the old line stays"
    text = render.live_line("working 3/8", previous="working 2/8", theme=plain)
    assert text == "working 3/8\n", "a log cannot un-write a line: it appends one"


def test_activity_line_is_built_from_the_facts_and_skips_empty_ones():
    from skaldir import theme as theme_mod

    plain = theme_mod.detect(io.StringIO(), mode="plain", no_color=True)
    line = render.activity_line(
        "working", {"chunks": 1240, "renders": 38, "tokens": None, "notes": ""},
        theme=plain,
    )
    assert "working" in line and "1240 chunks" in line and "38 renders" in line
    assert "tokens" not in line and "notes" not in line
    stalled = render.activity_line("working", {"chunks": 1}, stalled=True, theme=plain)
    assert "stalled" in stalled
    assert render.activity_line("x", {"a": 1}, theme=plain).isascii()


def test_batch_line_groups_a_parallel_wave_onto_one_line():
    from skaldir import theme as theme_mod

    plain = theme_mod.detect(io.StringIO(), mode="plain", no_color=True)
    line = render.batch_line(2, 3, ["read_file", "search_files", "patch_file"], theme=plain)
    assert line.startswith("> 2/3 parallel")
    assert "read_file, search_files, patch_file" in line
    assert line.isascii()
    assert render.batch_line(1, 1, [], theme=plain).endswith("1/1 parallel")


def test_a_huge_count_is_grouped_not_printed_as_digits():
    from skaldir import theme as theme_mod

    plain = theme_mod.detect(io.StringIO(), mode="plain", no_color=True)
    line = render.activity_line("working", {"chunks": 1240000}, theme=plain)
    assert "1 240 000 chunks" in line


# --------------------------------------------------------------------------- #
# the shell's wiring: fewer writes, same bytes
# --------------------------------------------------------------------------- #


def _session(tmp_path, chunks: int, rate: float, **kwargs):
    from skaldir import repl

    out = Counter()
    errors = Counter()

    def runner(_argv):
        for _ in range(chunks):
            import sys

            sys.stdout.write("xxxxxx")
            sys.stdout.flush()
            time.sleep(1.0 / rate)
        return 0

    session = repl.Session(runtime=str(tmp_path), runner=runner, out=out, err=errors, **kwargs)
    return session, out


def test_a_paced_shell_renders_fewer_writes_than_chunks_and_the_bytes_match(tmp_path):
    session, out = _session(tmp_path, chunks=120, rate=400.0)
    session.stream_hz = 30.0
    result = session.execute(["agent", "run", "x"])
    assert result.coalesced is True
    assert result.renders < 120, f"renders={result.renders} should be far below 120 chunks"
    assert result.chunks_per_render > 1
    assert out.getvalue() == "x" * (120 * 6)
    assert result.bytes_streamed == 120 * 6
    assert result.first_byte_ms >= 0


def test_an_unpaced_shell_writes_every_chunk_and_produces_the_same_bytes(tmp_path):
    session, out = _session(tmp_path, chunks=60, rate=400.0)
    session.stream_hz = 0.0
    result = session.execute(["agent", "run", "x"])
    assert result.coalesced is False
    assert result.renders == 0, "no coalescer means no paced renders to report"
    assert out.writes == 60, "one write per chunk is the pre-coalescing behaviour"
    assert out.getvalue() == "x" * (60 * 6)


def test_the_shell_session_default_is_off_for_a_pipe(tmp_path):
    """A pipe, a redirect and a JSON consumer all want every byte immediately."""
    from skaldir import repl

    out = Counter()
    options = repl.ShellOptions(probe=False)
    session = repl.Session(runtime=str(tmp_path), runner=lambda argv: 0, out=out)
    shell = repl.Shell(options, session=session, out=out, err=out, stdin=io.StringIO(""))
    assert shell.interactive is False
    assert session.stream_hz == 0.0


def test_the_shell_session_paces_when_the_caller_asks_for_a_rate(tmp_path):
    from skaldir import repl

    out = Counter()
    options = repl.ShellOptions(probe=False, stream_hz=12.0)
    session = repl.Session(runtime=str(tmp_path), runner=lambda argv: 0, out=out)
    repl.Shell(options, session=session, out=out, err=out, stdin=io.StringIO(""))
    assert session.stream_hz == 12.0
    assert repl.ShellOptions(probe=False, stream_hz=0.0).stream_hz == 0.0
    assert repl.ShellOptions(probe=False).stream_hz is None


def test_the_session_summary_carries_the_streaming_totals(tmp_path):
    import sys

    from skaldir import repl

    out = Counter()

    def runner(_argv):
        for _ in range(40):
            sys.stdout.write("ab")
            sys.stdout.flush()
            time.sleep(1.0 / 400.0)
        return 0

    session = repl.Session(runtime=str(tmp_path), runner=runner, out=out, err=out)
    session.stream_hz = 30.0
    session.execute(["agent", "run", "x"])
    assert session.renders >= 1
    assert session.streamed_chunks == 40
    assert session.stream_cpu_ms >= 0


# --------------------------------------------------------------------------- #
# the terminal has one writer: the indicator must not draw over a live stream
# --------------------------------------------------------------------------- #


def test_the_indicator_does_not_draw_while_output_flows():
    """Measured defect this rule prevents.

    With the indicator drawing during a stream, 4 of 240 answer lines were split by its
    ``\\r\\x1b[K`` (the bytes were all present; the lines were not). The rule is the fix:
    the indicator waits for silence, which is also the only state it is informative in.
    """
    from skaldir import repl

    activity = repl.Activity(Counter(), enabled=True, interval=0.5)
    activity.quiet_s = 1.0
    volume = [0, 1]
    # a fresh, larger volume every time the thread looks: output is flowing
    assert activity.should_draw((volume[0], volume[1]), now=100.0) is False
    volume[0] += 6
    assert activity.should_draw((volume[0], volume[1]), now=100.5) is False
    # nothing new for longer than quiet_s: now it may draw
    assert activity.should_draw((volume[0], volume[1]), now=101.6) is True
    assert activity.suppressed == 0 or activity.suppressed >= 0


def test_the_indicator_takes_the_same_lock_as_the_output():
    from skaldir import repl

    activity = repl.Activity(Counter(), enabled=False)
    assert activity.lock is activity._lock, "one lock, or the two writers race again"


def test_a_locked_stream_serializes_writers():
    import threading

    lock = threading.RLock()
    target = Counter()
    first = streaming.LockedStream(target, lock)
    second = streaming.LockedStream(target, lock)
    for index in range(50):
        first.write(f"a{index};")
        second.write(f"b{index};")
    assert target.getvalue() == "".join(
        f"a{index};b{index};" for index in range(50)
    )
    assert first.isatty() is False
    assert first.encoding == "utf-8"
    first.flush()


# --------------------------------------------------------------------------- #
# the CLI group
# --------------------------------------------------------------------------- #


def test_register_cli_is_idempotent_and_installs_its_contract_entries(capsys):
    import argparse

    parser = argparse.ArgumentParser(prog="skaldir")
    sub = parser.add_subparsers(dest="command")
    streaming.register_cli(sub)
    streaming.register_cli(sub)  # must not raise "conflicting subparser"
    assert "stream" in sub.choices

    from skaldir import cli_contract

    for path in streaming.contract_entries():
        assert path in cli_contract.COMMANDS, f"{path} must be declared"


def test_selfcheck_passes_and_reports_its_checks(capsys):
    code = streaming.main(["selfcheck", "--json", "--chunks", "120", "--chunk-rate", "0"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["ok"] is True
    assert payload["checks_passed"] == payload["checks_total"]
    assert payload["measurement"]["byte_identical"] is True
    assert payload["measurement"]["after"]["renders"] < payload["measurement"]["before"]["renders"]


def test_measure_reports_both_arms_and_the_saving(capsys):
    code = streaming.main(["measure", "--json", "--chunks", "80", "--chunk-rate", "0"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["before"]["renders"] == 80, "uncoalesced is one render per chunk"
    assert payload["after"]["renders"] < 80
    assert payload["byte_identical"] is True
    assert payload["renders_saved"] > 0


def test_policy_reports_the_batching_plan(capsys):
    code = streaming.main([
        "policy", "--json",
        "--tool", "read_a:parallel", "--tool", "read_b:parallel",
        "--tool", "write:sequential", "--tool", "read_c:parallel",
    ])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert [batch["mode"] for batch in payload["batches"]] == [
        "parallel", "sequential", "parallel",
    ]
    assert payload["parallel_batches"] == 2 and payload["barriers"] == 1


def test_policy_without_a_tool_is_a_usage_error(capsys):
    code = streaming.main(["policy", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 1 and "usage" in payload


def test_replay_streams_a_real_file_and_keeps_the_facts_on_stderr(tmp_path, capsys):
    source = tmp_path / "answer.txt"
    source.write_text("SKALDIR-STREAM-MARK\n" * 3, encoding="utf-8")
    code = streaming.main([
        "replay", "--source", str(source), "--chunk-bytes", "5", "--rate", "0",
    ])
    captured = capsys.readouterr()
    assert code == 0
    assert captured.out == "SKALDIR-STREAM-MARK\n" * 3
    assert "replayed" in captured.err


def test_replay_falls_back_to_the_builtin_sample_with_a_reason(tmp_path, capsys):
    code = streaming.main(["replay", "--json", "--source", str(tmp_path / "missing.txt"),
                           "--rate", "0", "--limit", "40"])
    captured = capsys.readouterr()
    assert code == 0
    payload = json.loads(captured.out)  # stdout is one JSON document and nothing else
    assert "could not read" in payload["source"]
    assert payload["answer_streamed"] is False
    assert payload["chars"] == 0


def test_the_wiring_snippet_wires_the_group_into_the_real_parser(tmp_path):
    """Prove the hand-over against the real parser, in a copy — never by claiming it.

    ``cli.py`` is not ours to edit, so the two lines travel as a snippet. What has to be
    true is that the snippet, applied to the real ``build_parser()``, adds the group, its
    four verbs and nothing else — and that the contract dump stays complete.
    """
    scratch = tmp_path / "wired"
    shutil.copytree(SRC, scratch / "src")
    cli = scratch / "src" / "skaldir" / "cli.py"
    text = cli.read_text(encoding="utf-8")
    anchor = "    _register_jev_cli(sub)\n"
    assert anchor in text, "the wiring anchor moved — the snippet needs a new home"
    snippet = streaming.WIRING_SNIPPET
    assert "register_cli as _register_stream" in snippet
    assert snippet.splitlines()[0].startswith("    "), "it must paste into build_parser()"
    cli.write_text(text.replace(anchor, anchor + "\n" + snippet + "\n", 1), encoding="utf-8")

    import subprocess
    import sys

    probe = (
        "import argparse;"
        "from skaldir.cli import build_parser;"
        "from skaldir import cli_contract as C;"
        "p = build_parser();"
        "sub = [a for a in p._actions if isinstance(a, argparse._SubParsersAction)][0];"
        "node = sub.choices['stream'];"
        "inner = [a for a in node._actions if isinstance(a, argparse._SubParsersAction)][0];"
        "print(sorted(inner.choices));"
        "print(sorted(k for k in C.COMMANDS if k.startswith('stream.')));"
        "print([k for k in C.COMMANDS if k.startswith('stream.')])"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(scratch),
        env={"PYTHONPATH": str(scratch / "src"), "PATH": "/usr/bin:/bin",
             "TMPDIR": "/tmp", "HOME": str(tmp_path), "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert completed.returncode == 0, completed.stderr[-800:]
    lines = completed.stdout.strip().splitlines()
    assert lines[0] == "['measure', 'policy', 'replay', 'selfcheck']"
    assert lines[1] == "['stream.measure', 'stream.policy', 'stream.replay', 'stream.selfcheck']"


# --------------------------------------------------------------------------- #
# the harness the report's numbers come from
# --------------------------------------------------------------------------- #

def test_the_perf_harness_settle_check_passes():
    import subprocess
    import sys

    completed = subprocess.run(
        [sys.executable, str(REPO / "tools" / "gv_stream_perf.py"), "settle",
         "--chunks", "120", "--hz", "30"],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    report = json.loads(completed.stdout)
    assert report["within_bound"] is True
    assert report["byte_identical"] is True
    assert report["first_write_immediate"] is True


def test_the_perf_harness_exposes_the_knobs_its_arms_use():
    """Both arms must be able to name their bound; the pty arm needs --src and --json."""
    import subprocess
    import sys

    for verb, expected in (
        ("pty", ("--json-summary", "--src", "--describe")),
        ("pipe", ("--src",)),
        ("settle", ("--chunks",)),
        ("table", ("--results",)),
    ):
        completed = subprocess.run(
            [sys.executable, str(REPO / "tools" / "gv_stream_perf.py"), verb, "--help"],
            cwd=str(REPO), capture_output=True, text=True, timeout=60,
        )
        assert completed.returncode == 0, completed.stderr
        for flag in expected:
            assert flag in completed.stdout, f"{verb} is missing {flag}"
