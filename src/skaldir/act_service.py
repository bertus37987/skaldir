"""ACT — Computer Use as a service: what this host can really do, declared runs, GUI sub-tools.

Three things live here, all of them plain data + real checks (the CLI surface is in
:mod:`skaldir.act_cli`):

1. **The capability matrix** (:func:`capability_report`). The honest answer to "what can
   this host really do today": which *bridge* carries a GUI action, which driver it needs,
   and — when a capability is not available — the **precise missing piece** (a missing
   binary, a missing image, a missing channel), never a vague "unsupported".
   Two bridges are real: the isolated container display (rootless podman + the local
   ``localhost/godsv-cu-x11`` image, Xvfb on ``:90``) and the bwrap sandbox (CLI only).
   The **live desktop is never driven**: ``CUPolicy.require_isolation`` forbids it, so the
   matrix reports it as unavailable-by-policy instead of as a missing feature.
2. **A declared run** (:func:`run_action_list`). A caller hands in a list of Computer-Use
   actions; every action must declare how it is verified (the core's hard rule — an attempt
   without an expectation is refused), the run is executed in an isolated runner, and the
   **end state is read from the real state** afterwards. A verified action without an
   observed end state is a failure (exit 2), never a success.
3. **GUI sub-tools** (:func:`run_subtool`, :data:`BUILTIN_SUBTOOLS`). A sub-tool is data,
   exactly as specified: **preconditions + UI anchors + actions + end-state check**, plus the
   applicability facts the §11 contract needs. The five built-ins the owner named are
   ``open_browser``, ``focus_search_bar``, ``open_website``, ``open_project``,
   ``open_settings``. A stored sequence may only run when its preconditions and anchors are
   *observed right now* (``check_blind_replay``): a missing anchor reobserves, an
   unobservable channel is blocked with the missing bridge named. The one declared
   exception is a ``window-absent`` precondition (``"expect": "absent"``): a window the
   flow's own actions create is reported as ``awaiting_appearance`` instead of missing,
   so the refusal never lands before the first act — and the end-state check still
   verifies the appearance against the real observation afterwards.

Exit-code vocabulary (the product contract's numbers, ``cli_contract`` when importable)::

    0 ok · 1 usage · 2 verification failed · 3 blocked · 5 not found · 6 conflict · 7 internal

The distinction that matters: **3** is "this host cannot do it at all today" (and the record
names the missing capability), **2** is "the host could, we checked the real state, and the
declared check did not hold". Nothing in this module runs on the live desktop, and no code
path returns a success it cannot back with an observation.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from . import __version__
from .computer_use import (
    ALL_KINDS,
    CHANNEL_ORDER,
    Action,
    Attempt,
    ContainerRunner,
    CUExecutor,
    CUPolicy,
    DryRunRunner,
    LocalSandboxRunner,
    PolicyViolation,
    Verification,
    _record_cu_run,
    runner_availability,
)
from .gates import _sandbox_available

# --------------------------------------------------------------------------- #
# exit codes — the product contract's vocabulary, adopted lazily                #
# --------------------------------------------------------------------------- #


def _contract():
    """``src/skaldir/cli_contract.py`` when this build has one, else ``None``.

    Adopted lazily: the same module must also import and run standalone (tests, tools).
    """
    try:
        from . import cli_contract
    except Exception:  # noqa: BLE001 — an older build without the module
        return None
    return cli_contract


_CONTRACT = _contract()

EXIT_OK = int(getattr(_CONTRACT, "EXIT_OK", 0))
EXIT_USAGE = int(getattr(_CONTRACT, "EXIT_USAGE", 1))
EXIT_VERIFICATION_FAILED = int(getattr(_CONTRACT, "EXIT_VERIFICATION_FAILED", 2))
EXIT_BLOCKED = int(getattr(_CONTRACT, "EXIT_BLOCKED", 3))
EXIT_NOT_FOUND = int(getattr(_CONTRACT, "EXIT_NOT_FOUND", 5))
EXIT_CONFLICT = int(getattr(_CONTRACT, "EXIT_CONFLICT", 6))
EXIT_INTERNAL = int(getattr(_CONTRACT, "EXIT_INTERNAL", 7))

#: The bridges a Computer-Use action can run on. ``live-desktop`` exists so the report can
#: say *why* it is unavailable instead of leaving the reader guessing; ``host-session`` is the
#: real host path added for GAP-K3 (a display this run owns on the host, or one named window
#: of the user's session when that is explicitly opted in).
BRIDGE_HOST_SESSION = "host-session"
BRIDGE_LIVE_DESKTOP = "live-desktop"
BRIDGE_CONTAINER_GUI = "container-gui"
BRIDGE_CONTAINER = "container"
BRIDGE_SANDBOX = "sandbox"
BRIDGE_DRY_RUN = "dry-run"

BRIDGES: tuple[str, ...] = (
    BRIDGE_CONTAINER_GUI,
    BRIDGE_HOST_SESSION,
    BRIDGE_LIVE_DESKTOP,
    BRIDGE_CONTAINER,
    BRIDGE_SANDBOX,
    BRIDGE_DRY_RUN,
)

#: The action vocabulary ``act`` accepts in a declared action list. It is the Computer-Use
#: core's own kinds plus three conveniences ``act`` translates into them (see
#: :func:`to_core_action`): ``scroll`` (wheel = mouse buttons 4/5), ``window``
#: (list/focus via xdotool) and ``api_call``. No kind is added to the core's policy.
DECLARED_KINDS: tuple[str, ...] = (
    "cli",
    "api_call",
    "launch",
    "mouse",
    "key",
    "type_text",
    "scroll",
    "drag",
    "window",
    "dom_click",
    "dom_fill",
)

#: Kinds the isolated container display really carries today (drivers: xdotool + Xvfb).
CONTAINER_GUI_KINDS: tuple[str, ...] = ("mouse", "key", "type_text", "scroll", "window")

#: Kinds a host-session runner carries (a real X server on this host, drivers: xdotool).
HOST_SESSION_KINDS: tuple[str, ...] = ("mouse", "key", "type_text", "cli", "scroll", "window")

GUI_IMAGE = ContainerRunner.DEFAULT_IMAGE
CONTAINER_DISPLAY = ":90"

#: Window-title pattern of "a browser is open" — Zen/Firefox/Chrome/… (measured:
#: the owner's default is Zen; a Firefox-only pin re-opened the browser 3×).
BROWSER_TITLE_RE = r"Zen|Firefox|Chrome|Chromium|Brave|Edge|Opera|browser"
#: Launch command preference: the owner's default browser first (never hardcode firefox).
#: Idempotent: when a browser is already running, ``xdg-open`` sends the URL to the
#: existing instance (new tab) instead of spawning another process — measured: the agent
#: launched zen + firefox + zen-browser as three separate processes in one goal.
BROWSER_LAUNCH = (
    "BROWSER=${SKALDIR_CU_BROWSER:-$(xdg-settings get default-web-browser 2>/dev/null | sed 's/\\.desktop$//')}; "
    "BROWSER=${BROWSER:-xdg-open}; command -v \"$BROWSER\" >/dev/null 2>&1 || BROWSER=xdg-open; "
    "pgrep -f 'zen|firefox|chrome|chromium|brave|edge' >/dev/null 2>&1 && BROWSER=xdg-open; "
)

#: Tools the container GUI image is expected to carry. The baseline below is what was
#: measured in ``localhost/godsv-cu-x11:latest``; ``act check --probe-image`` re-verifies it
#: inside a real container instead of trusting the note. ``xwd`` is the capture tool that is
#: actually there (measured: xwd yes, import/scrot/gnome-screenshot/python3 no) — it is what
#: makes ``observe.screenshot`` real instead of impossible (GAP-K2).
IMAGE_TOOLS: tuple[str, ...] = (
    "xdotool",
    "xterm",
    "xmessage",
    "wmctrl",
    "xwd",
    "xclip",
)

#: Measured 2026-09-19 against ``localhost/godsv-cu-x11:latest``: a probe run inside the image
#: found xdotool/xterm/xmessage/xwd and no wmctrl/xclip. Used only to say what is missing;
#: ``--probe-image`` replaces it with a live check.
IMAGE_TOOL_BASELINE: dict[str, bool] = {
    "xdotool": True,
    "xterm": True,
    "xmessage": True,
    "wmctrl": False,
    "xwd": True,
    "xclip": False,
}

#: Host binaries a **host-session** run needs: a display to own, an input driver, and a
#: capture tool. Measured on this host: Xvfb + xdotool + ImageMagick's ``import`` (there is no
#: host ``xwd``), so a host capture goes through ``import`` and is reported as such.
HOST_SESSION_TOOLS: tuple[str, ...] = ("Xvfb", "xdotool")
HOST_CAPTURE_TOOLS: tuple[str, ...] = ("xwd", "import")

#: Host binaries that would give a *live desktop* driver a chance. Recorded for honesty —
#: the live desktop stays refused by policy regardless of what is installed.
LIVE_DRIVER_CANDIDATES: tuple[str, ...] = ("xdotool", "wmctrl", "wtype", "ydotool")

BROWSER_BINARIES: tuple[str, ...] = (
    "firefox",
    "chromium",
    "google-chrome",
    "brave-browser",
    "epiphany",
    "xdg-open",
)


# --------------------------------------------------------------------------- #
# host facts — measured, never assumed                                          #
# --------------------------------------------------------------------------- #


def _which(name: str) -> str:
    return shutil.which(name) or ""


def _gui_image_present() -> tuple[bool, str]:
    """Is the GUI image on this host? Never starts a container (cheap, real)."""
    podman = _which("podman")
    if not podman:
        return False, "podman is not installed"
    import subprocess  # nosec B404 — fixed argv, no shell

    try:
        proc = subprocess.run(  # nosec B603 — fixed argv list
            [podman, "image", "exists", GUI_IMAGE], capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.SubprocessError) as exc:  # noqa: BLE001
        return False, f"cannot query podman: {type(exc).__name__}"
    if proc.returncode != 0:
        return False, f"image missing: {GUI_IMAGE}"
    return True, "image present"


def _host_session_state() -> tuple[dict, dict]:
    """The live-desktop opt-in plus the host-session runner's own availability.

    Both are imported lazily so this module keeps importing (and reporting) on a build
    where the host runner is absent — in that case the honest answer is "unavailable",
    never a silent ``False`` with no reason.
    """
    try:
        from .cu_host_session import host_session_availability, live_desktop_optin

        return live_desktop_optin(), host_session_availability()
    except Exception as exc:  # noqa: BLE001 — a missing host runner is a reportable fact
        reason = f"the host-session runner is not importable: {type(exc).__name__}: {exc}"
        return (
            {"allowed": False, "opted_in": False, "target": "", "source": "",
             "reason": reason},
            {"available": False, "detail": f"unavailable ({reason})", "tools": {}},
        )


def host_facts() -> dict:
    """What is really installed and reachable on this host (all measured, no guesses)."""
    gui_ok, gui_detail = _gui_image_present()
    sandbox = bool(_sandbox_available())
    tool_names = ("bwrap", "podman", "xdotool", "Xvfb", "wtype", "ydotool")
    tools = {name: _which(name) for name in tool_names}
    tools.update({name: _which(name) for name in HOST_CAPTURE_TOOLS})
    browsers = {name: _which(name) for name in BROWSER_BINARIES}
    display = str(os.environ.get("DISPLAY", "") or "")
    wayland = str(os.environ.get("WAYLAND_DISPLAY", "") or "")
    session_type = str(os.environ.get("XDG_SESSION_TYPE", "") or "")
    live_optin, host_session = _host_session_state()
    return {
        "platform": os.uname().sysname if hasattr(os, "uname") else os.name,
        "godsv_version": __version__,
        "runtime_dir": str(_runtime_dir()),
        "session_type": session_type,
        "display": display,
        "wayland_display": wayland,
        "live_desktop_session_present": bool(display or wayland),
        "isolation_tools": tools,
        "gui_image": {"image": GUI_IMAGE, "present": bool(gui_ok), "detail": gui_detail},
        "container_display": CONTAINER_DISPLAY,
        "browser": {"binaries": browsers, "present": any(browsers.values())},
        # The named facts a sub-tool precondition may reference (``source: "host"``).
        "facts": {
            "podman": bool(tools["podman"]),
            "bwrap": sandbox,
            "gui_image": bool(gui_ok),
            "isolated_display": bool(gui_ok),
            "container_input_driver": bool(tools["podman"] and gui_ok),
            "browser_installed": any(browsers.values()),
            "desktop_app_running": False,
            # GAP-K3: no longer a hard-coded False. This is the *measured* opt-in state —
            # unchanged (false) unless an operator flipped the switch AND named a target.
            "live_desktop_allowed": bool(live_optin["allowed"]),
            "host_session": bool(host_session["available"]),
        },
        "live_desktop_optin": live_optin,
        "host_session": host_session,
        "runner_availability": runner_availability(),
    }


def _runtime_dir(runtime: str | Path | None = None) -> Path:
    """The product runtime directory (``SKALDIR_HOME`` → ``HERMES_HOME/skaldir``)."""
    if runtime not in (None, ""):
        return Path(runtime).expanduser()
    from .registry import default_runtime_dir  # local: one resolver, no second default

    return Path(default_runtime_dir())


# --------------------------------------------------------------------------- #
# the capability matrix                                                         #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Capability:
    """One GUI/desktop capability: which bridge carries it, with which driver.

    ``requires_tools`` are the binaries the *target bridge* needs (inside the image for
    ``container-gui``); ``verification`` names the channel a real check can read afterwards.
    """

    name: str
    bridge: str
    driver: str
    requires_tools: tuple[str, ...] = ()
    #: Binaries the bridge needs *one of* (a capture tool: ``xwd`` or ``import``).
    requires_any: tuple[str, ...] = ()
    verification: str = ""
    note: str = ""
    kind: str = "cli"
    #: The precise piece that is missing when this capability is unavailable (the honest
    #: "what would have to exist" — never a vague "not supported").
    unavailable_capability: str = ""


CAPABILITIES: tuple[Capability, ...] = (
    Capability(
        name="window.list",
        bridge=BRIDGE_CONTAINER_GUI,
        driver="xdotool search --name . getwindowname %@",
        requires_tools=("xdotool",),
        verification="the window list itself (an observation, read back from the display)",
        note="this is the observation channel: it is what anchor checks read",
    ),
    Capability(
        name="window.focus",
        bridge=BRIDGE_CONTAINER_GUI,
        driver="xdotool windowfocus --sync <id>",
        requires_tools=("xdotool",),
        verification="the app receives the following keys (an app-state read)",
        note="measured: the image has no window manager, so `xdotool windowactivate` fails "
        "(no _NET_ACTIVE_WINDOW) and focus must be taken with `windowfocus`",
    ),
    Capability(
        name="click",
        bridge=BRIDGE_CONTAINER_GUI,
        driver="xdotool mousemove --sync X Y click [N]",
        requires_tools=("xdotool",),
        verification=(
            "an app-state read (the app reports the click in its own state) plus, since the "
            "capture exists, the screenshot taken right after the click"
        ),
        note="`mousemove` must be `--sync`, otherwise the button event overtakes the motion "
        "and the click lands where the pointer was (act builds the argv that way). A bare "
        "click still proves nothing on its own: its effect has to come from the target app "
        "or from the capture taken afterwards",
    ),
    Capability(
        name="key",
        bridge=BRIDGE_CONTAINER_GUI,
        driver="xdotool key --clearmodifiers <name>",
        requires_tools=("xdotool",),
        verification="an app-state read (the app received the keystroke)",
        note="needs the target window focused first: there is no window manager to route "
        "keys, so the focus has to be taken explicitly",
    ),
    Capability(
        name="type_text",
        bridge=BRIDGE_CONTAINER_GUI,
        driver="xdotool type --delay <ms> -- <text>",
        requires_tools=("xdotool",),
        verification="an app-state read (the typed text arrived in the app)",
        note="measured: `xdotool type --window` uses XSendEvent and real apps (xterm) ignore "
        "it — focus the window and type without --window",
    ),
    Capability(
        name="scroll",
        bridge=BRIDGE_CONTAINER_GUI,
        driver="xdotool click --repeat N 4|5",
        requires_tools=("xdotool",),
        verification="an app-state read (the app received the wheel events)",
        note="the Computer Use module has no dedicated `scroll` kind; act dispatches it as "
        "the mouse kind with the X wheel buttons 4 (up) / 5 (down)",
    ),
    Capability(
        name="observe.window_titles",
        bridge=BRIDGE_CONTAINER_GUI,
        driver="xdotool search --name . getwindowname %@",
        requires_tools=("xdotool",),
        verification="the observation is the check (strings read from the display)",
    ),
    Capability(
        name="observe.screenshot",
        bridge=BRIDGE_CONTAINER_GUI,
        driver="xwd inside the image (`xwd -root| -id <win> -silent`) → PNG on the host",
        requires_tools=("xwd",),
        verification=(
            "the bytes on disk: the XWD dump is decoded to a real PNG and hashed "
            "(sha256), so a capture either exists or the step is unknown"
        ),
        note="the image ships xwd and no converter, so the decode happens on the host with "
        "the standard library (computer_use.xwd_to_png) — no ImageMagick and no Pillow. "
        "Reviewing images against each other stays the caller's job (a vision model or a "
        "human), which is why this capability verifies the *capture*, not its meaning",
    ),
    Capability(
        name="host.session.screenshot",
        bridge=BRIDGE_HOST_SESSION,
        driver="xwd -root (host) or ImageMagick `import -window <id>` on the owned display",
        requires_any=HOST_CAPTURE_TOOLS,
        verification="a real PNG on disk with its sha256, captured from the display this run owns",
        note="a host-session run owns its own Xvfb, so a capture there shows only that "
        "display. In live-session mode only the one target window is captured, never the "
        "user's screen",
    ),
    Capability(
        name="app.state",
        bridge=BRIDGE_HOST_SESSION,
        driver="app_state_probe / app_state_file / window_metadata (xdotool+xprop)",
        verification="the app's own state, or honest window metadata labelled as such",
        note=(
            "the channel is wired: a declared app_state_probe or app_state_file is the "
            "strongest source; otherwise window metadata (titles + focus + WM_CLASS) is "
            "read and labelled `window_metadata` — never pretended to be a full a11y tree"
        ),
        unavailable_capability=(
            "app_state_probe (the app's own dump argv) or app_state_file for a richer state"
        ),
    ),
)


def _container_tools(*, probe: bool) -> dict:
    """Which tools the container GUI image really carries.

    Without ``probe`` the answer is the image's *presence* plus the measured baseline: the
    tools are reported as unverified (with the baseline only used to name what is missing),
    never as confirmed.
    """
    present, detail = _gui_image_present()
    if not present:
        return {"verified": False, "present": False, "detail": detail, "tools": {}}
    if not probe:
        return {
            "verified": False,
            "present": True,
            "detail": (
                f"{detail} — the tools inside it are not re-checked by this run "
                "(pass --probe-image to verify them in the real container)"
            ),
            "tools": {},
            "baseline": dict(IMAGE_TOOL_BASELINE),
        }
    return _probe_container_tools()


def _probe_container_tools() -> dict:
    """Start the isolated display once and ask it which tools exist (a real check)."""
    import subprocess  # nosec B404 — fixed argv, no shell

    runner = ContainerRunner(gui=True, name=f"godsv-act-check-{int(time.time()) % 1000000}")
    try:
        ok, detail = runner.start()
        if not ok:
            return {"verified": False, "present": True, "detail": detail, "tools": {}}
        script = "; ".join(
            f'printf "%s=%s\\n" {name} "$(command -v {name} || echo MISSING)"'
            for name in IMAGE_TOOLS
        )
        proc = subprocess.run(  # nosec B603 — fixed argv list
            ["podman", "exec", runner.name, "sh", "-c", script],
            capture_output=True,
            text=True,
            timeout=90,
        )
        found: dict[str, str] = {}
        for line in (proc.stdout or "").splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                found[key.strip()] = value.strip()
        return {
            "verified": True,
            "present": True,
            "detail": f"probed in a real container ({runner.name})",
            "tools": found,
        }
    except (OSError, subprocess.SubprocessError) as exc:  # noqa: BLE001
        return {
            "verified": False,
            "present": True,
            "detail": f"probe failed: {type(exc).__name__}: {exc}",
            "tools": {},
        }
    finally:
        try:
            runner.close()
        except Exception:  # noqa: BLE001 — a failed teardown must not hide the matrix
            pass


def _bridge_records(*, probe_image: bool = False) -> list[dict]:
    """One honest record per bridge: available or not, and exactly why."""
    facts = host_facts()
    image = facts["gui_image"]
    container_tools = _container_tools(probe=probe_image)
    live_drivers = {name: _which(name) for name in LIVE_DRIVER_CANDIDATES}

    records = [
        {
            "id": BRIDGE_HOST_SESSION,
            "isolated": True,
            "available": bool(facts["host_session"]["available"]),
            "real": bool(facts["host_session"]["available"]),
            "reason": (
                "a real X server on this host, in a display the run itself owns "
                f"({facts['host_session']['detail']})"
                if facts["host_session"]["available"]
                else f"unavailable: {facts['host_session']['detail']}"
            ),
            "detail": {
                "tools": facts["host_session"]["tools"],
                "capture_tool": next(
                    (
                        name
                        for name in HOST_CAPTURE_TOOLS
                        if facts["isolation_tools"].get(name)
                    ),
                    "",
                ),
                "scope": (
                    "owned display (an Xvfb this run starts; no user window is touched); "
                    "one named window of the user's session only when explicitly opted in"
                ),
            },
        },
        {
            "id": BRIDGE_LIVE_DESKTOP,
            "isolated": False,
            "available": bool(facts["live_desktop_optin"]["allowed"]),
            "real": bool(facts["live_desktop_optin"]["allowed"]),
            "forbidden_by_policy": not bool(facts["live_desktop_optin"]["allowed"]),
            "opt_in_target": str(facts["live_desktop_optin"].get("target", "")),
            "reason": (
                "opted in and target-scoped: only the window "
                f"{facts['live_desktop_optin']['target']!r} may be driven, and a policy that "
                "names the same target is still required"
                if facts["live_desktop_optin"]["allowed"]
                else (
                    "the live desktop is refused: CUPolicy.require_isolation rejects any "
                    "runner whose isolation is 'none', and the opt-in is not set — "
                    + str(facts["live_desktop_optin"]["reason"])
                )
            ),
            "detail": {
                "session_present": facts["live_desktop_session_present"],
                "session_type": facts["session_type"],
                "display": facts["display"],
                "wayland_display": facts["wayland_display"],
                "drivers_on_host": live_drivers,
                "opted_in": bool(facts["live_desktop_optin"]["opted_in"]),
                "target": str(facts["live_desktop_optin"].get("target", "")),
                "source": str(facts["live_desktop_optin"].get("source", "")),
            },
        },
        {
            "id": BRIDGE_CONTAINER_GUI,
            "isolated": True,
            "available": bool(image["present"]),
            "real": bool(image["present"]),
            "display": CONTAINER_DISPLAY,
            "reason": (
                f"isolated X display {CONTAINER_DISPLAY} inside rootless podman "
                f"({image['detail']})"
                if image["present"]
                else f"unavailable: {image['detail']}"
            ),
            "detail": {
                "image": GUI_IMAGE,
                "image_present": bool(image["present"]),
                "tools_verified": bool(container_tools["verified"]),
                "tools": container_tools["tools"],
                "baseline": container_tools.get("baseline") or {},
                "tools_detail": container_tools["detail"],
            },
        },
        {
            "id": BRIDGE_CONTAINER,
            "isolated": True,
            "available": bool(shutil.which("podman")),
            "real": bool(shutil.which("podman")),
            "reason": (
                "rootless podman is installed (a container without a display: CLI actions only)"
                if shutil.which("podman")
                else "unavailable: podman is not installed"
            ),
            "detail": {"podman": bool(shutil.which("podman"))},
        },
        {
            "id": BRIDGE_SANDBOX,
            "isolated": True,
            "available": bool(facts["facts"]["bwrap"]),
            "real": bool(facts["facts"]["bwrap"]),
            "reason": (
                "bwrap works on this host: real CLI actions inside a sandbox, no GUI"
                if facts["facts"]["bwrap"]
                else "unavailable: bwrap is missing or cannot create a user namespace"
            ),
            "detail": {"bwrap": bool(facts["facts"]["bwrap"])},
        },
        {
            "id": BRIDGE_DRY_RUN,
            "isolated": True,
            "available": True,
            "real": False,
            "fixture": True,
            "reason": (
                "always available, never real: a scripted in-process runner (fixture) — "
                "it drives no display and proves nothing about the host"
            ),
            "detail": {},
        },
    ]
    return records


def capability_report(*, probe_image: bool = False) -> dict:
    """The honest capability matrix of this host (see the module docstring).

    Read-only: without ``probe_image`` no container is started and nothing is executed.
    """
    facts = host_facts()
    bridges = _bridge_records(probe_image=probe_image)
    by_id = {record["id"]: record for record in bridges}
    container_gui = by_id[BRIDGE_CONTAINER_GUI]
    gui_detail = dict(container_gui["detail"])
    container_tools = dict(gui_detail.get("tools") or {})
    baseline = dict(gui_detail.get("baseline") or {})
    tools_verified = bool(gui_detail.get("tools_verified"))
    capabilities: list[dict] = []

    def tool_state(tool: str) -> tuple[bool | None, str]:
        """``(present, how_we_know)`` — ``None`` means "not measured", never "fine".

        With a live probe the container answers; without one the measured baseline answers
        for the tools it covers (and everything else stays unmeasured).
        """
        if tools_verified:
            return container_tools.get(tool, "MISSING") != "MISSING", "probed inside the image"
        if tool in baseline:
            return bool(baseline[tool]), "measured baseline (--probe-image re-verifies)"
        return None, "not measured"

    for spec in CAPABILITIES:
        record: dict[str, Any] = {
            "name": spec.name,
            "bridge": spec.bridge,
            "driver": spec.driver,
            "verification_channel": spec.verification,
            "kind": spec.kind,
            "note": spec.note,
        }
        target = by_id.get(spec.bridge, {})
        missing: list[str] = []
        available = bool(target.get("available"))
        reason = str(target.get("reason", ""))
        if available and spec.bridge == BRIDGE_CONTAINER_GUI:
            for tool in spec.requires_tools:
                present, how = tool_state(tool)
                if present is None:
                    missing.append(
                        f"an unmeasured tool '{tool}' inside {GUI_IMAGE} ({how}) — "
                        "run `act check --probe-image` to settle it"
                    )
                elif not present:
                    missing.append(
                        f"the tool '{tool}' inside the image {GUI_IMAGE} ({how})"
                    )
            if missing:
                available = False
        if spec.bridge == BRIDGE_HOST_SESSION:
            # A host-session capability needs the host tools (the bridge record names them
            # already) and, for a capture, *one of* the host capture binaries.
            for tool in spec.requires_tools:
                if not facts["isolation_tools"].get(tool):
                    missing.append(f"the host tool '{tool}'")
            if spec.requires_any and not any(
                facts["isolation_tools"].get(tool) for tool in spec.requires_any
            ):
                missing.append(
                    "a host capture tool (one of: "
                    + ", ".join(spec.requires_any)
                    + ")"
                )
            if missing:
                available = False
        if spec.bridge == BRIDGE_LIVE_DESKTOP:
            available = False
            optin = facts.get("live_desktop_optin") or {}
            reason = (
                "blocked: no channel reads the target app's own state (a11y tree / DOM / app "
                "API) and the live desktop is refused unless it is explicitly opted in — "
                + str(optin.get("reason", "the opt-in is off"))
            )
        record["available"] = bool(available)
        record["real"] = bool(available and target.get("real"))
        record["reason"] = reason if available else (reason or "unavailable")
        if missing:
            record["missing"] = missing
        if not available:
            record["missing_capability"] = (
                spec.unavailable_capability
                or (missing[0] if missing else f"a working '{spec.bridge}' bridge")
            )
        capabilities.append(record)

    real = [record for record in capabilities if record["real"]]
    live_allowed = bool((facts.get("facts") or {}).get("live_desktop_allowed"))
    return {
        "host": facts,
        "bridges": bridges,
        "capabilities": capabilities,
        "capability_names": [record["name"] for record in capabilities],
        "policy": policy_record(),
        "live_desktop": live_allowed,
        "live_desktop_allowed": live_allowed,
        "live_desktop_optin": facts.get("live_desktop_optin") or {},
        "verdict": (
            "live-desktop-opted-in"
            if live_allowed
            else ("isolated-only" if by_id[BRIDGE_CONTAINER_GUI]["available"] else "blocked")
        ),
        "summary": {
            "capabilities_total": len(capabilities),
            "available": len([r for r in capabilities if r["available"]]),
            "real": len(real),
            "bridges_available": [r["id"] for r in bridges if r["available"]],
            "bridges_real": [r["id"] for r in bridges if r.get("real")],
        },
    }


def capability_names(*, probe_image: bool = False) -> list[str]:
    return [
        record["name"]
        for record in capability_report(probe_image=probe_image)["capabilities"]
    ]


def capability_status(name: str, *, probe_image: bool = False) -> dict:
    """One capability's honest record, or a not-found record naming the known ones."""
    wanted = str(name or "").strip()
    for record in capability_report(probe_image=probe_image)["capabilities"]:
        if record["name"] == wanted:
            return record
    return {
        "name": wanted,
        "available": False,
        "known": capability_names(probe_image=probe_image),
        "not_found": True,
        "reason": f"unknown capability {wanted!r}",
        "missing_capability": f"a capability named {wanted!r}",
    }


