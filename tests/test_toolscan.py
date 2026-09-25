"""Tool-code scan: high findings block, medium/low inform."""

from skaldir.toolscan import (
    SEV_HIGH,
    SEV_LOW,
    SEV_MEDIUM,
    has_blocking,
    scan_tool_file,
    scan_tool_source,
)

FAKE_KEY = "sk-" + "A1b2C3d4E5f6G7h8I9j0K1l2"


def _severities(findings):
    return {finding.severity for finding in findings}


class TestScan:
    def test_clean_source_has_no_findings(self):
        assert scan_tool_source("def add(a, b):\n    return a + b\n") == []

    def test_eval_is_high_and_blocking(self):
        findings = scan_tool_source("x = eval('1 + 1')\n")
        assert SEV_HIGH in _severities(findings)
        assert has_blocking(findings)

    def test_exec_and_dunder_import(self):
        findings = scan_tool_source("exec('pass')\nmod = __import__('os')\n")
        assert has_blocking(findings)

    def test_subprocess_shell_is_high(self):
        findings = scan_tool_source("subprocess.run(cmd, shell=True)\n")
        assert has_blocking(findings)

    def test_network_import_is_medium_not_blocking(self):
        findings = scan_tool_source("import requests\n")
        assert _severities(findings) == {SEV_MEDIUM}
        assert not has_blocking(findings)

    def test_socket_is_medium(self):
        findings = scan_tool_source("import socket\n")
        assert SEV_MEDIUM in _severities(findings)

    def test_embedded_secret_blocks(self):
        findings = scan_tool_source(f'KEY = "{FAKE_KEY}"\n')
        assert any(finding.rule == "secret" for finding in findings)
        assert has_blocking(findings)

    def test_file_write_is_low_only(self):
        findings = scan_tool_source('open("out.txt", "w")\n')
        assert _severities(findings) == {SEV_LOW}
        assert not has_blocking(findings)

    def test_line_numbers_reported(self):
        findings = scan_tool_source("a = 1\nb = 2\nx = eval('1')\n")
        assert any(finding.line == 3 for finding in findings)

    def test_scan_file(self, tmp_path):
        path = tmp_path / "tool.py"
        path.write_text("y = eval('2')\n", encoding="utf-8")
        assert has_blocking(scan_tool_file(path))
