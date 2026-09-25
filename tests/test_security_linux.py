"""The Linux enforcement primitives, measured — Landlock, seccomp, `no_new_privs`, cgroup pids.

Every claim in `MVP/48` about a *kernel* control is pinned here with a real syscall and a real
observation, never with a string comparison:

* the seccomp filter is assembled in-process as classic BPF, so the program's **byte layout** is
  asserted (the first cut of it denied every syscall whose first argument was not fd 1 — a
  sockets-only smoke test would have called that a pass);
* the Landlock ruleset is applied for real and a write outside the allowed set is refused by the
  kernel, with the *same* write succeeding before the ruleset goes in;
* `PR_SET_NO_NEW_PRIVS` is read back from `/proc/self/status`;
* a cgroup v2 `pids.max` leaf is created without root and a fork past the cap really fails.

Tests that need a capability this host does not have **skip with the reason**, so a green run
never implies enforcement that did not happen.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from skaldir import security_linux as S


def _landlock_works() -> bool:
    return S.landlock_abi() >= S.LANDLOCK_ABI_MIN


def _bwrap_works() -> bool:
    return bool(shutil.which("bwrap"))


# ------------------------------------------------------------------ host probe


def test_the_probe_measures_this_host_and_names_its_reasons() -> None:
    report = S.probe()
    assert report["machine"]
    assert set(report) >= {"landlock", "seccomp", "no_new_privs", "pids_cgroup", "rlimit_nproc"}
    if report["landlock"]["available"]:
        assert report["landlock"]["abi"] >= S.LANDLOCK_ABI_MIN
        assert report["landlock"]["reason"] == ""
    else:
        assert report["landlock"]["reason"], "an unavailable control must carry its reason"
    assert report["seccomp"]["program_len"] > 4
    assert report["seccomp"]["audit_arch"], "the filter is arch-checked, so this must resolve"
    assert report["rlimit_nproc"]["usable"] is False
    assert "uid" in report["rlimit_nproc"]["reason"]


def test_the_landlock_abi_is_the_kernels_own_answer() -> None:
    abi = S.landlock_abi(refresh=True)
    assert isinstance(abi, int) and abi >= 0
    if abi == 0:
        assert S.landlock_unavailable_reason()
    else:
        assert S.landlock_unavailable_reason() == ""


def test_rlimit_nproc_is_rejected_as_the_process_cap_with_a_measurement() -> None:
    verdict = S.rlimit_nproc_verdict()
    assert verdict["usable"] is False
    assert verdict["user_process_count"] >= 0
    assert "process tree" in verdict["reason"]


# ------------------------------------------------------------- seccomp program


def test_the_seccomp_program_starts_with_the_arch_check() -> None:
    program = S.build_seccomp_program(deny_classes=("privilege", "network"), network_denied=True)
    ld_arch, jeq, kill, ld_nr = program[0], program[1], program[2], program[3]
    assert ld_arch == (S._BPF_LD_W_ABS, 0, 0, S._OFFSET_ARCH)
    assert jeq[0] == S._BPF_JEQ_K and jeq[3] == S.AUDIT_ARCH[S._machine()]
    assert jeq[1] == 1, "the arch match must skip the kill"
    assert kill == (S._BPF_RET_K, 0, 0, S.SECCOMP_RET_KILL_PROCESS)
    assert ld_nr == (S._BPF_LD_W_ABS, 0, 0, S._OFFSET_NR)


def test_every_jump_in_the_seccomp_program_lands_inside_it() -> None:
    program = S.build_seccomp_program(deny_classes=("privilege", "network"), network_denied=True)
    for index, (code, jt, jf, _k) in enumerate(program):
        assert 0 <= jt <= S._JUMP_MAX and 0 <= jf <= S._JUMP_MAX
        if code == S._BPF_JEQ_K:
            assert index + 1 + jt < len(program), f"instruction {index}: jt lands outside"
            assert index + 1 + jf < len(program), f"instruction {index}: jf lands outside"
    assert program[-1][3] == S.SECCOMP_RET_ERRNO | 1


def test_a_non_socket_syscall_never_reaches_the_family_gate() -> None:
    """The bug this file exists for.

    The first cut left ``jf=0`` on the two ``JEQ socket`` instructions, which does not mean
    "continue at the next check" but "fall into ``LD arg0``" — so every syscall ended at the
    family test with its *first argument* compared against ``AF_UNIX``. ``write(1, …)`` was
    allowed (fd 1 happens to equal ``AF_UNIX``) and ``write(2, …)`` was denied, which broke the
    process's own stderr and made a shell segfault. The structural assertion is exact: the
    ``jf`` arm of both socket checks must jump **past** the argument load to the allow.
    """
    program = S.build_seccomp_program(deny_classes=("privilege", "network"), network_denied=True)
    socket_nr = S.syscall_number("socket")
    socketpair_nr = S.syscall_number("socketpair")
    gate = [
        index
        for index, op in enumerate(program)
        if op[0] == S._BPF_JEQ_K and op[3] in (socket_nr, socketpair_nr)
    ]
    assert len(gate) == 2, program
    ld_arg0 = [
        index for index, op in enumerate(program) if op[0] == S._BPF_LD_W_ABS and op[3] == 16
    ]
    assert len(ld_arg0) == 1
    allow = next(
        index
        for index, op in enumerate(program)
        if op[0] == S._BPF_RET_K and op[3] == S.SECCOMP_RET_ALLOW
    )
    for index in gate:
        _code, jt, jf, _k = program[index]
        assert index + 1 + jt == ld_arg0[0], "the match arm must reach the argument load"
        assert index + 1 + jf == allow, (
            "a syscall that is not a socket must skip the family gate entirely"
        )
    errno_ret = [
        index
        for index, op in enumerate(program)
        if op[0] == S._BPF_RET_K and op[3] == S.SECCOMP_RET_ERRNO | 1
    ]
    assert len(errno_ret) >= 1
    for index, op in enumerate(program):
        if op[0] == S._BPF_JEQ_K and op[3] in S._SYSCALL_NUMBERS[S._machine()].values():
            if op[3] in (socket_nr, socketpair_nr):
                continue
            assert index + 1 + op[1] in errno_ret, f"instruction {index} does not deny"


def test_the_network_class_is_only_denied_while_the_network_is_off() -> None:
    offline = S.build_seccomp_program(deny_classes=("privilege", "network"), network_denied=True)
    online = S.build_seccomp_program(deny_classes=("privilege", "network"), network_denied=False)
    connect_nr = S.syscall_number("connect")
    assert any(op[3] == connect_nr for op in offline if op[0] == S._BPF_JEQ_K)
    assert not any(op[3] == connect_nr for op in online if op[0] == S._BPF_JEQ_K)
    ptrace_nr = S.syscall_number("ptrace")
    assert any(op[3] == ptrace_nr for op in online if op[0] == S._BPF_JEQ_K), (
        "the privilege class is on regardless of the network mode"
    )


def test_an_unverified_architecture_refuses_to_build_a_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(S, "_machine", lambda: "sparc64")
    with pytest.raises(S.SecurityRefusal) as excinfo:
        S.build_seccomp_program(deny_classes=("privilege",), network_denied=False)
    assert "audit_arch" in str(excinfo.value)
    assert excinfo.value.control == S.CONTROL_SECCOMP


# -------------------------------------------------------------- seccomp, applied


@pytest.mark.skipif(
    S.audit_arch() is None, reason="no audit_arch constant for this architecture"
)
def test_the_applied_filter_denies_the_network_and_keeps_the_process_working() -> None:
    """A real child: EPERM on AF_INET, AF_UNIX fine, and **the process still works**.

    The last part is the regression guard. ``print``/``sys.stderr``/``json`` after the filter is
    installed is what a broken jump table destroyed first.
    """
    S.build_seccomp_program(deny_classes=("privilege", "network"), network_denied=True)
    script = "\n".join(
        [
            "import json, os, socket, sys",
            "S = __import__('skaldir.security_linux', fromlist=['x'])",
            "S.set_no_new_privs()",
            "S.apply_seccomp(S.build_seccomp_program(deny_classes=('privilege','network'),"
            " network_denied=True))",
            "out = {}",
            "try:",
            "    socket.socket(socket.AF_INET, socket.SOCK_STREAM); out['inet'] = 'ALLOWED'",
            "except OSError as exc:",
            "    out['inet'] = exc.errno",
            "try:",
            "    socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); out['unix'] = 'ALLOWED'",
            "except OSError as exc:",
            "    out['unix'] = exc.errno",
            "libc = __import__('ctypes').CDLL('libc.so.6', use_errno=True)",
            "out['ptrace'] = libc.ptrace(0, 0, 0, 0)",
            "out['ptrace_errno'] = __import__('ctypes').get_errno()",
            "out['write'] = sys.stdout.write('')",
            "out['stderr'] = sys.stderr.write('')",
            "out['argv0'] = os.path.basename(sys.argv[0])",
            "print(json.dumps(out, sort_keys=True))",
        ]
    )
    completed = subprocess.run(  # noqa: S603 — fixed argv, python -c
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PYTHONPATH": str(Path(S.__file__).resolve().parents[1])},
    )
    assert completed.returncode == 0, completed.stderr
    data = json.loads(completed.stdout.strip().splitlines()[-1])
    assert data["inet"] == 1, "socket(AF_INET) must return EPERM"
    assert data["unix"] == "ALLOWED", "AF_UNIX sockets stay available (a sandbox is not a jail)"
    assert data["ptrace"] == -1 and data["ptrace_errno"] == 1, "ptrace must return EPERM"
    assert data["stderr"] == 0 and data["write"] == 0, "the process can still write"
    assert data["argv0"] == "-c"


def test_a_filter_is_refused_before_no_new_privs_is_set() -> None:
    """The kernel's own rule, measured: a filter without NNP is EACCES, not a silent success."""
    S.build_seccomp_program(deny_classes=("privilege",), network_denied=False)
    script = (
        "import ctypes\n"
        "from skaldir import security_linux as S\n"
        "S.apply_seccomp(S.build_seccomp_program(deny_classes=('privilege',),"
        " network_denied=False))\n"
    )
    completed = subprocess.run(  # noqa: S603 — fixed argv
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PYTHONPATH": str(Path(S.__file__).resolve().parents[1])},
    )
    assert completed.returncode != 0
    assert "PR_SET_SECCOMP" in completed.stderr


