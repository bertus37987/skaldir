#!/usr/bin/env python
"""Live probe of the Docker-touching layers (EXPAND, runtime lifecycle, builder probe).

Companion to ``tests/test_containers_layer_integration.py``: the suite is the gate,
this probe is the hand-runnable evidence generator. It drives the REAL daemon
through the product's own runtime layer and prints what it measured, including
``docker ps`` by label before and after, the image digests used, and the defects
this wave found (the comma-joined label-filter defect it reported is fixed in the
tree now and passes below; an unfixed defect is reported as ``[KNOWN]`` — with
``--strict`` such a line fails the probe).

Claims probed:

  a) a one-shot ``run()`` leaves 0 containers behind (success AND failure path);
  b) a long-lived ``start()`` leaves nothing after teardown;
  c) our label-scoped cleanup removes ONLY our labelled containers — the foreign
     ``penpot-local`` stack of the shared daemon is counted before/after and must
     be unchanged, and a foreign-shaped scope is refused outright;
  d) an ``exec`` round trip returns real output;
  e) EXPAND: a capability derived from a pinned container is probed inside that
     container by the tool builder, registered with the digest, and swept by label.

Label discipline: everything created here carries ``godsv.owner=skaldir``,
``purpose=test`` and ``godsv.test_wave=gv-container-layer-probe``; every teardown
is a label-scoped sweep of the product. No name-based removal, no
``docker system prune``, no touch of a foreign container.

Run:
  .venv/bin/python tools/gv_container_layer_probe.py            # report + evidence
  .venv/bin/python tools/gv_container_layer_probe.py --strict   # defects fail too
  .venv/bin/python tools/gv_container_layer_probe.py --skip-build

Exit codes: 0 = every claim held (known defects listed), 1 = a claim failed,
3 = blocked (docker unreachable — printed as SKIP with the measured reason).
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from godsv import docker_runtime as dr  # noqa: E402

#: The small local image, addressed by digest — never by this mutable tag.
IMAGE = "alpine:3.20"
WAVE = "gv-container-layer-probe"
TEST_LABELS = {
    dr.OWNER_LABEL_KEY: dr.OWNER_LABEL_VALUE,
    "purpose": "test",
    "godsv.test_wave": WAVE,
}
#: ONE label (see the multi-label defect) — ours and unique to this probe.
SWEEP_SCOPE = {"godsv.test_wave": WAVE}
FOREIGN_FILTER = "label=com.docker.compose.project=penpot-local"

#: SIGTERM-aware long-lived entrypoint (busybox `sleep` as PID 1 ignores SIGTERM
#: and makes `docker stop` wait the full 10 s grace — measured).
LONG_LIVED_ARGV = ["sh", "-c", "trap 'exit 0' TERM; while :; do sleep 0.2; done"]

PROBE_ARGV = ["sh", "-c", "echo container-probe-ok; cat /etc/os-release"]

CHECKS: list[tuple[str, str, str]] = []  # (kind, name, detail): kind = PASS/FAIL/KNOWN


def _docker(*args: str, timeout: float = 120.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=timeout
    )


def check(name: str, ok: bool, detail: str, *, kind: str = "") -> bool:
    label = kind or ("PASS" if ok else "FAIL")
    CHECKS.append((label, name, detail))
    print(f"[{label}] {name}: {detail}", flush=True)
    return ok


def ps_by_label() -> str:
    """`docker ps` by label — the evidence the wave asks for, verbatim."""
    lines = ["  all containers : " + " ".join(_docker("ps", "-a", "--format", "{{.ID}}").stdout.split())]
    for tag, filt in (
        ("ours (godsv.owner=skaldir)", "label=godsv.owner=skaldir"),
        (f"this wave ({WAVE})", f"label=godsv.test_wave={WAVE}"),
        ("foreign (penpot-local)", "label=com.docker.compose.project=penpot-local"),
    ):
        out = _docker("ps", "-a", "--filter", filt, "--format", "{{.ID}}").stdout.split()
        lines.append(f"  {tag:26s}: {len(out)} {out}")
    return "\n".join(lines)


def foreign_ids() -> list[str]:
    return _docker(
        "ps", "-a", "--filter", FOREIGN_FILTER, "--format", "{{.ID}}"
    ).stdout.split()


def all_ids() -> set[str]:
    return set(_docker("ps", "-a", "--format", "{{.ID}}").stdout.split())


def gone(cid: str) -> bool:
    return _docker("inspect", "--format", "{{.Id}}", cid).returncode != 0


def wait_absent(cid: str, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while not gone(cid):
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.15)
    return True


def wait_wave_empty(timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        empty = not _docker(
            "ps", "-a", "--filter", f"label=godsv.test_wave={WAVE}", "--format", "{{.ID}}"
        ).stdout.split()
        if empty:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.15)


def account_for(result, cid: str) -> str:
    """The removal report for ``cid``: "removed", "race", or the raw failure."""
    payload = getattr(result, "payload", None) or {}
    removed = [str(item) for item in payload.get("removed", [])]
    if result.ok and (not removed or any(cid.startswith(item) or item.startswith(cid[:12]) for item in removed)):
        return "removed"
    errors = " ".join(str(item) for item in payload.get("errors", []))
    if result.failure_class == "cleanup_partial" and cid[:12] in errors:
        return "race"  # the `-d --rm` auto-removal race (known defect): effect verified
    return f"failed: {result.failure_class}: {result.detail[:120]}"


def run_probe(manager: dr.DockerRuntimeManager, digest: str, *, with_build: bool) -> int:
    before = all_ids()
    foreign_before = foreign_ids()

    # --- (a) one-shot runs are self-cleaning -------------------------------- #
    config = dr.RunConfig(
        image=IMAGE,
        image_digest=digest,
        labels=dict(TEST_LABELS),
        network="none",
        entrypoint_argv=["sh", "-c", "echo one-shot-ok; cat /etc/alpine-release"],
    )
    argv = config.docker_argv("run", one_shot=True)
    check("one-shot argv carries --rm", "--rm" in argv, " ".join(argv[:6]) + " …")
    result = manager.run(config, timeout=60.0)
    check(
        "one-shot run really ran (claim a)",
        bool(result.ok) and "one-shot-ok" in result.stdout,
        f"exit={result.exit_code} stdout={result.stdout.strip()[:60]!r}",
    )
    new_after_success = sorted(all_ids() - before)
    check(
        "successful one-shot leaves 0 containers",
        not new_after_success and wait_wave_empty(),
        f"new container ids: {new_after_success}",
    )

    failing = dr.RunConfig(
        image=IMAGE,
        image_digest=digest,
        labels=dict(TEST_LABELS),
        network="none",
        entrypoint_argv=["sh", "-c", "echo boom >&2; exit 3"],
    )
    failed_run = manager.run(failing, timeout=60.0)
    check(
        "failing one-shot is reported as a failure",
        failed_run.ok is False and failed_run.exit_code == 3,
        f"exit={failed_run.exit_code} stderr={failed_run.stderr.strip()[:40]!r}",
    )
    check(
        "failing one-shot leaves 0 containers",
        not (all_ids() - before) and wait_wave_empty(),
        f"new container ids: {sorted(all_ids() - before)}",
    )

    # --- (d) + (b) long-lived lifecycle ------------------------------------ #
    with tempfile.TemporaryDirectory(prefix="gv-probe-") as scratch:
        cid_file = Path(scratch) / "cid"
        long_config = dr.RunConfig(
            image=IMAGE,
            image_digest=digest,
            labels=dict(TEST_LABELS),
            network="none",
            entrypoint_argv=list(LONG_LIVED_ARGV),
        )
        started = manager.start(long_config, cid_file=cid_file, timeout=60.0)
        check(
            "long-lived start returns a real cid",
            bool(started.ok) and bool(started.cid),
            f"ok={started.ok} cid={started.cid[:12] if started.cid else ''!r}",
        )
        if started.ok and started.cid:
            cid = started.cid
            labels = json.loads(
                _docker("inspect", "--format", "{{json .Config.Labels}}", cid).stdout or "{}"
            )
            check(
                "long-lived container carries our labels",
                labels.get(dr.OWNER_LABEL_KEY) == dr.OWNER_LABEL_VALUE
                and labels.get("purpose") == "test"
                and labels.get("godsv.test_wave") == WAVE,
                json.dumps(labels, sort_keys=True),
            )
            network = _docker(
                "inspect", "--format", "{{.HostConfig.NetworkMode}}", cid
            ).stdout.strip()
            check("long-lived container runs with --network none", network == "none", network)

            release = manager.exec_in_container(
                cid_file, argv=["cat", "/etc/alpine-release"], timeout=30.0
            )
            check(
                "exec round trip returns real output (claim d)",
                bool(release.ok) and release.stdout.strip().startswith("3."),
                f"ok={release.ok} stdout={release.stdout.strip()!r}",
            )
            effect = manager.exec_in_container(
                cid_file,
                argv=["sh", "-c", "echo effect > /tmp/proof && cat /tmp/proof"],
                timeout=30.0,
            )
            check(
                "exec round trip verifies an in-container effect",
                bool(effect.ok) and effect.stdout.strip() == "effect",
                f"ok={effect.ok} stdout={effect.stdout.strip()!r}",
            )

            removed = manager.stop_and_remove(cid)
            verdict = account_for(removed, cid)
            check(
                "long-lived teardown leaves nothing (claim b)",
                wait_absent(cid) and wait_wave_empty(),
                f"container gone={gone(cid)} report={verdict}",
            )
            if verdict == "race":
                check(
                    "teardown report is a false cleanup_partial (auto-removal race)",
                    False,
                    f"{removed.detail[:110]!r} — the container is gone, the report is not",
                    kind="KNOWN",
                )

    # --- (c) label scope: only ours, refused for anything foreign ---------- #
    for scope in ({}, {"purpose": "test"}, {"com.docker.compose.project": "penpot-local"}):
        refused = manager.cleanup_by_labels(scope)
        check(
            f"cleanup refuses a non-godsv scope {scope}",
            refused.ok is False and refused.failure_class == "policy_violation",
            f"policy={refused.policy!r} detail={refused.detail[:80]!r}",
        )
    check(
        "foreign container count is unchanged (claim c)",
        foreign_ids() == foreign_before,
        f"before={len(foreign_before)} after={len(foreign_ids())} ids={foreign_before}",
    )

    two = dr.RunConfig(
        image=IMAGE,
        image_digest=digest,
        labels={**TEST_LABELS, "godsv.build_id": f"{WAVE}-a"},
        network="none",
        entrypoint_argv=list(LONG_LIVED_ARGV),
    )
    other = dr.RunConfig(
        image=IMAGE,
        image_digest=digest,
        labels={**TEST_LABELS, "godsv.build_id": f"{WAVE}-b"},
        network="none",
        entrypoint_argv=list(LONG_LIVED_ARGV),
    )
    started_a = manager.start(two, timeout=60.0)
    started_b = manager.start(other, timeout=60.0)
    if started_a.ok and started_b.ok:
        scoped = manager.cleanup_by_labels({"godsv.build_id": f"{WAVE}-a"})
        check(
            "single-label sweep removes exactly the scoped container",
            account_for(scoped, started_a.cid) in ("removed", "race") and wait_absent(started_a.cid),
            f"report={account_for(scoped, started_a.cid)} removed={scoped.payload.get('removed')}",
        )
        survive_labels = _docker(
            "inspect", "--format", "{{json .Config.Labels}}", started_b.cid
        ).stdout.strip()
        check(
            "the other build survives the sweep (claim c)",
            not gone(started_b.cid),
            f"surviving labels={survive_labels}",
        )
        swept = manager.cleanup_by_labels(SWEEP_SCOPE)
        check(
            "wave sweep leaves no labelled container behind",
            wait_wave_empty(),
            f"report={account_for(swept, started_b.cid)} remaining={sorted(all_ids() - before)}",
        )
    else:
        check("two labelled containers for the scope check", False, f"a={started_a.detail} b={started_b.detail}")

    # multi-label scope: the measured defect
    labelled = dr.RunConfig(
        image=IMAGE,
        image_digest=digest,
        labels={**TEST_LABELS, "godsv.build_id": f"{WAVE}-multi"},
        network="none",
        entrypoint_argv=list(LONG_LIVED_ARGV),
    )
    started_m = manager.start(labelled, timeout=60.0)
    if started_m.ok:
        cid_m = started_m.cid
        try:
            raw = _docker(
                "ps", "-a",
                "--filter", f"label=godsv.owner={dr.OWNER_LABEL_VALUE}",
                "--filter", f"label=godsv.build_id={WAVE}-multi",
                "--format", "{{.ID}}",
            ).stdout.split()
            listing = manager.list_by_labels(
                {dr.OWNER_LABEL_KEY: dr.OWNER_LABEL_VALUE, "godsv.build_id": f"{WAVE}-multi"}
            )
            found = [item for item in listing.payload.get("containers", []) if item.startswith(cid_m[:12])]
            check(
                f"multi-label scope lists our container (raw two-filter: {len(raw)} hit(s))",
                bool(found),
                f"list_by_labels filters={listing.payload.get('filters')!r} -> "
                f"{listing.payload.get('containers')} (one --filter per label)",
                kind="KNOWN" if not found else "",
            )
            scoped = manager.cleanup_owned({"godsv.build_id": f"{WAVE}-multi"})
            check(
                "the builder scope (owner + build id) really removes it",
                account_for(scoped, cid_m) in ("removed", "race") and wait_absent(cid_m),
                f"report={account_for(scoped, cid_m)} removed={scoped.payload.get('removed')}",
            )
        finally:
            swept = manager.cleanup_by_labels({"godsv.build_id": f"{WAVE}-multi"})
            wait_absent(cid_m)

    # --- (e) EXPAND: capability from a pinned container -------------------- #
    if with_build:
        check_expand(manager, digest)

    # --- closing evidence -------------------------------------------------- #
    check(
        "no container of this wave is left (independent docker query)",
        wait_wave_empty(),
        f"remaining wave ids: {sorted(_docker('ps', '-a', '--filter', f'label=godsv.test_wave={WAVE}', '--format', '{{.ID}}').stdout.split())}",
    )
    check(
        "the foreign stack survived the whole probe",
        foreign_ids() == foreign_before,
        f"foreign ids unchanged: {foreign_ids()}",
    )
    return 0


def check_expand(manager: dr.DockerRuntimeManager, digest: str) -> None:
    """(e) a capability derived from the container, built, registered, swept."""
    from godsv import toolbuilder

    source = (
        '"""Expand probe: echo a value back through a pinned container image."""\n'
        "import json\n\n\n"
        'def main(text: str = "expand") -> dict:\n'
        '    return {"echoed": text}\n\n\n'
        'if __name__ == "__main__":  # pragma: no cover\n'
        "    print(json.dumps(main()))\n"
    )
    with tempfile.TemporaryDirectory(prefix="gv-probe-build-") as scratch:
        runtime = Path(scratch)
        derivation = manager.run(
            dr.RunConfig(
                image=IMAGE,
                image_digest=digest,
                labels=dict(TEST_LABELS),
                network="none",
                entrypoint_argv=list(PROBE_ARGV),
            ),
            timeout=60.0,
        )
        check(
            "capability evidence is derived from inside the container",
            bool(derivation.ok)
            and "container-probe-ok" in derivation.stdout
            and "Alpine Linux" in derivation.stdout,
            f"exit={derivation.exit_code} stdout={derivation.stdout.strip()[:60]!r}",
        )
        request = toolbuilder.ToolBuildRequest(
            capability="Echo a value through a pinned container image",
            reason="gv_container_layer_probe: capability derived from a container",
            name="container-echo",
            kind="cli",
            module="container_echo",
            source_code=source,
            entrypoint="main",
            sample_input={"text": "expand"},
            runtime="docker",
            image=IMAGE,
            image_digest=digest,
            healthcheck={"argv": list(PROBE_ARGV)},
        )
        result = toolbuilder.ToolBuilder(runtime).build(request)
        probe = (result.get("container_probe") or {}).get("probe") or {}
        check(
            "tool builder probes the capability inside the pinned container (claim e)",
            bool(result.get("ok")) and bool(probe.get("ok")) and "Alpine Linux" in probe.get("stdout", ""),
            f"ok={result.get('ok')} probe_ok={probe.get('ok')} stdout={probe.get('stdout', '')[:40]!r}",
        )
        teardown = (result.get("container_probe") or {}).get("teardown") or {}
        check(
            "builder sweep is scoped to owner + build label",
            teardown.get("payload", {}).get("scope")
            == f"{dr.OWNER_LABEL_KEY}={dr.OWNER_LABEL_VALUE},godsv.build_id={result.get('build_id')}",
            f"scope={teardown.get('payload', {}).get('scope')!r} removed={teardown.get('payload', {}).get('removed')}",
        )
        check(
            "the registered manifest pins the digest",
            (runtime / "generated_tools" / "container_echo" / "manifest.yaml").exists()
            and digest in (runtime / "generated_tools" / "container_echo" / "manifest.yaml").read_text(),
            f"manifest={runtime / 'generated_tools' / 'container_echo' / 'manifest.yaml'}",
        )
        check(
            "the EXPAND build left no container behind",
            wait_wave_empty(),
            f"probe failures={result.get('error', '')[:60]!r}",
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--strict", action="store_true", help="known defects fail the probe")
    parser.add_argument("--skip-build", action="store_true", help="skip the tool-builder build")
    args = parser.parse_args()

    binary = shutil.which("docker")
    if not binary:
        print("[SKIP] docker unreachable: no docker binary on PATH")
        return 3
    probe = _docker("version", "--format", "{{.Server.Version}}", timeout=20.0)
    if probe.returncode != 0:
        reason = (probe.stderr or probe.stdout).strip().splitlines()
        print(f"[SKIP] docker unreachable: {reason[0] if reason else 'no daemon answered'}")
        return 3

    manager = dr.DockerRuntimeManager()
    ok, detail = manager.available()
    check("product daemon probe", ok, detail)
    if not ok:
        print(f"[SKIP] docker unreachable through the product layer: {detail}")
        return 3

    digest = manager.resolve_digest(IMAGE)
    if not digest:
        pulled = manager.pull(IMAGE, timeout=300.0)
        if not pulled.ok:
            print(
                f"[SKIP] {IMAGE} is neither local nor pullable "
                f"({pulled.failure_class}: {pulled.detail[:120]})"
            )
            return 3
        digest = manager.resolve_digest(IMAGE)
    repo_digest = _docker(
        "image", "inspect", "--format", "{{index .RepoDigests 0}}", IMAGE
    ).stdout.strip()
    print(f"\nimage        : {IMAGE}")
    print(f"image id     : {digest}")
    print(f"repo digest  : {repo_digest}\n")
    print("docker ps by label BEFORE\n" + ps_by_label() + "\n")

    # baseline: this probe sweeps its own leftovers by label (never foreign ones)
    baseline = manager.cleanup_by_labels(SWEEP_SCOPE)
    check(
        "baseline sweep of this wave's scope",
        baseline.ok and wait_wave_empty(),
        baseline.detail[:100],
    )

    run_probe(manager, digest, with_build=not args.skip_build)

    print("\ndocker ps by label AFTER\n" + ps_by_label())

    failed = [name for kind, name, _ in CHECKS if kind == "FAIL"]
    known = [(name, detail) for kind, name, detail in CHECKS if kind == "KNOWN"]
    print(
        f"\ngv container layer probe: {len(CHECKS) - len(failed) - len(known)}/{len(CHECKS)} checks held"
        + (f" — FAILED: {failed}" if failed else "")
    )
    for name, detail in known:
        print(f"  [KNOWN DEFECT] {name}: {detail}")
    if failed or (known and args.strict):
        return 1
    return 0


if __name__ == "__main__":
    code = main()
    if code == 3:
        raise SystemExit(3)
    print("probe result:", "PASS" if code == 0 else "FAIL")
    raise SystemExit(code)
