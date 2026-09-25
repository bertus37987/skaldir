"""H8 programmatic tool calling: the host API inside the sandbox.

The capability under test is *composing* tool calls in code, so the central test is a
snippet that reads a file through the tool API, calls a second tool **with the first
result**, and returns structured JSON — plus the per-call evidence for both. The rest
pin the contract the snippet sees: the value (last expression or ``result``), the
refusal shape, the honest "not JSON-serializable" case, and that a call made inside the
sandbox returns *the same document* a normal call returns.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from skaldir import code_exec as CE

COMPOSED_SNIPPET = """\
import skaldir

# (a) read through the tool API (content search, not a file read: it gives a hit list)
hits = skaldir.call("search_files", pattern="TODO", path=".", file_glob="*.txt", limit=5)
matches = hits["output"].get("matches") or []

# (b) call another tool with the *result* of the first
path = matches[0]["path"] if matches else "notes.txt"
counted = skaldir.call("terminal", command=f"wc -l {path}")
lines = (counted["output"].get("stdout") or "").split()[0]
first = skaldir.call("read_file", path=path, limit=1)

print(f"composed {len(matches)} hit(s) for {path}")

# (c) structured JSON
result = {
    "hits": [item["text"] for item in matches],
    "file": path,
    "line_count": int(lines) if lines.isdigit() else -1,
    "first_line": (first["output"].get("content") or "").splitlines(),
}
"""


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "notes.txt").write_text("alpha\nTODO: fix me\nbeta\nTODO: second\n", encoding="utf-8")
    (ws / "data.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
    return ws


def _runtime(tmp_path: Path) -> Path:
    runtime = tmp_path / "runtime"
    runtime.mkdir(exist_ok=True)
    return runtime


def _run(source: str, workspace: Path, tmp_path: Path, **kwargs):
    return CE.run_code(source, workspace=workspace, runtime=_runtime(tmp_path), **kwargs)


# ------------------------------------------------------- the composed proof


def test_a_snippet_reads_a_file_then_calls_a_tool_with_that_result(
    workspace: Path, tmp_path: Path
) -> None:
    """(a) read via a tool, (b) feed the result into another tool, (c) return JSON."""
    result = _run(COMPOSED_SNIPPET, workspace, tmp_path)
    assert result.status == CE.STATUS_OK, result.reason or result.traceback_text
    assert result.stdout == "composed 2 hit(s) for notes.txt\n"
    assert result.value == {
        "hits": ["TODO: fix me", "TODO: second"],
        "file": "notes.txt",
        "line_count": 4,
        "first_line": ["1|alpha"],
    }
    assert result.value_source == "result"

    names = [call.tool for call in result.tool_calls]
    assert names == ["search_files", "terminal", "read_file"]
    for call in result.tool_calls:
        assert call.ok is True, (call.tool, call.error)
        assert call.refused is False
        assert call.error_kind == "" and call.exit_code == 0
        assert call.policy_classification == "ALLOW"
        assert call.duration_ms >= 0
        assert call.result_bytes > 0
        assert len(call.result_sha256) == 64
        assert len(call.arguments_sha256) == 64
    assert result.tool_budget == {
        "max": CE.DEFAULT_MAX_TOOL_CALLS,
        "used": 3,
        "remaining": CE.DEFAULT_MAX_TOOL_CALLS - 3,
        "exhausted": False,
        "refused": 0,
        "by_tool": {"read_file": 1, "search_files": 1, "terminal": 1},
    }
    assert result.workspace_digest_before == result.workspace_digest_after


def test_the_second_call_really_received_the_first_result(workspace: Path, tmp_path: Path) -> None:
    """The value only comes out right if the tool result reached the snippet intact."""
    result = _run(COMPOSED_SNIPPET, workspace, tmp_path)
    wc_call = next(call for call in result.tool_calls if call.tool == "terminal")
    expected = {"command": "wc -l notes.txt"}
    assert wc_call.arguments_sha256 == hashlib.sha256(
        json.dumps(expected, sort_keys=True, default=str).encode()
    ).hexdigest()


def test_a_sandboxed_call_returns_the_same_document_a_normal_call_does(
    workspace: Path, tmp_path: Path
) -> None:
    """The sandbox is a *path* to the tool, not a different tool (same output)."""
    from skaldir.tools_core import registry

    outside = registry.call("read_file", {"path": "notes.txt", "limit": 2}, root=workspace)
    result = _run(
        "r = skaldir.call('read_file', path='notes.txt', limit=2)\n"
        "result = r['output']\n",
        workspace,
        tmp_path,
    )
    assert result.status == CE.STATUS_OK
    inside = result.value
    assert inside["content"] == outside.data["content"]
    assert inside["sha256"] == outside.data["sha256"]
    assert inside["total_lines"] == outside.data["total_lines"]
    assert result.tool_calls[0].ok is True


# ------------------------------------------------------------ the return value


@pytest.mark.parametrize(
    ("source", "expected", "origin"),
    [
        ("result = {'a': [1, 2, 3]}\n", {"a": [1, 2, 3]}, "result"),
        ("{'a': 1, 'b': 2}\n", {"a": 1, "b": 2}, "last_expression"),
        ("import json\njson.dumps({'x': 1})\n", '{"x": 1}', "last_expression"),
        ("result = None\n", None, "result"),
    ],
)
def test_the_value_is_the_last_expression_or_the_result_variable(
    source: str, expected, origin: str, workspace: Path, tmp_path: Path
) -> None:
    result = _run(source, workspace, tmp_path)
    assert result.status == CE.STATUS_OK, result.traceback_text
    assert result.value == expected
    assert result.value_source == origin
    assert result.value_serializable is True


def test_a_snippet_without_a_value_reports_none(workspace: Path, tmp_path: Path) -> None:
    """No trailing expression and no ``result``: the honest answer is ``none``.

    (A trailing ``print(x)`` *is* an expression, so it becomes the value — ``None``,
    with ``value_source: "last_expression"``. This case is a snippet that ends in a
    statement, which has no value at all.)
    """
    silent = _run("x = 1 + 1\nif x:\n    y = 2\n", workspace, tmp_path)
    assert silent.status == CE.STATUS_OK
    assert silent.value is None and silent.value_source == "none"

    printed = _run("x = 1 + 1\nprint(x)\n", workspace, tmp_path)
    assert printed.status == CE.STATUS_OK
    assert printed.stdout == "2\n"
    assert printed.value is None and printed.value_source == "last_expression"


def test_a_value_that_is_not_json_serializable_is_reported_honestly(
    workspace: Path, tmp_path: Path
) -> None:
    """Never a silent ``str()``: the flag is false and the repr is kept instead."""
    result = _run("result = object()\n", workspace, tmp_path)
    assert result.status == CE.STATUS_OK
    assert result.value is None
    assert result.value_serializable is False
    assert "object object at" in result.value_repr


# ------------------------------------------------------------ snippet failures


def test_a_snippet_that_raises_is_reported_with_its_traceback(
    workspace: Path, tmp_path: Path
) -> None:
    """A raising snippet is a measured result: status, type, message and the frames."""
    result = _run(
        "print('before')\n"
        "hits = skaldir.call('list_dir', path='.', limit=3)\n"
        "raise ValueError('snippet broke')\n",
        workspace,
        tmp_path,
    )
    assert result.status == CE.STATUS_ERROR
    assert result.ok is False
    assert result.error_type == "ValueError"
    assert result.error_message == "snippet broke"
    assert "snippet.py" in result.traceback_text
    assert "skaldir_code_runner.py" not in result.traceback_text, "the runner's frames are noise"
    assert result.stdout == "before\n"
    assert result.exit_code == 1
    assert [call.tool for call in result.tool_calls] == ["list_dir"], "the call still counted"


def test_a_refused_call_is_a_result_document_the_snippet_can_inspect(
    workspace: Path, tmp_path: Path
) -> None:
    """Unknown tool: ``ok: False`` plus the reason — never an exception the snippet can't see."""
    result = _run(
        "r = skaldir.call('no_such_tool', whatever=1)\n"
        "result = {'ok': r['ok'], 'kind': r['error_kind'], 'by': r['refused_by'],\n"
        "          'exit': r['exit_code'], 'output': r['output'], 'refused': r['refused']}\n",
        workspace,
        tmp_path,
    )
    assert result.status == CE.STATUS_OK
    assert result.value == {
        "ok": False,
        "kind": "not_found",
        "by": "unknown_tool",
        "exit": 5,
        "output": {"refused": True, "refused_by": "unknown_tool"},
        "refused": True,
    }
    assert result.tool_calls[0].refused is True


