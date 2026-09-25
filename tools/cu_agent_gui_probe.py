#!/usr/bin/env python
"""Agent-driven Computer-Use against a REAL GUI app in the isolated container.

The plan is scripted (deterministic, no provider needed); everything else is real: the container
GUI runner, the app inside it, the input that reaches the app, and the verification, which reads
the app's OWN state. A counter-case pins that a missing effect is never reported as verified.

    .venv/bin/python tools/cu_agent_gui_probe.py
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from godsv.computer_use import (  # noqa: E402
    ContainerRunner,
    CUPolicy,
    _guistate_from_observation,
    run_cu_request,
)
from godsv.events import EventStore  # noqa: E402
from godsv.llm import ScriptedLLM  # noqa: E402

TITLE = "godsv-agent-cu"
APP_FILE = "/tmp/godsv-agent-cu-app.txt"
TYPED = "agent-cu-typed-4711"

results: list[tuple[str, bool, str]] = []


CANDIDATE = "candidate_a"


class _TestEvaluation:
    """A labelled test reading — never a live Jev claim (no TypeSafe credential in the probe)."""

    provider = "llm_system_one_adapter"
    provider_model = "probe-test-adapter"
    live_jev = False
    evaluation_id = "probe-gate"

    def __init__(self, choice: str = "") -> None:
        from godsv.system_one_subtools import CANDIDATE_CHOICE_QUESTION_ID

        class _Answer:
            def __init__(self, choice: str) -> None:
                self.choice = choice

        self.answers = {CANDIDATE_CHOICE_QUESTION_ID: _Answer(choice)} if choice else {}


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), str(detail)[:160]))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{str(detail)[:110]}]" if detail else ""))


def exec_in(runner: ContainerRunner, argv: list[str], detach: bool = False):
    import subprocess

    cmd = ["podman", "exec"]
    if runner.gui:
        cmd += ["-e", f"DISPLAY={runner.display}"]
    if detach:
        cmd += ["-d"]
    cmd += [runner.name] + argv
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120)


def _plan(expect: str) -> str:
    """One API-channel attempt: type into the real app window, then read the app's own state."""

    # focus + real input events: `xdotool type --window` uses XSendEvent, which xterm ignores
    script = (
        f"wid=$(xdotool search --name {TITLE} | head -1); "
        f"xdotool windowactivate --sync $wid || xdotool windowfocus --sync $wid; sleep 1; "
        f"xdotool key --clearmodifiers a; sleep 0.2; "
        f"xdotool type --delay 40 -- {TYPED}; sleep 2; "
        f"xdotool key --clearmodifiers Return; sleep 1; cat {APP_FILE}"
    )
    return json.dumps(
        {
            "attempts": [
                {
                    "channel": "api",
                    "description": "type into the running app and read its state",
                    "action": {"kind": "cli", "argv": ["sh", "-c", script]},
                    "verify": {"expect_stdout_contains": expect},
                }
            ]
        }
    )


