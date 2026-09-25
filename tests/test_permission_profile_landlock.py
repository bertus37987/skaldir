"""The enforcement matrix, real escape attempts, and which mechanism actually ran.

`MVP/43` named four gaps; this file pins their closure with a *run*, and pins the two fallbacks
with the one observation that cannot be faked: **the errno the kernel returns**.

* bubblewrap's mount view refuses a write with ``EROFS`` ("Read-only file system");
* Landlock refuses the same write with ``EACCES`` ("Permission denied").

So "which mechanism ran" is not read from the plan, it is read from the child's own error. The
host control in each case (the same operation succeeding outside the sandbox) is what turns the
observation into a proof.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import threading
from pathlib import Path

import pytest

from skaldir import permission_profile as P
from skaldir import security_linux as S

FAKE_BWRAP_ABSENT = "bwrap is not on PATH here"


def _bwrap_works() -> bool:
    if not shutil.which("bwrap"):
        return False
    return bool(P.probe_backend(P.default_profile("/tmp"))["available"])


def _landlock_works() -> bool:
    return S.landlock_abi() >= S.LANDLOCK_ABI_MIN


needs_bwrap = pytest.mark.skipif(not _bwrap_works(), reason="bubblewrap is not usable here")
needs_landlock = pytest.mark.skipif(not _landlock_works(), reason="this kernel has no Landlock")
needs_cgroup = pytest.mark.skipif(
    not S.pids_cgroup_support()[0], reason="no delegated cgroup v2 pids sub-tree"
)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    (root / ".git").mkdir(parents=True)
    (root / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    return root


@pytest.fixture
def runtime(tmp_path: Path) -> Path:
    home = tmp_path / "rt"
    home.mkdir()
    return home


def _host_writable_probe() -> Path:
    """A path the host user can write but the profile does not declare — the control."""
    probe = Path.home() / f".skaldir-landlock-probe-{os.getpid()}"
    probe.write_text("host marker\n", encoding="utf-8")
    return probe


def test_a_podman_only_host_does_not_get_bubblewrap_flags_or_a_false_mechanism(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`sandbox_backend='auto'` falls back to podman — and podman cannot take bwrap's flags.

    Before this split the mechanism name said `bwrap+landlock` (false) and `sandbox_argv` emitted
    `podman --new-session --die-with-parent …` (cannot run). Now the mount view is either
    bubblewrap's or absent, the mechanism says `landlock`, and the reason names podman so a reader
    knows which front end `backend_binary()` resolved to.
    """
    real_which = shutil.which

    def only_podman(name: str, mode: int = os.F_OK, path: str | None = None) -> str | None:
        if name == "bwrap":
            return None
        if name == "podman":
            return "/usr/bin/podman"
        return real_which(name, mode, path)

    monkeypatch.setattr(shutil, "which", only_podman)
    profile = P.default_profile(workspace)
    assert profile.backend_binary().endswith("podman")
    assert profile.mount_binary() == ""
    mechanism, reason = profile.mechanism()
    expected = S.MECHANISM_LANDLOCK if _landlock_works() else S.MECHANISM_NONE
    assert mechanism == expected, reason
    assert "podman" in reason, "the reason has to say which front end resolved"
    assert P.launcher_path() in profile.sandbox_argv("true")
    assert profile.mount_binary() == "" and "podman" not in profile.sandbox_argv("true")[0]


# --------------------------------------------------------------- the matrix


