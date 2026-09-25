"""Capability mining (Goal add-on): repeated workflows → tool candidates.

God's Voice watches for the *same technical capability* being needed again and
again — repeated research/API work, repeatedly rewritten scripts, recurring
smoke/E2E/verification flows — and turns it into a **candidate**:

    skill | cli | mcp | testing_tool

Candidates are only proposals. Promotion goes through the existing gated
tool-builder pipeline (no tool sprawl, no second architecture).
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .secretscan import redact

CANDIDATES_FILE = "capability_candidates.jsonl"

# Persistent cursor of the trace consumer (§1.5). Chosen location: the runtime
# dir, next to the ledgers it feeds (``capability_candidates.jsonl``,
# ``capability_health.jsonl``, ``tool_usage.jsonl``) and next to the
# ``events.db`` it reads. Plain JSON so an operator can inspect/rewind it by
# hand; written atomically (tmp + ``os.replace``) so a crash never leaves a
# torn high-water mark. ``capability_health`` already keeps its usage cursor
# the same way (``capability_health_cursor.json``) — one convention, not two.
TRACES_CURSOR_FILE = "traces_cursor.json"

# A run whose last completed step is older than this counts as finished even
# when no event closed it explicitly (same idle window ``traces_from_events``
# uses for splitting).
TRACES_SETTLE_SECONDS = 1800

_CURSOR_VERSION = 1
_CURSOR_MAX_RUNS = 5000

STATUS_CANDIDATE = "candidate"
STATUS_PROMOTED = "promoted"
STATUS_DISMISSED = "dismissed"

TYPE_SKILL = "skill"
TYPE_CLI = "cli"
TYPE_MCP = "mcp"
TYPE_TESTING = "testing_tool"

_TESTING_HINT = re.compile(
    r"(?i)\b(test(?:ing|s)?|verify|verification|smoke|e2e|end[- ]to[- ]end|"
    r"regression|check|assert|screenshot|restart|persistence)\b"
)
_API_HINT = re.compile(
    r"(?i)\b(api|http|rest|endpoint|provider|webhook|stocks?|shares?|market data|"
    r"prices?|weather|geocod|currency|exchange rate|fetch data)\b"
)
_SKILL_HINT = re.compile(
    r"(?i)\b(research|recherche|look ?up|find out|how to|procedure|workflow|"
    r"investigate|analy[sz]e|review|explain)\b"
)
_COMMAND_TESTING = re.compile(
    r"(?i)(pytest|check_|_check|gv_ui_e2e|smoke|e2e|verify|bandit|ruff)"
)
_SECRET_HINT = re.compile(r"secret://[A-Za-z0-9_./-]+")


def classify_capability(text: str, kind_hint: str = "") -> str:
    """Pick the smallest right tool type for a recurring capability."""
    kind = (kind_hint or "").strip().lower()
    if kind in ("mcp", "cli"):
        return kind
    if kind in ("testing", "testing_tool", "test"):
        return TYPE_TESTING
    if _TESTING_HINT.search(text):
        return TYPE_TESTING
    if _API_HINT.search(text):
        return TYPE_MCP
    if _SKILL_HINT.search(text):
        return TYPE_SKILL
    return TYPE_CLI


@dataclass
class Candidate:
    """One recurring capability, ready for a promotion decision."""

    capability: str
    occurrences: int = 1
    sessions: int = 1
    current_workaround: str = ""
    suggested_type: str = TYPE_CLI
    expected_benefit: str = ""
    required_secrets: list[str] = field(default_factory=list)
    first_seen: float = 0.0
    last_seen: float = 0.0
    status: str = STATUS_CANDIDATE
    id: str = ""
    # B6: the generalized flow and its parameters. Two literal runs collapse
    # into ONE candidate whose signature carries {placeholders}, never the
    # literals (A.blend / B.blend must not appear here).
    steps_signature: str = ""
    parameters: dict = field(default_factory=dict)
    evidence_sessions: list[str] = field(default_factory=list)
    # §1.5: the stable ids of the runs that produced this candidate
    # (``<session>:<first event seq>``). They make the merge idempotent: a
    # replayed/wiped cursor can re-read a run, but a run that is already
    # evidenced never inflates occurrences again.
    evidence_runs: list[str] = field(default_factory=list)


class CandidateStore:
    """Append-only JSONL; readers see the newest state per candidate id."""

    def __init__(self, runtime: str | Path) -> None:
        self.path = Path(runtime) / CANDIDATES_FILE

    def _append(self, row: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")

    def all(self) -> list[dict]:
        if not self.path.exists():
            return []
        latest: dict[str, dict] = {}
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = str(row.get("id") or "")
            if not key:
                continue
            if key in latest:
                merged = dict(latest[key])
                merged.update({k: v for k, v in row.items() if v not in (None, "")})
                merged["occurrences"] = max(
                    int(latest[key].get("occurrences") or 0),
                    int(row.get("occurrences") or 0),
                )
                latest[key] = merged
            else:
                latest[key] = row
        return list(latest.values())

    def by_id(self, candidate_id: str) -> dict | None:
        for row in self.all():
            if row.get("id") == candidate_id:
                return row
        return None

    def merge(self, candidate: Candidate) -> dict:
        """Add or update a candidate; occurrences/sessions accumulate.

        Trace candidates carry ``evidence_runs`` (stable run ids). When both
        sides have run ids, only *newly evidenced* runs count: a replay of an
        already-consumed run (or a wiped cursor that re-reads the store)
        returns the existing row unchanged instead of inflating occurrences.
        Candidates without run ids (requests/commands) keep the old
        accumulate semantics.
        """
        existing = None
        for row in self.all():
            if (
                row.get("capability") == candidate.capability
                and row.get("suggested_type") == candidate.suggested_type
                and row.get("status") == STATUS_CANDIDATE
            ):
                existing = row
                break
        if existing is not None:
            candidate.id = str(existing.get("id"))
            candidate.first_seen = float(existing.get("first_seen") or candidate.first_seen)
            known_runs = [str(item) for item in (existing.get("evidence_runs") or [])]
            incoming_runs = [str(item) for item in (candidate.evidence_runs or [])]
            if incoming_runs:
                new_runs = [item for item in incoming_runs if item not in known_runs]
                if not new_runs:
                    return existing
                candidate.evidence_runs = known_runs + new_runs
                candidate.occurrences = int(existing.get("occurrences") or 0) + len(new_runs)
                merged_sessions = list(
                    dict.fromkeys(
                        [str(item) for item in (existing.get("evidence_sessions") or [])]
                        + [str(item) for item in (candidate.evidence_sessions or [])]
                    )
                )
                candidate.evidence_sessions = merged_sessions
                candidate.sessions = max(1, len(merged_sessions))
            else:
                candidate.occurrences = (
                    int(existing.get("occurrences") or 0) + candidate.occurrences
                )
                candidate.sessions = int(existing.get("sessions") or 0) + candidate.sessions
        if not candidate.id:
            candidate.id = f"cand-{uuid.uuid4().hex[:10]}"
        self._append(asdict(candidate))
        return asdict(candidate)

    def set_status(self, candidate_id: str, status: str, **extra) -> dict:
        row = self.by_id(candidate_id)
        if row is None:
            raise ValueError(f"unknown candidate: {candidate_id}")
        updated = dict(row)
        updated["status"] = status
        updated.update(extra)
        self._append(updated)
        return updated


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def mine_from_requests(requests: list[dict], *, min_occurrences: int = 2) -> list[Candidate]:
    """Group repeated capability requests by capability key (: >= min occurrences)."""
    groups: dict[str, dict] = {}
    for row in requests:
        capability = str(row.get("capability") or "").strip()
        key = _normalize(capability)[:120]
        if not key:
            continue
        entry = groups.setdefault(
            key,
            {
                "capability": capability[:160],
                "occurrences": 0,
                "sessions": set(),
                "kinds": [],
                "reasons": [],
                "secrets": set(),
                "created": [],
            },
        )
        entry["occurrences"] += 1
        session = str(row.get("session_id") or "")
        if session:
            entry["sessions"].add(session)
        entry["kinds"].append(str(row.get("kind") or "auto"))
        reason = str(row.get("reason") or "")
        if reason:
            entry["reasons"].append(reason[:200])
        for secret in row.get("required_secrets") or []:
            if str(secret).startswith("secret://"):
                entry["secrets"].add(str(secret))
        entry["created"].append(float(row.get("created") or 0.0))
    candidates: list[Candidate] = []
    for key, entry in groups.items():
        if entry["occurrences"] < min_occurrences:
            continue
        kind_hint = next((k for k in entry["kinds"] if k in ("mcp", "cli")), "")
        reason_text = " ".join(entry["reasons"]) or key
        candidates.append(
            Candidate(
                capability=entry["capability"],
                occurrences=entry["occurrences"],
                sessions=max(1, len(entry["sessions"])),
                current_workaround=entry["reasons"][0] if entry["reasons"] else "",
                suggested_type=classify_capability(reason_text, kind_hint),
                expected_benefit=(
                    f"removes {entry['occurrences']}x manual re-doing of the same capability"
                ),
                required_secrets=sorted(entry["secrets"]),
                first_seen=min(entry["created"]) or time.time(),
                last_seen=max(entry["created"]) or time.time(),
            )
        )
    return candidates


def mine_from_commands(commands: list[dict], *, min_occurrences: int = 2) -> list[Candidate]:
    """Repeated shell commands across sessions → mostly testing-tool candidates."""
    groups: dict[str, dict] = {}
    for row in commands:
        raw = str(row.get("command") or "").strip()
        if not raw or len(raw) < 6:
            continue
        key = _normalize(re.sub(r"\s+", " ", raw))[:160]
        entry = groups.setdefault(key, {"occurrences": 0, "sessions": set(), "created": []})
        entry["occurrences"] += 1
        session = str(row.get("session_id") or "")
        if session:
            entry["sessions"].add(session)
        entry["created"].append(float(row.get("created") or 0.0))
    candidates: list[Candidate] = []
    for key, entry in groups.items():
        if entry["occurrences"] < min_occurrences:
            continue
        suggested = TYPE_TESTING if _COMMAND_TESTING.search(key) else TYPE_CLI
        candidates.append(
            Candidate(
                capability=f"recurring command: {key[:140]}",
                occurrences=entry["occurrences"],
                sessions=max(1, len(entry["sessions"])),
                current_workaround=key[:200],
                suggested_type=suggested,
                expected_benefit=(
                    "wrap the recurring flow once instead of re-running it by hand"
                ),
                first_seen=min(entry["created"]) or time.time(),
                last_seen=max(entry["created"]) or time.time(),
            )
        )
    return candidates


def mine(runtime: str | Path, *, min_occurrences: int = 2) -> dict:
    """Scan the real runtime artifacts and record new candidates."""
    runtime_dir = Path(runtime)
    requests: list[dict] = []
    requests_path = runtime_dir / "capability_requests.jsonl"
    if requests_path.exists():
        for line in requests_path.read_text(encoding="utf-8").splitlines():
            try:
                requests.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    commands: list[dict] = []
    events_path = runtime_dir / "events.db"
    if events_path.exists():
        try:
            from .events import EventStore

            store = EventStore(events_path)
            for event in store.stream():
                if event.kind != "tool_call":
                    continue
                payload = event.payload or {}
                args = payload.get("args")
                if not isinstance(args, dict):
                    continue
                command = args.get("command") or args.get("argv")
                if isinstance(command, list):
                    command = " ".join(str(part) for part in command)
                if isinstance(command, str) and command.strip():
                    commands.append(
                        {
                            "command": command,
                            "session_id": str(getattr(event, "session_id", "") or ""),
                            "created": float(getattr(event, "created", 0.0) or 0.0),
                        }
                    )
        except Exception:
            commands = []
    store = CandidateStore(runtime_dir)
    written = 0
    # B6: causal semantic generalization on top of the request/command paths —
    # repeated *successful runs* collapse into ONE parametric candidate.
    # §1.5: the consumer keeps a persistent cursor, so a second `tools mine`
    # (or a restart) never mines the same run twice — and a rewound cursor
    # cannot inflate a candidate either (stable evidence run ids).
    trace_report = consume_completed_traces(runtime_dir, min_occurrences=min_occurrences)
    repeated = mine_from_requests(requests, min_occurrences=min_occurrences) + mine_from_commands(
        commands, min_occurrences=min_occurrences
    )
    for candidate in repeated:
        if not candidate.id:
            candidate.id = f"cand-{uuid.uuid4().hex[:10]}"
        if not candidate.first_seen:
            candidate.first_seen = time.time()
        candidate.last_seen = candidate.last_seen or time.time()
        store.merge(candidate)
        written += 1
    return {
        "scanned_requests": len(requests),
        "scanned_commands": len(commands),
        "scanned_traces": int(trace_report["scanned"]),
        "new_traces": int(trace_report["new"]),
        "trace_candidates": int(trace_report["candidates"]),
        "redacted_arguments": int(trace_report["redacted"]),
        "candidates_recorded": written + int(trace_report["candidates"]),
    }


def promote(runtime: str | Path, candidate_id: str, *, session_id: str = "cli") -> dict:
    """Turn a candidate into a capability request (existing builder pipeline)."""
    from .capability import CapabilityRequest, CapabilityStore

    store = CandidateStore(runtime)
    row = store.by_id(candidate_id)
    if row is None:
        raise ValueError(f"unknown candidate: {candidate_id}")
    suggested = str(row.get("suggested_type") or TYPE_CLI)
    if suggested == TYPE_SKILL:
        raise ValueError(
            "skill candidates are promoted through the skill-mining flow, not as tools"
        )
    if suggested == TYPE_TESTING:
        # never silently treat a testing tool as a plain cli: the builder
        # enforces validation cases before registration (P1/B4)
        kind = TYPE_TESTING
    else:
        kind = TYPE_MCP if suggested == TYPE_MCP else "cli"
    request = CapabilityRequest(
        capability=str(row.get("capability") or ""),
        reason=(
            f"promoted from capability mining ({candidate_id}); "
            f"seen {row.get('occurrences')}x in {row.get('sessions')} session(s)"
        ),
        requested_by="godsv-mining",
        session_id=session_id,
        kind=kind,
        required_secrets=list(row.get("required_secrets") or []),
    )
    recorded = CapabilityStore(runtime).add(request)
    updated = store.set_status(
        candidate_id,
        STATUS_PROMOTED,
        request_id=recorded.request_id,
    )
    return {"candidate": updated, "request_id": recorded.request_id, "kind": kind}


def validate_testing_tool(run_case, cases: list[tuple[str, bool]]) -> dict:
    """A testing tool must fail on broken targets and pass on working ones.

    ``run_case(name) -> {"passed": bool, "detail": str}`` runs the actual tool.
    A tool that reports PASS on a deliberately broken target is *invalid* —
    it must never be trusted as completion evidence (§ testing-tool check).
    """
    checks = []
    valid = True
    for name, expect_pass in cases:
        try:
            result = run_case(name) or {}
            passed = bool(result.get("passed"))
            detail = str(result.get("detail") or "")
        except Exception as exc:  # a crashing tool is invalid, not a pass
            passed = False
            detail = f"{type(exc).__name__}: {exc}"
        agreement = passed == bool(expect_pass)
        if not agreement:
            valid = False
        checks.append(
            {
                "case": name,
                "expected_pass": bool(expect_pass),
                "actual_pass": passed,
                "ok": agreement,
                "detail": detail[:160],
            }
        )
    return {
        "status": "validated" if valid else "invalid",
        "checks": checks,
    }


# --------------------------------------------------------------------------- #
# (1) causal semantic generalization (TOOLS-30)
# --------------------------------------------------------------------------- #
# Mining that counts *equal command strings* cannot see the obvious: "blender
# A.blend … screenshot … inspect" and "blender B.blend … screenshot … inspect"
# are the SAME capability with a different input. The generalizer below reads
# whole successful runs (steps AND their results), fixes the invariant shape,
# and turns the varying argument tokens into named parameters — so two literal
# runs collapse into ONE candidate `inspect_blender_project(project_path)`.

# Varying tokens are named after what they are, not after the literal value.
_EXT_NOUNS = {
    ".blend": "project_path",
    ".blend1": "project_path",
    ".fcstd": "project_path",
    ".kra": "project_path",
    ".psd": "project_path",
    ".png": "image_path",
    ".jpg": "image_path",
    ".jpeg": "image_path",
    ".svg": "image_path",
    ".json": "data_path",
    ".jsonl": "data_path",
    ".csv": "data_path",
    ".yaml": "config_path",
    ".yml": "config_path",
    ".toml": "config_path",
    ".md": "document_path",
    ".txt": "document_path",
    ".log": "log_path",
    ".db": "database_path",
    ".sqlite": "database_path",
    ".sqlite3": "database_path",
}

_GENERIC_OPS = frozenset({"exec", "wait", "sleep", "run", "call", "open", "start", "shell"})


@dataclass
class TraceStep:
    """One step of an observed run: the tool, its normalized operation, args."""

    tool: str
    operation: str
    args: dict = field(default_factory=dict)
    ok: bool = True


@dataclass
class GeneralizedCapability:
    """One recurring run shape with its varying inputs as parameters."""

    name: str
    capability: str
    steps_signature: str
    parameters: dict = field(default_factory=dict)
    occurrences: int = 1
    sessions: int = 1
    evidence_sessions: list[str] = field(default_factory=list)
    suggested_type: str = TYPE_CLI
    synthesis_examples: list[str] = field(default_factory=list)


def _is_pathish(value: str) -> bool:
    text = str(value)
    if "/" in text or "\\" in text:
        return True
    return "." in text.rsplit("/", 1)[-1] and not text.replace(".", "").isdigit()


def _param_name(value, arg_key: str) -> str:
    """Name a varying input by what it is (extension/key), never by its value."""
    text = str(value)
    suffix = ""
    leaf = text.rsplit("/", 1)[-1]
    if "." in leaf:
        suffix = "." + leaf.rsplit(".", 1)[-1].lower()
    if suffix in _EXT_NOUNS:
        return _EXT_NOUNS[suffix]
    if _is_pathish(text):
        return "input_path"
    key = re.sub(r"[^a-z0-9_]+", "_", str(arg_key or "").lower()).strip("_")
    return key or "input"


def _value_type(value) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if _is_pathish(str(value)):
        return "path"
    return "string"


def _token_replace(tokens_a: list[str], tokens_b: list[str]) -> tuple[list[str], list[str]] | None:
    """Split two token lists into (constant pattern, varying tokens).

    ``["blender", "A.blend"]`` vs ``["blender", "B.blend"]`` → pattern
    ``["blender", "{project_path}"]`` and tokens ``["A.blend", "B.blend"]``.
    A pure insertion/deletion (different arity) is NOT the same shape → None.
    """
    import difflib

    matcher = difflib.SequenceMatcher(a=tokens_a, b=tokens_b, autojunk=False)
    pattern: list[str] = []
    varying: list[str] = []
    args: list[str] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            pattern.extend(tokens_a[i1:i2])
            continue
        if tag != "replace":
            return None
        args = []
        for left, right in zip(tokens_a[i1:i2], tokens_b[j1:j2], strict=False):
            if left == right:
                pattern.append(left)
                continue
            name = _param_name(left, args[-1] if args else "")
            pattern.append(f"{{{name}}}")
            varying.extend([left, right])
    return pattern, varying


def _boundary_pair(left: str, right: str) -> tuple[str, list[str]] | None:
    """Generalize two differing string values → (pattern, varying values).

    Word-level first (``blender A.blend``), then a shared path prefix
    (``/tmp/a.png`` vs ``/tmp/b.png`` → ``/tmp/{image_path}``).
    """
    tokens_a, tokens_b = str(left).split(), str(right).split()
    if tokens_a and len(tokens_a) == len(tokens_b):
        pair = _token_replace(tokens_a, tokens_b)
        if pair is not None:
            pattern, varying = pair
            return " ".join(pattern), varying
    return None


def _step_variance(key: str, values: list) -> tuple[str, dict | None]:
    """Render one arg position across the traces → (rendered fragment, param)."""
    first = values[0]
    if all(value == first for value in values):
        return f"{key}={first}", None
    if isinstance(first, str) and all(isinstance(value, str) for value in values):
        pair = _boundary_pair(first, values[1])
        if pair is not None:
            pattern, varying = pair
            name = _param_name(varying[0], key)
            return (
                f"{key}={pattern}",
                {
                    "name": name,
                    "type": _value_type(varying[0]),
                    "arg_key": key,
                    "values_sample": sorted({str(v) for v in varying})[:6],
                },
            )
        name = _param_name(first, key)
        return (
            f"{key}={{{name}}}",
            {
                "name": name,
                "type": _value_type(first),
                "arg_key": key,
                "values_sample": sorted({str(v) for v in values})[:6],
            },
        )
    numeric = isinstance(first, (int, float)) and not isinstance(first, bool)
    name = str(key or "value") if numeric else _param_name(first, key)
    return (
        f"{key}={{{name}}}",
        {
            "name": name,
            "type": _value_type(first),
            "arg_key": key,
            "values_sample": [str(v) for v in values][:6],
        },
    )


def generalize_traces(
    traces: list[tuple[str, list[TraceStep]]], *, min_occurrences: int = 2
) -> GeneralizedCapability | None:
    """Collapse repeated successful runs into ONE parametric capability.

    Returns the strongest generalization (most occurrences), or ``None`` when
    nothing repeats. Two runs of the same tool/operation sequence whose only
    differences are input values produce exactly one capability.
    """
    groups: dict[tuple, list[tuple[str, list[TraceStep]]]] = {}
    for session, steps in traces:
        usable = [step for step in steps if step.ok and step.tool]
        if len(usable) < 2:
            continue
        key = tuple((step.tool, step.operation) for step in usable)
        groups.setdefault(key, []).append((str(session or ""), usable))

    best: GeneralizedCapability | None = None
    for key, runs in groups.items():
        if len(runs) < max(2, min_occurrences):
            continue
        signature: list[str] = []
        parameters: dict[str, dict] = {}
        mergeable = True
        length = len(key)
        for index in range(length):
            fragments: list[str] = []
            for _session, steps in runs:
                args = steps[index].args or {}
                pieces = []
                for arg_key, _value in args.items():
                    values = [
                        (other_steps[index].args or {}).get(arg_key)
                        for _s, other_steps in runs
                    ]
                    if any(item is None for item in values):
                        pieces.append(f"{arg_key}=?")
                        continue
                    rendered, parameter = _step_variance(arg_key, values)
                    pieces.append(rendered)
                    if parameter:
                        slot = str(parameter["name"])
                        merged = parameters.setdefault(
                            slot,
                            {
                                "name": slot,
                                "type": parameter["type"],
                                "arg_keys": [],
                                "positions": [],
                                "values_sample": [],
                                "optional": False,
                            },
                        )
                        if parameter["arg_key"] not in merged["arg_keys"]:
                            merged["arg_keys"].append(parameter["arg_key"])
                        if index not in merged["positions"]:
                            merged["positions"].append(index)
                        for sample in parameter["values_sample"]:
                            if sample not in merged["values_sample"]:
                                merged["values_sample"].append(sample)
                        if parameter["type"] != merged["type"] and merged["type"] != "path":
                            merged["type"] = "path" if _is_pathish(
                                str(parameter["values_sample"][0])
                            ) else merged["type"]
                fragments.append(" ".join(pieces))
            variant = fragments[0] if fragments else ""
            if any(fragment != variant for fragment in fragments[1:]):
                # the same position renders differently across runs → not one shape
                mergeable = False
                break
            signature.append(f"{key[index][0]}:{key[index][1]}({variant})")
        if not mergeable or not parameters:
            continue
        sessions = sorted({session for session, _steps in runs if session})
        name = _capability_name(key, parameters, _signature_constants(signature))
        capability = f"{name.replace('_', ' ')}: " + " -> ".join(signature)
        # the derivation examples: what the run actually looked like. A holdout
        # validation must use DIFFERENT inputs (holdout_ok enforces this).
        primary = next(iter(parameters.values()))
        arg_key = str(primary["arg_keys"][0]) if primary["arg_keys"] else ""
        position = int(primary["positions"][0]) if primary["positions"] else 0
        synthesis = [
            str((steps[position].args or {}).get(arg_key))
            for _session, steps in runs
            if position < len(steps)
        ]
        candidate = GeneralizedCapability(
            name=name,
            capability=capability[:200],
            steps_signature="|".join(signature)[:400],
            parameters=parameters,
            occurrences=len(runs),
            sessions=max(1, len(sessions) or 1),
            evidence_sessions=sessions,
            suggested_type=classify_capability(capability),
            synthesis_examples=[item for item in synthesis if item],
        )
        if best is None or candidate.occurrences > best.occurrences:
            best = candidate
    return best


def _signature_constants(signature: list[str]) -> list[str]:
    """The literal tokens of a signature — the stable idiom of the flow.

    ``terminal:exec(command=blender {project_path})`` → ``["terminal",
    "exec", "blender"]``. Arg-key prefixes are dropped so ``command=`` does not
    become a naming input; the *value* noun (``blender``) does.
    """
    blob = " ".join(signature)
    blob = re.sub(r"\{[a-z0-9_]+\}", " ", blob)
    blob = re.sub(r"\b[a-z_][a-z0-9_]*=", " ", blob)
    return [token.lower() for token in re.findall(r"[A-Za-z][A-Za-z0-9_.-]{2,}", blob)]


def _capability_name(
    key: tuple, parameters: dict, constants: list[str] | None = None
) -> str:
    """A stable verb_subject_object name — e.g. ``inspect_blender_project``.

    The subject comes from the *invariant* part of the flow (the noun that
    repeats across runs: ``blender``), the action from the last non-generic
    operation, and the object from the primary parameter. Transport names
    (``terminal``, ``computer_use``) describe how a step ran, not what the
    capability IS — so they are the fallback, never the first choice.
    """
    operations = [operation for _tool, operation in key if operation not in _GENERIC_OPS]
    action = (operations[-1] if operations else key[-1][1]) or "run"
    stops = {
        "exec",
        "wait",
        "screenshot",
        "inspect",
        "run",
        "check",
        "and",
        "the",
        "true",
        "false",
    }
    tool_names = {str(tool).lower() for tool, _op in key}
    nouns = [
        token
        for token in (constants or [])
        if token not in stops and token not in tool_names and len(token) > 2 and not token.isdigit()
    ]
    subject = nouns[0] if nouns else ""
    if not subject:
        subject = next(
            (tool for tool, operation in key if operation not in _GENERIC_OPS),
            key[0][0],
        )
    primary = ""
    for parameter in parameters.values():
        primary = str(parameter["name"])
        break
    noun = primary.replace("_path", "").replace("_", " ")
    parts = [action, subject, noun]
    slug = re.sub(r"[^a-z0-9]+", "_", "_".join(parts).lower()).strip("_")
    return (slug or "capability")[:48]


def mine_from_traces(
    traces: list[tuple[str, list[TraceStep]]],
    *,
    min_occurrences: int = 2,
    run_ids: list[str] | None = None,
) -> list[Candidate]:
    """Mining entry point: repeated successful runs → parametric candidates.

    ``run_ids`` are the stable ids of the runs in ``traces`` (same order) —
    they travel into ``Candidate.evidence_runs`` so the store can dedupe on
    replay.
    """
    generalized = generalize_traces(traces, min_occurrences=min_occurrences)
    if generalized is None:
        return []
    return [
        Candidate(
            capability=generalized.capability,
            occurrences=generalized.occurrences,
            sessions=generalized.sessions,
            current_workaround=(
                "the same run was performed by hand "
                f"{generalized.occurrences}x with different inputs"
            ),
            suggested_type=generalized.suggested_type,
            expected_benefit=(
                f"one call with parameters replaces {generalized.occurrences} "
                "hand-repeated runs"
            ),
            steps_signature=generalized.steps_signature,
            parameters=generalized.parameters,
            evidence_sessions=generalized.evidence_sessions,
            evidence_runs=[str(item) for item in (run_ids or []) if str(item)],
        )
    ]


def traces_from_events(
    runtime: str | Path, *, limit: int = 400
) -> list[tuple[str, list[TraceStep]]]:
    """Read real successful runs out of the event store.

    A run is a maximal sequence of *successful* tool calls in one session —
    results are part of the reading, so a step that failed splits the run
    (causality, not command counting). Long idle gaps split runs too.
    """
    events_path = Path(runtime) / "events.db"
    if not events_path.exists():
        return []
    traces: list[tuple[str, list[TraceStep]]] = []
    try:
        from .events import EventStore

        store = EventStore(events_path)
        try:
            events = store.stream()
        finally:
            store.close()
    except Exception:
        return []
    session = ""
    run: list[TraceStep] = []
    last_ts = 0.0
    pending: dict[str, TraceStep] = {}

    def flush() -> None:
        if len(run) >= 2:
            traces.append((session, list(run)))

    for event in events[-limit:]:
        if event.kind not in ("tool_call", "tool_result"):
            # Foreign kinds (thoughts, gate verdicts, health changes) are not
            # part of a run: they must not reset the session state below. A
            # `capability_health_changed` event of the pseudo session
            # "capability-health" used to split every run it landed between.
            continue
        ts = float(getattr(event, "ts", 0.0) or 0.0)
        if ts and last_ts and ts - last_ts > 1800:
            flush()
            run = []
        last_ts = ts or last_ts
        current = str(getattr(event, "session_id", "") or "")
        if current != session:
            flush()
            run = []
            pending = {}
            session = current
        payload = getattr(event, "payload", {}) or {}
        if event.kind == "tool_call":
            name = str(payload.get("name") or payload.get("tool") or "")
            if not name:
                continue
            args = payload.get("args")
            args = args if isinstance(args, dict) else {}
            step = TraceStep(
                tool=name,
                operation=_operation_of(name, args),
                args={k: v for k, v in args.items() if _traceable(v)},
            )
            call_id = str(payload.get("call_id") or payload.get("id") or "")
            if call_id:
                pending[call_id] = step
            run.append(step)
        elif event.kind == "tool_result":
            call_id = str(payload.get("call_id") or payload.get("id") or "")
            status = str(payload.get("status") or "")
            failed = status in ("error", "failed") or bool(payload.get("error"))
            targeted = pending.get(call_id) if call_id else (run[-1] if run else None)
            if failed and targeted is not None:
                # a failed step ends the run: the flow after it is a different flow
                if targeted in run:
                    run.remove(targeted)
                flush()
                run = []
    flush()
    return traces


# --------------------------------------------------------------------------- #
# (1b) consuming completed executions exactly once (§1.5)
# --------------------------------------------------------------------------- #


def _cursor_path(runtime: str | Path) -> Path:
    return Path(runtime) / TRACES_CURSOR_FILE


def load_traces_cursor(runtime: str | Path) -> dict:
    """Read the trace cursor from disk (an empty cursor when unreadable/corrupt)."""
    empty = {"version": _CURSOR_VERSION, "processed_seq": 0, "consumed_runs": {}}
    try:
        data = json.loads(_cursor_path(runtime).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty
    if not isinstance(data, dict):
        return empty
    runs = data.get("consumed_runs")
    return {
        "version": int(data.get("version") or _CURSOR_VERSION),
        "processed_seq": int(data.get("processed_seq") or 0),
        "consumed_runs": runs if isinstance(runs, dict) else {},
    }


def save_traces_cursor(runtime: str | Path, cursor: dict) -> Path:
    """Persist the cursor atomically (tmp file + :func:`os.replace`).

    A torn cursor is worse than none: it could skip a run (a lost candidate).
    The rename makes the update all-or-nothing, and the file is plain JSON so
    an operator can inspect or rewind it by hand.
    """
    path = _cursor_path(runtime)
    path.parent.mkdir(parents=True, exist_ok=True)
    runs = {str(key): value for key, value in dict(cursor.get("consumed_runs") or {}).items()}
    if len(runs) > _CURSOR_MAX_RUNS:
        # bounded file: keep the newest runs (the candidate store still holds
        # the full evidence-run list, so eviction cannot cause double counts)
        ordered = sorted(runs.items(), key=lambda item: int((item[1] or {}).get("last_seq") or 0))
        runs = dict(ordered[-_CURSOR_MAX_RUNS:])
    payload = {
        "version": _CURSOR_VERSION,
        "processed_seq": int(cursor.get("processed_seq") or 0),
        "consumed_runs": runs,
        "updated": time.time(),
    }
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)
    return path


def _result_failed(payload: dict) -> bool:
    """Did this tool result report a failure? (never an implicit pass)

    Sources: an explicit ``error`` field, ``status`` error/failed, a non-zero
    ``exit_code`` (top level — the CLI/demo shape — or inside the JSON string
    the wrappers store under ``result``).
    """
    payload = payload or {}
    if payload.get("error"):
        return True
    if str(payload.get("status") or "") in ("error", "failed"):
        return True
    exit_code = payload.get("exit_code")
    if isinstance(exit_code, int) and not isinstance(exit_code, bool) and exit_code != 0:
        return True
    inner = payload.get("result")
    if isinstance(inner, str) and inner.strip().startswith("{"):
        try:
            inner = json.loads(inner)
        except json.JSONDecodeError:
            inner = None
    if isinstance(inner, dict):
        if inner.get("error"):
            return True
        code = inner.get("exit_code")
        if isinstance(code, int) and not isinstance(code, bool) and code != 0:
            return True
    return False


def _redact_step(step: TraceStep) -> tuple[TraceStep, int]:
    """Second-pass redaction before anything durable is written (§1.5).

    ``EventStore`` scans on append, but the mining path must not *depend* on
    every writer having done so (rows from older versions, foreign writers,
    restored backups, new patterns). A capability is durable — a secret inside
    it would be durable, too.
    """
    found = 0
    clean: dict = {}
    for key, value in (step.args or {}).items():
        if isinstance(value, str):
            text, findings = redact(value)
            found += len(findings)
            clean[str(key)] = text
        else:
            clean[str(key)] = value
    if not found:
        return step, 0
    return TraceStep(tool=step.tool, operation=step.operation, args=clean, ok=step.ok), found


def _completed_runs(events, *, gap_seconds: int = TRACES_SETTLE_SECONDS) -> list[dict]:
    """Maximal sequences of *completed, successful* executions — with stable ids.

    Pairing follows the ``completion.run_evidence_from_events`` idea: a call
    counts only when its result is present. Both sides pair through the payload
    call id when they carry one — the live store carries **no** ids at all, so
    the fallback is the oldest unmatched call of the same tool in the same
    session (FIFO, each call consumed once); a result without a tool name (the
    demo fixtures) falls back to the oldest unmatched call of its session.

    A failed result ends the run and is not part of it (the flow after a
    failure is a different flow). A call whose result never arrived is not
    completed: it is never mined and does **not** split the run — a logging gap
    must not fabricate a boundary.

    Every run carries its stable id ``<session>:<first event seq>`` (``seq`` is
    the store's AUTOINCREMENT — stable across restarts and replays) plus the
    seq range it covers.
    """
    ordered = [
        event for event in events if getattr(event, "kind", "") in ("tool_call", "tool_result")
    ]
    # pass 1 — pair results to calls
    by_id: dict[str, int] = {}
    queue_tool: dict[tuple[str, str], list[int]] = {}
    queue_session: dict[str, list[int]] = {}
    result_of: dict[int, dict] = {}
    for event in ordered:
        payload = getattr(event, "payload", None) or {}
        session = str(getattr(event, "session_id", "") or "")
        call_id = str(
            payload.get("call_id") or payload.get("tool_call_id") or payload.get("id") or ""
        )
        if event.kind == "tool_call":
            tool = str(payload.get("name") or payload.get("tool") or "")
            queue_tool.setdefault((session, tool), []).append(event.seq)
            queue_session.setdefault(session, []).append(event.seq)
            if call_id:
                by_id[call_id] = event.seq
            continue
        target = by_id.pop(call_id, None) if call_id else None
        if target is None:
            tool = str(payload.get("tool") or payload.get("name") or "")
            queue = queue_tool.get((session, tool)) if tool else None
            while queue and queue[0] in result_of:
                queue.pop(0)
            if queue:
                target = queue.pop(0)
        if target is None:
            fallback = queue_session.get(session)
            while fallback and fallback[0] in result_of:
                fallback.pop(0)
            if fallback:
                target = fallback.pop(0)
        if target is not None:
            result_of[target] = payload

    # pass 2 — build the runs
    runs: list[dict] = []
    current: dict | None = None

    def close() -> None:
        nonlocal current
        if current is not None and len(current["steps"]) >= 2:
            runs.append(current)
        current = None

    for event in ordered:
        if event.kind != "tool_call":
            continue  # the call carries the step; the result only decides failure
        result = result_of.get(event.seq)
        if result is not None and _result_failed(result):
            close()
            continue
        if result is None:
            continue  # not completed: never mined, never a boundary
        payload = event.payload or {}
        session = str(getattr(event, "session_id", "") or "")
        ts = float(getattr(event, "ts", 0.0) or 0.0)
        if current is not None and (
            session != current["session"]
            or (ts and current["last_ts"] and ts - current["last_ts"] > gap_seconds)
        ):
            close()
        if current is None:
            current = {
                "session": session,
                "first_seq": event.seq,
                "last_seq": event.seq,
                "last_ts": ts,
                "steps": [],
            }
        tool = str(payload.get("name") or payload.get("tool") or "")
        args = payload.get("args")
        args = args if isinstance(args, dict) else {}
        current["steps"].append(
            TraceStep(
                tool=tool,
                operation=_operation_of(tool, args),
                args={key: value for key, value in args.items() if _traceable(value)},
            )
        )
        current["last_seq"] = event.seq
        if ts:
            current["last_ts"] = ts
    close()
    for run in runs:
        run["id"] = f"{run['session']}:{run['first_seq']}"
    return runs


def consume_completed_traces(
    runtime: str | Path,
    *,
    limit: int = 2000,
    min_occurrences: int = 2,
    apply: bool = True,
    settle_seconds: int = TRACES_SETTLE_SECONDS,
    now: float | None = None,
) -> dict:
    """Mine every completed run out of ``events.db`` — exactly once per run.

    Reads only *completed* executions (call + paired result, see
    :func:`_completed_runs`), redacts sensitive arguments, dedupes over stable
    run ids and feeds :func:`mine_from_traces` into the ``CandidateStore``.
    ``limit`` bounds how many recent events one pass reads; the runs inside the
    window are re-mined as a whole, because generalization needs the
    *repeated* runs — what is new is then counted through
    ``Candidate.evidence_runs``.

    Replay is idempotent twice over:
      1. the persistent cursor (``traces_cursor.json``, atomic tmp +
         ``os.replace``) remembers the consumed runs, so a second pass reports
         ``new == 0``;
      2. even after a full cursor reset the candidate keeps its evidence run
         ids and :meth:`CandidateStore.merge` adds nothing twice.

    A run is consumable when it is closed (session change, failure, idle gap)
    or settled (its last step is older than ``settle_seconds``); an open tail
    waits for the next pass so it cannot be mined half-finished.
    ``apply=False`` answers the dry-run question ("what would the step find?")
    without writing anything — no cursor update, no candidate row.

    Returns ``{scanned, new, candidates, redacted, processed_seq, cursor,
    runs}``: runs examined, runs newly consumed, candidate rows that gained
    evidence, redacted secrets, cursor state.
    """
    runtime_dir = Path(runtime)
    clock = float(now if now is not None else time.time())
    cursor = load_traces_cursor(runtime_dir)
    processed = int(cursor["processed_seq"])
    consumed: dict = dict(cursor["consumed_runs"])
    report: dict = {
        "scanned": 0,
        "new": 0,
        "candidates": 0,
        "redacted": 0,
        "cursor": str(_cursor_path(runtime_dir)),
        "processed_seq": processed,
        "runs": [],
    }
    events_path = runtime_dir / "events.db"
    if not events_path.exists():
        return report
    try:
        from .events import EventStore

        store = EventStore(events_path)
        try:
            events = store.stream()
        finally:
            store.close()
    except Exception:
        return report
    if not events:
        return report
    if processed and int(events[-1].seq) <= processed:
        return report  # nothing new since the last pass — cheap no-op replay
    if limit and len(events) > limit:
        events = events[-limit:]
    runs = _completed_runs(events)
    report["scanned"] = len(runs)
    window: list[tuple[str, list[TraceStep]]] = []
    window_ids: list[str] = []
    fresh: list[tuple[str, list[TraceStep]]] = []
    fresh_ids: list[str] = []
    fresh_by_id: dict[str, dict] = {}
    advance_to = processed
    redacted = 0
    for index, run in enumerate(runs):
        closed = index < len(runs) - 1
        settled = (clock - float(run["last_ts"] or 0.0)) > float(settle_seconds)
        if not (closed or settled):
            break  # the tail may still grow — the next pass decides
        advance_to = max(advance_to, int(run["last_seq"]))
        steps: list[TraceStep] = []
        for step in run["steps"]:
            clean, found = _redact_step(step)
            redacted += found
            steps.append(clean)
        # the whole consumable window is the mining input: generalization
        # needs the *repeated* runs, and a run consumed in an earlier pass is
        # still part of the repetition. What is new is counted by merge
        # through the evidence run ids, never by occurrences alone.
        window.append((run["session"], steps))
        window_ids.append(run["id"])
        if run["id"] in consumed:
            continue
        fresh.append((run["session"], steps))
        fresh_ids.append(run["id"])
        fresh_by_id[run["id"]] = run
    report["new"] = len(fresh)
    report["redacted"] = redacted
    report["processed_seq"] = advance_to

    def summary() -> list[dict]:
        return [
            {
                "id": run["id"],
                "session": run["session"],
                "steps": len(run["steps"]),
                "first_seq": int(run["first_seq"]),
                "last_seq": int(run["last_seq"]),
                "consumed": run["id"] in consumed,
            }
            for run in runs
        ]

    if not fresh:
        report["runs"] = summary()
        if apply and advance_to > processed:
            cursor["processed_seq"] = advance_to
            cursor["consumed_runs"] = consumed
            save_traces_cursor(runtime_dir, cursor)
        return report
    candidates = mine_from_traces(window, min_occurrences=min_occurrences, run_ids=window_ids)
    store = CandidateStore(runtime_dir)
    known = {
        (str(row.get("capability")), str(row.get("suggested_type"))): set(
            row.get("evidence_runs") or []
        )
        for row in store.all()
        if row.get("status") == STATUS_CANDIDATE
    }
    if apply:
        written = 0
        for candidate in candidates:
            row = store.merge(candidate)
            key = (str(row.get("capability")), str(row.get("suggested_type")))
            if set(row.get("evidence_runs") or []) - known.get(key, set()):
                written += 1
        report["candidates"] = written
        cursor["processed_seq"] = advance_to
        for run_id in fresh_ids:
            run = fresh_by_id[run_id]
            consumed[run_id] = {
                "session": run["session"],
                "first_seq": int(run["first_seq"]),
                "last_seq": int(run["last_seq"]),
                "steps": len(run["steps"]),
            }
        cursor["consumed_runs"] = consumed
        save_traces_cursor(runtime_dir, cursor)
    else:
        report["candidates"] = sum(
            1
            for candidate in candidates
            if set(candidate.evidence_runs or [])
            - known.get((str(candidate.capability), str(candidate.suggested_type)), set())
        )
    report["runs"] = summary()
    return report


def _traceable(value) -> bool:
    """Only small scalar inputs take part in the shape; payloads do not."""
    if isinstance(value, (str, int, float, bool)):
        return len(str(value)) <= 120
    return False


def _operation_of(tool: str, args: dict) -> str:
    """Normalized operation of a step — a verb, not the raw command line."""
    for key in ("operation", "op", "action", "subcommand", "mode"):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return re.sub(r"[^a-z0-9_]+", "_", value.strip().lower())[:32]
    command = args.get("command") or args.get("argv")
    if isinstance(command, list):
        command = " ".join(str(part) for part in command)
    if isinstance(command, str) and command.strip():
        tokens = [
            token
            for token in re.split(r"[\s|;&]+", command.strip())
            if token and not token.startswith("-")
        ]
        if tokens:
            verb = tokens[0].rsplit("/", 1)[-1]
            wrappers = ("timeout", "time", "env", "nice", "bash", "sh", "python", "python3")
            if verb in wrappers and len(tokens) > 1:
                verb = tokens[1].rsplit("/", 1)[-1]
            if verb in ("python", "python3") and len(tokens) > 2:
                verb = "python_" + tokens[2].rsplit("/", 1)[-1].removesuffix(".py")
            return re.sub(r"[^a-z0-9_]+", "_", verb.lower())[:32]
    return re.sub(r"[^a-z0-9_]+", "_", str(tool or "").lower())[:32]


# --------------------------------------------------------------------------- #
# (2) search-before-build (TOOLS-27/35)
# --------------------------------------------------------------------------- #

# The surfaces an existing solution can live in. Reported back, never implied.
SEARCH_SURFACES = (
    "generated_tools",
    "testing_tools",
    "tool_hub",
    "skills",
    "mcp",
    "workflows",
)

_REUSE_MIN_OVERLAP = 2

# The builder refuses harder than the advisory search: refusing a build on a
# weak 2-term overlap would block legitimate new work, so a *refusal* needs a
# strong match (a real duplicate), while a report is offered from 2.
_BUILD_REFUSE_OVERLAP = 3


def _workflow_entries() -> list[dict]:
    try:
        from .workflows import workflow_contracts

        return [
            {
                "name": str(item.get("name") or ""),
                "kind": "workflow",
                "surface": "workflows",
                "purpose": " ".join(
                    [str(item.get("inputs") or ""), str(item.get("success") or "")]
                ),
            }
            for item in workflow_contracts()
        ]
    except Exception:
        return []


def _hub_entries(runtime: str | Path, skills_dir=None, config_path=None) -> list[dict]:
    try:
        from .toolhub import discover

        return [
            {
                "name": str(entry.get("name") or ""),
                "kind": str(entry.get("type") or entry.get("source") or "tool"),
                "surface": "skills" if entry.get("source") == "skill" else "tool_hub",
                "purpose": str(entry.get("purpose") or ""),
                "path": str(entry.get("path") or ""),
                "source": str(entry.get("source") or ""),
            }
            for entry in discover(runtime, skills_dir=skills_dir, config_path=config_path)
        ]
    except Exception:
        return []


def _registry_entries(runtime: str | Path) -> list[dict]:
    from .capability import load_tool_entries

    entries: list[dict] = []
    for entry in load_tool_entries(runtime):
        manifest = _load_manifest(Path(entry["path"]) / "manifest.yaml")
        surface = (
            "testing_tools" if entry.get("type") == "testing_tool" else "generated_tools"
        )
        entries.append(
            {
                "name": str(entry["name"]),
                "kind": str(entry.get("type") or "cli"),
                "surface": surface,
                "purpose": str(manifest.get("purpose") or manifest.get("created_for") or ""),
                "status": str(entry.get("status") or ""),
                "path": str(entry.get("path") or ""),
                "source": "generated",
            }
        )
    return entries


def _load_manifest(path: Path) -> dict:
    try:
        import yaml

        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


# Capability vocabulary that appears in almost every tool description. Sharing
# "inspect"/"screenshot"/"verify" is NOT evidence of a duplicate — only a shared
# *distinctive* noun ("blender", "postgres") is.
_BOILERPLATE = frozenset(
    {
        "about",
        "after",
        "before",
        "capability",
        "check",
        "inspect",
        "project",
        "screenshot",
        "state",
        "tool",
        "using",
        "verify",
        "with",
    }
)


def _match_score(haystack: str, wanted: set[str], min_overlap: int) -> int:
    """Relevance of one entry, or 0 for "not the same capability".

    Two conditions, both required:
      1. a shared *distinctive* term (a subject noun, not filler verbs) — this
         is what stops every "inspect … and verify" tool from matching every
         other one;
      2. at least ``min_overlap`` shared terms overall — so a single generic
         noun is a hint, not a verdict.
    """
    matched = {term for term in wanted if term in haystack}
    if not (matched - _BOILERPLATE):
        return 0
    if len(matched) < min_overlap:
        return 0
    return len(matched)


def search_existing(
    runtime: str | Path,
    capability: str,
    *,
    skills_dir=None,
    config_path=None,
    limit: int = 5,
    min_overlap: int = _REUSE_MIN_OVERLAP,
) -> dict:
    """Search every existing place a capability could already live.

    Returns the candidates and the surfaces that were actually searched, so a
    "no duplicate" verdict is inspectable instead of implied.
    """
    from .research import _terms

    wanted = set(_terms(capability, limit=12))
    entries = (
        _registry_entries(runtime)
        + _hub_entries(runtime, skills_dir, config_path)
        + _workflow_entries()
    )
    seen: set[tuple[str, str]] = set()
    scored: list[tuple[int, dict]] = []
    for entry in entries:
        key = (entry["surface"], entry["name"])
        if not entry["name"] or key in seen:
            continue
        seen.add(key)
        haystack = " ".join(
            [entry["name"].replace("_", " "), entry.get("purpose", ""), entry.get("kind", "")]
        ).lower()
        score = _match_score(haystack, wanted, min_overlap)
        if score:
            scored.append((score, entry))
    scored.sort(key=lambda pair: (-pair[0], pair[1]["name"]))
    return {
        "query": capability,
        "surfaces": sorted(set(SEARCH_SURFACES)),
        "matches": [entry for _score, entry in scored[:limit]],
    }


def search_before_build(
    runtime: str | Path,
    capability: str,
    *,
    name: str = "",
    skills_dir=None,
    config_path=None,
    min_overlap: int = _REUSE_MIN_OVERLAP,
) -> dict:
    """Decide REUSE vs BUILD before anything is generated.

    ``decision == "reuse"`` means an existing tool/skill/MCP/workflow already
    covers the capability: build nothing (and say what to use instead). A match
    with the REQUESTED name is skipped — rebuilding an existing name is the
    deliberate repair path, not a duplicate.
    """
    search = search_existing(
        runtime,
        capability,
        skills_dir=skills_dir,
        config_path=config_path,
        min_overlap=min_overlap,
    )
    match = search["matches"][0] if search["matches"] else None
    if match is not None and name and str(match["name"]) == str(name):
        match = search["matches"][1] if len(search["matches"]) > 1 else None
    return {
        "decision": "reuse" if match else "build",
        "match": match,
        "searched": search["surfaces"],
        "candidates": search["matches"],
    }


# --------------------------------------------------------------------------- #
# (3) holdout validation (TOOLS-30/§40)
# --------------------------------------------------------------------------- #


def holdout_ok(synthesis_examples: list[str], validation_examples: list[str]) -> bool:
    """Validation examples must exist and must NOT be the derivation examples.

    A capability proven only on the examples it was mined from has proven
    nothing (that is memorization, not generalization).
    """
    synthesis = {str(item).strip() for item in synthesis_examples or [] if str(item).strip()}
    validation = {str(item).strip() for item in validation_examples or [] if str(item).strip()}
    if not validation or not synthesis:
        return False
    return not (synthesis & validation)


def validation_is_holdout(
    cases: list[tuple[object, bool]], synthesis_examples: list[str] | None = None
) -> tuple[bool, str]:
    """Is a validation case set a real holdout? Returns (ok, reason).

    Requirements, in the order they fail:
      1. at least two distinct targets (a repeated target proves nothing),
      2. both outcomes present (a working target AND a broken target),
      3. no target is one of the examples the capability was derived from.
    """
    targets = [str(target).strip() for target, _expect in cases or []]
    if len(targets) < 2:
        return False, "at least two distinct targets are required"
    if len(set(targets)) != len(targets):
        return False, "a holdout may not repeat the same target"
    expectations = {bool(expect) for _target, expect in cases}
    if expectations != {True, False}:
        return False, "both a working (PASS) and a broken (FAIL) target are required"
    if synthesis_examples:
        synthesis = {
            str(item).strip().lower() for item in synthesis_examples if str(item).strip()
        }
        overlap = synthesis & {target.lower() for target in targets}
        if overlap:
            return False, (
                "targets overlap the synthesis examples: " + ", ".join(sorted(overlap))[:120]
            )
    return True, f"{len(targets)} held-out targets, both outcomes covered"


def _case_result(result) -> tuple[bool, str]:
    """Normalize a runner outcome: dict {'passed': bool} | object.ok | bool."""
    if isinstance(result, dict):
        return bool(result.get("passed")), str(result.get("detail") or "")
    ok = getattr(result, "ok", None)
    if ok is not None:
        detail = str(getattr(result, "detail", "") or "")
        return bool(ok), detail
    if isinstance(result, bool):
        return result, ""
    if result is None:
        return False, "no result (runner produced no evidence)"
    return bool(result), ""


def validate_testing_tool_holdout(run_case, cases: list[tuple[object, bool]]) -> dict:
    """Run a testing tool on HELD-OUT targets: working → PASS, broken → FAIL.

    Same discipline as :func:`validate_testing_tool`, but the targets come from
    outside the synthesis material — and a crash or an always-PASS tool is
    invalid, never evidence.
    """
    checks: list[dict] = []
    valid = True
    for target, expect_pass in cases:
        try:
            passed, detail = _case_result(run_case(target))
        except Exception as exc:  # a crashing tool is invalid, not a pass
            passed, detail = False, f"{type(exc).__name__}: {exc}"
        agreement = passed == bool(expect_pass)
        if not agreement:
            valid = False
        checks.append(
            {
                "case": str(target),
                "expected_pass": bool(expect_pass),
                "actual_pass": passed,
                "ok": agreement,
                "detail": detail[:160],
            }
        )
    return {"status": "validated" if valid else "invalid", "checks": checks}


def run_general_tool_holdout(
    runtime: str | Path,
    name: str,
    *,
    inputs: list[dict],
    expected_keys: set[str] | None = None,
    project_id: str = "",
) -> dict:
    """Call a general (non-testing) tool on inputs it was never derived from.

    Uses the real registry → MCP wrapper call path (the same callable the host
    invokes), in-process. ``status == "validated"`` requires every run to
    return a structured result carrying the expected keys.
    """
    from .mcpserver import _wrapper_for, approved_python_specs
    from .tools import ToolStore

    runtime = Path(runtime)
    store = ToolStore(runtime / "tools.db")
    try:
        specs = {
            spec.name: spec
            for spec in approved_python_specs(
                store,
                runtime=runtime,
                project_id=str(project_id or ""),
                include_legacy=True,
            )
        }
    finally:
        store.close()
    spec = specs.get(str(name))
    if spec is None:
        return {
            "status": "invalid",
            "checks": [],
            "error": f"unknown or unapproved tool: {name}",
        }
    call = _wrapper_for(spec, base_dir=runtime)
    checks: list[dict] = []
    valid = True
    for arguments in inputs:
        try:
            output = call(dict(arguments))
        except Exception as exc:
            output = {"error": f"{type(exc).__name__}: {exc}"}
        failed = isinstance(output, dict) and set(output) == {"error"}
        missing = sorted(set(expected_keys or ()) - set(output)) if isinstance(output, dict) else []
        ok = (not failed) and not missing
        if not ok:
            valid = False
        checks.append(
            {
                "input": dict(arguments),
                "output": output,
                "ok": ok,
                "detail": str(output.get("error", "")) if isinstance(output, dict) else "",
                "missing_keys": missing,
            }
        )
    return {"status": "validated" if valid else "invalid", "checks": checks, "tool": str(name)}


def route_candidates(runtime: str | Path, capability: str, *, limit: int = 5) -> list[dict]:
    """Auto-routing surface: only healthy/verified tools, ranked by relevance.

    A degraded, broken, quarantined or retired tool never appears here — it has
    to be retested or repaired first (TOOLS-32).
    """
    from . import capability_health as health
    from .research import _terms

    wanted = set(_terms(capability, limit=12))
    scored: list[tuple[int, dict]] = []
    for entry in _registry_entries(runtime):
        state = health.file_state(runtime, entry["name"])
        if not state.get("auto_routable"):
            continue
        haystack = " ".join(
            [entry["name"].replace("_", " "), entry.get("purpose", "")]
        ).lower()
        overlap = sum(1 for term in wanted if term in haystack) if wanted else 0
        if overlap:
            scored.append((overlap, {**entry, "health": state["state"]}))
    scored.sort(key=lambda pair: (-pair[0], pair[1]["name"]))
    return [entry for _score, entry in scored[:limit]]


# --------------------------------------------------------------------------- #
# (§4c) discovery metadata — the searchable surface of one capability
# --------------------------------------------------------------------------- #
#
# The discovery index searches *contract-backed* facts, never a tool's own
# marketing: every field below is traced to the manifest/spec that the gated
# builder wrote (``sources`` names it per field). A tool therefore cannot raise
# its own priority by claiming universal competence — rich wording that no
# validation backs stays out of the ranking (see ``discovery.py``, ranking
# rules) and contributes only to lexical recall.

# Index format version of the metadata contract. Bump when the shape changes:
# a cached search result is only valid for one (index, registry) pair.
DISCOVERY_METADATA_VERSION = 1

# The discovery fields (§4c). Order is the documented order of the contract.
DISCOVERY_FIELDS = (
    "effect",  # gewünschte Wirkung
    "tasks",  # typische Aufgaben
    "synonyms",
    "phrases_de",  # deutsche Formulierungen
    "phrases_en",  # englische Formulierungen
    "inputs",  # Eingabetypen
    "outputs",  # Ausgabetypen
    "preconditions",  # Vorbedingungen
    "non_use_cases",  # Nicht-Einsatzfälle
    "examples",
    "platforms",
    "requires_services",  # erforderliche Dienste
)

# Preconditions the contract states structurally (deps/secrets/network/fs).
# They are derived, not invented: an absent field yields no precondition.
def _string_list(value) -> list[str]:
    """A list of non-empty strings from any scalar/sequence shape (or [])."""
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]


def _spec_facts(runtime: str | Path) -> dict:
    """ToolSpec facts per name (approved contract) — read-only, never fatal.

    The spec is the second half of the contract (the manifest is the first):
    ``description``/``tags``/``sample_input`` come from ``tools.db``. A store
    that cannot be read simply contributes nothing.
    """
    try:
        from .tools import ToolStore

        store = ToolStore(Path(runtime) / "tools.db")
        try:
            return {
                str(spec.name): {
                    "title": str(getattr(spec, "title", "") or ""),
                    "description": str(getattr(spec, "description", "") or ""),
                    "tags": _string_list(getattr(spec, "tags", [])),
                    "sample_input": dict(getattr(spec, "sample_input", {}) or {}),
                    "rationale": str(getattr(spec, "rationale", "") or ""),
                    "status": str(getattr(spec, "status", "") or ""),
                }
                for spec in store.all()
            }
        finally:
            store.close()
    except Exception:
        return {}


def _json_type(value) -> str:
    """JSON type name for one sample value (schemas are derived, not guessed)."""
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "string"


def _typed_contract(manifest: dict) -> tuple[dict | None, dict | None, str]:
    """Read the JSON Schemas the approved descriptor declares, or say why there are none.

    The manifest names the descriptor (``contract: <path>``) and its own note says the
    typed contract lives there. A missing/unreadable descriptor yields ``(None, None, "")``
    — never a guessed schema. ``capability_mining`` must not invent argument shapes.
    """
    import json as _json

    raw = manifest.get("contract")
    if not raw:
        return None, None, ""
    path = Path(str(raw)).expanduser()
    if not path.is_file():
        return None, None, ""
    try:
        document = _json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, None, ""
    if not isinstance(document, dict):
        return None, None, ""
    source = f"contract.descriptor:{path.name}"
    input_schema = document.get("input_schema")
    output_schema = document.get("output_schema")
    return (
        input_schema if isinstance(input_schema, dict) else None,
        output_schema if isinstance(output_schema, dict) else None,
        source,
    )


def _contract_preconditions(manifest: dict) -> list[str]:
    """Structural preconditions from the approved contract (no inventions)."""
    out: list[str] = []
    for dependency in _string_list(manifest.get("dependencies")):
        out.append(f"dependency:{dependency}")
    for handle in _string_list(manifest.get("required_secrets")):
        # handles only — a secret value never enters the index
        out.append(f"requires:{handle}")
    network = manifest.get("network")
    if isinstance(network, dict):
        for domain in _string_list(network.get("allowed_domains")):
            out.append(f"network:{domain}")
        if str(network.get("allowed_domains") or "") == "all":
            out.append("network:any")
    filesystem = manifest.get("filesystem")
    if isinstance(filesystem, dict):
        access = str(filesystem.get("access") or "").strip()
        if access:
            out.append(f"filesystem:{access}")
    return out


def contract_metadata(name: str, manifest: dict, spec: dict | None = None) -> dict:
    """One capability's discovery metadata, traced to its contract.

    Every field carries its source in ``sources``. Optional ``discovery:``
    blocks (Manifest v2) are consumed when present; without them the metadata
    is what the approved contract already states (purpose/created_for, tags,
    sample_input, dependencies …). Nothing is invented for a missing field.
    """
    spec = spec or {}
    manifest = manifest or {}
    block = manifest.get("discovery")
    block = block if isinstance(block, dict) else {}

    def opt(*keys):
        for key in keys:
            if key in block:
                return block[key], f"manifest.discovery.{key}"
            if key in manifest:
                return manifest[key], f"manifest.{key}"
        return None, ""

    effect_raw, effect_src = opt("effect")
    effect = str(effect_raw or "").strip()
    if not effect:
        for key in ("purpose", "created_for", "description", "title"):
            text = str(manifest.get(key) or spec.get(key) or "").strip()
            if text:
                effect, effect_src = text, f"contract.{key}"
                break

    sources: dict = {}
    if effect_src:
        sources["effect"] = effect_src
    payload: dict = {
        "name": str(name),
        "version": str(manifest.get("version") or ""),
        "type": str(manifest.get("type") or ""),
        "status": str(manifest.get("status") or spec.get("status") or ""),
        "scope": str(manifest.get("scope") or "").strip().lower(),
        "project_id": str(manifest.get("project_id") or ""),
        "effect": effect,
        "tasks": [],
        "synonyms": [],
        "phrases_de": [],
        "phrases_en": [],
        "inputs": [],
        "outputs": [],
        "preconditions": [],
        "non_use_cases": [],
        "examples": [],
        "platforms": [],
        "requires_services": [],
        "tags": _string_list(spec.get("tags")),
        "description": str(spec.get("description") or manifest.get("purpose") or ""),
        "validation": "",
        "sources": sources,
    }

    optional = (
        "tasks",
        "synonyms",
        "phrases_de",
        "phrases_en",
        "inputs",
        "outputs",
        "non_use_cases",
        "examples",
        "platforms",
        "requires_services",
    )
    for name in optional:
        value, source = opt(name)
        values = _string_list(value)
        if values:
            payload[name] = values
            sources[name] = source
    # EN/DE split for untyped phrase lists: ``phrases`` fills both channels so
    # a German or English formulation finds the same capability.
    if not payload["phrases_de"] and not payload["phrases_en"]:
        value, source = opt("phrases")
        values = _string_list(value)
        if values:
            payload["phrases_de"] = list(values)
            payload["phrases_en"] = list(values)
            sources["phrases_de"] = sources["phrases_en"] = source

    preconditions = _contract_preconditions(manifest)
    declared, declared_src = opt("preconditions")
    preconditions.extend(_string_list(declared))
    if preconditions:
        payload["preconditions"] = preconditions
        sources["preconditions"] = (
            f"{declared_src}+contract" if declared else "contract.dependencies/secrets/network"
        )

    sample = spec.get("sample_input") or {}
    if isinstance(sample, dict) and sample and not payload["inputs"]:
        payload["inputs"] = [f"{key}:{_json_type(value)}" for key, value in sorted(sample.items())]
        sources["inputs"] = "contract.sample_input"

    # TOOL-02: discovery must hand the model a *call contract*, not just a name.
    # `inputs: ["path:string"]` is a label; the typed contract is the descriptor the
    # manifest points at (`contract: <path>`), which carries the real JSON Schema.
    # Without this, `tool_search` told the model a capability existed and left it to
    # guess the arguments — the exact P0 this project started from.
    input_schema, output_schema, schema_src = _typed_contract(manifest)
    if input_schema is not None:
        payload["input_schema"] = input_schema
        sources["input_schema"] = schema_src
    if output_schema is not None:
        payload["output_schema"] = output_schema
        sources["output_schema"] = schema_src

    validation = manifest.get("validation")
    if isinstance(validation, dict):
        payload["validation"] = str(validation.get("status") or "").strip().lower()
    else:
        payload["validation"] = str(validation or "").strip().lower()
    if payload["validation"]:
        sources["validation"] = "manifest.validation.status"
    return payload


def capability_metadata(
    runtime: str | Path, *, include_legacy: bool = True
) -> list[dict]:
    """Discovery metadata for every registered capability (contract facts).

    Reads the runtime's ``generated_tools`` manifests (the approved contract)
    plus the registry spec row for each name. ``include_legacy`` mirrors
    ``capability.visible_tools``: the default here is the *documented* view —
    callers that need the strict scope view pass ``False`` (or filter on the
    ``scope`` field themselves, as the discovery scope gate does).
    """
    from .capability import load_tool_entries

    specs = _spec_facts(runtime)
    out: list[dict] = []
    for entry in load_tool_entries(runtime):
        scope = str(entry.get("scope") or "")
        if scope == "legacy" and not include_legacy:
            continue
        manifest = _load_manifest(Path(entry["path"]) / "manifest.yaml")
        payload = contract_metadata(str(entry["name"]), manifest, specs.get(str(entry["name"])))
        if not payload.get("scope"):
            payload["scope"] = scope
            payload["sources"]["scope"] = "registry (scope-less legacy tool)"
        payload["path"] = str(entry.get("path") or "")
        payload["version"] = str(payload.get("version") or entry.get("version_raw") or "")
        payload["surface"] = (
            "testing_tools" if str(entry.get("type") or "") == "testing_tool" else "generated_tools"
        )
        sources = payload.setdefault("sources", {})
        sources.setdefault("effect", "contract.purpose")
        sources["name"] = "registry.name"
        out.append(payload)
    return out


def metadata_haystack(meta: dict) -> str:
    """The searchable text of one metadata record (all documented fields)."""
    parts = [str(meta.get("name") or "").replace("_", " ")]
    parts.append(str(meta.get("effect") or ""))
    parts.append(str(meta.get("description") or ""))
    for name in DISCOVERY_FIELDS:
        value = meta.get(name)
        if isinstance(value, (list, tuple)):
            parts.extend(str(item) for item in value)
        elif value:
            parts.append(str(value))
    parts.extend(str(tag) for tag in (meta.get("tags") or []))
    return " ".join(part for part in parts if part).strip()

