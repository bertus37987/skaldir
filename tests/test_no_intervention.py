"""§10 — the Investigator may conclude NO_INTERVENTION_NEEDED."""

import time
from types import SimpleNamespace

from skaldir.pause import STATUS_ABANDONED, PauseStore, VoicePause


class _NoInterventionRunner:
    def run(self, **kwargs):
        return SimpleNamespace(
            status="ok",
            intervention_needed=False,
            no_intervention_reason="normal recoverable debugging",
            research_id="r-noaction",
            duration_ms=1,
            redirect_md=lambda: "must not be delivered",
        )


class _RedirectRunner:
    def run(self, **kwargs):
        return SimpleNamespace(
            status="ok",
            intervention_needed=True,
            no_intervention_reason="",
            research_id="r-redirect",
            duration_ms=1,
            redirect_md=lambda: "## Direction\nDo X",
        )


def _wait_status(store, pid, wanted=("ready", "abandoned", "stale", "failed")):
    for _ in range(80):
        lease = store.get(pid)
        if lease and lease["status"] in wanted:
            return lease
        time.sleep(0.05)
    return store.get(pid)


def test_no_intervention_closes_the_pause_without_redirect(tmp_path):
    store = PauseStore(tmp_path / "pauses.db")
    emits = []
    pause = VoicePause(
        store,
        "s1",
        research_runner=_NoInterventionRunner(),
        cooldown_seconds=0.0,
        emit=lambda kind, payload: emits.append((kind, payload)),
    )
    pid = pause.consider(step=4, contradiction={"fingerprint": "c1", "claim": "x"})
    lease = _wait_status(store, pid)
    assert lease["status"] == STATUS_ABANDONED
    assert "no intervention needed" in (lease["released_reason"] or "")
    assert pause.deliverable(step=6) is None  # nothing interrupts the agent
    assert any(kind == "pause_no_action" for kind, _payload in emits)


def test_default_still_delivers_the_redirect(tmp_path):
    store = PauseStore(tmp_path / "pauses.db")
    pause = VoicePause(store, "s1", research_runner=_RedirectRunner(), cooldown_seconds=0.0)
    pid = pause.consider(step=4, contradiction={"fingerprint": "c2", "claim": "x"})
    lease = _wait_status(store, pid)
    assert lease["status"] == "ready"
