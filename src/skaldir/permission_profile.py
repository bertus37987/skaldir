"""The permission profile: **one** serializable object, two consumers, no drift.

The problem this module exists for
----------------------------------

Our sandbox limits used to be implicit: :func:`skaldir.tools_core.sandbox.bubblewrap_argv`
built a bubblewrap argv, and *nothing* ever told the agent what those limits were. A run
could therefore operate under a policy the model had never been told about — and the model
would keep proposing commands the sandbox was about to refuse, or (worse) believe it was
allowed something it was not.

The fix is not "document the flags twice". It is to make the policy **data**:

.. code-block:: text

    PermissionProfile  ──►  to_dict()/canonical_json()  ──►  sha256  = identity_sha256
           │
           ├──► sandbox_argv()/podman_argv()   the ENFORCED half  (bwrap argv, podman flags)
           └──► prompt_block()                 the TOLD half      (agent-visible text)

Both halves are rendered from the same immutable object and both carry the same
``identity_sha256``, which is taken over the exact canonical JSON that is passed to the
sandbox. So the two cannot drift: a change to the writable set moves the hash in the argv
*and* in the prompt block in the same instant, and
:func:`permission_profile_hash_from_argv` / :func:`permission_profile_hash_from_prompt`
recover the very same string from either side. That is the non-drift property, and it is
asserted by ``tests/test_permission_profile_nondrift.py`` both in-process and against a
real ``bwrap`` run.

What is enforced where — measured, not claimed
----------------------------------------------

Every field carries its own enforcement answer in :meth:`PermissionProfile.enforcement_matrix`,
and the honest categories are:

``bwrap argv``
    filesystem (read/write/protected paths), network, namespaces, capabilities. These are
    real argv elements, testable with ``profile.sandbox_argv(...)``.
``runner``
    wall-clock ``timeout_seconds`` (``subprocess`` timeout) and ``output_max_bytes``
    (head+tail truncation, the same accounting the core tools use). ``max_memory_bytes`` is
    a real ``RLIMIT_AS`` applied through ``resource.setrlimit`` in the child's
    ``preexec_fn`` — the host's bubblewrap 0.11 has **no** ``--rlimit`` flag, so the argv
    cannot carry it and saying otherwise would be a claim we cannot back with a run.
``policy module``
    ``allowed_subprocess_prefixes`` and the whole approval ladder: a command whose head
    token is outside the declared allowlist is refused (by :mod:`skaldir.approval`), not
    silently sandboxed into something else.
``unenforced``
    Reported as such, always. ``max_processes`` is documented but *not* enforced on this
    host (bubblewrap 0.11 offers no cgroup flag and we refuse to pretend); anything else
    that lands here is named with the reason.

A field may never be quietly dropped from the matrix: :func:`enforcement_matrix` iterates
:data:`PROFILE_FIELDS`, and ``tests/test_permission_profile.py`` fails if a field has no
row.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from . import env as _env
from . import security_linux as _linux

SCHEMA_VERSION = "skaldir.permission-profile/1"

#: The one environment variable name a sandboxed child reads its own profile from.
PROFILE_ENV = "SKALDIR_PERMISSION_PROFILE"
PROFILE_SHA_ENV = "SKALDIR_PERMISSION_PROFILE_SHA256"
PROFILE_NAME_ENV = "SKALDIR_PERMISSION_PROFILE_NAME"
NETWORK_ENV = "SKALDIR_PERMISSION_NETWORK"

#: The argv switch our own helpers use to pass the profile to a sandbox front end.
PROFILE_ARGV_FLAG = "--permission-profile"

NETWORK_MODES: tuple[str, ...] = ("off", "on")
READ_POLICIES: tuple[str, ...] = ("full", "roots")
BACKENDS: tuple[str, ...] = ("auto", "bwrap", "podman", "none")
#: A control's mode. ``require`` makes the run fail-closed when the kernel/host cannot apply it;
#: ``auto`` uses it when it works and says so when it does not; ``off`` never applies it (and the
#: matrix then names the reason).
CONTROL_MODES: tuple[str, ...] = ("auto", "require", "off")

#: Read-only platform roots. An explicit allowlist, so "include platform defaults" cannot
#: silently widen to user data (``$HOME``, ``/var``, another checkout).
PLATFORM_DEFAULT_READ_ROOTS: tuple[str, ...] = (
    "/bin",
    "/sbin",
    "/usr",
    "/etc",
    "/lib",
    "/lib64",
    "/nix/store",
    "/run/current-system/sw",
)

#: The environment a child may inherit — the same allowlist the core tools apply, **kept as a
#: literal here on purpose**: importing ``tools_core`` on the profile path would add ~50 ms to
#: every CLI start, and this module is on that path. A test pins the two lists together
#: (``tests/test_permission_profile_env.py``) so they cannot drift — the same "one policy, one
#: agreement test" rule the step-runner registry uses.
DEFAULT_ENV_ALLOW: tuple[str, ...] = (
    "PATH",
    "HOME",
    "LANG",
    "LANGUAGE",
    "LC_ALL",
    "LC_CTYPE",
    "TERM",
    "TZ",
    "TMPDIR",
    "TEMP",
    "TMP",
    "USER",
    "LOGNAME",
    "SHELL",
    "PWD",
    "COLUMNS",
    "LINES",
    "VIRTUAL_ENV",
    "CONDA_PREFIX",
    "SKALDIR_HOME",
    "GODSV_HOME",
    "HERMES_HOME",
    # the profile's own additions: the sandbox contract the tools read, and the workspace marker
    "TOOL_WORKSPACE",
    "PIP_REQUIRE_VIRTUALENV",
)

#: Names that are dropped outright, whatever else says otherwise.
DEFAULT_ENV_DROP: tuple[str, ...] = (
    "PYTHONPATH",
    "PYTHONSTARTUP",
    "NODE_OPTIONS",
    # a secret that is *read from* the environment by our own vault is never handed to a child
    "SKALDIR_VAULT_KEY",
    "GODSV_VAULT_KEY",
    "LD_PRELOAD",
    "LD_LIBRARY_PATH",
)

#: A name matching this is a credential, whatever else is true of it. Values are *never*
#: reported, only the names (the rule the core tools' own report already keeps).
DEFAULT_ENV_DENY_PATTERN = (
    r"(?i)(key|token|secret|passwd|password|credential|auth|cookie|session|bearer|private|"
    r"_pat$|gh_|_ssh)"
)

#: Names the runner always sets for the child, whatever the environment says (the sandbox
#: contract the tools read).
FORCED_ENV_NAMES: tuple[str, ...] = ("TOOL_WORKSPACE",)

#: Always read-only, even when their parent root is writable. These are the paths whose
#: corruption is not recoverable by a re-run: version control metadata, credentials and the
#: runtime's own state.
DEFAULT_PROTECTED_PATHS: tuple[str, ...] = (
    ".git",
    ".hg",
    ".ssh",
    ".gnupg",
    ".aws",
    ".config/gcloud",
    ".godsv",
    ".skaldir",
)

#: File-name globs that stay read-only inside a writable root (the core tools' own deny
#: list, so the sandbox and :mod:`skaldir.tools_core.sandbox` agree instead of differing).
DEFAULT_WRITE_DENY_GLOBS: tuple[str, ...] = (
    ".env",
    ".env.*",
    "*.pem",
    "*.p12",
    "*.pfx",
    "*.ppk",
    "id_rsa*",
    "id_ed25519*",
    "id_ecdsa*",
    "credentials",
    "credentials.*",
)

#: Every field of a profile. The enforcement matrix iterates exactly this tuple, and a test
#: fails when a field has no row — so a new field cannot ship undocumented.
PROFILE_FIELDS: tuple[str, ...] = (
    "schema_version",
    "name",
    "workspace",
    "read_policy",
    "readable_roots",
    "writable_paths",
    "protected_paths",
    "write_deny_globs",
    "network",
    "network_egress_ports",
    "allowed_subprocess_prefixes",
    "env_policy",
    "linux_security",
    "caps",
    "sandbox_backend",
)

#: The name and title of the section the profile occupies in the system prompt.
PROMPT_SECTION_NAME = "permissions"
PROMPT_SECTION_TITLE = "Permissions"

#: The one-line hook the composer needs (``prompt_builder.SECTION_PROVIDERS``). Kept here as
#: the *definition* of the section so the text and its budget cannot be invented upstream.
#:
#: **Raised from 1200 to 1900 in the security wave (MVP/48).** The block now has to carry five
#: more classes of limit — the environment policy, the explicit egress policy, the syscall
#: classes seccomp denies, ``PR_SET_NO_NEW_PRIVS`` and the ``max_processes`` mechanism — because
#: "the agent must be told every limit it runs under" is the whole point of the section. A
#: budget below the text would mean the agent is told a *prefix* of its limits, which is the
#: failure the section exists to prevent; ``tests/test_permission_profile_prompt.py`` asserts
#: the block fits under this number, so the two move together or not at all.
PROMPT_SECTION_BUDGET_CHARS = 1900

#: ``profile sha256 <hex>`` — the one shape the prompt block and the argv share.
_HASH_IN_BLOCK = re.compile(r"profile sha256 ([0-9a-f]{64})")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


# ------------------------------------------------------------------------ env


@dataclass(frozen=True)
class EnvPolicy:
    """The environment policy — **in the profile**, not beside it.

    `MVP/43` recorded this as a gap in one line: *"the environment of a sandboxed child is the
    host's, scrubbed. `tools_core.child_environment` applies the allowlist + credential scrub;
    the profile itself does not own the env policy."* That split is what this dataclass removes:
    the same object that decides the mounts and the network now decides what the child may read
    from the environment, so the block the agent reads can state it and the hash covers it.

    Rules, in order:

    1. ``drop`` names never pass, whatever else says otherwise;
    2. a name matching ``deny_patterns`` is a credential: it is dropped and **named** (the value
       is never read into a report, let alone a prompt);
    3. a name in ``allow`` passes (or, when ``inherit`` is true and ``allow`` is empty,
       everything that survived 1–2 passes);
    4. ``set`` values are applied last and win over the parent's environment;
    5. :data:`FORCED_ENV_NAMES` are always set, because the tools read them as the sandbox
       contract.

    ``inherit=False`` is the strict mode: nothing passes except ``allow`` and ``set``.
    """

    inherit: bool = True
    allow: tuple[str, ...] = DEFAULT_ENV_ALLOW
    drop: tuple[str, ...] = DEFAULT_ENV_DROP
    deny_patterns: tuple[str, ...] = (DEFAULT_ENV_DENY_PATTERN,)
    set: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        """Refuse an inline *secret* — the profile travels through the argv.

        The whole profile is passed to the child as canonical JSON on the command line
        (``--setenv`` for bubblewrap, ``--permission-profile`` for the launcher), so any value
        declared in ``set`` is readable by every process on the host. A secret therefore has to be
        **named**, not carried: allow the name and set it in the parent environment, and let
        :meth:`apply` pass the value through the process environment instead. This is Codex's own
        rule for server configuration — "a secret in config must be a variable reference, never an
        inline value" — applied to the place where it costs us something.
        """
        patterns = [re.compile(item) for item in self.deny_patterns if item]
        leaked = sorted(
            name
            for name, _value in self.set
            if any(pattern.search(name) for pattern in patterns)
        )
        if leaked:
            raise ValueError(
                f"env_policy.set declares a value for {', '.join(leaked)}: the profile is passed "
                f"to the child as JSON on the command line, so an inline secret is readable by "
                f"every process on this host. Allow the name instead and set the value in the "
                f"parent environment"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "inherit": bool(self.inherit),
            "allow": list(self.allow),
            "drop": list(self.drop),
            "deny_patterns": list(self.deny_patterns),
            "set": [[name, value] for name, value in self.set],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> EnvPolicy:
        raw = dict(data or {})
        return cls(
            inherit=bool(raw.get("inherit", True)),
            allow=tuple(str(item) for item in raw.get("allow", DEFAULT_ENV_ALLOW)),
            drop=tuple(str(item) for item in raw.get("drop", DEFAULT_ENV_DROP)),
            deny_patterns=tuple(
                str(item) for item in raw.get("deny_patterns", (DEFAULT_ENV_DENY_PATTERN,))
            ),
            set=tuple((str(pair[0]), str(pair[1])) for pair in raw.get("set", ())),
        )

    def set_map(self) -> dict[str, str]:
        return {name: value for name, value in self.set}

    def summary(self) -> str:
        mode = "allowlist" if self.allow else "inherit-all-but-credentials"
        if not self.inherit and self.allow:
            mode = "strict allowlist (nothing else passes)"
        if not self.inherit and not self.allow:
            mode = "nothing passes"
        return f"{mode}: {len(self.allow)} name(s) kept, {len(self.drop)} dropped by policy"

    def apply(
        self, parent: Mapping[str, str] | None = None
    ) -> tuple[dict[str, str], dict[str, Any]]:
        """``(env, report)`` — the environment for a child, plus the auditable account of it.

        The report carries **names only**: a dropped value is never read, so it cannot leak into
        a log, a payload or a prompt.
        """
        source = dict(os.environ if parent is None else parent)
        kept: dict[str, str] = {}
        dropped_credentials: list[str] = []
        dropped_unlisted: list[str] = []
        dropped_by_policy: list[str] = []
        patterns = [re.compile(item) for item in self.deny_patterns if item]
        allow = set(self.allow)
        for name in sorted(source):
            if name in self.drop:
                dropped_by_policy.append(name)
                continue
            if any(pattern.search(name) for pattern in patterns):
                dropped_credentials.append(name)
                continue
            if allow and name not in allow:
                dropped_unlisted.append(name)
                continue
            kept[name] = source[name]
        forced: dict[str, str] = {}
        set_refused: list[str] = []
        for name, value in sorted(self.set_map().items()):
            # `drop` and the credential patterns beat `set`: a value declared in the profile must
            # not be able to put a name back that the policy removes. The contradiction is
            # *reported* rather than resolved silently — see `set_refused` below.
            if name in self.drop or any(pattern.search(name) for pattern in patterns):
                set_refused.append(name)
                continue
            forced[name] = str(value)
        for name in FORCED_ENV_NAMES:
            if name not in forced and name in source and name not in self.drop:
                forced[name] = source[name]
        kept.update(forced)
        report = {
            "policy": "allowlist + credential scrub (declared in the profile)",
            "inherit": bool(self.inherit),
            "allow": sorted(allow),
            "kept": sorted(name for name in kept if name not in forced),
            "dropped_credentials": sorted(dropped_credentials),
            "dropped_unlisted": sorted(dropped_unlisted)[:40],
            "dropped_by_policy": sorted(dropped_by_policy),
            "set": sorted(forced),
            "set_refused": sorted(set_refused),
            "parent_names": len(source),
        }
        return kept, report


def default_env_policy() -> EnvPolicy:
    """The built-in environment policy: the tools' allowlist, the credential scrub, no secrets."""
    return EnvPolicy()


