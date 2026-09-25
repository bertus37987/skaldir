"""Project context files — discovery, precedence, the typed block, the fingerprint.

The four claims this file measures:

* **Discovery + precedence.** A file closer to the working directory wins; inside one directory
  the ladder is SKALDIR.md > SOUL.md > AGENTS.md > CLAUDE.md > .skaldir/context.md; the order
  is total, so the same workspace resolves the same way on every run. Nothing above the
  workspace boundary is read.
* **The typed block.** Only the six recognized sections are injected, an unrecognized heading is
  reported and never smuggled in, an explicit id is replaced by the higher-precedence file, and
  the losing text stays in the report.
* **The fingerprint.** Stable for the same content, different when a byte of the injected text
  changes, recomputable from the payload a run records, and carried in the evidence per file.
* **Safety.** Read-only (no fixture file changes), symlinks that leave the workspace are refused
  instead of followed, the per-file read is capped and reported, and the size cap never drops the
  rules silently.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from skaldir import project_context as P

AGENTS = """# House rules

## Role
You are a careful reviewer in the Acme monorepo.

## Rules
- R1: never push to main directly
- R2: run the test suite before every commit
- keep the changelog in sync
- R3: root-level rule that the nested file replaces

## Constraints
- C1: Python 3.11 or newer only

## Conventions
- prefer small functions

## Commands
- make test
"""

SOUL = """## Role
You speak plainly and never flatter.

## Rules
- S1: cite the source for every factual claim

## Commands
- make check
"""

SKALDIR = """## Rules
- R1: never push to main directly; open a PR and wait for review
"""

NESTED = """## Rules
- N1: this package pins its dependencies
- R3: nested rule wins over the root one

## Glossary
- "flow" = one capability run
"""

#: The block body (header stripped) the fixture below must produce, verbatim.
EXPECTED_BODY = """## Role
You speak plainly and never flatter.

## Rules
- [N1] this package pins its dependencies
- [R3] nested rule wins over the root one
- [R1] never push to main directly; open a PR and wait for review
- [S1] cite the source for every factual claim
- [R2] run the test suite before every commit
- keep the changelog in sync

## Constraints
- [C1] Python 3.11 or newer only

## Conventions
- prefer small functions

## Commands
- make check
- make test

## Glossary
- "flow" = one capability run
"""


def write(path: Path, text: str) -> Path:
    """Write one fixture file (and its parents)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def make_workspace(tmp_path: Path) -> tuple[Path, Path]:
    """The proof workspace: AGENTS/SOUL/SKALDIR at the root, one level nested, cwd two deep.

    ``.git`` makes the root the workspace boundary (so "nothing above the project" is testable:
    ``tmp_path/AGENTS.md`` sits above it and must never be read).
    """
    root = tmp_path / "root"
    (root / ".git").mkdir(parents=True)
    write(root / "AGENTS.md", AGENTS)
    write(root / "SOUL.md", SOUL)
    write(root / "SKALDIR.md", SKALDIR)
    write(root / "nested" / "AGENTS.md", NESTED)
    workdir = root / "nested" / "deeper"
    workdir.mkdir(parents=True)
    write(tmp_path / "AGENTS.md", "## Rules\n- ABOVE: this file is above the project\n")
    return root, workdir


def body_of(block: str) -> str:
    """The block without its provenance header — the injected text a prompt actually gets."""
    return block.split("\n", 1)[1]


# --------------------------------------------------------------------------- #
# discovery and precedence
# --------------------------------------------------------------------------- #


