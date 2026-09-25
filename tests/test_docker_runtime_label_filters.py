"""Regression — a label scope emits ONE ``--filter label=key=value`` PER label.

The defect (measured live on Server 29.6.1): ``DockerRuntimeManager.list_by_labels``
joined the whole scope into a single argument,
``["docker", "ps", "-a", "--filter", "label=godsv.owner=skaldir,godsv.build_id=b1", …]``.
docker reads that one value as ONE label pair (its value being ``skaldir,godsv.build_id=b1``)
and therefore matches **nothing** — exit 0, zero ids. Every multi-label teardown
(``cleanup_owned``, the tool builder's owner+build probe sweep) consequently reported
``removed 0 leftovers`` while the container stayed on the daemon: a false green, never an
error.

What this module pins:

* the emitted argv carries exactly one ``--filter`` per label and **no** comma inside a
  single ``--filter`` value (pure, no daemon);
* the helper is the ONE builder of a filter argument in the tree, and every listing goes
  through it (no second, comma-joining call site can come back silently);
* ``list_by_labels`` / ``cleanup_by_labels`` / ``cleanup_owned`` / ``list_owned`` really
  find and remove multi-label leftovers — driven against a faithful in-process stand-in of
  the daemon's own filter semantics (a comma inside one ``--filter`` value matches nothing,
  exactly like the real 29.6.1);
* one live test drives a **real** daemon: two labelled containers are created, removed by a
  two-label scope, and the foreign ``penpot-local`` containers of the owner's compose stack
  are counted before and after (never touched). Skipped when docker/image are absent.

Nothing here removes a container it did not label itself: every live scope carries a wave
label unique to this module, so parallel waves of the product are out of reach.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from skaldir import docker_runtime as dr

DIGEST = "sha256:" + "a" * 64
SRC = Path(__file__).resolve().parents[1] / "src" / "skaldir"

#: Two labels is the smallest multi-label scope — the shape that was broken.
SCOPE = {"godsv.owner": "skaldir", "godsv.build_id": "b-1"}

#: Registry: a container of ours (owner + build) and seven foreign ones.
OURS = [
    ("ours00000001", {"godsv.owner": "skaldir", "godsv.build_id": "b-1", "purpose": "test"}),
    ("ours00000002", {"godsv.owner": "skaldir", "godsv.build_id": "b-2", "purpose": "test"}),
    ("ours00000003", {"godsv.owner": "skaldir", "godsv.build_id": "b-1", "purpose": "test"}),
]
FOREIGN = [
    (f"foreign0000{index}", {"com.docker.compose.project": "penpot-local"})
    for index in range(7)
]


# --------------------------------------------------------------------------- #
# fixtures: a recording manager and a faithful stand-in for the daemon
# --------------------------------------------------------------------------- #


@pytest.fixture()
def recorded(monkeypatch):
    """A manager whose docker calls are recorded as argv (nothing is executed)."""
    manager = dr.DockerRuntimeManager(docker_bin="docker")
    calls: list[list[str]] = []

    def stub(argv, **_kw):
        calls.append(list(argv))
        return dr.CmdResult(0, "ours00000001", "")

    monkeypatch.setattr(manager, "_invoke_raw", stub)
    return SimpleNamespace(manager=manager, calls=calls)


def _label_filters_of(argv: list[str]) -> tuple[dict[str, str], bool]:
    """(scope the daemon would AND, impossible?) — the daemon's own reading.

    docker parses ONE ``--filter label=…`` value into ONE label pair: a comma inside
    that value belongs to the value (``label=a=1,b=2`` means label ``a`` with the
    value ``1,b=2``), which no container carries — so such a filter matches nothing.
    The real ``docker ps`` behaves this way on Server 29.6.1 (measured: 0 hits for a
    container carrying both labels, while two separate ``--filter`` arguments return
    it); a stand-in that split on the comma would be *more forgiving* than the daemon
    and would have hidden this defect (it did: see the P4a fake docker, now fixed too).
    """
    wanted: dict[str, str] = {}
    for index, item in enumerate(argv):
        if item != "--filter" or index + 1 >= len(argv):
            continue
        value = str(argv[index + 1])
        if not value.startswith("label="):
            continue
        pair = value[len("label="):]
        if "," in pair:
            return {}, True
        key, _sep, text = pair.partition("=")
        wanted[key] = text
    return wanted, False


class FaithfulDaemon:
    """In-process ``docker`` CLI with the daemon's real label-filter semantics."""

    def __init__(self, containers: list[tuple[str, dict[str, str]]]) -> None:
        self.containers = {cid: dict(labels) for cid, labels in containers}
        self.calls: list[list[str]] = []

    # -- the CLI surface this layer uses ------------------------------------- #
    def __call__(self, argv, **_kw) -> dr.CmdResult:
        argv = list(argv)
        self.calls.append(list(argv))
        if argv[:2] == ["docker", "ps"]:
            self.last_scope, impossible = _label_filters_of(argv[2:])
            if impossible:
                return dr.CmdResult(0, "", "")
            ids = [
                cid
                for cid, labels in self.containers.items()
                if all(str(labels.get(key, "")) == value for key, value in self.last_scope.items())
            ]
            # docker prints SHORT ids
            return dr.CmdResult(0, "\n".join(cid[:12] for cid in ids), "")
        if argv[:2] == ["docker", "rm"]:
            targets = [item for item in argv[2:] if not item.startswith("-")]
            removed = [
                cid
                for cid in list(self.containers)
                if any(cid.startswith(target) for target in targets)
            ]
            for cid in removed:
                del self.containers[cid]
            return dr.CmdResult(0, "\n".join(removed), "")
        return dr.CmdResult(0, "", "")


