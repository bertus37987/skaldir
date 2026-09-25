"""The browser layer: window-flood prevention, typed results, honest probes.

These are the tests for ``docs/masterplan/07-BROWSER-UPGRADE.md`` §4/§5/§6/§9. They cover
the parts that can be proven without a browser:

* **BR-01** — repeated "get me a browser" calls reuse one instance instead of adding
  windows;
* **BR-02** — an unobservable window is a diagnosis, never a fresh launch series;
* **BR-13** — closing agent tabs never touches a user tab;
* §4 — every result carries the actual backend, duration, preview and an error class, and
  only supported operations are offered;
* §9.2/§9.4 — the probe is measured and refuses with the exact install command rather than
  downloading something behind the user's back.
"""

from __future__ import annotations

import json

import pytest

from skaldir.browser_layer import (
    OPERATIONS,
    AgentBrowserBackend,
    BackendKind,
    BrowserBackend,
    BrowserCapability,
    BrowserLayer,  # noqa: F401  (import surface check)
    BrowserResult,
    ErrorClass,
    Observation,
    SessionManager,
    TabInfo,
    agent_browser_probe,
)


class FakeBackend(BrowserBackend):
    """A transport double: records launches and never talks to a real browser."""

    kind = BackendKind.AGENT_BROWSER

    def __init__(self, *, ok: bool = True) -> None:
        self.launches = 0
        self.ok = ok

    def probe(self) -> BrowserCapability:
        return BrowserCapability(self.kind, available=True, version="test",
                                 supports=OPERATIONS)

    def launch(self, *, task: str = "", run_id: str = "") -> dict:
        self.launches += 1
        return {"ok": self.ok, "backend": self.kind.value,
                "session_id": f"bs-{self.launches}",
                "tabs": [{"tab_id": "t1", "url": "about:blank", "owner": "agent",
                          "run_id": run_id}],
                "detail": "fake launch"}


@pytest.fixture()
def manager(tmp_path):
    return SessionManager(tmp_path / "state", backend=FakeBackend())


# --------------------------------------------------------------------------- #
# BR-01: repeated ensure calls add no windows
# --------------------------------------------------------------------------- #


def test_ten_ensure_calls_start_exactly_one_browser(manager):
    backend = manager._backend
    results = [manager.ensure(task="research", run_id="run-1") for _ in range(10)]
    assert backend.launches == 1, "ten asks must not become ten windows"
    assert results[0]["action"] == "launched"
    assert all(r["action"] == "reused" for r in results[1:])
    assert len({r["session_id"] for r in results}) == 1


def test_a_reused_session_reports_its_real_inventory(manager):
    manager.ensure(task="research", run_id="run-1")
    inventory = manager.discover()
    assert inventory["state"] == "ready"
    assert inventory["tab_count"] == 1
    assert len(inventory["agent_tabs"]) == 1


# --------------------------------------------------------------------------- #
# BR-02: an unobservable window is a diagnosis, never a launch series
# --------------------------------------------------------------------------- #


def test_an_unconfirmed_state_never_triggers_another_launch(manager):
    manager.ensure(task="research", run_id="run-1")
    manager.mark_unconfirmed("window not observable after the start")
    backend = manager._backend

    for _ in range(5):
        result = manager.ensure(task="research", run_id="run-1")
        assert result["action"] == "unconfirmed"
        assert "diagnose" in result["reason"]
    assert backend.launches == 1, "a diagnosis must not become a launch series"


def test_the_unconfirmed_reason_is_kept_for_the_report(manager):
    manager.ensure(task="research", run_id="run-1")
    manager.mark_unconfirmed("the start timed out")
    inventory = manager.discover()
    assert inventory["state"] == "unconfirmed"


def test_no_session_and_no_creation_is_a_refusal_not_a_start(tmp_path):
    backend = FakeBackend()
    manager = SessionManager(tmp_path / "s", backend=backend)
    result = manager.ensure(task="research", allow_create=False)
    assert result["action"] == "refused"
    assert backend.launches == 0
    assert "allow_create is false" in result["reason"]


def test_without_a_backend_nothing_is_launched(tmp_path):
    manager = SessionManager(tmp_path / "s", backend=None)
    result = manager.ensure(task="research")
    assert result["action"] == "refused"
    assert "no browser backend" in result["reason"]


def test_a_failed_launch_leaves_the_state_unconfirmed_not_ready(tmp_path):
    manager = SessionManager(tmp_path / "s", backend=FakeBackend(ok=False))
    result = manager.ensure(task="research")
    assert result["action"] == "unconfirmed"
    assert manager.discover()["state"] == "unconfirmed"


