"""Tool-result archiving — the biggest token lever in the runtime.

A browser dump, a container log, a test transcript or a computer-use trace is
*bulk observation*: the model needs to know it exists, what it is and where the
exact bytes live. It does **not** need all of it inside the prompt — and paying
for it there is what makes a long run impossible.

The rule this module implements, in one sentence: **the full result goes to a
durable, content-addressed artifact; the hot context gets an id, a digest and a
short summary — nothing else.**

* **Content-addressed, so retrieval is byte-identical by construction.** The
  artifact is ``<state_dir>/tool_archive/<sha256>.txt`` (or ``.json`` when the
  payload is structured). The name *is* the digest of the bytes, so
  :meth:`ToolArchive.retrieve` cannot return something else, and the same result
  archived twice is stored once (:meth:`ToolArchive.put` is idempotent).
* **The hot form is honest.** The short text says that the result was archived,
  gives the id, the digest, the real byte/char count, and a bounded head of the
  payload. It never says "truncated" and stops there — the old
  ``… [truncated; sha256 of the full result below]`` was a dead end: the model was
  told a digest existed and could not ask for the bytes.
* **Bounded by a threshold, decided by size.** Below ``threshold_chars`` the
  payload travels inline exactly as before, so a small, decisive result
  (``exit_code: 0``) is never hidden behind an indirection that costs more than
  it saves.

The archive is written under the run's own state dir and is never mutated after
creation: a resumed run reads the same bytes the first run stored.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "ARCHIVE_DIRNAME",
    "DEFAULT_THRESHOLD_CHARS",
    "DEFAULT_SUMMARY_CHARS",
    "RETRIEVE_MAX_CHARS",
    "RETRIEVE_TOOL_NAME",
    "ArchiveError",
    "ArchivedResult",
    "ToolArchive",
    "archive_dir",
    "summarise_payload",
]

#: where the artifacts live, under the run's state dir
ARCHIVE_DIRNAME = "tool_archive"

#: The tool name the loop offers when an archive is attached. The hot form tells the
#: model ``tool_archive:<sha256>``; without a tool that resolves that ref the model was
#: told a digest existed and could not ask for the bytes — the archive would turn a
#: large result into an unreachable one. This is the read half of the same lever.
RETRIEVE_TOOL_NAME = "read_archive"

#: How much of an archived payload one retrieval returns by default. It is **below**
#: ``DEFAULT_THRESHOLD_CHARS`` on purpose: a retrieval that came back bigger than the
#: archive threshold would be archived again on the way to the prompt, and the model
#: would be handed the same ref it just asked to resolve — an unreadable payload with
#: extra steps. A caller that wants more asks for more explicitly (``max_chars``), and
#: pays the truncation honestly.
RETRIEVE_MAX_CHARS = 4000

#: A payload at or below this size stays inline. Chosen so that a decisive small
#: result — a status line, a short diff, a single path — is never turned into an
#: indirection whose id and summary cost more than the bytes saved.
DEFAULT_THRESHOLD_CHARS = 6000

#: how much of the head the hot form carries back
DEFAULT_SUMMARY_CHARS = 500

# --------------------------------------------------------------------------- #
# shapes that are *bulk by nature* — the ones the task names explicitly        #
# --------------------------------------------------------------------------- #

#: Tool names/sources whose output is bulk observation. The list is not a filter
#: that *hides* anything: it is what makes the summary say something useful
#: ("the head of a container log") instead of a generic truncation note.
BULK_SOURCES: dict[str, str] = {
    "browser": "a browser dump",
    "browser_dump": "a browser dump",
    "docker": "container output",
    "docker_logs": "container logs",
    "container": "container output",
    "tests": "test output",
    "pytest": "test output",
    "test_output": "test output",
    "computer_use": "a computer-use trace",
    "cu_trace": "a computer-use trace",
    "trace": "a trace",
    "logs": "a log",
    "log": "a log",
    "fetch": "a fetched page",
    "web_fetch": "a fetched page",
}


class ArchiveError(RuntimeError):
    """The archive could not be written or read — reported, never swallowed."""


def archive_dir(state_dir: str | Path) -> Path:
    return Path(state_dir) / ARCHIVE_DIRNAME


def _digest_bytes(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def _atomic_write(path: Path, blob: bytes) -> None:
    """Write atomically (temp + fsync + replace) — the archive is an evidence store."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=path.suffix)
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(blob)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _lines(text: str) -> int:
    return text.count("\n") + (1 if text and not text.endswith("\n") else 0)


