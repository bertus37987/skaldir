"""Atomic, reversible writes to the runtime ``settings.yaml``.

The runtime state directory carries one behavioural settings file
(``<runtime>/settings.yaml``): feature flags such as ``strictness`` and the
``system_one:`` section. **Reads** degrade safely — a missing or malformed file
means "all defaults" (see :func:`skaldir.system_one_flags.load_system_one_flags`) —
but a **write** had no such care: the existing
:func:`skaldir.system_one_flags.save_system_one_flags` truncates and rewrites the
file in place, so a crash (or a full disk) between the truncate and the write
leaves a half-written settings file and destroys the previous state.

This module is the write path the runtime uses when a flag has to change:

* **atomic** — the new document goes to a temporary file in the *same*
  directory, is flushed and ``fsync``-ed, and only then replaces the target with
  one :func:`os.replace` call. Either the old or the new document is on disk,
  never a mixture; a failure removes the temporary file and leaves the original
  untouched.
* **reversible** — the previous file is copied to
  ``settings.yaml.bak-<YYYYmmdd-HHMMSS>`` *before* the change (only backups
  beyond :data:`MAX_BACKUPS` are pruned), and :func:`restore` puts a backup back
  byte for byte.
* **reported** — every write returns the dotted-key diff of the touched section
  (``system_one.enabled: absent → True``), so a caller can print exactly what
  changed instead of claiming it.
* **section-scoped** — unrelated top-level sections survive, and keys inside the
  touched section that the caller did not name keep their value.

No new dependency: PyYAML is already a runtime dependency, and this module never
sees a secret (settings carry behavioural flags only; credentials live in the
environment / the Treasury).
"""

from __future__ import annotations

import os
import tempfile
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

SETTINGS_NAME = "settings.yaml"
BACKUP_MARK = ".bak-"
BACKUP_STAMP = "%Y%m%d-%H%M%S"
#: How many ``settings.yaml.bak-*`` files to keep next to the settings file.
MAX_BACKUPS = 5


class SettingsError(RuntimeError):
    """The settings file could not be read, written or restored safely."""


# --------------------------------------------------------------------------- #
# reading                                                                       #
# --------------------------------------------------------------------------- #


