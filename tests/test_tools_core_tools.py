"""Every core tool, run for real — including the failing and refusing cases.

No fixture tool and no monkeypatched implementation: the calls go through
:func:`skaldir.tools_core.registry.call` and hit the real filesystem, the real
subprocess sandbox and (for the network tools) an **injected transport**, so the
suite stays offline while the parsing, the caps and the refusals are still the
real code paths.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from skaldir.tools_core import contract as C
from skaldir.tools_core import registry, sandbox, web


@pytest.fixture()
def ctx(tmp_path):
    runtime = tmp_path / "rt"
    runtime.mkdir()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    return {"runtime": runtime, "root": workspace, "call": _caller(runtime, workspace)}


def _caller(runtime: Path, workspace: Path):
    def call(name: str, arguments: dict, **kwargs):
        return registry.call(name, arguments, runtime=runtime, root=workspace, **kwargs)

    return call


# --------------------------------------------------------------------- terminal


def test_terminal_runs_a_command_and_reports_exit_code_stdout_and_cwd(ctx):
    result = ctx["call"]("terminal", {"command": "printf 'hello\\n' && pwd"})
    assert result.ok is True
    assert result.data["exit_code"] == 0
    assert result.data["stdout"].splitlines()[0] == "hello"
    assert result.data["cwd"] == str(ctx["root"])
    assert result.data["isolation"]["mode"] == "process"
    assert result.evidence and result.evidence[0]["kind"] == "run"


def test_terminal_reports_a_non_zero_exit_as_data_not_as_a_tool_failure(ctx):
    result = ctx["call"]("terminal", {"command": "exit 3"})
    assert result.ok is True  # the observation succeeded
    assert result.data["exit_code"] == 3
    assert result.exit_code == 0  # so the CLI call did not fail


def test_terminal_exit_7_is_reported_as_7_the_way_the_contract_says(ctx):
    """VAL-HARNESS-003 names the case literally: transport ok=true must not mask exit 7."""
    result = ctx["call"]("terminal", {"command": "exit 7"})
    assert result.ok is True, "the transport succeeded — it observed a command"
    assert result.data["exit_code"] == 7
    assert result.data["timed_out"] is False
    assert result.error_kind != "timeout"
    assert result.exit_code == 0, "the CLI call itself must not fail for exit 7"


def test_terminal_reports_stdout_and_stderr_as_separate_fields(ctx):
    """VAL-HARNESS-003: stdout, stderr and the exit code travel separately."""
    result = ctx["call"](
        "terminal", {"command": "printf 'to-stdout\\n'; printf 'to-stderr\\n' >&2; exit 7"}
    )
    assert result.ok is True
    assert result.data["stdout"] == "to-stdout\n"
    assert result.data["stderr"] == "to-stderr\n", "stderr must stay its own stream"
    assert result.data["exit_code"] == 7
    assert not result.data["stdout_truncated"] and not result.data["stderr_truncated"]


def test_terminal_reports_an_unknown_command_honestly(ctx):
    result = ctx["call"]("terminal", {"command": "definitely-not-a-command-xyz"})
    assert result.data["exit_code"] == 127
    assert "not found" in result.data["stderr"]


def _timeout_call_with_pgid_marker(ctx, marker_name: str):
    """Run the terminal timeout case; return (result, spawned pgid).

    The command records its own shell PID first — under the terminal's
    ``start_new_session`` spawn the shell IS the process-group leader, so that
    PID identifies exactly the group this test spawned.
    """
    marker = ctx["root"] / marker_name
    started = time.time()
    result = ctx["call"](
        "terminal", {"command": f"echo $$ > {marker_name}; sleep 30", "timeout_s": 1}
    )
    elapsed = time.time() - started
    return result, elapsed, int(marker.read_text().strip())


def _assert_process_group_gone(pgid: int) -> None:
    """Assert the spawned process group is empty, scoped to that pgid.

    Never scan the host process table for a command name ('sleep 30'): an
    unrelated host process running the same command must not flip this verdict
    (measured gate-flake, 2026-10-02). The poll absorbs the short window in
    which killed children are still unreaped zombies; a live group after the
    deadline fails with the surviving pids listed for diagnosis.
    """
    deadline = time.time() + 5.0
    while time.time() < deadline:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return  # the group is gone
        time.sleep(0.1)
    remaining = subprocess.run(  # noqa: S603
        ["/bin/sh", "-c", f"ps -eo pid=,pgid=,cmd= | awk -v g={pgid} '$2 == g'"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    pytest.fail(f"process group {pgid} survived the timeout kill:\n{remaining}")


def test_terminal_times_out_and_kills_the_process_group(ctx):
    result, elapsed, pgid = _timeout_call_with_pgid_marker(ctx, "pgid-marker")
    assert result.ok is False and result.error_kind == "timeout"
    assert result.exit_code == C.EXIT_UNKNOWN
    assert result.data["timed_out"] is True
    assert result.data["exit_code"] == -1
    assert elapsed < 10, "the timeout must not wait for the command"
    # the group is gone: checked via the spawned group's own pgid
    _assert_process_group_gone(pgid)


def test_a_foreign_host_sleep_process_is_not_counted_as_a_survivor(ctx):
    """Regression for the 2026-10-02 gate-flake: a live UNRELATED host 'sleep 30'
    must not flip the timeout verdict. The spawned group is verified dead while
    a foreign sleep with its own group provably still runs."""
    decoy = subprocess.Popen(  # noqa: S603
        ["/bin/sleep", "30"],
        start_new_session=True,
    )
    try:
        result, _elapsed, pgid = _timeout_call_with_pgid_marker(ctx, "pgid-marker-decoy")
        assert result.ok is False and result.error_kind == "timeout"
        assert result.data["timed_out"] is True
        assert decoy.poll() is None, "the decoy died early — the cross-talk proof is void"
        try:
            os.killpg(os.getpgid(decoy.pid), 0)
        except ProcessLookupError:  # pragma: no cover — decoy vanished mid-test
            pytest.fail("the foreign sleep group vanished — the cross-talk proof is void")
        # the spawned group is dead DESPITE the foreign sleep running:
        _assert_process_group_gone(pgid)
    finally:
        decoy.kill()
        decoy.wait(timeout=5)


def test_terminal_caps_its_output_and_spills_the_rest(ctx):
    result = ctx["call"](
        "terminal", {"command": "yes abcdefghij | head -c 200000", "max_output_bytes": 4096}
    )
    data = result.data
    assert data["stdout_truncated"] is True
    assert data["stdout_bytes"] == 200000
    assert len(data["stdout"].encode()) < 20000
    assert "TRUNCATED" in data["stdout"]
    assert data["spill"] and Path(data["spill"][0]).is_file()
    assert Path(data["spill"][0]).stat().st_size == 200000


def test_terminal_refuses_a_cwd_outside_the_sandbox_root(ctx):
    result = ctx["call"]("terminal", {"command": "ls", "cwd": "/etc"})
    assert result.ok is False and result.error_kind == "blocked"
    assert result.exit_code == C.EXIT_BLOCKED
    assert "escapes the declared root" in result.error


def test_terminal_scrubs_the_child_environment(ctx, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-must-not-reach-the-child")
    monkeypatch.setenv("PYTHONPATH", "/some/other/src")
    result = ctx["call"](
        "terminal",
        {
            "command": "python3 -c \"import os;print('API_KEY' in ''.join(os.environ));"
            "print('PYTHONPATH' in os.environ);print(os.environ['SKALDIR_TOOL_WORKSPACE'])\""
        },
    )
    lines = result.data["stdout"].splitlines()
    assert lines[0] == "False"
    assert lines[1] == "False"
    assert lines[2] == str(ctx["root"])
    assert "DEEPSEEK_API_KEY" in result.data["policy"]["dropped_credentials"]
    assert "PYTHONPATH" in result.data["policy"]["dropped_by_design"]


def test_terminal_validates_its_input_before_running_anything(ctx):
    result = ctx["call"]("terminal", {"command": "echo hi", "bogus": True})
    assert result.ok is False and result.error_kind == "usage"
    assert "unknown argument: bogus" in result.error
    assert ctx["call"]("terminal", {"command": "true", "timeout_s": 99999}).ok is False


def test_terminal_clamps_a_timeout_and_an_output_cap_above_the_contract(ctx):
    result = ctx["call"]("terminal", {"command": "true", "timeout_s": 600})
    assert result.ok is True
    bigger = ctx["call"]("terminal", {"command": "true", "max_output_bytes": 1048576})
    assert bigger.ok is True
    assert any("clamped" in text for text in bigger.warnings)
    # above the schema maximum it is a usage error, not a silent truncation
    assert (
        ctx["call"]("terminal", {"command": "true", "max_output_bytes": 100000000}).error_kind
        == "usage"
    )


@pytest.mark.skipif(
    not sandbox.isolation_report()["available"],
    reason="bubblewrap is not usable in this environment",
)
def test_terminal_isolated_mode_really_confines_the_command(ctx, tmp_path):
    inside = ctx["call"](
        "terminal", {"command": "echo inside > iso.txt && cat iso.txt", "isolated": True}
    )
    assert inside.ok is True
    assert inside.data["isolation"]["mode"] == "bubblewrap"
    assert inside.data["isolation"]["unshare_net"] is True
    assert inside.data["stdout"].strip() == "inside"

    # ``tmp_path`` is NOT a usable "outside" probe: isolated mode deliberately replaces
    # /tmp with a writable throwaway tmpfs (terminal.py's contract names "a private
    # /tmp"), so a write under /tmp succeeds *inside* and reaches nothing on the host.
    # Verify that fact, then prove the refusal with a real host path outside the root.
    scratch = tmp_path / "private-tmp-probe.txt"
    ctx["call"]("terminal", {"command": f"echo x > {scratch}", "isolated": True})
    assert not scratch.exists(), "a write to the private /tmp reached the host"

    outside = Path.home() / f".skaldir-isolation-probe-{os.getpid()}"
    outside.write_text("host marker\n", encoding="utf-8")
    try:
        blocked = ctx["call"]("terminal", {"command": f"echo nope > {outside}", "isolated": True})
        assert blocked.data["exit_code"] != 0, (
            f"a write outside the workspace succeeded: {blocked.data['stdout']!r}"
        )
        assert outside.read_text(encoding="utf-8") == "host marker\n", "the host file changed"
    finally:
        outside.unlink(missing_ok=True)

    netless = ctx["call"](
        "terminal",
        {
            "command": "python3 -c \"import socket;socket.create_connection(('1.1.1.1',443),2)\"",
            "isolated": True,
            "timeout_s": 20,
        },
    )
    assert netless.data["exit_code"] != 0


def test_terminal_falls_back_to_the_process_sandbox_and_says_so(ctx, monkeypatch):
    monkeypatch.setattr(
        registry.terminal.sandbox,
        "isolation_report",
        lambda **kwargs: {
            "mode": "process",
            "binary": "",
            "available": False,
            "probe": "",
            "error": "bwrap is not installed",
        },
    )
    result = ctx["call"]("terminal", {"command": "true", "isolated": True})
    assert result.ok is True
    assert result.data["isolation"]["mode"] == "process"
    assert result.data["isolation"]["fallback_reason"] == "bwrap is not installed"
    assert any("process sandbox" in text for text in result.warnings)


# -------------------------------------------------------------------- read_file


def test_read_file_pages_a_real_file(ctx):
    target = ctx["root"] / "doc.txt"
    target.write_text("\n".join(f"line {index}" for index in range(1, 11)) + "\n", encoding="utf-8")
    result = ctx["call"]("read_file", {"path": "doc.txt", "offset": 3, "limit": 2})
    assert result.ok is True
    data = result.data
    assert data["content"] == "3|line 3\n4|line 4"
    assert data["total_lines"] == 10
    assert data["truncated"] is True
    assert data["next_offset"] == 5
    assert len(data["sha256"]) == 64


def test_read_file_missing_file_is_not_found_with_the_path_named(ctx):
    result = ctx["call"]("read_file", {"path": "nope.txt"})
    assert result.ok is False and result.error_kind == "not_found"
    assert result.exit_code == C.EXIT_NOT_FOUND
    assert result.data["exists"] is False
    assert "nope.txt" in result.error


def test_read_file_refuses_to_leave_the_root(ctx):
    result = ctx["call"]("read_file", {"path": "../../etc/passwd"})
    assert result.ok is False and result.error_kind == "blocked"


def test_read_file_does_not_pour_a_binary_file_into_the_context(ctx):
    (ctx["root"] / "blob.bin").write_bytes(b"\x00\x01\x02" * 100)
    result = ctx["call"]("read_file", {"path": "blob.bin"})
    assert result.ok is True
    assert result.data["shape"] == "binary"
    assert result.data["content"] == ""
    assert result.data["warnings"]


def test_read_file_can_read_its_own_spill_directory(ctx):
    spill = ctx["runtime"] / "tool_output"
    spill.mkdir(parents=True)
    (spill / "earlier.txt").write_text("spilled\n", encoding="utf-8")
    result = ctx["call"]("read_file", {"path": str(spill / "earlier.txt")})
    assert result.ok is True
    assert result.data["content"] == "1|spilled"
    # but not an arbitrary absolute path next to it
    assert ctx["call"]("read_file", {"path": "/etc/hostname"}).error_kind == "blocked"


# ------------------------------------------------------------------- write_file


def test_write_file_writes_verifies_and_hashes(ctx):
    result = ctx["call"](
        "write_file", {"path": "deep/note.txt", "content": "hi\n", "make_parents": True}
    )
    assert result.ok is True
    data = result.data
    assert data["created"] is True and data["verified"] is True
    assert data["bytes_written"] == 3 and data["total_bytes"] == 3
    assert (ctx["root"] / "deep" / "note.txt").read_text() == "hi\n"
    assert data["parents_created"] == [str(ctx["root"] / "deep")]


def test_write_file_append_mode_appends_and_verifies(ctx):
    ctx["call"]("write_file", {"path": "log.txt", "content": "one\n"})
    result = ctx["call"]("write_file", {"path": "log.txt", "content": "two\n", "mode": "append"})
    assert result.data["verified"] is True
    assert result.data["total_bytes"] == 8
    assert (ctx["root"] / "log.txt").read_text() == "one\ntwo\n"


def test_write_file_create_mode_refuses_an_existing_file(ctx):
    ctx["call"]("write_file", {"path": "keep.txt", "content": "first"})
    result = ctx["call"]("write_file", {"path": "keep.txt", "content": "second", "mode": "create"})
    assert result.ok is False and result.error_kind == "conflict"
    assert result.exit_code == C.EXIT_CONFLICT
    assert (ctx["root"] / "keep.txt").read_text() == "first"


@pytest.mark.parametrize(
    "relative", [".env", ".git/config", "keys/id_rsa", "certs/server.pem", ".ssh/authorized_keys"]
)
def test_write_file_refuses_credential_and_vcs_paths(ctx, relative):
    result = ctx["call"]("write_file", {"path": relative, "content": "x"})
    assert result.ok is False and result.error_kind == "blocked"
    assert "deny rule" in result.error
    assert not (ctx["root"] / relative).exists()


def test_write_file_refuses_a_path_outside_the_root(ctx):
    result = ctx["call"]("write_file", {"path": "../outside.txt", "content": "x"})
    assert result.ok is False and result.error_kind == "blocked"


def test_write_file_missing_parent_needs_make_parents(ctx):
    result = ctx["call"]("write_file", {"path": "a/b/c.txt", "content": "x"})
    assert result.ok is False and result.error_kind == "not_found"
    assert "make_parents" in result.error


# -------------------------------------------------------------------- list_dir


def test_list_dir_lists_without_recursing_and_reports_skips(ctx):
    (ctx["root"] / "b.txt").write_text("b", encoding="utf-8")
    (ctx["root"] / "a.txt").write_text("aa", encoding="utf-8")
    (ctx["root"] / ".hidden").write_text("h", encoding="utf-8")
    (ctx["root"] / "node_modules").mkdir()
    result = ctx["call"]("list_dir", {"path": "."})
    names = [entry["name"] for entry in result.data["entries"]]
    assert names == ["a.txt", "b.txt"]  # sorted, dotfiles hidden by default
    assert result.data["count"] == 2
    assert "node_modules" in result.data["skipped"]


def test_list_dir_depth_glob_and_limit(ctx):
    nested = ctx["root"] / "src"
    nested.mkdir()
    (nested / "one.py").write_text("x", encoding="utf-8")
    (nested / "two.txt").write_text("x", encoding="utf-8")
    deep = ctx["call"]("list_dir", {"path": ".", "depth": 2, "glob": "*.py"})
    assert [entry["path"] for entry in deep.data["entries"]] == ["src/one.py"]
    limited = ctx["call"]("list_dir", {"path": ".", "limit": 1})
    assert limited.data["count"] == 1 and limited.data["truncated"] is True
    assert ctx["call"]("list_dir", {"path": "missing"}).error_kind == "not_found"


def test_list_dir_spends_the_limit_on_entries_the_caller_can_see(ctx):
    """The limit is applied AFTER the hidden/``SKIP_DIRS`` filter, not before it.

    The measured defect: with the limit counted against the raw sorted children,
    ``limit: 5`` on a directory whose first five children are hidden or build/cache
    directories answered **0 entries with ``truncated: true``** — the entire limit
    was spent on names the caller never receives. Here the five sorted-first
    children are exactly that shape.
    """
    for name in (".cache", ".git", ".hg", ".pytest_cache", ".venv"):
        (ctx["root"] / name).mkdir()
    (ctx["root"] / "z.txt").write_text("z", encoding="utf-8")
    (ctx["root"] / "a.txt").write_text("a", encoding="utf-8")

    result = ctx["call"]("list_dir", {"path": ".", "limit": 5})
    assert result.ok is True
    assert [entry["name"] for entry in result.data["entries"]] == ["a.txt", "z.txt"], (
        "the limit must not be spent on the hidden/skipped children"
    )
    assert result.data["count"] == 2
    assert result.data["truncated"] is False, "the tree was exhausted, nothing was cut"
    # what was skipped is still reported, including the hidden ones that are skip dirs
    assert {".git", ".hg", ".pytest_cache", ".venv"} <= set(result.data["skipped"])

    # and the limit is still a limit: one accepted entry, and `truncated` says so
    one = ctx["call"]("list_dir", {"path": ".", "limit": 1})
    assert [entry["name"] for entry in one.data["entries"]] == ["a.txt"]
    assert one.data["count"] == 1 and one.data["truncated"] is True


# ----------------------------------------------------------------------- patch


def test_patch_exact_match_returns_a_diff_and_the_hashes(ctx):
    target = ctx["root"] / "mod.py"
    target.write_text("def f():\n    return 1\n", encoding="utf-8")
    result = ctx["call"](
        "patch", {"path": "mod.py", "old_string": "return 1", "new_string": "return 2"}
    )
    assert result.ok is True
    data = result.data
    assert data["strategy"] == "exact" and data["score"] == 1.0
    assert data["applied"] is True and data["replacements"] == 1
    assert "-    return 1" in data["diff"] and "+    return 2" in data["diff"]
    assert data["before_sha256"] != data["after_sha256"]
    assert target.read_text() == "def f():\n    return 2\n"


def test_patch_falls_back_to_the_indentation_flexible_strategy(ctx):
    target = ctx["root"] / "ind.py"
    target.write_text("def outer():\n        value = 9\n        return value\n", encoding="utf-8")
    result = ctx["call"](
        "patch",
        {
            "path": "ind.py",
            "old_string": "value = 9\nreturn value",
            "new_string": "value = 10\nreturn value",
        },
    )
    assert result.data["strategy"] == "indentation-flexible"
    assert result.data["reindent"] == "'        '"
    assert target.read_text() == "def outer():\n        value = 10\n        return value\n"


def test_patch_tolerates_trailing_whitespace(ctx):
    target = ctx["root"] / "ws.txt"
    target.write_text("alpha   \nbeta\n", encoding="utf-8")
    result = ctx["call"](
        "patch", {"path": "ws.txt", "old_string": "alpha\nbeta", "new_string": "one\ntwo"}
    )
    assert result.data["strategy"] == "trailing-whitespace"
    assert target.read_text() == "one\ntwo\n"


def test_patch_refuses_an_ambiguous_match_instead_of_guessing(ctx):
    target = ctx["root"] / "twice.txt"
    target.write_text("value = 1\nvalue = 2\n", encoding="utf-8")
    result = ctx["call"]("patch", {"path": "twice.txt", "old_string": "value", "new_string": "val"})
    assert result.ok is False and result.error_kind == "conflict"
    assert result.exit_code == C.EXIT_CONFLICT
    assert result.data["matches"] == 2
    assert target.read_text() == "value = 1\nvalue = 2\n"
    all_of_them = ctx["call"](
        "patch",
        {"path": "twice.txt", "old_string": "value", "new_string": "val", "replace_all": True},
    )
    assert all_of_them.data["replacements"] == 2
    assert target.read_text() == "val = 1\nval = 2\n"


def test_patch_reports_a_missing_target(ctx):
    (ctx["root"] / "f.txt").write_text("hello\n", encoding="utf-8")
    result = ctx["call"]("patch", {"path": "f.txt", "old_string": "zzz", "new_string": "y"})
    assert result.ok is False and result.error_kind == "not_found"
    assert (
        ctx["call"]("patch", {"path": "ghost.txt", "old_string": "a", "new_string": "b"}).error_kind
        == "not_found"
    )


def test_patch_dry_run_writes_nothing(ctx):
    target = ctx["root"] / "dry.txt"
    target.write_text("one\n", encoding="utf-8")
    result = ctx["call"](
        "patch", {"path": "dry.txt", "old_string": "one", "new_string": "two", "dry_run": True}
    )
    assert result.data["applied"] is False and result.data["dry_run"] is True
    assert "+two" in result.data["diff"]
    assert target.read_text() == "one\n"


def test_patch_refuses_a_no_op_and_a_denied_path(ctx):
    (ctx["root"] / "f.txt").write_text("a\n", encoding="utf-8")
    assert (
        ctx["call"]("patch", {"path": "f.txt", "old_string": "a", "new_string": "a"}).error_kind
        == "usage"
    )
    assert (
        ctx["call"]("patch", {"path": ".env", "old_string": "a", "new_string": "b"}).error_kind
        == "blocked"
    )


def test_a_large_diff_is_truncated_and_spilled(ctx):
    target = ctx["root"] / "big.txt"
    target.write_text("\n".join(f"line {index}" for index in range(4000)) + "\n", encoding="utf-8")
    result = ctx["call"](
        "patch",
        {
            "path": "big.txt",
            "old_string": "\n".join(f"line {index}" for index in range(4000)),
            "new_string": "\n".join(f"changed {index}" for index in range(4000)),
        },
    )
    assert result.data["diff_truncated"] is True
    assert "TRUNCATED" in result.data["diff"]
    assert result.data["spill"] and Path(result.data["spill"][0]).is_file()


# ---------------------------------------------------------------- search_files


def test_search_files_finds_content_with_context(ctx):
    (ctx["root"] / "one.py").write_text("import os\n\ndef f():\n    return 1\n", encoding="utf-8")
    (ctx["root"] / "two.txt").write_text("nothing here\n", encoding="utf-8")
    result = ctx["call"]("search_files", {"pattern": "def f", "context": 1})
    assert result.ok is True
    assert result.data["engine"] == "python-re"
    assert result.data["count"] == 1
    match = result.data["matches"][0]
    assert match["path"] == "one.py" and match["line"] == 3
    assert match["context"] == ["2|", "4|    return 1"]


def test_search_files_output_modes_and_glob(ctx):
    (ctx["root"] / "a.py").write_text("needle\nneedle\n", encoding="utf-8")
    (ctx["root"] / "b.md").write_text("needle\n", encoding="utf-8")
    content = ctx["call"]("search_files", {"pattern": "needle", "file_glob": "*.py"})
    assert content.data["count"] == 2 and content.data["files_scanned"] == 1
    files = ctx["call"]("search_files", {"pattern": "needle", "output_mode": "files_only"})
    assert {match["path"] for match in files.data["matches"]} == {"a.py", "b.md"}
    counts = ctx["call"]("search_files", {"pattern": "needle", "output_mode": "count"})
    assert {match["path"]: match["count"] for match in counts.data["matches"]} == {
        "a.py": 2,
        "b.md": 1,
    }


def test_search_files_by_name_target(ctx):
    (ctx["root"] / "mod_alpha.py").write_text("x", encoding="utf-8")
    (ctx["root"] / "other.md").write_text("x", encoding="utf-8")
    result = ctx["call"]("search_files", {"pattern": "mod_*.py", "target": "files"})
    assert result.data["engine"] == "fnmatch"
    assert result.data["files_scanned"] == 2
    assert [match["path"] for match in result.data["matches"]] == ["mod_alpha.py"]


def test_search_files_rejects_a_bad_regex_with_the_reason(ctx):
    result = ctx["call"]("search_files", {"pattern": "([unclosed"})
    assert result.ok is False and result.error_kind == "usage"
    assert result.exit_code == C.EXIT_USAGE
    assert "unterminated" in result.error


def test_search_files_is_bounded_and_reports_truncation(ctx):
    for index in range(10):
        (ctx["root"] / f"f{index}.txt").write_text("hit\n", encoding="utf-8")
    result = ctx["call"]("search_files", {"pattern": "hit", "limit": 3})
    assert result.data["count"] == 3
    assert result.data["total_matches"] == 10
    assert result.data["truncated"] is True
    paged = ctx["call"]("search_files", {"pattern": "hit", "limit": 3, "offset": 3})
    assert paged.data["count"] == 3
    assert {m["path"] for m in paged.data["matches"]} != {m["path"] for m in result.data["matches"]}


def test_search_files_skips_binary_and_build_directories(ctx):
    (ctx["root"] / "blob.bin").write_bytes(b"\x00hit\x00")
    (ctx["root"] / "node_modules").mkdir()
    (ctx["root"] / "node_modules" / "vendored.js").write_text("hit\n", encoding="utf-8")
    result = ctx["call"]("search_files", {"pattern": "hit"})
    assert result.data["count"] == 0
    assert "node_modules" in result.data["skipped_dirs"]
    assert (
        ctx["call"]("search_files", {"pattern": "hit", "path": "nowhere"}).error_kind == "not_found"
    )


# ------------------------------------------------------------------- web tools


class _Transport:
    """A stand-in for the HTTP transport: recorded calls, declared answers."""

    def __init__(self, routes: dict):
        self.routes = routes
        self.calls: list[dict] = []

    def __call__(self, *, url, timeout_s, max_bytes, headers, data=None):
        self.calls.append(
            {"url": url, "timeout_s": timeout_s, "max_bytes": max_bytes, "data": data}
        )
        for prefix, response in self.routes.items():
            if url.startswith(prefix):
                if isinstance(response, Exception):
                    raise response
                return dict(response)
        raise AssertionError(f"unexpected url: {url}")


def make_transport(routes: dict) -> _Transport:
    return _Transport(routes)


def test_web_fetch_extracts_text_from_html_and_reports_the_facts(ctx):
    transport = make_transport(
        {
            "https://example.test/": {
                "status": 200,
                "final_url": "https://example.test/",
                "content_type": "text/html; charset=utf-8",
                "body": b"<html><head><style>x{}</style></head><body><h1>Title</h1>"
                b"<script>var x=1</script><p>Body&nbsp;text</p></body></html>",
            }
        }
    )
    result = ctx["call"]("web_fetch", {"url": "https://example.test/"}, transport=transport)
    assert result.ok is True
    data = result.data
    assert data["status"] == 200 and data["shape"] == "html"
    assert "Title" in data["text"] and "Body" in data["text"]
    assert "var x=1" not in data["text"] and "x{}" not in data["text"]
    assert data["engine"] == "injected transport"
    assert len(data["sha256"]) == 64
    assert transport.calls[0]["url"] == "https://example.test/"


def test_web_fetch_caps_the_body_and_spills_it(ctx):
    body = b"x" * 300000
    transport = make_transport(
        {
            "https://big.test/": {
                "status": 200,
                "final_url": "https://big.test/",
                "content_type": "application/octet-stream",
                "body": body,
            }
        }
    )
    result = ctx["call"]("web_fetch", {"url": "https://big.test/"}, transport=transport)
    assert result.data["truncated"] is True
    payload = result.data["text"].split("[TRUNCATED")[0]
    assert len(payload) <= 100000  # the cap applies to the payload, not the marker
    assert result.data["spill"] and Path(result.data["spill"][0]).is_file()


def test_web_fetch_refuses_schemes_metadata_addresses_and_offline_contexts(ctx):
    for url, fragment in (
        ("file:///etc/passwd", "http/https only"),
        ("http://169.254.169.254/latest/meta-data/", "link-local/metadata"),
    ):
        result = ctx["call"]("web_fetch", {"url": url})
        assert result.ok is False and result.error_kind == "blocked"
        assert fragment in result.error
    offline = ctx["call"]("web_fetch", {"url": "https://example.test/"}, offline=True)
    assert offline.error_kind == "blocked" and "offline" in offline.error


def test_web_fetch_reports_a_transport_failure_as_network(ctx):
    transport = make_transport({"https://down.test/": ConnectionRefusedError("refused")})
    result = ctx["call"]("web_fetch", {"url": "https://down.test/"}, transport=transport)
    assert result.ok is False and result.error_kind == "network"
    assert result.exit_code == C.EXIT_UNKNOWN
    assert "ConnectionRefusedError" in result.error


def test_web_fetch_refuses_a_credential_looking_header(ctx):
    result = ctx["call"](
        "web_fetch", {"url": "https://example.test/", "headers": {"X-Api-Key": "secret"}}
    )
    assert result.ok is False and result.error_kind == "usage"


def test_web_search_parses_the_key_free_backends(ctx):
    transport = make_transport(
        {
            # bing leads the chain (measured-working backend first); here it 502s
            # so the fallback under test — marginalia — is the one that answers.
            "https://www.bing.com/": {"status": 502, "body": b"bad gateway"},
            "https://api.marginalia.nu/": {
                "status": 200,
                "content_type": "application/json",
                "body": json.dumps(
                    {
                        "results": [
                            {"url": "https://a.test/", "title": "A", "description": "alpha"},
                            {"url": "https://b.test/", "title": "B", "description": "beta"},
                        ]
                    }
                ).encode(),
            }
        }
    )
    result = ctx["call"]("web_search", {"query": "anything", "limit": 1}, transport=transport)
    assert result.ok is True
    assert result.data["backend"] == "marginalia"
    assert result.data["count"] == 1 and result.data["truncated"] is True
    assert result.data["results"][0]["url"] == "https://a.test/"
    assert result.data["key_required"] is False
    assert "www.bing.com" in transport.calls[0]["url"], "bing is tried first"


def test_web_search_falls_over_to_the_second_key_free_backend(ctx):
    transport = make_transport(
        {
            "https://www.bing.com/": {"status": 502, "body": b"bad gateway"},
            "https://api.marginalia.nu/": {"status": 502, "body": b"bad gateway"},
            "https://api.duckduckgo.com/": {
                "status": 200,
                "content_type": "application/json",
                "body": json.dumps(
                    {
                        "AbstractText": "Rust is a language",
                        "AbstractURL": "https://rust.test/",
                        "RelatedTopics": [
                            {"Text": "Rust weekly", "FirstURL": "https://news.test/"}
                        ],
                    }
                ).encode(),
            },
        }
    )
    result = ctx["call"]("web_search", {"query": "rust"}, transport=transport)
    assert result.data["backend"] == "duckduckgo"
    assert [item["source"] for item in result.data["results"]] == [
        "duckduckgo:abstract",
        "duckduckgo:related",
    ]
    # one bounded retry for each 5xx-hit backend (bing, then marginalia — both
    # server_error), then the fallback to duckduckgo answered on its first try
    assert [attempt["answered"] for attempt in result.data["attempts"]] == [
        False, False, False, False, True
    ]
    assert result.data["attempts"][0]["failure_class"] == "server_error"
    assert result.data["attempts"][0]["backend"] == "bing"
    assert result.warnings, "the failed bing/marginalia tries must be named in the warnings"


def test_web_search_refuses_and_names_what_is_missing_when_no_backend_answers(ctx):
    transport = make_transport(
        {
            "https://www.bing.com/": {"status": 503, "body": b""},
            "https://api.marginalia.nu/": {"status": 503, "body": b""},
            "https://api.duckduckgo.com/": TimeoutError("timed out"),
            "https://lite.duckduckgo.com/lite/": TimeoutError("timed out"),
        }
    )
    result = ctx["call"]("web_search", {"query": "anything"}, transport=transport)
    assert result.ok is False and result.error_kind == "blocked"
    assert result.exit_code == C.EXIT_BLOCKED
    # bing and marginalia 503 = server_error → one bounded retry each (2 tries
    # per backend); the timeouts are not retryable inside one call — the
    # fallback (and then the refusal) is the recovery
    assert result.data["missing"] and len(result.data["missing"]) == 6
    assert "bing" in result.data["missing"][0]
    assert "server_error" in result.data["missing"][0]
    assert "marginalia" in result.data["missing"][2]
    assert "timed out" in result.data["missing"][4]
    assert set(result.data["failure_classes"]) == {"server_error", "transient_network"}
    assert result.data["key_required"] is False


def test_web_search_respects_an_explicit_backend_and_the_offline_context(ctx):
    transport = make_transport(
        {
            "https://api.duckduckgo.com/": {
                "status": 200,
                "body": json.dumps(
                    {"AbstractText": "x", "AbstractURL": "https://x.test/"}
                ).encode(),
            }
        }
    )
    result = ctx["call"]("web_search", {"query": "q", "backend": "duckduckgo"}, transport=transport)
    assert result.data["backend"] == "duckduckgo"
    assert len(result.data["attempts"]) == 1
    offline = ctx["call"]("web_search", {"query": "q"}, offline=True)
    assert offline.error_kind == "blocked"


def test_backend_probe_reports_availability_without_a_key():
    probe = web.probe_backends(transport=make_transport({}), timeout_s=1)
    assert probe["key_required"] is False
    assert probe["available"] is False
    assert all(attempt["key_required"] is False for attempt in probe["backends"])
    assert len(probe["missing"]) == len(web.BACKENDS)


def test_the_live_network_tools_when_this_machine_has_one():
    """Run the real transports — skip (never pass silently) when there is no network."""
    runtime_dir = Path(os.environ.get("TMPDIR", "/tmp")) / "core-tools-live"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    probe = web.probe_backends(timeout_s=5)
    if not probe["available"]:
        pytest.skip(f"no key-free backend reachable: {probe['missing']}")
    first = probe["answering"][0]
    fetch = registry.call(
        "web_fetch",
        {"url": "https://example.com/", "max_bytes": 20000},
        runtime=runtime_dir,
        root=runtime_dir,
    )
    assert fetch.ok is True and fetch.data["status"] == 200
    assert "Example Domain" in fetch.data["text"]
    search = registry.call(
        "web_search",
        {"query": "rust", "limit": 2, "backend": first},
        runtime=runtime_dir,
        root=runtime_dir,
    )
    if not search.ok:
        # a flaky/flapping key-free endpoint is not a defect of the tool: the
        # refusal names the reason, and the deterministic transport test above
        # already pins this path. Skip rather than report a false pass or fail.
        pytest.skip(
            f"the backend answered the probe but not the search: {search.data.get('missing')}"
        )
    assert search.data["backend"] == first and search.data["count"] >= 1
