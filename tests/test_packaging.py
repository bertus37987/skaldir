"""Packaging smoke tests: is `skaldir` a real, installable tool?

Three questions, three groups of tests — the *installation* itself is proven in
`tests/test_packaging_install.py` (a real `uv tool install` into a throwaway prefix, then the
installed console script answering `--version` / `contract dump --json` / `selftest --json`
from a different working directory).

1. **Metadata** — is the package self-describing (name, version, console script,
   requires-python) and does it declare *every* module-level third-party import in `godsv`?
   That rule is written against a measured hole, not a theory: `cli.py` imports
   `godsv.treasury` at module level, `treasury.py` imports `cryptography.fernet` at module
   level, and `cryptography` used to live only in the `dev`/`treasury` extra — so a fresh
   `uv tool install` produced a `skaldir` that died with
   `ModuleNotFoundError: No module named 'cryptography'` *before* argparse ran, meaning even
   `--version` was dead. See `MVP/20-shipping.md` §D.
2. **Contents** — does the wheel carry the data files the code reads by path (the CLI banner
   art, the Skaldir icons, the bundled fonts, the prompt files) and no `.pyc`?
3. **Round trip** — does the `sdist` build at all? It did not: an *absolute* symlink under
   `outputs/ui-audit/chromium-profile/` got packed, and both `uv build` and `uv pip install`
   then refused the tarball ("symlink path … is absolute, but external symlinks are not
   allowed"), so `uv tool install --from .` / `pipx install .` were dead on arrival.
"""

from __future__ import annotations

import ast
import re
import shutil
import subprocess
import sys
import tarfile
import tomllib
import warnings
import zipfile
from importlib.metadata import packages_distributions
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PKG = REPO / "src" / "skaldir"
LEGACY_PKG = REPO / "src" / "godsv"  # the alias package
CONSOLE_SCRIPT = "skaldir"
LEGACY_CONSOLE_SCRIPT = "godsvoice"
ENTRY_TARGET = "skaldir.perf_lazy:main"
PYPROJECT = REPO / "pyproject.toml"


def _pyproject() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def _dist_name(requirement: str) -> str:
    """`PyYAML>=6.0,<7` -> `pyyaml` (the name `importlib.metadata` and pip compare on)."""
    return re.split(r"[\[<>=!~; ]", requirement.strip(), maxsplit=1)[0].lower().replace("_", "-")


def _module_level_imports() -> list[tuple[str, str]]:
    """``(top-level module, "file:line")`` for every *module-level* absolute import in `godsv`.

    Function-level imports are this codebase's deliberate lazy pattern (a command imports its
    provider when it runs). Module-level imports execute for anyone who imports that module, so
    those — and only those — are the packaging contract.
    """
    found: list[tuple[str, str]] = []
    unparseable: list[str] = []
    for path in sorted(PKG.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError, OSError) as exc:
            # Sibling blocks edit these files in parallel (and a half-written file is not a
            # packaging defect): skip it, but never silently — the warning lands in the summary.
            unparseable.append(f"{path.relative_to(REPO)}: {exc}")
            continue
        for node in tree.body:
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
                names = [node.module.split(".")[0]]
            else:
                continue
            where = f"{path.relative_to(REPO)}:{node.lineno}"
            found.extend((name, where) for name in names)
    if unparseable:
        warnings.warn("unparseable modules were skipped: " + "; ".join(unparseable), stacklevel=2)
    return found


def _third_party_module_level_imports() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for name, where in _module_level_imports():
        if name == "godsv" or name in sys.stdlib_module_names:
            continue
        out.setdefault(name, []).append(where)
    return out


def test_every_module_level_third_party_import_is_a_runtime_dependency():
    declared = {_dist_name(spec) for spec in _pyproject()["project"]["dependencies"]}
    distributions = packages_distributions()  # installed module -> [distribution, ...]
    imports = _third_party_module_level_imports()
    assert imports, "the scanner matched no third-party import — it stopped reading the code"
    missing = []
    for module, places in imports.items():
        dists = distributions.get(module) or [module]
        if not any(_dist_name(dist) in declared for dist in dists):
            missing.append(f"{module} ({', '.join(places)}) -> distributions {dists}")
    assert not missing, (
        "these module-level imports are not covered by [project].dependencies, so a fresh "
        "install dies at import time (measured: `skaldir --version` -> "
        "ModuleNotFoundError: No module named 'cryptography'):\n  "
        + "\n  ".join(missing)
        + f"\ndeclared today: {sorted(declared)}"
    )


def test_project_metadata_is_installable():
    project = _pyproject()["project"]
    assert project["name"], "a distribution name is required (it is what `uv tool install` takes)"
    assert re.fullmatch(r"\d+\.\d+\.\d+", project["version"]), project["version"]
    assert project["requires-python"].startswith(">="), project["requires-python"]
    assert (REPO / project["readme"]).is_file(), project["readme"]
    assert project["scripts"][CONSOLE_SCRIPT] == ENTRY_TARGET
    declared = [_dist_name(spec) for spec in project["dependencies"]]
    assert len(declared) == len(set(declared)), f"a dependency is declared twice: {declared}"
    for required in ("pyyaml", "httpx", "pydantic", "cryptography"):
        assert required in declared, f"{required} missing from [project].dependencies: {declared}"


