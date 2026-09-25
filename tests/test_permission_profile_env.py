"""The environment policy lives **in the profile** — declared, hash-covered, enforced, told.

`MVP/43` gap 4, verbatim: *"the environment of a sandboxed child is the host's, scrubbed.
`tools_core.child_environment` applies the allowlist + credential scrub; the profile itself does
not own the env policy."* This file pins the closure:

* the profile carries an :class:`~skaldir.permission_profile.EnvPolicy`, it moves the identity
  hash, and it round-trips;
* the runner applies it, and a secret in the parent's environment does **not** reach the child —
  while the same variable is readable on the host, which is the control;
* the credential rule names a dropped variable and never its value;
* the two copies of the allowlist (the core tools' and the profile's) cannot drift apart;
* the prompt block tells the agent what it will be given.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from skaldir import permission_profile as P

SECRET_NAME = "SKALDIR_TEST_API_KEY"
SECRET_VALUE = "sk-do-not-leak-0192837465"


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    (root / ".git").mkdir(parents=True)
    return root


@pytest.fixture
def runtime(tmp_path: Path) -> Path:
    home = tmp_path / "rt"
    home.mkdir()
    return home


# ----------------------------------------------------------- one policy, two homes


def test_the_profile_allowlist_cannot_drift_from_the_core_tools() -> None:
    """Two literals, one policy: the profile's copy must stay a superset of the tools'."""
    from skaldir.tools_core import sandbox as tools_sandbox

    assert set(tools_sandbox.ENV_ALLOW) <= set(P.DEFAULT_ENV_ALLOW), (
        "the profile may add names (TOOL_WORKSPACE, the runtime-home markers) but must never "
        "keep fewer than the tools expect"
    )
    assert set(tools_sandbox.ENV_DROP) <= set(P.DEFAULT_ENV_DROP)
    assert P.DEFAULT_ENV_DENY_PATTERN == tools_sandbox.ENV_DENY_PATTERN, (
        "the credential pattern is one pattern; a second copy that is slightly weaker is how a "
        "secret leaks through the policy that was supposed to stop it"
    )
    assert set(P.DEFAULT_ENV_ALLOW) - set(tools_sandbox.ENV_ALLOW) == {
        "TOOL_WORKSPACE",
        "PIP_REQUIRE_VIRTUALENV",
    }


# ------------------------------------------------------------------ the rules


def test_the_allowlist_keeps_what_is_declared_and_drops_the_rest() -> None:
    policy = P.EnvPolicy(allow=("PATH", "HOME"))
    env, report = policy.apply(
        {"PATH": "/bin", "HOME": "/root", "AWS_PROFILE": "x", "EDITOR": "vim"}
    )
    assert env == {"PATH": "/bin", "HOME": "/root"}
    assert report["kept"] == ["HOME", "PATH"]
    assert report["dropped_unlisted"] == ["AWS_PROFILE", "EDITOR"]
    assert report["parent_names"] == 4
    assert report["policy"].endswith("(declared in the profile)")


def test_a_credential_is_dropped_and_named_but_its_value_is_never_read() -> None:
    policy = P.EnvPolicy(allow=("PATH", SECRET_NAME))
    env, report = policy.apply({"PATH": "/bin", SECRET_NAME: SECRET_VALUE})
    assert SECRET_NAME not in env, "an allowlist entry must not beat the credential rule"
    assert SECRET_NAME in report["dropped_credentials"]
    assert SECRET_VALUE not in json.dumps(report), "a dropped value must never be in the report"


def test_the_drop_list_beats_everything_including_an_explicit_set() -> None:
    policy = P.EnvPolicy(allow=("PATH",), drop=("PYTHONPATH",), set=(("PYTHONPATH", "/evil"),))
    env, report = policy.apply({"PATH": "/bin", "PYTHONPATH": "/tmp/other-checkout/src"})
    assert "PYTHONPATH" not in env, (
        "a declared set value must not be able to re-introduce a name the drop list removes — "
        "a leaked PYTHONPATH silently makes a child import another tree"
    )
    assert "PYTHONPATH" in report["dropped_by_policy"]
    assert "PYTHONPATH" in report["set_refused"], (
        "the contradiction is reported, so a profile that declares both is visible rather than "
        "silently resolved"
    )


def test_inherit_false_is_strict_and_set_wins() -> None:
    policy = P.EnvPolicy(inherit=False, allow=("PATH",), set=(("TOOL_MODE", "strict"),))
    env, _report = policy.apply({"PATH": "/bin", "HOME": "/root"})
    assert env["PATH"] == "/bin"
    assert env["TOOL_MODE"] == "strict"
    assert "HOME" not in env
    assert policy.summary().startswith("strict allowlist")


def test_a_forced_name_is_always_set_from_the_parent_when_present() -> None:
    policy = P.EnvPolicy(allow=("PATH",))
    env, report = policy.apply({"PATH": "/bin", "TOOL_WORKSPACE": "/srv/x"})
    assert env["TOOL_WORKSPACE"] == "/srv/x"
    assert "TOOL_WORKSPACE" in report["set"]


def test_the_env_policy_is_a_profile_field_that_moves_the_hash(workspace: Path) -> None:
    base = P.default_profile(workspace)
    assert "env_policy" in P.enforcement_matrix_fields()
    changed = [
        base.with_changes(env_policy=P.EnvPolicy(allow=("PATH",))),
        base.with_changes(env_policy=P.EnvPolicy(inherit=False)),
        base.with_changes(env_policy=P.EnvPolicy(drop=("PATH",))),
        base.with_changes(env_policy=P.EnvPolicy(deny_patterns=("(?i)secret",))),
        base.with_changes(env_policy=P.EnvPolicy(set=(("MODE", "x"),))),
    ]
    for other in changed:
        assert other.identity_sha256 != base.identity_sha256
        assert other.canonical_payload()["env_policy"] != base.canonical_payload()["env_policy"]
    for name in changed[0].canonical_payload()["env_policy"]:
        assert name in changed[0].to_dict()["env_policy"]


def test_the_env_policy_round_trips_through_the_profile_json(workspace: Path) -> None:
    profile = P.default_profile(workspace).with_changes(
        env_policy=P.EnvPolicy(
            inherit=False,
            allow=("PATH", "LANG"),
            drop=("PYTHONPATH",),
            deny_patterns=("(?i)token",),
            set=(("MODE", "strict"),),
        )
    )
    restored = P.PermissionProfile.from_dict(json.loads(profile.to_json()))
    assert restored.env_policy == profile.env_policy
    assert restored.identity_sha256 == profile.identity_sha256
    assert restored.child_environment({"PATH": "/bin"})[0]["MODE"] == "strict"


def test_the_child_environment_forces_the_workspace_contract(workspace: Path) -> None:
    env, report = P.default_profile(workspace).child_environment({"PATH": "/bin"})
    assert env["TOOL_WORKSPACE"] == str(workspace.resolve())
    assert report["workspace"] == str(workspace.resolve())
    assert "TOOL_WORKSPACE" in report["set"]


def test_the_prompt_block_states_the_policy_without_quoting_any_value(workspace: Path) -> None:
    profile = P.default_profile(workspace)
    block = profile.prompt_block()
    assert "environment: allowlist" in block
    assert "PYTHONPATH" in block, "the dropped-by-policy names are part of what the agent is told"
    assert "credential-shaped name is dropped and named" in block
    assert SECRET_VALUE not in block
    row = next(
        item for item in profile.enforcement_matrix() if item["field"] == "env_policy"
    )
    assert row["enforced_by"] == "runner"
    assert "never read" in row["detail"]


# ------------------------------------------------------------- the runner applies it


def _bwrap_works() -> bool:
    from skaldir import security_linux as S

    if not P.default_profile("/tmp").mount_binary():
        return S.landlock_abi() >= S.LANDLOCK_ABI_MIN
    return bool(P.probe_backend(P.default_profile("/tmp"))["available"])


@pytest.mark.skipif(not _bwrap_works(), reason="no enforcement mechanism is usable here")
def test_a_secret_in_the_parent_environment_does_not_reach_the_child(
    workspace: Path, runtime: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The control is the host read: the same variable *is* there for us, and absent inside."""
    monkeypatch.setenv(SECRET_NAME, SECRET_VALUE)
    monkeypatch.setenv("SKALDIR_LEAK_CANARY", "plain-unlisted")
    assert os.environ[SECRET_NAME] == SECRET_VALUE
    script = (
        "import json, os\n"
        "print(json.dumps({k: k in os.environ for k in "
        f"['{SECRET_NAME}', 'SKALDIR_LEAK_CANARY', 'PYTHONPATH', 'TOOL_WORKSPACE']}}))\n"
    )
    profile = P.default_profile(workspace)
    run = P.run_in_sandbox(profile, "python3 -c \"" + script.strip() + "\"", runtime=runtime)
    assert run.exit_code == 0, run.output
    seen = json.loads(run.output.strip().splitlines()[-1])
    assert seen[SECRET_NAME] is False, "a credential name reached the sandbox"
    assert seen["SKALDIR_LEAK_CANARY"] is False, "an unlisted name reached the sandbox"
    assert seen["PYTHONPATH"] is False
    assert seen["TOOL_WORKSPACE"] is True, "the tool contract must still be there"
    assert SECRET_VALUE not in json.dumps(run.to_dict()), (
        "the run payload must not carry a secret either"
    )
    assert SECRET_NAME in run.env_report["dropped_credentials"]
    assert SECRET_VALUE not in json.dumps(run.env_report)