@pytest.fixture()
def daemon(monkeypatch):
    """A manager driving the faithful stand-in — a real multi-label sweep, no docker."""
    fake = FaithfulDaemon([*OURS, *FOREIGN])
    manager = dr.DockerRuntimeManager(docker_bin="docker")
    monkeypatch.setattr(manager, "_invoke_raw", fake)
    return SimpleNamespace(manager=manager, daemon=fake)


# --------------------------------------------------------------------------- #
# the emitted argv: one --filter per label, never a comma inside one argument
# --------------------------------------------------------------------------- #


class TestOneFilterPerLabelArgv:
    def test_two_labels_are_two_filter_arguments(self, recorded):
        listing = recorded.manager.list_by_labels(SCOPE)
        assert listing.ok, listing.detail
        assert recorded.calls == [
            [
                "docker", "ps", "-a",
                "--filter", "label=godsv.owner=skaldir",
                "--filter", "label=godsv.build_id=b-1",
                "--format", "{{.ID}}",
            ]
        ], recorded.calls

    @pytest.mark.parametrize(
        "scope",
        [
            {"godsv.owner": "skaldir"},
            SCOPE,
            {"godsv.owner": "skaldir", "godsv.build_id": "b-1", "godsv.capability": "sha256"},
        ],
    )
    def test_a_comma_never_appears_inside_a_single_filter(self, recorded, scope):
        """The regression: a comma-joined value matches NOTHING on the daemon."""
        listing = recorded.manager.list_by_labels(scope)
        assert listing.ok, listing.detail
        argv = recorded.calls[0]
        values = [argv[index + 1] for index, item in enumerate(argv) if item == "--filter"]
        assert len(values) == len(scope), values
        assert values == [f"label={key}={value}" for key, value in scope.items()], values
        assert not any("," in value for value in values), values
        assert not any("," in arg for arg in argv), argv
        # …and the daemon's own reading of the line is the full AND scope
        wanted, impossible = _label_filters_of(argv[2:])
        assert impossible is False
        assert wanted == dict(scope)

    def test_the_payload_carries_the_filters_that_were_emitted(self, recorded):
        listing = recorded.manager.list_by_labels(SCOPE)
        assert listing.payload["filter_args"] == [
            "label=godsv.owner=skaldir",
            "label=godsv.build_id=b-1",
        ], listing.payload
        assert listing.payload["filters"] == "godsv.owner=skaldir,godsv.build_id=b-1"
        assert listing.payload["labels"] == SCOPE

    def test_cleanup_owned_sweeps_with_one_filter_per_label(self, recorded):
        """``cleanup_owned`` is the owner+build scope — the shape that was broken."""
        result = recorded.manager.cleanup_owned({"godsv.build_id": "b-9"})
        assert result.ok, result.detail
        listing_call = recorded.calls[0]
        assert listing_call[:2] == ["docker", "ps"]
        values = [
            listing_call[index + 1]
            for index, item in enumerate(listing_call)
            if item == "--filter"
        ]
        assert values == ["label=godsv.owner=skaldir", "label=godsv.build_id=b-9"], values
        assert not any("," in arg for arg in listing_call), listing_call
        assert result.payload["scope"] == "godsv.owner=skaldir,godsv.build_id=b-9"

    def test_list_owned_keeps_its_single_label_scope(self, recorded):
        recorded.manager.list_owned()
        assert recorded.calls == [
            ["docker", "ps", "-a", "--filter", "label=godsv.owner=skaldir", "--format", "{{.ID}}"]
        ], recorded.calls


