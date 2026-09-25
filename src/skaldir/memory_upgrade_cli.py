"""``skaldir memory …`` — the typed long-term memory group (MVP/31).

Wiring — ``register_cli`` is the whole contract (the group owns its verbs *and*
their ``cli_contract.COMMANDS`` entries, like ``act_cli`` / ``host_cli``)::

    from .memory_upgrade_cli import register_cli as _register_memory
    _register_memory(sub)          # right after the other register_cli(sub) calls

Verb set::

  memory remember   <text>       store one typed memory (kind/scope/confidence/ttl)
  memory recall     <query>      ranked K with a per-hit explanation (--jev for the façade)
  memory consolidate             merge duplicates, resolve contradictions (dry run unless --apply)
  memory explain    <id>         the full record: provenance, decay, conflicts, journal
  memory forget     <id>         retract (soft by default, --hard deletes, always journaled)
  memory scope                   scopes in use, kinds, decay policy, journal integrity
  memory recall --measure        the measured precision check on the synthetic corpus

Honest exit codes (declared in ``contract_entries``):

* ``0`` — the report itself; an empty recall is data (``hits: []``), not a failure.
* ``1`` — usage: unknown kind/scope, bad confidence, missing scope id, bad id shape.
* ``2`` — ``consolidate`` found contradictions it refuses to resolve by itself
  (it reports them and asks for an owner-authored decision), or ``recall --measure``
  regressed below the declared floor.
* ``5`` — no such memory id.
* ``7`` — internal (never a silent wrong answer).

No silent writes: every mutation is journaled in the same transaction, and
``memory scope`` reports ``journal_integrity`` so the claim can be checked.
``remember``/``forget`` accept ``--dry-run`` and then write nothing at all;
``consolidate`` writes nothing without ``--apply``.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from . import cli_shared as shared
from .memory_store import (
    DEFAULT_K,
    DEFAULT_NEAR_DUP,
    KINDS,
    SOURCE_WEIGHT,
    MemoryNotFound,
    MemoryStoreError,
    MemoryValidationError,
    TypedMemoryStore,
    build_synthetic_corpus,
    measure_ranking,
    validate_embedding_provider,
)

_CONTRACT: Any = None

#: Exit codes (the product's codes; see MVP/02).
EXIT_OK = 0
EXIT_USAGE = 1
EXIT_VERIFICATION_FAILED = 2
EXIT_NOT_FOUND = 5
EXIT_INTERNAL = 7

DEFAULT_DB_NAME = "typed_memory.db"

#: Floor the measured check must not drop below. A regression fails the command
#: (exit 2) instead of printing a nicer number.
MEASURED_FLOOR_TOP1 = 0.90


def product_contract() -> Any:
    """Re-resolve the product envelope module (lazy: groups run standalone too)."""
    global _CONTRACT
    _CONTRACT = shared.product_contract()
    return _CONTRACT


# --------------------------------------------------------------------------- #
# contract entries
# --------------------------------------------------------------------------- #


def contract_entries() -> dict[str, Any]:
    """This group's ``cli_contract.COMMANDS`` entries (registered at build time)."""
    if _CONTRACT is None or not hasattr(_CONTRACT, "CommandSpec"):
        return {}
    spec = _CONTRACT.CommandSpec
    _ev = _CONTRACT._ev if hasattr(_CONTRACT, "_ev") else (
        lambda ref, kind="code", note="": {"ref": ref, "kind": kind, "note": note}
    )
    evidence = (
        _ev("src/skaldir/memory_store.py", "code", "typed store, consolidation rule, ranking"),
    )
    return {
        "memory.remember": spec(
            "PERSIST",
            evidence,
            "store one typed memory (fact/preference/decision/incident/project/entity)",
            exit_codes=(EXIT_OK, EXIT_USAGE, EXIT_INTERNAL),
            mutates=True,
            data_schema={
                "type": "object",
                "properties": {
                    "memory": {"type": "object"},
                    "written": {"type": "boolean"},
                    "db": {"type": "string"},
                    "journal_seq": {"type": ["integer", "null"]},
                },
                "required": ["memory", "written", "db"],
            },
            notes="dry run writes nothing and says written=false; provenance is always "
                  "recorded (source/refs/session)",
        ),
        "memory.recall": spec(
            "PERSIST",
            evidence,
            "rank memories for a query and explain every hit",
            exit_codes=(EXIT_OK, EXIT_USAGE, EXIT_NOT_FOUND, EXIT_INTERNAL),
            data_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "hits": {"type": "array"},
                    "count": {"type": "integer"},
                    "k": {"type": "integer"},
                    "pool": {"type": "integer"},
                    "measurement": {"type": ["object", "null"]},
                    "jev_request": {"type": "object"},
                },
                "required": ["query", "hits", "count"],
            },
            notes="an empty hit list is data with exit 0; --measure runs the synthetic "
                  "precision check and exits 2 when it drops below the declared floor; "
                  "--jev also emits the Jev memory_injection request shape (shape only — "
                  "Jev decides inject_now/defer/drop)",
        ),
        "memory.consolidate": spec(
            "PERSIST",
            evidence,
            "merge duplicates and resolve contradictions (dry run unless --apply)",
            exit_codes=(EXIT_OK, EXIT_VERIFICATION_FAILED, EXIT_NOT_FOUND, EXIT_INTERNAL),
            mutates=True,
            data_schema={
                "type": "object",
                "properties": {
                    "dry_run": {"type": "boolean"},
                    "scanned": {"type": "integer"},
                    "merged": {"type": "integer"},
                    "superseded": {"type": "integer"},
                    "flagged": {"type": "integer"},
                    "unresolved_pairs": {"type": "integer"},
                    "before": {"type": "object"},
                    "after": {"type": "object"},
                    "actions": {"type": "array"},
                    "warnings": {"type": "array"},
                },
                "required": ["dry_run", "scanned", "merged", "flagged", "unresolved_pairs"],
            },
            notes="exit 2 when a contradiction is left flagged for a human; --apply then "
                  "writes, it does not resolve the flag",
        ),
        "memory.explain": spec(
            "PERSIST",
            evidence,
            "one memory in full: provenance, decay, conflicts, journal, optional ranking",
            exit_codes=(EXIT_OK, EXIT_USAGE, EXIT_NOT_FOUND, EXIT_INTERNAL),
            data_schema={
                "type": "object",
                "properties": {
                    "memory": {"type": "object"},
                    "expired": {"type": "boolean"},
                    "decay": {"type": "object"},
                    "evidence": {"type": "object"},
                    "conflicts": {"type": "array"},
                    "journal": {"type": "array"},
                    "would_rank": {"type": "array"},
                },
                "required": ["memory", "expired", "decay", "evidence"],
            },
        ),
        "memory.forget": spec(
            "PERSIST",
            evidence,
            "retract a memory (soft by default; --hard deletes; always journaled)",
            exit_codes=(EXIT_OK, EXIT_USAGE, EXIT_NOT_FOUND, EXIT_INTERNAL),
            mutates=True,
            data_schema={
                "type": "object",
                "properties": {
                    "memory": {"type": "object"},
                    "written": {"type": "boolean"},
                    "hard": {"type": "boolean"},
                    "db": {"type": "string"},
                },
                "required": ["memory", "written"],
            },
        ),
        "memory.scope": spec(
            "PERSIST",
            evidence,
            "scopes in use, kinds per scope, decay policy and journal integrity",
            exit_codes=(EXIT_OK, EXIT_NOT_FOUND, EXIT_INTERNAL),
            data_schema={
                "type": "object",
                "properties": {
                    "db": {"type": "string"},
                    "total": {"type": "integer"},
                    "scopes": {"type": "array"},
                    "decay_policy": {"type": "object"},
                    "journal_rows": {"type": "integer"},
                    "journal_integrity": {"type": "object"},
                    "embedding_provider": {"type": "object"},
                },
                "required": ["total", "scopes", "decay_policy"],
            },
        ),
    }


