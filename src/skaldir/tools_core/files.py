"""``read_file`` / ``write_file`` / ``list_dir`` — the filesystem hands.

All three address paths *relative to the declared root* (an absolute path is
accepted only when it is already inside the root). ``read_file`` additionally
accepts the spill directory (``<runtime>/tool_output``), because that is where an
oversized earlier result went — reading it back must not require a second tool.
``write_file`` refuses version-control and credential paths (see
:data:`skaldir.tools_core.sandbox.WRITE_DENY_DIRS` /
``WRITE_DENY_GLOBS``); that is an accident guard, not an isolation boundary.

``read_file`` and ``list_dir`` do not mutate; ``write_file`` does, and says what
it wrote: a re-read plus a SHA-256 comparison makes ``verified`` a measurement
instead of a promise.
"""

from __future__ import annotations

import hashlib
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

MODULE = "skaldir.tools_core.files"

_READ_RULE = TruncationRule(max_bytes=200000, strategy="head", max_lines=5000, spill=False)
_ID_MAX_BYTES = 2000000

_WRITE_DENY = sandbox.WRITE_DENY_GLOBS + tuple(f"{name}/**" for name in sandbox.WRITE_DENY_DIRS)


# ------------------------------------------------------------------ read_file

READ_NAME = "read_file"

READ_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "minLength": 1,
            "maxLength": 4096,
            "description": "file path relative to the workspace root",
        },
        "offset": {
            "type": "integer",
            "minimum": 1,
            "maximum": 100000000,
            "description": "first line to return (1-based, default 1)",
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 5000,
            "description": "maximum number of lines to return (default 200)",
        },
        "max_bytes": {
            "type": "integer",
            "minimum": 1024,
            "maximum": 2000000,
            "description": "byte budget for the returned content (default 200000)",
        },
        "expected_revision": {
            "type": "string",
            "description": "expected sha256 of the whole file; returns STALE_REFERENCE on mismatch",
        },
    },
    "required": ["path"],
    "additionalProperties": False,
}

READ_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "path as addressed (relative to the root)"},
        "abs_path": {"type": "string", "description": "absolute path that was read"},
        "exists": {"type": "boolean", "description": "whether the file exists"},
        "shape": {"type": "string", "description": "text | binary | empty"},
        "content": {"type": "string", "description": "line-numbered content ('N|text' lines)"},
        "start_line": {"type": "integer", "description": "first line in content"},
        "end_line": {"type": "integer", "description": "last line in content (0 when empty)"},
        "total_lines": {"type": "integer", "description": "lines in the whole file"},
        "returned_lines": {"type": "integer", "description": "lines in content"},
        "truncated": {"type": "boolean", "description": "more lines were available"},
        "next_offset": {"type": "integer", "description": "offset to continue with (0 = done)"},
        "size_bytes": {"type": "integer", "description": "file size in bytes"},
        "sha256": {"type": "string", "description": "sha256 of the whole file"},
        "encoding": {"type": "string", "description": "decoding used for the content"},
        "max_bytes": {"type": "integer", "description": "the byte budget that was applied"},
        "warnings": {"type": "array", "description": "notes, e.g. a binary file"},
    },
    "required": ["path", "abs_path", "exists", "shape", "content", "total_lines", "truncated"],
    "additionalProperties": False,
}

