"""P1-4 — deterministic context-drift / progress-stall signals."""

import godsv_plugin
import pytest
from test_contract_sessions import _FakeCtx


class TestDriftSignals:
    @pytest.fixture
    def ctx(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.setenv("GODSV_MODE", "whisper")
        monkeypatch.delenv("GODSV_GUARD_FORCE", raising=False)
        godsv_plugin._SESSIONS.clear()
        fake = _FakeCtx()
        godsv_plugin.register(fake)
        yield fake
        godsv_plugin._SESSIONS.clear()

    def _session(self, ctx, monkeypatch, tmp_path, sid, goal=""):
        monkeypatch.chdir(tmp_path)
        ctx.hooks["on_session_start"](session_id=sid)
        session = godsv_plugin._SESSIONS[sid]
        if goal:
            session._completion_store().begin_goal(sid, goal, [])
        return session

    def test_context_drift_fires_after_three_unrelated_edits(self, ctx, monkeypatch, tmp_path):
        s = self._session(ctx, monkeypatch, tmp_path, "d1", "Refactor the authentication module")
        assert s._drift_signals("file_edit", {"path": "docs/changelog.md"}) == []
        assert s._drift_signals("file_edit", {"path": "docs/notes.md"}) == []
        signals = s._drift_signals("file_edit", {"path": "docs/roadmap.md"})
        assert [sig.kind for sig in signals] == ["context_drift"]
        # one signal per streak: the counter reset, so two more edits stay quiet
        assert s._drift_signals("file_edit", {"path": "docs/more.md"}) == []
        assert s._drift_signals("file_edit", {"path": "docs/later.md"}) == []

    def test_dev_null_redirect_is_not_a_file_edit(self, ctx, monkeypatch, tmp_path):
        s = self._session(ctx, monkeypatch, tmp_path, "d5", "Fix the login bug")
        captured = []
        monkeypatch.setattr(s, "observe", lambda kind, payload: captured.append((kind, payload)))
        # a stderr discard is not an edit — neither as /dev/null nor as /dev/stderr
        s._maybe_file_edit("terminal", {"command": "grep x /tmp/f 2>/dev/null"})
        s._maybe_file_edit("terminal", {"command": "cmd > /dev/stderr"})
        assert captured == []
        # a REAL target in the same command still registers (and only that one)
        s._maybe_file_edit("terminal", {"command": "echo hi > /tmp/out.txt 2>/dev/null"})
        assert [pl["path"] for _k, pl in captured] == ["/tmp/out.txt"]

    def test_related_edits_reset_the_streak(self, ctx, monkeypatch, tmp_path):
        s = self._session(ctx, monkeypatch, tmp_path, "d2", "Refactor the authentication module")
        s._drift_signals("file_edit", {"path": "docs/a.md"})
        s._drift_signals("file_edit", {"path": "docs/b.md"})
        # a related edit (shares the term stem 'auth') resets the streak
        assert s._drift_signals("file_edit", {"path": "src/auth_login.py"}) == []
        assert s._drift_signals("file_edit", {"path": "docs/c.md"}) == []
        assert s._drift_signals("file_edit", {"path": "docs/d.md"}) == []

    def test_progress_stall_after_40_events_without_contract_progress(
        self, ctx, monkeypatch, tmp_path
    ):
        s = self._session(ctx, monkeypatch, tmp_path, "d3", "Fix the login bug")
        s._drift_signals("tool_call", {})  # sync the progress marker after begin_goal
        fired = []
        for step in range(1, 41):
            got = s._drift_signals("tool_call", {})
            if got:
                fired.append((step, got[0].kind))
        assert fired == [(40, "progress_stall")]

    def test_contract_progress_resets_the_stall_window(self, ctx, monkeypatch, tmp_path):
        s = self._session(ctx, monkeypatch, tmp_path, "d4", "Fix the login bug")
        s._drift_signals("tool_call", {})  # sync
        for _ in range(40):
            s._drift_signals("tool_call", {})
        # a real contract update (verdict/state) counts as progress → reset
        s._completion_store().transition("VERIFYING")
        quiet = [s._drift_signals("tool_call", {}) for _ in range(39)]
        assert all(not got for got in quiet)

    def test_counters_survive_turn_boundaries(self, ctx, monkeypatch, tmp_path):
        """F13: turn ends dispose the session object — drift must persist."""
        s = self._session(ctx, monkeypatch, tmp_path, "d7", "Refactor the authentication module")
        s._drift_signals("file_edit", {"path": "docs/one.md"})
        s._drift_signals("file_edit", {"path": "docs/two.md"})
        # simulate the turn boundary: the session object is disposed + rebuilt
        godsv_plugin._SESSIONS.pop("d7")
        ctx.hooks["on_session_start"](session_id="d7")
        s2 = godsv_plugin._SESSIONS["d7"]
        signals = s2._drift_signals("file_edit", {"path": "docs/three.md"})
        assert [sig.kind for sig in signals] == ["context_drift"]

    def test_no_contract_no_drift_signals(self, ctx, monkeypatch, tmp_path):
        s = self._session(ctx, monkeypatch, tmp_path, "d5")  # no goal
        for _ in range(45):
            assert s._drift_signals("tool_call", {}) == []
        assert s._drift_signals("file_edit", {"path": "x/y.md"}) == []


    def test_shell_writes_mirror_into_file_edits(self, ctx, monkeypatch, tmp_path):
        """F13: terminal redirections count as edits for the drift detectors."""
        s = self._session(ctx, monkeypatch, tmp_path, "d7", "Refactor the authentication module")
        s._maybe_file_edit("terminal", {"command": "cat > /tmp/driftcheck/x.md <<'EOF'\nx\nEOF"})
        assert s._unrelated_edits == 1
        s._maybe_file_edit("terminal", {"command": "echo y > /tmp/driftcheck/y.md"})
        assert s._unrelated_edits == 2
        s._maybe_file_edit("terminal", {"command": "printf z > /tmp/driftcheck/z.md"})
        assert s._unrelated_edits == 0  # third unrelated edit fired + reset the streak

    def test_shell_reads_and_related_writes_do_not_drift(self, ctx, monkeypatch, tmp_path):
        s = self._session(ctx, monkeypatch, tmp_path, "d8", "Refactor the authentication module")
        s._maybe_file_edit("terminal", {"command": "cat /tmp/driftcheck/x.md"})  # read only
        assert s._unrelated_edits == 0
        s._maybe_file_edit("terminal", {"command": "echo note > /tmp/driftcheck/n1.md"})
        assert s._unrelated_edits == 1
        s._maybe_file_edit("terminal", {"command": "echo x > src/refactor_notes.md"})  # related
        assert s._unrelated_edits == 0

    def test_observe_path_runs_clean_with_drift(self, ctx, monkeypatch, tmp_path):
        """The wired pipeline must stay silent-failure-free on drift events."""
        s = self._session(ctx, monkeypatch, tmp_path, "d6", "Ship the parser fix")
        for i in range(3):
            s.observe("file_edit", {"path": f"docs/noise-{i}.md", "added": "+ x"})
        assert s._unrelated_edits == 0  # streak fired + reset inside the pipeline
