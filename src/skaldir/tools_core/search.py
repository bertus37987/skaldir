"""``search_files`` — regex content search *and* file-name glob, one tool.

Two targets, one shape of answer:

* ``target: "content"`` — a regular expression (``re``, no ripgrep dependency, so
  the tool works on a bare installation) against file contents, with optional
  ``file_glob`` narrowing, optional context lines and three output modes
  (``content`` / ``files_only`` / ``count``).
* ``target: "files"`` — a glob against file *names*.

The traversal is bounded on purpose: ``max_files`` (default 20000) stops the
walk, ``max_file_bytes`` (default 2 MB) stops per-file reading, binary files are
skipped, and build/cache directories (``.git``, ``node_modules``, ``.venv``,
``__pycache__``, …) are never descended into — the list is reported in the
result, so an empty answer can be read as "nothing found" rather than "not
looked for". An invalid regular expression is a usage error naming the reason,
never a silent empty result.
"""

from __future__ import annotations

import fnmatch
import os
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

NAME = "search_files"
MODULE = "skaldir.tools_core.search"

_MAX_MATCH_TEXT = 400

INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "pattern": {
            "type": "string",
            "minLength": 1,
            "maxLength": 512,
            "description": "regular expression (content target) or glob (files target)",
        },
        "target": {
            "type": "string",
            "enum": ["content", "files"],
            "description": "search inside files (default) or match file names",
        },
        "path": {
            "type": "string",
            "maxLength": 4096,
            "description": (
                "directory to search, relative to the workspace root (default: the root)"
            ),
        },
        "file_glob": {
            "type": "string",
            "maxLength": 256,
            "description": "only consider files matching this glob, e.g. '*.py'",
        },
        "output_mode": {
            "type": "string",
            "enum": ["content", "files_only", "count"],
            "description": "match lines (default) | file paths | match counts per file",
        },
        "context": {
            "type": "integer",
            "minimum": 0,
            "maximum": 10,
            "description": "context lines before/after each content match (default 0)",
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 1000,
            "description": "maximum returned matches (default 50)",
        },
        "offset": {
            "type": "integer",
            "minimum": 0,
            "maximum": 100000,
            "description": "skip the first N matches (default 0)",
        },
        "case_sensitive": {
            "type": "boolean",
            "description": "case-sensitive matching (default true)",
        },
        "max_files": {
            "type": "integer",
            "minimum": 1,
            "maximum": 200000,
            "description": "stop the walk after this many files (default 20000)",
        },
        "max_file_bytes": {
            "type": "integer",
            "minimum": 1024,
            "maximum": 20000000,
            "description": "skip files larger than this (default 2000000)",
        },
        "order": {
            "type": "string",
            "enum": ["discovery", "modified"],
            "description": "path order (default) or newest-first",
        },
    },
    "required": ["pattern"],
    "additionalProperties": False,
}

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "pattern": {"type": "string", "description": "the pattern as given"},
        "target": {"type": "string", "description": "content | files"},
        "path": {"type": "string", "description": "directory that was searched"},
        "matches": {"type": "array", "description": "match records (shape depends on output_mode)"},
        "count": {"type": "integer", "description": "number of returned matches"},
        "total_matches": {"type": "integer", "description": "matches found before paging"},
        "files_scanned": {"type": "integer", "description": "files inspected"},
        "files_matched": {"type": "integer", "description": "files with at least one match"},
        "truncated": {"type": "boolean", "description": "the walk or the limit cut the result"},
        "engine": {"type": "string", "description": "the search engine that ran"},
        "skipped_dirs": {"type": "array", "description": "directory names never descended into"},
    },
    "required": ["pattern", "target", "path", "matches", "count", "files_scanned", "truncated"],
    "additionalProperties": False,
}

CONTRACT = ToolContract(
    name=NAME,
    title="Search file contents or file names",
    description=(
        "Regex content search with optional glob narrowing, context lines and three output "
        "modes, or a file-name glob search — both with a bounded walk and a reported engine."
    ),
    module=MODULE,
    input_schema=INPUT_SCHEMA,
    output_schema=OUTPUT_SCHEMA,
    permission=Permission(
        filesystem=FilesystemPermission(
            scope="workspace", mode="read", roots=("<workspace>",), deny_globs=()
        ),
        network=NetworkPermission(mode="none"),
    ),
    truncation=TruncationRule(max_bytes=8192, strategy="head", max_lines=1000, spill=False),
    timeout_s=60,
    tags=("search", "filesystem", "read"),
    mutates=False,
    smoke={
        "setup": [
            {
                "tool": "write_file",
                "input": {
                    "path": "smoke-search.txt",
                    "content": "needle:core-smoke\nsecond line\n",
                },
            }
        ],
        "input": {"pattern": "core-smoke", "output_mode": "content"},
        "expect": {"min_count": 1},
    },
)


def _iter_files(
    root: Path, *, max_files: int, skip_dirs: tuple[str, ...]
) -> tuple[list[Path], bool, list[str]]:
    """Deterministic bounded walk of ``root`` (directories are never followed twice)."""
    found: list[Path] = []
    skipped: set[str] = set()
    cut = False
    skip = set(skip_dirs)
    for current, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        kept = [name for name in dirnames if name not in skip and not name.startswith(".git")]
        for name in dirnames:
            if name not in kept:
                skipped.add(name)
        dirnames[:] = kept
        for name in sorted(filenames):
            found.append(Path(current) / name)
            if len(found) >= max_files:
                return found, True, sorted(skipped)
    return found, cut, sorted(skipped)