READ_CONTRACT = ToolContract(
    name=READ_NAME,
    title="Read a text file (paginated, hashed)",
    description=(
        "Return up to `limit` lines from `offset` inside a byte budget, with the file's "
        "sha256, its total line count and the offset that continues the read."
    ),
    module=MODULE,
    input_schema=READ_INPUT_SCHEMA,
    output_schema=READ_OUTPUT_SCHEMA,
    permission=Permission(
        filesystem=FilesystemPermission(
            scope="workspace + tool_output",
            mode="read",
            roots=("<workspace>", "<runtime>/tool_output"),
            deny_globs=(),
        ),
        network=NetworkPermission(mode="none"),
    ),
    truncation=_READ_RULE,
    timeout_s=20,
    tags=("filesystem", "read"),
    mutates=False,
    smoke={
        "setup": [
            {"tool": "write_file", "input": {"path": "smoke.txt", "content": "alpha\nbeta\n"}}
        ],
        "input": {"path": "smoke.txt"},
        "expect": {"shape": "text", "content_contains": "2|beta"},
    },
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_text_report(
    root: str | Path,
    path_value: str,
    *,
    offset: int = 1,
    limit: int = 200,
    max_bytes: int = 200000,
    allow_roots: tuple[str | Path, ...] = (),
) -> ToolResult:
    """Read one file with the ``read_file`` policy (shared with the smoke runner)."""
    path, problem = sandbox.candidate_path(root, path_value, allow_roots=allow_roots)
    label = str(path_value)
    if path is None:
        return failure(READ_NAME, "blocked", problem, data={"path": label, "abs_path": ""})
    label = sandbox.relative_label(root, path)
    if not path.exists():
        return failure(
            READ_NAME,
            "not_found",
            f"file does not exist: {label}",
            data={
                "path": label,
                "abs_path": str(path),
                "exists": False,
                "shape": "missing",
                "content": "",
                "total_lines": 0,
                "truncated": False,
            },
        )
    if path.is_dir():
        return failure(
            READ_NAME,
            "usage",
            f"{label} is a directory — use list_dir to inspect it",
            data={"path": label, "abs_path": str(path), "exists": True, "shape": "directory"},
        )
    raw = path.read_bytes()
    size = len(raw)
    warnings: list[str] = []
    if sandbox.is_probably_binary(raw):
        return ToolResult(
            tool=READ_NAME,
            ok=True,
            data={
                "path": label,
                "abs_path": str(path),
                "exists": True,
                "shape": "binary",
                "content": "",
                "start_line": 0,
                "end_line": 0,
                "total_lines": 0,
                "returned_lines": 0,
                "truncated": True,
                "next_offset": 0,
                "size_bytes": size,
                "sha256": _sha256(path),
                "encoding": "binary",
                "max_bytes": max_bytes,
                "warnings": ["binary file (NUL byte found): read it with terminal/a decoder"],
            },
            warnings=["binary content is not returned"],
            evidence=[{"ref": str(path), "kind": "file", "note": f"{size} bytes, binary"}],
        )
    text = raw.decode("utf-8", "replace")
    lines = text.splitlines()
    total = len(lines)
    start = max(1, int(offset))
    if start > max(total, 1):
        warnings.append(f"offset {start} is past the end of the file ({total} lines)")
    window = lines[start - 1 : start - 1 + max(1, int(limit))]
    kept: list[str] = []
    used = 0
    cut_line = False
    for line in window:
        chunk = len(line.encode("utf-8")) + 1
        if kept and used + chunk > max_bytes:
            break
        if not kept and chunk > max_bytes:
            kept.append(line[:max_bytes])
            used += max_bytes
            cut_line = True
            break
        kept.append(line)
        used += chunk
    end = start + len(kept) - 1 if kept else 0
    content = "\n".join(f"{start + index}|{line}" for index, line in enumerate(kept))
    truncated = (start + len(kept)) <= total or cut_line
    next_offset = start + len(kept) if truncated else 0
    if cut_line:
        warnings.append(f"line {start} was cut to the {max_bytes}-byte budget")
    return ToolResult(
        tool=READ_NAME,
        ok=True,
        data={
            "path": label,
            "abs_path": str(path),
            "exists": True,
            "shape": "empty" if total == 0 else "text",
            "content": content,
            "start_line": start,
            "end_line": end,
            "total_lines": total,
            "returned_lines": len(kept),
            "truncated": bool(truncated),
            "next_offset": int(next_offset),
            "size_bytes": size,
            "sha256": _sha256(path),
            "encoding": "utf-8",
            "max_bytes": max_bytes,
            "warnings": warnings,
        },
        warnings=warnings,
        evidence=[{"ref": str(path), "kind": "file", "note": f"{total} lines, {size} bytes"}],
    )


def read_file(args: dict, ctx: ToolContext) -> ToolResult:
    refusal = enforce_input(READ_CONTRACT, args)
    if refusal is not None:
        return refusal
    expected = str(args.get("expected_revision", "") or "")
    if expected:
        resolved = (ctx.root / str(args["path"])).resolve()
        if resolved.is_file():
            actual = _sha256(resolved)
            if actual != expected:
                return ToolResult.ok({
                    "path": str(args["path"]),
                    "abs_path": str(resolved),
                    "exists": True,
                    "shape": "stale",
                    "content": "",
                    "total_lines": 0,
                    "truncated": False,
                    "error": "STALE_REFERENCE",
                    "sha256": actual,
                    "message": f"expected {expected[:16]}…, found {actual[:16]}…",
                })
    spill = ctx.spill_dir()
    allow = (spill,) if spill is not None else ()
    return read_text_report(
        ctx.root,
        str(args["path"]),
        offset=int(args.get("offset", 1) or 1),
        limit=int(args.get("limit", 200) or 200),
        max_bytes=int(args.get("max_bytes", 200000) or 200000),
        allow_roots=allow,
    )


# ----------------------------------------------------------------- write_file

WRITE_NAME = "write_file"

WRITE_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "minLength": 1,
            "maxLength": 4096,
            "description": "file path relative to the workspace root",
        },
        "content": {
            "type": "string",
            "maxLength": 2000000,
            "description": "the text to write (replaces the file in overwrite mode)",
        },
        "mode": {
            "type": "string",
            "enum": ["overwrite", "append", "create"],
            "description": "overwrite (default) | append | create (refuses an existing file)",
        },
        "make_parents": {
            "type": "boolean",
            "description": "create missing parent directories (default false)",
        },
    },
    "required": ["path", "content"],
    "additionalProperties": False,
}

