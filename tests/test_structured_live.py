"""The ONE live structured-output test — skipped unless a credential is reachable.

A real key must be in the environment (read from the environment only; it is
never printed, never written, never asserted on by value). One of:

* ``DEEPSEEK_API_KEY`` — used against ``https://api.deepseek.com/v1`` (the
  owner's measured default endpoint);
* ``OPENROUTER_API_KEY`` — used against ``https://openrouter.ai/api/v1`` with
  the model from ``OPENROUTER_MODEL`` (no model is invented; without one the
  test skips and says so).

To exercise it::

    DEEPSEEK_API_KEY=... .venv/bin/python -m pytest tests/test_structured_live.py -v

The point of the file is the *measurement*: does the endpoint accept the native
``response_format: json_schema``, or does the run take the validated fallback?
Both answers are recorded — the test asserts the shape of the report, not that
the endpoint happens to agree with the catalog.
"""

from __future__ import annotations

import json
import os

import pytest

from skaldir import llm_provider as lp
from skaldir import provider_config as pc
from skaldir import structured as st

#: every name a reachable credential may live under (never a value).
KEY_NAMES = ("DEEPSEEK_API_KEY", "OPENROUTER_API_KEY", "SKALDIR_LLM_API_KEY", "OPENAI_API_KEY")


def _model_for(name: str, default: str) -> str:
    """The model to ask for: never invented from thin air beyond a documented default."""
    for var in ("SKALDIR_LLM_MODEL", "GODSV_LLM_MODEL", f"{name.upper()}_MODEL"):
        value = str(os.environ.get(var) or "").strip()
        if value:
            return value
    return default


def live_target() -> dict | None:
    """The first reachable credential as ``(provider name, config, key name)``."""
    if os.environ.get("DEEPSEEK_API_KEY"):
        return {
            "name": "deepseek",
            "base_url": "https://api.deepseek.com/v1",
            "model": _model_for("deepseek", "deepseek-chat"),
            "api_key_env": "DEEPSEEK_API_KEY",
        }
    if os.environ.get("OPENROUTER_API_KEY"):
        model = str(os.environ.get("OPENROUTER_MODEL") or "").strip()
        if not model:
            return None  # an OpenRouter call needs a routed model — do not invent one
        return {
            "name": "openrouter",
            "base_url": "https://openrouter.ai/api/v1",
            "model": model,
            "api_key_env": "OPENROUTER_API_KEY",
        }
    return None


def _key_value(name: str) -> str:
    return str(os.environ.get(name) or "")


pytestmark = pytest.mark.skipif(
    live_target() is None,
    reason=(
        "live structured-output test: needs a real endpoint — export "
        "DEEPSEEK_API_KEY (or OPENROUTER_API_KEY + OPENROUTER_MODEL)"
    ),
)


@pytest.fixture
def live_config(tmp_path):
    """A real store entry for the reachable credential (never a literal key)."""
    target = live_target()
    assert target is not None
    config = tmp_path / "config.yaml"
    pc.add_provider(
        target["name"],
        base_url=target["base_url"],
        model=target["model"],
        api_key_env=target["api_key_env"],
        path=config,
    )
    return config, target


def test_the_probe_measures_the_mode_with_raw_evidence(live_config, capsys):
    """One real schema-constrained call: which mode answered, and the raw answer."""
    config, target = live_config
    payload = st.probe_structured(target["name"], path=config, timeout_s=60.0)

    with capsys.disabled():
        print(
            "\n[typesafe provider] "
            f"{target['name']} {payload['endpoint']} model={payload['model']} "
            f"mode={payload['mode_used']} native_json_schema={payload['native_json_schema']} "
            f"validator={payload['validation']['backend']} latency={payload['latency_ms']}ms"
        )
        print(f"  response_format sent: {json.dumps(payload['request']['response_format'])}")
        print(f"  raw answer ({payload['answer']['raw_chars']} chars): {payload['answer']['raw']}")

    assert payload["ok"] is True, payload["error"]
    assert payload["mode_used"] in st.MODE_SEQUENCE
    assert payload["answer"]["value"] == {"answer": "pong", "probe": 1}
    assert payload["validation"]["ok"] is True
    assert payload["validation"]["backend"]
    assert payload["request"]["requests_made"] >= 1

    # the report agrees with itself: the mode that answered is the body that was sent
    sent = payload["request"]["response_format"]
    if payload["mode_used"] == st.MODE_NATIVE:
        assert sent["type"] == "json_schema" and sent["json_schema"]["strict"] is True
        assert payload["native_json_schema"] is True
    elif payload["mode_used"] == st.MODE_JSON_OBJECT:
        assert sent == {"type": "json_object"}
        assert payload["native_json_schema"] is False
    else:
        assert sent is None  # nothing constrained it — we validated it locally

    # a skipped mode must name the provider's own refusal, never a guess
    for item in payload["mode_trail"]:
        if item["status"] == "skipped":
            assert item["reason"] == st.REASON_UNSUPPORTED
            assert item["detail"]
    for name in KEY_NAMES:
        value = _key_value(name)
        if value:
            assert value not in json.dumps(payload)


