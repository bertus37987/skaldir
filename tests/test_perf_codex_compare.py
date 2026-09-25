"""The CODEX-PERF harness and the measurements it must keep honest.

A performance harness is itself code that can lie, so these pin the parts that
have already lied once:

* a stub endpoint must count **TCP connections**, not requests — that is the only
  observation that distinguishes a pooled client from a per-call one;
* ``first_byte_ms`` must be measured on the first *content* delta of a streamed
  answer, and must be ``None`` when nothing was streamed (never invented);
* the transcript's ``seq`` must stay dense and identical whether or not the
  legacy re-count path is on — otherwise the before/after table compares two
  different products;
* the table must render both columns and a delta for every metric.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

harness = importlib.import_module("gv_perf_codex_compare")

from skaldir import agent_loop as agent_loop_module  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_seams():
    from skaldir import perf_runtime as pr

    pr.reset_caches()
    yield
    pr.reset_caches()


# ------------------------------------------------------------------- statistics


def test_stats_of_reports_p50_and_p95_not_a_single_number():
    entry = harness.stats_of([1.0, 2.0, 3.0, 4.0, 100.0])
    assert entry["n"] == 5
    assert entry["p50"] == 3.0
    assert entry["p95"] == 100.0
    assert entry["min"] == 1.0 and entry["max"] == 100.0


def test_stats_of_handles_an_empty_sample_honestly():
    assert harness.stats_of([]) == {"n": 0, "p50": None, "p95": None, "min": None, "max": None}


# ------------------------------------------------------------------ first byte


def test_first_byte_is_measured_on_a_streamed_answer():
    step = harness.sse_step(["a", "b", "c"], delay_first=0.02)
    with harness.StubProvider([step]) as stub:
        client = harness.provider_for(stub, stream=True)
        result = client.chat([{"role": "user", "content": "go"}])
    assert result.text == "abc"
    assert result.first_byte_ms is not None
    assert result.first_byte_ms >= 15.0, "the stub's own 20 ms delay must be visible"


def test_first_byte_is_none_when_nothing_was_streamed():
    with harness.StubProvider([harness.Step(body=harness.answer_body("plain"))]) as stub:
        client = harness.provider_for(stub, stream=False)
        result = client.chat([{"role": "user", "content": "go"}])
    assert result.text == "plain"
    assert result.first_byte_ms is None, "a non-streamed answer has no first byte to report"


def test_a_role_only_chunk_is_not_a_visible_token():
    """The first *content* delta counts — a role frame must not stop the clock."""
    step = harness.Step(deltas=["x", "y"], delay_first=0.0)
    with harness.StubProvider([step]) as stub:
        client = harness.provider_for(stub, stream=True)
        client.chat([{"role": "user", "content": "go"}])
        assert client.first_byte_ms() is not None


# ------------------------------------------------------------------- transport


def test_two_calls_share_one_connection_end_to_end():
    steps = [harness.Step(body=harness.answer_body("one"))]
    with harness.StubProvider(steps) as stub:
        client = harness.provider_for(stub, stream=False)
        client.chat([{"role": "user", "content": "a"}])
        client.chat([{"role": "user", "content": "b"}])
        assert stub.request_count == 2
        assert stub.connections == 1


def test_a_streamed_answer_keeps_its_connection_too():
    """Draining ``[DONE]`` instead of breaking is what makes a stream reusable."""
    step = harness.sse_step(["x", "y"])
    with harness.StubProvider([step]) as stub:
        client = harness.provider_for(stub, stream=True)
        client.chat([{"role": "user", "content": "a"}])
        client.chat([{"role": "user", "content": "b"}])
        assert stub.connections == 1


# ------------------------------------------------------------------ transcript


def _transcript(tmp_path: Path, *, legacy: bool, monkeypatch, count: int = 500):
    monkeypatch.setattr(agent_loop_module, "_LEGACY_TRANSCRIPT_COUNT", legacy)
    path = tmp_path / "run.jsonl"
    transcript = agent_loop_module.Transcript(path)
    for index in range(count):
        transcript.event("probe", i=index)
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    return rows


def test_transcript_seq_is_dense_and_identical_in_both_modes(tmp_path, monkeypatch):
    fixed = _transcript(tmp_path, legacy=False, monkeypatch=monkeypatch)
    legacy = _transcript(tmp_path, legacy=True, monkeypatch=monkeypatch)
    expected = list(range(1, 501))
    assert [row["seq"] for row in fixed] == expected
    assert [row["seq"] for row in legacy] == expected, "the switch must not change the record"


def test_transcript_reopens_continue_the_numbering(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_loop_module, "_LEGACY_TRANSCRIPT_COUNT", False)
    path = tmp_path / "run.jsonl"
    first = agent_loop_module.Transcript(path)
    first.event("a")
    first.event("b")
    second = agent_loop_module.Transcript(path, truncate=False)
    second.event("c")
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert [row["seq"] for row in rows] == [1, 2, 3]


def test_an_event_after_close_is_returned_but_not_written(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_loop_module, "_LEGACY_TRANSCRIPT_COUNT", False)
    path = tmp_path / "run.jsonl"
    transcript = agent_loop_module.Transcript(path)
    transcript.event("a")
    transcript.close()
    record = transcript.event("b")
    assert record["kind"] == "b"
    assert len(path.read_text().splitlines()) == 1


# ----------------------------------------------------------------------- table


def _snapshot(label: str) -> dict:
    return {
        "label": label,
        "runs": 15,
        "load1": 1.0,
        "cache_enabled": label == "after",
        "tree": {"module": "/repo/src/skaldir/__init__.py"},
        "metrics": {
            "firstbyte": {
                "first_byte_ms": {"p50": 13.0 if label == "before" else 12.9, "p95": 14.0},
                "call_ms": {"p50": 55.0, "p95": 56.0},
                "tcp_connections_per_call": 1.0 if label == "before" else 0.07,
            },
            "toolrt": {
                "round_trip_ms": {"p50": 4.0 if label == "before" else 3.0, "p95": 5.0},
                "segments_ms": {
                    "request_to_tool": {"p50": 2.0, "p95": 3.0},
                    "tool_exec": {"p50": 0.2, "p95": 0.3},
                    "tool_to_next": {"p50": 0.2, "p95": 0.3},
                },
                "tcp_connections_per_run": {"p50": 2.0 if label == "before" else 1.0, "p95": 2.0},
            },
            "turn": {
                "turn_ms": {"p50": 5.0, "p95": 6.0},
                "loop_overhead_ms": {"p50": 1.0, "p95": 1.1},
            },
            "memory": {
                "repeats": 3,
                "rss_growth_mb": {"p50": 20.0 if label == "before" else 5.0, "p95": 21.0},
                "run_ms": {"p50": 1200.0, "p95": 1300.0},
                "tracemalloc_peak_mb": {
                    "p50": 9.0 if label == "before" else 1.0,
                    "p95": 9.1,
                },
            },
            "commands": {"cold_p50_sum_ms": 1500.0, "warm_p50_sum_ms": 700.0,
                         "median_speedup_p50": 2.1},
            "progress": {"naive_writes": 100000, "coalesced_writes": 14,
                         "slow_sink_naive_ms": 5200.0, "slow_sink_coalesced_ms": 60.0,
                         "push_only_ms": 40.0},
        },
    }


def test_table_renders_both_columns_and_a_delta_per_metric():
    table = harness.render_table(_snapshot("before"), _snapshot("after"))
    assert table.count("|") > 40
    assert "before p50/p95" in table
    assert "after p50/p95" in table
    assert "-25.0%" in table, "the tool round trip got 25 % faster in the fixture"
    assert "TCP connections" in table
    assert "measured tree" in table


def test_table_marks_missing_metrics_without_inventing_a_number():
    sparse = {"label": "before", "metrics": {}}
    table = harness.render_table(sparse, sparse)
    assert "—" in table
