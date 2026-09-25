"""Schema-constrained structured output with honest validation (typesafe provider).

``complete()`` returns free text and ``chat()`` returns text plus tool calls; a
*typed* decision (Jev routing, a verification verdict, a plan, a tool call the
model asks for) needs the answer to satisfy a **JSON Schema before anything is
allowed to use it**. This module is the whole contract for that:

.. code-block:: python

    from skaldir.structured import structured_call

    outcome = structured_call(
        client, schema, messages,
        tier="small", name="route_decision", response_model=RouteDecision,
    )
    if not outcome.ok:                      # reported, never coerced
        print(outcome.raw, outcome.validation_error)
    else:
        decision = outcome.model            # a typed pydantic instance

What is guaranteed, and what is not
-----------------------------------

* **The request is constrained when the endpoint supports it.** ``mode="auto"``
  (the default) first asks for the native form
  (``response_format: {"type": "json_schema", …}``, see
  :func:`skaldir.llm_provider.json_schema_response_format`), then for
  ``{"type": "json_object"}``, then falls back to a *prompted* JSON answer that
  is validated locally. Which mode answered — and why each earlier mode was
  skipped — is part of the result (:attr:`StructuredOutcome.mode_trail`), so a
  run can say truthfully what happened. Nothing is silently downgraded.
* **The answer is validated, always, locally.** The endpoint's promise is not a
  measurement: every answer is parsed and validated against the schema with
  :func:`validate_payload` (the real ``jsonschema`` package when it is
  importable, plus a built-in strict checker; the report names the backend that
  ran). A boolean is not an integer, ``"5"`` is not ``5``, and a missing
  required property is an error — **no value is ever coerced**.
* **A failing answer is reported, not repaired into shape.** The outcome
  carries the *raw text the provider returned* (:attr:`StructuredOutcome.raw`),
  the validation error, and every attempt in :attr:`StructuredOutcome.steps`.
  ``ok`` stays ``False``.
* **Repair means "ask again", never "fix it up".** With
  ``repair_attempts > 0`` a failed validation is fed back to the model together
  with its own raw answer, and the new answer is validated from scratch. The
  only local touch is *extraction* — a JSON value wrapped in a code fence or in
  prose is unwrapped, and the note says exactly what was done
  (``extraction``/``notes``), with the raw text still kept.
* **No provider, no answer** ⇒ ``ok=False`` with the error named; a refusal of
  ``response_format`` is reported as the capability fault it is
  (:class:`skaldir.llm_provider.StructuredOutputNotSupportedError`).

pydantic is used when a caller hands one in (``response_model=`` or a model
class as ``schema``): the answer is validated against the model's JSON Schema
first and *then* constructed with ``model_validate(..., strict=True)``, so a
field whose JSON Schema type is looser than the Python annotation is reported
rather than coerced (``strict_model=False`` opts into pydantic's own lax
construction, and the report always names which was used). pydantic itself is a
runtime dependency; ``jsonschema`` is **not** (it may arrive through a dev
extra), which is why the built-in validator exists and why the backend is always
reported.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx

from . import llm_provider as lp
from .llm import LLMError

# --------------------------------------------------------------------------- #
# modes                                                                        #
# --------------------------------------------------------------------------- #

#: The endpoint constrains the answer (``response_format: json_schema``).
MODE_NATIVE = "native"
#: The endpoint only promises JSON (``response_format: json_object``).
MODE_JSON_OBJECT = "json_object"
#: Nothing goes on the wire except a prompt; the answer is validated locally.
MODE_PROMPTED = "prompted"
#: Try native, then json_object, then prompted — record why each was left.
MODE_AUTO = "auto"

#: The modes that can answer, in the documented order (:data:`MODE_AUTO` uses it).
MODE_SEQUENCE: tuple[str, ...] = (MODE_NATIVE, MODE_JSON_OBJECT, MODE_PROMPTED)
MODES: tuple[str, ...] = (MODE_AUTO, *MODE_SEQUENCE)

#: Why a mode did not answer, or why an attempt failed. These strings are the
#: vocabulary a fallback chain reports; they are never invented per call site.
REASON_ANSWERED = "answered"
REASON_UNSUPPORTED = "unsupported_feature"
REASON_INVALID = "validation_failed"
REASON_NOT_JSON = "not_json"
REASON_EMPTY = "empty_answer"
REASON_BUDGET = "attempt_budget_exhausted"
REASON_TOOL_CALL = "no_text_answer"
REASON_RATE_LIMIT = "rate_limit"
REASON_TIMEOUT = "timeout"
REASON_AUTH = "auth_failed"
REASON_PROVIDER_ERROR = "provider_error"

DEFAULT_SCHEMA_NAME = "response"
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_REPAIR_ATTEMPTS = 1

#: Appended to the messages in :data:`MODE_PROMPTED` (and on a repair attempt).
JSON_INSTRUCTION = (
    "Answer with a single JSON value that satisfies this JSON Schema, and nothing "
    "else — no prose, no markdown, no code fence:\n{schema}"
)

REPAIR_INSTRUCTION = (
    "Your previous answer did not satisfy the schema. Validation error: {error}\n"
    "Previous answer (raw):\n{raw}\n"
    "Reply with the corrected JSON value only."
)


# --------------------------------------------------------------------------- #
# errors                                                                       #
# --------------------------------------------------------------------------- #


class StructuredError(LLMError):
    """A schema-constrained call could not produce a valid typed answer."""


class StructuredConfigError(StructuredError):
    """The request itself is wrong (no schema, no messages, a bad mode)."""


class StructuredValidationError(StructuredError):
    """The provider answered, and the answer did not satisfy the schema.

    Carries the evidence rather than a paraphrase: the raw text, the validation
    error list and the full attempt trail. Raising this is how a caller asks for
    an exception; :func:`structured_call` itself *returns* a failed
    :class:`StructuredOutcome` instead, so a caller that wants to handle the
    failure locally never has to catch anything.
    """

    def __init__(
        self,
        message: str,
        *,
        raw: str = "",
        validation_error: str = "",
        mode: str = "",
        attempts: int = 0,
        steps: Sequence[Mapping[str, Any]] = (),
    ) -> None:
        super().__init__(message)
        self.raw = str(raw)
        self.validation_error = str(validation_error)
        self.mode = str(mode)
        self.attempts = int(attempts)
        self.steps = [dict(step) for step in steps]


# --------------------------------------------------------------------------- #
# pydantic (optional at the call site, a dependency of the package)            #
# --------------------------------------------------------------------------- #


def _pydantic() -> Any:
    """The pydantic module, or ``None`` — never a hard import at module level."""
    try:
        import pydantic  # noqa: PLC0415 — optional at import time, reported if absent
    except ImportError:  # pragma: no cover - pydantic is a declared dependency
        return None
    return pydantic


def is_pydantic_model(value: Any) -> bool:
    """True for a pydantic model class **or** instance (``BaseModel``)."""
    pydantic = _pydantic()
    if pydantic is None:
        return False
    cls = value if isinstance(value, type) else type(value)
    try:
        return issubclass(cls, pydantic.BaseModel)
    except TypeError:  # pragma: no cover - defensive
        return False


def schema_from(model: Any, *, name: str = "") -> tuple[dict[str, Any], str, Any]:
    """``(json schema, schema name, response model class)`` from what a caller passed.

    Accepts a plain JSON Schema mapping (returned unchanged, ``response_model``
    ``None``) or a pydantic model class/instance (its ``model_json_schema()``,
    which is also returned as the response model). Anything else is a config
    error: a schema-constrained call has no useful default.
    """
    if isinstance(model, Mapping):
        schema = {str(key): value for key, value in model.items()}
        if not schema:
            raise StructuredConfigError("the JSON schema is empty")
        if not isinstance(schema.get("type"), (str, list)) and "properties" not in schema:
            raise StructuredConfigError(
                "the JSON schema declares neither a 'type' nor 'properties' — it "
                "would constrain nothing"
            )
        return schema, lp.schema_name_for(name or schema.get("title") or DEFAULT_SCHEMA_NAME), None
    if is_pydantic_model(model):
        cls = model if isinstance(model, type) else type(model)
        builder = getattr(cls, "model_json_schema", None)
        if builder is None:  # pragma: no cover - pydantic v1 shape
            raise StructuredConfigError(
                f"{cls.__name__} has no model_json_schema() — pydantic v2 is required"
            )
        schema = builder()
        return schema, lp.schema_name_for(name or cls.__name__), cls
    raise StructuredConfigError(
        "a schema-constrained call needs a JSON schema mapping or a pydantic model, "
        f"got {type(model).__name__}"
    )


def strict_compatible_schema(schema: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Make a schema fit OpenAI/DeepSeek **strict** mode; report every change.

    Strict mode requires ``additionalProperties: false`` and every property in
    ``required``. A property that was optional is answered by making it required
    **and nullable** (``anyOf: [<type>, {"type": "null"}]``), which keeps the
    caller's ability to omit it while satisfying the endpoint. The returned
    notes name each property that changed, so the relaxation is never silent
    (the caller decides whether it is acceptable).
    """
    notes: list[str] = []

    def walk(node: Any, path: str) -> Any:
        if isinstance(node, list):
            return [walk(item, f"{path}[]") for item in node]
        if not isinstance(node, Mapping):
            return node
        out = {str(k): walk(v, f"{path}.{k}") for k, v in node.items()}
        node_type = node.get("type")
        properties = out.get("properties")
        if node_type == "object" or isinstance(properties, Mapping):
            if isinstance(properties, Mapping):
                for key in properties:
                    if node.get("required") is None or key not in (node.get("required") or []):
                        child = out["properties"][key]
                        if isinstance(child, Mapping) and "anyOf" not in child:
                            types = child.get("type")
                            if isinstance(types, str):
                                out["properties"][key] = {
                                    "anyOf": [child, {"type": "null"}],
                                }
                                notes.append(
                                    f"{path}.{key} was optional → required+nullable "
                                    f"(strict mode needs every property in 'required')"
                                )
                            else:
                                notes.append(
                                    f"{path}.{key} was optional and could not be made "
                                    f"nullable automatically — check it before sending"
                                )
                out["required"] = list(properties.keys())
            if node.get("additionalProperties") is None:
                out["additionalProperties"] = False
                notes.append(f"{path} gained additionalProperties=false (strict mode)")
        return out

    return walk(schema, "$"), notes


