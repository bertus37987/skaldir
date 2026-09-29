"""Command line interface: ``skaldir <command>``.

Commands: init, pact-show, ledger-show, demo, eval, scan, prompts list,
memory-stats, memory-search, nightshift, metrics, skills list/mine/validate/
promote/demote/show/export, tools *, registry status/migrate, workflows
list/show/run, mcp serve, cu check/demo, completion status/history/begin/override,
vault *, spend *, desktop *, doctor, and **run** — the goal-locked loop
(``skaldir run "<goal>"``: plan → turns through the agent loop → the declared
checks really run → the completion gate → continue on failure → a verdict only on
``VERIFY PASS``; the goal survives a kill through ``skaldir run --resume``).

Machine-readable contract — one shape, one exit-code set, one self-description
(see ``cli_contract``): every command takes ``--json`` (exactly one envelope on
stdout, human text on stderr), exits with the shared codes (0 ok / 1 usage /
2 verification failed / 3 blocked / 4 needs credentials / 5 not found / 6 conflict /
7 internal / 8 unknown), and describes itself through ``skaldir contract dump``.
``skaldir selftest`` reports which of the eight layers is reachable — a layer that
is not wired says so instead of pretending.
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path

import yaml

from . import __version__, cli_contract
from .computer_use import (
    Action,
    ActionResult,
    Attempt,
    CUExecutor,
    CUPolicy,
    DryRunRunner,
    Verification,
    order_attempts,
    runner_availability,
)
from .demo import run_demo
from .env import getenv, host_getenv
from .evalharness import render_report, run_eval
from .events import EventStore
from .gates import run_gates
from .incidents import IncidentStore
from .ledger import Ledger
from .memory import ENFORCEMENT_GAPS, retention_report, schema_axes
from .metrics import compute_metrics
from .nightshift import (
    build_episode_summary,
    consolidate,
    consolidate_research_notes,
    propose_solved_incidents,
)
from .notes import NoteStore
from .pact import Pact
from .prompts import REQUIRED_IDS, PromptRegistry
from .retrieval import find_evidence
from .secretscan import scan
from .skills import (
    STATUS_DEMOTED,
    STATUS_PROMOTED,
    SkillStore,
    draft_skill,
    is_valid_skill_body,
    mine_tool_patterns,
    validate_candidate,
)
from .tools import (
    KIND_COMPOSITION,
    STATUS_APPROVED,
    STATUS_REVOKED,
    ToolSpec,
    ToolStore,
    compose_candidates,
    propose_tool,
    validate_composition,
)
from .toolscan import scan_tool_file
from .treasury import (
    PRESETS,
    SpendLedger,
    TreasuryState,
    Vault,
)

#: The exit-code set (§4.3) re-exported as part of the CLI surface. Never a bare number
#: at a call site, and a caller may import them from here.
EXIT_OK = cli_contract.EXIT_OK
EXIT_USAGE = cli_contract.EXIT_USAGE
EXIT_VERIFICATION_FAILED = cli_contract.EXIT_VERIFICATION_FAILED
EXIT_BLOCKED = cli_contract.EXIT_BLOCKED
EXIT_NEEDS_CREDENTIALS = cli_contract.EXIT_NEEDS_CREDENTIALS
EXIT_NOT_FOUND = cli_contract.EXIT_NOT_FOUND
EXIT_CONFLICT = cli_contract.EXIT_CONFLICT
EXIT_INTERNAL = cli_contract.EXIT_INTERNAL
EXIT_UNKNOWN = cli_contract.EXIT_UNKNOWN

DEFAULT_DIR = ".godsv"


def _runtime_file(name: str) -> str:
    """A runtime file (events.db, memory.db, treasury.db, vault.json) under the ONE runtime dir.

    These arguments used to default to `.godsv/<file>` — relative to the CALLER'S cwd. Run from
    anywhere but the project root, `skaldir metrics` answered "no events db at
    .godsv/events.db" while the runtime actually lives in $SKALDIR_HOME / $HERMES_HOME/godsv
    (measured 17.09.). An explicit --db/--events/--memory still wins; `.godsv` stays the default
    only for the project-scaffolding commands (init/pact-show/ledger-show).
    """
    return str(_gv_runtime() / name)


def _cmd_init(args: argparse.Namespace) -> int:
    target = Path(args.dir)
    target.mkdir(parents=True, exist_ok=True)
    pact_path = target / "pact.yaml"
    ledger_path = target / "ledger.yaml"
    if pact_path.exists() and not args.force:
        print(f"exists: {pact_path} (use --force to overwrite)")
        return 1
    pact = Pact.example()
    pact.validate()
    pact.to_yaml(pact_path)
    Ledger.from_pact(pact).save(ledger_path)
    print(f"created {pact_path}")
    print(f"created {ledger_path}")
    return 0


def _cmd_pact_show(args: argparse.Namespace) -> int:
    path = Path(args.dir) / "pact.yaml"
    if not path.is_file():
        print(f"no pact at {path}")
        return cli_contract.not_found(
            f"no pact at {path}",
            hint="create the scaffold first: skaldir init --dir .godsv",
            details={"path": str(path)},
        )
    pact = Pact.from_yaml(path)
    print(yaml.safe_dump(pact.to_dict(), sort_keys=False, allow_unicode=True))
    return 0


def _cmd_ledger_show(args: argparse.Namespace) -> int:
    path = Path(args.dir) / "ledger.yaml"
    if not path.is_file():
        print(f"no ledger at {path}")
        return cli_contract.not_found(
            f"no ledger at {path}",
            hint="create the scaffold first: skaldir init --dir .godsv",
            details={"path": str(path)},
        )
    ledger = Ledger.load(path)
    print(ledger.render_markdown())
    return 0


def _cmd_demo(args: argparse.Namespace) -> int:
    run_demo(db_path=args.db, verbose=True)
    return 0


def _cmd_eval(args: argparse.Namespace) -> int:
    results = run_eval()
    cli_contract.set_data(
        {
            "episodes": [
                {
                    "name": result.name,
                    "passed": bool(result.passed),
                    "hints": len(result.hint_kinds),
                    "signals": list(result.signal_kinds),
                }
                for result in results
            ],
            "passed": sum(1 for result in results if result.passed),
            "total": len(results),
            "scope": "in-process episodes",
        }
    )
    cli_contract.add_evidence(
        "src/skaldir/evalharness.py:358", "code", "run_eval() — deterministic in-process episodes"
    )
    cli_contract.warn(
        "in-process episodes only: no desktop, no window, no user flow was touched — "
        "this is not real-world verification and cannot PASS a goal"
    )
    print(render_report(results))
    print("scope: in-process episodes (deterministic, CI-useful) — not real-world verification")
    failed = [result.name for result in results if not result.passed]
    if failed:
        return cli_contract.verification_failed(
            "episode(s) failed: " + ", ".join(failed),
            details={"failed": failed},
        )
    return EXIT_OK


def _cmd_scan(args: argparse.Namespace) -> int:
    if args.path == "-":
        text = sys.stdin.read()
    else:
        path = Path(args.path)
        if not path.is_file():
            # refusing is the point: a scan that could not run must never report "no
            # findings", and a missing path is not an internal error (it used to escape as
            # an uncaught FileNotFoundError and exit 7)
            print(f"nothing to scan at {path}")
            return cli_contract.not_found(
                f"nothing to scan at {path}",
                hint="pass a file path, or - to scan stdin",
                details={"path": str(path)},
            )
        text = path.read_text(encoding="utf-8")
    findings = scan(text)
    payload = [
        {"kind": f.kind, "start": f.start, "end": f.end, "preview": f.preview}
        for f in findings
    ]
    cli_contract.set_data(
        {"findings": payload, "count": len(findings), "path": str(args.path)}
    )
    cli_contract.add_evidence(
        "stdin" if args.path == "-" else str(args.path), "file", "scanned input"
    )
    print(json.dumps(payload, ensure_ascii=False))
    return 1 if findings else 0


def _cmd_prompts_list(args: argparse.Namespace) -> int:
    registry = PromptRegistry()
    for prompt in registry.all():
        purpose = str(prompt.meta.get("purpose", ""))[:60]
        print(f"{prompt.id}.v{prompt.version} — {purpose}")
    missing = [pid for pid in REQUIRED_IDS if pid not in registry.ids()]
    if missing:
        print(f"MISSING required prompts: {missing}", file=sys.stderr)
        return 1
    return 0


def _cmd_memory_stats(args: argparse.Namespace) -> int:
    path = Path(args.db)
    if not path.exists():
        print(f"no memory db at {path}")
        return 1
    incidents = IncidentStore(path)
    notes = NoteStore(path)
    counts: dict[str, int] = {}
    try:
        incident_count = incidents.count()
        note_count = notes.count()
        for incident in incidents.all(include_archived=True):
            key = f"incident:{incident.status}"
            counts[key] = counts.get(key, 0) + 1
        for note in notes.all(include_archived=True):
            key = f"note:{note.status}"
            counts[key] = counts.get(key, 0) + 1
    finally:
        incidents.close()
        notes.close()
    cli_contract.set_data(
        {
            "incidents": incident_count,
            "notes": note_count,
            "by_status": counts,
            "db": str(path),
        }
    )
    cli_contract.add_evidence(str(path), "file", "memory db that was read")
    print(f"incidents: {incident_count}, notes: {note_count}")
    for key in sorted(counts):
        print(f"  {key}: {counts[key]}")
    return 0


def _cmd_memory_search(args: argparse.Namespace) -> int:
    path = Path(args.db)
    typed_path = path.with_name("typed_memory.db")
    have_legacy = path.exists()
    have_typed = typed_path.exists()
    if not (have_legacy or have_typed):
        print(f"no memory db at {path}")
        return 1
    rows: list[dict] = []
    if have_legacy:
        incidents = IncidentStore(path)
        notes = NoteStore(path)
        try:
            evidence = find_evidence(
                incidents, notes, signature=args.signature, query=args.query, limit=args.limit
            )
        finally:
            incidents.close()
            notes.close()
        rows.extend(
            {
                "ref": item.ref,
                "trust": float(item.trust),
                "exact": bool(item.exact),
                "untrusted": bool(item.untrusted),
                "source": "incident/note",
                "render": item.render(),
            }
            for item in evidence
        )
    if have_typed:
        # `memory remember` writes the typed store; search must see it too (one memory,
        # not two stores). A broken typed store is a note, never a crash.
        try:
            from .memory_store import TypedMemoryStore

            with TypedMemoryStore(typed_path) as store:
                for hit in store.recall(args.query or "", k=max(1, int(args.limit or 5))):
                    mem = getattr(hit, "memory", hit)
                    rows.append(
                        {
                            "ref": f"typed:{getattr(mem, 'id', '')}",
                            "trust": float(getattr(hit, "score", 0.0) or 0.0),
                            "exact": False,
                            "untrusted": False,
                            "source": "typed",
                            "render": str(
                                getattr(mem, "text", "") or getattr(mem, "subject", "")
                            ),
                        }
                    )
        except Exception:  # noqa: BLE001
            pass
    # an empty list is a result, not a prose dead end (§6 consequence 1)
    cli_contract.set_data(
        {"evidence": rows, "count": len(rows), "db": str(path), "typed_db": str(typed_path),
         "query": args.query or "", "signature": args.signature or ""}
    )
    if have_legacy:
        cli_contract.add_evidence(str(path), "file", "memory db that was read")
    if have_typed:
        cli_contract.add_evidence(str(typed_path), "file", "typed memory db that was read")
    if not rows:
        print("no evidence found")
        return 0
    for row in rows:
        flags = " untrusted" if row["untrusted"] else ""
        exact = " exact" if row["exact"] else ""
        src = "" if row.get("source") == "incident/note" else f" ({row['source']})"
        print(f"- [{row['ref']}] trust={row['trust']:.2f}{exact}{flags}{src}: {row['render']}")
    return 0


def _cmd_memory_audit(args: argparse.Namespace) -> int:
    """Read-only audit: event duplicates + retention + memory schema axes.

    Reports only — pruning rows or adding a uniqueness key happens in
    ``events.py`` (owned by another work block), so the enforcement gaps are
    printed explicitly instead of being silently skipped.
    """
    events_path = Path(args.events)
    if not events_path.exists():
        print(f"no events db at {events_path}")
        return cli_contract.not_found(
            f"no events db at {events_path}",
            hint="a session has not written any events yet, or --events points elsewhere",
            details={"events": str(events_path)},
        )
    store = EventStore(events_path)
    try:
        report = retention_report(store, max_age_days=args.max_age_days)
    finally:
        store.close()
    axes = schema_axes()
    cli_contract.set_data(
        {
            "total": report.total,
            "sessions": report.sessions,
            "bytes_on_disk": report.bytes_on_disk,
            "over_age": report.over_age,
            "duplicate_groups": report.duplicate_groups,
            "duplicate_rows": report.duplicate_rows,
            "policy": report.policy,
            "enforcement_gaps": report.enforcement_gaps,
            "schema_axes": axes,
            "events_db": str(events_path),
        }
    )
    cli_contract.add_evidence(str(events_path), "file", "events db that was read")
    print(f"events: {report.total} · sessions: {report.sessions}")
    print(
        f"bytes_on_disk: {report.bytes_on_disk}"
        f" · over_age({args.max_age_days}d): {report.over_age}"
    )
    print(f"duplicate groups: {len(report.duplicate_groups)} ({report.duplicate_rows} extra rows)")
    for group in report.duplicate_groups[:10]:
        print(
            f"  {group['session_id']} step {group['step']} {group['kind']}: {group['count']}×"
        )
    print(f"retention policy: {report.policy['mode']}")
    for gap in report.enforcement_gaps:
        print(f"open point (enforcement outside this module): {gap}")
    print(f"incident schema fields: {', '.join(axes['incident_fields'])}")
    print(f"research schema fields: {', '.join(axes['research_fields'])}")
    return 0


def _cmd_memory_retrieval_check(args: argparse.Namespace) -> int:
    """Show the retrieve → applicability check → use/reject verdict for a problem."""
    path = Path(args.db)
    if not path.exists():
        print(f"no memory db at {path}")
        return cli_contract.not_found(
            f"no memory db at {path}",
            hint="incidents and notes are written by a session (nightshift), not by this command",
            details={"db": str(path)},
        )
    incidents = IncidentStore(path)
    try:
        from .memory import RetrievalContext, evaluate_retrieval

        context = RetrievalContext(
            problem=args.problem or "",
            component=args.component or "",
            failure_class=args.failure_class or "",
            goal_scope=args.goal_scope or "",
            signature=args.signature or "",
            min_confidence=args.min_confidence,
        )
        decision = evaluate_retrieval(incidents, context=context, limit=args.limit)
    finally:
        incidents.close()
    cli_contract.set_data(
        {
            "strong_intervention_allowed": bool(decision.strong_intervention_allowed),
            "verdicts": [
                {
                    "ref": verdict.ref,
                    "verdict": verdict.verdict,
                    "similarity": float(verdict.similarity),
                    "confidence": float(verdict.confidence),
                    "matched_fields": list(verdict.matched_fields),
                    "conflict_fields": list(verdict.conflict_fields),
                    "reasons": list(verdict.reasons),
                }
                for verdict in decision.verdicts
            ],
            "enforcement_gaps": list(ENFORCEMENT_GAPS),
            "db": str(path),
        }
    )
    cli_contract.add_evidence(str(path), "file", "memory db that was read")
    print(f"strong intervention allowed: {decision.strong_intervention_allowed}")
    for verdict in decision.verdicts:
        fields = f" matched={','.join(verdict.matched_fields) or '-'}"
        conflicts = ""
        if verdict.conflict_fields:
            conflicts = f" conflicts={','.join(verdict.conflict_fields)}"
        print(
            f"- [{verdict.verdict}] {verdict.ref} sim={verdict.similarity:.2f}"
            f" conf={verdict.confidence:.2f} reasons={','.join(verdict.reasons)}{fields}{conflicts}"
        )
    if not decision.verdicts:
        print("- (no candidates retrieved)")
    for gap in ENFORCEMENT_GAPS:
        if args.verbose:
            print(f"enforcement gap: {gap}")
    return 0


def _cmd_nightshift(args: argparse.Namespace) -> int:
    events_path = Path(args.events)
    if not events_path.exists():
        print(f"no events db at {events_path}")
        return cli_contract.not_found(
            f"no events db at {events_path}",
            hint="consolidation needs a finished session's events",
            details={"events": str(events_path)},
        )
    store = EventStore(events_path)
    incidents = IncidentStore(args.memory)
    notes = NoteStore(args.memory)
    try:
        summary = build_episode_summary(store, args.session)
        if summary.events == 0:
            print(f"no events for session {args.session}")
            return cli_contract.not_found(
                f"no events for session {args.session}",
                hint="`skaldir metrics --json` lists what the events db holds",
                details={"session": args.session},
            )
        result = consolidate(summary, incidents=incidents, apply=args.apply)
        note_proposals = consolidate_research_notes(
            store, notes, args.session, apply=args.apply
        )
        solved = propose_solved_incidents(store, args.session, incidents)
        if args.apply and solved:
            for proposal in solved:
                incidents.upsert(proposal)
    finally:
        store.close()
        incidents.close()
        notes.close()
    applied = bool(result.applied or (args.apply and (solved or note_proposals)))
    cli_contract.set_data(
        {
            "session_id": args.session,
            "events": summary.events,
            "applied": applied,
            "proposals": {
                "incidents": [proposal.signature for proposal in result.proposals],
                "solved": [proposal.signature for proposal in solved],
                "notes": [note.topic for note in note_proposals],
            },
            "memory_db": str(args.memory),
        }
    )
    cli_contract.add_evidence(str(events_path), "file", "events db that was read")
    print(summary.to_text())
    print()
    if not result.proposals:
        print("no incident proposals (nothing new)")
    for proposal in result.proposals:
        print(f"- propose incident [{proposal.signature}] {proposal.title} ({proposal.status})")
    for proposal in solved:
        print(f"- propose solved incident [{proposal.signature}] {proposal.title}")
    for note in note_proposals:
        link = f" contradicts {', '.join(note.contradicts)}" if note.contradicts else ""
        print(f"- propose note [{note.topic[:60]}]{link}")
    print(f"applied: {result.applied or bool(args.apply and (solved or note_proposals))}")
    return 0


def _cmd_metrics(args: argparse.Namespace) -> int:
    events_path = Path(args.events)
    if not events_path.exists():
        print(f"no events db at {events_path}")
        return cli_contract.not_found(
            f"no events db at {events_path}",
            hint="metrics are computed from recorded sessions; --events points at the db",
            details={"events": str(events_path)},
        )
    store = EventStore(events_path)
    incidents = IncidentStore(args.memory)
    skills = SkillStore(args.memory)
    session_db = getattr(args, "session_db", None)
    session_id = getattr(args, "session_id", None)
    if session_id and not session_db:
        home = host_getenv("HERMES_HOME") or str(Path.home() / ".hermes")
        session_db = str(Path(home) / "state.db")
    pause_db = Path(args.memory).parent / "pauses.db"
    try:
        metrics = compute_metrics(
            store,
            incidents,
            skills,
            session_db=session_db,
            session_id=session_id,
            pause_db=pause_db if pause_db.is_file() else None,
            testing_runtime=Path(args.memory).parent,
        )
    finally:
        store.close()
        incidents.close()
        skills.close()
    payload = metrics.to_dict()
    cli_contract.set_data(payload)
    cli_contract.add_evidence(str(events_path), "file", "events db the counters came from")
    for key, value in payload.items():
        print(f"{key}: {value}")
    return 0


def _cmd_skills_list(args: argparse.Namespace) -> int:
    store = SkillStore(args.db)
    try:
        skills = store.all()
        rows = [
            {
                "name": skill.name,
                "status": skill.status,
                "occurrences": skill.occurrences,
                "sessions": len(skill.sessions),
                "success_rate": skill.success_rate(),
            }
            for skill in skills
        ]
    finally:
        store.close()
    cli_contract.set_data({"skills": rows, "count": len(rows), "db": str(args.db)})
    cli_contract.add_evidence(str(args.db), "file", "memory db holding the candidates")
    if not skills:
        print("no skill candidates")
    for skill in skills:
        rate = skill.success_rate()
        rate_text = "—" if rate is None else f"{rate:.0%}"
        print(
            f"[{skill.status:9s}] {skill.name} — {skill.occurrences}× in "
            f"{len(skill.sessions)} sessions · success: {rate_text}"
        )
    return 0


def _cmd_skills_install(args: argparse.Namespace) -> int:
    """`skaldir skills install <builtin|owner/repo|github-url>` — out-of-the-box skills."""
    from .skill_install import InstallError, install_skill

    skills_dir = args.skills_dir or str(_gv_runtime() / "skills")
    try:
        result = install_skill(args.target, db=args.db, skills_dir=skills_dir)
    except InstallError as exc:
        print(f"install failed: {exc}")
        return cli_contract.not_found(
            f"install failed: {exc}",
            hint="`skaldir skills list` shows what is installed; builtin names need no network",
        )
    cli_contract.set_data({"target": args.target, "result": result})
    print(result)
    return 0


def _cmd_skills_mine(args: argparse.Namespace) -> int:
    events_path = Path(args.events)
    if not events_path.exists():
        print(f"no events db at {events_path}")
        return 1
    events = EventStore(events_path)
    try:
        candidates = mine_tool_patterns(
            events,
            min_occurrences=args.min_occurrences,
            min_sessions=args.min_sessions,
        )
    finally:
        events.close()
    if not candidates:
        print("no repeated patterns found (yet)")
        return 0
    store = SkillStore(args.db)
    try:
        for candidate in candidates:
            draft_skill(candidate)
            valid = "SKILL.md ok" if is_valid_skill_body(candidate.body) else "SKILL.md invalid"
            print(
                f"- {candidate.name}: {candidate.occurrences}× in "
                f"{len(candidate.sessions)} Sessions ({valid})"
            )
            if args.apply:
                store.upsert(candidate)
    finally:
        store.close()
    print(f"applied: {args.apply}")
    return 0


def _cmd_skills_validate(args: argparse.Namespace) -> int:
    store = SkillStore(args.db)
    skill = store.get(args.name)
    if skill is None:
        store.close()
        print(f"unknown skill: {args.name}")
        return cli_contract.not_found(
            f"unknown skill: {args.name}",
            hint="`skaldir skills list` prints the candidates that exist",
        )
    events = EventStore(args.events) if Path(args.events).exists() else None
    try:
        report = validate_candidate(skill, events)
    finally:
        if events is not None:
            events.close()
        store.close()
    cli_contract.set_data(
        {
            "name": report.name,
            "ok": bool(report.ok),
            "checks": [
                {"passed": bool(passed), "detail": str(detail)}
                for passed, detail in report.checks
            ],
        }
    )
    cli_contract.add_evidence(str(args.db), "file", "skill candidate that was validated")
    print(f"Skill: {report.name}")
    for line in report.to_lines():
        print(line)
    if report.ok:
        return EXIT_OK
    return cli_contract.verification_failed(
        f"skill {args.name} did not pass validation",
        hint="the failed check lines above name what is missing",
    )


def _cmd_skills_promote(args: argparse.Namespace) -> int:
    store = SkillStore(args.db)
    skill = store.get(args.name)
    if skill is None:
        store.close()
        print(f"unknown skill: {args.name}")
        return cli_contract.not_found(
            f"unknown skill: {args.name}",
            hint="`skaldir skills list` prints the candidates that exist",
        )
    events = EventStore(args.events) if Path(args.events).exists() else None
    try:
        report = validate_candidate(skill, events)
        cli_contract.set_data(
            {
                "name": report.name,
                "ok": bool(report.ok),
                "status": STATUS_PROMOTED if report.ok or args.force else skill.status,
                "forced": bool(args.force and not report.ok),
                "checks": [
                    {"passed": bool(passed), "detail": str(detail)}
                    for passed, detail in report.checks
                ],
            }
        )
        cli_contract.add_evidence(str(args.db), "file", "skill candidate that was promoted")
        print(f"Skill: {report.name}")
        for line in report.to_lines():
            print(line)
        if not report.ok and not args.force:
            print("Promotion refused — validation failed (--force overrides).")
            return 1
        store.set_status(args.name, STATUS_PROMOTED)
        suffix = "" if report.ok else " (--force despite failed checks)"
        print(f"promoted: {args.name}{suffix}")
        return 0
    finally:
        if events is not None:
            events.close()
        store.close()


def _cmd_skills_demote(args: argparse.Namespace) -> int:
    store = SkillStore(args.db)
    try:
        if store.get(args.name) is None:
            print(f"unknown skill: {args.name}")
            return cli_contract.not_found(
                f"unknown skill: {args.name}",
                hint="`skaldir skills list` prints the candidates that exist",
            )
        store.set_status(args.name, STATUS_DEMOTED)
        cli_contract.set_data({"name": args.name, "status": STATUS_DEMOTED, "db": str(args.db)})
        cli_contract.add_evidence(str(args.db), "file", "memory db that was written")
        print(f"demoted: {args.name}")
    finally:
        store.close()
    return 0


def _cmd_skills_show(args: argparse.Namespace) -> int:
    store = SkillStore(args.db)
    try:
        skill = store.get(args.name)
        if skill is None:
            print(f"unknown skill: {args.name}")
            return cli_contract.not_found(
                f"unknown skill: {args.name}",
                hint="`skaldir skills list` prints the candidates that exist",
            )
        cli_contract.set_data(
            {
                "name": skill.name,
                "status": skill.status,
                "occurrences": skill.occurrences,
                "sessions": len(skill.sessions),
                "body": skill.body,
            }
        )
        cli_contract.add_evidence(str(args.db), "file", "memory db holding the candidate")
        print(
            f"# {skill.name} [{skill.status}] — {skill.occurrences}× in "
            f"{len(skill.sessions)} Sessions"
        )
        print()
        print(skill.body)
    finally:
        store.close()
    return 0


def _cmd_skills_export(args: argparse.Namespace) -> int:
    store = SkillStore(args.db)
    try:
        skill = store.get(args.name)
        if skill is None:
            print(f"unknown skill: {args.name}")
            return cli_contract.not_found(
                f"unknown skill: {args.name}",
                hint="`skaldir skills list` prints the candidates that exist",
            )
        if not is_valid_skill_body(skill.body):
            print(f"refusing export: body of {args.name} is not a valid SKILL.md")
            return cli_contract.verification_failed(
                f"refusing export: body of {args.name} is not a valid SKILL.md",
                hint=f"run `skaldir skills validate {args.name}` first",
            )
    finally:
        store.close()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{skill.name}.md"
    target.write_text(skill.body, encoding="utf-8")
    cli_contract.set_data({"name": skill.name, "path": str(target), "bytes": len(skill.body)})
    cli_contract.add_evidence(str(target), "file", "written SKILL.md")
    print(f"written: {target}")
    return 0


def _parse_validation_case(raw: str) -> tuple[str, bool]:
    """'TARGET:pass|fail' -> (target, expect_pass) for testing-tool requests."""
    target, _, expect = str(raw).rpartition(":")
    if not target or expect not in ("pass", "fail"):
        raise SystemExit(f"Ungueltiger --validation-case (erwartet ZIEL:pass|fail): {raw}")
    return target, expect == "pass"


def _registry_context(args: argparse.Namespace):
    """§1.2: resolve runtime dir, tools DB and project context in ONE place.

    Explicit ``--db`` keeps winning (its directory then provides the runtime);
    without flags the default is the runtime DB (``$HERMES_HOME/godsv/tools.db``)
    — never the old cwd-relative ``.godsv/tools.db``.
    """
    from .registry import resolve_registry

    return resolve_registry(
        runtime=getattr(args, "runtime", "") or None,
        project=getattr(args, "project", "") or None,
        db=getattr(args, "db", None) or None,
    )


def _tools_store(context):
    """Open the resolved tools DB read-only — ``None`` if it does not exist yet."""
    if not Path(context.tools_db).is_file():
        return None
    return ToolStore(str(context.tools_db))


def _tools_store_rw(context) -> ToolStore:
    """Open (creating parent dirs + DB) the resolved tools DB for a write path."""
    Path(context.tools_db).parent.mkdir(parents=True, exist_ok=True)
    return ToolStore(str(context.tools_db))


def _manifest_facts(context) -> dict:
    """Scope/version/project facts from ``generated_tools/*/manifest.yaml``."""
    from .capability import load_tool_entries

    return {entry["name"]: entry for entry in load_tool_entries(context.runtime_dir)}


def _registry_annotation(entry: dict | None) -> str:
    if entry is None:
        return " · scope=- · version=-"
    version = entry.get("version_raw")
    shown = version if version not in (None, "") else "-"
    text = f" · scope={entry.get('scope') or '-'} · version={shown}"
    if entry.get("project_id"):
        text += f" · project={entry['project_id']}"
    return text


def _evidence_contexts(values: list[str]) -> list[str]:
    """Evidence projects as PATH or already-resolved PROJECT_ID — normalized."""
    from .capability import project_id_for

    out: list[str] = []
    for raw in values:
        text = str(raw).strip()
        if not text:
            continue
        candidate = Path(text)
        out.append(project_id_for(candidate) if candidate.is_dir() else text)
    return out


def _cmd_tools_request(args: argparse.Namespace) -> int:
    """Record a capability request — a running session builds it on the next gap."""
    from skaldir.capability import CapabilityRequest, CapabilityStore

    sample: dict = {}
    if args.sample:
        try:
            sample = json.loads(args.sample)
        except json.JSONDecodeError as exc:
            print(f"--sample is not JSON: {exc}")
            return 1
    context = _registry_context(args)
    store = CapabilityStore(context.runtime_dir)
    project_id = context.project_id
    request = store.add(
        CapabilityRequest(
            capability=args.capability,
            project_id=project_id,
            validation_cases=[
                _parse_validation_case(v)
                for v in (getattr(args, "validation_case", None) or [])
            ],
            reason=args.reason or "",
            requested_by=args.requested_by or "main-agent",
            session_id=args.session or "",
            kind=args.kind or "auto",
            name=args.name or "",
            source_path=args.source or "",
            tests_path=args.tests or "",
            module=args.module or "",
            entrypoint=args.entrypoint or "",
            sample_input=sample,
            required_secrets=list(args.secret or []),
            allowed_domains=list(args.domain or []),
            dependencies=list(args.dependency or []),
            research_notes=args.research or "",
        )
    )
    payload = {
        "request_id": request.request_id,
        "capability": request.capability,
        "status": request.status,
        "kind": request.kind,
        "project_id": request.project_id,
        "created_at": str(getattr(request, "created_at", "") or ""),
        "validation_cases": [list(case) for case in (request.validation_cases or [])],
        "required_secrets": list(request.required_secrets or []),
        "allowed_domains": list(request.allowed_domains or []),
    }
    cli_contract.set_data(payload)
    cli_contract.add_evidence(
        str(Path(context.runtime_dir) / "capability_requests.jsonl"),
        "file",
        "capability request store the record was written to",
    )
    print(f"✓ capability request {request.request_id}: {request.capability}")
    print("  In a running Skaldir Agent session the next tool call detects the")
    print("  request → PAUSE → tool build → redirect → RESUME (same pipeline as the voice).")
    print(f"  Without a session, run directly: skaldir tools fulfill {request.request_id}")
    if getattr(args, "json", False) and cli_contract.current() is None:
        # direct handler call (no runner): the historical single-JSON line stays
        print(json.dumps({"request_id": request.request_id, "status": request.status}))
    return EXIT_OK


def _cmd_tools_fulfill(args: argparse.Namespace) -> int:
    """Run the capability pipeline right now (pause-less CLI path)."""
    from skaldir.capability import CapabilityStore

    runtime = _registry_context(args).runtime_dir
    store = CapabilityStore(runtime)
    if args.request_id:
        request = next(
            (r for r in store.all() if r.request_id == args.request_id), None
        )
        if request is None:
            print(f"unknown request: {args.request_id}")
            return cli_contract.not_found(
                f"unknown request: {args.request_id}",
                hint="`skaldir capability requests` (planned) or the request file lists ids",
                details={"runtime": str(runtime)},
            )
        if request.status != "open":
            print(f"request {request.request_id} is not open (status={request.status})")
            return cli_contract.fail(
                EXIT_CONFLICT,
                f"request {request.request_id} is not open (status={request.status})",
                hint="only an open request can be fulfilled",
                retryable=False,
                details={"request_id": request.request_id, "status": request.status},
            )
    else:
        request = store.newest_open()
        if request is None:
            print("no open capability request — first run: skaldir tools request …")
            return cli_contract.not_found(
                "no open capability request",
                hint=(
                    "first record one: skaldir tools request "
                    "--capability '<need>' --reason '<why>'"
                ),
            )
    runner = _toolbuild_runner_with_llm(runtime)
    result = runner.run(
        session_id=request.session_id or "cli",
        base_step=0,
        trigger_kind="capability_gap",
        trigger_fp=f"cap-{request.request_id}",
        problem=request.capability,
    )
    # The runner reports ``status="ok"`` for a build that FAILED but produced a
    # usable direction ("check source/tests …"), so the CLI must ask the request
    # itself whether the capability exists now: a success exit for a build that
    # produced nothing is exactly the lie the contract forbids.
    from skaldir.capability import STATUS_FULFILLED

    produced = ""
    try:
        fresh = next(
            (row for row in store.all() if row.request_id == request.request_id), None
        )
        if fresh is not None:
            produced = str(getattr(fresh, "result_tool", "") or "")
            fulfilled = str(fresh.status) == STATUS_FULFILLED
        else:
            fulfilled = False
    except Exception:  # noqa: BLE001 — a store read must not break the verdict
        fulfilled = False
    # `ResearchResult.status` is the RUNNER's word ("the build ran and left a usable
    # direction"), not the outcome. A caller reads `data.status` as the outcome ("is the
    # capability there now?"), so echoing the runner's "ok" for a build that produced
    # nothing is exactly the lie the contract forbids (documented defect, pinned by
    # tools/gv_cli_e2e.py). `runner_status` keeps the runner's own word — nothing is lost,
    # and the exit code below says the same thing as `status` now.
    if fulfilled and result.status == "ok":
        outcome = "ok"
    elif result.status in ("blocked", "failed"):
        outcome = result.status
    else:
        outcome = "not_fulfilled"
    payload = {
        "request_id": request.request_id,
        "status": outcome,
        "runner_status": result.status,
        "capability_fulfilled": bool(fulfilled),
        "tool": produced,
        "direction": result.direction,
        "avoid": result.avoid,
        "evidence": list(result.evidence),
    }
    cli_contract.set_data(payload)
    cli_contract.add_evidence(
        str(Path(runtime) / "capability_requests.jsonl"), "file", "capability request record"
    )
    print(f"[{outcome}] {result.direction}")
    if result.avoid:
        print(f"  avoid: {result.avoid}")
    for line in result.evidence:
        print(f"  · {line}")
    if getattr(args, "json", False) and cli_contract.current() is None:
        # direct handler call (no runner): the historical single-JSON line stays
        print(json.dumps(result.__dict__, ensure_ascii=False, default=str, sort_keys=True))
    if fulfilled and result.status == "ok":
        return EXIT_OK
    if result.status == "blocked":
        return cli_contract.blocked(
            str(result.avoid or result.direction or "the build is blocked"),
            hint="the build gate names the missing precondition",
        )
    return cli_contract.verification_failed(
        str(result.avoid or result.direction or "the build did not pass its gates"),
        hint="the request's status and data.capability_fulfilled say whether a tool exists now",
        details={"status": result.status, "capability_fulfilled": bool(fulfilled)},
    )


def _toolbuild_llm_client(args: argparse.Namespace) -> tuple[object | None, dict]:
    """Resolve the configured provider client for a tool-build draft (§1.8).

    The builder never constructs a provider client of its own; the caller hands
    over the one that is already configured. Returns ``(client, status)`` where
    ``client is None`` when no ready provider exists — the build then reports the
    honest ``no_source_no_client`` blocker instead of inventing code or a 401.
    """

    # No provider is configured (and no env/config file exists): skip the client
    # resolution entirely instead of raising through it — that keeps the honest
    # no-client blocker from degrading into a provider 401 on the draft call.
    # A test double that patches cli_shared.client_and_status counts as configured:
    # the resolver's own job is only to skip the EMPTY environment.
    from . import cli_shared

    if getattr(cli_shared.client_and_status, "__module__", "") == __name__:
        from . import env as _env

        if not _env.first_env(
            (
                "SKALDIR_LLM_BASE_URL",
                "SKALDIR_LLM_API_BASE",
                "SKALDIR_LLM_API_KEY",
                "OPENAI_API_KEY",
                "SKALDIR_LLM_MODEL",
            ),
            default="",
        ):
            return None, {"ready": False, "provider": "none"}
    try:
        client, status = cli_shared.client_and_status(args)
    except Exception:  # noqa: BLE001 — no configured provider is a blocker, not a crash
        return None, {"ready": False, "provider": "none"}
    ready = bool(isinstance(status, dict) and status.get("ready"))
    return (client if ready else None), status


def _toolbuild_runner_with_llm(runtime: str | Path):
    from .capability import ToolBuildRunner

    llm, _status = _toolbuild_llm_client(argparse.Namespace())
    return ToolBuildRunner(runtime=runtime, llm=llm)


def _toolbuild_builder_with_llm(context):
    from .toolbuilder import ToolBuilder

    llm, _status = _toolbuild_llm_client(argparse.Namespace())
    return ToolBuilder(context.runtime_dir, store=_tools_store_rw(context), llm=llm)


def _cmd_tools_build(args: argparse.Namespace) -> int:
    """Capability gap → build the tool through the full gated pipeline."""
    from skaldir.toolbuilder import ToolBuilder, ToolBuildRequest

    sample: dict = {}
    if args.sample:
        try:
            sample = json.loads(args.sample)
        except json.JSONDecodeError as exc:
            print(f"--sample is not JSON: {exc}")
            return 1
    research = args.research or ""
    if getattr(args, "research_file", None):
        try:
            research = Path(args.research_file).read_text(encoding="utf-8")
        except OSError as exc:
            print(f"research file not readable: {exc}")
            return 1
    context = _registry_context(args)
    request = ToolBuildRequest(
        capability=args.capability,
        reason=args.reason or "",
        requested_by=args.requested_by or "cli",
        kind=args.kind or "auto",
        name=args.name or "",
        source_path=args.source or "",
        tests_path=args.tests or "",
        module=args.module or "",
        entrypoint=args.entrypoint or "",
        sample_input=sample,
        research_notes=research,
        required_secrets=list(args.secret or []),
        allowed_domains=list(args.domain or []),
        project_id=context.project_id,
        dependencies=list(args.dependency or []),
        validation_cases=[
            _parse_validation_case(v)
            for v in (getattr(args, "validation_case", None) or [])
        ],
    )
    builder = _toolbuild_builder_with_llm(context)
    result = builder.build(request)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        for check in result.get("gates", []):
            mark = "✓" if check["ok"] else "✗"
            print(f"  {mark} {check['name']} — {str(check['detail'])[:90]}")
        if result["ok"]:
            print(f"✓ build {result['build_id']}: {result['tool']} ({result['kind']}) registered")
            print(f"  Wrapper: {result['wrapper']}")
            print(f"  Manifest: {result['manifest']}")
            store = _tools_store(context)
            if store is None:
                return EXIT_OK
            try:
                spec = store.get(result["tool"])
                if spec is not None:
                    print("  For the main agent:")
                    print(
                        "  "
                        + json.dumps(
                            ToolBuilder.redirect_payload(
                                spec, request, wrapper=result["wrapper"], kind=result["kind"]
                            ),
                            ensure_ascii=False,
                        )
                    )
            finally:
                store.close()
        else:
            print(f"✗ build {result['build_id']} failed: {result['error']}")
    # The exit code is the build's own verdict in BOTH modes. The `--json` path
    # used to fall through to `return 0`, so a failed build exited 0 with
    # `data.ok: false` in the very envelope that reported "ok: true".
    if result.get("ok"):
        return EXIT_OK
    message = str(result.get("error") or f"build {result.get('build_id', '?')} did not pass")
    blocked = str(result.get("blocked") or "")
    if blocked or str(result.get("flow_outcome") or "") in ("blocked", "superseded"):
        return cli_contract.blocked(
            message,
            hint="the gate that stopped the build is named in data.gates",
            details={"build_id": result.get("build_id", ""), "blocked": blocked},
        )
    return cli_contract.verification_failed(
        message,
        hint="the gate that failed is named in data.gates — fix it or supply a better source",
        details={"build_id": result.get("build_id", ""), "gates": result.get("gates", [])},
    )


def _cmd_tools_builds(args: argparse.Namespace) -> int:
    from skaldir.toolbuilder import ToolBuilder

    queue = ToolBuilder(_registry_context(args).runtime_dir).queue(limit=args.limit)
    if not queue:
        print("no builds in the queue")
        return 0
    for entry in queue:
        stages = " → ".join(stage["stage"] for stage in entry["stages"])
        print(f"[{entry['stage']:12s}] {entry['build_id']} {entry.get('capability', '')[:60]}")
        print(f"    {stages}")
    return 0


def _cmd_tools_list(args: argparse.Namespace) -> int:
    context = _registry_context(args)
    store = _tools_store(context)
    if store is None:
        cli_contract.set_data(
            {"tools": [], "count": 0, "tools_db": str(context.tools_db),
             "tools_db_exists": False}
        )
        cli_contract.add_evidence(str(context.tools_db), "file", "tools db (not created yet)")
        print(f"no tool specs (tools db not created yet: {context.tools_db})")
        return EXIT_OK
    try:
        tools = store.all()
    finally:
        store.close()
    facts = _manifest_facts(context)
    rows = [
        {
            "name": spec.name,
            "title": spec.title,
            "kind": spec.kind,
            "status": spec.status,
            "scope": (facts.get(spec.name) or {}).get("scope"),
            "version": (facts.get(spec.name) or {}).get("version_raw"),
            "project_id": (facts.get(spec.name) or {}).get("project_id", ""),
            "path": (facts.get(spec.name) or {}).get("path"),
        }
        for spec in tools
    ]
    cli_contract.set_data(
        {"tools": rows, "count": len(rows), "tools_db": str(context.tools_db),
         "tools_db_exists": True}
    )
    cli_contract.add_evidence(str(context.tools_db), "file", "tools db that was read")
    if not tools:
        print("no tool specs")
    for spec in tools:
        print(
            f"[{spec.status:9s}] {spec.name} ({spec.kind}) — {spec.title}"
            f"{_registry_annotation(facts.get(spec.name))}"
        )
    return EXIT_OK


def _cmd_tools_show(args: argparse.Namespace) -> int:
    context = _registry_context(args)
    store = _tools_store(context)
    if store is None:
        print(f"unknown tool: {args.name}")
        return cli_contract.not_found(
            f"unknown tool: {args.name}",
            hint="`skaldir tools list` shows the specs of the resolved runtime db",
            details={"tools_db": str(context.tools_db)},
        )
    try:
        spec = store.get(args.name)
    finally:
        store.close()
    if spec is None:
        print(f"unknown tool: {args.name}")
        return cli_contract.not_found(
            f"unknown tool: {args.name}",
            hint="`skaldir tools list` shows the specs of the resolved runtime db",
            details={"tools_db": str(context.tools_db)},
        )
    payload = spec.to_dict()
    entry = _manifest_facts(context).get(args.name)
    payload["registry"] = (
        {
            "scope": entry["scope"],
            "version": entry.get("version_raw"),
            "project_id": entry["project_id"],
            "path": entry["path"],
        }
        if entry
        else {"scope": None, "version": None, "project_id": "", "path": None}
    )
    cli_contract.set_data(payload)
    cli_contract.add_evidence(str(context.tools_db), "file", "tools db that was read")
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return EXIT_OK


def _cmd_tools_scan(args: argparse.Namespace) -> int:
    findings = scan_tool_file(args.path)
    rows = [
        {
            "severity": finding.severity,
            "line": finding.line,
            "rule": finding.rule,
            "message": finding.message,
        }
        for finding in findings
    ]
    cli_contract.set_data({"findings": rows, "count": len(rows), "path": str(args.path)})
    cli_contract.add_evidence(str(args.path), "file", "scanned tool source")
    if not findings:
        print("no findings")
        return EXIT_OK
    for finding in findings:
        print(f"[{finding.severity}] line {finding.line} {finding.rule}: {finding.message}")
    # legacy exit code: tests/test_tools_store.py::test_scan_cli_blocks_evil pins 1
    return EXIT_USAGE


def _cmd_tools_propose(args: argparse.Namespace) -> int:
    payload = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    data = payload.get("tool", payload) if isinstance(payload, dict) else {}
    spec = ToolSpec(
        name=str(data.get("name", "")),
        title=str(data.get("title", data.get("name", ""))),
        description=str(data.get("description", "")),
        tags=[str(tag) for tag in (data.get("tags") or [])],
        kind=str(data.get("kind", "python")),
        module=str(data.get("module", "")),
        entrypoint=str(data.get("entrypoint", "")),
        steps=[step for step in (data.get("steps") or []) if isinstance(step, dict)],
        test_command=[str(cmd) for cmd in (data.get("test_command") or [])],
        sample_input=dict(data.get("sample_input") or {}),
        rationale=str(data.get("rationale", "")),
        provenance=str(data.get("provenance", "manual")),
    )
    if not spec.name:
        print("spec needs a name")
        return 1
    store = _tools_store_rw(_registry_context(args))
    try:
        stored, composables = propose_tool(store, spec)
    finally:
        store.close()
    print(f"proposed: {stored.name} [{stored.status}] ({stored.kind})")
    if composables:
        print("Compose-first — existing tools with overlap:")
        for item in composables:
            print(f"  - {item.name} [{item.status}]: {item.title}")
    else:
        print("Compose-first: no overlapping tools found")
    return 0


def _cmd_tools_approve(args: argparse.Namespace) -> int:
    store = _tools_store_rw(_registry_context(args))
    try:
        spec = store.get(args.name)
        if spec is None:
            print(f"unknown tool: {args.name}")
            return 1
        if spec.kind == KIND_COMPOSITION:
            problems = validate_composition(store, spec)
            for problem in problems:
                print(f"✗ {problem}")
            if problems and not args.force:
                print("Approval refused (composition incomplete; --force overrides).")
                return 1
            store.set_status(spec.name, STATUS_APPROVED, scan_summary="composition ok")
            print(f"approved: {spec.name} (composition)")
            return 0
        report = run_gates(spec, workdir=args.workdir)
        for line in report.render_lines():
            print(line)
        summary = "; ".join(
            f"{check.gate}:{'ok' if check.passed else 'fail'}" for check in report.checks
        )
        if not report.passed and not args.force:
            store.set_status(spec.name, spec.status, scan_summary=summary)
            print("Approval refused — gates not passed (--force overrides).")
            return 1
        store.set_status(spec.name, STATUS_APPROVED, scan_summary=summary)
        print(f"approved: {spec.name}{'' if report.passed else ' (--force)'}")
        return 0
    finally:
        store.close()


def _cmd_tools_revoke(args: argparse.Namespace) -> int:
    store = _tools_store_rw(_registry_context(args))
    try:
        if store.get(args.name) is None:
            print(f"unknown tool: {args.name}")
            return 1
        store.set_status(args.name, STATUS_REVOKED)
        print(f"revoked: {args.name}")
    finally:
        store.close()
    return 0


def _cmd_tools_compose(args: argparse.Namespace) -> int:
    store = _tools_store_rw(_registry_context(args))
    try:
        candidates = compose_candidates(store, description=args.need, tags=args.tag)
    finally:
        store.close()
    rows = [
        {
            "name": spec.name,
            "status": spec.status,
            "kind": spec.kind,
            "description": spec.description or spec.title,
        }
        for spec in candidates
    ]
    cli_contract.set_data({"need": args.need, "candidates": rows, "count": len(rows)})
    cli_contract.add_evidence("src/skaldir/tools.py:469", "code", "compose_candidates()")
    if not candidates:
        print("no compose candidates found")
        return 0
    print("Compose candidates (ranked):")
    for spec in candidates:
        print(f"  - {spec.name} [{spec.status}] — {spec.description or spec.title}")
    return 0


def _cmd_tools_promote_global(args: argparse.Namespace) -> int:
    """Promote a validated project tool to a global version (§7, T11/T12).

    "global" = user-wide in THIS installation — the tool is never published.
    Evidence must come from >=2 independent FOREIGN project contexts;
    ``--evidence-project`` accepts a project path or a resolved project id.
    """
    from .capability import promote_tool

    context = _registry_context(args)
    result = promote_tool(
        context.runtime_dir,
        args.name,
        evidence_projects=_evidence_contexts(
            list(getattr(args, "evidence_project", None) or [])
        ),
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif result.get("promoted"):
        print(
            f"✓ promoted {result['name']} "
            f"{result.get('previous_version')} -> {result.get('version')} (global)"
        )
    else:
        print("✗ not promoted: " + "; ".join(result.get("blockers") or []))
    return 0 if result.get("promoted") else 1


def _cmd_registry_status(args: argparse.Namespace) -> int:
    """§1.2: which runtime dir, tools DB and project context apply everywhere."""
    from .registry import registry_snapshot

    context = _registry_context(args)
    snapshot = registry_snapshot(context)
    if getattr(args, "json", False):
        print(json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    print(f"runtime:    {snapshot['runtime_dir']}")
    state = "exists" if snapshot["tools_db_exists"] else "not created yet"
    print(f"tools db:   {snapshot['tools_db']} ({state}, {snapshot['tool_specs']} spec(s))")
    print(f"selection:  {snapshot['selection_reason']}")
    print(
        f"generated:  {snapshot['generated_tools_dir']} "
        f"({len(snapshot['generated_tools'])} tool(s))"
    )
    print(f"project:    {snapshot['project_id']}")
    for candidate in snapshot["legacy_candidates"]:
        if candidate["exists"]:
            names = ", ".join(candidate["names"]) or "-"
            print(
                f"legacy:     {candidate['path']} — FOUND "
                f"({candidate['tool_count']} spec(s): {names})"
            )
            print("            → skaldir registry migrate --dry-run")
        else:
            print(f"legacy:     {candidate['path']} — not present")
    return 0


def _cmd_registry_migrate(args: argparse.Namespace) -> int:
    """§1.2: merge legacy tool DBs into the runtime DB — backups first, dry-run default."""
    from .registry import migrate_legacy

    apply = bool(getattr(args, "apply", False))
    report = migrate_legacy(_registry_context(args), apply=apply)
    if getattr(args, "json", False):
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    else:
        print(f"[{'APPLY' if apply else 'DRY-RUN'}] target: {report['target_db']}")
        if not report["sources"]:
            print("no legacy database found — nothing to migrate")
        for source in report["sources"]:
            print(f"source: {source['path']} ({source.get('reason') or '-'})")
            if source.get("backup"):
                print(f"  backup: {source['backup']}")
            for tool in source["tools"]:
                print(
                    f"  {tool['name']:<28} [{tool['status']}] v{tool['version']} "
                    f"updated={tool['updated']:.0f} → {tool['action']}"
                )
            for conflict in source["conflicts"]:
                print(
                    f"  conflict {conflict['name']}: "
                    f"source_updated={conflict['source_updated']:.0f} "
                    f"target_updated={conflict['target_updated']:.0f} "
                    f"→ {conflict['resolution']}"
                )
        if report.get("target_backup"):
            print(f"target backup: {report['target_backup']}")
        print(
            f"{'migrated' if apply else 'would migrate'}: {report['migrated']}, "
            f"identical: {report['identical']}, conflicts: {len(report['conflicts'])}"
        )
        for error in report["errors"]:
            print(f"error: {error}")
        if not apply and report["migrated"]:
            print("re-run with --apply to write (source+target backup first, conflicts journaled)")
    return 1 if report["errors"] else 0


def _cmd_workflows_list(args: argparse.Namespace) -> int:
    """§9 autonomous workflows: what exists, in which scope, who can start it."""
    from .workflows import workflow_contracts, workflow_runners

    contracts = workflow_contracts()
    try:
        runners = set(workflow_runners())
    except Exception as exc:  # noqa: BLE001 — a broken runner map must not hide the registry
        runners = set()
        cli_contract.warn(f"workflow runner map unreadable: {type(exc).__name__}: {exc}")
    rows = [
        dict(contract, runnable=contract["name"] in runners) for contract in contracts
    ]
    startable = [row["name"] for row in rows if row["runnable"]]
    cli_contract.set_data({"workflows": rows, "count": len(rows), "runnable": startable})
    cli_contract.add_evidence(
        "src/skaldir/workflows.py:1044", "code", "workflow_contracts() registry"
    )
    declared_without_runner = [row["name"] for row in rows if not row["runnable"]]
    if declared_without_runner:
        cli_contract.warn(
            "declared but not startable: " + ", ".join(declared_without_runner)
        )
    if startable:
        cli_contract.next_action(
            f"skaldir workflows run {startable[0]} --param app=<app> --json",
            "start a declared workflow; its own verdict decides the exit code",
        )
    legacy_json = bool(getattr(args, "json", False)) and cli_contract.current() is None
    if legacy_json:
        # direct handler call (no runner): the historical bare-list JSON stays
        print(json.dumps(contracts, ensure_ascii=False, indent=2))
        return EXIT_OK
    print(f"{len(rows)} workflow contract(s), {len(startable)} startable")
    for row in rows:
        mark = "✓" if row["runnable"] else "✗"
        print(
            f"{mark} {row['name']}  [{row['scope']}/{row['execution']}]  {row['success']}"
        )
    return EXIT_OK


def _cmd_mcp_serve(args: argparse.Namespace) -> int:
    from .mcpserver import approved_python_specs, run_serve

    status = cli_contract.current()
    if status is not None and status.json_mode:
        # stdout carries the MCP protocol here — an envelope would corrupt it
        return cli_contract.usage_error(
            "mcp serve speaks the MCP protocol on stdout; --json would corrupt it",
            hint="run it without --json (the server's own status lines go to stderr)",
        )
    context = _registry_context(args)
    runtime = context.runtime_dir
    project_id = context.project_id
    include_legacy = bool(getattr(args, "all", False))
    store = _tools_store_rw(context)
    try:
        count = len(
            approved_python_specs(
                store, runtime=runtime, project_id=project_id, include_legacy=include_legacy
            )
        )
    finally:
        store.close()
    from .capability import visible_tools
    from .mcpserver import scope_conflicts

    for conflict in scope_conflicts(
        visible_tools(runtime, project_id, include_legacy=include_legacy)
    ):
        print(
            f"warning: name conflict '{conflict['name']}' not exported "
            "(resolve scope/version)",
            file=sys.stderr,
        )
    scope_note = f", scope: {'Projekt ' + project_id[:12] if project_id else 'global'}"
    if include_legacy:
        scope_note += " + legacy"
    print(
        f"skaldir MCP server '{args.name}' — {count} approved tool(s){scope_note}, stdio",
        file=sys.stderr,
    )
    run_serve(
        context.tools_db,
        name=args.name,
        runtime=runtime,
        project_id=project_id,
        include_legacy=include_legacy,
    )
    return 0


def _gv_runtime() -> Path:
    """Runtime dir from the ONE registry default (§1.2 — no second default)."""
    from .registry import default_runtime_dir

    return default_runtime_dir()


def _completion_snapshot(state: dict) -> tuple[int, int, str]:
    """(verified, total, last_status) for the goal's definition of done."""
    from .completion import CompletionStore

    store = CompletionStore(_gv_runtime())
    last = None
    for row in reversed(store.timeline(limit=200)):
        if row.get("kind") == "verification":
            last = row
            break
    failed_blob = " || ".join(
        list((last or {}).get("blocking_failures") or [])
        + list((last or {}).get("major_failures") or [])
    )
    dod = state.get("dod") or []
    verified = sum(1 for item in dod if str(item.get("text") or "") not in failed_blob)
    return verified, len(dod), str((last or {}).get("status") or "-")


def _cmd_completion_status(args: argparse.Namespace) -> int:
    from .completion import TERMINAL_STATES, CompletionStore

    store = CompletionStore(_gv_runtime())
    state = store.state()
    current = state.get("state") or "WORKING"
    verified, total, last_status = _completion_snapshot(state)
    last_row = None
    for row in reversed(store.timeline(limit=200)):
        if row.get("kind") == "verification":
            last_row = row
            break
    metrics = store.metrics()
    cli_contract.set_data(
        {
            "state": current,
            "terminal": current in TERMINAL_STATES,
            "goal": state.get("goal") or None,
            "revision": int(state.get("revision") or 0),
            "verified": int(verified),
            "total": int(total),
            "last_verdict": (
                {
                    "status": last_row.get("status"),
                    "at": last_row.get("at"),
                    "blocking_failures": list(last_row.get("blocking_failures") or []),
                    "major_failures": list(last_row.get("major_failures") or []),
                }
                if last_row
                else None
            ),
            "criteria": [
                {"text": str(item.get("text") or ""), "blocking": bool(item.get("blocking"))}
                for item in (state.get("dod") or [])
            ],
            "metrics": metrics,
            "runtime": str(_gv_runtime()),
        }
    )
    cli_contract.add_evidence(
        str(Path(_gv_runtime()) / "completion"), "file", "completion store that was read"
    )
    print(f"state:    {current}" + (" (terminal)" if current in TERMINAL_STATES else ""))
    print(f"goal:     {state.get('goal') or '(none)'}")
    print(f"last:     {last_status}")
    if total:
        print(f"progress: {verified}/{total} requirements without open failures")
    print(
        "metrics:  requests={completion_requests} rejections={completion_rejections} "
        "verified={verified_completions} "
        "false_completion_prevented={false_completion_prevented}".format(**metrics)
    )
    return EXIT_OK


def _cmd_completion_history(args: argparse.Namespace) -> int:
    from .completion import CompletionStore

    store = CompletionStore(_gv_runtime())
    rows = store.timeline(limit=int(getattr(args, "limit", 40)))
    cli_contract.set_data({"rows": rows, "count": len(rows)})
    cli_contract.add_evidence(
        str(Path(_gv_runtime()) / "completion"), "file", "completion store that was read"
    )
    if not rows:
        print("no completion history yet")
        return EXIT_OK
    for row in rows:
        kind = row.get("kind")
        if kind == "state":
            reason = str(row.get("reason") or "")[:80]
            print(f"[state]  {row.get('state')}  {reason}")
        elif kind == "completion_request":
            claims = len(row.get("claimed_completed") or [])
            print(f"[req]    {row.get('attempt_id')}  claims={claims}")
        elif kind == "verification":
            blockers = list(row.get("blocking_failures") or [])
            print(f"[verdict] {row.get('status')}  blockers={len(blockers)}")
            for blob in blockers[:3]:
                print(f"          - {str(blob)[:100]}")
    return EXIT_OK


def _cmd_completion_begin(args: argparse.Namespace) -> int:
    """Record a goal. Exit 3 while no loop is wired: nothing continues afterwards."""
    from .completion import CompletionStore, DodItem

    criteria = [str(c) for c in (getattr(args, "criterion", None) or []) if str(c).strip()]
    dod = [DodItem(id=f"c{i + 1}", text=text, blocking=True) for i, text in enumerate(criteria)]
    store = CompletionStore(_gv_runtime())
    record = store.begin_goal("cli", str(args.goal), dod)
    cli_contract.set_data(
        {
            "goal": str(args.goal),
            "goal_id": str(record.get("goal_id") or ""),
            "state": str(record.get("state") or "WORKING"),
            "revision": int(record.get("revision") or 1),
            "criteria": [{"id": item.id, "text": item.text, "blocking": True} for item in dod],
            "loop_wired": False,
            "started_at": str(record.get("started") or ""),
        }
    )
    cli_contract.add_evidence(
        str(Path(_gv_runtime()) / "completion"), "file", "completion store the goal was written to"
    )
    print(f"goal recorded: {args.goal} (criteria: {len(dod)}) — state WORKING")
    print("BLOCKED: no execution loop is wired to this CLI — nothing continues after this command")
    return cli_contract.blocked(
        "the goal was recorded, but no execution loop is wired: nothing keeps working "
        "after this command returns",
        hint="`skaldir completion status --json` shows the record; a run lifecycle is not "
             "wired to the CLI yet",
        next_command="skaldir completion status --json",
        reason="read the goal state (revision, criteria, metrics)",
    )


def _cmd_completion_override(args: argparse.Namespace) -> int:
    from .completion import CompletionStore

    reason = str(getattr(args, "reason", "") or "").strip()
    if not reason:
        print("override requires --reason (the override must be explicit)")
        return cli_contract.usage_error(
            "override requires --reason",
            hint="--reason is required and is written to the ledger",
        )
    store = CompletionStore(_gv_runtime())
    store.transition("SHIPPED_WITH_USER_OVERRIDE", reason=reason[:300])
    cli_contract.set_data(
        {
            "state": "SHIPPED_WITH_USER_OVERRIDE",
            "reason": reason[:300],
            "is_verified_complete": False,
        }
    )
    cli_contract.add_evidence(
        str(Path(_gv_runtime()) / "completion"), "file", "completion store that was written"
    )
    print("override recorded: SHIPPED_WITH_USER_OVERRIDE (this is never VERIFIED_COMPLETE)")
    return EXIT_OK


def _cmd_tools_mine(args: argparse.Namespace) -> int:
    from .capability_mining import mine

    summary = mine(_gv_runtime())
    print(
        f"scanned {summary['scanned_requests']} capability request(s) and "
        f"{summary['scanned_commands']} command run(s) → "
        f"{summary['candidates_recorded']} candidate update(s) recorded"
    )
    # The command declares a payload, so the envelope must carry it: a success
    # envelope with `data: null` while the human line names 26 candidates is a lie
    # the contract's own payload check catches (`data_available: true`).
    cli_contract.set_data(
        {
            "scanned_requests": summary["scanned_requests"],
            "scanned_commands": summary["scanned_commands"],
            "candidates_recorded": summary["candidates_recorded"],
            "runtime": str(_gv_runtime()),
        }
    )
    cli_contract.add_evidence(
        str(Path(_gv_runtime()) / "capability_candidates.jsonl"), "file",
        "the candidate store the scan updated",
    )
    return EXIT_OK


def _cmd_tools_candidates(args: argparse.Namespace) -> int:
    from .capability_mining import CandidateStore

    rows = [row for row in CandidateStore(_gv_runtime()).all() if row.get("status") != "dismissed"]
    if not rows:
        print("no candidates yet — run `skaldir tools mine` after some sessions")
        return 0
    for row in rows:
        print(
            f"{row.get('id')}  [{row.get('status')}]  {row.get('suggested_type')}  "
            f"{str(row.get('capability'))[:80]}  (x{row.get('occurrences')}, "
            f"{row.get('sessions')} session(s))"
        )
    return 0


def _cmd_tools_promote(args: argparse.Namespace) -> int:
    from .capability_mining import promote

    try:
        result = promote(_gv_runtime(), str(args.candidate))
    except ValueError as exc:
        message = str(exc)
        print(f"promote failed: {message}")
        if "unknown candidate" in message:
            return cli_contract.not_found(
                message, hint="`skaldir tools candidates` prints the mined candidate ids"
            )
        return cli_contract.blocked(
            message,
            hint="skill candidates follow the skill flow, not the tool build flow",
        )
    cli_contract.set_data(
        {
            "candidate_id": result["candidate"].get("id"),
            "request_id": result["request_id"],
            "kind": result["kind"],
            "capability": result["candidate"].get("capability"),
        }
    )
    cli_contract.add_evidence(
        str(Path(_gv_runtime()) / "capability_requests.jsonl"),
        "file",
        "capability request the candidate was promoted into",
    )
    print(
        f"promoted {result['candidate'].get('id')} → capability request "
        f"{result['request_id']} ({result['kind']}); "
        "run `skaldir tools fulfill` to build it"
    )
    return EXIT_OK


def _cmd_cu_check(args: argparse.Namespace) -> int:
    """``cu check`` — the same honest matrix ``act check`` reports (the legacy verb).

    Two defects lived here and both were *display* defects: ``live_desktop`` was the
    hard-coded literal ``False`` and the warning claimed no live desktop driver was
    wired. Neither is true any more — ``act_service.capability_report()`` carries the
    measured bridges/capabilities and ``cu_host_session.live_desktop_optin()`` the
    measured opt-in (refused unless an operator set the switch *and* named a target).
    Read-only: no container is started and nothing is executed.
    """
    from . import act_service as _act_service

    report = _act_service.capability_report()
    levels = runner_availability()
    runners = _cu_runner_facts()
    bridges = list(report.get("bridges") or [])
    capabilities = list(report.get("capabilities") or [])
    optin = dict(report.get("live_desktop_optin") or {})
    policy = dict(report.get("policy") or {})
    live_allowed = bool(report.get("live_desktop_allowed"))
    cli_contract.set_data(
        {
            "runners": runners,
            "levels_reported": sorted(levels),
            "host": report.get("host"),
            "bridges": bridges,
            "capabilities": capabilities,
            "capability_names": list(report.get("capability_names") or []),
            "verdict": report.get("verdict"),
            "summary": report.get("summary"),
            # the measured opt-in, not a literal: `allowed` is only true when the switch is
            # set *and* a target is named (an opt-in without a target is not an opt-in).
            "live_desktop": live_allowed,
            "live_desktop_optin": optin,
            # the policy that is really enforced, with its live-desktop fields (which the
            # executor re-checks against the runner's own target scope).
            "policy": policy,
        }
    )
    cli_contract.add_evidence(
        "src/skaldir/act_service.py:610",
        "code",
        "capability_report() — bridges, capabilities and the measured live-desktop opt-in",
    )
    cli_contract.add_evidence(
        "src/skaldir/cu_host_session.py:113",
        "code",
        "live_desktop_optin() — the switch, the target and the reason it names",
    )
    for name, detail in levels.items():
        print(f"{name}: {detail}")
    print(f"verdict: {report.get('verdict')}")
    for record in bridges:
        state = "available" if record.get("available") else "refused"
        print(f"bridge {record.get('id')}: {state} — {record.get('reason')}")
    cli_contract.warn(
        "the live desktop is a measured refusal, not a missing feature: "
        + str(optin.get("reason") or "the opt-in could not be read")
    )
    cli_contract.next_action(
        "skaldir act check --probe-image --json",
        "the same matrix, with the tools inside the GUI image really probed",
    )
    return EXIT_OK


def _cmd_cu_demo(args: argparse.Namespace) -> int:
    """In-process ordering demo — deliberately NOT computer use (see `data.scope`)."""
    policy = CUPolicy()
    runner = DryRunRunner(
        behaviors={
            "deploy via API": ActionResult(ok=False, detail="HTTP 500"),
            "close window": ActionResult(ok=True, stdout="window closed"),
        }
    )
    attempts = [
        Attempt(
            channel="vision",
            action=Action(kind="mouse", payload={"x": 10, "y": 10}, description="click the button"),
            verify=Verification(expect_observe_contains="closed"),
        ),
        Attempt(
            channel="api",
            action=Action(kind="cli", argv=["deploy", "--api"], description="deploy via API"),
            verify=Verification(expect_stdout_contains="deployed"),
        ),
        Attempt(
            channel="dom",
            action=Action(
                kind="dom_click", payload={"selector": "#close"}, description="close window"
            ),
            verify=Verification(expect_stdout_contains="window closed"),
        ),
    ]
    executor = CUExecutor(policy)
    result = executor.run("demo-step", attempts, runner)
    order = [attempt.channel for attempt in order_attempts(attempts)]
    cli_contract.set_data(
        {
            "scope": "in-process fixture",
            "live": False,
            "desktop_touched": False,
            "order": order,
            "status": result.status,
            "channel": result.channel,
            "actions": result.actions,
            "fallbacks": result.fallbacks,
        }
    )
    cli_contract.add_evidence(
        "src/skaldir/computer_use.py:151", "code", "DryRunRunner — scripted, side-effect free"
    )
    cli_contract.warn(
        "no computer use happened: this is an in-process fixture (scripted runners), "
        "not a desktop, not a window, not a real action"
    )
    cli_contract.next_action(
        "skaldir cu check --json", "the real runner/isolation facts on this host"
    )
    print("Order (API > DOM > Vision):")
    for attempt in order_attempts(attempts):
        print(f"  - {attempt.channel}: {attempt.action.description}")
    print(
        f"Status: {result.status} · channel: {result.channel} · "
        f"actions: {result.actions} · fallbacks: {result.fallbacks}"
    )
    print(f"verification: {result.detail}")
    print("scope: in-process fixture — no desktop, no window, no real action was taken")
    return 0 if result.status == "verified" and result.channel == "dom" else 1


def _cmd_spend_allowance(args: argparse.Namespace) -> int:
    state = TreasuryState(args.db)
    try:
        rules = PRESETS.get(args.tier, PRESETS["stufe0"])
        ledger = SpendLedger(state, rules)
        print(json.dumps(ledger.allowance(), ensure_ascii=False, indent=2, sort_keys=True))
    finally:
        state.close()
    return 0


def _vault_from_args(args: argparse.Namespace):
    key = getenv("VAULT_KEY")
    if not key:
        print("SKALDIR_VAULT_KEY not set (master secret — never put it in argv/history)")
        return None
    return Vault(args.vault, key)


def _cmd_vault_list(args: argparse.Namespace) -> int:
    vault = _vault_from_args(args)
    if vault is None:
        # legacy exit code: tests/test_treasury.py::test_vault_cli_requires_key pins 1
        # (the designed code is 4 = needs_credentials; flip both together)
        return EXIT_USAGE
    infos = vault.list_handles()
    cli_contract.set_data(
        {
            "handles": [
                {
                    "handle": info.handle,
                    "kind": info.kind,
                    "scopes": list(info.scopes),
                    "description": info.description,
                }
                for info in infos
            ],
            "count": len(infos),
            "vault": str(args.vault),
        }
    )
    cli_contract.add_evidence(str(args.vault), "file", "credential vault that was read")
    if not infos:
        print("no handles in the vault")
    for info in infos:
        scopes = ",".join(info.scopes)
        print(f"{info.handle} [{info.kind}] scopes={scopes} — {info.description}")
    return EXIT_OK


def _cmd_vault_describe(args: argparse.Namespace) -> int:
    vault = _vault_from_args(args)
    if vault is None:
        # legacy exit code: tests/test_treasury.py::test_vault_cli_requires_key pins 1
        # (the designed code is 4 = needs_credentials; flip both together)
        return EXIT_USAGE
    info = vault.describe(args.handle)
    if info is None:
        print(f"unknown handle: {args.handle}")
        return cli_contract.not_found(
            f"unknown handle: {args.handle}",
            hint="`skaldir vault list` prints the handles (metadata only)",
        )
    payload = info.to_dict()
    cli_contract.set_data(payload)
    cli_contract.add_evidence(str(args.vault), "file", "credential vault that was read")
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return EXIT_OK


def _cmd_vault_add(args: argparse.Namespace) -> int:
    vault = _vault_from_args(args)
    if vault is None:
        # legacy exit code: tests/test_treasury.py::test_vault_cli_requires_key pins 1
        # (the designed code is 4 = needs_credentials; flip both together)
        return EXIT_USAGE
    secret = getpass.getpass(f"Secret for secret://{args.service}/{args.name}: ")
    if not secret:
        print("empty secret — aborted")
        return cli_contract.usage_error(
            "empty secret — aborted", hint="a handle without a secret is not stored"
        )
    scopes = [part.strip() for part in (args.scopes or "use").split(",") if part.strip()]
    handle = vault.add(
        service=args.service,
        name=args.name,
        secret=secret,
        scopes=scopes,
        description=args.description or "",
    )
    cli_contract.set_data(
        {"handle": handle, "service": args.service, "name": args.name, "scopes": scopes}
    )
    cli_contract.add_evidence(str(args.vault), "file", "credential vault that was written")
    print(f"added: {handle} (secret never passed through argv/chat/history)")
    return EXIT_OK


def _cmd_vault_revoke(args: argparse.Namespace) -> int:
    vault = _vault_from_args(args)
    if vault is None:
        # legacy exit code: tests/test_treasury.py::test_vault_cli_requires_key pins 1
        # (the designed code is 4 = needs_credentials; flip both together)
        return EXIT_USAGE
    if not vault.revoke(args.handle):
        print(f"unknown handle: {args.handle}")
        return cli_contract.not_found(
            f"unknown handle: {args.handle}",
            hint="`skaldir vault list` prints the handles (metadata only)",
        )
    cli_contract.set_data({"handle": args.handle, "revoked": True})
    cli_contract.add_evidence(str(args.vault), "file", "credential vault that was written")
    print(f"revoked: {args.handle}")
    return EXIT_OK


def _cmd_spend_request(args: argparse.Namespace) -> int:
    state = TreasuryState(args.db)
    try:
        rules = PRESETS.get(args.tier, PRESETS["stufe0"])
        ledger = SpendLedger(state, rules)
        request = ledger.request(
            amount=args.amount,
            merchant=args.merchant,
            purpose=args.purpose,
            currency=args.currency,
        )
    finally:
        state.close()
    suffix = f" ({request.reason})" if request.reason else ""
    print(
        f"{request.request_id} [{request.status}] {request.amount:.2f} "
        f"{request.currency} → {request.merchant}{suffix}"
    )
    return 0 if request.status != "denied" else 1


def _cmd_spend_list(args: argparse.Namespace) -> int:
    state = TreasuryState(args.db)
    try:
        open_requests = state.open_spends()
    finally:
        state.close()
    if not open_requests:
        print("no open spend requests")
    for request in open_requests:
        print(
            f"{request.request_id} [{request.status}] {request.amount:.2f} "
            f"{request.currency} → {request.merchant} — {request.purpose}"
        )
    return 0


def _cmd_spend_approve(args: argparse.Namespace) -> int:
    state = TreasuryState(args.db)
    try:
        ledger = SpendLedger(state, PRESETS["stufe0"])
        ok = ledger.approve(args.request)
    finally:
        state.close()
    print(("approved: " if ok else "not possible: ") + args.request)
    return 0 if ok else 1


def _cmd_spend_deny(args: argparse.Namespace) -> int:
    state = TreasuryState(args.db)
    try:
        ledger = SpendLedger(state, PRESETS["stufe0"])
        ok = ledger.deny(args.request, reason=args.reason or "")
    finally:
        state.close()
    print(("denied: " if ok else "not possible: ") + args.request)
    return 0 if ok else 1


def _cmd_spend_complete(args: argparse.Namespace) -> int:
    state = TreasuryState(args.db)
    try:
        ledger = SpendLedger(state, PRESETS["stufe0"])
        ok = ledger.complete(args.request)
    finally:
        state.close()
    prefix = "completed: " if ok else "not possible (not approved or intent expired): "
    print(prefix + args.request)
    return 0 if ok else 1


_HELP_EPILOG = """\
Areas:
  contract & health   contract (list · show · dump) · selftest · doctor
  goal lock           run (plan · turns · gate · verify · resume)
  voice & memory      metrics · nightshift · memory-stats · memory-search
  skills & tools      skills · tools · capability · mcp
  workflows           workflows (list · show · run)
  safety & proof      eval · scan
  treasury            vault · spend
  desktop             desktop (install · status · start · uninstall)

Every command takes --json: stdout is then one JSON envelope
({schema_version, ok, command, exit_code, data, error, evidence, warnings, next_actions}).
Exit codes: 0 ok · 1 usage · 2 verification failed · 3 blocked · 4 needs credentials ·
5 not found · 6 conflict · 7 internal · 8 unknown.

Examples:
  skaldir selftest --json        which of the eight layers is reachable right now
  skaldir contract dump --json   every command, its args and its data schema
  skaldir run "<goal>"           work the goal until the declared checks pass
  skaldir run --resume <goal-id> continue a goal after a crash, from its checkpoint
  skaldir doctor                 deployment + health check
  skaldir desktop start          desktop app (last session, else a new chat)
"""


def build_parser() -> argparse.ArgumentParser:
    parser = cli_contract.ContractArgumentParser(
        prog="skaldir",
        description="Skaldir Agent — core CLI (pact, ledger, voice, memory, skills, metrics, eval)",
        epilog=_HELP_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        add_help=False,
    )
    parser.add_argument("--version", action="version", version=f"skaldir {__version__}")
    # The curated front door (MVP/68-slash-surface.md §3). The top-level *list* is smaller,
    # the capability is not: every root command below stays in the parser, keeps its handler
    # and stays reachable — `-h` shows the dozen a session actually types, and
    # `skaldir --all-commands` (or `skaldir ?`) prints all of them with their one-liners.
    # `slash` owns both texts and the action, so the wording, the counts and the wording of
    # the escape hatches cannot drift between the help screen, the docs and the tests.
    from .slash import CuratedHelpAction as _CuratedHelp

    parser.add_argument(
        "-h",
        "--help",
        action=_CuratedHelp,
        help="the curated command list (`skaldir --all-commands` for every root command)",
    )
    parser.add_argument(
        "--all-commands",
        action="store_true",
        help="print every root command this build carries (the full list)",
    )
    # the usage line names the placeholder, not the 61 choices: the full list belongs in
    # `--all-commands`, not in every error message
    sub = parser.add_subparsers(dest="command", required=False, metavar="<command>")

    # Jev decision façade (ROUTE): the group owns its verbs and the five
    # `cli_contract.COMMANDS` entries for them — see the hand-over note in
    # src/skaldir/jev/cli.py.
    from .jev.cli import register_cli as _register_jev_cli

    _register_jev_cli(sub)

    # --- The confirmed surface (UI-01..08, DESK-01..04, STOP-01..05):
    #     `skaldir tui …`, `skaldir stop …`, `skaldir desk …`. One owner for the three
    #     groups, because they are one confirmed decision set — see src/skaldir/surface_cli.py.
    from .surface_cli import register_cli as _register_surface

    _register_surface(sub)

    # --- CLINE-PERF-1: bounded-rate streaming. Two lines, paste-ready, right after
    #     `_register_jev_cli(sub)` inside build_parser() ---
    from .streaming import register_cli as _register_stream

    _register_stream(sub)

    # PERSIST — typed long-term memory (MVP/31): the group owns its verbs and
    from .knowledge_cli import register_cli as _register_knowledge

    _register_knowledge(sub)
    # their `cli_contract.COMMANDS` entries, like jev/toolbuilder do.
    from .memory_upgrade_cli import register_cli as _register_memory

    _register_memory(sub)

    # ----------------------------------------------------------------------- #
    # The delivered group hooks. Each module owns its verbs *and* the
    # `cli_contract.COMMANDS` entries for them (``install_contract_entries`` or a
    # literal table it installs at registration time), so `contract dump --json`
    # stays the single source of truth and `unregistered` stays empty. The
    # registration is idempotent where the module says so, which is what lets a
    # test wire the same group into the real parser a second time.
    # ----------------------------------------------------------------------- #

    # H2 — the built-in (core) toolset: terminal, files, patch, search, web.
    from .tools_core.cli import register_cli as _register_tools_core

    _register_tools_core(sub)

    # EXPAND — the container runtime the agent may start password-free (`MVP/71-docker-ensure.md`).
    # One call wires all three surfaces: the `docker ensure` verb, its `cli_contract.COMMANDS`
    # entry (so `unregistered` stays empty) and the capability's `selftest` probe.
    from .docker_ensure_cli import register_cli as _register_docker_ensure

    _register_docker_ensure(sub)

    # H3/H7 — the interactive shell (HERMES parity: one session, product verbs).
    from .repl import register_cli as _register_shell

    _register_shell(sub)

    # …and the shell's own `chat` alias, which the group registers through
    # `chatbar.add_chat_parser` (the alias path) — that path adds the parser but not the
    # contract entry, so the `chat` group is wired here explicitly, exactly as
    # `chatbar.register_cli` does on its own wiring path. Without this, `chat` is reachable
    # and `unregistered` is not empty (it also installs the group's `cli_contract.COMMANDS`
    # entries; the call is idempotent, so the alias path stays the only parser node).
    from .chatbar import register_cli as _register_chat

    _register_chat(sub)

    # COMPOSE — the activity vocabulary (`MVP/67-activity-art.md`): one mark per thing the
    # agent does, drawn in the frame's live row and in the transcript. `register_cli` is
    # idempotent; `install_contract_entries` lands the group's two
    # `cli_contract.COMMANDS` entries (`art.preview` / `art.json`), so the group cannot
    # ship without a contract (`unregistered` stays empty).
    from .art import install_contract_entries as _install_art_entries
    from .art import register_cli as _register_art

    _register_art(sub)
    _install_art_entries()

    # The CLI's own visual language (`style`), shared by every renderer.
    from .theme import register_cli as _register_style

    _register_style(sub)

    # VERIFY — real-world verification runs (plan / run / status / evidence).
    from .verify_cli import register_cli as _register_verify

    _register_verify(sub)

    # PERSIST — the durable run store and the goal record (`runs` + `goal`).
    from .runs_cli import register_cli as _register_runs

    _register_runs(sub)

    # PERSIST — the durable session history (MVP/40): the message store behind
    # `session list|show|search|resume|export` plus the append/fork/rotate/purge
    # verbs. The group owns its own `cli_contract.COMMANDS` entries.
    from .session_cli import register_cli as _register_session

    _register_session(sub)

    # COMPOSE — learned composite tools: create / validate / run / show.
    from .compose_cli import register_cli as _register_compose

    _register_compose(sub)

    # ACT — Computer Use: runner probes and real action lists / sub-tools.
    from .act_cli import register_cli as _register_act

    _register_act(sub)

    # Provider configuration (named providers, redacted views, tiers).
    from .provider_cli import register_cli as _register_provider

    _register_provider(sub)

    # First-run setup — the two mandatory keys (a TypeSafe/Jev provider and one LLM
    # provider), each proved by a real call. Owns its own `cli_contract` entry.
    from .setup_cli import register_cli as _register_setup

    _register_setup(sub)

    # The model picker (`MVP/73-model-picker.md`): change one of those two keys without
    # re-running the whole first run — role → provider (its base URL pre-filled) → model →
    # one hidden key. The same engine answers `/model` inside the chat frame
    # (`model_picker.handle_slash`). Owns its own `cli_contract` entry.
    from .model_cli import register_cli as _register_model

    _register_model(sub)

    # SUPERVISE — the Voice layer's own verbs (health, findings, triage).
    from .voice_cli import register_cli as _register_voice

    _register_voice(sub)

    # EXPAND gap C + D share ONE `image` group: the shell-free inspector creates
    # it (``inspect`` / ``layers``) and the digest-pin module attaches its verbs
    # (``resolve`` / ``pin`` / ``verify`` / ``guard`` / ``list``) to it. Two
    # ``add_parser("image", …)`` calls would be an argparse conflict.
    from .image_inspect_cli import register_cli as _register_image_inspect
    from .image_pin_cli import attach as _attach_image_pin

    _attach_image_pin(_register_image_inspect(sub))

    # ENFORCE/security — the audit of the CLI surface itself.
    from .security_audit_cli import register_cli as _register_security

    _register_security(sub)

    # The API tier: `serve` (stdio MCP, in-process) and `daemon serve --http`.
    from .api_server_cli import register_cli as _register_api_server

    _register_api_server(sub)

    # H6 — the host bridge (read-only facts about the Hermes host).
    from .host_cli import register_cli as _register_host

    _register_host(sub)

    # Warm sessions: pre-warmed context the CLI can reuse across calls.
    from .warm_cli import register_cli as _register_warm

    _register_warm(sub)

    # ENFORCE — the agent loop itself (`agent run` / `agent transcript`) …
    from .agent_loop_cli import register_cli as _register_agent_loop

    _register_agent_loop(sub)

    # … and H6's parallel subagent harness, which extends the SAME `agent` group.
    from .subagents_cli import register_cli as _register_subagents

    _register_subagents(sub)

    # H3 — the prompt assembler (sections, byte-stable fingerprint).
    from .prompt_builder_cli import register_cli as _register_prompt

    _register_prompt(sub)

    # Vision: screenshot/describe steps the agent loop and ACT can call.
    from .vision_cli import register_cli as _register_vision

    _register_vision(sub)

    # Project context: what this project is, as the agent sees it.
    from .project_context_cli import register_cli as _register_project

    _register_project(sub)

    # H8 — programmatic tool calling (`code run` / `code explain`) and the repo
    # map (`code map` / `code search` / `code symbols` / …) share ONE `code`
    # group. Both modules are "create-or-return": whichever runs second gets the
    # existing group back and adds nothing, so the group is created here once and
    # the second verb set is attached to its command action explicitly. Dropping
    # either set silently would be a lie in the contract dump.
    from . import cli_shared as _cli_shared

    # One statement per alias: isort's `combine_as_imports = false` forbids mixing an
    # aliased import with a plain member in one block.
    from .code_exec_cli import _add_code_verbs as _add_code_exec_verbs
    from .code_exec_cli import proposed_contract_entries as _code_exec_entries
    from .code_exec_cli import register_cli as _register_code_exec
    from .codectx_cli import register_cli as _register_codectx

    _code_group = _register_codectx(sub)
    _register_code_exec(sub)
    _code_verbs = next(
        (
            action
            for action in getattr(_code_group, "_actions", [])
            if isinstance(action, argparse._SubParsersAction)
        ),
        None,
    )
    if _code_verbs is not None and "run" not in getattr(_code_verbs, "choices", {}):
        _add_code_exec_verbs(_code_verbs)
    _cli_shared.install_contract_entries(_code_exec_entries(), cli_contract)

    # H7 — file editing as first-class verbs (apply / dry-run / undo / show).
    from .edit_cli import register_cli as _register_edit

    _register_edit(sub)

    # The adversarial release cases (many isolated runs, one deterministic verdict).
    from .adversarial_cli import register_cli as _register_adversarial

    _register_adversarial(sub)

    # The permission gate (`permission show/set/allow/deny`) and the approval queue.
    from .approval_cli import register_cli as _register_approval

    _register_approval(sub)

    # Context compaction (ROUTE): the live trigger, the preserved set and the dry run
    # ride the very `context` group the line above just created.
    from .context_manager import register_cli as _register_context_manager

    _register_context_manager(sub)

    # ENFORCE — the product command of the goal lock: one goal, one bounded multi-turn
    # loop, the declared checks really run, the gate decides, and exit 0 only on a
    # verification PASS. Owns its own `cli_contract.COMMANDS` entry.
    from .goal_run_cli import register_cli as _register_goal_run

    _register_goal_run(sub)

    # PERSIST — run telemetry (duration · tok/s · context usage), the unified
    # checkpoints (`checkpoint`), the coding-agent verbs (`coding`) and the prompt-cache
    # surface (`prompt cache`). Each group installs its own `cli_contract.COMMANDS`
    # entries at registration, so `unregistered` cannot drift while they are wired here.
    from .telemetry_cli import register_cli as _register_telemetry

    _register_telemetry(sub)

    from .checkpoints_cli import register_cli as _register_checkpoints

    _register_checkpoints(sub)

    from .coding_features_cli import register_cli as _register_coding_features

    _register_coding_features(sub)

    from .prompt_cache_cli import register_cli as _register_prompt_cache

    _register_prompt_cache(sub)

    p_init = sub.add_parser("init", help="create .godsv/ with an example pact + ledger")
    p_init.add_argument("--dir", default=DEFAULT_DIR)
    p_init.add_argument("--force", action="store_true")
    p_init.set_defaults(func=_cmd_init)

    p_pact = sub.add_parser("pact-show", help="print the pact")
    p_pact.add_argument("--dir", default=DEFAULT_DIR)
    p_pact.set_defaults(func=_cmd_pact_show)

    p_ledger = sub.add_parser("ledger-show", help="print the rendered ledger")
    p_ledger.add_argument("--dir", default=DEFAULT_DIR)
    p_ledger.set_defaults(func=_cmd_ledger_show)

    p_demo = sub.add_parser("demo", help="run the deterministic V0 pipeline demo")
    p_demo.add_argument("--db", default=None)
    p_demo.set_defaults(func=_cmd_demo)

    p_eval = sub.add_parser("eval", help="run the deterministic eval episodes (CI-able)")
    p_eval.set_defaults(func=_cmd_eval)

    p_scan = sub.add_parser("scan", help="scan a file (or -) for secrets")
    p_scan.add_argument("path")
    p_scan.set_defaults(func=_cmd_scan)

    p_prompts = sub.add_parser("prompts", help="prompt registry utilities")
    prompts_sub = p_prompts.add_subparsers(dest="subcommand", required=True)
    p_list = prompts_sub.add_parser("list", help="list prompts and check required ids")
    p_list.set_defaults(func=_cmd_prompts_list)

    p_mstats = sub.add_parser("memory-stats", help="incident/note counts of a memory db")
    p_mstats.add_argument("--db", default=_runtime_file("memory.db"))
    p_mstats.set_defaults(func=_cmd_memory_stats)

    p_msearch = sub.add_parser("memory-search", help="evidence view over incident/note memory")
    p_msearch.add_argument("query", nargs="?", default=None)
    p_msearch.add_argument("--db", default=_runtime_file("memory.db"))
    p_msearch.add_argument("--signature", default=None)
    p_msearch.add_argument("--limit", type=int, default=5)
    p_msearch.set_defaults(func=_cmd_memory_search)

    p_maudit = sub.add_parser(
        "memory-audit", help="read-only audit: event duplicates + retention + schema axes"
    )
    p_maudit.add_argument("--events", default=_runtime_file("events.db"))
    p_maudit.add_argument("--max-age-days", dest="max_age_days", type=float, default=30.0)
    p_maudit.set_defaults(func=_cmd_memory_audit)

    p_mcheck = sub.add_parser(
        "memory-retrieval-check",
        help="apply the retrieval rule (retrieve → applicability → use/reject) to a problem",
    )
    p_mcheck.add_argument("problem", nargs="?", default="")
    p_mcheck.add_argument("--db", default=_runtime_file("memory.db"))
    p_mcheck.add_argument("--component", default=None)
    p_mcheck.add_argument("--failure-class", dest="failure_class", default=None)
    p_mcheck.add_argument("--goal-scope", dest="goal_scope", default=None)
    p_mcheck.add_argument("--signature", default=None)
    p_mcheck.add_argument("--limit", type=int, default=5)
    p_mcheck.add_argument("--min-confidence", dest="min_confidence", type=float, default=0.5)
    p_mcheck.add_argument("--verbose", action="store_true")
    p_mcheck.set_defaults(func=_cmd_memory_retrieval_check)

    p_night = sub.add_parser(
        "nightshift", help="consolidate a finished session into memory (dry-run default)"
    )
    p_night.add_argument("--events", default=_runtime_file("events.db"))
    p_night.add_argument("--memory", default=_runtime_file("memory.db"))
    p_night.add_argument("--session", required=True)
    p_night.add_argument("--apply", action="store_true")
    p_night.set_defaults(func=_cmd_nightshift)

    p_metrics = sub.add_parser("metrics", help="recurrence-kill-rate + operational counters")
    p_metrics.add_argument("--events", default=_runtime_file("events.db"))
    p_metrics.add_argument("--memory", default=_runtime_file("memory.db"))
    p_metrics.add_argument(
        "--session-id",
        default=None,
        help="add host-measured token usage for this session (from the Hermes state DB)",
    )
    p_metrics.add_argument(
        "--session-db",
        default=None,
        help="path to the Hermes state DB (default: $HERMES_HOME/state.db with --session-id)",
    )
    p_metrics.set_defaults(func=_cmd_metrics)
    p_hub = sub.add_parser(
        "tools-hub", help="local tool registry: discover/search capabilities, build hub manifests"
    )
    p_hub.add_argument(
        "--runtime",
        default=None,
        help="runtime dir (default: $SKALDIR_HOME or ~/.hermes/godsv — where generated_tools live)",
    )
    p_hub.add_argument("--search", default=None, help="word-overlap search over name/purpose/type")
    p_hub.add_argument("--show", default=None, help="detail for one entry")
    p_hub.add_argument(
        "--manifest", default=None, help="build the shareable hub manifest for a tool"
    )
    p_hub.add_argument("--json", action="store_true")
    p_hub.set_defaults(func=_cmd_tools_hub)

    p_skills = sub.add_parser("skills", help="skill-candidate utilities")
    skills_sub = p_skills.add_subparsers(dest="subcommand", required=True)
    p_skills_list = skills_sub.add_parser("list", help="list skill candidates")
    p_skills_list.add_argument("--db", default=_runtime_file("memory.db"))
    p_skills_list.set_defaults(func=_cmd_skills_list)
    p_skills_install = skills_sub.add_parser(
        "install",
        help="install a skill: a builtin name, owner/repo, or a github.com URL",
    )
    p_skills_install.add_argument("target", help="builtin name | owner/repo | github URL")
    p_skills_install.add_argument("--db", default=_runtime_file("memory.db"))
    p_skills_install.add_argument(
        "--skills-dir",
        dest="skills_dir",
        default=None,
        help="install directory (default: skills/ under the runtime dir)",
    )
    p_skills_install.set_defaults(func=_cmd_skills_install)
    p_skills_mine = skills_sub.add_parser(
        "mine", help="mine repeated tool patterns into SKILL.md candidates"
    )
    p_skills_mine.add_argument("--events", default=_runtime_file("events.db"))
    p_skills_mine.add_argument("--db", default=_runtime_file("memory.db"))
    p_skills_mine.add_argument("--min-occurrences", type=int, default=3)
    p_skills_mine.add_argument("--min-sessions", type=int, default=2)
    p_skills_mine.add_argument("--apply", action="store_true")
    p_skills_mine.set_defaults(func=_cmd_skills_mine)

    for name, handler, help_text in (
        ("validate", _cmd_skills_validate, "re-verify a candidate against live events"),
        ("promote", _cmd_skills_promote, "promote after validation (propose-first review)"),
        ("demote", _cmd_skills_demote, "demote a skill for review"),
        ("show", _cmd_skills_show, "print the SKILL.md draft"),
        ("export", _cmd_skills_export, "write the SKILL.md to a directory"),
    ):
        review = skills_sub.add_parser(name, help=help_text)
        review.add_argument("name")
        review.add_argument("--db", default=_runtime_file("memory.db"))
        if name in ("validate", "promote"):
            review.add_argument("--events", default=_runtime_file("events.db"))
        if name == "promote":
            review.add_argument("--force", action="store_true")
        if name == "export":
            review.add_argument("--out", default=".")
        review.set_defaults(func=handler)

    p_tools = sub.add_parser("tools", help="tool-suite utilities (propose-first, gated)")
    tools_sub = p_tools.add_subparsers(dest="subcommand", required=True)
    p_tools_list = tools_sub.add_parser("list", help="list tool specs")
    p_tools_list.add_argument(
        "--db",
        default=None,
        help="explicit tools DB (default: the runtime DB; see `skaldir registry status`)",
    )
    p_tools_list.add_argument(
        "--runtime", default="", help="runtime dir (default: $HERMES_HOME/godsv)"
    )
    p_tools_list.set_defaults(func=_cmd_tools_list)
    p_tools_show = tools_sub.add_parser("show", help="print one tool spec as JSON")
    p_tools_show.add_argument("name")
    p_tools_show.add_argument(
        "--db",
        default=None,
        help="explicit tools DB (default: the runtime DB; see `skaldir registry status`)",
    )
    p_tools_show.add_argument(
        "--runtime", default="", help="runtime dir (default: $HERMES_HOME/godsv)"
    )
    p_tools_show.set_defaults(func=_cmd_tools_show)
    p_tools_scan = tools_sub.add_parser("scan", help="static security scan of a tool source file")
    p_tools_scan.add_argument("path")
    p_tools_scan.set_defaults(func=_cmd_tools_scan)
    p_tools_propose = tools_sub.add_parser("propose", help="propose a tool spec (JSON file)")
    p_tools_propose.add_argument("--spec", required=True)
    p_tools_propose.add_argument(
        "--db",
        default=None,
        help="explicit tools DB (default: the runtime DB; see `skaldir registry status`)",
    )
    p_tools_propose.add_argument(
        "--runtime", default="", help="runtime dir (default: $HERMES_HOME/godsv)"
    )
    p_tools_propose.set_defaults(func=_cmd_tools_propose)
    p_tools_approve = tools_sub.add_parser("approve", help="run gates, then approve (explicit)")
    p_tools_approve.add_argument("name")
    p_tools_approve.add_argument(
        "--db",
        default=None,
        help="explicit tools DB (default: the runtime DB; see `skaldir registry status`)",
    )
    p_tools_approve.add_argument("--workdir", default=".")
    p_tools_approve.add_argument("--force", action="store_true")
    p_tools_approve.add_argument(
        "--runtime", default="", help="runtime dir (default: $HERMES_HOME/godsv)"
    )
    p_tools_approve.set_defaults(func=_cmd_tools_approve)
    p_tools_promote_global = tools_sub.add_parser(
        "promote-global",
        help="promote a validated project tool to global — user-wide in this installation, "
        "never published; needs >=2 foreign project contexts and validated negative tests",
    )
    p_tools_promote_global.add_argument("name")
    p_tools_promote_global.add_argument(
        "--evidence-project",
        action="append",
        default=[],
        help="evidence project PATH or project id (repeatable; >=2 foreign contexts required)",
    )
    p_tools_promote_global.add_argument(
        "--db",
        default=None,
        help="explicit tools DB (default: the runtime DB; see `skaldir registry status`)",
    )
    p_tools_promote_global.add_argument("--runtime", default="")
    p_tools_promote_global.add_argument("--json", action="store_true")
    p_tools_promote_global.set_defaults(func=_cmd_tools_promote_global)
    p_tools_revoke = tools_sub.add_parser("revoke", help="revoke a tool")
    p_tools_revoke.add_argument("name")
    p_tools_revoke.add_argument(
        "--db",
        default=None,
        help="explicit tools DB (default: the runtime DB; see `skaldir registry status`)",
    )
    p_tools_revoke.add_argument(
        "--runtime", default="", help="runtime dir (default: $HERMES_HOME/godsv)"
    )
    p_tools_revoke.set_defaults(func=_cmd_tools_revoke)
    p_tools_build = tools_sub.add_parser(
        "build", help="autonomous builder: capability → gated tool → registered"
    )
    p_tools_build.add_argument("--capability", required=True, help="what capability is missing")
    p_tools_build.add_argument("--reason", default="", help="why the main agent needs it")
    p_tools_build.add_argument("--requested-by", default="cli")
    p_tools_build.add_argument(
        "--kind", choices=("auto", "mcp", "cli", "testing_tool"), default="auto"
    )
    p_tools_build.add_argument("--name", default="")
    p_tools_build.add_argument(
        "--project",
        default="",
        help="project path — records the stable project id (default: cwd)",
    )
    p_tools_build.add_argument(
        "--validation-case",
        action="append",
        help="testing tools: TARGET:pass|fail (broken target must FAIL, working must PASS)",
    )
    p_tools_build.add_argument("--source", default="", help="generated tool source file")
    p_tools_build.add_argument("--tests", default="", help="generated tool tests file")
    p_tools_build.add_argument("--module", default="")
    p_tools_build.add_argument("--entrypoint", default="")
    p_tools_build.add_argument("--sample", default="", help="JSON args for the determinism gate")
    p_tools_build.add_argument("--research", default="", help="research notes (text)")
    p_tools_build.add_argument("--research-file", default="", help="research notes (file)")
    p_tools_build.add_argument(
        "--secret", action="append", help="required secret HANDLE (repeatable)"
    )
    p_tools_build.add_argument("--domain", action="append", help="allowed domain (repeatable)")
    p_tools_build.add_argument(
        "--dependency", action="append", help="declared dependency (repeatable)"
    )
    p_tools_build.add_argument(
        "--db",
        default=None,
        help="explicit tools DB (default: the runtime DB; see `skaldir registry status`)",
    )
    p_tools_build.add_argument("--json", action="store_true")
    p_tools_build.add_argument(
        "--runtime", default="", help="runtime dir (default: $HERMES_HOME/godsv)"
    )
    p_tools_build.set_defaults(func=_cmd_tools_build)
    p_tools_builds = tools_sub.add_parser("builds", help="show the tool-build queue")
    p_tools_builds.add_argument("--limit", type=int, default=20)
    p_tools_builds.add_argument(
        "--runtime", default="", help="runtime dir (default: $HERMES_HOME/godsv)"
    )
    p_tools_builds.set_defaults(func=_cmd_tools_builds)
    p_tools_request = tools_sub.add_parser(
        "request", help="record a missing capability (built on the next session gap)"
    )
    p_tools_request.add_argument("--capability", required=True)
    p_tools_request.add_argument("--reason", default="")
    p_tools_request.add_argument("--requested-by", default="main-agent")
    p_tools_request.add_argument("--session", default="")
    p_tools_request.add_argument(
        "--kind", choices=("auto", "mcp", "cli", "testing_tool"), default="auto"
    )
    p_tools_request.add_argument("--name", default="")
    p_tools_request.add_argument("--source", default="")
    p_tools_request.add_argument("--tests", default="")
    p_tools_request.add_argument("--module", default="")
    p_tools_request.add_argument("--entrypoint", default="")
    p_tools_request.add_argument("--sample", default="")
    p_tools_request.add_argument("--research", default="")
    p_tools_request.add_argument("--secret", action="append", help="required secret HANDLE")
    p_tools_request.add_argument("--domain", action="append")
    p_tools_request.add_argument("--dependency", action="append")
    p_tools_request.add_argument(
        "--validation-case",
        action="append",
        help="testing tools: TARGET:pass|fail (broken target must FAIL, working must PASS)",
    )
    p_tools_request.add_argument(
        "--project",
        default="",
        help="project path — records the stable project id (default: cwd)",
    )
    p_tools_request.add_argument("--json", action="store_true")
    p_tools_request.add_argument(
        "--runtime", default="", help="runtime dir (default: $HERMES_HOME/godsv)"
    )
    p_tools_request.set_defaults(func=_cmd_tools_request)
    p_tools_fulfill = tools_sub.add_parser(
        "fulfill", help="run the capability pipeline now (no session needed)"
    )
    p_tools_fulfill.add_argument("request_id", nargs="?", default="")
    p_tools_fulfill.add_argument("--json", action="store_true")
    p_tools_fulfill.add_argument(
        "--runtime", default="", help="runtime dir (default: $HERMES_HOME/godsv)"
    )
    p_tools_fulfill.set_defaults(func=_cmd_tools_fulfill)
    p_tools_mine = tools_sub.add_parser(
        "mine", help="scan sessions for recurring capabilities (tool candidates)"
    )
    p_tools_mine.set_defaults(func=_cmd_tools_mine)
    p_tools_candidates = tools_sub.add_parser("candidates", help="list mined tool candidates")
    p_tools_candidates.set_defaults(func=_cmd_tools_candidates)
    p_tools_promote = tools_sub.add_parser(
        "promote", help="promote a candidate into a gated build request"
    )
    p_tools_promote.add_argument("candidate", help="candidate id (see `tools candidates`)")
    p_tools_promote.set_defaults(func=_cmd_tools_promote)

    p_tools_compose = tools_sub.add_parser("compose", help="find composition candidates for a need")
    p_tools_compose.add_argument("need")
    p_tools_compose.add_argument(
        "--db",
        default=None,
        help="explicit tools DB (default: the runtime DB; see `skaldir registry status`)",
    )
    p_tools_compose.add_argument("--tag", action="append", default=None)
    p_tools_compose.add_argument(
        "--runtime", default="", help="runtime dir (default: $HERMES_HOME/godsv)"
    )
    p_tools_compose.set_defaults(func=_cmd_tools_compose)

    # EXPAND container half (self-expanding tool system): the group owns its
    # verbs and their `cli_contract.COMMANDS` entries — see the hand-over note in
    # src/skaldir/toolbuilder_cli.py. Called AFTER every legacy `tools` subparser
    # exists: it upgrades the existing `mine`/`build` parsers in place, so calling
    # it earlier would make it create its own `mine` and collide with the one above.
    from .toolbuilder_cli import register_cli as _register_container_tools

    _register_container_tools(tools_sub)

    # EXPAND HTTP half: `tools api inspect/discover/register/call` — the API chain
    # lives under its own `api` group so the two halves stay distinguishable in the
    # contract dump (see the hand-over note in src/skaldir/adapter_http_cli.py).
    from .adapter_http_cli import register_cli as _register_api_adapter

    _register_api_adapter(tools_sub)

    # `tools call NAME --json-args '{…}'` — the manual call verb for the same
    # executable surface the agent loop wires (see the 2-line integration note
    # in src/skaldir/tools_call_cli.py).
    from .tools_call_cli import register_cli as _register_tools_call

    _register_tools_call(tools_sub)

    # `tools build --image-digest` is the documented spelling of the container
    # half's `--digest` (the flag `_upgrade_build` adds); both write the same
    # value, so a caller that reads the contract never has to guess the name.
    _build_cmd = getattr(tools_sub, "choices", {}).get("build")
    if _build_cmd is not None:
        _existing = {
            option
            for action in _build_cmd._actions  # noqa: SLF001 — argparse has no public API
            for option in action.option_strings
        }
        if "--image-digest" not in _existing:
            _build_cmd.add_argument(
                "--image-digest", dest="digest", default="",
                help="alias of --digest: pin the image by its sha256 digest",
            )

    p_registry = sub.add_parser(
        "registry",
        help="one registry truth: runtime dir, tools DB, project context (§1.2)",
    )
    registry_sub = p_registry.add_subparsers(dest="subcommand", required=True)
    p_registry_status = registry_sub.add_parser(
        "status", help="show the resolved runtime/db/project context (read-only)"
    )
    p_registry_status.add_argument("--runtime", default="", help="runtime dir override")
    p_registry_status.add_argument(
        "--db", default=None, help="explicit tools DB override"
    )
    p_registry_status.add_argument(
        "--project",
        default="",
        help="project path for the stable project id (default: cwd)",
    )
    p_registry_status.add_argument("--json", action="store_true")
    p_registry_status.set_defaults(func=_cmd_registry_status)
    p_registry_migrate = registry_sub.add_parser(
        "migrate",
        help="merge legacy tool DBs (.godsv/tools.db) into the runtime DB — backups first, "
        "dry-run by default, conflicts reported, source kept",
    )
    registry_scope = p_registry_migrate.add_mutually_exclusive_group()
    registry_scope.add_argument(
        "--dry-run", dest="dry_run", action="store_true", help="report only (default)"
    )
    registry_scope.add_argument(
        "--apply", action="store_true", help="back up source and target, then merge"
    )
    p_registry_migrate.add_argument("--runtime", default="", help="runtime dir override")
    p_registry_migrate.add_argument("--db", default=None, help="explicit tools DB override")
    p_registry_migrate.add_argument("--project", default="", help="project path (default: cwd)")
    p_registry_migrate.add_argument("--json", action="store_true")
    p_registry_migrate.set_defaults(func=_cmd_registry_migrate)

    p_workflows = sub.add_parser("workflows", help="autonomous workflow contracts (§9)")
    workflows_sub = p_workflows.add_subparsers(dest="subcommand", required=True)
    p_workflows_list = workflows_sub.add_parser("list", help="print the workflow contracts")
    p_workflows_list.set_defaults(func=_cmd_workflows_list)
    p_workflows_show = workflows_sub.add_parser("show", help="print one workflow contract")
    p_workflows_show.add_argument("workflow_id")
    p_workflows_show.set_defaults(func=_cmd_workflows_show)
    p_workflows_run = workflows_sub.add_parser(
        "run", help="start a declared workflow and report its own verdict"
    )
    p_workflows_run.add_argument("workflow_id", help="see `skaldir workflows list`")
    p_workflows_run.add_argument(
        "--param",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="workflow input (repeatable; missing inputs are reported as blocked)",
    )
    p_workflows_run.add_argument(
        "--project", default="", help="project path for the stable project id (default: cwd)"
    )
    p_workflows_run.set_defaults(func=_cmd_workflows_run)

    p_mcp = sub.add_parser("mcp", help="MCP export of approved tools (fastmcp)")
    mcp_sub = p_mcp.add_subparsers(dest="subcommand", required=True)
    p_mcp_serve = mcp_sub.add_parser("serve", help="run the stdio MCP server")
    p_mcp_serve.add_argument(
        "--db",
        default=None,
        help="explicit tools DB (default: the runtime DB; see `skaldir registry status`)",
    )
    p_mcp_serve.add_argument("--name", default="godsv-tools")
    p_mcp_serve.add_argument(
        "--project",
        default="",
        help="project path — expose global tools plus this project's tools only (§7)",
    )
    p_mcp_serve.add_argument(
        "--all",
        action="store_true",
        help="explicit all view: also expose legacy (scope-less) tools",
    )
    p_mcp_serve.add_argument(
        "--runtime",
        default="",
        help="runtime dir (default: resolved runtime; explicit --db: its directory)",
    )
    p_mcp_serve.set_defaults(func=_cmd_mcp_serve)

    # EXPAND MCP half: handshake / map / register verbs on the EXISTING `mcp` group
    # (the same pattern as `toolbuilder_cli.register_cli(tools_sub)`) — see the
    # hand-over note in src/skaldir/adapter_mcp_cli.py.
    from .adapter_mcp_cli import register_cli as _register_mcp_adapter

    _register_mcp_adapter(mcp_sub)

    p_cu = sub.add_parser("cu", help="computer-use utilities (isolated runners)")
    cu_sub = p_cu.add_subparsers(dest="subcommand", required=True)
    p_cu_check = cu_sub.add_parser("check", help="probe available runner isolations")
    p_cu_check.set_defaults(func=_cmd_cu_check)
    p_cu_demo = cu_sub.add_parser("demo", help="deterministic channel-ordering demo")
    p_cu_demo.set_defaults(func=_cmd_cu_demo)

    p_completion = sub.add_parser(
        "completion", help="strict completion supervisor (goal state and loop)"
    )
    completion_sub = p_completion.add_subparsers(dest="subcommand", required=True)
    p_completion_status = completion_sub.add_parser(
        "status", help="show the goal state and metrics"
    )
    p_completion_status.set_defaults(func=_cmd_completion_status)
    p_completion_history = completion_sub.add_parser("history", help="show the completion timeline")
    p_completion_history.add_argument("--limit", type=int, default=40)
    p_completion_history.set_defaults(func=_cmd_completion_history)
    p_completion_begin = completion_sub.add_parser(
        "begin", help="begin a new goal (re-arms the supervisor)"
    )
    p_completion_begin.add_argument("--goal", required=True)
    p_completion_begin.add_argument("--criterion", action="append", default=[])
    p_completion_begin.set_defaults(func=_cmd_completion_begin)
    p_completion_override = completion_sub.add_parser(
        "override", help="user override: accept blockers explicitly (not VERIFIED)"
    )
    p_completion_override.add_argument("--reason", required=True)
    p_completion_override.set_defaults(func=_cmd_completion_override)

    p_vault = sub.add_parser(
        "vault", help="credential vault (handles only; secrets encrypted at rest)"
    )
    vault_sub = p_vault.add_subparsers(dest="subcommand", required=True)
    p_vault_list = vault_sub.add_parser("list", help="list handles (metadata only)")
    p_vault_list.add_argument("--vault", default=_runtime_file("vault.json"))
    p_vault_list.set_defaults(func=_cmd_vault_list)
    p_vault_describe = vault_sub.add_parser("describe", help="describe one handle")
    p_vault_describe.add_argument("handle")
    p_vault_describe.add_argument("--vault", default=_runtime_file("vault.json"))
    p_vault_describe.set_defaults(func=_cmd_vault_describe)
    p_vault_add = vault_sub.add_parser("add", help="add a secret (prompted via getpass)")
    p_vault_add.add_argument("--service", required=True)
    p_vault_add.add_argument("--name", required=True)
    p_vault_add.add_argument("--scopes", default="use")
    p_vault_add.add_argument("--description", default="")
    p_vault_add.add_argument("--vault", default=_runtime_file("vault.json"))
    p_vault_add.set_defaults(func=_cmd_vault_add)
    p_vault_revoke = vault_sub.add_parser("revoke", help="revoke a handle")
    p_vault_revoke.add_argument("handle")
    p_vault_revoke.add_argument("--vault", default=_runtime_file("vault.json"))
    p_vault_revoke.set_defaults(func=_cmd_vault_revoke)

    p_spend = sub.add_parser("spend", help="staged spending (human-approved by default)")
    spend_sub = p_spend.add_subparsers(dest="subcommand", required=True)
    p_spend_request = spend_sub.add_parser("request", help="request a spend")
    p_spend_request.add_argument("amount", type=float)
    p_spend_request.add_argument("--merchant", required=True)
    p_spend_request.add_argument("--purpose", default="")
    p_spend_request.add_argument("--currency", default="EUR")
    p_spend_request.add_argument("--tier", default="stufe0", choices=sorted(PRESETS))
    p_spend_request.add_argument("--db", default=_runtime_file("treasury.db"))
    p_spend_request.set_defaults(func=_cmd_spend_request)
    p_spend_list = spend_sub.add_parser("list", help="list open spend requests")
    p_spend_list.add_argument("--db", default=_runtime_file("treasury.db"))
    p_spend_list.set_defaults(func=_cmd_spend_list)
    p_spend_approve = spend_sub.add_parser("approve", help="approve a pending request (owner)")
    p_spend_approve.add_argument("request")
    p_spend_approve.add_argument("--db", default=_runtime_file("treasury.db"))
    p_spend_approve.set_defaults(func=_cmd_spend_approve)
    p_spend_deny = spend_sub.add_parser("deny", help="deny a pending request")
    p_spend_deny.add_argument("request")
    p_spend_deny.add_argument("--reason", default="")
    p_spend_deny.add_argument("--db", default=_runtime_file("treasury.db"))
    p_spend_deny.set_defaults(func=_cmd_spend_deny)
    p_spend_complete = spend_sub.add_parser("complete", help="mark an approved spend as executed")
    p_spend_complete.add_argument("request")
    p_spend_complete.add_argument("--db", default=_runtime_file("treasury.db"))
    p_spend_complete.set_defaults(func=_cmd_spend_complete)
    p_spend_allowance = spend_sub.add_parser(
        "allowance", help="show the test-money allowance the agent may see"
    )
    p_spend_allowance.add_argument("--tier", default="stufe2", choices=sorted(PRESETS))
    p_spend_allowance.add_argument("--db", default=_runtime_file("treasury.db"))
    p_spend_allowance.set_defaults(func=_cmd_spend_allowance)

    p_desktop = sub.add_parser(
        "desktop", help="desktop app: install launcher+icons, status, start (Fedora)"
    )
    desktop_sub = p_desktop.add_subparsers(dest="subcommand", required=True)
    p_desktop_install = desktop_sub.add_parser(
        "install", help="user-local .desktop entry + hicolor icons + CLI symlink"
    )
    p_desktop_install.add_argument(
        "--no-link", action="store_true", help="skip the ~/.local/bin symlink"
    )
    p_desktop_install.set_defaults(func=_cmd_desktop_install)
    p_desktop_uninstall = desktop_sub.add_parser("uninstall", help="remove launcher + icons")
    p_desktop_uninstall.add_argument(
        "--remove-plugin",
        action="store_true",
        help="full teardown: also remove the $HERMES_HOME/plugins/godsv symlink",
    )
    p_desktop_uninstall.set_defaults(func=_cmd_desktop_uninstall)
    p_desktop_status = desktop_sub.add_parser(
        "status", help="deployment check (hermes, plugin, entry, icons)"
    )
    p_desktop_status.set_defaults(func=_cmd_desktop_status)
    p_desktop_start = desktop_sub.add_parser(
        "start", help="verify deployment, then start the desktop app"
    )
    p_desktop_start.add_argument(
        "--check-only", action="store_true", help="only verify, do not start"
    )
    p_desktop_start.set_defaults(func=_cmd_desktop_start)
    p_desktop_build = desktop_sub.add_parser(
        "build-app",
        help="build the Skaldir Agent app natively (bundled panel/theme, no plugin install)",
    )
    p_desktop_build.add_argument(
        "--force", action="store_true", help="refresh the source mirror from scratch"
    )
    p_desktop_build.add_argument(
        "--perf-probe",
        action="store_true",
        help="measurement build: embed the dev-only perf driver (VITE_PERF_PROBE=1)",
    )
    p_desktop_build.set_defaults(func=_cmd_desktop_build_app)

    p_doctor = sub.add_parser("doctor", help="release health check (deployment, prompts, tools)")
    p_doctor.set_defaults(func=_cmd_doctor)

    p_selftest = sub.add_parser(
        "selftest",
        help="probe all eight layers and report which one is reachable (read-only)",
    )
    p_selftest.add_argument(
        "--layer",
        action="append",
        default=[],
        metavar="LAYER",
        help=f"probe only these layers (repeatable; one of {', '.join(cli_contract.LAYERS)})",
    )
    p_selftest.add_argument("--runtime", default="", help="runtime dir override")
    p_selftest.set_defaults(func=_cmd_selftest)

    p_contract = sub.add_parser(
        "contract", help="self-description: commands, args and their JSON Schemas"
    )
    contract_sub = p_contract.add_subparsers(dest="subcommand", required=True)
    p_contract_list = contract_sub.add_parser(
        "list", help="every command with its layer, flags and JSON availability"
    )
    p_contract_list.add_argument("--layer", default="", help="filter by layer")
    p_contract_list.set_defaults(func=_cmd_contract_list)
    p_contract_show = contract_sub.add_parser(
        "show", help="one command's full contract record"
    )
    p_contract_show.add_argument("command", help="dotted command path, e.g. tools.show")
    p_contract_show.set_defaults(func=_cmd_contract_show)
    p_contract_dump = contract_sub.add_parser(
        "dump", help="the whole inventory with a JSON Schema per command"
    )
    p_contract_dump.add_argument("--out", default="", help="also write the inventory to FILE")
    p_contract_dump.set_defaults(func=_cmd_contract_dump)

    cli_contract.annotate(parser)
    return parser


def _cmd_desktop_build_app(args) -> int:
    from . import appbuild

    print("Building the Skaldir Agent app (native) — source mirror → overlay → npm run pack …")
    perf_probe = True if getattr(args, "perf_probe", False) else None
    result = appbuild.build(force=getattr(args, "force", False), perf_probe=perf_probe)
    if result.get("ok"):
        print(f"✓ Skaldir Agent app: {result['binary']}")
        if result.get("perf_probe"):
            print(
                "  ⚠ PERF-PROBE-Build (VITE_PERF_PROBE=1) — Messartefakt, kein Release. "
                "Ersetzt ~/.hermes/godsv/app/linux-unpacked; danach normal neu bauen."
            )
        print(f"  Mirror: {result['mirror']}  ({result['seconds']}s)")
        print("Start: skaldir desktop start")
        return 0
    print(f"✗ Build failed: stage '{result.get('stage')}' ({result.get('seconds')}s)")
    tail = result.get("output_tail")
    if tail:
        print(tail)
    return 1


def _cmd_desktop_install(args) -> int:
    from . import desktop

    result = desktop.install(link_cli=not args.no_link)
    print("Desktop entry:", result["desktop_file"])
    for icon in result["icons"]:
        print("Icon:", icon)
    if result["cli_link"]:
        print("CLI link:", result["cli_link"])
    skin = result.get("skin") or {}
    if skin.get("installed"):
        state = (
            "active"
            if skin.get("activated")
            else "ready (opt-in: hermes config set display.skin gods-voice)"
        )
        print("Skin:", skin["skin_file"], f"({state})")
    if result.get("panel_link"):
        print("Panel:", result["panel_link"])
    print("Start: skaldir desktop start  ·  or via the app menu (Skaldir)")
    return 0


def _cmd_desktop_uninstall(args) -> int:
    from . import desktop

    removal = desktop.uninstall(remove_plugin=getattr(args, "remove_plugin", False))
    if removal["removed"]:
        for path in removal["removed"]:
            print("removed:", path)
    else:
        print("nothing to remove")
    return 0


def _cmd_desktop_status(args) -> int:
    from . import desktop

    status = desktop.deployment_status()
    ok = bool(status["hermes_ok"] and status["plugin_ok"])
    cli_contract.set_data(
        {
            "hermes_ok": bool(status["hermes_ok"]),
            "hermes": status["hermes"],
            "plugin_ok": bool(status["plugin_ok"]),
            "plugin_dir": str(status["plugin_dir"]),
            "desktop_installed": bool(status["desktop_installed"]),
            "ok": ok,
        }
    )
    cli_contract.add_evidence(str(status["plugin_dir"]), "file", "deployed plugin dir")
    print(desktop.format_status(status))
    if ok:
        return EXIT_OK
    return cli_contract.not_found(
        "the desktop deployment is incomplete (hermes or the plugin is missing)",
        hint="run `skaldir desktop install` (and make sure `hermes` is in PATH)",
    )


def _cmd_desktop_start(args) -> int:
    from . import desktop

    was_running = desktop.app_running()
    code = desktop.launch(check_only=args.check_only)
    cli_contract.set_data(
        {
            "exit_code": int(code),
            "check_only": bool(args.check_only),
            "already_running": bool(was_running),
        }
    )
    cli_contract.add_evidence(
        "src/skaldir/desktop.py", "code", "desktop.launch() result the verdict came from"
    )
    if code == 0:
        if args.check_only:
            print("Deployment OK.")
        elif was_running:
            print("Skaldir Agent is running — focused (the open session stays open).")
        else:
            print("Skaldir Agent is starting (its own instance) — last session, else a new chat.")
        return EXIT_OK
    if code == 2:
        print("hermes not in PATH — desktop app cannot start (check the deployment).")
        return cli_contract.blocked(
            "hermes is not in PATH — the desktop app cannot start",
            hint="install/activate the Hermes CLI, then run `skaldir desktop status --json`",
            next_command="skaldir desktop status --json",
            reason="shows which part of the deployment is missing",
        )
    print("Start failed.")
    mapped = int(code) if int(code) in cli_contract.EXIT_NAMES else EXIT_INTERNAL
    return cli_contract.fail(
        mapped,
        f"desktop start failed (launch returned {code})",
        details={"launch_exit_code": int(code)},
    )


def _cmd_doctor(args) -> int:
    """Release health check: deployment, prompts, runtime, optional tools."""
    import platform
    import shutil
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as pkg_version

    from . import desktop
    from .prompts import REQUIRED_IDS, PromptRegistry

    ok = True
    checks: list[dict] = []

    def report(label: str, good: bool, detail: str = "") -> None:
        nonlocal ok
        ok = ok and bool(good)
        checks.append({"check": label, "ok": bool(good), "detail": detail})
        suffix = f" — {detail}" if detail else ""
        print(f"{'✓' if good else '✗'} {label}{suffix}")

    report("Python ≥ 3.11", sys.version_info >= (3, 11), platform.python_version())
    from . import DIST_NAME, LEGACY_DIST_NAME

    # The resolution order is the product's own (src/skaldir/__init__.py): the
    # post-rename distribution first, then its underscore form, and only then the
    # pre-rename `godsv` metadata — and when only THAT is present the line says so
    # instead of presenting a legacy install as the current package.
    pkg = "not installed"
    for _dist in (DIST_NAME, DIST_NAME.replace("-", "_")):
        try:
            pkg = pkg_version(_dist)
            break
        except PackageNotFoundError:
            continue
    if pkg == "not installed":
        try:
            pkg = f"{pkg_version(LEGACY_DIST_NAME)} (pre-rename '{LEGACY_DIST_NAME}' metadata)"
        except PackageNotFoundError:
            pkg = "not installed"
    report(f"{DIST_NAME} package", pkg != "not installed", pkg)

    registry = PromptRegistry()
    missing = [pid for pid in REQUIRED_IDS if pid not in registry.ids()]
    detail = f"missing: {', '.join(missing)}" if missing else f"{len(registry.ids())} loaded"
    report(f"Prompts ({len(REQUIRED_IDS)} required)", not missing, detail)

    status = desktop.deployment_status()
    report("Hermes CLI in PATH", status["hermes_ok"], status["hermes"] or "hermes missing")
    report("Plugin deployed", status["plugin_ok"], status["plugin_dir"])
    report(
        "Desktop entry",
        status["desktop_installed"],
        "" if status["desktop_installed"] else "skaldir desktop install",
    )
    from .registry import default_runtime_dir

    runtime = default_runtime_dir()
    report("Runtime", (runtime / "events.db").exists(), str(runtime))
    for tool, purpose in (("bwrap", "Sandbox"), ("podman", "Container"), ("convert", "Icons")):
        found = shutil.which(tool)
        checks.append(
            {"check": f"optional tool {tool} ({purpose})", "ok": True,
             "detail": found or "not installed (optional)"}
        )
        print(f"{'✓' if found else '·'} {tool} — {found or f'optional ({purpose})'}")
    cli_contract.set_data({"checks": checks, "ok": bool(ok), "runtime": str(runtime)})
    cli_contract.add_evidence(
        "src/skaldir/cli.py:2043", "code", "doctor's checks are the evidence they report"
    )
    print()
    if ok:
        print("Deployment OK.")
        return EXIT_OK
    print("Problems found — see ✗ above.")
    failed = [item["check"] for item in checks if not item["ok"]]
    return cli_contract.verification_failed(
        "health check failed: " + ", ".join(failed),
        hint="every ✗ line above names the missing piece; `skaldir desktop install` fixes most",
        details={"failed_checks": failed},
    )


def _cu_runner_facts() -> dict:
    """Runner isolation facts in English (the human view of `cu check` is unchanged)."""
    import shutil

    from .execution import sandbox_available

    bwrap = shutil.which("bwrap")
    podman = shutil.which("podman")
    return {
        "dry_run": {"available": True, "detail": "in-process deterministic runner"},
        "sandbox_bwrap": {
            "available": bool(bwrap) and sandbox_available(),
            "detail": bwrap or "bwrap is not installed",
        },
        "container_podman": {
            "available": bool(podman),
            "detail": podman or "podman is not installed",
        },
    }


def _cmd_selftest(args: argparse.Namespace) -> int:
    """Probe the eight layers — one honest verdict per layer, never a claim."""
    from . import render
    from .theme import for_args as _theme_for_args

    runtime = Path(args.runtime) if getattr(args, "runtime", "") else _gv_runtime()
    layers = [str(item) for item in (getattr(args, "layer", None) or [])]
    report = cli_contract.run_selftest(runtime, layers or None)
    cli_contract.set_data(report)
    cli_contract.add_evidence(str(runtime), "file", "runtime dir the probes read")
    theme = _theme_for_args(args)
    limit = render.terminal_width()
    sep = theme.glyph("sep")
    print(render.bar("layers", columns=limit, theme=theme))
    for record in report["layers"]:
        status = "ok" if record["ok"] else "fail"
        head = str(record["layer"])
        if not record["ok"]:
            head = f"{head} {sep} {record['blocked_reason']}"
        print(render.status_line(status, head, theme=theme, muted=record["ok"],
                                 columns=limit))
        for probe in record["probes"]:
            print(render.status_line(
                "ok" if probe["ok"] else "fail",
                f"{probe['name']}: {probe['detail']}",
                theme=theme, indent=render.DETAIL_INDENT, muted=True, columns=limit))
    print(render.end_rule(columns=limit, theme=theme))
    print(f"  {len(report['layers'])} layer(s) {sep} {report['probes']} probe(s) {sep} "
          f"{report['failed']} not reachable {sep} {report['duration_ms']}ms")
    if report["ok"]:
        return EXIT_OK
    for layer in report["failed_layers"]:
        cli_contract.next_action(
            f"skaldir selftest --layer {layer} --json",
            f"{layer} is not reachable — see its blocked_reason",
        )
    return cli_contract.verification_failed(
        "layer(s) not reachable: " + ", ".join(report["failed_layers"]),
        hint="each layer's blocked_reason names the missing wiring",
        details={"runtime": str(runtime), "failed_layers": report["failed_layers"]},
    )


def _cmd_contract_list(args: argparse.Namespace) -> int:
    """Every command with its layer, flags and JSON availability."""
    from . import render
    from .theme import for_args as _theme_for_args

    inventory = cli_contract.dump(build_parser())
    wanted = str(getattr(args, "layer", "") or "").strip().upper()
    commands = [
        {
            "command": item["command"],
            "layer": item["layer"],
            "mutates": item["mutates"],
            "has_json": item["has_json_flag"],
            "requires_credentials": item["requires_credentials"],
            "data_available": item["data_available"],
            "registered": item["registered"],
        }
        for item in inventory["commands"]
        if not wanted or item["layer"].upper() == wanted
    ]
    cli_contract.set_data(
        {"commands": commands, "count": len(commands), "layer": wanted or None}
    )
    if not commands:
        print(f"no command in layer {wanted!r} — see `skaldir contract list`")
        return EXIT_OK
    theme = _theme_for_args(args)
    commands.sort(key=lambda item: (str(item["layer"]).lower(), str(item["command"])))
    unregistered = [item["command"] for item in commands if not item["registered"]]
    limit = render.terminal_width()
    print(render.bar("commands", columns=limit, theme=theme))
    print(render.table(
        ["layer", "command", "format", "mode"],
        [[item["layer"], item["command"],
          "json" if item["has_json"] else "text",
          "write" if item["mutates"] else "read"] for item in commands],
        theme=theme, max_width=limit))
    print(render.end_rule(columns=limit, theme=theme))
    sep = theme.glyph("sep")
    print(f"  {len(commands)} command(s)"
          + (f" {sep} layer {wanted}" if wanted else "")
          + (f" {sep} not registered: {', '.join(unregistered)}" if unregistered else ""))
    return EXIT_OK


def _cmd_contract_show(args: argparse.Namespace) -> int:
    """One command's full contract record (args + data schema + exit codes)."""
    inventory = cli_contract.dump(build_parser())
    record = next(
        (item for item in inventory["commands"] if item["command"] == str(args.command)), None
    )
    if record is None:
        print(f"unknown command: {args.command}")
        return cli_contract.not_found(
            f"unknown command: {args.command}",
            hint="`skaldir contract list` prints every registered command",
        )
    cli_contract.set_data(record)
    print(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True))
    return EXIT_OK


def _cmd_contract_dump(args: argparse.Namespace) -> int:
    """The whole inventory with a JSON Schema per command — the GUI's cheap path."""
    inventory = cli_contract.dump(build_parser())
    cli_contract.set_data(inventory)
    out = str(getattr(args, "out", "") or "")
    if out:
        target = Path(out).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(inventory, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
        )
        cli_contract.add_evidence(str(target), "file", "written inventory")
        print(f"written: {target}")
    if inventory["unregistered"]:
        cli_contract.warn(
            "commands without a contract entry: " + ", ".join(inventory["unregistered"])
        )
    print(json.dumps(inventory, ensure_ascii=False, indent=1))
    return EXIT_OK


def _cmd_workflows_show(args: argparse.Namespace) -> int:
    """One workflow contract (inputs, execution, success rule)."""
    from .workflows import workflow_contract

    contract = workflow_contract(str(args.workflow_id))
    if contract is None:
        print(f"unknown workflow: {args.workflow_id}")
        return cli_contract.not_found(
            f"unknown workflow: {args.workflow_id}",
            hint="`skaldir workflows list` prints the declared contracts",
        )
    cli_contract.set_data(contract)
    print(json.dumps(contract, ensure_ascii=False, indent=2, sort_keys=True))
    return EXIT_OK


def _cmd_workflows_run(args: argparse.Namespace) -> int:
    """Start a declared workflow; the workflow's own verdict decides the exit code."""
    from .workflows import run_workflow, workflow_contract, workflow_contracts

    workflow_id = str(args.workflow_id)
    params: dict[str, str] = {}
    for raw in getattr(args, "param", None) or []:
        key, sep, value = str(raw).partition("=")
        if not sep or not key.strip():
            return cli_contract.usage_error(
                f"--param expects KEY=VALUE, got: {raw}",
                hint="example: --param app=skaldir",
            )
        params[key.strip()] = value
    if workflow_contract(workflow_id) is None:
        known = ", ".join(item["name"] for item in workflow_contracts())
        print(f"unknown workflow: {workflow_id}")
        return cli_contract.not_found(
            f"unknown workflow: {workflow_id}", hint=f"known: {known}"
        )
    outcome = run_workflow(workflow_id, runtime=_gv_runtime(), params=params)
    cli_contract.set_data(outcome)
    evidence_path = str(outcome.get("evidence") or "")
    if evidence_path:
        cli_contract.add_evidence(evidence_path, "file", "workflow run record")
    status = str(outcome.get("status") or "unknown")
    print(f"[{status}] {workflow_id}")
    if outcome.get("reason"):
        print(f"  reason: {outcome['reason']}")
    if outcome.get("success_rule"):
        print(f"  success rule: {outcome['success_rule']}")
    if evidence_path:
        print(f"  record: {evidence_path}")
    if outcome.get("verified"):
        return EXIT_OK
    if status == "blocked":
        return cli_contract.blocked(
            str(outcome.get("reason") or "the workflow reported itself blocked"),
            next_command=f"skaldir workflows show {workflow_id} --json",
            reason="the contract names the required inputs",
        )
    return cli_contract.verification_failed(
        str(outcome.get("reason") or f"workflow {workflow_id} did not verify"),
        details={"status": status, "workflow": workflow_id},
    )


def _logo_text() -> str:
    """The Skaldir banner art (``assets/cli-logo.txt`` via :mod:`skaldir.logo`).

    The legacy ``assets/logo.ansi`` (the pre-rebrand gold/red art) is deliberately not
    read any more: the CLI has one piece of art, and it is Skaldir's.
    """
    from . import logo

    try:
        return logo.banner()
    except Exception:  # noqa: BLE001 — a missing logo must never break the CLI
        return ""


def _print_banner() -> None:
    """Print the Skaldir banner — the one place the CLI shows its own art."""
    text = _logo_text()
    if text:
        print(text)


def _print_welcome(no_color: bool = False) -> None:
    """The startup screen for a bare call: the Skaldir banner, then the way in.

    Monochrome by decision and compact by taste: the art, the signature and one or two
    hint sections separated by bars (see ``docs/CLI-STYLE.md``). Colour is the terminal's
    choice — with ``--no-color``, ``NO_COLOR`` or a pipe this prints the same body as
    plain ASCII.
    """
    from . import logo
    from .registry import default_runtime_dir
    from .theme import activate as _activate_theme

    theme = _activate_theme(no_color=no_color)
    sections: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
        (
            "Get started",
            (
                ("skaldir shell", "interactive shell (slash palette, Ctrl-C cancels)"),
                ("skaldir doctor", "deployment + health check"),
                ("skaldir selftest --json", "which of the eight layers is reachable"),
                ("skaldir desktop start", f"desktop app {theme.glyph('arrow')} last session"),
            ),
        ),
        (
            "Explore",
            (
                ("skaldir contract dump", "every command + its JSON schema"),
                ("skaldir metrics", "intervention metrics"),
                ("skaldir skills list", "skill candidates"),
                ("skaldir spend allowance", "test-money budget"),
            ),
        ),
    )
    print(logo.welcome(
        theme=theme,
        version=__version__,
        runtime=str(default_runtime_dir()),
        directory=str(Path.cwd()),
        sections=sections,
        footer=(
            f"skaldir --help shows all commands {theme.glyph('sep')} "
            "NO_COLOR=1 for plain output"
        ),
    ))


def _cmd_tools_hub(args: argparse.Namespace) -> int:
    import json as _json

    from .registry import default_runtime_dir
    from .toolhub import build_hub_manifest, discover, search

    runtime = Path(args.runtime) if args.runtime else default_runtime_dir()
    if args.manifest:
        try:
            hub = build_hub_manifest(runtime, args.manifest)
        except ValueError as exc:
            print(f"refused: {exc}")
            return 1
        print(_json.dumps(hub, indent=1, ensure_ascii=False))
        return 0
    entries = discover(runtime)
    if args.search:
        entries = search(entries, args.search)
    elif args.show:
        entries = [e for e in entries if e["name"] == args.show]
    if args.json:
        print(_json.dumps(entries, indent=1, ensure_ascii=False))
        return 0
    for entry in entries[:20]:
        print(
            f"{entry['name']:<30} {entry['source']:<10} {entry['type']:<12} "
            f"{entry['verification']['state']:<12} {entry['purpose'][:56]}"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    """The one entry point: parse, then run every command through the envelope."""
    argv = list(sys.argv[1:] if argv is None else argv)
    # The two curated-help escape hatches (MVP/68 §3) are answered before argparse: they are
    # about the *view*, not a command, and neither may be answered by a subparser or become
    # a new leaf in `contract dump` (the leaf count must not move for a help screen).
    if argv and str(argv[0]) in ("?", "--all-commands"):
        from .complete import build_tree, help_overlay
        from .slash import full_help

        parser = build_parser()
        tree = build_tree(parser)
        rest = [part for part in argv[1:] if part != "--all-commands"]
        if rest:
            print(help_overlay(tree, rest))
        else:
            print(full_help(tree, per_group=str(argv[0]) == "?"))
        return EXIT_OK
    # `skaldir setup` writes the two keys to .env (0600, beside config.yaml) and validates
    # them from there, while every command reads the *process* environment. Loading the file
    # here is what makes "run setup, then the very next command" work; an already-exported
    # variable always wins (env.load_env_file_into never overrides one, and never raises).
    from .env import load_env_file_into

    load_env_file_into()
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else EXIT_USAGE
        if code != EXIT_OK:
            # §2: a refusal is answered with the code the CONTRACT declares for
            # the command being parsed (usage 1; 2 where the command declares
            # verification_failed and no usage code) — one code, envelope and
            # process exit agreeing (audit 62 #17).
            code = cli_contract.usage_exit_code(argv)
            if _json_requested_in_argv(argv):
                # §4.1: with --json, stdout carries exactly one JSON document — failures too.
                cli_contract.emit_json_line(cli_contract.usage_envelope(argv, code))
        raise SystemExit(code) from None
    if getattr(args, "all_commands", False):
        # `skaldir --all-commands` in any position the parser accepts
        from .complete import build_tree
        from .slash import full_help

        print(full_help(build_tree(parser), per_group=True))
        return EXIT_OK
    func = getattr(args, "func", None)
    if func is None:
        if cli_contract.json_requested(args):
            return _emit_root_envelope(parser)
        # A human at a terminal gets the shell (the first-run screen inside it); a pipe, a
        # script or a redirect keeps the welcome text and gets its exit code immediately.
        from .repl import launch_from_bare_call

        return launch_from_bare_call(no_color=bool(getattr(args, "no_color", False)))
    return cli_contract.run_command(args, func)


def _json_requested_in_argv(argv: list[str]) -> bool:
    """``--json`` in any accepted spelling (used when argparse fails early)."""
    for item in argv:
        text = str(item)
        if text == "--json" or text == "--format=json" or text == "--format=jsonl":
            return True
    if "--format" in [str(item) for item in argv] and "json" in [str(item) for item in argv]:
        return True
    return str(getenv("FORMAT", "") or "").strip().lower() in ("json", "jsonl")


def _emit_root_envelope(parser: argparse.ArgumentParser) -> int:
    """``skaldir --json``: what this CLI is and how to start (the GUI's first call)."""
    inventory = cli_contract.dump(parser)
    from .registry import default_runtime_dir

    envelope = cli_contract.build_envelope(
        command="skaldir",
        exit_code=EXIT_OK,
        data={
            "godsvoice_version": __version__,
            "runtime_dir": str(default_runtime_dir()),
            "leaf_commands": inventory["leaf_commands"],
            "commands": [item["command"] for item in inventory["commands"]],
        },
        evidence=[
            {
                "ref": "src/skaldir/cli.py:1411",
                "kind": "code",
                "note": "build_parser() — the command tree this answer was read from",
            }
        ],
        next_actions=[
            {
                "command": "skaldir selftest --json",
                "reason": "which of the eight layers is reachable right now",
            },
            {
                "command": "skaldir contract dump --json",
                "reason": "the full inventory with a JSON Schema per command",
            },
        ],
    )
    cli_contract.emit_json_line(envelope)
    return EXIT_OK
