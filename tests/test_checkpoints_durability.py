"""CHECKPOINTS — durability proved by really killing the process (MVP/55 §kill).

The feature claims a checkpoint is a point a run can come back to *after* the process that
made it died. So this file does not simulate a kill:

* **a real ``SIGKILL`` mid-create** — a child loops over ``create`` and is killed with
  ``SIGKILL`` from the outside (no cleanup, no ``finally``, no flush). Every checkpoint that
  survives is then restored *in a second process* and must come back byte-identical, and
  every directory a kill left behind must be reported as ``interrupted`` and refuse to
  restore;
* **a create killed between the blobs and the manifest** — the deterministic kill point
  (``SKALDIR_CHECKPOINT_CRASH``), because "killed at the right moment" is otherwise a
  question of luck. Nothing restorable may exist afterwards, the blobs it wrote must be
  complete at their content addresses, and the next create must reuse them;
* **a real ``SIGKILL`` mid-restore** — files come back whole or not at all (never a torn
  mix), and a second restore finishes the job;
* **a checkpoint that outlives its own process** — the child publishes the checkpoint and
  then kills itself; the parent restores it exactly, files and conversation.

Every assertion is made on the surviving on-disk state, read by a fresh process.
"""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from skaldir import checkpoints as CP
from skaldir.session_store import SessionStore

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"


def sha_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def child_env(home: Path) -> dict:
    """A clean environment for a child: this repo's source, no inherited Skaldir vars."""
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("SKALDIR_", "GODSV_", "HERMES_")) and k != "PYTHONPATH"
    }
    env.update(
        {
            "PYTHONPATH": str(SRC),
            "PYTHONDONTWRITEBYTECODE": "1",
            "HOME": str(home),
            "SKALDIR_HOME": str(home / "rt"),
        }
    )
    return env


@pytest.fixture
def project(tmp_path: Path) -> Iterator[dict]:
    """A scratch project with a session and a few files to keep changing."""
    ws = tmp_path / "ws"
    ws.mkdir()
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    store = SessionStore(runtime / "sessions.db")
    session = store.create_session("durability")
    for turn in range(4):
        store.append_turn(session.session_id, f"user {turn}", f"assistant {turn}")
    yield {
        "tmp": tmp_path,
        "ws": ws,
        "runtime": runtime,
        "state": tmp_path / "state",
        "store": store,
        "session_id": session.session_id,
    }
    store.close()


LOOP_CREATE = '''
"""Child: create checkpoints until something kills us."""
import os, sys, time
from pathlib import Path
from skaldir import checkpoints as CP

ws, state, runtime, session_id = sys.argv[1:5]
ws = Path(ws)
names = [f"part{index:02d}.bin" for index in range(24)]


def write_round(round_number):
    for index, name in enumerate(names):
        (ws / name).write_bytes((f"round {round_number} part {index}\\n" * 400).encode())


for round_number in range(100000):
    write_round(round_number)
    created = CP.create_checkpoint(
        workspace=ws, state_dir=state, session_id=session_id, runtime=runtime,
        files=names, label=f"round {round_number}",
    )
    if created.get("ok"):
        print(created["checkpoint_id"], flush=True)
    # no sleep on purpose: the child is inside a create almost all of the time, so an
    # outside SIGKILL lands in the window where it can do damage.
'''

KILL_SELF_AFTER_CREATE = '''
"""Child: publish a checkpoint, then die without any cleanup."""
import json, os, signal, sys
from pathlib import Path
from skaldir import checkpoints as CP

ws, state, runtime, session_id = sys.argv[1:5]
created = CP.create_checkpoint(
    workspace=ws, state_dir=state, session_id=session_id, runtime=runtime,
    files=["src/alpha.py", "src/beta.py"], label="killed after publishing",
)
print(json.dumps(created), flush=True)
os.kill(os.getpid(), signal.SIGKILL)
'''

