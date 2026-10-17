"""DockerRuntimeManager — docker binary + daemon probe + pull/pin + lifecycle.

Block B6 (§2/§3 of docs/IMPLEMENTATION-PLAN-docker-tool-builder.md).

Deliberate design choices:

* **Pin by digest, never by tag.** ``pull_and_pin`` returns the digest the
  daemon actually inspected; ``verify_pinned_digest`` raises a
  :class:`DigestMismatch` whenever the manifest's pinned digest diverges —
  an untraceable mutable tag is rejected, not mapped onto whatever is local.
* **No privileges at the config layer.** ``RunConfig`` refuses its own
  construction with ``privileged=True``; the mount validator refuses the docker
  socket, every protected host path and the host home; the network defaults to
  ``none``; ports are published only through explicit ``port_bindings``.
* **Structured failures.** :meth:`classify_pull_error` /
  :meth:`classify_start_error` produce :class:`RuntimeFailure` records with a
  taxonomy class (`image_missing`, `arch_mismatch`, `port_conflict`,
  `quota_limit`, `cleanup_partial`) and a machine-readable payload — the
  buildflow can then repair the infrastructure as infrastructure, never the
  adapter. A policy refusal is structured the same way
  (:func:`policy_refusal`, ``failure_class="policy_violation"`` + ``policy``):
  :meth:`DockerRuntimeManager.run` / ``start`` never raise, so a refused
  config cannot crash the tool builder (N12).
* **Self-cleaning one-shots.** ``docker_argv("run", one_shot=True)`` puts
  ``--rm`` on the command line, so a build probe leaves no stopped container
  behind; a long-lived service start keeps ownership of its lifetime
  (``-d --rm`` in :meth:`DockerRuntimeManager.start`) and stays controllable
  through its cid file.
* **Teardown is label-scoped, never name-based.** Every container Skaldir
  creates carries the owner label ``godsv.owner=skaldir`` (plus
  ``godsv.build_id`` / ``godsv.capability``); :meth:`cleanup_by_labels` refuses
  a label set that is not ours and only ever removes the ids a
  LABEL-FILTERED listing returned. The listing emits **one ``--filter`` per
  label** (:func:`label_filter_args`) — docker does not AND a comma-joined
  ``--filter label=a=1,b=2`` (it silently matches nothing, measured on Server
  29.6.1), and that form made every multi-label sweep a no-op that still looked
  green. No ``docker system prune``, no name guess — the daemon is shared with
  foreign compose stacks (this host: `penpot-local`).
* **Executor seam.** Instances satisfy the
  ``skaldir.execution.ContainerExecutor`` protocol (``available()`` +
  ``run_job()``): :meth:`DockerRuntimeManager.run_job` maps one job onto ONE
  one-shot ``--rm`` run and answers with the boundary's own ``RunResult`` — a
  refusal is ``not-certifiable``, never a pass — so the formerly-Null seam can be
  registered without touching the shared boundary's policy.
"""

from __future__ import annotations

import json
import platform
import re
import shutil
import subprocess  # nosec B404 — driving the docker CLI is this module's purpose
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover — typing only; no import cycle at runtime
    from .execution import RunResult

DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

#: Host paths that must NEVER be mounted into a container (A.1 policy).
_PROTECTED_HOST_PATHS = (
    "/",
    "/etc",
    "/bin",
    "/boot",
    "/dev",
    "/lib",
    "/lib64",
    "/proc",
    "/root",
    "/run",
    "/sbin",
    "/sys",
    "/usr",
    "/var",
    "/var/run/docker.sock",
    "/home",
)

#: Substrings of a `docker start` failure caused by a busy host port.
_PORT_HINTS = (
    "address already in use",
    "port is already allocated",
)

#: (substring of stderr, failure class) of a failed `docker pull`.
_PULL_HINTS: tuple[tuple[str, str], ...] = (
    ("manifest unknown", "image_missing"),
    ("not found: does not exist", "image_missing"),
    (": not found", "image_missing"),  # live message: "…library/alpine:tag: not found"
    ("pull access denied", "image_missing"),
    ("repository does not exist", "image_missing"),
    ("no matching manifest for", "arch_mismatch"),
    ("no such manifest for", "arch_mismatch"),
    # a registry rate limit. The live shape of Docker Hub's answer (measured
    # 2026-09-18) is `…unexpected status from HEAD request to
    # https://registry-1.docker.io/v2/library/alpine/manifests/3.20: 429 Too Many
    # Requests` — it contains neither "toomanyrequests" nor the old wording, so
    # it used to fall through to the generic `docker_error` while the build
    # really was rate-limited.
    ("429 too many requests", "quota_limit"),
    ("too many requests", "quota_limit"),
    ("toomanyrequests", "quota_limit"),
    ("pull rate limit", "quota_limit"),
    ("rate limit", "quota_limit"),
    ("rate exceeded", "quota_limit"),
)


#: Stable policy ids a structured refusal carries in ``StartResult.policy``.
POLICY_PIN_REQUIRED = "pin-required"
POLICY_IMAGE_REQUIRED = "image-required"
POLICY_RUN_CONFIG = "run-config"
POLICY_LABEL_SCOPE = "label-scope-required"

#: The human reason of each policy id (English, one sentence).
POLICY_REASONS: dict[str, str] = {
    POLICY_PIN_REQUIRED: "no pinned digest — an untraceable mutable tag is rejected",
    POLICY_IMAGE_REQUIRED: "container config needs image or image_digest",
    POLICY_RUN_CONFIG: "the run config violates the container policy",
    POLICY_LABEL_SCOPE: "teardown without our labels — refusing to remove a foreign container",
}

#: Every container Skaldir creates carries the owner label (policy L7).
OWNER_LABEL_KEY = "godsv.owner"
OWNER_LABEL_VALUE = "skaldir"

