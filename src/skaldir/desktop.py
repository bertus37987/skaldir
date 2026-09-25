"""Desktop integration for Skaldir Agent (Fedora/GNOME, user-local).

The release desktop app is the Hermes desktop surface with the Skaldir
plugin loaded. This module turns that into a first-class app: a user-local
``.desktop`` entry (App grid + launcher), hicolor icons scaled from the
shipped asset, a status/deployment check, and a detached launch. Everything
is XDG-correct, needs no sudo and is testable without a display.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

APP_ID = "gods-voice"
APP_NAME = "Skaldir Agent"
# The plugin directory name under $HERMES_HOME/plugins/ and the name the host uses in
# `plugins.enabled`/`plugins.disabled` — one string, so install checks cannot drift.
PLUGIN_NAME = "godsv"
#: Label of the app-menu / launcher entry. Deliberately shorter than APP_NAME:
#: the menu shows a name, the product identity keeps the full brand.
MENU_NAME = "Skaldir"
WM_CLASS = "SkaldirAgent"
ICON_NAME = "skaldir"
#: Pre-rebrand icon name — its hicolor files are removed on install/render.
LEGACY_ICON_NAME = "gods-voice"
#: .desktop file stem of the launcher entry. The pre-rebrand entry
#: (`gods-voice.desktop`) is removed on install, otherwise the app grid would
#: list the product twice (old entry + new one).
ENTRY_STEM = "skaldir"
LEGACY_ENTRY_STEM = "gods-voice"
SIZES = (64, 128, 256)
COMMENT = "Skaldir Agent — a chat-first agent harness (Hermes Engine)"


def assets_dir() -> Path:
    return Path(__file__).parent / "assets"


def data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))


def applications_dir() -> Path:
    return data_home() / "applications"


def icons_dir() -> Path:
    return data_home() / "icons" / "hicolor"


def desktop_file() -> Path:
    return applications_dir() / f"{ENTRY_STEM}.desktop"


def icon_file(size: int) -> Path:
    return icons_dir() / f"{size}x{size}" / "apps" / f"{ICON_NAME}.png"


def pixmap_file() -> Path:
    """Fallback copy that older menus look up before the hicolor theme."""
    return Path.home() / ".local" / "share" / "pixmaps" / f"{ICON_NAME}.png"


def bin_dir() -> Path:
    return Path.home() / ".local" / "bin"


def hermes_home() -> Path:
    from .env import host_getenv  # local: keeps this module's import graph flat

    return Path(host_getenv("HERMES_HOME") or (Path.home() / ".hermes"))


def plugin_link() -> Path:
    return hermes_home() / "plugins" / "godsv"


def cli_path() -> str:
    """Absolute path of the ``skaldir`` entry point (venv-aware, PATH fallback)."""
    candidate = Path(sys.executable).parent / "skaldir"
    if candidate.exists():
        return str(candidate)
    return shutil.which("skaldir") or f"{sys.executable} -m skaldir"


def hermes_binary() -> str | None:
    """Locate the hermes CLI even when launched from the app grid.

    GNOME session PATH does not include ~/.local/bin, so `which` alone is not
    enough — fall back to the standard install locations.
    """
    found = shutil.which("hermes")
    if found:
        return found
    candidates = (
        Path.home() / ".local" / "bin" / "hermes",
        Path.home() / "bin" / "hermes",
        Path.home() / ".hermes" / "hermes-agent" / "venv" / "bin" / "hermes",
        Path("/usr/local/bin/hermes"),
        Path("/usr/bin/hermes"),
    )
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None


PANEL_ROUTE = "gods-voice"


def skaldir_user_data() -> Path:
    """Dedicated Electron user-data dir → own instance, own single-instance lock.

    This is what makes Skaldir Agent and Hermes coexist: each window group has its
    own lock and state, so neither can ever steal focus/route from the other.
    """
    return hermes_home() / "godsv" / "desktop-userdata"


def gods_voice_env() -> dict:
    """Environment for the Skaldir Agent instance (official app overrides)."""
    env = dict(os.environ)
    env["HERMES_DESKTOP_USER_DATA_DIR"] = str(skaldir_user_data())
    env["HERMES_DESKTOP_APP_NAME"] = APP_NAME
    # The real Skaldir Agent app steers by default: pause, research,
    # redirect and the completion gate are ACTIVE (mode "whisper") unless
    # the owner opts down to shadow (observe only) or off.
    #
    # Both spellings are exported: the Hermes plugin that reads this steers through
    # the pre-rename `GODSV_MODE` (it is deployed into the host, another wave owns it),
    # so leaving it out would silently stop the plugin from steering. Reading either
    # name and writing both keeps the two from drifting apart.
    mode = env.get("SKALDIR_MODE") or env.get("GODSV_MODE") or "whisper"
    env["SKALDIR_MODE"] = mode
    env["GODSV_MODE"] = mode
    env.setdefault("HERMES_DESKTOP_PASSWORD_STORE", "gnome-libsecret")
    env.setdefault("HERMES_DESKTOP_CWD", os.getcwd())
    node_bin = Path.home() / ".hermes" / "node" / "bin"
    if node_bin.exists():
        env["PATH"] = f"{node_bin}:{env.get('PATH', '')}"
    return env


def pid_environ_text(pid: int) -> str:
    """``/proc/<pid>/environ`` as text (empty when unreadable/foreign)."""
    try:
        raw = Path(f"/proc/{pid}/environ").read_bytes()
    except OSError:
        return ""
    return raw.decode("utf-8", "replace")


def environ_value(env_text: str, name: str) -> str:
    """Value of one environment variable inside a NUL-separated environ blob."""
    for chunk in str(env_text).split("\0"):
        if chunk.startswith(f"{name}="):
            return chunk.split("=", 1)[1].strip()
    return ""


def normalize_user_data_path(value: str) -> str:
    """``/home/x/.hermes/godsv/`` -> ``/home/x/.hermes/godsv`` (slashes, quotes)."""
    text = str(value or "").strip().strip("'\"")
    while "//" in text:
        text = text.replace("//", "/")
    return text.rstrip("/")


def user_data_marker_matches(env_text: str, marker: str) -> bool:
    """Does this environ blob declare a user-data dir that is *this* instance's?

    The live instance has been observed with **two** real spellings: the marker
    root (``…/.hermes/godsv/``, trailing slash) and the Electron dir
    (``…/.hermes/godsv/desktop-userdata``). A raw substring comparison against
    one exact spelling misses the other and made ``--check`` report no instance
    while Skaldir Agent was running (audit-4 R1). So compare *paths*: the declared
    value and the marker match when they are equal after normalisation, or when
    the longer one is the other's direct child named ``desktop-userdata``.
    """
    declared = normalize_user_data_path(environ_value(env_text, "HERMES_DESKTOP_USER_DATA_DIR"))
    wanted = normalize_user_data_path(marker)
    if not declared or not wanted:
        return False
    if declared == wanted:
        return True
    for long_path, short_path in ((declared, wanted), (wanted, declared)):
        if not long_path.startswith(short_path + "/"):
            continue
        tail = long_path[len(short_path) + 1 :]
        if tail == "desktop-userdata" or tail.startswith("desktop-userdata-"):
            return True
    return False


def _pid_has_marker(pid: int, marker: str, *, read_environ=None) -> bool:
    """True when PID's environ carries the GV user-data marker.

    Path-aware first (both real spellings), then the historical raw-containment
    fallback so callers that pass an unusual marker keep working. ``read_environ``
    is resolved at call time, so the probe stays substitutable in tests.
    """
    reader = read_environ or pid_environ_text
    env_text = reader(pid)
    if not env_text:
        return False
    if user_data_marker_matches(env_text, marker):
        return True
    return marker.encode("utf-8") in env_text.encode("utf-8")


_APP_BINARY_NAMES = ("/Hermes", "/gods-voice", "/Skaldir Agent", "/skaldir")


def _running_app_pids() -> list[tuple[int, str]]:
    """(pid, binary path) of every running Hermes / Skaldir Agent desktop process."""
    found: dict[int, str] = {}
    for pattern in ("linux-unpacked/Hermes", "linux-unpacked/gods-voice"):
        try:
            proc = subprocess.run(  # nosec B603 — fixed argv, read-only process listing
                ["pgrep", "-af", pattern],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        for line in proc.stdout.splitlines():
            parts = line.split(None, 1)
            if len(parts) != 2:
                continue
            pid_text, cmdline = parts
            path = cmdline.split(" --")[0].strip()
            if not any(path.endswith(name) for name in _APP_BINARY_NAMES):
                continue
            if not Path(path).exists():
                continue
            try:
                found[int(pid_text)] = path
            except ValueError:
                continue
    return sorted(found.items())


def _running_app_binary(skaldir_only: bool = False) -> str | None:
    marker = str(skaldir_user_data())
    for pid, path in _running_app_pids():
        if skaldir_only and not _pid_has_marker(pid, marker):
            continue
        return path
    return None


def app_running() -> bool:
    """True when the *Skaldir Agent* instance is up (marker-filtered)."""
    return _running_app_binary(skaldir_only=True) is not None


def gods_voice_app_binary() -> str | None:
    """The bundled Skaldir Agent app build (godsv/app/linux-unpacked), if built."""
    base = hermes_home() / "godsv" / "app" / "linux-unpacked"
    if not base.is_dir():
        return None
    preferred = base / "gods-voice"
    if preferred.is_file() and os.access(preferred, os.X_OK):
        return str(preferred)
    for candidate in sorted(base.iterdir()):
        if candidate.is_file() and os.access(candidate, os.X_OK) and candidate.suffix != ".so":
            return str(candidate)
    return None


def hermes_app_binary() -> str | None:
    """Path of the app binary to launch — the GV build first, then stock."""
    running = _running_app_binary(skaldir_only=True)
    if running:
        return running
    bundled = gods_voice_app_binary()
    if bundled:
        return bundled
    roots = []
    from .env import host_getenv

    root_env = host_getenv("HERMES_DESKTOP_HERMES_ROOT")
    if root_env:
        roots.append(Path(root_env))
    roots.append(Path.home() / ".hermes" / "hermes-agent")
    for root in roots:
        candidate = root / "apps" / "desktop" / "release" / "linux-unpacked" / "Hermes"
        if candidate.exists():
            return str(candidate)
    return None


def open_panel() -> int:
    """Focus the Skaldir Agent instance and land it on its panel.

    Spawns the app binary with the Skaldir Agent env; the app's own
    single-instance routing (scoped to our userData dir) delivers the deep
    link into the Skaldir Agent window — a plain Hermes window can never be
    touched. Returns 0 ok, 2 no app binary, 3 spawn failed.
    """
    binary = hermes_app_binary()
    if binary is None:
        return 2
    try:
        subprocess.Popen(  # nosec B603 — fixed argv, no shell, user command
            [binary, f"--class={WM_CLASS}", f"hermes://open/{PANEL_ROUTE}"],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=gods_voice_env(),
        )
    except OSError:
        return 3
    return 0


def focus_instance() -> int:
    """Bring a running Skaldir Agent window forward **without navigating**.

    A route-less second launch hits the app's single-instance handler, which
    calls ``focusWindow`` and nothing else (main.ts `second-instance`) — the
    session the user has open stays open. `open_panel()` is the deliberate
    exception that *does* navigate to the panel.

    Returns 0 ok, 2 no app binary, 3 spawn failed.
    """
    binary = hermes_app_binary()
    if binary is None:
        return 2
    try:
        subprocess.Popen(  # nosec B603 — fixed argv, no shell, user command
            [binary, f"--class={WM_CLASS}"],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=gods_voice_env(),
        )
    except OSError:
        return 3
    return 0


def _wait_and_open_panel(timeout: float = 90.0, interval: float = 2.0) -> bool:
    """Wait until the Skaldir Agent instance is up, then route it to the panel."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if app_running():
            return open_panel() == 0
        time.sleep(interval)
    return False