WRITE_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "path as addressed"},
        "abs_path": {"type": "string", "description": "absolute path written"},
        "mode": {"type": "string", "description": "mode that was applied"},
        "created": {"type": "boolean", "description": "the file did not exist before"},
        "bytes_written": {"type": "integer", "description": "bytes appended/overwritten"},
        "total_bytes": {"type": "integer", "description": "file size after the write"},
        "sha256": {"type": "string", "description": "sha256 after the write"},
        "previous_sha256": {
            "type": "string",
            "description": "sha256 before the write ('' when new)",
        },
        "verified": {"type": "boolean", "description": "re-read hash matched the expected content"},
        "parents_created": {"type": "array", "description": "directories that were created"},
    },
    "required": ["path", "abs_path", "mode", "created", "bytes_written", "total_bytes", "verified"],
    "additionalProperties": False,
}

WRITE_CONTRACT = ToolContract(
    name=WRITE_NAME,
    title="Write a text file (scoped, verified)",
    description=(
        "Write, append to or create a file inside the workspace root, then re-read it "
        "and compare the sha256 so `verified` is measured, not claimed."
    ),
    module=MODULE,
    input_schema=WRITE_INPUT_SCHEMA,
    output_schema=WRITE_OUTPUT_SCHEMA,
    permission=Permission(
        filesystem=FilesystemPermission(
            scope="workspace",
            mode="write",
            roots=("<workspace>",),
            deny_globs=_WRITE_DENY,
            symlink_policy="escapes the root => refused",
        ),
        network=NetworkPermission(mode="none"),
    ),
    truncation=TruncationRule(max_bytes=2000000, strategy="head", spill=False),
    timeout_s=20,
    tags=("filesystem", "write"),
    mutates=True,
    smoke={
        "input": {"path": "smoke-write.txt", "content": "core-smoke"},
        "expect": {"verified": True, "created": True},
    },
)