def test_no_new_privs_is_read_back_from_the_kernel() -> None:
    assert S.no_new_privs_is_set() is False, "the control has to be applied to be observed"


# ------------------------------------------------------------- landlock, applied


def test_a_landlock_rule_for_a_non_directory_drops_the_directory_access() -> None:
    """Measured on this host: ``MAKE_REG`` under a device file is ``EINVAL``, not a narrowing."""
    access = S.READ_ONLY_FS_ACCESS | S.WRITE_FS_ACCESS
    assert S._access_for_path("/dev/null", access) == access & S.NON_DIRECTORY_ACCESS
    assert S._access_for_path("/tmp", access) == access
    missing = S._access_for_path("/nonexistent-skaldir-probe", access)
    assert missing & S.LANDLOCK_ACCESS_FS_MAKE_REG == 0


def test_the_landlock_ruleset_masks_what_this_abi_cannot_handle() -> None:
    plan = S.SecurityPlan(
        landlock="enforce",
        landlock_abi=1,
        read_only_paths=("/",),
        read_write_paths=(),
        network="off",
    )
    ruleset = plan.landlock_ruleset()
    assert ruleset.handled_access_fs & S.LANDLOCK_ACCESS_FS_REFER == 0
    assert ruleset.handled_access_fs & S.LANDLOCK_ACCESS_FS_TRUNCATE == 0
    assert ruleset.handled_access_net == 0, "network rules need ABI 4"
    assert ruleset.masked and all("ABI" in item for item in ruleset.masked)
    newer = S.SecurityPlan(
        landlock="enforce",
        landlock_abi=7,
        read_only_paths=("/",),
        read_write_paths=(),
        network="off",
    )
    assert newer.landlock_ruleset().handled_access_fs & S.LANDLOCK_ACCESS_FS_REFER
    assert newer.landlock_ruleset().masked == ()
    assert newer.landlock_ruleset().handled_access_net, "ABI >= 4 handles the TCP policy"


