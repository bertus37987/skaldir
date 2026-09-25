"""Wave 1 — a standard install must leave a plugin the engine actually loads.

Two measured defects (live 17.09., audit F-W01/F-W09):

* the panel symlink was deployed while the host config carried ``plugins.enabled: []`` and
  ``plugins.disabled: [godsv, ...]``, so the engine skipped the plugin and every hook-driven
  feature was inert (panel routes 404, zero events, no goal contract) — the install looked
  successful and produced nothing;
* the same config carried a stale ``godsv-tools`` MCP entry whose ``--runtime``/``--project``
  pointed into ``/tmp/pytest-of-willy/pytest-120/...`` and whose command file no longer existed.

Both fixes must go through the host's own CLI (``hermes plugins enable``, ``hermes mcp remove``).
These tests drive a fake host CLI that writes exactly what the real one writes, and they assert the
*verified* outcome read back from the config — never just a return code.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from skaldir import desktop, mcp_link


class FakeHermes:
    """Emulates the host CLI's effect on ``config.yaml`` for the two commands we drive."""

    def __init__(self, config: Path):
        self.config = config
        self.calls: list[list[str]] = []

    def _load(self) -> dict:
        try:
            return yaml.safe_load(self.config.read_text(encoding="utf-8")) or {}
        except FileNotFoundError:
            return {}

    def _save(self, data: dict) -> None:
        self.config.parent.mkdir(parents=True, exist_ok=True)
        self.config.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    def __call__(self, argv, stdin, timeout, env) -> subprocess.CompletedProcess:  # noqa: ARG002
        self.calls.append(list(argv))
        data = self._load()
        if argv[1] == "plugins" and argv[2] == "enable":
            plugins = data.setdefault("plugins", {})
            enabled = [n for n in (plugins.get("enabled") or []) if n != argv[3]]
            enabled.append(argv[3])
            plugins["enabled"] = enabled
            plugins["disabled"] = [n for n in (plugins.get("disabled") or []) if n != argv[3]]
            self._save(data)
            return subprocess.CompletedProcess(argv, 0, stdout=f"enabled {argv[3]}", stderr="")
        if argv[1] == "mcp" and argv[2] == "remove":
            servers = data.get("mcp_servers") or {}
            servers.pop(argv[3], None)
            data["mcp_servers"] = servers
            self._save(data)
            return subprocess.CompletedProcess(argv, 0, stdout="removed", stderr="")
        raise AssertionError(f"unexpected host call: {argv}")


class SilentHermes(FakeHermes):
    """A host CLI that reports success but writes nothing (the failure mode we must not accept)."""

    def __call__(self, argv, stdin, timeout, env):  # noqa: ARG002
        self.calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")


def _config(tmp_path: Path, plugins: dict, servers: dict | None = None) -> Path:
    path = tmp_path / "config.yaml"
    payload: dict = {"plugins": plugins}
    if servers is not None:
        payload["mcp_servers"] = servers
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


# ── installation enables the deployed plugin ────────────────────────────────────────────────


def test_a_deployed_but_disabled_plugin_is_enabled_through_the_host_cli(tmp_path):
    config = _config(tmp_path, {"enabled": [], "disabled": ["godsv", "kanban"]})
    fake = FakeHermes(config)
    report = desktop.ensure_plugin_enabled(
        hermes="/usr/bin/hermes", runner=fake, config_file=config
    )
    assert report["action"] == "enabled", report
    assert fake.calls == [["/usr/bin/hermes", "plugins", "enable", "godsv"]]
    state = desktop.plugin_enabled_state(config_file=config)
    assert state["enabled"] is True, state
    # the enable must not disturb unrelated plugins
    data = yaml.safe_load(config.read_text(encoding="utf-8"))
    assert data["plugins"]["disabled"] == ["kanban"]


def test_an_already_enabled_plugin_is_not_touched(tmp_path):
    config = _config(tmp_path, {"enabled": ["godsv"], "disabled": []})
    fake = FakeHermes(config)
    report = desktop.ensure_plugin_enabled(
        hermes="/usr/bin/hermes", runner=fake, config_file=config
    )
    assert report["action"] == "already", report
    assert fake.calls == []


def test_missing_host_cli_is_reported_honestly(tmp_path, monkeypatch):
    config = _config(tmp_path, {"enabled": [], "disabled": []})
    monkeypatch.setattr(desktop, "hermes_binary", lambda: None)
    report = desktop.ensure_plugin_enabled(config_file=config)
    assert report["action"] == "failed", report
    assert "hermes CLI" in report["reason"]
    assert desktop.plugin_enabled_state(config_file=config)["enabled"] is False


