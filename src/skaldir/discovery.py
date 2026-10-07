"""§4c capability discovery in the Hermes execution path.

One question, one call: *"I need X; I can pass these inputs; these are my
constraints — which verified capability fits, why, what are its limits, and is
it usable right now?"*

Contract (frozen acceptance testset semantics, ``tests/fixtures/discovery_cases.json``):

- **Eligibility is decided BEFORE ranking — in two SEPARATE gates**: scope/project
  access and authorization. A tool excluded by either gate leaves no trace: not
  as a result, not as a name in any error message (aggregate counters only).
- **Ranking is evidence-backed**: contract-backed task fit, covered criteria,
  validation, health, input readiness, reliability, expected cost (startup
  included). A missing measurement is ``UNKNOWN`` — never silently good.
  Lexical/semantic similarity is *relevance*, not a success probability.
- **An unvalidated capability is never an executable recommendation**: it
  surfaces only in the separately marked ``build_candidates`` list.
- **A stopped but verified tool stays findable** with state ``startable``. The
  container START itself arrives with the Docker stage (B6): :func:`ensure_ready`
  is the documented hook, not a second runtime.

States (the five the task names, plus the build candidate):

    ready · startable · temporarily_down · missing_prerequisite · locked

Index and registry revision are both reported with every result, so a cached
answer can always be pinned to the facts it was derived from.
"""

from __future__ import annotations

import hashlib
import math
import platform as _platform
import re
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DISCOVERY_INDEX_VERSION = 1

STATE_READY = "ready"
STATE_STARTABLE = "startable"
STATE_DOWN = "temporarily_down"
STATE_MISSING = "missing_prerequisite"
STATE_LOCKED = "locked"
STATE_BUILD_CANDIDATE = "build_candidate"

# Health-ledger states that block selection entirely (fail-closed).
_LOCKED_HEALTH = frozenset({"QUARANTINED", "RETIRED"})
# Observed failure states. CANDIDATE/BUILDING are NOT failure states: a tool
# the ledger has merely seen being built has no execution evidence yet — it
# belongs in the build-candidate list, never in "temporarily down".
_DOWN_HEALTH = frozenset({"DEGRADED", "BROKEN"})

# Evidence-backed ranking weights. A component without a measurement is
# dropped from the sum AND its weight re-normalised away (``unknown`` lists it),
# so "not measured" can never read as "measured good/bad".
RANK_WEIGHTS: dict[str, float] = {
    "task_fit": 1.0,
    "criteria_coverage": 0.6,
    "validation": 0.5,
    "health": 0.5,
    "input_readiness": 0.4,
    "reliability": 0.3,
    "cost": 0.2,
}

# Per-field lexical weights of the metadata contract (``capability_mining``).
# A self-written description carries the smallest weight on purpose: volume of
# prose must not buy priority.
_FIELD_WEIGHTS: dict[str, float] = {
    "name": 3.0,
    "tasks": 2.0,
    "synonyms": 2.0,
    "phrases_de": 2.0,
    "phrases_en": 2.0,
    "effect": 1.5,
    "tags": 1.5,
    "inputs": 1.5,
    "outputs": 1.2,
    "examples": 1.0,
    "preconditions": 0.8,
    "description": 0.7,
}

# Fields whose hit makes a match *canonical* (the contract's own wording). A
# single hit in a merely structural field (an input type, an example) is not
# enough to recommend a capability.
_CANONICAL_FIELDS = ("name", "tasks", "synonyms", "phrases_de", "phrases_en", "effect")

# Words that carry no capability signal (DE + EN). Deliberately small: a stop
# word list is a noise filter, not a semantic model.
_STOPWORDS = frozenset(
    """
    der die das den dem des ein eine einen einem eines derer und oder aber fuer für
    mit von zu zum zur aus bei nach ueber über unter vor auf im in ist sind war
    wird werden kann koennen können soll sollen muss muessen müssen nicht kein
    keine dieser diese dieses jener jene bitte mir mich dir dich sich ich du er
    sie es wir ihr man mal doch auch noch nur schon sehr mehr als wie was wer wo
    wann warum welche welcher welches einen dein meine
    the a an and or but with from into onto for to of in on at by is are was were
    be been being can could should must not no non this that these those please
    me my you your it its we our they their i do does did do want need needs
    something that which what who when why how
    """.split()
)


# --------------------------------------------------------------------------- #
# tokenisation
# --------------------------------------------------------------------------- #


def normalize(text: str) -> str:
    """Casefolded, umlaut-folded, punctuation-free text (DE + EN search form).

    ``Währungsumrechnung`` and ``waehrungsumrechnung`` must meet the same
    capability; so must ``Umwandlung`` and ``umwandlung``.
    """
    value = str(text or "").casefold()
    for source, target in (
        ("ä", "ae"),
        ("ö", "oe"),
        ("ü", "ue"),
        ("ß", "ss"),
        ("é", "e"),
        ("è", "e"),
        ("à", "a"),
    ):
        value = value.replace(source, target)
    out = []
    for char in value:
        out.append(char if (char.isalnum() or char in ".:/-_") else " ")
    return " ".join("".join(out).split())


def tokens(text: str, *, keep_stopwords: bool = False) -> list[str]:
    """Search tokens of a text: alphanumeric words (≥ 2 chars) minus stop words.

    ``docx-datei`` splits into ``docx`` + ``datei``; typed values keep both
    halves (``source_path:string`` to ``source_path`` + ``string``).
    """
    words = re.findall(r"[a-z0-9]+", normalize(text))
    return [
        word
        for word in words
        if len(word) >= 2 and (keep_stopwords or word not in _STOPWORDS)
    ]


