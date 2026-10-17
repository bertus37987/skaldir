"""CLI verbs for the knowledge store (INFO-01..04).

Registers ``skaldir knowledge list|show|search|add`` so the knowledge base is
reachable from the product surface, not just from tests.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

try:
    from . import cli_contract as _CONTRACT
except Exception:  # noqa: BLE001
    _CONTRACT = None


def contract_entries() -> dict[str, Any]:
    """This group's ``cli_contract.COMMANDS`` entries (registered at build time)."""
    if _CONTRACT is None or not hasattr(_CONTRACT, "CommandSpec"):
        return {}
    spec = _CONTRACT.CommandSpec
    _ev = _CONTRACT._ev if hasattr(_CONTRACT, "_ev") else (
        lambda ref, kind="code", note="": {"ref": ref, "kind": kind, "note": note}
    )
    evidence = (
        _ev(
            "src/skaldir/knowledge.py", "code",
            "knowledge store with FTS5 search and versioned reads"
        ),
    )
    return {
        "knowledge.list": spec(
            "PERSIST",
            evidence,
            "list knowledge entries with optional scope/kind/status filters",
            exit_codes=(0, 1, 7),
            data_schema={
                "type": "object",
                "properties": {
                    "entries": {"type": "array"},
                },
                "required": ["entries"],
            },
        ),
        "knowledge.show": spec(
            "PERSIST",
            evidence,
            "show one knowledge entry by id",
            exit_codes=(0, 1, 5, 7),
            data_schema={
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                    "revision": {"type": "string"},
                },
                "required": ["id", "revision"],
            },
        ),
        "knowledge.search": spec(
            "PERSIST",
            evidence,
            "search the knowledge base by query with scope/kind/status filters",
            exit_codes=(0, 1, 7),
            data_schema={
                "type": "object",
                "properties": {
                    "matches": {"type": "array"},
                    "truncated": {"type": "boolean"},
                },
                "required": ["matches", "truncated"],
            },
        ),
        "knowledge.add": spec(
            "PERSIST",
            evidence,
            "add or update a knowledge entry",
            exit_codes=(0, 1, 7),
            mutates=True,
            data_schema={
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "revision": {"type": "string"},
                },
                "required": ["id", "revision"],
            },
        ),
    }


def register_contract_entries() -> list[str]:
    """Add this group's ``COMMANDS`` entries (idempotent); returns the paths added."""
    from . import cli_contract as contract
    from . import cli_shared

    return cli_shared.install_contract_entries(contract_entries(), contract)


def register_cli(sub: Any) -> None:
    register_contract_entries()
    p_knowledge = sub.add_parser("knowledge", help="Markdown knowledge base with metadata")
    knowledge_sub = p_knowledge.add_subparsers(dest="knowledge_cmd", required=True)

    p_list = knowledge_sub.add_parser("list", help="list knowledge entries")
    p_list.add_argument("--scope", default="", help="filter by scope")
    p_list.add_argument("--kind", default="", help="filter by kind")
    p_list.add_argument("--status", default="", help="filter by status")
    p_list.add_argument("--json", action="store_true", help="JSON output")

    p_show = knowledge_sub.add_parser("show", help="show one knowledge entry")
    p_show.add_argument("id", help="entry id")
    p_show.add_argument("--json", action="store_true", help="JSON output")

    p_search = knowledge_sub.add_parser("search", help="search the knowledge base")
    p_search.add_argument("query", help="search query")
    p_search.add_argument("--scope", default="", help="filter by scope")
    p_search.add_argument("--kinds", nargs="*", default=None, help="filter by kinds")
    p_search.add_argument("--statuses", nargs="*", default=None, help="filter by statuses")
    p_search.add_argument("--limit", type=int, default=5, help="max results")
    p_search.add_argument("--json", action="store_true", help="JSON output")

    p_add = knowledge_sub.add_parser("add", help="add or update a knowledge entry")
    p_add.add_argument("id", help="entry id")
    p_add.add_argument("--title", required=True, help="entry title")
    p_add.add_argument("--kind", default="fact", help="entry kind")
    p_add.add_argument("--scope", default="global", help="entry scope")
    p_add.add_argument("--status", default="candidate", help="entry status")
    p_add.add_argument("--body", default="", help="entry body text")
    p_add.add_argument("--body-file", default="", help="read body from file")
    p_add.add_argument("--valid-until", default="", help="expiry timestamp")
    p_add.add_argument("--sources", nargs="*", default=None, help="evidence source ids")
    p_add.add_argument("--json", action="store_true", help="JSON output")

    for sub_p in (p_list, p_show, p_search, p_add):
        sub_p.set_defaults(func=_cmd_knowledge)
    p_knowledge.set_defaults(func=_cmd_knowledge)


