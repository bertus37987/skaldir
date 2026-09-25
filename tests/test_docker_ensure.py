"""DOCKER-ENSURE — the state machine, the guards, the CLI verb and the two hooks.

The capability's whole value is that it is *honest*: it starts nothing privileged,
runs no container, never hangs on a dead socket, and reports exit 3 with a literal
command when no password-free path answers. So the tests pin exactly those
properties — with injected probers, so no daemon is needed and no container is
created by the suite:

* the state machine's four outcomes (running by probe · running by socket
  activation · running by rootless podman · blocked) and their exit codes,
* the ``guard_argv`` refusals: a privileged binary and every non-probe verb,
* the bounded timeout: a runtime that hangs is reported, not waited on,
* the readiness poll after activation (a just-activated daemon is *starting*),
* the selftest probe and its install hook (idempotent, honest status),
* the CLI verb: standalone JSON + the wired ``cli_contract`` envelope and exit 3,
* the agent tool: contract, bridge entrypoint, blocked ⇒ exit 3.
"""

from __future__ import annotations

import argparse
import json
import stat
import subprocess
import time
from pathlib import Path

import pytest

from skaldir import docker_ensure as DE

# --------------------------------------------------------------------- helpers


def reachable(provider: str = DE.PROVIDER_DOCKER, version: str = "1.2.3",
              driver: str = "overlayfs", *, rootless: bool | None = None) -> DE.RuntimeProbe:
    """A probe answer that says "a runtime is up" — no process was started."""
    return DE.RuntimeProbe(
        provider=provider,
        reachable=True,
        detail=f"{provider} {version} answered (storage driver {driver})",
        binary=f"/usr/bin/{provider}",
        server_version=version,
        storage_driver=driver,
        rootless=rootless,
        commands=(f"/usr/bin/{provider} info --format '{{{{json .}}}}'",),
    )


def unreachable(provider: str = DE.PROVIDER_DOCKER, detail: str = "no daemon answered"):
    return DE.RuntimeProbe(provider=provider, reachable=False, detail=detail,
                           binary=f"/usr/bin/{provider}")


