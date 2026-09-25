"""Voice v2: LLM triage & synthesis with strict template fallbacks."""

import json

from skaldir.prompts import PromptRegistry
from skaldir.synthesis import synthesize_hint, triage_step

REGISTRY = PromptRegistry()


class BoomLLM:
    """LLMClient that always fails (fallback path)."""

    def complete(self, *, prompt_id: str, prompt: str, tier: str) -> str:
        raise RuntimeError("boom")


class TestTriage:
    def test_parses_stuck_result(self):
        from skaldir.llm import ScriptedLLM

        payload = json.dumps(
            {
                "stuck": True,
                "category": "test_failure",
                "confidence": 0.8,
                "rationale": "drei Läufe ohne Fortschritt",
                "search_queries": ["ImportError x", "q2", "q3", "q4"],
            }
        )
        client = ScriptedLLM(responses={"voice_triage": payload})
        result = triage_step(client, REGISTRY, pact_excerpt="P", event_digest="D")
        assert result is not None
        assert result.stuck is True
        assert result.category == "test_failure"
        assert result.confidence == 0.8
        assert result.search_queries == ["ImportError x", "q2", "q3"]  # capped at 3

    def test_confidence_is_clamped(self):
        from skaldir.llm import ScriptedLLM

        payload = json.dumps({"stuck": True, "category": "x", "confidence": 1.7})
        client = ScriptedLLM(responses={"voice_triage": payload})
        result = triage_step(client, REGISTRY, pact_excerpt="P", event_digest="D")
        assert result is not None and result.confidence == 1.0

    def test_malformed_output_returns_none(self):
        from skaldir.llm import ScriptedLLM

        client = ScriptedLLM(responses={"voice_triage": "das ist kein json"})
        assert triage_step(client, REGISTRY, pact_excerpt="P", event_digest="D") is None

    def test_missing_stuck_key_returns_none(self):
        """No explicit verdict must never be read as 'not stuck'."""
        from skaldir.llm import ScriptedLLM

        payload = json.dumps({"category": "x", "confidence": 0.9})
        client = ScriptedLLM(responses={"voice_triage": payload})
        assert triage_step(client, REGISTRY, pact_excerpt="P", event_digest="D") is None

    def test_client_failure_returns_none(self):
        assert triage_step(BoomLLM(), REGISTRY, pact_excerpt="P", event_digest="D") is None


class TestSynthesis:
    def test_parses_hint(self):
        from skaldir.llm import ScriptedLLM

        payload = json.dumps(
            {
                "hint_md": "**Hinweis:** Incident-Memory kennt diese Signatur.",
                "confidence": 0.9,
                "evidence_ids": ["incident:12"],
                "verify": "pytest -q laufen lassen",
            }
        )
        client = ScriptedLLM(responses={"voice_synthesis": payload})
        result = synthesize_hint(client, REGISTRY, trigger="error_burst: x", evidence_items=["e1"])
        assert result is not None
        assert "Incident-Memory" in result.hint_md
        assert result.confidence == 0.9
        assert result.evidence_ids == ["incident:12"]
        assert result.verify == "pytest -q laufen lassen"

    def test_declined_hint_returns_none(self):
        from skaldir.llm import ScriptedLLM

        payload = json.dumps({"hint_md": None, "confidence": 0.3})
        client = ScriptedLLM(responses={"voice_synthesis": payload})
        assert synthesize_hint(client, REGISTRY, trigger="t", evidence_items=[]) is None

    def test_empty_hint_returns_none(self):
        from skaldir.llm import ScriptedLLM

        payload = json.dumps({"hint_md": "   ", "confidence": 0.9})
        client = ScriptedLLM(responses={"voice_synthesis": payload})
        assert synthesize_hint(client, REGISTRY, trigger="t", evidence_items=[]) is None

    def test_client_failure_returns_none(self):
        assert synthesize_hint(BoomLLM(), REGISTRY, trigger="t", evidence_items=[]) is None
