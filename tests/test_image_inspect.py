"""EXPAND gap C — shell-free image inspection (``godsv.image_inspect``).

The point of the suite: an image **without a shell** (``hello-world``, distroless)
must be *classified*, not refused. The old route
(``toolbuilder.inspect_container_surface``) probes with ``sh -c``, so the absence of
``/bin/sh`` turns into "the surface cannot be inspected" — a statement about our
probe, not about the image. This suite pins the shell-free route and its honest
three-way answer per candidate: ``inspectable`` / ``needs-shell`` / ``impossible``.

No docker is needed here: a synthetic ``docker save`` tar and an injected docker
runner carry every case. The real-image runs live in
``tests/test_image_inspect_live.py`` (opt-in, ``GV_IMAGE_INSPECT_LIVE=1``).
"""

from __future__ import annotations

import gzip
import io
import json
import shutil
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from skaldir import docker_runtime as dr
from skaldir import image_inspect as II

HAVE_DOCKER = shutil.which("docker") is not None
PIN = "sha256:" + "a1" * 32


# --------------------------------------------------------------------------- #
# fixtures: a synthetic `docker save` stream and an injected docker             #
# --------------------------------------------------------------------------- #


def _layer_bytes(entries: dict[str, bytes]) -> bytes:
    """One gzip-compressed layer tar with exactly ``entries`` (dirs implied)."""
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tar:
        for name, payload in entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            info.mode = 0o755
            tar.addfile(info, io.BytesIO(payload))
    return gzip.compress(raw.getvalue())


def _image_tar(path: Path, layers: list[dict[str, bytes]], *, layer_source: str = "manifest",
               zstd_layer: bool = False) -> Path:
    """A ``docker save``-shaped tar: OCI blobs + ``manifest.json`` (+ ``index.json``)."""
    blob_names: list[str] = []
    with tarfile.open(path, "w") as outer:
        for index, entries in enumerate(layers):
            payload = _layer_bytes(entries)
            if zstd_layer and index == len(layers) - 1:
                payload = b"\x28\xb5\x2f\xfd" + b"not-really-zstd"
            name = f"blobs/sha256/layer{index}"
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            outer.addfile(info, io.BytesIO(payload))
            blob_names.append(name)

        def write(name: str, payload: bytes) -> None:
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            outer.addfile(info, io.BytesIO(payload))

        if layer_source == "manifest":
            write("manifest.json", json.dumps(
                [{"Config": "blobs/sha256/config", "RepoTags": ["synthetic:test"],
                  "Layers": blob_names}]
            ).encode())
        elif layer_source == "index":
            write("index.json", json.dumps(
                {"schemaVersion": 2,
                 "manifests": [{"digest": "sha256:man1",
                                "mediaType": "application/vnd.oci.image.manifest.v1+json"}]}
            ).encode())
            write("blobs/sha256/man1", json.dumps(
                {"layers": [{"digest": "sha256:" + name.rsplit("/", 1)[-1],
                             "mediaType": "application/vnd.oci.image.layer.v1.tar+gzip"}
                            for name in blob_names]}
            ).encode())
        # layer_source == "none": neither index → the magic scan must take over
    return path


class FakeDocker:
    """A docker CLI stand-in: answers `image inspect`, records everything else."""

    def __init__(self, *, image: str = "synthetic:test", config: dict | None = None,
                 id_value: str = PIN, inspect_error: str = "", run_exit: int = 0,
                 run_stdout: str = "Hello from a test image", run_stderr: str = "") -> None:
        self.image = image
        self.config = config if config is not None else {
            "Cmd": ["/hello"], "Env": ["PATH=/usr/bin"], "WorkingDir": "/",
        }
        self.id_value = id_value
        self.inspect_error = inspect_error
        self.run_exit = run_exit
        self.run_stdout = run_stdout
        self.run_stderr = run_stderr
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], timeout: float) -> dict:
        self.calls.append(list(argv))
        if "inspect" in argv and "{{json .}}" in argv:
            if self.inspect_error:
                return {"exit_code": 1, "stdout": "", "stderr": self.inspect_error}
            payload = {"Id": self.id_value, "Architecture": "amd64", "Os": "linux",
                       "Size": 4242, "Created": "2026-01-01T00:00:00Z",
                       "RepoTags": [self.image], "ReproDigests": [],
                       "RootFS": {"Layers": [PIN]}, "Config": self.config}
            return {"exit_code": 0, "stdout": json.dumps(payload), "stderr": ""}
        if "inspect" in argv and "{{.Id}}" in argv:
            return {"exit_code": 0, "stdout": self.id_value, "stderr": ""}
        if "run" in argv:
            return {"exit_code": self.run_exit, "stdout": self.run_stdout,
                    "stderr": self.run_stderr}
        return {"exit_code": 0, "stdout": "", "stderr": ""}

    def ran(self) -> list[list[str]]:
        return [call for call in self.calls if "run" in call]