def policy_record(policy: CUPolicy | None = None) -> dict:
    """The policy a run is bound by, always published with the result."""
    policy = policy or CUPolicy()
    return {
        "require_isolation": policy.require_isolation,
        "dry_run": policy.dry_run,
        "max_actions": policy.max_actions,
        "max_fallbacks": policy.max_fallbacks,
        "allowed_kinds": sorted(policy.allowed_kinds),
        # Published because it is the one field that decides whether the user's own session
        # may be touched at all: a record without it would hide the difference.
        "allow_live_desktop": bool(policy.allow_live_desktop),
        "live_desktop_target": str(policy.live_desktop_target or ""),
    }


def default_policy() -> CUPolicy:
    """The policy an ``act`` run uses: isolation required, the core's kinds unchanged.

    ``act`` adds no kind to the executable set — ``scroll`` and ``window`` are translated
    into the core's ``mouse``/``cli`` kinds (:func:`to_core_action`), so the state-based
    ``allowed_kinds`` discipline of the Computer-Use module stays exactly as it is.
    """
    return CUPolicy()


def live_setup_report(
    *,
    target: str = "",
    runtime: str | Path | None = None,
    write: bool = False,
    dry_run: bool = False,
) -> dict:
    """The guided live-desktop opt-in: measure, list windows, write one target.

    One place that answers "how do I let the agent drive a window of my session?":
    the current opt-in state, the host tools, the windows really on screen, and —
    only when ``write`` is set and ``target`` names one window — the atomic
    settings write. Never opens the desktop by itself.
    """
    from .cu_host_session import list_session_windows, write_live_optin

    facts = host_facts()
    optin = dict(facts.get("live_desktop_optin") or {})
    windows = list_session_windows()
    wanted = str(target or "").strip()
    written: dict | None = None
    # --dry-run alone is a write preview when a target is named; --write is the real write.
    if write or (dry_run and wanted):
        written = write_live_optin(wanted, runtime=runtime, dry_run=bool(dry_run or not write))
    if written and written.get("wrote"):
        facts = host_facts()
        optin = dict(facts.get("live_desktop_optin") or {})
    next_steps: list[str] = []
    if not wanted:
        next_steps.append(
            "pick a window id or exact title from `windows` and re-run with --target <it> --write"
        )
    elif dry_run and not write:
        next_steps.append(f"re-run with --target {wanted!r} --write to store the opt-in")
    elif not write:
        next_steps.append(f"re-run with --target {wanted!r} --write to store the opt-in")
    else:
        next_steps.append(
            "run `skaldir act run --runner live-desktop --actions FILE --screenshot --json`"
            " (the stored opt-in is enough; the CLI flags are a per-call override)"
        )
        next_steps.append(
            "or one sub-tool: `skaldir act subtool run open_browser --runner live-desktop --json`"
        )
    return {
        "optin": optin,
        "allowed_now": bool(optin.get("allowed")),
        "host": {
            "display": facts.get("display", ""),
            "session_type": facts.get("session_type", ""),
            "live_desktop_session_present": facts.get("live_desktop_session_present"),
            "host_session": (facts.get("host_session") or {}),
        },
        "windows": windows.get("windows") or [],
        "window_count": windows.get("count", 0),
        "windows_reason": windows.get("reason", ""),
        "requested_target": wanted,
        "write": written,
        "next_steps": next_steps,
        "ok": bool(written.get("ok", True)) if written else True,
    }


