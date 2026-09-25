"""Real user flows: declared, then driven against a real browser or a real desktop.

The VERIFY layer already answers *"did the declared check pass?"*
(:mod:`skaldir.verify_service`). It could not answer the owner's question —
*"can a user actually walk through this and end up where the product says they end up?"*
A command that exits 0 is not a user flow, and a passing unit test is not a screen a person
can use. This module is the missing half: a **user flow** is a first-class declaration

    start state (UI anchors)
        → actions (goto / click / fill / press / select / wait_for)
        → end state (UI anchors)

and it is executed against a **real** target — a real browser through Playwright, or a real
isolated desktop through :mod:`skaldir.computer_use` (the container runner with its own Xvfb:
the project's own rule is that computer use never touches the user's live display).

Verdicts, and the one rule that matters
---------------------------------------

``PASS`` / ``FAIL`` / ``UNKNOWN``, computed in exactly one place (:func:`verdict_for`):

* every declared flow really ran **and** its end-state anchors really held → ``PASS``;
* a flow ran and an anchor did not hold, or a declared interaction could not happen →
  ``FAIL`` (a real defect, with the observed value next to the expected one);
* a flow could not be executed at all — no browser, no isolated desktop runner, the target
  did not answer, a declared action this verifier does not implement → ``UNKNOWN``.

``UNKNOWN`` is **never** promoted to ``PASS``. A start state that could not be read is
``UNKNOWN`` too: "I could not look" is not "it looked right".

Honesty boundaries, stated rather than implied
---------------------------------------------

* The runner never invents a page, never retries an assertion into passing and never writes
  a screenshot it did not take. A screenshot is hashed from the bytes on disk; if the
  capture fails the step is ``unknown``/``failed`` with the exact reason.
* The declared vocabulary is closed: a flow that names an action or an anchor check this
  module does not implement is reported ``UNKNOWN`` with that name in the reason — the flow
  is never silently reduced to the part that happens to be implemented.
* A browser flow against a URL that does not answer is ``UNKNOWN``, not ``FAIL``: the
  verifier could not observe the target, and an unobserved target is not a defect.

The declaration file is ``<workspace>/.godsv/flows.json`` (or any path passed with
``--flows``)::

    {
      "flows": [
        {
          "id": "search-and-open",
          "title": "search a term and open the matching result",
          "target": {"kind": "browser", "url": "http://127.0.0.1:8000/"},
          "start_state": [{"check": "visible", "selector": "#q"},
                          {"check": "text_contains", "selector": "h1", "text": "Demo"}],
          "steps": [
            {"action": "fill", "selector": "#q", "value": "alpha"},
            {"action": "click", "selector": "#go"},
            {"action": "assert", "check": "text_contains", "selector": "#out",
             "text": "alpha"},
            {"action": "screenshot", "name": "result"}
          ],
          "end_state": [{"check": "text_contains", "selector": "#title",
                         "text": "Alpha"},
                        {"check": "url_contains", "value": "#result"}]
        }
      ]
    }

A desktop flow declares ``{"kind": "desktop"}`` and uses the actions the isolated runner can
really perform (``cli``, ``click`` with ``x``/``y``, ``fill``, ``press``, ``assert`` over the
runner's own observation, ``screenshot`` from the isolated display).
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess  # nosec B404 — fixed argv lists, no shell on the host
import tempfile
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from .verify_service import (
    STATUS_FAILED,
    STATUS_PASSED,
    STATUS_UNKNOWN,
    VERDICT_FAIL,
    VERDICT_PASS,
    VERDICT_UNKNOWN,
    canonical_json,
    sha256_file,
    sha256_text,
)

FLOWS_FILENAME = Path(".godsv") / "flows.json"
DEFAULT_SCREENSHOT_DIR = "flows"
DEFAULT_FLOW_TIMEOUT_MS = 20_000
DEFAULT_DESKTOP_TIMEOUT_S = 120.0

#: The target kinds this verifier can really drive.
TARGET_BROWSER = "browser"
TARGET_DESKTOP = "desktop"
TARGET_KINDS = (TARGET_BROWSER, TARGET_DESKTOP)

#: The anchor checks a declaration may use. Anything else is refused by name.
ANCHOR_PRESENT = "present"
ANCHOR_VISIBLE = "visible"
ANCHOR_TEXT_CONTAINS = "text_contains"
ANCHOR_TEXT_EQUALS = "text_equals"
ANCHOR_VALUE_EQUALS = "value_equals"
ANCHOR_URL_CONTAINS = "url_contains"
ANCHOR_URL_EQUALS = "url_equals"
ANCHOR_COUNT = "count"
ANCHOR_WINDOW_PRESENT = "window_present"
ANCHOR_WINDOW_ABSENT = "window_absent"
ANCHOR_CHECKS: tuple[str, ...] = (
    ANCHOR_PRESENT,
    ANCHOR_VISIBLE,
    ANCHOR_TEXT_CONTAINS,
    ANCHOR_TEXT_EQUALS,
    ANCHOR_VALUE_EQUALS,
    ANCHOR_URL_CONTAINS,
    ANCHOR_URL_EQUALS,
    ANCHOR_COUNT,
    ANCHOR_WINDOW_PRESENT,
    ANCHOR_WINDOW_ABSENT,
)

#: The actions a declaration may drive. Anything else is refused by name.
STEP_GOTO = "goto"
STEP_CLICK = "click"
STEP_FILL = "fill"
STEP_PRESS = "press"
STEP_SELECT = "select"
STEP_WAIT_FOR = "wait_for"
STEP_ASSERT = "assert"
STEP_SCREENSHOT = "screenshot"
STEP_SLEEP = "sleep"
STEP_CLI = "cli"
STEP_BACK = "back"
STEP_FORWARD = "forward"
STEP_ACTIONS: tuple[str, ...] = (
    STEP_GOTO,
    STEP_CLICK,
    STEP_FILL,
    STEP_PRESS,
    STEP_SELECT,
    STEP_WAIT_FOR,
    STEP_ASSERT,
    STEP_SCREENSHOT,
    STEP_SLEEP,
    STEP_CLI,
    STEP_BACK,
    STEP_FORWARD,
)

#: Which actions each target kind really implements. A mismatch is UNKNOWN, never a skip.
STEP_ACTIONS_BY_TARGET: dict[str, tuple[str, ...]] = {
    TARGET_BROWSER: (
        STEP_GOTO, STEP_CLICK, STEP_FILL, STEP_PRESS, STEP_SELECT, STEP_WAIT_FOR,
        STEP_ASSERT, STEP_SCREENSHOT, STEP_SLEEP, STEP_BACK, STEP_FORWARD,
    ),
    TARGET_DESKTOP: (
        STEP_CLI, STEP_CLICK, STEP_FILL, STEP_PRESS, STEP_ASSERT, STEP_SCREENSHOT,
        STEP_SLEEP,
    ),
}

#: Which anchor checks each target kind can really judge. A check outside its kind's column is
#: ``UNKNOWN`` for that flow — the verifier says what it cannot see instead of guessing.
ANCHOR_CHECKS_BY_TARGET: dict[str, tuple[str, ...]] = {
    TARGET_BROWSER: (
        ANCHOR_PRESENT, ANCHOR_VISIBLE, ANCHOR_TEXT_CONTAINS, ANCHOR_TEXT_EQUALS,
        ANCHOR_VALUE_EQUALS, ANCHOR_URL_CONTAINS, ANCHOR_URL_EQUALS, ANCHOR_COUNT,
    ),
    TARGET_DESKTOP: (
        ANCHOR_TEXT_CONTAINS, ANCHOR_TEXT_EQUALS, ANCHOR_URL_CONTAINS, ANCHOR_URL_EQUALS,
        ANCHOR_PRESENT, ANCHOR_VISIBLE, ANCHOR_WINDOW_PRESENT, ANCHOR_WINDOW_ABSENT,
    ),
}

PASS_REQUIREMENT = (
    "PASS requires every declared flow to have really run and every end-state anchor to "
    "have held against a real browser or a real desktop; a flow that could not be executed "
    "is UNKNOWN and never a PASS"
)

#: The evidence kind for a flow record, in the product contract's own vocabulary.
EVIDENCE_KIND = "verification"


def now_iso() -> str:
    """Local ISO-8601 with milliseconds (the same format the rest of VERIFY uses)."""
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


# --------------------------------------------------------------------------- #
# declaration                                                                   #
# --------------------------------------------------------------------------- #


class DeclarationError(ValueError):
    """A flow declaration the verifier refuses to guess about."""


@dataclass(frozen=True)
class Anchor:
    """One declared expectation about the UI ("the end state I say the user reaches")."""

    check: str
    selector: str = ""
    text: str = ""
    value: str = ""
    count: int = 0

    def describe(self) -> str:
        """The assertion in one line — what the evidence calls this check."""
        if self.check in (ANCHOR_URL_CONTAINS, ANCHOR_URL_EQUALS):
            return f"the page URL {self.check.replace('_', ' ')} {self.value!r}"
        if self.check == ANCHOR_COUNT:
            return f"{self.selector} occurs exactly {self.count} time(s)"
        if self.check == ANCHOR_WINDOW_PRESENT:
            return f"the desktop has a window matching {self.value!r}" if self.value else \
                "the desktop has at least one window"
        if self.check == ANCHOR_WINDOW_ABSENT:
            return f"the desktop has no window matching {self.value!r}" if self.value else \
                "the desktop has no window"
        if self.check == ANCHOR_PRESENT:
            return f"{self.selector} is present"
        if self.check == ANCHOR_VISIBLE:
            return f"{self.selector} is visible"
        if self.check == ANCHOR_VALUE_EQUALS:
            return f"{self.selector} has the value {self.value!r}"
        if self.check == ANCHOR_TEXT_CONTAINS:
            return f"{self.selector} contains the text {self.text!r}"
        if self.check == ANCHOR_TEXT_EQUALS:
            return f"{self.selector} reads exactly {self.text!r}"
        return f"{self.check} {self.selector}"

    def as_dict(self) -> dict:
        return {
            "check": self.check,
            "selector": self.selector,
            "text": self.text,
            "value": self.value,
            "count": self.count,
            "describe": self.describe(),
        }


@dataclass(frozen=True)
class Step:
    """One declared action, with the anchors an inline ``assert`` carries."""

    index: int
    action: str
    selector: str = ""
    value: str = ""
    url: str = ""
    key: str = ""
    name: str = ""
    ms: int = 0
    argv: tuple[str, ...] = ()
    anchors: tuple[Anchor, ...] = ()
    x: int = 0
    y: int = 0
    timeout_ms: int = 0
    note: str = ""

    def describe(self) -> str:
        if self.action == STEP_ASSERT:
            return "; ".join(anchor.describe() for anchor in self.anchors) or "assert"
        if self.action in (STEP_FILL,):
            return f"{self.action} {self.value!r} into {self.selector}"
        if self.action in (STEP_GOTO,):
            return f"go to {self.url}"
        if self.action == STEP_SCREENSHOT:
            return f"capture the screen as {self.name or f'step-{self.index}'}"
        if self.action == STEP_SLEEP:
            return f"wait {self.ms} ms"
        if self.action == STEP_CLI:
            return f"run {' '.join(self.argv)} in the isolated desktop"
        if self.action == STEP_CLICK and (self.x or self.y):
            return f"click at ({self.x}, {self.y})"
        if self.action == STEP_PRESS:
            return f"press {self.key} on {self.selector or 'the page'}"
        if self.action == STEP_WAIT_FOR:
            return f"wait for {self.selector} to be {self.value or 'visible'}"
        if self.action == STEP_BACK:
            return "the browser's own back navigation"
        if self.action == STEP_FORWARD:
            return "the browser's own forward navigation"
        return f"{self.action} {self.selector}".strip()

    def as_dict(self) -> dict:
        return {
            "index": self.index,
            "action": self.action,
            "selector": self.selector,
            "value": self.value,
            "url": self.url,
            "key": self.key,
            "name": self.name,
            "ms": self.ms,
            "argv": list(self.argv),
            "x": self.x,
            "y": self.y,
            "timeout_ms": self.timeout_ms,
            "note": self.note,
            "describe": self.describe(),
            "anchors": [anchor.as_dict() for anchor in self.anchors],
        }


@dataclass(frozen=True)
class Flow:
    """One declared user flow: a start state, the actions, and the end state."""

    flow_id: str
    title: str
    target_kind: str
    target_url: str
    start_state: tuple[Anchor, ...] = ()
    steps: tuple[Step, ...] = ()
    end_state: tuple[Anchor, ...] = ()
    viewport: tuple[int, int] = (1280, 800)
    timeout_ms: int = DEFAULT_FLOW_TIMEOUT_MS
    screenshot: bool = True
    container_image: str = ""
    desktop_display: str = ":90"
    note: str = ""

    def declared_actions_ok(self) -> tuple[bool, str]:
        """Are all of this flow's actions implemented for its target kind?"""
        allowed = STEP_ACTIONS_BY_TARGET.get(self.target_kind, ())
        unsupported = sorted({step.action for step in self.steps if step.action not in allowed})
        if unsupported:
            return False, (
                f"the declaration uses action(s) this verifier does not implement for a "
                f"{self.target_kind} target: {', '.join(unsupported)} "
                f"(implemented: {', '.join(allowed)})"
            )
        return True, ""

    def all_anchors(self) -> tuple[Anchor, ...]:
        """Every anchor this flow declares, wherever it sits."""
        inline = tuple(
            anchor for step in self.steps for anchor in step.anchors
        )
        return tuple(self.start_state) + inline + tuple(self.end_state)

    def declared_anchors_ok(self) -> tuple[bool, str]:
        """Are all of this flow's anchor checks judgeable for its target kind?"""
        allowed = ANCHOR_CHECKS_BY_TARGET.get(self.target_kind, ())
        unsupported = sorted({a.check for a in self.all_anchors() if a.check not in allowed})
        if unsupported:
            return False, (
                f"the declaration uses anchor check(s) this verifier cannot judge for a "
                f"{self.target_kind} target: {', '.join(unsupported)} "
                f"(judgeable: {', '.join(allowed)})"
            )
        return True, ""

    def declaration_problem(self) -> tuple[bool, str]:
        """``(ok, reason)`` — every reason the declaration cannot be run as written."""
        for check in (self.declared_actions_ok, self.declared_anchors_ok):
            ok, why = check()
            if not ok:
                return False, why
        return True, ""

    def as_dict(self) -> dict:
        return {
            "id": self.flow_id,
            "title": self.title,
            "target_kind": self.target_kind,
            "target_url": self.target_url,
            "viewport": {"width": self.viewport[0], "height": self.viewport[1]},
            "timeout_ms": self.timeout_ms,
            "screenshot": self.screenshot,
            "container_image": self.container_image,
            "desktop_display": self.desktop_display,
            "note": self.note,
            "start_state": [anchor.as_dict() for anchor in self.start_state],
            "steps": [step.as_dict() for step in self.steps],
            "end_state": [anchor.as_dict() for anchor in self.end_state],
        }


