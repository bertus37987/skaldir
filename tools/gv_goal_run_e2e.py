#!/usr/bin/env python3
"""Long-project end-to-end run **through the product command** ``skaldir run``.

    .venv/bin/python tools/gv_goal_run_e2e.py                    # both scenarios
    .venv/bin/python tools/gv_goal_run_e2e.py --scenario achievable
    .venv/bin/python tools/gv_goal_run_e2e.py --keep             # keep the scratch run dir

``tools/gv_long_project_e2e.py`` proved the pieces with its own runtime glue (the harness
decided what happens after a rejected completion). This one is the same scenario, the same
fixture (``tests/fixtures/long_project``) and the same deterministic loopback stub — but
**the glue is now the product**: every phase is a real ``python -m skaldir run …`` child
process, and the process is SIGKILLed mid-flight so the goal has to survive it.

========================================================  ==================================
what the product command is asked to do                    who answers for it
========================================================  ==================================
``skaldir run "<goal>" --criteria …`` — goal, criteria,     :mod:`skaldir.goal_run`
budget, checkpoint, cursor; plan turn; work turns;
declared checks executed; the completion gate; the
redirect after a rejection; the final verdict
``skaldir run --resume <goal-id>`` — continue the goal     ``runs_store`` checkpoint + cursor
from its durable checkpoint after the crash                + :mod:`skaldir.checkpoints`
the stub provider (loopback OpenAI-compatible, scripted)   ``tools/gv_long_project_e2e``
the fixture project + the two overlays                     ``tests/fixtures/long_project``
========================================================  ==================================

The scenario: a small project whose ``slugify`` is wrong. Three criteria, each certified by
its own declared check (the suite · the CLI · the packaged artifact). The stub's first work
turn applies a deliberately PARTIAL fix, so the project's own pytest run really fails while
the model claims success — and the runtime must refuse that claim, keep the goal open, and
continue. Then the process is killed; a second ``skaldir run --resume`` takes the goal over
from the durable checkpoint and finishes it.

The control (``impossible``) is the same run with one criterion that cannot be met here
(``api``: an upstream release manifest with no network). It must end ``BLOCKED_EXTERNAL``
with the unmet criterion named and the two achievable criteria still verified — never a
pass, never a ``VERIFIED_COMPLETE``.

Exit code 0 = every expectation of both scenarios held; 2 = at least one did not (the
report names it); 3 = the harness could not run the scenario at all.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
DEFAULT_OUT = ROOT / "outputs" / "goal-run-e2e"
SCRATCH_ROOT = Path(os.environ.get("GV_GR_SCRATCH", Path.home() / "gv-goal-run-e2e"))

#: The product's own session/contract id for this scenario (one goal per session).
SESSION = "goal-run-e2e"
STUB_MODEL = "stub-long-project-deterministic"
STUB_KEY = "stub-local-key"  # a loopback stand-in, never a real key

#: The same context budget the harness used: the stub reports a real prompt_tokens per
#: request, so the trigger fires on a measured number and the history is really compacted.
CONTEXT_BUDGET_TOKENS = 900
CONTEXT_TRIGGER_FRACTION = 0.30

#: The turn the crash is aimed at: the first WORK turn (index 1) is the one whose completion
#: request must be rejected before the process dies.
KILL_AT_TURN_INDEX = 1


# --------------------------------------------------------------------------- #
# the harness we port from (reused, not re-implemented)                         #
# --------------------------------------------------------------------------- #


def _load_harness():
    """Import ``tools/gv_long_project_e2e.py`` by path: fixture, stub, edits, scenarios."""
    path = Path(__file__).resolve().parent / "gv_long_project_e2e.py"
    spec = importlib.util.spec_from_file_location("gv_long_project_e2e_port_source", path)
    if spec is None or spec.loader is None:  # pragma: no cover — a missing file
        raise RuntimeError(f"cannot import the harness at {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


HARNESS = _load_harness()
SCENARIOS = HARNESS.SCENARIOS
sha256_file = HARNESS.sha256_file
write_json = HARNESS.write_json
read_json = HARNESS.read_json


# --------------------------------------------------------------------------- #
# small helpers                                                                #
# --------------------------------------------------------------------------- #


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def process_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except OSError:
        return False
    return True


def child_env(run_dir: Path, base_url: str) -> dict[str, str]:
    """The environment of one product child: a loopback provider and a private runtime.

    Nothing of the parent's own configuration leaks in: the provider is the stub's
    base URL, the runtime dir is this run's scratch state, and no real key is anywhere.
    """
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(Path.home()),
        "PYTHONPATH": str(SRC),
        "PYTHONUNBUFFERED": "1",
        "LC_ALL": "C.UTF-8",
        "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
        "SKALDIR_HOME": str(run_dir / "state"),
        "SKALDIR_LLM_BASE_URL": base_url,
        "SKALDIR_LLM_MODEL": STUB_MODEL,
        "SKALDIR_LLM_API_KEY": STUB_KEY,
        "SKALDIR_LLM_PROVIDER": "openai-compatible",
    }
    return env


def run_cli(
    run_dir: Path,
    base_url: str,
    argv: list[str],
    *,
    phase: str,
    timeout: float = 900.0,
) -> dict[str, Any]:
    """One product invocation: ``python -m skaldir run …``, captured and parsed.

    The child's stdout carries exactly one JSON envelope (``--json``); its stderr carries
    the human lines and the live progress. The raw bytes of both are kept on disk, so no
    claim in the report has to be taken on trust.
    """
    command = [sys.executable, "-m", "skaldir", *argv]
    out_path = run_dir / f"child-{phase}.stdout"
    err_path = run_dir / f"child-{phase}.stderr"
    started = time.time()
    with open(out_path, "wb") as out, open(err_path, "wb") as err:
        proc = subprocess.Popen(
            command,
            env=child_env(run_dir, base_url),
            stdout=out,
            stderr=err,
            cwd=str(run_dir),
        )
        returncode = proc.wait(timeout=timeout)
    stdout = out_path.read_text(encoding="utf-8", errors="replace")
    envelope: dict[str, Any] = {}
    for line in reversed(stdout.splitlines()):
        text = line.strip()
        if not text.startswith("{"):
            continue
        try:
            candidate = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict) and "schema_version" in candidate:
            envelope = candidate
            break
    return {
        "phase": phase,
        "argv": command,
        "returncode": int(returncode),
        "duration_s": round(time.time() - started, 3),
        "stdout_path": str(out_path),
        "stderr_path": str(err_path),
        "envelope": envelope,
        "data": envelope.get("data") or {},
        "exit_code": envelope.get("exit_code"),
        "error": envelope.get("error"),
    }


def spawn_cli(run_dir: Path, base_url: str, argv: list[str], phase: str) -> dict[str, Any]:
    """Start one product child and hand back the handle (used when we intend to kill it)."""
    command = [sys.executable, "-m", "skaldir", *argv]
    out_path = run_dir / f"child-{phase}.stdout"
    err_path = run_dir / f"child-{phase}.stderr"
    out = open(out_path, "wb")  # noqa: SIM115 — handed to the process, closed on kill/wait
    err = open(err_path, "wb")  # noqa: SIM115
    proc = subprocess.Popen(
        command,
        env=child_env(run_dir, base_url),
        stdout=out,
        stderr=err,
        cwd=str(run_dir),
    )
    return {
        "proc": proc,
        "argv": command,
        "stdout_path": str(out_path),
        "stderr_path": str(err_path),
        "handles": (out, err),
        "started": time.time(),
    }


def kill_child(handle: dict[str, Any]) -> dict[str, Any]:
    """SIGKILL the product child and prove it is gone (the crash, measured)."""
    proc: subprocess.Popen = handle["proc"]
    pid = proc.pid
    os.kill(pid, signal.SIGKILL)
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        pass
    time.sleep(0.4)
    for stream in handle.get("handles") or ():
        try:
            stream.close()
        except Exception:  # noqa: BLE001 — a closed pipe is not a failure
            pass
    return {
        "pid": pid,
        "signal": "SIGKILL",
        "returncode": proc.returncode,
        "alive_after_kill": process_alive(pid),
        "requested_at": now_iso(),
    }


# --------------------------------------------------------------------------- #
# reading the product's own durable records back                                #
# --------------------------------------------------------------------------- #


def checkpoint_rows(state_dir: Path) -> list[dict[str, Any]]:
    """Every checkpoint row of the run store with its payload, oldest first."""
    db_path = state_dir / "runs.db"
    if not db_path.is_file():
        return []
    try:
        connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10.0)
    except sqlite3.Error:
        return []
    try:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT * FROM checkpoints ORDER BY seq"
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        connection.close()
    out: list[dict[str, Any]] = []
    for row in rows:
        record = dict(row)
        payload: dict[str, Any] = {}
        try:
            document = json.loads(Path(str(record.get("path"))).read_text(encoding="utf-8"))
            payload = document.get("payload") or {}
        except (OSError, json.JSONDecodeError):
            payload = {}
        out.append(
            {
                "checkpoint_id": record.get("checkpoint_id"),
                "seq": record.get("seq"),
                "next_action": record.get("next_action"),
                "sha256": record.get("sha256"),
                "path": record.get("path"),
                "payload": payload,
            }
        )
    return out


def wait_for_turn_checkpoint(
    state_dir: Path, turn_index: int, proc: subprocess.Popen, timeout: float
) -> dict[str, Any]:
    """Block until the run has checkpointed turn ``turn_index`` (or the child dies)."""
    deadline = time.time() + timeout
    seen: list[dict[str, Any]] = []
    while time.time() < deadline:
        seen = checkpoint_rows(state_dir)
        for row in seen:
            payload = row.get("payload") or {}
            if int(payload.get("turn_index", -1)) == int(turn_index):
                return row
        if proc.poll() is not None:
            raise RuntimeError(
                f"the child exited early (rc={proc.returncode}) before turn "
                f"{turn_index + 1} was checkpointed"
            )
        time.sleep(0.1)
    raise RuntimeError(
        f"timed out waiting for the turn {turn_index + 1} checkpoint "
        f"(seen: {json.dumps([row.get('payload') for row in seen], default=str)[:400]})"
    )


def read_goal(state_dir: Path, goal_id: str) -> dict[str, Any]:
    """The durable goal record, its criteria, the cursor and the writer lease."""
    sys.path.insert(0, str(SRC))
    from skaldir.runs_store import RunsStore

    store = RunsStore(str(state_dir / "runs.db"), create=False)
    try:
        goal = store.goal(goal_id) or store.resolve_goal()
        if goal is None:
            return {}
        return {
            "goal_id": goal.goal_id,
            "state": goal.state,
            "goal_text": goal.goal_text,
            "revision": int(goal.revision),
            "workspace_fingerprint": goal.workspace_fingerprint,
            "blocked_reason": goal.blocked_reason,
            "criteria": [
                {
                    "criterion_id": item.criterion_id,
                    "text": item.text,
                    "blocking": bool(item.blocking),
                    "plan": item.verification_plan,
                    "status": item.status,
                    "verified_by": item.verified_by,
                }
                for item in store.criteria(goal.goal_id)
            ],
            "cursor": store.cursor(goal.goal_id),
            "writer": store.writer(),
            "checkpoints": [
                {
                    "checkpoint_id": row.get("checkpoint_id"),
                    "seq": row.get("seq"),
                    "next_action": row.get("next_action"),
                }
                for row in store.checkpoints(goal.goal_id)
            ],
        }
    finally:
        store.close()


def read_contract(state_dir: Path) -> dict[str, Any]:
    """The completion contract's own state and timeline (the durable second record)."""
    sys.path.insert(0, str(SRC))
    from skaldir.completion import CompletionStore

    store = CompletionStore(state_dir, session_id=SESSION)
    state = store.state()
    timeline = store.timeline(limit=500)
    results = [row for row in timeline if row.get("kind") == "verification"]
    return {
        "state": state.get("state"),
        "revision": int(state.get("revision") or 0),
        "goal": state.get("goal"),
        "timeline_len": len(timeline),
        "kinds": sorted({str(row.get("kind")) for row in timeline}),
        "results": [
            {
                "attempt_id": row.get("attempt_id"),
                "status": row.get("status"),
                "blocking_failures": list(row.get("blocking_failures") or [])[:3],
            }
            for row in results
        ],
    }


