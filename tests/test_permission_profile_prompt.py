"""The prompt half of non-drift: the real composer renders the block from the same object.

``prompt_block()`` is only useful if it reaches the agent through the *actual* composer, so this
file builds the prompt with ``skaldir.prompt_builder`` and checks that the hash in the composed
text is the hash in the sandbox argv.

The section itself is delivered as a **hook** (the prompt wave owns ``SECTION_ORDER``): these
tests install the spec and the provider in-process, which is exactly the edit the composer needs
— so the integration is proven here rather than described.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from skaldir import permission_profile as P
from skaldir import prompt_builder as PB


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    (root / ".git").mkdir(parents=True)
    return root


def _install(monkeypatch: pytest.MonkeyPatch, budget: int = P.PROMPT_SECTION_BUDGET_CHARS) -> None:
    """What the prompt wave has to add: one spec and one provider entry."""
    spec = PB.SectionSpec(
        P.PROMPT_SECTION_NAME,
        P.PROMPT_SECTION_TITLE,
        budget,
        "the limits this run operates under, as the sandbox enforces them",
    )
    monkeypatch.setattr(PB, "SECTION_ORDER", (*PB.SECTION_ORDER, P.PROMPT_SECTION_NAME))
    monkeypatch.setattr(PB, "SECTION_SPECS", {**PB.SECTION_SPECS, P.PROMPT_SECTION_NAME: spec})


def _assemble(tmp_path: Path, workspace: Path, budget: int = P.PROMPT_SECTION_BUDGET_CHARS):
    runtime = tmp_path / "rt"
    runtime.mkdir(exist_ok=True)
    ctx = PB.context_for(
        project=workspace,
        runtime=runtime,
        enabled=(P.PROMPT_SECTION_NAME,),
        total_budget_tokens=4096,
    )
    providers = {
        P.PROMPT_SECTION_NAME: P.prompt_provider(runtime=runtime, workspace=workspace),
    }
    return PB.build_prompt(ctx, providers=providers)


def test_the_composer_renders_the_block_verbatim(
    tmp_path: Path, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install(monkeypatch)
    assembly = _assemble(tmp_path, workspace)
    section = assembly.section(P.PROMPT_SECTION_NAME)
    assert section is not None and section.present
    assert section.truncated is False, "the agent must be told the whole policy, not a prefix"
    assert section.text == P.default_profile(workspace).prompt_block()
    assert section.source == "skaldir.permission_profile:default"
    assert section.title == "Permissions"
    assert "## Permissions" in assembly.text


def test_the_composed_prompt_carries_the_sandbox_hash(
    tmp_path: Path, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The property in one line: the bytes the model reads name the policy it will run under."""
    _install(monkeypatch)
    profile = P.default_profile(workspace)
    assembly = _assemble(tmp_path, workspace)
    in_prompt = P.permission_profile_hash_from_prompt(assembly.text)
    in_argv = P.permission_profile_hash_from_argv(profile.sandbox_argv("true"))
    assert in_prompt == in_argv == profile.identity_sha256


def test_a_changed_profile_changes_the_composed_prompt(
    tmp_path: Path, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install(monkeypatch)
    runtime = tmp_path / "rt"
    runtime.mkdir()
    before = _assemble(tmp_path, workspace)
    changed = P.default_profile(workspace).with_changes(
        network="on", writable_paths=(str(workspace), "/srv/out")
    )
    P.save_profile(changed, runtime)
    after = _assemble(tmp_path, workspace)
    assert before.fingerprint != after.fingerprint, (
        "a policy change must move the prompt fingerprint — otherwise a run can be replayed "
        "under limits it was never told about"
    )
    assert P.permission_profile_hash_from_prompt(before.text) != (
        P.permission_profile_hash_from_prompt(after.text)
    )


def test_non_drift_survives_a_truncated_section(
    tmp_path: Path, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even clipped at an absurd budget the hash line survives, so the claim still holds."""
    _install(monkeypatch, budget=120)
    assembly = _assemble(tmp_path, workspace, budget=120)
    section = assembly.section(P.PROMPT_SECTION_NAME)
    assert section is not None and section.truncated is True
    assert "truncated" in section.text
    profile = P.default_profile(workspace)
    assert P.permission_profile_hash_from_prompt(assembly.text) == profile.identity_sha256


def test_the_provider_works_without_the_composer_importable(
    tmp_path: Path, workspace: Path
) -> None:
    """The block is the deliverable; a build without the composer still gets an object."""
    provider = P.prompt_provider(runtime=tmp_path / "rt", workspace=workspace)
    section = provider()
    assert section.text == P.default_profile(workspace).prompt_block()
    assert section.source == "skaldir.permission_profile:default"
    assert section.detail.startswith("profile default@sha256:")


def test_the_section_budget_is_the_literal_the_composer_uses(workspace: Path) -> None:
    """The composer declares the budget as a literal (its sibling imports are lazy).

    So the literal and this constant have to agree, and the block has to fit — a section budget
    smaller than the policy would mean the agent is told a prefix of its limits.
    """
    assert P.PROMPT_SECTION_BUDGET_CHARS == 1900, (
        "the budget moved from 1200 to 1900 in the security wave: the block now also carries the "
        "environment policy, the egress policy, the syscall classes and the process cap"
    )
    block = P.default_profile(workspace).prompt_block()
    assert len(block) < P.PROMPT_SECTION_BUDGET_CHARS, (
        f"the permissions block is {len(block)} chars and its budget is "
        f"{P.PROMPT_SECTION_BUDGET_CHARS}: the agent would be told a prefix of its limits"
    )
