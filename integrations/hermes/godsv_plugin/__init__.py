"""Skaldir Agent — Hermes plugin adapter (V1: shadow default, whisper optional,
optional async LLM enrichment via the host-owned side-LLM).

Mirrors the live Hermes trajectory into the Skaldir Agent event store, runs the
deterministic monitor + voice, retrieves evidence from incident/note memory
for hints, and records everything. Delivery modes (env ``GODSV_MODE``, read
per call, default ``shadow``):

- ``shadow`` — hints are logged to ``inbox.jsonl`` only; nothing enters the
  conversation. Safe default for first deployments and validation.
- ``whisper`` — hints are appended to tool results via the
  ``transform_tool_result`` hook: append-only, cache-safe, no synthetic turn
  (design record: ``integrations/hermes/README.md``). Observed ordering on the
  executor path: transform fires *before* ``post_tool_call`` for the same
  call, so a hint raised by call N rides call N+1's result — still mid-loop,
  still cache-safe.
- ``off`` — the adapter does nothing (kill switch).

Optional LLM enrichment (``GODSV_LLM=1``, always on a **daemon thread** — the
hook never waits for the model; live runs showed host-side calls can exceed
20s despite a ``timeout`` kwarg, advisory in v0.21.1). All calls go through
the host-owned side-LLM ``ctx.llm`` (no overrides requested → no capability
needed; host-audited as ``purpose="godsv:<prompt_id>"``):

1. **Triage gate** (``GODSV_TRIAGE=1``, kinds: ``loop``): a not-stuck verdict
   with high confidence *suppresses* the hint (recorded as ``hint_triage`` /
   ``hint_suppressed`` events + inbox entry). Fail-safe: any parse/failure
   keeps the hint. Never guesses "safe" without an explicit ``stuck`` verdict.
2. **Evidence judge:** fuzzy (non-exact, non-untrusted) incident matches are
   confirmed/dropped via ``same_problem_judge`` before they enter the hint.
   Fail-open on errors; outcome recorded as ``evidence_judged``.
3. **Synthesis:** the template text of an evidence-backed hint is upgraded
   via ``voice_synthesis`` when it finishes before the next delivery
   opportunity; otherwise the template goes out ("upgrade missed" when the
   model answers later). ``GODSV_LLM_GRACE`` (default 12s) only bounds
   shadow-mode promotion; session end flushes.

Threading: Hermes invokes hooks on bounded worker threads; the event store is
thread-safe, per-session work is serialized with an RLock (including event
appends from enrichment threads), and synthesis threads only touch their own
hint under that lock.

Debug: with ``GODSV_DEBUG_TRACE=1``, load/registration/observe events are
appended to ``$GODSV_TRACE_FILE`` (default: <tmpdir>/godsv_plugin_trace.log).

Deployment: copy/symlink this package to ``~/.hermes/plugins/godsv/``; the
``godsv`` core must be importable in the Hermes environment
(``pip install -e /path/to/gods-voice`` into Hermes' venv). Per
``plugins/AGENTS.md``: callbacks accept ``**kwargs`` and never raise — Hermes
logs and skips failing callbacks.
"""

import hashlib
import json
import logging
import os
import re

# --- product package resolution (defect fix, measured in the built app) ---------
# The engine process that loads this plugin runs in the Hermes engine venv, which does
# NOT contain `godsv`: the package lives in the product checkout (`src/godsv`). Until now
# that only worked when the launching shell happened to export PYTHONPATH. Without it the
# engine logged `Failed to load plugin 'godsv': No module named 'godsv'` and every panel
# route raised ModuleNotFoundError (measured in one app session: 636 module errors, 127
# HTTP 500s, 0 events recorded). Resolve the package from our own location — the plugin
# and the package ship from the same checkout; no ambient variable is trusted.
import sys as _sys
import tempfile
import threading
import time
from pathlib import Path
from pathlib import Path as _Path

for _parent in _Path(__file__).resolve().parents:
    _candidate = _parent / "src" / "godsv" / "__init__.py"
    if _candidate.is_file():
        _src = str(_candidate.parent.parent)
        if _src not in _sys.path:
            _sys.path.insert(0, _src)
        break
# -------------------------------------------------------------------------------

from godsv.events import EventStore, SecretLeakError
from godsv.guard import (
    GuardFinding,
    GuardVerdict,
    build_evidence_index,
    evaluate_completion,
    evidence_digest,
    extract_claims,
    judge_completion,
)
from godsv.incidents import IncidentStore
from godsv.inject import InjectionQueue, format_hint
from godsv.llm import BudgetTracker
from godsv.monitor import (
    SEV_WARN,
    Monitor,
    Signal,
    error_fingerprint,
    error_text,
)
from godsv.notes import NoteStore
from godsv.pact import Pact
from godsv.patterns import (
    SequenceAnalyzer,
    observe_call,
    observe_edit,
    observe_result,
)
from godsv.pause import PauseStore, VoicePause
from godsv.research import ResearchRunner
from godsv.retrieval import Evidence, find_evidence, judge_same_problem
from godsv.skills import SkillStore, consumption_hint, relevant_skills
from godsv.synthesis import synthesize_hint, triage_step
from godsv.toolcall import (
    ALLOW,
    ASK_APPROVAL,
    BLOCK,
    PAUSE_AND_INVESTIGATE,
    ToolDecision,
    ToolIntent,
    judge_tool_call,
)
from godsv.untrusted import UNTRUSTED_RULE
from godsv.voice import HealthyDebuggingRunner, Hint, Voice

logger = logging.getLogger(__name__)

MAX_PENDING_HINTS = 5
VALID_MODES = ("shadow", "whisper", "off")
SUPPRESSIBLE_KINDS = ("loop",)
TRIAGE_MIN_CONFIDENCE = 0.6
TRACE_FILE = os.environ.get("GODSV_TRACE_FILE") or str(
    Path(tempfile.gettempdir()) / "godsv_plugin_trace.log"
)

_SESSION_LOCK = threading.RLock()
_OBSERVE_LOCK = threading.RLock()
_CTX = None  # set by register(); used for optional ctx.llm access


def _trace(event: str) -> None:
    """Diagnostics only: append a line when ``GODSV_DEBUG_TRACE=1``."""
    if os.environ.get("GODSV_DEBUG_TRACE") != "1":
        return
    try:
        with open(TRACE_FILE, "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} pid={os.getpid()} {event}\n")
    except OSError:
        pass


def mode() -> str:
    """Current delivery mode from ``GODSV_MODE`` (default: shadow)."""
    value = (os.environ.get("GODSV_MODE") or "shadow").strip().lower()
    return value if value in VALID_MODES else "shadow"


def _synth_grace() -> float:
    """Seconds held hints may wait for the model in shadow mode (no drains)."""
    try:
        return float(os.environ.get("GODSV_LLM_GRACE", "12"))
    except ValueError:
        return 12.0


def _triage_enabled() -> bool:
    return os.environ.get("GODSV_TRIAGE") == "1"


def _guard_enabled() -> bool:
    return os.environ.get("GODSV_GUARD", "1") == "1"


def _guard_forced() -> bool:
    return os.environ.get("GODSV_GUARD_FORCE") == "1"


def _pause_enabled() -> bool:
    return os.environ.get("GODSV_PAUSE", "1") == "1"


def _interception_enabled() -> bool:
    """Block 4: judge a planned tool call before it executes (default on)."""
    return os.environ.get("GODSV_INTERCEPT", "1") == "1"


def _pause_wait() -> float:
    try:
        return min(20.0, max(0.0, float(os.environ.get("GODSV_PAUSE_WAIT", "6"))))
    except ValueError:
        return 6.0


def _pause_ttl() -> float:
    try:
        return max(10.0, float(os.environ.get("GODSV_PAUSE_TTL", "90")))
    except ValueError:
        return 90.0


class ContextLLM:
    """godsv ``LLMClient`` backed by Hermes' host-owned side-LLM (``ctx.llm``).

    No provider/model overrides are requested, so no plugin capability is
    required (fail-closed otherwise); every call is host-audited under
    ``purpose="godsv:<prompt_id>"``. Exceptions propagate — the callers fall
    back to template hints / keep the evidence.
    """

    name = "hermes-ctx-llm"

    def __init__(self, llm, *, timeout: float | None = None, max_tokens: int = 900) -> None:
        self._llm = llm
        if timeout is None:
            try:
                timeout = float(os.environ.get("GODSV_LLM_TIMEOUT", "8"))
            except ValueError:
                timeout = 8.0
        self._timeout = timeout
        self._max_tokens = max_tokens

    def complete(self, *, prompt_id: str, prompt: str, tier: str) -> str:
        messages = [{"role": "user", "content": prompt}]
        kwargs: dict = {
            "timeout": self._timeout,
            "max_tokens": self._max_tokens,
            "purpose": f"godsv:{prompt_id}",
        }
        override = self._role_override(prompt_id, tier)
        if override:
            kwargs["provider"], kwargs["model"] = override[0], override[1]
        try:
            result = self._llm.complete(messages, **kwargs)
        except Exception as exc:  # noqa: BLE001 — trust-gate fallback must be narrow
            trust_missed = override and "trust" in type(exc).__name__.lower()
            if not trust_missed:
                raise
            # capability not granted (plugins.entries.godsv.llm.allow_*_override):
            # run on Hermes' default resolution instead of failing the channel.
            _trace(f"llm role override refused for {prompt_id} — default model")
            kwargs.pop("provider", None)
            kwargs.pop("model", None)
            result = self._llm.complete(messages, **kwargs)
        return str(getattr(result, "text", "") or "")

    @staticmethod
    def _role_override(prompt_id: str, tier: str) -> tuple[str, str] | None:
        """Provider/model from the Skaldir Agent roles file, when configured."""
        try:
            from godsv.modelroles import resolve_for_call
        except Exception:  # pragma: no cover — packaging anomalies must not break calls
            return None
        try:
            resolved = resolve_for_call(prompt_id, tier)
        except Exception:  # pragma: no cover — malformed roles file
            return None
        return (resolved[0], resolved[1]) if resolved else None


def _llm_client() -> ContextLLM | None:
    """Build the ctx.llm-backed client when ``GODSV_LLM=1`` (read at session start)."""
    if os.environ.get("GODSV_LLM") != "1":
        return None
    if _CTX is None or getattr(_CTX, "llm", None) is None:
        _trace("GODSV_LLM=1 but ctx.llm unavailable — template hints only")
        return None
    return ContextLLM(_CTX.llm)


