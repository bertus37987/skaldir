"""The approval ladder: who may run a command, on what authority, and on what record.

The gap this closes
-------------------

We had per-tool permission *declarations* and a per-tool classifier
(:func:`skaldir.toolcall.judge_tool_call`, ``ALLOW`` / ``ASK_APPROVAL`` /
``PAUSE_AND_INVESTIGATE`` / ``BLOCK``), but no approval dialog and no grant scopes: a decision
could not be "yes, just this once" versus "yes, and remember it", and nothing was ever
*persisted* as policy.

This module is the **command** half of the ladder, and it is four independent rungs:

``allow | prompt | forbid``
    The verdict of the persisted prefix rules (:class:`PrefixRule`) — a three-value
    language, never a bool. Rules live in ``<runtime>/approval/policy.json``.
``turn | session``
    The grant scope (:class:`GrantScope`). A *turn* grant is "allow once" and dies with the
    turn; a *session* grant survives inside its session id and is **not** inherited by a new
    session. Both live in :class:`GrantLedger`.
``cannot decompose ⇒ must prompt``
    A command is only eligible for rule matching when it can be split into plain segments.
    Redirection, command substitution, wildcards, a leading ``VAR=value``, a heredoc or an
    unbalanced quote makes it **ineligible** — and an ineligible command is never
    auto-allowed, whatever the rules say. It escalates to the user.
``banned prefixes``
    A list that can never be auto-approved: no rule, no turn grant and no session grant may
    allow one, and an attempt to create such a rule/grant is refused outright. Arbitrary
    interpreters (``python3 -``), destructive filesystem tools (``rm``, ``dd``), privilege
    escalation (``sudo``) and raw network fetchers (``curl``, ``nc``) are on it.

Precedence, once, and the reason for it::

    1. a FORBID rule matches            → forbid   (an explicit "never" beats everything,
                                                    including a session grant)
    2. a banned prefix is present       → prompt    (never allow; grants are refused)
    3. the profile's command allowlist  → forbid    (outside the declared permission profile)
       does not contain the head token
    4. an ALLOW rule matches            → allow
    5. a matching grant applies         → allow     (unless the policy is `unless_trusted`)
    6. a PROMPT rule matches            → prompt
    7. nothing matched                  → prompt    (fail closed: silence is not consent)

Headless honesty is a first-class rule, not a footnote: with no interactive user
(``--json`` / a pipe), a ``prompt`` verdict **must not block forever and must not
auto-allow**. :meth:`ApprovalEngine.decide` returns ``blocked`` (exit 3) carrying the exact
rule that decided it and what the user would need to allow (:meth:`Verdict.what_would_allow`).

Every judgement — allow, prompt, deny — is appended to an evidence ledger
(``<runtime>/approval/events.jsonl``) with the deciding rule, the scope, the profile hash and
the session, so "why did this run?" is answerable after the fact from the record, not from
anyone's memory.
"""

from __future__ import annotations

import json
import re
import shlex
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Any

from .permission_profile import PermissionProfile, default_profile

SCHEMA_VERSION = "skaldir.approval-policy/1"
EVENTS_SCHEMA = "skaldir.approval-events/1"


class Decision(StrEnum):
    """The three-value verdict of a rule (and of a judgement)."""

    ALLOW = "allow"
    PROMPT = "prompt"
    FORBID = "forbid"


class GrantScope(StrEnum):
    """How long a grant lives. ``TURN`` is "allow once"; ``SESSION`` is this conversation."""

    TURN = "turn"
    SESSION = "session"


class ReviewDecision(StrEnum):
    """What the user answered — the shape of an answer, not a bool.

    ``APPROVED`` is "just this once"; ``APPROVED_RULE_AMENDMENT`` is "yes, and make it
    policy"; ``APPROVED_FOR_SESSION`` is the session cache; ``DENIED`` carries a reason so
    the agent is told what to do differently instead of hitting a dead end; ``ABORT`` stops
    the turn (versus ``DENIED``, which continues); ``TIMED_OUT`` is the honest non-answer.
    """

    APPROVED = "approved"
    APPROVED_FOR_SESSION = "approved_for_session"
    APPROVED_RULE_AMENDMENT = "approved_rule_amendment"
    DENIED = "denied"
    TIMED_OUT = "timed_out"
    ABORT = "abort"


#: The vocabulary shared with :mod:`skaldir.toolcall`. One mapping, so the command ladder and
#: the tool classifier cannot invent two spellings for the same thing.
TOOL_CLASSIFICATION_DECISIONS: dict[str, str] = {
    "ALLOW": Decision.ALLOW.value,
    "ASK_APPROVAL": Decision.PROMPT.value,
    "PAUSE_AND_INVESTIGATE": Decision.PROMPT.value,
    "BLOCK": Decision.FORBID.value,
}

#: The approval policies, mirroring the tool-level vocabulary.
APPROVAL_POLICIES: tuple[str, ...] = ("on_request", "unless_trusted", "never")

#: Control operators a command is split at. Each resulting segment is judged on its own.
CONTROL_OPERATORS: tuple[str, ...] = (";", "&&", "||", "|", "&")

#: Redirection / here documents: the segment is not rule-eligible.
REDIRECTION_OPERATORS: tuple[str, ...] = (">", ">>", "<", "<<", "<>", ">&", "&>", ">|", "<<<")

#: Characters that make a segment ineligible for rule matching (substitution, globbing).
SUBSTITUTION_CHARS: tuple[str, ...] = ("$", "`", "*", "?", "[", "]")

#: ``VAR=value`` as a token — an environment prefix is not a plain segment.
_ENV_PREFIX = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

