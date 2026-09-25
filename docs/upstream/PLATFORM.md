# PLATFORM — where the enforcement actually runs

This document is about **runtime enforcement**: which operating systems the sandbox can really
restrict an agent command on, what each control degrades to when it is not available, and what a
user sees when it is not. It is not the distribution/packaging matrix — installers, artefacts and
signing identities are in `docs/audit/PLATFORM-MATRIX.md`; this file is the runtime half.

Every claim below is measured on the machine named at the end of each section, not inferred. The
raw artefacts live next to the round's record (`MVP/84-platform-guard-artifacts/`), and the code
that produces them is `src/skaldir/security_linux.py` (`platform_coverage`, `platform_support`)
plus the surfaces in `src/skaldir/permission_profile.py`, `src/skaldir/approval_cli.py` and the
`ENFORCE` layer of `src/skaldir/cli_contract.py`.

---

## 1. Supported platform set

**Linux only.** One entry: `linux` (any architecture whose syscall table this module has verified —
`x86_64`/`amd64`, `aarch64`/`arm64`, `riscv64`, `loongarch64`; an unverified table is reported as
"not supported here" rather than called with a foreign number).

| Target | Runtime enforcement | Why |
|---|---|---|
| Linux x86_64 | yes | measured on `7.2.5-200.fc44.x86_64` |
| Linux aarch64 | yes, not measured here | same primitives; the ABI/arch detection is table-driven |
| macOS (arm64/x64) | **no** | every primitive is Linux-only (§4) |
| Windows | **no** | every primitive is Linux-only (§4) |
| WSL2 | Linux kernel ⇒ as Linux | the kernel is Linux; the *front ends* (`bwrap`, cgroup delegation) are what usually differ |

What "supported" means concretely: the sandbox **runs the command only inside a mechanism that
enforces something**. When no mechanism exists, the command is refused — see §5.

## 2. The six primitives, what each needs, and what is lost without it

The enforcement stack is six pieces, in the order the launcher applies them
(`src/skaldir/security_linux.py`). `docs/SECURITY-BOUNDARIES.md` covers the policy side; this table
is the mechanism side.

| Primitive | Needs | Covers | Without it | What still blocks (measured) |
|---|---|---|---|---|
| `no_new_privs` | Linux 3.5+, `prctl(PR_SET_NO_NEW_PRIVS, 1)` | `execve` cannot gain privilege; no unprivileged user namespace | nothing blocks a setuid/file-capability elevation, and Landlock + seccomp cannot be installed at all | n/a — and the profile model **refuses to declare it off** (`LinuxSecurityPolicy.__post_init__`), measured: *"linux_security.no_new_privs cannot be turned off…"* |
| `landlock` | Linux 5.13+ (ABI 1); ABI 4 (5.19+) for the TCP rules | kernel-side filesystem policy; TCP connect/bind allowlist | the sandbox becomes a mount view only — every path the mounts got wrong is reachable | bubblewrap's mount view still refused the write (`EROFS`), seccomp still refused the connect (`EPERM`) |
| `namespaces` (bubblewrap) | Linux user/pid/net/mount namespaces + unprivileged userns + the `bwrap` binary | the mount view (protected paths carved out, private `/tmp`), the pid and net namespace | protected subpaths are no longer carved out of a writable root, `/tmp` is the host's `/tmp`, the run keeps a network namespace | Landlock still refused the write (`EACCES`), seccomp still refused the connect (`EPERM`) |
| `seccomp` | Linux 3.5+, an `AUDIT_ARCH_*` constant for the architecture | the syscall classes (mount/ptrace/bpf/…) and every socket family except `AF_UNIX` | mount/ptrace/bpf and non-`AF_UNIX` sockets are callable | Landlock's TCP rules still refused the connect (`EACCES`) |
| `pids_cgroup` | Linux cgroup v2 with the pids controller, **delegated** to the user session | `pids.max` on the whole child tree; a fork past the cap fails with `EAGAIN` | nothing caps the tree; a fork bomb is bounded only by host limits | with `max_processes = 8` and the cap **on**: 4 forks then `EAGAIN`; with it **off**: 40 of 40 forks succeeded |
| `rlimits` | POSIX `setrlimit(RLIMIT_AS)` — Linux and macOS; absent on Windows | `RLIMIT_AS` on the child | nothing bounds the child's address space; only the wall-clock timeout does | with a 256 MiB cap the payload died with `MemoryError` (the cap is real — even the interpreter); with no cap: 1 GiB allocated fine |

