"""Strukturierter Ergebnisvertrag für Testing-Tools (Audit P0, 13.09.).

Der belegte Fehler: Exit 0 mit ``FAIL: check did not PASS`` wurde als
``passed`` gewertet, weil "PASS" als Substring vorkam. Der Vertrag ist jetzt:
Exit 0 UND eine explizite PASS-Verdikt-Zeile; FAIL/ERROR gewinnt immer;
kein Verdikt => fail-closed."""

from skaldir.gates import parse_testing_result


def test_explicit_fail_at_exit_zero_is_not_a_pass():
    ok, reason = parse_testing_result(0, "FAIL: check did not PASS\n")
    assert ok is False
    assert "FAIL" in reason


def test_explicit_pass_verdict_passes():
    ok, reason = parse_testing_result(0, "PASS: doubled 21 -> 42\n")
    assert ok is True and reason == ""


def test_missing_verdict_fails_closed():
    ok, _ = parse_testing_result(0, "everything looks fine\n")
    assert ok is False


def test_nonzero_exit_never_passes():
    ok, reason = parse_testing_result(1, "PASS\n")
    assert ok is False and "exit 1" in reason


def test_contradictory_verdicts_fail():
    ok, _ = parse_testing_result(0, "PASS\nFAIL: later check failed\n")
    assert ok is False


def test_runner_uses_the_contract(monkeypatch, tmp_path):
    """Der echte _testing_tool_runner muss denselben Vertrag anwenden."""
    from skaldir import capability

    module = tmp_path / "negative_check.py"
    module.write_text(
        "import sys\nprint('FAIL: check did not PASS')\nsys.exit(0)\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        "godsv.gates._run_checked",
        lambda *a, **k: (0, "FAIL: check did not PASS\n", True),
    )
    runner = capability._testing_tool_runner(tmp_path, "negative_check", module)
    evidence = runner(str(module))
    assert evidence.status == "failed" and evidence.ok is False