# --------------------------------------------------------------------------- #
# the helper is the ONE builder — no second, comma-joining call site
# --------------------------------------------------------------------------- #


class TestOneBuilderOfTheFilterLine:
    def test_only_docker_runtime_builds_a_label_filter_argument(self):
        """A static guard: the filter ARGUMENT is assembled in one place only.

        A line that prints a command for a human (``"docker ps -a --filter label=…"``
        as CLI evidence) is not an argument this layer emits and is exempt; a
        `label={…}` f-string that is NOT such a hint would be a second builder.
        """
        builders: list[str] = []
        for path in sorted(SRC.glob("*.py")):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if not re.search(r"""["']label=\{""", line):
                    continue
                if re.search(r"""["']docker ps""", line):
                    continue
                builders.append(f"{path.name}:{number}")
        assert {entry.split(":")[0] for entry in builders} == {"docker_runtime.py"}, builders

    def test_every_listing_goes_through_label_filter_args(self, monkeypatch, recorded):
        """The argv really comes from the helper — not from a second code path."""
        calls: list[dict] = []
        original = dr.label_filter_args

        def spy(labels):
            calls.append(dict(labels))
            return original(labels)

        monkeypatch.setattr(dr, "label_filter_args", spy)
        recorded.manager.list_by_labels(SCOPE)
        recorded.manager.list_owned({"godsv.build_id": "b-1"})
        recorded.manager.cleanup_by_labels({"godsv.owner": "skaldir", "godsv.build_id": "b-1"})
        assert calls == [
            SCOPE,
            {"godsv.owner": "skaldir", "godsv.build_id": "b-1"},
            {"godsv.owner": "skaldir", "godsv.build_id": "b-1"},
        ], calls

    def test_the_old_comma_joined_form_is_what_the_stub_matches_nothing_with(self, daemon):
        """Self-check of the stand-in: the comma-joined value really matches nothing.

        Without this, the sweeps below would pass for the wrong reason (a stub that
        is more forgiving than the daemon).
        """
        listing = daemon.manager._invoke(
            [
                "docker", "ps", "-a",
                "--filter", "label=godsv.owner=skaldir,godsv.build_id=b-1",
                "--format", "{{.ID}}",
            ]
        )
        assert listing.stdout == "", "the comma-joined single filter must match nothing"
        both = daemon.manager._invoke(
            [
                "docker", "ps", "-a",
                "--filter", "label=godsv.owner=skaldir",
                "--filter", "label=godsv.build_id=b-1",
                "--format", "{{.ID}}",
            ]
        )
        assert both.stdout.split() == ["ours00000001", "ours00000003"]


# --------------------------------------------------------------------------- #
# the teardown paths that relied on the multi-label filter
# --------------------------------------------------------------------------- #


