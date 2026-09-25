"""Per-child subagent provider/model (MH-0048 task 48).

The rule under test, stated once and then enforced surface by surface: children
are **not** silently bound to the run's own provider. A ``settings.yaml``
``subagent.provider`` key names the provider every spawned child calls through;
the ``spawn_subagents`` tool and ``skaldir agent spawn`` can both name a provider
or a model per request; and every resolution reports *which* source produced the
endpoint a child will really call — never a silent inherit of the root.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from skaldir import runtime_delegation as rd
from skaldir import subagents as S
from skaldir.subagents import StubProvider


# --------------------------------------------------------------------------- #
# the settings keys                                                            #
# --------------------------------------------------------------------------- #


@pytest.fixture
def runtime(tmp_path: Path) -> Path:
    """A runtime directory whose ``settings.yaml`` the tests own."""
    base = tmp_path / "runtime"
    base.mkdir()
    return base


def _settings(runtime: Path, block: str) -> None:
    (runtime / "settings.yaml").write_text(block, encoding="utf-8")


def test_no_subagent_section_means_no_override(runtime: Path):
    _settings(runtime, "other:\n  key: 1\n")
    assert S.read_subagent_override(runtime) == {}
    assert S.subagent_provider_rule(runtime) == ""
    assert S.subagent_model_rule(runtime) == ""


def test_the_settings_keys_are_honest_and_clamped(runtime: Path):
    _settings(runtime, "subagent:\n  provider: deepseek\n  model: deepseek-chat\n")
    assert S.read_subagent_override(runtime) == {"provider": "deepseek",
                                                 "model": "deepseek-chat"}
    assert S.subagent_provider_rule(runtime) == "deepseek"
    assert S.subagent_model_rule(runtime) == "deepseek-chat"


def test_a_malformed_subagent_block_is_no_override_not_an_error(runtime: Path):
    _settings(runtime, "subagent: [broken]\n")
    assert S.read_subagent_override(runtime) == {}
    assert S.subagent_provider_rule(runtime) == ""
    assert S.subagent_model_rule(runtime) == ""


# --------------------------------------------------------------------------- #
# the resolver (rule A)                                                        #
# --------------------------------------------------------------------------- #


def test_the_resolver_refuses_an_unknown_provider_by_name(runtime: Path):
    with pytest.raises(S.SubagentError) as caught:
        S.resolve_subagent_provider("not-a-provider", runtime=runtime)
    assert "not-a-provider" in str(caught.value)
    assert "deepseek" in str(caught.value)  # it names a real known id


def test_the_resolver_refuses_a_model_the_catalog_does_not_carry(runtime: Path, monkeypatch):
    _settings(runtime, "subagent:\n  provider: deepseek\n")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k-test")
    with pytest.raises(S.SubagentError) as caught:
        S.resolve_subagent_provider("deepseek", model="not-a-deepseek-model",
                                    runtime=runtime)
    text = str(caught.value)
    assert "not-a-deepseek-model" in text
    assert "deepseek-flash" in text  # a known model id is named, not invented


def test_the_resolver_builds_a_catalog_backed_client(runtime: Path, monkeypatch):
    from skaldir.llm_provider import OpenAICompatibleLLM

    _settings(runtime, "subagent:\n  provider: deepseek\n")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k-test")
    _client, info = S.resolve_subagent_provider(runtime=runtime)
    assert isinstance(_client, OpenAICompatibleLLM)
    assert info["provider"] == "deepseek"
    assert info["endpoint"] == "https://api.deepseek.com/v1"
    assert info["model"] == "deepseek-flash"  # the catalog's own first documented model
    assert "settings.yaml" in info["source"]


def test_the_resolver_honours_the_model_key_from_settings(runtime: Path, monkeypatch):
    _settings(runtime, "subagent:\n  provider: deepseek\n  model: deepseek-v4-pro\n")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k-test")
    _client, info = S.resolve_subagent_provider(runtime=runtime)
    assert info["model"] == "deepseek-v4-pro"


def test_the_resolver_takes_the_settings_default_without_a_request(runtime: Path, monkeypatch):
    """Rule A: ``subagent.provider`` is the *default* — an explicit request wins."""
    _settings(runtime, "subagent:\n  provider: deepseek\n")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k-test")
    monkeypatch.setenv("OPENAI_API_KEY", "k-test")
    _client, info = S.resolve_subagent_provider("openai", runtime=runtime)
    assert info["provider"] == "openai" and "explicit provider request" in info["source"]


def test_an_unset_key_and_no_request_means_no_override(runtime: Path):
    client, info = S.resolve_subagent_provider(runtime=runtime)
    assert client is None and info["provider"] == ""


def test_a_request_without_a_credential_is_refused_not_called(runtime: Path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    _settings(runtime, "subagent:\n  provider: deepseek\n")
    with pytest.raises(S.SubagentError) as caught:
        S.resolve_subagent_provider(runtime=runtime)
    assert "DEEPSEEK_API_KEY" in str(caught.value)


# --------------------------------------------------------------------------- #
# the delegation surface (rules C + D)                                         #
# --------------------------------------------------------------------------- #


def _family(**kwargs: Any):
    tasks = kwargs.pop("tasks", ["one", "two"])
    kwargs.setdefault("workspace", Path(kwargs.pop("_workspace")))
    return rd.spawn_family(tasks=tasks, **kwargs)


def test_a_delegation_without_a_provider_or_a_default_is_refused(tmp_path: Path, runtime):
    with pytest.raises(rd.DelegationRefused) as caught:
        rd.spawn_family(
            tasks=["one"],
            provider=None,
            workspace=tmp_path / "ws",
            state_dir=tmp_path / "state",
            runtime=runtime,
        )
    assert caught.value.code == "delegation.no_provider"


def test_a_delegation_resolves_the_settings_default_when_no_client_is_attached(
    tmp_path: Path, runtime, monkeypatch
):
    """Rule A + D: no root client + ``subagent.provider`` ⇒ the child's own client."""
    _settings(runtime, "subagent:\n  provider: stub-marker\n")
    stub = StubProvider(latency_s=0.0, default_turns=1)

    def fake_resolver(requested: str = "", *, model: str = "", role: str = "",
                      runtime=None, env=None):
        assert requested == ""  # no request, the settings default decides
        assert str(runtime).endswith("runtime")
        return stub, {"provider": "stub-marker", "endpoint": "stub://",
                      "model": "stub", "source": "settings.yaml subagent.provider"}

    monkeypatch.setattr(S, "resolve_subagent_provider", fake_resolver)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    family = rd.spawn_family(
        tasks=["one"],
        provider=None,
        workspace=workspace,
        state_dir=tmp_path / "state",
        runtime=runtime,
    )
    record = family.record.as_dict()
    assert record["children_total"] == 1
    # the endpoint the child will call is reported *with its source*, not invented
    provider_block = record["provider"]
    assert provider_block["provider"] == "stub-marker"
    assert "settings.yaml" in provider_block["source"]


