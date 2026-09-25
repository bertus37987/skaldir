"""JEV-05 — state sanitizer pipeline (§40/§83).

ROT-FIRST evidence, captured before the module existed:

    $ .venv/bin/python -m pytest tests/test_system_one_sanitizer.py -p no:randomly
    E   ModuleNotFoundError: No module named 'godsv.system_one'   (0.19s, collection error)

Pipeline under test (§40):

    Runtime Snapshot → Scope Filter → Secret Redaction → Project Isolation
                     → State Minimizer → Provider Adapter

Assertions are on **behaviour**: the payload the provider adapter actually
receives. Three negative cases are mandatory:

1. an API key / session token present in the runtime snapshot never appears in
   the adapter's received payload (asserted on the captured prompt);
2. a foreign project's state is excluded (project isolation);
3. the minimized state carries only the allowed keys.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from skaldir.system_one import SystemOneState
from skaldir.system_one_adapters import LLMSystemOneAdapter
from skaldir.system_one_questions import compose_voice_triage
from skaldir.system_one_sanitizer import (
    DEFAULT_ALLOWED_KEYS,
    SanitizerPolicy,
    StateSanitizer,
    isolate_project,
    minimize,
    redact_secrets,
    runtime_snapshot,
    scope_filter,
)

API_KEY = "sk-live-3f9a1c7e4b2d8a6f0c5e"
# A well-formed JWT: the repo's single secret-scanning contract
# (godsv.secretscan) recognises the shape, so content-based redaction applies
# to free text as well — no second scanner is invented here (§27).
SESSION_TOKEN = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghij"


class CapturingLLM:
    def __init__(self, response: str) -> None:
        self.response = response
        self.prompts: list[str] = []

    def complete(self, *, prompt_id: str, prompt: str, tier: str) -> str:
        self.prompts.append(prompt)
        return self.response


def _run(coro):
    return asyncio.run(coro)


def _kind_correct_answers(qset, value: float = 0.5) -> dict:
    """Provider payloads that respect the per-kind answer contract (§38)."""
    answers: dict[str, dict] = {}
    for question in qset.questions:
        if question.kind == "noul":
            answers[question.id] = {"value": value}
        elif question.kind == "choice":
            options = question.options
            share = round(1.0 / len(options), 6)
            probs = {option: share for option in options}
            probs[options[-1]] = round(1.0 - share * (len(options) - 1), 6)
            answers[question.id] = {
                "choice": options[0],
                "probabilities": probs,
                "confidence": value,
            }
        else:
            bins = [str(item) for item in question.legend]
            share = round(1.0 / len(bins), 6)
            probs = {bucket: share for bucket in bins}
            probs[bins[-1]] = round(1.0 - share * (len(bins) - 1), 6)
            answers[question.id] = {
                "score": question.legend[0],
                "legend": list(question.legend),
                "probabilities": probs,
                "confidence": value,
            }
    return answers


def _dirty_snapshot() -> dict:
    return {
        "goal": "fix the flaky test",
        "step": 12,
        "signals": ["loop", "error_burst"],
        "env": {"OPENAI_API_KEY": API_KEY, "PATH": "/usr/bin"},
        "headers": {"Authorization": f"Bearer {SESSION_TOKEN}"},
        "terminal_history": [f"export TOKEN={API_KEY}", "pytest -q", "ls"],
        "chat_history": ["user: please fix", "assistant: ok"],
        "projects": {
            "secret-other-repo": {"goal": "leak me"},
            "godsv": {"goal": "fix the flaky test"},
        },
        "unused_debug_blob": "x" * 5000,
    }


def _policy(**overrides) -> SanitizerPolicy:
    base = {
        "allowed_keys": frozenset(
            {
                "goal",
                "step",
                "signals",
                "terminal_history",
                "chat_history",
                "projects",
                "env",
                "headers",
            }
        ),
        "project_id": "godsv",
    }
    base.update(overrides)
    return SanitizerPolicy(**base)


# --------------------------------------------------------------------------- #
# the mandatory negative case: a secret in the snapshot                       #
# --------------------------------------------------------------------------- #


def test_secret_in_snapshot_never_reaches_the_adapter():
    """The assertion is on what the adapter actually received."""
    sanitizer = StateSanitizer(policy=_policy())
    # The scope deliberately INCLUDES the secret-bearing keys: redaction, not
    # the scope filter, has to be what protects them.
    state = sanitizer.sanitize(
        _dirty_snapshot(),
        scope="task",
        keys=("env", "headers", "terminal_history", "chat_history", "goal", "signals"),
    )

    assert state.fingerprint and isinstance(state, SystemOneState)
    serialized_state = json.dumps(state.payload)
    assert API_KEY not in serialized_state
    assert SESSION_TOKEN not in serialized_state

    questions = compose_voice_triage("loop")
    llm = CapturingLLM(
        json.dumps({"answers": _kind_correct_answers(questions)})
    )
    adapter = LLMSystemOneAdapter(llm, model="test")
    from skaldir.system_one import EvaluationContext

    _run(
        adapter.evaluate(
            state=state,
            questions=questions,
            context=EvaluationContext(run_id="r", goal_id="g"),
        )
    )
    assert llm.prompts, "adapter did not receive any payload"
    received = "\n".join(llm.prompts)
    assert API_KEY not in received
    assert SESSION_TOKEN not in received
    assert "sk-live-" not in received


def test_secret_redaction_replaces_values_by_key_name():
    cleaned = redact_secrets(
        {"env": {"OPENAI_API_KEY": API_KEY}, "headers": {"x-token": SESSION_TOKEN}}
    )
    assert API_KEY not in json.dumps(cleaned)
    assert SESSION_TOKEN not in json.dumps(cleaned)
    assert "REDACTED" in json.dumps(cleaned)


def test_secret_redaction_also_catches_free_text_values():
    cleaned = redact_secrets({"note": f"use {SESSION_TOKEN} to log in"})
    assert SESSION_TOKEN not in json.dumps(cleaned)


# --------------------------------------------------------------------------- #
# foreign project isolation                                                    #
# --------------------------------------------------------------------------- #


def test_foreign_project_state_is_excluded():
    cleaned = isolate_project(_dirty_snapshot(), project_id="godsv")
    assert "secret-other-repo" not in json.dumps(cleaned)
    assert "leak me" not in json.dumps(cleaned)
    assert cleaned["projects"] == {"godsv": {"goal": "fix the flaky test"}}


def test_foreign_project_entries_in_a_list_are_excluded():
    snapshot = {
        "project_states": [
            {"project_id": "other-repo", "goal": "leak me"},
            {"project_id": "godsv", "goal": "keep me"},
        ]
    }
    cleaned = isolate_project(snapshot, project_id="godsv")
    assert cleaned["project_states"] == [{"project_id": "godsv", "goal": "keep me"}]


def test_project_isolation_runs_inside_the_pipeline():
    state = StateSanitizer(policy=_policy()).sanitize(
        _dirty_snapshot(), scope="task", keys=("projects",)
    )
    assert json.dumps(state.payload).count("godsv") >= 1
    assert "secret-other-repo" not in json.dumps(state.payload)


# --------------------------------------------------------------------------- #
# minimization: only allowed keys                                               #
# --------------------------------------------------------------------------- #


def test_minimized_state_carries_only_allowed_keys():
    state = StateSanitizer(policy=_policy()).sanitize(
        _dirty_snapshot(), scope="task", keys=("goal", "signals")
    )
    assert set(state.payload) <= set(DEFAULT_ALLOWED_KEYS) | {"goal", "signals"}
    assert set(state.payload) == {"goal", "signals"}
    assert state.payload["goal"] == "fix the flaky test"


def test_scope_filter_drops_keys_outside_the_requested_scope():
    filtered = scope_filter(_dirty_snapshot(), allowed_keys=_policy().allowed_keys, keys=("goal",))
    assert set(filtered) == {"goal"}


def test_state_minimizer_truncates_oversized_lists_and_strings():
    minimized = minimize(
        {"terminal_history": [f"cmd-{index}" for index in range(200)], "note": "y" * 4000},
        max_list_items=10,
        max_string_chars=100,
        max_depth=4,
    )
    assert len(minimized["terminal_history"]) == 10
    assert len(minimized["note"]) <= 100


def test_runtime_snapshot_rejects_a_non_mapping():
    from skaldir.system_one import SystemOneContractError

    with pytest.raises(SystemOneContractError):
        runtime_snapshot(["not", "a", "mapping"])


def test_pipeline_is_deterministic_for_the_same_input():
    sanitizer = StateSanitizer(policy=_policy())
    first = sanitizer.sanitize(_dirty_snapshot(), scope="task", keys=("goal", "signals"))
    second = sanitizer.sanitize(_dirty_snapshot(), scope="task", keys=("goal", "signals"))
    assert first.fingerprint == second.fingerprint


def test_sanitized_state_records_the_input_scope():
    state = StateSanitizer(policy=_policy()).sanitize(
        _dirty_snapshot(), scope="voice_triage", keys=("goal",)
    )
    assert state.input_scope == "voice_triage"
