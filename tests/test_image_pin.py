"""Registry manifest digest pinning — resolution, the pin store, and the refusals.

Two tiers:

* everything in this file that does not need a daemon runs against a **fake docker
  seam** (:class:`FakeDocker`), so every failure mode — no network, registry auth,
  a missing manifest, a moved tag, a digest that no longer matches — is exercised
  deterministically, with the *measured* docker texts this build was written against;
* the ``GV_IMAGE_PIN_LIVE=1`` tier does the same against the real daemon and a real
  registry (a tiny image only, digest-pinned, no container is ever created).

The point of the module under test is a refusal, so the assertions are mostly about
what it refuses and with which exit code — never about a green path that "probably"
holds.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from skaldir import image_pin as P

# --------------------------------------------------------------------------- #
# the fake docker seam                                                          #
# --------------------------------------------------------------------------- #

INDEX_DIGEST = "sha256:" + "a" * 64
AMD64_DIGEST = "sha256:" + "b" * 64
ARM64_DIGEST = "sha256:" + "c" * 64
OTHER_DIGEST = "sha256:" + "d" * 64
LOCAL_ID = INDEX_DIGEST

#: The real texts of a buildx/imagetools failure, copied from a live run (see the
#: module docstring of ``image_pin.classify_registry_error``).
OFFLINE_TEXT = (
    'ERROR: failed to do request: Head "https://registry-1.docker.io/v2/library/'
    'busybox/manifests/1.36": proxyconnect tcp: dial tcp 127.0.0.1:1: connect: '
    "connection refused"
)
AUTH_TEXT = (
    "ERROR: pull access denied, repository does not exist or may require authorization: "
    "server *** insufficient_scope: authorization failed"
)
NOT_FOUND_TEXT = "ERROR: docker.io/library/busybox:9.99: not found"
NOT_FOUND_FALLBACK = "no such manifest: docker.io/library/busybox:9.99"
RATE_LIMIT_TEXT = (
    "ERROR: unexpected status from HEAD request to https://registry-1.docker.io/v2/"
    "library/busybox/manifests/1.36: 429 Too Many Requests"
)
DAEMON_DOWN_TEXT = (
    "Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker "
    "daemon running?"
)
NO_IMAGE_TEXT = "Error response from daemon: No such image: busybox:1.36"


def index_payload(digest: str = INDEX_DIGEST, *, amd64: str = AMD64_DIGEST) -> dict:
    """An imagetools ``{{json .}}`` answer for a two-architecture index."""
    return {
        "name": "busybox:1.36",
        "manifest": {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "digest": digest,
            "size": 9535,
            "manifests": [
                {"mediaType": "application/vnd.oci.image.manifest.v1+json",
                 "digest": amd64, "size": 610,
                 "platform": {"architecture": "amd64", "os": "linux"}},
                {"mediaType": "application/vnd.oci.image.manifest.v1+json",
                 "digest": ARM64_DIGEST, "size": 610,
                 "platform": {"architecture": "arm64", "os": "linux", "variant": "v8"}},
                {"mediaType": "application/vnd.oci.image.manifest.v1+json",
                 "digest": OTHER_DIGEST, "size": 559,
                 "platform": {"architecture": "unknown", "os": "unknown"}},
            ],
        },
    }


def single_payload(digest: str = AMD64_DIGEST) -> dict:
    """An imagetools answer for a single-platform manifest (no index)."""
    return {
        "name": "pandoc/minimal:3.1.13",
        "manifest": {
            "mediaType": "application/vnd.docker.distribution.manifest.v2+json",
            "digest": digest,
            "size": 735,
        },
        "image": {"architecture": "amd64", "os": "linux", "created": "2024-06-06"},
    }


class FakeDocker:
    """A docker CLI whose answers a test dictates — and which records every call."""

    def __init__(
        self,
        *,
        imagetools: P.CmdOutcome | None = None,
        manifest: P.CmdOutcome | None = None,
        inspect: P.CmdOutcome | None = None,
    ) -> None:
        self.imagetools = imagetools
        self.manifest = manifest
        self.inspect = inspect
        self.calls: list[tuple[str, ...]] = []

    def run(self, argv, *, timeout=P.DEFAULT_TIMEOUT) -> P.CmdOutcome:
        args = tuple(str(item) for item in argv)
        self.calls.append(args)
        if args[:3] == ("docker", "buildx", "imagetools"):
            outcome = self.imagetools
        elif args[:3] == ("docker", "manifest", "inspect"):
            outcome = self.manifest
        elif args[:3] == ("docker", "image", "inspect"):
            outcome = self.inspect
        else:  # pragma: no cover — an unexpected argv is a test bug, not a pass
            raise AssertionError(f"unexpected docker call: {args}")
        if outcome is None:
            return P.CmdOutcome(args, 1, "", "(no fake answer configured)")
        return P.CmdOutcome(args, outcome.exit_code, outcome.stdout, outcome.stderr,
                            outcome.timed_out)

    @property
    def commands(self) -> list[str]:
        return [" ".join(call) for call in self.calls]


def ok(stdout: str = "", stderr: str = "") -> P.CmdOutcome:
    return P.CmdOutcome(("docker",), 0, stdout, stderr)


def err(code: int, text: str = "") -> P.CmdOutcome:
    return P.CmdOutcome(("docker",), code, "", text)


def local_stdout(image_id: str = LOCAL_ID, name: str = "busybox") -> str:
    return f"{image_id} [\"{name}@{image_id}\"]"


def pin_for(
    reference: str = "busybox:1.36",
    digest: str = INDEX_DIGEST,
    *,
    digest_kind: str = P.DIGEST_KIND_INDEX,
    platform_digest: str = AMD64_DIGEST,
    registry_verified: bool = True,
) -> P.ImagePin:
    return P.ImagePin(
        reference=reference, digest=digest, digest_kind=digest_kind,
        canonical=P.normalize_reference(reference),
        repository=P.repository_of(reference), platform="linux/amd64",
        platform_digest=platform_digest, source="registry:imagetools",
        registry_verified=registry_verified, recorded_at="2026-09-18T00:00:00+00:00",
        local_verified=True,
    )


@pytest.fixture()
def store(tmp_path: Path) -> P.PinStore:
    return P.PinStore(tmp_path / "image-pins.json")


# --------------------------------------------------------------------------- #
# references, digests, exit codes                                               #
# --------------------------------------------------------------------------- #


class TestReferences:
    def test_normalize_adds_only_the_docker_defaults(self):
        assert P.normalize_reference("busybox") == "docker.io/library/busybox:latest"
        assert P.normalize_reference("busybox:1.36") == "docker.io/library/busybox:1.36"
        assert P.normalize_reference("library/busybox:1.36") == "docker.io/library/busybox:1.36"
        assert P.normalize_reference("ghcr.io/a/b:1") == "ghcr.io/a/b:1"
        assert P.normalize_reference("localhost:5000/x/y") == "localhost:5000/x/y:latest"
        assert P.normalize_reference(f"busybox@{INDEX_DIGEST}") == "docker.io/library/busybox"

    def test_repository_and_tag(self):
        assert P.repository_of("busybox:1.36") == "docker.io/library/busybox"
        assert P.reference_tag("busybox") == "latest"
        assert P.reference_tag("busybox:1.36") == "1.36"

    def test_artifact_id_needs_a_pin_shaped_digest(self):
        assert P.artifact_id("busybox:1.36", INDEX_DIGEST) == (
            f"docker.io/library/busybox@{INDEX_DIGEST}")
        assert P.artifact_id("busybox:1.36", "latest") == ""
        assert P.artifact_id("busybox:1.36", "sha256:xyz") == ""

    def test_is_digest(self):
        assert P.is_digest(INDEX_DIGEST)
        assert not P.is_digest(INDEX_DIGEST.upper())
        assert not P.is_digest("sha256:" + "a" * 63)

    def test_mirrored_reference_only_rewrites_docker_io(self):
        assert P.mirrored_reference("alpine:latest", "mirror.gcr.io") == (
            "mirror.gcr.io/library/alpine:latest", True)
        assert P.mirrored_reference("ghcr.io/a/b:1", "mirror.gcr.io") == ("ghcr.io/a/b:1", False)
        assert P.mirrored_reference("busybox", "") == ("busybox", False)

    def test_host_platform_is_an_oci_string(self):
        platform = P.host_platform()
        assert platform.startswith("linux/")
        assert "/" in platform


class TestExitCodes:
    @pytest.mark.parametrize(
        ("state", "code"),
        [
            (P.STATE_PINNED, 0),
            (P.STATE_OFFLINE_UNVERIFIED, 0),
            (P.STATE_UNPINNED, 5),
            (P.STATE_NOT_PULLED, 5),
            (P.STATE_NOT_FOUND, 5),
            (P.STATE_AUTH_REQUIRED, 4),
            (P.STATE_NO_NETWORK, 3),
            (P.STATE_RATE_LIMITED, 3),
            (P.STATE_DOCKER_UNREACHABLE, 3),
            (P.STATE_TAG_MOVED, 6),
            (P.STATE_MISMATCH, 6),
            (P.STATE_PIN_CONFLICT, 6),
            (P.STATE_UNRESOLVED, 7),
        ],
    )
    def test_state_to_exit_code(self, state, code):
        assert P.exit_code_for(state) == code

    def test_an_unknown_state_is_internal_never_success(self):
        assert P.exit_code_for("something-new") == 7

    def test_every_state_has_a_message_and_the_messages_are_distinct(self):
        states = [state for state in P.STATE_EXIT_CODES if state != P.STATE_PINNED]
        messages = {state: P.state_message(state, reference="x", digest="d")[0]
                    for state in states}
        assert all(message for message in messages.values())
        assert len(set(messages.values())) == len(messages)

    def test_state_message_never_crashes_on_missing_fields(self):
        message, fix = P.state_message(P.STATE_MISMATCH)
        assert "REFUSED" in message
        assert fix


class TestRegistryErrorClassification:
    """The *measured* docker texts of a real machine decide the state."""

    @pytest.mark.parametrize(
        ("text", "state"),
        [
            (OFFLINE_TEXT, P.STATE_NO_NETWORK),
            ('dial tcp: lookup registry.invalid.example on 127.0.0.53:53: no such host',
             P.STATE_NO_NETWORK),
            ("i/o timeout", P.STATE_NO_NETWORK),
            (AUTH_TEXT, P.STATE_AUTH_REQUIRED),
            ("failed to authorize: failed to fetch anonymous token: unexpected status "
             "from GET request to https://ghcr.io/token: 403 Forbidden",
             P.STATE_AUTH_REQUIRED),
            (NOT_FOUND_TEXT, P.STATE_NOT_FOUND),
            (NOT_FOUND_FALLBACK, P.STATE_NOT_FOUND),
            (RATE_LIMIT_TEXT, P.STATE_RATE_LIMITED),
            ("toomanyrequests: You have reached your pull rate limit", P.STATE_RATE_LIMITED),
            ("something nobody has seen before", P.STATE_UNRESOLVED),
        ],
    )
    def test_classify(self, text, state):
        assert P.classify_registry_error(text) == state

    def test_auth_wins_over_the_does_not_exist_half_of_the_same_sentence(self):
        # the measured auth text also says "repository does not exist" — the auth
        # reading must win, because the fix is a different one
        assert "does not exist" in AUTH_TEXT
        assert P.classify_registry_error(AUTH_TEXT) == P.STATE_AUTH_REQUIRED

    def test_daemon_classification(self):
        # "docker binary not found" must NOT read as "image not found": the fixes
        # (install docker vs. pull the image) have nothing in common
        assert P.classify_daemon_error("docker binary not found: [Errno 2]") == (
            P.STATE_DOCKER_UNREACHABLE)
        assert P.classify_daemon_error(NO_IMAGE_TEXT) == P.STATE_NOT_PULLED
        assert P.classify_daemon_error(DAEMON_DOWN_TEXT) == P.STATE_DOCKER_UNREACHABLE
        assert P.classify_daemon_error(
            "permission denied while trying to connect to the docker API"
        ) == P.STATE_DOCKER_UNREACHABLE


# --------------------------------------------------------------------------- #
# resolution                                                                    #
# --------------------------------------------------------------------------- #


class TestResolveRegistryDigest:
    def test_an_index_answer_gives_the_index_and_the_platform_digest(self):
        fake = FakeDocker(imagetools=ok(json.dumps(index_payload())))
        result = P.resolve_registry_digest("busybox:1.36", platform="linux/amd64", runner=fake)
        assert result.ok and result.state == P.STATE_PINNED
        assert result.digest == INDEX_DIGEST
        assert result.digest_kind == P.DIGEST_KIND_INDEX
        assert result.platform_digest == AMD64_DIGEST
        assert result.registry_verified is True
        assert result.artifact == f"docker.io/library/busybox@{INDEX_DIGEST}"
        assert "imagetools inspect" in " ".join(fake.calls[0])
        assert result.exit_code == 0

    def test_another_platform_picks_that_manifest(self):
        fake = FakeDocker(imagetools=ok(json.dumps(index_payload())))
        result = P.resolve_registry_digest("busybox:1.36", platform="linux/arm64", runner=fake)
        assert result.platform_digest == ARM64_DIGEST

    def test_a_single_platform_answer_is_labelled_as_such(self):
        fake = FakeDocker(imagetools=ok(json.dumps(single_payload())))
        result = P.resolve_registry_digest("pandoc/minimal:3.1.13", runner=fake)
        assert result.ok
        assert result.digest == AMD64_DIGEST
        assert result.digest_kind == P.DIGEST_KIND_PLATFORM
        assert result.platform_digest == AMD64_DIGEST

    def test_a_missing_buildx_plugin_falls_back_to_manifest_inspect(self):
        fake = FakeDocker(
            imagetools=err(1, "docker: 'buildx' is not a docker command."),
            manifest=ok(json.dumps([
                {"Ref": "busybox:1.36@x",
                 "Descriptor": {"mediaType": "application/vnd.oci.image.manifest.v1+json",
                                "digest": AMD64_DIGEST, "size": 610,
                                "platform": {"architecture": "amd64", "os": "linux"}}},
                {"Ref": "busybox:1.36@y",
                 "Descriptor": {"mediaType": "application/vnd.oci.image.manifest.v1+json",
                                "digest": ARM64_DIGEST, "size": 610,
                                "platform": {"architecture": "arm64", "os": "linux"}}},
            ])),
        )
        result = P.resolve_registry_digest("busybox:1.36", runner=fake)
        assert result.ok
        assert result.digest == AMD64_DIGEST
        assert result.digest_kind == P.DIGEST_KIND_PLATFORM
        assert result.source == "registry:manifest-inspect"
        assert any("manifest inspect" in " ".join(call) for call in fake.calls)

    def test_no_network_is_blocked_not_a_guess(self):
        fake = FakeDocker(imagetools=err(1, OFFLINE_TEXT), manifest=err(1, OFFLINE_TEXT))
        result = P.resolve_registry_digest("busybox:1.36", runner=fake)
        assert not result.ok
        assert result.state == P.STATE_NO_NETWORK
        assert result.exit_code == 3
        assert "--offline" in result.fix

    def test_auth_is_needs_credentials(self):
        fake = FakeDocker(imagetools=err(1, AUTH_TEXT), manifest=err(1, AUTH_TEXT))
        result = P.resolve_registry_digest("library/private:1", runner=fake)
        assert result.state == P.STATE_AUTH_REQUIRED
        assert result.exit_code == 4
        assert "docker login" in result.fix

    def test_a_missing_manifest_is_not_found(self):
        fake = FakeDocker(imagetools=err(1, NOT_FOUND_TEXT), manifest=err(1, NOT_FOUND_FALLBACK))
        result = P.resolve_registry_digest("busybox:9.99", runner=fake)
        assert result.state == P.STATE_NOT_FOUND
        assert result.exit_code == 5

    def test_a_rate_limit_is_blocked_and_says_so(self):
        fake = FakeDocker(imagetools=err(1, RATE_LIMIT_TEXT), manifest=err(1, RATE_LIMIT_TEXT))
        result = P.resolve_registry_digest("busybox:1.36", runner=fake)
        assert result.state == P.STATE_RATE_LIMITED
        assert result.exit_code == 3
        assert "rate-limit" in result.message

    def test_an_unreadable_answer_is_internal(self):
        fake = FakeDocker(imagetools=ok("{not json"), manifest=ok("also not json"))
        result = P.resolve_registry_digest("busybox:1.36", runner=fake)
        assert result.state == P.STATE_UNRESOLVED
        assert result.exit_code == 7

    def test_an_empty_reference_is_refused(self):
        result = P.resolve_registry_digest("", runner=FakeDocker())
        assert result.state == P.STATE_UNRESOLVED
        assert result.exit_code == 7

    def test_a_digest_reference_is_its_own_identity_without_asking_a_registry(self):
        fake = FakeDocker()
        result = P.resolve_registry_digest(f"busybox@{INDEX_DIGEST}", runner=fake)
        assert result.ok
        assert result.digest == INDEX_DIGEST
        assert result.registry_verified is False
        assert fake.calls == []
        assert "registry was not asked" in result.details["note"]

    def test_a_registry_override_queries_the_mirror_but_keeps_the_reference(self):
        fake = FakeDocker(imagetools=ok(json.dumps(index_payload())))
        result = P.resolve_registry_digest("alpine:latest", registry="mirror.gcr.io", runner=fake)
        assert result.ok
        assert result.reference == "alpine:latest"
        assert result.digest == INDEX_DIGEST
        assert result.details["resolved_via"] == "mirror.gcr.io/library/alpine:latest"
        assert result.canonical == "docker.io/library/alpine:latest"

    def test_a_registry_override_on_a_foreign_reference_is_reported_not_applied(self):
        fake = FakeDocker(imagetools=ok(json.dumps(index_payload())))
        result = P.resolve_registry_digest("ghcr.io/a/b:1", registry="mirror.gcr.io", runner=fake)
        assert result.ok
        assert "was not applied" in result.details["registry_warning"]
        assert fake.calls[0][-1] == "ghcr.io/a/b:1"


class TestLocalImage:
    def test_absent_image_is_not_pulled(self):
        fake = FakeDocker(inspect=err(1, NO_IMAGE_TEXT))
        local = P.local_image_state("busybox:1.36", runner=fake)
        assert local.exists is False
        assert local.state == P.STATE_NOT_PULLED
        assert local.exit_code == 5

    def test_daemon_down_is_blocked(self):
        fake = FakeDocker(inspect=err(1, DAEMON_DOWN_TEXT))
        local = P.local_image_state("busybox:1.36", runner=fake)
        assert local.exists is False
        assert local.state == P.STATE_DOCKER_UNREACHABLE
        assert local.exit_code == 3

    def test_the_id_and_the_repo_digests_are_both_carried(self):
        fake = FakeDocker(inspect=ok(local_stdout()))
        local = P.local_image_state("busybox:1.36", runner=fake)
        assert local.exists and local.state == P.STATE_PINNED
        assert local.image_id == LOCAL_ID
        assert local.repo_digests == (f"busybox@{LOCAL_ID}",)
        assert local.digests == (LOCAL_ID,)
        assert local.carries(LOCAL_ID)
        assert not local.carries(OTHER_DIGEST)

    def test_an_image_without_a_usable_id_is_not_a_pass(self):
        fake = FakeDocker(inspect=ok(" [ ]"))
        local = P.local_image_state("busybox:1.36", runner=fake)
        assert local.state == P.STATE_UNRESOLVED
        assert local.exit_code == 7


class TestOfflineDigest:
    def test_the_pull_record_is_used_and_labelled_unverified(self):
        fake = FakeDocker(inspect=ok(local_stdout()))
        result = P.offline_digest("busybox:1.36", runner=fake)
        assert result.ok
        assert result.state == P.STATE_OFFLINE_UNVERIFIED
        assert result.exit_code == 0
        assert result.digest == LOCAL_ID
        assert result.registry_verified is False
        assert result.source == "local:repo-digest"
        assert "not registry-verified" in result.message

    def test_an_image_that_was_never_pulled_is_refused_offline_too(self):
        fake = FakeDocker(inspect=err(1, NO_IMAGE_TEXT))
        result = P.offline_digest("busybox:1.35", runner=fake)
        assert not result.ok
        assert result.state == P.STATE_NOT_PULLED
        assert result.exit_code == 5

    def test_a_locally_built_image_has_no_registry_digest_to_read(self):
        fake = FakeDocker(inspect=ok(f"{LOCAL_ID} []"))
        result = P.offline_digest("my-local-build:1", runner=fake)
        assert result.state == P.STATE_UNRESOLVED
        assert result.exit_code == 7
        assert "no repository digest" in result.details["detail"]


# --------------------------------------------------------------------------- #
# the pin store                                                                 #
# --------------------------------------------------------------------------- #


class TestPinStore:
    def test_round_trip(self, store: P.PinStore):
        recorded = store.record(pin_for())
        assert recorded.digest == INDEX_DIGEST
        loaded = store.get("busybox:1.36")
        assert loaded is not None
        assert loaded.digest == INDEX_DIGEST
        assert loaded.artifact == f"docker.io/library/busybox@{INDEX_DIGEST}"
        assert store.exists()
        on_disk = json.loads(store.path.read_text())
        assert on_disk["schema"] == P.PIN_SCHEMA
        assert on_disk["pins"]["busybox:1.36"]["digest"] == INDEX_DIGEST

    def test_a_missing_store_is_empty_not_an_error(self, store: P.PinStore):
        assert store.load() == {}
        assert store.get("busybox:1.36") is None
        assert store.all() == []

    def test_lookup_normalizes_the_reference(self, store: P.PinStore):
        store.record(pin_for())
        assert store.get("busybox") is not None
        assert store.get("docker.io/library/busybox:1.36") is not None
        assert store.get("busybox:1.36") is not None

    def test_a_corrupt_store_is_an_error_never_an_empty_one(self, store: P.PinStore):
        store.path.parent.mkdir(parents=True, exist_ok=True)
        store.path.write_text("{ this is not json", encoding="utf-8")
        with pytest.raises(P.PinStoreError):
            store.load()

    def test_a_stored_pin_without_a_digest_is_refused(self, store: P.PinStore):
        store.path.parent.mkdir(parents=True, exist_ok=True)
        store.path.write_text(json.dumps({"pins": {"busybox:1.36": {"reference": "busybox:1.36",
                                                                   "digest": "latest"}}}),
                              encoding="utf-8")
        with pytest.raises(P.PinStoreError):
            store.load()

    def test_a_different_digest_needs_a_deliberate_replace(self, store: P.PinStore):
        store.record(pin_for())
        with pytest.raises(P.PinConflict) as conflict:
            store.record(pin_for(digest=OTHER_DIGEST))
        assert conflict.value.digest == INDEX_DIGEST
        assert conflict.value.new_digest == OTHER_DIGEST
        assert store.get("busybox:1.36").digest == INDEX_DIGEST
        replaced = store.record(pin_for(digest=OTHER_DIGEST), replace=True)
        assert replaced.digest == OTHER_DIGEST
        assert store.get("busybox:1.36").digest == OTHER_DIGEST

    def test_recording_keeps_the_capabilities_and_bindings(self, store: P.PinStore):
        store.record(pin_for())
        store.bind_version("busybox:1.36", "sha-tool", "1.0")
        again = store.record(pin_for(), replace=True)
        assert again.capabilities == ("sha-tool",)
        assert [binding.version for binding in again.bindings] == ["1.0"]
        assert again.bindings[0].digest == INDEX_DIGEST

    def test_the_same_version_cannot_be_rebound_to_a_different_digest(self, store: P.PinStore):
        store.record(pin_for())
        store.bind_version("busybox:1.36", "sha-tool", "1.0")
        store.record(pin_for(digest=OTHER_DIGEST), replace=True)
        with pytest.raises(P.PinConflict):
            store.bind_version("busybox:1.36", "sha-tool", "1.0")
        rebound = store.bind_version("busybox:1.36", "sha-tool", "1.0", replace=True)
        assert rebound.binding_for("sha-tool", "1.0").digest == OTHER_DIGEST

    def test_binding_a_version_without_a_pin_is_an_error(self, store: P.PinStore):
        with pytest.raises(P.PinStoreError):
            store.bind_version("busybox:1.36", "sha-tool", "1.0")

    def test_removing_a_pin(self, store: P.PinStore):
        store.record(pin_for())
        assert store.remove("busybox") is True
        assert store.get("busybox:1.36") is None
        assert store.remove("busybox") is False

    def test_a_non_digest_pin_cannot_be_recorded(self, store: P.PinStore):
        with pytest.raises(P.PinStoreError):
            store.record(pin_for(digest="alpine@latest"))

    def test_the_write_is_atomic(self, store: P.PinStore):
        store.record(pin_for())
        leftovers = [item for item in store.path.parent.iterdir() if item != store.path]
        assert leftovers == []


class TestCarriesLocal:
    def test_an_index_pin_matches_an_image_that_records_the_platform_manifest(self):
        # the classic image store records the platform manifest digest for a
        # multi-arch pull; the containerd store records the index digest. Both name
        # the same pulled bytes and both must verify.
        pin = pin_for(platform_digest=AMD64_DIGEST)
        classic = P.LocalImage(reference="busybox:1.36", state=P.STATE_PINNED, exists=True,
                               image_id=OTHER_DIGEST,
                               repo_digests=(f"busybox@{AMD64_DIGEST}",))
        assert pin.carries_local(classic) is True

    def test_a_third_digest_is_never_accepted(self):
        pin = pin_for(platform_digest=AMD64_DIGEST)
        other = P.LocalImage(reference="busybox:1.36", state=P.STATE_PINNED, exists=True,
                             image_id=OTHER_DIGEST, repo_digests=(f"busybox@{OTHER_DIGEST}",))
        assert pin.carries_local(other) is False


# --------------------------------------------------------------------------- #
# verification                                                                  #
# --------------------------------------------------------------------------- #


def _verify(store, *, remote=False, reference="busybox:1.36", inspect=None, imagetools=None,
            manifest=None, capability=""):
    fake = FakeDocker(
        imagetools=imagetools,
        manifest=manifest,
        inspect=inspect if inspect is not None else ok(local_stdout()),
    )
    result = P.verify_pin(store, reference, remote=remote, runner=fake, capability=capability)
    return result, fake


class TestVerifyPin:
    def test_an_unpinned_reference_is_not_found_and_says_what_to_do(self, store: P.PinStore):
        result, _ = _verify(store)
        assert result.state == P.STATE_UNPINNED
        assert result.exit_code == 5
        assert result.next_actions
        assert "image pin" in result.next_actions[0][0]

    def test_a_matching_local_image_verifies(self, store: P.PinStore):
        store.record(pin_for())
        result, fake = _verify(store)
        assert result.ok
        assert result.state == P.STATE_PINNED
        assert result.exit_code == 0
        assert result.artifact == f"docker.io/library/busybox@{INDEX_DIGEST}"
        # the local check must not touch the network
        assert all("imagetools" not in command for command in fake.commands)

    def test_a_changed_local_digest_is_refused_with_exit_6(self, store: P.PinStore):
        store.record(pin_for())
        result, _ = _verify(store, inspect=ok(local_stdout(image_id=OTHER_DIGEST,
                                                           name="busybox")))
        assert result.state == P.STATE_MISMATCH
        assert result.exit_code == 6
        assert INDEX_DIGEST in result.message and OTHER_DIGEST in result.message
        assert "REFUSED" in result.message
        assert result.warnings
        assert result.next_actions[0][0].endswith("--force")

    def test_the_evidence_of_a_mismatch_names_both_digests(self, store: P.PinStore):
        store.record(pin_for())
        other_local = f"{OTHER_DIGEST} {json.dumps([f'busybox@{OTHER_DIGEST}'])}"
        result, _ = _verify(store, inspect=ok(other_local))
        evidence = result.evidence()
        assert evidence["image_digest"] == INDEX_DIGEST
        assert evidence["local_digests"] == [OTHER_DIGEST]
        assert evidence["ok"] is False
        assert evidence["state"] == P.STATE_MISMATCH

    def test_an_image_that_was_never_pulled_is_reported_as_such(self, store: P.PinStore):
        store.record(pin_for())
        result, _ = _verify(store, inspect=err(1, NO_IMAGE_TEXT))
        assert result.state == P.STATE_NOT_PULLED
        assert result.exit_code == 5
        assert "docker pull" in result.fix

    def test_an_unreachable_daemon_is_blocked(self, store: P.PinStore):
        store.record(pin_for())
        result, _ = _verify(store, inspect=err(1, DAEMON_DOWN_TEXT))
        assert result.state == P.STATE_DOCKER_UNREACHABLE
        assert result.exit_code == 3

    def test_a_moved_tag_is_a_conflict_even_when_the_local_image_matches(self, store: P.PinStore):
        store.record(pin_for())
        result, _ = _verify(store, remote=True,
                            imagetools=ok(json.dumps(index_payload(digest=OTHER_DIGEST))))
        assert result.state == P.STATE_TAG_MOVED
        assert result.exit_code == 6
        assert OTHER_DIGEST in result.message and INDEX_DIGEST in result.message
        assert any("only the tag moved" in warning for warning in result.warnings)

    def test_remote_resolution_failures_keep_their_own_state(self, store: P.PinStore):
        store.record(pin_for())
        offline, _ = _verify(store, remote=True, imagetools=err(1, OFFLINE_TEXT),
                             manifest=err(1, OFFLINE_TEXT))
        assert offline.state == P.STATE_NO_NETWORK and offline.exit_code == 3
        store.record(pin_for(), replace=True)
        unauthorized, _ = _verify(store, remote=True, imagetools=err(1, AUTH_TEXT),
                                  manifest=err(1, AUTH_TEXT))
        assert unauthorized.state == P.STATE_AUTH_REQUIRED and unauthorized.exit_code == 4

    def test_a_pin_bound_to_another_capability_is_refused(self, store: P.PinStore):
        store.record(pin_for())
        store.bind_version("busybox:1.36", "sha-tool", "1.0")
        result, _ = _verify(store, capability="other-tool")
        assert result.state == P.STATE_PIN_CONFLICT
        assert result.exit_code == 6
        assert "sha-tool" in result.message

    def test_an_unverified_pin_carries_a_warning(self, store: P.PinStore):
        store.record(pin_for(registry_verified=False, digest_kind=P.DIGEST_KIND_LOCAL,
                             platform_digest=""))
        result, _ = _verify(store, inspect=ok(local_stdout()))
        assert result.ok
        assert any("not verified against the registry" in warning
                   for warning in result.warnings)


# --------------------------------------------------------------------------- #
# recording                                                                     #
# --------------------------------------------------------------------------- #


class TestRecordPin:
    def test_a_registry_answer_is_recorded(self, store: P.PinStore):
        fake = FakeDocker(imagetools=ok(json.dumps(index_payload())), inspect=ok(local_stdout()))
        pin, resolution = P.record_pin(store, "busybox:1.36", capability="sha-tool",
                                       version="1.0", runner=fake)
        assert pin is not None and resolution.ok
        assert pin.digest == INDEX_DIGEST
        assert pin.registry_verified is True
        assert pin.local_verified is True
        assert pin.capabilities == ("sha-tool",)
        assert pin.binding_for("sha-tool", "1.0").digest == INDEX_DIGEST
        assert store.get("busybox:1.36") is not None

    def test_a_local_image_that_disagrees_with_the_registry_pins_nothing(self, store: P.PinStore):
        fake = FakeDocker(imagetools=ok(json.dumps(index_payload())),
                          inspect=ok(local_stdout(image_id=OTHER_DIGEST)))
        pin, resolution = P.record_pin(store, "busybox:1.36", runner=fake)
        assert pin is None
        assert resolution.state == P.STATE_MISMATCH
        assert resolution.exit_code == 6
        assert store.get("busybox:1.36") is None
        assert "nothing was pinned" in resolution.details["note"]

    def test_offline_records_the_pull_record_unverified(self, store: P.PinStore):
        fake = FakeDocker(imagetools=err(1, OFFLINE_TEXT), manifest=err(1, OFFLINE_TEXT),
                          inspect=ok(local_stdout()))
        pin, resolution = P.record_pin(store, "busybox:1.36", offline=True, runner=fake)
        assert pin is not None
        assert resolution.state == P.STATE_OFFLINE_UNVERIFIED
        assert pin.registry_verified is False
        assert pin.digest_kind == P.DIGEST_KIND_LOCAL

    def test_offline_without_a_local_image_still_reports_the_registry_failure(
        self, store: P.PinStore
    ):
        fake = FakeDocker(imagetools=err(1, OFFLINE_TEXT), manifest=err(1, OFFLINE_TEXT),
                          inspect=err(1, NO_IMAGE_TEXT))
        pin, resolution = P.record_pin(store, "busybox:1.36", offline=True, runner=fake)
        assert pin is None
        assert resolution.state == P.STATE_NO_NETWORK

    def test_a_second_digest_for_the_same_reference_needs_force(self, store: P.PinStore):
        store.record(pin_for(digest=OTHER_DIGEST))
        fake = FakeDocker(imagetools=ok(json.dumps(index_payload())), inspect=ok(local_stdout()))
        pin, resolution = P.record_pin(store, "busybox:1.36", runner=fake)
        assert pin is None
        assert resolution.state == P.STATE_PIN_CONFLICT
        assert resolution.exit_code == 6
        forced, resolution = P.record_pin(store, "busybox:1.36", replace=True, runner=fake)
        assert forced is not None and forced.digest == INDEX_DIGEST
        assert resolution.ok

    def test_a_registry_failure_records_nothing(self, store: P.PinStore):
        fake = FakeDocker(imagetools=err(1, AUTH_TEXT), manifest=err(1, AUTH_TEXT),
                          inspect=ok(local_stdout()))
        pin, resolution = P.record_pin(store, "library/private:1", runner=fake)
        assert pin is None
        assert resolution.state == P.STATE_AUTH_REQUIRED
        assert store.all() == []


# --------------------------------------------------------------------------- #
# the digest guard: health, invocation, rollback                                #
# --------------------------------------------------------------------------- #


class TestDigestGuard:
    def test_the_three_surfaces_have_three_codes(self):
        assert P.GUARD_EXIT_CODES[P.GUARD_HEALTH] == 2
        assert P.GUARD_EXIT_CODES[P.GUARD_INVOKE] == 6
        assert P.GUARD_EXIT_CODES[P.GUARD_ROLLBACK] == 3
        assert sorted(P.GUARD_SURFACES) == ["health", "invoke", "rollback"]
        assert P.guard_exit_code("nonsense") == 7

    @pytest.mark.parametrize("surface", P.GUARD_SURFACES)
    def test_a_matching_digest_passes_every_surface(self, store: P.PinStore, surface):
        store.record(pin_for())
        fake = FakeDocker(inspect=ok(local_stdout()))
        guard = P.guard_digest(store, "busybox:1.36", surface=surface,
                               capability="sha-tool", version="1.0", runner=fake)
        assert guard.ok
        assert guard.exit_code == 0
        evidence = guard.evidence()
        assert evidence["image_digest"] == INDEX_DIGEST
        assert evidence["artifact"] == f"docker.io/library/busybox@{INDEX_DIGEST}"
        assert evidence["surface"] == surface
        assert evidence["same_version_same_bytes"] is True
        assert evidence["evidence_kind"] == "verification"

    @pytest.mark.parametrize("surface", P.GUARD_SURFACES)
    def test_a_changed_digest_fails_closed_on_every_surface(self, store: P.PinStore, surface):
        store.record(pin_for())
        fake = FakeDocker(inspect=ok(f"{OTHER_DIGEST} {json.dumps([f'busybox@{OTHER_DIGEST}'])}"))
        guard = P.guard_digest(store, "busybox:1.36", surface=surface,
                               capability="sha-tool", version="1.0", runner=fake)
        assert not guard.ok
        assert guard.exit_code == P.GUARD_EXIT_CODES[surface]
        assert guard.exit_code != 0
        assert INDEX_DIGEST in guard.message and OTHER_DIGEST in guard.message
        evidence = guard.evidence()
        assert evidence["ok"] is False
        assert evidence["image_digest"] == INDEX_DIGEST
        assert evidence["local_digests"] == [OTHER_DIGEST]
        assert evidence["exit_code"] == P.GUARD_EXIT_CODES[surface]
        assert evidence["same_version_same_bytes"] is False

    def test_the_three_refusal_messages_are_distinct(self, store: P.PinStore):
        store.record(pin_for())
        fake = FakeDocker(inspect=ok(f"{OTHER_DIGEST} {json.dumps([f'busybox@{OTHER_DIGEST}'])}"))
        messages = {}
        for surface in P.GUARD_SURFACES:
            guard = P.guard_digest(store, "busybox:1.36", surface=surface,
                                   capability="sha-tool", version="1.0", runner=fake)
            messages[surface] = guard.message
        assert len(set(messages.values())) == 3
        assert "health check" in messages[P.GUARD_HEALTH]
        assert "invoke" in messages[P.GUARD_INVOKE]
        assert "roll back" in messages[P.GUARD_ROLLBACK]

    def test_a_version_registered_on_another_digest_is_a_conflict_on_every_surface(
        self, store: P.PinStore
    ):
        # the pin moved on (a re-pin) while the version binding still records the
        # artifact the version was registered with: the same version number would
        # otherwise resolve to different bytes
        store.record(pin_for())
        store.bind_version("busybox:1.36", "sha-tool", "1.0")
        store.record(pin_for(digest=OTHER_DIGEST), replace=True)
        for surface in P.GUARD_SURFACES:
            fake = FakeDocker(inspect=ok(local_stdout()))
            guard = P.guard_digest(store, "busybox:1.36", surface=surface,
                                   capability="sha-tool", version="1.0", runner=fake)
            assert guard.state == P.STATE_PIN_CONFLICT
            assert guard.exit_code == P.GUARD_EXIT_CODES[surface]
            assert "registered on" in guard.message
            assert "different bytes" in guard.message
            assert fake.calls == []  # refused before any local inspection

    def test_an_unpinned_capability_fails_closed_on_every_surface(self, store: P.PinStore):
        for surface in P.GUARD_SURFACES:
            guard = P.guard_digest(store, "busybox:1.36", surface=surface,
                                   capability="sha-tool", runner=FakeDocker())
            assert guard.state == P.STATE_UNPINNED
            assert guard.exit_code == P.GUARD_EXIT_CODES[surface]

    @pytest.mark.parametrize("surface", P.GUARD_SURFACES)
    def test_an_image_that_is_not_local_fails_closed(self, store: P.PinStore, surface):
        store.record(pin_for())
        guard = P.guard_digest(store, "busybox:1.36", surface=surface, capability="sha-tool",
                               runner=FakeDocker(inspect=err(1, NO_IMAGE_TEXT)))
        assert guard.state == P.STATE_NOT_PULLED
        assert guard.exit_code == P.GUARD_EXIT_CODES[surface]
        assert "docker pull" in guard.fix

    def test_an_unreachable_daemon_fails_closed(self, store: P.PinStore):
        store.record(pin_for())
        guard = P.guard_invoke(store, "busybox:1.36", capability="sha-tool",
                               runner=FakeDocker(inspect=err(1, DAEMON_DOWN_TEXT)))
        assert guard.state == P.STATE_DOCKER_UNREACHABLE
        assert guard.exit_code == 6

    def test_a_moved_tag_fails_closed_on_the_invocation_path(self, store: P.PinStore):
        store.record(pin_for())
        fake = FakeDocker(inspect=ok(local_stdout()),
                          imagetools=ok(json.dumps(index_payload(digest=OTHER_DIGEST))))
        guard = P.guard_invoke(store, "busybox:1.36", capability="sha-tool", version="1.0",
                               remote=True, runner=fake)
        assert guard.state == P.STATE_TAG_MOVED
        assert guard.exit_code == 6
        assert guard.evidence()["remote_digest"] == OTHER_DIGEST

    def test_the_guard_helpers_agree_with_guard_digest(self, store: P.PinStore):
        store.record(pin_for())
        fake = FakeDocker(inspect=ok(local_stdout()))
        assert P.guard_health(store, "busybox:1.36", runner=fake).surface == "health"
        assert P.guard_invoke(store, "busybox:1.36", runner=fake).surface == "invoke"
        assert P.guard_rollback(store, "busybox:1.36", runner=fake).surface == "rollback"

    def test_an_unknown_surface_is_an_internal_error_not_a_pass(self, store: P.PinStore):
        store.record(pin_for())
        guard = P.guard_digest(store, "busybox:1.36", surface="teleport",
                               runner=FakeDocker(inspect=ok(local_stdout())))
        assert guard.state == P.STATE_UNRESOLVED
        assert guard.exit_code == 7
        assert not guard.ok

    def test_guard_message_wraps_an_unlisted_state_instead_of_inventing_one(self):
        message, _fix = P.guard_message(P.GUARD_INVOKE, P.STATE_DOCKER_UNREACHABLE,
                                        capability="sha-tool", reference="busybox:1.36")
        assert message.startswith("REFUSED the capability invocation of sha-tool")
        assert "Docker daemon" in message


# --------------------------------------------------------------------------- #
# the bridge: the guard's digest is what the runtime actually accepts            #
# --------------------------------------------------------------------------- #


class TestGuardEvidenceIsUsable:
    """The guard's job is not the verdict alone: its evidence must carry the digest
    into the health ledger, the invocation record and the rollback record — in the
    exact shape the runtime's own pin check accepts."""

    def test_the_evidence_digest_is_pin_shaped_for_the_container_runtime(self, store: P.PinStore):
        from skaldir import docker_runtime as D

        store.record(pin_for())
        guard = P.guard_invoke(store, "busybox:1.36", capability="sha-tool", version="1.0",
                               runner=FakeDocker(inspect=ok(local_stdout())))
        evidence = guard.evidence()
        assert D.DIGEST_RE.match(evidence["image_digest"]), evidence["image_digest"]
        # the runtime refuses a config without a pin-shaped digest; this one passes it
        config = D.RunConfig(image="busybox:1.36", image_digest=evidence["image_digest"])
        assert config.policy_problems() == []

    def test_the_evidence_row_is_json_serializable_for_a_ledger(self, store: P.PinStore):
        store.record(pin_for())
        for surface in P.GUARD_SURFACES:
            guard = P.guard_digest(store, "busybox:1.36", surface=surface,
                                   capability="sha-tool", version="1.0",
                                   runner=FakeDocker(inspect=ok(local_stdout())))
            row = json.loads(json.dumps(guard.evidence(), sort_keys=True))
            assert row["surface"] == surface
            assert row["artifact"].endswith(row["image_digest"])
            assert row["recorded_at"]
            payload = json.loads(json.dumps(guard.to_dict()))
            assert payload["evidence"]["image_digest"] == row["image_digest"]

    def test_a_refused_guard_still_carries_the_registered_digest(self, store: P.PinStore):
        store.record(pin_for())
        guard = P.guard_health(store, "busybox:1.36", capability="sha-tool", version="1.0",
                               runner=FakeDocker(inspect=ok(
                                   f"{OTHER_DIGEST} {json.dumps([f'busybox@{OTHER_DIGEST}'])}")))
        evidence = guard.evidence()
        assert evidence["ok"] is False
        # the recorded digest is still named, so a health row can say what was expected
        assert evidence["image_digest"] == INDEX_DIGEST
        assert evidence["artifact"] == f"docker.io/library/busybox@{INDEX_DIGEST}"


