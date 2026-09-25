"""The TypeSafe/Jev decision provider as a **product** choice (owner gap T1).

What this file pins, in the owner's words: *"setup = exactly TypeSafe key + one LLM, both
validated live"* — and the TypeSafe half must be **TypeSafe**, not whichever language model
happens to be cheap that day. Before this file the only TraceSafe strings in the tree were
two constants in :mod:`skaldir.system_one_adapters` that **no product path read**: the
catalogue did not carry the endpoint, so ``skaldir provider catalog`` could not name it, the
picker offered ordinary LLM rows for the decision role, and ``skaldir setup`` wrote
``provider: openai-compatible`` whatever the user chose.

Every claim here is sourced, never invented:

* the request/response shape and the ``state: string | object | array`` claim come from the
  fetched page ``https://docs.typesafe.ai/api`` (recorded in
  :data:`skaldir.provider_registry_data.FETCHED_DOCS`), fetched on 2026-09-19;
* the reachability measurement is a **live, credential-free** POST this round:
  ``{"detail":{"error_type":"authentication_error","message":"Must supply an API key! ..."}}``
  with HTTP ``403`` — an auth gate, not a dead name, and explicitly **not** raised to
  ``verified`` because no authenticated call was made (there is no TypeSafe credential on
  this host: no ``TYPESAFE_API_KEY``, no ``secret://typesafe/systemone`` handle);
* ``TYPESAFE_API_KEY`` is the variable name the public LiteLLM pass-through documents for
  this endpoint; the repo's own Treasury handle is ``secret://typesafe/systemone``.

The whole point of the file: the decision role can select TypeSafe, the endpoint is
**not** offered as a chat provider to anyone, and the LLM that stands in for Jev stays
selectable — but labelled as the compatibility backend it is.
"""

from __future__ import annotations

import pytest
import yaml

from skaldir import model_picker as mp
from skaldir import provider_catalog as catalog
from skaldir import provider_registry_data as rd
from skaldir import setup_flow as sf
from skaldir import system_one_adapters as soa

#: The docs page the request contract was read from, in this round.
TYPESAFE_DOCS = "https://docs.typesafe.ai/api"


def test_the_catalogue_carries_the_real_typesafe_endpoint():
    """The endpoint stops being a constant nobody reads — it is a catalogue row."""
    assert "typesafe" in rd.REGISTRY_ORDER
    spec = rd.registry_entry("typesafe")
    endpoint = spec.primary_endpoint

    # the two strings come from the adapter that speaks them, so they cannot drift
    assert endpoint.base_url == soa.TYPESAFE_ENDPOINT
    assert spec.default_model == soa.TYPESAFE_MODEL == "jev-latest"

    # and it is NOT an OpenAI chat endpoint: nothing may ever hand it to that client
    assert endpoint.kind == rd.KIND_NATIVE
    assert endpoint.openai_compatible is False
    assert endpoint.chat_path == ""
    assert endpoint.url == soa.TYPESAFE_ENDPOINT

    assert endpoint.api_key_env[0] == "TYPESAFE_API_KEY"
    assert spec.docs.startswith("https://docs.typesafe.ai")
    assert spec.label


def test_every_typesafe_claim_is_sourced_and_nothing_is_called_verified():
    """A documented promise is not a measurement — and no credential means no probe."""
    spec = rd.registry_entry("typesafe")

    # the page was really fetched this round, and the registry says so
    assert TYPESAFE_DOCS in rd.FETCHED_DOCS
    assert spec.primary_endpoint.fetched is True
    assert spec.primary_endpoint.reference == TYPESAFE_DOCS
    assert spec.primary_endpoint.status == rd.DOCUMENTED

    # no authenticated call happened → not verified, and the reason is written down
    assert spec.verification == rd.DOCUMENTED
    assert spec.probe == rd.PROBE_NOT_RUN
    assert spec.probe_reason, "an unprobed entry must name why it is unprobed"

    # the state-shape claim the adapter relies on is the page's own words
    assert "string | object | array" in spec.to_dict()["endpoints"][0]["note"] or any(
        "string | object | array" in quirk for quirk in spec.quirks
    )


def test_the_credential_free_reachability_measurement_is_recorded_verbatim():
    """The 403 is evidence of an auth gate; it is kept as measured, not as a theorem."""
    steps = [step for step in rd.PROBE_LOG if step.provider == "typesafe"]
    assert steps, "the unauthenticated POST that answered 403 must be in the probe log"
    measured = [step for step in steps if step.status == 403]
    assert measured, "the measured status of that call was 403"
    evidence = " ".join(step.evidence for step in measured)
    assert "authentication_error" in evidence
    assert "Must supply an API key" in evidence
    assert all(step.ok is False for step in measured), "a refusal is not a success"


def test_the_picker_offers_typesafe_for_the_decision_role():
    """``provider_choices`` is the picker's one source — TypeSafe has to be in it."""
    choices = {choice.id: choice for choice in sf.provider_choices()}
    assert "typesafe" in choices
    row = choices["typesafe"]
    assert row.base_url == soa.TYPESAFE_ENDPOINT
    assert row.model == "jev-latest"
    assert row.api_key_env == "TYPESAFE_API_KEY"
    assert row.speakable is False, (
        "the OpenAI-shaped client cannot speak /v1/systemone — a row that says otherwise "
        "would send a chat completion to a decision endpoint"
    )