class GodsvSession:
    """Per-session state: event store, memory, monitor, voice, hint inbox."""

    def __init__(
        self, session_id: str, state_dir: Path, llm_client: ContextLLM | None = None
    ) -> None:
        self.session_id = session_id
        self.state_dir = state_dir
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.store = EventStore(state_dir / "events.db")
        self.incidents = IncidentStore(state_dir / "memory.db")
        self.notes = NoteStore(state_dir / "memory.db")
        # drift/progress tracking (P1-4) — deterministic, per session
        self._unrelated_edits = 0
        self._pending_claim_review = None
        self._steps_since_progress = 0
        self._progress_seen_updated = 0.0
        # §38/GS-04: the monitor's ``known_incident_repeat`` may only be fed
        # applicability-checked signatures. ``signatures()`` lists everything
        # ever recorded (archived, expired, never-reviewed) and would let a
        # stale entry raise a *strong* intervention — the retrieval rule
        # forbids exactly that. One word, same contract.
        self.monitor = Monitor(
            known_error_fingerprints=self.incidents.intervention_signatures()
        )
        self.voice = Voice()
        self.queue = InjectionQueue()
        self.pending: list[str] = []
        self.step = 0
        self.llm = llm_client
        self._triage = _triage_enabled()
        self._grace = _synth_grace()
        self._held: list[dict] = []
        self._synth_threads: list[threading.Thread] = []
        self._registry = None
        self._registry_failed = False
        # release features: skills consumption + completion guard + voice pause
        self.skills = SkillStore(state_dir / "memory.db")
        self._recent_tools: list[str] = []
        self._consumed_skills: set[str] = set()
        self._last_claim: tuple[int, str] | None = None
        self._claim_hashes: set[str] = set()
        self._error_fps: set[str] = set()
        # Block 4 — live control layer: sequence patterns over normalised
        # effects, the untrusted-content boundary, and pre-execution tool-call
        # interception. All three are advisory-or-fail-closed, never a second
        # runtime.
        self.patterns = SequenceAnalyzer()
        self._untrusted_directives: list[str] = []
        self._intercepted: set[str] = set()
        self._last_failure_class = ""
        self._intercept_enabled = _interception_enabled()
        self._guard_enabled = _guard_enabled()
        self._guard_forced = _guard_forced()
        self._guard_nudged_turn = False
        self._guard_cache: dict[str, GuardVerdict] = {}
        self._completion_nudges = 0
        self._last_strict = None
        self.started = False
        # §1.3 live capability surface (Block B5) — bound lazily at the first
        # model boundary (see _live_surface); None until then.
        self.live = None
        self._pact_cache: tuple[float, Pact | None] = (0.0, None)
        self._pause: VoicePause | None = None
        if _pause_enabled():
            from godsv.capability import CapabilityAwareRunner, ToolBuildRunner

            self._pause = VoicePause(
                PauseStore(state_dir / "pauses.db"),
                session_id,
                state_dir=state_dir,
                research_runner=HealthyDebuggingRunner(
                    primary=CapabilityAwareRunner(
                        primary=ResearchRunner(
                            store=self.store,
                            state_dir=self.state_dir,
                            incidents=self.incidents,
                            notes=self.notes,
                            repo_root=Path.cwd(),
                            client=llm_client,
                            registry=self._prompt_registry(),
                            budget=BudgetTracker(limits={"mid": 6000, "small": 4000}),
                        ),
                        # capability gaps run the autonomous tool builder in the pause
                        # §1.8: the same configured client the research runner above uses
                        tool_builder=ToolBuildRunner(runtime=self.state_dir, llm=llm_client),
                    ),
                    # Block 5: normal, recoverable debugging may close the pause
                    # without a redirect — read from this session's real events.
                    events_from=lambda sid: self.store.iter_session(sid),
                ),
                emit=self._emit,
                ttl=_pause_ttl(),
            )

    def _emit(self, kind: str, payload: dict) -> None:
        """Pause/research events (called from controller threads)."""
        try:
            self._record(kind, payload)
        except Exception as exc:
            _trace(f"emit {kind} failed: {type(exc).__name__}: {exc}")

    def begin_turn(self) -> None:
        """Per user turn reset (pre_llm_call fires once per turn)."""
        self._guard_nudged_turn = False
        self._completion_nudges = 0
        self._strict_crashed = False
        self._turn_started = time.time()

    def end(self) -> None:
        """Session teardown: release pauses, stop the watchdog."""
        if self._pause is not None:
            try:
                self._pause.release("session end")
                self._pause.stop()
            except Exception:
                pass

    def _maybe_file_edit(self, tool_name: str | None, args: dict | None) -> None:
        """Mirror write/patch tool calls into ``file_edit`` events.

        Slop, scope-drift and dependency checks run on ``file_edit`` events;
        without this mapping those detectors would never fire live.
        """
        if not isinstance(args, dict):
            return
        name = str(tool_name or "")
        if name in ("terminal", "run_terminal", "bash", "shell"):
            # Shell redirections/tee are edits too (F13): without this the
            # drift/slop detectors stay blind whenever the agent writes via
            # the terminal instead of write_file.
            command = str(args.get("command") or args.get("cmd") or "")
            targets = re.findall(r"(?:>>?|tee\s+(?:-a\s+)?)\s*([~./\w][^\s\"'<>|;)]*)", command)
            for target in list(dict.fromkeys(targets))[:4]:
                if target.startswith("/dev/"):
                    # stderr/stdout discards (`2>/dev/null`, `> /dev/stderr`) are
                    # not edits: they are not files. Live-found 13.09.: treating
                    # them as edits fed drift noise and advanced the evidence
                    # freshness checkpoint past every executed check.
                    continue
                self.observe("file_edit", {"path": target, "added": _truncate(command, 2000)})
            return
        path = str(args.get("path") or args.get("file_path") or "")
        if not path:
            return
        if name in ("write_file", "create_file", "write"):
            added = str(args.get("content") or "")
        elif name in ("patch", "apply_patch", "edit_file", "str_replace"):
            added = str(args.get("new_string") or args.get("content") or "")
        else:
            return
        self.observe("file_edit", {"path": path, "added": _truncate(added, 4000)})

    def _contract_progress_marker(self) -> float:
        """The record's `updated` timestamp — moves on verdicts/state edits."""
        try:
            return float(self._completion_store().state().get("updated") or 0.0)
        except Exception:
            return 0.0

    def _drift_file(self) -> Path:
        """Per-session drift counters, persisted next to the contract (F13)."""
        return Path(str(self._completion_store().state_path) + ".drift.json")

    def _drift_load(self) -> dict:
        try:
            import json as _json

            return _json.loads(self._drift_file().read_text(encoding="utf-8")) or {}
        except Exception:
            return {}

    def _drift_save(self, counters: dict) -> None:
        try:
            import json as _json

            path = self._drift_file()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(_json.dumps(counters), encoding="utf-8")
        except Exception:
            logger.debug("drift counter save failed", exc_info=True)

    def _drift_signals(self, kind: str, payload: dict) -> list:
        """Deterministic context-drift / progress-stall signals (P1-4).

        Conservative by construction: needs a live contract WITH a goal,
        warn-level only, deduped by fingerprint; the pause controller and
        the voice still decide what happens. Counters persist per session
        (F13) — drift is a multi-turn phenomenon and session objects die
        at turn boundaries.
        """
        try:
            state = self._completion_store().state()
        except Exception:
            return []
        goal = str(state.get("goal") or "").strip()
        if not goal:
            return []
        counters = self._drift_load()
        out = []
        if kind == "file_edit":
            path = str((payload or {}).get("path") or "")
            if path:
                from godsv.research import _terms

                contract_text = " ".join(
                    [goal]
                    + [str((item or {}).get("text") or "") for item in (state.get("dod") or [])]
                )
                terms = set(_terms(contract_text, limit=24))
                low = path.lower()

                def _shares_stem(a: str, b: str, n: int = 4) -> bool:
                    # word-stem overlap: 'auth' vs 'authentication' matches,
                    # 'docs' vs 'refactor' does not (conservative drift check)
                    shared = 0
                    for x, y in zip(a, b, strict=False):
                        if x != y:
                            break
                        shared += 1
                    return shared >= n

                tokens = [tok for tok in re.split(r"[^a-z0-9]+", low) if len(tok) >= 4]
                related = (
                    (not terms)
                    or any(term in low for term in terms)
                    or any(_shares_stem(tok, term) for tok in tokens for term in terms)
                )
                unrelated = int(counters.get("unrelated_edits") or 0)
                unrelated = 0 if related else unrelated + 1
                counters["unrelated_edits"] = unrelated
                self._unrelated_edits = unrelated
                if not related and unrelated >= 3:
                    _trace(f"drift signal: context_drift path={path}")
                    counters["unrelated_edits"] = 0  # one signal per streak
                    self._unrelated_edits = 0
                    out.append(
                        Signal(
                            kind="context_drift",
                            severity=SEV_WARN,
                            fingerprint=error_fingerprint(f"context_drift:{path}"),
                            message=(
                                f"{unrelated} edits in a row outside the goal's "
                                f"terms — latest: {path}"
                            ),
                            refs={"path": path},
                        )
                    )
        if kind in ("tool_call", "tool_result"):
            steps = int(counters.get("steps_since_progress") or 0) + 1
            seen = float(counters.get("progress_seen_updated") or 0.0)
            updated = float(state.get("updated") or 0.0)
            if updated > seen:
                seen = updated
                steps = 0
            counters["steps_since_progress"] = steps
            counters["progress_seen_updated"] = seen
            self._steps_since_progress = steps
            self._progress_seen_updated = seen
            if steps >= 40:
                _trace(f"drift signal: progress_stall events={steps}")
                out.append(
                    Signal(
                        kind="progress_stall",
                        severity=SEV_WARN,
                        fingerprint=error_fingerprint(f"progress_stall:{int(seen)}"),
                        message=(
                            f"{steps} tool events with no contract verdict/update — "
                            "high activity without goal progress"
                        ),
                    )
                )
        self._drift_save(counters)
        return out

    def _sequence_signals(self, kind: str, payload: dict) -> list:
        """Block 4: tool/command patterns over *sequences*, not strings.

        Feeds the normalised-effect analyzer and turns its hits into signals.
        Cosmetic differences collapse (``pytest -q`` ≡ ``python -m pytest``),
        exit codes are never used as identity, and every hit stays a signal —
        the pause controller owns escalation.
        """
        try:
            step = self.step
            hits: list = []
            if kind == "tool_call":
                hits = self.patterns.ingest(
                    observe_call(step, str(payload.get("name") or ""), payload.get("args") or {})
                )
            elif kind == "tool_result":
                error = error_text(payload)
                hits = self.patterns.ingest(
                    observe_result(
                        step,
                        failed=bool(error),
                        succeeded=not error,
                        error=error,
                        output=str(payload.get("output") or payload.get("stdout") or ""),
                        tool=str(payload.get("tool") or ""),
                    )
                )
            elif kind == "file_edit":
                hits = self.patterns.ingest(
                    observe_edit(
                        step,
                        str(payload.get("path") or ""),
                        str(payload.get("added") or ""),
                    )
                )
            if not hits:
                return []
            out = []
            for hit in hits:
                out.append(
                    Signal(
                        kind=f"pattern_{hit.pattern}",
                        severity=hit.severity,
                        fingerprint=hit.fingerprint,
                        message=hit.message,
                        refs={"pattern": hit.pattern, **hit.refs},
                    )
                )
            return out
        except Exception as exc:
            _trace(f"sequence signals failed: {type(exc).__name__}: {exc}")
            return []

    def _untrusted_signals(self, kind: str, payload: dict) -> list:
        """Block 4: read external content — signal, not verdict.

        Only three sources carry control authority (user/system/agent); every
        other text is data. A confirmed attempt is remembered as an untrusted
        directive so the pre-execution interception can refuse to *act* on it.
        """
        try:
            text, source = self._external_text(kind, payload)
            if not text:
                return []
            signals = self.monitor.ingest_untrusted(step=self.step, text=text, source=source)
            if not signals:
                return []
            signal = signals[0]
            self._record(
                signal.kind,
                {
                    "source": source,
                    "families": signal.refs.get("families", []),
                    "match": signal.refs.get("match", ""),
                    "line": signal.refs.get("line", ""),
                    "reason": signal.refs.get("reason", ""),
                    "severity": signal.severity,
                    "rule": UNTRUSTED_RULE,
                },
            )
            if signal.kind == "untrusted_instruction_attempt":
                line = str(signal.refs.get("line") or signal.refs.get("match") or "")
                if line and line not in self._untrusted_directives:
                    self._untrusted_directives.append(line[:300])
                    del self._untrusted_directives[:-12]
            return signals
        except Exception as exc:
            _trace(f"untrusted signals failed: {type(exc).__name__}: {exc}")
            return []

    @staticmethod
    def _external_text(kind: str, payload: dict) -> tuple[str, str]:
        """Extract external content + its source (empty for our own text)."""
        if kind == "tool_result":
            tool = str(payload.get("tool") or payload.get("name") or "")
            blob = " ".join(
                str(payload.get(key) or "")
                for key in ("stdout", "output", "result", "content", "error")
            )
            if not blob.strip():
                return "", ""
            source = "web" if tool in ("web_extract", "web_search", "browser") else "tool_output"
            return blob[:20000], source
        return "", ""

    def _capability_gap_signal(self, kind: str, payload: dict):
        """A fresh `godsvoice tools request` from the agent is a concrete gap.

        The pause controller then runs the ToolBuildRunner (dispatch by
        trigger_kind) and the redirect carries the ready tool — same
        pause → research → redirect → resume pipeline as the Voice.
        """
        if kind != "tool_call":
            return None
        args = payload.get("args")
        command = str(args.get("command") or "") if isinstance(args, dict) else ""
        try:
            from godsv.capability import CapabilityStore, looks_like_request_command
        except Exception:
            return None
        if not looks_like_request_command(command):
            return None
        try:
            store = CapabilityStore(self.state_dir)
            request = store.newest_open(session_id=self.session_id) or store.newest_open()
        except Exception:
            return None
        if request is None:
            return None
        # §7: annotate the request with the stable project id so the built
        # tool is visible in the right project context (was always empty).
        if not request.project_id:
            try:
                from godsv.capability import project_id_for

                hint = self._repo_root_hint()
                pid = project_id_for(hint) if hint else ""
                if pid:
                    request.project_id = pid
                    store.update(request)
            except Exception:
                pass
        try:
            self._record(
                "capability_requested",
                {
                    "request_id": request.request_id,
                    "capability": request.capability,
                    "name": request.name,
                    "requested_by": request.requested_by,
                },
            )
        except Exception:
            pass
        return Signal(
            kind="capability_gap",
            severity=3,
            fingerprint=f"cap-{request.request_id}",
            message=f"Capability missing: {request.capability} — tool build runs during the pause.",
            refs={"request_id": request.request_id},
        )

    def _maybe_pause(self, signals: list[Signal], event) -> None:
        """Feed the pause controller: signal counts + contradiction checks."""
        if self._pause is None:
            return
        try:
            if event.kind == "tool_result":
                error = error_text(event.payload)
                if error:
                    fingerprint = error_fingerprint(error)
                    if (
                        self._last_claim is not None
                        and event.step - self._last_claim[0] <= 12
                        and fingerprint in self._error_fps
                    ):
                        contradiction = {
                            "claim": self._last_claim[1][:200],
                            "fingerprint": fingerprint,
                            "claim_step": self._last_claim[0],
                            "error_step": event.step,
                        }
                        self._record("contradiction", contradiction)
                        problem = (
                            f"Claim (step {contradiction['claim_step']}): "
                            f"{contradiction['claim']} — then a failure again [{fingerprint}]"
                        )
                        self._pause.consider(
                            step=event.step, contradiction=contradiction, problem=problem
                        )
                        return
                    self._error_fps.add(fingerprint)
            if signals:
                problem = "; ".join(
                    f"{signal.kind}: {signal.message}" for signal in signals[:2]
                )
                self._pause.consider(step=event.step, signals=signals, problem=problem)
        except Exception as exc:
            _trace(f"pause feed failed: {type(exc).__name__}: {exc}")

    def record_claims(self, text: str) -> None:
        """Record success/completion claims from agent text (deduped per text)."""
        if not text:
            return
        try:
            claims = extract_claims(text)
            if not claims:
                return
            digest = hashlib.sha256(text[:600].encode("utf-8")).hexdigest()[:16]
            if digest in self._claim_hashes:
                return
            if len(self._claim_hashes) > 300:
                self._claim_hashes.clear()
            self._claim_hashes.add(digest)
            categories = sorted({category for category, _sentence in claims})
            snippet = "; ".join(sentence[:120] for _category, sentence in claims[:3])
            self._record("claim", {"text": snippet, "categories": categories})
            self._last_claim = (self.step, snippet)
            self._queue_claim_review(snippet, categories)
        except Exception as exc:
            _trace(f"claim record failed: {type(exc).__name__}: {exc}")

    def _queue_claim_review(self, snippet: str, categories: list) -> None:
        """A success claim against an open contract must not pass silently.

        The host's after-edits ``pre_verify`` gate IS reachable (corrected
        13.09.: ``_commit_tool_result`` -> ``_record_file_mutation_result``
        fills ``_turn_file_mutation_paths`` for landed write_file/patch
        results; the F10 "never filled" claim was outdated — the strict loop
        was proven live, see PROJECT-STATE "Auto-continue live"). The claim
        review additionally rides the durable channel: recorded at turn end
        (post_llm_call → record_claims) and delivered once as a
        ``voice·claim-check`` section in the next turn's contract block.
        """
        try:
            from godsv.completion import TERMINAL_STATES

            state = self._completion_store().state()
            if str(state.get("state") or "") in TERMINAL_STATES:
                return
            if not str(state.get("goal") or "").strip():
                return
            open_items = len(
                [i for i in (state.get("dod") or []) if str(i.get("status") or "") != "done"]
            )
            self._pending_claim_review = {
                "step": self.step,
                "snippet": snippet[:180],
                "categories": list(categories),
                "open_items": open_items,
            }
            self._record(
                "guard_check",
                {
                    "kind": "claim_pending_review",
                    "snippet": snippet[:180],
                    "categories": list(categories),
                    "open_items": open_items,
                },
            )
            _trace(f"claim review queued: categories={categories} open={open_items}")
        except Exception as exc:
            _trace(f"claim review failed: {type(exc).__name__}: {exc}")

    def before_tool_call(self, tool_name: str | None, args: dict | None = None) -> dict | None:
        """pre_tool_call entry point: deliver a ready redirect exactly once."""
        try:
            if self._pause is not None:
                deliverable = self._pause.deliverable(step=self.step, wait=_pause_wait())
                if deliverable is not None:
                    _trace(f"pre_tool_call BLOCK pause={deliverable['pause_id']}")
                    return {"action": "block", "message": deliverable["redirect"]}
            decision = self._intercept_tool_call(tool_name, args)
            if decision is not None and decision.classification in (BLOCK, ASK_APPROVAL):
                return decision.hook_action()
            self._maybe_skill_hint(tool_name)
            return None
        except Exception as exc:
            _trace(f"before_tool_call failed: {type(exc).__name__}: {exc}")
            return None

    def _intercept_tool_call(
        self, tool_name: str | None, args: dict | None
    ) -> ToolDecision | None:
        """Block 4: judge the planned call BEFORE it executes.

        Fail-closed on the security paths (destructive / production / secret /
        verification-disablement / untrusted impulse) and generous everywhere
        else: ``ALLOW`` is the default, and a merely *unrelated* call is never
        escalated (goal drift is a trajectory property, not a single call).
        PAUSE_AND_INVESTIGATE feeds the existing pause machinery instead of
        answering the hook — one control path, not two.
        """
        if not self._intercept_enabled:
            return None
        try:
            intent = self._tool_intent(tool_name, args)
            decision = judge_tool_call(intent)
        except Exception as exc:
            # fail-closed: an unjudgeable call is never silently approved
            _trace(f"intercept failed (fail-closed): {type(exc).__name__}: {exc}")
            return None
        if decision.classification == ALLOW:
            return None
        key = f"{decision.classification}:{decision.reasons[0] if decision.reasons else ''}"
        if key in self._intercepted:
            return None
        self._intercepted.add(key)
        try:
            self._record(
                "tool_call_intercepted",
                {
                    "tool": str(tool_name or ""),
                    "classification": decision.classification,
                    "reasons": list(decision.reasons)[:4],
                    "directive": decision.directive[:300],
                    "checks": {
                        key: value
                        for key, value in decision.checks.items()
                        if value
                    },
                },
            )
        except Exception as exc:  # never lose the hook answer to an event-store error
            _trace(f"intercept record failed: {type(exc).__name__}: {exc}")
        _trace(f"pre_tool_call INTERCEPT {decision.classification} tool={tool_name}")
        if decision.classification == PAUSE_AND_INVESTIGATE:
            # hand the judgment to the pause controller (its own delivery path)
            try:
                if self._pause is not None:
                    signal = Signal(
                        kind="wrong_tool_call",
                        severity=SEV_WARN,
                        fingerprint=error_fingerprint(
                            f"wrong_tool:{tool_name}:{decision.reasons[:1]}"
                        ),
                        message=decision.render(),
                        refs={"tool": str(tool_name or "")},
                    )
                    self._pause.consider(
                        step=max(1, self.step),
                        signals=[signal],
                        problem=decision.render(),
                    )
            except Exception as exc:
                _trace(f"intercept pause feed failed: {type(exc).__name__}: {exc}")
            return decision
        return decision

    def _tool_intent(self, tool_name: str | None, args: dict | None) -> ToolIntent:
        """Collect everything the policy may look at (all of it optional)."""
        pact = self._load_pact()
        goal = str(getattr(pact, "goal", "") or "")
        scope = tuple(str(item) for item in (getattr(pact, "scope_paths", None) or ()))
        workspace = ""
        try:
            state = self._completion_store().state()
            goal = goal or str(state.get("goal") or "")
            workspace = str(state.get("task_base") or "")
        except Exception:
            pass
        return ToolIntent(
            tool=str(tool_name or ""),
            args=args if isinstance(args, dict) else {},
            session_id=self.session_id,
            goal=goal,
            scope_paths=scope,
            workspace=workspace or str(Path.cwd()),
            trust_source="agent",
            untrusted_directives=tuple(self._untrusted_directives),
        )

    def _maybe_skill_hint(self, tool_name: str | None) -> None:
        """Surface a promoted skill once when its chain matches the live work."""
        if not tool_name or self.skills.count() == 0:
            return
        self._recent_tools.append(str(tool_name))
        del self._recent_tools[:-16]
        for skill in relevant_skills(self.skills, self._recent_tools):
            if skill.name in self._consumed_skills:
                continue
            self._consumed_skills.add(skill.name)
            self._record(
                "skill_consumed",
                {
                    "skill": skill.name,
                    "chain": skill.tool_chain,
                    "occurrences": skill.occurrences,
                },
            )
            hint = Hint(
                id=f"skill-{skill.name}",
                sig=f"skill:{skill.name}",
                kind="skill",
                severity=2,
                confidence=0.9,
                text=consumption_hint(skill),
            )
            formatted = self.queue.push(hint)
            self.pending.append(formatted)
            del self.pending[:-MAX_PENDING_HINTS]
            self._log_hint(formatted)

    def _load_pact(self) -> Pact | None:
        """Load ``<state_dir>/pact.yaml`` when present (mtime-cached)."""
        try:
            path = self.state_dir / "pact.yaml"
            if not path.exists():
                return None
            mtime = path.stat().st_mtime
            if self._pact_cache[0] != mtime:
                self._pact_cache = (mtime, Pact.from_yaml(path))
            return self._pact_cache[1]
        except Exception as exc:
            _trace(f"pact load failed: {type(exc).__name__}: {exc}")
            return None

    def _completion_verdict(self, final_text: str, *, allow_judge: bool) -> GuardVerdict | None:
        """Cached completion check; the LLM judge runs on the nudge path only."""
        try:
            key = hashlib.sha256((final_text or "")[:800].encode("utf-8")).hexdigest()[:16]
            cached = self._guard_cache.get(key)
            if cached is not None:
                return cached
            pact = self._load_pact()
            index = build_evidence_index(self.store, self.session_id)
            verdict = evaluate_completion(
                self.store, self.session_id, final_text or "", pact=pact, index=index
            )
            if self._guard_forced and not verdict.needs_continue:
                verdict.status = "unsupported"
                verdict.findings = [
                    GuardFinding(
                        category="forced",
                        claim="(Kanal-Test)",
                        missing=(
                            "Guard channel test (GODSV_GUARD_FORCE=1): back completion "
                            "explicitly with evidence or mark it as open"
                        ),
                    )
                ]
            if allow_judge and self.llm is not None and not verdict.needs_continue:
                registry = self._prompt_registry()
                if registry is not None:
                    extra = judge_completion(
                        self.llm,
                        registry,
                        final_text=final_text or "",
                        evidence_digest=evidence_digest(self.store, self.session_id),
                    )
                    if extra:
                        verdict.findings.extend(extra)
                        verdict.status = "needs_verification"
                        verdict.source = "deterministic+llm"
            self._record(
                "guard_check",
                {
                    "status": verdict.status,
                    "claims": verdict.claims_checked,
                    "findings": len(verdict.findings),
                    "source": verdict.source,
                },
            )
            self._guard_cache = {key: verdict}  # keep the last one only
            return verdict
        except Exception as exc:
            _trace(f"guard verdict failed: {type(exc).__name__}: {exc}")
            return None

    # -- strict completion supervisor (Goal: reject → redirect → continue) ---

    # Only *explicit status lines* terminate a run (P1/B5): free-text like
    # "the user was blocked by the captcha; we continued" must never do it.
    _BLOCKED_MARKERS = (
        ("BLOCKED_USER", re.compile(r"(?im)^\s*(?:status\s*:\s*)?BLOCKED[_ ]USER\b")),
        ("BLOCKED_EXTERNAL", re.compile(r"(?im)^\s*(?:status\s*:\s*)?BLOCKED[_ ]EXTERNAL\b")),
        (
            "FAILED_UNRECOVERABLE",
            re.compile(r"(?im)^\s*(?:status\s*:\s*)?FAILED[_ ]UNRECOVERABLE\b"),
        ),
    )
    _STRICT_NUDGE_CAP = 40  # deadlock guard only: the run never ends "verified" by this

    def _completion_store(self):
        from godsv.completion import CompletionStore

        return CompletionStore(self.state_dir, session_id=self.session_id)

    _GOAL_MENTION = re.compile(r"@goal\s+(?P<goal>[^\n]+)", re.IGNORECASE)

    def contract_context(self, user_message=None) -> dict | None:
        """Chat-side contract channel (``pre_llm_call``).

        The host hands us the real session id and the user message here — the
        one place a chat-typed ``@goal <text>`` lands on the *per-session*
        contract, and where the main agent receives the live contract (state,
        goal, blockers) through the sanctioned context channel: appended to
        the user message, never the system prompt.
        """
        from godsv.completion import TERMINAL_STATES

        text = str(user_message or "")
        store = self._completion_store()
        match = self._GOAL_MENTION.search(text)
        if match:
            goal = match.group("goal").strip()[:400]
            record = store.state()
            current = str(record.get("goal") or "").strip()
            if goal and (
                current != goal
                or (record.get("state") or "") in TERMINAL_STATES
            ):
                store.begin_goal(self.session_id, goal, [])
                try:
                    self._record("goal_set_from_chat", {"goal": goal[:200]})
                except Exception as exc:  # an event hiccup must not lose the context
                    _trace(f"goal event failed: {type(exc).__name__}: {exc}")
        record = store.state()
        goal = str(record.get("goal") or "").strip()
        state = str(record.get("state") or "")
        if not goal or not state or state in TERMINAL_STATES:
            return None
        lines = [f"godsv·contract: state={state} · goal={goal[:160]}"]
        dod = record.get("dod") or []
        if dod:
            verified = sum(1 for item in dod if str(item.get("status")) != "open")
            lines.append(f"godsv·dod: {verified}/{len(dod)} blocking requirements verified")
            pending = self._pending_claim_review
            if pending:
                self._pending_claim_review = None  # delivered exactly once
                _trace(f"claim check delivered open={pending['open_items']}")
                lines.append(
                    'voice·claim-check: the previous turn claimed: "'
                    f'{pending["snippet"]}" ({", ".join(pending["categories"])}) — '
                    f"a claim is "
                    f"not evidence; {pending['open_items']} requirement(s) still unverified. "
                    "Run the verification (or bring runner evidence) before claiming completion."
                )
        blockers: list[str] = []
        for row in reversed(store.timeline(limit=40)):
            if row.get("kind") == "verification":
                blockers = [str(b) for b in (row.get("blocking_failures") or [])][:3]
                break
        for blocker in blockers:
            lines.append(f"godsv·blocker: {blocker}")
        if blockers:
            lines.append(
                "godsv·note: if this is a genuine blocker that needs the owner's "
                "decision, end with a status line `BLOCKED_USER: <reason>`; prose "
                "alone does not stop the supervisor loop."
            )
        # Trajectory state (P0-1): decisions/assumptions/non-goals live on the
        # contract, outside this conversation — surface them so the agent stays
        # inside them, and teach the intake lines the composer middleware parses.
        for key in ("decisions", "assumptions", "non_goals"):
            for entry in (record.get(key) or [])[-3:]:
                lines.append(
                    f"godsv·{key[:-1]}: {str((entry or {}).get('text') or '')[:140]}"
                )
        lines.append(
            "godsv·note: record decisions, assumptions and non-goals with the lines "
            "`!decision: …`, `!assumption: …`, `!non-goal: …` in a message — the "
            "contract keeps them outside this conversation."
        )
        # The live supervisor block rides the transcript directive area: the
        # model addresses `::godsv-live{…}` as its own paragraph and the chat
        # renders the compact, self-updating state (spec: the chat IS the
        # cockpit). transform_llm_output does NOT reach the desktop surface.
        lines.append(
            'godsv·ui: when you report progress or finish a step, add the paragraph '
            '::godsv-live{stage="working"} on its own line — the chat renders the '
            "live supervisor block from it."
        )
        return {"context": "\n".join(lines)}

    def _contract(self) -> dict:
        """The authoritative contract for THIS session's task.

        Store-first: the session-scoped completion record IS the goal contract
        (chat edits and `@goal` land there). ``pact.yaml`` is only an import
        source — when no contract exists yet and a pact is present, it is
        adopted once, for this session only (P1/A4: the chat goal and the
        supervisor must read the same document).
        """
        store = self._completion_store()
        record = store.state()
        if not str(record.get("goal") or "").strip():
            pact = self._load_pact()
            items = self._dod_from_pact(pact)
            goal = str(getattr(pact, "goal", "") or "")
            if goal or items:
                record = store.begin_goal(self.session_id, goal, items)
        return record

    def _dod_from_store(self, record: dict) -> list:
        """DodItem objects from the contract record (unknown fields dropped)."""
        from godsv.completion import DodItem

        allowed = ("id", "text", "blocking", "verification", "status")
        items: list = []
        for raw in record.get("dod") or []:
            if not isinstance(raw, dict):
                continue
            payload = {key: raw.get(key) for key in allowed if raw.get(key) is not None}
            payload.setdefault("id", f"c{len(items) + 1}")
            payload.setdefault("text", "")
            items.append(DodItem(**payload))
        return items

    def _dod_from_pact(self, pact: Pact | None) -> list:
        """Pact acceptance criteria → blocking definition-of-done items."""
        from godsv.completion import DodItem
        from godsv.guard import criterion_categories

        items: list = []
        if pact is None:
            return items
        for criterion in pact.acceptance or []:
            categories = criterion_categories(criterion.text)
            if "tests" in categories:
                verification = "pytest"
            elif "ui" in categories:
                verification = "ui_e2e"
            elif "api" in categories:
                verification = "api"
            else:
                verification = "run"
            items.append(
                DodItem(
                    id=criterion.id,
                    text=criterion.text,
                    blocking=True,
                    verification=verification,
                )
            )
        return items

    def _repo_root_hint(self) -> Path:
        """Best-effort project root: contract task base, else edited files' root.

        Live-found 13.09.: the edited-files heuristic picked /tmp (the agent's
        read-only probe scripts) and the guard ran registered testing tools
        against the wrong repo -> runner evidence 'failed' -> the criterion
        could never be certified. The contract's declared task_base wins now.
        """
        try:
            base = str(self._completion_store().state().get("task_base") or "").strip()
            if base and Path(base).is_dir():
                return Path(base)
        except Exception:
            pass
        try:
            paths: list[Path] = []
            for event in self.store.iter_session(self.session_id):
                if event.kind != "file_edit":
                    continue
                raw = str((event.payload or {}).get("path") or "")
                if raw:
                    candidate = Path(raw)
                    paths.append(candidate if candidate.is_absolute() else Path.cwd() / candidate)
            if paths:
                common = Path(os.path.commonpath([str(p) for p in paths]))
                if not common.is_dir():
                    common = common.parent
                for candidate_root in [common, *common.parents]:
                    if (candidate_root / ".git").exists():
                        return candidate_root
                return common
        except Exception:
            pass
        return Path.cwd()

    def _run_evidence(self) -> list:
        """Objective evidence: only checks that were *actually executed* (post-edit)."""
        from godsv.completion import run_evidence_from_events

        try:
            events = self.store.iter_session(self.session_id)
        except Exception:
            return []
        return run_evidence_from_events(events)

    def _strict_completion(self, final_text: str):
        """Gate a completion claim against real facts — the strict supervisor."""
        from godsv.completion import (
            TERMINAL_STATES,
            CompletionRequest,
            GateRunner,
        )
        from godsv.guard import extract_claims

        try:
            store = self._completion_store()
            contract = self._contract()
            state = contract.get("state") or ""
            if state in TERMINAL_STATES:
                return None
            dod = self._dod_from_store(contract)
            claims = extract_claims(final_text or "")
            text = final_text or ""
            for marker, pattern in self._BLOCKED_MARKERS:
                if pattern.search(text):
                    if claims or marker == "FAILED_UNRECOVERABLE":
                        from types import SimpleNamespace

                        store.transition(marker, reason=text[:200])
                        self._record("completion_blocked", {"marker": marker})
                        return SimpleNamespace(status=marker)
            if not claims:
                return None
            # Without a goal contract the gates still judge the claim against
            # executed evidence: a green `pytest` run can certify "tests are
            # green", but a claim with no executed check behind it blocks as
            # INCOMPLETE. The old "session goal" substitute — which let empty
            # goals float into VERIFIED_COMPLETE — is gone (P1/A4).
            request = CompletionRequest(
                user_goal=str(contract.get("goal") or ""),
                claimed_completed=[sentence for _category, sentence in claims][:10],
                test_results="claimed in final response",
                attempt_id="att-" + hashlib.sha1(text.encode("utf-8")).hexdigest()[:10],
                created=time.time(),
            )
            evidence = self._run_evidence()
            # P1: specific requirements need runner-executed checks — event
            # evidence cannot certify them
            from godsv.completion import run_requirement_checks

            try:
                _state = store.state()
                _rev = int(_state.get("revision") or 0)
                _ws = str(_state.get("task_base") or self._repo_root_hint())
            except Exception:
                _rev, _ws = 0, ""
            evidence = list(evidence) + run_requirement_checks(
                dod, self._repo_root_hint(), timeout=300, revision=_rev, workspace=_ws
            )
            result = GateRunner(
                repo_root=self._repo_root_hint(), dod=dod, revision=_rev, workspace=_ws
            ).verify(request, evidence, store=store)
            self._last_strict = result
            self._record(
                "completion_verdict",
                {
                    "status": result.status,
                    "blockers": list(result.blocking_failures)[:4],
                    "majors": list(result.major_failures)[:3],
                    "fingerprint": result.fingerprint,
                    "escalate": bool(result.escalate),
                    "evidence": list(result.evidence)[:4],
                },
            )
            return result
        except Exception as exc:
            _trace(f"strict completion failed: {type(exc).__name__}: {exc}")
            return None

    # Voice-visible kinds: their appearance in a turn earns a live block even
    # when the stage itself did not change (spec: the chat is the cockpit).
    _VOICE_TURN_KINDS = (
        "pause_requested",
        "pause_delivered",
        "pause_released",
        "pause_expired",
        "research_started",
        "research_done",
        "research_failed",
        "completion_rejected",
        "completion_blocked",
        "completion_verdict",
        "completion_cap_reached",
        "guard_nudge",
        "contradiction",
        "goal_set_from_chat",
        "capability_requested",
        "tool_build_ready",
        "tool_build_failed",
        "cu_failed",
        "cu_blocked",
    )

    def live_block_suffix(self) -> str | None:
        """The `::godsv-live{…}` paragraph appended to a final answer.

        Emitted only when this turn had voice activity or the displayed stage
        changed — the block reads the real state live, so repeats are noise.
        """
        try:
            record = self._completion_store().state()
            state = str(record.get("state") or "")
            goal = str(record.get("goal") or "").strip()
            started = float(getattr(self, "_turn_started", 0.0) or 0.0)
            fresh = False
            if started:
                for event in self.store.iter_session(self.session_id):
                    if float(getattr(event, "ts", 0.0) or 0.0) < started:
                        continue
                    if str(getattr(event, "kind", "")) in self._VOICE_TURN_KINDS:
                        fresh = True
                        break
            # A fresh store reports WORKING with an empty goal — only a real
            # contract (a goal) or fresh voice activity earns the block.
            if not fresh and not goal:
                return None
            if not fresh and getattr(self, "_last_block_stage", None) == state:
                return None
            _trace(
                f"live block emit: state={state} fresh={fresh} "
                f"last={getattr(self, '_last_block_stage', None)}"
            )
            stage = (
                "verified"
                if state == "VERIFIED_COMPLETE"
                else "rejected"
                if state == "INCOMPLETE"
                else "verifying"
                if state == "VERIFYING"
                else "working"
                if state
                else "status"
            )
            self._last_block_stage = state
            return f'\n\n::godsv-live{{stage="{stage}" sid="{self.session_id}"}}\n'
        except Exception as exc:
            _trace(f"live block failed: {type(exc).__name__}: {exc}")
            return None

    def guard_pre_verify(self, *, attempt: int, final_response: str) -> dict | None:
        """pre_verify: strict completion loop — reject → redirect → continue.

        The strict supervisor runs on every attempt (no one-shot nudge): when
        the gates reject the claim, the turn continues with a precise redirect.
        Only terminal states (verified or honestly blocked) let the answer pass.
        """
        _trace(
            f"pre_verify called attempt={attempt} guard_enabled={bool(self._guard_enabled)}"
        )
        if not self._guard_enabled:
            return None
        try:
            strict = self._strict_completion(final_response)
        except Exception as exc:
            _trace(f"strict completion crashed: {type(exc).__name__}: {exc}")
            # a checker failure must never weaken the strict mode (P1/B5):
            # record it and keep the response marked unverified (fail-closed)
            self._strict_crashed = True
            self._record(
                "completion_checker_failed",
                {"error": f"{type(exc).__name__}: {exc}"[:200]},
            )
            strict = None
        if strict is not None:
            status = str(getattr(strict, "status", ""))
            terminal = (
                "VERIFIED_COMPLETE",
                "BLOCKED_USER",
                "BLOCKED_EXTERNAL",
                "FAILED_UNRECOVERABLE",
                "USER_CANCELLED",
                "SHIPPED_WITH_USER_OVERRIDE",
            )
            if status in terminal or mode() != "whisper":
                return None
            self._completion_nudges += 1
            if self._completion_nudges > self._STRICT_NUDGE_CAP:
                self._record(
                    "completion_cap_reached", {"nudges": self._completion_nudges}
                )
                return None  # honest stop: state stays unverified, footer explains
            self._guard_nudged_turn = True
            self._record(
                "completion_rejected",
                {
                    "attempt": attempt,
                    "status": status,
                    "blockers": list(getattr(strict, "blocking_failures", []))[:4],
                    "escalate": bool(getattr(strict, "escalate", False)),
                },
            )
            _trace(f"pre_verify STRICT reject status={status} attempt={attempt}")
            return {"action": "continue", "message": strict.redirect_md()}
        # fallback: legacy claim guard (one nudge per turn)
        if attempt > 0:
            return None
        verdict = self._completion_verdict(final_response, allow_judge=True)
        if mode() != "whisper":
            return None  # shadow: observe + record only, never steer the loop
        if verdict is None or not verdict.needs_continue:
            return None
        self._guard_nudged_turn = True
        self._record(
            "guard_nudge",
            {
                "status": verdict.status,
                "findings": [finding.missing for finding in verdict.findings[:3]],
            },
        )
        _trace(f"pre_verify NUDGE status={verdict.status} findings={len(verdict.findings)}")
        return {"action": "continue", "message": verdict.message()}

    def guard_final(self, response_text: str) -> str | None:
        """transform_llm_output: caveat footer when claims stay unverified."""
        if not self._guard_enabled or not response_text:
            return None
        if mode() != "whisper":
            return None  # shadow: observe + record only, never steer the loop
        strict = self._last_strict
        strict_pending = bool(getattr(self, "_strict_crashed", False)) or (
            strict is not None and str(getattr(strict, "status", "")) == "INCOMPLETE"
        )
        verdict = self._completion_verdict(response_text, allow_judge=False)
        if mode() != "whisper" or verdict is None or not verdict.needs_continue:
            if not strict_pending:
                return None
        if strict_pending:
            if getattr(self, "_strict_crashed", False):
                footer = (
                    "strict completion gate: checker FAILED — the completion claim was NOT "
                    "verified (fail-closed). See completion history."
                )
            else:
                footer = (
                    "strict completion gate: INCOMPLETE — the product was not independently "
                    "verified. See completion history for the blockers."
                )
        else:
            footer = verdict.message(compact=True) if verdict is not None else "unverified claims"
        self._record(
            "guard_footer",
            {
                "status": getattr(verdict, "status", ""),
                "strict": strict_pending,
                "footer": footer[:300],
            },
        )
        _trace(f"transform_llm_output FOOTER strict={strict_pending}")
        return response_text + f"\n\n[voice·guard] {footer}"

    def _record(self, kind: str, payload: dict):
        with _OBSERVE_LOCK:  # also called from enrichment threads → keep step + append atomic
            self.step += 1
            try:
                return self.store.append(self.session_id, self.step, kind, payload)
            except SecretLeakError:
                logger.warning("secret detected in %s payload — storing redacted", kind)
                _trace(f"secret redacted in {kind}")
                return self.store.append_redacted(self.session_id, self.step, kind, payload)

    def observe(self, kind: str, payload: dict) -> str | None:
        """Record an event, run monitor/voice, queue + log a hint if one is due."""
        try:
            with _OBSERVE_LOCK:
                self._promote_held()
                event = self._record(kind, payload)
                signals = self.monitor.ingest(event)
                drift = self._drift_signals(kind, payload)
                if drift:
                    signals = [*signals, *drift]
                gap = self._capability_gap_signal(kind, payload)
                if gap is not None:
                    signals = [*signals, gap]
                patterns = self._sequence_signals(kind, payload)
                if patterns:
                    signals = [*signals, *patterns]
                untrusted = self._untrusted_signals(kind, payload)
                if untrusted:
                    signals = [*signals, *untrusted]
                self._maybe_pause(signals, event)
                evidence_objects = self._gather_evidence(signals)
                rendered = [item.render() for item in evidence_objects]
                hint = self.voice.consider(signals, self.step, evidence=rendered)
                if hint is None:
                    _trace(f"observe ok kind={kind} step={self.step}")
                    return None
                if self.llm is not None and (
                    rendered or (self._triage and hint.kind in SUPPRESSIBLE_KINDS)
                ):
                    self._begin_enrichment(hint, signals, evidence_objects)
                    _trace(
                        f"observe ok kind={kind} step={self.step} hint={hint.kind} "
                        f"evidence={len(rendered)} held-for-enrichment"
                    )
                    return None
                formatted = self.queue.push(hint)
                self.pending.append(formatted)
                del self.pending[:-MAX_PENDING_HINTS]
                self._log_hint(formatted)
                _trace(
                    f"observe ok kind={kind} step={self.step} hint={hint.kind} "
                    f"evidence={len(rendered)}"
                )
                return formatted
        except Exception as exc:  # diagnostics; re-raise so Hermes logs+skips
            _trace(f"observe FAILED kind={kind} err={type(exc).__name__}: {exc}")
            raise

    # -- enrichment (daemon thread; never blocks the hook) -------------------
    def _begin_enrichment(
        self, hint: Hint, signals: list[Signal], evidence: list[Evidence]
    ) -> None:
        """Hold the hint while a daemon thread triages, judges and synthesizes."""
        holder: dict = {
            "hint": hint,
            "ready": False,
            "closed": False,
            "deadline": time.monotonic() + self._grace,
        }
        self._held.append(holder)

        def work() -> None:
            try:
                self._enrich(hint, signals, evidence, holder)
            finally:
                with _OBSERVE_LOCK:
                    holder["ready"] = True

        thread = threading.Thread(target=work, name="godsv-enrichment", daemon=True)
        self._synth_threads = [item for item in self._synth_threads if item.is_alive()]
        self._synth_threads.append(thread)
        thread.start()

    def _enrich(
        self,
        hint: Hint,
        signals: list[Signal],
        evidence: list[Evidence],
        holder: dict,
    ) -> None:
        """Thread-side enrichment: triage-gate → judge → synthesize (never raises)."""
        try:
            if self._triage and hint.kind in SUPPRESSIBLE_KINDS:
                verdict = self._triage_verdict(signals)
                if verdict is not None:
                    self._record(
                        "hint_triage",
                        {
                            "kind": hint.kind,
                            "stuck": verdict.stuck,
                            "confidence": verdict.confidence,
                            "category": verdict.category,
                        },
                    )
                    if verdict.stuck is False and verdict.confidence >= TRIAGE_MIN_CONFIDENCE:
                        holder["suppressed"] = True
                        holder["suppress_reason"] = (
                            f"triage: not stuck ({verdict.category}, "
                            f"conf {verdict.confidence:.2f})"
                        )
                        self._record(
                            "hint_suppressed",
                            {
                                "kind": hint.kind,
                                "sig": hint.sig,
                                "reason": holder["suppress_reason"],
                            },
                        )
                        _trace(
                            f"triage suppressed hint={hint.kind} conf={verdict.confidence:.2f}"
                        )
                        return
            rendered: list[str] = []
            if evidence:
                kept = self._judge_evidence(hint, signals, evidence)
                rendered = [item.render() for item in kept]
            if rendered:
                self._synthesize_into(hint, signals, rendered)
        except Exception as exc:  # defensive: the thread must never take the plugin down
            _trace(f"enrich failed: {type(exc).__name__}: {exc}")

    def _triage_verdict(self, signals: list[Signal]):
        """Run LLM triage over the recent event digest (None when unavailable)."""
        client = self.llm
        registry = self._prompt_registry()
        if client is None or registry is None:
            return None
        try:
            return triage_step(
                client,
                registry,
                pact_excerpt=self._pact_excerpt(),
                event_digest=self._event_digest(),
            )
        except Exception as exc:  # triage_step catches internally; belt and suspenders
            _trace(f"triage failed: {type(exc).__name__}: {exc}")
            return None

    def _event_digest(self, limit: int = 10) -> str:
        lines: list[str] = []
        for event in self.store.iter_session(self.session_id)[-limit:]:
            parts = [f"step {event.step}: {event.kind}"]
            name = event.payload.get("name") or event.payload.get("tool")
            if name:
                parts.append(str(name))
            status = event.payload.get("status")
            if status:
                parts.append(str(status))
            error = error_text(event.payload)
            if error:
                parts.append("ERR: " + error[:120])
            lines.append(" | ".join(parts))
        return "\n".join(lines)

    def _pact_excerpt(self) -> str:
        return "(no pact loaded — judge the event context only)"

    def _judge_evidence(
        self, hint: Hint, signals: list[Signal], evidence: list[Evidence]
    ) -> list[Evidence]:
        """Confirm/drop fuzzy incident matches via ``same_problem_judge``.

        Exact hits and (untrusted) research notes are never judged. On judge
        errors the evidence is kept unchanged (fail-open).
        """
        client = self.llm
        registry = self._prompt_registry()
        fuzzy = [item for item in evidence if not item.exact and not item.untrusted]
        if not fuzzy or client is None or registry is None:
            return evidence
        problem = next(
            (
                str(signal.refs.get("error"))
                for signal in signals
                if signal.kind == "error_burst" and signal.refs.get("error")
            ),
            None,
        ) or "; ".join(signal.message for signal in signals[:2])
        try:
            ref = judge_same_problem(problem, fuzzy, client=client, registry=registry)
        except Exception as exc:
            _trace(f"evidence judge failed (fail-open): {type(exc).__name__}: {exc}")
            return evidence
        fuzzy_refs = {item.ref for item in fuzzy}
        if ref is None:
            kept = [item for item in evidence if item.ref not in fuzzy_refs]
        else:
            kept = [item for item in evidence if item.ref not in fuzzy_refs or item.ref == ref]
        self._record(
            "evidence_judged",
            {
                "kept": [item.ref for item in kept],
                "dropped": sorted(fuzzy_refs - {item.ref for item in kept}),
            },
        )
        with _OBSERVE_LOCK:
            hint.evidence = [item.render() for item in kept]
        return kept

    def _synthesize_into(
        self, hint: Hint, signals: list[Signal], rendered_evidence: list[str]
    ) -> None:
        """Try LLM synthesis and patch the hint in place (never raises).

        Runs on the enrichment thread — the model call happens *without* the
        observe lock; the in-place patch takes the lock so promotion never
        renders a half-updated hint. If the holder was already closed (hint
        delivered as template), the patch is dropped and traced.
        """
        client = self.llm
        if client is None:
            return
        registry = self._prompt_registry()
        if registry is None:
            return
        trigger = "; ".join(f"{signal.kind}: {signal.message}" for signal in signals[:3])
        try:
            result = synthesize_hint(
                client, registry, trigger=trigger or hint.kind, evidence_items=rendered_evidence
            )
        except Exception as exc:
            _trace(f"llm synthesis failed: {type(exc).__name__}: {exc}")
            return
        if result is None:
            _trace("llm synthesis declined — template stays")
            return
        with _OBSERVE_LOCK:
            closed = any(holder["closed"] for holder in self._held if holder["hint"] is hint)
            if closed:
                _trace("llm synthesis finished late — upgrade missed (template delivered)")
                return
            hint.text = result.hint_md
            hint.confidence = max(hint.confidence, result.confidence)
            if result.verify:
                hint.text += f"\n\nVerification step: {result.verify}"
        _trace(f"llm synthesis applied conf={result.confidence:.2f}")

    def _promote_held(self, *, promote_unready: bool = False, force: bool = False) -> None:
        """Move held hints into the delivery queue (or record suppression).

        ``promote_unready`` promotes not-yet-enriched hints too (delivery
        opportunity — template goes out). ``force`` ignores both readiness
        and deadline (session end). Otherwise only ready/deadline-passed
        hints are promoted (shadow-mode bookkeeping).
        """
        now = time.monotonic()
        remaining: list[dict] = []
        for holder in self._held:
            due = force or promote_unready or holder["ready"] or now >= holder["deadline"]
            if not due:
                remaining.append(holder)
                continue
            holder["closed"] = True
            if holder.get("suppressed"):
                self._log_suppressed(holder)
                continue
            formatted = self.queue.push(holder["hint"])
            self.pending.append(formatted)
            del self.pending[:-MAX_PENDING_HINTS]
            self._log_hint(formatted)
        self._held = remaining

    def flush(self) -> None:
        """Promote everything still held (template fallback) — used at session end."""
        with _OBSERVE_LOCK:
            self._promote_held(force=True)

    def wait_synthesis(self, timeout: float = 6.0) -> None:
        """Join outstanding enrichment threads (tests/introspection)."""
        for thread in list(self._synth_threads):
            thread.join(timeout)
        self._synth_threads = [item for item in self._synth_threads if item.is_alive()]

    def _prompt_registry(self):
        if self._registry is None and not self._registry_failed:
            try:
                from godsv.prompts import PromptRegistry

                self._registry = PromptRegistry()
            except Exception as exc:
                self._registry_failed = True
                _trace(f"prompt registry unavailable: {type(exc).__name__}: {exc}")
        return self._registry

    def _gather_evidence(self, signals: list[Signal]) -> list[Evidence]:
        """Collect deduped Evidence objects from memory for the given signals."""
        collected: list[Evidence] = []
        for signal in signals:
            if signal.kind not in ("error_burst", "loop"):
                continue
            query = str(signal.refs.get("error") or signal.message)
            signature = signal.fingerprint if signal.kind == "error_burst" else None
            collected.extend(
                find_evidence(self.incidents, self.notes, signature=signature, query=query, limit=3)
            )
        unique: list[Evidence] = []
        seen: set[str] = set()
        for item in collected:
            if item.ref in seen:
                continue
            seen.add(item.ref)
            unique.append(item)
        return unique[:3]

    def take_pending(self) -> list[str]:
        with _OBSERVE_LOCK:
            self._promote_held(promote_unready=True)
            pending, self.pending = self.pending, []
            return pending

    def mark_delivered(self, hints: list[str], tool_name: str | None) -> None:
        self._record(
            "hint_delivered",
            {"via": "transform_tool_result", "tool": tool_name, "hints": hints},
        )

    def _log_hint(self, formatted: str) -> None:
        record = {
            "session": self.session_id,
            "step": self.step,
            "mode": mode(),
            "hint": formatted,
        }
        with (self.state_dir / "inbox.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _log_suppressed(self, holder: dict) -> None:
        """Inbox entry for a suppressed hint (the event is recorded at verdict time)."""
        hint: Hint = holder["hint"]
        reason = str(holder.get("suppress_reason", "triage"))
        record = {
            "session": self.session_id,
            "step": self.step,
            "mode": mode(),
            "suppressed": True,
            "reason": reason,
            "hint": format_hint(hint),
        }
        with (self.state_dir / "inbox.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")


_SESSIONS: dict[str, GodsvSession] = {}


def _state_dir() -> Path:
    home = os.environ.get("HERMES_HOME") or str(Path.home() / ".hermes")
    return Path(home) / "godsv"


def _session(session_id: str | None) -> GodsvSession:
    key = session_id or "default"
    with _SESSION_LOCK:
        session = _SESSIONS.get(key)
        if session is None:
            session = GodsvSession(key, _state_dir(), llm_client=_llm_client())
            _SESSIONS[key] = session
    return session


def _truncate(value, limit: int = 4000) -> str:
    if value is None:
        return ""
    text = str(value)
    return text if len(text) <= limit else text[:limit] + " […truncated]"


# --------------------------------------------------------------------------- #
# §1.3 live capability surface (Block B5)
#
# A capability that finished the gated build becomes a real Hermes tool at the
# NEXT MODEL BOUNDARY — no session restart. The chain lives in
# ``godsv.liveinsert``; this adapter only binds it to the host:
#
#   pre_llm_call (fires right before the first API call of a turn, ahead of the
#   host's between-turns tool refresh in ``agent/turn_context.py``)
#     → CapabilityIndex.sync_registry()   (verified-but-unknown tools, health-gated)
#     → apply_pending() at the boundary   (never mid-call; in-flight stays intact)
#     → ctx.register_tool(...)            (native surface, newest HOT_LIMIT only)
#
# The registry bump makes the very next tool-snapshot rebuild see the tool; a
# name that leaves the hot set is deregistered again (its handle is disposed) —
# a wide flush is noise, not capability. Nothing here raises: a broken surface
# must never break a turn.
# --------------------------------------------------------------------------- #

LIVE_TOOLSET = "godsv-tools"
LIVE_SYNC_MIN_INTERVAL_S = 5.0
_AUTOLINK_ENV = "GODSV_MCP_AUTOLINK"
_LINK_LOCK = threading.Lock()
_LINK_ATTEMPTED = False

# A stable tool of the live toolset, registered AT PLUGIN LOAD. Its purpose is
# twofold: the model gets one dependable entry point to ask what is live, and
# the toolset key exists from the first agent build — plugin toolsets are
# enabled by default only when their key is known at resolution time, so a
# toolset that first appears mid-session would be filtered out of the snapshot.
LIVE_REPORT_TOOL = "godsv_capabilities"
LIVE_REPORT_SCHEMA = {
    "name": LIVE_REPORT_TOOL,
    "description": (
        "Report the live Skaldir Agent capability surface — which verified capabilities are "
        "routed into a session right now, which are hot, and which the health ledger blocked. "
        "With action='computer_use' it also runs one Computer-Use request for a goal "
        "(plan → act → observe → verify) and returns the real verdict."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["report", "computer_use", "triage", "workflow", "runs"],
                "description": (
                    "report (default) reads the surface; computer_use runs one CU request; "
                    "triage runs "
                    "one System-One voice triage for a runtime signal."
                ),
            },
            "goal": {
                "type": "string",
                "description": (
                    "What the Computer-Use run should achieve (needed for computer_use)."
                ),
            },
            "runner": {
                "type": "string",
                "enum": ["dry-run", "sandbox", "container-gui"],
                "description": (
                    "Isolation to use. Defaults to the sandbox (real CLI action inside bwrap); "
                    "dry-run is deterministic without side effects."
                ),
            },
            "observations": {
                "type": "string",
                "description": "Optional current observations for the planner.",
            },
            "kind": {
                "type": "string",
                "description": "Signal kind for triage (e.g. repeated_test_failure, scope_drift).",
            },
            "message": {"type": "string", "description": "Signal message for triage."},
            "severity": {"type": "integer", "description": "Signal severity (>=1) for triage."},
            "step": {"type": "integer", "description": "Run step the signal belongs to."},
            "runs_action": {
                "type": "string",
                "enum": ["status", "sweep", "recover"],
                "description": (
                    "Runs: status is read-only, sweep reconciles, recover claims continuations."
                ),
            },
            "workflow": {
                "type": "string",
                "description": (
                    "Workflow: the §6 contract to start (workflows list shows the names) — "
                    "the contract decides which params are required."
                ),
            },
            "params": {
                "type": "object",
                "description": "Workflow: the contract inputs (app, start_cmd, …) as a mapping.",
            },
            "expect_change": {
                "type": "string",
                "description": (
                    "Computer use: the change the action should have caused in the app. "
                    "With it the run reads the post-action observation and reports "
                    "reobserve when the change is absent."
                ),
            },
            "post_text": {
                "type": "string",
                "description": "Computer use: the post-action observation text to check.",
            },
        },
        "additionalProperties": False,
    },
}