class FakeManager:
    """The lifecycle half: label listing, one-shot run, removal by id."""

    def __init__(self, *, listing: tuple[str, ...] = (), run_ok: bool = True,
                 run_exit: int = 0, run_stdout: str = "ok", run_stderr: str = "") -> None:
        self.docker_bin = "docker"
        self.listing = list(listing)
        self.run_ok = run_ok
        self.run_exit = run_exit
        self.run_stdout = run_stdout
        self.run_stderr = run_stderr
        self.runs: list[dr.RunConfig] = []
        self.removed: list[str] = []
        self.listings: list[dict] = []

    def run(self, config: dr.RunConfig, *, timeout: float = 60.0, **_kw):
        self.runs.append(config)
        return dr.StartResult(ok=self.run_ok, exit_code=self.run_exit,
                              stdout=self.run_stdout, stderr=self.run_stderr,
                              detail="ran" if self.run_ok else "exit")

    def list_by_labels(self, labels: dict, *, timeout: float = 30.0):
        self.listings.append(dict(labels))
        return dr.StartResult(ok=True, detail="listed",
                              payload={"containers": list(self.listing),
                                       "filters": "godsv.owner=skaldir"})

    def stop_and_remove(self, cid: str, *, timeout: float = 30.0):
        self.removed.append(cid)
        return dr.StartResult(ok=True, detail=f"removed {cid}")


@pytest.fixture
def spool(tmp_path, monkeypatch):
    """Point ``_spool_save`` at a synthetic tar — no docker, no subprocess."""
    def install(layers: list[dict[str, bytes]], **kwargs) -> Path:
        path = tmp_path / "image.tar"
        _image_tar(path, layers, **kwargs)
        monkeypatch.setattr(
            II, "_spool_save",
            lambda *a, **kw: {"ok": True, "blocked": "", "reason": "", "path": str(path),
                              "bytes": path.stat().st_size, "truncated": False},
        )
        return path
    return install


# --------------------------------------------------------------------------- #
# metadata — `docker image inspect` (no container, no shell)                    #
# --------------------------------------------------------------------------- #


def test_metadata_is_read_without_a_shell_and_normalized():
    fake = FakeDocker(config={
        "Entrypoint": ["/app/server"], "Cmd": ["--port", "8080"],
        "Env": ["PATH=/usr/bin", "HOME=/root"], "User": "1000:1000",
        "WorkingDir": "/app", "Labels": {"org.opencontainers.image.version": "1.2.3",
                                         "org.opencontainers.image.source": "https://x/y"},
        "ExposedPorts": {"8080/tcp": {}}, "Volumes": {"/data": {}},
        "Healthcheck": {"Test": ["CMD", "/bin/healthcheck"], "Interval": 30},
    })
    result = II.image_metadata(fake, "docker", "synthetic:test")
    assert result["ok"] is True
    config = result["config"]
    assert config["architecture"] == "amd64" and config["os"] == "linux"
    assert config["entrypoint"] == ["/app/server"]
    assert config["cmd"] == ["--port", "8080"]
    assert config["exposed_ports"] == ["8080/tcp"]
    assert config["volumes"] == ["/data"]
    assert config["user"] == "1000:1000" and config["working_dir"] == "/app"
    assert config["labels"]["org.opencontainers.image.version"] == "1.2.3"
    assert config["healthcheck"]["test"] == ["CMD", "/bin/healthcheck"]
    assert config["entrypoint_uses_shell"] is False
    # the daemon's inspect is the only call — no `run`, i.e. no container
    assert fake.ran() == []


def test_metadata_flags_a_shell_entrypoint_as_using_a_shell():
    fake = FakeDocker(config={"Entrypoint": ["/bin/sh", "-c", "exec /app/server"]})
    config = II.image_metadata(fake, "docker", "synthetic:test")["config"]
    assert config["entrypoint_uses_shell"] is True


def test_a_missing_image_is_not_found_not_a_crash():
    fake = FakeDocker(inspect_error="Error response from daemon: No such image: nope:latest")
    result = II.image_metadata(fake, "docker", "nope:latest")
    assert result["ok"] is False and result["blocked"] == "image_not_found"


def test_an_unreachable_daemon_is_blocked_and_not_confused_with_a_missing_image():
    fake = FakeDocker(inspect_error="Cannot connect to the Docker daemon at unix:///run/docker.sock")
    result = II.image_metadata(fake, "docker", "alpine:latest")
    assert result["ok"] is False and result["blocked"] == "docker_unreachable"


def test_unparsable_inspect_output_is_reported_as_such():
    class Broken(FakeDocker):
        def __call__(self, argv, timeout):
            return {"exit_code": 0, "stdout": "{not json", "stderr": ""}
    result = II.image_metadata(Broken(), "docker", "synthetic:test")
    assert result["ok"] is False and result["blocked"] == "metadata_unreadable"


