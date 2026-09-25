"""``selftest()`` — the run-level authority surface (MVP/03 §6, last bullet).

``skaldir jev selftest --json`` prints, per rule, the enforcement points and
the test ids that pin them, and exits ``1`` if any rule lacks a test. This module
is what makes that a measurement instead of a promise:

* every kind is actually pushed through :func:`skaldir.jev.decide` with the offline
  fixture evaluator, and its status + answer model are reported as measured;
* every kind is *also* pushed through the blocked path (disabled provider), so
  the honest "no advisory" outcome is exercised for each kind;
* :func:`skaldir.jev.rules.missing_rule_tests` reads the test file and reports the
  rule whose tests are absent.

The canonical per-kind requests live here (``SELFTEST_CASES``) so the CLI, the
self-audit and the tests all exercise the same minimal, declarative state — no
test invents its own version of "a valid request".
"""

from __future__ import annotations

from typing import Any

from ..system_one import PROVIDER_DISABLED
from ..system_one_flags import SystemOneFlags
from . import contracts as C
from .decide import decide
from .fixture import FixtureJevEvaluator
from .kinds import DECISION_KINDS, FLAG_FOR_KIND, pack_ref_for
from .packs import JEV_PACKS, default_jev_registry, pack_by_ref
from .rules import RULES, rule_report

