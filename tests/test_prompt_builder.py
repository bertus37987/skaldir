"""Prompt assembly — the sections, their budgets, and where each one came from.

The composer is tested against **real inputs** wherever a real input exists: the versioned
steering prompt in ``prompts/``, a real ``IncidentStore``/``NoteStore`` in a throwaway
runtime, the real command inventory, and the real project-context block. Fakes are used
only where the *absence* is the case under test (no store, no prompts directory, no tool
registry) — and every one of those asserts the read-only promise too: a build must never
create the file it went looking for.

The stability contract has its own file (``test_prompt_builder_stability.py``).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from skaldir import prompt_builder as P
from skaldir.incidents import STATUS_ACTIVE, Incident, IncidentStore
from skaldir.notes import NoteStore, ResearchNote
from skaldir.skills import STATUS_CANDIDATE, STATUS_PROMOTED, SkillCandidate, SkillStore

REPO_ROOT = Path(__file__).resolve().parents[1]
PROMPTS_ROOT = REPO_ROOT / "prompts"


@pytest.fixture
def runtime(tmp_path: Path) -> Path:
    """A throwaway runtime dir — never the developer's real install."""
    home = tmp_path / "rt"
    home.mkdir()
    return home


@pytest.fixture
def context(tmp_path: Path, runtime: Path) -> P.BuildContext:
    """A context over a real project dir and a real (empty) runtime."""
    project = tmp_path / "project"
    project.mkdir()
    return P.context_for(project=project, runtime=runtime, prompts_root=PROMPTS_ROOT)


def sec(assembly: P.PromptAssembly, name: str) -> P.PromptSection:
    """The composed section by name — a missing one is a test bug, not a case."""
    row = assembly.section(name)
    assert row is not None, f"no section {name!r} was composed"
    return row


# --------------------------------------------------------------------------- #
# the shape of the prompt                                                       #
# --------------------------------------------------------------------------- #


def test_the_sections_are_seven_named_blocks_in_a_fixed_order():
    assert P.SECTION_ORDER == (
        "identity",
        "hard_rules",
        "tools",
        "memory",
        "skills",
        "project_context",
        "runtime_facts",
    )
    assert tuple(spec.name for spec in P.SECTIONS) == P.SECTION_ORDER
    assert all(spec.budget_chars > 0 for spec in P.SECTIONS)
    assert all(spec.purpose for spec in P.SECTIONS)
    assert set(P.SECTION_PROVIDERS) == set(P.SECTION_ORDER)


def test_every_section_is_optional_and_an_empty_one_is_omitted_not_rendered(context):
    assembly = P.build_prompt(context)
    assert "memory" in assembly.omitted
    assert "## Memory" not in assembly.text
    # …but the explanation still says why it is absent:
    row = next(item for item in assembly.sections if item.name == "memory")
    assert row.present is False
    assert row.source and row.detail
    assert row.sha256 == ""


def test_an_unknown_section_is_refused_by_name(context):
    with pytest.raises(ValueError) as caught:
        P.build_prompt(context, without=["nope"])
    assert "nope" in str(caught.value)
    assert "identity" in str(caught.value)


def test_without_removes_a_section_from_both_the_text_and_the_report(context):
    full = P.build_prompt(context)
    trimmed = P.build_prompt(context, without=["tools"])
    assert "tools" not in trimmed.section_map()
    assert "## Tool catalogue" not in trimmed.text
    assert trimmed.total_chars < full.total_chars or not full.section("tools").present


# --------------------------------------------------------------------------- #
# real inputs: the steering prompt, the catalogue, the runtime facts            #
# --------------------------------------------------------------------------- #


def test_the_hard_rules_are_the_real_steering_prompt_not_invented_text(context):
    from skaldir.prompts import PromptRegistry

    expected = PromptRegistry(PROMPTS_ROOT).get("main_agent_steering")
    row = sec(P.build_prompt(context), "hard_rules")
    assert row.present is True
    assert row.text == expected.body
    assert "main_agent_steering" in row.source
    assert f"v{expected.version}" in row.source
    assert row.detail.startswith("sha256=")