# Light DE/EN stemmer. Not semantics, not an embedding: it folds inflection
# and the separable-verb prefix so "Wandle … um", "umwandeln" and "Umwandlung"
# meet the same capability. Deliberately tiny and predictable.
_SUFFIXES = (
    "ungen", "ung", "lich", "isch", "en", "ern", "em", "es", "er", "e", "n",
    "s", "ing", "ed", "ly",
)
_PREFIXES = (
    "um", "aus", "ein", "ab", "zu", "an", "auf", "vor", "los", "rueck",
    "zurueck", "her", "hin", "durch", "ueber", "unter", "ver",
)


def stem(word: str) -> str:
    """Fold one normalised word to its rough DE/EN stem (stable, idempotent)."""
    candidate = str(word or "")
    for _ in range(2):
        changed = False
        if len(candidate) >= 7:
            for prefix in _PREFIXES:
                if candidate.startswith(prefix) and len(candidate) - len(prefix) >= 4:
                    candidate = candidate[len(prefix) :]
                    changed = True
                    break
        for suffix in _SUFFIXES:
            if candidate.endswith(suffix) and len(candidate) - len(suffix) >= 4:
                candidate = candidate[: -len(suffix)]
                changed = True
                break
        if not changed:
            break
    return candidate


def _token_set(text: str, *, keep_stopwords: bool = False) -> set[str]:
    return set(tokens(text, keep_stopwords=keep_stopwords))


def _stem_set(text: str) -> set[str]:
    return {stem(word) for word in tokens(text, keep_stopwords=True)}


def _pair_match(left: str, right: str) -> bool:
    """Do two stems denote the same word? Equal, or one a ≥5-char prefix of the other.

    The prefix rule catches stemmer leftovers ("recherch" vs "recherchier")
    without turning the search into fuzzy guessing.
    """
    if not left or not right:
        return False
    if left == right:
        return True
    shorter, longer = (left, right) if len(left) <= len(right) else (right, left)
    return len(shorter) >= 5 and longer.startswith(shorter)


def _stem_hits(need_stems: Mapping[str, str], haystack_stems: set[str]) -> list[str]:
    """The need words whose stems appear in the haystack (original wording)."""
    hits = {
        word
        for st, word in need_stems.items()
        if any(_pair_match(st, other) for other in haystack_stems)
    }
    return sorted(hits)


# --------------------------------------------------------------------------- #
# registry revision
# --------------------------------------------------------------------------- #


def _published_events(runtime: str | Path) -> dict:
    """Consume ``tool_published`` (§4d/B7) defensively — absent is fine.

    B7 publishes in the same SQLite transaction as the registry write; the
    event kind only counts once it is registered in ``events.KINDS``. Nothing
    here raises: an unreadable store must not break discovery.
    """
    try:
        from . import events as events_module

        kinds = getattr(events_module, "KINDS", frozenset())
        if "tool_published" not in kinds:
            return {"supported": False, "count": 0, "last_seq": 0}
        store = events_module.EventStore(Path(runtime) / "events.db")
        try:
            rows = [event for event in store.stream() if event.kind == "tool_published"]
        finally:
            store.close()
        return {
            "supported": True,
            "count": len(rows),
            "last_seq": rows[-1].seq if rows else 0,
        }
    except Exception:
        return {"supported": False, "count": 0, "last_seq": 0}


def registry_revision(runtime: str | Path, *, project_id: str = "") -> dict:
    """Stable digest of the registry facts a search result depends on.

    Covers the ``tools.db`` file and every manifest (name/mtime/size), so a new
    or changed capability moves the revision — that is what invalidates a
    cached (possibly negative) answer. ``tool_published`` events are consumed
    when the kind exists (B7 outbox); otherwise the manifest facts stand alone.

    OPERATOR view: ``parts`` names the registry entries (that is the point of a
    revision you can audit). ``discover()`` embeds only the digest, so a search
    answer can never name a scope-/authorization-excluded tool through it.
    """
    runtime = Path(runtime)
    parts: list[str] = [f"index={DISCOVERY_INDEX_VERSION}", f"project={project_id or ''}"]
    db = runtime / "tools.db"
    try:
        if db.is_file():
            stat = db.stat()
            parts.append(f"db={stat.st_size}:{stat.st_mtime_ns}")
    except OSError:
        parts.append("db=unreadable")
    base = runtime / "generated_tools"
    try:
        if base.is_dir():
            for tool_dir in sorted(path for path in base.iterdir() if path.is_dir()):
                manifest = tool_dir / "manifest.yaml"
                if manifest.is_file():
                    stat = manifest.stat()
                    parts.append(f"m={tool_dir.name}:{stat.st_mtime_ns}:{stat.st_size}")
                else:
                    parts.append(f"m={tool_dir.name}:nomanifest")
    except OSError:
        parts.append("generated_tools=unreadable")
    published = _published_events(runtime)
    if published["supported"]:
        parts.append(f"published={published['count']}:{published['last_seq']}")
    digest = hashlib.sha1(
        "\n".join(parts).encode("utf-8"), usedforsecurity=False
    ).hexdigest()
    return {
        "revision": digest[:16],
        "index_version": DISCOVERY_INDEX_VERSION,
        "parts": parts,
        "published_events": published,
    }


# --------------------------------------------------------------------------- #
# cache
# --------------------------------------------------------------------------- #


def cache_key(
    need: str,
    *,
    project_id: str = "",
    revision: str = "",
    permissions: Iterable[str] | None = None,
) -> str:
    """Cache key = normalised need + project + registry revision + permissions.

    A new need, a moved registry revision or a changed permission set is a
    different key — a cached negative answer can therefore never shadow a new
    situation (that is the invalidation contract, not an optimisation detail).
    """
    permission_key = ",".join(sorted(str(item) for item in (permissions or ())))
    material = "\n".join(
        [normalize(need), str(project_id or ""), str(revision or ""), permission_key]
    )
    return hashlib.sha1(material.encode("utf-8"), usedforsecurity=False).hexdigest()[:20]


