"""``patch`` — targeted find/replace with fuzzy tolerance and a real diff.

The matching ladder, in order, with the strategy that won reported in the
result (never guessed):

1. ``exact`` — the literal byte sequence, once or (with ``replace_all``) every
   time.
2. ``trailing-whitespace`` — the needle matched line-wise with trailing
   whitespace stripped on both sides.
3. ``indentation-flexible`` — both sides stripped of leading whitespace per line,
   so a block that moved into/out of an indented scope still matches. The
   replacement is re-indented to the indentation actually found in the file
   (``reindent`` says by how much).
4. ``similar`` — a ``difflib`` ratio over the same normalized line blocks, only
   when ``fuzzy`` is true and the best window reaches ``fuzzy_min_ratio``; the
   best match must be *clearly* better than the runner-up, otherwise the call
   refuses with ``conflict`` and names the candidate lines (a patch that picks
   one of two nearly identical regions is how silent damage happens).

``dry_run`` runs the whole ladder and the diff and writes nothing. ``ambient``
failures — outside the root, a denied path, a binary or oversized file — are
refusals, not best efforts. The result always carries a unified diff (truncated
per the contract's rule), the sha256 before/after, and the strategy/score that
matched.
"""

from __future__ import annotations

import difflib
import hashlib
import re
from pathlib import Path

from . import sandbox
from .contract import (
    FilesystemPermission,
    NetworkPermission,
    Permission,
    ToolContext,
    ToolContract,
    ToolResult,
    TruncationRule,
    enforce_input,
    failure,
)

NAME = "patch"
MODULE = "skaldir.tools_core.patch_tool"

_MAX_PATCH_BYTES = 5 * 1024 * 1024
_AMBIGUITY_MARGIN = 0.02

INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "minLength": 1,
            "maxLength": 4096,
            "description": "file path relative to the workspace root",
        },
        "old_string": {
            "type": "string",
            "minLength": 1,
            "maxLength": 200000,
            "description": "the text to find",
        },
        "new_string": {
            "type": "string",
            "maxLength": 200000,
            "description": "the replacement text",
        },
        "replace_all": {
            "type": "boolean",
            "description": "replace every occurrence (default false; >1 without it is a conflict)",
        },
        "fuzzy": {
            "type": "boolean",
            "description": "allow the normalized/similar strategies (default true)",
        },
        "fuzzy_min_ratio": {
            "type": "number",
            "minimum": 0.5,
            "maximum": 1.0,
            "description": "minimum difflib ratio for the similar strategy (default 0.8)",
        },
        "dry_run": {
            "type": "boolean",
            "description": "compute the diff and the match, write nothing",
        },
        "context_lines": {
            "type": "integer",
            "minimum": 0,
            "maximum": 20,
            "description": "diff context lines (default 3)",
        },
    },
    "required": ["path", "old_string", "new_string"],
    "additionalProperties": False,
}

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "path as addressed"},
        "abs_path": {"type": "string", "description": "absolute path"},
        "applied": {"type": "boolean", "description": "the file was rewritten"},
        "dry_run": {"type": "boolean", "description": "the call only computed"},
        "strategy": {
            "type": "string",
            "description": "exact | trailing-whitespace | indentation-flexible | similar",
        },
        "score": {"type": "number", "description": "match ratio (1.0 for the literal strategies)"},
        "replacements": {"type": "integer", "description": "occurrences replaced"},
        "matches": {"type": "integer", "description": "occurrences found"},
        "reindent": {
            "type": "string",
            "description": "indentation prefix applied to the replacement",
        },
        "diff": {"type": "string", "description": "unified diff (truncated per the rule)"},
        "diff_truncated": {"type": "boolean", "description": "the diff was cut"},
        "spill": {"type": "array", "description": "paths of spilled full diffs"},
        "before_sha256": {"type": "string", "description": "sha256 before the patch"},
        "after_sha256": {"type": "string", "description": "sha256 after the patch"},
        "bytes_before": {"type": "integer", "description": "file size before"},
        "bytes_after": {"type": "integer", "description": "file size after"},
    },
    "required": [
        "path",
        "abs_path",
        "applied",
        "strategy",
        "replacements",
        "diff",
        "before_sha256",
    ],
    "additionalProperties": False,
}