def register_contract_entries() -> list[str]:
    """Add this group's ``COMMANDS`` entries (idempotent); returns the paths added."""
    contract = product_contract()
    return shared.install_contract_entries(contract_entries(), contract)


# --------------------------------------------------------------------------- #
# envelope helpers
# --------------------------------------------------------------------------- #


def _report(
    payload: Any, *, evidence: tuple[dict[str, Any], ...] = (), warnings: tuple[str, ...] = ()
) -> None:
    """Fill the shared envelope (data + evidence + warnings); harmless standalone."""
    if _CONTRACT is None:
        return
    try:
        _CONTRACT.set_data(payload)
        for item in evidence:
            _CONTRACT.add_evidence(
                str(item.get("ref", "")), str(item.get("kind", "code")), str(item.get("note", ""))
            )
        for text in warnings:
            _CONTRACT.warn(str(text))
    except Exception:  # noqa: BLE001 — an envelope problem must not break the verb
        return


def _usage(message: str, *, hint: str = "") -> int:
    if _CONTRACT is not None and hasattr(_CONTRACT, "usage_error"):
        try:
            return int(_CONTRACT.usage_error(message, hint=hint))
        except Exception:  # noqa: BLE001
            pass
    print(message, file=sys.stderr)
    return EXIT_USAGE