def main() -> int:
    runtime = Path(tempfile.mkdtemp(prefix="gv-cu-agent-"))
    runner = ContainerRunner(gui=True, name=f"godsv-cu-agent-{int(time.time()) % 100000}")
    ok, detail = runner.available()
    check("the container GUI image is available", ok, detail)
    if not ok:
        return 1
    events = EventStore(runtime / "events.db")
    try:
        ok, detail = runner.start()
        check("isolated display started (container, Xvfb :90)", ok, detail)
        if not ok:
            return 1
        launch = exec_in(
            runner,
            ["xterm", "-T", TITLE, "-e", "sh", "-c", f"cat > {APP_FILE}"],
            detach=True,
        )
        check("a real app runs inside the isolated display", launch.returncode == 0,
              f"podman exec -d rc={launch.returncode}")
        time.sleep(3)
        observed = runner.observe()
        check("the app is really perceived (window title in the observation)",
              TITLE in observed.text, observed.text.strip()[:90])

        outcome = run_cu_request(
            goal="type a marker into the running app and prove it arrived",
            runtime=runtime,
            client=ScriptedLLM(responses={"cu_step_plan": _plan(TYPED)}),
            runner=runner,
            policy=CUPolicy(),
            events=events,
            session_id="cu-agent-probe",
        )
        check("the run is verified through the app's own state",
              outcome.get("status") == "verified" and outcome.get("verified") is True,
              json.dumps({k: outcome.get(k) for k in ("status", "channel", "actions", "detail")}))
        check("the runner reports the real isolation",
              outcome.get("runner") == "container", str(outcome.get("runner")))
        record = json.loads((runtime / "cu_runs.jsonl").read_text(encoding="utf-8").strip().splitlines()[-1])
        check("the run is recorded like any other panel run",
              record["status"] == "verified" and record["kind"] == "container",
              json.dumps({k: record.get(k) for k in ("status", "kind", "planned")}))
        kinds = [row.kind for row in events.iter_session("cu-agent-probe")]
        check("the run emits the CU runtime events",
              "cu_attempt" in kinds and any(k.startswith("cu_") for k in kinds), ",".join(kinds[:6]))


        # §53 gate on the real path — the state proposes a candidate, the CALLER decides whether it may
        # run. Proven against the real container runner; the evaluation is labelled as a test reading.
        from godsv.system_one_flags import load_system_one_flags
        from godsv.system_one_guistate import GUIState
        from godsv.system_one_subtools import REOBSERVE_TRIGGERS, VisualSubtool

        subtool = VisualSubtool(
            capability_id="probe.type_marker",
            version="1.0",
            supported_app_versions=("1.0",),
            supported_locales=("en",),
            start_conditions=("app_running",),
            anchors=(TITLE,),
            allowed_actions=("type_marker",),
            expected_transitions=("marker_typed",),
            completion_conditions=(TYPED,),
            failure_conditions=("no_marker",),
            reobserve_policy=(REOBSERVE_TRIGGERS[0],),
            system2_escalation_policy=("insufficient_state",),
            verification_contract="the file the app writes holds the typed marker",
        )

        gate_home = runtime / "gate-home"
        gate_home.mkdir(parents=True, exist_ok=True)
        (gate_home / "settings.yaml").write_text(
            "system_one:\n  enabled: true\n  provider: llm_system_one_adapter\n"
            "  computer_use: true\n",
            encoding="utf-8",
        )
        gate_flags = load_system_one_flags(gate_home)
        # a fresh state, built from the runner's REAL perception: the fingerprint is content-bound, so
        # patching a state (dataclasses.replace) is refused by the contract — correctly so
        perceived = _guistate_from_observation(observed)
        gate_state = GUIState(
            gui_state_id="probe-gate-state",
            fields=perceived.fields,
            action_candidates=(CANDIDATE,),
            pre_approved_candidates=(CANDIDATE,),
            channel=perceived.channel,
            live_display=False,
        )
        gated = run_cu_request(
            goal="type a marker into the running app and prove it arrived",
            runtime=runtime,
            client=ScriptedLLM(responses={"cu_step_plan": _plan(TYPED)}),
            runner=runner,
            policy=CUPolicy(),
            guistate=gate_state,
            flags=gate_flags,
            evaluation=_TestEvaluation(),
            pre_approved_candidates=(),  # the caller approves nothing
        )
        check(
            "gate: state proposes, caller approves nothing → blocked before any action",
            gated.get("status") == "blocked"
            and gated.get("verified") is False
            and "actions" not in gated,
            json.dumps({k: gated.get(k) for k in ("status", "reason")})[:120],
        )
        check(
            "gate: the record never widens the kinds and never certifies",
            bool(gated["system_one"]["allowed_kinds"])
            and gated["system_one"]["mutates"] is False
            and gated["system_one"]["certifies_effect"] is False
            and gated["system_one"]["action_candidates"] == (),
            json.dumps(
                {
                    k: gated["system_one"][k]
                    for k in ("allowed_kinds", "action_candidates", "certifies_effect")
                }
            ),
        )
        approved = run_cu_request(
            goal="type a marker into the running app and prove it arrived",
            runtime=runtime,
            client=ScriptedLLM(responses={"cu_step_plan": _plan(TYPED)}),
            runner=runner,
            policy=CUPolicy(),
            guistate=gate_state,
            flags=gate_flags,
            evaluation=_TestEvaluation(),
            pre_approved_candidates=(CANDIDATE,),
            post_guistate=gate_state,
        )
        check(
            "gate: an approved candidate runs and the advisory travels with the result",
            approved.get("status") == "verified"
            and approved["system_one"]["action_candidates"] == (CANDIDATE,)
            and approved["post_system_one"]["certifies_effect"] is False,
            json.dumps({k: approved.get(k) for k in ("status", "planned")}),
        )


        # the "refuse or escalate instead of act" half, on the same real runner
        refused = run_cu_request(
            goal="type a marker into the running app and prove it arrived",
            runtime=runtime,
            client=ScriptedLLM(responses={"cu_step_plan": _plan(TYPED)}),
            runner=runner,
            policy=CUPolicy(),
            guistate=gate_state,
            flags=gate_flags,
            evaluation=_TestEvaluation("candidate_zzz"),
            pre_approved_candidates=(CANDIDATE,),
        )
        check(
            "choice: a provider answer naming an unallowed candidate is refused",
            refused.get("status") == "blocked"
            and "not among the already-allowed options" in str(refused.get("reason"))
            and "actions" not in refused,
            str(refused.get("reason"))[:100],
        )
        escalated = run_cu_request(
            goal="type a marker into the running app and prove it arrived",
            runtime=runtime,
            client=ScriptedLLM(responses={"cu_step_plan": _plan(TYPED)}),
            runner=runner,
            policy=CUPolicy(),
            guistate=gate_state,
            flags=gate_flags,
            evaluation=_TestEvaluation(CANDIDATE),
            pre_approved_candidates=(CANDIDATE,),
            escalation_check=True,
        )
        check(
            "escalation: an unmeasured reading hands over to System 2 instead of acting",
            escalated.get("status") == "blocked"
            and escalated["system_two"]["escalate"] is True
            and escalated["system_two"]["authorizes_action"] is False,
            json.dumps(
                {
                    "status": escalated.get("status"),
                    "triggers": escalated["system_two"]["triggers"][:3],
                }
            )[:120],
        )


        # the §11/§56 subtool guard on the real runner: the anchor comes from the REAL observation
        sub_ok = run_cu_request(
            goal="type a marker into the running app and prove it arrived",
            runtime=runtime,
            client=ScriptedLLM(responses={"cu_step_plan": _plan(TYPED)}),
            runner=runner,
            policy=CUPolicy(),
            subtool=subtool,
            app_version="1.0",
            locale="en",
            observed_start_conditions=("app_running",),
            observed_anchors=(TITLE,),  # the title really is on screen (checked above)
        )
        check(
            "subtool: an anchor that is really observed lets the step run and verify",
            sub_ok.get("status") == "verified"
            and sub_ok["subtool"]["proceed"] is True
            and sub_ok["subtool"]["applicability"]["applicable"] is True,
            json.dumps(
                {k: sub_ok.get(k) for k in ("status", "planned")}
            )[:110],
        )
        sub_bad = run_cu_request(
            goal="type a marker into the running app and prove it arrived",
            runtime=runtime,
            client=ScriptedLLM(responses={"cu_step_plan": _plan(TYPED)}),
            runner=runner,
            policy=CUPolicy(),
            subtool=subtool,
            app_version="1.0",
            locale="en",
            observed_start_conditions=("app_running",),
            observed_anchors=("anchor-der-nicht-auf-dem-schirm-ist",),
        )
        check(
            "subtool: an unobserved anchor refuses the replay (nothing is executed)",
            sub_bad.get("status") == "reobserve"
            and sub_bad.get("verified") is False
            and sub_bad.get("missing_anchors") == [TITLE]
            and "actions" not in sub_bad,
            json.dumps(
                {"status": sub_bad.get("status"), "missing": sub_bad.get("missing_anchors")}
            )[:110],
        )

        # counter-case: the effect is missing, so "verified" would be a lie
        missing = run_cu_request(
            goal="type a marker that never arrives",
            runtime=runtime,
            client=ScriptedLLM(responses={"cu_step_plan": _plan("marker-that-never-arrives")}),
            runner=runner,
            policy=CUPolicy(),
        )
        check("counter-case: an absent effect is never verified",
              missing.get("status") != "verified" and missing.get("verified") is False,
              json.dumps({k: missing.get(k) for k in ("status", "detail")}))
        check("counter-case: the host display is never used",
              runner.gui is True and runner.display == ":90" and runner.network is False,
              f"display={runner.display} network={runner.network}")
    finally:
        try:
            events.close()
        except Exception:
            pass
        runner.close()

    failures = [name for name, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failures)}/{len(results)} checks passed")
    if failures:
        print("FAILED: " + "; ".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
