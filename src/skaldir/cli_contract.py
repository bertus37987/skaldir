"""The CLI contract: one envelope, stable exit codes, one self-description.

This module is the machine-readable half of ``skaldir`` (MVP/02-cli-contract.md §4).
Three ideas, one implementation each:

1. **One envelope** for every command — ``{schema_version, ok, command, exit_code,
   data, error, evidence, warnings, next_actions}``. :func:`run_command` is the only
   emitter; human output stays the default, ``--json`` switches to the envelope and
   keeps stdout pure JSON (human lines move to stderr).
2. **One exit-code set** — ``0`` OK, ``1`` usage, ``2`` verification failed,
   ``3`` blocked, ``4`` needs credentials, ``5`` not found, ``6`` conflict,
   ``7`` internal, ``8`` unknown/timeout. Named constants, no bare numbers at the
   call sites.
3. **One self-description** — :func:`dump` walks ``build_parser()`` and returns every
   command with its args and a JSON Schema for its ``data`` payload, so a GUI renders
   forms and tables instead of hardcoding 68 output formats.

Rules this module enforces rather than documents:

* A success envelope without evidence is a contract violation and is downgraded to
  exit ``7`` (:func:`build_envelope`) — "no command reports success it cannot back".
* ``--json`` never mixes text and JSON on stdout.
* ``selftest`` reports a layer that is not wired as ``ok: false`` with a
  ``blocked_reason`` — never as reachable.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
import time
import traceback
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from . import __version__
from .env import getenv

# --------------------------------------------------------------------------- #
# exit codes (§4.3) — one meaning each
# --------------------------------------------------------------------------- #

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_VERIFICATION_FAILED = 2
EXIT_BLOCKED = 3
EXIT_NEEDS_CREDENTIALS = 4
EXIT_NOT_FOUND = 5
EXIT_CONFLICT = 6
EXIT_INTERNAL = 7
EXIT_UNKNOWN = 8

EXIT_NAMES: dict[int, str] = {
    EXIT_OK: "OK",
    EXIT_USAGE: "USAGE",
    EXIT_VERIFICATION_FAILED: "VERIFICATION_FAILED",
    EXIT_BLOCKED: "BLOCKED",
    EXIT_NEEDS_CREDENTIALS: "NEEDS_CREDENTIALS",
    EXIT_NOT_FOUND: "NOT_FOUND",
    EXIT_CONFLICT: "CONFLICT",
    EXIT_INTERNAL: "INTERNAL",
    EXIT_UNKNOWN: "UNKNOWN",
}

#: The stable ``error.code`` vocabulary (§4.2). One code per exit code.
EXIT_ERROR_CODES: dict[int, str] = {
    EXIT_OK: "ok",
    EXIT_USAGE: "usage",
    EXIT_VERIFICATION_FAILED: "verification_failed",
    EXIT_BLOCKED: "blocked",
    EXIT_NEEDS_CREDENTIALS: "needs_credentials",
    EXIT_NOT_FOUND: "not_found",
    EXIT_CONFLICT: "conflict",
    EXIT_INTERNAL: "internal",
    EXIT_UNKNOWN: "unknown",
}

ERROR_CODES: tuple[str, ...] = tuple(
    EXIT_ERROR_CODES[code] for code in sorted(EXIT_ERROR_CODES) if code != EXIT_OK
)

SCHEMA_VERSION = "1.0"

EVIDENCE_KINDS = ("code", "test", "file", "run", "verification")


def exit_style(code: int, message: str, *, hint: str = "", retryable: bool = False,
               details: dict | None = None) -> CommandError:
    """Build the error a handler returns through :func:`fail`."""
    return CommandError(
        code=code, message=message, hint=hint, retryable=retryable, details=dict(details or {})
    )


class CommandError(Exception):
    """A command's own verdict: exit code + the stable error record."""

    def __init__(
        self,
        code: int,
        message: str,
        *,
        hint: str = "",
        retryable: bool = False,
        details: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.code = int(code)
        self.message = str(message)
        self.hint = str(hint)
        self.retryable = bool(retryable)
        self.details = dict(details or {})

    def as_dict(self) -> dict:
        return {
            "code": EXIT_ERROR_CODES.get(self.code, "internal"),
            "message": self.message,
            "hint": self.hint or None,
            "retryable": self.retryable,
            "details": self.details,
        }


# --------------------------------------------------------------------------- #
# the command inventory (self-description, §4.5)
# --------------------------------------------------------------------------- #

_KERNEL = "kernel"

NO_SCHEMA: dict = {
    "type": "null",
    "description": "no structured payload yet — the command's --json envelope reports "
    "data: null and the human text moves to stderr",
}


@dataclass(frozen=True)
class Contract:
    """One command's machine-readable contract."""

    command: str
    layer: str
    description: str = ""
    exit_codes: tuple[int, ...] = (EXIT_OK, EXIT_USAGE, EXIT_INTERNAL)
    mutates: bool = False
    requires_credentials: bool = False
    data_schema: dict | None = None
    evidence: tuple[dict, ...] = ()
    #: mirror the payload's own keys into the envelope's top level (pre-contract
    #: consumers that already read them; see the module note in the CLI report)
    compat_top_level_keys: bool = False
    notes: str = ""

    def as_dict(self) -> dict:
        return {
            "command": self.command,
            "layer": self.layer,
            "description": self.description,
            "exit_codes": sorted(self.exit_codes),
            "mutates": self.mutates,
            "requires_credentials": self.requires_credentials,
            "data_available": bool(self.data_schema) and self.data_schema != NO_SCHEMA,
            "data_schema": self.data_schema or NO_SCHEMA,
            "default_evidence": list(self.evidence),
            "compat_top_level_keys": self.compat_top_level_keys,
            "notes": self.notes,
        }


def _ev(ref: str, kind: str = "code", note: str = "") -> dict:
    return {"ref": ref, "kind": kind, "note": note}


@dataclass(frozen=True)
class CommandSpec:
    """The compact table entry behind every :class:`Contract`."""

    layer: str
    evidence: tuple[dict, ...]
    description: str = ""
    exit_codes: tuple[int, ...] = (EXIT_OK, EXIT_USAGE, EXIT_INTERNAL)
    mutates: bool = False
    requires_credentials: bool = False
    data_schema: dict | None = None
    compat_top_level_keys: bool = False
    notes: str = ""


_C = CommandSpec

#: command path (as ``build_parser()`` spells it) → its contract.
#: The invariant test ``test_cli_contract_dump.py`` fails when a parser leaf is
#: missing here, so a new command cannot ship without a contract.
COMMANDS: dict[str, CommandSpec] = {
    # -- legacy / utility (kept, not grown: §2.4) --------------------------- #
    "init": _C("kernel", (_ev("src/skaldir/cli.py:92"),), "create .godsv/ with a pact + ledger",
               mutates=True, exit_codes=(0, 1, 7),
               notes="an existing pact exits 1, not the designed 6 (conflict): "
                     "tests/test_demo_cli.py::test_init_refuses_overwrite pins 1"),
    "pact-show": _C("kernel", (_ev("src/skaldir/pact.py"),), "print the pact",
                    exit_codes=(0, 1, 5, 7),
                    notes="exit 5 (not_found) when there is no .godsv/pact.yaml — an "
                          "installed wheel starts empty; the crash-exit 7 it used to "
                          "report was a missing-file traceback, not an internal error"),
    "ledger-show": _C("kernel", (_ev("src/skaldir/ledger.py"),), "print the rendered ledger",
                      exit_codes=(0, 1, 5, 7),
                      notes="exit 5 (not_found) when there is no .godsv/ledger.yaml"),
    "demo": _C("kernel", (_ev("src/skaldir/demo.py"),), "deterministic V0 pipeline demo",
               mutates=True),
    "eval": _C("VERIFY", (_ev("src/skaldir/evalharness.py:358"),),
               "run the in-process eval episodes (not real-world verification)",
               exit_codes=(0, 2, 7),
               data_schema={
                   "type": "object",
                   "properties": {
                       "episodes": {"type": "array", "items": {
                           "type": "object",
                           "properties": {"name": {"type": "string"},
                                          "passed": {"type": "boolean"},
                                          "hints": {"type": "integer"},
                                          "signals": {"type": "array",
                                                      "items": {"type": "string"}}},
                           "required": ["name", "passed"]}},
                       "passed": {"type": "integer"},
                       "total": {"type": "integer"},
                       "scope": {"type": "string"},
                       "report": {"type": "string"},
                   },
                   "required": ["episodes", "passed", "total", "scope"],
               },
               notes="exit 2 when any episode fails; in-process, never a PASS authority"),
    "scan": _C("kernel", (_ev("src/skaldir/secretscan.py"),), "scan a file (or -) for secrets",
               exit_codes=(0, 1, 5, 7),
               data_schema={"type": "object",
                            "properties": {
                                "findings": {"type": "array"},
                                "count": {"type": "integer"},
                                "path": {"type": "string"},
                            },
                            "required": ["findings", "count"]},
               notes="findings exit 1, not the designed 2 (verification_failed): "
                     "tests/test_demo_cli.py::test_scan_command pins 1"),
    "prompts.list": _C("kernel", (_ev("src/skaldir/prompts.py"),),
                       "list prompts and check required ids", exit_codes=(0, 5, 7)),
    "tools-hub": _C("EXPAND", (_ev("src/skaldir/toolhub.py:129"),),
                    "discover tools/skills/MCP servers next to the runtime",
                    data_schema={"type": "array", "items": {"type": "object"},
                                 "description": "discovered hub entries (unchanged legacy shape)"}),
    # -- PERSIST (knowledge) ------------------------------------------------ #
    "memory-stats": _C("PERSIST", (_ev("src/skaldir/incidents.py"), _ev("src/skaldir/notes.py"),),
                       "incident/note counts of a memory db",
                       data_schema={"type": "object",
                                    "properties": {"incidents": {"type": "integer"},
                                                   "notes": {"type": "integer"},
                                                   "by_status": {"type": "object"},
                                                   "db": {"type": "string"}},
                                    "required": ["incidents", "notes", "db"]},
                       notes="exit 5 (not_found) is planned for a missing db; today exit 1 "
                             "because tests/test_cli_runtime_paths.py pins it"),
    "memory-search": _C("PERSIST", (_ev("src/skaldir/retrieval.py"),), "evidence view over memory",
                        data_schema={"type": "object",
                                     "properties": {"evidence": {"type": "array"},
                                                    "count": {"type": "integer"},
                                                    "db": {"type": "string"}},
                                     "required": ["evidence", "count"]},
                        notes="an empty result is data.evidence=[] with exit 0; a missing db "
                              "exits 1 (see memory-stats)"),
    "memory-audit": _C("PERSIST", (_ev("src/skaldir/memory.py"),), "event duplicates + retention",
                       data_schema={"type": "object",
                                    "properties": {"total": {"type": "integer"},
                                                   "sessions": {"type": "integer"},
                                                   "bytes_on_disk": {"type": "integer"},
                                                   "over_age": {"type": "integer"},
                                                   "duplicate_groups": {"type": "array"},
                                                   "duplicate_rows": {"type": "integer"},
                                                   "enforcement_gaps": {"type": "array"},
                                                   "schema_axes": {"type": "object"}},
                                    "required": ["total", "sessions", "duplicate_groups"]}),
    "memory-retrieval-check": _C("PERSIST", (_ev("src/skaldir/memory.py"),),
                                 "retrieve → applicability → use/reject"),
    "nightshift": _C("PERSIST", (_ev("src/skaldir/nightshift.py"),), "consolidate a session",
                     mutates=True),
    "metrics": _C("PERSIST", (_ev("src/skaldir/metrics.py"),), "operational counters",
                  data_schema={"type": "object", "additionalProperties": True}),
    "skills.list": _C("COMPOSE", (_ev("src/skaldir/skills.py"),), "list skill candidates"),
    "skills.install": _C("COMPOSE", (_ev("src/skaldir/skill_install.py"),),
                          "install a builtin skill or from a GitHub repo",
                          mutates=True, exit_codes=(0, 5, 7),
                          notes="builtin names are package data (no network); GitHub "
                                "installs fetch codeload tar.gz/HEAD, only md/json/yaml/"
                                "toml, 256KB cap, refuse honestly otherwise"),
    "skills.mine": _C("COMPOSE", (_ev("src/skaldir/skills.py"),), "mine repeated tool patterns",
                      mutates=True, exit_codes=(0, 5, 7)),
    "skills.validate": _C("COMPOSE", (_ev("src/skaldir/skills.py"),), "re-verify a candidate",
                          exit_codes=(0, 2, 5, 7)),
    "skills.promote": _C("COMPOSE", (_ev("src/skaldir/skills.py"),), "promote after validation",
                         mutates=True, exit_codes=(0, 1, 5, 7),
                         notes="a validation refusal exits 1 (legacy), not 2: "
                               "tests/test_skills.py::test_promote_refuses_without_events pins 1"),
    "skills.demote": _C("COMPOSE", (_ev("src/skaldir/skills.py"),), "demote for review",
                        mutates=True, exit_codes=(0, 5, 7)),
    "skills.show": _C("COMPOSE", (_ev("src/skaldir/skills.py"),), "print the SKILL.md draft"),
    "skills.export": _C("COMPOSE", (_ev("src/skaldir/skills.py"),), "write the SKILL.md out",
                        mutates=True, exit_codes=(0, 2, 5, 7)),
    # -- EXPAND (tool system) ----------------------------------------------- #
    "tools.list": _C("EXPAND", (_ev("src/skaldir/registry.py:123"),),
                     "list tool specs from the runtime DB",
                     data_schema={"type": "object",
                                  "properties": {"tools": {"type": "array"},
                                                 "count": {"type": "integer"},
                                                 "tools_db": {"type": "string"},
                                                 "tools_db_exists": {"type": "boolean"}},
                                  "required": ["tools", "count"]}),
    "tools.show": _C("EXPAND", (_ev("src/skaldir/tools.py"),), "one tool spec as JSON",
                     data_schema={"type": "object", "additionalProperties": True}),
    "tools.scan": _C("EXPAND", (_ev("src/skaldir/toolscan.py"),), "static security scan",
                     exit_codes=(0, 1, 7),
                     notes="findings exit 1, not the designed 2: "
                           "tests/test_tools_store.py::test_scan_cli_blocks_evil pins 1"),
    "tools.propose": _C("EXPAND", (_ev("src/skaldir/tools.py:492"),), "propose a tool spec",
                        mutates=True),
    "tools.approve": _C("EXPAND", (_ev("src/skaldir/gates.py:336"),), "run gates, then approve",
                        mutates=True, exit_codes=(0, 2, 5, 7)),
    "tools.promote-global": _C("EXPAND", (_ev("src/skaldir/capability.py:690"),),
                               "promote a validated project tool to global",
                               mutates=True, exit_codes=(0, 1, 5, 7),
                               data_schema={"type": "object", "additionalProperties": True},
                               compat_top_level_keys=True,
                               notes="a refusal exits 1, not the designed 3/2 "
                                     "(tests/test_tool_scopes.py pins 1 x3); "
                                     "compat_top_level_keys: the pre-contract payload is read "
                                     "at top level by tests/test_tool_scopes.py"),
    "tools.revoke": _C("EXPAND", (_ev("src/skaldir/tools.py"),), "revoke a tool", mutates=True),
    "tools.build": _C(
        "EXPAND",
        (_ev("src/skaldir/toolbuilder.py:298"),
         _ev("src/skaldir/toolbuilder.py ToolBuilder.manager")),
        "capability → gated tool (or a container tool with --image)",
        mutates=True, exit_codes=(0, 2, 3, 4, 6, 7),
        data_schema={"type": "object", "additionalProperties": True},
        notes="--image/--digest build the container half (runtime=docker, pinned by digest): "
              "the container probe runs through the docker runtime and the payload carries "
              "data.container_probe; exit 3 when the build is blocked, 6 on a duplicate "
              "registration, 4 when the gate needs credentials"),
    "tools.builds": _C("EXPAND", (_ev("src/skaldir/toolbuilder.py"),), "the tool-build queue"),
    "tools.request": _C("EXPAND", (_ev("src/skaldir/capability.py:38"),),
                        "record a missing capability", mutates=True,
                        data_schema={"type": "object",
                                     "properties": {"request_id": {"type": "string"},
                                                    "capability": {"type": "string"},
                                                    "status": {"type": "string"},
                                                    "kind": {"type": "string"},
                                                    "project_id": {"type": "string"},
                                                    "created_at": {"type": "string"}},
                                     "required": ["request_id", "capability", "status"]},
                        notes="pre-contract this command mixed human text and JSON on stdout"),
    "tools.fulfill": _C("EXPAND", (_ev("src/skaldir/capability.py:145"),), "run the build pipeline",
                        mutates=True, exit_codes=(0, 2, 3, 5, 7)),
    "tools.mine": _C(
        "EXPAND",
        (_ev("src/skaldir/capability_mining.py:311"),
         _ev("src/skaldir/toolbuilder.py ContainerToolBuilder.mine_container")),
        "scan for candidates, or mine one from a container image (--image)",
        mutates=True, exit_codes=(0, 1, 2, 3, 5, 6, 7),
        data_schema={"type": "object", "additionalProperties": True},
        notes="--image/--capability mine a container capability (inspect → adapt → test); "
              "gains --name/--verb/--digest/--pull/--test-input/--expect/--session "
              "(the --db/--runtime overrides come from the registry context); "
              "exit 6 when an existing capability already covers the need, exit 3 when the "
              "surface cannot be inspected, 5 when the image is missing, 2 when the test fails; "
              "1 is kept from the legacy set and is real on the new path too "
              "(--capability missing / --test-input not JSON)"),
    "tools.candidates": _C("EXPAND", (_ev("src/skaldir/capability_mining.py:115"),),
                           "list mined candidates"),
    "tools.promote": _C("EXPAND", (_ev("src/skaldir/capability_mining.py:379"),),
                        "candidate → build request", mutates=True, exit_codes=(0, 2, 5, 7),
                        notes="an unknown candidate is exit 5 (not argparse's 2)"),
    "tools.compose": _C("COMPOSE", (_ev("src/skaldir/tools.py:469"),),
                        "list composition candidates for a need",
                        data_schema={"type": "object",
                                     "properties": {"need": {"type": "string"},
                                                    "candidates": {"type": "array"},
                                                    "count": {"type": "integer"}},
                                     "required": ["need", "candidates", "count"]}),
    # -- EXPAND (self-expanding tool system, container half) ---------------- #
    # `tools adapter` is a group and carries no entry of its own (only leaves do).
    "tools.adapter.show": _C(
        "EXPAND",
        (_ev("src/skaldir/toolbuilder.py ContainerToolBuilder.container_adapter"),
         _ev("src/skaldir/toolbuilder_cli.py _cmd_tools_adapter_show")),
        "the typed container adapter, its permissions and its test evidence",
        exit_codes=(0, 3, 5, 7),
        data_schema={"type": "object", "additionalProperties": True},
        notes="a staged or registered container tool is required; an unknown name exits 5"),
    "tools.register": _C(
        "EXPAND",
        (_ev("src/skaldir/toolbuilder.py ContainerToolBuilder.register_container_tool"),
         _ev("src/skaldir/toolbuilder_cli.py _cmd_tools_register")),
        "register a TESTED container adapter live (version/health/rollback snapshot)",
        mutates=True, exit_codes=(0, 2, 5, 7),
        data_schema={"type": "object", "additionalProperties": True},
        notes="exit 2 = the adapter is not (still) proven: no registration"),
    "tools.rollback": _C(
        "EXPAND",
        (_ev("src/skaldir/toolbuilder.py ContainerToolBuilder.rollback_container_tool"),
         _ev("src/skaldir/toolbuilder_cli.py _cmd_tools_rollback")),
        "take a container capability back (registry/files/health/containers)",
        mutates=True, exit_codes=(0, 3, 5, 7),
        data_schema={"type": "object", "additionalProperties": True},
        notes="nothing is deleted — the tool directory is quarantined; the container teardown "
              "is label-scoped; an unknown name exits 5"),
    "tools.health": _C(
        "EXPAND",
        (_ev("src/skaldir/toolbuilder.py ContainerToolBuilder.container_health"),
         _ev("src/skaldir/toolbuilder_cli.py _cmd_tools_health")),
        "health of a container capability (ledger state + live container facts)",
        exit_codes=(0, 3, 5, 7),
        data_schema={"type": "object", "additionalProperties": True},
        notes="exit 3 when docker is not reachable; the ledger state is still reported "
              "(and --probe makes a real call, never a simulated one)"),
    "registry.status": _C("EXPAND", (_ev("src/skaldir/registry.py:172"),),
                          "resolved runtime/db/project context",
                          data_schema={"type": "object", "additionalProperties": True},
                          compat_top_level_keys=True,
                          notes="compat_top_level_keys: the pre-contract payload is read at "
                                "top level by tests/test_registry_context.py"),
    "registry.migrate": _C("EXPAND", (_ev("src/skaldir/registry.py"),), "merge legacy tool DBs",
                           mutates=True, exit_codes=(0, 6, 7),
                           data_schema={"type": "object", "additionalProperties": True}),
    # -- kernel groups ------------------------------------------------------ #
    "workflows.list": _C("kernel", (_ev("src/skaldir/workflows.py:1044"),), "workflow contracts",
                         data_schema={"type": "object",
                                      "properties": {"workflows": {"type": "array"},
                                                     "count": {"type": "integer"},
                                                     "runnable": {"type": "array"}},
                                      "required": ["workflows", "count", "runnable"]}),
    "workflows.show": _C("kernel", (_ev("src/skaldir/workflows.py:929"),), "one workflow contract",
                         exit_codes=(0, 5, 7),
                         data_schema={"type": "object", "additionalProperties": True}),
    "workflows.run": _C("kernel", (_ev("src/skaldir/workflows.py:951"),), "start a workflow",
                        mutates=True, exit_codes=(0, 2, 3, 5, 7),
                        data_schema={"type": "object",
                                     "properties": {"run_id": {"type": "string"},
                                                    "workflow": {"type": "string"},
                                                    "status": {"type": "string"},
                                                    "verified": {"type": "boolean"},
                                                    "reason": {"type": "string"},
                                                    "evidence": {"type": "string"}},
                                     "required": ["workflow", "status", "verified"]}),
    "mcp.serve": _C("kernel", (_ev("src/skaldir/mcpserver.py"),), "run the stdio MCP server",
                    exit_codes=(0, 5, 7), notes="blocks by design (stdio server)"),
    # -- ACT ---------------------------------------------------------------- #
    "cu.check": _C("ACT", (_ev("src/skaldir/computer_use.py:464"),
                            _ev("src/skaldir/act_service.py:610"),
                            _ev("src/skaldir/cu_host_session.py:113")),
                   "probe the available runner isolations and bridges",
                   data_schema={"type": "object",
                                "properties": {"runners": {"type": "object"},
                                               "levels_reported": {"type": "array"},
                                               "host": {"type": "object"},
                                               "bridges": {"type": "array"},
                                               "capabilities": {"type": "array"},
                                               "capability_names": {"type": "array"},
                                               "verdict": {"type": "string"},
                                               "summary": {"type": "object"},
                                               "live_desktop": {"type": "boolean"},
                                               "live_desktop_optin": {"type": "object"},
                                               "policy": {"type": "object"}},
                                "required": ["runners", "live_desktop", "bridges", "verdict"]},
                   notes="the same matrix `act check` reports; `live_desktop` is the measured "
                         "opt-in (false unless the switch is set AND a target is named), never "
                         "a literal — no container is started here"),
    "cu.demo": _C("ACT", (_ev("src/skaldir/computer_use.py:151"),),
                  "deterministic channel-ordering demo (in-process fixture)",
                  exit_codes=(0, 1, 7),
                  notes="exit 0 stays (tests/test_computer_use.py::test_cu_demo_cli pins it) but "
                        "the envelope declares data.scope='in-process fixture' + a warning: no "
                        "desktop, no window, no real action"),
    # -- ENFORCE ------------------------------------------------------------ #
    "completion.status": _C("ENFORCE", (_ev("src/skaldir/completion.py:385"),),
                            "goal state and metrics",
                            data_schema={
                                "type": "object",
                                "properties": {
                                    "state": {"type": "string"},
                                    "goal": {"type": ["string", "null"]},
                                    "revision": {"type": "integer"},
                                    "terminal": {"type": "boolean"},
                                    "verified": {"type": "integer"},
                                    "total": {"type": "integer"},
                                    "last_verdict": {"type": ["object", "null"]},
                                    "metrics": {"type": "object"},
                                },
                                "required": ["state", "verified", "total", "metrics"],
                            },
                            notes="state/goal/last_verdict stay identical to the text output"),
    "completion.history": _C("ENFORCE", (_ev("src/skaldir/completion.py:966"),),
                             "the completion timeline",
                             data_schema={"type": "object",
                                          "properties": {"rows": {"type": "array"},
                                                         "count": {"type": "integer"}},
                                          "required": ["rows", "count"]}),
    "completion.begin": _C("ENFORCE", (_ev("src/skaldir/completion.py:541"),),
                           "record a new goal (no execution loop is wired yet)",
                           mutates=True, exit_codes=(0, 3, 7),
                           data_schema={"type": "object",
                                        "properties": {"goal": {"type": "string"},
                                                       "state": {"type": "string"},
                                                       "criteria": {"type": "array"},
                                                       "started_at": {"type": "string"},
                                                       "loop_wired": {"type": "boolean"}},
                                        "required": ["goal", "state", "loop_wired"]},
                           notes="exit 3 while no loop is wired: the goal is recorded, "
                                 "nothing continues after the command returns"),
    "completion.override": _C("ENFORCE", (_ev("src/skaldir/completion.py:496"),),
                              "user override (never VERIFIED_COMPLETE)", mutates=True,
                              exit_codes=(0, 1, 7)),
    # -- legacy treasury / desktop ------------------------------------------ #
    "vault.list": _C("kernel", (_ev("src/skaldir/treasury.py"),), "list credential handles",
                     requires_credentials=True, exit_codes=(0, 1, 7),
                     notes="a missing SKALDIR_VAULT_KEY exits 1, not the designed 4 "
                           "(needs_credentials): tests/test_treasury.py::"
                           "test_vault_cli_requires_key pins 1; the GUI's provider screen "
                           "must read error.message until that test is updated"),
    "vault.describe": _C("kernel", (_ev("src/skaldir/treasury.py"),), "describe one handle",
                         requires_credentials=True, exit_codes=(0, 1, 5, 7),
                         notes="a missing vault key exits 1 (see vault.list)"),
    "vault.add": _C("kernel", (_ev("src/skaldir/treasury.py"),), "add a secret", mutates=True,
                    requires_credentials=True, exit_codes=(0, 1, 7),
                    notes="a missing vault key exits 1 (see vault.list)"),
    "vault.revoke": _C("kernel", (_ev("src/skaldir/treasury.py"),), "revoke a handle", mutates=True,
                       requires_credentials=True, exit_codes=(0, 1, 5, 7),
                       notes="a missing vault key exits 1 (see vault.list)"),
    "spend.request": _C("kernel", (_ev("src/skaldir/treasury.py"),), "request a spend",
                        mutates=True,
                        exit_codes=(0, 2, 7)),
    "spend.list": _C("kernel", (_ev("src/skaldir/treasury.py"),), "list open spend requests"),
    "spend.approve": _C("kernel", (_ev("src/skaldir/treasury.py"),), "approve a request",
                        mutates=True, exit_codes=(0, 3, 5, 7)),
    "spend.deny": _C("kernel", (_ev("src/skaldir/treasury.py"),), "deny a request", mutates=True,
                     exit_codes=(0, 3, 5, 7)),
    "spend.complete": _C("kernel", (_ev("src/skaldir/treasury.py"),), "mark a spend executed",
                         mutates=True, exit_codes=(0, 3, 5, 7)),
    "spend.allowance": _C("kernel", (_ev("src/skaldir/treasury.py"),), "test-money allowance",
                          data_schema={"type": "object", "additionalProperties": True}),
    "desktop.install": _C("kernel", (_ev("src/skaldir/desktop.py"),), "desktop entry + icons",
                          mutates=True),
    "desktop.uninstall": _C("kernel", (_ev("src/skaldir/desktop.py"),), "remove launcher + icons",
                            mutates=True),
    "desktop.status": _C("kernel", (_ev("src/skaldir/desktop.py"),), "deployment check",
                         exit_codes=(0, 5, 7)),
    "desktop.start": _C("kernel", (_ev("src/skaldir/desktop.py"),), "verify, then start the app",
                        exit_codes=(0, 3, 7)),
    "desktop.build-app": _C("kernel", (_ev("src/skaldir/appbuild.py"),), "build the app natively",
                            mutates=True, exit_codes=(0, 7)),
    "doctor": _C("kernel", (_ev("src/skaldir/cli.py:2043"),), "release health check",
                 exit_codes=(0, 2, 7),
                 data_schema={"type": "object",
                              "properties": {"checks": {"type": "array"},
                                             "ok": {"type": "boolean"}},
                              "required": ["checks", "ok"]}),
    # -- the contract surface itself ---------------------------------------- #
    "contract.list": _C("kernel", (_ev("src/skaldir/cli_contract.py"),),
                        "every command, its layer and its JSON flag",
                        data_schema={"type": "object",
                                     "properties": {"commands": {"type": "array"},
                                                    "count": {"type": "integer"}},
                                     "required": ["commands", "count"]}),
    "contract.show": _C("kernel", (_ev("src/skaldir/cli_contract.py"),),
                        "one command's full contract record", exit_codes=(0, 5, 7),
                        data_schema={"type": "object", "additionalProperties": True}),
    "contract.dump": _C("kernel", (_ev("src/skaldir/cli_contract.py"),),
                        "the self-describing inventory with JSON Schemas",
                        data_schema={"type": "object",
                                     "properties": {"schema_version": {"type": "string"},
                                                    "godsvoice_version": {"type": "string"},
                                                    "root_commands": {"type": "integer"},
                                                    "leaf_commands": {"type": "integer"},
                                                    "parser_nodes": {"type": "integer"},
                                                    "unregistered": {"type": "array"},
                                                    "commands": {"type": "array"}},
                                     "required": ["schema_version", "leaf_commands", "commands"]}),
    "selftest": _C("kernel", (_ev("src/skaldir/cli_contract.py"),),
                   "probe all eight layers and report reachability", exit_codes=(0, 2, 7),
                   data_schema={
                       "type": "object",
                       "properties": {
                           "runtime": {"type": "string"},
                           "ok": {"type": "boolean"},
                           "probes": {"type": "integer"},
                           "failed": {"type": "integer"},
                           "layers": {"type": "array", "items": {
                               "type": "object",
                               "properties": {
                                   "layer": {"type": "string"},
                                   "ok": {"type": "boolean"},
                                   "status": {"type": "string",
                                              "enum": ["reachable", "blocked", "failed"]},
                                   "exit_code": {"type": "integer"},
                                   "blocked_reason": {"type": ["string", "null"]},
                                   "cli_commands": {"type": "array",
                                                    "items": {"type": "string"}},
                                   "evidence": {"type": "array"},
                                   "probes": {"type": "array"},
                               },
                               "required": ["layer", "ok", "status", "probes"],
                           }},
                       },
                       "required": ["layers", "ok", "probes", "failed"],
                   },
                   notes="exit 2 when any layer is not reachable; a layer that is not wired "
                         "reports ok=false with blocked_reason, never reachable"),
    # -- the Jev decision façade (ROUTE) ------------------------------------ #
    # `register_cli(sub)` is called in `build_parser()` right after the root
    # `add_subparsers(...)`; the group's five verbs are leaf commands of the
    # product tree. The façade adopts the contract's exit vocabulary and keeps
    # the two codes a pinned test demands (both spellings are recorded below).
    "jev.kinds": _C("ROUTE",
                    (_ev("src/skaldir/jev/kinds.py", note="kind -> pack map"),
                     _ev("src/skaldir/jev/contracts.py", note="JEV_REQUESTS/JEV_ANSWERS")),
                    "the six decision kinds with their pack ref, risk class and flag",
                    exit_codes=(0, 1, 7),
                    data_schema={"type": "object",
                                 "properties": {
                                     "kinds": {"type": "array", "items": {
                                         "type": "object",
                                         "properties": {
                                             "kind": {"type": "string"},
                                             "pack_ref": {"type": "string"},
                                             "owner": {"type": "string"},
                                             "risk_class": {"type": "string"},
                                             "applicable_states": {"type": "array",
                                                                   "items": {"type": "string"}},
                                             "flag": {"type": "string"},
                                             "answer_model": {"type": "string"},
                                             "request_model": {"type": "string"},
                                         },
                                         "required": ["kind", "pack_ref", "answer_model"]}},
                                     "count": {"type": "integer"},
                                 },
                                 "required": ["kinds", "count"]},
                    notes="read-only view; the same rows `skaldir jev selftest` probes"),
    "jev.packs": _C("ROUTE",
                    (_ev("src/skaldir/jev/packs.py", note="pack definitions"),
                     _ev("src/skaldir/system_one_questions.py",
                         note="the registry the view is built from (voice_triage_v1 is reused)")),
                    "the question-registry manifest (substrate packs + the five Jev packs)",
                    exit_codes=(0, 1, 7),
                    data_schema={"type": "object",
                                 "properties": {
                                     "packs": {"type": "array", "items": {
                                         "type": "object",
                                         "properties": {
                                             "ref": {"type": "string"},
                                             "id": {"type": "string"},
                                             "version": {"type": "string"},
                                             "owner": {"type": "string"},
                                             "risk_class": {"type": "string"},
                                             "purpose": {"type": "string"},
                                             "fallback_policy": {"type": "string"},
                                             "calibration_dataset_version": {"type": "string"},
                                             "questions": {"type": "array",
                                                           "items": {"type": "string"}},
                                         },
                                         "required": ["ref", "id", "owner", "questions"]}},
                                     "count": {"type": "integer"},
                                 },
                                 "required": ["packs", "count"]},
                    notes="12 packs live: the substrate's registry plus memory_injection_v1, "
                          "routing_v1, capability_ranking_v1, compaction_v1, "
                          "adversarial_test_v1"),
    "jev.selftest": _C("ROUTE",
                       (_ev("src/skaldir/jev/selftest.py", note="R1-R4 table + kind reachability"),
                        _ev("tests/test_jev_facade_rules.py", "test",
                            "the pinned test for every hard rule the report names")),
                       "the R1-R4 table plus per-kind reachability (exit 1 if untested)",
                       exit_codes=(0, 1, 7),
                       data_schema={"type": "object",
                                    "properties": {
                                        "selftest": {"type": "object",
                                                     "properties": {
                                                         "ok": {"type": "boolean"},
                                                         "reachable": {"type": "boolean"},
                                                         "blocked_path_ok": {"type": "boolean"},
                                                         "kinds": {"type": "array"},
                                                         "rules": {"type": "array"},
                                                         "untested_rules": {
                                                            "type": "array",
                                                            "items": {"type": "string"},
                                                        },
                                                         "rule_count": {"type": "integer"},
                                                         "contract_scan": {"type": "object"},
                                                         "packs": {"type": "array"},
                                                         "registry_refs": {
                                                            "type": "array",
                                                            "items": {"type": "string"},
                                                        },
                                                         "exit_code": {"type": "integer"},
                                                     },
                                                     "required": ["ok", "kinds", "rules",
                                                                  "untested_rules", "exit_code"]}},
                                    "required": ["selftest"]},
                       notes="exit 1 = a hard rule lost its tests or a kind is not reachable — "
                             "the façade's own vocabulary, not the designed 2 "
                             "(verification_failed): tests/test_jev_facade_cli.py pins 1. "
                             "A kind that is not wired reports reachable=false with its "
                             "blocked_path, never a fabricated answer. Wired, the report is "
                             "nested under data.selftest; standalone (`python -m skaldir.jev`) "
                             "it is the top level"),
    "jev.decide": _C("ROUTE",
                     (_ev("src/skaldir/jev/decide.py:1", note="one provider edge + the decision"),
                      _ev("src/skaldir/jev/rules.py", note="R1-R4 re-checked at construction"),
                      _ev("src/skaldir/jev/ledger.py", note="the append-only decision ledger")),
                     "one decision: request JSON in, exactly one advisory decision out",
                     exit_codes=(0, 1, 3, 4, 7), mutates=True, requires_credentials=True,
                     data_schema={"type": "object",
                                  "additionalProperties": True,
                                  "properties": {
                                      "decision_id": {"type": "string"},
                                      "kind": {"type": "string"},
                                      "run_id": {"type": "string"},
                                      "goal_id": {"type": "string"},
                                      "goal_revision": {"type": "integer"},
                                      "authority": {"type": "string", "enum": ["advisory"]},
                                      "status": {"type": "string",
                                                 "enum": ["ok", "blocked", "refused"]},
                                      "provider_mode": {"type": "string"},
                                      "live_jev": {"type": "boolean"},
                                      "question_pack_id": {"type": "string"},
                                      "question_pack_version": {"type": "string"},
                                      "confidence": {"type": "number"},
                                      "answer": {"type": "object"},
                                      "refusal_reason": {"type": "string"},
                                      "fallback": {"type": "string"},
                                      "caution_level": {"type": "integer"},
                                      "latency_ms": {"type": "integer"},
                                      "attempts": {"type": "integer"},
                                      "evidence_refs": {"type": "array",
                                                        "items": {"type": "string"}},
                                      "created_at": {"type": "number"},
                                  },
                                  "required": ["decision_id", "kind", "status", "provider_mode",
                                               "authority", "fallback"]},
                     notes="the request arrives on stdin or via --request/--transcript FILE; "
                           "exit 3 = no advisory (provider disabled/stalled), exit 4 = a "
                           "live-capable provider without an authorized credential "
                           "(needs_credentials), exit 7 = malformed request, kind mismatch or "
                           "a hard-rule violation. Every decision is appended to "
                           "--state/jev_decisions.jsonl unless --dry-run; `--apply` is "
                           "refused with exit 1 because rebuilding a transcript belongs to "
                           "the compaction port (src/skaldir/jevcomp). Never a goal PASS, never "
                           "a permission, never a security exception"),
    "jev.history": _C("ROUTE",
                      (_ev("src/skaldir/jev/ledger.py", note="the decision ledger read back"),),
                      "the recorded decisions (what Jev decided, which fallback fired)",
                      exit_codes=(0, 1, 7),
                      data_schema={"type": "object",
                                   "properties": {
                                       "decisions": {"type": "array",
                                                     "items": {"type": "object",
                                                               "additionalProperties": True}},
                                       "count": {"type": "integer"},
                                       "ledger": {"type": "string"},
                                   },
                                   "required": ["decisions", "count", "ledger"]},
                      notes="an absent ledger is an empty list with exit 0, never a 5 "
                            "(not_found): the state dir may not have been written yet. "
                            "--state defaults to the relative '.godsv', so the ledger path in "
                            "the payload is relative to the cwd it ran in"),
    # -- compose ------------------------------------------------ #
    "compose.candidates": _C(
        'COMPOSE',
        (_ev('src/skaldir/compose_service.py:1599', 'code', ''),),
        'buildable chains from the real registry (+ stored composites)',
        data_schema={
            'type': 'object',
            'properties': {
                'registry': {'type': 'string'},
                'steps_considered': {'type': 'integer'},
                'chains': {'type': 'array'},
                'composites': {'type': 'array'},
            },
        },
        notes='a chain candidate is a heuristic proposal, never proof of a join',
    ),
    "compose.create": _C(
        'COMPOSE',
        (_ev('src/skaldir/compose_service.py:686', 'code', ''),),
        'store a composite definition + its registry row',
        exit_codes=(0, 1, 2, 6, 7),
        mutates=True,
        data_schema={
            'type': 'object',
            'properties': {
                'composite': {'type': 'string'},
                'created': {'type': 'boolean'},
                'path': {'type': 'string'},
                'validation': {'type': 'object'},
            },
        },
        notes="an existing name is exit 6 (conflict) unless --replace; a stored but invalid "
              "composite exits 2",
    ),
    "compose.run": _C(
        'COMPOSE',
        (_ev('src/skaldir/compose_service.py:1355', 'code', ''),),
        "execute an approved composite through the runtime's real runners",
        exit_codes=(0, 1, 2, 3, 5, 7),
        mutates=True,
        data_schema={
            'type': 'object',
            'properties': {
                'composite': {'type': 'string'},
                'status': {'type': 'string'},
                'verified': {'type': 'boolean'},
                'steps': {'type': 'array'},
                'outputs': {'type': 'object'},
                'missing_kinds': {'type': 'array'},
            },
        },
        notes="a step kind without a runner is exit 3 and names the kind; a step that fails is "
              "exit 2; only a held end-state exits 0",
    ),
    "compose.show": _C(
        'COMPOSE',
        (_ev('src/skaldir/compose_service.py:807', 'code', ''),),
        'one composite (or all) with per-step executability',
        exit_codes=(0, 5, 7),
        data_schema={
            'type': 'object',
            'properties': {
                'composite': {'type': 'string'},
                'document': {'type': 'object'},
                'validation': {'type': 'object'},
                'registry': {'type': 'object'},
            },
        },
    ),
    "compose.validate": _C(
        'COMPOSE',
        (_ev('src/skaldir/compose_service.py:807', 'code', ''),),
        'validate a composite; --approve flips the registry row when clean',
        exit_codes=(0, 2, 5, 7),
        mutates=True,
        data_schema={
            'type': 'object',
            'properties': {
                'composite': {'type': 'string'},
                'validation': {'type': 'object'},
                'approved': {'type': 'boolean'},
            },
        },
        notes='--force records the override; it never makes a missing runner exist',
    ),
    # -- provider ------------------------------------------------ #
    "provider.catalog": _C(
        'kernel',
        (
            _ev(
                'src/skaldir/provider_registry_data.py',
                'code',
                'the breadth registry + the live probe log',
            ),
            _ev('src/skaldir/provider_catalog.py', 'code', 'drift check against the chain catalog'),
        ),
        "the verified multi-provider catalog: base URLs, auth, streaming, structured output, "
        "vision — each claim with its verification status",
        exit_codes=(0, 1, 3, 5, 7),
        data_schema={
            'type': 'object',
            'properties': {
                'count': {'type': 'integer'},
                'order': {'type': 'array'},
                'verification_counts': {'type': 'object'},
                'probe_counts': {'type': 'object'},
                'probe_date': {'type': 'string'},
                'results': {'type': 'array'},
                'features': {'type': 'array'},
                'drift': {'type': 'object'},
                'probes': {'type': 'object'},
                'chain': {'type': 'object'},
            },
            'required': ['count', 'results', 'verification_counts'],
        },
        notes="verified = a live probe we ran; documented = an official page (the fetched flag "
              "says whether we retrieved it); unverified = nobody proved it. --entry names one "
              "provider, --verification filters, --probes prints the evidence; an unknown "
              "--entry exits 5",
    ),
    "provider.add": _C(
        'kernel',
        (
            _ev('src/skaldir/provider_config.py', 'code', 'atomic write + backup'),
            _ev(
                'tests/test_provider_cli.py',
                'test',
                'the secret-refusal and stub-endpoint proofs',
            ),
        ),
        'add (or replace) one provider and project it into godsv.llm',
        exit_codes=(0, 1, 3, 5, 6, 7),
        mutates=True,
        data_schema={
            'type': 'object',
            'properties': {
                'action': {'type': 'string'},
                'name': {'type': 'string'},
                'default': {'type': 'string'},
                'path': {'type': 'string'},
                'backup': {'type': ['string', 'null']},
                'provider': {'type': 'object'},
            },
            'required': ['action', 'name', 'path'],
        },
        notes="a literal api_key/token/secret is refused (exit 3); --api-key-env takes a "
              "variable NAME only; the previous file is backed up",
    ),
    "provider.default": _C(
        'kernel',
        (
            _ev('src/skaldir/provider_config.py', 'code', 'projection write'),
            _ev('src/skaldir/llm_provider.py', 'code', 'the reader of godsv.llm'),
        ),
        'which provider the runtime uses (mirrored into godsv.llm)',
        exit_codes=(0, 1, 5, 7),
        mutates=True,
        data_schema={
            'type': 'object',
            'properties': {
                'action': {'type': 'string'},
                'name': {'type': 'string'},
                'projection': {'type': 'object'},
            },
            'required': ['action', 'name'],
        },
        notes="the projection is what `skaldir` runs with; the secret is still resolved from the "
              "environment",
    ),
    "provider.doctor": _C(
        'kernel',
        (
            _ev('src/skaldir/provider_config.py', 'code', 'offline diagnosis'),
            _ev('tests/test_provider_cli.py', 'test', 'the doctor cases'),
        ),
        'what is missing for each configured provider (offline)',
        exit_codes=(0, 1, 3, 7),
        data_schema={
            'type': 'object',
            'properties': {
                'config_file': {'type': 'string'},
                'default': {'type': 'string'},
                'count': {'type': 'integer'},
                'ok': {'type': 'boolean'},
                'providers': {'type': 'array'},
                'usable': {'type': 'array'},
                'broken': {'type': 'array'},
                'next_actions': {'type': 'array'},
            },
            'required': ['config_file', 'providers', 'ok'],
        },
        notes="exit 3 when a CONFIGURED provider is unusable; nothing configured exits 0 with "
              "ok=false and the command to run; no request is made",
    ),
    # -- first-run setup: the two mandatory keys (TypeSafe/Jev + one LLM) ------- #
    "setup": _C(
        'kernel',
        (
            _ev(
                'src/skaldir/setup_flow.py',
                'code',
                'the flow: detect, validate, atomic write, verify',
            ),
            _ev(
                'src/skaldir/provider_config.py',
                'code',
                'the store it writes through (backup + secret refusal)',
            ),
            _ev(
                'src/skaldir/jev/runtime.py',
                'code',
                'the decision layer the Jev key is verified against',
            ),
            _ev('tests/test_setup_flow.py', 'test', 'the stub-endpoint end-to-end proofs'),
        ),
        'first-run setup: the two mandatory keys, each proved by a real call',
        exit_codes=(0, 1, 2, 3, 4, 7, 8),
        mutates=True,
        requires_credentials=True,
        data_schema={
            'type': 'object',
            'properties': {
                'action': {'type': 'string'},
                'config_file': {'type': 'string'},
                'mandatory_keys': {'type': 'array'},
                'state': {'type': 'object'},
                'roles': {'type': 'object'},
                'write': {'type': 'object'},
                'verify': {'type': 'object'},
                'reread': {'type': 'object'},
                'ok': {'type': 'boolean'},
                'verdict': {'type': 'string'},
            },
            'required': ['action', 'ok', 'mandatory_keys'],
        },
        notes="a literal key is refused, never stored, never printed (api_key_env is a NAME); "
              "--check re-validates and writes nothing, exit 0 only when both keys answer; exit "
              "4 when a remote endpoint has no credential; the config is written atomically with "
              "a backup and then re-read before one real Jev decision and one real completion "
              "verify it",
    ),
    "provider.list": _C(
        'kernel',
        (
            _ev('src/skaldir/provider_config.py', 'code', 'the store'),
            _ev('src/skaldir/llm_provider.py', 'code', 'the same file/keys the runtime resolves'),
        ),
        'configured providers, redacted (default marked)',
        exit_codes=(0, 1, 3, 7),
        data_schema={
            'type': 'object',
            'properties': {
                'config_file': {'type': 'string'},
                'default': {'type': 'string'},
                'count': {'type': 'integer'},
                'providers': {'type': 'array'},
                'active': {'type': 'object'},
                'drift': {'type': 'array'},
            },
            'required': ['config_file', 'providers', 'count'],
        },
        notes="never prints a credential: api_key_env is a NAME, the state is "
              "set/unset/not-required",
    ),
    "provider.remove": _C(
        'kernel',
        (
            _ev('src/skaldir/provider_config.py', 'code', 'registry write'),
            _ev('tests/test_provider_cli.py', 'test', 'the removal cases'),
        ),
        'remove one provider (a removed default clears the projection)',
        exit_codes=(0, 1, 5, 7),
        mutates=True,
        data_schema={
            'type': 'object',
            'properties': {
                'action': {'type': 'string'},
                'name': {'type': 'string'},
                'remaining': {'type': 'array'},
                'projection_cleared': {'type': 'boolean'},
            },
            'required': ['action', 'name', 'remaining'],
        },
        notes="never guesses a new default: the runtime stays unconfigured until `provider "
              "default` names one",
    ),
    "provider.test": _C(
        'kernel',
        (
            _ev('src/skaldir/llm_provider.py', 'code', 'OpenAICompatibleLLM, the real client'),
            _ev(
                'src/skaldir/system_one_policy.py',
                'code',
                'the retry/timeout policy the call follows',
            ),
            _ev(
                'src/skaldir/structured.py',
                'code',
                '--structured: the schema-constrained call, its validation and the mode trail',
            ),
            _ev(
                'src/skaldir/provider_catalog.py',
                'code',
                '--structured: the fallback chain it reports',
            ),
        ),
        "one real call: latency, model echo, answer (redacted); --structured asks for a "
        "schema-constrained answer",
        exit_codes=(0, 1, 2, 3, 4, 5, 7, 8),
        requires_credentials=True,
        data_schema={
            'type': 'object',
            'properties': {
                'name': {'type': 'string'},
                'endpoint': {'type': 'string'},
                'model': {'type': 'string'},
                'model_echo': {'type': 'string'},
                'latency_ms': {'type': 'integer'},
                'answer_preview': {'type': 'string'},
                'credential': {'type': 'string'},
                'client': {'type': 'string'},
                'mode_used': {'type': 'string'},
                'native_json_schema': {'type': ['boolean', 'null']},
                'validation': {'type': 'object'},
                'fallback_chain': {'type': 'object'},
            },
            'required': ['name', 'endpoint', 'model', 'latency_ms'],
        },
        notes="exit 4 when a remote provider has no credential — no request is attempted; "
              "401/403 also exit 4; a timeout exits 8; with --structured an answer that does not "
              "satisfy the schema exits 2 and carries the raw text + the validation error (never "
              "coerced)",
    ),
    "provider.tiers": _C(
        'kernel',
        (
            _ev('src/skaldir/provider_config.py', 'code', 'tier mapping'),
            _ev('src/skaldir/llm.py', 'code', 'TIERS: small/mid/main'),
        ),
        'the small/mid/main → model mapping (show, or set with --small/--main)',
        exit_codes=(0, 1, 5, 7),
        mutates=True,
        data_schema={
            'type': 'object',
            'properties': {
                'name': {'type': 'string'},
                'tiers': {'type': 'object'},
                'requested': {'type': 'object'},
            },
            'required': ['name'],
        },
        notes="without --small/--mid/--main it only reads; an empty value clears that tier and "
              "the provider model stays the fallback",
    ),
    # -- hand ------------------------------------------------ #
    "goal.criteria": _C(
        'PERSIST',
        (_ev('src/skaldir/runs_store.py', 'code', ''),),
        'the criteria table; --verify flips one from evidence only',
        exit_codes=(0, 1, 2, 5, 6, 7),
        mutates=True,
        data_schema={'type': 'object', 'additionalProperties': True},
        notes="--verify demands --evidence-ref; a stale revision or a foreign workspace "
              "fingerprint is refused and nothing is written",
    ),
    "goal.show": _C(
        'PERSIST',
        (_ev('src/skaldir/runs_store.py', 'code', ''),),
        'the durable goal record: revision, state, decisions, criteria, operations, budget, owners',
        exit_codes=(0, 1, 5, 7),
        data_schema={'type': 'object', 'additionalProperties': True},
    ),
    "image.guard": _C(
        'EXPAND',
        (_ev('src/skaldir/image_pin.py', 'code', ''),),
        'the same check at the surface a running capability meets (health / invoke / rollback)',
        exit_codes=(0, 1, 2, 3, 5, 6, 7),
        data_schema={'type': 'object', 'additionalProperties': True},
        notes="exit 2 (health) · 6 (invoke) · 3 (rollback to --version); every guard carries the "
              "digest into its evidence",
    ),
    "image.inspect": _C(
        'EXPAND',
        (_ev('src/skaldir/image_inspect.py', 'code', ''),),
        'shell-free image inspection + candidate classification',
        exit_codes=(0, 1, 3, 5, 7),
        data_schema={'type': 'object', 'additionalProperties': True},
        notes="a produced classification exits 0 even when every candidate is 'impossible' — "
              "that is the answer, not a refusal",
    ),
    "image.layers": _C(
        'EXPAND',
        (_ev('src/skaldir/image_inspect.py', 'code', ''),),
        'layer/executable/library listing from `docker save`',
        exit_codes=(0, 1, 3, 5, 7),
        data_schema={'type': 'object', 'additionalProperties': True},
        notes='never starts a container (metadata + layers only)',
    ),
    "image.list": _C(
        'EXPAND',
        (_ev('src/skaldir/image_pin.py', 'code', ''),),
        'the recorded pins',
        data_schema={'type': 'object', 'additionalProperties': True},
    ),
    "image.pin": _C(
        'EXPAND',
        (_ev('src/skaldir/image_pin.py', 'code', ''),),
        'resolve, then record the digest (atomic store)',
        exit_codes=(0, 1, 3, 5, 6, 7),
        mutates=True,
        data_schema={'type': 'object', 'additionalProperties': True},
        notes="a different digest for an already-pinned reference needs --force: a re-pin is a "
              "decision, never a side effect of a moved tag",
    ),
    "image.resolve": _C(
        'EXPAND',
        (_ev('src/skaldir/image_pin.py', 'code', ''),),
        "what the registry says the reference resolves to (index + per-platform manifest "
        "digest); --offline reads the daemon's own record",
        exit_codes=(0, 1, 3, 5, 7),
        data_schema={'type': 'object', 'additionalProperties': True},
        notes='read-only: nothing is pulled and nothing is written',
    ),
    "image.verify": _C(
        'EXPAND',
        (_ev('src/skaldir/image_pin.py', 'code', ''),),
        're-verify a recorded pin against the local image (--remote also against the registry)',
        exit_codes=(0, 1, 2, 5, 6, 7),
        data_schema={'type': 'object', 'additionalProperties': True},
        notes='a digest that no longer matches is refused, exit 6',
    ),
    "mcp.call": _C(
        'EXPAND',
        (_ev('src/skaldir/adapter_mcp.py', 'code', ''),),
        "call the registered MCP capability through its adapter (--via wrapper runs the "
        "generated CLI wrapper)",
        exit_codes=(0, 2, 3, 5, 7),
        data_schema={'type': 'object', 'additionalProperties': True},
    ),
    "mcp.map": _C(
        'EXPAND',
        (_ev('src/skaldir/adapter_mcp.py', 'code', ''),),
        "map a server's tools to typed adapters; an untypable schema is refused with its reason",
        exit_codes=(0, 1, 3, 7),
        mutates=True,
        data_schema={'type': 'object', 'additionalProperties': True},
        notes="--stage writes the adapter/module/wrapper/manifest; a refused tool is never "
              "registered",
    ),
    "mcp.probe": _C(
        'EXPAND',
        (_ev('src/skaldir/adapter_mcp.py', 'code', ''),),
        'handshake + tools/list with the raw transcript',
        exit_codes=(0, 3, 7),
        data_schema={'type': 'object', 'additionalProperties': True},
        notes='nothing is written: this is the read-only door-opener',
    ),
    "mcp.register": _C(
        'EXPAND',
        (_ev('src/skaldir/adapter_mcp.py', 'code', ''), _ev('src/skaldir/tools.py', 'code', '')),
        'register a TESTED MCP adapter live',
        exit_codes=(0, 2, 3, 5, 7),
        mutates=True,
        data_schema={'type': 'object', 'additionalProperties': True},
        notes='exit 2 = the adapter is not (still) proven: no registration',
    ),
    "mcp.test": _C(
        'EXPAND',
        (_ev('src/skaldir/adapter_mcp.py', 'code', ''),),
        'run a real tools/call against the server and check the declared outputs',
        exit_codes=(0, 2, 3, 5, 7),
        mutates=True,
        data_schema={'type': 'object', 'additionalProperties': True},
        notes="exit 2 = the real call did not return the declared outputs (or --expect was "
              "missing); the evidence binds to the adapter bytes",
    ),
    "runs.checkpoint": _C(
        'PERSIST',
        (_ev('src/skaldir/runs_store.py', 'code', ''),),
        'the self-sufficient snapshot (file + index row + cursor)',
        exit_codes=(0, 1, 3, 5, 7),
        mutates=True,
        data_schema={'type': 'object', 'additionalProperties': True},
    ),
    "runs.list": _C(
        'PERSIST',
        (_ev('src/skaldir/agent_loop.py', 'code', ''),),
        'the agent run ids this state directory holds, newest first (read-only)',
        exit_codes=(0, 1, 7),
        data_schema={'type': 'object', 'additionalProperties': True},
        notes='reads <state-dir>/runs/*.jsonl through agent_loop.list_runs; --json carries '
              '{state_dir, runs_dir, exists, count, limit, runs}; an empty list is data with '
              'exit 0',
    ),
    "runs.resume": _C(
        'PERSIST',
        (_ev('src/skaldir/runs_store.py', 'code', ''),),
        "the R0-R3 resume protocol: writer lease, classify every in-flight operation, park what "
        "cannot be proven",
        exit_codes=(0, 1, 2, 3, 5, 7),
        mutates=True,
        data_schema={'type': 'object', 'additionalProperties': True},
        notes="exit 3 while a parked operation waits for operator:side_effect_verified; exit 2 "
              "for a record that cannot be classified",
    ),
    "runs.start": _C(
        'PERSIST',
        (_ev('src/skaldir/runs_store.py', 'code', ''),),
        'create the goal record + criteria + budget rows + the first checkpoint',
        exit_codes=(0, 1, 3, 5, 6, 7),
        mutates=True,
        data_schema={'type': 'object', 'additionalProperties': True},
        notes="exit 6 when the session already has an open goal — a goal is never silently "
              "re-created",
    ),
    "runs.status": _C(
        'PERSIST',
        (_ev('src/skaldir/runs_store.py', 'code', ''),),
        "the store, the goal records, the operations, the budget and the run supervisor's "
        "snapshot (read-only)",
        exit_codes=(0, 1, 5, 7),
        data_schema={'type': 'object', 'additionalProperties': True},
        notes='exit 5 when runs.db does not exist yet — no store is not a success',
    ),
    "security.coverage": _C(
        'ENFORCE',
        (_ev('src/skaldir/security_linux.py', 'code', ''),),
        'report measured enforcement coverage per primitive without hiding degradation',
        exit_codes=(0, 3),
        data_schema={'type': 'object', 'additionalProperties': True},
    ),
    "security.audit": _C(
        'ENFORCE',
        (_ev('src/skaldir/security_audit.py', 'code', ''),),
        'run the declared defect-class rules over the tree and report every finding with its fix',
        exit_codes=(0, 1, 2, 3, 7),
        data_schema={'type': 'object', 'additionalProperties': True},
        notes="a scan that could not run exits blocked, never ok — nothing is reported as clean "
              "that was not scanned",
    ),
    "style.logo": _C(
        'kernel',
        (_ev('src/skaldir/theme.py', 'code', ''), _ev('src/skaldir/logo.py', 'code', '')),
        'the Skaldir CLI art (full banner or compact mark)',
        data_schema={'type': 'object', 'additionalProperties': True},
    ),
    "style.palette": _C(
        'kernel',
        (_ev('src/skaldir/theme.py', 'code', ''),),
        'the grey ramp, the tokens and the status marks',
        data_schema={'type': 'object', 'additionalProperties': True},
    ),
    "style.preview": _C(
        'kernel',
        (_ev('src/skaldir/theme.py', 'code', ''),),
        'every helper of the visual language, one screen',
        data_schema={'type': 'object', 'additionalProperties': True},
    ),
    "tools.api.call": _C(
        'EXPAND',
        (_ev('src/skaldir/adapter_http.py', 'code', ''),),
        "call the registered HTTP capability and report the real request's status",
        exit_codes=(0, 2, 3, 5, 7),
        data_schema={'type': 'object', 'additionalProperties': True},
        notes='exit 3 = the pinned surface changed or the service is down',
    ),
    "tools.api.discover": _C(
        'EXPAND',
        (_ev('src/skaldir/adapter_http.py', 'code', ''),),
        "discover a documented operation, test it against the real service with an independent "
        "local oracle, stage it (does NOT register)",
        exit_codes=(0, 1, 2, 3, 5, 6, 7),
        mutates=True,
        data_schema={'type': 'object', 'additionalProperties': True},
    ),
    "tools.api.health": _C(
        'EXPAND',
        (
            _ev('src/skaldir/adapter_http.py', 'code', ''),
            _ev('src/skaldir/capability_health.py', 'code', ''),
        ),
        'the live health probe of a registered HTTP capability',
        exit_codes=(0, 3, 5, 7),
        data_schema={'type': 'object', 'additionalProperties': True},
    ),
    "tools.api.inspect": _C(
        'EXPAND',
        (
            _ev('src/skaldir/adapter_http.py', 'code', ''),
            _ev('src/skaldir/adapter_http_cli.py', 'code', ''),
        ),
        'which documented HTTP surface a service offers (read-only)',
        exit_codes=(0, 1, 3, 7),
        data_schema={'type': 'object', 'additionalProperties': True},
        notes='read-only: which documented surface answered + every probe',
    ),
    "tools.api.register": _C(
        'EXPAND',
        (
            _ev('src/skaldir/adapter_http.py', 'code', ''),
            _ev('src/skaldir/tools.py', 'code', ''),
            _ev('src/skaldir/capability_health.py', 'code', ''),
        ),
        'register a TESTED HTTP adapter live',
        exit_codes=(0, 2, 5, 7),
        mutates=True,
        data_schema={'type': 'object', 'additionalProperties': True},
        notes='exit 2 = the adapter is not (still) proven: no registration',
    ),
    "tools.api.rollback": _C(
        'EXPAND',
        (_ev('src/skaldir/toolbuilder.py', 'code', ''),),
        'retire a registered HTTP capability (and sweep only OUR containers)',
        exit_codes=(0, 3, 5, 7),
        mutates=True,
        data_schema={'type': 'object', 'additionalProperties': True},
    ),
    "tools.api.show": _C(
        'EXPAND',
        (_ev('src/skaldir/adapter_http.py', 'code', ''),),
        'one staged HTTP adapter with its evidence',
        exit_codes=(0, 3, 5, 7),
        data_schema={'type': 'object', 'additionalProperties': True},
    ),
    "verify.evidence": _C(
        'VERIFY',
        (_ev('src/skaldir/verify_service.py', 'code', ''),),
        'the recorded evidence records, filterable by run and criterion',
        exit_codes=(0, 1, 5, 7),
        data_schema={'type': 'object', 'additionalProperties': True},
    ),
    "verify.plan": _C(
        'VERIFY',
        (_ev('src/skaldir/verify_service.py', 'code', ''),),
        'derive the real check that would certify each goal criterion',
        exit_codes=(0, 1, 3, 7),
        data_schema={'type': 'object', 'additionalProperties': True},
        notes="exit 3 when a criterion has no certifiable check here, so a plan never looks "
              "stronger than it is",
    ),
    "verify.run": _C(
        'VERIFY',
        (_ev('src/skaldir/verify_service.py', 'code', ''),),
        'execute the checks against the real workspace and print PASS/FAIL/UNKNOWN',
        exit_codes=(0, 1, 2, 3, 7),
        mutates=True,
        data_schema={'type': 'object', 'additionalProperties': True},
        notes='UNKNOWN is never PASS: the verdict comes from executed results only and exits 3',
    ),
    "verify.status": _C(
        'VERIFY',
        (_ev('src/skaldir/verify_service.py', 'code', ''),),
        "the recorded verification runs and whether the latest verdict is still fresh for the "
        "workspace it was produced in",
        exit_codes=(0, 1, 5, 7),
        data_schema={'type': 'object', 'additionalProperties': True},
    ),
    "voice.check": _C(
        'SUPERVISE',
        (
            _ev('src/skaldir/monitor.py', 'code', ''),
            _ev('src/skaldir/patterns.py', 'code', ''),
            _ev('src/skaldir/guard.py', 'code', ''),
            _ev('src/skaldir/voice.py', 'code', ''),
        ),
        'one pass over a stored trajectory: six finding categories, each with evidence',
        exit_codes=(0, 1, 2, 3, 7),
        data_schema={'type': 'object', 'additionalProperties': True},
    ),
    "voice.explain": _C(
        'SUPERVISE',
        (_ev('src/skaldir/pause.py', 'code', ''), _ev('src/skaldir/voice.py', 'code', '')),
        'why the supervisor would or would not intervene for a trajectory',
        exit_codes=(0, 1, 3, 7),
        data_schema={'type': 'object', 'additionalProperties': True},
    ),
    "voice.status": _C(
        'SUPERVISE',
        (
            _ev('src/skaldir/voice_service.py', 'code', ''),
            _ev('src/skaldir/events.py', 'code', ''),
            _ev('src/skaldir/pause.py', 'code', ''),
        ),
        'what the supervisor is watching: sessions, leases, runs, policy thresholds',
        exit_codes=(0, 1, 5, 7),
        data_schema={'type': 'object', 'additionalProperties': True},
        notes='status reads the live stores read-only',
    ),
    "voice.triage": _C(
        'SUPERVISE',
        (
            _ev('src/skaldir/voice_service.py', 'code', ''),
            _ev('src/skaldir/jev/decide.py', 'code', ''),
            _ev('src/skaldir/pause.py', 'code', ''),
        ),
        'should the Voice be woken at all? deterministic pre-filter + Jev advisory',
        exit_codes=(0, 1, 2, 3, 7),
        data_schema={'type': 'object', 'additionalProperties': True},
    ),
}


def spec_for(command: str) -> Contract:
    """The contract for a dotted command path (unregistered paths get a placeholder)."""
    entry = COMMANDS.get(command)
    if entry is None:
        return Contract(
            command=command,
            layer="(unregistered)",
            description="no contract entry yet — add it to cli_contract.COMMANDS",
            notes="unregistered: the dump's invariant test fails while this is here",
        )
    return Contract(
        command=command,
        layer=entry.layer,
        description=entry.description,
        exit_codes=entry.exit_codes,
        mutates=entry.mutates,
        requires_credentials=entry.requires_credentials,
        data_schema=entry.data_schema,
        evidence=entry.evidence,
        compat_top_level_keys=entry.compat_top_level_keys,
        notes=entry.notes,
    )


def registered(command: str) -> bool:
    return command in COMMANDS


# --------------------------------------------------------------------------- #
# the envelope (§4.2)
# --------------------------------------------------------------------------- #


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def _jsonable(value: Any) -> Any:
    """JSON-safe view of arbitrary payloads (dataclasses, Paths, sets → strings)."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    as_dict = getattr(value, "to_dict", None)
    if callable(as_dict):
        try:
            return _jsonable(as_dict())
        except Exception:  # noqa: BLE001 — a payload must never break the envelope
            return str(value)
    if hasattr(value, "__dataclass_fields__"):
        from dataclasses import asdict

        try:
            return _jsonable(asdict(value))
        except Exception:  # noqa: BLE001
            return str(value)
    return str(value)


def build_envelope(
    *,
    command: str,
    exit_code: int,
    data: Any = None,
    error: CommandError | dict | None = None,
    evidence: Iterable[dict] = (),
    warnings: Iterable[str] = (),
    next_actions: Iterable[dict] = (),
    started_at: str = "",
    duration_ms: int = 0,
    ok: bool | None = None,
    compat_top_level_keys: bool = False,
    version: str = "",
) -> dict:
    """Build the one envelope shape. Never succeeds without evidence."""
    if isinstance(evidence, str):
        # VAL-UI-003: a bare string would be iterated and char-split into single
        # characters ("s", "r", "c", ...) in the envelope — refuse it loudly instead.
        raise ValueError(
            f"evidence must be {{ref, kind, note}} dicts, not the bare string "
            f"{evidence!r}: iterating a string would char-split it into single "
            "characters in the envelope"
        )
    error_record: dict | None
    if isinstance(error, CommandError):
        error_record = error.as_dict()
        exit_code = int(error.code)
    elif isinstance(error, dict):
        error_record = dict(error)
    else:
        error_record = None

    evidence_list = [_jsonable(item) for item in evidence if item]
    if exit_code != EXIT_OK and error_record is None:
        error_record = {
            "code": EXIT_ERROR_CODES.get(exit_code, "internal"),
            "message": EXIT_NAMES.get(exit_code, "failed"),
            "hint": None,
            "retryable": False,
            "details": {},
        }

    if ok is None:
        ok = exit_code == EXIT_OK
    if ok and not evidence_list:
        # The hard rule: no command reports success it cannot back (§4.2/§4.4).
        exit_code = EXIT_INTERNAL
        ok = False
        error_record = {
            "code": "internal",
            "message": f"contract violation: {command} returned success without evidence",
            "hint": "declare evidence in cli_contract.COMMANDS or add it from the handler",
            "retryable": False,
            "details": {},
        }

    envelope = {
        "schema_version": SCHEMA_VERSION,
        "ok": bool(ok),
        "command": command,
        "godsvoice_version": version or __version__,
        "started_at": started_at or now_iso(),
        "duration_ms": int(duration_ms),
        "exit_code": int(exit_code),
        "data": _jsonable(data),
        "error": error_record,
        "evidence": evidence_list,
        "warnings": [str(item) for item in warnings],
        "next_actions": [_jsonable(item) for item in next_actions],
    }
    if compat_top_level_keys and isinstance(envelope["data"], dict):
        for key, value in envelope["data"].items():
            envelope.setdefault(key, value)
    return envelope


def emit_json_line(payload: dict, *, stream=None) -> None:
    """Write exactly one JSON document (no indent, one trailing newline)."""
    out = stream if stream is not None else sys.stdout
    text = json.dumps(payload, ensure_ascii=False, default=str) + "\n"
    try:
        out.write(text)
        out.flush()
    except (BrokenPipeError, ValueError):
        # a consumer that stopped reading (`| head`) is not a command failure
        pass


# --------------------------------------------------------------------------- #
# per-command emission context (what a handler adds on top of its contract)
# --------------------------------------------------------------------------- #

_UNSET = object()


@dataclass
class Context:
    command: str
    json_mode: bool = False
    spec: Contract | None = None
    data: Any = _UNSET
    evidence: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    next_actions: list[dict] = field(default_factory=list)
    error: CommandError | None = None

    def resolved_spec(self) -> Contract:
        return self.spec if self.spec is not None else spec_for(self.command)


_CURRENT: Context | None = None


def current() -> Context | None:
    return _CURRENT


def set_data(data: Any) -> None:
    if _CURRENT is not None:
        _CURRENT.data = data


def add_evidence(ref: str, kind: str = "code", note: str = "") -> None:
    if not ref:
        return
    if kind not in EVIDENCE_KINDS:
        kind = "code"
    if _CURRENT is not None:
        _CURRENT.evidence.append({"ref": str(ref), "kind": kind, "note": str(note)})


def warn(text: str) -> None:
    if _CURRENT is not None and str(text):
        _CURRENT.warnings.append(str(text))


def next_action(command: str, reason: str) -> None:
    if _CURRENT is not None and command:
        _CURRENT.next_actions.append({"command": str(command), "reason": str(reason)})


def fail(
    code: int,
    message: str,
    *,
    hint: str = "",
    retryable: bool = False,
    details: dict | None = None,
) -> int:
    """Record the command's verdict and return the exit code."""
    error = CommandError(code, message, hint=hint, retryable=retryable, details=details)
    if _CURRENT is not None:
        _CURRENT.error = error
    return int(code)


def blocked(message: str, *, hint: str = "", next_command: str = "", reason: str = "",
            details: dict | None = None) -> int:
    """Exit 3 with a reason and the follow-up that unblocks it."""
    if next_command:
        next_action(next_command, reason or message)
    return fail(EXIT_BLOCKED, message, hint=hint, details=details)


def needs_credentials(message: str, *, hint: str = "", details: dict | None = None) -> int:
    return fail(EXIT_NEEDS_CREDENTIALS, message, hint=hint, details=details)


def not_found(message: str, *, hint: str = "", details: dict | None = None) -> int:
    return fail(EXIT_NOT_FOUND, message, hint=hint, details=details)


def verification_failed(message: str, *, hint: str = "", details: dict | None = None) -> int:
    return fail(EXIT_VERIFICATION_FAILED, message, hint=hint, details=details)


def usage_error(message: str, *, hint: str = "") -> int:
    return fail(EXIT_USAGE, message, hint=hint)


# --------------------------------------------------------------------------- #
# the common flags and the parser annotation (§4.1)
# --------------------------------------------------------------------------- #

COMMON_HELP = {
    "--json": "machine-readable output: exactly one JSON envelope on stdout",
    "--format": "output format (text|json|jsonl); SKALDIR_FORMAT is the fallback",
    "--quiet": "suppress the human lines on stderr",
    "--no-color": "disable ANSI colour even on a TTY (NO_COLOR is honoured too)",
}


def add_common_flags(parser: argparse.ArgumentParser) -> None:
    """Attach the shared flags once — never hand-written per command."""
    existing: set[str] = set()
    for action in parser._actions:  # noqa: SLF001 — argparse has no public API for this
        existing.update(action.option_strings)
    if "--json" not in existing:
        parser.add_argument("--json", action="store_true", help=COMMON_HELP["--json"])
    if "--format" not in existing:
        parser.add_argument(
            "--format", choices=("text", "json", "jsonl"), default=None,
            help=COMMON_HELP["--format"],
        )
    if "--quiet" not in existing:
        parser.add_argument("--quiet", action="store_true", help=COMMON_HELP["--quiet"])
    if "--no-color" not in existing:
        parser.add_argument("--no-color", dest="no_color", action="store_true",
                            help=COMMON_HELP["--no-color"])


def _subparsers_action(parser: argparse.ArgumentParser):
    for action in parser._actions:  # noqa: SLF001
        if isinstance(action, argparse._SubParsersAction):  # noqa: SLF001
            return action
    return None


def walk_parsers(parser: argparse.ArgumentParser, prefix: str = ""):
    """Yield ``(dotted_path, parser)`` for the tree, root first."""
    yield prefix, parser
    action = _subparsers_action(parser)
    if action is None:
        return
    for name, child in action.choices.items():
        child_path = f"{prefix}.{name}" if prefix else str(name)
        yield from walk_parsers(child, child_path)


def leaf_commands(parser: argparse.ArgumentParser) -> list[tuple[str, argparse.ArgumentParser]]:
    """Every leaf (a parser with no subcommands) as ``(dotted_path, parser)``."""
    leaves: list[tuple[str, argparse.ArgumentParser]] = []
    for path, sub in walk_parsers(parser):
        if _subparsers_action(sub) is None:
            leaves.append((path, sub))
    return leaves


def annotate(parser: argparse.ArgumentParser) -> None:
    """Give every parser the common flags and every leaf its dotted command path.

    Called once at the end of ``build_parser()``; keeps ``--json`` on every command
    without 68 hand-written ``add_argument`` calls.
    """
    for path, sub in walk_parsers(parser):
        add_common_flags(sub)
        if path and _subparsers_action(sub) is None:
            sub.set_defaults(command_path=path)


_WARNED_FORMATS: set[str] = set()


def json_requested(args: argparse.Namespace, env: dict | None = None) -> bool:
    """``--json`` or ``--format json|jsonl`` or ``SKALDIR_FORMAT``."""
    if getattr(args, "json", False):
        return True
    fmt = str(getattr(args, "format", "") or "").strip().lower()
    if not fmt:
        environment = os.environ if env is None else env
        fmt = str(getenv("FORMAT", "", environ=environment) or "").strip().lower()
        # An unknown SKALDIR_FORMAT used to be swallowed silently; warn once so the
        # user knows the fallback actually happened (F-4). One line on stderr,
        # never fatal — the command still runs in text mode.
        if fmt and fmt not in ("json", "jsonl", "text") and fmt not in _WARNED_FORMATS:
            _WARNED_FORMATS.add(fmt)
            sys.stderr.write(f"skaldir: unknown SKALDIR_FORMAT {fmt!r} — using text output\n")
    return fmt in ("json", "jsonl")


def _option_type(action: argparse.Action) -> str:
    if isinstance(action, (argparse._StoreTrueAction, argparse._StoreFalseAction)):  # noqa: SLF001
        return "flag"
    if isinstance(action, argparse._CountAction):  # noqa: SLF001
        return "count"
    if action.choices:
        return "choice"
    kind = action.type
    if kind in (int, float, str) or callable(kind):
        name = getattr(kind, "__name__", "string")
        return {"int": "integer", "float": "number", "str": "string"}.get(name, "string")
    return "string"


def _option_default(action: argparse.Action) -> Any:
    default = action.default
    if default is None or isinstance(default, (bool, int, float, str)):
        return default
    return str(default)


def args_of(parser: argparse.ArgumentParser) -> list[dict]:
    """The command's arguments as a GUI can render them."""
    records: list[dict] = []
    for action in parser._actions:  # noqa: SLF001
        if isinstance(action, argparse._HelpAction) or isinstance(  # noqa: SLF001
            action, argparse._SubParsersAction  # noqa: SLF001
        ):
            continue
        positional = not action.option_strings
        records.append(
            {
                "name": action.dest if positional else action.option_strings[0],
                "aliases": list(action.option_strings[1:]),
                "dest": action.dest,
                "type": _option_type(action),
                "positional": positional,
                "required": bool(action.required) or positional,
                "choices": list(action.choices) if action.choices else [],
                "repeatable": bool(action.nargs in ("*", "+") or callable(action.__class__) and
                                   isinstance(action, argparse._AppendAction)),  # noqa: SLF001
                "default": _option_default(action),
                "help": str(action.help or ""),
            }
        )
    return records


def dump(parser: argparse.ArgumentParser) -> dict:
    """The whole inventory: every command, its args, its data schema (§4.5)."""
    commands: list[dict] = []
    for path, sub in leaf_commands(parser):
        spec = spec_for(path)
        record = spec.as_dict()
        record.update(
            {
                "group": path.rsplit(".", 1)[0] if "." in path else "",
                "registered": registered(path),
                "args": args_of(sub),
                "has_json_flag": "--json" in {
                    option
                    for action in sub._actions  # noqa: SLF001
                    for option in action.option_strings
                },
            }
        )
        commands.append(record)
    commands.sort(key=lambda item: item["command"])
    nodes = sum(1 for _ in walk_parsers(parser))
    return {
        "schema_version": SCHEMA_VERSION,
        "godsvoice_version": __version__,
        "root_commands": len([1 for path, _ in walk_parsers(parser) if path and "." not in path]),
        "leaf_commands": len(commands),
        "parser_nodes": nodes,
        "unregistered": [item["command"] for item in commands if not item["registered"]],
        "commands": commands,
    }


# --------------------------------------------------------------------------- #
# usage errors: argparse's 2 becomes the contract's 1
# --------------------------------------------------------------------------- #

_LAST_USAGE_ERROR: list[str] = []


def record_usage_error(prog: str, message: str) -> None:
    _LAST_USAGE_ERROR.append(f"{prog}: {message}")


def last_usage_error() -> str:
    return _LAST_USAGE_ERROR[-1] if _LAST_USAGE_ERROR else ""


class ContractArgumentParser(argparse.ArgumentParser):
    """argparse, but a usage error exits the CONTRACT's code instead of 2."""

    def error(self, message: str) -> Any:  # type: ignore[override]
        record_usage_error(self.prog, message)
        self.print_usage(sys.stderr)
        sys.stderr.write(f"{self.prog}: error: {message}\n")
        raise SystemExit(usage_exit_code(_prog_argv(self.prog)))


def _prog_argv(prog: str) -> Sequence[str]:
    """The command path out of an argparse ``prog`` (``skaldir prompt build`` → tokens)."""
    parts = str(prog or "").split()
    return parts[1:] if parts[:1] == ["skaldir"] else parts


def usage_exit_code(argv: Sequence[str]) -> int:
    """The exit code the contract declares for a command line argparse refused.

    Docs §2 (CLI-REFERENCE): *argparse usage errors exit 1
    (ContractArgumentParser.error), not 2 — a code that a command does not list
    is not part of that command's contract.* So the refusal is mapped onto the
    CONTRACT of the command that was being parsed:

    * the command declares ``EXIT_USAGE`` (the normal case) → 1;
    * it does not declare 1 but declares ``EXIT_VERIFICATION_FAILED`` → 2,
      the code the contract says that command fails with (e.g. ``tools.build``,
      ``tools.register``, ``tools.mine`` declare 0/2/… but no 1 — their argparse
      misuse used to exit an *undeclared* 1);
    * a group (``verify``), an unknown name or an unregistered command declares
      nothing → the documented default, 1.

    The longest argv prefix that resolves to a ``COMMANDS`` leaf wins, so a
    stray flag *value* (``prompt build --text hi``) never masks the command.
    """
    tokens = [str(item) for item in argv if not str(item).startswith("-")]
    for length in range(len(tokens), 0, -1):
        spec = COMMANDS.get(".".join(tokens[:length]))
        if spec is None:
            continue
        declared = tuple(getattr(spec, "exit_codes", ()) or ())
        if EXIT_USAGE in declared:
            return EXIT_USAGE
        if EXIT_VERIFICATION_FAILED in declared:
            return EXIT_VERIFICATION_FAILED
        return EXIT_USAGE
    return EXIT_USAGE


# --------------------------------------------------------------------------- #
# the one runner every command goes through
# --------------------------------------------------------------------------- #


def _last_human_line(text: str) -> str:
    """The last line that is *prose*, not the punctuation of a JSON document.

    A ``--json`` handler writes its payload to (captured) stdout, so the naive
    "last non-empty line" of that stream is ``}`` or ``],`` — which is how a
    blocked ``jev decide`` ended up reporting ``error.message: "}"``. Structural
    lines are skipped here; :func:`_contract_message` reads the payload itself.
    """
    structural = {"{", "}", "[", "]", "},", "],", "})", "{)", ",", ":"}
    lines = [
        line.strip()
        for line in str(text or "").splitlines()
        if line.strip() and line.strip() not in structural
    ]
    return lines[-1] if lines else ""


def _payload_message(parsed: Any) -> str:
    """The human sentence a structured payload carries, if it carries one."""
    if not isinstance(parsed, dict):
        return ""
    for key in ("message", "reason", "refusal_reason", "detail", "error", "status"):
        value = parsed.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            for inner_key in ("message", "reason", "detail"):
                inner = value.get(inner_key)
                if isinstance(inner, str) and inner.strip():
                    return inner.strip()
    return ""


def _contract_message(human: str, exit_code: int) -> str:
    """What the envelope's ``error.message`` says when the handler left no error.

    The handler's own payload wins over a scraped line: a blocked decision says
    ``provider_disabled``, not ``}``. Only when there is no payload does the last
    prose line (or the exit code's name) stand in.
    """
    parsed = _parse_captured(human)
    message = _payload_message(parsed)
    if message:
        return message
    return _last_human_line(human) or EXIT_NAMES.get(exit_code, "failed")


def _parse_captured(text: str) -> Any:
    stripped = str(text or "").strip()
    if not stripped:
        return _UNSET
    try:
        return json.loads(stripped)
    except (ValueError, TypeError):
        return _UNSET


def run_command(args: argparse.Namespace, func: Callable, *, json_mode: bool | None = None,
                quiet: bool | None = None) -> int:
    """Run one handler; emit the envelope in ``--json`` mode. The single entry point."""
    command = str(getattr(args, "command_path", "") or "skaldir")
    if json_mode is None:
        json_mode = json_requested(args)
    if quiet is None:
        quiet = bool(getattr(args, "quiet", False))
    spec = spec_for(command)
    if isinstance(spec.evidence, str):
        # VAL-UI-003 defence in depth: a string in the evidence slot would be
        # iterated and char-split into single characters ("s", "r", "c", ...) in
        # the envelope. Refuse it BEFORE any handler runs (no side effects) instead
        # of emitting a success envelope the evidence cannot back.
        message = (
            f"{command} declares evidence as a bare string ({spec.evidence!r}); "
            "iterating that would char-split it into single characters in the "
            "envelope — declare {ref, kind, note} dicts in the spec's evidence instead"
        )
        if not json_mode:
            sys.stderr.write(f"skaldir: {message}\n")
            return EXIT_INTERNAL
        emit_json_line(build_envelope(
            command=command,
            exit_code=EXIT_INTERNAL,
            error=CommandError(
                EXIT_INTERNAL,
                message,
                hint="fix the command's contract entry (e.g. surface_cli's "
                     "COMMAND_EVIDENCE / entry())",
                details={"command": command},
            ),
            evidence=[{
                "ref": "src/skaldir/cli_contract.py:run_command",
                "kind": "code",
                "note": "evidence-shape guard: a bare string in the evidence slot is "
                        "refused before the handler runs",
            }],
        ))
        return EXIT_INTERNAL
    ctx = Context(command=command, json_mode=json_mode, spec=spec)

    global _CURRENT
    previous = _CURRENT
    _CURRENT = ctx
    started_at = now_iso()
    started = time.time()
    captured = io.StringIO()
    exit_code = EXIT_OK
    crash: BaseException | None = None
    try:
        if json_mode:
            with contextlib.redirect_stdout(captured):
                result = func(args)
        else:
            result = func(args)
        exit_code = EXIT_OK if result is None else int(result)
    except SystemExit as exc:  # a handler that exits (usage-ish) instead of returning
        code = exc.code
        if isinstance(code, str):
            exit_code = EXIT_USAGE
            ctx.error = CommandError(EXIT_USAGE, code)
        elif code is None:
            exit_code = EXIT_OK
        else:
            exit_code = int(code)
    except CommandError as exc:
        exit_code = int(exc.code)
        ctx.error = exc
    except Exception as exc:  # noqa: BLE001 — nothing may escape as a bare traceback
        crash = exc
        exit_code = EXIT_INTERNAL
        ctx.error = CommandError(
            EXIT_INTERNAL,
            f"{type(exc).__name__}: {exc}",
            hint="this is a bug in the command, not in your input",
            details={"command": command},
        )
    finally:
        _CURRENT = previous

    if exit_code not in EXIT_NAMES:
        exit_code = EXIT_INTERNAL
        if ctx.error is None:
            ctx.error = CommandError(EXIT_INTERNAL, f"unknown exit code result for {command}")

    duration_ms = int((time.time() - started) * 1000)
    human = captured.getvalue()

    if not json_mode:
        if crash is not None:
            traceback.print_exception(type(crash), crash, crash.__traceback__, file=sys.stderr)
            sys.stderr.write(f"skaldir: {command} failed internally ({type(crash).__name__})\n")
        return exit_code

    # --json: stdout carries the envelope and nothing else.
    data = ctx.data
    if data is _UNSET:
        parsed = _parse_captured(human)
        data = None if parsed is _UNSET else parsed
    if human.strip() and not quiet:
        # the human view is diagnostics now; stdout stays a single JSON document
        sys.stderr.write(human if human.endswith("\n") else human + "\n")

    if ctx.error is None and exit_code != EXIT_OK:
        ctx.error = CommandError(exit_code, _contract_message(human, exit_code))

    evidence: list[dict] = list(spec.evidence)
    for item in ctx.evidence:
        if item not in evidence:
            evidence.append(item)

    envelope = build_envelope(
        command=command,
        exit_code=exit_code,
        data=data,
        error=ctx.error,
        evidence=evidence,
        warnings=ctx.warnings,
        next_actions=ctx.next_actions,
        started_at=started_at,
        duration_ms=duration_ms,
        compat_top_level_keys=spec.compat_top_level_keys,
    )
    emit_json_line(envelope)
    if crash is not None:
        traceback.print_exception(type(crash), crash, crash.__traceback__, file=sys.stderr)
    return int(envelope["exit_code"])


def usage_envelope(argv: Sequence[str], exit_code: int, usage: str = "") -> dict:
    """The envelope for a command that never reached a handler (bad flag, bad name)."""
    command = next((str(item) for item in argv if not str(item).startswith("-")), "skaldir")
    return build_envelope(
        command=command,
        exit_code=exit_code,
        error=CommandError(
            exit_code,
            last_usage_error() or "invalid command line",
            hint="run `skaldir --help` or `skaldir contract dump --json`",
        ),
        evidence=[{"ref": "src/skaldir/cli_contract.py", "kind": "code",
                   "note": "ContractArgumentParser.error — argparse rejected the command line "
                           "before any work happened"}],
        warnings=[usage] if usage else [],
    )


# --------------------------------------------------------------------------- #
# selftest: one honest probe per layer (§5.10)
# --------------------------------------------------------------------------- #

LAYERS: tuple[str, ...] = (
    "EXPAND",
    "ACT",
    "COMPOSE",
    "ROUTE",
    "SUPERVISE",
    "ENFORCE",
    "VERIFY",
    "PERSIST",
)


@dataclass
class Probe:
    name: str
    ok: bool
    detail: str
    evidence_ref: str = ""
    evidence_kind: str = "code"
    status: str = ""  # reachable | blocked | failed

    def as_dict(self) -> dict:
        if not self.status:
            self.status = "reachable" if self.ok else "failed"
        return {
            "name": self.name,
            "ok": bool(self.ok),
            "status": self.status,
            "detail": self.detail,
            "evidence_ref": self.evidence_ref,
        }


def _probe(name: str, fn: Callable[[], tuple[bool, str, str]], *, blocked: bool = False,
           evidence_kind: str = "code") -> Probe:
    try:
        ok, detail, ref = fn()
    except Exception as exc:  # noqa: BLE001 — a probe must report, not raise
        return Probe(name, False, f"{type(exc).__name__}: {exc}", "", evidence_kind, "failed")
    status = "reachable" if ok else ("blocked" if blocked else "failed")
    return Probe(name, bool(ok), str(detail), str(ref), evidence_kind, status)


def _runtime_dir(runtime: str | Path | None = None) -> Path:
    from .registry import default_runtime_dir

    return Path(runtime) if runtime else default_runtime_dir()


#: Leaf command paths of the whole tree, built at most **once per** :func:`run_selftest`
#: invocation. Three probes read the command surface the same way (ACT, COMPOSE,
#: VERIFY) and a full ``build_parser()`` is a ~400-leaf argparse tree: measured 42-90 ms
#: per extra build *after* the one ``cli.main`` already paid, so the same static tree was
#: being rebuilt three times inside one command. Cleared at the start of every
#: invocation — a measurement never reuses the previous run's tree.
_LEAF_PATHS: dict[str, list[str]] = {}
_LEAF_KEY = "all"


def _command_paths() -> list[str]:
    """Every leaf command path, from one ``build_parser()`` per selftest invocation.

    ``build_parser`` is read off ``skaldir.cli`` at call time, so a test that patches
    the tree between invocations is answered from the patched parser, not a stale one.
    """
    cached = _LEAF_PATHS.get(_LEAF_KEY)
    if cached is None:
        from . import cli as _cli

        cached = sorted(path for path, _sub in leaf_commands(_cli.build_parser()))
        _LEAF_PATHS[_LEAF_KEY] = cached
    return cached


def _leaf_paths(prefix: str) -> list[str]:
    """The leaf command paths starting with ``prefix`` — the one cached tree, filtered."""
    return [path for path in _command_paths() if path.startswith(prefix)]


def _probe_expand(runtime: Path) -> tuple[bool, str, list[Probe]]:
    probes: list[Probe] = []

    def _resolve() -> tuple[bool, str, str]:
        from .registry import resolve_registry

        context = resolve_registry(runtime=runtime)
        return True, f"runtime={context.runtime_dir} tools_db={context.tools_db}", \
            "src/skaldir/registry.py:123"

    probes.append(_probe("registry.resolve", _resolve))

    def _entries() -> tuple[bool, str, str]:
        from .capability import load_tool_entries

        entries = load_tool_entries(runtime)
        return True, f"{len(entries)} generated tool(s) with manifest facts", \
            "src/skaldir/capability.py:536"

    probes.append(_probe("capability.load_tool_entries", _entries))

    def _health() -> tuple[bool, str, str]:
        from .capability_health import all_states

        states = all_states(runtime)
        detail = f"{len(states)} tool(s) with a health state"
        return True, detail, "src/skaldir/capability_health.py:265"

    probes.append(_probe("capability_health.all_states", _health))

    def _db() -> tuple[bool, str, str]:
        db = runtime / "tools.db"
        exists = db.is_file()
        detail = "tools db present" if exists else "no tools db yet (read path wired)"
        return True, detail, str(db)

    probes.append(_probe("tools_db", _db, evidence_kind="file"))
    ok = all(probe.ok for probe in probes)
    return ok, "", probes


def _probe_act(runtime: Path) -> tuple[bool, str, list[Probe]]:
    probes: list[Probe] = []

    def _runners() -> tuple[bool, str, str]:
        import shutil

        from .execution import sandbox_available

        facts = {
            "dry_run": True,
            "sandbox_bwrap": bool(shutil.which("bwrap")) and sandbox_available(),
            "container_podman": bool(shutil.which("podman")),
        }
        return True, f"isolation levels: {facts}", "src/skaldir/computer_use.py:464"

    probes.append(_probe("runner_availability", _runners))

    def _executor() -> tuple[bool, str, str]:
        from .computer_use import (
            Action,
            ActionResult,
            Attempt,
            CUExecutor,
            CUPolicy,
            DryRunRunner,
            Verification,
        )

        executor = CUExecutor(CUPolicy())
        runner = DryRunRunner(behaviors={"probe": ActionResult(ok=True, stdout="probe ok")})
        attempts = [
            Attempt(
                channel="cli",
                action=Action(kind="cli", argv=["echo", "probe"], description="probe"),
                verify=Verification(expect_stdout_contains="probe ok"),
            )
        ]
        result = executor.run("selftest", attempts, runner)
        return result.status == "verified", f"in-process executor verdict: {result.status}", \
            "src/skaldir/computer_use.py:357"

    probes.append(_probe("executor_in_process", _executor))

    def _desktop() -> tuple[bool, str, str]:
        """Is a real (isolated) desktop driving reachable *from the CLI*?

        The ACT verbs are wired now, so this measures the CLI surface and the
        bridges it can really reach. A live desktop stays refused by policy — the
        sentence above the record names that, it does not hide it.
        """
        verbs = _leaf_paths("act.")
        if not verbs:
            return False, ("no live desktop bridge is wired to the CLI — no command drives a "
                           "real window/click/keystroke yet (only in-process runners are "
                           "constructible)"), "src/skaldir/computer_use.py:985"
        from .act_service import capability_report

        report = capability_report(probe_image=False)
        bridges = sorted(
            str(record.get("id"))
            for record in (report.get("bridges") or [])
            if record.get("available")
        )
        # The screenshot capability is named here (report 15 §6.4): "pixel evidence" is the one
        # ACT fact a reader cannot infer from the bridge list, and it is the capability the audit
        # called impossible. It does NOT gate the layer — a host without the GUI image still has
        # the sandbox and the fixture — so an unavailable capture is stated, not faked as red.
        by_name = {str(row.get("name")): row for row in (report.get("capabilities") or [])}
        shots = [
            f"{name}="
            + ("available" if (by_name.get(name) or {}).get("available") else "unavailable")
            for name in ("observe.screenshot", "host.session.screenshot")
            if name in by_name
        ]
        name = (
            "live desktop refused without the opt-in: "
            "`--allow-live-desktop --live-target <window>`"
        )
        return True, (f"{name} — verdict={report.get('verdict')} via "
                      f"{', '.join(bridges) or 'no available bridge'}"
                      + (f"; {', '.join(shots)}" if shots else "")), \
            "src/skaldir/act_cli.py:889"

    probes.append(_probe("live_desktop", _desktop))
    ok = all(probe.ok for probe in probes)
    return ok, "" if ok else " ".join(p.detail for p in probes if not p.ok), probes


def _probe_compose(runtime: Path) -> tuple[bool, str, list[Probe]]:
    probes: list[Probe] = []
    db = runtime / "tools.db"

    def _candidates() -> tuple[bool, str, str]:
        if not db.is_file():
            return False, f"no tools db at {db} — nothing to compose from", str(db)
        from .tools import ToolStore, compose_candidates

        store = ToolStore(str(db))
        try:
            found = compose_candidates(store, description="selftest probe", tags=None)
        finally:
            store.close()
        return True, f"compose_candidates returned {len(found)} ranked candidate(s)", \
            "src/skaldir/tools.py:469"

    probes.append(_probe("compose_candidates", _candidates, blocked=True, evidence_kind="file"))

    def _run() -> tuple[bool, str, str]:
        """The lifecycle is wired: the `compose` group creates, validates and runs.

        The old verdict ("nothing can be created/validated/run") stopped being true
        the moment ``compose_cli.register_cli`` landed in ``build_parser()`` — this
        measures the layer instead of re-asserting it.
        """
        from .compose_service import executor_report

        verbs = _leaf_paths("compose.")
        if not verbs:
            return False, ("no composite can be created, validated or run from the CLI yet — "
                           "`tools compose` only lists candidates"), "src/skaldir/tools.py:507"
        table = executor_report()
        ready = sorted(kind for kind, row in table.items() if row["executable"])
        missing = sorted(kind for kind, row in table.items() if not row["executable"])
        # "wired" and "runnable on this host" are two different facts — the probe says both
        runnable = sorted(kind for kind, row in table.items() if row.get("available"))
        not_here = sorted(
            kind for kind, row in table.items()
            if row["executable"] and not row.get("available")
        )
        gap = f"; no runner yet for {', '.join(missing)}" if missing else ""
        local = f"; not runnable on this host: {', '.join(not_here)}" if not_here else ""
        detail = (
            "composite lifecycle wired (create/validate/run/show over "
            f"{len(verbs)} CLI verb(s)); {len(ready)} executable step kind(s): "
            f"{', '.join(ready)}; {len(runnable)} runnable here{gap}{local}"
        )
        return True, detail, "src/skaldir/compose_service.py:807"

    probes.append(_probe("compose_lifecycle", _run))
    ok = all(probe.ok for probe in probes)
    return ok, "" if ok else " ".join(p.detail for p in probes if not p.ok), probes


def _probe_route(runtime: Path) -> tuple[bool, str, list[Probe]]:
    probes: list[Probe] = []

    def _flags() -> tuple[bool, str, str]:
        from .system_one_flags import load_system_one_flags, settings_path

        flags = load_system_one_flags(runtime)
        path = settings_path(runtime)
        where = str(path) if path.is_file() else f"{path} (absent)"
        if not flags.enabled:
            return False, f"System-One is off in {where} (enabled=false)", str(path)
        detail = f"flags loaded: provider={flags.provider} shadow={flags.shadow_mode}"
        return True, detail, str(path)

    probes.append(_probe("system_one_flags", _flags, blocked=True, evidence_kind="file"))

    def _advise() -> tuple[bool, str, str]:
        """Is the routing entry point reachable — measured *offline*, on purpose.

        ``advise()`` still runs through the runtime's own construction path
        (``service_for``), the configured question pack and the real gate, but the
        provider edge answers from the runtime's offline replay fixture. A
        *reachability* selftest must not spend a paid provider request, and a live
        one cannot be gated by a wall-clock budget: measured on this box, with a
        credential in ``.env``, the configured LLM adapter answered this pack in
        **2.1-2.3 s** — 70 % of ``skaldir selftest --json`` — which moved the
        command's median past the 3 s startup budget (``SELFTEST_MS``) and made the
        measurement follow the network instead of this tree. MVP/87 tuned
        ``JEV_DEFAULT_MAX_TOKENS`` (8192) for exactly that live call, so the live
        answer belongs to ``skaldir jev status`` / ``skaldir jev decide
        --provider llm``; this probe reports the mode the settings resolve to and
        the number of questions the fixture answered, instead of faking either.

        The question *set* is built with the substrate's own ``compose_questions``
        (a ``QuestionSet``), not by handing the raw ``QuestionPack`` to ``advise()``:
        measured, the raw-pack shape raises ``TypeError: 'method' object is not
        iterable`` inside the gate for an evaluator that really answers, which would
        make the wiring look broken. ``skaldir.jev.runtime.status`` measures the
        same wiring the same way.
        """
        import asyncio

        from .jev.fixture import FixtureJevEvaluator
        from .jev.runtime import service_for as build_service
        from .system_one import EvaluationContext, SystemOneState
        from .system_one_flags import load_system_one_flags
        from .system_one_questions import compose_questions, default_question_registry

        flags = load_system_one_flags(runtime)
        service = build_service(flags=flags)
        offline = build_service(flags=flags, evaluator=FixtureJevEvaluator())
        if service is None or offline is None or not service.enabled:
            return False, "service disabled: no provider adapter is wired (src/skaldir/llm.py)", \
                "src/skaldir/system_one_service.py:417"
        registry = default_question_registry()
        packs = list(registry.packs())
        pack = next(
            (item for item in packs if "rout" in str(getattr(item, "id", ""))),
            packs[0] if packs else None,
        )
        if pack is None:
            return (
                False,
                "no question pack is registered",
                "src/skaldir/system_one_questions.py:1047",
            )
        questions = compose_questions(str(pack.id), registry=registry)
        state = SystemOneState(payload={"task": "selftest probe", "options": ["a", "b"]})
        context = EvaluationContext(run_id="selftest", goal_id="selftest")
        evaluation = asyncio.run(
            offline.advise(state=state, questions=questions, context=context, use_cache=False)
        )
        if evaluation is None:
            return False, "one advise() call returned no evaluation (provider or gate refused)", \
                "src/skaldir/system_one_service.py:165"
        answered = len(getattr(evaluation, "answers", {}) or {})
        return (
            True,
            f"advise() answered {answered} of {len(questions.ids)} question(s) through the "
            f"{pack.id} pack, offline on the replay fixture: provider_mode="
            f"{service.provider_mode} — the mode the settings resolve to; the live answer is "
            f"`skaldir jev status`'s measurement, never the reachability selftest's",
            "src/skaldir/system_one_service.py:165",
        )

    probes.append(_probe("advise", _advise, blocked=True))
    blocked = [probe.detail for probe in probes if not probe.ok]
    return not blocked, " ".join(blocked), probes


def _probe_supervise(runtime: Path) -> tuple[bool, str, list[Probe]]:
    probes: list[Probe] = []
    events_db = runtime / "events.db"

    def _trajectory() -> tuple[bool, str, str]:
        if not events_db.is_file():
            detail = f"no events db at {events_db} — no trajectory to supervise"
            return False, detail, str(events_db)
        from .events import EventStore
        from .voice import read_trajectory_health

        store = EventStore(str(events_db))
        try:
            sessions = store.session_ids()
            if not sessions:
                return False, f"no session recorded in {events_db}", str(events_db)
            newest = sessions[-1]
            events = store.iter_session(newest)
        finally:
            store.close()
        until = max((event.step for event in events), default=0)
        health = read_trajectory_health(events, until_step=until)
        return True, (f"{len(sessions)} session(s); newest={newest} "
                      f"window_steps={health.window_steps}"), "src/skaldir/voice.py:264"

    probes.append(_probe("read_trajectory_health", _trajectory, blocked=True, evidence_kind="file"))

    def _guard() -> tuple[bool, str, str]:
        from .guard import EvidenceIndex, evaluate_completion  # noqa: F401

        return True, "guard wired (evaluate_completion + EvidenceIndex importable)", \
            "src/skaldir/guard.py:266"

    probes.append(_probe("guard", _guard))

    def _delegation() -> tuple[bool, str, str]:
        """Is the model's one way to delegate really on the run's own tool surface?

        Read-only: this builds the *tool surface* of a turn (no run, no thread, no provider
        call) and reads it back. Two facts are pinned and both must hold — with a provider
        client the run offers ``spawn_subagents`` under a hard role ceiling, and **without**
        one the tool is refused by name rather than offered and guaranteed to fail. A probe
        that only checked an import would stay green while the seam was unwired; this one
        fails the moment ``goal_run.turn_tools`` stops handing the tool over (audit §6b /
        report 13 §4: the module existed before it had a call site).
        """
        from .goal_run import GoalRunOptions, turn_tools
        from .runtime_delegation import GRANTABLE_ROLES, SPAWN_TOOL_NAME

        workspace = Path(runtime).expanduser()
        options = GoalRunOptions(goal="selftest: delegation surface", workspace=str(workspace),
                                 state_dir=str(workspace), session="selftest")
        bus: dict = {}
        offered = turn_tools(workspace, workspace, "selftest", bus, options,
                             delegation_provider=object())
        names = sorted(str(getattr(tool, "name", "")) for tool in offered)
        record = bus.get("delegation") or {}
        if SPAWN_TOOL_NAME not in names or not record.get("offered"):
            return False, (
                f"the run's tool surface does not offer {SPAWN_TOOL_NAME!r} "
                f"(offered: {names}) — a run cannot delegate"
            ), "src/skaldir/goal_run.py:291"
        refused_bus: dict = {}
        refused = turn_tools(workspace, workspace, "selftest", refused_bus, options)
        refused_names = sorted(str(getattr(tool, "name", "")) for tool in refused)
        if SPAWN_TOOL_NAME in refused_names:
            return False, (
                f"{SPAWN_TOOL_NAME!r} is offered without a provider client — a child could "
                "not be called, so the tool must not be offered"
            ), "src/skaldir/goal_run.py:291"
        return True, (
            f"run surface offers {SPAWN_TOOL_NAME} with a provider client (roles: "
            f"{', '.join(GRANTABLE_ROLES)}; max_children={record.get('max_children')}, "
            f"max_depth={record.get('max_depth')}, mode={record.get('mode')}) and refuses "
            "it by name without one"
        ), "src/skaldir/goal_run.py:291"

    probes.append(_probe("delegation_surface", _delegation))

    def _pause() -> tuple[bool, str, str]:
        path = runtime / "pauses.db"
        from .pause import load_strictness

        strictness = load_strictness(runtime)
        if not path.is_file():
            return False, f"no pause store at {path} — no session has been paused yet", str(path)
        import sqlite3

        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2.0)
        try:
            total = con.execute("SELECT COUNT(*) FROM pauses").fetchone()[0]
            by_status = dict(
                con.execute("SELECT status, COUNT(*) FROM pauses GROUP BY status").fetchall()
            )
        finally:
            con.close()
        return True, (f"pause store readable: {total} lease(s) {by_status}, "
                      f"strictness={strictness}"), str(path)

    probes.append(_probe("pause_store", _pause, blocked=True, evidence_kind="file"))
    ok = all(probe.ok for probe in probes)
    return ok, " ".join(probe.detail for probe in probes if not probe.ok), probes