@dataclass
class FlowDeclaration:
    """What was declared, where it came from, and what the loader could not accept.

    ``rejected`` is not a footnote: a flow the loader refused becomes an ``UNKNOWN`` result
    in the run, so a declaration error can never make the verdict look stronger than it is.
    """

    flows: tuple[Flow, ...]
    path: str
    source: str
    warnings: tuple[str, ...] = ()
    rejected: tuple[dict, ...] = ()

    def as_dict(self) -> dict:
        return {
            "path": self.path,
            "source": self.source,
            "count": len(self.flows),
            "flows": [flow.as_dict() for flow in self.flows],
            "rejected": [dict(item) for item in self.rejected],
            "warnings": list(self.warnings),
        }


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


#: checks that need a CSS selector (a browser concept)
SELECTOR_CHECKS: tuple[str, ...] = (
    ANCHOR_PRESENT, ANCHOR_VISIBLE, ANCHOR_TEXT_CONTAINS, ANCHOR_TEXT_EQUALS,
    ANCHOR_VALUE_EQUALS, ANCHOR_COUNT,
)
#: checks that read something other than a selector (a URL, or the desktop's window list)
NON_SELECTOR_CHECKS: tuple[str, ...] = (
    ANCHOR_URL_CONTAINS, ANCHOR_URL_EQUALS, ANCHOR_WINDOW_PRESENT, ANCHOR_WINDOW_ABSENT,
)


def parse_anchor(raw: Mapping[str, Any]) -> Anchor:
    """One declared anchor. An unknown check is refused by name."""
    if not isinstance(raw, Mapping):
        raise DeclarationError(f"an anchor must be an object, got {type(raw).__name__}")
    check = str(raw.get("check") or "").strip()
    if not check:
        raise DeclarationError("an anchor needs a 'check'")
    if check not in ANCHOR_CHECKS:
        raise DeclarationError(
            f"unknown anchor check {check!r} (declared checks: {', '.join(ANCHOR_CHECKS)})"
        )
    selector = str(raw.get("selector") or "")
    if check in SELECTOR_CHECKS and not selector:
        raise DeclarationError(f"anchor check {check!r} needs a 'selector'")
    if check in (ANCHOR_URL_CONTAINS, ANCHOR_URL_EQUALS) and not str(raw.get("value") or ""):
        raise DeclarationError(f"anchor check {check!r} needs a 'value'")
    if check in (ANCHOR_TEXT_CONTAINS, ANCHOR_TEXT_EQUALS) and not str(raw.get("text") or ""):
        raise DeclarationError(f"anchor check {check!r} needs a 'text'")
    if check == ANCHOR_COUNT and _as_int(raw.get("count"), -1) < 0:
        raise DeclarationError("anchor check 'count' needs a non-negative 'count'")
    return Anchor(
        check=check,
        selector=selector,
        text=str(raw.get("text") or ""),
        value=str(raw.get("value") or ""),
        count=_as_int(raw.get("count"), 0),
    )


def parse_step(raw: Mapping[str, Any], index: int) -> Step:
    """One declared step. An unknown action is refused by name."""
    if not isinstance(raw, Mapping):
        raise DeclarationError(f"step {index} must be an object, got {type(raw).__name__}")
    action = str(raw.get("action") or "").strip()
    if not action:
        raise DeclarationError(f"step {index} needs an 'action'")
    if action not in STEP_ACTIONS:
        raise DeclarationError(
            f"step {index}: unknown action {action!r} "
            f"(declared actions: {', '.join(STEP_ACTIONS)})"
        )
    anchors: tuple[Anchor, ...] = ()
    if action == STEP_ASSERT:
        raw_anchors = raw.get("anchors")
        if raw_anchors is None:
            # an inline assert may be written flat, as one anchor
            raw_anchors = [raw]
        if not isinstance(raw_anchors, Sequence) or isinstance(raw_anchors, (str, bytes)):
            raise DeclarationError(f"step {index}: 'anchors' must be a list")
        anchors = tuple(parse_anchor(item) for item in raw_anchors)
        if not anchors:
            raise DeclarationError(f"step {index}: an assert step needs at least one anchor")
    argv = raw.get("argv") or []
    if action == STEP_CLI and not argv:
        raise DeclarationError(f"step {index}: a cli step needs an 'argv'")
    if action == STEP_CLI and not isinstance(argv, Sequence):
        raise DeclarationError(f"step {index}: 'argv' must be a list")
    return Step(
        index=int(index),
        action=action,
        selector=str(raw.get("selector") or ""),
        value=str(raw.get("value") or ""),
        url=str(raw.get("url") or ""),
        key=str(raw.get("key") or ""),
        name=str(raw.get("name") or ""),
        ms=_as_int(raw.get("ms"), 0),
        argv=tuple(str(part) for part in argv),
        anchors=anchors,
        x=_as_int(raw.get("x"), 0),
        y=_as_int(raw.get("y"), 0),
        timeout_ms=_as_int(raw.get("timeout_ms"), 0),
        note=str(raw.get("note") or ""),
    )


