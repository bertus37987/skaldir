"""The app's backend home carries the panel plugin — deployment contract.

Measured 17.09. on the shipped build: the packaged app spawns `hermes serve` with
`HERMES_HOME=<userData>/hermes-home`, so a plugin deployed only into `~/.hermes` is
invisible to the panel (routes 404 `{"detail":"Plugin not found"}` while the chat
works). `sync_app_backend_plugin()` is the product-side fix: link + enable inside
that home, idempotent, safe on every launch.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from skaldir import desktop  # noqa: E402


def test_app_backend_home_is_userdata_hermes_home(tmp_path: Path, monkeypatch) -> None:
    userdata = tmp_path / "userdata"
    monkeypatch.setattr(desktop, "skaldir_user_data", lambda: userdata)

    assert desktop.app_backend_home() == userdata / "hermes-home"


def test_sync_app_backend_plugin_links_and_enables_in_the_app_home(
    tmp_path: Path, monkeypatch
) -> None:
    userdata = tmp_path / "userdata"
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.yaml").write_text("name: godsv\n", encoding="utf-8")
    monkeypatch.setattr(desktop, "skaldir_user_data", lambda: userdata)
    monkeypatch.setattr(desktop, "panel_source", lambda: plugin_dir)

    calls: list[tuple[list[str], str | None, dict | None]] = []

    def runner(argv, stdin, timeout, env):
        calls.append((argv, env.get("HERMES_HOME") if env else None, env))
        home = Path(env["HERMES_HOME"])
        home.mkdir(parents=True, exist_ok=True)
        (home / "config.yaml").write_text(
            "plugins:\n  enabled:\n  - godsv\n  disabled: []\n", encoding="utf-8"
        )

        class _Proc:
            returncode = 0
            stdout = "✓ Plugin godsv enabled.\n"
            stderr = ""

        return _Proc()

    result = desktop.sync_app_backend_plugin(hermes="/usr/bin/hermes", runner=runner)

    home = userdata / "hermes-home"
    assert result["home"] == str(home)
    # the link is the product plugin, inside the APP's home — not ~/.hermes
    assert result["link"] == str(home / "plugins" / "godsv")
    assert (home / "plugins" / "godsv").is_symlink()
    assert Path(desktop.os.readlink(home / "plugins" / "godsv")) == plugin_dir
    # the host CLI ran scoped to that home, with the real argv shape
    assert calls and calls[0][0] == ["/usr/bin/hermes", "plugins", "enable", "godsv"]
    assert calls[0][1] == str(home)
    # and the result is verified by re-reading the config, not by the exit code
    assert result["state"]["action"] == "enabled"
    assert result["state"]["after"]["enabled"] is True


def test_sync_app_backend_plugin_is_idempotent(tmp_path: Path, monkeypatch) -> None:
    userdata = tmp_path / "userdata"
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    monkeypatch.setattr(desktop, "skaldir_user_data", lambda: userdata)
    monkeypatch.setattr(desktop, "panel_source", lambda: plugin_dir)

    runs: list[list[str]] = []

    def runner(argv, stdin, timeout, env):
        runs.append(argv)
        home = Path(env["HERMES_HOME"])
        home.mkdir(parents=True, exist_ok=True)
        (home / "config.yaml").write_text("plugins:\n  enabled:\n  - godsv\n", encoding="utf-8")

        class _Proc:
            returncode = 0
            stdout = ""
            stderr = ""

        return _Proc()

    first = desktop.sync_app_backend_plugin(hermes="/usr/bin/hermes", runner=runner)
    second = desktop.sync_app_backend_plugin(hermes="/usr/bin/hermes", runner=runner)

    assert first["state"]["action"] == "enabled"
    # already enabled ⇒ the CLI is not run a second time, and no second link appears
    assert second["state"]["action"] == "already"
    assert len(runs) == 1
