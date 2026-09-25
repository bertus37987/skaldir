"""Coding context harness — repo map, search, symbols/diagnostics, context pack.

A coding agent is only as good as the repository view it is given. The three harnesses we
measure ourselves against (Claude Code, Cline, OpenCode — see ``THIRD_PARTY_NOTICES.md``)
each ship the same four pieces, and this module is our version of them, built on nothing
but the standard library plus the tools the host already has:

============  ==========================================================================
``repo map``  a compact, **ranked** index of the workspace (path, language, lines,
              symbols, size) rendered under a **token budget**
``search``    regex content search and file-glob search with result caps, ``.gitignore``
              rules and a context-lines option
``symbols``   per-file symbol lookup: Python through ``ast`` (exact, free), every other
              language through ``ctags`` **when it is installed**
``diagnostics`` real diagnostics from a real tool (``ruff`` for Python, ``git`` for
              changed files/blame), or an honest ``blocked`` result naming what is missing
``pack``      a bounded, ranked context block for a task description — the artifact the
              prompt builder consumes
============  ==========================================================================

Four properties make this an *index* and not a directory listing.

**The map is not a rescan.** Every file fact (size, mtime, inode, line count, symbol count,
content digest) lives in a JSON cache under the runtime directory. Refreshing walks the
tree for metadata only and re-reads **only** files whose ``(size, mtime_ns, inode)`` moved;
unchanged files are served from the cache without ever being opened:

* ``mode: "fast"`` — the directory stamps (one ``stat`` per *directory*) match the cached
  ones, so not a single file is stat'ed;
* ``mode: "incremental"`` — a file-level walk, with ``reused`` / ``reopened`` / ``added`` /
  ``removed`` counters in the payload, so "how much did this call actually do" is a number
  and not a claim.

**Two fingerprints, two meanings.** ``map_fingerprint`` hashes the *facts* (path, size,
lines, symbols, content digest), so a ``touch`` that changes no byte leaves it identical
while a real edit moves it. ``pack.fingerprint`` hashes the rendered pack text, so the
prompt builder can freeze it exactly like it freezes a system prompt.

**Truncation is explicit.** Every verb has a declared timeout, a declared truncation rule
and a declared permission set (:data:`CONTRACTS`); a budget that bites appends a marker
naming how much was left out. An empty search answer says ``files_scanned`` so "nothing
found" can never be confused with "never looked".

**A missing tool is a ``blocked`` result, never a guess.** No language server is installed by
this module: symbol lookup for a non-Python file without ``ctags``, and diagnostics for a
file no installed linter understands, come back as ``blocked`` (exit 3) naming the exact
binary that would be needed. We never invent a symbol or a diagnostic.

Nothing here writes into the workspace: the cache is the only file written, and it goes to
``<runtime>/codectx/`` (``SKALDIR_CODECTX_STATE`` overrides it).
"""

from __future__ import annotations

import ast
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import warnings
from dataclasses import dataclass, field
from math import isqrt
from pathlib import Path
from typing import Any

from .env import getenv as env_getenv
from .tools_core import sandbox
from .tools_core.contract import (
    FilesystemPermission,
    NetworkPermission,
    Permission,
    SubprocessPermission,
    ToolContext,
    ToolContract,
    ToolResult,
    TruncationRule,
    enforce_input,
    failure,
)

SCHEMA = "skaldir.codectx/1"

#: The verbs this harness exposes, in the order the CLI lists them.
VERB_NAMES: tuple[str, ...] = (
    "map",
    "search",
    "symbols",
    "diagnostics",
    "changed",
    "blame",
    "pack",
)

#: The token accounting is an **estimate** and says so: no tokenizer is bundled (the same
#: rule the prompt builder follows).
CHARS_PER_TOKEN = 4
TOKEN_ESTIMATE = "chars/4"

DEFAULT_MAP_TOKENS = 1500
DEFAULT_PACK_TOKENS = 4000
DEFAULT_SEARCH_LIMIT = 50
MAX_SEARCH_LIMIT = 1000
MAX_FILES = 20000
MAX_FILE_BYTES = 2_000_000
MAX_WALK_DEPTH = 24
MAX_IGNORE_PATTERNS = 4000

#: hard timeout ceilings (a caller may ask for less, never for more)
TIMEOUTS: dict[str, int] = {
    "map": 60,
    "search": 60,
    "symbols": 30,
    "diagnostics": 60,
    "changed": 15,
    "blame": 15,
    "pack": 90,
}

TRUNCATION: dict[str, TruncationRule] = {
    "map": TruncationRule(max_bytes=24_000, strategy="head", max_lines=400, spill=False),
    "search": TruncationRule(max_bytes=32_000, strategy="head", max_lines=400, spill=False),
    "symbols": TruncationRule(max_bytes=16_000, strategy="head", max_lines=400, spill=False),
    "diagnostics": TruncationRule(max_bytes=24_000, strategy="head", max_lines=500, spill=False),
    "changed": TruncationRule(max_bytes=8_000, strategy="head", max_lines=400, spill=False),
    "blame": TruncationRule(max_bytes=8_000, strategy="head", max_lines=200, spill=False),
    "pack": TruncationRule(max_bytes=64_000, strategy="head", max_lines=1200, spill=False),
}

# ------------------------------------------------------------------ languages


LANGUAGES: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".js": "javascript",
    ".jsx": "jsx",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".rs": "rust",
    ".go": "go",
    ".java": "java",
    ".kt": "kotlin",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".sh": "shell",
    ".bash": "shell",
    ".zsh": "shell",
    ".sql": "sql",
    ".lua": "lua",
    ".pl": "perl",
    ".ex": "elixir",
    ".exs": "elixir",
    ".dart": "dart",
    ".scala": "scala",
    ".tf": "terraform",
    ".proto": "protobuf",
    ".toml": "toml",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
    ".ini": "ini",
    ".cfg": "ini",
    ".md": "markdown",
    ".rst": "rst",
    ".txt": "text",
    ".css": "css",
    ".scss": "scss",
    ".html": "html",
    ".vue": "vue",
    ".svelte": "svelte",
}

SOURCE_LANGUAGES = frozenset(
    {
        "python",
        "typescript",
        "tsx",
        "javascript",
        "jsx",
        "rust",
        "go",
        "java",
        "kotlin",
        "c",
        "cpp",
        "csharp",
        "ruby",
        "php",
        "swift",
        "shell",
        "lua",
        "perl",
        "elixir",
        "dart",
        "scala",
    }
)
DOC_LANGUAGES = frozenset({"markdown", "rst", "text"})
CONFIG_LANGUAGES = frozenset({"toml", "yaml", "json", "ini", "terraform"})

#: Path fragments that usually mean "generated": ranked down, never trusted as source.
GENERATED_HINTS: tuple[str, ...] = (
    "outputs/",
    "dist/",
    "build/",
    "vendor/",
    ".min.",
    "bundle",
    "_dep.",
    "generated",
)

#: file names that carry a repository's front door (ranked up, never a symbol)
ENTRY_NAMES = frozenset(
    {
        "readme.md",
        "agents.md",
        "claude.md",
        "skaldir.md",
        "soul.md",
        "contributing.md",
        "pyproject.toml",
        "package.json",
        "cargo.toml",
        "go.mod",
        "makefile",
        "dockerfile",
        "cli.py",
        "__init__.py",
        "__main__.py",
        "index.ts",
        "index.js",
        "main.py",
        "main.rs",
        "main.go",
    }
)

#: language → ``ctags --language-force``. A language that is missing here has no free
#: symbol index on this host, and the verb says so instead of guessing.
CTAGS_LANGUAGES: dict[str, str] = {
    "python": "Python",
    "typescript": "TypeScript",
    "tsx": "TypeScript",
    "javascript": "JavaScript",
    "jsx": "JavaScript",
    "rust": "Rust",
    "go": "Go",
    "java": "Java",
    "kotlin": "Kotlin",
    "c": "C",
    "cpp": "C++",
    "csharp": "C#",
    "ruby": "Ruby",
    "php": "PHP",
    "shell": "Sh",
    "lua": "Lua",
    "perl": "Perl",
    "sql": "SQL",
    "elixir": "Elixir",
}

#: What a language server would be, per language — named in a ``blocked`` answer so the
#: reader knows exactly what to install. This module installs nothing.
LSP_NEEDED: dict[str, str] = {
    "python": "basedpyright or pyright-langserver (or pylsp) for a Python language server",
    "typescript": "typescript-language-server (npm i -g typescript-language-server typescript)",
    "tsx": "typescript-language-server (npm i -g typescript-language-server typescript)",
    "javascript": "typescript-language-server (npm i -g typescript-language-server typescript)",
    "jsx": "typescript-language-server (npm i -g typescript-language-server typescript)",
    "rust": "rust-analyzer",
    "go": "gopls",
    "c": "clangd",
    "cpp": "clangd",
    "java": "jdtls (Eclipse JDT language server)",
    "kotlin": "kotlin-language-server",
    "csharp": "csharp-ls or OmniSharp",
    "ruby": "solargraph",
    "php": "intelephense",
    "lua": "lua-language-server",
    "swift": "sourcekit-lsp",
    "dart": "dart language-server (ships with the Dart SDK)",
    "scala": "metals",
}

#: The diagnostic tools this harness knows how to drive, in probe order.
LINTER_NAMES = ("ruff",)


def language_of(path: str | Path) -> str:
    """The language of a path by extension (``""`` when it has none we know)."""
    return LANGUAGES.get(Path(str(path)).suffix.lower(), "")


def kind_of(rel: str, language: str) -> str:
    """``source`` / ``test`` / ``doc`` / ``config`` / ``data`` — one label per file."""
    name = Path(rel).name.lower()
    parts = rel.lower().split("/")
    if "test" in parts or "tests" in parts or name.startswith("test_") or name.endswith("_test.py"):
        return "test"
    if language in DOC_LANGUAGES or name in ENTRY_NAMES and language in DOC_LANGUAGES:
        return "doc"
    if language in SOURCE_LANGUAGES:
        return "source"
    if language in CONFIG_LANGUAGES or name in ENTRY_NAMES:
        return "config"
    return "data"


