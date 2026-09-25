"""Tool health lifecycle (Block 6/TOOLS-31/32): health is its own fact.

Approval, verification state and *health* are three separate facts. A tool that
passed the gates and was verified can degrade later — and when it does, it must
leave the auto-routing surface until it is retested, repaired or quarantined.

States (one value per tool, derived by folding an append-only ledger):

    CANDIDATE      known, no verification evidence yet — never auto-routed
    BUILDING       the builder is working on it — never auto-routed
    VERIFIED       verification evidence was invalidated (code changed) —
                   needs a fresh run before it is trusted again
    HEALTHY        verified and currently working — auto-routed
    DEGRADED       a recoverable problem was observed — retest/repair
    BROKEN         a hard failure was observed (schema/API/dependency/false PASS)
    QUARANTINED    too many consecutive degradations — removed, retest required
    RETIRED        withdrawn by the owner — never routed again

This module is a *reader of facts*, not a second registry: the tool registry
stays ``generated_tools/*/manifest.yaml`` + ``tools.db`` (``toolhub``/``tools``),
the usage ledger stays ``tool_usage.jsonl`` (``toolusage``). Health only records
what was observed about those tools, in ``capability_health.jsonl``.

Four separate states, never conflated (they answer different questions):

============================  =======================  ============================================
axis                          source of truth          question
============================  =======================  ============================================
registration                  ``tools.db`` +           does the tool exist as an approved spec?
                              ``generated_tools/…``
validation                    ``manifest.yaml``        did it pass its own cases (broken→FAIL,
                              ``validation``           working→PASS)?
availability                  the runtime/execution    can it run here right now (sandbox,
                              layer                    dependencies, service reachable)?
health (this module)          ``capability_health``    was it *observed* working/broken lately?
                              ``.jsonl`` ledger
============================  =======================  ============================================

A tool can be registered and validated and still be unhealthy; being unhealthy
never changes the registry. Selection reads the axes through
:func:`selectable` (auto-routing in ``capability_mining.route_candidates``,
hot-set admission in ``liveinsert``).

State changes are written to the event store as ``capability_health_changed``
(one event per *transition*; the event store is the audit surface, the ledger
stays the state). The append is wrapped in try/except on purpose: an audit
write must never kill the caller that just observed reality.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

HEALTH_FILE = "capability_health.jsonl"
USAGE_CURSOR_FILE = "capability_health_cursor.json"

#: The container adapter spec of a tool (mirrors ``skaldir.toolbuilder.ADAPTER_FILE``
#: — the module is not imported here to keep this ledger free of import cycles).
ADAPTER_FILE = "adapter.json"

# Audit surface: one event per health *transition* (append-only event store).
EVENT_KIND = "capability_health_changed"
EVENT_SESSION = "capability-health"

STATES = (
    "CANDIDATE",
    "BUILDING",
    "VERIFIED",
    "HEALTHY",
    "DEGRADED",
    "BROKEN",
    "QUARANTINED",
    "RETIRED",
)

# Only these may be selected by auto-routing. Everything else must first be
# retested/repaired — routing a degraded tool silently is how bad output ships.
AUTO_ROUTABLE = frozenset({"HEALTHY", "VERIFIED"})

# Consecutive degradations tolerated before the tool is quarantined. The event
# *after* the threshold quarantines (three allowed, the fourth removes it).
QUARANTINE_AFTER = 3

# Hard failures: the tool's contract is provably not intact, no retest loop.
_HARD_TRIGGERS = frozenset(
    {
        "false_pass",
        "schema_mismatch",
        "api_change",
        "runtime_incompatible",
        "dependency_incompatible",
    }
)

TRIGGERS = (
    "wrong_output",
    "api_change",
    "timeout",
    "auth_error",
    "schema_mismatch",
    "dependency_incompatible",
    "false_pass",
    "runtime_incompatible",
)

KIND_VERIFICATION = "verification"
KIND_TRIGGER = "trigger"
KIND_RETEST = "retest"
KIND_RECHECK = "recheck"
KIND_RETIRE = "retire"
KIND_BUILDING = "building"
KIND_BUILD_FAILED = "build_failed"


def _path(runtime: str | Path) -> Path:
    return Path(runtime) / HEALTH_FILE


def _append(runtime: str | Path, row: dict) -> dict:
    path = _path(runtime)
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"ts": time.time(), **row}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    return row


def digest_dir(tool_dir: str | Path) -> str:
    """Content digest of a tool directory (code change → evidence invalid).

    ``.py`` files count, plus the container adapter spec
    (``adapter.json``, mirrors ``skaldir.toolbuilder.ADAPTER_FILE``) when the tool
    is a container capability: for such a tool the adapter IS the source — its
    argv template and pinned digest decide what runs. A manifest timestamp or a
    log artifact still must not invalidate verification.
    """
    directory = Path(tool_dir)
    digest = hashlib.sha256()
    if not directory.is_dir():
        return ""
    paths = [path for path in sorted(directory.rglob("*.py")) if "__pycache__" not in path.parts]
    adapter = directory / ADAPTER_FILE
    if adapter.is_file():
        # one well-known file at the tool root — no surprise, no rglob widening
        paths.append(adapter)
    for path in paths:
        digest.update(path.relative_to(directory).as_posix().encode())
        try:
            digest.update(path.read_bytes())
        except OSError:
            continue
    return digest.hexdigest()


def tool_dir(runtime: str | Path, name: str) -> Path:
    return Path(runtime) / "generated_tools" / str(name)


def events(runtime: str | Path, name: str = "") -> list[dict]:
    """All health events, oldest first, optionally for one tool."""
    path = _path(runtime)
    if not path.exists():
        return []
    rows: list[dict] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict) or not row.get("name"):
            continue
        if name and row.get("name") != name:
            continue
        rows.append(row)
    return rows


def _fold(rows: list[dict], name: str, *, digest: str) -> dict:
    """Derive the current state from the append-only ledger."""
    state = "CANDIDATE"
    reasons: list[str] = []
    consecutive = 0
    verified_at: float | None = None
    verified_digest = ""
    last_ts = 0.0
    for row in rows:
        if row.get("name") != name:
            continue
        kind = str(row.get("kind") or "")
        last_ts = float(row.get("ts") or last_ts)
        if kind == KIND_BUILDING:
            if state in ("CANDIDATE", "VERIFIED"):
                state = "BUILDING"
            reasons = [str(row.get("reason") or "build in progress")]
            continue
        if kind == KIND_VERIFICATION:
            if row.get("ok"):
                state = "HEALTHY"
                consecutive = 0
                verified_at = float(row.get("ts") or 0.0)
                verified_digest = str(row.get("digest") or "")
                reasons = ["verified: " + str(row.get("detail") or "checks green")]
            else:
                state = "BROKEN"
                reasons = [str(row.get("reason") or "verification failed")]
            continue
        if kind == KIND_RETEST:
            # evidence invalidated on purpose: needs a fresh verification run
            state = "VERIFIED"
            consecutive = 0
            verified_at = None
            verified_digest = digest
            reasons = ["retested — evidence reset, re-verification required"]
            continue
        if kind == KIND_RECHECK:
            state = str(row.get("state") or state)
            reasons = [str(row.get("reason") or "recheck")]
            if row.get("digest"):
                verified_digest = str(row["digest"])
            continue
        if kind == KIND_RETIRE:
            state = "RETIRED"
            reasons = [str(row.get("reason") or "retired by owner")]
            continue
        if kind == KIND_TRIGGER:
            if state == "RETIRED":
                # retirement is the owner's decision and terminal: later
                # observations stay in the ledger, but they never re-open the
                # tool ("retired is never routed", also under load)
                continue
            trigger = str(row.get("trigger") or "wrong_output")
            consecutive += 1
            detail = str(row.get("detail") or "")
            reasons.insert(0, f"{trigger}: {detail}" if detail else trigger)
            del reasons[4:]
            # quarantine wins over the hard-trigger classification: repeated
            # breakage of any kind ends in removal, not just a BROKEN label
            if consecutive > QUARANTINE_AFTER:
                state = "QUARANTINED"
            elif trigger in _HARD_TRIGGERS:
                state = "BROKEN"
            elif state not in ("QUARANTINED", "RETIRED"):
                state = "DEGRADED"
            continue
    # a stale code digest invalidates the verification evidence
    if state == "HEALTHY" and verified_digest and digest and verified_digest != digest:
        state = "DEGRADED"
        reasons.insert(0, "code changed since verification — evidence invalidated")
    return {
        "name": name,
        "state": state,
        "reasons": reasons,
        "consecutive_issues": consecutive,
        "verified_at": verified_at,
        "digest": digest,
        "updated": last_ts,
        "auto_routable": state in AUTO_ROUTABLE,
    }


def file_state(runtime: str | Path, name: str) -> dict:
    """Current health of one tool as a fresh reader would see it."""
    directory = tool_dir(runtime, name)
    return _fold(events(runtime, name), name, digest=digest_dir(directory))


def all_states(runtime: str | Path) -> dict[str, dict]:
    """Current health per tool, from the ledger plus the live registry dirs."""
    names = {str(row.get("name")) for row in events(runtime) if row.get("name")}
    base = Path(runtime) / "generated_tools"
    if base.is_dir():
        names.update(path.name for path in base.iterdir() if path.is_dir())
    return {name: file_state(runtime, name) for name in sorted(names)}


def record_building(runtime: str | Path, name: str, *, reason: str = "") -> dict:
    before = str(file_state(runtime, name)["state"])
    row = _append(runtime, {"name": name, "kind": KIND_BUILDING, "reason": reason})
    _emit_change(runtime, name, before, file_state(runtime, name))
    return row


def _emit_change(
    runtime: str | Path, name: str, before: str, after: dict, *, trigger: str = ""
) -> None:
    """Append one ``capability_health_changed`` event per transition.

    Never raises: an unregistered kind, a locked/corrupt store or an
    unwritable runtime must not kill the caller that just recorded a health
    fact (the ledger row is already durable at this point). Redacted on
    append — health details may quote real error text.
    """
    try:
        state = str(after.get("state") or "")
        if not state or state == before:
            return
        from .events import EventStore

        store = EventStore(Path(runtime) / "events.db")
        try:
            store.append_redacted(
                EVENT_SESSION,
                0,
                EVENT_KIND,
                {
                    "name": str(name),
                    "from": before,
                    "to": state,
                    "trigger": str(trigger or ""),
                    "reason": "; ".join(
                        str(item) for item in (after.get("reasons") or [])[:3]
                    )[:300],
                    "consecutive_issues": int(after.get("consecutive_issues") or 0),
                    "auto_routable": state in AUTO_ROUTABLE,
                },
            )
        finally:
            store.close()
    except Exception:
        return


def record_verification(runtime: str | Path, name: str, *, ok: bool, detail: str = "") -> str:
    """Record a real verification run. Returns the resulting state name."""
    before = str(file_state(runtime, name)["state"])
    directory = tool_dir(runtime, name)
    _append(
        runtime,
        {
            "name": name,
            "kind": KIND_VERIFICATION,
            "ok": bool(ok),
            "detail": detail[:300],
            "digest": digest_dir(directory),
            "reason": "" if ok else (detail[:200] or "verification failed"),
        },
    )
    after = file_state(runtime, name)
    _emit_change(runtime, name, before, after)
    return str(after["state"])


def record_trigger(runtime: str | Path, name: str, trigger: str, *, detail: str = "") -> dict:
    """Record an observed degradation trigger → state recomputed."""
    trigger = str(trigger or "wrong_output")
    if trigger not in TRIGGERS:
        trigger = "wrong_output"
    before = str(file_state(runtime, name)["state"])
    _append(
        runtime,
        {"name": name, "kind": KIND_TRIGGER, "trigger": trigger, "detail": detail[:300]},
    )
    after = file_state(runtime, name)
    _emit_change(runtime, name, before, after, trigger=trigger)
    return after


def recheck(runtime: str | Path, name: str) -> dict:
    """Re-read the live facts (digest) and record the verdict.

    This is the entry point a scheduler/panel calls: it answers "is the
    verification evidence still valid for the code that is on disk now?".
    """
    before = str(file_state(runtime, name)["state"])
    directory = tool_dir(runtime, name)
    current = digest_dir(directory)
    state = _fold(events(runtime, name), name, digest=current)["state"]
    reason = "digest unchanged" if state == "HEALTHY" else "evidence invalidated or issues open"
    _append(
        runtime,
        {"name": name, "kind": KIND_RECHECK, "state": state, "digest": current, "reason": reason},
    )
    after = file_state(runtime, name)
    _emit_change(runtime, name, before, after)
    return after


def retest(runtime: str | Path, name: str, *, reason: str = "") -> dict:
    """Take a degraded/quarantined tool back through verification (repair path)."""
    before = str(file_state(runtime, name)["state"])
    _append(runtime, {"name": name, "kind": KIND_RETEST, "reason": reason or "retest"})
    after = file_state(runtime, name)
    _emit_change(runtime, name, before, after)
    return after


def retire(runtime: str | Path, name: str, *, reason: str = "") -> dict:
    """Withdraw a tool from every surface, permanently."""
    before = str(file_state(runtime, name)["state"])
    _append(runtime, {"name": name, "kind": KIND_RETIRE, "reason": reason or "retired"})
    after = file_state(runtime, name)
    _emit_change(runtime, name, before, after)
    return after


def routable(runtime: str | Path, name: str) -> bool:
    return str(file_state(runtime, name)["state"]) in AUTO_ROUTABLE


def selectable(runtime: str | Path, name: str) -> tuple[bool, str]:
    """Selection gate: may this tool be picked automatically — and why (not)?

    A documented extension of :func:`routable`: same source of truth
    (``AUTO_ROUTABLE`` over the folded ledger), but it returns the verdict
    *with its reason* so a caller can say why a tool was skipped. The two
    exclusion states are explicit: ``QUARANTINED`` (too many consecutive
    degradations — retest required) and ``RETIRED`` (withdrawn by the owner)
    never come back through selection.
    """
    state = file_state(runtime, name)
    verdict = str(state.get("state") or "CANDIDATE")
    why = "; ".join(str(item) for item in (state.get("reasons") or [])[:2])
    suffix = f": {why}" if why else ""
    if verdict in AUTO_ROUTABLE:
        return True, f"{verdict} (auto-routable){suffix}"
    if verdict == "QUARANTINED":
        return False, f"QUARANTINED: excluded from selection, retest required{suffix}"
    if verdict == "RETIRED":
        return False, f"RETIRED: withdrawn by the owner, never selected{suffix}"
    return False, f"{verdict}: not selectable{suffix}"


# -- derive triggers from the real usage ledger -------------------------------

_ERROR_PATTERNS = (
    ("auth_error", re.compile(r"(?i)\b(401|403|auth|unauthor|forbidden|credential|permission)\b")),
    (
        "dependency_incompatible",
        re.compile(r"(?i)(modulenotfound|importerror|no module named|incompatib)"),
    ),
    ("schema_mismatch", re.compile(r"(?i)\b(schema|keyerror|missing key|validation error)\b")),
    ("api_change", re.compile(r"(?i)\b(404|410|not found|no such|deprecated|endpoint)\b")),
    (
        "runtime_incompatible",
        re.compile(r"(?i)(typeerror|attributeerror|unexpected keyword|runtime.?version)"),
    ),
    ("timeout", re.compile(r"(?i)\b(timeout|timed out|time out)\b")),
)


def classify_trigger(error_text: str) -> str:
    """Map a real failure string to one lifecycle trigger."""
    text = str(error_text or "")
    for name, pattern in _ERROR_PATTERNS:
        if pattern.search(text):
            return name
    return "wrong_output"


def _cursor(runtime: str | Path) -> dict:
    path = Path(runtime) / USAGE_CURSOR_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def observe_usage(runtime: str | Path) -> dict:
    """Derive degradation triggers from the real ``tool_usage.jsonl`` ledger.

    Idempotent: a high-water mark per tool means the same ledger line is
    classified once, however often this runs. This is the wiring that makes
    the lifecycle react to reality instead of to a synthetic call.
    """
    runtime = Path(runtime)
    usage = runtime / "tool_usage.jsonl"
    cursor = _cursor(runtime)
    seen: dict[str, float] = {}
    observed: list[dict] = []
    if usage.exists():
        for line in usage.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict) or row.get("ok"):
                continue
            name = str(row.get("name") or "")
            ts = float(row.get("ts") or 0.0)
            if not name or ts <= float(cursor.get(name) or 0.0):
                continue
            trigger = classify_trigger(str(row.get("error") or ""))
            state = record_trigger(
                runtime, name, trigger, detail=str(row.get("error") or "")[:200]
            )
            observed.append({"name": name, "trigger": trigger, "state": state["state"]})
            seen[name] = max(seen.get(name, 0.0), ts)
    if seen:
        cursor.update(seen)
        (runtime / USAGE_CURSOR_FILE).write_text(json.dumps(cursor), encoding="utf-8")
    return {"observed": observed, "count": len(observed)}
