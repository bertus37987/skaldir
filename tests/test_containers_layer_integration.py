"""Testcontainers + real-daemon integration suite for the Docker-touching layers.

CLI wave / container tests. This is the suite that drives a **real** docker daemon
with **real** containers for the three product paths that touch Docker:

* **EXPAND** — a capability is derived from a pinned container image, probed
  through the runtime and registered with the digest committed in its manifest;
* **the docker runtime lifecycle** — one-shot ``run()`` (self-cleaning through
  ``--rm``), long-lived ``start()`` with an ``exec`` round trip, and
  label-scoped teardown;
* **the tool-builder probe** — the builder's container probe + its sweep, end to
  end, into a throw-away runtime directory.

Label discipline (the daemon is shared with foreign stacks and with parallel
waves of this product): every container this module creates carries
``godsv.owner=skaldir``, ``purpose=test`` and ``godsv.test_wave=<this wave>``;
every teardown goes through the product's own label-scoped sweep, never a name,
never ``docker system prune``. A teardown scope may only name ``godsv*`` keys
(``docker_runtime._label_scope_problem``), which is why ``purpose=test`` is a
*marking* label and the sweeps use the ``godsv.test_wave`` / ``godsv.build_id``
labels — all of it asserted below, together with the refusal of a foreign scope.

Measured docker behaviour this suite has to live with (both re-measured here,
both reported in ``MVP/16-container-tests.md``):

* ``-d --rm`` + ``--rm`` auto-removal races an explicit ``docker rm -f``: the
  removal succeeds, but the report can come back as ``cleanup_partial``. The
  tests therefore assert the **effect** (the id is gone, verified against the
  daemon) and accept the documented race class as the only tolerated report.
* removal is asynchronous: a container can stay visible for a moment after a
  successful removal, so "nothing left behind" is asserted inside a bounded
  settle window, not on a single instantaneous reading.
* busybox ``sleep`` as PID 1 ignores SIGTERM (``docker stop`` waits the full
  10.1 s grace and then SIGKILLs, exit 137 — measured), so every long-lived
  entrypoint here is a SIGTERM-aware shell instead.

One defect this suite found is already fixed in the tree and is pinned here as a
plain test: ``list_by_labels`` used to join a multi-label scope into ONE
comma-joined ``--filter`` argument, which docker reads as a single label pair and
therefore matches nothing — every multi-label sweep (``cleanup_owned``, the tool
builder's owner+build teardown) was a silent no-op. It now emits one ``--filter``
per label (:func:`godsv.docker_runtime.label_filter_args`, fixes 2026-09-18);
:class:`TestMultiLabelScope` asserts that behaviour against the daemon.

The second item this suite carried — ``DockerRuntimeManager`` documenting the
``execution.ContainerExecutor`` seam (``available()`` + ``run_job()``) while
implementing no ``run_job()``, which made :class:`TestExecutorSeam` a strict
``xfail`` — is fixed too: the manager implements ``run_job()`` (one job = one
one-shot ``--rm`` run, label-scoped, answered as ``execution.RunResult``), so the
seam is a plain, passing expectation plus one live job really run through it.

Honesty rules: no daemon / no binary / no image ⇒ ``pytest.skip`` with the
measured reason, never a silent pass and never a fake container.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest
import yaml

from skaldir import discovery, execution, toolbuilder
from skaldir import docker_runtime as dr
from skaldir.tools import ToolStore

# --------------------------------------------------------------------------- #
# the pinned, small image, the label set and the timeouts
# --------------------------------------------------------------------------- #

#: Small local image; it is addressed by digest, never by this mutable tag.
IMAGE = "alpine:3.20"

#: The wave label — every sweep scope must live in the product's namespace.
WAVE = "containers-layer-integration"

#: What every container of this module carries (label discipline).
TEST_LABELS = {
    dr.OWNER_LABEL_KEY: dr.OWNER_LABEL_VALUE,
    "purpose": "test",
    "godsv.test_wave": WAVE,
}

#: The scope this module sweeps leftovers with: our owner label + this wave.
#:
#: Multi-label on purpose — ``docker_runtime.list_by_labels()`` emits one
#: ``--filter label=key=value`` argument per label and docker ANDs separate
#: filters. The comma-joined single argument it used before (which the daemon
#: reads as one pair and therefore matches nothing) was defect F1 of this wave;
#: it is fixed in the tree and pinned by :class:`TestMultiLabelScope`, so a
#: multi-label sweep is exercised by every sweep in this module.
SWEEP_SCOPE = {
    dr.OWNER_LABEL_KEY: dr.OWNER_LABEL_VALUE,
    "godsv.test_wave": WAVE,
}

OWNER_FILTER = f"label={dr.OWNER_LABEL_KEY}={dr.OWNER_LABEL_VALUE}"
WAVE_FILTER = f"label=godsv.test_wave={WAVE}"
#: The owner's compose stack shares this daemon — it must survive every sweep.
FOREIGN_FILTER = "label=com.docker.compose.project=penpot-local"

#: A long-lived entrypoint that really reacts to SIGTERM (0.25 s teardown,
#: measured) — busybox ``sleep`` as PID 1 would make ``docker stop`` wait the
#: whole 10 s grace period and then SIGKILL.
LONG_LIVED_ARGV = ["sh", "-c", "trap 'exit 0' TERM; while :; do sleep 0.2; done"]

#: The container probe argv of the EXPAND test: it proves in-container execution
#: by reading the *image's* release file (the host is Fedora and has none).
PROBE_ARGV = ["sh", "-c", "echo container-probe-ok; cat /etc/os-release"]
PROBE_MARKER = "container-probe-ok"
ALPINE_MARKER = "Alpine Linux"

#: Marker of the run_job seam test — its presence proves the job's stdout really
#: came out of the container (the assert is paired with ALPINE_MARKER).
JOB_MARKER = "run-job-seam-ok"

#: Settle window for "the daemon has finished removing it" (removal is async).
SETTLE_SECONDS = 15.0


# --------------------------------------------------------------------------- #
# daemon reachability — the skip gate
# --------------------------------------------------------------------------- #


def _docker(*args: str, timeout: float = 120.0) -> subprocess.CompletedProcess:
    """Raw docker CLI call: the independent observer of everything asserted here."""
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=timeout
    )


def _daemon_state() -> tuple[bool, str]:
    """(reachable?, measured reason) — probed with the CLI, not with the code under test."""
    binary = shutil.which("docker")
    if not binary:
        return False, "no docker binary on PATH"
    try:
        probe = _docker("version", "--format", "{{.Server.Version}}", timeout=20.0)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"docker CLI not runnable: {type(exc).__name__}: {exc}"
    if probe.returncode != 0:
        lines = [line for line in (probe.stderr or probe.stdout).splitlines() if line.strip()]
        return False, (lines[0][:200] if lines else "no docker daemon answered")
    return True, f"docker server {probe.stdout.strip()}"


_DAEMON_OK, _DAEMON_DETAIL = _daemon_state()

pytestmark = pytest.mark.skipif(
    not _DAEMON_OK,
    reason=(
        f"docker unreachable ({_DAEMON_DETAIL}) — the container integration suite needs "
        "a live daemon; start docker and re-run"
    ),
)


# --------------------------------------------------------------------------- #
# observations and bookkeeping
# --------------------------------------------------------------------------- #


def _ids(*filters: str) -> list[str]:
    listing = _docker("ps", "-a", *filters, "--format", "{{.ID}}")
    if listing.returncode != 0:
        raise AssertionError(
            f"docker ps {' '.join(filters)} failed: {(listing.stderr or listing.stdout)[:200]}"
        )
    return [line for line in listing.stdout.split() if line]


def _our_ids() -> list[str]:
    """Every container of ours on the daemon (independent docker query)."""
    return _ids(f"--filter={OWNER_FILTER}")


def _our_wave_ids() -> list[str]:
    """Containers of THIS wave — the scope the tests must leave empty."""
    return _ids(f"--filter={OWNER_FILTER}", f"--filter={WAVE_FILTER}")


def _foreign_ids() -> list[str]:
    """The owner's foreign compose containers — counted, never touched."""
    return _ids(f"--filter={FOREIGN_FILTER}")


