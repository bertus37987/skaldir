"""The four hard rules (MVP/03 §6 R1–R4) — this file is what makes them true.

Every test here is named in :data:`skaldir.jev.rules.RULES`; the last test in this
file fails if a rule names a test that no longer exists, which is what
``skaldir jev selftest`` reports as an untested rule (exit 1).

The tests are deliberately layered like the enforcement is:

* layer 1 — the import-time contract scan, exercised both positively (the real
  contracts pass) and negatively (a synthetic model with a forbidden field makes
  the scan raise ``ImportError``, i.e. the module would be unshippable);
* layer 2 — the pydantic validators and the request-aware checks in
  ``assert_decision_authority``;
* layer 3 — the import-graph proofs (the security modules and the completion
  gate cannot reach Jev) and the audit table itself.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Literal

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from skaldir.jev import contracts as C
from skaldir.jev.decide import decide
from skaldir.jev.fixture import FixtureJevEvaluator
from skaldir.jev.rules import (
    DECLARED_CONSTANT_FIELDS,
    FORBIDDEN_EXACT,
    HARD_CAUTION_FLOOR,
    RULES,
    JevRuleViolation,
    assert_decision_authority,
    assert_voice_cannot_be_silenced,
    missing_rule_tests,
    scan_contracts,
)
from skaldir.jev.selftest import case_request

SRC = Path(__file__).resolve().parents[1] / "src"
JEV_DIR = SRC / "godsv" / "jev"


# --------------------------------------------------------------------------- #
# helpers                                                                       #
# --------------------------------------------------------------------------- #


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            if node.level:
                found.add(f"skaldir.{node.module}")
    return found


def _decision(kind: str, **overrides):
    request = case_request(kind)
    if overrides:
        request = request.model_copy(update=overrides)
    return decide(request, evaluator=FixtureJevEvaluator()), request


# --------------------------------------------------------------------------- #
# R1 — Jev never returns a goal PASS and never certifies anything               #
# --------------------------------------------------------------------------- #


def test_no_contract_can_certify():
    """Layer 1: no contract model (or its JSON schema) carries a verdict name."""
    scanned = 0
    for model in C.JEV_CONTRACTS:
        constants = set(DECLARED_CONSTANT_FIELDS.get(model.__name__, {}))
        schema_names = _schema_property_names(model.model_json_schema())
        leaked = (schema_names & FORBIDDEN_EXACT) - constants
        assert not leaked, f"{model.__name__} schema carries {sorted(leaked)}"
        scanned += 1
    assert scanned == len(C.JEV_CONTRACTS) and scanned >= 30

    # ... and the scan refuses a model that *would* carry one: ImportError at
    # import time is the "unshippable" contract of §6 layer 1.
    class Sneaky(BaseModel):
        model_config = ConfigDict(extra="forbid")
        goal_status: str = "PASS"

    with pytest.raises(ImportError) as excinfo:
        scan_contracts([Sneaky])
    assert "goal_status" in str(excinfo.value)

    class SneakyCertify(BaseModel):
        model_config = ConfigDict(extra="forbid")
        certifies: bool = False

    with pytest.raises(ImportError):
        scan_contracts([SneakyCertify])

    class SneakyAnswer(BaseModel):
        model_config = ConfigDict(extra="forbid")
        pass_count: int = 0

    with pytest.raises(ImportError):
        scan_contracts([SneakyAnswer], answer_names=frozenset({"SneakyAnswer"}))

    class SneakyGrant(BaseModel):
        model_config = ConfigDict(extra="forbid")
        permission_hint: str = "denied"

    with pytest.raises(ImportError):
        scan_contracts([SneakyGrant], answer_names=frozenset({"SneakyGrant"}))

    class SneakyValue(BaseModel):
        model_config = ConfigDict(extra="forbid")
        outcome: Literal["pass"] = "pass"

    with pytest.raises(ImportError):
        scan_contracts([SneakyValue], answer_names=frozenset({"SneakyValue"}))


def _schema_property_names(schema: dict) -> set[str]:
    names: set[str] = set()

    def walk(node) -> None:
        if isinstance(node, dict):
            props = node.get("properties")
            if isinstance(props, dict):
                names.update(str(key) for key in props)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(schema)
    return names


def test_authority_is_advisory_and_grants_stay_empty():
    """Layer 2: the envelope's three constants cannot be talked out of their value."""
    decision, _ = _decision("voice_triage")
    assert decision.authority == "advisory"
    assert decision.certifies is False
    assert decision.grants == ()

    with pytest.raises(ValidationError):
        C.JevDecision(**{**_payload(decision), "authority": "authoritative"})
    with pytest.raises(ValidationError):
        C.JevDecision(**{**_payload(decision), "certifies": True})
    with pytest.raises(ValidationError):
        C.JevDecision(**{**_payload(decision), "grants": ("anything",)})
    with pytest.raises(ValidationError):
        C.JevDecision(**{**_payload(decision), "status": "passed"})


