"""JEV-02/VOICE-04 — the parts that were *not* already covered.

The core of JEV-02 ("Jev erteilt keine Rechte und ersetzt keine Approval-Policy") is
already held by ``tests/test_jev_facade_rules.py``:

* ``test_authority_is_advisory_and_grants_stay_empty`` — ``authority`` cannot be
  "authoritative", ``certifies`` cannot be True, ``grants`` cannot carry anything, and
  ``status`` cannot be "passed" (so Jev cannot claim a goal PASS either).
* ``test_completion_gate_never_reads_jev`` — the PASS producers do not know Jev exists,
  which is JEV-01's "nicht mit VERIFY zusammenlegen" in the direction that matters.

Those are re-verified here by importing the same module rather than by copying them. What
is added below is the ground the existing file does not cover.
"""

from __future__ import annotations

import inspect

import pytest

from skaldir.jev import contracts as C


# --------------------------------------------------------------------------- #
# existing coverage, asserted by name so it cannot quietly disappear
# --------------------------------------------------------------------------- #

def test_the_core_jev02_invariants_are_still_held_by_the_existing_suite():
    """A guard on the guard: the file that holds JEV-02 must keep holding it."""
    import pathlib

    rules = (pathlib.Path(__file__).parent / "test_jev_facade_rules.py").read_text(encoding="utf-8")
    assert "test_authority_is_advisory_and_grants_stay_empty" in rules, (
        "the test that holds JEV-02 was renamed or removed — restore it before deleting this"
    )
    assert "test_completion_gate_never_reads_jev" in rules, (
        "the test that keeps Jev and VERIFY apart was renamed or removed"
    )


# --------------------------------------------------------------------------- #
# new ground
# --------------------------------------------------------------------------- #

def test_grants_are_empty_no_matter_how_the_decision_ends():
    """The existing test checks one outcome. A grant must be impossible on *every* one."""
    fields = C.JevDecision.model_fields
    grants = fields["grants"]
    # max_length=0 is the structural guarantee; anything that loosens it is a defect
    metadata = {m for m in getattr(grants, "metadata", ())}
    assert any(getattr(m, "max_length", None) == 0 for m in metadata), (
        f"`grants` lost its max_length=0 cap: {grants}"
    )
    assert grants.default == () or grants.default_factory is not None, (
        "a decision must start with no grants, not with a populated list"
    )


def test_the_three_constants_are_literal_types_not_mere_conventions():
    """`Literal` is what makes them un-arguable. A `bool`/`str` would not be."""
    fields = C.JevDecision.model_fields
    for name, expected in (("authority", "advisory"), ("certifies", False)):
        annotation = str(fields[name].annotation)
        assert "Literal" in annotation, (
            f"{name} is typed as {annotation!r} — a Literal is what forbids the other value"
        )
        assert repr(expected) in annotation or str(expected) in annotation, (
            f"{name} is a Literal but not of {expected!r}: {annotation}"
        )


def test_the_module_documents_that_it_is_not_a_permission_or_a_verdict():
    """JEV-02 in the words a human reads first."""
    doc = (C.__doc__ or "")
    lowered = doc.lower()
    assert "permission" in lowered, "the contract must say it is not a permission"
    assert "verdict" in lowered or "advisory" in lowered


def test_requires_approval_is_documented_as_reported_never_granted():
    """An approval *state* echoed from the gate must not read as an approval *action*."""
    source = inspect.getsource(C)
    assert "Reported, never granted" in source, (
        "the contract must say out loud that this echoes the gate rather than replacing it"
    )
    fields = getattr(C, "Route", type("", (), {}))
    model_fields = getattr(fields, "model_fields", {})
    if "permission_state" in model_fields:
        annotation = model_fields["permission_state"].annotation
        names = set(getattr(annotation, "__args__", ()) or ())
        # the vocabulary is a state, never an action: nothing here can "approve"
        assert not any(word in str(n).lower() for n in names for word in ("approve", "grant")), (
            f"the permission vocabulary grew an action: {names}"
        )


def test_a_decision_records_the_cost_of_deciding_so_a_free_path_is_visible():
    """JEV-02: a decision without a model call must be distinguishable from one with.

    VOICE-04 asks for exactly this measurement ("Interventionen und Kosten messen").
    """
    fields = C.JevDecision.model_fields
    for name in ("latency_ms", "attempts", "provider_mode"):
        assert name in fields, f"{name} is what makes the cost of deciding measurable"
    assert fields["attempts"].default >= 1, "a decision counts at least one attempt"


def test_a_refusal_carries_a_reason_and_a_fallback_not_silence():
    fields = C.JevDecision.model_fields
    assert "refusal_reason" in fields
    assert "fallback" in fields, "a refusal must say what should happen instead"