def _all_ids() -> set[str]:
    return set(_ids())


def _labels_of(cid: str) -> dict:
    """Labels of one container as the daemon reports them ({}, when it is gone)."""
    out = _docker("inspect", "--format", "{{json .Config.Labels}}", cid)
    if out.returncode != 0:
        return {}
    try:
        return json.loads(out.stdout.strip() or "{}") or {}
    except json.JSONDecodeError:
        return {}


def _container_gone(cid: str) -> bool:
    return _docker("inspect", "--format", "{{.Id}}", cid).returncode != 0


def _found(ids, cid: str) -> bool:
    """``docker ps`` prints truncated ids — match a full cid against the short form."""
    return any(
        str(item).startswith(cid[:12]) or cid.startswith(str(item)) for item in ids or []
    )


def _wait_until(predicate, *, timeout: float = SETTLE_SECONDS, interval: float = 0.15) -> bool:
    """Bounded settle window: container removal is asynchronous in the daemon."""
    deadline = time.monotonic() + timeout
    while True:
        if predicate():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(interval)


def _wait_absent(cid: str) -> bool:
    return _wait_until(lambda: _container_gone(cid))


def _wait_wave_empty() -> bool:
    return _wait_until(lambda: _our_wave_ids() == [])


def _accounted(result, cid: str) -> bool:
    """The removal report accounts for ``cid`` — removed, or as the auto-removal race.

    ``docker stop`` + ``docker rm -f`` on a ``-d --rm`` container races the
    daemon's OWN auto-removal (measured, see the module docstring): the container
    is gone, but the explicit ``rm`` loses the race and reports
    ``cleanup_partial`` for exactly that id. Anything else is a real failure.
    """
    payload = getattr(result, "payload", None) or {}
    if _found(payload.get("removed", []), cid):
        return True
    if result.ok and not payload.get("errors"):
        return True
    return bool(
        result.failure_class == "cleanup_partial"
        and any(cid[:12] in str(err) for err in payload.get("errors", []))
    )


