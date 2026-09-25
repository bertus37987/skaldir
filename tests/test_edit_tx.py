"""FINAL-3 — the edit-transaction engine (``skaldir.edit_tx``).

What these tests pin, in the order the claims matter:

* a real multi-file transaction applies, verifies and records (1–4),
* a dry run prints the diff and writes nothing, and **exits with the code the apply
  would exit with** (5–7),
* every refusal is a refusal, not a best effort: ambiguous, missing, near-duplicate,
  cascade, no-op (8–13),
* the caps and the policy refusals: size, transaction size, counts, needles, binary
  files, deny globs, workspace containment, symlink escapes (14–20),
* undo is byte-identical, and refuses when a touched file moved (21–24),
* the declared verification really runs — a failing check rolls the transaction back and
  a kept one is never reported verified (25–29),
* the transaction that moved under the plan is refused without publishing (30),
* housekeeping: caps report, environment overrides, prune, records (31–35).
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

from skaldir import edit_tx as ET

# --------------------------------------------------------------------------- #
# fixtures + helpers                                                            #
# --------------------------------------------------------------------------- #


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """A scratch tree of real-looking sources — never the live repository."""
    root = tmp_path / "ws"
    (root / "src").mkdir(parents=True)
    (root / "src" / "alpha.py").write_text(
        "import os\n\n\ndef alpha(value):\n    return value + 1\n\n\n"
        "def beta(value):\n    return value - 1\n",
        encoding="utf-8",
    )
    (root / "src" / "beta.py").write_text(
        "SETTING = 'old'\nOTHER = 2\n", encoding="utf-8"
    )
    (root / "notes.md").write_text("# notes\n\nalpha holds the first value\n", encoding="utf-8")
    return root


@pytest.fixture
def state(tmp_path: Path) -> Path:
    return tmp_path / "state"


def document(*edits: tuple[str, str, str], **extra) -> dict:
    """A one-edit-per-file document from ``(path, old, new)`` triples."""
    files: dict[str, dict] = {}
    for path, old, new in edits:
        files.setdefault(path, {"path": path, "edits": []})
        files[path]["edits"].append({"old": old, "new": new})
    doc: dict = {"files": list(files.values())}
    doc.update(extra)
    return doc


def spec(*edits: tuple[str, str, str], **extra) -> dict:
    parsed, problem = ET.parse_spec(document(*edits, **extra))
    assert parsed is not None, problem
    return parsed


def reason_of(record: dict, path: str = "") -> str:
    for item in record["files"]:
        if not path or item["path"] == path:
            return str(item["reason"])
    return ""


def run(spec_dict: dict, workspace: Path, state: Path, **kwargs) -> dict:
    return ET.run_transaction(spec_dict, workspace, state_dir=state, **kwargs)


# --------------------------------------------------------------------------- #
# 1–4: a real transaction                                                       #
# --------------------------------------------------------------------------- #


def test_a_three_file_transaction_applies_and_verifies_syntax(workspace, state):
    document_paths = ["src/alpha.py", "src/beta.py", "notes.md"]
    before = {path: digest(workspace / path) for path in document_paths}
    record = run(
        spec(
            ("src/alpha.py", "    return value + 1", "    return value + 2"),
            ("src/beta.py", "SETTING = 'old'", "SETTING = 'new'"),
            ("notes.md", "alpha holds the first value", "alpha holds the second value"),
        ),
        workspace,
        state,
    )
    assert record["verdict"] == ET.VERDICT_APPLIED
    assert ET.exit_code_for(record) == 0
    assert record["applied"] is True and record["in_tree"] is True
    assert [item["ok"] for item in record["files"]] == [True, True, True]
    assert all(item["strategy"] == "exact" and item["matches"] == 1 for item in record["files"])
    assert all(
        before[item["path"]] != digest(workspace / item["path"]) for item in record["files"]
    )
    # the syntax check really parsed the three changed files
    assert record["verification"]["kind"] == "syntax"
    assert record["verification"]["executed"] is True
    assert record["verification"]["passed"] is True
    assert [check["parser"] for check in record["verification"]["checks"]] == [
        "python-compile", "python-compile", "",
    ]
    assert record["verified"] is True
    # the record and its snapshot are on disk, and the snapshot holds the pre-images
    record_path = Path(record["record_path"])
    assert record_path.is_file()
    snapshot = Path(record["snapshot"]["dir"])
    assert (snapshot / "diff.patch").is_file()
    assert snapshot.joinpath("pre").is_dir()
    stored = json.loads(record_path.read_text(encoding="utf-8"))
    assert stored["tx_id"] == record["tx_id"]
    assert len(stored["snapshot"]["pre"]) == 3
    assert stored["snapshot"]["bytes"] == sum(item["bytes_before"] for item in stored["files"]), (
        "the snapshot accounting is the pre-image bytes, not the published bytes"
    )


def test_the_snapshot_pre_image_is_the_original_bytes(workspace, state):
    original = (workspace / "src" / "beta.py").read_bytes()
    record = run(spec(("src/beta.py", "OTHER = 2", "OTHER = 3")), workspace, state)
    entry = record["snapshot"]["pre"][0]
    assert Path(entry["pre_file"]).read_bytes() == original
    assert entry["before_sha256"] == hashlib.sha256(original).hexdigest()
    assert entry["after_sha256"] == digest(workspace / "src" / "beta.py")


def test_a_fuzzy_tolerant_match_reports_its_strategy(workspace, state):
    """The needle differs in trailing whitespace: the ladder still finds it, once."""
    record = run(
        spec(("src/alpha.py", "    return value + 1   ", "    return value + 7")),
        workspace,
        state,
    )
    assert record["verdict"] == ET.VERDICT_APPLIED
    assert record["files"][0]["strategy"] == "trailing-whitespace"
    assert "return value + 7" in (workspace / "src" / "alpha.py").read_text(encoding="utf-8")


def test_every_record_states_its_caps_and_its_limits(workspace, state):
    record = run(spec(("src/beta.py", "OTHER = 2", "OTHER = 3")), workspace, state)
    assert set(record["caps"]) == {
        "max_file_bytes", "max_tx_bytes", "max_files", "max_edits",
        "max_needle_chars", "max_diff_bytes", "verify_timeout_s", "keep_transactions",
    }
    assert all(row["env"].startswith("SKALDIR_EDIT_") for row in record["caps"].values())
    assert record["limits"] and any("crash" in text for text in record["limits"])
    assert record["caps_hit"] == []


# --------------------------------------------------------------------------- #
# 5–7: the dry run                                                               #
# --------------------------------------------------------------------------- #


def test_the_dry_run_shows_the_diff_and_writes_nothing(workspace, state):
    before = {item.name: digest(item) for item in (workspace / "src").iterdir()}
    record = run(
        spec(
            ("src/alpha.py", "    return value + 1", "    return value + 42"),
            ("notes.md", "alpha holds the first value", "alpha holds the last value"),
        ),
        workspace,
        state,
        dry_run=True,
    )
    assert record["verdict"] == ET.VERDICT_APPLICABLE
    assert record["dry_run"] is True and record["applied"] is False
    assert record["snapshot"]["dir"] == ""
    assert "-    return value + 1" in record["diff"]
    assert "+    return value + 42" in record["diff"]
    assert record["verification"]["executed"] is False
    assert record["verification"]["note"] == "declared; a dry-run executes nothing"
    assert not state.exists(), "a dry run leaves no state directory behind"
    assert {item.name: digest(item) for item in (workspace / "src").iterdir()} == before
    assert "alpha holds the last value" not in (workspace / "notes.md").read_text(
        encoding="utf-8"
    )


def test_a_refused_apply_leaves_no_record_and_no_snapshot(workspace, state):
    record = run(
        spec(("src/alpha.py", "    return value + 1", "    return value + 2"),
             ("src/beta.py", "this needle is not here", "x = 1")),
        workspace,
        state,
    )
    assert record["verdict"] == ET.VERDICT_NOT_APPLICABLE
    assert record["record_path"] == ""
    assert not state.exists()
    assert "return value + 2" not in (workspace / "src" / "alpha.py").read_text(
        encoding="utf-8"
    ), "the file that *could* be edited must not be published when a sibling is refused"


@pytest.mark.parametrize(
    "edits, expected",
    [
        ((("src/beta.py", "SETTING = 'old'", "SETTING = 'new'"),), 0),
        ((("src/beta.py", "SETTING = 'old'", "SETTING = 'old'"),), 1),
        ((("src/beta.py", "missing entirely", "x"),), 5),
        ((("src/alpha.py", "def ", "def  "),), 6),
        ((("../../etc/passwd", "root", "toor"),), 3),
    ],
)
def test_the_dry_run_exits_with_the_code_the_apply_would_exit_with(
    workspace, state, edits, expected
):
    spec_dict = spec(*edits)
    dry = run(spec_dict, workspace, state, dry_run=True)
    applied = run(spec_dict, workspace, state)
    assert ET.exit_code_for(dry) == expected
    assert ET.exit_code_for(applied) == expected == ET.exit_code_for(dry)


# --------------------------------------------------------------------------- #
# 8–13: refusals, never best efforts                                             #
# --------------------------------------------------------------------------- #


def test_an_ambiguous_needle_is_refused_and_the_tree_is_untouched(workspace, state):
    (workspace / "twice.py").write_text("value = 1\nother = 2\nvalue = 1\n", encoding="utf-8")
    before = digest(workspace / "twice.py")
    record = run(spec(("twice.py", "value = 1", "value = 9")), workspace, state)
    assert record["verdict"] == ET.VERDICT_NOT_APPLICABLE
    assert reason_of(record) == ET.REASON_AMBIGUOUS
    assert record["files"][0]["detail"]["matches"] == 2
    assert record["files"][0]["detail"]["occurrence_lines"] == [1, 3]
    assert ET.exit_code_for(record) == 6
    assert digest(workspace / "twice.py") == before
    assert not state.exists()


def test_a_missing_needle_is_not_found(workspace, state):
    record = run(spec(("src/beta.py", "NOWHERE = 1", "NOWHERE = 2")), workspace, state)
    assert reason_of(record) == ET.REASON_NOT_FOUND
    assert ET.exit_code_for(record) == 5
    assert "similar" in record["files"][0]["detail"]["tried"]


def test_a_near_duplicate_region_is_refused_as_would_change_other_occurrences(workspace, state):
    """The literal needle is unique; its indentation-normalised twin is not."""
    (workspace / "twins.py").write_text(
        "def first():\n    value = compute(1)\n    return value\n\n\n"
        "def second():\n        value = compute(1)\n        return value\n",
        encoding="utf-8",
    )
    before = digest(workspace / "twins.py")
    strict = run(
        spec(("twins.py", "    value = compute(1)\n    return value",
              "    value = compute(2)\n    return value")),
        workspace,
        state,
    )
    assert reason_of(strict) == ET.REASON_OTHER_OCCURRENCES
    assert strict["files"][0]["detail"]["cause"] == "near_duplicate_regions"
    assert strict["files"][0]["detail"]["occurrence_lines"] == [2, 7]
    assert ET.exit_code_for(strict) == 6
    assert digest(workspace / "twins.py") == before
    # the same document, applied with the guard off, lands in exactly one place
    relaxed = run(
        spec(("twins.py", "    value = compute(1)\n    return value",
              "    value = compute(2)\n    return value"), strict=False),
        workspace,
        state,
    )
    assert relaxed["verdict"] == ET.VERDICT_APPLIED
    text = (workspace / "twins.py").read_text(encoding="utf-8")
    assert text.count("compute(2)") == 1 and text.count("compute(1)") == 1


def test_declared_counts_make_replace_all_honest(workspace, state):
    (workspace / "twice.py").write_text("value = 1\nother = 2\nvalue = 1\n", encoding="utf-8")
    document_with_all = {
        "files": [
            {
                "path": "twice.py",
                "edits": [
                    {"old": "value = 1", "new": "value = 9", "replace_all": True,
                     "expect_occurrences": 1}
                ],
            }
        ]
    }
    parsed, problem = ET.parse_spec(document_with_all)
    assert parsed is not None, problem
    record = run(parsed, workspace, state)
    assert reason_of(record) == ET.REASON_OTHER_OCCURRENCES
    assert record["files"][0]["detail"]["cause"] == "occurrence_count_mismatch"
    assert record["files"][0]["detail"]["matches"] == 2
    assert (workspace / "twice.py").read_text(encoding="utf-8").count("value = 9") == 0

    document_with_all["files"][0]["edits"][0]["expect_occurrences"] = 2
    parsed, problem = ET.parse_spec(document_with_all)
    assert parsed is not None, problem
    applied = run(parsed, workspace, state)
    assert applied["verdict"] == ET.VERDICT_APPLIED
    assert applied["files"][0]["matches"] == 2
    assert (workspace / "twice.py").read_text(encoding="utf-8").count("value = 9") == 2


def test_an_undeclared_replace_all_is_refused(workspace, state):
    document_with_all = {
        "files": [
            {"path": "src/beta.py",
             "edits": [{"old": "SETTING = 'old'", "new": "SETTING = 'new'", "replace_all": True}]},
        ]
    }
    parsed, problem = ET.parse_spec(document_with_all)
    assert parsed is not None, problem
    record = run(parsed, workspace, state)
    assert reason_of(record) == ET.REASON_OTHER_OCCURRENCES
    assert record["files"][0]["detail"]["cause"] == "occurrence_count_mismatch"
    assert "unstated" in record["files"][0]["message"]


def test_a_cascade_edit_is_refused(workspace, state):
    """A later edit inside one file must not match text an earlier edit inserted."""
    document_two_edits = {
        "files": [
            {
                "path": "src/beta.py",
                "edits": [
                    {"old": "OTHER = 2", "new": "OTHER = 2\nINSERTED = 5"},
                    {"old": "INSERTED = 5", "new": "INSERTED = 6"},
                ],
            }
        ]
    }
    parsed, problem = ET.parse_spec(document_two_edits)
    assert parsed is not None, problem
    before = digest(workspace / "src" / "beta.py")
    record = run(parsed, workspace, state)
    assert reason_of(record) == ET.REASON_CASCADE
    assert "inserted by edit #1" in record["files"][0]["message"]
    assert digest(workspace / "src" / "beta.py") == before
    assert ET.exit_code_for(record) == 6


def test_a_noop_edit_is_a_usage_refusal(workspace, state):
    record = run(spec(("src/beta.py", "OTHER = 2", "OTHER = 2")), workspace, state)
    assert reason_of(record) == ET.REASON_UNCHANGED
    assert ET.exit_code_for(record) == 1


def test_an_edit_list_applies_in_order_inside_one_file(workspace, state):
    document_two_edits = {
        "files": [
            {
                "path": "src/alpha.py",
                "edits": [
                    {"old": "def alpha(value):", "new": "def alpha(value, extra=1):"},
                    {"old": "def beta(value):", "new": "def beta(value, extra=1):"},
                ],
            }
        ]
    }
    parsed, problem = ET.parse_spec(document_two_edits)
    assert parsed is not None, problem
    record = run(parsed, workspace, state)
    assert record["verdict"] == ET.VERDICT_APPLIED
    assert [item["edit_index"] for item in record["files"][0]["edits"]] == [1, 2]
    text = (workspace / "src" / "alpha.py").read_text(encoding="utf-8")
    assert "def alpha(value, extra=1):" in text and "def beta(value, extra=1):" in text


# --------------------------------------------------------------------------- #
# 14–20: policy refusals and caps                                               #
# --------------------------------------------------------------------------- #


def test_a_path_outside_the_workspace_is_refused(workspace, state, tmp_path):
    outside = tmp_path / "outside.py"
    outside.write_text("secret = 1\n", encoding="utf-8")
    for path in ("../outside.py", str(outside)):
        record = run(spec((path, "secret = 1", "secret = 2")), workspace, state)
        assert record["verdict"] == ET.VERDICT_BLOCKED, path
        assert "escapes the declared root" in record["files"][0]["message"]
        assert ET.exit_code_for(record) == 3
    assert outside.read_text(encoding="utf-8") == "secret = 1\n"


def test_a_symlink_that_escapes_the_workspace_is_refused(workspace, state, tmp_path):
    outside = tmp_path / "outside.py"
    outside.write_text("secret = 1\n", encoding="utf-8")
    (workspace / "link_escape.py").symlink_to(outside)
    record = run(spec(("link_escape.py", "secret = 1", "secret = 2")), workspace, state)
    assert record["verdict"] == ET.VERDICT_BLOCKED
    assert "escapes the declared root" in record["files"][0]["message"]


def test_a_symlink_inside_the_workspace_is_allowed_and_reported(workspace, state):
    (workspace / "inner.py").write_text("inside = 1\n", encoding="utf-8")
    (workspace / "link_inside.py").symlink_to(workspace / "inner.py")
    record = run(spec(("link_inside.py", "inside = 1", "inside = 2")), workspace, state)
    assert record["verdict"] == ET.VERDICT_APPLIED
    assert record["files"][0]["via_symlink"] is True
    assert (workspace / "inner.py").read_text(encoding="utf-8") == "inside = 2\n"


def test_a_binary_file_is_refused(workspace, state):
    (workspace / "blob.bin").write_bytes(b"\x00\x01\x02payload")
    record = run(spec(("blob.bin", "payload", "other")), workspace, state)
    assert record["verdict"] == ET.VERDICT_BLOCKED
    assert "binary" in record["files"][0]["message"]


def test_a_file_over_the_per_file_cap_is_refused(workspace, state):
    (workspace / "big.py").write_text("line = 1\n" * 40_000, encoding="utf-8")
    caps = ET.EditCaps(max_file_bytes=1024)
    record = run(spec(("big.py", "line = 1", "line = 2")), workspace, state, caps=caps)
    assert record["verdict"] == ET.VERDICT_BLOCKED
    assert "file cap" in record["files"][0]["message"]
    assert record["files"][0]["detail"]["cap"] == 1024


def test_the_transaction_byte_cap_is_refused_and_named(workspace, state):
    (workspace / "one.py").write_text("value = 1\n" * 5000, encoding="utf-8")
    (workspace / "two.py").write_text("value = 1\n" * 5000, encoding="utf-8")
    caps = ET.EditCaps(max_tx_bytes=20_000)
    record = run(
        spec(("one.py", "value = 1", "value = 2"), ("two.py", "value = 1", "value = 2")),
        workspace,
        state,
        caps=caps,
    )
    assert record["verdict"] == ET.VERDICT_BLOCKED
    assert record["caps_hit"] == ["max_tx_bytes"], "a cap hit is declared once, not per file"
    assert "transaction cap" in record["blocked"]["message"]
    assert (workspace / "one.py").read_text(encoding="utf-8").count("value = 2") == 0


def test_the_file_and_edit_count_caps_are_refused(workspace, state):
    many = document(*[("src/beta.py", "SETTING = 'old'", "SETTING = 'new'")] * 3)
    parsed, problem = ET.parse_spec(many)
    assert parsed is not None, problem
    record = run(parsed, workspace, state, caps=ET.EditCaps(max_edits=2))
    assert record["verdict"] == ET.VERDICT_BLOCKED
    assert "above the 2-edit cap" in record["blocked"]["message"]

    record = run(parsed, workspace, state, caps=ET.EditCaps(max_files=0))
    assert record["verdict"] == ET.VERDICT_BLOCKED
    assert "0-file cap" in record["blocked"]["message"]


def test_the_needle_cap_is_refused(workspace, state):
    caps = ET.EditCaps(max_needle_chars=4)
    record = run(spec(("src/beta.py", "SETTING = 'old'", "SETTING = 'new'")),
                 workspace, state, caps=caps)
    assert record["verdict"] == ET.VERDICT_BLOCKED
    assert "needle cap" in record["files"][0]["message"]


def test_a_deny_glob_is_refused(workspace, state):
    (workspace / ".env").write_text("TOKEN=old\n", encoding="utf-8")
    record = run(spec((".env", "TOKEN=old", "TOKEN=new")), workspace, state)
    assert record["verdict"] == ET.VERDICT_BLOCKED
    assert ".env" in record["files"][0]["detail"]["deny_glob"]
    assert (workspace / ".env").read_text(encoding="utf-8") == "TOKEN=old\n"


def test_a_missing_workspace_is_blocked(tmp_path):
    spec_dict = spec(("src/beta.py", "SETTING = 'old'", "SETTING = 'new'"))
    record = run(spec_dict, tmp_path / "does-not-exist", tmp_path / "state")
    assert record["verdict"] == ET.VERDICT_BLOCKED
    assert "does not exist" in record["blocked"]["message"]
    assert ET.exit_code_for(record) == 3


# --------------------------------------------------------------------------- #
# 21–24: undo                                                                   #
# --------------------------------------------------------------------------- #


def test_undo_restores_byte_identical_content(workspace, state):
    paths = ["src/alpha.py", "src/beta.py", "notes.md"]
    before = {path: digest(workspace / path) for path in paths}
    record = run(
        spec(
            ("src/alpha.py", "    return value + 1", "    return value + 2"),
            ("src/beta.py", "OTHER = 2", "OTHER = 3"),
            ("notes.md", "alpha holds the first value", "alpha holds the last value"),
        ),
        workspace,
        state,
    )
    undone = ET.undo_transaction(record["tx_id"], state_dir=state)
    assert undone["verdict"] == ET.VERDICT_UNDONE
    assert ET.exit_code_for(undone) == 0
    assert undone["undo"]["complete"] is True
    assert [item["byte_identical"] for item in undone["undo"]["restored"]] == [True, True, True]
    assert {path: digest(workspace / path) for path in paths} == before, (
        "the restore must be byte-identical, not 'close enough'"
    )
    assert undone["in_tree"] is False


def test_undo_refuses_when_a_touched_file_moved_then_force_restores(workspace, state):
    record = run(spec(("src/beta.py", "OTHER = 2", "OTHER = 3")), workspace, state)
    target = workspace / "src" / "beta.py"
    target.write_text(target.read_text(encoding="utf-8") + "LATER = 9\n", encoding="utf-8")
    newer = digest(target)

    refused = ET.undo_transaction(record["tx_id"], state_dir=state)
    assert refused["undo_error"]["reason"] == ET.REASON_MODIFIED_SINCE_APPLY
    assert ET.exit_code_for(refused) == 6
    assert digest(target) == newer, "a refused undo must not discard newer work"

    forced = ET.undo_transaction(record["tx_id"], state_dir=state, force=True)
    assert forced["verdict"] == ET.VERDICT_UNDONE
    assert forced["undo"]["forced"] is True
    assert forced["undo"]["drift"][0]["path"] == "src/beta.py"
    assert "LATER = 9" not in target.read_text(encoding="utf-8")


def test_a_second_undo_is_refused_unless_forced(workspace, state):
    record = run(spec(("src/beta.py", "OTHER = 2", "OTHER = 3")), workspace, state)
    ET.undo_transaction(record["tx_id"], state_dir=state)
    again = ET.undo_transaction(record["tx_id"], state_dir=state)
    assert again["undo_error"]["reason"] == ET.REASON_ALREADY_UNDONE
    assert ET.exit_code_for(again) == 6
    forced = ET.undo_transaction(record["tx_id"], state_dir=state, force=True)
    assert forced["verdict"] == ET.VERDICT_UNDONE


def test_undo_of_an_unknown_or_never_applied_transaction(workspace, state):
    missing = ET.undo_transaction("20260101-000000-nope", state_dir=state)
    assert missing["reason"] == ET.REASON_NOT_FOUND
    assert ET.exit_code_for(missing) == 5
    refused = run(spec(("src/beta.py", "NOWHERE", "x")), workspace, state)
    assert refused["verdict"] == ET.VERDICT_NOT_APPLICABLE
    assert state.exists() is False


def test_a_deleted_file_is_recreated_by_force(workspace, state):
    record = run(spec(("src/beta.py", "OTHER = 2", "OTHER = 3")), workspace, state)
    (workspace / "src" / "beta.py").unlink()
    refused = ET.undo_transaction(record["tx_id"], state_dir=state)
    assert refused["undo_error"]["reason"] == ET.REASON_MODIFIED_SINCE_APPLY
    forced = ET.undo_transaction(record["tx_id"], state_dir=state, force=True)
    assert forced["verdict"] == ET.VERDICT_UNDONE
    restored = (workspace / "src" / "beta.py").read_text(encoding="utf-8")
    assert restored == "SETTING = 'old'\nOTHER = 2\n", "the deleted file is recreated exactly"


# --------------------------------------------------------------------------- #
# 25–29: the declared verification                                              #
# --------------------------------------------------------------------------- #


def test_a_failing_command_rolls_the_transaction_back(workspace, state):
    before = digest(workspace / "src" / "beta.py")
    record = run(
        spec(("src/beta.py", "OTHER = 2", "OTHER = 3"),
             verification={"kind": "command",
                           "argv": [sys.executable or "python", "-c", "raise SystemExit(3)"]}),
        workspace,
        state,
    )
    assert record["verdict"] == ET.VERDICT_ROLLED_BACK
    assert ET.exit_code_for(record) == 2
    assert record["verification"]["executed"] is True
    assert record["verification"]["exit_code"] == 3
    assert record["verification"]["passed"] is False
    assert record["rollback"]["complete"] is True
    assert record["in_tree"] is False
    assert digest(workspace / "src" / "beta.py") == before


def test_keep_on_failure_keeps_but_never_claims_verified(workspace, state):
    record = run(
        spec(("src/beta.py", "OTHER = 2", "OTHER = 3"),
             verification={"kind": "command", "argv": [sys.executable or "python", "-c",
                                                       "raise SystemExit(1)"]}),
        workspace,
        state,
        keep_on_failure=True,
    )
    assert record["verdict"] == ET.VERDICT_APPLIED_UNVERIFIED
    assert record["verified"] is False
    assert record["in_tree"] is True
    assert any("--keep-on-failure" in text for text in record["warnings"])
    assert "OTHER = 3" in (workspace / "src" / "beta.py").read_text(encoding="utf-8")


def test_a_command_that_does_not_exist_is_not_a_pass(workspace, state):
    record = run(
        spec(("src/beta.py", "OTHER = 2", "OTHER = 3"),
             verification={"kind": "command", "argv": ["definitely-not-a-binary-xyz"]}),
        workspace,
        state,
    )
    assert record["verification"]["executed"] is False
    assert record["verification"]["passed"] is None
    assert record["verdict"] == ET.VERDICT_APPLIED_UNVERIFIED
    assert record["verified"] is None


def test_the_test_target_kind_runs_the_declared_targets(workspace, state):
    (workspace / "tests").mkdir()
    (workspace / "tests" / "test_ok.py").write_text(
        "def test_passes():\n    assert 1 + 1 == 2\n", encoding="utf-8"
    )
    record = run(
        spec(("src/beta.py", "OTHER = 2", "OTHER = 3"),
             verification={"kind": "tests", "targets": ["tests/test_ok.py"]}),
        workspace,
        state,
    )
    assert record["verdict"] == ET.VERDICT_APPLIED
    verification = record["verification"]
    assert verification["kind"] == "tests"
    assert verification["argv"][1:4] == ["-m", "pytest", "-q"]
    assert verification["targets"] == ["tests/test_ok.py"]
    assert verification["passed"] is True
    assert "passed" in verification["stdout_tail"] or verification["exit_code"] == 0


def test_no_check_declared_means_syntax_and_an_honest_note(workspace, state):
    record = run(spec(("src/beta.py", "OTHER = 2", "OTHER = 3")), workspace, state)
    assert record["verification"]["kind"] == "syntax"
    assert record["verification"]["declared"] is False
    assert record["verification"]["executed"] is True


def test_a_syntax_error_introduced_by_the_edit_fails_the_check(workspace, state):
    record = run(spec(("src/beta.py", "OTHER = 2", "OTHER = (")), workspace, state)
    assert record["verdict"] == ET.VERDICT_ROLLED_BACK
    assert ET.exit_code_for(record) == 2
    assert record["verification"]["failures"]
    assert "SyntaxError" in record["verification"]["failures"][0]["error"]
    assert (workspace / "src" / "beta.py").read_text(encoding="utf-8") == (
        "SETTING = 'old'\nOTHER = 2\n"
    )


def test_a_changed_file_with_no_declared_parser_is_skipped_not_passed(workspace, state):
    record = run(spec(("notes.md", "alpha holds the first value", "the last value")),
                 workspace, state)
    assert record["verdict"] == ET.VERDICT_APPLIED_UNVERIFIED
    assert record["verification"]["executed"] is False
    assert record["verification"]["passed"] is None
    assert record["verification"]["skipped"] == ["notes.md"]


# --------------------------------------------------------------------------- #
# 30: the tree moved under the plan                                             #
# --------------------------------------------------------------------------- #


def test_a_file_that_moved_after_the_plan_is_refused_and_nothing_is_published(
    workspace, state, monkeypatch
):
    original_stage = ET._stage_file
    target = workspace / "src" / "beta.py"

    def stage_and_meddle(entry, root, stage_root, index, caps, **kwargs):
        result = original_stage(entry, root, stage_root, index, caps, **kwargs)
        if entry["path"] == "src/beta.py":
            target.write_text("# someone else wrote here\n", encoding="utf-8")
        return result

    monkeypatch.setattr(ET, "_stage_file", stage_and_meddle)
    record = run(
        spec(("src/beta.py", "OTHER = 2", "OTHER = 3"),
             ("src/alpha.py", "    return value + 1", "    return value + 2")),
        workspace,
        state,
    )
    assert reason_of(record, "src/beta.py") == ET.REASON_CHANGED_SINCE_PLAN
    assert ET.exit_code_for(record) == 6
    assert record["applied"] is False
    text = target.read_text(encoding="utf-8")
    assert text == "# someone else wrote here\n", "the foreign write must survive"
    assert "return value + 2" not in (workspace / "src" / "alpha.py").read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# 31–35: documents, caps, housekeeping                                          #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "bad, fragment",
    [
        ({"files": []}, "non-empty 'files'"),
        ({"files": [{"path": "", "edits": [{"old": "a", "new": "b"}]}]}, "has no 'path'"),
        ({"files": [{"path": "a.py"}]}, "needs a non-empty 'edits' list"),
        ({"files": [{"path": "a.py", "edits": [{"new": "b"}]}]}, "needs string 'old' and 'new'"),
        ({"files": [{"path": "a.py", "edits": [{"old": "", "new": "b"}]}]}, "empty 'old'"),
        ({"files": [{"path": "a.py", "edits": [
            {"old": "a", "new": "b", "expect_occurrences": 0}]}]}, "integer >= 1"),
        ({"files": [{"path": "a.py", "edits": [
            {"old": "a", "new": "b", "expect_occurrences": 2}]}]}, "replace_all: true"),
        ({"files": [{"path": "a.py", "edits": [
            {"old": "a", "new": "b", "fuzzy_min_ratio": "high"}]}]}, "not a number"),
        ({"files": [{"path": "a.py", "edits": [{"old": "a", "new": "b"}]}],
          "verification": {"kind": "telepathy"}}, "verification.kind"),
        ({"files": [{"path": "a.py", "edits": [{"old": "a", "new": "b"}]}],
          "verification": {"kind": "command"}}, "needs 'argv' or 'command'"),
        ({"files": [{"path": "a.py", "edits": [{"old": "a", "new": "b"}]}],
          "verification": {"kind": "command", "command": "sh -c 'rm -rf /'"}}, "shell"),
        ({"files": [{"path": "a.py", "edits": [{"old": "a", "new": "b"}]}],
          "verification": {"kind": "tests", "targets": []}}, "non-empty 'targets'"),
    ],
)
def test_parse_spec_validates_documents(bad, fragment):
    parsed, problem = ET.parse_spec(bad)
    assert parsed is None
    assert fragment in problem, problem


def test_parse_spec_defaults_are_strict_and_syntax():
    parsed, problem = ET.parse_spec(
        {"files": [{"path": "a.py", "edits": [{"old": "a", "new": "b"}]}]}
    )
    assert parsed is not None, problem
    assert parsed["strict"] is True
    assert parsed["verification"]["kind"] == "syntax"
    edit = parsed["files"][0]["edits"][0]
    assert edit == {
        "old": "a", "new": "b", "expect_occurrences": 1, "expect_declared": False,
        "replace_all": False, "fuzzy": True, "fuzzy_min_ratio": 0.8,
    }


def test_environment_overrides_reach_the_caps(monkeypatch):
    monkeypatch.setenv("SKALDIR_EDIT_MAX_FILE_BYTES", "4096")
    monkeypatch.setenv("SKALDIR_EDIT_MAX_FILES", "3")
    monkeypatch.setenv("SKALDIR_EDIT_VERIFY_TIMEOUT_S", "7.5")
    caps = ET.EditCaps.from_env()
    assert caps.max_file_bytes == 4096
    assert caps.max_files == 3
    assert caps.verify_timeout_s == 7.5
    monkeypatch.setenv("GODSV_EDIT_MAX_EDITS", "11")  # the legacy spelling is accepted
    monkeypatch.delenv("SKALDIR_EDIT_MAX_EDITS", raising=False)
    assert ET.EditCaps.from_env().max_edits == 11
    monkeypatch.setenv("SKALDIR_EDIT_MAX_EDITS", "not-a-number")
    assert ET.EditCaps.from_env().max_edits == ET.EditCaps.max_edits


def test_a_cap_override_is_enforced_by_the_transaction(workspace, state, monkeypatch):
    monkeypatch.setenv("SKALDIR_EDIT_MAX_FILE_BYTES", "16")
    record = run(spec(("src/beta.py", "OTHER = 2", "OTHER = 3")), workspace, state)
    assert record["verdict"] == ET.VERDICT_BLOCKED
    assert record["files"][0]["detail"]["cap"] == 16


def test_prune_keeps_every_transaction_whose_undo_is_still_possible(workspace, state):
    caps = ET.EditCaps(keep_transactions=1)
    ids = []
    for name in ("one.py", "two.py", "three.py"):
        (workspace / name).write_text("value = 1\n", encoding="utf-8")
        record = run(spec((name, "value = 1", "value = 2")), workspace, state, caps=caps)
        assert record["verdict"] == ET.VERDICT_APPLIED, record
        ids.append(record["tx_id"])
    report = ET.prune(state, caps=caps)
    assert report["removed"] == [], "an applied transaction is never pruned"
    assert report["kept"] == 3

    assert ET.undo_transaction(ids[0], state_dir=state)["verdict"] == ET.VERDICT_UNDONE
    report = ET.prune(state, caps=caps)
    assert report["removed"] == [ids[0]], "an undone record over the cap is expendable"
    assert (state / f"{ids[1]}.json").is_file(), "an applied record is not"
    assert (state / f"{ids[2]}.json").is_file()
    assert ET.undo_transaction(ids[1], state_dir=state)["verdict"] == ET.VERDICT_UNDONE
    report = ET.prune(state, caps=caps)
    assert report["removed"] == [ids[1]]
    assert (state / f"{ids[2]}.json").is_file(), "the newest record stays"


def test_list_show_and_resolve_talk_about_the_same_records(workspace, state):
    first = run(spec(("src/beta.py", "OTHER = 2", "OTHER = 3")), workspace, state)
    second = run(spec(("src/beta.py", "OTHER = 3", "OTHER = 4")), workspace, state)
    listed = ET.list_transactions(state)
    assert [item["tx_id"] for item in listed] == [second["tx_id"], first["tx_id"]]
    assert ET.resolve_tx_id(state, "last") == second["tx_id"]
    assert ET.resolve_tx_id(state, first["tx_id"]) == first["tx_id"]
    assert ET.resolve_tx_id(state, "") == second["tx_id"]
    loaded = ET.load_transaction(state, first["tx_id"])
    assert loaded is not None and loaded["tx_id"] == first["tx_id"]
    assert ET.load_transaction(state, "nope") is None
    assert ET.list_transactions(state / "elsewhere") == []


def test_the_text_renderer_says_what_happened(workspace, state):
    record = run(spec(("src/beta.py", "OTHER = 2", "OTHER = 3")), workspace, state)
    text = ET.render_text(record)
    assert record["tx_id"] in text and "applied" in text
    assert "ok src/beta.py" in text
    assert "verification: syntax executed=True passed=True" in text
    refused = run(spec(("src/beta.py", "NOWHERE", "x")), workspace, state, dry_run=True)
    assert "not_applicable" in ET.render_text(refused)


def test_near_duplicate_regions_finds_the_twin_lines():
    text = "def a():\n    value = 1\n\ndef b():\n        value = 1\n"
    assert ET.near_duplicate_regions(text, "    value = 1") == [2, 5]
    assert ET.near_duplicate_regions(text, "    value = 1", indent=False) == [2]


def test_the_ladder_is_the_sibling_modules_not_a_second_copy():
    """The matching ladder and the path policy live in tools_core — this layer runs them."""
    assert ET._patch_tool is not None and ET._sandbox is not None
    assert ET._patch_tool.__name__ == "skaldir.tools_core.patch_tool"
    assert ET._sandbox.__name__ == "skaldir.tools_core.sandbox"