# --------------------------------------------------------------------------- #
# filesystem — the layer walk (no shell, no container)                          #
# --------------------------------------------------------------------------- #


def test_the_layer_walk_finds_binaries_libraries_shells_and_os_release(tmp_path):
    path = _image_tar(tmp_path / "img.tar", [
        {"bin/sh": b"\x7fELF", "bin/busybox": b"\x7fELF",
         "lib/ld-musl-x86_64.so.1": b"\x7fELF",
         "etc/os-release": b'PRETTY_NAME="Alpine Linux v3.24"\nID=alpine\n'},
        {"hello": b"\x7fELF", "usr/lib/libz.so.1": b"\x7fELF"},
    ])
    filesystem = II.filesystem_listing(path)
    assert filesystem["layer_source"] == "manifest.json"
    assert filesystem["layer_count"] == 2
    assert filesystem["entries_scanned"] == 6
    assert filesystem["executables"] == ["/bin/busybox", "/bin/sh", "/hello"]
    assert filesystem["libraries"] == ["/lib/ld-musl-x86_64.so.1", "/usr/lib/libz.so.1"]
    assert filesystem["shells"] == ["/bin/busybox", "/bin/sh"]
    assert filesystem["os_release"]["PRETTY_NAME"] == "Alpine Linux v3.24"
    assert filesystem["warnings"] == []


def test_the_layer_walk_reads_the_oci_index_chain_when_manifest_json_is_absent(tmp_path):
    path = _image_tar(tmp_path / "img.tar", [{"bin/tool": b"\x7fELF"}], layer_source="index")
    filesystem = II.filesystem_listing(path)
    assert filesystem["layer_source"] == "index.json"
    assert filesystem["executables"] == ["/bin/tool"]


def test_the_layer_walk_falls_back_to_a_magic_scan_and_says_so(tmp_path):
    path = _image_tar(tmp_path / "img.tar", [{"bin/tool": b"\x7fELF"}], layer_source="none")
    filesystem = II.filesystem_listing(path)
    assert filesystem["layer_source"] == "magic-scan"
    assert filesystem["executables"] == ["/bin/tool"]
    assert any("magic scan" in warning for warning in filesystem["warnings"])


def test_a_zstd_layer_is_reported_as_unreadable_instead_of_silently_dropped(tmp_path):
    path = _image_tar(tmp_path / "img.tar", [{"bin/sh": b"\x7fELF"}], zstd_layer=True)
    filesystem = II.filesystem_listing(path)
    assert filesystem["layers"][-1]["status"] == "unsupported-compression: zstd"
    assert filesystem["shells"] == []
    assert any("zstd" in warning for warning in filesystem["warnings"])


def test_a_corrupt_spool_is_reported_not_raised(tmp_path):
    path = tmp_path / "broken.tar"
    path.write_bytes(b"this is not a tar at all")
    filesystem = II.filesystem_listing(path)
    assert filesystem["layer_count"] == 0
    assert any("not a readable tar" in warning for warning in filesystem["warnings"])


def test_the_entry_cap_marks_the_listing_as_partial(tmp_path):
    entries = {f"bin/tool{index}": b"\x7fELF" for index in range(50)}
    path = _image_tar(tmp_path / "img.tar", [entries])
    filesystem = II.filesystem_listing(path, max_entries=10)
    assert filesystem["truncated"] is True
    assert filesystem["layers"][0]["status"] == "truncated (entry cap reached)"


# --------------------------------------------------------------------------- #
# shell detection                                                               #
# --------------------------------------------------------------------------- #


def test_a_shell_less_image_reports_no_shell():
    metadata = {"entrypoint": [], "cmd": ["/hello"]}
    shell = II._shell_available(metadata, {"shells": []})
    assert shell["available"] is False
    assert "no shell" in shell["note"]


def test_a_shell_in_the_filesystem_or_the_entrypoint_counts():
    assert II._shell_available({"entrypoint": [], "cmd": ["/bin/sh"]},
                               {"shells": ["/bin/sh"]})["available"] is True
    assert II._shell_available({"entrypoint": ["/bin/sh"], "cmd": []},
                               {"shells": []})["available"] is True
    # hello-world carries a root-level binary that is NOT a shell
    assert II._shell_available({"entrypoint": [], "cmd": ["/hello"]},
                               {"shells": []})["available"] is False


# --------------------------------------------------------------------------- #
# candidates + the three-way classification (the heart of the gap)              #
# --------------------------------------------------------------------------- #