class TestDiscovery:
    def test_every_existing_candidate_is_reported_with_its_level(self, tmp_path):
        root, workdir = make_workspace(tmp_path)
        found, levels, start, boundary, why = P.discover(workdir)

        assert start == workdir.resolve()
        assert boundary == root.resolve()
        assert "git root" in why
        assert [(item.rel_path, item.level, item.kind) for item in found] == [
            ("nested/AGENTS.md", 1, "AGENTS.md"),
            ("SKALDIR.md", 2, "SKALDIR.md"),
            ("SOUL.md", 2, "SOUL.md"),
            ("AGENTS.md", 2, "AGENTS.md"),
        ]
        assert [row["level"] for row in levels] == [0, 1, 2]
        assert levels[0]["present"] == []  # the working directory itself has no context file
        assert levels[2]["present"] == ["SKALDIR.md", "SOUL.md", "AGENTS.md"]
        assert all(row["checked"] == len(P.FILENAME_LADDER) for row in levels)

    def test_nothing_above_the_workspace_boundary_is_read(self, tmp_path):
        """``tmp_path/AGENTS.md`` is one level above the git root — it is not this context."""
        _root, workdir = make_workspace(tmp_path)
        assert (tmp_path / "AGENTS.md").is_file()
        found, _levels, _start, boundary, _why = P.discover(workdir)
        assert all("ABOVE" not in item.text for item in found)
        assert all(not item.rel_path.startswith("..") for item in found)
        assert boundary == tmp_path / "root"

    def test_the_walk_stops_at_the_filesystem_root_without_a_git_dir(self, tmp_path):
        plain = tmp_path / "plain"
        write(plain / "AGENTS.md", AGENTS)
        found, _levels, _start, boundary, why = P.discover(plain)
        assert boundary == Path("/")
        assert "no .git found" in why
        # No boundary inside the tree: the relative paths are relative to the filesystem root.
        assert [item.rel_path for item in found] == [
            str((plain / "AGENTS.md").resolve()).lstrip("/")
        ]

    def test_an_explicit_workspace_wins(self, tmp_path):
        root, workdir = make_workspace(tmp_path)
        found, _levels, _start, boundary, why = P.discover(workdir, workspace=workdir)
        assert boundary == workdir.resolve()
        assert why == "explicit workspace boundary"
        assert found == ()  # the boundary excludes every file above it
        assert (root / "AGENTS.md").is_file()


class TestPrecedence:
    def test_deeper_beats_shallower_and_the_ladder_breaks_the_tie(self, tmp_path):
        _root, workdir = make_workspace(tmp_path)
        context = P.build(workdir)
        assert [item.rel_path for item in context.sources] == [
            "nested/AGENTS.md",  # level 1
            "SKALDIR.md",  # level 2, rank 0
            "SOUL.md",  # level 2, rank 1
            "AGENTS.md",  # level 2, rank 2
        ]

    def test_an_explicit_id_is_replaced_by_the_higher_precedence_file(self, tmp_path):
        _root, workdir = make_workspace(tmp_path)
        context = P.build(workdir)
        by_id = {row.id: row for row in context.resolutions}

        override = by_id["R1"]
        assert override.winner == "SKALDIR.md"
        assert override.losers == ("AGENTS.md",)
        assert override.text == "never push to main directly; open a PR and wait for review"
        assert override.losing_text == ("never push to main directly",)

        nested = by_id["R3"]  # level 1 beats level 2, whatever the ladder says
        assert nested.winner == "nested/AGENTS.md"
        assert nested.losers == ("AGENTS.md",)

        assert [row.id for row in context.overrides] == ["R3", "R1"]
        assert any("R1" in warning and "SKALDIR.md" in warning for warning in context.warnings)

    def test_role_is_scalar_and_the_higher_precedence_file_wins(self, tmp_path):
        _root, workdir = make_workspace(tmp_path)
        context = P.build(workdir)
        role = [section for section in context.sections if section.name == "role"][0]
        assert role.text == "You speak plainly and never flatter."  # SOUL.md, rank 1 over rank 2
        assert any("role: SOUL.md wins over AGENTS.md" in warning for warning in context.warnings)

    def test_identical_text_twice_is_one_entry_not_an_override(self, tmp_path):
        root = tmp_path / "same"
        write(root / "AGENTS.md", "## Rules\n- SHARED: keep the docs true\n")
        write(root / "SOUL.md", "## Rules\n- SHARED: keep the docs true\n")
        context = P.build(root)
        rules = [section for section in context.sections if section.name == "rules"][0]
        assert rules.total == 1
        assert context.overrides == ()
        winner = context.resolutions[0].winner
        assert winner.endswith("SOUL.md"), winner  # rank 1 beats rank 2, and without any noise