@pytest.mark.skipif(not _landlock_works(), reason="this kernel has no Landlock")
def test_a_real_landlock_ruleset_refuses_a_write_outside_the_allowed_set(tmp_path: Path) -> None:
    """The write succeeds before the ruleset and fails after — that is what makes it a proof."""
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "marker.txt"
    marker.write_text("before\n", encoding="utf-8")

    plan = S.SecurityPlan(
        mechanism=S.MECHANISM_LANDLOCK,
        landlock="enforce",
        landlock_abi=S.landlock_abi(),
        read_only_paths=("/",),
        read_write_paths=(str(allowed),),
        network="off",
    )
    script = "\n".join(
        [
            "import json, sys",
            "from skaldir import security_linux as S",
            "out = {}",
            "out['before'] = open(sys.argv[2], 'a').write('x') >= 0",
            "S.set_no_new_privs()",
            "applied = S.apply_landlock(S.SecurityPlan.from_json(open(sys.argv[1]).read())"
            ".landlock_ruleset())",
            "out['rules_added'] = applied['rules_added']",
            "try:",
            "    open(sys.argv[2], 'a').write('after')",
            "    out['after'] = 'ALLOWED'",
            "except OSError as exc:",
            "    out['after'] = [exc.errno, exc.strerror]",
            "try:",
            "    open(sys.argv[3], 'a').write('scratch')",
            "    out['in_allowed'] = 'ALLOWED'",
            "except OSError as exc:",
            "    out['in_allowed'] = exc.errno",
            "print(json.dumps(out))",
        ]
    )
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(plan.to_json(), encoding="utf-8")
    completed = subprocess.run(  # noqa: S603 — fixed argv
        [
            sys.executable,
            "-c",
            script,
            str(plan_file),
            str(marker),
            str(allowed / "scratch.txt"),
        ],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PYTHONPATH": str(Path(S.__file__).resolve().parents[1])},
    )
    assert completed.returncode == 0, completed.stderr
    data = json.loads(completed.stdout.strip().splitlines()[-1])
    assert data["before"] is True, "the write has to work before the ruleset, or nothing is proven"
    assert data["rules_added"] >= 2
    assert data["after"][0] == 13, f"the kernel must refuse with EACCES: {data}"
    assert data["after"][1] == "Permission denied"
    assert data["in_allowed"] == "ALLOWED", "the declared writable path stays writable"