class FakeProber:
    """A prober that hands out a scripted list of answers (the last one repeats)."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = 0

    def __call__(self, *, timeout_s: float = 5.0, docker_bin=None, environ=None, **kwargs):
        index = min(self.calls, len(self.answers) - 1)
        self.calls += 1
        return self.answers[index]


class FakeActivator:
    def __init__(self, ok: bool = True, detail: str = "connected — activation triggered"):
        self.ok = ok
        self.detail = detail
        self.calls = 0
        self.socket_paths: list[str] = []

    def __call__(self, *, socket_path: str | None = None, timeout_s: float = 5.0):
        self.calls += 1
        self.socket_paths.append(str(socket_path))
        return DE.Attempt(
            path=DE.PATH_SOCKET_ACTIVATION,
            ok=self.ok,
            detail=self.detail,
            commands=(f"connect({socket_path})",),
        )


class FakeSleep:
    """A waiter that records the intervals instead of sleeping."""

    def __init__(self):
        self.waits: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.waits.append(float(seconds))


def fake_binary(tmp_path: Path, name: str, body: str) -> Path:
    """A stand-in runtime CLI, so the real probe path is exercised without a daemon."""
    path = tmp_path / name
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


# -------------------------------------------------- 1. the state machine (fakes)


def test_running_by_probe_needs_no_start_path(tmp_path, monkeypatch):
    monkeypatch.setenv("SKALDIR_DOCKER_SOCKET", str(tmp_path / "docker.sock"))
    activator = FakeActivator()
    report = DE.ensure(
        prober=FakeProber(reachable(version="29.6.1")),
        podman_prober=FakeProber(unreachable(DE.PROVIDER_PODMAN)),
        activator=activator,
    )
    assert report.state == DE.STATE_RUNNING
    assert report.provider == DE.PROVIDER_DOCKER
    assert report.answered_by == DE.PATH_PROBE
    assert report.server_version == "29.6.1"
    assert report.storage_driver == "overlayfs"
    assert report.exit_code == DE.EXIT_OK == 0
    assert report.attempts == ()
    assert activator.calls == 0, "a running daemon must not be re-started"
    assert report.exact_command == ""


def test_socket_activation_path_answers_after_the_readiness_poll(tmp_path, monkeypatch):
    """The measured case: the connect returns before the daemon serves, so poll."""
    monkeypatch.setenv("SKALDIR_DOCKER_SOCKET", str(tmp_path / "docker.sock"))
    (tmp_path / "docker.sock").touch()
    prober = FakeProber(unreachable(), unreachable(), reachable(version="30.0.0"))
    activator = FakeActivator()
    sleep = FakeSleep()
    report = DE.ensure(
        prober=prober, podman_prober=FakeProber(unreachable(DE.PROVIDER_PODMAN)),
        activator=activator, sleep=sleep, ready_s=2.0,
    )
    assert report.state == DE.STATE_RUNNING
    assert report.answered_by == DE.PATH_SOCKET_ACTIVATION
    assert report.server_version == "30.0.0"
    assert report.exit_code == 0
    assert prober.calls == 3, "one probe before, two during the readiness wait"
    assert sleep.waits == [DE.ACTIVATION_POLL_S]
    attempt = report.attempts[0]
    assert attempt.path == DE.PATH_SOCKET_ACTIVATION and attempt.ok
    assert attempt.polls == 2 and attempt.readiness_ms >= 0
    assert "answered after" in attempt.detail
    assert activator.calls == 1


def test_rootless_podman_is_the_declared_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("SKALDIR_DOCKER_SOCKET", str(tmp_path / "missing.sock"))
    report = DE.ensure(
        prober=FakeProber(unreachable()),
        podman_prober=FakeProber(reachable(DE.PROVIDER_PODMAN, "5.8.1", "overlay", rootless=True)),
        activator=FakeActivator(ok=False, detail="no socket at …"),
        sleep=FakeSleep(),
    )
    assert report.state == DE.STATE_RUNNING
    assert report.provider == DE.PROVIDER_PODMAN
    assert report.answered_by == DE.PATH_ROOTLESS_PODMAN
    assert report.rootless is True
    assert report.server_version == "5.8.1"
    assert report.exit_code == 0
    assert [attempt.path for attempt in report.attempts] == [
        DE.PATH_SOCKET_ACTIVATION, DE.PATH_ROOTLESS_PODMAN
    ]
    assert any("podman answered" in note for note in report.notes)


def test_nothing_answers_is_blocked_with_the_exact_command(tmp_path, monkeypatch):
    monkeypatch.setenv("SKALDIR_DOCKER_SOCKET", str(tmp_path / "missing.sock"))
    report = DE.ensure(
        prober=FakeProber(unreachable(detail="daemon down")),
        podman_prober=FakeProber(unreachable(DE.PROVIDER_PODMAN, detail="podman down")),
        activator=FakeActivator(ok=False, detail="dead socket"),
        sleep=FakeSleep(),
    )
    assert report.state == DE.STATE_UNAVAILABLE
    assert report.ok is False
    assert report.exit_code == DE.EXIT_BLOCKED == 3
    assert report.exact_command == "sudo systemctl start docker"
    assert "daemon down" in report.reason and "podman down" in report.reason
    assert [item["command"] for item in report.remediation][0] == report.exact_command
    assert report.socket.endswith("missing.sock") and report.socket_exists is False
    assert report.container_ops_attempted == 0


def test_the_declared_order_is_the_order_that_runs(tmp_path, monkeypatch):
    monkeypatch.setenv("SKALDIR_DOCKER_SOCKET", str(tmp_path / "missing.sock"))
    activator = FakeActivator(ok=False)
    report = DE.ensure(
        prober=FakeProber(unreachable()),
        podman_prober=FakeProber(reachable(DE.PROVIDER_PODMAN, "5.8.1", "overlay", rootless=True)),
        activator=activator,
        paths=(DE.PATH_ROOTLESS_PODMAN, DE.PATH_SOCKET_ACTIVATION),
        sleep=FakeSleep(),
    )
    assert report.state == DE.STATE_RUNNING and report.provider == DE.PROVIDER_PODMAN
    assert activator.calls == 0, "podman was first, so the socket was never touched"
    assert [attempt.path for attempt in report.attempts] == [DE.PATH_ROOTLESS_PODMAN]
    assert list(report.paths) == [DE.PATH_ROOTLESS_PODMAN, DE.PATH_SOCKET_ACTIVATION]


def test_no_declared_path_attempts_nothing_and_still_reports_honestly(tmp_path, monkeypatch):
    monkeypatch.setenv("SKALDIR_DOCKER_SOCKET", str(tmp_path / "missing.sock"))
    activator = FakeActivator()
    report = DE.ensure(prober=FakeProber(unreachable()), paths=(), activator=activator)
    assert report.state == DE.STATE_UNAVAILABLE and report.exit_code == 3
    assert report.attempts == ()
    assert list(report.paths) == []
    assert activator.calls == 0
    assert report.exact_command == DE.PRIMARY_EXACT_COMMAND
    assert any("no start path was declared" in note for note in report.notes)


def test_no_start_is_read_only(tmp_path, monkeypatch):
    """``start=False`` (and the selftest probe) never touches a start path."""
    monkeypatch.setenv("SKALDIR_DOCKER_SOCKET", str(tmp_path / "missing.sock"))
    down = lambda **kw: unreachable()  # noqa: E731 — a scripted answer, not a helper
    activator = FakeActivator()
    report = DE.ensure(start=False, prober=down, podman_prober=down, activator=activator)
    status = DE.runtime_status(prober=down, podman_prober=down)
    assert report.state == status.state == DE.STATE_UNAVAILABLE
    assert report.attempts == () and status.attempts == ()
    assert activator.calls == 0
    assert report.exit_code == status.exit_code == 3


def test_runtime_status_prefers_docker_then_podman(monkeypatch):
    docker_up = DE.runtime_status(prober=FakeProber(reachable()))
    assert docker_up.provider == DE.PROVIDER_DOCKER and docker_up.answered_by == DE.PATH_PROBE
    podman_up = DE.runtime_status(
        prober=FakeProber(unreachable()),
        podman_prober=FakeProber(reachable(DE.PROVIDER_PODMAN, "5.8.1", "overlay", rootless=True)),
    )
    assert podman_up.provider == DE.PROVIDER_PODMAN
    assert podman_up.answered_by == DE.PATH_ROOTLESS_PODMAN
    assert podman_up.exit_code == 0


@pytest.mark.parametrize("report_factory,expected", [
    (lambda: DE.ensure(prober=FakeProber(reachable())), DE.EXIT_OK),
    (lambda: DE.ensure(start=False, prober=FakeProber(unreachable()),
                       podman_prober=FakeProber(unreachable(DE.PROVIDER_PODMAN))),
     DE.EXIT_BLOCKED),
])
def test_exit_codes_are_pinned(report_factory, expected):
    assert report_factory().exit_code == expected


def test_the_selftest_probe_exit_contract_is_the_blocked_one(monkeypatch):
    """The read-only probe answers what ``ensure`` would: 0 or 3, never a claim."""
    monkeypatch.setattr(DE, "probe_docker", FakeProber(reachable(version="29.6.1")))
    ok, _detail, _ref = DE.selftest_probe()
    assert ok is True
    monkeypatch.setattr(DE, "probe_docker", FakeProber(unreachable(detail="down")))
    monkeypatch.setattr(DE, "probe_podman", FakeProber(unreachable(DE.PROVIDER_PODMAN,
                                                                   detail="down too")))
    ok, _detail, _ref = DE.selftest_probe()
    assert ok is False


def test_every_case_reports_zero_container_operations(tmp_path, monkeypatch):
    monkeypatch.setenv("SKALDIR_DOCKER_SOCKET", str(tmp_path / "missing.sock"))
    cases = [
        DE.ensure(prober=FakeProber(reachable())),
        DE.ensure(prober=FakeProber(unreachable()), activator=FakeActivator(),
                  podman_prober=FakeProber(unreachable(DE.PROVIDER_PODMAN)), sleep=FakeSleep()),
        DE.ensure(prober=FakeProber(unreachable()), activator=FakeActivator(ok=False),
                  podman_prober=FakeProber(reachable(DE.PROVIDER_PODMAN, "5.8.1", "overlay")),
                  sleep=FakeSleep()),
    ]
    for report in cases:
        payload = report.to_dict()
        assert payload["container_ops_attempted"] == 0
        for command in payload["commands"]:
            if command.startswith("connect("):
                continue
            argv = command.split()
            assert argv[1] in DE.ALLOWED_VERBS, f"{command} is not a probe"


# ------------------------------------------------------ 2. the guards (no sudo, no container)


@pytest.mark.parametrize("binary", sorted(DE.PRIVILEGED_BINARIES))
def test_a_privileged_binary_is_refused(binary):
    with pytest.raises(DE.PrivilegedStartRefused):
        DE.guard_argv([binary, "version"])


@pytest.mark.parametrize("argv", [
    ["docker"],                       # no verb at all: a help page is not a probe
    ["docker", "run", "hello-world"],
    ["docker", "exec", "-it", "c", "sh"],
    ["docker", "ps"],
    ["docker", "compose", "up"],
    ["podman", "run", "alpine"],
    ["podman", "build", "."],
])
def test_any_non_probe_verb_is_refused(argv):
    with pytest.raises(DE.ContainerOperationRefused):
        DE.guard_argv(argv)


@pytest.mark.parametrize("argv", [
    ["docker", "version", "--format", "{{json .}}"],
    ["docker", "info", "--format", "{{json .}}"],
    ["podman", "info", "--format", "{{json .}}"],
])
def test_the_probe_verbs_are_allowed(argv):
    DE.guard_argv(argv)


def test_run_argv_refuses_before_it_executes():
    """The guard is on the execution path, so a container op cannot slip through."""
    with pytest.raises(DE.PrivilegedStartRefused):
        DE.run_argv(["sudo", "systemctl", "start", "docker"])
    with pytest.raises(DE.ContainerOperationRefused):
        DE.run_argv(["docker", "run", "hello-world"])


def test_a_prober_that_tries_a_container_op_is_stopped():
    """Even a rogue prober cannot make this capability run a container."""

    def rogue(*, timeout_s=5.0, docker_bin=None, environ=None, **kwargs):
        DE.run_argv(["docker", "run", "--rm", "hello-world"])
        return reachable()

    with pytest.raises(DE.ContainerOperationRefused):
        DE.ensure(prober=rogue, activator=FakeActivator(), sleep=FakeSleep())


# --------------------------------------------------------- 3. the bounded timeout


def test_a_hanging_runtime_is_reported_not_waited_on(tmp_path):
    binary = fake_binary(tmp_path, "docker", "sleep 30\n")
    started = time.monotonic()
    result = DE.run_argv([str(binary), "version"], timeout_s=0.5)
    elapsed = time.monotonic() - started
    assert result.timed_out is True and result.exit_code is None
    assert elapsed < 5.0, f"a bounded probe took {elapsed:.1f}s"
    probe = DE.probe_docker(timeout_s=0.5, docker_bin=str(binary))
    assert probe.reachable is False and "timed out" in probe.detail


def test_ensure_never_hangs_on_a_dead_runtime(tmp_path):
    binary = fake_binary(tmp_path, "docker", "sleep 30\n")
    started = time.monotonic()
    report = DE.ensure(timeout_s=0.5, docker_bin=str(binary), paths=(),
                       environ={"PATH": "/usr/bin:/bin"})
    elapsed = time.monotonic() - started
    assert report.state == DE.STATE_UNAVAILABLE and report.exit_code == 3
    assert elapsed < 5.0, f"ensure() took {elapsed:.1f}s on a dead runtime"
    assert "timed out" in report.reason


def test_a_timed_out_probe_leaves_no_process_behind(tmp_path):
    """The bounded probe also bounds the *processes*: the whole group is killed."""
    marker = "sleep 987654"
    binary = fake_binary(tmp_path, "docker", f"{marker} &\nsleep 987654\n")
    result = DE.run_argv([str(binary), "version"], timeout_s=0.5)
    assert result.timed_out is True
    time.sleep(0.3)
    survivors = subprocess.run(  # noqa: S603 — a fixed probe command
        ["/bin/sh", "-c", f"ps -o pid=,cmd= -C sleep | grep -c '{marker}' || true"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    assert survivors == "0", "a timed-out probe must not leave its child running"


# --------------------------------------------------------------- 4. the real probes


def test_probe_docker_parses_the_daemons_own_payload(tmp_path):
    binary = fake_binary(
        tmp_path, "docker",
        'case "$1" in version) echo \'{"Client":{"Version":"x"}}\';; '
        'info) echo \'{"ServerVersion":"29.6.1","Driver":"overlayfs"}\';; esac\n',
    )
    probe = DE.probe_docker(timeout_s=2.0, docker_bin=str(binary))
    assert probe.reachable is True
    assert probe.server_version == "29.6.1" and probe.storage_driver == "overlayfs"
    assert probe.commands and all(str(binary) in line for line in probe.commands)


def test_probe_docker_reports_a_daemon_that_does_not_answer(tmp_path):
    binary = fake_binary(tmp_path, "docker",
                         'case "$1" in version) exit 0;; esac\necho "Cannot connect" >&2\nexit 1\n')
    probe = DE.probe_docker(timeout_s=2.0, docker_bin=str(binary))
    assert probe.reachable is False
    assert "did not answer" in probe.detail and "Cannot connect" in probe.detail


def test_probe_docker_reports_unparsable_info(tmp_path):
    binary = fake_binary(tmp_path, "docker", 'echo "not json at all"\n')
    probe = DE.probe_docker(timeout_s=2.0, docker_bin=str(binary))
    assert probe.reachable is False and "did not return JSON" in probe.detail


def test_a_missing_binary_is_reported_not_faked(tmp_path):
    probe = DE.probe_docker(timeout_s=2.0, docker_bin=str(tmp_path / "nope" / "docker"))
    assert probe.reachable is False and "not found" in probe.detail


@pytest.mark.parametrize("payload,expected_version,expected_rootless", [
    # podman 5 spells the top-level keys lowercase …
    ('{"version":{"Version":"5.8.1"},"store":{"graphDriverName":"overlay"},'
     '"host":{"security":{"rootless":true}}}', "5.8.1", True),
    # … where podman 3/4 used PascalCase: both must be read, not "unknown".
    ('{"Version":{"Version":"4.9.4"},"Store":{"GraphDriverName":"overlay"},'
     '"Host":{"Security":{"Rootless":true}}}', "4.9.4", True),
])
def test_probe_podman_reads_both_podman_json_shapes(tmp_path, payload, expected_version,
                                                    expected_rootless):
    binary = fake_binary(tmp_path, "podman", f"cat <<'EOF'\n{payload}\nEOF\n")
    probe = DE.probe_podman(timeout_s=2.0, podman_bin=str(binary))
    assert probe.reachable is True
    assert probe.provider == DE.PROVIDER_PODMAN
    assert probe.server_version == expected_version
    assert probe.rootless is expected_rootless
    assert probe.storage_driver == "overlay"


def test_scrubbed_environ_keeps_docker_vars_and_drops_credentials(monkeypatch):
    """The probe inherits the caller's env minus the credential scrub (one pattern)."""
    monkeypatch.setenv("DOCKER_HOST", "unix:///tmp/x.sock")
    monkeypatch.setenv("DOCKER_TLS_VERIFY", "1")
    monkeypatch.setenv("SOME_API_KEY", "sk-should-not-survive")
    monkeypatch.setenv("MY_SESSION_TOKEN", "nope")
    monkeypatch.setenv("PYTHONPATH", "/elsewhere/src")
    env = DE.scrubbed_environ()
    assert env["DOCKER_HOST"] == "unix:///tmp/x.sock"
    assert env["DOCKER_TLS_VERIFY"] == "1"
    assert "SOME_API_KEY" not in env and "MY_SESSION_TOKEN" not in env
    assert "PYTHONPATH" not in env
    assert DE.scrubbed_environ({"A": "1"}) == {"A": "1"}