def estimate_tokens(text: str) -> int:
    """The labelled ``chars/4`` estimate (no tokenizer is bundled — say so when quoting)."""
    if not text:
        return 0
    return max(1, (len(text) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def default_state_dir() -> Path:
    """Where the map cache lives: ``SKALDIR_CODECTX_STATE``, else ``<runtime>/codectx``."""
    explicit = env_getenv("SKALDIR_CODECTX_STATE") or ""
    if explicit.strip():
        return Path(explicit).expanduser()
    try:  # the product runtime, one resolution (§ registry)
        from .registry import default_runtime_dir

        return Path(default_runtime_dir()) / "codectx"
    except Exception:  # noqa: BLE001 — a standalone build keeps a cache of its own
        base = env_getenv("XDG_CACHE_HOME") or str(Path.home() / ".cache")
        return Path(base) / "skaldir" / "codectx"


def resolve_workspace(start: str | Path | None = None, workspace: str | Path | None = None) -> Path:
    """The directory this harness indexes: explicit ``--workspace``, else the git root,
    else the current directory. It is never widened to the filesystem."""
    if workspace is not None and str(workspace).strip():
        return Path(str(workspace)).expanduser().resolve()
    base = Path(str(start or Path.cwd())).expanduser().resolve()
    probe = _run(["git", "-C", str(base), "rev-parse", "--show-toplevel"], timeout_s=5)
    if probe["ok"] and probe["stdout"].strip():
        return Path(probe["stdout"].strip().splitlines()[0]).resolve()
    return base


# ------------------------------------------------------------------ subprocess


def _run(
    argv: list[str],
    *,
    cwd: str | Path | None = None,
    timeout_s: int = 15,
    max_bytes: int = 4_000_000,
) -> dict[str, Any]:
    """Run one fixed argv (no shell) under the sandbox's child environment.

    A missing binary, a timeout and a non-zero exit are *measured results*, never
    exceptions: the caller decides what they mean.
    """
    env_map, env_report = sandbox.child_environment(cwd or Path.cwd())
    record: dict[str, Any] = {
        "argv": list(argv),
        "cwd": str(cwd) if cwd else "",
        "ok": False,
        "kind": "",
        "exit_code": 0,
        "stdout": "",
        "stderr": "",
        "timeout_s": int(timeout_s),
        "env_policy": env_report["policy"],
    }
    try:
        completed = subprocess.run(  # noqa: S603 — fixed argv, shell=False
            [str(item) for item in argv],
            cwd=str(cwd) if cwd else None,
            env=env_map,
            capture_output=True,
            timeout=int(timeout_s),
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as exc:
        record["kind"] = "timeout"
        record["stderr"] = f"timed out after {timeout_s}s"
        record["stdout"] = (exc.stdout or b"").decode("utf-8", "replace")[:max_bytes]
        return record
    except (OSError, subprocess.SubprocessError) as exc:
        record["kind"] = "unavailable"
        record["stderr"] = f"{type(exc).__name__}: {exc}"
        return record
    record["exit_code"] = int(completed.returncode)
    record["stdout"] = completed.stdout.decode("utf-8", "replace")[:max_bytes]
    record["stderr"] = completed.stderr.decode("utf-8", "replace")[:max_bytes]
    record["ok"] = completed.returncode == 0
    return record


# ------------------------------------------------------------------ gitignore


@dataclass(frozen=True)
class IgnoreEntry:
    """One ``.gitignore`` pattern, with the directory it was read from."""

    base: str
    depth: int
    order: int
    source: str
    raw: str
    negated: bool
    dir_only: bool
    regex: re.Pattern[str]


def _translate_glob(body: str) -> str:
    """A bounded gitignore glob → regex (``**``/``*``/``?``/``[...]``; no brace expansion)."""
    out: list[str] = []
    index = 0
    while index < len(body):
        char = body[index]
        if char == "*":
            if body[index : index + 2] == "**":
                if body[index + 2 : index + 3] == "/":
                    out.append("(?:.*/)?")
                    index += 3
                    continue
                out.append(".*")
                index += 2
                continue
            out.append("[^/]*")
            index += 1
            continue
        if char == "?":
            out.append("[^/]")
            index += 1
            continue
        if char == "[":
            close = body.find("]", index + 1)
            if close == -1:
                out.append(re.escape(char))
                index += 1
                continue
            inner = body[index + 1 : close]
            if inner.startswith("!"):
                inner = "^" + inner[1:]
            out.append("[" + inner + "]")
            index = close + 1
            continue
        out.append(re.escape(char))
        index += 1
    return "".join(out)


def _ignore_entry(
    line: str, *, base: str, depth: int, order: int, source: str
) -> IgnoreEntry | None:
    text = line.rstrip("\n")
    if not text.strip() or text.lstrip().startswith("#"):
        return None
    if text.endswith("\\ "):  # an escaped trailing space is part of the pattern
        body = text[:-2] + " "
    else:
        body = text.rstrip()
    negated = body.startswith("!")
    if negated:
        body = body[1:]
    dir_only = body.endswith("/")
    body = body.rstrip("/")
    if not body:
        return None
    anchored = body.startswith("/") or "/" in body
    body = body.lstrip("/")
    prefix = "^" if anchored else "^(?:.*/)?"
    try:
        regex = re.compile(prefix + _translate_glob(body) + "$")
    except re.error:  # pragma: no cover — a pattern we cannot compile is skipped, not fatal
        return None
    return IgnoreEntry(
        base=base,
        depth=depth,
        order=order,
        source=source,
        raw=text.strip(),
        negated=negated,
        dir_only=dir_only,
        regex=regex,
    )


def _under(base: str, rel: str) -> str | None:
    """``rel`` relative to ``base`` (``None`` when ``rel`` is not below ``base``)."""
    if not base:
        return rel
    if rel == base:
        return ""
    if rel.startswith(base + "/"):
        return rel[len(base) + 1 :]
    return None


@dataclass
class IgnoreRules:
    """The ``.gitignore`` view of one workspace (a bounded, documented subset).

    Supported: comments, blank lines, ``!`` negation, a trailing ``/`` for directory-only
    patterns, a leading/middle ``/`` for anchoring, ``**``, ``*``, ``?`` and a character
    class. Later patterns win, and the **full git rule** applies: *a file inside an excluded
    directory cannot be re-included* — ``logs/`` plus ``!logs/keep.log`` still leaves the
    file out, exactly as ``git check-ignore`` does. Not supported: brace expansion,
    ``.git/info/exclude`` and a global excludes file.
    """

    entries: tuple[IgnoreEntry, ...] = ()
    sources: tuple[str, ...] = ()
    truncated: bool = False

    def decide(self, rel: str, *, is_dir: bool) -> tuple[bool, str]:
        """``(ignored, pattern)`` — later patterns win, ``!`` re-includes (git's rule)."""
        ignored = False
        pattern = ""
        for entry in self.entries:
            sub = _under(entry.base, rel)
            if sub is None or sub == "":
                continue
            parts = sub.split("/")
            candidates = ["/".join(parts[: index + 1]) for index in range(len(parts))]
            if entry.dir_only and not is_dir:
                candidates = candidates[:-1]  # a directory pattern never matches the file itself
            if any(entry.regex.match(item) for item in candidates):
                ignored = not entry.negated
                pattern = entry.raw
        return ignored, pattern

    def to_dict(self) -> dict[str, Any]:
        return {
            "patterns": len(self.entries),
            "sources": list(self.sources),
            "truncated": bool(self.truncated),
        }


def load_ignore_rules(
    root: str | Path,
    *,
    enabled: bool = True,
    max_patterns: int = MAX_IGNORE_PATTERNS,
    max_depth: int = MAX_WALK_DEPTH,
) -> IgnoreRules:
    """Read every ``.gitignore`` under ``root`` (bounded); ``enabled=False`` reads none."""
    if not enabled:
        return IgnoreRules()
    base = Path(root)
    entries: list[IgnoreEntry] = []
    sources: list[str] = []
    truncated = False
    queue: list[tuple[Path, int]] = [(base, 0)]
    while queue:
        current, depth = queue.pop(0)
        try:
            children = sorted(current.iterdir(), key=lambda item: item.name)
        except OSError:
            continue
        for child in children:
            if len(entries) >= max_patterns:
                truncated = True
                return IgnoreRules(tuple(entries), tuple(sources), truncated)
            try:
                if child.is_file() and child.name == ".gitignore":
                    rel_dir = sandbox.relative_label(base, current)
                    source = f"{rel_dir}/.gitignore" if rel_dir else ".gitignore"
                    sources.append(source)
                    for order, line in enumerate(
                        child.read_text(encoding="utf-8", errors="replace").splitlines()
                    ):
                        entry = _ignore_entry(
                            line,
                            base="" if rel_dir == "." else rel_dir,
                            depth=depth,
                            order=order,
                            source=source,
                        )
                        if entry is not None:
                            entries.append(entry)
                    continue
                if child.is_dir() and not child.is_symlink():
                    if child.name in sandbox.SKIP_DIRS or child.name.startswith(".git"):
                        continue
                    if depth + 1 < max_depth:
                        queue.append((child, depth + 1))
            except OSError:
                continue
    entries.sort(key=lambda item: (item.depth, item.source, item.order))
    return IgnoreRules(tuple(entries), tuple(sources), truncated)


# ----------------------------------------------------------------------- walk


#: directory names never descended into, on top of ``sandbox.SKIP_DIRS``
EXTRA_SKIP_DIRS: tuple[str, ...] = (".hypothesis", ".godsv", ".skaldir", ".idea", ".direnv")


@dataclass
class Walk:
    """One metadata walk: file stamps, directory stamps and the honest counters."""

    root: Path
    files: list[tuple[str, int, int, int]] = field(default_factory=list)
    dirs: dict[str, int] = field(default_factory=dict)
    ignored: int = 0
    ignored_samples: list[tuple[str, str]] = field(default_factory=list)
    symlinks: int = 0
    cut: bool = False
    max_depth_hit: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "files": len(self.files),
            "dirs": len(self.dirs),
            "ignored": self.ignored,
            "symlinks_skipped": self.symlinks,
            "cut": bool(self.cut),
            "max_depth_hit": bool(self.max_depth_hit),
        }


def walk_workspace(
    root: str | Path,
    *,
    rules: IgnoreRules | None = None,
    max_files: int = MAX_FILES,
    max_depth: int = MAX_WALK_DEPTH,
    stat_files: bool = True,
    subpath: str = "",
) -> Walk:
    """A deterministic, bounded walk. ``stat_files=False`` is the fast path (dir stamps only).

    ``.git`` and the build/cache directories are never entered; every ignored path is
    counted (and sampled) instead of silently disappearing.
    """
    base = Path(root).resolve()
    start = base
    prefix = ""
    if subpath and subpath not in (".", ""):
        candidate, problem = sandbox.candidate_path(base, subpath)
        if candidate is None or not candidate.is_dir():
            return Walk(root=base)
        start = candidate
        prefix = sandbox.relative_label(base, candidate)
        if prefix == ".":
            prefix = ""
    result = Walk(root=base)
    skip = set(sandbox.SKIP_DIRS) | set(EXTRA_SKIP_DIRS)
    stack: list[tuple[Path, int]] = [(start, 0)]
    while stack:
        current, depth = stack.pop()
        try:
            with os.scandir(current) as handle:
                raw_entries = list(handle)
        except OSError:
            continue
        try:
            result.dirs[sandbox.relative_label(base, current)] = current.stat().st_mtime_ns
        except OSError:  # pragma: no cover
            pass
        subdirs: list[tuple[Path, int]] = []
        for entry in sorted(raw_entries, key=lambda item: item.name):
            rel = sandbox.relative_label(base, Path(entry.path))
            if entry.name.startswith(".git"):
                result.ignored += 1
                continue
            try:
                if entry.is_dir(follow_symlinks=False):
                    if entry.name in skip:
                        result.ignored += 1
                        if len(result.ignored_samples) < 20:
                            result.ignored_samples.append((rel, f"directory name: {entry.name}"))
                        continue
                    if rules is not None:
                        ignored, pattern = rules.decide(rel, is_dir=True)
                        if ignored:
                            result.ignored += 1
                            if len(result.ignored_samples) < 20:
                                result.ignored_samples.append((rel, pattern))
                            continue
                    if depth + 1 >= max_depth:
                        result.max_depth_hit = True
                        continue
                    subdirs.append((Path(entry.path), depth + 1))
                    continue
                if entry.is_symlink():
                    result.symlinks += 1
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
                if rules is not None:
                    ignored, pattern = rules.decide(rel, is_dir=False)
                    if ignored:
                        result.ignored += 1
                        if len(result.ignored_samples) < 20:
                            result.ignored_samples.append((rel, pattern))
                        continue
                if len(result.files) >= max_files:
                    result.cut = True
                    return result
                if stat_files:
                    stat = entry.stat(follow_symlinks=False)
                    result.files.append(
                        (rel, int(stat.st_size), int(stat.st_mtime_ns), int(stat.st_ino))
                    )
                else:
                    result.files.append((rel, -1, -1, -1))
            except OSError:  # pragma: no cover — a path that vanished mid-walk
                continue
        stack.extend(reversed(subdirs))
    result.files.sort(key=lambda item: item[0])
    return result


# ------------------------------------------------------------------- symbols


def python_symbols(source: str) -> tuple[list[dict[str, Any]], str]:
    """Top-level and nested definitions of one Python source, via ``ast``.

    Returns ``(symbols, error)``; a syntax error is reported, never raised. Compiler
    warnings about invalid escape sequences in the *parsed* file are silenced: they are
    that file's business, not this index's.
    """
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            tree = ast.parse(source)
    except (SyntaxError, ValueError) as exc:
        return [], f"{type(exc).__name__}: {exc}"

    symbols: list[dict[str, Any]] = []

    def visit(node: ast.AST, parent: str) -> None:
        for child in getattr(node, "body", []) or []:
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                symbols.append(
                    {
                        "name": child.name,
                        "kind": (
                            "async function"
                            if isinstance(child, ast.AsyncFunctionDef)
                            else "function"
                        ),
                        "line": child.lineno,
                        "parent": parent,
                    }
                )
                visit(child, f"{parent}.{child.name}" if parent else child.name)
                continue
            if isinstance(child, ast.ClassDef):
                symbols.append(
                    {"name": child.name, "kind": "class", "line": child.lineno, "parent": parent}
                )
                visit(child, f"{parent}.{child.name}" if parent else child.name)
                continue
            if isinstance(child, (ast.Assign, ast.AnnAssign)) and not parent:
                targets = child.targets if isinstance(child, ast.Assign) else [child.target]
                for target in targets:
                    if isinstance(target, ast.Name) and target.id.isupper():
                        symbols.append(
                            {
                                "name": target.id,
                                "kind": "constant",
                                "line": child.lineno,
                                "parent": "",
                            }
                        )
            if isinstance(child, (ast.If, ast.Try, ast.With, ast.For, ast.While, ast.Module)):
                visit(child, parent)

    visit(tree, "")
    symbols.sort(key=lambda item: (int(item["line"]), str(item["name"])))
    return symbols, ""


def _ctags_available() -> str:
    return shutil.which("ctags") or ""


def ctags_symbols(
    path: Path, *, language: str, timeout_s: int = 20
) -> tuple[list[dict[str, Any]], str]:
    """Symbols for one file through ``ctags --output-format=json`` (universal ctags)."""
    binary = _ctags_available()
    if not binary:
        return [], "ctags is not installed"
    argv = [binary, "-f", "-", "--output-format=json", "--fields=+n"]
    forced = CTAGS_LANGUAGES.get(language)
    if forced:
        argv += [f"--language-force={forced}"]
    argv.append(str(path))
    outcome = _run(argv, cwd=path.parent, timeout_s=timeout_s, max_bytes=1_000_000)
    if outcome["kind"] == "unavailable":
        return [], outcome["stderr"]
    if outcome["kind"] == "timeout":
        return [], f"ctags timed out after {timeout_s}s"
    symbols: list[dict[str, Any]] = []
    for line in outcome["stdout"].splitlines():
        text = line.strip()
        if not text.startswith("{"):
            continue
        try:
            tag = json.loads(text)
        except json.JSONDecodeError:
            continue
        if tag.get("_type") != "tag":
            continue
        symbols.append(
            {
                "name": str(tag.get("name", "")),
                "kind": str(tag.get("kind", "")),
                "line": int(tag.get("line") or tag.get("_line") or 0),
                "parent": str(tag.get("scope", "")),
            }
        )
    symbols.sort(key=lambda item: (int(item["line"]), str(item["name"])))
    return symbols, "" if outcome["ok"] or symbols else outcome["stderr"]


# ----------------------------------------------------------------- file facts


def _content_facts(path: Path) -> tuple[int, str, int]:
    """``(lines, sha256, symbol_count)`` of one file — read once, bounded, tolerant."""
    try:
        raw = path.read_bytes()[:MAX_FILE_BYTES]
    except OSError:
        return 0, "", 0
    if sandbox.is_probably_binary(raw):
        return 0, _sha256_bytes(raw), 0
    text = raw.decode("utf-8", "replace")
    lines = text.count("\n") + (1 if text and not text.endswith("\n") else 0)
    language = language_of(path)
    count = 0
    if language == "python":
        symbols, _error = python_symbols(text)
        count = len(symbols)
    else:
        pattern = r"^\s*(?:def|fn|func|function|class|struct|impl|interface|type)\b"
        count = len(re.findall(pattern, text, re.M))
    return lines, _sha256_bytes(raw), count


@dataclass
class MapEntry:
    """One file's cached facts plus the ranking that put it where it is."""

    path: str
    language: str = ""
    kind: str = "data"
    size: int = 0
    lines: int = 0
    symbols: int = 0
    digest: str = ""
    score: int = 0
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "language": self.language,
            "kind": self.kind,
            "bytes": self.size,
            "lines": self.lines,
            "symbols": self.symbols,
            "digest": self.digest[:12],
            "score": self.score,
            "reasons": list(self.reasons),
        }


def _looks_generated(rel: str) -> bool:
    """Does this path look like a build artifact rather than a file someone maintains?"""
    lowered = rel.lower()
    return any(hint in lowered for hint in GENERATED_HINTS)


def _score_entry(entry: MapEntry, *, changed: frozenset[str], depth: int) -> tuple[int, list[str]]:
    """The ranking rule: front doors and real code first, generated bulk last."""
    score = 0
    reasons: list[str] = []
    name = Path(entry.path).name.lower()
    generic = name in {"__init__.py", "__main__.py", "cli.py", "index.ts", "index.js"}
    if name in ENTRY_NAMES and (not generic or depth <= 2):
        score += 40
        reasons.append("entry-point name")
    if depth == 0:
        score += 25
        reasons.append("repository root")
    if entry.kind == "test":
        score -= 5
        reasons.append("test file")
    elif entry.kind == "source":
        score += 12
        reasons.append("source code")
    elif entry.kind == "doc":
        score += 6
        reasons.append("documentation")
    if entry.symbols:
        score += min(30, entry.symbols)
        reasons.append(f"{entry.symbols} symbol(s)")
    if 200 <= entry.size <= 40_000:
        score += 5
        reasons.append("typical file size")
    elif entry.size > 200_000:
        score -= 20
        reasons.append("large (likely generated)")
    elif entry.size < 40:
        score -= 8
        reasons.append("tiny")
    if entry.path in changed:
        score += 35
        reasons.append("changed since HEAD")
    if _looks_generated(entry.path):
        score -= 15
        reasons.append("looks generated")
    return score, reasons


def render_map(entries: list[MapEntry], *, token_budget: int, header: str = "") -> dict[str, Any]:
    """Render the ranked map under a token budget; the marker names what was left out."""
    budget_chars = max(200, int(token_budget) * CHARS_PER_TOKEN)
    lines: list[str] = []
    if header:
        lines.append(header)
    used = len(header) + 1 if header else 0
    included = 0
    for entry in entries:
        line = (
            f"{entry.path}  [{entry.language or 'file'} · {entry.lines}L · "
            f"{entry.symbols}sym · {entry.size}B]"
        )
        if len(line) > 160:
            line = line[:157] + "…"
        cost = len(line) + 1
        if used + cost > budget_chars:
            break
        lines.append(line)
        used += cost
        included += 1
    truncated = included < len(entries)
    if truncated:
        rest = len(entries) - included
        marker = (
            f"…[repo map truncated: {rest} more file(s) not shown, budget {token_budget} "
            f"tokens ≈ {budget_chars} chars]"
        )
        lines.append(marker)
        used += len(marker) + 1
    text = "\n".join(lines)
    return {
        "text": text,
        "chars": len(text),
        "tokens": estimate_tokens(text),
        "token_estimate": TOKEN_ESTIMATE,
        "budget_tokens": int(token_budget),
        "truncated": bool(truncated),
        "included": included,
        "dropped": len(entries) - included,
    }


# ------------------------------------------------------------------- repo map


def _cache_path(state_dir: str | Path | None, root: Path) -> Path:
    base = Path(state_dir) if state_dir is not None else default_state_dir()
    return base / f"map-{hashlib.sha256(str(root).encode()).hexdigest()[:12]}.json"


def _load_cache(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
        return {}
    return payload


def _write_cache(path: Path, payload: dict[str, Any]) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        return False
    return True


def refresh_map(
    root: str | Path,
    *,
    state_dir: str | Path | None = None,
    use_gitignore: bool = True,
    max_files: int = MAX_FILES,
    trust_dir_mtime: bool = False,
    force: bool = False,
    changed: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Bring the map cache up to date with the smallest amount of work that can do it.

    The mtime/inode strategy, in the order it is tried — ``mode`` reports which one ran:

    ``reused``
        every file's ``(size, mtime_ns, inode)`` matched the cache, so **not one file was
        read**; ``reopened == 0`` proves it.
    ``incremental``
        the metadata walk ran and only the files whose stamp moved were read
        (``reopened`` counts them).
    ``rebuilt``
        there was no cache, or ``force=True``: every file was read once.

    ``trust_dir_mtime=True`` adds a stat-free level in front: if the *directory* stamps
    match, the cached map is served without stat'ing a single file. That is cheaper and it
    is **not the default on purpose** — a directory's mtime does not move when a file's
    content is edited in place, so the trusted path can serve a stale fact. It is offered
    for very large trees where a stat pass is itself the cost, and the payload names the
    validation it used (``validation``).
    """
    base = Path(root).resolve()
    path = _cache_path(state_dir, base)
    cached = {} if force else _load_cache(path)
    rules = load_ignore_rules(base, enabled=use_gitignore, max_depth=MAX_WALK_DEPTH)

    if cached and trust_dir_mtime:
        stamps = walk_workspace(
            base, rules=rules, max_files=max_files, stat_files=False, max_depth=MAX_WALK_DEPTH
        )
        if stamps.dirs == (cached.get("dirs") or {}) and len(stamps.files) == len(
            cached.get("entries") or {}
        ):
            entries = {rel: dict(facts) for rel, facts in (cached.get("entries") or {}).items()}
            return {
                "mode": "reused",
                "validation": "directory mtime (no file was stat'ed)",
                "state_path": str(path),
                "cache_used": True,
                "rules": rules.to_dict(),
                "walk": stamps.to_dict(),
                "entries": entries,
                "stats": {"reused": len(entries), "reopened": 0, "added": 0, "removed": 0},
                "map_fingerprint": str(cached.get("map_fingerprint", "")),
                "cache_written": False,
                "max_files": max_files,
            }

    walked = walk_workspace(
        base, rules=rules, max_files=max_files, stat_files=True, max_depth=MAX_WALK_DEPTH
    )
    previous: dict[str, dict[str, Any]] = dict(cached.get("entries") or {})
    entries: dict[str, dict[str, Any]] = {}
    reused = reopened = added = 0
    for rel, size, mtime_ns, inode in walked.files:
        old = previous.get(rel)
        if (
            old is not None
            and int(old.get("size", -1)) == size
            and int(old.get("mtime_ns", -1)) == mtime_ns
            and int(old.get("inode", -1)) == inode
        ):
            entries[rel] = dict(old)
            reused += 1
            continue
        lines, digest, symbol_count = _content_facts(base / rel)
        entries[rel] = {
            "size": size,
            "mtime_ns": mtime_ns,
            "inode": inode,
            "lines": lines,
            "symbols": symbol_count,
            "digest": digest,
            "language": language_of(rel),
            "kind": kind_of(rel, language_of(rel)),
        }
        reopened += 1
        if old is None:
            added += 1
    removed = len([rel for rel in previous if rel not in entries])
    fingerprint = map_fingerprint(entries)
    if not cached:
        mode = "rebuilt"
    elif reopened or added or removed:
        mode = "incremental"
    else:
        mode = "reused"
    payload = {
        "schema": SCHEMA,
        "root": str(base),
        "version": int(cached.get("version", 0)) + (0 if mode == "reused" else 1),
        "map_fingerprint": fingerprint,
        "dirs": walked.dirs,
        "entries": entries,
        "changed_at_build": sorted(changed),
        "rules": rules.to_dict(),
    }
    written = _write_cache(path, payload)
    return {
        "mode": mode,
        "validation": "size + mtime_ns + inode per file (contents read only when a stamp moved)",
        "state_path": str(path),
        "cache_used": bool(cached),
        "rules": rules.to_dict(),
        "walk": walked.to_dict(),
        "entries": entries,
        "stats": {
            "reused": reused,
            "reopened": reopened,
            "added": added,
            "removed": removed,
        },
        "map_fingerprint": fingerprint,
        "cache_written": written,
        "max_files": max_files,
    }


def map_fingerprint(entries: dict[str, dict[str, Any]]) -> str:
    """Hash the **facts** of every entry (content digests included, order independent)."""
    payload = [
        (
            rel,
            int(facts.get("size", 0)),
            int(facts.get("lines", 0)),
            int(facts.get("symbols", 0)),
            str(facts.get("digest", "")),
        )
        for rel, facts in sorted(entries.items())
    ]
    return _sha256_bytes(json.dumps(payload, sort_keys=True).encode("utf-8"))


def _ranked_entries(
    entries: dict[str, dict[str, Any]], *, changed: frozenset[str] = frozenset()
) -> list[MapEntry]:
    ranked: list[MapEntry] = []
    for rel, facts in entries.items():
        entry = MapEntry(
            path=rel,
            language=str(facts.get("language") or language_of(rel)),
            kind=str(facts.get("kind") or "data"),
            size=int(facts.get("size", 0)),
            lines=int(facts.get("lines", 0)),
            symbols=int(facts.get("symbols", 0)),
            digest=str(facts.get("digest", "")),
        )
        depth = rel.count("/")
        entry.score, ideas = _score_entry(entry, changed=changed, depth=depth)
        entry.reasons = tuple(ideas)
        ranked.append(entry)
    ranked.sort(key=lambda item: (-item.score, item.path))
    return ranked


def git_changed(root: str | Path, *, limit: int = 500) -> dict[str, Any]:
    """Changed / untracked files of the workspace through ``git status --porcelain``."""
    base = Path(root).resolve()
    if not shutil.which("git"):
        return {
            "status": "blocked",
            "error_kind": "blocked",
            "needed": ["git"],
            "reason": "git is not installed, so there is no changed-file view",
            "files": [],
        }
    inside = _run(["git", "-C", str(base), "rev-parse", "--is-inside-work-tree"], timeout_s=8)
    if not inside["ok"] or inside["stdout"].strip() != "true":
        return {
            "status": "unavailable",
            "reason": "not a git work tree",
            "detail": (inside["stderr"] or inside["stdout"]).strip()[:200],
            "files": [],
        }
    status = _run(
        ["git", "-C", str(base), "status", "--porcelain=v1", "--untracked-files=all"],
        timeout_s=TIMEOUTS["changed"],
    )
    if status["kind"] in {"timeout", "unavailable"}:
        return {
            "status": "blocked",
            "error_kind": "blocked",
            "needed": ["git"],
            "reason": status["stderr"],
            "files": [],
        }
    files: list[dict[str, str]] = []
    for line in status["stdout"].splitlines():
        if len(line) < 4:
            continue
        code = line[:2].strip() or "??"
        rel = line[3:].strip()
        if " -> " in rel:  # a rename: the new path is the one that matters
            rel = rel.split(" -> ", 1)[1]
        files.append({"path": rel.strip('"'), "code": code})
    branch = _run(["git", "-C", str(base), "rev-parse", "--abbrev-ref", "HEAD"], timeout_s=8)
    head = _run(["git", "-C", str(base), "log", "-1", "--format=%H %s"], timeout_s=8)
    truncated = len(files) > limit
    return {
        "status": "ok",
        "branch": branch["stdout"].strip(),
        "head": head["stdout"].strip(),
        "files": files[:limit],
        "count": min(len(files), limit),
        "total": len(files),
        "truncated": truncated,
        "engine": "git status --porcelain=v1",
    }


def git_blame(root: str | Path, path: str, line: int, *, timeout_s: int = 15) -> dict[str, Any]:
    """Who wrote one line: ``git blame --line-porcelain -L N,N``."""
    base = Path(root).resolve()
    if not shutil.which("git"):
        return {
            "status": "blocked",
            "error_kind": "blocked",
            "needed": ["git"],
            "reason": "git is not installed",
        }
    target, problem = sandbox.candidate_path(base, path)
    if target is None:
        return {
            "status": "blocked",
            "error_kind": "blocked",
            "needed": [],
            "reason": problem,
            "path": path,
        }
    if not target.is_file():
        return {
            "status": "not_found",
            "error_kind": "not_found",
            "reason": f"no such file: {sandbox.relative_label(base, target)}",
            "path": path,
        }
    wanted = max(1, int(line))
    outcome = _run(
        [
            "git",
            "-C",
            str(base),
            "blame",
            "--line-porcelain",
            "-L",
            f"{wanted},{wanted}",
            "--",
            sandbox.relative_label(base, target),
        ],
        timeout_s=timeout_s,
    )
    if outcome["kind"] in {"timeout", "unavailable"} or not outcome["ok"]:
        kind = "blocked" if outcome["kind"] == "unavailable" else "not_found"
        return {
            "status": kind,
            "error_kind": kind,
            "needed": ["git", "a committed file (blame needs history)"],
            "reason": (outcome["stderr"] or outcome["stdout"]).strip()[:300],
            "path": path,
            "line": wanted,
        }
    record: dict[str, Any] = {"status": "ok", "path": path, "line": wanted, "raw_lines": 0}
    text_lines: list[str] = []
    for item in outcome["stdout"].splitlines():
        if item.startswith("\t"):
            text_lines.append(item[1:])
            continue
        key, _, value = item.partition(" ")
        if key == "author":
            record["author"] = value
        elif key == "author-mail":
            record["author_mail"] = value.strip("<>")
        elif key == "author-time":
            record["author_time_unix"] = value
        elif key == "summary":
            record["summary"] = value
        elif len(key) == 40 and not record.get("commit"):
            record["commit"] = key
    record["raw_lines"] = len(text_lines)
    record["text"] = text_lines[0] if text_lines else ""
    return record


def repo_map(
    root: str | Path,
    *,
    token_budget: int = DEFAULT_MAP_TOKENS,
    state_dir: str | Path | None = None,
    use_gitignore: bool = True,
    trust_dir_mtime: bool = False,
    force: bool = False,
    include_changed: bool = True,
    max_files: int = MAX_FILES,
    entry_limit: int = 500,
) -> dict[str, Any]:
    """The ranked repo map: cache-aware facts + the rendered, budgeted text."""
    base = Path(root).resolve()
    changed = frozenset()
    changed_report: dict[str, Any] = {"status": "unavailable", "files": []}
    if include_changed:
        changed_report = git_changed(base)
        changed = frozenset(item["path"] for item in changed_report.get("files", []))
    refreshed = refresh_map(
        base,
        state_dir=state_dir,
        use_gitignore=use_gitignore,
        max_files=max_files,
        trust_dir_mtime=trust_dir_mtime,
        force=force,
        changed=changed,
    )
    entries = refreshed["entries"]
    ranked = _ranked_entries(entries, changed=changed)
    languages: dict[str, dict[str, int]] = {}
    for rel, facts in entries.items():
        language = str(facts.get("language") or language_of(rel))
        bucket = languages.setdefault(language or "unknown", {"files": 0, "lines": 0, "bytes": 0})
        bucket["files"] += 1
        bucket["lines"] += int(facts.get("lines", 0))
        bucket["bytes"] += int(facts.get("size", 0))
    header = (
        f"# repo map · {len(ranked)} file(s) · ranked"
        + (f" · {len(changed)} changed file(s) ranked up" if changed else " · no git change info")
        + f" · ~{token_budget} token budget"
    )
    rendered = render_map(ranked[:entry_limit], token_budget=token_budget, header=header)
    return {
        "schema": SCHEMA,
        "root": str(base),
        "mode": refreshed["mode"],
        "update": refreshed["stats"],
        "cache": {
            "path": refreshed["state_path"],
            "written": refreshed["cache_written"],
            "used": refreshed["cache_used"],
        },
        "walk": refreshed["walk"],
        "ignore": refreshed["rules"],
        "files_indexed": len(entries),
        "languages": dict(sorted(languages.items())),
        "changed": sorted(changed),
        "git": changed_report,
        "entries": [entry.to_dict() for entry in ranked[:entry_limit]],
        "entries_total": len(ranked),
        "map": rendered,
        "map_fingerprint": refreshed["map_fingerprint"],
        "token_estimate": TOKEN_ESTIMATE,
        "max_files": max_files,
    }


# -------------------------------------------------------------------- search


def search(
    root: str | Path,
    pattern: str,
    *,
    target: str = "content",
    subpath: str = "",
    file_glob: str = "",
    output_mode: str = "content",
    context: int = 0,
    limit: int = DEFAULT_SEARCH_LIMIT,
    offset: int = 0,
    case_sensitive: bool = True,
    use_gitignore: bool = True,
    max_files: int = MAX_FILES,
    max_file_bytes: int = MAX_FILE_BYTES,
) -> dict[str, Any]:
    """Regex content search (``target="content"``) or file-name glob (``target="files"``).

    ``files_scanned`` and ``ignored_files`` are always reported, so an empty result is
    readable as "nothing matched", not as "nothing was looked at".
    """
    base = Path(root).resolve()
    rules = load_ignore_rules(base, enabled=use_gitignore)
    start = base
    if subpath and subpath not in (".", ""):
        candidate, problem = sandbox.candidate_path(base, subpath)
        if candidate is None:
            return {
                "status": "blocked",
                "error_kind": "blocked",
                "reason": problem,
                "pattern": pattern,
                "target": target,
                "path": subpath,
                "matches": [],
                "count": 0,
            }
        if not candidate.exists():
            return {
                "status": "not_found",
                "error_kind": "not_found",
                "reason": f"search path does not exist: {sandbox.relative_label(base, candidate)}",
                "pattern": pattern,
                "target": target,
                "path": sandbox.relative_label(base, candidate),
                "matches": [],
                "count": 0,
            }
        start = candidate

    regex: re.Pattern[str] | None = None
    if target == "content":
        try:
            regex = re.compile(pattern, 0 if case_sensitive else re.IGNORECASE)
        except re.error as exc:
            return {
                "status": "usage",
                "error_kind": "usage",
                "reason": f"invalid regular expression {pattern!r}: {exc}",
                "pattern": pattern,
                "target": target,
                "path": subpath or ".",
                "matches": [],
                "count": 0,
            }

    if start.is_file():
        candidates = [start]
        walk = Walk(root=base)
    else:
        walked = walk_workspace(
            base, rules=rules, max_files=max_files, subpath=subpath or ".", max_depth=MAX_WALK_DEPTH
        )
        candidates = [base / rel for rel, _size, _m, _i in walked.files]
        walk = walked
    if file_glob:
        candidates = [path for path in candidates if fnmatch.fnmatch(path.name, file_glob)]

    matches: list[dict[str, Any]] = []
    total = 0
    scanned = len(candidates) if target == "files" else 0
    matched_files: set[str] = set()
    for path in sorted(candidates, key=lambda item: str(item)):
        relative = sandbox.relative_label(base, path)
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
        hits = [
            index
            for index, line in enumerate(text_lines)
            if regex is not None and regex.search(line)
        ]
        if not hits:
            continue
        matched_files.add(relative)
        if output_mode == "content":
            for index in hits:
                total += 1
                if total <= offset or len(matches) >= limit:
                    continue
                record: dict[str, Any] = {
                    "path": relative,
                    "line": index + 1,
                    "text": text_lines[index][:400],
                }
                if context:
                    low = max(0, index - context)
                    high = min(len(text_lines), index + context + 1)
                    record["context"] = [
                        f"{number + 1}|{text_lines[number][:400]}"
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
                    else {"path": relative, "count": len(hits)}
                )
    truncated = bool(walk.cut or total > offset + len(matches))
    return {
        "status": "ok",
        "pattern": pattern,
        "target": target,
        "output_mode": output_mode,
        "path": subpath or ".",
        "matches": matches,
        "count": len(matches),
        "total_matches": total,
        "files_scanned": scanned,
        "files_matched": len(matched_files),
        "truncated": truncated,
        "engine": "python-re" if target == "content" else "fnmatch",
        "ignore": rules.to_dict(),
        "ignored_files": walk.ignored,
        "ignored_samples": walk.ignored_samples,
        "skipped_dirs": sorted(set(sandbox.SKIP_DIRS) | set(EXTRA_SKIP_DIRS)),
        "case_sensitive": bool(case_sensitive),
    }


# ------------------------------------------------------------------- symbols


def symbols(
    root: str | Path,
    subpath: str = "",
    *,
    language_filter: str = "",
    limit: int = 200,
    max_files: int = 2000,
    use_ctags: bool = True,
) -> dict[str, Any]:
    """Symbols of a file or directory tree: ``ast`` for Python, ``ctags`` for the rest.

    A file this host has no symbol index for is reported in ``unsupported`` with the tool
    that would be needed — it is never guessed at.
    """
    base = Path(root).resolve()
    rules = load_ignore_rules(base)
    target, problem = sandbox.candidate_path(base, subpath or ".")
    if target is None:
        return {"status": "blocked", "error_kind": "blocked", "reason": problem, "files": []}
    if not target.exists():
        return {
            "status": "not_found",
            "error_kind": "not_found",
            "reason": f"no such path: {subpath}",
            "files": [],
        }
    if target.is_file():
        paths = [target]
    else:
        walked = walk_workspace(base, rules=rules, max_files=max_files, subpath=subpath or ".")
        paths = [base / rel for rel, _s, _m, _i in walked.files]

    files: list[dict[str, Any]] = []
    unsupported: list[dict[str, Any]] = []
    totals: dict[str, int] = {}
    total_symbols = 0
    for path in sorted(paths, key=lambda item: str(item)):
        language = language_of(path)
        if language_filter and language != language_filter:
            continue
        relative = sandbox.relative_label(base, path)
        if language == "python":
            try:
                source = path.read_text(encoding="utf-8", errors="replace")[:MAX_FILE_BYTES]
            except OSError as exc:
                unsupported.append(
                    {"path": relative, "language": language, "needed": [], "reason": str(exc)}
                )
                continue
            found, error = python_symbols(source)
            tool = "python-ast"
            if error:
                files.append(
                    {
                        "path": relative,
                        "language": language,
                        "tool": tool,
                        "symbols": [],
                        "error": error,
                    }
                )
                continue
        elif language in CTAGS_LANGUAGES and use_ctags and _ctags_available():
            found, error = ctags_symbols(path, language=language)
            tool = "ctags"
            if error and not found:
                files.append(
                    {
                        "path": relative,
                        "language": language,
                        "tool": tool,
                        "symbols": [],
                        "error": error,
                    }
                )
                continue
        else:
            needed: list[str] = []
            if language in CTAGS_LANGUAGES:
                needed.append("ctags (universal-ctags) for a free symbol index")
            if language in LSP_NEEDED:
                needed.append(LSP_NEEDED[language])
            if not needed:
                continue  # not a code file at all: silently out of scope, not "unsupported"
            unsupported.append(
                {
                    "path": relative,
                    "language": language,
                    "needed": needed,
                    "reason": (
                        "no symbol index is available for this language on this host "
                        "(no language server is installed by this module)"
                    ),
                }
            )
            continue
        for symbol in found:
            totals[str(symbol["kind"])] = totals.get(str(symbol["kind"]), 0) + 1
        total_symbols += len(found)
        if found:
            files.append(
                {
                    "path": relative,
                    "language": language,
                    "tool": tool,
                    "symbols": found[:limit],
                    "symbols_total": len(found),
                    "truncated": len(found) > limit,
                }
            )
    scope = sandbox.relative_label(base, target) or "."
    status = "ok"
    needed_all: list[str] = []
    if not files and unsupported:
        status = "blocked"
        needed_all = sorted({item for row in unsupported for item in row["needed"]})
    return {
        "status": status,
        "error_kind": "blocked" if status == "blocked" else "",
        "reason": (
            "no symbol index is available for the requested path(s)"
            if status == "blocked"
            else ""
        ),
        "path": scope,
        "files": files,
        "files_indexed": len(files),
        "symbols_total": total_symbols,
        "by_kind": dict(sorted(totals.items())),
        "unsupported": unsupported[:50],
        "unsupported_count": len(unsupported),
        "needed": needed_all,
        "tools": {
            "python": "ast (stdlib)",
            "ctags": _ctags_available() or "not installed",
        },
    }


# --------------------------------------------------------------- diagnostics


def probe_linter(name: str = "ruff") -> dict[str, Any]:
    """Is a real diagnostic tool reachable? Probe once, report the argv honestly."""
    if name != "ruff":
        return {
            "name": name,
            "available": False,
            "argv": [],
            "reason": f"{name} is not a diagnostic tool this harness knows how to drive",
            "needed": list(LINTER_NAMES),
        }
    binary = shutil.which("ruff")
    if binary:
        probe = _run([binary, "--version"], timeout_s=10)
        if probe["ok"]:
            return {
                "name": "ruff",
                "available": True,
                "argv": [binary],
                "version": probe["stdout"].strip(),
                "source": "PATH",
            }
    module = _run([sys.executable, "-m", "ruff", "--version"], timeout_s=15)
    if module["ok"]:
        return {
            "name": "ruff",
            "available": True,
            "argv": [sys.executable, "-m", "ruff"],
            "version": module["stdout"].strip(),
            "source": "python -m ruff",
        }
    return {
        "name": "ruff",
        "available": False,
        "argv": [],
        "reason": (
            "ruff is neither on PATH nor importable as a module: no Python diagnostics "
            "can be run"
        ),
        "needed": [
            "ruff (pip install ruff, or the project's `dev` extra: ruff>=0.6)",
            "or a Python language server (basedpyright / pyright-langserver / pylsp)",
        ],
        "probe": {
            "which": binary or "",
            "python": sys.executable,
            "module_error": module["stderr"][:200],
        },
    }


def diagnostics(
    root: str | Path,
    paths: list[str] | tuple[str, ...] = (),
    *,
    linter: str = "auto",
    limit: int = 200,
    max_files: int = 2000,
) -> dict[str, Any]:
    """Real diagnostics for the requested paths, or an honest ``blocked`` answer.

    Python files go through ``ruff check --output-format=json``. A language no installed
    linter understands is reported per file with the tool that would be needed; when the
    whole request is unsupported the status is ``blocked`` (exit 3), naming the language
    server — this module installs none and never invents a finding.
    """
    base = Path(root).resolve()
    name = "ruff" if linter in ("", "auto") else linter
    wanted = [item for item in (paths or ()) if str(item).strip()]
    targets: list[Path] = []
    rules = load_ignore_rules(base)
    if not wanted:
        walked = walk_workspace(base, rules=rules, max_files=max_files)
        targets = [base / rel for rel, _s, _m, _i in walked.files]
    else:
        for item in wanted:
            candidate, problem = sandbox.candidate_path(base, item)
            if candidate is None:
                return {
                    "status": "blocked",
                    "error_kind": "blocked",
                    "reason": problem,
                    "linter": name,
                    "findings": [],
                }
            if not candidate.exists():
                return {
                    "status": "not_found",
                    "error_kind": "not_found",
                    "reason": f"no such path: {item}",
                    "linter": name,
                    "findings": [],
                }
            if candidate.is_dir():
                walked = walk_workspace(base, rules=rules, max_files=max_files)
                prefix = sandbox.relative_label(base, candidate).rstrip("/")
                for rel, _size, _mtime, _inode in walked.files:
                    if rel == prefix or rel.startswith(prefix + "/"):
                        targets.append(base / rel)
            else:
                targets.append(candidate)

    python_files = [path for path in targets if language_of(path) == "python"]
    unsupported: list[dict[str, Any]] = []
    seen_languages: dict[str, int] = {}
    for path in targets:
        language = language_of(path)
        if language == "python" or not language:
            continue
        seen_languages[language] = seen_languages.get(language, 0) + 1
    for language, count in sorted(seen_languages.items()):
        needed: list[str] = []
        if language in CTAGS_LANGUAGES:
            drivers = "/".join(LINTER_NAMES)
            needed.append(f"{language}: a linter (this harness drives only {drivers})")
        if language in LSP_NEEDED:
            needed.append(LSP_NEEDED[language])
        if not needed:
            # no language server and no ctags entry for it: the honest statement is that this
            # harness drives exactly the linters it names, and nothing else
            needed = [
                f"{language}: no diagnostics tool for this language is reachable on this host "
                f"(this harness drives {'/'.join(LINTER_NAMES)} for Python only)"
            ]
        unsupported.append(
            {
                "language": language,
                "files": count,
                "needed": needed,
                "reason": "no diagnostics tool for this language is reachable on this host",
            }
        )

    probe = probe_linter(name)
    if not probe["available"]:
        return {
            "status": "blocked",
            "error_kind": "blocked",
            "reason": probe.get("reason", f"{name} is not available"),
            "linter": probe,
            "needed": probe.get("needed", []),
            "files_checked": 0,
            "findings": [],
            "counts_by_code": {},
            "unsupported": unsupported,
        }
    if not python_files:
        needed = sorted({item for row in unsupported for item in row["needed"]})
        return {
            "status": "blocked",
            "error_kind": "blocked",
            "reason": (
                "none of the requested paths is a Python file and no language server is "
                "installed, so no diagnostic can be produced"
            ),
            "linter": probe,
            "needed": needed or [LSP_NEEDED.get("python", "a language server")],
            "files_checked": 0,
            "findings": [],
            "counts_by_code": {},
            "unsupported": unsupported,
        }

    argv = list(probe["argv"]) + ["check", "--output-format=json", "--no-cache"]
    relative = [
        sandbox.relative_label(base, path) for path in python_files[:max_files]
    ]
    outcome = _run(argv + relative, cwd=base, timeout_s=TIMEOUTS["diagnostics"])
    if outcome["kind"] == "timeout":
        return {
            "status": "blocked",
            "error_kind": "timeout",
            "reason": outcome["stderr"],
            "linter": probe,
            "findings": [],
            "counts_by_code": {},
            "unsupported": unsupported,
        }
    try:
        parsed = json.loads(outcome["stdout"] or "[]")
    except json.JSONDecodeError:
        return {
            "status": "blocked",
            "error_kind": "internal",
            "reason": "ruff answered with output that is not JSON",
            "linter": probe,
            "stdout_head": outcome["stdout"][:300],
            "findings": [],
            "counts_by_code": {},
            "unsupported": unsupported,
        }
    findings: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for item in parsed if isinstance(parsed, list) else []:
        code = str(item.get("code") or "")
        counts[code] = counts.get(code, 0) + 1
        location = item.get("location") or {}
        findings.append(
            {
                "path": str(item.get("filename", "")),
                "line": int(location.get("row") or 0),
                "column": int(location.get("column") or 0),
                "code": code,
                "message": str(item.get("message", "")),
                "severity": "error" if code in {"E902", "F821", "F822"} else "lint",
                "tool": "ruff",
            }
        )
    truncated = len(findings) > limit
    return {
        "status": "ok",
        "linter": probe,
        "files_checked": len(relative),
        "findings": findings[:limit],
        "count": min(len(findings), limit),
        "total_findings": len(findings),
        "counts_by_code": dict(sorted(counts.items())),
        "truncated": truncated,
        "unsupported": unsupported,
        "exit_code_observed": outcome["exit_code"],
        "argv": outcome["argv"],
    }


# --------------------------------------------------------------- context pack


_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
_STOPWORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "from",
        "into",
        "when",
        "then",
        "does",
        "have",
        "make",
        "made",
        "should",
        "must",
        "code",
        "file",
        "files",
        "task",
        "test",
        "tests",
        "please",
        "using",
        "use",
        "add",
        "fix",
        "new",
        "all",
        "any",
        "not",
        "but",
        "you",
        "your",
        "our",
        "its",
    }
)


def task_terms(task: str) -> list[str]:
    """The identifier-ish terms of a task description (lowercased, split on case/underscore)."""
    terms: set[str] = set()
    for raw in _WORD_RE.findall(task or ""):
        lowered = raw.lower()
        if lowered not in _STOPWORDS:
            terms.add(lowered)
        for piece in re.split(r"[_]|(?<=[a-z0-9])(?=[A-Z])", raw):
            piece = piece.lower()
            if len(piece) >= 3 and piece not in _STOPWORDS:
                terms.add(piece)
    return sorted(terms)


def rank_for_task(
    entries: dict[str, dict[str, Any]],
    task: str,
    *,
    changed: frozenset[str] = frozenset(),
    hits: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """Rank the map against a task description (path, symbols, kind, change state, hits).

    ``hits`` is the per-file count of task-term occurrences in the file's *content* (one
    bounded regex pass — the same engine :func:`search` uses). It is what pulls a file up
    when neither its path nor its name says anything about the task.
    """
    terms = task_terms(task)
    counts = dict(hits or {})
    ranked: list[dict[str, Any]] = []
    for rel, facts in entries.items():
        language = str(facts.get("language") or language_of(rel))
        entry = MapEntry(
            path=rel,
            language=language,
            kind=str(facts.get("kind") or "data"),
            size=int(facts.get("size", 0)),
            lines=int(facts.get("lines", 0)),
            symbols=int(facts.get("symbols", 0)),
            digest=str(facts.get("digest", "")),
        )
        reasons: list[str] = []
        score = 0
        if entry.kind == "source":
            score += 12
            reasons.append("source code")
        elif entry.kind == "test":
            score += 4
            reasons.append("test file")
        elif entry.kind == "doc":
            score += 6
            reasons.append("documentation")
        elif entry.kind == "config":
            score -= 6
            reasons.append("configuration file")
        else:
            score -= 12  # a log or a dump mentions everything and is rarely the file to edit
            reasons.append("data file")
        if entry.symbols:
            score += min(24, entry.symbols)
            reasons.append(f"{entry.symbols} symbol(s)")
        name = Path(rel).name.lower()
        if name in ENTRY_NAMES:
            score += 6
            reasons.append("entry-point name")
        if entry.size > 200_000:
            score -= 10
            reasons.append("large (likely generated)")
        if _looks_generated(rel):
            score -= 15
            reasons.append("looks generated")
        matched: list[str] = []
        path_tokens = set(re.split(r"[/._-]", rel.lower()))
        stem = Path(rel).stem.lower()
        for term in terms:
            if term == stem or term == name:
                score += 8
                matched.append(term)
                continue
            if term in path_tokens:
                score += 4
                matched.append(term)
                continue
            if term in rel.lower():
                score += 2
                matched.append(term)
        if matched:
            reasons.append("task terms: " + ",".join(sorted(set(matched))[:5]))
        content_hits = int(counts.get(rel, 0))
        if content_hits:
            # sublinear on purpose: without it every large file saturates the same cap and
            # the ranking degenerates into an alphabetical list of equally-scored files
            score += min(60, 4 * isqrt(content_hits))
            reasons.append(f"{content_hits} content hit(s)")
        if rel in changed:
            score += 12
            reasons.append("changed since HEAD")
        if score <= 0:
            continue
        ranked.append(
            {
                "path": rel,
                "language": language,
                "kind": entry.kind,
                "lines": entry.lines,
                "symbols": entry.symbols,
                "bytes": entry.size,
                "score": score,
                "content_hits": content_hits,
                "reasons": reasons,
                "matched_terms": sorted(set(matched)),
            }
        )
    ranked.sort(
        key=lambda item: (-int(item["score"]), -int(item["content_hits"]), str(item["path"]))
    )
    return ranked


def _excerpt(path: Path, terms: list[str], *, max_lines: int, max_chars: int) -> dict[str, Any]:
    """A bounded, line-numbered excerpt: the lines that match the task, else the head."""
    try:
        raw = path.read_bytes()[:MAX_FILE_BYTES]
    except OSError as exc:
        return {"lines": [], "reason": str(exc), "truncated": False}
    text = raw.decode("utf-8", "replace")
    lines = text.splitlines()
    wanted: list[int] = []
    if terms:
        pattern = re.compile("|".join(re.escape(term) for term in terms), re.IGNORECASE)
        for index, line in enumerate(lines):
            if pattern.search(line):
                wanted.extend(range(max(0, index - 1), min(len(lines), index + 2)))
                if len(wanted) >= max_lines:
                    break
    if not wanted:
        wanted = list(range(min(len(lines), max_lines)))
    ordered = sorted(set(wanted))[:max_lines]
    rendered: list[str] = []
    used = 0
    for number in ordered:
        row = f"{number + 1:>5}| {lines[number][:200]}"
        if used + len(row) + 1 > max_chars:
            rendered.append(f"      | …[excerpt truncated at {max_chars} chars]")
            break
        rendered.append(row)
        used += len(row) + 1
    return {
        "lines": rendered,
        "shown": len(rendered),
        "total_lines": len(lines),
        "truncated": len(rendered) < len(ordered) or len(ordered) < len(lines),
    }


def context_pack(
    root: str | Path,
    task: str,
    *,
    token_budget: int = DEFAULT_PACK_TOKENS,
    file_limit: int = 6,
    file_chars: int = 1200,
    symbol_limit: int = 60,
    include_diagnostics: bool = True,
    use_gitignore: bool = True,
    state_dir: str | Path | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """A bounded, ranked context block for a task description.

    Sections are assembled in a fixed order (``task``, ``repo_map``, ``changed``, ``files``,
    ``symbols``, ``diagnostics``), each with its own budget; what a budget cut is counted in
    ``dropped`` and marked in the block itself. The rendered text is byte-stable for the same
    workspace and task (no clock, relative paths, sorted order), so
    :func:`pack_fingerprint` can be frozen like a system prompt.
    """
    base = Path(root).resolve()
    budget_chars = max(400, int(token_budget) * CHARS_PER_TOKEN)
    terms = task_terms(task)
    map_payload = repo_map(
        base,
        token_budget=max(200, min(DEFAULT_MAP_TOKENS, budget_chars // (CHARS_PER_TOKEN * 4))),
        state_dir=state_dir,
        use_gitignore=use_gitignore,
        force=force,
        include_changed=True,
    )
    changed = frozenset(map_payload.get("changed") or ())
    entries = {
        row["path"]: {
            "size": row["bytes"],
            "lines": row["lines"],
            "symbols": row["symbols"],
            "digest": row["digest"],
            "language": row["language"],
            "kind": row["kind"],
        }
        for row in map_payload["entries"]
    }
    refreshed_entries = _load_cache(_cache_path(state_dir, base)).get("entries") or {}
    source_entries = refreshed_entries or entries
    hits: dict[str, int] = {}
    if terms:
        counted = search(
            base,
            r"\b(?:" + "|".join(re.escape(term) for term in terms[:24]) + r")\b",
            output_mode="count",
            limit=400,
            case_sensitive=False,
            use_gitignore=use_gitignore,
        )
        if counted.get("status") == "ok":
            hits = {
                str(row["path"]): int(row.get("count", 0))
                for row in counted.get("matches", [])
                if "count" in row
            }
    ranked = rank_for_task(source_entries, task, changed=changed, hits=hits)

    sections: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []

    def add_section(
        name: str,
        text: str,
        budget: int,
        *,
        dropped_rows: list[dict[str, Any]] | None = None,
    ) -> str:
        body = text.strip("\n")
        truncated = False
        if len(body) > budget:
            cut = body[:budget]
            boundary = cut.rfind("\n")
            if boundary > budget // 2:
                cut = cut[:boundary]
            body = cut + f"\n…[{name} truncated at {budget} chars]"
            truncated = True
        sections.append(
            {
                "name": name,
                "chars": len(body),
                "budget_chars": budget,
                "truncated": truncated,
                "included": len(dropped_rows or []) if dropped_rows else body.count("\n") + 1,
                "dropped": 0,
            }
        )
        if dropped_rows:
            sections[-1]["dropped"] = len(dropped_rows)
            dropped.extend(dropped_rows)
        return body

    blocks: list[str] = []
    blocks.append(
        add_section(
            "task",
            "# task\n" + (task.strip() or "(no task description given)"),
            max(200, budget_chars // 12),
        )
    )
    blocks.append(
        add_section(
            "repo_map",
            "# repo map\n" + str(map_payload["map"]["text"]),
            max(300, int(budget_chars * 0.22)),
        )
    )

    changed_rows = [
        {"path": item["path"], "code": item.get("code", "")}
        for item in (map_payload.get("git") or {}).get("files", [])[:40]
    ]
    changed_text = "\n".join(f"{row['code']:>3} {row['path']}" for row in changed_rows)
    blocks.append(
        add_section(
            "changed",
            "# changed files (git status)\n" + (changed_text or "(none)"),
            max(120, budget_chars // 12),
            dropped_rows=changed_rows[40:],
        )
    )

    file_budget = int(budget_chars * 0.42)
    excerpt_lines: list[str] = []
    used = 0
    used_paths: list[str] = []
    for row in ranked:
        if len(used_paths) >= file_limit:
            dropped.append({"path": row["path"], "reason": f"beyond the top {file_limit} files"})
            continue
        excerpt = _excerpt(
            base / row["path"], terms, max_lines=120, max_chars=min(file_chars, file_budget // 2)
        )
        head = (
            f"## {row['path']}  ({row['language'] or 'file'} · {row['lines']}L · "
            f"score {row['score']})"
        )
        body = "\n".join(excerpt["lines"])
        chunk = f"{head}\n{body}"
        if used + len(chunk) + 1 > file_budget:
            dropped.append(
                {"path": row["path"], "reason": f"file budget of {file_budget} chars reached"}
            )
            continue
        excerpt_lines.append(chunk)
        used += len(chunk) + 1
        used_paths.append(row["path"])
    excerpt_block = "# task-ranked file excerpts\n" + (
        "\n".join(excerpt_lines) or "(none)"
    )
    blocks.append(add_section("files", excerpt_block, file_budget))

    symbol_lines: list[str] = []
    for path in used_paths[:file_limit]:
        found = symbols(base, path, limit=symbol_limit)
        for file_row in found.get("files", []):
            names = ", ".join(
                f"{item['name']}:{item['line']}"
                for item in file_row.get("symbols", [])[:symbol_limit]
            )
            if names:
                symbol_lines.append(f"## {file_row['path']}\n{names}")
    blocks.append(
        add_section(
            "symbols",
            "# symbols of the selected files\n" + ("\n".join(symbol_lines) or "(none)"),
            max(200, int(budget_chars * 0.12)),
        )
    )

    diagnostics_status = "not_requested"
    diagnostics_text = "(not requested)"
    if include_diagnostics:
        diag = diagnostics(base, tuple(used_paths[:file_limit]) or ())
        diagnostics_status = str(diag["status"])
        if diag["status"] == "ok" and diag["findings"]:
            diagnostics_text = "\n".join(
                f"{item['path']}:{item['line']}:{item['column']} {item['code']} {item['message']}"
                for item in diag["findings"][:40]
            )
        elif diag["status"] == "ok":
            diagnostics_text = "(no findings)"
        else:
            diagnostics_text = f"({diag['status']}: {diag.get('reason', '')})"
            if diag.get("needed"):
                diagnostics_text += "\nneeded: " + "; ".join(diag["needed"][:3])
        blocks.append(
            add_section(
                "diagnostics",
                "# diagnostics (ruff, selected files)\n" + diagnostics_text,
                max(200, int(budget_chars * 0.1)),
            )
        )

    text = "\n\n".join(block for block in blocks if block)
    fingerprint = _sha256_bytes(text.encode("utf-8"))
    return {
        "schema": SCHEMA,
        "root": str(base),
        "task": task,
        "terms": terms,
        "budget": {
            "tokens": int(token_budget),
            "chars": budget_chars,
            "token_estimate": TOKEN_ESTIMATE,
        },
        "ranking": ranked[:30],
        "selected": used_paths,
        "sections": sections,
        "dropped": dropped[:40],
        "dropped_count": len(dropped),
        "block": {
            "text": text,
            "chars": len(text),
            "tokens": estimate_tokens(text),
            "token_estimate": TOKEN_ESTIMATE,
            "truncated": any(section["truncated"] for section in sections),
            "over_budget": len(text) > budget_chars,
        },
        "fingerprint": fingerprint,
        "fingerprint_short": fingerprint[:12],
        "map_fingerprint": map_payload["map_fingerprint"],
        "update": map_payload["update"],
        "mode": map_payload["mode"],
        "diagnostics_status": diagnostics_status,
    }


def pack_fingerprint(pack: dict[str, Any]) -> str:
    """The pack's own digest (recomputed from the text, not trusted from the dict)."""
    return _sha256_bytes(str(pack.get("block", {}).get("text", "")).encode("utf-8"))


def to_prompt_block(pack: dict[str, Any]) -> str:
    """The bytes the prompt builder consumes: the rendered pack text, nothing else."""
    return str(pack.get("block", {}).get("text", ""))


# ------------------------------------------------------------------ contracts


_FS_READ = Permission(
    filesystem=FilesystemPermission(
        scope="workspace",
        mode="read",
        roots=("<workspace>",),
        deny_globs=(),
        symlink_policy="a symlink target outside the workspace is refused",
    ),
    network=NetworkPermission(mode="none"),
    subprocess=SubprocessPermission(
        allowed=False,
        shell=False,
        env_policy="allowlist + credential scrub",
    ),
)

_FS_READ_PROC = Permission(
    filesystem=_FS_READ.filesystem,
    network=NetworkPermission(mode="none"),
    subprocess=SubprocessPermission(
        allowed=True,
        shell=False,
        interpreter="",
        default_timeout_s=20,
        max_timeout_s=60,
        kill="process group killed on timeout (subprocess.run timeout)",
        env_policy="allowlist + credential scrub (PYTHONPATH dropped)",
        isolation=("process; bubblewrap when the caller escalates",),
        stdin="closed (DEVNULL)",
    ),
)


def _schema(props: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": props,
        "required": required,
        "additionalProperties": False,
    }


MAP_INPUT = _schema(
    {
        "path": {"type": "string", "description": "sub-directory to index (default: the root)"},
        "tokens": {
            "type": "integer",
            "minimum": 100,
            "maximum": 20000,
            "description": "token budget of the rendered map (estimate: chars/4)",
        },
        "force": {"type": "boolean", "description": "ignore the cache and rebuild every fact"},
        "no_gitignore": {"type": "boolean", "description": "do not read .gitignore files"},
    },
    [],
)
MAP_OUTPUT = _schema(
    {
        "mode": {"type": "string", "description": "reused | incremental | rebuilt"},
        "entries": {"type": "array", "description": "ranked file facts"},
        "map": {"type": "object", "description": "rendered text + token accounting"},
        "update": {"type": "object", "description": "reused/reopened/added/removed counters"},
        "map_fingerprint": {"type": "string", "description": "digest over the file facts"},
    },
    ["mode", "entries", "map", "update", "map_fingerprint"],
)
SEARCH_INPUT = _schema(
    {
        "pattern": {"type": "string", "description": "regular expression or file-name glob"},
        "target": {
            "type": "string",
            "enum": ["content", "files"],
            "description": "search inside files, or match file names",
        },
        "path": {"type": "string", "description": "sub-directory to search"},
        "file_glob": {"type": "string", "description": "narrow the candidate files"},
        "output_mode": {
            "type": "string",
            "enum": ["content", "files_only", "count"],
            "description": "match lines | file paths | matches per file",
        },
        "context": {
            "type": "integer",
            "minimum": 0,
            "maximum": 10,
            "description": "context lines before and after each match",
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": MAX_SEARCH_LIMIT,
            "description": "maximum returned matches",
        },
    },
    ["pattern"],
)
SEARCH_OUTPUT = _schema(
    {
        "pattern": {"type": "string", "description": "the pattern as given"},
        "matches": {"type": "array", "description": "match records"},
        "count": {"type": "integer", "description": "returned matches"},
        "files_scanned": {"type": "integer", "description": "files inspected"},
        "ignored_files": {"type": "integer", "description": "files left out by the ignore rules"},
        "truncated": {"type": "boolean", "description": "a cap cut the result"},
    },
    ["pattern", "matches", "count", "files_scanned", "truncated"],
)
SYMBOLS_INPUT = _schema(
    {
        "path": {"type": "string", "description": "file or directory (default: the root)"},
        "language": {"type": "string", "description": "only this language"},
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 2000,
            "description": "maximum symbols per file",
        },
    },
    [],
)
SYMBOLS_OUTPUT = _schema(
    {
        "status": {"type": "string", "description": "ok | blocked | not_found"},
        "files": {"type": "array", "description": "one row per file with symbols"},
        "symbols_total": {"type": "integer", "description": "symbols found"},
        "unsupported": {"type": "array", "description": "files with no index + what is needed"},
        "needed": {"type": "array", "description": "tools a blocked answer requires"},
    },
    ["status", "files", "symbols_total"],
)
DIAGNOSTICS_INPUT = _schema(
    {
        "paths": {
            "type": "array",
            "items": {"type": "string"},
            "description": "files/dirs to check",
        },
        "linter": {
            "type": "string",
            "enum": ["auto", "ruff"],
            "description": "which diagnostic tool to drive (auto probes ruff)",
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 2000,
            "description": "maximum returned findings",
        },
    },
    [],
)
DIAGNOSTICS_OUTPUT = _schema(
    {
        "status": {"type": "string", "description": "ok | blocked | not_found"},
        "linter": {"type": "object", "description": "which tool was probed, and its version"},
        "findings": {"type": "array", "description": "typed diagnostics"},
        "counts_by_code": {"type": "object", "description": "findings per rule code"},
        "needed": {"type": "array", "description": "what a blocked answer requires"},
        "unsupported": {"type": "array", "description": "languages with no tool on this host"},
    },
    ["status", "findings"],
)
CHANGED_INPUT = _schema(
    {
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 5000,
            "description": "maximum returned changed files",
        }
    },
    [],
)
CHANGED_OUTPUT = _schema(
    {
        "status": {"type": "string", "description": "ok | blocked | unavailable"},
        "files": {"type": "array", "description": "changed file rows with their git code"},
        "branch": {"type": "string", "description": "current branch"},
        "head": {"type": "string", "description": "HEAD commit + subject"},
    },
    ["status", "files"],
)
BLAME_INPUT = _schema(
    {
        "path": {"type": "string", "description": "file to blame"},
        "line": {"type": "integer", "minimum": 1, "description": "line number (1-based)"},
    },
    ["path", "line"],
)
BLAME_OUTPUT = _schema(
    {
        "status": {"type": "string", "description": "ok | blocked | not_found"},
        "path": {"type": "string", "description": "the blamed file"},
        "line": {"type": "integer", "description": "the blamed line"},
        "author": {"type": "string", "description": "who wrote it"},
        "commit": {"type": "string", "description": "the commit"},
        "text": {"type": "string", "description": "the line itself"},
    },
    ["status", "path", "line"],
)
PACK_INPUT = _schema(
    {
        "task": {"type": "string", "minLength": 1, "description": "the task to pack context for"},
        "tokens": {
            "type": "integer",
            "minimum": 200,
            "maximum": 32000,
            "description": "token budget of the whole pack (estimate: chars/4)",
        },
        "files": {
            "type": "integer",
            "minimum": 1,
            "maximum": 40,
            "description": "how many ranked files to excerpt",
        },
        "diagnostics": {"type": "boolean", "description": "include a diagnostics section"},
    },
    ["task"],
)
PACK_OUTPUT = _schema(
    {
        "task": {"type": "string", "description": "the task as given"},
        "block": {"type": "object", "description": "the rendered text + its size"},
        "sections": {"type": "array", "description": "per-section budget accounting"},
        "ranking": {"type": "array", "description": "the task ranking that selected the files"},
        "fingerprint": {"type": "string", "description": "digest of the rendered block"},
    },
    ["task", "block", "sections", "fingerprint"],
)