#: One minimal, declarative state per kind (a *valid* request payload).
SELFTEST_CASES: dict[str, dict[str, Any]] = {
    "memory_injection": {
        "run_id": "run-selftest",
        "goal_id": "goal-selftest",
        "goal_text": "make the desktop app build again",
        "next_step_intent": "rebuild the app from the current sources",
        "context_digest": "window: pact + last 3 steps, ~800 tokens",
        "candidates": [
            {
                "memory_id": "incident#812",
                "source": "incident",
                "title": "app build fails when app-staging is missing",
                "digest": "the build step needs app-staging; without it the task fails",
                "age_days": 4,
                "retrieval_score": 0.81,
            },
            {
                "memory_id": "note#44",
                "source": "note",
                "title": "old provider timeout workaround",
                "digest": "a workaround for a provider timeout that no longer exists",
                "age_days": 400,
                "retrieval_score": 0.62,
            },
        ],
        "budget": {"max_inject": 2, "max_tokens": 800},
    },
    "tool_routing": {
        "run_id": "run-selftest",
        "goal_id": "goal-selftest",
        "next_step_intent": "open the settings page of the app",
        "candidates": [
            {
                "candidate_id": "browser.open_url",
                "kind": "gui_subtool",
                "name": "browser.open_url",
                "version": "1.0.0",
                "source": "subtool_library",
                "input_types": ["url"],
                "required_params": ["url"],
                "preconditions_satisfied": True,
                "health": "ok",
                "permission_state": "allowed",
                "risk_class": "R1",
                "anchor_available": True,
            }
        ],
        "constraints": {"max_risk_class": "R2"},
        "recent_failures": [],
        "environment": {"has_display": True, "has_docker": False},
    },
    "capability_ranking": {
        "run_id": "run-selftest",
        "goal_id": "goal-selftest",
        "need": {
            "intent": "fetch a URL and return its visible text",
            "required_input_types": ["url"],
            "expected_effect": "the page text is available to the next step",
            "tags": ["http", "fetch", "text"],
        },
        "candidates": [
            {
                "candidate_id": "cap.http_fetch",
                "name": "http_fetch",
                "summary": "fetch a URL and return visible text",
                "tags": ["http"],
                "input_types": ["url"],
                "output_types": ["text"],
                "health": "ok",
                "version": "1.0.0",
                "authority": "trusted",
                "last_used_days": 3,
                "scope_match": True,
                "permission_state": "allowed",
            }
        ],
        "top_k": 5,
        "min_fit": 0.5,
    },
    "context_compaction": {
        "run_id": "run-selftest",
        "goal_id": "goal-selftest",
        "state": {
            "context": "a long transcript, summarized for the decision state",
            "goal": "fix the failing build without touching src/a.ts",
            "history": [
                {"i": 0, "role": "user", "text": "never edit src/a.ts"},
                {"i": 1, "role": "assistant", "text": "", "tool_calls": [
                    {"id": "t1", "tool": "Read", "input": "src/a.ts", "result_chars": 4200}
                ]},
                {"i": 2, "role": "assistant", "text": "", "tool_calls": [
                    {"id": "t2", "tool": "Bash", "input": "npm test", "result_chars": 12400}
                ]},
                {"i": 3, "role": "assistant", "text": "rerunning the build"},
                {"i": 4, "role": "assistant", "text": "checking the lockfile"},
                {"i": 5, "role": "assistant", "text": "the lockfile is stale"},
                {"i": 6, "role": "assistant", "text": "regenerating it"},
                {"i": 7, "role": "assistant", "text": "rebuilding"},
                {"i": 8, "role": "assistant", "text": "build is green"},
            ],
        },
        "options": {},
        "protected_message_ids": [0],
        "messages_digest": "sha256:selftest",
    },
    "voice_triage": {
        "run_id": "run-selftest",
        "goal_id": "goal-selftest",
        "trigger_kind": "loop",
        "signals": {"loop": True, "failing_step": "npm test", "attempts": 3},
        "step": 3,
        "trajectory_window": [
            {
                "action": "npm test",
                "kind": "shell",
                "page_changed": False,
                "error_class": "nonzero_exit",
                "hash": "h-selftest",
            }
        ],
        "deterministic_hint": "pause_and_investigate",
        "agent_on_sensible_path": False,
        "interruptions_so_far": 0,
        "evidence_refs": ["ev-selftest"],
    },
    "adversarial_test_planning": {
        "run_id": "run-selftest",
        "goal_id": "goal-selftest",
        "target": {
            "name": "skaldir",
            "build_id": "build-selftest",
            "app_version": "1.5.0",
            "routes": ["/settings"],
            "env": "local",
        },
        "surfaces": [
            {
                "surface_id": "ui_flow:settings-save",
                "name": "settings save",
                "kind": "ui_flow",
                "last_covered_at": 1758000000.0,
                "risk": "R2",
                "entry_hint": "save-button",
                "seed_operations": ["click", "type_text"],
            }
        ],
        "budget": {
            "max_cases": 8,
            "max_wall_s": 600,
            "per_case_timeout_s": 60,
            "allow_network": False,
            "parallel_slots": 2,
        },
        "previous_results": [],
        "known_defects": [],
        "risk_class_max": "R2",
    },
    "semantic_verification": {
        "run_id": "run-selftest",
        "goal_id": "goal-selftest",
        "objective": "make the desktop app build again",
        "objective_digest": "goal:selftest:v1",
        "claim": "the build is fixed: the app builds from the current sources",
        "criteria_already_met": ["the project's own suite passes"],
        "files": [
            {
                "path": "src/app/main.rs",
                "diffstat": "+12 -3",
                "digest": "sha256:2f5c1a0b9e4d7c31",
            }
        ],
        "commands": [
            {"cmd": "cargo build -p app", "exit_code": 0, "duration_ms": 41200}
        ],
        "evidence_refs": ["verify:vr-selftest", "sha256:2f5c1a0b9e4d7c31"],
        "transcript_tail": ["worker: the build now succeeds", "worker: exit 0"],
        "brief_truncated": False,
        "baseline_revision": "a8fcaa1",
        "budget": {"max_tokens": 16000, "max_turns": 4, "max_reads": 12},
    },
}


def case_request(kind: str) -> C.JevRequestBase:
    """The canonical request of one kind, as a *typed* request object."""
    return C.request_for(kind, SELFTEST_CASES[kind])