def _not_found(message: str, *, hint: str = "", details: dict | None = None) -> int:
    """Exit 5 through the one shared helper (never a second copy of it)."""
    if _CONTRACT is not None:
        return int(shared.not_found(message, hint=hint, details=details))
    print(message, file=sys.stderr)
    return EXIT_NOT_FOUND


def _verification_failed(message: str, *, hint: str = "", details: dict | None = None) -> int:
    """Exit 2 through the one shared helper."""
    if _CONTRACT is not None:
        return int(shared.verification_failed(message, hint=hint, details=details))
    print(message, file=sys.stderr)
    return EXIT_VERIFICATION_FAILED


def _db_path(args: argparse.Namespace) -> Path:
    """``--db`` when given, else ``<runtime>/typed_memory.db``."""
    explicit = str(getattr(args, "db", "") or "").strip()
    if explicit:
        return Path(explicit).expanduser()
    return shared.runtime_dir(args) / DEFAULT_DB_NAME


def _open(
    args: argparse.Namespace, *, create: bool = True, dry_run: bool = False
) -> TypedMemoryStore:
    """Open the store; ``create=False`` refuses a missing file (exit 5).

    ``dry_run=True`` on a path that does not exist yet uses an in-memory store:
    a dry run must not leave a file behind, not even an empty one.
    """
    path = _db_path(args)
    if not create and not path.exists():
        raise MemoryNotFound(f"no typed memory db at {path}")
    if dry_run and not path.exists():
        return TypedMemoryStore(":memory:")
    return TypedMemoryStore(path)