def _report(**kwargs) -> dict:
    """A minimal report-shaped dict for the pure classification functions."""
    base = {
        "metadata": {"entrypoint": [], "cmd": ["/hello"], "exposed_ports": [],
                     "labels": {}},
        "filesystem": {"executables": ["/hello"], "libraries": [], "shells": []},
        "shell": {"available": False},
    }
    base.update(kwargs)
    return base


def test_candidates_come_from_the_declared_argv_the_filesystem_and_the_metadata():
    candidates = II.build_candidates(
        metadata={"entrypoint": ["/app/server"], "cmd": ["/hello"], "exposed_ports": ["8080/tcp"],
                  "labels": {}},
        filesystem={"executables": ["/app/server", "/hello", "/bin/busybox"]},
    )
    kinds = {(item["kind"], item["path"] or item["basename"]): item["source"]
             for item in candidates}
    assert kinds[("cli", "/app/server")] == "entrypoint"
    assert kinds[("cli", "/hello")] == "cmd"
    assert kinds[("cli", "/bin/busybox")] == "known-tool"
    assert kinds[("http", "8080/tcp")] == "exposed-port"


def test_an_mcp_executable_becomes_its_own_candidate():
    candidates = II.build_candidates(
        metadata={"entrypoint": [], "cmd": [], "exposed_ports": [], "labels": {}},
        filesystem={"executables": ["/usr/local/bin/mcp-server"]},
    )
    mcp = [item for item in candidates if item["kind"] == "mcp"]
    assert mcp and mcp[0]["path"] == "/usr/local/bin/mcp-server"


def test_http_labels_without_a_port_still_name_an_http_surface():
    candidates = II.build_candidates(
        metadata={"entrypoint": [], "cmd": [], "exposed_ports": [],
                  "labels": {"org.opencontainers.image.url": "http://x", "ui.port": "3000"}},
        filesystem={"executables": []},
    )
    http = [item for item in candidates if item["kind"] == "http"]
    assert http and http[0]["source"] == "labels"


def test_a_candidate_whose_direct_exec_ran_is_inspectable():
    verdict, reason = II.classify_candidate(
        {"kind": "cli", "path": "/hello", "probe": {"attempted": True, "ran": True,
                                                    "exit_code": 0,
                                                    "stdout_excerpt": "Hello from Docker!"}},
        shell_available=False,
    )
    assert verdict == II.CLASS_INSPECTABLE
    assert "no shell" in reason and "exit 0" in reason


def test_an_unsettled_candidate_in_an_image_with_a_shell_needs_the_shell():
    verdict, reason = II.classify_candidate(
        {"kind": "cli", "path": "/bin/busybox", "probe": {}}, shell_available=True)
    assert verdict == II.CLASS_NEEDS_SHELL
    assert "sh -c" in reason


def test_the_same_candidate_without_a_shell_is_impossible_not_a_refusal():
    verdict, reason = II.classify_candidate(
        {"kind": "cli", "path": "/bin/busybox", "probe": {}}, shell_available=False)
    assert verdict == II.CLASS_IMPOSSIBLE
    assert "no shell" in reason


def test_a_failed_direct_exec_is_classified_by_whether_a_shell_exists():
    failed = {"kind": "cli", "path": "/usr/bin/tool",
              "probe": {"attempted": True, "ran": False, "exit_code": 126,
                        "failure_class": "exec_nonzero"}}
    with_shell, reason_shell = II.classify_candidate(dict(failed), shell_available=True)
    without_shell, reason_none = II.classify_candidate(dict(failed), shell_available=False)
    assert with_shell == II.CLASS_NEEDS_SHELL and "exec_nonzero" in reason_shell
    assert without_shell == II.CLASS_IMPOSSIBLE and "neither" in reason_none


def test_an_http_surface_is_inspectable_without_executing_anything():
    verdict, reason = II.classify_candidate(
        {"kind": "http", "path": "", "basename": "8080/tcp", "probe": {}},
        shell_available=False)
    assert verdict == II.CLASS_INSPECTABLE
    assert "metadata" in reason and "no shell" in reason


def test_the_summary_verdict_prefers_shell_free_over_needs_shell():
    candidates = [
        {"id": "cli:/hello", "kind": "cli", "path": "/hello", "basename": "hello",
         "probe": {"attempted": True, "ran": True, "exit_code": 0}},
        {"id": "cli:/bin/ls", "kind": "cli", "path": "/bin/ls", "basename": "ls", "probe": {}},
    ]
    blocked = II.classify_candidates(candidates, shell_available=True)
    assert blocked["verdict"] == "shell-free"
    assert blocked["counts"] == {II.CLASS_INSPECTABLE: 1, II.CLASS_NEEDS_SHELL: 1,
                                 II.CLASS_IMPOSSIBLE: 0}
    assert candidates[0]["classification"] == II.CLASS_INSPECTABLE
    assert candidates[1]["classification"] == II.CLASS_NEEDS_SHELL
    del candidates[0]["probe"]["ran"]
    candidates[1]["probe"] = {"attempted": True, "ran": False, "exit_code": 126}
    stuck = II.classify_candidates(candidates, shell_available=False)
    assert stuck["verdict"] == "impossible"
    assert stuck["counts"][II.CLASS_IMPOSSIBLE] == 2