# --------------------------------------------------------------------------- #
# validation — the real jsonschema package when present, plus a strict checker  #
# --------------------------------------------------------------------------- #

_TYPE_NAMES = ("object", "array", "string", "integer", "number", "boolean", "null")


def _json_type_of(value: Any) -> str:
    """The JSON type of a decoded value (``bool`` is never ``integer``/``number``)."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "integer" if value.is_integer() else "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, Mapping):
        return "object"
    return type(value).__name__


def _type_matches(value: Any, expected: str) -> bool:
    actual = _json_type_of(value)
    if expected == "number":
        return actual in ("number", "integer")
    return actual == expected


def jsonschema_backend() -> str:
    """``"jsonschema"`` when the real package is importable, else ``""``.

    ``jsonschema`` is *not* a declared dependency of the package (it arrives
    through a dev extra), so it must never be imported at module level — and the
    report must never claim it ran when it did not.
    """
    try:
        import jsonschema  # noqa: PLC0415,F401 — deliberately optional
    except ImportError:
        return ""
    try:
        from importlib.metadata import version  # noqa: PLC0415

        return f"jsonschema {version('jsonschema')}"
    except Exception:  # noqa: BLE001 — a missing version is cosmetic, the import is the fact
        return "jsonschema"


def builtin_validate(schema: Any, payload: Any, *, path: str = "$") -> list[str]:
    """A strict JSON Schema checker for the drafts we actually send.

    Covers ``type`` (incl. a list of types), ``required``, ``properties``,
    ``additionalProperties``/``patternProperties`` (presence only), ``items``,
    ``enum``, ``const``, ``anyOf``/``oneOf``/``allOf``/``not``, numeric bounds,
    string length/pattern, array length/uniqueItems and object min/maxProperties.

    Deliberately stricter than the spec in the one place that matters here: a
    boolean is never accepted as an integer/number, and a string is never
    accepted as a number. That is exactly the coercion this module refuses to do
    silently — ``jsonschema`` alone would accept ``5.0`` for ``integer``, so
    both checkers run and the report names both.

    ``format`` is **not** checked (no format checker is installed) and is
    reported as a note by :func:`validate_payload`.
    """
    errors: list[str] = []
    if not isinstance(schema, Mapping):
        return errors
    types = schema.get("type")
    if types is not None:
        candidates = [types] if isinstance(types, str) else list(types)
        if candidates and not any(_type_matches(payload, str(item)) for item in candidates):
            errors.append(
                f"{path}: expected {'/'.join(str(item) for item in candidates)}, "
                f"got {_json_type_of(payload)}"
            )
            return errors  # further keywords would only add noise
    if "const" in schema and payload != schema["const"]:
        errors.append(f"{path}: expected const {schema['const']!r}, got {payload!r}")
    enum = schema.get("enum")
    if isinstance(enum, list) and payload not in enum:
        errors.append(f"{path}: {payload!r} is not one of {enum!r}")
    if isinstance(payload, Mapping):
        properties = schema.get("properties")
        properties = properties if isinstance(properties, Mapping) else {}
        required = schema.get("required")
        for key in required if isinstance(required, list) else []:
            if key not in payload:
                errors.append(f"{path}: missing required property {key!r}")
        extra = schema.get("additionalProperties")
        for key, value in payload.items():
            if key in properties:
                errors.extend(builtin_validate(properties[key], value, path=f"{path}.{key}"))
                continue
            if extra is False:
                errors.append(
                    f"{path}: unexpected property {key!r} "
                    f"(additionalProperties is false; allowed: {sorted(properties)})"
                )
            elif isinstance(extra, Mapping):
                errors.extend(builtin_validate(extra, value, path=f"{path}.{key}"))
        minimum = schema.get("minProperties")
        if isinstance(minimum, int) and len(payload) < minimum:
            errors.append(f"{path}: needs at least {minimum} propert(ies), got {len(payload)}")
        maximum = schema.get("maxProperties")
        if isinstance(maximum, int) and len(payload) > maximum:
            errors.append(f"{path}: allows at most {maximum} propert(ies), got {len(payload)}")
    if isinstance(payload, list):
        items = schema.get("items")
        if isinstance(items, Mapping):
            for index, item in enumerate(payload):
                errors.extend(builtin_validate(items, item, path=f"{path}[{index}]"))
        elif isinstance(items, list):
            for index, item in enumerate(payload):
                if index < len(items):
                    errors.extend(builtin_validate(items[index], item, path=f"{path}[{index}]"))
        minimum = schema.get("minItems")
        if isinstance(minimum, int) and len(payload) < minimum:
            errors.append(f"{path}: needs at least {minimum} item(s), got {len(payload)}")
        maximum = schema.get("maxItems")
        if isinstance(maximum, int) and len(payload) > maximum:
            errors.append(f"{path}: allows at most {maximum} item(s), got {len(payload)}")
        if schema.get("uniqueItems") is True:
            seen: list[str] = []
            for item in payload:
                rendered = json.dumps(item, sort_keys=True, default=str)
                if rendered in seen:
                    errors.append(f"{path}: duplicate item {rendered}")
                seen.append(rendered)
    if isinstance(payload, str):
        minimum = schema.get("minLength")
        if isinstance(minimum, int) and len(payload) < minimum:
            errors.append(f"{path}: needs at least {minimum} character(s), got {len(payload)}")
        maximum = schema.get("maxLength")
        if isinstance(maximum, int) and len(payload) > maximum:
            errors.append(f"{path}: allows at most {maximum} character(s), got {len(payload)}")
        pattern = schema.get("pattern")
        if isinstance(pattern, str) and not re.search(pattern, payload):
            errors.append(f"{path}: {payload!r} does not match {pattern!r}")
    if isinstance(payload, (int, float)) and not isinstance(payload, bool):
        for keyword, compare in (
            ("minimum", lambda a, b: a >= b),
            ("maximum", lambda a, b: a <= b),
            ("exclusiveMinimum", lambda a, b: a > b),
            ("exclusiveMaximum", lambda a, b: a < b),
        ):
            bound = schema.get(keyword)
            if isinstance(bound, (int, float)) and not isinstance(bound, bool):
                if not compare(payload, bound):
                    errors.append(f"{path}: {payload} violates {keyword}={bound}")
    alternatives = schema.get("anyOf")
    if isinstance(alternatives, list) and alternatives:
        if not any(not builtin_validate(option, payload, path=path) for option in alternatives):
            errors.append(f"{path}: matches none of the {len(alternatives)} anyOf branches")
    alternatives = schema.get("oneOf")
    if isinstance(alternatives, list) and alternatives:
        matches = [
            option
            for option in alternatives
            if not builtin_validate(option, payload, path=path)
        ]
        if len(matches) != 1:
            errors.append(f"{path}: matches {len(matches)} oneOf branches (exactly 1 required)")
    alternatives = schema.get("allOf")
    if isinstance(alternatives, list):
        for option in alternatives:
            errors.extend(builtin_validate(option, payload, path=path))
    negated = schema.get("not")
    if isinstance(negated, Mapping) and not builtin_validate(negated, payload, path=path):
        errors.append(f"{path}: matches the 'not' schema")
    return errors


def validate_payload(
    schema: Mapping[str, Any],
    payload: Any,
    *,
    backend: str = "auto",
) -> tuple[list[str], str, list[str]]:
    """Validate ``payload`` against ``schema`` → ``(errors, backend, notes)``.

    Both checkers are used when both are available: the real ``jsonschema``
    package (authoritative) and :func:`builtin_validate` (strict types). Their
    messages are merged and de-duplicated, and ``backend`` names exactly what
    ran — a report must never claim ``jsonschema`` when only the built-in
    checker ran. ``backend`` is ``"auto"`` (use what is there), ``"jsonschema"``
    (require the package) or ``"builtin"`` (never import it).
    """
    requested = str(backend or "auto").strip().lower()
    if requested not in ("auto", "jsonschema", "builtin"):
        raise StructuredConfigError(
            f"unknown validator backend {backend!r} (expected auto|jsonschema|builtin)"
        )
    errors = builtin_validate(schema, payload)
    used = ["builtin"]
    notes: list[str] = []
    available = jsonschema_backend()
    if requested == "jsonschema" and not available:
        raise StructuredConfigError(
            "validator backend 'jsonschema' was requested but the package is not "
            "importable — it is not a dependency of this package; use backend='auto'"
        )
    if requested in ("auto", "jsonschema") and available:
        from jsonschema import validators as jsonschema_validators  # noqa: PLC0415

        validator = jsonschema_validators.validator_for(dict(schema))
        validator.check_schema(dict(schema))
        for error in validator(dict(schema)).iter_errors(payload):
            location = "$" + "".join(
                f"[{part}]" if isinstance(part, int) else f".{part}" for part in error.absolute_path
            )
            errors.append(f"{location}: {error.message} ({list(error.schema_path)})")
        used.insert(0, "jsonschema")
        if _has_keyword(schema, "format"):
            notes.append("'format' keywords are declared but not checked (no format checker)")
    return _dedupe(errors), "+".join(used), notes


def _has_keyword(node: Any, keyword: str) -> bool:
    """True when ``keyword`` appears as a *schema key* anywhere in the tree."""
    if isinstance(node, Mapping):
        if keyword in node:
            return True
        return any(_has_keyword(value, keyword) for value in node.values())
    if isinstance(node, list):
        return any(_has_keyword(item, keyword) for item in node)
    return False


def _dedupe(items: Sequence[str]) -> list[str]:
    seen: list[str] = []
    for item in items:
        if item not in seen:
            seen.append(item)
    return seen


# --------------------------------------------------------------------------- #
# reading an answer                                                            #
# --------------------------------------------------------------------------- #


class _Balanced:
    """Cursor helpers for pulling the first balanced ``{…}``/``[…]`` out of text."""

    __slots__ = ("_positions",)

    def __init__(self, text: str) -> None:
        self._positions = text

    def extract(self, opener: str) -> tuple[str, int, int] | None:
        closer = "}" if opener == "{" else "]"
        start = self._positions.find(opener)
        while start != -1:
            depth = 0
            in_string = False
            escaped = False
            for index in range(start, len(self._positions)):
                char = self._positions[index]
                if in_string:
                    if escaped:
                        escaped = False
                    elif char == "\\":
                        escaped = True
                    elif char == '"':
                        in_string = False
                    continue
                if char == '"':
                    in_string = True
                elif char == opener:
                    depth += 1
                elif char == closer:
                    depth -= 1
                    if depth == 0:
                        return self._positions[start : index + 1], start, index + 1
            start = self._positions.find(opener, start + 1)
        return None


_FENCE_RE = re.compile(r"```(?:json|jsonc)?\s*\n?(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_json(text: Any) -> tuple[Any, str, str]:
    """Read a JSON value out of an answer → ``(value, extraction note, error)``.

    ``json.loads`` on the exact text first; then the body of a ```` ``` ````-fenced
    block; then the first balanced object/array inside prose. The note always
    says what was done (``""`` means the text *was* JSON), so an extraction is
    visible in the report next to the untouched raw text.
    """
    raw = "" if text is None else str(text)
    stripped = raw.strip()
    if not stripped:
        return None, "", "the answer is empty"
    try:
        return json.loads(stripped), "", ""
    except ValueError as exc:
        first_error = f"the answer is not JSON: {_compact(str(exc))}"
    fenced = _FENCE_RE.search(raw)
    if fenced:
        candidate = fenced.group(1).strip()
        try:
            return json.loads(candidate), "code fence unwrapped (```json … ```)", ""
        except ValueError:
            pass
    cursor = _Balanced(raw)
    for opener, label in (("{", "object"), ("[", "array")):
        found = cursor.extract(opener)
        if found is None:
            continue
        candidate, start, end = found
        try:
            value = json.loads(candidate)
        except ValueError:
            continue
        return (
            value,
            f"the first balanced JSON {label} was taken from surrounding prose "
            f"(chars {start}–{end})",
            "",
        )
    return None, "", first_error


def _compact(text: Any, limit: int = 300) -> str:
    value = " ".join(str(text).split())
    return value if len(value) <= limit else value[: limit - 1] + "…"


# --------------------------------------------------------------------------- #
# the call                                                                     #
# --------------------------------------------------------------------------- #


@dataclass
class StructuredAttempt:
    """One request/answer pair, kept exactly as it happened."""

    index: int
    mode: str
    response_format_type: str
    raw: str = ""
    extraction: str = ""
    validation_error: str = ""
    error: str = ""
    ok: bool = False
    finish_reason: str = ""
    prompt_chars: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "mode": self.mode,
            "response_format_type": self.response_format_type,
            "raw": self.raw,
            "raw_chars": len(self.raw),
            "extraction": self.extraction,
            "validation_error": self.validation_error,
            "error": self.error,
            "ok": self.ok,
            "finish_reason": self.finish_reason,
            "prompt_chars": self.prompt_chars,
        }


@dataclass
class StructuredOutcome:
    """A typed answer **or** an honest failure — never a coerced success.

    ``ok`` is the only thing a caller may branch on: when it is ``False``,
    :attr:`value` and :attr:`model` are ``None`` and :attr:`raw` /
    :attr:`validation_error` / :attr:`error` carry the evidence. Every request
    made is in :attr:`steps`; which mode produced the answer (and why earlier
    modes were skipped) is in :attr:`mode_trail`.
    """

    ok: bool
    value: Any = None
    model: Any = None
    raw: str = ""
    mode: str = ""
    attempts: int = 0
    repaired: bool = False
    schema_name: str = DEFAULT_SCHEMA_NAME
    validation_error: str = ""
    error: str = ""
    validator: str = ""
    strict_model: bool = True
    usage: dict[str, int] = field(default_factory=dict)
    finish_reason: str = ""
    latency_ms: int = 0
    notes: list[str] = field(default_factory=list)
    steps: list[StructuredAttempt] = field(default_factory=list)
    mode_trail: list[dict[str, Any]] = field(default_factory=list)

    def data(self) -> Any:
        """The validated payload as plain JSON data (``None`` when not ``ok``)."""
        return self.value if self.ok else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "data": self.data(),
            "raw": self.raw,
            "mode": self.mode,
            "attempts": self.attempts,
            "repaired": self.repaired,
            "schema_name": self.schema_name,
            "validation_error": self.validation_error,
            "error": self.error,
            "validator": self.validator,
            "strict_model": self.strict_model,
            "usage": dict(self.usage),
            "finish_reason": self.finish_reason,
            "latency_ms": self.latency_ms,
            "notes": list(self.notes),
            "mode_trail": [dict(item) for item in self.mode_trail],
            "steps": [step.to_dict() for step in self.steps],
        }


def require(outcome: StructuredOutcome) -> Any:
    """The validated payload, or a :class:`StructuredValidationError` with evidence."""
    if outcome.ok:
        return outcome.value
    raise StructuredValidationError(
        outcome.error or "the answer did not satisfy the schema",
        raw=outcome.raw,
        validation_error=outcome.validation_error,
        mode=outcome.mode,
        attempts=outcome.attempts,
        steps=[step.to_dict() for step in outcome.steps],
    )


def _messages_for(
    prompt: str, messages: Sequence[Mapping[str, Any]] | None
) -> list[dict[str, Any]]:
    if messages is not None:
        built = [dict(message) for message in messages]
        if not built:
            raise StructuredConfigError("messages is empty — there is nothing to ask")
        return built
    text = str(prompt or "")
    if not text.strip():
        raise StructuredConfigError("a schema-constrained call needs messages= or prompt=")
    return [{"role": "user", "content": text}]


def _response_format_type(
    mode: str, schema: Mapping[str, Any], name: str, strict: bool
) -> str:
    """The ``response_format.type`` one mode puts on the wire (``""`` = prompted)."""
    built = lp.response_format_for(mode, schema=schema, name=name, strict=strict)
    return str(built.get("type") or "") if isinstance(built, Mapping) else ""


def _client_call(
    client: Any,
    messages: Sequence[Mapping[str, Any]],
    *,
    tier: str,
    response_format: Mapping[str, Any] | None,
) -> Any:
    """The transport: the client's own ``chat``, with the field on the wire."""
    return lp.structured_chat(
        client, messages=messages, tier=tier, response_format=response_format
    )


def structured_call(
    client: Any,
    schema: Any,
    messages: Sequence[Mapping[str, Any]] | None = None,
    *,
    prompt: str = "",
    tier: str = "main",
    mode: str = MODE_AUTO,
    name: str = "",
    strict: bool = True,
    strict_schema: bool = False,
    response_model: Any = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    repair_attempts: int = DEFAULT_REPAIR_ATTEMPTS,
    backend: str = "auto",
    call: Any = None,
) -> StructuredOutcome:
    """Ask the model for an answer that satisfies ``schema``; validate it here.

    Returns a :class:`StructuredOutcome` in every case: ``ok=True`` with the
    validated ``value`` (and ``model`` when a pydantic model was given), or
    ``ok=False`` with the raw text, the validation error and the full trail.
    A **provider failure that is not a capability refusal** (rate limit, timeout,
    auth) propagates as its own error — a fallback chain is the layer that
    decides to move on, and it must see the real exception.

    Parameters
    ----------
    client
        Anything with ``chat(messages, *, tier, response_format=…) ->
        ChatResult`` — :class:`skaldir.llm_provider.OpenAICompatibleLLM` is the
        real one.
    schema
        A JSON Schema mapping, or a pydantic model class/instance (its schema is
        used and its instances are returned in ``outcome.model``).
    mode
        ``auto`` (native → json_object → prompted), or one specific mode.
    strict_schema
        Apply :func:`strict_compatible_schema` before sending and record every
        change as a note (never applied silently).
    response_model
        Overrides the model from ``schema`` when both are given.
    call
        Injectable transport ``(messages, mode, response_format, index)`` for
        tests that need no HTTP; defaults to the client's own ``chat``.
    """
    if mode not in MODES:
        raise StructuredConfigError(f"unknown mode {mode!r} (expected one of {list(MODES)})")
    json_schema, resolved_name, from_schema = schema_from(schema, name=name)
    model_cls = response_model if response_model is not None else from_schema
    if model_cls is not None and not is_pydantic_model(model_cls):
        raise StructuredConfigError(
            f"response_model must be a pydantic model, got {type(model_cls).__name__}"
        )
    if int(max_attempts) < 1:
        raise StructuredConfigError("max_attempts must be >= 1")
    if int(repair_attempts) < 0:
        raise StructuredConfigError("repair_attempts must be >= 0")

    convo = _messages_for(prompt, messages)
    sent_schema = json_schema
    notes: list[str] = []
    if strict_schema:
        sent_schema, schema_notes = strict_compatible_schema(json_schema)
        notes.extend(f"strict-schema: {note}" for note in schema_notes)

    plan = [mode] if mode != MODE_AUTO else list(MODE_SEQUENCE)
    outcome = StructuredOutcome(
        ok=False,
        schema_name=resolved_name,
        mode="",
        strict_model=bool(model_cls is not None),
    )
    started = time.monotonic()
    attempt_index = 0
    last_raw = ""
    last_validation = ""
    last_detail = ""
    budget_error = ""

    def _attempt(active_mode: str) -> tuple[bool, str]:
        """One request. Returns ``(decided, status)``.

        ``decided`` = the answer validated (done). ``status`` is one of
        ``"answered"``, ``"retry"`` (a real answer that failed — repair may
        help, otherwise the next mode), ``"skip"`` (this mode is refused by the
        endpoint) or ``"stop"`` (the schema was satisfied but the pydantic model
        refused it — repeating cannot help, and its message must survive).
        """
        nonlocal attempt_index, last_raw, last_validation, last_detail
        attempt_index += 1
        response_format = lp.response_format_for(
            active_mode, schema=sent_schema, name=resolved_name, strict=strict
        )
        request_messages = list(convo)
        if active_mode == MODE_PROMPTED:
            request_messages = [
                *convo,
                {
                    "role": "user",
                    "content": JSON_INSTRUCTION.format(
                        schema=json.dumps(sent_schema, sort_keys=True)
                    ),
                },
            ]
        step = StructuredAttempt(
            index=attempt_index,
            mode=active_mode,
            response_format_type=str((response_format or {}).get("type") or ""),
            prompt_chars=sum(len(str(item.get("content", ""))) for item in request_messages),
        )
        try:
            if call is not None:
                result = call(request_messages, active_mode, response_format, attempt_index)
            else:
                result = _client_call(
                    client, request_messages, tier=tier, response_format=response_format
                )
        except lp.StructuredOutputNotSupportedError as exc:
            step.error = str(exc)
            outcome.steps.append(step)
            outcome.mode_trail.append(
                {
                    "mode": active_mode,
                    "response_format_type": step.response_format_type,
                    "status": "skipped",
                    "reason": REASON_UNSUPPORTED,
                    "detail": _compact(exc.reason or str(exc)),
                }
            )
            notes.append(
                f"mode {active_mode}: the endpoint refused response_format "
                f"({_compact(exc.reason or str(exc), 120)})"
            )
            return False, "skip"
        except LLMError as exc:
            step.error = f"{type(exc).__name__}: {exc}"
            outcome.steps.append(step)
            raise
        text = str(getattr(result, "text", "") or "")
        step.raw = text
        step.finish_reason = str(getattr(result, "finish_reason", "") or "")
        outcome.usage = dict(getattr(result, "usage", {}) or {}) or outcome.usage
        outcome.finish_reason = step.finish_reason or outcome.finish_reason
        last_raw = text
        value, extraction, parse_error = extract_json(text)
        step.extraction = extraction
        if parse_error:
            step.error = parse_error
            step.validation_error = parse_error
            outcome.steps.append(step)
            last_validation = parse_error
            last_detail = f"{active_mode}: {parse_error}"
            return False, "retry"
        if extraction:
            notes.append(f"attempt {attempt_index} ({active_mode}): {extraction}")
        errors, used, validator_notes = validate_payload(sent_schema, value, backend=backend)
        outcome.validator = used
        for note in validator_notes:
            if note not in notes:
                notes.append(note)
        if not errors:
            step.ok = True
            outcome.steps.append(step)
            outcome.ok = True
            outcome.value = value
            outcome.raw = text
            outcome.mode = active_mode
            outcome.validation_error = ""
            outcome.error = ""
            outcome.attempts = attempt_index
            outcome.latency_ms = int((time.monotonic() - started) * 1000)
            outcome.notes = notes
            if model_cls is not None:
                built, model_error = _build_model(
                    model_cls, value, strict=bool(outcome.strict_model)
                )
                if model_error:
                    outcome.ok = False
                    outcome.value = None
                    outcome.model = None
                    outcome.validation_error = model_error
                    outcome.error = (
                        f"the JSON satisfied the schema but the pydantic model "
                        f"{getattr(model_cls, '__name__', model_cls)} refused it: {model_error}"
                    )
                    outcome.mode_trail.append(
                        {
                            "mode": active_mode,
                            "response_format_type": step.response_format_type,
                            "status": "invalid",
                            "reason": REASON_INVALID,
                            "detail": _compact(model_error),
                        }
                    )
                    last_detail = (
                        f"the JSON satisfied the schema but the pydantic model "
                        f"{getattr(model_cls, '__name__', model_cls)} refused it: {model_error}"
                    )
                    last_validation = model_error
                    return False, "stop"
                outcome.model = built
            outcome.mode_trail.append(
                {
                    "mode": active_mode,
                    "response_format_type": step.response_format_type,
                    "status": "answered",
                    "reason": REASON_ANSWERED,
                    "detail": "",
                }
            )
            return True, "answered"
        step.validation_error = "; ".join(errors)
        outcome.steps.append(step)
        last_validation = step.validation_error
        last_detail = f"{active_mode}: {step.validation_error}"
        return False, "retry"

    stopped = False
    for active_mode in plan:
        if attempt_index >= int(max_attempts):
            budget_error = (
                f"the attempt budget ({int(max_attempts)}) is used up before mode "
                f"{active_mode!r} was tried"
            )
            break
        trail_before = len(outcome.mode_trail)
        decided, status = _attempt(active_mode)
        if decided:
            return outcome
        if status == "stop":
            stopped = True
            budget_error = ""
            break
        if status == "retry" and attempt_index < int(max_attempts) and int(repair_attempts) > 0:
            # a real answer that failed: feed it back, exactly as it arrived
            repairs = 0
            while (
                repairs < int(repair_attempts)
                and attempt_index < int(max_attempts)
                and status == "retry"
            ):
                repairs += 1
                outcome.repaired = True
                convo = [
                    *convo,
                    {"role": "assistant", "content": last_raw},
                    {
                        "role": "user",
                        "content": REPAIR_INSTRUCTION.format(
                            error=last_validation, raw=_compact(last_raw, 2000)
                        ),
                    },
                ]
                decided, status = _attempt(active_mode)
                if decided:
                    return outcome
                if status == "stop":
                    stopped = True
                    budget_error = ""
                    break
            if stopped:
                break
        if len(outcome.mode_trail) == trail_before:
            # no entry yet for this mode (the refusal case writes its own).
            outcome.mode_trail.append(
                {
                    "mode": active_mode,
                    "response_format_type": _response_format_type(
                        active_mode, sent_schema, resolved_name, strict
                    ),
                    "status": "invalid" if last_validation else "no_answer",
                    "reason": REASON_INVALID if last_validation else REASON_EMPTY,
                    "detail": _compact(
                        last_validation or f"no usable answer in mode {active_mode}"
                    ),
                }
            )

    outcome.ok = False
    outcome.value = None
    outcome.model = None
    outcome.raw = last_raw
    outcome.validation_error = last_validation
    outcome.attempts = attempt_index
    outcome.latency_ms = int((time.monotonic() - started) * 1000)
    outcome.notes = notes
    outcome.error = _failure_message(
        budget_error=budget_error,
        detail=last_detail,
        validation=last_validation,
        stopped=stopped,
        attempts=attempt_index,
        trail=outcome.mode_trail,
    )
    return outcome


def _failure_message(
    *,
    budget_error: str,
    detail: str,
    validation: str,
    stopped: bool,
    attempts: int,
    trail: Sequence[Mapping[str, Any]],
) -> str:
    """One honest sentence for a failed call: what was tried, and the last reason."""
    modes = ", ".join(str(item.get("mode")) for item in trail) or "none"
    if stopped and detail:
        return f"{detail} (after {attempts} attempt(s); modes: {modes})"
    if budget_error:
        return f"{budget_error} — last reason: {detail}" if detail else budget_error
    if validation:
        return (
            f"no mode produced a valid answer in {attempts} attempt(s) "
            f"(modes tried: {modes}) — last validation error: {validation}"
        )
    return f"no answer could be read in {attempts} attempt(s) (modes tried: {modes})"


def _build_model(model_cls: Any, value: Any, *, strict: bool) -> tuple[Any, str]:
    """Construct the pydantic model; the error text is returned, never swallowed."""
    validate = getattr(model_cls, "model_validate", None)
    if validate is None:  # pragma: no cover - pydantic v1 shape
        return None, "the response model has no model_validate() (pydantic v2 is required)"
    try:
        if strict:
            try:
                return validate(value, strict=True), ""
            except TypeError:  # pragma: no cover - older pydantic without the kwarg
                return validate(value), ""
        return validate(value), ""
    except Exception as exc:  # noqa: BLE001 - pydantic.ValidationError and friends
        detail = str(exc)
        errors: Any = getattr(exc, "errors", None)
        raw_errors = errors() if callable(errors) else []
        if isinstance(raw_errors, (list, tuple)):
            parts: list[str] = []
            for error in raw_errors:
                location = ".".join(str(part) for part in error["loc"]) or "<root>"
                parts.append(f"{location}: {error['msg']}")
            detail = "; ".join(parts)
        return None, detail


def structured_or_raise(*args: Any, **kwargs: Any) -> Any:
    """:func:`structured_call`, but the payload is returned and a failure raises."""
    return require(structured_call(*args, **kwargs))


# --------------------------------------------------------------------------- #
# the probe — one real structured call, with the raw evidence                  #
# --------------------------------------------------------------------------- #

#: The probe's schema: small, strict-mode compatible, and impossible to satisfy
#: by accident (two required properties, one enum).
PROBE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "answer": {"type": "string", "enum": ["pong"]},
        "probe": {"type": "integer"},
    },
    "required": ["answer", "probe"],
    "additionalProperties": False,
}

PROBE_PROMPT = (
    "Reply with JSON: the string \"pong\" as 'answer', and the integer 1 as 'probe'."
)
PROBE_TIER = "small"
PROBE_SCHEMA_NAME = "skaldir_probe"


class RecordingClient:
    """An ``httpx.Client``-shaped wrapper that keeps what left the machine.

    Only the request bodies (and the statuses) are kept — **never** the headers,
    so an ``Authorization`` value cannot reach a report. The transport is the
    real one: this is an observer, not a mock.
    """

    def __init__(self, client: Any = None) -> None:
        self._client = client if client is not None else httpx.Client(follow_redirects=False)
        self.requests: list[dict[str, Any]] = []

    def _record(self, url: str, kwargs: Mapping[str, Any]) -> None:
        self.requests.append({"url": str(url), "body": kwargs.get("json")})

    def post(self, url: str, **kwargs: Any) -> Any:
        self._record(url, kwargs)
        return self._client.post(url, **kwargs)

    def stream(self, method: str, url: str, **kwargs: Any) -> Any:
        self._record(url, kwargs)
        return self._client.stream(method, url, **kwargs)

    def close(self) -> None:
        self._client.close()

    @property
    def last_body(self) -> dict[str, Any]:
        for entry in reversed(self.requests):
            body = entry.get("body")
            if isinstance(body, Mapping):
                return dict(body)
        return {}


def probe_structured(
    name: str | None = None,
    *,
    path: str | Any | None = None,
    env: Mapping[str, str] | None = None,
    tier: str = PROBE_TIER,
    prompt: str = PROBE_PROMPT,
    schema: Mapping[str, Any] | None = None,
    response_model: Any = None,
    mode: str = MODE_AUTO,
    timeout_s: float | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> dict[str, Any]:
    """One **real** structured call against the configured provider, with evidence.

    Answers the owner's question with a measurement instead of a claim: did the
    endpoint accept the native ``response_format: json_schema``, or did the run
    take the validated fallback? The payload carries the ``response_format``
    actually sent, the raw answer text, the validation backend and errors, and
    the per-mode trail (so "native" being skipped names the provider's own
    refusal text).

    The credential is read from the environment **only** and is never rendered:
    the payload reports its state (``set``/``unset``) and the variable name.
    Raises the store's own refusals (``ProviderUnusable``,
    ``ProviderNeedsCredential``) so the CLI maps them to the shared exit codes.
    """
    from . import provider_config as pc  # local import: keeps the import graph flat

    store = pc.load_store(path, env=env)
    env_map = pc._env_map(env)  # the store's own env view (os.environ when env is None)
    if str(name or "").strip():
        entry = store.entry(name)
    elif store.providers:
        entry = store.entry(store.default or None)
    else:
        active = lp.resolve_provider_config(env=env_map, config_path=store.path)
        entry = pc.ProviderEntry(
            name=pc.DEFAULT_ENTRY_NAME,
            kind=active.provider,
            base_url=active.base_url,
            model=active.model,
            tiers=dict(active.tier_models),
            timeout_s=active.timeout_s,
        )
        if not active.ready() and not entry.base_url:
            raise pc.ProviderUnusable(
                f"no provider configured (config file {store.path}) — add one, e.g.: "
                f"{pc.EXAMPLE_ADD}"
            )
    unusable = [
        item for item in entry.missing(env_map) if not item.startswith("credential is unset")
    ]
    if unusable:
        raise pc.ProviderUnusable(
            f"provider {entry.name!r} cannot be called: " + "; ".join(unusable)
        )
    if entry.needs_credential(env_map):
        export = entry.api_key_env or lp.ENV_API_KEY
        raise pc.ProviderNeedsCredential(
            f"provider {entry.name!r} points at {entry.base_url} and has no credential: "
            f"export {export}=<your-key> (the value is never stored in {store.path})"
        )

    explicit: dict[str, Any] = {
        "provider": entry.kind,
        "base_url": entry.base_url,
        "model": entry.model,
        "tiers": dict(entry.tiers),
    }
    if entry.api_key_env:
        explicit["api_key_env"] = entry.api_key_env
    effective_timeout = timeout_s if timeout_s is not None else entry.timeout_s
    if effective_timeout is not None:
        explicit["timeout_s"] = float(effective_timeout)
    cfg = lp.resolve_provider_config(explicit, env=env_map, config_path=store.path)
    if cfg.problems:
        raise pc.ProviderUnusable("provider config refused: " + "; ".join(cfg.problems))
    if not cfg.ready():
        raise pc.ProviderUnusable(lp.not_configured_message(cfg))

    recorder = RecordingClient()
    client = lp.OpenAICompatibleLLM(cfg, client=recorder)
    started = time.monotonic()
    try:
        outcome = structured_call(
            client,
            dict(schema) if schema else dict(PROBE_SCHEMA),
            prompt=prompt,
            tier=tier,
            mode=mode,
            name=PROBE_SCHEMA_NAME,
            response_model=response_model,
            max_attempts=max_attempts,
        )
    finally:
        latency_ms = int((time.monotonic() - started) * 1000)
        last_body = recorder.last_body
        recorder.close()

    sent = last_body.get("response_format") if isinstance(last_body, Mapping) else None
    native = next(
        (item for item in outcome.mode_trail if item.get("mode") == MODE_NATIVE), None
    )
    native_supported: bool | None = None
    native_reason = ""
    if native is not None:
        if native.get("reason") == REASON_UNSUPPORTED:
            native_supported, native_reason = False, str(native.get("detail") or "")
        elif native.get("status") == "answered" or outcome.mode == MODE_NATIVE:
            native_supported = True
        else:
            native_supported, native_reason = None, str(native.get("detail") or "")
    if outcome.mode == MODE_NATIVE:
        native_supported = True

    return {
        "ok": bool(outcome.ok),
        "name": entry.name,
        "kind": entry.kind,
        "base_url": entry.base_url,
        "endpoint": lp.endpoint_for(entry.base_url),
        "config_file": str(store.path),
        "tier": str(tier),
        "model": cfg.model_for_tier(tier),
        "client": type(client).__name__,
        "credential": entry.credential_state(env_map),
        "latency_ms": latency_ms,
        "schema_name": outcome.schema_name,
        "schema": dict(schema) if schema else dict(PROBE_SCHEMA),
        "mode_requested": mode,
        "mode_used": outcome.mode,
        "native_json_schema": native_supported,
        "native_reason": _compact(native_reason, 200),
        "mode_trail": [dict(item) for item in outcome.mode_trail],
        "request": {
            "response_format": sent if isinstance(sent, Mapping) else None,
            "model": last_body.get("model") if isinstance(last_body, Mapping) else None,
            "messages": len(last_body.get("messages") or [])
            if isinstance(last_body, Mapping)
            else 0,
            "requests_made": len(recorder.requests),
        },
        "answer": {
            "raw": _compact(outcome.raw, 600),
            "raw_chars": len(outcome.raw),
            "value": outcome.value,
            "finish_reason": outcome.finish_reason,
            "parse_error": outcome.validation_error,
        },
        "validation": {
            "backend": outcome.validator,
            "errors": [outcome.validation_error] if outcome.validation_error else [],
            "ok": outcome.ok,
        },
        "attempts": outcome.attempts,
        "repaired": outcome.repaired,
        "usage": dict(outcome.usage),
        "error": outcome.error,
        "notes": list(outcome.notes),
        "evidence": {
            "response_format_sent": sent if isinstance(sent, Mapping) else None,
            "raw_answer": _compact(outcome.raw, 600),
            "mode_trail": [dict(item) for item in outcome.mode_trail],
        },
    }


__all__ = [
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_REPAIR_ATTEMPTS",
    "DEFAULT_SCHEMA_NAME",
    "JSON_INSTRUCTION",
    "MODE_AUTO",
    "MODE_JSON_OBJECT",
    "MODE_NATIVE",
    "MODE_PROMPTED",
    "MODE_SEQUENCE",
    "MODES",
    "PROBE_PROMPT",
    "PROBE_SCHEMA",
    "PROBE_SCHEMA_NAME",
    "PROBE_TIER",
    "REASON_ANSWERED",
    "REASON_AUTH",
    "REASON_BUDGET",
    "REASON_EMPTY",
    "REASON_INVALID",
    "REASON_NOT_JSON",
    "REASON_PROVIDER_ERROR",
    "REASON_RATE_LIMIT",
    "REASON_TIMEOUT",
    "REASON_TOOL_CALL",
    "REASON_UNSUPPORTED",
    "REPAIR_INSTRUCTION",
    "RecordingClient",
    "StructuredAttempt",
    "StructuredConfigError",
    "StructuredError",
    "StructuredOutcome",
    "StructuredValidationError",
    "builtin_validate",
    "extract_json",
    "is_pydantic_model",
    "jsonschema_backend",
    "probe_structured",
    "require",
    "schema_from",
    "strict_compatible_schema",
    "structured_call",
    "structured_or_raise",
    "validate_payload",
]