def test_typesafe_can_never_become_a_chat_chain_step():
    """Honest chain: offered, and skipped with a named reason, never silently dropped."""
    candidate = catalog.candidate_from_registry("typesafe")
    assert candidate.kind == catalog.KIND_NOT_COMPATIBLE
    [step] = catalog.build_chain([candidate], env={})
    assert step.status == catalog.STATUS_SKIPPED
    assert step.reason == catalog.SKIP_CLIENT_UNSUPPORTED


def test_the_picker_prefers_typesafe_for_the_decision_role_and_names_the_stand_in():
    """Selection is explicit: the native endpoint first, an LLM stand-in labelled."""
    rows = mp.provider_rows(role=sf.ROLE_TYPESAFE)
    order = [row.value for row in rows]
    assert order[0] == "typesafe"
    assert order.index("typesafe") < order.index("deepseek")

    typesafe_row = rows[0]
    assert typesafe_row.description.startswith(soa.TYPESAFE_ENDPOINT)
    assert "TypeSafe" in typesafe_row.name

    stand_in = next(row for row in rows if row.value == "deepseek")
    text = f"{stand_in.name} {stand_in.description}".lower()
    assert "compatibility" in text or "stand-in" in text, (
        "an ordinary LLM answering Jev's questions is a compatibility backend and the row "
        "has to say so"
    )

    # and the general role is told the reverse: this row cannot answer chat
    llm_rows = {row.value: row for row in mp.provider_rows(role=sf.ROLE_LLM)}
    assert "cannot answer chat" in llm_rows["typesafe"].description


def test_the_decision_role_writes_which_side_of_the_line_it_chose(tmp_path):
    """The block has to record the decision provider — otherwise the runtime reads
    an OpenAI-shaped client against a decision endpoint, which is the substitution this
    whole change exists to stop."""
    path = tmp_path / "config.yaml"

    native = mp.plan_for(sf.ROLE_TYPESAFE, provider="typesafe")
    assert native.decision_layer == sf.SYSTEM_ONE_NATIVE
    mp.write_role_document(native, path=path, backup=False)
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    jev = doc["godsv"]["jev"]
    assert jev["base_url"] == soa.TYPESAFE_ENDPOINT
    assert jev["model"] == "jev-latest"
    assert jev["api_key_env"] == "TYPESAFE_API_KEY"
    assert jev[sf.SYSTEM_ONE_KEY] == sf.SYSTEM_ONE_NATIVE
    assert doc["godsv"]["providers"]["entries"]["jev"][sf.SYSTEM_ONE_KEY] == (
        sf.SYSTEM_ONE_NATIVE
    )

    # the stand-in path says so instead of pretending (and stays fully usable)
    compat = mp.plan_for(sf.ROLE_TYPESAFE, provider="deepseek")
    assert compat.decision_layer == sf.SYSTEM_ONE_COMPATIBILITY
    mp.write_role_document(compat, path=path, backup=False)
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert doc["godsv"]["jev"][sf.SYSTEM_ONE_KEY] == sf.SYSTEM_ONE_COMPATIBILITY
    assert doc["godsv"]["jev"]["base_url"] == "https://api.deepseek.com/v1"


def test_the_general_role_refuses_the_decision_endpoint_by_name():
    """A decision endpoint cannot answer chat — refuse it before it is written, not after
    a live probe has already failed against a URL that was never a chat provider."""
    with pytest.raises(sf.SetupRefused) as refused:
        mp.plan_for(sf.ROLE_LLM, provider="typesafe")
    problem = str(refused.value)
    assert "typesafe" in problem.lower()
    assert "chat" in problem.lower()


def test_the_setup_flow_writes_the_marker_too(tmp_path):
    """``skaldir setup`` and ``skaldir model`` write one shape (two hosts, one document)."""
    native = sf.key_spec(
        sf.ROLE_TYPESAFE,
        base_url=soa.TYPESAFE_ENDPOINT,
        model="jev-latest",
        api_key_env="TYPESAFE_API_KEY",
        catalog_id="typesafe",
    )
    llm = sf.key_spec(
        sf.ROLE_LLM,
        base_url="https://api.deepseek.com/v1",
        model="deepseek-flash",
        api_key_env="DEEPSEEK_API_KEY",
        catalog_id="deepseek",
    )
    document = sf.build_document(typesafe=native, llm=llm)
    assert document["godsv"]["jev"][sf.SYSTEM_ONE_KEY] == sf.SYSTEM_ONE_NATIVE
    assert document["godsv"]["providers"]["entries"]["jev"][sf.SYSTEM_ONE_KEY] == (
        sf.SYSTEM_ONE_NATIVE
    )
    assert sf.SYSTEM_ONE_KEY not in document["godsv"]["llm"], (
        "the marker belongs to the decision block; the general provider is not on that line"
    )