@dataclass
class DiscoveryCache:
    """Small bounded in-process cache; negative results included, by design.

    Entries carry the registry revision they were computed for. ``invalidate``
    is called by the triggers (new revision, project/permission change); the key
    already separates needs, so an old *negative* entry cannot hide a new
    demand even before the invalidation runs.
    """

    ttl_seconds: float = 300.0
    max_entries: int = 64
    _entries: dict = field(default_factory=dict)
    _stats: dict = field(
        default_factory=lambda: {"hit": 0, "miss": 0, "stored": 0, "invalidated": 0}
    )

    def lookup(self, key: str) -> dict | None:
        row = self._entries.get(key)
        if row is None:
            self._stats["miss"] += 1
            return None
        if self.ttl_seconds and (time.monotonic() - row["created"]) > self.ttl_seconds:
            self._entries.pop(key, None)
            self._stats["miss"] += 1
            return None
        self._stats["hit"] += 1
        return row["value"]

    def store(self, key: str, value: dict) -> None:
        self._entries[key] = {"value": value, "created": time.monotonic()}
        self._stats["stored"] += 1
        while len(self._entries) > max(1, int(self.max_entries)):
            oldest = min(self._entries, key=lambda item: self._entries[item]["created"])
            self._entries.pop(oldest, None)

    def invalidate(self, reason: str = "", *, project_id: str = "") -> int:
        """Drop cached answers. Returns the number of dropped entries."""
        if not reason and not project_id:
            dropped = len(self._entries)
            self._entries.clear()
            self._stats["invalidated"] += dropped
            return dropped
        dropped = 0
        for key, row in list(self._entries.items()):
            value = row.get("value") or {}
            if project_id and str(value.get("project_id") or "") != str(project_id):
                continue
            self._entries.pop(key, None)
            dropped += 1
        self._stats["invalidated"] += dropped
        return dropped

    def stats(self) -> dict:
        return {**self._stats, "entries": len(self._entries)}


# --------------------------------------------------------------------------- #
# metadata index
# --------------------------------------------------------------------------- #


@dataclass
class CapabilityEntry:
    """One searchable capability: contract metadata + derived search fields."""

    meta: dict
    fields: dict = field(default_factory=dict)  # field → token set

    @property
    def name(self) -> str:
        return str(self.meta.get("name") or "")

    @property
    def scope(self) -> str:
        return str(self.meta.get("scope") or "legacy")

    @property
    def project_id(self) -> str:
        return str(self.meta.get("project_id") or "")

    def text(self, field: str) -> str:
        value = self.meta.get(field)
        if isinstance(value, (list, tuple)):
            return " ".join(str(item) for item in value)
        return str(value or "")

    def tokens_for(self, field: str) -> set[str]:
        key = f"t:{field}"
        if key not in self.fields:
            self.fields[key] = _token_set(self.text(field), keep_stopwords=True)
        return self.fields[key]

    def stem_tokens_for(self, field: str) -> set[str]:
        key = f"s:{field}"
        if key not in self.fields:
            self.fields[key] = _stem_set(self.text(field))
        return self.fields[key]


def build_index(entries: Sequence[Mapping[str, Any]]) -> list[CapabilityEntry]:
    """Index contract metadata records (as ``capability_mining`` extracts them)."""
    out: list[CapabilityEntry] = []
    for meta in entries:
        payload = dict(meta)
        payload.setdefault("name", "")
        payload["search_text"] = " ".join(
            [str(payload.get("name") or "").replace("_", " "), str(payload.get("effect") or "")]
        )
        out.append(CapabilityEntry(meta=payload))
    return out


def metadata_index(
    runtime: str | Path, *, include_legacy: bool = True
) -> list[CapabilityEntry]:
    """The contract index of one runtime (manifest + spec facts, read-only).

    The GUI sub-tools of the same runtime home (``act_subtools/*.json`` plus the
    stored built-ins) are indexed next to the registry tools — SKILL-03: a GUI
    flow saved as a sub-tool is discovered by the same search the model already
    uses. An unreadable sub-tool store contributes nothing (read-only, never fatal).
    """
    from .capability_mining import capability_metadata

    entries = build_index(capability_metadata(runtime, include_legacy=include_legacy))
    try:
        from .act_service import subtool_metadata

        entries.extend(build_index(subtool_metadata(state=runtime)))
    except Exception:  # noqa: BLE001 — the sub-tool surface is additive
        pass
    return entries


# --------------------------------------------------------------------------- #
# eligibility (BEFORE ranking — two separate gates)
# --------------------------------------------------------------------------- #


@dataclass
class EligibilityContext:
    """The caller's situation: who asks, for which project, with what rights."""

    project_id: str = ""
    permissions: frozenset = frozenset()
    permissions_known: bool = False
    inputs: tuple = ()
    constraints: tuple = ()
    budget: float | None = None
    services: Mapping[str, str] = field(default_factory=dict)  # name → running|stopped|missing
    platform: str = ""
    include_legacy: bool = False

    @classmethod
    def make(
        cls,
        *,
        project_id: str = "",
        permissions: Iterable[str] | None = None,
        inputs: Iterable[str] = (),
        constraints: Iterable[str] = (),
        budget: float | None = None,
        services: Mapping[str, str] | None = None,
        platform: str = "",
        include_legacy: bool = False,
    ) -> EligibilityContext:
        return cls(
            project_id=str(project_id or ""),
            permissions=frozenset(str(item) for item in (permissions or ())),
            permissions_known=permissions is not None,
            inputs=tuple(str(item) for item in (inputs or ())),
            constraints=tuple(str(item) for item in (constraints or ())),
            budget=budget,
            services=dict(services or {}),
            platform=str(platform or ""),
            include_legacy=include_legacy,
        )