def _seconds(value: Any) -> float | None:
    """``--ttl 30d`` / ``2w`` / ``12h`` / ``90`` (seconds) → seconds."""
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    units = {"s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0, "w": 604800.0}
    if text[-1] in units and text[:-1].replace(".", "", 1).isdigit():
        return float(text[:-1]) * units[text[-1]]
    try:
        return float(text)
    except ValueError as exc:
        raise MemoryValidationError(
            f"cannot read a duration from {value!r} (use 30d, 12h, 90)"
        ) from exc


def _store_evidence(*refs: str) -> tuple[dict[str, Any], ...]:
    return tuple({"ref": ref, "kind": "file" if not ref.endswith(".py") else "code",
                  "note": "typed memory store"} for ref in refs if ref)


# --------------------------------------------------------------------------- #
# verbs
# --------------------------------------------------------------------------- #


def _cmd_remember(args: argparse.Namespace) -> int:
    evidence_refs = list(getattr(args, "evidence_ref", None) or [])
    provenance = {
        "source": args.source,
        "session": args.session or "",
        "note": args.note or "",
        "evidence": evidence_refs,
    }
    try:
        ttl_seconds = _seconds(args.ttl)
        half_life_seconds = _seconds(args.half_life)
        with _open(args, dry_run=bool(args.dry_run)) as store:
            memory = store.remember(
                args.text,
                kind=args.kind,
                scope=args.scope,
                scope_id=args.scope_id or "",
                subject=args.subject,
                confidence=args.confidence,
                provenance=provenance,
                ttl_seconds=ttl_seconds,
                half_life_seconds=half_life_seconds,
                contradicts=list(args.contradicts or []),
                actor=args.actor or "cli",
                dry_run=bool(args.dry_run),
                now=args.at,
            )
            journal = store.journal(memory.id, limit=1)
    except MemoryValidationError as exc:
        return _usage(
            f"refused: {exc}",
            hint="see `memory remember --help` for the accepted fields",
        )
    except MemoryStoreError as exc:
        return _not_found(str(exc))

    payload = {
        "memory": memory.to_dict(),
        "written": not bool(args.dry_run),
        "db": str(_db_path(args)),
        "journal_seq": journal[-1]["seq"] if journal else None,
        "policy": {
            "kind": memory.kind,
            "half_life_seconds": memory.half_life_seconds,
            "ttl_seconds": memory.ttl_seconds,
            "effective_score": round(
                memory.confidence * (0.5 + 0.5 * _evidence_weight(memory.provenance)), 4
            ),
        },
    }
    _report(payload, evidence=_store_evidence("src/skaldir/memory_store.py"))
    verb = "would write" if args.dry_run else "wrote"
    print(f"{verb} {memory.id} [{memory.kind}] {memory.text}")
    print(
        f"  scope={memory.scope}{':' + memory.scope_id if memory.scope_id else ''} "
        f"confidence={memory.confidence:.2f} source={memory.source or 'unset'} "
        f"half_life={memory.half_life_seconds / 86400:.0f}d "
        f"ttl={'never' if not memory.ttl_seconds else str(memory.ttl_seconds / 86400) + 'd'}"
    )
    if memory.subject:
        print(f"  subject={memory.subject!r} (duplicate/contradiction key)")
    if not memory.subject:
        print("  note: no subject — this memory can be ranked but not compared to a paraphrase")
    return EXIT_OK


def _evidence_weight(provenance: dict[str, Any]) -> float:
    from .memory_store import evidence_weight

    return evidence_weight(provenance)


def _cmd_recall(args: argparse.Namespace) -> int:
    query = " ".join(args.query or []).strip()
    if args.measure:
        # the measured check needs no query: it runs the declared corpus
        return _cmd_measure(args)
    if not query:
        return _usage("refusing an empty query: recall without a question returns noise")
    try:
        with _open(args, create=False) as store:
            pool = len(store.all())
            hits = store.recall(
                query,
                k=args.k,
                scope=args.scope or None,
                scope_id=args.scope_id or "",
                kind=args.kind,
                strict_scope=bool(args.strict_scope),
                include_superseded=bool(args.include_superseded),
            )
            offers = (
                store.jev_offers(
                    query, k=args.k, scope=args.scope or None, scope_id=args.scope_id or ""
                )
                if args.jev
                else None
            )
    except MemoryNotFound as exc:
        return _not_found(str(exc), hint="run `memory remember` first, or pass --db")
    except MemoryStoreError as exc:
        return _usage(str(exc))

    payload = {
        "query": query,
        "hits": [hit.to_dict() for hit in hits],
        "count": len(hits),
        "k": args.k,
        "pool": pool,
        "scope_filter": f"{args.scope}:{args.scope_id}" if args.scope_id else (args.scope or ""),
        "measurement": None,
    }
    if offers is not None:
        payload["jev_request"] = {
            "kind": "memory_injection",
            "goal_text": query[:500],
            "candidates": offers,
            "budget": {"max_inject": args.k, "max_tokens": 800},
            "note": "shape for Jev memory_injection; Jev decides inject_now/defer/drop",
        }
    _report(payload, evidence=_store_evidence("src/skaldir/memory_store.py"))
    if not hits:
        print(f"no memory answers {query!r} (0 of {pool} rows in scope)")
        return EXIT_OK
    print(f"{len(hits)} hit(s) of {pool} rows for {query!r}:")
    for rank, hit in enumerate(hits, start=1):
        print(f"{rank}. [{hit.memory_id}] {hit.score:.4f} {hit.text}")
        print(f"     {hit.explanation}")
        if hit.matched_terms:
            print(f"     matched: {', '.join(hit.matched_terms)}")
        for conflict in hit.contradicts:
            winner = f" — would keep {conflict['would_win']}" if conflict.get("would_win") else ""
            print(f"     conflict ({conflict['status']}): {conflict['reason']} -> "
                  f"{conflict['id']}{winner} ({conflict['basis']})")
    return EXIT_OK


def _cmd_consolidate(args: argparse.Namespace) -> int:
    try:
        with _open(args, create=False) as store:
            report = store.consolidate(
                apply=bool(args.apply), near_dup=args.near_dup, actor=args.actor or "cli"
            )
    except MemoryNotFound as exc:
        return _not_found(str(exc), hint="there is nothing to consolidate yet")
    except MemoryStoreError as exc:
        return _usage(str(exc))

    payload = report.to_dict()
    payload["db"] = str(_db_path(args))
    payload["rule"] = (
        "owner-authored wins; else weighted score (confidence x evidence) by >= 0.20; "
        "else confidence by >= 0.15; else newest by >= 60s; else flag for the agent"
    )
    _report(payload, evidence=_store_evidence("src/skaldir/memory_store.py"),
            warnings=tuple(report.warnings))
    mode = "applied" if args.apply else "dry run (nothing written — pass --apply)"
    print(f"consolidate {mode}: scanned {report.scanned}, merged {report.merged}, "
          f"superseded {report.superseded}, flagged {report.flagged}, "
          f"unresolved pairs {report.unresolved_pairs}")
    print(f"  before: {report.before}")
    if args.apply:
        print(f"  after : {report.after}")
    for action in report.actions:
        if action["action"] == "merge":
            print(f"  merge      {action['merged']} -> {action['kept']}  ({action['reason']})")
        elif action["action"] == "supersede":
            print(f"  supersede  {action['superseded']} -> {action['kept']}  ({action['basis']})")
        elif action["action"] == "flag":
            print(f"  FLAG       {action['ids'][0]} vs {action['ids'][1]}  ({action['reason']})")
        elif action["action"] == "already-flagged":
            print(f"  still-open {action['ids'][0]} vs {action['ids'][1]}  ({action['reason']})")
    for warning in report.warnings:
        print(f"  ! {warning}", file=sys.stderr)
    if report.needs_attention:
        return _verification_failed(
            f"{report.unresolved_pairs} contradiction(s) need an owner-authored decision",
            hint="read both sides with `memory explain <id>` and store the answer with "
                 "`memory remember --source owner`",
            details={"unresolved_pairs": report.unresolved_pairs},
        )
    return EXIT_OK


def _cmd_explain(args: argparse.Namespace) -> int:
    try:
        with _open(args, create=False) as store:
            payload = store.explain(args.id, query=args.query or "")
    except MemoryNotFound as exc:
        return _not_found(str(exc), hint="ids come from `memory recall` or `memory scope`")
    payload["db"] = str(_db_path(args))
    _report(payload, evidence=_store_evidence("src/skaldir/memory_store.py"))
    memory = payload["memory"]
    print(f"{memory['id']} [{memory['kind']}/{memory['status']}] {memory['text']}")
    print(f"  subject={memory['subject'] or '(none)'} confidence={memory['confidence']}")
    print(f"  scope={memory['scope']}{':' + memory['scope_id'] if memory['scope_id'] else ''} "
          f"age={payload['age_days']}d expired={payload['expired']}")
    print(f"  decay: half_life={payload['decay']['half_life_days']}d "
          f"ttl={payload['decay']['ttl_days']}d now={payload['decay']['factors']['now']}")
    print(f"  evidence: source={payload['evidence']['source']} "
          f"evidence_weight={payload['evidence']['evidence_weight']} "
          f"effective={payload['evidence']['effective_score']}")
    print(f"  provenance: {json.dumps(payload['evidence']['provenance'], sort_keys=True)}")
    if memory["superseded_by"]:
        print(f"  superseded by {memory['superseded_by']}")
    if memory["merged_into"]:
        print(f"  merged into {memory['merged_into']}")
    for conflict in payload["conflicts"]:
        print(f"  conflict ({conflict['status']}): {conflict['reason']} -> {conflict['id']}")
    for row in payload["journal"]:
        print(f"  journal {row['seq']} {row['action']} -> {row['related_id'] or '-'} "
              f"{json.dumps(row['detail'], sort_keys=True)}")
    if payload["would_rank"]:
        print("  would rank for that query:")
        for rank, hit in enumerate(payload["would_rank"], start=1):
            print(f"    {rank}. {hit['id']} {hit['score']:.4f} {hit['text'][:60]}")
    return EXIT_OK


def _cmd_forget(args: argparse.Namespace) -> int:
    try:
        with _open(args, create=False, dry_run=bool(args.dry_run)) as store:
            memory = store.forget(
                args.id,
                actor=args.actor or "cli",
                reason=args.reason or "",
                hard=bool(args.hard),
                dry_run=bool(args.dry_run),
            )
            payload = {
                "memory": memory.to_dict(),
                "written": not bool(args.dry_run),
                "hard": bool(args.hard),
                "db": str(_db_path(args)),
            }
    except MemoryNotFound as exc:
        return _not_found(str(exc))
    except MemoryStoreError as exc:
        return _usage(str(exc))
    _report(payload, evidence=_store_evidence("src/skaldir/memory_store.py"))
    if args.dry_run:
        print(f"would forget {memory.id} (nothing written)")
    elif args.hard:
        print(f"deleted {memory.id} (row removed, journal kept)")
    else:
        print(f"forgot {memory.id}: status is now 'forgotten' — it is never retrieved again, "
              "and `memory explain` keeps the history")
    return EXIT_OK


def _cmd_scope(args: argparse.Namespace) -> int:
    try:
        with _open(args, create=False) as store:
            report = store.scope_report()
            integrity = store.journal_consistency()
            counts = store.status_counts()
    except MemoryNotFound as exc:
        return _not_found(str(exc), hint="no typed memories have been written yet")
    payload = dict(report)
    payload["status_counts"] = counts
    payload["journal_integrity"] = integrity
    payload["embedding_provider"] = validate_embedding_provider()
    if args.scope:
        payload["scopes"] = [item for item in payload["scopes"] if item["scope"] == args.scope]
    _report(payload, evidence=_store_evidence("src/skaldir/memory_store.py"))
    print(f"{payload['total']} typed memories in {payload['db']}")
    print(f"  statuses: {counts}")
    for item in payload["scopes"]:
        key = f"{item['scope']}:{item['scope_id']}" if item["scope_id"] else item["scope"]
        print(f"  scope {key}: {item['total']} memories, kinds {item['kinds']}, "
              f"expired {item['expired']}")
    policy = {k: (v["half_life_days"], v["ttl_days"]) for k, v in payload["decay_policy"].items()}
    print(f"  decay policy (half-life, ttl in days): {policy}")
    print(f"  journal: {integrity['journal_rows']} rows, integrity ok={integrity['ok']}")
    if not integrity["ok"]:
        for item in integrity["missing"]:
            print(f"  ! {item['id']} has no {item['missing']} journal row", file=sys.stderr)
    provider = payload["embedding_provider"]
    print(f"  embedding: {provider['provider']} dim={provider['dim']} "
          f"usable={provider['usable']} semantic={provider['semantic']} "
          f"(paraphrase probe {provider['lexical_probe']['paraphrase_no_shared_tokens']})")
    return EXIT_OK


def _cmd_measure(args: argparse.Namespace) -> int:
    """The measured check: synthetic corpus, consolidation, ranking vs baselines."""
    with tempfile.TemporaryDirectory(prefix="skaldir-memory-corpus-") as tmp:
        path = Path(tmp) / "synthetic.db"
        with TypedMemoryStore(path) as store:
            plan = build_synthetic_corpus(store)
            consolidation = store.consolidate(apply=True)
            measurement = measure_ranking(store, plan.queries, k=args.k)
    within_floor = measurement.to_dict()["precision_at_1"] >= MEASURED_FLOOR_TOP1
    payload = {
        "synthetic": True,
        "corpus": plan.to_dict(),
        "consolidation": consolidation.to_dict(),
        "measurement": measurement.to_dict(),
        "within_floor": within_floor,
        "floor": MEASURED_FLOOR_TOP1,
        "honesty": [
            "the corpus and its labels are authored by this repo, not field data",
            "the numbers are an upper bound on a real store of the same shape",
            "self-authored labels cannot find the queries nobody thought of",
        ],
    }
    _report(payload, evidence=_store_evidence("src/skaldir/memory_store.py"),
            warnings=tuple(consolidation.warnings))
    measured = measurement.to_dict()
    print(f"synthetic corpus: {plan.stored} memories, consolidation "
          f"merged={consolidation.merged} superseded={consolidation.superseded} "
          f"flagged={consolidation.flagged} unresolved={consolidation.unresolved_pairs}")
    print(f"  before {consolidation.before} -> after {consolidation.after}")
    print(f"ranking over {measured['queries']} labeled queries (k={measured['k']}):")
    print(f"  ours          precision@1 {measured['precision_at_1']:.4f} "
          f"precision@k {measured['precision_at_k']:.4f} mrr {measured['mrr']:.4f}")
    print(f"  flat list     precision@1 {measured['baseline_newest']['precision_at_1']:.4f}")
    print(f"  lexical only  precision@1 {measured['baseline_lexical']['precision_at_1']:.4f}")
    for failure in measured["failures"]:
        print(f"  MISS: {failure['query']!r} — {failure['note']}")
    if not within_floor:
        return _verification_failed(
            f"precision@1 {measured['precision_at_1']:.4f} is below the floor "
            f"{MEASURED_FLOOR_TOP1}",
            hint="the ranking changed for the worse; re-read MVP/31 before shipping",
            details={"measured": measured["precision_at_1"], "floor": MEASURED_FLOOR_TOP1},
        )
    return EXIT_OK


# --------------------------------------------------------------------------- #
# parser
# --------------------------------------------------------------------------- #


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--db",
        default="",
        help=f"typed memory db (default: <runtime>/{DEFAULT_DB_NAME})",
    )
    parser.add_argument(
        "--runtime",
        default="",
        help="runtime dir override (used to resolve the default db path)",
    )
    shared.share_common_flags(parser)


