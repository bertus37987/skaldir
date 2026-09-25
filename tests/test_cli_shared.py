"""One home per CLI helper — every ``*_cli.py`` group rides ``cli_shared``.

The slop sweep found the same plumbing copied per group. The first pass unified
``_share_common_flags`` / ``_runtime_dir`` / ``_dump``; the second finished the job. These
are one implementation each in :mod:`skaldir.cli_shared` now, with the copies they
replaced:

===============================  =======
helper                           copies
===============================  =======
``_product_contract``            9
``_text_mode``                   6
``_dump`` / ``_share_common_flags`` 5 each
``_envelope_mode``               5
``_runtime_dir`` / ``_blocked`` / ``_not_found`` / ``_verification_failed`` / ``_session``  2 each
``_resolve`` / ``_registry_context`` (one body, three homes)  3
``register_contract_entries``    2
``_as_subparsers``               2
===============================  =======

The guard has three edges:

* :func:`test_the_group_rides_the_shared_helper` — the name in the group *is* the one
  implementation (``skaldir.act_cli.dump is skaldir.cli_shared.dump``), so a group cannot
  keep a private copy under the shared name.
* :func:`test_no_group_defines_a_body_the_shared_home_already_has` — an AST comparison of
  every top-level function of every ``*_cli.py`` against the shared home. It compares the
  *body*, not the name, so a copy reappearing under a new name or with different
  formatting still fails.
* :func:`test_duplicated_helpers_stay_within_the_known_residue` — what is still duplicated
  (see :data:`KNOWN_RESIDUE`, each entry with the reason it was left alone) may not grow;
  shrinking it is fine.

The behaviour of the shared helpers is pinned below too — including the promise that a
flag helper never breaks a group (no contract module, no ``add_common_flags``, an
``add_common_flags`` that raises: all three are a no-op, never an exception).
"""

from __future__ import annotations

import argparse
import ast
import json
from collections import defaultdict
from pathlib import Path

import pytest

from skaldir import (
    act_cli,
    adapter_http_cli,
    adapter_mcp_cli,
    api_server_cli,
    cli_shared,
    compose_cli,
    host_cli,
    image_inspect_cli,
    image_pin_cli,
    llm_provider,
    provider_cli,
    provider_config,
    runs_cli,
    security_audit_cli,
    toolbuilder_cli,
    verify_cli,
    voice_cli,
)

SRC = Path(__file__).resolve().parents[1] / "src" / "skaldir"

#: Name -> module object, for the groups this test knows by name.
MODULES = {
    module.__name__.rsplit(".", 1)[-1]: module
    for module in (
        act_cli,
        adapter_http_cli,
        adapter_mcp_cli,
        api_server_cli,
        compose_cli,
        host_cli,
        image_inspect_cli,
        image_pin_cli,
        provider_cli,
        runs_cli,
        security_audit_cli,
        toolbuilder_cli,
        verify_cli,
        voice_cli,
    )
}

#: What each group must take from ``cli_shared`` — the re-use edges of the refactor.
#: ``api_server_cli`` is absent on purpose: its one shared call is written
#: ``cli_shared.as_subparsers(...)``, so there is no imported name to pin.
SHARED: dict[str, set[str]] = {
    "act_cli": {"install_contract_entries", "product_contract", "share_common_flags"},
    "adapter_http_cli": {"registry_from_args"},
    "adapter_mcp_cli": {"dump", "envelope_mode", "runtime_dir", "share_common_flags", "text_mode"},
    "compose_cli": {"registry_from_args", "share_common_flags"},
    "host_cli": {"install_contract_entries", "product_contract"},
    "image_inspect_cli": {"dump", "product_contract", "share_common_flags", "text_mode"},
    "image_pin_cli": {
        "dump",
        "envelope_mode",
        "product_contract",
        "runtime_dir",
        "share_common_flags",
        "text_mode",
    },
    "provider_cli": {"dump", "product_contract"},
    "runs_cli": {"product_contract", "runtime_dir"},
    "security_audit_cli": {
        "dump",
        "envelope_mode",
        "product_contract",
        "share_common_flags",
        "text_mode",
    },
    "toolbuilder_cli": {"registry_from_args"},
    "verify_cli": {
        "blocked",
        "dump",
        "envelope_mode",
        "install_contract_entries",
        "not_found",
        "product_contract",
        "runtime_dir",
        "session_id",
        "share_common_flags",
        "text_mode",
        "verification_failed",
    },
    "voice_cli": {
        "blocked",
        "dump",
        "envelope_mode",
        "not_found",
        "product_contract",
        "session_id",
        "share_common_flags",
        "text_mode",
        "verification_failed",
    },
}

