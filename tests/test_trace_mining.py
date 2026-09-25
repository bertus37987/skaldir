"""§1.5 — trace mining from real completed executions (``events.db``).

What this proves, claim by claim:

- only *completed* executions (tool_call + paired tool_result) enter a trace —
  a call without a result is never mined;
- a failed step ends the run and is never part of it;
- foreign events (capability_health_changed, thoughts) between two steps do
  not split the run;
- the persistent cursor (``traces_cursor.json``, atomic write) makes replays
  idempotent: a second pass reports 0 new and adds no occurrences;
- even a *wiped* cursor file cannot inflate a candidate: the candidate carries
  its stable evidence run ids and ``CandidateStore.merge`` ignores known runs;
- secrets that reached the store without passing the append gate are redacted
  before anything durable is written.
"""

import json
import sqlite3

from skaldir.capability_mining import (
    CandidateStore,
    _completed_runs,
    consume_completed_traces,
    load_traces_cursor,
)
from skaldir.events import EventStore

TS = 1000.0  # long past: every run counts as settled unless a test says otherwise
SECRET = "sk-" + "abcdefghijklmnopqrstuvwx"  # matches the openai pattern
OK = {"tool": "terminal", "status": "ok"}


def _append(store, session, step, kind, payload, ts):
    store.append(session, step, kind, payload, ts=ts)


def _run(store, session, step, ts, blend, png, tail=None):
    """One completed two-step run; ``tail`` adds a call that never completes."""
    _append(
        store,
        session,
        step,
        "tool_call",
        {"name": "terminal", "args": {"command": f"blender {blend} --background"}},
        ts,
    )
    _append(store, session, step + 1, "tool_result", OK, ts + 0.2)
    _append(
        store,
        session,
        step + 2,
        "tool_call",
        {"name": "terminal", "args": {"command": f"screenshot {png}"}},
        ts + 0.4,
    )
    _append(store, session, step + 3, "tool_result", OK, ts + 0.6)
    if tail:
        _append(
            store,
            session,
            step + 4,
            "tool_call",
            {"name": "terminal", "args": {"command": tail}},
            ts + 0.8,
        )


def _store(
    tmp_path,
    runs=(("s1", "A.blend", "/tmp/a.png"), ("s2", "B.blend", "/tmp/b.png")),
    tail=None,
):
    store = EventStore(tmp_path / "events.db")
    for index, (session, blend, png) in enumerate(runs):
        _run(store, session, 1, TS + index * 10, blend, png, tail=tail)
    store.close()


def _raw_events(tmp_path, session, step, ts, rows):
    """Insert rows by hand — a foreign writer whose payloads were never scanned."""
    con = sqlite3.connect(tmp_path / "events.db")
    try:
        for kind, payload in rows:
            con.execute(
                "INSERT INTO events(ts, session_id, step, kind, payload) VALUES (?,?,?,?,?)",
                (ts, session, step, kind, json.dumps(payload)),
            )
            step += 1
        con.commit()
    finally:
        con.close()


def _all_events(tmp_path):
    store = EventStore(tmp_path / "events.db")
    try:
        return store.stream()
    finally:
        store.close()


def test_only_completed_executions_are_mined(tmp_path):
    _store(tmp_path, tail="rm -rf /tmp/never-finished")
    runs = _completed_runs(_all_events(tmp_path))
    assert len(runs) == 2
    # the dangling tail call is not a completed execution: not a step, and no
    # extra run either
    assert all(len(run["steps"]) == 2 for run in runs)
    assert all(step.operation != "rm" for run in runs for step in run["steps"])
    report = consume_completed_traces(tmp_path)
    assert report["scanned"] == 2
    assert report["new"] == 2
    assert report["candidates"] == 1
    rows = CandidateStore(tmp_path).all()
    assert len(rows) == 1
    assert rows[0]["occurrences"] == 2
    # run ids are anchored on the store's stable event sequences: s1 starts at
    # seq 1, s2 at seq 6 (its dangling tail call occupies seq 5)
    assert sorted(rows[0]["evidence_runs"]) == ["s1:1", "s2:6"]


def test_failed_step_ends_the_run_and_is_not_part_of_it(tmp_path):
    store = EventStore(tmp_path / "events.db")
    _run(store, "s1", 1, TS, "A.blend", "/tmp/a.png")
    _append(
        store,
        "s1",
        9,
        "tool_call",
        {"name": "terminal", "args": {"command": "rm -rf /tmp/broken"}},
        TS + 1.0,
    )
    _append(
        store,
        "s1",
        10,
        "tool_result",
        {"tool": "terminal", "status": "error", "error": "exit 1"},
        TS + 1.2,
    )
    _run(store, "s1", 11, TS + 2.0, "C.blend", "/tmp/c.png")
    store.close()
    runs = _completed_runs(_all_events(tmp_path))
    assert [len(run["steps"]) for run in runs] == [2, 2]
    assert all(step.operation != "rm" for run in runs for step in run["steps"])