# ------------------------------------------------------------------ linux security


@dataclass(frozen=True)
class LinuxSecurityPolicy:
    """Which kernel controls the child applies, and how strictly.

    Each field takes :data:`CONTROL_MODES`:

    ``auto``
        use it when the host supports it, and report the answer either way;
    ``require``
        **the run is refused** when the host cannot apply it — never a silent downgrade;
    ``off``
        never apply it. The enforcement matrix then names the reason.

    ``no_new_privs`` is not optional: :meth:`__post_init__` refuses a profile that turns it off,
    because a seccomp filter and a Landlock ruleset both *require* it — a policy that could
    disable it would be a policy that can be bypassed by the process it constrains.
    """

    landlock: str = "auto"
    seccomp: str = "auto"
    pids_cgroup: str = "auto"
    no_new_privs: bool = True

    def __post_init__(self) -> None:
        for field_name in ("landlock", "seccomp", "pids_cgroup"):
            value = getattr(self, field_name)
            if value not in CONTROL_MODES:
                raise ValueError(
                    f"linux_security.{field_name} must be one of {', '.join(CONTROL_MODES)} "
                    f"(got {value!r})"
                )
        if not self.no_new_privs:
            raise ValueError(
                "linux_security.no_new_privs cannot be turned off: PR_SET_NO_NEW_PRIVS is what "
                "makes the seccomp filter and the Landlock ruleset unbypassable, so a profile "
                "that could disable it could be bypassed by the process it constrains"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "landlock": self.landlock,
            "seccomp": self.seccomp,
            "pids_cgroup": self.pids_cgroup,
            "no_new_privs": bool(self.no_new_privs),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> LinuxSecurityPolicy:
        raw = dict(data or {})
        return cls(
            landlock=str(raw.get("landlock", "auto")),
            seccomp=str(raw.get("seccomp", "auto")),
            pids_cgroup=str(raw.get("pids_cgroup", "auto")),
            no_new_privs=bool(raw.get("no_new_privs", True)),
        )


# --------------------------------------------------------------------------- caps


@dataclass(frozen=True)
class ResourceCaps:
    """The resource caps of a profile, in the units the runners actually use."""

    timeout_seconds: float = 120.0
    output_max_bytes: int = 65536
    spill_max_bytes: int = 1048576
    #: ``RLIMIT_AS`` for the child. ``0`` means "no cap" — and then the matrix says so.
    max_memory_bytes: int = 0
    #: Documented, not enforced on this host (see the module docstring).
    max_processes: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "timeout_seconds": float(self.timeout_seconds),
            "output_max_bytes": int(self.output_max_bytes),
            "spill_max_bytes": int(self.spill_max_bytes),
            "max_memory_bytes": int(self.max_memory_bytes),
            "max_processes": int(self.max_processes),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> ResourceCaps:
        raw = dict(data or {})
        base = cls()
        return cls(
            timeout_seconds=float(raw.get("timeout_seconds", base.timeout_seconds)),
            output_max_bytes=int(raw.get("output_max_bytes", base.output_max_bytes)),
            spill_max_bytes=int(raw.get("spill_max_bytes", base.spill_max_bytes)),
            max_memory_bytes=int(raw.get("max_memory_bytes", base.max_memory_bytes)),
            max_processes=int(raw.get("max_processes", base.max_processes)),
        )

    def summary(self) -> str:
        parts = [f"timeout {self.timeout_seconds:g}s", f"output {self.output_max_bytes} bytes"]
        if self.max_memory_bytes:
            parts.append(f"memory {self.max_memory_bytes} bytes")
        if self.max_processes:
            parts.append(f"processes {self.max_processes} (not enforced)")
        return ", ".join(parts)


# ------------------------------------------------------------------------ profile


@dataclass(frozen=True)
class PermissionProfile:
    """One serializable policy: declared writable set, read roots, network, prefixes, caps.

    Immutable on purpose. The identity hash is taken over the canonical JSON of the fields
    below, so ``dataclasses.replace`` (or :meth:`with_changes`) is the only way to change a
    profile — and every change necessarily moves the hash that both consumers carry.
    """

    schema_version: str = SCHEMA_VERSION
    name: str = "default"
    workspace: str = ""
    read_policy: str = "full"
    readable_roots: tuple[str, ...] = ()
    writable_paths: tuple[str, ...] = ()
    protected_paths: tuple[str, ...] = DEFAULT_PROTECTED_PATHS
    write_deny_globs: tuple[str, ...] = DEFAULT_WRITE_DENY_GLOBS
    network: str = "off"
    #: The declared **egress** policy. Empty means "no port restriction beyond ``network``"; a
    #: non-empty tuple is enforced by Landlock's net-port rules (ABI >= 4) as "TCP connect to
    #: these ports only". Ports are the finest grain the kernel offers — a host allowlist cannot
    #: be expressed at this layer, and the matrix says so instead of implying it.
    network_egress_ports: tuple[int, ...] = ()
    allowed_subprocess_prefixes: tuple[tuple[str, ...], ...] = ()
    env_policy: EnvPolicy = field(default_factory=EnvPolicy)
    linux_security: LinuxSecurityPolicy = field(default_factory=LinuxSecurityPolicy)
    caps: ResourceCaps = field(default_factory=ResourceCaps)
    sandbox_backend: str = "auto"

    # ------------------------------------------------------------------ shape

    def __post_init__(self) -> None:
        if self.read_policy not in READ_POLICIES:
            raise ValueError(
                f"unknown read_policy {self.read_policy!r} (known: {', '.join(READ_POLICIES)})"
            )
        if self.network not in NETWORK_MODES:
            raise ValueError(f"network must be one of {', '.join(NETWORK_MODES)}")
        if self.sandbox_backend not in BACKENDS:
            raise ValueError(
                f"unknown sandbox_backend {self.sandbox_backend!r} (known: {', '.join(BACKENDS)})"
            )
        for port in self.network_egress_ports:
            if not 0 < int(port) <= 65535:
                raise ValueError(f"network_egress_ports entry {port!r} is not a TCP port")
        if self.network == "off" and self.network_egress_ports:
            raise ValueError(
                "network_egress_ports is declared while network is 'off': an egress allowlist on "
                "a network that is switched off is a contradiction, and a policy that contradicts "
                "itself cannot be enforced honestly"
            )

    # ------------------------------------------------------- canonical identity

    def canonical_payload(self) -> dict[str, Any]:
        """The policy as data, without the hash — canonical, sorted, machine-comparable."""
        return {
            "schema_version": str(self.schema_version),
            "name": str(self.name),
            "workspace": str(self.workspace),
            "read_policy": str(self.read_policy),
            "readable_roots": list(self.readable_roots),
            "writable_paths": list(self.writable_paths),
            "protected_paths": list(self.protected_paths),
            "write_deny_globs": list(self.write_deny_globs),
            "network": str(self.network),
            "network_egress_ports": [int(port) for port in self.network_egress_ports],
            "allowed_subprocess_prefixes": [
                list(item) for item in self.allowed_subprocess_prefixes
            ],
            "env_policy": self.env_policy.to_dict(),
            "linux_security": self.linux_security.to_dict(),
            "caps": self.caps.to_dict(),
            "sandbox_backend": str(self.sandbox_backend),
        }

    def canonical_json(self) -> str:
        """The exact bytes the sandbox is handed and the hash is taken over."""
        return json.dumps(self.canonical_payload(), sort_keys=True, separators=(",", ":"))

    @property
    def identity_sha256(self) -> str:
        """``sha256`` of :meth:`canonical_json` — the profile's stable identity."""
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    @property
    def identity(self) -> str:
        """``name@sha256:<hex>`` — a compact label for evidence lines."""
        return f"{self.name}@sha256:{self.identity_sha256}"

    def fingerprint_line(self) -> str:
        """The one line both consumers carry verbatim."""
        return f"profile sha256 {self.identity_sha256}"

    def to_dict(self) -> dict[str, Any]:
        payload = self.canonical_payload()
        payload["identity_sha256"] = self.identity_sha256
        return payload

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n"

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> PermissionProfile:
        """Rebuild a profile from stored data. Unknown keys are ignored, never guessed at."""
        raw = dict(data or {})
        if raw.get("identity_sha256"):
            raw.pop("identity_sha256")
        prefixes = tuple(
            tuple(str(part) for part in item)
            for item in raw.get("allowed_subprocess_prefixes", ())
        )
        return cls(
            schema_version=str(raw.get("schema_version", SCHEMA_VERSION)),
            name=str(raw.get("name", "default")),
            workspace=str(raw.get("workspace", "")),
            read_policy=str(raw.get("read_policy", "full")),
            readable_roots=tuple(str(item) for item in raw.get("readable_roots", ())),
            writable_paths=tuple(str(item) for item in raw.get("writable_paths", ())),
            protected_paths=tuple(
                str(item) for item in raw.get("protected_paths", DEFAULT_PROTECTED_PATHS)
            ),
            write_deny_globs=tuple(
                str(item) for item in raw.get("write_deny_globs", DEFAULT_WRITE_DENY_GLOBS)
            ),
            network=str(raw.get("network", "off")),
            network_egress_ports=tuple(
                int(port) for port in raw.get("network_egress_ports", ())
            ),
            allowed_subprocess_prefixes=prefixes,
            env_policy=EnvPolicy.from_dict(raw.get("env_policy")),
            linux_security=LinuxSecurityPolicy.from_dict(raw.get("linux_security")),
            caps=ResourceCaps.from_dict(raw.get("caps")),
            sandbox_backend=str(raw.get("sandbox_backend", "auto")),
        )

    def with_changes(self, **changes: Any) -> PermissionProfile:
        """A new profile with ``changes`` applied (the only way to change one)."""
        if "caps" in changes and isinstance(changes["caps"], Mapping):
            changes["caps"] = ResourceCaps.from_dict(changes["caps"])
        if "env_policy" in changes and isinstance(changes["env_policy"], Mapping):
            changes["env_policy"] = EnvPolicy.from_dict(changes["env_policy"])
        if "linux_security" in changes and isinstance(changes["linux_security"], Mapping):
            changes["linux_security"] = LinuxSecurityPolicy.from_dict(changes["linux_security"])
        return replace(self, **changes)

    def verify_identity(self, expected: str) -> bool:
        """True when ``expected`` is this profile's hash — the drift check, one call."""
        return str(expected).strip().lower() == self.identity_sha256

    def diff(self, other: PermissionProfile) -> list[str]:
        """The field names that differ — what "the profile changed" means, named."""
        mine, theirs = self.canonical_payload(), other.canonical_payload()
        return sorted(key for key in mine if mine[key] != theirs.get(key))

    # ------------------------------------------------------------- path helpers

    def workspace_path(self) -> Path:
        return Path(self.workspace).expanduser() if self.workspace else Path.cwd()

    def _absolute(self, value: str) -> str:
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = self.workspace_path() / path
        return str(path)

    def writable_absolute(self) -> tuple[str, ...]:
        return tuple(self._absolute(item) for item in self.writable_paths)

    def readable_absolute(self) -> tuple[str, ...]:
        if self.read_policy == "full":
            return ("/",)
        roots = list(PLATFORM_DEFAULT_READ_ROOTS) + list(self.readable_roots)
        seen: dict[str, None] = {}
        for item in roots:
            seen.setdefault(self._absolute(item), None)
        return tuple(seen)

    def protected_absolute(self) -> tuple[str, ...]:
        """Protected subpaths, resolved; whatever exists on disk wins, the rest is dropped."""
        out: dict[str, None] = {}
        for item in self.protected_paths:
            candidate = Path(self._absolute(item))
            if candidate.exists():
                out.setdefault(str(candidate), None)
        return tuple(out)

    # ------------------------------------------------------------ enforcement

    def control_mode(self, declared: str) -> str:
        """Resolve a control's declared mode against what this host can actually do."""
        if str(declared) == "off":
            return "off"
        if declared in ("auto", "require"):
            return "enforce"
        raise ValueError(f"unknown control mode {declared!r} (known: {', '.join(CONTROL_MODES)})")

    def landlock_mode(self) -> str:
        """``enforce`` / ``off`` / ``unavailable`` — measured against the running kernel."""
        declared = self.linux_security.landlock
        if declared == "off":
            return "off"
        return "enforce" if _linux.landlock_abi() >= _linux.LANDLOCK_ABI_MIN else "unavailable"

    def seccomp_mode(self) -> str:
        declared = self.linux_security.seccomp
        if declared == "off":
            return "off"
        return "enforce" if _linux.audit_arch() else "unavailable"

    def pids_cgroup_mode(self) -> tuple[str, str, str]:
        """``(mode, parent_path, reason)`` for the process cap, measured on this host."""
        declared = self.linux_security.pids_cgroup
        if not self.caps.max_processes:
            return "off", "", "no cap declared (caps.max_processes is 0)"
        if declared == "off":
            return "off", "", "the profile declares linux_security.pids_cgroup = 'off'"
        ok, path, reason = _linux.pids_cgroup_support()
        return ("enforce" if ok else "unavailable"), path, reason

    def mount_binary(self) -> str:
        """The **bubblewrap** binary `sandbox_argv()` will use, or ``""`` when there is none.

        Deliberately not :meth:`backend_binary`: ``sandbox_backend='auto'`` falls back to
        ``podman``, and ``podman`` cannot be handed bubblewrap's flags. Before this split, an
        ``auto`` profile on a host without bubblewrap resolved to ``podman`` and then emitted
        ``podman --new-session --die-with-parent …`` — an argv that cannot run. The mount view in
        *this* argv is bubblewrap's or it is absent; a container path is
        :meth:`podman_argv`, which is a different invocation on purpose.
        """
        if self.sandbox_backend == "none":
            return ""
        found = shutil.which("bwrap")
        return found or ""

    def mechanism(self) -> tuple[str, str]:
        """``(name, reason)`` — which enforcement stack this profile resolves to *here*."""
        name, reason = _linux.mechanism_for(
            landlock_mode=self.landlock_mode(),
            bwrap=self.mount_binary(),
            abi=_linux.landlock_abi(),
        )
        resolved = self.backend_binary()
        if resolved and not Path(resolved).name.startswith("bwrap"):
            reason += (
                f"; note: sandbox_backend {self.sandbox_backend!r} resolves to "
                f"{Path(resolved).name}, whose own invocation is podman_argv() — the kernel "
                "controls below apply inside either front end"
            )
        return name, reason

    def unenforceable_required(self) -> list[dict[str, str]]:
        """Controls the profile *required* that this host cannot apply — the fail-closed list.

        ``require`` is not a wish: a profile that says it needs Landlock and runs on a kernel
        without it must not run. This is where that is decided, once, for the runner and for the
        approval ladder.
        """
        out: list[dict[str, str]] = []
        if self.linux_security.landlock == "require" and self.landlock_mode() != "enforce":
            out.append(
                {
                    "control": "landlock",
                    "reason": _linux.landlock_unavailable_reason()
                    or "Landlock is not available on this kernel",
                    "fix": "use a kernel with Landlock (5.13+), or set "
                    "linux_security.landlock = auto",
                }
            )
        if self.linux_security.seccomp == "require" and self.seccomp_mode() != "enforce":
            out.append(
                {
                    "control": "seccomp",
                    "reason": "no audit_arch constant / syscall table for this architecture",
                    "fix": "set linux_security.seccomp = auto",
                }
            )
        if self.linux_security.pids_cgroup == "require" and self.linux_security.pids_cgroup:
            mode, _path, reason = self.pids_cgroup_mode()
            if mode != "enforce":
                out.append(
                    {
                        "control": "pids_cgroup",
                        "reason": reason,
                        "fix": "declare caps.max_processes = 0, or set linux_security.pids_cgroup "
                        "= auto, or run inside a session with a delegated pids sub-tree",
                    }
                )
        return out

    def launcher_read_paths(self) -> tuple[str, ...]:
        """The two files the enforcement launcher itself needs readable, resolved.

        Under ``read_policy='roots'`` the sandbox starts from an empty root and binds only the
        platform defaults and the declared roots — and the launcher's interpreter (usually inside
        this checkout's ``.venv``) and the launcher script itself are then *outside* the readable
        set. Without this the policy cannot be applied at all: the run dies with ENOENT before a
        single control is installed.

        So they are bound read-only **and named**: they join the plan's ``read_only_paths``, appear
        in the argv, and are reported in the run's ``warnings``-adjacent detail, so a reader can
        see that exactly two files were added to the readable set and why. Under
        ``read_policy='full'`` the whole filesystem is readable already and this returns ``()``.
        """
        if self.read_policy == "full":
            return ()
        seen: dict[str, None] = {}
        for item in (launcher_python(), launcher_path()):
            seen.setdefault(str(Path(item).resolve()), None)
        return tuple(seen)

    def security_plan(self, *, pids_max: int | None = None) -> _linux.SecurityPlan:
        """**The plan.** The profile as plain data for the child that will apply it.

        One derivation site: this method. The launcher reads the plan and makes kernel calls; it
        never re-derives a policy from the profile, so the plan the child applies is the plan the
        argv and the prompt block were both rendered from.
        """
        abi = _linux.landlock_abi()
        extra_read = self.launcher_read_paths()
        if self.read_policy == "full":
            read_only: tuple[str, ...] = ("/",)
        else:
            seen: dict[str, None] = {item: None for item in self.readable_absolute()}
            for item in extra_read:
                seen.setdefault(item, None)
            read_only = tuple(seen)
        limit = self.caps.max_processes if pids_max is None else int(pids_max)
        notes: list[str] = []
        if extra_read:
            notes.append(
                "read-only exceptions for the enforcement launcher itself (they lie outside the "
                "declared read roots and the policy cannot be applied without them): "
                + ", ".join(extra_read)
            )
        has_bwrap = bool(self.mount_binary())
        sandbox_tmp = "/tmp" if has_bwrap else ""
        writable_set = self.writable_absolute()
        if sandbox_tmp and self.landlock_mode() == "enforce" and "/tmp" not in writable_set:
            notes.append(
                "bubblewrap mounts a private tmpfs at /tmp and the Landlock ruleset grants it "
                "read-write: it is the sandbox's own scratch space, not the host's /tmp"
            )
        if self.protected_paths and self.landlock_mode() == "enforce":
            notes.append(
                "Landlock has no deny rule: a protected subpath inside a writable root cannot be "
                "carved out by a Landlock ruleset — bubblewrap's mount order does that. "
                "Mechanism 'bwrap+landlock' carries both; 'landlock' alone cannot."
            )
        return _linux.SecurityPlan(
            profile_name=self.name,
            profile_sha256=self.identity_sha256,
            mechanism=self.mechanism()[0],
            landlock=self.landlock_mode(),
            landlock_abi=abi,
            read_only_paths=tuple(read_only),
            read_write_paths=self.writable_absolute(),
            protected_paths=self.protected_absolute(),
            sandbox_tmp=sandbox_tmp,
            network=self.network,
            egress_ports=tuple(self.network_egress_ports),
            seccomp=self.seccomp_mode(),
            seccomp_classes=("privilege", "network"),
            no_new_privs=bool(self.linux_security.no_new_privs),
            pids_max=int(limit),
            notes=tuple(notes),
        )

    def child_environment(
        self, parent: Mapping[str, str] | None = None
    ) -> tuple[dict[str, str], dict[str, Any]]:
        """``(env, report)`` for a child of this profile — the policy is *this* object's.

        `MVP/43`'s fourth gap, closed: the allowlist, the drop list, the credential patterns and
        the forced values are profile fields, so they move the identity hash, they appear in
        ``permission explain``, and the prompt block can tell the agent what it will be given.
        """
        env, report = self.env_policy.apply(parent)
        report["workspace"] = str(self.workspace_path())
        env["TOOL_WORKSPACE"] = report["workspace"]
        report["set"] = sorted(set(report["set"]) | {"TOOL_WORKSPACE"})
        return env, report

    def backend_binary(self) -> str:
        """The sandbox binary this profile resolves to, or ``""`` when there is none."""
        order = [self.sandbox_backend] if self.sandbox_backend != "auto" else ["bwrap", "podman"]
        for name in order:
            if name == "none":
                return ""
            found = shutil.which(name)
            if found:
                return found
        return ""

    def sandbox_argv(
        self,
        command: str,
        *,
        cwd: str | Path | None = None,
        shell: str = "/bin/sh",
        plan: _linux.SecurityPlan | None = None,
    ) -> list[str]:
        """**The enforced half.** The exact bubblewrap argv for ``command`` under this profile.

        The profile's canonical JSON and its ``sha256`` are passed as ``--setenv`` elements, so
        the sandboxed child (and any auditor reading the argv) holds the very same policy object
        the prompt block was rendered from.

        The command is not run directly: bubblewrap builds the filesystem view, then **our own
        launcher** (``security_linux.py exec``) applies ``PR_SET_NO_NEW_PRIVS``, the Landlock
        ruleset and the seccomp filter, and only then execs the command. That is Codex's
        three-stage order, and it is why ``--apply-seccomp-then-exec`` exists upstream: a setuid
        bubblewrap has to finish its mounts before the process tightens itself. The launcher gets
        the plan **and** the profile JSON with the hash, and refuses if the two disagree — so a
        wrapper cannot swap the policy between the argv and the process.
        """
        current = plan if plan is not None else self.security_plan()
        binary = self.mount_binary() or "bwrap"
        argv: list[str] = [binary, "--new-session", "--die-with-parent"]
        argv += [
            "--unshare-user",
            "--unshare-pid",
            "--unshare-ipc",
            "--unshare-uts",
            "--unshare-cgroup-try",
        ]
        if self.network == "off":
            argv += ["--unshare-net"]
        if self.read_policy == "full":
            argv += ["--ro-bind", "/", "/"]
        else:
            argv += ["--tmpfs", "/"]
            roots = self.readable_absolute()
            for root in roots:
                argv += ["--ro-bind-try", root, root]
            for extra in self.launcher_read_paths():
                if not any(is_inside(extra, root) for root in roots):
                    argv += ["--ro-bind-try", extra, extra]
        argv += ["--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp"]
        for target in self.writable_absolute():
            argv += ["--bind", target, target]
        # protected subpaths win over the writable bind above, by mount order
        for target in self.protected_absolute():
            argv += ["--ro-bind", target, target]
        argv += ["--chdir", str(cwd or self.workspace or "/"), "--cap-drop", "ALL"]
        # the policy travels with the process, hash included
        argv += ["--setenv", PROFILE_ENV, self.canonical_json()]
        argv += ["--setenv", PROFILE_SHA_ENV, self.identity_sha256]
        argv += ["--setenv", PROFILE_NAME_ENV, self.name]
        argv += ["--setenv", NETWORK_ENV, self.network]
        argv += launcher_argv(
            command,
            canonical_json=self.canonical_json(),
            identity_sha256=self.identity_sha256,
            plan=current,
            cwd=str(cwd or self.workspace or "/"),
            shell=shell,
        )
        return argv

    def podman_argv(
        self,
        command: str,
        *,
        image: str = "",
        cwd: str | Path | None = None,
        name: str = "",
    ) -> list[str]:
        """**The enforced half for the container case** — podman flags from the same object.

        The mount set, the network mode and the caps mirror :meth:`sandbox_argv` exactly, so
        the container path and the bwrap path cannot disagree about what the profile allows.
        """
        binary = shutil.which("podman") or "podman"
        argv: list[str] = [binary, "run", "--rm"]
        if name:
            argv += ["--name", str(name)]
        argv += ["--read-only", "--tmpfs", "/tmp", "--cap-drop", "ALL"]
        argv += ["--security-opt", "no-new-privileges"]
        if self.network == "off":
            argv += ["--network", "none"]
        for target in self.writable_absolute():
            argv += ["-v", f"{target}:{target}:rw"]
        for target in self.protected_absolute():
            argv += ["-v", f"{target}:{target}:ro"]
        argv += ["-w", str(cwd or self.workspace or "/")]
        argv += ["--env", f"{PROFILE_ENV}={self.canonical_json()}"]
        argv += ["--env", f"{PROFILE_SHA_ENV}={self.identity_sha256}"]
        argv += ["--env", f"{PROFILE_NAME_ENV}={self.name}"]
        argv += ["--env", f"{NETWORK_ENV}={self.network}"]
        # the environment policy goes through `--env NAME` (name only!): podman then copies the
        # value from the client's environment, which the runner has already filtered, so no
        # secret value ever appears in an argv a `ps` can read.
        env, _report = self.child_environment()
        for name in sorted(env):
            argv += ["--env", name]
        argv += [str(image or "localhost/skaldir-sandbox:latest")]
        # the command is the container's own shell line, as one argv element: nothing
        # is spliced into that text (the same split as sandbox.bubblewrap_argv)
        argv += ["/bin/sh", "-c", command]
        return argv

    # ------------------------------------------------------------- told half

    def prompt_block(self) -> str:
        """**The told half.** The agent-visible block, rendered from the same object.

        Every enforced line has a sentence here, and the ``profile sha256 …`` line is
        byte-identical to the value in :meth:`sandbox_argv`. A run can therefore never
        operate under limits it was not told about — and a reader can verify that claim by
        comparing the two hashes, which is exactly what the tests do.

        The block covers **all five** things `MVP/43` said the agent was not told: the filesystem
        policy, the network *egress* policy, the environment policy, the syscall classes seccomp
        denies and the ``max_processes`` mechanism, plus how a refusal is reported.
        """
        writable = self.writable_absolute()
        mechanism, mechanism_reason = self.mechanism()
        lines: list[str] = [
            f"{self.fingerprint_line()} — name {self.name}, schema {self.schema_version}",
        ]
        if self.read_policy == "full":
            lines.append("filesystem: the whole filesystem is readable, read-only")
        else:
            lines.append(
                "filesystem: only these roots are readable (read-only): "
                + ", ".join(self.readable_absolute())
            )
        lines.append(
            "writable: " + (", ".join(writable) if writable else "(nothing — no path is writable)")
        )
        protected = self.protected_absolute()
        if protected:
            lines.append("always read-only, even inside a writable root: " + ", ".join(protected))
        if self.write_deny_globs:
            lines.append("never written: " + ", ".join(self.write_deny_globs))
        if self.network == "off":
            lines.append(
                "network: off — no egress at all (namespace unshared, seccomp denies "
                "connect/sendto, Landlock denies every TCP connect and bind)"
            )
        elif self.network_egress_ports:
            lines.append(
                "network: on, egress limited to TCP connect to port(s) "
                + ", ".join(str(port) for port in self.network_egress_ports)
                + " — every other TCP connect is denied by the kernel (Landlock)"
            )
        else:
            lines.append("network: on, no port restriction declared")
        if self.mechanism()[0] == _linux.MECHANISM_BWRAP_LANDLOCK:
            lines.append(
                "scratch: /tmp is a private tmpfs inside the sandbox and is writable; it is not "
                "the host's /tmp and nothing written there survives the run"
            )
        elif self.mechanism()[0] == _linux.MECHANISM_LANDLOCK:
            lines.append(
                "scratch: there is no private /tmp in this mechanism — /tmp is the host's and is "
                "read-only here; write scratch files inside a declared writable path"
            )
        env = self.env_policy
        env_line = (
            f"environment: {env.summary()}; dropped by policy: "
            + (", ".join(env.drop) if env.drop else "(none)")
            + "; a credential-shaped name is dropped and named, its value is never passed"
        )
        lines.append(env_line)
        controls: list[str] = [
            "no-new-privileges always",
        ]
        landlock_mode = self.landlock_mode()
        if landlock_mode == "enforce":
            controls.append(
                f"Landlock ABI {_linux.landlock_abi()} enforces the paths above by the kernel"
            )
        else:
            controls.append(f"Landlock {landlock_mode} here, so the mounts carry the paths")
        seccomp_mode = self.seccomp_mode()
        if seccomp_mode == "enforce":
            denied = "ptrace/process_vm/io_uring" + (
                ", all network egress" if self.network == "off" else ""
            )
            controls.append(f"seccomp denies {denied} (AF_UNIX sockets stay allowed)")
        else:
            controls.append(f"seccomp {seccomp_mode} here")
        if self.caps.max_processes:
            mode, _parent, _reason = self.pids_cgroup_mode()
            controls.append(
                f"at most {self.caps.max_processes} processes"
                if mode == "enforce"
                else f"max_processes {self.caps.max_processes} declared but not enforced here"
            )
        lines.append("enforced: " + "; ".join(controls))
        lines.append(
            f"mechanism: {mechanism} ({mechanism_reason}); a required control that cannot be "
            "applied refuses the run (exit 3) instead of running it unwatched"
        )
        if self.allowed_subprocess_prefixes:
            rendered = "; ".join(" ".join(item) for item in self.allowed_subprocess_prefixes)
            lines.append(f"commands: only commands starting with one of these may run: {rendered}")
        else:
            lines.append("commands: no command allowlist is declared — see the approval rules")
        lines.append(f"limits: {self.caps.summary()}")
        lines.append(
            "approval: a command outside this profile is refused; a command that cannot be "
            "decomposed into plain segments (redirection, substitution, wildcards, an "
            "environment prefix, a heredoc) is never auto-approved — it needs the user."
        )
        return "\n".join(lines)

    def prompt_section(self) -> dict[str, str]:
        """The prompt section as data, for the composer's ``permissions`` section."""
        return {
            "name": PROMPT_SECTION_NAME,
            "title": PROMPT_SECTION_TITLE,
            "source": f"skaldir.permission_profile:{self.name}",
            "detail": f"profile {self.identity}",
            "text": self.prompt_block(),
        }

    # -------------------------------------------------------------- reporting

    def enforcement_matrix(self) -> list[dict[str, str]]:
        """One honest row per field: what enforces it, and the value.

        The ``enforced_by`` vocabulary, after the security wave (``MVP/48``):

        ``bwrap argv``
            the mount view and the namespaces — a real argv element, testable.
        ``bwrap argv + landlock``
            the path policy is expressed **twice**: bubblewrap builds the view, Landlock makes
            the kernel enforce the declared set inside the child, so a mount that is missing or
            misordered is not the only thing standing between the agent and the file.
        ``landlock``
            Landlock alone (the no-bubblewrap fallback).
        ``landlock (net)``
            the declared egress policy: ``LANDLOCK_ACCESS_NET_CONNECT_TCP`` / ``BIND_TCP``,
            TCP only — Landlock has no UDP or raw-socket rule, and this matrix says so.
        ``seccomp``
            the syscall classes, with the filter built in-process (no libseccomp).
        ``cgroup v2 pids``
            ``max_processes``, enforced by ``pids.max`` in a delegated user sub-tree.
        ``runner``
            the caps the runner applies to the child process itself.
        ``policy module``
            the approval ladder and the globs a mount cannot express.
        ``unenforced``
            named, always, with the reason.
        """
        rows: list[dict[str, str]] = []
        mechanism, mechanism_reason = self.mechanism()
        landlock = self.landlock_mode()
        seccomp = self.seccomp_mode()
        has_bwrap = bool(self.mount_binary())
        path_owner = (
            "bwrap argv + landlock"
            if has_bwrap and landlock == "enforce"
            else "bwrap argv"
            if has_bwrap
            else "landlock"
            if landlock == "enforce"
            else "unenforced"
        )

        def _row(name: str, enforced_by: str, value: str, detail: str) -> None:
            rows.append(
                {"field": name, "enforced_by": enforced_by, "value": value, "detail": detail}
            )

        _row("schema_version", "metadata", self.schema_version,
             "names the document format; it restricts nothing and enforces nothing")
        _row("name", "metadata", self.name,
             "names the profile: the rules and grants are filed under it, it is not a limit")
        _row("workspace", "runner", self.workspace or str(self.workspace_path()),
             "the root every relative path in this profile is resolved against")
        _row("read_policy", path_owner, self.read_policy,
             "--ro-bind / / (full) or --tmpfs / + --ro-bind-try per root (roots)"
             + (
                 "; Landlock handles READ_FILE/READ_DIR/EXECUTE and allows exactly these roots"
                 if landlock == "enforce"
                 else ""
             ))
        _row("readable_roots", path_owner, ", ".join(self.readable_absolute()),
             "only rendered into the argv while read_policy is 'roots'")
        _row("writable_paths", path_owner,
             ", ".join(self.writable_absolute()) or "(none)",
             "--bind per path, and the same set as a Landlock read-write rule; nothing else in "
             "the filesystem view is writable, and the kernel refuses a write the mounts missed")
        _row("protected_paths", "bwrap argv" if has_bwrap else "unenforced",
             ", ".join(self.protected_absolute()) or "(none)",
             "--ro-bind re-applied after the writable bind, so these win by mount order"
             + (
                 "; Landlock has no deny rule, so a carve-out inside a writable root needs the "
                 "mount view — landlock-only runs cannot express it and report it here"
                 if landlock == "enforce" and not has_bwrap
                 else ""
             )
             if has_bwrap
             else "no bubblewrap on this host; a Landlock ruleset cannot carve a subpath out of a "
             "writable root (rules are allow-only), so these paths are readable and writable "
             "through their parent root")
        if has_bwrap:
            _row("sandbox_tmp", "bwrap argv + landlock", "/tmp",
                 "--tmpfs /tmp gives the child a private scratch space, and the Landlock ruleset "
                 "grants it read-write because it is not the host's /tmp")
        else:
            _row("sandbox_tmp", "unenforced", "(none)",
                 "no bubblewrap on this host, so there is no private tmpfs: /tmp is the host's "
                 "/tmp and stays read-only, because the profile does not declare it writable")
        _row("write_deny_globs", "policy module", ", ".join(self.write_deny_globs),
             "the write tools refuse these paths (sandbox.tools_core.write_denied); neither the "
             "argv nor a Landlock ruleset can express a glob")
        if self.network == "off":
            net_by = " + ".join(
                item
                for item in (
                    "bwrap argv" if has_bwrap else "",
                    "landlock (net)" if landlock == "enforce" else "",
                    "seccomp" if seccomp == "enforce" else "",
                )
                if item
            ) or "unenforced"
            net_detail = (
                "--unshare-net" if has_bwrap else "no network namespace available"
            ) + (
                "; Landlock handles CONNECT_TCP and BIND_TCP with no rule, so the kernel denies "
                "every TCP connect and bind (UDP is not covered by Landlock)"
                if landlock == "enforce"
                else ""
            ) + (
                "; seccomp denies connect/accept/bind/listen/sendto/sendmmsg and every socket "
                "family except AF_UNIX"
                if seccomp == "enforce"
                else ""
            )
        elif self.network_egress_ports:
            net_by = "landlock (net)" if landlock == "enforce" else "unenforced"
            net_detail = (
                "the declared egress allowlist: Landlock handles CONNECT_TCP and allows only "
                "port(s) "
                + ", ".join(str(port) for port in self.network_egress_ports)
                + "; a host allowlist cannot be expressed at this layer, and UDP is not covered"
                if landlock == "enforce"
                else "no Landlock on this host, so the port allowlist is declared and not enforced"
            )
        else:
            net_by = "bwrap argv" if has_bwrap else "runner"
            net_detail = "no --unshare-net: the child keeps the host network namespace"
        _row("network", net_by, self.network, net_detail)
        _row(
            "network_egress_ports",
            "landlock (net)" if (self.network_egress_ports and landlock == "enforce") else
            ("unenforced" if self.network_egress_ports else "metadata"),
            ", ".join(str(port) for port in self.network_egress_ports) or "(none declared)",
            "LANDLOCK_ACCESS_NET_CONNECT_TCP for port(s) "
            + ", ".join(str(port) for port in self.network_egress_ports)
            + " (ABI >= 4): a TCP connect to any other port is EACCES. UDP is not covered by "
            "Landlock at all, and a host allowlist cannot be expressed at this layer"
            if landlock == "enforce"
            else "declared but not enforced on this host: Landlock is unavailable, and seccomp "
            "cannot express a port"
            if self.network_egress_ports
            else "no port restriction is declared; an empty list means 'no limit beyond network'",
        )
        _row("allowed_subprocess_prefixes", "policy module",
             "; ".join(" ".join(item) for item in self.allowed_subprocess_prefixes) or "(any)",
             "skaldir.approval refuses a command whose head token is outside the declared set")
        _row("env_policy", "runner", self.env_policy.summary(),
             "the declared allowlist, drop list and credential patterns are applied by the runner "
             "to the child's environment (values of dropped names are never read, so they cannot "
             "leak); the same apply() is what the report and the prompt block read")
        _row("linux_security", "runner", ", ".join(
            f"{name}={value}" for name, value in sorted(self.linux_security.to_dict().items())
        ), f"resolved here: {mechanism} ({mechanism_reason})")
        _row("caps.timeout_seconds", "runner", f"{self.caps.timeout_seconds:g}",
             "subprocess timeout — a hostile child cannot outlive the wall clock")
        _row("caps.output_max_bytes", "runner", str(self.caps.output_max_bytes),
             "head+tail truncation with the same accounting as the core tools")
        _row("caps.max_memory_bytes", "runner" if self.caps.max_memory_bytes else "unenforced",
             str(self.caps.max_memory_bytes),
             "RLIMIT_AS set in the child's preexec_fn; bubblewrap 0.11 has no --rlimit flag, "
             "so the argv cannot carry it. 0 means no cap is declared."
             if self.caps.max_memory_bytes
             else "no cap declared — nothing enforces a memory ceiling here")
        pids_mode, pids_parent, pids_reason = self.pids_cgroup_mode()
        if not self.caps.max_processes:
            _row("caps.max_processes", "unenforced", "0",
                 "no cap is declared, so nothing enforces a process ceiling. Declare it and a "
                 "cgroup v2 pids leaf caps the tree (no root needed)")
        elif pids_mode == "enforce":
            _row("caps.max_processes", "cgroup v2 pids", str(self.caps.max_processes),
                 f"pids.max written to a leaf under {pids_parent}; every process the child forks "
                 f"counts, and a fork past the cap fails with EAGAIN")
        else:
            _row("caps.max_processes", "unenforced", str(self.caps.max_processes),
                 f"declared but not enforceable here: {pids_reason}. RLIMIT_NPROC is not the "
                 f"answer — {_linux.rlimit_nproc_verdict()['reason'].split('.')[0]}.")
        _row("sandbox_backend", "runner", self.sandbox_backend,
             f"resolves to {self.backend_binary() or '(no sandbox binary)'}; the bubblewrap "
             f"mount view is {self.mount_binary() or '(absent)'}; mechanism {mechanism}")
        return rows

    def unenforced_fields(self) -> list[str]:
        """The fields nothing on this host enforces — the honest gap list."""
        return [
            row["field"]
            for row in self.enforcement_matrix()
            if row["enforced_by"] == "unenforced"
        ]

    # ------------------------------------------------- platform coverage (gap 11)

    #: The primitives whose absence is a *security* downgrade rather than a policy choice. The
    #: two caps (``pids_cgroup``, ``rlimits``) are absent by default because the default profile
    #: declares no cap — that is a decision, and it is reported as one.
    CRITICAL_PRIMITIVES: tuple[str, ...] = (
        "no_new_privs",
        "landlock",
        "namespaces",
        "seccomp",
    )

    def platform_want(self) -> dict[str, bool]:
        """Which enforcement primitives *this profile* asks for, as the coverage model's map.

        Built from the same profile fields the enforcement matrix reads, so "declared" and
        "reported" cannot drift apart: ``linux_security.landlock = 'off'``, a missing bubblewrap
        *(resolved, not declared)*, ``caps.max_processes = 0`` and ``caps.max_memory_bytes = 0``
        each land here as ``False``.
        """
        return {
            "no_new_privs": True,
            "landlock": self.linux_security.landlock != "off",
            "seccomp": self.linux_security.seccomp != "off",
            "namespaces": bool(self.mount_binary()),
            "pids_cgroup": bool(self.caps.max_processes)
            and self.linux_security.pids_cgroup != "off",
            "rlimits": bool(self.caps.max_memory_bytes),
        }

    def coverage_rows(self) -> list[_linux.PrimitiveCoverage]:
        """The measured rows, once — every surface below reads *these*."""
        return _linux.platform_coverage(
            want=self.platform_want(), mechanism=self.mechanism()[0]
        )

    def platform_coverage(self) -> list[dict[str, Any]]:
        """Per-primitive coverage for **this profile on this host**: status, reason, requirement.

        ``enforced`` / ``degraded`` / ``unavailable``, one row per primitive, always with a
        reason when the status is not ``enforced``. This is the answer to "what am I actually
        getting?" on a host or profile where a control is not in force — the silent downgrade
        `MVP/84` (gap 11) found.
        """
        return [row.to_dict() for row in self.coverage_rows()]

    def platform_verdict(self) -> dict[str, Any]:
        """The coverage verdict: worst status, the counts, and whether a command can run at all.

        ``execution_refused`` is the fail-closed fact: with no mechanism on this platform the
        runner refuses the command *before* it starts, so "unsandboxed but working" is not a
        state this product can be in.
        """
        rows = self.coverage_rows()
        mechanism, mechanism_reason = self.mechanism()
        verdict = _linux.coverage_verdict(rows)
        notes = [row.sentence() for row in rows if row.status != _linux.STATUS_ENFORCED]
        support = _linux.platform_support()
        critical = [
            row.primitive
            for row in rows
            if row.primitive in self.CRITICAL_PRIMITIVES
            and row.status != _linux.STATUS_ENFORCED
        ]
        line = f"{support['platform']} ({support['kernel']}): coverage {verdict['verdict']}"
        if notes:
            line += " — " + "; ".join(notes)
        return {
            **verdict,
            "profile_sha256": self.identity_sha256,
            "mechanism": mechanism,
            "mechanism_reason": mechanism_reason,
            "platform": support,
            "critical_not_enforced": critical,
            "execution_refused": mechanism == _linux.MECHANISM_NONE,
            "notes": notes,
            "line": line,
        }


# --------------------------------------------------------------- factories/hashes


# --------------------------------------------------------------- launcher argv


def launcher_path() -> str:
    """The absolute path of the enforcement launcher — `security_linux.py` itself.

    The file is executed **as a script**, not imported: that keeps the launcher's start-up to a
    stdlib-only interpreter with no package import on the critical path, and it is the reason
    ``security_linux`` never imports anything from ``skaldir``.
    """
    return str(Path(_linux.__file__).resolve())


def launcher_python() -> str:
    """The interpreter the launcher runs under — the one that is running *this* code."""
    return str(Path(sys.executable).resolve())


def launcher_argv(
    command: str,
    *,
    canonical_json: str,
    identity_sha256: str,
    plan: _linux.SecurityPlan,
    cwd: str = "/",
    shell: str = "/bin/sh",
) -> list[str]:
    """The argv that applies the plan inside the sandbox and then becomes ``command``.

    One shape, built in one place, so ``bwrap+landlock``, ``landlock`` and the refusal path all
    hand the child exactly the same three facts: the profile JSON, its hash, and the plan.
    """
    return [
        launcher_python(),
        launcher_path(),
        "exec",
        "--permission-profile",
        canonical_json,
        "--permission-profile-sha256",
        identity_sha256,
        "--security-plan",
        plan.to_json(),
        "--cwd",
        str(cwd or "/"),
        "--",
        shell,
        "-c",
        command,
    ]


def launcher_argv_for(profile: PermissionProfile, command: str, *, cwd: str | Path | None = None,
                      plan: _linux.SecurityPlan | None = None) -> list[str]:
    """The launcher argv alone — the fallback path when there is no bubblewrap to wrap it in."""
    current = plan if plan is not None else profile.security_plan()
    return launcher_argv(
        command,
        canonical_json=profile.canonical_json(),
        identity_sha256=profile.identity_sha256,
        plan=current,
        cwd=str(cwd or profile.workspace or "/"),
    )


def default_profile(
    workspace: str | Path | None = None,
    *,
    name: str = "default",
    **overrides: Any,
) -> PermissionProfile:
    """The built-in profile: full read, the workspace writable, the network off.

    The defaults are the *safe* ones on purpose: a profile that forgets to declare a writable
    path writes nowhere, and one that forgets the network gets no network.
    """
    root = Path(workspace).expanduser().resolve() if workspace else Path.cwd().resolve()
    base = {
        "name": name,
        "workspace": str(root),
        "writable_paths": (str(root),),
        "readable_roots": (str(root),),
    }
    base.update(overrides)
    return PermissionProfile(**base)


def profile_from_argv(argv: Sequence[str]) -> dict[str, Any]:
    """The profile JSON a sandbox argv carries (``{}`` when the argv carries none)."""
    items = [str(item) for item in argv]
    for index, item in enumerate(items):
        if item == PROFILE_ARGV_FLAG and index + 1 < len(items):
            try:
                data = json.loads(items[index + 1])
            except (TypeError, ValueError):
                return {}
            return data if isinstance(data, dict) else {}
        if item in ("--setenv", "--env") and index + 2 < len(items):
            if items[index + 1] == PROFILE_ENV:
                try:
                    data = json.loads(items[index + 2])
                except (TypeError, ValueError):
                    return {}
                return data if isinstance(data, dict) else {}
    return {}


def permission_profile_hash_from_argv(argv: Sequence[str]) -> str:
    """The profile hash a sandbox argv carries, or ``""``.

    Reads the explicit ``SKALDIR_PERMISSION_PROFILE_SHA256`` element *and* re-hashes the
    profile JSON element; when the two disagree the explicit one is refused, because an argv
    that claims a hash it does not carry is exactly the drift this design forbids.
    """
    items = [str(item) for item in argv]
    claimed = ""
    for index, item in enumerate(items):
        if item in ("--setenv", "--env") and index + 2 < len(items):
            if items[index + 1] == PROFILE_SHA_ENV:
                claimed = items[index + 2].strip().lower()
    payload = profile_from_argv(items)
    recomputed = ""
    if payload:
        recomputed = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
    if claimed and recomputed and claimed != recomputed:
        return ""
    return claimed or recomputed


def permission_profile_hash_from_prompt(text: str) -> str:
    """The profile hash an agent-visible prompt block carries, or ``""``."""
    match = _HASH_IN_BLOCK.search(str(text))
    return match.group(1) if match else ""


def hash_is_wellformed(value: str) -> bool:
    return bool(_HASH_RE.match(str(value).strip().lower()))


def shlex_join(argv: Sequence[str]) -> str:
    """An argv as one copy-pasteable line (every element quoted)."""
    return " ".join(shlex.quote(str(item)) for item in argv)


# ------------------------------------------------------------------ persistence


def permissions_dir(runtime: str | Path) -> Path:
    return Path(runtime) / "permissions"


def profile_path(runtime: str | Path, name: str = "default") -> Path:
    return permissions_dir(runtime) / f"{name.strip() or 'default'}.json"


def save_profile(profile: PermissionProfile, runtime: str | Path) -> Path:
    """Persist one named profile; returns the path it landed at."""
    target = profile_path(runtime, profile.name)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(profile.to_json(), encoding="utf-8")
    return target


def load_profile(runtime: str | Path, name: str = "default") -> PermissionProfile | None:
    """Load one named profile, or ``None`` when it was never written."""
    target = profile_path(runtime, name)
    if not target.is_file():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return PermissionProfile.from_dict(data)


def profile_names(runtime: str | Path) -> list[str]:
    directory = permissions_dir(runtime)
    if not directory.is_dir():
        return []
    return sorted(path.stem for path in directory.glob("*.json"))


#: Field → the ``SKALDIR_PERMISSION_*`` token that overrides it (bare tokens go through
#: :mod:`skaldir.env`, so the legacy ``GODSV_*`` spelling keeps working).
ENV_FIELD_TOKENS: dict[str, str] = {
    "network": "PERMISSION_NETWORK",
    "read_policy": "PERMISSION_READ_POLICY",
    "sandbox_backend": "PERMISSION_SANDBOX_BACKEND",
    "name": "PERMISSION_PROFILE_NAME",
}


def profile_from_env(
    workspace: str | Path | None = None, *, base: PermissionProfile | None = None
) -> PermissionProfile:
    """The profile with the declared environment overrides applied (highest precedence)."""
    current = base or default_profile(workspace)
    changes: dict[str, Any] = {}
    for field_name, token in ENV_FIELD_TOKENS.items():
        value = _env.getenv(token)
        if value:
            changes[field_name] = value.strip()
    network = changes.get("network")
    if network and network not in NETWORK_MODES:
        changes.pop("network")
    policy = changes.get("read_policy")
    if policy and policy not in READ_POLICIES:
        changes.pop("read_policy")
    backend = changes.get("sandbox_backend")
    if backend and backend not in BACKENDS:
        changes.pop("sandbox_backend")
    return current.with_changes(**changes) if changes else current


def effective_profile(
    runtime: str | Path | None = None,
    workspace: str | Path | None = None,
    *,
    name: str = "default",
    use_env: bool = True,
) -> PermissionProfile:
    """The one resolution order: built-in default → the stored profile → env overrides."""
    base = default_profile(workspace, name=name)
    if runtime is not None:
        stored = load_profile(runtime, name)
        if stored is not None:
            # The stored profile names the workspace it was created for; a caller-supplied
            # workspace wins for *resolution*, but it must not erase a declared writable set:
            # widening or narrowing what may be written because the caller chdir'ed would be
            # exactly the silent drift this module exists to prevent. The declared *sets* are
            # kept; only the root they resolve against moves — and if nothing was declared,
            # the workspace is the one writable path (the safe default).
            root = str(Path(workspace).expanduser().resolve()) if workspace else stored.workspace
            changes: dict[str, Any] = {"workspace": root or stored.workspace}
            if not stored.writable_paths and root:
                changes["writable_paths"] = (root,)
            base = stored.with_changes(**changes)
    return profile_from_env(workspace, base=base) if use_env else base


# ---------------------------------------------------------------------- runner


@dataclass
class SandboxRun:
    """One measured run under a profile: what ran, what it cost, what was enforced."""

    profile_sha256: str
    backend: str
    argv: list[str]
    exit_code: int
    output: str
    duration_ms: int
    sandboxed: bool
    blocked: bool = False
    blocked_reason: str = ""
    output_truncated: bool = False
    caps_enforced: list[str] = field(default_factory=list)
    caps_unenforced: list[str] = field(default_factory=list)
    spill_path: str = ""
    #: The mechanism that ran: ``bwrap+landlock`` / ``landlock`` / ``bwrap`` / ``none``.
    mechanism: str = ""
    mechanism_reason: str = ""
    #: What the *child* reported it applied (``SKALDIR-SECURITY-REPORT``). ``None`` means the
    #: child never confirmed — which is reported as such, never assumed to be success.
    security_report: dict[str, Any] = field(default_factory=dict)
    #: The refused control, when a control the policy required could not be applied.
    refusal: dict[str, Any] = field(default_factory=dict)
    #: What the runner did to the child's environment, and the cgroup accounting.
    env_report: dict[str, Any] = field(default_factory=dict)
    pids_report: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def controls_enforced(self) -> list[str]:
        """The controls the child confirmed — read from the report, not from the plan."""
        controls = dict(self.security_report.get("controls") or {})
        return sorted(name for name, ok in controls.items() if ok)

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_sha256": self.profile_sha256,
            "backend": self.backend,
            "argv": list(self.argv),
            "argv_line": shlex_join(self.argv),
            "exit_code": int(self.exit_code),
            "output": self.output,
            "duration_ms": int(self.duration_ms),
            "sandboxed": bool(self.sandboxed),
            "blocked": bool(self.blocked),
            "blocked_reason": self.blocked_reason,
            "output_truncated": bool(self.output_truncated),
            "caps_enforced": list(self.caps_enforced),
            "caps_unenforced": list(self.caps_unenforced),
            "spill_path": self.spill_path,
            "mechanism": self.mechanism,
            "mechanism_reason": self.mechanism_reason,
            "security_report": dict(self.security_report),
            "controls_enforced": self.controls_enforced,
            "refusal": dict(self.refusal),
            "env_report": dict(self.env_report),
            "pids_report": dict(self.pids_report),
            "warnings": list(self.warnings),
        }


