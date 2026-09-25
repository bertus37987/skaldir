"""The approval ladder: the rule language, the decomposition rule, and the precedence.

The four rungs this file pins, in the order the ladder evaluates them:

1. ``forbid`` beats everything, including a session grant;
2. a **banned** prefix can never be allowed — no rule and no grant reaches it;
3. the profile's declared command set is a *ceiling* (outside it: forbid) and never an
   authorisation of its own;
4. a command that cannot be decomposed safely **must prompt**, whatever the rules say, and a
   command with several segments is allowed only when *every* segment is covered.

The matrix of 27 end-to-end cases lives in ``test_permission_policy_matrix.py``; this file
tests the pieces the matrix is built from.
"""

from __future__ import annotations

import pytest

from skaldir import approval as A
from skaldir import permission_profile as P


@pytest.fixture
def profile(tmp_path) -> P.PermissionProfile:
    root = tmp_path / "ws"
    root.mkdir()
    return P.default_profile(root)


@pytest.fixture
def store() -> A.RuleStore:
    return A.RuleStore(persist=False)


@pytest.fixture
def engine(profile, store) -> A.ApprovalEngine:
    ledger = A.GrantLedger(banned=store.banned, persist=False)
    return A.ApprovalEngine(profile, store=store, grants=ledger, interactive=False, session="t")


# ------------------------------------------------------------- decomposition


def test_a_plain_command_is_rule_eligible() -> None:
    parts = A.decompose("git status --short")
    assert parts.segments == (("git", "status", "--short"),)
    assert parts.rule_eligible is True
    assert parts.reasons == ()


def test_control_operators_split_the_command_into_segments() -> None:
    parts = A.decompose("git log && git diff | head")
    assert parts.segments == (("git", "log"), ("git", "diff"), ("head",))
    assert parts.control_operators == ("&&", "|")
    assert parts.rule_eligible is True


@pytest.mark.parametrize(
    ("command", "needle"),
    [
        ("git status > out.txt", "redirection"),
        ("git status >> out.txt", "redirection"),
        ("cat < in.txt", "redirection"),
        ("cat <<EOF", "redirection"),
        ("ls *.py", "wildcard"),
        ("ls ?.py", "wildcard"),
        ("echo $(whoami)", "substitution"),
        ("echo `id`", "substitution"),
        ("FOO=bar ls", "environment prefix"),
    ],
)
def test_a_command_with_shell_magic_is_not_rule_eligible(command: str, needle: str) -> None:
    parts = A.decompose(command)
    assert parts.rule_eligible is False, f"{command!r} must not be rule-eligible"
    assert any(needle in reason for reason in parts.reasons), parts.reasons


def test_an_unbalanced_quote_cannot_be_tokenized() -> None:
    parts = A.decompose("echo 'unclosed")
    assert parts.rule_eligible is False
    assert parts.segments == ()
    assert "cannot tokenize" in parts.reasons[0]


def test_an_empty_command_has_nothing_to_judge() -> None:
    parts = A.decompose("   ")
    assert parts.rule_eligible is False
    assert parts.segments == ()


def test_decomposition_is_reported_as_data() -> None:
    payload = A.decompose("git log && git diff").to_dict()
    assert payload["segments"] == [["git", "log"], ["git", "diff"]]
    assert payload["rule_eligible"] is True


# ------------------------------------------------------------------- rules


def test_a_rule_matches_a_prefix_of_a_segment() -> None:
    rule = A.PrefixRule(prefix=("git", "status"), decision="allow")
    assert rule.matches(("git", "status", "--short"))
    assert not rule.matches(("git", "log"))
    assert not rule.matches(("git",))


def test_a_prefix_token_may_carry_alternatives() -> None:
    rule = A.PrefixRule(prefix=("git", "add|stage"), decision="allow")
    assert rule.matches(("git", "add", ".")) and rule.matches(("git", "stage", "."))
    assert not rule.matches(("git", "commit"))


def test_a_rule_needs_a_known_verdict() -> None:
    with pytest.raises(A.PolicyError):
        A.PrefixRule(prefix=("git",), decision="maybe")


def test_evaluate_prefers_forbid_over_allow_regardless_of_order(store: A.RuleStore) -> None:
    store.add_rule(("git", "status"), "allow")
    store.add_rule(("git", "status"), "forbid")
    decision, rule = store.evaluate(("git", "status"))
    assert decision == A.Decision.FORBID.value
    assert rule is not None and rule.decision == "forbid"