def test_the_bound_timeout_is_the_documented_one(tmp_path):
    binary = fake_binary(tmp_path, "docker", "sleep 30\n")
    started = time.monotonic()
    probe = DE.probe_docker(timeout_s=0.5, docker_bin=str(binary))
    assert probe.reachable is False and "timed out" in probe.detail
    assert time.monotonic() - started < 5.0


def test_activate_socket_reports_a_missing_socket(tmp_path):
    attempt = DE.activate_socket(socket_path=str(tmp_path / "missing.sock"), timeout_s=1.0)
    assert attempt.path == DE.PATH_SOCKET_ACTIVATION and attempt.ok is False
    assert "no socket at" in attempt.detail


def test_activate_socket_reports_a_refused_connection(monkeypatch, tmp_path):
    def boom(path: str, timeout_s: float) -> str:
        raise ConnectionRefusedError("nothing is listening")

    attempt = DE.activate_socket(socket_path=str(tmp_path), timeout_s=1.0, connect=boom)
    assert attempt.ok is False and "ConnectionRefusedError" in attempt.detail


def test_clamp_timeout_is_bounded():
    assert DE.clamp_timeout(None) == DE.DEFAULT_TIMEOUT_S
    assert DE.clamp_timeout(0) == DE.DEFAULT_TIMEOUT_S
    assert DE.clamp_timeout(-3) == DE.DEFAULT_TIMEOUT_S
    assert DE.clamp_timeout(0.01) == DE.MIN_TIMEOUT_S
    assert DE.clamp_timeout(10_000) == DE.MAX_TIMEOUT_S