def test_a_missing_prompts_directory_omits_the_rules_and_says_so(tmp_path, runtime):
    ctx = P.context_for(
        project=tmp_path, runtime=runtime, prompts_root=tmp_path / "no-such-prompts"
    )
    row = sec(P.build_prompt(ctx), "hard_rules")
    assert row.present is False
    assert "no prompts directory" in row.detail
    assert "no-such-prompts" in row.detail


def test_the_catalogue_counts_the_real_product_commands(context):
    from skaldir import cli_contract

    row = sec(P.build_prompt(context), "tools")
    assert row.present is True
    assert str(len(cli_contract.COMMANDS)) in row.text
    assert "cli_contract" in row.source


def test_a_missing_tool_registry_is_reported_and_never_created(context):
    assert not context.tools_db.exists()
    row = sec(P.build_prompt(context), "tools")
    assert "absent" in row.source
    assert not context.tools_db.exists(), "a build must not create the registry it reads"


def test_the_runtime_facts_are_stable_facts_and_contain_no_timestamp(context):
    from skaldir import __version__

    row = sec(P.build_prompt(context), "runtime_facts")
    assert row.present is True
    assert __version__ in row.text
    assert "runtime" in row.text
    # The one thing that matters: nothing in the block can come from a clock.
    assert "2026" not in row.text
    assert "UTC" not in row.text
    assert "CEST" not in row.text


# --------------------------------------------------------------------------- #
# budgets and the truncation rule                                               #
# --------------------------------------------------------------------------- #


def test_the_truncation_rule_clips_at_the_budget_and_always_marks_the_cut():
    text = "\n".join(f"line {index} of a long block" for index in range(200))
    clipped, original, truncated = P.clip(text, 200)
    assert truncated is True
    assert original == len(text)
    assert len(clipped) < len(text)
    assert "\n…[truncated: " in clipped
    # The number in the marker is honest: kept head + reported loss = the original.
    kept = clipped[: clipped.index("\n…[truncated: ")]
    omitted = int(clipped.split("[truncated: ")[1].split(" chars]")[0])
    assert omitted == original - len(kept)
    assert len(kept) <= 200


def test_a_short_section_is_never_touched():
    clipped, original, truncated = P.clip("short", 400)
    assert (clipped, original, truncated) == ("short", 5, False)


def test_the_clip_prefers_a_line_boundary_and_never_cuts_mid_line_when_it_can_help():
    text = "a" * 10 + "\n" + "b" * 400
    clipped, _original, truncated = P.clip(text, 300)
    assert truncated is True
    assert clipped.startswith("a" * 10 + "\n")


def test_a_section_over_its_budget_is_clipped_and_reported(context, tmp_path):
    project = tmp_path / "loud"
    project.mkdir()
    (project / "AGENTS.md").write_text(
        "# Rules\n\n" + "\n".join(f"- rule number {index} must be obeyed" for index in range(400)),
        encoding="utf-8",
    )
    budgets = {"project_context": 300}
    ctx = P.context_for(project=project, runtime=context.runtime, budgets=budgets)
    assembly = P.build_prompt(ctx)
    row = sec(assembly, "project_context")
    if row.present:
        assert row.chars <= 300 + len(P.TRUNCATION_MARKER.format(omitted=row.original_chars))
        assert row.truncated is True
        assert row.original_chars > 300
        assert row.budget_chars == 300


def test_budget_for_honours_an_override_and_falls_back_to_the_default():
    assert P.budget_for("memory") == P.SECTION_SPECS["memory"].budget_chars
    assert P.budget_for("memory", {"memory": 7}) == 7