#: Prefixes that can never be auto-approved. Arbitrary scripting, destructive filesystem
#: tools, privilege escalation, raw network fetchers, container/namespace escapes.
DEFAULT_BANNED_PREFIXES: tuple[tuple[str, ...], ...] = (
    ("python",), ("python3",), ("pypy",), ("pypy3",),
    ("perl",), ("ruby",), ("node",), ("php",), ("lua",), ("tclsh",),
    ("sh",), ("bash",), ("dash",), ("zsh",), ("ksh",), ("fish",),
    ("env",), ("xargs",), ("eval",), ("exec",), ("source",), ("sudo",), ("su",), ("doas",),
    ("pkexec",),
    ("rm",), ("rmdir",), ("dd",), ("shred",), ("truncate",), ("wipefs",), ("mkfs",),
    ("chmod",), ("chown",), ("chgrp",), ("setfacl",),
    ("kill",), ("pkill",), ("killall",),
    ("curl",), ("wget",), ("nc",), ("ncat",), ("netcat",), ("socat",), ("ssh",), ("scp",),
    ("systemctl",), ("mount",), ("umount",), ("modprobe",), ("insmod",), ("crontab",),
    ("docker",), ("podman",), ("nsenter",), ("unshare",), ("tee",),
    ("git", "push"), ("git", "reset"), ("git", "clean"),
)

#: The one-line reasons the banned list comes with, so the refusal teaches instead of just
#: refusing.
BANNED_REASONS: dict[str, str] = {
    "python": "an interpreter turns a fixed prefix into arbitrary code",
    "sh": "a shell turns a fixed prefix into arbitrary code",
    "rm": "a destructive filesystem tool",
    "dd": "writes raw devices",
    "curl": "fetches and can execute remote content",
    "sudo": "privilege escalation beyond the declared permission profile",
}

#: The reason text for a prefix not in the map above.
BANNED_REASON_DEFAULT = (
    "on the banned-prefix list: an approved rule for it would allow arbitrary or destructive "
    "work, so it can never be auto-approved"
)


class BannedPrefixRefusal(ValueError):
    """Raised when a rule or a grant is requested for a banned prefix."""


class PolicyError(ValueError):
    """Raised for a malformed policy document or an unknown policy value."""


# ------------------------------------------------------------------ decomposition


@dataclass(frozen=True)
class Decomposition:
    """A command split into the segments a rule may be matched against."""

    segments: tuple[tuple[str, ...], ...]
    control_operators: tuple[str, ...]
    rule_eligible: bool
    reasons: tuple[str, ...] = ()

    @property
    def heads(self) -> tuple[str, ...]:
        return tuple(segment[0] for segment in self.segments if segment)

    def to_dict(self) -> dict[str, Any]:
        return {
            "segments": [list(segment) for segment in self.segments],
            "control_operators": list(self.control_operators),
            "rule_eligible": bool(self.rule_eligible),
            "reasons": list(self.reasons),
        }


def _tokenize(text: str) -> list[str]:
    """Split one command line into tokens, keeping quotes meaningful and operators separate."""
    lexer = shlex.shlex(text, posix=True, punctuation_chars="();<>|&`")
    lexer.whitespace_split = True
    lexer.commenters = ""
    return list(lexer)


def decompose(command: str) -> Decomposition:
    """Split ``command`` into rule-matchable segments — or say why it cannot be.

    Ineligible is not an error: it is the escalation trigger. A command we cannot decompose
    safely is a command we may not auto-allow (the rule that makes the whole ladder safe).
    """
    text = str(command or "")
    if not text.strip():
        return Decomposition((), (), False, ("the command is empty",))
    segments: list[list[str]] = []
    operators: list[str] = []
    reasons: list[str] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            tokens = _tokenize(line)
        except ValueError as exc:
            return Decomposition((), (), False, (f"cannot tokenize the command: {exc}",))
        current: list[str] = []
        for token in tokens:
            if token in CONTROL_OPERATORS:
                operators.append(token)
                if current:
                    segments.append(current)
                current = []
                continue
            current.append(token)
        if current:
            segments.append(current)
        for token in tokens:
            if token in REDIRECTION_OPERATORS:
                reasons.append(f"redirection/heredoc {token!r} — the file it touches is opaque")
            elif token in SUBSTITUTION_CHARS or any(char in token for char in SUBSTITUTION_CHARS):
                reasons.append(
                    f"substitution or wildcard in {token!r} — the expanded command is not "
                    f"the command that would be matched"
                )
            elif _ENV_PREFIX.match(token):
                reasons.append(f"environment prefix {token!r} — the child environment is opaque")
    if not segments:
        reasons.append("no command segment could be identified")
    if operators and any(operator in ("&",) for operator in operators):
        pass  # a background `&` is split like any other control operator
    # de-duplicate while keeping the order a reader can follow
    unique: list[str] = []
    for reason in reasons:
        if reason not in unique:
            unique.append(reason)
    return Decomposition(
        segments=tuple(tuple(segment) for segment in segments),
        control_operators=tuple(operators),
        rule_eligible=bool(segments) and not unique,
        reasons=tuple(unique),
    )


def _matches_token(pattern: str, token: str) -> bool:
    """One pattern token matches ``token`` — literally, or as a ``a|b`` alternative."""
    text = str(pattern)
    if "|" in text:
        return str(token) in text.split("|")
    return text == str(token)


def _prefix_matches(prefix: Sequence[str], tokens: Sequence[str]) -> bool:
    if not prefix or len(prefix) > len(tokens):
        return False
    return all(
        _matches_token(pattern, token) for pattern, token in zip(prefix, tokens, strict=False)
    )


def render_prefix(prefix: Sequence[str]) -> str:
    return " ".join(str(item) for item in prefix)


# ----------------------------------------------------------------------- rules