# ------------------------------------------------- 5. the selftest probe + its hook


@pytest.fixture()
def restore_expand_prober():
    """Put ``cli_contract._PROBES['EXPAND']`` back after a test installed on it.

    The shipped ``cli.py`` now wires this capability (the integration round added the one
    registration line), so by the time these tests run the probe is normally **already**
    installed and the ``docker.ensure`` contract entry already exists — the exact state a test
    *about installing* must not start from. The fixture therefore also drops the wrapper marker
    and the entry for the duration of the test and puts both back afterwards, which keeps the
    two "installs it itself" tests order-independent: before this, they passed only when no
    earlier test in the session had built the real parser.
    """
    from skaldir import cli_contract

    original = cli_contract._PROBES["EXPAND"]
    saved_commands = dict(cli_contract.COMMANDS)
    if getattr(original, DE._PROBE_MARKER, False):
        # the shipped wiring installed it: start from the pre-wiring wrapper
        cli_contract._PROBES["EXPAND"] = getattr(original, "__wrapped__", original)
    cli_contract.COMMANDS.pop("docker.ensure", None)
    yield cli_contract
    cli_contract._PROBES["EXPAND"] = original
    cli_contract.COMMANDS.clear()
    cli_contract.COMMANDS.update(saved_commands)


def test_selftest_probe_is_read_only_and_honest(monkeypatch):
    monkeypatch.setattr(DE, "probe_docker", FakeProber(reachable(version="29.6.1")))
    monkeypatch.setattr(DE, "probe_podman", FakeProber(unreachable(DE.PROVIDER_PODMAN)))
    ok, detail, ref = DE.selftest_probe()
    assert ok is True and ref == DE.EVIDENCE_REF
    assert "docker 29.6.1" in detail and "probe" in detail and "overlayfs" in detail

    monkeypatch.setattr(DE, "probe_docker", FakeProber(unreachable(detail="daemon down")))
    monkeypatch.setattr(DE, "probe_podman", FakeProber(unreachable(DE.PROVIDER_PODMAN,
                                                                    detail="podman down")))
    ok, detail, ref = DE.selftest_probe()
    assert ok is False
    assert "daemon down" in detail and "podman down" in detail
    assert DE.PRIMARY_EXACT_COMMAND in detail


