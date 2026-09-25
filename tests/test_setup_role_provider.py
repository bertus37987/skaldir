"""The provider question **per role**: the decision layer's own list, and its own current row.

The product has exactly two keys (``AGENTS.md``): a **TypeSafe/Jev provider** (the always-armed
decision layer) and **one LLM provider** (the general model). This file pins what that means for
the *question* ``skaldir setup`` and ``skaldir model`` ask about each of them:

* the decision layer is asked with the **decision-layer list** — the measured and local endpoints
  first, the rest marked as meant for the general model — while the general model's question keeps
  the catalogue's own order. The same words, in both verbs, from one code path;
* the row that question pre-selects is the provider **this role is already configured with**,
  matched back from the stored endpoint (a role is stored as an entry, never as a catalogue id);
* so ``Enter`` — which the banner explicitly tells the user to press — can never move a
  configured decision layer onto the general model's endpoint, and never resets it to the
  hard-coded catalogue default;
* a first run keeps its convenience: with nothing configured yet, the decision layer's question
  still defaults to the endpoint the user just chose for the general model;
* an endpoint that matches no catalogue row is never claimed to be one (``""``, not a guess).

No network, no key, no terminal: the config is written by the product's own writer
(``setup_flow.build_document`` + ``write_document``) and the picker is a recorder.
"""

from __future__ import annotations

import argparse
from typing import Any

import pytest

from skaldir import model_picker as mp
from skaldir import setup_cli as scli
from skaldir import setup_flow as sf

#: The variables the flow reads for a role's *provider* — a developer's shell must not answer.
_PROVIDER_ENV = (
    "SKALDIR_SETUP_JEV_PROVIDER",
    "GODSV_SETUP_JEV_PROVIDER",
    "SKALDIR_SETUP_LLM_PROVIDER",
    "GODSV_SETUP_LLM_PROVIDER",
    "SKALDIR_SETUP_JEV_BASE_URL",
    "SKALDIR_SETUP_LLM_BASE_URL",
    "SKALDIR_SETUP_JEV_MODEL",
    "SKALDIR_SETUP_LLM_MODEL",
    "SKALDIR_SETUP_JEV_API_KEY_ENV",
    "SKALDIR_SETUP_LLM_API_KEY_ENV",
)


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch):
    """Neither the developer's exports nor the real ``~/.hermes`` may answer a question here."""
    for name in _PROVIDER_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.delenv("GODSV_HOME", raising=False)
    monkeypatch.delenv("SKALDIR_LLM_CONFIG", raising=False)


def _spec(role: str, provider: str) -> Any:
    """One role's key exactly as the catalogue supplies it (endpoint, model, variable name)."""
    supplied = sf.provider_defaults(provider)
    return sf.key_spec(
        role,
        base_url=supplied["base_url"],
        model=supplied["model"] or f"stub-{provider}",
        api_key_env=supplied["api_key_env"],
    )


def _config(tmp_path, *, llm: str = "llamacpp", jev: str = "vllm") -> str:
    """A real config on disk, written by the product's own writer: two roles, two endpoints."""
    target = tmp_path / "config.yaml"
    document = sf.build_document(
        typesafe=_spec(sf.ROLE_TYPESAFE, jev),
        llm=_spec(sf.ROLE_LLM, llm),
        env={},
    )
    sf.write_document(document, target)
    return str(target)


def _args(config: str) -> argparse.Namespace:
    """The flags ``cmd_setup`` hands ``_collect``: nothing supplied, a real config path."""
    return argparse.Namespace(
        config=config,
        jev_provider="", jev_base_url="", jev_model="", jev_api_key_env="",
        llm_provider="", llm_base_url="", llm_model="", llm_api_key_env="",
        timeout=None,
    )


def _record(monkeypatch) -> list[dict[str, Any]]:
    """Answer every picker question with its own default (what ``Enter`` does) and record it."""
    seen: list[dict[str, Any]] = []

    def fake_choose(title: str, rows: Any, **kwargs: Any) -> str:
        seen.append(
            {
                "title": str(title),
                "first": rows[0].value if rows else "",
                "default": str(kwargs.get("default_value") or ""),
                "current": [row.value for row in rows if row.mark == "current"],
            }
        )
        return str(kwargs.get("default_value") or "")

    monkeypatch.setattr(scli.mp, "choose", fake_choose)
    return seen


def _scripted(monkeypatch, *, model: str = "stub-model") -> None:
    """The two readers a human would be: ``Enter`` for the endpoint, an id for the model.

    The endpoint prompt must get the empty line (its answer is the pre-filled row *by default*);
    the credential prompt gets nothing either, because both endpoints in these fixtures are
    loopback and the flow says so instead of asking for a key.
    """

    def line(prompt: str) -> str:
        return model if "model" in str(prompt).lower() else ""

    monkeypatch.setattr(scli, "_read_line", line)
    monkeypatch.setattr(scli, "_read_secret", lambda prompt: "")


# --------------------------------------------------------------------------- #
# 1. the endpoint → catalogue id match (no guessing)                            #
# --------------------------------------------------------------------------- #


