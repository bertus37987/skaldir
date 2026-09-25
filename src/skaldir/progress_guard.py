"""PROGRESS-GUARD — durable progress signals, the stuck decision, the retry gate.

Three things a long run needs and this runtime used to lack:

1. **Durable progress signals.** The run record carried ``retry_count`` and
   ``last_progress_at`` and nothing else, so a resumed run could not tell that it
   had been *stuck*: it knew time had passed, not that the answer had not changed.
   :class:`ProgressSignals` is the per-turn set that fixes it —
   ``no_progress_streak``, ``no_tool_streak``, ``last_verification``,
   ``reply_fingerprint`` plus the turn/budget state — and it is persisted in the
   run record (:mod:`skaldir.run_supervisor`), not only in memory.

2. **No-progress as a runtime DECISION, not a log line.** The rule, in one
   sentence: the *same answer again* (fingerprint) **and** *no tool calls*
   **and** *no new evidence* **and** *no state delta* means the agent is stuck.
   :meth:`ProgressGuard.observe` returns a :class:`ProgressDecision` that names
   the four conditions it checked, the signal it matched (the runtime already
   declares ``pattern_no_new_evidence`` / ``pattern_retry_without_strategy_change``
   / ``pattern_search_without_progress`` in :mod:`skaldir.events` — this is what
   makes them decisions) and the **action** the runtime must take:
   ``strategy_change_required`` (a different tool/approach, delivered as a real
   directive in the conversation) or ``stop`` with a **named** reason. Never
   ``continue`` on the same answer forever.

   The decision is *recorded* — the run record names the action taken — so a
   resumed run can see it was already told to change strategy and stop instead of
   re-arming the same nudge.

3. **Retry only before the visible commit point.** :class:`CommitPoint` is the
   line: *no tool call executed* and *no user-visible output emitted*. While
   nothing is behind that line a provider retry (disconnect, 5xx) is safe and
   costs the user nothing. Once anything is behind it — a delta already went to
   the terminal, or a tool really ran — a blind retry would either duplicate the
   text on screen or run the tool **twice**. :class:`RetryGate` refuses that and
   turns it into an explicit re-plan, and the case is named in the record.

Nothing in this module calls a provider, a tool or a store: it decides, and the
loop acts. That keeps the rule testable without a network and keeps the loop the
only place that can execute anything.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "ACTION_CONTINUE",
    "ACTION_STRATEGY_CHANGE",
    "ACTION_STOP",
    "ACTION_RETRY",
    "ACTION_REPLAN",
    "ACTION_REFUSE",
    "SIGNAL_NO_NEW_EVIDENCE",
    "SIGNAL_RETRY_WITHOUT_STRATEGY_CHANGE",
    "SIGNAL_SEARCH_WITHOUT_PROGRESS",
    "STUCK_REASON_NO_PROGRESS",
    "STUCK_REASON_NO_STRATEGY_CHANGE",
    "STUCK_REASON_VISIBLE_COMMIT",
    "CommitPoint",
    "ProgressDecision",
    "ProgressGuard",
    "ProgressSignals",
    "RetryDecision",
    "RetryGate",
    "TurnObservation",
    "reply_fingerprint",
]

# --- the signal vocabulary: the kinds the event store already declares -------- #

#: same answer, no tool call, no new evidence, no state delta
SIGNAL_NO_NEW_EVIDENCE = "pattern_no_new_evidence"
#: a repeated/retried attempt whose strategy (tool set, arguments shape) did not change
SIGNAL_RETRY_WITHOUT_STRATEGY_CHANGE = "pattern_retry_without_strategy_change"
#: the same search/read repeated with no new result set
SIGNAL_SEARCH_WITHOUT_PROGRESS = "pattern_search_without_progress"

SIGNALS = (
    SIGNAL_NO_NEW_EVIDENCE,
    SIGNAL_RETRY_WITHOUT_STRATEGY_CHANGE,
    SIGNAL_SEARCH_WITHOUT_PROGRESS,
)

# --- the actions the runtime may take ---------------------------------------- #

#: keep going: real progress was observed
ACTION_CONTINUE = "continue"
#: the agent must change approach; the runtime delivers a real directive
ACTION_STRATEGY_CHANGE = "strategy_change_required"
#: stop, with a named reason — never loop on the same answer
ACTION_STOP = "stop"

# --- named stop/inspection reasons (a name, never a bare False) --------------- #

STUCK_REASON_NO_PROGRESS = "no_progress"
STUCK_REASON_NO_STRATEGY_CHANGE = "no_strategy_change_after_directive"
STUCK_REASON_VISIBLE_COMMIT = "visible_commit"

# --- the retry vocabulary ---------------------------------------------------- #

ACTION_RETRY = "retry"
ACTION_REPLAN = "replan"
ACTION_REFUSE = "refuse"

_WHITESPACE = re.compile(r"\s+")

#: How much of an answer the fingerprint reads. The *point* of the fingerprint is
#: "the same answer again", and a long answer that differs only past this point
#: is, for the purpose of the loop, the same answer.
FINGERPRINT_CHARS = 4000


def _normalise(text: str) -> str:
    """Case- and whitespace-insensitive: two spellings of one answer are one answer."""
    return _WHITESPACE.sub(" ", str(text or "")).strip().lower()


def reply_fingerprint(text: str, *, limit: int = FINGERPRINT_CHARS) -> str:
    """A stable digest of an answer, for "is this the same answer again?".

    Empty text hashes to the empty string, not to the digest of ``""``: "the model
    said nothing" and "the model said something" must never collide, and a caller
    that tests truthiness must get an honest ``""``.
    """
    normalised = _normalise(text)[:limit]
    if not normalised:
        return ""
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()


def _digest(value: Any) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, str):
        blob = value
    else:
        blob = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    if not blob:
        return ""
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# 1 — the durable signals                                                      #
# --------------------------------------------------------------------------- #


@dataclass
class ProgressSignals:
    """The per-turn progress state of a run — small, serde-safe, and persisted.

    ``turn`` and the budget fields travel with it so a resumed run knows *which*
    turn it was stuck on and how much budget was left when it got there.
    """

    turn: int = 0
    no_progress_streak: int = 0
    no_tool_streak: int = 0
    last_verification: str = ""
    reply_fingerprint: str = ""
    #: the action the guard last demanded (``continue``/``strategy_change_required``/``stop``)
    last_action: str = ""
    #: the signal that last matched (a named pattern, or ``""``)
    last_signal: str = ""
    #: budget state at that turn: tokens spent, and the declared budget (0 = unbounded)
    tokens_used: int = 0
    token_budget: int = 0
    steps: int = 0
    max_steps: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "turn": int(self.turn),
            "no_progress_streak": int(self.no_progress_streak),
            "no_tool_streak": int(self.no_tool_streak),
            "last_verification": str(self.last_verification or ""),
            "reply_fingerprint": str(self.reply_fingerprint or ""),
            "last_action": str(self.last_action or ""),
            "last_signal": str(self.last_signal or ""),
            "tokens_used": int(self.tokens_used),
            "token_budget": int(self.token_budget),
            "steps": int(self.steps),
            "max_steps": int(self.max_steps),
        }

    @classmethod
    def from_dict(cls, payload: Any) -> ProgressSignals:
        """Tolerant reader: a legacy or partial record yields defaults, never a raise.

        Reads both spellings, because the *run record* stores these under the column
        names (``progress_action``/``progress_signal``) while the guard names them
        ``last_action``/``last_signal``. One reader for both means a resumed run can
        hand its own record straight back to the guard.
        """
        if not isinstance(payload, dict):
            return cls()
        out = cls()
        out.turn = _int(payload.get("turn"))
        out.no_progress_streak = _int(payload.get("no_progress_streak"))
        out.no_tool_streak = _int(payload.get("no_tool_streak"))
        out.tokens_used = _int(payload.get("tokens_used"))
        out.token_budget = _int(payload.get("token_budget"))
        out.steps = _int(payload.get("steps"))
        out.max_steps = _int(payload.get("max_steps"))
        out.last_verification = str(payload.get("last_verification") or "")
        out.reply_fingerprint = str(payload.get("reply_fingerprint") or "")
        out.last_action = str(
            payload.get("last_action", payload.get("progress_action")) or ""
        )
        out.last_signal = str(
            payload.get("last_signal", payload.get("progress_signal")) or ""
        )
        return out


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


# --------------------------------------------------------------------------- #
# 2 — the decision                                                             #
# --------------------------------------------------------------------------- #


@dataclass
class TurnObservation:
    """What one turn really produced — the *only* material the rule may read.

    Every field is evidence, not opinion: ``answer_text`` is what the model said,
    ``tool_calls``/``executed`` are what the runtime ran, ``evidence`` is a digest
    of new observation the turn produced (a check verdict, an output digest — the
    caller decides what counts), and ``state`` is a digest of the durable state
    delta (revision, workspace fingerprint, criterion statuses).
    """

    turn: int = 0
    answer_text: str = ""
    tool_calls: tuple[str, ...] = ()
    executed: int = 0
    #: digest of the new evidence this turn produced; ``""`` = none
    evidence: str = ""
    #: digest of the durable state delta this turn produced; ``""`` = unchanged
    state: str = ""
    #: the named verification outcome of this turn (``PASS``/``FAIL``/``INCOMPLETE``/``""``)
    verification: str = ""
    tokens_used: int = 0
    token_budget: int = 0
    steps: int = 0
    max_steps: int = 0

    @property
    def tools(self) -> int:
        return len(self.tool_calls)


@dataclass
class ProgressDecision:
    """What the runtime must do about this turn — named, and complete on the wire."""

    stuck: bool
    signal: str
    action: str
    reason: str
    signals: ProgressSignals
    conditions: dict[str, bool] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def directive_needed(self) -> bool:
        return self.action == ACTION_STRATEGY_CHANGE

    @property
    def stop(self) -> bool:
        return self.action == ACTION_STOP

    def to_dict(self) -> dict[str, Any]:
        return {
            "stuck": bool(self.stuck),
            "signal": self.signal,
            "action": self.action,
            "reason": self.reason,
            "conditions": dict(self.conditions),
            "signals": self.signals.to_dict(),
            "notes": list(self.notes),
        }

    def to_wire(self) -> dict[str, Any]:
        return self.to_dict()


#: The directive the runtime injects when it demands a strategy change. It is a
#: *real* turn in the conversation (a user message), not a log line: the model is
#: told, with the evidence, what it must do differently.
STRATEGY_CHANGE_DIRECTIVE = (
    "Progress guard: your last answer is the same as the one before it, you called no "
    "tool, produced no new evidence and changed no state. Repeating it will not move "
    "the goal. Do something different NOW: name a different tool or approach, take a "
    "concrete step you have not taken yet, or report a named blocker (what you cannot "
    "do and what you need). Answering the same way again stops the run."
)


class ProgressGuard:
    """Turns per-turn observations into progress signals and one named decision.

    ``redemand_after`` is the second threshold: the guard *asks* for a strategy
    change on the first stuck turn and **stops** if the very next turn is stuck
    again in the same way. That is deliberate — a directive nobody obeyed is
    evidence that the run cannot proceed, and continuing would spend the budget to
    prove it a third time.
    """

    def __init__(
        self,
        *,
        enabled: bool = True,
        stop_after: int = 1,
    ) -> None:
        self.enabled = bool(enabled)
        #: how many *demands* may be made before the guard stops the run.
        self.stop_after = max(1, int(stop_after))
        self.signals = ProgressSignals()
        self._demands = 0
        self._previous: TurnObservation | None = None

    # -- projection -------------------------------------------------------- #

    def state(self) -> ProgressSignals:
        return self.signals

    def restore(self, durable: Any) -> dict[str, Any]:
        """Adopt the durable signals of an interrupted run (PERSIST, not decoration).

        This is the half that makes the signals *worth persisting*: a run that was told
        "change strategy — this is the same answer again" and then crashed must come back
        knowing it was told. Without it the resumed process re-arms the identical nudge,
        spends the identical budget and stalls in the identical place — the write without
        the read. ``_demands`` is restored too, so a second stall in the same way stops
        the run exactly as it would have in the process that was killed.
        """
        payload = durable.to_dict() if hasattr(durable, "to_dict") else durable
        restored = ProgressSignals.from_dict(payload)
        self.signals = restored
        stalled_before = bool(int(restored.no_progress_streak or 0) >= 1)
        if restored.last_action == ACTION_STRATEGY_CHANGE or stalled_before:
            # the directive already went out once; it is not sent a second time
            self._demands = max(1, self.stop_after)
        elif restored.last_action == ACTION_STOP:
            self._demands = self.stop_after + 1
        else:
            self._demands = 0
        self._previous = None
        return {
            "restored": True,
            "turn": int(restored.turn),
            "no_progress_streak": int(restored.no_progress_streak),
            "no_tool_streak": int(restored.no_tool_streak),
            "reply_fingerprint": str(restored.reply_fingerprint),
            "last_verification": str(restored.last_verification),
            "last_action": str(restored.last_action),
            "last_signal": str(restored.last_signal),
            "demands_restored": int(self._demands),
            "source": "the durable run record (skaldir.runs_store.RunsStore.progress)",
        }

    # -- the rule ---------------------------------------------------------- #

    def observe(self, observation: TurnObservation) -> ProgressDecision:
        """Read one turn and decide. Never raises: a guard must not break a run."""
        if not self.enabled:
            self.signals.turn = observation.turn
            return ProgressDecision(
                stuck=False,
                signal="",
                action=ACTION_CONTINUE,
                reason="the progress guard is disabled for this run",
                signals=self.signals,
            )

        fingerprint = reply_fingerprint(observation.answer_text)
        previous = self._previous
        signals = self.signals

        # the four conditions, each one *measured* — reported whether or not they fire
        #
        # "The same answer again" is measured against the previous turn when this process
        # saw it, and against the **durable fingerprint** when it did not (a resumed run):
        # without that fallback the very turn after a resume could not be compared to
        # anything, so a run killed right after a strategy-change directive came back and
        # was nudged with the identical directive all over again.
        durable_fingerprint = str(signals.reply_fingerprint or "")
        prior_answer = (
            reply_fingerprint(previous.answer_text) if previous is not None else durable_fingerprint
        )
        same_answer = bool(fingerprint) and bool(prior_answer) and (fingerprint == prior_answer)
        no_tools = observation.tools == 0
        no_new_evidence = not observation.evidence or (
            previous is not None and observation.evidence == previous.evidence
        )
        no_state_delta = not observation.state or (
            previous is not None and observation.state == previous.state
        )
        conditions = {
            "same_answer": bool(same_answer),
            "no_tool_calls": bool(no_tools),
            "no_new_evidence": bool(no_new_evidence),
            "no_state_delta": bool(no_state_delta),
        }
        stuck = same_answer and no_tools and no_new_evidence and no_state_delta

        # -- the signals (persisted, and the reason a resumed run is not blind) --
        if same_answer:
            signals.no_progress_streak += 1
        else:
            signals.no_progress_streak = 0
        if no_tools:
            signals.no_tool_streak += 1
        else:
            signals.no_tool_streak = 0
        signals.reply_fingerprint = fingerprint
        signals.turn = observation.turn
        signals.tokens_used = observation.tokens_used
        signals.token_budget = observation.token_budget
        signals.steps = observation.steps
        signals.max_steps = observation.max_steps
        if observation.verification:
            signals.last_verification = str(observation.verification)

        if not stuck:
            # A turn that did anything real clears the demand counter: the guard is about a
            # *stall*, not about how many times the run has been nudged. The one case that
            # keeps the demand standing is "the same answer again, with no tool call":
            # that is the nudge being ignored, and re-arming it would spend the same
            # budget to re-prove the same stall (the resumed run especially, where the
            # demand was restored from the durable record).
            if not (same_answer and no_tools):
                self._demands = 0
            signals.last_action = ACTION_CONTINUE
            signals.last_signal = ""
            self._previous = observation
            return ProgressDecision(
                stuck=False,
                signal="",
                action=ACTION_CONTINUE,
                reason="this turn produced tool activity, new evidence or a state change",
                signals=signals,
                conditions=conditions,
            )

        self._demands += 1
        signal = self._signal_for(observation)
        notes: list[str] = []
        if observation.verification and observation.verification == signals.last_verification:
            notes.append(
                f"the same verification verdict ({observation.verification}) was reported "
                "again with no new run behind it"
            )
        self._previous = observation

        if self._demands > self.stop_after:
            signals.last_action = ACTION_STOP
            signals.last_signal = signal
            return ProgressDecision(
                stuck=True,
                signal=signal,
                action=ACTION_STOP,
                reason=STUCK_REASON_NO_STRATEGY_CHANGE,
                signals=signals,
                conditions=conditions,
                notes=notes
                + [
                    f"a strategy change was demanded {self._demands - 1} time(s) and the "
                    "same answer came back — stopping, because looping spends budget to "
                    "re-prove the same stall",
                ],
            )

        signals.last_action = ACTION_STRATEGY_CHANGE
        signals.last_signal = signal
        return ProgressDecision(
            stuck=True,
            signal=signal,
            action=ACTION_STRATEGY_CHANGE,
            reason=STUCK_REASON_NO_PROGRESS,
            signals=signals,
            conditions=conditions,
            notes=notes,
        )

    def _signal_for(self, observation: TurnObservation) -> str:
        """Which declared pattern this stall really is (named, never guessed)."""
        if self._demands > self.stop_after:
            return SIGNAL_RETRY_WITHOUT_STRATEGY_CHANGE
        if observation.tools and all(
            name in _SEARCH_TOOLS for name in observation.tool_calls
        ):
            return SIGNAL_SEARCH_WITHOUT_PROGRESS
        return SIGNAL_NO_NEW_EVIDENCE


#: Tools whose whole job is to look something up. A stall made only of these is a
#: *search* that finds nothing new, which the runtime names separately so the
#: directive can be about the query rather than about "stop repeating yourself".
_SEARCH_TOOLS = frozenset(
    {
        "search",
        "search_files",
        "grep",
        "find",
        "read",
        "read_file",
        "list",
        "list_dir",
        "web_search",
        "fetch",
        "web_fetch",
    }
)


# --------------------------------------------------------------------------- #
# 3 — the retry gate                                                           #
# --------------------------------------------------------------------------- #


@dataclass
class CommitPoint:
    """The line a retry must stay behind: nothing visible, nothing executed.

    ``visible_output`` is set the moment a content delta (or any user-visible line
    of this attempt) has been emitted; ``tool_executed`` the moment a tool call has
    really run. Either one makes a blind retry unsafe:

    * ``visible_output`` — the provider would stream the answer again and the user
      would watch it be written twice;
    * ``tool_executed`` — the tool would run a **second time**, which is exactly the
      double effect the operation ledger exists to prevent.
    """

    turn: int = 0
    attempt: int = 0
    visible_output: bool = False
    tool_executed: bool = False
    visible_chars: int = 0

    @property
    def committed(self) -> bool:
        return bool(self.visible_output or self.tool_executed)

    @property
    def blocker(self) -> str:
        parts = []
        if self.visible_output:
            parts.append(f"user-visible output was already emitted ({self.visible_chars} chars)")
        if self.tool_executed:
            parts.append("a tool call has already executed")
        return " and ".join(parts)

    def to_wire(self) -> dict[str, Any]:
        return {
            "turn": int(self.turn),
            "attempt": int(self.attempt),
            "visible_output": bool(self.visible_output),
            "visible_chars": int(self.visible_chars),
            "tool_executed": bool(self.tool_executed),
            "committed": bool(self.committed),
            "blocker": self.blocker,
        }


@dataclass
class RetryDecision:
    """Whether a retry may be attempted, and what to do instead when it may not."""

    retry: bool
    action: str
    reason: str
    attempt: int
    max_attempts: int
    commit: dict[str, Any] = field(default_factory=dict)
    replan: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "retry": bool(self.retry),
            "action": self.action,
            "reason": self.reason,
            "attempt": int(self.attempt),
            "max_attempts": int(self.max_attempts),
            "commit": dict(self.commit),
            "replan": self.replan,
        }

    to_wire = to_dict


#: The explicit re-plan text used when a retry is refused past the commit point.
#: It is a *named* continuation, not a silent stop: the runtime tells the model
#: (and the record) exactly what was refused and why.
REPLAN_AFTER_COMMIT = (
    "the provider call failed after this attempt had already committed "
    "({blocker}) — a blind retry would {risk}. Re-plan instead: state what you have "
    "already done and continue from there, without repeating it."
)


class RetryGate:
    """Decides whether a provider retry may happen, and refuses past the commit point.

    The gate is deliberately tiny and *stateful per attempt*: :meth:`begin` opens an
    attempt, the loop feeds it the two facts it alone can know (a delta went out, a
    tool ran), and :meth:`decide` answers the provider. A gate that is never told
    anything still answers conservatively — an attempt is retryable by default,
    because a retry that happens *before* anything committed is free.
    """

    def __init__(self, *, max_attempts: int = 1) -> None:
        #: attempts **after** the first, so ``1`` = one retry
        self.max_attempts = max(0, int(max_attempts))
        self.commit = CommitPoint()
        self.refusals = 0
        self.retries = 0
        self._last: RetryDecision | None = None

    # -- the loop's side --------------------------------------------------- #

    def begin(self, *, turn: int, attempt: int = 1) -> CommitPoint:
        """Open an attempt: the commit point is *per attempt*, by construction.

        The previous attempt's decision is dropped with its commit point: a
        ``last()`` from an earlier step must never be read as this step's verdict.
        """
        self.commit = CommitPoint(turn=turn, attempt=attempt)
        self._last = None
        return self.commit

    def note_visible_output(self, chars: int = 0) -> None:
        self.commit.visible_output = True
        self.commit.visible_chars += max(0, int(chars))

    def note_tool_executed(self, count: int = 1) -> None:
        if int(count) > 0:
            self.commit.tool_executed = True

    # -- the provider's side ----------------------------------------------- #

    def allow_retry(self, *, attempt: int = 1) -> tuple[bool, str]:
        """The hook a provider client consults before re-sending a request.

        ``attempt`` is **the number of attempts already made on this step** (the
        provider's own counter: the call that just failed). One unit, one meaning — the
        earlier version let the two call sites disagree, so a refusal recorded by the
        provider could be re-recorded by the loop as an allowance.

        Returns ``(allowed, reason)``. Cheap, allocation-free and honest: past the
        commit point it refuses and the reason says which of the two blockers fired.
        """
        if self.commit.committed:
            return False, f"refused past the commit point: {self.commit.blocker}"
        if int(attempt) > self.max_attempts:
            return False, (
                f"refused: {int(attempt)} attempt(s) already made and this attempt "
                f"allows {self.max_attempts} retry/retries"
            )
        return True, "nothing has been committed on this attempt"

    def effective_attempts(self, provider_max_attempts: int) -> dict[str, Any]:
        """Reconcile the two budgets instead of letting them contradict each other.

        The provider brings its own §43 ceiling (``max_retries + 1`` total attempts) and
        this gate brings the runtime's (``max_attempts`` retries). The gate may only
        **narrow** the provider's policy — never widen it — so the number of attempts a
        step can really make is ``min(provider_max_attempts, max_attempts + 1)``. Both
        numbers and the outcome are reported, so a run's record says which budget bound
        it instead of leaving two prose claims to argue.
        """
        provider = max(1, int(provider_max_attempts))
        gate_ceiling = max(1, int(self.max_attempts) + 1)
        return {
            "provider_max_attempts": provider,
            "gate_max_retries": int(self.max_attempts),
            "gate_ceiling_attempts": gate_ceiling,
            "effective_attempts": min(provider, gate_ceiling),
            "narrowed_by": (
                "the provider's §43 policy"
                if provider <= gate_ceiling
                else "the runtime's retry gate"
            ),
            "commit_point_blocks_every_retry": True,
        }

    def decide(self, *, attempt: int = 1, error: str = "") -> RetryDecision:
        """The explicit record of the retry decision (retry / refuse / re-plan).

        ``attempt`` is the number of attempts already made on this step — the same unit
        :meth:`allow_retry` takes. Exactly one of the two call sites should ask: the
        provider asks when it is about to re-send, and the loop reads
        :meth:`last` afterwards instead of asking again (a second ask would count a
        retry that never happened and a refusal that was already recorded).
        """
        allowed, reason = self.allow_retry(attempt=attempt)
        if allowed:
            self.retries += 1
            decision = RetryDecision(
                retry=True,
                action=ACTION_RETRY,
                reason=reason,
                attempt=int(attempt),
                max_attempts=self.max_attempts,
                commit=self.commit.to_wire(),
            )
        else:
            self.refusals += 1
            past_commit = self.commit.committed
            action = ACTION_REPLAN if past_commit else ACTION_REFUSE
            decision = RetryDecision(
                retry=False,
                action=action,
                reason=reason + (f" (after: {error})" if error else ""),
                attempt=int(attempt),
                max_attempts=self.max_attempts,
                commit=self.commit.to_wire(),
                replan=(
                    REPLAN_AFTER_COMMIT.format(
                        blocker=self.commit.blocker,
                        risk=(
                            "run a tool a second time"
                            if self.commit.tool_executed
                            else "write the same answer to the screen twice"
                        ),
                    )
                    if past_commit
                    else ""
                ),
            )
        self._last = decision
        return decision

    def last(self) -> RetryDecision | None:
        return self._last

    def decide_caller_retry(self, *, error: str = "", provider_attempts: int = 0) -> RetryDecision:
        """The decision when the **caller** wonders whether to re-run a failed call.

        By the time a provider failure reaches the caller, the provider's own attempt
        loop has already ended — it exhausted its §43 ceiling, or this gate refused a
        re-send. A caller-side "retry" is therefore never a retry: it would re-send a
        request the policy already gave up on. This refuses it **by name**, and the
        reason says which budget ended the attempt loop, so the run's record cannot claim
        a retry that never happened.

        Past the commit point the refusal becomes an explicit **re-plan** — the caller
        keeps the work but continues from what already happened, instead of duplicating
        a visible answer or a tool that really ran.
        """
        if self.commit.committed:
            risk = (
                "run a tool a second time"
                if self.commit.tool_executed
                else "write the same answer to the screen twice"
            )
            reason = f"refused past the commit point: {self.commit.blocker}"
            action = ACTION_REPLAN
            replan = REPLAN_AFTER_COMMIT.format(blocker=self.commit.blocker, risk=risk)
        else:
            reason = (
                "refused: the provider's own attempt loop already ended after "
                f"{max(0, int(provider_attempts))} attempt(s) — §43 / this gate owns "
                "re-sending, and a caller-side send would be a second request"
            )
            action = ACTION_REFUSE
            replan = ""
        self.refusals += 1
        decision = RetryDecision(
            retry=False,
            action=action,
            reason=reason + (f" (after: {error})" if error else ""),
            attempt=max(1, int(provider_attempts) or 1),
            max_attempts=int(self.max_attempts),
            commit=self.commit.to_wire(),
            replan=replan,
        )
        self._last = decision
        return decision

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_attempts": int(self.max_attempts),
            "retries": int(self.retries),
            "refusals": int(self.refusals),
            "commit": self.commit.to_wire(),
            "last": self._last.to_dict() if self._last else None,
        }