def probe_backend(profile: PermissionProfile | None = None) -> dict[str, Any]:
    """Does the profile's sandbox really work *here*? Measure, never assume.

    The probe runs the profile's own argv (with a trivial command) and reports the raw
    return code and stderr. A failed probe is never cached, so a fixed host is picked up.
    """
    current = profile or default_profile()
    binary = current.backend_binary()
    report: dict[str, Any] = {
        "backend": Path(binary).name if binary else "",
        "binary": binary,
        "available": False,
        "probe_argv": [],
        "probe_output": "",
    }
    if not binary:
        report["probe_output"] = "no bubblewrap and no podman on PATH"
        return report
    # a probe must not fail for a missing workspace: it proves the *sandbox* works, and the
    # argv it runs is still the profile's own argv (only --chdir moves). The writable binds
    # stay in place — they are what makes a workspace under a masked path (``--tmpfs /tmp``)
    # reachable at all.
    probe_cwd = current.workspace if current.workspace and Path(current.workspace).is_dir() else "/"
    if Path(binary).name.startswith("bwrap"):
        argv = current.sandbox_argv("/bin/true", cwd=probe_cwd)
    else:
        argv = current.podman_argv(
            "/bin/true", image="docker.io/library/busybox:1.36", cwd=probe_cwd
        )
    report["probe_argv"] = argv
    try:
        completed = subprocess.run(  # noqa: S603 — fixed argv, no shell
            argv, capture_output=True, timeout=20, check=False
        )
        report["available"] = completed.returncode == 0
        report["returncode"] = completed.returncode
        report["probe_output"] = (completed.stdout + completed.stderr).decode(
            "utf-8", "replace"
        ).strip()[:400]
    except (OSError, subprocess.SubprocessError) as exc:
        report["probe_output"] = f"{type(exc).__name__}: {exc}"
    return report