def test_an_endpoint_maps_back_to_its_catalogue_row():
    assert sf.provider_id_for_url("https://api.deepseek.com/v1") == "deepseek"
    # a trailing slash and a different case are the same endpoint, not a different provider
    assert sf.provider_id_for_url("http://127.0.0.1:8000/v1/") == "vllm"
    assert sf.provider_id_for_url("HTTP://127.0.0.1:8000/V1") == "vllm"
    # anything the catalogue does not carry is "no provider id", never a neighbour
    assert sf.provider_id_for_url("https://example.invalid/v1") == ""
    assert sf.provider_id_for_url("") == ""


def test_a_role_reads_back_the_provider_it_is_configured_with(tmp_path):
    target = _config(tmp_path)
    assert sf.configured_provider(sf.ROLE_TYPESAFE, path=target, env={}) == "vllm"
    assert sf.configured_provider(sf.ROLE_LLM, path=target, env={}) == "llamacpp"
    # nothing on disk is "nothing configured" — the caller then uses the catalogue's default
    assert sf.configured_provider(sf.ROLE_TYPESAFE, path=str(tmp_path / "fresh.yaml"), env={}) == ""


# --------------------------------------------------------------------------- #
# 2. which list each role is asked with                                         #
# --------------------------------------------------------------------------- #


def test_the_decision_layer_is_asked_with_the_decision_layer_list():
    typesafe = mp.provider_rows(role=sf.ROLE_TYPESAFE)
    llm = mp.provider_rows(role=sf.ROLE_LLM)

    choices = [choice.id for choice in sf.provider_choices()]
    assert len(typesafe) == len(llm) == len(choices), "nothing is hidden"
    assert {row.value for row in typesafe} == set(choices)
    # Native TypeSafe leads; compatibility endpoints retain explicit preference rank.
    ids = [row.value for row in typesafe]
    front = [value for value in ids if value in mp.TYPESAFE_PREFERRED]
    assert front == [value for value in mp.TYPESAFE_PREFERRED if value in choices]
    assert front[0] == "typesafe"
    assert ids[: len(front)] == front, "the decision layer's rows are ordered, not just kept"
    # the general model's question is the catalogue's own order, untouched
    assert [row.value for row in llm] == [choice.id for choice in sf.provider_choices()]
    assert [row.value for row in llm][0] == "openai"


# --------------------------------------------------------------------------- #
# 3. the pre-selected row: the role's own provider, never the sibling's         #
# --------------------------------------------------------------------------- #


def test_enter_keeps_the_decision_layer_on_its_own_provider(tmp_path, monkeypatch):
    """The defect: the sibling role's endpoint was the pre-fill, so ``Enter`` moved the layer."""
    target = _config(tmp_path)
    seen = _record(monkeypatch)
    _scripted(monkeypatch)
    env_file = sf.read_env_file(tmp_path / sf.ENV_FILE_NAME)

    item = scli._collect(
        sf.ROLE_TYPESAFE,
        _args(target),
        interactive=True,
        env={},
        env_file=env_file,
        fallback_provider="llamacpp",  # what the flow passes after the general model's answer
    )

    assert seen, "the provider question was asked"
    assert seen[0]["default"] == "vllm", "the pre-fill is this role's own provider"
    assert seen[0]["current"] == ["vllm"], "and the row it is on is marked as the current one"
    assert seen[0]["first"] == "typesafe", "native decision provider leads the role's list"
    assert item.provider == "vllm"
    assert item.key is not None and item.key.base_url == sf.provider_defaults("vllm")["base_url"]


def test_enter_keeps_the_general_model_on_its_own_provider(tmp_path, monkeypatch):
    target = _config(tmp_path)
    seen = _record(monkeypatch)
    _scripted(monkeypatch)

    item = scli._collect(
        sf.ROLE_LLM,
        _args(target),
        interactive=True,
        env={},
        env_file=sf.read_env_file(tmp_path / sf.ENV_FILE_NAME),
    )

    assert seen[0]["default"] == "llamacpp"
    assert seen[0]["current"] == ["llamacpp"]
    assert item.provider == "llamacpp"


def test_a_first_run_still_defaults_the_second_key_to_the_endpoint_just_chosen(
    tmp_path, monkeypatch
):
    """One pass, two keys: with nothing configured, the sibling's answer is the pre-fill."""
    target = str(tmp_path / "fresh.yaml")
    seen = _record(monkeypatch)
    _scripted(monkeypatch)

    item = scli._collect(
        sf.ROLE_TYPESAFE,
        _args(target),
        interactive=True,
        env={},
        env_file=sf.read_env_file(tmp_path / sf.ENV_FILE_NAME),
        fallback_provider="llamacpp",
    )

    assert seen[0]["default"] == "llamacpp", "a fresh install keeps the one-pass convenience"
    assert seen[0]["current"] == [], "nothing is marked as the current row: nothing is configured"
    assert item.provider == "llamacpp"


def test_a_supplied_provider_flag_still_wins_over_the_configured_one(tmp_path, monkeypatch):
    """``--jev-provider`` is an instruction, not a default: it outranks what is on disk."""
    target = _config(tmp_path)
    seen = _record(monkeypatch)
    _scripted(monkeypatch)
    args = _args(target)
    args.jev_provider = "openrouter"
    args.jev_model = "stub-router"

    item = scli._collect(
        sf.ROLE_TYPESAFE, args, interactive=True, env={},
        env_file=sf.read_env_file(tmp_path / sf.ENV_FILE_NAME), fallback_provider="llamacpp",
    )

    assert item.provider == "openrouter"
    assert seen == [], "a complete flag answer asks nothing at all"
