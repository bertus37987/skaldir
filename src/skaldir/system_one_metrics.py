"""System-One observability — the §26 fields, measured or absent.

The rule that shapes this module (ABNAHME §5.1/§117.1, goal §26): **a metric
with no measurement is ``not_run``, never ``0``**. A fabricated zero reads like
a measurement — "no false accepts happened" is a completely different statement
from "nobody looked for false accepts". :meth:`SystemOneMetrics.report` keeps
those apart: measured names appear in ``values`` (where a real ``0`` is fine),
everything else lands in ``not_run``.

Usage is only ever recorded from a mapping a provider *reported*
(``record_usage``); a provider that reports nothing leaves ``input_tokens``,
``output_tokens_if_reported`` and ``provider_cost_if_known`` unmeasured.

This is a sink, not a second runtime: it stores numbers the existing paths
hand it and never computes an evaluation, a decision or a completion.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

# --------------------------------------------------------------------------- #
# §26 — the field names                                                        #
# --------------------------------------------------------------------------- #

STATE_BUILD_MS = "state_build_ms"
SANITIZATION_MS = "sanitization_ms"
QUEUE_MS = "queue_ms"
PROVIDER_LATENCY_MS = "provider_latency_ms"
DECISION_POLICY_MS = "decision_policy_ms"
TOTAL_SYSTEM_ONE_MS = "total_system_one_ms"
INPUT_TOKENS = "input_tokens"
OUTPUT_TOKENS_IF_REPORTED = "output_tokens_if_reported"
PROVIDER_COST_IF_KNOWN = "provider_cost_if_known"
SYSTEM2_CALLS_SAVED = "system2_calls_saved"

# Goal-completion metrics (§26/§20).
COMPLETION_CRITERIA_CHECKED = "completion_criteria_checked"
COMPLETION_CRITERIA_FLAGGED = "completion_criteria_flagged"
COMPLETION_CRITERIA_ESCALATED = "completion_criteria_escalated"
COMPLETION_FALSE_ACCEPTS = "completion_false_accepts"
COMPLETION_FALSE_REJECTS = "completion_false_rejects"
COMPLETION_TOTAL_LATENCY_MS = "completion_total_latency_ms"

# Computer-use metrics (§26) are named here so the CU worker does not invent a
# second vocabulary; they are recorded by the CU paths, not by this module.
CU_PERCEPTION_MS = "perception_ms"
CU_GUI_STATE_BUILD_MS = "gui_state_build_ms"
CU_SYSTEM_ONE_MS = "system_one_ms"
CU_ACTION_MS = "action_ms"
CU_REOBSERVE_MS = "reobserve_ms"
CU_TOTAL_STEP_MS = "total_step_ms"

#: Every metric name this sink accepts, with its unit semantics.
TIMING_METRICS: tuple[str, ...] = (
    STATE_BUILD_MS,
    SANITIZATION_MS,
    QUEUE_MS,
    PROVIDER_LATENCY_MS,
    DECISION_POLICY_MS,
    TOTAL_SYSTEM_ONE_MS,
    COMPLETION_TOTAL_LATENCY_MS,
    CU_PERCEPTION_MS,
    CU_GUI_STATE_BUILD_MS,
    CU_SYSTEM_ONE_MS,
    CU_ACTION_MS,
    CU_REOBSERVE_MS,
    CU_TOTAL_STEP_MS,
)

COUNTER_METRICS: tuple[str, ...] = (
    INPUT_TOKENS,
    OUTPUT_TOKENS_IF_REPORTED,
    PROVIDER_COST_IF_KNOWN,
    SYSTEM2_CALLS_SAVED,
    COMPLETION_CRITERIA_CHECKED,
    COMPLETION_CRITERIA_FLAGGED,
    COMPLETION_CRITERIA_ESCALATED,
    COMPLETION_FALSE_ACCEPTS,
    COMPLETION_FALSE_REJECTS,
)

ALL_METRICS: tuple[str, ...] = TIMING_METRICS + COUNTER_METRICS

#: Which metric names a provider usage mapping may fill.
USAGE_METRIC_KEYS: dict[str, str] = {
    "input_tokens": INPUT_TOKENS,
    "prompt_tokens": INPUT_TOKENS,
    "output_tokens": OUTPUT_TOKENS_IF_REPORTED,
    "completion_tokens": OUTPUT_TOKENS_IF_REPORTED,
    "cost_usd": PROVIDER_COST_IF_KNOWN,
}


@dataclass
class MetricSample:
    """One measured number, with the moment it was measured."""

    name: str
    value: float
    measured_at: float = field(default_factory=time.time)
    source: str = ""

    def as_dict(self) -> dict:
        return {
            "value": self.value,
            "measured_at": self.measured_at,
            "source": self.source,
        }


class SystemOneMetrics:
    """The §26 sink. Every ``record_*`` requires a real number; nothing defaults."""

    def __init__(self) -> None:
        self.samples: dict[str, MetricSample] = {}
        self.not_measured: set[str] = set()

    # -- recording ---------------------------------------------------------- #

    def _store(self, name: str, value: float, *, source: str = "") -> MetricSample:
        if name not in ALL_METRICS:
            raise ValueError(f"unknown metric: {name!r} (known: {len(ALL_METRICS)} names)")
        sample = MetricSample(name=name, value=float(value), source=source)
        self.samples[name] = sample
        self.not_measured.discard(name)
        return sample

    def record_timing(self, name: str, milliseconds: float, *, source: str = "") -> MetricSample:
        """Record a duration in milliseconds — the caller measured it."""
        if name not in TIMING_METRICS:
            raise ValueError(f"{name!r} is not a §26 timing metric")
        return self._store(name, milliseconds, source=source)

    def record_counter(self, name: str, value: float, *, source: str = "") -> MetricSample:
        """Record a count — a *measured* zero is a real measurement."""
        if name not in COUNTER_METRICS:
            raise ValueError(f"{name!r} is not a §26 counter metric")
        return self._store(name, value, source=source)

    def record_usage(self, usage: dict | None, *, source: str = "") -> dict:
        """Record tokens/cost *only* from what a provider reported.

        Returns the names that were recorded. A missing key stays unmeasured —
        the honest alternative to inventing a token count.
        """
        recorded: dict[str, float] = {}
        for key, metric in USAGE_METRIC_KEYS.items():
            if not isinstance(usage, dict) or key not in usage:
                continue
            value = usage.get(key)
            if value is None:
                continue
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            self._store(metric, number, source=source)
            recorded[metric] = number
        return recorded

    # -- completion --------------------------------------------------------- #

    def record_completion_run(
        self,
        *,
        criteria_checked: int,
        criteria_flagged: int,
        criteria_escalated: int,
        total_latency_ms: float,
        source: str = "",
    ) -> None:
        """Record one goal-completion preflight pass (§26)."""
        self.record_counter(COMPLETION_CRITERIA_CHECKED, criteria_checked, source=source)
        self.record_counter(COMPLETION_CRITERIA_FLAGGED, criteria_flagged, source=source)
        self.record_counter(COMPLETION_CRITERIA_ESCALATED, criteria_escalated, source=source)
        self.record_timing(COMPLETION_TOTAL_LATENCY_MS, total_latency_ms, source=source)

    def note_false_accept(self, *, source: str = "") -> MetricSample:
        """Count a false completion acceptance — only when it was really seen.

        The counter is never recorded on its own: it needs a caller that knows
        the completion was wrong (a later contradiction, a reproduced defect).
        """
        current = self.samples.get(COMPLETION_FALSE_ACCEPTS)
        value = 0.0 if current is None else current.value
        return self.record_counter(COMPLETION_FALSE_ACCEPTS, value + 1, source=source)

    def note_system2_call_saved(self, *, count: int = 1, source: str = "") -> MetricSample:
        """A System-2 call that the System-One reading made unnecessary (§20)."""
        current = self.samples.get(SYSTEM2_CALLS_SAVED)
        value = 0.0 if current is None else current.value
        return self.record_counter(SYSTEM2_CALLS_SAVED, value + int(count), source=source)

    # -- reporting ---------------------------------------------------------- #

    def report(self) -> dict:
        """Measured values and — separately — everything nobody measured."""
        values = {name: sample.value for name, sample in self.samples.items()}
        not_run = [name for name in ALL_METRICS if name not in values]
        return {
            "values": values,
            "not_run": not_run,
            "samples": {name: sample.as_dict() for name, sample in self.samples.items()},
            "measured_count": len(values),
            "not_run_count": len(not_run),
        }

    def measured(self, name: str) -> bool:
        return name in self.samples

    def as_dict(self) -> dict:
        return self.report()


__all__ = [
    "ALL_METRICS",
    "COMPLETION_CRITERIA_CHECKED",
    "COMPLETION_CRITERIA_ESCALATED",
    "COMPLETION_CRITERIA_FLAGGED",
    "COMPLETION_FALSE_ACCEPTS",
    "COMPLETION_FALSE_REJECTS",
    "COMPLETION_TOTAL_LATENCY_MS",
    "COUNTER_METRICS",
    "CU_ACTION_MS",
    "CU_GUI_STATE_BUILD_MS",
    "CU_PERCEPTION_MS",
    "CU_REOBSERVE_MS",
    "CU_SYSTEM_ONE_MS",
    "CU_TOTAL_STEP_MS",
    "DECISION_POLICY_MS",
    "INPUT_TOKENS",
    "MetricSample",
    "OUTPUT_TOKENS_IF_REPORTED",
    "PROVIDER_COST_IF_KNOWN",
    "PROVIDER_LATENCY_MS",
    "QUEUE_MS",
    "SANITIZATION_MS",
    "STATE_BUILD_MS",
    "SYSTEM2_CALLS_SAVED",
    "SystemOneMetrics",
    "TIMING_METRICS",
    "TOTAL_SYSTEM_ONE_MS",
]