def run_in_sandbox(
    profile: PermissionProfile,
    command: str,
    *,
    runtime: str | Path | None = None,
    cwd: str | Path | None = None,
    timeout: float | None = None,
) -> SandboxRun:
    """Run ``command`` under ``profile`` — fail-closed, and honest about the caps.

    There is **no silent unprotected fallback**: with no working mechanism the run is
    ``blocked``, and a control the profile *required* that the host cannot apply refuses the
    command **before it starts** — the refusal carries the control, the kernel's own reason and
    the fix, and it is what the agent sees. That is the whole difference between "declared" and
    "enforced", and it is measured on every run:

    * the mechanism that ran is resolved from probes (Landlock ABI, bubblewrap, cgroup
      delegation) and recorded;
    * the child writes one ``SKALDIR-SECURITY-REPORT`` line *after* it applied the controls, so
      the enforcement is confirmed by the process that ran and not by the plan that described it;
    * ``max_processes`` is a real cgroup v2 ``pids.max`` leaf, and its ``pids.events`` counter is
      kept as evidence that the cap ever fired.
    """
    plan = profile.security_plan()
    mechanism, mechanism_reason = profile.mechanism()
    unenforceable = profile.unenforceable_required()
    required_failure = _refusal_payload(unenforceable)
    if unenforceable:
        return SandboxRun(
            profile_sha256=profile.identity_sha256,
            backend="none",
            argv=[],
            exit_code=3,
            output="",
            duration_ms=0,
            sandboxed=False,
            blocked=True,
            blocked_reason=(
                "a control this profile requires cannot be applied: "
                + "; ".join(f"{item['control']}: {item['reason']}" for item in unenforceable)
            ),
            refusal=required_failure,
            mechanism=mechanism,
            mechanism_reason=mechanism_reason,
        )
    if mechanism == _linux.MECHANISM_NONE:
        return SandboxRun(
            profile_sha256=profile.identity_sha256,
            backend="none",
            argv=[],
            exit_code=3,
            output="",
            duration_ms=0,
            sandboxed=False,
            blocked=True,
            blocked_reason=(
                f"sandbox unavailable — execution blocked (fail-closed): {mechanism_reason}"
            ),
            caps_unenforced=["filesystem", "network", "namespaces"],
            mechanism=mechanism,
            mechanism_reason=mechanism_reason,
        )
    binary = profile.mount_binary()
    warnings: list[str] = []

    # --- the process cap: a cgroup v2 leaf, created before the child exists so the child can be
    # placed in it from the moment it is forked.
    cgroup: _linux.ProcessCgroup | None = None
    pids_mode, _parent, pids_reason = profile.pids_cgroup_mode()
    if plan.pids_max and pids_mode == "enforce":
        try:
            cgroup = _linux.ProcessCgroup.open(int(plan.pids_max), label="run")
        except _linux.CgroupUnavailable as exc:
            warnings.append(f"max_processes not enforced: {exc.reason}")
    elif plan.pids_max:
        warnings.append(f"max_processes not enforced: {pids_reason}")

    argv = (
        profile.sandbox_argv(command, cwd=cwd or profile.workspace or None, plan=plan)
        if binary
        else launcher_argv_for(profile, command, cwd=cwd, plan=plan)
    )
    env, env_report = profile.child_environment()
    if cgroup is not None:
        env["SKALDIR_SECURITY_CGROUP"] = str(cgroup.path)

    def _limits() -> None:  # pragma: no cover — runs in the forked child
        import resource

        if profile.caps.max_memory_bytes:
            limit = int(profile.caps.max_memory_bytes)
            resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
        if cgroup is not None:
            # Every descendant inherits the cgroup, so the cap covers the whole tree however the
            # child forks — and it is applied here, before exec, so bubblewrap itself is inside.
            try:
                cgroup.join(os.getpid())
            except OSError:
                pass

    caps_enforced: list[str] = []
    if profile.caps.max_memory_bytes:
        caps_enforced.append("memory")
    if cgroup is not None:
        caps_enforced.append("processes")
    started = time.time()
    try:
        completed = subprocess.run(  # noqa: S603 — fixed argv, no shell
            argv,
            capture_output=True,
            timeout=float(timeout if timeout is not None else profile.caps.timeout_seconds),
            check=False,
            env=env,
            preexec_fn=_limits if (profile.caps.max_memory_bytes or cgroup) else None,
        )
        raw = completed.stdout + completed.stderr
        exit_code = int(completed.returncode)
    except subprocess.TimeoutExpired as exc:
        raw = (exc.stdout or b"") + (exc.stderr or b"")
        exit_code = 124
    except (OSError, subprocess.SubprocessError) as exc:
        _release(cgroup)
        return SandboxRun(
            profile_sha256=profile.identity_sha256,
            backend=Path(binary).name if binary else "launcher",
            argv=argv,
            exit_code=7,
            output=f"{type(exc).__name__}: {exc}",
            duration_ms=int((time.time() - started) * 1000),
            sandboxed=False,
            blocked=True,
            blocked_reason=f"the sandbox could not be started: {type(exc).__name__}",
            mechanism=mechanism,
            mechanism_reason=mechanism_reason,
            env_report=env_report,
            warnings=warnings,
        )
    duration_ms = int((time.time() - started) * 1000)
    text = raw.decode("utf-8", "replace")
    report = _linux.report_from_output(text)
    refusal = _linux.refusal_from_output(text)
    if report is None and refusal is None:
        warnings.append(
            "the child wrote no SKALDIR-SECURITY-REPORT line, so the controls it applied are "
            "unconfirmed — the run is reported as sandboxed but not as verified"
        )
    if refusal:
        warnings.append(_refusal_sentence(refusal))
    pids_report = cgroup.release() if cgroup is not None else {}
    if pids_report and not pids_report.get("removed"):
        warnings.append(
            f"cgroup {pids_report.get('path')} was not removed "
            f"({pids_report.get('reason')}) — processes may still be inside it"
        )
    # the protocol lines are metadata, not output: they are removed *before* the cap is applied,
    # so a clipped run spills exactly what the command wrote and not our own report.
    clean = _linux.strip_report_lines(text).encode("utf-8")
    clipped, truncated, spill = _clip_with_spill(clean, profile.caps, runtime)
    return SandboxRun(
        profile_sha256=profile.identity_sha256,
        backend=Path(binary).name if binary else "launcher",
        argv=argv,
        exit_code=exit_code,
        output=clipped,
        duration_ms=duration_ms,
        sandboxed=True,
        blocked=bool(refusal),
        blocked_reason=_refusal_sentence(refusal) if refusal else "",
        output_truncated=truncated,
        caps_enforced=[*caps_enforced, "timeout", "output"],
        caps_unenforced=profile.unenforced_fields(),
        spill_path=spill,
        mechanism=mechanism,
        mechanism_reason=mechanism_reason,
        security_report=report.to_dict() if report else {},
        refusal=dict(refusal or {}),
        env_report=env_report,
        pids_report=pids_report,
        warnings=warnings,
    )


