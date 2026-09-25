"""Audit P1: register_testing_tools darf module != Verzeichnisname nicht still
verwerfen — das Modul wird über das Manifest aufgelöst."""

import yaml


def _make_tool(runtime, dir_name, module_name):
    d = runtime / "generated_tools" / dir_name
    d.mkdir(parents=True)
    (d / f"{module_name}.py").write_text(
        "import sys\nprint('PASS: ok for', sys.argv[1])\n", encoding="utf-8"
    )
    (d / "manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "name": dir_name,
                "type": "testing_tool",
                "status": "ready",
                "module": module_name,
                "validation": {"status": "validated"},
            }
        ),
        encoding="utf-8",
    )
    return d


def test_manifest_module_name_is_resolved(tmp_path):
    from skaldir import capability

    _make_tool(tmp_path, "checker", "custom_impl")
    registered = capability.register_testing_tools(tmp_path)
    assert registered == ["checker"], "tool with module: custom_impl must register"


def test_registered_tool_runs_through_the_gate_contract(tmp_path):
    from skaldir import capability
    from skaldir.gates import parse_testing_result

    _make_tool(tmp_path, "checker", "custom_impl")
    registered = capability.register_testing_tools(tmp_path)
    assert registered

    from skaldir.completion import execute_check

    ev = execute_check("checker", tmp_path, timeout=60)
    assert ev is not None and ev.passed, ev.detail if ev else "no evidence"
    ok, _ = parse_testing_result(0, "FAIL: nope")
    assert ok is False


def test_missing_module_stays_unregistered(tmp_path):
    from skaldir import capability

    d = tmp_path / "generated_tools" / "broken"
    d.mkdir(parents=True)
    (d / "manifest.yaml").write_text(
        yaml.safe_dump({"type": "testing_tool", "status": "ready", "module": "ghost",
                        "validation": {"status": "validated"}}),
        encoding="utf-8",
    )
    assert capability.register_testing_tools(tmp_path) == []