@pytest.mark.skipif(not _bwrap_works(), reason="no enforcement mechanism is usable here")
def test_a_profile_set_value_reaches_the_child_without_appearing_in_the_argv(
    workspace: Path, runtime: Path
) -> None:
    profile = P.default_profile(workspace).with_changes(
        env_policy=P.EnvPolicy(set=(("SKALDIR_RUN_MODE", "measured"),))
    )
    run = P.run_in_sandbox(
        profile, "python3 -c \"import os;print(os.environ.get('SKALDIR_RUN_MODE'))\"",
        runtime=runtime,
    )
    assert run.exit_code == 0, run.output
    assert run.output.strip() == "measured"
    joined = " ".join(run.argv)
    assert "SKALDIR_RUN_MODE=measured" not in joined, (
        "the value must not be passed as an environment pair on the command line — it goes "
        "through the process environment"
    )
    assert joined.count("measured") == 2, (
        "and it appears only inside the profile JSON, which the argv carries twice on purpose "
        "(bubblewrap's --setenv and the launcher's --permission-profile)"
    )


def test_an_inline_secret_in_the_profile_is_refused_at_construction(workspace: Path) -> None:
    """The profile travels through the argv, so a secret may be *named*, never *carried*."""
    with pytest.raises(ValueError, match="readable by every process"):
        P.EnvPolicy(set=((SECRET_NAME, SECRET_VALUE),))
    with pytest.raises(ValueError, match="readable by every process"):
        P.default_profile(workspace).with_changes(
            env_policy=P.EnvPolicy(set=(("MY_TOKEN", "hunter2"),))
        )
    # the named-not-carried form is the supported one and it reaches the child
    policy = P.EnvPolicy(allow=("PATH", SECRET_NAME))
    env, report = policy.apply({"PATH": "/bin", SECRET_NAME: SECRET_VALUE})
    assert SECRET_NAME not in env and SECRET_NAME in report["dropped_credentials"], (
        "an allowed *name* is still a credential: the value must come from the parent environment "
        "of a process the agent cannot read"
    )


