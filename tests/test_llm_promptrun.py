"""LLM client abstraction + prompt runner (JSON handling, repair, budgets)."""

import json

import pytest

from skaldir.llm import (
    BudgetExceeded,
    BudgetTracker,
    CallableLLM,
    LLMError,
    ScriptedLLM,
    estimate_tokens,
)
from skaldir.promptrun import PromptError, extract_json, render_input_block, run_prompt
from skaldir.prompts import PromptRegistry


class TestClients:
    def test_scripted_llm_returns_canned_and_records_calls(self):
        client = ScriptedLLM(responses={"voice_triage": '{"stuck": false}'})
        out = client.complete(prompt_id="voice_triage", prompt="x", tier="small")
        assert out == '{"stuck": false}'
        assert client.calls[0]["tier"] == "small"

    def test_scripted_llm_unknown_prompt_raises(self):
        with pytest.raises(LLMError):
            ScriptedLLM(responses={}).complete(prompt_id="nope", prompt="x", tier="small")

    def test_callable_llm(self):
        client = CallableLLM(lambda *, prompt_id, prompt, tier: f"{prompt_id}/{tier}")
        assert client.complete(prompt_id="a", prompt="x", tier="mid") == "a/mid"


class TestBudget:
    def test_spend_accumulates_and_total(self):
        budget = BudgetTracker()
        budget.spend("small", "a" * 40, "b" * 40)  # 10 + 10 tokens
        assert budget.usage["small"] == 20
        assert budget.total() == 20

    def test_limit_exceeded_raises(self):
        budget = BudgetTracker(limits={"small": 5})
        with pytest.raises(BudgetExceeded):
            budget.spend("small", "x" * 40, "y" * 40)

    def test_unknown_tier_raises(self):
        with pytest.raises(LLMError):
            BudgetTracker().spend("giant", "a", "b")

    def test_estimate_tokens_minimum_one(self):
        assert estimate_tokens("") == 1


class TestExtractJson:
    def test_plain_json(self):
        assert extract_json('{"a": 1}') == {"a": 1}

    def test_fenced_json(self):
        assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}

    def test_json_in_prose(self):
        assert extract_json('Hier das Ergebnis: {"a": [1, 2]} Ende.') == {"a": [1, 2]}

    def test_no_json_raises(self):
        with pytest.raises(PromptError):
            extract_json("kein json hier")


class TestRunPrompt:
    def test_runs_registry_prompt_and_parses(self):
        registry = PromptRegistry()
        client = ScriptedLLM(
            responses={"voice_triage": '{"stuck": true, "category": "error_loop"}'}
        )
        result = run_prompt(client, registry, "voice_triage", {"event_digest": "x" * 10})
        assert result.parsed == {"stuck": True, "category": "error_loop"}
        assert result.tier == "small"  # from prompt frontmatter

    def test_inputs_land_in_input_block(self):
        registry = PromptRegistry()
        captured = {}

        def fake(*, prompt_id, prompt, tier):
            captured["prompt"] = prompt
            return '{"ok": true}'

        result = run_prompt(CallableLLM(fake), registry, "same_problem_judge", {"problem": "abc"})
        assert result.parsed == {"ok": True}
        assert "# Input" in captured["prompt"]
        assert "## problem" in captured["prompt"]

    def test_repair_attempt_recovers(self):
        registry = PromptRegistry()
        calls = {"n": 0}

        def flaky(*, prompt_id, prompt, tier):
            calls["n"] += 1
            return "nicht json" if calls["n"] == 1 else '{"ok": 1}'

        result = run_prompt(CallableLLM(flaky), registry, "voice_triage")
        assert result.parsed == {"ok": 1}
        assert calls["n"] == 2

    def test_repair_exhausted_raises(self):
        registry = PromptRegistry()
        client = ScriptedLLM(responses={"voice_triage": "immer noch kein json"})
        with pytest.raises(PromptError):
            run_prompt(client, registry, "voice_triage")
        assert len(client.calls) == 2  # original + one repair attempt

    def test_budget_tracks_calls(self):
        registry = PromptRegistry()
        budget = BudgetTracker()
        client = ScriptedLLM(responses={"voice_triage": '{"a": 1}'})
        run_prompt(client, registry, "voice_triage", budget=budget)
        assert budget.usage["small"] > 0

    def test_render_input_block_format(self):
        block = render_input_block({"a": "1", "b": "2"})
        assert block.startswith("# Input")
        assert "## a" in block and "## b" in block


class TestJsonRoundtrip:
    def test_scripted_json_payload_parses(self):
        payload = {"topic": "t", "claim": "c", "steps": ["a", "b"]}
        client = ScriptedLLM(responses={"research_note": json.dumps(payload)})
        result = run_prompt(client, PromptRegistry(), "research_note", {"raw": "x"})
        assert result.parsed == payload
