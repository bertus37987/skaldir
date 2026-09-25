"""Coverage must be reachable through the shipped command dispatcher."""
import json

import pytest

from skaldir import security_audit_cli, security_linux


@pytest.mark.parametrize(
    "verdict, expected", [("enforced", 0), ("degraded", 3), ("unavailable", 3)]
)
def test_coverage_dispatch_preserves_platform_verdict(monkeypatch, capsys, verdict, expected):
    payload = {"verdict": verdict, "primitives": [], "notes": []}
    monkeypatch.setattr(security_linux, "platform_report", lambda: payload)
    result = security_audit_cli.main(["coverage", "--json"])
    assert result == expected
    output = json.loads(capsys.readouterr().out)
    assert output["data"]["verdict"] == payload["verdict"]


def test_coverage_does_not_claim_observed_child_enforcement(monkeypatch, capsys):
    payload = {"verdict": "enforced", "primitives": [
        {"primitive": "landlock", "status": "enforced", "applied": True}
    ], "notes": []}
    monkeypatch.setattr(security_linux, "platform_report", lambda: payload)
    assert security_audit_cli.main(["coverage", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)["data"]
    assert data["assessment_scope"] == "host_capability_probe"
    assert data["execution_observed"] is False
    assert data["primitives"][0]["applied"] is None
    assert data["primitives"][0]["capability_available"] is True
    assert payload["primitives"][0]["applied"] is True  # do not mutate the model