@dataclass(frozen=True)
class PrefixRule:
    """One persisted policy rule: a prefix of a command segment, and its verdict."""

    prefix: tuple[str, ...]
    decision: str
    id: str = ""
    note: str = ""
    source: str = "policy"
    created_at: float = 0.0
    order: int = 0

    def __post_init__(self) -> None:
        if self.decision not in {item.value for item in Decision}:
            raise PolicyError(
                f"unknown decision {self.decision!r} (known: allow, prompt, forbid)"
            )

    @property
    def rule_id(self) -> str:
        return self.id or "rule:" + render_prefix(self.prefix).replace(" ", "-")

    def rule_text(self) -> str:
        return f'{self.decision} "{render_prefix(self.prefix)}"'

    def matches(self, tokens: Sequence[str]) -> bool:
        return _prefix_matches(self.prefix, tokens)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.rule_id,
            "prefix": list(self.prefix),
            "decision": self.decision,
            "note": self.note,
            "source": self.source,
            "created_at": self.created_at,
            "order": self.order,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PrefixRule:
        return cls(
            prefix=tuple(str(item) for item in data.get("prefix", ())),
            decision=str(data.get("decision", Decision.PROMPT.value)),
            id=str(data.get("id", "")),
            note=str(data.get("note", "")),
            source=str(data.get("source", "policy")),
            created_at=float(data.get("created_at", 0.0) or 0.0),
            order=int(data.get("order", 0) or 0),
        )


def is_banned_prefix(prefix: Sequence[str], banned: Iterable[Sequence[str]]) -> bool:
    """Is ``prefix`` on the banned list? A banned prefix may never be auto-approved."""
    tokens = [str(item) for item in prefix]
    return any(_prefix_matches(item, tokens) for item in banned)


def banned_reason(prefix: Sequence[str]) -> str:
    """Why this prefix is on the list, in one line a reader can act on."""
    head = str(prefix[0]) if prefix else ""
    for token, reason in BANNED_REASONS.items():
        if head.startswith(token):
            return reason
    return BANNED_REASON_DEFAULT


# ----------------------------------------------------------------------- grants


@dataclass(frozen=True)
class Grant:
    """A user's "yes" with a scope attached."""

    prefix: tuple[str, ...]
    scope: str
    command: str = ""
    session: str = ""
    actor: str = "user"
    granted_at: float = 0.0
    evidence_ref: str = ""

    def __post_init__(self) -> None:
        if self.scope not in {item.value for item in GrantScope}:
            raise PolicyError(f"unknown grant scope {self.scope!r} (known: turn, session)")

    def matches(self, tokens: Sequence[str]) -> bool:
        return _prefix_matches(self.prefix, tokens)

    def to_dict(self) -> dict[str, Any]:
        return {
            "prefix": list(self.prefix),
            "scope": self.scope,
            "command": self.command,
            "session": self.session,
            "actor": self.actor,
            "granted_at": self.granted_at,
            "evidence_ref": self.evidence_ref,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Grant:
        return cls(
            prefix=tuple(str(item) for item in data.get("prefix", ())),
            scope=str(data.get("scope", GrantScope.TURN.value)),
            command=str(data.get("command", "")),
            session=str(data.get("session", "")),
            actor=str(data.get("actor", "user")),
            granted_at=float(data.get("granted_at", 0.0) or 0.0),
            evidence_ref=str(data.get("evidence_ref", "")),
        )


def grants_dir(runtime: str | Path) -> Path:
    return Path(runtime) / "approval" / "grants"


def grants_path(runtime: str | Path, session: str) -> Path:
    name = re.sub(r"[^A-Za-z0-9_.-]", "_", str(session)) or "default"
    return grants_dir(runtime) / f"{name}.json"


class GrantLedger:
    """Turn- and session-scoped grants, with the one rule that matters: a turn grant dies.

    A session grant is persisted under its **session id**. A different session id therefore
    starts with an empty ledger — "allow for this session" never becomes "allow forever by
    accident", which is exactly what ``tests/test_approval_grants.py`` measures.
    """

    def __init__(
        self,
        *,
        runtime: str | Path | None = None,
        session: str = "",
        banned: Iterable[Sequence[str]] = DEFAULT_BANNED_PREFIXES,
        persist: bool = True,
    ) -> None:
        self.runtime = Path(runtime) if runtime else None
        self.session = str(session)
        self.banned = tuple(tuple(str(item) for item in entry) for entry in banned)
        self.persist = bool(persist)
        self._turn: list[Grant] = []
        self._session: list[Grant] = []
        if self.persist and self.runtime is not None and self.session:
            self._load()

    # ------------------------------------------------------------ persistence

    def _load(self) -> None:
        runtime = self.runtime
        if runtime is None or not self.session:
            return
        path = grants_path(runtime, self.session)
        if not path.is_file():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return
        for item in data.get("session_grants", ()) or ():
            if isinstance(item, Mapping):
                self._session.append(Grant.from_dict(item))

    def save(self) -> Path | None:
        """Persist the session grants. Turn grants are deliberately *not* written."""
        if not self.persist or self.runtime is None or not self.session:
            return None
        path = grants_path(self.runtime, self.session)
        path.parent.mkdir(parents=True, exist_ok=True)
        document = {
            "schema": SCHEMA_VERSION,
            "session": self.session,
            "session_grants": [grant.to_dict() for grant in self._session],
            "note": (
                "only session-scoped grants are persisted: a turn grant must not survive the "
                "turn it was given in"
            ),
        }
        path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return path

    # ----------------------------------------------------------------- shape

    @property
    def turn(self) -> tuple[Grant, ...]:
        return tuple(self._turn)

    @property
    def session_grants(self) -> tuple[Grant, ...]:
        return tuple(self._session)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session": self.session,
            "turn": [grant.to_dict() for grant in self._turn],
            "session_grants": [grant.to_dict() for grant in self._session],
            "counts": {"turn": len(self._turn), "session": len(self._session)},
        }

    # ---------------------------------------------------------------- writes

    def add(
        self,
        prefix: Sequence[str],
        scope: str,
        *,
        command: str = "",
        actor: str = "user",
        granted_at: float | None = None,
        evidence_ref: str = "",
    ) -> Grant:
        """Record a grant. A banned prefix is refused — that is what "banned" means here."""
        tokens = tuple(str(item) for item in prefix if str(item))
        if not tokens:
            raise PolicyError("a grant needs a non-empty prefix")
        if is_banned_prefix(tokens, self.banned):
            raise BannedPrefixRefusal(
                f"refusing a {scope} grant for {render_prefix(tokens)!r}: "
                f"{banned_reason(tokens)} — banned prefixes can never be auto-approved"
            )
        grant = Grant(
            prefix=tokens,
            scope=str(scope),
            command=str(command),
            session=self.session,
            actor=str(actor),
            granted_at=float(granted_at if granted_at is not None else time.time()),
            evidence_ref=str(evidence_ref),
        )
        if grant.scope == GrantScope.TURN.value:
            self._turn.append(grant)
        else:
            self._session.append(grant)
            self.save()
        return grant

    def purge_turn(self) -> int:
        """Drop every turn grant (the end of a turn); returns how many were dropped."""
        count = len(self._turn)
        self._turn = []
        return count

    # ---------------------------------------------------------------- reads

    def match(self, tokens: Sequence[str]) -> Grant | None:
        """The most recent grant that covers ``tokens``, turn grants preferred (more specific)."""
        for grant in reversed(self._turn):
            if grant.matches(tokens):
                return grant
        for grant in reversed(self._session):
            if grant.matches(tokens):
                return grant
        return None