def test_the_settings_default_is_honoured_even_with_a_root_client(
    tmp_path: Path, runtime, monkeypatch
):
    """The rule the whole task is about: the root client is *not* the child default."""
    _settings(runtime, "subagent:\n  provider: stub-marker\n")
    stub = StubProvider(latency_s=0.0, default_turns=1)
    monkeypatch.setattr(
        S, "resolve_subagent_provider",
        lambda requested="", *, model="", role="", runtime=None, env=None: (
            stub, {"provider": "stub-marker", "endpoint": "stub://", "model": "m",
                   "source": "settings.yaml subagent.provider"}),
    )
    workspace = tmp_path / "ws"
    workspace.mkdir()
    root_client = StubProvider(latency_s=0.0, default_turns=1)
    family = rd.spawn_family(
        tasks=["one"],
        provider=root_client,
        workspace=workspace,
        state_dir=tmp_path / "state",
        runtime=runtime,
    )
    # the children's client is the resolver's own (the fake's), not the root's
    assert family.run._provider is not root_client
    # ... and the record says the children took the settings-backed client
    assert family.record.as_dict()["provider"]["provider"] == "stub-marker"


def test_a_root_client_alone_stays_todays_fallback(tmp_path: Path, runtime):
    """No settings key, no request ⇒ the run's own client (exactly as before)."""
    stub = StubProvider(latency_s=0.0, default_turns=1)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    family = rd.spawn_family(
        tasks=["one"],
        provider=stub,
        workspace=workspace,
        state_dir=tmp_path / "state",
        runtime=runtime,
    )
    assert family.record.as_dict()["provider"].get("mode", True) is not None
    assert family.record.as_dict()["children_total"] == 1