# One live surface per session id, keyed module-level: the session OBJECT can be
# re-created between turns, the surface must not.
_LIVE_SURFACES: dict = {}


def godsv_capabilities(args=None, **_ignored) -> str:
    """The live toolset's stable entry point (see ``LIVE_REPORT_TOOL``)."""
    params = args if isinstance(args, dict) else {}
    action = str(params.get("action") or "report").strip().lower()
    if action in ("computer_use", "cu"):
        return _computer_use_entry(params)
    if action in ("runs", "run_supervisor"):
        return _runs_entry(params)
    if action in ("workflow", "workflows"):
        return _workflow_entry(params)
    if action in ("triage", "system_one", "voice_triage"):
        return _system_one_triage_entry(params)
    report: dict = {"toolset": LIVE_TOOLSET, "sessions": {}}
    for key, session in list(_SESSIONS.items()):
        surface = getattr(session, "live", None)
        if surface is None:
            continue
        last = surface.last_report if isinstance(surface.last_report, dict) else {}
        report["sessions"][key] = {
            "live": sorted(surface.registered),
            "hot": surface.index.hot_set(),
            "inserted": surface.index.inserted(),
            "boundary": last.get("reason", ""),
            "blocked": last.get("blocked", {}),
        }
    report["note"] = (
        "Verified capabilities register as individual tools in this toolset at each model "
        "boundary; this report renders the current surface."
    )
    return json.dumps(report, default=str, sort_keys=True)


