"""H8 safety proof: what the sandbox refuses, kills, caps and scrubs.

Every test here runs a **real child process** through
:func:`skaldir.code_exec.run_code` — the point of a sandbox is what actually happens
to a real snippet, so nothing is mocked and nothing is asserted from the policy text
alone. Four claims are proven separately, because they fail separately:

* a runaway is killed at the deadline **and** the workspace is byte-identical after;
* the child has no ambient credentials (a secret the parent holds is not there);
* the child cannot import the product (it would let a snippet call tools *directly*,
  without this run's policy, budget or evidence);
* every cap is a real refusal or a real truncation with a marker, never silence.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from skaldir import code_exec as CE


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """A small workspace with two files a snippet can really read."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "notes.txt").write_text("alpha\nTODO: fix\nbeta\n", encoding="utf-8")
    (ws / "data.txt").write_text("one\ntwo\n", encoding="utf-8")
    return ws


def _tree(root: Path) -> dict[str, str]:
    """Every file under ``root`` with its content hash — the "unchanged" evidence."""
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _runtime(tmp_path: Path) -> Path:
    runtime = tmp_path / "runtime"
    runtime.mkdir(exist_ok=True)
    return runtime


# --------------------------------------------------------------------- runaway


def test_a_runaway_is_killed_at_the_deadline_and_leaves_the_tree_untouched(
    workspace: Path, tmp_path: Path
) -> None:
    """An infinite loop dies by SIGKILL at the deadline; the workspace is unchanged."""
    before = _tree(workspace)
    result = CE.run_code(
        "print('spinning')\nwhile True:\n    pass\n",
        workspace=workspace,
        runtime=_runtime(tmp_path),
        timeout_s=2,
    )
    after = _tree(workspace)
    assert result.status == CE.STATUS_TIMEOUT
    assert result.ok is False
    assert result.killed is True and result.killed_signal == "SIGKILL"
    assert 1_500 <= result.duration_ms <= 12_000, "the deadline was not what killed it"
    assert result.exit_code is not None and result.exit_code < 0, "a signal, not an exit code"
    assert result.workspace_digest_before == result.workspace_digest_after
    assert before == after, "the runaway touched the workspace"
    # `-u` is why this survives the SIGKILL: the loop's output was already written.
    assert result.stdout == "spinning\n"
    assert result.process_group  # the group the signal went to


def test_the_kill_targets_the_whole_process_group(workspace: Path, tmp_path: Path) -> None:
    """A child the snippet spawned is in the same group and dies with it."""
    result = CE.run_code(
        "import os, subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(600)'])\n"
        "print(child.pid, flush=True)\n"
        "time.sleep(600)\n",
        workspace=workspace,
        runtime=_runtime(tmp_path),
        timeout_s=2,
    )
    assert result.status == CE.STATUS_TIMEOUT
    spawned = int(result.stdout.strip())
    with pytest.raises(ProcessLookupError):
        os.kill(spawned, 0)
    assert result.workspace_digest_before == result.workspace_digest_after


# ------------------------------------------------------------------ credentials