def scope_gate(entry: CapabilityEntry, ctx: EligibilityContext) -> bool:
    """Gate 1 (separate): may this project context see the capability at all?

    ``global`` always; ``project`` only in its own project; ``legacy`` (no
    scope recorded) only in the explicit include-legacy view. A denied entry is
    invisible — the caller only ever gets an aggregate counter.
    """
    scope = entry.scope
    if scope == "global":
        return True
    if scope == "project":
        return bool(ctx.project_id) and entry.project_id == ctx.project_id
    return ctx.include_legacy


def authorization_gate(entry: CapabilityEntry, ctx: EligibilityContext) -> tuple[bool, str]:
    """Gate 2 (separate): is the *caller* authorized for what the tool needs?

    Authorization is not scope: a project tool can be visible and still
    unauthorized (a missing secret handle, a missing permission). Unknown
    caller permissions are reported as ``authorization: unknown`` and gate
    nothing — the caller chose not to declare them.
    """
    if not ctx.permissions_known:
        return True, "authorization unknown (no permission set declared)"
    required = [
        str(item[len("requires:") :])
        for item in (entry.meta.get("preconditions") or [])
        if str(item).startswith("requires:")
    ]
    missing = [handle for handle in required if handle not in ctx.permissions]
    if missing:
        return False, "authorization denied: missing permission(s)"
    return True, ""


def compatibility_filter(entry: CapabilityEntry, ctx: EligibilityContext) -> tuple[bool, str]:
    """Schema/platform/budget compatibility (hard filter, before ranking)."""
    declared = [str(item) for item in (entry.meta.get("platforms") or [])]
    if declared and ctx.platform:
        normalized = {normalize(item) for item in declared}
        current = normalize(ctx.platform)
        if current not in normalized and "any" not in normalized:
            return False, f"platform mismatch (needs {', '.join(declared)})"
    wants_offline = any(
        "offline" in normalize(item) or "no network" in normalize(item)
        for item in ctx.constraints
    )
    if wants_offline and any(
        str(item).startswith("network:") for item in (entry.meta.get("preconditions") or [])
    ):
        return False, "constraint conflict: capability needs network, caller demands offline"
    if ctx.budget is not None and ctx.budget <= 0:
        return False, "budget exhausted"
    return True, ""


def health_snapshot(runtime: str | Path, name: str) -> dict:
    """Health ledger verdict for one tool — never raises, absent store = unknown."""
    try:
        from . import capability_health as health

        state = health.file_state(runtime, name)
        rows = health.events(runtime, name)
        return {
            "state": str(state.get("state") or ""),
            "reason": "; ".join(str(item) for item in (state.get("reasons") or [])[:2]),
            "auto_routable": bool(state.get("auto_routable")),
            "observed": bool(rows),
        }
    except Exception:
        return {"state": "", "reason": "", "auto_routable": False, "observed": False}


def _required_input_types(entry: CapabilityEntry) -> list[str]:
    declared = [str(item) for item in (entry.meta.get("inputs") or [])]
    if declared:
        return declared
    return []


def state_of(
    entry: CapabilityEntry, ctx: EligibilityContext, health: dict
) -> tuple[str, list[str]]:
    """The documented availability state + the reasons that produced it."""
    reasons: list[str] = []
    observed_state = str(health.get("state") or "")
    if observed_state in _LOCKED_HEALTH:
        detail = f"health ledger: {observed_state} {health.get('reason')}".strip()
        return STATE_LOCKED, [detail]
    if observed_state in _DOWN_HEALTH:
        detail = f"health ledger: {observed_state} {health.get('reason')}".strip()
        return STATE_DOWN, [detail]

    services = [str(item) for item in (entry.meta.get("requires_services") or [])]
    for service in services:
        status = str(ctx.services.get(service) or "").strip().lower()
        if not status:
            reasons.append(f"service {service}: state unknown")
            continue
        if status == "running":
            continue
        if status == "stopped":
            return STATE_STARTABLE, [f"service {service} is stopped but startable (ensure_ready)"]
        return STATE_MISSING, [f"service {service} is missing on this host"]

    validated = _execution_evidence(entry, health)
    if not validated:
        return STATE_BUILD_CANDIDATE, ["no passing validation evidence — build candidate only"]
    return STATE_READY, reasons


def _execution_evidence(entry: CapabilityEntry, health: dict) -> bool:
    """Real execution evidence: a passed validation or an observed healthy use."""
    validation = str(entry.meta.get("validation") or "").strip().lower()
    if validation in ("validated", "passed", "pass", "ok"):
        return True
    return bool(health.get("auto_routable"))


# --------------------------------------------------------------------------- #
# ranking (after the gates — evidence only)
# --------------------------------------------------------------------------- #


def _task_fit(
    entry: CapabilityEntry, need_stems: Mapping[str, str]
) -> tuple[float, list[str], bool, list[str]]:
    """Contract-backed lexical fit with diminishing returns per field.

    Matching is stem-based (DE + EN) and asymmetric: only the *documented*
    fields of the contract are searched, and a field's weight is capped by
    diminishing returns — a tool cannot buy priority with prose volume. A need
    that hits a declared *non-use case* is a warning (small penalty), never a
    silent match: "video or audio files" is what this tool does NOT do.
    """
    score = 0.0
    matched: set[str] = set()
    canonical = False
    for name, weight in _FIELD_WEIGHTS.items():
        hits = _stem_hits(need_stems, entry.stem_tokens_for(name))
        if not hits:
            continue
        matched |= set(hits)
        canonical = canonical or name in _CANONICAL_FIELDS
        score += weight * (len(hits) / (1.0 + 0.08 * len(hits)))
    non_use = _stem_hits(need_stems, entry.stem_tokens_for("non_use_cases"))
    if non_use:
        score = max(0.0, score - 0.5 * len(non_use))
    fit = score / (score + 2.5) if score else 0.0
    return fit, sorted(matched), canonical, non_use


