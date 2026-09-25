"""P1-5 — repeated interventions on the same failure class demand a novel strategy.

The escalation rule: from the third pause on the same trigger class
(counted durably from the store, not from in-memory counters that die at
turn boundaries) the problem/redirect explicitly requires a novel strategy
and names the repetition — blind retry loops are not allowed.
"""

from skaldir.pause import PauseStore, VoicePause


class TestNovelStrategyEscalation:
    def test_third_pause_on_same_class_requires_novel_strategy(self, tmp_path):
        store = PauseStore(tmp_path / "pauses.db")
        events: list[tuple[str, dict]] = []
        vp = VoicePause(
            store,
            "s1",
            research_runner=None,
            cooldown_seconds=0.0,
            emit=lambda kind, payload: events.append((kind, payload)),
        )
        for i in range(3):
            pid = vp.consider(
                step=10 * (i + 1),
                contradiction={"fingerprint": f"c{i}", "claim": "same claim"},
            )
            assert pid is not None, f"pause {i + 1} was not requested"
        history = store.history_for_session("s1")
        assert len(history) == 3
        # newest first: the THIRD pause carries the escalation, the first not
        assert "REPEATED INTERVENTION (#3 for 'contradiction')" in history[0]["problem"]
        assert "REPEATED INTERVENTION" not in (history[-1]["problem"] or "")
        pause_emits = [payload for kind, payload in events if kind == "pause_requested"]
        assert pause_emits[0]["novel_strategy_required"] is False
        assert pause_emits[-1]["repeated"] == 2
        assert pause_emits[-1]["novel_strategy_required"] is True

    def test_escalation_counts_durable_history_not_memory(self, tmp_path):
        """A fresh controller (new turn/session object) still sees the repeats."""
        store = PauseStore(tmp_path / "pauses.db")
        first = VoicePause(store, "s1", research_runner=None, cooldown_seconds=0.0)
        assert first.consider(step=2, contradiction={"fingerprint": "c0", "claim": "x"})
        assert first.consider(step=4, contradiction={"fingerprint": "c1", "claim": "x"})
        # the session object (and with it the controller's memory) is disposed
        second = VoicePause(store, "s1", research_runner=None, cooldown_seconds=0.0)
        assert second.consider(step=6, contradiction={"fingerprint": "c2", "claim": "x"})
        newest = store.history_for_session("s1")[0]
        assert "REPEATED INTERVENTION (#3" in newest["problem"]
