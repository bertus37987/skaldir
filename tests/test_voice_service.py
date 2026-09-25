"""``voice status|check|triage|explain`` — the SUPERVISE layer's CLI surface.

The fixtures store real trajectories through the real
:class:`godsv.events.EventStore` (no hand-written SQL, no fake store), so every
finding in these tests comes out of the same detectors the running supervisor uses
(``monitor`` / ``patterns`` / ``guard`` / ``voice`` / ``pause``).

The load-bearing test is
:meth:`TestTriage.test_stays_silent_when_the_agent_is_already_on_it`: the owner's
rule is that the Voice must NOT interfere when the agent has already recognised the
problem and is sensibly working on it.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from skaldir import voice_cli
from skaldir import voice_service as V
from skaldir.events import EventStore
from skaldir.pause import KIND_VOICE, PauseStore

# --------------------------------------------------------------------------- #
# fixtures: real stores, real events                                            #
# --------------------------------------------------------------------------- #


def _store(runtime: Path) -> EventStore:
    runtime.mkdir(parents=True, exist_ok=True)
    return EventStore(runtime / "events.db")


def _call(store: EventStore, session: str, step: int, command: str,
          tool: str = "terminal") -> None:
    store.append(session, step, "tool_call", {"name": tool, "args": {"command": command}})


def _result_ok(store: EventStore, session: str, step: int, tool: str = "terminal") -> None:
    store.append(session, step, "tool_result",
                 {"tool": tool, "exit_code": 0, "stdout": "ok", "ok": True})


def _result_error(store: EventStore, session: str, step: int, error: str,
                  tool: str = "terminal") -> None:
    store.append(session, step, "tool_result",
                 {"tool": tool, "exit_code": 1, "stderr": error, "error": error})


def _edit(store: EventStore, session: str, step: int, path: str, added: str) -> None:
    store.append(session, step, "file_edit", {"path": path, "added": added})


def problem_trajectory(runtime: Path, session: str = "problem") -> Path:
    """A stuck session: a loop, unhandled errors, a false claim, an unsafe call.

    Deliberately has: no edit, no successful run, three goal-alien edits, one
    blocked tool call and a completion claim with nothing behind it.
    """
    store = _store(runtime)
    try:
        store.append(session, 1, "goal_set_from_chat",
                     {"goal": "Prepare the quarterly release notes for the marketing site"})
        # the same failing command, three times, with the same API-shaped error
        for step in (2, 3, 4):
            _call(store, session, step, "pytest -q tests/test_auth.py")
            _result_error(store, session, step, "ImportError: cannot import name 'cv_split'")
        # a second, unrelated failure class
        _call(store, session, 5, "python -c 'import godsv.policy'")
        _result_error(store, session, 5, "NameError: name 'cfg' is not defined")
        # a blocked tool call (the pre-execution interception stores this event)
        store.append(session, 6, "tool_call_intercepted",
                     {"classification": "BLOCK", "tool": "terminal",
                      "reasons": ["destructive: rm_recursive"],
                      "directive": "pick a targeted path"})
        # a completion claim the trajectory backs with nothing
        store.append(session, 7, "claim",
                     {"text": "Implemented: the parser module is done and all tests are green",
                      "categories": ["done", "tests"]})
        # three edits in a row whose paths share no term with the goal
        _edit(store, session, 8, "src/skaldir/parser.py", "def parse(x):\n    return x\n")
        _edit(store, session, 9, "src/utils/helpers.py", "HELPERS = {}\n")
        _edit(store, session, 10, "tests/test_parser.py", "def test_parse():\n    assert True\n")
        # the guard's own record of the unbacked claim
        store.append(session, 10, "completion_verdict",
                     {"status": "INCOMPLETE", "escalate": True,
                      "fingerprint": "deadbeef",
                      "blockers": ["evidence: completion claimed but no executed check was "
                                   "provided as evidence"],
                      "majors": [], "evidence": []})
        store.append(session, 11, "thought", {"text": "keep going"})
    finally:
        store.close()
    return runtime / "events.db"


def handled_trajectory(runtime: Path, session: str = "handled") -> Path:
    """A session that hits real errors and then handles them properly.

    Two distinct error signatures, a localised fix in the file the error named, and
    a green run after the change — exactly the evidence
    :func:`godsv.voice.read_trajectory_health` calls "normal recoverable debugging".
    """
    store = _store(runtime)
    try:
        for step in (1, 3):  # the first repair attempt fails the same way twice
            _call(store, session, step, "pytest -q tests/test_auth.py")
            _result_error(store, session, step, "AssertionError: expected 3 got 4")
        for step in (5, 7):  # a second, different hypothesis fails twice
            _call(store, session, step, "python -c 'import godsv.auth'")
            _result_error(store, session, step,
                          "NameError: name 'cfg' is not defined (src/godsv/auth.py)")
        _edit(store, session, 9, "src/skaldir/auth.py", "cfg = load_config()\n")
        _call(store, session, 10, "pytest -q tests/test_auth.py")
        _result_ok(store, session, 11)
        store.append(session, 12, "thought",
                     {"text": "the cfg fix holds, tests are green again"})
    finally:
        store.close()
    return runtime / "events.db"


def clean_trajectory(runtime: Path, session: str = "clean") -> Path:
    """Edits that are verified after the change: no finding at all."""
    store = _store(runtime)
    try:
        store.append(session, 1, "goal_set_from_chat", {"goal": "Implement the parser"})
        _edit(store, session, 2, "src/skaldir/parser.py", "def parse(x):\n    return x\n")
        _call(store, session, 3, "pytest -q tests/test_parser.py")
        _result_ok(store, session, 4)
    finally:
        store.close()
    return runtime / "events.db"


@pytest.fixture()
def runtime(tmp_path: Path) -> Path:
    root = tmp_path / "runtime"
    root.mkdir(parents=True, exist_ok=True)
    return root


# --------------------------------------------------------------------------- #
# status                                                                        #
# --------------------------------------------------------------------------- #


class TestStatus:
    def test_reports_sessions_leases_and_policy(self, runtime: Path) -> None:
        problem_trajectory(runtime)
        store = PauseStore(runtime / "pauses.db")
        store.create(session_id="problem", kind=KIND_VOICE, base_step=4,
                     trigger_kind="loop", trigger_fp="abc", problem="repeats")
        store.close()
        payload = V.status(runtime=runtime)
        assert payload["runtime_exists"] is True
        assert payload["events"]["exists"] is True
        assert payload["events"]["sessions"] == 1
        assert payload["events"]["list"][0]["session_id"] == "problem"
        assert payload["pauses"]["leases"] == 1
        assert payload["pauses"]["statuses"].get("requested") == 1
        assert payload["pauses"]["sessions"] == {"problem": 1}
        # the policy the supervisor would apply is reported, not invented
        assert payload["watched"]["pause"]["trigger_rules"]["loop"] == 2
        assert payload["watched"]["pause"]["strictness"] == "standard"
        assert V.CAT_LOOPS in payload["watched"]["categories"]
        assert payload["watched"]["pause"]["cooldown_seconds"] > 0

    def test_empty_runtime_is_reported_and_exits_not_found(self, runtime: Path,
                                                          capsys) -> None:
        payload = V.status(runtime=runtime)
        assert payload["events"]["exists"] is False
        assert payload["pauses"]["exists"] is False
        code = voice_cli.main(["status", "--runtime", str(runtime), "--json"])
        envelope = json.loads(capsys.readouterr().out)
        assert code == 0  # the runtime directory exists; there is simply nothing in it
        assert envelope["data"]["events"]["sessions"] == 0
        assert any("no store yet" in item for item in envelope["warnings"])

    def test_missing_runtime_exits_5(self, tmp_path: Path, capsys) -> None:
        missing = tmp_path / "nope"
        code = voice_cli.main(["status", "--runtime", str(missing), "--json"])
        envelope = json.loads(capsys.readouterr().out)
        assert code == 5
        assert envelope["ok"] is False
        assert envelope["data"]["runtime_exists"] is False


# --------------------------------------------------------------------------- #
# check                                                                         #
# --------------------------------------------------------------------------- #


class TestCheck:
    def test_reports_all_six_categories_with_evidence(self, runtime: Path) -> None:
        problem_trajectory(runtime)
        report = V.check(runtime=runtime, session_id="problem")
        assert report["checked"] is True
        assert report["verdict"] == "attention"
        assert set(report["counts"]) == set(V.CATEGORIES)
        for category in V.CATEGORIES:
            assert report["counts"][category] > 0, f"{category} produced no finding"
        kinds = {item["kind"] for item in report["findings"]}
        assert {"loop", "goal_drift", "hallucinated_api", "guard_finding",
                "tool_call_intercepted", "no_verification_after_change"} <= kinds
        for finding in report["findings"]:
            assert finding["evidence"], f"{finding['kind']} carries no evidence"
            assert str(runtime / "events.db") in finding["evidence"][0]
            assert finding["severity_name"] in ("info", "warn", "high")
        assert report["highest_severity_name"] == "high"

    def test_goal_drift_needs_a_goal(self, runtime: Path) -> None:
        problem_trajectory(runtime)
        without = V.check(runtime=runtime, session_id="problem", goal_from_contract=False)
        with_goal = V.check(runtime=runtime, session_id="problem")
        assert without["counts"][V.CAT_DRIFT] == 0
        assert without["goal"] == {"text": "", "source": "disabled"}
        assert with_goal["counts"][V.CAT_DRIFT] > 0
        assert with_goal["goal"]["source"] == "goal_set_from_chat"
        assert with_goal["facts"]["drift"]["unrelated_streak"] >= V.DRIFT_STREAK

    def test_verified_trajectory_is_clean(self, runtime: Path) -> None:
        clean_trajectory(runtime)
        report = V.check(runtime=runtime, session_id="clean")
        assert report["checked"] is True
        assert report["verdict"] == "clean"
        assert report["findings_count"] == 0
        assert report["facts"]["health"]["verified_after_change"] is True

    def test_does_not_write_into_the_store(self, runtime: Path) -> None:
        db = problem_trajectory(runtime)
        before = hashlib.sha256(db.read_bytes()).hexdigest()
        V.check(runtime=runtime, session_id="problem")
        V.triage(runtime=runtime, session_id="problem")
        V.explain(runtime=runtime, session_id="problem")
        after = hashlib.sha256(db.read_bytes()).hexdigest()
        assert before == after

    def test_unknown_session_is_blocked_not_a_success(self, runtime: Path) -> None:
        problem_trajectory(runtime)
        report = V.check(runtime=runtime, session_id="nope")
        assert report["checked"] is False
        assert "no stored events" in report["blocked_reason"]
        assert report["findings"] == []

    def test_limit_keeps_the_counts_honest(self, runtime: Path) -> None:
        problem_trajectory(runtime)
        full = V.check(runtime=runtime, session_id="problem", limit=0)
        capped = V.check(runtime=runtime, session_id="problem", limit=2)
        assert capped["findings_count"] == full["findings_count"]
        assert capped["counts"] == full["counts"]
        assert len(capped["findings"]) == 2
        assert capped["findings_truncated"] is True
        assert "attention" == capped["verdict"] == full["verdict"]

    def test_pact_supplies_scope_drift(self, runtime: Path, tmp_path: Path) -> None:
        db = problem_trajectory(runtime)
        assert db.is_file()
        pact_file = tmp_path / "pact.json"
        pact_file.write_text(json.dumps({
            "goal": "Prepare the quarterly release notes",
            "acceptance": [{"id": "ac1", "text": "the notes exist"}],
            "scope_paths": ["docs/**"],
        }), encoding="utf-8")
        from skaldir.pact import Pact

        pact = Pact.from_dict(json.loads(pact_file.read_text(encoding="utf-8")))
        report = V.check(runtime=runtime, session_id="problem", pact=pact, limit=0)
        scope = [item for item in report["findings"] if item["kind"] == "scope_drift"]
        assert scope, "a declared pact scope must produce scope_drift findings"
        assert report["facts"]["pact"] is True


# --------------------------------------------------------------------------- #
# triage — the owner's rule                                                     #
# --------------------------------------------------------------------------- #


class TestTriage:
    def test_wakes_the_voice_on_an_unhandled_problem(self, runtime: Path) -> None:
        problem_trajectory(runtime)
        payload = V.triage(runtime=runtime, session_id="problem")
        assert payload["checked"] is True
        assert payload["decision"] == "wake_voice"
        assert payload["wake"] is True
        assert payload["reason_code"] == "unhandled_finding"
        assert payload["handled"]["already_working"] is False
        assert payload["findings_count"] > 0

    def test_stays_silent_when_the_agent_is_already_on_it(self, runtime: Path) -> None:
        """The owner's rule: no interference while the agent handles it sensibly."""
        handled_trajectory(runtime)
        report = V.check(runtime=runtime, session_id="handled", limit=0)
        # the trajectory really does contain a problem the supervisor would flag
        assert report["findings_count"] > 0
        assert report["counts"][V.CAT_LOOPS] > 0

        payload = V.triage(runtime=runtime, session_id="handled")
        assert payload["decision"] == "stay_silent"
        assert payload["wake"] is False
        assert payload["intervene"] is False
        assert payload["reason_code"] == "already_recognised_and_working"
        handled = payload["handled"]
        assert handled["already_working"] is True
        assert handled["healthy"] is True
        assert handled["responses"], "the reading must name the response it saw"
        assert handled["markers"]["distinct_errors"] >= 2
        assert handled["markers"]["localized_fix"] is True
        assert handled["markers"]["verified_after_change"] is True
        assert "recoverable debugging" in handled["health_reason"]

    def test_the_handled_reading_is_evidence_backed_not_hopeful(self, runtime: Path) -> None:
        """Same errors, no fix and no verification ⇒ the Voice IS woken."""
        store = _store(runtime)
        try:
            for step in (1, 3, 5):
                _call(store, "stuck", step, "pytest -q tests/test_auth.py")
                _result_error(store, "stuck", step, "AssertionError: expected 3 got 4")
            store.append("stuck", 6, "thought", {"text": "still failing"})
        finally:
            store.close()
        payload = V.triage(runtime=runtime, session_id="stuck")
        assert payload["decision"] == "wake_voice"
        assert payload["handled"]["already_working"] is False

    def test_defers_after_repeated_interventions(self, runtime: Path) -> None:
        """A stuck session that was already interrupted twice is not interrupted again.

        Note the precedence: an *already handled* problem stays silent even then
        (see the test above) — repeated interventions only defer what would
        otherwise be woken.
        """
        problem_trajectory(runtime)
        store = PauseStore(runtime / "pauses.db")
        for step in (4, 8):
            store.create(session_id="problem", kind=KIND_VOICE, base_step=step,
                         trigger_kind="loop", trigger_fp=f"fp{step}", problem="repeat")
        store.close()
        payload = V.triage(runtime=runtime, session_id="problem")
        assert payload["handled"]["already_working"] is False
        assert payload["decision"] == "defer"
        assert payload["reason_code"] == "repeated_interventions"
        assert payload["interruptions"]["count"] == 2
        assert payload["interruptions"]["source"].endswith("pauses.db")

    def test_records_the_jev_advisory_without_obeying_it(self, runtime: Path) -> None:
        problem_trajectory(runtime)
        payload = V.triage(runtime=runtime, session_id="problem")
        advisory = payload["jev"]
        assert advisory is not None
        # the substrate's own pack, reached through the facade
        assert advisory["question_pack_id"] == "voice_triage"
        assert advisory["question_pack_version"] == "v1"
        assert advisory["authority"] == "advisory"
        assert advisory["decision_id"].startswith("jev-")
        # no provider configured ⇒ Jev blocks and answers its neutral reading
        assert advisory["status"] == "blocked"
        assert advisory["refusal_reason"] == "provider_disabled"
        assert advisory["chosen"] == "defer"
        # ... and the deterministic decision is untouched by it
        assert payload["decision"] == "wake_voice"
        assert payload["advisory_applied"] is False

    def test_a_never_called_provider_says_attempted_false(self, runtime: Path) -> None:
        """Audit 63/V2, half one: ``disabled`` may only sit next to "nothing was tried".

        The honest fallback text and the §4.2 ``provider_mode`` stay exactly as they
        were — what is added is the split that stops ``disabled`` from being read as
        "we never called out".
        """
        problem_trajectory(runtime)
        jev = V.triage(runtime=runtime, session_id="problem")["jev"]
        assert jev["status"] == "blocked"
        assert jev["attempted"] is False, "no provider was ever called here"
        assert jev["refused"] is True
        assert jev["mode"] == "disabled"
        assert jev["provider_mode"] == "disabled"  # the contract value is unchanged
        assert jev["refusal_reason"] == "provider_disabled"
        assert jev["fallback"], "the honest fallback text is still named"

    def test_a_failed_402_attempt_is_reported_as_attempted(self, runtime: Path,
                                                           monkeypatch) -> None:
        """Audit 63/V2, half two: a real outbound attempt that fails is NOT "disabled".

        The runtime settings arm ``system_one`` with the LLM adapter (exactly like the
        audited host) and the resolved client answers HTTP 402 Insufficient Balance —
        the measured case. The report must say the call went out and was refused.
        """
        import importlib

        from skaldir.llm import LLMProviderError
        from skaldir.system_one_flags import SystemOneFlags, save_system_one_flags

        class InsufficientBalance:
            """The audited stub: the request leaves, the provider refuses it (402)."""

            def complete(self, *, prompt_id: str, prompt: str, tier: str) -> str:
                raise LLMProviderError(
                    "provider call failed (HTTP 402, message: Insufficient Balance)",
                    status=402,
                )

        save_system_one_flags(
            runtime,
            SystemOneFlags(enabled=True, provider="llm_system_one_adapter",
                           shadow_mode=False, voice_triage=True),
        )
        decide_module = importlib.import_module("skaldir.jev.decide")
        runtime_module = importlib.import_module("skaldir.jev.runtime")
        # pin the client resolution so an ambient SKALDIR_LLM_* cannot decide this test
        monkeypatch.setattr(decide_module, "_ambient_provider_pin", lambda *a, **k: None)
        monkeypatch.setattr(
            runtime_module,
            "provider_resolution",
            lambda **k: {"client": InsufficientBalance(), "problem": "",
                         "source": "test-stub"},
        )

        problem_trajectory(runtime)
        payload = V.triage(runtime=runtime, session_id="problem")
        jev = payload["jev"]
        assert jev["status"] == "blocked"
        assert jev["attempted"] is True, "the outbound attempt really happened"
        assert jev["refused"] is True
        assert "HTTP 402" in jev["refusal_reason"]
        assert "Insufficient Balance" in jev["refusal_reason"]
        assert jev["mode"] == "attempted_failed"
        assert jev["provider_mode"] == "disabled"  # §4.2 labelling itself is untouched
        assert jev["fallback"], "the honest fallback text is still named"
        # the advisory never becomes authority, attempted or not
        assert payload["decision"] == "wake_voice"
        assert payload["advisory_applied"] is False

    def test_advisory_can_be_skipped(self, runtime: Path) -> None:
        problem_trajectory(runtime)
        payload = V.triage(runtime=runtime, session_id="problem", jev=False)
        assert payload["jev"] is None
        assert payload["decision"] == "wake_voice"

    def test_missing_trajectory_is_blocked(self, runtime: Path) -> None:
        payload = V.triage(runtime=runtime, session_id="nobody")
        assert payload["checked"] is False
        assert payload["decision"] == "stay_silent"
        assert payload["reason_code"] == "nothing_to_check"


