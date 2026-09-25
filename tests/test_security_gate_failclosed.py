"""Block 1 / Defekt 1 — `gate_security` muss fail-closed sein.

Rot-first-Beleg (vor dem Fix, Ausgabe des echten Laufs)::

    $ .venv/bin/python -m pytest tests/test_security_gate_failclosed.py -q
    FFFF  (4 failed)
    - scanner crash -> findings == []  => Secret-Diff passiert still
    - ein Secret erzeugte ZWEI BLOCKER-Findings (generic_key + openai)

Vertrag nach dem Fix:

* Scanner-**Crash** (Exception/ImportError) => BLOCKER-Finding, das Gate
  schlägt durch (fail-closed).
* Sauberer Scan **ohne Treffer** => KEIN Finding (darf nie blocken — sonst
  blockiert jedes fehlende optionale Modul die Arbeit).
* Ein Secret => genau EIN Finding (Dedupe überlappender Treffer).
"""

from pathlib import Path

import pytest

from skaldir.completion import BLOCKER, GateRunner, RepoFacts

FAKE_KEY = "sk-" + "A1b2C3d4E5f6G7h8I9j0K1l2"  # musterförmig, kein echtes Secret
SECRET_DIFF = f'+api_key = "{FAKE_KEY}"\n'


def _facts(diff_text: str) -> RepoFacts:
    return RepoFacts(root=Path("."), diff_text=diff_text)


def _runner() -> GateRunner:
    return GateRunner(repo_root=".")


class TestScannerCrashFailsClosed:
    def test_scanner_exception_becomes_a_blocking_finding(self, monkeypatch):
        """Ein kaputter Scanner darf einen Secret-Diff nicht still durchlassen."""
        from skaldir import secretscan

        def _boom(_text):
            raise RuntimeError("scanner exploded")

        monkeypatch.setattr(secretscan, "redact", _boom)
        findings = _runner().gate_security(_facts(SECRET_DIFF))

        assert findings, "scanner crash must not yield an empty finding list"
        assert any(
            f.gate == "security" and f.severity == BLOCKER for f in findings
        ), f"scanner crash must be a BLOCKER, got {findings!r}"
        # Der Befund nennt den Grund — nicht „kein Secret gefunden“.
        assert any(
            "scanner" in f.detail.lower() or "scan" in f.detail.lower() for f in findings
        ), f"finding must name the scanner failure, got {[f.detail for f in findings]!r}"

    def test_unavailable_scanner_module_fails_closed(self, monkeypatch):
        """Auch ein nicht importierbarer Scanner ist ein offener, kein grüner Zustand."""
        import builtins

        real_import = builtins.__import__

        def _guarded(name, *args, **kwargs):
            if name.endswith("secretscan") or name == "godsv.secretscan":
                raise ImportError("secretscan missing")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _guarded)
        findings = _runner().gate_security(_facts(SECRET_DIFF))
        assert any(f.severity == BLOCKER for f in findings)


class TestCleanScanNeverBlocks:
    def test_empty_diff_has_no_security_finding(self):
        assert _runner().gate_security(_facts("")) == []

    @pytest.mark.parametrize(
        "line",
        [
            "+# secret://openrouter-main is a handle, never a value\n",
            "+# the dashboard token must be sent as a bearer header\n",
            "+diff --git a/docs/token-notes.md b/docs/token-notes.md\n",
        ],
    )
    def test_prose_and_handles_are_not_findings(self, line):
        """Gegenprobe gegen Überhärten: Prosa/Handles erzeugen KEIN Verdikt."""
        assert _runner().gate_security(_facts(line)) == []


class TestSecretsAreFoundOnce:
    def test_secret_in_diff_is_a_blocker(self):
        findings = _runner().gate_security(_facts(SECRET_DIFF))
        assert [f.gate for f in findings] == ["security"]
        assert findings[0].severity == BLOCKER

    def test_overlapping_patterns_report_one_secret_once(self):
        """`api_key = "sk-…"` matcht generic_key UND openai — ein Secret, ein Befund."""
        findings = _runner().gate_security(_facts(SECRET_DIFF))
        assert len(findings) == 1, f"duplicate findings for one secret: {findings!r}"

    def test_scan_dedupes_overlapping_matches(self):
        from skaldir.secretscan import scan

        findings = scan(SECRET_DIFF)
        assert len(findings) == 1, [f.kind for f in findings]

    def test_distinct_secrets_are_still_reported_separately(self):
        from skaldir.secretscan import scan

        text = f"{FAKE_KEY} ghp_" + "b" * 36
        findings = scan(text)
        kinds = [f.kind for f in findings]
        assert len(findings) == len(set(kinds)) >= 2, kinds
