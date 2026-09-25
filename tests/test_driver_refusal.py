"""A missing driver must be a **structured refusal**, never a verdict.

The browser half of the adversarial suite imports `playwright` lazily, which is correct — the core
CLI must answer `--version` without a browser. It was, however, declared nowhere, and that turned a
missing *package* into a **false product defect** on a clean install of the published artifact:

* ``skaldir adversarial selftest`` -> exit 2, ``"the suite does not distinguish the targets"``,
  ``detectors fired: 0/8``, 9 false positives. The message told the user a detector that never
  fired is decoration and to fix the case or the oracle — for a `pip install` that never happened.
* ``skaldir adversarial run --canary broken`` -> exit 3, but the reason was a raw
  ``ModuleNotFoundError: No module named 'playwright'`` in the per-case text and the hint was
  *"make it observable"*. Nothing named the package, let alone the extra.
* ``skaldir adversarial run --with-api`` -> exit 7 internal **with a traceback**, because the API
  child process died on `import fastapi`.

What this file pins, all three answers:

1. the refusal is *structured* — exit 3, the driver, the extra, and the exact install commands, as
   data (``--json``) and as one actionable line;
2. it is **not waivable** — ``--allow-unknown`` exists to accept cases that ran and could not be
   judged, not to accept a suite that never started. A blocked run exits 3 whatever the waiter asks;
3. it blames nobody — no detector, no oracle and no case is reported defective.

The driver is made to look absent by patching the probe, not by uninstalling anything: these tests
must answer the same way on a developer machine that *has* a browser.
"""

from __future__ import annotations

import json

import pytest

from skaldir import adversarial as A
from skaldir import adversarial_cli as CLI

#: The probe's answer on a clean install: exactly the shape `_browser_probe` returns when the
#: import fails (see `browser_driver_status`).
NO_BROWSER = {
    "feature": "the browser adversarial suite",
    "driver": "playwright",
    "extra": "browser",
    "install_commands": [
        "pip install 'skaldir-agent[browser]'",
        "playwright install chromium",
    ],
    "available": False,
    "reason": "driver_missing",
    "missing": "playwright",
    "detail": "ModuleNotFoundError: No module named 'playwright'",
}