def run(args: dict, ctx: ToolContext) -> ToolResult:
    refusal = enforce_input(CONTRACT, args)
    if refusal is not None:
        return refusal
    root = Path(ctx.root)
    base, problem = sandbox.candidate_path(root, args.get("path", ".") or ".")
    if base is None:
        return failure(NAME, "blocked", problem, data={"path": str(args.get("path", ""))})
    if not base.exists():
        return failure(
            NAME,
            "not_found",
            f"search path does not exist: {sandbox.relative_label(root, base)}",
            data={"path": sandbox.relative_label(root, base)},
        )
    pattern = str(args["pattern"])
    target = str(args.get("target", "content") or "content")
    output_mode = str(args.get("output_mode", "content") or "content")
    file_glob = str(args.get("file_glob", "") or "")
    context = int(args.get("context", 0) or 0)
    limit = int(args.get("limit", 50) or 50)
    offset = int(args.get("offset", 0) or 0)
    max_files = int(args.get("max_files", 20000) or 20000)
    max_file_bytes = int(args.get("max_file_bytes", 2000000) or 2000000)
    case_sensitive = bool(args.get("case_sensitive", True))
    order = str(args.get("order", "discovery") or "discovery")
    label = sandbox.relative_label(root, base) or "."

    regex: re.Pattern[str] | None = None
    if target == "content":
        try:
            regex = re.compile(pattern, 0 if case_sensitive else re.IGNORECASE)
        except re.error as exc:
            return failure(
                NAME,
                "usage",
                f"invalid regular expression {pattern!r}: {exc}",
                data={"pattern": pattern, "target": target, "path": label},
            )

    if base.is_file():
        candidates = [base]
        cut = False
        skipped: list[str] = []
    else:
        candidates, cut, skipped = _iter_files(
            base, max_files=max_files, skip_dirs=sandbox.SKIP_DIRS
        )
    if file_glob:
        candidates = [path for path in candidates if fnmatch.fnmatch(path.name, file_glob)]
    if order == "modified":
        try:
            candidates.sort(key=lambda path: (-path.stat().st_mtime, str(path)))
        except OSError:  # pragma: no cover — a file that vanished mid-walk
            candidates.sort(key=str)

    matches: list[dict] = []
    total = 0
    # a name search inspects every candidate's name; a content search only counts
    # the files it actually opened
    scanned = len(candidates) if target == "files" else 0
    matched_files: set[str] = set()
    for path in candidates:
        relative = sandbox.relative_label(root, path)
        if target == "files":
            if fnmatch.fnmatch(path.name, pattern) or fnmatch.fnmatch(relative, pattern):
                total += 1
                if total > offset and len(matches) < limit:
                    matches.append({"path": relative})
            continue
        try:
            if path.stat().st_size > max_file_bytes:
                continue
            raw = path.read_bytes()
        except OSError:
            continue
        if sandbox.is_probably_binary(raw):
            continue
        scanned += 1
        text_lines = raw.decode("utf-8", "replace").splitlines()
        # file modes count one match per FILE, content mode one per matching line
        lines_hit = [
            index
            for index, line in enumerate(text_lines)
            if regex is not None and regex.search(line)
        ]
        if not lines_hit:
            continue
        matched_files.add(relative)
        if output_mode == "content":
            for index in lines_hit:
                total += 1
                if total <= offset or len(matches) >= limit:
                    continue
                record: dict = {
                    "path": relative,
                    "line": index + 1,
                    "text": text_lines[index][:_MAX_MATCH_TEXT],
                }
                if context:
                    low = max(0, index - context)
                    high = min(len(text_lines), index + context + 1)
                    record["context"] = [
                        f"{number + 1}|{text_lines[number][:_MAX_MATCH_TEXT]}"
                        for number in range(low, high)
                        if number != index
                    ]
                matches.append(record)
        else:
            total += 1
            if total > offset and len(matches) < limit:
                matches.append(
                    {"path": relative}
                    if output_mode == "files_only"
                    else {"path": relative, "count": len(lines_hit)}
                )
    truncated = bool(cut or total > offset + len(matches))
    return ToolResult(
        tool=NAME,
        ok=True,
        data={
            "pattern": pattern,
            "target": target,
            "path": label,
            "matches": matches,
            "count": len(matches),
            "total_matches": total,
            "files_scanned": scanned,
            "files_matched": len(matched_files),
            "truncated": truncated,
            "engine": "python-re" if target == "content" else "fnmatch",
            "skipped_dirs": skipped,
        },
        evidence=[
            {
                "ref": str(base),
                "kind": "run",
                "note": f"{target} search, {len(matches)} returned / {total} found",
            }
        ],
    )


RUNNERS = {NAME: run}

__all__ = ["CONTRACT", "INPUT_SCHEMA", "MODULE", "NAME", "OUTPUT_SCHEMA", "RUNNERS", "run"]
