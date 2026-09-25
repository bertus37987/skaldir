"""Wave 3 (Voice axis) — the System-One triage is reachable for the agent.

Measured 17.09. at HEAD: `SystemOneService.voice_triage` had **no production caller**. The §33
facade, the composed question pack and the §49 decision policy existed and were covered by tests,
but "runtime event → System One triage → observe / verify / escalate" never ran on a real path.

`godsv_capabilities({"action": "triage", "kind": …})` is that path. It drives the existing pieces —
flags from the runtime `settings.yaml`, the evaluator the flags ask for (the LLM adapter uses the
session's host-owned client), a composed question pack per trigger kind, a sanitized state (§40),
an evaluation context — and it stays honest:

* flags off / provider disabled / no client for the adapter → ``legacy`` with the reason;
* a model answer that does not parse → ``legacy`` as well, never a fabricated advisory;
* a real advisory is marked ``via: system_one_service`` and ``live_jev`` stays false (a Jev live
  reading is never claimed by the LLM adapter);
* the deterministic hint is always computed and returned unchanged (§27: one contract).
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import godsv_plugin
import pytest

from skaldir.monitor import Signal
from skaldir.system_one_questions import compose_voice_triage, default_question_registry
from skaldir.voice import PATH_ADVISORY, PATH_LEGACY, PATH_SHADOW, Voice

KIND = "repeated_test_failure"

FLAGS_ON = (
    "system_one:\n"
    "  enabled: true\n"
    "  provider: llm_system_one_adapter\n"
    "  voice_triage: true\n"
)


def _answers() -> dict:
    """A valid ``{"answers": …}`` block for exactly the composed questions of this trigger kind."""

    questions = compose_voice_triage(KIND, registry=default_question_registry()).questions
    answers: dict = {}
    for question in questions:
        data = question.as_dict()
        if data["kind"] == "noul":
            answers[data["id"]] = {"value": 0.2}
        elif data["kind"] == "choice":
            options = list(data.get("options") or ["unknown"])
            share = 1.0 / len(options)
            answers[data["id"]] = {
                "choice": options[0],
                "probabilities": {option: share for option in options},
                "confidence": 0.6,
            }
        else:  # score
            answers[data["id"]] = {
                "score": 2.0,
                "legend": [0.0, 1.0],
                "probabilities": {"2": 1.0},
                "confidence": 0.6,
            }
    return {"answers": answers}


class FakeHostLLM:
    """Hermes' host side-LLM returns an object with ``.text`` (see the plugin's ContextLLM)."""

    payload = json.dumps(_answers())

    def complete(self, messages, **kwargs):  # noqa: ARG002 — host signature
        return SimpleNamespace(text=self.payload)


class FakeCtx:
    def __init__(self) -> None:
        self.hooks: dict = {}
        self.llm = FakeHostLLM()
        self.tools: dict = {}

    def register_hook(self, name, fn):
        self.hooks[name] = fn

    def register_tool(self, **kwargs):
        self.tools[kwargs.get("name")] = kwargs


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    godsv_plugin._SESSIONS.clear()
    yield
    for session in list(godsv_plugin._SESSIONS.values()):
        try:
            session.end()
        except Exception:  # noqa: BLE001 — teardown must not mask the result
            pass
    godsv_plugin._SESSIONS.clear()


def _settings(tmp_path, body: str) -> None:
    path = tmp_path / "godsv" / "settings.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _triage(**params) -> dict:
    params.setdefault("kind", KIND)
    return json.loads(godsv_plugin.godsv_capabilities({"action": "triage", **params}))


class TestTheHonestStates:
    def test_a_triage_without_a_signal_kind_is_refused(self):
        godsv_plugin.register(FakeCtx())
        payload = _triage(kind="")
        assert payload["available"] is False
        assert payload["path"] == PATH_LEGACY
        assert "signal kind" in payload["reason"]

    def test_with_the_flags_off_the_path_is_legacy_with_the_reason(self, tmp_path):
        godsv_plugin.register(FakeCtx())
        payload = _triage()
        assert payload["available"] is False
        assert payload["path"] == PATH_LEGACY
        assert "disabled" in payload["reason"]
        assert payload["flags"]["enabled"] is False
        assert payload["advisory"] in ({}, None)

    def test_without_a_client_the_adapter_is_reported_missing(self, tmp_path, monkeypatch):
        _settings(tmp_path, FLAGS_ON)
        monkeypatch.delenv("GODSV_LLM", raising=False)
        godsv_plugin.register(FakeCtx())
        payload = _triage()
        assert payload["available"] is False
        assert "no model client" in payload["reason"]

    def test_an_unparsable_model_answer_stays_legacy(self, tmp_path, monkeypatch):
        """A lying/garbled model reading must not turn into an advisory."""
        _settings(tmp_path, FLAGS_ON)
        monkeypatch.setenv("GODSV_LLM", "1")
        ctx = FakeCtx()
        ctx.llm.payload = "not json at all"
        godsv_plugin.register(ctx)
        payload = _triage()
        assert payload["available"] is False
        assert payload["path"] == PATH_LEGACY
        assert not payload["advisory"]
        assert payload["hint"], "the deterministic hint is computed regardless"


class TestTheRealRun:
    def test_a_valid_reading_reaches_the_advisory_path(self, tmp_path, monkeypatch):
        _settings(tmp_path, FLAGS_ON)
        monkeypatch.setenv("GODSV_LLM", "1")
        godsv_plugin.register(FakeCtx())
        payload = _triage(step=3, message="the same test failed twice")
        assert payload["available"] is True, payload
        assert payload["path"] in (PATH_ADVISORY, PATH_SHADOW), payload
        assert payload["via"] == "system_one_service"
        assert payload["live_jev"] is False, "the LLM adapter is never a Jev live reading"
        assert payload["questions"], payload
        assert payload["hint"] and payload["hint"]["kind"], payload

    def test_the_deterministic_hint_is_unchanged(self, tmp_path, monkeypatch):
        """§27: Voice consults System One, it never replaces its own triage."""
        _settings(tmp_path, FLAGS_ON)
        monkeypatch.setenv("GODSV_LLM", "1")
        godsv_plugin.register(FakeCtx())
        payload = _triage(step=3)
        signal = Signal(kind=KIND, severity=1, fingerprint=KIND, message="")
        expected = Voice().consider([signal], 3, evidence=[KIND])
        assert payload["hint"]["kind"] == str(getattr(expected, "kind", "") or "")
        assert payload["hint"]["text"] == str(getattr(expected, "text", "") or "")