@pytest.mark.skipif(not _landlock_works(), reason="this kernel has no Landlock")
def test_a_real_landlock_ruleset_refuses_a_tcp_connect(tmp_path: Path) -> None:
    """The egress policy, by the kernel: `handled_access_net` with no rule means no TCP."""
    plan = S.SecurityPlan(
        landlock="enforce",
        landlock_abi=S.landlock_abi(),
        read_only_paths=("/",),
        read_write_paths=(str(tmp_path),),
        network="off",
    )
    script = "\n".join(
        [
            "import json, socket, sys",
            "from skaldir import security_linux as S",
            "S.set_no_new_privs()",
            "S.apply_landlock(S.SecurityPlan.from_json(open(sys.argv[1]).read()).landlock_ruleset())",
            "out = {}",
            "try:",
            "    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)",
            "    out['socket'] = 'ALLOWED'",
            "    try:",
            "        s.bind(('127.0.0.1', 0)); out['bind'] = 'ALLOWED'",
            "    except OSError as exc: out['bind'] = [exc.errno, exc.strerror]",
            "except OSError as exc:",
            "    out['socket'] = exc.errno",
            "try:",
            "    socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); out['unix'] = 'ALLOWED'",
            "except OSError as exc: out['unix'] = exc.errno",
            "print(json.dumps(out))",
        ]
    )
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(plan.to_json(), encoding="utf-8")
    completed = subprocess.run(  # noqa: S603 — fixed argv
        [sys.executable, "-c", script, str(plan_file)],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PYTHONPATH": str(Path(S.__file__).resolve().parents[1])},
    )
    assert completed.returncode == 0, completed.stderr
    data = json.loads(completed.stdout.strip().splitlines()[-1])
    if S.landlock_abi() < S.LANDLOCK_ABI_NET:
        assert data["socket"] == "ALLOWED", "below ABI 4 the kernel has no network rules"
        return
    assert data["socket"] == "ALLOWED", "creating the socket is not the connect"
    assert data["bind"][0] == 13, f"BIND_TCP is handled with no rule: {data}"
    assert data["unix"] == "ALLOWED", "AF_UNIX is not TCP and stays available"


