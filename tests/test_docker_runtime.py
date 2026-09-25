"""B6 — DockerRuntimeManager + Manifest v2 + Executor-Registry (§2/§3 of the
docker-tool-builder plan).

Rot-first: this suite pins the capability chain

    manifest v2 (toolbuilder) → DockerRuntimeManager (docker_runtime.py) →
    shared execution boundary (execution.py ContainerExecutor seam) →
    buildflow state machine (docker stages in ONE state machine)

Tiers:

* pure/policy tests — always run in CI (no docker needed);
* daemon tests (``docker_daemon`` marker) — run when a daemon answers;
* live E2E with image pulls — opt-in via ``--live-docker`` (network + disk).

Every live expectation is probed for availability first — the suite never
fabricates a pass it did not measure.
"""

from __future__ import annotations

import os
import shutil
import socket as socket_mod
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from skaldir import buildflow as bf
from skaldir import docker_runtime as dr
from skaldir import execution, toolbuilder

HAVE_DOCKER = shutil.which("docker") is not None


def _daemon_up() -> bool:
    if not HAVE_DOCKER:
        return False
    ok, _ = dr.DockerRuntimeManager().available()
    return ok


def _live_opt_in() -> bool:
    """Opt-in via env (pyproject addopts would need a registered plugin hook —
    the env switch is the cheap, dependency-free gate)."""
    return os.environ.get("GV_B6_LIVE_DOCKER") == "1"


docker_daemon = pytest.mark.skipif(
    not _daemon_up(), reason="docker daemon not answering"
)
live_docker = pytest.mark.skipif(
    not _daemon_up() or not _live_opt_in(), reason="live docker (pulls images)"
)


# --------------------------------------------------------------------------- #
# manifest v2 (toolbuilder) — cheap, no docker
# --------------------------------------------------------------------------- #


class TestManifestV2Schema:
    def test_v2_field_names_are_pinned(self):
        assert toolbuilder.manifest_v2_fields() == dict(
            runtime="docker",
            image_digest="",
            entrypoint_argv=[],
            mounts=[],
            resource_limits={},
            require_bind_ro=True,
            healthcheck={},
        )

    def test_digest_is_required_and_pinshaped(self):
        for bad in ("", "v1.0", "sha256:short"):
            with pytest.raises(ValueError):
                toolbuilder.validate_manifest_v2(
                    {"runtime": "docker", "image_digest": bad}
                )

    def test_mount_must_have_both_paths(self):
        with pytest.raises(ValueError):
            toolbuilder.validate_manifest_v2(
                {
                    "runtime": "docker",
                    "image_digest": "sha256:" + "a" * 64,
                    "mounts": [{"container_path": "/x"}],
                }
            )

    def test_mount_inside_workdir_is_clean_outside_flagged(self, tmp_path):
        workdir = tmp_path / "tool"
        workdir.mkdir()
        mounts = [
            {"host_path": str(workdir / "f"), "container_path": "/opt/f", "readonly": True},
        ]
        assert toolbuilder.validate_build_mounts(mounts, workdir=workdir) == []
        outside = tmp_path / "outside"
        outside.mkdir()
        assert toolbuilder.validate_build_mounts(
            [{"host_path": str(outside), "container_path": "/x", "readonly": True}],
            workdir=workdir,
        ) == ["outside-workdir"]
        assert toolbuilder.validate_build_mounts([], workdir=workdir) == []


# --------------------------------------------------------------------------- #
# DockerRuntimeManager — policy tests (no docker)
# --------------------------------------------------------------------------- #