# -------------------------------------------------------------- policy store


def policy_path(runtime: str | Path, profile: str = "default") -> Path:
    """The policy file of one profile: a profile owns its rules, not just its sandbox."""
    name = re.sub(r"[^A-Za-z0-9_.-]", "_", str(profile).strip()) or "default"
    return Path(runtime) / "approval" / f"policy-{name}.json"


class RuleStore:
    """The persisted rule language: prefix rules + the banned-prefix list."""

    def __init__(
        self,
        *,
        runtime: str | Path | None = None,
        profile: str = "default",
        rules: Iterable[PrefixRule] = (),
        banned: Iterable[Sequence[str]] = DEFAULT_BANNED_PREFIXES,
        persist: bool = False,
    ) -> None:
        self.runtime = Path(runtime) if runtime else None
        self.profile = str(profile or "default")
        self.persist = bool(persist)
        self.banned: tuple[tuple[str, ...], ...] = tuple(
            tuple(str(item) for item in entry) for entry in banned
        )
        self._rules: list[PrefixRule] = list(rules)
        if self.persist and self.runtime is not None:
            self.load()

    # ------------------------------------------------------------ persistence

    def load(self) -> RuleStore:
        path = policy_path(self.runtime, self.profile) if self.runtime else None
        if path is None or not path.is_file():
            return self
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise PolicyError(f"{path} is not readable as policy: {exc}") from exc
        if not isinstance(data, dict):
            raise PolicyError(f"{path} is not a policy document")
        self._rules = [
            PrefixRule.from_dict(item)
            for item in data.get("rules", ()) or ()
            if isinstance(item, Mapping)
        ]
        extra = [
            tuple(str(part) for part in item)
            for item in data.get("banned_prefixes", ()) or ()
            if isinstance(item, (list, tuple))
        ]
        if extra:
            known = {tuple(entry) for entry in self.banned}
            for entry in extra:
                if entry not in known:
                    self.banned = (*self.banned, entry)
        return self

    def save(self) -> Path | None:
        if not self.persist or self.runtime is None:
            return None
        path = policy_path(self.runtime, self.profile)
        path.parent.mkdir(parents=True, exist_ok=True)
        document = {
            "schema": SCHEMA_VERSION,
            "profile": self.profile,
            "rules": [rule.to_dict() for rule in self._rules],
            "banned_prefixes": [list(entry) for entry in self.banned],
            "note": (
                "a banned prefix can never be auto-approved: no rule and no grant may allow it"
            ),
        }
        path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return path

    # ----------------------------------------------------------------- shape

    @property
    def rules(self) -> tuple[PrefixRule, ...]:
        return tuple(self._rules)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA_VERSION,
            "profile": self.profile,
            "path": str(policy_path(self.runtime, self.profile)) if self.runtime else "",
            "rules": [rule.to_dict() for rule in self._rules],
            "banned_prefixes": [list(entry) for entry in self.banned],
            "counts": {
                "rules": len(self._rules),
                "banned": len(self.banned),
                "allow": sum(1 for r in self._rules if r.decision == Decision.ALLOW.value),
                "prompt": sum(1 for r in self._rules if r.decision == Decision.PROMPT.value),
                "forbid": sum(1 for r in self._rules if r.decision == Decision.FORBID.value),
            },
        }

    # ---------------------------------------------------------------- writes

    def add_rule(
        self,
        prefix: Sequence[str],
        decision: str,
        *,
        note: str = "",
        source: str = "policy",
        now: float | None = None,
        allow_banned: bool = False,
    ) -> PrefixRule:
        """Add a rule. ``allow``/``prompt`` rules for a banned prefix are refused.

        A ``forbid`` rule for a banned prefix is allowed (and pointless) — adding a *harder*
        verdict is never an escalation. Only the permissive directions are refused.
        """
        tokens = tuple(str(item) for item in prefix if str(item))
        if not tokens:
            raise PolicyError("a rule needs a non-empty prefix")
        wanted = Decision(str(decision))
        if wanted is not Decision.FORBID and not allow_banned:
            if is_banned_prefix(tokens, self.banned):
                raise BannedPrefixRefusal(
                    f"refusing a {wanted.value} rule for {render_prefix(tokens)!r}: "
                    f"{banned_reason(tokens)} — it can never be auto-approved"
                )
        rule = PrefixRule(
            prefix=tokens,
            decision=wanted.value,
            id=f"rule-{len(self._rules) + 1}-{render_prefix(tokens).replace(' ', '_')}",
            note=note,
            source=source,
            created_at=float(now if now is not None else time.time()),
            order=len(self._rules),
        )
        self._rules.append(rule)
        self.save()
        return rule

    def remove_rule(self, rule_id: str) -> bool:
        before = len(self._rules)
        self._rules = [rule for rule in self._rules if rule.rule_id != str(rule_id)]
        changed = len(self._rules) != before
        if changed:
            self.save()
        return changed

    # ---------------------------------------------------------------- reads

    def evaluate(self, tokens: Sequence[str]) -> tuple[str, PrefixRule | None]:
        """The verdict for one segment: ``(decision, rule)``; ``rule`` is ``None`` when none.

        A ``forbid`` rule wins over everything else, whatever the declaration order: an
        explicit "never" must not be defeated by a later friendly rule (or by a session grant
        evaluated after it).
        """
        matched = [rule for rule in self._rules if rule.matches(tokens)]
        for rule in matched:
            if rule.decision == Decision.FORBID.value:
                return Decision.FORBID.value, rule
        for rule in matched:
            if rule.decision == Decision.ALLOW.value:
                return Decision.ALLOW.value, rule
        for rule in matched:
            if rule.decision == Decision.PROMPT.value:
                return Decision.PROMPT.value, rule
        return "", None

    def rules_for(self, tokens: Sequence[str]) -> list[PrefixRule]:
        return [rule for rule in self._rules if rule.matches(tokens)]