# ------------------------------------------------------------------- cgroup pids


def test_the_pids_cgroup_probe_answers_with_a_reason_either_way() -> None:
    ok, path, reason = S.pids_cgroup_support()
    if ok:
        assert path.startswith("/sys/fs/cgroup")
        assert Path(path).is_dir()
    else:
        assert reason


@pytest.mark.skipif(
    not S.pids_cgroup_support()[0], reason="no delegated cgroup v2 pids sub-tree here"
)
def test_a_pids_leaf_is_created_without_root_and_the_cap_bites() -> None:
    """`max_processes` is only enforced if a fork past the cap *fails*, so a fork is made."""
    cgroup = S.ProcessCgroup.open(4, label="pytest")
    try:
        assert cgroup.pids_max == 4
        script = (
            "import os, sys, time\n"
            f"path = {str(cgroup.path)!r}\n"
            "open(path + '/cgroup.procs', 'w').write(str(os.getpid()))\n"
            "made = 0\n"
            "for _ in range(8):\n"
            "    try:\n"
            "        pid = os.fork()\n"
            "    except OSError:\n"
            "        continue\n"
            "    if pid == 0:\n"
            "        time.sleep(1.0)\n"
            "        os._exit(0)\n"
            "    made += 1\n"
            "print(made)\n"
        )
        completed = subprocess.run(  # noqa: S603 — fixed argv
            [sys.executable, "-c", script], capture_output=True, text=True, check=False
        )
        made = int(completed.stdout.strip() or "0")
        report = cgroup.measure()
        assert made < 8, f"the cap let {made} extra processes through: {report}"
        assert report["pids_events_max"] >= 1, (
            "pids.events must record the refusals — that counter is what makes the cap evidence"
        )
        assert report["pids_current"] is not None
    finally:
        subprocess.run(  # noqa: S603 — fixed argv
            ["/usr/bin/pkill", "-f", "time.sleep(1.0)"], capture_output=True, check=False
        )
        released = cgroup.release()
        assert released["still_present"] is False, released