def _criteria_coverage(
    entry: CapabilityEntry, criteria: Sequence[str]
) -> tuple[float | None, list[str], list[str]]:
    """Which caller-declared criteria does the contract cover? (None = no criteria)"""
    if not criteria:
        return None, [], []
    covered: list[str] = []
    missing: list[str] = []
    haystack = " ".join(
        entry.text(field)
        for field in ("name", "effect", "tasks", "inputs", "outputs", "synonyms",
                      "phrases_de", "phrases_en", "examples", "tags", "description")
    )
    haystack_stems = _stem_set(haystack)
    for criterion in criteria:
        criterion_stems = _stem_set(criterion)
        if criterion_stems and any(
            _pair_match(st, other) for st in criterion_stems for other in haystack_stems
        ):
            covered.append(str(criterion))
        else:
            missing.append(str(criterion))
    return len(covered) / len(criteria), covered, missing


def _input_readiness(
    entry: CapabilityEntry, ctx: EligibilityContext
) -> tuple[float | None, list[str]]:
    """Do the caller's available inputs cover the tool's required input types?"""
    required = _required_input_types(entry)
    if not required or not ctx.inputs:
        return None, []
    available = {normalize(item) for item in ctx.inputs}
    available_stems: set[str] = set()
    for item in ctx.inputs:
        available_stems |= _stem_set(item)
    satisfied = []
    for item in required:
        key = normalize(item).replace(":", " ")
        named = normalize(item).split(":")[0]
        item_stems = _stem_set(item)
        if key in available or named in available:
            satisfied.append(item)
        elif item_stems and any(
            _pair_match(st, other) for st in item_stems for other in available_stems
        ):
            satisfied.append(item)
    return len(satisfied) / len(required), [item for item in required if item not in satisfied]


def _reliability(entry: CapabilityEntry, runtime: str | Path | None) -> tuple[float | None, int]:
    """Observed reliability from the health ledger (None = never observed)."""
    if runtime is None:
        return None, 0
    try:
        from . import capability_health as health

        rows = health.events(runtime, entry.name)
    except Exception:
        return None, 0
    if not rows:
        return None, 0
    triggers = sum(1 for row in rows if str(row.get("kind") or "") == "trigger")
    verifications = [row for row in rows if str(row.get("kind") or "") == "verification"]
    passes = sum(
        1
        for row in verifications
        if str(row.get("ok")) in ("True", "true") or row.get("ok") is True
    )
    if verifications:
        return passes / len(verifications), len(rows)
    return max(0.0, 1.0 - 0.25 * triggers), len(rows)


def _cost(entry: CapabilityEntry) -> tuple[float | None, float | None]:
    """Expected cost incl. startup, from declared measurements only (ms)."""
    latency = entry.meta.get("expected_latency_ms")
    startup = entry.meta.get("startup_cost_ms")
    values = [float(value) for value in (latency, startup) if isinstance(value, (int, float))]
    if not values:
        return None, None
    total = sum(values)
    return 1.0 / (1.0 + total / 1000.0), total


def rank_entries(
    entries: Sequence[CapabilityEntry],
    *,
    need: str,
    ctx: EligibilityContext,
    runtime: str | Path | None = None,
    semantic: Mapping[str, float] | None = None,
    explicit: Sequence[str] = (),
) -> list[dict]:
    """Score the surviving entries. Deterministic; UNKNOWN stays unknown."""
    need_stems = {stem(word): word for word in tokens(need)}
    criteria = list(ctx.constraints) + list(ctx.inputs)
    explicit_names = {str(item) for item in explicit}
    scored: list[dict] = []
    for entry in entries:
        health = health_snapshot(runtime, entry.name) if runtime is not None else {
            "state": "", "reason": "", "auto_routable": False, "observed": False
        }
        state, state_reasons = state_of(entry, ctx, health)
        fit, matched, canonical, non_use = _task_fit(entry, need_stems)
        coverage, covered, uncovered = _criteria_coverage(entry, criteria)
        readiness, missing_inputs = _input_readiness(entry, ctx)
        reliability, observations = _reliability(entry, runtime)
        cost_score, cost_ms = _cost(entry)

        components: dict[str, float] = {}
        unknown: list[str] = []
        if fit > 0:
            components["task_fit"] = fit
        else:
            unknown.append("task_fit")
        if coverage is None:
            unknown.append("criteria_coverage")
        else:
            components["criteria_coverage"] = coverage
        if entry.meta.get("validation"):
            components["validation"] = 1.0 if _execution_evidence(entry, health) else 0.0
        else:
            unknown.append("validation")
        if health.get("observed"):
            components["health"] = {
                STATE_READY: 1.0,
                STATE_STARTABLE: 0.8,
                STATE_MISSING: 0.3,
                STATE_DOWN: 0.1,
                STATE_LOCKED: 0.0,
                STATE_BUILD_CANDIDATE: 0.4,
            }.get(state, 0.2)
        else:
            unknown.append("health")
        if readiness is None:
            unknown.append("input_readiness")
        else:
            components["input_readiness"] = readiness
        if reliability is None:
            unknown.append("reliability")
        else:
            components["reliability"] = reliability
        if cost_score is None:
            unknown.append("cost")
        else:
            components["cost"] = cost_score

        weight_sum = sum(RANK_WEIGHTS[key] for key in components)
        score = (
            sum(RANK_WEIGHTS[key] * value for key, value in components.items()) / weight_sum
            if weight_sum
            else 0.0
        )
        if semantic:
            similarity = float(semantic.get(entry.name) or 0.0)
            if similarity:
                score = min(1.0, score + 0.05 * similarity)
        # Relevance gate: evidence (validation/health) may RANK a capability —
        # it must never *recommend* one that does not fit the task. Required:
        # a canonically sited match, or two distinct terms, or a verbatim name.
        relevant = entry.name in explicit_names or (
            fit > 0 and (canonical or len(matched) >= 2)
        )
        scored.append(
            {
                "name": entry.name,
                "relevant": relevant,
                "version": str(entry.meta.get("version") or ""),
                "scope": entry.scope,
                "project_id": entry.project_id,
                "surface": str(entry.meta.get("surface") or "generated_tools"),
                "state": state,
                "score": round(score, 3),
                "components": {key: round(value, 3) for key, value in sorted(components.items())},
                "unknown": sorted(unknown),
                "matched_terms": matched,
                "canonical_match": canonical,
                "non_use_hits": non_use,
                "covered_criteria": covered,
                "uncovered_criteria": uncovered,
                "missing_inputs": missing_inputs,
                "state_reasons": state_reasons,
                "health": health,
                "observations": observations,
                "cost_ms": cost_ms,
                "meta": entry.meta,
            }
        )
    # Deterministic: score desc (3-decimal ties are real ties), project tools
    # preferred on a tie, then the name — never dict/insertion order.
    scored.sort(key=lambda row: (-row["score"], 0 if row["scope"] == "project" else 1, row["name"]))
    return scored


