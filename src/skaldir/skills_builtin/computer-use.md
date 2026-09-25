---
name: computer-use
description: "Drive any GUI app (Blender, Docker Desktop, DaVinci Resolve, browser, terminal) through the platform adapter primitives — observe, act, verify. No app-specific built-ins required. Self-improves from user feedback and verified flows."
version: 0.1.0
---

# Computer use — generic app driving

Drive **any** desktop application through six primitives. Built-ins (`open_browser`,
`open_youtube`, …) are shortcuts, never a requirement. If you can see a window and
send keys/clicks, you can drive the app.

## The loop (every task)

1. **Observe** — `skill_act_check` (cached 30 s) then read `live_state` from the
   previous result or call `skill_act` with `{"actions": [], "end_state": {...}}`
   to get the current window list. Never guess what is on screen.
2. **Plan** — break the task into 2–8 GUI steps. Each step declares:
   `kind` (launch / key / type_text / mouse / drag / window), `payload`, and a
   `verify` expectation.
3. **Act** — one `skill_act` call with the full action list and an `end_state`
   that names what the screen must show afterwards. The engine is idempotent:
   if the end_state already holds, it skips (result `skipped: true`).
4. **Verify** — the end_state is checked against the **real** observation
   (window titles / app_state), never against the action's stdout. If the
   verification fails, observe again and adapt — do not blindly re-run.

## Platform primitives

| Primitive | Action kind | Payload |
|---|---|---|
| Launch an app | `launch` | `{"command": "blender"}` or `{"argv": ["blender", "file.blend"]}` |
| Press a key / shortcut | `key` | `{"key": "ctrl+o"}` (logical names: ctrl/alt/shift/super, return, escape, tab, F1–F12, arrows) |
| Type text | `type_text` | `{"text": "hello"}` |
| Click / move | `mouse` | `{"x": 100, "y": 200, "click": true, "button": 1}` |
| Drag / slider | `drag` | `{"x": 100, "y": 200, "to_x": 300, "to_y": 200}` |
| Focus a window | `window` | `{"op": "focus", "title": "Blender"}` |

Every action needs a `verify` block (`expect_ok`, `expect_observe_contains`, or
`expect_stdout_contains`). Every run needs an `end_state` with
`expect_observe_contains` — the state the screen must show after the run.

## Platform-specific notes

### Omarchy (Hyprland)
- Windows are listed via `hyprctl clients -j` (includes Wayland-native).
- Input via `wtype` (keyboard) and `ydotool` (mouse).
- `omarchy` CLI for screenshots: `omarchy capture screenshot fullscreen save`.
- Hotkeys: `Super+Space` menu, `Super+Return` terminal, `Super+Shift+Return` browser,
  `Super+W` close, `Super+F` fullscreen, `Super+C/X/V` clipboard, `Super+1..4` workspaces.
- Full table: `hyprctl binds` or the `OMARCHY_HOTKEYS` dict in `cu_platform.py`.

### GNOME (Ubuntu / Fedora / RHEL)
- X11 windows via `xdotool`; Wayland-native via `org.gnome.Shell.Introspect`.
- Children are forced to XWayland (`MOZ_ENABLE_WAYLAND=0` etc.) so xdotool can drive them.
- `xdg-open <url>` opens in the existing browser instance — never spawn a second one.
- Hotkeys: `Ctrl+Alt+T` terminal, `Print Screen` screenshot, `Alt+F4` close, `Alt+Tab` switch.

### macOS
- Windows via `osascript` (System Events); input via `osascript keystroke` / `cliclick`.
- `open -a <App>` launches; `open <url>` opens the default browser.
- Clipboard: `Cmd+C/X/V`. Screenshot: `Cmd+Shift+3` or `screencapture -x <path>`.

### Windows
- Windows via PowerShell (`Get-Process | Where MainWindowTitle`).
- Input via `SendKeys` / `mouse_event` P/Invoke.
- `start <app>` launches. Screenshot via `System.Drawing`.