class TestSafetyPolicy:
    def test_protected_hostpaths_never_mounted(self, tmp_path):
        for target in ("/", "/etc", "/var/run/docker.sock"):
            problems = dr.validate_mounts(
                [{"host_path": target, "container_path": "/x", "readonly": True}],
                workdir=tmp_path,
            )
            assert problems, f"expected mount refusal for {target}"
            assert any(
                p == "protected-host-path" for p in problems
            ), problems

    def test_privileged_is_rejected_at_the_config_layer(self):
        with pytest.raises(dr.PolicyViolation):
            dr.RunConfig(image_digest="sha256:" + "a" * 64, privileged=True)

    def test_docker_socket_mount_is_rejected(self):
        with pytest.raises(dr.PolicyViolation):
            dr.RunConfig(
                image_digest="sha256:" + "a" * 64,
                mounts=[
                    {
                        "host_path": "/var/run/docker.sock",
                        "container_path": "/docker.sock",
                        "readonly": True,
                    }
                ],
            )

    def test_network_defaults_to_none(self):
        rc = dr.RunConfig(image_digest="sha256:" + "a" * 64)
        assert rc.network == "none"
        assert rc.validate() == []

    def test_explicit_port_bindings_are_allowed_but_static(self):
        rc = dr.RunConfig(
            image_digest="sha256:" + "a" * 64, port_bindings={8080: 18080}
        )
        assert rc.validate() == []
        assert rc.port_bindings == {8080: 18080}

    def test_resource_limits_shape_cpu_ram(self):
        rc = dr.RunConfig(
            image_digest="sha256:" + "a" * 64, resource_limits={"cpu": 1.0, "ram": "256m"}
        )
        assert rc.validate() == []
        argv = rc.docker_argv("run")
        assert any(arg.startswith("--cpus=") for arg in argv)
        assert "--memory=256m" in argv


class TestErrorTaxonomy:
    def test_image_missing(self):
        err = dr.classify_pull_error(
            dr.CmdResult(125, "", "manifest unknown: image not known"), "alpine:9999"
        )
        assert err.failure_class == "image_missing"
        assert "alpine:9999" in err.payload["image"]

    def test_arch_mismatch(self):
        err = dr.classify_pull_error(
            dr.CmdResult(
                125, "", "no matching manifest for linux/arm64 in the manifest list"
            ),
            "alpine:3.20",
        )
        assert err.failure_class == "arch_mismatch"

    def test_port_conflict(self):
        err = dr.classify_start_error(
            dr.CmdResult(
                125, "", "Error starting userland proxy: address already in use"
            ),
            "x",
        )
        assert err.failure_class == "port_conflict"

    def test_quota_limit(self):
        err = dr.classify_pull_error(
            dr.CmdResult(
                429, "", "toomanyrequests: You have reached your pull rate limit"
            ),
            "x",
        )
        assert err.failure_class == "quota_limit"

    def test_quota_limit_live_shape(self):
        """The LIVE Hub answer (measured 2026-09-18) is a rate limit, not a generic error.

        Docker Hub answers an anonymous pull of an image that DOES exist with
        ``…unexpected status from HEAD request to …/manifests/3.20: 429 Too Many
        Requests`` — no ``toomanyrequests`` substring and no ``pull rate limit``
        wording, so it used to be classified ``docker_error`` and a rate-limited
        registry read as a broken build. It must be ``quota_limit``.
        """
        live = (
            "Error response from daemon: unknown: failed to resolve reference "
            '"docker.io/library/alpine:3.20": unexpected status from HEAD request '
            "to https://registry-1.docker.io/v2/library/alpine/manifests/3.20: "
            "429 Too Many Requests"
        )
        err = dr.classify_pull_error(dr.CmdResult(1, "", live), "alpine:3.20")

        assert err.failure_class == "quota_limit", err
        assert live[:60] in err.detail  # the daemon's own (English) text is kept

    def test_pull_carries_the_quota_limit_class_into_the_result(self):
        """``pull()`` reports the class (no raise) — the build reads it, not prose."""
        manager = dr.DockerRuntimeManager(docker_bin="docker")
        live = (
            "unexpected status from HEAD request to "
            "https://registry-1.docker.io/v2/library/alpine/manifests/3.20: "
            "429 Too Many Requests"
        )
        manager._invoke_raw = lambda argv, **_kw: dr.CmdResult(1, "", live)  # noqa: SLF001

        pulled = manager.pull("alpine:3.20")

        assert pulled.ok is False
        assert pulled.failure_class == "quota_limit", pulled
        assert pulled.detail

    def test_unknown_failure_carries_the_raw_detail(self):
        err = dr.classify_pull_error(
            dr.CmdResult(125, "", "weird daemon error"), "img"
        )
        assert err.failure_class
        assert "weird daemon error" in err.detail


