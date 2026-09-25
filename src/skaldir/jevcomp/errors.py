"""Typed failures of the Jev compaction engine.

Upstream throws plain ``Error`` objects with a fixed message text. The port keeps
the message text byte-for-byte (tests and the fallback path match on it) but adds
the class so a caller can decide what to fall back to without parsing strings.

Every failure is meant to be **caught by the caller**: the upstream design is
"fail loud, let the caller fall back to its built-in compaction".
"""

from __future__ import annotations


class JevCompactionError(RuntimeError):
    """Base of every compaction failure."""


class JevRequestError(JevCompactionError):
    """The Jev request failed (HTTP status) or the answer body was not usable."""


class JevAnswerError(JevCompactionError):
    """An answer is missing, is not a ``noul`` answer, or is not a finite number."""


class StateFittingError(JevCompactionError):
    """The history cannot be fitted into the token budget, so no request was made.

    Covers both upstream failures: a history that stays too large after every
    fitting stage, and a batch of questions that does not fit between the state
    and ``max_request_tokens``.
    """