def read_verify_runs(state_dir: Path) -> list[dict[str, Any]]:
    """Every recorded verification run of this state dir, oldest first."""
    sys.path.insert(0, str(SRC))
    from skaldir.verify_service import VerifyStore

    store = VerifyStore(state_dir / "verify")
    runs = store.runs(limit=50)
    return list(reversed(runs))


# --------------------------------------------------------------------------- #
# one scenario                                                                 #
# --------------------------------------------------------------------------- #


def run_scenario(scenario_key: str, *, keep: bool, out_root: Path) -> dict[str, Any]:
    scenario = SCENARIOS[scenario_key]
    run_dir = SCRATCH_ROOT / f"{scenario_key}-{time.strftime('%Y%m%dT%H%M%S')}"
    project = run_dir / "project"
    state_dir = run_dir / "state"
    run_dir.mkdir(parents=True, exist_ok=True)
    state_dir.mkdir(parents=True, exist_ok=True)

    report: dict[str, Any] = {
        "scenario": scenario_key,
        "outcome_expected": scenario["outcome"],
        "run_dir": str(run_dir),
        "project": str(project),
        "state_dir": str(state_dir),
        "started_at": now_iso(),
        "goal": scenario["goal"],
        "criteria": scenario["criteria"],
        "phases": {},
        "measures": {},
        "checks": [],
        "checks_failed": [],
    }

    HARNESS._install_scratch_project(project, scenario_key)
    baseline_slug = sha256_file(project / "textkit" / "slug.py")
    report["baseline_slug_sha256"] = baseline_slug

    control = HARNESS.StubControl(run_dir / "stub-A.json", run_dir / "stub.jsonl")
    stub = HARNESS.StubProvider(control)
    report["stub"] = {"base_url": stub.base_url, "model": STUB_MODEL}
    try:
        base_url = stub.base_url
        common = [
            "--workspace", str(project),
            "--session", SESSION,
            "--context-budget", str(CONTEXT_BUDGET_TOKENS),
            "--context-trigger", str(CONTEXT_TRIGGER_FRACTION),
            "--preserve-recent", "1",
            "--compaction-asker", "fixture",
            "--probability", "0.05",
            "--max-steps", "14",
            "--check-timeout", "300",
            "--json",
        ]

        # ---------------- phase A: plan + work + a rejected claim ------------- #
        control.set_script(
            [action for turn in scenario["turn_a"] for action in turn["actions"]]
        )
        handle = spawn_cli(
            run_dir, base_url,
            ["run", scenario["goal"], *common, "--max-turns", "6"],
            "A",
        )
        report["phases"]["A_argv"] = handle["argv"]
        checkpoint_a = wait_for_turn_checkpoint(
            state_dir, KILL_AT_TURN_INDEX, handle["proc"], timeout=600.0
        )
        report["phases"]["A_checkpoint"] = checkpoint_a
        report["phases"]["kill"] = kill_child(handle)
        report["measures"]["wall_time_s_until_kill"] = round(time.time() - handle["started"], 3)

        goal = read_goal(state_dir, "")
        goal_id = str(goal.get("goal_id") or "")
        report["goal_id"] = goal_id
        report["phases"]["A_goal"] = goal
        report["phases"]["A_contract"] = read_contract(state_dir)
        report["phases"]["A_verify_runs"] = read_verify_runs(state_dir)
        report["phases"]["slug_sha256_at_crash"] = sha256_file(project / "textkit" / "slug.py")

        # ---------------- phase B: resume from the checkpoint ----------------- #
        control.set_script(
            [action for turn in scenario["turn_b"] for action in turn["actions"]]
        )
        resume = run_cli(
            run_dir, base_url,
            ["run", "--resume", goal_id, *common, "--max-turns", "4"],
            phase="B",
        )
        report["phases"]["B"] = resume
        data = resume.get("data") or {}
        report["phases"]["B_turns"] = [
            {
                "name": row.get("name"),
                "status": row.get("status"),
                "steps": row.get("steps"),
                "tool_calls": row.get("tool_calls"),
                "edits": [item.get("verdict") for item in row.get("edits") or []],
                "test_runs": [item.get("status") for item in row.get("test_runs") or []],
                "compactions": len(row.get("compactions") or []),
                "answer": str(row.get("answer") or "")[:200],
            }
            for row in data.get("turns") or []
        ]
        report["phases"]["B_completions"] = [
            {
                "turn": row.get("turn"),
                "verdict": row.get("verdict"),
                "accepted": row.get("accepted"),
                "blocking_failures": list(row.get("blocking_failures") or [])[:3],
                "evidence": [
                    {"check_id": item.get("check_id"), "status": item.get("status")}
                    for item in row.get("evidence") or []
                ],
            }
            for row in data.get("completions") or []
        ]
        report["phases"]["B_goal"] = read_goal(state_dir, goal_id)
        report["phases"]["B_contract"] = read_contract(state_dir)
        report["phases"]["B_verify_runs"] = read_verify_runs(state_dir)
        report["phases"]["slug_sha256_at_end"] = sha256_file(project / "textkit" / "slug.py")
        report["phases"]["resume"] = data.get("resume") or {}
        report["phases"]["verify"] = data.get("verify") or {}
        report["phases"]["baseline"] = data.get("baseline") or {}
        report["measures"].update(
            {
                "turns_phase_b": int(data.get("turns_run") or 0),
                "accepted_completions": int(data.get("accepted_completions") or 0),
                "compactions_phase_b": len(data.get("compactions") or []),
                "checkpoints": len(data.get("checkpoints") or []),
                "exit_code_b": resume.get("returncode"),
                "duration_s_phase_b": resume.get("duration_s"),
            }
        )
        report["checks"] = _evaluate(report, scenario)
        report["checks_failed"] = [row["name"] for row in report["checks"] if not row["ok"]]
    finally:
        stub.stop()
        report["finished_at"] = now_iso()

    pack = out_root / scenario_key
    pack.mkdir(parents=True, exist_ok=True)
    write_json(pack / "report.json", report)
    write_json(pack / "metrics.json", report.get("measures") or {})
    _pack_artifacts(pack, run_dir, report)
    if not keep:
        shutil.rmtree(run_dir, ignore_errors=True)
    return report