# --------------------------------------------------------------------------- #
# pinned fast suite (no docker needed)
# --------------------------------------------------------------------------- #


def test_docker_stub_pull_and_pin(monkeypatch):
    """The pinner drives the docker CLI — argv is data, a stub answers."""
    recorded: list[list[str]] = []

    def stub(cmd, **kw):
        recorded.append(list(cmd))
        if "pull" in cmd or "inspect" in cmd:
            return dr.CmdResult(0, f"sha256:{'b' * 64}\n", "")
        return dr.CmdResult(0, "{}", "")

    mgr = dr.DockerRuntimeManager(docker_bin="docker")
    monkeypatch.setattr(mgr, "_invoke_raw", stub)
    digest = mgr.pull_and_pin("alpine:3.20")
    assert digest.startswith("sha256:")
    # pin verification goes through inspect, not the mutable tag
    assert any("inspect" in cmd for cmd in recorded)


def test_pin_verification_rejects_mismatch():
    with pytest.raises(dr.DigestMismatch):
        dr.verify_pinned_digest(
            actual=f"sha256:{'c' * 64}",
            expected=f"sha256:{'d' * 64}",
            image="alpine:3.20",
        )


# --------------------------------------------------------------------------- #
# buildflow extension — one state machine, docker-aware
# --------------------------------------------------------------------------- #


class TestBuildflowContainerStages:
    def test_stage_phase_stays_complete_for_real_toolbuilder_stages(self):
        for stage in toolbuilder.STAGES:
            assert stage in bf.STAGE_PHASE

    def test_docker_runtime_record_reaches_the_build_record(self, tmp_path):
        flow = bf.BuildFlowStore(tmp_path / "buildflow.db")
        record, _created = flow.admit(need="wrap a container cli", runtime="docker")
        assert record.runtime == "docker"
        assert flow.get(record.build_id).runtime == "docker"

    def test_publish_path_is_unchanged_outbox_only(self):
        # the outbox hook stays the publish trigger — no new gate before publish
        assert set(bf.ALLOWED_TRANSITIONS["validate"]) == {"publish", "adapter"}


# --------------------------------------------------------------------------- #
# executor registry seam (execution.py)
# --------------------------------------------------------------------------- #


def test_container_executor_replaces_the_null_executor():
    mgr = dr.DockerRuntimeManager()
    execution.register_container_executor(mgr)
    try:
        assert execution.get_container_executor() is mgr
    finally:
        execution.register_container_executor(execution.NullExecutor())
    # and the seam still fails closed when nothing is registered
    result = execution.get_container_executor().run_job(
        object(), execution.RunPolicy()
    )
    assert result.ok is False and result.status == "not-certifiable"


# --------------------------------------------------------------------------- #
# the manager's own executor seam — run_job(), no daemon needed
# --------------------------------------------------------------------------- #

JOB_DIGEST = "sha256:" + "c" * 64

#: The live test's own build label — the scope it sweeps and never exceeds.
LIVE_JOB_LABELS = {"godsv.build_id": "b6-run-job-live"}

#: The owner's foreign compose stack shares this daemon (never touched, counted).
FOREIGN_FILTER = "label=com.docker.compose.project=penpot-local"


def _raw_ids(*filters: str) -> list[str]:
    """Independent docker observation — the raw CLI, not the code under test."""
    out = subprocess.run(
        ["docker", "ps", "-a", *filters, "--format", "{{.ID}}"],
        capture_output=True, text=True, timeout=60.0,
    )
    assert out.returncode == 0, (out.stderr or out.stdout)[:200]
    return [line for line in out.stdout.split() if line]


