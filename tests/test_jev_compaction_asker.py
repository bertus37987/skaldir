"""Compaction is judged by the **decision layer's own** provider, not by the general model.

The measured defect (audit 12 §3, GAP-J3): ``context_manager.build_provider_asker`` called
``skaldir.llm.provider_for()``, which reads ``godsv.llm`` — the general model — while the
decision layer's own block is ``godsv.jev``, written and validated by ``skaldir setup``. So
the credential an owner configured *for Jev* was read by exactly one caller (the setup flow),
and the keep/shorten/drop decisions of a compaction were answered by whatever chat model the
general role happened to name.

The rule pinned here is the runtime's own, in the runtime's own order
(``skaldir.jev.runtime.provider_resolution``): ``godsv.jev`` →
``godsv.providers.entries.jev`` → ``godsv.llm``, and **a decision block that is present but
unusable blocks by name** — it is never quietly replaced by the general endpoint (that
substitution is exactly how a TypeSafe role becomes an LLM role).

No live call is made: the config is a written file and the assertions are about which block
the asker resolved to.
"""

from __future__ import annotations

import pytest
import yaml

from skaldir import context_manager as cm
from skaldir import setup_flow as sf
from skaldir import system_one_adapters as soa

COMPAT_BLOCK = {
    "provider": "openai-compatible",
    "base_url": "https://api.deepseek.com/v1",
    "model": "deepseek-chat",
    "api_key_env": "DEEPSEEK_API_KEY",
    sf.SYSTEM_ONE_KEY: sf.SYSTEM_ONE_COMPATIBILITY,
}
NATIVE_BLOCK = {
    "provider": "openai-compatible",
    "base_url": soa.TYPESAFE_ENDPOINT,
    "model": "jev-latest",
    "api_key_env": "TYPESAFE_API_KEY",
    sf.SYSTEM_ONE_KEY: sf.SYSTEM_ONE_NATIVE,
}
GENERAL_BLOCK = {
    "provider": "openai-compatible",
    "base_url": "https://api.deepseek.com/v1",
    "model": "deepseek-flash",
    "api_key_env": "DEEPSEEK_API_KEY",
}
ENV = {"DEEPSEEK_API_KEY": "test-value-not-a-credential"}


def _config(tmp_path, *, jev=None, llm=GENERAL_BLOCK):
    document: dict = {"godsv": {}}
    if jev is not None:
        document["godsv"]["jev"] = dict(jev)
    if llm is not None:
        document["godsv"]["llm"] = dict(llm)
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    return path


def test_the_compaction_asker_reads_the_jev_block_first(tmp_path):
    """The decision block's model, not the general one — measured on the asker itself."""
    config = _config(tmp_path, jev=COMPAT_BLOCK)
    asker = cm.build_provider_asker(config_path=config, env=ENV)
    assert isinstance(asker, cm.ProviderJevAsker)
    assert asker.source == "godsv.jev"
    assert asker.decision_layer == sf.SYSTEM_ONE_COMPATIBILITY
    assert asker.model == "deepseek-chat"  # the JEV block
    assert asker.model != GENERAL_BLOCK["model"]


def test_without_a_decision_block_the_general_provider_is_the_documented_fallback(tmp_path):
    config = _config(tmp_path, jev=None)
    asker = cm.build_provider_asker(config_path=config, env=ENV)
    assert asker.source == "godsv.llm"
    assert asker.model == GENERAL_BLOCK["model"]
    assert asker.decision_layer == ""


def test_a_native_decision_block_is_never_silently_replaced(tmp_path):
    """TypeSafe configured but without a credential = a named blocker, not the general model."""
    config = _config(tmp_path, jev=NATIVE_BLOCK)
    with pytest.raises(cm.AskerUnavailable) as failure:
        cm.build_provider_asker(config_path=config, env=ENV)
    problem = str(failure.value)
    assert soa.TYPESAFE_ENDPOINT in problem
    assert "credential" in problem
    assert GENERAL_BLOCK["base_url"] not in problem, (
        "the general provider is not the answer to a decision block that cannot run"
    )


def test_an_explicit_client_is_still_taken_as_it_is():
    """One construction path: a caller that owns the client owns the provenance."""
    asker = cm.build_provider_asker(client=object())
    assert isinstance(asker, cm.ProviderJevAsker)
    assert asker.source == "explicit"