def selftest(*, evaluator: Any | None = None) -> dict:
    """Measure the façade: every kind reachable, every rule pinned by a test."""
    fixture = evaluator if evaluator is not None else FixtureJevEvaluator()
    # The blocked path: master flag on, provider disabled — the one construction
    # path the whole runtime uses (the flags decide the evaluator).
    blocked_flags = SystemOneFlags(enabled=True, provider=PROVIDER_DISABLED)

    kinds: list[dict] = []
    for kind in DECISION_KINDS:
        request = case_request(kind)
        entry: dict[str, Any] = {
            "kind": kind,
            "pack_ref": pack_ref_for(kind),
            "flag": FLAG_FOR_KIND.get(kind, ""),
            "reachable": False,
            "status": "",
            "provider_mode": "",
            "answer_model": "",
            "reason": "",
        }
        blocked = decide(request, flags=blocked_flags)
        entry["blocked_path"] = {
            "status": blocked.status,
            "provider_mode": blocked.provider_mode,
            "refusal_reason": blocked.refusal_reason,
            "fallback": blocked.fallback,
        }
        try:
            decision = decide(request, evaluator=fixture)
        except Exception as exc:  # noqa: BLE001 — the report is the measurement
            entry["reason"] = f"{type(exc).__name__}: {exc}"[:200]
            kinds.append(entry)
            continue
        entry["status"] = decision.status
        entry["provider_mode"] = decision.provider_mode
        entry["answer_model"] = type(decision.answer).__name__
        entry["reachable"] = (
            decision.status == "ok" and type(decision.answer) is C.JEV_ANSWERS[kind]
        )
        if not entry["reachable"]:
            entry["reason"] = f"status={decision.status!r}, answer={entry['answer_model']!r}"
        kinds.append(entry)

    rules = rule_report()
    untested = [entry["rule"] for entry in rules if not entry["test_present"]]
    blocked_ok = all(entry["blocked_path"]["status"] == "blocked" for entry in kinds)
    reachable_ok = all(entry["reachable"] for entry in kinds)
    registry = default_jev_registry()
    report = {
        "ok": bool(reachable_ok and blocked_ok and not untested),
        "kinds": kinds,
        "reachable": reachable_ok,
        "blocked_path_ok": blocked_ok,
        "rules": rules,
        "untested_rules": untested,
        "contract_scan": {
            "models": list(C.CONTRACT_SCAN["models"]),
            "model_count": C.CONTRACT_SCAN["model_count"],
            "field_count": C.CONTRACT_SCAN["field_count"],
            "forbidden_exact": list(C.CONTRACT_SCAN["forbidden_exact"]),
            "forbidden_substrings": list(C.CONTRACT_SCAN["forbidden_substrings"]),
            "input_only_fields": list(C.CONTRACT_SCAN["input_only_fields"]),
            "constant_fields": C.CONTRACT_SCAN["constant_fields"],
        },
        "packs": [
            {
                "ref": pack.ref,
                "owner": pack.owner,
                "risk_class": pack.risk_class,
                "applicable_states": list(pack.applicable_states),
                "questions": len(pack.questions),
            }
            for pack in (*JEV_PACKS, pack_by_ref("voice_triage_v1"))
        ],
        "registry_refs": list(registry.refs()),
        "rule_count": len(RULES),
        "exit_code": 0 if (reachable_ok and blocked_ok and not untested) else 1,
    }
    return report


def kinds_report() -> list[dict]:
    """The seven kinds with their pack ref, risk class and gating flag."""
    rows = []
    for kind in DECISION_KINDS:
        ref = pack_ref_for(kind)
        try:
            pack = pack_by_ref(ref)
            owner, risk, states = pack.owner, pack.risk_class, list(pack.applicable_states)
        except Exception:  # noqa: BLE001 — a missing pack must be visible, not fatal
            owner, risk, states = "", "", []
        rows.append(
            {
                "kind": kind,
                "pack_ref": ref,
                "owner": owner,
                "risk_class": risk,
                "applicable_states": states,
                "flag": FLAG_FOR_KIND.get(kind, ""),
                "answer_model": C.JEV_ANSWERS[kind].__name__,
                "request_model": C.JEV_REQUESTS[kind].__name__,
            }
        )
    return rows


def packs_report() -> list[dict]:
    """Every pack the Jev registry view serves, in registry order."""
    registry = default_jev_registry()
    return [
        {
            "ref": pack.ref,
            "id": pack.id,
            "version": pack.version,
            "owner": pack.owner,
            "risk_class": pack.risk_class,
            "applicable_states": list(pack.applicable_states),
            "purpose": pack.purpose,
            "fallback_policy": pack.fallback_policy,
            "calibration_dataset_version": pack.calibration_dataset_version,
            "questions": [question.id for question in pack.questions],
        }
        for pack in registry.packs()
    ]


__all__ = ["SELFTEST_CASES", "case_request", "kinds_report", "packs_report", "selftest"]