# --------------------------------------------------------------------------- #
# registration evidence — the digest must survive into the registration          #
# --------------------------------------------------------------------------- #


class TestRegistrationEvidence:
    def test_an_unpinned_reference_cannot_produce_evidence(self, store: P.PinStore):
        evidence = P.registration_evidence(store, "busybox:1.36", capability="sha-tool")
        assert evidence["pinned"] is False
        assert evidence["image_digest"] == ""
        assert "no pin is recorded" in evidence["reason"]
        ok_flag, checked = P.require_registration_pin(store, "busybox:1.36")
        assert ok_flag is False and checked["pinned"] is False

    def test_a_pin_names_the_exact_artifact_and_the_versions(self, store: P.PinStore):
        store.record(pin_for())
        store.bind_version("busybox:1.36", "sha-tool", "1.0")
        evidence = P.registration_evidence(store, "busybox:1.36", capability="sha-tool")
        assert evidence["pinned"] is True
        assert evidence["artifact"] == f"docker.io/library/busybox@{INDEX_DIGEST}"
        assert evidence["image_digest"] == INDEX_DIGEST
        assert evidence["versions"][0]["version"] == "1.0"
        assert evidence["registry_verified"] is True

    def test_a_registration_can_demand_a_registry_verified_pin(self, store: P.PinStore):
        store.record(pin_for(registry_verified=False, digest_kind=P.DIGEST_KIND_LOCAL,
                             platform_digest=""))
        ok_flag, evidence = P.require_registration_pin(store, "busybox:1.36",
                                                      require_registry=True)
        assert ok_flag is False
        assert "not verified against the registry" in evidence["reason"]
        ok_flag, evidence = P.require_registration_pin(store, "busybox:1.36")
        assert ok_flag is True

    def test_bind_capability_records_the_name(self, store: P.PinStore):
        store.record(pin_for())
        bound = P.bind_capability(store, "busybox:1.36", "sha-tool")
        assert bound.capabilities == ("sha-tool",)
        assert store.get("busybox:1.36").capabilities == ("sha-tool",)


