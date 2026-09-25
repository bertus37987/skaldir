"""P0-2 — generated testing tools reach the completion gate.

The audit found `register_check` had zero callers. These tests prove the
full chain: registrar scans validated manifests, the runner executes the
tool for real through the sandboxed runner, and — the definition of a
trustworthy testing tool — it FAILS on a broken target and PASSES on a
working target.
"""

from skaldir.capability import register_testing_tools
from skaldir.completion import execute_check
from skaldir.toolusage import usage_counts

TOOL_SOURCE = '''import pathlib
import sys

target = pathlib.Path(sys.argv[1])
ok = (target / "README.md").exists()
print("PASS" if ok else "FAIL")
raise SystemExit(0 if ok else 1)
'''


def _plant_tool(runtime, name, *, tool_type="testing_tool", status="ready", validation="validated"):
    tool_dir = runtime / "generated_tools" / name
    tool_dir.mkdir(parents=True)
    (tool_dir / f"{name}.py").write_text(TOOL_SOURCE, encoding="utf-8")
    validation_block = (
        f"validation:\n  status: {validation}\n  checks:\n  - case: broken\n    ok: true\n"
        if validation
        else ""
    )
    (tool_dir / "manifest.yaml").write_text(
        f"name: {name}\ntype: {tool_type}\nstatus: {status}\n"
        f"purpose: check that a target ships a README\n{validation_block}",
        encoding="utf-8",
    )
    return tool_dir


class TestTestingToolGate:
    def test_working_target_passes_broken_target_fails(self, tmp_path):
        runtime = tmp_path / "runtime"
        _plant_tool(runtime, "check_readme")
        registered = register_testing_tools(runtime)
        assert registered == ["check_readme"]

        working = tmp_path / "working"
        working.mkdir()
        (working / "README.md").write_text("hi", encoding="utf-8")
        evidence = execute_check("check_readme", working)
        assert evidence is not None and evidence.ok is True
        assert evidence.status == "passed" and evidence.source == "runner"

        broken = tmp_path / "broken"
        broken.mkdir()
        evidence = execute_check("check_readme", broken)
        assert evidence is not None and evidence.ok is False
        assert evidence.status == "failed"

        counts = usage_counts(runtime)
        assert counts.get("check_readme", 0) >= 2  # both runs recorded

    def test_unvalidated_tool_is_never_registered(self, tmp_path):
        runtime = tmp_path / "runtime"
        _plant_tool(runtime, "shaky", validation="failed")
        assert register_testing_tools(runtime) == []
        assert execute_check("shaky", tmp_path) is None

    def test_non_testing_type_is_skipped(self, tmp_path):
        runtime = tmp_path / "runtime"
        _plant_tool(runtime, "plain_cli", tool_type="cli")
        assert register_testing_tools(runtime) == []

    def test_registrar_is_idempotent(self, tmp_path):
        runtime = tmp_path / "runtime"
        _plant_tool(runtime, "check_readme")
        assert register_testing_tools(runtime) == ["check_readme"]
        assert register_testing_tools(runtime) == ["check_readme"]