def test_native_json_schema_support_is_measured_not_assumed(live_config, capsys):
    """The native form is forced: either it answers, or the refusal is the answer."""
    config, target = live_config
    cfg = lp.resolve_provider_config(
        {
            "provider": "openai-compatible",
            "base_url": target["base_url"],
            "model": target["model"],
            "api_key_env": target["api_key_env"],
            "timeout_s": 60.0,
            "max_retries": 0,
        },
        env=os.environ,
        config_path=config,
    )
    assert cfg.ready(), cfg.problems
    client = lp.OpenAICompatibleLLM(cfg)
    outcome = st.structured_call(
        client,
        st.PROBE_SCHEMA,
        prompt=st.PROBE_PROMPT,
        tier="small",
        mode=st.MODE_NATIVE,
        name=st.PROBE_SCHEMA_NAME,
        repair_attempts=0,
    )

    with capsys.disabled():
        native = outcome.ok and outcome.mode == st.MODE_NATIVE
        print(
            f"\n[typesafe provider] native json_schema on {target['name']}: "
            f"{'SUPPORTED' if native else 'NOT SUPPORTED'} "
            f"(mode_trail={json.dumps(outcome.mode_trail)})"
        )
        if not outcome.ok:
            print(f"  raw answer: {outcome.raw[:200]!r}")
            print(f"  reason: {outcome.error[:300]}")

    assert outcome.attempts == 1
    if outcome.ok:
        assert outcome.mode == st.MODE_NATIVE
        assert outcome.value == {"answer": "pong", "probe": 1}
    else:
        # a refusal is a measurement, not a failure of the test
        trail = outcome.mode_trail[0]
        assert trail["mode"] == st.MODE_NATIVE
        assert trail["reason"] in (
            st.REASON_UNSUPPORTED,
            st.REASON_INVALID,
            st.REASON_EMPTY,
            st.REASON_NOT_JSON,
        )
        assert outcome.raw or trail["reason"] == st.REASON_UNSUPPORTED


def test_an_invalid_answer_from_the_real_endpoint_is_never_coerced(live_config, capsys):
    """A schema with exactly one conforming answer: satisfied, or reported verbatim."""
    config, target = live_config
    impossible = {
        "type": "object",
        "properties": {"answer": {"type": "string", "enum": ["pong"]}, "probe": {"type": "null"}},
        "required": ["answer", "probe"],
        "additionalProperties": False,
    }
    cfg = lp.resolve_provider_config(
        {
            "provider": "openai-compatible",
            "base_url": target["base_url"],
            "model": target["model"],
            "api_key_env": target["api_key_env"],
            "timeout_s": 60.0,
            "max_retries": 0,
        },
        env=os.environ,
        config_path=config,
    )
    client = lp.OpenAICompatibleLLM(cfg)
    outcome = st.structured_call(
        client,
        impossible,
        prompt="Reply with JSON: the string \"pong\" as 'answer', and null as 'probe'.",
        tier="small",
        name="impossible_probe",
        repair_attempts=0,
        max_attempts=2,
    )
    with capsys.disabled():
        print(
            f"\n[typesafe provider] satisfiable-null schema: ok={outcome.ok} mode={outcome.mode!r} "
            f"raw={outcome.raw[:160]!r} validation_error={outcome.validation_error[:200]!r}"
        )
    if outcome.ok:
        # the only conforming answer for this schema
        assert outcome.value == {"answer": "pong", "probe": None}
        assert outcome.validation_error == ""
    else:
        assert outcome.value is None and outcome.model is None
        assert outcome.raw or outcome.validation_error  # the evidence is there, not a coercion