def test_a_host_cli_that_writes_nothing_is_not_success(tmp_path):
    """Exit code 0 without the config actually changing is a failure, not a pass."""
    config = _config(tmp_path, {"enabled": [], "disabled": ["godsv"]})
    report = desktop.ensure_plugin_enabled(
        hermes="/usr/bin/hermes", runner=SilentHermes(config), config_file=config
    )
    assert report["action"] == "failed", report
    assert "still disabled" in report["reason"] or "left the plugin disabled" in report["reason"]


def test_install_surfaces_the_verified_plugin_state(tmp_path, monkeypatch):
    """The production caller: ``install()`` must report what it verified, not what it hoped."""
    monkeypatch.setattr(desktop, "applications_dir", lambda: tmp_path / "applications")
    monkeypatch.setattr(
        desktop, "desktop_file", lambda: tmp_path / "applications" / "skaldir.desktop"
    )
    monkeypatch.setattr(desktop, "render_icons", lambda: [])
    monkeypatch.setattr(desktop, "_link_cli", lambda: None)
    monkeypatch.setattr(desktop, "install_skin", lambda: "")
    monkeypatch.setattr(desktop, "_link_panel", lambda: str(tmp_path / "plugins" / "godsv"))
    monkeypatch.setattr(
        desktop,
        "ensure_plugin_enabled",
        lambda: {"action": "enabled", "after": {"enabled": True, "plugin": "godsv"}},
    )
    report = desktop.install(link_cli=False)
    assert report["plugin_action"] == "enabled"
    assert report["plugin_state"]["enabled"] is True
    assert report["panel_link"]


# ── stale MCP entries leave the install ─────────────────────────────────────────────────────


def _stale_servers() -> dict:
    return {
        "godsv-tools": {
            "command": "/home/willy/.hermes/hermes-agent/venv/bin/skaldir",
            "args": [
                "mcp",
                "serve",
                "--runtime",
                "/tmp/pytest-of-willy/pytest-120/test_checker_crash_is_recorded0/godsv",
            ],
            "enabled": False,
        },
        "other-server": {"command": "/usr/bin/true", "args": []},
    }


def test_a_stale_entry_is_removed_through_the_host_cli(tmp_path):
    config = _config(tmp_path, {"enabled": ["godsv"]}, servers=_stale_servers())
    fake = FakeHermes(config)
    report = mcp_link.unlink(hermes="/usr/bin/hermes", runner=fake, config_file=config)
    assert report["action"] == "removed", report
    assert fake.calls == [["/usr/bin/hermes", "mcp", "remove", "godsv-tools"]]
    assert report["others_unchanged"] is True
    data = yaml.safe_load(config.read_text(encoding="utf-8"))
    assert "godsv-tools" not in (data.get("mcp_servers") or {})
    assert "other-server" in data["mcp_servers"]
    assert Path(report["backup"]).is_file()


def test_an_absent_entry_is_not_an_error(tmp_path):
    config = _config(tmp_path, {"enabled": ["godsv"]}, servers={"other-server": {"command": "x"}})
    fake = FakeHermes(config)
    report = mcp_link.unlink(hermes="/usr/bin/hermes", runner=fake, config_file=config)
    assert report["action"] == "none", report
    assert fake.calls == []


def test_unlink_without_host_cli_says_so(tmp_path, monkeypatch):
    config = _config(tmp_path, {"enabled": ["godsv"]}, servers=_stale_servers())
    monkeypatch.setattr(mcp_link, "hermes_binary", lambda *a, **k: None)
    report = mcp_link.unlink(config_file=config)
    assert report["action"] == "failed", report
    assert "hermes CLI" in report["reason"]


def test_an_entry_that_survives_removal_is_a_failure(tmp_path):
    config = _config(tmp_path, {"enabled": ["godsv"]}, servers=_stale_servers())
    report = mcp_link.unlink(
        hermes="/usr/bin/hermes", runner=SilentHermes(config), config_file=config
    )
    assert report["action"] == "failed", report
    assert "survived" in report["reason"]


@pytest.mark.parametrize("name", ["godsv-tools"])
def test_the_cli_flag_removes_and_reports(tmp_path, name, monkeypatch, capsys):
    config = _config(tmp_path, {"enabled": ["godsv"]}, servers=_stale_servers())
    fake = FakeHermes(config)
    monkeypatch.setattr(mcp_link, "hermes_binary", lambda explicit=None: "/usr/bin/hermes")
    monkeypatch.setattr(mcp_link, "_run", fake)
    rc = mcp_link.main(["--unlink", "--config", str(config)])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "removed" in out
    assert name not in (yaml.safe_load(config.read_text(encoding="utf-8")).get("mcp_servers") or {})