def _shape(value: Any) -> str:
    if isinstance(value, dict):
        keys = list(value)
        head = ", ".join(str(key) for key in keys[:6])
        more = "" if len(keys) <= 6 else f", +{len(keys) - 6} more"
        return f"an object with {len(keys)} key(s): {head}{more}"
    if isinstance(value, (list, tuple)):
        return f"a list of {len(value)} item(s)"
    if isinstance(value, str):
        return f"a string of {len(value)} character(s)"
    return f"a {type(value).__name__}"


def _bulk_label(source: str) -> str:
    """Map a tool/source name to a readable phrase, tolerating the runtime's spellings.

    ``run_tests``, ``tests`` and ``pytest`` are the same lever; the label exists so the
    summary says *what* was archived rather than a generic note. An unknown name is
    left alone — inventing a category would be worse than naming the tool.
    """
    key = str(source or "").strip().lower()
    if not key:
        return ""
    if key in BULK_SOURCES:
        return BULK_SOURCES[key]
    for alias, label in BULK_SOURCES.items():
        if alias in key:
            return label
    return ""


def summarise_payload(value: Any, *, source: str = "", limit: int = DEFAULT_SUMMARY_CHARS) -> str:
    """A short, structural description of a payload — deterministic, no model call.

    The summary answers three questions the model actually has: *what is this*,
    *how big is it*, and *what does it look like at the start*. It is bounded by
    ``limit`` so the hot form can never itself become the bulk it replaced.
    """
    kind = _bulk_label(source)
    label = f"{kind} " if kind else ""
    if isinstance(value, str):
        body = value
    else:
        body = json.dumps(value, ensure_ascii=False, default=str)
    head = body[: max(0, int(limit))]
    head = head.replace("\r", "")
    ellipsis = " …" if len(body) > len(head) else ""
    return f"{label}{_shape(value)}, {len(body)} char(s), {_lines(body)} line(s): {head}{ellipsis}"


@dataclass
class ArchivedResult:
    """What the hot context carries instead of the full payload."""

    ref: str
    digest: str
    path: str
    chars: int
    bytes: int
    summary: str
    media: str = "text/plain"
    source: str = ""
    token_estimate_before: int = 0
    token_estimate_after: int = 0

    @property
    def saved_tokens(self) -> int:
        return max(0, self.token_estimate_before - self.token_estimate_after)

    @property
    def saved_chars(self) -> int:
        return max(0, self.chars - len(self.summary))

    def to_wire(self) -> dict[str, Any]:
        return {
            "ref": self.ref,
            "sha256": self.digest,
            "path": self.path,
            "chars": int(self.chars),
            "bytes": int(self.bytes),
            "summary": self.summary,
            "media": self.media,
            "source": self.source,
            "tokens_before": int(self.token_estimate_before),
            "tokens_after": int(self.token_estimate_after),
            "tokens_saved": int(self.saved_tokens),
        }


