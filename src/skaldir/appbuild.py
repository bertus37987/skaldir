"""God's Voice desktop app build — native integration on top of the Hermes tree.

Builds the *God's Voice* desktop app: the Hermes desktop source (the app is
the same engine) with the GV overlay applied — bundled panel plugin (no
install step), gold theme as the default, the God's Voice splash, mark and
app identity, nous-portal wording neutralised.

How it stays safe and update-proof:

  * never edits the Hermes checkout — a hardlinked mirror (rsync --link-dest)
    lives under $HERMES_HOME/godsv/app-src and is refreshed per build,
  * the overlay is applied to that mirror (anchored patches fail loudly when a
    Hermes update moves a line — see app-overlay/patch_app.py),
  * the packed app is deployed to $HERMES_HOME/godsv/app/linux-unpacked and
    launched by the GV launcher (desktop.hermes_app_binary prefers it).

CLI: `skaldir desktop build-app [--force] [--perf-probe]`
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import desktop
from .env import getenv

SKIP_DIRS = (".git", "apps/desktop/release", "apps/desktop/dist", "apps/desktop/node_modules/.vite")

# Opt-in for a *probe* build: SKALDIR_PERF_PROBE=1 puts VITE_PERF_PROBE=1 into the
# `npm run pack` environment, and `vite.config.ts` (debugEntry) bundles the
# dev-only perf driver (`@/debug/dev-only`) into the production renderer only
# then. Measurement builds only — a release build must never carry the debug
# graph, so nothing else (and no ambient VITE_PERF_PROBE) may switch it on.
PERF_PROBE_ENV = "SKALDIR_PERF_PROBE"


def perf_probe_enabled() -> bool:
    """The env-var opt-in: `SKALDIR_PERF_PROBE=1`."""
    return str(getenv(PERF_PROBE_ENV, "") or "").strip() == "1"


def build_env(*, probe: bool) -> dict[str, str]:
    """Environment for `npm run pack`; probe builds get `VITE_PERF_PROBE=1`."""
    env = dict(os.environ)
    env.setdefault("NODE_OPTIONS", "--max-old-space-size=16384")
    if probe:
        env["VITE_PERF_PROBE"] = "1"
    else:
        # A stray ambient VITE_PERF_PROBE must not leak the debug graph into a
        # release artifact — `perf_probe_enabled()` is the only supported path.
        env.pop("VITE_PERF_PROBE", None)
    return env


def hermes_source() -> Path:
    env = getenv("HERMES_SOURCE")
    if env:
        return Path(env)
    return desktop.hermes_home() / "hermes-agent"


def source_mirror() -> Path:
    return desktop.hermes_home() / "godsv" / "app-src"


def app_dir() -> Path:
    return desktop.hermes_home() / "godsv" / "app"


def unpacked_dir(root: Path | None = None) -> Path:
    return (root or app_dir()) / "linux-unpacked"


def packaged_binary(root: Path | None = None) -> Path | None:
    """The GV app executable, if a build has been deployed."""
    base = unpacked_dir(root)
    if not base.is_dir():
        return None
    preferred = base / "gods-voice"
    if preferred.is_file() and os.access(preferred, os.X_OK):
        return preferred
    for candidate in sorted(base.iterdir()):
        if candidate.is_file() and os.access(candidate, os.X_OK) and candidate.suffix != ".so":
            return candidate
    return None


def _rsync(from_dir: Path, to_dir: Path, *, extra: list[str] | None = None) -> None:
    to_dir.mkdir(parents=True, exist_ok=True)
    cmd = ["rsync", "-a", "--delete"]
    for rel in SKIP_DIRS:
        cmd += ["--exclude", rel]
    if extra:
        cmd += extra
    cmd += [str(from_dir) + "/", str(to_dir) + "/"]
    subprocess.run(cmd, check=True, capture_output=True)  # nosec B603 — fixed argv


def sync_source(*, force: bool = False) -> Path:
    """Hardlinked mirror of the Hermes tree under godsv/app-src."""
    source = hermes_source()
    if not (source / "apps" / "desktop" / "package.json").exists():
        raise RuntimeError(f"Hermes source tree not found at {source}")
    mirror = source_mirror()
    stamp = mirror / ".godsv-mirror"
    if force or not stamp.exists():
        _rsync(source, mirror, extra=["--link-dest", str(source)])
        stamp.write_text(f"synced {time.strftime('%Y-%m-%d %H:%M:%S')}\n", encoding="utf-8")
    else:
        # incremental refresh: relink changed files, keep the rest
        _rsync(source, mirror, extra=["--link-dest", str(source)])
        stamp.write_text(f"synced {time.strftime('%Y-%m-%d %H:%M:%S')}\n", encoding="utf-8")
    return mirror


def _run_overlay(mirror: Path, repo: Path) -> dict:
    sys.path.insert(0, str(repo / "app-overlay"))
    import patch_app  # type: ignore[import-not-found]  # noqa: PLC0415

    return patch_app.apply_patches(mirror, repo)


def _npm() -> str:
    npm = shutil.which("npm")
    if not npm:
        raise RuntimeError("npm not found on PATH")
    return npm


def build(*, force: bool = False, timeout: int = 3600, perf_probe: bool | None = None) -> dict:
    """Full pipeline: mirror → overlay → `npm run pack` → deploy. Returns a report.

    ``perf_probe=True`` embeds the dev-only perf driver into the built renderer
    (env opt-in ``SKALDIR_PERF_PROBE=1``; the CLI flag ``--perf-probe`` maps here).
    A probe build is a *measurement* artifact: same overlay, plus the debug graph.
    """
    probe = perf_probe_enabled() if perf_probe is None else perf_probe
    repo = Path(__file__).resolve().parent.parent.parent
    started = time.time()
    mirror = sync_source(force=force)

    desktop_dir = mirror / "apps" / "desktop"
    # fresh build outputs — never trust a stale dist/release inside the mirror
    for sub in ("dist", "release"):
        shutil.rmtree(desktop_dir / sub, ignore_errors=True)

    overlay = _run_overlay(mirror, repo)

    npm = _npm()
    env = build_env(probe=probe)
    proc = subprocess.run(  # nosec B603 — fixed argv, user-owned tree
        [npm, "run", "pack", "--", "--publish", "never"],
        cwd=desktop_dir,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "")[-2000:]
        return {
            "ok": False,
            "stage": "npm run pack",
            "returncode": proc.returncode,
            "output_tail": tail,
            "mirror": str(mirror),
            "overlay": overlay,
            "perf_probe": probe,
            "seconds": round(time.time() - started, 1),
        }

    built = desktop_dir / "release" / "linux-unpacked"
    binary = None
    if built.is_dir():
        candidate = built / "gods-voice"
        binary = candidate if candidate.exists() else next(
            (p for p in built.iterdir() if p.is_file() and os.access(p, os.X_OK)), None
        )
    if binary is None:
        return {
            "ok": False,
            "stage": "locate packaged binary",
            "mirror": str(mirror),
            "overlay": overlay,
            "perf_probe": probe,
            "seconds": round(time.time() - started, 1),
        }

    # deploy → $HERMES_HOME/godsv/app/linux-unpacked (atomic-ish rsync + swap)
    target = app_dir()
    staging = target.with_name("app-staging")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)
    subprocess.run(  # nosec B603 — fixed argv
        ["rsync", "-a", "--delete", str(built) + "/", str(staging / "linux-unpacked") + "/"],
        check=True,
        capture_output=True,
    )
    shutil.rmtree(target, ignore_errors=True)
    staging.rename(target)
    deployed = packaged_binary()
    return {
        "ok": deployed is not None,
        "stage": "deployed",
        "binary": str(deployed) if deployed else "",
        "mirror": str(mirror),
        "overlay": overlay,
        "perf_probe": probe,
        "seconds": round(time.time() - started, 1),
    }


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    force = "--force" in argv
    probe = True if "--perf-probe" in argv else None
    result = build(force=force, perf_probe=probe)
    if result.get("ok"):
        suffix = (
            "  [PERF-PROBE — measurement build, NOT a release]"
            if result.get("perf_probe")
            else ""
        )
        print(f"✓ God's Voice app built: {result['binary']}  ({result['seconds']}s){suffix}")
        return 0
    print(f"✗ Build failed at stage '{result.get('stage')}' ({result.get('seconds')}s)")
    tail = result.get("output_tail")
    if tail:
        print(tail)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