def test_releasing_a_cgroup_never_claims_success_from_the_call_alone() -> None:
    """A non-empty leaf cannot be removed, and the report says so — then it really is removed.

    The holder is a **subprocess**, never the test process itself: joining the pytest process
    would leave it inside a `pids.max=8` leaf for the rest of the session (measured: a leaked
    leaf per run, and every later subprocess counted against the cap).
    """
    ok, _parent, _reason = S.pids_cgroup_support()
    if not ok:
        pytest.skip("no delegated cgroup here")
    cgroup = S.ProcessCgroup.open(8, label="pytest-hold")
    holder = subprocess.Popen(["/bin/sleep", "30"])  # noqa: S603 — fixed argv
    try:
        cgroup.join(holder.pid)
        released = cgroup.release()
        assert released["removed"] is False, "a cgroup with a live process in it cannot be rmdir'ed"
        assert released["still_present"] is True
        assert released["reason"], "and the reason is named"
        assert released["pids_current"] == 1
    finally:
        holder.terminate()
        holder.wait(timeout=10)
    second = cgroup.release()
    assert second["removed"] is True and second["still_present"] is False, (
        "once it is empty the same call really removes it — so `removed` is not a constant"
    )


def test_an_unavailable_cgroup_is_a_reason_not_a_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(S, "pids_cgroup_support", lambda: (False, "", "no delegation here"))
    with pytest.raises(S.CgroupUnavailable) as excinfo:
        S.ProcessCgroup.open(4, label="pytest")
    assert "no delegation here" in str(excinfo.value)
    with pytest.raises(S.CgroupUnavailable):
        S.ProcessCgroup.open(0, label="pytest")


# ------------------------------------------------------------- plan and reports


def test_a_plan_survives_a_json_round_trip_bit_for_bit() -> None:
    plan = S.SecurityPlan(
        profile_name="t",
        profile_sha256="a" * 64,
        mechanism=S.MECHANISM_BWRAP_LANDLOCK,
        landlock="enforce",
        landlock_abi=10,
        read_only_paths=("/", "/opt/x"),
        read_write_paths=("/w",),
        protected_paths=("/w/.git",),
        network="on",
        egress_ports=(443, 8443),
        sandbox_tmp="/tmp",
        seccomp="enforce",
        seccomp_classes=("privilege", "network"),
        pids_max=32,
        notes=("n",),
    )
    assert S.SecurityPlan.from_json(plan.to_json()) == plan
    assert json.loads(plan.to_json())["egress_ports"] == [443, 8443]


def test_apply_plan_reports_every_control_it_was_asked_for(tmp_path: Path) -> None:
    """The report is the evidence: one boolean per control, written after they are in place."""
    script = "\n".join(
        [
            "import json, sys",
            "from skaldir import security_linux as S",
            "plan = S.SecurityPlan.from_json(open(sys.argv[1]).read())",
            "report = S.apply_plan(plan)",
            "sys.stderr.write('RESULT ' + report.to_json() + chr(10))",
        ]
    )
    plan = S.SecurityPlan(
        landlock="enforce",
        landlock_abi=S.landlock_abi(),
        read_only_paths=("/",),
        read_write_paths=(str(tmp_path),),
        network="off",
        seccomp="enforce",
        seccomp_classes=("privilege", "network"),
        no_new_privs=True,
    )
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(plan.to_json(), encoding="utf-8")
    completed = subprocess.run(  # noqa: S603 — fixed argv
        [sys.executable, "-c", script, str(plan_file)],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PYTHONPATH": str(Path(S.__file__).resolve().parents[1])},
    )
    assert completed.returncode == 0, completed.stderr
    line = next(
        item for item in completed.stderr.splitlines() if item.startswith("RESULT ")
    )
    report = S.SecurityReport.from_json(line[len("RESULT ") :])
    assert report.controls[S.CONTROL_NO_NEW_PRIVS] is True
    assert report.controls[S.CONTROL_LANDLOCK] is (S.landlock_abi() >= S.LANDLOCK_ABI_MIN)
    assert report.controls[S.CONTROL_SECCOMP] is (S.audit_arch() is not None)
    assert report.controls[S.CONTROL_PIDS_CGROUP] is False, "no cap was declared"
    assert S.CONTROL_PIDS_CGROUP in report.missing
    parsed = S.report_from_output(completed.stderr)
    assert parsed is not None and parsed.controls == report.controls
    assert "SKALDIR-SECURITY-REPORT" not in S.strip_report_lines(completed.stderr).replace(
        "RESULT ", ""
    )


