"""Per-endpoint wire defaults: the token-budget field and the opencode session header.

Modern OpenAI chat models reject ``max_tokens`` (HTTP 400: use ``max_completion_tokens``).
When a config does not name the field, the default is chosen for the endpoint — not one
hard default for every provider. And OpenCode Go/Zen require ``x-opencode-session`` on
every request; a config that names the endpoint gets one (stable across loads) so the
endpoint does not 400 before the first token.
"""

from __future__ import annotations

from skaldir import llm_provider as lp


def test_openai_endpoint_defaults_to_max_completion_tokens():
    cfg = lp.resolve_provider_config(
        {"base_url": "https://api.openai.com/v1", "model": "gpt-5.6-terra"}, env={}
    )
    assert cfg.max_tokens_field == "max_completion_tokens", cfg.max_tokens_field


def test_a_generic_endpoint_keeps_max_tokens():
    cfg = lp.resolve_provider_config(
        {"base_url": "https://api.deepseek.com/v1", "model": "deepseek-flash"}, env={}
    )
    assert cfg.max_tokens_field == "max_tokens", cfg.max_tokens_field


def test_an_explicit_field_is_never_overridden():
    cfg = lp.resolve_provider_config(
        {
            "base_url": "https://api.openai.com/v1",
            "model": "gpt-5.6-terra",
            "max_tokens_field": "max_tokens",
        },
        env={},
    )
    assert cfg.max_tokens_field == "max_tokens", cfg.max_tokens_field


def test_opencode_endpoint_gets_a_stable_session_header():
    first = lp.resolve_provider_config(
        {"base_url": "https://opencode.ai/zen/go/v1", "model": "glm-5.3-flash"}, env={}
    )
    second = lp.resolve_provider_config(
        {"base_url": "https://opencode.ai/zen/go/v1", "model": "glm-5.3-flash"}, env={}
    )
    assert "x-opencode-session" in first.headers, first.headers
    assert first.headers["x-opencode-session"], first.headers
    # stable across loads — the endpoint routes on it
    assert first.headers["x-opencode-session"] == second.headers["x-opencode-session"]