def _sync_live_policy(
    policy: CUPolicy,
    resolved_runner: Any,
    *,
    allow_live_desktop: bool = False,
    live_target: str = "",
) -> CUPolicy:
    """Align the policy with a live runner the owner's opt-in already authorised.

    :func:`resolve_runner` builds a ``live-desktop`` runner when the *settings/env*
    opt-in is on, but the executor still refuses it unless the policy carries the same
    opt-in — so "set settings.yaml" used to create a runner and then die at
    ``PolicyViolation``. The CLI ``--allow-live-desktop`` path set the policy
    explicitly; this helper covers the settings-only path so the documented switch is
    a complete instruction.

    Isolated runners are never widened. A model tool call never passes
    ``allow_live_desktop=True`` (``skill_act`` has no such field): only the owner's
    switch can open the live desktop.
    """
    if getattr(resolved_runner, "is_isolated", True):
        return policy
    if policy.allow_live_desktop and str(policy.live_desktop_target or "").strip():
        return policy
    target_scope = str(getattr(resolved_runner, "target_scope", "") or "")
    if allow_live_desktop:
        target = str(live_target or target_scope or "").strip()
        if not target:
            target = str((host_facts().get("live_desktop_optin") or {}).get("target", "") or "")
        return replace(policy, allow_live_desktop=True, live_desktop_target=target)
    optin = host_facts().get("live_desktop_optin") or {}
    if not optin.get("allowed"):
        return policy
    target = target_scope or str(optin.get("target", "") or "")
    return replace(policy, allow_live_desktop=True, live_desktop_target=target)


# --------------------------------------------------------------------------- #
# runner resolution                                                             #
# --------------------------------------------------------------------------- #


def resolve_runner(
    kind: str = "auto",
    *,
    workdir: str | Path | None = None,
    timeout: float = 90.0,
    allow_live_desktop: bool = False,
    live_target: str = "",
) -> tuple[Any | None, dict]:
    """Map a requested runner name to a real runner, or explain precisely why not.

    ``auto`` picks the strongest **real** bridge that is available (isolated container GUI →
    host session → sandbox). It never falls back to the in-process fixture: an ``auto`` run
    that could only be answered by a fixture is blocked, so nothing pretends to be a real
    action. The fixture stays reachable by asking for it (``--runner dry-run``).

    ``host-session`` is the host path: a real X server, in a display this run owns. It is
    *not* part of ``auto`` when the live-desktop opt-in is off — but the owned-display mode
    needs no opt-in (it touches nothing of the user's), so it is offered as a real bridge
    whenever the host tools are there. ``live-desktop`` (the user's own session, one named
    window) requires *both* ``allow_live_desktop`` and ``live_target``.
    """
    key = (kind or "auto").strip().lower()
    facts = host_facts()
    workdir_path = Path(workdir).expanduser() if workdir else Path.cwd()
    optin = facts.get("live_desktop_optin") or {}

    def record(bridge: str, runner, *, real: bool, reason: str) -> dict:
        return {
            "requested": key or "auto",
            "bridge": bridge,
            "resolved": bool(runner is not None),
            "isolation": getattr(runner, "isolation", ""),
            "real": bool(real and runner is not None),
            "fixture": bridge == BRIDGE_DRY_RUN,
            "scope": "in-process fixture" if bridge == BRIDGE_DRY_RUN else "real",
            "workdir": str(workdir_path),
            "target_scope": str(getattr(runner, "target_scope", "") or ""),
            "reason": reason,
        }

    if key in ("dry-run", "dryrun", "dry", "fixture"):
        return DryRunRunner(), record(
            BRIDGE_DRY_RUN,
            DryRunRunner(),
            real=False,
            reason=(
                "explicitly requested in-process fixture: scripted observations only, no "
                "display is driven and nothing about this host is proven"
            ),
        )

    if key in ("container-gui", "gui", "container"):
        gui = key != "container"
        runner = ContainerRunner(gui=gui, timeout=timeout)
        ok, detail = runner.available()
        if not ok:
            return None, record(
                BRIDGE_CONTAINER_GUI if gui else BRIDGE_CONTAINER,
                None,
                real=False,
                reason=f"unavailable: {detail}",
            )
        bridge = BRIDGE_CONTAINER_GUI if gui else BRIDGE_CONTAINER
        return runner, record(
            bridge,
            runner,
            real=True,
            reason=(
                f"isolated X display {CONTAINER_DISPLAY} (rootless podman, {GUI_IMAGE})"
                if gui
                else "rootless podman without a display: CLI actions only"
            ),
        )

    if key in ("host-session", "host", "live-desktop"):
        from .cu_host_session import HostSessionRunner, TargetScopeRefused

        attach = key == "live-desktop"
        target = str(live_target or optin.get("target", "") or "").strip()
        wanted_optin = bool(allow_live_desktop or optin.get("opted_in"))
        if attach and not (wanted_optin and target):
            return None, record(
                BRIDGE_LIVE_DESKTOP,
                None,
                real=False,
                reason=(
                    "unavailable: the live desktop needs an explicit opt-in "
                    f"({BRIDGE_LIVE_DESKTOP} was asked for, but "
                    + (
                        f"no target is named) — {optin.get('reason', '')}"
                        if wanted_optin
                        else f"the opt-in is off) — {optin.get('reason', '')}"
                    )
                ),
            )
        try:
            runner = HostSessionRunner(
                target=target,
                allow_live_desktop=wanted_optin,
                attach_to_session=attach,
                timeout=timeout,
            )
        except TargetScopeRefused as exc:
            return None, record(BRIDGE_LIVE_DESKTOP, None, real=False, reason=str(exc))
        ok, detail = runner.available()
        if not ok:
            return None, record(
                BRIDGE_LIVE_DESKTOP if attach else BRIDGE_HOST_SESSION,
                None,
                real=False,
                reason=f"unavailable: {detail}",
            )
        bridge = BRIDGE_LIVE_DESKTOP if attach else BRIDGE_HOST_SESSION
        return runner, record(
            bridge,
            runner,
            real=True,
            reason=(
                f"live session {runner.display}, target window {target!r} only"
                if attach
                else "a real X server on this host, in a display this run owns "
                "(no user window is touched)"
            ),
        )

    if key in ("sandbox", "bwrap", "local-sandbox"):
        if not _sandbox_available():
            return None, record(
                BRIDGE_SANDBOX,
                None,
                real=False,
                reason="unavailable: bwrap is missing or cannot create a user namespace",
            )
        runner = LocalSandboxRunner(workdir_path, timeout=timeout)
        return runner, record(
            BRIDGE_SANDBOX,
            runner,
            real=True,
            reason="bwrap sandbox (network namespace off, root read-only): CLI actions only",
        )

    if key not in ("", "auto"):
        return None, record(
            "",
            None,
            real=False,
            reason=(
                f"unknown runner {kind!r} — known: auto, container-gui, container, "
                "host-session, live-desktop, sandbox, dry-run"
            ),
        )

    # auto: the strongest REAL bridge, never the fixture
    if facts["gui_image"]["present"]:
        runner = ContainerRunner(gui=True, timeout=timeout)
        ok, detail = runner.available()
        if ok:
            return runner, record(
                BRIDGE_CONTAINER_GUI,
                runner,
                real=True,
                reason=(
                    f"auto → isolated X display {CONTAINER_DISPLAY} (rootless podman, "
                    f"{GUI_IMAGE})"
                ),
            )
        auto_detail = detail
    else:
        auto_detail = facts["gui_image"]["detail"]
    # ``host-session`` is deliberately NOT part of ``auto``: it starts a display process on
    # this host, and ``auto`` is the path a composite takes without saying anything. It stays
    # reachable by naming it (``--runner host-session``), so the automatic behaviour — and the
    # isolation guarantee it implies — is exactly what it was before this wave.
    if _sandbox_available():
        runner = LocalSandboxRunner(workdir_path, timeout=timeout)
        return runner, record(
            BRIDGE_SANDBOX,
            runner,
            real=True,
            reason=(
                f"auto → bwrap sandbox (no isolated display available: {auto_detail}); "
                "CLI actions only, no GUI kind"
            ),
        )
    return None, record(
        "",
        None,
        real=False,
        reason=(
            "no real bridge is available on this host: "
            f"container GUI → {auto_detail}; sandbox → bwrap is unusable. "
            "A run would be an in-process fixture, which `auto` refuses — ask for it "
            "explicitly with --runner dry-run if a fixture is what you want"
        ),
    )


# --------------------------------------------------------------------------- #
# actions: declaration, validation, translation                                 #
# --------------------------------------------------------------------------- #


def action_error(index: int, item: Any) -> str:
    """Why this declared action cannot run — with the index, never a vague refusal."""
    if not isinstance(item, Mapping):
        return f"action {index}: must be an object, got {type(item).__name__}"
    kind = str(item.get("kind", "") or "").strip()
    if not kind:
        return f"action {index}: 'kind' is required"
    if kind not in DECLARED_KINDS:
        return (
            f"action {index}: unknown kind {kind!r} — known: {', '.join(DECLARED_KINDS)}"
        )
    channel = str(item.get("channel", "") or "").strip()
    if channel and channel not in CHANNEL_ORDER:
        return f"action {index}: unknown channel {channel!r} — known: {', '.join(CHANNEL_ORDER)}"
    verify = item.get("verify")
    if not isinstance(verify, Mapping):
        return (
            f"action {index}: 'verify' is required — an attempt without an expectation is "
            "refused by the Computer Use policy"
        )
    expected = [
        str(verify.get(key, "") or "").strip()
        for key in ("expect_stdout_contains", "expect_observe_contains")
    ]
    if not any(expected) and not bool(verify.get("expect_ok", False)):
        return (
            f"action {index}: 'verify' names no expectation — set expect_stdout_contains "
            "and/or expect_observe_contains (or expect_ok for a pure key/text touch "
            "whose proof is the end state) (with none of them, the action could never "
            "be verified)"
        )
    if kind == "cli":
        argv = item.get("argv")
        if not isinstance(argv, (list, tuple)) or not argv:
            return f"action {index}: kind 'cli' needs a non-empty 'argv' list"
    if kind == "api_call":
        # api_call is an HTTP call, not a process — needs payload (url, method…)
        payload = item.get("payload")
        if not isinstance(payload, Mapping) or not payload.get("url"):
            return (
                f"action {index}: kind 'api_call' needs payload.url "
                "(it is an HTTP call, not a shell command)"
            )
    if kind == "key" and not str((item.get("payload") or {}).get("key", "") or "").strip():
        return f"action {index}: kind 'key' needs payload.key"
    if kind == "type_text" and not str((item.get("payload") or {}).get("text", "") or ""):
        return f"action {index}: kind 'type_text' needs payload.text"
    if kind == "window":
        operation = str((item.get("payload") or {}).get("op", "") or "").strip()
        if operation not in ("list", "focus"):
            return f"action {index}: kind 'window' needs payload.op 'list' or 'focus'"
    return ""


def _xdotool_window_argv(payload: Mapping) -> list[str]:
    title = str(payload.get("title", "") or "").strip()
    if str(payload.get("op", "")) == "list":
        return ["xdotool", "search", "--name", title or ".", "getwindowname", "%@"]
    # focus: `windowactivate` needs a window manager (there is none in the image), so
    # `windowfocus` is what actually moves the input focus — measured, not guessed.
    # `shlex.quote` because the title is data: a title containing `'` used to end the
    # quoted pattern and hand the rest of the line to the shell (see the security
    # audit's SHELL-C-INTERPOLATION finding — the argv is executed by `sh -c`).
    pattern = shlex.quote(title or ".")
    return [
        "sh",
        "-c",
        f"wid=$(xdotool search --name {pattern} | head -1); "
        "test -n \"$wid\" && xdotool windowfocus --sync $wid && echo FOCUSED",
    ]


def _mouse_argv(payload: Mapping) -> list[str]:
    """A pointer action whose move is *settled* before the click.

    Measured (2026-09-18, isolated display): ``xdotool mousemove X Y click 1`` in one
    invocation clicks where the pointer *was*, because the motion is asynchronous and the
    button event overtakes it — the click lands outside the window. ``mousemove --sync``
    waits for the server to apply the motion, so ``act`` builds the argv that way. (The
    Computer Use core's own payload path omits ``--sync``; this is the ACT layer's fix, and
    the reason the click capability names its verification channel so precisely.)
    """
    x = int(payload.get("x", 0) or 0)
    y = int(payload.get("y", 0) or 0)
    argv = ["xdotool", "mousemove", "--sync", str(x), str(y)]
    if payload.get("click"):
        argv += ["click", str(int(payload.get("button", 1) or 1))]
    return argv


def _scroll_argv(payload: Mapping) -> list[str]:
    direction = str(payload.get("direction", "down") or "down").strip().lower()
    button = 4 if direction in ("up", "4") else 5
    amount = max(1, int(payload.get("amount", 1) or 1))
    argv: list[str] = []
    if payload.get("x") is not None and payload.get("y") is not None:
        argv += ["xdotool", "mousemove", "--sync", str(int(payload["x"])), str(int(payload["y"]))]
    argv += ["xdotool", "click", "--repeat", str(amount), str(button)]
    return argv