# ------------------------------------------------------------------ evidence


def events_path(runtime: str | Path) -> Path:
    return Path(runtime) / "approval" / "events.jsonl"


class EvidenceLedger:
    """The append-only record of every allow / prompt / denial, with the deciding rule."""

    def __init__(self, runtime: str | Path | None = None) -> None:
        self.runtime = Path(runtime) if runtime else None

    @property
    def path(self) -> Path | None:
        return events_path(self.runtime) if self.runtime else None

    def record(self, entry: Mapping[str, Any]) -> dict[str, Any]:
        """Append one record; returns it with its ``seq`` and ``evidence_ref`` filled in."""
        payload = {str(key): value for key, value in entry.items()}
        payload.setdefault("schema", EVENTS_SCHEMA)
        payload.setdefault("at", time.time())
        seq = self.count() + 1
        payload["seq"] = seq
        payload["evidence_ref"] = f"approval-events.jsonl:{seq}"
        path = self.path
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, sort_keys=True, default=str) + "\n")
        return payload

    def read(self) -> list[dict[str, Any]]:
        path = self.path
        if path is None or not path.is_file():
            return []
        out: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if isinstance(item, dict):
                out.append(item)
        return out

    def count(self) -> int:
        path = self.path
        if path is None or not path.is_file():
            return 0
        with path.open("r", encoding="utf-8") as handle:
            return sum(1 for line in handle if line.strip())

    def tail(self, limit: int = 20) -> list[dict[str, Any]]:
        items = self.read()
        return items[-max(1, int(limit)):] if items else []


# ------------------------------------------------------------------ verdicts


@dataclass(frozen=True)
class Verdict:
    """The answer for one command: what would happen, why, and on whose authority."""

    command: str
    decision: str
    reason_kind: str
    reason: str
    rule_id: str = ""
    rule_text: str = ""
    scope: str = ""
    head: str = ""
    profile_name: str = ""
    profile_sha256: str = ""
    session: str = ""
    policy: str = "on_request"
    interactive: bool = False
    segments: tuple[tuple[str, ...], ...] = ()
    rule_eligible: bool = True
    ineligible_reasons: tuple[str, ...] = ()
    banned_prefix: str = ""
    blocked: bool = False
    exit_code: int = 0
    evidence_ref: str = ""
    would_allow: tuple[str, ...] = ()
    #: **Under what** this command would run: the mechanism, the controls the mechanism resolves
    #: to, and every field nothing enforces. "Why did this run" and "under what" are one question.
    enforcement: dict[str, Any] = field(default_factory=dict)

    # -------------------------------------------------------------- predicates

    @property
    def allowed(self) -> bool:
        return self.decision == Decision.ALLOW.value and not self.blocked

    @property
    def forbidden(self) -> bool:
        return self.decision == Decision.FORBID.value

    @property
    def prompted(self) -> bool:
        return self.decision == Decision.PROMPT.value

    @property
    def approval_required(self) -> bool:
        return self.prompted or self.forbidden

    def what_would_allow(self) -> list[str]:
        """The exact commands the user would have to run for this to be allowed."""
        if self.would_allow:
            return list(self.would_allow)
        if self.decision == Decision.ALLOW.value:
            return []
        prefix = render_prefix(self.segments[0]) if self.segments else self.head or self.command
        head = self.head or (self.segments[0][0] if self.segments else "")
        if self.reason_kind == "unenforceable_control":
            return [
                "no command can be allowed while a required control is unenforceable — fix the "
                "profile first: skaldir permission explain",
                "or relax the declaration: skaldir permission set "
                "--set linux_security.landlock=auto",
            ]
        if self.banned_prefix:
            return [
                f"a foreground approval for this exact command: skaldir approval allow "
                f"--scope turn {shlex.quote(self.command)}",
                f"permanent rules and session grants are refused for the banned prefix "
                f"{self.banned_prefix!r} ({banned_reason((self.banned_prefix,))}) — the user has "
                f"to approve it themselves, and the approval dies with the turn",
            ]
        if not self.rule_eligible:
            return [
                f"a foreground approval for this exact command: skaldir approval allow "
                f"--scope turn {shlex.quote(self.command)}",
                f"rewrite the command without redirection, substitution, wildcards or an "
                f"environment prefix so a prefix rule can cover it "
                f"(head token: {head!r})",
            ]
        return [
            f"allow it once, for the next turn: skaldir approval allow --scope turn "
            f"{shlex.quote(prefix)}",
            f"allow it for this session: skaldir approval allow --scope session "
            f"{shlex.quote(prefix)}",
            f"make it policy: skaldir approval allow --rule {shlex.quote(prefix)}",
            f"or deny it with a reason the agent can act on: skaldir approval deny "
            f"--reason '<what to do instead>' {shlex.quote(prefix)}",
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "decision": self.decision,
            "allowed": self.allowed,
            "prompted": self.prompted,
            "forbidden": self.forbidden,
            "blocked": self.blocked,
            "exit_code": int(self.exit_code),
            "reason_kind": self.reason_kind,
            "reason": self.reason,
            "rule_id": self.rule_id,
            "rule_text": self.rule_text,
            "scope": self.scope,
            "head": self.head,
            "banned_prefix": self.banned_prefix,
            "profile": {"name": self.profile_name, "sha256": self.profile_sha256},
            "policy": self.policy,
            "interactive": bool(self.interactive),
            "session": self.session,
            "segments": [list(segment) for segment in self.segments],
            "rule_eligible": bool(self.rule_eligible),
            "ineligible_reasons": list(self.ineligible_reasons),
            "evidence_ref": self.evidence_ref,
            "would_allow": self.what_would_allow(),
            "enforcement": dict(self.enforcement),
        }


