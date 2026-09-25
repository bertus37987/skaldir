#!/usr/bin/env python3
"""Build the artifact hash manifest required by ABNAHME.md §114.

§114 asks for a "Screenshot-/Artefaktmanifest mit Hashes" of the release
artifacts. This tool emits ``docs/audit/ARTIFACT-MANIFEST.json`` with, per
artifact, its real ``sha256``, its byte size and what it is.

Rules:

* **Only real files.** A path that does not exist is reported in
  ``missing_artifacts`` and the tool exits non-zero — it never writes a
  placeholder digest for something that is not on disk.
* The manifest does not hash itself (a file cannot contain its own digest); it
  records ``self_excluded`` with the reason instead.
* ``groups`` mirror the §114 completion folder so a reviewer can see which
  mandatory artifact is still absent.

Usage::

    .venv/bin/python tools/build_artifact_manifest.py            # write the manifest
    .venv/bin/python tools/build_artifact_manifest.py --check     # fail if stale
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "docs" / "audit" / "ARTIFACT-MANIFEST.json"

#: group -> (glob list, description of what the group is)
GROUPS: dict[str, tuple[tuple[str, ...], str]] = {
    "criteria_register": (
        (
            "audit/REQUIREMENTS.json",
            "audit/verification-mapping.json",
            "audit/CODE_MAP.md",
            "audit/RESUME.md",
            "audit/findings.json",
        ),
        "§114/§115 audit package: criteria register (97 ids, three state dimensions),"
        " verified evidence mapping, generated CODE_MAP, resume, §113 findings",
    ),
    "completion_folder": (
        (
            "docs/audit/TEST_REPORT.md",
            "docs/audit/PERFORMANCE.md",
            "docs/audit/BENCHMARK_MANIFEST.json",
            "docs/audit/BENCHMARK-RAW-README.md",
            "docs/audit/SKILLS_USED.md",
            "docs/audit/UPDATE_COMPATIBILITY.md",
            "docs/audit/RELEASE_NOTES.md",
            "docs/audit/PLATFORM-MATRIX.md",
            "docs/audit/DOCKER-BUILDER-EVIDENCE.md",
            "docs/audit/RF09-CRASH-WINDOW-INVENTORY.md",
            "docs/audit/SYSTEM_ONE_QUESTIONS.json",
            "docs/audit/COMPUTER_USE_SUBTOOLS.md",
        ),
        "§114 Pflichtartefakte of the completion folder (TEST_REPORT, PERFORMANCE,"
        " BENCHMARK_MANIFEST, raw-data README, SKILLS_USED, UPDATE_COMPATIBILITY,"
        " RELEASE_NOTES, Plattformmatrix, Docker evidence, RF-09 inventory,"
        " System-One question manifest, Computer-Use subtool manifest)",
    ),
    "host_overlays": (
        ("host-overlay/patch_*.py",),
        "Anchored, idempotent engine overlays (RF-02..RF-10 + agent/tool refresh)"
        " — artifacts, applied only on explicit invocation",
    ),
    "probes": (
        (
            "tools/host_engine_regression_probe.py",
            "tools/host_rf_monotonic_probe.py",
            "tools/host_rf02_deadline_probe.py",
            "tools/host_rf04_retry_budget_probe.py",
            "tools/host_rf05_fallback_probe.py",
            "tools/host_rf07_interim_report_probe.py",
            "tools/host_rf08_segment_probe.py",
            "tools/host_rf09_crash_probe.py",
            "tools/host_rf10_stop_probe.py",
            "tools/rf_measure.py",
            "tools/rf_test_provider.py",
        ),
        "Red-first engine probes + the RF measurement harness and its isolated test provider",
    ),
    "pinned_tests": (
        (
            "tests/test_rf02_request_deadline.py",
            "tests/test_rf03_monotonic_stream_clock.py",
            "tests/test_rf07_interim_report.py",
            "tests/test_rf08_segment.py",
            "tests/test_rf09_crash_windows.py",
            "tests/test_rf10_stop_responsiveness.py",
            "tests/test_rf_test_provider.py",
            "tests/test_secfix16.py",
            "tests/test_subagent_briefs.py",
            "tests/test_shared_budget.py",
            "tests/test_host_overlay.py",
        ),
        "Hermetic pins for the overlays and for the review-16 fixes",
    ),
    "evaluation": (
        (
            "tools/build_audit_register.py",
            "tools/check_findings_criteria.py",
            "tools/eval_benchmark.py",
            "tools/gv_benchmark.py",
            "tools/gv_discovery_eval.py",
            "docs/BENCHMARK-RUN-SCHEMA.json",
            "docs/DISCOVERY-TESTSET.md",
            "tests/fixtures/discovery_cases.json",
        ),
        "§109-§114 evaluation harness: audit-register builder, findings cross-check,"
        " §97 benchmark evaluation, benchmark schema, frozen discovery test set",
    ),
    "raw_data": (
        ("outputs/rf/*/raw.jsonl", "outputs/rf/*/summary.json"),
        "Raw measurement data that exists on disk (RF test-provider round of 2026-09-16)",
    ),
    "screenshots": (
        ("ui-artifacts/*.png", "ui-artifacts/audit/*.png"),
        "Real screenshot artifacts present in the repo (UI waves)"
        " — no screenshot is regenerated by this tool",
    ),
    "state_docs": (
        (
            "docs/RF-STATUS.md",
            "docs/PROJECT-STATE.md",
            "docs/E2E-LIVE-PROOF-40.md",
            "docs/VERIFICATION-1.4.md",
        ),
        "Living state documents the criteria mappings cite",
    ),
}

#: Artifacts the groups above cannot see yet, tracked explicitly so their
#: absence is visible instead of silently missing.
EXPECTED_ABSENT = (
    "docs/audit/SYSTEM_ONE_REPORT.md",
    "docs/audit/benchmark-runs/*.jsonl",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve(globs: tuple[str, ...]) -> list[Path]:
    found: list[Path] = []
    for pattern in globs:
        matches = sorted(REPO.glob(pattern))
        if matches:
            found.extend(matches)
        else:
            found.append(REPO / pattern)  # keep the miss visible
    return sorted({p for p in found}, key=lambda p: str(p))


def repo_head() -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(REPO), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        ).stdout.strip()
    except OSError:
        return ""


def build() -> tuple[dict, list[str]]:
    groups: dict[str, dict] = {}
    missing: list[str] = []
    total_files = 0
    total_bytes = 0

    for name, (globs, description) in GROUPS.items():
        entries = []
        for path in resolve(globs):
            rel = str(path.relative_to(REPO)) if path.is_relative_to(REPO) else str(path)
            if not path.is_file():
                missing.append(rel)
                continue
            size = path.stat().st_size
            entries.append({"path": rel, "sha256": sha256(path), "size_bytes": size})
            total_files += 1
            total_bytes += size
        groups[name] = {"description": description, "artifacts": entries}

    for pattern in EXPECTED_ABSENT:
        if not list(REPO.glob(pattern)):
            missing.append(f"{pattern} (expected absent — §114 artifact not produced yet)")

    payload = {
        "schema_version": 1,
        "kind": "artifact_hash_manifest",
        "contract": "ABNAHME.md §114 ('Screenshot-/Artefaktmanifest mit Hashes')",
        "observed_project_head": repo_head(),
        "hash": "sha256",
        "self_excluded": {
            "path": "docs/audit/ARTIFACT-MANIFEST.json",
            "reason": (
                "a file cannot carry its own digest; hash it externally if you need to"
                " pin this manifest"
            ),
        },
        "totals": {"files": total_files, "bytes": total_bytes},
        "missing_artifacts": missing,
        "groups": groups,
    }
    return payload, missing


STAMP_KEY = '"observed_project_head"'


def _without_stamp(text: str) -> str:
    """Drop the informational head stamp before comparing.

    The stamp records where the tree stood when the manifest was written; a later
    commit would otherwise leave the gate red forever, and a gate that is always
    red gets ignored. Content is what must match.
    """
    return "\n".join(line for line in text.splitlines() if STAMP_KEY not in line)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="fail when the manifest is stale")
    args = parser.parse_args()

    payload, missing = build()
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"

    if args.check:
        current = _without_stamp(OUT.read_text(encoding="utf-8")) if OUT.is_file() else None
        if current != _without_stamp(text):
            print(f"stale artifact manifest: {OUT}", file=sys.stderr)
            return 1
        print(
            f"artifact manifest current — {payload['totals']['files']} files, "
            f"{len(missing)} expected-absent entries"
        )
        return 0

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    print(
        f"wrote {OUT.relative_to(REPO)} — {payload['totals']['files']} files hashed "
        f"({payload['totals']['bytes']} bytes)"
    )
    if missing:
        print("expected-absent / missing artifacts:", *missing, sep="\n  ")
    # Missing §114 artifacts are a finding, not a crash: the manifest must exist
    # to make them visible. Exit non-zero only when a FILE inside a group is gone.
    hard_missing = [m for m in missing if "(expected absent" not in m]
    if hard_missing:
        print(
            "ERROR: declared artifacts that are not on disk:",
            *hard_missing,
            sep="\n  ",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