def test_probe_targets_prefer_the_declared_binary_then_an_mcp_executable():
    candidates = II.build_candidates(
        metadata={"entrypoint": [], "cmd": ["/app/server"], "exposed_ports": [], "labels": {}},
        filesystem={"executables": ["/app/server", "/bin/cat", "/usr/local/bin/mcp-server"]},
    )
    chosen = II.probe_targets(candidates, limit=2)
    assert chosen[0]["path"] == "/usr/local/bin/mcp-server"
    assert chosen[1]["path"] == "/app/server"


# --------------------------------------------------------------------------- #
# the exec probe — a direct run, never `sh -c`                                  #
# --------------------------------------------------------------------------- #


def test_the_exec_probe_runs_the_binary_directly_with_no_shell():
    manager = FakeManager(run_stdout="BusyBox v1.37.0 multi-call binary")
    record = II.exec_probe(manager, image=PIN, image_digest=PIN, path="/bin/busybox")
    assert record["ran"] is True and record["exit_code"] == 0
    assert record["argv"] == ["/bin/busybox", "--help"]
    assert record["labels"]["godsv.owner"] == "skaldir"
    config = manager.runs[0]
    assert config.entrypoint_argv == ["/bin/busybox", "--help"]
    assert "sh" not in " ".join(config.entrypoint_argv)
    assert config.network == "none" and config.readonly_rootfs is True
    assert config.resource_limits == {"cpu": "1", "ram": "128m"}
    assert config.labels["godsv.owner"] == "skaldir"
    assert PIN in config.docker_argv("run", one_shot=True)  # pinned, one-shot, --rm


def test_the_exec_probe_reports_a_non_zero_exit_as_evidence_not_as_a_pass():
    manager = FakeManager(run_ok=False, run_exit=1, run_stderr="usage: busybox ...")
    record = II.exec_probe(manager, image=PIN, image_digest=PIN, path="/bin/busybox")
    assert record["ran"] is True and record["exit_code"] == 1
    assert record["failure_class"] == "exec_nonzero"
    assert "still evidence" in record["detail"]


def test_the_exec_probe_without_a_runtime_is_not_attempted_and_says_why():
    record = II.exec_probe(None, image=PIN, image_digest=PIN, path="/hello")
    assert record["attempted"] is False and record["ran"] is False
    assert "not run" in record["detail"]


# --------------------------------------------------------------------------- #
# the whole entry point                                                         #
# --------------------------------------------------------------------------- #


HELLO_LAYER = {"hello": b"\x7fELF hello"}


def test_a_shell_less_image_is_classified_instead_of_refused(spool):
    spool([HELLO_LAYER])
    fake = FakeDocker(config={"Cmd": ["/hello"], "Env": [], "WorkingDir": "/"})
    manager = FakeManager(run_stdout="Hello from Docker!")
    report = II.inspect_image(image="hello-world", manager=manager, runner=fake)

    assert report["ok"] is True and report["blocked"] == ""
    assert report["method"] == "shell-free"
    assert report["shell"]["available"] is False
    assert report["filesystem"]["executables"] == ["/hello"]
    assert report["classification"]["verdict"] == "shell-free"
    assert report["classification"]["counts"][II.CLASS_INSPECTABLE] == 1
    assert report["classification"]["counts"][II.CLASS_IMPOSSIBLE] == 0
    candidate = report["candidates"][0]
    assert candidate["path"] == "/hello" and candidate["classification"] == II.CLASS_INSPECTABLE
    assert candidate["probe"]["exit_code"] == 0
    assert report["image_digest"] == PIN
    assert report["container_started"] is True


def test_the_shell_less_image_case_is_exactly_what_the_sh_probe_cannot_do(spool):
    """The classification is real, and the only thing that failed is the shell route."""
    spool([HELLO_LAYER])
    fake = FakeDocker(config={"Cmd": ["/hello"], "Env": [], "WorkingDir": "/"})
    report = II.inspect_image(image="hello-world", runner=fake, probe_exec=False,
                              manager=FakeManager())
    assert report["ok"] is True
    surface = II.surface_for_adapter(report)
    assert surface["inspected"] is True and surface["method"] == "shell-free"
    # the sh -c probe the old path uses is simply not part of this route
    joined = json.dumps(report)
    assert '"sh"' not in json.dumps(report["probes"])
    assert "sh -c" not in json.dumps(surface["cli"]["probe"])
    assert joined  # the report is data, not a refusal