#: Still duplicated between groups, with the reason each was left alone. The guard below
#: fails when a *new* duplicate appears — it does not fail when one of these is unified,
#: so shrinking this list (or emptying it) is always the right direction.
KNOWN_RESIDUE: tuple[frozenset[str], ...] = (
    # The standalone entry points: identical bodies, but each calls its own
    # ``build_parser`` and its own ``EXIT_*`` globals, so a shared runner would need the
    # module passed back in — more machinery than the nine lines it saves.
    frozenset(
        {
            "act_cli.py:main",
            "compose_cli.py:main",
            "host_cli.py:main",
            "image_inspect_cli.py:main",
            "image_pin_cli.py:main",
            "provider_cli.py:main",
            "verify_cli.py:main",
        }
    ),
    frozenset({"runs_cli.py:main", "voice_cli.py:main"}),
    # One-line wrappers, not copies: each re-resolves its own module-global ``_CONTRACT``
    # and bundles its own ``contract_entries()`` into ``install_contract_entries``.
    frozenset({"act_cli.py:register_contract_entries", "host_cli.py:register_contract_entries"}),
    # ``_report`` / ``_emit`` exist in ten and five *different* variants across the groups
    # (each filters its own evidence kinds and prints through its own text renderer).
    # These two pairs happen to be byte-identical; unifying them means a callback API for
    # five lines saved, so they wait for a wave that owns that decision.
    frozenset({"image_pin_cli.py:_report", "voice_cli.py:_report"}),
    frozenset({"image_pin_cli.py:_emit", "verify_cli.py:_emit", "voice_cli.py:_emit"}),
    # Each delegates to its own group's ``build_parser``: the body only looks identical.
    frozenset({"image_inspect_cli.py:register_cli", "image_pin_cli.py:register_cli"}),
    # ``warm_cli.py`` is not part of this block. Its copies are reported, not edited:
    # it must import ``cli_shared.as_subparsers`` (line 432) and build its evidence with
    # its own ``_ev`` — the latter is the literal the contract-snippet generators emit.
    frozenset({"api_server_cli.py:_ev", "warm_cli.py:_ev"}),
)


def _top_level_bodies(source: str) -> list[tuple[str, str]]:
    """``(name, normalised body)`` for every top-level function in ``source``.

    The docstring is dropped and the body is compared as an AST dump, so whitespace,
    comments and docstring edits do not hide a copy — only a different implementation
    is a different body.
    """
    bodies: list[tuple[str, str]] = []
    for node in ast.parse(source).body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = [
            stmt
            for stmt in node.body
            if not (
                isinstance(stmt, ast.Expr)
                and isinstance(stmt.value, ast.Constant)
                and isinstance(stmt.value.value, str)
            )
        ]
        dump = ast.dump(ast.Module(body=body, type_ignores=[]))
        bodies.append((node.name, ast.dump(node.args) + "|" + dump))
    return bodies


def _cli_sources() -> dict[str, str]:
    return {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(SRC.glob("*_cli.py"))
    }


def test_the_group_rides_the_shared_helper() -> None:
    """The name in the group *is* the one implementation, not a copy of it."""

    offenders = [
        f"{module_name}.{name}"
        for module_name, names in SHARED.items()
        for name in sorted(names)
        if getattr(MODULES[module_name], name, None) is not getattr(cli_shared, name, None)
    ]
    assert not offenders, (
        f"{offenders} are not the objects from skaldir.cli_shared — import them instead "
        "of defining a local copy"
    )