# ------------------------------------------------------------------- engine


def decision_from_tool_classification(classification: str) -> str:
    """Map a :mod:`skaldir.toolcall` classification onto this ladder (one vocabulary)."""
    return TOOL_CLASSIFICATION_DECISIONS.get(str(classification).upper(), Decision.PROMPT.value)


def enforcement_snapshot(profile: PermissionProfile) -> dict[str, Any]:
    """**"Under what?"** — the profile's enforcement answer as one payload for a verdict.

    Deliberately taken from the *profile* (which is the same object the sandbox argv and the
    prompt block come from), so a verdict cannot report a mechanism the run will not use.
    """
    mechanism, reason = profile.mechanism()
    return {
        "mechanism": mechanism,
        "mechanism_reason": reason,
        "profile_sha256": profile.identity_sha256,
        "controls": {
            "landlock": profile.landlock_mode(),
            "seccomp": profile.seccomp_mode(),
            "no_new_privs": "enforce" if profile.linux_security.no_new_privs else "off",
            "pids_cgroup": profile.pids_cgroup_mode()[0],
        },
        "unenforced": profile.unenforced_fields(),
        "unenforceable_required": profile.unenforceable_required(),
        # Gap 11 (MVP/84): *which* primitives this host can enforce at all, per primitive, with
        # the reason — the fail-closed sentence ("nothing runs here") is in `execution_refused`.
        "platform_coverage": profile.platform_coverage(),
        "platform_verdict": profile.platform_verdict(),
    }