`RLIMIT_NPROC` is deliberately **not** the process cap: it counts every process of the real uid, so
a cap below the user's live process count blocks all forking and above it does nothing
(`rlimit_nproc_verdict()` carries the measurement).

## 3. Degradation matrix — one primitive switched off at a time

Measured by running the product's own runner (`permission_profile.run_in_sandbox`) with one
primitive disabled **through the policy** (`linux_security.*`, `caps.*`, `sandbox_backend`) — no
kernel change, no privilege needed. Escape attempts: write a file outside the workspace, open a TCP
connection to a listener on the host, fork 40 processes.
Host: `Linux 7.2.5-200.fc44.x86_64`, Fedora 44, cgroup v2 unified, unprivileged userns enabled,
`bwrap` `/usr/bin/bwrap`, Landlock ABI 10. Raw: `84-degradation-matrix.json`.

| Variant | Mechanism | Write outside workspace | TCP connect | Forks (cap 8) | `coverage` verdict |
|---|---|---|---|---|---|
| all primitives on | `bwrap+landlock` | refused — `OSError: [Errno 30] Read-only file system` | refused — `PermissionError: [Errno 1] Operation not permitted` | 4, then `EAGAIN` | enforced |
| `landlock` off | `bwrap` | refused — `EROFS` | refused — `EPERM` | 4, then `EAGAIN` | degraded |
| `seccomp` off | `bwrap+landlock` | refused — `EROFS` | refused — `EACCES` (Landlock net) | 4, then `EAGAIN` | degraded |
| no bubblewrap (`landlock` only) | `landlock` | refused — `EACCES` (Landlock) | refused — `EPERM` | 6, then `EAGAIN` | degraded |
| no bubblewrap **and** no `landlock` | `none` | **the command never started** — exit 3, `blocked` | same | — | degraded |
| `pids_cgroup = off` with `max_processes = 8` | `bwrap+landlock` | refused — `EROFS` | refused — `EPERM` | **40 of 40** — nothing caps the tree | degraded |
| `max_memory_bytes = 268435456` | `bwrap+landlock` | every payload died with `MemoryError` | — | — | enforced |
| every kernel control off | `none` | **never started** — exit 3, `blocked`, reason *"sandbox unavailable — execution blocked (fail-closed): neither bubblewrap nor Landlock is available"* | same | — | degraded |

Two facts from this matrix matter for the honesty rules:

* **No primitive is load-bearing on its own.** Each escape attempt is refused in every variant
  *except* the two where nothing runs at all — the controls are layered, which is why the coverage
  model reports them one by one instead of printing a single "sandboxed: yes".
* **The single-primitive gaps are real where they are real.** With the pids cap off, the fork bomb
  ran to completion; with the others off the payload still failed, and the error code names *which*
  layer refused it (`EROFS` = mount view, `EACCES` = Landlock, `EPERM` = seccomp).

## 4. macOS and Windows: what a user gets today

Nothing is enforced, and the product says so instead of degrading quietly.

Since this wave, detection is gated on the real platform (`detected_platform()`, `is_linux()`):
no Landlock/seccomp/prctl call is issued on a kernel that does not have it. The bug that gate
closes is concrete — `AUDIT_ARCH` is keyed by *machine name* and contains `arm64`, so before the
gate a **macOS arm64** host would have answered `seccomp_mode() = enforce` (a claim about a kernel
with no seccomp), and `landlock_abi()` would have issued `syscall(444)`, which on the Darwin table
is a different syscall.

Measured for `darwin` in a subprocess with `sys.platform` patched *before* the import and `PATH`
emptied (so no Linux front ends are found) — `84-platform-simulation.json`:

| Reading | darwin |
|---|---|
| `landlock_abi` / reason | `0` — "Landlock is a Linux-only kernel feature; this platform is darwin" |
| `audit_arch` | `None` (the unguarded table entry for `arm64` is `3221225598`) |
| `landlock_mode` / `seccomp_mode` / `pids_cgroup_mode` | `unavailable` / `unavailable` / `unavailable` |
| `mechanism` | `none` |
| `run_in_sandbox` | `blocked = true`, exit 3, reason *"sandbox unavailable — execution blocked (fail-closed): neither bubblewrap nor Landlock is available"*; the escape file was never created |
| `set_no_new_privs()` | `SecurityRefusal`: *"prctl(PR_SET_NO_NEW_PRIVS) is a Linux call and this platform is darwin"* |
| coverage | 5 × `unavailable`, `rlimits` `degraded` (*"no sandbox mechanism on this platform: the runner refuses the command before a child exists (fail-closed), so this control never runs"*), verdict `unavailable`, `supported = false` |

