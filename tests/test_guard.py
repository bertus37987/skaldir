"""Completion & evidence guards — claims, evidence index, contradiction."""

from skaldir.events import EventStore
from skaldir.guard import (
    build_evidence_index,
    evaluate_completion,
    extract_assumptions,
    extract_claims,
    find_contradiction,
)
from skaldir.pact import AcceptanceCriterion, Pact


def _store(tmp_path):
    return EventStore(tmp_path / "events.db")


def _append(store, step, kind, payload):
    return store.append("g1", step, kind, payload, ts=float(step))


def _edit(store, step, path="src/a.py"):
    _append(store, step, "file_edit", {"path": path, "added": "def f():\n    return 1\n"})


def _tool_ok(store, step, name="terminal", command="pytest -q"):
    _append(store, step, "tool_call", {"name": name, "args": {"command": command}})
    _append(store, step + 1, "tool_result", {"tool": name, "exit_code": 0, "stdout": "3 passed"})


def _tool_err(store, step, text="ImportError: cannot import name 'cv_split'"):
    _append(store, step, "tool_call", {"name": "terminal", "args": {"command": "python app.py"}})
    _append(store, step + 1, "tool_result", {"tool": "terminal", "error": text})


class TestClaimExtraction:
    def test_positive_claims(self):
        text = (
            "Ich bin fertig. Alle Tests laufen grün. Die API liefert jetzt Daten. "
            "Das UI funktioniert. Und insgesamt funktioniert jetzt alles."
        )
        categories = {category for category, _ in extract_claims(text)}
        assert {"done", "tests", "api", "ui", "works"} <= categories

    def test_negations_are_not_claims(self):
        text = "Noch nicht fertig — die Tests sind nicht grün, das UI funktioniert nicht."
        assert extract_claims(text) == []

    def test_assumptions(self):
        text = "Hermes v0.21.1 unterstützt das. Python 3.11 ist installiert."
        found = extract_assumptions(text)
        assert len(found) == 2
        assert any("Hermes" in item for item in found)


class TestEvidenceIndex:
    def test_index_classifies_actions(self, tmp_path):
        store = _store(tmp_path)
        try:
            _edit(store, 1)
            _tool_ok(store, 2, command="pytest -q")
            _tool_ok(store, 4, name="browser", command="screenshot http://x")
            _tool_ok(store, 6, name="terminal", command="curl http://api")
            index = build_evidence_index(store, "g1")
            assert index.last_edit_step == 1
            assert index.tests_ok_steps == [3]
            assert index.visual_ok_steps == [5]
            assert index.api_ok_steps == [7]
        finally:
            store.close()


class TestEvaluateCompletion:
    def test_false_completion_needs_verification(self, tmp_path):
        store = _store(tmp_path)
        try:
            _edit(store, 1)
            verdict = evaluate_completion(
                store, "g1", "Ich bin fertig, die Funktion ist implementiert."
            )
            assert verdict.needs_continue
            assert verdict.status == "unsupported"
            assert any("without verification" in f.missing for f in verdict.findings)
            assert "completion guard" in verdict.message()
        finally:
            store.close()

    def test_verified_completion_is_silent(self, tmp_path):
        store = _store(tmp_path)
        try:
            _edit(store, 1)
            _tool_ok(store, 2, command="pytest -q")
            verdict = evaluate_completion(store, "g1", "Fertig — alle Tests laufen grün.")
            assert verdict.status == "ok"
            assert not verdict.needs_continue
        finally:
            store.close()

    def test_claim_without_any_action_is_unsupported(self, tmp_path):
        store = _store(tmp_path)
        try:
            verdict = evaluate_completion(store, "g1", "Erledigt, alles funktioniert.")
            assert verdict.status == "unsupported"
            assert any("with no executed check" in f.missing for f in verdict.findings)
        finally:
            store.close()

    def test_ui_claim_needs_visual_evidence(self, tmp_path):
        store = _store(tmp_path)
        try:
            _edit(store, 1, path="ui/app.tsx")
            _tool_ok(store, 2, command="pytest -q")  # tests are NOT visual evidence
            verdict = evaluate_completion(store, "g1", "Das UI funktioniert jetzt.")
            assert verdict.needs_continue
            assert any(f.category == "ui" for f in verdict.findings)
        finally:
            store.close()

    def test_done_with_generic_run_after_edit_is_supported(self, tmp_path):
        """Tool results after the edit count as evidence (spec: Test-/Tool-Ergebnisse)."""
        store = _store(tmp_path)
        try:
            _edit(store, 1)
            _tool_ok(store, 2, command="python3 app.py")  # any successful run
            verdict = evaluate_completion(store, "g1", "Fertig, ist implementiert.")
            assert verdict.status == "ok"
        finally:
            store.close()

    def test_acceptance_criteria_mapped(self, tmp_path):
        store = _store(tmp_path)
        try:
            pact = Pact(
                goal="CV ergänzen",
                acceptance=[AcceptanceCriterion("ac1", "suite/cv_test.py ist grün")],
            )
            _edit(store, 1)
            verdict = evaluate_completion(
                store, "g1", "Implementation abgeschlossen.", pact=pact
            )
            assert any(f.criterion_id == "ac1" for f in verdict.findings)
        finally:
            store.close()

    def test_message_lists_evidence_gap(self, tmp_path):
        store = _store(tmp_path)
        try:
            _edit(store, 5, path="src/app.py")
            verdict = evaluate_completion(store, "g1", "Alles fertig, läuft.")
            message = verdict.message()
            assert "step 5" in message
            compact = verdict.message(compact=True)
            assert len(compact) < len(message)
        finally:
            store.close()


class TestContradiction:
    def test_claim_then_recurring_error(self, tmp_path):
        store = _store(tmp_path)
        try:
            _tool_err(store, 1)  # error known
            _append(store, 3, "claim", {"text": "Alles läuft jetzt.", "categories": ["works"]})
            _tool_err(store, 4)  # same error again after the claim
            found = find_contradiction(store, "g1")
            assert found is not None
            assert found["claim_step"] == 3
            assert found["error_step"] == 5
        finally:
            store.close()

    def test_no_contradiction_without_claim(self, tmp_path):
        store = _store(tmp_path)
        try:
            _tool_err(store, 1)
            _tool_err(store, 3)
            assert find_contradiction(store, "g1") is None
        finally:
            store.close()


class TestEvidenceFreshness:
    def test_dev_null_redirect_never_moves_the_checkpoint(self, tmp_path):
        store = _store(tmp_path)
        _tool_ok(store, 1)  # an executed check at step 1
        _edit(store, 3, path="/tmp/real.txt")  # a real edit at step 3
        _append(store, 5, "file_edit", {"path": "/dev/null", "added": "grep x 2>/dev/null"})
        index = build_evidence_index(store, "g1")
        assert index.last_edit_step == 3
        # the check at step 1 is NOT after the checkpoint — that is the point:
        # moving the checkpoint to 5 would be the bug this guards.
        assert index.last_edit_step != 5

    def test_real_edit_after_check_keeps_it_stale(self, tmp_path):
        store = _store(tmp_path)
        _tool_ok(store, 1)
        _edit(store, 3, path="/tmp/real.txt")
        index = build_evidence_index(store, "g1")
        assert index.last_ok_after([1], 3) is None  # genuinely stale, as intended
