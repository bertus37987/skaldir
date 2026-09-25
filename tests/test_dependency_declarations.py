"""Every third-party import the shipped package makes must be declared — or explicitly excused.

The rule this file enforces, and why it exists as a *test* instead of a review habit:

`src/skaldir/adversarial.py` drives the browser half of the adversarial suite through
``playwright``, imported lazily inside a function. Lazily is the right call (the core CLI must
not need a browser to answer ``--version``), but it made the import invisible to the one rule
that already existed — ``test_packaging``'s
``test_every_module_level_third_party_import_is_a_runtime_dependency``
only inspects *module-level* imports — and ``playwright`` was declared nowhere at all. The
published package therefore shipped a browser suite that no user could run: on a clean install
the suite reported

* ``adversarial selftest`` -> exit 2, ``"the suite does not distinguish the targets"``,
  ``detectors fired: 0/8``, 9 false positives — a **false product defect**, blaming the
  detectors for a package that was never installed; and
* ``adversarial run --with-api`` -> exit 7 internal **with a traceback**.

So the rule is widened from "module-level" to "every absolute third-party import in the shipped
tree, at any depth", and the failure message names the file, the line, the module and the fix.

Two buckets, both explicit:

* declared — the module resolves to a distribution named in ``[project].dependencies`` or in one
  of ``[project.optional-dependencies]``. The mapping module -> distribution is written out below,
  because ``yaml`` is shipped by ``PyYAML`` and ``Xlib`` by ``python-xlib``, and
  ``importlib.metadata.packages_distributions()`` answers with whatever happens to be installed
  *here* (on this host ``fastmcp`` resolves to ``fastmcp-slim``, which is not what the project
  declares) — a packaging rule must not depend on the reviewer's machine.
* excused — a module provided by the *host application* rather than by a package on the index,
  with the reason written next to it and a second test proving the excuse is still needed.
"""

from __future__ import annotations

import ast
import re
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PKG = REPO / "src" / "skaldir"
PYPROJECT = REPO / "pyproject.toml"

#: Modules this repository provides itself. `patch_app` is not a package on the index: it is the
#: overlay script under `app-overlay/`, injected onto `sys.path` by `appbuild._run_overlay`.
FIRST_PARTY = {"skaldir", "godsv", "patch_app"}

#: top-level module -> the distribution that ships it. Written out on purpose: the name a module
#: imports under is not the name a user has to install (the whole class of bug this file exists
#: to catch), and resolving it from `importlib.metadata` would make the rule environment-specific.
MODULE_DISTRIBUTIONS: dict[str, str] = {
    "Xlib": "python-xlib",
    "cryptography": "cryptography",
    "fastapi": "fastapi",
    "fastmcp": "fastmcp",
    "httpx": "httpx",
    "jsonschema": "jsonschema",
    "playwright": "playwright",
    "pydantic": "pydantic",
    "starlette": "starlette",
    "uvicorn": "uvicorn",
    "websockets": "websockets",
    "yaml": "PyYAML",
}

#: Imported, but deliberately **not** a dependency: the Hermes host application provides it when
#: Skaldir runs as a plugin, and it cannot be installed from an index as a requirement of this
#: package. The import is guarded (`modelroles.overrides_enabled` returns False when it is
#: absent), so a standalone install behaves. Every entry needs a reason, and
#: `test_every_excused_module_is_still_needed` fails when one goes stale.
EXCUSED: dict[str, str] = {
    "hermes_cli": (
        "provided by the Hermes host application (plugin guest), not installable from an index; "
        "imported only inside a try/except that returns False — src/skaldir/modelroles.py"
    ),
}

#: The extras this file knows about. An extra nobody classified here means an extra nobody
#: decided about — declare it above, or say why it is exempt.
KNOWN_EXTRAS = frozenset(
    {"api", "browser", "computer-use", "dev", "mcp", "structured", "treasury"}
)

#: The extras that exist for a *lazy* capability, and the module that proves each one is needed.
LAZY_CAPABILITIES: dict[str, str] = {
    "extra:browser": "playwright",
    "extra:computer-use": "websockets",
    "extra:mcp": "fastmcp",
    "extra:structured": "jsonschema",
}


def _distribution_name(requirement: str) -> str:
    """`PyYAML>=6.0,<7` -> `pyyaml` (the name pip and importlib.metadata compare on)."""
    head = re.split(r"[\[<>=!~; ]", requirement.strip(), maxsplit=1)[0]
    return head.lower().replace("_", "-")


def declared_entries() -> dict[str, set[str]]:
    """Every declared distribution -> the set of places it is declared in.

    A set, not one place: `jsonschema` is declared in both `dev` and `structured`, and the
    capability check below needs to see the second one.
    """
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    project = data["project"]
    out: dict[str, set[str]] = {}
    for spec in project.get("dependencies", []):
        out.setdefault(_distribution_name(spec), set()).add("dependencies")
    for extra, specs in (project.get("optional-dependencies") or {}).items():
        for spec in specs:
            out.setdefault(_distribution_name(spec), set()).add(f"extra:{extra}")
    return out