Windows: the same **process-level** simulation is *not possible on a Linux host* — patching
`sys.platform` to `win32` changes which branch the stdlib `shutil` imports (`_winapi`), so the
import itself fails: `ModuleNotFoundError: No module named '_winapi'`. Windows is therefore
measured through the coverage model, which is pure data: `landlock`, `seccomp`, `no_new_privs`,
`namespaces`, `pids_cgroup` and `rlimits` all `unavailable`, verdict `unavailable`,
`supported = false`. **A real Windows or macOS host has not been exercised** — that is a statement
about evidence, not a claim about behaviour.

So what does a macOS/Windows user actually get? **Nothing is enforced, and nothing runs.** The
sandbox path refuses an agent command (exit 3, fail-closed) because there is no mechanism to
enforce anything with. What the CLI does *outside* the sandbox path is out of scope here and is not
claimed: this round measured the enforcement answer, not the packaging or the GUI/dialog surface of
those platforms. The refusal is now visible in three places:

* `skaldir selftest --layer ENFORCE` → probe `enforcement_primitives`, status `blocked`, listing
  every primitive with its status and the reason;
* `skaldir permission explain` → an "enforcement primitives on this platform" block, one row per
  primitive, and `platform_verdict.execution_refused = true` in `--json`;
* `python -m skaldir.security_linux coverage` → the same table, exit 3 when any primitive is not
  `enforced`.

### What parity would require

Listed per platform in `PARITY_REQUIREMENTS` (`src/skaldir/security_linux.py`) and printed by
`coverage` on an unsupported host:

* **macOS** — a `sandbox-exec`/Seatbelt (SBPL) front end instead of bubblewrap's argv; a Seatbelt
  filesystem/network policy in place of Landlock; no unprivileged seccomp exists (Seatbelt is the
  closest, deprecated-but-present); no cgroup v2, so a per-run process cap needs a launchd/Mach
  equivalent (RLIMIT_NPROC is per-user, see above).
* **Windows** — AppContainer or a restricted-token job object as the front end (not bubblewrap's
  argv); AppContainer capabilities + a Windows Filtering Platform rule set for filesystem/network;
  no seccomp equivalent (ACG/CIG mitigation policies are per-process opt-ins, not a syscall filter);
  `JOB_OBJECT_LIMIT_ACTIVE_PROCESS` for the process cap; a job-object memory limit in place of
  `RLIMIT_AS`.

## 5. The fail-closed rule (why "unsupported" is not "unprotected")

`run_in_sandbox` has **no silent unprotected fallback**: with no mechanism the run is `blocked`
(exit 3) and the command is never started; a control the profile *requires* that the host cannot
apply refuses the command before it starts, naming the control, the kernel's reason and the fix.
Measured twice in §3 (variants "no bubblewrap and no `landlock`" and "every kernel control off"):
`blocked = true`, exit 3, no payload output at all.

The coverage model must never present a downgrade as enforcement, so a primitive is
`enforced` only when it is applied **and** complete:

* `unavailable` — this platform/kernel cannot do it (reason names the platform or the errno);
* `degraded` — the host could do it and the policy does not (declared off, no cap declared, no
  `bwrap` binary), or it is applied with reduced coverage (`landlock` on ABI 1–3: no TCP rules);
* `enforced` — applied and complete.

A profile's verdict is the worst row; the two absent-by-default caps (`pids_cgroup`, `rlimits`)
make the *default* profile report `degraded` on a fully capable Linux host — because the default
declares no process and no memory cap. That is a policy choice, and it is reported as one.

## 6. How to check your own machine

```sh
# the enforcement coverage of this host, one row per primitive (exit 3 when not all enforced)
python -m skaldir.security_linux coverage            # table;  --json for the report

# what the product's sandbox does on this machine, with the reasons
skaldir selftest --layer ENFORCE                     # probe: enforcement_primitives

# the profile's view: enforcement matrix + this platform's primitives
skaldir permission explain
skaldir permission explain --json | python -m json.tool | grep -A3 platform_verdict
```

A `degraded` or `unavailable` row always carries its reason; `skaldir selftest --layer ENFORCE`
exits 3 (`blocked`, not `failed`) when no mechanism exists at all, because that is a platform fact
and not a broken installation.

## 7. Live GUI verification (Computer Use / `act`)

This is a separate axis from §1–6 — no kernel control is involved, but "can the agent act on a
real desktop?" has the same problem shape: the answer depends on the machine, and the product used
to leave the user with `live_desktop_session_present: false` and no next step.

### 7.1 What the product does today

`skaldir act check --json` reports every bridge, and on this host (a GNOME **Wayland** session,
`DISPLAY=:0`, `WAYLAND_DISPLAY=wayland-0`) it says:

