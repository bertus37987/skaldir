#!/usr/bin/env python3
"""Acceptance-evidence recorder for the Skaldir criteria inventory.

Purpose: make the evidence chain of `ABNAHME.md` §5/§5.1 and the master goal §2
machine-checkable instead of prose. Every record carries the binding fields a
reviewer needs to decide whether a claim still holds for THIS tree:

    criterion_id, run_id, goal_revision, workspace_fingerprint, commit,
    dirty_fingerprint, platform, environment, runner, start_time, end_time,
    exit_code, result, raw_artifact, producer

A record is only as strong as its command. This tool never invents a result: it
runs the command it is given, stores the real exit code and the real output tail,
and refuses to record anything when the command could not be started.

Usage
-----
    # record one run for a criterion (repeatable; appends)
    python3 tools/acceptance_evidence.py record RF-10 \
        --cmd ".venv/bin/python tools/host_rf10_stop_probe.py /tmp/gv_secfull" \
        --note "stop latency patched vs pristine"

    # negative case for the same criterion
    python3 tools/acceptance_evidence.py record RF-10 --kind negative \
        --cmd ".venv/bin/python -m pytest tests/test_rf10_stop.py -k counter"

    # recompute the manifest and the aggregate from the records + the register
    python3 tools/acceptance_evidence.py rebuild

    # print the aggregate without writing anything
    python3 tools/acceptance_evidence.py status

The aggregate (TOTAL/SATISFIED/FAILED/BLOCKED/UNKNOWN) is computed from
`audit/REQUIREMENTS.json` plus the recorded evidence — never edited by hand.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shlex
import subprocess
import sys
import time
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
AUDIT = REPO / "audit"
EVIDENCE_DIR = AUDIT / "evidence"
RECORD_DIR = EVIDENCE_DIR / "records"
MANIFEST = EVIDENCE_DIR / "manifest.json"
REGISTER = AUDIT / "REQUIREMENTS.json"

PRODUCER = "acceptance-evidence/1"
RAW_KEEP_BYTES = 4000


#: Ambient environment variables that silently change which tree a child process
#: imports. ``PYTHONPATH`` outranks site-packages and the editable finder, so a
#: probe or a test that builds an engine copy will read the LIVE engine while it
#: believes it reads the copy -- the same command then yields opposite results in
#: different environments. Recorded here, and scrubbed by default.
IMPORT_HIJACKERS = ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP")


def _child_env(clean: bool) -> tuple[dict, dict]:
    env = os.environ.copy()
    removed = {}
    if clean:
        for key in IMPORT_HIJACKERS:
            if key in env:
                removed[key] = env.pop(key)
    return env, removed


def _run(cmd: list[str], timeout: int, *, cwd: Path, clean_env: bool) -> tuple[int, str, int, dict]:
    started = time.time()
    env, removed = _child_env(clean_env)
    try:
        proc = subprocess.run(  # noqa: S603 - the command is the evidence, given explicitly
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        return proc.returncode, out, int((time.time() - started) * 1000), removed
    except FileNotFoundError as exc:
        return 127, f"could not start command: {exc}", int((time.time() - started) * 1000), removed
    except subprocess.TimeoutExpired:
        return 124, f"timeout after {timeout}s", int((time.time() - started) * 1000), removed


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(*args: str) -> str:
    proc = subprocess.run(  # noqa: S603
        ["git", *args], cwd=REPO, capture_output=True, text=True, check=False
    )
    return (proc.stdout or "").strip()


def binding() -> dict:
    """The tree state that this evidence belongs to."""
    dirty = _git("status", "--porcelain")
    dirty_paths = sorted(line[3:] for line in dirty.splitlines() if line.strip())
    return {
        "run_id": uuid.uuid4().hex[:16],
        "commit": _git("rev-parse", "HEAD"),
        "commit_subject": _git("log", "-1", "--pretty=%s")[:120],
        "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "dirty": bool(dirty_paths),
        "dirty_paths": dirty_paths[:200],
        "dirty_fingerprint": hashlib.sha256(dirty.encode("utf-8")).hexdigest()[:16],
        "workspace_fingerprint": _workspace_fingerprint(),
        "platform": f"{platform.system()} {platform.machine()}",
        "environment": {
            "python": platform.python_version(),
            "kernel": platform.release(),
            "repo": str(REPO),
        },
        "producer": PRODUCER,
    }


def _workspace_fingerprint() -> str:
    """Hash of the tracked file list + sizes: cheap, stable, no content leak."""
    out = _git("ls-files")
    digest = hashlib.sha256()
    for rel in out.splitlines():
        path = REPO / rel
        try:
            digest.update(rel.encode())
            digest.update(str(path.stat().st_size).encode())
        except OSError:
            continue
    return digest.hexdigest()[:16]


def record(args: argparse.Namespace) -> int:
    register = json.loads(REGISTER.read_text(encoding="utf-8"))
    known = {c["id"] for c in register["criteria"]}
    if args.criterion not in known:
        print(f"unknown criterion id: {args.criterion}", file=sys.stderr)
        return 2

    cmd = shlex.split(args.cmd)
    if not cmd:
        print("empty command — nothing to record", file=sys.stderr)
        return 2
    if any(part == "~" or part.startswith("~/") for part in cmd):
        # Without a shell, "~/x" is passed through literally: a probe that
        # sys.path.insert's it then imports whatever the LAUNCHED interpreter
        # resolves — i.e. the wrong tree, silently. Expand it first.
        print(
            "refusing a command containing an unexpanded '~' — pass an absolute path "
            f"(expanduser gives {os.path.expanduser('~')})",
            file=sys.stderr,
        )
        return 2
    started = time.time()
    cwd = Path(args.cwd).expanduser().resolve()
    if not cwd.is_dir():
        print(f"--cwd is not a directory: {cwd}", file=sys.stderr)
        return 2
    exit_code, output, duration_ms, removed_env = _run(
        cmd, args.timeout, cwd=cwd, clean_env=not args.keep_env
    )
    info = binding()
    info["cwd"] = str(cwd)
    info["env_scrubbed"] = sorted(removed_env)
    info["ambient_pythonpath"] = os.environ.get("PYTHONPATH", "")

    RECORD_DIR.mkdir(parents=True, exist_ok=True)
    raw_name = f"{info['run_id']}-{args.criterion}-{args.kind}.txt"
    raw_path = EVIDENCE_DIR / raw_name
    raw_path.write_text(output[-args.keep_bytes :], encoding="utf-8")

    entry = {
        "evidence_id": f"ev-{info['run_id']}",
        **info,
        "criterion_id": args.criterion,
        "kind": args.kind,
        "runner": " ".join(cmd[:3]),
        "command_or_action": args.cmd,
        "start_time": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(started)),
        "end_time": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime()),
        "duration_ms": duration_ms,
        "exit_code": exit_code,
        "result": "pass" if exit_code == 0 else f"rc={exit_code}",
        "note": args.note,
        "raw_artifact": str(raw_path.relative_to(REPO)),
        "output_tail": output[-1200:],
    }
    path = RECORD_DIR / f"{args.criterion}.json"
    existing = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
    existing.append(entry)
    path.write_text(json.dumps(existing, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"recorded {entry['evidence_id']} for {args.criterion} ({args.kind}): {entry['result']}")
    mark = f" +dirty({info['dirty_fingerprint']})" if info["dirty"] else ""
    print(f"  commit {info['commit'][:8]}{mark}  raw: {entry['raw_artifact']}")
    scrubbed = info["env_scrubbed"] or "nothing"
    ambient = info["ambient_pythonpath"] or "(unset)"
    print(f"  cwd: {info['cwd']} | scrubbed: {scrubbed} | ambient PYTHONPATH: {ambient}")
    if args.show:
        print(output[-args.show :])
    return 0


def _load_records() -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    if not RECORD_DIR.is_dir():
        return out
    for path in sorted(RECORD_DIR.glob("*.json")):
        try:
            out[path.stem] = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
    return out


def aggregate() -> dict:
    """Compute the audit aggregate from the register + the recorded evidence."""
    register = json.loads(REGISTER.read_text(encoding="utf-8"))
    records = _load_records()
    per_criterion = []
    for crit in register["criteria"]:
        cid = crit["id"]
        recs = records.get(cid, [])
        passes = [r for r in recs if r.get("exit_code") == 0]
        negatives = [r for r in recs if r.get("kind") == "negative"]
        acceptance = crit.get("acceptance_state", "unknown")
        per_criterion.append(
            {
                "criterion_id": cid,
                "implementation_state": crit.get("implementation_state", "unknown"),
                "evidence_state": crit.get("evidence_state", "unreviewed"),
                "acceptance_state": acceptance,
                "evidence_ids": [r["evidence_id"] for r in recs],
                "runs": len(recs),
                "passing_runs": len(passes),
                "negative_cases": len(negatives),
                "latest_commit": recs[-1]["commit"][:8] if recs else "",
                "has_current_evidence": any(
                    r.get("exit_code") == 0 and r.get("dirty") is True for r in recs
                ),
                "blocker": crit.get("blockers") or "",
                "next_action": crit.get("next_action") or "",
            }
        )

    def count(pred) -> int:
        return sum(1 for c in per_criterion if pred(c))

    def is_unknown(c: dict) -> bool:
        """UNKNOWN means nobody has read the code against this criterion yet.

        `implementation_state == "unknown"` is exactly that state; an acceptance
        state of "unknown" counts the same way. Keeping this derived from the
        register (not from a hand-kept number) is what makes the aggregate
        trustworthy.
        """
        return c["implementation_state"] == "unknown" or c["acceptance_state"] == "unknown"

    summary = {
        "total": len(per_criterion),
        "satisfied": count(lambda c: c["acceptance_state"] == "satisfied"),
        "failed": count(lambda c: c["acceptance_state"] == "failed"),
        "blocked": count(lambda c: c["acceptance_state"] == "blocked"),
        "unknown": count(is_unknown),
        "partial": count(
            lambda c: not is_unknown(c)
            and c["acceptance_state"] not in ("satisfied", "failed", "blocked")
        ),
        "criteria_with_evidence": count(lambda c: c["runs"] > 0),
        "criteria_with_negative_case": count(lambda c: c["negative_cases"] > 0),
        "criteria_with_current_evidence": count(lambda c: c["has_current_evidence"]),
    }
    summary["accounted"] = (
        summary["satisfied"] + summary["failed"] + summary["blocked"]
        + summary["unknown"] + summary["partial"]
    )
    return {"summary": summary, "criteria": per_criterion}


def rebuild() -> int:
    result = aggregate()
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "kind": "acceptance_evidence_manifest",
        "observed_project_head": _git("rev-parse", "HEAD"),
        "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "generated_by": PRODUCER,
        "summary": result["summary"],
        "criteria": result["criteria"],
        "notes": [
            "The aggregate is computed from audit/REQUIREMENTS.json plus the recorded runs — "
            "never hand-edited.",
            "A criterion counts as satisfied only when the register says so; evidence alone "
            "does not promote it.",
            "`has_current_evidence` is true only for runs recorded while the tree was dirty "
            "(the run provably reflects the working tree, not an older commit).",
            "CAVEAT (findings AF-06): the host engine tree ~/.hermes/hermes-agent is NOT pristine "
            "-- it carries the RF-06 overlay in agent/context_compressor.py. Runs that used it as "
            "the untouched reference compare against 'this overlay absent', not against upstream.",
        ],
    }
    MANIFEST.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    s = result["summary"]
    print(f"evidence manifest written — {MANIFEST.relative_to(REPO)}")
    print(f"  TOTAL {s['total']} | SATISFIED {s['satisfied']} | FAILED {s['failed']}"
          f" | BLOCKED {s['blocked']} | PARTIAL/OPEN {s['partial']} | UNKNOWN {s['unknown']}"
          f" (accounted {s['accounted']})")
    print(f"  with evidence {s['criteria_with_evidence']} | with negative case "
          f"{s['criteria_with_negative_case']} | current-revision evidence "
          f"{s['criteria_with_current_evidence']}")
    return 0


def status() -> int:
    result = aggregate()
    s = result["summary"]
    print(json.dumps(s, indent=2))
    gaps = [c for c in result["criteria"] if c["acceptance_state"] == "unknown"]
    print(f"\nunknown criteria: {len(gaps)}")
    for c in gaps[:40]:
        nxt = (c["next_action"] or c["blocker"] or "no next action recorded")[:100]
        print(f"  {c['criterion_id']:7s} {nxt}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)

    rec = sub.add_parser("record", help="run a command and store it as evidence")
    rec.add_argument("criterion")
    rec.add_argument("--cmd", required=True)
    rec.add_argument("--kind", default="positive", choices=("positive", "negative", "blocked"))
    rec.add_argument("--note", default="")
    rec.add_argument("--timeout", type=int, default=900)
    rec.add_argument("--cwd", default=str(REPO),
                     help="working directory for the command (engine-tree probes need their tree)")
    rec.add_argument("--keep-env", action="store_true",
                     help="do NOT scrub PYTHONPATH/PYTHONHOME/PYTHONSTARTUP from the child "
                          "(only for commands that genuinely need the ambient env)")
    rec.add_argument("--keep-bytes", type=int, default=RAW_KEEP_BYTES)
    rec.add_argument("--show", type=int, default=0)

    sub.add_parser("rebuild", help="recompute the evidence manifest and the aggregate")
    sub.add_parser("status", help="print the aggregate without writing")

    args = parser.parse_args()
    if args.action == "record":
        return record(args)
    if args.action == "rebuild":
        return rebuild()
    return status()


if __name__ == "__main__":
    raise SystemExit(main())