class TestRunJobSeam:
    """``DockerRuntimeManager`` is the boundary's ``ContainerExecutor`` — for real.

    The class used to *document* ``available() + run_job()`` while implementing no
    ``run_job``: registering it on ``godsv.execution`` raised ``AttributeError``
    instead of returning a ``RunResult``. These tests pin the method: one job = ONE
    one-shot ``--rm`` run, label-scoped, answered as the boundary's ``RunResult``,
    and a refusal that is ``not-certifiable`` instead of an exception.
    """

    @staticmethod
    def _manager(
        monkeypatch, *, stdout: str = "job output", exit_code: int = 0, stderr: str = ""
    ) -> SimpleNamespace:
        """A manager whose daemon probe answers and whose docker calls are recorded."""
        manager = dr.DockerRuntimeManager(docker_bin="docker")
        calls: list[list[str]] = []

        def stub(argv, **_kw):
            calls.append(list(argv))
            return dr.CmdResult(exit_code, stdout, stderr, timeout=exit_code < 0)

        monkeypatch.setattr(manager, "_invoke_raw", stub)
        monkeypatch.setattr(manager, "available", lambda: (True, "Server Version 29.6.1"))
        return SimpleNamespace(manager=manager, calls=calls)

    def test_the_protocol_surface_exists(self):
        manager = dr.DockerRuntimeManager()
        assert callable(getattr(manager, "available", None))
        assert callable(getattr(manager, "run_job", None)), (
            "docker_runtime documents the ContainerExecutor seam — it must exist"
        )

    def test_a_job_the_runtime_cannot_map_is_refused_without_calling_docker(self):
        manager = dr.DockerRuntimeManager()
        manager._invoke_raw = lambda *_a, **_k: pytest.fail(  # noqa: SLF001
            "an unmappable job must not reach docker"
        )
        for job in (object(), None, [], 42):
            result = manager.run_job(job, execution.RunPolicy())
            assert isinstance(result, execution.RunResult)
            assert result.ok is False, result
            assert result.status == execution.STATUS_NOT_CERTIFIABLE, result
            assert result.sandboxed is False
            assert "refuses a job" in result.detail or "no job to run" in result.detail
        assert "object" in manager.run_job(object()).detail

    def test_a_job_without_a_pinned_digest_is_refused(self, monkeypatch):
        rec = self._manager(monkeypatch)
        result = rec.manager.run_job({"image": "alpine:3.20"}, execution.RunPolicy())
        assert result.ok is False and result.status == execution.STATUS_NOT_CERTIFIABLE
        assert "pinned" in result.detail, result.detail
        assert rec.calls == [], "a refused job must not start a container"

    def test_one_job_is_one_one_shot_rm_run_with_our_labels(self, monkeypatch):
        rec = self._manager(monkeypatch)
        job = {
            "image": "alpine:3.20",
            "image_digest": JOB_DIGEST,
            "argv": ["echo", "hi"],
            "labels": {dr.OWNER_LABEL_KEY: dr.OWNER_LABEL_VALUE, "godsv.build_id": "b-7"},
        }
        result = rec.manager.run_job(job, execution.RunPolicy())

        assert result.ok is True and result.status == execution.STATUS_OK, result
        assert result.exit_code == 0 and result.sandboxed is True
        assert result.stdout == "job output"
        assert len(rec.calls) == 1, rec.calls
        argv = rec.calls[0]
        assert argv[:3] == ["docker", "run", "--rm"], argv
        assert argv[argv.index("--network") + 1] == "none", argv
        labels = [argv[i + 1] for i, item in enumerate(argv) if item == "--label"]
        assert f"{dr.OWNER_LABEL_KEY}={dr.OWNER_LABEL_VALUE}" in labels, labels
        assert "godsv.build_id=b-7" in labels, labels
        assert labels.count(f"{dr.OWNER_LABEL_KEY}={dr.OWNER_LABEL_VALUE}") == 1, labels
        assert argv[-3:] == [JOB_DIGEST, "echo", "hi"], argv

    def test_a_run_config_job_is_used_as_given(self, monkeypatch, tmp_path):
        rec = self._manager(monkeypatch)
        config = dr.RunConfig(
            image_digest=JOB_DIGEST, entrypoint_argv=["true"], workdir=tmp_path,
            labels={"godsv.build_id": "b-7"},
        )
        result = rec.manager.run_job(config, execution.RunPolicy())
        assert result.ok is True and result.status == execution.STATUS_OK, result
        assert rec.calls[0][:3] == ["docker", "run", "--rm"], rec.calls[0]

    def test_the_mount_allowlist_refuses_before_docker_runs(self, monkeypatch, tmp_path):
        rec = self._manager(monkeypatch)
        job = {
            "image": "alpine:3.20",
            "image_digest": JOB_DIGEST,
            "argv": ["true"],
            "workdir": str(tmp_path),
            "mounts": [{"host_path": str(Path.home()), "container_path": "/data"}],
        }
        result = rec.manager.run_job(job, execution.RunPolicy())

        assert result.ok is False and result.status == execution.STATUS_NOT_CERTIFIABLE
        assert "protected-host-path" in result.detail, result.detail
        assert rec.calls == [], "a policy-refused job must not start a container"

    def test_the_boundary_grants_the_network_only_through_the_policy(self, monkeypatch):
        job = {"image": "alpine:3.20", "image_digest": JOB_DIGEST, "argv": ["true"]}
        strict = self._manager(monkeypatch)
        strict.manager.run_job(job, execution.RunPolicy())
        assert strict.calls[0][strict.calls[0].index("--network") + 1] == "none"

        permitted = self._manager(monkeypatch)
        result = permitted.manager.run_job(job, execution.RunPolicy(allow_network=True))
        assert result.ok is True, result
        assert permitted.calls[0][permitted.calls[0].index("--network") + 1] == "bridge"

    def test_a_workload_that_exits_non_zero_is_a_failure(self, monkeypatch):
        """The container ran: that is a failed run, not an uncertifiable one."""
        rec = self._manager(monkeypatch, stdout="boom", exit_code=3)
        result = rec.manager.run_job(
            {"image": "alpine", "image_digest": JOB_DIGEST, "argv": ["false"]},
            execution.RunPolicy(),
        )
        assert result.ok is False and result.status == execution.STATUS_FAILED, result
        assert result.exit_code == 3 and result.sandboxed is True

    def test_a_docker_level_failure_is_not_certifiable(self, monkeypatch):
        """docker's own exit code (125) means the container never ran — no pass, no 'failed'."""
        rec = self._manager(
            monkeypatch, exit_code=125,
            stderr="docker: Error response from daemon: No such image: alpine:9999.",
        )
        result = rec.manager.run_job(
            {"image": "alpine:9999", "image_digest": JOB_DIGEST, "argv": ["true"]},
            execution.RunPolicy(),
        )
        assert result.ok is False and result.status == execution.STATUS_NOT_CERTIFIABLE, result
        assert "never ran" in result.detail and "image_missing" in result.detail, result.detail

    def test_the_output_cap_of_the_policy_is_applied(self, monkeypatch):
        rec = self._manager(monkeypatch, stdout="x" * 500)
        result = rec.manager.run_job(
            {"image": "alpine", "image_digest": JOB_DIGEST, "argv": ["true"]},
            execution.RunPolicy(max_output_bytes=64),
        )
        assert "truncated" in result.stdout, result.stdout

    def test_a_timed_out_job_sweeps_its_own_labels(self, monkeypatch):
        """A killed ``docker run`` leaves its container running — sweep by label."""
        manager = dr.DockerRuntimeManager(docker_bin="docker")
        calls: list[list[str]] = []

        def stub(argv, **_kw):
            calls.append(list(argv))
            if argv[:2] == ["docker", "run"]:
                return dr.CmdResult(-9, "", "", timeout=True)
            return dr.CmdResult(0, "0ur00000009", "")

        monkeypatch.setattr(manager, "_invoke_raw", stub)
        monkeypatch.setattr(manager, "available", lambda: (True, "Server Version 29.6.1"))
        result = manager.run_job(
            {
                "image": "alpine",
                "image_digest": JOB_DIGEST,
                "argv": ["sleep", "600"],
                "labels": {"godsv.build_id": "b-timeout"},
            },
            execution.RunPolicy(),
        )

        assert result.ok is False and result.status == execution.STATUS_NOT_CERTIFIABLE, result
        assert "timeout" in result.detail, result.detail
        assert "timeout sweep removed 1 container(s)" in result.detail, result.detail
        listing = [call for call in calls if call[:2] == ["docker", "ps"]][0]
        values = [
            listing[index + 1] for index, item in enumerate(listing) if item == "--filter"
        ]
        assert values == [
            f"label={dr.OWNER_LABEL_KEY}={dr.OWNER_LABEL_VALUE}",
            "label=godsv.build_id=b-timeout",
        ], listing
        assert not any("," in value for value in values), listing
        assert [call for call in calls if call[:2] == ["docker", "rm"]] == [
            ["docker", "rm", "-f", "0ur00000009"]
        ], calls

    def test_a_timed_out_job_without_a_narrowing_label_is_not_swept(self, monkeypatch):
        """Never a blanket owner sweep: without a narrowing label nothing is removed."""
        manager = dr.DockerRuntimeManager(docker_bin="docker")
        calls: list[list[str]] = []

        def stub(argv, **_kw):
            calls.append(list(argv))
            return dr.CmdResult(-9, "", "", timeout=True)

        monkeypatch.setattr(manager, "_invoke_raw", stub)
        monkeypatch.setattr(manager, "available", lambda: (True, "Server Version 29.6.1"))
        result = manager.run_job(
            {"image": "alpine", "image_digest": JOB_DIGEST, "argv": ["sleep", "600"]},
            execution.RunPolicy(),
        )

        assert result.status == execution.STATUS_NOT_CERTIFIABLE, result
        assert "no narrowing label" in result.detail, result.detail
        assert [call for call in calls if call[:2] == ["docker", "rm"]] == [], calls

    def test_an_unavailable_daemon_is_reported_not_raised(self, monkeypatch):
        manager = dr.DockerRuntimeManager(docker_bin="docker")
        monkeypatch.setattr(manager, "available", lambda: (False, "cannot connect to the daemon"))
        result = manager.run_job(
            {"image": "alpine", "image_digest": JOB_DIGEST, "argv": ["true"]},
            execution.RunPolicy(),
        )
        assert result.ok is False and result.status == execution.STATUS_NOT_CERTIFIABLE
        assert "not available" in result.detail and "cannot connect" in result.detail