# --------------------------------------------------------------------------- #
# the search API
# --------------------------------------------------------------------------- #


def _semantic_scores(
    entries: Sequence[CapabilityEntry], need: str, embedder: Any = None
) -> tuple[dict[str, float], dict]:
    """Optional semantic channel over the EXISTING embedding infrastructure.

    Default is the repo's own ``HashingEmbeddings`` — character trigrams, i.e.
    lexical overlap, and it is labelled as exactly that. A real provider can be
    passed in (same protocol); nothing here requires a new paid dependency.
    Similarity is relevance, never a success probability.
    """
    try:
        from .embedding import HashingEmbeddings, cosine
    except Exception:
        return {}, {"mode": "unavailable", "used": False}
    provider = embedder or HashingEmbeddings()
    mode = "provider" if embedder is not None else "hashing-trigram (lexical)"
    try:
        need_vector = provider.embed(need)
        scores = {
            entry.name: round(cosine(need_vector, provider.embed(_search_blob(entry))), 4)
            for entry in entries
        }
    except Exception:
        return {}, {"mode": mode, "used": False}
    return scores, {"mode": mode, "used": True}


def _search_blob(entry: CapabilityEntry) -> str:
    return " ".join(
        entry.text(field)
        for field in ("name", "effect", "tasks", "synonyms", "phrases_de", "phrases_en", "tags")
    )


def _explicit_names(need: str, entries: Sequence[CapabilityEntry]) -> set[str]:
    """Capability names the need mentions verbatim (a lookup, not a guess)."""
    normalized = normalize(need)
    return {entry.name for entry in entries if entry.name and normalize(entry.name) in normalized}


def discover(
    need: str,
    *,
    runtime: str | Path | None = None,
    entries: Sequence[Mapping[str, Any]] | None = None,
    project_id: str = "",
    inputs: Iterable[str] = (),
    constraints: Iterable[str] = (),
    permissions: Iterable[str] | None = None,
    services: Mapping[str, str] | None = None,
    budget: float | None = None,
    limit: int = 3,
    include_legacy: bool = False,
    include_locked: bool = False,
    cache: DiscoveryCache | None = None,
    embedder: Any = None,
    now: float | None = None,
) -> dict:
    """Find capabilities for a need — filtered, ranked, explained.

    ``runtime`` is the registry directory (``generated_tools`` + ``tools.db``);
    ``entries`` injects metadata records directly (harness/tests). The return
    value is JSON-ready and never names a scope- or authorization-excluded tool.
    """
    started = now if now is not None else time.monotonic()
    if entries is not None:
        source_entries = build_index(entries)
    elif runtime is not None:
        source_entries = metadata_index(runtime, include_legacy=include_legacy)
    else:
        source_entries = []
    revision = registry_revision(runtime, project_id=project_id) if runtime is not None else {
        "revision": "entries",
        "index_version": DISCOVERY_INDEX_VERSION,
        "parts": ["in-memory entries"],
        "published_events": {"supported": False, "count": 0, "last_seq": 0},
    }
    ctx = EligibilityContext.make(
        project_id=project_id,
        permissions=permissions,
        inputs=inputs,
        constraints=constraints,
        budget=budget,
        services=services,
        platform=_platform.system().lower(),
        include_legacy=include_legacy,
    )
    key = cache_key(
        need,
        project_id=project_id,
        revision=str(revision.get("revision") or ""),
        permissions=permissions,
    )
    if cache is not None:
        cached = cache.lookup(key)
        if cached is not None:
            result = dict(cached)
            result["cache"] = {"hit": True, "key": key, "stats": cache.stats()}
            return result

    counters = {"scope": 0, "authorization": 0, "compatibility": 0, "locked": 0}
    eligible: list[CapabilityEntry] = []
    explicit = _explicit_names(need, source_entries)
    for entry in source_entries:
        if not scope_gate(entry, ctx):
            counters["scope"] += 1
            continue
        allowed, _why = authorization_gate(entry, ctx)
        if not allowed:
            counters["authorization"] += 1
            continue
        compatible, _reason = compatibility_filter(entry, ctx)
        if not compatible:
            counters["compatibility"] += 1
            continue
        if not entry.name or not entry.text("effect"):
            continue
        eligible.append(entry)

    semantic_scores, semantic_meta = _semantic_scores(eligible, need, embedder)
    ranked = rank_entries(
        eligible,
        need=need,
        ctx=ctx,
        runtime=runtime,
        semantic=semantic_scores,
        explicit=sorted(explicit),
    )

    results: list[dict] = []
    build_candidates: list[dict] = []
    locked_seen: list[str] = []
    for row in ranked:
        if not row.get("relevant"):
            continue
        state = row["state"]
        if state == STATE_LOCKED:
            counters["locked"] += 1
            locked_seen.append(row["name"])
            if not (include_locked or row["name"] in explicit):
                continue
        if state == STATE_BUILD_CANDIDATE:
            build_candidates.append(_public_row(row, executable=False))
            continue
        if row["score"] <= 0 and row["name"] not in explicit:
            continue
        results.append(_public_row(row, executable=state == STATE_READY))

    executable = [row for row in results if row["executable"]]
    selected = results[: max(0, int(limit))]
    error = _error_envelope(selected, build_candidates, locked_seen, counters, need)
    payload = {
        "need": str(need),
        "normalized_need": normalize(need),
        "index_version": DISCOVERY_INDEX_VERSION,
        "registry_revision": str(revision.get("revision") or ""),
        # NOTE: only the digest, never the revision's human-readable parts —
        # those name tools (including excluded ones) and belong to the operator
        # view of ``registry_revision()``, not to a search answer.

        "published_events": dict(revision.get("published_events") or {}),
        "project_id": str(project_id or ""),
        "results": selected,
        "build_candidates": build_candidates[: max(0, int(limit))],
        "executable_count": len(executable),
        "blocked": counters,
        "semantic": semantic_meta,
        "searched_surfaces": _searched_surfaces(runtime),
        "error": error,
        "took_ms": round((time.monotonic() - started) * 1000.0, 3),
        "cache": {"hit": False, "key": key, "stats": cache.stats() if cache else None},
    }
    if cache is not None:
        cache.store(key, payload)
    return payload