# --------------------------------------------------------------------------- #
# the typed block
# --------------------------------------------------------------------------- #


class TestBlock:
    def test_the_block_is_the_expected_text_verbatim(self, tmp_path):
        _root, workdir = make_workspace(tmp_path)
        context = P.build(workdir)
        assert body_of(context.block) == EXPECTED_BODY
        header = context.block.splitlines()[0]
        assert header.startswith("<!-- project context · skaldir.project_context/1 · fingerprint ")
        assert header.endswith(
            "sources: nested/AGENTS.md, SKALDIR.md, SOUL.md, AGENTS.md -->"
        )
        assert context.fingerprint[:16] in header
        assert f"chars {context.budgeted_chars}/{context.max_chars}" in header

    def test_only_recognized_sections_are_injected_and_the_rest_is_reported(self, tmp_path):
        work = tmp_path / "unmapped"
        write(
            work / "AGENTS.md",
            "# Title\n"
            "a preamble line that is not under any heading\n\n"
            "## Rules\n- R1: only this is injected\n\n"
            "## Deployment notes\n- secret operational detail\n",
        )
        context = P.build(work)
        assert "[R1] only this is injected" in context.block
        assert "secret operational detail" not in context.block
        assert "a preamble line" not in context.block
        assert any("Deployment notes" in warning for warning in context.warnings)
        assert any(
            "before the first recognized section" in warning for warning in context.warnings
        )
        unmapped = context.to_dict()
        assert unmapped["sections"]  # the report keeps the typed view
        assert any("Deployment notes" in warning for warning in unmapped["warnings"])

    def test_a_section_without_bullets_still_becomes_entries(self, tmp_path):
        work = tmp_path / "bare"
        write(work / "AGENTS.md", "## Commands\nmake check\nmake ship\n")
        context = P.build(work)
        assert "- make check" in context.block
        assert "- make ship" in context.block

    def test_a_derived_id_is_not_shown_but_a_duplicate_inside_one_file_is_reported(self, tmp_path):
        work = tmp_path / "dupes"
        write(work / "AGENTS.md", "## Rules\n- R1: one\n- R1: two\n")
        context = P.build(work)
        assert "- [R1] one" in context.block
        assert "two" not in context.block.split("## Rules")[1].split("\n")[1]
        assert any("appears twice in 'rules'" in warning for warning in context.warnings)

    def test_a_file_without_recognized_headings_is_injected_whole_and_marked(self, tmp_path):
        """Real-world AGENTS.md is prose: taking it whole beats injecting nothing."""
        work = tmp_path / "prose"
        write(
            work / "AGENTS.md",
            "# Project standard\n\n"
            "Read this before changing anything.\n\n"
            "## Code Shape Rules\n"
            "- keep functions small\n",
        )
        context = P.build(work)  # no .git here: the relative paths start above the tmp tree
        assert context.fallbacks[0].endswith("prose/AGENTS.md"), context.fallbacks
        assert context.to_dict()["fallback_files"] == list(context.fallbacks)
        assert "keep functions small" in context.block
        assert "Project standard" in context.block
        # The file's own text, not a bullet wrapped around it.
        assert "- # Project standard" not in context.block
        row = context.resolutions[0]
        assert (row.section, row.block) == ("rules", True)
        assert any("injected whole as one" in warning for warning in context.warnings)
        assert any(
            "inside the whole-file fallback" in warning for warning in context.warnings
        )

    def test_a_typed_file_stays_typed(self, tmp_path):
        """One recognized heading is enough: the file is injected by section, not whole."""
        work = tmp_path / "typed"
        write(
            work / "AGENTS.md",
            "## Rules\n- R1: typed\n\n## Code Shape Rules\n- NOT injected here\n",
        )
        context = P.build(work)
        assert context.fallbacks == ()
        assert "- [R1] typed" in context.block
        assert "NOT injected here" not in context.block
        assert any("not injected" in warning for warning in context.warnings)

    def test_two_prose_files_resolve_by_precedence_not_by_the_size_cap(self, tmp_path):
        """Both carry the reserved id, so the ladder picks the winner — at every cap."""
        root = tmp_path / "prose"
        (root / ".git").mkdir(parents=True)
        write(root / "AGENTS.md", "# Root standard\n\nbe careful with the database\n")
        write(root / "nested" / "AGENTS.md", "# Nested standard\n\nalways run the tests\n")

        for cap in (8000, 200):
            context = P.build(root / "nested", max_chars=cap)
            assert context.fallbacks == ("nested/AGENTS.md", "AGENTS.md")
            assert "always run the tests" in context.block, cap
            assert "be careful with the database" not in context.block, cap
            winner = [row for row in context.resolutions if row.id == P.WHOLE_FILE_ID][0]
            assert winner.winner == "nested/AGENTS.md"
            assert winner.losers == ("AGENTS.md",)
            # Blank lines are collapsed in the fallback text — it is a block, not a document.
            assert winner.losing_text == ("# Root standard\nbe careful with the database",)


