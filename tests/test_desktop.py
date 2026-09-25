"""Desktop integration tests: .desktop generation, install/uninstall, status, launch."""

import os
from pathlib import Path

from skaldir import desktop


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))


class TestEntry:
    def test_desktop_entry_content(self):
        text = desktop.desktop_entry(exec_line="/opt/venv/bin/skaldir")
        assert text.startswith("[Desktop Entry]\n")
        assert "Exec=/opt/venv/bin/skaldir desktop start" in text
        assert "Icon=skaldir" in text
        assert "Terminal=false" in text
        assert "Categories=Utility;" in text
        # Invariant that keeps the app-menu icon attached: the entry's
        # StartupWMClass must be the SAME token the launcher passes as
        # --class=, i.e. desktop.WM_CLASS (not the display name — APP_NAME
        # contains a space, which broke the association before 16.09.).
        assert f"StartupWMClass={desktop.WM_CLASS}" in text

    def test_cli_path_prefers_venv_script(self):
        found = desktop.cli_path()
        assert found
        # in the project venv the console script must exist
        if Path(found).name == "skaldir" and Path(found).is_absolute():
            assert Path(found).exists()


class TestInstallRoundtrip:
    def test_install_writes_files_then_uninstall_removes(self, tmp_path, monkeypatch):
        _isolate(monkeypatch, tmp_path)
        result = desktop.install(link_cli=False)
        assert Path(result["desktop_file"]).exists()
        assert len(result["icons"]) == len(desktop.SIZES)
        for icon in result["icons"]:
            assert Path(icon).exists()
        assert "desktop start" in Path(result["desktop_file"]).read_text(encoding="utf-8")

        status = desktop.deployment_status()
        assert status["desktop_installed"] is True
        assert status["icons_installed"] is True

        removal = desktop.uninstall(remove_link=False)
        assert len(removal["removed"]) >= 4
        assert not Path(result["desktop_file"]).exists()
        assert desktop.deployment_status()["desktop_installed"] is False

    def test_status_without_install(self, tmp_path, monkeypatch):
        _isolate(monkeypatch, tmp_path)
        status = desktop.deployment_status()
        assert status["desktop_installed"] is False
        assert status["icons_installed"] is False
        assert status["plugin_dir"].endswith("plugins/godsv")

    def test_format_status_marks(self, tmp_path, monkeypatch):
        _isolate(monkeypatch, tmp_path)
        text = desktop.format_status(desktop.deployment_status())
        assert "✗" in text and "✗" in text
        desktop.install(link_cli=False)
        text2 = desktop.format_status(desktop.deployment_status())
        assert "✓ Desktop entry" in text2