def test_the_enforcement_matrix_before_and_after_names_every_gap(workspace: Path) -> None:
    """The before/after table as data: one row per gap `MVP/43` recorded, with its owner now."""
    profile = P.default_profile(workspace).with_changes(
        caps=P.ResourceCaps(max_processes=64), network_egress_ports=()
    )
    rows = {row["field"]: row for row in profile.enforcement_matrix()}
    owners = {field: row["enforced_by"] for field, row in rows.items()}

    # (1) no seccomp filter  -> the seccomp row exists and says who owns it
    assert owners["network"].count("seccomp") == (1 if profile.seccomp_mode() == "enforce" else 0)
    # (2) no PR_SET_NO_NEW_PRIVS -> always, and it is not a profile field that can turn it off
    assert "no_new_privs" not in {
        key.split(".")[0] for key in owners
    }, "no_new_privs is not a declared field: it is applied unconditionally"
    with pytest.raises(ValueError, match="cannot be turned off"):
        P.LinuxSecurityPolicy(no_new_privs=False)
    # (3) max_processes unenforced -> the cgroup owns it when declared
    if profile.pids_cgroup_mode()[0] == "enforce":
        assert owners["caps.max_processes"] == "cgroup v2 pids"
    else:
        assert owners["caps.max_processes"] == "unenforced"
    # (4) env policy outside the profile -> it is a field, and the runner applies it
    assert owners["env_policy"] == "runner"
    assert "env_policy" in P.enforcement_matrix_fields()
    # (5) no explicit egress policy -> a declared field with its own owner
    assert "network_egress_ports" in P.enforcement_matrix_fields()
    assert owners["network_egress_ports"] == "metadata", "nothing declared, so nothing claimed"


def test_a_declared_egress_policy_is_owned_by_landlock_not_by_seccomp(workspace: Path) -> None:
    profile = P.default_profile(workspace).with_changes(network="on", network_egress_ports=(8443,))
    row = next(
        item
        for item in profile.enforcement_matrix()
        if item["field"] == "network_egress_ports"
    )
    if _landlock_works():
        assert row["enforced_by"] == "landlock (net)"
        assert "8443" in row["detail"]
        assert "UDP is not covered" in row["detail"], (
            "the honest limit of a TCP-only kernel rule has to be in the row"
        )
    else:
        assert row["enforced_by"] == "unenforced"


def test_an_egress_allowlist_on_a_disabled_network_is_refused_at_construction(
    workspace: Path,
) -> None:
    with pytest.raises(ValueError, match="contradiction"):
        P.default_profile(workspace).with_changes(network="off", network_egress_ports=(443,))


def test_an_out_of_range_port_is_refused(workspace: Path) -> None:
    with pytest.raises(ValueError, match="not a TCP port"):
        P.default_profile(workspace).with_changes(network="on", network_egress_ports=(0,))


# ------------------------------------------------- which mechanism ran, measured


@needs_landlock
@needs_bwrap
def test_the_bwrap_plus_landlock_mechanism_is_the_one_that_runs(
    workspace: Path, runtime: Path
) -> None:
    profile = P.default_profile(workspace)
    assert profile.mechanism()[0] == S.MECHANISM_BWRAP_LANDLOCK
    argv = profile.sandbox_argv("true")
    assert argv[0].endswith("bwrap")
    assert P.launcher_path() in argv and "--security-plan" in argv
    run = P.run_in_sandbox(profile, "true", runtime=runtime)
    assert run.mechanism == S.MECHANISM_BWRAP_LANDLOCK
    assert run.controls_enforced == ["landlock", "no_new_privs", "seccomp"], run.to_dict()
    assert run.security_report["details"]["landlock"]["abi"] == S.landlock_abi()


