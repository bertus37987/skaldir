"""Guard: no module may ship without a product caller (the "orphan module" pattern).

The pattern this test exists for (found in this repo, recorded in
``MVP/88-integration-sweep.md``): a module is written, documented and covered by its own
tests, but **nothing in the product imports it** — so the feature exists and is
unreachable from any surface a user has. ``verify_flows.py`` was the instance that started
the sweep; a grep for the module name finds only the tests that exercise it.

What counts as a product caller
-------------------------------

Reachability is computed from the product's own roots over two layers:

* **the package** (``src/skaldir/**``) — roots are the console script
  (``skaldir.perf_lazy:main`` in ``pyproject.toml``) and every ``__main__`` module;
* **the host layer** (``integrations/**``) — the Hermes plugin that imports the package as
  ``godsv.*`` (the deprecation shim: ``src/godsv/__init__.py`` re-exports ``skaldir``).
  It is a real product surface: several modules (``discovery``, ``liveinsert``,
  ``synthesis``, …) are reached *only* from there, and a sweep that ignores it reports
  false orphans.

An edge is a static import (``import skaldir.x.y``, ``from .x import y``, …) **or** a dotted
string literal naming a module (``"skaldir.tools_core.bridge"`` in
``tools_core/registry.py`` — the one dynamic wiring this tree really has). A module that
can only be reached by neither is an orphan.

Two shapes are deliberately *not* accepted as reachable. (1) A test importing the module — the
tests are not a product surface. (2) A module-level CLI (a `main()` with an `if __name__ ==
"__main__"` guard on a plain module): `python -m skaldir.<module>` runs for someone who already
knows the module name, but the product's own surface (`skaldir --help`, the contract dump) never
shows it, so it is a gap to close (register the verb) or to register in the allow-list —
`python -m skaldir.system_one_calibration`-style entries are in the list today. `__main__.py`
modules are different: they *are* the package's documented `python -m <package>` entry point and
count as roots.

The allow-list
--------------

Some modules are legitimately library-only *today*: their caller is a surface that does not
exist yet, and the project decided to keep them (see ``MVP/14-slop-sweep.md`` §6/§7). Those
are listed in :data:`ALLOWED_UNREACHABLE` with the owner and the file that decided it. The
list is checked in both directions: an entry that became reachable again must be removed,
and a new unreachable module fails the build.

A failure here is not "delete the module": it is *wire it, or add it to the allow-list with
the owner that will wire it*.
"""

from __future__ import annotations

import ast
from collections import deque
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
HOST = REPO_ROOT / "integrations"

#: The console script (`pyproject.toml`, `[project.scripts]`) plus every `__main__` module.
ENTRY_POINTS = (
    "skaldir.perf_lazy",
    "skaldir.__main__",
    "skaldir.jev.__main__",
    "skaldir.tools_core.__main__",
)

#: `src/godsv/` is the deprecation shim; every `godsv.x` is `skaldir.x`.
ALIAS_ROOTS = {"godsv": "skaldir"}

