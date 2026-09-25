"""§B6 — the container half of the self-expanding tool system (CLI + builder).

The tests are hermetic: a FAKE docker runtime stands in for the daemon, so they
prove the PIPELINE (search → inspect → adapt → test → register → rollback) and
its refusals without needing a container. The real-container runs are the
``GODSV_DOCKER_E2E=1`` test at the bottom (skipped by default) and the proof
transcript of the wave.

What is pinned here, in the order the owner stated it:

* a capability is SEARCHED for before anything is built (and a duplicate refuses),
* nothing is generated from an uninspected surface (exit 3 + reason),
* the adapter is typed (inputs/outputs/permissions) and bound to the pinned digest,
* the adapter is TESTED against the real container against an INDEPENDENT oracle,
* registration happens only on that evidence — and refuses when the adapter
  changed after the test,
* the registered tool carries version, health, permissions and a rollback,
* rollback tears containers down BY LABEL and restores the previous state,
* ``discovery.ensure_ready`` really starts the container — exactly once — and
  installs the shared execution seam.
"""

import hashlib
import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from skaldir import capability_health as health
from skaldir import cli_contract, discovery, execution, toolbuilder
from skaldir import docker_runtime as dr
from skaldir.docker_runtime import (
    OWNER_LABEL_KEY,
    OWNER_LABEL_VALUE,
    DockerRuntimeManager,
)
from skaldir.toolbuilder import (
    _SURFACE_PROBES,
    ADAPTER_FILE,
    CAPABILITY_LABEL,
    CONTAINER_KIND,
    CONTAINER_PERMISSIONS,
    ContainerJob,
    ContainerToolBuilder,
    DockerJobExecutor,
    ToolBuilder,
    ToolBuildRequest,
    ensure_container_started,
    generate_container_adapter,
    inspect_container_surface,
    parse_container_stdout,
    render_container_argv,
)
from skaldir.tools import STATUS_APPROVED, STATUS_REVOKED, ToolSpec, ToolStore

DIGEST = "sha256:" + "d9e853e87e55526f6b2917df91a2115c36dd7c696a35be12163d44e6e2a4b6bc"
IMAGE = "alpine:3.20"
CAPABILITY = "compute the sha-256 digest of a utf-8 string"
IDENTITY = (
    'NAME="Alpine Linux"\nID=alpine\nVERSION_ID="3.20.10"\n'
    'PRETTY_NAME="Alpine Linux v3.20"\n'
)
VERBS = "sha256sum\nmd5sum\nsha512sum\nbase64\ncksum\n"

SIMPLE_SOURCE = '''"""Deterministic one-shot tool used by the container build tests."""
from __future__ import annotations


def main(amount: float = 1.0) -> dict:
    return {"amount": float(amount)}
'''


@dataclass
class FakeResult:
    """The shape of docker_runtime.StartResult this pipeline consumes."""

    ok: bool = True
    detail: str = "fake"
    cid: str = ""
    exit_code: int | None = 0
    stdout: str = ""
    stderr: str = ""
    failure_class: str = ""
    payload: dict = field(default_factory=dict)
    policy: str = ""

    def to_dict(self) -> dict:
        return {
            "ok": self.ok, "detail": self.detail, "cid": self.cid,
            "exit_code": self.exit_code, "stdout": self.stdout, "stderr": self.stderr,
            "failure_class": self.failure_class, "payload": dict(self.payload),
        }


@dataclass
class FakeCmd:
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    timeout: bool = False


