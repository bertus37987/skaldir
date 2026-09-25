"""Jev is **always-armed**: a configured decision layer must not answer "provider disabled".

The measured defect this file closes (audit 12 §3): ``skaldir jev decide`` carried
``--provider`` with ``default=disabled``, so a fully configured installation — one whose
``skaldir setup`` had already validated both keys live — got ``exit 3`` with
``refusal_reason: provider_disabled`` and ``fallback: keep the full transcript``. The
product's own runtime flags were never read, and neither was the ``godsv.jev`` block the
setup flow had just written and verified.

The rule pinned here: with **no flags at all** the verb runs the provider the installation
is configured with — the runtime's ``settings.yaml`` flags when they name one, else the
decision block the config file carries (and *which* side of the line that block is on: the
System One endpoint, or the LLM compatibility backend). ``--provider disabled`` stays
selectable and stays honest; it is simply no longer the default, and when nothing is
configured the refusal names that instead of pretending a decision happened.

Nothing live is called here: the provider config is a written file and the settings are a
written file, so every assertion is about a resolution, never about a network answer.
"""

from __future__ import annotations

from types import SimpleNamespace

import yaml

from skaldir import setup_flow as sf
from skaldir import system_one as so
from skaldir import system_one_adapters as soa
from skaldir.jev import cli as jev_cli
from skaldir.jev import runtime as jev_runtime

NATIVE_BLOCK = {
    "provider": "openai-compatible",
    "base_url": soa.TYPESAFE_ENDPOINT,
    "model": "jev-latest",
    "api_key_env": "TYPESAFE_API_KEY",
    sf.SYSTEM_ONE_KEY: sf.SYSTEM_ONE_NATIVE,
}
COMPAT_BLOCK = {
    "provider": "openai-compatible",
    "base_url": "https://api.deepseek.com/v1",
    "model": "deepseek-chat",
    "api_key_env": "DEEPSEEK_API_KEY",
    sf.SYSTEM_ONE_KEY: sf.SYSTEM_ONE_COMPATIBILITY,
}


def _config(tmp_path, block, name="config.yaml"):
    path = tmp_path / name
    path.write_text(
        yaml.safe_dump({"godsv": {"jev": dict(block)}}), encoding="utf-8"
    )
    return path


def _settings(tmp_path, **flags):
    path = tmp_path / "settings.yaml"
    section = {"enabled": True, "provider": so.PROVIDER_LLM_ADAPTER}
    section.update(flags)
    path.write_text(yaml.safe_dump({"system_one": section}), encoding="utf-8")
    return path


def test_the_decide_verb_does_not_default_to_a_disabled_provider():
    parser = jev_cli.build_parser()
    args = parser.parse_args(["decide", "--kind", "tool_routing"])
    assert args.provider != so.PROVIDER_DISABLED, (
        "a configured decision layer was answering exit 3 because the verb defaulted to "
        "`disabled`"
    )
    assert args.provider == jev_cli.PROVIDER_SETTINGS
    assert jev_cli.PROVIDER_SETTINGS in jev_cli.PROVIDER_CHOICES
    assert so.PROVIDER_DISABLED in jev_cli.PROVIDER_CHOICES, "still selectable, never a trap"
    assert so.PROVIDER_TYPESAFE in jev_cli.PROVIDER_CHOICES


def test_the_runtime_settings_arm_the_verb(tmp_path):
    _settings(tmp_path, provider=so.PROVIDER_LLM_ADAPTER)
    flags, provenance = jev_runtime.configured_provider_flags(runtime=tmp_path, env={})
    assert flags.enabled is True
    assert flags.provider == so.PROVIDER_LLM_ADAPTER
    assert provenance["source"] == "settings"


def test_the_armed_flags_come_back_whole(tmp_path):
    """Every hook the owner armed is read back — the resolution never trims the flags.

    ``goal_completion`` / ``completion_precheck`` / ``recovery`` / ``computer_use`` /
    ``llm_fallback`` / ``voice_triage`` are the events the decision layer is consulted for
    (GAP-J2). They are read from the file verbatim: a resolution that dropped one would
    silently disarm an event the owner turned on, which is the same defect from the other
    side.
    """
    _settings(
        tmp_path,
        goal_completion=True,
        completion_precheck=True,
        recovery=True,
        computer_use=True,
        llm_fallback=True,
        voice_triage=True,
    )
    flags, provenance = jev_runtime.configured_provider_flags(runtime=tmp_path, env={})
    assert flags.goal_completion is True
    assert flags.completion_precheck is True
    assert flags.recovery is True
    assert flags.computer_use is True
    assert flags.llm_fallback is True
    assert flags.voice_triage is True
    assert flags.shadow_mode is False, "arming an event is not the same as shadow mode"
    assert provenance["source"] == "settings"


