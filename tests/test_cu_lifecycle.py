"""GUI runs clean up **their own** containers — on success, on failure, and never others'.

Evidence class: real execution (a real container per case), skipped when podman or the
image is missing.

Why this file exists: `act run --runner container-gui` used to resolve a runner and never
close it, so every successful run left a `godsv-cu-…` container running `sleep 3600` behind
it (measured live: `godsv-cu-116477-841896 Up 5 seconds` after a run that reported
`verified: true`). Two rules are pinned here:

* a run that resolved its own runner removes exactly that container, and the record says
  whether it is really gone (``cleanup.still_present``) instead of promising it;
* a runner the **caller** supplied is left alone — its lifecycle belongs to the caller, and
  a composite step that started its own container must not have it yanked away;
* the reaper is prefix-scoped: ``own_container_names``/``reap_own_containers`` refuse
  anything that is not a ``godsv-cu-…`` name, so they can never become "clean up everything"
  (the host also runs the owner's own containers, which must survive untouched).
"""

from __future__ import annotations

import shutil
import subprocess
import time

import pytest

from skaldir import act_service as A
from skaldir.computer_use import (
    CONTAINER_PREFIX,
    ContainerRunner,
    own_container_names,
    reap_own_containers,
)

PODMAN = str(shutil.which("podman") or "")
GUI_IMAGE = ContainerRunner.DEFAULT_IMAGE


def _image_present(image: str) -> bool:
    if not PODMAN:
        return False
    return (
        subprocess.run(  # nosec B603 — fixed argv list, no shell  # noqa: S603
            [PODMAN, "image", "exists", image], capture_output=True
        ).returncode
        == 0
    )


def _containers() -> list[str]:
    out = subprocess.run(  # nosec B603 — fixed argv list, no shell  # noqa: S603
        [PODMAN, "ps", "-a", "--format", "{{.Names}}"], capture_output=True, text=True
    ).stdout
    return sorted(line.strip() for line in out.splitlines() if line.strip())


pytestmark = pytest.mark.skipif(
    not _image_present(GUI_IMAGE), reason="the godsv-cu-x11 image is not available"
)


def _launch_action(title: str) -> dict:
    return {
        "kind": "cli",
        "channel": "vision",
        "argv": [
            "sh",
            "-c",
            f"(xmessage -name {title} 'lifecycle' >/dev/null 2>&1 &); sleep 2; echo LAUNCHED",
        ],
        "verify": {"expect_stdout_contains": "LAUNCHED"},
    }


def test_a_verified_gui_run_leaves_no_container_behind(tmp_path) -> None:
    before = set(_containers())
    title = f"godsv-cu-life-{time.time_ns() % 100000}"
    record = A.run_action_list(
        [_launch_action(title)],
        runner_kind="container-gui",
        end_state={"channel": "window_title", "expect_observe_contains": title},
        runtime=tmp_path,
        timeout=120.0,
    )
    assert record["status"] == "verified", record.get("reason")
    cleanup = record["cleanup"]
    assert cleanup["owned"] is True
    assert cleanup["still_present"] is False
    assert cleanup["container"].startswith(CONTAINER_PREFIX)
    after = set(_containers())
    assert cleanup["container"] not in after
    # Nothing the run did not own disappeared: the invariant is "my own container is gone
    # and nobody else's is", not "the host is empty" (this host runs the owner's own
    # containers, and other work may add containers meanwhile).
    assert before - after == set(), (
        f"the run removed container(s) it does not own: {before - after}"
    )


def test_a_failed_gui_run_also_cleans_up(tmp_path) -> None:
    """The end state does not hold, the run fails — and the container is still gone."""
    before = set(_containers())
    record = A.run_action_list(
        [_launch_action(f"godsv-cu-fail-{time.time_ns() % 100000}")],
        runner_kind="container-gui",
        end_state={"channel": "window_title", "expect_observe_contains": "this-is-not-shown"},
        runtime=tmp_path,
        timeout=120.0,
    )
    assert record["status"] == "verification_failed"
    assert record["verified"] is False
    cleanup = record["cleanup"]
    assert cleanup["owned"] is True and cleanup["still_present"] is False
    assert cleanup["container"] not in _containers()
    assert before - set(_containers()) == set()


def test_the_caller_supplied_runner_stays_the_callers(tmp_path) -> None:
    """`owns=False`: the run reports the runner's state but never closes it."""
    runner = ContainerRunner(gui=True, name=f"godsv-cu-caller-{time.time_ns() % 100000}")
    title = f"godsv-cu-caller-win-{time.time_ns() % 100000}"
    try:
        ok, detail = runner.start()
        assert ok, detail
        record = A.run_action_list(
            [_launch_action(title)],
            runner=runner,
            end_state={"channel": "window_title", "expect_observe_contains": title},
            runtime=tmp_path,
            timeout=120.0,
        )
        assert record["status"] == "verified", record.get("reason")
        assert record["cleanup"]["owned"] is False
        assert "caller" in record["cleanup"]["detail"]
        assert runner.name in _containers(), "the caller's container must still be running"
    finally:
        runner.close()
    assert runner.cleanup_record()["still_present"] is False


def test_the_reaper_is_prefix_scoped_and_refuses_foreign_names() -> None:
    with pytest.raises(ValueError):
        own_container_names("penpot-local")
    with pytest.raises(ValueError):
        own_container_names("godsv-cu-")
    with pytest.raises(ValueError):
        reap_own_containers("postgres")


def test_an_unrelated_container_survives_a_gui_run(tmp_path) -> None:
    """The run's cleanup may only ever touch its own name — measured, with a witness."""
    witness = f"cu-witness-{time.time_ns() % 100000}"
    started = subprocess.run(  # nosec B603 — fixed argv list, no shell  # noqa: S603
        [PODMAN, "run", "-d", "--rm", "--name", witness, "docker.io/library/alpine:latest",
         "sleep", "120"],
        capture_output=True,
        text=True,
    )
    if started.returncode != 0:
        pytest.skip(f"could not start the witness container: {started.stderr[:120]}")
    try:
        record = A.run_action_list(
            [_launch_action(f"godsv-cu-witness-{time.time_ns() % 100000}")],
            runner_kind="container-gui",
            end_state={"channel": "window_title", "expect_observe_contains": "godsv-cu-witness-"},
            runtime=tmp_path,
            timeout=120.0,
        )
        assert record["cleanup"]["still_present"] is False
        assert witness in _containers(), "a foreign container was removed by the GUI run"
    finally:
        subprocess.run(  # nosec B603 — fixed argv list, no shell  # noqa: S603
            [PODMAN, "rm", "-f", witness], capture_output=True
        )
