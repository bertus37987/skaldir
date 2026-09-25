"""§1.4 — MCP-Link: idempotent, merging, backed up, ``--check``. Real files,
fake host CLI (the emulation writes exactly what ``hermes mcp add/remove`` writes).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import yaml

from skaldir import mcp_link


def _write_config(path: Path, servers: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"mcp_servers": servers}, sort_keys=False), encoding="utf-8")


class FakeHostCLI:
    """Emulates the sanctioned host mechanism's effect on ``config.yaml``."""

    def __init__(self, path: Path, *, exit_code: int = 0, save: bool = True):
        self.path = path
        self.calls: list[list[str]] = []
        self.exit_code = exit_code
        self.save = save

    def __call__(self, argv, stdin, timeout, env):
        self.calls.append(list(argv))
        if argv[1] == "mcp" and argv[2] == "remove":
            servers = mcp_link.read_servers(self.path)
            servers.pop(argv[3], None)
            _write_config(self.path, servers)
            return subprocess.CompletedProcess(argv, 0, stdout="removed", stderr="")
        assert argv[1] == "mcp" and argv[2] == "add", argv
        name, command = argv[3], argv[argv.index("--command") + 1]
        args = argv[argv.index("--args") + 1:]
        if self.save and self.exit_code == 0:
            servers = mcp_link.read_servers(self.path)
            servers[name] = {"command": command, "args": args, "enabled": True}
            _write_config(self.path, servers)
        return subprocess.CompletedProcess(argv, self.exit_code, stdout="saved", stderr="")


def _other_servers(path: Path) -> dict:
    return {k: v for k, v in mcp_link.read_servers(path).items() if k != mcp_link.SERVER_NAME}


def test_first_link_adds_the_entry_and_never_touches_other_servers(tmp_path):
    config = tmp_path / "config.yaml"
    _write_config(
        config,
        {"tavily": {"command": "npx", "args": ["tavily-mcp"], "enabled": True}},
    )
    before = _other_servers(config)
    cli = FakeHostCLI(config)

    report = mcp_link.ensure_linked(hermes="/usr/bin/hermes", config_file=config, runner=cli)

    assert report["action"] == "added", report
    assert report["state"]["matches"] is True
    assert report["others_unchanged"] is True
    assert _other_servers(config) == before
    assert [call[2] for call in cli.calls] == ["add"]  # exactly one host call
    add = cli.calls[0]
    assert add[:3] == ["/usr/bin/hermes", "mcp", "add"]
    assert mcp_link.SERVER_NAME in add
    assert "--command" in add and "--args" in add


def test_second_link_is_a_no_op_without_a_single_cli_call(tmp_path):
    config = tmp_path / "config.yaml"
    cli = FakeHostCLI(config)
    first = mcp_link.ensure_linked(hermes="/usr/bin/hermes", config_file=config, runner=cli)
    assert first["action"] == "added"
    calls_after_first = len(cli.calls)

    second = mcp_link.ensure_linked(hermes="/usr/bin/hermes", config_file=config, runner=cli)

    assert second["action"] == "none"
    assert "already linked" in second["reason"]
    assert len(cli.calls) == calls_after_first  # idempotent: no write, no probe


def test_missing_host_cli_is_reported_failed_without_writing(tmp_path, monkeypatch):
    config = tmp_path / "config.yaml"
    # never let a test resolve the REAL host CLI: that would write the real config
    monkeypatch.setattr(mcp_link, "hermes_binary", lambda explicit=None: None)
    report = mcp_link.ensure_linked(hermes="", config_file=config)
    assert report["action"] == "failed"
    assert "no hermes CLI" in report["reason"]
    assert not config.exists()


def test_differing_entry_is_reported_not_overwritten_until_replace(tmp_path):
    config = tmp_path / "config.yaml"
    _write_config(
        config, {mcp_link.SERVER_NAME: {"command": "/old/skaldir", "args": ["mcp", "serve"]}}
    )
    cli = FakeHostCLI(config)

    report = mcp_link.ensure_linked(hermes="/usr/bin/hermes", config_file=config, runner=cli)
    assert report["action"] == "mismatch"
    assert cli.calls == []  # nothing written, nothing removed
    assert mcp_link.read_servers(config)[mcp_link.SERVER_NAME]["command"] == "/old/skaldir"

    replaced = mcp_link.ensure_linked(
        hermes="/usr/bin/hermes", config_file=config, runner=cli, replace=True
    )
    assert replaced["action"] == "replaced", replaced
    assert replaced["state"]["matches"] is True
    assert [call[2] for call in cli.calls] == ["remove", "add"]