def _pack_artifacts(pack: Path, run_dir: Path, report: dict[str, Any]) -> None:
    """Keep the raw transcripts of both product invocations next to the verdict.

    A report that only says "it passed" is not evidence: the child's own stdout (the
    envelope), its stderr (the progress lines), the stub's request log, the product's run
    report and the durable stores are copied out before the scratch dir goes away.
    """
    artifacts: list[str] = []
    for name in ("child-A.stdout", "child-A.stderr", "child-B.stdout", "child-B.stderr",
                 "stub.jsonl"):
        source = run_dir / name
        if source.is_file():
            shutil.copy2(source, pack / name)
            artifacts.append(name)
    goal_id = str(report.get("goal_id") or "")
    goal_report = Path(str(report.get("state_dir") or "")) / "goal_run" / f"{goal_id}.json"
    if goal_id and goal_report.is_file():
        shutil.copy2(goal_report, pack / "goal-run-report.json")
        artifacts.append("goal-run-report.json")
    for relative, label in ((Path("runs.db"), "runs.db"),):
        source = Path(str(report.get("state_dir") or "")) / relative
        if source.is_file():
            target = pack / label
            try:
                connection = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
                try:
                    dump = target.with_suffix(".sql")
                    lines = [
                        str(row)
                        for row in connection.iterdump()  # the durable record, as text
                    ]
                    dump.write_text("\n".join(lines) + "\n", encoding="utf-8")
                    artifacts.append(dump.name)
                finally:
                    connection.close()
            except sqlite3.Error:  # a store we cannot read is reported, not invented
                pass
    report["artifacts"] = artifacts
    write_json(pack / "report.json", report)


