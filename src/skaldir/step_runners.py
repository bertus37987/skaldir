"""Step runners for composite tools — the ``api`` and ``gui`` kinds COMPOSE refuses today.

``skaldir.compose_service`` executes a composite through one runner per step kind. Two of its
four kinds have a runner today (``tool`` through ``mcpserver._wrapper_for``, ``docker``
through ``docker_runtime.DockerRuntimeManager``); ``api`` and ``gui`` end in exit 3 with an
honest reason. This module supplies **exactly those two** and puts every kind behind one
registry, so a combined tool can chain an HTTP step and a GUI step like any other step — and
a kind this host really cannot run still ends in exit 3 with the missing capability named.

What makes the two runners honest
=================================

* **api** (:func:`run_api_step`). The request is *declared* (method, url, headers, params,
  body), the answer is *measured* (status, headers, body excerpt, per-assertion results) and
  the declared status/body assertions are evaluated against the real response — never
  against a claim. Transport failure, status mismatch and body mismatch are three different
  ``failure_class`` values, so a caller can tell them apart. Retries are declared, counted
  and reported (``attempts``); the transport is the project's declared ``httpx`` dependency
  (``pyproject.toml``: ``httpx>=0.27,<1``).
* **gui** (:func:`run_gui_step`). Nothing is simulated. The step is delegated to
  :mod:`skaldir.act_service` — ``run_action_list`` for a declared action list (window / click /
  type / scroll / key), ``run_subtool`` for a stored GUI sub-tool — which drives a **real**
  isolated bridge and reads the end state back from the real display. When this host has no
  usable bridge the outcome is ``blocked`` with the ACT layer's own precise missing
  capability; when the only thing available would be the in-process fixture (``dry-run``),
  the step is **refused** (``allow_fixture=False``) rather than reported as a GUI run.

Values flow in through the composite context
============================================

A composite binds ``$input.<name>`` / ``$steps.<id>.<key>`` references before a step runs
(``compose_service._bind_step_inputs``). Both runners here additionally substitute those
references *inside* their declared fields (``url``/``headers``/``params``/``body``/``json``
for api, the declared ``actions`` and ``end_state`` for gui) when the caller passes
``context=`` — same grammar, same failure rule: an unresolved reference is refused, never
left in a request or a GUI action as a literal ``$steps.…`` string.

The registry
============

``RUNNER_KINDS`` is exactly ``compose_service.STEP_KINDS`` (``tool``, ``docker``, ``api``,
``gui``). :func:`runner_registry` maps a kind to its runner object, :func:`registry_report`
answers the same dict shape ``compose_service.executor_report`` uses
(``{kind, executable, runner, reason}``, plus ``owner``), and :func:`run_step` dispatches.
The two existing kinds stay where they are (their runners live in ``compose_service`` by
design); the registry *points at* them so every kind resolves in one place.

Hand-over to the COMPOSE layer (this file does not edit ``compose_service.py``)
==============================================================================

One import line and three small edits in ``src/skaldir/compose_service.py``::

    from .step_runners import as_step_tuple, registry_report, run_api_step, run_gui_step

    1. build the executor table from the registry instead of the hardcoded four::

           _EXECUTORS = {
               kind: KindExecutor(row["kind"], row["runner"], row["reason"])
               for kind, row in registry_report().items()
           }

       (``api`` and ``gui`` then report ``executable: true``; ``missing_kinds`` is empty and
       the exit-3 refusal disappears for both kinds.)

    2. in ``run_composite``'s dispatch, replace the two refusals (the ``else:  # gui …``
       branch becomes an explicit ``elif step.kind == "gui":``)::

           elif step.kind == "api":
               outcome = run_api_step(step, arguments, context=context, runtime=runtime_path)
               ok, outputs, raw, detail, missing = as_step_tuple(outcome)
               entry.evidence = "step_runners.run_api_step (httpx; declared status/body assertions)"
               if outcome.blocked:
                   entry.status, entry.detail, entry.missing_kind = "blocked", detail, missing
                   entry.duration_ms = int((time.time() - step_started) * 1000)
                   out.steps.append(entry)
                   out.missing_kinds = [missing]
                   out.status, out.exit_code = "blocked", EXIT_BLOCKED
                   out.reason = f"step {step.id}: {detail}"
                   return out
           elif step.kind == "gui":
               outcome = run_gui_step(step, arguments, context=context, runtime=runtime_path)
               ok, outputs, raw, detail, missing = as_step_tuple(outcome)
               # …same blocked branch as api…

       The rest of the loop (``entry.raw = raw`` / ``entry.outputs = …`` / ``if not ok:
       failed``) already handles a real result, so nothing else changes. ``detail``/
       ``missing`` keep their meaning (a blocked outcome names the kind); the old
       ``_execute_api_step`` / ``_execute_gui_step`` refusals are what this replaces and
       can then go. ``outcome.exit_code`` tells the three results apart (0 ok · 1 a
       declaration problem · 2 the step ran and did not pass · 3 blocked).

       Both edits were proven in a scratch copy of ``src/`` (the original file untouched):
       a composite ``fetch`` (api) → ``drive`` (gui) → ``confirm`` (api) validated with
       ``missing_kinds: []`` and ran to ``status: verified, exit_code: 0`` — the GUI step
       reported ``live: true, isolation: container`` and the last HTTP step received the
       window title the GUI step observed plus the first step's status code.

    3. optional, for the same validation rigour the docker kind already has: use
       :func:`output_keys_for` so a declared step output that the runner cannot produce is a
       blocking validation problem instead of a run-time surprise (see ``API_OUTPUT_KEYS`` /
       ``GUI_OUTPUT_KEYS``).

    4. **known gap on the COMPOSE side, not fixed here** (``compose_service`` owns it): the
       api validation requires a literal ``http(s)`` ``url``, so a composite whose url is a
       ``$input.…`` / ``$steps.…`` reference is rejected at validation time even though this
       runner resolves such a reference (it substitutes refs inside ``url``/``headers`` /
       ``params`` / ``body`` / ``json``). A gui step likewise must name a ``target``: use a
       stored sub-tool id, or the literal marker ``"inline"`` (:data:`GUI_INLINE_TARGETS`)
       when the flow is declared in the step's own ``actions``.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "API_ARG_KEYS",
    "API_OUTPUT_KEYS",
    "DEFAULT_BACKOFF_S",
    "DEFAULT_MAX_EXCERPT",
    "DEFAULT_RETRY_ON_STATUS",
    "FAILURE_BODY",
    "FAILURE_BRIDGE",
    "FAILURE_NETWORK",
    "FAILURE_NOT_FOUND",
    "FAILURE_POLICY",
    "FAILURE_RUNNER",
    "FAILURE_STATUS",
    "FAILURE_TIMEOUT",
    "FAILURE_USAGE",
    "FAILURE_VERIFICATION",
    "GUI_ARG_KEYS",
    "GUI_INLINE_TARGETS",
    "GUI_OUTPUT_KEYS",
    "KIND_API",
    "KIND_DOCKER",
    "KIND_GUI",
    "KIND_TOOL",
    "RUNNER_KINDS",
    "RunnerRefused",
    "StepOutcome",
    "as_step_tuple",
    "available_bridges",
    "output_keys_for",
    "registry_report",
    "run_api_step",
    "run_gui_step",
    "run_step",
    "runner_for",
    "runner_registry",
]

try:  # the product CLI's exit-code vocabulary wins; this module also runs standalone
    from . import cli_contract as _CONTRACT
except Exception:  # noqa: BLE001 — an older build without the contract module
    _CONTRACT = None

EXIT_OK = int(getattr(_CONTRACT, "EXIT_OK", 0))
EXIT_USAGE = int(getattr(_CONTRACT, "EXIT_USAGE", 1))
EXIT_VERIFICATION_FAILED = int(getattr(_CONTRACT, "EXIT_VERIFICATION_FAILED", 2))
EXIT_BLOCKED = int(getattr(_CONTRACT, "EXIT_BLOCKED", 3))
EXIT_NOT_FOUND = int(getattr(_CONTRACT, "EXIT_NOT_FOUND", 5))
EXIT_INTERNAL = int(getattr(_CONTRACT, "EXIT_INTERNAL", 7))

KIND_TOOL = "tool"
KIND_DOCKER = "docker"
KIND_API = "api"
KIND_GUI = "gui"

#: Exactly ``compose_service.STEP_KINDS`` — the registry answers a runner for every kind.
RUNNER_KINDS: tuple[str, ...] = (KIND_TOOL, KIND_DOCKER, KIND_API, KIND_GUI)

#: The declared kinds ``skaldir.act_service`` accepts in an action list (its own vocabulary).
GUI_ACTION_KINDS: tuple[str, ...] = (
    "cli",
    "api_call",
    "mouse",
    "key",
    "type_text",
    "scroll",
    "window",
    "dom_click",
    "dom_fill",
)

#: The runner names ``act_service.resolve_runner`` understands.
GUI_RUNNER_KINDS: tuple[str, ...] = (
    "auto",
    "container-gui",
    "container",
    "sandbox",
    "dry-run",
)

#: A gui step may declare its flow inline; ``target`` then carries one of these words
#: (``compose_service`` requires a ``target`` on a gui step — the sub-tool id, or this
#: marker meaning "the actions are declared in the step itself").
GUI_INLINE_TARGETS: tuple[str, ...] = ("inline", "actions", "declare")

# --------------------------------------------------------------------------- #
# the error taxonomy (the shared vocabulary of failure_class)                    #
# --------------------------------------------------------------------------- #

#: Mirrors ``docker_runtime``'s ``failure_class`` strings where they exist, so a caller can
#: read every step's failure the same way regardless of its kind.
FAILURE_POLICY = "policy_violation"
FAILURE_USAGE = "usage"
FAILURE_TIMEOUT = "timeout"
FAILURE_NETWORK = "network_error"
FAILURE_STATUS = "status_mismatch"
FAILURE_BODY = "body_mismatch"
FAILURE_BRIDGE = "bridge_unavailable"
FAILURE_VERIFICATION = "verification_failed"
FAILURE_NOT_FOUND = "not_found"
FAILURE_RUNNER = "runner_error"


class RunnerRefused(ValueError):
    """A step this runner refuses to run — a *declaration* problem, never a result.

    Raised only for something the caller can fix (an unknown field, a non-http url, an
    unparsable retry policy, an unresolved ``$ref``). A refused step is never executed.
    """


# --------------------------------------------------------------------------- #
# the result of one step                                                         #
# --------------------------------------------------------------------------- #


@dataclass
class StepOutcome:
    """One executed step: its outputs, its honest verdict and why.

    ``ok`` is the runner's own ``exit_ok`` for the step — a composite may treat it as the
    step's success. ``blocked`` says this host cannot run the kind at all (compose turns that
    into its exit 3 with ``missing_kind`` named); ``failure_class`` is the shared taxonomy.
    """

    kind: str
    ok: bool
    blocked: bool = False
    missing_kind: str = ""
    outputs: dict = field(default_factory=dict)
    raw: Any = None
    detail: str = ""
    failure_class: str = ""
    exit_code: int = EXIT_OK
    duration_ms: int = 0

    @property
    def status(self) -> str:
        """``ok`` | ``blocked`` | ``failed`` — the three verdicts a step can have."""
        if self.ok:
            return "ok"
        return "blocked" if self.blocked else "failed"

    @classmethod
    def success(cls, kind: str, outputs: Mapping[str, Any], raw: Any, detail: str) -> StepOutcome:
        return cls(
            kind=kind,
            ok=True,
            outputs=dict(outputs),
            raw=raw,
            detail=str(detail),
            exit_code=EXIT_OK,
        )

    @classmethod
    def failure(
        cls,
        kind: str,
        detail: str,
        *,
        outputs: Mapping[str, Any] | None = None,
        raw: Any = None,
        failure_class: str = FAILURE_RUNNER,
        exit_code: int = EXIT_VERIFICATION_FAILED,
    ) -> StepOutcome:
        """The step really ran (or really could not reach its target) and did not pass.

        ``exit_code`` is :data:`EXIT_USAGE` for a *declaration* problem (the caller can fix
        it and no request/action was made) and :data:`EXIT_VERIFICATION_FAILED` otherwise.
        """
        return cls(
            kind=kind,
            ok=False,
            outputs=dict(outputs or {}),
            raw=raw,
            detail=str(detail),
            failure_class=str(failure_class),
            exit_code=int(exit_code),
        )

    @classmethod
    def block(
        cls,
        kind: str,
        detail: str,
        *,
        missing_kind: str = "",
        outputs: Mapping[str, Any] | None = None,
        raw: Any = None,
        failure_class: str = FAILURE_BRIDGE,
    ) -> StepOutcome:
        """This host cannot run the step — exit 3, the missing capability named."""
        return cls(
            kind=kind,
            ok=False,
            blocked=True,
            missing_kind=str(missing_kind or kind),
            outputs=dict(outputs or {}),
            raw=raw,
            detail=str(detail),
            failure_class=str(failure_class),
            exit_code=EXIT_BLOCKED,
        )

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "status": self.status,
            "ok": self.ok,
            "blocked": self.blocked,
            "missing_kind": self.missing_kind,
            "outputs": dict(self.outputs),
            "detail": self.detail,
            "failure_class": self.failure_class,
            "exit_code": self.exit_code,
            "duration_ms": self.duration_ms,
        }


def as_step_tuple(outcome: StepOutcome) -> tuple[bool, dict, Any, str, str]:
    """The 5-tuple ``compose_service.run_composite`` passes between its dispatch branches.

    ``(ok, outputs, raw, detail, missing_kind)`` — identical in meaning to what the existing
    ``_execute_tool_step`` / ``_execute_docker_step`` helpers return, so the COMPOSE layer
    needs no new convention.
    """
    return (
        bool(outcome.ok),
        dict(outcome.outputs),
        outcome.raw,
        str(outcome.detail),
        str(outcome.missing_kind),
    )


# --------------------------------------------------------------------------- #
# step fields and reference substitution                                         #
# --------------------------------------------------------------------------- #

_REF_RE = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+")

#: Field names a step may carry, whichever object describes it (a ``StepSpec`` or a mapping).
STEP_FIELDS: tuple[str, ...] = (
    "id",
    "kind",
    "target",
    "url",
    "method",
    "timeout_s",
    "network",
    "inputs",
    "outputs",
    "note",
)


def _field(step: Any, name: str, default: Any = None) -> Any:
    """One field of a step declaration — a ``StepSpec`` attribute or a mapping key."""
    if isinstance(step, Mapping):
        value = step.get(name, default)
    else:
        value = getattr(step, name, default)
    return default if value is None else value


def _step_id(step: Any) -> str:
    return str(_field(step, "id", "") or "?")


def _dig(context: Mapping[str, Any], ref: str) -> tuple[bool, Any]:
    """Resolve ``$input.x`` / ``$steps.id.key`` against the composite context.

    Same grammar as ``compose_service._dig`` (a duplicated 15-line walker, not a second
    contract: it exists so this module stays importable without ``compose_service``, which
    will import *this* module).
    """
    parts = str(ref).lstrip("$").split(".")
    if len(parts) < 2 or parts[0] not in ("input", "steps"):
        return False, f"reference {ref!r} is not $input.<name> or $steps.<id>.<key>"
    node: Any = (context or {}).get(parts[0])
    for index, part in enumerate(parts[1:], start=1):
        if isinstance(node, Mapping) and part in node:
            node = node[part]
            continue
        where = "input" if parts[0] == "input" else f"step {parts[1]!r}"
        return False, f"{where} has no {'.'.join(parts[1 : index + 1])!r}"
    return True, node


def _resolve_value(
    value: Any,
    *,
    context: Mapping[str, Any] | None,
    resolve: Callable[[str], Any] | None,
) -> Any:
    """Resolve one whole-value ``$ref`` (the shape ``compose_service._bind`` produces)."""
    if not (isinstance(value, str) and value.startswith("$")):
        return value
    if resolve is not None:
        return resolve(value)
    if context is None:
        raise RunnerRefused(
            f"the value {value!r} is a reference but no composite context was supplied — "
            "pass context=<the composite context> (or resolve=<callable>)"
        )
    found, resolved = _dig(context, value)
    if not found:
        raise RunnerRefused(str(resolved))
    return resolved


def _substitute(
    text: Any,
    *,
    context: Mapping[str, Any] | None,
    resolve: Callable[[str], Any] | None,
) -> Any:
    """Substitute every inline ``$ref`` inside one string; a non-string passes through.

    An unresolved reference is **refused** — a request carrying a literal ``$steps.…`` token
    would look like a valid call and silently do the wrong thing.
    """
    if not isinstance(text, str) or "$" not in text:
        return text
    out = str(text)
    for ref in _REF_RE.findall(out):
        if resolve is not None:
            value = resolve(ref)
        elif context is None:
            raise RunnerRefused(
                f"{ref} is a reference but no composite context was supplied — pass "
                "context=<the composite context> (or resolve=<callable>)"
            )
        else:
            found, value = _dig(context, ref)
            if not found:
                raise RunnerRefused(str(value))
        out = out.replace(ref, str(value))
    return out


# --------------------------------------------------------------------------- #
# the API runner                                                                 #
# --------------------------------------------------------------------------- #

#: The declared request/assertion fields an api step may bind (from ``$input``/``$steps``
#: or as literals). An unknown name is refused: a second, silently-ignored binding path is
#: exactly how a step runs with an unbound value.
API_ARG_KEYS: tuple[str, ...] = (
    "url",
    "method",
    "headers",
    "params",
    "json",
    "body",
    "expect_status",
    "expect_body_contains",
    "expect_body_absent",
    "expect_body_regex",
    "retry",
    "timeout_s",
    "follow_redirects",
    "max_excerpt",
)

#: Every key an api step's result carries (always all of them: a declared output may be any
#: subset, exactly like ``compose_service.DOCKER_OUTPUT_KEYS``).
API_OUTPUT_KEYS: tuple[str, ...] = (
    "ok",
    "status_code",
    "url",
    "method",
    "attempts",
    "elapsed_ms",
    "content_type",
    "text",
    "text_len",
    "truncated",
    "headers",
    "json",
    "assertions",
    "failure_class",
    "error",
)

#: HTTP methods an api step may declare (httpx would accept anything; a typo must not).
API_METHODS: tuple[str, ...] = (
    "GET",
    "POST",
    "PUT",
    "PATCH",
    "DELETE",
    "HEAD",
    "OPTIONS",
)

DEFAULT_TIMEOUT_S = 30.0
DEFAULT_MAX_EXCERPT = 4000
MAX_ATTEMPTS = 10
#: What a retry policy retries when the caller sets ``retry.attempts > 1`` without a list.
DEFAULT_RETRY_ON_STATUS: tuple[int, ...] = (408, 429, 500, 502, 503, 504)
DEFAULT_BACKOFF_S = 0.25


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _text_list(value: Any, *, where: str) -> tuple[str, ...]:
    out: list[str] = []
    for item in _as_list(value):
        text = str(item)
        if not text:
            raise RunnerRefused(f"{where}: an empty assertion is not an assertion")
        out.append(text)
    return tuple(out)


def _status_list(value: Any) -> tuple[Any, ...]:
    """``expect_status`` / ``retry.on_status``: ints, digits or a ``5xx`` family pattern."""
    out: list[Any] = []
    for item in _as_list(value):
        if isinstance(item, bool):
            raise RunnerRefused(f"a status assertion cannot be a bool: {item!r}")
        if isinstance(item, int):
            out.append(int(item))
            continue
        text = str(item).strip().lower()
        if re.fullmatch(r"[1-5]xx", text):
            out.append(text)
            continue
        try:
            out.append(int(text))
        except ValueError as exc:
            raise RunnerRefused(
                f"status {item!r} is neither a status code nor an 'Nxx' family pattern"
            ) from exc
    return tuple(out)


def _status_matches(allowed: Sequence[Any], status: int) -> bool:
    for item in allowed:
        if isinstance(item, int):
            if status == item:
                return True
        elif re.fullmatch(r"[1-5]xx", str(item)) and status // 100 == int(str(item)[0]):
            return True
    return False


@dataclass(frozen=True)
class ApiRetry:
    """A declared retry policy: how many attempts, on what, and with which backoff."""

    attempts: int = 1
    backoff_s: float = DEFAULT_BACKOFF_S
    on_status: tuple[Any, ...] = ()
    on_error: bool = True

    @property
    def retries(self) -> bool:
        return self.attempts > 1

    def retry_status(self, status: int) -> bool:
        if not self.retries:
            return False
        allowed = self.on_status or DEFAULT_RETRY_ON_STATUS
        return _status_matches(allowed, status)

    def to_dict(self) -> dict:
        return {
            "attempts": int(self.attempts),
            "backoff_s": float(self.backoff_s),
            "on_status": [item for item in (self.on_status or DEFAULT_RETRY_ON_STATUS)],
            "on_error": bool(self.on_error),
        }

    @staticmethod
    def from_value(value: Any) -> ApiRetry:
        if value in (None, ""):
            return ApiRetry()
        if isinstance(value, (int, str)) and not isinstance(value, bool):
            attempts = int(str(value))
            if attempts < 1 or attempts > MAX_ATTEMPTS:
                raise RunnerRefused(f"retry attempts {attempts} is outside 1..{MAX_ATTEMPTS}")
            return ApiRetry(attempts=attempts)
        if not isinstance(value, Mapping):
            raise RunnerRefused(
                "retry must be an object like {'attempts': 3, 'backoff_s': 0.5, "
                "'on_status': ['5xx']} (or a bare number of attempts)"
            )
        unknown = sorted(set(value) - {"attempts", "backoff_s", "on_status", "on_error"})
        if unknown:
            raise RunnerRefused(
                f"retry has unknown field(s) {unknown} — allowed: attempts, backoff_s, "
                "on_status, on_error"
            )
        try:
            attempts = int(value.get("attempts", 1) or 1)
        except (TypeError, ValueError) as exc:
            raise RunnerRefused(f"retry.attempts is not a number: {exc}") from exc
        if attempts < 1 or attempts > MAX_ATTEMPTS:
            raise RunnerRefused(f"retry attempts {attempts} is outside 1..{MAX_ATTEMPTS}")
        try:
            backoff = float(value.get("backoff_s", DEFAULT_BACKOFF_S) or 0.0)
        except (TypeError, ValueError) as exc:
            raise RunnerRefused(f"retry.backoff_s is not a number: {exc}") from exc
        if backoff < 0:
            raise RunnerRefused(f"retry.backoff_s must not be negative ({backoff})")
        return ApiRetry(
            attempts=attempts,
            backoff_s=backoff,
            on_status=_status_list(value.get("on_status")),
            on_error=bool(value.get("on_error", True)),
        )


@dataclass(frozen=True)
class ApiStepDecl:
    """One declared HTTP step, read from the step + its bound values (never invented)."""

    url: str
    method: str
    headers: dict[str, str] = field(default_factory=dict)
    params: dict[str, str] = field(default_factory=dict)
    json_body: Any = None
    body: str = ""
    expect_status: tuple[Any, ...] = ()
    expect_body_contains: tuple[str, ...] = ()
    expect_body_absent: tuple[str, ...] = ()
    expect_body_regex: tuple[str, ...] = ()
    retry: ApiRetry = field(default_factory=ApiRetry)
    timeout_s: float = DEFAULT_TIMEOUT_S
    follow_redirects: bool = True
    max_excerpt: int = DEFAULT_MAX_EXCERPT

    @property
    def has_body(self) -> bool:
        return self.json_body is not None or bool(self.body)

    def to_dict(self) -> dict:
        """The request as it will really go out (the payload's own evidence)."""
        return {
            "method": self.method,
            "url": self.url,
            "headers": dict(self.headers),
            "params": dict(self.params),
            "json": self.json_body,
            "body": self.body,
            "expect_status": [item for item in self.expect_status],
            "expect_body_contains": list(self.expect_body_contains),
            "expect_body_absent": list(self.expect_body_absent),
            "expect_body_regex": list(self.expect_body_regex),
            "retry": self.retry.to_dict(),
            "timeout_s": float(self.timeout_s),
            "follow_redirects": bool(self.follow_redirects),
            "max_excerpt": int(self.max_excerpt),
        }

    @staticmethod
    def from_step(
        step: Any,
        arguments: Mapping[str, Any] | None = None,
        *,
        context: Mapping[str, Any] | None = None,
        resolve: Callable[[str], Any] | None = None,
    ) -> ApiStepDecl:
        """Read the declaration; an unsupported or unbound field is **refused**.

        Nothing is guessed: a typo like ``expct_status`` must not silently turn into a call
        whose assertions nobody checks.
        """
        bound = dict(arguments or {})
        unknown = sorted(set(bound) - set(API_ARG_KEYS))
        if unknown:
            raise RunnerRefused(
                f"step {_step_id(step)}: unsupported api field(s) {unknown} — allowed: "
                f"{list(API_ARG_KEYS)}"
            )

        def sub(value: Any) -> Any:
            return _substitute(value, context=context, resolve=resolve)

        raw_url = _resolve_value(
            bound.get("url") or _field(step, "url", ""), context=context, resolve=resolve
        )
        url = str(sub(raw_url) or "").strip()
        if not url:
            raise RunnerRefused(
                f"step {_step_id(step)}: an api step needs a url (the step's own `url` or a "
                "bound `url` value)"
            )
        method = str(
            sub(_resolve_value(bound.get("method") or _field(step, "method", "GET"),
                               context=context, resolve=resolve))
            or "GET"
        ).strip().upper()
        if method not in API_METHODS:
            raise RunnerRefused(
                f"step {_step_id(step)}: method {method!r} is not one of {list(API_METHODS)}"
            )
        if not (url.startswith("http://") or url.startswith("https://")):
            raise RunnerRefused(
                f"step {_step_id(step)}: url {url!r} is not an http(s) URL — an api step "
                "speaks HTTP, nothing else"
            )

        def string_map(value: Any, where: str) -> dict[str, str]:
            if value in (None, "", {}):
                return {}
            if not isinstance(value, Mapping):
                raise RunnerRefused(f"step {_step_id(step)}: {where} must be an object")
            out: dict[str, str] = {}
            for name, item in value.items():
                key = str(sub(name)).strip()
                if not key:
                    raise RunnerRefused(f"step {_step_id(step)}: {where} has an empty name")
                out[key] = str(sub(_resolve_value(item, context=context, resolve=resolve)))
            return out

        json_body = bound.get("json")
        if json_body is not None:
            json_body = _substitute_refs_deep(json_body, context=context, resolve=resolve)
        body = bound.get("body")
        body = "" if body in (None, "") else str(
            sub(_resolve_value(body, context=context, resolve=resolve))
        )
        if json_body is not None and body:
            raise RunnerRefused(
                f"step {_step_id(step)}: both `json` and `body` are declared — pick one "
                "(two body paths, one of them silently ignored, is how a request goes out wrong)"
            )

        try:
            timeout = float(
                _resolve_value(
                    bound.get("timeout_s") or _field(step, "timeout_s", DEFAULT_TIMEOUT_S),
                    context=context,
                    resolve=resolve,
                )
            )
        except (TypeError, ValueError) as exc:
            raise RunnerRefused(f"step {_step_id(step)}: timeout_s is not a number: {exc}") from exc
        if timeout <= 0:
            raise RunnerRefused(f"step {_step_id(step)}: timeout_s must be positive ({timeout})")

        raw_excerpt = _resolve_value(
            bound.get("max_excerpt", DEFAULT_MAX_EXCERPT), context=context, resolve=resolve
        )
        try:
            max_excerpt = int(raw_excerpt)
        except (TypeError, ValueError) as exc:
            raise RunnerRefused(
                f"step {_step_id(step)}: max_excerpt is not a number: {exc}"
            ) from exc
        if max_excerpt < 1:
            raise RunnerRefused(f"step {_step_id(step)}: max_excerpt must be positive")

        regexes = _text_list(bound.get("expect_body_regex"), where="expect_body_regex")
        for pattern in regexes:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise RunnerRefused(
                    f"step {_step_id(step)}: expect_body_regex {pattern!r} does not compile: {exc}"
                ) from exc

        return ApiStepDecl(
            url=url,
            method=method,
            headers=string_map(bound.get("headers"), "headers"),
            params=string_map(bound.get("params"), "params"),
            json_body=json_body,
            body=body,
            expect_status=_status_list(bound.get("expect_status")),
            expect_body_contains=_text_list(
                bound.get("expect_body_contains"), where="expect_body_contains"
            ),
            expect_body_absent=_text_list(
                bound.get("expect_body_absent"), where="expect_body_absent"
            ),
            expect_body_regex=regexes,
            retry=ApiRetry.from_value(bound.get("retry")),
            timeout_s=timeout,
            follow_redirects=bool(bound.get("follow_redirects", True)),
            max_excerpt=max_excerpt,
        )


def _substitute_refs_deep(
    value: Any,
    *,
    context: Mapping[str, Any] | None,
    resolve: Callable[[str], Any] | None,
) -> Any:
    """Substitute refs inside a JSON body (nested dicts/lists included)."""
    if isinstance(value, str):
        if value.startswith("$") and _REF_RE.fullmatch(value):
            return _resolve_value(value, context=context, resolve=resolve)
        return _substitute(value, context=context, resolve=resolve)
    if isinstance(value, Mapping):
        return {
            key: _substitute_refs_deep(item, context=context, resolve=resolve)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_substitute_refs_deep(item, context=context, resolve=resolve) for item in value]
    return value


def _api_payload(
    decl: ApiStepDecl,
    *,
    status: int,
    headers: Mapping[str, str],
    text: str,
    body_json: Any,
    attempts: int,
    elapsed_ms: int,
    assertions: Sequence[Mapping[str, Any]],
    failure_class: str,
    error: str,
    ok: bool,
) -> dict:
    """Every ``API_OUTPUT_KEYS`` key, always — whatever the run did."""
    excerpt = text[: decl.max_excerpt]
    return {
        "ok": bool(ok),
        "status_code": int(status),
        "url": decl.url,
        "method": decl.method,
        "attempts": int(attempts),
        "elapsed_ms": int(elapsed_ms),
        "content_type": str(headers.get("content-type", "") or ""),
        "text": excerpt,
        "text_len": len(text),
        "truncated": len(text) > len(excerpt),
        "headers": {str(key): str(value) for key, value in dict(headers).items()},
        "json": body_json,
        "assertions": [dict(item) for item in assertions],
        "failure_class": str(failure_class),
        "error": str(error),
    }


def _check_assertions(decl: ApiStepDecl, text: str, status: int) -> list[dict]:
    """Evaluate the declared status/body assertions against the **real** response."""
    out: list[dict] = []
    if decl.expect_status:
        ok = _status_matches(decl.expect_status, status)
        out.append(
            {
                "check": "expect_status",
                "ok": ok,
                "expect": [item for item in decl.expect_status],
                "actual": status,
                "detail": f"status {status} in {[item for item in decl.expect_status]} -> {ok}",
            }
        )
    for needle in decl.expect_body_contains:
        ok = needle in text
        out.append(
            {
                "check": "expect_body_contains",
                "ok": ok,
                "expect": needle,
                "actual": text[:200],
                "detail": f"the body contains {needle!r} -> {ok}",
            }
        )
    for needle in decl.expect_body_absent:
        ok = needle not in text
        out.append(
            {
                "check": "expect_body_absent",
                "ok": ok,
                "expect": needle,
                "actual": text[:200],
                "detail": f"the body does not contain {needle!r} -> {ok}",
            }
        )
    for pattern in decl.expect_body_regex:
        ok = re.search(pattern, text) is not None
        out.append(
            {
                "check": "expect_body_regex",
                "ok": ok,
                "expect": pattern,
                "actual": text[:200],
                "detail": f"the body matches /{pattern}/ -> {ok}",
            }
        )
    return out


def run_api_step(
    step: Any,
    arguments: Mapping[str, Any] | None = None,
    *,
    context: Mapping[str, Any] | None = None,
    resolve: Callable[[str], Any] | None = None,
    runtime: str | Path | None = None,
    client: Any = None,
    transport: Any = None,
    sleep: Callable[[float], None] | None = None,
) -> StepOutcome:
    """Execute one declared HTTP step and report what really happened.

    ``client`` is a ready ``httpx.Client`` the **caller owns** (it is not closed here);
    ``transport`` is an ``httpx`` transport (tests use ``httpx.MockTransport``); without
    either, a client is built and closed for this step. ``sleep`` lets a test observe the
    backoff without waiting for it. Nothing is retried unless the step declares it, and the
    number of attempts that really happened is always in the payload.
    """
    del runtime  # an HTTP step needs no workspace; accepted for a uniform call signature
    started = time.time()
    try:
        decl = ApiStepDecl.from_step(step, arguments, context=context, resolve=resolve)
    except RunnerRefused as exc:
        return StepOutcome.failure(
            KIND_API,
            str(exc),
            failure_class=FAILURE_USAGE,
            exit_code=EXIT_USAGE,
            raw={"usage_error": str(exc)},
        )

    try:
        import httpx
    except Exception as exc:  # noqa: BLE001 — the dependency is the capability here
        return StepOutcome.block(
            KIND_API,
            "the HTTP runner needs the httpx dependency (pyproject.toml: "
            f"httpx>=0.27,<1) and it could not be imported: {type(exc).__name__}: {exc}",
            missing_kind=KIND_API,
            raw={"declared": decl.to_dict()},
        )

    owns_client = client is None
    if owns_client:
        try:
            client = httpx.Client(transport=transport)
        except Exception as exc:  # noqa: BLE001 — an unbuildable client is a real failure
            return StepOutcome.failure(
                KIND_API,
                f"the httpx client could not be built: {type(exc).__name__}: {exc}",
                failure_class=FAILURE_RUNNER,
                raw={"declared": decl.to_dict()},
            )

    request_kwargs: dict[str, Any] = {
        "headers": dict(decl.headers) or None,
        "params": dict(decl.params) or None,
        "timeout": float(decl.timeout_s),
        "follow_redirects": bool(decl.follow_redirects),
    }
    if decl.json_body is not None:
        request_kwargs["json"] = decl.json_body
    elif decl.body:
        request_kwargs["content"] = decl.body.encode("utf-8")

    attempts = 0
    failure_class = ""
    error = ""
    answered = False  # did the LAST attempt get a response at all?
    last_payload: dict | None = None
    try:
        while attempts < decl.retry.attempts:
            attempts += 1
            failure_class, error, answered = "", "", False
            try:
                response = client.request(decl.method, decl.url, **request_kwargs)
            except httpx.TimeoutException as exc:  # httpx's own word for a timeout
                failure_class = FAILURE_TIMEOUT
                error = (
                    f"the request timed out after {decl.timeout_s:g}s: "
                    f"{type(exc).__name__}: {exc}"
                )
            except httpx.TransportError as exc:
                failure_class = FAILURE_NETWORK
                error = f"the request did not reach a server: {type(exc).__name__}: {exc}"
            except Exception as exc:  # noqa: BLE001 — any other httpx/OS error, named
                failure_class = FAILURE_RUNNER
                error = f"the request failed: {type(exc).__name__}: {exc}"
            else:
                answered = True
                text = str(response.text or "")
                headers = {str(key).lower(): str(value) for key, value in response.headers.items()}
                try:
                    body_json = response.json() if text else None
                except Exception:  # noqa: BLE001 — a non-JSON answer is a normal answer
                    body_json = None
                assertions = _check_assertions(decl, text, int(response.status_code))
                failed = [item for item in assertions if not item["ok"]]
                if failed:
                    failure_class = (
                        FAILURE_STATUS
                        if any(item["check"] == "expect_status" for item in failed)
                        else FAILURE_BODY
                    )
                    error = "; ".join(str(item["detail"]) for item in failed)
                last_payload = _api_payload(
                    decl,
                    status=int(response.status_code),
                    headers=headers,
                    text=text,
                    body_json=body_json,
                    attempts=attempts,
                    elapsed_ms=int((time.time() - started) * 1000),
                    assertions=assertions,
                    failure_class=failure_class,
                    error=error,
                    ok=not failed,
                )
                if not failed:
                    verified = f"HTTP {response.status_code} answered"
                    if assertions:
                        verified += f" ({len(assertions)} assertion(s) held)"
                    return _finish_api(
                        StepOutcome.success(KIND_API, last_payload, last_payload, verified), started
                    )
                if not decl.retry.retry_status(int(response.status_code)):
                    return _finish_api(
                        StepOutcome.failure(KIND_API, error, outputs=last_payload,
                                            raw=last_payload, failure_class=failure_class),
                        started,
                    )
            if attempts >= decl.retry.attempts:
                break
            if failure_class in (FAILURE_TIMEOUT, FAILURE_NETWORK, FAILURE_RUNNER):
                if not decl.retry.on_error:
                    break
            if decl.retry.backoff_s:
                (sleep or time.sleep)(float(decl.retry.backoff_s))
        # Reuse the last real *response* only when the last attempt really got one: a
        # payload that shows a 503 body next to a "timed out" error would read as if the
        # service had answered, which it did not.
        if last_payload is not None and answered:
            payload = last_payload
            payload["attempts"] = attempts
            payload["failure_class"] = failure_class
            payload["error"] = error
        else:
            payload = _api_payload(
                decl,
                status=0,
                headers={},
                text="",
                body_json=None,
                attempts=attempts,
                elapsed_ms=int((time.time() - started) * 1000),
                assertions=[],
                failure_class=failure_class or FAILURE_RUNNER,
                error=error,
                ok=False,
            )
        detail = (
            f"{attempts} attempt(s) did not pass: {error}"
            if attempts > 1
            else (error or "the request did not pass")
        )
        return _finish_api(
            StepOutcome.failure(
                KIND_API, detail, outputs=payload, raw=payload,
                failure_class=failure_class or FAILURE_RUNNER,
            ),
            started,
        )
    finally:
        if owns_client:
            try:
                client.close()
            except Exception:  # noqa: BLE001 — a close failure must not rewrite the verdict
                pass


def _finish_api(outcome: StepOutcome, started: float) -> StepOutcome:
    outcome.duration_ms = int((time.time() - started) * 1000)
    return outcome


# --------------------------------------------------------------------------- #
# the GUI runner                                                                 #
# --------------------------------------------------------------------------- #

#: The declared fields a gui step may bind. Either a stored sub-tool (``subtool`` / the
#: step's ``target``) or an inline flow (``actions`` + ``end_state``) — never both.
GUI_ARG_KEYS: tuple[str, ...] = (
    "subtool",
    "actions",
    "end_state",
    "runner_kind",
    "state",
    "app_version",
    "locale",
    "goal",
    "session_id",
    "timeout_s",
    "write_record",
    "screenshot",
)

#: Every key a gui step's result carries (``compose_service`` may validate declared outputs
#: against this tuple, exactly as it does for docker steps).
GUI_OUTPUT_KEYS: tuple[str, ...] = (
    "ok",
    "status",
    "verified",
    "exit_code",
    "isolation",
    "bridge",
    "live",
    "scope",
    "reason",
    "executed",
    "actions",
    "observation",
    "end_state",
    "subtool",
    "missing_capability",
    "failure_class",
    # The visual evidence and the teardown result: a GUI step that ran on a real display
    # can hand the caller the PNG it captured and the proof that it cleaned up after itself.
    "screenshot",
    "cleanup",
)

#: Which ACT capability carries a declared action kind (used to name the precise gap).
_GUI_KIND_CAPABILITY: dict[str, str] = {
    "mouse": "click",
    "scroll": "scroll",
    "key": "key",
    "type_text": "type_text",
    "window": "window.list",
    "dom_click": "click",
    "dom_fill": "type_text",
    "cli": "observe.window_titles",
}


@dataclass(frozen=True)
class GuiStepDecl:
    """One declared GUI step: a stored sub-tool, or an inline action list + end state."""

    target: str
    subtool: str = ""
    actions: tuple = ()
    end_state: Any = None
    runner_kind: str = "auto"
    state: Any = None
    app_version: str = ""
    locale: str = ""
    goal: str = ""
    session_id: str = "compose-gui"
    timeout_s: float = 90.0
    write_record: bool = True
    #: Capture the end state from the display. **On by default**: a GUI step whose whole
    #: point is a visible end state should leave the picture of it, and the capture is the
    #: product's own (xwd + the stdlib decoder), so it costs no new dependency.
    screenshot: bool = True

    @property
    def inline(self) -> bool:
        return bool(self.actions)

    @property
    def modes(self) -> list[str]:
        declared = (("sub-tool", bool(self.subtool)), ("inline", self.inline))
        return [name for name, on in declared if on]

    def to_dict(self) -> dict:
        return {
            "target": self.target,
            "subtool": self.subtool,
            "actions": [dict(item) if isinstance(item, Mapping) else item for item in self.actions],
            "end_state": self.end_state,
            "runner_kind": self.runner_kind,
            "state": str(self.state) if self.state else "",
            "app_version": self.app_version,
            "locale": self.locale,
            "goal": self.goal,
            "timeout_s": float(self.timeout_s),
            "write_record": bool(self.write_record),
            "screenshot": bool(self.screenshot),
        }

    @staticmethod
    def from_step(
        step: Any,
        arguments: Mapping[str, Any] | None = None,
    ) -> GuiStepDecl:
        """Read the declaration; a step with no flow, or with two, is **refused**."""
        bound = dict(arguments or {})
        unknown = sorted(set(bound) - set(GUI_ARG_KEYS))
        if unknown:
            raise RunnerRefused(
                f"step {_step_id(step)}: unsupported gui field(s) {unknown} — allowed: "
                f"{list(GUI_ARG_KEYS)}"
            )
        target = str(_field(step, "target", "") or "").strip()
        subtool = str(bound.get("subtool") or "").strip()
        if not subtool and target and target.lower() not in GUI_INLINE_TARGETS:
            subtool = target
        actions = bound.get("actions")
        if actions in (None, "", []):
            actions = ()
        elif not isinstance(actions, (list, tuple)) or not all(
            isinstance(item, Mapping) for item in actions
        ):
            raise RunnerRefused(
                f"step {_step_id(step)}: gui actions must be a list of declared action objects"
            )
        else:
            actions = tuple(dict(item) for item in actions)
        if subtool and actions:
            raise RunnerRefused(
                f"step {_step_id(step)}: a gui step declares both a sub-tool ({subtool!r}) and "
                "an inline action list — two binding paths, one of them silently ignored"
            )
        if not subtool and not actions:
            raise RunnerRefused(
                f"step {_step_id(step)}: a gui step needs either a stored sub-tool (the step's "
                f"`target`, or a bound `subtool`) or an inline flow (a bound `actions` list plus "
                f"`end_state`) — target is {target!r}"
            )
        end_state = bound.get("end_state")
        if actions:
            if not isinstance(end_state, Mapping):
                raise RunnerRefused(
                    f"step {_step_id(step)}: an inline gui flow needs an `end_state` object — the "
                    "end state is read from the real display and a flow without one cannot be "
                    "verified (act_service rule)"
                )
            expected = end_state.get("expect_observe_contains") or end_state.get(
                "expect_state_contains"
            )
            if not expected:
                raise RunnerRefused(
                    f"step {_step_id(step)}: end_state declares no expectation — set "
                    "expect_observe_contains (the end state is read from the real state, never "
                    "from the action's stdout)"
                )
        runner_kind = str(bound.get("runner_kind") or "auto").strip().lower()
        if runner_kind not in GUI_RUNNER_KINDS:
            raise RunnerRefused(
                f"step {_step_id(step)}: runner_kind {runner_kind!r} is not one of "
                f"{list(GUI_RUNNER_KINDS)}"
            )
        try:
            timeout = float(bound.get("timeout_s") or _field(step, "timeout_s", 90.0) or 90.0)
        except (TypeError, ValueError) as exc:
            raise RunnerRefused(f"step {_step_id(step)}: timeout_s is not a number: {exc}") from exc
        if timeout <= 0:
            raise RunnerRefused(f"step {_step_id(step)}: timeout_s must be positive ({timeout})")
        return GuiStepDecl(
            target=target,
            subtool=subtool,
            actions=actions,
            end_state=end_state,
            runner_kind=runner_kind,
            state=bound.get("state") or None,
            app_version=str(bound.get("app_version") or ""),
            locale=str(bound.get("locale") or ""),
            goal=str(bound.get("goal") or _field(step, "note", "") or ""),
            session_id=str(bound.get("session_id") or "compose-gui"),
            timeout_s=timeout,
            write_record=bool(bound.get("write_record", True)),
            screenshot=bool(bound.get("screenshot", True)),
        )


def available_bridges() -> dict:
    """This host's honest ACT bridge status (a read-only view of ``act_service``)."""
    try:
        from . import act_service
    except Exception as exc:  # noqa: BLE001 — an unimportable ACT layer is a real gap
        return {
            "available": [],
            "detail": f"the ACT service could not be imported: {type(exc).__name__}: {exc}",
        }
    try:
        report = act_service.capability_report()
    except Exception as exc:  # noqa: BLE001 — a broken report must not become a pass
        return {
            "available": [],
            "detail": f"the capability report failed: {type(exc).__name__}: {exc}",
        }
    bridges = [row for row in report.get("bridges", []) if row.get("available")]
    return {
        "available": [str(row.get("id", "")) for row in bridges if not row.get("fixture")],
        "fixtures": [str(row.get("id", "")) for row in bridges if row.get("fixture")],
        "verdict": str(report.get("verdict", "")),
    }


def _missing_gui_capability(decl: GuiStepDecl, *, resolution: Mapping[str, Any]) -> str:
    """Name the precise piece this host is missing — the ACT layer's own wording if possible."""
    kinds = [
        str(item.get("kind", "") or "")
        for item in decl.actions
        if isinstance(item, Mapping)
    ]
    report: Mapping[str, Any] = {}
    try:
        from . import act_service

        report = act_service.capability_report()
    except Exception:  # noqa: BLE001 — a fallback sentence is honest enough, a crash is not
        report = {}
    records = {str(row.get("name", "")): row for row in report.get("capabilities", [])}
    for kind in kinds or ["window", "mouse", "type_text"]:
        row = records.get(_GUI_KIND_CAPABILITY.get(kind, ""))
        if row and not row.get("available"):
            gap = str(row.get("missing_capability") or "")
            if not gap:
                missing = row.get("missing") or []
                gap = str(missing[0]) if missing else str(row.get("reason", ""))
            if gap:
                return f"the {kind!r} action needs {gap}"
    reason = str(resolution.get("reason", "") or "")
    if reason:
        return reason
    return (
        "an available isolation bridge: the container GUI image, the bwrap sandbox, or a live "
        "desktop bridge (which the policy refuses)"
    )


def _gui_payload(
    decl: GuiStepDecl,
    *,
    ok: bool,
    status: str,
    reason: str,
    isolation: str = "",
    bridge: str = "",
    live: bool = False,
    scope: str = "",
    executed: int = 0,
    actions: Sequence[Any] | None = None,
    observation: Any = None,
    end_state: Any = None,
    missing_capability: str = "",
    failure_class: str = "",
    exit_code: int = EXIT_OK,
    screenshot: Any = None,
    cleanup: Any = None,
) -> dict:
    """Every ``GUI_OUTPUT_KEYS`` key, always — a declared output may be any subset."""
    return {
        "ok": bool(ok),
        "status": str(status),
        "verified": bool(status == "verified" and ok),
        "exit_code": int(exit_code),
        "isolation": str(isolation),
        "bridge": str(bridge),
        "live": bool(live),
        "scope": str(scope),
        "reason": str(reason),
        "executed": int(executed),
        "actions": [
            dict(item) if isinstance(item, Mapping) else item for item in (actions or [])
        ],
        "observation": str(observation or "")[:600],
        "end_state": end_state if isinstance(end_state, Mapping) else {},
        "subtool": decl.subtool,
        "missing_capability": str(missing_capability),
        "failure_class": str(failure_class),
        "screenshot": dict(screenshot) if isinstance(screenshot, Mapping) else {},
        "cleanup": dict(cleanup) if isinstance(cleanup, Mapping) else {},
    }


def run_gui_step(
    step: Any,
    arguments: Mapping[str, Any] | None = None,
    *,
    context: Mapping[str, Any] | None = None,
    resolve: Callable[[str], Any] | None = None,
    runtime: str | Path | None = None,
    act_runner: Any = None,
    allow_fixture: bool = False,
) -> StepOutcome:
    """Execute one declared GUI step through ``skaldir.act_service`` (never a pretend run).

    ``act_runner`` is a ready runner object the **caller owns** (a started
    ``ContainerRunner``, or a scripted fixture in a test). Without one, the runner is
    resolved by ``act_service.resolve_runner`` and closed here when the run is over, so a
    composite does not leak a container per step. ``allow_fixture`` is the explicit opt-in
    for the in-process ``dry-run`` runner: without it such a resolution is **refused**
    (exit 3), and with it the result stays labelled ``live: false`` /
    ``scope: in-process fixture``. A composite document can never turn it on (it is a call
    parameter, not a bindable field), so a step cannot fake a GUI run through its own
    declaration.
    """
    started = time.time()
    try:
        decl = GuiStepDecl.from_step(step, arguments)
        # A ``$ref`` inside a declared action (or its end state) is substituted from the
        # composite context — and refused when there is none, so an unbound value can never
        # reach the display as the literal text ``$steps.…``.
        actions = list(_substitute_refs_deep(decl.actions, context=context, resolve=resolve))
        end_state = _substitute_refs_deep(decl.end_state, context=context, resolve=resolve)
    except RunnerRefused as exc:
        return _finish_gui(
            StepOutcome.failure(
                KIND_GUI,
                str(exc),
                failure_class=FAILURE_USAGE,
                exit_code=EXIT_USAGE,
                outputs=_gui_payload(
                    GuiStepDecl(target=str(_field(step, "target", ""))),
                    ok=False,
                    status="usage",
                    reason=str(exc),
                    failure_class=FAILURE_USAGE,
                    exit_code=EXIT_USAGE,
                ),
            ),
            started,
        )

    try:
        from . import act_service
    except Exception as exc:  # noqa: BLE001 — an unimportable ACT layer is the missing piece
        return _finish_gui(
            StepOutcome.block(
                KIND_GUI,
                f"the Computer-Use service could not be imported: {type(exc).__name__}: {exc}",
                missing_kind=KIND_GUI,
                outputs=_gui_payload(
                    decl, ok=False, status="blocked", reason=str(exc), missing_capability=str(exc)
                ),
            ),
            started,
        )

    owned_runner = act_runner is None
    resolution: dict = {}
    runner = act_runner
    if runner is None:
        runner, resolution = act_service.resolve_runner(
            decl.runner_kind, workdir=runtime, timeout=decl.timeout_s
        )
    else:
        resolution = {
            "requested": decl.runner_kind,
            "bridge": str(getattr(runner, "isolation", "")),
            "resolved": True,
            "isolation": str(getattr(runner, "isolation", "")),
            "real": str(getattr(runner, "isolation", "")) != "dry-run",
            "fixture": str(getattr(runner, "isolation", "")) == "dry-run",
            "scope": (
                "in-process fixture"
                if str(getattr(runner, "isolation", "")) == "dry-run"
                else "real"
            ),
            "reason": "a runner was supplied by the caller",
        }

    if runner is None:
        gap = _missing_gui_capability(decl, resolution=resolution)
        detail = (
            "no GUI bridge can run this step on this host: "
            f"{resolution.get('reason', '')} — missing capability: {gap}"
        )
        return _finish_gui(
            StepOutcome.block(
                KIND_GUI,
                detail,
                missing_kind=KIND_GUI,
                outputs=_gui_payload(
                    decl,
                    ok=False,
                    status="blocked",
                    reason=detail,
                    missing_capability=gap,
                    failure_class=FAILURE_BRIDGE,
                    exit_code=EXIT_BLOCKED,
                ),
                raw={"resolution": resolution},
            ),
            started,
        )

    if bool(resolution.get("fixture")) and not allow_fixture:
        detail = (
            "the resolved runner is the in-process fixture (dry-run): it drives no display and "
            "proves nothing about this host, so a composite step may not claim a GUI run with it "
            "— pass allow_fixture=True only where a fixture is what you mean, or give this host a "
            "real bridge"
        )
        if owned_runner:
            _close_runner(runner)
        return _finish_gui(
            StepOutcome.block(
                KIND_GUI,
                detail,
                missing_kind=KIND_GUI,
                outputs=_gui_payload(
                    decl,
                    ok=False,
                    status="blocked",
                    reason=detail,
                    isolation=str(resolution.get("isolation", "")),
                    bridge=str(resolution.get("bridge", "")),
                    scope=str(resolution.get("scope", "")),
                    missing_capability=(
                        "a real isolation bridge (the container GUI image or the bwrap sandbox); "
                        "the requested runner was the in-process fixture"
                    ),
                    failure_class=FAILURE_POLICY,
                    exit_code=EXIT_BLOCKED,
                ),
                raw={"resolution": resolution},
                failure_class=FAILURE_POLICY,
            ),
            started,
        )

    try:
        if decl.inline:
            record = act_service.run_action_list(
                actions,
                runner=runner,
                workdir=runtime,
                end_state=end_state,
                goal=decl.goal,
                runtime=runtime,
                timeout=decl.timeout_s,
                session_id=decl.session_id,
                write_record=decl.write_record,
                screenshot=decl.screenshot,
                screenshot_dir=(
                    (Path(runtime) / "compose-screenshots")
                    if runtime not in (None, "")
                    else None
                ),
            )
        else:
            record = act_service.run_subtool(
                decl.subtool,
                state=decl.state,
                runner=runner,
                workdir=runtime,
                app_version=decl.app_version,
                locale=decl.locale or "en",
                runtime=runtime,
                timeout=decl.timeout_s,
                write_record=decl.write_record,
            )
    except RunnerRefused:
        raise
    except Exception as exc:  # noqa: BLE001 — a crashing bridge is a failed step, not a pass
        detail = f"the ACT run failed: {type(exc).__name__}: {exc}"
        return _finish_gui(
            StepOutcome.failure(
                KIND_GUI,
                detail,
                failure_class=FAILURE_RUNNER,
                outputs=_gui_payload(
                    decl,
                    ok=False,
                    status="failed",
                    reason=detail,
                    isolation=str(resolution.get("isolation", "")),
                    bridge=str(resolution.get("bridge", "")),
                    scope=str(resolution.get("scope", "")),
                    failure_class=FAILURE_RUNNER,
                ),
                raw={"resolution": resolution},
            ),
            started,
        )
    finally:
        if owned_runner:
            _close_runner(runner)

    if owned_runner and isinstance(record, Mapping):
        # The runner is closed by now, so the teardown is re-read **after** the close:
        # reporting the state from before it would claim a leak that did not happen.
        record = dict(record)
        cleanup = dict(_cleanup_record(runner))
        cleanup.update(
            {
                "owned": True,
                "detail": "closed by the composite step that resolved it",
            }
        )
        record["cleanup"] = cleanup

    return _finish_gui(
        _gui_outcome(decl, record, resolution, allow_fixture=allow_fixture), started
    )


def _cleanup_record(runner: Any) -> dict:
    """What a runner leaves behind — read from the runner, never assumed to be clean."""
    reader = getattr(runner, "cleanup_record", None)
    if callable(reader):
        try:
            return dict(reader())  # type: ignore[arg-type]
        except Exception as exc:  # noqa: BLE001 — an unknown runner still answers usefully
            return {"container": "", "prefix": "", "closed": False, "still_present": True,
                    "error": f"{type(exc).__name__}: {exc}"}
    return {"container": "", "prefix": "", "closed": False, "still_present": True,
            "detail": "the runner reports no teardown state"}


def _close_runner(runner: Any) -> None:
    closer = getattr(runner, "close", None)
    if callable(closer):
        try:
            closer()
        except Exception:  # noqa: BLE001 — a failed teardown must not rewrite the verdict
            pass


def _gui_outcome(
    decl: GuiStepDecl,
    record: Mapping[str, Any],
    resolution: Mapping[str, Any],
    *,
    allow_fixture: bool,
) -> StepOutcome:
    """Map one ``act_service`` run record onto a step outcome — no verdict is invented.

    A fixture run (``dry-run`` resolver) only counts as an ``ok`` step when the caller opted
    in with ``allow_fixture=True``; a composite never does, so an unverified GUI step can
    never be reported as verified.
    """
    status = str(record.get("status", "") or "")
    reason = str(record.get("reason", "") or "")
    isolation = str(record.get("isolation", "") or resolution.get("isolation", ""))
    bridge = str(resolution.get("bridge", ""))
    scope = str(record.get("scope", "") or resolution.get("scope", ""))
    live = bool(record.get("live", False))
    gap = str(record.get("missing_capability", "") or "")
    raw = dict(record)
    achieved = status == "verified" and (live or allow_fixture)
    payload = _gui_payload(
        decl,
        ok=achieved,
        status=status,
        reason=reason,
        isolation=isolation,
        bridge=bridge,
        live=live,
        scope=scope,
        executed=int(record.get("executed", 0) or 0),
        actions=record.get("actions") or [],
        observation=(record.get("observation") or {}).get("text", "")
        if isinstance(record.get("observation"), Mapping)
        else "",
        end_state=record.get("end_state") if isinstance(record.get("end_state"), Mapping) else {},
        missing_capability=gap,
        failure_class="",
        exit_code=int(record.get("exit_code", EXIT_INTERNAL) or 0),
        screenshot=(
            record.get("screenshot") if isinstance(record.get("screenshot"), Mapping) else {}
        ),
        cleanup=record.get("cleanup") if isinstance(record.get("cleanup"), Mapping) else {},
    )

    if status == "verified":
        if not live and not allow_fixture:
            detail = (
                "the ACT record says verified but the run was not live (scope "
                f"{scope or 'unknown'!r}) — a fixture is not a GUI run"
            )
            payload["ok"] = False
            payload["verified"] = False
            payload["failure_class"] = FAILURE_POLICY
            return StepOutcome.failure(
                KIND_GUI, detail, outputs=payload, raw=raw, failure_class=FAILURE_POLICY
            )
        where = (
            f"the real bridge ({isolation or bridge or 'isolation unknown'})"
            if live
            else "the in-process fixture (nothing about this host is proven)"
        )
        evidence = "end_state held"
        shot = payload.get("screenshot") or {}
        if shot.get("path"):
            evidence += (
                f"; screenshot {shot['path']} (sha256 {str(shot.get('sha256'))[:12]}, "
                f"named after {shot.get('label') or 'the observed window'})"
            )
        cleanup = payload.get("cleanup") or {}
        if cleanup.get("still_present"):
            # A GUI step that leaves its container behind is not a clean success: the step
            # ran, but the run says so out loud instead of leaking quietly.
            evidence += (
                f"; WARNING the runner was not cleaned up "
                f"({cleanup.get('container') or cleanup.get('display') or 'unknown'})"
            )
        elif cleanup:
            evidence += "; the runner cleaned up after itself"
        return StepOutcome.success(
            KIND_GUI, payload, raw,
            f"{payload['executed']} action(s) verified on {where}; {evidence}",
        )
    if status == "blocked":
        detail = f"{reason} ({gap})" if gap and gap not in reason else (reason or "blocked")
        payload["failure_class"] = FAILURE_BRIDGE
        return StepOutcome.block(
            KIND_GUI, detail, missing_kind=KIND_GUI, outputs=payload, raw=raw,
            failure_class=FAILURE_BRIDGE,
        )
    if status in ("usage", "not_found"):
        failure_class = FAILURE_USAGE if status == "usage" else FAILURE_NOT_FOUND
        payload["failure_class"] = failure_class
        return StepOutcome.failure(
            KIND_GUI,
            reason or status,
            outputs=payload,
            raw=raw,
            failure_class=failure_class,
            exit_code=EXIT_USAGE if status == "usage" else EXIT_NOT_FOUND,
        )
    payload["failure_class"] = FAILURE_VERIFICATION
    return StepOutcome.failure(
        KIND_GUI, reason or f"the run ended as {status!r}", outputs=payload, raw=raw,
        failure_class=FAILURE_VERIFICATION,
    )


def _finish_gui(outcome: StepOutcome, started: float) -> StepOutcome:
    outcome.duration_ms = int((time.time() - started) * 1000)
    return outcome


# --------------------------------------------------------------------------- #
# the registry                                                                   #
# --------------------------------------------------------------------------- #


@runtime_checkable
class StepRunner(Protocol):
    """One step kind's runner: what it is, whether it can run here, and how to run a step."""

    kind: str
    runner: str
    reason: str

    @property
    def implemented(self) -> bool: ...

    def available(self) -> tuple[bool, str]: ...

    def run(
        self, step: Any, arguments: Mapping[str, Any] | None = None, **options: Any
    ) -> StepOutcome: ...


@dataclass(frozen=True)
class ApiStepRunner:
    """The ``api`` kind: a declared HTTP step through httpx."""

    kind: str = KIND_API
    runner: str = (
        "skaldir.step_runners.run_api_step (httpx>=0.27; declared request + status/body "
        "assertions against the real response, declared retry policy)"
    )
    reason: str = ""
    owner: str = "skaldir.step_runners"

    @property
    def implemented(self) -> bool:
        return True

    def available(self) -> tuple[bool, str]:
        try:
            import httpx  # noqa: F401 — the probe is the import
        except Exception as exc:  # noqa: BLE001
            return False, (
                "the httpx dependency is missing (pyproject.toml: httpx>=0.27,<1): "
                f"{type(exc).__name__}: {exc}"
            )
        return True, "httpx is importable; HTTP steps run against the declared url"

    def run(
        self, step: Any, arguments: Mapping[str, Any] | None = None, **options: Any
    ) -> StepOutcome:
        return run_api_step(step, arguments, **options)


@dataclass(frozen=True)
class GuiStepRunner:
    """The ``gui`` kind: a declared GUI step delegated to the ACT service."""

    kind: str = KIND_GUI
    runner: str = (
        "skaldir.step_runners.run_gui_step -> skaldir.act_service.run_action_list / run_subtool "
        "(real isolated bridge: container GUI Xvfb :90 or the bwrap sandbox; end state read "
        "back from the display)"
    )
    reason: str = ""
    owner: str = "skaldir.step_runners"

    @property
    def implemented(self) -> bool:
        return True

    def available(self) -> tuple[bool, str]:
        bridges = available_bridges()
        names = [str(name) for name in bridges.get("available", [])]
        if names:
            return True, f"real bridge(s) available: {', '.join(names)}"
        return False, str(
            bridges.get("detail")
            or "no real Computer-Use bridge is available on this host (the container GUI image "
            "and the bwrap sandbox are both unusable)"
        )

    def run(
        self, step: Any, arguments: Mapping[str, Any] | None = None, **options: Any
    ) -> StepOutcome:
        return run_gui_step(step, arguments, **options)


@dataclass(frozen=True)
class DelegatedStepRunner:
    """A kind whose runner lives in ``compose_service`` (``tool`` / ``docker``).

    The registry points at the existing runners instead of re-implementing them: COMPOSE
    already owns the sandbox call and the container policy, and a second implementation
    would be a second contract.
    """

    kind: str
    runner: str
    helper: str
    owner: str = "skaldir.compose_service"
    reason: str = ""

    @property
    def implemented(self) -> bool:
        return True

    def available(self) -> tuple[bool, str]:
        return True, f"the runner lives in {self.owner} ({self.helper})"

    def run(
        self, step: Any, arguments: Mapping[str, Any] | None = None, **options: Any
    ) -> StepOutcome:
        if arguments is not None and dict(arguments):
            raise RunnerRefused(
                f"a {self.kind} step is executed by {self.owner}.{self.helper} and binds through "
                "its declaration (a docker step through its argv), not through arguments"
            )
        try:
            # The COMPOSE layer owns these two runners; the registry only points at them, so
            # a renamed helper is reported as a refusal instead of a silently skipped step.
            from .compose_service import (  # noqa: PLC2701 — the owner's own helpers
                _execute_docker_step,
                _execute_tool_step,
            )
        except Exception as exc:  # noqa: BLE001 — a renamed helper must not become a pass
            raise RunnerRefused(
                f"the {self.kind} runner ({self.owner}.{self.helper}) is not importable in this "
                f"build: {type(exc).__name__}: {exc}"
            ) from exc
        started = time.time()
        if self.kind == KIND_TOOL:
            store = options.get("store")
            if store is None:
                raise RunnerRefused(
                    "a tool step needs the tool store (`store=<ToolStore>`) that owns its target"
                )
            ok, outputs, raw, detail = _execute_tool_step(
                step,
                dict(options.get("arguments") or arguments or {}),
                store=store,
                runtime=options.get("runtime"),
            )
            outcome = (
                StepOutcome.success(self.kind, outputs, raw, detail)
                if ok
                else StepOutcome.failure(self.kind, detail, outputs=outputs, raw=raw)
            )
        else:
            ok, outputs, raw, detail, missing = _execute_docker_step(
                step,
                options.get("context") or {},
                runtime=options.get("runtime"),
                docker_bin=options.get("docker_bin"),
            )
            if missing:
                outcome = StepOutcome.block(self.kind, detail, missing_kind=missing, raw=raw)
            elif ok:
                outcome = StepOutcome.success(self.kind, outputs, raw, detail)
            else:
                outcome = StepOutcome.failure(self.kind, detail, outputs=outputs, raw=raw)
        outcome.duration_ms = int((time.time() - started) * 1000)
        return outcome


def _runner_registry() -> dict[str, Any]:
    """Build the registry fresh (lazy imports only: ``compose_service`` imports this module)."""
    docker = DelegatedStepRunner(
        kind=KIND_DOCKER,
        runner=(
            "skaldir.docker_runtime.DockerRuntimeManager.run (one-shot `--rm`, owner-labelled, "
            "`--network none` unless the step asks otherwise)"
        ),
        helper="_execute_docker_step",
    )
    tool = DelegatedStepRunner(
        kind=KIND_TOOL,
        runner=(
            "skaldir.mcpserver._wrapper_for (approved python spec; generated tools run in the "
            "bwrap sandbox, usage recorded in the tool-usage ledger)"
        ),
        helper="_execute_tool_step",
    )
    return {
        KIND_TOOL: tool,
        KIND_DOCKER: docker,
        KIND_API: ApiStepRunner(),
        KIND_GUI: GuiStepRunner(),
    }


def runner_registry() -> dict[str, Any]:
    """Every step kind mapped to its runner — exactly ``RUNNER_KINDS``, nothing else."""
    return _runner_registry()


def runner_for(kind: str) -> Any:
    """The runner of one step kind, or ``None`` for an unknown kind."""
    return runner_registry().get(str(kind or "").strip().lower())


def registry_report() -> dict[str, dict]:
    """The registry as data — the same shape ``compose_service.executor_report`` uses.

    ``{kind: {kind, executable, runner, reason, owner}}``: ``executable`` is true exactly
    when a runner will really execute the kind here, and ``reason`` is empty then (a kind
    that cannot run names the precise missing piece instead).
    """
    report: dict[str, dict] = {}
    for kind in RUNNER_KINDS:
        runner = runner_for(kind)
        if runner is None:  # pragma: no cover — RUNNER_KINDS and the map are built together
            report[kind] = {
                "kind": kind,
                "executable": False,
                "runner": "",
                "reason": f"no runner is registered for {kind!r}",
                "owner": "",
            }
            continue
        report[kind] = {
            "kind": kind,
            "executable": bool(runner.implemented),
            "runner": str(runner.runner),
            "reason": "" if runner.implemented else str(runner.reason),
            "owner": str(getattr(runner, "owner", "")),
        }
    return report


def output_keys_for(kind: str) -> tuple[str, ...]:
    """The result keys a step of this kind always carries (empty for the delegated kinds)."""
    if str(kind or "").strip().lower() == KIND_API:
        return API_OUTPUT_KEYS
    if str(kind or "").strip().lower() == KIND_GUI:
        return GUI_OUTPUT_KEYS
    return ()


def run_step(
    kind: str,
    step: Any,
    arguments: Mapping[str, Any] | None = None,
    **options: Any,
) -> StepOutcome:
    """Run one step of any kind through its registered runner (the one dispatch entry point).

    Extra ``options`` go to the runner: ``context=``/``runtime=`` for every kind, plus
    ``client=``/``transport=``/``sleep=`` for api, ``act_runner=``/``allow_fixture=`` for gui
    and ``store=``/``docker_bin=`` for the two delegated kinds.
    """
    runner = runner_for(kind)
    if runner is None:
        return StepOutcome.block(
            str(kind),
            f"unknown step kind {kind!r} — known: {list(RUNNER_KINDS)}",
            missing_kind=str(kind),
            failure_class=FAILURE_USAGE,
        )
    return runner.run(step, arguments, **options)


def environment_facts() -> dict:
    """What this host can really run, per kind — evidence for a caller's own report."""
    facts: dict[str, Any] = {"kinds": {}, "bridges": available_bridges()}
    for kind in RUNNER_KINDS:
        runner = runner_for(kind)
        if runner is None:  # pragma: no cover
            continue
        ok, detail = runner.available()
        facts["kinds"][kind] = {"available": bool(ok), "detail": detail, "runner": runner.runner}
    facts["httpx"] = _httpx_version()
    return facts


def _httpx_version() -> str:
    try:
        import httpx

        return str(getattr(httpx, "__version__", "") or "")
    except Exception:  # noqa: BLE001
        return ""