#: ``docker run`` exit codes docker itself uses when IT failed (docker's own
#: contract: 125 = docker error, 126/127 = entrypoint/command not executable or not
#: found). A job that ends on one of these never ran to completion, so it is
#: ``not-certifiable`` — not a failed workload. A workload's own exit code is
#: passed through unchanged and IS a failure.
_DOCKER_LEVEL_EXIT_CODES = frozenset({125, 126, 127})

#: The fields a container job may carry (a ``ContainerJob``-shaped object or a
#: mapping): the only surface :meth:`DockerRuntimeManager.run_job` reads.
_JOB_FIELDS = (
    "image_digest",
    "image",
    "name",
    "workdir",
    "argv",
    "mounts",
    "resource_limits",
    "labels",
    "network",
    "readonly_rootfs",
    "timeout",
)


class PolicyViolation(ValueError):
    """A container config the safety policy forbids outright."""


class DockerUnreachable(RuntimeError):
    """No docker binary / no answering daemon where one was required."""


class DigestMismatch(RuntimeError):
    """Image on disk does not match the pinned digest the manifest carries."""


@dataclass
class CmdResult:
    exit_code: int
    stdout: str
    stderr: str
    timeout: bool = False


@dataclass
class RuntimeFailure:
    """Structured failure: taxonomy class + human detail + machine payload."""

    failure_class: str
    detail: str
    payload: dict[str, Any] = field(default_factory=dict)
    ok: bool = False

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "failure_class": self.failure_class,
            "detail": self.detail[:400],
            "payload": dict(self.payload),
        }


@dataclass
class StartResult:
    """Structured outcome of pull/start/exec — carries taxonomy on failure."""

    ok: bool
    detail: str = ""
    cid: str = ""
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    failure_class: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    #: the policy that refused (only set when ``failure_class == "policy_violation"``)
    policy: str = ""

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "detail": self.detail[:300],
            "cid": self.cid,
            "exit_code": self.exit_code,
            "stdout": self.stdout[:2000],
            "stderr": self.stderr[:2000],
            "failure_class": self.failure_class,
            "payload": dict(self.payload),
            "policy": self.policy,
        }


def policy_refusal(problems: list[str]) -> StartResult:
    """A structured refusal — the policy, the reason and the problem ids as data.

    This is the shape ``run()`` / ``start()`` return instead of raising a
    :class:`PolicyViolation`: ``ok=False``, ``failure_class="policy_violation"``,
    ``policy`` naming the rule that refused, ``payload["problems"]`` listing
    every problem found, ``detail`` carrying an English sentence.
    """
    items = [str(item) for item in (problems or []) if str(item)]
    if POLICY_IMAGE_REQUIRED in items and POLICY_PIN_REQUIRED in items:
        # nothing to pin at all — the image is the more fundamental refusal
        policy = POLICY_IMAGE_REQUIRED
    elif POLICY_PIN_REQUIRED in items:
        policy = POLICY_PIN_REQUIRED
    elif POLICY_IMAGE_REQUIRED in items:
        policy = POLICY_IMAGE_REQUIRED
    else:
        policy = POLICY_RUN_CONFIG
    if not items:
        items = [policy]
    reason = POLICY_REASONS[policy]
    if "network-must-be-none-to-publish-ports" in items:
        reason = (
            f"{reason} (declare port_bindings with default network='none' so the "
            "config layer promotes none -> bridge; explicit network overrides with "
            "published ports are refused)"
        )
    return StartResult(
        ok=False,
        detail=f"policy refused ({policy}): {reason}; problems: {', '.join(items)}"[:300],
        failure_class="policy_violation",
        policy=policy,
        payload={"policy": policy, "reason": reason, "problems": items},
    )


def owned_labels(**extra: str) -> dict[str, str]:
    """The label set that marks a container as Skaldir's own (policy L7)."""
    labels = {OWNER_LABEL_KEY: OWNER_LABEL_VALUE}
    labels.update({str(key): str(value) for key, value in extra.items() if str(value)})
    return labels


def _is_owned_label_key(key: str) -> bool:
    """True for our label namespace (``godsv`` / ``godsv.*``) — nothing else."""
    text = str(key or "")
    return text == "godsv" or text.startswith("godsv.")


def label_filters(labels: dict[str, str]) -> str:
    """The label scope as the ``key=value,key=value`` text reports carry.

    This is a *description* of the scope, never an argument: docker does not
    AND a comma-joined label string (see :func:`label_filter_args`). Every
    docker call site goes through that helper.
    """
    return ",".join(f"{key}={value}" for key, value in (labels or {}).items())


def label_filter_args(labels: dict[str, str]) -> list[str]:
    """``["--filter", "label=key=value"]`` per label — docker ANDs separate filters.

    docker does **not** AND a comma-joined label string: ``--filter
    label=a=1,b=2`` is read as ONE label pair (the value being ``1,b=2``) and so
    matches **nothing** — silently, exit 0, zero results (measured live on
    Server 29.6.1: 0 hits for a container carrying both labels, while two
    separate ``--filter`` arguments return it). A multi-label scope must
    therefore emit one ``--filter`` PER label.

    This is the ONE place the filter line is built, so no caller can rebuild the
    comma-joined form (that form made every label-scoped teardown a silent
    no-op: ``cleanup_owned`` reported ``removed 0 leftovers`` while the
    container stayed on the daemon).
    """
    args: list[str] = []
    for key, value in (labels or {}).items():
        args += ["--filter", f"label={key}={value}"]
    return args


