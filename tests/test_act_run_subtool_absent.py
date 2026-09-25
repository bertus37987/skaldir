"""TDD ``window-absent`` preconditions: a flow whose first act CREATES the window.

Defect (confirmed, report 54/60): ``act subtool run open_browser`` refused with
``status=reobserve`` before executing anything — ``check_blind_replay`` demands that
every declared precondition/anchor is observed *right now*, which can never hold for
the window the flow itself opens (and the old anchor text ``Browser window`` never
matched the real titles ``Firefox`` / ``Mozilla Firefox`` anyway).

The fix chosen here (one mechanism, minimal schema): a precondition may declare
``"expect": "absent"`` (source ``window_title``). Then

* the absence itself is the observed condition — satisfied when the window is NOT
  on the display,
* the other ``window_title`` preconditions/anchors move to ``awaiting_appearance``
  instead of ``missing``: the pre-check SKIPS the reobserve-refusal and the action
  list runs first,
* the end_state check still verifies against the real observation afterwards — the
  invariant that decides success is unchanged,
* host-fact misses and app-state (unobservable) checks refuse exactly as before.

The learned-subtool loop is closed the same way: ``act subtool learn`` declares its
draft entry precondition ``window-absent`` (the learner never observed a future run's
start), so learn → save → ``act subtool run <name>`` executes instead of refusing.
"""
from __future__ import annotations

import re

import pytest
from skaldir import act_service as S
from skaldir.computer_use import ActionResult, DryRunRunner


# --------------------------------------------------------------------------- #
# helpers (the patterns of tests/test_act_service.py)
# --------------------------------------------------------------------------- #


def _action(description: str, *, expect: str = "ok") -> dict:
    return {
        "kind": "cli",
        "channel": "api",
        "argv": ["echo", expect],
        "description": description,
        "verify": {"expect_stdout_contains": expect},
    }


def _creates_window_spec(**overrides) -> dict:
    """A stored flow whose anchor is NOT on the display until its actions ran."""
    spec = {
        "name": "create_window_flow",
        "version": "1.0",
        "description": "a flow whose own first action creates the window it anchors on",
        "applicability": {
            "app": "probe",
            "app_versions": [">=1.0,<2"],
            "locales": ["en", "*"],
        },
        "preconditions": [
            {
                "id": "target_absent",
                "text": "TARGET-WINDOW",
                "source": "window_title",
                "expect": "absent",
            },
        ],
        "anchors": [
            {"id": "target_window", "text": "TARGET-WINDOW", "source": "window_title"},
        ],
        "actions": [_action("do the thing")],
        "end_state": {
            "channel": "window_title",
            "description": "the window the flow promised is on the display",
            "expect_observe_contains": "AFTER-WINDOW",
        },
        "verification_contract": "the display shows the window the flow created",
    }
    spec.update(overrides)
    return spec


def _runner(*observations: str, description: str = "do the thing",
            stdout: str = "ok") -> DryRunRunner:
    return DryRunRunner(
        observations=list(observations) or [""],
        behaviors={description: ActionResult(ok=True, stdout=stdout)},
    )


# --------------------------------------------------------------------------- #
# the schema: one new, validated form
# --------------------------------------------------------------------------- #


def test_the_window_absent_form_is_valid_and_only_in_its_own_shape():
    spec = _creates_window_spec()
    assert S.subtool_errors(spec) == []

    bad_expect = _creates_window_spec(
        preconditions=[
            {"id": "p", "text": "TARGET-WINDOW", "source": "window_title",
             "expect": "sometimes"},
        ]
    )
    assert any("expect" in problem for problem in S.subtool_errors(bad_expect))

    bad_source = _creates_window_spec(
        preconditions=[
            {"id": "p", "text": "GUI", "source": "host", "fact": "gui_image",
             "expect": "absent"},
        ]
    )
    assert any(
        "window_title" in problem for problem in S.subtool_errors(bad_source)
    )

    on_anchor = _creates_window_spec(
        preconditions=[
            {"id": "p", "text": "TARGET-WINDOW", "source": "window_title",
             "expect": "absent"},
        ],
        anchors=[
            {"id": "a", "text": "TARGET-WINDOW", "source": "window_title",
             "expect": "absent"},
        ],
    )
    assert any("precondition" in problem for problem in S.subtool_errors(on_anchor))
    # save_subtool refuses the same declarations (the validation is the one path)
    with pytest.raises(ValueError):
        S.save_subtool(bad_expect, state="/nonexistent-for-save")
    with pytest.raises(ValueError):
        S.save_subtool(on_anchor, state="/nonexistent-for-save")