def test_the_probe_hook_installs_once_and_reports_what_it_did(restore_expand_prober, monkeypatch):
    contract = restore_expand_prober
    monkeypatch.setattr(DE, "probe_docker", FakeProber(reachable(version="29.6.1")))
    monkeypatch.setattr(DE, "probe_podman", FakeProber(unreachable(DE.PROVIDER_PODMAN)))

    first = DE.install_selftest_probe(contract)
    second = DE.install_selftest_probe(contract)
    assert first == {"installed": True, "already": False, "layer": "EXPAND",
                     "probe": DE.SELFTEST_PROBE_NAME,
                     "replaces": "cli_contract._PROBES['EXPAND']"}
    assert second["installed"] is True and second["already"] is True

    layer = contract.run_selftest(layers=["EXPAND"])["layers"][0]
    names = [probe["name"] for probe in layer["probes"]]
    assert names.count(DE.SELFTEST_PROBE_NAME) == 1, "the hook must not add the probe twice"
    installed = next(probe for probe in layer["probes"]
                     if probe["name"] == DE.SELFTEST_PROBE_NAME)
    assert installed["ok"] is True and installed["status"] == "reachable"
    assert layer["ok"] is True and layer["exit_code"] == 0
    assert any(item["ref"] == DE.EVIDENCE_REF for item in layer["evidence"])