def test_the_source_files_agree_with_the_identity_table() -> None:
    """What the modules import from ``cli_shared`` is exactly what the table pins."""

    sources = _cli_sources()
    for module_name, names in SHARED.items():
        source = sources[f"{module_name}.py"]
        imported: set[str] = set()
        for node in ast.parse(source).body:
            if (
                isinstance(node, ast.ImportFrom)
                and node.module == "cli_shared"
                and node.level == 1
            ):
                imported |= {alias.name for alias in node.names}
        assert imported == names, (
            f"{module_name}.py imports {sorted(imported)} from cli_shared, the table says "
            f"{sorted(names)} — keep both sides honest"
        )


def test_no_group_defines_a_body_the_shared_home_already_has() -> None:
    """A module-level copy of a shared helper is exactly the slop that came back."""

    shared_source = (SRC / "cli_shared.py").read_text(encoding="utf-8")
    canonical = {body: name for name, body in _top_level_bodies(shared_source)}
    assert len(canonical) >= len(cli_shared.__all__), "the shared home lost a helper"

    offenders = [
        f"{filename}:{name} == cli_shared.{canonical[body]}"
        for filename, source in _cli_sources().items()
        for name, body in _top_level_bodies(source)
        if body in canonical
    ]
    assert not offenders, (
        f"{offenders} redefine a helper skaldir.cli_shared already owns — import it "
        "instead of copying it"
    )


def test_duplicated_helpers_stay_within_the_known_residue() -> None:
    """No duplicate between the groups that this file does not already name."""

    groups: dict[str, set[str]] = defaultdict(set)
    for filename, source in _cli_sources().items():
        for name, body in _top_level_bodies(source):
            groups[body].add(f"{filename}:{name}")

    found = {frozenset(members) for members in groups.values() if len(members) > 1}
    new = sorted(sorted(group) for group in found - set(KNOWN_RESIDUE))
    assert not new, (
        f"new duplicate helper(s) between the groups: {new} — give it one home in "
        "skaldir.cli_shared, or add it to KNOWN_RESIDUE with the reason it stays"
    )


def test_the_provider_side_has_one_compaction_helper() -> None:
    """``provider_config`` imports ``_compact`` from ``llm_provider`` instead of copying it."""

    assert provider_config._compact is llm_provider._compact


def test_provider_config_keeps_its_own_yaml_dump() -> None:
    """The near-name trap: ``provider_config._dump`` is a *different* helper and stays."""

    assert provider_config._dump({"a": 1}).strip() == "a: 1"


def test_cli_shared_holds_the_canonical_implementations() -> None:
    """The shared home names every helper the groups ride, and each one is callable.

    The five envelope helpers (``client_and_status`` · ``driven`` · ``ev`` · ``human`` ·
    ``report_envelope``) moved here when the goal-run command needed the same plumbing the
    agent-loop group had: one home instead of a second copy.
    """

    assert cli_shared.__all__ == [
        "as_subparsers",
        "blocked",
        "client_and_status",
        "driven",
        "dump",
        "envelope_mode",
        "ev",
        "human",
        "install_contract_entries",
        "not_found",
        "product_contract",
        "registry_from_args",
        "report_envelope",
        "runtime_dir",
        "session_id",
        "share_common_flags",
        "text_mode",
        "verification_failed",
    ]
    assert all(callable(getattr(cli_shared, name)) for name in cli_shared.__all__)


# --------------------------------------------------------------------------- #
# the behaviour of the shared home                                              #
# --------------------------------------------------------------------------- #


def test_product_contract_is_the_cli_contract_module() -> None:
    """What every group used to resolve for itself: ``cli_contract``, or ``None``."""

    from skaldir import cli_contract

    assert cli_shared.product_contract() is cli_contract


def test_share_common_flags_attaches_the_product_flag_vocabulary() -> None:
    """What the helper is for: the three flags ``cli_contract.add_common_flags`` adds."""

    parser = argparse.ArgumentParser(prog="leaf")
    cli_shared.share_common_flags(parser)
    option_strings = {option for action in parser._actions for option in action.option_strings}
    assert {"--format", "--quiet", "--no-color"} <= option_strings


@pytest.mark.parametrize("contract", [None, object()])
def test_share_common_flags_is_a_no_op_without_a_capable_contract(contract, monkeypatch) -> None:
    """No contract module, or one without ``add_common_flags``: a no-op, never a raise."""

    monkeypatch.setattr(cli_shared, "_CONTRACT", contract)
    parser = argparse.ArgumentParser(prog="leaf")
    assert cli_shared.share_common_flags(parser) is None
    assert parser._actions  # untouched


