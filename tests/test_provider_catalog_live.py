"""The fallback chain against a real endpoint — skipped unless a key is reachable.

This is the end-to-end shape of the requirement: an ordered list of candidates
whose first entry cannot answer (a dead local port) and whose second is a real
endpoint with a credential in the environment. The chain must report **which
candidate answered** and, for the one that did not, the reason — measured, not
claimed. It runs only when a credential is in the environment and never prints
it.
"""

from __future__ import annotations

import json
import os

import pytest

from skaldir import llm_provider as lp
from skaldir import provider_catalog as pc
from skaldir import structured as st
from tests.test_structured_live import KEY_NAMES, _key_value, live_target

pytestmark = pytest.mark.skipif(
    live_target() is None,
    reason=(
        "live chain test: needs a real endpoint — export DEEPSEEK_API_KEY "
        "(or OPENROUTER_API_KEY + OPENROUTER_MODEL)"
    ),
)


#: a loopback endpoint that is not listening — refused instantly, no network
DEAD_LOCAL = "http://127.0.0.1:9/v1"


def test_the_chain_skips_a_dead_candidate_and_reports_who_answered(tmp_path, capsys):
    target = live_target()
    assert target is not None
    steps = pc.build_chain(
        [
            pc.Candidate(id="dead-local", base_url=DEAD_LOCAL, model="nobody-is-listening"),
            pc.Candidate(
                id=target["name"],
                label=target["name"],
                base_url=target["base_url"],
                model=target["model"],
                api_key_env=(target["api_key_env"],),
                features=dict(pc.catalog_entry(target["name"]).features)
                if target["name"] in pc.CATALOG
                else {},
            ),
        ],
        env=os.environ,
        require=(pc.FEATURE_STRUCTURED,),
    )
    assert [step.status for step in steps] == [pc.STATUS_READY, pc.STATUS_READY]

    calls: list[str] = []

    def call(step):
        calls.append(step.id)
        cfg = lp.resolve_provider_config(
            {
                "provider": "openai-compatible",
                "base_url": step.candidate.base_url,
                "model": step.candidate.model,
                "api_key_env": (step.candidate.api_key_env or ("",))[0],
                "timeout_s": 60.0,
                "max_retries": 0,
            },
            env=os.environ,
            config_path=tmp_path / "config.yaml",
        )
        assert cfg.ready(), cfg.problems
        return st.structured_call(
            lp.OpenAICompatibleLLM(cfg),
            st.PROBE_SCHEMA,
            prompt=st.PROBE_PROMPT,
            tier="small",
            name=st.PROBE_SCHEMA_NAME,
        )

    outcome = pc.run_chain(steps, call)

    with capsys.disabled():
        print(
            "\n[typesafe provider] chain: "
            + " -> ".join(
                f"{item['id']}={item['status']}/{item['reason']}" for item in outcome.attempts
            )
        )
        print(f"  answered_by={outcome.answered_by!r} mode={outcome.mode!r} raw={outcome.raw!r}")

    assert calls == ["dead-local", target["name"]]  # in order, exactly once each
    assert outcome.ok is True
    assert outcome.answered_by == target["name"]
    assert outcome.value == {"answer": "pong", "probe": 1}
    assert outcome.mode in st.MODE_SEQUENCE
    first = outcome.attempts[0]
    assert first["status"] == pc.STATUS_FAILED
    assert first["reason"] in (pc.SKIP_TIMEOUT, pc.SKIP_PROVIDER_ERROR)
    assert first["detail"]  # the reason is never a bare word
    for name in KEY_NAMES:
        value = _key_value(name)
        if value:
            assert value not in json.dumps(outcome.to_dict())


def test_a_chain_with_no_reachable_credential_says_why():
    """The honest negative: every candidate that names a key variable names its absence."""
    env = {key: value for key, value in os.environ.items() if key not in KEY_NAMES}
    steps = pc.auto_chain(
        env=env, require=(pc.FEATURE_STRUCTURED,), config_path="/nonexistent/live/config.yaml"
    )
    report = pc.chain_report(steps)
    reasons = {item["id"]: item["reason"] for item in report["skipped"]}
    assert reasons["deepseek"] == pc.SKIP_MISSING_CREDENTIAL
    assert reasons["openai"] == pc.SKIP_MISSING_CREDENTIAL
    assert "openrouter" in reasons  # no credential, or no model when no credential is needed
    assert all(item["detail"] for item in report["skipped"])
    for step in report["steps"]:
        # a candidate that names no variable at all is ready AND honest about the 401 risk
        if step["credential"] == "undeclared":
            assert "401" in step["detail"]
    # the variable NAMES appear (that is the report); their VALUES never do
    for name in KEY_NAMES:
        value = _key_value(name)
        if value:
            assert value not in json.dumps(report)