def test_the_hook_makes_the_layer_blocked_when_no_runtime_answers(restore_expand_prober,
                                                                 monkeypatch):
    """``blocked``, not ``failed``: an absent runtime is the outside world, not a defect."""
    contract = restore_expand_prober
    monkeypatch.setattr(DE, "probe_docker", FakeProber(unreachable(detail="daemon down")))
    monkeypatch.setattr(DE, "probe_podman", FakeProber(unreachable(DE.PROVIDER_PODMAN,
                                                                   detail="podman down")))
    DE.install_selftest_probe(contract)
    layer = contract.run_selftest(layers=["EXPAND"])["layers"][0]
    probe = next(item for item in layer["probes"] if item["name"] == DE.SELFTEST_PROBE_NAME)
    assert probe["ok"] is False and probe["status"] == "blocked"
    assert DE.SELFTEST_PROBE_NAME in layer["blocked_reason"]


def test_the_probe_hook_tolerates_a_build_without_the_contract():
    class NoProbes:
        pass

    assert DE.install_selftest_probe(NoProbes())["installed"] is False
    assert DE.install_selftest_probe(NoProbes())["reason"] == "the probe table has no EXPAND layer"


# ------------------------------------------------------------------- 6. the CLI verb


@pytest.fixture()
def cli_env(monkeypatch):
    """A CLI run against fake probes: no daemon, no container, no side effect."""
    from skaldir import docker_ensure_cli as cli

    monkeypatch.setattr(DE, "probe_docker", FakeProber(reachable(version="29.6.1")))
    monkeypatch.setattr(DE, "probe_podman", FakeProber(unreachable(DE.PROVIDER_PODMAN)))
    return cli


def test_standalone_cli_reports_running_and_exits_zero(cli_env, capsys):
    code = cli_env.main(["ensure", "--json"])
    document = json.loads(capsys.readouterr().out)
    assert code == 0
    assert document["state"] == "running" and document["provider"] == "docker"
    assert document["server_version"] == "29.6.1"
    assert document["container_ops_attempted"] == 0


def test_standalone_cli_is_blocked_with_the_exact_command(cli_env, capsys, monkeypatch):
    monkeypatch.setattr(DE, "probe_docker", FakeProber(unreachable(detail="daemon down")))
    code = cli_env.main(["ensure", "--json"])
    document = json.loads(capsys.readouterr().out)
    assert code == DE.EXIT_BLOCKED == 3
    assert document["state"] == "unavailable"
    assert document["exact_command"] == "sudo systemctl start docker"
    assert document["attempts"] and document["attempts"][0]["path"] == DE.PATH_SOCKET_ACTIVATION


def test_standalone_cli_human_line_names_the_next_step(cli_env, capsys, monkeypatch):
    monkeypatch.setattr(DE, "probe_docker", FakeProber(unreachable(detail="daemon down")))
    code = cli_env.main(["ensure"])
    out = capsys.readouterr().out
    assert code == 3
    assert "unavailable" in out and "sudo systemctl start docker" in out