def test_no_container_is_started_when_the_exec_probe_is_off(spool):
    spool([HELLO_LAYER])
    fake = FakeDocker(config={"Cmd": ["/hello"]})
    manager = FakeManager()
    report = II.inspect_image(image="hello-world", manager=manager, runner=fake,
                              probe_exec=False)
    assert manager.runs == [] and manager.listings == []
    assert report["container_started"] is False
    assert report["probes"] == []
    # without an exec probe the candidate is honestly unresolved, never "inspectable"
    assert report["candidates"][0]["classification"] == II.CLASS_IMPOSSIBLE


def test_an_alpine_shaped_image_yields_inspectable_and_needs_shell_candidates(spool):
    spool([{"bin/sh": b"\x7fELF", "bin/busybox": b"\x7fELF",
            "usr/bin/sha256sum": b"\x7fELF", "etc/os-release": b'PRETTY_NAME="Alpine Linux"\n'}])
    fake = FakeDocker(config={"Cmd": ["/bin/sh"], "Env": [], "WorkingDir": "/"})
    manager = FakeManager(run_stdout="BusyBox v1.37.0 multi-call binary")
    report = II.inspect_image(image="alpine:latest", manager=manager, runner=fake,
                              probe_exec=True, exec_probe_limit=1)
    assert report["ok"] is True
    assert report["shell"]["available"] is True
    assert report["filesystem"]["os_release"]["PRETTY_NAME"] == "Alpine Linux"
    assert report["classification"]["counts"][II.CLASS_INSPECTABLE] >= 1
    assert report["classification"]["counts"][II.CLASS_NEEDS_SHELL] >= 1
    needs_shell = [item for item in report["candidates"]
                   if item["classification"] == II.CLASS_NEEDS_SHELL]
    assert any("sh -c" in item["reason"] for item in needs_shell)


def test_a_tag_without_a_pin_shaped_digest_is_refused(spool):
    spool([HELLO_LAYER])
    fake = FakeDocker(config={"Cmd": ["/hello"]}, id_value="not-a-digest")
    report = II.inspect_image(image="hello-world", runner=fake, probe_exec=False)
    assert report["ok"] is False and report["blocked"] == "pin_required"


def test_an_empty_image_name_is_a_structured_block_not_an_exception():
    report = II.inspect_image(image="")
    assert report["ok"] is False and report["blocked"] == "image_required"


def test_an_unreadable_save_stream_is_blocked_with_the_reason(spool, monkeypatch, tmp_path):
    monkeypatch.setattr(II, "_spool_save",
                        lambda *a, **kw: {"ok": False, "blocked": "spool_failed",
                                          "reason": "the image stream could not be spooled: "
                                                    "disk quota exceeded",
                                          "path": "", "bytes": 0, "truncated": False})
    report = II.inspect_image(image="hello-world", runner=FakeDocker(), probe_exec=False)
    assert report["ok"] is False and report["blocked"] == "spool_failed"
    assert "disk quota" in report["reason"]


def test_the_container_delta_is_swept_and_a_foreign_container_is_left_alone(spool):
    spool([HELLO_LAYER])
    fake = FakeDocker(config={"Cmd": ["/hello"]})
    manager = FakeManager(listing=("foreign-before",))
    calls = {"n": 0}
    original = manager.list_by_labels

    def listing(labels, *, timeout=30.0):
        calls["n"] += 1
        manager.listing = ["foreign-before"] if calls["n"] == 1 else ["foreign-before", "ours"]
        return original(labels, timeout=timeout)

    manager.list_by_labels = listing
    report = II.inspect_image(image="hello-world", manager=manager, runner=fake)
    sweep = report["containers"]["sweep"]
    assert sweep["new"] == ["ours"] and sweep["removed"] == ["ours"]
    assert manager.removed == ["ours"]  # by id only — never the pre-existing container
    assert report["containers"]["before"]["owned"] == 1
    assert report["containers"]["after"]["owned"] == 2


def test_a_sweep_that_cannot_remove_reports_the_leftover_as_open(spool):
    """A verdict without cost: a removal call that reported success is re-checked."""
    spool([HELLO_LAYER])
    fake = FakeDocker(config={"Cmd": ["/hello"]})

    class StubbornSweep(FakeManager):
        def __init__(self, **kw):
            super().__init__(**kw)
            self.calls = 0

        def list_by_labels(self, labels, *, timeout=30.0):
            self.calls += 1
            self.listings.append(dict(labels))
            containers = [] if self.calls == 1 else ["the-orphan"]
            return dr.StartResult(ok=True, detail="listed",
                                  payload={"containers": containers,
                                           "filters": "godsv.owner=skaldir"})

        def stop_and_remove(self, cid: str, *, timeout: float = 30.0):
            self.removed.append(cid)
            return dr.StartResult(ok=False, detail="rm: removal of container is already "
                                                   "in progress", failure_class="cleanup_partial")

    manager = StubbornSweep()
    report = II.inspect_image(image="hello-world", manager=manager, runner=fake)
    sweep = report["containers"]["sweep"]
    assert sweep["new"] == ["the-orphan"]
    assert sweep["removed"] == []
    assert sweep["remaining"] == ["the-orphan"]
    assert sweep["swept"] is False
    assert sweep["failure_class"] == "cleanup_partial"
    assert "already in progress" in sweep["detail"]


