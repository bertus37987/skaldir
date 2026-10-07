"""General container → tool templates: any CLI verb, not just the five hand-written ones.

Why this module exists (W2). ``toolbuilder.VERB_TEMPLATES`` certified container
capabilities against an *independent host twin* (hashlib/base64, PNG/MP4 headers).
That is the strongest proof — but it only exists for tools whose twin is obvious.
"Build a tool from any useful container" also needs:

1. **More text-verb families** whose host twin is plain Python
   (``tr``/``wc``/``sort``/``jq``/``basename``/``cut``) — same independent-oracle
   discipline, just more of them.
2. **A declared-fixture path** for tools with *no* honest host twin
   (``pandoc``, ``tesseract``, ``sqlite3`` on real files): the expectation is a
   **golden fixture** the operator supplies, labelled as such. It is never
   presented as a host twin, and a payload other than the sample is refused
   rather than guessed.

Template shape is exactly ``toolbuilder.VERB_TEMPLATES``'s: ``id``, ``terms``,
``verb``, ``argv_template``, ``inputs``, ``outputs``, ``oracle``, ``sample_input``.
:func:`skaldir.toolbuilder.generate_container_adapter` therefore accepts any
template from here without knowing where it was defined.

Oracle honesty ladder (strongest first, all labelled in the test record):

* ``host twin`` — the expectation is computed by Python stdlib from the input.
* ``declared fixture`` — the expectation is a golden value for ONE sample input;
  any other payload is refused by the oracle itself.
* (never) "the container agrees with itself" — that is not an oracle.

What this module does NOT do: invent an adapter from ``--help`` text. A schema
comes from :func:`cli_template` / :func:`fixture_template` (a typed declaration
or an existing template). Guessing argv from help prose would be the exact
"builder does not invent code" failure mode.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from .container_adapter import AdapterRefusal

# --------------------------------------------------------------------------- #
# oracles                                                                       #
# --------------------------------------------------------------------------- #


def oracle_host_twin(fn: Callable[[dict], Any], *, note: str) -> Callable[[dict], Any]:
    """Wrap a pure host function as an independent oracle (labelled)."""

    def _oracle(payload: dict) -> dict:
        result = fn(dict(payload))
        if isinstance(result, Mapping):
            return {
                "values": dict(result.get("values") or result),
                "tolerance": dict(result.get("tolerance") or {}),
                "note": note,
            }
        return {"values": {"value": str(result)}, "tolerance": {}, "note": note}

    return _oracle


def oracle_fixture(
    sample_input: Mapping[str, Any],
    expected: Mapping[str, str],
    *,
    note: str = "declared golden fixture (NOT a host twin) — certifies only the sample input",
) -> Callable[[dict], Any]:
    """Fixture oracle: one golden expectation for one exact sample payload.

    Any other payload raises :class:`AdapterRefusal` instead of inventing an
    expectation — a fixture never pretends to predict a call it was not given.
    """
    sample = dict(sample_input)
    golden = {str(key): str(value) for key, value in expected.items()}

    def _oracle(payload: dict) -> dict:
        if dict(payload) != sample:
            raise AdapterRefusal(
                "this capability is certified by a declared fixture for one sample "
                f"input only — got {sorted(payload)} want {sorted(sample)}; "
                "pass --test-input matching the sample, or a host-twin template"
            )
        return {"values": dict(golden), "tolerance": {}, "note": note}

    return _oracle


# --------------------------------------------------------------------------- #
# compact factories                                                             #
# --------------------------------------------------------------------------- #


def text_oracle(name: str, fn: Callable[[str], str], *, note: str) -> Callable[[dict], Any]:
    """``payload['text']`` → one string, compared exactly (the verb-template shape)."""

    def _payload(payload: dict) -> Any:
        return fn(str(payload.get("text", "")))

    return oracle_host_twin(_payload, note=note)


def text_template(
    *,
    id: str,
    terms: Sequence[str],
    verb: str,
    argv_template: Sequence[str | dict],
    output_name: str,
    oracle: Callable[[dict], Any],
    output_pattern: str = r"^.*$",
    sample_text: str = "skaldir",
    verb_candidates: Sequence[str] | None = None,
    description: str = "",
) -> dict:
    """One text-in / one-text-out template (the hash verbs' shape)."""

    def _named(payload: dict) -> Any:
        result = oracle(dict(payload))
        if isinstance(result, Mapping):
            values = dict(
                result.get("values") or {k: v for k, v in result.items() if k != "tolerance"}
            )
            if "value" in values and output_name != "value":
                values = {output_name: values["value"]}
            elif output_name not in values and len(values) == 1:
                only = next(iter(values.values()))
                values = {output_name: only}
            return {
                "values": values,
                "tolerance": dict(result.get("tolerance") or {}),
                "note": str(result.get("note") or ""),
            }
        return {"values": {output_name: str(result)}, "tolerance": {}, "note": ""}

    entry: dict[str, Any] = {
        "id": id,
        "kind": "text",
        "terms": tuple(terms),
        "verb": verb,
        "argv_template": list(argv_template),
        "inputs": {
            "text": {
                "type": "string",
                "required": True,
                "description": description or "the text the container verb acts on",
            }
        },
        "outputs": {
            output_name: {
                "type": "string",
                "from": "stdout",
                "pattern": output_pattern,
            }
        },
        "oracle": _named,
        "sample_input": {"text": sample_text},
    }
    if verb_candidates:
        entry["verb_candidates"] = tuple(verb_candidates)
    return entry


def cli_template(
    *,
    id: str,
    terms: Sequence[str],
    verb: str,
    argv_template: Sequence[str | dict],
    inputs: Mapping[str, Any],
    outputs: Mapping[str, Any],
    oracle: Callable[[dict], Any],
    sample_input: Mapping[str, Any],
    kind: str = "cli",
    verb_candidates: Sequence[str] | None = None,
    output_protocol: str = "",
    timeout_seconds: int = 60,
    description: str = "",
    service: Mapping[str, Any] | None = None,
) -> dict:
    """A typed CLI template (argument-mapping shape) with a declared oracle.

    ``service`` (optional) declares the capability's warm service: ``argv`` of
    the long-running command plus, when the service must actually SERVE
    (a dashboard), ``network`` (e.g. ``bridge``) and ``ports``
    (``{"container_port": host_port}``). The service spec is data: the builder
    validates the argv; the runtime turns the ports into one ``docker run``.
    """
    entry: dict[str, Any] = {
        "id": id,
        "kind": kind,
        "terms": tuple(terms),
        "verb": verb,
        "argv_template": list(argv_template),
        "inputs": dict(inputs),
        "outputs": dict(outputs),
        "oracle": oracle,
        "sample_input": dict(sample_input),
        "timeout_seconds": int(timeout_seconds),
    }
    if service:
        entry["service"] = dict(service)
    if verb_candidates:
        entry["verb_candidates"] = tuple(verb_candidates)
    if output_protocol:
        entry["output_protocol"] = output_protocol
    if description:
        entry["description"] = description
    return entry


def fixture_template(
    *,
    id: str,
    terms: Sequence[str],
    verb: str,
    argv_template: Sequence[str | dict],
    inputs: Mapping[str, Any],
    outputs: Mapping[str, Any],
    sample_input: Mapping[str, Any],
    expected: Mapping[str, str],
    kind: str = "cli-fixture",
    verb_candidates: Sequence[str] | None = None,
    output_protocol: str = "",
    timeout_seconds: int = 60,
    description: str = "",
    service: Mapping[str, Any] | None = None,
) -> dict:
    """A template whose oracle is a declared golden fixture (never a host twin)."""
    return cli_template(
        id=id,
        terms=terms,
        verb=verb,
        argv_template=argv_template,
        inputs=inputs,
        outputs=outputs,
        oracle=oracle_fixture(sample_input, expected),
        sample_input=sample_input,
        kind=kind,
        verb_candidates=verb_candidates,
        output_protocol=output_protocol,
        timeout_seconds=timeout_seconds,
        description=description,
        service=service,
    )


def load_schema_template(path: str | Path, *, oracle: Callable[[dict], Any] | None = None) -> dict:
    """Load a CLI template declaration from JSON.

    The file is the same shape as :func:`cli_template` / :func:`fixture_template`
    arguments (``oracle`` is never read from the file — it is code, not data).
    ``oracle.kind`` selects the builder:

    * ``fixture`` (default) — needs ``oracle.sample_input`` + ``oracle.expected``;
    * ``host_twin`` — refused here: a host twin is code and cannot ship as JSON.
    """
    source = Path(path)
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AdapterRefusal(f"cannot read template schema {source}: {exc}") from exc
    if not isinstance(data, Mapping):
        raise AdapterRefusal(f"{source} must hold a JSON object (the template fields)")
    required = ("id", "verb", "argv_template", "inputs", "outputs", "sample_input")
    missing = [key for key in required if key not in data]
    if missing:
        raise AdapterRefusal(f"{source} is missing required field(s): {', '.join(missing)}")
    oracle_spec = dict(data.get("oracle") or {})
    kind = str(oracle_spec.get("kind") or "fixture")
    if oracle is not None:
        built = oracle
    elif kind == "fixture":
        sample = dict(oracle_spec.get("sample_input") or data.get("sample_input") or {})
        expected = dict(oracle_spec.get("expected") or {})
        if not expected:
            raise AdapterRefusal(
                f"{source}: oracle.kind=fixture needs oracle.expected "
                "(the golden outputs for sample_input) — nothing is invented"
            )
        built = oracle_fixture(sample, expected, note=str(
            oracle_spec.get("note")
            or f"declared golden fixture from {source.name} (NOT a host twin)"
        ))
    else:
        raise AdapterRefusal(
            f"{source}: oracle.kind={kind!r} cannot be loaded from JSON — "
            "a host twin is code; pass it via load_schema_template(..., oracle=…)"
        )
    return cli_template(
        id=str(data["id"]),
        terms=tuple(data.get("terms") or (data["verb"],)),
        verb=str(data["verb"]),
        argv_template=list(data["argv_template"]),
        inputs=dict(data["inputs"]),
        outputs=dict(data["outputs"]),
        oracle=built,
        sample_input=dict(data["sample_input"]),
        kind=str(data.get("kind") or "cli-fixture"),
        verb_candidates=tuple(data["verb_candidates"]) if data.get("verb_candidates") else None,
        output_protocol=str(data.get("output_protocol") or ""),
        timeout_seconds=int(data.get("timeout_seconds") or 60),
        description=str(data.get("description") or ""),
        service=dict(data.get("service") or {}) or None,
    )


# --------------------------------------------------------------------------- #
# host-twin text families (independent oracles, plain Python)                    #
# --------------------------------------------------------------------------- #


def _upper(text: str) -> str:
    # ASCII-only: `tr '[:lower:]' '[:upper:]'` is ASCII, not Unicode
    return text.upper().encode("ascii", "replace").decode("ascii")


def _char_count(text: str) -> str:
    # wc -c counts BYTES, not codepoints
    return str(len(text.encode("utf-8")))


def _line_count(text: str) -> str:
    # wc -l counts NEWLINES, not splitlines() entries
    if not text:
        return "0"
    return str(text.count("\n") + (0 if text.endswith("\n") else 1))


def _sorted_lines(text: str) -> str:
    # LC_ALL=C: byte-wise sort, not locale-aware
    return "\n".join(sorted(text.splitlines()))


def _unique_lines(text: str) -> str:
    out: list[str] = []
    for line in text.splitlines():
        if not out or out[-1] != line:
            out.append(line)
    return "\n".join(out)


def _first_field(text: str) -> str:
    # cut -d: -f1 maps EVERY line, not just the first
    return "\n".join(
        line.split(":", 1)[0] if line else "" for line in text.splitlines()
    )


def _basename(text: str) -> str:
    return Path(text.strip()).name


def _json_field(payload: dict) -> str:
    """``jq -r .field`` — the host twin is ``json.loads`` + one key."""
    field = str(payload.get("field") or "value")
    try:
        doc = json.loads(str(payload.get("text") or ""))
    except ValueError as exc:
        raise AdapterRefusal(f"jq-field oracle needs valid JSON text: {exc}") from exc
    if not isinstance(doc, Mapping) or field not in doc:
        raise AdapterRefusal(f"jq-field oracle: JSON has no key {field!r}")
    return str(doc[field])


NOTE_TEXT_TWIN = (
    "host twin: computed by Python stdlib from the input text (independent of the container)"
)

TEXT_TEMPLATES: tuple[dict, ...] = (
    text_template(
        id="tr-upper",
        terms=("uppercase", "upper", "capital", "tr", "case"),
        verb="tr",
        argv_template=["sh", "-c", 'printf %s "$0" | tr "[:lower:]" "[:upper:]"', "{text}"],
        output_name="upper",
        output_pattern=r"^[\s\S]*$",
        oracle=text_oracle("tr-upper", _upper, note=NOTE_TEXT_TWIN),
        description="upper-case every character of a string",
    ),
    text_template(
        id="wc-chars",
        terms=("count characters", "character count", "char count", "length", "wc",
               "how many characters"),
        verb="wc",
        argv_template=["sh", "-c", 'printf %s "$0" | wc -c | tr -d " "', "{text}"],
        output_name="chars",
        output_pattern=r"^[0-9]+$",
        oracle=text_oracle("wc-chars", _char_count, note=NOTE_TEXT_TWIN),
        description="count the bytes of a string (ASCII/UTF-8 as the tool counts them)",
    ),
    text_template(
        id="wc-lines",
        terms=("count lines", "line count", "how many lines", "wc -l"),
        verb="wc",
        argv_template=["sh", "-c", 'printf %s "$0" | wc -l | tr -d " "', "{text}"],
        output_name="lines",
        output_pattern=r"^[0-9]+$",
        oracle=text_oracle("wc-lines", _line_count, note=NOTE_TEXT_TWIN),
        description="count the lines of a string",
    ),
    text_template(
        id="sort-lines",
        terms=("sort the lines", "sort lines", "alphabetical", "order lines"),
        verb="sort",
        argv_template=["sh", "-c", 'printf %s "$0" | sort', "{text}"],
        output_name="sorted",
        output_pattern=r"^[\s\S]*$",
        oracle=text_oracle("sort-lines", _sorted_lines, note=NOTE_TEXT_TWIN),
        description="sort the lines of a string (C locale, one line per record)",
    ),
    text_template(
        id="uniq-lines",
        terms=("uniq", "unique lines", "dedupe", "collapse duplicates"),
        verb="uniq",
        argv_template=["sh", "-c", 'printf %s "$0" | uniq', "{text}"],
        output_name="unique",
        output_pattern=r"^[\s\S]*$",
        oracle=text_oracle("uniq-lines", _unique_lines, note=NOTE_TEXT_TWIN),
        description="collapse consecutive duplicate lines",
    ),
    text_template(
        id="cut-first-field",
        terms=("first field", "cut", "column", "before the colon", "split field"),
        verb="cut",
        argv_template=["sh", "-c", 'printf %s "$0" | cut -d: -f1', "{text}"],
        output_name="field",
        output_pattern=r"^[\s\S]*$",
        oracle=text_oracle("cut-first-field", _first_field, note=NOTE_TEXT_TWIN),
        description="the text before the first colon of the first line",
    ),
    text_template(
        id="basename-path",
        terms=("basename", "filename", "file name", "last path segment"),
        verb="basename",
        argv_template=["sh", "-c", 'basename "$0"', "{text}"],
        output_name="name",
        output_pattern=r"^[^\n/]+$",
        oracle=text_oracle("basename-path", _basename, note=NOTE_TEXT_TWIN),
        description="the last path segment of a file path",
    ),
    text_template(
        id="jq-field",
        terms=("json field", "jq", "extract field", "json key", "parse json"),
        verb="jq",
        argv_template=[
            "sh",
            "-c",
            'printf %s "$0" | jq -r --arg f "$1" ".[$f] // empty"',
            "{text}",
            "{field}",
        ],
        output_name="value",
        output_pattern=r"^[\s\S]*$",
        oracle=oracle_host_twin(_json_field, note=NOTE_TEXT_TWIN),
        sample_text='{"value": "skaldir"}',
        description="one top-level string field of a JSON object (jq -r .field)",
    ),
)

#: The jq template's second input (the field name) is not in ``text_template``'s
#: fixed single-input shape — patch it in without a second factory.
TEXT_TEMPLATES = tuple(
    {
        **entry,
        "inputs": {
            **entry["inputs"],
            **(
                {
                    "field": {
                        "type": "string",
                        "required": True,
                        "pattern": r"^[A-Za-z_][A-Za-z0-9_]*$",
                        "description": "the top-level JSON key to read",
                    }
                }
                if entry["id"] == "jq-field"
                else {}
            ),
        },
        "sample_input": (
            {"text": '{"value": "skaldir"}', "field": "value"}
            if entry["id"] == "jq-field"
            else entry["sample_input"]
        ),
    }
    for entry in TEXT_TEMPLATES
)


def all_templates(extra: Sequence[dict] = ()) -> tuple[dict, ...]:
    """This module's families, plus any extras a caller already has."""
    return (*TEXT_TEMPLATES, *tuple(extra))


def match_terms(capability: str, templates: Sequence[dict]) -> dict | None:
    """Best term match of *this* catalogue (the same scoring as verb templates)."""
    text = (capability or "").lower()
    scored: list[tuple[int, dict]] = []
    for template in templates:
        hits = sum(1 for term in template.get("terms") or () if term in text)
        if hits:
            scored.append((hits, template))
    scored.sort(key=lambda pair: (-pair[0], pair[1]["id"]))
    if not scored:
        return None
    top_hits, top = scored[0]
    if len(scored) > 1 and scored[1][0] == top_hits:
        raise AdapterRefusal(
            f"ambiguous capability '{capability}': {top['id']} and "
            f"{scored[1][1]['id']} both match — name one verb explicitly (--verb)"
        )
    return top
