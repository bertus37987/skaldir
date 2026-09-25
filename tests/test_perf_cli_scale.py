"""The capability-catalogue path scales — measured, not asserted by adjective.

`load_tool_entries` (per-directory ``manifest.yaml``), `ToolStore.all()` (sqlite)
and `registry_snapshot` are the paths a large catalogue goes through. The test
builds a synthetic runtime at two sizes, warms both, and pins:

* an absolute bound that catches a real blow-up (a 2000-entry catalogue that
  needs minutes), and
* a growth ratio with a generous ceiling — 4x the entries must not cost more
  than 4x the time (linear); quadratic growth is caught, a busy machine is not.

Measured values and the full curve (0 → 10 000 entries) are in
``MVP/19-performance.md``.
"""

from __future__ import annotations

import statistics
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

SMALL = 400
LARGE = 1200
RUNS = 3
#: Absolute ceiling for the largest catalogue (linear would be ~1/5 of this).
ABSOLUTE_BUDGET_S = 15.0
#: time(LARGE)/time(SMALL) ceiling. Linear = 3.0; quadratic = 9.0.
GROWTH_CEILING = 4.5


def _manifest(index: int) -> str:
    return (
        f"name: perf-tool-{index:05d}\n"
        "type: cli\n"
        "version: 1.0.0\n"
        "status: ready\n"
        "scope: global\n"
        "project_id: perf\n"
        f"purpose: synthetic capability {index}\n"
        "validation:\n"
        "  status: validated\n"
        "network:\n"
        "  allowed_domains:\n"
        "    - example.com\n"
        "filesystem:\n"
        "  access: read\n"
        "required_secrets: []\n"
    )


def _build(runtime: Path, size: int) -> Path:
    from skaldir.tools import ToolSpec, ToolStore

    tools_root = runtime / "generated_tools"
    tools_root.mkdir(parents=True, exist_ok=True)
    for index in range(size):
        tool_dir = tools_root / f"perf-tool-{index:05d}"
        tool_dir.mkdir()
        (tool_dir / "manifest.yaml").write_text(_manifest(index), encoding="utf-8")
    store = ToolStore(runtime / "tools.db")
    try:
        for index in range(size):
            store.upsert(
                ToolSpec(
                    name=f"perf-tool-{index:05d}",
                    title=f"Perf tool {index}",
                    description="scaling probe",
                    kind="cli",
                    module="perf",
                    entrypoint="run",
                    status="approved",
                )
            )
    finally:
        store.close()
    return runtime


def _catalogue_read(runtime: Path) -> tuple[int, int]:
    """One full read of the catalogue: manifests + sqlite specs."""

    from skaldir.capability import load_tool_entries
    from skaldir.registry import registry_snapshot, resolve_registry
    from skaldir.tools import ToolStore

    entries = load_tool_entries(runtime)
    context = resolve_registry(runtime=runtime)
    snapshot = registry_snapshot(context)
    store = ToolStore(context.tools_db)
    try:
        specs = store.all()
    finally:
        store.close()
    assert snapshot["generated_tools"] is not None
    return len(entries), len(specs)


def _timed(runtime: Path) -> float:
    """Median of RUNS full reads, in seconds."""

    samples = []
    for _ in range(RUNS):
        started = time.perf_counter()
        entries, specs = _catalogue_read(runtime)
        samples.append(time.perf_counter() - started)
        assert entries > 0 and specs > 0
    return statistics.median(samples)


@pytest.fixture(scope="module")
def catalogues(tmp_path_factory) -> dict[int, Path]:
    root = tmp_path_factory.mktemp("perf-scale")
    return {size: _build(root / f"rt-{size}", size) for size in (SMALL, LARGE)}


def test_catalogue_read_counts_are_correct(catalogues):
    for size, runtime in catalogues.items():
        entries, specs = _catalogue_read(runtime)
        assert entries == size and specs == size


def test_catalogue_read_stays_linear(catalogues):
    small = _timed(catalogues[SMALL])
    large = _timed(catalogues[LARGE])
    ratio = large / small if small > 0 else 0.0
    assert large <= ABSOLUTE_BUDGET_S, (
        f"{LARGE} catalogue entries took {large:.2f} s — the registry path blew up"
    )
    assert ratio <= GROWTH_CEILING, (
        f"{LARGE}/{SMALL} entries cost x{ratio:.2f} time "
        f"({small:.3f} s -> {large:.3f} s) — superlinear registry path"
    )
