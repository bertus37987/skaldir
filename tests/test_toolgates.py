"""Gate runner: real subprocesses, offline, sandboxed when bwrap is available."""

import sys

from skaldir.gates import run_gates
from skaldir.tools import ToolSpec

CLEAN_MODULE = "def add(a, b):\n    return a + b\n"

EVIL_MODULE = "def run():\n    return eval('1 + 1')\n"

SECRET_MODULE = 'KEY = "sk-' + 'A1b2C3d4E5f6G7h8I9j0K1l2"\n'

BROKEN_MODULE = "import does_not_exist_xyz\n"

NONDET_MODULE = "import time\n\ndef now(dummy=1):\n    return time.time()\n"

SLOW_MODULE = "import time\n\ndef slow(x=1):\n    time.sleep(30)\n    return x\n"

TEST_CMD = [
    sys.executable,
    "-m",
    "pytest",
    "test_gatetool.py",
    "-q",
    "-p",
    "no:cacheprovider",
]


def _write(workdir, name: str, body: str):
    path = workdir / name
    path.write_text(body, encoding="utf-8")
    return path


def _check(report, gate: str):
    return next(check for check in report.checks if check.gate == gate)


class TestScanAndImport:
    def test_clean_module_passes_scan_and_import(self, tmp_path):
        _write(tmp_path, "gattool.py", CLEAN_MODULE)
        spec = ToolSpec(name="t", title="T", module="gattool")
        report = run_gates(spec, workdir=tmp_path)
        assert _check(report, "scan").passed
        assert _check(report, "import").passed
        assert report.passed

    def test_eval_blocks_the_gate(self, tmp_path):
        _write(tmp_path, "gamod.py", EVIL_MODULE)
        spec = ToolSpec(name="t", title="T", module="gamod")
        report = run_gates(spec, workdir=tmp_path)
        assert not _check(report, "scan").passed
        assert not report.passed

    def test_embedded_secret_blocks(self, tmp_path):
        _write(tmp_path, "gamod.py", SECRET_MODULE)
        spec = ToolSpec(name="t", title="T", module="gamod")
        report = run_gates(spec, workdir=tmp_path)
        assert not _check(report, "scan").passed

    def test_missing_source_fails_scan(self, tmp_path):
        spec = ToolSpec(name="t", title="T", module="nope_missing")
        report = run_gates(spec, workdir=tmp_path)
        assert not _check(report, "scan").passed

    def test_broken_import_fails(self, tmp_path):
        _write(tmp_path, "gabroken.py", BROKEN_MODULE)
        spec = ToolSpec(name="t", title="T", module="gabroken")
        report = run_gates(spec, workdir=tmp_path)
        assert _check(report, "scan").passed
        assert not _check(report, "import").passed


class TestTestGate:
    def test_passing_tests(self, tmp_path):
        _write(tmp_path, "gatetool.py", CLEAN_MODULE)
        _write(
            tmp_path,
            "test_gatetool.py",
            "from gatetool import add\n\ndef test_add():\n    assert add(1, 2) == 3\n",
        )
        spec = ToolSpec(
            name="t",
            title="T",
            module="gatetool",
            test_command=TEST_CMD,
        )
        report = run_gates(spec, workdir=tmp_path, timeout=60)
        assert _check(report, "test").passed

    def test_failing_tests(self, tmp_path):
        _write(tmp_path, "gatetool.py", CLEAN_MODULE)
        _write(
            tmp_path,
            "test_gatetool.py",
            "def test_bad():\n    assert 1 == 2\n",
        )
        spec = ToolSpec(
            name="t",
            title="T",
            module="gatetool",
            test_command=TEST_CMD,
        )
        report = run_gates(spec, workdir=tmp_path, timeout=60)
        assert not _check(report, "test").passed


class TestDeterminismGate:
    def test_deterministic_tool_passes(self, tmp_path):
        _write(tmp_path, "gatetool.py", CLEAN_MODULE)
        spec = ToolSpec(
            name="t",
            title="T",
            module="gatetool",
            entrypoint="add",
            sample_input={"a": 1, "b": 2},
        )
        report = run_gates(spec, workdir=tmp_path)
        check = _check(report, "determinism")
        assert check.passed
        assert "3" in check.detail

    def test_nondeterministic_tool_fails(self, tmp_path):
        _write(tmp_path, "gand.py", NONDET_MODULE)
        spec = ToolSpec(
            name="t",
            title="T",
            module="gand",
            entrypoint="now",
            sample_input={"dummy": 1},
        )
        report = run_gates(spec, workdir=tmp_path)
        assert not _check(report, "determinism").passed

    def test_timeout_fails_with_message(self, tmp_path):
        _write(tmp_path, "gaslow.py", SLOW_MODULE)
        spec = ToolSpec(
            name="t",
            title="T",
            module="gaslow",
            entrypoint="slow",
            sample_input={"x": 1},
        )
        report = run_gates(spec, workdir=tmp_path, timeout=2)
        check = _check(report, "determinism")
        assert not check.passed
        assert "timeout" in check.detail
