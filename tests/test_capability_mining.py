"""Capability mining + testing-tool validation (Goal add-on).

Covers: repeated-capability detection (requests + recurring commands), type
classification, candidate store semantics, promotion into the gated builder
pipeline, and the testing-tool negative check ("PASS on a broken target" must
be impossible — a lying tester is invalid, never completion evidence).
§1.5 additions: the trace path runs through the persistent cursor and the
merge ignores already-evidenced runs (no double counting on replay).
"""

import json
import os
import subprocess
import sys
from pathlib import Path

from skaldir.capability_mining import (
    TYPE_CLI,
    TYPE_MCP,
    TYPE_SKILL,
    TYPE_TESTING,
    Candidate,
    CandidateStore,
    classify_capability,
    mine,
    mine_from_commands,
    mine_from_requests,
    promote,
    validate_testing_tool,
)
from skaldir.events import EventStore


def test_classify_maps_capability_to_type():
    assert classify_capability("check that the desktop app still works") == TYPE_TESTING
    assert classify_capability("fetch stock prices from an API") == TYPE_MCP
    assert classify_capability("research how to rotate credentials") == TYPE_SKILL
    assert classify_capability("resize images locally") == TYPE_CLI
    assert classify_capability("whatever text", "mcp") == TYPE_MCP


def test_mining_needs_repetition():
    rows = [
        {"capability": "fetch stock prices", "session_id": "s1", "kind": "mcp", "created": 1},
        {"capability": "Fetch stock prices", "session_id": "s2", "kind": "mcp", "created": 2},
        {"capability": "one-off thing", "session_id": "s1", "kind": "auto", "created": 1},
    ]
    candidates = mine_from_requests(rows)
    assert len(candidates) == 1
    top = candidates[0]
    assert top.occurrences == 2
    assert top.sessions == 2
    assert top.suggested_type == TYPE_MCP


def test_mining_from_commands_flags_testing_tools():
    commands = [
        {"command": "node tools/check_desktop_plugin.mjs", "session_id": "s1", "created": 1},
        {"command": "node tools/check_desktop_plugin.mjs", "session_id": "s2", "created": 2},
        {"command": "echo hello", "session_id": "s1", "created": 1},
    ]
    candidates = mine_from_commands(commands)
    assert len(candidates) == 1
    assert candidates[0].suggested_type == TYPE_TESTING
    assert "check_desktop_plugin" in candidates[0].capability


def test_mine_records_and_merges(tmp_path):
    (tmp_path / "capability_requests.jsonl").write_text(
        json.dumps(
            {"capability": "weather lookup", "session_id": "s1", "kind": "auto", "created": 1}
        )
        + "\n"
        + json.dumps(
            {"capability": "weather lookup", "session_id": "s2", "kind": "auto", "created": 2}
        )
        + "\n"
    )
    summary = mine(tmp_path)
    assert summary["candidates_recorded"] == 1
    again = mine(tmp_path)  # second scan accumulates, no duplicate candidate
    rows = CandidateStore(tmp_path).all()
    assert len(rows) == 1
    assert rows[0]["occurrences"] >= 4
    assert again["scanned_requests"] == 2


def _traced_run(store, session, step):
    """Two completed terminal steps with one varying input."""
    store.append(
        session,
        step,
        "tool_call",
        {"name": "terminal", "args": {"command": f"blender {session}.blend --background"}},
        ts=1000.0,
    )
    store.append(session, step + 1, "tool_result", {"tool": "terminal", "status": "ok"}, ts=1000.2)
    store.append(
        session,
        step + 2,
        "tool_call",
        {"name": "terminal", "args": {"command": f"screenshot /tmp/{session}.png"}},
        ts=1000.4,
    )
    store.append(session, step + 3, "tool_result", {"tool": "terminal", "status": "ok"}, ts=1000.6)


def test_mine_runs_the_trace_path_once(tmp_path):
    """§1.5: `tools mine` mines completed runs — and never twice."""
    store = EventStore(tmp_path / "events.db")
    _traced_run(store, "s1", 1)
    _traced_run(store, "s2", 1)
    store.close()
    summary = mine(tmp_path)
    assert summary["scanned_traces"] == 2
    assert summary["new_traces"] == 2
    assert summary["trace_candidates"] == 1
    assert summary["candidates_recorded"] == 1
    rows = CandidateStore(tmp_path).all()
    assert len(rows) == 1
    assert rows[0]["occurrences"] == 2
    again = mine(tmp_path)
    assert again["new_traces"] == 0
    assert again["trace_candidates"] == 0
    rows = CandidateStore(tmp_path).all()
    assert len(rows) == 1
    assert rows[0]["occurrences"] == 2  # the replay inflated nothing