def parse_flow(raw: Mapping[str, Any]) -> Flow:
    """One declared flow, validated far enough that a typo cannot become a silent skip."""
    if not isinstance(raw, Mapping):
        raise DeclarationError(f"a flow must be an object, got {type(raw).__name__}")
    flow_id = str(raw.get("id") or raw.get("flow") or "").strip()
    if not flow_id:
        raise DeclarationError("a flow needs an 'id'")
    target = raw.get("target")
    if not isinstance(target, Mapping):
        raise DeclarationError(f"flow {flow_id!r}: a 'target' object is required")
    kind = str(target.get("kind") or "").strip()
    if kind not in TARGET_KINDS:
        raise DeclarationError(
            f"flow {flow_id!r}: unknown target kind {kind!r} (declared: {', '.join(TARGET_KINDS)})"
        )
    url = str(target.get("url") or "").strip()
    if kind == TARGET_BROWSER and not url:
        raise DeclarationError(f"flow {flow_id!r}: a browser target needs a 'url'")
    viewport = target.get("viewport") or {}
    width = _as_int((viewport or {}).get("width"), 1280)
    height = _as_int((viewport or {}).get("height"), 800)
    steps_raw = raw.get("steps") or []
    if not isinstance(steps_raw, Sequence) or isinstance(steps_raw, (str, bytes)):
        raise DeclarationError(f"flow {flow_id!r}: 'steps' must be a list")
    steps = tuple(parse_step(item, index) for index, item in enumerate(steps_raw))
    if not steps:
        raise DeclarationError(f"flow {flow_id!r}: a flow with no step proves nothing")
    start = tuple(parse_anchor(item) for item in (raw.get("start_state") or []))
    end = tuple(parse_anchor(item) for item in (raw.get("end_state") or []))
    if not end:
        raise DeclarationError(
            f"flow {flow_id!r}: an end state is required — a flow without one only says "
            "'something ran'"
        )
    return Flow(
        flow_id=flow_id,
        title=str(raw.get("title") or flow_id),
        target_kind=kind,
        target_url=url,
        start_state=start,
        steps=steps,
        end_state=end,
        viewport=(max(200, width), max(200, height)),
        timeout_ms=(
            _as_int(raw.get("timeout_ms"), DEFAULT_FLOW_TIMEOUT_MS)
            or DEFAULT_FLOW_TIMEOUT_MS
        ),
        screenshot=bool(raw.get("screenshot", True)),
        container_image=str(target.get("container_image") or raw.get("container_image") or ""),
        desktop_display=str(target.get("display") or raw.get("display") or ":90"),
        note=str(raw.get("note") or ""),
    )


def load_flows(workspace: str | Path, path: str | Path = "") -> FlowDeclaration:
    """Read and validate the declaration. A malformed flow is refused, never guessed."""
    workspace = Path(workspace).expanduser()
    target = Path(path).expanduser() if str(path) else workspace / FLOWS_FILENAME
    if not target.is_file():
        return FlowDeclaration(
            flows=(),
            path=str(target),
            source="none",
            warnings=(
                f"no flow declaration at {target} — a flow must be declared to be verified",
            ),
        )
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return FlowDeclaration(
            flows=(),
            path=str(target),
            source="file",
            warnings=(f"{target} could not be read as JSON: {type(exc).__name__}: {exc}",),
        )
    raw_flows = data.get("flows") if isinstance(data, Mapping) else None
    if raw_flows is None and isinstance(data, Sequence) and not isinstance(data, (str, bytes)):
        raw_flows = data
    if not isinstance(raw_flows, Sequence) or isinstance(raw_flows, (str, bytes)):
        return FlowDeclaration(
            flows=(),
            path=str(target),
            source="file",
            warnings=(f"{target} carries no 'flows' list",),
        )
    flows: list[Flow] = []
    warnings: list[str] = []
    rejected: list[dict] = []
    for position, item in enumerate(raw_flows):
        try:
            flows.append(parse_flow(item))
        except DeclarationError as exc:
            raw_id = ""
            if isinstance(item, Mapping):
                raw_id = str(item.get("id") or item.get("flow") or "")
            rejected.append({
                "flow_id": raw_id or f"<unparsed flow #{position + 1}>",
                "position": position,
                "reason": str(exc),
            })
            warnings.append(f"refused flow {raw_id or f'#{position + 1}'}: {exc}")
    return FlowDeclaration(
        flows=tuple(flows),
        path=str(target),
        source="file",
        warnings=tuple(warnings),
        rejected=tuple(rejected),
    )


# --------------------------------------------------------------------------- #
# results                                                                       #
# --------------------------------------------------------------------------- #


@dataclass
class CheckResult:
    """One anchor, judged — with the value that was really observed next to the expected one."""

    where: str
    anchor: Anchor
    status: str
    observed: Any = None
    reason: str = ""
    duration_ms: int = 0

    def as_dict(self) -> dict:
        return {
            "where": self.where,
            "anchor": self.anchor.as_dict(),
            "assertion": self.anchor.describe(),
            "status": self.status,
            "observed": _bounded(self.observed),
            "reason": self.reason,
            "duration_ms": self.duration_ms,
        }


@dataclass
class StepResult:
    """One declared action, executed (or not) — and why."""

    step: Step
    status: str
    observed: Any = None
    reason: str = ""
    duration_ms: int = 0
    checks: tuple[CheckResult, ...] = ()

    def as_dict(self) -> dict:
        return {
            "index": self.step.index,
            "action": self.step.action,
            "description": self.step.describe(),
            "status": self.status,
            "observed": _bounded(self.observed),
            "reason": self.reason,
            "duration_ms": self.duration_ms,
            "checks": [check.as_dict() for check in self.checks],
        }


@dataclass
class Screenshot:
    """A real capture on disk, with the hash of the bytes that are actually there."""

    name: str
    path: str
    step: int
    sha256: str
    size_bytes: int
    scope: str = ""

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "path": self.path,
            "step": self.step,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "scope": self.scope,
        }


@dataclass
class FlowResult:
    """One flow, run and judged."""

    flow_id: str
    title: str
    target_kind: str
    target: str
    status: str
    reason: str
    runner: str = ""
    runner_detail: str = ""
    start_state: tuple[CheckResult, ...] = ()
    steps: tuple[StepResult, ...] = ()
    end_state: tuple[CheckResult, ...] = ()
    screenshots: tuple[Screenshot, ...] = ()
    observed: dict = field(default_factory=dict)
    started_at: str = ""
    finished_at: str = ""
    duration_ms: int = 0

    @property
    def passed(self) -> bool:
        return self.status == STATUS_PASSED

    def as_dict(self) -> dict:
        payload = {
            "flow_id": self.flow_id,
            "title": self.title,
            "target_kind": self.target_kind,
            "target": self.target,
            "runner": self.runner,
            "runner_detail": self.runner_detail,
            "status": self.status,
            "verdict": {"passed": VERDICT_PASS, "failed": VERDICT_FAIL}.get(
                self.status, VERDICT_UNKNOWN
            ),
            "reason": self.reason,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": self.duration_ms,
            "start_state": [check.as_dict() for check in self.start_state],
            "steps": [step.as_dict() for step in self.steps],
            "end_state": [check.as_dict() for check in self.end_state],
            "screenshots": [shot.as_dict() for shot in self.screenshots],
            "observed": _bounded(self.observed),
        }
        payload["evidence_sha256"] = sha256_text(canonical_json(payload))
        return payload


@dataclass
class FlowReport:
    """Every flow's result, and the one verdict that follows from them."""

    run_id: str
    verdict: str
    verdict_reason: str
    results: tuple[FlowResult, ...]
    declaration: dict
    started_at: str
    finished_at: str
    duration_ms: int
    screenshot_dir: str
    state_dir: str = ""
    source: str = ""
    warnings: tuple[str, ...] = ()
    pass_requirement: str = PASS_REQUIREMENT

    @property
    def counts(self) -> dict:
        return {
            "flows": len(self.results),
            "passed": sum(1 for item in self.results if item.status == STATUS_PASSED),
            "failed": sum(1 for item in self.results if item.status == STATUS_FAILED),
            "unknown": sum(1 for item in self.results if item.status == STATUS_UNKNOWN),
        }

    def as_dict(self) -> dict:
        payload = {
            "run_id": self.run_id,
            "verdict": self.verdict,
            "verdict_reason": self.verdict_reason,
            "counts": self.counts,
            "results": [result.as_dict() for result in self.results],
            "declaration": self.declaration,
            "source": self.source,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": self.duration_ms,
            "screenshot_dir": self.screenshot_dir,
            "state_dir": self.state_dir,
            "warnings": list(self.warnings),
            "pass_requirement": self.pass_requirement,
        }
        payload["evidence_sha256"] = sha256_text(canonical_json(payload))
        return payload


#: an observation may carry a whole page's text; the report keeps a bounded but still
#: diagnostic view, and hashes exactly the view a reader can recompute.
OBSERVATION_CHAR_LIMIT = 400


def _one_line(text: str, limit: int = 300) -> str:
    """Collapse a multi-line tool message into one readable line (the log stays in data)."""
    collapsed = " ".join(str(text or "").split())
    return collapsed if len(collapsed) <= limit else collapsed[:limit] + "…"


def _bounded(value: Any, *, limit: int = OBSERVATION_CHAR_LIMIT) -> Any:
    if isinstance(value, str):
        return value if len(value) <= limit else f"{value[:limit]}…[+{len(value) - limit} chars]"
    if isinstance(value, Mapping):
        return {str(key): _bounded(item, limit=limit) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_bounded(item, limit=limit) for item in value]
    return value