class TestPanelRouting:
    def test_open_panel_spawns_deeplink(self, monkeypatch):
        calls = []
        binary = "/fake/linux-unpacked/Hermes"
        monkeypatch.setenv("HERMES_HOME", "/tmp/fake-hermes")
        monkeypatch.setattr(desktop, "hermes_app_binary", lambda: binary)
        monkeypatch.setattr(
            desktop.subprocess, "Popen", lambda argv, **kw: calls.append((argv, kw))
        )
        assert desktop.open_panel() == 0
        argv, kwargs = calls[0]
        assert argv == [binary, "--class=SkaldirAgent", "hermes://open/gods-voice"]
        env = kwargs["env"]
        assert env["HERMES_DESKTOP_USER_DATA_DIR"].endswith("godsv/desktop-userdata")
        assert env["HERMES_DESKTOP_APP_NAME"] == "Skaldir Agent"

    def test_open_panel_without_binary(self, monkeypatch):
        monkeypatch.setattr(desktop, "hermes_app_binary", lambda: None)
        assert desktop.open_panel() == 2

    def test_launch_focuses_running_app_without_navigating(self, monkeypatch):
        """`start` on a running app focuses it — it never yanks the window to the panel.

        The panel route would drop whatever session the user is in the middle of
        (owner report 17.09.: every start landed on the cockpit).
        """
        monkeypatch.setattr(
            desktop, "deployment_status", lambda: {"hermes_ok": True, "hermes": "/x"}
        )
        monkeypatch.setattr(desktop, "app_running", lambda: True)
        focused, routed = [], []
        monkeypatch.setattr(desktop, "focus_instance", lambda: (focused.append(True), 0)[1])
        monkeypatch.setattr(desktop, "open_panel", lambda: (routed.append(True), 0)[1])
        assert desktop.launch() == 0
        assert focused == [True]
        assert routed == []

    def test_focus_instance_passes_no_route(self, monkeypatch):
        """A route-less second launch only hits the app's focusWindow path."""
        monkeypatch.setattr(desktop, "hermes_app_binary", lambda: "/opt/Hermes")
        spawned = []
        monkeypatch.setattr(
            desktop.subprocess, "Popen", lambda argv, **kw: spawned.append(argv)
        )
        assert desktop.focus_instance() == 0
        assert spawned == [["/opt/Hermes", "--class=SkaldirAgent"]]

    def test_launch_cold_start_builds_then_launches_binary(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
        monkeypatch.setattr(
            desktop, "deployment_status", lambda: {"hermes_ok": True, "hermes": "/usr/bin/hermes"}
        )
        monkeypatch.setattr(desktop, "app_running", lambda: False)
        monkeypatch.setattr(desktop, "hermes_app_binary", lambda: "/opt/Hermes")
        runs = []
        spawned = []
        monkeypatch.setattr(
            desktop.subprocess, "run", lambda argv, **kw: runs.append((argv, kw))
        )
        monkeypatch.setattr(
            desktop.subprocess, "Popen", lambda argv, **kw: spawned.append((argv, kw))
        )
        assert desktop.launch() == 0
        # first the app-home plugin sync (the panel cannot mount without it), then the build
        assert runs[0][0] == ["/usr/bin/hermes", "plugins", "enable", "godsv"]
        assert runs[0][1]["env"]["HERMES_HOME"].endswith("desktop-userdata/hermes-home")
        assert runs[1][0] == ["/usr/bin/hermes", "desktop", "--build-only"]
        argv, kwargs = spawned[0]
        # no route: the app restores its remembered session, else an empty new chat
        assert argv == ["/opt/Hermes", "--class=SkaldirAgent"]
        assert not any("hermes://" in part for part in argv)
        assert kwargs["env"]["HERMES_DESKTOP_APP_NAME"] == "Skaldir Agent"
        assert kwargs["env"]["HERMES_DESKTOP_USER_DATA_DIR"].endswith("desktop-userdata")

    def test_gods_voice_env_defaults_to_the_steering_mode(self, monkeypatch):
        """The real app steers by default; shadow stays opt-in (P2)."""
        monkeypatch.delenv("GODSV_MODE", raising=False)
        assert desktop.gods_voice_env()["GODSV_MODE"] == "whisper"
        monkeypatch.setenv("GODSV_MODE", "shadow")
        assert desktop.gods_voice_env()["GODSV_MODE"] == "shadow"

    def test_gods_voice_env_keeps_ambient_env(self, monkeypatch):
        monkeypatch.setenv("GODSV_TEST_CANARY", "1")
        env = desktop.gods_voice_env()
        assert env["GODSV_TEST_CANARY"] == "1"
        assert env["HERMES_DESKTOP_APP_NAME"] == "Skaldir Agent"

    def test_wait_and_open_panel_routes_once_up(self, monkeypatch):
        monkeypatch.setattr(desktop, "app_running", lambda: True)
        calls = []
        monkeypatch.setattr(desktop, "open_panel", lambda: (calls.append(1), 0)[1])
        assert desktop._wait_and_open_panel(timeout=1, interval=0.01) is True
        assert calls == [1]


class TestLaunch:
    def test_launch_without_hermes_fails_clean(self, tmp_path, monkeypatch):
        _isolate(monkeypatch, tmp_path)
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        monkeypatch.setenv("PATH", str(tmp_path / "emptybin"))
        assert desktop.launch() == 2
        assert desktop.launch(check_only=True) == 2

    def test_hermes_found_via_local_bin_without_path(self, tmp_path, monkeypatch):
        """GNOME-session case: empty PATH, hermes only at ~/.local/bin."""
        home = tmp_path / "home"
        (home / ".local" / "bin").mkdir(parents=True)
        fake = home / ".local" / "bin" / "hermes"
        fake.write_text("#!/bin/sh\necho fake\n", encoding="utf-8")
        os.chmod(fake, 0o755)
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("PATH", str(tmp_path / "emptybin"))
        assert desktop.hermes_binary() == str(fake)
        assert desktop.deployment_status()["hermes_ok"] is True

    def test_launch_check_only_with_hermes(self, tmp_path, monkeypatch):
        _isolate(monkeypatch, tmp_path)
        fake_bin = tmp_path / "bin"
        fake_bin.mkdir()
        fake_hermes = fake_bin / "hermes"
        fake_hermes.write_text("#!/bin/sh\necho fake\n", encoding="utf-8")
        os.chmod(fake_hermes, 0o755)
        monkeypatch.setenv("PATH", str(fake_bin))
        assert desktop.launch(check_only=True) == 0


class _FakeRun:
    def __init__(self, calls, returncode=0):
        self.calls = calls
        self.returncode = returncode

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        return type("R", (), {"returncode": self.returncode, "stdout": "ok", "stderr": ""})()


class TestSkinAndPanel:
    def test_install_skin_copies_without_touching_global_theme(self, tmp_path, monkeypatch):
        """The installer must never flip the shared Hermes theme again."""
        _isolate(monkeypatch, tmp_path)
        calls = []
        monkeypatch.setattr(desktop, "hermes_binary", lambda: "/usr/bin/hermes")
        monkeypatch.setattr(desktop.subprocess, "run", _FakeRun(calls))
        result = desktop.install_skin()
        assert result["installed"] is True and result["activated"] is False
        assert desktop.skin_file().exists()
        assert desktop.skin_file().name == "gods-voice.yaml"
        assert calls == []  # no `hermes config set` — Hermes keeps its theme
        text = desktop.skin_file().read_text(encoding="utf-8")
        assert "name: gods-voice" in text and "Skaldir Agent" in text

    def test_install_skin_activate_is_explicit_opt_in(self, tmp_path, monkeypatch):
        _isolate(monkeypatch, tmp_path)
        calls = []
        monkeypatch.setattr(desktop, "hermes_binary", lambda: "/usr/bin/hermes")
        monkeypatch.setattr(desktop.subprocess, "run", _FakeRun(calls))
        result = desktop.install_skin(activate=True)
        assert result["activated"] is True
        assert calls == [["/usr/bin/hermes", "config", "set", "display.skin", "gods-voice"]]

    def test_install_skin_without_hermes_still_installs(self, tmp_path, monkeypatch):
        _isolate(monkeypatch, tmp_path)
        monkeypatch.setattr(desktop, "hermes_binary", lambda: None)
        result = desktop.install_skin()
        assert result["installed"] is True and result["activated"] is False

    def test_uninstall_skin_removes_file_and_resets_default(self, tmp_path, monkeypatch):
        _isolate(monkeypatch, tmp_path)
        calls = []
        monkeypatch.setattr(desktop, "hermes_binary", lambda: "/usr/bin/hermes")
        monkeypatch.setattr(desktop.subprocess, "run", _FakeRun(calls))
        desktop.install_skin()
        assert desktop.skin_file().exists()
        desktop.uninstall_skin()
        assert not desktop.skin_file().exists()
        assert ["/usr/bin/hermes", "config", "set", "display.skin", "default"] in calls

    def test_install_links_panel_and_uninstall_keeps_symlink_target(
        self, tmp_path, monkeypatch
    ):
        _isolate(monkeypatch, tmp_path)
        source = tmp_path / "checkout" / "integrations" / "hermes" / "godsv_plugin"
        (source / "desktop").mkdir(parents=True)
        (source / "desktop" / "plugin.js").write_text("export default {}\n", encoding="utf-8")
        monkeypatch.setattr(desktop, "panel_source", lambda: source)
        monkeypatch.setattr(desktop, "hermes_binary", lambda: None)
        result = desktop.install(link_cli=False)
        link = desktop.plugin_link()
        assert link.is_symlink() and Path(os.readlink(link)) == source
        assert result["panel_link"] == str(link)
        # idempotent
        desktop.install(link_cli=False)
        assert link.is_symlink()
        # uninstall only removes the launcher; the plugin link stays deployable
        desktop.uninstall(remove_link=False)
        assert link.is_symlink()

    def test_link_panel_never_clobbers_real_directory(self, tmp_path, monkeypatch):
        _isolate(monkeypatch, tmp_path)
        link = desktop.plugin_link()
        link.mkdir(parents=True)
        (link / "keep.txt").write_text("mine", encoding="utf-8")
        source = tmp_path / "checkout" / "integrations" / "hermes" / "godsv_plugin"
        (source / "desktop").mkdir(parents=True)
        (source / "desktop" / "plugin.js").write_text("x", encoding="utf-8")
        monkeypatch.setattr(desktop, "panel_source", lambda: source)
        desktop._link_panel()
        assert not link.is_symlink()
        assert (link / "keep.txt").exists()

    def test_status_reports_skin(self, tmp_path, monkeypatch):
        _isolate(monkeypatch, tmp_path)
        monkeypatch.setattr(desktop, "hermes_binary", lambda: None)
        monkeypatch.setattr(desktop, "app_running", lambda: False)
        assert desktop.deployment_status()["skin_installed"] is False
        calls = []
        monkeypatch.setattr(desktop.subprocess, "run", _FakeRun(calls))
        desktop.install_skin()
        assert desktop.deployment_status()["skin_installed"] is True
        out = desktop.format_status(desktop.deployment_status())
        assert "Skaldir skin" in out


def test_uninstall_plugin_symlink_is_opt_in(tmp_path, monkeypatch):
    """T9: full teardown via remove_plugin=True; default keeps it deployable."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    (tmp_path / "hermes" / "plugins").mkdir(parents=True)
    (tmp_path / "data").mkdir(parents=True)

    from skaldir import desktop

    link = desktop.plugin_link()
    link.symlink_to("/nonexistent/godsv_plugin")
    desktop.uninstall(remove_link=False)
    assert link.is_symlink(), "default must keep the plugin link (documented)"
    desktop.uninstall(remove_link=False, remove_plugin=True)
    assert not link.is_symlink(), "remove_plugin=True must remove it"
