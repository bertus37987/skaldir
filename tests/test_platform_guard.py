"""Platform coverage: what this host/platform really enforces, per primitive — gap 11 of `MVP/84`.

The enforcement primitives (Landlock, seccomp, ``PR_SET_NO_NEW_PRIVS``, cgroup v2 ``pids.max``,
the bubblewrap namespace view) are **Linux-only**. Before this wave a macOS/Windows user got
nothing from the sandbox and nothing in the product said so, and a host where a primitive was not
applied degraded silently. These tests pin the three things that close that gap:

* every primitive carries ``enforced`` / ``degraded`` / ``unavailable`` **and a reason**;
* a simulated non-Linux platform can never be reported as enforced, and the detection gates keep
  Linux-only syscall numbers off a kernel that has no such syscall;
* the status reaches the surfaces a user reads: ``skaldir permission show``, the approval
  verdict's enforcement payload, and the ``ENFORCE`` layer of ``skaldir selftest``.

The host-dependent assertions are written both ways (enforced *or* the reason), so a green run
never claims enforcement this host does not have.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from skaldir import approval as A
from skaldir import approval_cli as AC
from skaldir import cli_contract
from skaldir import permission_profile as P
from skaldir import security_linux as S


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    (root / ".git").mkdir(parents=True)
    (root / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    return root


@pytest.fixture(autouse=True)
def _clean_abi_cache():
    """The ABI answer is cached per process — a simulated-platform test must not leak into one."""
    S._ABI_CACHE.clear()
    yield
    S._ABI_CACHE.clear()


def _row(rows: list[dict], primitive: str) -> dict:
    matches = [row for row in rows if row["primitive"] == primitive]
    assert len(matches) == 1, f"expected exactly one {primitive!r} row, got {len(matches)}"
    return matches[0]


# ------------------------------------------------------------------ the model


def test_the_coverage_model_reports_every_primitive_with_a_status_and_a_reason() -> None:
    """One row per primitive, a status from the three-value vocabulary, and never a bare claim."""
    rows = [row.to_dict() for row in S.platform_coverage()]
    assert [row["primitive"] for row in rows] == list(S.PRIMITIVES)
    for row in rows:
        assert row["status"] in S.COVERAGE_STATUSES
        assert row["requires"], "every primitive must name what it needs"
        assert row["scope"] and row["fallback"] and row["loses"]
        if row["status"] == S.STATUS_ENFORCED:
            assert row["applied"] is True
        else:
            # the whole point: a downgrade is *named*, never silent
            assert row["reason"], f"{row['primitive']} is {row['status']} with no reason"
            assert row["applied"] is False


def test_a_linux_host_with_the_caps_declared_reaches_enforced_on_every_primitive(
    workspace: Path,
) -> None:
    """The positive case, measured — and skipped (with the reason) when a piece is absent."""
    missing = [
        name
        for name, ok in (
            ("landlock", S.landlock_abi() >= S.LANDLOCK_ABI_MIN),
            ("seccomp", S.audit_arch() is not None),
            ("bwrap", bool(S._which("bwrap"))),
        )
        if not ok
    ]
    if missing:
        pytest.skip(f"this host lacks {', '.join(missing)} — cannot measure the enforced case")
    profile = P.default_profile(workspace).with_changes(
        caps={**P.default_profile(workspace).caps.to_dict(), "max_processes": 8,
              "max_memory_bytes": 268435456}
    )
    verdict = profile.platform_verdict()
    assert verdict["verdict"] == S.STATUS_ENFORCED, verdict["notes"]
    assert verdict["counts"][S.STATUS_ENFORCED] == len(S.PRIMITIVES)
    assert verdict["execution_refused"] is False
    assert verdict["critical_not_enforced"] == []
    assert verdict["notes"] == []


def test_the_default_profile_reports_the_two_undeclared_caps_as_degraded(workspace: Path) -> None:
    """The default declares no process/memory cap. That is a *reported* downgrade, not silence."""
    profile = P.default_profile(workspace)
    rows = profile.platform_coverage()
    assert profile.platform_want()["pids_cgroup"] is False
    assert profile.platform_want()["rlimits"] is False
    for primitive, needle in (("pids_cgroup", "max_processes"), ("rlimits", "max_memory_bytes")):
        row = _row(rows, primitive)
        assert row["status"] == S.STATUS_DEGRADED
        assert row["applied"] is False
        assert needle in row["reason"]
    verdict = profile.platform_verdict()
    assert set(verdict["degraded"]) >= {"pids_cgroup", "rlimits"}
    assert "coverage" in verdict["line"]


def test_a_primitive_declared_off_is_degraded_and_says_why(workspace: Path) -> None:
    """`linux_security.landlock = 'off'` must not be reported as enforcement of *something*."""
    base = P.default_profile(workspace)
    off = base.with_changes(
        linux_security={**base.linux_security.to_dict(), "landlock": "off", "seccomp": "off"}
    )
    rows = off.platform_coverage()
    for primitive in ("landlock", "seccomp"):
        row = _row(rows, primitive)
        assert row["status"] != S.STATUS_ENFORCED
        assert "declared off" in row["reason"]
    assert {"landlock", "seccomp"} <= set(off.platform_verdict()["critical_not_enforced"])


# ------------------------------------------------------- a non-Linux platform


def test_a_macos_host_is_unavailable_per_primitive_with_the_platform_in_the_reason() -> None:
    report = S.platform_report(platform="darwin", kernel="23.6.0")
    assert report["supported"] is False
    assert report["platform"] == "darwin"
    assert report["verdict"] == S.STATUS_UNAVAILABLE
    assert report["parity_requirements"], "parity must name the work, not a wish"
    for primitive in ("landlock", "seccomp", "no_new_privs", "namespaces", "pids_cgroup"):
        row = _row(report["primitives"], primitive)
        assert row["status"] == S.STATUS_UNAVAILABLE
        assert row["applied"] is False
        assert "darwin" in row["reason"]
    assert "darwin is not a supported platform" in report["reason"]


def test_a_windows_host_is_reported_unavailable_and_not_supported() -> None:
    report = S.platform_report(platform="win32", kernel="10.0.22631", rlimit_ok=False)
    assert report["supported"] is False
    assert report["verdict"] == S.STATUS_UNAVAILABLE
    for primitive in ("landlock", "seccomp", "no_new_privs", "namespaces", "pids_cgroup"):
        assert _row(report["primitives"], primitive)["status"] == S.STATUS_UNAVAILABLE
    assert _row(report["primitives"], "rlimits")["status"] == S.STATUS_UNAVAILABLE


def test_a_simulated_platform_is_labelled_and_cannot_invent_enforcement() -> None:
    """A caller's override can only make the report *worse* — never manufacture enforcement."""
    rows = S.platform_coverage(platform="darwin", mechanism=S.MECHANISM_NONE, rlimit_ok=True)
    assert all(row.measured.get("simulated") is True for row in rows)
    rlimits = [row for row in rows if row.primitive == "rlimits"][0]
    assert rlimits.status == S.STATUS_DEGRADED, (
        "macOS has setrlimit(RLIMIT_AS), but with no mechanism no child ever reaches it — "
        "reporting it as enforced would be the silent downgrade this wave removes"
    )
    assert "never runs" in rlimits.reason
    verdict = S.coverage_verdict(rows)
    assert verdict["verdict"] == S.STATUS_UNAVAILABLE


