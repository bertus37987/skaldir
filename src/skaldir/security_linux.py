"""The Linux enforcement primitives: Landlock, `no_new_privs`, seccomp, cgroup pids.

Why this module exists
----------------------

`MVP/43` documented four gaps in our own permission model, in its own words:

1. **no seccomp filter and no `PR_SET_NO_NEW_PRIVS`** — "a stdlib-only Python runtime has no
   `libseccomp` binding, and inventing a claim would be worse than the gap";
2. **`max_processes` is documentation only** — bubblewrap 0.11 ships no cgroup/pid cap;
3. **no explicit network egress policy** beyond "network off";
4. **the environment policy lives outside the profile.**

This module closes 1–3 at kernel level and gives `permission_profile` the object it needs to
close 4. It is the *mechanism*; the policy stays in :mod:`skaldir.permission_profile`.

The design is mirrored from `openai/codex` (`codex-rs/linux-sandbox/src/landlock.rs`,
`codex-rs/sandboxing/src/landlock.rs` — design only, no code copied, Apache-2.0 upstream; see
`THIRD_PARTY_NOTICES.md` and `MVP/48-security-codex.md`). Codex's three-stage order is kept:

.. code-block:: text

    1. bubblewrap builds the filesystem view            (the outer stage)
    2. in-process: no_new_privs -> Landlock -> seccomp  (THIS MODULE, inside the child)
    3. execvp into the final command

Step 2 runs *after* bubblewrap on purpose: a setuid bubblewrap must still be able to do its
mount work, so the tightening happens only once the filesystem view exists. That is why this
module is invoked as a **launcher** (`exec` subcommand) inside the sandbox rather than applied
with a `preexec_fn` in the parent.

No third-party dependency
-------------------------

Everything here is `ctypes` + the stdlib:

* **Landlock** is three syscalls (`landlock_create_ruleset`, `landlock_add_rule`,
  `landlock_restrict_self`), reachable through `libc.syscall`.
* **seccomp** needs a *classic BPF* program. That is a plain array of 8-byte
  ``struct sock_filter`` records, assembled here by :func:`build_seccomp_program` — no `libseccomp`,
  no C compiler, no compilation step at install time. `MVP/43` assumed this was infeasible; the
  measured answer is in §"What is actually enforced" of `MVP/48`.
* **`PR_SET_NO_NEW_PRIVS`** is one `prctl`.
* **`max_processes`** is a **cgroup v2** leaf in the delegated user sub-tree with `pids.max`
  written to it — no root needed on a systemd host. `RLIMIT_NPROC` is *not* used: it is a
  per-uid counter, so a child limit of N is checked against *every* process the user owns
  (measured: 651 processes alive, `RLIMIT_NPROC = 654` still refused the fork — see
  :func:`rlimit_nproc_verdict`).

Honesty rules this module obeys
-------------------------------

* **A control that cannot be applied is reported, never dropped.** Every step lands in
  :class:`SecurityReport` with its own boolean, and a failure to apply a control that the policy
  required raises :class:`SecurityRefusal` — the launcher then exits 3 *without running the
  command*, and the refusal line names the control, the errno and the fix.
* **`kb_ok` is only ever set by a syscall's own return value.** Nothing here infers success from
  the absence of an error message.
* **The report is the evidence.** The launcher writes one
  ``SKALDIR-SECURITY-REPORT {json}`` line on stderr *after* the restrictions are in place and
  *before* `execvp`, so "what ran" and "what was enforced" are the same observation.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import errno
import json
import os
import re
import stat
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

__all__ = [
    "AF_INET",
    "AF_INET6",
    "AF_UNIX",
    "AUDIT_ARCH",
    "CONTROL_LANDLOCK",
    "CONTROL_NO_NEW_PRIVS",
    "CONTROL_PIDS_CGROUP",
    "CONTROL_SECCOMP",
    "CgroupUnavailable",
    "COVERAGE_STATUSES",
    "EXIT_REFUSED",
    "FULL_FS_ACCESS",
    "KERNEL_REQUIREMENTS",
    "LANDLOCK_ABI_MIN",
    "LIMITS",
    "MECHANISM_BWRAP",
    "MECHANISM_BWRAP_LANDLOCK",
    "MECHANISM_LANDLOCK",
    "MECHANISM_NONE",
    "NET_CLASS_SYSCALLS",
    "PARITY_REQUIREMENTS",
    "PRIMITIVES",
    "PRIVILEGE_CLASS_SYSCALLS",
    "PrimitiveCoverage",
    "ProcessCgroup",
    "READ_ONLY_FS_ACCESS",
    "REPORT_PREFIX",
    "REFUSAL_PREFIX",
    "SECCOMP_MODE_FILTER",
    "STATUS_DEGRADED",
    "STATUS_ENFORCED",
    "STATUS_UNAVAILABLE",
    "SUPPORTED_PLATFORMS",
    "SecurityPlan",
    "SecurityRefusal",
    "SecurityReport",
    "WRITE_FS_ACCESS",
    "apply_landlock",
    "apply_plan",
    "apply_seccomp",
    "build_seccomp_program",
    "coverage_verdict",
    "detected_platform",
    "is_linux",
    "kernel_release",
    "kernel_tuple",
    "landlock_abi",
    "landlock_unavailable_reason",
    "no_new_privs_is_set",
    "mechanism_for",
    "pids_cgroup_support",
    "platform_coverage",
    "platform_report",
    "platform_support",
    "report_from_output",
    "rlimit_nproc_verdict",
    "strip_report_lines",
    "syscall_number",
]

# --------------------------------------------------------------------------- exit code

#: The one exit code a refused *sandbox* uses, before the command can run.
EXIT_REFUSED = 3

#: Every enforcement step the plan can carry. Used as the key set of
#: :attr:`SecurityReport.controls` so a control can never be silently absent.
CONTROL_LANDLOCK = "landlock"
CONTROL_SECCOMP = "seccomp"
CONTROL_NO_NEW_PRIVS = "no_new_privs"
CONTROL_PIDS_CGROUP = "pids_cgroup"
LIMITS = (CONTROL_LANDLOCK, CONTROL_SECCOMP, CONTROL_NO_NEW_PRIVS, CONTROL_PIDS_CGROUP)

#: The mechanism names a resolved plan can carry.
MECHANISM_BWRAP_LANDLOCK = "bwrap+landlock"
MECHANISM_LANDLOCK = "landlock"
MECHANISM_BWRAP = "bwrap"
MECHANISM_NONE = "none"

#: One line the launcher writes to stderr once the restrictions are in place.
REPORT_PREFIX = "SKALDIR-SECURITY-REPORT"
#: One line the launcher writes instead of running the command.
REFUSAL_PREFIX = "SKALDIR-SECURITY-REFUSED"


# ----------------------------------------------------------------- landlock ABI

#: ``landlock_create_ruleset(NULL, 0, LANDLOCK_CREATE_RULESET_VERSION)``.
_LANDLOCK_CREATE_RULESET_VERSION = 1
_LANDLOCK_RULE_PATH_BENEATH = 1
_LANDLOCK_RULE_NET_PORT = 2

#: syscall numbers are identical for x86_64, aarch64, riscv64 and the asm-generic table
#: (444/445/446 since Linux 5.13). A host on another architecture reports the mismatch
#: instead of guessing — see :func:`_syscall_numbers`.
_LANDLOCK_SYSCALLS: tuple[int, int, int] = (444, 445, 446)

LANDLOCK_ABI_MIN = 1
#: ``handled_access_net`` arrived in ABI 4; it is what makes an egress policy enforceable.
LANDLOCK_ABI_NET = 4
#: ``LANDLOCK_ACCESS_FS_IOCTL_DEV`` arrived in ABI 5.
LANDLOCK_ABI_IOCTL_DEV = 5
#: ``LANDLOCK_ACCESS_FS_TRUNCATE`` arrived in ABI 3.
LANDLOCK_ABI_TRUNCATE = 3
#: ``LANDLOCK_ACCESS_FS_REFER`` arrived in ABI 2.
LANDLOCK_ABI_REFER = 2

#: ``landlock_access_fs`` bits (uapi/linux/landlock.h).
LANDLOCK_ACCESS_FS_EXECUTE = 1 << 0
LANDLOCK_ACCESS_FS_WRITE_FILE = 1 << 1
LANDLOCK_ACCESS_FS_READ_FILE = 1 << 2
LANDLOCK_ACCESS_FS_READ_DIR = 1 << 3
LANDLOCK_ACCESS_FS_REMOVE_DIR = 1 << 4
LANDLOCK_ACCESS_FS_REMOVE_FILE = 1 << 5
LANDLOCK_ACCESS_FS_MAKE_CHAR = 1 << 6
LANDLOCK_ACCESS_FS_MAKE_DIR = 1 << 7
LANDLOCK_ACCESS_FS_MAKE_REG = 1 << 8
LANDLOCK_ACCESS_FS_MAKE_SOCK = 1 << 9
LANDLOCK_ACCESS_FS_MAKE_FIFO = 1 << 10
LANDLOCK_ACCESS_FS_MAKE_BLOCK = 1 << 11
LANDLOCK_ACCESS_FS_MAKE_SYM = 1 << 12
LANDLOCK_ACCESS_FS_REFER = 1 << 13
LANDLOCK_ACCESS_FS_TRUNCATE = 1 << 14
LANDLOCK_ACCESS_FS_IOCTL_DEV = 1 << 15

#: What "read a file" means, as one mask (this is the read-only rule's access set).
READ_ONLY_FS_ACCESS = (
    LANDLOCK_ACCESS_FS_EXECUTE | LANDLOCK_ACCESS_FS_READ_FILE | LANDLOCK_ACCESS_FS_READ_DIR
)
#: Everything a writer needs: create, truncate, remove, rename, write.
WRITE_FS_ACCESS = (
    LANDLOCK_ACCESS_FS_WRITE_FILE
    | LANDLOCK_ACCESS_FS_REMOVE_DIR
    | LANDLOCK_ACCESS_FS_REMOVE_FILE
    | LANDLOCK_ACCESS_FS_MAKE_CHAR
    | LANDLOCK_ACCESS_FS_MAKE_DIR
    | LANDLOCK_ACCESS_FS_MAKE_REG
    | LANDLOCK_ACCESS_FS_MAKE_SOCK
    | LANDLOCK_ACCESS_FS_MAKE_FIFO
    | LANDLOCK_ACCESS_FS_MAKE_BLOCK
    | LANDLOCK_ACCESS_FS_MAKE_SYM
    | LANDLOCK_ACCESS_FS_REFER
    | LANDLOCK_ACCESS_FS_TRUNCATE
)
#: The full handled set: read + write + (ioctl on device files, ABI >= 5).
FULL_FS_ACCESS = READ_ONLY_FS_ACCESS | WRITE_FS_ACCESS | LANDLOCK_ACCESS_FS_IOCTL_DEV

#: ``landlock_access_net`` bits (ABI >= 4). Note these are *TCP* only — Landlock has no
#: UDP or raw-socket rule, which is stated in the enforcement matrix rather than implied.
LANDLOCK_ACCESS_NET_BIND_TCP = 1 << 0
LANDLOCK_ACCESS_NET_CONNECT_TCP = 1 << 1

LIBERRNO_UNAVAILABLE = (errno.ENOSYS, errno.EOPNOTSUPP, errno.EINVAL, errno.EPERM)

_ABI_CACHE: dict[str, Any] = {}


def _libc() -> ctypes.CDLL:
    """``libc`` with `use_errno`, resolved once (a failed lookup is not cached as success)."""
    lib = _ABI_CACHE.get("libc")
    if lib is None:
        name = ctypes.util.find_library("c") or "libc.so.6"
        lib = ctypes.CDLL(name, use_errno=True)
        _ABI_CACHE["libc"] = lib
    return lib


def _machine() -> str:
    import platform

    return platform.machine().lower()


# ---------------------------------------------------------------- the platform
#
# Gap 11 (``MVP/84``): every control in this module is a *Linux* control — Landlock, seccomp,
# `PR_SET_NO_NEW_PRIVS`, cgroup v2 `pids.max` and the bubblewrap namespace view exist only on
# Linux, and nothing in the product used to say so. Two things follow from that, and both are
# fixed here rather than documented away:
#
# * the *detection* below is gated on the real platform, so no Landlock/seccomp syscall is ever
#   issued on a kernel that does not have it (on a macOS arm64 host the unguarded code called
#   `syscall(444)` — a *different* syscall on the Darwin table, which is worse than a wrong
#   answer); and
# * :func:`platform_coverage` reports, per primitive, `enforced` / `degraded` / `unavailable`
#   **with the reason**, so a host that cannot enforce says so instead of silently degrading.

#: ``sys.platform`` values we recognise by name. Anything else is reported verbatim.
PLATFORM_ALIASES: dict[str, str] = {
    "linux": "linux",
    "linux2": "linux",
    "linux3": "linux",
    "darwin": "darwin",
    "win32": "win32",
    "cygwin": "cygwin",
    "msys": "msys",
    "emscripten": "emscripten",
}


def detected_platform() -> str:
    """The platform this interpreter really runs on (``sys.platform``, normalised).

    Normalising ``linux2``/``linux3`` matters: only one of the spellings ever appears on a
    modern interpreter, and a check written against the wrong one is a check that never fires.
    """
    return PLATFORM_ALIASES.get(sys.platform, sys.platform)


def is_linux() -> bool:
    """Is this host Linux? **The** gate for every kernel call in this module.

    Never inferred from an environment variable and never from the machine name: a Linux-only
    syscall number means something else on another kernel, so the platform of the *running*
    interpreter is the only admissible answer.
    """
    return detected_platform() == "linux"


def kernel_release() -> str:
    """The kernel release string (``""`` where ``os.uname`` does not exist, e.g. Windows)."""
    try:
        return str(os.uname().release)
    except (AttributeError, OSError):  # pragma: no cover — Windows has no os.uname
        return ""


def kernel_tuple(release: str | None = None) -> tuple[int, int, int] | None:
    """``(major, minor, patch)`` from a release string, or ``None`` when it is not numeric.

    ``7.2.5-200.fc44.x86_64`` → ``(7, 2, 5)``. A non-Linux or unparsable string returns ``None``
    so a caller reports "cannot be compared" instead of comparing nonsense.
    """
    text = kernel_release() if release is None else str(release)
    head = text.split("-", 1)[0].strip()
    parts = head.split(".")
    if not parts or not parts[0].isdigit():
        return None
    numbers = [int(part) if part.isdigit() else 0 for part in parts[:3]]
    while len(numbers) < 3:
        numbers.append(0)
    return numbers[0], numbers[1], numbers[2]


#: The kernel version each control needs. Measured requirements, one per primitive, so a reader
#: can check their own machine instead of taking a product claim on trust.
KERNEL_REQUIREMENTS: dict[str, str] = {
    "landlock": "Linux 5.13+ (Landlock ABI 1); 5.19+ (ABI 4) for the TCP rules",
    "seccomp": "Linux 3.5+ (SECCOMP_MODE_FILTER, unprivileged); any kernel we support",
    "no_new_privs": "Linux 3.5+ (PR_SET_NO_NEW_PRIVS)",
    "namespaces": "Linux user/pid/net/mount namespaces (unprivileged user namespaces enabled)",
    "pids_cgroup": "Linux cgroup v2 with the pids controller, delegated to the user session",
    "rlimits": "POSIX setrlimit(RLIMIT_AS) — Linux and macOS; absent on Windows",
}


def _syscall_numbers() -> tuple[int, int, int] | None:
    """The three Landlock syscall numbers, or ``None`` on an architecture we did not verify.

    Only the architectures whose tables actually carry 444/445/446 are accepted; anything else
    returns ``None`` so the caller reports "not supported here" instead of calling a foreign
    syscall number (which on a different table would be a *different* syscall).
    """
    machine = _machine()
    if machine in ("x86_64", "amd64", "aarch64", "arm64", "riscv64", "loongarch64"):
        return _LANDLOCK_SYSCALLS
    return None


def landlock_abi(*, refresh: bool = False) -> int:
    """The Landlock ABI the running kernel supports, or ``0`` when it supports none.

    Measured, never assumed: the answer is the kernel's own return value from
    ``landlock_create_ruleset(NULL, 0, LANDLOCK_CREATE_RULESET_VERSION)``. A failure is *not*
    cached across processes (there is nothing to cache — the answer is a property of the
    running kernel), but it is cached for this process because the kernel cannot change under
    it.
    """
    if not refresh and "abi" in _ABI_CACHE:
        return int(_ABI_CACHE["abi"])
    if not is_linux():
        # The three Landlock syscall numbers are Linux numbers. On another kernel they are
        # *different* syscalls (Darwin has its own table at the same indices), so this returns
        # "no Landlock" **without issuing the call** — an honest 0, never a foreign syscall.
        _ABI_CACHE["abi"] = 0
        _ABI_CACHE["abi_reason"] = (
            f"Landlock is a Linux-only kernel feature; this platform is {detected_platform()}"
        )
        return 0
    if not _machine().startswith(("x86_64", "amd64", "aarch64", "arm64", "riscv64", "loongarch")):
        _ABI_CACHE["abi"] = 0
        _ABI_CACHE["abi_reason"] = f"unverified syscall table for {_machine()}"
        return 0
    numbers = _syscall_numbers()
    if numbers is None:
        _ABI_CACHE["abi"] = 0
        return 0
    lib = _libc()
    ctypes.set_errno(0)
    result = lib.syscall(numbers[0], None, 0, _LANDLOCK_CREATE_RULESET_VERSION)
    if result < 0:
        _ABI_CACHE["abi"] = 0
        _ABI_CACHE["abi_errno"] = ctypes.get_errno()
        return 0
    _ABI_CACHE["abi"] = int(result)
    return int(result)


def landlock_unavailable_reason() -> str:
    """Why :func:`landlock_abi` answered ``0`` — the sentence the report carries."""
    landlock_abi()
    if _ABI_CACHE.get("abi"):
        return ""
    err = _ABI_CACHE.get("abi_errno")
    if err:
        return f"landlock_create_ruleset returned errno {err} ({os.strerror(err)})"
    return str(_ABI_CACHE.get("abi_reason") or "the kernel reports no Landlock ABI")


# ------------------------------------------------------------------- structures


class _RulesetAttr(ctypes.Structure):
    """``struct landlock_ruleset_attr`` — 24 bytes on ABI >= 6, shorter ones are accepted."""

    _fields_ = [
        ("handled_access_fs", ctypes.c_uint64),
        ("handled_access_net", ctypes.c_uint64),
        ("scoped", ctypes.c_uint64),
    ]


class _PathBeneathAttr(ctypes.Structure):
    """``struct landlock_path_beneath_attr`` — the parent fd plus the allowed access."""

    _fields_ = [
        ("allowed_access", ctypes.c_uint64),
        ("parent_fd", ctypes.c_int32),
        ("reserved", ctypes.c_uint32),
    ]


class _NetPortAttr(ctypes.Structure):
    """``struct landlock_net_port_attr`` — ABI >= 4. Port 0 means "every port"."""

    _fields_ = [("allowed_access", ctypes.c_uint64), ("port", ctypes.c_uint64)]


class SecurityRefusal(RuntimeError):
    """A control the policy *required* could not be applied — the command must not run."""

    def __init__(self, control: str, reason: str, *, fix: str = "") -> None:
        self.control = str(control)
        self.reason = str(reason)
        self.fix = str(fix)
        text = f"{self.control}: {self.reason}"
        if self.fix:
            text += f" — {self.fix}"
        super().__init__(text)

    def to_dict(self) -> dict[str, str]:
        return {"control": self.control, "reason": self.reason, "fix": self.fix}


#: The ``allowed_access`` bits a **non-directory** parent accepts. The directory-only bits
#: (``MAKE_*``, ``REMOVE_*``) are ``EINVAL`` on a file, a device or a fifo — measured, see
#: :meth:`SecurityPlan.landlock_ruleset`.
NON_DIRECTORY_ACCESS = (
    LANDLOCK_ACCESS_FS_EXECUTE
    | LANDLOCK_ACCESS_FS_WRITE_FILE
    | LANDLOCK_ACCESS_FS_READ_FILE
    | LANDLOCK_ACCESS_FS_TRUNCATE
    | LANDLOCK_ACCESS_FS_IOCTL_DEV
)


def _mask_access(access: int, abi: int) -> tuple[int, tuple[str, ...]]:
    """Drop access bits the kernel's ABI does not know, and *name* every bit dropped.

    This is Codex's `CompatLevel::BestEffort` with the one thing it lacks: the masked bits are
    returned as data, so a report can say "REFER/TRUNCATE are handled but this kernel cannot
    enforce them" instead of silently narrowing the policy.
    """
    unsupported: list[tuple[int, int, str]] = [
        (LANDLOCK_ACCESS_FS_REFER, LANDLOCK_ABI_REFER, "REFER"),
        (LANDLOCK_ACCESS_FS_TRUNCATE, LANDLOCK_ABI_TRUNCATE, "TRUNCATE"),
        (LANDLOCK_ACCESS_FS_IOCTL_DEV, LANDLOCK_ABI_IOCTL_DEV, "IOCTL_DEV"),
    ]
    masked: tuple[str, ...] = ()
    for bit, needed, name in unsupported:
        if access & bit and abi < needed:
            access &= ~bit
            masked += (f"{name} (needs ABI {needed})",)
    return access, masked


# -------------------------------------------------------------------- seccomp


class _SockFilter(ctypes.Structure):
    """``struct sock_filter`` — one classic-BPF instruction, 8 bytes."""

    _fields_ = [
        ("code", ctypes.c_uint16),
        ("jt", ctypes.c_uint8),
        ("jf", ctypes.c_uint8),
        ("k", ctypes.c_uint32),
    ]


class _SockFprog(ctypes.Structure):
    _fields_ = [("len", ctypes.c_ushort), ("filter", ctypes.POINTER(_SockFilter))]


#: cBPF opcodes (linux/bpf_common.h).
_BPF_LD_W_ABS = 0x20
_BPF_JEQ_K = 0x15
_BPF_RET_K = 0x06

#: seccomp return actions (linux/seccomp.h).
SECCOMP_RET_KILL_PROCESS = 0x80000000
SECCOMP_RET_ERRNO = 0x00050000
SECCOMP_RET_ALLOW = 0x7FFF0000

SECCOMP_MODE_FILTER = 2

#: ``jt``/``jf`` are 8-bit: a longer program cannot express its jumps, so assembly refuses.
_JUMP_MAX = 255

#: ``offsetof(struct seccomp_data, arch)`` / ``args`` — the two fields a filter reads.
_OFFSET_ARCH = 4
_OFFSET_NR = 0
_OFFSET_ARG0 = 16

#: ``audit_arch`` values. A filter that does not check this can be bypassed with a
#: 32-bit syscall entry on a 64-bit kernel, so the check is not optional.
AUDIT_ARCH = {
    "x86_64": 0xC000003E,
    "amd64": 0xC000003E,
    "aarch64": 0xC000003E | 0x40,  # AUDIT_ARCH_AARCH64 = EM_AARCH64|__AUDIT_ARCH_64BIT|LE
    "arm64": 0xC000003E | 0x40,
    "riscv64": 0xC000003E | 0xF3,
}

AF_UNIX, AF_INET, AF_INET6 = 1, 2, 10

#: x86_64 syscall numbers for the classes we deny. Resolved per architecture by
#: :func:`syscall_number`; a syscall with no number on the running architecture is *named* as
#: unenforceable rather than dropped.
_SYSCALL_NUMBERS: dict[str, dict[str, int]] = {
    "x86_64": {
        "ptrace": 101,
        "process_vm_readv": 310,
        "process_vm_writev": 311,
        "io_uring_setup": 425,
        "io_uring_enter": 426,
        "io_uring_register": 427,
        "socket": 41,
        "socketpair": 53,
        "connect": 42,
        "accept": 43,
        "accept4": 288,
        "bind": 49,
        "listen": 50,
        "getpeername": 52,
        "getsockname": 51,
        "shutdown": 48,
        "sendto": 44,
        "sendmmsg": 307,
        "recvmmsg": 299,
        "getsockopt": 55,
        "setsockopt": 54,
    },
    "aarch64": {
        "ptrace": 117,
        "process_vm_readv": 270,
        "process_vm_writev": 271,
        "io_uring_setup": 425,
        "io_uring_enter": 426,
        "io_uring_register": 427,
        "socket": 198,
        "socketpair": 199,
        "connect": 203,
        "accept": 202,
        "accept4": 242,
        "bind": 200,
        "listen": 201,
        "getpeername": 205,
        "getsockname": 204,
        "shutdown": 210,
        "sendto": 206,
        "sendmmsg": 269,
        "recvmmsg": 243,
        "getsockopt": 209,
        "setsockopt": 208,
    },
}
_SYSCALL_NUMBERS["arm64"] = _SYSCALL_NUMBERS["aarch64"]

#: The **privilege** class: introspection of another process, plus the io_uring path that
#: reaches AF_VSOCK without a `socket()` call. Mirrors Codex's always-on denies.
PRIVILEGE_CLASS_SYSCALLS: tuple[str, ...] = (
    "ptrace",
    "process_vm_readv",
    "process_vm_writev",
    "io_uring_setup",
    "io_uring_enter",
    "io_uring_register",
)

#: The **network** class: every way to reach a peer, applied only while the network is off.
#: `recvfrom` is deliberately *not* denied — some tools (cargo, `multiprocessing`) need it on a
#: socketpair with a child; Codex makes the same call and says so in a comment.
NET_CLASS_SYSCALLS: tuple[str, ...] = (
    "connect",
    "accept",
    "accept4",
    "bind",
    "listen",
    "getpeername",
    "getsockname",
    "shutdown",
    "sendto",
    "sendmmsg",
    "recvmmsg",
    "getsockopt",
    "setsockopt",
)


def syscall_number(name: str) -> int | None:
    """The number of ``name`` on this architecture, or ``None`` when it has none here."""
    table = _SYSCALL_NUMBERS.get(_machine())
    if table is None:
        return None
    return table.get(str(name))


def build_seccomp_program(
    *,
    deny_classes: tuple[str, ...],
    network_denied: bool = True,
    allow_unix_sockets: bool = True,
) -> tuple[tuple[int, int, int, int], ...]:
    """Assemble the filter as plain ``(code, jt, jf, k)`` tuples — the testable form.

    Layout is fixed, so the jump arithmetic is done once, in one place:

    .. code-block:: text

        0   LD  arch
        1   JEQ audit_arch            jt -> 3,  jf -> 2
        2   RET KILL_PROCESS
        3   LD  nr
        4…N JEQ <denied nr>           jt -> idx_errno, jf -> next
        N+1 JEQ socket                jt -> idx_arg0,  jf -> idx_allow
        N+2 JEQ socketpair            jt -> idx_arg0,  jf -> idx_allow
        N+3 LD  arg0
        N+4 JEQ AF_UNIX               jt -> idx_allow, jf -> idx_errno_gate
        N+5 RET ERRNO(EPERM)          the non-AF_UNIX socket family
        N+6 RET ALLOW                 every syscall that was not denied
        N+7 RET ERRNO(EPERM)          the plain denies' shared target

    **Both** arms of every socket-gate jump are explicit on purpose. The first cut of this
    function left ``jf=0`` on the two ``JEQ socket`` instructions, which does not mean "continue
    at the next check" but "fall into ``LD arg0``": every syscall then reached the family test
    with its *first argument* (a file descriptor!) compared against ``AF_UNIX``, so
    ``write(2, …)`` — fd 2 ≠ 1 — was denied while ``write(1, …)`` was allowed because fd 1
    happens to equal ``AF_UNIX``. A program that "looks right" and denies almost everything is
    exactly the failure a unit test of the byte layout catches and a sockets-only smoke test
    does not; :func:`tests.test_security_linux` pins the tail shape.
    """
    arch = AUDIT_ARCH.get(_machine())
    if arch is None:
        raise SecurityRefusal(
            CONTROL_SECCOMP,
            f"no audit_arch constant for {_machine()}: an arch-checked filter cannot be built",
            fix="declare linux_security.seccomp = 'off' for this host",
        )
    names: list[str] = list(PRIVILEGE_CLASS_SYSCALLS) if "privilege" in deny_classes else []
    if network_denied and "network" in deny_classes:
        names += list(NET_CLASS_SYSCALLS)
    wanted: list[tuple[str, int]] = []
    for name in dict.fromkeys(names):
        number = syscall_number(name)
        if number is None:
            continue
        wanted.append((name, number))

    socket_nr = syscall_number("socket")
    socketpair_nr = syscall_number("socketpair")
    # The AF_UNIX gate belongs to the **network class**, and to nothing else. Installing it
    # whenever a filter exists (the first cut did) denies `socket(AF_INET)` even with the network
    # switched on — a profile that declares egress by port was then refused at `socket()` with
    # EPERM before Landlock could allow the port it had declared.
    wants_network = "network" in deny_classes and network_denied
    has_socket_gate = (
        allow_unix_sockets and wants_network and socket_nr is not None and socketpair_nr is not None
    )
    ambiguous = (socket_nr, socketpair_nr) if has_socket_gate else ()
    plain = [(name, nr) for name, nr in wanted if nr not in ambiguous]

    # ---- lay the program out (indices are known before we emit, so jumps are exact)
    length = 4 + len(plain)
    if has_socket_gate:
        idx_arg0 = length + 2
        idx_errno_gate = length + 4
        idx_allow = length + 5
        idx_errno = length + 6
    else:
        idx_arg0 = idx_errno_gate = -1  # only the socket gate reads these
        idx_allow = length
        idx_errno = length + 1

    ops: list[tuple[int, int, int, int]] = []
    ops.append((_BPF_LD_W_ABS, 0, 0, _OFFSET_ARCH))
    ops.append((_BPF_JEQ_K, 3 - 2, 0, arch))  # at index 1: jt must reach index 3
    ops.append((_BPF_RET_K, 0, 0, SECCOMP_RET_KILL_PROCESS))
    ops.append((_BPF_LD_W_ABS, 0, 0, _OFFSET_NR))
    for _name, number in plain:
        index = len(ops)
        ops.append((_BPF_JEQ_K, idx_errno - (index + 1), 0, number))
    if has_socket_gate:
        if length != len(ops) or idx_arg0 != len(ops) + 2:
            raise SecurityRefusal(  # pragma: no cover — a layout bug, not a host condition
                CONTROL_SECCOMP,
                "internal layout error: the socket gate is not where it was planned",
            )
        index = len(ops)
        ops.append((_BPF_JEQ_K, idx_arg0 - (index + 1), idx_allow - (index + 1), socket_nr or 0))
        index = len(ops)
        ops.append(
            (_BPF_JEQ_K, idx_arg0 - (index + 1), idx_allow - (index + 1), socketpair_nr or 0)
        )
        if idx_arg0 != len(ops):
            raise SecurityRefusal(  # pragma: no cover — a layout bug, not a host condition
                CONTROL_SECCOMP, "internal layout error: argument load is not where it was planned"
            )
        ops.append((_BPF_LD_W_ABS, 0, 0, _OFFSET_ARG0))
        index = len(ops)
        ops.append((_BPF_JEQ_K, idx_allow - (index + 1), idx_errno_gate - (index + 1), AF_UNIX))
        ops.append((_BPF_RET_K, 0, 0, SECCOMP_RET_ERRNO | errno.EPERM))
    ops.append((_BPF_RET_K, 0, 0, SECCOMP_RET_ALLOW))
    ops.append((_BPF_RET_K, 0, 0, SECCOMP_RET_ERRNO | errno.EPERM))

    for index, op in enumerate(ops):
        for slot in (1, 2):
            if op[slot] > _JUMP_MAX or op[slot] < 0:
                raise SecurityRefusal(
                    CONTROL_SECCOMP,
                    f"jump offset {op[slot]} at instruction {index} does not fit in 8 bits",
                )
    return tuple(ops)


def apply_seccomp(program: tuple[tuple[int, int, int, int], ...]) -> None:
    """Install ``program`` with ``prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, ...)``.

    ``PR_SET_NO_NEW_PRIVS`` must already be set (the kernel returns EACCES otherwise), which is
    why :func:`apply_plan` orders the two.
    """
    if not program:
        return
    array = (_SockFilter * len(program))()
    for index, (code, jt, jf, k) in enumerate(program):
        array[index] = _SockFilter(code, jt, jf, k)
    fprog = _SockFprog(len(program), array)
    ctypes.set_errno(0)
    result = _libc().prctl(22, SECCOMP_MODE_FILTER, ctypes.byref(fprog), 0, 0)
    if result != 0:
        code = ctypes.get_errno()
        raise SecurityRefusal(
            CONTROL_SECCOMP,
            f"prctl(PR_SET_SECCOMP) failed with errno {code} ({os.strerror(code)})",
            fix="no_new_privs must be set first, and the filter must be < the kernel's BPF limit",
        )


def set_no_new_privs() -> None:
    """``prctl(PR_SET_NO_NEW_PRIVS, 1)`` — not optional, and it is what makes the rest stick.

    With it set, `execve` can never gain privilege: a setuid or file-capability binary is
    executed without its elevation, and an unprivileged user namespace cannot be created.

    ``prctl`` is Linux-only. On any other platform this **refuses** (it does not return quietly):
    the caller is a launcher that must not run a command while believing a control it asked for
    is in place.
    """
    if not is_linux():
        raise SecurityRefusal(
            CONTROL_NO_NEW_PRIVS,
            f"prctl(PR_SET_NO_NEW_PRIVS) is a Linux call and this platform is "
            f"{detected_platform()}",
            fix="run the sandboxed command on Linux, or use a platform with an equivalent "
            "primitive (none is implemented today)",
        )
    ctypes.set_errno(0)
    result = _libc().prctl(38, 1, 0, 0, 0)
    if result != 0:
        code = ctypes.get_errno()
        raise SecurityRefusal(
            CONTROL_NO_NEW_PRIVS,
            f"prctl(PR_SET_NO_NEW_PRIVS) failed with errno {code} ({os.strerror(code)})",
        )


def no_new_privs_is_set() -> bool:
    """Read the flag back from ``/proc/self/status`` — the only measurement that counts."""
    try:
        for line in Path("/proc/self/status").read_text(encoding="utf-8").splitlines():
            if line.startswith("NoNewPrivs:"):
                return line.split(":", 1)[1].strip() == "1"
    except OSError:
        return False
    return False


# ------------------------------------------------------------------- landlock


@dataclass(frozen=True)
class LandlockRuleset:
    """A Landlock ruleset as data: handled access, the rules, and the ABI it was built for."""

    abi: int
    handled_access_fs: int
    handled_access_net: int
    #: ``(path, allowed_access)`` — rules are allow-only; a path with no rule is denied for
    #: every handled access.
    path_rules: tuple[tuple[str, int], ...] = ()
    #: ``(port, allowed_access)`` — only used while ``handled_access_net`` is non-zero.
    net_port_rules: tuple[tuple[int, int], ...] = ()
    #: The mechanism's private scratch path, granted read-write, or ``""``.
    sandbox_tmp: str = ""
    masked: tuple[str, ...] = ()
    dropped_paths: tuple[str, ...] = ()

    def describe(self) -> str:
        parts = [f"ABI {self.abi}", f"handled fs 0x{self.handled_access_fs:x}"]
        if self.handled_access_net:
            parts.append(f"handled net 0x{self.handled_access_net:x}")
        if self.sandbox_tmp:
            parts.append(f"scratch {self.sandbox_tmp}")
        parts.append(f"{len(self.path_rules)} path rule(s)")
        if self.net_port_rules:
            parts.append(f"{len(self.net_port_rules)} net rule(s)")
        if self.masked:
            parts.append("masked: " + ", ".join(self.masked))
        if self.dropped_paths:
            parts.append(f"{len(self.dropped_paths)} path(s) absent on disk")
        return "; ".join(parts)


def _access_for_path(path: str, access: int) -> int:
    """Narrow ``access`` to what a rule attached to ``path`` may legally request.

    The kernel validates a rule's access set against the **file type of the parent**: a
    directory takes the whole set, anything else takes only
    :data:`NON_DIRECTORY_ACCESS`. A path that does not exist is narrowed too — the rule will be
    dropped later by :func:`apply_landlock`, which reports it by name.
    """
    try:
        info = os.lstat(path)
    except OSError:
        return access & NON_DIRECTORY_ACCESS
    if stat.S_ISDIR(info.st_mode):
        return access
    return access & NON_DIRECTORY_ACCESS


def _open_for_rule(path: str) -> int:
    """`O_PATH` is what a Landlock rule wants — it needs a reference, not a readable file."""
    return os.open(path, getattr(os, "O_PATH", os.O_RDONLY) | os.O_CLOEXEC | os.O_NOFOLLOW)


def apply_landlock(ruleset: LandlockRuleset) -> dict[str, Any]:
    """Create, populate and restrict with ``ruleset``. Returns what the kernel accepted."""
    numbers = _syscall_numbers()
    if numbers is None:
        raise SecurityRefusal(
            CONTROL_LANDLOCK, f"no Landlock syscall numbers for {_machine()}"
        )
    abi = int(ruleset.abi) or landlock_abi()
    if abi < LANDLOCK_ABI_MIN:
        raise SecurityRefusal(
            CONTROL_LANDLOCK, landlock_unavailable_reason() or "Landlock is not available"
        )
    handled_fs, masked = _mask_access(ruleset.handled_access_fs, abi)
    handled_net = ruleset.handled_access_net if abi >= LANDLOCK_ABI_NET else 0
    attr = _RulesetAttr()
    attr.handled_access_fs = handled_fs
    attr.handled_access_net = handled_net
    size = ctypes.sizeof(_RulesetAttr) if abi >= LANDLOCK_ABI_NET else 8
    lib = _libc()
    ctypes.set_errno(0)
    fd = lib.syscall(numbers[0], ctypes.byref(attr), size, 0)
    if fd < 0:
        code = ctypes.get_errno()
        raise SecurityRefusal(
            CONTROL_LANDLOCK,
            f"landlock_create_ruleset failed with errno {code} ({os.strerror(code)})",
            fix="handled access must be a subset of what this ABI supports",
        )
    added = 0
    dropped: list[str] = []
    try:
        for path, allowed in ruleset.path_rules:
            try:
                parent = _open_for_rule(path)
            except OSError:
                dropped.append(path)
                continue
            try:
                rule = _PathBeneathAttr(allowed, parent, 0)
                ctypes.set_errno(0)
                result = lib.syscall(
                    numbers[1], fd, _LANDLOCK_RULE_PATH_BENEATH, ctypes.byref(rule), 0
                )
                if result != 0:
                    code = ctypes.get_errno()
                    raise SecurityRefusal(
                        CONTROL_LANDLOCK,
                        f"landlock_add_rule({path}) failed with errno {code} "
                        f"({os.strerror(code)})",
                    )
                added += 1
            finally:
                os.close(parent)
        for port, allowed in ruleset.net_port_rules:
            if abi < LANDLOCK_ABI_NET:
                continue
            rule = _NetPortAttr(allowed, int(port) & 0xFFFF)
            ctypes.set_errno(0)
            result = lib.syscall(
                numbers[1], fd, _LANDLOCK_RULE_NET_PORT, ctypes.byref(rule), 0
            )
            if result != 0:
                code = ctypes.get_errno()
                raise SecurityRefusal(
                    CONTROL_LANDLOCK,
                    f"landlock_add_rule(net port {port}) failed with errno {code} "
                    f"({os.strerror(code)})",
                )
        ctypes.set_errno(0)
        result = lib.syscall(numbers[2], fd, 0)
        if result != 0:
            code = ctypes.get_errno()
            raise SecurityRefusal(
                CONTROL_LANDLOCK,
                f"landlock_restrict_self failed with errno {code} ({os.strerror(code)})",
                fix="PR_SET_NO_NEW_PRIVS has to be set first",
            )
    finally:
        os.close(fd)
    return {
        "abi": abi,
        "handled_access_fs": handled_fs,
        "handled_access_net": handled_net,
        "rules_added": added,
        "paths_dropped": dropped,
        "masked": list(masked),
    }


# ---------------------------------------------------------------- cgroup pids


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _own_cgroup() -> str:
    """Our own cgroup v2 path, from ``/proc/self/cgroup`` (``0::/…`` on a unified host)."""
    try:
        for line in Path("/proc/self/cgroup").read_text(encoding="utf-8").splitlines():
            fields = line.split(":", 2)
            if len(fields) == 3 and fields[0] == "0":
                return fields[2].strip()
    except OSError:
        return ""
    return ""


def pids_cgroup_support() -> tuple[bool, str, str]:
    """Can we make a `pids.max` leaf without root? ``(ok, path, reason)``.

    Measured, not assumed. A systemd host delegates ``user@<uid>.service`` to the user, so a
    user-owned ancestor with ``pids`` already in ``cgroup.subtree_control`` exists — that is the
    directory a leaf may be created under. Two passes, and the second one is only a *maybe*:

    1. **Nearest writable ancestor with ``pids`` enabled** — this is the real answer;
    2. otherwise the nearest writable ancestor that *has* the controller but has not enabled it
       and holds no process of its own — we would have to write ``+pids`` first, which the
       kernel refuses (``EBUSY``) when a process lives there.

    Both passes are necessary in practice: the first writable directory on the path is usually
    the *scope we are running in* (wall of processes, no subtree control), and it is the
    grandparent slice that can actually host a leaf.
    """
    root = Path("/sys/fs/cgroup")
    if not is_linux():
        return (
            False,
            "",
            f"cgroup v2 is a Linux-kernel feature; this platform is {detected_platform()}",
        )
    own = _own_cgroup()
    if not own:
        return False, "", "/proc/self/cgroup reports no cgroup v2 entry (not a unified host?)"
    chain: list[Path] = []
    current = root / own.lstrip("/")
    while True:
        chain.append(current)
        if current == root or current.parent == current:
            break
        current = current.parent
    maybe = ""
    maybe_reason = ""
    for directory in chain:
        if not (directory.is_dir() and os.access(directory, os.W_OK)):
            continue
        subtree = _read_text(directory / "cgroup.subtree_control").split()
        if "pids" in subtree:
            return True, str(directory), ""
        if "pids" in _read_text(directory / "cgroup.controllers").split() and not maybe:
            if not _read_text(directory / "cgroup.procs"):
                maybe, maybe_reason = str(directory), f"pids has to be enabled at {directory} first"
    if maybe:
        return True, maybe, maybe_reason
    return (
        False,
        "",
        "no writable cgroup ancestor offers the pids controller "
        f"(checked {len(chain)} level(s) from {root / own.lstrip('/')} up to {root}): the user "
        "session is not delegated a pids sub-tree",
    )


class CgroupUnavailable(RuntimeError):
    """No delegated cgroup leaf we may write to — reported, never fatal by itself."""

    def __init__(self, reason: str) -> None:
        self.reason = str(reason)
        super().__init__(self.reason)


class ProcessCgroup:
    """A cgroup v2 leaf that caps the sandboxed process tree with ``pids.max``.

    This is the mechanism `MVP/43` said we did not have. It works **without root** on a
    systemd host, and the numbers it produces (`pids.current`, `pids.events`) are the evidence
    that the cap is real — see :meth:`measure`.
    """

    def __init__(self, path: Path, parent: Path, pids_max: int, name: str = "") -> None:
        self.path = path
        self.parent = parent
        self.pids_max = int(pids_max)
        self.name = name or path.name

    # -------------------------------------------------------------- construction

    @classmethod
    def open(cls, pids_max: int, *, label: str = "") -> ProcessCgroup:
        """Create the leaf and set ``pids.max``. Raises :class:`CgroupUnavailable` with a reason."""
        limit = int(pids_max)
        if limit <= 0:
            raise CgroupUnavailable("max_processes is 0 — no cap is declared, so none is applied")
        ok, parent_text, reason = pids_cgroup_support()
        if not ok:
            raise CgroupUnavailable(reason)
        parent = Path(parent_text)
        if "pids" not in _read_text(parent / "cgroup.subtree_control").split():
            try:
                with (parent / "cgroup.subtree_control").open("w", encoding="utf-8") as handle:
                    handle.write("+pids")
            except OSError as exc:
                raise CgroupUnavailable(
                    f"cannot enable the pids controller at {parent}: {type(exc).__name__} "
                    f"({exc.strerror or exc})"
                ) from exc
        stem = f"skaldir-{label or 'run'}-{os.getpid()}"
        for attempt in range(0, 100):
            candidate = parent / (stem if attempt == 0 else f"{stem}-{attempt}")
            try:
                candidate.mkdir(mode=0o755)
            except FileExistsError:
                continue
            except OSError as exc:
                raise CgroupUnavailable(
                    f"cannot create {candidate}: {type(exc).__name__} ({exc.strerror or exc})"
                ) from exc
            try:
                with (candidate / "pids.max").open("w", encoding="utf-8") as handle:
                    handle.write(str(limit))
            except OSError as exc:
                candidate.rmdir()
                raise CgroupUnavailable(
                    f"cannot write pids.max at {candidate}: {type(exc).__name__} "
                    f"({exc.strerror or exc})"
                ) from exc
            return cls(candidate, parent, limit)
        raise CgroupUnavailable(f"cannot find a free cgroup name under {parent}")

    # ------------------------------------------------------------------- joiner

    def join(self, pid: int | None = None) -> None:
        """Put ``pid`` (default: ourselves) into the leaf. Children inherit the membership."""
        with (self.path / "cgroup.procs").open("w", encoding="utf-8") as handle:
            handle.write(str(int(pid if pid is not None else os.getpid())))

    # ----------------------------------------------------------------- evidence

    def measure(self) -> dict[str, Any]:
        """What the kernel recorded: the live count and whether the cap ever fired."""
        current = _read_text(self.path / "pids.current")
        events = _read_text(self.path / "pids.events")
        peak = 0
        for token in events.replace("max", " ").split():
            if token.isdigit():
                peak = int(token)
        return {
            "path": str(self.path),
            "pids_max": self.pids_max,
            "pids_current": int(current) if current.isdigit() else None,
            "pids_events_max": peak,
            "pids_max_file": _read_text(self.path / "pids.max"),
        }

    # ------------------------------------------------------------------ cleanup

    def release(self) -> dict[str, Any]:
        """Remove the leaf. A non-empty cgroup cannot be removed, and that is reported.

        Never reports success from the ``rmdir`` call alone: the directory is listed again and
        the answer carries what is still there.
        """
        report = self.measure()
        removed = False
        reason = ""
        try:
            self.path.rmdir()
            removed = True
        except OSError as exc:
            reason = f"{type(exc).__name__} ({exc.strerror or exc})"
        still_there = self.path.exists()
        if removed and still_there:  # pragma: no cover — a race we prefer to admit
            removed, reason = False, "the directory reappeared after rmdir"
        return {
            **report,
            "removed": removed and not still_there,
            "still_present": still_there,
            "reason": reason,
        }


def rlimit_nproc_verdict() -> dict[str, Any]:
    """Why ``RLIMIT_NPROC`` is **not** our ``max_processes`` mechanism — measured here.

    ``RLIMIT_NPROC`` is checked against the number of processes owned by the **real user id**,
    not by the process tree. So a child cap of N either does nothing (N above the user's live
    count) or blocks every fork in the tree (N below it) — it cannot express "this run may
    spawn at most N children".
    """
    import subprocess

    try:
        live = len(
            subprocess.run(  # noqa: S603 — fixed argv, no shell
                ["/usr/bin/ps", "-u", str(os.getuid()), "-o", "pid="],
                capture_output=True,
                check=False,
            ).stdout.split()
        )
    except OSError:
        live = -1
    return {
        "mechanism": "RLIMIT_NPROC",
        "usable": False,
        "user_process_count": live,
        "reason": (
            "RLIMIT_NPROC counts every process owned by the real uid, not the process tree: "
            f"this user has {live} processes alive, so a per-run cap of N would block all "
            "forking whenever N < the user's live count (measured: exit 254 with "
            "'fork: Resource temporarily unavailable' for N = live + 3) and do nothing "
            "otherwise. The cgroup v2 pids controller is the mechanism that expresses "
            "'this tree may spawn at most N'."
        ),
    }


# ------------------------------------------------- platform coverage (gap 11)

#: The enforcement primitives, in the order the launcher applies them. ``namespaces`` is
#: bubblewrap's mount/pid/net view (the outer stage); the other five are kernel controls.
PRIMITIVES: tuple[str, ...] = (
    "no_new_privs",
    "landlock",
    "namespaces",
    "seccomp",
    "pids_cgroup",
    "rlimits",
)

#: A primitive's coverage state. Three values, and no fourth: ``enforced`` means applied and
#: complete, ``degraded`` means **something is missing** (the host could do it and the policy
#: does not, or it is applied with reduced coverage), ``unavailable`` means this platform/kernel
#: cannot do it at all.
STATUS_ENFORCED = "enforced"
STATUS_DEGRADED = "degraded"
STATUS_UNAVAILABLE = "unavailable"
COVERAGE_STATUSES: tuple[str, ...] = (STATUS_ENFORCED, STATUS_DEGRADED, STATUS_UNAVAILABLE)

#: What each primitive actually covers while it is enforced — the plain-language scope.
PRIMITIVE_SCOPE: dict[str, str] = {
    "no_new_privs": "every execve in the run loses setuid/file-capability elevation, and an "
    "unprivileged user namespace cannot be created",
    "landlock": "the kernel denies the filesystem accesses the policy did not grant, and (ABI >= "
    "4) every TCP connect/bind the egress policy did not allow",
    "namespaces": "the mount view (protected paths carved out, private /tmp), the pid and net "
    "namespace (no host network namespace at all)",
    "seccomp": "the privilege syscalls (mount/ptrace/bpf/...) and every socket family except "
    "AF_UNIX, plus connect/bind/listen/send* while the network is off",
    "pids_cgroup": "every process the run forks counts against pids.max; a fork past the cap "
    "fails with EAGAIN",
    "rlimits": "RLIMIT_AS caps the child's address space, so an unbounded allocation fails "
    "instead of taking the host down",
}

#: What is **left** when the primitive is not applied. Written as the design statement; the
#: measured version of each sentence is the degradation matrix in `MVP/84` (raw artefact:
#: ``artefacts/84-degradation-matrix.json``), which switches one primitive off at a time and
#: records what a hostile child could still do.
PRIMITIVE_FALLBACK: dict[str, str] = {
    "no_new_privs": "nothing — and Landlock and seccomp both require it, so the launcher does "
    "not run without it",
    "landlock": "bubblewrap's mount view still refuses a write outside the declared roots and "
    "--unshare-net/seccomp still refuse a connect, but a *path* whose mount view is missing or "
    "misordered is then unprotected at the kernel level",
    "namespaces": "Landlock still refuses the write (EACCES) and its TCP rules still refuse the "
    "connect; what is lost is the protected-path carve-out, the private /tmp and the network "
    "namespace itself",
    "seccomp": "Landlock still refuses the filesystem and TCP accesses; the syscall classes "
    "(mount/ptrace/bpf) and the non-AF_UNIX socket families are then unguarded",
    "pids_cgroup": "nothing caps the tree: a fork bomb is bounded only by the host's own limits "
    "(and eventually by RLIMIT_NPROC, which refuses *all* forking for the user)",
    "rlimits": "nothing caps the child's address space; only the wall-clock timeout and the "
    "host's own memory handling bound a runaway allocation",
}

#: What the user loses while the primitive is not applied (the product-facing sentence).
PRIMITIVE_LOSSES: dict[str, str] = {
    "no_new_privs": "the flag that makes Landlock and seccomp unbypassable — without it a "
    "sandboxed command could keep a privilege it gained",
    "landlock": "the kernel's own filesystem/network policy: the sandbox becomes a *mount view* "
    "only, so every path the mounts got wrong is reachable",
    "namespaces": "the mount view: protected subpaths are no longer carved out of a writable "
    "root, /tmp is the host's /tmp again, and the run keeps a network namespace",
    "seccomp": "the syscall-class policy: mount/ptrace/bpf and non-AF_UNIX sockets are callable",
    "pids_cgroup": "a per-run process ceiling; the run can fork without limit",
    "rlimits": "a per-run memory ceiling",
}


def _rlimit_as_available() -> tuple[bool, str]:
    """Can this platform set ``RLIMIT_AS``? ``(ok, reason)`` — the one POSIX primitive here."""
    try:
        import resource
    except ImportError:  # pragma: no cover — Windows only
        return False, "the `resource` module does not exist on this platform (Windows)"
    if not hasattr(resource, "RLIMIT_AS"):
        return False, "this platform's `resource` module has no RLIMIT_AS"
    return True, ""


def _unprivileged_userns_ok() -> tuple[bool, str]:
    """Is an unprivileged user namespace allowed? ``(ok, reason)`` — read from /proc, no call."""
    if not is_linux():
        return False, f"Linux namespaces do not exist on {detected_platform()}"
    raw = _read_text(Path("/proc/sys/user/max_user_namespaces"))
    if raw == "":
        return True, ""  # the knob is absent on some kernels: not a "no"
    try:
        maximum = int(raw)
    except ValueError:
        return True, ""
    if maximum == 0:
        return (
            False,
            "/proc/sys/user/max_user_namespaces is 0: unprivileged user namespaces are disabled "
            "on this host, so bubblewrap cannot build its mount view",
        )
    return True, ""


def _cgroup_v2_present() -> tuple[bool, str]:
    """Is a cgroup v2 hierarchy mounted here? ``(ok, reason)`` — a file read, not a guess."""
    if not is_linux():
        return False, f"cgroup v2 is Linux-only; this platform is {detected_platform()}"
    controllers = _read_text(Path("/sys/fs/cgroup/cgroup.controllers"))
    if not controllers:
        return False, "no unified cgroup v2 hierarchy at /sys/fs/cgroup (cgroup v1 or none)"
    return True, ""


def _effective_status(host: str, applied: bool) -> str:
    """The status a *reader* needs: how much of this primitive is really in force right now.

    A primitive the host cannot do is ``unavailable`` whatever the policy says. A primitive the
    host *can* do but this profile does not apply (declared off, or no bubblewrap binary, or a
    cap that was never declared) is ``degraded`` — never silently "enforced".
    """
    if applied:
        return host
    return STATUS_UNAVAILABLE if host == STATUS_UNAVAILABLE else STATUS_DEGRADED


@dataclass(frozen=True)
class PrimitiveCoverage:
    """One enforcement primitive, on one host, under one policy — with the reason it counts."""

    primitive: str
    status: str
    reason: str
    applied: bool
    requires: str = ""
    scope: str = ""
    fallback: str = ""
    loses: str = ""
    measured: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "primitive": self.primitive,
            "status": self.status,
            "applied": bool(self.applied),
            "reason": self.reason,
            "requires": self.requires,
            "scope": self.scope,
            "fallback": self.fallback,
            "loses": self.loses,
            "measured": dict(self.measured),
        }

    def sentence(self) -> str:
        """One line: status, and — when it is not ``enforced`` — why."""
        text = f"{self.primitive}: {self.status}"
        if self.status != STATUS_ENFORCED and self.reason:
            text += f" ({self.reason})"
        return text


def _coverage_row(
    primitive: str,
    *,
    host: str,
    host_reason: str,
    wanted: bool,
    measured: dict[str, Any],
    not_wanted_reason: str = "",
    applied_reason: str = "",
) -> PrimitiveCoverage:
    """Assemble one row. The reason is never empty unless the primitive is fully enforced.

    The host's own reason wins when the host cannot do it at all: "cgroup v2 is a Linux feature;
    this platform is darwin" is the fact a reader needs, and "you declared no cap" would point at
    the wrong thing (the status would say ``unavailable`` and the reason would blame the policy).
    """
    applied = bool(wanted) and host == STATUS_ENFORCED
    if host == STATUS_UNAVAILABLE:
        reason = host_reason
    elif applied:
        reason = applied_reason
    elif not wanted:
        reason = not_wanted_reason or "not declared by this profile"
    else:
        reason = host_reason
    return PrimitiveCoverage(
        primitive=primitive,
        status=_effective_status(host, applied),
        reason=reason,
        applied=applied,
        requires=KERNEL_REQUIREMENTS.get(primitive, ""),
        scope=PRIMITIVE_SCOPE.get(primitive, ""),
        fallback=PRIMITIVE_FALLBACK.get(primitive, ""),
        loses=PRIMITIVE_LOSSES.get(primitive, ""),
        measured=dict(measured),
    )


def platform_coverage(
    *,
    platform: str | None = None,
    kernel: str | None = None,
    abi: int | None = None,
    seccomp_arch: int | None = None,
    pids: tuple[bool, str, str] | None = None,
    bwrap: str | None = None,
    userns_ok: bool | None = None,
    rlimit_ok: bool | None = None,
    cgroup_v2: bool | None = None,
    want: Mapping[str, bool] | None = None,
    mechanism: str = "",
) -> list[PrimitiveCoverage]:
    """Per-primitive coverage on this host: ``enforced`` / ``degraded`` / ``unavailable`` + why.

    **Measured, never assumed**, and every measurement can be supplied by the caller — that is
    the test hook. ``platform="darwin"`` measures the Darwin answer *without* touching the
    kernel, and a simulated platform can never *invent* enforcement: the syscall-backed probes
    (:func:`landlock_abi`, :func:`audit_arch`, :func:`pids_cgroup_support`) refuse to run on a
    non-Linux interpreter whatever the argument says, so the worst a caller can do is make the
    report more pessimistic.

    ``want`` says which primitives the *policy* asks for (default: all of them). A primitive the
    host can do but the policy does not ask for is reported ``degraded`` with that reason — the
    downgrade is named, never hidden.
    """
    plat = detected_platform() if platform is None else PLATFORM_ALIASES.get(
        str(platform), str(platform)
    )
    kernel_text = kernel_release() if kernel is None else str(kernel)
    abi_value = landlock_abi() if abi is None else int(abi)
    arch = audit_arch() if seccomp_arch is None else seccomp_arch
    pids_ok, pids_parent, pids_reason = (
        pids_cgroup_support() if pids is None else pids
    )
    bwrap_bin = _which("bwrap") if bwrap is None else str(bwrap)
    userns, userns_reason = _unprivileged_userns_ok() if userns_ok is None else (
        bool(userns_ok),
        "",
    )
    rlimit, rlimit_reason = _rlimit_as_available() if rlimit_ok is None else (
        bool(rlimit_ok),
        "",
    )
    cg2, cg2_reason = _cgroup_v2_present() if cgroup_v2 is None else (bool(cgroup_v2), "")
    wanted = {name: True for name in PRIMITIVES}
    if want:
        wanted.update({str(key): bool(value) for key, value in want.items()})
    linux = plat == "linux"
    no_mechanism = mechanism == MECHANISM_NONE
    unreachable = (
        "no sandbox mechanism on this platform: the runner refuses the command before a child "
        "exists (fail-closed), so this control never runs — nothing is enforced here"
    )
    rows: list[PrimitiveCoverage] = []

    def _wanted(name: str) -> bool:
        return bool(wanted[name]) and not no_mechanism

    def _reason(specific: str) -> str:
        return unreachable if no_mechanism else specific

    # 1. no_new_privs — Linux prctl, and the launcher never runs without it.
    if not linux:
        host, host_reason = STATUS_UNAVAILABLE, (
            f"prctl(PR_SET_NO_NEW_PRIVS) is a Linux call; this platform is {plat}"
        )
    elif not hasattr(_libc(), "prctl"):  # pragma: no cover — a libc without prctl
        host, host_reason = STATUS_UNAVAILABLE, "this libc exposes no prctl symbol"
    else:
        host, host_reason = STATUS_ENFORCED, ""
    rows.append(
        _coverage_row(
            "no_new_privs",
            host=host,
            host_reason=host_reason,
            wanted=_wanted("no_new_privs"),
            not_wanted_reason=_reason(
                "not applied by the launcher: the profile's linux_security.no_new_privs cannot "
                "switch it off, so this can only be the no-mechanism case"
            ),
            measured={"platform": plat, "kernel": kernel_text, "prctl": host == STATUS_ENFORCED},
            applied_reason=(
                "set unconditionally by the launcher (Landlock and seccomp both require it); the "
                "profile's linux_security.no_new_privs cannot switch it off"
            ),
        )
    )

    # 2. landlock — ABI 1 gives the filesystem rules, ABI 4 adds the TCP rules.
    if not linux:
        host, host_reason = STATUS_UNAVAILABLE, (
            f"Landlock is a Linux kernel feature (5.13+); this platform is {plat}"
        )
    elif abi_value <= 0:
        host, host_reason = STATUS_UNAVAILABLE, (
            landlock_unavailable_reason() or "the kernel reports no Landlock ABI"
        )
    elif abi_value < LANDLOCK_ABI_NET:
        host, host_reason = STATUS_DEGRADED, (
            f"Landlock ABI {abi_value}: filesystem rules only — the TCP rules "
            f"(LANDLOCK_ACCESS_NET_*) need ABI >= {LANDLOCK_ABI_NET} (Linux 5.19), so a network "
            "egress policy is not enforceable by Landlock on this kernel"
        )
    else:
        host, host_reason = STATUS_ENFORCED, ""
    rows.append(
        _coverage_row(
            "landlock",
            host=host,
            host_reason=host_reason,
            wanted=_wanted("landlock"),
            not_wanted_reason=_reason(
                "declared off (linux_security.landlock = 'off'): the filesystem and TCP policy "
                "is then not enforced by the kernel"
            ),
            measured={
                "platform": plat,
                "kernel": kernel_text,
                "abi": int(abi_value),
                "abi_min": int(LANDLOCK_ABI_MIN),
                "network_rules_abi": int(LANDLOCK_ABI_NET),
                "reason": host_reason,
            },
        )
    )

    # 3. namespaces — bubblewrap's mount view (the outer stage of the three-stage order).
    if not linux:
        host, host_reason = STATUS_UNAVAILABLE, (
            f"bubblewrap needs Linux namespaces; this platform is {plat}"
        )
    elif not userns:
        host, host_reason = STATUS_UNAVAILABLE, userns_reason
    else:
        host, host_reason = STATUS_ENFORCED, ""
    rows.append(
        _coverage_row(
            "namespaces",
            host=host,
            host_reason=host_reason,
            wanted=_wanted("namespaces") and bool(bwrap_bin),
            not_wanted_reason=_reason(
                "no bubblewrap binary on PATH"
                if not bwrap_bin
                else "sandbox_backend = 'none': the profile asks for no mount view"
            ),
            measured={
                "platform": plat,
                "bwrap": bwrap_bin,
                "unprivileged_userns": bool(userns),
                "reason": host_reason or userns_reason,
            },
        )
    )

    # 4. seccomp — classic BPF, built in-process; needs an AUDIT_ARCH constant.
    if not linux:
        host, host_reason = STATUS_UNAVAILABLE, (
            f"seccomp is a Linux syscall filter; this platform is {plat}"
        )
    elif arch is None:
        host, host_reason = STATUS_UNAVAILABLE, (
            f"no AUDIT_ARCH_* constant for {_machine()}: an arch-checked filter cannot be built"
        )
    else:
        host, host_reason = STATUS_ENFORCED, ""
    rows.append(
        _coverage_row(
            "seccomp",
            host=host,
            host_reason=host_reason,
            wanted=_wanted("seccomp"),
            not_wanted_reason=_reason(
                "declared off (linux_security.seccomp = 'off'): the syscall classes are then "
                "not filtered"
            ),
            measured={"platform": plat, "audit_arch": arch, "machine": _machine()},
        )
    )

    # 5. pids cgroup — a delegated cgroup v2 leaf with pids.max.
    if not linux:
        host, host_reason = STATUS_UNAVAILABLE, (
            f"the cgroup v2 pids controller is a Linux feature; this platform is {plat}"
        )
    elif pids_ok:
        host, host_reason = STATUS_ENFORCED, ""
    elif cg2:
        host, host_reason = STATUS_DEGRADED, (
            f"cgroup v2 is mounted but no writable ancestor offers the pids controller: "
            f"{pids_reason}"
        )
    else:
        host, host_reason = STATUS_UNAVAILABLE, cg2_reason or pids_reason
    rows.append(
        _coverage_row(
            "pids_cgroup",
            host=host,
            host_reason=host_reason,
            wanted=_wanted("pids_cgroup"),
            not_wanted_reason=_reason(
                "no cap declared (caps.max_processes = 0), or linux_security.pids_cgroup = "
                "'off': nothing caps the process tree"
            ),
            measured={
                "platform": plat,
                "cgroup_v2": bool(cg2),
                "leaf_parent": pids_parent,
                "reason": host_reason or pids_reason,
            },
        )
    )

    # 6. rlimits — POSIX, so the only primitive here that is not Linux-only.
    if not rlimit:
        host, host_reason = STATUS_UNAVAILABLE, rlimit_reason or "RLIMIT_AS is unavailable"
    else:
        host, host_reason = STATUS_ENFORCED, ""
    rows.append(
        _coverage_row(
            "rlimits",
            host=host,
            host_reason=host_reason,
            wanted=_wanted("rlimits"),
            not_wanted_reason=_reason(
                "no memory cap declared (caps.max_memory_bytes = 0): nothing bounds the child's "
                "address space"
            ),
            measured={"platform": plat, "rlimit_as": bool(rlimit), "reason": host_reason},
        )
    )
    native = plat == detected_platform()
    if not native:
        # A simulated platform must be labelled as one: the *measurements* in these rows come
        # from whichever host ran the call, and only the platform checks were overridden.
        rows = [
            replace(row, measured={**row.measured, "simulated": True}) for row in rows
        ]
    return rows


def coverage_verdict(rows: Sequence[PrimitiveCoverage]) -> dict[str, Any]:
    """The worst state on the host, plus the lists — one verdict a caller cannot oversell."""
    counts = {status: 0 for status in COVERAGE_STATUSES}
    for row in rows:
        counts[row.status] = counts.get(row.status, 0) + 1
    if counts.get(STATUS_UNAVAILABLE):
        verdict = STATUS_UNAVAILABLE
    elif counts.get(STATUS_DEGRADED):
        verdict = STATUS_DEGRADED
    else:
        verdict = STATUS_ENFORCED
    return {
        "verdict": verdict,
        "counts": counts,
        "enforced": [row.primitive for row in rows if row.status == STATUS_ENFORCED],
        "degraded": [row.primitive for row in rows if row.status == STATUS_DEGRADED],
        "unavailable": [row.primitive for row in rows if row.status == STATUS_UNAVAILABLE],
    }


#: The supported platform set, plainly. One entry today, and the document that says so
#: (``docs/PLATFORM.md``) is generated from this table rather than maintained beside it.
SUPPORTED_PLATFORMS: tuple[str, ...] = ("linux",)

#: What parity on another platform would require — per platform, the honest work list.
PARITY_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "darwin": (
        "a sandbox front end equivalent to bubblewrap: macOS has `sandbox-exec` (Seatbelt) / "
        "the App Sandbox SBPL profiles — a different language, not bubblewrap's argv",
        "a filesystem/network policy equivalent to Landlock: Seatbelt profiles can express it, "
        "but nothing in this module speaks SBPL today",
        "a syscall filter equivalent to seccomp: macOS has no unprivileged seccomp; the closest "
        "is a Seatbelt profile (unavailable on iOS, deprecated-but-present on macOS)",
        "a process-count cap equivalent to cgroup pids.max: macOS has no cgroup v2; "
        "RLIMIT_NPROC is per-user (see rlimit_nproc_verdict) and would need a launchd/Mach "
        "equivalent",
    ),
    "win32": (
        "a sandbox front end: AppContainer or a job object with restricted tokens — neither is "
        "bubblewrap's argv, so sandbox_argv() cannot carry it",
        "a filesystem/network policy: AppContainer capabilities + a Windows Filtering Platform "
        "rule set",
        "a syscall filter: none; Windows offers mitigation policies (ACG/CIG) which are not "
        "seccomp and are per-process opt-ins",
        "a process-count cap: a job object with JOB_OBJECT_LIMIT_ACTIVE_PROCESS (no root needed)",
        "RLIMIT_AS has no Windows equivalent; a job object memory limit is the closest",
    ),
}


def platform_support(*, platform: str | None = None, kernel: str | None = None) -> dict[str, Any]:
    """Is this platform supported, on which kernel, and what would parity require?"""
    plat = detected_platform() if platform is None else PLATFORM_ALIASES.get(
        str(platform), str(platform)
    )
    kernel_text = kernel_release() if kernel is None else str(kernel)
    numbers = kernel_tuple(kernel_text)
    supported = plat in SUPPORTED_PLATFORMS
    reason = ""
    if not supported:
        reason = (
            f"{plat} is not a supported platform: every enforcement primitive this product has "
            "(Landlock, seccomp, PR_SET_NO_NEW_PRIVS, cgroup v2 pids.max) is Linux-only, so "
            "there is nothing to enforce with — the sandbox therefore refuses to run a command "
            "instead of running it unprotected"
        )
    elif numbers is not None and numbers < (5, 13, 0):
        reason = (
            f"Linux {kernel_text} predates Landlock (5.13); the run still gets bubblewrap's "
            "mount view, seccomp, no_new_privs and the cgroup pids cap, but no kernel-side "
            "filesystem/network policy"
        )
    return {
        "platform": plat,
        "kernel": kernel_text,
        "kernel_minimum": KERNEL_REQUIREMENTS["landlock"],
        "supported": bool(supported),
        "supported_platforms": list(SUPPORTED_PLATFORMS),
        "reason": reason,
        "parity_requirements": list(PARITY_REQUIREMENTS.get(plat, ())),
    }


def platform_report(
    *,
    platform: str | None = None,
    kernel: str | None = None,
    want: Mapping[str, bool] | None = None,
    mechanism: str = "",
    **measurements: Any,
) -> dict[str, Any]:
    """The whole coverage answer: platform support, per-primitive rows, and the verdict.

    ``**measurements`` are the coverage model's own overrides (``abi``, ``seccomp_arch``,
    ``pids``, ``bwrap``, ``userns_ok``, ``rlimit_ok``, ``cgroup_v2``) — the test hook, forwarded
    verbatim so a caller can describe a platform this host is not.
    """
    rows = platform_coverage(
        platform=platform, kernel=kernel, want=want, mechanism=mechanism, **measurements
    )
    verdict = coverage_verdict(rows)
    support = platform_support(platform=platform, kernel=kernel)
    return {
        **support,
        **verdict,
        "mechanism": mechanism,
        "primitives": [row.to_dict() for row in rows],
        "notes": [
            row.sentence() for row in rows if row.status != STATUS_ENFORCED
        ],
    }


# ------------------------------------------------------------------ the plan


@dataclass(frozen=True)
class SecurityPlan:
    """What the child will apply, as plain data. Built by ``permission_profile``, read here.

    It is a flat, JSON-round-trippable document on purpose: the launcher parses it from its own
    argv, so there is exactly **one** place where the policy is turned into a plan (the
    parent) and exactly one place where a plan is turned into kernel calls (the child).
    """

    schema_version: str = "skaldir.linux-security/1"
    profile_name: str = ""
    profile_sha256: str = ""
    mechanism: str = MECHANISM_NONE
    landlock: str = "off"  # enforce | off | unavailable
    landlock_abi: int = 0
    read_only_paths: tuple[str, ...] = ()
    read_write_paths: tuple[str, ...] = ()
    protected_paths: tuple[str, ...] = ()
    network: str = "off"
    egress_ports: tuple[int, ...] = ()
    #: The path the mechanism itself provides as **private scratch space** (bubblewrap's
    #: ``--tmpfs /tmp``), or ``""`` when there is none. It is granted read-write in the Landlock
    #: ruleset *because it is not the host's /tmp* — in the landlock-only fallback there is no
    #: private tmpfs, so ``/tmp`` stays read-only and the matrix says so.
    sandbox_tmp: str = ""
    seccomp: str = "off"  # enforce | off | unavailable
    seccomp_classes: tuple[str, ...] = ()
    no_new_privs: bool = True
    pids_max: int = 0
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "profile_name": self.profile_name,
            "profile_sha256": self.profile_sha256,
            "mechanism": self.mechanism,
            "landlock": self.landlock,
            "landlock_abi": int(self.landlock_abi),
            "read_only_paths": list(self.read_only_paths),
            "read_write_paths": list(self.read_write_paths),
            "protected_paths": list(self.protected_paths),
            "network": self.network,
            "egress_ports": [int(port) for port in self.egress_ports],
            "sandbox_tmp": str(self.sandbox_tmp),
            "seccomp": str(self.seccomp),
            "seccomp_classes": list(self.seccomp_classes),
            "no_new_privs": bool(self.no_new_privs),
            "pids_max": int(self.pids_max),
            "notes": list(self.notes),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> SecurityPlan:
        raw = dict(data or {})
        return cls(
            schema_version=str(raw.get("schema_version", "skaldir.linux-security/1")),
            profile_name=str(raw.get("profile_name", "")),
            profile_sha256=str(raw.get("profile_sha256", "")),
            mechanism=str(raw.get("mechanism", MECHANISM_NONE)),
            landlock=str(raw.get("landlock", "off")),
            landlock_abi=int(raw.get("landlock_abi", 0)),
            read_only_paths=tuple(str(item) for item in raw.get("read_only_paths", ())),
            read_write_paths=tuple(str(item) for item in raw.get("read_write_paths", ())),
            protected_paths=tuple(str(item) for item in raw.get("protected_paths", ())),
            network=str(raw.get("network", "off")),
            egress_ports=tuple(int(item) for item in raw.get("egress_ports", ())),
            sandbox_tmp=str(raw.get("sandbox_tmp", "")),
            seccomp=str(raw.get("seccomp", "off")),
            seccomp_classes=tuple(str(item) for item in raw.get("seccomp_classes", ())),
            no_new_privs=bool(raw.get("no_new_privs", True)),
            pids_max=int(raw.get("pids_max", 0)),
            notes=tuple(str(item) for item in raw.get("notes", ())),
        )

    @classmethod
    def from_json(cls, text: str) -> SecurityPlan:
        return cls.from_dict(json.loads(text or "{}"))

    # --------------------------------------------------------------------- rules

    def landlock_ruleset(self) -> LandlockRuleset:
        """The ruleset this plan describes, with the ABI **and the file type** applied.

        Rule order matters for one thing only: ``/`` is granted read-only first, then the
        writable roots widen it. Landlock unions the matching rules, so a writable root inside a
        read-only ``/`` still ends up writable — which is exactly the intent.

        The second correction is the one that bites: a rule's ``allowed_access`` is checked
        against the **file type of the parent** the rule is attached to. A non-directory accepts
        only ``EXECUTE|WRITE_FILE|READ_FILE|TRUNCATE|IOCTL_DEV`` — asking for ``MAKE_REG`` under
        ``/dev/null`` is ``EINVAL``, not a narrowing. Measured on this host with a live ruleset
        (``/dev/null`` + the directory bits → ``EINVAL``; the non-directory subset → ``0``).
        Codex's Landlock path asks for the whole ``from_all`` set here, which is why their
        equivalent call site is documented upstream as "currently unused": we run it live, so
        the mask has to exist.
        """
        abi = self.landlock_abi or landlock_abi()
        read_only, masked = _mask_access(READ_ONLY_FS_ACCESS, abi)
        write, more = _mask_access(WRITE_FS_ACCESS, abi)
        masked += more
        handled, even_more = _mask_access(FULL_FS_ACCESS, abi)
        masked += even_more
        rules: list[tuple[str, int]] = [
            (path, _access_for_path(path, read_only)) for path in self.read_only_paths
        ]
        rules.append(("/dev/null", _access_for_path("/dev/null", read_only | write)))
        rules += [
            (path, _access_for_path(path, read_only | write)) for path in self.read_write_paths
        ]
        if self.sandbox_tmp:
            # the mechanism's own scratch space — a private tmpfs, not the host's /tmp
            rules.append(
                (self.sandbox_tmp, _access_for_path(self.sandbox_tmp, read_only | write))
            )
        net_handled = 0
        net_rules: list[tuple[int, int]] = []
        if abi >= LANDLOCK_ABI_NET:
            if self.network == "off":
                # handled with no rule at all: every TCP connect and bind returns EACCES
                net_handled = LANDLOCK_ACCESS_NET_CONNECT_TCP | LANDLOCK_ACCESS_NET_BIND_TCP
            elif self.egress_ports:
                net_handled = LANDLOCK_ACCESS_NET_CONNECT_TCP
                net_rules = [
                    (int(port), LANDLOCK_ACCESS_NET_CONNECT_TCP) for port in self.egress_ports
                ]
        return LandlockRuleset(
            abi=abi,
            handled_access_fs=handled,
            handled_access_net=net_handled,
            path_rules=tuple(rules),
            net_port_rules=tuple(net_rules),
            sandbox_tmp=self.sandbox_tmp,
            masked=tuple(dict.fromkeys(masked)),
        )


# ----------------------------------------------------------------- the report


@dataclass
class SecurityReport:
    """What was actually applied in the child — one boolean per control, plus the measurements."""

    mechanism: str = MECHANISM_NONE
    profile_sha256: str = ""
    controls: dict[str, bool] = field(default_factory=dict)
    refused: str = ""
    notes: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)
    applied_at_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "mechanism": self.mechanism,
            "profile_sha256": self.profile_sha256,
            "controls": dict(self.controls),
            "refused": self.refused,
            "notes": list(self.notes),
            "details": dict(self.details),
            "applied_at_ms": int(self.applied_at_ms),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> SecurityReport:
        raw = dict(data or {})
        return cls(
            mechanism=str(raw.get("mechanism", MECHANISM_NONE)),
            profile_sha256=str(raw.get("profile_sha256", "")),
            controls={str(k): bool(v) for k, v in dict(raw.get("controls") or {}).items()},
            refused=str(raw.get("refused", "")),
            notes=[str(item) for item in raw.get("notes", ())],
            details=dict(raw.get("details") or {}),
            applied_at_ms=int(raw.get("applied_at_ms", 0)),
        )

    @classmethod
    def from_json(cls, text: str) -> SecurityReport:
        return cls.from_dict(json.loads(text or "{}"))

    @property
    def enforced(self) -> list[str]:
        return sorted(name for name, ok in self.controls.items() if ok)

    @property
    def missing(self) -> list[str]:
        return sorted(name for name in LIMITS if not self.controls.get(name))


# --------------------------------------------------------------- apply (child)


def apply_plan(
    plan: SecurityPlan,
    *,
    report: bool = True,
    stream: Any = None,
) -> SecurityReport:
    """Apply ``plan`` to the current process. Order is the contract:

    1. ``PR_SET_NO_NEW_PRIVS`` — required before Landlock and before seccomp;
    2. Landlock ``restrict_self`` — the filesystem and network policy;
    3. seccomp — the syscall-class policy.

    A control whose plan mode is ``enforce`` and which cannot be applied raises
    :class:`SecurityRefusal`. A control in ``auto`` mode that is unavailable is *reported as
    not applied* instead — the caller decides whether that is fatal.
    """
    out = stream if stream is not None else sys.stderr
    started = int(time.time() * 1000)
    result = SecurityReport(mechanism=plan.mechanism, profile_sha256=plan.profile_sha256)
    result.controls = {name: False for name in LIMITS}
    result.notes.extend(plan.notes)

    # 1. no_new_privs — always, unconditionally: it is what makes 2 and 3 unbypassable.
    set_no_new_privs()
    result.controls[CONTROL_NO_NEW_PRIVS] = no_new_privs_is_set()
    if not result.controls[CONTROL_NO_NEW_PRIVS]:  # pragma: no cover — prctl returned 0
        raise SecurityRefusal(
            CONTROL_NO_NEW_PRIVS, "prctl succeeded but /proc/self/status still says NoNewPrivs: 0"
        )
    result.details["no_new_privs"] = {"readback": "NoNewPrivs: 1"}

    # 2. Landlock
    if plan.landlock == "enforce":
        ruleset = plan.landlock_ruleset()
        applied = apply_landlock(ruleset)
        result.controls[CONTROL_LANDLOCK] = True
        result.details["landlock"] = {**applied, "ruleset": ruleset.describe()}
        if applied["masked"]:
            result.notes.append(
                "this kernel cannot enforce " + ", ".join(applied["masked"])
            )
        if applied["paths_dropped"]:
            result.notes.append(
                "declared paths absent on disk, so no rule was added: "
                + ", ".join(applied["paths_dropped"])
            )
    else:
        result.notes.append(
            f"landlock: {plan.landlock}"
            + (f" ({landlock_unavailable_reason()})" if plan.landlock == "unavailable" else "")
        )

    # 3. seccomp
    if plan.seccomp == "enforce":
        deny_classes = tuple(plan.seccomp_classes)
        program = build_seccomp_program(
            deny_classes=deny_classes, network_denied=(plan.network == "off")
        )
        apply_seccomp(program)
        result.controls[CONTROL_SECCOMP] = True
        material: tuple[str, ...] = (
            PRIVILEGE_CLASS_SYSCALLS if "privilege" in deny_classes else ()
        )
        if "network" in deny_classes and plan.network == "off":
            material += NET_CLASS_SYSCALLS
        denied = [
            {"name": name, "number": syscall_number(name)}
            for name in dict.fromkeys(material)
        ]
        result.details["seccomp"] = {
            "program_len": len(program),
            "action": "SECCOMP_RET_ERRNO(EPERM)",
            "arch_checked": True,
            "unix_sockets_allowed": True,
            "family_gate_installed": bool(plan.network == "off" and "network" in deny_classes),
            "denied": denied,
        }
    else:
        result.notes.append(f"seccomp: {plan.seccomp}")

    # the cgroup membership is not applied here: the parent puts the whole child tree in the
    # leaf in its `preexec_fn`, so bwrap/the launcher and every descendant are inside it.
    result.controls[CONTROL_PIDS_CGROUP] = bool(plan.pids_max)
    result.details["pids"] = {
        "max": int(plan.pids_max),
        "source": "cgroup v2 pids controller" if plan.pids_max else "not declared",
    }
    result.applied_at_ms = int(time.time() * 1000) - started
    if report:
        out.write(f"{REPORT_PREFIX} {result.to_json()}\n")
        out.flush()
    return result


# --------------------------------------------------- report lines / refusals


def _protocol_payloads(text: str, prefix: str) -> list[dict[str, Any]]:
    """Every JSON object that follows ``prefix`` — found wherever the line starts.

    Searching rather than line-matching is the same correction as
    :func:`strip_report_lines`: a command that writes without a trailing newline leaves the
    protocol line glued to its own bytes, and a parser that only looks at line starts would then
    report "the child never confirmed" for a child that confirmed perfectly well.
    """
    pattern = re.compile(re.escape(prefix) + r" (\{[^\n]*\})")
    found: list[dict[str, Any]] = []
    for match in pattern.finditer(str(text or "")):
        try:
            data = json.loads(match.group(1))
        except (TypeError, ValueError):
            continue
        if isinstance(data, dict):
            found.append(data)
    return found


def report_from_output(text: str) -> SecurityReport | None:
    """The last report line in captured output, or ``None`` when the child never wrote one."""
    payloads = _protocol_payloads(text, REPORT_PREFIX)
    return SecurityReport.from_dict(payloads[-1]) if payloads else None


def refusal_from_output(text: str) -> dict[str, Any] | None:
    """The refusal the child wrote instead of running the command, if there is one."""
    payloads = _protocol_payloads(text, REFUSAL_PREFIX)
    return payloads[-1] if payloads else None


_PROTOCOL_PREFIXES = REPORT_PREFIX + "|" + REFUSAL_PREFIX
_PROTOCOL_LINE = re.compile(
    # (1) a protocol line that really is alone on its line: eat the *leading* newline and keep the
    #     trailing one, so the command's own lines never get glued together;
    r"(?:\n|^)(?:" + _PROTOCOL_PREFIXES + r") \{[^\n]*\}[ \t]*(?=\n|$)"
    # (2) a protocol line glued to the end of command output that had no newline of its own
    r"|(?:" + _PROTOCOL_PREFIXES + r") \{[^\n]*\}[ \t]*\n?"
)


def strip_report_lines(text: str) -> str:
    """Remove the protocol lines so the caller sees the *command's* output and nothing else.

    Two properties matter, and both are behavioural:

    * the cut happens **wherever the prefix appears**, not at a line start — a command that writes
      without a trailing newline (``printf "%s" …``) leaves the report glued to its own last byte,
      and a line-start test would hand the agent a JSON blob appended to its hash;
    * everything else is **byte-identical**, so an output cap that spills exactly what the command
      wrote keeps spilling exactly that (a ``split("\\n")``/``join`` version added one byte per
      removed line and made a 50 000-byte spill 50 001).
    """
    return _PROTOCOL_LINE.sub("", str(text or ""))


def mechanism_for(*, landlock_mode: str, bwrap: str, abi: int) -> tuple[str, str]:
    """``(mechanism, reason)`` — which enforcement stack will run, and why *that* one.

    The two fallbacks are both real and both named:

    * no Landlock (older kernel, or the syscall is blocked by a seccomp policy of our own
      parent) → the pre-existing bubblewrap path carries the policy;
    * no bubblewrap (a distro without it, or an unprivileged container where user namespaces
      are refused) → Landlock alone carries it, and whatever it cannot express is reported.
    """
    landlock_ok = landlock_mode == "enforce" and int(abi) >= LANDLOCK_ABI_MIN
    if bwrap and landlock_ok:
        return MECHANISM_BWRAP_LANDLOCK, "bubblewrap mount view + in-process Landlock/seccomp"
    if landlock_ok:
        return MECHANISM_LANDLOCK, (
            "no bubblewrap on this host: Landlock alone enforces the declared paths and the "
            "network policy"
        )
    if bwrap:
        reason = (
            "Landlock is not available here "
            f"({landlock_unavailable_reason() or landlock_mode}) — the bubblewrap path carries "
            "the filesystem, network-namespace and capability policy"
        )
        return MECHANISM_BWRAP, reason
    return MECHANISM_NONE, "neither bubblewrap nor Landlock is available"


# --------------------------------------------------------------------- launcher


def _usage() -> str:
    return (
        "usage: python security_linux.py exec --permission-profile JSON "
        "--permission-profile-sha256 HEX --security-plan JSON [--cwd DIR] -- CMD …\n"
        "       python security_linux.py probe [--json]\n"
        "       python security_linux.py coverage [--json]   "
        "(per-primitive coverage: enforced/degraded/unavailable + the reason, as a table; "
        "--json for the machine-readable report; exit 3 when a primitive is not enforced)\n"
    )


#: flag → the key it fills. Written out rather than derived: a derived name is exactly how
#: a launcher silently reads the wrong option.
_EXEC_FLAGS: dict[str, str] = {
    "--permission-profile": "profile",
    "--permission-profile-sha256": "sha256",
    "--security-plan": "plan",
    "--cwd": "cwd",
}


def _parse_exec(argv: list[str]) -> dict[str, Any]:
    options: dict[str, Any] = {
        "profile": "",
        "sha256": "",
        "plan": "",
        "cwd": "",
        "command": [],
    }
    index = 0
    while index < len(argv):
        item = argv[index]
        if item == "--":
            options["command"] = argv[index + 1 :]
            break
        key = _EXEC_FLAGS.get(item)
        if key is None:
            raise SecurityRefusal("usage", f"unknown argument {item!r}", fix=_usage())
        if index + 1 >= len(argv):
            raise SecurityRefusal("usage", f"{item} needs a value", fix=_usage())
        options[key] = argv[index + 1]
        index += 2
    return options


def _split_cmd(command: list[str]) -> tuple[str, list[str]]:
    """``-- /bin/sh -c 'text'`` → ``('/bin/sh', ['/bin/sh', '-c', 'text'])``."""
    return (command[0] if command else ""), list(command)


def _prefixed(name: str) -> str:
    """``SKALDIR_<name>``, falling back to the legacy ``GODSV_<name>`` spelling.

    This module runs as a standalone script and must not import the package (it is the child
    process, started before any restriction is applied), so it cannot use ``skaldir.env``. It
    still honours both spellings, which is the part of the rename contract that matters here.
    ``tests/test_rename_compat.py`` carries it in ``PREFIX_LITERAL_EXCEPTIONS`` for exactly this
    reason.
    """
    for prefix in ("SKALDIR_", "GODSV_"):
        value = os.environ.get(prefix + name)
        if value is not None:
            return value
    return ""


def _prepare_ownership_check(claimed: str, canonical: str) -> None:
    """The anti-tamper check: the hash the argv claims must be the hash of the JSON it carries.

    This is the child-side half of non-drift. A wrapper that rewrites one of the two (or the
    environment variable) is refused *before* any restriction is applied, so a policy cannot be
    swapped between the block the agent read and the process that runs.
    """
    import hashlib

    recomputed = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    if claimed and claimed.strip().lower() != recomputed:
        raise SecurityRefusal(
            "permission_profile",
            f"the argv claims profile {claimed[:12]}… but the JSON it carries hashes to "
            f"{recomputed[:12]}…",
            fix="re-emit the argv from the profile object; never edit a profile in place",
        )
    from_env = _prefixed("PERMISSION_PROFILE")
    if from_env and from_env.strip() != canonical.strip():
        raise SecurityRefusal(
            "permission_profile",
            "SKALDIR_PERMISSION_PROFILE and --permission-profile disagree",
            fix="the environment carries a different policy than the argv — re-run from "
            "the profile",
        )
    from_env_hash = _prefixed("PERMISSION_PROFILE_SHA256").strip().lower()
    if from_env_hash and from_env_hash != recomputed:
        raise SecurityRefusal(
            "permission_profile",
            f"SKALDIR_PERMISSION_PROFILE_SHA256 says {from_env_hash[:12]}… but the JSON hashes to "
            f"{recomputed[:12]}…",
            fix="re-emit the argv from the profile object",
        )


def run_exec(argv: list[str], *, stream: Any = None) -> int:
    """The launcher: apply the plan, then become the command. Never returns on success."""
    out = stream if stream is not None else sys.stderr
    try:
        options = _parse_exec(argv)
        canonical = str(options["profile"] or "").strip()
        claimed = str(options["sha256"] or "").strip()
        shell, command = _split_cmd(list(options["command"]))
        if not canonical or not shell:
            raise SecurityRefusal(
                "usage", "a permission profile and a command are both required", fix=_usage()
            )
        _prepare_ownership_check(claimed, canonical)
        plan = SecurityPlan.from_json(str(options["plan"] or "{}"))
        if not plan.profile_sha256:
            plan = replace(plan, profile_sha256=claimed)
        _maybe_join_cgroup(plan, out)
        result = apply_plan(plan, stream=out)
    except SecurityRefusal as refusal:
        payload = {
            "control": refusal.control,
            "reason": refusal.reason,
            "fix": refusal.fix,
            "mechanism": SecurityPlan.from_json(str(options["plan"] or "{}")).mechanism,
        }
        out.write(f"{REFUSAL_PREFIX} {json.dumps(payload, sort_keys=True)}\n")
        out.flush()
        return EXIT_REFUSED
    except (OSError, ValueError) as exc:
        payload = {"control": "launcher", "reason": f"{type(exc).__name__}: {exc}", "fix": ""}
        out.write(f"{REFUSAL_PREFIX} {json.dumps(payload, sort_keys=True)}\n")
        out.flush()
        return EXIT_REFUSED
    del result
    cwd = str(options["cwd"] or "").strip()
    try:
        if cwd:
            os.chdir(cwd)
        os.execv(shell, command)
    except OSError as exc:
        out.write(
            f"{REFUSAL_PREFIX} "
            f"{json.dumps({'control': 'exec', 'reason': f'{type(exc).__name__}: {exc}'})}\n"
        )
        out.flush()
        return int(getattr(exc, "errno", None) or EXIT_REFUSED)
    return 0  # pragma: no cover — os.execv does not return


def _maybe_join_cgroup(plan: SecurityPlan, out: Any) -> None:
    """Join the cgroup named by the environment, when the parent asked for one.

    The parent puts the *spawned* process into the leaf with a ``preexec_fn``; this is the
    launcher's own defence for the case where it was started some other way (a hand-written
    argv, a wrapper). It is idempotent and it is the *only* filesystem write this module
    performs, so it happens before Landlock closes the gate.
    """
    path = _prefixed("SECURITY_CGROUP").strip()
    if not path or not plan.pids_max:
        return
    try:
        with open(Path(path) / "cgroup.procs", "w", encoding="utf-8") as handle:
            handle.write(str(os.getpid()))
    except OSError as exc:  # reported, never fatal: the parent already placed us
        note = f"cgroup join skipped: {type(exc).__name__}: {exc}"
        payload = {"mechanism": plan.mechanism, "controls": {}, "notes": [note]}
        out.write(f"{REPORT_PREFIX} {json.dumps(payload)}\n")
        out.flush()


def audit_arch() -> int | None:
    """The ``AUDIT_ARCH_*`` constant for the running architecture, or ``None``.

    An arch-checked seccomp filter cannot be built without it, so this is the one question
    :meth:`PermissionProfile.seccomp_mode` asks. ``None`` is also the answer on any non-Linux
    platform: the table carries an ``arm64`` entry, and without this gate a macOS arm64 host
    would have reported ``seccomp = enforce`` — a claim about a kernel that has no seccomp.
    """
    if not is_linux():
        return None
    return AUDIT_ARCH.get(_machine())


def probe(*, as_json: bool = True) -> dict[str, Any]:
    """What this host actually offers — every answer measured, none assumed."""
    abi = landlock_abi(refresh=True)
    ok, path, reason = pids_cgroup_support()
    data: dict[str, Any] = {
        "machine": _machine(),
        "python": sys.version.split()[0],
        "landlock": {
            "abi": abi,
            "available": abi >= LANDLOCK_ABI_MIN,
            "network_rules": abi >= LANDLOCK_ABI_NET,
            "ioctl_dev": abi >= LANDLOCK_ABI_IOCTL_DEV,
            "reason": "" if abi >= LANDLOCK_ABI_MIN else landlock_unavailable_reason(),
        },
        "seccomp": {
            "backend": "classic BPF assembled in-process (no libseccomp)",
            "available": _machine() in _SYSCALL_NUMBERS and AUDIT_ARCH.get(_machine()) is not None,
            "audit_arch": AUDIT_ARCH.get(_machine()),
            "program_len": len(
                build_seccomp_program(deny_classes=("privilege", "network"), network_denied=True)
            ),
        },
        "no_new_privs": {"available": True, "mechanism": "prctl(PR_SET_NO_NEW_PRIVS, 1)"},
        "pids_cgroup": {"available": ok, "parent": path, "reason": reason},
        "rlimit_nproc": rlimit_nproc_verdict(),
        "bwrap": {"path": _which("bwrap")},
    }
    return data


def _which(name: str) -> str:
    import shutil

    return shutil.which(name) or ""


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help", "help"):
        sys.stderr.write(_usage())
        return 1 if not args else 0
    verb, rest = args[0], args[1:]
    if verb == "exec":
        return run_exec(rest)
    if verb == "probe":
        data = probe()
        if "--json" in rest or not rest:
            sys.stdout.write(json.dumps(data, indent=2, sort_keys=True) + "\n")
        else:
            for key in ("machine", "python"):
                sys.stdout.write(f"{key}: {data[key]}\n")
            sys.stdout.write(f"landlock: ABI {data['landlock']['abi']}\n")
            sys.stdout.write(f"seccomp program: {data['seccomp']['program_len']} instructions\n")
            sys.stdout.write(f"pids cgroup: {data['pids_cgroup']['available']}\n")
        return 0
    if verb == "coverage":
        # The user-facing answer to "what do I actually get on this machine?" — one row per
        # primitive, the reason for every row that is not `enforced`, and exit 3 when any of
        # them is not enforced (so a script cannot mistake a downgrade for coverage).
        report = platform_report()
        if "--json" in rest:
            sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
        else:
            sys.stdout.write(
                f"{report['platform']} kernel {report['kernel'] or '(unknown)'} "
                f"supported={report['supported']} coverage={report['verdict']}\n"
            )
            for row in report["primitives"]:
                sys.stdout.write(
                    f"  {row['primitive']:14} {row['status']:11} {row['reason']}\n"
                )
            if not report["supported"]:
                sys.stdout.write(f"  {report['reason']}\n")
                if report["parity_requirements"]:
                    sys.stdout.write("  parity would require:\n")
                    for item in report["parity_requirements"]:
                        sys.stdout.write(f"    - {item}\n")
        return 0 if report["verdict"] == STATUS_ENFORCED else 3
    sys.stderr.write(f"security_linux: unknown verb {verb!r}\n{_usage()}")
    return 1


if __name__ == "__main__":  # pragma: no cover — the launcher entry point
    raise SystemExit(main())