def test_a_settings_file_that_says_nothing_falls_back_to_the_configured_block(tmp_path):
    """The block ``skaldir setup`` validated live is the installation's decision provider."""
    _settings(tmp_path, enabled=False, provider=so.PROVIDER_DISABLED)
    config = _config(tmp_path, COMPAT_BLOCK)
    flags, provenance = jev_runtime.configured_provider_flags(
        runtime=tmp_path, config_path=config, env={}
    )
    assert flags.provider == so.PROVIDER_LLM_ADAPTER
    assert provenance["source"] == jev_runtime.SOURCE_TYPESAFE
    assert provenance["decision_layer"] == sf.SYSTEM_ONE_COMPATIBILITY


def test_a_native_decision_block_arms_the_typesafe_provider(tmp_path):
    """The other side of the line: the System One endpoint is not the LLM adapter."""
    _settings(tmp_path, enabled=False, provider=so.PROVIDER_DISABLED)
    config = _config(tmp_path, NATIVE_BLOCK)
    flags, provenance = jev_runtime.configured_provider_flags(
        runtime=tmp_path, config_path=config, env={}
    )
    assert flags.provider == so.PROVIDER_TYPESAFE
    assert provenance["decision_layer"] == sf.SYSTEM_ONE_NATIVE


def test_nothing_configured_still_refuses_honestly(tmp_path):
    config = tmp_path / "missing.yaml"
    flags, provenance = jev_runtime.configured_provider_flags(
        runtime=tmp_path, config_path=config, env={}
    )
    assert flags.provider == so.PROVIDER_DISABLED
    assert provenance["reason"], "a disabled resolution must say why it is disabled"


def test_a_native_block_never_builds_an_openai_chat_client(tmp_path):
    """The substitution, spelled out: an OpenAI-shaped client against /v1/systemone.

    ``provider_resolution`` used to hand any ``godsv.jev`` block to ``provider_for()``,
    which builds a chat client — so the decision endpoint would have been called with a
    ``/chat/completions`` request it does not have.
    """
    config = _config(tmp_path, NATIVE_BLOCK)
    resolution = jev_runtime.provider_resolution(config_path=config, env={})
    assert resolution["source"] == jev_runtime.SOURCE_TYPESAFE
    assert resolution["decision_layer"] == sf.SYSTEM_ONE_NATIVE
    assert resolution["client"] is None
    assert soa.TYPESAFE_ENDPOINT in resolution["problem"]
    assert "credential" in resolution["problem"], (
        "the native path is blocked by its credential (Treasury handle), and says so"
    )
    assert "chat" in resolution["problem"], "and it says why no chat client was built"


def test_a_compatibility_block_still_builds_the_llm_client(tmp_path):
    """The compatibility backend keeps working — it is a supported configuration, not a bug."""
    config = _config(tmp_path, COMPAT_BLOCK)
    resolution = jev_runtime.provider_resolution(
        config_path=config, env={"DEEPSEEK_API_KEY": "test-value-not-a-credential"}
    )
    assert resolution["decision_layer"] == sf.SYSTEM_ONE_COMPATIBILITY
    assert resolution["problem"] == ""
    assert resolution["client"] is not None, "the stand-in path still gets its chat client"


def test_the_cli_default_asks_the_runtime_for_the_armed_provider(tmp_path, monkeypatch):
    _settings(tmp_path, provider=so.PROVIDER_LLM_ADAPTER)
    args = SimpleNamespace(
        provider=jev_cli.PROVIDER_SETTINGS, shadow=False, runtime=str(tmp_path), config=""
    )
    flags = jev_cli._flags_for(args)
    assert flags.enabled is True
    assert flags.provider == so.PROVIDER_LLM_ADAPTER

    # and the explicit spells still win, unchanged
    args = SimpleNamespace(
        provider=so.PROVIDER_DISABLED, shadow=False, runtime=str(tmp_path), config=""
    )
    assert jev_cli._flags_for(args).provider == so.PROVIDER_DISABLED


def test_the_decide_verb_takes_the_runtime_and_config_pins():
    args = jev_cli.build_parser().parse_args(
        ["decide", "--kind", "tool_routing", "--runtime", "/tmp/rt", "--config", "/tmp/c.yaml"]
    )
    assert args.runtime == "/tmp/rt"
    assert args.config == "/tmp/c.yaml"
