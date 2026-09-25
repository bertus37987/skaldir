"""PERSIST — durability proved by really killing the process (MVP/10 §6.1/§6.2/§6.4).

The acceptance criterion is explicit there: "a real ``kill -9`` run is
explicitly **open** and must be the new evidence". So this test does not simulate
a restart — it starts a goal in a **child process**, writes the operations and the
budget, ``SIGKILL``s that process (no cleanup, no ``finally``, no flush), and then
reads the record back in a **second child process**. Every assertion below is made
on the surviving on-disk state:

* the goal id, revision, criteria and checkpoint are the same ones,
* ``spent``/``reserved``/``remaining`` after the restart equal the values before
  it (the budget ledger's load path — MVP/10 gap 5),
* the in-flight operations classify as ``RESOLVED``/``REPEAT``/``IDLE``/``UNKNOWN``,
  the unprovable one is parked and never retried,
* a second resume does not apply anything twice.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"

CHILD_START = '''
"""Child 1: start a goal, work on it, get killed with SIGKILL."""
import contextlib
import io
import json
import os
import signal
import sys
from pathlib import Path

runtime = Path(sys.argv[1])
from skaldir import runs_cli
from skaldir.runs_store import open_store


def cli(*argv):
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = runs_cli.main([str(item) for item in argv])
    document = json.loads(buffer.getvalue().strip().splitlines()[-1])
    assert code == document["exit_code"], (code, document)
    return code, document


start_code, start = cli(
    "runs", "start", "--goal", "survive a real kill -9", "--session", "durability",
    "--criterion", "the goal survives the kill", "--criterion", "the budget survives the kill",
    "--budget", "main_agent=20000", "--json",
)
assert start_code == 0, start
goal_id = start["data"]["goal_id"]
started_at = start["data"]["checkpoint"]["sha256"]

store = open_store(runtime)

# op A: idempotent, in flight -> REPEAT after the crash
op_a, _ = store.register_operation(
    goal_id, kind="publish", target="registry", payload={"version": "1.0.0"}, idempotent=True
)
assert store.begin_operation(op_a.op_id)

# op B: NOT idempotent, in flight -> UNKNOWN after the crash (parked, never retried)
op_b, _ = store.register_operation(
    goal_id, kind="send_email", target="owner@example", payload={"subject": "shipped"}
)
assert store.begin_operation(op_b.op_id)

# op C: the effect landed and its proof is on disk, but the commit never happened
# (the crash window MVP/10 R4.2 describes: intent + effect, no commit)
op_c, _ = store.register_operation(goal_id, kind="deploy", target="prod", payload={"rev": 1})
assert store.begin_operation(op_c.op_id)
artifact = runtime / "artifacts" / "deploy.txt"
artifact.parent.mkdir(parents=True, exist_ok=True)
artifact.write_text("deployed\\n", encoding="utf-8")
with store.transaction() as db:
    db.execute("UPDATE operations SET result_ref = ? WHERE op_id = ?", (str(artifact), op_c.op_id))
committed = {"result_ref": str(artifact)}

# op D: intent row only, nothing was in flight -> IDLE
op_d, _ = store.register_operation(
    goal_id, kind="draft_notes", target="readme", payload={"n": 1}
)

ledger = store.ledger(goal_id)
first = ledger.reserve("main_agent", 5000)
ledger.spend("main_agent", 1200, reservation=first["reservation_id"], note="turn 1")
second = ledger.reserve("main_agent", 8000)
ledger.spend("main_agent", 3000, reservation=second["reservation_id"], note="turn 2")
budget_before = ledger.state()["main_agent"]

checkpoint_code, checkpoint = cli(
    "runs", "checkpoint", "--goal-id", goal_id, "--next-action", "finish criterion c1",
    "--last-event-seq", "17", "--json",
)
assert checkpoint_code == 0, checkpoint

store.close()
print(json.dumps({
    "goal_id": goal_id,
    "revision": start["data"]["revision"],
    "store": start["data"]["store"],
    "checkpoint_sha256": started_at,
    "ops": {
        "a": {"op_id": op_a.op_id, "key": op_a.idempotency_key},
        "b": {"op_id": op_b.op_id, "key": op_b.idempotency_key},
        "c": {"op_id": op_c.op_id, "key": op_c.idempotency_key,
              "result_ref": committed["result_ref"]},
        "d": {"op_id": op_d.op_id, "key": op_d.idempotency_key},
    },
    "budget_before": budget_before,
    "checkpoint": checkpoint["data"],
}))
sys.stdout.flush()
os.kill(os.getpid(), signal.SIGKILL)   # no cleanup: the process dies mid-flight
'''

CHILD_RESUME = '''
"""Child 2 (fresh process): resume the goal that child 1 was killed in."""
import contextlib
import io
import json
import sys
from pathlib import Path

runtime = Path(sys.argv[1])
from skaldir import runs_cli
from skaldir.budget import BudgetAccount
from skaldir.runs_store import open_store

buffer = io.StringIO()
with contextlib.redirect_stdout(buffer):
    code = runs_cli.main(["runs", "resume", "--json", "--restore-budget"])
envelope = json.loads(buffer.getvalue().strip().splitlines()[-1])
assert code == envelope["exit_code"], (code, envelope)

store = open_store(runtime)
goal_id = envelope["data"]["goal_id"]
ops = {item.op_id: item.to_dict() for item in store.operations(goal_id)}
checkpoint = store.latest_checkpoint(goal_id)
read_back = store.checkpoint_payload(checkpoint)
account = BudgetAccount(goal_id=goal_id,
                        limits={"main_agent": store.budget_state(goal_id)["main_agent"]["limit"]})
replay = store.restore_budget_account(account, goal_id)
goal = store.require_goal(goal_id)
print(json.dumps({
    "exit_code": code,
    "resume": envelope["data"],
    "ops": ops,
    "checkpoint": checkpoint,
    "checkpoint_sha256_ok": read_back["sha256_ok"],
    "goal": {"goal_id": goal.goal_id, "revision": goal.revision, "state": goal.state,
             "goal": goal.goal_text, "criteria": [item.criterion_id
                                                  for item in store.criteria(goal_id)]},
    "replay": replay,
    "account": {"spent": account.spent("main_agent"), "reserved": account.reserved("main_agent"),
                "remaining": account.remaining("main_agent")},
    "ledger_entries": store.ledger(goal_id).entries(),
    "operation_rows": len(store.operations()),
}))
'''

CHILD_RESUME_TWICE = '''
"""Child 3: a third process resumes again — nothing may be applied a second time."""
import contextlib
import io
import json
import sys
from pathlib import Path

runtime = Path(sys.argv[1])
from skaldir import runs_cli
from skaldir.runs_store import open_store

buffer = io.StringIO()
with contextlib.redirect_stdout(buffer):
    code = runs_cli.main(["runs", "resume", "--json"])
envelope = json.loads(buffer.getvalue().strip().splitlines()[-1])
store = open_store(runtime)
print(json.dumps({
    "exit_code": code,
    "data": envelope["data"],
    "ops": {item.op_id: {"state": item.state, "attempt": item.attempt,
                         "side_effect_state": item.side_effect_state,
                         "result_ref": item.result_ref, "wake_source": item.wake_source}
            for item in store.operations()},
    "operation_rows": len(store.operations()),
    "receipts": sorted(path.name for path in (runtime / "receipts" / "operations").glob("*.json")),
}))
'''


@pytest.fixture(scope="module")
def killed_run(tmp_path_factory):
    """Run child 1 (SIGKILLed) once and hand its record to every test below."""
    runtime = tmp_path_factory.mktemp("godsv-runtime")
    script = runtime / "child_start.py"
    script.write_text(CHILD_START, encoding="utf-8")
    env = {
        **os.environ,
        "PYTHONPATH": str(SRC),
        "GODSV_HOME": str(runtime),
        "HERMES_HOME": str(runtime.parent / "hermes-home"),
    }
    completed = subprocess.run(
        [sys.executable, str(script), str(runtime)],
        capture_output=True,
        text=True,
        env=env,
        timeout=180,
    )
    assert completed.returncode == -signal.SIGKILL, (
        f"child 1 was expected to die from SIGKILL, got {completed.returncode}\n"
        f"stdout: {completed.stdout}\nstderr: {completed.stderr}"
    )
    before = json.loads(completed.stdout.strip().splitlines()[-1])
    return {"runtime": runtime, "env": env, "before": before}


@pytest.fixture(scope="module")
def resumed(killed_run):
    """Run child 2 in a fresh process and hand back what it read."""
    runtime = killed_run["runtime"]
    script = runtime / "child_resume.py"
    script.write_text(CHILD_RESUME, encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, str(script), str(runtime)],
        capture_output=True,
        text=True,
        env=killed_run["env"],
        timeout=180,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout.strip().splitlines()[-1])


def test_the_kill_is_real_and_the_store_survives_it(killed_run):
    assert killed_run["before"]["goal_id"]
    store_file = Path(killed_run["before"]["store"])
    assert store_file.is_file() and store_file.name == "runs.db"
    assert killed_run["before"]["ops"]["a"]["op_id"]


def test_the_goal_survived_a_sigkill(resumed):
    """Resumability (§6.2): the same record, in a process that never saw child 1."""
    assert resumed["goal"]["goal_id"] == resumed["resume"]["goal_id"]
    assert resumed["goal"]["revision"] == 1
    assert resumed["goal"]["state"] == "WORKING"
    assert resumed["goal"]["goal"] == "survive a real kill -9"
    assert resumed["goal"]["criteria"] == ["c1", "c2"]
    assert resumed["resume"]["next_action"] == "finish criterion c1"
    assert resumed["resume"]["open_criteria"] == ["c1", "c2"]
    assert resumed["checkpoint"]["seq"] == 2
    assert resumed["checkpoint"]["last_event_seq"] == 17
    assert resumed["checkpoint_sha256_ok"] is True
    assert resumed["resume"]["checkpoint"]["sha256_ok"] is True


def test_the_budget_survived_and_the_load_path_restores_it(killed_run, resumed):
    """Budget continuity (§6.4): the in-memory account was killed with child 1."""
    before = killed_run["before"]["budget_before"]
    after = resumed["resume"]["budget"]["main_agent"]
    assert (before["spent"], before["reserved"], before["limit"]) == (4200, 8800, 20000)
    assert (after["spent"], after["reserved"], after["remaining"]) == (4200, 8800, 7000)
    assert after["spent"] + after["reserved"] <= after["limit"]

    replay = resumed["replay"]
    assert replay["restored"] is True, replay
    assert replay["entries"] == 4  # reserve, spend, reserve, spend
    assert replay["matches"]["main_agent"] is True
    assert resumed["account"] == {"spent": 4200, "reserved": 8800, "remaining": 7000}
    assert resumed["resume"]["budget_restored"]["restored"] is True
    assert [entry["action"] for entry in resumed["ledger_entries"]] == [
        "reserve", "spend", "reserve", "spend",
    ]


def test_every_in_flight_operation_is_classified_exactly_once(killed_run, resumed):
    """§6.1: one effect, one continuation — RESOLVED/REPEAT/IDLE/UNKNOWN."""
    ids = {name: item["op_id"] for name, item in killed_run["before"]["ops"].items()}
    ops = resumed["ops"]

    # C: the proof was on disk before the kill -> done, never re-dispatched
    assert ops[ids["c"]]["state"] == "done"
    assert ops[ids["c"]]["side_effect_state"] == "known"
    assert ops[ids["c"]]["result_ref"] == killed_run["before"]["ops"]["c"]["result_ref"]
    assert ops[ids["c"]]["result_ref"].endswith("deploy.txt")

    # A: idempotent -> back to pending, same key, one extra attempt on dispatch
    assert ops[ids["a"]]["state"] == "pending"
    assert ops[ids["a"]]["attempt"] == 1
    assert ops[ids["a"]]["disposition"] == "REPEAT"
    assert ops[ids["a"]]["idempotency_key"] == killed_run["before"]["ops"]["a"]["key"]

    # B: unprovable -> parked with the operator wake source, attempt unchanged
    assert ops[ids["b"]]["state"] == "unknown"
    assert ops[ids["b"]]["side_effect_state"] == "unknown"
    assert ops[ids["b"]]["disposition"] == "UNKNOWN"
    assert ops[ids["b"]]["attempt"] == 1

    # D: intent only -> nothing was in flight
    assert ops[ids["d"]]["state"] == "pending"
    assert ops[ids["d"]]["attempt"] == 0

    assert resumed["operation_rows"] == 4, "no operation may be duplicated by a resume"
    assert len({item["key"] for item in killed_run["before"]["ops"].values()}) == 4


def test_the_parked_operation_blocks_the_resume_honestly(resumed):
    """§6.5 honest parking: the resume reports it and exits 3, it never retries."""
    assert resumed["exit_code"] == 3
    assert resumed["resume"]["resumable"] is False
    parked = resumed["resume"]["parked_operations"]
    assert len(parked) == 1
    assert parked[0]["wake_source"] == "operator:side_effect_verified"
    assert parked[0]["op_id"] == resumed["ops"][parked[0]["op_id"]]["op_id"]
    assert resumed["resume"]["wrote"]["parked"] == [parked[0]["op_id"]]
    # the crash-window operation was RESOLVED from its on-disk proof, not repeated
    resolved = resumed["resume"]["wrote"]["resolved"]
    assert len(resolved) == 1
    assert resumed["ops"][resolved[0]]["state"] == "done"
    assert resumed["ops"][resolved[0]]["result_ref"].endswith("deploy.txt")


def test_a_third_process_applies_nothing_twice(killed_run, resumed):
    runtime = killed_run["runtime"]
    script = runtime / "child_resume_twice.py"
    script.write_text(CHILD_RESUME_TWICE, encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, str(script), str(runtime)],
        capture_output=True,
        text=True,
        env=killed_run["env"],
        timeout=180,
    )
    assert completed.returncode == 0, completed.stderr
    again = json.loads(completed.stdout.strip().splitlines()[-1])

    assert again["exit_code"] == 3  # still parked, still the operator's call
    assert again["operation_rows"] == 4
    assert again["data"]["wrote"]["parked"] == []
    assert again["data"]["wrote"]["already_parked"] == [
        resumed["resume"]["parked_operations"][0]["op_id"]
    ]
    assert again["data"]["wrote"]["resolved"] == []
    before_ops = resumed["ops"]
    for op_id, row in before_ops.items():
        assert again["ops"][op_id]["state"] == row["state"], "no operation changed state"
        assert again["ops"][op_id]["attempt"] == row["attempt"], "no retry happened"
    assert again["ops"][resumed["resume"]["parked_operations"][0]["op_id"]]["state"] == "unknown"
    # exactly one receipt for the committed operation, and none for the parked one
    assert len(again["receipts"]) == 1
    done_ops = [item for item, row in resumed["ops"].items() if row["state"] == "done"]
    assert again["receipts"] == [f"{done_ops[0]}.json"]