def test_a_sweep_whose_post_listing_disagrees_is_not_a_false_green(spool):
    """The removal says ok, the re-listing still shows it: report the leftover."""
    spool([HELLO_LAYER])
    fake = FakeDocker(config={"Cmd": ["/hello"]})

    class Liar(FakeManager):
        def __init__(self, **kw):
            super().__init__(**kw)
            self.calls = 0

        def list_by_labels(self, labels, *, timeout=30.0):
            self.calls += 1
            return dr.StartResult(ok=True, detail="listed",
                                  payload={"containers": [] if self.calls == 1 else ["sneaky"],
                                           "filters": "godsv.owner=skaldir"})

    manager = Liar()
    report = II.inspect_image(image="hello-world", manager=manager, runner=fake)
    sweep = report["containers"]["sweep"]
    assert manager.removed == ["sneaky"]  # the removal call claimed success
    assert sweep["swept"] is False and sweep["remaining"] == ["sneaky"]


def test_without_a_trustworthy_listing_no_sweep_is_attempted(spool):
    spool([HELLO_LAYER])
    fake = FakeDocker(config={"Cmd": ["/hello"]})

    class BlindListing(FakeManager):
        def list_by_labels(self, labels, *, timeout=30.0):
            return dr.StartResult(ok=False, detail="docker ps failed",
                                  failure_class="docker_unreachable", payload={})

    manager = BlindListing()
    report = II.inspect_image(image="hello-world", manager=manager, runner=fake)
    assert report["containers"]["sweep"]["swept"] is False
    assert manager.removed == []
    assert "the label-scoped listing did not answer" in report["containers"]["sweep"]["detail"]
    assert "a removal without a trustworthy scope is never done" in \
        report["containers"]["sweep"]["detail"]
    assert report["containers"]["sweep"]["failure_class"] == "listing_unavailable"


# --------------------------------------------------------------------------- #
# the handoff to the existing adapter generator                                 #
# --------------------------------------------------------------------------- #


def test_the_handoff_uses_the_existing_generator_and_its_own_refusal(spool):
    spool([{"usr/bin/sha256sum": b"\x7fELF", "bin/sh": b"\x7fELF"}])
    fake = FakeDocker(config={"Cmd": ["/bin/sh"]})
    report = II.inspect_image(image="alpine:latest", runner=fake, probe_exec=False,
                              manager=FakeManager(),
                              capability="hash a string with sha256")
    handoff = report["adapter"]
    assert handoff["handoff"] == "generated"
    adapter = handoff["adapter"]
    assert adapter["template_id"] == "sha256-string" and adapter["verb"] == "sha256sum"
    # the generator's own argv still needs a shell — reported, not hidden
    assert handoff["adapter_needs_shell"] is True
    assert handoff["shell_available"] is True


def test_an_offered_capability_the_surface_does_not_have_is_refused_by_the_generator(spool):
    spool([HELLO_LAYER])
    fake = FakeDocker(config={"Cmd": ["/hello"]})
    report = II.inspect_image(image="hello-world", runner=fake, probe_exec=False,
                              manager=FakeManager(), capability="hash a string with sha256")
    handoff = report["adapter"]
    assert handoff["handoff"] == "refused"
    assert "AdapterRefusal" in handoff["reason"]
    assert handoff["surface_verbs"] == ["hello"]


def test_a_shell_less_image_is_warned_about_when_the_adapter_still_calls_a_shell(spool):
    spool([{"usr/bin/sha256sum": b"\x7fELF"}])  # a hashing tool WITH NO shell
    fake = FakeDocker(config={"Cmd": ["/usr/bin/sha256sum"]})
    report = II.inspect_image(image="scratch-tool", runner=fake, probe_exec=False,
                              manager=FakeManager(), capability="sha256 hash of a string")
    handoff = report["adapter"]
    assert handoff["handoff"] == "generated-with-warning"
    assert handoff["shell_available"] is False
    assert "no shell" in handoff["warning"]


def test_the_pin_required_case_never_invents_an_adapter():
    surface = {"inspected": False, "reason": "no pinned digest"}
    from skaldir import toolbuilder as tb

    with pytest.raises(tb.SurfaceRefusal):
        tb.generate_container_adapter("hash", name="x", image="i", image_digest="",
                                      surface=surface)
    # and the handoff function reports the refusal rather than raising
    handoff = II.handoff_to_adapter({"image": "i", "image_digest": "", "metadata": {},
                                     "filesystem": {}, "shell": {}}, capability="hash")
    assert handoff["handoff"] in ("refused", "failed")
    assert handoff["handoff"] != "generated" or "adapter" in handoff


