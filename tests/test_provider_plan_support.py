"""Plan/subscription support: one row per SKU, key-prefix routing, honest quota errors.

Owner ask (2026-09-23): "plan support für Opencode go und andere plan based
subscriptions codex usw". Measured root of the Xiaomi 401: a ``tp-`` Token-Plan key
on the pay-as-you-go host is a 401 that looks like a bad key. The plan is a
first-class pick, never a silent default.
"""

from __future__ import annotations

from skaldir import model_picker as mp
from skaldir import provider_registry_data as rd
from skaldir import setup_flow as sf


def test_endpoints_carry_a_sku_and_key_prefixes_where_the_plans_differ():
    xiaomi = rd.REGISTRY["xiaomi-mimo"]
    skus = {ep.sku for ep in xiaomi.plan_endpoints()}
    assert rd.SKU_PAY_AS_YOU_GO in skus and rd.SKU_TOKEN_PLAN in skus
    token = next(ep for ep in xiaomi.plan_endpoints() if ep.sku == rd.SKU_TOKEN_PLAN)
    assert token.matches_key("tp-everything")
    assert not token.matches_key("sk-something")


def test_pick_endpoint_routes_a_tp_key_to_the_token_plan_host():
    spec = rd.REGISTRY["xiaomi-mimo"]
    assert spec.pick_endpoint(key="tp-e123").base_url.endswith("token-plan-cn.xiaomimimo.com/v1")
    assert spec.pick_endpoint(key="other").base_url.endswith("api.xiaomimimo.com/v1")
    assert spec.pick_endpoint(sku="token-plan").base_url.endswith("token-plan-cn.xiaomimimo.com/v1")


def test_multi_sku_providers_expand_to_one_pickable_row_per_plan():
    rows = sf.provider_choices()
    ids = [row.id for row in rows]
    assert "xiaomi-mimo" in ids and "xiaomi-mimo#token-plan" in ids
    assert "zai" in ids and "zai#coding-plan" in ids
    assert "openai-codex" in ids and "openai-codex#chatgpt-plan" in ids
    # single-SKU subscription stays a plain id (OpenCode Go)
    assert "opencode-go" in ids
    assert len(ids) == len(set(ids)), "a plan row is never a silent duplicate"


def test_plan_for_accepts_a_plan_row_id():
    plan = mp.plan_for("llm", provider="xiaomi-mimo#token-plan", model="mimo-v2.5-pro")
    assert plan.provider == "xiaomi-mimo"
    assert "token-plan-cn" in plan.base_url
    plan = mp.plan_for("llm", provider="zai#coding-plan", model="glm-4.5")
    assert "coding" in plan.base_url
    plan = mp.plan_for("llm", provider="opencode-go", model="deepseek-v4.1-flash")
    assert plan.base_url.endswith("opencode.ai/zen/go/v1")


def test_the_chatgpt_plan_row_is_never_the_general_chat_provider():
    import pytest

    from skaldir.setup_flow import SetupRefused

    with pytest.raises(SetupRefused) as exc:
        mp.plan_for("llm", provider="openai-codex#chatgpt-plan", model="gpt-5.1-codex")
    assert "not an OpenAI-shaped chat endpoint" in str(exc.value)
    assert "openai-codex" in str(exc.value)


def test_unknown_plan_sku_is_refused_by_name():
    import pytest

    from skaldir.setup_flow import SetupRefused

    with pytest.raises(SetupRefused) as exc:
        sf.provider_choice("xiaomi-mimo#enterprise")
    assert "no plan SKU" in str(exc.value)
    assert "token-plan" in str(exc.value)