def _set_contract_data(payload: dict[str, Any]) -> None:
    if _CONTRACT is not None and hasattr(_CONTRACT, "set_data"):
        _CONTRACT.set_data(payload)
        if hasattr(_CONTRACT, "add_evidence"):
            _CONTRACT.add_evidence(
                [{"ref": "src/skaldir/knowledge.py", "kind": "code", "note": "KnowledgeStore"}]
            )


def _in_envelope_mode() -> bool:
    from . import cli_shared

    return bool(cli_shared.envelope_mode())


def _cmd_knowledge(args: argparse.Namespace) -> int:
    from .knowledge import KnowledgeEntry, KnowledgeStore

    store = KnowledgeStore()
    cmd = args.knowledge_cmd

    if cmd == "list":
        entries = store.list_entries(
            scope=args.scope, kind=args.kind, status=args.status
        )
        _set_contract_data({"entries": entries})
        if args.json:
            if not _in_envelope_mode():
                print(json.dumps(entries, indent=2))
        else:
            for e in entries:
                stale = " STALE" if e.get("valid_until", "") and e["valid_until"] < "2026" else ""
                print(
                    f"  {e['id']:30s} {e['kind']:12s} {e['status']:10s} {e['revision'][:12]}{stale}"
                )

        return 0

    if cmd == "show":
        entry = store.get(args.id)
        if entry is None:
            print(f"not found: {args.id}", file=sys.stderr)
            return 5
        data = entry.to_dict()
        data["body"] = entry.body
        _set_contract_data(data)
        if args.json:
            if not _in_envelope_mode():
                print(json.dumps(data, indent=2))
        else:
            print(f"id: {entry.id}")
            print(f"title: {entry.title}")
            print(f"kind: {entry.kind}  scope: {entry.scope}  status: {entry.status}")
            print(f"revision: {entry.revision}")
            if entry.is_stale:
                print("WARNING: this entry is stale")
            print(f"\n{entry.body}")
        return 0

    if cmd == "search":
        result = store.search(
            args.query,
            scope=args.scope,
            kinds=args.kinds,
            statuses=args.statuses,
            limit=args.limit,
        )
        _set_contract_data(result)
        if args.json:
            if not _in_envelope_mode():
                print(json.dumps(result, indent=2))
        else:
            for m in result["matches"]:
                stale = " STALE" if m.get("is_stale") else ""
                print(f"  {m['id']:30s} {m['kind']:12s} {m['status']:10s} "
                      f"lines {m['start_line']}-{m['end_line']}{stale}")
                if m.get("snippet"):
                    print(f"    {m['snippet'][:80]}")
            if result["truncated"]:
                print("  (truncated)")
        return 0

    if cmd == "add":
        body = args.body
        if args.body_file:
            body = Path(args.body_file).read_text(encoding="utf-8")
        entry = KnowledgeEntry(
            id=args.id,
            title=args.title,
            kind=args.kind,
            scope=args.scope,
            status=args.status,
            body=body,
            valid_until=args.valid_until,
            sources=args.sources or [],
        )
        stored = store.upsert(entry)
        payload = stored.to_dict()
        _set_contract_data(payload)
        if args.json:
            if not _in_envelope_mode():
                print(json.dumps(payload, indent=2))
        else:
            print(f"stored: {stored.id} (revision {stored.revision[:16]}…)")
        return 0

    return 1

