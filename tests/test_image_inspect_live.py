"""EXPAND gap C — live proof: a shell-less image is classified, not refused.

Tier: **opt-in** (``GV_IMAGE_INSPECT_LIVE=1``) plus a docker daemon that answers and
the tiny images already present locally (``hello-world`` 21.8 kB, ``alpine:latest``
13.2 MB). Nothing is pulled here on purpose — a live tier must not depend on the
network budget of whoever runs the suite.

What this file proves, with real containers:

* ``hello-world`` (no shell at all) yields a real classification — ``inspectable``
  for ``/hello`` — instead of the exit-3 refusal the ``sh -c`` route produces;
* ``alpine:latest`` (a shell) yields ``inspectable`` candidates **and** honest
  ``needs-shell`` ones, and the adapter handoff really generates an adapter;
* the owner's 7 foreign ``penpot-local`` containers are untouched and Skaldir leaves
  no container of its own behind (both counted by label, never by name).
"""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from skaldir import docker_runtime as dr
from skaldir import image_inspect as II

HAVE_DOCKER = shutil.which("docker") is not None

HELLO = "hello-world"
ALPINE = "alpine:latest"
OWNER_LABEL = "godsv.owner=skaldir"
FOREIGN_LABEL = "com.docker.compose.project=penpot-local"


def _live_opt_in() -> bool:
    return os.environ.get("GV_IMAGE_INSPECT_LIVE") == "1"


def _daemon_up() -> bool:
    if not HAVE_DOCKER:
        return False
    try:
        return dr.DockerRuntimeManager().available()[0]
    except Exception:  # noqa: BLE001
        return False


def _image_local(image: str) -> bool:
    if not HAVE_DOCKER:
        return False
    proc = subprocess.run(["docker", "image", "inspect", image], capture_output=True, text=True)
    return proc.returncode == 0


