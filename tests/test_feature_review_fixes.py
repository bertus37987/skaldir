"""Regression tests for the 20 fixes applied during the M13 comprehensive feature review."""

from __future__ import annotations

import io
from pathlib import Path

import pytest


def test_act_service_empty_stdout_end_state_does_not_raise_unbound_local(
    tmp_path: Path,
) -> None:
    """Fix #3: _execute_action_list initializes observed_text = '' on empty stdout."""
    from skaldir import act_service

    outcome = act_service.run_action_list(
        [
            {
                "channel": "cli",
                "kind": "cli",
                "argv": ["true"],
                "verify": {"expect_ok": True},
            }
        ],
        workdir=tmp_path,
        end_state={
            "channel": "stdout",
            "expect_observe_contains": "expected-output",
        },
        runtime=tmp_path,
    )
    assert outcome["status"] == "verification_failed"
    assert outcome["verified"] is False
    assert outcome["observation"]["text"] == ""


def test_act_service_lease_gate_refusal_includes_contract_fields(tmp_path: Path) -> None:
    """Fix #4: DESK-01 refusal includes status, verified, reason, runner, exit_code."""
    from skaldir import act_service

    class _RefusingLeaseStore:
        def acquire(self, _session_id: str, *, task: str = "") -> None:
            raise RuntimeError(f"lease held by another run ({task})")

    outcome = act_service.run_action_list(
        [
            {
                "channel": "cli",
                "kind": "cli",
                "argv": ["true"],
                "verify": {"expect_ok": True},
            }
        ],
        workdir=tmp_path,
        runtime=tmp_path,
        desktop_lease=_RefusingLeaseStore(),
    )
    assert outcome["ok"] is False
    assert outcome["status"] == "blocked"
    assert outcome["verified"] is False
    assert "DESK-01" in outcome["reason"]
    assert "runner" in outcome
    assert outcome["exit_code"] == act_service.EXIT_BLOCKED


def test_input_backend_portal_path_state_checks_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fix #7: portal_path_state checks .exists() on candidate portal libraries."""
    from skaldir import input_backend

    empty_dir = tmp_path / "empty_lib"
    empty_dir.mkdir()
    monkeypatch.setattr(input_backend, "_library_folders", lambda: [str(empty_dir)])

    state, reason = input_backend.portal_path_state(run_cmd=lambda *_a, **_kw: (0, "true"))
    assert state == input_backend.PORTAL_UNAVAILABLE
    assert "missing" in reason

    for lib_name in input_backend.PORTAL_LIBRARIES:
        (empty_dir / lib_name).write_text("", encoding="utf-8")
    state2, _reason2 = input_backend.portal_path_state(run_cmd=lambda *_a, **_kw: (0, "(true,)"))
    assert state2 == input_backend.PORTAL_NEEDS_CONSENT