def read_document(path: str | Path) -> dict:
    """The whole settings document (an empty mapping when the file is absent).

    A malformed file raises :class:`SettingsError` instead of being silently
    replaced: a write path must never destroy content it could not parse.
    """
    import yaml

    settings = Path(path)
    if not settings.is_file():
        return {}
    try:
        loaded = yaml.safe_load(settings.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 — any parse failure is a hard stop
        raise SettingsError(f"cannot read {settings}: {exc}") from exc
    if loaded is None:
        return {}
    if not isinstance(loaded, Mapping):
        raise SettingsError(f"{settings} does not hold a mapping at the top level")
    return {str(key): value for key, value in loaded.items()}


def section_of(document: Mapping[str, Any] | None, section: str) -> dict:
    """One top-level section as a plain dict (``{}`` when absent or not a mapping)."""
    if not isinstance(document, Mapping):
        return {}
    value = document.get(section)
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    return {}


def diff_sections(
    before: Mapping[str, Any] | None, after: Mapping[str, Any] | None, *, section: str = ""
) -> list[dict]:
    """The dotted-key diff between two section mappings (``None`` = absent).

    Only keys whose value actually changed appear, so an idempotent write
    reports ``changed == []`` — the caller can then say "nothing changed"
    instead of "I wrote something".
    """
    old = dict(before or {})
    new = dict(after or {})
    prefix = f"{section}." if section else ""
    rows: list[dict] = []
    for key in sorted(set(old) | set(new)):
        was = old.get(key, None)
        now = new.get(key, None)
        if was == now:
            continue
        rows.append({"key": f"{prefix}{key}", "before": was, "after": now})
    return rows


# --------------------------------------------------------------------------- #
# the atomic write                                                              #
# --------------------------------------------------------------------------- #


def atomic_write_text(path: str | Path, text: str) -> int:
    """Write ``text`` to ``path`` atomically; returns the number of bytes written.

    Same-directory temporary file → ``flush`` → ``fsync`` → :func:`os.replace`
    → directory ``fsync``. The mode is the settings file's usual ``0o644``; the
    file is never world-writable and never group-writable.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    data = text.encode("utf-8")
    handle = tempfile.NamedTemporaryFile(  # noqa: SIM115 — closed/replaced explicitly
        mode="wb", dir=str(target.parent), prefix=f"{target.name}.tmp-", delete=False
    )
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    _fsync_dir(target.parent)
    return len(data)


def _fsync_dir(directory: Path) -> None:
    """Make the rename itself durable; best effort (not every fs supports it)."""
    try:
        fd = os.open(str(directory), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def dump_document(document: Mapping[str, Any]) -> str:
    """The on-disk YAML spelling (identical options to the existing flag writer)."""
    import yaml

    return yaml.safe_dump(dict(document), allow_unicode=True, sort_keys=True)


# --------------------------------------------------------------------------- #
# backups                                                                       #
# --------------------------------------------------------------------------- #


def backup_path(path: str | Path, *, stamp: str | None = None) -> Path:
    """The backup file name for one settings path (deterministic given a stamp)."""
    target = Path(path)
    mark = stamp or time.strftime(BACKUP_STAMP)
    return target.with_name(f"{target.name}{BACKUP_MARK}{mark}")


def make_backup(path: str | Path, *, stamp: str | None = None) -> Path | None:
    """Copy the current settings file aside; ``None`` when there is nothing to copy."""
    target = Path(path)
    if not target.is_file():
        return None
    destination = backup_path(target, stamp=stamp)
    if destination.exists():
        destination = destination.with_name(f"{destination.name}-{os.getpid()}")
    destination.write_bytes(target.read_bytes())
    os.chmod(destination, 0o600)
    return destination


def list_backups(path: str | Path) -> list[Path]:
    """Every backup of this settings file, newest name last."""
    target = Path(path)
    if not target.parent.is_dir():
        return []
    prefix = f"{target.name}{BACKUP_MARK}"
    return sorted(
        item for item in target.parent.iterdir() if item.is_file() and item.name.startswith(prefix)
    )


def latest_backup(path: str | Path) -> Path | None:
    backups = list_backups(path)
    return backups[-1] if backups else None


def prune_backups(path: str | Path, *, keep: int = MAX_BACKUPS) -> list[str]:
    """Delete all but the newest ``keep`` backups; returns the removed paths."""
    backups = list_backups(path)
    removed: list[str] = []
    for item in backups[: max(0, len(backups) - max(0, int(keep)))]:
        try:
            item.unlink()
        except OSError:
            continue
        removed.append(str(item))
    return removed


# --------------------------------------------------------------------------- #
# the two edits the runtime needs                                               #
# --------------------------------------------------------------------------- #


def apply_section(
    path: str | Path,
    section: str,
    values: Mapping[str, Any],
    *,
    dry_run: bool = False,
    keep_backups: int = MAX_BACKUPS,
) -> dict:
    """Merge ``values`` into one section of the settings file, atomically.

    Returns the report a ``--json`` caller prints: the dotted-key diff, the
    backup path (``""`` when nothing changed or the file was absent) and the
    sizes before/after. ``dry_run`` computes everything but writes nothing —
    including no backup, so a dry run leaves the directory byte-identical.
    """
    target = Path(path)
    document = read_document(target)
    before = section_of(document, section)
    after = {**before, **{str(key): value for key, value in dict(values).items()}}
    changed = diff_sections(before, after, section=section)
    existed = target.is_file()
    report: dict[str, Any] = {
        "path": str(target),
        "section": section,
        "existed_before": existed,
        "dry_run": bool(dry_run),
        "wrote": False,
        "backup": "",
        "pruned_backups": [],
        "changed": changed,
        "before": before,
        "after": after,
        "bytes_before": target.stat().st_size if existed else 0,
        "bytes_after": 0,
    }
    if not changed:
        report["bytes_after"] = report["bytes_before"]
        return report
    text = dump_document({**document, section: after})
    report["bytes_after"] = len(text.encode("utf-8"))
    if dry_run:
        return report
    backup = make_backup(target)
    report["backup"] = str(backup) if backup else ""
    atomic_write_text(target, text)
    report["pruned_backups"] = prune_backups(target, keep=keep_backups)
    report["wrote"] = True
    return report


def restore(path: str | Path, backup: str | Path) -> dict:
    """Put a backup back, byte for byte, through the same atomic write."""
    target = Path(path)
    source = Path(backup)
    if not source.is_file():
        raise SettingsError(f"no backup to restore: {source}")
    if not source.name.startswith(f"{target.name}{BACKUP_MARK}"):
        raise SettingsError(
            f"refusing to restore {source.name}: not a backup of {target.name}"
        )
    payload = source.read_bytes()
    report = {
        "path": str(target),
        "restored_from": str(source),
        "bytes": len(payload),
        "wrote": True,
    }
    atomic_write_text(target, payload.decode("utf-8"))
    return report


__all__ = [
    "BACKUP_MARK",
    "BACKUP_STAMP",
    "MAX_BACKUPS",
    "SETTINGS_NAME",
    "SettingsError",
    "apply_section",
    "atomic_write_text",
    "backup_path",
    "diff_sections",
    "dump_document",
    "latest_backup",
    "list_backups",
    "make_backup",
    "prune_backups",
    "read_document",
    "restore",
    "section_of",
]


# Reading helper


def read_section(path: str | Path, section: str) -> dict:
    """``section_of(read_document(path))`` — the one-line read most callers want.

    An unreadable or absent file is ``{}``: a *reader* must never blow up on a
    settings file it did not write. Writes (:func:`apply_section`) are the
    strict side of this module.
    """
    try:
        return section_of(read_document(path), section)
    except SettingsError:
        return {}
