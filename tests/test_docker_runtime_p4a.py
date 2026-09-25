"""P4a — the two measured Docker-path defects + label-scoped teardown.

Defect 1 (P0 report §1.3.6): ``mgr.run()``'s argv provably lacked ``--rm``, so
every build probe left a stopped container behind.
Defect 2 (§1.3.7): ``run()`` / ``start()`` raised ``PolicyViolation`` instead of
returning a structured result while ``toolbuilder.py`` called them without
try/except — a refused policy crashed the builder.
Teardown (N5/R14): only containers carrying OUR labels may be removed. This
daemon also runs 7 foreign ``penpot-local`` containers — no name-based removal,
no ``docker system prune``, ever.

No docker is needed for any of it: the argv is data (a recorded ``_invoke_raw``)
and the teardown path is driven against a fake ``docker`` binary on disk, so no
container (foreign or ours) is ever started, stopped or removed for real.
"""

from __future__ import annotations

import json
import stat
import sys
from types import SimpleNamespace

import pytest

from skaldir import docker_runtime as dr
from skaldir.capability import CapabilityRequest
from skaldir.toolbuilder import ToolBuilder, ToolBuildRequest

DIGEST = "sha256:" + "a" * 64

#: two of OURS (same owner label, two builds) …
OURS = [
    {
        "id": "0ur00000001",
        "labels": {
            "godsv.owner": "skaldir",
            "godsv.build_id": "b-1",
            "godsv.capability": "inspect something",
        },
    },
    {
        "id": "0ur00000002",
        "labels": {
            "godsv.owner": "skaldir",
            "godsv.build_id": "b-2",
            "godsv.capability": "probe something",
        },
    },
    {
        "id": "0ur00000003",
        "labels": {
            "godsv.owner": "skaldir",
            "godsv.build_id": "b-1",
            "godsv.capability": "probe something else",
        },
    },
]

#: … and seven FOREIGN containers the owner's own compose stack is running.
FOREIGN = [
    {
        "id": f"f0reign0000{index}",
        "labels": {
            "com.docker.compose.project": "penpot-local",
            "com.docker.compose.service": service,
        },
    }
    for index, service in enumerate(
        ["frontend", "backend", "exporter", "postgres", "redis", "minio", "mailpit"]
    )
]

#: A fake `docker` binary: it answers ONLY the argv the runtime layer emits,
#: logs every call, and "removes" containers by writing them into its state
#: file. Nothing about a real daemon — and nothing foreign — is touched.
_FAKE_DOCKER = rf'''#!{sys.executable}
"""Fake docker CLI (P4a tests) — records argv, never touches a real daemon."""
import json
import os
import sys

with open(os.environ["FAKE_DOCKER_LOG"], "a", encoding="utf-8") as handle:
    handle.write(json.dumps(sys.argv[1:]) + "\n")

state_path = os.environ["FAKE_DOCKER_STATE"]
with open(state_path, encoding="utf-8") as handle:
    state = json.load(handle)
containers = state.get("containers", [])
argv = sys.argv[1:]


def label_filters(args):
    # docker reads ONE `--filter label=…` value as ONE label pair: a comma inside
    # that value belongs to the VALUE, it is not a second pair. `label=a=1,b=2`
    # therefore matches nothing at all (Server 29.6.1, measured live) — modelled
    # here so a comma-joined filter cannot pass this fake by accident.
    wanted = {{}}
    for index, item in enumerate(args):
        if item == "--filter" and index + 1 < len(args) and args[index + 1].startswith("label="):
            pair = args[index + 1][len("label="):]
            if "," in pair:
                return {{"godsv.never-a-label": "matches-nothing"}}
            key, _, value = pair.partition("=")
            wanted[key] = value
    return wanted


def save():
    with open(state_path, "w", encoding="utf-8") as handle:
        json.dump(state, handle)


if argv[:2] == ["ps", "-a"]:
    wanted = label_filters(argv)
    ids = [
        container["id"]
        for container in containers
        if all(str(container.get("labels", {{}}).get(key, "")) == value
               for key, value in wanted.items())
    ]
    print("\n".join(ids))
    raise SystemExit(0)

if argv[:1] == ["rm"]:
    targets = [item for item in argv[1:] if not item.startswith("-")]
    refusing = [item for item in targets if item in state.get("fail_rm", [])]
    if refusing:
        print("Error response from daemon: cannot remove " + refusing[0], file=sys.stderr)
        raise SystemExit(1)
    state["removed"] = state.get("removed", []) + targets
    save()
    for item in targets:
        print(item)
    raise SystemExit(0)

print("fake docker: unexpected argv " + json.dumps(argv), file=sys.stderr)
raise SystemExit(2)
'''