def _contract(
    name: str,
    title: str,
    description: str,
    input_schema: dict[str, Any],
    output_schema: dict[str, Any],
    *,
    permission: Permission,
    tags: tuple[str, ...],
    entrypoint: str,
    smoke: dict[str, Any] | None = None,
) -> ToolContract:
    return ToolContract(
        name=name,
        title=title,
        description=description,
        module="skaldir.codectx",
        input_schema=input_schema,
        output_schema=output_schema,
        permission=permission,
        truncation=TRUNCATION[name],
        timeout_s=TIMEOUTS[name],
        tags=tags,
        mutates=(name == "map"),  # the map writes its cache; nothing writes the workspace
        entrypoint=entrypoint,
        smoke=dict(smoke or {}),
    )


CONTRACTS: dict[str, ToolContract] = {
    "code_map": _contract(
        "map",
        "Ranked repo map with a token budget",
        "A compact, ranked index of the workspace (path, language, lines, symbols, size) "
        "rendered under a token budget, served from an mtime/inode cache that re-reads only "
        "files that actually changed.",
        MAP_INPUT,
        MAP_OUTPUT,
        permission=_FS_READ_PROC,
        tags=("context", "index", "repo-map", "cache"),
        entrypoint="repo_map",
        smoke={"input": {"tokens": 400}, "expect": {"min_key": "map_fingerprint"}},
    ),
    "code_search": _contract(
        "search",
        "Regex content search and file-glob search",
        "Regex search over file contents or a glob over file names, with result caps, "
        "context lines, .gitignore rules and the honest files_scanned counter.",
        SEARCH_INPUT,
        SEARCH_OUTPUT,
        permission=_FS_READ,
        tags=("context", "search", "read"),
        entrypoint="search",
        smoke={"input": {"pattern": "def ", "limit": 5}, "expect": {"min_key": "files_scanned"}},
    ),
    "code_symbols": _contract(
        "symbols",
        "Symbols of a file or directory",
        "Definitions per file: Python through the standard library's ast, every other "
        "language through ctags when it is installed. No index available means a blocked "
        "answer naming what is needed, never a guessed symbol.",
        SYMBOLS_INPUT,
        SYMBOLS_OUTPUT,
        permission=_FS_READ_PROC,
        tags=("context", "symbols", "index"),
        entrypoint="symbols",
        smoke={
            "input": {"path": "src/skaldir/context.py", "limit": 10},
            "expect": {"min_key": "status"},
        },
    ),
    "code_diagnostics": _contract(
        "diagnostics",
        "Real diagnostics from a real tool",
        "Python diagnostics through ruff (JSON), or an honest blocked result naming the "
        "linter or language server that would be needed.",
        DIAGNOSTICS_INPUT,
        DIAGNOSTICS_OUTPUT,
        permission=_FS_READ_PROC,
        tags=("context", "diagnostics", "lint"),
        entrypoint="diagnostics",
        smoke={"input": {"paths": ["src/skaldir/context.py"]}, "expect": {"min_key": "status"}},
    ),
    "code_changed": _contract(
        "changed",
        "Changed and untracked files (git)",
        "git status --porcelain plus the branch and HEAD, so a context pack can rank what "
        "the user is actually working on.",
        CHANGED_INPUT,
        CHANGED_OUTPUT,
        permission=_FS_READ_PROC,
        tags=("context", "git"),
        entrypoint="git_changed",
        smoke={"input": {}, "expect": {"min_key": "status"}},
    ),
    "code_blame": _contract(
        "blame",
        "One line's author, through git blame",
        "git blame --line-porcelain for a single line: author, commit, time and the line.",
        BLAME_INPUT,
        BLAME_OUTPUT,
        permission=_FS_READ_PROC,
        tags=("context", "git"),
        entrypoint="git_blame",
        smoke={"input": {"path": "README.md", "line": 1}, "expect": {"min_key": "status"}},
    ),
    "code_pack": _contract(
        "pack",
        "Bounded, ranked context block for a task",
        "Assembles the repo map, changed files, the best task-matched file excerpts, their "
        "symbols and the diagnostics of the selection into one byte-stable block under a "
        "token budget — the artifact the prompt builder consumes.",
        PACK_INPUT,
        PACK_OUTPUT,
        permission=_FS_READ_PROC,
        tags=("context", "pack", "prompt"),
        entrypoint="context_pack",
        smoke={"input": {"task": "repo map token budget"}, "expect": {"min_key": "block"}},
    ),
}