| Fact | Value |
|---|---|
| `verdict` | `isolated-only` |
| `live_desktop` | `false` — the *measured* opt-in, not a literal: it is only true when the switch is set **and** a target is named |
| `live_desktop_optin` | `{allowed: false, opted_in: false, target: "", reason: "…set computer_use.allow_live_desktop…"}` — the reason names the switch |
| `host.live_desktop_session_present` | `true` (a real session is there) |
| `host.isolation_tools` | `bwrap`, `podman`, `xdotool`, `Xvfb`, `wtype`, `ydotool` — all present |
| `host.gui_image` | `localhost/godsv-cu-x11:latest` present |
| bridges available/real | `container-gui`, `container`, `sandbox`, **`host-session`** (an Xvfb this run owns); `dry-run` available but a fixture |
| `live-desktop` bridge | unavailable until opted in: *"the live desktop is refused: CUPolicy.require_isolation rejects any runner whose isolation is 'none', and the opt-in is not set — …"* |
| `observe.screenshot` | **available** (driver `xwd`) — the image ships `xwd`+`base64`; the XWD dump is decoded into a real PNG by the application (§7.4) |
| `host.session.screenshot` | available via the host capture tools (`import`) on the display a run owns |
| `app.state` | unavailable: *"no channel reads the target app's own state (a11y tree / DOM / app API) — window titles are the only observation channel today"* |
| `policy.require_isolation` | `true` (and `policy.allow_live_desktop: false`, `policy.live_desktop_target: ""`) |

So: the product verifies GUI work on an **isolated** display — Xvfb `:90` inside a rootless podman
container built from `docker/cu-x11.Containerfile`, or Xvfb `:91`+ on the host owned by the run
(`--runner host-session`) — and refuses the user's own desktop unless it is explicitly opted in
**and** target-scoped (§7.3).

