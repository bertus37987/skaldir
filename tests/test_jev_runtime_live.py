"""The ONE live test of the Jev runtime path — skipped unless ``GODSV_LLM_LIVE=1``.

A real credential (or a local model server) is required, so this never runs in
the default suite. To exercise it (the key is read from the environment only and
is never rendered — :func:`godsv.llm_provider.provider_status` reports
``"set"``/``"unset"``)::

    export GODSV_LLM_BASE_URL=https://api.deepseek.com/v1
    export GODSV_LLM_MODEL=deepseek-chat
    export GODSV_LLM_API_KEY=...            # never committed, never logged
    GODSV_LLM_LIVE=1 .venv/bin/python -m pytest tests/test_jev_runtime_live.py -v

What it proves, end to end and with no fixture in the path: the flag file is
enabled, the service is built from those flags through
``godsv.llm_provider.provider_for()``, a real decision comes back ``ok`` with
``provider_mode="llm_system_one_adapter"``, a non-zero latency and real token
usage — and the four hard rules still hold on that live decision.
"""

from __future__ import annotations

import json
import os

import pytest

from skaldir.jev import runtime

LIVE = os.environ.get("GODSV_LLM_LIVE") == "1"

SKIP_REASON = (
    "live provider test: needs a real endpoint — set GODSV_LLM_LIVE=1 plus "
    "GODSV_LLM_BASE_URL / GODSV_LLM_MODEL / GODSV_LLM_API_KEY"
)


@pytest.mark.skipif(not LIVE, reason=SKIP_REASON)
def test_a_live_provider_decides_through_the_runtime_path(tmp_path):
    """Enable the flags, ask ROUTE, and let a real model answer one decision."""
    runtime.enable(tmp_path)

    report = runtime.status(tmp_path, probe="settings")

    assert report["route"]["reachable"] is True, report["route"]["blocked_reason"]
    assert report["route"]["exit_code"] == 0
    assert report["route"]["provider_mode"] == "llm_system_one_adapter"
    assert report["advise"]["answers"] > 0, "a live evaluation must answer questions"
    assert report["decision"]["status"] == "ok", report["decision"]["refusal_reason"]
    assert (report["decision"]["latency_ms"] or 0) > 0, "a real call takes time"
    assert report["rules_rechecked"]["violations"] == []
    assert report["rules_rechecked"]["advisory_only"] is True
    assert report["provider"]["ready"] is True
    assert report["provider"]["api_key"] in ("set", "unset"), (
        "the report carries the redacted provider state, never the key"
    )
    assert "sk-" not in json.dumps(report)

    decision, flags, problem = runtime.decide_through_runtime(
        "tool_routing", runtime=tmp_path, probe="settings"
    )
    assert problem == ""
    assert flags.enabled is True and flags.provider == "llm_system_one_adapter"
    assert decision.status == "ok", decision.refusal_reason
    assert decision.provider_mode == "llm_system_one_adapter"
    assert decision.answer.chosen_kind in ("route", "computer_use_fallback", "no_route")
    assert decision.authority == "advisory"
    assert decision.certifies is False
    assert tuple(decision.grants) == ()
    assert decision.goal_revision == 0
    print(
        f"\nlive Jev decision: {decision.kind} -> {decision.answer.chosen_kind} "
        f"via {decision.provider_mode} in {decision.latency_ms} ms "
        f"(usage={decision.usage}, endpoint={report['provider']['endpoint']})"
    )