def test_share_common_flags_survives_a_broken_contract(monkeypatch) -> None:
    """A contract whose ``add_common_flags`` raises must not break the group."""

    class Broken:
        @staticmethod
        def add_common_flags(parser: argparse.ArgumentParser) -> None:
            raise RuntimeError("this contract is broken")

    monkeypatch.setattr(cli_shared, "_CONTRACT", Broken)
    assert cli_shared.share_common_flags(argparse.ArgumentParser(prog="leaf")) is None


def test_runtime_dir_prefers_the_flag_and_falls_back_to_the_product_default() -> None:
    """``--runtime`` wins when given; without it the product's default runtime is read.

    Whitespace is *not* stripped — the helper is the byte-exact body the four groups
    used before, and pinning that keeps a later "tidy-up" from changing which directory
    a command points at.
    """

    from skaldir.registry import default_runtime_dir

    assert cli_shared.runtime_dir(argparse.Namespace(runtime="")) == default_runtime_dir()
    assert cli_shared.runtime_dir(argparse.Namespace()) == default_runtime_dir()
    assert cli_shared.runtime_dir(argparse.Namespace(runtime="~/somewhere")) == Path(
        "~/somewhere"
    ).expanduser()
    assert cli_shared.runtime_dir(argparse.Namespace(runtime="  ")) == Path("  ")


def test_registry_from_args_asks_the_one_registry() -> None:
    """The three flags become the one resolution ``cli.py`` uses (§1.2) — nothing else."""

    from skaldir import registry

    seen: dict = {}

    def fake_resolve(**kwargs):
        seen.update(kwargs)
        return "a registry context"

    original = registry.resolve_registry
    registry.resolve_registry = fake_resolve
    try:
        bare = argparse.Namespace(runtime="", project="", db=None)
        assert cli_shared.registry_from_args(bare) == "a registry context"
        assert seen == {"runtime": None, "project": None, "db": None}
        given = argparse.Namespace(runtime="/tmp/rt", project=None, db="/tmp/db")
        assert cli_shared.registry_from_args(given) == "a registry context"
        assert seen == {"runtime": "/tmp/rt", "project": None, "db": "/tmp/db"}
    finally:
        registry.resolve_registry = original


def test_text_mode_is_explicit_only() -> None:
    """Human text is asked for, never assumed: ``--text`` or ``--format text``."""

    assert cli_shared.text_mode(argparse.Namespace(text=True)) is True
    assert cli_shared.text_mode(argparse.Namespace(format="TEXT")) is True
    assert cli_shared.text_mode(argparse.Namespace(format=" text ")) is True
    assert cli_shared.text_mode(argparse.Namespace(text=False, format="json")) is False
    assert cli_shared.text_mode(argparse.Namespace(format="jsonl")) is False
    assert cli_shared.text_mode(argparse.Namespace()) is False


def test_session_id_reads_the_flag_or_gives_an_empty_string() -> None:
    """``--session`` as text; an absent or ``None`` session is ``""``, never ``"None"``."""

    assert cli_shared.session_id(argparse.Namespace(session="s-42")) == "s-42"
    assert cli_shared.session_id(argparse.Namespace(session=None)) == ""
    assert cli_shared.session_id(argparse.Namespace()) == ""


@pytest.mark.parametrize("contract", [None, object()])
def test_envelope_mode_is_false_without_a_capable_contract(contract, monkeypatch) -> None:
    """No contract, or one without ``current()``: not in envelope mode, never a raise."""

    monkeypatch.setattr(cli_shared, "_CONTRACT", contract)
    assert cli_shared.envelope_mode() is False