def _unexplained_new_ids(before: set[str]) -> set[str]:
    """New container ids this module cannot account for.

    Accounted for: containers of our product (``godsv.owner=skaldir`` — parallel
    waves of the same product run against this shared daemon), testcontainers'
    own infrastructure (Ryuk carries ``org.testcontainers=true``) and containers
    of any compose stack (the workspace's own compose wave and the owner's
    penpot stack are both compose; the penpot set is additionally compared
    exactly in every test). Everything else — a bare, unlabelled container
    appearing during a test — fails the assertion.
    """
    unexplained: set[str] = set()
    for cid in _all_ids() - set(before):
        labels = _labels_of(cid)
        if labels.get(dr.OWNER_LABEL_KEY) == dr.OWNER_LABEL_VALUE:
            continue
        if str(labels.get("org.testcontainers", "")).lower() == "true":
            continue
        if labels.get("com.docker.compose.project"):
            continue
        unexplained.add(cid)
    return unexplained


def _testcontainers_container():
    """The testcontainers 4.x container class (skip with a reason when absent)."""
    module = pytest.importorskip(
        "testcontainers.core.container", reason="testcontainers is not installed in this venv"
    )
    return module.DockerContainer


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def manager() -> dr.DockerRuntimeManager:
    return dr.DockerRuntimeManager()


@pytest.fixture(scope="module")
def image_digest(manager: dr.DockerRuntimeManager) -> str:
    """The pinned digest of the small local image (a copy is pulled when absent)."""
    digest = manager.resolve_digest(IMAGE)
    if not digest:
        pulled = manager.pull(IMAGE, timeout=300.0)
        if not pulled.ok:
            pytest.skip(
                f"{IMAGE} is neither local nor pullable ({pulled.failure_class}: "
                f"{pulled.detail[:120]}) — run `docker pull {IMAGE}` once and re-run"
            )
        digest = manager.resolve_digest(IMAGE)
    if not str(digest).startswith("sha256:"):
        pytest.skip(f"no digest inspectable for {IMAGE} (daemon answered {digest!r})")
    return str(digest)


@pytest.fixture(scope="module")
def image_ref() -> str:
    """Digest-addressed reference for the testcontainers client (never the tag)."""
    out = _docker("image", "inspect", "--format", "{{index .RepoDigests 0}}", IMAGE)
    ref = out.stdout.strip()
    if out.returncode != 0 or "@sha256:" not in ref:
        pytest.skip(f"no RepoDigest for {IMAGE} — pin the image (`docker pull {IMAGE}`) first")
    return ref


@pytest.fixture()
def runtime_dir(tmp_path: Path) -> Path:
    """A throw-away registry/runtime directory: builds never touch the live runtime."""
    directory = tmp_path / "runtime"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


@pytest.fixture(scope="module", autouse=True)
def container_discipline() -> object:
    """Label discipline around the whole module: sweep ours, never touch a foreign one.

    Baseline: THIS wave's scope is empty (a leftover from a crashed earlier run is
    swept by label, not silently tolerated — the scope is one wave label, so a
    parallel wave of the same product is never affected).
    """
    foreign_before = _foreign_ids()
    swept = dr.DockerRuntimeManager().cleanup_by_labels(SWEEP_SCOPE)
    assert swept.ok or swept.failure_class == "cleanup_partial", swept.detail
    assert _wait_wave_empty(), "baseline: no container of this wave before it starts"
    yield
    leftovers = _our_wave_ids()
    if leftovers:
        cleaned = dr.DockerRuntimeManager().cleanup_by_labels(SWEEP_SCOPE)
        assert cleaned.ok or _accounted(cleaned, leftovers[0]), cleaned.to_dict()
        assert _wait_wave_empty(), f"containers leaked by this wave: {leftovers}"
        pytest.fail(f"containers leaked by this wave and swept afterwards: {leftovers}")
    # the shared daemon's foreign containers are counted by label, before and after
    assert _foreign_ids() == foreign_before, (
        "the foreign compose containers of the shared daemon changed while this module ran"
    )


# --------------------------------------------------------------------------- #
# the daemon probe itself (no container created)
# --------------------------------------------------------------------------- #


class TestDaemonProbe:
    def test_product_probe_agrees_with_the_raw_cli(self, manager):
        """The product's availability probe reports the real server, not a fixture."""
        wrapper_ok, wrapper_detail = manager.available()
        raw = _docker("version", "--format", "{{.Server.Version}}").stdout.strip()
        assert wrapper_ok is True, wrapper_detail
        assert raw, "the raw CLI reported no server version"
        assert raw in wrapper_detail, f"{wrapper_detail!r} does not carry {raw!r}"