#: verb → (contract, runner). The runner is the function the CLI drives.
RUNNERS: dict[str, Any] = {
    "code_map": repo_map,
    "code_search": search,
    "code_symbols": symbols,
    "code_diagnostics": diagnostics,
    "code_changed": git_changed,
    "code_blame": git_blame,
    "code_pack": context_pack,
}


def surface_report() -> dict[str, Any]:
    """The declared surface of the whole harness: schemas, permissions, limits."""
    return {
        "schema": SCHEMA,
        "verbs": [
            {
                "verb": name.split("_", 1)[1],
                "name": name,
                "contract": CONTRACTS[name].to_dict(),
            }
            for name in sorted(CONTRACTS)
        ],
        "tools_available": {
            "ruff": probe_linter("ruff"),
            "ctags": _ctags_available() or "not installed",
            "git": shutil.which("git") or "not installed",
            "python-ast": "stdlib",
            "language_servers": "none installed by this harness",
        },
        "limits": {
            "max_files": MAX_FILES,
            "max_file_bytes": MAX_FILE_BYTES,
            "max_walk_depth": MAX_WALK_DEPTH,
            "max_ignore_patterns": MAX_IGNORE_PATTERNS,
            "chars_per_token": CHARS_PER_TOKEN,
            "token_estimate": TOKEN_ESTIMATE,
        },
    }