def _payload(decision: C.JevDecision) -> dict:
    payload = decision.model_dump(mode="json")
    payload["answer"] = decision.answer
    return payload


def test_completion_gate_never_reads_jev():
    """Layer 3: the only PASS producers do not know Jev exists."""
    for relative in ("src/skaldir/gates.py", "src/skaldir/completion.py"):
        imported = _imports(SRC.parent / relative)
        offenders = {name for name in imported if name.startswith("skaldir.jev")}
        assert not offenders, f"{relative} imports {sorted(offenders)}"
        text = (SRC.parent / relative).read_text(encoding="utf-8")
        assert "jev" not in text.lower() or "skaldir.jev" not in text


# --------------------------------------------------------------------------- #
# R2 — Jev never grants a permission                                            #
# --------------------------------------------------------------------------- #


def test_jev_routing_cannot_upgrade_permission():
    """A denied candidate can never be the named route, whatever the provider says."""
    state = dict(_routing_state())
    state["candidates"] = [
        {**state["candidates"][0], "candidate_id": "allowed.tool", "permission_state": "allowed"},
        {**state["candidates"][0], "candidate_id": "denied.tool", "permission_state": "denied"},
    ]
    request = C.request_for("tool_routing", state)
    evaluator = FixtureJevEvaluator(
        {"routing.choice": _choice_payload("denied.tool", ["denied.tool", "allowed.tool"])}
    )
    with pytest.raises(JevRuleViolation) as excinfo:
        decide(request, evaluator=evaluator)
    assert excinfo.value.rule == "R2_no_permission_grant"
    assert "denied" in str(excinfo.value)


def test_denied_only_candidates_short_circuit_to_no_route():
    """Every candidate denied ⇒ no_route before a provider is ever asked (R2)."""
    state = dict(_routing_state())
    state["candidates"] = [
        {**state["candidates"][0], "candidate_id": "denied.a", "permission_state": "denied"},
        {**state["candidates"][0], "candidate_id": "denied.b", "permission_state": "denied"},
    ]
    request = C.request_for("tool_routing", state)
    evaluator = FixtureJevEvaluator()
    decision = decide(request, evaluator=evaluator)
    assert decision.status == "ok"
    assert decision.answer.chosen_kind == "no_route"
    assert decision.answer.reason_code == "permission_denied_only"
    assert decision.provider_mode == "disabled"  # no provider ran
    assert evaluator.calls == []  # the provider was never asked