def test_foreign_events_between_steps_do_not_split_the_run(tmp_path):
    store = EventStore(tmp_path / "events.db")
    _append(
        store,
        "s1",
        1,
        "tool_call",
        {"name": "terminal", "args": {"command": "blender A.blend --background"}},
        TS,
    )
    _append(store, "s1", 2, "tool_result", OK, TS + 0.2)
    # a health event of another (pseudo) session sits between the two steps
    _append(
        store,
        "capability-health",
        0,
        "capability_health_changed",
        {"name": "demo_tool", "from": "HEALTHY", "to": "DEGRADED"},
        TS + 0.3,
    )
    _append(store, "s1", 3, "thought", {"text": "weiter"}, TS + 0.35)
    _append(
        store,
        "s1",
        4,
        "tool_call",
        {"name": "terminal", "args": {"command": "screenshot /tmp/a.png"}},
        TS + 0.4,
    )
    _append(store, "s1", 5, "tool_result", OK, TS + 0.6)
    store.close()
    runs = _completed_runs(_all_events(tmp_path))
    assert [len(run["steps"]) for run in runs] == [2]
    assert [step.operation for step in runs[0]["steps"]] == ["blender", "screenshot"]


def test_second_pass_reports_zero_new(tmp_path):
    _store(tmp_path)
    first = consume_completed_traces(tmp_path)
    assert (first["new"], first["candidates"]) == (2, 1)
    before = CandidateStore(tmp_path).all()
    second = consume_completed_traces(tmp_path)
    assert second["new"] == 0
    assert second["candidates"] == 0
    after = CandidateStore(tmp_path).all()
    assert after == before
    assert after[0]["occurrences"] == 2


def test_cursor_persists_across_instances(tmp_path):
    _store(tmp_path)
    consume_completed_traces(tmp_path)
    cursor = load_traces_cursor(tmp_path)  # fresh read from disk
    assert cursor["processed_seq"] > 0
    assert sorted(cursor["consumed_runs"]) == ["s1:1", "s2:5"]
    assert all(
        entry["last_seq"] >= entry["first_seq"] for entry in cursor["consumed_runs"].values()
    )
    # a fresh consumer (new process view) reports nothing new either
    assert consume_completed_traces(tmp_path)["new"] == 0


def test_wiped_cursor_cannot_inflate_a_candidate(tmp_path):
    _store(tmp_path)
    consume_completed_traces(tmp_path)
    (tmp_path / "traces_cursor.json").unlink()  # replay after a full reset
    replay = consume_completed_traces(tmp_path)
    assert replay["new"] == 2  # the runs are re-read …
    assert replay["candidates"] == 0  # … but no candidate gains evidence twice
    rows = CandidateStore(tmp_path).all()
    assert len(rows) == 1
    assert rows[0]["occurrences"] == 2
    assert sorted(rows[0]["evidence_runs"]) == ["s1:1", "s2:5"]
    # the known run is a no-op, not even a duplicate row is appended
    lines = (tmp_path / "capability_candidates.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1


def test_unsettled_tail_waits_for_the_next_pass(tmp_path):
    store = EventStore(tmp_path / "events.db")
    _run(store, "s1", 1, 5000.0, "A.blend", "/tmp/a.png")
    _run(store, "s2", 1, 5001.0, "B.blend", "/tmp/b.png")
    store.close()
    fresh = consume_completed_traces(tmp_path, now=5001.5)
    assert fresh["scanned"] == 2
    # s1 is closed by the session change; s2 is the open tail and may still
    # grow — a single run is no repetition anyway
    assert fresh["new"] == 1
    assert fresh["candidates"] == 0
    settled = consume_completed_traces(tmp_path, now=5001.5 + 1801)
    assert settled["new"] == 1  # the tail only now counts as finished
    # …and the candidate generalizes over BOTH runs: the already-consumed s1
    # is part of the repetition, its evidence is just not counted twice
    assert settled["candidates"] == 1
    rows = CandidateStore(tmp_path).all()
    assert rows[0]["occurrences"] == 2
    assert sorted(rows[0]["evidence_runs"]) == ["s1:1", "s2:5"]


def test_secrets_from_a_foreign_writer_are_redacted(tmp_path):
    EventStore(tmp_path / "events.db").close()  # schema only
    # two completed runs whose screenshot arguments carry a secret that never
    # passed the append-time scan (inserted directly, like a foreign writer)
    for session, blend, png in (("s3", "C.blend", "/tmp/c.png"), ("s4", "D.blend", "/tmp/d.png")):
        _raw_events(
            tmp_path,
            session,
            1,
            TS,
            [
                (
                    "tool_call",
                    {"name": "terminal", "args": {"command": f"blender {blend} --background"}},
                ),
                ("tool_result", OK),
                (
                    "tool_call",
                    {"name": "terminal", "args": {"command": f"screenshot {png} --auth {SECRET}"}},
                ),
                ("tool_result", OK),
            ],
        )
    report = consume_completed_traces(tmp_path)
    assert report["new"] == 2
    assert report["redacted"] >= 2
    text = (tmp_path / "capability_candidates.jsonl").read_text(encoding="utf-8")
    assert SECRET not in text
    assert "[REDACTED:openai]" in text


def test_traces_only_use_small_scalar_arguments(tmp_path):
    store = EventStore(tmp_path / "events.db")
    for session, blend in (("s1", "A.blend"), ("s2", "B.blend")):
        _append(
            store,
            session,
            1,
            "tool_call",
            {
                "name": "terminal",
                "args": {"command": f"blender {blend} --background", "payload": "x" * 400},
            },
            TS,
        )
        _append(store, session, 2, "tool_result", OK, TS + 0.2)
        _append(
            store,
            session,
            3,
            "tool_call",
            {"name": "terminal", "args": {"command": "screenshot /tmp/a.png"}},
            TS + 0.4,
        )
        _append(store, session, 4, "tool_result", OK, TS + 0.6)
    store.close()
    runs = _completed_runs(_all_events(tmp_path))
    assert runs
    assert all("payload" not in step.args for run in runs for step in run["steps"])
