"""``skaldir.codectx`` — the coding-context harness, measured on a real workspace.

The properties under test are the ones the verb claims:

* the repo map is **ranked** and **budgeted** (a cut says how much was left out),
* the update strategy is **mtime/inode based**: a ``touch`` re-reads one file and leaves the
  fact fingerprint where it was, a real edit moves it, and a second call re-reads nothing,
* ``.gitignore`` is respected (and can be switched off),
* a search that finds nothing is distinguishable from a search that never looked
  (``files_scanned``), and a bad regex is a usage error, not an empty result,
* symbols come from ``ast`` for Python; where no index exists the answer is ``blocked`` and
  **names the tool** — never a guess,
* diagnostics are real (ruff) or honestly blocked, and
* the context pack is bounded, ranked, byte-stable and consumable as a prompt block.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time

import pytest

from skaldir import codectx as C
from skaldir.tools_core.contract import ToolContext, validate_schema

# --------------------------------------------------------------------- fixtures


def _write(path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def make_workspace(root):
    """A small, realistic workspace: source, tests, docs, config, ignores, a git repo."""
    _write(
        root / "src" / "app" / "main.py",
        "import os\n\n\nCONFIG_PATH = 'x'\n\n\nclass Runner:\n"
        "    def start(self):\n        return 1\n\n\n"
        "def entry_point():\n    return Runner()\n",
    )
    _write(
        root / "src" / "app" / "util.py",
        "def helper(value):\n    return value + 1\n",
    )
    _write(
        root / "src" / "app" / "widget.py",
        "def render_widget():\n    return 'widget'\n",
    )
    _write(root / "tests" / "test_app.py", "def test_ok():\n    assert True\n")
    _write(root / "AGENTS.md", "# Rules\n\nRead this first.\n")
    _write(root / "README.md", "# Fixture project\n\nThe widget renderer.\n")
    _write(root / "config" / "settings.toml", "[app]\nname = 'fixture'\n")
    _write(root / "src" / "app" / "flow.ts", "export function flow(): number {\n  return 1;\n}\n")
    _write(root / "src" / "app" / "engine.rs", "pub fn engine() -> u8 { 1 }\n")
    _write(root / "build" / "artifact.py", "def generated():\n    return 0\n")
    _write(root / "logs" / "app.log", "widget render failed\n")
    _write(root / "logs" / "keep.log", "widget render kept\n")
    _write(root / "vendor" / "vendored.py", "def vendored():\n    return 1\n")
    _write(root / "drop.tmp", "scratch widget\n")
    _write(root / "keep.tmp", "kept widget\n")
    _write(root / ".gitignore", "build/\nlogs/\n!logs/keep.log\nvendor/\n*.tmp\n!keep.tmp\n")
    _write(root / "nested" / ".gitignore", "secret.py\n")
    _write(root / "nested" / "secret.py", "TOKEN = 'nope'\n")
    _write(root / "nested" / "public.py", "def public():\n    return 2\n")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=root, check=True)
    return root


@pytest.fixture
def workspace(tmp_path):
    return make_workspace(tmp_path / "ws")


@pytest.fixture
def state(tmp_path):
    directory = tmp_path / "state"
    directory.mkdir()
    return directory


# ----------------------------------------------------------------- repo map


def test_the_map_is_ranked_and_every_entry_carries_its_reasons(workspace, state):
    payload = C.repo_map(workspace, token_budget=500, state_dir=state, include_changed=False)
    assert payload["files_indexed"] > 8
    scores = [row["score"] for row in payload["entries"]]
    assert scores == sorted(scores, reverse=True), "the map must be ranked, highest first"
    by_path = {row["path"]: row for row in payload["entries"]}
    assert by_path["AGENTS.md"]["kind"] == "doc"
    assert by_path["src/app/main.py"]["language"] == "python"
    assert by_path["src/app/main.py"]["symbols"] >= 2  # Runner + entry_point
    assert by_path["AGENTS.md"]["reasons"], "a rank without a reason is not reviewable"


def test_the_map_carries_its_token_budget_and_names_what_it_dropped(workspace, state):
    wide = C.repo_map(workspace, token_budget=4000, state_dir=state)
    tight = C.repo_map(workspace, token_budget=120, state_dir=state)
    assert wide["map"]["truncated"] is False
    assert tight["map"]["truncated"] is True
    assert tight["map"]["dropped"] > 0
    # the entry lines are held to the budget; the truncation marker is not charged against
    # it (the same rule project_context's block follows), so the rendering may exceed it by
    # exactly one marker line
    assert tight["map"]["tokens"] <= 130 + 40
    assert "truncated" in tight["map"]["text"]
    assert tight["token_estimate"] == C.TOKEN_ESTIMATE == "chars/4"


def test_gitignore_is_respected_and_can_be_switched_off(workspace, state):
    payload = C.repo_map(workspace, state_dir=state, include_changed=False)
    paths = {row["path"] for row in payload["entries"]}
    assert "build/artifact.py" not in paths
    assert "vendor/vendored.py" not in paths
    assert "logs/app.log" not in paths
    assert "drop.tmp" not in paths, "a '*.tmp' pattern must exclude it"
    assert "keep.tmp" in paths, "a '!keep.tmp' negation must re-include it"
    assert "logs/keep.log" not in paths, (
        "git's own rule: a file inside an excluded directory cannot be re-included "
        "(logs/ excludes the directory, so !logs/keep.log cannot win)"
    )
    assert "nested/secret.py" not in paths, "a nested .gitignore must be read too"
    assert "nested/public.py" in paths
    assert payload["ignore"]["patterns"] >= 5
    assert payload["ignore"]["sources"]

    ignored_off = C.repo_map(workspace, state_dir=state, use_gitignore=False)
    off_paths = {row["path"] for row in ignored_off["entries"]}
    assert "vendor/vendored.py" in off_paths, "--no-ignore must turn the .gitignore rules off"
    assert "drop.tmp" in off_paths
    assert (
        "build/artifact.py" not in off_paths
    ), "build/ is a build directory, not a gitignore rule: the skip list has its own reason"
    assert ignored_off["ignore"]["patterns"] == 0


def test_a_touch_re_reads_one_file_and_leaves_the_map_fingerprint_alone(workspace, state):
    first = C.refresh_map(workspace, state_dir=state)
    assert first["mode"] == "rebuilt"
    assert first["stats"]["reopened"] == first["stats"]["reused"] + first["stats"]["reopened"]

    again = C.refresh_map(workspace, state_dir=state)
    assert again["mode"] == "reused"
    assert again["stats"]["reopened"] == 0, "an unchanged tree must not be re-read"
    assert again["map_fingerprint"] == first["map_fingerprint"]

    target = workspace / "src" / "app" / "util.py"
    os.utime(target, (time.time() + 5, time.time() + 5))
    touched = C.refresh_map(workspace, state_dir=state)
    assert touched["stats"]["reopened"] == 1, "exactly the touched file is re-read"
    assert touched["stats"]["reused"] == len(first["entries"]) - 1
    assert touched["map_fingerprint"] == first["map_fingerprint"], (
        "a touch changed no fact, so the fact fingerprint must not move"
    )


def test_a_real_edit_moves_the_map_fingerprint(workspace, state):
    before = C.refresh_map(workspace, state_dir=state)
    target = workspace / "src" / "app" / "util.py"
    target.write_text("def helper(value):\n    return value + 2\n", encoding="utf-8")
    after = C.refresh_map(workspace, state_dir=state)
    assert after["mode"] == "incremental"
    assert after["stats"]["reopened"] == 1
    assert after["map_fingerprint"] != before["map_fingerprint"]


def test_a_new_file_and_a_deleted_file_are_both_counted(workspace, state):
    before = C.refresh_map(workspace, state_dir=state)
    _write(workspace / "src" / "app" / "brand_new.py", "def brand_new():\n    return 3\n")
    added = C.refresh_map(workspace, state_dir=state)
    assert added["stats"]["added"] == 1
    assert "src/app/brand_new.py" in added["entries"]
    (workspace / "src" / "app" / "brand_new.py").unlink()
    removed = C.refresh_map(workspace, state_dir=state)
    assert removed["stats"]["removed"] == 1
    assert removed["map_fingerprint"] == before["map_fingerprint"]


def test_the_directory_mtime_shortcut_is_opt_in_and_can_serve_a_stale_fact(workspace, state):
    """The documented footgun: a dir mtime does not move on an in-place edit."""
    C.refresh_map(workspace, state_dir=state)
    target = workspace / "src" / "app" / "util.py"
    target.write_text("def helper(value):\n    return value + 99\n", encoding="utf-8")
    stale = C.refresh_map(workspace, state_dir=state, trust_dir_mtime=True)
    assert stale["mode"] == "reused"
    assert stale["validation"].startswith("directory mtime")
    fresh = C.refresh_map(workspace, state_dir=state)
    assert fresh["stats"]["reopened"] == 1
    assert fresh["map_fingerprint"] != stale["map_fingerprint"], (
        "the stat pass is the default for exactly this reason"
    )


def test_the_map_writes_only_its_cache(workspace, state):
    def stamps() -> set:
        # .git/ is excluded: `git status` legitimately refreshes the index, and that is the
        # repository's own bookkeeping, not a write by this harness
        return {
            path.stat().st_mtime_ns
            for path in workspace.rglob("*")
            if path.is_file() and ".git" not in path.parts
        }

    before = stamps()
    C.repo_map(workspace, state_dir=state, force=True)
    assert stamps() == before, "indexing must not touch a workspace file"
    assert list(state.glob("map-*.json")), "the cache lands in the state dir"


# -------------------------------------------------------------------- search


def test_a_search_hit_and_a_search_miss_are_distinguishable(workspace):
    hit = C.search(workspace, r"def render_widget")
    assert hit["status"] == "ok"
    assert hit["count"] >= 1
    assert hit["files_scanned"] > 0
    assert {row["path"] for row in hit["matches"]} == {"src/app/widget.py"}

    miss = C.search(workspace, r"def no_such_function_anywhere")
    assert miss["status"] == "ok"
    assert miss["count"] == 0
    assert miss["total_matches"] == 0
    assert miss["files_scanned"] > 0, "'nothing found' must not look like 'never looked'"


def test_search_caps_context_and_ignore_rules(workspace):
    capped = C.search(workspace, r"def ", limit=2, output_mode="files_only")
    assert capped["count"] == 2
    assert capped["truncated"] is True
    assert capped["total_matches"] > 2

    with_context = C.search(workspace, r"return value", context=1)
    assert with_context["matches"][0]["context"], "context lines were asked for"
    assert any(line.startswith("1|") for line in with_context["matches"][0]["context"]), (
        "the line above the match must be in the context"
    )

    ignored = C.search(workspace, r"vendored")
    assert ignored["count"] == 0, "vendor/ is excluded by the .gitignore file"
    assert ignored["ignored_files"] >= 1
    assert "build" in ignored["skipped_dirs"], (
        "the build/cache directory list is reported, so an empty result stays explainable"
    )
    unignored = C.search(workspace, r"vendored", use_gitignore=False)
    assert unignored["count"] == 1
    assert {row["path"] for row in unignored["matches"]} == {"vendor/vendored.py"}


def test_an_invalid_regex_is_a_usage_error_not_an_empty_result(workspace):
    payload = C.search(workspace, r"def (")
    assert payload["status"] == "usage"
    assert payload["error_kind"] == "usage"
    assert payload["count"] == 0


def test_a_path_outside_the_workspace_is_refused(workspace):
    payload = C.search(workspace, r"def ", subpath="../..")
    assert payload["status"] == "blocked"
    assert payload["error_kind"] == "blocked"


def test_a_file_glob_search_finds_names(workspace):
    payload = C.search(workspace, "*.py", target="files", file_glob="")
    assert payload["status"] == "ok"
    assert payload["count"] >= 3
    assert all(row["path"].endswith(".py") for row in payload["matches"])


# ------------------------------------------------------------------- symbols


def test_python_symbols_come_from_the_standard_library(workspace):
    payload = C.symbols(workspace, "src/app/main.py", limit=50)
    assert payload["status"] == "ok"
    names = {item["name"] for row in payload["files"] for item in row["symbols"]}
    assert {"Runner", "start", "entry_point"} <= names
    assert payload["files"][0]["tool"] == "python-ast"
    assert payload["symbols_total"] >= 3
    kinds = {item["kind"] for row in payload["files"] for item in row["symbols"]}
    assert "class" in kinds and "function" in kinds


def test_a_language_without_an_index_is_blocked_and_names_the_tool(workspace, monkeypatch):
    monkeypatch.setattr(C, "_ctags_available", lambda: "")
    payload = C.symbols(workspace, "src/app/flow.ts", use_ctags=True)
    assert payload["status"] == "blocked"
    assert payload["error_kind"] == "blocked"
    assert payload["files"] == []
    assert payload["needed"], "a blocked answer must name what is missing"
    assert any("ctags" in item or "language server" in item for item in payload["needed"])
    assert payload["unsupported"][0]["path"] == "src/app/flow.ts"
    assert payload["unsupported"][0]["needed"]


def test_symbols_stay_silent_about_a_file_that_is_not_code(workspace):
    payload = C.symbols(workspace, "AGENTS.md")
    assert payload["status"] == "ok"
    assert payload["files"] == []
    assert payload["unsupported"] == [], "markdown is out of scope, not unsupported"


# --------------------------------------------------------------- diagnostics


def test_diagnostics_report_a_blocked_answer_when_no_linter_is_reachable(workspace, monkeypatch):
    monkeypatch.setattr(
        C,
        "probe_linter",
        lambda name="ruff": {
            "name": name,
            "available": False,
            "argv": [],
            "reason": "ruff is neither on PATH nor importable as a module",
            "needed": ["ruff (pip install ruff, or the project's `dev` extra: ruff>=0.6)"],
        },
    )
    payload = C.diagnostics(workspace, ("src/app/main.py",))
    assert payload["status"] == "blocked"
    assert payload["error_kind"] == "blocked"
    assert payload["findings"] == []
    assert any("ruff" in item for item in payload["needed"])
    assert payload["files_checked"] == 0


def test_diagnostics_name_the_language_server_for_a_language_nothing_drives(workspace):
    probe = C.probe_linter("ruff")
    if not probe["available"]:
        pytest.skip("ruff is not installed here, so the python half cannot be driven")
    payload = C.diagnostics(workspace, ("src/app/engine.rs",))
    assert payload["status"] == "blocked"
    assert payload["unsupported"], "the unsupported language must be reported"
    assert any("rust-analyzer" in item for item in payload["needed"])


def test_diagnostics_return_typed_findings_when_ruff_is_there(workspace):
    probe = C.probe_linter("ruff")
    if not probe["available"]:
        pytest.skip("ruff is not installed here")
    _write(
        workspace / "src" / "app" / "broken.py",
        "import os\n\n\ndef f():\n    return undefined_name\n",
    )
    payload = C.diagnostics(workspace, ("src/app/broken.py",))
    assert payload["status"] == "ok"
    assert payload["files_checked"] == 1
    assert payload["findings"], "an undefined name is a real finding"
    first = payload["findings"][0]
    assert first["path"].endswith("broken.py") and first["line"] > 0 and first["code"]
    assert payload["counts_by_code"]


def test_the_linter_probe_is_honest_about_the_argv_it_would_run(workspace):
    probe = C.probe_linter("ruff")
    assert set(probe) >= {"name", "available", "argv"}
    if probe["available"]:
        assert probe["argv"] and probe["version"].startswith("ruff")
    else:
        assert probe["needed"]


# ------------------------------------------------------------------------ git


def test_changed_files_and_blame_answer_from_the_real_repository(workspace):
    changed = C.git_changed(workspace)
    assert changed["status"] == "ok"
    assert changed["branch"] in {"master", "main"}
    assert changed["count"] == 0

    _write(workspace / "src" / "app" / "util.py", "def helper(value):\n    return value + 3\n")
    dirty = C.git_changed(workspace)
    assert {row["path"] for row in dirty["files"]} == {"src/app/util.py"}
    assert dirty["files"][0]["code"] == "M"

    _write(workspace / "src" / "app" / "fresh.py", "def fresh():\n    return 4\n")
    untracked = C.git_changed(workspace)
    assert any(row["path"] == "src/app/fresh.py" for row in untracked["files"])

    blamed = C.git_blame(workspace, "src/app/main.py", 1)
    assert blamed["status"] == "ok"
    assert blamed["line"] == 1
    assert blamed["author"] == "Test"
    assert blamed["text"] == "import os"


def test_git_outside_a_work_tree_is_unavailable_not_a_refusal(tmp_path):
    outside = tmp_path / "plain"
    outside.mkdir()
    payload = C.git_changed(outside)
    assert payload["status"] == "unavailable"
    assert payload["files"] == []
    assert "work tree" in payload["reason"]


def test_blame_of_a_path_outside_the_workspace_is_blocked(workspace):
    payload = C.git_blame(workspace, "../../etc/passwd", 1)
    assert payload["status"] == "blocked"
    assert "escapes" in payload["reason"] or "outside" in payload["reason"]


# ---------------------------------------------------------------- context pack


def test_the_pack_is_bounded_ranked_and_byte_stable(workspace, state):
    first = C.context_pack(workspace, "widget renderer", token_budget=600, state_dir=state)
    second = C.context_pack(workspace, "widget renderer", token_budget=600, state_dir=state)
    assert first["fingerprint"] == second["fingerprint"], "the pack must be byte-stable"
    assert C.pack_fingerprint(first) == first["fingerprint"]
    assert C.to_prompt_block(first) == first["block"]["text"]
    assert (
        hashlib.sha256(first["block"]["text"].encode("utf-8")).hexdigest() == first["fingerprint"]
    )
    assert first["block"]["chars"] <= first["budget"]["chars"] + 400, "the marker is the slack"
    names = [section["name"] for section in first["sections"]]
    assert names[:3] == ["task", "repo_map", "changed"], names
    assert "widget" in first["block"]["text"].lower()
    assert all(row["path"] for row in first["ranking"])
    scores = [row["score"] for row in first["ranking"]]
    assert scores == sorted(scores, reverse=True)


def test_the_pack_selects_the_file_the_task_is_about(workspace, state):
    payload = C.context_pack(workspace, "render_widget widget", token_budget=800, state_dir=state)
    assert "src/app/widget.py" in payload["selected"]
    top = payload["ranking"][0]
    assert top["path"] == "src/app/widget.py"
    assert top["content_hits"] >= 1
    assert any("content hit" in reason for reason in top["reasons"])
    assert "src/app/widget.py" in payload["block"]["text"]


def test_the_pack_reports_what_it_left_out(workspace, state):
    payload = C.context_pack(
        workspace, "widget helper runner config", token_budget=300, file_limit=1, state_dir=state
    )
    assert payload["dropped_count"] >= 1
    assert payload["dropped"], "a bounded pack must say what did not fit"
    assert len(payload["selected"]) == 1


def test_the_pack_never_fails_because_a_tool_is_missing(workspace, state, monkeypatch):
    monkeypatch.setattr(
        C,
        "probe_linter",
        lambda name="ruff": {"name": name, "available": False, "argv": [], "needed": ["ruff"]},
    )
    payload = C.context_pack(workspace, "widget", token_budget=600, state_dir=state)
    assert payload["diagnostics_status"] in {"blocked", "ok"}
    assert "diagnostics" in payload["block"]["text"]


# --------------------------------------------------------- contracts / schemas


def test_every_verb_declares_schemas_permissions_timeouts_and_truncation():
    assert set(C.CONTRACTS) == {f"code_{name}" for name in C.VERB_NAMES}
    for name, contract in C.CONTRACTS.items():
        assert contract.timeout_s > 0
        assert contract.truncation.max_bytes > 0
        assert validate_schema(contract.input_schema, where=f"{name}.input") == []
        assert validate_schema(contract.output_schema, where=f"{name}.output") == []
        assert contract.permission.filesystem.mode == "read"
        assert contract.permission.filesystem.scope == "workspace"
        assert contract.permission.network.mode == "none"
        assert contract.module == "skaldir.codectx"
        assert contract.registry_tags()


def test_the_contracts_refuse_an_unknown_argument(workspace):
    payload = C.run_verb("map", {"nonsense": 1}, ToolContext(root=workspace))
    assert payload.ok is False
    assert payload.error_kind == "usage"
    assert "unknown argument: nonsense" in payload.error


def test_run_verb_drives_a_verb_through_its_contract(workspace, tmp_path):
    payload = C.run_verb(
        "search",
        {"pattern": "def helper", "limit": 3},
        ToolContext(root=workspace, runtime=tmp_path / "rt"),
    )
    assert payload.ok is True
    assert payload.exit_code == 0
    assert payload.data["count"] >= 1
    assert payload.evidence


def test_an_unknown_verb_is_not_found(workspace):
    payload = C.run_verb("nope", {}, ToolContext(root=workspace))
    assert payload.error_kind == "not_found"


def test_the_surface_report_lists_every_verb_and_probes_the_host():
    report = C.surface_report()
    assert len(report["verbs"]) == len(C.VERB_NAMES)
    assert {row["verb"] for row in report["verbs"]} == set(C.VERB_NAMES)
    assert set(report["tools_available"]) >= {"ruff", "ctags", "git", "python-ast"}
    assert report["limits"]["token_estimate"] == "chars/4"


def test_the_json_payloads_are_serialisable(workspace, state):
    for payload in (
        C.repo_map(workspace, state_dir=state),
        C.search(workspace, "def "),
        C.symbols(workspace, "src/app/main.py"),
        C.git_changed(workspace),
        C.context_pack(workspace, "helper", state_dir=state),
    ):
        json.dumps(payload, default=str)
