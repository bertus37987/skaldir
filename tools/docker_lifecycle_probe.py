#!/usr/bin/env python
"""Real lifecycle probe for the container path (S09) — crash, duplicate request, cleanup.

The criterion asks that a container/worker crash, a DUPLICATE build request, a lost event, a
revocation and an abort leave consistent states and no unintended side effects. This probe creates
those situations against the real docker daemon through the product's own runtime layer:

  start -> kill the container out of band (the "crash") -> the product still reports it consistently
  -> stop_and_remove twice (idempotent, never silent) -> duplicate start must not create a second
  container -> cleanup_by_labels({}) must refuse, not clean blindly -> no leftovers.

Run:  .venv/bin/python tools/docker_lifecycle_probe.py
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from godsv.docker_runtime import DockerRuntimeManager, RunConfig  # noqa: E402

IMAGE = "docker.io/library/alpine:latest"
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str) -> None:
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}", flush=True)


def docker(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=120)


def containers_named(name: str) -> list[str]:
    out = docker("ps", "-a", "--filter", f"name=^{name}$", "--format", "{{.ID}}").stdout
    return [line for line in out.split() if line.strip()]


def main() -> int:
    manager = DockerRuntimeManager()
    ok, detail = manager.available()
    check("docker daemon reachable", ok, detail)
    if not ok:
        return 1
    digest = manager.resolve_digest(IMAGE)
    name = f"godsv-lifecycle-{int(time.time()) % 1000000}"
    config = RunConfig(image=IMAGE, image_digest=digest, name=name,
                       entrypoint_argv=["sleep", "120"], labels={"godsv": "lifecycle-probe"})

    started = manager.start(config)
    check("container started through the product layer", bool(getattr(started, "ok", False)),
          f"start -> {str(started)[:100]}")

    # the "crash": killed out of band, exactly like a container dying on its own
    killed = docker("kill", name)
    check("container killed out of band (the crash)", killed.returncode == 0, f"docker kill rc={killed.returncode}")
    # A killed container still EXISTS (stopped, not removed) - so the honest check reads its state,
    # not its existence: `docker inspect` succeeds for a corpse.
    state = docker("inspect", "--format", "{{.State.Running}}", name)
    check("independent observation: the container is no longer running",
          state.returncode == 0 and state.stdout.strip() == "false",
          f"docker inspect .State.Running -> {state.stdout.strip()!r} (rc={state.returncode})")

    first = manager.stop_and_remove(name)
    check("cleanup after the crash is reported consistently", bool(getattr(first, "ok", False)),
          f"stop_and_remove -> {str(first)[:90]}")
    second = manager.stop_and_remove(name)
    check("cleanup is idempotent (second call does not lie or raise)", bool(getattr(second, "ok", False)),
          f"stop_and_remove #2 -> {str(second)[:90]}")

    # duplicate request: the same config twice must not leave two containers behind
    a = manager.start(config)
    b = manager.start(config)
    live = containers_named(name)
    check("duplicate start leaves exactly ONE container (no silent duplicate)",
          len(live) == 1, f"first={getattr(a, 'ok', None)} second={getattr(b, 'ok', None)} containers={len(live)}")

    blind = manager.cleanup_by_labels({})
    check("counter-case: cleanup WITHOUT labels is refused (no blind removal)",
          getattr(blind, "ok", True) is False, f"cleanup_by_labels({{}}) -> {str(blind)[:90]}")

    final = manager.stop_and_remove(name)
    leftovers = containers_named(name)
    check("no leftovers after the run (no unintended side effects)",
          bool(getattr(final, "ok", False)) and leftovers == [], f"leftover containers: {leftovers}")

    return 0 if all(step_ok for _, step_ok, _ in results) else 1


if __name__ == "__main__":
    code = main()
    failed = [n for n, step_ok, _ in results if not step_ok]
    print(f"\ndocker lifecycle probe: {len(results) - len(failed)}/{len(results)} checks held"
          + (f" — failed: {failed}" if failed else ""))
    raise SystemExit(code)