def test_standalone_cli_no_fallback_asks_the_docker_only_question(cli_env, capsys, monkeypatch):
    monkeypatch.setattr(DE, "probe_docker", FakeProber(unreachable(detail="daemon down")))
    code = cli_env.main(["ensure", "--json", "--no-fallback"])
    document = json.loads(capsys.readouterr().out)
    assert code == 3
    assert document["paths"] == [] and document["attempts"] == []


def test_standalone_cli_no_start_is_read_only(cli_env, capsys):
    code = cli_env.main(["ensure", "--json", "--no-start"])
    document = json.loads(capsys.readouterr().out)
    assert code == 0 and document["attempts"] == []


def test_a_bad_path_is_a_usage_error(cli_env, capsys):
    code = cli_env.main(["ensure", "--json", "--path", "nonsense"])
    assert code == 1  # argparse refuses a choice it does not know


def test_register_cli_wires_the_group_the_contract_entry_and_the_probe(restore_expand_prober,
                                                                      monkeypatch):
    from skaldir import cli_contract
    from skaldir import docker_ensure_cli as cli

    assert "docker.ensure" not in cli_contract.COMMANDS
    parser = argparse.ArgumentParser(prog="skaldir")
    sub = parser.add_subparsers(dest="command", required=True)
    group = cli.register_cli(sub)
    again = cli.register_cli(sub)  # idempotent: no argparse conflict
    assert group is again and "docker" in sub.choices
    cli_contract.annotate(parser)

    assert "docker.ensure" in cli_contract.COMMANDS
    assert cli_contract.registered("docker.ensure") is True
    dump = cli_contract.dump(parser)
    leaf = next(item for item in dump["commands"] if item["command"] == "docker.ensure")
    assert leaf["registered"] is True and leaf["layer"] == "EXPAND"
    assert leaf["exit_codes"] == [0, 1, 3, 7]
    assert leaf["mutates"] is False, "no file, no container, no registry is written"
    assert "docker.ensure" not in dump["unregistered"]
    assert getattr(cli_contract._PROBES["EXPAND"], "_skaldir_docker_ensure_installed", False)


def test_the_wired_verb_runs_through_the_contract_envelope(restore_expand_prober, cli_env,
                                                          capsys):
    from skaldir import cli_contract

    parser = argparse.ArgumentParser(prog="skaldir")
    sub = parser.add_subparsers(dest="command", required=True)
    cli_env.register_cli(sub)
    cli_contract.annotate(parser)
    args = parser.parse_args(["docker", "ensure", "--json"])
    code = cli_contract.run_command(args, args.func, json_mode=True)
    document = json.loads(capsys.readouterr().out)
    assert code == 0
    assert document["command"] == "docker.ensure" and document["ok"] is True
    assert document["data"]["state"] == "running"
    assert document["data"]["server_version"] == "29.6.1"
    assert {"ref": DE.EVIDENCE_REF, "kind": "code",
            "note": "the state machine that produced this report"} in document["evidence"]


def test_the_wired_verb_exits_three_and_carries_the_command(restore_expand_prober, cli_env,
                                                           capsys, monkeypatch):
    from skaldir import cli_contract

    monkeypatch.setattr(DE, "probe_docker", FakeProber(unreachable(detail="daemon down")))
    parser = argparse.ArgumentParser(prog="skaldir")
    sub = parser.add_subparsers(dest="command", required=True)
    cli_env.register_cli(sub)
    cli_contract.annotate(parser)
    args = parser.parse_args(["docker", "ensure", "--json", "--no-fallback"])
    code = cli_contract.run_command(args, args.func, json_mode=True)
    document = json.loads(capsys.readouterr().out)
    assert code == 3
    assert document["error"]["code"] == "blocked"
    assert document["data"]["exact_command"] == "sudo systemctl start docker"
    assert document["next_actions"][0]["command"] == "sudo systemctl start docker"


# ----------------------------------------------------------- 7. the agent tool


@pytest.fixture()
def runtime(tmp_path):
    directory = tmp_path / "rt"
    directory.mkdir()
    return directory


def test_the_tool_is_a_core_tool_with_a_full_typed_contract():
    from skaldir.tools_core import contract as C
    from skaldir.tools_core import registry

    table = registry.contracts()
    assert "docker.ensure" in table
    contract = table["docker.ensure"]
    assert contract.name == "docker.ensure"
    assert contract.module.startswith("skaldir.tools_core.")
    assert C.validate_schema(contract.input_schema, where="input") == []
    assert C.validate_schema(contract.output_schema, where="output") == []
    assert contract.timeout_s > 0 and contract.max_timeout_s >= contract.timeout_s
    assert contract.truncation.max_bytes > 0
    assert contract.permission.subprocess.allowed is True
    assert contract.permission.subprocess.shell is False
    assert contract.smoke["network"] is True, "a daemon is an outside dependency: skip by default"
    assert contract.smoke["expect"]