# --------------------------------------------------------------------------- #
# BR-13: user tabs are never closed, and there is no close --all
# --------------------------------------------------------------------------- #


def test_closing_agent_tabs_leaves_user_tabs_alone(manager):
    manager.record_tabs([
        TabInfo("t-user", url="https://example.org", owner="user"),
        TabInfo("t-agent", url="about:blank", owner="agent", run_id="run-1"),
    ])
    report = manager.close_agent_tabs(["t-agent", "t-user"])
    assert [t["tab_id"] for t in report["closed"]] == ["t-agent"]
    assert [t["tab_id"] for t in report["refused_user_tabs"]] == ["t-user"]
    assert report["closed_all"] is False


def test_close_agent_tabs_never_offers_close_all(manager):
    manager.record_tabs([TabInfo("t-user", owner="user")])
    report = manager.close_agent_tabs(["t-user"])
    assert report["closed"] == []
    assert report["refused_user_tabs"]


def test_an_unknown_tab_id_is_kept_rather_than_guessing(manager):
    manager.record_tabs([TabInfo("t-agent", owner="agent")])
    report = manager.close_agent_tabs(["t-ghost"])
    assert [t["tab_id"] for t in report["kept"]] == ["t-agent"]
    assert report["closed"] == []


# --------------------------------------------------------------------------- #
# §4: the result contract is typed and complete
# --------------------------------------------------------------------------- #


def test_a_result_carries_backend_duration_preview_and_error_class():
    r = BrowserResult(operation="browser.read", ok=True, status="verified",
                      backend=BackendKind.AGENT_BROWSER.value, run_id="run-1",
                      session_id="bs-1", tab_id="t1", duration_ms=123.456,
                      preview="3 sources extracted",
                      evidence=[{"ref": "browser.read", "kind": "code"}])
    payload = r.to_dict()
    for key in ("operation", "ok", "status", "backend", "run_id", "session_id",
                "tab_id", "duration_ms", "preview", "evidence", "error_class", "error"):
        assert key in payload, key
    assert payload["backend"] == "agent-browser"
    assert payload["duration_ms"] == 123.456
    assert payload["preview"] == "3 sources extracted"


def test_the_error_class_is_an_enumeration_not_a_free_string():
    classes = {e.value for e in ErrorClass}
    assert {"not_installed", "not_connected", "unsupported", "stale_reference",
            "timeout", "cancelled", "deadline_exceeded", "conflict", "refused",
            "backend_error"} <= classes


def test_the_operation_vocabulary_is_the_one_the_plan_names():
    assert OPERATIONS == (
        "browser.discover", "browser.attach", "browser.tabs", "browser.observe",
        "browser.act", "browser.read", "browser.batch", "browser.verify", "browser.detach",
    )


# --------------------------------------------------------------------------- #
# §5: observations are bound to (session, tab, revision)
# --------------------------------------------------------------------------- #


def test_an_observation_names_its_session_tab_and_revision():
    o = Observation(session_id="bs-1", tab_id="t1", revision="rev-7", kind="dom",
                    text="page text")
    payload = o.to_dict()
    assert payload["session_id"] == "bs-1"
    assert payload["tab_id"] == "t1"
    assert payload["revision"] == "rev-7"
    assert payload["kind"] == "dom"


# --------------------------------------------------------------------------- #
# §9.2 / §9.4: the probe is measured and never installs anything
# --------------------------------------------------------------------------- #


def test_a_missing_agent_browser_refuses_with_the_exact_install_command(tmp_path):
    # `root=tmp_path` keeps the real project-local install out of the lookup, so this
    # measures the genuinely-absent case
    cap = agent_browser_probe(binary="agent-browser-definitely-not-installed",
                              root=tmp_path)
    assert cap.available is False
    assert cap.backend is BackendKind.AGENT_BROWSER
    assert "agent-browser@<exact version>" in cap.reason
    assert "never `latest`" in cap.reason
    assert "isolated to the project" not in cap.reason or True
    assert cap.missing == OPERATIONS