def _runs_entry(params: dict) -> str:
    """Run-supervisor surface on the runtime's own store (§66 lifecycle).

    Wiring only: `run_supervisor` (RunStore, check/repair, sweep, recover_pending, describe) had no
    production caller at all. `status` is read-only, `sweep` runs the watchdog pass
    (check -> repair -> claim) and `recover` claims queued continuations exactly once each. A
    violation or an unknown disposition is reported as it is — never smoothed into success.
    """

    from godsv import run_supervisor

    what = str(params.get("runs_action") or "status").strip().lower()
    owner = str(params.get("owner") or "skaldir-panel")
    store = run_supervisor.open_store(_state_dir())
    try:
        if what in ("status", "describe"):
            payload = run_supervisor.describe(store)
        elif what in ("sweep", "reconcile"):
            payload = run_supervisor.sweep(store, owner=owner)
        elif what in ("recover", "continuations"):
            payload = {
                "claimed": run_supervisor.recover_pending(store, owner=owner),
                "owner": owner,
            }
        else:
            payload = {
                "status": "blocked",
                "verified": False,
                "reason": f"unknown runs action {what!r} — known: status, sweep, recover",
            }
    finally:
        store.close()
    summary = json.dumps(payload, default=str)[:140]
    _trace(f"runs entry: {what} -> {summary}")
    return json.dumps(payload, default=str, sort_keys=True)


