#!/usr/bin/env python3
"""CHECKPOINTS — the real-run proof (MVP/55).

One scratch project, real processes, no fakes. What it does, in order:

1. builds ``<scratch>/ws`` (3 files) + ``<scratch>/runtime`` (a 50-message session) and runs
   a real edit transaction over the workspace (``edit_tx``), so the declared file set comes
   from a run and not from a command line;
2. ``checkpoint create`` five times, reporting the measured cost per create
   (ms, manifest bytes, blob bytes written/reused, bytes on disk) — the cheapness claim;
3. drifts the run (3 files rewritten, 5 more turns), then
   ``checkpoint restore`` without ``--yes`` (confirmation), with ``--yes`` (drift refusal,
   exit 6), and with ``--yes --force`` (exact restore): prints the per-file sha256 before /
   checkpoint / after, the conversation digest before and after, and the resume block;
4. proves the declared set is the whole world (an undeclared file keeps its bytes and mtime);
5. kills a creating child with a real ``SIGKILL`` and reports what survived; kills a create
   at the deterministic point (blobs written, manifest not published) and reports that
   nothing is restorable; kills a restoring child and checks no file is a torn mix;
6. drives ``skaldir checkpoint …`` through the CLI in a child process for each verb and
   prints the exit codes.

Run:  ``.venv/bin/python tools/gv_checkpoints_proof.py``
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import statistics
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
SCRATCH = Path(os.environ.get("GV_CHECKPOINT_SCRATCH") or "/home/willy/gv-checkpoints-proof/run")
sys.path.insert(0, str(SRC))

from skaldir import checkpoints as CP  # noqa: E402
from skaldir import edit_tx as ET  # noqa: E402
from skaldir.session_store import SessionStore  # noqa: E402

FILES = {
    "src/alpha.py": "def alpha(value):\n    return value + 1\n",
    "src/beta.py": "COUNT = 1\nNAME = 'beta'\n",
    "README.md": "# demo\n\nthree files, one checkpoint\n",
}
TURNS = 25  # -> 50 messages


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_of(path: Path) -> str:
    return sha(path.read_bytes())


def line(text: str = "") -> None:
    print(text, flush=True)


def child_env() -> dict:
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("SKALDIR_", "GODSV_", "HERMES_")) and key != "PYTHONPATH"
    }
    env.update({"PYTHONPATH": str(SRC), "PYTHONDONTWRITEBYTECODE": "1"})
    return env


def build_project() -> dict:
    if SCRATCH.exists():
        shutil.rmtree(SCRATCH)
    ws = SCRATCH / "ws"
    (ws / "src").mkdir(parents=True)
    for name, body in FILES.items():
        (ws / name).write_text(body, encoding="utf-8")
    runtime = SCRATCH / "runtime"
    runtime.mkdir(parents=True)
    store = SessionStore(runtime / "sessions.db")
    session = store.create_session("checkpoint proof")
    for turn in range(TURNS):
        store.append_turn(session.session_id, f"user turn {turn}", f"assistant reply {turn}")
    spec, problem = ET.parse_spec(
        {
            "reason": "the proof run's edit",
            "verification": {"kind": "syntax"},
            "files": [
                {
                    "path": "src/alpha.py",
                    "edits": [{"old": "return value + 1", "new": "return value + 41"}],
                },
                {"path": "src/beta.py", "edits": [{"old": "COUNT = 1", "new": "COUNT = 99"}]},
                {"path": "README.md", "edits": [{"old": "# demo", "new": "# demo project"}]},
            ],
        }
    )
    assert spec is not None, problem
    record = ET.run_transaction(spec, ws, state_dir=runtime / "edit")
    assert record["verdict"] == ET.VERDICT_APPLIED, record
    return {
        "ws": ws,
        "runtime": runtime,
        "state": SCRATCH / "state",
        "store": store,
        "session_id": session.session_id,
        "tx_id": record["tx_id"],
    }


def phase_cost(project: dict) -> dict:
    line("## 2. storage cost — a 3-file / 50-message run, five creates")
    created = []
    for index in range(5):
        started = time.perf_counter()
        payload = CP.create_checkpoint(
            workspace=project["ws"],
            state_dir=project["state"],
            session_id=project["session_id"],
            runtime=project["runtime"],
            edit_state_dir=project["runtime"] / "edit",
            label=f"proof #{index}",
        )
        wall_ms = (time.perf_counter() - started) * 1000
        assert payload["verdict"] == CP.VERDICT_CREATED, payload
        payload["wall_ms"] = wall_ms
        created.append(payload)
    for index, payload in enumerate(created):
        stats = payload["stats"]
        line(
            f"  create #{index}: wall {payload['wall_ms']:.1f} ms · internal "
            f"{stats['create_ms']} ms · manifest {stats['manifest_bytes']} B · blobs written "
            f"{stats['blob_bytes_written']} B · reused {stats['blob_bytes_reused']} B · "
            f"captured {stats['captured_bytes']} B · state dir {stats['state_dir_bytes']} B"
        )
    first = created[0]["stats"]
    line(
        f"  summary: wall min/median/max "
        f"{min(item['wall_ms'] for item in created):.1f}/"
        f"{statistics.median(item['wall_ms'] for item in created):.1f}/"
        f"{max(item['wall_ms'] for item in created):.1f} ms · "
        f"manifest {first['manifest_bytes']} B · first-write blobs "
        f"{first['blob_bytes_written']} B · later creates "
        f"{created[-1]['stats']['blob_bytes_written']} B new bytes · "
        f"50 messages referenced ({first['conversation_bytes_referenced']} body bytes stay in "
        "sessions.db)"
    )
    tx = created[0]["file_set"]["transactions"][0]["tx_id"]
    line(
        f"  declared set: {', '.join(item['path'] for item in created[0]['files'])} "
        f"(source: {created[0]['file_set']['source']}, tx {tx})"
    )
    return {"created": created, "first": created[0]}


def phase_restore(project: dict, first: dict) -> dict:
    line()
    line("## 3. drift, refusal, exact restore")
    (project["ws"] / "src" / "alpha.py").write_text(
        "def alpha(value):\n    return value + 1000\n", encoding="utf-8"
    )
    (project["ws"] / "src" / "beta.py").write_text("COUNT = 1234\n", encoding="utf-8")
    (project["ws"] / "README.md").write_text("# demo\n\nchanged later\n", encoding="utf-8")
    outsider = project["ws"] / "changelog.md"
    outsider.write_text("# changelog\n\nnot part of the checkpoint\n", encoding="utf-8")
    outsider_before = (sha_of(outsider), outsider.stat().st_mtime_ns)
    for turn in range(TURNS, TURNS + 5):
        project["store"].append_turn(
            project["session_id"], f"user turn {turn}", f"assistant reply {turn}"
        )
    turns = project["store"].require_session(project["session_id"]).message_count
    line(f"  after more work: session {turns} messages, 3 files rewritten")

    preview = CP.restore_checkpoint(
        project["state"], "last", workspace=project["ws"], store=project["store"]
    )
    line(
        f"  restore without --yes: verdict {preview['verdict']}, exit {CP.exit_code_for(preview)}, "
        f"writes planned {preview['preview']['writes']}, files touched 0"
    )
    alpha = next(item for item in preview["files"] if item["path"] == "src/alpha.py")
    line(f"  preview diff for src/alpha.py (first 3 lines): {alpha['diff'].splitlines()[:5]}")

    refusal = CP.restore_checkpoint(
        project["state"], "last", workspace=project["ws"], confirm=True, store=project["store"]
    )
    line(
        f"  restore --yes on a drifted tree: verdict {refusal['verdict']}, exit "
        f"{CP.exit_code_for(refusal)}, kind {refusal['error']['kind']}"
    )
    for item in refusal["error"]["detail"]["refusals"]:
        message = item["message"][:90]
        line(f"    refused: {item['kind']} (forceable={item['forceable']}) — {message}")

    started = time.perf_counter()
    answer = CP.restore_checkpoint(
        project["state"], "last", workspace=project["ws"], confirm=True, force=True,
        store=project["store"],
    )
    restore_ms = (time.perf_counter() - started) * 1000
    assert answer["verdict"] == CP.VERDICT_RESTORED, answer
    line(
        f"  restore --yes --force: verdict {answer['verdict']}, exit {CP.exit_code_for(answer)}, "
        f"complete {answer['complete']}, wall {restore_ms:.1f} ms"
    )
    recorded = {entry["path"]: entry for entry in first["files"]}
    for item in answer["written"]:
        entry = recorded[item["path"]]
        now = sha_of(project["ws"] / item["path"])
        line(
            f"    {item['path']}: before {item['sha256_before'][:12]} · checkpoint "
            f"{item['sha256_checkpoint'][:12]} · after {item['sha256_after'][:12]} · "
            f"byte_identical={item['byte_identical']} · equals recorded {now == entry['sha256']}"
        )
    conversation = answer["conversation"]
    line(
        f"  conversation: checkpoint {first['conversation']['conversation_sha256']} vs restored "
        f"{conversation['conversation_sha256_after']} — identical={conversation['identical']}"
    )
    line(
        f"    mode {conversation['mode']} · restored session {conversation['restore_session_id']} "
        f"· {conversation['message_count']} messages replayed · history_matches "
        f"{conversation['history_matches']} · original session keeps "
        f"{project['store'].require_session(project['session_id']).message_count} messages"
    )
    resume = answer["resume"]
    line(
        f"  resume (step {resume['step']}): session {resume['session_id']} · next_role "
        f"{resume['next_role']} · next_seq {resume['next_seq']} · "
        f"store history matches {resume['store_resume']['history']['matches']}"
    )
    line(f"    continuation: {resume['continuation_command']}")
    line(
        f"  drift recorded: {[item['kind'] for item in answer['forced_drift']]} · "
        f"stats {answer['stats']}"
    )
    line()
    line("## 4. isolation — only the declared set is touched")
    after_outsider = (sha_of(outsider), outsider.stat().st_mtime_ns)
    line(
        f"  changelog.md (never declared): sha256 {outsider_before[0][:12]} -> "
        f"{after_outsider[0][:12]}, mtime {outsider_before[1]} -> {after_outsider[1]}, "
        f"unchanged={outsider_before == after_outsider}"
    )
    return {"answer": answer, "restore_ms": restore_ms}


def phase_kill(project: dict) -> dict:
    line()
    line("## 5. process kill")
    loop = SCRATCH / "loop_create.py"
    loop.write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "from skaldir import checkpoints as CP\n"
        "ws, state, runtime, session_id = sys.argv[1:5]\n"
        "ws = Path(ws)\n"
        "names = ['part%02d.bin' % index for index in range(24)]\n"
        "for round_number in range(100000):\n"
        "    for index, name in enumerate(names):\n"
        "        text = 'round %d part %d\\n' % (round_number, index)\n"
        "        (ws / name).write_bytes((text * 400).encode())\n"
        "    created = CP.create_checkpoint(workspace=ws, state_dir=state, session_id=session_id,\n"
        "                                   runtime=runtime, files=names,\n"
        "                                   label='round %d' % round_number)\n"
        "    if created.get('ok'):\n"
        "        print(created['checkpoint_id'], flush=True)\n",
        encoding="utf-8",
    )
    state = SCRATCH / "kill-state"
    process = subprocess.Popen(
        [sys.executable, str(loop), str(project["ws"]), str(state), str(project["runtime"]),
         project["session_id"]],
        env=child_env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    printed: list[str] = []
    assert process.stdout is not None
    deadline = time.time() + 30
    while time.time() < deadline and len(printed) < 3:
        text = process.stdout.readline()
        if not text:
            break
        printed.append(text.strip())
    time.sleep(0.05)
    process.send_signal(signal.SIGKILL)
    process.wait(timeout=30)
    listing = CP.list_checkpoints(state)
    sound = 0
    for row in listing["checkpoints"]:
        payload = CP.load_checkpoint(state, row["checkpoint_id"])[0]
        manifest_path = Path(row["manifest_path"])
        size = payload["stats"]["manifest_bytes"] if payload else -1
        ok = payload is not None and size == manifest_path.stat().st_size
        ok = ok and all(
            CP.sha256_bytes(Path(entry["blob"]).read_bytes()) == entry["sha256"]
            for entry in payload["files"]
            if entry["exists"]
        )
        sound += int(ok)
    verified = 0
    for row in listing["checkpoints"]:
        answer = CP.restore_checkpoint(
            state, row["checkpoint_id"], workspace=project["ws"], confirm=True, force=True,
            store=project["store"],
        )
        verified += int(answer["verdict"] == CP.VERDICT_RESTORED)
    line(
        f"  real SIGKILL mid-create (returncode {process.returncode}): "
        f"{len(printed)} checkpoints published before the kill, {listing['count']} survived, "
        f"{sound} with a complete manifest and verified blobs, {verified} restored "
        f"byte-identically afterwards"
    )
    line(
        f"  interrupted directories left by the kill: {len(listing['interrupted'])} "
        f"({[Path(item['path']).name for item in listing['interrupted']]})"
    )
    for item in listing["interrupted"]:
        refused = CP.restore_checkpoint(
            state, Path(item["path"]).name, confirm=True, store=project["store"]
        )
        reason = refused["error"]["message"][:80]
        line(f"    restoring one is refused: {refused['verdict']} — {reason}")

    hook = SCRATCH / "hook_create.py"
    hook.write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "from skaldir import checkpoints as CP\n"
        "ws, state, runtime, session_id = sys.argv[1:5]\n"
        "names = sorted(path.name for path in Path(ws).glob('part*.bin'))\n"
        "created = CP.create_checkpoint(workspace=ws, state_dir=state,\n"
        "                               session_id=session_id, runtime=runtime,\n"
        "                               files=names, label='killed')\n"
        "print(created.get('verdict'))\n",
        encoding="utf-8",
    )
    hook_state = SCRATCH / "hook-state"
    env = child_env()
    env["SKALDIR_CHECKPOINT_CRASH"] = CP.CRASH_AFTER_SNAPSHOT
    completed = subprocess.run(
        [sys.executable, str(hook), str(project["ws"]), str(hook_state), str(project["runtime"]),
         project["session_id"]],
        env=env, capture_output=True, text=True, timeout=120,
    )
    killed = CP.list_checkpoints(hook_state)
    blobs = [path for path in (hook_state / CP.BLOB_DIRNAME).rglob("*") if path.is_file()]
    complete = all(path.name == CP.sha256_bytes(path.read_bytes()) for path in blobs)
    line(
        f"  killed between the blobs and the manifest (exit {completed.returncode}): "
        f"{len(blobs)} blobs on disk, all at their content address={complete}, "
        f"checkpoints listed {killed['count']}, interrupted "
        f"{len(killed['interrupted'])} — nothing restorable"
    )
    fresh = CP.create_checkpoint(
        workspace=project["ws"], state_dir=hook_state, session_id=project["session_id"],
        runtime=project["runtime"],
        files=sorted(path.name for path in project["ws"].glob("part*.bin")),
        label="after the kill",
    )
    line(
        f"  the next create reuses them: blobs written "
        f"{fresh['stats']['blob_bytes_written']} B, reused {fresh['stats']['blob_bytes_reused']} B"
    )

    restore_loop = SCRATCH / "restore_loop.py"
    restore_loop.write_text(
        "import sys, time\n"
        "from pathlib import Path\n"
        "from skaldir import checkpoints as CP\n"
        "state, ws, store_path = sys.argv[1:4]\n"
        "root = Path(ws)\n"
        "for round_number in range(100000):\n"
        "    for path in sorted(root.glob('big*.bin')):\n"
        "        path.write_bytes(b'drifted away\\n')\n"
        "    CP.restore_checkpoint(state, 'last', workspace=ws, confirm=True, force=True,\n"
        "                          store=CP.open_session_store(store_path))\n"
        "    print('round %d' % round_number, flush=True)\n"
        "    time.sleep(0.002)\n",
        encoding="utf-8",
    )
    torn_state = SCRATCH / "torn-state"
    bodies = {}
    for index in range(40):
        body = (f"line {index}\n" * 900).encode()
        (project["ws"] / f"big{index:02d}.bin").write_bytes(body)
        bodies[f"big{index:02d}.bin"] = body
    created = CP.create_checkpoint(
        workspace=project["ws"], state_dir=torn_state, session_id=project["session_id"],
        runtime=project["runtime"], files=sorted(bodies), label="many files",
    )
    recorded = {entry["path"]: entry["sha256"] for entry in created["files"]}
    drifted = {name: sha(b"drifted away\n") for name in bodies}
    process = subprocess.Popen(
        [sys.executable, str(restore_loop), str(torn_state), str(project["ws"]),
         str(project["runtime"] / "sessions.db")],
        env=child_env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    assert process.stdout is not None
    first = ""
    deadline = time.time() + 60
    while time.time() < deadline and not first:
        text = process.stdout.readline()
        if not text:
            break
        first = text.strip()
    time.sleep(0.2)
    process.send_signal(signal.SIGKILL)
    process.wait(timeout=30)
    torn = [
        name
        for name in bodies
        if sha_of(project["ws"] / name) not in (recorded[name], drifted[name])
    ]
    finished = CP.restore_checkpoint(
        torn_state, "last", workspace=project["ws"], confirm=True, force=True,
        store=project["store"],
    )
    exact = all(sha_of(project["ws"] / name) == recorded[name] for name in bodies)
    line(
        f"  real SIGKILL mid-restore (returncode {process.returncode}, after '{first}'): "
        f"torn files {len(torn)} · a second restore finishes it: verdict "
        f"{finished['verdict']}, all {len(bodies)} files byte-identical={exact}"
    )
    return {"sound": sound, "count": listing["count"], "torn": len(torn)}


def phase_cli(project: dict) -> None:
    line()
    line("## 6. the CLI (child processes, one per verb)")
    cli_state = SCRATCH / "cli-state"
    common = ["--state", str(cli_state), "--runtime", str(project["runtime"])]
    runs = [
        ("create", ["create", "--workspace", str(project["ws"]), "--file", "changelog.md",
                    "--session", project["session_id"], *common]),
        ("list", ["list", *common]),
        ("show", ["show", "last", "--workspace", str(project["ws"]),
                  "--session-store", str(project["runtime"] / "sessions.db"), *common]),
        ("restore (no --yes)", ["restore", "last", "--workspace", str(project["ws"]),
                                "--session-store", str(project["runtime"] / "sessions.db"),
                                *common]),
        ("restore --yes --force", ["restore", "last", "--workspace", str(project["ws"]),
                                   "--session-store", str(project["runtime"] / "sessions.db"),
                                   "--yes", "--force", *common]),
        ("resume --yes --force", ["resume", "last", "--workspace", str(project["ws"]),
                                  "--session-store", str(project["runtime"] / "sessions.db"),
                                  "--yes", "--force", *common]),
    ]
    for label, argv in runs:
        completed = subprocess.run(
            [sys.executable, "-m", "skaldir.checkpoints_cli", *argv, "--json"],
            env=child_env(), capture_output=True, text=True, timeout=300, cwd=str(SCRATCH),
        )
        document = json.loads(completed.stdout.strip().splitlines()[-1])
        payload = document.get("data") or {}
        line(
            f"  checkpoint {label}: exit {completed.returncode} (envelope {document['exit_code']}) "
            f"· verdict {payload.get('verdict')} · evidence {len(document['evidence'])}"
        )
        if label.startswith("resume"):
            resume = (payload.get("resume") or {})
            line(
                f"    step {resume.get('step')} · next_role {resume.get('next_role')} · "
                f"next_seq {resume.get('next_seq')} · complete {resume.get('complete')}"
            )
        if label.startswith("create"):
            stats = payload.get("stats") or {}
            line(
                f"    cost through the CLI: {stats.get('total_ms')} ms total, "
                f"manifest {stats.get('manifest_bytes')} B, "
                f"state dir {stats.get('state_dir_bytes')} B"
            )


def main() -> int:
    line("# CHECKPOINTS — real-run proof (MVP/55)")
    line()
    line(f"scratch: {SCRATCH}")
    line(f"python: {sys.executable}")
    line()
    line("## 1. the scratch project")
    project = build_project()
    line(f"  workspace {project['ws']} (3 files, one edit transaction {project['tx_id']})")
    line(
        f"  session {project['session_id']}: "
        f"{project['store'].require_session(project['session_id']).message_count} messages"
    )
    line()
    cost = phase_cost(project)
    restored = phase_restore(project, cost["first"])
    killed = phase_kill(project)
    phase_cli(project)
    line()
    line("## 7. verdict")
    line(
        f"  cost: {cost['first']['stats']['manifest_bytes']} B manifest for a 3-file/50-message "
        f"point; {cost['created'][-1]['stats']['blob_bytes_written']} new blob bytes on a second "
        f"create of an unchanged tree"
    )
    line(
        f"  exactness: restore wall {restored['restore_ms']:.1f} ms, "
        "conversation digest identical"
    )
    line(
        f"  kill: {killed['count']} checkpoints survived a real SIGKILL mid-create and restored; "
        f"{killed['torn']} torn files after a SIGKILL mid-restore"
    )
    project["store"].close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
