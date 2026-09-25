"""P4 — voice strictness is a real runtime setting (thresholds shift live)."""

import pytest

from skaldir.monitor import SEV_WARN, Signal
from skaldir.pause import PauseStore, VoicePause, load_strictness, save_strictness


def test_strictness_roundtrips_and_validates(tmp_path):
    assert load_strictness(tmp_path) == "standard"  # default without a file
    assert save_strictness(tmp_path, "strict") == "strict"
    assert load_strictness(tmp_path) == "strict"
    with pytest.raises(ValueError):
        save_strictness(tmp_path, "nonsense")
    # an invalid stored value also degrades to standard (never crashes)
    (tmp_path / "settings.yaml").write_text("strictness: bogus\n", encoding="utf-8")
    assert load_strictness(tmp_path) == "standard"


def _pause_after_n(tmp_path, level, n):
    save_strictness(tmp_path, level)
    store = PauseStore(tmp_path / f"pauses-{level}.db")
    vp = VoicePause(
        store, "s1", research_runner=None, cooldown_seconds=0.0, state_dir=tmp_path
    )
    sig = Signal(kind="loop", severity=SEV_WARN, fingerprint="lp", message="loop detected")
    for i in range(n):
        pid = vp.consider(step=4 * (i + 1), signals=[sig], problem="x")
        if pid:
            return i + 1
    return None


class TestStrictnessShiftsThresholds:
    """TRIGGER_RULES['loop'] = 2: strict→1 (first signal), relaxed→3."""

    def test_strict_pauses_on_the_first_signal(self, tmp_path):
        assert _pause_after_n(tmp_path, "strict", 4) == 1

    def test_standard_waits_for_the_second(self, tmp_path):
        assert _pause_after_n(tmp_path, "standard", 4) == 2

    def test_relaxed_waits_for_the_third(self, tmp_path):
        assert _pause_after_n(tmp_path, "relaxed", 5) == 3
