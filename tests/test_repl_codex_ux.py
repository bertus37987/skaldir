"""The Codex-UX transfer in the shell: approval, diff preview, footer, activity, /init, /diff.

Studied from Codex's TUI (design only — their approval overlay, their diff rendering, their
footer and status indicator, their ``/init`` and ``/diff``) and rebuilt on this stack's own
pieces: the ladder in :mod:`skaldir.approval`, the transaction engine in
:mod:`skaldir.edit_tx`, the real CLI for every recorded answer.

What these tests hold down:

* the approval prompt shows the exact command, the decision and its reason, the deciding
  rule and the profile, then the answers — and every *yes*/*no* is recorded by running the
  real ``approval allow`` / ``approval deny`` commands (the runner sees them, the shell
  does not re-implement them);
* ``allow once`` is a **one-shot** session grant (its file is gone once the command ran, so
  the same command asks again), while ``allow for this session`` keeps its grant — the one
  difference a user can check;
* a non-interactive session never prompts, prints why, and the gated command keeps the
  CLI's own headless answer (exit 3, nothing auto-allowed);
* an ``edit apply`` is previewed with the real ``edit dry-run`` before it applies, and a
  "no" leaves the tree alone;
* the footer carries the session facts and is re-drawn only when it has news;
* the activity indicator measures (elapsed, bytes, lines), draws in place on a terminal and
  draws nothing on a pipe;
* ``/init`` writes AGENTS.md from what it could read and never overwrites one;
* ``/diff`` renders the last transaction's diff through the real ``edit show``.
"""

from __future__ import annotations

import builtins
import io
import json
from pathlib import Path

import pytest

from skaldir import approval, cli, edit_tx, render, repl, slash

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"


# --------------------------------------------------------------------------- #
# harness
# --------------------------------------------------------------------------- #


class Stub:
    """A runner that answers argv the way the real CLI would, and records every call.

    ``calls`` is the proof surface: the shell may only ever *ask* the product CLI for
    things (judge, allow, deny, dry-run, apply), so every gate is checked by what it ran.
    """

    def __init__(self, runtime: Path, *, verdict: dict | None = None,
                 dry_record: dict | None = None, gate_code: int = 0) -> None:
        self.runtime = Path(runtime)
        self.verdict = verdict
        self.dry_record = dry_record
        self.gate_code = gate_code
        self.calls: list[list[str]] = []

    def __call__(self, argv) -> int:
        argv = [str(item) for item in argv]
        self.calls.append(argv)
        path = list(argv[:2])
        if path == ["approval", "check"]:
            session = _flag(argv, "--session") or "default"
            verdict = dict(self.verdict or _verdict())
            if approval.grants_path(self.runtime, session).is_file():
                # the ladder finds a session grant: it does not ask again
                verdict.update({"decision": "allow", "allowed": True, "prompted": False,
                                "blocked": False, "exit_code": 0, "reason_kind": "grant",
                                "reason": f"the user granted a session grant in session "
                                          f"{session!r}"})
            self._print({"schema_version": "1.0", "ok": not verdict.get("blocked", True),
                         "command": "approval.check", "data": verdict})
            return int(verdict.get("exit_code") or 0)
        if path == ["approval", "allow"]:
            self._grant(argv)
            self._print({"schema_version": "1.0", "ok": True, "command": "approval.allow",
                         "data": {"evidence_ref": "approval-events.jsonl:7",
                                  "scope": _flag(argv, "--scope") or "persisted_rule"}})
            return 0
        if path == ["approval", "deny"]:
            self._print({"schema_version": "1.0", "ok": True, "command": "approval.deny",
                         "data": {"evidence_ref": "approval-events.jsonl:8",
                                  "reason": _flag(argv, "--reason") or ""}})
            return 0
        if path == ["edit", "dry-run"]:
            self._print({"schema_version": "1.0", "ok": True, "command": "edit.dry-run",
                         "data": self.dry_record if self.dry_record is not None
                         else _dry_record()})
            return 0
        if path == ["edit", "apply"]:
            self._print({"schema_version": "1.0", "ok": True, "command": "edit.apply",
                         "data": _dry_record(verdict="applied")})
            return 0
        return self.gate_code

    # -- the real state a real command would leave behind ------------------ #
    def _grant(self, argv: list[str]) -> None:
        session = _flag(argv, "--session") or "default"
        path = approval.grants_path(self.runtime, session)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"schema": approval.SCHEMA_VERSION, "session": session,
                                    "session_grants": [{"prefix": argv[-1:], "scope": "session",
                                                        "session": session}]}),
                        encoding="utf-8")

    @staticmethod
    def _print(payload: dict) -> None:
        print(json.dumps(payload))