def _refusal_payload(items: list[dict[str, str]]) -> dict[str, Any]:
    """A refused run as one payload: a single control, or the whole list when several failed."""
    if len(items) == 1:
        return dict(items[0])
    return {"control": ", ".join(item["control"] for item in items), "issues": items}


def _refusal_sentence(refusal: Mapping[str, Any] | None) -> str:
    """The refusal **in the words the agent gets** — control, kernel reason, and the fix.

    This is the model-facing half of a refusal: an enforcement failure is not a mystery exit
    code, it is a sentence naming the control, why the kernel refused it, and what to change. A
    run that was refused reports ``blocked`` with this text, so an agent that reads it can act
    instead of retrying the same thing.
    """
    if not refusal:
        return ""
    control = str(refusal.get("control", ""))
    reason = str(refusal.get("reason", ""))
    fix = str(refusal.get("fix", ""))
    text = f"refused before the command could run — {control}: {reason}"
    if fix:
        text += f"; {fix}"
    return text


def _release(cgroup: _linux.ProcessCgroup | None) -> dict[str, Any]:
    """Best-effort cgroup teardown on an error path; the answer is never assumed."""
    if cgroup is None:
        return {}
    try:
        return cgroup.release()
    except OSError:  # pragma: no cover — a broken cgroupfs must not mask the real error
        return {"path": str(cgroup.path), "removed": False, "reason": "release raised OSError"}


