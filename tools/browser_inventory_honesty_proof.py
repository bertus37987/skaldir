"""Live proof (browser-inventory-honesty-after-relaunch): the recorded tab inventory
must stop claiming tabs of a dead browser after an observed relaunch.

Run with the repo venv and an ISOLATED runtime home, e.g.:

    cd /home/willy/Agentic_Projects/Skaldir_Agent && \
    SKALDIR_HOME=/tmp/skaldir-browser-inventory/home-after \
    .venv/bin/python tools/browser_inventory_honesty_proof.py

Mirrors logs/browser-inventory/before_proof.py step-for-step — the ONLY difference is
the product version under test (this one runs the FIXED repo code), so the
before/after envelopes differ by nothing but the fix.

Phantom signature measured (agent-browser 0.38.1 reuses per-session sequential tab
ids — the fresh browser's first tab is `t1` again): a recorded entry is a phantom
when its (tab_id, url) pair does not exist in the REAL relaunched browser.

What it proves with the REAL agent-browser binary (real browser, real state file,
real CLI subprocesses for every `skaldir browser ...` envelope):

1. the recorded inventory reflects the REAL settled tab of the live browser, and the
   CLI's `browser tabs`/`discover` agree with the transport's own `tab list`;
2. after an OUT-OF-BAND close the state file still claims that tab (the defect's
   precondition);
3. the transport call that relaunched the browser is OBSERVED, and the product's
   recording of it (`record_observed_relaunch`) SUPERSEDES the stale inventory: the
   dead tab's pair leaves the state file, `last_relaunch_observed` names what was
   superseded;
4. a real inventory refresh (the agent path's post-act inventory) repopulates the
   state file with the NEW browser's real tabs — agent-owned, count in agreement
   with the transport, and the CLI reports no phantom and no phantom count;
5. VAL-WEB-003 stays true live: 10 concurrent CLI `browser ensure` demands on the
   live session all merge into reuse with no extra start;
6. `browser detach` closes nothing (foreign browsers survive, BR-13);
7. cleanup closes only our own named session — never `close --all`.

Extension (2026-10-07, the orchestrator's measured staleness case): while the
browser is STILL ALIVE, an in-tab navigation must UPSERT the recorded metadata —
the old post-act refresh skipped known tab ids, so the recorded url/title stayed
frozen at the first note. Step 3b proves: after navigating the same tab, the
state file's entry for it carries the NEW url and the CLI's `browser tabs`
(reads the recorded inventory directly) shows the refreshed url, not the stale one.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent


def _agent_browser() -> str:
    local = REPO / "node_modules" / ".bin" / "agent-browser"
    return str(local) if local.exists() else "agent-browser"


def _cli() -> str:
    local = Path(sys.executable).parent / "skaldir"
    return str(local) if local.exists() else "skaldir"


def _close_session(session: str) -> None:
    subprocess.run([_agent_browser(), "--session", session, "close", "--json"],
                   capture_output=True, text=True, timeout=60, check=False)


def _cli_json(*argv: str) -> tuple[int, dict[str, Any]]:
    """One real CLI invocation. Measured: the payload lands on stdout OR (when the
    contract runner drives) the envelope on stdout with the payload on stderr.
    Prefer the envelope's ``data``; fall back to the first JSON document anywhere.
    Returns (exit code, payload)."""
    proc = subprocess.run([_cli(), *argv, "--json"], capture_output=True, text=True,
                          timeout=120, check=False, env={**os.environ})
    docs: list[dict[str, Any]] = []
    for stream in (proc.stdout or "", proc.stderr or ""):
        idx = 0
        text = stream
        while idx < len(text):
            rest = text[idx:].lstrip()
            if not rest:
                break
            try:
                doc, end = json.JSONDecoder().raw_decode(rest)
            except json.JSONDecodeError:
                break
            if isinstance(doc, dict):
                docs.append(doc)
            idx = (len(text) - len(rest)) + end
    envelopes = [d for d in docs if d.get("schema_version")]
    if envelopes and isinstance(envelopes[0].get("data"), dict):
        return proc.returncode, envelopes[0]["data"]
    return proc.returncode, (docs[0] if docs else {})


def _pairs(tabs: Any) -> list[tuple[str, str]]:
    return [(str(t.get("tab_id") or ""), str(t.get("url") or "")) for t in (tabs or [])]


def main() -> int:
    from skaldir.browser_layer import (
        AgentBrowserBackend,
        SessionManager,
        TabInfo,
        session_state_dir,
    )
    from skaldir.core_capabilities import core_capability_tools

    home = os.environ.get("SKALDIR_HOME", "")
    if not home or "skaldir-browser-inventory" not in home:
        print("SET SKALDIR_HOME to an isolated /tmp/skaldir-browser-inventory/... home",
              file=sys.stderr)
        return 2
    session = "skaldir"           # the product's own named session (never `default`)
    backend = AgentBrowserBackend()
    if not backend.probe().available:
        print("agent-browser is not available; the proof needs the real transport",
              file=sys.stderr)
        return 2
    manager = SessionManager(session_state_dir(), backend=backend)
    workspace = Path(home) / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    (tool,) = core_capability_tools(workspace=workspace, runtime=Path(home) / "runtime",
                                    run_id="inventory-proof", include=("browser",))
    nonce = str(int(time.time() * 1000))
    url_a = f"https://example.org/?skaldir-proof-before={nonce}"
    url_b = f"https://example.com/?skaldir-proof-after={nonce}"
    checks: dict[str, Any] = {}
    commands: list[dict[str, Any]] = []
    log: list[dict[str, Any]] = []

    def note(label: str, payload: dict[str, Any]) -> None:
        log.append({"label": label, "payload": payload})

    def state_pairs() -> list[tuple[str, str]]:
        data = json.loads(
            (session_state_dir() / "browser-session.json").read_text(encoding="utf-8"))
        return _pairs(data.get("tabs") or [])

    try:
        # -- 1. a REAL tab of the LIVE browser, created by the agent path ----------- #
        result = tool.handler(do="act", kind="navigate", url=url_a)
        checks["agent_path_creates_a_real_tab"] = bool(result.get("ok"))
        time.sleep(1.5)          # measured: `open` returns before the URL settles
        transport = backend.tabs(session=session)
        transport_pairs = _pairs(transport.get("tabs") or [])
        note("transport tab list (alive)", {"tabs": transport_pairs})

        # -- 2. the recorded inventory reflects the real tab (product's refresh) ---- #
        manager.record_tabs([
            TabInfo(tab_id=tid, url=url, title="", owner="agent", run_id="inventory-proof")
            for tid, url in transport_pairs])
        recorded = state_pairs()
        note("state file inventory (alive)", {"tabs": recorded})
        checks["recorded_inventory_is_the_real_tab"] = recorded == transport_pairs

        # -- 3. CLI agrees with the transport while the browser lives --------------- #
        rc_tabs, tabs_payload = _cli_json("browser", "tabs")
        rc_disc, disc_payload = _cli_json("browser", "discover")
        commands.append({"cmd": "browser tabs (alive)", "exit": rc_tabs,
                         "tabs": _pairs(tabs_payload.get("tabs"))})
        commands.append({"cmd": "browser discover (alive)", "exit": rc_disc,
                         "tab_count": disc_payload.get("tab_count")})
        checks["cli_agrees_with_transport_while_alive"] = (
            rc_tabs == 0 and rc_disc == 0
            and _pairs(tabs_payload.get("tabs")) == transport_pairs
            and int(disc_payload.get("tab_count") or 0) == len(transport_pairs))

        # -- 3b. in-tab navigation UPSERTS the recorded metadata (alive browser) ---- #
        # Measured staleness (orchestrator 2026-10-07): the old post-act refresh
        # skipped known tab ids, so the recorded url stayed at the first note.
        url_c = f"https://example.org/?skaldir-proof-navigated={nonce}"
        nav = tool.handler(do="act", kind="navigate", url=url_c)
        checks["navigation_demand_executed"] = bool(nav.get("ok"))
        # measured: `open` returns before the URL settles — wait until the transport
        # itself reports the navigated url (bounded, honest either way)
        transport_pairs_c: list[tuple[str, str]] = []
        deadline = time.time() + 6.0
        while time.time() < deadline:
            transport_c = backend.tabs(session=session)
            transport_pairs_c = _pairs(transport_c.get("tabs") or [])
            if any(url == url_c for _, url in transport_pairs_c):
                break
            time.sleep(1.0)
        note("transport tab list (after in-tab navigation)",
             {"tabs": transport_pairs_c})
        checks["navigation_settled_at_transport"] = (
            any(url == url_c for _, url in transport_pairs_c))
        data_c = json.loads(
            (session_state_dir() / "browser-session.json").read_text(encoding="utf-8"))
        recorded_c = _pairs(data_c.get("tabs") or [])
        note("state file inventory (after in-tab navigation)", {"tabs": recorded_c})
        live_ids = {p[0] for p in transport_pairs_c}
        stale_first_url = {(tid, url) for tid, url in recorded_c
                           if tid in live_ids and url != url_c}
        checks["post_act_refresh_upserts_recorded_url"] = (
            sorted(recorded_c) == sorted(transport_pairs_c) and not stale_first_url)
        rc_nav_tabs, nav_tabs_payload = _cli_json("browser", "tabs")
        cli_pairs_c = _pairs(nav_tabs_payload.get("tabs"))
        commands.append({"cmd": "browser tabs (after in-tab navigation, upsert)",
                         "exit": rc_nav_tabs, "tabs": cli_pairs_c})
        checks["cli_tabs_show_refreshed_url"] = (
            rc_nav_tabs == 0 and cli_pairs_c == recorded_c
            and not {(tid, url) for tid, url in cli_pairs_c
                     if tid in live_ids and url != url_c})

        # -- 4. out-of-band death: the state file keeps claiming the tab ------------ #
        state_before_death = state_pairs()   # AFTER the 3b upsert — the honest baseline
        _close_session(session)
        time.sleep(1.0)
        verdict = backend.session_alive(session)
        note("out-of-band death", {"session_alive": verdict})
        stale_claim = state_pairs()
        checks["out_of_band_death_is_measured"] = verdict is False
        checks["defect_precondition_stale_inventory"] = stale_claim == state_before_death

        # -- 5. the relaunched transport call is observed and RECORDED -------------- #
        seen = backend.tabs(session=session)
        seen_pairs = _pairs(seen.get("tabs") or [])
        note("transport tab list (auto-relaunch)",
             {"relaunched": seen.get("relaunched"), "tabs": seen_pairs})
        checks["relaunch_surfaced_by_transport"] = seen.get("relaunched") is True
        recorded_relaunch = manager.record_observed_relaunch(
            session_id=session, detail="after-proof: tab list auto-launched the browser")
        note("record_observed_relaunch (FIXED)", {
            "starts": recorded_relaunch.get("starts"),
        })
        relaunch = json.loads(
            (session_state_dir() / "browser-session.json").read_text(
                encoding="utf-8")).get("last_relaunch_observed") or {}
        checks["relaunch_recorded_in_state_file"] = bool(relaunch.get("at"))
        checks["superseded_ids_name_the_dead_tabs"] = (
            {pair[0] for pair in stale_claim}
            <= set(relaunch.get("superseded_tab_ids") or []))
        ids_now = state_pairs()
        checks["dead_browser_pair_left_the_state_file"] = (
            not set(stale_claim) & set(ids_now))

        # -- 6. the real inventory refresh repopulates with the NEW browser's tabs -- #
        result = tool.handler(do="act", kind="navigate", url=url_b)
        checks["refresh_demand_executed"] = bool(result.get("ok"))
        data = json.loads(
            (session_state_dir() / "browser-session.json").read_text(encoding="utf-8"))
        repopulated = _pairs(data.get("tabs") or [])
        note("state file inventory (refreshed)", {"tabs": repopulated})
        checks["inventory_repopulated_by_real_refresh"] = (
            bool(repopulated) and all(
                t.get("owner") == "agent" for t in (data.get("tabs") or [])))
        transport = backend.tabs(session=session)
        transport_ids = [str(t.get("tab_id") or "") for t in (transport.get("tabs") or [])]
        checks["repopulated_ids_match_the_transport"] = (
            [p[0] for p in repopulated] == transport_ids
            or set(p[0] for p in repopulated) == set(transport_ids))
        checks["no_dead_browser_pair_anywhere"] = not (set(stale_claim) & set(repopulated))

        # -- 7. CLI agreement after the relaunch ------------------------------------ #
        rc_tabs, tabs_payload = _cli_json("browser", "tabs")
        rc_disc, disc_payload = _cli_json("browser", "discover")
        commands.append({"cmd": "browser tabs (after relaunch, FIXED)", "exit": rc_tabs,
                         "tabs": _pairs(tabs_payload.get("tabs"))})
        commands.append({"cmd": "browser discover (after relaunch, FIXED)",
                         "exit": rc_disc, "tab_count": disc_payload.get("tab_count")})
        checks["cli_reports_no_phantom_tabs"] = (
            rc_tabs == 0 and not (set(stale_claim) & set(_pairs(tabs_payload.get("tabs")))))
        checks["cli_tab_count_agrees_with_transport"] = (
            rc_disc == 0
            and int(disc_payload.get("tab_count") or 0) == len(transport_ids))

        # -- 8. VAL-WEB-003 live: 10 concurrent ensure demands, no extra start ------ #
        starts_before = int(data.get("starts", 0) or 0)
        with ThreadPoolExecutor(max_workers=10) as pool:
            outcomes = list(pool.map(lambda _: _cli_json("browser", "ensure"), range(10)))
        exits = [rc for rc, _ in outcomes]
        actions = [str(p.get("action") or "") for _, p in outcomes]
        _, disc_after = _cli_json("browser", "discover")
        commands.append({"cmd": "10x browser ensure concurrent", "exits": exits,
                         "actions": actions})
        checks["val_web_003_merge_live"] = (
            all(rc == 0 for rc in exits)
            and all(a in ("reused", "reconciled") for a in actions)
            and int(disc_after.get("starts", 0) or 0) == starts_before)

        # -- 9. detach never closes anything ---------------------------------------- #
        rc_det, detach_payload = _cli_json("browser", "detach")
        commands.append({"cmd": "browser detach", "exit": rc_det})
        transport_after = backend.tabs(session=session)
        checks["detach_closes_nothing"] = (
            rc_det == 0 and detach_payload.get("foreign_browsers_closed") is False
            and len(transport_after.get("tabs") or []) == len(transport_ids))

        print(json.dumps({"checks": checks, "commands": commands, "log": log},
                         sort_keys=True, indent=2, default=str))
        return 0 if all(bool(v) for v in checks.values()) else 1
    finally:
        # cleanup: only our own named session — never `close --all`
        _close_session(session)


if __name__ == "__main__":
    sys.exit(main())
