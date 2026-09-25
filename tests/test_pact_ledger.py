"""Tests for Pact (task contract) and Ledger (continuity state)."""

import pytest

from skaldir.ledger import STATUS_AT_RISK, STATUS_EVIDENCED, STATUS_IN_PROGRESS, Ledger
from skaldir.pact import Pact


class TestPact:
    def test_example_is_valid(self):
        Pact.example().validate()  # must not raise

    def test_validate_rejects_empty_goal(self):
        with pytest.raises(ValueError, match="goal"):
            Pact(goal="  ").validate()

    def test_validate_rejects_missing_acceptance(self):
        with pytest.raises(ValueError, match="acceptance"):
            Pact(goal="x").validate()

    def test_validate_rejects_duplicate_ids(self):
        pact = Pact.from_dict(
            {"goal": "x", "acceptance": [{"id": "a", "text": "1"}, {"id": "a", "text": "2"}]}
        )
        with pytest.raises(ValueError, match="duplicate"):
            pact.validate()

    def test_from_dict_assigns_ids_to_string_acceptance(self):
        pact = Pact.from_dict({"goal": "x", "acceptance": ["erst", "zweit"]})
        assert [c.id for c in pact.acceptance] == ["ac1", "ac2"]

    def test_yaml_roundtrip(self, tmp_path):
        pact = Pact.example()
        path = tmp_path / "pact.yaml"
        pact.to_yaml(path)
        assert Pact.from_yaml(path).to_dict() == pact.to_dict()


class TestLedger:
    @staticmethod
    def _make() -> Ledger:
        return Ledger.from_pact(Pact.example())

    def test_from_pact_copies_criteria(self):
        ledger = self._make()
        assert set(ledger.criteria) == {"ac1", "ac2"}
        assert ledger.completion() == (0, 2)
        assert not ledger.is_complete()

    def test_unknown_criterion_raises(self):
        with pytest.raises(KeyError):
            self._make().set_status("nope", STATUS_IN_PROGRESS)

    def test_evidenced_requires_evidence(self):
        ledger = self._make()
        with pytest.raises(ValueError, match="evidence"):
            ledger.set_status("ac1", STATUS_EVIDENCED)
        ledger.set_status("ac1", STATUS_EVIDENCED, evidence="pytest -q: 12 passed")
        assert ledger.criteria["ac1"].status == STATUS_EVIDENCED

    def test_completion_and_render(self):
        ledger = self._make()
        ledger.set_status("ac1", STATUS_EVIDENCED, evidence="test run: 12 passed")
        ledger.set_status("ac2", STATUS_AT_RISK)
        ledger.add_decision("Ansatz A verworfen", reason="zu langsam")
        ledger.add_question("GPU oder CPU?")
        markdown = ledger.render_markdown()
        assert "1/2 backed" in markdown
        assert "☑ [ac1]" in markdown
        assert "⚠ [ac2]" in markdown
        assert "zu langsam" in markdown
        assert "GPU oder CPU?" in markdown

    def test_save_load_roundtrip(self, tmp_path):
        ledger = self._make()
        ledger.set_status("ac1", STATUS_IN_PROGRESS)
        path = tmp_path / "ledger.yaml"
        ledger.save(path)
        assert Ledger.load(path).to_dict() == ledger.to_dict()

class TestAcceptanceTextNormalization:
    """A nested mapping is never a criterion text. Live-found 13.09.: a pact
    whose acceptance item carried the full item as its `text` leaked the dict
    repr into the contract ("{'id': 'c1', …}"), which then showed up as the
    criterion text in the chat live block."""

    def test_nested_mapping_text_takes_the_inner_text(self):
        pact = Pact.from_dict(
            {
                "goal": "g",
                "acceptance": [
                    {
                        "id": "c1",
                        "text": {"id": "c1", "text": "The real text", "blocking": True},
                    }
                ],
            }
        )
        assert pact.acceptance[0].text == "The real text"

    def test_plain_text_stays_unchanged(self):
        pact = Pact.from_dict({"goal": "g", "acceptance": [{"id": "c1", "text": "plain"}]})
        assert pact.acceptance[0].text == "plain"

    def test_missing_text_stays_empty(self):
        pact = Pact.from_dict({"goal": "g", "acceptance": [{"id": "c1"}]})
        assert pact.acceptance[0].text == ""