def _workflow_entry(params: dict) -> str:
    """Start one of the declared §6 workflows through the same agent surface.

    Wiring only: `workflows list` could always read the registry; this is what makes a declared
    workflow startable from a conversation. The workflow's own verdict is what the caller sees — a
    missing prerequisite is an honest `blocked`, never a substitute run.
    """

    from godsv.workflows import run_workflow

    outcome = run_workflow(
        str(params.get("workflow") or ""),
        runtime=_state_dir(),
        params=dict(params.get("params") or {}),
    )
    _trace(f"workflow entry: {outcome.get('workflow')} -> {outcome.get('status')}")
    return json.dumps(outcome, default=str, sort_keys=True)


def _system_one_triage_entry(params: dict) -> str:
    """Agent-facing System-One voice triage (Wave 3, Voice axis) — through the ONE facade (§33).

    Measured 17.09. at HEAD: `SystemOneService.voice_triage` had **no production caller** — the
    facade, the composed question pack and the decision policy were reachable only from tests, so
    "runtime event → System One triage → observe / verify / escalate" never ran on a real path.

    This entry drives exactly that chain with the pieces the product already has: flags from the
    runtime `settings.yaml`, the evaluator the flags ask for (the LLM adapter uses the session's
    host-owned client), a composed question pack per trigger kind, a sanitized state (§40) and an
    evaluation context. Honest states by construction:

    * flags off / provider disabled / no client for the adapter → **legacy** with the reason;
    * the deterministic hint is always computed and always returned unchanged;
    * an advisory appears only when the facade really produced one (`via: system_one_service`),
      and `live_jev` stays false for the LLM adapter — a Jev live reading is never claimed.
    """

    import asyncio

    kind = str(params.get("kind") or params.get("signal") or "").strip()
    if not kind:
        return json.dumps(
            {"available": False, "path": "legacy", "reason": "triage needs a signal kind"},
            sort_keys=True,
        )
    from godsv.system_one import PROVIDER_DISABLED, PROVIDER_LLM_ADAPTER
    from godsv.system_one_flags import load_system_one_flags

    flags = load_system_one_flags(_state_dir())
    meta = {
        "enabled": bool(getattr(flags, "enabled", False)),
        "provider": str(getattr(flags, "provider", "")),
        "voice_triage": bool(getattr(flags, "voice_triage", False)),
        "shadow_mode": bool(getattr(flags, "shadow_mode", False)),
    }

    def _legacy(reason: str) -> str:
        return json.dumps(
            {
                "available": False,
                "path": "legacy",
                "reason": reason,
                "flags": meta,
                "hint": None,
                "advisory": {},
                "via": "",
                "live_jev": False,
            },
            ensure_ascii=False,
            sort_keys=True,
        )

    if not meta["enabled"]:
        return _legacy("system one disabled — flags off in the runtime settings.yaml")
    if meta["provider"] in ("", PROVIDER_DISABLED):
        return _legacy("provider disabled in the runtime settings.yaml")
    client = _llm_client()
    if meta["provider"] == PROVIDER_LLM_ADAPTER and client is None:
        return _legacy(
            "no model client for the llm_adapter — set GODSV_LLM=1 and give the host a side-LLM"
        )

    from godsv.monitor import Signal
    from godsv.system_one import EvaluationContext
    from godsv.system_one_questions import compose_voice_triage, default_question_registry
    from godsv.system_one_sanitizer import SanitizerPolicy, StateSanitizer
    from godsv.system_one_service import build_service
    from godsv.voice import Voice

    step = int(params.get("step") or 0)
    project = str(params.get("project_id") or "").strip() or "gods-voice"
    try:
        registry = default_question_registry()
        questions = compose_voice_triage(kind, registry=registry)
        state = StateSanitizer(policy=SanitizerPolicy(project_id=project)).sanitize(
            {
                "goal": str(params.get("goal") or ""),
                "non_goals": [],
                "signals": [kind],
                "step": step,
            },
            scope="task",
            project_id=project,
        )
        context = EvaluationContext(
            run_id=f"agent-triage-{int(time.time())}",
            goal_id=project,
            goal_revision=1,
            policy_revision=1,
            event_cursor=step,
            project_id=project,
            model=str(params.get("model") or ""),
        )
        signal = Signal(
            kind=kind,
            severity=int(params.get("severity") or 1),
            fingerprint=str(params.get("fingerprint") or kind),
            message=str(params.get("message") or ""),
        )
        service = build_service(
            flags=flags,
            llm=client,
            emit=lambda kind_name, payload=None: _emit_system_one(kind_name, payload),
        )
        outcome = asyncio.run(
            service.voice_triage(
                voice=Voice(),
                signals=[signal],
                step=step,
                evidence=[kind],
                flags=flags,
                state=state,
                questions=questions,
                context=context,
            )
        )
    except Exception as exc:  # noqa: BLE001 — a failed advisory is a legacy path, never a crash
        _trace(f"system one triage failed: {type(exc).__name__}: {exc}")
        return _legacy(f"{type(exc).__name__}: {exc}"[:200])

    hint = outcome.hint
    hint_payload = None
    if hint is not None:
        hint_payload = {
            "kind": str(getattr(hint, "kind", "") or ""),
            "text": str(getattr(hint, "text", "") or "")[:400],
            "evidence": [str(item) for item in (getattr(hint, "evidence", ()) or ())][:8],
        }
    advisory = dict(outcome.advisory or {})
    _trace(
        f"system one triage: path={outcome.path} reason={outcome.reason} "
        f"via={advisory.get('via', '')}"
    )
    return json.dumps(
        {
            "available": outcome.path != "legacy",
            "path": outcome.path,
            "reason": outcome.reason,
            "flags": meta,
            "hint": hint_payload,
            "advisory": advisory,
            "provider": advisory.get("provider") or meta["provider"],
            "live_jev": bool(advisory.get("live_jev")),
            "via": advisory.get("via", ""),
            "questions": [question.id for question in questions.questions],
        },
        default=str,
        ensure_ascii=False,
        sort_keys=True,
    )