def test_the_snippet_sees_the_declared_inventory(workspace: Path, tmp_path: Path) -> None:
    """``skaldir.tools()`` is the allowlist the host built, with the reasons it refused."""
    result = _run(
        "rows = {row['name']: (row['allowed'], row['reason']) for row in skaldir.tools()}\n"
        "result = {'read_file': rows['read_file'], 'web_fetch': rows['web_fetch'],\n"
        "          'allowed': sorted(skaldir.allowed_tools())}\n",
        workspace,
        tmp_path,
    )
    assert result.status == CE.STATUS_OK
    assert result.value["read_file"] == [True, ""]
    assert result.value["web_fetch"][0] is False
    assert "network is not declared" in result.value["web_fetch"][1]
    assert "read_file" in result.value["allowed"]
    assert "web_fetch" not in result.value["allowed"]


def test_a_snippet_can_loop_and_filter_tool_results(workspace: Path, tmp_path: Path) -> None:
    """The point of the capability: a loop over files, filtered in code, one result."""
    result = _run(
        "listed = skaldir.call('list_dir', path='.', limit=10)\n"
        "names = [row['name'] for row in listed['output']['entries'] if row['type'] == 'file']\n"
        "sizes = {}\n"
        "for name in sorted(names):\n"
        "    read = skaldir.call('read_file', path=name, limit=100)\n"
        "    sizes[name] = read['output'].get('total_lines')\n"
        "result = {'files': sorted(names), 'lines': sizes}\n",
        workspace,
        tmp_path,
    )
    assert result.status == CE.STATUS_OK, result.traceback_text
    assert result.value["files"] == ["data.txt", "notes.txt"]
    assert result.value["lines"] == {"data.txt": 3, "notes.txt": 4}
    assert [call.tool for call in result.tool_calls] == [
        "list_dir",
        "read_file",
        "read_file",
    ]
    assert result.tool_budget["by_tool"] == {"list_dir": 1, "read_file": 2}


def test_the_run_directory_is_cleaned_up_unless_it_is_kept(
    workspace: Path, tmp_path: Path
) -> None:
    """A run leaves no litter; ``keep_run_dir`` is the deliberate opt-in."""
    cleaned = _run("result = 1\n", workspace, tmp_path)
    assert cleaned.run_dir == ""
    kept = _run("result = 1\n", workspace, tmp_path, keep_run_dir=True)
    assert kept.run_dir and Path(kept.run_dir, "snippet.py").is_file()
    assert Path(kept.run_dir, CE.RUNNER_FILENAME).is_file()