def _searched_surfaces(runtime: str | Path | None) -> list[str]:
    surfaces = ["generated_tools", "testing_tools", "capability_contract"]
    if runtime is not None:
        surfaces.append("health_ledger")
        surfaces.append("registry_revision")
        try:
            from .act_service import subtool_dir
        except Exception:  # noqa: BLE001 — surface naming stays non-fatal
            subtool_dir = None  # type: ignore[assignment]
        if subtool_dir is not None and Path(runtime).is_dir():
            surfaces.append("act_subtools")
    return surfaces


def _public_row(row: dict, *, executable: bool) -> dict:
    """One result row: no internal token sets, no manifest dump, honest limits."""
    meta = row.get("meta") or {}
    limits: list[str] = []
    limits.extend(str(item) for item in (meta.get("non_use_cases") or [])[:3])
    if row.get("non_use_hits"):
        limits.append("need mentions a declared non-use case: " + ", ".join(row["non_use_hits"]))
    limits.extend(row.get("state_reasons") or [])
    for item in row.get("missing_inputs") or []:
        limits.append(f"missing input: {item}")
    if not limits and executable:
        limits.append("no declared limits (contract carries none)")
    return {
        "name": row["name"],
        "capability_id": row["name"],
        "version": row.get("version") or "",
        "scope": row.get("scope") or "",
        "project_id": row.get("project_id") or "",
        "surface": row.get("surface") or "generated_tools",
        "state": row.get("state") or "",
        "selectable": bool(executable),
        "executable": bool(executable),
        "build_candidate": row.get("state") == STATE_BUILD_CANDIDATE,
        "score": row.get("score"),
        "reasons": _reasons(row),
        "limits": limits,
        "unknown": list(row.get("unknown") or []),
        "components": dict(row.get("components") or {}),
        "preconditions": [str(item) for item in (meta.get("preconditions") or [])],
        "validation": str(meta.get("validation") or ""),
        "effect": str(meta.get("effect") or ""),
        # TOOL-02: the call contract travels with the row, so a discovery hit is
        # immediately actionable. Absent when the descriptor declares none — an omitted
        # key, never a fabricated argument shape.
        **({"input_schema": meta["input_schema"]}
           if isinstance(meta.get("input_schema"), dict) else {}),
        **({"output_schema": meta["output_schema"]}
           if isinstance(meta.get("output_schema"), dict) else {}),
        **({"call": str(meta["call"])}
           if str(meta.get("call") or "").strip() else {}),
    }


def _reasons(row: dict) -> list[str]:
    reasons: list[str] = []
    matched = row.get("matched_terms") or []
    if matched:
        reasons.append("task fit via: " + ", ".join(matched[:6]))
    covered = row.get("covered_criteria") or []
    if covered:
        reasons.append("covers criteria: " + ", ".join(covered[:4]))
    if row.get("state") == STATE_STARTABLE:
        reasons.append("startable: verified and startable on demand (ensure_ready)")
    if row.get("scope") == "project":
        reasons.append("project-scoped match")
    if row.get("health", {}).get("observed"):
        reasons.append(f"health observed: {row['health'].get('state')}")
    if not reasons:
        reasons.append(f"state: {row.get('state')}")
    return reasons


_STATE_CODES = {
    STATE_LOCKED: "capability_locked",
    STATE_DOWN: "capability_temporarily_down",
    STATE_MISSING: "capability_missing_prerequisite",
    STATE_STARTABLE: "capability_startable",
}