# --------------------------------------------------------------------------- #
# explain                                                                       #
# --------------------------------------------------------------------------- #


class TestExplain:
    def test_states_that_the_agent_already_sees_the_problem(self, runtime: Path) -> None:
        handled_trajectory(runtime)
        payload = V.explain(runtime=runtime, session_id="handled")
        assert payload["checked"] is True
        assert payload["would_intervene"] is False
        assert payload["reason_code"] == "already_recognised_and_working"
        assert "already sees the problem" in payload["statement"]
        codes = {item["code"] for item in payload["reasons"]}
        assert "agent_already_on_it" in codes
        assert "health_evidence" in codes

    def test_would_intervene_and_names_the_chain(self, runtime: Path) -> None:
        problem_trajectory(runtime)
        payload = V.explain(runtime=runtime, session_id="problem")
        assert payload["would_intervene"] is True
        assert payload["decision"] == "wake_voice"
        assert "would intervene" in payload["statement"]
        triggers = {item["kind"]: item for item in payload["triggers"]}
        assert triggers, "the chain must name the triggers it considered"
        loop = triggers["loop"]
        assert loop["has_pause_trigger"] is True
        # the threshold is the live policy's own, shifted by the session's strictness
        assert loop["threshold"] == V.P.TRIGGER_RULES["loop"]
        assert loop["cost_gate"]["measured"]["occurrences"] == loop["occurrences"]
        assert loop["cost_gate"]["estimated"]["continuation_tokens"] > 0
        # a trigger the live policy carries no threshold for is reported as such
        assert any(item["has_pause_trigger"] is False for item in payload["triggers"])

    def test_gate_reports_the_live_policy(self, runtime: Path) -> None:
        problem_trajectory(runtime)
        payload = V.explain(runtime=runtime, session_id="problem")
        gate = payload["gate"]
        assert gate["strictness"] == "standard"
        assert gate["trigger_rules"] == dict(V.P.TRIGGER_RULES)
        assert gate["max_per_hour"] >= 1
        assert gate["cooldown_seconds"] > 0

    def test_missing_trajectory_is_blocked(self, runtime: Path) -> None:
        payload = V.explain(runtime=runtime, session_id="nobody")
        assert payload["checked"] is False
        assert payload["would_intervene"] is False
        assert payload["reasons"][0]["code"] == "nothing_to_check"


