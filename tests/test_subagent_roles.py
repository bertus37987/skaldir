"""Subagent **roles** — the ceiling is enforced, not requested (MVP/93 §1).

The claim under test is narrow and adversarial: a role-bearing child cannot make
a call its role does not hold, and the refusal it gets is a **named code**, not a
sentence in a prompt. Each test therefore *attempts the forbidden call* and holds
the resulting refusal, rather than asserting that the prompt looks strict.

Pinned here:

1. PROVISIONING — an ``explore`` child is not offered ``edit``/``write``/``exec``
   at all; a ``verifier`` is not offered ``exec``; the pure filter and the brief
   agree with the policy table.
2. DISPATCH — the forbidden call raises (or returns) a refusal with a stable
   ``code``; the handler behind the forbidden tool is **never invoked**.
3. DELEGATION — a ``worker`` may not spawn, and in the in-process harness its
   declared ``[[spawn:…]]`` is refused with ``role_ceiling.delegation_forbidden``
   while the child keeps working; the refusal is in the run record.
4. BUDGET — a role's own tool-call/turn/token envelope is a ceiling on the brief,
   so no caller can widen a role by passing a bigger number.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from skaldir import subagent_roles as R
from skaldir import subagents as S

# -- provisioning ----------------------------------------------------------- #


def test_explore_is_never_offered_a_write_or_exec_tool():
    offered = R.tools_for("explore")
    assert "edit" not in offered and "write" not in offered and "exec" not in offered
    assert "spawn" not in offered
    assert {"read", "grep", "glob"} <= set(offered)


def test_provision_tools_removes_what_a_role_may_not_have():
    everything = sorted(R.KNOWN_TOOLS)
    assert R.provision_tools(everything, "explore") == sorted(R.tools_for("explore"))
    assert "edit" not in R.provision_tools(everything, "verifier")
    assert "exec" not in R.provision_tools(everything, "verifier")
    assert "test_run" in R.provision_tools(everything, "verifier")
    assert "spawn" not in R.provision_tools(everything, "worker")
    assert "edit" in R.provision_tools(everything, "worker")
    # ``main`` is the root's own surface: it keeps the delegation tools.
    assert "spawn" in R.provision_tools(everything, "main")


def test_the_brief_and_the_policy_table_cannot_drift():
    for role in R.CANONICAL_ROLES:
        brief = S.briefs_from_tasks(["work"], role=role)[0]
        assert brief.tools == R.tools_for(role)
        assert brief.may_spawn is bool(R.ROLE_POLICIES[role].may_spawn)
    assert R.ceiling_report()["roles"][0]["role"] == "explore"


def test_ceiling_report_names_the_enforcement_point():
    report = R.ceiling_report()
    assert report["schema"] == "skaldir.subagent_roles.v1"
    assert set(report["known_tools"]) >= {"read", "edit", "exec", "spawn"}
    assert R.REFUSAL_DELEGATION_FORBIDDEN in report["refusal_codes"]
    for row in report["roles"]:
        assert row["forbidden_tools"] or row["role"] == "main"
    explore = next(row for row in report["roles"] if row["role"] == "explore")
    assert explore["read_only"] is True and explore["may_write"] is False


# -- dispatch --------------------------------------------------------------- #


def test_an_explore_child_is_refused_edit_write_and_exec_by_name():
    calls: list[str] = []
    dispatcher = R.RoleToolDispatcher(
        "explore", handlers={"edit": lambda **kw: calls.append("edit")}
    )
    for tool in ("edit", "write", "exec"):
        with pytest.raises(R.ToolCeilingRefused) as excinfo:
            dispatcher.dispatch(tool, path="x.py")
        refusal = excinfo.value
        assert refusal.code == R.REFUSAL_TOOL_NOT_GRANTED
        assert refusal.role == "explore" and refusal.tool == tool
        assert "does not hold this tool" in refusal.reason
    assert calls == [], "a refused tool must never reach its handler"
    assert len(dispatcher.refusals()) == 3
    assert dispatcher.executed() == []


def test_a_worker_is_refused_the_spawn_tool_with_the_delegation_code():
    calls: list[str] = []
    dispatcher = R.RoleToolDispatcher(
        "worker", handlers={"spawn": lambda **kw: calls.append("spawn")}
    )
    with pytest.raises(R.ToolCeilingRefused) as excinfo:
        dispatcher.dispatch("spawn", tasks=["a grandchild"])
    assert excinfo.value.code == R.REFUSAL_DELEGATION_FORBIDDEN
    assert "may not delegate" in excinfo.value.reason
    assert calls == []
    # the same call from the root's own surface is fine — the ceiling is the role's
    root = R.RoleToolDispatcher("main", handlers={"spawn": lambda **kw: {"ok": True}})
    assert root.dispatch("spawn", tasks=["a child"])["ok"] is True


def test_a_verifier_is_read_only_except_for_the_declared_check():
    dispatcher = R.RoleToolDispatcher("verifier")
    assert dispatcher.may_call("read") and dispatcher.may_call("test_run")
    assert not dispatcher.may_call("edit")
    assert not dispatcher.may_call("exec")
    with pytest.raises(R.ToolCeilingRefused) as excinfo:
        dispatcher.dispatch("write", path="out.txt")
    assert excinfo.value.code == R.REFUSAL_TOOL_NOT_GRANTED
    # a read is granted: the ceiling refuses rights, it does not refuse work.
    assert dispatcher.dispatch("read", path="src/app.py")["mode"] == "declared"
    assert dispatcher.dispatch("test_run", argv=["pytest", "-q"])["ok"] is True


def test_an_unknown_tool_is_refused_by_every_role_including_main():
    for role in (*R.CANONICAL_ROLES, "main"):
        dispatcher = R.RoleToolDispatcher(role)
        with pytest.raises(R.ToolCeilingRefused) as excinfo:
            dispatcher.dispatch("rm_rf", path="/")
        assert excinfo.value.code == R.REFUSAL_TOOL_NOT_GRANTED
        assert "unknown tool name" in excinfo.value.reason


def test_try_dispatch_returns_the_refusal_instead_of_raising():
    dispatcher = R.RoleToolDispatcher("explore")
    result, refusal = dispatcher.try_dispatch("exec", cmd="rm -rf /")
    assert result is None
    assert refusal is not None and refusal["code"] == R.REFUSAL_TOOL_NOT_GRANTED
    ok, none = dispatcher.try_dispatch("read", path="README.md")
    assert none is None and ok is not None and ok["tool"] == "read"


def test_the_role_tool_call_budget_is_a_ceiling_too():
    dispatcher = R.RoleToolDispatcher(
        "explore", budget=R.RoleBudget(max_turns=1, max_tokens=100, max_tool_calls=2)
    )
    assert dispatcher.dispatch("read", path="a")["ok"] is True
    assert dispatcher.dispatch("read", path="b")["ok"] is True
    with pytest.raises(R.RoleBudgetExhausted) as excinfo:
        dispatcher.dispatch("read", path="c")
    assert excinfo.value.code == R.REFUSAL_BUDGET_EXHAUSTED
    assert isinstance(excinfo.value, R.ToolCeilingRefused)
    assert dispatcher.report()["refused"] == 1


def test_a_budget_may_be_narrowed_never_widened():
    verifier = R.ROLE_BUDGETS["verifier"]
    tighter = R.RoleBudget(max_turns=1, max_tokens=10, max_tool_calls=1)
    assert verifier.narrowed_by(tighter) == tighter
    assert tighter.narrowed_by(verifier) == tighter
    with pytest.raises(ValueError):
        R.RoleBudget(max_turns=0, max_tokens=10, max_tool_calls=1)


def test_an_unknown_role_name_is_refused_loudly():
    for bad in ("admin", "root", "explore2", "EXPLORE!"):
        with pytest.raises(ValueError):
            R.validate_role(bad)
    assert R.validate_role("  VERIFIER  ") == "verifier"
    with pytest.raises(ValueError):
        R.RoleToolDispatcher("nope")
    assert R.policy_for("", allow_unroled=True) is None


# -- the child is bounded by DEFAULT, and the ceiling-free child is asked for --- #


def test_a_child_is_role_bearing_unless_unroled_is_asked_for_by_name():
    """The ceiling used to be opt-in (``role=""`` = unlimited): a control that was off."""
    default = S.briefs_from_tasks(["do the work"])[0]
    assert default.role == R.DEFAULT_CHILD_ROLE == "worker"
    assert default.may_spawn is False

    # the kind picks the canonical role — a research child is not a writer
    assert S.briefs_from_tasks(["map it"], kind="research")[0].role == "explore"
    assert S.briefs_from_tasks(["check it"], kind="verify")[0].role == "verifier"

    # the historical ceiling-free child still exists, but only by explicit name
    legacy = S.briefs_from_tasks(["do the work"], role=R.UNROLED)[0]
    assert legacy.role == "" and legacy.policy is None
    assert legacy.tools == tuple(sorted(R.KNOWN_TOOLS))
    # ...and a plain ChildBrief cannot land there by omitting the parameter
    assert S.ChildBrief(child_id="c9", task="x").role == "worker"


def test_the_role_ceiling_speaks_the_products_real_tool_vocabulary():
    """A ceiling over a vocabulary the runtime does not speak is not a ceiling."""
    from skaldir.tools_core import registry as core_registry

    real = set(core_registry.names())
    assert real <= R.KNOWN_TOOLS, sorted(real - R.KNOWN_TOOLS)
    # every real core tool has exactly one class, and no name has two
    classes = R.tool_classes()
    for name in real:
        assert name in classes, name
    for tool in sorted(real):
        holders = [role for role in R.ROLES if tool in R.ROLE_POLICIES[role].tools]
        assert holders, f"{tool} is held by no role at all"
        if tool in {"patch", "write_file", "terminal", "docker.ensure"}:
            assert "explore" not in holders and "verifier" not in holders, tool
    # a read-only role can really be offered the real read tools
    assert {"read_file", "search_files", "list_dir"} <= set(R.tools_for("explore"))
    assert R.READ_TOOLS.isdisjoint(R.WRITE_TOOLS | R.EXEC_TOOLS | R.TEST_TOOLS)


def test_the_run_role_reaches_the_dispatcher_and_a_retry_keeps_it(tmp_path: Path):
    """A retry must not silently re-role a child (an explore child gaining write)."""
    briefs = S.briefs_from_tasks(["recon the tree"], depth=1, role="explore")
    run = S.SubagentRun(
        provider=S.StubProvider(latency_s=0.0, default_turns=1),
        briefs=briefs,
        state_dir=tmp_path,
        run_id="role-retry",
    )
    record = run.run().as_dict()
    retried = S.retry_briefs(record, only_unfinished=False)
    assert retried and retried[0].role == "explore", "the retry lost (or widened) the role"

    unroled_record = S.SubagentRun(
        provider=S.StubProvider(latency_s=0.0, default_turns=1),
        briefs=S.briefs_from_tasks(["legacy work"], depth=1, role=R.UNROLED),
        state_dir=tmp_path,
        run_id="role-retry-unroled",
    ).run().as_dict()
    assert S.retry_briefs(unroled_record, only_unfinished=False)[0].role == ""


# -- the brief cannot widen a role ------------------------------------------ #


def test_a_role_brief_cannot_be_granted_more_than_its_envelope():
    greedy = S.briefs_from_tasks(
        ["read everything"], max_turns=99, budget_tokens=999_999, role="verifier"
    )[0]
    envelope = R.ROLE_BUDGETS["verifier"]
    assert greedy.max_turns == envelope.max_turns
    assert greedy.budget_tokens == envelope.max_tokens
    assert greedy.role_budget() == envelope
    with pytest.raises(S.SubagentError):
        S.briefs_from_tasks(["x"], role="admin")
    # the default role narrows the brief's own numbers (a caller cannot widen a role)...
    default = S.briefs_from_tasks(["x"], max_turns=99)[0]
    assert default.role == "worker" and default.max_turns == R.ROLE_BUDGETS["worker"].max_turns
    # ...and the ceiling-free child, asked for by name, keeps the caller's numbers
    unroled = S.briefs_from_tasks(["x"], max_turns=99, role=R.UNROLED)[0]
    assert unroled.max_turns == 99 and unroled.role == ""


# -- the harness: a worker's declared spawn is refused ---------------------- #


def test_the_harness_refuses_a_worker_spawn_and_records_the_named_code(tmp_path: Path):
    run = S.SubagentRun(
        provider=S.StubProvider(latency_s=0.0, default_turns=1),
        briefs=S.briefs_from_tasks(
            ["parent work [[spawn:grandchild A]] [[turns:1]]"], depth=1, role="worker"
        ),
        state_dir=tmp_path,
        run_id="role-worker",
        max_depth=2,
        provider_summary={"mode": "stub"},
    )
    record = run.run()
    child = record.children[0]

    # the child kept working — a refused delegation is not a failed child
    assert child.status == S.STATUS_DELIVERED
    refused = record.nesting["refused"]
    assert len(refused) == 1
    assert refused[0]["code"] == R.REFUSAL_DELEGATION_FORBIDDEN
    assert refused[0]["ceiling"] == "role"
    assert refused[0]["role"] == "worker"
    assert record.nesting["spawned"] == []
    assert not list(Path(child.context["nested_dir"]).glob("*")), "a refused spawn left files"

    # the refusal is on the child's own result, and in the run's role report
    assert child.role == "worker"
    assert child.tool_refusals and child.tool_refusals[0]["code"] == R.REFUSAL_DELEGATION_FORBIDDEN
    assert any("role ceiling" in note for note in child.notes)
    report = run.role_report()["c1"]
    assert report["role"] == "worker"
    assert report["may_spawn"] is False
    assert report["spawn_ceiling"] == {"depth": "allows", "role": "blocks"}
    assert "edit" in report["tools_offered"] and "spawn" not in report["tools_offered"]
    assert report["refusals"][0]["code"] == R.REFUSAL_DELEGATION_FORBIDDEN


def test_an_unroled_child_keeps_the_depth_only_behaviour(tmp_path: Path):
    # NB: the ceiling-free child is now asked for **by name** (``role="unroled"``); the
    # default child is a ``worker`` whose declared spawn is refused by its role.
    record = S.spawn(
        ["parent work [[spawn:grandchild A]] [[turns:1]]"],
        provider=S.StubProvider(latency_s=0.0, default_turns=1),
        state_dir=tmp_path,
        run_id="role-unroled",
        max_depth=2,
        role=R.UNROLED,
        provider_summary={"mode": "stub"},
    )
    child = record.children[0]
    assert child.status == S.STATUS_DELIVERED
    assert record.nesting["refused"] == []
    assert len(record.nesting["spawned"]) == 1
    assert child.role == "" and child.tool_refusals == []


def test_the_role_reaches_the_child_prompt_as_what_it_has(tmp_path: Path):
    provider = S.StubProvider(latency_s=0.0, default_turns=1)
    record = S.spawn(
        ["map the repo [[turns:1]]"],
        provider=provider,
        state_dir=tmp_path,
        run_id="role-prompt",
        role="explore",
        provider_summary={"mode": "stub"},
    )
    assert record.children[0].role == "explore"
    prompts_seen = [call for call in getattr(provider, "calls", [])]
    assert prompts_seen, "the stub recorded no call"  # sanity: the child really ran
    marker = json.loads(
        (
            Path(record.children[0].context["workdir"]) / ".subagent.json"
        ).read_text(encoding="utf-8")
    )
    assert marker["role"] == "explore"
    assert "edit" not in marker["tools"]


def test_the_run_carries_a_tool_refusal_in_its_events(tmp_path: Path):
    events: list[dict] = []
    run = S.SubagentRun(
        provider=S.StubProvider(latency_s=0.0, default_turns=1),
        briefs=S.briefs_from_tasks(["work [[turns:1]]"], depth=1, role="verifier"),
        state_dir=tmp_path,
        run_id="role-events",
        on_event=events.append,
        provider_summary={"mode": "stub"},
    )
    run.run()
    with pytest.raises(R.ToolCeilingRefused):
        run.dispatch_tool("c1", "write", path="out.txt")
    assert run.tool_refusals("c1")["c1"][0]["code"] == R.REFUSAL_TOOL_NOT_GRANTED
    emitted = [event for event in events if event.get("kind") == "child_tool_refused"]
    assert emitted and emitted[0]["tool"] == "write"
    assert emitted[0]["role"] == "verifier"
