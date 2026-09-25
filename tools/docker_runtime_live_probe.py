#!/usr/bin/env python
"""Live probe of the product's Docker runtime (VD-13) — real daemon, real digest, real refusals.

`godsvoice tools build` runs the full gated builder pipeline, which PUBLISHES a capability into the
live runtime; that write is not made here (it needs the owner's approval — finding AF-07). What this
probe does instead is exercise the layer the builder stands on, live against the real docker daemon:

  available() -> pull a real image -> resolve its digest -> pin it -> verify it
  -> refuse a wrong digest (DigestMismatch)
  -> refuse the forbidden configs (privileged, docker.sock mount, unpinned argv)

Run:  .venv/bin/python tools/docker_runtime_live_probe.py
Exits 0 only when the daemon path worked AND every refusal fired.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from godsv.docker_runtime import (  # noqa: E402
    DigestMismatch,
    DockerRuntimeManager,
    PolicyViolation,
    RunConfig,
)

IMAGE = "docker.io/library/alpine:latest"
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str) -> None:
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}", flush=True)


def main() -> int:
    manager = DockerRuntimeManager()
    ok, detail = manager.available()
    check("docker daemon reachable through the product layer", ok, detail)
    if not ok:
        return 1

    pulled = manager.pull(IMAGE, timeout=300.0)
    check("real image pull succeeded", bool(getattr(pulled, "ok", True)),
          f"pull -> {pulled!r}"[:120])

    digest = manager.resolve_digest(IMAGE)
    check("digest resolved for the pulled image", str(digest).startswith("sha256:"), f"digest={digest}")

    try:
        manager.verify_pinned(IMAGE, digest)
        pinned_ok, detail_pin = True, "the real digest was accepted"
    except DigestMismatch as exc:  # pragma: no cover - only on a broken daemon
        pinned_ok, detail_pin = False, f"rejected its own digest: {exc}"
    check("pinned digest verifies", pinned_ok, detail_pin)

    wrong = "sha256:" + "0" * 64
    try:
        manager.verify_pinned(IMAGE, wrong)
        refused_digest, detail_wrong = False, "a wrong digest was ACCEPTED"
    except DigestMismatch as exc:
        refused_digest, detail_wrong = True, f"refused: {str(exc)[:70]}"
    check("counter-case: a wrong digest is refused", refused_digest, detail_wrong)

    for name, kwargs in (
        ("privileged container", {"image": IMAGE, "privileged": True}),
        ("docker.sock mount", {"image": IMAGE, "mounts": [{"host_path": "/var/run/docker.sock"}]}),
    ):
        try:
            RunConfig(**kwargs)
            refused_config, detail_cfg = False, f"{name} was ACCEPTED"
        except PolicyViolation as exc:
            refused_config, detail_cfg = True, f"refused: {str(exc)[:60]}"
        check(f"counter-case: {name} is refused", refused_config, detail_cfg)

    try:
        RunConfig(image=IMAGE).docker_argv("run")
        refused_argv, detail_argv = False, "an unpinned tag produced a docker run line"
    except PolicyViolation as exc:
        refused_argv, detail_argv = True, f"refused: {str(exc)[:60]}"
    check("counter-case: an unpinned tag cannot build a run line", refused_argv, detail_argv)

    cfg = RunConfig(image=IMAGE, image_digest=digest, mounts=[])
    problems = cfg.validate()
    check("a pinned config validates clean", problems == [], f"validate -> {problems}")

    return 0 if all(step_ok for _, step_ok, _ in results) else 1


if __name__ == "__main__":
    code = main()
    failed = [n for n, step_ok, _ in results if not step_ok]
    print(f"\ndocker runtime live probe: {len(results) - len(failed)}/{len(results)} checks held"
          + (f" — failed: {failed}" if failed else ""))
    raise SystemExit(code)