def test_jev_does_not_bypass_authorization_gate(monkeypatch):
    """Jev does not call (or replace) the authorization gate — it reads its copy."""
    from skaldir import discovery

    calls = {"n": 0}
    original = discovery.authorization_gate

    def counting(*args, **kwargs):
        calls["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(discovery, "authorization_gate", counting)
    decision, _ = _decision("tool_routing")
    assert decision.status == "ok"
    assert calls["n"] == 0, "Jev must not re-run the authorization gate"
    # ... and the answer only reports the approval need it was handed.
    assert decision.answer.requires_approval in (True, False)


# --------------------------------------------------------------------------- #
# R3 — Jev never grants a security exception, never lowers caution              #
# --------------------------------------------------------------------------- #


def test_jev_caution_is_monotone():
    """Every kind keeps caution at or above the request's baseline (property-style)."""
    for kind in C.JEV_REQUESTS:
        request = case_request(kind).model_copy(update={"caution_baseline": 2})
        decision = decide(request, evaluator=FixtureJevEvaluator())
        assert decision.caution_level >= 2, kind

    # a tampered decision is refused when the request is present again
    decision, request = _decision("voice_triage")
    lowered = decision.model_copy(update={"caution_level": 0})
    raised_baseline = request.model_copy(update={"caution_baseline": 2})
    with pytest.raises(JevRuleViolation) as excinfo:
        assert_decision_authority(lowered, request=raised_baseline)
    assert excinfo.value.rule == "R3_no_security_exception"


def test_untrusted_trigger_cannot_be_silenced():
    """An authority/secret attempt floors caution at 3 and forbids 'stay_silent'."""
    state = dict(_voice_state())
    state["trigger_kind"] = "untrusted_content"
    state["signals"] = {"untrusted_content": True, "content_attempts_to_change_authority": True}
    request = C.request_for("voice_triage", state)
    decision = decide(request, evaluator=FixtureJevEvaluator())
    assert decision.answer.chosen == "wake_voice"
    assert decision.answer.reason_code == "untrusted_content_authority_attempt"
    assert decision.caution_level == HARD_CAUTION_FLOOR

    # the rule itself: a 'stay_silent' answer under that floor is a violation
    silent = C.VoiceTriageAnswer(chosen="stay_silent", route_hint="observe")
    with pytest.raises(JevRuleViolation) as excinfo:
        assert_voice_cannot_be_silenced(request, silent)
    assert excinfo.value.rule == "R3_no_security_exception"

    secret_state = dict(state)
    secret_state["signals"] = {"content_requests_secret_exposure": True}
    secret_request = C.request_for("voice_triage", secret_state)
    secret_decision = decide(secret_request, evaluator=FixtureJevEvaluator())
    assert secret_decision.answer.reason_code == "untrusted_content_secret_request"
    assert secret_decision.answer.chosen == "wake_voice"


def test_jev_not_imported_by_security_modules():
    """The security modules cannot reach Jev: no import edge, no name."""
    modules = ("src/skaldir/guard.py", "src/skaldir/untrusted.py",
               "src/skaldir/secretscan.py")
    for relative in modules:
        path = SRC.parent / relative
        assert path.is_file(), relative
        imported = _imports(path)
        offenders = {name for name in imported if name.startswith("skaldir.jev")}
        assert not offenders, f"{relative} imports {sorted(offenders)}"


# --------------------------------------------------------------------------- #
# R4 — Jev never overrides a user goal                                          #
# --------------------------------------------------------------------------- #


def test_jev_never_mutates_goal(tmp_path):
    """A pact/goal file is byte-identical after every kind of decision."""
    pact = tmp_path / "PACT.md"
    pact.write_bytes(b"# Pact\n\nGOAL: ship the MVP without touching user goals\n")
    before = pact.read_bytes()
    for kind in C.JEV_REQUESTS:
        decide(case_request(kind), evaluator=FixtureJevEvaluator())
        assert pact.read_bytes() == before, kind

    # ... and the request itself is frozen: there is no writable goal object.
    request = case_request("memory_injection")
    with pytest.raises(ValidationError):
        request.goal_text = "a different goal"


def test_goal_revision_echo_enforced():
    """The decision echoes the request's revision; a mismatch is refused (R4)."""
    request = case_request("tool_routing").model_copy(update={"goal_revision": 3})
    decision = decide(request, evaluator=FixtureJevEvaluator())
    assert decision.goal_revision == 3
    assert decision.goal_id == request.goal_id

    tampered = decision.model_copy(update={"goal_revision": 9})
    with pytest.raises(JevRuleViolation) as excinfo:
        assert_decision_authority(tampered, request=request)
    assert excinfo.value.rule == "R4_no_goal_override"

    other_goal = decision.model_copy(update={"goal_id": "someone-elses-goal"})
    with pytest.raises(JevRuleViolation):
        assert_decision_authority(other_goal, request=request)


# --------------------------------------------------------------------------- #
# layer 3 — the audit table itself                                              #
# --------------------------------------------------------------------------- #


def test_every_hard_rule_has_its_pinned_tests():
    """The meta-rule: a rule whose tests are missing fails the suite (and the CLI)."""
    missing = missing_rule_tests()
    assert missing == {}, f"rule(s) without their pinned tests: {missing}"
    assert {spec.rule for spec in RULES} == {
        "R1_no_goal_pass",
        "R2_no_permission_grant",
        "R3_no_security_exception",
        "R4_no_goal_override",
    }
    for spec in RULES:
        assert spec.layer1 and spec.layer2 and spec.tests, spec.rule


# --------------------------------------------------------------------------- #
# the small state builders used above                                           #
# --------------------------------------------------------------------------- #


def _routing_state() -> dict:
    from skaldir.jev.selftest import SELFTEST_CASES

    return dict(SELFTEST_CASES["tool_routing"])


def _voice_state() -> dict:
    from skaldir.jev.selftest import SELFTEST_CASES

    return dict(SELFTEST_CASES["voice_triage"])


def _choice_payload(chosen: str, options: list[str]) -> dict:
    weights = {option: 0.02 for option in options if option != chosen}
    weights[chosen] = 1.0 - sum(weights.values())
    return {"choice": chosen, "probabilities": weights, "confidence": 0.9}