def skin_file() -> Path:
    return hermes_home() / "skins" / f"{APP_ID}.yaml"


def install_skin(*, activate: bool = False) -> dict:
    """Drop the Skaldir Agent skin into the Hermes home (CLI/TUI offer, opt-in).

    The GV *app* carries its own colours (built-in theme) — the skin is a
    separate offer for CLI/TUI users and is NEVER activated globally by an
    installer run, because Hermes (the engine) shares the same config and must
    keep its own theme. Activation is explicit: ``hermes config set
    display.skin gods-voice``.
    """
    source = assets_dir() / "skin-skaldir.yaml"
    result: dict = {"skin_file": str(skin_file()), "installed": False, "activated": False}
    if not source.exists():
        return result
    skin_file().parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, skin_file())
    result["installed"] = True
    hermes = hermes_binary()
    if activate and hermes:
        try:
            proc = subprocess.run(  # nosec B603 — fixed argv, no shell, user command
                [hermes, "config", "set", "display.skin", APP_ID],
                capture_output=True,
                text=True,
                timeout=30,
            )
            result["activated"] = proc.returncode == 0
            result["activate_output"] = (proc.stdout or proc.stderr or "").strip()[:200]
        except (OSError, subprocess.SubprocessError):
            result["activated"] = False
    return result


