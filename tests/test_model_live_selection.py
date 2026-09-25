"""Live selection must bind the selected endpoint, never another provider's key."""

from skaldir import model_discovery as md
from skaldir import model_picker as mp


def test_live_rows_replace_stale_defaults_and_keep_only_bound_current(monkeypatch, tmp_path):
    monkeypatch.setattr(mp, "role_state", lambda **kw: {"llm": {
        "base_url": "https://api.openai.com/v1", "model": "private-finetune", "api_key_env": "MY_KEY",
    }})
    calls = []

    def fetch(base, **kw):
        calls.append((base, kw))
        return md.DiscoveryResult(("newest-model", "new-model"), "live")

    monkeypatch.setattr(md, "discover_models", fetch)
    rows = mp.live_model_rows("openai", role="llm", env={"MY_KEY": "synthetic"},
                              config=str(tmp_path / "config.yaml"))
    assert [row.value for row in rows] == ["private-finetune", "newest-model", "new-model"]
    assert rows[0].mark == "current/custom"
    assert rows[1].mark == "live"
    assert calls[0][1]["api_key"] == "synthetic"


def test_switching_provider_never_sends_previous_key_or_model(monkeypatch, tmp_path):
    monkeypatch.setattr(mp, "role_state", lambda **kw: {"llm": {
        "base_url": "https://api.openai.com/v1", "model": "old-openai", "api_key_env": "MY_KEY",
    }})
    calls = []

    def fetch(base, **kw):
        calls.append((base, kw))
        return md.DiscoveryResult(("new-model",), "live")

    monkeypatch.setattr(md, "discover_models", fetch)
    rows = mp.live_model_rows("groq", role="llm", env={"MY_KEY": "other-secret"},
                              config=str(tmp_path / "config.yaml"))
    assert [row.value for row in rows] == ["new-model"]
    assert calls[0][0] == "https://api.groq.com/openai/v1"
    assert calls[0][1]["api_key"] == ""


def test_cli_and_setup_use_same_live_picker(monkeypatch, tmp_path):
    from skaldir import model_cli, setup_cli

    calls = []

    def rows(provider, **kw):
        calls.append((provider, kw))
        return (mp.PickRow("1 fresh", "", "live", "fresh"),)

    monkeypatch.setattr(mp, "live_model_rows", rows)
    monkeypatch.setattr(model_cli, "_choose", lambda *a, **kw: kw["default"])
    monkeypatch.setattr(mp, "choose", lambda *a, **kw: kw["default_value"])
    config = str(tmp_path / "selected.yaml")
    assert model_cli._ask_model("llm", "groq", env={}, config=config,
                                base_url="http://localhost:12345/v1") == "fresh"
    assert setup_cli._choose_model("llm", "groq", supplied="obsolete", env={}, config=config,
                                   base_url="http://localhost:12345/v1") == "fresh"
    assert len(calls) == 2
    assert all(kw["config"] == config for _, kw in calls)
    assert all(kw["base_url"] == "http://localhost:12345/v1" for _, kw in calls)


def test_custom_url_does_not_receive_official_provider_key(monkeypatch, tmp_path):
    monkeypatch.setattr(mp, "role_state", lambda **kw: {})
    calls = []
    monkeypatch.setattr(md, "discover_models", lambda base, **kw: (
        calls.append(kw) or md.DiscoveryResult((), "unavailable")))
    rows = mp.live_model_rows("openai", role="llm", base_url="http://localhost:12345/v1",
                              env={"OPENAI_API_KEY": "synthetic-secret"},
                              config=str(tmp_path / "c.yaml"))
    assert calls[0]["api_key"] == ""
    assert rows and all(row.mark == "catalog/offline" for row in rows)


def test_chat_picker_uses_live_models(monkeypatch, tmp_path):
    from types import SimpleNamespace

    calls = []
    monkeypatch.setattr(mp, "role_state", lambda **kw: {})
    monkeypatch.setattr(mp, "live_model_rows", lambda provider, **kw: (
        calls.append((provider, kw)) or (mp.PickRow("1 new", "", "live", "brand-new"),)))
    answers = iter(["deepseek", "brand-new"])
    monkeypatch.setattr(mp, "select_in_frame", lambda *a, **kw: next(answers))
    monkeypatch.setattr(mp, "_ask_key_value", lambda *a, **kw: "")
    monkeypatch.setattr(mp, "_invalidate", lambda *a: None)
    monkeypatch.setattr(mp, "_repaint", lambda *a: None)
    front = SimpleNamespace(bar=SimpleNamespace(clear=lambda: None))
    plan, _ = mp._pick_in_frame(None, front, role="llm",
                                env={"SKALDIR_LLM_CONFIG": str(tmp_path / "c.yaml")})
    assert plan.model == "brand-new"
    assert calls and calls[0][0] == "deepseek"