def _clip(raw: bytes, limit: int) -> str:
    text = raw.decode("utf-8", "replace")
    if len(raw) <= limit:
        return text
    head = limit // 2
    return (
        text[:head]
        + f"\n[TRUNCATED: output was {len(raw)} bytes; {limit} kept]\n"
        + text[-(limit - head):]
    )


def _clip_with_spill(
    raw: bytes, caps: ResourceCaps, runtime: str | Path | None
) -> tuple[str, bool, str]:
    """Head+tail clip with the full text spilled when a runtime dir is available."""
    if len(raw) <= caps.output_max_bytes:
        return raw.decode("utf-8", "replace"), False, ""
    spill = ""
    target = None
    if runtime is not None:
        directory = Path(runtime) / "permission_output"
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"run-{int(time.time() * 1000)}-{id(raw) & 0xFFFF}.txt"
        target.write_bytes(raw[: caps.spill_max_bytes])
        spill = str(target)
    head = caps.output_max_bytes // 2
    tail = caps.output_max_bytes - head
    text = raw.decode("utf-8", "replace")
    marker = (
        f"[TRUNCATED: output was {len(raw)} bytes; {head} head + {tail} tail kept"
        + (f"; full output: {spill}" if spill else "; full output not spilled")
        + "]"
    )
    return text[:head] + f"\n{marker}\n" + text[-tail:], True, spill


