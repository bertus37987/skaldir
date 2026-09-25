"""Model routing proof (spec §5/§24): role selections actually reach the calls.

No real provider call happens: a capturing side-LLM records the provider/model
kwargs the plugin requests, and the host's capability gate is simulated both
open (override passes) and closed (PluginLlmTrustError → fail-open retry
without an override).

Covers two different roles (watcher/triage and investigator/redirect) plus
the tier fallback mapping and the "no roles configured → no override" state.
"""

import types
from pathlib import Path

import godsv_plugin
import pytest


class CapturingLLM:
    """Stands in for ``ctx.llm``; records every call's kwargs."""

    def __init__(self, deny_trust_once: bool = False) -> None:
        self.calls: list[dict] = []
        self.deny_trust_once = deny_trust_once

    def complete(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        if self.deny_trust_once and kwargs.get("model"):
            self.deny_trust_once = False
            raise type("PluginLlmTrustError", (Exception,), {})("no capability")
        return types.SimpleNamespace(text="ok")


@pytest.fixture
def home(tmp_path, monkeypatch):
    hermes_home = tmp_path / "hermes"
    (hermes_home / "godsv").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    return hermes_home


def _write_roles(home: Path, text: str) -> None:
    (home / "godsv" / "models.yaml").write_text(text, encoding="utf-8")


ROLES_YAML = """\
roles:
  watcher:
    provider: deepseek
    model: deepseek-v4-flash
  investigator:
    provider: openrouter
    model: deepseek/deepseek-v4.1-flash
  judge:
    provider: deepseek
    model: deepseek-v4-pro
"""


class TestModelRouting:
    def test_watcher_role_reaches_the_call(self, home):
        _write_roles(home, ROLES_YAML)
        llm = CapturingLLM()
        client = godsv_plugin.ContextLLM(llm)
        out = client.complete(prompt_id="voice_triage", prompt="digest…", tier="small")
        assert out == "ok"
        call = llm.calls[-1]
        assert call["provider"] == "deepseek"
        assert call["model"] == "deepseek-v4-flash"
        assert call["purpose"] == "godsv:voice_triage"

    def test_investigator_role_reaches_the_call(self, home):
        _write_roles(home, ROLES_YAML)
        llm = CapturingLLM()
        client = godsv_plugin.ContextLLM(llm)
        client.complete(prompt_id="voice_redirect", prompt="redirect…", tier="small")
        call = llm.calls[-1]
        # a second, *different* role routes to a different provider/model
        assert call["provider"] == "openrouter"
        assert call["model"] == "deepseek/deepseek-v4.1-flash"

    def test_tier_mapping_when_no_prompt_hint(self, home):
        _write_roles(home, ROLES_YAML)
        llm = CapturingLLM()
        client = godsv_plugin.ContextLLM(llm)
        client.complete(prompt_id="generic_summary", prompt="x", tier="small")
        assert llm.calls[-1]["model"] == "deepseek-v4-flash"  # small → watcher
        client.complete(prompt_id="generic_summary", prompt="x", tier="mid")
        assert llm.calls[-1]["model"] == "deepseek/deepseek-v4.1-flash"  # mid → investigator

    def test_without_roles_no_override_requested(self, home):
        llm = CapturingLLM()
        client = godsv_plugin.ContextLLM(llm)
        client.complete(prompt_id="voice_triage", prompt="x", tier="small")
        call = llm.calls[-1]
        assert "provider" not in call and "model" not in call

    def test_closed_gate_fails_open_without_override(self, home):
        """Host denies the override → the call is retried without provider/model."""
        _write_roles(home, ROLES_YAML)
        llm = CapturingLLM(deny_trust_once=True)
        client = godsv_plugin.ContextLLM(llm)
        out = client.complete(prompt_id="voice_triage", prompt="x", tier="small")
        assert out == "ok"
        assert len(llm.calls) == 2
        assert llm.calls[0].get("model") == "deepseek-v4-flash"  # first: with override
        assert "model" not in llm.calls[1]  # retry: fail-open, no override

    def test_role_file_can_be_edited_and_takes_effect(self, home):
        """Selection persists in godsv/models.yaml and drives later calls."""
        _write_roles(home, ROLES_YAML)
        first = CapturingLLM()
        godsv_plugin.ContextLLM(first).complete(prompt_id="voice_triage", prompt="x", tier="small")
        assert first.calls[-1]["model"] == "deepseek-v4-flash"
        # user changes the role (persisted file) — next call uses the new model
        _write_roles(
            home,
            "roles:\n  watcher:\n    provider: deepseek\n    model: deepseek-v4-pro\n",
        )
        second = CapturingLLM()
        godsv_plugin.ContextLLM(second).complete(prompt_id="voice_triage", prompt="x", tier="small")
        assert second.calls[-1]["model"] == "deepseek-v4-pro"