def _emit_system_one(kind: str, payload: dict | None = None) -> None:
    """Record a System-One lifecycle event in this session's store.

    Best effort: an unregistered lifecycle kind must never break the run."""

    try:
        session = _session(next(iter(_SESSIONS), None))
        session._record(kind, payload or {})
    except Exception as exc:  # noqa: BLE001 — an unregistered lifecycle kind must not break the run
        _trace(f"system one event skipped: {kind} ({type(exc).__name__})")


def _computer_use_entry(params: dict) -> str:
    """Agent-facing Computer-Use entry (Wave 7 S10): a goal in, a real verdict out.

    Measured 17.09.: the surface could list runners and run a fixed dry-run demo, but nothing
    in a chat could bring a Computer-Use goal in — and the gap was silent. This is that entry;
    it reuses the product pipeline (plan with the session's client → ordered attempts → isolated
    runner → act/observe/verify) and reports honest states: no client / no plan / policy refusal
    are ``blocked``, never a fake success.
    """

    goal = str(params.get("goal") or "").strip()
    if not goal:
        return json.dumps(
            {"status": "blocked", "reason": "computer_use needs a goal"}, sort_keys=True
        )
    key = next(iter(_SESSIONS), None)
    session = _session(key)
    from godsv.computer_use import default_runner_kind, run_cu_request

    outcome = run_cu_request(
        goal=goal,
        runtime=_state_dir(),
        client=_llm_client(),
        observations=str(params.get("observations") or ""),
        runner_kind=str(params.get("runner") or default_runner_kind()),
        events=getattr(session, "store", None),
        session_id=getattr(session, "session_id", "cu"),
        expect_change=str(params.get("expect_change") or ""),
        post_text=str(params.get("post_text") or ""),
    )
    _trace(
        f"computer_use entry: status={outcome.get('status')} "
        f"runner={outcome.get('runner')}"
    )
    return json.dumps(outcome, default=str, sort_keys=True)