def verify_pinned_digest(*, actual: str, expected: str, image: str) -> None:
    """Pin check: digest divergence is FATAL — no warning, no partial trust."""
    if not expected or not (expected.startswith("sha256:") and len(expected) == 71):
        raise DigestMismatch(
            f"manifest pins no pin-shaped digest for {image}: {expected!r}"
        )
    if (actual or "").strip() != expected:
        raise DigestMismatch(
            f"digest mismatch for {image}: manifest pinned {expected}, "
            f"daemon inspected {actual or 'nothing'} — untraceable mutable tag"
        )


def validate_mounts(mounts: list[dict], *, workdir: Path) -> list[str]:
    """The mount allowlist: the tool's own paths, nothing else.

    ``workdir`` is the tool's own build scratch directory (usually
    ``<runtime>/generated_tools/<name>``). A host path inside ``workdir`` or
    in a sibling directory of the same build tree AND same filesystem root is
    allowed. `/`, `/etc`, docker socket and host home are refused outright.
    """
    workdir = Path(workdir).resolve()
    problems: list[str] = []
    for mount in mounts or []:
        host_path = str(mount.get("host_path", ""))
        if not str(mount.get("container_path", "")).startswith("/"):
            problems.append("bad-container-path")
        host_absolute = str(host_path)
        if host_absolute in _PROTECTED_HOST_PATHS:
            problems.append("protected-host-path")
            continue
        home = str(Path.home())
        resolved = str(Path(host_absolute).resolve()) if host_absolute else ""
        if resolved == home or resolved.startswith(f"{home}/"):
            problems.append("protected-host-path")
            continue
        if "docker.sock" in host_absolute:
            problems.append("protected-host-path")
            continue
        try:
            host = Path(host_absolute).resolve()
            inside = host == workdir or workdir in host.parents
        except OSError:
            inside = False
        if not inside:
            problems.append("outside-workdir")
    return problems


@dataclass
class RunConfig:
    """Container config of a build-time run — validate-then-use, no defaults hidden."""

    image: str = ""
    image_digest: str = ""
    name: str = ""
    workdir: Path | None = None  # scratch dir the tool paths bind from
    entrypoint_argv: list[str] = field(default_factory=list)
    mounts: list[dict] = field(default_factory=list)
    resource_limits: dict = field(default_factory=dict)
    network: str = "none"
    port_bindings: dict[int, int] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)
    readonly_rootfs: bool = False
    privileged: bool = False
    #: The image's ENTRYPOINT to run with. ``None`` (the default) leaves the
    #: image's own entrypoint in charge — the behaviour every earlier caller
    #: relies on. ``""`` emits ``--entrypoint ""``, which CLEARS it so the argv
    #: is the command: that is how a probe reaches an image whose entrypoint
    #: would otherwise swallow it (``dpokidov/imagemagick`` declares
    #: ``ENTRYPOINT ["convert"]``, ``jrottenberg/ffmpeg`` ``["/bin/ffmpeg"]``).
    entrypoint: str | None = None
    _network_promoted: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.privileged:
            raise PolicyViolation(
                "privileged containers are forbidden (docker_runtime safety policy)"
            )
        for mount in self.mounts or []:
            if "docker.sock" in str(mount.get("host_path", "")):
                raise PolicyViolation(
                    "mounting the docker socket is forbidden "
                    "(docker_runtime safety policy)"
                )
        # L7: everything this config creates is OURS by label — that is the only
        # scope a teardown may use later, so it is enforced at the config layer
        # instead of being the caller's memory. An explicitly set owner label is
        # left untouched.
        if OWNER_LABEL_KEY not in (self.labels or {}):
            self.labels = dict(self.labels or {})
            self.labels[OWNER_LABEL_KEY] = OWNER_LABEL_VALUE

    def validate(self, *, workdir: Path | None = None) -> list[str]:
        effective_workdir = workdir if workdir is not None else self.workdir
        if not (self.image or self.image_digest):
            raise PolicyViolation("container config needs image or image_digest")
        if self.network != "none" and self.port_bindings:
            if not (self._network_promoted and self.network == "bridge"):
                return ["network-must-be-none-to-publish-ports"]
        for _c, host in (self.port_bindings or {}).items():
            if not (0 < int(host) < 65536):
                return ["bad-host-port"]
        if self.port_bindings:
            # docker silently DROPS -p under --network none (verified live on
            # Server 29.6.1) — publishing ports requires a real network.
            self.network = self.network or "none"
            if self.network == "none":
                self.network = "bridge"
                self._network_promoted = True
        if self.privileged:
            raise PolicyViolation(
                "privileged containers are forbidden (docker_runtime safety policy)"
            )
        if effective_workdir is not None:
            problems = validate_mounts(self.mounts, workdir=effective_workdir)
            if problems:
                return problems
        return []

    def policy_problems(self, *, workdir: Path | None = None) -> list[str]:
        """Every policy refusal of this config as DATA — this method never raises.

        ``validate()`` stays the strict config API (it raises for a config with
        neither image nor digest, and for a privileged one). The runtime layer
        calls *this* method so a refused config comes back as a structured
        :func:`policy_refusal` result instead of an uncaught ``PolicyViolation``
        (policy N12). The returned ids are the refusal's machine payload.
        """
        problems: list[str] = []
        if not (self.image or self.image_digest):
            problems.append(POLICY_IMAGE_REQUIRED)
        if not self.image_digest:
            problems.append(POLICY_PIN_REQUIRED)
        try:
            problems += [str(item) for item in self.validate(workdir=workdir)]
        except PolicyViolation as exc:
            problems.append(f"{type(exc).__name__}: {exc}")
        return problems

    def docker_argv(self, verb: str, *, one_shot: bool = False) -> list[str]:
        """The `docker <verb>` argv — ONE place builds the command line.

        ``one_shot=True`` (a build probe, a tool invocation) puts ``--rm``
        directly after the verb, so the container removes itself when it exits:
        one-shot runs are self-cleaning and never leave a stopped container
        behind (policy N1). The long-lived path passes ``one_shot=False`` (the
        default) and owns its lifetime — :meth:`DockerRuntimeManager.start`
        adds its own ``-d --rm`` and keeps the cid.
        """
        if not self.image_digest:
            raise PolicyViolation("no pinned digest — an untraceable mutable tag is rejected")
        if verb != "run":
            return ["docker", verb]
        argv = ["docker", "run"]
        if one_shot:
            argv += ["--rm"]
        if self.name:
            argv += ["--name", self.name]
        argv += ["--network", self.network or "none"]
        argv += ["--security-opt", "no-new-privileges"]
        if self.readonly_rootfs:
            argv += ["--read-only"]
        for container_port, host_port in (self.port_bindings or {}).items():
            argv += ["-p", f"{int(host_port)}:{int(container_port)}"]
        for mount in self.mounts or []:
            readonly = mount.get("readonly", True)
            spec = (
                f"type=bind,src={mount.get('host_path', '')},"
                f"dst={mount.get('container_path', '')},readonly"
                if readonly
                else (
                    f"type=bind,src={mount.get('host_path', '')},"
                    f"dst={mount.get('container_path', '')}"
                )
            )
            argv += ["--mount", spec]
        limits = self.resource_limits or {}
        if limits.get("cpu"):
            argv += [f"--cpus={limits['cpu']}"]
        if limits.get("ram"):
            argv += [f"--memory={limits['ram']}"]
        for key, value in (self.labels or {}).items():
            argv += ["--label", f"{key}={value}"]
        if self.entrypoint is not None:
            # BEFORE the image: `--entrypoint ""` clears the image's own
            # entrypoint so the argv is the command, not its arguments
            argv += ["--entrypoint", self.entrypoint]
        argv += [self.image_digest]
        argv += list(self.entrypoint_argv)
        return argv

    readonly_rootfs: bool = False