def test_envelope_mode_follows_the_driver_context(monkeypatch) -> None:
    """True exactly while the product driver runs us in JSON mode."""

    class Contract:
        @staticmethod
        def current():
            return argparse.Namespace(json_mode=True)

    monkeypatch.setattr(cli_shared, "_CONTRACT", Contract)
    assert cli_shared.envelope_mode() is True

    class Quiet:
        @staticmethod
        def current():
            return argparse.Namespace(json_mode=False)

    monkeypatch.setattr(cli_shared, "_CONTRACT", Quiet)
    assert cli_shared.envelope_mode() is False

    class Broken:
        @staticmethod
        def current():
            raise RuntimeError("no context")

    monkeypatch.setattr(cli_shared, "_CONTRACT", Broken)
    assert cli_shared.envelope_mode() is False


def test_dump_prints_the_one_json_document(capsys) -> None:
    """One payload, one document: sorted keys, indentation, no ASCII escaping."""

    cli_shared.dump({"b": 1, "a": "é"})
    assert capsys.readouterr().out == json.dumps(
        {"b": 1, "a": "é"}, ensure_ascii=False, indent=2, sort_keys=True, default=str
    ) + "\n"


def test_install_contract_entries_is_additive_and_idempotent() -> None:
    """A group hands its ``COMMANDS`` entries over; an existing entry is never overwritten."""

    entries = {"probe.one": {"kind": "x"}, "probe.two": {"kind": "y"}}

    class Contract:
        COMMANDS = {"probe.two": {"kind": "kept"}}

    contract = Contract()
    assert cli_shared.install_contract_entries(entries, contract) == ["probe.one"]
    assert contract.COMMANDS["probe.two"] == {"kind": "kept"}
    assert cli_shared.install_contract_entries(entries, contract) == []
    assert cli_shared.install_contract_entries({}, contract) == []
    assert cli_shared.install_contract_entries(entries, None) == []


def test_the_refusal_helpers_pass_through_the_contract(monkeypatch) -> None:
    """``blocked`` / ``not_found`` / ``verification_failed`` reach the contract verbatim."""

    calls: list[tuple] = []

    class Contract:
        @staticmethod
        def blocked(message, *, hint="", next_command="", reason="", details=None):
            calls.append(("blocked", message, hint, next_command, reason, details))
            return 3

        @staticmethod
        def not_found(message, *, hint="", details=None):
            calls.append(("not_found", message, hint, details))
            return 5

        @staticmethod
        def verification_failed(message, *, hint="", details=None):
            calls.append(("verification_failed", message, hint, details))
            return 2

    monkeypatch.setattr(cli_shared, "_CONTRACT", Contract)
    assert cli_shared.blocked("nope", hint="h", next_command="c", reason="r", details={"a": 1}) == 3
    assert cli_shared.not_found("gone") == 5
    assert cli_shared.verification_failed("unproven") == 2
    assert calls == [
        ("blocked", "nope", "h", "c", "r", {"a": 1}),
        ("not_found", "gone", "", None),
        ("verification_failed", "unproven", "", None),
    ]


@pytest.mark.parametrize("contract", [None, object()])
def test_the_refusal_helpers_fall_back_to_the_contract_codes(contract, monkeypatch) -> None:
    """Standalone — no contract, or one without the helper — the group still exits right."""

    monkeypatch.setattr(cli_shared, "_CONTRACT", contract)
    assert cli_shared.blocked("nope") == cli_shared.EXIT_BLOCKED
    assert cli_shared.not_found("gone") == cli_shared.EXIT_NOT_FOUND
    assert cli_shared.verification_failed("unproven") == cli_shared.EXIT_VERIFICATION_FAILED


def test_as_subparsers_accepts_the_action_or_a_parser_that_has_one() -> None:
    """``register_cli`` is handed either — and a parser without subparsers is a TypeError."""

    parser = argparse.ArgumentParser(prog="skaldir")
    sub = parser.add_subparsers(dest="command")
    assert cli_shared.as_subparsers(sub) is sub
    assert cli_shared.as_subparsers(parser) is sub
    with pytest.raises(TypeError):
        cli_shared.as_subparsers(argparse.ArgumentParser(prog="bare"))


def test_api_server_cli_rides_the_shared_subparsers_adapter() -> None:
    """``api_server_cli`` must not carry its own ``_as_subparsers`` again."""

    source = (SRC / "api_server_cli.py").read_text(encoding="utf-8")
    assert "_as_subparsers" not in source
    assert "cli_shared.as_subparsers(" in source