def test_the_podman_mirror_passes_names_and_never_values(workspace: Path) -> None:
    profile = P.default_profile(workspace)
    argv = profile.podman_argv("true", image="img")
    env_args = [argv[index + 1] for index, item in enumerate(argv) if item == "--env"]
    assert all("=" not in item or item.startswith("SKALDIR_PERMISSION_") for item in env_args), (
        f"only the profile's own non-secret variables may carry a value in the argv: {env_args}"
    )
    assert "PATH" in env_args and "HOME" in env_args


def test_the_launcher_drops_a_pythonpath_that_would_import_another_tree(
    workspace: Path, runtime: Path
) -> None:
    """The measured trap this repository has hit: `PYTHONPATH` shadowing the checkout."""
    other = workspace.parent / "other-checkout"
    (other / "skaldir").mkdir(parents=True)
    (other / "skaldir" / "__init__.py").write_text("BROKEN = True\n", encoding="utf-8")
    completed = subprocess.run(  # noqa: S603 — fixed argv
        [
            sys.executable,
            "-c",
            "import json, os; print(json.dumps({'PYTHONPATH': os.environ.get('PYTHONPATH')}))",
        ],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PYTHONPATH": str(other)},
    )
    assert "other-checkout" in completed.stdout
    profile = P.default_profile(workspace)
    env, report = profile.child_environment({**os.environ, "PYTHONPATH": str(other)})
    assert "PYTHONPATH" not in env
    assert "PYTHONPATH" in report["dropped_by_policy"]