# --------------------------------------------------------------------------- #
# error taxonomy
# --------------------------------------------------------------------------- #


def classify_pull_error(result: CmdResult, image: str) -> RuntimeFailure:
    """Map a failed `docker pull` to the taxonomy + structured payload."""
    text = (result.stdout + "\n" + result.stderr).strip()
    low = text.lower()
    payload: dict[str, Any] = {"image": image, "exit_code": result.exit_code}
    if result.timeout:
        payload["timeout"] = True
        return RuntimeFailure("timeout", "pull timed out", payload)
    failure_class = "docker_error"
    for hint, klass in _PULL_HINTS:
        if hint in low:
            failure_class = klass
            break
    if failure_class == "arch_mismatch":
        payload["host_arch"] = platform.machine()
        hit = re.search(r"no matching manifest for ([a-z0-9/]+)", low)
        if hit:
            payload["requested_platform"] = hit.group(1)
    if failure_class == "docker_error":
        payload["raw"] = text[:400]
    return RuntimeFailure(failure_class, text[:300], payload)


def classify_start_error(result: CmdResult, name: str) -> RuntimeFailure:
    """Map a failed `docker run/start` to the lifecycle taxonomy."""
    text = (result.stdout + "\n" + result.stderr).strip()
    payload: dict[str, Any] = {"name": name, "exit_code": result.exit_code}
    if result.timeout:
        payload["timeout"] = True
        return RuntimeFailure("timeout", "start timed out", payload)
    if any(hint in text.lower() for hint in _PORT_HINTS):
        failure_class = "port_conflict"
        hit = re.search(r"0\.0\.0\.0:(\d+)", text)
        if hit:
            payload["host_port"] = int(hit.group(1))
    elif "no such image" in text.lower():
        failure_class = "image_missing"
    elif "oci" in text.lower() and ("arch" in text.lower() or "platform" in text.lower()):
        failure_class = "arch_mismatch"
    else:
        failure_class = "docker_error"
        payload["raw"] = text[:400]
    return RuntimeFailure(failure_class, text[:300], payload)


# --------------------------------------------------------------------------- #
# manager
# --------------------------------------------------------------------------- #