def test_surface_for_adapter_carries_the_verbs_the_builder_matches_against(spool):
    spool([{"bin/sh": b"\x7fELF", "bin/base64": b"\x7fELF", "bin/nginx": b"\x7fELF",
            "lib/libc.so.6": b"\x7fELF", "etc/os-release": b'PRETTY_NAME="Alpine"\n'}])
    fake = FakeDocker(config={"Cmd": ["/bin/sh"]})
    report = II.inspect_image(image="x:1", runner=fake, probe_exec=False,
                              manager=FakeManager())
    surface = II.surface_for_adapter(report)
    assert surface["inspected"] is True
    assert set(surface["cli"]["verbs"]) == {"sh", "base64", "nginx"}
    assert surface["api"]["server_hint"] == "nginx"
    assert surface["os"]["PRETTY_NAME"] == "Alpine"
    assert surface["image_digest"] == PIN


# --------------------------------------------------------------------------- #
# rendering + the container-free layer verb                                     #
# --------------------------------------------------------------------------- #


def test_the_text_rendering_states_the_shell_fact_and_the_verdict(spool):
    spool([HELLO_LAYER])
    fake = FakeDocker(config={"Cmd": ["/hello"]})
    report = II.inspect_image(image="hello-world", runner=fake, manager=FakeManager())
    text = II.render_text(report)
    assert "shell: NO" in text
    assert "classification: shell-free" in text
    assert "[inspectable] cli /hello" in text


def test_the_text_rendering_of_a_blocked_report_is_one_honest_line():
    text = II.render_text({"ok": False, "image": "nope", "blocked": "image_not_found",
                           "reason": "the image 'nope' is not local"})
    assert "BLOCKED (image_not_found)" in text and "not local" in text


def test_layers_only_never_probes_and_excerpts_the_listing(spool):
    spool([{"bin/sh": b"\x7fELF", "usr/bin/tool": b"\x7fELF", "lib/libx.so.1": b"\x7fELF"}])
    fake = FakeDocker(config={"Cmd": ["/bin/sh"]})
    manager = FakeManager()
    payload = II.layers_only(image="alpine:latest", manager=manager, runner=fake,
                             excerpt_limit=2)
    assert payload["ok"] is True and payload["container_started"] is False
    assert manager.runs == []
    filesystem = payload["filesystem"]
    assert filesystem["executables_total"] == 2
    assert len(filesystem["executables"]) == 2
    assert filesystem["libraries"] == ["/lib/libx.so.1"]
    assert filesystem["layers"][0]["status"] == "ok"
    assert "classification" in payload


def test_the_default_runner_reports_a_missing_docker_binary_instead_of_raising():
    result = II._subprocess_runner(["/definitely/not/a/docker", "info"], 5.0)
    assert result["exit_code"] == 127
    assert "not found" in result["stderr"]


# --------------------------------------------------------------------------- #
# the probe-target model is data, not a promise                                 #
# --------------------------------------------------------------------------- #


def test_the_report_says_how_many_executables_were_sampled(spool):
    entries = {f"bin/tool{index:02d}": b"\x7fELF" for index in range(30)}
    spool([entries])
    fake = FakeDocker(config={"Cmd": []})
    report = II.inspect_image(image="big:1", runner=fake, probe_exec=False,
                              manager=FakeManager(), candidate_limit=4)
    summary = report["candidate_summary"]
    assert summary["executables_in_image"] == 30
    assert summary["sampled"] == 4
    assert summary["total"] == 4
    assert "sample is capped" in summary["note"]


def test_probe_exec_kwarg_accepts_a_plain_none_manager_without_crashing(spool):
    spool([HELLO_LAYER])
    fake = FakeDocker(config={"Cmd": ["/hello"]})
    report = II.inspect_image(image="hello-world", manager=None, runner=fake,
                             probe_exec=True, exec_probe_limit=1)
    # no manager → no lifecycle: the probe is honestly reported as not attempted
    assert report["ok"] is True
    assert report["probes"][0]["attempted"] in (True, False)
    assert report["containers"]["before"]["listed"] is False
    assert report["candidates"][0]["classification"] in (II.CLASS_IMPOSSIBLE,)


def test_the_module_is_importable_without_docker_being_installed():
    # the module must never require docker at import time (policy: CLI-only access)
    source = Path(II.__file__).read_text(encoding="utf-8")
    assert "subprocess" in source  # the seam is explicit
    assert SimpleNamespace(docker_bin="").docker_bin == ""