def _error_envelope(
    results: Sequence[dict],
    build_candidates: Sequence[dict],
    locked_seen: Sequence[str],
    counters: Mapping[str, int],
    need: str,
) -> dict | None:
    """Structured error — never a silent empty success, never a foreign name.

    An impaired match (down/locked/missing/startable) is reported *even when* a
    healthy alternative exists: the caller must learn that the top match cannot
    run right now ("strukturierter Fehler + Alternative/Blocker").
    """
    impaired = [row for row in results if not row["executable"]]
    executable = [row for row in results if row["executable"]]
    if results:
        if not impaired:
            return None
        code = "capability_not_executable"
        for state, state_code in _STATE_CODES.items():
            if any(row["state"] == state for row in impaired):
                code = state_code
                break
        return {
            "code": code,
            "message": (
                f"{len(impaired)} matching capability(ies) are not executable right now"
                + ("; alternatives available" if executable else "")
            ),
            "capabilities": [row["name"] for row in impaired],
            "alternatives": [row["name"] for row in executable],
            "blocked": dict(counters),
        }
    if any(row["executable"] for row in results):  # pragma: no cover - unreachable
        return None
    if build_candidates:
        return {
            "code": "build_candidate_only",
            "message": "no verified capability covers this need — build candidate(s) only",
            "candidates": [row["name"] for row in build_candidates],
            "blocked": dict(counters),
        }
    if locked_seen:
        return {
            "code": "capability_locked",
            "message": "matching capability(ies) are locked (quarantine/retirement)",
            "blocked": dict(counters),
        }
    return {
        "code": "no_match",
        "message": f"no capability covers: {need.strip()}",
        "blocked": dict(counters),
    }


# --------------------------------------------------------------------------- #
# candidate set for the model context (bounded, sanctioned channel)
# --------------------------------------------------------------------------- #


def context_block(result: Mapping[str, Any], *, limit: int = 3, max_chars: int = 900) -> str:
    """The compact, bounded candidate set the plugin injects (pre_llm_call).

    Small on purpose: at most ``limit`` executable capabilities, a build
    candidate hint, and the honest state of each. Never a system prompt edit —
    the host appends this to the user message.
    """
    rows = [row for row in (result.get("results") or []) if row.get("executable")][:limit]
    lines: list[str] = []
    for row in rows:
        reason = (row.get("reasons") or [""])[0]
        limit_text = (row.get("limits") or [""])[0]
        lines.append(
            f"- {row['name']} v{row.get('version') or '?'} "
            f"[{row.get('scope')}/{row.get('state')}]: "
            f"{row.get('effect') or ''} — {reason}; limit: {limit_text}"
        )
    if not rows:
        candidates = result.get("build_candidates") or []
        if candidates:
            lines.append(f"- no verified capability; build candidate: {candidates[0]['name']}")
        else:
            error = result.get("error") or {}
            lines.append(f"- no capability matched ({error.get('code') or 'no_match'})")
    block = "godsv capability discovery: " + " | ".join(lines)
    if len(block) > max_chars:
        block = block[: max_chars - 3] + "..."
    return block


def estimate_tokens(text: str) -> int:
    """Estimate of the context tokens a block costs (chars/4, documented)."""
    return int(math.ceil(len(str(text or "")) / 4.0))


# --------------------------------------------------------------------------- #
# ensure_ready — the B6 interface, wired to the real container start
# --------------------------------------------------------------------------- #


def ensure_ready(
    name: str,
    *,
    runtime: str | Path | None = None,
    project_id: str = "",
    services: Mapping[str, str] | None = None,
    manager=None,
) -> dict:
    """Start exactly once what a startable capability needs (container/service).

    B6 is wired: a capability whose staged container adapter declares a service
    spec is started through ``DockerRuntimeManager`` EXACTLY once (the container
    carries our labels and is torn down by label, never by name). A capability
    without a container adapter has nothing to start — the structured refusal
    below, never a fake success, so callers can always tell ``startable`` from
    ``started``. ``manager`` injects a docker runtime (tests, embeddings);
    without one the real daemon is used.
    """
    runtime_dir = Path(runtime) if runtime is not None else default_runtime_dir()
    try:
        from .toolbuilder import ensure_container_started

        report = ensure_container_started(runtime_dir, str(name), manager=manager)
    except Exception as exc:  # noqa: BLE001 — a start that cannot even be attempted reports
        report = {
            "capability": str(name), "started": False, "startable": True, "reused": False,
            "reason": f"{type(exc).__name__}: {exc}"[:200], "hook": "ensure_ready",
        }
    report.setdefault("services", dict(services or {}))
    report.setdefault("project_id", str(project_id or ""))
    return report


def default_runtime_dir() -> Path:
    """The runtime dir this module resolves to (one truth, the registry's)."""
    from .registry import default_runtime_dir as _default

    return Path(_default())


def default_cache() -> DiscoveryCache:
    """The process-wide cache the plugin uses (bounded, revision-keyed)."""
    return DiscoveryCache()


__all__ = [
    "DISCOVERY_INDEX_VERSION",
    "RANK_WEIGHTS",
    "STATE_BUILD_CANDIDATE",
    "STATE_DOWN",
    "STATE_LOCKED",
    "STATE_MISSING",
    "STATE_READY",
    "STATE_STARTABLE",
    "CapabilityEntry",
    "DiscoveryCache",
    "EligibilityContext",
    "authorization_gate",
    "build_index",
    "cache_key",
    "compatibility_filter",
    "context_block",
    "default_cache",
    "discover",
    "ensure_ready",
    "estimate_tokens",
    "health_snapshot",
    "metadata_index",
    "normalize",
    "rank_entries",
    "registry_revision",
    "scope_gate",
    "state_of",
    "tokens",
]
