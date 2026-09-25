"""``skaldir permission …`` and ``skaldir approval …`` through their real verbs.

The CLI is the surface the product actually ships, so these tests drive it the way a user does:
``main([...])`` with real flags, real exit codes, real payloads. Three things are measured here
that no unit test above can:

* ``permission explain`` prints the enforcement matrix *and both hashes*, and says
  ``agree True`` — so the non-drift claim is checkable from the command line;
* ``approval check --dry-run`` answers allowed / prompted / forbidden for the **same command
  under three profiles**, which is the acceptance criterion for the ladder;
* the wiring call installs the group's contract entries, so a CLI that adds the group cannot
  leave ``cli_contract.COMMANDS`` behind.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from skaldir import approval as A
from skaldir import approval_cli as C
from skaldir import cli_contract
from skaldir import permission_profile as P


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    (root / ".git").mkdir(parents=True)
    (root / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    return root


@pytest.fixture
def runtime(tmp_path: Path) -> Path:
    home = tmp_path / "rt"
    home.mkdir()
    return home


def run(args: list[str], capsys) -> tuple[int, str, str]:
    code = C.main(args)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _base(runtime: Path, workspace: Path, profile: str = "default") -> list[str]:
    return ["--runtime", str(runtime), "--profile", profile, "--workspace", str(workspace)]


# ------------------------------------------------------------------ parser


def test_the_group_registers_both_verb_sets() -> None:
    import argparse

    parser = argparse.ArgumentParser(prog="skaldir")
    sub = parser.add_subparsers(dest="command", required=True)
    permission, approval = C.register_cli(sub)
    assert {"permission", "approval"} <= set(sub.choices)
    assert permission.prog.endswith("permission") and approval.prog.endswith("approval")


def test_registering_the_group_installs_its_contract_entries() -> None:
    """A CLI that adds the group cannot leave COMMANDS behind."""
    missing = [
        path for path in C.CONTRACT_ENTRIES if path not in cli_contract.COMMANDS
    ]
    assert not missing or C.install_entries() is not None
    for path, spec in C.CONTRACT_ENTRIES.items():
        assert path in cli_contract.COMMANDS, f"{path} never reached cli_contract.COMMANDS"
        assert spec is not None, f"{path} has no CommandSpec"
        assert spec.layer in cli_contract.LAYERS, f"{path} declares an unknown layer"
        assert spec.evidence, f"{path} declares no evidence"
        assert spec.data_schema, f"{path} declares no data schema"


def test_installing_twice_is_idempotent() -> None:
    assert C.install_entries() == []
    assert C.install_entries() == []


def test_every_verb_has_a_command_path() -> None:
    """The contract's own leaf walker agrees with this group's COMMANDS entries."""
    parser = C.build_parser()
    leaves = {path for path, _parser in cli_contract.leaf_commands(parser)}
    assert leaves == set(C.CONTRACT_ENTRIES), (
        "a verb without a contract entry is a verb nobody can call: "
        + str(sorted(leaves ^ set(C.CONTRACT_ENTRIES)))
    )
    for path, leaf in cli_contract.leaf_commands(parser):
        assert leaf.get_default("command_path") == path


# ------------------------------------------------------------- permission verbs


def test_permission_show_prints_the_profile_and_its_hash(
    runtime: Path, workspace: Path, capsys
) -> None:
    code, out, _ = run(["permission", "show", *_base(runtime, workspace)], capsys)
    assert code == 0
    profile = P.effective_profile(runtime, workspace)
    assert profile.identity_sha256 in out
    assert str(workspace) in out


def test_permission_show_carries_the_prompt_block_in_json(
    runtime: Path, workspace: Path, capsys
) -> None:
    code, out, _ = run(["permission", "show", *_base(runtime, workspace), "--json"], capsys)
    assert code == 0
    payload = json.loads(out)
    profile = P.effective_profile(runtime, workspace)
    assert payload["identity_sha256"] == profile.identity_sha256
    assert payload["prompt_block"] == profile.prompt_block()
    assert payload["hash_in_prompt"] == payload["hash_in_argv"] == profile.identity_sha256
    assert payload["enforcement"]


def test_permission_explain_proves_non_drift_from_the_command_line(
    runtime: Path, workspace: Path, capsys
) -> None:
    code, out, _ = run(["permission", "explain", *_base(runtime, workspace), "--json"], capsys)
    assert code == 0
    payload = json.loads(out)
    assert payload["hashes_agree"] is True
    assert payload["hash_in_argv"] == payload["hash_in_prompt"]
    assert payload["hash_in_argv"] in payload["sandbox_argv_line"]
    assert all(row["enforced_by"] for row in payload["enforcement"])
    assert payload["unenforced"], "an unenforced field must be reported"
    assert payload["probe"]["backend"] in {"bwrap", "podman", ""}


def test_permission_explain_is_readable_as_text(runtime: Path, workspace: Path, capsys) -> None:
    code, out, _ = run(["permission", "explain", *_base(runtime, workspace)], capsys)
    assert code == 0
    assert "enforcement" in out
    assert "sandbox argv (the enforced half)" in out
    assert "prompt block (the told half)" in out
    assert "agree              True" in out


def test_permission_set_moves_the_hash_and_persists(runtime: Path, workspace: Path, capsys) -> None:
    before = P.effective_profile(runtime, workspace)
    code, out, _ = run(
        [
            "permission",
            "set",
            *_base(runtime, workspace),
            "--set",
            "network=on",
            "--set",
            "read-policy=roots",
            "--set",
            "allow-command=git status;ls",
            "--json",
        ],
        capsys,
    )
    assert code == 0
    payload = json.loads(out)
    assert payload["hash_moved"] is True
    assert payload["previous_sha256"] == before.identity_sha256
    assert sorted(payload["changed_fields"]) == [
        "allowed_subprocess_prefixes",
        "network",
        "read_policy",
    ]
    stored = P.load_profile(runtime, "default")
    assert stored is not None and stored.network == "on"
    assert stored.identity_sha256 == payload["identity_sha256"]


def test_permission_set_refuses_an_unknown_field(runtime: Path, workspace: Path, capsys) -> None:
    code, out, _ = run(
        ["permission", "set", *_base(runtime, workspace), "--set", "nonsense=1"], capsys
    )
    assert code == 1


def test_permission_set_without_changes_is_a_usage_error(
    runtime: Path, workspace: Path, capsys
) -> None:
    code, _, _ = run(["permission", "set", *_base(runtime, workspace)], capsys)
    assert code == 1


def test_permission_set_appends_with_a_plus(runtime: Path, workspace: Path, capsys) -> None:
    run(["permission", "set", *_base(runtime, workspace), "--set", "writable=/a"], capsys)
    code, out, _ = run(
        ["permission", "set", *_base(runtime, workspace), "--set", "writable+=/b", "--json"], capsys
    )
    assert code == 0
    stored = P.load_profile(runtime, "default")
    assert stored is not None
    assert stored.writable_paths[-1] == "/b" and "/a" in stored.writable_paths


# --------------------------------------------------------------- approval verbs


def test_approval_check_says_prompt_for_an_uncovered_command(
    runtime: Path, workspace: Path, capsys
) -> None:
    code, out, _ = run(
        ["approval", "check", *_base(runtime, workspace), "--dry-run", "git", "status"], capsys
    )
    assert code == 3, "a prompt-required command blocks with exit 3"
    assert out.startswith("PROMPT")
    assert "--scope session" in out, "the block must say what would allow it"


def test_approval_check_answers_the_three_verdicts_for_the_same_command(
    runtime: Path, workspace: Path, capsys
) -> None:
    """The acceptance criterion: one command, three profiles, three answers."""
    command = "git status"
    for profile, verb in (("allow", "allow"), ("prompt", ""), ("forbid", "deny")):
        if verb:
            setup = ["approval", verb, *_base(runtime, workspace, profile), "--rule"]
            if verb == "deny":
                setup += ["--reason", "not here"]
            setup += [command]
            code, _, err = run(setup, capsys)
            assert code == 0, err
        code, out, _ = run(
            ["approval", "check", *_base(runtime, workspace, profile), "--dry-run", command],
            capsys,
        )
        expected = {
            "allow": ("ALLOW", 0),
            "prompt": ("PROMPT", 3),
            "forbid": ("FORBID", 3),
        }[profile]
        assert out.split()[0] == expected[0], f"profile {profile}: {out}"
        assert code == expected[1], f"profile {profile}: exit {code}"


def test_approval_check_dry_run_never_runs_anything(runtime: Path, workspace: Path, capsys) -> None:
    marker = workspace / "must-not-exist.txt"
    run(
        [
            "approval",
            "allow",
            *_base(runtime, workspace),
            "--rule",
            "touch",
            "must-not-exist.txt",
        ],
        capsys,
    )
    code, out, _ = run(
        [
            "approval",
            "check",
            *_base(runtime, workspace),
            "--dry-run",
            "touch",
            "must-not-exist.txt",
        ],
        capsys,
    )
    assert code == 0, out
    assert not marker.exists(), "check must never execute the command"


def test_approval_allow_reports_the_scope_and_the_record(
    runtime: Path, workspace: Path, capsys
) -> None:
    code, out, _ = run(
        ["approval", "allow", *_base(runtime, workspace), "--scope", "session", "pytest", "-q"],
        capsys,
    )
    assert code == 0
    assert "scope   session" in out
    assert "approval-events.jsonl:1" in out
    code, out, _ = run(["approval", "check", *_base(runtime, workspace), "pytest", "-q"], capsys)
    assert code == 0 and out.startswith("ALLOW")

    # the session grant is filed under its session id ("default" when none was named), and the
    # next command in that session finds it without asking again
    assert A.grants_path(runtime, "default").is_file()
    document = json.loads(A.grants_path(runtime, "default").read_text())
    assert document["session_grants"][0]["prefix"] == ["pytest", "-q"]
    other_session, other_out, _ = run(
        ["approval", "check", *_base(runtime, workspace), "--session", "another", "pytest", "-q"],
        capsys,
    )
    assert other_session == 3 and other_out.startswith("PROMPT"), (
        "a session grant must not be inherited by another session"
    )


def test_approval_allow_refuses_a_banned_prefix(runtime: Path, workspace: Path, capsys) -> None:
    code, out, err = run(
        [
            "approval",
            "allow",
            *_base(runtime, workspace),
            "--scope",
            "session",
            "python3",
            "-c",
            "x",
        ],
        capsys,
    )
    assert code == 3, out
    assert "refusing" in err, "a refusal must be visible from the standalone entry point"


def test_approval_deny_records_a_reason_the_agent_can_act_on(
    runtime: Path, workspace: Path, capsys
) -> None:
    code, out, _ = run(
        [
            "approval",
            "deny",
            *_base(runtime, workspace),
            "--reason",
            "use the release job",
            "make",
            "deploy",
        ],
        capsys,
    )
    assert code == 0
    assert "use the release job" in out
    event = A.EvidenceLedger(runtime).tail(1)[0]
    assert event["kind"] == "denial" and event["decision"] == "forbid"
    assert event["review_decision"] == "denied"


def test_approval_deny_with_rule_persists_a_forbid(runtime: Path, workspace: Path, capsys) -> None:
    run(
        [
            "approval",
            "deny",
            *_base(runtime, workspace),
            "--rule",
            "--reason",
            "no",
            "make",
            "deploy",
        ],
        capsys,
    )
    code, out, _ = run(["approval", "check", *_base(runtime, workspace), "make", "deploy"], capsys)
    assert code == 3 and out.startswith("FORBID")
    assert 'forbid "make deploy"' in out


def test_approval_list_shows_rules_bans_and_grants(runtime: Path, workspace: Path, capsys) -> None:
    run(["approval", "allow", *_base(runtime, workspace), "--rule", "pytest"], capsys)
    code, out, _ = run(["approval", "list", *_base(runtime, workspace), "--json"], capsys)
    assert code == 0
    payload = json.loads(out)
    assert payload["policy"]["counts"]["rules"] == 1
    assert payload["policy"]["counts"]["banned"] >= 50
    assert payload["events"]["count"] == 1
    assert any(item["prefix"] == ["pytest"] for item in payload["policy"]["rules"])
    assert ["python3"] in [list(item) for item in payload["policy"]["banned_prefixes"]]


def test_approval_run_blocks_headless_with_exit_3(runtime: Path, workspace: Path, capsys) -> None:
    code, out, _ = run(["approval", "run", *_base(runtime, workspace), "pytest", "-q"], capsys)
    assert code == 3, out
    assert "blocked (3)" in out
    assert "headless" in out
    event = A.EvidenceLedger(runtime).tail(1)[0]
    assert event["decision"] == "prompt" and event["exit_code"] == 3


def test_approval_run_dry_run_judges_without_running(
    runtime: Path, workspace: Path, capsys
) -> None:
    run(["approval", "allow", *_base(runtime, workspace), "--rule", "echo allowed"], capsys)
    code, out, _ = run(
        ["approval", "run", *_base(runtime, workspace), "--dry-run", "echo", "allowed"], capsys
    )
    assert code == 0
    assert "blocked" not in out


@pytest.mark.skipif(not shutil.which("bwrap"), reason="bubblewrap is not installed")
def test_approval_run_executes_inside_the_profile_and_carries_its_hash(
    runtime: Path, workspace: Path, capsys
) -> None:
    if not P.probe_backend(P.default_profile(workspace))["available"]:
        pytest.skip("bubblewrap is not usable on this host")
    run(["approval", "allow", *_base(runtime, workspace), "--rule", "echo"], capsys)
    code, out, _ = run(
        ["approval", "run", *_base(runtime, workspace), "echo", "profile-hash-ok"], capsys
    )
    assert code == 0, out
    assert "profile-hash-ok" in out
    profile = P.effective_profile(runtime, workspace)
    assert profile.identity_sha256 in out, "the run must name the profile it ran under"
    event = A.EvidenceLedger(runtime).tail(1)[0]
    assert event["decision"] == "allow"


def test_approval_run_reports_a_failing_command_as_verification_failed(
    runtime: Path, workspace: Path, capsys
) -> None:
    if not P.probe_backend(P.default_profile(workspace))["available"]:
        pytest.skip("bubblewrap is not usable on this host")
    run(["approval", "allow", *_base(runtime, workspace), "--rule", "false"], capsys)
    code, out, _ = run(["approval", "run", *_base(runtime, workspace), "false"], capsys)
    assert code == 2, out
    assert "ran (1)" in out


def test_the_suggested_follow_ups_are_real_commands() -> None:
    """A next_action must be runnable: prose offered as a command is noise."""
    engine = A.ApprovalEngine(
        P.default_profile("/tmp"),
        store=A.RuleStore(persist=False),
        interactive=False,
    )
    asked = C.suggested_commands(engine.judge("pytest -q"))
    assert asked, "a prompt must offer a follow-up"
    for command, reason in asked:
        assert command.startswith("skaldir approval "), command
        assert reason

    banned = C.suggested_commands(engine.judge("python3 -c 'x'"))
    assert banned and all(command.startswith("skaldir approval ") for command, _ in banned)
    assert all("refused" not in command for command, _ in banned), (
        "prose must not be offered as a command"
    )

    assert C.suggested_commands(engine.judge("echo *")) , "an undecomposable block still helps"

    engine.store.add_rule(("pytest",), "allow")
    assert C.suggested_commands(engine.judge("pytest -q")) == [], "an allow owes no follow-up"


def test_the_product_cli_exposes_the_group_as_enforce_commands(tmp_path) -> None:
    """The wiring the CLI owner already has must resolve to these eight commands."""
    from skaldir import cli

    runtime = tmp_path / "rt"
    runtime.mkdir()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    code = cli.main(["contract", "list", "--layer", "ENFORCE", "--json"])
    assert code == 0
    code = cli.main(
        [
            "approval",
            "check",
            "--runtime",
            str(runtime),
            "--workspace",
            str(workspace),
            "--json",
            "git",
            "status",
        ]
    )
    assert code == 3
    code = cli.main(
        ["permission", "show", "--runtime", str(runtime), "--workspace", str(workspace), "--json"]
    )
    assert code == 0


def test_no_persist_changes_no_policy_but_still_records_the_verdict(
    runtime: Path, workspace: Path, capsys
) -> None:
    """``--no-persist`` is about policy, not about the audit trail.

    A judgement is evidence and is always written; a rule or a grant is policy and is not. That
    split is deliberate — "do not remember this decision" must not mean "do not record that the
    decision happened", or the ledger would have holes exactly where someone was cautious.
    """
    run(["approval", "allow", *_base(runtime, workspace), "--no-persist", "pytest"], capsys)
    assert not A.policy_path(runtime, "default").exists()
    assert not A.grants_path(runtime, "default").exists()
    assert A.EvidenceLedger(runtime).count() == 1
    code, out, _ = run(
        ["approval", "check", *_base(runtime, workspace), "--no-persist", "pytest"], capsys
    )
    assert code == 3 and out.startswith("PROMPT"), "the grant really was not remembered"