@live_docker
def test_live_run_job_runs_to_completion_and_leaves_nothing_behind():
    """Live: one real job through ``run_job`` — ``--rm`` leaves 0 containers of ours.

    The job's stdout is read, the scoped listing after the run is empty, the number
    of containers of ours on the daemon does not grow and the owner's foreign
    ``penpot-local`` containers are counted before and after (never touched).
    """
    mgr = dr.DockerRuntimeManager()
    # resolve FIRST: when the image is already local a Hub round trip is pointless,
    # and `docker pull` is rate-limited (429) — this test must not add to that.
    digest = mgr.resolve_digest("alpine:3.20", timeout=60.0)
    if not str(digest).startswith("sha256:"):
        digest = mgr.pull_and_pin("alpine:3.20")
    mgr.cleanup_by_labels({dr.OWNER_LABEL_KEY: dr.OWNER_LABEL_VALUE, **LIVE_JOB_LABELS})
    ours_before = _raw_ids(f"--filter=label={dr.OWNER_LABEL_KEY}={dr.OWNER_LABEL_VALUE}")
    foreign_before = _raw_ids(f"--filter={FOREIGN_FILTER}")
    assert foreign_before, "expected the owner's foreign compose containers on this daemon"

    job = {
        "image": "alpine:3.20",
        "image_digest": digest,
        "argv": ["sh", "-c", "echo run-job-live; cat /etc/os-release"],
        "labels": dict(LIVE_JOB_LABELS),
        "network": "none",
        "readonly_rootfs": True,
    }
    try:
        result = mgr.run_job(job, execution.RunPolicy())
        print(f"[live] run_job status={result.status} exit={result.exit_code} "
              f"sandboxed={result.sandboxed} stdout={result.stdout!r}")
        assert result.ok is True and result.status == execution.STATUS_OK, result
        assert result.exit_code == 0 and result.sandboxed is True, result
        assert "run-job-live" in result.stdout, result.stdout
        assert "Alpine Linux" in result.stdout, (
            f"the job did not run inside the image: {result.stdout!r}"
        )
    finally:
        swept = mgr.cleanup_by_labels(
            {dr.OWNER_LABEL_KEY: dr.OWNER_LABEL_VALUE, **LIVE_JOB_LABELS}
        )

    scoped = mgr.list_owned(dict(LIVE_JOB_LABELS))
    ours_after = _raw_ids(f"--filter=label={dr.OWNER_LABEL_KEY}={dr.OWNER_LABEL_VALUE}")
    foreign_after = _raw_ids(f"--filter={FOREIGN_FILTER}")
    print(f"[live] scoped containers after the run: "
          f"{scoped.payload.get('containers')} (sweep report: {swept.detail})")
    print(
        f"[live] our containers on the daemon: before={len(ours_before)} "
        f"after={len(ours_after)}"
    )
    print(
        f"[live] foreign penpot containers: before={len(foreign_before)} "
        f"after={len(foreign_after)}"
    )

    assert scoped.ok, scoped.detail
    assert scoped.payload["containers"] == [], scoped.payload
    assert set(ours_after) == set(ours_before), (
        f"run_job left containers of ours behind: {set(ours_after) - set(ours_before)}"
    )
    assert foreign_after == foreign_before, (
        "the foreign compose containers of the shared daemon changed during the run"
    )