class ApprovalEngine:
    """Judge a command against the profile, the rules, the grants — and record the verdict."""

    def __init__(
        self,
        profile: PermissionProfile | None = None,
        *,
        runtime: str | Path | None = None,
        session: str = "",
        store: RuleStore | None = None,
        grants: GrantLedger | None = None,
        evidence: EvidenceLedger | None = None,
        policy: str = "on_request",
        interactive: bool = False,
        now: float | None = None,
    ) -> None:
        if str(policy) not in APPROVAL_POLICIES:
            raise PolicyError(
                f"unknown approval policy {policy!r} (known: {', '.join(APPROVAL_POLICIES)})"
            )
        self.profile = profile or default_profile()
        self.runtime = Path(runtime) if runtime else None
        self.session = str(session)
        self.policy = str(policy)
        self.interactive = bool(interactive)
        self.now = now
        self.store = store if store is not None else RuleStore(runtime=self.runtime, persist=False)
        self.grants = grants if grants is not None else GrantLedger(
            runtime=self.runtime, session=self.session, banned=self.store.banned, persist=False
        )
        self.evidence = evidence if evidence is not None else EvidenceLedger(self.runtime)

    # ------------------------------------------------------------------ judge

    def judge(self, command: str) -> Verdict:
        """The ladder, in one place. Never raises for a hostile command — it classifies it."""
        text = str(command or "")
        parts = decompose(text)
        head = parts.heads[0] if parts.heads else ""
        base: dict[str, Any] = {
            "command": text,
            "head": head,
            "segments": parts.segments,
            "rule_eligible": parts.rule_eligible,
            "ineligible_reasons": parts.reasons,
            "profile_name": self.profile.name,
            "profile_sha256": self.profile.identity_sha256,
            "session": self.session,
            "policy": self.policy,
            "interactive": self.interactive,
            "enforcement": enforcement_snapshot(self.profile),
        }

        # 0. a control the profile *requires* that this host cannot apply forfeits the whole
        #    ladder. This is the rung that closes the honest gap between "declared" and
        #    "enforced": a rule that would allow a command must not be able to run it under a
        #    policy the kernel is not applying. Requiring nothing keeps the old behaviour exactly.
        unenforceable = self.profile.unenforceable_required()
        if unenforceable:
            details = "; ".join(
                f"{item['control']}: {item['reason']} ({item['fix']})" for item in unenforceable
            )
            return self._finish(
                base,
                decision=Decision.FORBID.value,
                reason_kind="unenforceable_control",
                reason=(
                    "the permission profile requires a control this host cannot apply, so no "
                    "command may run under it — " + details
                ),
            )

        # 0b. nothing to judge
        if not parts.segments:
            return self._finish(
                base,
                decision=Decision.PROMPT.value,
                reason_kind="undecomposable",
                reason="the command could not be decomposed into segments: "
                + ("; ".join(parts.reasons) or "no segment found"),
            )

        banned = next(
            (
                segment[0]
                for segment in parts.segments
                if is_banned_prefix(segment, self.store.banned)
            ),
            "",
        )

        # 1. a FORBID rule beats everything, including a session grant
        if parts.rule_eligible:
            for segment in parts.segments:
                decision, rule = self.store.evaluate(segment)
                if decision == Decision.FORBID.value and rule:
                    return self._finish(
                        base,
                        decision=Decision.FORBID.value,
                        reason_kind="rule",
                        reason=f"a persisted rule forbids it: {rule.rule_text()}",
                        rule=rule,
                    )

        # 2. a command we cannot decompose may never be auto-allowed
        if not parts.rule_eligible:
            return self._finish(
                base,
                decision=Decision.PROMPT.value,
                reason_kind="undecomposable",
                reason="the command cannot be decomposed safely ("
                + "; ".join(parts.reasons)
                + ") — an undecomposable command is never auto-allowed, whatever the rules say",
                banned_prefix=banned,
            )

        # 3. a banned prefix is always a prompt: no rule and no grant may allow it
        if banned:
            reason = (
                f"{banned!r} is on the banned-prefix list ({banned_reason((banned,))}) — "
                f"it can never be auto-approved"
            )
            if self.policy == "never":
                return self._finish(
                    base,
                    decision=Decision.FORBID.value,
                    reason_kind="banned_prefix",
                    reason=reason + "; the approval policy is 'never', so it is rejected outright",
                    banned_prefix=banned,
                )
            return self._finish(
                base,
                decision=Decision.PROMPT.value,
                reason_kind="banned_prefix",
                reason=reason,
                banned_prefix=banned,
            )

        # 4. the profile's own command allowlist, when one is declared
        allowed_prefixes = self.profile.allowed_subprocess_prefixes
        if allowed_prefixes:
            for segment in parts.segments:
                if not any(_prefix_matches(list(allow), segment) for allow in allowed_prefixes):
                    return self._finish(
                        base,
                        decision=Decision.FORBID.value,
                        reason_kind="profile_allowlist",
                        reason=(
                            f"{segment[0]!r} is not in the permission profile's command "
                            f"allowlist "
                            f"({'; '.join(render_prefix(item) for item in allowed_prefixes)})"
                        ),
                    )

        # 5-8. Every segment must be covered, or the user decides. This is the rule that makes
        # the ladder safe: `git log && git diff` with a rule for `git log` alone is NOT an
        # allow — the second segment was never authorised by anything.
        authorities: list[str] = []
        grant_used: Grant | None = None
        for segment in parts.segments:
            decision, rule = self.store.evaluate(segment)
            if decision == Decision.ALLOW.value and rule:
                authorities.append(rule.rule_text())
                continue
            if decision == Decision.PROMPT.value and rule:
                return self._finish(
                    base,
                    decision=Decision.PROMPT.value,
                    reason_kind="rule",
                    reason=f"a persisted rule asks the user: {rule.rule_text()}",
                    rule=rule,
                )
            if self.policy != "unless_trusted":
                grant = self.grants.match(segment)
                if grant is not None:
                    authorities.append(f"grant {grant.scope} \"{render_prefix(grant.prefix)}\"")
                    grant_used = grant
                    continue
            return self._finish(
                base,
                decision=Decision.PROMPT.value,
                reason_kind="default",
                reason=(
                    f"no rule and no grant covers {render_prefix(segment)!r}"
                    + (
                        f" (already decided for {', '.join(authorities)})" if authorities else ""
                    )
                    + " — silence is not consent, so the user decides"
                ),
            )
        if grant_used is not None:
            return self._finish(
                base,
                decision=Decision.ALLOW.value,
                reason_kind="grant",
                reason=(
                    "the user granted "
                    + ", ".join(authorities)
                    + f" in session {grant_used.session or self.session!r}"
                ),
                scope=grant_used.scope,
                rule_text="; ".join(authorities),
            )
        return self._finish(
            base,
            decision=Decision.ALLOW.value,
            reason_kind="rule",
            reason="every segment is covered by a persisted rule: " + "; ".join(authorities),
            rule_text="; ".join(authorities),
        )

    def _finish(
        self,
        base: Mapping[str, Any],
        *,
        decision: str,
        reason_kind: str,
        reason: str,
        rule: PrefixRule | None = None,
        scope: str = "",
        banned_prefix: str = "",
        rule_text: str = "",
    ) -> Verdict:
        """Apply the policy override, decide the exit code, and settle the reason text."""
        effective = decision
        text = reason
        if decision == Decision.PROMPT.value and self.policy == "never":
            effective = Decision.FORBID.value
            text = (
                reason + "; the approval policy is 'never', so a prompt cannot be shown and the "
                "command is rejected outright"
            )
        blocked = effective != Decision.ALLOW.value
        exit_code = 0
        if effective == Decision.FORBID.value:
            exit_code = 3
        elif effective == Decision.PROMPT.value:
            exit_code = 3  # headless: return blocked, never block forever, never auto-allow
        verdict = Verdict(
            decision=effective,
            reason_kind=reason_kind,
            reason=text,
            rule_id=rule.rule_id if rule else "",
            rule_text=rule.rule_text() if rule else rule_text,
            scope=scope,
            banned_prefix=banned_prefix,
            blocked=blocked,
            exit_code=exit_code,
            **base,
        )
        recorded = self.evidence.record(
            {
                "kind": "judgement",
                "command": verdict.command,
                "decision": verdict.decision,
                "reason_kind": verdict.reason_kind,
                "reason": verdict.reason,
                "rule_id": verdict.rule_id,
                "rule_text": verdict.rule_text,
                "scope": verdict.scope,
                "head": verdict.head,
                "banned_prefix": verdict.banned_prefix,
                "rule_eligible": verdict.rule_eligible,
                "ineligible_reasons": list(verdict.ineligible_reasons),
                "segments": [list(segment) for segment in verdict.segments],
                "session": verdict.session,
                "profile": {"name": verdict.profile_name, "sha256": verdict.profile_sha256},
                "enforcement": {
                    "mechanism": verdict.enforcement.get("mechanism", ""),
                    "controls": dict(verdict.enforcement.get("controls") or {}),
                    "unenforced": list(verdict.enforcement.get("unenforced") or ()),
                },
                "policy": verdict.policy,
                "interactive": verdict.interactive,
                "actor": "runtime",
                "exit_code": verdict.exit_code,
            }
        )
        return replace(verdict, evidence_ref=str(recorded.get("evidence_ref", "")))

    # ------------------------------------------------------------- decisions

    def approve(
        self,
        command: str,
        *,
        scope: str = GrantScope.TURN.value,
        review: str = "",
        persist_rule: bool = False,
        actor: str = "user",
        reason: str = "",
    ) -> tuple[Grant | None, PrefixRule | None, dict[str, Any]]:
        """The user said yes. Grants a prefix (or persists a rule) and records the answer.

        ``persist_rule=True`` is ``ReviewDecision.APPROVED_RULE_AMENDMENT``: yes, **and** make
        it policy. A banned prefix is refused in both directions — raising
        :class:`BannedPrefixRefusal` — which is the only thing "banned" needs to mean.
        """
        parts = decompose(command)
        if not parts.segments:
            raise PolicyError(
                "cannot approve a command that does not decompose into segments: "
                + ("; ".join(parts.reasons) or "no segment found")
            )
        wanted = GrantScope(str(scope))
        decision = str(
            review
            or (
                ReviewDecision.APPROVED_RULE_AMENDMENT.value
                if persist_rule
                else ReviewDecision.APPROVED_FOR_SESSION.value
                if wanted is GrantScope.SESSION
                else ReviewDecision.APPROVED.value
            )
        )
        prefix = parts.segments[0]
        grant: Grant | None = None
        rule: PrefixRule | None = None
        if persist_rule:
            rule = self.store.add_rule(
                prefix, Decision.ALLOW.value, note=reason, source="amendment"
            )
        else:
            grant = self.grants.add(prefix, wanted.value, command=command, actor=actor)
        record = self.evidence.record(
            {
                "kind": "approval",
                "command": command,
                "decision": "allow",
                "review_decision": decision,
                "scope": wanted.value if grant else "persisted_rule",
                "prefix": list(prefix),
                "rule_id": rule.rule_id if rule else "",
                "rule_text": rule.rule_text() if rule else (grant.to_dict() if grant else {}),
                "reason": reason,
                "session": self.session,
                "profile": {"name": self.profile.name, "sha256": self.profile.identity_sha256},
                "actor": actor,
                "rule_eligible": parts.rule_eligible,
                "exit_code": 0,
            }
        )
        if grant is not None:
            grant = replace(grant, evidence_ref=str(record.get("evidence_ref", "")))
        return grant, rule, record

    def deny(
        self,
        command: str,
        *,
        reason: str = "",
        persist_rule: bool = False,
        abort: bool = False,
        actor: str = "user",
    ) -> tuple[PrefixRule | None, dict[str, Any]]:
        """The user said no — with a reason, so the agent is taught instead of blocked.

        ``ReviewDecision.DENIED`` continues the turn with the reason (the "tell Codex what to
        do differently" rung); ``abort=True`` is ``ReviewDecision.ABORT`` and stops the turn.
        """
        parts = decompose(command)
        prefix = parts.segments[0] if parts.segments else (str(command).strip(),)
        rule: PrefixRule | None = None
        if persist_rule:
            rule = self.store.add_rule(
                prefix, Decision.FORBID.value, note=reason, source="denial"
            )
        decision = ReviewDecision.ABORT.value if abort else ReviewDecision.DENIED.value
        record = self.evidence.record(
            {
                "kind": "denial",
                "command": command,
                "decision": "forbid",
                "review_decision": decision,
                "prefix": list(prefix),
                "rule_id": rule.rule_id if rule else "",
                "rule_text": rule.rule_text() if rule else "",
                "reason": reason or "the user declined without a reason",
                "session": self.session,
                "profile": {"name": self.profile.name, "sha256": self.profile.identity_sha256},
                "actor": actor,
                "exit_code": 3,
            }
        )
        return rule, record

    def decide(self, command: str, *, interactive: bool | None = None) -> Verdict:
        """Judge and settle the headless contract.

        With ``interactive=False`` a prompt-required command does **not** block forever and
        does **not** auto-allow: it comes back as ``blocked`` (exit 3) with the deciding rule
        and what the user would need to allow. That is the honesty requirement, and it is the
        same code path for a ``--json`` run and a piped one.
        """
        was = self.interactive
        if interactive is not None:
            self.interactive = bool(interactive)
        try:
            return self.judge(command)
        finally:
            self.interactive = was