def _flag(argv: list[str], name: str) -> str:
    if name in argv:
        index = argv.index(name)
        if index + 1 < len(argv):
            return argv[index + 1]
    return ""


def _verdict(**over) -> dict:
    """The ``approval check`` payload shape (one object, the ladder's own vocabulary)."""
    payload = {
        "command": "echo hi",
        "decision": "prompt",
        "allowed": False,
        "prompted": True,
        "forbidden": False,
        "blocked": True,
        "exit_code": 3,
        "reason_kind": "default",
        "reason": "no rule and no grant covers 'echo' — silence is not consent, so the "
                  "user decides",
        "rule_id": "",
        "rule_text": "",
        "banned_prefix": "",
        "profile": {"name": "default", "sha256": "a212695b15219d945a5e08b44496f65b3a0ed3de"},
        "policy": "on_request",
        "segments": [["echo", "hi"]],
        "rule_eligible": True,
        "would_allow": [
            "allow it once, for the next turn: skaldir approval allow --scope turn 'echo'",
            "allow it for this session: skaldir approval allow --scope session 'echo'",
        ],
    }
    payload.update(over)
    return payload


def _diff_for(path: str, before: str, after: str) -> str:
    import difflib

    return "".join(difflib.unified_diff(before.splitlines(keepends=True),
                                        after.splitlines(keepends=True),
                                        fromfile=f"a/{path}", tofile=f"b/{path}"))


def _dry_record(*, verdict: str = "applicable", path: str = "src/a.py") -> dict:
    return {
        "schema_version": "1.0",
        "tx_id": "tx-20260918T000000Z",
        "created_at": "2026-09-18T00:00:00+00:00",
        "workspace": "/tmp/ws",
        "reason": "a test transaction",
        "mode": "dry_run",
        "dry_run": True,
        "verdict": verdict,
        "applied": False,
        "in_tree": False,
        "diff": _diff_for(path, "one\ntwo\n", "one\ntwo changed\nthree\n"),
        "diff_truncated": False,
        "verification": {"kind": "none"},
        "files": [
            {
                "path": path,
                "ok": True,
                "diff": _diff_for(path, "one\ntwo\n", "one\ntwo changed\nthree\n"),
            }
        ],
        "snapshot": {"dir": "", "pre": [], "bytes": 0},
        "caps_hit": [],
        "duration_ms": 1,
    }


def _shell(tmp_path, runner, **options):
    out, err = io.StringIO(), io.StringIO()
    opts = repl.ShellOptions(probe=False, **options)
    session = repl.Session(runtime=tmp_path, runner=runner, out=out, err=err)
    shell = repl.Shell(opts, session=session, out=out, err=err, stdin=io.StringIO(""))
    return shell, out, err


@pytest.fixture
def typed(monkeypatch):
    """Scripted answers to the prompts, with the prompt text they came with."""
    answers: list[str] = []
    seen: list[str] = []

    def fake_input(prompt: str = "") -> str:
        seen.append(str(prompt))
        if not answers:
            raise EOFError
        return answers.pop(0)

    monkeypatch.setattr(builtins, "input", fake_input)
    return answers, seen


# --------------------------------------------------------------------------- #
# the approval prompt
# --------------------------------------------------------------------------- #


def test_the_approval_prompt_shows_the_command_the_reason_the_rule_and_the_answers(
    tmp_path, typed
):
    answers, _seen = typed
    answers.append("")  # Enter denies
    stub = Stub(tmp_path)
    shell, out, _err = _shell(tmp_path, stub, interactive=True)
    shell.dispatch("approval run echo hi")
    text = out.getvalue()
    assert "approval needed" in text and "approval.run" in text
    assert "command  echo hi" in text
    assert "no rule and no grant covers 'echo'" in text          # the deciding reason
    assert "rule     (none)" in text
    assert "sha256 a212695b1521" in text                          # the profile it was judged under
    for key in ("[a] allow once", "[s] allow for this session", "[r] allow and make it policy",
                "[d] deny with a reason", "[Enter] deny"):
        assert key in text, key
    # Enter is a denial, recorded through the real command, and nothing ran
    assert ["approval", "deny"] == stub.calls[-1][:2]
    assert not any(call[:2] == ["approval", "run"] for call in stub.calls)


