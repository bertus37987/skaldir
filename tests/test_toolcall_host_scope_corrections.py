"""Host-agent access rules: the guard refused legitimate work (user P0).

The trace showed the deciding component: `[voice·guard]` is only the *render* label of
`toolcall.ToolDecision.render()`. What actually decided was the **path classification**
(`_paths()` + `_in_workspace()`), which pulled redirect targets and plain arguments into one
flat list. `2>/dev/null` was therefore judged "writing into a foreign tree".

Skaldir is an explicit host agent: browsers, LibreOffice, user artefacts and system
diagnosis legitimately work outside the repository. That does not mean a free pass — the
acceptance below pins both sides.
"""

from __future__ import annotations

import pytest

from skaldir.toolcall import ToolIntent, judge_tool_call

WS = "/home/willy/Agentic_Projects/Skaldir_Agent"


def decide(command: str, **kw):
    return judge_tool_call(ToolIntent(
        tool="terminal", args={"command": command},
        workspace=WS, project_root=WS, **kw))


# 1. An allowed diagnosis with 2>/dev/null works. ---------------------------- #

def test_redirect_to_dev_null_is_not_a_foreign_write():
    """The exact user case: a LibreOffice headless conversion discarding stderr."""
    d = decide("soffice --headless --convert-to csv report.ods 2>/dev/null")
    assert d.classification is not judge_tool_call.__globals__["BLOCK"], d.reasons
    assert not d.checks.get("foreign_paths"), d.checks.get("foreign_paths")


@pytest.mark.parametrize("device", ["/dev/null", "/dev/stdout", "/dev/stderr"])
def test_device_targets_are_discards_not_writes(device):
    d = decide(f"soffice --convert-to csv r.ods 2>{device}")
    assert d.checks.get("managed_tmp_paths") in (None, [])
    assert d.checks.get("foreign_paths") == [], d.checks.get("foreign_paths")


# 2. My own temporary log file works. ---------------------------------------- #

def test_a_managed_temporary_log_is_the_agents_own_scratch_space():
    """Corrected: plain /tmp is NOT our own area, so this is a foreign write now.

    The earlier version of this test asserted `/tmp/lo.log` was managed. That was the
    wrong rule — `/tmp` holds other people's files. The owned scratch space is
    `/tmp/skaldir` and `<SKALDIR_HOME>/tmp`; writing into bare /tmp is a *choice* the
    model has to name, not a free pass.
    """
    d = decide("soffice --version >/tmp/lo.log")
    assert d.checks.get("managed_tmp_paths") == []
    assert d.checks.get("foreign_paths") == ["/tmp/lo.log"]


def test_the_owned_scratch_space_is_allowed(monkeypatch, tmp_path):
    home = tmp_path / "runtime"
    (home / "tmp").mkdir(parents=True)
    monkeypatch.setenv("SKALDIR_HOME", str(home))
    log = home / "tmp" / "lo.log"
    d = decide(f"soffice --version >{log}")
    assert d.checks.get("managed_tmp_paths") == [str(log.resolve())]
    assert not d.checks.get("foreign_paths")


def test_the_managed_tmp_area_honours_tmpdir(monkeypatch, tmp_path):
    """A TMPDIR inside the runtime is honoured — but only when it really exists.

    The earlier version asserted /tmp/skaldir-scope was managed; that directory was
    never created, which is exactly the gap the user named: the name alone proves no
    exclusive control.
    """
    # the override is only honoured inside the runtime home (or /tmp/skaldir): a
    # directory under bare /tmp is somebody else's, which is the whole point
    home = tmp_path / "runtime"
    (home / "tmp").mkdir(parents=True, mode=0o755)
    monkeypatch.setenv("SKALDIR_HOME", str(home))
    root = home / "tmp"
    monkeypatch.setenv("SKALDIR_TMPDIR", str(root))

    import skaldir.toolcall as tc

    log = root / "a.log"
    d = tc.judge_tool_call(tc.ToolIntent(
        tool="terminal", args={"command": f"soffice --version >{log}"},
        workspace=WS, project_root=WS))
    assert d.checks.get("managed_tmp_paths") == [str(log)]
    assert not d.checks.get("foreign_paths")