def test_the_child_has_no_ambient_credentials(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A secret the parent process holds is *not* in the child's environment."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-parent-only-openai")
    monkeypatch.setenv("MY_SECRET_TOKEN", "sk-parent-only-secret")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-parent-only-deepseek")
    result = CE.run_code(
        "import os\n"
        "result = {name: os.environ.get(name) for name in (\n"
        "    'OPENAI_API_KEY', 'MY_SECRET_TOKEN', 'DEEPSEEK_API_KEY', 'PATH', 'HOME')}\n",
        workspace=workspace,
        runtime=_runtime(tmp_path),
    )
    assert result.status == CE.STATUS_OK, result.reason
    assert result.value["OPENAI_API_KEY"] is None
    assert result.value["MY_SECRET_TOKEN"] is None
    assert result.value["DEEPSEEK_API_KEY"] is None
    # positive control: the environment is not simply empty
    assert result.value["PATH"] and result.value["HOME"]
    assert os.environ["OPENAI_API_KEY"] == "sk-parent-only-openai"  # the parent still has it


def test_the_policy_report_names_the_dropped_credentials_but_never_their_values(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``code explain`` documents the scrub by *name* — a value must not appear anywhere."""
    monkeypatch.setenv("MY_SECRET_TOKEN", "sk-parent-only-secret")
    report = CE.policy_report(workspace=workspace, runtime=_runtime(tmp_path))
    scrub = report["environment"]["scrub"]
    assert scrub["available"] is True
    assert "MY_SECRET_TOKEN" in scrub["dropped_credentials"]
    assert "PYTHONPATH" in scrub["dropped_by_design"]
    assert "sk-parent-only-secret" not in json.dumps(report)
    assert report["limits"]["wall_clock_s"] == CE.DEFAULT_TIMEOUT_S
    assert report["tools"]["allowed"], "no tool is allowed: the sandbox could do nothing"


# ----------------------------------------------------------------------- imports


def test_the_child_cannot_import_the_product_or_its_dependencies(
    workspace: Path, tmp_path: Path, leaked_module: Path
) -> None:
    """No site-packages and no PYTHONPATH: only stdlib, and `skaldir` is the host API.

    If the child could ``import skaldir`` (the editable install of this very repo), a
    snippet could call a tool directly and skip the host's policy, budget and evidence.
    The second probe removes the host API from ``sys.modules`` first — that is the
    deliberate attempt to get at the *real* package, and it must fail too.
    """
    result = CE.run_code(
        "import importlib, sys\n"
        "import skaldir\n"
        "def can(name):\n"
        "    try:\n"
        "        importlib.import_module(name)\n"
        "        return 'imported'\n"
        "    except Exception as exc:\n"
        "        return type(exc).__name__\n"
        "call = skaldir.call('list_dir', path='.', limit=3)\n"
        "host_api = call['ok'] and not hasattr(skaldir, '__file__')\n"
        "direct = can('skaldir.tools_core')\n"
        "del sys.modules['skaldir']\n"
        "deliberate = can('skaldir.tools_core')\n"
        "result = {'host_api': host_api, 'tools_core': direct, 'after_del': deliberate,\n"
        "          'httpx': can('httpx'), 'leaky': can('leaky_marker'), 'json': can('json')}\n",
        workspace=workspace,
        runtime=_runtime(tmp_path),
    )
    assert result.status == CE.STATUS_OK, result.reason
    assert result.value["host_api"] is True
    assert result.value["tools_core"] == "ModuleNotFoundError"
    assert result.value["after_del"] == "ModuleNotFoundError", (
        "the product is importable in the child — a snippet could call tools directly"
    )
    assert result.value["httpx"] == "ModuleNotFoundError"
    assert result.value["leaky"] == "ModuleNotFoundError", "PYTHONPATH reached the child"
    assert result.value["json"] == "imported"
    assert "-S" in result.argv and "-I" in result.argv


@pytest.fixture
def leaked_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, workspace: Path
) -> Path:
    """A module on PYTHONPATH *and* in the cwd — neither path may reach the child."""
    directory = tmp_path / "leaky"
    directory.mkdir()
    (directory / "leaky_marker.py").write_text("VALUE = 'leaked'\n", encoding="utf-8")
    (workspace / "leaky_marker.py").write_text("VALUE = 'leaked from cwd'\n", encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(directory))
    return directory


def test_site_packages_can_only_be_widened_explicitly(
    workspace: Path, tmp_path: Path
) -> None:
    """``site_packages=True`` is the documented widening — and it is a real one.

    Narrow (the default) the product is not importable *even after deleting the host API
    from ``sys.modules``*; widened it is, which is exactly what the flag's help says and
    why it is off by default.
    """
    probe = (
        "import importlib, sys\n"
        "del sys.modules['skaldir']\n"
        "try:\n"
        "    importlib.import_module('skaldir.tools_core')\n"
        "    result = 'imported'\n"
        "except Exception as exc:\n"
        "    result = type(exc).__name__\n"
    )
    narrow = CE.run_code(probe, workspace=workspace, runtime=_runtime(tmp_path))
    wide = CE.run_code(
        probe, workspace=workspace, runtime=_runtime(tmp_path), site_packages=True
    )
    assert narrow.value == "ModuleNotFoundError"
    assert wide.value == "imported", "the widening is documented — it must really widen"
    assert "-S" in narrow.argv
    assert "-S" not in wide.argv
    report = CE.policy_report(workspace=workspace)
    assert report["environment"]["imports"] == "stdlib only"
    widened = CE.policy_report(workspace=workspace, site_packages=True)
    assert widened["environment"]["imports"] == "stdlib + site-packages"


# ------------------------------------------------------------------- the caps


def test_output_is_capped_with_a_marker_and_the_total_is_reported(
    workspace: Path, tmp_path: Path
) -> None:
    """10 KB of output through a 2 KB cap: head+tail, a marker, and the real total."""
    result = CE.run_code(
        "print('x' * 10_000)\nresult = 'done'\n",
        workspace=workspace,
        runtime=_runtime(tmp_path),
        max_output_bytes=2_000,
    )
    assert result.status == CE.STATUS_OK
    assert result.stdout_truncated is True
    assert result.stdout_total_bytes >= 10_000
    assert "TRUNCATED" in result.stdout
    assert "10000" in result.stdout or "10001" in result.stdout
    assert len(result.stdout) < 4_000


def test_the_result_value_is_capped_with_a_marker(workspace: Path, tmp_path: Path) -> None:
    result = CE.run_code(
        "result = {'blob': 'y' * 20_000}\n",
        workspace=workspace,
        runtime=_runtime(tmp_path),
        max_result_bytes=1_000,
    )
    assert result.status == CE.STATUS_OK
    assert result.result_truncated is True
    assert result.result_bytes > 1_000
    assert isinstance(result.value, str) and "TRUNCATED" in result.value


def test_the_tool_call_budget_refuses_every_call_over_the_cap(
    workspace: Path, tmp_path: Path
) -> None:
    """Six calls, a budget of three: the last three are refused, and it is reported."""
    result = CE.run_code(
        "outs = []\n"
        "for _ in range(6):\n"
        "    outs.append(skaldir.call('list_dir', path='.', limit=5)['ok'])\n"
        "result = outs\n",
        workspace=workspace,
        runtime=_runtime(tmp_path),
        max_tool_calls=3,
    )
    assert result.status == CE.STATUS_OK
    assert result.value == [True, True, True, False, False, False]
    assert result.tool_budget["max"] == 3
    assert result.tool_budget["used"] == 6, "a refusal counts against the budget"
    assert result.tool_budget["exhausted"] is True
    refused = [call for call in result.tool_calls if call.refused]
    assert len(refused) == 3
    assert {call.refused_by for call in refused} == {"budget"}
    assert all(call.error_kind == "budget" for call in refused)
    assert any("exhausted" in warning for warning in result.warnings)


# -------------------------------------------------------------------- refusals


def test_network_tools_are_refused_until_they_are_declared(
    workspace: Path, tmp_path: Path
) -> None:
    """``web_fetch`` is not on the allowlist — the refusal is named, not a network error."""
    result = CE.run_code(
        "r = skaldir.call('web_fetch', url='https://example.invalid/')\n"
        "result = {'ok': r['ok'], 'kind': r['error_kind'], 'by': r['refused_by'],\n"
        "          'error': r['error']}\n",
        workspace=workspace,
        runtime=_runtime(tmp_path),
    )
    assert result.status == CE.STATUS_OK
    assert result.value["ok"] is False
    assert result.value["kind"] == "blocked"
    assert result.value["by"] == "allowlist"
    assert "network is not declared" in result.value["error"]
    policy = CE.CodeExecPolicy(workspace=workspace, runtime=_runtime(tmp_path)).normalized()
    narrow = {row["name"]: row["allowed"] for row in CE.host_inventory(policy)}
    assert narrow["web_fetch"] is False and narrow["read_file"] is True
    wide = CE.CodeExecPolicy(workspace=workspace, allow_network=True).normalized()
    assert {row["name"]: row["allowed"] for row in CE.host_inventory(wide)}["web_fetch"] is True


def test_a_write_outside_the_workspace_is_refused(workspace: Path, tmp_path: Path) -> None:
    """``..`` and an absolute path both stop at the tool root — nothing lands outside."""
    outside = tmp_path / "escaped.txt"
    result = CE.run_code(
        "a = skaldir.call('write_file', path='../escaped.txt', content='nope')\n"
        "b = skaldir.call('write_file', path=" + json.dumps(str(outside)) + ", content='nope')\n"
        "result = [(r['ok'], r['error_kind'], r['refused_by']) for r in (a, b)]\n",
        workspace=workspace,
        runtime=_runtime(tmp_path),
    )
    assert result.status == CE.STATUS_OK
    assert result.value == [[False, "blocked", "policy:BLOCK"], [False, "blocked", "policy:BLOCK"]]
    assert not outside.exists()
    assert sorted(_tree(workspace)) == ["data.txt", "notes.txt"]


def test_a_destructive_terminal_call_is_refused_by_the_policy(
    workspace: Path, tmp_path: Path
) -> None:
    """The pre-execution policy runs for a snippet's call exactly as for a normal one."""
    result = CE.run_code(
        "r = skaldir.call('terminal', command='rm -rf /')\n"
        "result = {'ok': r['ok'], 'by': r['refused_by'], 'kind': r['error_kind'],\n"
        "          'class': r['output']['classification'],\n"
        "          'reasons': len(r['output']['reasons']), 'error': r['error']}\n",
        workspace=workspace,
        runtime=_runtime(tmp_path),
    )
    assert result.status == CE.STATUS_OK
    assert result.value["ok"] is False
    assert result.value["kind"] == "blocked"
    assert result.value["by"].startswith("policy:")
    # the verdict and its reasons are *data*; the prose the snippet reads is English
    assert result.value["class"] in {"BLOCK", "ASK_APPROVAL"}
    assert result.value["reasons"] >= 1
    assert "the pre-execution policy classified" in result.value["error"]
    call = result.tool_calls[0]
    assert call.policy_classification in {"BLOCK", "ASK_APPROVAL"}
    assert call.policy_reasons, "a refusal must name the reason"


def test_a_run_that_cannot_be_honoured_is_refused_with_a_reason(
    workspace: Path, tmp_path: Path
) -> None:
    """No tool allowed, an empty snippet and a missing workspace are refusals, not runs.

    The deny list is **derived from the toolset**, never hand-written: a hand-written list
    stops covering the sandbox the moment a tool is added, and ``run_code`` then succeeds
    legitimately (``docker.ensure`` did exactly that to the old 8-name list — one tool
    stayed allowed, and this test failed on ``assert 'ok' == 'refused'``). The size is
    asserted on purpose, so a tenth core tool fails here loudly instead of quietly.
    """
    from skaldir.tools_core import CORE_TOOL_NAMES

    every_tool = CE._core_names()
    assert set(every_tool) == set(CORE_TOOL_NAMES), (
        "the sandbox's names and the toolset's declared names disagree: "
        f"{every_tool} vs {CORE_TOOL_NAMES}"
    )
    assert len(every_tool) == 9, (
        f"the core toolset changed size ({len(every_tool)}): {every_tool} — "
        "deny every name this test now sees, and update the count"
    )
    denied = CE.run_code(
        "result = 1\n",
        workspace=workspace,
        runtime=_runtime(tmp_path),
        deny_tools=tuple(CE.NETWORK_TOOLS) + every_tool,
    )
    assert denied.status == CE.STATUS_REFUSED
    assert "no tool is allowed" in denied.reason
    assert denied.tool_calls == []

    empty = CE.run_code("   \n", workspace=workspace, runtime=_runtime(tmp_path))
    assert empty.status == CE.STATUS_REFUSED and "empty" in empty.reason

    missing = CE.run_code("result = 1\n", workspace=tmp_path / "nope", runtime=_runtime(tmp_path))
    assert missing.status == CE.STATUS_REFUSED
    assert "not a directory" in missing.reason
