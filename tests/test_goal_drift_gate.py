"""§33: gradual goal drift must escalate — varied paths, not only one repeated.

Found in the harness: three scope_drift signals over three DIFFERENT paths
(the realistic gradual drift) never reached the threshold because counts are
kept per fingerprint (one per path). One path repeated three times paused,
three different paths did not. These tests pin the fixed semantics.
"""

import time
from types import SimpleNamespace

from skaldir.events import EventStore
from skaldir.monitor import Monitor
from skaldir.pact import AcceptanceCriterion, Pact
from skaldir.pause import PauseStore, VoicePause


class _OkRunner:
    def run(self, **kwargs):
        return SimpleNamespace(
            status="ok",
            intervention_needed=True,
            no_intervention_reason="",
            research_id="r-drift",
            duration_ms=1,
            redirect_md=lambda: "## Direction\nstay in scope",
        )


PACT = Pact(
    goal="keep the core healthy",
    acceptance=[AcceptanceCriterion("a1", "suite green")],
    scope_paths=["src/**", "tests/**"],
)


def _pause_for(tmp_path, sid):
    events = EventStore(tmp_path / f"{sid}.events.db")
    store = PauseStore(tmp_path / f"{sid}.pauses.db")
    pause = VoicePause(store, sid, research_runner=_OkRunner(), cooldown_seconds=0.0)
    return events, store, pause


def _feed(events, monitor, sid, paths):
    signals = []
    for step, path in enumerate(paths, start=5):
        event = events.append(sid, step, "file_edit", {"path": path, "added": 3, "removed": 0})
        signals.extend(monitor.ingest(event))
    return signals


class TestGradualDriftGate:
    def test_varied_outside_paths_reach_the_threshold(self, tmp_path):
        events, store, pause = _pause_for(tmp_path, "drift-varied")
        monitor = Monitor(pact=PACT)
        paths = ["docs/a.md", "infra/b.sh", "scratch/c.py"]
        signals = _feed(events, monitor, "drift-varied", paths)
        kinds = {signal.kind for signal in signals}
        assert kinds == {"scope_drift"}
        pid = pause.consider(step=30, signals=signals, problem="gradual drift")
        assert pid is not None, "three scope violations over different paths must escalate"
        # let the research thread finish, then close cleanly
        for _ in range(40):
            lease = store.get(pid)
            if lease and lease["status"] in ("ready", "abandoned", "stale"):
                break
            time.sleep(0.05)
        events.close()
        store.close()

    def test_one_off_edit_stays_quiet(self, tmp_path):
        """A single out-of-scope edit is a signal, not a verdict."""
        events, store, pause = _pause_for(tmp_path, "drift-single")
        monitor = Monitor(pact=PACT)
        signals = _feed(events, monitor, "drift-single", ["docs/notes.md"])
        pid = pause.consider(step=30, signals=signals, problem="single edit")
        assert pid is None
        events.close()
        store.close()

    def test_in_scope_edits_stay_quiet(self, tmp_path):
        events, store, pause = _pause_for(tmp_path, "drift-inside")
        monitor = Monitor(pact=PACT)
        signals = _feed(events, monitor, "drift-inside", ["src/core.py", "tests/test_x.py"])
        assert signals == []
        pid = pause.consider(step=30, signals=signals, problem="in scope")
        assert pid is None
        events.close()
        store.close()