def is_inside(target: str | Path, base: str | Path) -> bool:
    """Is ``target`` inside ``base`` (after resolving symlinks)? The one containment rule."""
    try:
        Path(target).resolve().relative_to(Path(base).resolve())
    except (OSError, ValueError):
        return False
    return True


def writable_roots_for(paths: Iterable[str], writable: Sequence[str]) -> list[str]:
    """Which of ``paths`` the profile declares writable — the check a writer asks for."""
    roots = [str(Path(item).resolve()) for item in writable]
    return [
        str(Path(item).resolve())
        for item in paths
        if any(is_inside(item, root) for root in roots)
    ]


def prompt_provider(runtime: str | Path | None = None, workspace: str | Path | None = None,
                    *, name: str = "default") -> Any:
    """A composer-ready provider for the ``permissions`` section.

    Returns a callable the prompt composer can call with its own build context; the section it
    produces is rendered from :meth:`PermissionProfile.prompt_block`, so the block the agent
    reads carries the very hash the sandbox argv does. It duck-types the composer's
    ``SectionContent`` (``text`` / ``source`` / ``detail``) and uses the real class when the
    composer is importable — one object, two consumers, whichever way the composer is built.
    """

    def _provider(_context: Any = None) -> Any:
        scope = _context
        resolved = effective_profile(
            getattr(scope, "runtime", None) or runtime,
            getattr(scope, "project", None) or workspace,
            name=name,
        )
        section = resolved.prompt_section()
        try:
            from . import prompt_builder as composer

            return composer.SectionContent(
                text=section["text"], source=section["source"], detail=section["detail"]
            )
        except Exception:  # noqa: BLE001 — a build without the composer still gets the block
            return _PlainSection(section["text"], section["source"], section["detail"])

    return _provider


