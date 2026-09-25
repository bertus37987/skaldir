"""Panel-Backend: Models-Rollen + Treasury-Credentials/Grants (Panel-Mutationen).

Beweist den E2E-Pfad: Credential anlegen → Handle → Grant an genau EIN Tool →
Tool-Scope prüfbar → Revoke → kein Zugriff. Plus: kein Klartext-Secret in
irgendeiner Panel-Payload. Models: echte Provider/Listen aus HERMES_HOME,
Rollen-Persistenz pro Rolle.
"""

import importlib.util
import json
from pathlib import Path

import pytest

from skaldir.execution import sandbox_available
from skaldir.treasury import TreasuryState

PANEL_PATH = (
    Path(__file__).resolve().parent.parent
    / "integrations"
    / "hermes"
    / "godsv_plugin"
    / "dashboard"
    / "plugin_api.py"
)


def _load_panel():
    spec = importlib.util.spec_from_file_location("godsv_panel_api", PANEL_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def panel():
    return _load_panel()


def _hermes_home(tmp_path: Path) -> Path:
    home = tmp_path / "hermes"
    (home / "godsv").mkdir(parents=True, exist_ok=True)
    (home / "provider_models_cache.json").write_text(
        json.dumps(
            {
                "deepseek": {
                    "fp": "fp1",
                    "at": 1.0,
                    "models": ["deepseek-v4-pro", "deepseek-v4-flash"],
                },
                "openrouter": {
                    "fp": "fp2",
                    "at": 1.0,
                    "models": ["deepseek/deepseek-v4.1-flash"],
                },
            }
        ),
        encoding="utf-8",
    )
    (home / "config.yaml").write_text(
        "model:\n"
        "  default: deepseek-v4-flash\n"
        "  provider: deepseek\n"
        "fallback_providers:\n"
        "  - provider: openrouter\n"
        "    model: deepseek/deepseek-v4.1-flash\n",
        encoding="utf-8",
    )
    return home


# --------------------------------------------------------------------------- #
# tools panel: tools_payload / detail / actions                                #
# --------------------------------------------------------------------------- #

TOOL_SRC = '''"""Tiny tool for the tools-panel tests."""
def main(n: int = 2) -> dict:
    return {"doubled": n * 2}
'''


def _build_one_tool(
    tmp_home,
    *,
    name="doubler",
    required_secrets=("secret://demo/key",),
    capability="double a number for panel tests",
):
    from skaldir.toolbuilder import ToolBuilder, ToolBuildRequest

    builder = ToolBuilder(tmp_home / "godsv")
    result = builder.build(
        ToolBuildRequest(
            capability=capability,
            name=name,
            source_code=TOOL_SRC,
            module="doubler",
            entrypoint="main",
            sample_input={"n": 21},
            required_secrets=list(required_secrets),
            allowed_domains=["demo.example.com"],
            research_notes="trivial",
        )
    )
    assert result["ok"] is True, result
    return result


class TestToolsPanel:
    def test_tools_payload_lists_installed_and_queue(self, panel, tmp_path, monkeypatch):
        home = _hermes_home(tmp_path)
        monkeypatch.setenv("HERMES_HOME", str(home))
        _build_one_tool(home)
        payload = panel.tools_payload()
        names = {row["name"] for row in payload["installed"]}
        assert "doubler" in names
        row = next(r for r in payload["installed"] if r["name"] == "doubler")
        assert row["generated"] is True
        assert row["required_secrets"] == ["secret://demo/key"]
        assert payload["generated"] and payload["queue"]
        stages = [stage["stage"] for stage in payload["queue"][0]["stages"]]
        assert stages[-1] == "ready" and "gates" in stages

    def test_tools_payload_exposes_capability_requests(self, panel, tmp_path, monkeypatch):
        home = _hermes_home(tmp_path)
        monkeypatch.setenv("HERMES_HOME", str(home))
        _build_one_tool(home)
        from skaldir.capability import CapabilityRequest, CapabilityStore

        store = CapabilityStore(home / "godsv")
        built = store.add(
            CapabilityRequest(
                capability="double a number", reason="blocked mid-task",
                session_id="s1", name="doubler",
                required_secrets=["secret://demo/key"],
            )
        )
        built.status = "fulfilled"
        built.result_tool = "doubler"
        store.update(built)
        waiting = store.add(
            CapabilityRequest(
                capability="Linear API", reason="need to update issue state",
                session_id="s1", kind="mcp", name="linear_tool",
                required_secrets=["secret://linear/token"],
                allowed_domains=["api.linear.app"],
            )
        )
        payload = panel.tools_payload()
        reqs = {row["request_id"]: row for row in payload["requests"]}
        assert reqs[built.request_id]["status"] == "fulfilled"
        assert reqs[built.request_id]["result_tool"] == "doubler"
        assert reqs[built.request_id]["build"], "Build-Stufen müssen verknüpft sein"
        assert "ready" in reqs[built.request_id]["build"]["stages"]
        assert reqs[waiting.request_id]["status"] == "open"
        assert reqs[waiting.request_id]["requested_by"] == "main-agent"
        assert reqs[waiting.request_id]["kind"] == "mcp"
        assert reqs[waiting.request_id]["required_secrets"] == ["secret://linear/token"]
        # Handles sichtbar, Werte nie
        for row in payload["requests"]:
            for handle in row["required_secrets"]:
                assert handle.startswith("secret://")

    def test_tool_detail_and_actions(self, panel, tmp_path, monkeypatch):
        home = _hermes_home(tmp_path)
        monkeypatch.setenv("HERMES_HOME", str(home))
        _build_one_tool(home)
        detail = panel.tool_detail_payload("doubler")
        assert detail["spec"]["name"] == "doubler"
        assert detail["manifest"]["required_secrets"] == ["secret://demo/key"]
        assert detail["builds"]

        # Wave 6: a tool that declares credentials is NOT run outside the treasury gate
        gated = panel.tool_action_payload("doubler", "test")
        assert gated["ok"] is False and gated["treasury_denied"] is True, gated
        assert "vault" in gated["output"].lower() or "grant" in gated["output"].lower()

        # … and a tool without declared handles still runs the real wrapper deterministically
        # (the manifest is what the gate reads, so this is the honest counter-case)
        import yaml as _yaml

        manifest_path = home / "godsv" / "generated_tools" / "doubler" / "manifest.yaml"
        manifest = _yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        manifest["required_secrets"] = []
        manifest_path.write_text(_yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
        tested = panel.tool_action_payload("doubler", "test")
        assert tested["ok"] is True, tested
        assert '"doubled": 42' in tested["output"]

        # … and with an active grant for this tool the same action runs under the broker
        if sandbox_available():
            from skaldir.treasury import TreasuryState, Vault

            monkeypatch.setenv("GODSV_VAULT_KEY", "panel-master-key")
            vault = Vault(home / "godsv" / "vault.json", "panel-master-key")
            vault.add(
                service="demo",
                name="key",
                secret="sk-test-panel-123",
                scopes=("use", "grant:required"),
            )
            state = TreasuryState(home / "godsv" / "treasury.db")
            grant = state.add_grant_request(
                "secret://demo/key", "panel test", 600, tool_id="doubler"
            )
            assert state.approve_grant(grant, by="owner")
            state.close()
            manifest["required_secrets"] = ["secret://demo/key"]
            manifest_path.write_text(_yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
            granted = panel.tool_action_payload("doubler", "test")
            assert granted["ok"] is True, granted
            assert '"doubled": 42' in granted["output"]
            assert granted["treasury"] == {"secret://demo/key": "SKALDIR_SECRET_DEMO_KEY"}

        # disable → revoked; enable → approved again
        disabled = panel.tool_action_payload("doubler", "disable")
        assert disabled["ok"] is True and disabled["status"] == "revoked"
        enabled = panel.tool_action_payload("doubler", "enable")
        assert enabled["ok"] is True and enabled["status"] == "approved"

        with pytest.raises(ValueError):
            panel.tool_action_payload("doubler", "delete-everything")
        with pytest.raises(ValueError):
            panel.tool_detail_payload("nope-not-here")


class TestModelsPanel:
    def test_payload_uses_real_providers(self, panel, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(_hermes_home(tmp_path)))
        payload = panel.models_payload()
        slugs = {row["slug"] for row in payload["providers"]}
        assert {"deepseek", "openrouter"} <= slugs
        assert payload["hermes"]["provider"] == "deepseek"
        assert payload["hermes"]["model"] == "deepseek-v4-flash"
        assert payload["hermes"]["fallbacks"][0]["provider"] == "openrouter"
        deepseek = next(row for row in payload["providers"] if row["slug"] == "deepseek")
        assert "deepseek-v4-pro" in deepseek["models"]

    def test_role_save_persists_and_validates(self, panel, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(_hermes_home(tmp_path)))
        result = panel.set_role_payload("watcher", "deepseek", "deepseek-v4-flash")
        assert result["ok"] is True and result["model_listed"] is True
        result = panel.set_role_payload(
            "investigator", "openrouter", "deepseek/deepseek-v4.1-flash"
        )
        assert result["ok"] is True
        # fresh read: persisted per role
        payload = panel.models_payload()
        assert payload["roles"]["watcher"]["model"] == "deepseek-v4-flash"
        assert payload["roles"]["investigator"]["provider"] == "openrouter"
        with pytest.raises(ValueError):
            panel.set_role_payload("not-a-role", "deepseek", "deepseek-v4-flash")
        with pytest.raises(ValueError):
            panel.set_role_payload("watcher", "", "")
        with pytest.raises(ValueError):
            panel.set_role_payload("watcher", "unknown-provider", "x")

    def test_unlisted_model_warns_but_saves(self, panel, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(_hermes_home(tmp_path)))
        result = panel.set_role_payload("judge", "deepseek", "deepseek-v5-future")
        assert result["ok"] is True and result["model_listed"] is False

    def test_test_model_rejects_empty(self, panel):
        with pytest.raises(ValueError):
            panel.test_model_payload("", "")


class TestTreasuryPanel:
    def test_without_master_key_is_graceful(self, panel, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(_hermes_home(tmp_path)))
        monkeypatch.delenv("GODSV_VAULT_KEY", raising=False)
        payload = panel.credentials_payload()
        assert payload["available"] is False
        assert payload["credentials"] == []
        assert "GODSV_VAULT_KEY" in payload["reason"]

    def test_credential_grant_revoke_roundtrip_no_leak(self, panel, tmp_path, monkeypatch):
        home = _hermes_home(tmp_path)
        monkeypatch.setenv("HERMES_HOME", str(home))
        monkeypatch.setenv("GODSV_VAULT_KEY", "panel-test-master")
        secret = "tok-super-secret-987"

        added = panel.add_credential_payload(
            service="weather", name="api", secret=secret, description="E2E"
        )
        handle = added["handle"]
        assert handle.startswith("secret://")
        assert secret not in json.dumps(added)

        payload = panel.credentials_payload()
        blob = json.dumps(payload)
        assert secret not in blob  # never a plaintext value in the payload
        entry = next(c for c in payload["credentials"] if c["handle"] == handle)
        assert entry["service"] == "weather" and entry["used_by"] == []

        grant = panel.grant_credential_payload(
            handle=handle, tool_id="tool_weather", ttl=600, purpose="e2e"
        )
        assert grant["ok"] is True
        state = TreasuryState(home / "godsv" / "treasury.db")
        assert state.has_active_grant_for_tool(handle, "tool_weather") is True
        assert state.has_active_grant_for_tool(handle, "tool_other") is False
        # used_by now reflects the tool
        payload = panel.credentials_payload()
        entry = next(c for c in payload["credentials"] if c["handle"] == handle)
        assert entry["used_by"] == ["tool_weather"]

        revoked = panel.revoke_grant_payload(grant["grant_id"])
        assert revoked["ok"] is True
        assert state.has_active_grant_for_tool(handle, "tool_weather") is False

        # credential revoke removes the handle
        removed = panel.revoke_credential_payload(handle)
        assert removed["ok"] is True
        payload = panel.credentials_payload()
        assert all(c["handle"] != handle for c in payload["credentials"])

    def test_validation_rejects_bad_input(self, panel, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(_hermes_home(tmp_path)))
        monkeypatch.setenv("GODSV_VAULT_KEY", "panel-test-master")
        with pytest.raises(ValueError):
            panel.add_credential_payload(service="bad service!", name="api", secret="x")
        with pytest.raises(ValueError):
            panel.add_credential_payload(service="ok", name="api", secret="")
        with pytest.raises(ValueError):
            panel.grant_credential_payload(handle="secret://ok/a", tool_id="", ttl=600)
        with pytest.raises(ValueError):
            panel.grant_credential_payload(handle="secret://ok/a", tool_id="t", ttl=5)
        with pytest.raises(ValueError):
            panel.grant_credential_payload(handle="secret://missing/x", tool_id="t", ttl=600)

    def test_broker_denies_without_tool_grant(self, panel, tmp_path, monkeypatch):
        """run_with_secret with tool_id must fail closed without a tool grant."""
        home = _hermes_home(tmp_path)
        monkeypatch.setenv("HERMES_HOME", str(home))
        monkeypatch.setenv("GODSV_VAULT_KEY", "panel-test-master")
        from skaldir.treasury import Broker, Vault

        added = panel.add_credential_payload(service="svc", name="key", secret="s3cr3t")
        handle = added["handle"]
        vault = Vault(home / "godsv" / "vault.json", "panel-test-master")
        state = TreasuryState(home / "godsv" / "treasury.db")
        broker = Broker(vault, state)
        result = broker.run_with_secret(
            handle,
            argv=["true"],
            env_var="TOKEN",
            purpose="e2e-deny",
            tool_id="tool_without_grant",
        )
        assert result.ok is False and result.denied is True
        assert "grant" in (result.detail or "")


class TestCuAndEvalsPanel:
    def test_cu_payload_shows_real_runners_and_policy(self, panel, tmp_path, monkeypatch):
        home = _hermes_home(tmp_path)
        monkeypatch.setenv("HERMES_HOME", str(home))
        payload = panel.cu_payload()
        names = {row["name"] for row in payload["runners"]}
        assert "dry-run" in names
        assert payload["policy"]["channel_order"] == "API > DOM > Vision"
        assert payload["policy"]["require_isolation"] is True
        assert payload["runs"] == []

    def test_cu_demo_runs_real_executor_and_persists(self, panel, tmp_path, monkeypatch):
        home = _hermes_home(tmp_path)
        monkeypatch.setenv("HERMES_HOME", str(home))
        result = panel.cu_demo_payload()
        assert result["ok"] is True
        run = result["run"]
        assert run["status"] in ("verified", "aborted")
        assert run["channel"] in ("api", "dom", "vision")
        runs = panel.cu_payload()["runs"]
        assert len(runs) == 1 and runs[0]["kind"] == "dry-run"

    def test_evals_payload_lists_episodes_and_metrics(self, panel, tmp_path, monkeypatch):
        home = _hermes_home(tmp_path)
        monkeypatch.setenv("HERMES_HOME", str(home))
        payload = panel.evals_payload()
        assert payload["episodes"], "Episoden-Katalog muss gefüllt sein"
        names = {ep["name"] for ep in payload["episodes"]}
        assert "demo" in names or any("demo" in n for n in names)
        measured = payload["metrics"]["measured"]
        assert measured["events_total"] == 0  # leere Runtime: ehrliche Nullen
        assert "notes" in payload["metrics"]["estimated"]

    def test_evals_run_executes_suite_and_persists(self, panel, tmp_path, monkeypatch):
        home = _hermes_home(tmp_path)
        monkeypatch.setenv("HERMES_HOME", str(home))
        result = panel.evals_run_payload()
        assert result["ok"] is True
        run = result["run"]
        assert run["total"] >= 5 and run["passed"] == run["total"]
        again = panel.evals_payload()["last_run"]
        assert again is not None and again["passed"] == run["passed"]


class TestToolUsagePanel:
    def test_usage_ledger_redacts_and_counts(self, panel, tmp_path, monkeypatch):
        home = _hermes_home(tmp_path)
        monkeypatch.setenv("HERMES_HOME", str(home))
        from skaldir.toolusage import record_usage

        runtime = home / "godsv"
        record_usage(runtime, "tool_a", ok=True, duration_ms=12)
        record_usage(runtime, "tool_a", ok=True, duration_ms=20)
        record_usage(
            runtime, "tool_a", ok=False,
            error="ValueError: boom key sk-ABCDEF1234567890abcdef",
        )
        record_usage(runtime, "tool_b", ok=True, duration_ms=5)
        from skaldir.toolusage import usage_counts, usage_summary

        summary = usage_summary(runtime, "tool_a")
        assert summary["count"] == 3
        assert summary["ok"] == 2 and summary["failed"] == 1
        assert summary["avg_duration_ms"] == 16
        assert summary["last_errors"], "Fehlertext muss erhalten bleiben"
        assert "sk-" not in summary["last_errors"][0], "Secret muss redigiert sein"
        assert usage_counts(runtime) == {"tool_a": 3, "tool_b": 1}

    def test_tool_detail_and_listing_include_usage(self, panel, tmp_path, monkeypatch):
        home = _hermes_home(tmp_path)
        monkeypatch.setenv("HERMES_HOME", str(home))
        _build_one_tool(home)
        from skaldir.toolusage import record_usage

        record_usage(home / "godsv", "doubler", ok=True, duration_ms=7)
        detail = panel.tool_detail_payload("doubler")
        assert detail["usage"]["count"] == 1
        assert detail["usage"]["ok"] == 1
        listing = panel.tools_payload()
        row = next(r for r in listing["installed"] if r["name"] == "doubler")
        assert row["usage_count"] == 1


class TestCredentialSelftest:
    def test_selftest_proves_injection_without_leaking(self, panel, tmp_path, monkeypatch):
        home = _hermes_home(tmp_path)
        monkeypatch.setenv("HERMES_HOME", str(home))
        monkeypatch.setenv("GODSV_VAULT_KEY", "panel-test-master")
        added = panel.add_credential_payload(
            service="svc", name="key", secret="s3cr3t-value-123"
        )
        result = panel.test_credential_payload(added["handle"])
        assert result["ok"] is True, result
        assert "injection-ok" in result["detail"]
        import json as _json

        blob = _json.dumps(result)
        assert "s3cr3t-value-123" not in blob, "Klartext darf nie zurückkommen"

    def test_selftest_denies_unknown_handle(self, panel, tmp_path, monkeypatch):
        home = _hermes_home(tmp_path)
        monkeypatch.setenv("HERMES_HOME", str(home))
        monkeypatch.setenv("GODSV_VAULT_KEY", "panel-test-master")
        result = panel.test_credential_payload("secret://unknown/none")
        assert result["ok"] is False
        assert result["denied"] is True
        assert "unknown handle" in result["detail"]

    def test_selftest_rejects_bad_handle(self, panel, tmp_path, monkeypatch):
        home = _hermes_home(tmp_path)
        monkeypatch.setenv("HERMES_HOME", str(home))
        monkeypatch.setenv("GODSV_VAULT_KEY", "panel-test-master")
        import pytest

        with pytest.raises(ValueError):
            panel.test_credential_payload("not-a-handle")