# --------------------------------------------------------------------------- #
# testcontainers: an independently created container under OUR label discipline
# --------------------------------------------------------------------------- #


class TestTestcontainersUnderOurLabels:
    def test_container_runs_labelled_without_network_and_is_swept_by_label(
        self, manager, image_ref, image_digest
    ):
        """testcontainers starts it, the product's label sweep removes it again.

        The cross-client proof: a container created by an *independent* tool
        (testcontainers 4.15 / docker-py) carries our labels, so the product's
        label-scoped teardown finds exactly it — and the exec round trip against
        it returns real output (claim d).
        """
        container_cls = _testcontainers_container()
        before = _all_ids()
        foreign_before = _foreign_ids()
        container = (
            container_cls(image_ref)
            .with_command("sleep 90")
            .with_kwargs(labels=dict(TEST_LABELS), network_mode="none", init=True)
        )
        container.start()
        cid = container.get_container_id()
        try:
            labels = _labels_of(cid)
            assert labels.get(dr.OWNER_LABEL_KEY) == dr.OWNER_LABEL_VALUE, labels
            assert labels.get("purpose") == "test", labels
            assert labels.get("godsv.test_wave") == WAVE, labels
            # testcontainers marks its own container too — that is its label, not ours
            assert str(labels.get("org.testcontainers")).lower() == "true", labels
            network = _docker(
                "inspect", "--format", "{{.HostConfig.NetworkMode}}", cid
            ).stdout.strip()
            assert network == "none", f"container started with network mode {network!r}"
            image_id = _docker("inspect", "--format", "{{.Image}}", cid).stdout.strip()
            assert image_id == image_digest, f"{image_id!r} is not the pinned {image_digest!r}"

            # (d) a real exec round trip inside that container
            code, output = container.exec(["sh", "-c", "echo exec-roundtrip-ok; id -u"])
            assert code == 0, output
            assert b"exec-roundtrip-ok" in output, output
            assert b"0" in output, output

            # the product's sweep sees exactly this container
            listing = manager.list_by_labels(SWEEP_SCOPE)
            assert listing.ok, listing.detail
            assert _found(listing.payload["containers"], cid), listing.payload

            removed = manager.cleanup_by_labels(SWEEP_SCOPE)
            assert _accounted(removed, cid), removed.to_dict()
        finally:
            if not _wait_absent(cid):
                # only reached when the sweep above failed — then testcontainers'
                # own teardown is the fallback, still not a name-based removal
                container.stop()
        assert _container_gone(cid), "the swept container is still on the daemon"
        assert _wait_wave_empty(), _our_wave_ids()
        assert _foreign_ids() == foreign_before
        assert _unexplained_new_ids(before) == set(), (
            "container(s) appeared that this wave neither created nor can explain"
        )


# --------------------------------------------------------------------------- #
# (a) one-shot runs leave nothing behind — through the product's runtime layer
# --------------------------------------------------------------------------- #


class TestOneShotRunIsSelfCleaning:
    def test_successful_one_shot_leaves_zero_containers(self, manager, image_digest):
        before = _all_ids()
        foreign_before = _foreign_ids()
        config = dr.RunConfig(
            image=IMAGE,
            image_digest=image_digest,
            labels=dict(TEST_LABELS),
            network="none",
            entrypoint_argv=["sh", "-c", "echo one-shot-ok; cat /etc/alpine-release"],
        )
        # the argv carries --rm: that is the mechanism under test …
        assert "--rm" in config.docker_argv("run", one_shot=True)
        result = manager.run(config, timeout=60.0)
        assert result.ok, f"{result.detail} / {result.stderr[:200]}"
        assert result.exit_code == 0
        assert "one-shot-ok" in result.stdout, result.stdout
        # … proved by the daemon: no new container exists afterwards
        assert _wait_wave_empty(), _our_wave_ids()
        assert _wait_until(lambda: _unexplained_new_ids(before) == set())
        assert _foreign_ids() == foreign_before

    def test_failing_one_shot_leaves_zero_containers_too(self, manager, image_digest):
        """A probe that fails must not leave a corpse behind either (policy N1)."""
        before = _all_ids()
        config = dr.RunConfig(
            image=IMAGE,
            image_digest=image_digest,
            labels=dict(TEST_LABELS),
            network="none",
            entrypoint_argv=["sh", "-c", "echo boom >&2; exit 3"],
        )
        result = manager.run(config, timeout=60.0)
        assert result.ok is False
        assert result.exit_code == 3
        assert "boom" in result.stderr, result.stderr
        assert _wait_wave_empty(), _our_wave_ids()
        assert _wait_until(lambda: _unexplained_new_ids(before) == set())

    def test_digest_addressed_one_shot_leaves_nothing(self, manager, image_ref):
        """The pinned digest (not the mutable tag) is what actually runs."""
        before = _all_ids()
        config = dr.RunConfig(
            image=IMAGE,
            image_digest=image_ref.split("@", 1)[1],
            labels=dict(TEST_LABELS),
            network="none",
            entrypoint_argv=["echo", "digest-addressed-ok"],
        )
        result = manager.run(config, timeout=60.0)
        assert result.ok, f"{result.detail} / {result.stderr[:200]}"
        assert "digest-addressed-ok" in result.stdout
        assert _wait_until(lambda: _unexplained_new_ids(before) == set())
        assert _wait_wave_empty(), _our_wave_ids()