def verdict_for(results: Sequence[FlowResult]) -> tuple[str, str]:
    """The verdict — the only place one is computed. ``UNKNOWN`` is never a PASS.

    * any flow ``failed`` → ``FAIL`` (a real defect, with the anchor that did not hold)
    * else any flow ``unknown`` → ``UNKNOWN`` (the verifier could not look)
    * else every flow ``passed`` → ``PASS``
    * no flow at all → ``UNKNOWN`` (nothing was verified)
    """
    if not results:
        return VERDICT_UNKNOWN, "no flow was run — nothing to certify"
    failed = [item for item in results if item.status == STATUS_FAILED]
    if failed:
        first = failed[0]
        return VERDICT_FAIL, (
            f"{len(failed)} of {len(results)} flow(s) failed: "
            f"{first.flow_id} — {first.reason}"
        )
    unknown = [item for item in results if item.status == STATUS_UNKNOWN]
    if unknown:
        first = unknown[0]
        return VERDICT_UNKNOWN, (
            f"{len(unknown)} of {len(results)} flow(s) could not be verified: "
            f"{first.flow_id} — {first.reason}"
        )
    return VERDICT_PASS, f"all {len(results)} flow(s) ran and every end-state anchor held"


# --------------------------------------------------------------------------- #
# the browser runner — a real browser, really driven                            #
# --------------------------------------------------------------------------- #


def browser_availability() -> tuple[bool, str]:
    """Whether a real browser can be driven here, and with what."""
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except Exception as exc:  # noqa: BLE001 — a missing browser is a reason, not a crash
        return False, f"playwright is not importable: {type(exc).__name__}: {exc}"
    return True, "playwright chromium (headless)"


class _BrowserSession:
    """One real browser for one flow, closed no matter how the flow ends."""

    def __init__(self, *, headless: bool = True, timeout_ms: int = DEFAULT_FLOW_TIMEOUT_MS) -> None:
        self.headless = headless
        self.timeout_ms = timeout_ms
        self._pw = None
        self._browser = None
        self._context = None
        self.page = None
        self.detail = ""
        self.opened = False

    def open(self, *, viewport: tuple[int, int]) -> tuple[bool, str]:
        from playwright.sync_api import sync_playwright

        try:
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(headless=self.headless)
            self._context = self._browser.new_context(
                viewport={"width": viewport[0], "height": viewport[1]},
                ignore_https_errors=True,
            )
            self._context.set_default_timeout(self.timeout_ms)
            self.page = self._context.new_page()
        except Exception as exc:  # noqa: BLE001
            self.close()
            return False, _one_line(
                f"the browser could not be started: {type(exc).__name__}: {exc}"
            )
        self.opened = True
        version = ""
        try:
            version = str(self._browser.version)
        except Exception:  # noqa: BLE001
            version = ""
        self.detail = f"playwright chromium {version}".strip()
        return True, self.detail

    def close(self) -> None:
        for closer in (self._context, self._browser):
            try:
                if closer is not None:
                    closer.close()
            except Exception:  # noqa: BLE001 — a closed browser is not a result
                pass
        try:
            if self._pw is not None:
                self._pw.stop()
        except Exception:  # noqa: BLE001
            pass
        self._pw = self._browser = self._context = self.page = None


def _page_observation(page: Any) -> dict:
    """What the page really is right now (URL, title) — never invented."""
    observed: dict = {"url": "", "title": ""}
    try:
        observed["url"] = str(page.url or "")
    except Exception:  # noqa: BLE001
        pass
    try:
        observed["title"] = str(page.title() or "")
    except Exception:  # noqa: BLE001
        pass
    return observed


def _judge_anchor(page: Any, anchor: Anchor, *, where: str) -> CheckResult:
    """Evaluate one anchor against the live page. A broken selector is a FAILED check."""
    started = time.time()
    try:
        if anchor.check in (ANCHOR_URL_CONTAINS, ANCHOR_URL_EQUALS):
            url = str(page.url or "")
            ok = anchor.value in url if anchor.check == ANCHOR_URL_CONTAINS else url == anchor.value
            reason = "" if ok else f"the URL is {url!r}, not {anchor.value!r}"
            return CheckResult(where=where, anchor=anchor,
                               status=STATUS_PASSED if ok else STATUS_FAILED, observed=url,
                               reason=reason,
                               duration_ms=int((time.time() - started) * 1000))
        if anchor.check not in ANCHOR_CHECKS_BY_TARGET[TARGET_BROWSER]:
            return CheckResult(where=where, anchor=anchor, status=STATUS_UNKNOWN,
                               reason=f"the anchor check {anchor.check!r} cannot be judged "
                                      "from a browser page",
                               duration_ms=int((time.time() - started) * 1000))
        locator = page.locator(anchor.selector)
        if anchor.check == ANCHOR_COUNT:
            seen = int(locator.count())
            ok = seen == int(anchor.count)
            return CheckResult(where=where, anchor=anchor,
                               status=STATUS_PASSED if ok else STATUS_FAILED, observed=seen,
                               reason="" if ok else
                               f"{anchor.selector} occurs {seen} time(s), declared {anchor.count}",
                               duration_ms=int((time.time() - started) * 1000))
        if anchor.check == ANCHOR_PRESENT:
            seen = int(locator.count())
            ok = seen > 0
            return CheckResult(where=where, anchor=anchor,
                               status=STATUS_PASSED if ok else STATUS_FAILED, observed=seen,
                               reason="" if ok else f"{anchor.selector} is not in the document",
                               duration_ms=int((time.time() - started) * 1000))
        if anchor.check == ANCHOR_VISIBLE:
            try:
                seen = bool(locator.first.is_visible())
            except Exception as exc:  # noqa: BLE001 — a missing element is not visible
                return CheckResult(where=where, anchor=anchor, status=STATUS_FAILED,
                                   observed=False,
                                   reason=f"{anchor.selector} could not be looked at: "
                                          f"{type(exc).__name__}: {exc}",
                                   duration_ms=int((time.time() - started) * 1000))
            return CheckResult(where=where, anchor=anchor,
                               status=STATUS_PASSED if seen else STATUS_FAILED, observed=seen,
                               reason="" if seen else f"{anchor.selector} is not visible",
                               duration_ms=int((time.time() - started) * 1000))
        if anchor.check == ANCHOR_VALUE_EQUALS:
            seen = str(locator.first.input_value())
            ok = seen == anchor.value
            return CheckResult(where=where, anchor=anchor,
                               status=STATUS_PASSED if ok else STATUS_FAILED, observed=seen,
                               reason="" if ok else
                               f"{anchor.selector} holds {seen!r}, declared {anchor.value!r}",
                               duration_ms=int((time.time() - started) * 1000))
        # text checks
        try:
            seen = str(locator.first.text_content() or "")
        except Exception as exc:  # noqa: BLE001
            return CheckResult(where=where, anchor=anchor, status=STATUS_FAILED, observed="",
                               reason=f"{anchor.selector} could not be read: "
                                      f"{type(exc).__name__}: {exc}",
                               duration_ms=int((time.time() - started) * 1000))
        if anchor.check == ANCHOR_TEXT_CONTAINS:
            ok = anchor.text in seen
        else:
            ok = seen.strip() == anchor.text.strip()
        return CheckResult(
            where=where,
            anchor=anchor,
            status=STATUS_PASSED if ok else STATUS_FAILED,
            observed=seen,
            reason="" if ok else f"{anchor.selector} reads {seen!r}, declared {anchor.text!r}",
            duration_ms=int((time.time() - started) * 1000),
        )
    except Exception as exc:  # noqa: BLE001 — an unusable selector is a failed assertion
        return CheckResult(where=where, anchor=anchor, status=STATUS_FAILED,
                           reason=f"the check could not be evaluated: {type(exc).__name__}: {exc}",
                           duration_ms=int((time.time() - started) * 1000))


def _capture_browser(page: Any, name: str, step: int, shot_dir: Path,
                     flow_id: str) -> tuple[Screenshot | None, str]:
    """Take a real screenshot and hash the bytes that landed on disk."""
    folder = shot_dir if shot_dir.name == flow_id else shot_dir / flow_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{step:02d}-{name or 'shot'}.png"
    try:
        page.screenshot(path=str(path), full_page=True)
    except Exception as exc:  # noqa: BLE001
        return None, f"the screenshot could not be taken: {type(exc).__name__}: {exc}"
    digest, scope = sha256_file(path)
    size = int(path.stat().st_size) if path.is_file() else 0
    if not size or not digest:
        return None, f"the screenshot at {path} is empty — not counted as evidence"
    return Screenshot(name=name or f"step-{step}", path=str(path), step=step, sha256=digest,
                      size_bytes=size, scope=scope), ""