def test_the_platform_gate_keeps_linux_syscalls_off_a_non_linux_kernel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On a non-Linux interpreter no Landlock/seccomp/prctl call may be issued *at all*.

    The bug this pins: the syscall table is keyed by *machine name* (``arm64``), and a macOS
    arm64 host would have called ``syscall(444)`` — on Darwin that index is a different syscall.
    """
    calls: list[str] = []

    class Spy:
        def __getattr__(self, name: str):  # pragma: no cover — only touched on a regression

            def _record(*_args, **_kwargs):
                calls.append(name)
                raise AssertionError(f"libc.{name} must not be called on a non-Linux platform")

            return _record

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(S, "_libc", lambda: Spy())
    S._ABI_CACHE.clear()

    assert S.detected_platform() == "darwin"
    assert S.is_linux() is False
    assert S.landlock_abi(refresh=True) == 0
    assert "darwin" in S.landlock_unavailable_reason()
    assert S.audit_arch() is None
    ok, path, reason = S.pids_cgroup_support()
    assert (ok, path) == (False, "")
    assert "darwin" in reason
    with pytest.raises(S.SecurityRefusal) as refused:
        S.set_no_new_privs()
    assert refused.value.control == S.CONTROL_NO_NEW_PRIVS
    assert "darwin" in refused.value.reason
    assert calls == [], f"these libc calls must not happen on darwin: {calls}"


# ------------------------------------------------------- no mechanism at all


def test_no_mechanism_means_the_run_is_refused_not_run_unenforced(workspace: Path) -> None:
    """Every kernel control off ⇒ the command is *refused*, and the coverage says so."""
    base = P.default_profile(workspace)
    bare = base.with_changes(
        sandbox_backend="none",
        linux_security={**base.linux_security.to_dict(), "landlock": "off", "seccomp": "off"},
    )
    assert bare.mechanism()[0] == S.MECHANISM_NONE
    verdict = bare.platform_verdict()
    assert verdict["execution_refused"] is True
    assert verdict["verdict"] in (S.STATUS_DEGRADED, S.STATUS_UNAVAILABLE)
    assert set(verdict["critical_not_enforced"]) >= {"landlock", "seccomp", "namespaces"}

    run = P.run_in_sandbox(bare, "echo SHOULD-NOT-RUN", timeout=20)
    assert run.blocked is True
    assert run.exit_code == 3
    assert "SHOULD-NOT-RUN" not in run.output
    assert "fail-closed" in run.blocked_reason


# ------------------------------------------------------------- the surfaces


def test_the_permission_profile_output_carries_the_coverage(workspace: Path) -> None:
    """``skaldir permission show``: the JSON payload *and* the human render name the statuses."""
    profile = P.default_profile(workspace)
    payload = AC._profile_report(profile, stored_path="", sources={}, probe=False)
    assert [row["primitive"] for row in payload["platform_coverage"]] == list(S.PRIMITIVES)
    assert payload["platform_verdict"]["verdict"] in S.COVERAGE_STATUSES
    text = "\n".join(AC._render_profile(payload, full=True))
    assert "enforcement primitives on this platform (linux)" in text
    for row in payload["platform_coverage"]:
        assert f"{row['primitive']}" in text


def test_the_approval_enforcement_payload_carries_the_coverage(workspace: Path) -> None:
    """A verdict's "under what?" answer must not be unable to say "nothing, on this platform"."""
    snapshot = A.enforcement_snapshot(P.default_profile(workspace))
    assert snapshot["platform_verdict"]["verdict"] in S.COVERAGE_STATUSES
    assert snapshot["platform_verdict"]["line"]
    assert [row["primitive"] for row in snapshot["platform_coverage"]] == list(S.PRIMITIVES)