def run_verb(name: str, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Drive one verb through its typed contract (schema check → runner → truncation)."""
    key = name if name in CONTRACTS else f"code_{name}"
    contract = CONTRACTS.get(key)
    if contract is None:
        return failure(name, "not_found", f"unknown codectx verb: {name}")
    refusal = enforce_input(contract, args)
    if refusal is not None:
        return refusal
    try:
        payload = RUNNERS[key](Path(ctx.root), **dict(args))
    except TypeError as exc:
        return failure(key, "usage", f"invalid arguments for {key}: {exc}")
    error_kind = str(payload.get("error_kind", "") or "")
    result = ToolResult(
        tool=key,
        ok=not error_kind,
        data=payload,
        error=str(payload.get("reason", "")) if error_kind else "",
        error_kind=error_kind,
        evidence=[
            {
                "ref": str(Path(ctx.root)),
                "kind": "run",
                "note": f"{key} over {payload.get('path', ctx.root)}",
            }
        ],
    )
    return result


__all__ = [
    "CHARS_PER_TOKEN",
    "CONTRACTS",
    "DEFAULT_MAP_TOKENS",
    "DEFAULT_PACK_TOKENS",
    "EXTRA_SKIP_DIRS",
    "LANGUAGES",
    "LINTER_NAMES",
    "LSP_NEEDED",
    "MAX_FILES",
    "RUNNERS",
    "SCHEMA",
    "TOKEN_ESTIMATE",
    "VERB_NAMES",
    "Walk",
    "context_pack",
    "ctags_symbols",
    "default_state_dir",
    "diagnostics",
    "estimate_tokens",
    "git_blame",
    "git_changed",
    "kind_of",
    "language_of",
    "load_ignore_rules",
    "map_fingerprint",
    "pack_fingerprint",
    "probe_linter",
    "python_symbols",
    "rank_for_task",
    "refresh_map",
    "render_map",
    "repo_map",
    "resolve_workspace",
    "run_verb",
    "search",
    "surface_report",
    "symbols",
    "task_terms",
    "to_prompt_block",
    "walk_workspace",
]