# ------------------------------------------------------------------- helpers


def grant_scope_from(value: str) -> GrantScope:
    try:
        return GrantScope(str(value))
    except ValueError as exc:
        raise PolicyError(
            f"unknown grant scope {value!r} (known: turn, session)"
        ) from exc


def policy_summary(
    store: RuleStore,
    ledger: GrantLedger,
    profile: PermissionProfile | None = None,
) -> dict[str, Any]:
    """One payload for ``approval list``: rules, bans, grants, counts — and "under what?".

    ``profile`` is optional so an existing caller keeps working; when it is given, the payload
    also answers which mechanism the ladder's answers will actually run under, which is the
    question a reader of the rules is really asking.
    """
    payload: dict[str, Any] = {
        "policy": store.to_dict(),
        "grants": ledger.to_dict(),
        "grants_note": (
            "turn grants live only in this process; session grants are persisted per session id "
            "and are not inherited by another session"
        ),
    }
    if profile is not None:
        payload["enforcement"] = enforcement_snapshot(profile)
    return payload


__all__ = [
    "APPROVAL_POLICIES",
    "BANNED_REASON_DEFAULT",
    "BANNED_REASONS",
    "CONTROL_OPERATORS",
    "DEFAULT_BANNED_PREFIXES",
    "Decision",
    "EVENTS_SCHEMA",
    "EvidenceLedger",
    "enforcement_snapshot",
    "Grant",
    "GrantLedger",
    "GrantScope",
    "PolicyError",
    "PrefixRule",
    "REDIRECTION_OPERATORS",
    "ReviewDecision",
    "RuleStore",
    "SCHEMA_VERSION",
    "SUBSTITUTION_CHARS",
    "TOOL_CLASSIFICATION_DECISIONS",
    "Verdict",
    "ApprovalEngine",
    "BannedPrefixRefusal",
    "Decomposition",
    "banned_reason",
    "decision_from_tool_classification",
    "decompose",
    "events_path",
    "grant_scope_from",
    "grants_path",
    "is_banned_prefix",
    "policy_path",
    "policy_summary",
    "render_prefix",
]
