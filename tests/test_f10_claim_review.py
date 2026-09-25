"""F10 — the claim→review cycle rides the channels that actually fire.

Host finding: the after-edits `pre_verify` gate is unreachable in the
desktop flow because nothing fills `agent._turn_file_mutation_paths`.
The review therefore lives where turns really pass: recorded at turn end
(record_claims in post_llm_call), delivered once in the next turn's
contract block (pre_llm_call).
"""

import godsv_plugin
import pytest
from test_contract_sessions import _FakeCtx

from skaldir.completion import DodItem


class TestClaimReview:
    @pytest.fixture
    def ctx(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.setenv("GODSV_MODE", "whisper")
        monkeypatch.delenv("GODSV_GUARD_FORCE", raising=False)
        godsv_plugin._SESSIONS.clear()
        fake = _FakeCtx()
        godsv_plugin.register(fake)
        yield fake
        godsv_plugin._SESSIONS.clear()

    def _session_with_contract(self, ctx, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        ctx.hooks["on_session_start"](session_id="f10")
        session = godsv_plugin._SESSIONS["f10"]
        session._completion_store().begin_goal(
            "f10",
            "Keep the repo healthy",
            [
                DodItem(
                    id="c1",
                    text="the pytest suite is green",
                    blocking=True,
                    verification="pytest",
                )
            ],
        )
        return session

    def test_claim_against_open_contract_queues_a_review(self, ctx, monkeypatch, tmp_path):
        s = self._session_with_contract(ctx, monkeypatch, tmp_path)
        s.record_claims("All tests are green and everything is done.")
        assert s._pending_claim_review is not None
        assert s._pending_claim_review["open_items"] >= 1
        assert "tests" in s._pending_claim_review["categories"]

    def test_review_is_delivered_once_in_the_next_contract_block(self, ctx, monkeypatch, tmp_path):
        s = self._session_with_contract(ctx, monkeypatch, tmp_path)
        s.record_claims("All tests are green and everything is done.")
        first = str(s.contract_context("next message"))
        assert "voice·claim-check" in first
        assert s._pending_claim_review is None  # delivered exactly once
        second = str(s.contract_context("another message"))
        assert "voice·claim-check" not in second

    def test_no_contract_no_review(self, ctx, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        ctx.hooks["on_session_start"](session_id="f10b")
        s = godsv_plugin._SESSIONS["f10b"]
        s.record_claims("All tests are green and everything is done.")
        assert s._pending_claim_review is None
