#!/usr/bin/env python
"""Real Computer-Use end-to-end probe against the isolated GUI image (§10/§52/§55).

Why this exists: `godsvoice cu demo` drives a DryRunRunner with scripted behaviours, so it proves
the channel order, not the GUI path. This probe talks to a real container with a real Xvfb display
and a real application (xterm), drives it with the runner's own GUI action kinds, and verifies the
effect against the APPLICATION'S OWN STATE - the text it really received:

  start container -> launch xterm writing stdin to a file -> observe (window metadata)
  -> real screenshot before -> windowfocus/type/Return through the runner
  -> the app's file contains exactly what was typed   (real effect, app state)
  -> fresh observation: window still there; screenshot changed
  -> ctrl+d -> observation + process check: app gone
  -> counter-cases: stale window anchor, blind replay, unperceived field, unknown app version,
     live_display claimed on a metadata-only channel

Run:  .venv/bin/python tools/cu_gui_e2e_probe.py
Exits 0 only if every step held; prints the observed raw values, never a verdict without them.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from godsv.computer_use import Action, ContainerRunner  # noqa: E402
from godsv.system_one_guistate import (  # noqa: E402
    GUI_STATE_FIELDS,
    GUIState,
    GuiStateContractError,
    PerceivedField,
)
from godsv.system_one_subtools import (  # noqa: E402
    BlindReplayRefused,
    VisualSubtool,
    assert_no_blind_replay,
    check_applicability,
    check_blind_replay,
)

TITLE = "GODSV-CU-PROBE"
TYPED = "godsv-e2e-ok"
METADATA_CHANNEL = "window_metadata"   # what xdotool actually gives us
APP_FILE = "/tmp/typed.txt"
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str) -> None:
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}", flush=True)


def exec_in(runner: ContainerRunner, argv: list[str], detach: bool = False) -> subprocess.CompletedProcess:
    """podman exec wrapper. detach=True is required for long-lived apps: a plain exec kills its
    process group when it returns, so `sh -c 'app &'` leaves no window behind."""
    cmd = ["podman", "exec"]
    if detach:
        cmd.append("-d")
    cmd += ["-e", "DISPLAY=:90", runner.name] + argv
    return subprocess.run(cmd, capture_output=True, text=True, timeout=runner.timeout)


def root_shot(runner: ContainerRunner, path: str) -> str:
    """Capture the real root window inside the container; return the md5 of the raw capture."""
    exec_in(runner, ["sh", "-c", f"xwd -root -silent -out {path}"])
    return (exec_in(runner, ["md5sum", path]).stdout or "").strip().split(" ")[0]


def main() -> int:
    runner = ContainerRunner(gui=True, name=f"godsv-cu-probe-{int(time.time()) % 100000}")
    ok, detail = runner.available()
    check("image available for the runner", ok, detail)
    if not ok:
        return 1
    try:
        ok, detail = runner.start()
        check("container + Xvfb :90 started", ok, detail)
        if not ok:
            return 1

        # 1. a real application inside the isolated display; it writes its stdin to APP_FILE, so the
        #    effect of our input is readable in the app's own state (no host display is ever used)
        launch = exec_in(runner, ["xterm", "-T", TITLE, "-e", "sh", "-c", f"cat > {APP_FILE}"],
                         detach=True)
        check("real app launched inside the container", launch.returncode == 0,
              f"podman exec -d rc={launch.returncode} {launch.stderr.strip()[:60]}")
        time.sleep(3.0)
        obs = runner.observe()
        check("the real app window is observed (actual perception)", TITLE in obs.text,
              f"observe() -> {obs.text.strip()[:90]!r}")

        # 2. GUIState from that observation. §52 refuses an OMITTED field, so all are reported -
        #    unperceived ones explicitly without a value ("not looked at" != "absent").
        fields = {name: PerceivedField(name=name, value=None, perceived=False)
                  for name in GUI_STATE_FIELDS}
        fields["window_title"] = PerceivedField(name="window_title", value=TITLE, perceived=True,
                                                channel=METADATA_CHANNEL)
        metadata_state = GUIState(gui_state_id=f"probe-meta-{int(time.time())}", fields=fields,
                                  channel=METADATA_CHANNEL, live_display=False)
        view = metadata_state.contract_view()
        check("GUIState reports the observed value, its channel and its provenance",
              view["fields"]["window_title"]["value"] == TITLE
              and view["fields"]["window_title"]["channel"] == METADATA_CHANNEL
              and view["fields"]["window_title"]["provenance"] == "perception",
              f"window_title={view['fields']['window_title']!r}")
        check("counter-case B1: an unperceived field carries no value",
              view["fields"]["app_version"]["value"] is None
              and view["fields"]["app_version"]["perceived"] is False
              and "app_version" in view["unperceived"],
              f"app_version={view['fields']['app_version']!r}")
        check("a GUIState never certifies an effect (ground truth stays the app effect)",
              metadata_state.certifies_effect is False and view["certifies_effect"] is False,
              f"certifies_effect={view['certifies_effect']} ground_truth={view['ground_truth']!r}")

        # 3. counter-case: claiming a live display on a channel that is only metadata must fail
        try:
            GUIState(gui_state_id="probe-lie", fields=fields, channel=METADATA_CHANNEL, live_display=True)
            refused_live, detail_live = False, "window_metadata was accepted as a live GUI surface"
        except GuiStateContractError as exc:
            refused_live, detail_live = True, f"refused: {str(exc)[:70]}"
        check("counter-case B3: live_display on a metadata-only channel is refused", refused_live, detail_live)

        # 4. a real screen capture before the action
        before_md5 = root_shot(runner, "/tmp/shot-before.xwd")
        check("real screenshot taken before the action", bool(before_md5), f"xwd -> md5 {before_md5}")

        # 5. the real action, using the runner's own GUI kinds (key/type_text -> xdotool in-container)
        wid = (exec_in(runner, ["xdotool", "search", "--name", TITLE]).stdout or "").strip().splitlines()[0]
        focus = runner.act(Action(kind="cli", argv=["xdotool", "windowfocus", "--sync", wid]))
        typed = runner.act(Action(kind="type_text", payload={"text": TYPED}))
        submit = runner.act(Action(kind="key", payload={"key": "Return"}))
        check("focus + type + Return executed through the runner", bool(wid) and focus.ok and typed.ok and submit.ok,
              f"window={wid!r} focus={focus.ok} type={typed.ok} key={submit.ok}")

        # 6. the effect: the APPLICATION'S OWN STATE must contain exactly what was typed
        time.sleep(1.5)
        readback = (exec_in(runner, ["cat", APP_FILE]).stdout or "")
        check("the app really received our input (its own file holds the typed text)",
              readback.strip() == TYPED, f"cat {APP_FILE} -> {readback.strip()!r}")
        after_md5 = root_shot(runner, "/tmp/shot-after.xwd")
        check("the real screen changed (before/after capture differ)",
              bool(after_md5) and after_md5 != before_md5, f"{before_md5} -> {after_md5}")

        # 7. live_display IS legitimate on a screenshot channel - the honest variant of step 3
        shot_fields = {name: PerceivedField(name=name, value=None, perceived=False)
                       for name in GUI_STATE_FIELDS}
        shot_fields["window_title"] = PerceivedField(name="window_title", value=None, perceived=True,
                                                     channel="screenshot")
        shot_state = GUIState(gui_state_id=f"probe-shot-{int(time.time())}", fields=shot_fields,
                              channel="screenshot", live_display=True)
        check("a screenshot-channel state may declare a live display",
              shot_state.live_display is True and shot_state.ground_truth == "app_effect",
              f"live_display={shot_state.live_display} ground_truth={shot_state.ground_truth!r}")

        # 8. closing the app for real, then verifying it with two independent observations
        close = runner.act(Action(kind="key", payload={"key": "ctrl+d"}))
        time.sleep(1.5)
        obs2 = runner.observe()
        proc = exec_in(runner, ["pgrep", "-f", "xterm"])
        check("the app is really gone (observation + process check)",
              close.ok and TITLE not in obs2.text and proc.returncode == 1,
              f"ctrl+d={close.ok} observe->{obs2.text.strip()[:40]!r} pgrep rc={proc.returncode}")

        # 9. counter-case A: the same anchor after the app is gone must not be replayed blindly
        stale = exec_in(runner, ["xdotool", "key", "--window", wid, "Return"])
        check("counter-case A1: acting on the stale window id fails (no silent no-op)",
              stale.returncode != 0, f"xdotool rc={stale.returncode} {stale.stderr.strip()[:60]!r}")
        subtool = VisualSubtool(
            capability_id="probe.type_into_app",
            version="1.0",
            supported_app_versions=("xterm 1.0",),
            supported_locales=("de_DE",),
            start_conditions=("window_visible",),
            anchors=("window_title",),
            allowed_actions=("key", "type_text"),
            expected_transitions=("text_accepted",),
            completion_conditions=("text_accepted",),
            failure_conditions=("anchor_lost",),
            reobserve_policy=(),
            system2_escalation_policy=(),
            verification_contract="app-state readback + fresh observation + process check",
        )
        blind = check_blind_replay(subtool, observed_start_conditions=(), observed_anchors=())
        try:
            assert_no_blind_replay(blind)
            refused, detail_a2 = False, "a blind replay was allowed"
        except BlindReplayRefused as exc:
            refused, detail_a2 = True, f"refused: {str(exc)[:70]}"
        check("counter-case A2: blind replay of the stored macro is refused", refused, detail_a2)
        live = check_blind_replay(subtool, observed_start_conditions=("window_visible",),
                                  observed_anchors=("window_title",))
        check("positive control: the guard allows the replay when the anchors ARE observed",
              assert_no_blind_replay(live) is not None, f"replay_allowed={live.replay_allowed}")

        # 10. counter-case B: an unsupported app version is refused, never assumed
        bad = check_applicability(subtool, app_version="9.9.9-never-seen", locale="de_DE")
        good = check_applicability(subtool, app_version="xterm 1.0", locale="de_DE")
        check("counter-case B2: unsupported app version refused, supported one passes",
              bad.applicable is False and good.applicable is True,
              f"unknown={bad.applicable} supported={good.applicable}")

        return 0 if all(step_ok for _, step_ok, _ in results) else 1
    finally:
        runner.close()


if __name__ == "__main__":
    code = main()
    failed = [name for name, step_ok, _ in results if not step_ok]
    print(f"\ncu gui e2e probe: {len(results) - len(failed)}/{len(results)} checks held"
          + (f" — failed: {failed}" if failed else ""))
    raise SystemExit(code)
