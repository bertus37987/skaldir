"""``skaldir image …`` — the CLI surface, its envelope and its contract entries.

The group is wired by ``register_cli(subparsers)`` (see ``src/godsv/image_pin_cli.py``);
these tests drive it through the **real** ``cli.build_parser()`` tree and the real
``cli_contract.run_command`` envelope, so the exit codes and the "no success without
evidence" rule are measured, not asserted from the handler.

The docker seam is replaced through ``image_pin_cli.runner_for`` — the CLI is exercised
end to end (arguments, verdict, envelope, pin store) without a container, a pull or a
network call. The real-registry tier lives in ``tests/test_image_pin.py``.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from skaldir import cli, cli_contract, image_pin_cli
from skaldir import image_pin as P
from skaldir.image_pin_cli import register_cli

INDEX = "sha256:" + "a" * 64
LOCAL_ID = INDEX
AMD64 = "sha256:" + "b" * 64
OTHER = "sha256:" + "d" * 64

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
RATE_LIMIT_TEXT = "ERROR: unexpected status from HEAD request to ...: 429 Too Many Requests"
NO_IMAGE_TEXT = "Error response from daemon: No such image: busybox:1.36"

#: The exact ``COMMANDS`` entries the CLI owner adds to ``cli_contract.py`` — this dict
#: is the patch that must land there (the dump invariant fails without it).
IMAGE_COMMANDS = {
    "image.resolve": cli_contract._C(  # noqa: SLF001 — the module's own constructor
        "EXPAND",
        (cli_contract._ev("src/skaldir/image_pin.py"),),  # noqa: SLF001
        "what the registry says an image reference resolves to (read-only)",
        exit_codes=(0, 1, 3, 4, 5, 7),
        data_schema={"type": "object",
                     "properties": {"resolution": {"type": "object"},
                                    "state": {"type": "string"},
                                    "ok": {"type": "boolean"},
                                    "artifact": {"type": "string"}}},
        notes="read-only; a missing buildx plugin is not a failure (manifest fallback)",
    ),
    "image.pin": cli_contract._C(  # noqa: SLF001
        "EXPAND",
        (cli_contract._ev("src/skaldir/image_pin.py"),),  # noqa: SLF001
        "resolve the registry manifest digest and record it",
        mutates=True, exit_codes=(0, 1, 3, 4, 5, 6, 7),
        data_schema={"type": "object",
                     "properties": {"pin": {"type": "object"},
                                    "pinned": {"type": "boolean"},
                                    "registration_evidence": {"type": "object"}}},
        notes="a different digest for an existing pin exits 6 unless --force; a local "
              "image that disagrees with the registry pins nothing",
    ),
    "image.verify": cli_contract._C(  # noqa: SLF001
        "EXPAND",
        (cli_contract._ev("src/skaldir/image_pin.py"),),  # noqa: SLF001
        "re-verify a recorded pin against the local image (--remote: the registry)",
        exit_codes=(0, 1, 3, 4, 5, 6, 7),
        data_schema={"type": "object",
                     "properties": {"verification": {"type": "object"},
                                    "state": {"type": "string"},
                                    "ok": {"type": "boolean"}}},
        notes="a digest that no longer matches exits 6; a moved tag exits 6 as well",
    ),
    "image.guard": cli_contract._C(  # noqa: SLF001
        "EXPAND",
        (cli_contract._ev("src/skaldir/image_pin.py"),),  # noqa: SLF001
        "re-verify the recorded digest at health / invocation / rollback",
        exit_codes=(0, 1, 2, 3, 4, 5, 6, 7),
        data_schema={"type": "object",
                     "properties": {"guard": {"type": "object"},
                                    "surface": {"type": "string"},
                                    "ok": {"type": "boolean"},
                                    "exit_code": {"type": "integer"}}},
        notes="fail-closed and surface-specific: health exits 2, invocation 6, "
              "rollback 3; the evidence always carries the digest",
    ),
    "image.list": cli_contract._C(  # noqa: SLF001
        "EXPAND",
        (cli_contract._ev("src/skaldir/image_pin.py"),),  # noqa: SLF001
        "the recorded registry digest pins",
        exit_codes=(0, 1, 7),
        data_schema={"type": "object",
                     "properties": {"pins": {"type": "array"},
                                    "count": {"type": "integer"},
                                    "pin_store": {"type": "string"}}},
    ),
}


# --------------------------------------------------------------------------- #
# the fake docker seam, injection and the parser                                #
# --------------------------------------------------------------------------- #


class FakeDocker:
    """Answers the three argv shapes the module produces; records every call."""

    def __init__(self, *, imagetools=None, manifest=None, inspect=None) -> None:
        self.answers = {"imagetools": imagetools, "manifest": manifest, "inspect": inspect}
        self.calls: list[tuple[str, ...]] = []

    def run(self, argv, *, timeout=P.DEFAULT_TIMEOUT):  # noqa: ANN001 — the seam's shape
        args = tuple(str(item) for item in argv)
        self.calls.append(args)
        if args[:3] == ("docker", "buildx", "imagetools"):
            key = "imagetools"
        elif args[:3] == ("docker", "manifest", "inspect"):
            key = "manifest"
        elif args[:3] == ("docker", "image", "inspect"):
            key = "inspect"
        else:  # pragma: no cover
            raise AssertionError(f"unexpected docker call: {args}")
        answer = self.answers[key]
        if answer is None:
            return P.CmdOutcome(args, 1, "", "(no fake answer configured)")
        return P.CmdOutcome(args, answer.exit_code, answer.stdout, answer.stderr)


def ok(stdout: str = "") -> P.CmdOutcome:
    return P.CmdOutcome(("docker",), 0, stdout, "")


def err(text: str, code: int = 1) -> P.CmdOutcome:
    return P.CmdOutcome(("docker",), code, "", text)


def index_payload(digest: str = INDEX) -> dict:
    return {
        "name": "busybox:1.36",
        "manifest": {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "digest": digest, "size": 9535,
            "manifests": [
                {"mediaType": "application/vnd.oci.image.manifest.v1+json",
                 "digest": AMD64, "size": 610,
                 "platform": {"architecture": "amd64", "os": "linux"}},
            ],
        },
    }


def local_stdout(image_id: str = LOCAL_ID) -> str:
    return f"{image_id} [\"busybox@{image_id}\"]"


def _subparsers(parser):
    return next(
        action for action in parser._actions  # noqa: SLF001 — argparse has no public API
        if isinstance(action, argparse._SubParsersAction)  # noqa: SLF001
    )


def _image_parser():
    """The product parser with the group registered exactly once (like ``cli.py`` does)."""
    parser = cli.build_parser()
    sub = _subparsers(parser)
    if "image" not in sub.choices:
        register_cli(sub)
        cli_contract.annotate(parser)
    return parser


def _invoke(argv, capsys, *, runner: FakeDocker, json_mode: bool = True):
    """Run one command through the real envelope; return (exit_code, envelope, runner)."""
    if runner is not None:
        image_pin_cli.runner_for = lambda _args: runner  # the seam, not the module
    args = _image_parser().parse_args(argv)
    code = cli_contract.run_command(args, args.func, json_mode=json_mode, quiet=True)
    out = capsys.readouterr().out
    return code, (json.loads(out) if out.strip() else {}), runner


@pytest.fixture(autouse=True)
def _restore_runner():
    original = image_pin_cli.runner_for
    yield
    image_pin_cli.runner_for = original


@pytest.fixture()
def state(tmp_path: Path) -> Path:
    directory = tmp_path / "pins"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _pin(args_state: Path, *, digest: str = INDEX, kind: str = P.DIGEST_KIND_INDEX,
         platform_digest: str = AMD64, capability: str = "sha-tool",
         version: str = "1.0") -> P.ImagePin:
    store = P.PinStore(args_state / P.PIN_FILENAME)
    pin = P.ImagePin(
        reference="busybox:1.36", digest=digest, digest_kind=kind,
        canonical=P.normalize_reference("busybox:1.36"),
        repository=P.repository_of("busybox:1.36"), platform="linux/amd64",
        platform_digest=platform_digest, source="registry:imagetools",
        registry_verified=True, recorded_at="2026-09-18T00:00:00+00:00", local_verified=True,
    )
    store.record(pin)
    if capability:
        store.bind_version("busybox:1.36", capability, version)
    return store.get("busybox:1.36")


# --------------------------------------------------------------------------- #
# the contract entries and the parser                                           #
# --------------------------------------------------------------------------- #


class TestContractEntries:
    def test_the_five_entries_are_valid_specs(self):
        assert set(IMAGE_COMMANDS) == {
            "image.resolve", "image.pin", "image.verify", "image.guard", "image.list",
        }
        for path, spec in IMAGE_COMMANDS.items():
            assert spec.layer == "EXPAND", path
            assert spec.description, path
            assert spec.evidence, path
            assert isinstance(spec.exit_codes, tuple)

    def test_the_guard_entry_declares_the_three_surface_codes(self):
        codes = IMAGE_COMMANDS["image.guard"].exit_codes
        for code in (2, 3, 6):
            assert code in codes

    def test_the_mutating_verbs_are_marked_as_such(self):
        assert IMAGE_COMMANDS["image.pin"].mutates is True
        for path in ("image.resolve", "image.verify", "image.list"):
            assert IMAGE_COMMANDS[path].mutates is False, path

    def test_the_entries_can_be_added_to_the_product_table(self):
        before = dict(cli_contract.COMMANDS)
        try:
            cli_contract.COMMANDS.update(IMAGE_COMMANDS)
            assert cli_contract.registered("image.guard")
            spec = cli_contract.spec_for("image.guard")
            assert spec.exit_codes == IMAGE_COMMANDS["image.guard"].exit_codes
            assert spec.mutates is False
        finally:
            cli_contract.COMMANDS.clear()
            cli_contract.COMMANDS.update(before)


class TestParserContract:
    def test_the_group_registers_once(self):
        parser = _image_parser()
        sub = _subparsers(parser)
        assert "image" in sub.choices
        # a second registration is where a wired-in group breaks ("conflicting subparser")
        with pytest.raises(argparse.ArgumentError):
            register_cli(_subparsers(parser))

    def test_every_leaf_carries_its_dotted_command_path(self):
        parser = _image_parser()
        leaves = {path for path, _parser in cli_contract.leaf_commands(parser)}
        for path in IMAGE_COMMANDS:
            assert path in leaves, (path, sorted(leaves))

    def test_each_verb_needs_a_reference_and_keeps_its_dotted_path(self):
        parser = _image_parser()
        extra = {"resolve": [], "pin": [], "verify": [], "guard": ["--surface", "invoke"]}
        for verb, flags in extra.items():
            with pytest.raises(SystemExit):
                parser.parse_args(["image", verb, *flags])
            args = parser.parse_args(["image", verb, "busybox:1.36", *flags])
            assert args.reference == "busybox:1.36"
            assert args.command_path == f"image.{verb}"

    def test_the_guard_surface_is_constrained(self):
        parser = _image_parser()
        for surface in ("health", "invoke", "rollback"):
            args = parser.parse_args(["image", "guard", "x:1", "--surface", surface])
            assert args.surface == surface
        with pytest.raises(SystemExit):
            parser.parse_args(["image", "guard", "x:1", "--surface", "teleport"])

    def test_the_verbs_can_join_an_image_group_another_module_created(self):
        """``image inspect``/``image layers`` live in the same group as the digest pin.

        Two ``add_parser("image", …)`` calls would raise "conflicting subparser: image",
        so the group is created once and the other module attaches its verbs.
        """
        root = argparse.ArgumentParser(prog="skaldir")
        sub = root.add_subparsers(dest="command")
        group = sub.add_parser("image", help="another module owns the group")
        group_sub = group.add_subparsers(dest="image_command", required=True)
        other = group_sub.add_parser("layers", help="the other module's verb")
        other.set_defaults(func=lambda _args: 0, command_path="image.layers")
        image_pin_cli.attach(group)
        cli_contract.annotate(root)
        parser = root
        args = parser.parse_args(["image", "resolve", "busybox:1.36"])
        assert args.command_path == "image.resolve"
        args = parser.parse_args(["image", "guard", "x:1", "--surface", "health"])
        assert args.command_path == "image.guard"
        args = parser.parse_args(["image", "layers"])
        assert args.func is not image_pin_cli.cmd_list
        assert other.prog.endswith("image layers")
        leaves = {path for path, _parser in cli_contract.leaf_commands(parser)}
        assert {"image.resolve", "image.pin", "image.verify", "image.guard",
                "image.list", "image.layers"} <= leaves

    def test_attach_refuses_a_group_without_a_command_subparser(self):
        bare = argparse.ArgumentParser(prog="image")
        with pytest.raises(ValueError, match="no command subparsers"):
            image_pin_cli.attach(bare)

    def test_the_standalone_parser_offers_the_same_verbs(self):
        parser = image_pin_cli.build_parser()
        assert parser.prog == "skaldir image"
        args = parser.parse_args(["list"])
        assert args.func is image_pin_cli.cmd_list


# --------------------------------------------------------------------------- #
# resolve                                                                       #
# --------------------------------------------------------------------------- #


class TestResolveVerb:
    def test_a_registry_answer_is_reported_with_the_exact_artifact(self, capsys, state):
        runner = FakeDocker(imagetools=ok(json.dumps(index_payload())))
        code, envelope, _ = _invoke(
            ["image", "resolve", "busybox:1.36", "--state", str(state), "--json"],
            capsys, runner=runner)
        assert code == 0
        assert envelope["ok"] is True
        assert envelope["command"] == "image.resolve"
        assert envelope["exit_code"] == 0
        assert envelope["data"]["resolution"]["digest"] == INDEX
        assert envelope["data"]["artifact"] == f"docker.io/library/busybox@{INDEX}"
        assert envelope["evidence"], "no success without evidence"
        assert f"busybox@{INDEX}" in json.dumps(envelope["evidence"])

    def test_resolve_writes_no_pin(self, capsys, state):
        runner = FakeDocker(imagetools=ok(json.dumps(index_payload())))
        code, _envelope, _ = _invoke(
            ["image", "resolve", "busybox:1.36", "--state", str(state), "--json"],
            capsys, runner=runner)
        assert code == 0
        assert not (state / P.PIN_FILENAME).exists()

    def test_no_network_is_blocked_3(self, capsys, state):
        runner = FakeDocker(imagetools=err(OFFLINE_TEXT), manifest=err(OFFLINE_TEXT))
        code, envelope, _ = _invoke(
            ["image", "resolve", "busybox:1.36", "--state", str(state), "--json"],
            capsys, runner=runner)
        assert code == 3
        assert envelope["ok"] is False
        assert envelope["exit_code"] == 3
        assert envelope["error"]["hint"]
        assert "--offline" in envelope["data"]["resolution"]["fix"]

    def test_the_offline_flag_uses_the_pull_record_and_warns(self, capsys, state):
        runner = FakeDocker(imagetools=err(OFFLINE_TEXT), manifest=err(OFFLINE_TEXT),
                            inspect=ok(local_stdout()))
        code, envelope, _ = _invoke(
            ["image", "resolve", "busybox:1.36", "--offline", "--state", str(state), "--json"],
            capsys, runner=runner)
        assert code == 0
        assert envelope["data"]["resolution"]["state"] == P.STATE_OFFLINE_UNVERIFIED
        assert envelope["data"]["resolution"]["registry_verified"] is False
        assert envelope["warnings"], "an unverified digest must be flagged as such"
        assert envelope["evidence"]

    def test_auth_is_needs_credentials_4(self, capsys, state):
        runner = FakeDocker(imagetools=err(AUTH_TEXT), manifest=err(AUTH_TEXT))
        code, envelope, _ = _invoke(
            ["image", "resolve", "library/private:1", "--state", str(state), "--json"],
            capsys, runner=runner)
        assert code == 4
        assert envelope["exit_code"] == 4
        assert "docker login" in envelope["error"]["hint"]

    def test_a_missing_manifest_is_not_found_5(self, capsys, state):
        runner = FakeDocker(imagetools=err(NOT_FOUND_TEXT), manifest=err(NOT_FOUND_TEXT))
        code, envelope, _ = _invoke(
            ["image", "resolve", "busybox:9.99", "--state", str(state), "--json"],
            capsys, runner=runner)
        assert code == 5
        assert envelope["exit_code"] == 5

    def test_a_rate_limit_is_blocked_and_named(self, capsys, state):
        runner = FakeDocker(imagetools=err(RATE_LIMIT_TEXT), manifest=err(RATE_LIMIT_TEXT))
        code, envelope, _ = _invoke(
            ["image", "resolve", "busybox:1.36", "--state", str(state), "--json"],
            capsys, runner=runner)
        assert code == 3
        assert envelope["data"]["resolution"]["state"] == P.STATE_RATE_LIMITED

    def test_the_registry_override_reaches_the_docker_call(self, capsys, state):
        runner = FakeDocker(imagetools=ok(json.dumps(index_payload())))
        code, envelope, _ = _invoke(
            ["image", "resolve", "busybox:1.36", "--registry", "mirror.gcr.io",
             "--state", str(state), "--json"], capsys, runner=runner)
        assert code == 0
        assert runner.calls[0][-1] == "mirror.gcr.io/library/busybox:1.36"
        assert envelope["data"]["resolution"]["details"]["resolved_via"] == (
            "mirror.gcr.io/library/busybox:1.36")


# --------------------------------------------------------------------------- #
# pin                                                                           #
# --------------------------------------------------------------------------- #


class TestPinVerb:
    def test_pinning_records_the_registry_digest_and_the_evidence(self, capsys, state):
        runner = FakeDocker(imagetools=ok(json.dumps(index_payload())), inspect=ok(local_stdout()))
        code, envelope, _ = _invoke(
            ["image", "pin", "busybox:1.36", "--capability", "sha-tool", "--version", "1.0",
             "--state", str(state), "--json"], capsys, runner=runner)
        assert code == 0
        assert envelope["data"]["pinned"] is True
        assert envelope["data"]["pin"]["digest"] == INDEX
        evidence = envelope["data"]["registration_evidence"]
        assert evidence["artifact"] == f"docker.io/library/busybox@{INDEX}"
        assert evidence["versions"][0]["version"] == "1.0"
        on_disk = json.loads((state / P.PIN_FILENAME).read_text())
        assert on_disk["pins"]["busybox:1.36"]["digest"] == INDEX
        assert envelope["evidence"]

    def test_a_second_digest_without_force_is_a_conflict_6(self, capsys, state):
        _pin(state, digest=OTHER)
        runner = FakeDocker(imagetools=ok(json.dumps(index_payload())), inspect=ok(local_stdout()))
        code, envelope, _ = _invoke(
            ["image", "pin", "busybox:1.36", "--state", str(state), "--json"],
            capsys, runner=runner)
        assert code == 6
        assert envelope["exit_code"] == 6
        assert envelope["data"]["pinned"] is False
        assert "refusing to overwrite" in envelope["error"]["message"].lower()
        stored = json.loads((state / P.PIN_FILENAME).read_text())
        assert stored["pins"]["busybox:1.36"]["digest"] == OTHER  # untouched

    def test_force_re_pins_deliberately(self, capsys, state):
        _pin(state, digest=OTHER)
        runner = FakeDocker(imagetools=ok(json.dumps(index_payload())), inspect=ok(local_stdout()))
        code, envelope, _ = _invoke(
            ["image", "pin", "busybox:1.36", "--force", "--state", str(state), "--json"],
            capsys, runner=runner)
        assert code == 0
        assert json.loads((state / P.PIN_FILENAME).read_text())[
            "pins"]["busybox:1.36"]["digest"] == INDEX

    def test_a_local_image_that_disagrees_with_the_registry_pins_nothing(self, capsys, state):
        runner = FakeDocker(imagetools=ok(json.dumps(index_payload())),
                            inspect=ok(f"{OTHER} {json.dumps([f'busybox@{OTHER}'])}"))
        code, envelope, _ = _invoke(
            ["image", "pin", "busybox:1.36", "--state", str(state), "--json"],
            capsys, runner=runner)
        assert code == 6
        assert envelope["data"]["pinned"] is False
        assert not (state / P.PIN_FILENAME).exists()

    def test_pin_offline_records_and_marks_it_unverified(self, capsys, state):
        runner = FakeDocker(imagetools=err(OFFLINE_TEXT), manifest=err(OFFLINE_TEXT),
                            inspect=ok(local_stdout()))
        code, envelope, _ = _invoke(
            ["image", "pin", "busybox:1.36", "--offline", "--state", str(state), "--json"],
            capsys, runner=runner)
        assert code == 0
        assert envelope["data"]["pin"]["registry_verified"] is False
        assert envelope["data"]["pin"]["digest_kind"] == P.DIGEST_KIND_LOCAL


# --------------------------------------------------------------------------- #
# verify                                                                        #
# --------------------------------------------------------------------------- #


class TestVerifyVerb:
    def test_a_matching_pin_verifies(self, capsys, state):
        _pin(state)
        runner = FakeDocker(inspect=ok(local_stdout()))
        code, envelope, _ = _invoke(
            ["image", "verify", "busybox:1.36", "--state", str(state), "--json"],
            capsys, runner=runner)
        assert code == 0
        assert envelope["data"]["verification"]["state"] == P.STATE_PINNED
        assert envelope["data"]["artifact"] == f"docker.io/library/busybox@{INDEX}"
        assert envelope["evidence"]

    def test_an_unpinned_reference_is_not_found_5(self, capsys, state):
        runner = FakeDocker(inspect=ok(local_stdout()))
        code, envelope, _ = _invoke(
            ["image", "verify", "busybox:1.36", "--state", str(state), "--json"],
            capsys, runner=runner)
        assert code == 5
        assert envelope["data"]["verification"]["state"] == P.STATE_UNPINNED
        assert any("image pin" in item["command"] for item in envelope["next_actions"])

    def test_a_changed_digest_is_refused_with_exit_6(self, capsys, state):
        _pin(state)
        runner = FakeDocker(inspect=ok(f"{OTHER} {json.dumps([f'busybox@{OTHER}'])}"))
        code, envelope, _ = _invoke(
            ["image", "verify", "busybox:1.36", "--state", str(state), "--json"],
            capsys, runner=runner)
        assert code == 6
        assert envelope["ok"] is False
        assert envelope["data"]["verification"]["state"] == P.STATE_MISMATCH
        message = envelope["error"]["message"]
        assert INDEX in message and OTHER in message
        assert any(item["kind"] == "verification" for item in envelope["evidence"])

    def test_a_never_pulled_image_exits_5(self, capsys, state):
        _pin(state)
        runner = FakeDocker(inspect=err(NO_IMAGE_TEXT))
        code, envelope, _ = _invoke(
            ["image", "verify", "busybox:1.36", "--state", str(state), "--json"],
            capsys, runner=runner)
        assert code == 5
        assert "docker pull" in envelope["error"]["hint"]

    def test_an_unreachable_daemon_is_blocked_3(self, capsys, state):
        _pin(state)
        runner = FakeDocker(inspect=err("Cannot connect to the Docker daemon at unix:///x"))
        code, envelope, _ = _invoke(
            ["image", "verify", "busybox:1.36", "--state", str(state), "--json"],
            capsys, runner=runner)
        assert code == 3

    def test_remote_re_verification_catches_a_moved_tag(self, capsys, state):
        _pin(state)
        runner = FakeDocker(inspect=ok(local_stdout()),
                            imagetools=ok(json.dumps(index_payload(digest=OTHER))))
        code, envelope, _ = _invoke(
            ["image", "verify", "busybox:1.36", "--remote", "--state", str(state), "--json"],
            capsys, runner=runner)
        assert code == 6
        assert envelope["data"]["verification"]["state"] == P.STATE_TAG_MOVED
        assert OTHER in envelope["error"]["message"]

    def test_verify_without_remote_never_touches_the_registry(self, capsys, state):
        _pin(state)
        runner = FakeDocker(inspect=ok(local_stdout()))
        code, _envelope, _ = _invoke(
            ["image", "verify", "busybox:1.36", "--state", str(state), "--json"],
            capsys, runner=runner)
        assert code == 0
        assert all("imagetools" not in " ".join(call) for call in runner.calls)


# --------------------------------------------------------------------------- #
# guard: the three surfaces                                                     #
# --------------------------------------------------------------------------- #


class TestGuardVerb:
    @pytest.mark.parametrize(
        ("surface", "expected"),
        [(P.GUARD_HEALTH, 2), (P.GUARD_INVOKE, 6), (P.GUARD_ROLLBACK, 3)],
    )
    def test_a_changed_digest_fails_closed_with_the_surface_code(
        self, capsys, state, surface, expected
    ):
        _pin(state)
        runner = FakeDocker(inspect=ok(f"{OTHER} {json.dumps([f'busybox@{OTHER}'])}"))
        code, envelope, _ = _invoke(
            ["image", "guard", "busybox:1.36", "--surface", surface,
             "--capability", "sha-tool", "--version", "1.0",
             "--state", str(state), "--json"], capsys, runner=runner)
        assert code == expected
        assert envelope["exit_code"] == expected
        assert envelope["data"]["ok"] is False
        assert envelope["data"]["guard"]["exit_code"] == expected
        assert envelope["data"]["guard"]["state"] in (P.STATE_MISMATCH, P.STATE_PIN_CONFLICT)

    @pytest.mark.parametrize("surface", P.GUARD_SURFACES)
    def test_every_guard_carries_the_digest_into_its_evidence(self, capsys, state, surface):
        _pin(state)
        runner = FakeDocker(inspect=ok(local_stdout()))
        code, envelope, _ = _invoke(
            ["image", "guard", "busybox:1.36", "--surface", surface,
             "--capability", "sha-tool", "--version", "1.0",
             "--state", str(state), "--json"], capsys, runner=runner)
        assert code == 0
        guard = envelope["data"]["guard"]
        evidence = guard["evidence"]
        assert evidence["image_digest"] == INDEX
        assert evidence["surface"] == surface
        assert evidence["same_version_same_bytes"] is True
        assert evidence["artifact"] == f"docker.io/library/busybox@{INDEX}"
        assert envelope["evidence"], "the envelope carries the guard as evidence too"

    @pytest.mark.parametrize(
        ("surface", "expected"),
        [(P.GUARD_HEALTH, 2), (P.GUARD_INVOKE, 6), (P.GUARD_ROLLBACK, 3)],
    )
    def test_an_unpinned_capability_fails_closed_on_every_surface(
        self, capsys, state, surface, expected
    ):
        code, envelope, _ = _invoke(
            ["image", "guard", "busybox:1.36", "--surface", surface,
             "--capability", "sha-tool", "--state", str(state), "--json"],
            capsys, runner=FakeDocker(inspect=ok(local_stdout())))
        assert code == expected
        assert envelope["data"]["guard"]["state"] == P.STATE_UNPINNED
        assert "no pin is recorded" in envelope["data"]["guard"]["message"]

    def test_the_three_refusals_read_differently(self, capsys, state):
        _pin(state)
        runner = FakeDocker(inspect=ok(f"{OTHER} {json.dumps([f'busybox@{OTHER}'])}"))
        messages = {}
        for surface in P.GUARD_SURFACES:
            _code, envelope, _ = _invoke(
                ["image", "guard", "busybox:1.36", "--surface", surface,
                 "--capability", "sha-tool", "--version", "1.0",
                 "--state", str(state), "--json"], capsys, runner=runner)
            messages[surface] = envelope["data"]["guard"]["message"]
        assert len(set(messages.values())) == 3

    def test_a_rollback_to_a_version_registered_on_another_digest_is_blocked_3(
        self, capsys, state
    ):
        _pin(state)
        store = P.PinStore(state / P.PIN_FILENAME)
        store.record(P.ImagePin(reference="busybox:1.36", digest=OTHER,
                                digest_kind=P.DIGEST_KIND_INDEX,
                                canonical="docker.io/library/busybox:1.36",
                                repository="docker.io/library/busybox",
                                platform_digest=OTHER, source="registry:imagetools",
                                registry_verified=True), replace=True)
        code, envelope, _ = _invoke(
            ["image", "guard", "busybox:1.36", "--surface", "rollback",
             "--capability", "sha-tool", "--version", "1.0",
             "--state", str(state), "--json"],
            capsys, runner=FakeDocker(inspect=ok(local_stdout())))
        assert code == 3
        assert envelope["data"]["guard"]["state"] == P.STATE_PIN_CONFLICT
        assert "different bytes" in envelope["data"]["guard"]["message"]


# --------------------------------------------------------------------------- #
# list, the store location and the read-only verbs                              #
# --------------------------------------------------------------------------- #


class TestListVerb:
    def test_an_empty_store_is_data_not_an_error(self, capsys, state):
        code, envelope, _ = _invoke(
            ["image", "list", "--state", str(state), "--json"], capsys, runner=FakeDocker())
        assert code == 0
        assert envelope["data"]["count"] == 0
        assert envelope["data"]["pins"] == []
        assert envelope["evidence"]

    def test_the_recorded_pins_are_listed_with_their_artifact(self, capsys, state):
        _pin(state)
        code, envelope, _ = _invoke(
            ["image", "list", "--state", str(state), "--json"], capsys, runner=FakeDocker())
        assert code == 0
        assert envelope["data"]["count"] == 1
        row = envelope["data"]["pins"][0]
        assert row["digest"] == INDEX
        assert row["artifact"] == f"docker.io/library/busybox@{INDEX}"
        assert row["bindings"][0]["version"] == "1.0"

    def test_a_corrupt_store_is_internal_7_never_an_empty_list(self, capsys, state):
        (state / P.PIN_FILENAME).write_text("{ not json", encoding="utf-8")
        code, envelope, _ = _invoke(
            ["image", "list", "--state", str(state), "--json"], capsys, runner=FakeDocker())
        assert code == 7
        assert envelope["ok"] is False
        assert envelope["warnings"]

    def test_the_default_store_lives_in_the_runtime_dir(self, capsys, state, monkeypatch):
        monkeypatch.setenv("GODSV_HOME", str(state))
        code, envelope, _ = _invoke(
            ["image", "list", "--json"], capsys, runner=FakeDocker())
        assert code == 0
        assert envelope["data"]["pin_store"] == str(state / P.PIN_FILENAME)

    def test_a_state_path_that_is_a_file_is_used_as_the_store(self, capsys, tmp_path):
        target = tmp_path / "pins.json"
        code, envelope, _ = _invoke(
            ["image", "list", "--state", str(target), "--json"], capsys, runner=FakeDocker())
        assert code == 0
        assert envelope["data"]["pin_store"] == str(target)


class TestReadOnlyVerbs:
    def test_resolve_verify_and_list_never_write_the_store(self, capsys, state):
        _pin(state)
        before = (state / P.PIN_FILENAME).read_text()
        runner = FakeDocker(inspect=ok(local_stdout()),
                            imagetools=ok(json.dumps(index_payload())))
        for argv in (
            ["image", "resolve", "busybox:1.36", "--state", str(state), "--json"],
            ["image", "verify", "busybox:1.36", "--state", str(state), "--json"],
            ["image", "guard", "busybox:1.36", "--surface", "invoke", "--state", str(state),
             "--json"],
            ["image", "list", "--state", str(state), "--json"],
        ):
            code, _envelope, _ = _invoke(argv, capsys, runner=runner)
            assert code in (0, 5, 6), (argv, code)
        assert (state / P.PIN_FILENAME).read_text() == before