def test_model_lists_are_the_provider_s_own_even_for_a_plan_row():
    # `xiaomi-mimo#token-plan` used to look up CATALOG with the #sku id → empty
    base = mp.model_choices("xiaomi-mimo")
    plan = mp.model_choices("xiaomi-mimo#token-plan")
    assert base and plan
    assert [item.id for item in base] == [item.id for item in plan]
    rows = mp.model_rows("xiaomi-mimo#token-plan")
    assert rows and rows[0].value
    # newest first: mimo-v2.6-pro is the default a first Enter takes
    assert base[0].id == "mimo-v2.6-pro" and base[0].default is True


def test_plan_is_auto_detected_from_an_existing_tp_key(monkeypatch):
    monkeypatch.setenv("MIMO_API_KEY", "tp-everything")
    defaults = sf.provider_defaults("xiaomi-mimo")
    assert "token-plan-cn" in defaults["base_url"], defaults
    monkeypatch.setenv("MIMO_API_KEY", "sk-pay-as-you-go")
    defaults = sf.provider_defaults("xiaomi-mimo")
    assert defaults["base_url"].endswith("api.xiaomimimo.com/v1"), defaults


def test_the_live_desktop_gate_opens_the_approve_bar_when_a_shell_is_installed():
    from skaldir import cut as cut_mod

    class FakeShell:
        interactive = True
        cu_live_desktop_allowed = ""

        def pick(self, chooser):
            chooser.handle("right")  # → allow for this session
            chooser.handle("enter")
            return chooser.result()

        def ask(self, _prompt, *, default=""):
            return default

        def say(self, _text=""):
            pass

    shell = FakeShell()
    cut_mod.install_gate(shell)
    try:
        decision = cut_mod.maybe_approve_live(goal="open a browser")
        assert decision["granted_now"] is True
        assert decision["decision"] == "session_allow"
    finally:
        cut_mod.install_gate(None)


def test_without_a_shell_the_gate_stays_closed():
    from skaldir import cut as cut_mod

    cut_mod.install_gate(None)
    decision = cut_mod.maybe_approve_live(goal="open a browser")
    assert decision["granted_now"] is False
    assert decision["prompted"] is False


def test_auth_failure_retries_the_other_plan_sku_once():
    """Auto PAYG vs token-plan when the key prefix is unclear — one retry, never two."""
    from skaldir import model_picker as mp

    calls: list[str] = []

    def prober(spec, *, env, timeout_s=None, measure_streaming=True):
        calls.append(spec.base_url)
        if "token-plan-cn" in spec.base_url:
            return {"ok": True, "model_echo": "mimo", "status": 200}
        return {"ok": False, "problem": "HTTP 401 Invalid API Key", "status": 401}

    from dataclasses import replace as dc_replace

    plan = mp.plan_for("llm", provider="xiaomi-mimo", model="mimo-v2.6-pro")
    plan = dc_replace(plan, base_url="https://api.xiaomimimo.com/v1")
    payload, code = mp.apply_choice(
        plan, key_value="sk-not-a-prefix", env={"HOME": "/tmp/gv-w1-final2"},
        config_path="/tmp/gv-w1-final2/no-write.yaml", write_env=False, write_config=False,
        prober=prober,
    )
    assert code == 0 or payload.get("ok") or payload.get("sku_auto"), (code, payload.get("problem"))
    assert any("token-plan-cn" in url for url in calls), calls


def test_key_prompt_asks_keep_skip_replace_before_the_hidden_input():
    """Owner: Enter must not both 'keep the old key' and 'confirm the typed key'."""
    from skaldir import model_picker as mp

    for letter, want in (("k", "keep"), ("K", "keep"), ("s", "skip"), ("r", "replace"),
                         ("", "keep")):
        got = mp.ask_existing_key(
            {"available": True, "source": "the .env"},
            name="MIMO_API_KEY",
            say=lambda _t: None,
            read=lambda _p: letter,
        )
        assert got == want, (letter, got)

    # the hidden prompt after 'replace' means: Enter confirms the typed key
    typed = mp._ask_key_value.__doc__
    assert "Enter confirms" in typed
    assert "Enter keeps the one" not in typed