def test_merge_ignores_already_evidenced_runs(tmp_path):
    store = CandidateStore(tmp_path)
    first = store.merge(
        Candidate(
            capability="inspect blender project",
            suggested_type=TYPE_CLI,
            occurrences=2,
            sessions=2,
            evidence_sessions=["s1", "s2"],
            evidence_runs=["s1:1", "s2:9"],
        )
    )
    assert first["occurrences"] == 2
    replay = store.merge(
        Candidate(
            capability="inspect blender project",
            suggested_type=TYPE_CLI,
            occurrences=2,
            sessions=2,
            evidence_sessions=["s1", "s2"],
            evidence_runs=["s1:1", "s2:9"],
        )
    )
    assert replay["occurrences"] == 2
    assert len((tmp_path / "capability_candidates.jsonl").read_text().splitlines()) == 1
    grown = store.merge(
        Candidate(
            capability="inspect blender project",
            suggested_type=TYPE_CLI,
            occurrences=3,
            sessions=1,
            evidence_sessions=["s3"],
            evidence_runs=["s1:1", "s2:9", "s3:4"],
        )
    )
    assert grown["occurrences"] == 3  # only the new run counts
    assert grown["sessions"] == 3
    assert sorted(grown["evidence_runs"]) == ["s1:1", "s2:9", "s3:4"]


def test_promote_creates_capability_request(tmp_path):
    store = CandidateStore(tmp_path)
    row = store.merge(
        Candidate(
            capability="recurring command: node tools/gv_ui_e2e.mjs",
            occurrences=3,
            sessions=2,
            suggested_type=TYPE_TESTING,
        )
    )
    result = promote(tmp_path, row["id"], session_id="cli")
    # a testing candidate stays a testing tool: the builder enforces its
    # validation cases before registration (P1/B4)
    assert result["kind"] == "testing_tool"
    requests = (tmp_path / "capability_requests.jsonl").read_text().splitlines()
    assert len(requests) == 1
    recorded = json.loads(requests[0])
    assert recorded["request_id"] == result["request_id"]
    assert "capability mining" in recorded["reason"]
    assert CandidateStore(tmp_path).by_id(row["id"])["status"] == "promoted"


def test_promote_rejects_skill_candidates(tmp_path):
    store = CandidateStore(tmp_path)
    row = store.merge(
        Candidate(capability="research workflow", occurrences=2, suggested_type=TYPE_SKILL)
    )
    try:
        promote(tmp_path, row["id"])
    except ValueError as exc:
        assert "skill" in str(exc)
    else:  # pragma: no cover - the promotion must not silently succeed
        raise AssertionError("skill candidates must not be promoted as tools")


def test_validate_testing_tool_negative_and_positive():
    def run_case(name):
        # a *correct* tool: passes on the working target, fails on the broken one
        return {"passed": name == "working", "detail": f"ran {name}"}

    result = validate_testing_tool(run_case, [("working", True), ("broken", False)])
    assert result["status"] == "validated"
    assert all(check["ok"] for check in result["checks"])


def test_validate_testing_tool_rejects_always_pass():
    """A tester that reports PASS on a broken target is invalid — never evidence."""

    def liar(name):
        return {"passed": True, "detail": "all good"}

    result = validate_testing_tool(liar, [("working", True), ("broken", False)])
    assert result["status"] == "invalid"
    broken = next(check for check in result["checks"] if check["case"] == "broken")
    assert broken["ok"] is False


def test_validate_testing_tool_crashing_tool_is_invalid():
    def crasher(name):
        raise RuntimeError("boom")

    result = validate_testing_tool(crasher, [("working", True)])
    assert result["status"] == "invalid"