# --------------------------------------------------------------------------- #
# (b) + (d) long-lived lifecycle: start → exec round trip → teardown leaves nothing
# --------------------------------------------------------------------------- #


class TestLongLivedLifecycle:
    def test_start_exec_teardown_leaves_nothing(self, manager, image_digest, tmp_path):
        before = _all_ids()
        foreign_before = _foreign_ids()
        cid_file = tmp_path / "cid"
        config = dr.RunConfig(
            image=IMAGE,
            image_digest=image_digest,
            labels=dict(TEST_LABELS),
            network="none",
            entrypoint_argv=list(LONG_LIVED_ARGV),
        )
        started = manager.start(config, cid_file=cid_file, timeout=60.0)
        assert started.ok, started.detail
        assert started.cid, "a long-lived start must hand back a real cid"
        assert cid_file.read_text(encoding="utf-8").strip() == started.cid
        cid = started.cid
        try:
            # the long-lived path carries our labels and no network
            labels = _labels_of(cid)
            assert labels.get(dr.OWNER_LABEL_KEY) == dr.OWNER_LABEL_VALUE, labels
            assert labels.get("purpose") == "test", labels
            assert _docker(
                "inspect", "--format", "{{.HostConfig.NetworkMode}}", cid
            ).stdout.strip() == "none"
            state = _docker("inspect", "--format", "{{.State.Running}}", cid).stdout.strip()
            assert state == "true", state

            # (d) real exec round trips: the image's own release file and a write/read
            release = manager.exec_in_container(
                cid_file, argv=["cat", "/etc/alpine-release"], timeout=30.0
            )
            assert release.ok, release.detail
            assert release.stdout.strip().startswith("3."), release.stdout
            effect = manager.exec_in_container(
                cid_file,
                argv=["sh", "-c", "echo effect > /tmp/proof && cat /tmp/proof"],
                timeout=30.0,
            )
            assert effect.ok, effect.detail
            assert effect.stdout.strip() == "effect", effect.stdout
        finally:
            removed = manager.stop_and_remove(cid)
        # (b) the honest claim: the container is gone (removal is asynchronous, so
        # this is a bounded wait, not a single reading) …
        assert _wait_absent(cid), f"container {cid[:12]} survived its teardown"
        # … and the report is accounted for: a `cleanup_partial` naming this id is
        # the documented auto-removal race of `-d --rm` (the effect is verified
        # above), anything else would be a real teardown failure.
        assert removed.ok or _accounted(removed, cid), removed.to_dict()
        assert _wait_wave_empty(), _our_wave_ids()
        assert _foreign_ids() == foreign_before
        assert _wait_until(lambda: _unexplained_new_ids(before) == set())


# --------------------------------------------------------------------------- #
# (c) label-scoped cleanup: only ours, never a foreign container
# --------------------------------------------------------------------------- #


class TestLabelScopedCleanup:
    def test_cleanup_only_removes_the_scoped_build(self, manager, image_digest):
        """Two of our containers, one scope: the other one survives the sweep.

        The scope is one label (``godsv.build_id``) — see the module constant
        ``SWEEP_SCOPE`` and :class:`TestMultiLabelScopeDefect` for why a
        multi-label scope cannot be used here today.
        """
        before = _all_ids()
        foreign_before = _foreign_ids()
        first = dr.RunConfig(
            image=IMAGE,
            image_digest=image_digest,
            labels={**TEST_LABELS, "godsv.build_id": f"{WAVE}-a"},
            network="none",
            entrypoint_argv=list(LONG_LIVED_ARGV),
        )
        second = dr.RunConfig(
            image=IMAGE,
            image_digest=image_digest,
            labels={**TEST_LABELS, "godsv.build_id": f"{WAVE}-b"},
            network="none",
            entrypoint_argv=list(LONG_LIVED_ARGV),
        )
        started_a = manager.start(first, timeout=60.0)
        started_b = manager.start(second, timeout=60.0)
        assert started_a.ok, started_a.detail
        assert started_b.ok, started_b.detail
        try:
            removed = manager.cleanup_by_labels({"godsv.build_id": f"{WAVE}-a"})
            assert _accounted(removed, started_a.cid), removed.to_dict()
            assert _wait_absent(started_a.cid), "the scoped container was not removed"
            # the sibling scope is untouched: still there, still running
            assert not _container_gone(started_b.cid), "the other build was swept too"
            assert _labels_of(started_b.cid).get("godsv.build_id") == f"{WAVE}-b"
        finally:
            swept = manager.cleanup_by_labels(SWEEP_SCOPE)
            assert swept.ok or _accounted(swept, started_a.cid), swept.to_dict()
        assert _wait_wave_empty(), _our_wave_ids()
        assert _foreign_ids() == foreign_before, "a foreign container disappeared"
        assert _wait_until(lambda: _unexplained_new_ids(before) == set())

    def test_cleanup_refuses_every_foreign_shaped_scope(self, manager):
        """No scope outside ``godsv*`` may ever select a container for removal."""
        foreign_before = _foreign_ids()
        for scope in (
            {},
            {"purpose": "test"},
            {"com.docker.compose.project": "penpot-local"},
            {"godsv.owner": "skaldir", "com.docker.compose.project": "penpot-local"},
        ):
            refused = manager.cleanup_by_labels(scope)
            assert refused.ok is False, f"scope {scope} was not refused"
            assert refused.failure_class == "policy_violation", refused.to_dict()
            assert refused.policy == dr.POLICY_LABEL_SCOPE, refused.to_dict()
        # the foreign containers were never even looked at, let alone removed
        assert _foreign_ids() == foreign_before

    def test_a_foreign_container_is_invisible_to_our_listing(self, manager):
        """Our listing is label-filtered: a foreign container cannot enter it."""
        foreign = _foreign_ids()
        assert foreign, "expected the owner's foreign compose containers on this daemon"
        listing = manager.list_owned()
        assert listing.ok, listing.detail
        assert not set(foreign) & set(listing.payload["containers"]), listing.payload