## Driving any app — patterns

### Open a file in an app
```
launch: {"command": "blender"}
key: {"key": "ctrl+o"}           → File → Open
type_text: {"text": "/path/to/file.blend"}
key: {"key": "return"}
end_state: expect_observe_contains: "file.blend"
```

### Navigate a menu (any app)
1. Focus the window (`window` kind, `{"op": "focus", "title": "<app>"}`).
2. Press `alt` to reveal menu bar (or `F10` on Windows).
3. Type the first letter of the menu (e.g. `f` for File).
4. Arrow down to the item, `return` to activate.

### Fill a dialog
1. `window` focus the dialog (its title usually contains "Open", "Save", "Export").
2. `key` `ctrl+l` or `tab` to reach the path/field input.
3. `type_text` the value.
4. `key` `return` to confirm.

### Blender / 3D apps
- Use `key` for shortcuts (`shift+a` Add, `g` Grab, `s` Scale, `tab` Edit mode).
- Use `mouse` + `drag` for viewport navigation (middle-drag = rotate).
- Verify via window title (file name) or `screenshot` when the effect is visual.

### Docker Desktop / containers
- GUI: `launch` Docker Desktop, `window` focus, `mouse` clicks on containers list.
- CLI is faster: `skill_act` with `kind: "cli"` and `argv: ["docker", "ps"]` +
  `verify: {"expect_stdout_contains": "Up"}`. Use the GUI only when the task
  requires visual confirmation.

### Video editing (DaVinci Resolve, Kdenlive, OpenShot)
- Timeline interactions need `mouse`/`drag` with pixel coordinates from `screenshot`.
- Use `key` for shortcuts (`space` play/pause, `i`/`o` in/out points, `ctrl+s` save).
- Verify via window title (project name) or `screenshot` diff.

### Terminals
- `type_text` sends literal text; `key` `return` executes.
- For long commands, write to a file first (`write_text` tool), then
  `type_text: "bash /tmp/setup.sh"` + `return` — avoids shell-escaping issues.

## End-state verification

The end_state is the **contract** — the run is only `verified` when the real
observation matches. Patterns:

- Window title: `"expect_observe_contains": "Blender"` (substring match).
- OR-pattern: `"expect_observe_contains": "Zen|Firefox|Chrome"` (any branch).
- App state: `"channel": "app_state"` with `"expect_state_contains": "running"`.

If verification fails, the result names the `missing` expectation and the
`observed` text. Adapt and retry — never claim success without a match.

## Self-improvement

After a **verified** multi-step flow (2+ actions, end_state matched):
1. The engine auto-saves it as a `learned_subtool` (e.g. `open_google_docs`).
2. Next time the same goal appears, call `skill_act` with
   `{"subtool": "open_google_docs"}` — one call instead of rebuilding.
3. If a learned flow fails on a new app/version, it is demoted — rebuild the
   action list and let it re-learn.

### User feedback loop
When the owner corrects a flow ("no, use Super+Space not Alt+F2"):
1. Record the correction in the skill body under **Learned corrections**.
2. Update the action pattern for that app/platform.
3. Re-verify the flow end-to-end before marking it as the new default.

### Skill review cadence
After every 10 verified computer-use flows, review:
- Which flows became `learned_subtool`s? Are they being reused?
- Which actions failed most often? (Timeouts → wrong coordinates; `skipped: true`
  → end_state too vague.)
- Is the platform detection still accurate? (`skill_act_check` reports `platform`.)

## Acceptance criteria

- Every action has a `verify` block; every run has an `end_state`.
- No app-specific built-in is required — `open_browser` is a shortcut, not a dependency.
- The end_state is checked against the real observation, never against stdout.
- A failed verification produces a retry with adapted actions, not a false claim.
- Learned flows are reused via `subtool` when the goal matches.
