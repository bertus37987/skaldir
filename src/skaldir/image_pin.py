"""Registry manifest digest pinning — the artifact identity a capability is bound to.

Gap D (EXPAND). The old "pin" was the *local* image id
(``docker image inspect --format {{.Id}}``). That is not reproducibility: a local id
is assigned by whatever daemon holds the image, it changes when the image store
changes driver, and it says nothing about what the registry still serves. This module
resolves the pin where it belongs — at the **registry**: the manifest digest of the
image reference, plus the per-platform manifest digest, recorded once and verified
from then on.

What it does
------------

* **Resolve** the registry manifest digest of a reference. Primary source is the
  registry itself (``docker buildx imagetools inspect --format {{json .}}``); a daemon
  that lacks the buildx plugin falls back to ``docker manifest inspect --verbose``;
  ``--offline`` reads the digest the daemon recorded for the *pulled* image
  (``.RepoDigests``) and marks it honestly as **not registry-verified**.
* **Record** that digest as a pin (atomic JSON store, one file).
* **Verify** a pin: the local image must still carry the pinned digest, and with
  ``remote=True`` the tag must still resolve to it.
* **Refuse**: a pin whose digest no longer matches is a conflict — the caller gets a
  distinct English message and exit code 6, never a silent pass. A deliberate re-pin
  is required.
* **Carry** the digest into capability registration
  (:func:`registration_evidence` / :func:`require_registration_pin`) so the evidence
  names the exact artifact (``docker.io/library/busybox@sha256:…``) instead of a tag.
* **Guard** the digest long after it was recorded: :func:`guard_health`,
  :func:`guard_invoke` and :func:`guard_rollback` re-verify it at a capability's health
  check, at **every** invocation and before a rollback. Each surface fails closed with
  its own exit code — 2 for health, 6 for invocation, 3 for rollback — and each carries
  the digest into its evidence, so one version number can never quietly resolve to
  different bytes. A ``(capability, version)`` binding is recorded with the pin
  (:meth:`PinStore.bind_version`) to make that a checkable fact.

Two digests, one artifact
-------------------------

A multi-arch reference has an **index** digest (the manifest list) and one manifest
digest per platform. Docker's image store decides which of the two it records for a
pulled image (measured on this host, Server 29.6.1, ``Driver: overlayfs``: the
containerd image store records the index digest; the classic store records the
platform manifest digest). :meth:`ImagePin.carries_local` accepts either, because both
name the same pulled bytes for the pinned platform — but it never accepts a third
value.

Exit-code vocabulary (``cli_contract``)
---------------------------------------

====  ===================================================================
0     ok — resolved / pinned / verified (``offline_unverified`` is 0 with a warning)
1     usage
2     verification failed — ``image guard --surface health`` refused the digest
3     blocked — no network, a registry rate limit (429), the Docker daemon does not
      answer, or a rollback whose digest cannot be certified
4     needs credentials — the registry refused the manifest request
5     not found — no pin recorded, image never pulled, manifest unknown
6     conflict — digest mismatch, the tag moved away from the pin, a version bound to
      another digest, or an attempted re-pin (``image guard --surface invoke``)
7     internal — an unreadable registry answer or a broken pin store
====  ===================================================================
"""

from __future__ import annotations

import json
import os
import platform as _platform_mod
import re
import shutil
import subprocess  # nosec B404 — driving the docker CLI is this module's purpose
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PIN_SCHEMA = "godsv.image-pin/1"

#: A digest as docker spells it (and as ``docker_runtime`` requires for a pin).
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

#: The pin store's file name inside the runtime dir.
PIN_FILENAME = "image-pins.json"

DEFAULT_TIMEOUT = 60.0

# -- states + exit codes ----------------------------------------------------- #
STATE_PINNED = "pinned"
STATE_OFFLINE_UNVERIFIED = "offline_unverified"
STATE_UNPINNED = "unpinned"
STATE_NOT_PULLED = "not_pulled"
STATE_TAG_MOVED = "tag_moved"
STATE_MISMATCH = "mismatch"
STATE_PIN_CONFLICT = "pin_conflict"
STATE_NOT_FOUND = "not_found"
STATE_AUTH_REQUIRED = "auth_required"
STATE_RATE_LIMITED = "rate_limited"
STATE_NO_NETWORK = "no_network"
STATE_DOCKER_UNREACHABLE = "docker_unreachable"
STATE_UNRESOLVED = "unresolved"

#: The one place a state maps onto an exit code.
STATE_EXIT_CODES: dict[str, int] = {
    STATE_PINNED: 0,
    STATE_OFFLINE_UNVERIFIED: 0,
    STATE_UNPINNED: 5,
    STATE_NOT_PULLED: 5,
    STATE_NOT_FOUND: 5,
    STATE_AUTH_REQUIRED: 4,
    STATE_RATE_LIMITED: 3,
    STATE_NO_NETWORK: 3,
    STATE_DOCKER_UNREACHABLE: 3,
    STATE_TAG_MOVED: 6,
    STATE_MISMATCH: 6,
    STATE_PIN_CONFLICT: 6,
    STATE_UNRESOLVED: 7,
}

#: Digest kinds a pin may carry, in the order of preference.
DIGEST_KIND_INDEX = "index"
DIGEST_KIND_PLATFORM = "platform-manifest"
DIGEST_KIND_LOCAL = "local-repo-digest"

#: (message, fix) per state — English, one sentence each, with the fix as text for
#: the human. Skaldir never runs a ``sudo`` command itself; these strings never
#: contain one that it would.
MESSAGES: dict[str, tuple[str, str]] = {
    STATE_PINNED: (
        "the registry manifest digest of {reference} is {digest} "
        "({digest_kind}, {platform}).",
        "",
    ),
    STATE_OFFLINE_UNVERIFIED: (
        "no registry was consulted: the digest of {reference} comes from the daemon's "
        "own pull record and is not registry-verified.",
        "run the same command without --offline to verify it against the registry.",
    ),
    STATE_UNPINNED: (
        "no pin is recorded for {reference} — nothing can be verified against a "
        "registry manifest digest yet.",
        "skaldir image pin {reference}",
    ),
    STATE_NOT_PULLED: (
        "the image {reference} is not present locally, so there is nothing to verify "
        "the pinned digest {digest} against.",
        "docker pull {reference}",
    ),
    STATE_TAG_MOVED: (
        "the tag {reference} now resolves to {remote_digest}, but the pin records "
        "{digest} — the reference no longer names the pinned artifact.",
        "decide deliberately: check the new digest, then re-pin with "
        "`skaldir image pin {reference} --force`; the recorded digest itself is "
        "immutable and stays pullable as {artifact}.",
    ),
    STATE_MISMATCH: (
        "REFUSED: the local image for {reference} carries {local_digests}, not the "
        "pinned {digest} — the artifact changed since it was pinned.",
        "do not run it; check what changed and re-pin deliberately with "
        "`skaldir image pin {reference} --force`.",
    ),
    STATE_PIN_CONFLICT: (
        "a pin for {reference} already records {digest}; refusing to overwrite it "
        "with {new_digest}.",
        "re-pin deliberately with `--force` once you have checked the new digest.",
    ),
    STATE_NOT_FOUND: (
        "the registry has no manifest for {reference} ({detail}).",
        "check the repository and tag — a typo looks exactly like this.",
    ),
    STATE_AUTH_REQUIRED: (
        "the registry refused the manifest request for {reference}: the repository is "
        "missing, private, or your login does not cover it ({detail}).",
        "log in once with `docker login <registry>`, or check the repository name.",
    ),
    STATE_RATE_LIMITED: (
        "the registry is rate-limiting this machine for {reference} — it answered 429 "
        "and no manifest was served ({detail}).",
        "wait and retry, or log in with `docker login` to use your own pull budget "
        "instead of the anonymous one.",
    ),
    STATE_NO_NETWORK: (
        "the registry cannot be reached for {reference} — no network ({detail}).",
        "restore the network, or pass --offline to read the digest the daemon recorded "
        "at pull time (honestly marked as not registry-verified).",
    ),
    STATE_DOCKER_UNREACHABLE: (
        "the Docker daemon did not answer, so no image can be inspected locally "
        "({detail}).",
        "start it: `sudo systemctl enable --now docker` — then check with: docker info",
    ),
    STATE_UNRESOLVED: (
        "the registry answer for {reference} could not be read ({detail}).",
        "run `docker buildx imagetools inspect {reference}` by hand and report it.",
    ),
}


# -- digest guards: the digest is re-verified long after it was recorded --------- #
#: The three surfaces that must re-verify a recorded digest. A pin that is only
#: checked at registration time proves nothing about the artifact that runs today.
GUARD_HEALTH = "health"
GUARD_INVOKE = "invoke"
GUARD_ROLLBACK = "rollback"
GUARD_SURFACES: tuple[str, ...] = (GUARD_HEALTH, GUARD_INVOKE, GUARD_ROLLBACK)

#: Each surface fails **closed** with its own exit code — three surfaces, three codes,
#: never a zero: a health check that cannot certify the artifact is a failed
#: verification (2), an invocation on an artifact that disagrees with the registration
#: is a conflict (6), a rollback whose target cannot be certified is blocked (3).
GUARD_EXIT_CODES: dict[str, int] = {
    GUARD_HEALTH: 2,
    GUARD_INVOKE: 6,
    GUARD_ROLLBACK: 3,
}

GUARD_LABELS: dict[str, str] = {
    GUARD_HEALTH: "capability health check",
    GUARD_INVOKE: "capability invocation",
    GUARD_ROLLBACK: "capability rollback",
}