class FakeManager:
    """A docker runtime that never touches docker — records what it was asked."""

    def __init__(
        self,
        *,
        digest: str = DIGEST,
        verbs: str = VERBS,
        identity: str = IDENTITY,
        probe_ok: bool = True,
        adapter_stdout: str = "",
        containers: list[str] | None = None,
    ) -> None:
        self.digest = digest
        self.verbs = verbs
        self.identity = identity
        self.probe_ok = probe_ok
        self.adapter_stdout = adapter_stdout
        self.containers = list(containers or [])
        self.runs: list[list[str]] = []
        self.starts: list[dict] = []
        self.execs: list[list[str]] = []
        self.cleanups: list[dict] = []
        self.lists: list[dict] = []
        self.running: dict[str, bool] = {}
        #: the labels each container really carries — label listings AND over them
        self.labels_by_cid: dict[str, dict] = {}

    # -- probe surface ------------------------------------------------------- #
    def available(self) -> tuple[bool, str]:
        return True, "fake Server 29.6.1 (x86_64)"

    def resolve_digest(self, image: str, *, timeout: float = 60.0) -> str:
        return self.digest

    def pull(self, image: str, *, timeout: float = 600.0) -> FakeResult:
        return FakeResult(ok=True, stdout=self.digest, detail="pulled")

    def _invoke(self, argv: list[str], *, timeout: float = 30.0, workdir=None) -> FakeCmd:
        self.runs.append(list(argv))
        if "inspect" in argv:
            cid = argv[-1]
            return FakeCmd(0, "true" if self.running.get(cid) else "false")
        return FakeCmd(0, "")

    # -- lifecycle ----------------------------------------------------------- #
    def run(self, config, *, timeout: float = 60.0, **_kw) -> FakeResult:
        argv = list(config.entrypoint_argv)
        self.runs.append(argv)
        if not self.probe_ok:
            return FakeResult(
                ok=False, exit_code=127, stderr='exec: "sh": executable file not found',
                detail='exec: "sh": executable file not found', failure_class="docker_error",
            )
        if argv == list(_SURFACE_PROBES[0][1]):
            return FakeResult(ok=True, stdout=self.identity)
        if argv == list(_SURFACE_PROBES[1][1]):
            return FakeResult(ok=True, stdout=self.verbs)
        if argv == list(_SURFACE_PROBES[2][1]):
            return FakeResult(ok=True, stdout="")
        return FakeResult(ok=True, stdout=self.adapter_stdout or _sha256_of(argv[-1]))

    def start(self, config, *, cid_file=None, timeout: float = 60.0) -> FakeResult:
        cid = f"cid{len(self.starts) + 1:04d}"
        self.starts.append({
            "name": config.name, "image": config.image, "image_digest": config.image_digest,
            "argv": list(config.entrypoint_argv), "labels": dict(config.labels),
            "readonly": bool(config.readonly_rootfs), "network": config.network,
        })
        self.running[cid] = True
        self.containers.append(cid)
        # every container this fake starts carries its real label set: the fake
        # answers label listings by ANDing them, like the daemon does
        self.labels_by_cid[cid] = dict(config.labels)
        if cid_file is not None:
            Path(cid_file).parent.mkdir(parents=True, exist_ok=True)
            Path(cid_file).write_text(cid + "\n", encoding="utf-8")
        return FakeResult(ok=True, cid=cid, detail="started")

    def seed_container(self, cid: str, labels: dict | None = None, *, running: bool = True) -> None:
        """A container that already existed (e.g. started by an earlier process)."""
        self.containers.append(cid)
        self.running[cid] = bool(running)
        self.labels_by_cid[cid] = dict(labels or {})

    def exec_in_container(self, cid_file, *, argv: list[str], timeout: float = 30.0) -> FakeResult:
        self.execs.append(list(argv))
        return FakeResult(ok=True, detail="exec ok", stdout=_sha256_of(argv[-1]))

    def stop_and_remove(self, cid: str, *, timeout: float = 30.0) -> FakeResult:
        if cid in self.containers:
            self.containers.remove(cid)
        self.running.pop(cid, None)
        self.labels_by_cid.pop(cid, None)
        return FakeResult(ok=True, detail=f"removed {cid}")

    # -- label scope --------------------------------------------------------- #
    def list_by_labels(self, labels: dict, *, timeout: float = 30.0) -> FakeResult:
        """AND over the labels a container really carries (the fixed daemon shape).

        ``docker_runtime.list_by_labels`` emits ONE ``--filter`` per label and the
        daemon ANDs separate filters; the comma-joined single filter it used
        before matched nothing (Server 29.6.1 — the multi-label sweep was a silent
        no-op, see ``tests/test_docker_runtime_label_filters.py``). Modelling the
        fixed behaviour here keeps this fake honest: a regression to the
        comma-joined form would make every multi-label scope in this module empty
        again, and the tests below pin that.
        """
        self.lists.append(dict(labels))
        if not labels or any(not key.startswith("godsv") for key in labels):
            return FakeResult(ok=False, failure_class="policy_violation", detail="refused")
        found = [
            cid for cid in self.containers
            if all(
                str(self.labels_by_cid.get(cid, {}).get(key, "")) == str(value)
                for key, value in labels.items()
            )
        ]
        return FakeResult(
            ok=True,
            payload={
                "containers": found,
                "filter_args": [f"label={key}={value}" for key, value in labels.items()],
                "filters": ",".join(f"{key}={value}" for key, value in labels.items()),
                "labels": dict(labels),
            },
        )

    def cleanup_by_labels(self, labels: dict, *, timeout: float = 30.0) -> FakeResult:
        listing = self.list_by_labels(labels)
        if not listing.ok:
            return listing
        removed = []
        for cid in listing.payload.get("containers", []):
            removed.append(cid)
            self.containers.remove(cid)
            self.running.pop(cid, None)
        self.cleanups.append(dict(labels))
        return FakeResult(
            ok=True, detail=f"removed {len(removed)} leftovers",
            payload={"removed": removed, "scope": ",".join(
                f"{key}={value}" for key, value in labels.items())},
        )