# --------------------------------------------------------------------------- #
# multi-label scopes — the defect was found here, the fix is pinned here
# --------------------------------------------------------------------------- #


class TestMultiLabelScope:
    """A multi-label scope must emit ONE ``--filter label=…`` argument per label.

    The wave found this broken: ``list_by_labels`` joined the scope into a single
    comma-joined ``--filter label=a=1,b=2`` argument, which docker 29.6.1 reads as
    one label pair (value ``1,b=2``) and therefore matches **nothing** — so every
    multi-label sweep of the product (``cleanup_owned``, the tool builder's
    owner+build teardown) reported success while removing nothing. Measured then:
    0 hits for a container that carries both labels, while two separate
    ``--filter`` arguments return it.

    The tree now carries ``docker_runtime.label_filter_args()`` (one ``--filter``
    per label, fixed 2026-09-18). Until that fix landed, the first test below was
    a strict ``xfail``; it is now a plain test that pins the fixed behaviour
    against the daemon.
    """

    def test_multi_label_scope_finds_and_removes_our_container(self, manager, image_digest):
        build_label = f"{WAVE}-multi"
        config = dr.RunConfig(
            image=IMAGE,
            image_digest=image_digest,
            labels={**TEST_LABELS, "godsv.build_id": build_label},
            network="none",
            entrypoint_argv=list(LONG_LIVED_ARGV),
        )
        started = manager.start(config, timeout=60.0)
        assert started.ok, started.detail
        cid = started.cid
        try:
            # what the daemon really answers for the two labels (AND, two filters)
            found_by_the_daemon = _ids(
                f"--filter={OWNER_FILTER}", f"--filter=label=godsv.build_id={build_label}"
            )
            assert _found(found_by_the_daemon, cid), "the daemon cannot find its own container"
            listing = manager.list_by_labels(
                {
                    dr.OWNER_LABEL_KEY: dr.OWNER_LABEL_VALUE,
                    "godsv.build_id": build_label,
                }
            )
            assert listing.ok, listing.detail
            assert _found(listing.payload["containers"], cid), listing.payload
            # the emitted filters are separate single-label arguments, never a
            # comma-joined pair (docker would read that as one label)
            filter_args = listing.payload.get("filter_args", [])
            assert all("," not in str(arg) for arg in filter_args), filter_args
            # the tool builder's OWN scope (owner + build id) removes it — that is
            # the sweep a build relies on and it used to be a silent no-op
            scoped = manager.cleanup_owned({"godsv.build_id": build_label})
            assert _accounted(scoped, cid), scoped.to_dict()
        finally:
            swept = manager.cleanup_by_labels(
                {dr.OWNER_LABEL_KEY: dr.OWNER_LABEL_VALUE, "godsv.build_id": build_label}
            )
            assert _accounted(swept, cid), swept.to_dict()
        assert _wait_absent(cid), "the scoped container was not removed"

    def test_single_label_scope_is_the_shape_that_works(self, manager, image_digest):
        """A one-label scope lists and removes correctly too (no regression)."""
        build_label = f"{WAVE}-single"
        config = dr.RunConfig(
            image=IMAGE,
            image_digest=image_digest,
            labels={**TEST_LABELS, "godsv.build_id": build_label},
            network="none",
            entrypoint_argv=list(LONG_LIVED_ARGV),
        )
        started = manager.start(config, timeout=60.0)
        assert started.ok, started.detail
        cid = started.cid
        listing = manager.list_by_labels({"godsv.build_id": build_label})
        assert listing.ok, listing.detail
        assert _found(listing.payload["containers"], cid), listing.payload
        removed = manager.cleanup_by_labels({"godsv.build_id": build_label})
        assert _accounted(removed, cid), removed.to_dict()
        assert _wait_absent(cid), "the scoped container was not removed"
        assert _wait_wave_empty(), _our_wave_ids()


