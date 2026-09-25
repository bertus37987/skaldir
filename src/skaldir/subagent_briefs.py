"""Subagent assignment briefs — the worker contract (design 04 §5).

Plain, in-memory dataclasses plus a small coordinator that owns the status
lifecycle and the owner/epoch fencing. This intentionally does NOT add a new
lease/store system: the coordinator is the local adapter for what RunStore
already establishes per-run (lease owner + monotonic expectations). A run
supervisor can persist briefs via :meth:`skaldir.run_supervisor.RunStore.
enqueue_brief` / :meth:`record_brief_result` without duplicating that system.

Semantics:
* worktree required for code-writing workers (invalid brief -> ValueError at
  creation);
* results are fenced: only the current ``(owner, owner_epoch)`` pair is
  accepted, a stale epoch after takeover is rejected;
* a timed-out child yields ``timed_out_partial`` when any verifiable output
  exists (non-empty, non-whitespace) else ``timed_out_empty``;
* parent abort cancels every brief in the family and records which workers may
  already have started external actions (``side_effect_state = unknown``).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from .llm import estimate_tokens

STATUS_CREATED = "created"
STATUS_RUNNING = "running"
STATUS_COLLECTING = "collecting"
STATUS_VERIFYING = "verifying"

OUTCOME_DELIVERED = "delivered"
OUTCOME_TIMED_OUT_PARTIAL = "timed_out_partial"
OUTCOME_TIMED_OUT_EMPTY = "timed_out_empty"
OUTCOME_ABORTED = "aborted"

LIFECYCLE = {
    STATUS_CREATED: 1,
    STATUS_RUNNING: 2,
    STATUS_COLLECTING: 3,
    STATUS_VERIFYING: 4,
}
TERMINAL = {OUTCOME_DELIVERED, OUTCOME_TIMED_OUT_PARTIAL, OUTCOME_TIMED_OUT_EMPTY, OUTCOME_ABORTED}

CODE_WORKER_KINDS = frozenset({"code", "build", "test", "reviewer_diff"})


class SubagentBriefError(ValueError):
    """Raised when a brief contract is violated (validation or protocol)."""


@dataclass
class SubagentBrief:
    """The assignment contract between the parent agent and one worker."""

    assignment_id: str
    goal_id: str
    base_revision: str
    allowed_write_paths: list[str]  # scope: empty = invalid
    read_context: list[str]
    owner: str
    deadline_monotonic: float = 0.0  # seconds on the parent's monotonic clock
    acceptance: list[str] = field(default_factory=list)  # empty = invalid
    worktree: str | None = None  # REQUIRED for code-writing workers
    worker_kind: str = "code"
    budget_reservation: str = ""  # reservation id tied to a skaldir.budget account
    status: str = STATUS_CREATED
    owner_epoch: int = 1  # monotonic fencing token, bumped on takeover
    external_may_have_started: bool = False
    side_effect_state: str = "unknown"

    def __post_init__(self) -> None:
        validate_brief(self)

    def advance(self, status: str, *, external_may_have_started: bool = False) -> None:
        """Move the lifecycle forward (created → running → collecting → verifying)."""
        if status not in LIFECYCLE:
            raise SubagentBriefError(f"unknown brief status: {status!r}")
        if status in TERMINAL or self.status in TERMINAL:
            raise SubagentBriefError("brief is already terminal")
        if LIFECYCLE[status] <= LIFECYCLE[self.status]:
            raise SubagentBriefError(
                f"lifecycle is forward-only: {self.status!r} -> {status!r}"
            )
        self.status = status
        if external_may_have_started:
            self.external_may_have_started = True
        if self.external_may_have_started:
            self.side_effect_state = "unknown"


@dataclass
class BriefDecision:
    """Structured result of reporting a result / timeout / abort."""

    accepted: bool = False
    reason: str = ""
    outcome: str = ""
    side_effects_unknown: list[str] = field(default_factory=list)


def validate_brief(brief: SubagentBrief) -> None:
    """Reject an invalid contract at creation time."""
    missing = [
        name
        for name in ("assignment_id", "goal_id", "owner", "base_revision")
        if not str(getattr(brief, name) or "").strip()
    ]
    if missing:
        raise SubagentBriefError(f"brief missing required identity fields: {missing}")
    if not brief.allowed_write_paths:
        raise SubagentBriefError(f"brief {brief.assignment_id}: empty write scope")
    # scope paths must be explicit entries, not wildcard free-for-alls
    if any(not str(path).strip() for path in brief.allowed_write_paths):
        raise SubagentBriefError(f"brief {brief.assignment_id}: blank entry in write scope")
    if not brief.acceptance:
        raise SubagentBriefError(f"brief {brief.assignment_id}: no acceptance criteria")
    if brief.deadline_monotonic is None or float(brief.deadline_monotonic) <= 0.0:
        raise SubagentBriefError(f"brief {brief.assignment_id}: missing deadline")
    if brief.status not in LIFECYCLE:
        raise SubagentBriefError(f"brief {brief.assignment_id}: invalid status")
    if brief.worker_kind in CODE_WORKER_KINDS and not (brief.worktree or "").strip():
        raise SubagentBriefError(
            f"brief {brief.assignment_id}: code worker requires an isolated worktree"
        )


class BriefCoordinator:
    """Owns briefs for one goal: lifecycle, fencing, timeouts, aborts."""

    def __init__(self, *, now: float = 0.0) -> None:
        self._briefs: dict[str, SubagentBrief] = {}
        self._now = float(now)

    def create_brief(
        self,
        *,
        assignment_id: str,
        goal_id: str,
        base_revision: str,
        allowed_write_paths: list[str],
        read_context: list[str],
        owner: str,
        deadline_monotonic: float,
        acceptance: list[str],
        worktree: str | None = None,
        worker_kind: str = "code",
        budget_reservation: str = "",
    ) -> SubagentBrief:
        """Validate-and-register a new brief (worker_kind drives worktree rule)."""
        brief = SubagentBrief(
            assignment_id=assignment_id,
            goal_id=goal_id,
            base_revision=base_revision,
            allowed_write_paths=list(allowed_write_paths),
            read_context=list(read_context),
            owner=owner,
            deadline_monotonic=float(deadline_monotonic),
            acceptance=list(acceptance),
            worktree=worktree,
            worker_kind=worker_kind,
            budget_reservation=budget_reservation,
        )
        if brief.assignment_id in self._briefs:
            raise SubagentBriefError(f"assignment already registered: {brief.assignment_id}")
        self._briefs[brief.assignment_id] = brief
        return brief

    def get(self, assignment_id: str) -> SubagentBrief | None:
        return self._briefs.get(str(assignment_id))

    def briefs(self) -> list[SubagentBrief]:
        return list(self._briefs.values())

    # -- takeover / fencing -----------------------------------------------

    def takeover(self, assignment_id: str, *, new_owner: str) -> SubagentBrief:
        """Take a brief over: new owner, strictly monotonic epoch.

        Any result the previous owner reports from now on is fenced out.
        """
        brief = self.get(str(assignment_id))
        if brief is None:
            raise SubagentBriefError(f"unknown assignment: {assignment_id}")
        if not str(new_owner).strip():
            raise SubagentBriefError("takeover needs a new owner identity")
        brief.owner = str(new_owner)
        brief.owner_epoch += 1
        # the new owner restarts its share of the lifecycle (not below running)
        if brief.status not in TERMINAL:
            brief.status = STATUS_RUNNING
        return brief

    def check_fencing(self, brief: SubagentBrief, *, owner: str, owner_epoch: int) -> str:
        """Return an empty string when authentic, else the rejection reason.

        The monotonic epoch is the primary fence: any pre-takeover epoch is
        rejected as stale even if the owner name still matches (owners can be
        reused; tokens are not).
        """
        if int(owner_epoch) != int(brief.owner_epoch):
            return "stale_epoch"
        if str(owner) != brief.owner:
            return "unknown_owner"
        if brief.status in TERMINAL:
            return "already_settled"
        return ""

    # -- results / timeout / abort -----------------------------------------

    def report_result(
        self,
        assignment_id: str,
        *,
        owner: str,
        owner_epoch: int,
        delivered: bool,
        outputs: list[str] | None = None,
    ) -> BriefDecision:
        """Record a worker result — fenced on (owner, owner_epoch)."""
        brief = self.get(str(assignment_id))
        if brief is None:
            raise SubagentBriefError(f"unknown assignment: {assignment_id}")
        reason = self.check_fencing(brief, owner=owner, owner_epoch=owner_epoch)
        if reason:
            return BriefDecision(accepted=False, reason=reason)
        if not delivered and not has_verifiable_output(outputs):
            outcome = OUTCOME_TIMED_OUT_EMPTY
        elif delivered:
            outcome = OUTCOME_DELIVERED
        else:
            outcome = OUTCOME_TIMED_OUT_PARTIAL
        brief.status = outcome
        return BriefDecision(accepted=True, outcome=outcome)

    def on_timeout(
        self,
        assignment_id: str,
        *,
        owner: str,
        owner_epoch: int,
        outputs: list[str] | None = None,
    ) -> BriefDecision:
        """A child that hit its deadline: partial result if anything verifies.

        Fenced exactly like :meth:`report_result` on ``(owner, owner_epoch)``:
        a stale epoch after takeover (or an unknown owner) may NOT settle the
        brief with a timeout outcome — otherwise anyone who observed the
        assignment id could close a live brief.
        """
        brief = self.get(str(assignment_id))
        if brief is None:
            raise SubagentBriefError(f"unknown assignment: {assignment_id}")
        reason = self.check_fencing(brief, owner=owner, owner_epoch=owner_epoch)
        if reason:
            return BriefDecision(accepted=False, reason=reason)
        outcome = (
            OUTCOME_TIMED_OUT_PARTIAL if has_verifiable_output(outputs) else OUTCOME_TIMED_OUT_EMPTY
        )
        brief.status = outcome
        return BriefDecision(accepted=True, outcome=outcome)

    def abort_all(self, *, reason: str = "") -> BriefDecision:
        """Parent abort: cancel every outstanding brief, mark unclear externals.

        A worker that already reported running and may have started external
        actions keeps ``side_effect_state = unknown`` — it is never silently
        assumed safe or clean.
        """
        unknown = []
        for brief in self._briefs.values():
            if brief.status in TERMINAL:
                continue
            brief.status = OUTCOME_ABORTED
            if brief.external_may_have_started:
                brief.side_effect_state = "unknown"
                unknown.append(brief.assignment_id)
        return BriefDecision(accepted=True, outcome=OUTCOME_ABORTED, side_effects_unknown=unknown)


def has_verifiable_output(outputs: list[str] | None) -> bool:
    """True iff at least one output entry is usable as a verifiable artifact."""
    return any(str(item).strip() for item in (outputs or []))


# --------------------------------------------------------------------------- #
# the Evidence Brief — what a verifier is handed instead of the transcript        #
# --------------------------------------------------------------------------- #

#: The brief's own schema id. Bumped when the packet's shape changes, so a
#: reader can tell "a brief from before the change" from "a malformed brief".
BRIEF_SCHEMA = "skaldir.subagent_brief.v1"
BRIEF_VERSION = 1

#: The rendered brief's hard ceiling. A brief is an *index*, not a transcript:
#: it must fit one prompt with room for the verifier's own instructions.
DEFAULT_MAX_BRIEF_CHARS = 14_000
#: How many transcript lines the tail keeps by default (the newest ones).
DEFAULT_TAIL_LINES = 20
MAX_OBJECTIVE_CHARS = 4_000
MAX_CLAIM_CHARS = 2_000
MAX_CHANGE_ITEMS = 100
MAX_COMMAND_ITEMS = 100
MAX_EVIDENCE_REFS = 50
MAX_REF_CHARS = 500
MAX_PATH_CHARS = 1_000
MAX_DIFFSTAT_CHARS = 64
MAX_CMD_CHARS = 2_000
MAX_TAIL_LINE_CHARS = 800
#: Used when the caller supplied no completion claim at all. A brief never
#: invents one, and never reads silence as a claim.
NO_CLAIM = "No explicit completion claim was supplied."


class EvidenceBriefError(SubagentBriefError):
    """The brief could not be built — never silently shortened past its limits."""


@dataclass(frozen=True)
class ChangedFile:
    """One file the run changed: path, diffstat and a content digest.

    ``diffstat`` is the shape ``"+12 -3"`` (insertions/deletions) so a verifier
    can weigh a change without the diff; ``digest`` is a short sha256 of the
    file's bytes *as the run left them*, so "the file changed after the claim"
    is detectable.
    """

    path: str
    diffstat: str = ""
    digest: str = ""
    added: int = 0
    removed: int = 0

    def __post_init__(self) -> None:
        if not str(self.path).strip():
            raise EvidenceBriefError("a changed file needs a path")

    @property
    def stat(self) -> str:
        """The diffstat, derived from the counts when none was supplied."""
        if str(self.diffstat).strip():
            return str(self.diffstat).strip()[:MAX_DIFFSTAT_CHARS]
        return f"+{int(self.added)} -{int(self.removed)}"

    def as_dict(self) -> dict:
        return {
            "path": str(self.path)[:MAX_PATH_CHARS],
            "diffstat": self.stat,
            "digest": str(self.digest)[:64],
        }


@dataclass(frozen=True)
class CommandFact:
    """One command the run executed, with the exit code that makes it evidence."""

    cmd: str
    exit_code: int | None = None
    duration_ms: int = 0
    source: str = ""

    def __post_init__(self) -> None:
        if not str(self.cmd).strip():
            raise EvidenceBriefError("a command fact needs a command")

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    def as_dict(self) -> dict:
        return {
            "cmd": str(self.cmd)[:MAX_CMD_CHARS],
            "exit_code": None if self.exit_code is None else int(self.exit_code),
            "duration_ms": int(self.duration_ms),
            "source": str(self.source)[:64],
        }


@dataclass(frozen=True)
class EvidenceBrief:
    """The structured packet handed to a verifier **instead of** the run history.

    Fields, and why each one is here rather than in a transcript:

    * ``objective`` / ``objective_digest`` — what was asked, plus a digest that
      binds the brief to the exact goal revision it was built for;
    * ``claim`` — the completion claim **verbatim** (never paraphrased: the
      verifier's job is to check *this* sentence);
    * ``files`` — changed files with diffstat + digest;
    * ``commands`` — commands executed with exit codes;
    * ``evidence_refs`` — the evidence the run itself recorded (hashes, run ids,
      artifact paths);
    * ``transcript_tail`` — a bounded tail of N lines, so a verifier can see the
      manner of the ending without the whole history;
    * ``truncated`` — true whenever anything was dropped to fit, so a verifier
      never reads a shortened packet as a complete one.
    """

    objective: str
    claim: str
    objective_digest: str = ""
    files: tuple[ChangedFile, ...] = ()
    commands: tuple[CommandFact, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    transcript_tail: tuple[str, ...] = ()
    transcript_lines_total: int = 0
    truncated: bool = False
    baseline_revision: str = ""
    version: int = BRIEF_VERSION
    chars: int = 0

    @property
    def schema(self) -> str:
        return BRIEF_SCHEMA

    def as_dict(self) -> dict:
        return {
            "schema": BRIEF_SCHEMA,
            "version": int(self.version),
            "objective": {"text": self.objective, "digest": self.objective_digest},
            "claim": self.claim,
            "changes": {
                "files": [item.as_dict() for item in self.files],
                "commands": [item.as_dict() for item in self.commands],
            },
            "evidence_refs": list(self.evidence_refs),
            "transcript_tail": list(self.transcript_tail),
            "transcript_lines_total": int(self.transcript_lines_total),
            "baseline_revision": self.baseline_revision,
            "truncated": bool(self.truncated),
        }

    def with_chars(self, chars: int) -> EvidenceBrief:
        """A copy carrying its own rendered size (set by the builder)."""
        return replace(self, chars=int(chars))


def _as_changed_file(item: Any) -> ChangedFile:
    if isinstance(item, ChangedFile):
        return item
    if isinstance(item, Mapping):
        return ChangedFile(
            path=str(item.get("path") or item.get("file") or ""),
            diffstat=str(item.get("diffstat") or ""),
            digest=str(item.get("digest") or item.get("sha256") or ""),
            added=int(item.get("added") or item.get("insertions") or 0),
            removed=int(item.get("removed") or item.get("deletions") or 0),
        )
    if isinstance(item, (tuple, list)) and item:
        parts = list(item)
        path = str(parts[0])
        if len(parts) >= 3 and all(isinstance(part, int) for part in parts[1:3]):
            return ChangedFile(path=path, added=int(parts[1]), removed=int(parts[2]))
        return ChangedFile(path=path, diffstat=str(parts[1]) if len(parts) > 1 else "")
    raise EvidenceBriefError(f"cannot read a changed file from {type(item).__name__}")


def _as_command_fact(item: Any) -> CommandFact:
    if isinstance(item, CommandFact):
        return item
    if isinstance(item, Mapping):
        exit_code = item.get("exit_code", item.get("exit"))
        return CommandFact(
            cmd=str(item.get("cmd") or item.get("command") or ""),
            exit_code=None if exit_code is None else int(exit_code),
            duration_ms=int(item.get("duration_ms") or 0),
            source=str(item.get("source") or ""),
        )
    if isinstance(item, (tuple, list)) and item:
        parts = list(item)
        return CommandFact(
            cmd=str(parts[0]),
            exit_code=None if len(parts) < 2 or parts[1] is None else int(parts[1]),
        )
    raise EvidenceBriefError(f"cannot read a command fact from {type(item).__name__}")


def _tail_lines(source: Any) -> list[str]:
    """Normalise a tail source (lines, or turn dicts) into plain strings."""
    if source is None:
        return []
    if isinstance(source, str):
        return source.splitlines()
    lines: list[str] = []
    for item in list(source):
        if isinstance(item, Mapping):
            text = item.get("text") or item.get("answer") or item.get("line") or ""
            turn = item.get("turn")
            prefix = f"t{turn}: " if turn is not None else ""
            role = item.get("role")
            role_prefix = f"{role}: " if role else ""
            lines.append(f"{prefix}{role_prefix}{text}")
        else:
            lines.append(str(item))
    return lines


def _render(blob: dict) -> str:
    """One JSON string, with ``<``/``>`` escaped so data cannot forge a tag."""
    return json.dumps(blob, ensure_ascii=False, sort_keys=True).replace(
        "<", "\\u003c"
    ).replace(">", "\\u003e")


def build_evidence_brief(
    *,
    objective: str,
    claim: str = "",
    objective_digest: str = "",
    files: Any = (),
    commands: Any = (),
    evidence_refs: Any = (),
    transcript: Any = (),
    baseline_revision: str = "",
    tail_lines: int = DEFAULT_TAIL_LINES,
    max_chars: int = DEFAULT_MAX_BRIEF_CHARS,
) -> EvidenceBrief:
    """Build one bounded Evidence Brief — the single entry point to this schema.

    Everything is capped (objective, claim, files, commands, refs, tail) and the
    whole packet is then fitted to ``max_chars`` by dropping, in this order:
    oldest tail lines, then commands, then files, then a shortened claim, then a
    shortened objective. It **never** drops the claim entirely and it **never**
    silently exceeds the limit: if nothing is left to drop, it raises
    :class:`EvidenceBriefError` instead of sending an over-long packet.

    ``truncated`` is set as soon as anything was dropped, so a verifier can tell
    "this is the whole picture" from "this is what fitted".
    """
    if not str(objective or "").strip():
        raise EvidenceBriefError("an evidence brief needs an objective")
    if int(max_chars) < 256:
        raise EvidenceBriefError("max_chars must be >= 256")
    if int(tail_lines) < 0:
        raise EvidenceBriefError("tail_lines must be >= 0")

    dropped = False

    tail_source = _tail_lines(transcript)
    total_lines = len(tail_source)
    keep = max(0, int(tail_lines))
    if total_lines > keep:
        dropped = True
    tail = [
        line[:MAX_TAIL_LINE_CHARS] for line in tail_source[-keep:] if str(line).strip()
    ]

    files_list = [_as_changed_file(item) for item in list(files or ())][:MAX_CHANGE_ITEMS]
    commands_list = [_as_command_fact(item) for item in list(commands or ())][
        :MAX_COMMAND_ITEMS
    ]
    refs = [
        str(item)[:MAX_REF_CHARS] for item in list(evidence_refs or ()) if str(item).strip()
    ][:MAX_EVIDENCE_REFS]
    if len(list(files or ())) > MAX_CHANGE_ITEMS or len(list(commands or ())) > MAX_COMMAND_ITEMS:
        dropped = True

    claim_text = str(claim or "").strip() or NO_CLAIM
    if len(claim_text) > MAX_CLAIM_CHARS:
        claim_text = claim_text[:MAX_CLAIM_CHARS]
        dropped = True
    objective_text = str(objective).strip()
    if len(objective_text) > MAX_OBJECTIVE_CHARS:
        objective_text = objective_text[:MAX_OBJECTIVE_CHARS]
        dropped = True
    # A digest is a digest: bounded here, not left as the one field that could
    # make the packet overflow with nothing left to drop.
    digest_text = str(objective_digest or "")[:128]

    brief = EvidenceBrief(
        objective=objective_text,
        claim=claim_text,
        objective_digest=digest_text,
        files=tuple(files_list),
        commands=tuple(commands_list),
        evidence_refs=tuple(refs),
        transcript_tail=tuple(tail),
        transcript_lines_total=total_lines,
        truncated=dropped,
        baseline_revision=str(baseline_revision or "")[:128],
    )

    def compiled(candidate: EvidenceBrief) -> str:
        return _render(candidate.as_dict())

    blob = compiled(brief)
    while len(blob) > int(max_chars) and brief.transcript_tail:
        brief = replace(brief, transcript_tail=brief.transcript_tail[1:], truncated=True)
        blob = compiled(brief)
    while len(blob) > int(max_chars) and (brief.commands or brief.files):
        if len(brief.commands) >= len(brief.files):
            brief = replace(brief, commands=brief.commands[:-1], truncated=True)
        else:
            brief = replace(brief, files=brief.files[:-1], truncated=True)
        blob = compiled(brief)
    while len(blob) > int(max_chars) and brief.evidence_refs:
        brief = replace(brief, evidence_refs=brief.evidence_refs[:-1], truncated=True)
        blob = compiled(brief)
    if len(blob) > int(max_chars) and len(brief.claim) > 120:
        overflow = len(blob) - int(max_chars)
        brief = replace(
            brief, claim=brief.claim[: max(120, len(brief.claim) - overflow)], truncated=True
        )
        blob = compiled(brief)
    if len(blob) > int(max_chars) and len(brief.objective) > 120:
        overflow = len(blob) - int(max_chars)
        brief = replace(
            brief,
            objective=brief.objective[: max(120, len(brief.objective) - overflow)],
            truncated=True,
        )
        blob = compiled(brief)
    if len(blob) > int(max_chars):
        raise EvidenceBriefError(
            f"evidence brief is {len(blob)} chars (> {int(max_chars)}) and has nothing "
            "left to drop — refusing to send an over-long packet"
        )
    return brief.with_chars(len(blob))


def render_evidence_brief(brief: EvidenceBrief, *, label: str = "evidence_brief_json") -> str:
    """The verifier-facing prompt block: the brief, tagged as untrusted data."""
    payload = _render(brief.as_dict())
    return "\n".join(
        [
            f'<{label} trust="untrusted_data" schema="{BRIEF_SCHEMA}">',
            payload,
            f"</{label}>",
        ]
    )


def brief_chars(brief: EvidenceBrief) -> int:
    """The brief's rendered size (measured, never estimated)."""
    return len(_render(brief.as_dict()))


def brief_tokens(brief: EvidenceBrief) -> int:
    """Estimated tokens for the brief, on the runtime's own chars/4 estimate."""
    return estimate_tokens(_render(brief.as_dict()))


def transcript_tokens(lines: Any) -> int:
    """Estimated tokens for the *whole* transcript the brief replaces.

    The same estimator and the same payload shape as :func:`brief_tokens`, so the
    two numbers in ``MVP/93-subagent-roles.md`` §4 are comparable.
    """
    payload = json.dumps(list(_tail_lines(lines)), ensure_ascii=False)
    return estimate_tokens(payload)


def brief_tail_from_turns(turns: Any, *, limit: int = DEFAULT_TAIL_LINES) -> list[str]:
    """Build a transcript tail from a harness turn list (the harness' own shape)."""
    return _tail_lines(list(turns))[-max(0, int(limit)) :]