@needs_landlock
def test_the_landlock_fallback_runs_when_bubblewrap_is_missing(
    workspace: Path, runtime: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fallback path, and the errno that proves *it* was the one enforcing.

    With bubblewrap gone the argv carries no ``bwrap`` at all, and the write outside the declared
    set is refused with ``EACCES``: Landlock's answer, not the mount view's ``EROFS``. A test that
    only checked "it failed" could not tell the two apart.
    """
    real_which = shutil.which

    def without_front_end(name: str, mode: int = os.F_OK, path: str | None = None) -> str | None:
        # both front ends are removed: `auto` would otherwise fall back to podman, and the point
        # of this test is the *no mount view at all* path
        if name in ("bwrap", "podman"):
            return None
        return real_which(name, mode, path)

    monkeypatch.setattr(shutil, "which", without_front_end)
    profile = P.default_profile(workspace)
    mechanism, reason = profile.mechanism()
    assert mechanism == S.MECHANISM_LANDLOCK, reason
    assert "no bubblewrap" in reason
    assert profile.mount_binary() == "", "no bubblewrap means no mount view to point at"
    argv = profile.sandbox_argv("true")
    assert argv[0] == "bwrap", (
        "sandbox_argv() stays the documented bubblewrap shape, so `permission explain` keeps "
        "showing the reader what a bubblewrap front end would be"
    )
    probe_run = P.run_in_sandbox(profile, "true", runtime=runtime)
    assert probe_run.argv[0] == P.launcher_python(), (
        "the run itself has no front end at all: the launcher applies the policy directly"
    )

    probe = _host_writable_probe()
    try:
        run = P.run_in_sandbox(profile, f"printf 'escaped\\n' >> {probe}", runtime=runtime)
        assert run.mechanism == S.MECHANISM_LANDLOCK
        assert run.exit_code != 0
        assert "Permission denied" in run.output, run.output
        assert "Read-only file system" not in run.output, (
            "that would be bubblewrap's answer: the fallback did not run"
        )
        assert probe.read_text() == "host marker\n"
        assert run.controls_enforced == ["landlock", "no_new_privs", "seccomp"]
        # the declared writable path still works — Landlock is allow-by-declaration, not a jail
        inside = P.run_in_sandbox(
            profile, "printf 'inside\\n' > written.txt; cat written.txt", runtime=runtime
        )
        assert inside.exit_code == 0, inside.output
        assert inside.output.strip() == "inside"
    finally:
        probe.unlink(missing_ok=True)


@needs_bwrap
def test_the_bwrap_path_runs_when_landlock_is_off_with_the_other_answer(
    workspace: Path, runtime: Path
) -> None:
    """The other fallback: no Landlock, so bubblewrap carries the policy — ``EROFS`` says so."""
    profile = P.default_profile(workspace).with_changes(
        linux_security=P.LinuxSecurityPolicy(landlock="off")
    )
    assert profile.mechanism()[0] == S.MECHANISM_BWRAP
    probe = _host_writable_probe()
    try:
        run = P.run_in_sandbox(profile, f"printf 'escaped\\n' >> {probe}", runtime=runtime)
        assert run.mechanism == S.MECHANISM_BWRAP
        assert "Read-only file system" in run.output, run.output
        assert run.controls_enforced == ["no_new_privs", "seccomp"], run.to_dict()
        assert run.security_report["notes"], "a control that is off is named in the report"
    finally:
        probe.unlink(missing_ok=True)


def test_landlock_off_and_seccomp_off_leaves_only_the_mount_view(workspace: Path) -> None:
    profile = P.default_profile(workspace).with_changes(
        linux_security=P.LinuxSecurityPolicy(landlock="off", seccomp="off")
    )
    assert profile.seccomp_mode() == "off"
    assert profile.landlock_mode() == "off"
    rows = {row["field"]: row["enforced_by"] for row in profile.enforcement_matrix()}
    assert rows["writable_paths"] == "bwrap argv"
    assert rows["network"] == "bwrap argv"


# ----------------------------------------------------- escape attempt: filesystem


@needs_bwrap
def test_a_write_outside_the_declared_set_is_refused_and_the_host_control_shows_it_would_work(
    workspace: Path, runtime: Path
) -> None:
    probe = _host_writable_probe()
    try:
        subprocess.run(  # noqa: S603 — the control: the same write, outside the sandbox
            ["/bin/sh", "-c", f"printf 'host\\n' >> {probe}"], check=True
        )
        assert probe.read_text().endswith("host\n")
        probe.write_text("host marker\n", encoding="utf-8")
        run = P.run_in_sandbox(
            P.default_profile(workspace), f"printf 'escaped\\n' >> {probe}", runtime=runtime
        )
        assert run.exit_code != 0
        assert probe.read_text() == "host marker\n", "the sandboxed write reached the host"
    finally:
        probe.unlink(missing_ok=True)


@needs_bwrap
def test_a_protected_subpath_stays_read_only_and_landlock_is_not_claimed_for_it(
    workspace: Path, runtime: Path
) -> None:
    """The one thing Landlock structurally cannot do, asserted rather than implied.

    Landlock rules are allow-only, so a carve-out *inside* a writable root needs the mount view.
    The mechanism therefore keeps bubblewrap, and the plan says why instead of pretending.
    """
    before = (workspace / ".git" / "HEAD").read_text()
    run = P.run_in_sandbox(
        P.default_profile(workspace), "printf 'tampered\\n' >> .git/HEAD", runtime=runtime
    )
    assert run.exit_code != 0
    assert (workspace / ".git" / "HEAD").read_text() == before
    plan = P.default_profile(workspace).security_plan()
    assert any("Landlock has no deny rule" in note for note in plan.notes), plan.notes


# --------------------------------------------------------- escape attempt: network


@needs_bwrap
def test_network_egress_is_refused_by_the_kernel_with_the_host_control(
    workspace: Path, runtime: Path
) -> None:
    script = (
        "import socket\n"
        "try:\n"
        "    socket.create_connection(('1.1.1.1', 443), timeout=4)\n"
        "    print('CONNECTED')\n"
        "except OSError as exc:\n"
        "    print('BLOCKED', exc.errno)\n"
    )
    run = P.run_in_sandbox(
        P.default_profile(workspace), "python3 -c \"" + script + "\"", runtime=runtime
    )
    assert "BLOCKED" in run.output.split(), run.output
    if run.controls_enforced and "seccomp" in run.controls_enforced:
        assert "1" in run.output.split(), "seccomp answers with EPERM"
    try:
        socket.create_connection(("1.1.1.1", 443), timeout=4).close()
        host_can_connect = True
    except OSError:
        host_can_connect = False
    if host_can_connect:
        assert "CONNECTED" not in run.output.split(), (
            "the host reaches the same address, so the refusal is the sandbox and not the network"
        )


@needs_landlock
def test_the_declared_egress_allowlist_admits_one_port_and_refuses_another(
    workspace: Path, runtime: Path
) -> None:
    """A real kernel rule for a real port, with a listener for each — allowed and refused.

    Nothing about this test is symbolic: two sockets are listening on the host, the profile allows
    exactly one of the two ports, and the child gets ``EACCES`` for the other one.
    """
    listeners: list[socket.socket] = []
    ports: list[int] = []
    try:
        for _ in range(2):
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.bind(("127.0.0.1", 0))
            server.listen(1)
            listeners.append(server)
            ports.append(server.getsockname()[1])
        allowed_port, other_port = ports[0], ports[1]
        accept_threads = [
            threading.Thread(target=_accept_once, args=(server,), daemon=True)
            for server in listeners
        ]
        for thread in accept_threads:
            thread.start()
        profile = P.default_profile(workspace).with_changes(
            network="on", network_egress_ports=(allowed_port,)
        )
        script = (
            "import socket\n"
            f"for port in ({allowed_port}, {other_port}):\n"
            "    try:\n"
            "        socket.create_connection(('127.0.0.1', port), timeout=3).close()\n"
            "        print(port, 'CONNECTED')\n"
            "    except OSError as exc:\n"
            "        print(port, 'BLOCKED', exc.errno)\n"
        )
        run = P.run_in_sandbox(profile, "python3 -c \"" + script + "\"", runtime=runtime)
        assert f"{allowed_port} CONNECTED" in run.output, run.output
        assert f"{other_port} BLOCKED 13" in run.output, (
            f"the port outside the allowlist must be EACCES from Landlock: {run.output}"
        )
    finally:
        for server in listeners:
            server.close()


def _accept_once(server: socket.socket) -> None:
    try:
        server.settimeout(5)
        connection, _address = server.accept()
        connection.close()
    except OSError:
        return


# ------------------------------------------- escape attempt: privilege escalation


@needs_bwrap
def test_a_setuid_binary_cannot_elevate_inside_the_sandbox_and_the_control_shows_it_would(
    workspace: Path, runtime: Path
) -> None:
    """`PR_SET_NO_NEW_PRIVS`, observed through a real setuid program.

    ``sudo`` reports the flag itself. The host control matters: outside the sandbox the same
    command fails for a *different* reason (a password), so the sandbox refusal is visible as a
    difference rather than a coincidence.
    """
    sudo = shutil.which("sudo")
    if not sudo:
        pytest.skip("no sudo on this host to observe the flag through")
    host = subprocess.run(  # noqa: S603 — the control
        [sudo, "-n", "true"], capture_output=True, text=True, check=False
    )
    assert host.returncode != 0
    assert "no new privileges" not in (host.stderr + host.stdout).lower(), (
        "the host must not already have the flag, or the comparison proves nothing"
    )
    run = P.run_in_sandbox(
        P.default_profile(workspace),
        "grep -i '^NoNewPrivs' /proc/self/status; sudo -n true 2>&1 | head -1",
        runtime=runtime,
    )
    assert "NoNewPrivs:\t1" in run.output, run.output
    assert "no new privileges" in run.output.lower(), run.output
    assert run.controls_enforced and "no_new_privs" in run.controls_enforced


@needs_bwrap
def test_the_capability_set_is_empty_and_no_new_privs_is_readable_in_the_child(
    workspace: Path, runtime: Path
) -> None:
    run = P.run_in_sandbox(
        P.default_profile(workspace),
        "grep -E '^(NoNewPrivs|CapEff|CapPrm|Seccomp)' /proc/self/status",
        runtime=runtime,
    )
    fields = dict(
        line.split(":", 1) for line in run.output.splitlines() if ":" in line
    )
    assert fields.get("NoNewPrivs", "").strip() == "1"
    assert fields.get("CapEff", "").strip() == "0000000000000000", fields
    assert fields.get("Seccomp", "").strip() in ("2", "3"), (
        "mode 2 is a filter: the seccomp row would otherwise be a claim"
    )


# ---------------------------------------------------------------- max_processes


@needs_cgroup
@needs_bwrap
def test_the_process_cap_is_enforced_by_a_cgroup_and_the_counter_shows_it_fired(
    workspace: Path, runtime: Path
) -> None:
    profile = P.default_profile(workspace).with_changes(caps=P.ResourceCaps(max_processes=4))
    # A bounded fork loop: os.fork() raises immediately at the cap, while a shell retries its own
    # fork with backoff and would sit there until the wall clock cap (measured: 120 s).
    script = "\n".join(
        [
            "import os, time",
            "made = 0",
            "for _ in range(12):",
            "    try:",
            "        pid = os.fork()",
            "    except OSError as exc:",
            "        print('fork refused', exc.errno)",
            "        continue",
            "    if pid == 0:",
            "        time.sleep(0.4)",
            "        os._exit(0)",
            "    made += 1",
            "# reap the children before exiting: an orphan keeps the cgroup non-empty and the leaf",
            "# cannot be removed, which would leak a `pids` leaf into the caller's session",
            "while made:",
            "    try:",
            "        os.wait()",
            "    except ChildProcessError:",
            "        break",
            "    made -= 1",
            "print('done')",
        ]
    )
    run = P.run_in_sandbox(profile, "python3 -c \"" + script + "\"", runtime=runtime)
    assert run.exit_code == 0, run.output
    assert "processes" in run.caps_enforced
    assert run.pids_report["pids_max"] == 4
    assert run.pids_report["pids_events_max"] >= 1, run.pids_report
    refusals = run.output.count("fork refused")
    assert refusals >= 1, f"the cap did not bite at pids.max=4: {run.output}"
    assert "fork refused 11" in run.output, (
        f"the kernel refuses a fork past the cap with EAGAIN (11): {run.output}"
    )
    assert "done" in run.output and run.pids_report["removed"] is True, (
        f"the child reaped its processes and the leaf was removed: {run.pids_report}"
    )
    assert "caps.max_processes" not in run.caps_unenforced


def test_an_undeclared_process_cap_stays_unenforced_and_is_named(
    workspace: Path, runtime: Path
) -> None:
    profile = P.default_profile(workspace)
    assert profile.caps.max_processes == 0
    assert "caps.max_processes" in profile.unenforced_fields()
    assert "processes" not in P.run_in_sandbox(profile, "true", runtime=runtime).caps_enforced


# --------------------------------------------------------------- the refusal


def test_a_required_control_that_cannot_be_applied_refuses_with_a_sentence_for_the_agent(
    workspace: Path, runtime: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`require` is a contract, and a broken contract stops the command — before it starts."""
    monkeypatch.setattr(S, "landlock_abi", lambda refresh=False: 0)
    monkeypatch.setattr(S, "_ABI_CACHE", {"abi": 0, "abi_errno": 95})
    profile = P.default_profile(workspace).with_changes(
        linux_security=P.LinuxSecurityPolicy(landlock="require"),
        sandbox_backend="none",
    )
    required = profile.unenforceable_required()
    assert [item["control"] for item in required] == ["landlock"]
    assert "Landlock" in required[0]["reason"] or "landlock" in required[0]["reason"]
    assert required[0]["fix"]

    marker = workspace / "must-not-exist.txt"
    run = P.run_in_sandbox(profile, f"touch {marker}", runtime=runtime)
    assert run.blocked is True and run.exit_code == 3
    assert not marker.exists(), "the command ran despite the refusal"
    assert run.refusal["control"] == "landlock"
    assert run.blocked_reason.startswith("a control this profile requires cannot be applied")
    assert "fix" in run.to_dict()["refusal"]
    assert profile.prompt_block().count("cannot be applied refuses the run") == 1


def test_the_prompt_block_tells_the_agent_every_limit_it_will_run_under(workspace: Path) -> None:
    profile = P.default_profile(workspace).with_changes(
        caps=P.ResourceCaps(max_processes=32),
        allowed_subprocess_prefixes=(("pytest",),),
        network="off",
    )
    block = profile.prompt_block()
    assert len(block) < P.PROMPT_SECTION_BUDGET_CHARS
    lowered = block.lower()
    for expected in (
        "writable:",
        "network: off",
        "environment:",
        "no-new-privileges",
        "seccomp denies",
        "at most 32 processes",
        "mechanism:",
        "approval:",
        "refused",
    ):
        assert expected in lowered or expected in block, f"the block does not mention {expected!r}"
    assert P.permission_profile_hash_from_prompt(block) == profile.identity_sha256
    assert P.permission_profile_hash_from_argv(profile.sandbox_argv("true")) == (
        profile.identity_sha256
    )


# ------------------------------------------------------------------- tampering


@needs_landlock
def test_a_tampered_profile_in_the_argv_is_refused_by_the_child(
    workspace: Path, runtime: Path
) -> None:
    """Non-drift is enforced *at the enforcement site*, not only compared in a test.

    The argv is rewritten so the JSON and the claimed hash disagree — exactly what a wrapper that
    "helpfully" widens a policy would do — and the child refuses before applying anything.
    """
    profile = P.default_profile(workspace)
    argv = profile.sandbox_argv("echo should-not-run")
    plan_index = argv.index("--security-plan")
    plan_json = argv[plan_index + 1]
    json_index = argv.index("--permission-profile")
    widened = P.default_profile(workspace).with_changes(writable_paths=("/",))
    argv = list(argv)
    argv[json_index + 1] = widened.canonical_json()
    assert argv[plan_index + 1] == plan_json, "only the profile was swapped"

    if argv[0].endswith("bwrap"):
        completed = subprocess.run(  # noqa: S603 — the hand-written argv, no shell
            argv, capture_output=True, text=True, check=False, env={"PATH": "/usr/bin:/bin"}
        )
    else:
        completed = subprocess.run(  # noqa: S603 — fixed argv
            argv, capture_output=True, text=True, check=False
        )
    assert completed.returncode == 3, completed.stderr
    refusal = S.refusal_from_output(completed.stdout + completed.stderr)
    assert refusal is not None, completed.stdout + completed.stderr
    assert refusal["control"] == "permission_profile"
    assert "claims profile" in refusal["reason"]
    assert "should-not-run" not in completed.stdout

# ------------------------------------------------------------------- leaf hygiene


@pytest.fixture(autouse=True)
def _no_leaked_cgroup_leaves():
    """Remove only the leaves **this test process** created, and only when they are empty.

    The repository's own rule for a label-scoped teardown: list before, remove by name the delta
    that appeared here, never a foreign entry and never a prune. A `pids` leaf whose holder has
    not reaped its children cannot be removed, so a leftover empty leaf is a *report*, not a
    silent sweep.
    """
    before = _leaf_names()
    yield
    leaked = _leaf_names() - before
    for name in sorted(leaked):
        _remove_leaf_if_empty(name)


def _leaf_names() -> set[str]:
    ok, parent, _reason = S.pids_cgroup_support()
    if not ok:
        return set()
    path = Path(parent)
    return {item.name for item in path.glob("skaldir-*") if item.is_dir()}


def _remove_leaf_if_empty(name: str) -> None:
    ok, parent, _reason = S.pids_cgroup_support()
    if not ok:
        return
    leaf = Path(parent) / name
    try:
        if (leaf / "cgroup.procs").read_text(encoding="utf-8").strip():
            return  # still holding something: not ours to empty
        leaf.rmdir()
    except (OSError, ValueError):
        return