class LiveSurface:
    """One session's model-facing live surface: index, view, registrations."""

    def __init__(self, state_dir: Path, session_id: str) -> None:
        from godsv.liveinsert import CapabilityIndex

        self.session_id = session_id
        self.index = CapabilityIndex(state_dir)
        self.view = self.index.runtime_view()
        self.registered: dict = {}
        self.last_sync = 0.0
        self.last_report: dict = {}

    def project_root(self) -> Path:
        session = _SESSIONS.get(self.session_id)
        try:
            return session._repo_root_hint() if session is not None else Path.cwd()
        except Exception:
            return Path.cwd()

    def project_id(self) -> str:
        try:
            from godsv.capability import project_id_for

            return project_id_for(self.project_root())
        except Exception:
            return ""


def _live_surface(session: GodsvSession) -> LiveSurface:
    """The session's live surface — kept across session-object churn.

    Live-found (14.09.): the session object can be dropped and re-created
    between two turns; a per-object surface would then hand out a fresh index
    (re-inserting every already-known capability and forgetting the native
    registrations). The surface is keyed by session id in a module-level map
    instead, so "no restart" survives the churn.
    """
    surface = _LIVE_SURFACES.get(session.session_id) or getattr(session, "live", None)
    if surface is None:
        surface = LiveSurface(session.state_dir, session.session_id)
    _LIVE_SURFACES[session.session_id] = surface
    session.live = surface
    return surface


def _apply_live_surface(session: GodsvSession, surface: LiveSurface) -> dict:
    """Register the hot set natively, deregister what left it. Never raises."""
    from godsv.liveinsert import approved_spec, native_handler, native_schema, surface_plan

    plan = surface_plan(surface.index, list(surface.registered))
    for name in plan.get("remove", []):
        handle = surface.registered.pop(name, None)
        try:
            if handle is not None and hasattr(handle, "dispose"):
                handle.dispose()
        except Exception as exc:  # noqa: BLE001 - a broken handle must not block the boundary
            _trace(f"liveinsert: deregister {name} failed: {type(exc).__name__}: {exc}")
    added: list[str] = []
    for name in plan.get("add", []):
        if name in surface.registered or _CTX is None or not hasattr(_CTX, "register_tool"):
            continue
        spec = approved_spec(surface.index, name)
        if spec is None:
            continue
        schema = native_schema(spec)
        enriched = _enrich_schema_with_metadata(surface, name, schema)
        if enriched is not None:
            schema = enriched
        try:
            handle = _CTX.register_tool(
                name=str(name),
                toolset=LIVE_TOOLSET,
                schema=schema,
                handler=native_handler(surface.index, name),
                description=str(schema.get("description") or name),
                emoji="🧠",
            )
        except Exception as exc:  # noqa: BLE001 - registration is additive, never fatal
            _trace(f"liveinsert: register {name} failed: {type(exc).__name__}: {exc}")
            continue
        if handle is None:
            _trace(f"liveinsert: register {name} refused by the host registry (name claimed?)")
            continue
        surface.registered[str(name)] = handle
        added.append(str(name))
    return {
        "add": added,
        "remove": list(plan.get("remove", [])),
        "live": sorted(surface.registered),
    }


def _live_boundary(session: GodsvSession) -> dict:
    """§1.3: sync + insert + register at a real model boundary. Never raises."""
    try:
        surface = _live_surface(session)
    except Exception as exc:  # noqa: BLE001
        _trace(f"liveinsert: surface unavailable: {type(exc).__name__}: {exc}")
        return {}
    now = time.monotonic()
    if surface.last_sync and (now - surface.last_sync) < LIVE_SYNC_MIN_INTERVAL_S:
        return {"skipped": "throttled"}
    surface.last_sync = now
    try:
        surface.index.project_id = surface.project_id()
        if not surface.index.boundary_safe():
            # a call is in flight: the surface is not re-shaped right now
            return {"deferred": True}
        report = surface.index.sync_registry()
        applied = (
            surface.index.apply_pending(surface.view).get("applied", [])
            if report.get("inserted")
            else []
        )
        plan = _apply_live_surface(session, surface)
        result = {
            "reason": "pre_llm_call",
            "inserted": list(report.get("inserted", [])),
            "blocked": dict(report.get("blocked", {})),
            "skipped": dict(report.get("skipped", {})),
            "applied": list(applied),
            "add": list(plan.get("add", [])),
            "remove": list(plan.get("remove", [])),
            "live": list(plan.get("live", [])),
        }
        surface.last_report = result
        if result["inserted"] or result["add"] or result["remove"]:
            _trace(
                "liveinsert boundary "
                f"session={session.session_id} inserted={result['inserted']} "
                f"add={result['add']} remove={result['remove']} live={result['live']} "
                f"blocked={result['blocked']}"
            )
        return result
    except Exception as exc:  # noqa: BLE001 - a broken surface must never break a turn
        _trace(f"liveinsert boundary failed: {type(exc).__name__}: {exc}")
        return {}


def _maybe_autolink_mcp(session: GodsvSession) -> None:
    """§1.4: make the host reach the godsv MCP surface — once per process.

    Non-blocking (the host CLI probes the server and can take seconds) and
    idempotent (``ensure_linked`` writes nothing when the entry already
    matches). ``GODSV_MCP_AUTOLINK=0`` is the kill switch. A config change is
    NOT a live proof — the insertion proof is the native surface above.
    """
    global _LINK_ATTEMPTED
    if os.environ.get(_AUTOLINK_ENV, "1").strip() == "0":
        return
    with _LINK_LOCK:
        if _LINK_ATTEMPTED:
            return
        _LINK_ATTEMPTED = True
    try:
        root = _live_surface(session).project_root()
    except Exception:  # noqa: BLE001
        root = Path.cwd()

    def work() -> None:
        try:
            from godsv.mcp_link import ensure_linked, link_state, server_entry

            entry = server_entry(project_id=str(root), runtime=str(session.state_dir))
            state = link_state(entry=entry)
            if state["matches"]:
                _trace("mcp link: already linked and matching")
                return
            report = ensure_linked(entry=entry, replace=False)
            _trace(
                f"mcp link: action={report.get('action')} "
                f"reason={report.get('reason', '')} "
                f"others_unchanged={report.get('others_unchanged')}"
            )
        except Exception as exc:  # noqa: BLE001
            _trace(f"mcp link failed: {type(exc).__name__}: {exc}")

    threading.Thread(target=work, name="godsv-mcp-link", daemon=True).start()


# --------------------------------------------------------------------------- #
# §4c capability discovery at the chat boundary: triggers, cache, candidates
# --------------------------------------------------------------------------- #
# Additive on purpose: discovery never steers, never blocks a turn and never
# searches for every token. It runs on real triggers — a new or materially
# changed need, a structured capability gap / unfit tool result, a moved
# registry revision (``tool_published`` / new manifests), a project or
# permission change — and injects a small candidate set through the sanctioned
# ``pre_llm_call`` channel (appended to the user message, never the system
# prompt). ``GODSV_DISCOVERY=0`` is the kill switch.
DISCOVERY_ENV = "GODSV_DISCOVERY"
DISCOVERY_PERMISSIONS_ENV = "GODSV_DISCOVERY_PERMISSIONS"
DISCOVERY_MIN_INTERVAL_S = 3.0
DISCOVERY_MAX_CANDIDATES = 3
DISCOVERY_CACHE_TTL_S = 300.0
DISCOVERY_MAX_NEED_CHARS = 400
DISCOVERY_MAX_INJECTIONS = 8

# A structured gap: the host/a tool says a capability is missing. Matched on
# payload keys and error classes, never on free prose.
GAP_MARKERS = (
    "unknown tool",
    "tool not found",
    "tool_not_found",
    "not available for routing",
    "no such tool",
    "capability_gap",
)
# The tool ran but did not fit the task (schema/format), so the *next* need is
# a candidate for a fresh search.
UNFIT_MARKERS = (
    "schema mismatch",
    "schema_mismatch",
    "wrong output",
    "unsupported format",
)

_DISCOVERY_STATES: dict = {}


def _discovery_enabled() -> bool:
    return os.environ.get(DISCOVERY_ENV, "1").strip().lower() not in ("0", "off", "false")


def _discovery_permissions() -> list[str] | None:
    """The caller's declared rights (env), or None = undeclared (honest)."""
    raw = os.environ.get(DISCOVERY_PERMISSIONS_ENV)
    if raw is None:
        return None
    return [item.strip() for item in raw.split(",") if item.strip()]


def _need_of_turn(user_message) -> str:
    return re.sub(r"\s+", " ", str(user_message or "")).strip()[:DISCOVERY_MAX_NEED_CHARS]


class DiscoveryState:
    """Per-session discovery bookkeeping: triggers, cache, last injection."""

    def __init__(self, session_id: str, state_dir: Path) -> None:
        from godsv.discovery import DiscoveryCache

        self.session_id = session_id
        self.state_dir = Path(state_dir)
        self.cache = DiscoveryCache(ttl_seconds=DISCOVERY_CACHE_TTL_S)
        self.pending: list = []
        self.reported_needs: set = set()
        self.last_need = ""
        self.last_project = ""
        self.last_revision = ""
        self.last_injected = ""
        self.last_run = 0.0
        self.injections = 0
        self.stats = {"runs": 0, "injections": 0, "repeats_suppressed": 0, "throttled": 0}

    # -- triggers (no search runs here; this only classifies the situation) --
    def observe_tool_result(
        self,
        tool_name: str | None,
        result=None,
        status: str | None = None,
        error_type: str | None = None,
        error_message: str | None = None,
    ) -> None:
        text = " ".join(
            str(item or "") for item in (status, error_type, error_message, result)
        )[:2000].lower()
        for marker in GAP_MARKERS:
            if marker in text:
                self.pending.append(f"capability_gap:{marker}")
                return
        for marker in UNFIT_MARKERS:
            if marker in text:
                self.pending.append(f"tool_result_unfit:{marker}")
                return

    def triggers(self, need: str, project_id: str, revision: str) -> list:
        from godsv.discovery import normalize

        triggers = list(self.pending)
        if need and normalize(need) != normalize(self.last_need):
            triggers.append("new_need" if not self.last_need else "criteria_change")
        if project_id and self.last_project and project_id != self.last_project:
            triggers.append("project_change")
        if revision and self.last_revision and revision != self.last_revision:
            triggers.append("registry_revision")
        return triggers

    def note(self, need: str, project_id: str, revision: str) -> None:
        self.pending.clear()
        self.last_need = need
        self.last_project = project_id
        self.last_revision = revision


def _discovery_state_for(session) -> DiscoveryState:
    state = _DISCOVERY_STATES.get(session.session_id)
    if state is None:
        state = DiscoveryState(session.session_id, session.state_dir)
        _DISCOVERY_STATES[session.session_id] = state
    return state


def _discovery_boundary(session, user_message) -> str | None:
    """One turn's discovery decision. Additive: never raises, never steers."""
    if not _discovery_enabled():
        return None
    try:
        from godsv import discovery

        state = _discovery_state_for(session)
        try:
            project_id = _live_surface(session).project_id()
        except Exception:
            project_id = ""
        revision_info = discovery.registry_revision(state.state_dir, project_id=project_id)
        revision = str(revision_info.get("revision") or "")
        need = _need_of_turn(user_message)
        triggers = state.triggers(need, project_id, revision)
        if revision != state.last_revision or (project_id and project_id != state.last_project):
            # invalidation events: a moved revision/project must never be
            # answered from a cached (possibly negative) result
            dropped = state.cache.invalidate("revision/project change")
            if dropped:
                _trace(f"discovery cache invalidated ({dropped} entries)")
        state.note(need, project_id, revision)
        if not triggers or not need:
            return None
        now = time.monotonic()
        if state.last_run and (now - state.last_run) < DISCOVERY_MIN_INTERVAL_S:
            state.stats["throttled"] += 1
            return None
        state.last_run = now
        result = discovery.discover(
            need,
            runtime=state.state_dir,
            project_id=project_id,
            permissions=_discovery_permissions(),
            limit=DISCOVERY_MAX_CANDIDATES,
            cache=state.cache,
        )
        state.stats["runs"] += 1
        block = discovery.context_block(result, limit=DISCOVERY_MAX_CANDIDATES)
        executable = [row for row in (result.get("results") or []) if row.get("executable")]
        gap_trigger = any(str(item).startswith("capability_gap") for item in triggers)
        if not executable and gap_trigger:
            _record_capability_gap(session, state, need, result)
        error_code = (result.get("error") or {}).get("code")
        # A bare no-match carries no actionable candidate: stay silent instead
        # of filling every plain turn with discovery noise (contract §5). Only a
        # structured capability gap (a tool the host reported missing) still
        # surfaces the honest empty result, so the model can ask for a build.
        if error_code == "no_match" and not gap_trigger:
            return None
        if not (executable or result.get("build_candidates") or error_code):
            return None
        if block == state.last_injected and not gap_trigger:
            state.stats["repeats_suppressed"] += 1
            return None
        if state.injections >= DISCOVERY_MAX_INJECTIONS:
            return None
        state.injections += 1
        state.last_injected = block
        state.stats["injections"] += 1
        _trace(
            f"discovery session={session.session_id} triggers={triggers} "
            f"cache={result.get('cache', {}).get('hit')} "
            f"top={[row['name'] for row in (result.get('results') or [])][:3]} "
            f"error={(result.get('error') or {}).get('code')}"
        )
        return block
    except Exception as exc:  # noqa: BLE001 - discovery must never break a turn
        _trace(f"discovery boundary failed: {type(exc).__name__}: {exc}")
        return None