def test_evaluate_falls_through_to_the_prompt_rule(store: A.RuleStore) -> None:
    store.add_rule(("npm",), "prompt")
    decision, rule = store.evaluate(("npm", "install"))
    assert decision == A.Decision.PROMPT.value and rule is not None


def test_evaluate_says_nothing_when_nothing_matches(store: A.RuleStore) -> None:
    assert store.evaluate(("git", "status")) == ("", None)


def test_rule_store_round_trips_through_disk(tmp_path) -> None:
    runtime = tmp_path / "rt"
    store = A.RuleStore(runtime=runtime, profile="work", persist=True)
    store.add_rule(("pytest",), "allow", note="the project's own runner")
    store.add_rule(("make", "deploy"), "forbid", note="production")
    reopened = A.RuleStore(runtime=runtime, profile="work", persist=True)
    assert [rule.decision for rule in reopened.rules] == ["allow", "forbid"]
    assert reopened.rules[0].note == "the project's own runner"
    assert A.policy_path(runtime, "work").is_file()


def test_each_profile_owns_its_rules(tmp_path) -> None:
    runtime = tmp_path / "rt"
    A.RuleStore(runtime=runtime, profile="a", persist=True).add_rule(("git", "status"), "allow")
    A.RuleStore(runtime=runtime, profile="b", persist=True).add_rule(("git", "status"), "forbid")
    a = A.RuleStore(runtime=runtime, profile="a", persist=True)
    b = A.RuleStore(runtime=runtime, profile="b", persist=True)
    assert a.rules[0].decision == "allow" and b.rules[0].decision == "forbid"


def test_a_broken_policy_file_is_an_error_not_a_silent_empty_policy(tmp_path) -> None:
    runtime = tmp_path / "rt"
    path = A.policy_path(runtime, "default")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(A.PolicyError):
        A.RuleStore(runtime=runtime, persist=True)


def test_a_rule_can_be_removed(store: A.RuleStore) -> None:
    rule = store.add_rule(("git", "status"), "allow")
    assert store.remove_rule(rule.rule_id) is True
    assert store.remove_rule(rule.rule_id) is False
    assert store.rules == ()


def test_a_rule_for_a_banned_prefix_is_refused(store: A.RuleStore) -> None:
    with pytest.raises(A.BannedPrefixRefusal):
        store.add_rule(("python3",), "allow")
    with pytest.raises(A.BannedPrefixRefusal):
        store.add_rule(("rm",), "prompt")
    # harder verdicts are never an escalation, so a forbid rule stays possible
    assert store.add_rule(("rm",), "forbid").decision == "forbid"


# --------------------------------------------------------------- precedence


def test_a_banned_prefix_prompts_but_is_never_allowed(engine: A.ApprovalEngine) -> None:
    verdict = engine.judge("python3 -c 'print(1)'")
    assert verdict.decision == A.Decision.PROMPT.value
    assert verdict.reason_kind == "banned_prefix"
    assert verdict.banned_prefix == "python3"
    assert verdict.exit_code == 3
    assert any("foreground approval" in line for line in verdict.what_would_allow())
    assert any("refused" in line for line in verdict.what_would_allow())


def test_an_undecomposable_command_prompts_even_with_a_perfect_rule(
    engine: A.ApprovalEngine,
) -> None:
    engine.store.add_rule(("git", "status"), "allow")
    verdict = engine.judge("git status > /tmp/out.txt")
    assert verdict.decision == A.Decision.PROMPT.value
    assert verdict.reason_kind == "undecomposable"
    assert "never auto-allowed" in verdict.reason


def test_every_segment_must_be_covered(engine: A.ApprovalEngine) -> None:
    engine.store.add_rule(("git", "log"), "allow")
    verdict = engine.judge("git log && git diff")
    assert verdict.decision == A.Decision.PROMPT.value
    assert "'git diff'" in verdict.reason
    assert "allow \"git log\"" in verdict.reason, "the reason names what was already decided"


def test_two_covered_segments_are_allowed(engine: A.ApprovalEngine) -> None:
    engine.store.add_rule(("git", "log"), "allow")
    engine.store.add_rule(("git", "diff"), "allow")
    verdict = engine.judge("git log && git diff")
    assert verdict.decision == A.Decision.ALLOW.value
    assert verdict.exit_code == 0


