"""Cold-start fast path for the ``skaldir`` CLI (performance block).

The measured problem (raw numbers in ``MVP/19-performance.md``)::

    skaldir --version        230 ms wall   / 49 MB RSS
      ├─ CPython interpreter    14 ms
      ├─ import skaldir.cli       85 ms   (85 of 230 = 37 % of the whole command)
      │    ├─ importlib.metadata 25 ms  ← skaldir/__init__.py: _pkg_version("godsv")
      │    ├─ skaldir.demo→inject→voice 15 ms
      │    ├─ skaldir.computer_use 14 ms
      │    ├─ skaldir.treasury / yaml / evalharness / monitor / system_one* 31 ms
      └─ build_parser()        102 ms   (258 sub-parsers, 73 leaf commands)

None of that is needed to print one version string. Every command pays it,
because ``cli.py`` imports its whole handler graph at module level and then
builds the complete ``argparse`` tree before ``--version`` can be answered.

Why a *front door* and not a module-level ``__getattr__``
--------------------------------------------------------
PEP 562 module ``__getattr__`` is **not** consulted for global-name lookups
inside the module's own functions (measured: ``tests/test_perf_cli_lazy.py``
proves it on this interpreter — a ``LOAD_GLOBAL`` of a name that only exists
behind ``__getattr__`` raises ``NameError``, it does not call ``__getattr__``).
``cli.py`` binds names like ``SpendLedger`` at module level and uses them inside
handler functions, so those bindings must resolve eagerly: the only safe way to
stop paying for ``cli.py`` is not to import it at all for commands that do not
need it. That is what this module does.

Usage — one line in ``pyproject.toml``::

    [project.scripts]
    skaldir = "skaldir.perf_lazy:main"     # was: skaldir.cli:main

``main()`` answers the zero-import argv shapes itself and delegates everything
else to ``skaldir.cli.main`` unchanged. Fallback if the wiring is not applied:
``fast_path()`` can also be called at the top of ``skaldir.cli.main`` (that saves
the 102 ms ``build_parser()`` but not the 85 ms import — cli.py is imported by
then).
"""

from __future__ import annotations

import sys
from collections.abc import Iterable, Sequence
from pathlib import Path

__all__ = [
    "FAST_ARGV",
    "HEAVY_IMPORTS",
    "cached_version",
    "fast_path",
    "main",
]

#: argv shapes answered without importing ``skaldir.cli``. Only shapes whose
#: output provably does not depend on the command tree; anything else — a
#: command, ``--help``, ``--json``, an unknown flag — falls through to the real
#: CLI so behaviour and exit codes stay byte-identical.
FAST_ARGV: frozenset[tuple[str, ...]] = frozenset({("--version",), ("-V",)})

#: The eager import graph behind ``skaldir.cli`` that the front door defers.
#: Per-module cost measured with ``tools/gv_cli_perf.py imports``.
HEAVY_IMPORTS: tuple[str, ...] = (
    "skaldir.cli",
    "skaldir.cli_contract",
    "skaldir.computer_use",
    "skaldir.demo",
    "skaldir.evalharness",
    "skaldir.inject",
    "skaldir.monitor",
    "skaldir.system_one",
    "skaldir.treasury",
    "skaldir.voice",
    "yaml",
)

_DIST = "skaldir-agent"
#: The pre-rename distribution name. An installation made before the rename has
#: only `godsv-<v>.dist-info`, so `--version` must still find it — otherwise the
#: cold-start path would print `0.0.0+source` for a correctly installed tool.
_LEGACY_DIST = "godsv"
_VERSION_FALLBACK = "0.0.0+source"


def dist_candidates(dist: str) -> tuple[str, ...]:
    """Every distribution spelling ``dist`` may be installed under.

    Wheel/tool metadata normalises ``-`` to ``_`` in the ``.dist-info`` directory
    name (``skaldir-agent`` installs as ``skaldir_agent-1.5.0.dist-info``), and the
    pre-rename name is accepted as a fallback for both.
    """
    out = [dist]
    normalized = dist.replace("-", "_")
    if normalized != dist:
        out.append(normalized)
    if dist in (_DIST, _DIST.replace("-", "_")):
        out.append(_LEGACY_DIST)
    return tuple(dict.fromkeys(out))


def _version_from_dist_info(dist: str, search: Iterable[str] | None = None) -> str | None:
    """Read ``Version:`` from ``<dist>-<ver>.dist-info/METADATA`` on ``sys.path``.

    Same file ``importlib.metadata.version()`` reads, in the same ``sys.path``
    order, but without importing ``importlib.metadata`` (which drags in
    ``email``/``email.message``/``email.utils`` — 25 ms of the cold start).
    """

    for name in dist_candidates(dist):
        found = _version_from_dist_info_exact(name, search)
        if found is not None:
            return found
    return None


def _version_from_dist_info_exact(dist: str, search: Iterable[str] | None = None) -> str | None:
    prefix = f"{dist}-"
    for entry in search if search is not None else sys.path:
        if not entry:
            continue
        root = Path(entry)
        try:
            candidates = list(root.glob(f"{prefix}*.dist-info"))
        except OSError:  # pragma: no cover — unreadable sys.path entry
            continue
        for info in candidates:
            metadata = info / "METADATA"
            try:
                text = metadata.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for line in text.splitlines():
                if line.startswith("Version:"):
                    value = line.split(":", 1)[1].strip()
                    if value:
                        return value
                if not line.strip():
                    break  # the Version: header is in the first block
    return None


def cached_version(dist: str = _DIST) -> str:
    """The distribution version, without paying ``importlib.metadata``'s import.

    Must return exactly what ``skaldir.__version__`` returns (``skaldir/__init__.py``):
    ``importlib.metadata.version(dist)``, or ``"0.0.0+source"`` for a source
    checkout without install metadata. ``tests/test_perf_cli_budget.py`` pins the
    equality, so a packaging change cannot silently drift the ``--version`` output.
    """

    found = _version_from_dist_info(dist)
    if found is not None:
        return found
    try:  # exact parity with skaldir/__init__.py for exotic installs (eggs, zips)
        from importlib.metadata import PackageNotFoundError
        from importlib.metadata import version as _pkg_version

        for name in dist_candidates(dist):
            try:
                return _pkg_version(name)
            except PackageNotFoundError:
                continue
        return _VERSION_FALLBACK
    except ImportError:  # pragma: no cover
        return _VERSION_FALLBACK


def fast_path(argv: Sequence[str]) -> int | None:
    """Answer a zero-import command, or return ``None`` to mean "fall through".

    Kept a pure function of ``argv`` so the CLI owner can call it from any
    entry point without duplicating the policy.
    """

    if tuple(argv) not in FAST_ARGV:
        return None
    print(f"skaldir {cached_version()}")
    return 0


def notice_deprecated_alias(invoked_as: str = "") -> bool:
    """One notice line when the deprecated ``godsvoice`` script was used (TTY only).

    Delegated to :mod:`skaldir.deprecation` and imported *inside* the call so the
    cold path of ``skaldir --version`` pays nothing for it.
    """
    from .deprecation import notice_deprecated_alias as _notice

    return _notice(invoked_as)


def main(argv: list[str] | None = None) -> int:
    """Entry point: the fast path for trivial argv, ``skaldir.cli.main`` for the rest."""

    args = list(sys.argv[1:] if argv is None else argv)
    notice_deprecated_alias(sys.argv[0])
    code = fast_path(args)
    if code is not None:
        return code
    from .cli import main as cli_main  # imported only when a real command runs

    return int(cli_main(args))