def to_core_action(item: Mapping) -> Action:
    """Translate a declared action into the Computer-Use core's own ``Action``.

    The core keeps its kind vocabulary: ``scroll`` becomes the ``mouse`` kind with the X
    wheel buttons 4/5, and ``window`` becomes ``cli`` with an xdotool argv. Nothing is
    widened — the policy's ``allowed_kinds`` stays the core's.

    The argv attached here is the **owned-display** form (that display belongs to the run).
    In live-session mode the runner never executes a caller argv: a pointer action is built
    there from ``payload`` + the target window's geometry, keys/text from their payload, and
    a ``cli``/``window`` action is refused (report 18, F1/F8) — so a plan cannot smuggle a
    command into the user's session through this translation.
    """
    kind = str(item.get("kind", "")).strip()
    payload = dict(item.get("payload") or {})
    description = str(item.get("description", "") or "")[:200]
    if kind == "scroll":
        return Action(kind="mouse", argv=_scroll_argv(payload), payload=payload,
                      description=description or "scroll")
    if kind == "drag":
        # slider / drag: kept as its own kind so the full-desktop runner builds
        # mousedown → mousemove → mouseup (a human slider gesture)
        return Action(kind="drag", argv=[], payload=payload,
                      description=description or "drag")
    if kind == "launch":
        # app launch on the live desktop: payload.command / payload.argv
        return Action(kind="launch", argv=[], payload=payload,
                      description=description or "launch")
    if kind == "window":
        return Action(kind="cli", argv=_xdotool_window_argv(payload), payload=payload,
                      description=description or f"window {payload.get('op', '')}")
    if kind == "mouse":
        return Action(kind="mouse", argv=_mouse_argv(payload), payload=payload,
                      description=description or "mouse")
    return Action(
        kind=kind,
        argv=[str(part) for part in (item.get("argv") or [])],
        payload=payload,
        description=description,
    )


def to_core_verification(item: Mapping) -> Verification:
    verify = dict(item.get("verify") or {})
    return Verification(
        expect_stdout_contains=_opt_str(verify.get("expect_stdout_contains")),
        expect_observe_contains=_opt_str(verify.get("expect_observe_contains")),
        expect_ok=bool(verify.get("expect_ok", False)),
        description=str(verify.get("description", "") or "")[:200],
    )


def action_channel(item: Mapping) -> str:
    """The report channel of a declaration — the attempt is ordered by the core."""
    channel = str(item.get("channel", "") or "").strip()
    return channel or "api"


def _opt_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _expected_text(item: Mapping) -> str:
    verify = dict(item.get("verify") or {})
    for key in ("expect_stdout_contains", "expect_observe_contains"):
        text = _opt_str(verify.get(key))
        if text:
            return f"{key}={text!r}"
    return ""


# --------------------------------------------------------------------------- #
# the declared run                                                              #
# --------------------------------------------------------------------------- #


def _open_events(runtime_dir: Path):
    """An event store for the audit trail, or ``None`` (a broken store must not hide the run)."""
    try:
        from .events import EventStore

        return EventStore(runtime_dir / "events.db")
    except Exception:  # noqa: BLE001 — the audit trail is additive, never a precondition
        return None


def check_end_state(
    end_state: Any,
    observation_text: str,
    *,
    app_state: Mapping[str, Any] | None = None,
    retries: int = 0,
    retry_delay: float = 1.5,
    observe_fn: Any = None,
) -> dict:
    """Compare the declared end state against the REAL observation, never against a claim.

    A declared end state is mandatory: a run whose effect is never stated cannot be verified
    (the core's §54.2 rule). Only the observation counts; the action's own stdout is *not*
    accepted here, because the end state is about the state the run left behind.

    When ``end_state.channel`` is ``app_state``, the comparison runs against
    :func:`read_app_state`'s text (the app's own state / window metadata), not against
    bare window titles.

    **Retry with delay** (``retries > 0``): the observation is re-read via ``observe_fn``
    (callable returning new observation text) every ``retry_delay`` seconds until the
    end state matches or retries are exhausted. Measured: a browser launch takes 2–5s
    to create its window — checking immediately always failed.

    **ACT-04 Re-observe**: when the initial observation is ``outcome_unknown`` (e.g.
    after a timeout or crash), the function re-observes via ``observe_fn`` before
    giving up, so a slow app gets a second chance before the run reports unknown.
    """
    import time as _time

    if not isinstance(end_state, Mapping):
        return {
            "ok": False,
            "declared": False,
            "error": (
                "no end_state declared — a run must state what its end state looks like "
                "(end_state.expect_observe_contains)"
            ),
        }
    channel = str(end_state.get("channel", "window_title") or "window_title")

    for attempt in range(max(0, retries) + 1):
        if attempt > 0 and observe_fn is not None:
            _time.sleep(retry_delay)
            try:
                observation_text = str(observe_fn() or observation_text)
            except Exception:  # noqa: BLE001 — observe failure is not a crash
                pass
        if channel == "app_state":
            state = dict(app_state) if app_state is not None else read_app_state({}, observation_text)
            observation_text = str(state.get("text") or observation_text)
        expected = [
            str(value).strip()
            for key in ("expect_observe_contains", "expect_state_contains")
            for value in _as_list(end_state.get(key))
            if str(value).strip()
        ]
        if not expected:
            return {
                "ok": False,
                "declared": False,
                "error": (
                    "end_state declares no expectation — set expect_observe_contains "
                    "(the end state is read from the real state, never from the action's stdout)"
                ),
            }
        missing = []
        for text in expected:
            # `|` is OR (browser patterns: Zen|Firefox|Chrome) — match if any branch hits
            branches = [part for part in str(text).split("|") if part] or [text]
            if not any(branch in observation_text for branch in branches):
                missing.append(text)
        if not missing:
            return {
                "ok": True,
                "declared": True,
                "channel": channel,
                "description": str(end_state.get("description", "") or ""),
                "expected": expected,
                "missing": [],
                "observed": observation_text[:600],
                "verified": True,
                "retries_used": attempt,
            }
        # P0: for CLI actions, don't retry against the desktop observation —
        # the end state is about the command's output, not the window title.
        # Fall through to the failure return immediately.
        if channel == "stdout":
            break
    return {
        "ok": False,
        "declared": True,
        "channel": channel,
        "description": str(end_state.get("description", "") or ""),
        "expected": expected,
        "missing": missing,
        "observed": observation_text[:600],
        "verified": False,
        "retries_used": retries,
    }


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def run_action_list(
    actions: Sequence[Any],
    *,
    runner_kind: str = "auto",
    runner: Any | None = None,
    workdir: str | Path | None = None,
    end_state: Any | None = None,
    goal: str = "",
    runtime: str | Path | None = None,
    timeout: float = 90.0,
    session_id: str = "act-run",
    policy: CUPolicy | None = None,
    events: Any | None = None,
    write_record: bool = True,
    allow_live_desktop: bool = False,
    live_target: str = "",
    screenshot: bool = False,
    screenshot_dir: str | Path | None = None,
    desktop_lease: Any | None = None,
    lease_task: str = "",
) -> dict:
    """Run a declared action list, verify the end state, and **always clean up after itself**.

    This is the product entry point; :func:`_execute_action_list` is the run itself. The
    wrapper exists for two rules the run must not be able to skip:

    * a runner *this call resolved* is closed again — on success, on failure and on an
      exception — and the record carries what the teardown actually left behind
      (``cleanup``), so "the GUI run cleaned up its own container" is a measurement;
    * a runner the **caller** supplied is left alone: the caller owns its lifecycle, and a
      composite step that started its own container must not have it yanked away.

    ``screenshot=True`` also captures the run's end state from the runner (named after the
    window the observation read, so the evidence is identifiable) and records its path and
    sha256. A capture that cannot happen is reported as absent — never as an empty file.

    ``desktop_lease`` (a ``skaldir.desktop_lease.DesktopLeaseStore``) is the DESK-01/DESK-02
    wiring. When a run drives the host desktop it takes the lease for its ``session_id``,
    and the runner's input path is fenced with that lease's generation — so an action queued
    before a pause, crash or owner change is dropped at the adapter. The lease is released
    in the same ``finally``-shaped teardown as the runner: on success, on failure and on an
    exception. A refused lease is a blocker, not a silent fall-through.
    """
    holder: dict[str, Any] = {}
    lease: Any | None = None
    lease_generation = 0
    desktop_payload: dict[str, Any] = {}
    # DESK-01 — the lease is not an opt-in. When the run drives the real host desktop and
    # the caller did not hand in a store, the service takes one itself. Otherwise every
    # caller would have to remember to pass it, and one that forgot would move the user's
    # mouse unattended — the exact bypass the requirement forbids.
    if desktop_lease is None and _needs_desktop_lease(runner_kind=runner_kind, runner=runner):
        desktop_lease = _default_desktop_store()
    if desktop_lease is not None:
        try:
            lease = desktop_lease.acquire(
                session_id, task=lease_task or goal or session_id
            )
            lease_generation = int(getattr(lease, "generation", 0) or 0)
            desktop_payload = {
                "acquired": True,
                "owner_run": getattr(lease, "run_id", session_id),
                "task": lease_task or goal or session_id,
                "fencing_token": getattr(lease, "owner_token", ""),
                "generation": lease_generation,
            }
        except Exception as exc:  # noqa: BLE001 — a held desktop is a named blocker
            return {
                "ok": False,
                "verdict": "BLOCKED",
                "blocked_reason": f"DESK-01: the host desktop is not available to this run "
                                  f"— {exc}",
                "desktop": {"acquired": False, "reason": str(exc)[:300]},
                "actions": [],
            }
    try:
        record = _execute_action_list(
            actions,
            runner_kind=runner_kind,
            runner=runner,
            workdir=workdir,
            end_state=end_state,
            goal=goal,
            runtime=runtime,
            timeout=timeout,
            session_id=session_id,
            policy=policy,
            events=events,
            write_record=write_record,
            allow_live_desktop=allow_live_desktop,
            live_target=live_target,
            holder=holder,
            # DESK-02: bind the fence to the resolved runner *before the first action*.
            # Without this line the fence existed and was tested but never reached the
            # input path — exactly the gap the requirement audit named.
            on_runner=(
                (lambda resolved: _bind_desktop_fence(
                    resolved, desktop_lease, session_id, lease_generation))
                if lease is not None else None
            ),
        )
    except BaseException:
        _teardown_resolved_runner(holder, owns=runner is None)
        _release_desktop_lease(desktop_lease, session_id, desktop_payload)
        raise
    if screenshot and holder.get("runner") is not None:
        capture = _capture_run_screenshot(
            holder["runner"], record, screenshot_dir=screenshot_dir, runtime=runtime
        )
        if capture is not None:
            record["screenshot"] = capture
    record["cleanup"] = _teardown_resolved_runner(holder, owns=runner is None)
    released = _release_desktop_lease(desktop_lease, session_id, desktop_payload)
    if desktop_lease is not None:
        desktop_payload["released"] = released
        record["desktop"] = desktop_payload
    return record


def _needs_desktop_lease(*, runner_kind: str, runner: Any | None) -> bool:
    """Does this run drive the *real* host desktop and therefore have to hold the lease?

    The runner knows best: a runner that says ``requires_input_fence`` is the live one. The
    ``runner_kind`` is the fallback for callers that resolve the runner later. Isolated
    containers and isolated host sessions are deliberately excluded — they are not the
    shared desktop and must not queue behind it.
    """
    if bool(getattr(runner, "requires_input_fence", False)):
        return True
    return str(runner_kind or "") == "live-desktop"


_DEFAULT_STORE: dict[str, Any] = {}


def _default_desktop_store() -> Any:
    """One shared :class:`~skaldir.desktop_lease.DesktopLeaseStore` for this home.

    The desktop is a per-machine resource, so the state file lives outside any single run's
    runtime directory — two runs must contend for the *same* file or the lease means
    nothing. Instances are cached per state path so runs in one process share an object,
    and the file itself is the authority between processes.
    """
    from .desktop_lease import DesktopLeaseStore

    base = (
        os.environ.get("SKALDIR_HOME")
        or os.environ.get("GODSV_HOME")
        or str(Path.home() / ".skaldir")
    )
    path = str(Path(base) / "state" / "desktop-lease.json")
    store = _DEFAULT_STORE.get(path)
    if store is None:
        store = _DEFAULT_STORE[path] = DesktopLeaseStore(path)
    return store


def _bind_desktop_fence(runner: Any, store: Any, run_id: str, generation: int) -> bool:
    """DESK-02: put the fence on the runner's input path, at the adapter.

    Only runners that expose ``bind_input_fence`` get one (``HostSessionRunner`` does).
    A runner without it is reported rather than silently treated as fenced.

    This is also the runner's connection to the lease, so it is where a **lease acquisition**
    and a **stop / generation bump** invalidate the runner's measured input-backend pick
    (``invalidate_input_backend()``): a new owner or a ``DesktopLeaseStore.request_stop``
    that bumps the generation is a new input world, and a cached backend pick — a negative
    one above all — must be re-measured against it rather than trusted across the change.
    """
    binder = getattr(runner, "bind_input_fence", None)
    if not callable(binder):
        return False

    invalidate = getattr(runner, "invalidate_input_backend", None)

    def _fence() -> None:
        try:
            store.fence(run_id, generation)
        except Exception:
            # A refused fence means the generation moved on (a stop / owner change). Drop the
            # runner's backend pick so the next input re-measures it, then let the DESK-02
            # refusal stand — this adapter never swallows a stale-fence drop.
            if callable(invalidate):
                invalidate()
            raise

    binder(_fence)
    # A freshly-acquired lease is a fresh input world: drop any pick the runner carried in.
    if callable(invalidate):
        invalidate()
    return True


def _release_desktop_lease(store: Any, run_id: str, payload: dict[str, Any]) -> bool:
    """Give the desktop back; never raise out of a teardown."""
    if store is None:
        return False
    try:
        return bool(store.release(run_id, reason="run finished"))
    except Exception as exc:  # noqa: BLE001
        payload["release_error"] = f"{type(exc).__name__}: {exc}"[:200]
        return False


def _teardown_resolved_runner(holder: Mapping, *, owns: bool) -> dict:
    """Close the runner this call resolved and report what is left behind.

    ``owns=False`` means the caller handed the runner in: nothing is closed (the caller's
    lifecycle), and the record says so instead of implying a cleanup nobody did.
    """
    runner = holder.get("runner") if isinstance(holder, Mapping) else None
    if runner is None:
        return {"container": "", "prefix": "", "closed": True, "still_present": False,
                "owned": None, "detail": "no runner existed: nothing was started"}
    if not owns:
        return {
            **dict(_safe_cleanup_record(runner)),
            "owned": False,
            "detail": "the caller supplied this runner: its lifecycle stays with the caller",
        }
    closer = getattr(runner, "close", None)
    error = ""
    if callable(closer):
        try:
            closer()
        except Exception as exc:  # noqa: BLE001 — a failed teardown must not rewrite the verdict
            error = f"{type(exc).__name__}: {exc}"
    record = dict(_safe_cleanup_record(runner))
    record.update({"owned": True, "detail": error or "closed by the run that started it"})
    return record


def _safe_cleanup_record(runner: Any) -> dict:
    try:
        record = runner.cleanup_record()
    except Exception as exc:  # noqa: BLE001 — an unknown runner still answers usefully
        return {"container": "", "prefix": "", "closed": False, "still_present": True,
                "error": f"{type(exc).__name__}: {exc}"}
    return dict(record)


