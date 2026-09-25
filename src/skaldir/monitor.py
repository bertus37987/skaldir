"""Deterministic step-level monitor — the cheap tier of the Voice.

See docs/CORE-PLAN.md §1.4 (Step-Ebene) and §1.7 (Scope-/Slop-Guard).
Runs on every event, costs ~0, and only emits signals; the voice decides
whether anything is delivered to the agent.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import deque
from dataclasses import dataclass, field
from fnmatch import fnmatch

from .events import Event
from .pact import Pact
from .untrusted import UNTRUSTED_RULE, scan_untrusted

SEV_INFO = 1
SEV_WARN = 2
SEV_HIGH = 3


@dataclass(frozen=True)
class Signal:
    kind: str
    severity: int
    fingerprint: str
    message: str
    refs: dict = field(default_factory=dict)


SLOP_RULES: list[tuple[str, int, re.Pattern[str], str]] = [
    ("todo_placeholder", SEV_WARN, re.compile(r"\b(TODO|FIXME)\b"),
     "placeholder marker in the new code"),
    ("stub", SEV_HIGH, re.compile(r"\bNotImplementedError\b"),
     "stub instead of an implementation"),
    ("bare_except", SEV_HIGH, re.compile(r"\bexcept\s*:"),
     "bare `except:` swallows failures"),
    ("except_pass", SEV_HIGH, re.compile(r"except[^:\n]*:\s*pass\b"),
     "`except …: pass` swallows failures"),
    ("assert_true", SEV_HIGH, re.compile(r"\bassert\s+True\b"),
     "test theatre (`assert True`)"),
    ("tbd_comment", SEV_INFO, re.compile(r"(?i)#\s*(tbd|placeholder|stub)\b"),
     "TBD/placeholder in the comment"),
]

TOOL_ERROR_CLASSES: list[tuple[str, re.Pattern[str]]] = [
    (
        "auth",
        re.compile(
            r"(?i)(\b401\b|\b403\b|unauthorized|forbidden|invalid[_ -]?api[_ -]?key"
            r"|authentication|access denied)"
        ),
    ),
    ("rate_limit", re.compile(r"(?i)(\b429\b|rate.?limit|too many requests|quota exceeded)")),
    ("timeout", re.compile(r"(?i)(timed?\s*out|timeout)")),
    (
        "invalid_args",
        re.compile(
            r"(?i)(invalid (arguments?|parameters?|tool_input)|missing required"
            r"|schema (validation )?error|unexpected keyword|jsondecodeerror)"
        ),
    ),
    (
        "unsupported",
        re.compile(r"(?i)(not supported|unsupported|unknown tool|no such tool|not implemented)"),
    ),
    # -- infrastructure classes (P1-6): never debug these as code bugs ------
    (
        "permissions",
        re.compile(
            r"(?i)(permission denied|EACCES|EPERM|read-only file system"
            r"|operation not permitted|no write access)"
        ),
    ),
    (
        "provider_outage",
        re.compile(
            r"(?i)(\b50[0-4]\b|service unavailable|bad gateway|gateway time-?out"
            r"|upstream (connect|error|unavailable)|overloaded(_error)?"
            r"|provider (is )?(down|outage))"
        ),
    ),
    (
        "schema_mismatch",
        re.compile(
            r"(?i)(schema (mismatch|changed|incompatible)|contract mismatch"
            r"|response did not match|unexpected (field|response shape)|json schema.*(fail|error))"
        ),
    ),
    (
        "mcp_unavailable",
        re.compile(
            r"(?i)(mcp (server )?(unavailable|not (running|connected|found|installed))"
            r"|server disconnected|stdio.*(closed|broken)|no such server)"
        ),
    ),
    (
        "ui_failure",
        re.compile(
            r"(?i)(target closed|no such window|element (not|isn't) (found|visible|interactable)"
            r"|net::ERR_|playwright|puppeteer|cdevtools|devtools.*disconnected|browser.*(crash|closed))"
        ),
    ),
    (
        "api_changed",
        re.compile(
            r"(?i)(api.*(changed|deprecated|removed|no longer)|\b410\b|endpoint.*(moved|gone)"
            r"|renamed (endpoint|parameter|field)|breaking change)"
        ),
    ),
]

# Errors that usually mean the agent *invented* an API surface (hallucinated
# names/signatures) rather than the environment being broken.
HALLUCINATION_CLASSES: list[tuple[str, re.Pattern[str]]] = [
    ("attr", re.compile(r"(?i)(has no attribute|has no member|object has no|AttributeError)")),
    (
        "import",
        re.compile(r"(?i)(cannot import name|no module named|ModuleNotFoundError|ImportError)"),
    ),
    (
        "name",
        re.compile(
            r"(?i)(is not defined|NameError|undefined (is not|property)|is not a function"
            r"|TypeError: .*undefined)"
        ),
    ),
    (
        "signature",
        re.compile(r"(?i)(unexpected keyword argument|takes \d+ positional|missing \d+ required)"),
    ),
]

ERROR_RECOVERY: dict[str, str] = {
    "auth": "Clarify access (scopes/key); do not blindly retry.",
    "rate_limit": "Wait out the throttle/backoff instead of retrying immediately.",
    "timeout": "Shrink the scope or raise the timeout deliberately (check the cause).",
    "invalid_args": "Correct the arguments against the tool schema.",
    "permissions": "Check permissions (chmod/owner/sandbox grant) — not a code bug.",
    "provider_outage": (
        "Provider outage: check backoff/status, switch route if needed — "
        "do not debug it as a code bug."
    ),
    "schema_mismatch": (
        "Schema drift: check the response against the expected contract, "
        "adjust parser/version."
    ),
    "mcp_unavailable": (
        "MCP server unavailable: check server/connection — "
        "do not rework your own code."
    ),
    "ui_failure": "Browser/UI disturbed: check window/selector, retry with a fresh tab.",
    "api_changed": "API behaviour changed: check docs/version instead of adapting blindly.",
    "unsupported": "Operation not supported — choose a different path.",
}


def classify_hallucination(text: str) -> str | None:
    """API-shaped errors (invented method/module/signature) — verify the real API."""
    if not text:
        return None
    for class_id, pattern in HALLUCINATION_CLASSES:
        if pattern.search(text):
            return class_id
    return None


def classify_tool_error(text: str) -> str | None:
    """Deterministic tool-error classification (auth/rate_limit/timeout/…)."""
    if not text:
        return None
    for class_id, pattern in TOOL_ERROR_CLASSES:
        if pattern.search(text):
            return class_id
    return None


_REVERT = re.compile(r"\bgit\s+(checkout|revert|reset|stash)\b")
_DEPENDENCY_FILES = re.compile(
    r"(?i)(requirements[\w.-]*\.txt|pyproject\.toml|package\.json|cargo\.toml|poetry\.lock|uv\.lock)"
)

# Block 4 / GS-12: product code vs. infrastructure/config. A provider outage is
# never a reason to change product code — the redirect belongs to backoff,
# routing or config. These two patterns keep that separation checkable.
_CODE_FILES = re.compile(
    r"(?i)\.(?:py|pyi|js|mjs|cjs|jsx|ts|tsx|go|rs|java|kt|rb|php|c|cc|cpp|h|hpp|cs|swift|scala)"
    r"(?:$|\?)"
)
_CONFIG_FILES = re.compile(
    r"(?i)(?:^|/)(?:dockerfile|compose\.ya?ml|makefile|justfile|\.env[\w.]*|[\w.-]*\.ya?ml|"
    r"[\w.-]*\.toml|[\w.-]*\.ini|[\w.-]*\.cfg|[\w.-]*\.conf|[\w.-]*\.service|[\w.-]*\.json)(?:$|\?)"
)


def scan_slop(added_text: str) -> list[tuple[str, int, str, str]]:
    """Return (rule_id, severity, message, matched_text) for slop patterns."""
    hits: list[tuple[str, int, str, str]] = []
    for rule_id, severity, pattern, message in SLOP_RULES:
        for match in pattern.finditer(added_text):
            hits.append((rule_id, severity, message, match.group(0)))
    return hits


def error_text(payload: dict) -> str:
    error = payload.get("error")
    if error:
        return str(error)
    exit_code = payload.get("exit_code")
    if exit_code not in (None, 0):
        stderr = str(payload.get("stderr") or "").strip()
        if stderr:
            return stderr
        # detail-free: the exit status alone is not an error identity
        return f"exit code {exit_code} (no output)"
    return ""


def error_fingerprint(text: str) -> str:
    normalized = re.sub(r"\d+", "#", text.lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()[:160]
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]


_COARSE_TEXT = re.compile(
    r"(?i)^(tool_error:\s*)?exit(\s*code)?\s*#+(\s*\(no output\))?$"
)


def is_coarse_error_text(text: str) -> bool:
    """True when the text carries no identity beyond an exit status.

    Such failures must never be treated as 'the same error' (repeat) or
    matched against known incident fingerprints — the false-loop root
    cause was exactly this blur (sha of 'exit code 1').
    """
    normalized = re.sub(r"\d+", "#", (text or "").lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return bool(_COARSE_TEXT.match(normalized))


def tool_signature(name: str, args: dict) -> str:
    canonical = json.dumps(args, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(f"{name}|{canonical}".encode()).hexdigest()[:12]


def _fp(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


class Monitor:
    """Deterministic step-level detectors. One instance per session."""

    def __init__(
        self,
        pact: Pact | None = None,
        loop_repeat: int = 3,
        loop_window: int = 8,
        error_repeat: int = 2,
        error_window: int = 6,
        stall_after: int = 12,
        known_error_fingerprints: set[str] | None = None,
        churn_lines: int = 400,
        outage_window: int = 6,
    ) -> None:
        self.pact = pact
        self.loop_repeat = loop_repeat
        self.loop_window = loop_window
        self.error_repeat = error_repeat
        self.error_window = error_window
        self.stall_after = stall_after
        self.known_error_fingerprints = set(known_error_fingerprints or ())
        self.churn_lines = churn_lines
        self.outage_window = outage_window
        self._tool_calls: deque[tuple[int, str, str]] = deque(maxlen=200)
        self._last_tool_sig: tuple[int, str, str] | None = None
        self._strategy_hits: dict[tuple[str, str], int] = {}
        self._strategy_variety: dict[str, set[str]] = {}
        self._errors: deque[tuple[int, str]] = deque(maxlen=200)
        self._signaled_clusters: set[str] = set()
        self._hallu_classes: dict[str, int] = {}
        self._last_progress = 0
        self._stall_signaled = False
        self._edit_lines_since_progress = 0
        self._churn_signaled = False
        self._last_outage_step = 0

    def ingest(self, event: Event) -> list[Signal]:
        if event.kind == "tool_call":
            signals = self._on_tool_call(event)
        elif event.kind == "tool_result":
            signals = self._on_tool_result(event)
        elif event.kind == "file_edit":
            signals = self._on_file_edit(event)
        else:
            signals = []
        stall = self._stall_check(event)
        if stall is not None:
            signals.append(stall)
        return signals

    # -- untrusted content boundary (Block 4) -------------------------------
    def ingest_untrusted(
        self, *, step: int, text: str, source: str = "external"
    ) -> list[Signal]:
        """Read external content: a signal is not a verdict (Block 4).

        Content that *tries* to take control produces one of two signals:

        * ``prompt_injection_signal`` — a suspicious pattern was found. It is
          explicitly **not** a verdict; a single string never blocks anything.
        * ``untrusted_instruction_attempt`` — the pattern is addressed to the
          agent and carries an executable demand. Only this is evidence of an
          attempt, and only then does the caller mark the content untrusted.

        Neither kind carries a pause trigger (``pause.TRIGGER_RULES``): the
        boundary reports, the policy decides — never the other way round.
        """
        if not text:
            return []
        try:
            assessment = scan_untrusted(text, source=source)
        except Exception:  # a broken detector must never take the trajectory down
            return []
        if not assessment.findings:
            return []
        refs = {
            "source": source,
            "families": list(assessment.families),
            "match": assessment.findings[0].match[:120],
            "line": assessment.findings[0].line[:160],
            "reason": assessment.reason,
            "untrusted_rule": UNTRUSTED_RULE,
        }
        return [
            Signal(
                kind=assessment.kind(),
                severity=assessment.severity,
                fingerprint=assessment.fingerprint(),
                message=assessment.message(),
                refs=refs,
            )
        ]

    def _note_progress(self, step: int) -> None:
        self._last_progress = step
        self._stall_signaled = False

    def _reset_churn(self) -> None:
        """Churn resets on verified progress (a successful run), not on edits."""
        self._edit_lines_since_progress = 0
        self._churn_signaled = False

    def _stall_check(self, event: Event) -> Signal | None:
        """Long phase without any progress event (success/edit)."""
        if event.kind not in ("tool_call", "tool_result", "file_edit"):
            return None
        if self._stall_signaled or event.step - self._last_progress < self.stall_after:
            return None
        if self._last_progress == 0 and event.step < self.stall_after:
            return None
        self._stall_signaled = True
        return Signal(
            kind="stall",
            severity=SEV_WARN,
            fingerprint=f"stall:{self._last_progress}",
            message=(
                f"No progress for {event.step - self._last_progress} steps "
                f"(last successful step/edit: step {self._last_progress or '—'})"
            ),
            refs={"last_progress": self._last_progress, "step": event.step},
        )

    # -- detectors ----------------------------------------------------------
    def _on_tool_call(self, event: Event) -> list[Signal]:
        name = str(event.payload.get("name", ""))
        raw_args = event.payload.get("args") or {}
        args = raw_args if isinstance(raw_args, dict) else {"raw": str(raw_args)}
        sig = tool_signature(name, args)
        self._last_tool_sig = (event.step, sig, name)
        in_window = sum(
            1
            for step, seen_sig, _name in self._tool_calls
            if seen_sig == sig and event.step - step <= self.loop_window
        )
        signals: list[Signal] = []
        if in_window + 1 >= self.loop_repeat:
            signals.append(
                Signal(
                    kind="loop",
                    severity=SEV_WARN,
                    fingerprint=sig,
                    message=(
                        f"Repeated tool call `{name}` "
                        f"({in_window + 1}× in {self.loop_window} steps)"
                    ),
                    refs={"tool": name, "count": in_window + 1},
                )
            )
        self._tool_calls.append((event.step, sig, name))
        if _REVERT.search(json.dumps(args, ensure_ascii=False, sort_keys=True)):
            signals.append(
                Signal(
                    kind="revert",
                    severity=SEV_WARN,
                    fingerprint=_fp(f"revert:{args.get('command') or args.get('cmd') or name}"),
                    message=(
                        "Reversal operation (git checkout/revert/reset/stash) — intended? "
                        "Do not silently roll back someone else's or already verified changes."
                    ),
                    refs={"tool": name},
                )
            )
        return signals

    def _on_tool_result(self, event: Event) -> list[Signal]:
        text = error_text(event.payload)
        if not text:
            self._note_progress(event.step)
            self._reset_churn()
            return []
        fp = error_fingerprint(text)
        coarse = is_coarse_error_text(text)
        signals: list[Signal] = []
        if coarse:
            # No identity beyond the exit status: never a repeat, never an
            # incident match. A signal about missing evidence, nothing more.
            signals.append(
                Signal(
                    kind="error_without_details",
                    severity=SEV_WARN,
                    fingerprint=f"coarse:{event.step}",
                    message=(
                        f"Error without details ({text[:60]}) — no statement about class, "
                        "repetition or incident linkage is possible."
                    ),
                    refs={"error": text[:120]},
                )
            )
        else:
            previous = [step for step, seen_fp in self._errors if seen_fp == fp]
            # Joint semantic reading (audit 13.09.): when real progress happened
            # since the same error last appeared (a file change, a green side
            # check), the same command is NOT an unchanged strategy — the
            # world changed between the runs. 'strategy_repeat' would be the
            # contradictory half of the pair; the accurate signal in that case
            # is 'error_recurrence_after_progress' below.
            progressed_since = bool(previous) and self._last_progress > previous[0]
            strategy = self._last_tool_sig
            strategy_sig = (
                str(strategy[1]) if strategy and strategy[0] >= event.step - 2 else ""
            )
            strategy_name = str(strategy[2]) if strategy_sig and strategy else ""
            if strategy_sig:
                key = (fp, strategy_sig)
                hits = self._strategy_hits.get(key, 0)
                self._strategy_hits[key] = hits + 1
                variety = self._strategy_variety.setdefault(fp, set())
                variety.add(strategy_sig)
                if hits >= 1 and not progressed_since:
                    signals.append(
                        Signal(
                            kind="strategy_repeat",
                            severity=SEV_WARN,
                            fingerprint=f"strat:{fp}:{strategy_sig}",
                            message=(
                                f"Same strategy '{strategy_name or strategy_sig}' "
                                "repeated against the same error — change the strategy "
                                "instead of blind-retrying."
                            ),
                            refs={
                                "error": text[:120],
                                "strategy": strategy_name or strategy_sig,
                                "variety": len(variety),
                            },
                        )
                    )
            in_window = sum(
                1 for step in previous if event.step - step <= self.error_window
            )
            distinct_in_window = {
                seen_fp for step, seen_fp in self._errors if event.step - step <= self.error_window
            }
            distinct_in_window.add(fp)  # the current failure counts as its own signature
            if in_window + 1 >= self.error_repeat:
                known = fp in self.known_error_fingerprints
                if progressed_since:
                    # the same signature after real progress: recovery in
                    # progress or a fix that did not hold — a signal for the
                    # voice, not a pause-worthy loop verdict (debugging is
                    # allowed to contain several failures).
                    signals.append(
                        Signal(
                            kind="error_recurrence_after_progress",
                            severity=SEV_WARN,
                            fingerprint=f"recur:{fp}",
                            message=(
                                f"Same error signature {in_window + 1}× in "
                                f"{self.error_window} steps, but real progress happened "
                                "in between — first check whether the last repair attempt held."
                            ),
                            refs={"error": text[:160]},
                        )
                    )
                elif known:
                    signals.append(
                        Signal(
                            kind="known_incident_repeat",
                            severity=SEV_HIGH,
                            fingerprint=fp,
                            message=(
                                "A known error from earlier sessions is repeating "
                                f"({in_window + 1}× in {self.error_window} steps, with no "
                                "progress in between) — memory holds an incident for it; "
                                "check the documented cause first."
                            ),
                            refs={"error": text[:160], "known_incident": True},
                        )
                    )
                else:
                    signals.append(
                        Signal(
                            kind="error_burst",
                            severity=SEV_HIGH,
                            fingerprint=fp,
                            message=(
                                f"Same error signature {in_window + 1}× in "
                                f"{self.error_window} Steps"
                            ),
                            refs={"error": text[:160]},
                        )
                    )
            # distinct UNRELATED failures in the window: an honest signal
            # (symptoms to inspect), never a loop proof — and no pause
            # trigger (the kind has no TRIGGER_RULES entry).
            cluster_key = f"cluster:{len(distinct_in_window)}"
            if len(distinct_in_window) >= 3 and cluster_key not in self._signaled_clusters:
                self._signaled_clusters.add(cluster_key)
                signals.append(
                    Signal(
                        kind="error_cluster",
                        severity=SEV_WARN,
                        fingerprint=f"cluster:{event.step}",
                        message=(
                            f"{len(distinct_in_window)} distinct error signatures in "
                            f"{self.error_window} steps — inspect the symptoms; this is no "
                            "proof of a loop."
                        ),
                        refs={"distinct": len(distinct_in_window)},
                    )
                )
        error_class = classify_tool_error(text)
        if error_class is not None:
            if error_class in ("provider_outage", "mcp_unavailable"):
                # GS-12: remember when infrastructure (not code) broke, so a
                # product-code edit right after it can be flagged as a
                # misdiagnosis.
                self._last_outage_step = event.step
            signals.append(
                Signal(
                    kind=f"tool_error_{error_class}",
                    severity=SEV_WARN,
                    fingerprint=f"errcls:{error_class}",
                    message=(
                        f"Tool-Fehlerklasse `{error_class}`: {ERROR_RECOVERY.get(error_class, '')}"
                    ),
                    refs={"class": error_class, "error": text[:160]},
                )
            )
        hallucination = classify_hallucination(text)
        if hallucination is not None:
            self._hallu_classes[hallucination] = self._hallu_classes.get(hallucination, 0) + 1
            # one API-shaped error can be legitimate; a second one of the same
            # class strongly suggests an invented API surface
            if self._hallu_classes[hallucination] >= 2:
                signals.append(
                    Signal(
                        kind="hallucinated_api",
                        severity=SEV_HIGH,
                        fingerprint=f"hallu:{hallucination}",
                        message=(
                            f"API error ({hallucination}) repeated — likely an invented "
                            "API/signature: verify the real interface (docs/source) "
                            "instead of guessing again."
                        ),
                        refs={"class": hallucination, "error": text[:160]},
                    )
                )
        if not coarse:
            self._errors.append((event.step, fp))
        return signals

    def _on_file_edit(self, event: Event) -> list[Signal]:
        path = str(event.payload.get("path", ""))
        added = str(event.payload.get("added") or "")
        signals: list[Signal] = []
        self._note_progress(event.step)
        self._edit_lines_since_progress += len(added.splitlines())
        # Block 4 / GS-12: a provider outage is never a reason to change product
        # code. Right after an infrastructure error the redirect belongs to
        # backoff/routing/config — changing src is a misdiagnosis.
        if (
            self._last_outage_step
            and event.step - self._last_outage_step <= self.outage_window
            and path
            and _CODE_FILES.search(path)
            and not _CONFIG_FILES.search(path)
        ):
            signals.append(
                Signal(
                    kind="outage_misdiagnosis",
                    severity=SEV_WARN,
                    fingerprint=_fp(f"outage_misdiag:{path}"),
                    message=(
                        f"Product code changed ({path}) shortly after an infrastructure error "
                        f"(step {self._last_outage_step}) — provider/MCP outages are not "
                        "code bugs: check backoff/route/configuration, not the product code."
                    ),
                    refs={"path": path, "outage_step": self._last_outage_step},
                )
            )
        if self._edit_lines_since_progress >= self.churn_lines and not self._churn_signaled:
            self._churn_signaled = True
            signals.append(
                Signal(
                    kind="diff_churn",
                    severity=SEV_WARN,
                    fingerprint=f"churn:{self._last_progress}",
                    message=(
                        f"~{self._edit_lines_since_progress} new lines since the last "
                        "successful run — the diff grows without proportional progress. "
                        "Cut smaller and verify the last green state."
                    ),
                    refs={"lines": self._edit_lines_since_progress},
                )
            )
        if path and _DEPENDENCY_FILES.search(path):
            signals.append(
                Signal(
                    kind="dependency_change",
                    severity=SEV_WARN,
                    fingerprint=_fp(f"dep:{path}"),
                    message=(
                        f"Dependency/build file changed: {path} — check new dependencies/refactors "
                        "against the pact (a warning is not proof of a defect)."
                    ),
                    refs={"path": path},
                )
            )
        for rule_id, severity, message, matched in scan_slop(added):
            signals.append(
                Signal(
                    kind=f"slop_{rule_id}",
                    severity=severity,
                    fingerprint=_fp(f"slop:{rule_id}:{path}"),
                    message=f"{message} ({path})",
                    refs={"path": path, "match": matched},
                )
            )
        if (
            self.pact
            and self.pact.scope_paths
            and path
            and not any(fnmatch(path, pattern) for pattern in self.pact.scope_paths)
        ):
            signals.append(
                Signal(
                    kind="scope_drift",
                    severity=SEV_WARN,
                    fingerprint=_fp(f"scope:{path}"),
                    message=f"File changed outside the pact scope: {path}",
                    refs={"path": path},
                )
            )
        return signals