#: The only host-specific path the shipped sources may carry. `logo.py` names the owner's design
#: file to *regenerate* the art; the CLI itself renders the art shipped in `assets/`. Any other
#: path here means the installed package needs this checkout (MVP/20-shipping.md §E).
ALLOWED_HOST_PATH_LITERALS = {"/home/willy/Documents/Design_Docs/logos_icons/Skaldir_Logo.png"}


def test_shipped_sources_carry_no_other_host_paths():
    found: set[str] = set()
    for path in sorted(PKG.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        found.update(re.findall(r'"(\/(?:home|Users)\/[^"\n]*)"', text))
    assert found == ALLOWED_HOST_PATH_LITERALS, (
        "host absolute paths in the shipped package: "
        f"{sorted(found - ALLOWED_HOST_PATH_LITERALS)} — an install must not need this checkout"
    )


def _uv() -> str:
    uv = shutil.which("uv")
    if not uv:
        pytest.skip("uv is not installed — cannot build or install the distributions")
    return uv


@pytest.fixture(scope="module")
def built_distributions(tmp_path_factory) -> dict:
    """`uv build`: the sdist, then the wheel built *from that sdist* (the round trip)."""
    out = tmp_path_factory.mktemp("gv-dist")
    proc = subprocess.run(
        [_uv(), "build", "--out-dir", str(out)],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=900,
    )
    raw = f"$ uv build --out-dir {out}\n{proc.stdout}{proc.stderr}"
    if proc.returncode != 0:
        offline = re.search(
            r"(failed to fetch|failed to download|offline|network|no such host)", raw, re.I
        )
        if offline:
            pytest.skip(f"the build backend is not available offline:\n{raw}")
        pytest.fail(f"`uv build` failed — a shipping defect (MVP/20-shipping.md §C):\n{raw}")
    wheels, sdists = sorted(out.glob("*.whl")), sorted(out.glob("*.tar.gz"))
    assert wheels and sdists, raw
    return {"wheel": wheels[-1], "sdist": sdists[-1], "raw": raw}


def test_wheel_carries_every_data_file_the_code_reads(built_distributions):
    with zipfile.ZipFile(built_distributions["wheel"]) as archive:
        names = set(archive.namelist())
        assets = {
            f"skaldir/{path.relative_to(PKG).as_posix()}"
            for path in (PKG / "assets").rglob("*")
            if path.is_file()
        }
        assert assets, "the source tree has no assets"
        assert assets <= names, f"missing from the wheel: {sorted(assets - names)}"
        banner = archive.read("skaldir/assets/logo.ansi").decode("utf-8")
    assert banner.strip(), "the CLI banner art shipped empty"
    assert "skaldir/assets/skaldir-logo.png" in names
    prompts = {name for name in names if name.startswith("skaldir/prompts/")}
    assert len(prompts) >= 10, sorted(prompts)
    assert not [name for name in names if name.endswith(".pyc") or "__pycache__" in name]


def test_wheel_metadata_declares_the_version_and_runtime_dependencies(built_distributions):
    project = _pyproject()["project"]
    with zipfile.ZipFile(built_distributions["wheel"]) as archive:
        names = archive.namelist()
        metadata_name = next(n for n in names if n.endswith(".dist-info/METADATA"))
        metadata = archive.read(metadata_name).decode()
        entry_points = archive.read(
            next(n for n in names if n.endswith(".dist-info/entry_points.txt"))
        ).decode()
    assert f"\nName: {project['name']}\n" in f"\n{metadata}"
    version_line = f"\nVersion: {project['version']}\n"
    assert version_line in f"\n{metadata}", "the wheel is not the current version"
    assert f"\nRequires-Python: {project['requires-python']}\n" in f"\n{metadata}"
    assert f"{CONSOLE_SCRIPT} = {ENTRY_TARGET}" in entry_points, entry_points
    # the deprecated alias is the SAME entry point: same behaviour, same exit codes
    assert f"{LEGACY_CONSOLE_SCRIPT} = {ENTRY_TARGET}" in entry_points, entry_points
    requires = {
        _dist_name(spec) for spec in re.findall(r"^Requires-Dist: ([^\s;]+)", metadata, re.M)
    }
    assert {_dist_name(spec) for spec in project["dependencies"]} <= requires, (
        f"declared in pyproject but not in the wheel metadata: {sorted(requires)}"
    )


def test_sdist_packs_no_symlinks(built_distributions):
    """Regression: `outputs/ui-audit/chromium-profile/SingletonSocket` is an absolute symlink.

    Hatchling's default sdist selection walked it in, and unpacking the tarball then failed with
    "symlink path `/tmp/org.chromium.…` is absolute, but external symlinks are not allowed" — the
    failure took the *whole* `uv build` and every install-from-checkout with it.
    """
    with tarfile.open(built_distributions["sdist"]) as archive:
        links = [member.name for member in archive.getmembers() if member.issym() or member.islnk()]
        names = archive.getnames()
    assert not links, f"the sdist packs symlinks, which no unpacker accepts: {links}"
    assert any(name.endswith("/prompts/voice_triage.v1.md") for name in names), "prompts missing"
    assert any(name.endswith("/pyproject.toml") for name in names)