CONTRACT = ToolContract(
    name=NAME,
    title="Patch a file (fuzzy find/replace with a diff)",
    description=(
        "Replace a target string in a file with a four-step matching ladder "
        "(literal, trailing-whitespace, indentation-flexible, similar) and return a "
        "unified diff; over-many matches refuse instead of guessing."
    ),
    module=MODULE,
    input_schema=INPUT_SCHEMA,
    output_schema=OUTPUT_SCHEMA,
    permission=Permission(
        filesystem=FilesystemPermission(
            scope="workspace",
            mode="read-write",
            roots=("<workspace>",),
            deny_globs=sandbox.WRITE_DENY_GLOBS
            + tuple(f"{name}/**" for name in sandbox.WRITE_DENY_DIRS),
        ),
        network=NetworkPermission(mode="none"),
    ),
    truncation=TruncationRule(max_bytes=32768, strategy="head+tail", spill=True),
    timeout_s=20,
    tags=("filesystem", "write", "diff"),
    mutates=True,
    smoke={
        "setup": [
            {"tool": "write_file", "input": {"path": "smoke-patch.txt", "content": "alpha\nbeta\n"}}
        ],
        "input": {"path": "smoke-patch.txt", "old_string": "beta", "new_string": "gamma"},
        "expect": {"applied": True, "strategy": "exact", "diff_contains": "+gamma"},
    },
)


def _normalized(line: str, *, indent: bool) -> str:
    stripped = line.rstrip()
    return stripped.lstrip() if indent else stripped


def _block(haystack_lines: list[str], start: int, length: int, *, indent: bool) -> list[str]:
    return [_normalized(line, indent=indent) for line in haystack_lines[start : start + length]]


def _find_windows(
    haystack_lines: list[str], needle_lines: list[str], *, indent: bool
) -> list[tuple[int, float]]:
    """Every line window whose normalized block equals the needle block."""
    wanted = [_normalized(line, indent=indent) for line in needle_lines]
    span = len(wanted)
    hits: list[tuple[int, float]] = []
    for start in range(0, len(haystack_lines) - span + 1):
        if _block(haystack_lines, start, span, indent=indent) == wanted:
            hits.append((start, 1.0))
    return hits


def _find_similar(
    haystack_lines: list[str], needle_lines: list[str], *, indent: bool, floor: float
) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    """Best *similar* windows (≥ floor) and the close runner-ups (ambiguity)."""
    wanted = [_normalized(line, indent=indent) for line in needle_lines]
    span = len(wanted)
    scored: list[tuple[int, float]] = []
    for start in range(0, len(haystack_lines) - span + 1):
        block = _block(haystack_lines, start, span, indent=indent)
        first = difflib.SequenceMatcher(None, wanted[0], block[0]).ratio()
        if first < 0.5:  # cheap gate: the first line must look alike
            continue
        ratio = difflib.SequenceMatcher(None, "\n".join(wanted), "\n".join(block)).ratio()
        if ratio >= floor:
            scored.append((start, ratio))
    scored.sort(key=lambda item: (-item[1], item[0]))
    if not scored:
        return [], []
    best = scored[0][1]
    top = [item for item in scored if item[1] >= best - _AMBIGUITY_MARGIN]
    close = [item for item in scored[1:] if item[1] >= best - _AMBIGUITY_MARGIN]
    return (top if len(top) == 1 else scored[:1]), close


def _line_offsets(text: str) -> tuple[list[str], list[int]]:
    lines = text.splitlines(keepends=True)
    offsets: list[int] = []
    position = 0
    for line in lines:
        offsets.append(position)
        position += len(line)
    offsets.append(position)
    return lines, offsets


def _indent_of(line: str) -> str:
    return line[: len(line) - len(line.lstrip())]