def test_a_failing_host_cli_never_claims_a_link(tmp_path):
    config = tmp_path / "config.yaml"
    cli = FakeHostCLI(config, exit_code=2, save=False)
    report = mcp_link.ensure_linked(hermes="/usr/bin/hermes", config_file=config, runner=cli)
    assert report["action"] == "failed"
    assert report["state"]["matches"] is False


def test_an_entry_saved_disabled_is_not_a_working_link(tmp_path):
    """The host saves `enabled: false` when its probe finds no tools (wrong scope)."""
    config = tmp_path / "config.yaml"
    desired = mcp_link.server_entry()
    _write_config(config, {mcp_link.SERVER_NAME: {**desired, "enabled": False}})

    state = mcp_link.link_state(config_file=config)
    assert state["linked"] is True
    assert state["enabled"] is False
    assert state["matches"] is False
    assert "DISABLED" in state["reason"]

    cli = DisabledHostCLI(config)
    report = mcp_link.ensure_linked(hermes="/usr/bin/hermes", config_file=config, runner=cli)
    assert report["action"] == "mismatch"  # an existing (broken) entry is never silently rewritten
    assert "DISABLED" in report["state"]["reason"]

    relinked = mcp_link.ensure_linked(
        hermes="/usr/bin/hermes", config_file=config, runner=FakeHostCLI(config), replace=True
    )
    assert relinked["action"] == "replaced"
    assert relinked["state"]["matches"] is True


class DisabledHostCLI(FakeHostCLI):
    """Emulates the probe-failed path: the CLI saves the entry disabled."""

    def __call__(self, argv, stdin, timeout, env):
        self.calls.append(list(argv))
        assert argv[2] == "add", argv
        name, command = argv[3], argv[argv.index("--command") + 1]
        servers = mcp_link.read_servers(self.path)
        servers[name] = {
            "command": command,
            "args": argv[argv.index("--args") + 1:],
            "enabled": False,
        }
        _write_config(self.path, servers)
        return subprocess.CompletedProcess(argv, 0, stdout="saved (disabled)", stderr="")


def test_backup_is_written_before_the_first_write(tmp_path):
    config = tmp_path / "config.yaml"
    _write_config(config, {"tavily": {"command": "npx", "args": []}})
    before_text = config.read_text(encoding="utf-8")
    cli = FakeHostCLI(config)

    report = mcp_link.ensure_linked(hermes="/usr/bin/hermes", config_file=config, runner=cli)

    backup = Path(report["backup"])
    assert backup.is_file()
    assert backup.read_text(encoding="utf-8") == before_text


def test_the_link_never_creates_a_tool_filter(tmp_path):
    """A frozen name list would hide every capability built after the link."""
    config = tmp_path / "config.yaml"
    cli = FakeHostCLI(config)
    mcp_link.ensure_linked(hermes="/usr/bin/hermes", config_file=config, runner=cli)
    assert "tools" not in (mcp_link.read_servers(config)[mcp_link.SERVER_NAME] or {})
    assert mcp_link.link_state(config_file=config)["tool_filter"] is False


def test_link_state_flags_a_frozen_tool_filter(tmp_path):
    config = tmp_path / "config.yaml"
    desired = mcp_link.server_entry()
    _write_config(
        config,
        {mcp_link.SERVER_NAME: {**desired, "tools": {"include": ["money_convert"]}}},
    )
    state = mcp_link.link_state(config_file=config)
    assert state["matches"] is True
    assert state["tool_filter"] is True


def test_check_mode_is_read_only_and_its_exit_code_reports_the_state(tmp_path, capsys):
    config = tmp_path / "config.yaml"
    cli = FakeHostCLI(config)
    assert mcp_link.main(["--check", "--config", str(config)]) == 1  # not linked yet
    assert not config.exists()

    mcp_link.ensure_linked(hermes="/usr/bin/hermes", config_file=config, runner=cli)
    assert mcp_link.main(["--check", "--config", str(config)]) == 0
    out = capsys.readouterr().out
    assert "linked and matching" in out


def test_server_entry_scopes_with_the_servers_own_flags(tmp_path):
    entry = mcp_link.server_entry(project_id=str(tmp_path), runtime="/tmp/rt", include_legacy=True)
    assert entry["command"]
    assert entry["args"][:2] == ["mcp", "serve"]
    assert "--runtime" in entry["args"]
    assert "--project" in entry["args"]
    assert "--all" in entry["args"]


def test_hermes_binary_prefers_an_explicit_path():
    assert mcp_link.hermes_binary("/usr/bin/hermes") == "/usr/bin/hermes"