# --------------------------------------------------------------------------- #
# the pre-check: absent is observed, "not there yet" is not a refusal
# --------------------------------------------------------------------------- #


def test_observe_anchors_reads_the_absence_and_queues_the_window_for_appearance():
    spec = _creates_window_spec()
    cold = S.observe_anchors(spec, "(no windows)")
    # the declared absence is the observed condition …
    assert "TARGET-WINDOW" in cold["observed_start_conditions"]
    # … and the anchor the flow creates is queued, never "missing" (no refusal)
    assert cold["missing"] == ()
    assert any("TARGET-WINDOW" in item for item in cold["awaiting_appearance"])
    # a window-title pattern matches like the focus title: regex OR literal
    patterned = _creates_window_spec(
        preconditions=[
            {"id": "absent", "text": "Mozilla Firefox|Firefox", "source": "window_title",
             "expect": "absent"},
        ],
        anchors=[
            {"id": "browser_window", "text": "Mozilla Firefox|Firefox",
             "source": "window_title"},
        ],
    )
    hot = S.observe_anchors(patterned, "root — Mozilla Firefox")
    assert hot["missing"] == ()
    assert any("already" in item for item in hot["notes"])


def test_running_a_flow_whose_window_does_not_exist_yet_actually_runs(tmp_path):
    """The red defect: this used to refuse with reobserve and execute nothing."""
    spec = _creates_window_spec()
    assert S.subtool_errors(spec) == []
    S.save_subtool(spec, state=tmp_path)
    record = S.run_subtool(
        "create_window_flow",
        state=tmp_path,
        runner=_runner("(no windows)", "TARGET-WINDOW AFTER-WINDOW"),
        write_record=False,
    )
    assert record["status"] != "reobserve", record.get("reason")
    assert record["status"] == "verified", record.get("reason")
    assert record["verified"] is True
    assert record["exit_code"] == S.EXIT_OK
    # the action list really ran first …
    assert record["executed"] == 1
    # … and the end state — unchanged invariant — is verified against real observation
    assert record["end_state"]["ok"] is True
    # the pre-check reports both facts instead of hiding them
    check = record["precondition_check"]
    assert "TARGET-WINDOW" in check["observed_start_conditions"]
    assert any("TARGET-WINDOW" in item for item in check["awaiting_appearance"])


def test_a_host_fact_miss_still_refuses_even_with_the_absent_declaration(tmp_path):
    """The absent declaration never weakens a check that has a working channel."""
    facts = S.host_facts()["facts"]
    if facts.get("gui_image"):
        pytest.skip("this host measures gui_image=true — the miss path needs it false")
    spec = _creates_window_spec(
        preconditions=[
            {"id": "target_absent", "text": "TARGET-WINDOW", "source": "window_title",
             "expect": "absent"},
            {"id": "gui", "text": "GUI", "source": "host", "fact": "gui_image"},
        ],
    )
    S.save_subtool(spec, state=tmp_path, force=True)
    record = S.run_subtool(
        "create_window_flow", state=tmp_path, runner=_runner("(no windows)"),
        write_record=False,
    )
    assert record["status"] == "reobserve"
    assert "actions" not in record


# --------------------------------------------------------------------------- #
# the builtin: open_browser declares its own window absent at start
# --------------------------------------------------------------------------- #


