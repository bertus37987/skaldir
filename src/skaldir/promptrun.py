"""Run versioned prompts through an LLM client (input block + JSON parsing).

Inputs are appended as a separate ``# Input`` section — prompt bodies are
instructions and are never string-templated (our prompts contain literal
JSON braces). Untrusted content must already be ``spotlight()``-wrapped by
the caller (policy: external content is data, never instructions).

JSON handling: model output is parsed best-effort (code fences, surrounding
prose, first balanced object); on parse failure one repair attempt is made
with a strict "JSON only" instruction.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from .llm import BudgetTracker, LLMClient, LLMError
from .prompts import PromptRegistry


class PromptError(LLMError):
    """Raised when a prompt run fails to produce a valid result."""


def render_input_block(inputs: dict) -> str:
    lines = ["# Input"]
    for key, value in inputs.items():
        lines.append(f"## {key}")
        lines.append(str(value).strip())
    return "\n\n".join(lines)


def extract_json(text: str):
    """Best-effort JSON extraction from model output (fences, prose, nesting)."""
    candidate = text.strip()
    if candidate.startswith("```"):
        parts = candidate.split("```")
        if len(parts) >= 2:
            inner = parts[1]
            if inner.lower().startswith("json"):
                inner = inner[4:]
            candidate = inner.strip()
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass
    for opener, closer in (("{", "}"), ("[", "]")):
        start = candidate.find(opener)
        if start == -1:
            continue
        depth = 0
        for idx in range(start, len(candidate)):
            char = candidate[idx]
            if char == opener:
                depth += 1
            elif char == closer:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(candidate[start : idx + 1])
                    except json.JSONDecodeError:
                        break
    raise PromptError("no valid JSON found in model output")


@dataclass
class PromptResult:
    prompt_id: str
    tier: str
    raw: str
    parsed: dict | list | None


def run_prompt(
    client: LLMClient,
    registry: PromptRegistry,
    prompt_id: str,
    inputs: dict | None = None,
    *,
    tier: str | None = None,
    expect_json: bool = True,
    budget: BudgetTracker | None = None,
    repair_attempts: int = 1,
) -> PromptResult:
    prompt = registry.get(prompt_id)
    used_tier = tier or str(prompt.meta.get("model_tier", "small"))
    prompt_text = prompt.body
    if inputs:
        prompt_text = f"{prompt_text}\n\n{render_input_block(inputs)}"
    raw = client.complete(prompt_id=prompt.id, prompt=prompt_text, tier=used_tier)
    if budget is not None:
        budget.spend(used_tier, prompt_text, raw)
    parsed = None
    if expect_json:
        for attempt in range(repair_attempts + 1):
            try:
                parsed = extract_json(raw)
                break
            except PromptError:
                if attempt == repair_attempts:
                    raise
                repair = (
                    f"{prompt_text}\n\n# Previous response (invalid)\n{raw}\n\n"
                    "# Task\nReply with ONLY the corrected JSON, no prose, no fences."
                )
                raw = client.complete(prompt_id=prompt.id, prompt=repair, tier=used_tier)
                if budget is not None:
                    budget.spend(used_tier, repair, raw)
    return PromptResult(prompt_id=prompt.id, tier=used_tier, raw=raw, parsed=parsed)