@dataclass(frozen=True)
class _PlainSection:
    """The composer's ``SectionContent`` shape, without importing the composer."""

    text: str
    source: str
    detail: str = ""


def enforcement_matrix_fields() -> tuple[str, ...]:
    """The field names the matrix covers — pinned so a new field cannot be forgotten."""
    return PROFILE_FIELDS


__all__ = [
    "BACKENDS",
    "DEFAULT_PROTECTED_PATHS",
    "DEFAULT_WRITE_DENY_GLOBS",
    "ENV_FIELD_TOKENS",
    "NETWORK_MODES",
    "NETWORK_ENV",
    "PLATFORM_DEFAULT_READ_ROOTS",
    "PROFILE_ARGV_FLAG",
    "PROFILE_ENV",
    "PROMPT_SECTION_BUDGET_CHARS",
    "PROMPT_SECTION_NAME",
    "PROMPT_SECTION_TITLE",
    "PROFILE_FIELDS",
    "PROFILE_NAME_ENV",
    "PROFILE_SHA_ENV",
    "READ_POLICIES",
    "SCHEMA_VERSION",
    "PermissionProfile",
    "ResourceCaps",
    "SandboxRun",
    "default_profile",
    "effective_profile",
    "enforcement_matrix_fields",
    "hash_is_wellformed",
    "is_inside",
    "load_profile",
    "permission_profile_hash_from_argv",
    "permission_profile_hash_from_prompt",
    "permissions_dir",
    "probe_backend",
    "profile_from_argv",
    "profile_from_env",
    "profile_names",
    "profile_path",
    "prompt_provider",
    "run_in_sandbox",
    "save_profile",
    "shlex_join",
    "writable_roots_for",
]