def _probe_enforce(runtime: Path) -> tuple[bool, str, list[Probe]]:
    probes: list[Probe] = []

    def _primitives() -> tuple[bool, str, str]:
        """Which enforcement primitives this host/platform can really apply — and their state.

        Gap 11 (MVP/84): every primitive the sandbox uses is Linux-only (Landlock, seccomp,
        ``PR_SET_NO_NEW_PRIVS``, cgroup v2 ``pids.max``, the bubblewrap namespace view), so a
        macOS/Windows user used to get a *silent* downgrade. This probe is the surface: one
        ``name=status`` per primitive, and the reason for every primitive that is not
        ``enforced``. ``blocked`` (not ``failed``) when no mechanism exists at all — that is a
        platform fact, and ``run_in_sandbox`` refuses the command rather than running it
        unprotected.
        """
        from .permission_profile import default_profile
        from .security_linux import MECHANISM_NONE

        profile = default_profile()
        verdict = profile.platform_verdict()
        rows = profile.platform_coverage()
        states = ", ".join(f"{row['primitive']}={row['status']}" for row in rows)
        has_mechanism = str(verdict["mechanism"]) != MECHANISM_NONE
        detail = (
            f"mechanism={verdict['mechanism']} coverage={verdict['verdict']} "
            f"[{states}]"
        )
        if verdict["notes"]:
            detail += " — " + " | ".join(verdict["notes"])
        return has_mechanism, detail, "src/skaldir/security_linux.py:platform_coverage"

    # A host with no mechanism is *blocked*, not broken: runs are refused, never run unprotected.
    probes.append(_probe("enforcement_primitives", _primitives, blocked=True))

    def _state() -> tuple[bool, str, str]:
        from .completion import CompletionStore

        store = CompletionStore(runtime)
        state = store.state()
        metrics = store.metrics()
        return True, (f"state={state.get('state')} goal={state.get('goal') or '(none)'} "
                      f"criteria={len(state.get('dod') or [])} "
                      f"requests={metrics.get('completion_requests')}"), \
            "src/skaldir/completion.py:385"

    probes.append(_probe("completion_store", _state, evidence_kind="code"))

    def _terminal() -> tuple[bool, str, str]:
        from .completion import TERMINAL_STATES

        return True, f"{len(TERMINAL_STATES)} terminal states known", \
            "src/skaldir/completion.py:496"

    probes.append(_probe("terminal_states", _terminal))
    ok = all(probe.ok for probe in probes)
    return ok, " ".join(probe.detail for probe in probes if not probe.ok), probes