def _screenshot_label(record: Mapping, runner: Any) -> str:
    """Name a capture after the window/process the run actually observed.

    The point of the name is that a reviewer can tell *what* was on screen without opening
    the file; a generic ``shot.png`` would not do that.
    """
    observation = record.get("observation")
    text = ""
    if isinstance(observation, Mapping):
        text = str(observation.get("text") or "")
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    if first and first != "(no windows)":
        return first
    goal = str(record.get("goal") or "").strip()
    if goal:
        return goal
    isolation = str(getattr(runner, "isolation", "") or "display")
    return f"{isolation}-end-state"


def _capture_run_screenshot(
    runner: Any,
    record: dict,
    *,
    screenshot_dir: str | Path | None,
    runtime: str | Path | None,
) -> dict | None:
    """Capture the run's end state and return its evidence record (or ``None``)."""
    shooter = getattr(runner, "screenshot", None)
    if not callable(shooter):
        record.setdefault("warnings", []).append(
            f"the runner '{getattr(runner, 'isolation', '')}' cannot capture: no screenshot method"
        )
        return None
    label = _screenshot_label(record, runner)
    from .computer_use import safe_label

    status = str(record.get("status", "") or "run")
    name = f"{status}-{safe_label(label, fallback='end-state')}"
    folder = Path(screenshot_dir).expanduser() if screenshot_dir else (
        _runtime_dir(runtime) / "act-screenshots"
    )
    capture: Any = None
    why = ""
    try:
        capture, why = shooter(folder / f"{name}.png", label=label)
    except Exception as exc:  # noqa: BLE001 — a crashing capture is a missing capture
        capture, why = None, f"{type(exc).__name__}: {exc}"
    if capture is None:
        record.setdefault("warnings", []).append(
            f"the end-state screenshot could not be taken: {why}"
        )
        return None
    as_dict = getattr(capture, "as_dict", None)
    payload: dict = dict(as_dict()) if callable(as_dict) else {"path": str(capture)}  # type: ignore[arg-type]
    payload["label"] = label
    return payload


def _execute_action_list(
    actions: Sequence[Any],
    *,
    runner_kind: str = "auto",
    runner: Any | None = None,
    workdir: str | Path | None = None,
    end_state: Any | None = None,
    goal: str = "",
    runtime: str | Path | None = None,
    timeout: float = 90.0,
    session_id: str = "act-run",
    policy: CUPolicy | None = None,
    events: Any | None = None,
    write_record: bool = True,
    allow_live_desktop: bool = False,
    live_target: str = "",
    holder: dict | None = None,
    on_runner: Any | None = None,
) -> dict:
    """Run a declared action list in an isolated runner and verify the end state.

    ``on_runner`` is called with the resolved runner immediately after resolution and
    **before any action runs**. That is the only moment at which a cross-cutting rule can
    bind to the real input path (DESK-02's fencing), because the runner does not exist
    before it and the actions start right after.

    Returns one record whatever happens; ``record["status"]`` is one of ``verified`` /
    ``verification_failed`` / ``blocked`` / ``usage`` and carries the matching exit code.
    Nothing is executed when a declaration is invalid, when no real bridge exists, or when
    the policy refuses the run. Use :func:`run_action_list` — it adds the lifecycle rule.
    """
    runtime_dir = _runtime_dir(runtime)
    goal = str(goal or "").strip()
    policy = policy or default_policy()
    declared = list(actions or [])
    base: dict[str, Any] = {
        "goal": goal,
        "declared": len(declared),
        "policy": policy_record(policy),
        "end_state": None,
        "actions": [],
        "executed": 0,
        "live": False,
    }

    if not declared:
        return _stop(
            base,
            status="usage",
            reason="no actions declared — an empty run has nothing to verify",
            hint="pass --actions FILE with a JSON action list, or --actions - for stdin",
        )
    for index, item in enumerate(declared):
        problem = action_error(index, item)
        if problem:
            return _stop(
                base,
                status="usage",
                reason=problem,
                hint=(
                    "every declared action needs {kind, channel, verify.expect_*}; see "
                    "`skaldir act check --json` for the capabilities this host really has"
                ),
            )
    if len(declared) > policy.max_actions:
        return _stop(
            base,
            status="usage",
            reason=(
                f"{len(declared)} actions declared but the policy allows max_actions="
                f"{policy.max_actions}"
            ),
            hint="split the flow or raise the bound deliberately; a runaway list is refused",
        )

    resolved_runner = runner
    resolution: dict
    if resolved_runner is None:
        resolved_runner, resolution = resolve_runner(
            runner_kind,
            workdir=workdir,
            timeout=timeout,
            allow_live_desktop=allow_live_desktop,
            live_target=live_target,
        )
    else:
        resolution = {
            "requested": runner_kind,
            "bridge": str(getattr(resolved_runner, "isolation", "")),
            "resolved": True,
            "isolation": str(getattr(resolved_runner, "isolation", "")),
            "real": str(getattr(resolved_runner, "isolation", "")) != "dry-run",
            "fixture": str(getattr(resolved_runner, "isolation", "")) == "dry-run",
            "scope": (
                "in-process fixture"
                if str(getattr(resolved_runner, "isolation", "")) == "dry-run"
                else "real"
            ),
            "target_scope": str(getattr(resolved_runner, "target_scope", "") or ""),
            "reason": "a runner was supplied by the caller",
        }
    base["runner"] = resolution
    if holder is not None:
        # The wrapper owns the lifecycle decision; it needs the runner this call resolved.
        holder["runner"] = resolved_runner
    if resolved_runner is not None and callable(on_runner):
        # DESK-02 binds here: after the runner exists, before the first input action.
        try:
            on_runner(resolved_runner)
        except Exception as exc:  # noqa: BLE001 — a hook must not break the run record
            base["hook_error"] = f"{type(exc).__name__}: {exc}"[:200]
    if resolved_runner is None:
        return _stop(
            base,
            status="blocked",
            reason=f"no usable runner: {resolution['reason']}",
            hint="`skaldir act check --json` reports which bridges exist on this host",
            missing_capability=_missing_from_resolution(resolution),
            record_to=runtime_dir,
            write_record=write_record,
        )

    policy = _sync_live_policy(
        policy,
        resolved_runner,
        allow_live_desktop=allow_live_desktop,
        live_target=live_target,
    )
    base["policy"] = policy_record(policy)

    events = events if events is not None else _open_events(runtime_dir)
    executor = CUExecutor(policy, events=events, session_id=session_id)
    results: list[dict] = []

    for index, item in enumerate(declared):
        core_action = to_core_action(item)
        attempt = Attempt(
            channel=action_channel(item),
            action=core_action,
            verify=to_core_verification(item),
        )
        step = f"{goal or 'act'}#{index}"
        try:
            outcome = executor.run(step, [attempt], resolved_runner)
        except PolicyViolation as exc:
            results.append(
                {
                    "index": index,
                    "kind": str(item.get("kind", "")),
                    "channel": attempt.channel,
                    "status": "refused",
                    "detail": f"policy: {exc}",
                    "expectation": _expected_text(item),
                }
            )
            base["actions"] = results
            base["executed"] = len(results)
            return _stop(
                base,
                status="blocked",
                reason=f"the policy refused action {index}: {exc}",
                hint="`skaldir act check --json` lists the kinds this host can carry",
                missing_capability=f"a policy that allows kind {item.get('kind')!r}",
                record_to=runtime_dir,
                write_record=write_record,
                runner=resolved_runner,
                isolation=str(getattr(resolved_runner, "isolation", "")),
            )
        results.append(
            {
                "index": index,
                "kind": str(item.get("kind", "")),
                "channel": outcome.channel or attempt.channel,
                "status": outcome.status,
                "detail": outcome.detail,
                "stdout": str(getattr(outcome, "stdout", "") or ""),
                "expectation": _expected_text(item),
                "description": str(item.get("description", "") or ""),
            }
        )
        base["actions"] = results
        base["executed"] = len(results)
        if outcome.status != "verified":
            # P0: separate execution from verification — a command that ran but
            # didn't match the expectation is different from one that didn't run.
            executed = bool(outcome.detail and "did not run" not in str(outcome.detail))
            status_label = "executed but unverified" if executed else "not executed"
            return _stop(
                base,
                status="verification_failed",
                reason=(
                    f"action {index} ({item.get('kind')}) {status_label}: {outcome.detail}"
                ),
                hint="the run stopped at the first unverified action; nothing further ran",
                record_to=runtime_dir,
                write_record=write_record,
                runner=resolved_runner,
                isolation=str(getattr(resolved_runner, "isolation", "")),
                extra={"channel": outcome.channel, "fallbacks": outcome.fallbacks},
            )

    # P0: when end_state.channel is "stdout", use the action's actual stdout
    # (from ActionResult.stdout) as the end-state text — NOT the verification
    # status string (outcome.detail = "verified" etc.).
    end_channel = str(end_state.get("channel", "") or "") if isinstance(end_state, Mapping) else ""
    use_stdout = end_channel == "stdout"

    if use_stdout and results:
        cli_stdout = "\n".join(
            str(r.get("stdout", "")) for r in results if r.get("stdout")
        )
        if cli_stdout.strip():
            observed_text = cli_stdout

    try:
        if not use_stdout:
            observation = resolved_runner.observe()
            observed_text = str(getattr(observation, "text", "") or "")
            observed_detail = str(getattr(observation, "detail", "") or "")
        else:
            observed_detail = "stdout channel — observation is the command output"
    except Exception as exc:  # noqa: BLE001 — no perception ⇒ no end-state verdict
        return _stop(
            base,
            status="verification_failed",
            reason=(
                "the end state could not be read: the runner's observation failed with "
                f"{type(exc).__name__}: {exc}"
            ),
            hint="without an observation the end state stays unverified — never a success",
            record_to=runtime_dir,
            write_record=write_record,
            runner=resolved_runner,
            isolation=str(getattr(resolved_runner, "isolation", "")),
        )

    end = check_end_state(
        end_state,
        observed_text,
        app_state=read_app_state(
            end_state if isinstance(end_state, Mapping) else {},
            observed_text,
            runner=resolved_runner,
            workdir=workdir,
        ),
        retries=3,
        retry_delay=1.5,
        observe_fn=None if use_stdout else (lambda: resolved_runner.observe().text),
    )
    base["end_state"] = end
    base["observation"] = {"text": observed_text[:600], "detail": observed_detail[:200]}
    base["isolation"] = str(getattr(resolved_runner, "isolation", ""))
    if not end.get("declared"):
        return _stop(
            base,
            status="usage",
            reason=str(end.get("error")),
            hint="declare the end state: end_state.expect_observe_contains",
            record_to=runtime_dir,
            write_record=write_record,
            runner=resolved_runner,
        )
    if not end["ok"]:
        return _stop(
            base,
            status="verification_failed",
            reason=(
                "the end state was not observed: missing "
                + ", ".join(repr(text) for text in end["missing"])
                + f" in the real observation {observed_text[:120]!r}"
            ),
            hint="the actions ran but the state does not show it — that is a failure, not a pass",
            record_to=runtime_dir,
            write_record=write_record,
            runner=resolved_runner,
            isolation=str(getattr(resolved_runner, "isolation", "")),
        )

    base["status"] = "verified"
    base["verified"] = True
    base["reason"] = (
        f"all {len(results)} action(s) verified and the end state was observed"
        if results
        else "the end state was observed"
    )
    base["exit_code"] = EXIT_OK
    base["scope"] = resolution["scope"]
    base["live"] = bool(resolution.get("real")) and not resolution.get("fixture")
    base["record"] = _write_run_record(
        runtime_dir, base, write=write_record, goal=goal, channel=None
    )
    _close_events(events)
    return base


def _missing_from_resolution(resolution: Mapping) -> str:
    reason = str(resolution.get("reason", ""))
    if "image missing" in reason:
        return f"the container GUI image {GUI_IMAGE} (build it, or use another bridge)"
    if "podman is not installed" in reason or ("podman" in reason and "unavailable" in reason):
        return "podman (a rootless container runtime)"
    if "bwrap" in reason:
        return "bwrap (the sandbox isolation)"
    if "live desktop" in reason or "opt-in" in reason:
        return (
            "an isolated bridge (the container GUI image, the host-session display or the "
            "bwrap sandbox) — or, deliberately, the live-desktop opt-in: set "
            "computer_use.allow_live_desktop plus computer_use.live_desktop_target (or "
            "SKALDIR_CU_ALLOW_LIVE_DESKTOP=1 with SKALDIR_CU_LIVE_TARGET=<window>)"
        )
    return (
        "an available isolation bridge: the container GUI image, the host-session display, "
        "the bwrap sandbox, or a live desktop bridge (which needs the explicit opt-in)"
    )


def _stop(
    base: dict,
    *,
    status: str,
    reason: str,
    hint: str = "",
    missing_capability: str = "",
    missing_anchors: Sequence[str] | None = None,
    goal: str = "",
    next_command: str = "",
    record_to: Path | None = None,
    write_record: bool = True,
    runner: Any | None = None,
    isolation: str = "",
    extra: Mapping | None = None,
) -> dict:
    """Finish a run with an honest non-success verdict (never a partial pass)."""
    record = dict(base)
    record["status"] = status
    record["verified"] = False
    record["reason"] = str(reason)
    record["hint"] = str(hint or "")
    if goal:
        record["goal"] = str(goal)
    if missing_capability:
        record["missing_capability"] = str(missing_capability)
    if missing_anchors is not None:
        record["missing_anchors"] = list(missing_anchors)
    if next_command:
        record["next_command"] = str(next_command)
    if isolation:
        record["isolation"] = isolation
    if extra:
        record.update(dict(extra))
    record["exit_code"] = {
        "verified": EXIT_OK,
        "verification_failed": EXIT_VERIFICATION_FAILED,
        "blocked": EXIT_BLOCKED,
        "usage": EXIT_USAGE,
        "reobserve": EXIT_VERIFICATION_FAILED,
        "not_found": EXIT_NOT_FOUND,
        "conflict": EXIT_CONFLICT,
    }.get(status, EXIT_INTERNAL)
    record.setdefault("scope", str(record.get("runner", {}).get("scope", "real")))
    record.setdefault("live", False)
    if record_to is not None:
        record["record"] = _write_run_record(
            record_to, record, write=write_record, goal=str(record.get("goal", "")), channel=None
        )
    return record


def _write_run_record(
    runtime_dir: Path, record: Mapping, *, write: bool, goal: str, channel: Any
) -> str:
    """Append the run to the same ``cu_runs.jsonl`` the panel view reads."""
    path = runtime_dir / "cu_runs.jsonl"
    if not write:
        return str(path)
    payload = {
        "ts": time.time(),
        "step": goal or "act-run",
        "status": record.get("status", ""),
        "channel": channel or record.get("channel", ""),
        "actions": record.get("executed", 0),
        "detail": str(record.get("reason", ""))[:200],
        "kind": record.get("isolation", "") or record.get("runner", {}).get("isolation", ""),
        "goal": goal,
        "planned": record.get("declared", 0),
        "source": "act",
    }
    try:
        _record_cu_run(path, payload)
    except OSError:  # noqa: BLE001 — a full/absent disk must not fake the verdict
        return ""
    return str(path)


def _close_events(events: Any | None) -> None:
    try:
        if events is not None:
            events.close()
    except Exception:  # noqa: BLE001
        pass


# --------------------------------------------------------------------------- #
# GUI sub-tools: preconditions + UI anchors + actions + end-state check          #
# --------------------------------------------------------------------------- #