class TestEvidenceAndFingerprint:
    def test_the_fingerprint_is_stable_and_recomputable_from_the_payload(self, tmp_path):
        _root, workdir = make_workspace(tmp_path)
        first = P.build(workdir)
        second = P.build(workdir)
        assert first.fingerprint == second.fingerprint
        assert len(first.fingerprint) == 64
        assert first.fingerprint_short == first.fingerprint[:16]
        assert first.fingerprint_of_payload() == first.fingerprint

    def test_the_fingerprint_changes_when_the_injected_text_changes(self, tmp_path):
        _root, workdir = make_workspace(tmp_path)
        before = P.build(workdir).fingerprint
        write(workdir.parent.parent / "SKALDIR.md", "## Rules\n- R1: a different instruction\n")
        after = P.build(workdir)
        assert after.fingerprint != before
        assert after.fingerprint_of_payload() == after.fingerprint

    def test_the_cap_is_part_of_the_fingerprint(self, tmp_path):
        _root, workdir = make_workspace(tmp_path)
        assert P.build(workdir).fingerprint != P.build(workdir, max_chars=1000).fingerprint

    def test_the_evidence_names_every_source_by_digest(self, tmp_path):
        root, workdir = make_workspace(tmp_path)
        evidence = P.build(workdir).evidence()
        assert evidence["fingerprint"].startswith(evidence["fingerprint_short"])
        assert evidence["truncated"] is False
        assert [row["path"] for row in evidence["sources"]] == [
            "nested/AGENTS.md",
            "SKALDIR.md",
            "SOUL.md",
            "AGENTS.md",
        ]
        for row in evidence["sources"]:
            on_disk = hashlib.sha256((root / row["path"]).read_bytes()).hexdigest()
            assert row["sha256"] == on_disk, row["path"]
        assert evidence["refused"] == []

    def test_prompt_block_returns_the_block_and_its_fingerprint(self, tmp_path):
        _root, workdir = make_workspace(tmp_path)
        block, fingerprint = P.prompt_block(workdir)
        assert block == P.build(workdir).block
        assert fingerprint == P.build(workdir).fingerprint


# --------------------------------------------------------------------------- #
# the size cap
# --------------------------------------------------------------------------- #