def _run_browser_flow(
    flow: Flow,
    *,
    shot_dir: Path,
    headless: bool = True,
) -> FlowResult:
    """Drive a real browser through the declared flow."""
    started = time.time()
    started_at = now_iso()
    available, detail = browser_availability()
    if not available:
        return _unknown_flow(flow, started, started_at, "browser", detail)

    session = _BrowserSession(headless=headless, timeout_ms=flow.timeout_ms)
    steps: list[StepResult] = []
    screenshots: list[Screenshot] = []
    start_checks: list[CheckResult] = []
    end_checks: list[CheckResult] = []
    status = STATUS_PASSED
    reason = ""
    observed = {"url": flow.target_url, "title": ""}
    try:
        ok, opened_detail = session.open(viewport=flow.viewport)
        if not ok:
            return _unknown_flow(flow, started, started_at, "browser", opened_detail)
        page = session.page
        if page is None:  # pragma: no cover — open() guarantees a page or returns False
            return _unknown_flow(flow, started, started_at, "browser",
                                 "the browser opened without a page")
        # reachability first: a target that does not answer is UNKNOWN, not a defect
        try:
            page.goto(flow.target_url, wait_until="load")
        except Exception as exc:  # noqa: BLE001
            return _unknown_flow(
                flow, started, started_at, opened_detail,
                _one_line(f"the target {flow.target_url} did not answer: "
                          f"{type(exc).__name__}: {exc}"),
            )
        observed = _page_observation(page)
        # 1. the declared start state
        for anchor in flow.start_state:
            check = _judge_anchor(page, anchor, where="start_state")
            start_checks.append(check)
            if check.status != STATUS_PASSED:
                status = check.status
                reason = (
                    f"the start state does not hold: {anchor.describe()} — {check.reason}"
                    if check.status == STATUS_FAILED
                    else f"the start state could not be judged: {anchor.describe()} — "
                         f"{check.reason}"
                )
                break
        # 2. the declared actions
        if status == STATUS_PASSED:
            for step in flow.steps:
                result_ = _run_browser_step(page, step, shot_dir=shot_dir, flow_id=flow.flow_id)
                steps.append(result_)
                screenshots.extend(
                    item for item in result_.__dict__.get("_screenshots", ()) or ()
                )
                if result_.status != STATUS_PASSED:
                    status = result_.status
                    reason = f"step {step.index} ({step.action}): {result_.reason}"
                    break
        # 3. the declared end state
        if status == STATUS_PASSED:
            for anchor in flow.end_state:
                check = _judge_anchor(page, anchor, where="end_state")
                end_checks.append(check)
                if check.status != STATUS_PASSED:
                    status = check.status
                    reason = (
                        f"the end state was not reached: {anchor.describe()} — {check.reason}"
                        if check.status == STATUS_FAILED
                        else f"the end state could not be judged: {anchor.describe()} — "
                             f"{check.reason}"
                    )
                    break
        observed = _page_observation(page)
        # a final capture is evidence of the state the verdict was made about. A flow whose
        # evidence cannot be produced is not certified: "I could not show it" is UNKNOWN.
        if flow.screenshot:
            shot, why = _capture_browser(page, "end-state", 99, shot_dir, flow.flow_id)
            if shot is not None:
                screenshots.append(shot)
            else:
                steps.append(StepResult(step=Step(index=99, action=STEP_SCREENSHOT,
                                                  name="end-state"),
                                        status=STATUS_UNKNOWN, reason=why))
                if status == STATUS_PASSED:
                    status = STATUS_UNKNOWN
                    reason = f"the flow ran, but its evidence could not be captured: {why}"
        if status == STATUS_PASSED:
            reason = "every declared step ran and every end-state anchor held"
    except Exception as exc:  # noqa: BLE001 — an infrastructure failure is not a defect
        status = STATUS_UNKNOWN
        reason = f"the browser session failed: {type(exc).__name__}: {exc}"
    finally:
        runner_detail = session.detail or detail
        session.close()

    return FlowResult(
        flow_id=flow.flow_id,
        title=flow.title,
        target_kind=flow.target_kind,
        target=flow.target_url,
        status=status,
        reason=reason,
        runner="browser",
        runner_detail=runner_detail,
        start_state=tuple(start_checks),
        steps=tuple(steps),
        end_state=tuple(end_checks),
        screenshots=tuple(screenshots),
        observed=observed,
        started_at=started_at,
        finished_at=now_iso(),
        duration_ms=int((time.time() - started) * 1000),
    )


def _run_browser_step(page: Any, step: Step, *, shot_dir: Path, flow_id: str) -> StepResult:
    """One declared browser action, really driven."""
    started = time.time()
    checks: list[CheckResult] = []

    def flow_timeout(active: Any) -> int:
        """The page's own default timeout — the flow's declared budget, never a new one."""
        try:
            return max(2_000, int(active.get_default_timeout()))
        except Exception:  # noqa: BLE001
            return DEFAULT_FLOW_TIMEOUT_MS

    try:
        if step.timeout_ms:
            page.set_default_timeout(int(step.timeout_ms))
        if step.action == STEP_GOTO:
            page.goto(step.url, wait_until="load")
            return _ok(step, started, {"url": str(page.url or "")})
        if step.action == STEP_CLICK:
            page.click(step.selector)
            return _ok(step, started, {"clicked": step.selector})
        if step.action == STEP_FILL:
            page.fill(step.selector, step.value)
            return _ok(step, started, {"selector": step.selector, "value": step.value})
        if step.action == STEP_PRESS:
            if step.selector:
                page.press(step.selector, step.key)
            else:
                page.keyboard.press(step.key)
            return _ok(step, started, {"key": step.key})
        if step.action == STEP_SELECT:
            selected = page.select_option(step.selector, step.value)
            return _ok(step, started, {"selected": selected})
        if step.action == STEP_WAIT_FOR:
            state = step.value or "visible"
            page.wait_for_selector(step.selector, state=state)
            return _ok(step, started, {"selector": step.selector, "state": state})
        if step.action == STEP_SLEEP:
            page.wait_for_timeout(max(0, step.ms))
            return _ok(step, started, {"waited_ms": step.ms})
        if step.action in (STEP_BACK, STEP_FORWARD):
            if step.action == STEP_BACK:
                response = page.go_back(wait_until="load", timeout=flow_timeout(page))
            else:
                response = page.go_forward(wait_until="load", timeout=flow_timeout(page))
            return _ok(step, started, {"url": str(page.url or ""),
                                       "status": (getattr(response, "status", None)
                                                  if response else None)})
        if step.action == STEP_SCREENSHOT:
            shot, why = _capture_browser(page, step.name, step.index, shot_dir, flow_id)
            if shot is None:
                return StepResult(step=step, status=STATUS_FAILED, reason=why,
                                  duration_ms=int((time.time() - started) * 1000))
            result_ = _ok(step, started, {"path": shot.path, "sha256": shot.sha256,
                                          "bytes": shot.size_bytes})
            result_._screenshots = (shot,)  # type: ignore[attr-defined]
            return result_
        if step.action == STEP_ASSERT:
            for anchor in step.anchors:
                checks.append(_judge_anchor(page, anchor, where=f"step {step.index}"))
            failed = [check for check in checks if check.status == STATUS_FAILED]
            unknown = [check for check in checks if check.status == STATUS_UNKNOWN]
            status = STATUS_FAILED if failed else (STATUS_UNKNOWN if unknown else STATUS_PASSED)
            first = failed[0] if failed else (unknown[0] if unknown else None)
            reason = "" if first is None else first.reason
            return StepResult(step=step, status=status, observed={"url": str(page.url or "")},
                              reason=reason, checks=tuple(checks),
                              duration_ms=int((time.time() - started) * 1000))
    except Exception as exc:  # noqa: BLE001 — the declared action did not happen
        return StepResult(
            step=step,
            status=STATUS_FAILED,
            reason=_one_line(
                f"the declared action did not happen: {type(exc).__name__}: {exc}", 300
            ),
            checks=tuple(checks),
            duration_ms=int((time.time() - started) * 1000),
        )
    return StepResult(step=step, status=STATUS_UNKNOWN,
                      reason=f"the action {step.action!r} is not implemented by the browser runner",
                      duration_ms=int((time.time() - started) * 1000))


def _ok(step: Step, started: float, observed: Any) -> StepResult:
    return StepResult(step=step, status=STATUS_PASSED, observed=observed,
                      duration_ms=int((time.time() - started) * 1000))


def _unknown_flow(flow: Flow, started: float, started_at: str, runner: str,
                  reason: str) -> FlowResult:
    return FlowResult(
        flow_id=flow.flow_id,
        title=flow.title,
        target_kind=flow.target_kind,
        target=flow.target_url,
        status=STATUS_UNKNOWN,
        reason=reason,
        runner=runner,
        runner_detail=reason,
        started_at=started_at,
        finished_at=now_iso(),
        duration_ms=int((time.time() - started) * 1000),
    )


# --------------------------------------------------------------------------- #
# the desktop runner — a real isolated display, really driven                   #
# --------------------------------------------------------------------------- #


def desktop_availability(image: str = "") -> tuple[bool, str]:
    """Whether a real isolated desktop can be driven here, and with what."""
    from .computer_use import ContainerRunner

    runner = ContainerRunner(image=image or ContainerRunner.DEFAULT_IMAGE)
    return runner.available()