def test_a_tmpdir_that_does_not_exist_is_not_managed(monkeypatch, tmp_path):
    """A directory that was never created is a claim, not an owned root."""
    root = tmp_path / "never-created"
    monkeypatch.setenv("SKALDIR_TMPDIR", str(root))

    import skaldir.toolcall as tc

    log = root / "a.log"
    d = tc.judge_tool_call(tc.ToolIntent(
        tool="terminal", args={"command": f"soffice --version >{log}"},
        workspace=WS, project_root=WS))
    assert d.checks.get("managed_tmp_paths") == []
    assert d.checks.get("foreign_paths") == [str(log)]


def test_the_managed_tmp_is_a_target_prefix_not_a_substring():
    """/tmpfoo must not be treated as /tmp."""
    d = decide("echo x > /tmpfoo/leak.log")
    assert d.checks.get("managed_tmp_paths") == []
    assert d.checks.get("foreign_paths") == ["/tmpfoo/leak.log"]


# 3. An authorised ODS outside the repo is opened. --------------------------- #

def test_an_authorised_user_artefact_outside_the_repo_is_readable():
    """`soffice --calc /home/willy/AMD_latest_data.ods` — the file is an ARGUMENT."""
    d = decide("soffice --calc /home/willy/AMD_latest_data.ods")
    assert d.checks.get("foreign_paths") == [], d.checks.get("foreign_paths")
    assert d.classification is not judge_tool_call.__globals__["BLOCK"], d.reasons


def test_reading_a_host_diagnostic_file_is_normal_work():
    d = decide("cat /var/log/Xorg.0.log | tail -20")
    assert d.classification is not judge_tool_call.__globals__["BLOCK"], d.reasons


# 4. Overwriting a foreign file stays protected. ----------------------------- #

def test_overwriting_a_foreign_system_file_stays_blocked():
    d = decide("echo x > /etc/passwd")
    assert d.classification is judge_tool_call.__globals__["BLOCK"]
    assert d.checks.get("foreign_paths") == ["/etc/passwd"]


def test_a_readonly_flag_cannot_smuggle_a_write_past_the_fence():
    d = decide("echo x | tee -a /etc/hosts")
    assert d.classification is judge_tool_call.__globals__["BLOCK"]
    assert "/etc/hosts" in (d.checks.get("foreign_paths") or [])


def test_a_traversal_write_stays_blocked():
    d = judge_tool_call(ToolIntent(
        tool="write_file", args={"path": "../../elsewhere/secret.txt"},
        workspace=WS, project_root=WS))
    assert d.classification is judge_tool_call.__globals__["BLOCK"]
    assert d.checks.get("traversal_paths") == ["../../elsewhere/secret.txt"]


def test_the_destructive_checks_still_apply_in_the_managed_tmp_area():
    """Managed tmp is not a free pass: a destructive command still hits the danger checks."""
    d = decide("rm -rf /tmp/skaldir-scope/../others && echo x >/dev/null")
    # the rm itself must still be flagged by the destructive rules, not waved through
    assert d.checks.get("managed_tmp_paths") in (None, [])
    assert "destructive" in d.checks


# 5. Reversible allowed work is not interrupted. ----------------------------- #

def test_allowed_diagnosis_is_not_paused_or_paused_to_investigate():
    d = decide("soffice --version")
    assert d.classification is judge_tool_call.__globals__["ALLOW"], d.reasons


def test_a_batch_of_independent_diagnoses_stays_allowed():
    for command in (
        "uname -a",
        "soffice --version 2>/dev/null",
        "xdpyinfo -display :0 | grep dimensions",
        "python3 -c 'import sys; print(sys.version)'",
    ):
        d = decide(command)
        assert d.classification is judge_tool_call.__globals__["ALLOW"], (command, d.reasons)


# 6. Every refusal names source, access, rule and next step. ----------------- #

def test_every_refusal_names_what_the_user_must_act_on():
    d = decide("echo x > /etc/passwd")
    text = d.render()
    assert "[voice·guard]" in text, "the render label the user saw"
    assert "access affected: /etc/passwd" in text, text
    assert "rule:" in text, text
    assert "next step:" in text, text


def test_the_refusal_names_the_deciding_component_honestly():
    """`[voice·guard]` is the LABEL; the decision is the path classification.

    Calling it "the voice model" would misattribute the cause, so the render says
    which component decided.
    """
    d = decide("echo x > /etc/passwd")
    text = d.render()
    assert "path classification" in text or "path rule" in text, text