def _add_memory_verbs(sub: Any) -> None:
    """Add the six verbs to a subparsers action (shared by both parsers)."""
    p_remember = sub.add_parser("remember", help="store one typed memory")
    p_remember.add_argument("text")
    p_remember.add_argument("--kind", choices=KINDS, default="fact")
    p_remember.add_argument("--scope", choices=("global", "project", "session"), default="global")
    p_remember.add_argument("--scope-id", dest="scope_id", default="")
    p_remember.add_argument("--subject", default=None, help="comparison key (else 'subject: text')")
    p_remember.add_argument("--confidence", type=float, default=0.6)
    p_remember.add_argument(
        "--source", choices=tuple(SOURCE_WEIGHT), default="agent",
        help="where it came from; owner is authoritative in a conflict",
    )
    p_remember.add_argument("--session", default="")
    p_remember.add_argument("--note", default="")
    p_remember.add_argument(
        "--evidence-ref", dest="evidence_ref", action="append", default=None,
        help="repeatable: a file/run/commit this memory rests on",
    )
    p_remember.add_argument("--ttl", default=None, help="expiry, e.g. 30d / 12h / 0 (never)")
    p_remember.add_argument("--half-life", dest="half_life", default=None, help="e.g. 180d")
    p_remember.add_argument("--contradicts", action="append", default=None, help="repeatable id")
    p_remember.add_argument("--actor", default="")
    p_remember.add_argument("--at", type=float, default=None, help="created_at (epoch; tests)")
    p_remember.add_argument("--dry-run", dest="dry_run", action="store_true")
    _add_common(p_remember)
    p_remember.set_defaults(func=_cmd_remember)

    p_recall = sub.add_parser("recall", help="rank memories for a query, with explanations")
    p_recall.add_argument("query", nargs="*")
    p_recall.add_argument("--k", type=int, default=DEFAULT_K)
    p_recall.add_argument("--scope", default="")
    p_recall.add_argument("--scope-id", dest="scope_id", default="")
    p_recall.add_argument("--kind", choices=KINDS, default=None)
    p_recall.add_argument("--strict-scope", dest="strict_scope", action="store_true")
    p_recall.add_argument("--include-superseded", dest="include_superseded", action="store_true")
    p_recall.add_argument("--jev", action="store_true", help="also emit the Jev request shape")
    p_recall.add_argument("--measure", action="store_true", help="the measured precision check")
    _add_common(p_recall)
    p_recall.set_defaults(func=_cmd_recall)

    p_cons = sub.add_parser("consolidate", help="merge duplicates, resolve contradictions")
    p_cons.add_argument("--apply", action="store_true", help="write (default is a dry run)")
    p_cons.add_argument("--near-dup", dest="near_dup", type=float, default=DEFAULT_NEAR_DUP)
    p_cons.add_argument("--actor", default="")
    _add_common(p_cons)
    p_cons.set_defaults(func=_cmd_consolidate)

    p_explain = sub.add_parser("explain", help="one memory in full")
    p_explain.add_argument("id")
    p_explain.add_argument("--query", default="", help="also show where it ranks for this query")
    _add_common(p_explain)
    p_explain.set_defaults(func=_cmd_explain)

    p_forget = sub.add_parser("forget", help="retract a memory")
    p_forget.add_argument("id")
    p_forget.add_argument("--reason", default="")
    p_forget.add_argument("--hard", action="store_true", help="delete the row (journal stays)")
    p_forget.add_argument("--actor", default="")
    p_forget.add_argument("--dry-run", dest="dry_run", action="store_true")
    _add_common(p_forget)
    p_forget.set_defaults(func=_cmd_forget)

    p_scope = sub.add_parser("scope", help="scopes, kinds, decay policy, journal integrity")
    p_scope.add_argument("--scope", choices=("global", "project", "session"), default=None)
    _add_common(p_scope)
    p_scope.set_defaults(func=_cmd_scope)


