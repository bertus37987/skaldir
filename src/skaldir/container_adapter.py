"""The runtime of a **generated container adapter**: typed inputs in, argv out,
keyed outputs back in, artefact facts derived locally.

Why this module exists
----------------------
``toolbuilder`` generates an adapter for a capability it mined from a container
image. The first three templates were verb templates — one text in, one line of
text out — and the tiny rendering rule (``str(payload[param])`` substituted into
an argv part) lived in two places: the builder, and the module the builder
*writes* into the runtime's ``generated_tools/<name>/`` directory. Two copies of
one rule is one copy too many, and the argument-mapping templates
(ImageMagick ``-resize``, ffmpeg ``-ss/-to``) need a rule that is worth not
duplicating: typed parameters, flags that appear or disappear with a boolean,
enum values that render as the tool's own token, host files that travel into the
container, artefacts that come back and whose *facts* are read from their bytes.

So the rule lives here, once, as a **stdlib-only, dependency-free** module:

* the builder imports it and stages a copy of it next to the tool it writes, so
  the generated module and the builder that certified it run the *same* code —
  ``tests/test_toolbuilder_container.py`` pins that agreement;
* it imports nothing from the rest of this package except
  :mod:`skaldir.container_media` (also stdlib-only, also copied), so the staged
  tool directory is self-contained.

The four steps of one container call
------------------------------------
====================================  ==========================================
``resolve_inputs``                    a payload becomes typed, validated values
                                      (integer/number/boolean/enum/string/path,
                                      bounds, enums, patterns, required-ness,
                                      file existence, media sniffing, size caps)
``render``                            those values become argv: literal parts,
                                      flags with values, flags that appear only
                                      when their parameter is true, parts that
                                      only appear when a parameter was *given*,
                                      and a host file transferred as base64
``parse_stdout``                      the container's stdout becomes the declared
                                      outputs: the keyed protocol (``name@b64=…``
                                      for a file, ``name=value`` for a scalar)
                                      plus the artefact fields derived from the
                                      received bytes by ``container_media``
``write_artefacts``                    the decoded artefact lands on the host at
                                      the path the caller named

What a template author may declare
-----------------------------------
A parameter spec is ``{"type", "required", "default", "host_only", "exists",
"media", "max_bytes", "min", "max", "values", "token_map", "pattern",
"description"}``. Two rules carry the argument mapping:

* **host_only** — the value never reaches the container's argv. ``input`` (the
  file whose bytes are transferred) and ``out`` (the host path the returned
  artefact is written to) are both host-side; a template that tries to
  interpolate a host-only parameter into a literal part is *refused by name*
  instead of shipping the host's path to the container.
* **the parts** — a part is a literal string (``-resize``, ``{width}x{height}``),
  ``{"const": …}``, ``{"transfer": "base64", "param": …}``, or a flag:
  ``{"flag": name, "value": template}`` with one of the four presence rules
  ``when`` / ``when_not`` / ``when_given`` / ``when_not_given``.

Honesty rules
-------------
* Nothing is coerced silently: ``"12"`` for an integer is *not* accepted as 12
  (the caller sends JSON — a string is a different thing), a boolean parameter
  is a JSON boolean, an enum value outside its list is refused with the list.
* A payload key that the adapter does not declare is refused (the same rule the
  verb templates already had).
* An unsubstituted ``{…}`` placeholder left in a rendered part is refused: a
  template that names a parameter it did not declare is a construction error,
  and it is better to refuse the call than to send ``{width}x`` to the tool.
* The declared outputs must all be present exactly once in the container's
  stdout; an extra line, a duplicate or a missing output is refused by name.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from pathlib import Path

from . import container_media

SCHEMA_VERSION = 1

#: The keyed-output marker: ``<name>@b64=<base64>`` carries a file, ``<name>=``
#: a scalar. The container prints one line per declared output.
FILE_MARKER = "@b64"

#: The two output protocols an adapter may declare.
PROTOCOL_SINGLE = "single-value"
PROTOCOL_KEYED = "keyed"

#: The two input-transfer modes a template may declare.
TRANSFER_ARGV_BASE64 = "argv-base64"

#: The kernel's own cap on ONE argv element (``MAX_ARG_STRLEN``); a transfer
#: larger than this cannot be handed to ``docker run`` at all. A template that
#: carries a file in argv declares the source size it accepts
#: (``max_bytes``), and the builder keeps it well below this number.
ARGV_ELEMENT_LIMIT = 131072

_NAMES = ("_in", "_out")


class AdapterRefusal(RuntimeError):
    """The call does not map onto the adapter — never a partially-run command."""


# --------------------------------------------------------------------------- #
# typed inputs
# --------------------------------------------------------------------------- #


def _as_integer(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise AdapterRefusal(f"input '{name}' is not an integer ({value!r})")
    return int(value)


def _as_number(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AdapterRefusal(f"input '{name}' is not a number ({value!r})")
    return float(value)


def _as_boolean(name: str, value: object) -> bool:
    if isinstance(value, bool):
        return value
    raise AdapterRefusal(f"input '{name}' is not a boolean ({value!r}) — JSON true/false")


def _as_text(name: str, value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    raise AdapterRefusal(f"input '{name}' is not a string ({value!r})")


def _as_enum(name: str, value: object, values: tuple) -> str:
    text = _as_text(name, value)
    allowed = [str(item) for item in values]
    if text not in allowed:
        raise AdapterRefusal(
            f"input '{name}' is not one of {allowed} (got {text!r})"
        )
    return text


def _as_path(name: str, value: object, spec: dict) -> str:
    text = _as_text(name, value)
    if not text:
        raise AdapterRefusal(f"input '{name}' is an empty path")
    path = Path(text)
    exists = bool(spec.get("exists", True))
    if exists and not path.is_file():
        raise AdapterRefusal(f"input '{name}' is not an existing file: {text}")
    if not exists:
        # an OUTPUT path: it may exist (a caller re-running the same call writes
        # the same artefact again, exactly as the first call did) but a directory
        # or a device is never a file target
        if path.is_dir():
            raise AdapterRefusal(
                f"input '{name}' is a directory, and an artefact cannot be written "
                f"onto one: {text}"
            )
        return str(path)
    size = path.stat().st_size
    limit = spec.get("max_bytes")
    if limit and size > int(limit):
        raise AdapterRefusal(
            f"input '{name}' is {size} bytes and this adapter carries at most "
            f"{int(limit)} bytes (the transfer is one argv element, and the "
            f"kernel caps one element at {ARGV_ELEMENT_LIMIT} bytes)"
        )
    declared = str(spec.get("media") or "")
    if declared:
        head = path.read_bytes()[:64]
        found = container_media.media_format(head)
        if found != declared:
            raise AdapterRefusal(
                f"input '{name}' is not {declared}: its magic bytes say "
                f"{found or 'nothing this reader knows'}"
            )
    return str(path)


def resolve_inputs(adapter: dict, payload: dict) -> dict:
    """The payload as typed values, or :class:`AdapterRefusal` naming the parameter."""
    specs = dict((adapter.get("operation") or {}).get("inputs") or {})
    supplied = dict(payload or {})
    unknown = sorted(set(supplied) - set(specs))
    if unknown:
        raise AdapterRefusal("unknown input(s) for this adapter: " + ", ".join(unknown))
    resolved: dict = {}
    for name, spec in specs.items():
        spec = dict(spec or {})
        kind = str(spec.get("type") or "string")
        if name in supplied:
            value = supplied[name]
        elif "default" in spec:
            value = spec["default"]
        elif spec.get("required"):
            raise AdapterRefusal(f"missing required input: {name}")
        else:
            continue  # absent and optional: no value, no argv part
        try:
            if kind == "integer":
                resolved[name] = _as_integer(name, value)
            elif kind == "number":
                resolved[name] = _as_number(name, value)
            elif kind == "boolean":
                resolved[name] = _as_boolean(name, value)
            elif kind == "enum":
                resolved[name] = _as_enum(name, value, tuple(spec.get("values") or ()))
            elif kind == "path":
                resolved[name] = _as_path(name, value, spec)
            elif kind == "string":
                resolved[name] = _as_text(name, value)
            else:
                raise AdapterRefusal(f"input '{name}' declares unknown type '{kind}'")
        except AdapterRefusal:
            raise
        if kind in ("integer", "number"):
            low, high = spec.get("min"), spec.get("max")
            if low is not None and resolved[name] < low:
                raise AdapterRefusal(
                    f"input '{name}' is below its minimum {low} ({resolved[name]})"
                )
            if high is not None and resolved[name] > high:
                raise AdapterRefusal(
                    f"input '{name}' is above its maximum {high} ({resolved[name]})"
                )
        pattern = str(spec.get("pattern") or "")
        if pattern and not re.fullmatch(pattern, str(resolved[name])):
            raise AdapterRefusal(
                f"input '{name}' does not match {pattern}: {str(resolved[name])[:60]!r}"
            )
    # cross-field rules: a declared ordering between two parameters (the range
    # template's `end > start`) is part of the contract, not a caller's duty
    for name, spec in specs.items():
        other = str((spec or {}).get("greater_than") or "")
        if not other or name not in resolved or other not in resolved:
            continue
        if not resolved[name] > resolved[other]:
            raise AdapterRefusal(
                f"input '{name}' ({resolved[name]}) must be greater than "
                f"'{other}' ({resolved[other]})"
            )
    return resolved


def context_of(adapter: dict, resolved: dict) -> dict:
    """The values a template may interpolate: the inputs plus the scratch names."""
    context = {name: value for name, value in resolved.items()}
    for key, value in dict(adapter.get("scratch") or {}).items():
        context[str(key)] = value
    return context


def _token(name: str, value: object, spec: dict) -> str:
    """One parameter's token as argv: an enum may render as the tool's own word."""
    token_map = dict(spec.get("token_map") or {})
    text = str(value)
    if text in token_map:
        return str(token_map[text])
    return text


def _interpolate(name: str, text: str, context: dict, specs: dict) -> str:
    """One literal part with its ``{placeholders}`` substituted or refused."""
    def swap(match: re.Match) -> str:
        key = match.group(1)
        if key not in context:
            raise AdapterRefusal(
                f"the argv part {text!r} names '{key}', which this adapter does "
                "not declare"
            )
        spec = dict(specs.get(key) or {})
        if spec.get("host_only"):
            raise AdapterRefusal(
                f"the argv part {text!r} interpolates the host-only input "
                f"'{key}' — a host value never reaches the container's argv"
            )
        return _token(key, context[key], spec)

    rendered = re.sub(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", swap, str(text))
    if "{" in rendered or "}" in rendered:
        raise AdapterRefusal(
            f"the argv part {name!r} still carries an unsubstituted placeholder: "
            f"{rendered!r}"
        )
    return rendered


def _condition_holds(part: dict, resolved: dict, supplied: set) -> tuple[bool, str]:
    """Whether a conditional part is rendered — and the parameter it depends on."""
    for key in ("when", "when_not", "when_given", "when_not_given"):
        name = part.get(key)
        if not name:
            continue
        truthy = bool(resolved.get(str(name)))
        if key == "when" and not truthy:
            return False, str(name)
        if key == "when_not" and truthy:
            return False, str(name)
        if key == "when_given" and str(name) not in supplied:
            return False, str(name)
        if key == "when_not_given" and str(name) in supplied:
            return False, str(name)
    return True, ""


def render(adapter: dict, payload: dict) -> list[str]:
    """The container argv for one payload — typed, validated, or refused."""
    resolved = resolve_inputs(adapter, payload)
    parts = list(adapter.get("argv_template") or [])
    if not parts:
        raise AdapterRefusal("this adapter declares no argv template")
    if not any(isinstance(part, dict) and part.get("transfer") for part in parts) \
            and any(
                str((spec or {}).get("transfer") or "") == TRANSFER_ARGV_BASE64
                for spec in ((adapter.get("operation") or {}).get("inputs") or {}).values()
            ):
        # a template that declares a transferred input must carry it somewhere
        raise AdapterRefusal(
            "this adapter declares a transferred input but no 'transfer' argv "
            "part renders it — the container would receive nothing"
        )
    context = context_of(adapter, resolved)
    specs = dict((adapter.get("operation") or {}).get("inputs") or {})
    supplied = {name for name in (payload or {})}
    rendered: list[str] = []
    for part in parts:
        if isinstance(part, str):
            rendered.append(_interpolate(part, part, context, specs))
            continue
        if not isinstance(part, dict):
            raise AdapterRefusal(f"an argv part is neither a string nor a mapping: {part!r}")
        transfer = part.get("transfer")
        if part.get("verb"):
            # the resolved verb of the adapter (`magick` or `convert`, whichever
            # the inspected surface really offered) is the command's own token
            name = str(adapter.get("verb") or "")
            if not name:
                raise AdapterRefusal(
                    "this template places the verb in its argv and the adapter "
                    "resolves none"
                )
            rendered.append(name)
            continue
        if transfer:
            name = str(part.get("param") or "")
            if name not in resolved:
                raise AdapterRefusal(f"the transfer part names '{name}', which is unset")
            spec = dict(specs.get(name) or {})
            if str(spec.get("transfer") or "") != TRANSFER_ARGV_BASE64:
                raise AdapterRefusal(
                    f"input '{name}' is not declared as an {TRANSFER_ARGV_BASE64} "
                    "input — its bytes may not be carried into the container"
                )
            data = Path(str(resolved[name])).read_bytes()
            encoded = base64.b64encode(data).decode("ascii")
            if len(encoded) >= ARGV_ELEMENT_LIMIT:
                raise AdapterRefusal(
                    f"the encoded input is {len(encoded)} bytes and one argv "
                    f"element holds at most {ARGV_ELEMENT_LIMIT} — use a smaller "
                    "source file (this transfer mode carries the file in argv)"
                )
            rendered.append(encoded)
            continue
        holds, _name = _condition_holds(part, resolved, supplied)
        if not holds:
            continue
        const = part.get("const")
        if const is not None:
            rendered.append(_interpolate("const", str(const), context, specs))
            continue
        flag = part.get("flag")
        if flag is None:
            raise AdapterRefusal(f"an argv part declares neither a flag, a const nor a "
                          f"transfer: {json.dumps(part, sort_keys=True)[:120]}")
        rendered.append(_interpolate("flag", str(flag), context, specs))
        value = part.get("value")
        if value is not None:
            rendered.append(_interpolate("value", str(value), context, specs))
    return [str(item) for item in list(adapter.get("runner") or [])] + rendered


# --------------------------------------------------------------------------- #
# the keyed output protocol
# --------------------------------------------------------------------------- #


def _decode(name: str, payload: str) -> bytes:
    text = "".join(str(payload).split())
    if not text:
        raise AdapterRefusal(f"output '{name}' carries no bytes")
    try:
        return base64.b64decode(text, validate=True)
    except (ValueError, TypeError) as exc:
        raise AdapterRefusal(f"output '{name}' is not valid base64: {exc}") from exc


def _derived(name: str, spec: dict, artefacts: dict) -> object:
    derive = dict(spec.get("derive") or {})
    source = str(derive.get("from") or "")
    field = str(derive.get("field") or "")
    if source not in artefacts:
        raise AdapterRefusal(
            f"output '{name}' derives '{field}' from '{source}', which this "
            "adapter does not return"
        )
    try:
        return container_media.field_of(artefacts[source], field)
    except container_media.ArtefactRefusal as exc:
        raise AdapterRefusal(f"output '{name}' is not derivable: {exc}") from exc


def _summarise(data: bytes) -> dict:
    return {
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "media": container_media.media_format(data),
        "path": "",
    }


def parse_stdout(
    adapter: dict, stdout: str, *, keep_artefacts: bool = False
) -> tuple[bool, dict, str]:
    """The container's stdout as the declared outputs — or ``(False, {}, reason)``.

    The ``single-value`` protocol (the three verb templates) is one pattern per
    output matched against the whole stdout, exactly as before. The ``keyed``
    protocol reads one line per declared output and derives the artefact-sourced
    outputs from the bytes that arrived.
    """
    outputs = dict((adapter.get("operation") or {}).get("outputs") or {})
    text = str(stdout or "").strip()
    protocol = str(adapter.get("output_protocol") or PROTOCOL_SINGLE)
    if protocol != PROTOCOL_KEYED:
        parsed: dict[str, str] = {}
        for out_name, spec in outputs.items():
            pattern = str((spec or {}).get("pattern") or "")
            if pattern and not re.fullmatch(pattern, text):
                return False, {}, (
                    f"output '{out_name}' does not match {pattern}: {text[:80]!r}"
                )
            parsed[out_name] = text
        return True, parsed, ""

    lines = [line for line in str(stdout or "").splitlines() if line.strip()]
    derived = {
        name for name, spec in outputs.items() if (spec or {}).get("derive")
    }
    carried: dict[str, tuple[str, bool]] = {}
    for line in lines:
        key, sep, value = line.partition("=")
        if not sep:
            return False, {}, (
                f"the container printed a line without an output key: {line[:80]!r}"
            )
        is_file = key.endswith(FILE_MARKER)
        name = key[: -len(FILE_MARKER)] if is_file else key
        if name not in outputs:
            return False, {}, (
                f"the container printed an undeclared output '{name}' "
                f"(declared: {', '.join(sorted(outputs)) or 'none'})"
            )
        if name in derived:
            return False, {}, (
                f"the container printed '{name}', which this adapter DERIVES from "
                "the artefact it returns — a derived output is never the "
                "container's own word"
            )
        if name in carried:
            return False, {}, f"the container printed output '{name}' twice"
        carried[name] = (value, is_file)
    missing = sorted((set(outputs) - derived) - set(carried))
    if missing:
        return False, {}, (
            "the container did not print the declared output(s): "
            + ", ".join(missing)
        )

    artefacts: dict[str, bytes] = {}
    declared: dict[str, bytes] = {}
    parsed_out: dict = {}
    for name, spec in outputs.items():
        spec = dict(spec or {})
        if name in derived:
            continue  # read out of the artefact below, never printed by the container
        value, is_file = carried[name]
        if is_file:
            try:
                data = _decode(name, value)
            except AdapterRefusal as exc:
                return False, {}, str(exc)
            declared_media = spec.get("media")
            if declared_media:
                allowed = [str(item) for item in declared_media] if isinstance(
                    declared_media, (list, tuple)
                ) else [str(declared_media)]
                found = container_media.media_format(data)
                if found not in allowed:
                    return False, {}, (
                        f"output '{name}' is declared {allowed} but its bytes say "
                        f"{found or 'nothing this reader knows'}"
                    )
            declared[name] = data
            artefacts[name] = data
            summary = _summarise(data)
            if keep_artefacts:
                summary["data"] = data
            parsed_out[name] = summary
            continue
        pattern = str(spec.get("pattern") or "")
        if pattern and not re.fullmatch(pattern, value):
            return False, {}, (
                f"output '{name}' does not match {pattern}: {value[:80]!r}"
            )
        parsed_out[name] = value
    for name, spec in outputs.items():
        spec = dict(spec or {})
        if not spec.get("derive"):
            continue
        try:
            parsed_out[name] = _derived(name, spec, artefacts)
        except AdapterRefusal as exc:
            return False, {}, str(exc)
        pattern = str(spec.get("pattern") or "")
        if pattern and not re.fullmatch(pattern, str(parsed_out[name])):
            return False, {}, (
                f"output '{name}' does not match {pattern}: "
                f"{str(parsed_out[name])[:80]!r}"
            )
    return True, parsed_out, ""


def redacted_argv(adapter: dict, payload: dict, rendered: list[str]) -> list[str]:
    """The rendered argv with any transferred SOURCE replaced by a placeholder.

    A rendered command line carries the source file as base64 — which is how it
    reaches the container, and which is also the wrong thing to write into a
    record: the evidence, the manifest and a health probe's output are files on
    disk, and a source file may be anything at all. The element is replaced by
    ``<source:NAME:BASE64_BYTES>`` *in the record only*; the command that really
    ran carried the bytes, and the payload (which names the paths) is recorded
    beside it, so the call is reproducible without storing the file twice.
    """
    masked = list(rendered)
    specs = dict((adapter.get("operation") or {}).get("inputs") or {})
    for name, spec in specs.items():
        if str((spec or {}).get("transfer") or "") != TRANSFER_ARGV_BASE64:
            continue
        path = str((payload or {}).get(name) or "")
        if not path or not Path(path).is_file():
            continue
        encoded = base64.b64encode(Path(path).read_bytes()).decode("ascii")
        placeholder = f"<source:{name}:{len(encoded)} base64 bytes>"
        masked = [placeholder if item == encoded else item for item in masked]
    return masked


def write_artefacts(adapter: dict, payload: dict, parsed: dict) -> dict:
    """Write every returned file output to the host path the caller named.

    A file output declares ``host_path_param``; when the payload does not carry
    that parameter the bytes are simply not written (the caller asked for the
    facts, not the file). The ``data`` key is dropped either way, so a parsed
    record never carries a buffer the caller did not ask for.
    """
    written: dict[str, str] = {}
    outputs = dict((adapter.get("operation") or {}).get("outputs") or {})
    for name, spec in outputs.items():
        entry = parsed.get(name)
        if not isinstance(entry, dict) or "data" not in entry:
            continue
        data = entry.pop("data")
        param = str((spec or {}).get("host_path_param") or "")
        target = str((payload or {}).get(param) or "") if param else ""
        if not target:
            continue
        path = Path(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        entry["path"] = str(path)
        written[name] = str(path)
    return written
