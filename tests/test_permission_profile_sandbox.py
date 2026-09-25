"""The profile is enforced by a REAL bubblewrap run, not by its own argv string.

Every test here executes something inside ``bwrap`` under a profile and measures the result on
this host. The claims that matter:

* a declared writable path is writable **inside**, and the write lands on the host;
* a path the user *can* write on the host but the profile does not declare is **read-only**
  inside (this is the one that distinguishes real enforcement from a permission error: if the
  sandbox were absent, the write would have succeeded);
* a protected subpath stays read-only even under a writable root;
* the network is cut when the profile says ``off`` — and the same connect works on the host,
  which is what makes it a proof rather than a coincidence;
* the resource caps are actually applied by the runner (timeout, output cap, RLIMIT_AS).

When ``bwrap`` is not usable the whole file skips, so a green run never implies enforcement
that did not happen.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
from pathlib import Path

import pytest

from skaldir import permission_profile as P


def _bwrap_works() -> bool:
    if not shutil.which("bwrap"):
        return False
    return bool(P.probe_backend(P.default_profile("/tmp"))["available"])


pytestmark = pytest.mark.skipif(
    not _bwrap_works(), reason="bubblewrap is not usable on this host"
)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    (root / ".git").mkdir(parents=True)
    (root / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (root / "keep.txt").write_text("keep\n", encoding="utf-8")
    return root


@pytest.fixture
def runtime(tmp_path: Path) -> Path:
    home = tmp_path / "rt"
    home.mkdir()
    return home


def test_a_declared_writable_path_is_writable_inside(workspace: Path, runtime: Path) -> None:
    run = P.run_in_sandbox(
        P.default_profile(workspace), "printf 'inside\\n' > written.txt", runtime=runtime
    )
    assert run.sandboxed is True
    assert run.exit_code == 0, run.output
    assert (workspace / "written.txt").read_text() == "inside\n", (
        "the sandbox reported success but nothing reached the host"
    )


def test_a_path_the_profile_does_not_declare_is_read_only_inside(
    workspace: Path, runtime: Path
) -> None:
    """The distinguishing test: this path IS writable on the host, so a missing sandbox shows."""
    probe = Path.home() / f".skaldir-permission-probe-{os.getpid()}"
    probe.write_text("host marker\n", encoding="utf-8")
    try:
        run = P.run_in_sandbox(
            P.default_profile(workspace),
            f"printf 'escaped\\n' >> {probe}",
            runtime=runtime,
        )
        assert run.sandboxed is True
        assert run.exit_code != 0, (
            f"writing outside the declared writable set succeeded: {run.output!r} — the profile "
            f"is not enforced"
        )
        assert "Read-only file system" in run.output or "Permission denied" in run.output
        assert probe.read_text() == "host marker\n", "the host file changed"
    finally:
        probe.unlink(missing_ok=True)


def test_a_protected_subpath_stays_read_only_under_a_writable_root(
    workspace: Path, runtime: Path
) -> None:
    before = (workspace / ".git" / "HEAD").read_text()
    run = P.run_in_sandbox(
        P.default_profile(workspace), "printf 'tampered\\n' >> .git/HEAD", runtime=runtime
    )
    assert run.exit_code != 0
    assert "Read-only file system" in run.output
    assert (workspace / ".git" / "HEAD").read_text() == before


def test_protection_is_a_policy_choice_not_a_hardcode(workspace: Path, runtime: Path) -> None:
    """With the carve-out removed the same write succeeds — so the carve-out is what did it."""
    profile = P.default_profile(workspace).with_changes(protected_paths=())
    run = P.run_in_sandbox(profile, "printf 'ok\\n' >> .git/HEAD", runtime=runtime)
    assert run.exit_code == 0, run.output
    assert (workspace / ".git" / "HEAD").read_text().endswith("ok\n")


def test_the_network_is_cut_when_the_profile_says_off(workspace: Path, runtime: Path) -> None:
    # the script keeps real newlines (wrapped in single quotes for sh) — a repr()'d string
    # would arrive as a literal backslash-n and Python would refuse to parse it
    script = (
        "import socket\n"
        'try:\n'
        '    socket.create_connection(("1.1.1.1", 443), timeout=4)\n'
        '    print("CONNECTED")\n'
        "except OSError as exc:\n"
        '    print("BLOCKED", exc.errno)\n'
    )
    run = P.run_in_sandbox(
        P.default_profile(workspace), "python3 -c '" + script + "'", runtime=runtime
    )
    words = set(run.output.split())
    assert "BLOCKED" in words, f"the sandbox reached the network: {run.output!r}"

    # control: the same connect from the host. Without this the test would pass on a host
    # with no network at all, which would prove nothing about the sandbox.
    try:
        socket.create_connection(("1.1.1.1", 443), timeout=4).close()
        host_can_connect = True
    except OSError:
        host_can_connect = False
    if host_can_connect:
        assert "CONNECTED" not in words


def test_network_on_keeps_the_namespace(workspace: Path, runtime: Path) -> None:
    profile = P.default_profile(workspace).with_changes(network="on")
    assert "--unshare-net" not in profile.sandbox_argv("true")
    run = P.run_in_sandbox(profile, "python3 -c \"print('net namespace kept')\"", runtime=runtime)
    assert run.exit_code == 0, run.output
    assert "net namespace kept" in run.output


def test_the_timeout_cap_is_applied_by_the_runner(workspace: Path, runtime: Path) -> None:
    profile = P.default_profile(workspace).with_changes(caps=P.ResourceCaps(timeout_seconds=1))
    run = P.run_in_sandbox(profile, "sleep 30", runtime=runtime)
    assert run.exit_code == 124, run.output
    assert "timeout" in run.caps_enforced
    assert run.duration_ms < 10_000, "the wall clock cap did not fire"


def test_the_output_cap_truncates_and_spills(workspace: Path, runtime: Path) -> None:
    profile = P.default_profile(workspace).with_changes(
        caps=P.ResourceCaps(output_max_bytes=1000, spill_max_bytes=200000)
    )
    run = P.run_in_sandbox(
        profile,
        "python3 -c \"import sys;sys.stdout.write('x'*50000)\"",
        runtime=runtime,
    )
    assert run.output_truncated is True
    assert "TRUNCATED" in run.output
    assert len(run.output.encode()) < 5000
    assert run.spill_path and Path(run.spill_path).is_file()
    assert Path(run.spill_path).stat().st_size == 50000, "the full output was not spilled"


def test_the_memory_cap_is_a_real_rlimit(workspace: Path, runtime: Path) -> None:
    profile = P.default_profile(workspace).with_changes(
        caps=P.ResourceCaps(max_memory_bytes=128 * 1024 * 1024, timeout_seconds=30)
    )
    run = P.run_in_sandbox(
        profile,
        "python3 -c \"b = bytearray(400 * 1024 * 1024); print('ALLOCATED', len(b))\"",
        runtime=runtime,
    )
    # The word "ALLOCATED" is *always* in the output: CPython's traceback echoes the source
    # line it died on, and that line contains the literal. The fact to test is that the
    # 400 MiB allocation did **not** succeed — the interpreter raised MemoryError (or could
    # not even build the object, which CPython reports as "cannot allocate"), so the
    # success line the program would have printed is absent.
    assert "MemoryError" in run.output or "cannot allocate" in run.output, (
        f"RLIMIT_AS did not bite: {run.output[:300]!r}"
    )
    assert "ALLOCATED 419430400" not in run.output, "the 400 MiB allocation really happened"
    assert run.exit_code != 0
    assert "memory" in run.caps_enforced


def test_the_command_never_runs_when_no_mechanism_is_left(
    workspace: Path, runtime: Path
) -> None:
    """No bubblewrap *and* no kernel controls: nothing is left, so the command does not run.

    ``sandbox_backend='none'`` alone is not this case any more — Landlock still enforces the
    declared set without a binary, and the mechanism says so. This is the combination where the
    profile genuinely asks for no isolation, and a refusal is the only honest answer.
    """
    profile = P.default_profile(workspace).with_changes(
        sandbox_backend="none",
        linux_security=P.LinuxSecurityPolicy(landlock="off", seccomp="off"),
    )
    marker = workspace / "should-not-exist.txt"
    run = P.run_in_sandbox(profile, f"touch {marker}", runtime=runtime)
    assert run.blocked is True
    assert run.mechanism == "none"
    assert not marker.exists()


def test_capability_inventory_matches_the_matrix(workspace: Path, runtime: Path) -> None:
    """What the runner says it enforced and what the matrix says is enforced must agree."""
    run = P.run_in_sandbox(P.default_profile(workspace), "true", runtime=runtime)
    assert "timeout" in run.caps_enforced
    assert "output" in run.caps_enforced
    assert set(run.caps_unenforced) <= set(P.default_profile(workspace).unenforced_fields())


def test_a_read_only_probe_does_not_modify_the_workspace(workspace: Path, runtime: Path) -> None:
    before = sorted(item.name for item in workspace.iterdir())
    digest = subprocess.run(  # noqa: S603 — fixed argv
        ["/usr/bin/find", str(workspace), "-type", "f", "-printf", "%p %s\n"],
        capture_output=True,
        check=False,
    ).stdout
    P.run_in_sandbox(P.default_profile(workspace), "ls -la", runtime=runtime)
    assert sorted(item.name for item in workspace.iterdir()) == before
    after = subprocess.run(  # noqa: S603 — fixed argv
        ["/usr/bin/find", str(workspace), "-type", "f", "-printf", "%p %s\n"],
        capture_output=True,
        check=False,
    ).stdout
    assert after == digest