def write_text_report(
    root: str | Path,
    path_value: str,
    content: str,
    *,
    mode: str = "overwrite",
    make_parents: bool = False,
) -> ToolResult:
    """Write one file under the ``write_file`` policy (shared with the smoke runner)."""
    path, problem = sandbox.candidate_path(root, path_value)
    if path is None:
        return failure(
            WRITE_NAME, "blocked", problem, data={"path": str(path_value), "abs_path": ""}
        )
    label = sandbox.relative_label(root, path)
    denied = sandbox.write_denied(root, path)
    if denied:
        return failure(
            WRITE_NAME,
            "blocked",
            f"writing {label} is refused by the deny rule {denied!r} "
            "(version-control/credential paths are never written by this tool)",
            data={"path": label, "abs_path": str(path), "deny_glob": denied},
        )
    existed = path.exists()
    if path.is_dir():
        return failure(
            WRITE_NAME,
            "usage",
            f"{label} is a directory",
            data={"path": label, "abs_path": str(path)},
        )
    if mode == "create" and existed:
        return failure(
            WRITE_NAME,
            "conflict",
            f"{label} already exists (mode 'create' never overwrites)",
            data={"path": label, "abs_path": str(path), "exists": True},
        )
    parents_created: list[str] = []
    if make_parents and not path.parent.exists():
        missing: list[Path] = []
        probe = path.parent
        while not probe.exists() and probe != probe.parent:
            missing.append(probe)
            probe = probe.parent
        path.parent.mkdir(parents=True, exist_ok=True)
        parents_created = [str(item) for item in reversed(missing)]
    elif not path.parent.exists():
        return failure(
            WRITE_NAME,
            "not_found",
            f"parent directory does not exist: {sandbox.relative_label(root, path.parent)} "
            "(pass make_parents: true to create it)",
            data={"path": label, "abs_path": str(path)},
        )
    previous = _sha256(path) if existed and path.is_file() else ""
    before_size = path.stat().st_size if existed and path.is_file() else 0
    payload = content.encode("utf-8")
    if mode == "append" and existed:
        with path.open("ab") as handle:
            handle.write(payload)
    else:
        path.write_bytes(payload)
    total = path.stat().st_size
    after = _sha256(path)
    # verification is a re-read, never a promise: the full content in
    # overwrite/create mode (size + hash), the appended tail in append mode.
    if mode == "append" and existed:
        with path.open("rb") as handle:
            handle.seek(max(0, total - len(payload)))
            tail = handle.read()
        verified = total == before_size + len(payload) and tail == payload
    else:
        verified = total == len(payload) and after == hashlib.sha256(payload).hexdigest()
    return ToolResult(
        tool=WRITE_NAME,
        ok=True,
        data={
            "path": label,
            "abs_path": str(path),
            "mode": mode,
            "created": not existed,
            "bytes_written": len(payload),
            "total_bytes": total,
            "sha256": after,
            "previous_sha256": previous,
            "verified": bool(verified),
            "parents_created": parents_created,
        },
        evidence=[{"ref": str(path), "kind": "file", "note": f"{mode}, {len(payload)} bytes"}],
    )


def write_file(args: dict, ctx: ToolContext) -> ToolResult:
    refusal = enforce_input(WRITE_CONTRACT, args)
    if refusal is not None:
        return refusal
    return write_text_report(
        ctx.root,
        str(args["path"]),
        str(args["content"]),
        mode=str(args.get("mode", "overwrite") or "overwrite"),
        make_parents=bool(args.get("make_parents", False)),
    )


# ------------------------------------------------------------------- list_dir

LIST_NAME = "list_dir"

LIST_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "maxLength": 4096,
            "description": "directory relative to the workspace root (default: the root)",
        },
        "depth": {
            "type": "integer",
            "minimum": 1,
            "maximum": 3,
            "description": "how many levels to walk (default 1)",
        },
        "glob": {
            "type": "string",
            "maxLength": 256,
            "description": "file-name glob filter, e.g. '*.py'",
        },
        "show_hidden": {"type": "boolean", "description": "include dotfiles (default false)"},
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 2000,
            "description": "maximum entries (default 200)",
        },
        "order": {
            "type": "string",
            "enum": ["discovery", "modified"],
            "description": "name order (default) or newest-first by mtime",
        },
    },
    "required": [],
    "additionalProperties": False,
}

LIST_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "directory as addressed"},
        "abs_path": {"type": "string", "description": "absolute directory"},
        "entries": {
            "type": "array",
            "description": "one entry per path: name, path, type, size, mtime",
        },
        "count": {"type": "integer", "description": "number of entries returned"},
        "truncated": {"type": "boolean", "description": "the limit cut the listing"},
        "skipped": {"type": "array", "description": "directory names that were not descended into"},
        "depth": {"type": "integer", "description": "depth that was walked"},
    },
    "required": ["path", "abs_path", "entries", "count", "truncated"],
    "additionalProperties": False,
}

LIST_CONTRACT = ToolContract(
    name=LIST_NAME,
    title="List a directory (bounded)",
    description=(
        "List a directory up to three levels deep with a hard entry limit, skipping "
        "build/cache directories by default and reporting what was skipped."
    ),
    module=MODULE,
    input_schema=LIST_INPUT_SCHEMA,
    output_schema=LIST_OUTPUT_SCHEMA,
    permission=Permission(
        filesystem=FilesystemPermission(
            scope="workspace", mode="read", roots=("<workspace>",), deny_globs=()
        ),
        network=NetworkPermission(mode="none"),
    ),
    truncation=TruncationRule(max_bytes=20000, strategy="head", max_lines=2000, spill=False),
    timeout_s=20,
    tags=("filesystem", "read"),
    mutates=False,
    smoke={
        "setup": [{"tool": "write_file", "input": {"path": "smoke-list.txt", "content": "x"}}],
        "input": {"path": "."},
        "expect": {"min_count": 1},
    },
)


