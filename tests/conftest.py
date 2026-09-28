"""No test may touch the developer's real Hermes home (Wave 1; measured contamination).

The live ``~/.hermes/config.yaml`` carried an ``mcp_servers.godsv-tools`` entry whose ``--runtime``
and ``--project`` pointed into ``/tmp/pytest-of-willy/pytest-120/...``. ``mcp_link.config_path()``
resolves ``$HERMES_HOME/config.yaml`` and ``$HERMES_HOME`` defaults to ``~/.hermes``, so any test
(or helper) that took the default path wrote into the real install. Two guards, because either one
alone is bypassable:

1. ``HERMES_HOME``/``GODSV_HOME`` are pointed at a throwaway directory for the session — unless the
   developer set them explicitly (an explicit choice is respected).
2. The real config file is fingerprinted before and after every test: the test that changes it
   fails with its own name, instead of silently contaminating the install.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

import pytest

# Resolved before any fixture rewrites the environment, so it always names the real install.
REAL_HERMES_HOME = Path(os.environ.get("GODSV_REAL_HERMES_HOME") or "") or (Path.home() / ".hermes")
REAL_CONFIG = REAL_HERMES_HOME / "config.yaml"


def _fingerprint(path: Path) -> tuple[int, int, str] | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    return (path.stat().st_mtime_ns, len(data), hashlib.sha256(data).hexdigest())


#: Runtime artifacts that live in the repository root and are tracked in git. They are
#: *runtime* state, not source: a run whose runtime resolved to the repository root left
#: appended rows here (measured 2026-09-28; the rows carried timestamps from before this
#: session). A test that writes one of them dirties the working tree, so the suite is
#: guarded the same way the real Hermes home above is: the test that changed it fails,
#: naming itself, instead of silently contaminating the repository.
REPO_ROOT = Path(__file__).resolve().parents[1]
TRACKED_RUNTIME_ARTIFACTS: tuple[Path, ...] = (
    REPO_ROOT / "cu_runs.jsonl",
    REPO_ROOT / "events.db",
)


def _content_fingerprint(path: Path) -> tuple[int, str] | None:
    """Content only (no mtime): a rewrite that changes nothing must not fail a test."""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    return (len(data), hashlib.sha256(data).hexdigest())


@pytest.fixture(scope="session", autouse=True)
def _throwaway_hermes_home():
    """Point HERMES_HOME at a temp dir so default paths never hit the real install.

    ``GODSV_HOME`` is deliberately NOT set here: it is the stronger, explicit override for the
    runtime dir ("established override ... wins", ``registry.runtime_dir``), so a test that sets
    ``HERMES_HOME`` to its own tmp_path would silently write into this fixture's directory instead
    — that broke three capability tests when this guard first landed. Tests that want a runtime
    dir set ``GODSV_HOME`` themselves.
    """
    created: list[str] = []
    for name in ("HERMES_HOME",):
        if os.environ.get(name):
            continue
        os.environ[name] = tempfile.mkdtemp(prefix="godsv-tests-home-")
        created.append(name)
    yield
    for name in created:
        os.environ.pop(name, None)


@pytest.fixture(autouse=True)
def _real_config_untouched():
    """Fail — naming the test — when it rewrites the real install's config."""
    before = _fingerprint(REAL_CONFIG)
    yield
    after = _fingerprint(REAL_CONFIG)
    if after != before:
        pytest.fail(
            f"{REAL_CONFIG} changed during this test "
            f"(before={before}, after={after}) — tests must never write the real Hermes home. "
            "Pass an explicit config_file/tmp_path instead of relying on $HERMES_HOME."
        )


@pytest.fixture(autouse=True)
def _tracked_runtime_artifacts_untouched():
    """Fail — naming the test — when it writes a runtime artifact the repository tracks.

    Deliberately a *guard*, never a restore: writing the snapshot back would also undo a
    row a concurrent run of the owner legitimately appended while the test was running.
    The fix a failing test is pointed at is the same one the real-home guard above names:
    give the run its own runtime (``runtime=tmp_path``, ``SKALDIR_HOME=tmp_path``), never the
    repository root.
    """
    before = [_content_fingerprint(path) for path in TRACKED_RUNTIME_ARTIFACTS]
    yield
    changed = [
        path
        for path, old in zip(TRACKED_RUNTIME_ARTIFACTS, before, strict=True)
        if _content_fingerprint(path) != old
    ]
    if changed:
        names = ", ".join(str(path) for path in changed)
        pytest.fail(
            f"{names} changed during this test — tests must never write the tracked runtime "
            "artifacts (a run whose runtime resolved to the repository root). Pass an "
            "explicit runtime (runtime=tmp_path) or point SKALDIR_HOME at tmp_path."
        )
