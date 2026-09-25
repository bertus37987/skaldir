"""Transport-agnostic Jev request/response handling — port of ``src/request.ts``.

Ported from ``fast-jev-compaction`` (Claude Code plugin 0.3.0 / npm package 0.2.0,
commit ``e3f262a7f4d42bd8dd32ced30d26176f7cb545b0``), MIT License,
Copyright (c) 2025 — https://github.com/tamaratran/fast-jev-compaction

Full upstream license text: ``UPSTREAM_LICENSE`` in this package.

The port keeps the wire shape (``{model, state, questions}``, ``Authorization:
Bearer <key>``) and the validation rules, so a Skaldir provider adapter can build
the body here instead of guessing it. The upstream ``JevClient`` (a ``fetch``
POST to TypeSafe) is deliberately **not** ported: Skaldir injects its own LLM
provider through the ``JevAsker`` protocol, and no API key lives in this package.
"""

from __future__ import annotations

import json
import math
from typing import Any

from .errors import JevAnswerError, JevRequestError
from .types import JevRequest, JevResponse, wire_json

SYSTEM_ONE_URL = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODEL = "jev-latest"


def build_jev_request(
    state: Any,
    questions: Any,
    *,
    api_key: str,
    model: str | None = None,
    base_url: str | None = None,
) -> JevRequest:
    """The HTTP request for one Jev call, for any fetch-like transport.

    ``state`` and ``questions`` are the JSON-ready objects from
    :func:`~skaldir.jevcomp.compact.ask_batch`; the body key order matches upstream.
    """
    return JevRequest(
        url=base_url or SYSTEM_ONE_URL,
        method="POST",
        headers={
            "authorization": f"Bearer {api_key}",
            "content-type": "application/json",
        },
        body=wire_json({"model": model or DEFAULT_MODEL, "state": state, "questions": questions}),
    )


def parse_jev_response(status: int, ok: bool, text: str) -> JevResponse:
    """Validate a Jev response body; raise on anything but an ``answers`` object."""
    if not ok:
        raise JevRequestError(f"Jev request failed ({status}): {text[:200]}")
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise JevRequestError("Jev returned malformed JSON") from exc
    if (
        parsed is None
        or not isinstance(parsed, dict)
        or "answers" not in parsed
        or parsed["answers"] is None
        or not isinstance(parsed["answers"], dict)
    ):
        raise JevRequestError("Jev response is missing answers")
    return JevResponse(
        answers=parsed["answers"],
        model=parsed.get("model") if isinstance(parsed.get("model"), str) else None,
        usage=parsed.get("usage") if isinstance(parsed.get("usage"), dict) else None,
        raw=parsed,
    )


def noul_answer(answers: Any, name: str) -> float:
    """The ``noul`` probability of one answer; raises when it is not there."""
    answer: Any = None
    if isinstance(answers, dict):
        answer = answers.get(name)
    elif answers is not None and hasattr(answers, "get"):
        answer = answers.get(name)
    value: Any = None
    if isinstance(answer, dict):
        value = answer.get("noul")
    elif answer is not None and hasattr(answer, "noul"):
        value = getattr(answer, "noul", None)
    if (
        value is None
        or isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
    ):
        raise JevAnswerError(f"Invalid Jev answer for {name}")
    return float(value)
