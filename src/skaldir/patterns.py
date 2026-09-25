"""Sequence patterns over tool/command trajectories (Block 4).

Detects *patterns in sequences*, not identical strings and not exit-code
counts. Everything works on a normalised **effect signature**:

``<action-class>·<target-class>``

so ``pytest -q --maxfail=1 tests/test_a.py`` and ``python -m pytest tests/test_a.py``
collapse to the same ``test·tests`` effect — cosmetically different, identical
in consequence. A bare exit code carries no identity and is never used as one
(:mod:`skaldir.monitor` already pins that lesson for error fingerprints).

Kinds (all emitted as ``sequence_pattern`` signals with ``refs["pattern"]``):

======================  ===================================================
``tool_loop``           same tool+effect repeated inside the window
``command_loop``        same normalised command repeated inside the window
``oscillation``         A → B → A flapping (revert/reapply, toggle)
``dependency_churn``    dependency manifest edits ≥ N times without progress
``no_new_evidence``     activity without any new evidence/observation
``retry_without_strategy_change``  same failure retried, strategy unchanged
``test_without_change`` a test run with no source change since the last run
``change_without_verification``    source changes, then no verification runs
``search_without_progress``        repeated searches yielding no new result set
======================  ===================================================

Determinism: pure functions over an event window. No LLM, no I/O.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass, field

SEV_INFO = 1
SEV_WARN = 2
SEV_HIGH = 3

PATTERN_TOOL_LOOP = "tool_loop"
PATTERN_COMMAND_LOOP = "command_loop"
PATTERN_OSCILLATION = "oscillation"
PATTERN_DEPENDENCY_CHURN = "dependency_churn"
PATTERN_NO_NEW_EVIDENCE = "no_new_evidence"
PATTERN_RETRY_WITHOUT_STRATEGY_CHANGE = "retry_without_strategy_change"
PATTERN_TEST_WITHOUT_CHANGE = "test_without_change"
PATTERN_CHANGE_WITHOUT_VERIFICATION = "change_without_verification"
PATTERN_SEARCH_WITHOUT_PROGRESS = "search_without_progress"

PATTERNS = (
    PATTERN_TOOL_LOOP,
    PATTERN_COMMAND_LOOP,
    PATTERN_OSCILLATION,
    PATTERN_DEPENDENCY_CHURN,
    PATTERN_NO_NEW_EVIDENCE,
    PATTERN_RETRY_WITHOUT_STRATEGY_CHANGE,
    PATTERN_TEST_WITHOUT_CHANGE,
    PATTERN_CHANGE_WITHOUT_VERIFICATION,
    PATTERN_SEARCH_WITHOUT_PROGRESS,
)

# --------------------------------------------------------------------------- #
# normalisation: text → effect class                                           #
# --------------------------------------------------------------------------- #

# noise that never changes what a command does
_NOISE = re.compile(
    r"(?i)(?:\s+(?:-q|--quiet|--verbose|-v+|--color(?:=\w+)?|--no-header|-p\s+no:cacheprovider"
    r"|--tb=\w+|--maxfail=\d+|--durations=\d+|--capture=\w+|--junitxml=\S+))\b"
)
_TMP_PATH = re.compile(r"(?:/tmp|/var/tmp|/dev/shm)/[\w.\-]+")
_NUMBER = re.compile(r"\b\d+\b")

_TEST_RUNNERS = re.compile(
    r"(?i)\b(?:pytest|py\.test|unittest|tox|nox|jest|vitest|mocha|rspec|go\s+test|cargo\s+test|"
    r"phpunit|ctest|catch2)\b"
)
_CHANGE_RUNNERS = re.compile(
    r"(?i)\b(?:ruff\s+check|black|isort|prettier|eslint|tsc\b|mypy|pyright|flake8|pylint|"
    r"cargo\s+(?:build|clippy|check)|go\s+(?:build|vet)|npm\s+run\s+(?:build|lint|typecheck))\b"
)
_INSTALL = re.compile(
    r"(?i)\b(?:pip|pip3|uv|poetry|conda|npm|yarn|pnpm|pipx|gem|cargo|go)\s+"
    r"(?:install|add|sync|lock|update|upgrade|remove|uninstall|require)\b"
)
_SEARCH = re.compile(
    r"(?i)\b(?:grep|rg|ag|ack|find|fd|locate|git\s+grep|tree|ls|glob|search_files|web_search)\b"
)
_READ_FILES = re.compile(r"(?i)^\s*(?:cat|less|more|head|tail|sed|awk|read_file|wc)\b")
_GIT_READ = re.compile(
    r"(?i)\bgit\s+(?:status|diff|log|show|branch|remote|describe|rev-parse|ls-files)\b"
)
_GIT_WRITE = re.compile(
    r"(?i)\bgit\s+(?:commit|push|pull|fetch|checkout|revert|reset|stash|clean|rebase|merge|tag|add)\b"
)
_REVERT_LIKE = re.compile(
    r"(?i)\b(?:git\s+(?:checkout|revert|reset|stash|restore)|undo|rollback)\b"
)
_DEPENDENCY_FILE = re.compile(
    r"(?i)(?:requirements[\w.-]*\.txt|pyproject\.toml|package\.json|package-lock\.json|"
    r"cargo\.toml|cargo\.lock|poetry\.lock|uv\.lock|pipfile|go\.mod|go\.sum|gemfile|"
    r"composer\.json|pom\.xml|build\.gradle)"
)
_VERIFY_CMD = re.compile(
    r"(?i)\b(?:pytest|ruff|mypy|pyright|black|eslint|tsc\b|npm\s+run\s+(?:build|test|lint)"
    r"|cargo\s+(?:test|check|build)|go\s+(?:test|build|vet)|make\s+(?:test|check|build))\b"
)
_LONG_OPTION_VALUE = re.compile(r"(?:^|\s)(--[\w-]+=)(\S+)")


def normalize_command(command: str) -> str:
    """Canonical form of a shell command: cosmetics and numbers removed."""
    text = (command or "").strip()
    if not text:
        return ""
    text = _NOISE.sub(" ", text)
    text = _TMP_PATH.sub("<tmp>", text)
    # --flag=value / --flag value → keep the flag name only (its value rarely
    # changes the *effect class*, but a changed value IS a strategy change —
    # so keep values that look like paths/checks, drop pure enumerations)
    text = _LONG_OPTION_VALUE.sub(lambda m: f" {m.group(1)}", text)
    text = _NUMBER.sub("#", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def effect_signature(tool: str, args: dict | None) -> str:
    """``<action-class>·<target-class>`` — cosmetic changes collapse."""
    tool = (tool or "").strip().lower()
    args = args if isinstance(args, dict) else {}
    command = ""
    for key in ("command", "cmd", "script"):
        if args.get(key):
            command = str(args[key])
            break
    targets: list[str] = []
    for key in ("path", "file_path", "filepath", "target", "dest"):
        if args.get(key):
            targets.append(str(args[key]))
    for key in ("paths", "files"):
        value = args.get(key)
        if isinstance(value, (list, tuple)):
            targets.extend(str(item) for item in value)
    if command:
        targets.extend(
            re.findall(r"(?:>>?|tee\s+(?:-a\s+)?)\s*([~./\w][^\s\"'<>|;)]*)", command)
        )
    action = _action_class(tool, command, args)
    target = _target_class(command, targets)
    return f"{action}·{target}"


def _action_class(tool: str, command: str, args: dict) -> str:
    if command:
        if _REVERT_LIKE.search(command):
            return "revert"
        if _TEST_RUNNERS.search(command):
            return "test"
        if _INSTALL.search(command):
            return "install"
        if _DEPENDENCY_FILE.search(command) and re.search(r"(?i)(>|tee|sed\s+-i)", command):
            return "manifest"
        if _VERIFY_CMD.search(command):
            return "verify"
        if _CHANGE_RUNNERS.search(command):
            return "verify"
        if _SEARCH.search(command):
            return "search"
        if _READ_FILES.search(command):
            return "read"
        if _GIT_WRITE.search(command):
            return "vcs-write"
        if _GIT_READ.search(command):
            return "vcs-read"
        first = re.split(r"\s+", command.strip(), maxsplit=1)[0]
        return f"cmd:{first}" if first else "cmd"
    if tool in {
        "write_file",
        "create_file",
        "write",
        "patch",
        "apply_patch",
        "edit_file",
        "str_replace",
    }:
        path = " ".join(str(args.get(key) or "") for key in ("path", "file_path", "filepath"))
        if _DEPENDENCY_FILE.search(path):
            return "manifest"
        return "edit"
    if tool in {"read_file", "read", "ls", "list_dir"}:
        return "read"
    if tool in {"grep", "glob", "search_files", "web_search", "web_extract", "session_search"}:
        return "search"
    if tool in {"computer_use", "click", "type_text"}:
        return "ui"
    if tool in {"mcp", "call_mcp"}:
        return "mcp"
    return f"tool:{tool or 'unknown'}"


def _target_class(command: str, targets: list[str]) -> str:
    # `/dev/null`, `/dev/stderr` … are discards, not file targets (the plugin's
    # file-edit mirroring excludes them for the same reason).
    targets = [
        item for item in targets if item and not item.startswith("/dev/")
    ]
    blob = " ".join(targets)
    if _DEPENDENCY_FILE.search(blob) or _DEPENDENCY_FILE.search(command):
        return "deps"
    if targets:
        first = targets[0]
        parts = [part for part in re.split(r"[\\/]+", first) if part and part != "~"]
        if parts:
            return parts[0]
        return "root"
    if _TEST_RUNNERS.search(command):
        return "suite"
    return "none"


def effect_fingerprint(tool: str, args: dict | None) -> str:
    """Short stable hash of the effect signature (for dedup/fingerprints)."""
    return hashlib.sha256(effect_signature(tool, args).encode("utf-8")).hexdigest()[:12]


# --------------------------------------------------------------------------- #
# observations: one per event, in order                                        #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Observation:
    """One step of the trajectory, already reduced to what patterns care about."""

    step: int
    kind: str  # tool_call | tool_result | file_edit | claim | thought
    tool: str = ""
    args: dict = field(default_factory=dict)
    effect: str = ""
    command: str = ""
    normalized: str = ""
    failed: bool = False
    succeeded: bool = False
    evidence: str = ""  # a *new* piece of evidence (hash of first-seen observation)
    changed: bool = False  # a source edit landed
    verified: bool = False  # a verification ran successfully

    @property
    def is_activity(self) -> bool:
        return self.kind in ("tool_call", "tool_result", "file_edit")


def observe_call(step: int, tool: str, args: dict | None) -> Observation:
    args = args if isinstance(args, dict) else {}
    command = ""
    for key in ("command", "cmd", "script"):
        if args.get(key):
            command = str(args[key])
            break
    return Observation(
        step=step,
        kind="tool_call",
        tool=(tool or "").strip(),
        args=args,
        effect=effect_signature(tool, args),
        command=command,
        normalized=normalize_command(command),
    )


def observe_edit(step: int, path: str, added: str = "") -> Observation:
    args = {"path": path, "added": added}
    return Observation(
        step=step,
        kind="file_edit",
        tool="write_file",
        args=args,
        effect=effect_signature("write_file", args),
        changed=True,
        evidence=_digest(f"edit:{path}:{added[:200]}"),
    )


def observe_result(
    step: int,
    *,
    failed: bool = False,
    succeeded: bool = False,
    error: str = "",
    output: str = "",
    tool: str = "",
    effect: str = "",
) -> Observation:
    evidence = ""
    if error or output:
        evidence = _digest(f"{error[:200]}|{output[:200]}")
    return Observation(
        step=step,
        kind="tool_result",
        tool=tool,
        effect=effect,
        failed=failed,
        succeeded=succeeded and not failed,
        evidence=evidence,
        verified=succeeded and not failed,
    )


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


# --------------------------------------------------------------------------- #
# detection                                                                    #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PatternHit:
    pattern: str
    severity: int
    fingerprint: str
    message: str
    refs: dict = field(default_factory=dict)


@dataclass
class SequenceAnalyzer:
    """Sliding-window sequence patterns over normalised effect signatures.

    ``window`` bounds how far back a pattern looks; ``repeat`` is the number of
    occurrences that makes a repetition a loop (never 1 — one call is never a
    loop).
    """

    window: int = 12
    repeat: int = 3
    churn: int = 3
    no_evidence_after: int = 6
    test_without_change_after: int = 2
    change_without_verification_after: int = 8
    search_without_progress_after: int = 3

    def __post_init__(self) -> None:
        self.history: list[Observation] = []
        self._seen_effects: Counter[str] = Counter()
        self._seen_evidence: set[str] = set()
        self._seen_search_results: set[str] = set()
        self._emitted: set[str] = set()
        self._last_change_step = 0
        self._last_verify_step = 0
        self._last_test_step = 0
        self._last_new_evidence_step = 0
        self._last_failure: tuple[str, str] = ("", "")  # (error fp, effect)
        self._dependency_edits = 0
        self._change_streak = 0
        self._changes_since_last_test = 0
        self._tests_without_change = 0

    # -- ingest -------------------------------------------------------------
    def ingest(self, observation: Observation, *, error_fingerprint: str = "") -> list[PatternHit]:
        hits: list[PatternHit] = []
        self.history.append(observation)
        del self.history[:-200]
        # Evidence novelty is tracked for EVERY step (calls, results, edits):
        # a tool_result carries the error/output that proves progress or its
        # absence. Only tracking calls left every result looking "novel".
        self._note_evidence(observation)
        editing = observation.effect.startswith(("edit·", "manifest·"))
        if observation.kind == "file_edit" or (observation.kind == "tool_call" and editing):
            self._on_change(observation)
        if observation.kind == "tool_result":
            self._on_result(observation, error_fingerprint=error_fingerprint)
        if observation.kind == "tool_call":
            self._on_call(observation)
        hits.extend(self._check_loops(observation))
        hits.extend(self._check_oscillation(observation))
        hits.extend(self._check_dependency_churn(observation))
        hits.extend(self._check_no_new_evidence(observation))
        hits.extend(self._check_retry_without_strategy_change(observation))
        hits.extend(self._check_test_without_change(observation))
        hits.extend(self._check_change_without_verification(observation))
        hits.extend(self._check_search_without_progress(observation))
        return hits

    # -- bookkeeping --------------------------------------------------------
    def _on_change(self, observation: Observation) -> None:
        self._last_change_step = observation.step
        self._change_streak += 1
        self._changes_since_last_test += 1
        if observation.effect.startswith("manifest·") or _DEPENDENCY_FILE.search(
            str(observation.args.get("path") or "")
        ):
            self._dependency_edits += 1

    def _on_result(self, observation: Observation, *, error_fingerprint: str) -> None:
        if observation.succeeded:
            self._change_streak = 0
            if _VERIFY_CMD.search(observation.command or observation.tool):
                self._last_verify_step = observation.step
        if observation.failed and error_fingerprint:
            self._last_failure = (error_fingerprint, observation.effect)

    def _on_call(self, observation: Observation) -> None:
        line = observation.normalized or observation.command
        if line:
            if _TEST_RUNNERS.search(line):
                self._last_test_step = observation.step
                # a test run right after changes consumes them: the NEXT run
                # without an intervening change is the redundant one.
                if self._changes_since_last_test:
                    self._changes_since_last_test = 0
                    self._tests_without_change = 0
                else:
                    self._tests_without_change += 1
            if _VERIFY_CMD.search(line):
                self._last_verify_step = self._last_verify_step or observation.step
        if observation.effect.startswith("search·"):
            self._seen_search_results.add(
                _digest(f"{observation.effect}|{observation.normalized}")
            )

    def _note_evidence(self, observation: Observation) -> None:
        """Track whether this step contributed anything *new*.

        Novelty — not activity — is the measure: a repeated identical error
        carries no new information (the same digest), while a different error
        does. That is what separates a blind loop from healthy debugging.
        """
        if not observation.evidence:
            return
        if observation.evidence in self._seen_evidence:
            return
        self._seen_evidence.add(observation.evidence)
        self._last_new_evidence_step = observation.step

    # -- patterns -----------------------------------------------------------
    def _window(self, step: int) -> list[Observation]:
        return [
            item
            for item in self.history
            if item.kind in ("tool_call", "tool_result", "file_edit")
            and step - item.step <= self.window
        ]

    def _emit_once(self, key: str) -> bool:
        if key in self._emitted:
            return False
        self._emitted.add(key)
        return True

    def _check_loops(self, observation: Observation) -> list[PatternHit]:
        hits: list[PatternHit] = []
        if observation.kind != "tool_call":
            return hits
        window = self._window(observation.step)
        effect = observation.effect
        # tool_loop: the same tool+effect class repeatedly, string-differences ignored
        same_effect = [
            item for item in window if item.kind == "tool_call" and item.effect == effect
        ]
        loop_key = f"tool_loop:{effect}:{observation.step // 4}"
        if len(same_effect) >= self.repeat and self._emit_once(loop_key):
            hits.append(
                PatternHit(
                    pattern=PATTERN_TOOL_LOOP,
                    severity=SEV_WARN,
                    fingerprint=_digest(f"tool_loop:{effect}"),
                    message=(
                        f"Action class '{effect}' repeated ({len(same_effect)}× in "
                        f"{self.window} steps) — same effect, not merely the same string."
                    ),
                    refs={"effect": effect, "count": len(same_effect)},
                )
            )
        # command_loop: the same *normalised* command (cosmetics collapsed)
        line = observation.normalized
        if line:
            same_command = [
                item
                for item in window
                if item.kind == "tool_call" and item.normalized == line and line
            ]
            if len(same_command) >= self.repeat and self._emit_once(
                f"command_loop:{line}:{observation.step // 4}"
            ):
                hits.append(
                    PatternHit(
                        pattern=PATTERN_COMMAND_LOOP,
                        severity=SEV_WARN,
                        fingerprint=_digest(f"command_loop:{line}"),
                        message=(
                            f"same command (normalized) {len(same_command)}× — "
                            f"'{line[:80]}'."
                        ),
                        refs={"command": line[:120], "count": len(same_command)},
                    )
                )
        return hits

    def _check_oscillation(self, observation: Observation) -> list[PatternHit]:
        """A → B → A flapping: change followed by revert followed by re-change."""
        if observation.kind != "tool_call":
            return []
        line = observation.normalized
        if not line:
            return []
        recent = [item for item in self._window(observation.step) if item.kind == "tool_call"]
        if len(recent) < 3:
            return []
        revert_index = None
        for index in range(len(recent) - 2, -1, -1):
            if _REVERT_LIKE.search(recent[index].normalized):
                revert_index = index
                break
        if revert_index is None:
            return []
        before = [item for item in recent[:revert_index] if item.effect.startswith("edit·")]
        after = [item for item in recent[revert_index:] if item.effect.startswith("edit·")]
        if before and after and self._emit_once(f"oscillation:{line}:{observation.step // 4}"):
            return [
                PatternHit(
                    pattern=PATTERN_OSCILLATION,
                    severity=SEV_WARN,
                    fingerprint=_digest(f"oscillation:{line}"),
                    message=(
                        "Change → revert → change again: the trajectory oscillates "
                        "between two states instead of establishing either one."
                    ),
                    refs={
                        "command": line[:120],
                        "edits_before": len(before),
                        "edits_after": len(after),
                    },
                )
            ]
        return []

    def _check_dependency_churn(self, observation: Observation) -> list[PatternHit]:
        if self._dependency_edits < self.churn:
            return []
        if not self._emit_once(f"dependency_churn:{observation.step // 3}"):
            return []
        return [
            PatternHit(
                pattern=PATTERN_DEPENDENCY_CHURN,
                severity=SEV_WARN,
                fingerprint=_digest("dependency_churn"),
                message=(
                    f"Dependency/build files changed {self._dependency_edits}× without "
                    "evidence justifying it — establish the need first, then the dependency."
                ),
                refs={"edits": self._dependency_edits},
            )
        ]

    def _check_no_new_evidence(self, observation: Observation) -> list[PatternHit]:
        window = self._window(observation.step)
        activity = [item for item in window if item.is_activity]
        if len(activity) < self.no_evidence_after:
            return []
        # Novelty, not activity: does ANY step in the window carry information
        # that was not already seen? A repeated identical error does not — that
        # is precisely the blind-loop case.
        novel = [
            item
            for item in activity
            if item.evidence and item.evidence not in self._seen_evidence
        ]
        if novel:
            return []
        if self._last_new_evidence_step and (
            observation.step - self._last_new_evidence_step < self.no_evidence_after
        ):
            return []
        if not self._emit_once(f"no_new_evidence:{observation.step // 6}"):
            return []
        return [
            PatternHit(
                pattern=PATTERN_NO_NEW_EVIDENCE,
                severity=SEV_HIGH,
                fingerprint=_digest("no_new_evidence"),
                message=(
                    f"{len(activity)} activities without new evidence — activity is not "
                    "progress; pick a next step you can evidence."
                ),
                refs={"activities": len(activity)},
            )
        ]

    def _check_retry_without_strategy_change(self, observation: Observation) -> list[PatternHit]:
        """Same failure, same strategy — the strategy never moved.

        Identity here is the *error's own digest* (the novel-or-known evidence
        of the failure), never an exit code and never an externally supplied
        fingerprint: the plugin path may hand us either, and a policy that only
        works when the caller cooperates is not a detector.
        """
        if observation.kind != "tool_result" or not observation.failed:
            return []
        if not observation.evidence:
            return []
        window = self._window(observation.step)
        same_failure = [
            item
            for item in window
            if item.kind == "tool_result"
            and item.failed
            and item.evidence == observation.evidence
        ]
        if len(same_failure) < 2:
            return []
        strategies = {item.effect for item in window if item.kind == "tool_call"}
        if len(strategies) > 1:
            return []  # the strategy DID change — healthy debugging
        error_fp = self._last_failure[0] or observation.evidence
        if not self._emit_once(f"retry_no_change:{error_fp}:{observation.step // 4}"):
            return []
        return [
            PatternHit(
                pattern=PATTERN_RETRY_WITHOUT_STRATEGY_CHANGE,
                severity=SEV_HIGH,
                fingerprint=_digest(f"retry_no_change:{error_fp}"),
                message=(
                    "The same error is repeated with an unchanged strategy — change the "
                    "strategy first (new approach/hypothesis), then try again."
                ),
                refs={"error_fingerprint": error_fp, "repeats": len(same_failure)},
            )
        ]

    def _check_test_without_change(self, observation: Observation) -> list[PatternHit]:
        if observation.kind != "tool_call" or not observation.effect.startswith("test·"):
            return []
        if not self._last_change_step:
            return []
        since = observation.step - self._last_change_step
        if since < self.test_without_change_after:
            return []
        if not self._emit_once(f"test_without_change:{observation.step // 3}"):
            return []
        return [
            PatternHit(
                pattern=PATTERN_TEST_WITHOUT_CHANGE,
                severity=SEV_WARN,
                fingerprint=_digest("test_without_change"),
                message=(
                    f"Test run {since} steps after the last source change — the same state "
                    "is tested again; change first, then verify (or stop)."
                ),
                refs={"steps_since_change": since},
            )
        ]

    def _check_change_without_verification(self, observation: Observation) -> list[PatternHit]:
        if self._change_streak < self.change_without_verification_after:
            return []
        if self._last_verify_step > self._last_change_step - self._change_streak:
            return []
        if not self._emit_once(f"change_no_verify:{observation.step // 6}"):
            return []
        return [
            PatternHit(
                pattern=PATTERN_CHANGE_WITHOUT_VERIFICATION,
                severity=SEV_WARN,
                fingerprint=_digest("change_without_verification"),
                message=(
                    f"{self._change_streak} changes without verification — changes without "
                    "a check are not evidenced work."
                ),
                refs={"changes": self._change_streak},
            )
        ]

    def _check_search_without_progress(self, observation: Observation) -> list[PatternHit]:
        window = self._window(observation.step)
        searches = [item for item in window if item.effect.startswith("search·")]
        if len(searches) < self.search_without_progress_after:
            return []
        results = {
            _digest(f"{item.effect}|{item.normalized}") for item in searches
        }
        if len(results) > 1:
            return []  # the search space IS moving
        if not self._emit_once(f"search_no_progress:{observation.step // 4}"):
            return []
        return [
            PatternHit(
                pattern=PATTERN_SEARCH_WITHOUT_PROGRESS,
                severity=SEV_WARN,
                fingerprint=_digest("search_without_progress"),
                message=(
                    f"{len(searches)} searches without a new result space — rephrase the "
                    "same search or take a different route."
                ),
                refs={"searches": len(searches)},
            )
        ]


def analyze_sequence(observations: list[Observation], **kwargs) -> list[PatternHit]:
    """Convenience: run one analyzer over a finished sequence."""
    analyzer = SequenceAnalyzer(**kwargs)
    hits: list[PatternHit] = []
    for observation in observations:
        hits.extend(analyzer.ingest(observation))
    return hits


__all__ = [
    "PATTERNS",
    "PATTERN_CHANGE_WITHOUT_VERIFICATION",
    "PATTERN_COMMAND_LOOP",
    "PATTERN_DEPENDENCY_CHURN",
    "PATTERN_NO_NEW_EVIDENCE",
    "PATTERN_OSCILLATION",
    "PATTERN_RETRY_WITHOUT_STRATEGY_CHANGE",
    "PATTERN_SEARCH_WITHOUT_PROGRESS",
    "PATTERN_TEST_WITHOUT_CHANGE",
    "PATTERN_TOOL_LOOP",
    "Observation",
    "PatternHit",
    "SequenceAnalyzer",
    "analyze_sequence",
    "effect_fingerprint",
    "effect_signature",
    "normalize_command",
    "observe_call",
    "observe_edit",
    "observe_result",
]