def _probe_verify(runtime: Path) -> tuple[bool, str, list[Probe]]:
    probes: list[Probe] = []

    def _plan() -> tuple[bool, str, str]:
        from .completion import plan_verification

        plan = plan_verification("the test suite passes")
        if not plan:
            return False, "plan_verification returned no plan for a 'tests' criterion", \
                "src/skaldir/completion.py:334"
        return True, f"plan_verification('the test suite passes') -> {plan}", \
            "src/skaldir/completion.py:334"

    probes.append(_probe("plan_verification", _plan))

    def _gates() -> tuple[bool, str, str]:
        from .completion import GateRunner, execute_check, run_requirement_checks  # noqa: F401

        return True, "gate runner present (execute_check + run_requirement_checks importable)", \
            "src/skaldir/completion.py:3507"

    probes.append(_probe("gate_runner", _gates))

    def _real_world() -> tuple[bool, str, str]:
        """Is a real-workspace verification reachable *from the CLI*?

        ``verify run`` executes the goal's checks against the real workspace and
        records the evidence; ``UNKNOWN`` still exits 3 and never counts as PASS.
        Until that group is wired the honest verdict stays the old sentence.
        """
        verbs = _leaf_paths("verify.")
        if not verbs:
            return False, ("no verification command runs a check against the real workspace yet "
                           "— `eval` is in-process only and cannot certify a goal"), \
                "src/skaldir/evalharness.py:358"
        return True, (f"`skaldir verify` runs the real-workspace checks "
                      f"({', '.join(verbs)}); the verdict is PASS/FAIL/UNKNOWN and UNKNOWN "
                      "exits 3 — never PASS"), "src/skaldir/verify_cli.py:627"

    probes.append(_probe("real_world_runner", _real_world))
    ok = all(probe.ok for probe in probes)
    return ok, "" if ok else " ".join(p.detail for p in probes if not p.ok), probes