def all_imports() -> list[tuple[str, str]]:
    """``(top-level module, "file:line")`` for every absolute import in the tree, at any depth.

    Nested imports count. That is the point: the defect this file guards was a lazy import, and a
    "module-level only" rule is exactly the blind spot that let it ship.
    """
    found: list[tuple[str, str]] = []
    for path in sorted(PKG.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
                names = [node.module.split(".")[0]]
            else:
                continue
            where = f"{path.relative_to(REPO)}:{node.lineno}"
            found.extend((name, where) for name in names)
    return found


def third_party_imports() -> dict[str, list[str]]:
    """module -> every ``file:line`` it is imported at, for imports neither stdlib nor ours."""
    out: dict[str, list[str]] = {}
    for module, where in all_imports():
        if module in FIRST_PARTY or module in sys.stdlib_module_names:
            continue
        out.setdefault(module, []).append(where)
    return out


def undeclared(declared: dict[str, set[str]]) -> list[str]:
    """The violations, as printable lines — the one function the guard tests share."""
    problems: list[str] = []
    for module, places in sorted(third_party_imports().items()):
        if module in EXCUSED:
            continue
        distribution = MODULE_DISTRIBUTIONS.get(module)
        if distribution is None:
            problems.append(
                f"{module} ({', '.join(sorted(places))}) -> no module->distribution mapping: "
                f"add `{module}` to MODULE_DISTRIBUTIONS and declare the distribution, or excuse it"
            )
            continue
        if _distribution_name(distribution) not in declared:
            problems.append(
                f"{module} ({', '.join(sorted(places))}) -> needs {distribution!r}, "
                "declared nowhere"
            )
    return problems


# --------------------------------------------------------------------------- #
# the rule                                                                      #
# --------------------------------------------------------------------------- #


def test_the_scanner_reads_the_shipped_tree():
    """A scanner that silently matched nothing would make every other test here vacuous."""
    imports = third_party_imports()
    assert len(imports) >= 8, f"too few third-party imports found — the scanner stopped: {imports}"
    assert "playwright" in imports, (
        "playwright is gone from the scan: the browser suite's driver is the case this test was "
        "written for — if it was refactored away, revisit MODULE_DISTRIBUTIONS and the browser "
        "extra deliberately instead of letting this rule quietly stop applying"
    )


def test_every_third_party_import_is_declared_somewhere():
    """A lazy import is still a dependency: it must be in `dependencies` or in an extra."""
    declared = declared_entries()
    problems = undeclared(declared)
    assert not problems, (
        "these third-party imports are declared in no dependency and in no extra, so the feature "
        "they carry cannot run from an install of this package (measured on a clean install: "
        "`skaldir adversarial selftest` -> exit 2 blaming the detectors, `adversarial run "
        "--with-api` -> exit 7 with a traceback):\n  "
        + "\n  ".join(problems)
        + f"\ndeclared today: {sorted(declared)}"
    )


def test_every_excused_module_is_still_needed():
    """An excuse for a module nobody imports any more is a comment pretending to be a decision."""
    imported = set(third_party_imports())
    stale = sorted(set(EXCUSED) - imported)
    assert not stale, f"EXCUSED names modules the tree no longer imports: {stale}"


def test_every_extra_is_classified_and_its_capability_is_real():
    """No extra exists without a purpose, and no declared extra covers code that is gone."""
    declared = declared_entries()
    extras = {
        entry
        for locations in declared.values()
        for entry in locations
        if entry.startswith("extra:")
    }
    unknown = {extra for extra in extras if extra.removeprefix("extra:") not in KNOWN_EXTRAS}
    assert not unknown, (
        f"extras nobody classified: {sorted(unknown)} — add them to KNOWN_EXTRAS (and to "
        "LAZY_CAPABILITIES when they exist for a lazily imported module)"
    )
    imported = set(third_party_imports())
    for extra, module in LAZY_CAPABILITIES.items():
        assert extra in extras, f"{extra} missing from pyproject — {module} undeclared"
        assert module in imported, f"{extra} names {module}, which the tree no longer imports"


def test_the_guard_bites_on_the_shipped_defect():
    """Positive control: with this morning's declaration set, the rule fails on `playwright`.

    Without this, the guard could pass by being unable to fail. The set below is the published
    `pyproject.toml` (commit `41ff3b7`): four runtime dependencies plus the extras
    `dev`/`mcp`/`treasury` — and no `browser` extra, no `api`, no `computer-use`, no `structured`.
    """
    published = {
        "pyyaml": {"dependencies"},
        "httpx": {"dependencies"},
        "pydantic": {"dependencies"},
        "cryptography": {"dependencies", "extra:treasury"},
        "pytest": {"extra:dev"},
        "ruff": {"extra:dev"},
        "bandit": {"extra:dev"},
        "fastapi": {"extra:dev"},
        "fastmcp": {"extra:mcp"},
    }
    problems = undeclared(published)
    for expected in ("playwright", "Xlib", "jsonschema", "websockets"):
        assert any(expected in problem for problem in problems), (
            f"the guard did not reproduce the published defect for {expected!r} — it cannot be "
            f"trusted to catch the next one; problems found: {problems}"
        )
    assert len(problems) >= 4, problems