def _run_desktop_flow(
    flow: Flow,
    *,
    shot_dir: Path,
    timeout_s: float = DEFAULT_DESKTOP_TIMEOUT_S,
) -> FlowResult:
    """Drive the real isolated desktop through the declared flow.

    Every action goes through :class:`skaldir.computer_use.ContainerRunner` — the project's
    own isolated runner. The live desktop is never touched (``CUPolicy``'s rule), so a host
    without podman or without the display image is ``UNKNOWN`` with that capability named.
    """
    from .computer_use import ContainerRunner

    started = time.time()
    started_at = now_iso()
    image = flow.container_image or ContainerRunner.DEFAULT_IMAGE
    runner = ContainerRunner(image=image, gui=True, display=flow.desktop_display,
                             timeout=max(30.0, float(timeout_s)))
    ok, detail = runner.available()
    if not ok:
        return _unknown_flow(flow, started, started_at, "desktop (isolated container)", detail)
    steps: list[StepResult] = []
    screenshots: list[Screenshot] = []
    start_checks: list[CheckResult] = []
    end_checks: list[CheckResult] = []
    status = STATUS_PASSED
    reason = ""
    observed: dict = {"display": flow.desktop_display, "window_list": ""}
    try:
        ok, started_detail = runner.start()
        if not ok:
            return _unknown_flow(flow, started, started_at, "desktop (isolated container)",
                                 f"the isolated display did not start: {started_detail}")
        # the start state is the runner's own observation of the display
        for anchor in flow.start_state:
            check = _judge_desktop_anchor(runner, anchor, where="start_state")
            start_checks.append(check)
            if check.status != STATUS_PASSED:
                status = check.status
                reason = (
                    f"the start state does not hold: {anchor.describe()} — {check.reason}"
                    if check.status == STATUS_FAILED
                    else f"the start state could not be judged: {anchor.describe()} — "
                         f"{check.reason}"
                )
                break
        if status == STATUS_PASSED:
            for step in flow.steps:
                result_ = _run_desktop_step(runner, step, shot_dir=shot_dir, flow_id=flow.flow_id)
                steps.append(result_)
                screenshots.extend(getattr(result_, "_screenshots", ()) or ())
                if result_.status != STATUS_PASSED:
                    status = result_.status
                    reason = f"step {step.index} ({step.action}): {result_.reason}"
                    break
        if status == STATUS_PASSED:
            for anchor in flow.end_state:
                check = _judge_desktop_anchor(runner, anchor, where="end_state")
                end_checks.append(check)
                if check.status != STATUS_PASSED:
                    status = STATUS_FAILED
                    reason = f"the end state was not reached: {anchor.describe()} — {check.reason}"
                    break
        observation = runner.observe()
        observed = {"display": flow.desktop_display, "window_list": str(observation.text or "")}
        if flow.screenshot:
            shot, why = _capture_desktop(
                runner,
                "end-state",
                99,
                shot_dir,
                flow.flow_id,
                label=_label_from_observation(str(observation.text or ""), "end-state"),
            )
            if shot is not None:
                screenshots.append(shot)
            else:
                steps.append(StepResult(step=Step(index=99, action=STEP_SCREENSHOT,
                                                  name="end-state"),
                                        status=STATUS_UNKNOWN, reason=why))
                if status == STATUS_PASSED:
                    status = STATUS_UNKNOWN
                    reason = f"the flow ran, but its evidence could not be captured: {why}"
        if status == STATUS_PASSED:
            reason = "every declared desktop step ran and every end-state anchor held"
    except Exception as exc:  # noqa: BLE001
        status = STATUS_UNKNOWN
        reason = f"the isolated desktop session failed: {type(exc).__name__}: {exc}"
    finally:
        try:
            runner.close()
        except Exception:  # noqa: BLE001
            pass
        runner_detail = ("computer_use.ContainerRunner (isolated, gui) "
                         f"image={image or ContainerRunner.DEFAULT_IMAGE}")

    return FlowResult(
        flow_id=flow.flow_id,
        title=flow.title,
        target_kind=flow.target_kind,
        target=flow.target_url or f"display {flow.desktop_display}",
        status=status,
        reason=reason,
        runner="desktop (isolated container)",
        runner_detail=runner_detail,
        start_state=tuple(start_checks),
        steps=tuple(steps),
        end_state=tuple(end_checks),
        screenshots=tuple(screenshots),
        observed=observed,
        started_at=started_at,
        finished_at=now_iso(),
        duration_ms=int((time.time() - started) * 1000),
    )


def _judge_desktop_anchor(runner: Any, anchor: Anchor, *, where: str) -> CheckResult:
    """Judge an anchor against the isolated desktop's own observation."""
    started = time.time()
    try:
        observation = runner.observe()
    except Exception as exc:  # noqa: BLE001
        return CheckResult(where=where, anchor=anchor, status=STATUS_UNKNOWN,
                           reason=f"the desktop could not be observed: "
                                  f"{type(exc).__name__}: {exc}",
                           duration_ms=int((time.time() - started) * 1000))
    text = str(getattr(observation, "text", "") or "")
    detail = str(getattr(observation, "detail", "") or "")
    if not text:
        return CheckResult(where=where, anchor=anchor, status=STATUS_UNKNOWN, observed=text,
                           reason=f"the observation is empty ({detail or 'no reason given'}) — "
                                  "an unobserved desktop is not a passing one",
                           duration_ms=int((time.time() - started) * 1000))
    if anchor.check in (ANCHOR_TEXT_CONTAINS, ANCHOR_URL_CONTAINS):
        wanted = anchor.text or anchor.value
        ok = bool(wanted) and wanted in text
        return CheckResult(where=where, anchor=anchor,
                           status=STATUS_PASSED if ok else STATUS_FAILED, observed=text,
                           reason="" if ok else
                           f"the observation does not contain {wanted!r}: {text[:200]!r}",
                           duration_ms=int((time.time() - started) * 1000))
    if anchor.check in (ANCHOR_TEXT_EQUALS, ANCHOR_URL_EQUALS):
        wanted = anchor.text or anchor.value
        ok = bool(wanted) and text.strip() == wanted.strip()
        return CheckResult(where=where, anchor=anchor,
                           status=STATUS_PASSED if ok else STATUS_FAILED, observed=text,
                           reason="" if ok else f"the observation reads {text!r}",
                           duration_ms=int((time.time() - started) * 1000))
    if anchor.check in (ANCHOR_PRESENT, ANCHOR_VISIBLE):
        # "present" on a desktop means the runner produced an observation at all — the
        # observation *is* the UI surface there, and an empty one is UNKNOWN (handled above).
        ok = bool(text.strip())
        return CheckResult(where=where, anchor=anchor,
                           status=STATUS_PASSED if ok else STATUS_FAILED, observed=text,
                           reason="" if ok else "the desktop produced no observation",
                           duration_ms=int((time.time() - started) * 1000))
    if anchor.check in (ANCHOR_WINDOW_PRESENT, ANCHOR_WINDOW_ABSENT):
        windows = [line for line in text.splitlines()
                   if line.strip() and line.strip() != "(no windows)"]
        named = [line for line in windows if anchor.value in line] if anchor.value else windows
        found = bool(named)
        ok = found if anchor.check == ANCHOR_WINDOW_PRESENT else not found
        return CheckResult(
            where=where, anchor=anchor,
            status=STATUS_PASSED if ok else STATUS_FAILED,
            observed={"windows": windows, "matching": named},
            reason="" if ok else (
                f"the desktop has {len(windows)} window(s) and none matches {anchor.value!r}"
                if anchor.check == ANCHOR_WINDOW_PRESENT
                else f"the desktop has a window matching {anchor.value!r}: {named[:2]}"
            ),
            duration_ms=int((time.time() - started) * 1000),
        )
    return CheckResult(where=where, anchor=anchor, status=STATUS_UNKNOWN, observed=text,
                       reason=f"the anchor check {anchor.check!r} cannot be judged from a "
                              "desktop observation",
                       duration_ms=int((time.time() - started) * 1000))


def _run_desktop_step(runner: Any, step: Step, *, shot_dir: Path, flow_id: str) -> StepResult:
    """One declared desktop action, really driven through the isolated runner."""
    from .computer_use import Action

    started = time.time()
    try:
        if step.action == STEP_CLI:
            action = Action(kind="cli", argv=list(step.argv),
                            description=step.note or " ".join(step.argv))
            result_ = runner.act(action)
            if not getattr(result_, "ok", False):
                return StepResult(step=step, status=STATUS_FAILED,
                                  observed=str(getattr(result_, "stdout", "")),
                                  reason=f"the declared command failed: "
                                         f"{getattr(result_, 'detail', '')}"[:300],
                                  duration_ms=int((time.time() - started) * 1000))
            return _ok(step, started, {"stdout": str(getattr(result_, "stdout", ""))})
        if step.action == STEP_CLICK:
            result_ = runner.act(Action(kind="mouse",
                                        payload={"x": step.x, "y": step.y, "click": True,
                                                 "button": 1},
                                        description=f"click at ({step.x}, {step.y})"))
            if not getattr(result_, "ok", False):
                return StepResult(step=step, status=STATUS_FAILED,
                                  reason=f"the click did not happen: "
                                         f"{getattr(result_, 'detail', '')}"[:300],
                                  duration_ms=int((time.time() - started) * 1000))
            return _ok(step, started, {"clicked": [step.x, step.y]})
        if step.action == STEP_FILL:
            result_ = runner.act(Action(kind="type_text", payload={"text": step.value},
                                        description=f"type {step.value!r}"))
            if not getattr(result_, "ok", False):
                return StepResult(step=step, status=STATUS_FAILED,
                                  reason=f"the text was not typed: "
                                         f"{getattr(result_, 'detail', '')}"[:300],
                                  duration_ms=int((time.time() - started) * 1000))
            return _ok(step, started, {"typed": step.value})
        if step.action == STEP_PRESS:
            result_ = runner.act(Action(kind="key", payload={"key": step.key},
                                        description=f"press {step.key}"))
            if not getattr(result_, "ok", False):
                return StepResult(step=step, status=STATUS_FAILED,
                                  reason=f"the key was not pressed: "
                                         f"{getattr(result_, 'detail', '')}"[:300],
                                  duration_ms=int((time.time() - started) * 1000))
            return _ok(step, started, {"key": step.key})
        if step.action == STEP_SLEEP:
            time.sleep(max(0, step.ms) / 1000.0)
            return _ok(step, started, {"waited_ms": step.ms})
        if step.action == STEP_ASSERT:
            checks = tuple(
                _judge_desktop_anchor(runner, anchor, where=f"step {step.index}")
                for anchor in step.anchors
            )
            failed = [check for check in checks if check.status == STATUS_FAILED]
            unknown = [check for check in checks if check.status == STATUS_UNKNOWN]
            status = STATUS_FAILED if failed else (STATUS_UNKNOWN if unknown else STATUS_PASSED)
            first = failed[0] if failed else (unknown[0] if unknown else None)
            return StepResult(step=step, status=status, checks=checks,
                              reason="" if first is None else first.reason,
                              duration_ms=int((time.time() - started) * 1000))
        if step.action == STEP_SCREENSHOT:
            shot, why = _capture_desktop(runner, step.name, step.index, shot_dir, flow_id,
                                         label=step.name or f"step-{step.index}")
            if shot is None:
                return StepResult(step=step, status=STATUS_UNKNOWN, reason=why,
                                  duration_ms=int((time.time() - started) * 1000))
            result_ = _ok(step, started, {"path": shot.path, "sha256": shot.sha256,
                                          "bytes": shot.size_bytes})
            result_._screenshots = (shot,)  # type: ignore[attr-defined]
            return result_
    except Exception as exc:  # noqa: BLE001
        return StepResult(step=step, status=STATUS_UNKNOWN,
                          reason=f"the desktop action could not be driven: "
                                 f"{type(exc).__name__}: {exc}"[:300],
                          duration_ms=int((time.time() - started) * 1000))
    return StepResult(step=step, status=STATUS_UNKNOWN,
                      reason=f"the action {step.action!r} is not implemented by the desktop runner",
                      duration_ms=int((time.time() - started) * 1000))