#: (message, fix) per surface and state. The three refusal sentences for the *same*
#: cause are deliberately different: an operator must be able to tell from the text
#: which surface refused, without reading the code.
GUARD_MESSAGES: dict[str, dict[str, tuple[str, str]]] = {
    GUARD_HEALTH: {
        STATE_MISMATCH: (
            "the digest guard FAILED the health check of {capability}: the local image "
            "for {reference} carries {local_digests}, but the registration recorded "
            "{digest} — the capability is not HEALTHY.",
            "do not route to it; re-pin deliberately "
            "(`skaldir image pin {reference} --force`) or retire the capability.",
        ),
        STATE_TAG_MOVED: (
            "the digest guard FAILED the health check of {capability}: the tag "
            "{reference} now resolves to {remote_digest}, not to the registered "
            "{digest}.",
            "decide deliberately: re-pin, or retire the capability.",
        ),
        STATE_UNPINNED: (
            "REFUSED the health check of {capability}: no pin is recorded for "
            "{reference} — an unpinned artifact cannot be certified HEALTHY.",
            "record the registry digest first: skaldir image pin {reference} "
            "--capability {capability}",
        ),
        STATE_PIN_CONFLICT: (
            "the digest guard FAILED the health check of {capability}: version "
            "{version} was registered on {digest}, while the pin now records "
            "{new_digest} — the same version resolves to different bytes, so it is not "
            "HEALTHY.",
            "register the version again deliberately, or pin the artifact this machine "
            "actually runs.",
        ),
        STATE_NOT_PULLED: (
            "REFUSED the health check of {capability}: {reference} is not present "
            "locally, so the registered digest {digest} cannot be checked.",
            "docker pull {reference}",
        ),
    },
    GUARD_INVOKE: {
        STATE_MISMATCH: (
            "REFUSED to invoke {capability}: the image behind {reference} carries "
            "{local_digests}, not the registered {digest} — one version must never run "
            "different bytes.",
            "stop; check what changed, then re-pin deliberately or restore the "
            "registered artifact {artifact}.",
        ),
        STATE_TAG_MOVED: (
            "REFUSED to invoke {capability}: the tag {reference} now resolves to "
            "{remote_digest}, not to the registered {digest}.",
            "re-pin deliberately, or invoke the registered artifact {artifact} directly.",
        ),
        STATE_UNPINNED: (
            "REFUSED to invoke {capability}: no pin is recorded for {reference} — an "
            "unpinned artifact may not be invoked.",
            "record the registry digest first: skaldir image pin {reference} "
            "--capability {capability}",
        ),
        STATE_PIN_CONFLICT: (
            "REFUSED to invoke {capability} version {version}: the version was "
            "registered on {digest}, while the pin now records {new_digest} — one "
            "version number must never run different bytes.",
            "re-pin or re-register the version deliberately before invoking it.",
        ),
        STATE_NOT_PULLED: (
            "REFUSED to invoke {capability}: {reference} is not present locally, so the "
            "registered digest {digest} cannot be checked.",
            "docker pull {reference}",
        ),
    },
    GUARD_ROLLBACK: {
        STATE_MISMATCH: (
            "REFUSED to roll back {capability} to version {version}: that version was "
            "registered on {digest}, but the artifact now carries {local_digests}.",
            "do not roll back onto a changed artifact; restore {artifact}, or register "
            "the version again deliberately.",
        ),
        STATE_PIN_CONFLICT: (
            "REFUSED to roll back {capability} to version {version}: the version was "
            "registered on {digest}, while the pin now records {new_digest} — the same "
            "version number would resolve to different bytes.",
            "decide deliberately: re-pin the reference, or register the version again "
            "with its new digest.",
        ),
        STATE_TAG_MOVED: (
            "REFUSED to roll back {capability} to version {version}: the tag "
            "{reference} now resolves to {remote_digest}, not to the registered "
            "{digest}.",
            "re-pin deliberately before rolling back.",
        ),
        STATE_UNPINNED: (
            "REFUSED to roll back {capability}: no pin is recorded for {reference} — a "
            "rollback target without a pinned digest cannot be certified.",
            "record the registry digest first: skaldir image pin {reference} "
            "--capability {capability}",
        ),
        STATE_NOT_PULLED: (
            "REFUSED to roll back {capability} to version {version}: {reference} is not "
            "present locally, so the registered digest {digest} cannot be checked.",
            "docker pull {reference}",
        ),
    },
}


def guard_exit_code(surface: str) -> int:
    """The fail-closed exit code of one guard surface (an unknown surface is internal)."""
    return int(GUARD_EXIT_CODES.get(str(surface), 7))


def guard_message(surface: str, state: str, **fields: Any) -> tuple[str, str]:
    """``(message, fix)`` of one surface+state; other states keep their own words."""
    specific = GUARD_MESSAGES.get(str(surface), {}).get(str(state))
    if specific is not None:
        message = specific[0]
        fix = specific[1]
    else:
        state_text, state_fix = state_message(state, **fields)
        message = (
            f"REFUSED the {GUARD_LABELS.get(str(surface), 'operation')} of "
            f"{fields.get('capability') or 'the capability'}: {state_text}"
        )
        fix = state_fix
    values = {"capability": "", "reference": "", "digest": "", "new_digest": "",
              "remote_digest": "", "local_digests": "", "platform": "", "artifact": "",
              "digest_kind": "", "detail": "", "version": ""}
    values.update({key: ("" if value is None else value) for key, value in fields.items()})
    values = {key: (", ".join(str(item) for item in value) if isinstance(value, (list, tuple))
                    else value) for key, value in values.items()}

    class _Safe(dict):
        def __missing__(self, key: str) -> str:
            return ""

    return message.format_map(_Safe(values)), (fix.format_map(_Safe(values)) if fix else "")


def exit_code_for(state: str) -> int:
    """The exit code of one state — unknown states are an internal error, never 0."""
    return int(STATE_EXIT_CODES.get(str(state), 7))


def state_message(state: str, **fields: Any) -> tuple[str, str]:
    """``(message, fix)`` of one state, its fields filled in (missing ones stay)."""
    template, fix = MESSAGES.get(str(state), ("unclassified pin state {reference}.", ""))
    values: dict[str, Any] = {"reference": "", "digest": "", "remote_digest": "",
                              "new_digest": "", "local_digests": "", "detail": "",
                              "platform": "", "artifact": "", "digest_kind": ""}
    values.update({key: ("" if value is None else value) for key, value in fields.items()})
    values = {key: (", ".join(str(item) for item in value) if isinstance(value, (list, tuple))
                    else value) for key, value in values.items()}

    class _Safe(dict):
        def __missing__(self, key: str) -> str:  # an unfilled field never crashes a message
            return ""

    return template.format_map(_Safe(values)), (fix.format_map(_Safe(values)) if fix else "")


# -- registry error classification ------------------------------------------- #
#: Measured texts (docker 29.6.1, buildx 0.35.0) this classifier was built from::
#:
#:   offline      "failed to do request: Head \"https://registry-1.docker.io/v2/…\":
#:                 proxyconnect tcp: dial tcp 127.0.0.1:1: connect: connection refused"
#:   no DNS       "failed to do request: Head \"https://registry.invalid.example/…\":
#:                 dial tcp: lookup registry.invalid.example on 127.0.0.53:53: no such host"
#:   auth         "pull access denied, repository does not exist or may require
#:                 authorization: server *** insufficient_scope: authorization failed"
#:                "failed to authorize: failed to fetch anonymous token: … 403 Forbidden"
#:   not found    "docker.io/library/busybox:9.99: not found"
#:                "no such manifest: docker.io/library/busybox:9.99" (manifest fallback)
_AUTH_HINTS: tuple[str, ...] = (
    "insufficient_scope",
    "failed to authorize",
    "unauthorized",
    "authentication required",
    "authorization failed",
    "pull access denied",
    "may require authorization",
    "may require 'docker login'",
    "denied: requested access to the resource is denied",
    "403 forbidden",
    "401 unauthorized",
)
_NETWORK_HINTS: tuple[str, ...] = (
    "no such host",
    "connection refused",
    "connection reset",
    "i/o timeout",
    "timeout awaiting response headers",
    "tls handshake timeout",
    "network is unreachable",
    "no route to host",
    "proxyconnect",
    "lookup ",
    "context deadline exceeded",
    "temporary failure in name resolution",
    "server misbehaving",
)
_RATE_LIMIT_HINTS: tuple[str, ...] = (
    "429 too many requests",
    "toomanyrequests",
    "too many requests",
    "pull rate limit",
    "rate limit",
    "rate exceeded",
)
_NOT_FOUND_HINTS: tuple[str, ...] = (
    "no such manifest",
    "manifest unknown",
    "name unknown",
    ": not found",
    "not found: manifest",
    "does not exist",
)


def classify_registry_error(text: str) -> str:
    """Map a docker registry failure text onto a state (never a guess at a pass).

    Order matters and is deliberate: a transport failure is checked first (it names
    no credentials at all), then the auth texts, then "not found" — the measured auth
    text also contains "repository does not exist", so "does not exist" may not win.
    """
    lowered = str(text or "").lower()
    for hint in _NETWORK_HINTS:
        if hint in lowered:
            return STATE_NO_NETWORK
    for hint in _RATE_LIMIT_HINTS:
        if hint in lowered:
            return STATE_RATE_LIMITED
    for hint in _AUTH_HINTS:
        if hint in lowered:
            return STATE_AUTH_REQUIRED
    for hint in _NOT_FOUND_HINTS:
        if hint in lowered:
            return STATE_NOT_FOUND
    return STATE_UNRESOLVED


def classify_daemon_error(text: str) -> str:
    """Map a *local* docker failure onto a state (the daemon half of the classifier).

    Order matters here too: "docker binary not found" and "No such image" both read
    like a "not found" text for a naive substring rule, and they mean two completely
    different things (nothing to inspect vs. nothing installed).
    """
    lowered = str(text or "").lower()
    if "docker binary" in lowered or "no such file or directory" in lowered:
        return STATE_DOCKER_UNREACHABLE
    if "no such image" in lowered or "no such object" in lowered:
        return STATE_NOT_PULLED
    if ("permission denied" in lowered or "cannot connect" in lowered
            or "failed to connect" in lowered):
        return STATE_DOCKER_UNREACHABLE
    if "is the docker daemon running" in lowered or "docker daemon" in lowered:
        return STATE_DOCKER_UNREACHABLE
    if "not found" in lowered:
        return STATE_NOT_PULLED
    state = classify_registry_error(lowered)
    if state in (STATE_NO_NETWORK, STATE_AUTH_REQUIRED):
        return STATE_DOCKER_UNREACHABLE
    return state