def _sha256_of(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


@pytest.fixture(autouse=True)
def _no_real_docker(monkeypatch):
    """No test of this module may create a REAL DockerRuntimeManager.

    Every test injects a fake manager; a test that forgets one fails here
    instead of starting a container on the shared daemon (the daemon is shared
    with foreign stacks and with sibling processes — a leaked container of ours
    breaks them).
    """
    def guarded(manager=None):
        if manager is None:
            raise AssertionError(
                "this test tried to create a real DockerRuntimeManager — "
                "inject the fake manager instead"
            )
        return manager

    monkeypatch.setattr(toolbuilder, "_manager_of", guarded)
    yield


@pytest.fixture
def runtime(tmp_path) -> Path:
    path = tmp_path / "runtime"
    path.mkdir()
    return path


@pytest.fixture
def manager() -> FakeManager:
    return FakeManager()


@pytest.fixture
def builder(runtime, manager):
    return ContainerToolBuilder(runtime, manager=manager)


def _mine(builder, **overrides) -> dict:
    kwargs = {"image": IMAGE, "name": "container_sha256", "capability": CAPABILITY}
    kwargs.update(overrides)
    return builder.mine_container(**kwargs)


def _stubbed_runtime(
    monkeypatch,
    *,
    exit_code: int = 0,
    stdout: str = "job output",
    stderr: str = "",
) -> tuple[DockerRuntimeManager, list[list[str]]]:
    """A REAL :class:`DockerRuntimeManager` whose docker CLI is stubbed.

    The manager IS the one job runner (``DockerJobExecutor`` delegates to it), so
    the executor tests below exercise the real mapping over a recorded fake CLI:
    no daemon, no container, nothing on the shared daemon.
    """
    manager = DockerRuntimeManager(docker_bin="docker")
    calls: list[list[str]] = []

    def stub(argv, **_kw):
        calls.append(list(argv))
        return dr.CmdResult(exit_code, stdout, stderr)

    monkeypatch.setattr(manager, "_invoke_raw", stub)
    monkeypatch.setattr(
        manager, "available", lambda: (True, "Server Version 29.6.1 (stub)")
    )
    return manager, calls


# --------------------------------------------------------------------------- #
# (1) search before build
# --------------------------------------------------------------------------- #


class TestSearchBeforeBuild:
    def test_an_existing_capability_refuses_the_build(self, builder, manager):
        """A capability mined once is REUSED on the next request, never rebuilt."""
        first = _mine(builder, name="container_sha256")
        assert first["ok"] is True, first
        runs_after_first = len(manager.runs)

        result = _mine(builder, name="container_sha256_v2")

        assert result["ok"] is False and result["failure_class"] == "reuse"
        assert result["search"]["decision"] == "reuse"
        assert result["search"]["match"]["name"] == "container_sha256"
        # the second request inspected NOTHING and wrote NOTHING
        assert len(manager.runs) == runs_after_first
        assert not (builder.tools_dir / "container_sha256_v2").exists()

    def test_a_new_capability_is_searched_and_then_built(self, builder, manager):
        result = _mine(builder)

        assert result["ok"] is True, result
        assert result["search"]["decision"] == "build"
        assert result["search"]["searched"] == [
            "generated_tools", "mcp", "skills", "testing_tools", "tool_hub", "workflows",
        ]
        assert [stage["stage"] for stage in result["stages"]] == [
            "search", "resolve_image", "inspect_surface", "generate_adapter",
            "test_adapter", "stage",
        ]


# --------------------------------------------------------------------------- #
# (2) inspect the container surface
# --------------------------------------------------------------------------- #


class TestSurfaceInspection:
    def test_a_pinned_digest_is_required(self, runtime, manager):
        surface = inspect_container_surface(manager, image=IMAGE, image_digest="")
        assert surface["inspected"] is False
        assert surface["failure_class"] == "pin_required"
        assert manager.runs == []

    def test_an_image_without_a_shell_refuses_instead_of_guessing(self, builder, manager):
        manager.probe_ok = False
        result = _mine(builder)

        assert result["ok"] is False
        assert result["failure_class"] == "docker_error"
        assert "cannot be inspected" in result["reason"]
        assert result["surface"]["inspected"] is False
        assert result["adapter"] == {}
        assert not (builder.tools_dir / "container_sha256" / ADAPTER_FILE).exists()

    def test_the_surface_lists_the_real_verbs_and_the_absent_api_mcp(self, builder):
        surface = _mine(builder)["surface"]

        assert surface["inspected"] is True
        assert surface["cli"]["verbs"] == ["base64", "cksum", "md5sum", "sha256sum", "sha512sum"]
        assert surface["os"]["PRETTY_NAME"] == "Alpine Linux v3.20"
        assert surface["api"]["server_hint"] == "" and surface["mcp"]["executable"] == ""
        assert [probe["probe"] for probe in surface["probes"]] == ["identity", "cli", "api_mcp"]

    def test_a_capability_without_a_matching_verb_is_refused(self, builder, manager):
        manager.verbs = "echo\nls\ncat\n"
        result = _mine(builder)

        assert result["ok"] is False and result["failure_class"] == "adapter_refused"
        assert "no container verb" in result["reason"]
        assert "surface verbs: cat, echo, ls" in result["reason"]


# --------------------------------------------------------------------------- #
# (3) the typed adapter
# --------------------------------------------------------------------------- #


class TestTypedAdapter:
    def test_the_adapter_is_typed_and_pinned(self, builder):
        result = _mine(builder)
        adapter = result["adapter"]

        assert adapter["verb"] == "sha256sum"
        assert adapter["image_digest"] == DIGEST
        assert adapter["operation"]["inputs"] == {"text": {"type": "string", "required": True}}
        assert adapter["operation"]["outputs"]["sha256"]["pattern"] == "^[0-9a-f]{64}$"
        assert adapter["permissions"] == CONTAINER_PERMISSIONS
        assert adapter["permissions"]["network"] == "none"
        assert adapter["permissions"]["privileged"] is False
        assert adapter["permissions"]["mounts"] == []

    def test_rendering_validates_the_declared_inputs(self, builder):
        adapter = _mine(builder)["adapter"]

        assert render_container_argv(adapter, {"text": "skaldir"})[-1] == "skaldir"
        with pytest.raises(toolbuilder.AdapterRefusal):
            render_container_argv(adapter, {})
        with pytest.raises(toolbuilder.AdapterRefusal):
            render_container_argv(adapter, {"text": "x", "extra": "y"})

    def test_parsing_checks_the_declared_output_pattern(self, builder):
        adapter = _mine(builder)["adapter"]

        ok, parsed, _ = parse_container_stdout(adapter, _sha256_of("skaldir"))
        assert ok is True and set(parsed) == {"sha256"}
        ok, parsed, detail = parse_container_stdout(adapter, "not a digest")
        assert ok is False and "does not match" in detail

    def test_an_adapter_cannot_be_generated_from_an_uninspected_surface(self, runtime):
        with pytest.raises(toolbuilder.SurfaceRefusal):
            generate_container_adapter(
                CAPABILITY, name="x", image=IMAGE, image_digest=DIGEST,
                surface={"inspected": False, "reason": "no shell"},
            )


# --------------------------------------------------------------------------- #
# (4) the test against the real container
# --------------------------------------------------------------------------- #


class TestAdapterTest:
    def test_the_test_runs_the_real_container_and_compares_to_a_local_oracle(
        self, builder, manager
    ):
        result = _mine(builder)

        assert result["test"]["ok"] is True
        assert result["test"]["expected"] == _sha256_of("skaldir")
        assert result["test"]["observed"] == result["test"]["expected"]
        assert "independent" in result["test"]["oracle"]
        # the container really was asked, one-shot, network none, read-only
        assert result["test"]["argv"][0] == "sh"
        assert result["test"]["isolation"]["network"] == "none"
        assert result["test"]["isolation"]["mode"].startswith("one-shot")

    def test_a_wrong_container_answer_fails_the_test_and_stages_nothing(self, builder, manager):
        manager.adapter_stdout = "0" * 64
        result = _mine(builder)

        assert result["ok"] is False and result["failure_class"] == "test_failed"
        assert result["test"]["observed"] != result["test"]["expected"]
        assert result["staged"] is False
        assert not (builder.tools_dir / "container_sha256" / ADAPTER_FILE).exists()

    def test_a_declared_expectation_must_agree_with_the_oracle(self, builder):
        result = _mine(builder, expect="0" * 64)

        assert result["ok"] is False and result["failure_class"] == "test_failed"
        assert "disagrees with the independent local oracle" in result["test"]["detail"]


# --------------------------------------------------------------------------- #
# (5) registration — evidence or nothing
# --------------------------------------------------------------------------- #


class TestRegistration:
    def test_registration_carries_version_health_permissions_and_rollback(self, builder, manager):
        _mine(builder)
        result = builder.register_container_tool("container_sha256", requested_by="test")

        assert result["ok"] is True and result["registered"] is True
        assert result["version"] == "0.1.0" and result["status"] == STATUS_APPROVED
        assert result["kind"] == CONTAINER_KIND
        assert result["permissions"]["network"] == "none"
        assert result["health_state"] == "HEALTHY"
        assert result["health"]["auto_routable"] is True
        assert result["rollback_id"]
        assert result["publish"]["outbox_id"]  # one registry commit + one publication fact

        spec = ToolStore(builder.runtime / "tools.db").get("container_sha256")
        assert spec is not None and spec.status == STATUS_APPROVED
        assert spec.kind == CONTAINER_KIND and spec.version == "0.1.0"
        assert spec.module.startswith("container_")
        assert "pinned" in spec.rationale or DIGEST in spec.rationale

        # the manifest is the runtime=docker v2 shape
        import yaml

        manifest_path = builder.tools_dir / "container_sha256" / "manifest.yaml"
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        assert manifest["runtime"] == "docker"
        assert manifest["image_digest"] == DIGEST
        assert manifest["status"] == "ready"
        assert manifest["validation"]["status"] == "validated"
        assert manifest["last_tested_at"] is not None
        health.file_state(builder.runtime, "container_sha256")  # ledger is readable

    def test_registration_without_evidence_is_refused(self, builder):
        paths = toolbuilder.container_tool_paths(builder.runtime, "ghost")
        paths["adapter"].parent.mkdir(parents=True, exist_ok=True)
        paths["adapter"].write_text(json.dumps({"name": "ghost"}), encoding="utf-8")

        result = builder.register_container_tool("ghost")

        assert result["ok"] is False and result["failure_class"] == "unverified"
        assert ToolStore(builder.runtime / "tools.db").get("ghost") is None
        assert health.file_state(builder.runtime, "ghost")["state"] == "CANDIDATE"

    def test_a_failed_test_can_never_be_registered(self, builder, manager):
        manager.adapter_stdout = "0" * 64
        _mine(builder)
        # a "staged" adapter whose evidence says the test failed
        paths = toolbuilder.container_tool_paths(builder.runtime, "container_sha256")
        paths["adapter"].parent.mkdir(parents=True, exist_ok=True)
        paths["adapter"].write_text(json.dumps({"name": "container_sha256"}), encoding="utf-8")
        paths["evidence"].write_text(
            json.dumps({"ok": False, "detail": "observed 000… != expected abc…"}),
            encoding="utf-8",
        )
        result = builder.register_container_tool("container_sha256")

        assert result["ok"] is False and result["failure_class"] == "unverified"
        assert "did not pass" in result["reason"]

    def test_an_adapter_edited_after_the_test_is_not_registered(self, builder):
        _mine(builder)
        paths = toolbuilder.container_tool_paths(builder.runtime, "container_sha256")
        adapter = json.loads(paths["adapter"].read_text(encoding="utf-8"))
        adapter["argv_template"] = ["sh", "-c", "echo " + "0" * 64]
        paths["adapter"].write_text(json.dumps(adapter, indent=2, sort_keys=True), encoding="utf-8")

        result = builder.register_container_tool("container_sha256")

        assert result["ok"] is False and result["failure_class"] == "adapter_changed"
        assert ToolStore(builder.runtime / "tools.db").get("container_sha256") is None

    def test_a_repair_mine_invalidates_the_old_evidence_until_re_registration(self, builder):
        """Rebuilding under the same name is a repair — and it is never a stale green."""
        _mine(builder)
        builder.register_container_tool("container_sha256")
        assert health.file_state(builder.runtime, "container_sha256")["state"] == "HEALTHY"

        repaired = _mine(builder)
        assert repaired["ok"] is True and repaired["search"]["decision"] == "repair"
        # the adapter bytes changed while the tool is still registered: the
        # ledger downgrades the tool instead of keeping a verification that no
        # longer covers the code on disk
        stale = health.file_state(builder.runtime, "container_sha256")
        assert stale["state"] == "DEGRADED"
        assert "code changed since verification" in " ".join(stale["reasons"])

        again = builder.register_container_tool("container_sha256")
        assert again["ok"] is True and again["version"] == "0.1.1"
        assert health.file_state(builder.runtime, "container_sha256")["state"] == "HEALTHY"

    def test_re_registering_bumps_the_version_and_snapshots_the_previous_one(self, builder):
        _mine(builder)
        first = builder.register_container_tool("container_sha256")
        _mine(builder)
        second = builder.register_container_tool("container_sha256")

        assert first["version"] == "0.1.0" and second["version"] == "0.1.1"
        snapshots = sorted(
            (builder.runtime / "container_rollbacks" / "container_sha256").glob("*.json")
        )
        assert len(snapshots) == 2
        previous = json.loads(snapshots[-1].read_text(encoding="utf-8"))["previous_spec"]
        assert previous["version"] == "0.1.0"


# --------------------------------------------------------------------------- #
# call / health / rollback
# --------------------------------------------------------------------------- #


class TestCallHealthRollback:
    def test_a_call_returns_the_typed_output(self, builder):
        _mine(builder)
        builder.register_container_tool("container_sha256")

        call = builder.call_container_tool("container_sha256", {"text": "skaldir"})

        assert call["ok"] is True and call["mode"] == "one-shot"
        assert call["output"] == {"sha256": _sha256_of("skaldir")}

    def test_health_reports_the_ledger_and_the_live_container_facts(self, builder):
        _mine(builder)
        builder.register_container_tool("container_sha256")

        report = builder.container_health("container_sha256", probe=True)

        assert report["health"]["state"] == "HEALTHY"
        assert report["selectable"] is True and report["auto_routable"] is True
        assert report["container"]["digest_match"] is True
        assert report["registered"] is True and report["version"] == "0.1.0"
        assert report["probe"]["output"] == {"sha256": _sha256_of("skaldir")}

    def test_rollback_removes_the_registration_files_health_and_containers(self, builder, manager):
        _mine(builder)
        builder.register_container_tool("container_sha256")
        ensure_container_started(builder.runtime, "container_sha256", manager=manager)
        assert manager.starts and manager.containers
        ours = list(manager.containers)

        result = builder.rollback_container_tool("container_sha256", reason="test rollback")

        assert result["ok"] is True and result["rolled_back"] is True
        assert result["removed_registration"] is True
        assert result["containers_removed"] == ours
        assert manager.containers == []
        # the teardown scope is this capability's label; a wider owner+capability
        # scope is correct too since list_by_labels emits ONE --filter per label
        # (the comma-joined single filter it used before matched nothing on the
        # live daemon and made multi-label sweeps a silent no-op)
        assert manager.cleanups == [{CAPABILITY_LABEL: "container_sha256"}]
        assert result["teardown_scope"] == f"{CAPABILITY_LABEL}=container_sha256"
        assert result["health"]["state"] == "RETIRED"
        assert result["health"]["auto_routable"] is False
        assert not (builder.tools_dir / "container_sha256").exists()
        assert Path(result["quarantine"]).is_dir()
        spec = ToolStore(builder.runtime / "tools.db").get("container_sha256")
        assert spec is not None and spec.status == STATUS_REVOKED
        assert not (builder.runtime / "container_services" / "container_sha256.cid").exists()

    def test_rollback_restores_the_previous_registration(self, builder, manager):
        store = ToolStore(builder.runtime / "tools.db")
        store.upsert(
            ToolSpec(
                name="container_sha256", title="old", description="old",
                status=STATUS_APPROVED, version="9.9.9", kind="python",
                module="old_module",
            )
        )
        _mine(builder)
        builder.register_container_tool("container_sha256")

        result = builder.rollback_container_tool("container_sha256")

        assert result["removed_registration"] is False
        assert result["restored_spec"]["version"] == "9.9.9"
        assert result["restored_spec"]["kind"] == "python"
        restored = ToolStore(builder.runtime / "tools.db").get("container_sha256")
        assert restored.version == "9.9.9" and restored.kind == "python"

    def test_an_unknown_container_tool_is_not_found(self, builder):
        result = builder.rollback_container_tool("nope")
        assert result["ok"] is False and result["failure_class"] == "not_found"


# --------------------------------------------------------------------------- #
# discovery.ensure_ready + the shared execution seam
# --------------------------------------------------------------------------- #


class TestEnsureReadyAndTheSeam:
    def test_a_capability_without_a_container_adapter_keeps_the_b6_refusal(self, runtime):
        report = discovery.ensure_ready("web_search", runtime=runtime)

        assert report["started"] is False and report["startable"] is True
        assert "B6" in report["reason"] and report["hook"] == "ensure_ready"

    def test_the_container_is_started_exactly_once_then_reused(self, builder, manager):
        _mine(builder)
        builder.register_container_tool("container_sha256")

        first = ensure_container_started(builder.runtime, "container_sha256", manager=manager)
        second = ensure_container_started(builder.runtime, "container_sha256", manager=manager)

        assert first["started"] is True and first["reused"] is False
        assert second["started"] is True and second["reused"] is True
        assert len(manager.starts) == 1
        started = manager.starts[0]
        assert started["labels"]["godsv.owner"] == "skaldir"
        assert started["labels"][CAPABILITY_LABEL] == "container_sha256"
        assert started["image_digest"] == DIGEST
        assert started["readonly"] is True and started["network"] == "none"
        report = discovery.ensure_ready(
            "container_sha256", runtime=builder.runtime, services={"searxng": "stopped"},
            manager=manager,
        )
        assert report["started"] is True and report["services"] == {"searxng": "stopped"}

    def test_a_multi_label_scope_finds_and_removes_our_container(self, builder, manager):
        """Regression: a scope with MORE than one label really matches (the fix).

        ``docker_runtime.list_by_labels`` used to join a multi-label scope into a
        single comma-joined ``--filter`` — docker reads that as ONE label pair and
        matches nothing, so a multi-label sweep (this builder's owner+build
        teardown, ``cleanup_owned``) removed nothing while reporting success. With
        one ``--filter`` per label the same scope finds and removes the container.
        """
        _mine(builder)
        builder.register_container_tool("container_sha256")
        ensure_container_started(builder.runtime, "container_sha256", manager=manager)
        ours = list(manager.containers)
        assert ours
        scope = {OWNER_LABEL_KEY: OWNER_LABEL_VALUE, CAPABILITY_LABEL: "container_sha256"}

        listing = manager.list_by_labels(scope)
        assert listing.ok, listing.detail
        assert listing.payload["containers"] == ours, listing.payload
        assert listing.payload["filter_args"] == [
            f"label={OWNER_LABEL_KEY}={OWNER_LABEL_VALUE}",
            f"label={CAPABILITY_LABEL}=container_sha256",
        ], listing.payload

        removed = manager.cleanup_by_labels(scope)
        assert removed.ok, removed.detail
        assert removed.payload["removed"] == ours
        assert manager.containers == []

    def test_a_running_container_is_adopted_by_label_instead_of_a_second_start(
        self, builder, manager
    ):
        _mine(builder)
        manager.seed_container(
            "cid-from-an-earlier-process",
            {OWNER_LABEL_KEY: OWNER_LABEL_VALUE, CAPABILITY_LABEL: "container_sha256"},
        )

        report = ensure_container_started(builder.runtime, "container_sha256", manager=manager)

        assert report["reused"] is True and report["cid"] == "cid-from-an-earlier-process"
        assert manager.starts == []

    def test_a_warm_call_goes_through_docker_exec(self, builder, manager):
        _mine(builder)
        builder.register_container_tool("container_sha256")

        call = builder.call_container_tool("container_sha256", {"text": "warm"}, warm=True)

        assert call["ok"] is True and call["mode"] == "warm-exec"
        assert manager.execs and manager.execs[0][-1] == "warm"

    def test_the_shared_boundary_gets_the_docker_executor(self, builder, manager):
        _mine(builder)
        try:
            ensure_container_started(builder.runtime, "container_sha256", manager=manager)
            executor = execution.get_container_executor()
            assert isinstance(executor, DockerJobExecutor)
            available, detail = executor.available()
            assert available is True and "fake" in detail
        finally:
            execution.register_container_executor(execution.NullExecutor())

    def test_the_executor_refuses_a_job_it_cannot_run(self, monkeypatch):
        manager, calls = _stubbed_runtime(monkeypatch)
        executor = DockerJobExecutor(manager)

        foreign = executor.run_job(object(), execution.RunPolicy())
        assert foreign.ok is False and foreign.status == "not-certifiable"
        assert "ContainerJob" in foreign.detail

        unpinned = executor.run_job(ContainerJob(image=IMAGE), execution.RunPolicy())
        assert unpinned.ok is False and unpinned.status == "not-certifiable"
        assert "pinned" in unpinned.detail
        assert calls == [], "a refused job must not reach docker"

    def test_the_executor_delegates_to_the_one_job_runner(self):
        """ONE implementation: the manager's ``run_job`` — no second mapping here."""
        seen: list = []
        policy = execution.RunPolicy()

        class RecordingManager:
            def available(self) -> tuple[bool, str]:
                return True, "recording stub"

            def run_job(self, job, job_policy=None):
                seen.append((job, job_policy))
                return execution.RunResult(
                    ok=True, status=execution.STATUS_OK, sandboxed=True, detail="delegated"
                )

        executor = DockerJobExecutor(RecordingManager())
        job = ContainerJob(image=IMAGE, image_digest=DIGEST, argv=["true"])

        result = executor.run_job(job, policy)

        assert seen == [(job, policy)], seen
        assert result.status == execution.STATUS_OK and result.detail == "delegated"

    def test_a_docker_level_failure_is_not_certifiable_not_failed(self, monkeypatch):
        """The manager's precision wins: docker's own exit code means nothing ran."""
        manager, calls = _stubbed_runtime(
            monkeypatch, exit_code=125,
            stderr="docker: Error response from daemon: No such image: alpine:9999.",
        )
        executor = DockerJobExecutor(manager)

        result = executor.run_job(
            ContainerJob(image="alpine:9999", image_digest=DIGEST, argv=["true"]),
            execution.RunPolicy(),
        )

        assert result.ok is False
        assert result.status == execution.STATUS_NOT_CERTIFIABLE, result
        assert "never ran" in result.detail, result.detail
        assert result.exit_code is None
        assert calls and calls[0][:3] == ["docker", "run", "--rm"], calls

    def test_the_executor_runs_a_pinned_job_and_enforces_the_mount_allowlist(
        self, monkeypatch, tmp_path
    ):
        manager, calls = _stubbed_runtime(monkeypatch)
        executor = DockerJobExecutor(manager)
        job = ContainerJob(
            image=IMAGE, image_digest=DIGEST, argv=["sh", "-c", "echo hi"],
            mounts=[{"host_path": str(Path.home()), "container_path": "/data"}],
            workdir=tmp_path / "tool",
        )
        refused = executor.run_job(job, execution.RunPolicy())
        assert refused.status == "not-certifiable"
        assert "protected-host-path" in refused.detail
        assert calls == [], "a policy-refused job must not reach docker"

        clean = ContainerJob(
            image=IMAGE, image_digest=DIGEST, argv=["sh", "-c", "echo hi"],
            workdir=tmp_path / "tool",
        )
        ok = executor.run_job(clean, execution.RunPolicy())
        assert ok.ok is True and ok.status == "ok" and ok.sandboxed is True
        assert calls and calls[0][:3] == ["docker", "run", "--rm"], calls


# --------------------------------------------------------------------------- #
# the docker build path (runtime=docker) is reachable — it used to be dead code
# --------------------------------------------------------------------------- #


def test_the_docker_build_probes_the_container_and_tears_down_by_label(runtime, manager):
    builder = ToolBuilder(runtime, manager=manager)
    result = builder.build(
        ToolBuildRequest(
            capability="convert money amounts in a container",
            name="container_money",
            source_code=SIMPLE_SOURCE,
            module="container_money",
            entrypoint="main",
            sample_input={"amount": 2},
            runtime="docker",
            image=IMAGE,
            image_digest=DIGEST,
        )
    )

    gates = {check["name"]: check for check in result.get("gates", [])}
    assert "container_probe" in gates, result
    assert "container_teardown" in gates, result
    assert gates["container_probe"]["ok"] is True
    assert gates["container_teardown"]["ok"] is True
    assert result["container_probe"]["docker"]["available"] is True
    assert result["container_probe"]["image_digest"] == DIGEST


# --------------------------------------------------------------------------- #
# hub-independence: the build probe pins the LOCAL digest, and only an explicit
# --pull asks the registry (measured defect 2026-09-18: an unconditional
# `docker pull` failed every build whose image was already on this daemon,
# because docker.io answered `429 Too Many Requests`)
# --------------------------------------------------------------------------- #

#: Docker Hub's LIVE rate-limit answer, measured 2026-09-18 — verbatim.
RATE_LIMITED = (
    "Error response from daemon: unknown: failed to resolve reference "
    '"docker.io/library/alpine:3.20": unexpected status from HEAD request to '
    "https://registry-1.docker.io/v2/library/alpine/manifests/3.20: "
    "429 Too Many Requests"
)


def _docker_build_request(**overrides) -> ToolBuildRequest:
    kwargs = {
        "capability": "convert money amounts in a container",
        "name": "container_money",
        "source_code": SIMPLE_SOURCE,
        "module": "container_money",
        "entrypoint": "main",
        "sample_input": {"amount": 2},
        "runtime": "docker",
        "image": IMAGE,
        "image_digest": DIGEST,
    }
    kwargs.update(overrides)
    return ToolBuildRequest(**kwargs)


def _stub_docker_cli(
    monkeypatch, *, digest: str = DIGEST
) -> tuple[DockerRuntimeManager, list[list[str]]]:
    """A REAL manager whose docker CLI answers locally — and 429s every pull.

    That is the no-network proof in measurable form: the registry is effectively
    unreachable (any `docker pull` gets the measured rate-limit answer), so a
    build that still pins the digest and probes cannot have needed the Hub.
    """
    manager = DockerRuntimeManager(docker_bin="docker")
    calls: list[list[str]] = []

    def stub(argv, **_kw):
        calls.append(list(argv))
        subcommand = list(argv[1:2])
        if subcommand == ["pull"]:
            return dr.CmdResult(1, "", RATE_LIMITED)
        if subcommand == ["image"]:
            return dr.CmdResult(0, digest, "")
        if subcommand == ["ps"]:
            return dr.CmdResult(0, "", "")
        return dr.CmdResult(0, "container probe ok", "")

    monkeypatch.setattr(manager, "_invoke_raw", stub)
    monkeypatch.setattr(
        manager, "available", lambda: (True, "Server Version 29.6.1 (stub)")
    )
    return manager, calls


def test_the_build_pins_the_local_digest_and_never_pulls(monkeypatch, runtime, manager):
    """Every pull path is hard-forbidden — the local digest IS the pin."""
    attempts: list[str] = []

    def forbidden_pull(image, **_kw):
        attempts.append(str(image))
        raise AssertionError("the build must not pull an image that is already local")

    monkeypatch.setattr(manager, "pull", forbidden_pull)
    # the fake carries no `pull_and_pin` at all: the build path no longer calls
    # it (raising=False keeps this a hard-forbid if a fake ever grows one)
    monkeypatch.setattr(manager, "pull_and_pin", forbidden_pull, raising=False)

    result = ToolBuilder(runtime, manager=manager).build(_docker_build_request())

    assert result["ok"] is True, result
    assert attempts == []
    assert result["container_probe"]["image_digest"] == DIGEST


def test_a_rate_limited_registry_cannot_fail_a_build_from_a_local_image(monkeypatch, runtime):
    """No `docker pull` is issued at all — the 429 answer is never even asked."""
    manager, calls = _stub_docker_cli(monkeypatch)

    result = ToolBuilder(runtime, manager=manager).build(_docker_build_request())

    assert result["ok"] is True, result
    assert [call for call in calls if call[1:2] == ["pull"]] == [], calls
    assert result["container_probe"]["image_digest"] == DIGEST
    assert result["container_probe"]["probe"]["exit_code"] == 0
    assert "container probe ok" in result["container_probe"]["probe"]["stdout"]


def test_an_explicit_pull_is_the_one_path_that_asks_the_registry(monkeypatch, runtime):
    """``request.pull`` (CLI ``--pull``) is deliberate — and a 429 is classified."""
    manager, calls = _stub_docker_cli(monkeypatch)

    result = ToolBuilder(runtime, manager=manager).build(_docker_build_request(pull=True))

    assert [call for call in calls if call[1:2] == ["pull"]], calls
    assert result["ok"] is False
    assert result["blocked"] == "docker_pull"
    assert result["failure_class"] == "quota_limit"
    assert "429 Too Many Requests" in result["error"]


def test_a_quota_limit_pull_is_blocked_not_a_verification_failure(
    monkeypatch, runtime, manager
):
    """The taxonomy reaches the CLI: quota_limit ⇒ exit 3 (retryable), never 2."""
    from skaldir import toolbuilder_cli

    monkeypatch.setattr(manager, "resolve_digest", lambda image, **_kw: "")
    monkeypatch.setattr(
        manager, "pull",
        lambda image, **_kw: dr.StartResult(
            ok=False, failure_class="quota_limit", detail=RATE_LIMITED
        ),
    )

    result = ToolBuilder(runtime, manager=manager).build(_docker_build_request())

    assert result["ok"] is False
    assert result["blocked"] == "docker_pull"
    assert result["failure_class"] == "quota_limit"
    assert "quota_limit" in result["error"]
    assert toolbuilder_cli.FAILURE_EXIT["quota_limit"] == cli_contract.EXIT_BLOCKED
    assert toolbuilder_cli._exit_for(
        {"ok": False, "failure_class": "quota_limit", "reason": RATE_LIMITED}
    ) == cli_contract.EXIT_BLOCKED


def test_a_docker_build_without_a_pinned_digest_is_a_structured_refusal(runtime, manager):
    manager.digest = ""  # nothing pinned, nothing inspectable locally
    builder = ToolBuilder(runtime, manager=manager)

    result = builder.build(
        ToolBuildRequest(
            capability="convert money amounts in a container",
            name="container_money",
            source_code=SIMPLE_SOURCE,
            module="container_money",
            entrypoint="main",
            sample_input={"amount": 2},
            runtime="docker",
            image="not-local:latest",
        )
    )

    assert result["ok"] is False
    assert result["blocked"] == "manifest_v2"
    assert "pin-shaped image_digest" in result["error"]
    assert result["next_step"]


# --------------------------------------------------------------------------- #
# the CLI surface (tools mine / adapter show / register / rollback / health)
# --------------------------------------------------------------------------- #


def _cli_parser():
    """A `tools` parser with the container commands — the cli.py integration."""
    import argparse

    from skaldir.toolbuilder_cli import register_cli

    parser = argparse.ArgumentParser(prog="skaldir")
    sub = parser.add_subparsers(dest="command", required=True)
    tools = sub.add_parser("tools")
    tools_sub = tools.add_subparsers(dest="subcommand", required=True)
    register_cli(tools_sub)
    cli_contract.annotate(parser)
    return parser


def _run_cli(argv: list[str], capsys) -> tuple[int, dict]:
    parser = _cli_parser()
    args = parser.parse_args(argv)
    code = cli_contract.run_command(args, args.func)
    out = capsys.readouterr().out.strip().splitlines()
    envelope = json.loads(out[-1]) if out else {}
    return code, envelope


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "godsv").mkdir(parents=True)
    monkeypatch.setenv("GODSV_HOME", str(home / "godsv"))
    monkeypatch.delenv("GODSV_FORMAT", raising=False)
    return home / "godsv"