# --------------------------------------------------------------------------- #
# the CLI                                                                       #
# --------------------------------------------------------------------------- #


class TestCli:
    def test_register_cli_adds_the_four_verbs(self) -> None:
        import argparse

        parser = argparse.ArgumentParser(prog="skaldir")
        sub = parser.add_subparsers(dest="command", required=True)
        returned = voice_cli.register_cli(sub)
        assert returned is not None
        voice = next(
            action for action in parser._actions
            if isinstance(action, argparse._SubParsersAction)
        )
        assert "voice" in voice.choices
        group = voice.choices["voice"]
        verbs = next(
            action for action in group._actions
            if isinstance(action, argparse._SubParsersAction)
        )
        assert set(verbs.choices) == {"status", "check", "triage", "explain"}
        for name in ("status", "check", "triage", "explain"):
            assert callable(verbs.choices[name].get_default("func"))

    def test_check_json_envelope_carries_data_and_evidence(self, runtime: Path,
                                                          capsys) -> None:
        problem_trajectory(runtime)
        code = voice_cli.main(["check", "--runtime", str(runtime),
                               "--session", "problem", "--json"])
        out = capsys.readouterr().out
        envelope = json.loads(out)
        assert envelope["command"] == "voice.check"
        assert code == 2  # a high-severity finding is a failed supervision check
        assert envelope["exit_code"] == 2
        assert envelope["data"]["session_id"] == "problem"
        assert envelope["data"]["findings"], "the envelope must carry the findings"
        assert envelope["evidence"], "no success without evidence"
        assert any(item["kind"] == "file" for item in envelope["evidence"])

    def test_triage_exit_code_follows_the_decision(self, runtime: Path, capsys) -> None:
        problem_trajectory(runtime)
        handled_trajectory(runtime)
        assert voice_cli.main(["triage", "--runtime", str(runtime),
                               "--session", "problem", "--json"]) == 2
        wake = json.loads(capsys.readouterr().out)
        assert wake["data"]["decision"] == "wake_voice"

        assert voice_cli.main(["triage", "--runtime", str(runtime),
                               "--session", "handled", "--json"]) == 0
        quiet = json.loads(capsys.readouterr().out)
        assert quiet["ok"] is True
        assert quiet["data"]["decision"] == "stay_silent"
        assert quiet["data"]["reason_code"] == "already_recognised_and_working"

    def test_blocked_run_exits_3_with_the_reason(self, runtime: Path, capsys) -> None:
        problem_trajectory(runtime)
        assert voice_cli.main(["explain", "--runtime", str(runtime),
                               "--session", "ghost", "--json"]) == 3
        envelope = json.loads(capsys.readouterr().out)
        assert envelope["ok"] is False
        assert envelope["data"]["checked"] is False
        assert "no stored events" in envelope["error"]["message"]

        assert voice_cli.main(["check", "--runtime", str(runtime), "--json"]) == 2
        capsys.readouterr()  # the newest session really exists here

    def test_empty_runtime_check_is_blocked(self, runtime: Path, capsys) -> None:
        assert voice_cli.main(["check", "--runtime", str(runtime), "--json"]) == 3
        envelope = json.loads(capsys.readouterr().out)
        assert envelope["data"]["checked"] is False
        assert "no session to check" in envelope["error"]["message"]

    def test_bad_pact_is_a_usage_error(self, runtime: Path, tmp_path: Path, capsys) -> None:
        problem_trajectory(runtime)
        bad = tmp_path / "pact.json"
        bad.write_text("{not json", encoding="utf-8")
        assert voice_cli.main(["check", "--runtime", str(runtime), "--session", "problem",
                               "--pact", str(bad), "--json"]) == 1
        envelope = json.loads(capsys.readouterr().out)
        assert envelope["ok"] is False

    def test_text_mode_prints_the_human_view(self, runtime: Path, capsys) -> None:
        problem_trajectory(runtime)
        voice_cli.main(["status", "--runtime", str(runtime), "--text"])
        text = capsys.readouterr().out
        assert "runtime:" in text and "events:" in text and "pauses:" in text

        voice_cli.main(["triage", "--runtime", str(runtime), "--session", "problem",
                        "--text"])
        text = capsys.readouterr().out
        assert "decision: wake_voice" in text
        assert "jev:" in text

        voice_cli.main(["explain", "--runtime", str(runtime), "--session", "problem",
                        "--text"])
        text = capsys.readouterr().out
        assert "would_intervene: True" in text
        assert "loop" in text

    def test_parser_is_standalone(self) -> None:
        parser = voice_cli.build_parser()
        args = parser.parse_args(["triage", "--session", "s", "--step", "5"])
        assert args.session == "s" and args.step == 5
        assert args.command_path == "voice.triage"