# -- references --------------------------------------------------------------- #
def _split_reference(reference: str) -> tuple[str, str, str]:
    """``(registry, path, tag)`` of a docker reference — a conservative split."""
    ref = str(reference or "").strip()
    digest = ""
    if "@" in ref:
        ref, digest = ref.split("@", 1)
    registry = "docker.io"
    remainder = ref
    head, sep, tail = ref.partition("/")
    if sep and ("." in head or ":" in head or head == "localhost"):
        registry, remainder = head, tail
    tag = ""
    name = remainder
    if ":" in remainder.rsplit("/", 1)[-1]:
        name, _, tag = remainder.rpartition(":")
    if registry == "docker.io" and "/" not in name:
        name = f"library/{name}"
    return registry, name, (tag or "latest")


def normalize_reference(reference: str) -> str:
    """``busybox`` → ``docker.io/library/busybox:latest`` (never a registry change).

    Only the docker.io defaults docker itself applies are added: a missing registry
    host, the ``library/`` namespace for a single-component name, and ``latest`` for a
    missing tag. A reference that already carries a digest keeps its tag-less form.
    """
    ref = str(reference or "").strip()
    registry, name, tag = _split_reference(ref)
    if "@" in ref:
        return f"{registry}/{name}"
    return f"{registry}/{name}:{tag}"


def repository_of(reference: str) -> str:
    """``docker.io/library/busybox`` — the immutable-name half of a reference."""
    registry, name, _tag = _split_reference(str(reference or ""))
    return f"{registry}/{name}"


def reference_tag(reference: str) -> str:
    """The tag of a reference (``latest`` when none is written)."""
    return _split_reference(str(reference or ""))[2]


def mirrored_reference(reference: str, registry: str) -> tuple[str, bool]:
    """``(reference on that registry, applied)`` — only a docker.io reference is mirrored.

    A pull-through mirror (measured here: ``mirror.gcr.io``) serves the same content as
    the Hub without the anonymous rate limit. The rewrite is only applied when the
    reference really points at docker.io; for any other registry the original reference
    is returned with ``applied=False`` so the caller can say so instead of quietly
    querying the wrong host.
    """
    host = str(registry or "").strip().rstrip("/")
    if not host:
        return str(reference or "").strip(), False
    registry_name, name, tag = _split_reference(str(reference or ""))
    if registry_name != "docker.io":
        return str(reference or "").strip(), False
    if "@" in str(reference or ""):
        return f"{host}/{name}@{str(reference).split('@', 1)[1]}", True
    return f"{host}/{name}:{tag}", True


def host_platform() -> str:
    """The host's OCI platform string (``linux/amd64``) — what a run here needs."""
    machine = _platform_mod.machine().lower()
    mapping = {
        "x86_64": "amd64",
        "amd64": "amd64",
        "aarch64": "arm64",
        "arm64": "arm64",
        "armv7l": "arm/v7",
        "armv6l": "arm/v6",
        "i386": "386",
        "i686": "386",
    }
    return f"linux/{mapping.get(machine, machine)}"


def is_digest(value: str) -> bool:
    """True iff ``value`` is a pin-shaped digest (``sha256:`` + 64 hex)."""
    return bool(DIGEST_RE.match(str(value or "").strip()))


def artifact_id(reference: str, digest: str) -> str:
    """``docker.io/library/busybox@sha256:…`` — the exact artifact, tag-free."""
    if not is_digest(digest):
        return ""
    return f"{repository_of(reference)}@{str(digest).strip()}"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# -- the process seam --------------------------------------------------------- #
@dataclass(frozen=True)
class CmdOutcome:
    """One finished (or never started) docker invocation."""

    argv: tuple[str, ...]
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    @property
    def detail(self) -> str:
        """The one line a user should see (stderr first, then stdout)."""
        text = (self.stderr or self.stdout or "").strip()
        return text.splitlines()[0][:400] if text else f"exit code {self.exit_code}"

    @property
    def full_text(self) -> str:
        return (self.stderr or self.stdout or "").strip()


class DockerCliRunner:
    """The single place this module starts a process; a missing binary reports.

    ``argv[0] is "docker"`` is normalized onto the resolved binary, exactly like
    :class:`skaldir.docker_runtime.DockerRuntimeManager` does.
    """

    def __init__(self, docker_bin: str | None = None) -> None:
        self.docker_bin = docker_bin or shutil.which("docker") or "docker"

    def run(self, argv: Sequence[str], *, timeout: float = DEFAULT_TIMEOUT) -> CmdOutcome:
        args = list(argv)
        if args and args[0] == "docker":
            args = [self.docker_bin, *args[1:]]
        try:
            proc = subprocess.run(  # nosec B603 — fixed argv, no shell
                args, capture_output=True, text=True, timeout=timeout
            )
        except subprocess.TimeoutExpired:
            return CmdOutcome(tuple(args), -9, "", f"timeout after {timeout:g}s", True)
        except FileNotFoundError as exc:
            return CmdOutcome(tuple(args), 127, "", f"docker binary not found: {exc}")
        except OSError as exc:
            return CmdOutcome(tuple(args), 125, "", f"{type(exc).__name__}: {exc}")
        return CmdOutcome(
            tuple(args),
            proc.returncode,
            (proc.stdout or "").strip(),
            (proc.stderr or "").strip(),
        )


Runner = DockerCliRunner  # the seam's public name (a test injects its own)


# -- resolution --------------------------------------------------------------- #
@dataclass(frozen=True)
class PlatformManifest:
    """One per-platform manifest inside a registry manifest list."""

    digest: str
    platform: str
    media_type: str = ""
    size: int = 0
    kind: str = "image"

    def to_dict(self) -> dict[str, Any]:
        return {
            "digest": self.digest,
            "platform": self.platform,
            "media_type": self.media_type,
            "size": self.size,
            "kind": self.kind,
        }


@dataclass(frozen=True)
class DigestResolution:
    """What a registry (or the daemon's pull record) says a reference resolves to."""

    reference: str
    state: str
    digest: str = ""
    digest_kind: str = ""
    canonical: str = ""
    media_type: str = ""
    size: int = 0
    platform: str = ""
    platform_digest: str = ""
    source: str = ""
    registry_verified: bool = False
    message: str = ""
    fix: str = ""
    raw: str = ""
    details: Mapping[str, Any] = field(default_factory=dict)
    platform_manifests: tuple[PlatformManifest, ...] = ()

    @property
    def ok(self) -> bool:
        return self.state in (STATE_PINNED, STATE_OFFLINE_UNVERIFIED)

    @property
    def exit_code(self) -> int:
        return exit_code_for(self.state)

    @property
    def artifact(self) -> str:
        return artifact_id(self.canonical or self.reference, self.digest)

    def to_dict(self) -> dict[str, Any]:
        return {
            "reference": self.reference,
            "canonical": self.canonical,
            "state": self.state,
            "ok": self.ok,
            "digest": self.digest,
            "digest_kind": self.digest_kind,
            "media_type": self.media_type,
            "size": self.size,
            "platform": self.platform,
            "platform_digest": self.platform_digest,
            "source": self.source,
            "registry_verified": self.registry_verified,
            "artifact": self.artifact,
            "message": self.message,
            "fix": self.fix,
            "details": dict(self.details),
            "platform_manifests": [item.to_dict() for item in self.platform_manifests],
        }


def _refused_resolution(
    reference: str, state: str, *, raw: str = "", detail: str = "", **extra: Any
) -> DigestResolution:
    message, fix = state_message(state, reference=reference, detail=detail or raw)
    details: dict[str, Any] = dict(extra)
    if detail:
        details.setdefault("detail", detail)
    return DigestResolution(
        reference=reference,
        canonical=normalize_reference(reference) if reference else "",
        state=state,
        message=message,
        fix=fix,
        raw=raw,
        details=details,
    )


def _platform_entry(entry: Mapping[str, Any]) -> tuple[str, str]:
    """``(platform string, kind)`` of one manifest entry in an index."""
    raw = entry.get("platform") or {}
    arch = str(raw.get("architecture") or "").strip()
    os_name = str(raw.get("os") or "").strip()
    variant = str(raw.get("variant") or "").strip()
    if not arch or not os_name or arch == "unknown" or os_name == "unknown":
        return "unknown/unknown", "attestation"
    platform = f"{os_name}/{arch}"
    if variant:
        platform = f"{platform}/{variant}"
    return platform, "image"


def _platform_matches(want: str, entry_platform: str) -> bool:
    if not want or not entry_platform or entry_platform == "unknown/unknown":
        return False
    if want == entry_platform:
        return True
    # linux/arm64 matches linux/arm64/v8; linux/amd64 never matches linux/arm64
    return entry_platform.startswith(want + "/") or want.startswith(entry_platform + "/")


def _from_imagetools(payload: Mapping[str, Any], *, reference: str, platform: str,
                     raw: str) -> DigestResolution | None:
    """Parse ``docker buildx imagetools inspect --format {{json .}}``."""
    manifest = payload.get("manifest")
    if not isinstance(manifest, Mapping):
        return None
    digest = str(manifest.get("digest") or "").strip()
    if not is_digest(digest):
        return None
    media_type = str(manifest.get("mediaType") or "")
    entries: list[PlatformManifest] = []
    for item in manifest.get("manifests") or []:
        if not isinstance(item, Mapping):
            continue
        entry_platform, kind = _platform_entry(item)
        entry_digest = str(item.get("digest") or "").strip()
        if not is_digest(entry_digest):
            continue
        entries.append(
            PlatformManifest(
                digest=entry_digest,
                platform=entry_platform,
                media_type=str(item.get("mediaType") or ""),
                size=int(item.get("size") or 0),
                kind=kind,
            )
        )
    index_like = bool(entries) or "index" in media_type or "manifest.list" in media_type
    if entries:
        digest_kind = DIGEST_KIND_INDEX
        platform_digest = next(
            (entry.digest for entry in entries
             if entry.kind == "image" and _platform_matches(platform, entry.platform)),
            "",
        )
    else:
        # a single-platform manifest: the digest IS the platform manifest
        image = payload.get("image") or {}
        digest_kind = DIGEST_KIND_INDEX if index_like else DIGEST_KIND_PLATFORM
        platform_digest = digest
        if not platform and isinstance(image, Mapping):
            arch = str(image.get("architecture") or "")
            os_name = str(image.get("os") or "")
            if arch and os_name:
                platform = f"{os_name}/{arch}"
    message, fix = state_message(
        STATE_PINNED,
        reference=reference,
        digest=digest,
        digest_kind=digest_kind,
        platform=platform,
    )
    return DigestResolution(
        reference=reference,
        canonical=normalize_reference(str(payload.get("name") or reference)),
        state=STATE_PINNED,
        digest=digest,
        digest_kind=digest_kind,
        media_type=media_type,
        size=int(manifest.get("size") or 0),
        platform=platform,
        platform_digest=platform_digest,
        source="registry:imagetools",
        registry_verified=True,
        message=message,
        fix=fix,
        raw=raw,
        details={"manifests_in_index": len(entries), "tool": "docker buildx imagetools"},
        platform_manifests=tuple(entries),
    )


