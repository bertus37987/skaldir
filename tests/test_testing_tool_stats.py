"""§26: testing-tool proof stats — reuse of the usage ledger + validation.

Audit found both sources already exist with the right semantics; these tests
pin the new aggregated view: runs/passes/fails/bugs_detected from the
append-only ledger, validation state from the manifests, wired into metrics.
"""

import yaml

import skaldir.capability as capability
from skaldir.toolusage import record_usage


def _tool(tmp_path, name, status="validated", capability="checks a readme"):
    folder = tmp_path / "generated_tools" / name
    folder.mkdir(parents=True)
    (folder / "manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "type": "testing_tool",
                "status": "ready",
                "capability": capability,
                "validation": {"status": status, "checks": []},
            }
        ),
        encoding="utf-8",
    )


class TestTestingToolStats:
    def test_empty_runtime_is_all_zero(self, tmp_path):
        stats = capability.testing_tool_stats(tmp_path)
        assert stats["totals"]["tools"] == 0
        assert stats["totals"]["runs"] == 0
        assert stats["tools"] == {}

    def test_aggregates_runs_and_validation(self, tmp_path):
        _tool(tmp_path, "check_readme")
        _tool(tmp_path, "broken_kit", status="unvalidated")
        record_usage(tmp_path, "check_readme", ok=True, duration_ms=5)
        record_usage(tmp_path, "check_readme", ok=False, error="PASS missing")
        stats = capability.testing_tool_stats(tmp_path)
        tool = stats["tools"]["check_readme"]
        assert (tool["runs"], tool["passes"], tool["fails"]) == (2, 1, 1)
        assert tool["bugs_detected"] == 1
        assert tool["validation"] == "validated"
        assert "PASS missing" in tool["last_error"]
        assert stats["tools"]["broken_kit"]["validation"] == "unvalidated"
        assert stats["totals"]["validated"] == 1
        assert stats["totals"]["bugs_detected"] == 1
        assert "structurally excluded" in stats["totals"]["false_pass_policy"]

    def test_metrics_carries_the_field(self, tmp_path):
        from skaldir.events import EventStore
        from skaldir.metrics import compute_metrics

        _tool(tmp_path, "check_readme")
        store = EventStore(tmp_path / "events.db")
        metrics = compute_metrics(store, testing_runtime=tmp_path)
        store.close()
        assert metrics.testing_tools is not None
        assert metrics.testing_tools["totals"]["tools"] == 1
