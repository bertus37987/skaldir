"""§11 — testing-tool reliability metrics come from the real ledger."""

import json

from skaldir.toolhub import discover
from skaldir.toolusage import usage_stats


def _write_ledger(runtime) -> None:
    runtime.mkdir(parents=True, exist_ok=True)
    rows = [
        {"ts": 1.0, "name": "check_readme", "ok": True},
        {"ts": 2.0, "name": "check_readme", "ok": False, "error": "missing README at /tmp/x"},
        {"ts": 3.0, "name": "check_readme", "ok": True},
        {"ts": 4.0, "name": "money_convert", "ok": True},
    ]
    (runtime / "tool_usage.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
    )


def test_usage_stats_aggregates_runs_passes_fails(tmp_path):
    _write_ledger(tmp_path)
    stats = usage_stats(tmp_path)["check_readme"]
    assert stats["runs"] == 3
    assert stats["passes"] == 2
    assert stats["fails"] == 1
    assert "missing README" in stats["last_error"]
    assert stats["last_ts"] == 3.0


def test_missing_ledger_is_empty_not_an_error(tmp_path):
    assert usage_stats(tmp_path / "nope") == {}


def test_toolhub_exposes_the_reliability_numbers(tmp_path):
    tool = tmp_path / "generated_tools" / "check_readme"
    tool.mkdir(parents=True)
    (tool / "manifest.yaml").write_text(
        "name: check_readme\nversion: 0.1.0\ntype: testing_tool\npurpose: x\nstatus: ready\n"
        "validation:\n  status: validated\n",
        encoding="utf-8",
    )
    _write_ledger(tmp_path)
    entry = next(
        e for e in discover(tmp_path, skills_dir=tmp_path / "none") if e["name"] == "check_readme"
    )
    assert entry["reliability"] == {"uses": 3, "passes": 2, "fails": 1}