def _entry_type(path: Path) -> str:
    if path.is_symlink():
        return "symlink"
    if path.is_dir():
        return "dir"
    if path.is_file():
        return "file"
    return "other"


def list_dir(args: dict, ctx: ToolContext) -> ToolResult:
    refusal = enforce_input(LIST_CONTRACT, args)
    if refusal is not None:
        return refusal
    import fnmatch

    root = Path(ctx.root)
    target, problem = sandbox.candidate_path(root, args.get("path", ".") or ".")
    if target is None:
        return failure(LIST_NAME, "blocked", problem, data={"path": str(args.get("path", ""))})
    if not target.exists():
        return failure(
            LIST_NAME,
            "not_found",
            f"directory does not exist: {sandbox.relative_label(root, target)}",
            data={"path": sandbox.relative_label(root, target), "abs_path": str(target)},
        )
    if not target.is_dir():
        return failure(
            LIST_NAME,
            "usage",
            f"{sandbox.relative_label(root, target)} is not a directory",
            data={"path": sandbox.relative_label(root, target), "abs_path": str(target)},
        )
    depth = int(args.get("depth", 1) or 1)
    limit = int(args.get("limit", 200) or 200)
    pattern = str(args.get("glob", "") or "")
    show_hidden = bool(args.get("show_hidden", False))
    skipped_names: set[str] = set()

    def keep(path: Path) -> bool:
        """Hidden / ``SKIP_DIRS`` / glob — the filter, applied **before** the limit.

        ``sandbox.walk_entries`` counts the ``limit`` against what this accepts. The
        old order applied the limit to the raw walk first, so ``limit: 5`` on a
        directory whose first five sorted children were hidden or build/cache
        directories answered ``0 entries, truncated: true`` — the whole limit was
        spent on entries the caller never sees.
        """
        name = path.name
        if path.is_dir() and name in sandbox.SKIP_DIRS:
            skipped_names.add(name)
            return False
        if not show_hidden and name.startswith("."):
            return False
        if (
            pattern
            and not fnmatch.fnmatch(name, pattern)
            and not fnmatch.fnmatch(sandbox.relative_label(target, path), pattern)
        ):
            return False
        return True

    paths, cut = sandbox.walk_entries(target, depth=depth, limit=limit, keep=keep)
    skipped = sorted(skipped_names)
    entries: list[dict] = []
    for path in paths:
        try:
            stat = path.stat() if not path.is_symlink() else path.lstat()
        except OSError:
            continue
        entry_type = _entry_type(path)
        entries.append(
            {
                "name": path.name,
                "path": sandbox.relative_label(root, path),
                "type": entry_type,
                "size": int(stat.st_size) if entry_type == "file" else 0,
                "mtime": round(float(stat.st_mtime), 3),
            }
        )
    if str(args.get("order", "discovery")) == "modified":
        entries.sort(key=lambda item: (-item["mtime"], item["path"]))
    else:
        entries.sort(key=lambda item: item["path"])
    # the walk stopped at `limit` accepted entries (or exhausted the tree): nothing
    # was spent on a filtered child, so `truncated` now means exactly that
    truncated = bool(cut)
    entries = entries[:limit]
    return ToolResult(
        tool=LIST_NAME,
        ok=True,
        data={
            "path": sandbox.relative_label(root, target) or ".",
            "abs_path": str(target),
            "entries": entries,
            "count": len(entries),
            "truncated": truncated,
            "skipped": skipped,
            "depth": depth,
        },
        evidence=[{"ref": str(target), "kind": "file", "note": f"{len(entries)} entries"}],
    )


RUNNERS = {READ_NAME: read_file, WRITE_NAME: write_file, LIST_NAME: list_dir}

CONTRACTS = (READ_CONTRACT, WRITE_CONTRACT, LIST_CONTRACT)

__all__ = [
    "CONTRACTS",
    "LIST_CONTRACT",
    "MODULE",
    "READ_CONTRACT",
    "RUNNERS",
    "WRITE_CONTRACT",
    "list_dir",
    "read_file",
    "read_text_report",
    "write_file",
    "write_text_report",
]
