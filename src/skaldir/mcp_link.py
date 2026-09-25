"""MCP-Link (§1.4): bind the Skaldir MCP surface into the host's own config.

The running Hermes host reaches godsv capabilities through its MCP client. This
module makes that connection exist **automatically** — by driving the host's own
sanctioned mechanism (``hermes mcp add`` / ``hermes mcp remove``, fixed argv, no
shell), never by editing ``~/.hermes/config.yaml`` by hand.

Guarantees (each covered in ``tests/test_mcp_link.py``):
* **idempotent** — an already matching entry performs no write at all;
* **merging** — every other ``mcp_servers`` entry is verified unchanged after a
  write (byte-compared as objects); this module never rewrites the map itself;
* **backup** — the config file is copied beside itself before the first write;
* **no tool filter** — the link must not create ``tools.include``: a frozen name
  list would hide every capability built *after* the link;
* **honest** — a config change is NOT a live proof. The running host picks the
  server up on its next start; the *insertion* proof is the native surface
  (see ``docs/TOOLS-V2-HOST-WIRING.md`` §8).

CLI: ``python -m skaldir.mcp_link [--check] [--project PATH] [--hermes BIN]``.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

SERVER_NAME = "godsv-tools"
DEFAULT_ARGS = ("mcp", "serve")
# The discovery probe answers two prompts: "already exists — overwrite?" (only
# on replace) and "enable all N tools? [Y/n/select]" — yes keeps the surface
# unfiltered, which is exactly what a live capability surface needs.
_PROBE_STDIN = "y\ny\ny\n"


def skaldir_binary() -> str:
    """Absolute path of the ``skaldir`` CLI (venv sibling first, then PATH)."""
    found = shutil.which("skaldir")
    if found:
        return found
    candidate = Path(sys.executable).parent / "skaldir"
    if candidate.is_file():
        return str(candidate)
    return "skaldir"


def hermes_binary(explicit: str | None = None) -> str | None:
    """The host CLI to drive; ``None`` when Hermes is not installed here."""
    if explicit:
        return explicit
    try:  # reuse the resolution the model-role setup already proves
        from .modelroles import _hermes_binary as _resolve

        found = _resolve()
        if found:
            return str(found)
    except Exception:
        pass
    found = shutil.which("hermes")
    if found:
        return found
    local = Path.home() / ".local" / "bin" / "hermes"
    return str(local) if local.exists() else None


def config_path(hermes_home: str | Path | None = None) -> Path:
    """The host config this link writes into (``$HERMES_HOME/config.yaml``)."""
    home = Path(hermes_home) if hermes_home else Path.home() / ".hermes"
    return Path(home) / "config.yaml"


def server_entry(
    *,
    command: str = "",
    args: tuple[str, ...] | list[str] = DEFAULT_ARGS,
    project_id: str = "",
    runtime: str | Path | None = None,
    include_legacy: bool = False,
) -> dict:
    """The desired stdio entry for the godsv MCP surface.

    Scope is expressed with the server's own flags (``--project`` / ``--all``),
    never with an ``mcp_servers`` tool filter: new capabilities must reach the
    host without a config change.
    """
    argv = [str(item) for item in args]
    if runtime:
        argv += ["--runtime", str(runtime)]
    if project_id:
        argv += ["--project", str(project_id)]
    if include_legacy:
        argv += ["--all"]
    return {"command": command or skaldir_binary(), "args": argv, "enabled": True}


def read_servers(path: str | Path | None = None) -> dict:
    """The configured ``mcp_servers`` map — read-only, tolerant (never raises)."""
    target = Path(path) if path else config_path()
    if not target.is_file():
        return {}
    try:
        import yaml

        data = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    servers = data.get("mcp_servers") if isinstance(data, dict) else None
    return servers if isinstance(servers, dict) else {}


def _entry_matches(current: Any, desired: dict) -> bool:
    if not isinstance(current, dict):
        return False
    if str(current.get("command") or "") != str(desired.get("command") or ""):
        return False
    if current.get("enabled") is False:
        # the host's own probe found nothing to expose (e.g. wrong scope) and
        # saved the entry disabled — that is NOT a working link
        return False
    current_args = [str(item) for item in (current.get("args") or [])]
    desired_args = [str(item) for item in (desired.get("args") or [])]
    return current_args == desired_args


def link_state(
    *,
    config_file: str | Path | None = None,
    entry: dict | None = None,
) -> dict:
    """Everything a caller needs to decide: linked? matching? what else is there?"""
    path = Path(config_file) if config_file else config_path()
    desired = entry or server_entry()
    servers = read_servers(path)
    current = servers.get(SERVER_NAME)
    tools_filter = current.get("tools") if isinstance(current, dict) else None
    matches = _entry_matches(current, desired)
    enabled = True if not isinstance(current, dict) else current.get("enabled") is not False
    if current is None:
        reason = "not linked"
    elif matches:
        reason = "linked and matching"
    elif not enabled:
        reason = "linked but DISABLED — the host's probe found no tools for this scope"
    else:
        reason = "linked but the command/args differ from the desired surface"
    return {
        "config": str(path),
        "server": SERVER_NAME,
        "desired": desired,
        "entry": current,
        "linked": current is not None,
        "enabled": enabled,
        "matches": matches,
        "reason": reason,
        # a tools.include filter would freeze the exported name list → report it
        "tool_filter": bool(isinstance(tools_filter, dict) and tools_filter.get("include")),
        "other_servers": sorted(name for name in servers if name != SERVER_NAME),
        "timestamp": time.time(),
    }


def _run(
    argv: list[str], stdin: str, timeout: float, env: dict | None
) -> subprocess.CompletedProcess:
    return subprocess.run(  # nosec B603 — fixed argv, no shell
        argv, input=stdin, capture_output=True, text=True, timeout=timeout, env=env
    )


def ensure_linked(
    *,
    hermes: str | None = None,
    config_file: str | Path | None = None,
    entry: dict | None = None,
    replace: bool = False,
    runner: Callable[..., subprocess.CompletedProcess] | None = None,
    timeout: float = 60.0,
    env: dict | None = None,
    backup: bool = True,
) -> dict:
    """Idempotently link the godsv MCP server through the host's own CLI.

    ``replace=False`` (default) never overwrites an existing, different entry —
    it reports ``mismatch`` so a human decides. Every write goes through
    ``hermes mcp add``; the module itself never edits the config file. Returns a
    result dict with ``action`` in ``none | added | replaced | mismatch |
    failed`` and always a fresh :func:`link_state` under ``state``.
    """
    path = Path(config_file) if config_file else config_path()
    desired = entry or server_entry()
    run = runner or _run
    result: dict[str, Any] = {"config": str(path), "server": SERVER_NAME, "action": "none"}
    state = link_state(config_file=path, entry=desired)
    if state["matches"]:
        result["state"] = state
        result["reason"] = "already linked and matching — nothing written"
        return result
    binary = hermes_binary(hermes)
    if not binary:
        result.update(
            action="failed",
            reason="no hermes CLI found — the link cannot be made automatically",
        )
        result["state"] = state
        return result

    others_before = {
        name: value for name, value in read_servers(path).items() if name != SERVER_NAME
    }
    backup_path = ""
    if backup and path.is_file():
        backup_path = str(path.parent / f"{path.name}.gv-link-backup")
        try:
            shutil.copy2(path, backup_path)
        except OSError:
            backup_path = ""
    result["backup"] = backup_path

    if state["linked"]:
        if not replace:
            result.update(
                action="mismatch",
                reason=(
                    "an existing entry differs from the desired surface — pass replace=True "
                    "to re-link it (other servers stay untouched either way)"
                ),
            )
            result["state"] = state
            return result
        removal = run([binary, "mcp", "remove", SERVER_NAME], "y\n", timeout, env)
        result["remove_returncode"] = int(removal.returncode)

    argv = [
        binary, "mcp", "add", SERVER_NAME, "--connect-timeout", "20",
        "--command", desired["command"],
    ]
    argv += ["--args", *[str(item) for item in desired.get("args") or []]]
    try:
        proc = run(argv, _PROBE_STDIN, timeout, env)
    except (OSError, subprocess.SubprocessError) as exc:
        result.update(action="failed", reason=f"{type(exc).__name__}: {exc}")
        result["state"] = link_state(config_file=path, entry=desired)
        return result
    result["returncode"] = int(proc.returncode)
    result["stdout_tail"] = " ".join((proc.stdout or "").split())[-400:]
    result["stderr_tail"] = " ".join((proc.stderr or "").split())[-200:]

    after = link_state(config_file=path, entry=desired)
    others_after = {
        name: value for name, value in read_servers(path).items() if name != SERVER_NAME
    }
    result["state"] = after
    result["others_unchanged"] = others_after == others_before
    if after["matches"]:
        result["action"] = "replaced" if state["linked"] else "added"
    else:
        result["action"] = "failed"
        result["reason"] = f"the host CLI left no usable entry behind ({after['reason']})"
    return result


def unlink(
    *,
    hermes: str | None = None,
    config_file: str | Path | None = None,
    runner: Callable[..., subprocess.CompletedProcess] | None = None,
    timeout: float = 60.0,
    env: dict | None = None,
    backup: bool = True,
) -> dict:
    """Remove the godsv MCP server through the host's own CLI (``hermes mcp remove``).

    A stale entry is worse than none: the live install carried a ``godsv-tools`` entry whose
    ``--runtime``/``--project`` pointed into a pytest temp dir and whose command file no longer
    existed, so the config *looked* linked while every start reported zero connected servers
    (audit finding F-W09). Removal goes through the host CLI like :func:`ensure_linked` does;
    other servers stay untouched and the file is backed up first. YAML is never edited by hand.
    """
    path = Path(config_file) if config_file else config_path()
    run = runner or _run
    result: dict[str, Any] = {"config": str(path), "server": SERVER_NAME, "action": "none"}
    before = link_state(config_file=path)
    if not before["linked"]:
        result["state"] = before
        result["reason"] = "no entry to remove — nothing written"
        return result
    binary = hermes_binary(hermes)
    if not binary:
        result.update(
            action="failed",
            reason="no hermes CLI found — the entry cannot be removed automatically",
        )
        result["state"] = before
        return result
    if backup and path.is_file():
        backup_path = str(path.parent / f"{path.name}.gv-unlink-backup")
        try:
            shutil.copy2(path, backup_path)
        except OSError:
            backup_path = ""
        result["backup"] = backup_path
    try:
        proc = run([binary, "mcp", "remove", SERVER_NAME], "y\n", timeout, env)
    except (OSError, subprocess.SubprocessError) as exc:
        result.update(action="failed", reason=f"{type(exc).__name__}: {exc}")
        result["state"] = link_state(config_file=path)
        return result
    result["returncode"] = int(proc.returncode)
    after = link_state(config_file=path)
    result["state"] = after
    result["others_unchanged"] = sorted(after["other_servers"]) == sorted(before["other_servers"])
    if after["linked"]:
        result.update(action="failed", reason=f"the entry survived the removal ({after['reason']})")
    else:
        result["action"] = "removed"
    return result


def main(argv: list[str] | None = None) -> int:
    """``--check`` (no write, exit 0 only when linked+matching) or apply."""
    parser = argparse.ArgumentParser(description="Link the godsv MCP surface into the host config")
    parser.add_argument("--check", action="store_true", help="report state only, never write")
    parser.add_argument("--hermes", default="", help="path to the hermes CLI")
    parser.add_argument(
        "--config", default="", help="config file to inspect (default: $HERMES_HOME)"
    )
    parser.add_argument("--project", default="", help="project path for the exposed scope")
    parser.add_argument("--runtime", default="", help="godsv runtime dir the server should read")
    parser.add_argument("--all", action="store_true", help="expose legacy (scope-less) tools too")
    parser.add_argument("--replace", action="store_true", help="re-link a differing entry")
    parser.add_argument(
        "--unlink", action="store_true", help="remove the godsv entry (backed up, host CLI)"
    )
    args = parser.parse_args(argv)

    entry = server_entry(project_id=args.project, runtime=args.runtime, include_legacy=args.all)
    config_file = args.config or None
    if args.unlink:
        report = unlink(hermes=args.hermes or None, config_file=config_file)
        print(f"action: {report['action']} — {report.get('reason', '')}".rstrip(" —"))
        if report.get("backup"):
            print(f"backup: {report['backup']}")
        state = report.get("state") or link_state(config_file=config_file)
        print(f"server: {state['server']} — {state['reason']}")
        print(f"others untouched: {report.get('others_unchanged')}")
        return 0 if report["action"] in {"removed", "none"} else 1
    if args.check:
        state = link_state(config_file=config_file, entry=entry)
        print(f"config: {state['config']}")
        print(f"server: {state['server']} — {state['reason']}")
        if state["other_servers"]:
            print(f"other servers (untouched): {', '.join(state['other_servers'])}")
        if state["tool_filter"]:
            print(
                "warning: the entry carries a tools.include filter — new capabilities stay hidden"
            )
        return 0 if state["matches"] else 1

    report = ensure_linked(
        hermes=args.hermes or None, config_file=config_file, entry=entry, replace=args.replace
    )
    print(f"action: {report['action']} — {report.get('reason', '')}".rstrip(" —"))
    if report.get("backup"):
        print(f"backup: {report['backup']}")
    state = report.get("state") or {}
    if state:
        print(
            f"state: {state.get('reason', '')} "
            f"(others untouched: {report.get('others_unchanged')})"
        )
    return 0 if report["action"] in ("none", "added", "replaced") else 1


if __name__ == "__main__":
    raise SystemExit(main())