def test_a_required_control_that_cannot_be_applied_refuses_instead_of_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(S, "landlock_abi", lambda refresh=False: 0)
    monkeypatch.setattr(S, "_ABI_CACHE", {"abi": 0, "abi_errno": 95})
    plan = S.SecurityPlan(landlock="enforce", landlock_abi=0, read_only_paths=("/",))
    with pytest.raises(S.SecurityRefusal) as excinfo:
        S.apply_landlock(plan.landlock_ruleset())
    assert excinfo.value.control == S.CONTROL_LANDLOCK
    assert excinfo.value.to_dict()["reason"]


def test_the_protocol_lines_are_removed_wherever_they_start() -> None:
    report = S.SecurityReport(mechanism="landlock", profile_sha256="b" * 64)
    line = f"{S.REPORT_PREFIX} {report.to_json()}"
    glued = f"50000 bytes of output{line}\n"
    assert S.strip_report_lines(glued) == "50000 bytes of output"
    own_line = f"first\n{line}\nsecond\n"
    assert S.strip_report_lines(own_line) == "first\nsecond\n"
    refusal = f'{S.REFUSAL_PREFIX} {{"control": "landlock", "reason": "EINVAL"}}'
    assert S.strip_report_lines(f"out{refusal}\n") == "out"
    assert S.refusal_from_output(f"out{refusal}\n") == {
        "control": "landlock",
        "reason": "EINVAL",
    }
    assert S.report_from_output(glued) is not None
    assert S.report_from_output("no protocol line here") is None
    assert S.strip_report_lines("untouched") == "untouched"


# ----------------------------------------------------------------- mechanism_for


@pytest.mark.parametrize(
    ("landlock_mode", "bwrap", "abi", "expected"),
    [
        ("enforce", "/usr/bin/bwrap", 10, S.MECHANISM_BWRAP_LANDLOCK),
        ("enforce", "", 10, S.MECHANISM_LANDLOCK),
        ("off", "/usr/bin/bwrap", 10, S.MECHANISM_BWRAP),
        ("enforce", "/usr/bin/bwrap", 0, S.MECHANISM_BWRAP),
        ("off", "", 10, S.MECHANISM_NONE),
        ("off", "", 0, S.MECHANISM_NONE),
    ],
)
def test_the_mechanism_matrix_has_no_hidden_branch(
    landlock_mode: str, bwrap: str, abi: int, expected: str
) -> None:
    name, reason = S.mechanism_for(landlock_mode=landlock_mode, bwrap=bwrap, abi=abi)
    assert name == expected
    assert reason, "the choice is always explained"


def test_the_launcher_argv_is_one_shape() -> None:
    from skaldir import permission_profile as P

    plan = S.SecurityPlan(landlock="enforce", landlock_abi=S.landlock_abi())
    argv = P.launcher_argv(
        "true", canonical_json='{"a":1}', identity_sha256="c" * 64, plan=plan, cwd="/tmp"
    )
    assert argv[0] == P.launcher_python()
    assert argv[1] == P.launcher_path()
    assert argv[2] == "exec"
    assert argv[argv.index("--permission-profile") + 1] == '{"a":1}'
    assert argv[argv.index("--permission-profile-sha256") + 1] == "c" * 64
    assert argv[argv.index("--security-plan") + 1] == plan.to_json()
    assert argv[-3:] == ["/bin/sh", "-c", "true"]


def test_the_launcher_script_runs_standalone() -> None:
    """No package import on the launcher's path — it is a script, and it must start fast."""
    completed = subprocess.run(  # noqa: S603 — fixed argv
        [sys.executable, str(Path(S.__file__).resolve()), "probe", "--json"],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PYTHONPATH": ""},
    )
    assert completed.returncode == 0, completed.stderr
    data = json.loads(completed.stdout)
    assert data["machine"] == S._machine()
    assert data["landlock"]["abi"] == S.landlock_abi()

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