class TestContainerCli:
    def test_mine_stages_and_the_envelope_carries_the_evidence(
        self, cli_env, manager, monkeypatch, capsys
    ):
        monkeypatch.setattr(toolbuilder, "_manager_of", lambda _manager=None: manager)

        code, envelope = _run_cli(
            ["tools", "mine", "--image", IMAGE, "--capability", CAPABILITY,
             "--name", "container_sha256", "--json"],
            capsys,
        )

        assert code == 0 and envelope["ok"] is True
        assert envelope["command"] == "tools.mine"
        assert envelope["data"]["staged"] is True
        assert envelope["data"]["test"]["observed"] == _sha256_of("skaldir")
        assert envelope["data"]["surface"]["inspected"] is True
        assert envelope["data"]["surface"]["verbs"]["matched"] == "sha256sum"
        assert envelope["evidence"]

    def test_mine_without_a_capability_is_a_usage_error(
        self, cli_env, manager, monkeypatch, capsys
    ):
        monkeypatch.setattr(toolbuilder, "_manager_of", lambda _manager=None: manager)

        code, envelope = _run_cli(
            ["tools", "mine", "--image", IMAGE, "--json"], capsys
        )

        assert code == 1 and envelope["ok"] is False
        assert "needs --capability" in envelope["error"]["message"]

    def test_an_uninspectable_surface_exits_3_with_the_reason(
        self, cli_env, manager, monkeypatch, capsys
    ):
        manager.probe_ok = False
        monkeypatch.setattr(toolbuilder, "_manager_of", lambda _manager=None: manager)

        code, envelope = _run_cli(
            ["tools", "mine", "--image", "hello-world:latest", "--capability", CAPABILITY,
             "--name", "nope", "--json"],
            capsys,
        )

        assert code == 3 and envelope["ok"] is False
        assert envelope["error"]["code"] == "blocked"
        assert "cannot be inspected" in envelope["error"]["message"]
        assert "no adapter is generated" in json.dumps(envelope["data"])

    def test_register_without_evidence_exits_2(self, cli_env, capsys):
        paths = toolbuilder.container_tool_paths(cli_env, "ghost")
        paths["adapter"].parent.mkdir(parents=True, exist_ok=True)
        paths["adapter"].write_text(json.dumps({"name": "ghost"}), encoding="utf-8")

        code, envelope = _run_cli(["tools", "register", "ghost", "--json"], capsys)

        assert code == 2 and envelope["ok"] is False
        assert envelope["error"]["code"] == "verification_failed"
        assert envelope["data"]["failure_class"] == "unverified"

    def test_register_of_a_tool_that_was_never_staged_exits_5(self, cli_env, capsys):
        code, envelope = _run_cli(["tools", "register", "ghost", "--json"], capsys)

        assert code == 5 and envelope["error"]["code"] == "not_found"

    def test_mine_register_show_health_rollback_round_trip(
        self, cli_env, manager, monkeypatch, capsys
    ):
        monkeypatch.setattr(toolbuilder, "_manager_of", lambda _manager=None: manager)
        argv = ["--name", "container_sha256"]

        code, mined = _run_cli(
            ["tools", "mine", "--image", IMAGE, "--capability", CAPABILITY, *argv, "--json"],
            capsys,
        )
        assert code == 0 and mined["data"]["staged"] is True

        code, registered = _run_cli(["tools", "register", "container_sha256", "--json"], capsys)
        assert code == 0 and registered["data"]["registered"] is True
        assert registered["data"]["version"] == "0.1.0"
        assert registered["data"]["health_state"] == "HEALTHY"
        assert registered["data"]["permissions"]["network"] == "none"
        assert registered["data"]["rollback_id"]

        code, shown = _run_cli(["tools", "adapter", "show", "container_sha256", "--json"], capsys)
        assert code == 0 and shown["data"]["adapter"]["verb"] == "sha256sum"
        assert shown["data"]["permissions"]["privileged"] is False

        code, reported = _run_cli(
            ["tools", "health", "container_sha256", "--probe", "--json"], capsys
        )
        assert code == 0 and reported["data"]["health"]["state"] == "HEALTHY"
        assert reported["data"]["probe"]["ok"] is True

        code, rolled = _run_cli(
            ["tools", "rollback", "container_sha256", "--reason", "cli round trip", "--json"],
            capsys,
        )
        assert code == 0 and rolled["data"]["rolled_back"] is True
        assert rolled["data"]["health"]["state"] == "RETIRED"

        # after the rollback the capability is gone from every surface
        code, gone = _run_cli(["tools", "health", "container_sha256", "--json"], capsys)
        assert code == 5 and gone["error"]["code"] == "not_found"

    def test_every_new_command_takes_json(self, cli_env):
        parser = _cli_parser()
        leaves = {
            path: sub for path, sub in cli_contract.leaf_commands(parser)
            if path.startswith("tools.")
        }
        assert {
            "tools.mine", "tools.adapter.show", "tools.register", "tools.rollback",
            "tools.health",
        } <= set(leaves)
        for path, sub in leaves.items():
            options = {option for action in sub._actions for option in action.option_strings}
            assert "--json" in options, path


