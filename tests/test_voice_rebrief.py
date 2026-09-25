"""VOICE-03: a rebrief names four things, may not do two, and one pause is not self-clearing.

    "Rebrief nennt Befund, Evidence, betroffene Zielkriterien und konkreten nächsten
     Schritt. Kein vollständiges Neuschreiben des Goals, keine Rechteausweitung.
     Wiederaufnahme nach Voice-Pause nach dokumentierter Runtime-Regel; Nutzer-Stop bleibt
     ausschließlich nutzerfreigabepflichtig."

One test per sentence — the four required parts, the two prohibitions, and the pause that
only the user may end.
"""

from __future__ import annotations

import pytest

from skaldir.voice_rebrief import (
    Rebrief,
    RebriefInvalid,
    ResumeRefused,
    UserStop,
    resume_after_voice_pause,
    validate_rebrief,
)


def _good(**kw) -> Rebrief:
    base = dict(
        finding="the run opened Firefox twice without looking first",
        evidence="two launch actions in the run transcript, no observe between them",
        criteria=("sources visited", "report written"),
        next_step="observe the window before the next launch",
    )
    base.update(kw)
    return Rebrief(**base)


# --------------------------------------------------------------------------- #
# the four parts
# --------------------------------------------------------------------------- #

def test_a_complete_rebrief_is_accepted():
    assert validate_rebrief(_good()) is not None


@pytest.mark.parametrize("missing", ["finding", "evidence", "next_step"])
def test_a_rebrief_without_a_required_part_is_refused(missing):
    with pytest.raises(RebriefInvalid):
        validate_rebrief(_good(**{missing: ""}))


def test_a_rebrief_without_criteria_is_refused():
    """Nothing says which of the run's goals this touches — it cannot steer."""
    with pytest.raises(RebriefInvalid, match="criteria"):
        validate_rebrief(_good(criteria=()))


def test_evidence_is_required_because_a_pause_is_not_ended_by_a_guess():
    with pytest.raises(RebriefInvalid, match="evidence"):
        validate_rebrief(_good(evidence="   "))


def test_continue_is_not_a_next_step():
    """"continue" re-enters the loop Voice just paused."""
    for step in ("continue", "keep going", "proceed", "weiter"):
        with pytest.raises(RebriefInvalid, match="not a step"):
            validate_rebrief(_good(next_step=step))


# --------------------------------------------------------------------------- #
# the two prohibitions
# --------------------------------------------------------------------------- #

def test_a_rebrief_may_restate_the_goal_but_not_rewrite_it():
    """Kein vollständiges Neuschreiben des Goals."""
    with pytest.raises(RebriefInvalid, match="not rewrite"):
        validate_rebrief(_good(goal="a completely different goal " * 400))


def test_a_rebrief_that_widens_rights_is_refused():
    """Keine Rechteausweitung."""
    for marker in ("grant me access", "skip approval", "expand scope", "widen rights"):
        with pytest.raises(RebriefInvalid, match="widen rights"):
            validate_rebrief(_good(next_step=f"then {marker} for the rest of the run"))


def test_a_short_goal_restatement_is_fine():
    """Restating the goal is allowed — only rewriting it is not."""
    assert validate_rebrief(_good(goal="write the report")).goal == "write the report"


# --------------------------------------------------------------------------- #
# the pause that only the user may end
# --------------------------------------------------------------------------- #

def test_a_user_stop_is_cleared_only_by_the_user():
    stop = UserStop(reason="double-escape", at=1.0)
    for by in ("voice", "scheduler", "runtime", "restart", ""):
        with pytest.raises(ResumeRefused, match="user-release"):
            stop.release(by=by)
        assert stop.released is False, f"{by!r} cleared a user stop"
    stop.release(by="user")
    assert stop.released is True and stop.released_by == "user"


def test_a_voice_pause_resumes_only_through_a_documented_rule():
    """Wiederaufnahme nach dokumentierter Runtime-Regel."""
    documented = ["operator resumes", "goal completed"]
    with pytest.raises(ResumeRefused, match="documented runtime rule"):
        resume_after_voice_pause(by="voice", rule="just keep going", documented_rules=documented)
    result = resume_after_voice_pause(by="operator", rule="operator resumes",
                                     documented_rules=documented)
    assert result["resumed"] is True and result["rule"] == "operator resumes"


def test_an_undocumented_rule_is_refused_not_forgiven():
    with pytest.raises(ResumeRefused):
        resume_after_voice_pause(by="voice", rule="auto", documented_rules=["operator resumes"])
    with pytest.raises(ResumeRefused):
        resume_after_voice_pause(by="voice", rule="", documented_rules=["operator resumes"])


def test_a_runtime_rule_never_clears_a_user_stop():
    """The two checks are independent — a documented rule is not enough on its own."""
    stop = UserStop(reason="double-escape", at=1.0)
    with pytest.raises(ResumeRefused, match="user-release"):
        resume_after_voice_pause(by="operator", rule="operator resumes",
                                documented_rules=["operator resumes"], user_stop=stop)
    assert stop.released is False

    stop.release(by="user")
    result = resume_after_voice_pause(by="operator", rule="operator resumes",
                                     documented_rules=["operator resumes"], user_stop=stop)
    assert result["resumed"] is True


# --------------------------------------------------------------------------- #
# VOICE-01 — silence beats a redundant intervention
# --------------------------------------------------------------------------- #

def test_the_assessment_gate_is_cost_based_so_voice_does_not_speak_after_every_tool():
    """VOICE-02: kein vollstaendiger Supervisor-Aufruf nach jedem Tool."""
    from skaldir.voice import assess_intervention

    quiet = assess_intervention(trigger_kind="repeat", severity=1, occurrences=1)
    loud = assess_intervention(trigger_kind="repeat", severity=3, occurrences=6)
    assert quiet.intervene is False, "one mild occurrence must not spend a pause"
    assert loud.intervene is True, "a repeated severe problem must"
    # the decision is measured and estimated, so VOICE-04 can count what it cost
    assert "method" in quiet.estimated
    assert "occurrences" in quiet.measured