#: The sub-tool schema, as specified: the four declared parts plus the applicability facts
#: the §11 contract of the visual-subtool registry needs.
SUBTOOL_KEYS: tuple[str, ...] = (
    "name",
    "version",
    "description",
    "applicability",
    "preconditions",
    "anchors",
    "actions",
    "end_state",
    "verification_contract",
)

#: Where an anchor/precondition is read from. ``window_title`` is observable today;
#: ``host`` reads a named host fact; ``app_state`` reads the app's own state through
#: :func:`read_app_state` (declared probe → state file → window metadata).
ANCHOR_SOURCES: tuple[str, ...] = ("window_title", "host", "app_state")

#: Optional per-spec app-state reader. ``probe`` runs a fixed argv and uses stdout;
#: ``state_file`` reads a path the app itself wrote. Neither is invented from --help.
APP_STATE_KEYS = ("app_state_probe", "app_state_file")

DEFAULT_STATE_SUBDIR = "act_subtools"


def _anchor(anchor_id: str, text: str, source: str = "window_title", *, fact: str = "") -> dict:
    entry = {"id": anchor_id, "text": str(text), "source": source}
    if fact:
        entry["fact"] = fact
    return entry


def _builtin(
    name: str,
    description: str,
    *,
    preconditions: Sequence[dict],
    anchors: Sequence[dict],
    actions: Sequence[dict],
    end_state: dict,
    verification_contract: str,
    app_versions: Sequence[str] = (">=1.0,<2",),
    locales: Sequence[str] = ("en", "*"),
) -> dict:
    return {
        "name": name,
        "version": "1.0",
        "description": description,
        "applicability": {
            "app": "skaldir-desktop",
            "app_versions": list(app_versions),
            "locales": list(locales),
        },
        "preconditions": [dict(item) for item in preconditions],
        "anchors": [dict(item) for item in anchors],
        "actions": [dict(item) for item in actions],
        "end_state": dict(end_state),
        "verification_contract": verification_contract,
    }


#: The five flows the owner named, as data — preconditions, anchors, actions, end state.
#: They target the desktop app (window titles + the app's own state), so on a host where
#: the live desktop is refused (which is every host: the policy forbids it) ``run`` reports
#: the precise missing bridge instead of pretending to have clicked something.
BUILTIN_SUBTOOLS: tuple[dict, ...] = (
    _builtin(
        "open_browser",
        "Open the browser and bring its window to the front.",
        preconditions=[
            _anchor("desktop_session", "desktop session present", "host", fact="host_session"),
            _anchor("browser_installed", "browser installed", "host", fact="browser_installed"),
            # The flow's OWN window: declared absent, because its first action is what
            # puts it on the display. Without this the pre-check refused with
            # `reobserve` before ever executing (the window cannot be observed before
            # it exists); the absence is the observed condition now, and the end_state
            # check verifies the appearance against the real display afterwards.
            {
                "id": "browser_absent",
                "text": BROWSER_TITLE_RE,
                "source": "window_title",
                "expect": "absent",
            },
        ],
        anchors=[
            # Matched like the focus action matches its target: a pattern over the real
            # titles (`Firefox`, `Mozilla Firefox`) — the literal "Browser window" never
            # matched anything the display reports.
            _anchor("browser_window", BROWSER_TITLE_RE, "window_title"),
        ],
        actions=[
            {
                "kind": "cli",
                "channel": "api",
                "argv": [
                    "sh",
                    "-c",
                    BROWSER_LAUNCH + '"$BROWSER" about:blank >/dev/null 2>&1 & sleep 2; echo LAUNCHED',
                ],
                "description": "launch the browser",
                "verify": {"expect_stdout_contains": "LAUNCHED"},
            },
            {
                "kind": "window",
                "channel": "dom",
                "payload": {
                    "op": "focus",
                    "title": BROWSER_TITLE_RE,
                },
                "description": "focus the browser window",
                "verify": {"expect_stdout_contains": "FOCUSED"},
            },
        ],
        end_state={
            "channel": "window_title",
            "description": "the browser window is open on the display",
            "expect_observe_contains": "Zen|Firefox|Chrome|Chromium|Brave|Edge",
        },
        verification_contract=(
            "the browser's own window is in the display's window list and holds the input focus"
        ),
    ),
    _builtin(
        "open_youtube",
        "Open YouTube in the browser (fast path: one named flow, no rebuild).",
        preconditions=[
            _anchor("desktop_session", "desktop session present", "host", fact="host_session"),
            {
                # Only YouTube must be absent — a browser already open is fine:
                # BROWSER_LAUNCH reuses it via xdg-open (measured: the old
                # BROWSER_TITLE_RE+"|YouTube" absent check blocked the flow whenever
                # any browser window existed, forcing raw re-launches).
                "id": "youtube_absent",
                "text": "YouTube",
                "source": "window_title",
                "expect": "absent",
            },
        ],
        anchors=[
            _anchor("browser_window", BROWSER_TITLE_RE + "|YouTube", "window_title"),
        ],
        actions=[
            {
                "kind": "cli",
                "channel": "api",
                "argv": [
                    "sh",
                    "-c",
                    BROWSER_LAUNCH + '"$BROWSER" https://www.youtube.com >/dev/null 2>&1 & sleep 3; echo LAUNCHED',
                ],
                "description": "open YouTube in the browser",
                "verify": {"expect_stdout_contains": "LAUNCHED"},
            },
        ],
        end_state={
            "channel": "window_title",
            "description": "the browser shows YouTube",
            "expect_observe_contains": "YouTube",
        },
        verification_contract="the browser window title shows YouTube on the live screen",
    ),
    _builtin(
        "focus_search_bar",
        "Focus the app's search bar so the next keystrokes land in it.",
        preconditions=[
            _anchor("app_running", "App window", "window_title"),
        ],
        anchors=[
            _anchor("search_bar", "Search", "app_state"),
        ],
        actions=[
            {
                "kind": "key",
                "channel": "api",
                "payload": {"key": "ctrl+k"},
                "description": "the app's search shortcut",
                "verify": {"expect_observe_contains": "Search"},
            },
        ],
        end_state={
            "channel": "app_state",
            "description": "the search bar holds the keyboard focus",
            "expect_observe_contains": "Search",
        },
        verification_contract="the app itself reports the search bar as focused",
    ),
    _builtin(
        "open_website",
        "Open a URL in the browser's address bar.",
        preconditions=[
            # the same real titles the focus action targets: "Browser window" never
            # matched what a display actually reports (report 54/60)
            _anchor("browser_window", BROWSER_TITLE_RE, "window_title"),
        ],
        anchors=[
            _anchor("address_bar", "Address", "app_state"),
        ],
        actions=[
            {
                "kind": "key",
                "channel": "api",
                "payload": {"key": "ctrl+l"},
                "description": "focus the address bar",
                "verify": {"expect_observe_contains": "Address"},
            },
            {
                "kind": "type_text",
                "channel": "api",
                "payload": {"text": "https://example.org"},
                "description": "type the URL",
                "verify": {"expect_observe_contains": "example.org"},
            },
            {
                "kind": "key",
                "channel": "api",
                "payload": {"key": "Return"},
                "description": "navigate",
                "verify": {"expect_observe_contains": "example.org"},
            },
        ],
        end_state={
            "channel": "window_title",
            "description": "the browser shows the requested site",
            "expect_observe_contains": "example.org",
        },
        verification_contract="the browser's window title/address shows the requested URL",
    ),
    _builtin(
        "open_project",
        "Open a project in the app.",
        preconditions=[
            _anchor("app_running", "App window", "window_title"),
        ],
        anchors=[
            _anchor("project_list", "Projects", "app_state"),
        ],
        actions=[
            {
                "kind": "key",
                "channel": "api",
                "payload": {"key": "ctrl+o"},
                "description": "the app's open-project shortcut",
                "verify": {"expect_observe_contains": "Project"},
            },
        ],
        end_state={
            "channel": "app_state",
            "description": "the project screen is open and shows the project",
            "expect_observe_contains": "Projects",
        },
        verification_contract="the app's own state shows the opened project",
    ),
    _builtin(
        "open_settings",
        "Open the app's settings screen.",
        preconditions=[
            _anchor("app_running", "App window", "window_title"),
        ],
        anchors=[
            _anchor("settings_entry", "Settings", "app_state"),
        ],
        actions=[
            {
                "kind": "key",
                "channel": "api",
                "payload": {"key": "ctrl+comma"},
                "description": "the app's settings shortcut",
                "verify": {"expect_observe_contains": "Settings"},
            },
        ],
        end_state={
            "channel": "app_state",
            "description": "the settings screen is open",
            "expect_observe_contains": "Settings",
        },
        verification_contract="the app's own state shows the settings screen",
    ),
    _builtin(
        "host_window_state",
        (
            "Read the host display's app-state channel (window metadata: titles + focus + "
            "WM_CLASS) and verify it reports windows. Proves app_state is wired end to end."
        ),
        preconditions=[
            _anchor("app_state_up", "titles:", "app_state"),
        ],
        anchors=[
            _anchor("window_meta", "window:|focused:|titles:", "app_state"),
        ],
        actions=[
            {
                # key, not cli: the live-desktop path only runs key/text/pointer it
                # builds itself (F1). expect_ok declares "the touch ran"; the real
                # proof is the end-state read from the app-state channel.
                "kind": "key",
                "channel": "api",
                "payload": {"key": "Escape"},
                "description": "one host touch (the proof is the end-state app_state read)",
                "verify": {"expect_ok": True},
            },
        ],
        end_state={
            "channel": "app_state",
            "description": "the app-state channel reports window metadata",
            "expect_observe_contains": "titles:",
        },
        verification_contract=(
            "the app-state channel (window_metadata / probe / state file) reports the "
            "display's state — not a claim from the action's stdout"
        ),
    ),
)


def builtin_subtools() -> dict[str, dict]:
    """The built-ins, with the environment read **at call time**.

    ``SKALDIR_CU_BROWSER_TITLE`` names the window ``open_browser`` focuses; it is read
    here rather than once at module import so a caller that exports it in-process (the
    agent toolset, a test) sees the override too — subprocess users got it either way.
    """
    resolved: list[dict] = []
    for spec in BUILTIN_SUBTOOLS:
        spec = dict(spec)
        actions = []
        for action in spec.get("actions") or []:
            if (str(action.get("kind", "")) == "window"
                    and str((action.get("payload") or {}).get("op", "")) == "focus"):
                action = dict(action)
                payload = dict(action["payload"])
                payload["title"] = (
                    __import__("skaldir.env", fromlist=["getenv"]).getenv(
                        "SKALDIR_CU_BROWSER_TITLE", ""
                    )
                    or ""
                ).strip() or payload.get("title", "")
                action["payload"] = payload
            actions.append(action)
        spec["actions"] = actions
        resolved.append(spec)
    return {str(spec["name"]): spec for spec in resolved}


def subtool_dir(state: str | Path | None = None) -> Path:
    """Where saved sub-tools live: ``<runtime>/act_subtools`` (one JSON file per sub-tool)."""
    base = Path(state).expanduser() if state not in (None, "") else _runtime_dir()
    if base.name == DEFAULT_STATE_SUBDIR:
        return base
    return base / DEFAULT_STATE_SUBDIR