@pytest.fixture
def no_browser(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Make every driver probe answer "not here", as a clean install without the extra does."""
    monkeypatch.setattr(A, "browser_driver_status", lambda **kwargs: dict(NO_BROWSER))
    return NO_BROWSER


def _plan() -> A.SuitePlan:
    return A.plan_suite(surfaces=A.offered_surfaces(ui_url="http://127.0.0.1:1/"))


def test_a_run_without_the_driver_is_blocked_not_run(no_browser):
    """No case is attempted, every case is `unknown`, and the report carries the refusal as data."""
    report = A.run_suite(_plan())

    assert report.verdict == "UNKNOWN", "a suite that cannot start does not get a FAIL either"
    assert report.blocked, "the refusal must travel with the report, not only in the text"
    assert report.blocked["missing"] == "playwright"
    assert report.blocked["extra"] == "browser"
    assert set(report.blocked["install_commands"]) == {
        "pip install 'skaldir-agent[browser]'",
        "playwright install chromium",
    }
    assert report.results, "the plan's cases are still reported — nothing is silently dropped"
    assert {result.outcome for result in report.results} == {"unknown"}
    assert not report.defects, "a missing package must not be reported as a defect of a detector"


def test_the_refusal_names_the_exact_remediation_in_one_line(no_browser):
    report = A.run_suite(_plan())
    refusal = A._refusal_line(report.blocked)
    assert "playwright" in refusal
    assert "pip install 'skaldir-agent[browser]'" in refusal
    assert "playwright install chromium" in refusal
    assert "\n" not in refusal, "a payload line must survive a one-line consumer"
    per_case = {result.reason for result in report.results}
    assert not any(reason.startswith("could not observe the target") for reason in per_case), (
        "the per-case reason must be the refusal, not a raw import error dressed up as an oracle"
    )


def test_a_blocked_run_is_never_waivable(no_browser):
    """`--allow-unknown` waives unjudged cases, never an unstarted suite."""
    report = A.run_suite(_plan())
    assert A.exit_code_for(report, block_release=False) == A.EXIT_BLOCKED
    assert A.exit_code_for(report, block_release=False, allow_unknown=True) == A.EXIT_BLOCKED, (
        "waiving a run that never started is the silent skip this refusal exists to prevent"
    )
    gate = A.release_gate_payload(report, block_release=False, allow_unknown=True)
    assert gate["waived_unknown"] is False
    assert gate["exit_code"] == A.EXIT_BLOCKED


def test_selftest_refuses_instead_of_blaming_the_detectors(no_browser):
    """The sharpest form of the defect: exit 2 said 'the detectors are broken' for no package."""
    check = A.selftest()

    assert check.ok is False
    assert check.exit_code == A.EXIT_BLOCKED
    assert check.blocked.get("missing") == "playwright"
    assert check.detected == (), "nothing was attempted, so no detector was measured"
    assert check.false_positives == (), "the fixed canary was never run, so it cannot 'not pass'"
    assert "playwright" in check.detail
    assert "pip install 'skaldir-agent[browser]'" in check.detail


def test_a_missing_api_driver_is_refused_before_a_child_process_is_spawned(monkeypatch):
    """`--with-api` used to exit 7 internal with a traceback from the child's ImportError."""
    monkeypatch.setattr(
        A,
        "api_driver_status",
        lambda **kwargs: {
            "feature": "the real Skaldir API as a target (--with-api)",
            "driver": "fastapi+uvicorn",
            "extra": "api",
            "install_commands": ["pip install 'skaldir-agent[api]'"],
            "available": False,
            "reason": "driver_missing",
            "missing": "fastapi/uvicorn",
            "detail": "ModuleNotFoundError: No module named 'fastapi'",
        },
    )
    with pytest.raises(A.DriverUnavailable) as caught:
        A.start_skaldir_api()
    refusal = caught.value.as_dict()
    assert refusal["exit_code"] == A.EXIT_BLOCKED
    assert refusal["install_commands"] == ["pip install 'skaldir-agent[api]'"]


# --------------------------------------------------------------------------- #
# the same three answers through the product CLI                                 #
# --------------------------------------------------------------------------- #


def _streams(capsys: pytest.CaptureFixture[str]) -> tuple[str, str]:
    """The two streams, read once (``readouterr`` clears). The refusal is one line; the payload
    is data — a consumer reads one of the two, so the assertions read both."""
    captured = capsys.readouterr()
    return captured.out, captured.err


def test_cli_run_answers_exit_3_with_the_remediation(no_browser, capsys):
    code = CLI.main(["run", "--ui-url", "http://127.0.0.1:1/", "--json"])
    out, err = _streams(capsys)
    text = f"{out}\n{err}"

    assert code == A.EXIT_BLOCKED
    assert "playwright" in text
    assert "pip install 'skaldir-agent[browser]'" in text
    assert "playwright install chromium" in text
    assert "Traceback" not in text
    payload = json.loads(out)
    assert payload["verdict"] == "UNKNOWN", "a suite that never started is not a FAIL either"
    assert payload["blocked"]["extra"] == "browser"
    assert {result["outcome"] for result in payload["cases"]} == {"unknown"}
    assert payload["defect_classes"] == [], "no detector may be blamed for a missing package"


def test_cli_selftest_answers_exit_3_with_the_remediation(no_browser, capsys):
    code = CLI.main(["selftest", "--json"])
    out, err = _streams(capsys)
    text = f"{out}\n{err}"

    assert code == A.EXIT_BLOCKED
    assert "playwright" in text
    assert "pip install 'skaldir-agent[browser]'" in text
    assert "playwright install chromium" in text
    payload = json.loads(out)
    assert payload["blocked"]["install_commands"] == [
        "pip install 'skaldir-agent[browser]'",
        "playwright install chromium",
    ]
    assert payload["missed"], "the cases are still named — nothing is silently dropped"


def test_cli_run_with_api_answers_exit_3_when_fastapi_is_missing(no_browser, monkeypatch, capsys):
    monkeypatch.setattr(
        A,
        "api_driver_status",
        lambda **kwargs: {
            "feature": "the real Skaldir API as a target (--with-api)",
            "driver": "fastapi+uvicorn",
            "extra": "api",
            "install_commands": ["pip install 'skaldir-agent[api]'"],
            "available": False,
            "reason": "driver_missing",
            "missing": "fastapi/uvicorn",
            "detail": "ModuleNotFoundError: No module named 'fastapi'",
        },
    )
    code = CLI.main(["run", "--with-api", "--json"])
    out, err = _streams(capsys)
    text = f"{out}\n{err}"

    assert code == A.EXIT_BLOCKED, "an absent extra is a setup step, not exit 7 internal"
    assert "fastapi" in text
    assert "pip install 'skaldir-agent[api]'" in text
    assert "Traceback" not in text
