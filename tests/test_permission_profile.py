"""The permission profile as ONE object: shape, identity hash, argv, prompt block.

The point of these tests is the *object*, not a flag list: the profile is serializable, its
identity hash is taken over the canonical JSON that is handed to the sandbox, and both the
sandbox argv and the agent-visible prompt block are rendered from it. Anything that can drift
between those two consumers is a bug this file is here to catch.

The real-sandbox half (an actual ``bwrap`` run proving the writable set, the read-only
protected subpaths and the cut network) lives in ``test_permission_profile_sandbox.py``; the
non-drift property has its own file.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from skaldir import permission_profile as P


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    (root / ".git").mkdir(parents=True)
    (root / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (root / "readme.md").write_text("hello\n", encoding="utf-8")
    return root


@pytest.fixture
def runtime(tmp_path: Path) -> Path:
    home = tmp_path / "rt"
    home.mkdir()
    return home


# ------------------------------------------------------------------ shape/hash


def test_defaults_are_the_safe_ones(workspace: Path) -> None:
    profile = P.default_profile(workspace)
    assert profile.network == "off", "a profile that forgets the network gets no network"
    assert profile.writable_paths == (str(workspace),)
    assert profile.read_policy == "full"
    assert ".git" in profile.protected_paths
    assert profile.caps.timeout_seconds > 0
    assert profile.caps.output_max_bytes > 0


def test_an_empty_writable_set_writes_nowhere(workspace: Path) -> None:
    profile = P.default_profile(workspace).with_changes(writable_paths=())
    argv = profile.sandbox_argv("true")
    assert "--bind" not in argv, "an empty writable set must not bind anything writable"
    assert "(nothing" in profile.prompt_block()


def test_identity_hash_is_the_hash_of_the_canonical_json(workspace: Path) -> None:
    import hashlib

    profile = P.default_profile(workspace)
    expected = hashlib.sha256(profile.canonical_json().encode("utf-8")).hexdigest()
    assert profile.identity_sha256 == expected
    assert P.hash_is_wellformed(profile.identity_sha256)


def test_the_identity_hash_is_stable_across_object_identity(workspace: Path) -> None:
    a = P.default_profile(workspace)
    b = P.default_profile(workspace)
    assert a is not b
    assert a.identity_sha256 == b.identity_sha256


def test_canonical_json_is_sorted_and_compact(workspace: Path) -> None:
    raw = P.default_profile(workspace).canonical_json()
    assert " " not in raw.replace('"', ""), "no whitespace: the bytes are the hash input"
    assert json.loads(raw) == json.loads(raw)
    keys = list(json.loads(raw))
    assert keys == sorted(keys)


@pytest.mark.parametrize(
    "change",
    [
        {"network": "on"},
        {"read_policy": "roots"},
        {"writable_paths": ("/tmp/other",)},
        {"protected_paths": ()},
        {"allowed_subprocess_prefixes": (("git", "status"),)},
        {"caps": P.ResourceCaps(timeout_seconds=5)},
        {"sandbox_backend": "podman"},
        {"write_deny_globs": ("*.secret",)},
        {"name": "other"},
    ],
)
def test_every_declared_field_moves_the_identity_hash(workspace: Path, change: dict) -> None:
    base = P.default_profile(workspace)
    moved = base.with_changes(**change)
    assert moved.identity_sha256 != base.identity_sha256, (
        f"changing {sorted(change)} did not move the identity hash — the field is not part of "
        f"the identity"
    )
    assert base.diff(moved) == sorted(change)


def test_a_change_that_changes_nothing_does_not_move_the_hash(workspace: Path) -> None:
    base = P.default_profile(workspace)
    same = base.with_changes(network="off")
    assert same.identity_sha256 == base.identity_sha256
    assert same.diff(base) == []


@pytest.mark.parametrize(
    ("field", "value"),
    [("network", "maybe"), ("read_policy", "some"), ("sandbox_backend", "chroot")],
)
def test_a_declared_enum_refuses_an_unknown_value(workspace: Path, field: str, value: str) -> None:
    with pytest.raises(ValueError):
        P.default_profile(workspace).with_changes(**{field: value})


def test_dict_round_trip_is_lossless(workspace: Path) -> None:
    profile = P.default_profile(workspace).with_changes(
        network="on", allowed_subprocess_prefixes=(("git", "status"), ("pytest",))
    )
    restored = P.PermissionProfile.from_dict(profile.to_dict())
    assert restored.identity_sha256 == profile.identity_sha256
    assert restored.canonical_payload() == profile.canonical_payload()


def test_stored_profiles_round_trip_and_are_listed(runtime: Path, workspace: Path) -> None:
    profile = P.default_profile(workspace, name="strict").with_changes(network="off")
    path = P.save_profile(profile, runtime)
    assert path.is_file()
    assert P.profile_names(runtime) == ["strict"]
    loaded = P.load_profile(runtime, "strict")
    assert loaded is not None and loaded.identity_sha256 == profile.identity_sha256
    assert P.load_profile(runtime, "absent") is None


def test_effective_profile_prefers_the_stored_one(runtime: Path, workspace: Path) -> None:
    strict = P.default_profile(workspace, name="p").with_changes(network="off")
    P.save_profile(strict, runtime)
    stored = P.effective_profile(runtime, workspace, name="p")
    assert stored.network == "off"


def test_environment_override_wins_over_builtin(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKALDIR_PERMISSION_NETWORK", "on")
    profile = P.profile_from_env(workspace)
    assert profile.network == "on"
    monkeypatch.setenv("SKALDIR_PERMISSION_NETWORK", "nonsense")
    assert P.profile_from_env(workspace).network == "off", "an invalid override is ignored"


def test_the_legacy_env_spelling_still_works(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SKALDIR_PERMISSION_NETWORK", raising=False)
    monkeypatch.setenv("GODSV_PERMISSION_NETWORK", "on")
    assert P.profile_from_env(workspace).network == "on"


# --------------------------------------------------------------- argv / block


def test_sandbox_argv_is_a_real_bwrap_invocation(workspace: Path) -> None:
    profile = P.default_profile(workspace)
    argv = profile.sandbox_argv("echo hi", cwd=workspace)
    assert argv[0].endswith("bwrap")
    for flag in (
        "--die-with-parent",
        "--unshare-user",
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-net",
        "--cap-drop",
        "--proc",
        "--dev",
    ):
        assert flag in argv, f"{flag} is missing from the sandbox argv"
    assert argv[-3:] == ["/bin/sh", "-c", "echo hi"]
    assert argv[argv.index("--bind") + 1 : argv.index("--bind") + 3] == [
        str(workspace),
        str(workspace),
    ]


def test_network_on_drops_the_unshare_flag(workspace: Path) -> None:
    off = P.default_profile(workspace).sandbox_argv("true")
    on = P.default_profile(workspace).with_changes(network="on").sandbox_argv("true")
    assert "--unshare-net" in off
    assert "--unshare-net" not in on


def test_the_protected_bind_comes_after_the_writable_bind(workspace: Path) -> None:
    """Mount order is the enforcement: the later read-only bind wins over the writable one."""
    argv = P.default_profile(workspace).sandbox_argv("true")
    writable_at = argv.index("--bind")
    protected_at = argv.index("--ro-bind", writable_at)
    assert argv[protected_at + 1] == str(workspace / ".git")


def test_read_policy_roots_starts_from_an_empty_root(workspace: Path) -> None:
    argv = P.default_profile(workspace).with_changes(read_policy="roots").sandbox_argv("true")
    assert "--tmpfs" in argv
    assert ["--tmpfs", "/"] == argv[argv.index("--tmpfs") : argv.index("--tmpfs") + 2], (
        "a restricted-read policy starts from an empty root, not from the host filesystem"
    )
    full_bind = [
        index
        for index in range(len(argv) - 2)
        if argv[index : index + 3] == ["--ro-bind", "/", "/"]
    ]
    assert full_bind == [], "no full-disk read bind under 'roots'"
    # the readable set is what the `--ro-bind-try` elements name — not the whole argv, which now
    # also carries the enforcement launcher's own absolute paths (they are a *named* exception,
    # asserted separately below).
    readable = [argv[index + 1] for index, item in enumerate(argv) if item == "--ro-bind-try"]
    assert "/usr" in readable and "/etc" in readable, "platform default roots are readable"
    declared = set(P.PLATFORM_DEFAULT_READ_ROOTS) | {str(workspace)}
    roots_profile = P.default_profile(workspace).with_changes(read_policy="roots")
    exceptions = set(profile_exceptions(roots_profile))
    assert set(readable) <= declared | exceptions, (
        "the readable set is the platform defaults plus the declared roots — nothing else, bar the "
        f"named launcher exception: {sorted(set(readable) - declared - exceptions)}"
    )
    assert not [item for item in readable if item == "/home" or item == "/home/willy"], (
        "user data is not a platform default"
    )


def profile_exceptions(profile: P.PermissionProfile) -> list[str]:
    """Readable paths the plan adds beyond the declared readable set (the launcher's two files)."""
    return [
        path
        for path in profile.security_plan().read_only_paths
        if path not in {"/", *profile.readable_absolute()}
    ]


def test_the_launcher_exception_under_roots_is_named_not_smuggled(workspace: Path) -> None:
    """A `roots` profile still has to be able to *apply* itself, and says what that costs.

    The enforcement launcher and its interpreter live outside the declared read roots, so they are
    bound read-only — that is two files added to the readable set, and the plan names them instead
    of widening the masked root silently.
    """
    profile = P.default_profile(workspace).with_changes(read_policy="roots")
    plan = profile.security_plan()
    exceptions = [
        path for path in plan.read_only_paths if path not in profile.readable_absolute()
    ]
    assert exceptions == sorted(exceptions) or exceptions
    assert P.launcher_python() in exceptions and P.launcher_path() in exceptions
    assert any("enforcement launcher itself" in note for note in plan.notes), plan.notes
    argv = profile.sandbox_argv("true")
    for path in exceptions:
        assert path in argv, f"{path} is readable in the plan but not bound in the argv"
    assert P.default_profile(workspace).security_plan().notes == [] or all(
        "enforcement launcher" not in note
        for note in P.default_profile(workspace).security_plan().notes
    ), "under read_policy=full nothing needs the exception"



def test_podman_argv_mirrors_the_same_policy(workspace: Path) -> None:
    profile = P.default_profile(workspace)
    argv = profile.podman_argv("echo hi", cwd=workspace)
    assert argv[0].endswith("podman") and argv[1] == "run"
    assert "--read-only" in argv
    assert argv[argv.index("--network") + 1] == "none"
    volumes = [argv[index + 1] for index, item in enumerate(argv) if item == "-v"]
    assert f"{workspace}:{workspace}:rw" in volumes
    assert f"{workspace / '.git'}:{workspace / '.git'}:ro" in volumes


def test_the_prompt_block_tells_the_agent_its_limits(workspace: Path) -> None:
    profile = P.default_profile(workspace).with_changes(
        network="off", allowed_subprocess_prefixes=(("pytest",), ("git", "status"))
    )
    block = profile.prompt_block()
    assert f"profile sha256 {profile.identity_sha256}" in block
    assert str(workspace) in block
    assert "network: off" in block
    assert "pytest; git status" in block
    assert ".git" in block
    assert "cannot be decomposed" in block
    assert "timeout" in block


def test_the_prompt_section_is_what_the_composer_would_take(workspace: Path) -> None:
    section = P.default_profile(workspace).prompt_section()
    assert section["name"] == "permissions"
    assert section["title"] == "Permissions"
    assert section["text"] == P.default_profile(workspace).prompt_block()
    assert section["source"].startswith("skaldir.permission_profile:")


def test_hash_extraction_reads_both_consumers(workspace: Path) -> None:
    profile = P.default_profile(workspace)
    argv = profile.sandbox_argv("true")
    assert P.permission_profile_hash_from_argv(argv) == profile.identity_sha256
    assert P.permission_profile_hash_from_prompt(profile.prompt_block()) == profile.identity_sha256
    assert P.permission_profile_hash_from_argv(["bwrap", "/bin/true"]) == ""
    assert P.permission_profile_hash_from_prompt("no hash here") == ""


def test_a_forged_argv_hash_is_refused(workspace: Path) -> None:
    """An argv that *claims* a hash it does not carry is exactly the drift we forbid."""
    profile = P.default_profile(workspace)
    argv = [
        item if item != profile.identity_sha256 else "0" * 64
        for item in profile.sandbox_argv("true")
    ]
    assert P.permission_profile_hash_from_argv(argv) == "", (
        "the claimed hash and the re-hashed profile JSON disagreed, so neither may be trusted"
    )


def test_profile_from_argv_reads_the_json_back(workspace: Path) -> None:
    profile = P.default_profile(workspace)
    payload = P.profile_from_argv(profile.sandbox_argv("true"))
    assert payload["network"] == "off"
    assert P.PermissionProfile.from_dict(payload).identity_sha256 == profile.identity_sha256


# ------------------------------------------------------------- honest matrix


def test_the_enforcement_matrix_covers_every_declared_field() -> None:
    """A field with no row is a field nobody can account for."""
    rows = {row["field"].split(".")[0] for row in P.default_profile("/tmp").enforcement_matrix()}
    missing = [item for item in P.enforcement_matrix_fields() if item not in rows]
    assert not missing, f"no enforcement row for {missing}"


def test_the_matrix_names_where_each_field_is_enforced() -> None:
    """Every field names its *real* owner — and the paths are owned twice when they can be."""
    rows = {row["field"]: row for row in P.default_profile("/tmp").enforcement_matrix()}
    assert rows["writable_paths"]["enforced_by"] == "bwrap argv + landlock"
    assert rows["network"]["enforced_by"] == "bwrap argv + landlock (net) + seccomp"
    assert rows["network_egress_ports"]["enforced_by"] == "metadata"
    assert rows["caps.timeout_seconds"]["enforced_by"] == "runner"
    assert rows["caps.output_max_bytes"]["enforced_by"] == "runner"
    assert rows["allowed_subprocess_prefixes"]["enforced_by"] == "policy module"
    assert rows["env_policy"]["enforced_by"] == "runner"
    assert rows["linux_security"]["enforced_by"] == "runner"


def test_an_unenforceable_field_is_reported_as_unenforced_and_not_hidden() -> None:
    """The row that used to say "bubblewrap has no pid cap" now says *why nothing is capped*.

    The old assertion pinned the gap: ``caps.max_processes`` was documentation only. The gap is
    closed (cgroup v2 ``pids.max``), so the honest row moved to the one case that is still real —
    a profile that declares **no** cap — and a second test below proves the declared case is
    enforced by the kernel rather than described.
    """
    profile = P.default_profile("/tmp")
    unenforced = profile.unenforced_fields()
    assert "caps.max_processes" in unenforced, (
        "no cap is declared, so nothing may claim a process ceiling"
    )
    row = next(
        item for item in profile.enforcement_matrix() if item["field"] == "caps.max_processes"
    )
    assert row["enforced_by"] == "unenforced"
    assert "no cap is declared" in row["detail"]
    assert "cgroup v2 pids" in row["detail"], (
        "the row must name the mechanism that *would* enforce it, not just say no"
    )


def test_a_declared_process_cap_is_owned_by_the_cgroup_not_by_bubblewrap() -> None:
    """Declared ⇒ enforced, and the row names the kernel object that does it."""
    profile = P.default_profile("/tmp").with_changes(caps=P.ResourceCaps(max_processes=64))
    row = next(
        item for item in profile.enforcement_matrix() if item["field"] == "caps.max_processes"
    )
    if profile.pids_cgroup_mode()[0] == "enforce":
        assert row["enforced_by"] == "cgroup v2 pids"
        assert "pids.max" in row["detail"]
        assert "caps.max_processes" not in profile.unenforced_fields()
    else:  # a host without a delegated pids sub-tree must say so instead
        assert row["enforced_by"] == "unenforced"
        assert "not enforceable here" in row["detail"]
    assert "pids.max" not in " ".join(profile.sandbox_argv("true")), (
        "the cap is a cgroup, not an argv flag — the argv must not imply one"
    )


def test_a_declared_memory_cap_is_enforced_by_the_runner_not_claimed_in_the_argv() -> None:
    profile = P.default_profile("/tmp").with_changes(
        caps=P.ResourceCaps(max_memory_bytes=268435456)
    )
    row = next(
        item for item in profile.enforcement_matrix() if item["field"] == "caps.max_memory_bytes"
    )
    assert row["enforced_by"] == "runner"
    assert "--rlimit" not in " ".join(profile.sandbox_argv("true"))


# ------------------------------------------------------------------- backend


def test_backend_none_selects_no_binary(workspace: Path) -> None:
    profile = P.default_profile(workspace).with_changes(sandbox_backend="none")
    assert profile.backend_binary() == ""
    assert "bwrap" in profile.sandbox_argv("true")[0], (
        "the argv still shows what *would* run — the backend only governs resolution"
    )


def test_the_probe_reports_the_truth_about_this_host(workspace: Path) -> None:
    profile = P.default_profile(workspace)
    report = P.probe_backend(profile)
    assert set(report) >= {"backend", "binary", "available", "probe_argv", "probe_output"}
    if report["binary"]:
        assert report["probe_argv"], "a resolved backend must carry the profile's own argv"
    else:
        assert report["available"] is False


def test_no_backend_means_no_silent_unprotected_run(workspace: Path) -> None:
    """``sandbox_backend = 'none'`` removes the *binary*; the kernel controls still decide.

    Two independent facts, and both are asserted: turning the binary backend off does **not**
    silently drop to unprotected (Landlock still carries the declared set — the mechanism says
    ``landlock``), and turning the kernel controls off too leaves no mechanism at all, which is
    the case that must fail closed.
    """
    without_bwrap = P.default_profile(workspace).with_changes(sandbox_backend="none")
    assert without_bwrap.mechanism()[0] == P._linux.MECHANISM_LANDLOCK, (
        "no bubblewrap must not mean no sandbox while Landlock is available"
    )
    protected = P.run_in_sandbox(
        without_bwrap.with_changes(caps=P.ResourceCaps(timeout_seconds=20)),
        "echo should-not-run; echo $SKALDIR_PERMISSION_PROFILE_SHA256",
        runtime=workspace,
    )
    assert protected.sandboxed is True and protected.blocked is False
    assert "landlock" in protected.controls_enforced, protected.to_dict()

    nothing_left = without_bwrap.with_changes(
        linux_security=P.LinuxSecurityPolicy(landlock="off", seccomp="off")
    )
    assert nothing_left.mechanism()[0] == P._linux.MECHANISM_NONE
    run = P.run_in_sandbox(nothing_left, "echo should-not-run", runtime=workspace)
    assert run.blocked is True
    assert run.exit_code == 3
    assert "fail-closed" in run.blocked_reason
    assert "should-not-run" not in run.output


def test_shlex_join_quotes_every_element(workspace: Path) -> None:
    line = P.shlex_join(["bwrap", "--setenv", "A", '{"a": 1}'])
    assert line.startswith("bwrap --setenv A ")
    assert "'{\"a\": 1}'" in line