def test_the_coverage_verb_prints_a_table_and_exits_zero_on_a_fully_enforced_host(
    capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`python -m skaldir.security_linux coverage` — the user-facing "what do I get here?"."""
    report = S.platform_report()
    code = S.main(["coverage"])
    out = capsys.readouterr().out
    assert code == (0 if report["verdict"] == S.STATUS_ENFORCED else 3)
    assert "supported=" in out and "coverage=" in out
    for primitive in S.PRIMITIVES:
        assert primitive in out
    assert capsys.readouterr().err == ""


def test_the_coverage_verb_exits_three_and_names_the_reason_when_a_primitive_is_not_enforced(
    capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A downgrade must be visible *and* must not exit 0 — otherwise a script reads it as
    covered."""
    degraded = S.platform_report(platform="darwin", rlimit_ok=False)
    monkeypatch.setattr(S, "platform_report", lambda **_kwargs: degraded)
    code = S.main(["coverage"])
    out = capsys.readouterr().out
    assert code == 3
    assert "darwin" in out and "supported=False" in out
    assert "landlock" in out and "unavailable" in out
    assert "parity would require" in out


def test_selftest_surfaces_the_primitive_statuses() -> None:
    """``skaldir selftest`` (ENFORCE): one probe, every primitive named with its status."""
    report = cli_contract.run_selftest(layers=["ENFORCE"])
    layer = report["layers"][0]
    probes = {probe["name"]: probe for probe in layer["probes"]}
    assert "enforcement_primitives" in probes
    detail = probes["enforcement_primitives"]["detail"]
    for primitive in S.PRIMITIVES:
        assert f"{primitive}=" in detail, f"{primitive} is missing from the selftest detail"
    if S.mechanism_for(landlock_mode="enforce", bwrap=S._which("bwrap"),
                       abi=S.landlock_abi())[0] == S.MECHANISM_NONE:
        assert probes["enforcement_primitives"]["ok"] is False
        assert probes["enforcement_primitives"]["status"] == "blocked"


def test_selftest_reports_blocked_when_no_mechanism_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A host that can enforce nothing is *blocked* (not failed): runs are refused, not run."""
    monkeypatch.setattr(
        S, "mechanism_for", lambda **_kwargs: (S.MECHANISM_NONE, "no mechanism on this host")
    )
    report = cli_contract.run_selftest(layers=["ENFORCE"])
    layer = report["layers"][0]
    probe = {probe["name"]: probe for probe in layer["probes"]}["enforcement_primitives"]
    assert probe["ok"] is False
    assert probe["status"] == "blocked"
    assert "landlock=" in probe["detail"]
    assert layer["ok"] is False
    assert layer["status"] == "blocked"


def test_the_coverage_json_is_round_trippable() -> None:
    """The report is an API payload: it must survive ``json.dumps`` unchanged."""
    report = S.platform_report()
    assert json.loads(json.dumps(report))["verdict"] == report["verdict"]
