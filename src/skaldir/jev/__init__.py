"""Jev — the typed decision façade over the System-One substrate (MVP/03, phase P2).

Jev is a *fast decision/routing layer under the agent*: typesafe AI that makes six
kinds of small decision and can never make a big one. It **never** decides a goal
PASS, a permission, a security exception, or an override of the user's goal — and
that is not a promise, it is enforced at import time, at construction time and by
tests (see :mod:`skaldir.jev.rules`).

What this package is **not**: a second Jev. ``skaldir.system_one*`` already is the
substrate — 15 modules, a versioned question registry (``voice_triage_v1``,
``completion_v1``, …), the sanitizer, the policy/gate, the cache, the metrics,
the shadow harness — and its own rule §27 "no second contract". So ``skaldir.jev``
is a *façade*: typed request → registry-routed question set →
``SystemOneService.advise()`` → typed, advisory-labelled decision. The provider
edge stays where it is; the ``voice_triage`` pack is reused, never forked.

Quick start::

    from skaldir.jev import decide, selftest

    decision = decide("voice_triage", {"run_id": "r1", "goal_id": "g1",
                                       "trigger_kind": "loop",
                                       "signals": {"loop": True}})
    assert decision.authority == "advisory"      # always
    assert decision.certifies is False           # always
    assert selftest()["ok"]                      # every kind reachable, every rule tested

CLI: ``skaldir jev kinds|packs|selftest|decide|history``. Two hand-over steps
belong to the CLI owner (neither edits ``cli.py`` from here) — step 1 wires the
group, step 2 keeps the CLI's own invariant test green::

    # 1) src/skaldir/cli.py, inside build_parser(), right after the line
    #    sub = parser.add_subparsers(dest="command", required=False)
    from .jev.cli import register_cli

    register_cli(sub)

    # 2) src/skaldir/cli_contract.py: one COMMANDS entry per jev leaf —
    #    "jev.kinds", "jev.packs", "jev.selftest", "jev.decide", "jev.history" —
    #    each with layer="ROUTE", its evidence refs and its exit codes.
    #    tests/test_cli_contract_dump.py fails while they are missing
    #    ("a new command cannot ship without a contract").

See :mod:`skaldir.jev.cli` for the exit-code envelope (it adopts
``cli_contract``'s codes when that module exists) and :mod:`skaldir.jev.selftest`
for the run-level rule audit.
"""

from __future__ import annotations

# The CLI group is importable here so the product CLI needs one import and one
# call (`skaldir jev …`); importing it costs argparse and nothing else.
from .cli import register_cli
from .contracts import (
    CONTRACT_SCAN,
    JEV_ANSWERS,
    JEV_CONTRACTS,
    JEV_REQUESTS,
    JevDecision,
    JevRequestBase,
    request_for,
)
from .decide import decide, decide_async, decide_sync
from .kinds import DECISION_KINDS, PACK_FOR_KIND, ReadThresholds
from .rules import (
    RULES,
    JevRuleViolation,
    assert_decision_authority,
    caution_floor_for,
    scan_contracts,
)
from .selftest import selftest

__version__ = "1.0.0"

__all__ = [
    "CONTRACT_SCAN",
    "DECISION_KINDS",
    "JEV_ANSWERS",
    "JEV_CONTRACTS",
    "JEV_REQUESTS",
    "PACK_FOR_KIND",
    "RULES",
    "JevDecision",
    "JevRequestBase",
    "JevRuleViolation",
    "ReadThresholds",
    "__version__",
    "assert_decision_authority",
    "caution_floor_for",
    "decide",
    "decide_async",
    "decide_sync",
    "register_cli",
    "request_for",
    "scan_contracts",
    "selftest",
]