def _from_manifest_inspect(payload: Any, *, reference: str, platform: str,
                           raw: str) -> DigestResolution | None:
    """Parse ``docker manifest inspect --verbose`` (no buildx plugin needed).

    This source never prints the index digest, so the pin is a **platform manifest
    digest** — still an immutable registry digest, and honestly labelled as such.
    """
    if not isinstance(payload, list):
        return None
    entries: list[PlatformManifest] = []
    for item in payload:
        if not isinstance(item, Mapping):
            continue
        descriptor = item.get("Descriptor")
        if not isinstance(descriptor, Mapping):
            continue
        entry_digest = str(descriptor.get("digest") or "").strip()
        if not is_digest(entry_digest):
            continue
        entry_platform, kind = _platform_entry(descriptor)
        entries.append(
            PlatformManifest(
                digest=entry_digest,
                platform=entry_platform,
                media_type=str(descriptor.get("mediaType") or ""),
                size=int(descriptor.get("size") or 0),
                kind=kind,
            )
        )
    chosen = next(
        (entry for entry in entries
         if entry.kind == "image" and _platform_matches(platform, entry.platform)),
        None,
    )
    if chosen is None:
        return None
    message, fix = state_message(
        STATE_PINNED,
        reference=reference,
        digest=chosen.digest,
        digest_kind=DIGEST_KIND_PLATFORM,
        platform=chosen.platform,
    )
    return DigestResolution(
        reference=reference,
        canonical=normalize_reference(reference),
        state=STATE_PINNED,
        digest=chosen.digest,
        digest_kind=DIGEST_KIND_PLATFORM,
        media_type=chosen.media_type,
        size=chosen.size,
        platform=chosen.platform,
        platform_digest=chosen.digest,
        source="registry:manifest-inspect",
        registry_verified=True,
        message=message,
        fix=fix,
        raw=raw,
        details={"manifests_in_index": len(entries), "tool": "docker manifest inspect"},
        platform_manifests=tuple(entries),
    )


def _as_mirror_answer(
    resolution: DigestResolution, reference: str, query: str, warning: str
) -> DigestResolution:
    """Keep the *asked-for* reference as the identity when a mirror answered.

    The digest is the mirror's answer for the same content, but the artifact the user
    named is ``reference``; the query that produced it travels in ``details`` so nothing
    is hidden, and an unapplied ``--registry`` is carried as a warning.
    """
    details = dict(resolution.details)
    if query and query != reference:
        details["resolved_via"] = query
    if warning:
        details["registry_warning"] = warning
    return DigestResolution(
        reference=reference,
        canonical=normalize_reference(reference),
        state=resolution.state,
        digest=resolution.digest,
        digest_kind=resolution.digest_kind,
        media_type=resolution.media_type,
        size=resolution.size,
        platform=resolution.platform,
        platform_digest=resolution.platform_digest,
        source=resolution.source,
        registry_verified=resolution.registry_verified,
        message=resolution.message.replace(normalize_reference(reference), reference)
        if query and query != reference else resolution.message,
        fix=resolution.fix,
        raw=resolution.raw,
        details=details,
        platform_manifests=resolution.platform_manifests,
    )


def resolve_registry_digest(
    reference: str,
    *,
    platform: str = "",
    registry: str = "",
    timeout: float = DEFAULT_TIMEOUT,
    runner: Any = None,
) -> DigestResolution:
    """Resolve the registry manifest digest of ``reference`` (read-only, no pull).

    Three honest outcomes that all look like "it did not work" to a naive caller, and
    are told apart here: the registry is unreachable (3), the registry refused the
    request (4), the registry has no such manifest (5). A missing buildx plugin is
    *not* a failure — the manifest fallback answers instead.

    ``registry`` asks a different host (a pull-through mirror) for the *same* Hub
    reference; the answer still belongs to ``reference`` — the pin keeps the local name
    and records ``details["resolved_via"]``. A reference that is not on docker.io is
    never re-pointed: the caller gets the plain answer plus a warning.
    """
    target = str(reference or "").strip()
    if not target or "@" in target:
        # a digest reference has no tag to resolve; the digest IS the identity
        if "@" in target:
            digest = target.split("@", 1)[1].strip()
            if is_digest(digest):
                message, fix = state_message(
                    STATE_PINNED, reference=target, digest=digest,
                    digest_kind=DIGEST_KIND_PLATFORM, platform=platform or host_platform(),
                )
                return DigestResolution(
                    reference=target, canonical=target, state=STATE_PINNED, digest=digest,
                    digest_kind=DIGEST_KIND_PLATFORM, platform=platform or host_platform(),
                    source="reference:digest", registry_verified=False, message=message,
                    fix=fix, raw=target,
                    details={"note": "the reference already carries a digest — nothing was "
                                     "resolved from a tag, and the registry was not asked"},
                )
        return _refused_resolution(
            target, STATE_UNRESOLVED,
            detail="empty image reference" if not target else "not a pin-shaped digest",
        )
    tool = runner or DockerCliRunner()
    plat = platform or host_platform()
    query, mirrored = mirrored_reference(target, registry)
    warning = ""
    if registry and not mirrored:
        warning = (
            f"--registry {registry} was not applied: {target} does not point at "
            f"docker.io, so the named registry was queried instead"
        )
    first = tool.run(
        ["docker", "buildx", "imagetools", "inspect", "--format", "{{json .}}", query],
        timeout=timeout,
    )
    if first.exit_code == 0:
        try:
            payload = json.loads(first.stdout)
        except json.JSONDecodeError as exc:
            payload = None
            json_problem = f"unparsable JSON: {exc}"
        else:
            resolved = _from_imagetools(payload, reference=target, platform=plat,
                                        raw=first.stdout)
            if resolved is not None:
                return _as_mirror_answer(resolved, target, query, warning)
            json_problem = "no manifest digest in the answer"
    else:
        json_problem = ""
    # Fallback: no buildx plugin, or an answer this build cannot read.
    fallback = tool.run(["docker", "manifest", "inspect", "--verbose", query], timeout=timeout)
    if fallback.exit_code == 0:
        try:
            fallback_payload = json.loads(fallback.stdout)
        except json.JSONDecodeError:
            fallback_payload = None
        if fallback_payload is not None:
            resolved = _from_manifest_inspect(fallback_payload, reference=target,
                                              platform=plat, raw=fallback.stdout)
            if resolved is not None:
                return _as_mirror_answer(resolved, target, query, warning)
    # Now decide what to tell the user. The imagetools stderr is the primary text; the
    # fallback's text is used only when the primary one says nothing useful.
    text = ""
    if first.exit_code != 0:
        text = first.full_text
    if not text or classify_registry_error(text) == STATE_UNRESOLVED:
        text = fallback.full_text or text
    if not text:
        text = json_problem or f"exit code {first.exit_code}"
    state = classify_registry_error(text)
    return _refused_resolution(target, state, raw=text, detail=first.detail)


# -- local image --------------------------------------------------------------- #
@dataclass(frozen=True)
class LocalImage:
    """What the local daemon knows about a reference (no network involved)."""

    reference: str
    state: str
    exists: bool = False
    image_id: str = ""
    repo_digests: tuple[str, ...] = ()
    detail: str = ""
    raw: str = ""

    @property
    def digests(self) -> tuple[str, ...]:
        """Every digest the daemon associates with this image (id + repo digests)."""
        found: list[str] = []
        if is_digest(self.image_id):
            found.append(self.image_id)
        for item in self.repo_digests:
            value = str(item).split("@", 1)[-1].strip()
            if is_digest(value) and value not in found:
                found.append(value)
        return tuple(found)

    def carries(self, digest: str) -> bool:
        """True iff this local image really carries ``digest``."""
        return bool(is_digest(digest)) and digest in self.digests

    @property
    def exit_code(self) -> int:
        return exit_code_for(self.state)

    def to_dict(self) -> dict[str, Any]:
        return {
            "reference": self.reference,
            "state": self.state,
            "exists": self.exists,
            "image_id": self.image_id,
            "repo_digests": list(self.repo_digests),
            "digests": list(self.digests),
            "detail": self.detail,
        }


def local_image_state(
    reference: str, *, timeout: float = 30.0, runner: Any = None
) -> LocalImage:
    """Inspect the local image — ``exists`` is the daemon's answer, never assumed."""
    target = str(reference or "").strip()
    tool = runner or DockerCliRunner()
    outcome = tool.run(
        ["docker", "image", "inspect", "--format", "{{.Id}} {{json .RepoDigests}}", target],
        timeout=timeout,
    )
    if outcome.exit_code != 0:
        state = classify_daemon_error(outcome.full_text)
        detail = outcome.detail
        if state == STATE_NOT_PULLED:
            detail = f"the daemon has no image for {target}"
        return LocalImage(reference=target, state=state, exists=False, detail=detail,
                          raw=outcome.full_text)
    text = outcome.stdout.strip()
    image_id, _, repo_json = text.partition(" ")
    try:
        parsed = json.loads(repo_json) if repo_json.strip() else []
    except json.JSONDecodeError:
        parsed = []
    repo_digests = tuple(str(item) for item in parsed) if isinstance(parsed, list) else ()
    if not is_digest(image_id):
        return LocalImage(
            reference=target, state=STATE_UNRESOLVED, exists=True, detail=(
                f"the daemon answered without a usable image id: {text[:120]}"
            ), raw=text,
        )
    return LocalImage(reference=target, state=STATE_PINNED, exists=True,
                      image_id=image_id.strip(), repo_digests=repo_digests, raw=text)


