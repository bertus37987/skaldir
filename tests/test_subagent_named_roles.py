"""Named subagent roles: Researcher · Repo Scout · Debugger · Evidence Collector (+3).

Each role is a *right bundle* — tools it holds, whether it may delegate, and its own
budget — never prose that can widen a ceiling. The owner's four roles plus three more:
Implementer (writes), Verifier (checks), Planner (decomposes). Per-role provider/api-key
is wired separately (settings `subagent.roles.<role>.*`).
"""

from __future__ import annotations

from skaldir import subagent_roles as sr


def test_the_six_named_roles_exist_with_budgets():
    for name in (
        "researcher",
        "repo_scout",
        "debugger",
        "evidence_collector",
        "implementer",
        "planner",
    ):
        assert name in sr.ROLE_POLICIES, f"missing role {name}"
        assert name in sr.ROLE_BUDGETS, f"missing budget for {name}"
        assert name in sr.ROLES, f"role {name} not offered to the orchestrator"


def test_researcher_is_read_only_broad_research_no_spawn():
    role = sr.ROLE_POLICIES["researcher"]
    assert role.read_only and not role.may_spawn
    assert role.tools <= sr.READ_TOOLS, sorted(role.tools - sr.READ_TOOLS)
    assert {"web_search", "web_fetch"} <= role.tools  # broad research reaches the web


def test_repo_scout_traverses_the_repo_not_the_web():
    role = sr.ROLE_POLICIES["repo_scout"]
    assert role.read_only and not role.may_spawn
    assert {"read", "glob", "grep", "git_read"} <= role.tools
    assert "web_fetch" not in role.tools and "web_search" not in role.tools


def test_debugger_reproduces_with_exec_and_test_run():
    role = sr.ROLE_POLICIES["debugger"]
    assert not role.read_only and not role.may_spawn
    assert role.tools & sr.TEST_TOOLS and role.tools & sr.EXEC_TOOLS
    assert role.tools & sr.READ_TOOLS  # reads code while hunting the root cause


def test_evidence_collector_captures_logs_and_artifacts():
    role = sr.ROLE_POLICIES["evidence_collector"]
    assert not role.may_spawn
    assert role.tools & sr.EXEC_TOOLS  # runs log/test capture commands
    assert role.tools & sr.READ_TOOLS


def test_implementer_writes_and_may_delegate():
    role = sr.ROLE_POLICIES["implementer"]
    assert not role.read_only and role.may_spawn
    assert role.tools & sr.WRITE_TOOLS and role.tools & sr.TEST_TOOLS


def test_planner_decomposes_without_writing():
    role = sr.ROLE_POLICIES["planner"]
    assert role.read_only and not role.may_spawn
    assert not (role.tools & sr.WRITE_TOOLS)


def test_roles_provision_within_their_bundle():
    """A role never holds a tool outside its bundle (the ceiling is the bundle)."""
    for name in ("researcher", "repo_scout", "debugger", "evidence_collector",
                 "implementer", "planner"):
        role = sr.ROLE_POLICIES[name]
        granted = sr.provision_tools(sorted(sr.READ_TOOLS | sr.WRITE_TOOLS |
                                            sr.EXEC_TOOLS | sr.TEST_TOOLS), name)
        assert set(granted) <= role.tools, f"{name} provisioned {set(granted) - role.tools}"


# --- per-role provider / api-key (settings `subagent.roles.<role>.*`) -----------


def test_per_role_provider_and_key_win_over_the_global(tmp_path):
    """A role names its own provider + its own key variable; both beat the global."""
    (tmp_path / "settings.yaml").write_text(
        "subagent:\n"
        "  provider: deepseek\n"
        "  roles:\n"
        "    researcher:\n"
        "      provider: openai\n"
        "      model: gpt-5.6-luna\n"
        "      api_key_env: RESEARCHER_KEY\n",
        encoding="utf-8",
    )
    from skaldir import subagents as S

    client, info = S.resolve_subagent_provider(
        role="researcher", runtime=tmp_path, env={"RESEARCHER_KEY": "x"}
    )
    assert info["provider"] == "openai", info
    assert info["model"] == "gpt-5.6-luna", info
    assert info["api_key_env"] == "RESEARCHER_KEY", info
    assert "role" in info["source"] and "researcher" in info["source"], info["source"]


def test_a_role_without_an_override_falls_to_the_global_subagent_provider(tmp_path):
    (tmp_path / "settings.yaml").write_text(
        "subagent:\n  provider: deepseek\n  roles:\n    debugger:\n      provider: anthropic\n",
        encoding="utf-8",
    )
    from skaldir import subagents as S

    _client, info = S.resolve_subagent_provider(
        role="planner", runtime=tmp_path, env={"DEEPSEEK_API_KEY": "x"}
    )
    # planner has no role block → the global `subagent.provider`
    assert info["provider"] == "deepseek", info
    assert "subagent.provider" in info["source"], info["source"]