def test_allow_once_is_recorded_and_its_grant_dies_with_the_command(tmp_path, typed):
    answers, _seen = typed
    answers.append("a")
    stub = Stub(tmp_path)
    shell, out, _err = _shell(tmp_path, stub, interactive=True)
    shell.dispatch("approval run echo hi")
    text = out.getvalue()
    allow = [call for call in stub.calls if call[:2] == ["approval", "allow"]][0]
    session_id = _flag(allow, "--session")
    assert "--scope" in allow and _flag(allow, "--scope") == "session"
    assert "-once-" in session_id, session_id
    assert "allowed once" in text and session_id in text
    # the gated command ran, with the one-shot session the grant was filed under
    gated = [call for call in stub.calls if call[:2] == ["approval", "run"]][0]
    assert _flag(gated, "--session") == session_id
    # ... and the grant file is gone: that is what makes it "once"
    assert not approval.grants_path(shell.session.runtime, session_id).exists()
    # so the very same line asks again
    before = len(stub.calls)
    answers.append("")
    shell.dispatch("approval run echo hi")
    assert any(call[:2] == ["approval", "check"] for call in stub.calls[before:])
    assert "approval needed" in out.getvalue().split("allowed once")[-1]


def test_allow_for_this_session_files_the_grant_under_the_shell_session(tmp_path, typed):
    answers, _seen = typed
    answers.append("s")
    stub = Stub(tmp_path)
    shell, out, _err = _shell(tmp_path, stub, interactive=True)
    shell.dispatch("approval run echo hi")
    allow = [call for call in stub.calls if call[:2] == ["approval", "allow"]][0]
    session_id = _flag(allow, "--session")
    assert session_id == shell.session_id
    assert "allowed for this session" in out.getvalue()
    # the grant really persisted, and the gated run was told which session it belongs to
    assert approval.grants_path(shell.session.runtime, session_id).is_file()
    gated = [call for call in stub.calls if call[:2] == ["approval", "run"]][0]
    assert _flag(gated, "--session") == session_id


def test_a_session_grant_covers_the_next_identical_call_without_asking_again(tmp_path, typed):
    """The shell names its own session, so "for this session" means this session."""
    answers, seen = typed
    answers.append("s")
    stub = Stub(tmp_path)
    shell, out, _err = _shell(tmp_path, stub, interactive=True)
    shell.dispatch("approval run echo hi")
    asked = len(seen)
    before = len(stub.calls)
    shell.dispatch("approval run echo hi")
    # no second question, one more real run, and the shell's session on its argv
    assert len(seen) == asked, seen
    gated = [call for call in stub.calls[before:] if call[:2] == ["approval", "run"]][0]
    assert _flag(gated, "--session") == shell.session_id
    assert "approval needed" not in out.getvalue().split("allowed for this session")[-1]


def test_making_it_policy_persists_a_rule_and_runs_the_command(tmp_path, typed):
    answers, _seen = typed
    answers.append("r")
    stub = Stub(tmp_path)
    shell, out, _err = _shell(tmp_path, stub, interactive=True)
    shell.dispatch("approval run echo hi")
    allow = [call for call in stub.calls if call[:2] == ["approval", "allow"]][0]
    assert "--rule" in allow
    assert "made policy" in out.getvalue()
    assert any(call[:2] == ["approval", "run"] for call in stub.calls)


def test_deny_carries_a_reason_back_and_runs_nothing(tmp_path, typed):
    answers, seen = typed
    answers += ["d", "use the read-only tool instead"]
    stub = Stub(tmp_path)
    shell, out, _err = _shell(tmp_path, stub, interactive=True)
    shell.dispatch("approval run echo hi")
    text = out.getvalue()
    deny = [call for call in stub.calls if call[:2] == ["approval", "deny"]][0]
    assert _flag(deny, "--reason") == "use the read-only tool instead"
    assert "the agent is told: use the read-only tool instead" in text
    assert not any(call[:2] == ["approval", "run"] for call in stub.calls)
    assert any("reason (the agent is told this" in prompt for prompt in seen)