def test_verify_cli_print_text_handles_flow_payload(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Fix #10: verify_cli._print_text handles flow/flows payloads without KeyError."""
    from skaldir import verify_cli

    payload = {
        "verdict": "PASS",
        "results": [
            {
                "flow_id": "cli_roundtrip",
                "status": "PASS",
                "reason": "ok",
                "duration_ms": 12,
                "steps": [
                    {"step": "echo", "status": "PASS", "detail": "hello"},
                ],
            }
        ],
    }
    verify_cli._print_text("verify flows", payload)
    captured = capsys.readouterr().out
    assert "cli_roundtrip" in captured
    assert "PASS" in captured


def test_knowledge_store_upsert_removes_stale_fts_tokens(tmp_path: Path) -> None:
    """Fix #20a: KnowledgeStore.upsert deletes old FTS5 rows before INSERT OR REPLACE."""
    from skaldir.knowledge import KnowledgeEntry, KnowledgeStore

    store = KnowledgeStore(tmp_path / "knowledge")
    store.upsert(
        KnowledgeEntry(
            id="test-fact",
            title="Original Title",
            body="xylophone_unique_old_token",
            kind="fact",
        )
    )
    assert len(store.search("xylophone_unique_old_token")["matches"]) == 1

    store.upsert(
        KnowledgeEntry(
            id="test-fact",
            title="Updated Title",
            body="marimba_unique_new_token",
            kind="fact",
        )
    )
    assert len(store.search("marimba_unique_new_token")["matches"]) == 1
    assert store.search("xylophone_unique_old_token")["matches"] == []


def test_context_manager_verify_preserved_handles_corrections_and_cancel_state() -> None:
    """Fix #20b: verify_preserved recognizes corrections, unknown_effect, cancel/approval state."""
    from skaldir.context_manager import preserved_items, verify_preserved

    preserved = preserved_items(
        [],
        goal="ship feature",
        user_corrections=["use uv instead of pip"],
        unknown_effects=["network timeout after send"],
        cancel_state="cancelled:user-esc",
        approval_state="pending:apr-1",
    )
    checks = verify_preserved(
        preserved,
        [],
        goal="ship feature",
        user_corrections=["use uv instead of pip"],
        unknown_effects=["network timeout after send"],
        cancel_state="cancelled:user-esc",
        approval_state="pending:apr-1",
    )
    by_label = {c.label: c for c in checks}
    assert by_label["user_correction:0"].found is True
    assert by_label["unknown_effect:0"].found is True
    assert by_label["cancel_state"].found is True
    assert by_label["approval_state"].found is True


def test_system_one_policy_propose_route_only_flags_low_progress() -> None:
    """Fix #19a: propose_route only triggers RULE_LOOP_WITHOUT_PROGRESS on low progress."""
    from skaldir.system_one import ACCESS_FIXTURE, NoulAnswer, SystemOneEvaluation
    from skaldir.system_one_policy import (
        ROUTE_OBSERVE,
        ROUTE_PAUSE_AND_INVESTIGATE,
        propose_route,
    )

    def _make_eval(progress_val: float) -> SystemOneEvaluation:
        return SystemOneEvaluation(
            evaluation_id="soe-test-1",
            run_id="run-1",
            goal_id="goal-1",
            goal_revision=1,
            policy_revision=1,
            event_cursor=1,
            provider="fixture",
            provider_model="fixture",
            provider_access_mode=ACCESS_FIXTURE,
            question_pack_id="triage",
            question_pack_version="1",
            state_fingerprint="fp",
            input_scope="task",
            started_at=1.0,
            completed_at=2.0,
            latency_ms=10,
            answers={
                "voice.loop.same_strategy": NoulAnswer("voice.loop.same_strategy", 0.9),
                "voice.loop.new_information": NoulAnswer("voice.loop.new_information", 0.1),
                "voice.loop.measurable_progress": NoulAnswer(
                    "voice.loop.measurable_progress", progress_val
                ),
            },
        )

    assert propose_route(_make_eval(0.85)).route == ROUTE_OBSERVE
    assert propose_route(_make_eval(0.2)).route == ROUTE_PAUSE_AND_INVESTIGATE


def test_tui_app_draw_updates_duplicate_rows_correctly() -> None:
    """Fix #13: TuiApp.draw uses enumerate(frame, start=1) instead of frame.index(new) + 1."""
    from skaldir.tui_app import TuiApp, TuiAppConfig

    buf = io.StringIO()
    app = TuiApp(config=TuiAppConfig(width=80, height=5), stdout=buf)

    app.draw()
    app._last_frame = ["dup-line", "row-2", "row-3", "row-4", "old-row-5"]
    app.tui.render = lambda: ["dup-line", "row-2", "row-3", "row-4", "dup-line"]  # type: ignore[method-assign]
    buf.seek(0)
    buf.truncate(0)
    app.draw()
    out = buf.getvalue()
    assert "\x1b[5;1H" in out
    assert "\x1b[1;1H" not in out


def test_approval_engine_approve_grants_all_compound_segments(tmp_path: Path) -> None:
    """Fix #20c: ApprovalEngine.approve grants rules for all compound command segments."""
    from skaldir.approval import ApprovalEngine, Decision, GrantScope

    engine = ApprovalEngine(runtime=tmp_path, session="sess-1")
    cmd = "git status && cargo build --release"
    verdict1 = engine.judge(cmd)
    assert verdict1.decision == Decision.PROMPT.value

    engine.approve(cmd, scope=GrantScope.SESSION.value, actor="test")
    verdict2 = engine.judge(cmd)
    assert verdict2.decision == Decision.ALLOW.value
