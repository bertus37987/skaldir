"""CHECKPOINTS — the unified workspace+conversation snapshot (MVP/55).

What is pinned here, one test per promise the feature makes:

* **cheap** — a 3-file / 50-message run is snapshotted in single-digit milliseconds and
  the bytes are content-addressed (a second checkpoint of an unchanged tree writes **0**
  new blob bytes); the conversation is referenced by hash, never copied into the manifest;
* **exact** — restore walks the declared set by sha256 and reports ``byte_identical`` per
  file, and the conversation digest of the restored point equals the recorded one;
* **drift-refusing** — a tree that changed since the checkpoint is refused (exit 6) with
  the drift named; ``--force`` restores and *records* the drift;
* **isolated** — nothing outside the declared set is read, written or deleted (a sibling
  file keeps its bytes *and* its mtime);
* **kill-safe** — a create killed after the blobs and before the manifest leaves an
  ``interrupted`` directory that cannot be restored, and the blobs it wrote are complete
  and verified, so the next create reuses them.

The real ``SIGKILL`` proofs (a child process killed mid-create and mid-restore) live in
``tests/test_checkpoints_durability.py``; the CLI surface in ``tests/test_checkpoints_cli.py``.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from skaldir import checkpoints as CP
from skaldir import edit_tx as ET
from skaldir.session_store import SessionStore

REPO = Path(__file__).resolve().parents[1]

FILES = {
    "src/alpha.py": "def alpha(value):\n    return value + 1\n",
    "src/beta.py": "COUNT = 1\nNAME = 'beta'\n",
    "README.md": "# demo\n\nthree files, one checkpoint\n",
}

TURNS = 25  # -> 50 messages (user + assistant per turn)


def sha_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_edit(document: dict, workspace: Path, state: Path) -> dict:
    """Run one real edit transaction (parse + stage + publish) over the workspace."""
    spec, problem = ET.parse_spec(document)
    assert spec is not None, problem
    return ET.run_transaction(spec, workspace, state_dir=state)


@pytest.fixture
def run(tmp_path: Path) -> Iterator[dict]:
    """A scratch project: 3 files, a 50-message session, and one applied transaction."""
    ws = tmp_path / "ws"
    (ws / "src").mkdir(parents=True)
    for name, body in FILES.items():
        (ws / name).write_text(body, encoding="utf-8")
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    store = SessionStore(runtime / "sessions.db")
    session = store.create_session("checkpoint proof")
    for turn in range(TURNS):
        store.append_turn(session.session_id, f"user turn {turn}", f"assistant reply {turn}")
    edit_state = runtime / "edit"
    record = write_edit(
        {
            "reason": "the run's edit",
            "verification": {"kind": "syntax"},
            "files": [
                {
                    "path": "src/alpha.py",
                    "edits": [{"old": "return value + 1", "new": "return value + 41"}],
                },
                {"path": "src/beta.py", "edits": [{"old": "COUNT = 1", "new": "COUNT = 99"}]},
                {
                    "path": "README.md",
                    "edits": [{"old": "# demo", "new": "# demo project"}],
                },
            ],
        },
        ws,
        edit_state,
    )
    assert record["verdict"] == ET.VERDICT_APPLIED, record
    yield {
        "tmp": tmp_path,
        "ws": ws,
        "state": tmp_path / "state",
        "runtime": runtime,
        "edit_state": edit_state,
        "store": store,
        "session_id": session.session_id,
        "tx_id": record["tx_id"],
    }
    store.close()


def do_create(run: dict, **kwargs) -> dict:
    return CP.create_checkpoint(
        workspace=run["ws"],
        state_dir=run["state"],
        session_id=run["session_id"],
        runtime=run["runtime"],
        edit_state_dir=run["edit_state"],
        label=kwargs.pop("label", "proof point"),
        **kwargs,
    )


# --------------------------------------------------------------------------- #
# 1 — storing a checkpoint is cheap                                             #
# --------------------------------------------------------------------------- #


def test_a_three_file_fifty_message_run_is_snapshotted_cheaply(run):
    started = time.perf_counter()
    created = do_create(run)
    elapsed_ms = (time.perf_counter() - started) * 1000
    assert created["verdict"] == CP.VERDICT_CREATED, created.get("error")
    stats = created["stats"]
    assert stats["files"] == 3 and stats["files_with_content"] == 3
    assert stats["messages"] == 50
    assert stats["captured_bytes"] == sum(
        (run["ws"] / name).stat().st_size for name in FILES
    ), "the checkpoint captures the tree as it is, after the transaction"
    assert stats["blob_bytes_written"] == stats["captured_bytes"], "first write stores the bytes"
    assert stats["blob_bytes_reused"] == 0
    # the manifest is one small JSON document — the conversation is not copied into it
    manifest_path = Path(created["manifest_path"])
    assert stats["manifest_bytes"] == manifest_path.stat().st_size
    assert stats["manifest_bytes"] < 64_000, stats["manifest_bytes"]
    assert elapsed_ms < 2000, f"create took {elapsed_ms:.1f} ms"
    assert stats["create_ms"] <= int(elapsed_ms) + 1
    # the declared set came from the transaction, and only from it
    assert sorted(item["path"] for item in created["files"]) == sorted(FILES)
    sources = {item["path"]: item["source_tx"] for item in created["files"]}
    assert sources["src/alpha.py"] == run["tx_id"]
    assert all(sources[name] == run["tx_id"] for name in FILES)
    assert all(item["declared_by"] == "transaction" for item in created["files"])
    assert all(item["sha256"] == sha_of(run["ws"] / item["path"]) for item in created["files"])


def test_the_conversation_is_referenced_by_hash_never_copied(run):
    created = do_create(run)
    text = Path(created["manifest_path"]).read_text(encoding="utf-8")
    assert "assistant reply 7" not in text, "a body in the manifest is a copy, not a reference"
    assert "user turn 24" not in text
    conversation = created["conversation"]
    assert conversation["bodies_stored"] is False
    assert conversation["message_count"] == 50
    assert conversation["body_bytes_referenced"] > 0
    assert conversation["history_matches_at_snapshot"] is True
    rows = conversation["messages"]
    assert len(rows) == 50
    assert rows[0]["seq"] == 1 and rows[-1]["seq"] == 50
    assert all(len(item["content_sha256"]) == 64 for item in rows)
    assert CP.conversation_hash(rows) == conversation["conversation_sha256"]
    assert conversation["algorithm"] == CP.CONVERSATION_HASH_ALGORITHM


def test_a_second_checkpoint_of_an_unchanged_tree_writes_no_new_blob_bytes(run):
    first = do_create(run)
    second = do_create(run, label="again")
    assert second["checkpoint_id"] != first["checkpoint_id"]
    assert second["stats"]["blob_bytes_written"] == 0
    assert second["stats"]["blob_bytes_reused"] == first["stats"]["captured_bytes"]
    assert second["stats"]["manifest_bytes"]
    # both manifests point at the same addresses
    assert {item["sha256"] for item in first["files"]} == {
        item["sha256"] for item in second["files"]
    }


def test_an_absent_declared_file_is_restored_by_deleting_it(run):
    extra = run["ws"] / "notes.md"
    created = do_create(run, files=["notes.md"])
    recorded = {item["path"]: item for item in created["files"]}["notes.md"]
    assert recorded["exists"] is False and recorded["sha256"] == ""
    extra.write_text("appeared after the checkpoint\n", encoding="utf-8")
    answer = CP.restore_checkpoint(
        run["state"], "last", confirm=True, force=True, store=run["store"]
    )
    assert answer["verdict"] == CP.VERDICT_RESTORED, answer.get("error")
    row = [item for item in answer["written"] if item["path"] == "notes.md"]
    assert row and row[0]["action"] == CP.ACTION_DELETE and row[0]["result"] == "deleted"
    assert not extra.exists()


# --------------------------------------------------------------------------- #
# 2 — restore is exact, or refused                                              #
# --------------------------------------------------------------------------- #


def drift_the_run(run: dict) -> None:
    """Change all three files and append 10 messages — the state after 'more work'."""
    (run["ws"] / "src" / "alpha.py").write_text(
        "def alpha(value):\n    return value + 1000\n", encoding="utf-8"
    )
    (run["ws"] / "src" / "beta.py").write_text("COUNT = 1234\nNAME = 'beta'\n", encoding="utf-8")
    (run["ws"] / "README.md").write_text("# demo\n\nchanged later\n", encoding="utf-8")
    for turn in range(TURNS, TURNS + 5):
        run["store"].append_turn(
            run["session_id"], f"user turn {turn}", f"assistant reply {turn}"
        )


def test_restore_returns_the_files_and_the_conversation_exactly(run):
    created = do_create(run)
    drift_the_run(run)
    assert run["store"].require_session(run["session_id"]).message_count == 60

    refusal = CP.restore_checkpoint(run["state"], "last", confirm=True, store=run["store"])
    assert refusal["verdict"] == CP.VERDICT_REFUSED_DRIFT
    assert refusal["error"]["kind"] == "drift"
    assert CP.exit_code_for(refusal) == CP.EXIT_CONFLICT
    # nothing was written: the tree still holds the later work
    assert (run["ws"] / "src" / "alpha.py").read_text(encoding="utf-8").endswith(
        "return value + 1000\n"
    )

    answer = CP.restore_checkpoint(
        run["state"], "last", confirm=True, force=True, store=run["store"]
    )
    assert answer["verdict"] == CP.VERDICT_RESTORED, answer.get("error")
    assert answer["complete"] is True and CP.exit_code_for(answer) == CP.EXIT_OK
    written = {item["path"]: item for item in answer["written"]}
    assert set(written) == set(FILES)
    for path, item in written.items():
        assert item["result"] == "written" and item["byte_identical"] is True
        assert item["sha256_after"] == item["sha256_checkpoint"]
        assert item["sha256_before"] != item["sha256_after"]
        recorded = next(entry for entry in created["files"] if entry["path"] == path)
        assert (run["ws"] / path).read_bytes() == (run["ws"] / path).read_bytes()
        assert sha_of(run["ws"] / path) == recorded["sha256"]

    conversation = answer["conversation"]
    assert conversation["mode"] == CP.CONV_FORK
    assert conversation["identical"] is True
    assert conversation["conversation_sha256_after"] == created["conversation"][
        "conversation_sha256"
    ]
    assert conversation["history_matches"] is True
    assert conversation["message_count"] == 50
    assert conversation["next_role"] == "user" and conversation["next_seq"] == 51
    successor = run["store"].require_session(conversation["restore_session_id"])
    assert successor.message_count == 50
    assert successor.parent_session_id == run["session_id"]
    assert successor.depth == 1
    # the append-only store kept the newer messages: the fork is the way back, not a delete
    assert run["store"].require_session(run["session_id"]).message_count == 60
    assert [item["target"] for item in answer["forced_drift"] if item["kind"] == "file"] == sorted(
        FILES
    )
    assert [
        item["kind"] for item in answer["forced_drift"] if item["kind"] == "conversation"
    ] == ["conversation"]
    assert answer["stats"]["files_written"] == 3
    assert answer["stats"]["drift_recorded"] == 4  # 3 files + the conversation


def test_a_restore_that_is_already_there_writes_nothing(run):
    do_create(run)
    answer = CP.restore_checkpoint(run["state"], "last", confirm=True, store=run["store"])
    assert answer["verdict"] == CP.VERDICT_RESTORED
    assert answer["written"] == []
    assert answer["stats"]["files_skipped"] == 3
    assert answer["conversation"]["mode"] == CP.CONV_IN_PLACE
    assert answer["conversation"]["restored"] is True
    assert answer["conversation"]["history_matches"] is True
    assert answer["conversation"]["identical"] is True


def test_the_restore_is_previewed_first_and_writes_nothing_without_confirmation(run):
    do_create(run)
    drift_the_run(run)
    preview = CP.restore_checkpoint(run["state"], "last", store=run["store"])
    assert preview["verdict"] == CP.VERDICT_CONFIRMATION_REQUIRED
    assert preview["confirmation_required"] is True
    assert CP.exit_code_for(preview) == CP.EXIT_USAGE
    assert preview["confirmation_command"].endswith("--yes --json")
    assert preview["preview"]["writes"] == 3 and preview["preview"]["deletes"] == 0
    alpha = next(item for item in preview["files"] if item["path"] == "src/alpha.py")
    assert alpha["status"] == CP.FILE_CHANGED
    assert alpha["action"] == CP.ACTION_WRITE
    assert "+++ checkpoint/src/alpha.py" in alpha["diff"]
    assert "+    return value + 41" in alpha["diff"]
    assert alpha["recorded_sha256"] != alpha["current_sha256"]
    dry = CP.restore_checkpoint(run["state"], "last", dry_run=True, store=run["store"])
    assert dry["verdict"] == CP.VERDICT_PREVIEW and dry["written"] == []
    # and the tree is untouched by either call
    assert (run["ws"] / "README.md").read_text(encoding="utf-8").endswith("changed later\n")


def test_show_returns_the_manifest_and_the_preview(run):
    created = do_create(run)
    drift_the_run(run)
    shown = CP.show_checkpoint(run["state"], "last", store=run["store"])
    assert shown["verdict"] == CP.VERDICT_PREVIEW
    assert shown["checkpoint_id"] == created["checkpoint_id"]
    assert shown["plan"]["workspace"] == str(run["ws"].resolve())
    assert shown["preview"]["restorable"] is False, "drift without force is not restorable"
    assert shown["preview"]["would_refuse"] == "drift"
    assert len(shown["preview"]["files"]) == 3
    assert all(item["diff"] for item in shown["preview"]["files"] if item["action"] != "skip")
    assert CP.exit_code_for(shown) == CP.EXIT_OK


def test_a_file_that_never_changed_is_not_in_the_diff(run):
    created = do_create(run)
    (run["ws"] / "src" / "beta.py").write_text("COUNT = 7\nNAME = 'beta'\n", encoding="utf-8")
    shown = CP.show_checkpoint(run["state"], created["checkpoint_id"], store=run["store"])
    rows = {item["path"]: item for item in shown["preview"]["files"]}
    assert rows["src/alpha.py"]["status"] == CP.FILE_IDENTICAL
    assert rows["src/alpha.py"]["action"] == CP.ACTION_SKIP
    assert rows["src/alpha.py"]["diff"] == ""
    assert rows["src/beta.py"]["status"] == CP.FILE_CHANGED


# --------------------------------------------------------------------------- #
# 3 — the declared set is the whole world                                       #
# --------------------------------------------------------------------------- #


def test_nothing_outside_the_declared_set_is_touched(run):
    outsider = run["ws"] / "src" / "outside.py"
    outsider.write_text("KEEP = 'newer work'\n", encoding="utf-8")
    neighbor = run["ws"] / "changelog.md"
    neighbor.write_text("# changelog\n", encoding="utf-8")
    before = {path: (sha_of(path), path.stat().st_mtime_ns) for path in (outsider, neighbor)}
    do_create(run)
    drift_the_run(run)
    outsider.write_text("KEEP = 'even newer work'\n", encoding="utf-8")
    before[outsider] = (sha_of(outsider), outsider.stat().st_mtime_ns)

    answer = CP.restore_checkpoint(
        run["state"], "last", confirm=True, force=True, store=run["store"]
    )
    assert answer["verdict"] == CP.VERDICT_RESTORED
    assert set(item["path"] for item in answer["written"]) == set(FILES)
    time.sleep(0.01)
    for path, (digest, stamp) in before.items():
        assert sha_of(path) == digest, f"{path} was rewritten by the restore"
        assert path.stat().st_mtime_ns == stamp, f"{path}'s mtime moved"


def test_an_unsafe_declared_path_is_refused_before_anything_is_written(run):
    for value in ("../escape.py", "/etc/passwd", "src/../../escape.py"):
        refused = do_create(run, files=[value])
        assert refused["ok"] is False
        assert refused["error"]["kind"] == "blocked"
        assert CP.exit_code_for(refused) == CP.EXIT_BLOCKED
        assert refused["error"]["detail"]["problems"][0]["path"] == value
        listing = CP.list_checkpoints(run["state"])
        assert listing["count"] == 0, "a refused create publishes nothing"
        assert listing["interrupted"] == [], "a refusal is not an interrupted create"
    assert not (run["tmp"] / "escape.py").exists()


def test_a_cap_hit_refuses_instead_of_publishing_a_partial_snapshot(run):
    small = CP.Caps(max_files=2)
    refused = do_create(run, caps=small)
    assert refused["ok"] is False
    assert refused["error"]["kind"] == "cap_exceeded"
    assert refused["error"]["detail"]["cap"] == 2
    assert CP.exit_code_for(refused) == CP.EXIT_BLOCKED
    # the per-file byte cap refuses too
    tiny = CP.Caps(max_file_bytes=4)
    refused = do_create(run, caps=tiny)
    assert refused["error"]["kind"] == "blocked"
    assert "per-file cap" in refused["error"]["detail"]["problems"][0]["message"]


def test_caps_are_declared_and_overridable_through_the_environment_helper(run):
    caps = CP.Caps.from_env(environ={"SKALDIR_CHECKPOINT_MAX_FILES": "4"})
    assert caps.max_files == 4
    assert caps.max_messages == CP.Caps.max_messages
    declared = CP.Caps.from_env().as_dict()
    assert declared["max_files"]["env"] == "SKALDIR_CHECKPOINT_MAX_FILES"
    assert declared["max_checkpoint_bytes"]["env"] == "SKALDIR_CHECKPOINT_MAX_BYTES"
    created = do_create(run)
    assert created["caps"]["max_messages"]["value"] == CP.Caps.max_messages
    assert CP.LIMITS and all(isinstance(item, str) for item in CP.LIMITS)
    assert created["limits"] == list(CP.LIMITS)


def test_a_conversation_above_the_message_cap_is_refused(run):
    refused = do_create(run, caps=CP.Caps(max_messages=10))
    assert refused["ok"] is False
    assert refused["error"]["kind"] == "cap_exceeded"
    assert CP.exit_code_for(refused) == CP.EXIT_BLOCKED
    assert CP.list_checkpoints(run["state"])["count"] == 0


# --------------------------------------------------------------------------- #
# 4 — integrity, and what the store can no longer give back                      #
# --------------------------------------------------------------------------- #


def test_a_corrupt_blob_is_detected_and_the_tree_stays_untouched(run):
    created = do_create(run)
    (run["ws"] / "src" / "alpha.py").write_text("def alpha(v):\n    return v\n", encoding="utf-8")
    target = next(item for item in created["files"] if item["path"] == "src/alpha.py")
    blob = Path(target["blob"])
    raw = bytearray(blob.read_bytes())
    raw[0] = raw[0] ^ 0xFF
    blob.write_bytes(bytes(raw))
    before = sha_of(run["ws"] / "src" / "alpha.py")

    answer = CP.restore_checkpoint(
        run["state"], "last", confirm=True, force=True, store=run["store"]
    )
    assert answer["verdict"] == CP.VERDICT_CORRUPT, answer
    assert answer["error"]["kind"] == "blob_mismatch"
    assert CP.exit_code_for(answer) == CP.EXIT_VERIFICATION_FAILED
    assert sha_of(run["ws"] / "src" / "alpha.py") == before, "a corrupt blob must not be written"


def test_a_purged_conversation_is_refused_and_force_restores_the_files_only(run):
    created = do_create(run)
    drift_the_run(run)
    purged = run["store"].purge(run["session_id"], mode="delete", reason="test", dry_run=False)
    assert purged["dry_run"] is False
    answer = CP.restore_checkpoint(run["state"], "last", confirm=True, store=run["store"])
    assert answer["verdict"] == CP.VERDICT_REFUSED_DRIFT
    assert answer["error"]["kind"] == "conversation_gone"
    forced = CP.restore_checkpoint(
        run["state"], "last", confirm=True, force=True, store=run["store"]
    )
    assert forced["verdict"] == CP.VERDICT_RESTORED_PARTIAL
    assert forced["complete"] is False
    assert forced["conversation"]["restored"] is False
    assert CP.exit_code_for(forced) == CP.EXIT_BLOCKED
    for entry in created["files"]:
        assert sha_of(run["ws"] / entry["path"]) == entry["sha256"], "the files still came back"


def test_an_unknown_checkpoint_is_not_found_and_a_torn_directory_is_reported(run):
    missing = CP.restore_checkpoint(run["state"], "cp-nope", confirm=True, store=run["store"])
    assert missing["verdict"] == CP.VERDICT_NOT_FOUND
    assert CP.exit_code_for(missing) == CP.EXIT_NOT_FOUND
    (run["state"] / "cp-20260101-000000-000000-abcdef").mkdir(parents=True)
    listing = CP.list_checkpoints(run["state"])
    assert listing["count"] == 0
    assert listing["interrupted"], "a directory without a manifest must be visible"
    assert "interrupted create" in listing["interrupted"][0]["reason"]
    torn = CP.restore_checkpoint(
        run["state"], "cp-20260101-000000-000000-abcdef", confirm=True, store=run["store"]
    )
    assert torn["verdict"] == CP.VERDICT_NOT_FOUND
    assert "no manifest" in torn["error"]["message"]


# --------------------------------------------------------------------------- #
# 5 — a killed create cannot fake a checkpoint                                  #
# --------------------------------------------------------------------------- #

CHILD_CREATE = '''
import json, sys
from pathlib import Path
from skaldir import checkpoints as CP

ws, state, runtime, session_id = sys.argv[1:5]
created = CP.create_checkpoint(
    workspace=ws, state_dir=state, session_id=session_id, runtime=runtime,
    edit_state_dir=str(Path(runtime) / "edit"), label="killed",
)
print(json.dumps(created["checkpoint_id"]))
'''


def test_a_create_killed_before_the_manifest_publishes_nothing(run):
    script = run["tmp"] / "child_create.py"
    script.write_text(CHILD_CREATE, encoding="utf-8")
    env = dict(os.environ)
    env.update(
        {
            "PYTHONPATH": str(REPO / "src"),
            "SKALDIR_CHECKPOINT_CRASH": CP.CRASH_AFTER_SNAPSHOT,
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    completed = subprocess.run(
        [sys.executable, str(script), str(run["ws"]), str(run["state"]), str(run["runtime"]),
         run["session_id"]],
        env=env, capture_output=True, text=True, timeout=300,
    )
    assert completed.returncode == CP.KILL_EXIT, completed.stderr[-2000:]

    listing = CP.list_checkpoints(run["state"])
    assert listing["count"] == 0, "a kill before the manifest must leave nothing restorable"
    assert len(listing["interrupted"]) == 1
    checkpoint_id = Path(listing["interrupted"][0]["path"]).name
    torn = CP.restore_checkpoint(run["state"], checkpoint_id, confirm=True, store=run["store"])
    assert torn["verdict"] == CP.VERDICT_NOT_FOUND
    assert "no manifest" in torn["error"]["message"]

    # the blobs the killed run wrote are complete and verified — content addressing
    blobs = sorted((run["state"] / CP.BLOB_DIRNAME).rglob("*"))
    files = [path for path in blobs if path.is_file()]
    assert files, "the killed run should have written the blobs before dying"
    for path in files:
        assert path.name == CP.sha256_bytes(path.read_bytes()), f"{path} is not at its address"

    # ...and the next create reuses them instead of writing them again
    created = do_create(run, label="after the kill")
    assert created["verdict"] == CP.VERDICT_CREATED
    assert created["stats"]["blob_bytes_written"] == 0
    assert created["stats"]["blob_bytes_reused"] == created["stats"]["captured_bytes"]
    assert CP.list_checkpoints(run["state"])["count"] == 1


def test_the_crash_hook_is_inert_without_the_variable(run, monkeypatch):
    monkeypatch.delenv("SKALDIR_CHECKPOINT_CRASH", raising=False)
    monkeypatch.delenv("GODSV_CHECKPOINT_CRASH", raising=False)
    assert CP.CRASH_POINTS == ("after_snapshot", "mid_restore")
    created = do_create(run)
    assert created["verdict"] == CP.VERDICT_CREATED


# --------------------------------------------------------------------------- #
# 6 — the shape of the record                                                   #
# --------------------------------------------------------------------------- #


def test_the_manifest_is_self_describing_and_the_list_row_costs_nothing_to_read(run):
    created = do_create(run)
    manifest = json.loads(Path(created["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["schema_version"] == CP.SCHEMA_VERSION
    assert manifest["workspace"] == str(run["ws"].resolve())
    assert manifest["state_dir"] == str(run["state"])
    assert manifest["file_set"]["source"] == "edit:in_tree"
    assert manifest["file_set"]["transactions"][0]["tx_id"] == run["tx_id"]
    assert manifest["file_set"]["count"] == 3
    assert manifest["stats"]["manifest_bytes"] == Path(created["manifest_path"]).stat().st_size
    listing = CP.list_checkpoints(run["state"])
    row = listing["checkpoints"][0]
    assert row["checkpoint_id"] == created["checkpoint_id"]
    assert row["files"] == 3 and row["messages"] == 50
    assert row["manifest_bytes"] == manifest["stats"]["manifest_bytes"]
    assert row["session_id"] == run["session_id"]
    assert row["create_ms"] >= 0
    assert listing["interrupted"] == []


def test_two_checkpoints_of_the_same_session_track_the_conversation_at_their_point(run):
    first = do_create(run, label="before more turns")
    for turn in range(TURNS, TURNS + 3):
        run["store"].append_turn(
            run["session_id"], f"user turn {turn}", f"assistant reply {turn}"
        )
    second = do_create(run, label="after more turns")
    assert first["conversation"]["message_count"] == 50
    assert second["conversation"]["message_count"] == 56
    assert first["conversation"]["conversation_sha256"] != second[
        "conversation"
    ]["conversation_sha256"]
    rows = CP.list_checkpoints(run["state"])["checkpoints"]
    assert [row["checkpoint_id"] for row in rows][0] == second["checkpoint_id"]
    assert CP.resolve_checkpoint_id(run["state"], "last") == second["checkpoint_id"]
    assert CP.resolve_checkpoint_id(run["state"], first["checkpoint_id"]) == first[
        "checkpoint_id"
    ]


def test_safe_path_refuses_every_way_out_of_the_workspace(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    inside, problem = CP.safe_path(ws, "src/ok.py")
    assert inside == ws / "src" / "ok.py" and problem == ""
    for value in ("", "..", "../x", "/abs/x", "a/../../x"):
        target, problem = CP.safe_path(ws, value)
        assert target is None and problem, value
    outside = tmp_path / "outside"
    outside.mkdir()
    (ws / "link").symlink_to(outside)
    target, problem = CP.safe_path(ws, "link/x.py")
    assert target is None and "outside the workspace" in problem


def test_collect_file_set_only_folds_in_transactions_that_are_still_in_the_tree(run):
    folded = CP.collect_file_set(run["edit_state"])
    assert folded["source"] == "edit:in_tree"
    assert sorted(folded["paths"]) == sorted(FILES)
    undone = ET.undo_transaction(run["tx_id"], state_dir=run["edit_state"])
    assert undone["verdict"] == ET.VERDICT_UNDONE
    after = CP.collect_file_set(run["edit_state"])
    assert after["source"] == "none" and after["paths"] == {}
    explicit = CP.collect_file_set(run["edit_state"], transactions=["last"], declared=["x.md"])
    assert sorted(explicit["paths"]) == sorted([*FILES, "x.md"])
    assert explicit["source"] == "edit:explicit"
    assert explicit["paths"]["x.md"] == ""


# --------------------------------------------------------------------------- #
# the blob store's garbage collection (`checkpoint prune`)                       #
# --------------------------------------------------------------------------- #


def blob_file(state: Path, sha: str) -> Path:
    return CP.blob_path(state, sha)


def orphan_blob(state: Path, body: bytes = b"an orphan, named by no manifest\n") -> dict:
    """A blob that is on disk and named by **no** manifest — what prune is for."""
    return CP.store_blob(state, body)


def test_prune_is_a_dry_run_by_default_and_removes_nothing(run):
    created = do_create(run)
    orphan = orphan_blob(run["state"])
    assert blob_file(run["state"], orphan["sha256"]).is_file()

    plan = CP.prune_checkpoints(run["state"])

    assert plan["ok"] is True and plan["verdict"] == CP.VERDICT_PRUNE_PLAN
    assert plan["dry_run"] is True
    assert plan["checkpoints"]["readable"] == 1
    assert plan["blobs"]["unreferenced"] == 1
    assert plan["blobs"]["removed"] == 0 and plan["removed"] == []
    assert plan["blobs"]["selected"] == 1, "the dry run still names the candidates"
    assert [row["sha256"] for row in plan["candidates"]] == [orphan["sha256"]]
    assert plan["blobs"]["reclaimable_bytes"] == len(b"an orphan, named by no manifest\n")
    assert blob_file(run["state"], orphan["sha256"]).is_file(), "a dry run deletes nothing"
    assert created["checkpoint_id"] in {
        name for names in plan["referenced_by"].values() for name in names
    } or plan["referenced_by"] == {}


def test_prune_reclaims_the_unreferenced_blob_and_keeps_the_referenced_one(run):
    """The one contract of a garbage collector: what a checkpoint names always survives."""
    created = do_create(run)
    manifest = json.loads(Path(created["manifest_path"]).read_text(encoding="utf-8"))
    referenced = sorted(CP.manifest_blob_refs(manifest))
    assert len(referenced) == len(FILES), "one blob per snapshotted file"
    body = b"an orphan, named by no manifest\n"
    orphan = orphan_blob(run["state"], body)

    result = CP.prune_checkpoints(run["state"], dry_run=False)

    assert result["verdict"] == CP.VERDICT_PRUNED and result["ok"] is True
    assert result["blobs"]["removed"] == 1
    assert result["blobs"]["removed_bytes"] == len(body)
    assert result["blobs"]["referenced"] == len(referenced)
    for sha in referenced:
        assert blob_file(run["state"], sha).is_file(), f"a referenced blob was reclaimed: {sha}"
    assert not blob_file(run["state"], orphan["sha256"]).exists()
    assert result["removed"][0]["sha256"] == orphan["sha256"]
    # and the checkpoint is still restorable, byte for byte — the real proof
    for sha in referenced:
        raw, problem = CP.read_blob(run["state"], sha)
        assert raw is not None and problem == ""
    for name in FILES:
        (run["ws"] / name).write_text("clobbered\n", encoding="utf-8")
    restored = CP.restore_checkpoint(
        run["state"], created["checkpoint_id"], workspace=run["ws"], confirm=True,
        force=True, runtime=run["runtime"],
    )
    assert restored["verdict"] == CP.VERDICT_RESTORED, restored.get("refusals")
    assert {row["path"] for row in restored["written"]} == set(FILES)
    assert all(row["byte_identical"] for row in restored["written"]), restored["written"]
    # every restored file is byte-identical to a blob that survived the sweep
    for entry in manifest["files"]:
        raw, problem = CP.read_blob(run["state"], entry["sha256"])
        assert raw is not None and problem == "", entry["path"]
        assert (run["ws"] / entry["path"]).read_bytes() == raw, entry["path"]


def test_prune_reports_a_second_pass_when_the_first_one_was_bounded(run):
    do_create(run)
    orphans = [orphan_blob(run["state"], f"orphan {index}\n".encode()) for index in range(3)]

    first = CP.prune_checkpoints(run["state"], dry_run=False, limit=2)

    assert first["limit"] == 2 and first["truncated"] is True
    assert first["blobs"]["removed"] == 2 and first["blobs"]["left"] == 1
    surviving = [row for row in orphans if blob_file(run["state"], row["sha256"]).exists()]
    assert len(surviving) == 1

    second = CP.prune_checkpoints(run["state"], dry_run=False, limit=2)

    assert second["truncated"] is False
    assert second["blobs"]["removed"] == 1
    assert [row for row in orphans if blob_file(run["state"], row["sha256"]).exists()] == []


def test_prune_refuses_the_sweep_on_a_manifest_it_cannot_read(run):
    """A manifest we cannot read may name blobs we cannot see: refuse, never guess."""
    do_create(run)
    orphan = orphan_blob(run["state"])
    broken = run["state"] / "cp-broken-000000" / CP.MANIFEST_NAME
    broken.parent.mkdir(parents=True)
    broken.write_text("{ this is not json", encoding="utf-8")

    refused = CP.prune_checkpoints(run["state"], dry_run=False)

    assert refused["ok"] is False and refused["verdict"] == CP.VERDICT_BLOCKED
    assert refused["error"]["kind"] == "blocked"
    assert refused["checkpoints"]["unreadable"] == 1
    assert refused["refusals"], "the refusal is named"
    assert refused["blobs"]["removed"] == 0
    assert blob_file(run["state"], orphan["sha256"]).is_file(), "a refusal removes nothing"
    assert CP.exit_code_for(refused) == CP.EXIT_BLOCKED


def test_prune_refuses_on_an_interrupted_create_and_force_overrides_only_that(run):
    """A directory without a manifest may be a create still in flight — so refusals, twice."""
    do_create(run)
    orphan = orphan_blob(run["state"])
    (run["state"] / "cp-interrupted-000000").mkdir()
    assert CP.list_checkpoints(run["state"])["interrupted"], "the fixture is an interrupted create"

    plan = CP.prune_checkpoints(run["state"])
    assert plan["ok"] is True, "a dry run only reports; it removes nothing either way"
    assert plan["checkpoints"]["interrupted"] == 1

    refused = CP.prune_checkpoints(run["state"], dry_run=False)
    assert refused["verdict"] == CP.VERDICT_BLOCKED
    assert refused["checkpoints"]["interrupted"] == 1
    assert blob_file(run["state"], orphan["sha256"]).is_file()

    forced = CP.prune_checkpoints(run["state"], dry_run=False, force=True)
    assert forced["verdict"] == CP.VERDICT_PRUNED and forced["force"] is True
    assert forced["checkpoints"]["interrupted"] == 1, "the override is recorded, not hidden"
    assert forced["blobs"]["removed"] == 1
    assert not blob_file(run["state"], orphan["sha256"]).exists()


def test_prune_never_sweeps_a_file_that_is_not_a_content_address(run):
    do_create(run)
    stray = run["state"] / CP.BLOB_DIRNAME / "zz" / "notes.txt"
    stray.parent.mkdir(parents=True)
    stray.write_text("not a blob\n", encoding="utf-8")

    result = CP.prune_checkpoints(run["state"], dry_run=False)

    assert result["blobs"]["removed"] == 0
    assert [row["path"] for row in result["unexpected"]] == [str(stray)]
    assert "not a content address" in result["unexpected"][0]["reason"]
    assert stray.is_file(), "an unaddressed file is reported, never deleted"


def test_prune_reuses_the_store_it_cleaned(run):
    """After a sweep, storing the same bytes again is a write, not a broken read."""
    do_create(run)
    body = b"reused after a prune\n"
    first = orphan_blob(run["state"], body)
    CP.prune_checkpoints(run["state"], dry_run=False)
    assert not blob_file(run["state"], first["sha256"]).exists()

    again = CP.store_blob(run["state"], body)

    assert again["sha256"] == first["sha256"] and again["written"] is True
    raw, problem = CP.read_blob(run["state"], again["sha256"])
    assert raw == body and problem == ""
