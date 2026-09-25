"""The ONE live provider test — skipped unless ``GODSV_LLM_LIVE=1``.

A real credential (or a local model server) is required, so this test never runs
in the default suite. To exercise it:

    export GODSV_LLM_BASE_URL=https://api.openai.com/v1
    export GODSV_LLM_MODEL=gpt-4o-mini
    export GODSV_LLM_API_KEY=sk-...            # never committed, never logged
    GODSV_LLM_LIVE=1 .venv/bin/python -m pytest tests/test_llm_provider_live.py -v

A keyless local server works too (llama.cpp ``llama-server`` or vLLM):

    export GODSV_LLM_BASE_URL=http://127.0.0.1:8080/v1
    export GODSV_LLM_MODEL=qwen2.5-7b-instruct
    GODSV_LLM_LIVE=1 .venv/bin/python -m pytest tests/test_llm_provider_live.py -v

The key is read from the environment only and is never rendered: the assertion
messages use :func:`godsv.llm_provider.provider_status`, which reports
``"set"``/``"unset"`` instead of the value.
"""

from __future__ import annotations

import os

import pytest

from skaldir.llm_provider import provider_for, provider_status, resolve_provider_config

LIVE = os.environ.get("GODSV_LLM_LIVE") == "1"


@pytest.mark.skipif(
    not LIVE,
    reason=(
        "live provider test: needs a real endpoint — set GODSV_LLM_LIVE=1 plus "
        "GODSV_LLM_BASE_URL / GODSV_LLM_MODEL / GODSV_LLM_API_KEY"
    ),
)
def test_live_provider_answers_a_prompt():
    """One real chat completion against the configured endpoint."""
    status = provider_status()
    cfg = resolve_provider_config()
    assert cfg.ready(), f"provider not ready: {status['problems'] or status['hint']}"
    client = provider_for()
    answer = client.complete(
        prompt_id="live_probe",
        prompt="Reply with the single word: pong",
        tier="small",
    )
    assert isinstance(answer, str) and answer.strip(), "the endpoint returned an empty answer"
    print(
        f"\nlive answer from {status['endpoint']} (model={cfg.model}, "
        f"api_key={status['api_key']}): {answer.strip()[:200]}"
    )
