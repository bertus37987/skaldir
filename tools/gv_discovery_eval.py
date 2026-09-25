#!/usr/bin/env python
"""§4c discovery eval runner — runs the FROZEN acceptance set (§8, Wave 4).

Loads ``tests/fixtures/discovery_cases.json`` (frozen 2026-09-14; never edited,
never used to tune wording or ranking) and executes every case against the real
discovery contract (:mod:`godsv.discovery`), reporting per metric numerator over
denominator — never a lone percentage.

Two modes, kept strictly apart in the report:

- ``--harness`` (default): a temporary runtime whose inventory stands in for the
  parts of the target surface that do not exist yet (SearXNG/Pandoc/research
  tools — §6, Docker stage B6/B10). The inventory is written from the
  *reference-tool contracts* (names, formats, services, both languages); it
  plants approved manifests + registry specs exactly as the gated builder would.
- ``--runtime <dir>``: the real runtime (``~/.hermes/godsv``), read-only. Cases
  whose target capability is not registered there are reported **blocked**, with
  the reason — the runner never substitutes a dry run for a missing surface.

What is scored (per the frozen rules): Recall@3 over ``allowed`` patterns
(case-insensitive glob on capability id/name), false fits on ``no_match`` cases,
scope leaks (``excluded`` hits), search latency, context tokens of the candidate
set. Critical cases (``s*``, ``q*``) must pass.

Blocked (needs a later stage, reported separately, never counted as a pass):
container START / exactly-once start / parallel starts (Docker stage B6),
dispatch-time revocation guard, builder budget path (B7).
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from godsv import discovery  # noqa: E402
from godsv.capability import project_id_for  # noqa: E402
from godsv.events import EventStore  # noqa: E402

DEFAULT_FIXTURE = ROOT / "tests" / "fixtures" / "discovery_cases.json"

# Case → the later stage its *action* part needs. The discovery-level part of
# these cases is still evaluated; only the named action is marked blocked.
BLOCKED_ACTIONS = {
    "a01": "container start (Docker stage B6): findable + state only here",
    "a02": "container start (Docker stage B6): findable + state only here",
    "a03": "readiness failure updates health (Docker stage B6)",
    "q03": "dispatch-time revocation guard (not a discovery metric)",
    "g01": "builder budget path (B7)",
}

# --------------------------------------------------------------------------- #
# harness inventory — written from the §6 reference-tool contracts
# --------------------------------------------------------------------------- #

HARNESS_TOOLS = [
    {
        "name": "web_search",
        "scope": "global",
        "purpose": "Search the web through SearXNG and return ranked results with source links",
        "discovery": {
            "tasks": [
                "web search", "search the web", "search for current information",
                "Websuche", "im Web suchen", "recherchieren", "Suche",
            ],
            "synonyms": ["search", "lookup", "look up", "find", "Suche", "Recherche"],
            "phrases_en": ["search the web for current information", "look something up online"],
            "phrases_de": [
                "im Web nach aktuellen Informationen und Quellen suchen",
                "etwas online nachschlagen",
            ],
            "outputs": ["results:json", "sources:list"],
            "requires_services": ["searxng"],
            "platforms": ["linux"],
            "non_use_cases": ["pages behind a login", "documents about the machine itself"],
            "examples": ["search for release notes of a project"],
        },
        "network": {"allowed_domains": ["*"]},
        "validation": "validated",
        "tags": ["web", "search", "sources"],
    },
    {
        "name": "research_topic",
        "scope": "global",
        "purpose": "Research a topic across several sources, deduplicate and write a cited report",
        "discovery": {
            "tasks": [
                "deep dive research", "multi-source research", "compare sources",
                "ausfuehrliche Recherche", "mehrere Quellen vergleichen", "Bericht mit Belegen",
            ],
            "synonyms": ["research", "Recherche", "investigate", "deep dive", "study"],
            "phrases_en": ["do a sourced deep dive on a topic", "compile a report with citations"],
            "phrases_de": [
                "ein Thema gruendlich recherchieren und mit Quellen zusammenfassen",
                "einen Bericht mit Belegen erstellen",
            ],
            "inputs": ["topic:string"],
            "outputs": ["report:markdown", "citations:list"],
            "requires_services": ["searxng"],
            "non_use_cases": ["single lookup", "questions about this machine"],
        },
        "network": {"allowed_domains": ["*"]},
        "validation": "validated",
        "tags": ["research", "report", "sources"],
    },
    {
        "name": "pandoc_convert",
        "scope": "global",
        "purpose": (
            "Convert documents between formats (docx, odt, pdf, markdown, html, csv) "
            "with Pandoc"
        ),
        "discovery": {
            "tasks": [
                "convert documents", "document conversion", "Dokumente konvertieren",
                "Dateien umwandeln", "Format aendern", "Tabellen exportieren",
            ],
            "synonyms": ["convert", "conversion", "umwandeln", "konvertieren", "umformatieren"],
            "phrases_en": [
                "convert a document to another format",
                "turn a document into PDF, HTML or plain text",
            ],
            "phrases_de": [
                "ein Dokument in ein anderes Format bringen",
                "aus einem Dokument PDF oder Markdown erzeugen",
            ],
            "inputs": ["source_path:string", "target_format:string"],
            "outputs": ["converted_path:string"],
            "requires_services": ["pandoc"],
            "platforms": ["linux"],
            "non_use_cases": ["scanned pages without a text layer", "video or audio files"],
            "examples": ["convert a docx invoice to markdown"],
        },
        "validation": "validated",
        "tags": ["documents", "conversion", "pandoc"],
    },
    {
        "name": "check_readme",
        "scope": "global",
        "purpose": "Prove a target ships a README (product-state check)",
        "discovery": {
            "tasks": ["check README", "README pruefen", "required sections check"],
            "synonyms": ["readme check", "documentation check"],
            "inputs": ["target_path:string"],
            "outputs": ["verdict:string"],
            "non_use_cases": ["writing a README"],
        },
        "validation": "validated",
        "tags": ["readme", "check"],
    },
    {
        "name": "screen_capture",
        "scope": "global",
        "purpose": "Capture a screenshot of the current screen for inspection",
        "discovery": {
            "tasks": ["screenshot", "capture the screen"],
            "synonyms": ["screenshot", "screen capture"],
            "outputs": ["image_path:string"],
            "non_use_cases": ["navigating an application", "changing settings"],
        },
        "validation": "validated",
        "tags": ["screenshot", "screen"],
    },
    {
        "name": "alpha_exclusive_convert",
        "scope": "project",
        "project_id": "fixture:alpha",
        "purpose": "Convert documents inside project alpha only",
        "discovery": {
            "tasks": ["convert documents", "Dokumente konvertieren"],
            "inputs": ["source_path:string"],
        },
        "validation": "validated",
        "tags": ["documents", "conversion"],
    },
    {
        "name": "beta_convert",
        "scope": "project",
        "project_id": "fixture:beta",
        "purpose": "Convert documents inside project beta",
        "discovery": {
            "tasks": ["convert documents", "Dokumente konvertieren"],
            "inputs": ["source_path:string"],
        },
        "validation": "validated",
        "tags": ["documents", "conversion"],
    },
    {
        "name": "audio_trim_draft",
        "scope": "global",
        "purpose": "Trim silence out of audio recordings",
        "discovery": {
            "tasks": ["trim audio", "Stille entfernen", "Audio zuschneiden"],
            "inputs": ["source_path:string"],
        },
        "validation": "",
        "tags": ["audio", "trim"],
    },
]

# Scenario mutations (state part of a case) — applied to a fresh runtime copy.
SCENARIOS = {
    "base": {},
    "quarantine": {"quarantine": ["web_search"]},
    "degraded": {"degrade": ["pandoc_convert"]},
    "autostart": {"service": {"searxng": "stopped"}},
    "readiness_fail": {"service": {"searxng": "missing"}, "degrade": ["web_search"]},
    "revoked": {"quarantine": ["pandoc_convert"]},
}

SCENARIO_BY_CASE = {
    "q01": "quarantine",
    "q02": "degraded",
    "q03": "revoked",
    "a01": "autostart",
    "a02": "autostart",
    "a03": "readiness_fail",
    "s01": "base",
    "s02": "base",
    "s03": "base",
}


# --------------------------------------------------------------------------- #
# harness runtime
# --------------------------------------------------------------------------- #


def _plant(runtime: Path, tool: dict, fixture_ids: dict) -> None:
    """Write one approved manifest + registry spec (the shape the builder writes)."""
    import yaml

    project_id = tool.get("project_id", "")
    if project_id.startswith("fixture:"):
        project_id = fixture_ids.get(project_id.split(":", 1)[1], "")
    manifest = {
        "name": tool["name"],
        "version": tool.get("version", "0.1.0"),
        "type": tool.get("type", "cli"),
        "purpose": tool["purpose"],
        "status": "ready",
        "scope": tool.get("scope", "global"),
        "validation": {"status": tool.get("validation") or "", "checks": []},
    }
    if project_id:
        manifest["project_id"] = project_id
    if tool.get("network"):
        manifest["network"] = tool["network"]
    if tool.get("discovery"):
        manifest["discovery"] = tool["discovery"]
    tool_dir = runtime / "generated_tools" / tool["name"]
    tool_dir.mkdir(parents=True, exist_ok=True)
    (tool_dir / "manifest.yaml").write_text(
        yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    (tool_dir / f"{tool['name']}.py").write_text(
        f"def main(**kwargs):\n    return {{'tool': {tool['name']!r}}}\n", encoding="utf-8"
    )
    try:
        from godsv.tools import KIND_PYTHON, STATUS_APPROVED, ToolSpec, ToolStore

        store = ToolStore(runtime / "tools.db")
        try:
            spec = ToolSpec(
                name=tool["name"],
                title=tool["name"].replace("_", " ").title(),
                description=tool["purpose"],
                tags=list(tool.get("tags") or []),
                kind=KIND_PYTHON,
                module=tool["name"],
                entrypoint="main",
                sample_input={"source_path": "/tmp/input", "target_format": "markdown"}
                if tool["name"] in ("pandoc_convert", "alpha_exclusive_convert", "beta_convert")
                else {},
                status=STATUS_APPROVED,
            )
            store.upsert(spec)
        finally:
            store.close()
    except Exception as exc:  # pragma: no cover - harness convenience only
        print(f"warning: spec write failed for {tool['name']}: {exc}", file=sys.stderr)


def _apply_scenario(runtime: Path, scenario: str) -> dict:
    """Health-ledger + service state for one scenario (real ledger API)."""
    from godsv import capability_health as health

    config = SCENARIOS.get(scenario, {})
    for name in config.get("quarantine", []):
        # QUARANTINE_AFTER = 3 tolerated issues — the event AFTER the threshold
        # quarantines (capability_health._fold: consecutive > 3).
        for _ in range(4):
            health.record_trigger(runtime, name, "error_burst", detail="harness quarantine")
    for name in config.get("degrade", []):
        health.record_trigger(runtime, name, "runtime_error", detail="harness degradation")
    return dict(config.get("service", {}))


def build_harness_runtimes(root: Path) -> dict:
    """One planted runtime PER scenario.

    Ledger state is a mutation of a runtime, so each scenario needs its own
    runtime — otherwise the quarantine of case q01 would follow every later
    case (observed in the first harness run).
    """
    fixture_ids = {
        "alpha": project_id_for(root / "projects" / "alpha"),
        "beta": project_id_for(root / "projects" / "beta"),
    }
    runtimes: dict = {}
    for scenario in SCENARIOS:
        runtime = root / "scenarios" / scenario / "runtime"
        runtime.mkdir(parents=True, exist_ok=True)
        for tool in HARNESS_TOOLS:
            _plant(runtime, tool, fixture_ids)
        EventStore(runtime / "events.db").close()  # the runtime owns its event store
        runtimes[scenario] = {"runtime": runtime, "services": _apply_scenario(runtime, scenario)}
    return {"fixture_ids": fixture_ids, "runtimes": runtimes}


# --------------------------------------------------------------------------- #
# case execution
# --------------------------------------------------------------------------- #


# Harness tools that exist per fixture project only — the symbolic
# ``fixture:<name>*`` patterns of the frozen set bind to exactly these
# (project isolation: they must never surface in another project's context).
FIXTURE_EXCLUSIVE = frozenset(
    tool["name"] for tool in HARNESS_TOOLS if str(tool.get("project_id", "")).startswith("fixture:")
)


def _match_any(name: str, patterns: list[str], fixture_ids: dict) -> list[str]:
    hits = []
    for pattern in patterns:
        text = str(pattern)
        if text.startswith("fixture:"):
            bare = text[len("fixture:") :]
            if name in FIXTURE_EXCLUSIVE and fnmatch.fnmatchcase(name.lower(), bare.lower()):
                hits.append(pattern)
            continue
        if fnmatch.fnmatchcase(name.lower(), text.lower()):
            hits.append(pattern)
    return hits


def _case_need(case: dict) -> str:
    return str(case.get("need") or "")


def _project_for_case(case: dict, fixture_ids: dict, default_project: str) -> str:
    """The project context a case runs in — from the NEED, never from ``excluded``.

    Deriving it from the exclusion pattern would run a beta case inside alpha
    and make the alpha-exclusive tool legitimately visible (found in the first
    harness run: s01-s03 showed ``alpha_exclusive_convert``).
    """
    need = _case_need(case).lower()
    if "alpha" in need:
        return fixture_ids.get("alpha", default_project)
    if "beta" in need:
        return fixture_ids.get("beta", default_project)
    return default_project


def run_case(case: dict, *, runtime: Path, fixture_ids: dict, services: dict, live: bool) -> dict:
    project_id = _project_for_case(case, fixture_ids, fixture_ids.get("beta", ""))
    started = time.monotonic()
    result = discovery.discover(
        _case_need(case),
        runtime=runtime,
        project_id=project_id,
        services=services,
        include_locked=False,
    )
    latency_ms = (time.monotonic() - started) * 1000.0
    names = [row["name"] for row in result.get("results", [])]
    top3 = names[:3]
    allowed = list(case.get("allowed") or [])
    excluded = list(case.get("excluded") or [])
    recall_hits = [
        pattern
        for name in top3
        for pattern in _match_any(name, allowed, fixture_ids)
    ]
    leak_hits = [pattern for name in names for pattern in _match_any(name, excluded, fixture_ids)]
    block = discovery.context_block(result)
    case_id = str(case.get("id") or "")
    blocked_action = BLOCKED_ACTIONS.get(case_id)
    if live and (allowed or excluded):
        # Live mode: the frozen set binds to FIXTURE projects that a live
        # runtime does not have. A case whose matching capabilities exist only
        # under such a project, or whose target is not registered at all, is a
        # blocked surface — never a failed case and never a faked pass.
        runtime_names = _all_names(runtime)
        matches = [
            name for name in runtime_names for _ in _match_any(name, allowed, fixture_ids)
        ]
        if allowed and not matches:
            blocked_action = blocked_action or "target capability not registered in this runtime"
        elif matches:
            # Reachable = global in this runtime, or project-scoped to exactly
            # the case's project. Legacy (scope-less) and foreign-project tools
            # are not reachable in a project context — for the frozen set (whose
            # projects are fixtures) that is a blocked surface, not a failure.
            visible = {
                entry.name
                for entry in discovery.metadata_index(runtime, include_legacy=False)
                if entry.scope == "global" or entry.project_id == str(project_id)
            }
            if not (set(matches) & visible):
                blocked_action = blocked_action or (
                    "matching capabilities are not reachable in this runtime's project context"
                )
    executable = [row for row in result.get("results", []) if row.get("executable")]
    false_fit = None
    if case.get("kind") == "none":
        false_fit = bool(names)
    passed = None
    if not blocked_action:
        checks = [bool(recall_hits) if allowed else True, not leak_hits]
        if case.get("kind") == "none":
            # a no-match case must return NOTHING — an unimpaired-but-unfit
            # recommendation is a false fit too
            checks.append(not names)
        if case.get("kind") == "build_candidate":
            checks.append(not executable)
        if case.get("must_not_build"):
            # reuse: the existing capability is recommended AND no build is
            # proposed for the same need
            checks.append(bool(recall_hits) and not list(result.get("build_candidates") or []))
        if case.get("must_report_error"):
            checks.append(bool((result.get("error") or {}).get("code")))
        expect_state = case.get("expect_state")
        if expect_state:
            states = {row["state"] for row in result.get("results", [])}
            checks.append(
                expect_state in states
                or (expect_state == "quarantined" and "locked" in states)
            )
        passed = all(checks)
    return {
        "id": case_id,
        "kind": case.get("kind", ""),
        "category": case.get("category", ""),
        "need": _case_need(case),
        "allowed": allowed,
        "excluded": excluded,
        "top3": top3,
        "recall_hits": sorted(set(recall_hits)),
        "leak_hits": sorted(set(leak_hits)),
        "executable": [row["name"] for row in executable],
        "states": {row["name"]: row["state"] for row in result.get("results", [])},
        "error_code": (result.get("error") or {}).get("code"),
        "false_fit": false_fit,
        "latency_ms": round(latency_ms, 3),
        "context_tokens": discovery.estimate_tokens(block),
        "critical": case_id[:1] in ("s", "q"),
        "blocked": blocked_action,
        "passed": passed,
    }


def _all_names(runtime: Path) -> list[str]:
    try:
        return [entry.name for entry in discovery.metadata_index(runtime)]
    except Exception:
        return []


# --------------------------------------------------------------------------- #
# report
# --------------------------------------------------------------------------- #


def evaluate(
    cases: list[dict], *, runtimes: dict, fixture_ids: dict, live: bool
) -> dict:
    rows = []
    for case in cases:
        scenario = SCENARIO_BY_CASE.get(str(case.get("id")), "base")
        entry = runtimes.get(scenario) or runtimes["base"]
        rows.append(
            run_case(
                case,
                runtime=entry["runtime"],
                fixture_ids=fixture_ids,
                services=entry.get("services", {}),
                live=live,
            )
        )
    evaluable = [row for row in rows if not row["blocked"]]
    recall_rows = [row for row in evaluable if row["allowed"]]
    recall_num = sum(1 for row in recall_rows if row["recall_hits"])
    none_rows = [row for row in evaluable if row["kind"] == "none"]
    false_fit_num = sum(1 for row in none_rows if row["false_fit"])
    leak_rows = [row for row in evaluable if row["excluded"]]
    leak_num = sum(1 for row in leak_rows if row["leak_hits"])
    critical = [row for row in rows if row["critical"]]
    # A blocked case is NOT a failed case: the action it needs belongs to a
    # later stage (Docker B6 / builder B7) and is reported as blocked.
    critical_failed = [
        row for row in critical if not row["blocked"] and row["passed"] is not True
    ]
    critical_blocked = [row for row in critical if row["blocked"]]
    critical_passed = [
        row for row in critical if not row["blocked"] and row["passed"] is True
    ]
    latencies = [row["latency_ms"] for row in evaluable] or [0.0]
    tokens = [row["context_tokens"] for row in evaluable] or [0]
    return {
        "mode": "live" if live else "harness",
        "runtime": str(runtimes["base"]["runtime"]),
        "cases": rows,
        "metrics": {
            "recall_at_3": {"numerator": recall_num, "denominator": len(recall_rows)},
            "false_fit_on_no_match": {"numerator": false_fit_num, "denominator": len(none_rows)},
            "scope_leaks": {"numerator": leak_num, "denominator": len(leak_rows)},
            "latency_ms": {
                "mean": round(statistics.fmean(latencies), 3),
                "p95": round(sorted(latencies)[max(0, int(0.95 * (len(latencies) - 1)))], 3),
                "max": round(max(latencies), 3),
                "total": round(sum(latencies), 3),
            },
            "context_tokens": {
                "mean": round(statistics.fmean(tokens), 1),
                "max": max(tokens),
            },
            "critical_cases": {
                "passed": len(critical_passed),
                "total": len(critical),
                "failed": [row["id"] for row in critical_failed],
                "blocked": [row["id"] for row in critical_blocked],
            },
            "blocked": {"numerator": len(rows) - len(evaluable), "denominator": len(rows)},
            "evaluated": {"numerator": len(evaluable), "denominator": len(rows)},
        },
    }


def _print_report(report: dict, *, verbose: bool) -> None:
    metrics = report["metrics"]
    print(f"discovery eval — mode={report['mode']} runtime={report['runtime']}")
    print(
        "  recall@3:                 "
        f"{metrics['recall_at_3']['numerator']}/{metrics['recall_at_3']['denominator']}"
    )
    print(
        "  false fit (no_match):     "
        f"{metrics['false_fit_on_no_match']['numerator']}/{metrics['false_fit_on_no_match']['denominator']}"
    )
    print(
        "  scope leaks:              "
        f"{metrics['scope_leaks']['numerator']}/{metrics['scope_leaks']['denominator']}"
    )
    latency = metrics["latency_ms"]
    print(
        f"  search latency (ms):      mean={latency['mean']} "
        f"p95={latency['p95']} max={latency['max']}"
    )
    tokens = metrics["context_tokens"]
    print(f"  context tokens:           mean={tokens['mean']} max={tokens['max']}")
    critical = metrics["critical_cases"]
    print(
        f"  critical (s*/q*):         {critical['passed']}/{critical['total']} "
        f"(blocked: {', '.join(critical['blocked']) or 'none'})"
    )
    blocked = metrics["blocked"]
    print(f"  blocked (separate):       {blocked['numerator']}/{blocked['denominator']}")
    if verbose:
        for row in report["cases"]:
            marker = "PASS" if row["passed"] else ("BLOCK" if row["blocked"] else "FAIL")
            print(
                f"  [{marker:5}] {row['id']:4} top3={row['top3']} "
                f"hits={row['recall_hits']} leaks={row['leak_hits']} "
                f"err={row['error_code']} blocked={row['blocked'] or ''}"
            )
    for row in report["cases"]:
        if row["blocked"]:
            print(f"  blocked: {row['id']} — {row['blocked']}")
    if critical["failed"]:
        print(f"  CRITICAL FAILURES: {', '.join(critical['failed'])}")
    if metrics["scope_leaks"]["numerator"]:
        print("  SCOPE LEAKS PRESENT — acceptance fails")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", default=str(DEFAULT_FIXTURE))
    parser.add_argument("--runtime", default="", help="real runtime dir (live mode, read-only)")
    parser.add_argument("--json", default="", help="write the full report as JSON to this path")
    parser.add_argument("--verbose", action="store_true", help="per-case lines")
    args = parser.parse_args(argv)

    fixture = json.loads(Path(args.fixture).read_text(encoding="utf-8"))
    cases = list(fixture.get("cases") or [])
    live = bool(args.runtime)
    if live:
        runtime = Path(args.runtime).expanduser()
        fixture_ids = {"alpha": "fixture:alpha", "beta": "fixture:beta"}
        runtimes = {name: {"runtime": runtime, "services": {}} for name in SCENARIOS}
        report = evaluate(cases, runtimes=runtimes, fixture_ids=fixture_ids, live=True)
    else:
        with tempfile.TemporaryDirectory(prefix="gv-discovery-eval-") as tmp:
            meta = build_harness_runtimes(Path(tmp))
            report = evaluate(
                cases,
                runtimes=meta["runtimes"],
                fixture_ids=meta["fixture_ids"],
                live=False,
            )
    _print_report(report, verbose=args.verbose)
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
        print(f"report written: {args.json}")
    critical = report["metrics"]["critical_cases"]
    clean = not critical["failed"] and not report["metrics"]["scope_leaks"]["numerator"]
    return 0 if clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