# --------------------------------------------------------------------------- #
# EXPAND + the tool-builder probe path, end to end against the real daemon
# --------------------------------------------------------------------------- #

_EXPAND_SOURCE = '''"""Expand probe: echo a value back through a pinned container image."""
import json


def main(text: str = "expand") -> dict:
    return {"echoed": text}


if __name__ == "__main__":  # pragma: no cover
    print(json.dumps(main()))
'''

_EXPAND_TESTS = '''"""Plain unit tests for the generated adapter (no container needed)."""
import importlib.util
from pathlib import Path


def _load():
    path = Path(__file__).resolve().parents[1] / "container_echo.py"
    spec = importlib.util.spec_from_file_location("container_echo", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_echoes():
    assert _load().main("x")["echoed"] == "x"
'''


class TestExpandFromAContainer:
    def test_capability_is_derived_from_the_container_and_registered(
        self, manager, image_digest, runtime_dir
    ):
        """The whole EXPAND container half, measured:

        1. the capability is DERIVED from the pinned image — the container's own
           output is the evidence, and it names the image (derivation, not
           invention);
        2. the builder probes the capability *inside* the container and sweeps
           afterwards, scoped to owner + build label;
        3. the registered manifest carries ``runtime: docker`` and the digest.
        """
        before = _all_ids()
        foreign_before = _foreign_ids()

        # 1. derivation evidence straight out of the container
        derivation = manager.run(
            dr.RunConfig(
                image=IMAGE,
                image_digest=image_digest,
                labels=dict(TEST_LABELS),
                network="none",
                entrypoint_argv=list(PROBE_ARGV),
            ),
            timeout=60.0,
        )
        assert derivation.ok, f"{derivation.detail} / {derivation.stderr[:200]}"
        assert PROBE_MARKER in derivation.stdout, derivation.stdout
        assert ALPINE_MARKER in derivation.stdout, (
            f"the container did not identify its own image: {derivation.stdout!r}"
        )

        # 2. + 3. the builder pipeline with the container probe
        request = toolbuilder.ToolBuildRequest(
            capability="Echo a value through a pinned container image",
            reason="EXPAND integration test (container-derived capability)",
            name="container-echo",
            kind="cli",
            module="container_echo",
            source_code=_EXPAND_SOURCE,
            tests_code=_EXPAND_TESTS,
            entrypoint="main",
            sample_input={"text": "expand"},
            runtime="docker",
            image=IMAGE,
            image_digest=image_digest,
            healthcheck={"argv": list(PROBE_ARGV)},
        )
        result = toolbuilder.ToolBuilder(runtime_dir).build(request)
        assert result["ok"] is True, json.dumps(result, default=str)[:800]

        probe = result["container_probe"]["probe"]
        assert probe["ok"] is True, probe
        assert probe["exit_code"] == 0, probe
        assert PROBE_MARKER in probe["stdout"], probe
        assert ALPINE_MARKER in probe["stdout"], (
            f"the build probe did not run inside the image: {probe!r}"
        )
        assert result["container_probe"]["image_digest"] == image_digest

        # the sweep ran, scoped to OUR owner label + this build — nothing else,
        # and (since the F1 fix) that multi-label scope really lists what carries
        # both labels. `removed` is empty because the probe container is a
        # one-shot with `--rm`: it had already removed itself. The scan of the
        # scope is asserted here, the removal by a working multi-label scope in
        # TestMultiLabelScope (via `cleanup_owned`, the builder's own call).
        teardown = result["container_probe"]["teardown"]
        assert teardown["ok"] is True, teardown
        assert teardown["payload"]["scope"] == (
            f"{dr.OWNER_LABEL_KEY}={dr.OWNER_LABEL_VALUE},godsv.build_id={result['build_id']}"
        ), teardown
        assert teardown["payload"]["removed"] == [], teardown

        checks = {check["name"]: check["ok"] for check in result["gates"]}
        assert checks["container_probe"] is True and checks["container_teardown"] is True

        # the capability is registered with the digest committed
        manifest = yaml.safe_load(Path(result["manifest"]).read_text(encoding="utf-8"))
        assert manifest["runtime"] == "docker"
        assert manifest["image_digest"] == image_digest
        assert manifest["healthcheck"]["argv"] == PROBE_ARGV
        assert manifest["status"] == "ready"
        store = ToolStore(Path(runtime_dir) / "tools.db")
        try:
            spec = store.get(result["tool"])
        finally:
            store.close()
        assert spec is not None, f"{result['tool']} is not in the registry"
        indexed = {entry.name for entry in discovery.metadata_index(runtime_dir)}
        assert result["tool"] in indexed, indexed

        # and nothing of ours is left on the shared daemon
        assert _wait_wave_empty(), _our_wave_ids()
        assert [
            cid for cid in _our_ids() if _labels_of(cid).get("godsv.test_wave") == WAVE
        ] == []
        assert _foreign_ids() == foreign_before
        assert _wait_until(lambda: _unexplained_new_ids(before) == set())

    def test_builder_refuses_a_mutable_tag_before_any_container(self, runtime_dir):
        """No pin-shaped digest ⇒ the build is blocked before docker is called."""
        before = _all_ids()
        request = toolbuilder.ToolBuildRequest(
            capability="Echo a value through an unpinned container image",
            name="unpinned-echo",
            kind="cli",
            module="unpinned_echo",
            source_code=_EXPAND_SOURCE,
            tests_code=_EXPAND_TESTS,
            entrypoint="main",
            sample_input={"text": "expand"},
            runtime="docker",
            image=IMAGE,
            image_digest="",
            healthcheck={"argv": ["echo", "must not run"]},
        )
        result = toolbuilder.ToolBuilder(runtime_dir).build(request)
        assert result["ok"] is False
        assert result["blocked"] == "manifest_v2", json.dumps(result, default=str)[:400]
        assert "pin-shaped image_digest" in result["error"], result["error"]
        assert "untraceable mutable tag" in result["error"], result["error"]
        assert "container_probe" not in result, "a refused build must not start a container"
        assert _wait_until(lambda: _unexplained_new_ids(before) == set())
        assert _wait_wave_empty(), _our_wave_ids()


