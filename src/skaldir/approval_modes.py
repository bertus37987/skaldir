"""The three named approval modes and their Tab-driven cycling.

``docs/masterplan/01-MASTERPLAN.md`` APR-01..APR-05 and ``03-TUI-ENTSCHEIDUNGEN.md`` §4.

The product already has the hard part: :mod:`skaldir.approval` is a real policy engine
(``allow | prompt | forbid`` rules, grants, a banned-prefix ladder, an evidence ledger). What
it does not have is the **user-facing mode vocabulary** the user confirmed:

======== ==================== ==========================================================
Mode     Label                Behaviour (APR-01)
======== ==================== ==========================================================
ask      Ask Everything       ask before executable tool actions; a fully listed batch or a
                              bounded sub-tool assignment may be released together
auto     Approve for me       known harmless actions automatically; unclear or
                              consequential ones are put in front of the user
always   Approve Everything   actions inside the user's assignment automatically; the
                              fixed exceptions below stay
======== ==================== ==========================================================

"Approve nichts" is an **alias** for Ask Everything, not a fourth mode (APR-01).

APR-02's exceptions are not a mode and cannot be turned off by one: new spending and
irreversible or permanently harmful effects need specific approval in **every** mode. That
rule lives in :func:`policy_for` and is asserted in ``tests/test_tui_layout.py`` (the APR
block).

APR-05: the mode is per goal/run with a configurable default for new runs. Nothing here
silently switches a global to Approve Everything.
"""

from __future__ import annotations

import enum
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "ALWAYS_NEEDS_APPROVAL",
    "ApprovalMode",
    "ApprovalModeStore",
    "DEFAULT_MODE",
    "MODE_LABELS",
    "policy_for",
    "resolve_mode",
]


class ApprovalMode(str, enum.Enum):
    """One of exactly three modes (APR-01). Order here is the Tab order."""

    ASK_EVERYTHING = "ask"
    APPROVE_FOR_ME = "auto"
    APPROVE_EVERYTHING = "always"

    @property
    def label(self) -> str:
        return MODE_LABELS[self]

    def next(self) -> ApprovalMode:
        """What Tab does at an empty prompt: the next mode, wrapping around."""
        order = list(ApprovalMode)
        return order[(order.index(self) + 1) % len(order)]

    def describe(self) -> str:
        return _DESCRIPTIONS[self]


MODE_LABELS: dict[ApprovalMode, str] = {
    ApprovalMode.ASK_EVERYTHING: "Ask Everything",
    ApprovalMode.APPROVE_FOR_ME: "Approve for me",
    ApprovalMode.APPROVE_EVERYTHING: "Approve Everything",
}

#: "Approve nichts" is an alias for Ask Everything — APR-01 says so explicitly, and
#: :func:`resolve_mode` accepts it so a user who types the alias gets the real mode.
ALIASES: dict[str, ApprovalMode] = {
    "ask": ApprovalMode.ASK_EVERYTHING,
    "ask everything": ApprovalMode.ASK_EVERYTHING,
    "ask_everything": ApprovalMode.ASK_EVERYTHING,
    "approve nichts": ApprovalMode.ASK_EVERYTHING,
    "approve-nothing": ApprovalMode.ASK_EVERYTHING,
    "auto": ApprovalMode.APPROVE_FOR_ME,
    "approve for me": ApprovalMode.APPROVE_FOR_ME,
    "approve_for_me": ApprovalMode.APPROVE_FOR_ME,
    "always": ApprovalMode.APPROVE_EVERYTHING,
    "approve everything": ApprovalMode.APPROVE_EVERYTHING,
    "approve_everything": ApprovalMode.APPROVE_EVERYTHING,
}

DEFAULT_MODE = ApprovalMode.APPROVE_FOR_ME

_DESCRIPTIONS: dict[ApprovalMode, str] = {
    ApprovalMode.ASK_EVERYTHING:
        "ask before every executable tool action; a listed batch can be released together",
    ApprovalMode.APPROVE_FOR_ME:
        "known harmless actions run automatically; unclear or consequential ones are asked",
    ApprovalMode.APPROVE_EVERYTHING:
        "actions inside the assignment run automatically; spending and irreversible effects "
        "are still asked",
}

#: APR-02: not a mode, not disableable. The words a review must see before it can approve.
ALWAYS_NEEDS_APPROVAL: tuple[str, ...] = (
    "new spending / money",
    "irreversible or permanently harmful effect",
)


