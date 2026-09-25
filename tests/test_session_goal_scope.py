"""The goal fact is **session-scoped** — a chat never adopts another session's goal.

The measured defect (2026-09-19): ``repl.Probes.goal()`` read *any* non-terminal goal out of
the runtime's ``runs.db``, so a fresh chat painted ``final1-i WORKING`` — the abandoned goal of
another session (lease owner ``pid=3571473``, dead; lease expired the day before). The store is
shared by every process that ever ran against the runtime, so "a goal is open" is not the same
fact as "this session has a goal".

The rule this file pins, from both ends:

* the **probe** (what the status line and the band paint) — the attached run's record, else the
  goal whose ``session_id`` is this session's, else ``none`` with the reason; other sessions'
  open goals are **counted in the detail**, never adopted;
* the **delivery target** (``/steer``/``/queue``) — the same scope, because messaging a goal
  that belongs to somebody else behind their back is the same defect with worse consequences.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from skaldir import repl
from skaldir import runs_store as R

MINE = "s-mine"
OTHER = "s-other"


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("GODSV_HOME", str(tmp_path))
    monkeypatch.setenv("SKALDIR_HOME", str(tmp_path))
    return tmp_path


def _store(runtime: Path) -> R.RunsStore:
    return R.RunsStore(R.run_store_path(runtime))


def _shell(tmp_path: Path, *, session_id: str, attach: str = "") -> repl.Shell:
    out, err = io.StringIO(), io.StringIO()
    options = repl.ShellOptions(probe=False, session_id=session_id, attach=attach)
    session = repl.Session(runtime=tmp_path, runner=lambda argv: 0, out=out, err=err)
    session.probes.attach = attach
    session.probes.session_id = session_id
    return repl.Shell(options, session=session, out=out, err=err, stdin=io.StringIO(""))


# --------------------------------------------------------------------------- #
# the probe                                                                     #
# --------------------------------------------------------------------------- #


def test_a_foreign_open_goal_is_counted_never_adopted(runtime):
    store = _store(runtime)
    try:
        foreign = store.start_goal("someone else's abandoned run", session_id=OTHER,
                                   criteria=["done"])
    finally:
        store.close()
    probes = repl.Probes(runtime, session_id=MINE)
    value = probes.goal()
    assert value.label == "none", "a stranger's goal is not this session's goal"
    assert foreign.goal_id[:8] not in value.label
    assert "1 open goal(s) belong to other sessions" in value.detail
    assert "not this chat's" in value.detail


def test_this_sessions_goal_is_the_one_shown(runtime):
    store = _store(runtime)
    try:
        mine = store.start_goal("make the tree clean", session_id=MINE, criteria=["done"])
        store.start_goal("the other one", session_id=OTHER, criteria=["done"])
    finally:
        store.close()
    value = repl.Probes(runtime, session_id=MINE).goal()
    assert value.label == f"{mine.goal_id[:8]} {mine.state}"
    assert value.detail.startswith("this session's goal")
    assert value.ok is True


def test_the_goal_probe_needs_a_session_to_scope_by(runtime):
    store = _store(runtime)
    try:
        store.start_goal("nobody owns this", session_id=OTHER, criteria=["done"])
    finally:
        store.close()
    value = repl.Probes(runtime, session_id="").goal()
    assert value.label == "none"
    assert "no run is attached to this session" in value.detail


def test_an_attached_run_is_named_even_when_it_is_another_sessions(runtime):
    """``--attach <goal-id>`` (and the hand-over) *is* the answer: that record, by id."""
    store = _store(runtime)
    try:
        foreign = store.start_goal("the run this chat is the surface of", session_id=OTHER,
                                   criteria=["done"])
    finally:
        store.close()
    value = repl.Probes(runtime, attach=foreign.goal_id, session_id=MINE).goal()
    assert value.label == f"{foreign.goal_id[:8]} {foreign.state}"
    assert value.detail.startswith("attached run")


def test_an_attached_id_that_is_not_in_this_store_says_so(runtime):
    store = _store(runtime)
    store.close()
    value = repl.Probes(runtime, attach="g-does-not-exist", session_id=MINE).goal()
    assert value.label == "none"
    assert "is not in" in value.detail


def test_a_missing_run_store_is_reported_not_guessed(runtime):
    value = repl.Probes(runtime, session_id=MINE).goal()
    assert value.label == "none"
    assert "no run store at" in value.detail


def test_a_terminal_goal_is_never_in_the_open_count(runtime):
    store = _store(runtime)
    try:
        done = store.start_goal("finished", session_id=OTHER, criteria=["done"])
        store.set_goal_state(done.goal_id, "VERIFIED_COMPLETE")
    finally:
        store.close()
    value = repl.Probes(runtime, session_id=MINE).goal()
    assert value.label == "none"
    assert "open goal(s)" not in value.detail


# --------------------------------------------------------------------------- #
# the delivery target: the same scope, because the consequences are worse        #
# --------------------------------------------------------------------------- #


def test_steering_targets_this_sessions_goal(runtime):
    store = _store(runtime)
    try:
        mine = store.start_goal("mine", session_id=MINE, criteria=["done"])
    finally:
        store.close()
    shell = _shell(runtime, session_id=MINE)
    assert shell.run_message_target() == (mine.goal_id, "")


def test_steering_never_targets_a_foreign_goal(runtime):
    store = _store(runtime)
    try:
        store.start_goal("someone else's", session_id=OTHER, criteria=["done"])
    finally:
        store.close()
    shell = _shell(runtime, session_id=MINE)
    goal_id, why = shell.run_message_target()
    assert goal_id == ""
    assert "no run is attached to this session" in why


def test_an_attached_shell_messages_the_run_it_is_the_surface_of(runtime):
    store = _store(runtime)
    try:
        goal = store.start_goal("the attached run", session_id=OTHER, criteria=["done"])
    finally:
        store.close()
    shell = _shell(runtime, session_id=MINE, attach=goal.goal_id)
    assert shell.run_message_target() == (goal.goal_id, "")


def test_a_message_to_a_goal_that_is_gone_is_refused_by_name(runtime):
    shell = _shell(runtime, session_id=MINE, attach="g-gone")
    goal_id, why = shell.run_message_target()
    assert goal_id == ""
    assert "is not in" in why


def test_a_queued_message_is_refused_by_name_when_no_run_is_attached(runtime):
    shell = _shell(runtime, session_id=MINE)
    assert shell.deliver_message("queue", "keep going") == 3, "blocked, and it says why"