# --------------------------------------------------------------------------- #
# live (daemon + opt-in) — image pull round trip
# --------------------------------------------------------------------------- #


@docker_daemon
def test_daemon_probe_reports_server_version():
    mgr = dr.DockerRuntimeManager()
    ok, detail = mgr.available()
    assert ok, detail
    assert "Server Version" in detail


@live_docker
def test_live_round_trip(tmp_path):
    """pull → pin → start → exec → stop/remove on a real alpine image."""
    mgr = dr.DockerRuntimeManager()
    digest = mgr.pull_and_pin("alpine:3.20")
    assert digest.startswith("sha256:")
    # `sleep` keeps the ONE-SHOT service container alive; busybox `sh` with a
    # closed stdin would exit() immediately (measured live, see evidence doc).
    rc = dr.RunConfig(
        image_digest=digest, entrypoint_argv=["sleep", "60"],
        workdir=tmp_path, name="godsv-b6-rt",
    )
    started = mgr.start(rc, cid_file=tmp_path / "cid")
    assert started.ok, started.detail
    assert started.cid, "the started container must carry a real cid"
    try:
        run = mgr.exec_in_container(
            tmp_path / "cid", argv=["echo", "hello-build"], timeout=30.0
        )
        assert run.ok, run.detail
        assert "hello-build" in run.stdout
        effect = mgr.exec_in_container(
            tmp_path / "cid",
            argv=["sh", "-c", "echo effect > /tmp/proof && cat /tmp/proof"],
            timeout=30.0,
        )
        assert effect.ok and "effect" in effect.stdout
    finally:
        mgr.stop_and_remove(started.cid)


@live_docker
def test_port_conflict_live(tmp_path):
    """A real port collision produces port_conflict + a structured payload."""
    s = socket_mod.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.listen(1)
    try:
        mgr = dr.DockerRuntimeManager()
        digest = mgr.pull_and_pin("alpine:3.20")
        rc = dr.RunConfig(
            image_digest=digest,
            port_bindings={80: port},
            workdir=tmp_path,
            name="godsv-b6-port",
        )
        result = mgr.start(rc, cid_file=tmp_path / "cid")
        assert result.ok is False
        assert result.failure_class == "port_conflict"
        assert result.payload.get("host_port") == port
    finally:
        s.close()


@live_docker
def test_image_missing_live():
    mgr = dr.DockerRuntimeManager()
    result = mgr.pull("alpine:no-such-tag-gv-b6")
    assert result.ok is False
    assert result.failure_class == "image_missing"