def test_the_builtin_open_browser_declares_absent_and_anchors_on_the_real_title():
    spec = S.builtin_subtools()["open_browser"]
    assert S.subtool_errors(spec) == []
    absent = [
        entry for entry in spec["preconditions"] if entry.get("expect") == "absent"
    ]
    assert absent, "open_browser's own window must be declared absent at start"
    assert absent[0]["source"] == "window_title"
    # the anchor (and the absence) match the titles the display really reports
    anchor = spec["anchors"][0]["text"]
    assert re.search(anchor, "Mozilla Firefox"), anchor
    assert re.search(anchor, "Firefox"), anchor
    # and the absence pattern is the same one the focus action targets
    assert re.search(absent[0]["text"], "Mozilla Firefox")


def test_open_browser_runs_past_the_pre_check_when_the_window_is_not_there(tmp_path):
    """Report 54's scenario, end to end: refusal before, actions + end state after."""
    facts = S.host_facts()["facts"]
    spec = S.builtin_subtools()["open_browser"]
    S.save_subtool(spec, state=tmp_path, force=True)
    runner = DryRunRunner(
        observations=["(no windows)", "Mozilla Firefox"],
        behaviors={
            "launch the browser": ActionResult(ok=True, stdout="LAUNCHED"),
            "focus the browser window": ActionResult(ok=True, stdout="FOCUSED"),
        },
    )
    record = S.run_subtool("open_browser", state=tmp_path, runner=runner,
                           write_record=False)
    # whatever the host facts say, the WINDOW check never refuses any more …
    if not (facts.get("isolated_display") and facts.get("browser_installed")):
        assert record["status"] != "reobserve" or "host" in str(
            record.get("reason", "")
        ), record.get("reason")
        if record["status"] == "reobserve":
            return  # the host lacks a fact — the honest refusal, not the anchor one
    # … and with the host facts present the whole flow verifies
    assert record["status"] == "verified", record.get("reason")
    assert record["executed"] == 2
    assert record["end_state"]["ok"] is True


# --------------------------------------------------------------------------- #
# the learned loop: learn → save → run executes (no reobserve refusal)
# --------------------------------------------------------------------------- #


def _seed_session(runtime, session_id: str) -> None:
    from skaldir import events as events_mod

    runtime.mkdir(parents=True, exist_ok=True)
    store = events_mod.EventStore(runtime / "events.db")
    step = 0
    # an open-browser-like sequence: a launch step then a focus step, twice
    seq = [("cli", "Browser window"), ("window", "Browser window")]
    for cycle in range(2):
        for kind, anchor in seq:
            step += 1
            store.append(
                session_id,
                step,
                "cu_action",
                {
                    "step": f"step-{step}",
                    "channel": "api",
                    "kind": kind,
                    "ok": True,
                    "anchor": anchor,
                    "detail": f"{anchor}-{cycle}",
                },
            )
    store.close()


def test_learn_then_run_executes_the_draft_without_the_reobserve_refusal(tmp_path):
    runtime = tmp_path / "rt"
    _seed_session(runtime, "sess-open-like")
    learned = S.learn_subtool("sess-open-like", runtime=runtime, state=runtime)
    assert learned.get("learned") is True, learned
    name = learned["subtool"]["name"]
    stored, origin, _path = S.load_subtool(name, state=runtime)
    assert origin == "saved"
    # the draft declares its entry state window-absent: the learner never observed
    # a future run's start, and an open-like sequence creates the entry window
    assert stored["preconditions"][0].get("expect") == "absent"
    assert S.subtool_errors(stored) == []

    descriptions = [action["description"] for action in stored["actions"]]
    # Scripted fixture: the cold observation has no window; the post-act observation
    # carries the step tokens the learner's own verify expectations ask for (the
    # learner rewrites a window-kind step's fallback verify to its full step token).
    runner = DryRunRunner(
        observations=[
            "(no windows)",
            "Browser window cli+Browser window window+Browser window",
        ],
        behaviors={
            description: ActionResult(ok=True, stdout="ok")
            for description in descriptions
        },
    )
    record = S.run_subtool(name, state=runtime, runner=runner, write_record=False)
    assert record["status"] != "reobserve", record.get("reason")
    assert record["status"] == "verified", record.get("reason")
    assert record["executed"] == len(stored["actions"])
    assert record["end_state"]["ok"] is True