# --------------------------------------------------------------------------- #
# live tier: a real registry, a real daemon, a tiny image                        #
# --------------------------------------------------------------------------- #

LIVE = os.environ.get("GV_IMAGE_PIN_LIVE") == "1"
LIVE_REFERENCE = os.environ.get("GV_IMAGE_PIN_LIVE_REF", "mirror.gcr.io/library/busybox:1.36")


@pytest.mark.skipif(not LIVE, reason="set GV_IMAGE_PIN_LIVE=1 for the real-registry tier")
class TestLive:
    def test_a_real_digest_resolution_a_re_verify_and_the_refusal(self, tmp_path: Path):
        store = P.PinStore(tmp_path / "image-pins.json")
        resolution = P.resolve_registry_digest(LIVE_REFERENCE)
        if not resolution.ok:
            pytest.skip(f"no usable registry right now: {resolution.state} — {resolution.message}")
        assert P.is_digest(resolution.digest)
        assert resolution.registry_verified is True
        assert resolution.raw  # the raw registry answer travels with the result

        pin, recorded = P.record_pin(store, LIVE_REFERENCE, capability="live-demo",
                                     version="1.0")
        assert pin is not None, recorded.message
        assert pin.digest == resolution.digest

        verified = P.verify_pin(store, LIVE_REFERENCE)
        if verified.state == P.STATE_NOT_PULLED:
            pytest.skip("the image is not pulled locally — nothing to re-verify")
        assert verified.ok, verified.message

        for surface in P.GUARD_SURFACES:
            guard = P.guard_digest(store, LIVE_REFERENCE, surface=surface,
                                   capability="live-demo", version="1.0")
            assert guard.ok, guard.message
            assert guard.evidence()["image_digest"] == pin.digest

        # now make the pin stale, exactly as a changed artifact would: a real,
        # pin-shaped digest of another artifact
        other = "sha256:" + "1" * 64
        store.record(P.ImagePin(reference=pin.reference, digest=other,
                                digest_kind=pin.digest_kind, canonical=pin.canonical,
                                repository=pin.repository, platform=pin.platform,
                                platform_digest=pin.platform_digest, source=pin.source,
                                registry_verified=True), replace=True)
        refused = P.verify_pin(store, LIVE_REFERENCE)
        assert refused.state == P.STATE_MISMATCH
        assert refused.exit_code == 6
        for surface in P.GUARD_SURFACES:
            guard = P.guard_digest(store, LIVE_REFERENCE, surface=surface,
                                   capability="live-demo", version="1.0")
            assert guard.exit_code == P.GUARD_EXIT_CODES[surface]
            assert not guard.ok

    def test_the_offline_path_still_reads_the_pull_record(self, tmp_path: Path):
        local = P.local_image_state(LIVE_REFERENCE)
        if not local.exists:
            pytest.skip("the image is not pulled locally")
        offline = P.offline_digest(LIVE_REFERENCE)
        assert offline.ok
        assert offline.registry_verified is False
        assert offline.state == P.STATE_OFFLINE_UNVERIFIED
        assert offline.digest in local.digests