#: Modules that are library-only today, with the reason and the owner of the missing caller.
#: Keep this list minimal and honest: every line is a *known* gap, not a permission slip.
ALLOWED_UNREACHABLE = {
    "skaldir.action_engine": (
        "W3/COMPOSE execution graph engine — wired via tests/test_action_engine.py and "
        "the planned `skaldir compose run` CLI verb (spec 72-action-engine-spec.md §11). "
        "Owner: the COMPOSE block — the wiring seam is agent_toolset.build_agent_toolset."
    ),
    "skaldir.perf_progress": (
        "library-only: the Codex-port frame limiter / coalescer. Its only caller today is the "
        "measurement harness tools/gv_perf_codex_compare.py; the product writes progress through "
        "progress.py::RunProgress and streaming.py. Owner of the missing caller: the streaming / "
        "perceived-speed seam (MVP/47-performance-codex.md)."
    ),
    "skaldir.system_one_calibration": (
        "System-One substrate (dataset shapes, frozen threshold profiles, §19/§20). Its only "
        "entry point today is a module-level CLI (`main()` + `__main__` guard, driven in the "
        "tests as `python -m skaldir.system_one_calibration`): no `register_cli`, no "
        "`cli_contract` entry and no product file imports it, so `skaldir --help` never shows "
        "it. Owner: the JEV block — the façade composition point is "
        "system_one_service.build_service (system_one_service.py:411, __all__:449), whose only "
        "importer is jev/decide.py:64; the gap is a missing `jev` verb "
        "(MVP/14-slop-sweep.md §7.2 keeps the substrate deliberately)."
    ),
    "skaldir.system_one_canary": (
        "System-One substrate (the J0…J5 stage machine, §21). Reached from tests only; no "
        "product caller, no CLI. Owner: the Jev façade / a `jev` canary verb — checked, neither "
        "system_one_flags.py nor jev/selftest.py references a stage, so nothing is waiting for "
        "the import (MVP/14-slop-sweep.md §7.2)."
    ),
    "skaldir.system_one_shadow": (
        "System-One substrate (the Voice triage pack evaluated against productive "
        "counter-examples, §90). Like calibration it has a module-level CLI (`main()` + "
        "`__main__` guard) that no product surface reaches — no `register_cli`, no contract "
        "entry, no importer. The harness it must drive (system_one_policy.ShadowHarness) *is* "
        "reached; only the offline pack is not. Owner: the Jev façade."
    ),
    "skaldir.voice_cockpit": (
        "Read-only cockpit payload for the desktop panel (report 63 V6: import-only, writes "
        "nothing). The desktop surface is parked per AGENTS.md; no `voice cockpit` CLI verb "
        "imports this module yet. Owner of the missing caller: the desktop/panel seam "
        "(FEATURE-TABLE #29)."
    ),

}


# --------------------------------------------------------------------------- #
# the scan — pure functions so the bite-proof test can run it on a scratch tree
# --------------------------------------------------------------------------- #
def _module_id(path: Path, base: Path) -> str:
    rel = path.relative_to(base).with_suffix("")
    parts = [p for p in rel.parts if p != "__init__"]
    return ".".join(parts)


def _alias(name: str) -> str:
    for old, new in ALIAS_ROOTS.items():
        if name == old:
            return new
        if name.startswith(old + "."):
            return new + name[len(old):]
    return name


