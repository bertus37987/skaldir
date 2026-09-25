"""``skaldir code …`` — the coding-context harness on the product CLI.

Nine verbs, one per question a coding agent asks before it edits anything::

    code map          the ranked repo map under a token budget (mtime/inode cached)
    code search       regex content search and file-glob search (caps, gitignore, context)
    code symbols      definitions of a file or tree (python ast, ctags when installed)
    code diagnostics  real diagnostics (ruff) — or a blocked answer naming what is needed
    code changed      changed/untracked files through git
    code blame        one line's author, commit and text
    code pack         the bounded, ranked context block for a task description
    code tools        the declared surface: schemas, permissions, timeouts, truncation
    code cache        what the map cache holds right now (and ``--clear``)

The ``code`` group is **shared** with the H8 block (``src/skaldir/code_exec_cli.py``:
``code run`` / ``code explain``). ``register_cli`` is therefore create-or-extend — whichever
module is wired first creates the group, the other attaches its verbs to the same action, and
neither set is ever dropped.

**Integration — the only change ``cli.py`` needs (2 lines).** In
``cli.build_parser()``, right after ``sub = parser.add_subparsers(...)`` (where the
``jev`` and ``memory`` groups are already planted)::

    from .codectx_cli import register_cli as _register_code   # +
    _register_code(sub)                                      # +

``register_cli`` installs its own ``COMMANDS`` entries through
``cli_shared.install_contract_entries`` — the same mechanism ``tools_core.cli``,
``act_cli`` and ``project_context_cli`` use — so nothing has to be pasted into
``cli_contract.py``. :func:`proposed_contract_entries` returns the identical objects for a
reviewer who prefers the literal table. The group name is ``code`` and not ``context``
because ``context`` already belongs to ``project_context_cli`` (the AGENTS.md/SOUL.md
block); ``code pack`` is this harness's context-pack command.

Exit codes are the product CLI's vocabulary: ``0`` ok · ``1`` usage (an invalid regex, a
non-object payload) · ``3`` blocked (no symbol index, no linter, a path outside the
workspace) · ``5`` not found (a path that does not exist, an unknown verb) · ``8`` a
timeout. A search that finds nothing is **not** a failure: it is data with ``count: 0``
and a non-zero ``files_scanned``, and it exits ``0``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import codectx
from .tools_core.contract import ERROR_EXIT, EXIT_INTERNAL, EXIT_OK, EXIT_UNKNOWN

try:  # the shared group plumbing (one implementation, never copied)
    from .cli_shared import (
        blocked,
        dump,
        install_contract_entries,
        not_found,
        product_contract,
        share_common_flags,
        text_mode,
    )
except Exception:  # pragma: no cover — a build without the product CLI helpers
    install_contract_entries = None  # type: ignore[assignment]

    def product_contract() -> Any:  # type: ignore[misc]
        return None

    def share_common_flags(parser: Any) -> None:  # type: ignore[misc]
        return None

    def text_mode(args: argparse.Namespace) -> bool:  # type: ignore[misc]
        return True

    def dump(payload: Any) -> None:  # type: ignore[misc]
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str))

    def blocked(  # type: ignore[misc]
        message: str,
        *,
        hint: str = "",
        next_command: str = "",
        reason: str = "",
        details: Any = None,
    ) -> int:
        print(f"blocked: {message}", file=sys.stderr, flush=True)
        return 3

    def not_found(message: str, *, hint: str = "", details: Any = None) -> int:  # type: ignore[misc]
        print(f"not found: {message}", file=sys.stderr, flush=True)
        return 5


_CONTRACT = product_contract()

#: True while ``python -m skaldir.codectx_cli`` drives the verbs itself.
_STANDALONE = False

#: The parsed arguments of that standalone run (so a verb can print its own JSON).
_STANDALONE_ARGS: argparse.Namespace = argparse.Namespace()

_EVIDENCE_KINDS = ("code", "test", "file", "run", "verification")


def _wants_text(args: argparse.Namespace) -> bool:
    """Human text only when explicitly asked (``--text``); the envelope owns stdout."""
    if _STANDALONE:
        return not bool(getattr(args, "json", False))
    return text_mode(args)


# --------------------------------------------------------------- envelope glue


def _publish(
    payload: Any,
    *,
    evidence: tuple[dict, ...] = (),
    warnings: tuple[str, ...] = (),
    more: tuple[tuple[str, str], ...] = (),
) -> None:
    """Hand the payload, its evidence and its hints to the shared envelope."""
    if _STANDALONE:
        if bool(getattr(_STANDALONE_ARGS, "json", False)):
            dump(payload)
        return
    if _CONTRACT is None:
        return
    try:
        _CONTRACT.set_data(payload)
    except Exception:  # noqa: BLE001 — an envelope problem must never break a verb
        return
    for item in evidence:
        kind = str(item.get("kind", "code"))
        _CONTRACT.add_evidence(
            str(item.get("ref", "")),
            kind if kind in _EVIDENCE_KINDS else "code",
            str(item.get("note", "")),
        )
    for note in warnings:
        _CONTRACT.warn(str(note))
    for command, reason in more:
        _CONTRACT.next_action(str(command), str(reason))


def _soft_fail(code: int, message: str, *, hint: str = "", details: dict | None = None) -> int:
    """Report a refusal through the envelope, falling back to stderr when there is none."""
    if _CONTRACT is not None and not _STANDALONE:
        for name in ("fail",):
            handler = getattr(_CONTRACT, name, None)
            if handler is not None:
                try:
                    return int(handler(code, message, hint=hint, details=details))
                except TypeError:
                    continue
    print(f"error: {message}", file=sys.stderr, flush=True)
    return int(code)


# ------------------------------------------------------------------- plumbing


def _state_dir(args: argparse.Namespace) -> str:
    explicit = str(getattr(args, "state", "") or "")
    if explicit:
        return explicit
    runtime = str(getattr(args, "runtime", "") or "")
    if runtime:
        return str(Path(runtime) / "codectx")
    return str(codectx.default_state_dir())


def _workspace(args: argparse.Namespace) -> Path:
    """The directory this call indexes: ``--workspace``, else the git root of ``--path``."""
    explicit = str(getattr(args, "workspace", "") or "")
    if explicit:
        return codectx.resolve_workspace(Path.cwd(), explicit)
    start = str(getattr(args, "path", "") or "")
    if start and start not in (".", ""):
        candidate = Path(start).expanduser()
        if candidate.is_dir():
            return codectx.resolve_workspace(candidate)
    return codectx.resolve_workspace(Path.cwd())


def _finish(
    payload: dict[str, Any],
    *,
    evidence: tuple[dict, ...] = (),
    warnings: tuple[str, ...] = (),
    more: tuple[tuple[str, str], ...] = (),
) -> int:
    """Publish one verb's payload and turn its ``error_kind`` into the CLI's exit code."""
    _publish(payload, evidence=evidence, warnings=warnings, more=more)
    kind = str(payload.get("error_kind", "") or "")
    code = ERROR_EXIT.get(kind, EXIT_INTERNAL)
    if not kind:
        return EXIT_OK
    message = str(payload.get("reason", "") or payload.get("status", "the verb did not complete"))
    detail = {
        "status": payload.get("status", ""),
        "needed": payload.get("needed", []),
        "path": payload.get("path", ""),
    }
    if code == 3:
        return blocked(
            message,
            hint="the payload names what is missing; nothing was guessed",
            reason=str(payload.get("status", "blocked")),
            details=detail,
        )
    if code == 5:
        return not_found(message, details=detail)
    return _soft_fail(code, message, details=detail)


def _root_evidence(root: Path, note: str) -> dict:
    return {"ref": str(root), "kind": "file", "note": note}


# ---------------------------------------------------------------------- verbs


def _cmd_map(args: argparse.Namespace) -> int:
    root = _workspace(args)
    state = _state_dir(args)
    payload = codectx.repo_map(
        root,
        token_budget=int(getattr(args, "tokens", codectx.DEFAULT_MAP_TOKENS)),
        state_dir=state,
        use_gitignore=not bool(getattr(args, "no_ignore", False)),
        trust_dir_mtime=bool(getattr(args, "trust_dir_mtime", False)),
        force=bool(getattr(args, "force", False)),
    )
    evidence = [
        _root_evidence(root, "workspace the map describes"),
        {
            "ref": payload["cache"]["path"],
            "kind": "file",
            "note": f"map cache ({payload['mode']}, reopened {payload['update']['reopened']})",
        },
    ]
    more: tuple[tuple[str, str], ...] = ()
    if payload["map"]["truncated"]:
        more = (
            (
                f"skaldir code map --tokens {int(getattr(args, 'tokens', 1500)) * 2} --json",
                f"{payload['map']['dropped']} file(s) were left out of the rendered map",
            ),
        )
    if _wants_text(args):
        print(payload["map"]["text"])
        print(
            f"# {payload['files_indexed']} file(s) indexed · mode {payload['mode']} · "
            f"reused {payload['update']['reused']} · reopened {payload['update']['reopened']} · "
            f"~{payload['map']['tokens']} tokens (estimate {payload['token_estimate']})",
            file=sys.stderr,
        )
    return _finish(payload, evidence=tuple(evidence), more=more)


def _cmd_search(args: argparse.Namespace) -> int:
    root = _workspace(args)
    payload = codectx.search(
        root,
        args.pattern,
        target=str(getattr(args, "target", "content") or "content"),
        subpath=str(getattr(args, "path", "") or ""),
        file_glob=str(getattr(args, "glob", "") or ""),
        output_mode=str(getattr(args, "mode", "content") or "content"),
        context=int(getattr(args, "context", 0) or 0),
        limit=int(getattr(args, "limit", codectx.DEFAULT_SEARCH_LIMIT)),
        offset=int(getattr(args, "offset", 0) or 0),
        case_sensitive=not bool(getattr(args, "ignore_case", False)),
        use_gitignore=not bool(getattr(args, "no_ignore", False)),
    )
    evidence = (
        {
            "ref": f"search {args.pattern!r} over {str(root)}",
            "kind": "run",
            "note": (
                f"{payload.get('count', 0)} returned / {payload.get('total_matches', 0)} found in "
                f"{payload.get('files_scanned', 0)} scanned file(s)"
            ),
        },
        _root_evidence(root, "workspace that was searched"),
    )
    if _wants_text(args):
        if not payload.get("matches"):
            print(
                f"no match for {args.pattern!r} ({payload.get('files_scanned', 0)} file(s) "
                f"scanned, {payload.get('ignored_files', 0)} ignored)"
            )
        for row in payload.get("matches", []):
            if "line" in row:
                print(f"{row['path']}:{row['line']}: {row['text']}")
                for extra in row.get("context", []):
                    print(f"  {extra}")
            elif "count" in row:
                print(f"{row['path']}: {row['count']} match(es)")
            else:
                print(row["path"])
    return _finish(payload, evidence=evidence)


def _cmd_symbols(args: argparse.Namespace) -> int:
    root = _workspace(args)
    payload = codectx.symbols(
        root,
        str(getattr(args, "path", "") or ""),
        language_filter=str(getattr(args, "language", "") or ""),
        limit=int(getattr(args, "limit", 200)),
        use_ctags=not bool(getattr(args, "no_ctags", False)),
    )
    evidence = (
        {
            "ref": f"{payload.get('path', '.')} · {payload.get('tools', {}).get('python', 'ast')}",
            "kind": "run",
            "note": (
                f"{payload.get('symbols_total', 0)} symbol(s) in "
                f"{payload.get('files_indexed', 0)} file(s), "
                f"{payload.get('unsupported_count', 0)} file(s) without an index"
            ),
        },
        _root_evidence(root, "workspace that was read"),
    )
    warnings = tuple(
        f"{row['path']}: {row['reason']} (needs: {'; '.join(row['needed'])})"
        for row in payload.get("unsupported", [])[:5]
    )
    more: tuple[tuple[str, str], ...] = ()
    if payload.get("status") == "blocked":
        more = (
            (
                "skaldir code symbols <python-file> --json",
                "the Python half needs no extra tool (stdlib ast)",
            ),
        )
    if _wants_text(args):
        for row in payload.get("files", []):
            print(f"{row['path']} [{row.get('tool', '')}]")
            for item in row.get("symbols", []):
                parent = f"{item.get('parent', '')}." if item.get("parent") else ""
                print(f"  {item['line']:>5}  {item['kind']:<12} {parent}{item['name']}")
        if payload.get("status") == "blocked":
            print("blocked: no symbol index for the requested path(s)", file=sys.stderr)
            for item in payload.get("needed", []):
                print(f"  needs: {item}", file=sys.stderr)
    return _finish(payload, evidence=evidence, warnings=warnings, more=more)


def _cmd_diagnostics(args: argparse.Namespace) -> int:
    root = _workspace(args)
    payload = codectx.diagnostics(
        root,
        tuple(getattr(args, "paths", ()) or ()),
        linter=str(getattr(args, "linter", "auto") or "auto"),
        limit=int(getattr(args, "limit", 200)),
    )
    probe = payload.get("linter") or {}
    evidence = (
        {
            "ref": " ".join(str(item) for item in (payload.get("argv") or probe.get("argv") or []))
            or f"probe: {probe.get('name', '?')}",
            "kind": "run",
            "note": (
                f"{probe.get('version', probe.get('reason', 'not probed'))} · "
                f"{payload.get('files_checked', 0)} file(s), "
                f"{payload.get('total_findings', 0)} finding(s)"
            ),
        },
        _root_evidence(root, "workspace that was checked"),
    )
    more: tuple[tuple[str, str], ...] = ()
    if payload.get("status") == "blocked":
        more = (
            (
                "skaldir code diagnostics --linter ruff --json",
                "re-probe the linter after installing it",
            ),
        )
    if _wants_text(args):
        if payload.get("status") == "ok":
            for item in payload.get("findings", []):
                print(
                    f"{item['path']}:{item['line']}:{item['column']} "
                    f"{item['code']} {item['message']}"
                )
            print(
                f"# {payload.get('total_findings', 0)} finding(s) in "
                f"{payload.get('files_checked', 0)} file(s) via {probe.get('name', '?')}",
                file=sys.stderr,
            )
        else:
            print(f"{payload.get('status')}: {payload.get('reason', '')}", file=sys.stderr)
            for item in payload.get("needed", []):
                print(f"  needed: {item}", file=sys.stderr)
    warnings = tuple(
        f"{row['language']} ({row['files']} file(s)): {row['reason']}"
        for row in payload.get("unsupported", [])[:5]
    )
    return _finish(payload, evidence=evidence, warnings=warnings, more=more)


def _cmd_changed(args: argparse.Namespace) -> int:
    root = _workspace(args)
    payload = codectx.git_changed(root, limit=int(getattr(args, "limit", 500)))
    evidence = (
        {
            "ref": "git status --porcelain=v1 --untracked-files=all",
            "kind": "run",
            "note": f"{payload.get('count', 0)} changed file(s) on {payload.get('branch', '?')}",
        },
        _root_evidence(root, "git work tree that was read"),
    )
    if _wants_text(args):
        for row in payload.get("files", []):
            print(f"{row['code']:>3} {row['path']}")
        print(
            f"# {payload.get('count', 0)} changed · branch {payload.get('branch', '?')} · "
            f"{payload.get('head', '')[:60]}",
            file=sys.stderr,
        )
    return _finish(payload, evidence=evidence)


def _cmd_blame(args: argparse.Namespace) -> int:
    root = _workspace(args)
    payload = codectx.git_blame(root, args.path, int(args.line))
    evidence = (
        {
            "ref": f"git blame --line-porcelain -L {args.line},{args.line} -- {args.path}",
            "kind": "run",
            "note": f"{payload.get('author', '?')} @ {str(payload.get('commit', ''))[:12]}",
        },
    )
    if _wants_text(args):
        if payload.get("status") == "ok":
            print(f"{payload['path']}:{payload['line']}  {payload.get('author', '?')}")
            print(f"  commit: {str(payload.get('commit', ''))[:12]}  {payload.get('summary', '')}")
            print(f"  {payload.get('text', '')}")
        else:
            print(f"{payload.get('status')}: {payload.get('reason', '')}", file=sys.stderr)
    return _finish(payload, evidence=evidence)


def _cmd_pack(args: argparse.Namespace) -> int:
    root = _workspace(args)
    payload = codectx.context_pack(
        root,
        args.task,
        token_budget=int(getattr(args, "tokens", codectx.DEFAULT_PACK_TOKENS)),
        file_limit=int(getattr(args, "files", 6)),
        include_diagnostics=not bool(getattr(args, "no_diagnostics", False)),
        state_dir=_state_dir(args),
        force=bool(getattr(args, "force", False)),
    )
    evidence = (
        {
            "ref": f"pack fingerprint {payload['fingerprint_short']}",
            "kind": "file",
            "note": (
                f"{payload['block']['chars']} chars ≈ {payload['block']['tokens']} tokens "
                f"(estimate)"
            ),
        },
        {
            "ref": str(root / ".codectx"),
            "kind": "run",
            "note": (
                f"map {payload['mode']} · {len(payload['selected'])} file(s) selected · "
                f"{payload['dropped_count']} dropped"
            ),
        },
    )
    more: tuple[tuple[str, str], ...] = ()
    if payload["block"]["over_budget"]:
        more = (
            (
                f"skaldir code pack {json.dumps(args.task)[:40]} "
                f"--tokens {payload['budget']['tokens'] * 2}",
                "the block exceeded its budget and was truncated section by section",
            ),
        )
    if _wants_text(args):
        print(codectx.to_prompt_block(payload))
        print(f"# fingerprint {payload['fingerprint']}", file=sys.stderr)
    return _finish(payload, evidence=evidence, more=more)


def _cmd_tools(args: argparse.Namespace) -> int:
    payload = codectx.surface_report()
    evidence = tuple(
        {"ref": "src/skaldir/codectx.py", "kind": "code", "note": f"{row['name']} contract"}
        for row in payload["verbs"]
    )
    if _wants_text(args):
        for row in payload["verbs"]:
            contract = row["contract"]
            print(
                f"{row['verb']:<12} timeout={contract['timeout_s']:>3}s "
                f"truncate={contract['truncation']['max_bytes']:>6}b "
                f"fs={contract['permissions']['filesystem']['mode']:>4} "
                f"proc={'yes' if contract['permissions']['subprocess']['allowed'] else 'no':>3} "
                f"net={contract['permissions']['network']['mode']}"
            )
        print(f"limits: {payload['limits']}")
    return _finish(payload, evidence=evidence)


def _cmd_cache(args: argparse.Namespace) -> int:
    root = _workspace(args)
    state = _state_dir(args)
    path = codectx._cache_path(state, root)
    removed: list[str] = []
    if bool(getattr(args, "clear", False)):
        for candidate in sorted(Path(state).glob("map-*.json")) if Path(state).is_dir() else []:
            try:
                candidate.unlink()
                removed.append(str(candidate))
            except OSError:
                continue
    cached = codectx._load_cache(path)
    entries = cached.get("entries") or {}
    payload = {
        "status": "ok",
        "workspace": str(root),
        "state_dir": str(state),
        "cache_path": str(path),
        "exists": bool(cached),
        "entries": len(entries),
        "map_fingerprint": str(cached.get("map_fingerprint", "")),
        "version": int(cached.get("version", 0)),
        "cleared": removed,
        "validation": "size + mtime_ns + inode per file",
    }
    evidence = (
        {"ref": str(path), "kind": "file", "note": f"{len(entries)} cached file fact(s)"},
        _root_evidence(root, "workspace the cache belongs to"),
    )
    if _wants_text(args):
        state_label = "present" if cached else "absent"
        print(f"cache {state_label}: {path} ({len(entries)} entries, version {payload['version']})")
        for item in removed:
            print(f"removed {item}")
    return _finish(payload, evidence=evidence)


# -------------------------------------------------------------------- parser


_GROUP_HELP = (
    "coding context (repo map, search, symbols, diagnostics, context pack); shared with "
    "`code run`/`code explain` from the H8 block"
)


def _add_text_flag(parser: argparse.ArgumentParser) -> None:
    """``--text``: human output on stdout (the JSON envelope owns it otherwise)."""
    parser.add_argument(
        "--text", action="store_true", help="human-readable output instead of the JSON payload"
    )


def _add_workspace_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--workspace", default="", help="workspace root (default: the git root)")
    parser.add_argument("--path", default="", help="sub-directory to work in")
    parser.add_argument("--state", default="", help="map cache directory override")
    parser.add_argument("--runtime", default="", help="runtime dir (default: the product runtime)")


def _add_verbs(sub: Any) -> None:
    p_map = sub.add_parser("map", help="the ranked repo map under a token budget")
    p_map.add_argument("--tokens", type=int, default=codectx.DEFAULT_MAP_TOKENS)
    p_map.add_argument("--force", action="store_true", help="rebuild every file fact")
    p_map.add_argument("--no-ignore", action="store_true", help="do not read .gitignore files")
    p_map.add_argument(
        "--trust-dir-mtime",
        action="store_true",
        help=(
            "skip the per-file stat pass when every directory mtime matches "
            "(can serve stale facts)"
        ),
    )
    _add_workspace_flags(p_map)
    _add_text_flag(p_map)
    share_common_flags(p_map)
    p_map.set_defaults(func=_cmd_map)

    p_search = sub.add_parser("search", help="regex content search or file-glob search")
    p_search.add_argument("pattern")
    p_search.add_argument("--target", choices=("content", "files"), default="content")
    p_search.add_argument("--glob", default="", help="only files whose name matches this glob")
    p_search.add_argument(
        "--mode", choices=("content", "files_only", "count"), default="content"
    )
    p_search.add_argument("--context", type=int, default=0, help="context lines around a match")
    p_search.add_argument("--limit", type=int, default=codectx.DEFAULT_SEARCH_LIMIT)
    p_search.add_argument("--offset", type=int, default=0)
    p_search.add_argument("--ignore-case", action="store_true")
    p_search.add_argument("--no-ignore", action="store_true", help="ignore the .gitignore rules")
    _add_workspace_flags(p_search)
    _add_text_flag(p_search)
    share_common_flags(p_search)
    p_search.set_defaults(func=_cmd_search)

    p_symbols = sub.add_parser("symbols", help="definitions of a file or directory tree")
    p_symbols.add_argument("path", nargs="?", default="")
    p_symbols.add_argument("--language", default="", help="only this language")
    p_symbols.add_argument("--limit", type=int, default=200)
    p_symbols.add_argument("--no-ctags", action="store_true", help="python files only (stdlib ast)")
    _add_workspace_flags(p_symbols)
    _add_text_flag(p_symbols)
    share_common_flags(p_symbols)
    p_symbols.set_defaults(func=_cmd_symbols)

    p_diag = sub.add_parser(
        "diagnostics", help="real diagnostics, or a blocked answer naming the tool"
    )
    p_diag.add_argument("paths", nargs="*", help="files/dirs to check (default: the workspace)")
    p_diag.add_argument("--linter", choices=("auto", "ruff"), default="auto")
    p_diag.add_argument("--limit", type=int, default=200)
    _add_workspace_flags(p_diag)
    _add_text_flag(p_diag)
    share_common_flags(p_diag)
    p_diag.set_defaults(func=_cmd_diagnostics)

    p_changed = sub.add_parser("changed", help="changed and untracked files (git)")
    p_changed.add_argument("--limit", type=int, default=500)
    _add_workspace_flags(p_changed)
    _add_text_flag(p_changed)
    share_common_flags(p_changed)
    p_changed.set_defaults(func=_cmd_changed)

    p_blame = sub.add_parser("blame", help="one line's author, commit and text")
    p_blame.add_argument("path")
    p_blame.add_argument("line", type=int)
    _add_workspace_flags(p_blame)
    _add_text_flag(p_blame)
    share_common_flags(p_blame)
    p_blame.set_defaults(func=_cmd_blame)

    p_pack = sub.add_parser("pack", help="the bounded, ranked context block for a task")
    p_pack.add_argument("task", help="the task description to pack context for")
    p_pack.add_argument("--tokens", type=int, default=codectx.DEFAULT_PACK_TOKENS)
    p_pack.add_argument("--files", type=int, default=6, help="how many files to excerpt")
    p_pack.add_argument("--no-diagnostics", action="store_true")
    p_pack.add_argument("--force", action="store_true", help="rebuild the map first")
    _add_workspace_flags(p_pack)
    _add_text_flag(p_pack)
    share_common_flags(p_pack)
    p_pack.set_defaults(func=_cmd_pack)

    p_tools = sub.add_parser("tools", help="the declared surface of the harness")
    _add_text_flag(p_tools)
    share_common_flags(p_tools)
    p_tools.set_defaults(func=_cmd_tools)

    p_cache = sub.add_parser("cache", help="what the map cache holds right now")
    p_cache.add_argument("--clear", action="store_true", help="delete the cache files")
    _add_workspace_flags(p_cache)
    _add_text_flag(p_cache)
    share_common_flags(p_cache)
    p_cache.set_defaults(func=_cmd_cache)


def _entry(name: str, layer: str, ref: str, text: str, **kwargs: Any) -> Any:
    """One ``COMMANDS`` entry (the compact table shape ``cli_contract`` uses)."""
    if _CONTRACT is None:
        return None
    spec = _CONTRACT.CommandSpec
    return spec(layer, ({"ref": ref, "kind": "code", "note": name},), text, **kwargs)


def proposed_contract_entries() -> dict[str, Any]:
    """The ``COMMANDS`` entries this group installs (identical objects)."""
    code_ref = "src/skaldir/codectx.py"
    cli_ref = "src/skaldir/codectx_cli.py"
    entries = {
        "code.map": _entry(
            "code.map",
            "EXPAND",
            code_ref,
            "the ranked repo map (paths, languages, lines, symbols, sizes) under a token budget",
            mutates=True,
            exit_codes=(0, 1, 7),
            data_schema={
                "type": "object",
                "properties": {
                    "mode": {"type": "string"},
                    "entries": {"type": "array"},
                    "map": {"type": "object"},
                    "update": {"type": "object"},
                    "map_fingerprint": {"type": "string"},
                },
                "required": ["mode", "entries", "map", "update", "map_fingerprint"],
            },
            notes="writes ONLY its cache under <runtime>/codectx (never the workspace); "
            "mode reused|incremental|rebuilt names how much work this call did",
        ),
        "code.search": _entry(
            "code.search",
            "EXPAND",
            code_ref,
            "regex content search or file-glob search, with caps, context lines "
            "and gitignore rules",
            exit_codes=(0, 1, 3, 5, 7),
            data_schema={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "matches": {"type": "array"},
                    "count": {"type": "integer"},
                    "files_scanned": {"type": "integer"},
                    "truncated": {"type": "boolean"},
                },
                "required": ["pattern", "matches", "count", "files_scanned", "truncated"],
            },
            notes="no match is data (exit 0 with count 0); an invalid regex exits 1, "
            "a path outside the workspace exits 3, a missing path exits 5",
        ),
        "code.symbols": _entry(
            "code.symbols",
            "EXPAND",
            code_ref,
            "definitions per file: python through ast, other languages through ctags",
            exit_codes=(0, 3, 5, 7),
            data_schema={
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "files": {"type": "array"},
                    "symbols_total": {"type": "integer"},
                    "needed": {"type": "array"},
                },
                "required": ["status", "files", "symbols_total"],
            },
            notes="no index for the requested language exits 3 and names the tool; "
            "a guess is never returned",
        ),
        "code.diagnostics": _entry(
            "code.diagnostics",
            "VERIFY",
            code_ref,
            "real diagnostics from ruff, or an honest blocked answer naming the missing tool",
            exit_codes=(0, 3, 5, 7, 8),
            data_schema={
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "linter": {"type": "object"},
                    "findings": {"type": "array"},
                    "counts_by_code": {"type": "object"},
                    "needed": {"type": "array"},
                },
                "required": ["status", "findings"],
            },
            notes="no linter reachable exits 3 (never an empty 'all clean'); the probe names "
            "the argv it would run",
        ),
        "code.changed": _entry(
            "code.changed",
            "PERSIST",
            code_ref,
            "changed and untracked files through git status",
            exit_codes=(0, 3, 7),
            data_schema={
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "files": {"type": "array"},
                    "branch": {"type": "string"},
                },
                "required": ["status", "files"],
            },
            notes="outside a git work tree the status is unavailable (exit 0 with the reason); "
            "a missing git exits 3",
        ),
        "code.blame": _entry(
            "code.blame",
            "PERSIST",
            code_ref,
            "one line's author, commit and text through git blame",
            exit_codes=(0, 3, 5, 7, 8),
            data_schema={
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "path": {"type": "string"},
                    "line": {"type": "integer"},
                    "author": {"type": "string"},
                },
                "required": ["status", "path", "line"],
            },
            notes="an uncommitted line has no blame and exits 5 with git's own reason",
        ),
        "code.pack": _entry(
            "code.pack",
            "EXPAND",
            code_ref,
            "the bounded, ranked context block for a task description",
            mutates=True,
            exit_codes=(0, 1, 3, 7),
            data_schema={
                "type": "object",
                "properties": {
                    "task": {"type": "string"},
                    "block": {"type": "object"},
                    "sections": {"type": "array"},
                    "fingerprint": {"type": "string"},
                },
                "required": ["task", "block", "sections", "fingerprint"],
            },
            notes="byte-stable for the same workspace and task (no clock in the render); "
            "fingerprint is the digest a prompt builder freezes; --text prints exactly the block",
        ),
        "code.tools": _entry(
            "code.tools",
            "EXPAND",
            cli_ref,
            "the declared surface: schemas, permissions, timeouts and truncation per verb",
            data_schema={"type": "object", "additionalProperties": True},
            notes="read-only self-description; the probes report ruff/ctags/git availability",
        ),
        "code.cache": _entry(
            "code.cache",
            "EXPAND",
            cli_ref,
            "what the repo-map cache holds right now",
            mutates=True,
            exit_codes=(0, 7),
            data_schema={
                "type": "object",
                "properties": {
                    "cache_path": {"type": "string"},
                    "entries": {"type": "integer"},
                    "exists": {"type": "boolean"},
                },
                "required": ["cache_path", "entries", "exists"],
            },
            notes="--clear removes the map cache files only; the workspace is never touched",
        ),
    }
    return {path: entry for path, entry in entries.items() if entry is not None}


def _verb_action(parent: Any) -> Any:
    """The verb action ``parent`` already carries, or ``None`` when it has none yet."""
    for item in getattr(parent, "_actions", []):
        if isinstance(item, argparse._SubParsersAction):
            return item
    return None


def register_cli(subparsers: Any) -> Any:
    """Add the ``code`` group — or extend the one that already exists.

    The top-level name ``code`` is **shared** with the H8 block
    (``src/skaldir/code_exec_cli.py``, ``code run`` / ``code explain``): whichever module is
    wired first creates the group and the other attaches its verb set to the same action.
    Returning early on an existing group would drop this harness's nine verbs while still
    installing their contract entries — a lie in the contract dump. Hence create-or-extend.
    """
    from .cli_shared import as_subparsers

    sub = as_subparsers(subparsers)
    parent = (getattr(sub, "choices", None) or {}).get("code")
    if parent is None:
        parent = sub.add_parser("code", help=_GROUP_HELP)
    verbs = _verb_action(parent)
    if verbs is None:
        verbs = parent.add_subparsers(dest="code_command", required=True)
    if "map" not in getattr(verbs, "choices", {}):
        _add_verbs(verbs)
    for parser in (parent, verbs):
        try:
            share_common_flags(parser)
        except Exception:  # noqa: BLE001 — the flags are a convenience, not the contract
            continue
    if install_contract_entries is not None and _CONTRACT is not None:
        install_contract_entries(proposed_contract_entries(), _CONTRACT)
    return parent


def build_parser() -> argparse.ArgumentParser:
    """A standalone parser with just this group's verbs (tests / ``--help``).

    ``--json`` is **not** declared here: every leaf already carries it through
    ``share_common_flags``, and a flag on the parent would be clobbered by the subparser's
    own default anyway (argparse lets a subparser's default overwrite the parent's value).
    """
    parser = argparse.ArgumentParser(prog="skaldir code", description=_GROUP_HELP)
    verbs = parser.add_subparsers(dest="code_command", required=True)
    _add_verbs(verbs)
    return parser


def main(argv: list[str] | None = None) -> int:
    """``skaldir code …`` standalone: returns the exit code, never raises for a request."""
    global _STANDALONE
    invoked = list(sys.argv[1:] if argv is None else argv)
    global _STANDALONE_ARGS
    parser = build_parser()
    _STANDALONE = True
    try:
        args = parser.parse_args(invoked)
    except SystemExit as exc:
        return EXIT_OK if not exc.code else 1
    _STANDALONE_ARGS = args
    handler = getattr(args, "func", None)
    if handler is None:
        parser.print_help()
        return 1
    return int(handler(args))


if __name__ == "__main__":  # pragma: no cover — the module's own entry point
    raise SystemExit(main(sys.argv[1:]))


__all__ = [
    "EXIT_UNKNOWN",
    "build_parser",
    "main",
    "proposed_contract_entries",
    "register_cli",
]