def test_a_forbid_rule_beats_a_session_grant(profile) -> None:
    store = A.RuleStore(persist=False)
    ledger = A.GrantLedger(banned=store.banned, persist=False)
    ledger.add(("pytest",), "session", command="pytest -q", granted_at=1.0)
    engine = A.ApprovalEngine(profile, store=store, grants=ledger, session="s")
    assert engine.judge("pytest -q").decision == A.Decision.ALLOW.value
    store.add_rule(("pytest",), "forbid")
    assert engine.judge("pytest -q").decision == A.Decision.FORBID.value, (
        "an explicit never must win over a stored grant"
    )


def test_the_profile_allowlist_is_a_ceiling(profile) -> None:
    strict = profile.with_changes(allowed_subprocess_prefixes=(("git", "status"),))
    engine = A.ApprovalEngine(strict, store=A.RuleStore(persist=False), session="s")
    engine.store.add_rule(("make", "deploy"), "allow")
    verdict = engine.judge("make deploy")
    assert verdict.decision == A.Decision.FORBID.value
    assert verdict.reason_kind == "profile_allowlist"


def test_being_inside_the_allowlist_is_not_an_authorisation(profile) -> None:
    strict = profile.with_changes(allowed_subprocess_prefixes=(("pytest",),))
    engine = A.ApprovalEngine(strict, store=A.RuleStore(persist=False), session="s")
    verdict = engine.judge("pytest -q")
    assert verdict.decision == A.Decision.PROMPT.value, (
        "the sandbox's declared command set is a limit, not the user's yes"
    )


def test_policy_never_rejects_outright(profile) -> None:
    engine = A.ApprovalEngine(profile, store=A.RuleStore(persist=False), policy="never")
    verdict = engine.judge("git status")
    assert verdict.decision == A.Decision.FORBID.value
    assert "cannot be shown" in verdict.reason


def test_unless_trusted_ignores_grants_but_honours_rules(profile) -> None:
    ledger = A.GrantLedger(banned=A.DEFAULT_BANNED_PREFIXES, persist=False)
    ledger.add(("ls",), "session", command="ls -la")
    engine = A.ApprovalEngine(
        profile, store=A.RuleStore(persist=False), grants=ledger, policy="unless_trusted"
    )
    assert engine.judge("ls -la").decision == A.Decision.PROMPT.value
    engine.store.add_rule(("ls",), "allow")
    assert engine.judge("ls -la").decision == A.Decision.ALLOW.value


def test_an_unknown_policy_is_refused(profile) -> None:
    with pytest.raises(A.PolicyError):
        A.ApprovalEngine(profile, policy="whatever")


def test_the_verdict_is_serializable(engine: A.ApprovalEngine) -> None:
    payload = engine.judge("git status").to_dict()
    assert payload["decision"] == "prompt"
    assert payload["profile"]["sha256"] == engine.profile.identity_sha256
    assert payload["segments"] == [["git", "status"]]
    assert payload["would_allow"]


def test_the_ladder_shares_the_tool_classifier_vocabulary() -> None:
    """One vocabulary at the seam: the command ladder and the tool classifier agree."""
    assert A.decision_from_tool_classification("ALLOW") == "allow"
    assert A.decision_from_tool_classification("ASK_APPROVAL") == "prompt"
    assert A.decision_from_tool_classification("PAUSE_AND_INVESTIGATE") == "prompt"
    assert A.decision_from_tool_classification("BLOCK") == "forbid"
    assert A.decision_from_tool_classification("something-new") == "prompt", (
        "an unknown classification must fail closed, never open"
    )


def test_the_banned_list_covers_arbitrary_run_and_destruction() -> None:
    banned = {A.render_prefix(item) for item in A.DEFAULT_BANNED_PREFIXES}
    assert {"python", "python3", "sh", "bash", "env", "xargs", "eval", "exec"} <= banned
    assert {"rm", "dd", "mkfs", "chmod", "chown"} <= banned
    assert {"curl", "wget", "nc", "ssh"} <= banned
    assert {"sudo", "su", "doas"} <= banned
    assert A.banned_reason(("python3",)).startswith("an interpreter")
