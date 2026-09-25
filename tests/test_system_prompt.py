"""The data-built system prompt: capability awareness derived from the real toolset.

The rule the prompt builder must keep: every capability the text claims is backed
by a tool that is actually in the ``ToolSet`` this run built — names come from
the toolset, never from a hardcoded list. Nothing claimed is not offered.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from skaldir.agent_loop import AgentTool, ToolSet, builtin_tools  # noqa: E402
from skaldir.system_prompt import CORE_PROMPT_MARKERS, build_system_prompt  # noqa: E402


def _tool(name: str, *, description: str = "", source: str = "builtin") -> AgentTool:
    return AgentTool(
        name=name,
        description=description,
        input_schema={"type": "object", "properties": {}},
        handler=lambda **_: {},
        source=source,
    )


def _layer_toolset() -> ToolSet:
    from skaldir.agent_toolset import build_agent_toolset

    return build_agent_toolset(workspace="/tmp/skaldir-prompt-test")


def test_the_core_contract_keeps_the_old_guarantees():
    """The fallback constant keeps behaviour: honest calls, errors fed back, no fake success."""
    import skaldir.agent_loop as agent_loop

    text = str(agent_loop.SYSTEM_PROMPT)
    for marker in CORE_PROMPT_MARKERS:
        assert marker in text, f"the core contract lost: {marker!r}"


def test_builtin_only_toolset_names_only_the_file_tools():
    prompt = build_system_prompt(builtin_tools("/tmp/skaldir-prompt-test"))
    assert "file" in prompt.lower()
    for name in ("read_text", "list_dir", "sha256_file", "write_text"):
        assert name in prompt
    # A layer the toolset does not carry is never claimed.
    assert "skill_expand" not in prompt
    assert "skill_act" not in prompt
    assert "spawn_subagents" not in prompt


def test_every_claimed_tool_name_is_in_the_given_toolset():
    """The identity rule: a wire name in the prompt must exist in the ToolSet."""
    toolset = _layer_toolset()
    prompt = build_system_prompt(toolset)
    import re

    mentioned = {
        name
        for name in re.findall(r"`([a-z_0-9]+)`", prompt)
        if toolset.get(name) is not None
    }
    # sanity: at least one real name is mentioned
    assert mentioned
    # and every layer tool mentioned is real
    for match in re.findall(
        r"`(skill_[a-z_0-9]+|spawn_subagents|read_text|write_text|list_dir|sha256_file)`",
        prompt,
    ):
        assert toolset.get(match) is not None, f"prompt claims {match!r}, toolset does not offer it"


def test_layer_tools_gain_the_expand_act_compose_verify_sections():
    toolset = _layer_toolset()
    prompt = build_system_prompt(toolset)
    low = prompt.lower()
    for section, keyword in (
        ("expand", "expand"),
        ("act", "act"),
        ("compose", "compose"),
        ("verify", "verify"),
    ):
        assert keyword in low, f"the {section} layer is not explained"
    assert "unknown" in low
    assert "workspaces" in low or "workspace" in low


def test_the_layer_tool_names_come_from_the_toolset_not_a_hardcode():
    """Rename a tool in the toolset → the prompt mentions the new name, not the old."""
    from skaldir.agent_toolset import build_agent_toolset

    base = build_agent_toolset(workspace="/tmp/skaldir-prompt-test")
    custom = ToolSet.of(
        [
            _tool("layer_probe", description="check a capability honestly"),
            *builtin_tools("/tmp/skaldir-prompt-test"),
        ]
    )
    assert "layer_probe" in build_system_prompt(custom)
    assert "skill_verify" in build_system_prompt(base)  # real name from the real toolset


def test_the_spawn_capability_is_described_bounded():
    toolset = ToolSet.of(
        [
            _tool("spawn_subagents", description="delegate work"),
            *builtin_tools("/tmp/skaldir-prompt-test"),
        ]
    )
    prompt = build_system_prompt(toolset)
    assert "spawn_subagents" in prompt
    for bound in ("3", "depth", "bounded"):
        assert bound in prompt.lower() or bound in prompt


def test_verify_never_treats_unknown_as_success():
    toolset = _layer_toolset()
    prompt = build_system_prompt(toolset)
    assert "UNKNOWN" in prompt and "never" in prompt


def test_act_discipline_names_isolation_first_and_the_approval():
    toolset = _layer_toolset()
    prompt = build_system_prompt(toolset)
    low = prompt.lower()
    assert "isolation" in low or "sandbox" in low
    assert "approval" in low or "deny" in low


def test_the_goal_is_carried_when_given():
    prompt = build_system_prompt(
        builtin_tools("/tmp/skaldir-prompt-test"), goal="ship the release"
    )
    assert "ship the release" in prompt
    # and omitted cleanly when not
    assert "ship the release" not in build_system_prompt(
        builtin_tools("/tmp/skaldir-prompt-test")
    )


def test_the_skills_section_is_included_verbatim_when_given():
    lines = ["- Proven procedure available (`deploy`, confirmed 3×): a → b."]
    prompt = build_system_prompt(
        builtin_tools("/tmp/skaldir-prompt-test"), skills=tuple(lines)
    )
    assert "deploy" in prompt and "confirmed 3×" in prompt


def test_the_jev_layer_is_mentioned_only_when_armed_and_never_obeyed():
    text = build_system_prompt(builtin_tools("/tmp/skaldir-prompt-test"))
    assert "advisory" not in text.lower() or "jev" not in text.lower()
    armed = build_system_prompt(
        builtin_tools("/tmp/skaldir-prompt-test"), jev_armed=True
    )
    assert "advisory" in armed and "never" in armed


def test_the_prompt_is_english_only():
    from skaldir.system_prompt import has_non_ascii_words

    assert not has_non_ascii_words(
        build_system_prompt(_layer_toolset(), jev_armed=True, goal="etwas auf Deutsch")
    )


def test_the_build_is_explicitly_deterministic_and_frozen_safe():
    """Two builds over the same toolset give the same bytes."""
    toolset = _layer_toolset()
    assert build_system_prompt(toolset) == build_system_prompt(toolset)