def _argv_calls(log_path) -> list[list[str]]:
    if not log_path.exists():
        return []
    return [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]


def _daemon_state(state_path) -> dict:
    return json.loads(state_path.read_text(encoding="utf-8"))


@pytest.fixture()
def fake_docker(tmp_path, monkeypatch):
    """A real subprocess against a fake `docker` binary — no container runs."""
    script = tmp_path / "docker"
    script.write_text(_FAKE_DOCKER, encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    log = tmp_path / "docker_argv.jsonl"
    state = tmp_path / "daemon_state.json"
    state.write_text(
        json.dumps({"containers": [*OURS, *FOREIGN], "removed": [], "fail_rm": []}),
        encoding="utf-8",
    )
    monkeypatch.setenv("FAKE_DOCKER_LOG", str(log))
    monkeypatch.setenv("FAKE_DOCKER_STATE", str(state))
    manager = dr.DockerRuntimeManager(docker_bin=str(script))
    return SimpleNamespace(manager=manager, log=log, state=state, script=script)


# --------------------------------------------------------------------------- #
# defect 1 — the one-shot argv is self-cleaning, the long-lived path is not
# --------------------------------------------------------------------------- #


@pytest.fixture()
def recorded(monkeypatch):
    """A manager whose docker invocations are recorded instead of executed."""
    manager = dr.DockerRuntimeManager(docker_bin="docker")
    calls: list[list[str]] = []

    def stub(argv, **_kw):
        calls.append(list(argv))
        return dr.CmdResult(0, f"sha256:{'b' * 64}", "")

    monkeypatch.setattr(manager, "_invoke_raw", stub)
    return SimpleNamespace(manager=manager, calls=calls)


def test_one_shot_argv_starts_with_rm():
    """The defect: `docker run` without `--rm` leaves a stopped container behind."""
    config = dr.RunConfig(
        image_digest=DIGEST,
        name="godsv-build-x",
        entrypoint_argv=["echo", "container probe ok"],
        labels={"godsv.build_id": "b1"},
    )
    argv = config.docker_argv("run", one_shot=True)
    assert argv[:3] == ["docker", "run", "--rm"], argv
    assert "--rm" in argv and argv.count("--rm") == 1


def test_run_through_the_manager_emits_rm(recorded):
    config = dr.RunConfig(
        image_digest=DIGEST, name="godsv-build-x", entrypoint_argv=["echo", "ok"]
    )
    result = recorded.manager.run(config, timeout=5.0)
    assert result.ok is True
    assert len(recorded.calls) == 1
    argv = recorded.calls[0]
    assert argv[:3] == ["docker", "run", "--rm"], argv
    assert argv[-2:] == ["echo", "ok"]


def test_long_lived_start_owns_its_lifetime(recorded):
    """A service container is not a one-shot: no `--rm` in the shared base argv.

    `start()` adds its own `-d --rm` (the container removes itself when it is
    stopped, so a service leaves nothing behind either) and keeps the cid, which
    is what makes it controllable while it runs.
    """
    config = dr.RunConfig(
        image_digest=DIGEST, name="godsv-svc", entrypoint_argv=["sleep", "60"]
    )
    assert "--rm" not in config.docker_argv("run"), "long-lived base argv must not be one-shot"
    result = recorded.manager.start(config)
    assert result.ok is True and result.cid
    argv = recorded.calls[0]
    assert "-d" in argv and "--rm" in argv
    assert argv.index("-d") < argv.index("--rm") < argv.index(DIGEST)


# --------------------------------------------------------------------------- #
# defect 2 — a refusal is a structured result, never an escaping exception
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("verb", ["run", "start"])
def test_unpinned_config_returns_a_structured_refusal(recorded, verb):
    config = dr.RunConfig(image="alpine:3.20")  # validate() == [] , but no pin
    result = getattr(recorded.manager, verb)(config)
    assert result.ok is False
    assert result.failure_class == "policy_violation"
    assert result.policy == dr.POLICY_PIN_REQUIRED
    assert "no pinned digest" in result.detail
    assert dr.POLICY_PIN_REQUIRED in result.payload["problems"]
    assert result.payload["reason"] == dr.POLICY_REASONS[dr.POLICY_PIN_REQUIRED]
    assert result.to_dict()["policy"] == dr.POLICY_PIN_REQUIRED
    assert recorded.calls == [], "refused before any docker process exists"


@pytest.mark.parametrize("verb", ["run", "start"])
def test_config_without_image_returns_a_structured_refusal(recorded, verb):
    config = dr.RunConfig()
    with pytest.raises(dr.PolicyViolation):
        config.validate()  # the strict config API keeps its promise to raise
    result = getattr(recorded.manager, verb)(config)
    assert result.ok is False
    assert result.failure_class == "policy_violation"
    assert result.policy == dr.POLICY_IMAGE_REQUIRED
    assert recorded.calls == []


def test_docker_argv_still_raises_for_an_unpinned_config():
    """Only the manager keeps its promise to return; the config API raises."""
    with pytest.raises(dr.PolicyViolation):
        dr.RunConfig(image="alpine:3.20").docker_argv("run")


def test_policy_problems_is_data_and_never_raises():
    problems = dr.RunConfig(image="alpine:3.20").policy_problems()
    assert dr.POLICY_PIN_REQUIRED in problems
    assert dr.RunConfig(image_digest=DIGEST).policy_problems() == []


# --------------------------------------------------------------------------- #
# label-scoped teardown against a fake docker binary
# --------------------------------------------------------------------------- #


def test_cleanup_owned_removes_only_our_labelled_containers(fake_docker):
    result = fake_docker.manager.cleanup_owned()
    assert result.ok is True, result.detail
    assert result.payload["removed"] == [c["id"] for c in OURS]

    foreign_ids = {c["id"] for c in FOREIGN}
    calls = _argv_calls(fake_docker.log)
    removals = [call[2] for call in calls if call[:2] == ["rm", "-f"]]
    assert removals == [c["id"] for c in OURS]
    assert not (set(removals) & foreign_ids), "a foreign container must never be removed"
    # the removal scope came from ONE label-filtered listing …
    listing = [call for call in calls if call[:2] == ["ps", "-a"]]
    assert len(listing) == 1
    assert "label=godsv.owner=skaldir" in listing[0]
    # … and nothing blanket or name-based was ever built
    flat = " ".join(" ".join(call) for call in calls)
    assert "prune" not in flat
    assert "--filter name" not in flat and "name=" not in flat
    assert "-aq" not in flat and "container ls -a" not in flat
    assert "penpot" not in flat
    # independent observation: the fake daemon removed exactly those three
    assert _daemon_state(fake_docker.state)["removed"] == [c["id"] for c in OURS]


def test_cleanup_owned_can_be_narrowed_to_one_build(fake_docker):
    result = fake_docker.manager.cleanup_owned({"godsv.build_id": "b-1"})
    assert result.ok is True
    assert result.payload["removed"] == ["0ur00000001", "0ur00000003"]
    assert _daemon_state(fake_docker.state)["removed"] == ["0ur00000001", "0ur00000003"]
    listing = [call for call in _argv_calls(fake_docker.log) if call[:2] == ["ps", "-a"]][0]
    # the two-label scope is TWO `--filter` arguments — never one comma-joined
    # argument, which the daemon reads as a single pair and matches nothing with
    assert "label=godsv.owner=skaldir" in listing
    assert "label=godsv.build_id=b-1" in listing
    assert listing[listing.index("label=godsv.owner=skaldir") - 1] == "--filter"
    assert not any("," in arg for arg in listing), listing


def test_list_owned_shows_ours_and_never_the_foreign_ones(fake_docker):
    result = fake_docker.manager.list_owned()
    assert result.ok is True
    assert result.payload["containers"] == [c["id"] for c in OURS]
    assert _argv_calls(fake_docker.log) == [
        ["ps", "-a", "--filter", "label=godsv.owner=skaldir", "--format", "{{.ID}}"]
    ], "listing must not remove anything"


def test_foreign_label_scope_is_refused_before_any_docker_call(fake_docker):
    result = fake_docker.manager.cleanup_by_labels(
        {"com.docker.compose.project": "penpot-local"}
    )
    assert result.ok is False
    assert result.failure_class == "policy_violation"
    assert result.policy == dr.POLICY_LABEL_SCOPE
    assert _argv_calls(fake_docker.log) == [], "no docker call may be made for a foreign scope"
    assert _daemon_state(fake_docker.state)["removed"] == []


def test_cleanup_without_labels_is_refused(fake_docker):
    """The existing counter-case (docker_lifecycle_probe.py:83) stays refused."""
    result = fake_docker.manager.cleanup_by_labels({})
    assert result.ok is False
    assert result.failure_class == "policy_violation"
    assert result.policy == dr.POLICY_LABEL_SCOPE
    assert _argv_calls(fake_docker.log) == []


def test_half_removed_leftovers_are_reported_as_cleanup_partial(fake_docker):
    state = _daemon_state(fake_docker.state)
    state["fail_rm"] = ["0ur00000002"]
    fake_docker.state.write_text(json.dumps(state), encoding="utf-8")
    result = fake_docker.manager.cleanup_owned()
    assert result.ok is False
    assert result.failure_class == "cleanup_partial"
    assert result.payload["removed"] == ["0ur00000001", "0ur00000003"]
    assert result.payload["errors"] and "0ur00000002" in result.payload["errors"][0]
    assert _daemon_state(fake_docker.state)["removed"] == ["0ur00000001", "0ur00000003"]


def test_owned_labels_helper_carries_the_owner_marker():
    assert dr.owned_labels() == {dr.OWNER_LABEL_KEY: dr.OWNER_LABEL_VALUE}
    assert dr.owned_labels(**{"godsv.build_id": "b-9"}) == {
        dr.OWNER_LABEL_KEY: dr.OWNER_LABEL_VALUE,
        "godsv.build_id": "b-9",
    }


def test_every_run_config_carries_the_owner_label():
    """L7 as code, not as discipline: teardown can only find what we labelled."""
    config = dr.RunConfig(image_digest=DIGEST, labels={"godsv.build_id": "b-1"})
    assert config.labels[dr.OWNER_LABEL_KEY] == dr.OWNER_LABEL_VALUE
    argv = config.docker_argv("run", one_shot=True)
    assert f"{dr.OWNER_LABEL_KEY}={dr.OWNER_LABEL_VALUE}" in argv
    # an explicitly set owner label is left as the caller set it
    explicit = dr.RunConfig(image_digest=DIGEST, labels={dr.OWNER_LABEL_KEY: "someone"})
    assert explicit.labels[dr.OWNER_LABEL_KEY] == "someone"


# --------------------------------------------------------------------------- #
# the call site: the tool builder consumes the result instead of dying
# --------------------------------------------------------------------------- #

_DOCKER_TOOL_SOURCE = '''"""One deterministic function behind a container-built adapter."""
from __future__ import annotations

import json


def convert(amount: float, rate: float) -> float:
    return round(float(amount) * float(rate), 2)


def main(amount: float = 1.0, rate: float = 1.1) -> dict:
    return {"converted": convert(amount, rate)}


if __name__ == "__main__":  # pragma: no cover
    print(json.dumps(main()))
'''

_DOCKER_TOOL_TESTS = '''"""Plain unit tests for the generated adapter (no container needed)."""
import importlib.util
from pathlib import Path


def _load():
    path = Path(__file__).resolve().parents[1] / "money_convert.py"
    spec = importlib.util.spec_from_file_location("money_convert", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_rounds():
    assert _load().convert(10, 1.105) == 11.05
'''


@pytest.fixture()
def fake_runtime(monkeypatch):
    """A duck-typed DockerRuntimeManager: no daemon, no image, no container.

    It answers the builder's calls and records what the builder asked for — the
    point under test is how the builder HANDLES what the runtime returns.
    """
    scripted = {
        "run_result": dr.StartResult(ok=True, detail="ran", exit_code=0),
        "run_raise": None,
        "teardown": dr.StartResult(ok=True, detail="removed 0 leftovers"),
    }
    calls: dict[str, list] = {"run": [], "teardown": []}

    class FakeManager:
        def __init__(self, *, docker_bin=None):
            self.docker_bin = docker_bin or "docker"

        def available(self):
            return True, "fake daemon"

        def pull_and_pin(self, image, *, digest_hint=None, timeout=600.0):
            return DIGEST

        def resolve_digest(self, image, *, timeout=60.0):
            return DIGEST

        def run(self, config, *, timeout=60.0, **_kw):
            calls["run"].append(config)
            if scripted["run_raise"] is not None:
                raise scripted["run_raise"]
            return scripted["run_result"]

        def cleanup_by_labels(self, labels, *, timeout=30.0):
            calls["teardown"].append(dict(labels))
            return scripted["teardown"]

    monkeypatch.setattr(dr, "DockerRuntimeManager", FakeManager)
    return scripted, calls


def _docker_request() -> ToolBuildRequest:
    return ToolBuildRequest(
        capability="Convert money amounts in a pinned container",
        reason="container-backed capability",
        name="money-convert",
        kind="cli",
        module="money_convert",
        source_code=_DOCKER_TOOL_SOURCE,
        tests_code=_DOCKER_TOOL_TESTS,
        entrypoint="main",
        sample_input={"amount": 10, "rate": 1.1},
        runtime="docker",
        image="alpine:3.20",
        image_digest=DIGEST,
    )


def test_builder_turns_a_refusal_into_a_structured_failure(tmp_path, fake_runtime):
    scripted, calls = fake_runtime
    scripted["run_result"] = dr.policy_refusal([dr.POLICY_PIN_REQUIRED])
    builder = ToolBuilder(tmp_path / "runtime")
    result = builder.build(_docker_request())  # must not raise
    assert result["ok"] is False
    assert result["failure_class"] == "policy_violation"
    assert "container probe failed (policy_violation)" in result["error"]
    probe = result["container_probe"]["probe"]
    assert probe["policy"] == dr.POLICY_PIN_REQUIRED
    assert probe["failure_class"] == "policy_violation"
    # the sweep ran anyway, scoped to OUR labels only
    assert calls["teardown"] == [
        {"godsv.owner": "skaldir", "godsv.build_id": result["build_id"]}
    ]
    # the container config carried the owner label (that is what teardown scopes by)
    assert calls["run"][0].labels[dr.OWNER_LABEL_KEY] == dr.OWNER_LABEL_VALUE


def test_builder_survives_a_raising_runtime_call(tmp_path, fake_runtime):
    """The raising API is still handled: no traceback escapes the builder."""
    scripted, calls = fake_runtime
    scripted["run_raise"] = dr.PolicyViolation(
        "no pinned digest — an untraceable mutable tag is rejected"
    )
    builder = ToolBuilder(tmp_path / "runtime")
    result = builder.build(_docker_request())
    assert result["ok"] is False
    assert result["container_probe"]["probe"]["failure_class"] == "policy_violation"
    assert calls["teardown"], "the sweep must run even after a refused probe"


def test_builder_sweeps_after_a_failed_probe_too(tmp_path, fake_runtime):
    scripted, calls = fake_runtime
    scripted["run_result"] = dr.StartResult(
        ok=False, detail="exit 3", exit_code=3, failure_class="docker_error",
        payload={"raw": "exit 3"},
    )
    builder = ToolBuilder(tmp_path / "runtime")
    result = builder.build(_docker_request())
    assert result["ok"] is False
    assert result["failure_class"] == "docker_error"
    assert calls["teardown"] == [
        {"godsv.owner": "skaldir", "godsv.build_id": result["build_id"]}
    ]


def test_builder_reports_a_partial_teardown_instead_of_passing(tmp_path, fake_runtime):
    scripted, _calls = fake_runtime
    scripted["teardown"] = dr.StartResult(
        ok=False,
        detail="0ur00000002: cannot remove",
        failure_class="cleanup_partial",
        payload={"removed": ["0ur00000001"], "errors": ["0ur00000002: cannot remove"]},
    )
    builder = ToolBuilder(tmp_path / "runtime")
    result = builder.build(_docker_request())
    assert result["ok"] is False
    assert result["failure_class"] == "cleanup_partial"
    assert "container teardown failed" in result["error"]
    assert result["container_probe"]["teardown"]["ok"] is False
    assert result["container_probe"]["teardown"]["failure_class"] == "cleanup_partial"


# --------------------------------------------------------------------------- #
# supporting: a capability request can actually ask for a container tool
# --------------------------------------------------------------------------- #


def test_capability_request_forwards_the_docker_request():
    request = CapabilityRequest(
        capability="wrap a container cli",
        runtime="docker",
        image="alpine:3.20",
        image_digest=DIGEST,
        resource_limits={"cpu": 1.0, "ram": "256m"},
    )
    tool_request = request.to_tool_request()
    assert tool_request.runtime == "docker"
    assert tool_request.image == "alpine:3.20"
    assert tool_request.image_digest == DIGEST
    assert tool_request.resource_limits == {"cpu": 1.0, "ram": "256m"}
    assert tool_request.mounts == []
    # without the docker fields a request stays a LOCAL python tool
    plain = CapabilityRequest(capability="add two numbers").to_tool_request()
    assert plain.runtime == "" and plain.image == "" and plain.image_digest == ""