class ToolArchive:
    """The content-addressed store of full tool results.

    One instance per run (or per state dir). Cheap to construct and safe to use
    from several threads: :meth:`put` writes through a temp file and ``os.replace``,
    so two workers archiving the same bytes converge on the same artifact.
    """

    def __init__(
        self,
        state_dir: str | Path,
        *,
        threshold_chars: int = DEFAULT_THRESHOLD_CHARS,
        summary_chars: int = DEFAULT_SUMMARY_CHARS,
        enabled: bool = True,
    ) -> None:
        self.root = archive_dir(state_dir)
        self.threshold_chars = max(1, int(threshold_chars))
        self.summary_chars = max(0, int(summary_chars))
        self.enabled = bool(enabled)
        self._puts = 0
        self._deduped = 0
        self.learned: list[dict[str, Any]] = []

    # -- writing ----------------------------------------------------------- #

    def should_archive(self, text: str) -> bool:
        return bool(self.enabled) and len(text) > self.threshold_chars

    def put(
        self,
        payload: Any,
        *,
        source: str = "",
        media: str = "text/plain",
    ) -> ArchivedResult:
        """Archive one full payload and return the hot form's facts.

        ``payload`` is serialised exactly the way the hot form would have carried
        it (JSON for anything structured, raw text for a string) so the archived
        bytes and the inline bytes are *the same bytes* — the before/after token
        numbers then describe a real substitution, not two different encodings.
        """
        if isinstance(payload, str):
            text = payload
            raw = text.encode("utf-8")
        else:
            text = json.dumps(payload, ensure_ascii=False, default=str)
            raw = text.encode("utf-8")
        digest = _digest_bytes(raw)
        suffix = ".json" if not isinstance(payload, str) else ".txt"
        path = self.root / f"{digest}{suffix}"
        existed = path.exists()
        if not existed:
            try:
                _atomic_write(path, raw)
            except OSError as exc:
                raise ArchiveError(f"could not write the archive artifact: {exc}") from exc
        self._puts += 1
        if existed:
            self._deduped += 1
        summary = summarise_payload(payload, source=source, limit=self.summary_chars)
        result = ArchivedResult(
            ref=f"tool_archive:{digest}",
            digest=digest,
            path=str(path),
            chars=len(text),
            bytes=len(raw),
            summary=summary,
            media=media,
            source=str(source or ""),
            token_estimate_before=_estimate_tokens(text),
            token_estimate_after=_estimate_tokens(summary),
        )
        self.learned.append(result.to_wire())
        return result

    # -- reading ----------------------------------------------------------- #

    def path_for(self, ref_or_digest: str) -> Path:
        """Resolve a ref or a bare digest to the artifact path (no existence check)."""
        raw = str(ref_or_digest or "").strip()
        if raw.startswith("tool_archive:"):
            raw = raw.split(":", 1)[1]
        if not raw:
            raise ArchiveError("an empty ref names no artifact")
        for suffix in (".txt", ".json"):
            candidate = self.root / f"{raw}{suffix}"
            if candidate.exists():
                return candidate
        raise ArchiveError(f"no archived tool result for {ref_or_digest!r} in {self.root}")

    def retrieve(self, ref_or_digest: str) -> bytes:
        """The exact bytes that were archived — byte-identical, or an error.

        The digest is re-checked on read: a corrupted artifact is *reported*, never
        silently returned as if it were the evidence.
        """
        path = self.path_for(ref_or_digest)
        blob = path.read_bytes()
        if _digest_bytes(blob) != path.stem:
            raise ArchiveError(
                f"the artifact {path} does not hash to its own name — it was modified "
                "after it was archived"
            )
        return blob

    def retrieve_text(self, ref_or_digest: str) -> str:
        return self.retrieve(ref_or_digest).decode("utf-8", errors="replace")

    # -- reporting --------------------------------------------------------- #

    def stats(self) -> dict[str, Any]:
        return {
            "puts": int(self._puts),
            "deduped": int(self._deduped),
            "threshold_chars": int(self.threshold_chars),
            "summary_chars": int(self.summary_chars),
            "enabled": bool(self.enabled),
            "root": str(self.root),
        }


def _estimate_tokens(text: str) -> int:
    """The loop's own estimator (``chars/4``) — labelled as an estimate everywhere.

    Deliberately not a tokenizer: telemetry's rule is that a number says where it
    came from, and this one says ``estimate/chars-4``.
    """
    return max(0, len(str(text or "")) // 4)