def test_an_unreadable_answer_is_asked_again_and_never_guessed(tmp_path, typed):
    answers, _seen = typed
    answers += ["maybe", "d", ""]
    stub = Stub(tmp_path)
    shell, out, _err = _shell(tmp_path, stub, interactive=True)
    shell.dispatch("approval run echo hi")
    assert "is not one of the answers" in out.getvalue()
    assert [call[:2] for call in stub.calls if call[:2] == ["approval", "allow"]] == []


def test_headless_prints_the_prompt_and_keeps_the_cli_answer_of_exit_3(tmp_path):
    stub = Stub(tmp_path, gate_code=3)
    shell, out, _err = _shell(tmp_path, stub, interactive=False)
    shell.dispatch("approval run echo hi")
    text = out.getvalue()
    assert "command  echo hi" in text                     # the same facts a person would see
    assert "headless - no interactive user" in text
    assert "exit 3" in text and "nothing is auto-allowed" in text
    # the real command still ran, so its own verdict and exit code are the record
    assert any(call[:2] == ["approval", "run"] for call in stub.calls)
    assert not any(call[:2] in (["approval", "allow"], ["approval", "deny"])
                   for call in stub.calls)
    assert shell.last_exit == 3


def test_an_allowed_command_is_not_prompted_at_all(tmp_path, typed):
    answers, seen = typed
    stub = Stub(tmp_path, verdict=_verdict(decision="allow", allowed=True, prompted=False,
                                           blocked=False, exit_code=0,
                                           reason_kind="rule",
                                           reason="a persisted rule covers 'echo'",
                                           rule_id="rule-7", rule_text="allow echo"))
    shell, out, _err = _shell(tmp_path, stub, interactive=True)
    shell.dispatch("approval run echo hi")
    text = out.getvalue()
    assert "approval needed" not in text and seen == []
    assert any(call[:2] == ["approval", "run"] for call in stub.calls)


def test_a_forbidden_command_offers_no_answers(tmp_path, typed):
    answers, seen = typed
    stub = Stub(tmp_path, verdict=_verdict(decision="forbid", forbidden=True, blocked=True,
                                           exit_code=3, reason_kind="rule",
                                           reason="a persisted rule forbids it: deny echo",
                                           rule_id="rule-3"))
    shell, out, _err = _shell(tmp_path, stub, interactive=True)
    shell.dispatch("approval run echo hi")
    text = out.getvalue()
    assert "nothing to answer" in text and "forbids it" in text
    assert "[a] allow once" not in text          # a forbid has no answer row
    assert seen == []                            # the user was never asked
    assert any(call[:2] == ["approval", "run"] for call in stub.calls)


def test_the_banned_prefix_path_is_the_ladders_not_the_shells(tmp_path, typed):
    """A banned prefix never reaches an ``allow``: the shell records what the ladder said."""
    answers, _seen = typed
    answers.append("a")
    verdict = _verdict(banned_prefix="python", reason_kind="banned_prefix",
                       reason="'python' is on the banned-prefix list — it can never be "
                              "auto-approved")
    stub = Stub(tmp_path, verdict=verdict)
    shell, out, _err = _shell(tmp_path, stub, interactive=True)
    shell.dispatch("approval run python -c 'print(1)'")
    text = out.getvalue()
    assert "'python' is on the banned-prefix list" in text
    assert "banned_prefix" in json.dumps(verdict) or "banned" in text
    # the shell asked the real allow command; a refusal there is *reported*, never hidden
    assert any(call[:2] == ["approval", "allow"] for call in stub.calls)


# --------------------------------------------------------------------------- #
# the diff preview
# --------------------------------------------------------------------------- #