# --------------------------------------------------------------------------- #
# the real container (opt-in: GODSV_DOCKER_E2E=1)
# --------------------------------------------------------------------------- #

E2E = os.environ.get("GODSV_DOCKER_E2E", "").strip() not in ("", "0", "false")


@pytest.mark.skipif(not E2E, reason="set GODSV_DOCKER_E2E=1 to run against the real daemon")
@pytest.mark.skipif(shutil.which("docker") is None, reason="docker is not installed")
def test_e2e_mine_register_call_rollback_against_the_real_container(tmp_path):
    """The whole chain on a pinned LOCAL image — no pull, no foreign container."""
    runtime = tmp_path / "rt"
    runtime.mkdir()
    # the ONE place a real manager is wanted — and it is passed explicitly, so
    # the module-wide guard (above) keeps every other test off the daemon
    builder = ContainerToolBuilder(runtime, manager=DockerRuntimeManager())
    image = os.environ.get("GODSV_E2E_IMAGE", "alpine:3.20")
    name = "e2e_container_sha256"
    try:
        mined = builder.mine_container(CAPABILITY, image=image, name=name)
        assert mined["ok"] is True, mined
        registered = builder.register_container_tool(name)
        assert registered["ok"] is True, registered
        assert registered["health_state"] == "HEALTHY"
        call = builder.call_container_tool(name, {"text": "skaldir"})
        assert call["ok"] is True and call["output"] == {"sha256": _sha256_of("skaldir")}
        warm = ensure_container_started(runtime, name, manager=builder.manager())
        assert warm["started"] is True and warm["reused"] is False
    finally:
        result = builder.rollback_container_tool(name, reason="e2e teardown")
        assert result["ok"] is True, result
        # nothing of ours is left behind (the foreign stacks are untouched)
        executor = builder.manager()
        listing = executor.list_by_labels({CAPABILITY_LABEL: name})
        assert listing.payload.get("containers") == []