def _code(value: Any, default: int = -1) -> int:
    """An exit code as an int — ``0`` is a code, not an absence."""
    if value is None:
        return int(default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _evaluate(report: dict[str, Any], scenario: dict[str, Any]) -> list[dict[str, Any]]:
    """Every expectation, measured against what the run actually left behind."""
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, evidence: Any) -> None:
        checks.append({"name": name, "ok": bool(ok), "evidence": evidence})

    phases = report["phases"]
    goal_a = phases.get("A_goal") or {}
    goal_b = phases.get("B_goal") or {}
    contract_a = phases.get("A_contract") or {}
    contract_b = phases.get("B_contract") or {}
    checkpoint_a = (phases.get("A_checkpoint") or {}).get("payload") or {}
    kill = phases.get("kill") or {}
    kill_phase_b = phases.get("B") or {}
    data_b = kill_phase_b.get("data") or {}
    verify = phases.get("verify") or {}
    counts = verify.get("counts") or {}
    baseline_runs = phases.get("A_verify_runs") or []
    baseline = baseline_runs[0] if baseline_runs else {}
    baseline_counts = baseline.get("counts") or {}
    completions = phases.get("B_completions") or []
    accepted = [row for row in completions if row.get("accepted")]
    rejected_b = [row for row in completions if not row.get("accepted")]
    resume = phases.get("resume") or {}
    turn_rows = phases.get("B_turns") or []
    compactions = sum(int(row.get("compactions") or 0) for row in turn_rows)

    add(
        "the product command created the goal with the three declared criteria",
        len(goal_a.get("criteria") or []) == 3,
        {"goal_id": report.get("goal_id"),
         "criteria": [row.get("criterion_id") for row in goal_a.get("criteria") or []]},
    )
    add(
        "the criteria are the declared checks of the workspace plan, not a guess",
        [row.get("plan") for row in goal_a.get("criteria") or []]
        == [item["check"] for item in scenario["criteria"]],
        [row.get("plan") for row in goal_a.get("criteria") or []],
    )
    add(
        "all three criteria were genuinely RED before any edit (the product's own "
        "baseline verification run)",
        str(baseline.get("verdict")) == "FAIL"
        and int(baseline_counts.get("passed") or 0) == 0
        and int(baseline_counts.get("failed") or 0) == 3,
        {"run_id": baseline.get("run_id"), "verdict": baseline.get("verdict"),
         "counts": baseline_counts},
    )
    add(
        "the first work turn applied its edit through the edit-transaction engine",
        bool(checkpoint_a.get("test_runs")) and bool(checkpoint_a.get("edits")),
        {"edits": checkpoint_a.get("edits"), "test_runs": checkpoint_a.get("test_runs")},
    )
    add(
        "the project's own suite really failed after that edit (the runner's own word)",
        any(str(row.get("status")) != "passed" for row in checkpoint_a.get("test_runs") or []),
        checkpoint_a.get("test_runs"),
    )
    add(
        "the runtime REFUSED the claim that followed, with named blockers",
        str(checkpoint_a.get("verdict")) == "INCOMPLETE"
        and bool(checkpoint_a.get("blocking_failures")),
        {"verdict": checkpoint_a.get("verdict"),
         "blocking_failures": checkpoint_a.get("blocking_failures")},
    )
    add(
        "the goal stayed open through the refusal (contract state after the rejection)",
        str(contract_a.get("state")) in ("WORKING", "INCOMPLETE", "REDIRECT", "VERIFYING"),
        {"state": contract_a.get("state"), "results": contract_a.get("results")},
    )
    add(
        "the refused completion is in the durable contract timeline (not memory only)",
        len(contract_a.get("results") or []) >= 1
        and all(str(row.get("status")) != "VERIFIED_COMPLETE"
                for row in contract_a.get("results") or []),
        contract_a.get("results"),
    )
    add(
        "the process was killed for real (SIGKILL, PID gone)",
        kill.get("signal") == "SIGKILL" and kill.get("alive_after_kill") is False,
        kill,
    )
    add(
        "the killed process left a dead writer lease behind (the crash is visible)",
        bool((goal_a.get("writer") or {}).get("held"))
        and (goal_a.get("writer") or {}).get("pid_alive") is False,
        {"writer_after_crash": goal_a.get("writer")},
    )
    add(
        "the phase-A edit survived the crash (the resume continues, it does not restart)",
        bool(phases.get("slug_sha256_at_crash"))
        and phases.get("slug_sha256_at_crash") != report.get("baseline_slug_sha256"),
        {"slug_before": report.get("baseline_slug_sha256"),
         "slug_at_crash": phases.get("slug_sha256_at_crash"),
         "slug_at_end": phases.get("slug_sha256_at_end")},
    )
    add(
        "the resume took the goal over and read its checkpoint back (hash verified)",
        bool(resume.get("checkpoint_id"))
        and resume.get("payload_sha256_matches") is True
        and int(resume.get("next_index") or -1) >= 1,
        {key: resume.get(key) for key in (
            "goal_id", "checkpoint_id", "checkpoint_seq", "checkpoint_next_action",
            "payload_readable", "payload_sha256_matches", "previous_turn", "next_index",
            "prior_rejections")},
    )
    add(
        "the resumed run already knew about the earlier rejection (durable, not memory)",
        int(resume.get("prior_rejections") or 0) >= 1
        and bool(resume.get("blocking_failures")),
        {"prior_rejections": resume.get("prior_rejections"),
         "blocking_failures": resume.get("blocking_failures")},
    )
    add(
        "the resumed process kept working on its own (turns, no user input)",
        int(report["measures"].get("turns_phase_b") or 0) >= 1,
        {"turns_phase_b": report["measures"].get("turns_phase_b"),
         "turns": [row.get("name") for row in turn_rows]},
    )
    add(
        "the context threshold fired and the history was really compacted",
        compactions >= 1,
        {"compactions": compactions,
         "per_turn": [row.get("compactions") for row in turn_rows]},
    )

    if scenario["outcome"] == "verified":
        add(
            "the completion was ACCEPTED only once the checks really passed",
            len(accepted) >= 1
            and all(
                str(item.get("status")) == "passed"
                for row in accepted
                for item in row.get("evidence") or []
            ),
            accepted,
        )
        add(
            "the product command exited 0 (verification PASS)",
            _code(kill_phase_b.get("returncode")) == 0
            and str(verify.get("verdict")) == "PASS",
            {"returncode": kill_phase_b.get("returncode"),
             "exit_code": kill_phase_b.get("exit_code"),
             "verdict": verify.get("verdict")},
        )
        add(
            "VERIFY PASSED on all three criteria, each with its own executed evidence hash",
            str(verify.get("verdict")) == "PASS" and int(counts.get("passed") or 0) == 3,
            {"verdict": verify.get("verdict"), "counts": counts,
             "criteria": [
                 {"criterion_id": row.get("criterion_id"), "check": row.get("check_id"),
                  "status": row.get("status"), "evidence_sha256": row.get("evidence_sha256")}
                 for row in verify.get("criteria") or []
             ]},
        )
        add(
            "the durable goal record ends VERIFIED_COMPLETE with every criterion verified",
            str(goal_b.get("state")) == "VERIFIED_COMPLETE"
            and all(str(row.get("status")) == "verified"
                    for row in goal_b.get("criteria") or []),
            {"goal_state": goal_b.get("state"),
             "criteria": [{"id": row.get("criterion_id"), "status": row.get("status")}
                          for row in goal_b.get("criteria") or []]},
        )
        add(
            "the completion contract ended VERIFIED_COMPLETE too",
            str(contract_b.get("state")) == "VERIFIED_COMPLETE",
            {"state": contract_b.get("state"), "results": contract_b.get("results")},
        )
    else:
        last_all = (completions[-1] if completions else {}) or {}
        add(
            "the unmet criterion's check really ran and really failed (not a skip)",
            any(str(row.get("check_id")) == "api" and str(row.get("status")) != "passed"
                for row in last_all.get("evidence") or []),
            [row for row in last_all.get("evidence") or [] if row.get("check_id") == "api"],
        )
        add(
            "the runtime NEVER claimed success (no accepted completion, ever)",
            not accepted
            and all(str(row.get("verdict")) != "VERIFIED_COMPLETE" for row in completions),
            completions,
        )
        add(
            "the product command exited 3 (blocked) and not 0",
            _code(kill_phase_b.get("returncode")) == 3
            and _code(kill_phase_b.get("exit_code")) == 3,
            {"returncode": kill_phase_b.get("returncode"),
             "exit_code": kill_phase_b.get("exit_code"),
             "error": kill_phase_b.get("error")},
        )
        add(
            "the final completion state is BLOCKED_EXTERNAL with the criterion named",
            str(data_b.get("terminal_state")) == "BLOCKED_EXTERNAL"
            and (
                "api" in str(data_b.get("blocked_reason") or "")
                or "release manifest" in str(data_b.get("blocked_reason") or "")
            ),
            {"terminal_state": data_b.get("terminal_state"),
             "blocked_reason": data_b.get("blocked_reason")},
        )
        add(
            "the independent VERIFY pass reports FAIL — never PASS",
            str(verify.get("verdict")) == "FAIL" and int(counts.get("passed") or 0) == 2,
            {"verdict": verify.get("verdict"), "counts": counts,
             "reason": verify.get("verdict_reason")},
        )
        add(
            "the durable goal record is blocked, never verified",
            str(goal_b.get("state")) == "BLOCKED_EXTERNAL"
            and any(str(row.get("status")) != "verified"
                    for row in goal_b.get("criteria") or []),
            {"goal_state": goal_b.get("state"),
             "criteria": [{"id": row.get("criterion_id"), "status": row.get("status")}
                          for row in goal_b.get("criteria") or []]},
        )
        add(
            "the ledger separates what WAS achieved (2 verified) from what was not (1 failed)",
            len([row for row in goal_b.get("criteria") or []
                 if str(row.get("status")) == "verified"]) == 2
            and len([row for row in goal_b.get("criteria") or []
                     if str(row.get("status")) == "failed"]) == 1,
            goal_b.get("criteria"),
        )
        add(
            "the work that COULD be done was still done (2 of 3 checks green at the end)",
            int(counts.get("passed") or 0) == 2,
            [{"criterion_id": row.get("criterion_id"), "check": row.get("check_id"),
              "status": row.get("status")} for row in verify.get("criteria") or []],
        )
    return checks