class TestMultiLabelTeardownWorksAgain:
    def test_list_by_labels_finds_a_container_carrying_both_labels(self, daemon):
        listing = daemon.manager.list_by_labels(SCOPE)
        assert listing.ok, listing.detail
        assert listing.payload["containers"] == ["ours00000001", "ours00000003"]

    def test_a_two_label_sweep_removes_exactly_that_scope(self, daemon):
        result = daemon.manager.cleanup_by_labels(
            {"godsv.owner": "skaldir", "godsv.build_id": "b-1"}
        )
        assert result.ok, result.detail
        assert result.payload["removed"] == ["ours00000001", "ours00000003"]
        assert set(daemon.daemon.containers) == {
            "ours00000002",  # the sibling build survives
            *[cid for cid, _labels in FOREIGN],  # and every foreign container
        }

    def test_cleanup_owned_sweeps_ours_and_never_a_foreign_one(self, daemon):
        result = daemon.manager.cleanup_owned()
        assert result.ok, result.detail
        assert result.payload["removed"] == [cid for cid, _labels in OURS]
        assert set(daemon.daemon.containers) == {cid for cid, _labels in FOREIGN}
        flat = " ".join(" ".join(call) for call in daemon.daemon.calls)
        assert "prune" not in flat and "name=" not in flat and "penpot" not in flat
        # the removal scope came from the LABEL-filtered listing: ids only
        removals = [call for call in daemon.daemon.calls if call[:2] == ["docker", "rm"]]
        assert all(len(call) == 4 and call[2] == "-f" for call in removals), removals

    def test_cleanup_owned_narrowed_to_one_build_leaves_the_other_alone(self, daemon):
        result = daemon.manager.cleanup_owned({"godsv.build_id": "b-2"})
        assert result.ok, result.detail
        assert result.payload["removed"] == ["ours00000002"]
        assert set(daemon.daemon.containers) == {
            "ours00000001", "ours00000003", *[cid for cid, _labels in FOREIGN]
        }

    def test_list_owned_never_listing_a_foreign_container(self, daemon):
        listing = daemon.manager.list_owned()
        assert listing.ok, listing.detail
        assert listing.payload["containers"] == [cid for cid, _labels in OURS]
        assert not set(listing.payload["containers"]) & {cid for cid, _labels in FOREIGN}


# --------------------------------------------------------------------------- #
# live: the real daemon — two labelled containers, removed by a two-label scope
# --------------------------------------------------------------------------- #

IMAGE = "alpine:3.20"
#: Unique to this module: no other wave's containers can fall into its scope.
WAVE = "label-filter-fix"
LONG_LIVED_ARGV = ["sh", "-c", "trap 'exit 0' TERM; while :; do sleep 0.2; done"]
FOREIGN_FILTER = "label=com.docker.compose.project=penpot-local"
SETTLE_SECONDS = 20.0


def _live_ready() -> tuple[bool, str]:
    if not shutil.which("docker"):
        return False, "no docker binary on PATH"
    manager = dr.DockerRuntimeManager()
    ok, detail = manager.available()
    if not ok:
        return False, detail
    if not manager.resolve_digest(IMAGE):
        return False, f"{IMAGE} is not local"
    return True, detail


_LIVE_OK, _LIVE_DETAIL = _live_ready()
live_daemon = pytest.mark.skipif(
    not _LIVE_OK, reason=f"live docker unavailable ({_LIVE_DETAIL})"
)


def _docker(*args: str, timeout: float = 120.0) -> subprocess.CompletedProcess:
    """The independent observer: the raw CLI, never the code under test."""
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)


def _ids(*filters: str) -> list[str]:
    out = _docker("ps", "-a", *filters, "--format", "{{.ID}}")
    assert out.returncode == 0, (out.stderr or out.stdout)[:200]
    return [line for line in out.stdout.split() if line]


def _short(cid: str) -> str:
    return str(cid)[:12]


def _found(ids, cid: str) -> bool:
    return any(str(item).startswith(_short(cid)) or str(cid).startswith(str(item)) for item in ids)


def _accounted(result, cid: str) -> bool:
    """The report accounts for ``cid`` — removed, or as the documented removal race."""
    payload = getattr(result, "payload", None) or {}
    if _found(payload.get("removed", []), cid):
        return True
    if result.ok and not payload.get("errors"):
        return True
    return bool(
        result.failure_class == "cleanup_partial"
        and any(_short(cid) in str(err) for err in payload.get("errors", []))
    )


def _wait_until(predicate, *, timeout: float = SETTLE_SECONDS, interval: float = 0.2) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        if predicate():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(interval)


def _wave_scope() -> dict[str, str]:
    return {
        dr.OWNER_LABEL_KEY: dr.OWNER_LABEL_VALUE,
        "godsv.test_wave": WAVE,
    }


