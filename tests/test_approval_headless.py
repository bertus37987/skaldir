"""Headless honesty: with no interactive user a prompt returns blocked — never a hang, never
an auto-allow.

The failure this file exists to prevent is the one that looks like nothing: a ``--json`` run (or
a piped one) where a prompt-required command is silently treated as approved, or where the
process waits for a user who is not there. Both are measured here, including the wall clock.

Also pinned: what the caller is told. A block carries the exact rule that decided it and the
commands the user would have to run — a refusal the operator can act on, not a dead end.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from skaldir import approval as A
from skaldir import permission_profile as P


@pytest.fixture
def runtime(tmp_path: Path) -> Path:
    home = tmp_path / "rt"
    home.mkdir()
    return home


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    root.mkdir()
    return root


def _engine(
    runtime: Path | None,
    workspace: Path,
    *,
    interactive: bool,
    policy: str = "on_request",
):
    store = A.RuleStore(runtime=runtime, persist=False)
    ledger = A.GrantLedger(
        runtime=runtime, session="headless", banned=store.banned, persist=False
    )
    return A.ApprovalEngine(
        P.default_profile(workspace),
        runtime=runtime,
        session="headless",
        store=store,
        grants=ledger,
        policy=policy,
        interactive=interactive,
    )


def test_a_headless_prompt_returns_blocked_with_exit_3(runtime: Path, workspace: Path) -> None:
    verdict = _engine(runtime, workspace, interactive=False).decide("pytest -q")
    assert verdict.decision == A.Decision.PROMPT.value
    assert verdict.blocked is True
    assert verdict.exit_code == 3
    assert verdict.allowed is False


def test_a_headless_prompt_does_not_block_forever(runtime: Path, workspace: Path) -> None:
    engine = _engine(runtime, workspace, interactive=False)
    started = time.monotonic()
    engine.decide("pytest -q")
    engine.decide("npm ci")
    engine.decide("python3 -c 'x'")
    elapsed = time.monotonic() - started
    assert elapsed < 5, f"a headless judgement took {elapsed:.1f}s — something waited for a user"


def test_a_headless_prompt_never_auto_allows(runtime: Path, workspace: Path) -> None:
    """The dangerous outcome: a command runs because nobody said no."""
    engine = _engine(runtime, workspace, interactive=False)
    for command in ("pytest -q", "npm ci", "make deploy", "git status > out.txt", "python3 -c 'x'"):
        verdict = engine.decide(command)
        assert verdict.allowed is False, f"{command!r} was allowed without a user"


def test_the_block_names_the_rule_that_decided_it(runtime: Path, workspace: Path) -> None:
    engine = _engine(runtime, workspace, interactive=False)
    engine.store.add_rule(("npm",), "prompt", note="ask")
    verdict = engine.decide("npm ci")
    assert verdict.rule_id and verdict.rule_text == 'prompt "npm"'
    assert verdict.reason_kind == "rule"


def test_the_block_says_what_the_user_would_need_to_allow(runtime: Path, workspace: Path) -> None:
    verdict = _engine(runtime, workspace, interactive=False).decide("pytest -q")
    text = "\n".join(verdict.what_would_allow())
    assert "approval allow --scope turn" in text, "the suggestion must be copy-pasteable"
    assert "approval allow --scope session" in text
    assert "approval allow --rule" in text
    assert "approval deny" in text
    assert verdict.to_dict()["would_allow"] == verdict.what_would_allow()


def test_a_forbidden_command_offers_no_grant_route(runtime: Path, workspace: Path) -> None:
    verdict = _engine(runtime, workspace, interactive=False).decide("python3 -c 'print(1)'")
    text = "\n".join(verdict.what_would_allow())
    assert "refused" in text, "the banned list must say it is refused, not offer a scope"
    assert "--scope session" not in text


def test_the_interactive_flag_is_visible_in_the_verdict(runtime: Path, workspace: Path) -> None:
    headless = _engine(runtime, workspace, interactive=False).decide("pytest -q")
    asked = _engine(runtime, workspace, interactive=True).decide("pytest -q")
    assert headless.interactive is False and asked.interactive is True
    assert headless.blocked is True and asked.blocked is True, (
        "an interactive run still owes a decision; it is the caller's job to show the prompt"
    )
    assert headless.exit_code == asked.exit_code == 3


def test_decide_with_an_override_does_not_mutate_the_engine(runtime: Path, workspace: Path) -> None:
    engine = _engine(runtime, workspace, interactive=False)
    engine.decide("pytest -q", interactive=True)
    assert engine.interactive is False, "the override must not leak into the next judgement"
    assert engine.decide("pytest -q").interactive is False


def test_policy_never_blocks_a_prompt_for_good(runtime: Path, workspace: Path) -> None:
    verdict = _engine(runtime, workspace, interactive=False, policy="never").decide("pytest -q")
    assert verdict.decision == A.Decision.FORBID.value
    assert verdict.blocked is True
    assert verdict.exit_code == 3
    assert "approval policy is 'never'" in verdict.reason


def test_an_allowed_command_is_not_blocked_in_headless_mode(runtime: Path, workspace: Path) -> None:
    engine = _engine(runtime, workspace, interactive=False)
    engine.store.add_rule(("pytest",), "allow")
    verdict = engine.decide("pytest -q")
    assert verdict.decision == A.Decision.ALLOW.value
    assert verdict.blocked is False
    assert verdict.exit_code == 0
    assert verdict.what_would_allow() == []


def test_the_reason_is_readable_by_a_human(runtime: Path, workspace: Path) -> None:
    engine = _engine(runtime, workspace, interactive=False)
    for command, needle in (
        ("pytest -q", "silence is not consent"),
        ("git status > f", "cannot be decomposed safely"),
        ("python3 -c 'x'", "banned-prefix list"),
        ("rm -rf build", "destructive filesystem tool"),
    ):
        verdict = engine.decide(command)
        assert needle in verdict.reason, f"{command!r}: {verdict.reason}"
        assert verdict.reason != verdict.rule_text or verdict.rule_id, (
            "a reason must say something a reader can act on"
        )
