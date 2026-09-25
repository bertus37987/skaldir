"""§1.2 — ONE registry truth for CLI, panel, MCP, builder and Hermes.

Everything tool-shaped gets its runtime directory, tools database,
``generated_tools`` directory and project context from exactly this module.
No surface derives these from its own default again — the CLI's old
cwd-relative ``.godsv/tools.db`` default (vs. the runtime DB used by builder,
panel and MCP) was the concrete source of the "empty surface out of the box"
gap.

Resolution rules (first match wins):

1. explicit ``runtime`` argument → that runtime directory;
2. else explicit ``db`` argument → runtime = the db's directory (historic CLI
   rule: an explicit database wins, and its neighbours belong to it);
3. else ``$SKALDIR_HOME`` (fallback ``$GODSV_HOME``) → that directory;
4. else ``$HERMES_HOME/skaldir`` (fallback ``~/.hermes/skaldir``) — or the
   pre-rename ``godsv`` directory when that one already exists and the new one
   does not (:func:`runtime_dir_for`).

The project context comes from ``capability.project_id_for`` (git remote >
shared git dir > canonical path). Legacy databases — the old cwd-relative
default ``.godsv/tools.db`` — are DETECTED and REPORTED in the context; they
are never read for selection and never touched here.

Migration is a separate, explicit act (:func:`migrate_legacy`): dry-run by
default, backups of source and target before any write, name collisions are
surfaced instead of silently overwritten, newer legacy records are only taken
over with an entry in ``registry_migration.jsonl`` — and the source database
is never modified or deleted.

Nothing is published: "global" means user-wide *in this installation*.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from .capability import load_tool_entries, project_id_for
from .env import getenv as env_getenv
from .env import host_getenv as env_host_getenv
from .env import isset as env_isset
from .env import source as env_source

__all__ = [
    "JOURNAL_NAME",
    "LEGACY_RUNTIME_DIRNAME",
    "RUNTIME_DIRNAME",
    "RegistryContext",
    "default_runtime_dir",
    "runtime_dir_for",
    "runtime_reason",
    "migrate_legacy",
    "registry_snapshot",
    "resolve_registry",
]

JOURNAL_NAME = "registry_migration.jsonl"

_LEGACY_REASON_CWD = "cwd-relative legacy default (.godsv/tools.db)"
_LEGACY_REASON_PROJECT = "project-local legacy default (<project>/.godsv/tools.db)"

_SPEC_COLUMNS = (
    "name",
    "title",
    "description",
    "tags",
    "kind",
    "module",
    "entrypoint",
    "steps",
    "test_command",
    "sample_input",
    "rationale",
    "provenance",
    "status",
    "version",
    "scan_summary",
    "created",
    "updated",
)

# Fields that decide whether two records for the same name carry the SAME
# content (timestamps and row ids are bookkeeping, not content).
_FINGERPRINT_FIELDS = tuple(c for c in _SPEC_COLUMNS if c not in ("created", "updated"))


# --------------------------------------------------------------------- context


@dataclass
class RegistryContext:
    """The resolved registry facts every surface must agree on."""

    runtime_dir: Path
    tools_db: Path
    generated_tools_dir: Path
    project_id: str
    selection_reason: str
    legacy_candidates: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "runtime_dir": str(self.runtime_dir),
            "tools_db": str(self.tools_db),
            "generated_tools_dir": str(self.generated_tools_dir),
            "project_id": self.project_id,
            "selection_reason": self.selection_reason,
            "legacy_candidates": self.legacy_candidates,
        }


#: The runtime directory this product creates. The pre-rename name next to it is
#: still *used* when it already exists (see :func:`runtime_dir_for`): migrating a
#: live runtime means moving ``tools.db``, ``runs.db``, ``vault.json`` and
#: ``config.yaml`` at once, and the same directory is shared with the desktop app.
RUNTIME_DIRNAME = "skaldir"
LEGACY_RUNTIME_DIRNAME = "godsv"


def runtime_dir_for(hermes_home: str | Path) -> Path:
    """``<hermes_home>/skaldir``, or the existing pre-rename ``godsv`` directory.

    The rule is one-directional on purpose: a *new* installation gets
    ``skaldir``; an installation that already has ``godsv`` and no ``skaldir``
    keeps its state where it is. To move an existing runtime, create the new
    directory (or set ``SKALDIR_HOME``/``GODSV_HOME``) — nothing is copied or
    deleted implicitly.
    """
    home = Path(hermes_home).expanduser()
    new = home / RUNTIME_DIRNAME
    legacy = home / LEGACY_RUNTIME_DIRNAME
    if new.exists() or not legacy.exists():
        return new
    return legacy


def default_runtime_dir() -> Path:
    """The installation's runtime directory — the ONE default.

    ``$SKALDIR_HOME`` (established override, see ``skaldir tools-hub``) wins,
    then the legacy ``$GODSV_HOME``, then ``$HERMES_HOME/skaldir``, then
    ``~/.hermes/skaldir`` — each falling back to the pre-rename ``godsv``
    directory while only that one exists.
    """

    override = env_getenv("HOME")
    if override:
        return Path(override).expanduser()
    hermes_home = env_host_getenv("HERMES_HOME") or (Path.home() / ".hermes")
    return runtime_dir_for(hermes_home)


def runtime_reason(runtime_dir: Path) -> str:
    """Why :func:`default_runtime_dir` picked this directory (reported, never guessed)."""
    legacy_note = (
        f" ({LEGACY_RUNTIME_DIRNAME}: pre-rename directory, still in use)"
        if runtime_dir.name == LEGACY_RUNTIME_DIRNAME
        else ""
    )
    if env_isset("HOME"):
        # Name the variable that actually supplied the value: an operator who set
        # the legacy `GODSV_HOME` must not be told `SKALDIR_HOME` was read.
        name = env_source("HOME") or "SKALDIR_HOME"
        return f"runtime default (${name}){legacy_note}"
    if env_host_getenv("HERMES_HOME"):
        return f"runtime default ($HERMES_HOME/{RUNTIME_DIRNAME}){legacy_note}"
    return f"runtime default (~/.hermes/{RUNTIME_DIRNAME}){legacy_note}"


def resolve_registry(
    runtime: str | Path | None = None,
    project: str | Path | None = None,
    *,
    db: str | Path | None = None,
) -> RegistryContext:
    """Resolve runtime dir, tools DB and project context in one place.

    Read-only: creates nothing, writes nothing, deletes nothing. ``project`` is
    a path used for the stable project id (default: the current directory).
    """

    explicit_db = Path(str(db)).expanduser() if db else None
    explicit_runtime = Path(str(runtime)).expanduser() if runtime else None

    if explicit_runtime is not None:
        runtime_dir = explicit_runtime
        reason = "explicit --runtime"
    elif explicit_db is not None:
        # Historic CLI rule, kept as-is: an explicit --db wins and its
        # directory is the runtime its generated_tools live next to.
        runtime_dir = explicit_db.parent
        reason = "runtime derived from explicit --db directory"
    else:
        runtime_dir = default_runtime_dir()
        reason = runtime_reason(runtime_dir)

    if explicit_db is not None:
        tools_db = explicit_db
        reason = "explicit --db" + (" (runtime given)" if explicit_runtime else "")
    else:
        tools_db = runtime_dir / "tools.db"

    project_path = Path(str(project)).expanduser() if project else Path.cwd()
    return RegistryContext(
        runtime_dir=runtime_dir,
        tools_db=tools_db,
        generated_tools_dir=runtime_dir / "generated_tools",
        project_id=project_id_for(project_path),
        selection_reason=reason,
        legacy_candidates=_legacy_candidates(tools_db, project if project else None),
    )


def registry_snapshot(context: RegistryContext) -> dict:
    """Read-only status view: paths, counts, generated tools, legacy finds."""

    payload = context.to_dict()
    specs, error = _read_specs(context.tools_db)
    payload.update(
        {
            "tools_db_exists": context.tools_db.is_file(),
            "tools_db_readable": error is None,
            "tools_db_error": error or "",
            "tool_specs": len(specs),
            "tool_names": [spec["name"] for spec in specs],
            "generated_tools": [
                {
                    "name": entry["name"],
                    "scope": entry["scope"],
                    "version": entry["version_raw"],
                    "project_id": entry["project_id"],
                    "status": entry["status"],
                }
                for entry in load_tool_entries(context.runtime_dir)
            ],
        }
    )
    return payload


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return str(left) == str(right)


def _legacy_candidates(tools_db: Path, project: str | Path | None) -> list[dict]:
    """Every known legacy location, reported (never selected, never touched)."""

    probes: list[tuple[Path, str]] = [(Path.cwd() / ".godsv" / "tools.db", _LEGACY_REASON_CWD)]
    if project:
        project_path = Path(str(project)).expanduser()
        if project_path.is_dir() and not _same_path(project_path, Path.cwd()):
            probes.append(
                (project_path / ".godsv" / "tools.db", _LEGACY_REASON_PROJECT)
            )

    seen: set[str] = set()
    out: list[dict] = []
    for path, reason in probes:
        try:
            key = str(path.resolve())
        except OSError:
            key = str(path)
        if key in seen or _same_path(path, tools_db):
            continue
        seen.add(key)
        candidate: dict = {
            "path": str(path),
            "reason": reason,
            "exists": False,
            "readable": False,
            "error": "",
            "tool_count": 0,
            "names": [],
        }
        if path.is_file():
            candidate["exists"] = True
            specs, error = _read_specs(path)
            candidate["readable"] = error is None
            candidate["error"] = error or ""
            candidate["tool_count"] = len(specs)
            candidate["names"] = [spec["name"] for spec in specs]
        out.append(candidate)
    return out


# --------------------------------------------------------------------- reading


def _read_specs(path: Path) -> tuple[list[dict], str | None]:
    """Read tool specs read-only. Returns ``(specs, error)``.

    Opens with ``mode=ro``: the source database is never created, migrated or
    modified by a read.
    """

    target = Path(path)
    if not target.is_file():
        return [], None
    try:
        connection = sqlite3.connect(f"file:{target}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error as exc:  # pragma: no cover - defensive
        return [], str(exc)
    try:
        rows = connection.execute(
            f"SELECT {', '.join(_SPEC_COLUMNS)} FROM tool_specs"  # nosec B608
        ).fetchall()
    except sqlite3.Error as exc:
        return [], str(exc)
    finally:
        connection.close()
    specs: list[dict] = []
    for row in rows:
        spec = dict(zip(_SPEC_COLUMNS, row, strict=True))
        for fieldname in ("tags", "steps", "test_command", "sample_input"):
            try:
                spec[fieldname] = json.loads(spec.get(fieldname) or "null")
            except (TypeError, ValueError):
                pass
        spec["updated"] = float(spec.get("updated") or 0.0)
        spec["created"] = float(spec.get("created") or 0.0)
        specs.append(spec)
    specs.sort(key=lambda item: str(item.get("name") or ""))
    return specs, None


def _fingerprint(spec: dict) -> str:
    return json.dumps(
        {name: spec.get(name) for name in _FINGERPRINT_FIELDS},
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )


# ------------------------------------------------------------------- migration


def migrate_legacy(context: RegistryContext, *, apply: bool = False) -> dict:
    """Merge legacy tool databases into the resolved runtime database.

    - **dry run** (default): per-name report (status/version/updated), zero
      writes — the target database is not even created;
    - **apply**: backup of the SOURCE (``<db>.pre-migration-<utc-ts>.bak``) and
      of the target database **before the first actual write** — a run without
      any importable record writes nothing and creates no backups;
    - **name collision**: never a silent overwrite — identical records are
      skipped, a different record is only taken over when its ``updated`` is
      newer AND the takeover is documented in ``registry_migration.jsonl``;
      otherwise the target wins and the conflict is reported;
    - the source database survives untouched (never modified, never deleted).
    """

    sources = [item for item in context.legacy_candidates if item.get("exists")]
    journal_path = context.runtime_dir / JOURNAL_NAME
    report: dict = {
        "apply": bool(apply),
        "target_db": str(context.tools_db),
        "target_backup": None,
        "journal": str(journal_path),
        "sources": [],
        "migrated": 0,
        "identical": 0,
        "conflicts": [],
        "errors": [],
    }
    if not sources:
        report["note"] = "no legacy database found — nothing to migrate"
        return report

    stamp = _utc_stamp()
    target_specs, target_error = _read_specs(context.tools_db)
    if target_error is not None:
        report["errors"].append(f"target db unreadable: {target_error}")
        return report

    store_holder: dict = {}

    def store_for_write():
        """Open the target lazily — and back it up right before the FIRST write."""

        if store_holder.get("store") is None:
            if context.tools_db.is_file() and report["target_backup"] is None:
                report["target_backup"] = str(_backup(context.tools_db, stamp))
            context.tools_db.parent.mkdir(parents=True, exist_ok=True)
            from .tools import ToolStore

            store_holder["store"] = ToolStore(context.tools_db)
        return store_holder["store"]

    try:
        for source in sources:
            report["sources"].append(
                _migrate_source(
                    source, target_specs, store_for_write, context, stamp, report
                )
            )
    finally:
        if store_holder.get("store") is not None:
            store_holder["store"].close()
    return report


def _migrate_source(
    source: dict,
    target_specs: list[dict],
    store_for_write,
    context: RegistryContext,
    stamp: str,
    report: dict,
) -> dict:
    from .tools import ToolSpec

    source_path = Path(str(source["path"]))
    entry: dict = {
        "path": str(source_path),
        "reason": source.get("reason") or "",
        "backup": None,
        "tools": [],
        "imported": [],
        "identical": [],
        "conflicts": [],
    }
    specs, error = _read_specs(source_path)
    if error is not None:
        entry["error"] = error
        report["errors"].append(f"{source_path}: {error}")
        return entry

    target_index = {str(spec["name"]): spec for spec in target_specs}
    for spec in specs:
        name = str(spec["name"])
        existing = target_index.get(name)
        row = {
            "name": name,
            "status": str(spec.get("status") or ""),
            "version": str(spec.get("version") or ""),
            "updated": spec.get("updated") or 0.0,
        }
        if existing is None:
            row["action"] = "new"
            entry["imported"].append(name)
            report["migrated"] += 1
            if report["apply"]:
                if entry["backup"] is None:
                    entry["backup"] = str(_backup(source_path, stamp))
                _write_spec(store_for_write(), spec, ToolSpec)
                target_index[name] = spec
                _journal(
                    context,
                    {
                        "action": "imported",
                        "tool": name,
                        "source": str(source_path),
                        "source_updated": row["updated"],
                    },
                )
        elif _fingerprint(existing) == _fingerprint(spec):
            row["action"] = "identical"
            entry["identical"].append(name)
            report["identical"] += 1
        elif (spec.get("updated") or 0.0) > (existing.get("updated") or 0.0):
            row["action"] = "imported-newer"
            conflict = {
                "name": name,
                "source": str(source_path),
                "source_updated": spec.get("updated") or 0.0,
                "target_updated": existing.get("updated") or 0.0,
                "resolution": (
                    "legacy-newer-imported+journaled"
                    if report["apply"]
                    else "would-import-newer"
                ),
            }
            entry["conflicts"].append(conflict)
            report["conflicts"].append(dict(conflict))
            entry["imported"].append(name)
            report["migrated"] += 1
            if report["apply"]:
                if entry["backup"] is None:
                    entry["backup"] = str(_backup(source_path, stamp))
                _write_spec(store_for_write(), spec, ToolSpec)
                target_index[name] = spec
                _journal(
                    context,
                    {
                        "action": "imported-newer",
                        "tool": name,
                        "source": str(source_path),
                        "source_updated": spec.get("updated") or 0.0,
                        "target_updated": existing.get("updated") or 0.0,
                        "resolution": "legacy-newer-imported+journaled",
                    },
                )
        else:
            # Name collision, content differs, the target is not older: the
            # registry NEVER overwrites silently — the target keeps its record.
            conflict = {
                "name": name,
                "source": str(source_path),
                "source_updated": spec.get("updated") or 0.0,
                "target_updated": existing.get("updated") or 0.0,
                "resolution": "target-kept",
            }
            entry["conflicts"].append(conflict)
            report["conflicts"].append(dict(conflict))
            if report["apply"]:
                _journal(
                    context,
                    {
                        "action": "target-kept",
                        "tool": name,
                        "source": str(source_path),
                        "source_updated": spec.get("updated") or 0.0,
                        "target_updated": existing.get("updated") or 0.0,
                        "resolution": "target-kept",
                    },
                )
        entry["tools"].append(row)
    return entry


def _write_spec(store, spec: dict, spec_class) -> None:
    """Import one legacy record, keeping its own ``updated`` bookkeeping."""

    payload: dict = {}
    for name in _SPEC_COLUMNS:
        if name in ("created", "updated"):
            continue
        value = spec.get(name)
        if name in ("tags", "steps", "test_command"):
            payload[name] = value if isinstance(value, list) else []
        elif name == "sample_input":
            payload[name] = value if isinstance(value, dict) else {}
        else:
            payload[name] = "" if value is None else str(value)
    store.upsert(spec_class(**payload), ts=spec.get("updated") or None)


def _journal(context: RegistryContext, entry: dict) -> None:
    path = context.runtime_dir / JOURNAL_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    line = {"ts": time.time(), "target": str(context.tools_db), **entry}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(line, ensure_ascii=False, default=str) + "\n")


def _utc_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _backup(path: Path, stamp: str) -> Path:
    """Copy ``path`` to ``<path>.pre-migration-<stamp>.bak`` (never overwrite)."""

    target = Path(f"{path}.pre-migration-{stamp}.bak")
    counter = 2
    while target.exists():
        target = Path(f"{path}.pre-migration-{stamp}-{counter}.bak")
        counter += 1
    shutil.copy2(path, target)
    return target