def _py_files(base: Path) -> list[Path]:
    out: list[Path] = []
    for path in sorted(base.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        out.append(path)
    return out


def _edges(path: Path, module: str, by_mod: dict[str, Path]) -> set[str]:
    """Every module this file can reach: static imports + dotted module-string literals."""
    def resolve(name: str) -> set[str]:
        parts = _alias(name).split(".")
        for i in range(len(parts), 0, -1):
            candidate = ".".join(parts[:i])
            if candidate in by_mod:
                return {candidate}
        return set()

    found: set[str] = set()
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for imported in node.names:
                found |= resolve(imported.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = module.split(".")
                pkg = base[: len(base) - node.level]
                target = ".".join(pkg + (node.module.split(".") if node.module else []))
            else:
                target = _alias(node.module or "")
            if target:
                found |= resolve(target)
                for imported in node.names:
                    # `from pkg import name` — `name` may be a submodule of `pkg`
                    found |= resolve(target + "." + imported.name)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value
            if value.startswith(("skaldir.", "godsv.")):
                found |= resolve(value)
    return found


def scan_orphans(src: Path, host: Path, entry_points: tuple[str, ...]) -> dict[str, Path]:
    """Return {module: file} for every module no product file can reach.

    ``__init__`` modules are never reported: a package initialiser is imported by any import
    of anything inside the package, so it cannot be an orphan in this sense. They stay in the
    resolution map, though — `from .jev import wiring` resolves through the `skaldir.jev`
    package.
    """
    all_modules = {_module_id(path, src): path for path in _py_files(src)}
    graph: dict[str, set[str]] = {}
    for module, path in all_modules.items():
        graph[module] = _edges(path, module, all_modules)

    # every `__main__` module is itself a user-reachable entry point (`python -m ...`)
    roots = [m for m in all_modules if m.endswith(".__main__")]
    roots += [m for m in entry_points if m in all_modules and m not in roots]
    # the host layer contributes edges from (and only from) modules the package reaches
    host_edges: set[str] = set()
    host_files = _py_files(host) if host.is_dir() else []
    for path in host_files:
        host_edges |= _edges(path, "integrations.host", all_modules)

    seen = set(roots)
    queue = deque(roots)
    for module in host_edges:  # the host plugin is a root: it runs inside the host
        if module not in seen:
            seen.add(module)
            queue.append(module)
    while queue:
        current = queue.popleft()
        for nxt in graph.get(current, ()):
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return {
        m: p
        for m, p in all_modules.items()
        if m not in seen and p.name != "__init__.py"
    }


# --------------------------------------------------------------------------- #
# the guard
# --------------------------------------------------------------------------- #
def test_the_scan_is_not_vacuous():
    """A guard that silently scans nothing passes forever — pin the shape of the scan."""
    live = scan_orphans(SRC, HOST, ENTRY_POINTS)
    assert SRC.is_dir() and HOST.is_dir(), "the package or the host layer moved"
    modules = len(_py_files(SRC))
    assert modules > 150, f"only {modules} modules found under {SRC} — the scan is broken"
    for root in ENTRY_POINTS:
        path = SRC / (root.replace(".", "/") + ".py")
        assert path.is_file(), f"entry point {root} is not in the tree ({path})"
    assert len(live) < 40, f"the scan reports {len(live)} orphans — it is almost certainly broken"


def test_no_module_without_a_product_caller():
    """Every module is reachable from an entry point, or explicitly allow-listed."""
    orphans = scan_orphans(SRC, HOST, ENTRY_POINTS)
    unexpected = sorted(m for m in orphans if m not in ALLOWED_UNREACHABLE)
    assert not unexpected, (
        "module(s) nothing in the product imports — a feature nobody can reach:\n  "
        + "\n  ".join(f"{m}  ({orphans[m].relative_to(REPO_ROOT)})" for m in unexpected)
        + "\n\nWire it (an import from the surface that should use it), or add it to "
        "ALLOWED_UNREACHABLE with the reason and the owner of the missing caller."
    )


def test_allow_list_is_not_stale():
    """An allow-listed module that became reachable must leave the list (it is a gap register)."""
    orphans = scan_orphans(SRC, HOST, ENTRY_POINTS)
    healed = sorted(m for m in ALLOWED_UNREACHABLE if m not in orphans)
    assert not healed, (
        "these modules now have a product caller — remove them from ALLOWED_UNREACHABLE so the "
        f"list keeps meaning 'still unwired': {healed}"
    )


@pytest.mark.parametrize("module", sorted(ALLOWED_UNREACHABLE))
def test_every_allow_list_entry_names_its_owner(module: str):
    reason = ALLOWED_UNREACHABLE[module]
    assert len(reason) > 80, f"{module}: the reason is a placeholder, not a decision"
    assert "Owner" in reason or "owner" in reason, f"{module}: no owner named"


def _calls_a_cli_hook(path: Path, module: str) -> bool:
    """Does this file call `module`'s CLI hook?

    Covers the three shapes this tree really uses: `register_cli(sub)` (imported by name or
    as an attribute), `_register_x(sub)` (imported under an alias) and
    `getattr(module, "register_cli")(sub)`.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    called: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            called.add(func.id)
        elif isinstance(func, ast.Attribute):
            called.add(func.attr)
        elif isinstance(func, ast.Call):  # getattr(module, "register_cli")(sub)
            inner = func.func
            if (
                isinstance(inner, ast.Name)
                and inner.id == "getattr"
                and len(func.args) > 1
                and isinstance(func.args[1], ast.Constant)
            ):
                called.add(str(func.args[1].value))
    if called & {"register_cli", "attach"}:
        return True
    for node in ast.walk(tree):  # aliases bound by importing exactly this module
        if isinstance(node, ast.ImportFrom):
            if node.level:
                base = module.split(".")
                pkg = base[: len(base) - node.level]
                target = ".".join(pkg + (node.module.split(".") if node.module else []))
            else:
                target = _alias(node.module or "")
            if target == module:
                for imported in node.names:
                    if (imported.asname or imported.name) in called:
                        return True
    return False


def scan_uncalled_cli_hooks(src: Path, host: Path) -> dict[str, Path]:
    """{module: file} for every module that defines a CLI hook nothing calls.

    A `register_cli` that is imported but never invoked is the same defect one step later:
    the verb group exists as code and is not in the parser.
    """
    all_modules = {_module_id(path, src): path for path in _py_files(src)}
    callers: dict[str, set[Path]] = {m: set() for m in all_modules}
    for module, path in all_modules.items():
        for target in _edges(path, module, all_modules):
            if target != module:
                callers[target].add(path)
    for path in _py_files(host) if host.is_dir() else []:
        for target in _edges(path, "integrations.host", all_modules):
            callers[target].add(path)

    lonely: dict[str, Path] = {}
    for module, path in all_modules.items():
        if path.name == "__init__.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        defines_cli = any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name in {"register_cli", "attach"}
            for node in tree.body
        )
        if not defines_cli:
            continue
        if not any(_calls_a_cli_hook(importer, module) for importer in callers[module]):
            lonely[module] = path
    return lonely


def test_every_register_cli_module_is_plugged_into_the_cli():
    """The finer half of the pattern: a CLI surface whose ``register_cli`` is never called."""
    lonely = scan_uncalled_cli_hooks(SRC, HOST)
    assert not lonely, (
        "module(s) define a CLI hook (`register_cli`/`attach`) that no product file calls — the "
        "verb group exists as code and is unreachable from the parser:\n  "
        + "\n  ".join(f"{m}  ({p.relative_to(REPO_ROOT)})" for m, p in sorted(lonely.items()))
    )


def test_a_new_unwired_module_fails_the_guard(tmp_path: Path):
    """Bite-proof: on a scratch copy, a module nobody imports is reported as an orphan."""
    src = tmp_path / "src" / "skaldir"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "__main__.py").write_text("from .perf_lazy import main\n", encoding="utf-8")
    (src / "perf_lazy.py").write_text("from .wired import run\n\n\ndef main():\n    return run()\n")
    (src / "wired.py").write_text("def run():\n    return 0\n", encoding="utf-8")
    (src / "dead.py").write_text(
        '"""A feature nobody imports."""\n\n\ndef unreachable():\n    return 42\n',
        encoding="utf-8",
    )
    orphans = scan_orphans(
        tmp_path / "src", tmp_path / "integrations", ("skaldir.perf_lazy", "skaldir.__main__")
    )
    assert set(orphans) == {"skaldir.dead"}, orphans
    # and it stops being an orphan the moment something imports it
    (src / "wired.py").write_text("from .dead import unreachable\n\n\ndef run():\n    return 0\n")
    assert scan_orphans(tmp_path / "src", tmp_path / "integrations", ("skaldir.perf_lazy",)) == {}


def test_a_cli_hook_nobody_calls_fails_the_guard(tmp_path: Path):
    """Bite-proof for the second half: a `register_cli` that is imported but never invoked."""
    src = tmp_path / "src" / "skaldir"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "perf_lazy.py").write_text(
        "from .lonely_cli import register_cli as _register_lonely\n\n\n"
        "def main(subparsers=None):\n"
        "    return 0  # the hook is imported and never called\n"
    )
    (src / "lonely_cli.py").write_text(
        "def register_cli(subparsers):\n"
        '    """A verb group that would never reach the parser."""\n'
        "    return subparsers\n",
        encoding="utf-8",
    )
    # it is reachable (something imports it) …
    assert scan_orphans(
        tmp_path / "src", tmp_path / "integrations", ("skaldir.perf_lazy",)
    ) == {}
    # … but the hook is not called, which is the defect
    assert set(scan_uncalled_cli_hooks(tmp_path / "src", tmp_path / "integrations")) == {
        "skaldir.lonely_cli"
    }
    (src / "perf_lazy.py").write_text(
        "from .lonely_cli import register_cli as _register_lonely\n\n\n"
        "def main(subparsers=None):\n    return _register_lonely(subparsers)\n"
    )
    assert scan_uncalled_cli_hooks(tmp_path / "src", tmp_path / "integrations") == {}
