"""Live proof for VAL-TOOL-002: a parameterized GUI subtool on the REAL host desktop.

Steps (own resources only):
1. spawn the probe's own window (``skaldir.probe_target``) — a confirmed binding;
2. save a parameterized subtool whose precondition/anchor is that window's title and whose
   end state is read from the app's own state (the receiver's key log) via a declared probe;
3. ``skaldir act subtool run`` with ``--params`` on the host-session runner;
4. destroy the window (state changed) and re-run with different params: must refuse before acting;
5. tool search finds the subtool with a typed call form.

Usage: uv run python tools/gui_subtool_live_proof.py [--out logs/gui-subtool-live/<ts>.json]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from skaldir import probe_target
from skaldir.discovery import discover

REPO = Path(__file__).resolve().parents[1]
SKALDIR = str(REPO / ".venv" / "bin" / "skaldir")


def _cli(argv: list[str]) -> tuple[int, dict]:
    proc = subprocess.run([SKALDIR, *argv, "--json"], capture_output=True, text=True, timeout=180)
    try:
        doc = json.loads(proc.stdout.strip().splitlines()[-1]) if proc.stdout.strip() else {}
    except (json.JSONDecodeError, IndexError):
        doc = {"raw_stdout": proc.stdout[-2000:]}
    doc["_stderr_tail"] = proc.stderr[-1500:]
    return proc.returncode, doc


def _spec(title: str, log_path: Path, run_id: str) -> dict:
    join = (
        "import json,sys\n"
        "out=''\n"
        "for l in open(sys.argv[1]):\n"
        "    try: r=json.loads(l)\n"
        "    except Exception: continue\n"
        "    if r.get('press'): out+=r.get('char') or ''\n"
        "print(out)\n"
    )
    return {
        "name": "type_into_probe_window",
        "version": "1.0",
        "description": "type a value into the own probe window and verify the app received it",
        "applicability": {"app": "probe", "app_versions": [">=1.0,<2"], "locales": ["en", "*"]},
        "preconditions": [{"id": "target", "text": title, "source": "window_title"}],
        "anchors": [{"id": "target_window", "text": title, "source": "window_title"}],
        "actions": [
            {
                "kind": "type_text",
                "channel": "api",
                "payload": {"text": "{{value}}"},
                "description": "type {{value}} into the probe window",
                "verify": {"expect_observe_contains": title},
            }
        ],
        "end_state": {
            "channel": "app_state",
            "description": "the probe window's own receiver logged the value",
            "expect_observe_contains": "{{value}}",
            "app_state_probe": {"argv": [sys.executable, "-c", join, str(log_path)], "timeout": 5},
        },
        "verification_contract": "the receiver of the target window recorded exactly the value",
        "parameters": {
            "value": {
                "type": "string",
                "required": True,
                "description": "the text to type",
                "arg_ref": "actions[0].payload.text",
            }
        },
        "output_schema": {
            "type": "object",
            "properties": {"status": {"type": "string"}, "verified": {"type": "boolean"}},
            "required": ["status", "verified"],
        },
        "timeout": 45.0,
        "evidence": [
            {
                "ref": f"logs/gui-subtool-live/{run_id}.json",
                "kind": "verified_run",
                "note": "live host-session run verified the end state at the own probe window",
            }
        ],
        "target": {"windows": [title], "paths": []},
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    run_id = time.strftime("%Y%m%d-%H%M%S")
    out = Path(args.out or REPO / "logs" / "gui-subtool-live" / f"{run_id}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    state = Path(f"/tmp/skaldir-subtool-proof-{run_id}")
    state.mkdir(parents=True, exist_ok=True)
    report: dict = {"run_id": run_id, "state": str(state), "steps": {}}

    def flush() -> None:
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False))

    window = probe_target.spawn_probe_window(run=f"subtool-{run_id}")
    b = window.binding
    report["steps"]["spawn"] = b.as_dict()
    flush()
    if not b.confirmed:
        report["verdict"] = "probe-misconfigured"
        window.destroy()
        flush()
        return 3
    try:
        spec_path = state / "spec.json"
        spec_path.write_text(json.dumps(_spec(b.title, b.log_path, run_id)))
        rc, doc = _cli(
            ["act", "subtool", "save", "--file", str(spec_path), "--state", str(state), "--force"]
        )
        report["steps"]["save"] = {"rc": rc, "doc": doc}
        flush()
        live = [
            "--runner",
            "live-desktop",
            "--allow-live-desktop",
            "--live-target",
            b.title,
            "--state",
            str(state),
        ]
        rc, doc = _cli(
            [
                "act",
                "subtool",
                "run",
                "type_into_probe_window",
                "--params",
                json.dumps({"value": "hello42"}),
                *live,
            ]
        )
        report["steps"]["run_1"] = {
            "rc": rc,
            "doc": doc,
            "receiver_chars": "".join(r.char for r in window.records if getattr(r, "press", True)),
        }
        flush()
    finally:
        report["steps"]["destroy"] = window.destroy()
        flush()
    time.sleep(1.0)
    rc, doc = _cli(
        [
            "act",
            "subtool",
            "run",
            "type_into_probe_window",
            "--params",
            json.dumps({"value": "second"}),
            *live,
        ]
    )
    report["steps"]["run_2_altered_state"] = {"rc": rc, "doc": doc}
    flush()
    found = discover("type a value into the own probe window", runtime=str(state), limit=10)
    rows = [r for r in found.get("results", []) if r.get("name") == "type_into_probe_window"]
    report["steps"]["tool_search"] = (
        rows[0]
        if rows
        else {"missing": True, "names": [r.get("name") for r in found.get("results", [])]}
    )
    d1 = report["steps"]["run_1"]["doc"].get("data") or {}
    d2 = report["steps"]["run_2_altered_state"]["doc"].get("data") or {}
    report["verdict"] = {
        "run_1_verified": d1.get("verified"),
        "run_1_status": d1.get("status"),
        "run_2_status": d2.get("status"),
        "run_2_verified": d2.get("verified"),
        "tool_search_hit": bool(rows),
    }
    flush()
    print(json.dumps(report["verdict"], indent=2))
    print(f"evidence: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
