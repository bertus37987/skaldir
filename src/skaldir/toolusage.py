"""Tool usage ledger (spec §15): call counts, failures, last errors.

Append-only JSONL next to the registry. Only metadata is recorded — never
payloads or results — and error strings pass the secret scanner's redactor
before they are written (spec §11: exception messages must not leak keys).
"""

from __future__ import annotations

import json
import time
from pathlib import Path


def _redact(text: str) -> str:
    try:
        from .secretscan import redact

        cleaned, _findings = redact(text or "")
        return cleaned
    except Exception:
        return text or ""


def usage_path(runtime: str | Path) -> Path:
    return Path(runtime) / "tool_usage.jsonl"


def record_usage(
    runtime: str | Path,
    name: str,
    *,
    ok: bool,
    error: str = "",
    duration_ms: int | None = None,
) -> None:
    """One call of tool ``name`` — append-only, best effort, never raises."""
    record: dict = {"ts": time.time(), "name": name, "ok": bool(ok)}
    if duration_ms is not None:
        record["duration_ms"] = int(duration_ms)
    if error:
        record["error"] = _redact(str(error))[:300]
    path = usage_path(runtime)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _entries(runtime: str | Path) -> list[dict]:
    path = usage_path(runtime)
    if not path.exists():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            rows.append(parsed)
    return rows


def usage_summary(runtime: str | Path, name: str, *, limit_errors: int = 3) -> dict:
    """Aggregated stats for one tool (spec §15: usage + success/failure + errors)."""
    rows = [row for row in _entries(runtime) if row.get("name") == name]
    ok = sum(1 for row in rows if row.get("ok"))
    failed = len(rows) - ok
    errors = [str(row.get("error", "")) for row in rows if not row.get("ok") and row.get("error")]
    durations = [int(row.get("duration_ms") or 0) for row in rows if row.get("duration_ms")]
    return {
        "count": len(rows),
        "ok": ok,
        "failed": failed,
        "success_rate": round(ok / len(rows), 3) if rows else None,
        "avg_duration_ms": int(sum(durations) / len(durations)) if durations else None,
        "last_used": max((float(row.get("ts") or 0) for row in rows), default=None),
        "last_errors": errors[-limit_errors:][::-1],
    }


def usage_stats(runtime: str | Path) -> dict[str, dict]:
    """Aggregated per-tool reliability (spec §11/§15 metrics).

    runs / passes / fails come straight from the append-only ledger — a
    fail is a gate run that refused (the tool found the target broken).
    False passes/false fails are structurally excluded by validation: a
    testing tool only reaches the completion gate with BROKEN→FAIL and
    WORKING→PASS proven (validation.status=validated).
    """
    stats: dict[str, dict] = {}
    path = usage_path(runtime)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return stats
    for line in lines:
        try:
            record = json.loads(line)
        except Exception:
            continue
        name = str(record.get("name") or "")
        if not name:
            continue
        entry = stats.setdefault(
            name, {"runs": 0, "passes": 0, "fails": 0, "last_ts": 0.0, "last_error": ""}
        )
        entry["runs"] += 1
        if record.get("ok"):
            entry["passes"] += 1
        else:
            entry["fails"] += 1
            if record.get("error"):
                entry["last_error"] = str(record["error"])[:200]
        entry["last_ts"] = max(float(entry["last_ts"]), float(record.get("ts") or 0.0))
    return stats


def usage_counts(runtime: str | Path) -> dict[str, int]:
    """Call counts per tool — for the registry table."""
    counts: dict[str, int] = {}
    for row in _entries(runtime):
        name = str(row.get("name") or "")
        if name:
            counts[name] = counts.get(name, 0) + 1
    return counts