def local_repo_digest(reference: str, *, timeout: float = 30.0, runner: Any = None) -> str:
    """The digest the daemon recorded for a *pulled* image (``""`` when unknown)."""
    local = local_image_state(reference, timeout=timeout, runner=runner)
    if not local.exists or not local.repo_digests:
        return ""
    return str(local.repo_digests[0]).split("@", 1)[-1].strip()


# -- the pin ------------------------------------------------------------------- #
@dataclass(frozen=True)
class VersionBinding:
    """One ``(capability, version)`` registered against a specific digest.

    This is what makes "the same version number cannot silently change" a checkable
    fact rather than a promise: a rollback to ``version`` re-verifies ``digest``.
    """

    capability: str = ""
    version: str = ""
    digest: str = ""
    bound_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability": self.capability,
            "version": self.version,
            "digest": self.digest,
            "bound_at": self.bound_at,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> VersionBinding:
        return cls(
            capability=str(payload.get("capability") or ""),
            version=str(payload.get("version") or ""),
            digest=str(payload.get("digest") or ""),
            bound_at=str(payload.get("bound_at") or ""),
        )


@dataclass(frozen=True)
class ImagePin:
    """One recorded artifact: a reference bound to a registry manifest digest."""

    reference: str
    digest: str
    digest_kind: str = DIGEST_KIND_INDEX
    canonical: str = ""
    repository: str = ""
    media_type: str = ""
    size: int = 0
    platform: str = ""
    platform_digest: str = ""
    source: str = ""
    registry_verified: bool = False
    recorded_at: str = ""
    local_verified: bool = False
    capabilities: tuple[str, ...] = ()
    bindings: tuple[VersionBinding, ...] = ()
    note: str = ""

    @property
    def artifact(self) -> str:
        """``docker.io/library/busybox@sha256:…`` — the exact artifact of this pin."""
        return artifact_id(self.repository or self.canonical or self.reference, self.digest)

    @property
    def tag(self) -> str:
        return reference_tag(self.reference)

    def carries_local(self, local: LocalImage) -> bool:
        """True iff ``local`` is the artifact this pin names.

        A multi-arch reference has two digests (index + platform manifest) and the
        daemon records one of them depending on its image store; both name the same
        pulled bytes, so both are accepted. Any other digest is a mismatch.
        """
        if local.carries(self.digest):
            return True
        if self.digest_kind == DIGEST_KIND_INDEX and is_digest(self.platform_digest):
            return local.carries(self.platform_digest)
        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "reference": self.reference,
            "canonical": self.canonical,
            "repository": self.repository,
            "tag": self.tag,
            "digest": self.digest,
            "digest_kind": self.digest_kind,
            "artifact": self.artifact,
            "media_type": self.media_type,
            "size": self.size,
            "platform": self.platform,
            "platform_digest": self.platform_digest,
            "source": self.source,
            "registry_verified": self.registry_verified,
            "recorded_at": self.recorded_at,
            "local_verified": self.local_verified,
            "capabilities": list(self.capabilities),
            "bindings": [binding.to_dict() for binding in self.bindings],
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ImagePin:
        """Rebuild a pin, refusing a malformed one instead of guessing at it."""
        digest = str(payload.get("digest") or "").strip()
        if not is_digest(digest):
            raise PinStoreError(
                f"a stored pin carries no pin-shaped digest: {digest!r}"
            )
        reference = str(payload.get("reference") or "").strip()
        if not reference:
            raise PinStoreError("a stored pin carries no reference")
        return cls(
            reference=reference,
            digest=digest,
            digest_kind=str(payload.get("digest_kind") or DIGEST_KIND_INDEX),
            canonical=str(payload.get("canonical") or ""),
            repository=str(payload.get("repository") or "") or repository_of(reference),
            media_type=str(payload.get("media_type") or ""),
            size=int(payload.get("size") or 0),
            platform=str(payload.get("platform") or ""),
            platform_digest=str(payload.get("platform_digest") or ""),
            source=str(payload.get("source") or ""),
            registry_verified=bool(payload.get("registry_verified", False)),
            recorded_at=str(payload.get("recorded_at") or ""),
            local_verified=bool(payload.get("local_verified", False)),
            capabilities=tuple(str(item) for item in payload.get("capabilities") or ()),
            bindings=tuple(
                VersionBinding.from_dict(item)
                for item in payload.get("bindings") or ()
                if isinstance(item, Mapping)
            ),
            note=str(payload.get("note") or ""),
        )

    def bind(self, capability: str) -> ImagePin:
        """A copy of this pin with ``capability`` recorded (idempotent)."""
        name = str(capability or "").strip()
        if not name or name in self.capabilities:
            return self
        return ImagePin(
            reference=self.reference, digest=self.digest, digest_kind=self.digest_kind,
            canonical=self.canonical, repository=self.repository,
            media_type=self.media_type, size=self.size, platform=self.platform,
            platform_digest=self.platform_digest, source=self.source,
            registry_verified=self.registry_verified, recorded_at=self.recorded_at,
            local_verified=self.local_verified,
            capabilities=tuple([*self.capabilities, name]), bindings=self.bindings,
            note=self.note,
        )

    def bind_version(self, capability: str, version: str) -> ImagePin:
        """A copy of this pin with ``(capability, version)`` bound to ``digest``.

        Re-binding the same version to a *different* digest is refused by the store
        (``ImagePin`` is immutable here; :meth:`PinStore.bind_version` is the door),
        so a version number cannot silently change its artifact.
        """
        name = str(capability or "").strip()
        label = str(version or "").strip()
        if not label:
            return self
        existing = self.binding_for(name, label)
        if existing is not None and existing.digest == self.digest:
            return self
        bindings = tuple(
            binding for binding in self.bindings
            if not (binding.capability == name and binding.version == label)
        )
        return ImagePin(
            reference=self.reference, digest=self.digest, digest_kind=self.digest_kind,
            canonical=self.canonical, repository=self.repository,
            media_type=self.media_type, size=self.size, platform=self.platform,
            platform_digest=self.platform_digest, source=self.source,
            registry_verified=self.registry_verified, recorded_at=self.recorded_at,
            local_verified=self.local_verified, capabilities=self.capabilities,
            bindings=tuple([*bindings,
                            VersionBinding(capability=name, version=label,
                                           digest=self.digest, bound_at=_now_iso())]),
            note=self.note,
        )

    def binding_for(self, capability: str, version: str) -> VersionBinding | None:
        """The recorded binding of ``(capability, version)`` — ``None`` when unknown."""
        name = str(capability or "").strip()
        label = str(version or "").strip()
        for binding in self.bindings:
            if binding.version == label and (not name or binding.capability == name):
                return binding
        return None

    def evidence(self, *, capability: str = "", kind: str = "verification",
                 note: str = "") -> dict[str, Any]:
        """The evidence record that names this exact artifact (registration input)."""
        payload = {
            "capability": str(capability or ""),
            "evidence_kind": str(kind or "verification"),
            "artifact": self.artifact,
            "image_reference": self.canonical or self.reference,
            "image_digest": self.digest,
            "digest_kind": self.digest_kind,
            "platform": self.platform,
            "source": self.source,
            "registry_verified": bool(self.registry_verified),
            "local_verified": bool(self.local_verified),
            "recorded_at": self.recorded_at,
            "versions": [binding.to_dict() for binding in self.bindings],
            "pinned": True,
            "reason": "",
        }
        payload["note"] = str(note) or (
            f"{self.artifact} is bound to this capability"
            if capability else f"{self.artifact} is pinned"
        )
        return payload


def pin_from_resolution(
    resolution: DigestResolution, *, local: LocalImage | None = None, capability: str = "",
    note: str = "",
) -> ImagePin:
    """Build the pin a resolution describes (the only constructor of a real pin)."""
    registry_verified = bool(resolution.registry_verified)
    capabilities = tuple([capability]) if str(capability or "").strip() else ()
    return ImagePin(
        reference=resolution.reference,
        digest=resolution.digest,
        digest_kind=resolution.digest_kind or DIGEST_KIND_INDEX,
        canonical=resolution.canonical,
        repository=repository_of(resolution.canonical or resolution.reference),
        media_type=resolution.media_type,
        size=resolution.size,
        platform=resolution.platform,
        platform_digest=resolution.platform_digest,
        source=resolution.source,
        registry_verified=registry_verified,
        recorded_at=_now_iso(),
        local_verified=bool(
            local is not None and local.exists
            and (local.carries(resolution.digest)
                 or (resolution.digest_kind == DIGEST_KIND_INDEX
                     and local.carries(resolution.platform_digest)))
        ),
        capabilities=capabilities,
        note=str(note or ""),
    )


# -- the store ----------------------------------------------------------------- #
class PinStoreError(RuntimeError):
    """The pin store itself is broken — never silently ignored."""


class PinConflict(RuntimeError):
    """A different digest is already pinned for this reference (decision required)."""

    def __init__(self, reference: str, digest: str, new_digest: str) -> None:
        self.reference = reference
        self.digest = digest
        self.new_digest = new_digest
        super().__init__(
            f"a pin for {reference} already records {digest}; refusing to overwrite it "
            f"with {new_digest}"
        )