@pytest.fixture()
def live_teardown():
    """Label discipline: this module's wave scope is empty before and after."""
    manager = dr.DockerRuntimeManager()
    foreign_before = _ids(f"--filter={FOREIGN_FILTER}")
    swept = manager.cleanup_by_labels(_wave_scope())
    assert swept.ok or swept.failure_class == "cleanup_partial", swept.to_dict()
    assert _wait_until(lambda: not _ids(
        f"--filter=label={dr.OWNER_LABEL_KEY}={dr.OWNER_LABEL_VALUE}",
        f"--filter=label=godsv.test_wave={WAVE}",
    )), "baseline: no container of this wave may exist"
    yield SimpleNamespace(manager=manager, foreign_before=foreign_before)
    leftovers = _ids(
        f"--filter=label={dr.OWNER_LABEL_KEY}={dr.OWNER_LABEL_VALUE}",
        f"--filter=label=godsv.test_wave={WAVE}",
    )
    if leftovers:
        cleaned = dr.DockerRuntimeManager().cleanup_by_labels(_wave_scope())
        assert _wait_until(lambda: not _ids(
            f"--filter=label={dr.OWNER_LABEL_KEY}={dr.OWNER_LABEL_VALUE}",
            f"--filter=label=godsv.test_wave={WAVE}",
        )), f"leaked and could not be swept: {leftovers} / {cleaned.to_dict()}"
        pytest.fail(f"containers leaked by this wave and swept afterwards: {leftovers}")


@live_daemon
class TestLiveLabelFilterTeardown:
    def test_two_labelled_containers_are_removed_by_a_two_label_scope(
            self, tmp_path, live_teardown):
        """The live regression: 2 of ours created, removed by label, 0 remain, foreign intact."""
        manager = live_teardown.manager
        digest = manager.resolve_digest(IMAGE)
        ours: list[str] = []
        for suffix in ("a", "b"):
            config = dr.RunConfig(
                image=IMAGE,
                image_digest=digest,
                labels={
                    "purpose": "test",
                    "godsv.test_wave": WAVE,
                    "godsv.build_id": f"{WAVE}-{suffix}",
                },
                network="none",
                entrypoint_argv=list(LONG_LIVED_ARGV),
            )
            started = manager.start(config, cid_file=tmp_path / f"{suffix}.cid", timeout=60.0)
            assert started.ok, started.detail
            assert started.cid, "a started container must carry a real cid"
            ours.append(started.cid)
        assert len(ours) == 2

        wave_before = _ids(
            f"--filter=label={dr.OWNER_LABEL_KEY}={dr.OWNER_LABEL_VALUE}",
            f"--filter=label=godsv.test_wave={WAVE}",
        )
        assert len(wave_before) == 2, wave_before

        # the two-label listing finds BOTH — the multi-label scope that was broken
        listing = manager.list_by_labels(_wave_scope())
        assert listing.ok, listing.detail
        assert all("," not in value for value in listing.payload["filter_args"]), listing.payload
        for cid in ours:
            assert _found(listing.payload["containers"], cid), (cid, listing.payload)
        # independent oracle: the raw daemon agrees with two separate filters
        raw = _ids(
            f"--filter=label={dr.OWNER_LABEL_KEY}={dr.OWNER_LABEL_VALUE}",
            f"--filter=label=godsv.test_wave={WAVE}",
        )
        for cid in ours:
            assert _found(raw, cid), (cid, raw)
        assert not set(raw) & set(live_teardown.foreign_before), raw

        # the sweep of the same two-label scope removes exactly ours
        removed = manager.cleanup_by_labels(_wave_scope(), timeout=60.0)
        for cid in ours:
            assert _accounted(removed, cid), (cid, removed.to_dict())
        assert _wait_until(lambda: not _ids(
            f"--filter=label={dr.OWNER_LABEL_KEY}={dr.OWNER_LABEL_VALUE}",
            f"--filter=label=godsv.test_wave={WAVE}",
        )), "0 containers of this wave must remain"
        assert manager.list_by_labels(_wave_scope()).payload["containers"] == []
        # the foreign stack is untouched, counted independently
        assert _ids(f"--filter={FOREIGN_FILTER}") == live_teardown.foreign_before

    def test_the_sweep_is_idempotent_and_reports_a_clean_zero(self, live_teardown):
        first = live_teardown.manager.cleanup_by_labels(_wave_scope())
        second = live_teardown.manager.cleanup_by_labels(_wave_scope())
        for result in (first, second):
            assert result.ok, result.to_dict()
            assert result.payload["removed"] == [], result.payload
        assert _ids(f"--filter={FOREIGN_FILTER}") == live_teardown.foreign_before