class TestBudgetAndTruncation:
    def test_the_rules_are_funded_first(self):
        plan = P.plan_budgets(400, needs={"rules": 9_000, "role": 50})
        assert plan["rules"] == 400
        assert plan["role"] == 0
        assert sum(plan.values()) <= 400

    def test_a_small_rules_section_leaves_the_room_to_the_others(self):
        plan = P.plan_budgets(3000, needs={"rules": 100, "role": 50})
        assert plan["rules"] == 100
        assert plan["role"] == 50
        assert plan["constraints"] == P.SECTION_QUOTAS["constraints"]

    def test_the_default_cap_holds_every_section_at_full_size(self, tmp_path):
        _root, workdir = make_workspace(tmp_path)
        context = P.build(workdir)
        assert context.truncated is False
        assert context.omitted_names == ()
        assert context.clipped_names == ()
        assert context.entries_included == context.entries_total == 12
        assert context.budgeted_chars <= context.max_chars

    def test_a_small_cap_drops_whole_entries_and_names_them(self, tmp_path):
        _root, workdir = make_workspace(tmp_path)
        context = P.build(workdir, max_chars=150)
        rules = [section for section in context.sections if section.name == "rules"][0]

        assert context.truncated is True
        assert rules.clipped is True
        # The rules are still there — clipped with an explicit marker, never dropped.
        assert "## Rules" in context.block
        assert "- [N1] this package pins its dependencies" in context.block
        assert "…[+4 not shown]" in context.block
        assert rules.dropped_ids == ("R1", "S1", "R2", "#480a3949abec")
        assert any(
            "rules: 4 of 6 entries are not in the block" in warning
            and "R1, S1" in warning
            for warning in context.warnings
        )
        assert context.budgeted_chars <= context.max_chars

    def test_a_cap_below_the_rules_still_injects_them(self, tmp_path):
        """The promise: a pathological cap clips the rules, it does not remove them."""
        _root, workdir = make_workspace(tmp_path)
        context = P.build(workdir, max_chars=40)
        assert "## Rules" in context.block
        # odd little rows land here: assert the marker is present or the rows are short
        assert "…[" in context.block or "- " in context.block
        assert context.truncated is True

    def test_a_single_entry_longer_than_the_section_is_cut_and_reported(self, tmp_path):
        """The whole-file fallback path: one entry, still bounded, still announced."""
        work = tmp_path / "long-prose"
        write(work / "AGENTS.md", "# Standard\n\n" + "\n".join(f"line {i} " + "y" * 40
                                                                for i in range(300)))
        context = P.build(work, max_chars=4000)
        rules = [section for section in context.sections if section.name == "rules"][0]
        assert context.truncated is True
        assert context.clipped_names == ("rules",)
        assert rules.clipped is True
        assert rules.dropped_ids == ()
        assert "…[truncated]" in context.block
        assert rules.used <= rules.quota
        assert any(
            "its entry is cut short (…[truncated]) at max_chars=4000" in warning
            for warning in context.warnings
        )

    def test_omitted_sections_say_so_in_the_block_and_in_the_warnings(self, tmp_path):
        _root, workdir = make_workspace(tmp_path)
        context = P.build(workdir, max_chars=150)
        assert "role" in context.omitted_names
        assert "glossary" in context.omitted_names
        assert "…[section omitted: no budget left" in context.block
        assert "omitted role,constraints" in context.block.splitlines()[0]
        assert any("role: omitted" in warning for warning in context.warnings)

    def test_an_empty_section_is_empty_not_omitted(self, tmp_path):
        """A section with nothing to inject is nothing; only a starved one is an omission."""
        work = tmp_path / "sparse"
        write(work / "AGENTS.md", "## Rules\n- R1: only rules here\n")
        context = P.build(work, max_chars=100)
        assert context.omitted_names == ()
        assert context.clipped_names == ()
        assert "## Glossary" not in context.block
        assert "omitted" not in context.block.splitlines()[0]

    def test_nothing_found_is_an_empty_block_and_a_warning(self, tmp_path):
        work = tmp_path / "bare-dir"
        work.mkdir()
        context = P.build(work)
        assert context.empty is True
        assert context.sources == ()
        assert "no project context file found" in context.warnings[0]
        assert context.block.count("\n") == 1  # the header and nothing else
        assert context.omitted_names == ()


# --------------------------------------------------------------------------- #
# safety
# --------------------------------------------------------------------------- #