def _probe_persist(runtime: Path) -> tuple[bool, str, list[Probe]]:
    probes: list[Probe] = []

    def _events() -> tuple[bool, str, str]:
        path = runtime / "events.db"
        if not path.is_file():
            return False, f"no events db at {path}", str(path)
        from .events import EventStore

        store = EventStore(str(path))
        try:
            total = store.count()
            sessions = len(store.session_ids())
        finally:
            store.close()
        return True, f"{total} event(s) in {sessions} session(s)", str(path)

    probes.append(_probe("events", _events, blocked=True, evidence_kind="file"))

    def _memory() -> tuple[bool, str, str]:
        path = runtime / "memory.db"
        if not path.is_file():
            return False, f"no memory db at {path}", str(path)
        from .incidents import IncidentStore
        from .notes import NoteStore

        incidents = IncidentStore(path)
        notes = NoteStore(path)
        try:
            return True, f"{incidents.count()} incident(s), {notes.count()} note(s)", str(path)
        finally:
            incidents.close()
            notes.close()

    probes.append(_probe("memory", _memory, blocked=True, evidence_kind="file"))

    def _runs() -> tuple[bool, str, str]:
        from .run_supervisor import describe, open_store, run_store_path

        path = run_store_path(runtime)
        if not path.is_file():
            return False, (f"no run store at {path} — no run has ever been started "
                           "(no long-run record exists)"), str(path)
        store = open_store(runtime)
        try:
            snapshot = describe(store)
        finally:
            store.close()
        return True, f"{len(snapshot.get('runs') or [])} run(s), ok={snapshot.get('ok')}", str(path)

    probes.append(_probe("run_store", _runs, blocked=True, evidence_kind="file"))

    def _sessions() -> tuple[bool, str, str]:
        """The durable session history (MVP/40) — read-only, never creates the store."""
        path = runtime / "sessions.db"
        if not path.is_file():
            return False, (f"no session store at {path} — no message history has been "
                           "persisted yet"), str(path)
        from .session_store import SessionStore

        store = SessionStore(path, create=False)
        try:
            snapshot = store.status()
            counts = snapshot["counts"]
            search = snapshot["search"]
        finally:
            store.close()
        return True, (f"{counts['sessions']} session(s), {counts['messages']} message(s), "
                      f"{counts['purged_sessions']} purged; search {search['engine']}"), str(path)

    probes.append(_probe("session_store", _sessions, blocked=True, evidence_kind="file"))
    ok = all(probe.ok for probe in probes)
    return ok, "" if ok else " ".join(p.detail for p in probes if not p.ok), probes