def _label_from_observation(text: str, fallback: str) -> str:
    """The window/process name a screenshot should be filed under.

    Evidence naming matters: a reviewer has to be able to tell what was on screen from the
    file name. The first window the observation read is that name; a flow with no window
    (or no observation) falls back to the step's own name and never to a generic ``shot``.
    """
    first = next(
        (
            line.strip()
            for line in str(text or "").splitlines()
            if line.strip() and line.strip() != "(no windows)"
        ),
        "",
    )
    return first or fallback


def _capture_desktop(runner: Any, name: str, step: int, shot_dir: Path,
                     flow_id: str, *, label: str = "") -> tuple[Screenshot | None, str]:
    """Capture the isolated display for real, through the runner's own capture first.

    The runner path is preferred because it is the product's own capability: the image
    ships ``xwd``, the bytes come back over the same ``podman exec`` the actions use, and
    :func:`skaldir.computer_use.xwd_to_png` decodes them **on the host with the standard
    library** — no ImageMagick, no Pillow, nothing extra to install (GAP-K2). The older
    host-converter route stays as an explicit fallback for a runner that has no capture
    method of its own; if neither path can produce bytes the step is ``unknown`` with the
    exact capability named, never a stand-in image.
    """
    from .computer_use import Action, safe_label

    folder = shot_dir if shot_dir.name == flow_id else shot_dir / flow_id
    folder.mkdir(parents=True, exist_ok=True)
    shot_name = safe_label(label, fallback=name or f"step-{step}")
    path = folder / f"{step:02d}-{shot_name}.png"

    shooter = getattr(runner, "screenshot", None)
    if callable(shooter):
        capture: Any = None
        why = ""
        try:
            capture, why = shooter(path, label=label or name)
        except Exception as exc:  # noqa: BLE001 — a crashing capture is a missing capture
            capture, why = None, f"{type(exc).__name__}: {exc}"
        if capture is not None:
            digest, scope = sha256_file(path)
            return Screenshot(
                name=shot_name,
                path=str(path),
                step=step,
                sha256=digest,
                size_bytes=int(path.stat().st_size),
                scope=scope,
            ), ""
        runner_why = why
    else:
        runner_why = (
            f"the runner '{getattr(runner, 'isolation', '')}' has no capture method"
        )

    result_ = runner.act(
        Action(
            kind="cli",
            # A literal pipeline with the display as a positional argument: the shell
            # re-parses the script text, so the display is passed, never spliced in.
            argv=[
                "sh",
                "-c",
                'xwd -root -display "$1" -silent | base64 -w0',
                "sh",
                str(runner.display),
            ],
            description="capture the isolated display",
        )
    )
    if not getattr(result_, "ok", False):
        return None, (
            f"the display could not be captured: {runner_why}; the xwd fallback also failed "
            f"({getattr(result_, 'detail', '')[:200]})"
        )
    raw = str(getattr(result_, "stdout", "") or "").strip()
    if not raw:
        return None, (
            f"the display could not be captured: {runner_why}; the xwd fallback returned "
            "no bytes"
        )
    try:
        xwd_bytes = base64.b64decode(raw, validate=False)
    except Exception as exc:  # noqa: BLE001
        return None, f"the display capture could not be decoded: {type(exc).__name__}: {exc}"
    converter = shutil.which("magick") or shutil.which("convert")
    if not converter:
        return None, (
            f"the display could not be captured: {runner_why}; the capture is XWD and no "
            "host converter (magick/convert) is installed to make a PNG of it (the standard "
            "library decoder lives in the runner's own screenshot path)"
        )
    with tempfile.NamedTemporaryFile(suffix=".xwd", delete=False) as handle:
        handle.write(xwd_bytes)
        temp = handle.name
    try:
        proc = subprocess.run(  # nosec B603 — fixed argv, no shell
            [converter, "xwd:" + temp, str(path)],
            capture_output=True, text=True, timeout=60,
        )
        if proc.returncode != 0 or not path.is_file() or path.stat().st_size == 0:
            return None, (f"the capture could not be converted: exit {proc.returncode} "
                          f"{(proc.stderr or '')[:200]}")
    except Exception as exc:  # noqa: BLE001
        return None, f"the capture could not be converted: {type(exc).__name__}: {exc}"
    finally:
        try:
            os.unlink(temp)
        except OSError:
            pass
    digest, scope = sha256_file(path)
    size = int(path.stat().st_size)
    return Screenshot(name=shot_name, path=str(path), step=step, sha256=digest,
                      size_bytes=size, scope=scope), ""


# --------------------------------------------------------------------------- #
# the run                                                                       #
# --------------------------------------------------------------------------- #


def run_flow(
    flow: Flow,
    *,
    screenshot_dir: str | Path,
    headless: bool = True,
    desktop_timeout_s: float = DEFAULT_DESKTOP_TIMEOUT_S,
) -> FlowResult:
    """Run one declared flow against its real target."""
    shot_dir = Path(screenshot_dir)
    declared_ok, why = flow.declaration_problem()
    if not declared_ok:
        started = time.time()
        return _unknown_flow(flow, started, now_iso(),
                             "browser" if flow.target_kind == TARGET_BROWSER else "desktop", why)
    if flow.target_kind == TARGET_BROWSER:
        return _run_browser_flow(flow, shot_dir=shot_dir, headless=headless)
    return _run_desktop_flow(flow, shot_dir=shot_dir, timeout_s=desktop_timeout_s)


def run_flows(
    flows: Sequence[Flow],
    *,
    screenshot_dir: str | Path,
    state_dir: str | Path | None = None,
    declaration: FlowDeclaration | None = None,
    headless: bool = True,
    desktop_timeout_s: float = DEFAULT_DESKTOP_TIMEOUT_S,
    on_result: Any = None,
) -> FlowReport:
    """Run every declared flow, in order, and derive the verdict from the results.

    A flow the loader refused becomes an ``UNKNOWN`` result of its own: a declaration error
    must show up in the verdict, not only in a warning nobody reads.
    """
    started = time.time()
    started_at = now_iso()
    run_id = f"flow-{uuid.uuid4().hex[:12]}"
    shot_dir = Path(screenshot_dir).expanduser()
    shot_dir.mkdir(parents=True, exist_ok=True)
    results: list[FlowResult] = []
    for rejected in (declaration.rejected if declaration else ()):
        results.append(FlowResult(
            flow_id=str(rejected.get("flow_id") or "rejected-flow"),
            title="a flow the declaration file could not be parsed into",
            target_kind="",
            target=str(declaration.path if declaration else ""),
            status=STATUS_UNKNOWN,
            reason=(f"the declaration was refused: {rejected.get('reason')} — a flow that "
                    "cannot be parsed is not a flow that passed"),
            runner="(not run)",
            runner_detail="the declaration never became a flow",
            started_at=started_at,
            finished_at=now_iso(),
        ))
    for flow in flows:
        result_ = run_flow(flow, screenshot_dir=shot_dir, headless=headless,
                           desktop_timeout_s=desktop_timeout_s)
        results.append(result_)
        if on_result is not None:
            try:
                on_result(result_)
            except Exception:  # noqa: BLE001 — a progress hook never changes a verdict
                pass
    verdict, verdict_reason = verdict_for(results)
    report = FlowReport(
        run_id=run_id,
        verdict=verdict,
        verdict_reason=verdict_reason,
        results=tuple(results),
        declaration=(declaration or FlowDeclaration((), "", "none")).as_dict(),
        started_at=started_at,
        finished_at=now_iso(),
        duration_ms=int((time.time() - started) * 1000),
        screenshot_dir=str(shot_dir),
        state_dir=str(state_dir) if state_dir else "",
        source=(declaration.source if declaration else "none"),
        warnings=tuple(declaration.warnings) if declaration else (),
    )
    if state_dir:
        FlowStore(state_dir).record(report)
    return report


class FlowStore:
    """Where flow runs are recorded, so a later reader can re-hash what was claimed."""

    def __init__(self, state_dir: str | Path) -> None:
        self.dir = Path(state_dir).expanduser() / "flows"
        self.runs_dir = self.dir / "runs"
        self.evidence_path = self.dir / "evidence.jsonl"

    def record(self, report: FlowReport) -> str:
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        path = self.runs_dir / f"{report.run_id}.json"
        path.write_text(json.dumps(report.as_dict(), ensure_ascii=False, indent=2, default=str),
                        encoding="utf-8")
        with self.evidence_path.open("a", encoding="utf-8") as handle:
            for result in report.results:
                handle.write(json.dumps({
                    "run_id": report.run_id,
                    "recorded_at": report.finished_at,
                    "flow_id": result.flow_id,
                    "status": result.status,
                    "reason": result.reason,
                    "target": result.target,
                    "target_kind": result.target_kind,
                    "duration_ms": result.duration_ms,
                    "screenshots": [shot.as_dict() for shot in result.screenshots],
                    "evidence_sha256": result.as_dict()["evidence_sha256"],
                }, ensure_ascii=False, sort_keys=True, default=str) + "\n")
        return str(path)

    def get(self, run_id: str) -> dict | None:
        path = self.runs_dir / f"{run_id}.json"
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def latest(self) -> dict | None:
        if not self.runs_dir.is_dir():
            return None
        files = sorted(self.runs_dir.glob("*.json"), key=lambda item: item.stat().st_mtime)
        return self.get(files[-1].stem) if files else None

    def runs(self, *, limit: int = 20) -> list[dict]:
        if not self.runs_dir.is_dir():
            return []
        files = sorted(self.runs_dir.glob("*.json"), key=lambda item: item.stat().st_mtime,
                       reverse=True)
        rows: list[dict] = []
        for path in files[: max(1, int(limit))]:
            record = self.get(path.stem) or {}
            counts = record.get("counts") or {}
            rows.append({
                "run_id": record.get("run_id", path.stem),
                "verdict": record.get("verdict", ""),
                "counts": counts,
                "started_at": record.get("started_at", ""),
                "duration_ms": record.get("duration_ms", 0),
                "screenshot_dir": record.get("screenshot_dir", ""),
            })
        return rows

    def evidence(self, *, run_id: str = "", flow_id: str = "", limit: int = 50) -> list[dict]:
        if not self.evidence_path.is_file():
            return []
        rows: list[dict] = []
        try:
            with self.evidence_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if run_id and str(row.get("run_id")) != run_id:
                        continue
                    if flow_id and str(row.get("flow_id")) != flow_id:
                        continue
                    rows.append(row)
        except OSError:
            return []
        rows.reverse()
        return rows[: int(limit)] if limit and int(limit) > 0 else rows