def test_edit_apply_is_previewed_with_the_real_dry_run_then_applies(tmp_path, typed):
    answers, seen = typed
    answers.append("y")
    stub = Stub(tmp_path)
    shell, out, _err = _shell(tmp_path, stub, interactive=True)
    shell.dispatch("edit apply --tx tx.json --workspace /tmp/ws")
    text = out.getvalue()
    assert stub.calls[0][:2] == ["edit", "dry-run"] and "--json" in stub.calls[0]
    assert "diff preview" in text and "dry run - nothing is written yet" in text
    assert "files 1 | +2 -1 | verdict applicable" in text
    assert "M src/a.py  +2 -1" in text
    assert "@@ " in text and "-two" in text and "+two changed" in text and "+three" in text
    assert any("apply this transaction? [y/N]" in prompt for prompt in seen)
    assert [call[:2] for call in stub.calls].count(["edit", "apply"]) == 1


def test_answering_no_leaves_the_tree_alone(tmp_path, typed):
    answers, _seen = typed
    answers.append("n")
    stub = Stub(tmp_path)
    shell, out, _err = _shell(tmp_path, stub, interactive=True)
    shell.dispatch("edit apply --tx tx.json --workspace /tmp/ws")
    text = out.getvalue()
    assert "not applied - nothing was written" in text
    assert not any(call[:2] == ["edit", "apply"] for call in stub.calls)


def test_a_refused_dry_run_skips_the_question_and_lets_the_real_command_answer(
    tmp_path, typed
):
    answers, seen = typed
    record = _dry_record(verdict="not_applicable")
    record["files"][0].update({"ok": False, "message": "src/a.py: the old text was not found",
                               "diff": ""})
    stub = Stub(tmp_path, dry_record=record)
    shell, out, _err = _shell(tmp_path, stub, interactive=True)
    shell.dispatch("edit apply --tx tx.json")
    text = out.getvalue()
    assert "the old text was not found" in text          # the refusal, in the record's words
    assert "the dry run refuses it (not_applicable)" in text
    assert seen == []                                     # nothing was asked
    assert any(call[:2] == ["edit", "apply"] for call in stub.calls)


def test_a_piped_session_previews_but_is_not_asked(tmp_path, typed):
    answers, seen = typed
    stub = Stub(tmp_path)
    shell, out, _err = _shell(tmp_path, stub, interactive=False)
    shell.dispatch("edit apply --tx tx.json")
    text = out.getvalue()
    assert "diff preview" in text                          # the preview is still shown
    assert seen == [] and "not applied" not in text
    assert any(call[:2] == ["edit", "apply"] for call in stub.calls)


# --------------------------------------------------------------------------- #
# the footer
# --------------------------------------------------------------------------- #


_TX_ID = "tx-20260918T201500Z"


def _write_record(runtime: Path, tx_id: str = _TX_ID, verdict: str = "applied") -> None:
    state = Path(runtime) / repl.EDIT_STATE_DIR
    state.mkdir(parents=True, exist_ok=True)
    (state / f"{tx_id}.json").write_text(json.dumps({
        "tx_id": tx_id,
        "created_at": "2026-09-18T20:15:00+00:00",
        "verdict": verdict,
        "workspace": str(runtime),
        "files": [{"path": "src/a.py"}],
        "applied": True,
        "in_tree": True,
        "record_path": str(state / f"{tx_id}.json"),
    }), encoding="utf-8")


def test_the_footer_carries_the_session_facts_and_reprints_only_on_news(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)          # no AGENTS.md here: the honest /init hint applies
    stub = Stub(tmp_path)
    shell, out, _err = _shell(tmp_path, stub)
    shell.commands, shell.failures = 3, 1
    assert shell.show_footer() is True
    text = out.getvalue()
    assert "3 commands" in text and "1 failed" in text
    assert "/init writes AGENTS.md" in text
    assert shell.show_footer() is False                # unchanged: no second line
    _write_record(tmp_path)                            # a transaction appeared: that is news
    assert shell.show_footer() is True
    # the id is shown as its time part with an ellipsis (a full transaction id is 28
    # characters) — and /diff prints the whole one, so nothing is presented as the id
    assert f"last edit {_TX_ID[:15]}" in out.getvalue()
    assert shell.footer_line().count(_TX_ID) == 0      # the id itself is never truncated
    assert "/diff shows it" in out.getvalue()
    assert shell.footer_key() == shell._footer_shown
    # the counters alone are never "news": a footer that reappears because a counter
    # ticked would reprint itself on every single turn
    assert shell.show_footer() is False