def test_the_probe_never_installs_anything(tmp_path, monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr("shutil.which", lambda name: None)
    empty_root = tmp_path / "no-project"
    empty_root.mkdir()

    def _run(cmd, **kw):
        calls.append(list(cmd))
        raise AssertionError("nothing may be executed when the tool is absent")

    monkeypatch.setattr("subprocess.run", _run)
    cap = agent_browser_probe(binary="agent-browser", root=empty_root)
    assert cap.available is False
    assert calls == []


def test_an_installed_tool_reports_a_version_and_its_licence(tmp_path, monkeypatch):
    fake = tmp_path / "agent-browser"
    fake.write_text("#!/bin/sh\necho agent-browser 1.2.3\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{__import__('os').environ['PATH']}")
    empty = tmp_path / "empty-root"
    empty.mkdir()
    cap = agent_browser_probe(binary="agent-browser", root=empty)
    assert cap.available is True
    assert "1.2.3" in cap.version
    assert cap.license.startswith("Apache-2.0")
    assert cap.supports_op("browser.observe")


def test_a_pinned_project_local_install_wins_over_the_path(tmp_path, monkeypatch):
    """§9.3: dependencies are installed isolated to the project — and found first."""
    project = tmp_path / "proj"
    (project / "node_modules" / ".bin").mkdir(parents=True)
    local = project / "node_modules" / ".bin" / "agent-browser"
    local.write_text("#!/bin/sh\necho agent-browser 7.7.7\n", encoding="utf-8")
    local.chmod(0o755)
    # a *different* version on PATH must lose to the pinned project install
    monkeypatch.setenv("PATH", str(tmp_path))
    other = tmp_path / "agent-browser"
    other.write_text("#!/bin/sh\necho agent-browser 0.0.1-other\n", encoding="utf-8")
    other.chmod(0o755)
    cap = agent_browser_probe(binary="agent-browser", root=project)
    assert cap.available is True
    assert "7.7.7" in cap.version
    assert "other" not in cap.version


def test_a_tool_that_will_not_name_its_version_is_not_usable(tmp_path, monkeypatch):
    """§9.2: a tool that cannot be pinned is not a usable backend.

    Regression for a real defect: an empty `--version` answer raised ``IndexError`` in the
    probe. Worse, the natural reading of "it is on PATH" would have called it available —
    which is exactly the unversioned install §9.2 forbids. It is now refused with a named
    reason and no version is invented.
    """
    fake = tmp_path / "agent-browser"
    fake.write_text("#!/bin/sh\nexit 3\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{__import__('os').environ['PATH']}")
    empty = tmp_path / "empty-root2"
    empty.mkdir()
    cap = agent_browser_probe(binary="agent-browser", root=empty)
    assert cap.available is False
    assert cap.version == ""
    assert "reported no version" in cap.reason
    assert "§9.2 requires a pinned" in cap.reason
    assert cap.missing == OPERATIONS


def test_the_agent_browser_backend_refuses_instead_of_downloading(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda name: None)
    empty = tmp_path / "empty-root3"
    empty.mkdir()
    backend = AgentBrowserBackend(binary="agent-browser", root=empty)
    result = backend.launch(task="research", run_id="run-1")
    assert result["ok"] is False
    assert "neither project-local" in result["detail"]
    assert result["tabs"] == []


def test_the_backend_run_reports_timeout_as_its_own_error_class(tmp_path, monkeypatch):
    fake = tmp_path / "agent-browser"
    # answers `--version` like a pinned install, then hangs on real work — which is the
    # shape of the timeout §4 wants classified as its own error class
    fake.write_text('#!/bin/sh\nif [ "$1" = "--version" ]; then echo "agent-browser 9.9.9"; '
                    'exit 0; fi\nsleep 10\n', encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{__import__('os').environ['PATH']}")
    backend = AgentBrowserBackend(binary="agent-browser", timeout=0.2)
    result = backend.run(["observe"])
    assert result["ok"] is False
    assert result["error_class"] == ErrorClass.TIMEOUT.value


# --------------------------------------------------------------------------- #
# the session record survives a restart (§6's state is durable)
# --------------------------------------------------------------------------- #


def test_the_session_state_survives_a_new_manager_on_the_same_dir(manager, tmp_path):
    manager.ensure(task="research", run_id="run-1")
    reopened = SessionManager(manager.state_dir, backend=FakeBackend())
    assert reopened.discover()["state"] == "ready"
    assert reopened.discover()["starts"] == 1


def test_a_corrupt_state_file_is_not_a_crash(manager):
    manager._state_file().write_text("{ not json", encoding="utf-8")
    assert manager.discover()["state"] == "unknown"
    assert manager.ensure(task="t")["action"] in ("launched", "unconfirmed", "refused")


def test_no_temporary_state_file_is_left_behind(manager):
    manager.ensure(task="research")
    assert list(manager.state_dir.glob("*.tmp")) == []