def _count_by_label(label: str) -> int:
    """How many containers carry a label — the only scope we ever reason about."""
    proc = subprocess.run(
        ["docker", "ps", "-a", "--filter", f"label={label}", "--format", "{{.ID}}"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return -1
    return len([line for line in proc.stdout.split() if line])


def _ids_by_label(label: str) -> list[str]:
    proc = subprocess.run(
        ["docker", "ps", "-a", "--filter", f"label={label}", "--format", "{{.ID}}"],
        capture_output=True, text=True,
    )
    return [line for line in proc.stdout.split() if line] if proc.returncode == 0 else []


live = pytest.mark.skipif(not _daemon_up() or not _live_opt_in(),
                          reason="live image inspection (opt-in via GV_IMAGE_INSPECT_LIVE=1)")
has_hello = pytest.mark.skipif(not _image_local(HELLO), reason=f"{HELLO} is not local")
has_alpine = pytest.mark.skipif(not _image_local(ALPINE), reason=f"{ALPINE} is not local")


# --------------------------------------------------------------------------- #
# the gap: a shell-less image is described instead of refused                    #
# --------------------------------------------------------------------------- #


@live
@has_hello
def test_hello_world_is_classified_shell_free_instead_of_refused():
    manager = dr.DockerRuntimeManager()
    ours_before = _ids_by_label(OWNER_LABEL)
    foreign_before = _count_by_label(FOREIGN_LABEL)

    report = II.inspect_image(image=HELLO, manager=manager, probe_exec=True,
                              exec_probe_limit=2, timeout=120.0,
                              capability="hash a string with sha256")

    assert report["ok"] is True, report.get("reason")
    assert report["method"] == "shell-free"
    assert report["image_digest"].startswith("sha256:") and len(report["image_digest"]) == 71
    # no shell anywhere: this is exactly the image the sh -c probe cannot handle
    assert report["shell"]["available"] is False
    assert report["filesystem"]["shells"] == []
    assert report["filesystem"]["executables"] == ["/hello"]
    # ... and it is still classified, with real exec evidence
    assert report["classification"]["verdict"] == "shell-free"
    assert report["classification"]["counts"][II.CLASS_INSPECTABLE] == 1
    candidate = report["candidates"][0]
    assert candidate["path"] == "/hello"
    assert candidate["classification"] == II.CLASS_INSPECTABLE
    assert candidate["probe"]["ran"] is True and candidate["probe"]["exit_code"] == 0
    assert "Hello from Docker" in candidate["probe"]["stdout_excerpt"]
    # the adapter generator was consulted and refused honestly (no matching verb)
    assert report["adapter"]["handoff"] == "refused"
    assert report["adapter"]["surface_verbs"] == ["hello"]
    # containment: our probe is gone, the owner's stack is untouched
    assert _count_by_label(FOREIGN_LABEL) == foreign_before
    assert report["containers"]["sweep"]["swept"] is True
    assert sorted(_ids_by_label(OWNER_LABEL)) == sorted(ours_before)


@live
@has_hello
def test_the_shell_free_path_succeeds_where_the_sh_c_probe_refuses():
    """One run, both routes: the contrast the gap is about, measured live."""
    manager = dr.DockerRuntimeManager()
    report = II.inspect_image(image=HELLO, manager=manager, probe_exec=True,
                              exec_probe_limit=1, timeout=120.0, compare_shell=True)

    assert report["ok"] is True
    assert report["classification"]["counts"][II.CLASS_INSPECTABLE] == 1
    baseline = report["baseline_shell_path"]
    assert baseline and baseline["available"] is True
    # the old route needs the shell it does not find → no surface, no adapter
    assert baseline["inspected"] is False
    assert baseline["failure_class"], baseline
    assert "did not run" in baseline["reason"] or "not found" in baseline["reason"].lower()
    # the new route needed no shell for any of it
    assert all("sh" not in (probe.get("argv") or []) for probe in report["probes"])


@live
@has_alpine
def test_alpine_has_a_shell_and_classifies_both_ways():
    manager = dr.DockerRuntimeManager()
    report = II.inspect_image(image=ALPINE, manager=manager, probe_exec=True,
                              exec_probe_limit=3, timeout=180.0,
                              capability="hash a string with sha256")

    assert report["ok"] is True, report.get("reason")
    assert report["shell"]["available"] is True
    assert "/bin/sh" in report["filesystem"]["shells"]
    assert report["classification"]["verdict"] == "shell-free"
    counts = report["classification"]["counts"]
    assert counts[II.CLASS_INSPECTABLE] >= 1
    assert counts[II.CLASS_NEEDS_SHELL] >= 1, "unprobed candidates must stay unresolved"
    # the layer walk really read a distribution: hundreds of binaries, real libraries
    assert len(report["filesystem"]["executables"]) > 200
    assert any(".so" in path for path in report["filesystem"]["libraries"])
    assert "Alpine" in (report["filesystem"]["os_release"].get("PRETTY_NAME") or "")
    # the adapter handoff generated a real adapter from the shell-free surface
    handoff = report["adapter"]
    assert handoff["handoff"] in ("generated", "generated-with-warning")
    adapter = handoff["adapter"]
    assert adapter["verb"] == "sha256sum" and adapter["template_id"] == "sha256-string"
    assert adapter["image_digest"] == report["image_digest"]


@live
@has_alpine
def test_the_layer_listing_excerpt_matches_the_real_filesystem():
    payload = II.layers_only(image=ALPINE, timeout=180.0, excerpt_limit=8)
    assert payload["ok"] is True
    assert payload["container_started"] is False  # this verb never starts a container
    filesystem = payload["filesystem"]
    assert filesystem["layer_source"] in ("manifest.json", "index.json")
    assert filesystem["layer_count"] >= 1
    assert filesystem["entries_scanned"] > 200
    assert "/bin/busybox" in filesystem["executables"]
    assert filesystem["executables_total"] > 200
    assert "/bin/sh" in filesystem["shells"]
    assert filesystem["os_release"]["PRETTY_NAME"].startswith("Alpine Linux")
    # layers only never executes anything: with a shell present every candidate stays
    # unresolved, and "needs-shell" is the honest verdict for that (not a refusal)
    assert payload["classification"]["verdict"] == "needs-shell"
    assert payload["classification"]["counts"]["needs-shell"] >= 1
    assert payload["classification"]["counts"]["inspectable"] == 0


@live
@has_hello
def test_hello_world_layers_are_tiny_and_shell_free():
    payload = II.layers_only(image=HELLO, timeout=120.0, excerpt_limit=4)
    assert payload["ok"] is True
    filesystem = payload["filesystem"]
    assert filesystem["layer_count"] == 1
    assert filesystem["executables"] == ["/hello"]
    assert filesystem["libraries"] == [] and filesystem["shells"] == []


# --------------------------------------------------------------------------- #
# containment: nothing foreign is touched, nothing of ours is left behind        #
# --------------------------------------------------------------------------- #


@live
@has_alpine
def test_no_container_is_left_behind_and_the_owner_stack_is_untouched():
    manager = dr.DockerRuntimeManager()
    ours_before = _ids_by_label(OWNER_LABEL)
    foreign_before = _count_by_label(FOREIGN_LABEL)

    for image in (ALPINE, HELLO):
        if _image_local(image):
            report = II.inspect_image(image=image, manager=manager, probe_exec=True,
                                      exec_probe_limit=2, timeout=180.0)
            assert report["ok"] is True
            sweep = report["containers"]["sweep"]
            assert sweep["swept"] is True
            assert sweep["removed"] == sweep["new"], "every container of this run is gone"

    assert _count_by_label(FOREIGN_LABEL) == foreign_before
    assert sorted(_ids_by_label(OWNER_LABEL)) == sorted(ours_before)


@live
@has_hello
def test_an_exec_probe_of_every_binary_stays_within_the_image_and_the_label_scope():
    manager = dr.DockerRuntimeManager()
    report = II.inspect_image(image=HELLO, manager=manager, probe_exec=True,
                              exec_probe_limit=1, timeout=120.0)
    probe = report["probes"][0]
    assert probe["labels"] == {"godsv.owner": "skaldir", "godsv.purpose": "inspect"}
    assert probe["argv"] == ["/hello", "--help"]
    assert all(part != "sh" and part != "-c" for part in probe["argv"])
