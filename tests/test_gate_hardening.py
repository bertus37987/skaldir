"""Etappe 3 (P1/B1–B3, B5): build-event kinds, sandbox fail-closed, exact origin
allowlist, explicit blocked markers, checker-failure honesty.

Fail before the hardening: build stages raised in the event store (and were
swallowed), the sandbox fell back to a plain unsandboxed subprocess, the origin
check was a substring test, and free-text could terminate a run.
"""

import importlib.util
import os
import shutil
import sys
from pathlib import Path

import godsv_plugin
import pytest

from skaldir import gates
from skaldir.events import CHAT_KINDS, KINDS, EventStore
from skaldir.toolbuilder import STAGES

PANEL_PATH = (
    Path(__file__).resolve().parent.parent
    / "integrations"
    / "hermes"
    / "godsv_plugin"
    / "dashboard"
    / "plugin_api.py"
)


def _load_panel():
    spec = importlib.util.spec_from_file_location("godsv_panel_api_t3", PANEL_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeCtx:
    def __init__(self) -> None:
        self.hooks: dict = {}

    def register_hook(self, name, fn):
        self.hooks[name] = fn


class TestBuildEvents:
    def test_every_stage_is_a_registered_kind(self):
        for stage in STAGES:
            assert f"tool_build_{stage}" in KINDS, f"missing kind for stage {stage}"
        assert "tool_build_failed" in KINDS

    def test_build_stages_reach_the_chat_feed(self):
        for stage in STAGES:
            assert f"tool_build_{stage}" in CHAT_KINDS, f"stage {stage} hidden from chat"

    def test_builder_mirrors_stage_events_with_session(self, tmp_path):
        from skaldir.toolbuilder import ToolBuilder

        db = tmp_path / "events.db"
        store = EventStore(db)
        builder = ToolBuilder(tmp_path, events=store, session_id="s-build")
        builder._record("b1", "requested", capability="demo")
        store.close()
        store = EventStore(db)
        events = list(store.stream())
        store.close()
        assert any(
            event.kind == "tool_build_requested" and event.session_id == "s-build"
            for event in events
        )
        assert any(event.kind == "tool_build_requested" for event in events)


class TestSandboxFailClosed:
    def test_without_bwrap_nothing_executes(self, tmp_path, monkeypatch):
        monkeypatch.setattr(gates.shutil, "which", lambda _name: None)
        code, output, sandboxed = gates._run(
            [sys.executable, "-c", "print('must not run')"], workdir=tmp_path, timeout=10
        )
        assert code == -127
        assert sandboxed is False
        assert "fail-closed" in output

    @pytest.mark.skipif(shutil.which("bwrap") is None, reason="bwrap required")
    def test_sandbox_hides_foreign_tmp_and_home_data(self, tmp_path):
        workdir = tmp_path / "work"
        workdir.mkdir()
        elsewhere = Path(f"/tmp/gv-probe-{os.getpid()}")
        elsewhere.mkdir(exist_ok=True)
        (elsewhere / "hidden.txt").write_text("hidden", encoding="utf-8")
        try:
            code, _out, sandboxed = gates._run(
                ["/usr/bin/cat", str(elsewhere / "hidden.txt")], workdir=workdir, timeout=15
            )
            assert sandboxed is True
            assert code != 0  # foreign /tmp is empty inside the sandbox
            # home data stays invisible: only the interpreter's bind points
            # exist, never .ssh/.config/... (P1/B2)
            home = Path.home()
            if (home / ".bashrc").exists():
                code2, _out2, _ = gates._run(
                    ["/usr/bin/cat", str(home / ".bashrc")], workdir=workdir, timeout=15
                )
                assert code2 != 0
            _code3, out3, _ = gates._run(
                ["/usr/bin/ls", "-A", str(home)], workdir=workdir, timeout=15
            )
            for marker in (".ssh", ".config", ".bash_history", ".gitconfig"):
                assert marker not in out3
        finally:
            shutil.rmtree(elsewhere, ignore_errors=True)


class TestSecretInjectionFailClosed:
    def test_broker_refuses_secret_run_without_sandbox(self, tmp_path, monkeypatch):
        from skaldir.treasury import Broker, TreasuryState, Vault

        vault = Vault(tmp_path / "vault.json", "audit-master-123")
        handle = vault.add(service="s", name="n", secret="s3cret-value-12345")
        state = TreasuryState(tmp_path / "treasury.db")
        try:
            broker = Broker(vault, state)
            monkeypatch.setattr(
                gates,
                "_run",
                lambda *a, **k: (
                    -127,
                    "sandbox unavailable — execution blocked (fail-closed)",
                    False,
                ),
            )
            result = broker.run_with_secret(
                handle, argv=["/usr/bin/true"], env_var="X", purpose="t"
            )
            assert not result.ok
            assert "sandbox" in result.detail.lower()
        finally:
            state.close()


class TestOriginAllowlist:
    def test_substring_origins_are_rejected(self):
        panel = _load_panel()
        assert panel.origin_allowed("https://evil.com/x?localhost") is False
        assert panel.origin_allowed("https://localhost.evil.com") is False
        assert panel.origin_allowed("http://evil.com/#127.0.0.1") is False
        assert panel.origin_allowed("https://evil.com") is False

    def test_local_origins_pass(self):
        panel = _load_panel()
        assert panel.origin_allowed("null") is True
        assert panel.origin_allowed("file:///app/index.html") is True
        assert panel.origin_allowed("http://localhost:8080") is True
        assert panel.origin_allowed("http://127.0.0.1:9333") is True
        assert panel.origin_allowed("https://[::1]:5000") is True

    def test_missing_origin_is_rejected(self):
        """Block 1: leeres Origin war erlaubt — genau das war das Trust-Loch.

        Der Desktop-App-Pfad schickt ``app://…`` plus Token; ein Aufrufer
        ohne Origin-Header ist kein Browser-Same-Origin-Fetch, sondern curl
        oder ein lokaler Prozess.
        """
        panel = _load_panel()
        assert panel.origin_allowed("") is False
        assert panel.origin_allowed("   ") is False


class TestBlockedMarkersAndCheckerFailure:
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

    def test_free_text_never_terminates_a_run(self):
        for _marker, pattern in godsv_plugin.GodsvSession._BLOCKED_MARKERS:
            assert not pattern.search(
                "The user was blocked by the captcha; we continued. "
                "The external provider blocked us briefly — no impact."
            )

    def test_explicit_status_lines_do_terminate(self):
        markers = dict(godsv_plugin.GodsvSession._BLOCKED_MARKERS)
        assert markers["BLOCKED_USER"].search("BLOCKED_USER: credentials missing")
        assert markers["BLOCKED_USER"].search("Status: BLOCKED_USER")
        assert markers["FAILED_UNRECOVERABLE"].search("\nFAILED_UNRECOVERABLE — build broke")

    def test_checker_crash_is_recorded_and_fails_closed(self, ctx, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        ctx.hooks["on_session_start"](session_id="r1")
        session = godsv_plugin._SESSIONS["r1"]

        def _boom(_text):
            raise RuntimeError("checker exploded")

        monkeypatch.setattr(session, "_strict_completion", _boom)
        out = ctx.hooks["pre_verify"](
            session_id="r1", attempt=0, final_response="Alles ist fertig und läuft."
        )
        assert out is None or out.get("action") == "continue"  # never a blind pass
        kinds = [event.kind for event in session.store.iter_session("r1")]
        assert "completion_checker_failed" in kinds
        # the final answer is marked unverified — the failure never weakens the gate
        final = ctx.hooks["transform_llm_output"](
            response_text="Alles fertig.", session_id="r1"
        )
        assert final is not None and "checker FAILED" in final


class TestTestingToolValidationGate:
    ALWAYS_PASS = (
        "import sys\n\n"
        "def check(target=''):\n"
        "    print('PASS')\n"
        "    return 0\n\n"
        "if __name__ == '__main__':\n"
        "    sys.exit(check(sys.argv[1] if len(sys.argv) > 1 else ''))\n"
    )
    HONEST = (
        "import sys\n\n"
        "def check(target=''):\n"
        "    ok = target == 'good-target'\n"
        "    print('PASS' if ok else 'FAIL')\n"
        "    return 0 if ok else 1\n\n"
        "if __name__ == '__main__':\n"
        "    sys.exit(check(sys.argv[1] if len(sys.argv) > 1 else ''))\n"
    )

    def _request(self, source):
        from skaldir.toolbuilder import ToolBuildRequest

        return ToolBuildRequest(
            capability="demo checker",
            kind="testing_tool",
            name="checker",
            module="checker",
            entrypoint="check",
            sample_input={"target": "good-target"},
            source_code=source,
            validation_cases=[("broken-target", False), ("good-target", True)],
            requested_by="test",
        )

    def test_always_pass_tool_is_refused(self, tmp_path):
        from skaldir.toolbuilder import ToolBuilder

        result = ToolBuilder(tmp_path).build(self._request(self.ALWAYS_PASS))
        assert result["ok"] is False
        assert "promotion refused" in result["error"]

    def test_honest_tool_passes_validation(self, tmp_path):
        from skaldir.toolbuilder import ToolBuilder

        result = ToolBuilder(tmp_path).build(self._request(self.HONEST))
        assert result["ok"] is True, result.get("error")

    def test_missing_cases_block_registration(self, tmp_path):
        from skaldir.toolbuilder import ToolBuilder

        request = self._request(self.HONEST)
        request.validation_cases = []
        result = ToolBuilder(tmp_path).build(request)
        assert result["ok"] is False
        assert "requires validation cases" in result["error"]