class PinStore:
    """The recorded pins — one JSON file, written atomically, never silently reset."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    @classmethod
    def default(cls, state: str | Path | None = None) -> PinStore:
        """``<state>/image-pins.json`` where ``<state>`` defaults to the runtime dir."""
        if state:
            base = Path(state)
        else:
            from .registry import default_runtime_dir

            base = Path(default_runtime_dir())
        return cls(base / PIN_FILENAME)

    def exists(self) -> bool:
        return self.path.is_file()

    def load(self) -> dict[str, ImagePin]:
        """Every pin by reference; a missing file is empty, a corrupt one is an error."""
        if not self.path.is_file():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise PinStoreError(f"the pin store {self.path} is not valid JSON: {exc}") from exc
        except OSError as exc:
            raise PinStoreError(f"the pin store {self.path} cannot be read: {exc}") from exc
        if not isinstance(payload, Mapping):
            raise PinStoreError(f"the pin store {self.path} is not a JSON object")
        raw_pins = payload.get("pins", payload)
        if not isinstance(raw_pins, Mapping):
            raise PinStoreError(f"the pin store {self.path} has no pin mapping")
        pins: dict[str, ImagePin] = {}
        for key, item in raw_pins.items():
            if not isinstance(item, Mapping):
                raise PinStoreError(f"the pin store {self.path} has a non-object pin at {key!r}")
            pin = ImagePin.from_dict({**dict(item), "reference": item.get("reference", key)})
            pins[pin.reference] = pin
        return pins

    def get(self, reference: str) -> ImagePin | None:
        """The pin for ``reference`` — exact, then canonical, then tag-normalized."""
        target = str(reference or "").strip()
        pins = self.load()
        for key in (target, normalize_reference(target), normalize_reference(target).lower()):
            if key and key in pins:
                return pins[key]
        wanted = repository_of(target)
        for pin in pins.values():
            if repository_of(pin.reference) == wanted:
                return pin
        return None

    def all(self) -> list[ImagePin]:
        return sorted(self.load().values(), key=lambda pin: pin.reference)

    def record(self, pin: ImagePin, *, replace: bool = False) -> ImagePin:
        """Write ``pin``; a different digest for the same reference needs ``replace``."""
        if not is_digest(pin.digest):
            raise PinStoreError(f"refusing to record a non-digest pin: {pin.digest!r}")
        pins = self.load()
        existing = self.get(pin.reference)
        if existing is not None and existing.digest != pin.digest and not replace:
            raise PinConflict(pin.reference, existing.digest, pin.digest)
        merged = pin
        if existing is not None:
            capabilities = tuple(dict.fromkeys([*existing.capabilities, *pin.capabilities]))
            merged = ImagePin(
                reference=pin.reference, digest=pin.digest, digest_kind=pin.digest_kind,
                canonical=pin.canonical or existing.canonical,
                repository=pin.repository or existing.repository,
                media_type=pin.media_type or existing.media_type,
                size=pin.size or existing.size, platform=pin.platform or existing.platform,
                platform_digest=pin.platform_digest or existing.platform_digest,
                source=pin.source or existing.source,
                registry_verified=pin.registry_verified or existing.registry_verified,
                recorded_at=pin.recorded_at or existing.recorded_at,
                local_verified=pin.local_verified, capabilities=capabilities,
                bindings=pin.bindings or existing.bindings,
                note=pin.note or existing.note,
            )
        pins[merged.reference] = merged
        self._write(pins)
        return merged

    def bind_version(
        self, reference: str, capability: str, version: str, *, replace: bool = False
    ) -> ImagePin:
        """Bind ``(capability, version)`` to the pinned digest — or refuse.

        This is the "same version number, same bytes" rule. An existing binding of the
        same version to a different digest raises :class:`PinConflict` unless
        ``replace`` says the change was deliberate.
        """
        pin = self.get(reference)
        if pin is None:
            raise PinStoreError(
                f"no pin is recorded for {reference} — bind the version after pinning"
            )
        label = str(version or "").strip()
        existing = pin.binding_for(capability, label)
        if existing is not None and existing.digest != pin.digest and not replace:
            raise PinConflict(pin.reference, existing.digest, pin.digest)
        updated = pin.bind(capability).bind_version(capability, label)
        return self.record(updated, replace=True)

    def remove(self, reference: str) -> bool:
        """Drop every pin whose repository is the asked-for one (like :meth:`get`)."""
        pins = self.load()
        target = str(reference or "").strip()
        wanted = repository_of(target) if target else ""
        removed = False
        for key in list(pins):
            if key == target or (wanted and repository_of(key) == wanted):
                del pins[key]
                removed = True
        if removed:
            self._write(pins)
        return removed

    def _write(self, pins: Mapping[str, ImagePin]) -> None:
        payload = {
            "schema": PIN_SCHEMA,
            "updated_at": _now_iso(),
            "pins": {key: pin.to_dict() for key, pin in sorted(pins.items())},
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=str(self.path.parent),
            prefix=self.path.name + ".", suffix=".tmp", delete=False,
        )
        try:
            with handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
            os.replace(handle.name, self.path)
        except BaseException:
            Path(handle.name).unlink(missing_ok=True)
            raise


# -- verification -------------------------------------------------------------- #
@dataclass(frozen=True)
class VerifyResult:
    """The verdict of one verification: state, exit code, message, evidence."""

    reference: str
    state: str
    message: str
    fix: str = ""
    pin: ImagePin | None = None
    local: LocalImage | None = None
    remote: DigestResolution | None = None
    details: Mapping[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    next_actions: tuple[tuple[str, str], ...] = ()

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    @property
    def exit_code(self) -> int:
        return exit_code_for(self.state)

    @property
    def artifact(self) -> str:
        return self.pin.artifact if self.pin is not None else ""

    def evidence(self) -> dict[str, Any]:
        """The evidence record of this verdict (always names what was compared)."""
        payload = {
            "artifact": self.artifact,
            "image_reference": (self.pin.canonical or self.pin.reference) if self.pin else "",
            "image_digest": self.pin.digest if self.pin else "",
            "digest_kind": self.pin.digest_kind if self.pin else "",
            "state": self.state,
            "ok": self.ok,
            "registry_verified": bool(self.pin.registry_verified) if self.pin else False,
            "local_digests": list(self.local.digests) if self.local else [],
            "remote_digest": self.remote.digest if self.remote else "",
        }
        payload["note"] = self.message
        return payload

    def to_dict(self) -> dict[str, Any]:
        return {
            "reference": self.reference,
            "state": self.state,
            "ok": self.ok,
            "exit_code": self.exit_code,
            "artifact": self.artifact,
            "message": self.message,
            "fix": self.fix,
            "pin": self.pin.to_dict() if self.pin is not None else None,
            "local": self.local.to_dict() if self.local is not None else None,
            "remote": self.remote.to_dict() if self.remote is not None else None,
            "details": dict(self.details),
            "warnings": list(self.warnings),
            "next_actions": [
                {"command": command, "reason": reason} for command, reason in self.next_actions
            ],
        }


def verify_pin(
    store: PinStore,
    reference: str,
    *,
    remote: bool = False,
    timeout: float = DEFAULT_TIMEOUT,
    runner: Any = None,
    capability: str = "",
    registry: str = "",
) -> VerifyResult:
    """Verify a recorded pin: local first, then (``remote``) the registry.

    Every branch returns its own state and message. A digest that no longer matches is
    a **conflict**: exit 6, and the caller must not run the capability.
    """
    target = str(reference or "").strip()
    pin = store.get(target)
    if pin is None:
        message, fix = state_message(STATE_UNPINNED, reference=target)
        return VerifyResult(
            reference=target, state=STATE_UNPINNED, message=message, fix=fix,
            next_actions=((f"skaldir image pin {target}", "record the registry digest first"),),
            details={"pin_store": str(store.path), "capability": str(capability or "")},
        )
    if capability and pin.capabilities and str(capability) not in pin.capabilities:
        message = (
            f"REFUSED: the pin for {pin.reference} is bound to "
            f"{', '.join(pin.capabilities)}, not to {capability}."
        )
        return VerifyResult(
            reference=target, state=STATE_PIN_CONFLICT, message=message,
            fix=f"bind it deliberately: skaldir image pin {target} "
                f"--capability {capability}",
            pin=pin, details={"bound_to": list(pin.capabilities), "capability": str(capability)},
        )
    local = local_image_state(target, timeout=timeout, runner=runner)
    if local.state in (STATE_DOCKER_UNREACHABLE, STATE_NOT_FOUND, STATE_UNRESOLVED) \
            and not local.exists:
        message, fix = state_message(STATE_DOCKER_UNREACHABLE, reference=target,
                                     detail=local.detail)
        if local.state != STATE_DOCKER_UNREACHABLE:
            message, fix = state_message(STATE_UNRESOLVED, reference=target, detail=local.detail)
        return VerifyResult(reference=target, state=local.state, message=message, fix=fix,
                            pin=pin, local=local, details={"pin_store": str(store.path)})
    if not local.exists:
        message, fix = state_message(STATE_NOT_PULLED, reference=target, digest=pin.digest)
        return VerifyResult(
            reference=target, state=STATE_NOT_PULLED, message=message, fix=fix, pin=pin,
            local=local, details={"pin_store": str(store.path)},
            next_actions=((f"docker pull {target}", "make the artifact available locally"),),
        )
    if not pin.carries_local(local):
        message, fix = state_message(
            STATE_MISMATCH, reference=target, digest=pin.digest,
            local_digests=list(local.digests), artifact=pin.artifact,
        )
        return VerifyResult(
            reference=target, state=STATE_MISMATCH, message=message, fix=fix, pin=pin,
            local=local,
            details={"pinned": pin.digest, "pinned_kind": pin.digest_kind,
                     "local_digests": list(local.digests), "pin_store": str(store.path)},
            warnings=("a capability must not run on an artifact that no longer matches "
                      "its pin",),
            next_actions=((f"skaldir image pin {target} --force",
                           "re-pin deliberately once the change is understood"),),
        )
    warnings: list[str] = []
    if not pin.registry_verified:
        warnings.append(
            f"the pin of {pin.reference} was not verified against the registry "
            f"(source: {pin.source or 'unknown'})"
        )
    remote_result: DigestResolution | None = None
    if remote:
        remote_result = resolve_registry_digest(target, platform=pin.platform, timeout=timeout,
                                                runner=runner, registry=registry)
        if not remote_result.ok:
            return VerifyResult(
                reference=target, state=remote_result.state, message=remote_result.message,
                fix=remote_result.fix, pin=pin, local=local, remote=remote_result,
                details={"pin_store": str(store.path), "local_match": True},
                warnings=tuple(warnings),
            )
        if remote_result.digest != pin.digest:
            message, fix = state_message(
                STATE_TAG_MOVED, reference=target, digest=pin.digest,
                remote_digest=remote_result.digest, artifact=pin.artifact,
            )
            return VerifyResult(
                reference=target, state=STATE_TAG_MOVED, message=message, fix=fix, pin=pin,
                local=local, remote=remote_result,
                details={"pinned": pin.digest, "remote": remote_result.digest,
                         "local_match": True, "pin_store": str(store.path)},
                warnings=tuple(warnings) + (
                    "the local image still matches the pin; only the tag moved",
                ),
                next_actions=((f"skaldir image pin {target} --force",
                               "move the pin to the digest the tag now resolves to"),),
            )
    message, fix = state_message(
        STATE_PINNED, reference=target, digest=pin.digest, digest_kind=pin.digest_kind,
        platform=pin.platform,
    )
    return VerifyResult(
        reference=target, state=STATE_PINNED, message=message, fix=fix, pin=pin, local=local,
        remote=remote_result,
        details={"local_digests": list(local.digests), "pin_store": str(store.path),
                 "capability": str(capability or "")},
        warnings=tuple(warnings),
    )


# -- the digest guard: health, invocation, rollback ---------------------------- #
@dataclass(frozen=True)
class DigestGuard:
    """The fail-closed verdict of re-verifying a recorded digest at one surface.

    ``ok`` is true only for :data:`STATE_PINNED`; every other state is a refusal whose
    ``exit_code`` is the surface's own code (:data:`GUARD_EXIT_CODES`). The evidence
    always names the digest that was compared — a guard that cannot say which bytes it
    saw is not a guard.
    """

    surface: str
    reference: str
    capability: str = ""
    version: str = ""
    state: str = STATE_PINNED
    message: str = ""
    fix: str = ""
    pin: ImagePin | None = None
    local: LocalImage | None = None
    remote: DigestResolution | None = None
    details: Mapping[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.state == STATE_PINNED

    @property
    def exit_code(self) -> int:
        return 0 if self.ok else guard_exit_code(self.surface)

    @property
    def label(self) -> str:
        return GUARD_LABELS.get(self.surface, "operation")

    @property
    def artifact(self) -> str:
        return self.pin.artifact if self.pin is not None else ""

    def evidence(self) -> dict[str, Any]:
        """The evidence record of this guard — it always carries the digest."""
        return {
            "surface": self.surface,
            "capability": self.capability,
            "version": self.version,
            "artifact": self.artifact,
            "image_reference": (self.pin.canonical or self.pin.reference) if self.pin else "",
            "image_digest": self.pin.digest if self.pin else "",
            "digest_kind": self.pin.digest_kind if self.pin else "",
            "platform": self.pin.platform if self.pin else "",
            "registry_verified": bool(self.pin.registry_verified) if self.pin else False,
            "recorded_at": self.pin.recorded_at if self.pin else "",
            "local_digests": list(self.local.digests) if self.local else [],
            "remote_digest": self.remote.digest if self.remote else "",
            "state": self.state,
            "ok": self.ok,
            "exit_code": self.exit_code,
            "same_version_same_bytes": bool(self.ok),
            "evidence_kind": "verification",
            "note": self.message,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "surface": self.surface,
            "label": self.label,
            "reference": self.reference,
            "capability": self.capability,
            "version": self.version,
            "state": self.state,
            "ok": self.ok,
            "exit_code": self.exit_code,
            "artifact": self.artifact,
            "image_digest": self.pin.digest if self.pin else "",
            "message": self.message,
            "fix": self.fix,
            "details": dict(self.details),
            "warnings": list(self.warnings),
            "local_digests": list(self.local.digests) if self.local else [],
            "evidence": self.evidence(),
        }


def _guard_refusal(
    surface: str,
    reference: str,
    capability: str,
    version: str,
    state: str,
    *,
    pin: ImagePin | None = None,
    local: LocalImage | None = None,
    remote: DigestResolution | None = None,
    digest: str = "",
    new_digest: str = "",
    local_digests: Sequence[str] = (),
    detail: str = "",
    warnings: Sequence[str] = (),
    extra: Mapping[str, Any] | None = None,
) -> DigestGuard:
    """One refusal record — the message comes from the surface+state table."""
    artifact = pin.artifact if pin is not None else ""
    message, fix = guard_message(
        surface, state,
        capability=capability or "the capability",
        reference=reference,
        digest=digest or (pin.digest if pin is not None else ""),
        new_digest=new_digest,
        remote_digest=(remote.digest if remote is not None else ""),
        local_digests=list(local_digests or (local.digests if local is not None else ())),
        platform=(pin.platform if pin is not None else ""),
        artifact=artifact,
        detail=detail,
        version=version,
    )
    details: dict[str, Any] = {
        "surface": surface,
        "capability": capability,
        "version": version,
        "recorded_digest": pin.digest if pin is not None else "",
        "artifact": artifact,
        "local_digests": list(local.digests) if local is not None else list(local_digests),
    }
    if extra:
        details.update(dict(extra))
    return DigestGuard(
        surface=surface, reference=reference, capability=capability, version=version,
        state=state, message=message, fix=fix, pin=pin, local=local, remote=remote,
        details=details, warnings=tuple(warnings),
    )


def guard_digest(
    store: PinStore,
    reference: str,
    *,
    surface: str,
    capability: str = "",
    version: str = "",
    remote: bool = False,
    timeout: float = DEFAULT_TIMEOUT,
    runner: Any = None,
    registry: str = "",
) -> DigestGuard:
    """Re-verify a recorded digest at ``surface`` (health / invoke / rollback).

    Called at *every* health check, *every* invocation and *before* a rollback. It
    fails closed: a missing pin, a missing local image, an unreachable daemon, a moved
    tag or a digest that no longer matches all return ``ok=False`` with the surface's
    exit code — never a zero, and never a silent "probably fine".
    """
    surface_name = str(surface or "").strip()
    target = str(reference or "").strip()
    name = str(capability or "").strip()
    label_version = str(version or "").strip()
    if surface_name not in GUARD_SURFACES:
        return DigestGuard(
            surface=surface_name, reference=target, capability=name, version=label_version,
            state=STATE_UNRESOLVED,
            message=(
                f"unknown digest-guard surface {surface_name!r} — expected one of "
                f"{', '.join(GUARD_SURFACES)}."
            ),
            fix="call the guard with one of: " + ", ".join(GUARD_SURFACES),
            details={"surfaces": list(GUARD_SURFACES)},
        )
    pin = store.get(target)
    if pin is None:
        return _guard_refusal(surface_name, target, name, label_version, STATE_UNPINNED,
                              extra={"pin_store": str(store.path)})
    if label_version:
        binding = pin.binding_for(name, label_version)
        if binding is not None and binding.digest != pin.digest:
            # the same version number would resolve to different bytes: refuse
            return _guard_refusal(
                surface_name, target, name, label_version, STATE_PIN_CONFLICT, pin=pin,
                digest=binding.digest, new_digest=pin.digest,
                extra={"bound_at": binding.bound_at, "pin_store": str(store.path)},
                warnings=("the version binding predates the current pin — a rollback "
                          "must not resolve the version differently than it was "
                          "registered",),
            )
    local = local_image_state(target, timeout=min(timeout, 30.0), runner=runner)
    if not local.exists:
        state = STATE_DOCKER_UNREACHABLE if local.state == STATE_DOCKER_UNREACHABLE \
            else STATE_NOT_PULLED
        return _guard_refusal(surface_name, target, name, label_version, state, pin=pin,
                              local=local, detail=local.detail,
                              extra={"pin_store": str(store.path)})
    if not pin.carries_local(local):
        return _guard_refusal(
            surface_name, target, name, label_version, STATE_MISMATCH, pin=pin, local=local,
            digest=pin.digest, extra={"pin_store": str(store.path)},
            warnings=("the artifact behind this capability changed without a new "
                      "version — refusing to treat it as the registered one",),
        )
    remote_result: DigestResolution | None = None
    if remote:
        remote_result = resolve_registry_digest(target, platform=pin.platform,
                                                timeout=timeout, runner=runner,
                                                registry=registry)
        if not remote_result.ok:
            return _guard_refusal(surface_name, target, name, label_version,
                                  remote_result.state, pin=pin, local=local,
                                  remote=remote_result, detail=remote_result.message)
        if remote_result.digest != pin.digest:
            return _guard_refusal(surface_name, target, name, label_version, STATE_TAG_MOVED,
                                  pin=pin, local=local, remote=remote_result,
                                  digest=pin.digest)
    warnings: list[str] = []
    if not pin.registry_verified:
        warnings.append(
            f"the recorded digest of {pin.reference} was never verified against the "
            f"registry (source: {pin.source or 'unknown'})"
        )
    message = (
        f"the {GUARD_LABELS[surface_name]} passed: {target} still carries the recorded "
        f"digest {pin.digest} ({pin.digest_kind})."
    )
    return DigestGuard(
        surface=surface_name, reference=target, capability=name, version=label_version,
        state=STATE_PINNED, message=message, pin=pin, local=local, remote=remote_result,
        details={
            "surface": surface_name, "capability": name, "version": label_version,
            "recorded_digest": pin.digest, "artifact": pin.artifact,
            "local_digests": list(local.digests), "pin_store": str(store.path),
        },
        warnings=tuple(warnings),
    )


def guard_health(store: PinStore, reference: str, *, capability: str = "", version: str = "",
                 remote: bool = False, timeout: float = DEFAULT_TIMEOUT,
                 runner: Any = None, registry: str = "") -> DigestGuard:
    """The digest re-verification a capability HEALTH check must run (exit 2 on failure)."""
    return guard_digest(store, reference, surface=GUARD_HEALTH, capability=capability,
                        version=version, remote=remote, timeout=timeout, runner=runner,
                        registry=registry)


def guard_invoke(store: PinStore, reference: str, *, capability: str = "", version: str = "",
                 remote: bool = False, timeout: float = DEFAULT_TIMEOUT,
                 runner: Any = None, registry: str = "") -> DigestGuard:
    """The digest re-verification EVERY invocation must run (exit 6 on failure)."""
    return guard_digest(store, reference, surface=GUARD_INVOKE, capability=capability,
                        version=version, remote=remote, timeout=timeout, runner=runner,
                        registry=registry)


def guard_rollback(store: PinStore, reference: str, *, capability: str = "", version: str = "",
                   remote: bool = False, timeout: float = DEFAULT_TIMEOUT,
                   runner: Any = None, registry: str = "") -> DigestGuard:
    """The digest re-verification a ROLLBACK to ``version`` must run (exit 3 on failure)."""
    return guard_digest(store, reference, surface=GUARD_ROLLBACK, capability=capability,
                        version=version, remote=remote, timeout=timeout, runner=runner,
                        registry=registry)


def offline_digest(
    reference: str, *, platform: str = "", timeout: float = DEFAULT_TIMEOUT, runner: Any = None
) -> DigestResolution:
    """The digest the daemon recorded for a *pulled* image — never registry-verified.

    This is the honest offline path: it reads ``.RepoDigests``, which the daemon wrote
    when the image was pulled, and labels the result ``offline_unverified`` with a
    warning. It never pretends to be a registry answer, and it refuses when the image
    was pulled by a different reference (a locally built image records no repo digest).
    """
    target = str(reference or "").strip()
    if not target:
        return _refused_resolution(target, STATE_UNRESOLVED, detail="empty image reference")
    local = local_image_state(target, timeout=min(timeout, 30.0), runner=runner)
    if not local.exists:
        state = (
            STATE_DOCKER_UNREACHABLE if local.state == STATE_DOCKER_UNREACHABLE
            else STATE_NOT_PULLED
        )
        return _refused_resolution(target, state, raw=local.raw, detail=local.detail)
    digest = ""
    if local.repo_digests:
        digest = str(local.repo_digests[0]).split("@", 1)[-1].strip()
    if not is_digest(digest):
        return _refused_resolution(
            target, STATE_UNRESOLVED, raw=local.raw,
            detail=("the daemon records no repository digest for this image — it was "
                    "not pulled by reference, so there is no registry digest to read"),
        )
    message, fix = state_message(STATE_OFFLINE_UNVERIFIED, reference=target, digest=digest)
    return DigestResolution(
        reference=target,
        canonical=normalize_reference(target),
        state=STATE_OFFLINE_UNVERIFIED,
        digest=digest,
        digest_kind=DIGEST_KIND_LOCAL,
        platform=platform or host_platform(),
        platform_digest="",
        source="local:repo-digest",
        registry_verified=False,
        message=message,
        fix=fix,
        raw=local.raw,
        details={
            "local_digests": list(local.digests),
            "note": "read from the daemon's RepoDigests, not from the registry",
        },
    )


def record_pin(
    store: PinStore,
    reference: str,
    *,
    platform: str = "",
    capability: str = "",
    version: str = "",
    replace: bool = False,
    offline: bool = False,
    timeout: float = DEFAULT_TIMEOUT,
    runner: Any = None,
    note: str = "",
    registry: str = "",
) -> tuple[ImagePin | None, DigestResolution]:
    """Resolve and record a pin. Returns ``(pin, resolution)`` — ``pin`` is None on failure.

    A pin is never written from a *local* image id: ``offline`` reads the digest the
    daemon recorded for a pulled image and labels it ``offline_unverified``; the
    registry is always preferred when it can be reached.
    """
    target = str(reference or "").strip()
    tool = runner or DockerCliRunner()
    resolution = resolve_registry_digest(target, platform=platform, timeout=timeout,
                                         runner=tool, registry=registry)
    local = local_image_state(target, timeout=min(timeout, 30.0), runner=tool)
    if offline and not resolution.ok:
        fallback = offline_digest(target, platform=platform, timeout=timeout, runner=tool)
        if not fallback.ok:
            # keep the registry failure: it is the more informative reason
            return None, resolution
        resolution = fallback
    if not resolution.ok:
        return None, resolution
    if resolution.digest_kind != DIGEST_KIND_LOCAL and local.exists \
            and not local.carries(resolution.digest) \
            and not local.carries(resolution.platform_digest):
        message, fix = state_message(
            STATE_MISMATCH, reference=target, digest=resolution.digest,
            local_digests=list(local.digests), artifact=resolution.artifact,
        )
        return None, DigestResolution(
            reference=target, canonical=resolution.canonical, state=STATE_MISMATCH,
            digest=resolution.digest, digest_kind=resolution.digest_kind,
            platform=resolution.platform, platform_digest=resolution.platform_digest,
            source=resolution.source, registry_verified=resolution.registry_verified,
            message=message, fix=fix, raw=resolution.raw,
            details={"pinned": resolution.digest, "local_digests": list(local.digests),
                     "note": "the local image disagrees with the registry for the same "
                             "reference — nothing was pinned"},
        )
    pin = pin_from_resolution(resolution, local=local, capability=capability, note=note)
    try:
        recorded = store.record(pin, replace=replace)
        if str(version or "").strip():
            recorded = store.bind_version(
                recorded.reference, capability, version, replace=replace
            )
    except PinConflict as conflict:
        message, fix = state_message(
            STATE_PIN_CONFLICT, reference=conflict.reference, digest=conflict.digest,
            new_digest=conflict.new_digest,
        )
        return None, DigestResolution(
            reference=target, canonical=resolution.canonical, state=STATE_PIN_CONFLICT,
            digest=conflict.digest, digest_kind=resolution.digest_kind,
            source=resolution.source, message=message, fix=fix,
            details={"pinned": conflict.digest, "resolved": conflict.new_digest,
                     "pin_store": str(store.path)},
        )
    return recorded, resolution


def bind_capability(store: PinStore, reference: str, capability: str) -> ImagePin | None:
    """Record that a capability uses this pinned artifact (idempotent)."""
    pin = store.get(reference)
    name = str(capability or "").strip()
    if pin is None or not name:
        return pin
    return store.record(pin.bind(name), replace=True)


def registration_evidence(
    store: PinStore, reference: str, *, capability: str = "", kind: str = "verification"
) -> dict[str, Any]:
    """The evidence a capability registration carries for its container artifact.

    Registration code calls this (or :func:`require_registration_pin`) instead of
    quoting a tag: the evidence names the exact digest the capability was built from.
    A missing pin is reported as ``pinned: false`` with a reason — never as an empty
    success.
    """
    pin = store.get(reference)
    if pin is None:
        return {
            "capability": str(capability or ""),
            "evidence_kind": str(kind or "verification"),
            "artifact": "",
            "image_reference": normalize_reference(reference) if reference else "",
            "image_digest": "",
            "digest_kind": "",
            "platform": "",
            "source": "",
            "registry_verified": False,
            "local_verified": False,
            "recorded_at": "",
            "pinned": False,
            "reason": (
                f"no pin is recorded for {reference} — a capability may not register "
                f"against a mutable tag"
            ),
            "note": "",
        }
    return pin.evidence(capability=capability, kind=kind)


def require_registration_pin(
    store: PinStore, reference: str, *, capability: str = "", require_registry: bool = False
) -> tuple[bool, dict[str, Any]]:
    """``(ok, evidence)`` for a registration — the gate a capability build must pass."""
    evidence = registration_evidence(store, reference, capability=capability)
    if not evidence.get("pinned"):
        return False, evidence
    if require_registry and not evidence.get("registry_verified"):
        evidence = dict(evidence)
        evidence["pinned"] = False
        evidence["reason"] = (
            f"the pin of {reference} was not verified against the registry "
            f"(source: {evidence.get('source') or 'unknown'})"
        )
        return False, evidence
    return True, evidence


__all__ = [
    "DEFAULT_TIMEOUT",
    "DIGEST_KIND_INDEX",
    "DIGEST_KIND_LOCAL",
    "DIGEST_KIND_PLATFORM",
    "DIGEST_RE",
    "GUARD_EXIT_CODES",
    "GUARD_HEALTH",
    "GUARD_INVOKE",
    "GUARD_LABELS",
    "GUARD_MESSAGES",
    "GUARD_ROLLBACK",
    "GUARD_SURFACES",
    "MESSAGES",
    "PIN_FILENAME",
    "PIN_SCHEMA",
    "STATE_AUTH_REQUIRED",
    "STATE_DOCKER_UNREACHABLE",
    "STATE_EXIT_CODES",
    "STATE_MISMATCH",
    "STATE_NOT_FOUND",
    "STATE_NOT_PULLED",
    "STATE_NO_NETWORK",
    "STATE_OFFLINE_UNVERIFIED",
    "STATE_PINNED",
    "STATE_PIN_CONFLICT",
    "STATE_RATE_LIMITED",
    "STATE_TAG_MOVED",
    "STATE_UNPINNED",
    "STATE_UNRESOLVED",
    "CmdOutcome",
    "DigestGuard",
    "DigestResolution",
    "DockerCliRunner",
    "ImagePin",
    "LocalImage",
    "PinConflict",
    "PinStore",
    "PinStoreError",
    "PlatformManifest",
    "VerifyResult",
    "VersionBinding",
    "artifact_id",
    "bind_capability",
    "classify_daemon_error",
    "classify_registry_error",
    "exit_code_for",
    "guard_digest",
    "guard_exit_code",
    "guard_health",
    "guard_invoke",
    "guard_message",
    "guard_rollback",
    "host_platform",
    "is_digest",
    "local_image_state",
    "local_repo_digest",
    "mirrored_reference",
    "normalize_reference",
    "offline_digest",
    "pin_from_resolution",
    "record_pin",
    "reference_tag",
    "registration_evidence",
    "repository_of",
    "require_registration_pin",
    "resolve_registry_digest",
    "state_message",
    "verify_pin",
]