def _record_capability_gap(session, state: DiscoveryState, need: str, result: dict) -> None:
    """One event per unmet need (existing kind — events.py belongs to B7)."""
    from godsv.discovery import normalize

    key = normalize(need)
    if key in state.reported_needs:
        return
    state.reported_needs.add(key)
    try:
        session._record(
            "capability_requested",
            {
                "need": need[:200],
                "source": "discovery",
                "error": (result.get("error") or {}).get("code") or "no_match",
                "candidates": len(result.get("results") or []),
                "build_candidates": [
                    row.get("name") for row in (result.get("build_candidates") or [])
                ][:3],
            },
        )
    except Exception as exc:  # noqa: BLE001 - eventing must never lose the turn
        _trace(f"discovery gap event failed: {type(exc).__name__}: {exc}")


def _enrich_schema_with_metadata(surface, name: str, schema: dict) -> dict | None:
    """Feed the host bridge (§1.3 ``tool_search``/``tool_describe``) the new metadata.

    The registered tool description is what the host's catalog matches on, so
    the documented discovery fields (tasks, synonyms, DE/EN formulations,
    input/output types, preconditions, non-use cases) ride along with the
    contract description. Additive and fail-safe: ``None`` keeps the original
    schema untouched.
    """
    try:
        from godsv.capability_mining import capability_metadata

        meta = next(
            (
                row
                for row in capability_metadata(surface.index.runtime)
                if str(row.get("name")) == str(name)
            ),
            None,
        )
        if not meta:
            return None
        parts = [str(schema.get("description") or "").strip() or str(name)]
        for label, key in (
            ("does", "effect"),
            ("tasks", "tasks"),
            ("synonyms", "synonyms"),
            ("de", "phrases_de"),
            ("en", "phrases_en"),
            ("inputs", "inputs"),
            ("outputs", "outputs"),
            ("preconditions", "preconditions"),
            ("not for", "non_use_cases"),
        ):
            value = meta.get(key)
            if isinstance(value, (list, tuple)) and value:
                parts.append(f"{label}: " + "; ".join(str(item) for item in value[:8]))
            elif isinstance(value, str) and value.strip():
                parts.append(f"{label}: {value.strip()}")
        enriched = dict(schema)
        enriched["description"] = " | ".join(part for part in parts if part)[:2000]
        return enriched
    except Exception as exc:  # noqa: BLE001 - enrichment must never block registration
        _trace(f"schema enrichment failed for {name}: {type(exc).__name__}: {exc}")
        return None


def on_session_start(session_id: str | None = None, **kwargs) -> None:
    if mode() == "off":
        return
    _trace(f"on_session_start session={session_id}")
    session = _session(session_id)
    session.started = True
    # Generated testing tools reachable for the completion gate (P0-2):
    # idempotent registrar — only validated tools; failures stay silent.
    try:
        from godsv.capability import register_testing_tools

        registered = register_testing_tools(session.state_dir)
        if registered:
            _trace(f"testing tools registered: {', '.join(registered)}")
    except Exception as exc:
        _trace(f"testing tool registration failed: {type(exc).__name__}: {exc}")
    # §1.4: the host-side MCP link, on demand only (no-op when already linked)
    _maybe_autolink_mcp(session)


def on_session_end(session_id: str | None = None, **kwargs) -> None:
    session = _SESSIONS.pop(session_id or "default", None)
    _DISCOVERY_STATES.pop(session_id or "default", None)
    if session is not None:
        session.flush()
        session.end()


def pre_tool_call(
    tool_name: str | None = None,
    args: dict | None = None,
    session_id: str | None = None,
    **kwargs,
) -> dict | None:
    """Block a tool call once when a ready voice redirect exists (pause path)."""
    if mode() == "off":
        return None
    session = _SESSIONS.get(session_id or "default")
    if session is None:
        return None
    return session.before_tool_call(tool_name, args)


def pre_verify(
    session_id: str | None = None,
    attempt: int = 0,
    final_response: str | None = None,
    **kwargs,
) -> dict | None:
    """Keep the agent going when completion claims lack evidence (guard path)."""
    if mode() == "off":
        return None
    session = _SESSIONS.get(session_id or "default")
    if session is None or not getattr(session, "started", False):
        # CLI/--resume turns reach the stop gate without on_session_start;
        # when an on-disk contract exists, adopt the session so the goal
        # always passes the completion decision (audit 13.09.). Sessions
        # without a contract stay untouched.
        adopted = None
        if session_id:
            try:
                from godsv.completion import CompletionStore

                if CompletionStore(_state_dir(), session_id=session_id).state_path.exists():
                    adopted = _session(session_id)
                    adopted.started = True
                    _trace(f"pre_verify adopted session={session_id}")
            except Exception:
                adopted = None
        session = adopted
    if session is None:
        return None
    try:
        attempt_value = int(attempt or 0)
    except (TypeError, ValueError):
        attempt_value = 0
    return session.guard_pre_verify(attempt=attempt_value, final_response=final_response or "")


def transform_llm_output(
    response_text: str | None = None, session_id: str | None = None, **kwargs
) -> str | None:
    """Append the guard caveat and the live supervisor block to a final answer.

    The `::godsv-live{…}` paragraph renders the compact Skaldir Agent state
    inline in the transcript (spec: the chat IS the live cockpit) — emitted
    only when this turn had voice activity or the stage changed.
    """
    if mode() == "off":
        return None
    session = _SESSIONS.get(session_id or "default")
    if session is None or not getattr(session, "started", True):
        return None
    _trace(f"transform_llm_output fired session={session_id} mode={mode()}")
    text = session.guard_final(response_text or "")
    base = text if text is not None else (response_text or "")
    block = session.live_block_suffix()
    if block:
        return base + block
    return text


def on_interim_message(text: str | None = None, session_id: str | None = None, **kwargs) -> None:
    """Mid-loop assistant messages: claims are visible before the final answer."""
    if mode() == "off":
        return
    session = _SESSIONS.get(session_id or "default")
    if session is None:
        return
    session.observe("thought", {"text": _truncate(text, 1200), "interim": True})
    session.record_claims(text or "")


def pre_llm_call(session_id: str | None = None, user_message=None, **kwargs):
    """Once per user turn: reset per-turn bookkeeping + teach the contract.

    Returns the contract context block (or ``None``) — the host appends it to
    the user message, so the main agent always sees the live goal, the open
    blockers and the honest-stop convention. §1.3: this hook also sits on the
    real model boundary — verified capabilities are synced and registered here.
    """
    if mode() == "off":
        return None
    session = _SESSIONS.get(session_id or "default")
    if session is None and session_id:
        # CLI/--oneshot turns reach this boundary without on_session_start (the
        # same gap the completion gate adopts for in pre_verify). The live
        # surface and the contract block must exist on a real turn.
        session = _session(session_id)
        _trace(f"pre_llm_call adopted session={session_id}")
    if session is None:
        return None
    session.begin_turn()
    _live_boundary(session)
    try:
        context = session.contract_context(user_message)
    except Exception as exc:
        _trace(f"pre_llm_call context failed: {type(exc).__name__}: {exc}")
        context = None
    block = _discovery_boundary(session, user_message)
    if not block:
        return context
    if isinstance(context, dict):
        existing = str(context.get("context") or "").strip()
        return {**context, "context": f"{existing}\n\n{block}".strip()}
    return {"context": block}


def post_tool_call(
    tool_name: str | None = None,
    args: dict | None = None,
    result=None,
    session_id: str | None = None,
    status: str | None = None,
    error_type: str | None = None,
    error_message: str | None = None,
    **kwargs,
) -> None:
    _trace(f"post_tool_call tool={tool_name} session={session_id} status={status}")
    if mode() == "off":
        return
    session = _session(session_id)
    session.observe("tool_call", {"name": tool_name, "args": args or {}})
    session._maybe_file_edit(tool_name, args)
    payload: dict = {"tool": tool_name, "status": status, "result": _truncate(result)}
    if isinstance(result, str):
        try:
            parsed = json.loads(result)
        except (json.JSONDecodeError, TypeError, ValueError):
            parsed = None
        if isinstance(parsed, dict):
            for key in ("exit_code", "stdout", "stderr", "error"):
                if key in parsed and key not in payload:
                    value = parsed[key]
                    if isinstance(value, str) and len(value) > 4000:
                        value = value[:4000] + " […truncated]"
                    payload[key] = value
    if error_type or error_message:
        payload["error"] = f"{error_type or ''}: {error_message or ''}".strip(": ")
    session.observe("tool_result", payload)
    try:
        _discovery_state_for(session).observe_tool_result(
            tool_name, result=result, status=status,
            error_type=error_type, error_message=error_message,
        )
    except Exception as exc:  # noqa: BLE001 - trigger bookkeeping is not the turn
        _trace(f"discovery trigger observe failed: {type(exc).__name__}: {exc}")


def post_llm_call(assistant_response=None, session_id: str | None = None, **kwargs) -> None:
    _trace(f"post_llm_call session={session_id}")
    if mode() == "off":
        return
    session = _session(session_id)
    session.observe("thought", {"text": _truncate(assistant_response, 2000)})
    session.record_claims(assistant_response or "")


def transform_tool_result(
    tool_name: str | None = None,
    result=None,
    session_id: str | None = None,
    **kwargs,
) -> str | None:
    """Append queued voice hints to a tool result (whisper mode).

    Contract (``model_tools.py::_apply_transform_tool_result_hook``): the
    first string return replaces the result; ``None`` keeps it untouched
    (and lets other plugins' transforms apply). Observed on the executor
    path: fires before ``post_tool_call`` for the same call, so a hint
    raised by call N rides call N+1's result.
    """
    if mode() != "whisper":
        return None
    result_kind = type(result).__name__
    _trace(f"transform called tool={tool_name} session={session_id} result_type={result_kind}")
    session = _SESSIONS.get(session_id or "default")
    if session is None:
        _trace("transform: no session")
        return None
    if not isinstance(result, str):
        _trace("transform: non-string result deferred")
        return None
    pending = session.take_pending()
    if not pending:
        _trace("transform: no pending hints")
        return None
    block = "\n\n".join(pending)
    session.mark_delivered(pending, tool_name)
    _trace(f"transform_tool_result DELIVERED {len(pending)} hint(s) tool={tool_name}")
    return f"{result}\n\n{block}"


def register(ctx) -> None:
    """Register Skaldir Agent hooks on a Hermes plugin context."""
    global _CTX
    _CTX = ctx
    _trace(f"register() called — HERMES_HOME={os.environ.get('HERMES_HOME')} mode={mode()}")
    ctx.register_hook("on_session_start", on_session_start)
    ctx.register_hook("post_tool_call", post_tool_call)
    ctx.register_hook("post_llm_call", post_llm_call)
    ctx.register_hook("on_session_end", on_session_end)
    ctx.register_hook("transform_tool_result", transform_tool_result)
    ctx.register_hook("pre_tool_call", pre_tool_call)
    ctx.register_hook("pre_verify", pre_verify)
    ctx.register_hook("transform_llm_output", transform_llm_output)
    ctx.register_hook("on_interim_message", on_interim_message)
    ctx.register_hook("pre_llm_call", pre_llm_call)
    # §1.3: pin the live toolset with one stable tool. The key must exist at
    # agent-build time, else the toolset filter drops every capability that is
    # registered later at a boundary (live-found 14.09.).
    register_tool = getattr(ctx, "register_tool", None)
    if callable(register_tool):
        try:
            register_tool(
                name=LIVE_REPORT_TOOL,
                toolset=LIVE_TOOLSET,
                schema=LIVE_REPORT_SCHEMA,
                handler=godsv_capabilities,
                description=LIVE_REPORT_SCHEMA["description"],
                emoji="🧠",
            )
        except Exception as exc:  # noqa: BLE001 - a toolset pin must never break the plugin
            _trace(f"live toolset pin failed: {type(exc).__name__}: {exc}")


_trace(f"module imported — HERMES_HOME={os.environ.get('HERMES_HOME')}")