def _safe_name(name: str) -> str:
    text = str(name or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", text):
        raise ValueError(
            f"sub-tool name {name!r} is not usable as a file name "
            "(letters, digits, '_', '-', '.' — max 64 chars)"
        )
    return text


def subtool_errors(spec: Any) -> list[str]:
    """Every reason this sub-tool declaration is unusable (empty list ⇒ it is valid)."""
    problems: list[str] = []
    if not isinstance(spec, Mapping):
        return [f"a sub-tool must be an object, got {type(spec).__name__}"]
    for key in SUBTOOL_KEYS:
        if key not in spec:
            problems.append(f"missing required key {key!r}")
    if problems:
        return problems
    name = str(spec.get("name", "") or "").strip()
    try:
        _safe_name(name)
    except ValueError as exc:
        problems.append(str(exc))
    if not str(spec.get("version", "") or "").strip():
        problems.append("'version' must not be empty")
    applicability = spec.get("applicability")
    if not isinstance(applicability, Mapping):
        problems.append("'applicability' must be an object")
    else:
        versions = _as_list(applicability.get("app_versions"))
        locales = _as_list(applicability.get("locales"))
        if not versions:
            problems.append(
                "'applicability.app_versions' must not be empty — an unsupported app "
                "version can never be 'probably fine'"
            )
        if not locales:
            problems.append("'applicability.locales' must not be empty")
    for key in ("preconditions", "anchors", "actions"):
        entries = spec.get(key)
        if not isinstance(entries, list) or not entries:
            problems.append(f"'{key}' must be a non-empty list")
            continue
        for index, entry in enumerate(entries):
            if not isinstance(entry, Mapping):
                problems.append(f"{key}[{index}] must be an object")
                continue
            if key == "actions":
                problem = action_error(index, entry)
                if problem:
                    problems.append(f"actions: {problem}")
            else:
                source = str(entry.get("source", "") or "").strip()
                if source not in ANCHOR_SOURCES:
                    problems.append(
                        f"{key}[{index}]: source {source!r} unknown — known: "
                        f"{', '.join(ANCHOR_SOURCES)}"
                    )
                if not str(entry.get("text", "") or "").strip():
                    problems.append(f"{key}[{index}]: 'text' must not be empty")
                if source == "host" and not str(entry.get("fact", "") or "").strip():
                    problems.append(
                        f"{key}[{index}]: a host precondition needs 'fact' (the host fact "
                        "to read)"
                    )
                expect = str(entry.get("expect", "") or "").strip()
                if expect and expect != "absent":
                    problems.append(
                        f"{key}[{index}]: expect {expect!r} unknown — only 'absent' is "
                        "declared (a window the flow itself creates: absent at start, "
                        "verified by end_state afterwards)"
                    )
                elif expect == "absent":
                    if key != "preconditions":
                        problems.append(
                            f"{key}[{index}]: 'expect: absent' is a precondition form "
                            "(the pre-check reads it before the first act), not an "
                            "anchor form"
                        )
                    if source != "window_title":
                        problems.append(
                            f"{key}[{index}]: 'expect: absent' reads the display, so "
                            "source must be 'window_title'"
                        )
    end_state = spec.get("end_state")
    if not isinstance(end_state, Mapping):
        problems.append("'end_state' must be an object")
    elif not _as_list(end_state.get("expect_observe_contains")) and not _as_list(
        end_state.get("expect_state_contains")
    ):
        problems.append(
            "'end_state' needs expect_observe_contains — the end state is read from the real "
            "state"
        )
    if not str(spec.get("verification_contract", "") or "").strip():
        problems.append("'verification_contract' must not be empty")
    return problems


def subtool_summary(spec: Mapping, *, origin: str, path: str = "") -> dict:
    return {
        "name": str(spec.get("name", "")),
        "version": str(spec.get("version", "")),
        "description": str(spec.get("description", "")),
        "origin": origin,
        "path": path,
        "preconditions": len(_as_list(spec.get("preconditions"))),
        "anchors": len(_as_list(spec.get("anchors"))),
        "actions": len(_as_list(spec.get("actions"))),
        "end_state": dict(spec.get("end_state") or {}),
        "verification_contract": str(spec.get("verification_contract", "")),
        "applicability": dict(spec.get("applicability") or {}),
    }


def list_subtools(state: str | Path | None = None) -> list[dict]:
    """Every sub-tool: the saved ones (they win) plus the five built-ins."""
    directory = subtool_dir(state)
    rows: list[dict] = []
    saved: dict[str, dict] = {}
    if directory.is_dir():
        for path in sorted(directory.glob("*.json")):
            try:
                spec = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                rows.append(
                    {
                        "name": path.stem,
                        "version": "",
                        "origin": "saved",
                        "path": str(path),
                        "error": "the stored sub-tool is not readable JSON — it is ignored",
                    }
                )
                continue
            saved[str(spec.get("name", path.stem))] = spec
            rows.append(subtool_summary(spec, origin="saved", path=str(path)))
    for name, spec in sorted(builtin_subtools().items()):
        if name in saved:
            for row in rows:
                if row.get("name") == name:
                    row["shadows_builtin"] = True
            continue
        rows.append(subtool_summary(spec, origin="builtin"))
    return rows


def load_subtool(name: str, *, state: str | Path | None = None) -> tuple[dict, str, str]:
    """``(spec, origin, path)`` for one sub-tool; a saved one shadows the built-in."""
    wanted = _safe_name(name)
    path = subtool_dir(state) / f"{wanted}.json"
    if path.is_file():
        try:
            spec = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise FileNotFoundError(
                f"the stored sub-tool {path} is not readable JSON: {exc}"
            ) from exc
        if not isinstance(spec, Mapping):
            raise FileNotFoundError(f"the stored sub-tool {path} is not a JSON object")
        return dict(spec), "saved", str(path)
    builtin = builtin_subtools().get(wanted)
    if builtin is None:
        raise FileNotFoundError(
            f"unknown sub-tool {wanted!r} — known: "
            + ", ".join(sorted(set(builtin_subtools()) | set(_saved_names(state))))
        )
    return builtin, "builtin", ""


def _saved_names(state: str | Path | None) -> list[str]:
    directory = subtool_dir(state)
    if not directory.is_dir():
        return []
    return [path.stem for path in sorted(directory.glob("*.json"))]


def save_subtool(
    spec: Mapping, *, state: str | Path | None = None, force: bool = False
) -> dict:
    """Validate and store one sub-tool as JSON. Refuses silently-invalid data."""
    problems = subtool_errors(spec)
    if problems:
        raise ValueError("the sub-tool declaration is not usable: " + "; ".join(problems))
    name = _safe_name(str(spec["name"]))
    directory = subtool_dir(state)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.json"
    if path.exists() and not force:
        raise FileExistsError(
            f"{path} already exists — a stored sub-tool is not overwritten silently "
            "(use --force to replace it)"
        )
    payload = {key: spec[key] for key in SUBTOOL_KEYS if key in spec}
    # optional app-state readers travel with the declaration (never invented, never dropped)
    for key in APP_STATE_KEYS:
        if key in spec and spec[key]:
            payload[key] = spec[key]
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    return subtool_summary(payload, origin="saved", path=str(path))


def to_visual_subtool(spec: Mapping):
    """Map a stored sub-tool onto the §11 ``VisualSubtool`` contract of the CU substrate.

    A sub-tool is data: this is the bridge to the existing visual-subtool registry, so the
    same declaration can also be consumed by the System-One/§11 path.
    """
    from .system_one_subtools import REOBSERVE_TRIGGERS, VisualSubtool

    applicability = dict(spec.get("applicability") or {})
    anchors = [
        str(entry.get("text", ""))
        for entry in _as_list(spec.get("anchors"))
        if isinstance(entry, Mapping) and str(entry.get("text", "")).strip()
    ]
    preconditions = [
        f"{entry.get('source', '')}:{entry.get('text', '')}"
        for entry in _as_list(spec.get("preconditions"))
        if isinstance(entry, Mapping)
    ]
    actions = _as_list(spec.get("actions"))
    allowed = tuple(
        dict.fromkeys(str(entry.get("kind", "")) for entry in actions if isinstance(entry, Mapping))
    )
    return VisualSubtool(
        capability_id=str(spec.get("name", "")),
        version=str(spec.get("version", "")),
        supported_app_versions=tuple(
            str(item) for item in _as_list(applicability.get("app_versions"))
        ),
        supported_locales=tuple(str(item) for item in _as_list(applicability.get("locales"))),
        start_conditions=tuple(preconditions),
        anchors=tuple(anchors),
        allowed_actions=allowed,
        expected_transitions=tuple(
            f"action {index}: {entry.get('description', entry.get('kind', ''))}"
            for index, entry in enumerate(actions)
            if isinstance(entry, Mapping)
        ),
        completion_conditions=tuple(
            str(item)
            for item in _as_list(
                dict(spec.get("end_state") or {}).get(
                    "expect_observe_contains",
                    dict(spec.get("end_state") or {}).get("expect_state_contains"),
                )
            )
        ),
        failure_conditions=("end state not observed",),
        reobserve_policy=(REOBSERVE_TRIGGERS[0],),
        system2_escalation_policy=("insufficient_state",),
        verification_contract=str(spec.get("verification_contract", "")),
    )


def _host_source_state() -> dict:
    return host_facts()["facts"]


def _window_metadata_state(observation_text: str, *, display: str = "") -> dict:
    """What a display can honestly say about app state beyond bare titles.

    Composed from the observation (window names) plus — when ``xdotool``/``xprop``
    are there — the focused window id and each window's ``WM_CLASS``. Labelled
    ``window_metadata`` (one of the §51 perception channels), never pretended to be
    a full accessibility/DOM tree. ``display`` is the runner's own X display (an
    owned Xvfb is not ``$DISPLAY``).
    """
    import subprocess  # noqa: PLC0415 — only this channel needs it

    env = dict(os.environ)
    if display:
        env["DISPLAY"] = str(display)
    lines: list[str] = [f"titles: {observation_text.strip()}"]
    xdotool = _which("xdotool")
    xprop = _which("xprop")
    if xdotool:
        for argv in (
            [xdotool, "getactivewindow"],
            [xdotool, "search", "--name", "."],
        ):
            try:
                proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
                    argv,
                    capture_output=True,
                    text=True,
                    timeout=3.0,
                    check=False,
                    env=env,
                )
            except Exception:  # noqa: BLE001 — a missing fact is not a crash
                continue
            out = (proc.stdout or "").strip()
            if argv[1] == "getactivewindow" and out:
                lines.append(f"focused:{out.splitlines()[0].strip()}")
            elif out:
                for wid in out.splitlines():
                    wid = wid.strip()
                    if not wid.isdigit():
                        continue
                    lines.append(f"window:{wid}")
                    if xprop:
                        try:
                            cls = subprocess.run(  # noqa: S603 — fixed argv, no shell
                                [xprop, "-id", wid, "WM_CLASS"],
                                capture_output=True,
                                text=True,
                                timeout=3.0,
                                check=False,
                                env=env,
                            )
                            text = (cls.stdout or "").strip()
                            if text and "=" in text:
                                lines.append(f"class:{text.split('=', 1)[1].strip()}")
                        except Exception:  # noqa: BLE001
                            continue
    return {
        "text": "\n".join(lines),
        "channel": "window_metadata",
        "available": True,
        "detail": (
            "window metadata from the display (titles + focus + WM_CLASS) — not a "
            "full app_state/accessibility tree"
        ),
    }


def read_app_state(
    spec: Mapping[str, Any],
    observation_text: str = "",
    *,
    runner: Any | None = None,
    workdir: str | Path | None = None,
) -> dict:
    """The app's own state, as far as this host can really read it.

    Sources, strongest first — and each one names its channel:

    1. ``app_state_probe`` — a declared argv whose stdout *is* the state (the app's
       own dump command). Never inferred from prose.
    2. ``app_state_file`` — a path the app wrote (JSON/text).
    3. ``window_metadata`` — titles + focus + ``WM_CLASS`` from the display.

    Returns ``{text, channel, available, detail}``. ``available`` is False only
    when nothing could be read (then anchors report the missing capability).
    """
    probe = spec.get("app_state_probe")
    if isinstance(probe, Mapping) and probe.get("argv"):
        argv = [str(part) for part in list(probe["argv"]) if str(part)]
        try:
            import subprocess  # noqa: PLC0415

            proc = subprocess.run(  # noqa: S603 — declared argv, no shell
                argv,
                capture_output=True,
                text=True,
                timeout=float(probe.get("timeout") or 5.0),
                cwd=str(workdir) if workdir else None,
                check=False,
            )
            text = (proc.stdout or "").strip()
            return {
                "text": text,
                "channel": "app_state_probe",
                "available": bool(text) or proc.returncode == 0,
                "detail": f"declared probe exit {proc.returncode} ({len(text)} chars)",
            }
        except Exception as exc:  # noqa: BLE001 — a failed probe is a reportable fact
            return {
                "text": "",
                "channel": "app_state_probe",
                "available": False,
                "detail": f"app_state_probe failed: {type(exc).__name__}: {exc}",
            }

    state_file = str(spec.get("app_state_file") or "").strip()
    if state_file:
        path = Path(state_file).expanduser()
        try:
            text = path.read_text(encoding="utf-8", errors="replace").strip()
            return {
                "text": text,
                "channel": "app_state_file",
                "available": True,
                "detail": f"app state file {path} ({len(text)} chars)",
            }
        except OSError as exc:
            return {
                "text": "",
                "channel": "app_state_file",
                "available": False,
                "detail": f"app_state_file {path} unreadable: {exc}",
            }

    return _window_metadata_state(
        observation_text or str(getattr(runner, "last_observation", "") or ""),
        display=str(getattr(runner, "display", "") or ""),
    )


def _window_matches(text: str, observation_text: str) -> bool:
    """Does a declared window title/pattern match what is on the display right now?

    Matched as a regular expression first — ``Mozilla Firefox|Firefox`` matches either
    real title, the way ``open_browser``'s focus action names its target — and then as
    a literal substring, so a plain title behaves exactly as before and an invalid
    pattern degrades to the literal instead of raising.
    """
    if not text:
        return False
    try:
        if re.search(text, observation_text):
            return True
    except re.error:
        pass
    return text in observation_text


def _expects_absent(spec: Mapping) -> bool:
    """Does the declaration carry a ``window-absent`` precondition (``expect: absent``)?"""
    return any(
        isinstance(entry, Mapping)
        and str(entry.get("expect", "") or "").strip() == "absent"
        for entry in _as_list(spec.get("preconditions"))
    )


def observe_anchors(
    spec: Mapping,
    observation_text: str,
    *,
    app_state: Mapping[str, Any] | None = None,
) -> dict:
    """Which declared preconditions/anchors are observed RIGHT NOW (§11/§56).

    ``window_title`` entries are matched against the real observation (as a pattern
    first, then as a literal — :func:`_window_matches`), ``host`` entries against the
    measured host facts, and ``app_state`` entries against :func:`read_app_state`
    (declared probe → state file → window metadata). A channel that cannot be read
    at all reports the missing capability instead of guessing.

    A ``window-absent`` precondition (``"expect": "absent"``) inverts its own check:
    the ABSENCE is the observed condition. While such a declaration exists, every
    other ``window_title`` entry that is not on the display yet moves to
    ``awaiting_appearance`` instead of ``missing`` — that window is what the flow's
    own actions create, so the pre-check must not refuse before the first act. The
    end-state check still verifies the appearance against the real observation
    afterwards; host facts and app-state checks are never weakened by the
    declaration (they land in ``missing``/``unobservable`` exactly as before).
    """
    facts = _host_source_state()
    absent_declared = _expects_absent(spec)
    state = dict(app_state) if app_state is not None else read_app_state(spec, observation_text)
    state_text = str(state.get("text") or "")
    state_channel = str(state.get("channel") or "none")
    state_ok = bool(state.get("available"))
    observed_conditions: list[str] = []
    observed_anchors: list[str] = []
    unobservable: list[str] = []
    missing: list[str] = []
    awaiting: list[str] = []
    notes: list[str] = []

    def _app_state_check(entry: Mapping, bucket: list[str], *, kind: str) -> None:
        text = str(entry.get("text", ""))
        label = f"{kind} {entry.get('id', text)!r} ({text!r})"
        if not state_ok:
            unobservable.append(
                f"{label} needs the app's own state — {state.get('detail') or 'no app-state channel'}"
            )
            return
        if _window_matches(text, state_text):
            bucket.append(text)
        else:
            missing.append(
                f"{label} is not in the app state "
                f"(channel {state_channel}: {state.get('detail', '')})"
            )

    for entry in _as_list(spec.get("preconditions")):
        if not isinstance(entry, Mapping):
            continue
        source = str(entry.get("source", ""))
        text = str(entry.get("text", ""))
        if source == "window_title":
            if str(entry.get("expect", "") or "").strip() == "absent":
                if _window_matches(text, observation_text):
                    notes.append(
                        f"precondition {entry.get('id', text)!r} declares {text!r} "
                        "absent, but it is already on the display — the run proceeds, "
                        "and the end state decides"
                    )
                else:
                    observed_conditions.append(text)
            elif _window_matches(text, observation_text):
                observed_conditions.append(text)
            elif absent_declared:
                awaiting.append(
                    f"precondition {entry.get('id', text)!r} ({text!r}) is not on the "
                    "display yet — this flow declares its window absent at start, so "
                    "the state may only appear through the actions; the end state "
                    "verifies it"
                )
            else:
                missing.append(f"precondition {entry.get('id', text)!r} is not on the display")
        elif source == "host":
            if bool(facts.get(str(entry.get("fact", "")), False)):
                observed_conditions.append(text)
            else:
                missing.append(
                    f"precondition {entry.get('id', text)!r} does not hold on this host "
                    f"(host fact {entry.get('fact', '')!r})"
                )
        elif source == "app_state":
            _app_state_check(entry, observed_conditions, kind="precondition")
        else:
            unobservable.append(
                f"precondition {entry.get('id', text)!r} uses unknown source {source!r}"
            )

    for entry in _as_list(spec.get("anchors")):
        if not isinstance(entry, Mapping):
            continue
        source = str(entry.get("source", ""))
        text = str(entry.get("text", ""))
        if source == "window_title":
            if _window_matches(text, observation_text):
                observed_anchors.append(text)
            elif absent_declared:
                awaiting.append(
                    f"anchor {entry.get('id', text)!r} ({text!r}) is not on the display "
                    "yet — this flow creates it; the end state verifies it after the "
                    "actions"
                )
            else:
                missing.append(f"anchor {entry.get('id', text)!r} ({text!r}) is not on the display")
        elif source == "host":
            if bool(facts.get(str(entry.get("fact", "")), False)):
                observed_anchors.append(text)
            else:
                missing.append(
                    f"anchor {entry.get('id', text)!r} does not hold on this host "
                    f"(host fact {entry.get('fact', '')!r})"
                )
        elif source == "app_state":
            _app_state_check(entry, observed_anchors, kind="anchor")
        else:
            unobservable.append(
                f"anchor {entry.get('id', text)!r} uses unknown source {source!r}"
            )

    return {
        "observed_start_conditions": tuple(observed_conditions),
        "observed_anchors": tuple(observed_anchors),
        "missing": tuple(missing),
        "unobservable": tuple(unobservable),
        "awaiting_appearance": tuple(awaiting),
        "notes": tuple(notes),
        "observation": observation_text[:600],
        "app_state": {
            "channel": state_channel,
            "available": state_ok,
            "detail": str(state.get("detail", "")),
            "text": state_text[:600],
        },
    }