def test_the_input_schema_bites(runtime):
    from skaldir.tools_core import contract as C
    from skaldir.tools_core import registry

    schema = registry.contracts()["docker.ensure"].input_schema
    assert C.validate_input(schema, {}) == []
    assert C.validate_input(schema, {"start": "yes"}) == ["start: expected boolean, got str"]
    assert C.validate_input(schema, {"nope": 1}) == ["unknown argument: nope"]
    assert C.validate_input(schema, {"timeout_s": 99}) == ["timeout_s: must be <= 30"]

    # an unknown *path* is refused by the runner (the enum is a runtime rule)
    from skaldir.tools_core import docker as tool
    from skaldir.tools_core.contract import ToolContext

    context = ToolContext(root=runtime, runtime=runtime)
    refused = tool.run({"paths": ["teleport"]}, context)
    assert refused.ok is False and refused.error_kind == "usage"
    assert "unknown start path" in refused.error


def test_the_bridge_exposes_the_dotted_entrypoint():
    from skaldir.tools_core import bridge, registry

    assert registry.bridge_entrypoint(registry.contracts()["docker.ensure"]) == "docker.ensure"
    assert callable(getattr(bridge, "docker.ensure"))
    assert callable(bridge.docker_ensure)


def test_the_model_sees_a_usable_schema_through_the_agent_loop(runtime, monkeypatch):
    """The loop types a registry tool from its sample — the sample must show the shape."""
    from skaldir import docker_ensure as de
    from skaldir.agent_loop import registry_tools, wire_tool_name
    from skaldir.tools import ToolStore
    from skaldir.tools_core import registry

    monkeypatch.setattr(de, "probe_docker", FakeProber(reachable(version="29.6.1")))
    assert registry.register(runtime, probe_network=False)["ok"] is True
    store = ToolStore(runtime / "tools.db")
    try:
        tools = {tool.name: tool for tool in registry_tools(store, runtime=runtime)}
    finally:
        store.close()
    assert "docker.ensure" in tools
    exposed = tools["docker.ensure"]
    assert wire_tool_name("docker.ensure") == "docker_ensure"
    properties = exposed.input_schema["properties"]
    assert set(properties) == {"start", "timeout_s", "ready_s"}, properties
    assert properties["start"]["type"] == "boolean"
    assert properties["timeout_s"]["type"] == "number"
    assert exposed.input_schema.get("required") is None, "no argument is required"


@pytest.mark.parametrize("paths,message_part", [
    (["teleport"], "unknown start path"),
    ("socket-activation", "must be an array"),
])
def test_the_tool_refuses_a_bad_path_list(paths, message_part):
    from skaldir.tools_core import docker as tool

    problem = tool.paths_problem(paths)
    assert message_part in problem


def test_the_tool_call_reports_running(runtime, monkeypatch):
    from skaldir import docker_ensure as de
    from skaldir.tools_core import registry

    monkeypatch.setattr(de, "probe_docker", FakeProber(reachable(version="29.6.1")))
    assert registry.register(runtime, probe_network=False)["ok"] is True
    result = registry.call("docker.ensure", {}, runtime=runtime, root=str(runtime))
    assert result.ok is True and result.exit_code == 0
    assert result.data["state"] == "running"
    assert result.data["server_version"] == "29.6.1"
    assert result.data["container_ops_attempted"] == 0
    assert result.data["probed_at"]


def test_the_tool_call_is_blocked_with_exit_three(runtime, monkeypatch):
    from skaldir import docker_ensure as de
    from skaldir.tools_core import registry

    monkeypatch.setattr(de, "probe_docker", FakeProber(unreachable(detail="daemon down")))
    monkeypatch.setattr(de, "probe_podman", FakeProber(unreachable(de.PROVIDER_PODMAN,
                                                                   detail="podman down")))
    assert registry.register(runtime, probe_network=False)["ok"] is True
    result = registry.call(
        "docker.ensure", {"paths": []}, runtime=runtime, root=str(runtime)
    )
    assert result.ok is False
    assert result.error_kind == "blocked" and result.exit_code == 3
    assert "daemon down" in result.error
    assert result.data["exact_command"] == "sudo systemctl start docker"
    assert result.data["state"] == "unavailable"


def test_the_tool_smoke_case_is_skipped_without_the_network_flag(runtime):
    """A host without a container daemon must not make the toolset's smoke red."""
    from skaldir.tools_core import registry

    report = registry.smoke(runtime, name="docker.ensure", record_health=False)
    assert report["tools"][0]["ok"] is None
    assert "network smoke was not requested" in report["tools"][0]["skipped"]
    assert report["skipped"] == ["docker.ensure"]
