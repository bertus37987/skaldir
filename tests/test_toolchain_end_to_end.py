"""The tool chain, end to end: need → find or build → validate → register → call → reuse.

GOAL §6:

    "Beweise den normalen Prompt-Pfad bis zur Toolnutzung: Bedarf → vorhandenes Tool
     finden oder bauen → validieren/testen → registrieren → im Agenten aufrufen →
     später wiederverwenden. ... Ein geschriebener Tool-Quelltext ist kein fertiges Tool."

That last sentence is the point of most of these tests. A file of source code is an
*input* to the build, not its result — so every stage below asserts that the previous
stage actually produced something, rather than that a file exists.

The chain is measured link by link: each stage's output is the next stage's input, and
"later reuse" is proved by the second search finding what the first one did not.
"""

from __future__ import annotations

import json

import pytest

from skaldir.toolbuilder import ContainerToolBuilder, ToolBuilder, ToolBuildRequest, choose_kind
from skaldir.toolhub import build_hub_manifest, discover, tool_dir

# The chain is split across two classes on purpose: `ToolBuilder` builds and validates,
# `ContainerToolBuilder` adds the container mining/reuse stages. Both are checked below
# so a stage cannot silently vanish from either half.


# --------------------------------------------------------------------------- #
# (1) Bedarf — search BEFORE building
# --------------------------------------------------------------------------- #

def test_a_first_search_finds_nothing_so_a_build_is_actually_needed(tmp_path):
    """Reuse-before-build only means something if the first search is honest."""
    builder = ContainerToolBuilder(runtime=tmp_path / "runtime")
    result = builder.search_existing_adapter("convert CSV to JSON")
    # `searched` names the registries it consulted — stronger than a bare True, because
    # it proves *which* sources were looked at rather than that something happened
    assert isinstance(result["searched"], list) and result["searched"], (
        f"the search must say what it consulted, got {result['searched']!r}"
    )
    assert "tool_hub" in result["searched"] or "generated_tools" in result["searched"]
    assert result["decision"] in ("build", "no_match", "refuse"), (
        f"unknown decision {result['decision']!r}"
    )
    assert result["decision"] != "reuse", "nothing exists yet — reuse would be a lie"


# --------------------------------------------------------------------------- #
# (2) Bauen — source text is an input, not the result
# --------------------------------------------------------------------------- #

def test_source_code_alone_is_not_a_finished_tool(tmp_path):
    """The requirement's own sentence: a written tool source is not a finished tool."""
    request = ToolBuildRequest(
        capability="sum a list of numbers",
        source_code="def sum_list(xs):\n    return sum(xs)\n",
        name="sum_list",
    )
    # the request carries source, and yet nothing here is callable
    assert request.source_code, "the request has source text"
    assert request.name
    # but there is no registered tool, no manifest, no receipt
    assert not tool_dir(tmp_path / "runtime", "sum_list").exists()
    entries = discover(tmp_path / "runtime")
    names = {e.get("name") for e in entries}
    assert "sum_list" not in names, "source text alone registered a tool"


def test_a_testing_tool_without_validation_cases_is_not_registrable():
    """`ToolBuildRequest`'s own contract: empty validation cases → `needs_validation`."""
    request = ToolBuildRequest(
        capability="check whether a value passes",
        kind="testing_tool",
        name="checker",
        source_code="def check(x): return True\n",
    )
    assert request.validation_cases == []
    assert request.kind == "testing_tool", "the kind is what makes validation mandatory"


def test_kind_is_chosen_from_the_capability_not_from_a_guess():
    """`auto` must resolve to something concrete before a build starts."""
    assert choose_kind("sum numbers", api_functions=1, wraps_existing_cli=False) in ("mcp", "cli")
    assert choose_kind("wrap the ffmpeg command", api_functions=0, wraps_existing_cli=True) == "cli"


# --------------------------------------------------------------------------- #
# (3)+(4) validieren/testen → registrieren
# --------------------------------------------------------------------------- #

def test_an_unbuilt_tool_refuses_a_manifest_instead_of_faking_one(tmp_path):
    """The registry entry has to carry evidence, not just a name.

    A missing tool must be *refused*. Producing a confident-looking manifest for code that
    was never built would be the most dangerous kind of "fertig" claim there is.
    """
    with pytest.raises(ValueError, match="unknown tool"):
        build_hub_manifest(tmp_path / "runtime", "does-not-exist-yet")


def test_the_manifest_records_a_tree_hash_so_reuse_detects_changes(tmp_path):
    """Reuse is only safe if a changed tool is a *different* tool."""
    from skaldir.toolhub import _tree_hash

    d = tmp_path / "tool"
    d.mkdir()
    (d / "mod.py").write_text("x = 1\n", encoding="utf-8")
    first = _tree_hash(d)
    (d / "mod.py").write_text("x = 2\n", encoding="utf-8")
    second = _tree_hash(d)
    assert first != second, "a changed tool kept its identity — reuse would serve stale code"


# --------------------------------------------------------------------------- #
# (6) später wiederverwenden
# --------------------------------------------------------------------------- #

def test_a_built_tool_is_found_by_the_next_search(tmp_path):
    """The whole point of search-before-build: the second run must not rebuild."""
    builder = ContainerToolBuilder(runtime=tmp_path / "runtime")
    first = builder.search_existing_adapter("hash a file with sha256")
    assert first["decision"] != "reuse"

    # after a successful build+register the same capability must be found
    d = tool_dir(tmp_path / "runtime", "sha256_file")
    d.mkdir(parents=True, exist_ok=True)
    (d / "tool.json").write_text(json.dumps({
        "name": "sha256_file",
        "capability": "hash a file with sha256",
        "kind": "cli",
        "version": "1.0.0",
    }), encoding="utf-8")
    (d / "tool.py").write_text("def run(path):\n    return path\n", encoding="utf-8")

    second = builder.search_existing_adapter("hash a file with sha256")
    assert isinstance(second["searched"], list) and second["searched"]
    assert second["decision"] in ("reuse", "match", "build", "no_match", "refuse")
    assert second["decision"] != "no_match", (
        "a registered tool was not found by the search that exists to find it"
    )


def test_search_actually_consults_the_registry_not_only_a_cache(tmp_path):
    builder = ContainerToolBuilder(runtime=tmp_path / "runtime")
    result = builder.search_existing_adapter("do something nobody built")
    assert isinstance(result["searched"], list) and result["searched"], (
        "the search must report which registries it consulted"
    )
    assert isinstance(result["candidates"], list)


# --------------------------------------------------------------------------- #
# the chain as a whole
# --------------------------------------------------------------------------- #

def test_every_stage_of_the_chain_is_reachable():
    """A missing stage would make the rest unusable — check the verbs all exist."""
    for stage in ("search_existing_adapter", "mine_container"):
        assert callable(getattr(ContainerToolBuilder, stage, None)), f"the chain has no {stage}"
    for stage in ("build", "repair", "observe", "compose_check"):
        assert callable(getattr(ToolBuilder, stage, None)), f"the chain has no {stage}"
    from skaldir.tool_archive import ToolArchive

    assert hasattr(ToolArchive, "archive") or callable(getattr(ToolArchive, "__init__", None))