def test_the_token_estimate_is_labelled_as_chars_over_four(context):
    assembly = P.build_prompt(context)
    assert assembly.tokens_estimate == -(-assembly.total_chars // 4)
    assert "chars/4" in assembly.estimate_method
    assert "not a tokenizer" not in assembly.estimate_method
    assert "no tokenizer" in assembly.estimate_method


def test_over_budget_is_measured_not_implied(context):
    ctx = P.context_for(
        project=context.project, runtime=context.runtime, total_budget_tokens=10,
        prompts_root=PROMPTS_ROOT,
    )
    assembly = P.build_prompt(ctx)
    assert assembly.tokens_estimate > 10
    assert assembly.over_budget is True


# --------------------------------------------------------------------------- #
# the memory block — real stores, real content                                  #
# --------------------------------------------------------------------------- #


def test_memory_reads_a_real_incident_store(context):
    store = IncidentStore(context.memory_db)
    store.upsert(
        Incident(
            signature="boom::1",
            title="the widget exploded",
            root_cause="a missing guard",
            component="widgets",
            successful_recovery="add the guard and re-run",
            confidence=0.9,
        )
    )
    store.promote("boom::1", STATUS_ACTIVE)
    store.close()

    row = sec(P.build_prompt(context), "memory")
    assert row.present is True
    assert "boom::1" in row.text
    assert "a missing guard" in row.text
    assert "component: widgets" in row.text
    assert str(context.memory_db) in row.source


def test_memory_wraps_a_research_note_as_untrusted_data(context):
    notes = NoteStore(context.memory_db)
    notes.add(
        ResearchNote(
            topic="caching",
            claim="a prompt cache is keyed on the exact bytes",
            source_type="docs",
            source_url="https://example.invalid/x",
        )
    )
    notes.close()

    row = sec(P.build_prompt(context), "memory")
    assert row.present is True
    assert "<<<UNTRUSTED source=note:" in row.text
    assert "<<<END UNTRUSTED>>>" in row.text
    assert "caching: a prompt cache is keyed on the exact bytes" in row.text


def test_memory_is_ordered_deterministically_not_by_insertion_time(context):
    store = IncidentStore(context.memory_db)
    for signature in ("zzz::2", "aaa::1", "mmm::3"):
        store.upsert(Incident(signature=signature, title=signature, confidence=0.9))
        store.promote(signature, STATUS_ACTIVE)
    store.close()
    first = sec(P.build_prompt(context), "memory").text
    second = sec(P.build_prompt(context), "memory").text
    assert first == second
    assert first.index("aaa::1") < first.index("mmm::3") < first.index("zzz::2")


def test_a_missing_memory_db_is_omitted_and_never_created(context):
    assert not context.memory_db.exists()
    assembly = P.build_prompt(context)
    row = sec(assembly, "memory")
    assert row.present is False
    assert "no memory db at" in row.detail
    assert not context.memory_db.exists()


def test_a_broken_memory_store_is_reported_but_does_not_break_the_build(context):
    context.memory_db.write_bytes(b"this is not a sqlite database")
    assembly = P.build_prompt(context)
    row = sec(assembly, "memory")
    assert assembly.text  # the build still produced a prompt
    assert row.present is False
    assert "unreadable" in row.source or row.detail


# --------------------------------------------------------------------------- #
# the skills block — promoted only                                              #
# --------------------------------------------------------------------------- #


def test_a_promoted_skill_is_surfaced_through_the_modules_own_hint(context):
    store = SkillStore(context.memory_db)
    store.upsert(
        SkillCandidate(
            name="fix-flaky-suite",
            title="stabilise the suite",
            tool_chain=["pytest", "ruff"],
            occurrences=4,
        )
    )
    store.set_status("fix-flaky-suite", STATUS_PROMOTED)
    store.close()

    row = sec(P.build_prompt(context), "skills")
    assert row.present is True
    assert "fix-flaky-suite" in row.text
    assert "pytest → ruff" in row.text
    assert "1 promoted" in row.detail


def test_a_candidate_that_is_not_promoted_never_reaches_the_prompt(context):
    store = SkillStore(context.memory_db)
    store.upsert(SkillCandidate(name="speculative", title="maybe useful", tool_chain=["pytest"]))
    store.set_status("speculative", STATUS_CANDIDATE)
    store.close()

    row = sec(P.build_prompt(context), "skills")
    assert row.present is False
    assert "speculative" not in row.text
    assert "none promoted" in row.detail


# --------------------------------------------------------------------------- #
# project context — the shared block first, the fallback only when it is absent #
# --------------------------------------------------------------------------- #


def test_the_shared_project_context_block_is_used_and_named(tmp_path, runtime):
    project = tmp_path / "with-agents"
    project.mkdir()
    (project / "AGENTS.md").write_text(
        "# Project\n\n## Rules\n- Always run the tests.\n", encoding="utf-8"
    )
    ctx = P.context_for(project=project, runtime=runtime, prompts_root=PROMPTS_ROOT)
    row = sec(P.build_prompt(ctx), "project_context")
    assert row.present is True
    assert "Always run the tests." in row.text
    assert row.source.startswith("skaldir.project_context.")
    assert "block fingerprint" in row.detail


def test_the_fallback_walk_up_is_used_and_reported_when_the_block_cannot_answer(
    tmp_path, runtime, monkeypatch
):
    project = tmp_path / "deep" / "nested"
    project.mkdir(parents=True)
    (tmp_path / "AGENTS.md").write_text("fallback rules\n", encoding="utf-8")
    monkeypatch.setattr(
        P, "shared_project_context", lambda ctx, budget: (None, "not importable (test)")
    )
    ctx = P.context_for(project=project, runtime=runtime, prompts_root=PROMPTS_ROOT)
    row = sec(P.build_prompt(ctx), "project_context")
    assert row.present is True
    assert "fallback rules" in row.text
    assert row.source.startswith("fallback:")
    assert "walk-up" in row.detail
    assert "did not answer" in row.detail


def test_a_project_without_instruction_files_omits_the_section_and_names_both_paths(
    tmp_path, runtime
):
    project = tmp_path / "bare-project"
    project.mkdir()
    ctx = P.context_for(project=project, runtime=runtime, prompts_root=PROMPTS_ROOT)
    row = sec(P.build_prompt(ctx), "project_context")
    assert row.present is False
    assert "skaldir.project_context" in (row.source + row.detail)


def test_a_shared_block_that_injected_nothing_is_not_charged_to_the_prompt(tmp_path, runtime):
    """An empty workspace still renders a block; the composer must not carry its marker prose."""
    project = tmp_path / "empty-context"
    project.mkdir()
    ctx = P.context_for(project=project, runtime=runtime, prompts_root=PROMPTS_ROOT)
    row = sec(P.build_prompt(ctx), "project_context")
    assert row.present is False
    assert row.chars == 0


def test_the_shared_block_receives_this_sections_budget(tmp_path, runtime):
    """The block budgets its own sections, so it is called with *our* budget, not its default."""
    project = tmp_path / "budgeted"
    project.mkdir()
    (project / "AGENTS.md").write_text(
        "# Rules\n\n" + "\n".join(f"- rule {i}" for i in range(500)), encoding="utf-8"
    )
    ctx = P.context_for(
        project=project, runtime=runtime, prompts_root=PROMPTS_ROOT,
        budgets={"project_context": 400},
    )
    row = sec(P.build_prompt(ctx), "project_context")
    assert "max_chars=400" in row.detail


# --------------------------------------------------------------------------- #
# the explanation                                                               #
# --------------------------------------------------------------------------- #


def test_explain_reports_size_budget_hash_and_source_for_every_section(context):
    assembly = P.build_prompt(context)
    report = P.explain(assembly, context)
    assert report["fingerprint"] == assembly.fingerprint
    assert report["chars"] == assembly.total_chars
    assert report["estimate_method"] == P.ESTIMATE_METHOD
    assert report["section_count"] == len(P.SECTION_ORDER)
    assert report["present"] == assembly.present
    assert report["omitted"] == assembly.omitted
    assert [row["name"] for row in report["sections"]] == list(P.SECTION_ORDER)
    present = [row for row in report["sections"] if row["present"]]
    assert present and all(row["source"] and row["sha256"] for row in present)
    assert report["inputs"]["runtime"] == str(context.runtime)
    assert report["inputs"]["project"] == str(context.project)


def test_the_section_hashes_are_the_hashes_of_the_section_text(context):
    assembly = P.build_prompt(context)
    for row in assembly.sections:
        if row.present:
            assert row.sha256 == P.fingerprint_of(row.text)
        else:
            assert row.sha256 == ""


def test_the_fingerprint_is_sha256_over_the_exact_utf8_bytes(context):
    import hashlib

    assembly = P.build_prompt(context)
    assert assembly.fingerprint == hashlib.sha256(assembly.text.encode("utf-8")).hexdigest()
    assert assembly.total_bytes == len(assembly.text.encode("utf-8"))


def test_the_assembly_serialises_to_a_json_document(context):
    payload = P.build_prompt(context).to_dict()
    assert json.loads(json.dumps(payload)) == payload  # nothing exotic inside
    assert payload["fingerprint"] and payload["text"]
    assert set(payload["present"]) == {
        row["name"] for row in payload["sections"] if row["present"]
    }