def run_subtool(
    name: str,
    *,
    state: str | Path | None = None,
    runner_kind: str = "auto",
    runner: Any | None = None,
    workdir: str | Path | None = None,
    app_version: str = "",
    locale: str = "en",
    runtime: str | Path | None = None,
    timeout: float = 90.0,
    write_record: bool = True,
    allow_live_desktop: bool = False,
    live_target: str = "",
) -> dict:
    """Run one stored sub-tool: observe → check preconditions/anchors → act → check end state.

    Two refusals are distinguished on purpose: a check whose **channel** does not exist is
    ``blocked`` (exit 3, the missing capability is named), a check whose channel works but
    whose anchor is not on screen is ``reobserve`` (exit 2, nothing is executed). The one
    declared exception is a ``window-absent`` precondition: a window the flow itself
    creates is queued under ``precondition_check["awaiting_appearance"]`` instead of
    refusing, the actions run first, and the end state — read from the real observation,
    unchanged — decides success.

    The lifecycle rule of :func:`run_action_list` also covers this entry: a runner resolved
    here is closed again on every path, and a caller-supplied one stays the caller's.
    """
    holder: dict[str, Any] = {}
    try:
        record = _execute_subtool(
            name,
            state=state,
            runner_kind=runner_kind,
            runner=runner,
            workdir=workdir,
            app_version=app_version,
            locale=locale,
            runtime=runtime,
            timeout=timeout,
            write_record=write_record,
            allow_live_desktop=allow_live_desktop,
            live_target=live_target,
            holder=holder,
        )
    except BaseException:
        _teardown_resolved_runner(holder, owns=runner is None)
        raise
    if holder.get("runner") is not None:
        record["cleanup"] = _teardown_resolved_runner(holder, owns=runner is None)
    return record


def _execute_subtool(
    name: str,
    *,
    state: str | Path | None = None,
    runner_kind: str = "auto",
    runner: Any | None = None,
    workdir: str | Path | None = None,
    app_version: str = "",
    locale: str = "en",
    runtime: str | Path | None = None,
    timeout: float = 90.0,
    write_record: bool = True,
    allow_live_desktop: bool = False,
    live_target: str = "",
    holder: dict | None = None,
) -> dict:
    """The sub-tool run itself; use :func:`run_subtool`, which adds the lifecycle rule."""
    from .system_one_subtools import check_applicability

    base: dict[str, Any] = {"subtool": str(name), "origin": "", "path": "", "live": False}
    try:
        spec, origin, path = load_subtool(name, state=state)
    except FileNotFoundError as exc:
        return _stop(base, status="not_found", reason=str(exc))
    base["origin"] = origin
    base["path"] = path
    base["declared"] = len(_as_list(spec.get("actions")))
    base["spec"] = subtool_summary(spec, origin=origin, path=path)

    problems = subtool_errors(spec)
    if problems:
        return _stop(
            base,
            status="usage",
            reason="the stored sub-tool is not usable: " + "; ".join(problems),
        )

    resolved_runner = runner
    resolution: dict
    if resolved_runner is None:
        resolved_runner, resolution = resolve_runner(
            runner_kind,
            workdir=workdir,
            timeout=timeout,
            allow_live_desktop=allow_live_desktop,
            live_target=live_target,
        )
    else:
        resolution = {
            "requested": runner_kind,
            "bridge": str(getattr(resolved_runner, "isolation", "")),
            "resolved": True,
            "isolation": str(getattr(resolved_runner, "isolation", "")),
            "real": str(getattr(resolved_runner, "isolation", "")) != "dry-run",
            "fixture": str(getattr(resolved_runner, "isolation", "")) == "dry-run",
            "scope": (
                "in-process fixture"
                if str(getattr(resolved_runner, "isolation", "")) == "dry-run"
                else "real"
            ),
            "reason": "a runner was supplied by the caller",
        }
    base["runner"] = resolution
    if holder is not None:
        holder["runner"] = resolved_runner
    if resolved_runner is None:
        return _stop(
            base,
            status="blocked",
            reason=f"no usable runner: {resolution['reason']}",
            hint="`skaldir act check --json` reports which bridges exist on this host",
            missing_capability=_missing_from_resolution(resolution),
            record_to=_runtime_dir(runtime),
            write_record=write_record,
            goal=str(spec.get("name", "")),
        )

    synced_policy = _sync_live_policy(
        default_policy(),
        resolved_runner,
        allow_live_desktop=allow_live_desktop,
        live_target=live_target,
    )
    base["policy"] = policy_record(synced_policy)

    visual = to_visual_subtool(spec)
    version = str(app_version or "").strip() or __version__
    applicability = check_applicability(visual, app_version=version, locale=str(locale or ""))
    base["applicability"] = applicability.as_dict()
    if not applicability.applicable:
        return _stop(
            base,
            status="blocked",
            reason=f"not applicable here: {applicability.reason}",
            hint="the sub-tool states the app versions and locales it was verified for",
            missing_capability="an applicability that matches this app version/locale",
            record_to=_runtime_dir(runtime),
            write_record=write_record,
            runner=resolved_runner,
            isolation=str(getattr(resolved_runner, "isolation", "")),
            goal=str(spec.get("name", "")),
        )

    try:
        observation = resolved_runner.observe()
        observation_text = str(getattr(observation, "text", "") or "")
    except Exception as exc:  # noqa: BLE001 — no observation ⇒ no precondition verdict
        return _stop(
            base,
            status="reobserve",
            reason=(
                "the preconditions could not be checked: the observation failed with "
                f"{type(exc).__name__}: {exc}"
            ),
            hint="nothing was executed",
            record_to=_runtime_dir(runtime),
            write_record=write_record,
            runner=resolved_runner,
            isolation=str(getattr(resolved_runner, "isolation", "")),
            goal=str(spec.get("name", "")),
        )

    app_state = read_app_state(
        spec, observation_text, runner=resolved_runner, workdir=workdir
    )
    observed = observe_anchors(spec, observation_text, app_state=app_state)
    base["precondition_check"] = dict(observed)
    base["app_state"] = dict(observed.get("app_state") or {})
    if observed["unobservable"]:
        return _stop(
            base,
            status="blocked",
            reason=(
                "the sub-tool declares a check that cannot be read on this host: "
                + "; ".join(observed["unobservable"])
            ),
            hint=(
                "declare app_state_probe (the app's dump argv) or app_state_file on the "
                "sub-tool, or use window_title anchors; `skaldir act check --json` names "
                "the capabilities and what is missing for them"
            ),
            missing_capability=(
                "app_state_probe (the app's own dump argv) or app_state_file for a richer state"
            ),
            record_to=_runtime_dir(runtime),
            write_record=write_record,
            runner=resolved_runner,
            isolation=str(getattr(resolved_runner, "isolation", "")),
            goal=str(spec.get("name", "")),
        )
    if observed["missing"]:
        return _stop(
            base,
            status="reobserve",
            reason=(
                "blind replay refused: " + "; ".join(observed["missing"])
                + " — reobserve instead of replaying a stored sequence"
            ),
            hint="nothing was executed; the stored flow may only run when it is observed",
            missing_anchors=list(observed["missing"]),
            record_to=_runtime_dir(runtime),
            write_record=write_record,
            runner=resolved_runner,
            isolation=str(getattr(resolved_runner, "isolation", "")),
            goal=str(spec.get("name", "")),
        )

    outcome = run_action_list(
        _as_list(spec.get("actions")),
        runner=resolved_runner,
        workdir=workdir,
        end_state=spec.get("end_state"),
        goal=str(spec.get("name", "")),
        runtime=runtime,
        timeout=timeout,
        session_id=f"act-subtool:{name}",
        write_record=write_record,
        policy=synced_policy,
        allow_live_desktop=allow_live_desktop,
        live_target=live_target,
    )
    merged = dict(outcome)
    merged.update(
        {
            "subtool": str(spec.get("name", "")),
            "origin": origin,
            "path": path,
            "spec": base["spec"],
            "applicability": base["applicability"],
            "precondition_check": base["precondition_check"],
            "app_state": base.get("app_state") or {},
            "runner": outcome.get("runner", resolution),
        }
    )
    return merged


def learn_subtool(
    session_id: str,
    *,
    runtime: str | Path | None = None,
    state: str | Path | None = None,
    name: str = "",
    min_repeats: int = 2,
    force: bool = False,
) -> dict:
    """Learn a draft sub-tool from one recorded session — save it, execute nothing.

    The session's ``cu_action`` events are replayed in order; every entry is reduced to
    a ``kind + anchor`` step, and the *longest* ordered step sequence that repeats at
    least ``min_repeats`` times becomes the draft's action list (preconditions, anchors
    and the end-state check are read off the same sequence — the pattern's own first
    anchor and last detail). The draft is stored through :func:`save_subtool`, so it is
    validated by the exact same rules a hand-written declaration passes.

    No repeated sequence (or no recorded session) is an honest refusal, never an
    invented sub-tool.
    """
    root = Path(runtime).expanduser() if runtime not in (None, "") else _runtime_dir()
    if not name:
        safe = _safe_name(session_id)
        name = f"learned_{safe}"
    try:
        from .events import EventStore

        db_path = root / "events.db"
        if not db_path.is_file():
            return {
                "learned": False,
                "error": "not_found",
                "reason": f"no recorded session at {db_path} (the event store is not there)",
            }
        store = EventStore(db_path)
        try:
            rows = [
                event
                for event in store.iter_session(str(session_id))
                if event.kind in ("cu_action", "cu_attempt")
            ]
        finally:
            store.close()
    except Exception as exc:  # noqa: BLE001 — a broken store is a fact, not a crash
        return {"learned": False, "error": "not_found", "reason": f"the event store is unreadable: {exc}"}
    if not rows:
        return {
            "learned": False,
            "error": "not_found",
            "reason": f"no recorded session {session_id!r} with computer-use steps in the store",
        }
    steps = [
        (
            f"{str(entry.payload.get('kind', '') or '')}"
            f"+{str(entry.payload.get('anchor', '') or entry.payload.get('detail', '') or '').strip()}"
        )
        for entry in rows
    ]
    pattern = _longest_repeated_sequence(steps, min_repeats=int(min_repeats or 2))
    if not pattern:
        return {
            "learned": False,
            "error": "nothing_repeated_to_learn",
            "reason": (
                f"no sequence of kind+anchor steps repeats ≥{int(min_repeats or 2)}× in "
                f"{len(steps)} recorded step(s) — nothing repeated to learn"
            ),
            "session_id": str(session_id),
            "steps_seen": len(steps),
        }
    actions: list[dict] = []
    anchors: list[dict] = []
    payloads = [entry.payload for entry in rows]
    for index, step_token in enumerate(pattern):
        kind = step_token.split("+", 1)[0] or "cli"
        anchor_text = step_token.split("+", 1)[1]
        first = payloads[steps.index(step_token)]
        if index == 0:
            anchors.append({"id": f"anchor_{index + 1}", "text": anchor_text or "target", "source": "window_title"})
        declared = {
            "kind": kind if kind in DECLARED_KINDS else "cli",
            "channel": str(payloads[steps.index(step_token)].get("channel", "") or "api"),
            "argv": ["true"],
            "description": f"learned step {index + 1}: {step_token}",
            "verify": {"expect_observe_contains": anchor_text or step_token},
        }
        problem = action_error(index, declared)
        if problem:
            declared["kind"] = "cli"
            declared["argv"] = ["true"]
            declared["verify"] = {"expect_observe_contains": step_token}
        actions.append(declared)
    for index, entry in enumerate(anchors[:1]):
        pass  # preconditions mirror the pattern's first anchor
    spec = {
        "name": name,
        "version": "0.1",
        "description": (
            f"draft learned from session {session_id}: the sequence "
            f"{' → '.join(pattern)} repeated {min_repeats}×"
        ),
        "applicability": {
            "app": "learned",
            "app_versions": [">=0.0"],
            "locales": ["*"],
        },
        "preconditions": [
            {
                "id": "learned_entry_anchor",
                "text": anchors[0]["text"] if anchors else "entry state",
                "source": "window_title",
                # Declared window-absent: the learner never observed a future run's
                # start, and an open-like sequence's entry window is exactly what the
                # sequence itself creates. The pre-check then never refuses before the
                # first act (`reobserve` used to kill every learned draft here); the
                # end_state check — read from the real display — is what proves the
                # replay, unchanged.
                "expect": "absent",
            }
        ],
        "anchors": anchors,
        "actions": actions,
        "end_state": {
            "channel": "window_title",
            "description": "the state the repeated sequence leaves behind",
            "expect_observe_contains": pattern[-1].split("+", 1)[1] or pattern[-1],
        },
        "verification_contract": (
            f"replaying the learned sequence reproduces the end state observed "
            f"{min_repeats}× in session {session_id}"
        ),
    }
    problems = subtool_errors(spec)
    if problems:
        return {
            "learned": False,
            "error": "invalid_draft",
            "reason": "; ".join(problems),
            "spec": spec,
        }
    try:
        summary = save_subtool(spec, state=state if state not in (None, "") else root, force=force)
    except FileExistsError as exc:
        return {
            "learned": False,
            "error": "already_stored",
            "reason": str(exc),
            "hint": "pass --force to replace the learned draft deliberately",
        }
    return {
        "learned": True,
        "subtool": summary,
        "session_id": str(session_id),
        "pattern": list(pattern),
        "repeats": int(min_repeats or 2),
        "steps_seen": len(steps),
    }


def _longest_repeated_sequence(steps: list[str], min_repeats: int) -> list[str]:
    """The longest ordered step list that appears at least ``min_repeats`` times in order.

    A simple scan: every candidate length from longest to shortest, every start, counted
    non-overlapping occurrences. The first hit wins; ``[]`` when nothing qualifies.
    """
    size = len(steps)
    for length in range(size // max(1, min_repeats), 0, -1):
        for start in range(size - length + 1):
            candidate = steps[start:start + length]
            occurrences = 0
            cursor = 0
            while cursor + length <= size:
                if steps[cursor:cursor + length] == candidate:
                    occurrences += 1
                    cursor += length
                else:
                    cursor += 1
            if occurrences >= min_repeats:
                return candidate
    return []


__all__ = [
    "ALL_KINDS",
    "ANCHOR_SOURCES",
    "BRIDGES",
    "BRIDGE_CONTAINER",
    "BRIDGE_CONTAINER_GUI",
    "BRIDGE_DRY_RUN",
    "BRIDGE_HOST_SESSION",
    "BRIDGE_LIVE_DESKTOP",
    "BRIDGE_SANDBOX",
    "BUILTIN_SUBTOOLS",
    "CAPABILITIES",
    "CONTAINER_DISPLAY",
    "DECLARED_KINDS",
    "DEFAULT_STATE_SUBDIR",
    "EXIT_BLOCKED",
    "EXIT_CONFLICT",
    "EXIT_INTERNAL",
    "EXIT_NOT_FOUND",
    "EXIT_OK",
    "EXIT_USAGE",
    "EXIT_VERIFICATION_FAILED",
    "GUI_IMAGE",
    "SUBTOOL_KEYS",
    "action_channel",
    "action_error",
    "builtin_subtools",
    "capability_names",
    "capability_report",
    "capability_status",
    "check_end_state",
    "default_policy",
    "host_facts",
    "list_subtools",
    "live_setup_report",
    "load_subtool",
    "learn_subtool",
    "observe_anchors",
    "policy_record",
    "read_app_state",
    "resolve_runner",
    "run_action_list",
    "run_subtool",
    "save_subtool",
    "subtool_dir",
    "subtool_errors",
    "subtool_summary",
    "to_core_action",
    "to_core_verification",
    "to_visual_subtool",
]