def resolve_mode(value: Any, *, default: ApprovalMode = DEFAULT_MODE) -> ApprovalMode:
    """Accept a mode from a user, a config file or a CLI flag without ever inventing one."""
    if isinstance(value, ApprovalMode):
        return value
    text = str(value or "").strip().lower()
    if not text:
        return default
    if text in ALIASES:
        return ALIASES[text]
    for mode in ApprovalMode:
        if mode.value == text:
            return mode
    raise ValueError(
        f"unknown approval mode {value!r}; known: "
        + ", ".join(f"{m.value} ({m.label})" for m in ApprovalMode)
    )


def policy_for(mode: ApprovalMode) -> dict[str, Any]:
    """The mode as the policy document :mod:`skaldir.approval` consumes.

    The mapping is deliberately *conservative in every mode*: ``always`` still leaves the
    spending/irreversibility rules as ``prompt``, because APR-02 is an invariant, not a
    preference. A mode can widen what runs without asking; it cannot widen past that line.
    """
    base: dict[str, Any] = {
        "schema": "skaldir.approval-policy/1",
        "mode": mode.value,
        "mode_label": mode.label,
        # APR-02: identical in all three modes
        "requires_explicit_approval": list(ALWAYS_NEEDS_APPROVAL),
        "note": "spending and irreversible effects are `prompt` in every mode (APR-02)",
    }
    if mode is ApprovalMode.ASK_EVERYTHING:
        base.update({
            "default_decision": "prompt",
            "auto_allow_readonly": False,
            "batch_release": "allowed",      # a fully listed batch may be released at once
            "subtool_autoplay": False,
        })
    elif mode is ApprovalMode.APPROVE_FOR_ME:
        base.update({
            "default_decision": "prompt",
            "auto_allow_readonly": True,     # known harmless: reads, status, search
            "batch_release": "allowed",
            "subtool_autoplay": "validated_only",  # APR-01: a pre-approved deterministic sub-tool
        })
    else:
        base.update({
            "default_decision": "allow",
            "auto_allow_readonly": True,
            "batch_release": "allowed",
            "subtool_autoplay": "validated_only",
        })
    return base


@dataclass
class ApprovalModeStore:
    """APR-05: the mode per goal/run, plus a configurable default for new runs.

    Nothing here is global. The default lives in this store's own file (``default_mode``)
    and is applied to *new* runs only; an existing run keeps the mode it was given until the
    user changes it. Changing the mode does not retroactively release an already-finished
    action (APR-04).

    Deliberately **not** read from a global runtime config: APR-05 names that as an
    implementation decision, and a default that a config file could silently raise to
    ``Approve Everything`` is exactly the "stiller globaler Wechsel" APR-05 forbids. The
    only ways in are :meth:`set_default` (an explicit action) and the constructor.
    """

    path: str | Path
    default_mode: ApprovalMode = DEFAULT_MODE

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    # -- storage ------------------------------------------------------------

    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, data: dict[str, Any]) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)

    # -- the run's mode -----------------------------------------------------

    def mode_for(self, run_id: str) -> ApprovalMode:
        """The run's mode, falling back to the configured default for a new run."""
        stored = self._read().get("runs", {}).get(str(run_id), {})
        raw = stored.get("mode") if isinstance(stored, dict) else None
        try:
            return resolve_mode(raw, default=self.default_mode)
        except ValueError:
            return self.default_mode

    def set_mode(self, run_id: str, mode: ApprovalMode | str) -> ApprovalMode:
        resolved = resolve_mode(mode)
        data = self._read()
        runs = data.setdefault("runs", {})
        entry = runs.setdefault(str(run_id), {})
        entry["mode"] = resolved.value
        entry["label"] = resolved.label
        entry["changed_at"] = time.time()
        self._write(data)
        return resolved

    def cycle(self, run_id: str) -> ApprovalMode:
        """Tab at an empty prompt: advance this run's mode one step and persist it."""
        return self.set_mode(run_id, self.mode_for(run_id).next())

    def set_default(self, mode: ApprovalMode | str) -> ApprovalMode:
        """Configurable default for NEW runs (APR-05) — never a silent global switch."""
        resolved = resolve_mode(mode)
        data = self._read()
        data["default_mode"] = resolved.value
        self._write(data)
        self.default_mode = resolved
        return resolved

    def default(self) -> ApprovalMode:
        raw = self._read().get("default_mode")
        try:
            return resolve_mode(raw, default=self.default_mode)
        except ValueError:
            return self.default_mode