def build_memory_parser(subparsers: Any) -> Any:
    """Create the ``memory`` group under ``subparsers`` and return it.

    No group-level ``--db``: argparse lets a subparser's own default overwrite the
    parent's value, so the flag lives on the leaves only (one owner, no shadowing).
    """
    parser = subparsers.add_parser(
        "memory",
        help="typed long-term memory: remember/recall/consolidate/explain/forget/scope",
    )
    group = parser.add_subparsers(dest="memory_command", required=True)
    _add_memory_verbs(group)
    return parser


def register_cli(subparsers: Any) -> Any:
    """The CLI envelope contract: ``register_cli(subparsers) -> parser``.

    Idempotent: called twice on the same handle it returns the existing ``memory``
    group instead of letting argparse raise ``conflicting subparser``. It also
    installs this group's ``cli_contract.COMMANDS`` entries, so a wired-in group
    never ships without a contract.
    """
    existing = getattr(subparsers, "choices", {}).get("memory")
    if existing is not None:
        register_contract_entries()
        return existing
    parser = build_memory_parser(subparsers)
    register_contract_entries()
    return parser


def build_parser() -> argparse.ArgumentParser:
    """A standalone parser with just this group's verbs (``python -m …`` / tests)."""
    contract = product_contract()
    base = (
        contract.ContractArgumentParser
        if contract is not None and hasattr(contract, "ContractArgumentParser")
        else argparse.ArgumentParser
    )
    parser = base(prog="skaldir", description="typed long-term memory (MVP/31)")
    sub = parser.add_subparsers(dest="command", required=True)
    _add_memory_verbs(sub)
    if contract is not None and hasattr(contract, "annotate"):
        contract.annotate(parser)
        register_contract_entries()
    return parser


def main(argv: list[str] | None = None) -> int:
    """``skaldir memory …`` — returns the exit code, never raises for a request."""
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return EXIT_OK if exc.code in (0, None) else EXIT_USAGE
    runner = getattr(_CONTRACT, "run_command", None) if _CONTRACT is not None else None
    if runner is not None:
        return int(runner(args, args.func))
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover - manual entry
    raise SystemExit(main(sys.argv[1:]))