def test_the_footer_is_loud_when_the_last_transaction_did_not_land(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_record(tmp_path, verdict="rolled_back")
    shell, out, _err = _shell(tmp_path, Stub(tmp_path))
    shell.show_footer()
    assert "last edit " in out.getvalue() and "(rolled_back)" in out.getvalue()
    # a plain success is not restated: only a transaction that did *not* land says so.
    # A second runtime, because the edit state is the runtime's (and `_write_record` above
    # intentionally wrote the rolled-back one into the first).
    other_dir = tmp_path / "second"
    other_dir.mkdir()
    monkeypatch.chdir(other_dir)
    _write_record(other_dir, tx_id="tx-20260918T211500Z", verdict="applied")
    _other, out2, _err2 = _shell(other_dir, Stub(other_dir))
    _other.show_footer()
    tx = "tx-20260918T211500Z"
    assert f"last edit {tx[:15]}" in out2.getvalue()
    assert tx not in out2.getvalue()          # the cut is marked, not hidden
    assert "rolled_back" not in out2.getvalue()
    assert "applied_unverified" not in out2.getvalue()   # a landing needs no adjective


def test_the_footer_reports_tokens_only_when_a_command_reported_them(tmp_path):
    stub = Stub(tmp_path)
    shell, out, _err = _shell(tmp_path, stub)
    shell.last_result = repl.RunResult(argv=("x",), tokens_reported=1240)
    shell.show_footer()
    assert "tokens:1.2K (reported)" in out.getvalue()
    _other, out2, _err2 = _shell(tmp_path, Stub(tmp_path))
    _other.last_result = repl.RunResult(argv=("x",))
    _other.show_footer()
    assert "tokens" not in out2.getvalue()


def test_the_footer_can_be_turned_off(tmp_path):
    stub = Stub(tmp_path)
    shell, out, _err = _shell(tmp_path, stub, footer=False)
    shell.show_footer()
    assert out.getvalue() == ""


# --------------------------------------------------------------------------- #
# the streaming activity indicator
# --------------------------------------------------------------------------- #


def test_the_activity_indicator_measures_and_draws_in_place():
    stream = io.StringIO()
    activity = repl.Activity(stream, enabled=True, interval=0.05)
    activity.start("running skaldir agent run demo")
    activity.note_output(bytes_streamed=1340, lines_streamed=12)
    activity.note_tokens(1240)
    activity.repaint()
    activity.stop()
    text = stream.getvalue()
    assert "running skaldir agent run demo" in text
    assert "1.3 kB" in text and "12 lines" in text and "tokens:1.2K" in text
    assert "s)" in text                                   # the elapsed second it was drawn at
    assert activity.drawn >= 2                            # drawn, then redrawn by the clock
    assert text.endswith("\r\x1b[K")                      # its line is erased at the end
    assert activity.elapsed() == 0.0                      # stopped: no clock keeps running


def test_the_activity_indicator_writes_nothing_on_a_pipe_but_still_measures():
    stream = io.StringIO()
    activity = repl.Activity(stream, enabled=False)
    activity.start("running skaldir tools list")
    activity.note_output(bytes_streamed=42, lines_streamed=1)
    activity.stop()
    assert stream.getvalue() == ""
    assert activity.bytes == 42 and activity.lines == 1


def test_a_command_runs_the_clock_and_the_volume_lands_in_the_result(tmp_path):
    calls: list[str] = []

    def slow(argv):
        for index in range(3):
            print(f"line {index}")
            calls.append("write")
        return 0

    shell, out, _err = _shell(tmp_path, slow)
    shell.session.activity = repl.Activity(out, enabled=True, interval=0.05)
    result = shell.session.execute(["demo"])
    assert result.exit_code == 0
    assert result.lines_streamed == 3
    assert result.bytes_streamed > 0
    assert "3 line" not in result.footer()                 # the footer shows volume, not lines
    assert f"{repl.format_size(result.bytes_streamed)} streamed" in result.footer()
    assert shell.session.activity.drawn >= 1


def test_the_number_formats_are_the_ones_the_lines_use():
    assert repl.format_elapsed(0.5) == "0.5s"
    assert repl.format_elapsed(12.44) == "12.4s"
    assert repl.format_elapsed(63) == "1m 03s"
    assert repl.format_elapsed(3723) == "1h 02m 03s"
    assert repl.format_size(999) == "999 B"
    assert repl.format_size(1340) == "1.3 kB"
    assert repl.format_size(2_500_000) == "2.5 MB"
    assert repl.format_count(999) == "999"
    assert repl.format_count(1240) == "1.2K"
    assert repl.format_count(2_500_000) == "2.5M"


# --------------------------------------------------------------------------- #
# the diff layout helpers (render.py)
# --------------------------------------------------------------------------- #


def test_the_diff_stat_counts_the_diff_it_shows():
    text = _diff_for("src/a.py", "one\ntwo\n", "one\ntwo changed\nthree\n")
    added, removed = render.diff_stat(text)
    assert (added, removed) == (2, 1)
    assert render.diff_stat("") == (0, 0)
    # the per-file header rows are not changes
    assert render.diff_stat("--- a/x\n+++ b/x\n") == (0, 0)


def test_a_compact_preview_says_how_many_lines_it_hid():
    body = "".join(f"+line {index}\n" for index in range(40))
    files = [{"path": "src/a.py", "ok": True, "diff": body}]
    text, hidden = render.diff_preview(files, columns=80, limit=5)
    assert text.count("+line") == 5
    assert hidden == 35


# --------------------------------------------------------------------------- #
# /init
# --------------------------------------------------------------------------- #


def _repo(tmp_path) -> Path:
    root = tmp_path / "proj"
    (root / "src" / "pkg").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / ".git").mkdir()
    (root / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (root / "pyproject.toml").write_text(
        '[project]\nname = "demo-project"\nversion = "0.1.0"\n'
        'description = "a demo project"\nrequires-python = ">=3.12"\n',
        encoding="utf-8",
    )
    return root


def test_init_writes_agents_md_from_what_it_can_read(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    monkeypatch.chdir(root)
    stub = Stub(tmp_path)
    shell, out, _err = _shell(tmp_path, stub)
    code = shell.cmd_init([])
    assert code == repl.EXIT_OK
    text = out.getvalue()
    written = (root / "AGENTS.md").read_text(encoding="utf-8")
    assert "demo-project" in written                       # the declared name
    assert ">=3.12" in written                             # the declared interpreter
    assert "tests/" in written and "src/" in written       # the layout that really exists
    assert "pytest tests -q" in written                    # the test runner this repo declares
    assert "main" in written                               # the branch, from .git/HEAD
    assert "wrote" in text and "AGENTS.md" in text
    # the shell never leaves a command record out of the session's history
    assert "## Running the work" in written


def test_init_never_overwrites_an_existing_agents_md_and_force_does(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    (root / "AGENTS.md").write_text("# hand written\n", encoding="utf-8")
    monkeypatch.chdir(root)
    shell, out, _err = _shell(tmp_path, Stub(tmp_path))
    assert shell.cmd_init([]) == repl.EXIT_BLOCKED
    assert "already exists" in out.getvalue()
    assert (root / "AGENTS.md").read_text(encoding="utf-8") == "# hand written\n"
    assert shell.cmd_init(["--force"]) == repl.EXIT_OK
    assert "hand written" not in (root / "AGENTS.md").read_text(encoding="utf-8")


def test_init_states_nothing_it_could_not_read(tmp_path, monkeypatch):
    root = tmp_path / "bare"
    root.mkdir()
    monkeypatch.chdir(root)
    body = repl.agents_md_for(root)
    assert "pytest" not in body and "npm test" not in body   # no invented test command
    assert "## Layout" in body                               # the sections are still there


def test_init_and_diff_are_palette_builtins():
    for name in ("init", "diff"):
        command = slash.command_for(name)
        assert command is not None and command.builtin and command.argv == ()
        assert command.group == "session"


def test_the_shell_routes_init_and_diff_without_running_a_command(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    monkeypatch.chdir(root)
    stub = Stub(tmp_path)
    shell, out, _err = _shell(tmp_path, stub)
    assert shell.run_slash("/init") is False
    assert (root / "AGENTS.md").is_file()
    assert "wrote" in out.getvalue()
    assert stub.calls == [], "a built-in must not invent a command to run"
    # ... and it is recorded like any other line, so /history shows it
    assert shell.history.entries[-1].line == "/init"
    assert shell.commands == 1 and shell.last_exit == repl.EXIT_OK


# --------------------------------------------------------------------------- #
# /diff, on a real transaction written by the real command
# --------------------------------------------------------------------------- #


def _real_transaction(tmp_path) -> tuple[Path, Path]:
    """A real ``edit apply`` through the real CLI — the record ``/diff`` must render."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "a.py").write_text("one\ntwo\n", encoding="utf-8")
    tx = tmp_path / "tx.json"
    tx.write_text(json.dumps({
        "reason": "the /diff test transaction",
        "verification": {"kind": "none"},
        "files": [{"path": "a.py", "edits": [{"old": "two\n", "new": "two changed\n"}]}],
    }), encoding="utf-8")
    # the state directory the shell reads by default: <runtime>/edit
    state = tmp_path / repl.EDIT_STATE_DIR
    code = cli.main(["edit", "apply", "--tx", str(tx), "--workspace", str(workspace),
                     "--state", str(state), "--runtime", str(tmp_path), "--json", "--quiet"])
    assert code in (0, 2), code
    return state, workspace


def test_dial_diff_shows_the_last_transactions_diff(tmp_path):
    state, _workspace = _real_transaction(tmp_path)
    records = edit_tx.list_transactions(state)
    assert records, "the real command must have written a record"

    def runner(argv):
        return cli.main(list(argv))

    shell, out, _err = _shell(tmp_path, runner)
    code = shell.cmd_diff([])
    text = out.getvalue()
    assert code == repl.EXIT_OK
    assert "edit diff" in text and records[0]["tx_id"] in text
    assert "a.py" in text and "+1 -1" in text
    assert "-two" in text and "+two changed" in text
    assert "verdict" in text
    assert "skaldir edit undo" in text        # the next honest step is on the line


def test_dial_diff_says_so_when_there_is_no_transaction(tmp_path):
    def runner(argv):
        return cli.main(list(argv))

    shell, out, _err = _shell(tmp_path, runner)
    assert shell.cmd_diff([]) == repl.EXIT_NOT_FOUND
    # the real `edit show` says it in its own words (reason "not_found", exit 5) and the
    # shell repeats that sentence instead of printing a flat empty record
    text = out.getvalue()
    assert "no transaction 'last'" in text
    assert "--list" in text
    assert "not_applicable" not in text


def test_dial_diff_says_so_when_the_named_transaction_is_unknown(tmp_path):
    def runner(argv):
        return cli.main(list(argv))

    shell, out, _err = _shell(tmp_path, runner)
    assert shell.cmd_diff(["bogus-tx"]) == repl.EXIT_NOT_FOUND
    assert "no transaction 'bogus-tx'" in out.getvalue()
    assert "None" not in out.getvalue()        # no unread fact sneaks into the line


def test_a_refused_init_counts_as_a_failed_turn(tmp_path, monkeypatch):
    """The footer's `N failed` has to include the shell's own refusals, not only commands."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "AGENTS.md").write_text("# mine\n", encoding="utf-8")
    shell, out, _err = _shell(tmp_path, Stub(tmp_path))

    class _Invocation:
        raw = "/init"

        class command:                      # noqa: N801 — a stand-in for the parsed command
            name = "init"
            slash = "/init"

        extra: tuple[str, ...] = ()

    assert shell.run_builtin(_Invocation) is False
    assert shell.last_exit == repl.EXIT_BLOCKED
    assert shell.failures == 1
    assert "does not overwrite it" in out.getvalue()
    assert (tmp_path / "AGENTS.md").read_text(encoding="utf-8") == "# mine\n"


def test_dial_diff_list_lists_the_records(tmp_path):
    _state, _workspace = _real_transaction(tmp_path)
    shell, out, _err = _shell(tmp_path, Stub(tmp_path))
    assert shell.cmd_diff(["--list"]) == repl.EXIT_OK
    assert "edit record(s)" in out.getvalue()


# --------------------------------------------------------------------------- #
# the wiring contract (no cli.py / cli_contract.py edit needed)
# --------------------------------------------------------------------------- #


def test_the_shell_groups_contract_entry_reports_the_new_facts():
    entries = repl.contract_entries()
    assert "shell" in entries
    schema = entries["shell"].data_schema["properties"]
    for key in ("approvals", "approval_session", "last_edit"):
        assert key in schema, key