# --------------------------------------------------------------------------- #
# entry point                                                                   #
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), default="")
    parser.add_argument("--keep", action="store_true", help="keep the scratch run dir")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))

    keys = [args.scenario] if args.scenario else sorted(SCENARIOS)
    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)
    reports = [run_scenario(key, keep=args.keep, out_root=out_root) for key in keys]
    pack = {
        "generated_at": now_iso(),
        "scenarios": {
            report["scenario"]: {
                "run_dir": report["run_dir"],
                "outcome_expected": report["outcome_expected"],
                "checks": report["checks"],
                "checks_failed": report["checks_failed"],
                "measures": report["measures"],
            }
            for report in reports
        },
    }
    write_json(out_root / "report.json", pack)

    failed = [(report["scenario"], row) for report in reports
              for row in report["checks"] if not row["ok"]]
    for report in reports:
        print(f"scenario {report['scenario']}: "
              f"{len(report['checks']) - len(report['checks_failed'])}/{len(report['checks'])} "
              f"expectations held")
        for row in report["checks"]:
            mark = "ok  " if row["ok"] else "FAIL"
            print(f"  [{mark}] {row['name']}")
            if not row["ok"]:
                print(f"         {json.dumps(row['evidence'], default=str)[:600]}")
    if failed:
        print(f"{len(failed)} expectation(s) did not hold", file=sys.stderr)
        return 2
    print("every expectation of every scenario held")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
