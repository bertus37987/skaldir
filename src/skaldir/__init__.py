"""Skaldir — self-learning agent harness core.

V0 "Voice-Skelett": Pact (task contract), Ledger (continuity), event store,
deterministic step monitor, voice (hint budget/dedup), injection formatting.

Design docs live in docs/ — CORE-PLAN.md is the feature master.

The package was called ``godsv`` before the rename; ``import godsv`` still works
(same module objects, one DeprecationWarning) — see ``src/godsv/__init__.py``.
"""

from __future__ import annotations

#: The distribution this package ships in. A wheel normalises the name for its
#: `.dist-info` directory (`skaldir_agent-…`), and an installation made before the
#: rename still carries only the old metadata — both are read, newest name first.
DIST_NAME = "skaldir-agent"
LEGACY_DIST_NAME = "godsv"

_VERSION_FALLBACK = "0.0.0+source"


def _resolve_version() -> str:
    """The installed distribution version, newest name first (no drift)."""
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as _pkg_version

    for name in (DIST_NAME, DIST_NAME.replace("-", "_"), LEGACY_DIST_NAME):
        try:
            return _pkg_version(name)
        except PackageNotFoundError:  # source checkout without install
            continue
    return _VERSION_FALLBACK


try:  # single source of truth: the installed package metadata (no drift)
    __version__ = _resolve_version()
except ImportError:  # pragma: no cover
    __version__ = "0.0.0"