class DockerRuntimeManager:
    """One docker CLI: probe the daemon, pull/pin, run, exec, stop/remove.

    Also the container executor of the shared execution boundary: ``available()``
    plus :meth:`run_job` (one job = one one-shot ``--rm`` run, structured
    ``execution.RunResult``).
    """

    def __init__(self, *, docker_bin: str | None = None) -> None:
        self.docker_bin = docker_bin or shutil.which("docker") or "docker"

    # -- CLI plumbing ----------------------------------------------------------
    def _invoke_raw(
        self, argv: list[str], *, timeout: float = 120.0, workdir: Path | None = None
    ) -> CmdResult:
        """Raw call with a hard timeout; a missing binary reports, never fakes."""
        try:
            proc = subprocess.run(  # nosec B603 — fixed argv, no shell
                argv,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(workdir) if workdir else None,
            )
        except subprocess.TimeoutExpired:
            return CmdResult(-9, "", "", timeout=True)
        except FileNotFoundError as exc:
            return CmdResult(127, "", f"docker binary not found: {exc}")
        except OSError as exc:
            return CmdResult(125, "", f"{type(exc).__name__}: {exc}")
        return CmdResult(
            proc.returncode,
            (proc.stdout or "").strip(),
            (proc.stderr or "").strip(),
        )

    def _invoke(
        self, argv: list[str], *, timeout: float = 120.0, workdir: Path | None = None
    ) -> CmdResult:
        """Prefix-normalized call — argv['0'] must be the manager's binary."""
        if argv and argv[0] in ("docker", self.docker_bin):
            argv = [self.docker_bin, *argv[1:]]
        return self._invoke_raw(
            list(argv),
            timeout=timeout,
            workdir=workdir,
        )

    # -- daemon probe -----------------------------------------------------------
    def available(self) -> tuple[bool, str]:
        """True iff docker `version` AND `info` answer (Welle-2 contract)."""
        version = self._invoke(
            ["docker", "version", "--format", "{{json .}}"], timeout=15.0
        )
        if version.exit_code != 0:
            return False, (version.stderr or version.stdout)[:200] or "/docker binary missing"
        info = self._invoke(
            ["docker", "info", "--format", "{{json .}}"], timeout=15.0
        )
        if info.exit_code != 0:
            return False, (info.stderr or info.stdout)[:200] or "no docker daemon answered"
        try:
            payload = json.loads(info.stdout)
        except json.JSONDecodeError as exc:
            return False, f"info returned unparsable JSON: {exc}: {info.stdout[:120]}"
        server = payload.get("ServerVersion", "")
        arch = payload.get("Architecture", "")
        return True, f"Server Version {server} ({arch})"

    # -- pull + pin --------------------------------------------------------------
    def pull(self, image: str, *, timeout: float = 600.0) -> StartResult:
        """`docker pull <image>`; failures map to the pull taxonomy."""
        result = self._invoke(["docker", "pull", image], timeout=timeout)
        if result.exit_code == 127:
            return StartResult(
                ok=False, detail=result.stderr[:200], failure_class="docker_unreachable"
            )
        if result.exit_code != 0:
            err = classify_pull_error(result, image)
            return StartResult(
                ok=False, detail=err.detail, failure_class=err.failure_class, payload=err.payload
            )
        inspect = self._invoke(
            ["docker", "image", "inspect", "--format", "{{.Id}}", image], timeout=30.0
        )
        digest = inspect.stdout.strip() if inspect.exit_code == 0 else ""
        if not digest.startswith("sha256:"):
            return StartResult(
                ok=False,
                detail=f"no digest inspectable for {image}",
                failure_class="docker_unreachable",
                payload={"image": image},
            )
        return StartResult(ok=True, cid=digest, stdout=digest, detail="pulled")

    def pull_and_pin(
        self, image: str, *, digest_hint: str | None = None, timeout: float = 600.0
    ) -> str:
        """Pull + return the pinned digest; a `digest_hint` divergence is fatal."""
        result = self.pull(image, timeout=timeout)
        if not result.ok:
            raise DockerUnreachable(f"pull failed ({result.failure_class}): {result.detail}")
        actual = result.stdout.strip()
        if digest_hint:
            verify_pinned_digest(actual=actual, expected=digest_hint, image=image)
        return actual

    def resolve_digest(self, image: str, *, timeout: float = 60.0) -> str:
        """Digest of an ALREADY-local image (no network) — "" when absent."""
        inspect = self._invoke(
            ["docker", "image", "inspect", "--format", "{{.Id}}", image], timeout=timeout
        )
        return inspect.stdout.strip() if inspect.exit_code == 0 else ""

    def verify_pinned(self, image: str, expected: str) -> None:
        """Raise :class:`DigestMismatch` when the local image diverges from `expected`."""
        verify_pinned_digest(
            actual=self.resolve_digest(image), expected=expected, image=image
        )

    # -- lifecycle ---------------------------------------------------------------
    @staticmethod
    def _refused(config: RunConfig, *, workdir: Path | None = None) -> StartResult | None:
        """Structured refusal for a policy-forbidden config — ``None`` when clean.

        This is why ``run()`` / ``start()`` cannot raise: the config is asked for
        its problems as data (never ``validate()``'s raise) before any docker
        process exists.
        """
        problems = config.policy_problems(workdir=workdir)
        return policy_refusal(problems) if problems else None

    def start(
        self, config: RunConfig, *, cid_file: Path | None = None, timeout: float = 60.0
    ) -> StartResult:
        """`docker run -d --rm` from a validated config; the cid goes to ``cid_file``.

        Never raises: a config the policy forbids comes back as a structured
        :func:`policy_refusal` result (``failure_class="policy_violation"``).
        """
        refusal = self._refused(config)
        if refusal is not None:
            return refusal
        # start a LONG-LIVING service container; a caller wanting a daemon
        # overrides the entrypoint (e.g. `sleep infinity`) in its own config.
        try:
            base_argv = config.docker_argv("run")  # long-lived: no --rm here
        except PolicyViolation as exc:  # the config API keeps its promise to raise
            return policy_refusal([str(exc)])
        if config.entrypoint_argv:
            base_argv = base_argv[: -len(config.entrypoint_argv)]
        image = base_argv.pop()
        argv = base_argv + ["-d", "--rm", image]
        if config.entrypoint_argv:
            # a real service start binds entrypoint_argv as the container cmd
            argv += list(config.entrypoint_argv)
        result = self._invoke(argv, timeout=timeout)
        if result.exit_code != 0:
            err = classify_start_error(result, config.name)
            return StartResult(
                ok=False, detail=err.detail, failure_class=err.failure_class, payload=err.payload
            )
        cid = result.stdout.strip()
        if cid_file is not None and cid:
            try:
                cid_file.parent.mkdir(parents=True, exist_ok=True)
                cid_file.write_text(cid + "\n", encoding="utf-8")
            except OSError:
                pass
        return StartResult(ok=True, cid=cid, detail="started")

    def run(
        self, config: RunConfig, *, timeout: float = 60.0, **_kw: Any
    ) -> StartResult:
        """One-shot: `docker run --rm` with the tool's entrypoint — no tail.

        The argv carries ``--rm`` (``docker_argv("run", one_shot=True)``) so the
        container removes itself: a build probe leaves **no** stopped container
        behind, not even when it fails (policy N1). Never raises — a refused
        config returns a structured :func:`policy_refusal` result, which is what
        the tool builder consumes.
        """
        refusal = self._refused(config)
        if refusal is not None:
            return refusal
        try:
            argv = config.docker_argv("run", one_shot=True)
        except PolicyViolation as exc:  # the config API keeps its promise to raise
            return policy_refusal([str(exc)])
        result = self._invoke(argv, timeout=timeout)
        if result.exit_code != 0 and (result.stdout + result.stderr).strip():
            err = classify_start_error(result, config.name)
            return StartResult(
                ok=False,
                exit_code=result.exit_code,
                stdout=result.stdout,
                stderr=result.stderr,
                detail=err.detail,
                failure_class=err.failure_class,
                payload=err.payload,
            )
        return StartResult(
            ok=result.exit_code == 0,
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            detail="ran" if result.exit_code == 0 else f"exit {result.exit_code}",
        )

    # -- execution boundary (skaldir.execution.ContainerExecutor) -------------------
    @staticmethod
    def _job_field(job: Any, name: str, default: Any = None) -> Any:
        """One field of a container job — a mapping and an object read the same."""
        if isinstance(job, dict):
            return job.get(name, default)
        return getattr(job, name, default)

    @classmethod
    def _job_refusal(cls, job: Any) -> str:
        """``""`` when the job maps onto a run, else the English reason it cannot.

        This is why :meth:`run_job` never raises for a foreign object: an
        unprepared job (``object()``, ``None``, a job type of another layer) comes
        back as a structured ``not-certifiable`` answer, the same shape
        ``execution.NullExecutor`` returns.
        """
        if isinstance(job, RunConfig):
            return ""
        if job is None:
            return "the docker runtime has no job to run: got None"
        carried = [
            field
            for field in _JOB_FIELDS
            if cls._job_field(job, field, None) not in (None, "", [], {})
        ]
        if not carried:
            return (
                "the docker runtime refuses a job it cannot map: got "
                f"{type(job).__name__}, which carries none of "
                f"{', '.join(_JOB_FIELDS)}"
            )
        return ""

    @classmethod
    def _job_labels(cls, job: Any) -> dict[str, str]:
        """The run's labels: always OUR owner label, plus the job's own labels.

        The owner label is what makes the one-shot reachable by
        :meth:`cleanup_by_labels` — a job can never create an unlabelled container,
        and no sweep can ever reach a foreign one.
        """
        labels = owned_labels()
        job_labels = dict(cls._job_field(job, "labels", {}) or {})
        labels.update({str(key): str(value) for key, value in job_labels.items()})
        return labels

    def _job_config(self, job: Any, *, policy: Any) -> tuple[RunConfig, float]:
        """(the one :class:`RunConfig` this job maps onto, its timeout) — mapping only."""
        timeout = float(self._job_field(job, "timeout", 60.0) or 60.0)
        if isinstance(job, RunConfig):
            return job, timeout
        workdir = self._job_field(job, "workdir", None)
        network = str(self._job_field(job, "network", "") or "none")
        if bool(getattr(policy, "allow_network", False)):
            # the boundary GRANTS the network; a job cannot take it for itself
            network = "bridge"
        return (
            RunConfig(
                image=str(self._job_field(job, "image", "") or ""),
                image_digest=str(self._job_field(job, "image_digest", "") or ""),
                name=str(self._job_field(job, "name", "") or ""),
                workdir=Path(str(workdir)) if workdir else None,
                entrypoint_argv=[
                    str(arg) for arg in self._job_field(job, "argv", []) or []
                ],
                mounts=[dict(mount) for mount in self._job_field(job, "mounts", []) or []],
                resource_limits=dict(self._job_field(job, "resource_limits", {}) or {}),
                network=network,
                labels=self._job_labels(job),
                readonly_rootfs=bool(self._job_field(job, "readonly_rootfs", False)),
                entrypoint=self._job_field(job, "entrypoint", None),
            ),
            timeout,
        )

    @staticmethod
    def _capped(text: str, policy: Any) -> str:
        """The boundary's own output cap — the same rule, not a second one."""
        from . import execution

        limit = int(getattr(policy, "max_output_bytes", 200_000) or 200_000)
        return execution._truncate(str(text or ""), limit)  # noqa: SLF001 — one cap rule

    def _sweep_timed_out_job(self, job: Any) -> str:
        """Remove the container of a job whose ``docker run`` CLI we had to kill.

        Killing the `docker run` client does **not** stop the container it started
        (measured CLI behaviour), so a timeout can leave one running. The sweep is
        scoped to the JOB's own labels and only runs when the job narrows the owner
        label (``godsv.build_id`` / ``godsv.capability`` / …) — never a name, never a
        blanket owner sweep, so no other Skaldir container and no foreign one can be
        hit. The outcome is reported in the result's ``detail``.
        """
        labels = self._job_labels(job)
        narrowing = {key: value for key, value in labels.items() if key != OWNER_LABEL_KEY}
        if not narrowing:
            return " | the timed-out job carried no narrowing label to sweep by"
        scope = label_filters(labels)
        swept = self.cleanup_by_labels(labels)
        if not swept.ok:
            return f" | timeout sweep of {scope} failed: {swept.detail}"
        removed = list(swept.payload.get("removed") or [])
        return f" | timeout sweep removed {len(removed)} container(s) of {scope}"

    def run_job(self, job: Any, policy: Any = None) -> RunResult:
        """One containerised job, run to completion — the ``ContainerExecutor`` seam.

        ``available()`` + ``run_job()`` is the protocol ``skaldir.execution``
        registers a container executor with; this method makes the manager itself
        that executor, so the shared boundary needs no second adapter object.

        **One job = ONE one-shot container run.** The argv carries ``--rm`` (the
        self-cleaning path, policy N1) and the run is label-scoped with
        ``godsv.owner=skaldir`` plus the job's own labels, so a finished job leaves
        no container behind and any leftover could only ever be swept by label,
        never by name. The answer is the boundary's own
        :class:`skaldir.execution.RunResult`:

        * ``status="ok"`` — the container ran and exited 0;
        * ``status="failed"`` — the container ran and exited non-zero (its own code
          is passed through);
        * ``status="not-certifiable"`` — nothing ran: no mappable job, no daemon, no
          pinned digest, a policy-refused config (mount allowlist, privileged
          container, port/network rule) or a docker-level failure (docker's own exit
          codes 125/126/127, a killed CLI / timeout).

        ``sandboxed`` is True only for a run that really happened inside the
        container — the container *is* the isolation boundary here.

        A job is a :class:`RunConfig`, a ``ContainerJob``-shaped object or a mapping
        of those fields (``image_digest``, ``image``, ``argv``, ``mounts``,
        ``labels``, …). The tool builder's job type is not imported here, so this
        module stays free of a dependency on the builder.
        """
        from . import execution  # lazy: no import cycle, this module stays standalone

        unmappable = self._job_refusal(job)
        if unmappable:
            return execution.RunResult(
                ok=False,
                status=execution.STATUS_NOT_CERTIFIABLE,
                sandboxed=False,
                detail=unmappable,
            )
        available, probe = self.available()
        if not available:
            return execution.RunResult(
                ok=False,
                status=execution.STATUS_NOT_CERTIFIABLE,
                sandboxed=False,
                detail=f"container execution not available: {probe}",
            )
        config, timeout = self._job_config(job, policy=policy)
        if not config.image_digest:
            return execution.RunResult(
                ok=False,
                status=execution.STATUS_NOT_CERTIFIABLE,
                sandboxed=False,
                detail="refused: the container job carries no pinned image digest",
            )
        problems = config.policy_problems(workdir=config.workdir)
        if problems:
            return execution.RunResult(
                ok=False,
                status=execution.STATUS_NOT_CERTIFIABLE,
                sandboxed=False,
                detail=policy_refusal(problems).detail,
            )
        result = self.run(config, timeout=timeout)
        exit_code = getattr(result, "exit_code", None)
        failure_class = str(getattr(result, "failure_class", "") or "")
        # A negative code is the docker CLI WE had to kill at the timeout (it reports
        # nothing then); 125/126/127 are docker's own failure codes. Either way the
        # container never ran to completion.
        timed_out = failure_class == "timeout" or (
            isinstance(exit_code, int) and exit_code < 0
        )
        never_ran = (
            not isinstance(exit_code, int)
            or exit_code < 0
            or exit_code in _DOCKER_LEVEL_EXIT_CODES
        )
        if not result.ok and never_ran:
            reason = failure_class or ("timeout" if timed_out else "docker-level failure")
            detail = f"the container never ran ({reason}): {result.detail}"
            if timed_out:
                # a killed `docker run` does not stop what it started — sweep the
                # job's own labels so a stuck job cannot leak a container of ours
                detail += self._sweep_timed_out_job(job)
            return execution.RunResult(
                ok=False,
                status=execution.STATUS_NOT_CERTIFIABLE,
                sandboxed=False,
                stdout=self._capped(result.stdout, policy),
                stderr=self._capped(result.stderr, policy),
                detail=detail[:600],
            )
        return execution.RunResult(
            ok=bool(result.ok),
            status=execution.STATUS_OK if result.ok else execution.STATUS_FAILED,
            exit_code=exit_code if isinstance(exit_code, int) else None,
            stdout=self._capped(result.stdout, policy),
            stderr=self._capped(result.stderr, policy),
            detail=str(result.detail or "")[:300],
            sandboxed=True,
        )

    def exec_in_container(
        self, cid_file: Path, *, argv: list[str], timeout: float = 30.0
    ) -> StartResult:
        """`docker exec` in the still-running container — verify an effect."""
        try:
            cid = Path(cid_file).read_text(encoding="utf-8").strip()
        except OSError as exc:
            return StartResult(
                ok=False, detail=f"no cid file: {exc}", failure_class="lifecycle"
            )
        if not cid:
            return StartResult(ok=False, detail="empty cid file", failure_class="lifecycle")
        result = self._invoke(
            ["docker", "exec", cid, *argv], timeout=timeout
        )
        return StartResult(
            ok=result.exit_code == 0,
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            detail="exec ok" if result.exit_code == 0 else result.stderr[:200],
        )

    def stop_and_remove(self, cid: str, *, timeout: float = 30.0) -> StartResult:
        """Stop + remove; anything half-removed is ``cleanup_partial`` — never silent."""
        errors: list[str] = []
        stop = self._invoke(["docker", "stop", cid], timeout=timeout)
        if stop.exit_code != 0 and "no such container" not in (stop.stdout + stop.stderr).lower():
            errors.append(f"stop: {(stop.stderr or stop.stdout)[:120]}")
        remove = self._invoke(["docker", "rm", "-f", cid], timeout=timeout)
        missing = "no such container" not in (remove.stdout + remove.stderr).lower()
        if remove.exit_code != 0 and missing:
            errors.append(f"rm: {(remove.stderr or remove.stdout)[:120]}")
        if errors:
            return StartResult(
                ok=False,
                detail="; ".join(errors),
                failure_class="cleanup_partial",
                payload={"cid": cid, "errors": errors},
            )
        return StartResult(ok=True, detail=f"removed {cid}")

    # -- teardown (label-scoped ONLY — the daemon is shared) ---------------------
    @staticmethod
    def _label_scope_problem(labels: dict[str, str] | None) -> StartResult | None:
        """Refuse a teardown scope that is not ours — never a foreign container.

        Missing labels mean "clean up blindly" and are refused; a label key
        outside the ``godsv`` namespace is refused too, because that scope could
        select containers another stack created (policy N5/R14).
        """
        if not labels:
            return StartResult(
                ok=False,
                detail="no labels — refuse to clean up blindly",
                failure_class="policy_violation",
                policy=POLICY_LABEL_SCOPE,
                payload={"policy": POLICY_LABEL_SCOPE, "problems": ["no-labels"]},
            )
        foreign = sorted(str(key) for key in labels if not _is_owned_label_key(str(key)))
        if foreign:
            return StartResult(
                ok=False,
                detail=(
                    "refusing a teardown that is not scoped to our labels "
                    f"(godsv.*): {', '.join(foreign)} — Skaldir never removes a "
                    "container it did not create"
                )[:300],
                failure_class="policy_violation",
                policy=POLICY_LABEL_SCOPE,
                payload={"policy": POLICY_LABEL_SCOPE, "problems": foreign},
            )
        return None

    def list_by_labels(self, labels: dict[str, str], *, timeout: float = 30.0) -> StartResult:
        """Ids of the containers carrying ``labels`` — the ONLY removal scope.

        One place builds the ``docker ps -a --filter label=…`` line, and it emits
        ONE ``--filter`` per label (:func:`label_filter_args`): a comma-joined
        ``--filter label=a=1,b=2`` matches nothing on the daemon (measured,
        Server 29.6.1), which silently turned every multi-label sweep into a
        no-op. No caller can invent a name-based or blanket listing.
        """
        scope_problem = self._label_scope_problem(labels)
        if scope_problem is not None:
            return scope_problem
        filters = label_filters(labels)
        filter_args = label_filter_args(labels)
        argv = ["docker", "ps", "-a", *filter_args, "--format", "{{.ID}}"]
        listing = self._invoke(argv, timeout=timeout)
        if listing.exit_code != 0:
            return StartResult(
                ok=False,
                detail=(listing.stderr or listing.stdout)[:200] or "docker ps failed",
                failure_class="docker_unreachable",
                payload={"filters": filters, "filter_args": filter_args},
            )
        containers = [cid for cid in listing.stdout.split() if cid]
        return StartResult(
            ok=True,
            detail=f"{len(containers)} container(s) carry {filters}",
            payload={
                "containers": containers,
                "filters": filters,
                # the exact `--filter label=…` values this listing was built from
                "filter_args": [arg for arg in filter_args if arg != "--filter"],
                "argv": list(argv),
                "labels": dict(labels),
            },
        )

    def list_owned(
        self, labels: dict[str, str] | None = None, *, timeout: float = 30.0
    ) -> StartResult:
        """Containers of OURS: the owner label, optionally narrowed by one build."""
        scope = owned_labels()
        scope.update({str(key): str(value) for key, value in (labels or {}).items()})
        return self.list_by_labels(scope, timeout=timeout)

    def cleanup_by_labels(self, labels: dict[str, str], *, timeout: float = 30.0) -> StartResult:
        """Remove leftovers attributed by OUR labels (never a name guess).

        Removal uses exactly the ids the label-filtered listing returned — no
        name, no pattern, no prune. A label set that is empty or not ours is
        refused (``policy_violation`` / ``label-scope-required``), so the foreign
        containers sharing this daemon are untouchable from here.
        """
        listing = self.list_by_labels(labels, timeout=timeout)
        if not listing.ok:
            return listing
        errors: list[str] = []
        removed: list[str] = []
        for cid in listing.payload.get("containers", []):
            result = self._invoke(["docker", "rm", "-f", cid], timeout=timeout)
            if result.exit_code != 0:
                errors.append(f"{cid}: {(result.stderr or result.stdout)[:100]}")
            else:
                removed.append(cid)
        scope = str(listing.payload.get("filters", ""))
        if errors:
            return StartResult(
                ok=False, detail="; ".join(errors), failure_class="cleanup_partial",
                payload={"removed": removed, "errors": errors, "scope": scope},
            )
        return StartResult(
            ok=True,
            detail=f"removed {len(removed)} leftovers",
            payload={"removed": removed, "scope": scope},
        )

    def cleanup_owned(
        self, labels: dict[str, str] | None = None, *, timeout: float = 30.0
    ) -> StartResult:
        """Sweep ONLY Skaldir's own containers (policy L7/N5).

        Scope is ``godsv.owner=skaldir``; an extra ``godsv.*`` label narrows it to
        one build (``godsv.build_id=…``). Never ``docker system prune``, never
        ``docker rm $(docker ps -aq)``, never a name — 7 foreign `penpot-local`
        containers share this daemon and must survive every sweep.
        """
        scope = owned_labels()
        scope.update({str(key): str(value) for key, value in (labels or {}).items()})
        return self.cleanup_by_labels(scope, timeout=timeout)


__all__ = [
    "CmdResult",
    "DigestMismatch",
    "DockerRuntimeManager",
    "DockerUnreachable",
    "OWNER_LABEL_KEY",
    "OWNER_LABEL_VALUE",
    "POLICY_IMAGE_REQUIRED",
    "POLICY_LABEL_SCOPE",
    "POLICY_PIN_REQUIRED",
    "POLICY_REASONS",
    "POLICY_RUN_CONFIG",
    "PolicyViolation",
    "RunConfig",
    "RuntimeFailure",
    "StartResult",
    "classify_pull_error",
    "classify_start_error",
    "label_filter_args",
    "label_filters",
    "owned_labels",
    "policy_refusal",
    "validate_mounts",
    "verify_pinned_digest",
]