def test_cli_mine_candidates_promote(tmp_path):
    home = tmp_path / "home"
    (home / "godsv").mkdir(parents=True)
    (home / "godsv" / "capability_requests.jsonl").write_text(
        json.dumps(
            {"capability": "fetch stock prices", "session_id": "s1", "kind": "mcp", "created": 1}
        )
        + "\n"
        + json.dumps(
            {"capability": "fetch stock prices", "session_id": "s2", "kind": "mcp", "created": 2}
        )
        + "\n"
    )
    # `HERMES_HOME` alone is not the runtime: `SKALDIR_HOME`/`GODSV_HOME` outrank it
    # (`registry.runtime_dir_for`), so the child must not inherit a leaked one — measured:
    # with `SKALDIR_HOME` exported, `tools mine` read a *foreign* runtime's requests and the
    # candidate written here never appeared.
    env = {key: value for key, value in os.environ.items()
           if key not in ("SKALDIR_HOME", "GODSV_HOME")}
    env["HERMES_HOME"] = str(home)

    def run(*args):
        return subprocess.run(
            [sys.executable, "-m", "skaldir", *args],
            capture_output=True,
            text=True,
            env=env,
            cwd=tmp_path,
            check=False,
        )

    mined = run("tools", "mine")
    assert mined.returncode == 0, mined.stderr
    assert "candidate" in mined.stdout
    listed = run("tools", "candidates")
    assert "fetch stock prices" in listed.stdout, listed.stdout
    candidate_id = listed.stdout.split()[0]
    promoted = run("tools", "promote", candidate_id)
    assert promoted.returncode == 0, promoted.stderr
    assert "promoted" in promoted.stdout

# --------------------------------------------------------------------------- #
# (§4c) discovery metadata — the contract-backed search surface
# --------------------------------------------------------------------------- #


def _manifest(tool_dir: Path, name: str, **fields) -> None:
    import yaml

    tool_dir.mkdir(parents=True, exist_ok=True)
    payload = {"name": name, "version": "0.1.0", "type": "cli", "status": "ready"}
    payload.update(fields)
    (tool_dir / "manifest.yaml").write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )


def test_discovery_metadata_covers_the_documented_fields(tmp_path):
    from skaldir.capability_mining import (
        DISCOVERY_FIELDS,
        capability_metadata,
        metadata_haystack,
    )

    runtime = tmp_path / "rt"
    _manifest(
        runtime / "generated_tools" / "convert_doc",
        "convert_doc",
        purpose="Convert documents with Pandoc",
        scope="global",
        validation={"status": "validated"},
        discovery={
            "tasks": ["convert documents"],
            "synonyms": ["umwandeln"],
            "phrases_de": ["Dokumente umwandeln"],
            "phrases_en": ["convert a document"],
            "inputs": ["source_path:string"],
            "outputs": ["converted_path:string"],
            "non_use_cases": ["video files"],
            "examples": ["convert an invoice"],
            "platforms": ["linux"],
            "requires_services": ["pandoc"],
        },
    )
    row = capability_metadata(runtime)[0]
    for field in DISCOVERY_FIELDS:
        assert field in row, field
        assert row["sources"].get(field) or field in ("preconditions",)
    assert row["requires_services"] == ["pandoc"]
    assert row["validation"] == "validated"
    haystack = metadata_haystack(row)
    for needle in ("convert documents", "umwandeln", "Dokumente umwandeln", "pandoc"):
        assert needle in haystack


def test_untyped_phrases_fill_both_language_channels(tmp_path):
    from skaldir.capability_mining import capability_metadata

    runtime = tmp_path / "rt"
    _manifest(
        runtime / "generated_tools" / "tool_x",
        "tool_x",
        purpose="Do X",
        discovery={"phrases": ["etwas umwandeln", "convert something"]},
    )
    row = capability_metadata(runtime)[0]
    assert row["phrases_de"] == row["phrases_en"] == [
        "etwas umwandeln",
        "convert something",
    ]
    assert row["sources"]["phrases_de"] == "manifest.discovery.phrases"


def test_legacy_tools_can_be_excluded_from_the_metadata_view(tmp_path):
    from skaldir.capability_mining import capability_metadata

    runtime = tmp_path / "rt"
    _manifest(runtime / "generated_tools" / "legacy_tool", "legacy_tool", purpose="Old tool")
    assert [row["name"] for row in capability_metadata(runtime)] == ["legacy_tool"]
    assert capability_metadata(runtime, include_legacy=False) == []