class TestSafety:
    def test_a_symlink_out_of_the_workspace_is_refused_not_followed(self, tmp_path):
        root, workdir = make_workspace(tmp_path)
        outside = write(tmp_path / "outside.md", "## Rules\n- EVIL: injected from outside\n")
        os.symlink(outside, root / "CLAUDE.md")
        context = P.build(workdir)

        assert "EVIL" not in context.block
        refused = {item.rel_path: item for item in context.refused}
        assert refused["CLAUDE.md"].status == P.STATUS_REFUSED_ESCAPE
        assert "outside the workspace" in refused["CLAUDE.md"].reason
        assert any("refused-symlink-escape" in warning for warning in context.warnings)
        assert [row["path"] for row in context.evidence()["refused"]] == ["CLAUDE.md"]

    def test_a_symlink_inside_the_workspace_is_used_and_the_resolution_is_reported(self, tmp_path):
        root, workdir = make_workspace(tmp_path)
        target = write(root / "docs" / "context.md", "## Rules\n- D1: inside the workspace\n")
        os.symlink(target, root / "CLAUDE.md")
        context = P.build(workdir)
        used = {item.rel_path: item for item in context.sources}
        assert used["CLAUDE.md"].sha256 == hashlib.sha256(target.read_bytes()).hexdigest()
        assert "resolved via" in used["CLAUDE.md"].reason
        assert "D1" in context.block

    def test_the_per_file_read_is_capped_and_said_out_loud(self, tmp_path):
        work = tmp_path / "big"
        filler = "\n".join(f"- R{index}: " + "x" * 80 for index in range(1200))
        write(work / "AGENTS.md", "## Rules\n" + filler + "\n")
        assert (work / "AGENTS.md").stat().st_size > P.MAX_FILE_BYTES
        context = P.build(work)
        assert context.sources[0].file_truncated is True
        assert context.sources[0].nbytes == P.MAX_FILE_BYTES
        assert any("read the first" in warning for warning in context.warnings)

    def test_reading_a_context_never_writes(self, tmp_path):
        root, workdir = make_workspace(tmp_path)
        files = sorted(root.rglob("*"))
        before = {
            str(path): (path.stat().st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest())
            for path in files
            if path.is_file()
        }
        P.build(workdir)
        P.explain(workdir)
        P.prompt_block(workdir)
        after = {
            str(path): (path.stat().st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest())
            for path in files
            if path.is_file()
        }
        assert before == after
        assert sorted(str(path) for path in root.rglob("*")) == sorted(str(path) for path in files)

    def test_a_directory_named_like_a_context_file_is_refused(self, tmp_path):
        work = tmp_path / "dir-typo"
        (work / "AGENTS.md").mkdir(parents=True)  # a directory where a file belongs
        context = P.build(work)
        assert context.sources == ()
        assert len(context.refused) == 1
        refused = context.refused[0]
        assert refused.status == P.STATUS_REFUSED_NOT_FILE
        assert refused.rel_path.endswith("dir-typo/AGENTS.md")
        assert "not a regular file" in refused.reason

    def test_a_refused_target_is_never_read(self, tmp_path):
        """The refusal is checked before any read: the outside file is unreadable by mode 000."""
        root, workdir = make_workspace(tmp_path)
        outside = write(tmp_path / "secret.md", "## Rules\n- SECRET: must not be read\n")
        outside.chmod(0o000)
        os.symlink(outside, root / "CLAUDE.md")
        try:
            context = P.build(workdir)
            assert [item.status for item in context.refused] == [P.STATUS_REFUSED_ESCAPE]
            assert "SECRET" not in context.block
        finally:
            outside.chmod(0o600)


# --------------------------------------------------------------------------- #
# determinism
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("cap", [8000, 3000, 150, 40])
def test_the_selection_is_reproducible_under_the_cap(tmp_path, cap):
    _root, workdir = make_workspace(tmp_path)
    first = P.build(workdir, max_chars=cap)
    second = P.build(workdir, max_chars=cap)
    assert (first.block, first.fingerprint) == (second.block, second.fingerprint)
    assert first.budgeted_chars <= cap


def test_explain_reports_the_ladder_the_levels_and_the_budget(tmp_path):
    _root, workdir = make_workspace(tmp_path)
    report = P.explain(workdir)
    assert report["ladder"]["checked_in_each_directory"] == list(P.FILENAME_LADDER)
    assert any("closer to the working directory" in rule for rule in report["ladder"]["rules"])
    assert [row["level"] for row in report["levels"]] == [0, 1, 2]
    assert report["budget"]["funding_order"][0] == "rules"
    assert report["budget"]["planned"]["rules"] > 0
    assert "the rules section is funded first" in report["budget"]["truncation_rule"]
    assert report["overrides"][0]["winner"] == "nested/AGENTS.md"
    assert report["fingerprint"] == P.build(workdir).fingerprint
