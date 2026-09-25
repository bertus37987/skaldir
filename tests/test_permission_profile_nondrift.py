"""Non-drift: the hash in the sandbox argv equals the hash in the prompt block.

This is the property the whole design exists for — *a run can never operate under limits it
was not told about*. It is asserted three ways:

1. **In the object**: ``sandbox_argv()`` and ``prompt_block()`` both carry
   :attr:`PermissionProfile.identity_sha256`, and re-hashing the profile JSON the argv carries
   reproduces it (so the argv is self-verifying, not just self-describing).
2. **Across a change**: changing a field moves both, in the same instant, to the same value.
3. **In a real run**: a command actually executed inside ``bwrap`` under the profile, whose
   argv carries the same hash as the block the agent was shown.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from skaldir import permission_profile as P


def _hashes(profile: P.PermissionProfile) -> tuple[str, str, str]:
    argv = profile.sandbox_argv("true")
    return (
        profile.identity_sha256,
        P.permission_profile_hash_from_argv(argv),
        P.permission_profile_hash_from_prompt(profile.prompt_block()),
    )


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    (root / ".git").mkdir(parents=True)
    (root / "keep.txt").write_text("keep\n", encoding="utf-8")
    return root


@pytest.mark.parametrize(
    "changes",
    [
        {},
        {"network": "on"},
        {"read_policy": "roots"},
        {"writable_paths": ("/tmp", "/var/tmp")},
        {"protected_paths": ()},
        {"allowed_subprocess_prefixes": (("git", "status"), ("pytest", "-q"))},
        {"write_deny_globs": ("*.secret",)},
        {"caps": P.ResourceCaps(timeout_seconds=3, output_max_bytes=512)},
        {"sandbox_backend": "bwrap"},
        {"readable_roots": ("/opt/data",)},
    ],
)
def test_the_three_hashes_are_one_hash(workspace: Path, changes: dict) -> None:
    profile = P.default_profile(workspace).with_changes(**changes)
    identity, in_argv, in_block = _hashes(profile)
    assert in_argv == identity, "the sandbox argv does not carry the profile's identity hash"
    assert in_block == identity, "the prompt block does not carry the profile's identity hash"
    assert P.hash_is_wellformed(identity)


def test_a_changed_profile_moves_both_consumers_together(workspace: Path) -> None:
    before = P.default_profile(workspace)
    after = before.with_changes(network="on", writable_paths=(str(workspace), "/srv/out"))
    for profile in (before, after):
        identity, in_argv, in_block = _hashes(profile)
        assert in_argv == in_block == identity
    assert after.identity_sha256 != before.identity_sha256
    assert P.permission_profile_hash_from_prompt(after.prompt_block()) != before.identity_sha256


def test_two_profiles_differing_in_one_field_cannot_share_a_hash(workspace: Path) -> None:
    base = P.default_profile(workspace)
    other = base.with_changes(writable_paths=(*base.writable_paths, "/srv/out"))
    assert base.identity_sha256 != other.identity_sha256
    assert P.permission_profile_hash_from_prompt(base.prompt_block()) != other.identity_sha256


def test_the_argv_carries_the_exact_json_the_hash_covers(workspace: Path) -> None:
    import hashlib

    profile = P.default_profile(workspace)
    argv = profile.sandbox_argv("true")
    payload = P.profile_from_argv(argv)
    assert payload
    recomputed = hashlib.sha256(
        __import__("json").dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assert recomputed == profile.identity_sha256


def _bwrap_works() -> bool:
    """A real probe, never an assumption — the run below claims real enforcement."""
    if not shutil.which("bwrap"):
        return False
    return bool(P.probe_backend(P.default_profile("/tmp"))["available"])


@pytest.mark.skipif(not _bwrap_works(), reason="bubblewrap is not usable on this host")
def test_a_real_run_carries_the_same_hash_the_agent_was_told(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    (workspace / ".git").mkdir(parents=True)
    profile = P.default_profile(workspace)
    run = P.run_in_sandbox(
        profile,
        'printf "%s" "$SKALDIR_PERMISSION_PROFILE_SHA256"',
        runtime=tmp_path / "rt",
    )
    assert run.sandboxed is True, run.output
    assert run.exit_code == 0, run.output
    told = P.permission_profile_hash_from_prompt(profile.prompt_block())
    from_argv = P.permission_profile_hash_from_argv(run.argv)
    from_child = run.output.strip()
    assert from_child == from_argv == told == profile.identity_sha256, (
        "the process that ran, the argv that started it and the block the agent read disagreed "
        "about the policy"
    )
    assert str(run.profile_sha256) == told