RESTORE_WITH_CRASH = '''
"""Child: restore with the deterministic kill point in the middle of the file loop."""
import json, sys
from skaldir import checkpoints as CP

state, ws, store_path = sys.argv[1:4]
answer = CP.restore_checkpoint(
    state, "last", workspace=ws, confirm=True, force=True, store=CP.open_session_store(store_path)
)
print(json.dumps({"verdict": answer.get("verdict")}))
'''

RESTORE_LOOP = '''
"""Child: drift, restore, repeat — the file writes never stop until something kills us."""
import sys, time
from pathlib import Path
from skaldir import checkpoints as CP

state, ws, store_path = sys.argv[1:4]
root = Path(ws)
for round_number in range(100000):
    for path in sorted(root.glob("big*.bin")):
        path.write_bytes(b"drifted away\\n")
    CP.restore_checkpoint(
        state, "last", workspace=ws, confirm=True, force=True,
        store=CP.open_session_store(store_path),
    )
    print(f"round {round_number}", flush=True)
    time.sleep(0.002)
'''


def run_child(
    script: Path, *argv: str, env: dict, timeout: int = 120
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(script), *[str(item) for item in argv]],
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def test_a_real_sigkill_mid_create_leaves_only_sound_checkpoints(tmp_path, project):
    """Kill a creating child with SIGKILL; every survivor must restore byte-identically."""
    script = project["tmp"] / "loop_create.py"
    script.write_text(LOOP_CREATE, encoding="utf-8")
    env = child_env(project["tmp"])
    process = subprocess.Popen(
        [sys.executable, str(script), str(project["ws"]), str(project["state"]),
         str(project["runtime"]), project["session_id"]],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    printed: list[str] = []
    deadline = time.time() + 30
    assert process.stdout is not None
    while time.time() < deadline and len(printed) < 3:
        line = process.stdout.readline()
        if not line:
            break
        printed.append(line.strip())
    time.sleep(0.05)
    process.send_signal(signal.SIGKILL)
    process.wait(timeout=30)
    assert process.returncode == -signal.SIGKILL, process.returncode
    assert printed, "the child should have published at least one checkpoint first"

    listing = CP.list_checkpoints(project["state"])
    assert listing["count"] >= 1, "a kill must not take the published checkpoints with it"
    for row in listing["checkpoints"]:
        manifest_path = Path(row["manifest_path"])
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert payload["stats"]["manifest_bytes"] == manifest_path.stat().st_size, (
            "a published manifest must be complete (it is written with one os.replace)"
        )
        assert set(row["checkpoint_id"] for row in listing["checkpoints"]) >= set(printed)
    # every survivor restores exactly, in a fresh process
    for row in listing["checkpoints"]:
        payload = CP.load_checkpoint(project["state"], row["checkpoint_id"])[0]
        assert payload is not None
        for entry in payload["files"]:
            blob = Path(entry["blob"])
            assert blob.name == entry["sha256"]
            assert CP.sha256_bytes(blob.read_bytes()) == entry["sha256"]

    # the interrupted directories (if the kill landed inside a create) are visible...
    for torn in listing["interrupted"]:
        checkpoint_id = Path(torn["path"]).name
        refused = CP.restore_checkpoint(
            project["state"], checkpoint_id, confirm=True, store=project["store"]
        )
        assert refused["verdict"] == CP.VERDICT_NOT_FOUND
        assert "no manifest" in refused["error"]["message"]
    assert listing["count"] + len(listing["interrupted"]) >= len(printed), (
        "every published checkpoint and every interrupted create must still be visible"
    )
    # ...and the next create finishes normally in the same store
    names = [f"part{index:02d}.bin" for index in range(24)]
    fresh = CP.create_checkpoint(
        workspace=project["ws"], state_dir=project["state"], session_id=project["session_id"],
        runtime=project["runtime"], files=names, label="after the kill",
    )
    assert fresh["verdict"] == CP.VERDICT_CREATED, fresh.get("error")
    restored = CP.restore_checkpoint(
        project["state"], fresh["checkpoint_id"], confirm=True, force=True,
        store=project["store"],
    )
    assert restored["verdict"] == CP.VERDICT_RESTORED
    assert all(item["byte_identical"] for item in restored["written"] or [])


def test_a_create_killed_between_the_blobs_and_the_manifest_is_not_restorable(tmp_path, project):
    script = project["tmp"] / "crash_create.py"
    script.write_text(
        LOOP_CREATE.replace("for round_number in range(100000):", "for round_number in range(1):"),
        encoding="utf-8",
    )
    env = child_env(project["tmp"])
    env["SKALDIR_CHECKPOINT_CRASH"] = CP.CRASH_AFTER_SNAPSHOT
    completed = run_child(
        script, project["ws"], project["state"], project["runtime"], project["session_id"],
        env=env,
    )
    assert completed.returncode == CP.KILL_EXIT, completed.stderr[-2000:]
    listing = CP.list_checkpoints(project["state"])
    assert listing["count"] == 0
    assert len(listing["interrupted"]) == 1
    checkpoint_id = Path(listing["interrupted"][0]["path"]).name
    assert not (project["state"] / checkpoint_id / CP.MANIFEST_NAME).exists()
    blobs = [path for path in (project["state"] / CP.BLOB_DIRNAME).rglob("*") if path.is_file()]
    assert blobs
    for path in blobs:
        assert path.name == CP.sha256_bytes(path.read_bytes())

    # the kill injection is inert without the variable: the same create now succeeds
    completed = run_child(
        script, project["ws"], project["state"], project["runtime"], project["session_id"],
        env=child_env(project["tmp"]),
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    assert CP.list_checkpoints(project["state"])["count"] == 1


def test_a_checkpoint_outlives_the_process_that_made_it(tmp_path, project):
    """The child publishes the checkpoint and SIGKILLs itself; the parent restores exactly."""
    (project["ws"] / "src").mkdir()
    (project["ws"] / "src" / "alpha.py").write_text("ALPHA = 1\n", encoding="utf-8")
    (project["ws"] / "src" / "beta.py").write_text("BETA = 2\n", encoding="utf-8")
    script = project["tmp"] / "kill_after_create.py"
    script.write_text(KILL_SELF_AFTER_CREATE, encoding="utf-8")
    completed = run_child(
        script, project["ws"], project["state"], project["runtime"], project["session_id"],
        env=child_env(project["tmp"]),
    )
    assert completed.returncode == -signal.SIGKILL, completed.stderr[-2000:]
    created = json.loads(completed.stdout.strip().splitlines()[-1])
    assert created["verdict"] == CP.VERDICT_CREATED

    # the child's process is gone; its checkpoint still restores, byte for byte
    (project["ws"] / "src" / "alpha.py").write_text("ALPHA = 99\n", encoding="utf-8")
    (project["ws"] / "src" / "beta.py").unlink()
    for turn in range(4, 9):
        project["store"].append_turn(
            project["session_id"], f"user {turn}", f"assistant {turn}"
        )
    answer = CP.restore_checkpoint(
        project["state"], created["checkpoint_id"], confirm=True, force=True,
        store=project["store"],
    )
    assert answer["verdict"] == CP.VERDICT_RESTORED, answer.get("error")
    for entry in created["files"]:
        assert sha_of(project["ws"] / entry["path"]) == entry["sha256"]
    assert answer["conversation"]["identical"] is True
    assert (
        answer["conversation"]["conversation_sha256_after"]
        == created["conversation"]["conversation_sha256"]
    )
    assert answer["resume"]["complete"] is True


def test_a_restore_killed_midway_leaves_whole_files_and_is_re_runnable(tmp_path, project):
    """The deterministic kill point in the middle of the file loop, then a second restore."""
    for index in range(4):
        (project["ws"] / f"file{index}.py").write_text(
            f"VALUE = {index}\n", encoding="utf-8"
        )
    names = [f"file{index}.py" for index in range(4)]
    created = CP.create_checkpoint(
        workspace=project["ws"], state_dir=project["state"], session_id=project["session_id"],
        runtime=project["runtime"], files=names, label="before the kill",
    )
    assert created["verdict"] == CP.VERDICT_CREATED
    recorded = {entry["path"]: entry["sha256"] for entry in created["files"]}
    for name in names:
        (project["ws"] / name).write_text(f"# rewritten {name}\n", encoding="utf-8")
    drifted = {name: sha_of(project["ws"] / name) for name in names}

    script = project["tmp"] / "restore_crash.py"
    script.write_text(RESTORE_WITH_CRASH, encoding="utf-8")
    env = child_env(project["tmp"])
    env["SKALDIR_CHECKPOINT_CRASH"] = CP.CRASH_MID_RESTORE
    completed = run_child(
        script, project["state"], project["ws"], project["runtime"] / "sessions.db", env=env
    )
    assert completed.returncode == CP.KILL_EXIT, completed.stderr[-2000:]

    # nothing is torn: every file is either the drifted bytes or the checkpoint's bytes
    for name in names:
        current = sha_of(project["ws"] / name)
        assert current in (recorded[name], drifted[name]), f"{name} is a torn mix"
    partial = sorted(
        name for name in names if sha_of(project["ws"] / name) == recorded[name]
    )
    assert partial, "the kill landed inside the restore, so at least one file came back"

    # finishing it needs --force (the half-restored files no longer match the checkpoint)
    refused = CP.restore_checkpoint(
        project["state"], "last", workspace=project["ws"], confirm=True,
        store=CP.open_session_store(project["runtime"] / "sessions.db"),
    )
    assert refused["verdict"] == CP.VERDICT_REFUSED_DRIFT
    finished = CP.restore_checkpoint(
        project["state"], "last", workspace=project["ws"], confirm=True, force=True,
        store=CP.open_session_store(project["runtime"] / "sessions.db"),
    )
    assert finished["verdict"] == CP.VERDICT_RESTORED
    for name in names:
        assert sha_of(project["ws"] / name) == recorded[name]


def test_a_real_sigkill_during_a_restore_never_leaves_a_torn_file(tmp_path, project):
    """Kill a restoring child from the outside: whole files only, and a re-run finishes."""
    bodies = {}
    for index in range(40):
        name = f"big{index:02d}.bin"
        payload = (f"line {index}\n" * 900).encode()
        (project["ws"] / name).write_bytes(payload)
        bodies[name] = payload
    created = CP.create_checkpoint(
        workspace=project["ws"], state_dir=project["state"], session_id=project["session_id"],
        runtime=project["runtime"], files=sorted(bodies), label="many files",
    )
    assert created["verdict"] == CP.VERDICT_CREATED, created.get("error")
    recorded = {entry["path"]: entry["sha256"] for entry in created["files"]}
    for name in bodies:
        (project["ws"] / name).write_bytes(b"drifted away\n")
    drifted = {name: sha_of(project["ws"] / name) for name in bodies}

    script = project["tmp"] / "restore_loop.py"
    script.write_text(RESTORE_LOOP, encoding="utf-8")
    env = child_env(project["tmp"])
    process = subprocess.Popen(
        [sys.executable, str(script), str(project["state"]), str(project["ws"]),
         str(project["runtime"] / "sessions.db")],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    assert process.stdout is not None
    first = ""
    deadline = time.time() + 60
    while time.time() < deadline and not first:
        line = process.stdout.readline()
        if not line:
            break
        first = line.strip()
    assert first.startswith("round"), (
        "the child never finished a restore round, so the kill would prove nothing"
    )
    time.sleep(0.2)  # let it get well into the write/restore cycle
    process.send_signal(signal.SIGKILL)
    process.wait(timeout=30)
    assert process.returncode == -signal.SIGKILL

    for name in bodies:
        current = sha_of(project["ws"] / name)
        assert current in (recorded[name], drifted[name]), f"{name} is a torn mix"
    finished = CP.restore_checkpoint(
        project["state"], "last", workspace=project["ws"], confirm=True, force=True,
        store=project["store"],
    )
    assert finished["verdict"] == CP.VERDICT_RESTORED
    for name, body in bodies.items():
        assert (project["ws"] / name).read_bytes() == body
        assert sha_of(project["ws"] / name) == recorded[name]