def _reindent(text: str, *, needle_indent: str, target_indent: str) -> str:
    if needle_indent == target_indent:
        return text
    lines = text.splitlines(keepends=True)
    adjusted: list[str] = []
    for line in lines:
        if not line.strip():
            adjusted.append(line)
            continue
        body = line
        if needle_indent and body.startswith(needle_indent):
            body = body[len(needle_indent) :]
        adjusted.append(target_indent + body)
    return "".join(adjusted)


def patch_text(
    root: str | Path,
    path_value: str,
    old_string: str,
    new_string: str,
    *,
    replace_all: bool = False,
    fuzzy: bool = True,
    fuzzy_min_ratio: float = 0.8,
    dry_run: bool = False,
    context_lines: int = 3,
    runtime: str | Path | None = None,
) -> ToolResult:
    """Apply one patch under the ``patch`` policy (shared with the smoke runner)."""
    path, problem = sandbox.candidate_path(root, path_value)
    if path is None:
        return failure(NAME, "blocked", problem, data={"path": str(path_value), "abs_path": ""})
    label = sandbox.relative_label(root, path)
    denied = sandbox.write_denied(root, path)
    if denied:
        return failure(
            NAME,
            "blocked",
            f"patching {label} is refused by the deny rule {denied!r}",
            data={"path": label, "abs_path": str(path), "deny_glob": denied},
        )
    if not path.is_file():
        return failure(
            NAME,
            "not_found",
            f"file does not exist: {label}",
            data={"path": label, "abs_path": str(path)},
        )
    raw = path.read_bytes()
    if len(raw) > _MAX_PATCH_BYTES:
        return failure(
            NAME,
            "blocked",
            f"{label} is {len(raw)} bytes — above the {_MAX_PATCH_BYTES}-byte patch limit",
            data={"path": label, "size_bytes": len(raw)},
        )
    if sandbox.is_probably_binary(raw):
        return failure(
            NAME, "blocked", f"{label} is a binary file", data={"path": label, "shape": "binary"}
        )
    text = raw.decode("utf-8", "replace")
    before_sha = hashlib.sha256(raw).hexdigest()
    lines, offsets = _line_offsets(text)
    needle_lines = old_string.splitlines()
    if not needle_lines:
        return failure(NAME, "usage", "old_string has no lines", data={"path": label})

    strategy = ""
    score = 1.0
    spans: list[tuple[int, int]] = []
    replacements_text = new_string
    reindent_note = ""
    occurrence_lines: list[int] = []

    occurrences = text.count(old_string)
    if occurrences:
        strategy = "exact"
        if occurrences > 1 and not replace_all:
            return failure(
                NAME,
                "conflict",
                f"old_string occurs {occurrences} times in {label}; pass replace_all: true "
                "or a longer old_string",
                data={
                    "path": label,
                    "matches": occurrences,
                    "occurrence_lines": [
                        1 + text[:index].count("\n") for index in _find_all(text, old_string)
                    ],
                },
            )
        spans = [(index, index + len(old_string)) for index in _find_all(text, old_string)]
    else:
        hits: list[tuple[int, float]] = []
        for candidate_indent in (False, True):
            hits = _find_windows(lines, needle_lines, indent=candidate_indent)
            if hits:
                strategy = "trailing-whitespace" if not candidate_indent else "indentation-flexible"
                matched_indent = _indent_of(lines[hits[0][0]]) if candidate_indent else ""
                needle_indent = _indent_of(needle_lines[0]) if candidate_indent else ""
                replacements_text = _reindent(
                    new_string, needle_indent=needle_indent, target_indent=matched_indent
                )
                if candidate_indent:
                    reindent_note = f"{matched_indent!r}"
                break
        if not hits and fuzzy:
            top, close = _find_similar(
                lines, needle_lines, indent=True, floor=float(fuzzy_min_ratio)
            )
            if top and close:
                return failure(
                    NAME,
                    "conflict",
                    f"{len(close) + 1} regions of {label} match within "
                    f"{_AMBIGUITY_MARGIN} of the best ratio — refusing to guess",
                    data={
                        "path": label,
                        "candidates": [
                            {"line": item[0] + 1, "ratio": round(item[1], 3)}
                            for item in [*top, *close]
                        ],
                    },
                )
            if top:
                strategy = "similar"
                hits = top
                score = round(top[0][1], 4)
                replacements_text = new_string
        if not hits:
            return failure(
                NAME,
                "not_found",
                f"old_string was not found in {label} (tried literal, trailing-whitespace, "
                "indentation-flexible" + (", similar" if fuzzy else "") + ")",
                data={"path": label, "fuzzy": bool(fuzzy)},
            )
        if len(hits) > 1 and not replace_all:
            return failure(
                NAME,
                "conflict",
                f"old_string matches {len(hits)} regions in {label}; pass replace_all: true "
                "or a longer old_string",
                data={
                    "path": label,
                    "matches": len(hits),
                    "occurrence_lines": [item[0] + 1 for item in hits],
                },
            )
        spans = sorted(
            (offsets[item[0]], offsets[min(item[0] + len(needle_lines), len(offsets) - 1)])
            for item in hits
        )
        occurrence_lines = [item[0] + 1 for item in hits]

    patched = text
    # The line-block strategies match whole lines *including* their newline. A
    # replacement that does not end a line must not silently join the line below
    # it: the matched region's trailing newline is preserved.
    if strategy != "exact" and text[max(0, spans[0][1] - 1) : spans[0][1]] == "\n":
        if not replacements_text.endswith("\n"):
            replacements_text += "\n"
    for start, end in sorted(spans, reverse=True):
        patched = patched[:start] + replacements_text + patched[end:]
    diff = "".join(
        difflib.unified_diff(
            text.splitlines(keepends=True),
            patched.splitlines(keepends=True),
            fromfile=f"a/{label}",
            tofile=f"b/{label}",
            n=max(0, int(context_lines)),
        )
    )
    report = sandbox.enforce_truncation(diff, CONTRACT.truncation, label="diff", runtime=runtime)
    applied = False
    after_sha = before_sha
    if not dry_run:
        path.write_text(patched, encoding="utf-8")
        after_sha = hashlib.sha256(path.read_bytes()).hexdigest()
        applied = True
    return ToolResult(
        tool=NAME,
        ok=True,
        data={
            "path": label,
            "abs_path": str(path),
            "applied": applied,
            "dry_run": bool(dry_run),
            "strategy": strategy,
            "score": float(score),
            "replacements": len(spans),
            "matches": len(spans),
            "reindent": reindent_note,
            "diff": report["text"],
            "diff_truncated": bool(report["truncated"]),
            "spill": [report["spill_path"]] if report["spill_path"] else [],
            "before_sha256": before_sha,
            "after_sha256": after_sha,
            "bytes_before": len(raw),
            "bytes_after": len(patched.encode("utf-8")),
            "occurrence_lines": occurrence_lines,
        },
        evidence=[
            {"ref": str(path), "kind": "file", "note": f"{strategy}, {len(spans)} replacement(s)"}
        ],
    )


def _find_all(text: str, needle: str) -> list[int]:
    """All (non-overlapping) start offsets of ``needle``."""
    return [match.start() for match in re.finditer(re.escape(needle), text)]


def run(args: dict, ctx: ToolContext) -> ToolResult:
    refusal = enforce_input(CONTRACT, args)
    if refusal is not None:
        return refusal
    if str(args["old_string"]) == str(args["new_string"]):
        return failure(NAME, "usage", "old_string and new_string are identical (no-op patch)")
    return patch_text(
        ctx.root,
        str(args["path"]),
        str(args["old_string"]),
        str(args["new_string"]),
        replace_all=bool(args.get("replace_all", False)),
        fuzzy=bool(args.get("fuzzy", True)),
        fuzzy_min_ratio=float(args.get("fuzzy_min_ratio", 0.8) or 0.8),
        dry_run=bool(args.get("dry_run", False)),
        context_lines=int(args.get("context_lines", 3) or 0),
        runtime=ctx.runtime,
    )


RUNNERS = {NAME: run}

__all__ = [
    "CONTRACT",
    "INPUT_SCHEMA",
    "MODULE",
    "NAME",
    "OUTPUT_SCHEMA",
    "RUNNERS",
    "patch_text",
    "run",
]