_PROBES: dict[str, Callable[[Path], tuple[bool, str, list[Probe]]]] = {
    "EXPAND": _probe_expand,
    "ACT": _probe_act,
    "COMPOSE": _probe_compose,
    "ROUTE": _probe_route,
    "SUPERVISE": _probe_supervise,
    "ENFORCE": _probe_enforce,
    "VERIFY": _probe_verify,
    "PERSIST": _probe_persist,
}


def cli_commands_for_layer(layer: str) -> list[str]:
    """Which CLI commands exist for a layer today (the honest surface list)."""
    return sorted(
        command for command, spec in COMMANDS.items() if spec.layer == layer
    )


def _evidence_kind(ref: str) -> str:
    """``path:line`` and ``*.py`` are code; everything else is a file reference."""
    return "code" if (":" in str(ref) or str(ref).endswith(".py")) else "file"


def run_selftest(runtime: str | Path | None = None, layers: Sequence[str] | None = None) -> dict:
    """Probe the layers and report reachability per layer. Read-only."""
    base = _runtime_dir(runtime)
    # One invocation, one command tree: the probes that read the leaf surface reuse the
    # first build (measured: three extra full parses, 42-90 ms each). Cleared here so a
    # later invocation re-reads ``build_parser()`` instead of inheriting this one's tree.
    _LEAF_PATHS.clear()
    wanted = [str(item).upper() for item in (layers or LAYERS)]
    unknown = [item for item in wanted if item not in LAYERS]
    if unknown:
        raise CommandError(
            EXIT_USAGE,
            f"unknown layer(s): {', '.join(unknown)}",
            hint=f"known layers: {', '.join(LAYERS)}",
        )
    started = time.time()
    records: list[dict] = []
    for layer in wanted:
        layer_started = time.time()
        ok, blocked_reason, probes = _PROBES[layer](base)
        statuses = {probe.status for probe in probes}
        status = "failed" if "failed" in statuses else ("blocked" if "blocked" in statuses
                                                       else "reachable")
        exit_code = EXIT_OK if ok else (
            EXIT_VERIFICATION_FAILED if status == "failed" else EXIT_BLOCKED
        )
        evidence = [
            {"ref": probe.evidence_ref, "kind": _evidence_kind(probe.evidence_ref),
             "note": f"{layer} probe: {probe.name}"}
            for probe in probes
            if probe.evidence_ref
        ]
        records.append(
            {
                "layer": layer,
                "ok": bool(ok),
                "status": status,
                "exit_code": exit_code,
                "blocked_reason": None if ok else (blocked_reason or "probe failed"),
                "cli_commands": cli_commands_for_layer(layer),
                "probes": [
                    dict(probe.as_dict(), duration_ms=int((time.time() - layer_started) * 1000))
                    for probe in probes
                ],
                "evidence": evidence,
                "duration_ms": int((time.time() - layer_started) * 1000),
            }
        )
    failed = [record["layer"] for record in records if not record["ok"]]
    return {
        "runtime": str(base),
        "ok": not failed,
        "layers": records,
        "probes": sum(len(record["probes"]) for record in records),
        "failed": len(failed),
        "failed_layers": failed,
        "duration_ms": int((time.time() - started) * 1000),
    }


__all__ = [
    "COMMANDS",
    "ERROR_CODES",
    "EXIT_BLOCKED",
    "EXIT_CONFLICT",
    "EXIT_ERROR_CODES",
    "EXIT_INTERNAL",
    "EXIT_NAMES",
    "EXIT_NEEDS_CREDENTIALS",
    "EXIT_NOT_FOUND",
    "EXIT_OK",
    "EXIT_UNKNOWN",
    "EXIT_USAGE",
    "EXIT_VERIFICATION_FAILED",
    "LAYERS",
    "SCHEMA_VERSION",
    "CommandError",
    "Context",
    "Contract",
    "ContractArgumentParser",
    "Probe",
    "add_common_flags",
    "annotate",
    "blocked",
    "build_envelope",
    "cli_commands_for_layer",
    "current",
    "dump",
    "emit_json_line",
    "fail",
    "json_requested",
    "leaf_commands",
    "next_action",
    "now_iso",
    "run_command",
    "run_selftest",
    "set_data",
    "spec_for",
    "usage_envelope",
    "walk_parsers",
    "warn",
]