The same matrix is reachable from the legacy `skaldir cu check` (kept for compatibility: it used
to hard-code `live_desktop: false` and warn that no live-desktop driver was wired — both were
stale, and it now reports `act check`'s own record).

### 7.2 Enable the isolated path (this works today, on your machine)

```sh
# 1. the image the GUI runner expects (once)
podman build -t localhost/godsv-cu-x11:latest -f docker/cu-x11.Containerfile docker/

# 2. what this host can really do — read `verdict`, `bridges`, and `host.isolation_tools`
skaldir act check --json

# 3. verify the drivers *inside* the image rather than trusting the note
skaldir act check --probe-image --json

# 4. run a declared GUI flow on the isolated display and verify the end state
skaldir act run --runner container-gui --actions flow.json --json

# 5. the same run with pixel evidence and the lifecycle report
skaldir act run --runner container-gui --actions flow.json --screenshot --json

# 6. a display THIS RUN owns on the host (no opt-in needed: nothing of yours is touched)
skaldir act run --runner host-session --actions host-flow.json --screenshot --json
```

`flow.json` is `{actions: [...], end_state: {...}}`; every action must declare how it is verified.
Evidence from this machine (`item12-act-run.json`): three actions (`cli` opening a window, `window`
listing window names, `key` sending a keystroke) all `verified`, `runner.isolation = container`,
`runner.reason = "isolated X display :90 (rootless podman, localhost/godsv-cu-x11:latest)"`, and the
end-state check observed the real window title `SKALDIR-GUARD-DEMO` read back from the isolated
display. The run record is appended to `<runtime>/cu_runs.jsonl`.

`--screenshot` adds `data.screenshot{path, sha256, width, height, scope, label, converter}` (the
capture is named after the window the observation read) and every run — verified, failed or
blocked — carries `data.cleanup{container, closed, still_present, owned}`: a GUI run closes the
container/display it started and says whether anything is left. A capture that cannot happen is
reported as absent with its reason, never as an empty file.

### 7.3 Enable a *live* desktop (your own session) — implemented, gated, **unexercised here**

The code exists and is bounded; what has **not** been done on this machine is driving a real window
of the owner's session (the status paragraph below says exactly that). The three pieces:

1. **A channel that reads the target application's own state.** The product's channel order is
   API > DOM > Vision.
   * API — the app's own API, declared as a sub-tool action (`channel: "api"`).
   * DOM — a browser started with `chromium --remote-debugging-port=9222`; the DOM channel reads
     the page, not pixels.
   * Vision — a screenshot. This host has `import` (ImageMagick); the GUI image ships `xwd` +
     `base64` and the application decodes the XWD dump into a PNG itself (`observe.screenshot`,
     available). `app.state` (the app's a11y tree / DOM) is still **not** wired.
2. **A driver that can inject input into your session.** X11: `xdotool` works directly. Wayland:
   `ydotool` (needs its daemon and `/dev/uinput`) or `wtype` (virtual-keyboard protocol, compositor
   support required). Both are installed on this host; the Wayland session is the reason a
   `DISPLAY=:0` X driver is not automatically the right answer.
3. **The policy switch and a live runner — implemented, and it takes two facts.** `CUPolicy` now
   carries `allow_live_desktop` / `live_desktop_target`, `cu_host_session.HostSessionRunner` can
   attach to a named window (`xdotool windowfocus --sync <id>`; a pointer action must land inside
   that window's geometry; a title matching more than one window is **refused**, never guessed),
   and `computer_use._check_policy` lets a non-isolated runner through only when the policy opted
   in **and** named the same target the runner declares as its own `target_scope`. The switch is
   read from `settings.yaml` (`computer_use.allow_live_desktop: true` plus
   `computer_use.live_desktop_target`) or the environment (`SKALDIR_CU_ALLOW_LIVE_DESKTOP=1`,
   `SKALDIR_CU_LIVE_TARGET=<window>`); `act check` publishes it as `live_desktop_optin`, and
   **an opt-in without a target is not an opt-in**.

   In live mode the runner also **builds every command it runs itself** (report 18, F1): a
   declared action's `kind` + `payload` + the resolved target window id are the only inputs,
   the target id must be the *window operand* of an allowlisted `xdotool` subcommand (never a
   substring of the argument list), and a pointer action's absolute point is derived from the
   target's own geometry. A declared `cli`/`window` action — whose command comes from the plan
   — is therefore **refused** in live mode rather than executed, and a caller-supplied `argv`
   is never run; keys, text, pointer and the window capture are what the live path carries. A
   live-mode capture accepts only the resolved target window id, and an owned display is
   claimed only when its socket did not exist before `start()`.

**What the product would then verify** is what it verifies on the isolated display today, under the
same rules: each action is checked against its own declared expectation, an end-state observation is
read from the real state *after* the actions, and the run is recorded — with the scope (`real` vs
`in-process fixture`) published alongside the verdict, so a fixture can never be reported as a real
end state.

**Status: `unverified on this host` for a user's own desktop.** The bridge, the runner, the gate and
the refusals exist and are covered by unit tests, but **no action has ever been sent to `:0`** here:
this wave deliberately did not launch anything into the owner's session to test it. The live path
should not be reported as proven until an operator drives **one named window of their own session**
and captures the result (`skaldir act run --runner live-desktop --live-target '<window>' …`, with
the opt-in set). What is *not* missing any more: the switch, the target scope, the window-scoped
key/pointer path and the refusal when a title matches more than one window.

### 7.4 What *was* exercised here (isolated sessions only)

Two isolated sessions were driven on this machine, and **neither is the user's desktop**:

* the product's own path — Xvfb `:90` **inside the rootless podman container**, §7.2 evidence;
* a host-side **isolated** session — Xvfb `:91` (800×600, `-nolisten tcp`) + `openbox`, holding a
  real GTK window: window id `0x00400005`, title **`SKALDIR-GUARD-DEMO-ISOLATED`**, shown by the
  process **`zenity --info --title=SKALDIR-GUARD-DEMO-ISOLATED …` (pid 1658631)**; a real GUI action
  (`xdotool mousemove` + `click 1`) succeeded and the window title was read back afterwards.
  Screenshot: `MVP/84-platform-guard-artifacts/item12-isolated-session.png` (800×600 sRGB, 17 632 B)
  — it shows that window's title text and body text; GTK draws the title inside the client window,
  so no window-manager title bar is visible in the capture. Both sessions were torn down afterwards
  (no `Xvfb :91` left).

Pitfall worth knowing before anyone tries this on their own machine: this host is a GNOME Wayland
session, so a GUI toolkit with `WAYLAND_DISPLAY` set does not necessarily honour `DISPLAY=:91` and
selects the real compositor instead. Measured effect: the first attempt (zenity on `:91` with
`WAYLAND_DISPLAY=wayland-0` still exported) left `:91` with **no** window at all — `wmctrl -lp` and
`xdotool search` were empty and the root capture was blank — while the process was alive, i.e. the
window went somewhere other than the session the measurement claimed to use. An "isolated" session
must pin the X backend; the working recipe is `unset WAYLAND_DISPLAY`, `GDK_BACKEND=x11`,
`QT_QPA_PLATFORM=xcb` (see `item12_isolated_session.sh`).
