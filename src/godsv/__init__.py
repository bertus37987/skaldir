"""Deprecated alias package — the product package is now :mod:`skaldir`.

Every name below this package resolves to the *same module object* the
``skaldir`` import produces (``godsv.cli is skaldir.cli``), so module-level
state, monkeypatching and ``isinstance`` checks stay shared between the two
spellings:

    >>> import godsv, skaldir
    >>> godsv is skaldir
    True

Importing this package emits a :class:`DeprecationWarning` once; the code
itself is not duplicated and is not executed twice. Use ``skaldir``.
"""

from __future__ import annotations

import sys
from importlib import import_module
from importlib.abc import Loader, MetaPathFinder
from importlib.machinery import ModuleSpec

_OLD = "godsv"
_NEW = "skaldir"


def _warn_deprecated() -> None:
    """Announce the rename — on a TTY only (a pipe/log stays byte-clean).

    The policy and its reason live in :mod:`skaldir.deprecation`; this wrapper
    exists so the import works even if that module grows a heavier body.
    """
    from skaldir.deprecation import warn_deprecated_package

    warn_deprecated_package()


_warn_deprecated()

__all__ = ["__version__"]


class _AliasLoader(Loader):
    """Hands back the module object the ``skaldir`` import already produced."""

    def __init__(self, module, saved):
        self._module = module
        self._saved = saved

    def create_module(self, spec):
        return self._module

    def exec_module(self, module):
        # `module_from_spec` rewrites `__name__`/`__spec__`/`__loader__`/
        # `__package__` on the object it was handed; because that object is the
        # shared `skaldir` module, restore its own metadata right away.
        name, spec_, loader, package = self._saved
        module.__name__ = name
        module.__spec__ = spec_
        module.__loader__ = loader
        module.__package__ = package

    def get_code(self, fullname):
        """Source for ``python -m godsv.<mod>`` (runpy asks the loader get_code).

        Without this, `-m godsv.perf_lazy` died in runpy with an AttributeError.
        The target module's own loader is asked under the *real* module name, so
        the returned code object carries the right filename.
        """
        loader = self._saved[2]
        get_code = getattr(loader, "get_code", None)
        if get_code is None:
            raise ImportError(f"no code object available for {fullname!r}")
        return get_code(self._module.__name__)


class _AliasFinder(MetaPathFinder):
    """``godsv[.x]`` → the identical object as ``skaldir[.x]``."""

    def find_spec(self, fullname, path=None, target=None):
        if fullname != _OLD and not fullname.startswith(_OLD + "."):
            return None
        try:
            module = import_module(_NEW + fullname[len(_OLD):])
        except ImportError:
            return None
        saved = (
            module.__name__,
            getattr(module, "__spec__", None),
            getattr(module, "__loader__", None),
            getattr(module, "__package__", None),
        )
        spec = ModuleSpec(fullname, _AliasLoader(module, saved))
        spec.origin = getattr(module, "__file__", None)
        if hasattr(module, "__path__"):
            spec.submodule_search_locations = list(module.__path__)
        # NOTE: `sys.modules[fullname]` must NOT be filled here. When the name is
        # already in `sys.modules`, `importlib._bootstrap._find_spec` returns the
        # *found module's own* `__spec__` instead of this one, and the target
        # would then be loaded a second time under the alias name (measured:
        # two distinct module objects, module state split in half). The import
        # machinery registers the module under `spec.name` itself.
        return spec


def _install_finder() -> None:
    for finder in sys.meta_path:
        if isinstance(finder, _AliasFinder):
            return
    sys.meta_path.insert(0, _AliasFinder())


_install_finder()

# `import godsv` itself hands back the real package, so `godsv is skaldir`.
_target = import_module(_NEW)
sys.modules[_OLD] = _target
__version__ = getattr(_target, "__version__", "0.0.0")