#: The flow declaration a fresh workspace gets: the owner's real user flows, each of which is
#: genuinely executed against whatever target the operator points it at. The targets are
#: loopback placeholders for the demo site in ``tests/fixtures/verify_flows/site``;
#: ``--base-url`` (or :func:`apply_base_url`) rewrites them for a real run.
def template_flows(base_url: str = "http://127.0.0.1:8000") -> dict:
    """A declaration an operator can copy into ``.godsv/flows.json`` — every action real."""
    base = base_url.rstrip("/")
    return {
        "flows": [
            {
                "id": "open-and-read",
                "title": "open the page and see the declared heading, search control and list",
                "target": {"kind": "browser", "url": f"{base}/index.html"},
                "start_state": [{"check": "visible", "selector": "#q"}],
                "steps": [
                    {"action": "assert", "check": "text_contains", "selector": "h1",
                     "text": "Search"},
                    {"action": "assert", "check": "present", "selector": "#results"},
                    {"action": "screenshot", "name": "opened"},
                ],
                "end_state": [
                    {"check": "visible", "selector": "#q"},
                    {"check": "text_contains", "selector": "h1", "text": "Search"},
                    {"check": "value_equals", "selector": "#q", "value": ""},
                ],
            },
            {
                "id": "search-a-term",
                "title": "type a term, submit, and read it back in the result list",
                "target": {"kind": "browser", "url": f"{base}/index.html"},
                "start_state": [{"check": "visible", "selector": "#q"}],
                "steps": [
                    {"action": "fill", "selector": "#q", "value": "alpha"},
                    {"action": "click", "selector": "#go"},
                    {"action": "wait_for", "selector": "#results li", "value": "visible"},
                    {"action": "assert", "check": "text_contains", "selector": "#results",
                     "text": "alpha"},
                    {"action": "assert", "check": "count", "selector": "#results li", "count": 1},
                    {"action": "screenshot", "name": "searched"},
                ],
                "end_state": [
                    {"check": "text_contains", "selector": "#results", "text": "alpha"},
                    {"check": "count", "selector": "#results li", "count": 1},
                    {"check": "url_contains", "value": "/search.html"},
                ],
            },
            {
                "id": "follow-a-link",
                "title": "open a result and land on the article page",
                "target": {"kind": "browser", "url": f"{base}/index.html"},
                "start_state": [{"check": "visible", "selector": "#q"}],
                "steps": [
                    {"action": "fill", "selector": "#q", "value": "alpha"},
                    {"action": "click", "selector": "#go"},
                    {"action": "click", "selector": "#results li a"},
                    {"action": "wait_for", "selector": "#article", "value": "visible"},
                    {"action": "screenshot", "name": "article"},
                ],
                "end_state": [
                    {"check": "text_contains", "selector": "#article h2", "text": "Alpha"},
                    {"check": "url_contains", "value": "/article.html"},
                ],
            },
            {
                "id": "empty-search-is-refused",
                "title": "submitting an empty term says so instead of showing results",
                "target": {"kind": "browser", "url": f"{base}/index.html"},
                "start_state": [{"check": "visible", "selector": "#q"}],
                "steps": [
                    {"action": "click", "selector": "#go"},
                    {"action": "wait_for", "selector": "#message", "value": "visible"},
                    {"action": "assert", "check": "text_contains", "selector": "#message",
                     "text": "type a search term"},
                    {"action": "screenshot", "name": "refused"},
                ],
                "end_state": [
                    {"check": "text_contains", "selector": "#message",
                     "text": "type a search term"},
                    {"check": "count", "selector": "#results li", "count": 0},
                    {"check": "url_contains", "value": "/index.html"},
                ],
            },
            {
                "id": "history-back-returns-to-results",
                "title": "the browser back button returns to the result list",
                "target": {"kind": "browser", "url": f"{base}/index.html"},
                "start_state": [{"check": "visible", "selector": "#q"}],
                "steps": [
                    {"action": "fill", "selector": "#q", "value": "beta"},
                    {"action": "click", "selector": "#go"},
                    {"action": "wait_for", "selector": "#results li", "value": "visible"},
                    {"action": "click", "selector": "#results li a"},
                    {"action": "wait_for", "selector": "#article", "value": "visible"},
                    {"action": "back"},
                    {"action": "wait_for", "selector": "#results li", "value": "visible"},
                    {"action": "screenshot", "name": "back"},
                ],
                "end_state": [
                    {"check": "text_contains", "selector": "#results", "text": "beta"},
                    {"check": "url_contains", "value": "/search.html"},
                ],
            },
        ]
    }


def template_desktop_flows(display: str = ":90") -> dict:
    """A desktop flow an operator can copy: launch an app on the isolated display, then see it.

    Everything it declares is something the isolated runner can really do: a command inside the
    container, the runner's own window observation, and a capture of the isolated display.
    """
    return {
        "flows": [
            {
                "id": "launch-an-app-on-the-isolated-display",
                "title": "launch an app on the isolated display and see its window",
                "target": {"kind": "desktop", "display": display},
                "start_state": [{"check": "window_absent", "value": "skaldir-userflow-target"}],
                "steps": [
                    {
                        "action": "cli",
                        "argv": [
                            "sh", "-c",
                            "xmessage -name skaldir-userflow-target 'the flow target is up' "
                            ">/dev/null 2>&1 & sleep 2; echo LAUNCHED",
                        ],
                        "note": "launch the app under test on the isolated display",
                    },
                    {"action": "sleep", "ms": 800},
                    {"action": "assert", "check": "window_present",
                     "value": "skaldir-userflow-target"},
                    {"action": "screenshot", "name": "desktop"},
                    {"action": "press", "key": "Escape"},
                ],
                "end_state": [
                    {"check": "window_present", "value": "skaldir-userflow-target"},
                ],
            }
        ]
    }


def apply_base_url(flows: Sequence[Flow], base_url: str) -> tuple[Flow, ...]:
    """Rewrite loopback placeholder targets onto a real base URL (a run-shaped concern)."""
    base = str(base_url or "").rstrip("/")
    if not base:
        return tuple(flows)
    placeholders = ("http://127.0.0.1:8000", "http://localhost:8000")
    rewritten: list[Flow] = []
    for flow in flows:
        if flow.target_kind != TARGET_BROWSER or not flow.target_url:
            rewritten.append(flow)
            continue
        target = flow.target_url
        for prefix in placeholders:
            if target.startswith(prefix):
                target = base + target[len(prefix):]
                break
        steps = []
        for step in flow.steps:
            if step.action == STEP_GOTO and step.url:
                url = step.url
                for prefix in placeholders:
                    if url.startswith(prefix):
                        url = base + url[len(prefix):]
                        break
                steps.append(replace(step, url=url))
            else:
                steps.append(step)
        rewritten.append(replace(flow, target_url=target, steps=tuple(steps)))
    return tuple(rewritten)


__all__ = [
    "ANCHOR_CHECKS",
    "ANCHOR_CHECKS_BY_TARGET",
    "ANCHOR_WINDOW_ABSENT",
    "ANCHOR_WINDOW_PRESENT",
    "DEFAULT_FLOW_TIMEOUT_MS",
    "DEFAULT_SCREENSHOT_DIR",
    "DEFAULT_DESKTOP_TIMEOUT_S",
    "DeclarationError",
    "EVIDENCE_KIND",
    "FLOWS_FILENAME",
    "NON_SELECTOR_CHECKS",
    "SELECTOR_CHECKS",
    "Anchor",
    "CheckResult",
    "Flow",
    "FlowDeclaration",
    "FlowReport",
    "FlowResult",
    "FlowStore",
    "PASS_REQUIREMENT",
    "STEP_ACTIONS",
    "STEP_ACTIONS_BY_TARGET",
    "STEP_ASSERT",
    "STEP_BACK",
    "STEP_CLI",
    "STEP_CLICK",
    "STEP_FILL",
    "STEP_FORWARD",
    "STEP_GOTO",
    "STEP_PRESS",
    "STEP_SCREENSHOT",
    "STEP_SELECT",
    "STEP_SLEEP",
    "STEP_WAIT_FOR",
    "Screenshot",
    "Step",
    "StepResult",
    "TARGET_BROWSER",
    "TARGET_DESKTOP",
    "TARGET_KINDS",
    "apply_base_url",
    "browser_availability",
    "desktop_availability",
    "load_flows",
    "now_iso",
    "parse_anchor",
    "parse_flow",
    "parse_step",
    "run_flow",
    "run_flows",
    "template_desktop_flows",
    "template_flows",
    "verdict_for",
]
