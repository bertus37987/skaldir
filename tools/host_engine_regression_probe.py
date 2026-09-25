#!/usr/bin/env python3
"""Run selected hermes-agent test functions under the ENGINE's own interpreter.

Why: the engine checkout ships a runtime venv (with openai/requests) but no
pytest, and chapter 09 §2 requires the *existing* clock/wait regressions to be
re-checked against a patched tree before any RF change is called done. The two
modules in question use nothing from pytest beyond ``mark.parametrize`` and the
``monkeypatch`` fixture, so this runs the REAL code paths with a tiny stand-in
instead of installing pytest into the host venv.

    <engine-python> tools/host_engine_regression_probe.py <tree> <test-module-file>...

Exit 0 only when every collected test passes. One line per case.
"""
from __future__ import annotations

import importlib.util
import inspect
import sys
import traceback
import types
from pathlib import Path


class _MonkeyPatch:
    """The subset of pytest's MonkeyPatch these modules use."""

    def __init__(self) -> None:
        self._undo: list[tuple[object, str, object]] = []

    def setattr(self, target: object, name: str, value: object) -> None:
        self._undo.append((target, name, getattr(target, name)))
        setattr(target, name, value)

    def undo(self) -> None:
        for target, name, old in reversed(self._undo):
            setattr(target, name, old)
        self._undo.clear()

    def setenv(self, name: str, value: str) -> None:  # unused here, kept for parity
        import os

        self._undo.append((os.environ, name, os.environ.get(name)))
        os.environ[name] = value


def _install_pytest_stub() -> None:
    """Minimal ``pytest`` so the modules can import; parametrize is recorded."""

    def parametrize(argnames: str, argvalues: list) -> object:
        names = [n.strip() for n in argnames.split(",")]

        def decorate(fn):
            fn._gv_param_names = names
            fn._gv_param_values = [list(v) if isinstance(v, (tuple, list)) else [v] for v in argvalues]
            return fn

        return decorate

    pytest = types.ModuleType("pytest")
    pytest.mark = types.SimpleNamespace(parametrize=parametrize, linux_only=lambda f: f)
    sys.modules.setdefault("pytest", pytest)


def load_module(path: Path):
    spec = importlib.util.spec_from_file_location(f"gv_probe_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def run_module(module, label: str) -> tuple[int, int]:
    passed = failed = 0
    for name, fn in sorted(vars(module).items()):
        if not (name.startswith("test_") and inspect.isfunction(fn)):
            continue
        params = inspect.signature(fn).parameters
        names = getattr(fn, "_gv_param_names", None)
        cases = getattr(fn, "_gv_param_values", [None]) if names else [None]
        for case in cases:
            kwargs: dict[str, object] = {}
            monkeypatch = _MonkeyPatch()
            if names and case is not None:
                kwargs.update(dict(zip(names, case, strict=False)))
            if "monkeypatch" in params:
                kwargs["monkeypatch"] = monkeypatch
            suffix = f"[{', '.join(str(v) for v in case or [])}]" if names else ""
            try:
                fn(**kwargs)
            except Exception:  # noqa: BLE001 — the probe reports, it does not judge
                failed += 1
                print(f"FAIL {label}::{name}{suffix}")
                print("".join(traceback.format_exc().splitlines(keepends=True)[-6:]).rstrip())
            else:
                passed += 1
                print(f"PASS {label}::{name}{suffix}")
            finally:
                monkeypatch.undo()
    return passed, failed


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__, file=sys.stderr)
        return 2
    tree = Path(sys.argv[1]).resolve()
    sys.path.insert(0, str(tree))
    _install_pytest_stub()
    passed = failed = 0
    for raw in sys.argv[2:]:
        path = Path(raw)
        path = path if path.is_absolute() else tree / raw
        module = load_module(path)
        ok, bad = run_module(module, path.stem)
        passed += ok
        failed += bad
    print(f"engine regression probe: {passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