def uninstall_skin() -> dict:
    """Remove our skin file and fall back to the default skin (best effort)."""
    removed: list[str] = []
    try:
        skin_file().unlink()
        removed.append(str(skin_file()))
    except FileNotFoundError:
        pass
    hermes = hermes_binary()
    if hermes:
        try:
            subprocess.run(  # nosec B603 — fixed argv, no shell, user command
                [hermes, "config", "set", "display.skin", "default"],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            pass
    return {"removed": removed}


def desktop_entry(*, exec_line: str) -> str:
    """Deterministic .desktop file content (unit-testable)."""
    return "\n".join(
        [
            "[Desktop Entry]",
            "Type=Application",
            f"Name={MENU_NAME}",
            "GenericName=Agent Harness",
            f"Comment={COMMENT}",
            f"Exec={exec_line} desktop start",
            f"Icon={ICON_NAME}",
            "Terminal=false",
            "Categories=Utility;",
            "Keywords=agent;voice;hermes;steering;",
            "StartupNotify=true",
            f"StartupWMClass={WM_CLASS}",
        ]
    ) + "\n"


def render_icons() -> list[Path]:
    """Scale ``assets/icon.png`` into the hicolor theme (ImageMagick, fallback copy).

    Also removes the LEGACY icon files: the entry now points at
    ``ICON_NAME`` (``skaldir``) after the rebrand, and leaving
    ``gods-voice.png`` behind would keep the old logo alive in the icon theme
    (and in any menu that still caches it).
    """
    source = assets_dir() / "icon.png"
    written: list[Path] = []
    if not source.exists():
        return written
    convert = shutil.which("convert")
    for size in SIZES:
        target = icon_file(size)
        target.parent.mkdir(parents=True, exist_ok=True)
        ok = False
        if convert:
            try:
                subprocess.run(  # nosec B603 — fixed argv, no shell, local tool
                    [convert, str(source), "-resize", f"{size}x{size}", str(target)],
                    check=True,
                    capture_output=True,
                    timeout=30,
                )
                ok = True
            except (OSError, subprocess.SubprocessError):
                ok = False
        if not ok:
            shutil.copyfile(source, target)
        written.append(target)
        # The window/taskbar lookup is NOT the entry's Icon= alone: Chromium sets
        # WM_CLASS and the shell resolves the window icon via the class-derived
        # name, so the same mark is installed under that name too (lowercased,
        # no spaces — WM_CLASS is not space delimited).
        for alias in (WM_CLASS.lower(), "skaldir"):
            if alias == ICON_NAME:
                continue
            alias_path = target.with_name(f"{alias}.png")
            if not alias_path.exists() or alias_path.read_bytes() != target.read_bytes():
                shutil.copyfile(target, alias_path)
            # Deliberately NOT appended to `written`: the reported list stays the
            # canonical icon set (one per SIZES entry); aliases are lookup details.
        # Older menus look in ~/.local/share/pixmaps before the hicolor theme.
        pixmap = pixmap_file()
        pixmap.parent.mkdir(parents=True, exist_ok=True)
        if not pixmap.exists() or pixmap.read_bytes() != target.read_bytes():
            shutil.copyfile(target, pixmap)
        legacy = target.with_name(f"{LEGACY_ICON_NAME}.png")
        if legacy.exists() and legacy != target:
            legacy.unlink()
    return written


def _link_cli() -> str | None:
    """Symlink the entry point into ``~/.local/bin`` (never touches foreign files)."""
    source = cli_path()
    if not source or " " in source:  # module-fallback form: skip
        return None
    link = bin_dir() / "skaldir"
    try:
        bin_dir().mkdir(parents=True, exist_ok=True)
        if link.is_symlink():
            if os.readlink(link) == source:
                return str(link)
            link.unlink()
        elif link.exists():
            return None
        link.symlink_to(source)
        return str(link)
    except OSError:
        return None


def panel_source() -> Path:
    """The godsv_plugin tree inside this checkout (repo → integrations/hermes)."""
    root = Path(__file__).resolve().parent.parent.parent
    return root / "integrations" / "hermes" / "godsv_plugin"


def _link_panel() -> str | None:
    """Deploy the desktop plugin as $HERMES_HOME/plugins/godsv (symlink)."""
    source = panel_source()
    if not (source / "desktop" / "plugin.js").exists():
        return None
    link = plugin_link()
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        if link.is_symlink():
            if os.readlink(link) == str(source):
                return str(link)
            link.unlink()
        elif link.exists():
            return str(link)  # never clobber a real directory someone else manages
        link.symlink_to(source)
    except OSError:
        return None
    return str(link)


def plugin_enabled_state(*, config_file: Path | None = None) -> dict:
    """Read the host config: is the deployed plugin actually going to load?"""
    path = config_file or (hermes_home() / "config.yaml")
    state: dict = {"config": str(path), "plugin": PLUGIN_NAME, "enabled": None, "reason": ""}
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        state["reason"] = "no config.yaml in this HERMES_HOME"
        return state
    except Exception as exc:  # noqa: BLE001 — an unreadable config is unknown, not "enabled"
        state["reason"] = f"{type(exc).__name__}: {exc}"
        return state
    plugins = data.get("plugins") if isinstance(data, dict) else None
    enabled = list((plugins or {}).get("enabled") or []) if isinstance(plugins, dict) else []
    disabled = list((plugins or {}).get("disabled") or []) if isinstance(plugins, dict) else []
    if PLUGIN_NAME in disabled:
        state.update(enabled=False, reason="listed in plugins.disabled — the engine skips it")
    elif PLUGIN_NAME in enabled:
        state.update(enabled=True, reason="listed in plugins.enabled")
    else:
        state.update(
            enabled=False,
            reason=(
                "not listed in plugins.enabled — bundled plugins ship off and user plugins need "
                "explicit opt-in, so the deployed panel would stay dark"
            ),
        )
    return state


def ensure_plugin_enabled(
    *,
    hermes: str | None = None,
    runner=None,
    timeout: float = 60.0,
    config_file: Path | None = None,
    env: dict | None = None,
) -> dict:
    """A deployed plugin that stays disabled is dead weight — enable it through the host CLI.

    Measured in the shipped install (17.09.): the panel symlink existed, while the host config
    carried ``plugins.enabled: []`` and ``plugins.disabled: [godsv, ...]``. The engine skipped the
    plugin, so every hook-driven feature was inert (panel routes 404, zero events, no goal
    contract) — installing looked successful and produced nothing. This runs the host's own
    ``hermes plugins enable <name>``; ``config.yaml`` is never edited by hand, and the result is
    verified by re-reading the config instead of trusting an exit code.
    """
    import shutil as _shutil
    import subprocess as _subprocess

    before = plugin_enabled_state(config_file=config_file)
    result: dict = {"plugin": PLUGIN_NAME, "action": "none", "before": before, "after": before}
    if before["enabled"] is True:
        result.update(action="already", reason=before["reason"])
        return result
    binary = hermes or hermes_binary()
    if not binary:
        result.update(
            action="failed",
            reason="no hermes CLI found — the plugin stays disabled until it is enabled by hand",
        )
        return result
    run = runner or (
        lambda argv, stdin, tmo, env: _subprocess.run(  # noqa: S603 — fixed argv, no shell
            argv, input=stdin, capture_output=True, text=True, timeout=tmo, env=env
        )
    )
    try:
        proc = run([binary, "plugins", "enable", PLUGIN_NAME], "y\n", timeout, env)
    except (OSError, _subprocess.SubprocessError) as exc:
        result.update(action="failed", reason=f"{type(exc).__name__}: {exc}")
        return result
    result["returncode"] = int(proc.returncode)
    result["stdout_tail"] = " ".join((proc.stdout or "").split())[-300:]
    result["stderr_tail"] = " ".join((proc.stderr or "").split())[-200:]
    after = plugin_enabled_state(config_file=config_file)
    result["after"] = after
    if after["enabled"] is True:
        result.update(action="enabled", reason=after["reason"])
    else:
        result.update(
            action="failed",
            reason=f"the host CLI left the plugin disabled ({after['reason']})",
        )
    _ = _shutil  # kept for symmetry with the other host-CLI call sites
    return result


#: The packaged app does NOT run its backend in `~/.hermes`: the Electron main pins the
#: `hermes serve` child to `<userData>/hermes-home` (measured 17.09. on the shipped build:
#: `tr '\0' '\n' < /proc/<serve>/environ` → `HERMES_HOME=…/desktop-userdata/hermes-home`).
#: A plugin deployed only into `~/.hermes` is therefore invisible to the panel: the app's
#: panel routes 404 (`{"detail":"Plugin not found"}`, 3642 log lines in one day) while the
#: chat keeps working — installing looks successful and produces a dead panel.
APP_BACKEND_HOME_DIRNAME = "hermes-home"


def app_backend_home() -> Path:
    """The ``HERMES_HOME`` the packaged app's backend actually runs with."""
    return skaldir_user_data() / APP_BACKEND_HOME_DIRNAME


def sync_app_backend_plugin(
    *, hermes: str | None = None, runner=None, timeout: float = 60.0
) -> dict:
    """Deploy + enable the panel plugin in the APP's own home, so the panel can mount.

    Idempotent and safe to call on every launch: it links
    ``<userData>/hermes-home/plugins/godsv`` at the product plugin and enables it through
    the host CLI *scoped to that home* (``HERMES_HOME`` in the child env) — the stock
    profile's ``plugins.*`` is a different thing and never governs the app.
    """
    home = app_backend_home()
    plugins_dir = home / "plugins"
    target = plugins_dir / PLUGIN_NAME
    source = panel_source()
    linked: str | None = None
    try:
        plugins_dir.mkdir(parents=True, exist_ok=True)
        if target.is_symlink():
            if os.readlink(target) != str(source):
                target.unlink()
                target.symlink_to(source)
                linked = str(target)
        elif not target.exists():
            target.symlink_to(source)
            linked = str(target)
    except OSError as exc:
        return {
            "home": str(home),
            "link": linked,
            "state": {"plugin": PLUGIN_NAME, "action": "failed", "reason": f"link: {exc}"},
        }
    env = dict(os.environ)
    env["HERMES_HOME"] = str(home)
    state = ensure_plugin_enabled(
        hermes=hermes, runner=runner, timeout=timeout, config_file=home / "config.yaml", env=env
    )
    return {"home": str(home), "link": linked, "state": state}


def install(*, link_cli: bool = True) -> dict:
    """Install the user-local launcher: .desktop, icons, skin, optional CLI symlink."""
    applications_dir().mkdir(parents=True, exist_ok=True)
    exec_path = cli_path()
    desktop_file().write_text(desktop_entry(exec_line=exec_path), encoding="utf-8")
    # Migration: drop the pre-rebrand entry so the app grid shows ONE Skaldir
    # item instead of a Skaldir item next to the old one.
    legacy_entry = applications_dir() / f"{LEGACY_ENTRY_STEM}.desktop"
    if legacy_entry.exists() and legacy_entry != desktop_file():
        legacy_entry.unlink()
    icons = render_icons()
    linked = _link_cli() if link_cli else None
    skin = install_skin()
    panel = _link_panel()
    # A linked plugin that the host has disabled is not an installation: enable it through the
    # host's own command and report the verified state (measured: enabled: [], disabled: [godsv]).
    plugin = ensure_plugin_enabled() if panel else {"action": "skipped", "reason": "no panel link"}
    return {
        "desktop_file": str(desktop_file()),
        "icons": [str(path) for path in icons],
        "exec": exec_path,
        "cli_link": linked or "",
        "skin": skin,
        "panel_link": panel or "",
        "plugin_state": plugin.get("after") or {},
        "plugin_action": plugin.get("action", ""),
    }


def uninstall(*, remove_link: bool = True, remove_plugin: bool = False) -> dict:
    """Remove launcher + icons + skin; the CLI symlink only if it points at us.

    The plugin symlink stays deployable by default (documented behavior);
    ``remove_plugin=True`` removes it too for a full teardown.
    """
    removed: list[str] = []
    targets = [
        desktop_file(),
        applications_dir() / f"{LEGACY_ENTRY_STEM}.desktop",
        pixmap_file(),
        *(icon_file(size) for size in SIZES),
        *(icon_file(size).with_name(f"{LEGACY_ICON_NAME}.png") for size in SIZES),
        *(icon_file(size).with_name(f"{WM_CLASS.lower()}.png") for size in SIZES),
    ]
    for target in targets:
        try:
            target.unlink()
            removed.append(str(target))
        except FileNotFoundError:
            continue
    removed.extend(uninstall_skin()["removed"])
    if remove_link:
        link = bin_dir() / "skaldir"
        source = cli_path()
        try:
            if link.is_symlink() and (not source or os.readlink(link) == source):
                link.unlink()
                removed.append(str(link))
        except OSError:
            pass
    if remove_plugin:  # full teardown only — default keeps the link deployable
        try:
            plink = plugin_link()
            if plink.is_symlink():
                plink.unlink()
                removed.append(str(plink))
        except OSError:
            pass
    return {"removed": removed}


def deployment_status() -> dict:
    """What the launcher verifies before starting (and what `status` shows)."""
    link = plugin_link()
    hermes = hermes_binary()
    return {
        "hermes": hermes or "",
        "hermes_ok": hermes is not None,
        "plugin_dir": str(link),
        "plugin_ok": link.exists(),
        "panel_ok": (link / "desktop" / "plugin.js").exists(),
        "desktop_file": str(desktop_file()),
        "desktop_installed": desktop_file().exists(),
        "icons_installed": all(icon_file(size).exists() for size in SIZES),
        "cli": cli_path(),
        "user_data": str(skaldir_user_data()),
        "instance_running": app_running(),
        "skin_file": str(skin_file()),
        "skin_installed": skin_file().exists(),
        "gv_app": gods_voice_app_binary() or "",
    }


def launch(*, check_only: bool = False) -> int:
    """Open *Skaldir Agent* — the dedicated app instance (or focus it).

    Running GV instance → focus + panel route via its own deep link (scoped to
    the GV userData; a plain Hermes window is never touched). Cold start:
    refresh the packaged app (``--build-only``), then start the binary into the
    user's **last session** — the app remembers it per profile
    (``hermes.desktop.lastRoute``/``lastSessionId``) and falls back to an empty
    new chat when there is none. Never force the panel route on start.

    Returns 0 on success; 2 = hermes missing, 3 = spawn failed.
    """
    status = deployment_status()
    if check_only:
        return 0 if status["hermes_ok"] else 2
    if app_running():
        # Focus only: `start` must not yank a running window to the panel and
        # drop whatever session the user is in the middle of.
        return focus_instance()
    hermes = status["hermes"]
    if not hermes:
        return 2
    log_dir = Path.home() / ".hermes" / "godsv"
    log_dir.mkdir(parents=True, exist_ok=True)
    log = open(log_dir / "desktop-launch.log", "a", encoding="utf-8")  # noqa: SIM115
    log.write(f"\n--- launch {time.strftime('%Y-%m-%d %H:%M:%S')} via {hermes}\n")
    log.flush()
    try:
        synced = sync_app_backend_plugin(hermes=hermes)
        log.write(
            f"app-home plugin: {synced['state'].get('action')} — {synced['state'].get('reason')} "
            f"[{synced['home']}]\n"
        )
    except Exception as exc:  # noqa: BLE001 — a failed sync must never block the launch
        log.write(f"app-home plugin sync failed: {type(exc).__name__}: {exc}\n")
    log.flush()
    binary = gods_voice_app_binary()
    if binary is None:
        try:
            # No bundled GV build yet — keep the stock app current, use it meanwhile.
            # Fast stamp check; builds the packaged app only when stale. Never launches.
            subprocess.run(  # nosec B603 — fixed argv, no shell, user command
                [hermes, "desktop", "--build-only"],
                capture_output=True,
                text=True,
                timeout=900,
                env=gods_voice_env(),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            log.write(f"build-only failed: {exc}\n")
            log.flush()
        binary = hermes_app_binary()
    if binary is None:
        log.write("no packaged app found after --build-only\n")
        log.flush()
        return 3
    try:
        # No panel route here on purpose: the app restores its own remembered
        # session (`hermes.desktop.lastRoute`/`lastSessionId`, store/session.ts)
        # and falls back to an empty new chat. Forcing `hermes://open/<panel>`
        # made every start land on the cockpit, whatever the user had open
        # before (owner report 17.09.). The panel stays reachable through
        # `open_panel()`.
        subprocess.Popen(  # nosec B603 — fixed argv, no shell, user command
            [binary, f"--class={WM_CLASS}"],
            start_new_session=True,
            stdout=log,
            stderr=log,
            env=gods_voice_env(),
        )
    except OSError:
        return 3
    return 0


def format_status(status: dict) -> str:
    mark = lambda flag: "✓" if flag else "✗"  # noqa: E731 — terse local helper
    lines = [
        f"{mark(bool(status['hermes']))} Hermes-CLI: {status['hermes'] or 'not in PATH'}",
        f"{mark(status['plugin_ok'])} Plugin: {status['plugin_dir']}",
        f"{mark(status.get('panel_ok', False))} Desktop panel (sidebar + ⌘K)",
        f"{mark(status['desktop_installed'])} Desktop entry: {status['desktop_file']}",
        f"{mark(status['icons_installed'])} Icons (hicolor 64/128/256)",
        f"{mark(status.get('skin_installed', False))} Skaldir skin (monochrome, all surfaces)",
        f"{mark(bool(status.get('gv_app')))} Skaldir app build"
        f" {'(native: ' + status['gv_app'] + ')' if status.get('gv_app') else ''}"
        f"{'(uses the stock app)' if not status.get('gv_app') else ''}",
        f"{mark(status.get('instance_running', False))} Skaldir agent instance running"
        " (own userData)",
        f"  skaldir: {status['cli']}",
    ]
    return "\n".join(lines)