# --------------------------------------------------------------------------- #
# the execution-boundary seam the runtime manager is registered on
# --------------------------------------------------------------------------- #


class TestExecutorSeam:
    """The manager itself is the container executor (``available()`` + ``run_job()``).

    This was the wave's open item: ``DockerRuntimeManager`` documented that it
    satisfies ``godsv.execution.ContainerExecutor`` while implementing no
    ``run_job()``, so registering it on the boundary raised ``AttributeError``
    instead of answering with a ``RunResult``. The method now exists for real (one
    job = one one-shot ``--rm`` run, label-scoped, structured result), which is why
    the former strict ``xfail`` is a plain, passing expectation again.
    """

    def test_registered_manager_is_callable_through_the_boundary(self, manager):
        """A job the runtime cannot map is refused structurally — never AttributeError."""
        try:
            execution.register_container_executor(manager)
            result = execution.get_container_executor().run_job(
                object(), execution.RunPolicy()
            )
        finally:
            execution.register_container_executor(execution.NullExecutor())
        assert isinstance(result, execution.RunResult)
        assert result.ok is False, "an unprepared job must never be certified as ok"
        assert result.status == execution.STATUS_NOT_CERTIFIABLE, result
        assert result.sandboxed is False
        assert "object" in result.detail, result.detail

    def test_a_real_job_runs_through_the_boundary_and_leaves_nothing_behind(
        self, manager, image_digest
    ):
        """Live: one real container job through the registered boundary.

        The job is a ``toolbuilder.ContainerJob`` (the builder's job type) and the
        executor is the bare runtime manager — the duck-typed seam. Asserted: the
        job ran *inside* the image (the alpine release file, which this Fedora host
        does not have), and afterwards zero containers of ours are left on the
        shared daemon while the foreign ``penpot-local`` set is unchanged.
        """
        before = _all_ids()
        foreign_before = _foreign_ids()
        job = toolbuilder.ContainerJob(
            image=IMAGE,
            image_digest=image_digest,
            argv=["sh", "-c", f"echo {JOB_MARKER}; cat /etc/os-release"],
            labels={"godsv.test_wave": WAVE, "godsv.job": "seam-live"},
            network="none",
            readonly_rootfs=True,
        )
        try:
            execution.register_container_executor(manager)
            result = execution.get_container_executor().run_job(
                job, execution.RunPolicy()
            )
        finally:
            execution.register_container_executor(execution.NullExecutor())

        assert isinstance(result, execution.RunResult)
        assert result.ok is True and result.status == execution.STATUS_OK, result
        assert result.exit_code == 0, result
        assert result.sandboxed is True, "a real container run is a sandboxed run"
        assert JOB_MARKER in result.stdout, result.stdout
        assert ALPINE_MARKER in result.stdout, (
            f"the job did not run inside the image: {result.stdout!r}"
        )

        # `--rm` on the one-shot path: the container removed itself …
        scoped = manager.list_owned({"godsv.job": "seam-live"})
        assert scoped.ok, scoped.detail
        assert scoped.payload["containers"] == [], scoped.payload
        # … nothing else of ours is left, and the foreign containers are untouched
        assert _wait_wave_empty(), _our_wave_ids()
        assert _foreign_ids() == foreign_before
        assert _wait_until(lambda: _unexplained_new_ids(before) == set()), (
            _unexplained_new_ids(before)
        )