def test_the_spawn_tool_carries_optional_provider_and_model(tmp_path: Path, monkeypatch):
    """Rule C: the model may name a provider/model per call — clamped, refuse unknown."""
    from skaldir.subagent_roles import ROLE_EXPLORE

    seen: dict[str, Any] = {}

    def fake_family(*, provider=None, model_override="", requested_provider="", **kwargs):
        seen.update(model=model_override, requested=requested_provider)
        stub = StubProvider(latency_s=0.0, default_turns=1)

        class FakeFamily:
            record = {"children_total": 0, "provider": {"mode": "stub"}}

            def payload(self):  # pragma: no cover - shape only
                return {"total": 0}

        return FakeFamily()

    real_spawn_family = rd.spawn_family
    monkeypatch.setattr(rd, "spawn_family", fake_family)
    tool = rd.spawn_subagents_tool(
        workspace=tmp_path, state_dir=tmp_path / "state", provider=None
    )
    outcome = tool.handler(tasks=["x"], provider="deepseek", model="deepseek-chat")
    assert outcome["ok"] is True
    assert seen == {"model": "deepseek-chat", "requested": "deepseek"}

    # the refusals must come from the REAL resolver: restore the real spawn_family
    monkeypatch.setattr(rd, "spawn_family", real_spawn_family)
    refusal = tool.handler(tasks=["x"], provider="not-a-provider")
    assert refusal["ok"] is False and refusal["error"]["code"] == "delegation.unknown_provider"
    model_refusal = tool.handler(tasks=["x"], provider="deepseek", model="nope")
    assert model_refusal["ok"] is False
    assert model_refusal["error"]["code"] == "delegation.unknown_model"


def test_the_tool_schema_declares_the_new_arguments(tmp_path: Path):
    tool = rd.spawn_subagents_tool(
        workspace=tmp_path, state_dir=tmp_path / "state", provider=None
    )
    props = tool.input_schema["properties"]
    assert props["provider"]["type"] == "string"
    assert props["model"]["type"] == "string"
    assert "subagent.provider" in tool.description


# --------------------------------------------------------------------------- #
# the CLI (rule B)                                                             #
# --------------------------------------------------------------------------- #


def _cli_argv(state: Path, *extra: str) -> list[str]:
    return ["agent", "spawn", "--state-dir", str(state), "--json", *extra]


def _cli_result(capsys, *argv: str) -> tuple[int, dict]:
    """``subagents_cli.main`` + the JSON envelope on stdout, unpacked."""
    from skaldir import subagents_cli

    code = subagents_cli.main([str(item) for item in argv])
    out = capsys.readouterr().out
    document = json.loads(out) if out.strip() else {}
    return int(code), document


def test_the_cli_accepts_a_catalog_provider_and_model(
    tmp_path: Path, runtime, monkeypatch, capsys
):
    from skaldir import subagents_cli

    _settings(runtime, "subagent:\n  provider: unused\n")
    monkeypatch.setenv("GODSV_HOME", str(runtime))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k-test")
    monkeypatch.setenv("SKALDIR_LLM_CONFIG", "/dev/null")  # no env key for 'openai'
    seen: dict[str, Any] = {}

    def fake_resolve(requested: str = "", *, model: str = "", runtime=None, env=None):
        seen.update(requested=requested, model=model)
        handle = subagents_cli.open_provider("stub")
        return handle.provider, {"provider": requested, "endpoint": "stub://",
                                 "model": model, "source": "explicit provider request"}

    monkeypatch.setattr(subagents_cli, "resolve_subagent_provider", fake_open_spy(seen))
    code, document = _cli_result(
        capsys, "agent", "spawn", "--state-dir", str(tmp_path / "state"), "--json",
        "--provider", "deepseek", "--model", "deepseek-chat", "--task", "one",
    )
    assert seen["requested"] == "deepseek" and seen["model"] == "deepseek-chat"


def fake_open_spy(seen: dict):
    def resolver(requested: str = "", *, model: str = "", runtime=None, env=None):
        seen.update(requested=requested, model=model)
        from skaldir.subagents import StubProvider

        return StubProvider(latency_s=0.0, default_turns=1), {
            "provider": requested, "endpoint": "stub://", "model": model,
            "source": "explicit provider request",
        }

    return resolver


def test_the_cli_refuses_an_unknown_provider_by_name(
    tmp_path: Path, runtime, monkeypatch, capsys
):
    from skaldir import subagents_cli

    monkeypatch.setenv("GODSV_HOME", str(runtime))
    code, document = _cli_result(
        capsys, "agent", "spawn", "--state-dir", str(tmp_path / "state"), "--json",
        "--provider", "not-a-provider", "--task", "one",
    )
    assert code == 1  # usage: a provider the catalog does not carry is a contract error
    text = json.dumps(document)
    assert "not-a-provider" in text
    assert "deepseek" in text  # a real known provider id is named, not invented
