"""The provider catalog and the fallback chain — honesty as the assertion.

Two properties are tested here, and they are the whole point of the module:

* **a claim is what it says it is** — every documented claim carries the page it
  came from, no claim is ever labelled ``measured`` (only a real call measures),
  and the one entry this client cannot speak to is skipped *with that reason*
  instead of failing at runtime;
* **a chain says why** — every candidate is either ready or skipped with a
  reason from the shared vocabulary (``missing_credential``, ``no_model``,
  ``unsupported_feature``, ``rate_limit``, ``timeout``, …), and a run reports
  which candidate answered. Nothing is dropped silently and nothing is invented:
  the only ids a chain can contain are the ones handed to it.

The runtime part of ``run_chain`` is tested with a stub call (no HTTP, no key);
the real call against a live endpoint lives in ``test_structured_live.py`` and is
skipped unless a credential is in the environment.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from skaldir import llm_provider as lp
from skaldir import provider_catalog as pc
from skaldir import structured as st

CONFIG = Path("/nonexistent/catalog-tests/config.yaml")


# --------------------------------------------------------------------------- #
# the catalog is honest about what it knows                                    #
# --------------------------------------------------------------------------- #


def test_every_documented_claim_carries_its_source():
    for entry_id in pc.CATALOG_ORDER:
        entry = pc.catalog_entry(entry_id)
        for feature, claim in entry.features.items():
            assert claim.value in pc.CLAIM_VALUES
            if claim.source == pc.SRC_DOCUMENTED:
                assert claim.reference.startswith("https://"), (
                    f"{entry_id}.{feature} claims to be documented without a page"
                )
            else:
                assert claim.source == pc.SRC_UNKNOWN
                assert claim.note, f"{entry_id}.{feature} is unknown without a reason"


def test_no_claim_pretends_to_be_a_measurement():
    """Only a real call measures — the catalog has no 'measured' source at all."""
    report = pc.catalog_report()
    assert report["disclaimer"]
    for entry in report["entries"]:
        for feature, claim in entry["features"].items():
            assert claim["source"] in (pc.SRC_DOCUMENTED, pc.SRC_UNKNOWN), (
                f"{entry['id']}.{feature} has source {claim['source']!r}"
            )
            assert claim["value"] != pc.CLAIM_YES or claim["source"] == pc.SRC_DOCUMENTED


def test_only_a_documented_no_blocks_a_candidate():
    assert pc.Claim(pc.CLAIM_NO, pc.SRC_DOCUMENTED, reference="x").blocks is True
    assert pc.Claim(pc.CLAIM_UNKNOWN, pc.SRC_UNKNOWN, note="n").blocks is False
    assert pc.Claim(pc.CLAIM_LIKELY, pc.SRC_DOCUMENTED, reference="x").blocks is False
    assert pc.Claim(pc.CLAIM_PER_MODEL, pc.SRC_DOCUMENTED, reference="x").blocks is False


def test_an_unknown_claim_value_is_refused():
    with pytest.raises(ValueError):
        pc.Claim("definitely", pc.SRC_DOCUMENTED)
    with pytest.raises(KeyError):
        pc.catalog_entry("not-a-provider")


def test_the_catalog_lists_the_owner_endpoints_and_marks_what_it_cannot_speak_to():
    assert "deepseek" in pc.CATALOG and "openrouter" in pc.CATALOG
    assert pc.CATALOG["anthropic"].kind == pc.KIND_NOT_COMPATIBLE
    # the documented ceiling for DeepSeek is json_object, not json_schema
    deepseek = pc.CATALOG["deepseek"].claim(pc.FEATURE_STRUCTURED)
    assert deepseek.value == pc.CLAIM_LIKELY
    assert "json_object" in deepseek.note and "NOT documented" in deepseek.note


def test_a_catalog_entry_takes_its_model_from_the_env_in_both_spellings():
    entry = pc.catalog_entry("local")
    assert entry.model_for({"SKALDIR_LLM_MODEL": "skaldir-model"}) == "skaldir-model"
    # the pre-rename spelling is honoured too (skaldir.env.expand owns the fallback)
    assert entry.model_for({"GODSV_LLM_MODEL": "legacy-model"}) == "legacy-model"
    assert entry.model_for({"OPENAI_MODEL": "third-party"}) == "third-party"
    # a documented default wins over the environment — and it is the id the probe
    # observed (2026-09-19: GET /models listed deepseek-flash, deepseek-v4-pro)
    assert pc.catalog_entry("deepseek").model_for({"SKALDIR_LLM_MODEL": "x"}) == "deepseek-flash"


def test_the_secret_is_only_ever_a_variable_name():
    for entry_id in pc.CATALOG_ORDER:
        entry = pc.catalog_entry(entry_id)
        for name in entry.api_key_env:
            # it is a VARIABLE NAME (an uppercase env identifier), never a literal value.
            # The real AWS bearer var (`AWS_BEARER_TOKEN_BEDROCK`) shows the old `_KEY`/
            # `_TOKEN`/`_ENV` suffix was a proxy too narrow for the real convention.
            assert name.isupper() and name.isidentifier(), name
        # the entry itself carries no value field at all
        assert "api_key" not in entry.to_dict()


def test_credential_state_reports_a_state_never_a_value():
    candidate = pc.Candidate(id="x", base_url="https://x/v1", api_key_env=("SOME_KEY",))
    assert pc.credential_state(candidate, {}) == "unset"
    assert pc.credential_state(candidate, {"SOME_KEY": "s3cret"}) == "set"
    assert pc.credential_state(candidate, {"SOME_KEY": "   "}) == "unset"
    assert pc.credential_state(pc.Candidate(id="l", base_url="http://127.0.0.1:1/v1"), {}) == (
        "not_required"
    )
    # a remote endpoint that names no variable is *undeclared*, not "fine"
    assert pc.credential_state(pc.Candidate(id="r", base_url="https://r/v1"), {}) == "undeclared"
    assert "s3cret" not in repr(pc.credential_state(candidate, {"SOME_KEY": "s3cret"}))


# --------------------------------------------------------------------------- #
# build_chain: every skip has a reason                                         #
# --------------------------------------------------------------------------- #


def _candidate(**overrides) -> pc.Candidate:
    base = {
        "id": "c",
        "base_url": "https://c.invalid/v1",
        "model": "m",
        "api_key_env": ("C_KEY",),
    }
    base.update(overrides)
    return pc.Candidate(**base)


def test_a_missing_credential_is_a_skip_that_names_the_variable():
    steps = pc.build_chain([_candidate()], env={})
    assert steps[0].status == pc.STATUS_SKIPPED
    assert steps[0].reason == pc.SKIP_MISSING_CREDENTIAL
    assert "C_KEY" in steps[0].detail


def test_a_local_candidate_needs_no_credential():
    steps = pc.build_chain(
        [
            pc.Candidate(id="no-key", base_url="http://127.0.0.1:8080/v1", model="m"),
            pc.Candidate(
                id="declared-key", base_url="http://127.0.0.1:8080/v1", model="m",
                api_key_env=("C_KEY",),
            ),
        ],
        env={},
    )
    assert steps[0].status == pc.STATUS_READY
    assert steps[0].credential == "not_required"
    assert "declares no credential" in steps[0].detail
    # a declared key that is unset is only a skip for a REMOTE endpoint
    assert steps[1].status == pc.STATUS_READY
    assert steps[1].credential == "unset"
    assert "local endpoint" in steps[1].detail


def test_a_remote_candidate_that_declares_no_key_is_ready_but_warned():
    """A proxy may inject the key — the chain reports the risk instead of guessing."""
    steps = pc.build_chain(
        [pc.Candidate(id="proxy", base_url="https://proxy.invalid/v1", model="m")], env={}
    )
    assert steps[0].status == pc.STATUS_READY
    assert steps[0].credential == "undeclared"
    assert "401" in steps[0].detail


def test_every_structural_skip_reason_is_reachable():
    steps = pc.build_chain(
        [
            _candidate(id="no-url", base_url=""),
            _candidate(id="no-model", model=""),
            _candidate(id="wrong-kind", kind=pc.KIND_NOT_COMPATIBLE),
            _candidate(id="off", disabled=True),
            _candidate(id="no-key", api_key_env=("NOPE_KEY",)),
            _candidate(id="blocked", features={pc.FEATURE_STRUCTURED: pc.Claim(
                pc.CLAIM_NO, pc.SRC_DOCUMENTED, reference="https://x"
            )}),
        ],
        env={"C_KEY": "1"},
        require=(pc.FEATURE_STRUCTURED,),
    )
    by_id = {step.id: step for step in steps}
    assert by_id["no-url"].reason == pc.SKIP_NO_BASE_URL
    assert by_id["no-model"].reason == pc.SKIP_NO_MODEL
    assert by_id["wrong-kind"].reason == pc.SKIP_CLIENT_UNSUPPORTED
    assert by_id["off"].reason == pc.SKIP_DISABLED
    assert by_id["no-key"].reason == pc.SKIP_MISSING_CREDENTIAL
    assert by_id["blocked"].reason == pc.SKIP_UNSUPPORTED
    assert all(step.reason in pc.SKIP_REASONS for step in steps)
    assert all(step.detail for step in steps)


def test_an_unverified_claim_keeps_the_candidate_and_is_reported():
    candidate = _candidate(
        features={
            pc.FEATURE_STRUCTURED: pc.Claim(
                pc.CLAIM_PER_MODEL, pc.SRC_DOCUMENTED, reference="https://x", note="per endpoint"
            )
        }
    )
    steps = pc.build_chain([candidate], env={"C_KEY": "1"}, require=(pc.FEATURE_STRUCTURED,))
    assert steps[0].status == pc.STATUS_READY
    assert steps[0].claim_notes and "per_model" in steps[0].claim_notes[0]


def test_an_unknown_required_feature_is_refused():
    with pytest.raises(ValueError):
        pc.build_chain([_candidate()], env={"C_KEY": "1"}, require=("telepathy",))


def test_chain_report_names_the_order_the_ready_and_every_skip():
    steps = pc.build_chain(
        [_candidate(id="a"), _candidate(id="b", api_key_env=("NOPE",))],
        env={"C_KEY": "1"},
    )
    report = pc.chain_report(steps)
    assert report["order"] == ["a", "b"]
    assert report["ready"] == ["a"] and report["ready_count"] == 1
    assert report["first"] == "a"
    assert report["skipped"] == [
        {
            "id": "b",
            "reason": pc.SKIP_MISSING_CREDENTIAL,
            "detail": "none of NOPE is set in the environment",
        }
    ]


# --------------------------------------------------------------------------- #
# the model-id guard: what we offer vs what the probe observed                  #
# --------------------------------------------------------------------------- #


def _stale_deepseek() -> dict:
    """The 2026-09-19 catalog entry as it was BEFORE the probe corrected it."""
    return {
        "deepseek": pc.CatalogEntry(
            id="deepseek",
            label="DeepSeek",
            kind=pc.KIND_OPENAI_COMPATIBLE,
            base_url="https://api.deepseek.com/v1",
            api_key_env=("DEEPSEEK_API_KEY",),
            default_model="deepseek-chat",
            models=("deepseek-chat", "deepseek-reasoner"),
        )
    }


def test_the_probe_log_yields_the_model_ids_it_observed():
    """The ids come out of the probe log — and the alias echo is not an observed id."""
    observed = pc.observed_model_ids("deepseek")
    assert observed["ids"] == ["deepseek-flash", "deepseek-v4-pro"]
    assert observed["listed"] == ["deepseek-flash", "deepseek-v4-pro"]
    # deepseek-chat was ASKED for and echoed back as another id: it is an alias
    assert observed["echoes"] == {"deepseek-chat": "deepseek-flash"}
    step = observed["evidence"][0]
    assert step["step"] == "auth: GET /models" and step["date"] == pc.PROBE_DATE
    assert "deepseek-flash" in step["evidence"]


def test_a_provider_without_a_probe_step_is_unchecked_not_failed():
    """The honesty rule: no credential ⇒ nothing measured ⇒ no verdict invented."""
    observed = pc.observed_model_ids("openai")
    assert observed["ids"] == [] and observed["probed"] is False
    assert "nothing was measured" in observed["reason"]
    report = pc.model_id_report()
    assert "openai" not in report["compared"]
    unchecked = {item["id"]: item for item in report["unchecked"]}
    # openai has no probe ⇒ it is *unchecked*, never a failed verdict. (The exact set is
    # the catalog's, so this pins the invariant — openai ∈ unchecked — not the catalog size.)
    assert "openai" in unchecked
    # …and a counted-but-unnamed /models list is a gap in the READING, not a failure;
    # the offered ids are the catalog's own (derived, so a model refresh does not rot this)
    assert unchecked["openai"]["offered"] == list(pc.catalog_entry("openai").models)


def test_the_catalog_offers_only_ids_the_probe_observed():
    """The live guard: every id the curated entry offers was seen on the wire."""
    report = pc.model_id_report()
    assert report["agree"] is True, json.dumps(report["conflicts"], indent=2)
    assert report["compared"] == ["deepseek"]
    assert report["observed"]["deepseek"] == ["deepseek-flash", "deepseek-v4-pro"]
    entry = pc.catalog_entry("deepseek")
    assert pc.catalog_model_ids(entry) == ["deepseek-flash", "deepseek-v4-pro"]
    assert set(pc.catalog_model_ids(entry)) <= set(report["observed"]["deepseek"])


def test_a_stale_model_id_is_a_failure_that_carries_its_probe_evidence():
    """The regression this guard exists for: deepseek-chat offered, deepseek-flash served."""
    drift = pc.drift_report(_stale_deepseek())
    assert drift["agree"] is False
    model_conflicts = [item for item in drift["conflicts"] if item["field"] == "model_ids"]
    assert len(model_conflicts) == 1
    conflict = model_conflicts[0]
    assert conflict["id"] == "deepseek"
    assert conflict["offered_but_not_observed"] == ["deepseek-chat", "deepseek-reasoner"]
    assert conflict["observed_ids"] == ["deepseek-flash", "deepseek-v4-pro"]
    # the failure is not a verdict without evidence: the probe step is quoted
    assert conflict["probe_step"] == "auth: GET /models"
    assert conflict["probe_date"] == pc.PROBE_DATE
    assert "2 model ids listed" in conflict["probe_evidence"]
    assert drift["model_ids"]["agree"] is False


def test_an_unknown_catalog_id_is_caught_even_when_it_was_never_asked_about():
    """An id nobody probed is still not an observed id — the guard fails it."""
    table = _stale_deepseek()
    table["deepseek"] = pc.CatalogEntry(
        id="deepseek",
        label="DeepSeek",
        kind=pc.KIND_OPENAI_COMPATIBLE,
        base_url="https://api.deepseek.com/v1",
        api_key_env=("DEEPSEEK_API_KEY",),
        default_model="deepseek-flash",
        models=("deepseek-flash", "deepseek-omega-9"),
    )
    drift = pc.drift_report(table)
    assert drift["agree"] is False
    assert drift["conflicts"][0]["offered_but_not_observed"] == ["deepseek-omega-9"]


# --------------------------------------------------------------------------- #
# auto_chain: the configured provider first, then the catalog                   #
# --------------------------------------------------------------------------- #


def test_the_configured_candidate_comes_first_and_catalog_order_is_kept():
    steps = pc.auto_chain(
        env={"DEEPSEEK_API_KEY": "x", "SKALDIR_LLM_MODEL": "qwen2.5-7b-instruct"},
        config_path=CONFIG,
    )
    ids = [step.id for step in steps]
    assert ids[0].startswith("config:")
    catalog_ids = [step.id for step in steps if step.candidate.source == pc.SOURCE_CATALOG]
    assert catalog_ids == list(pc.CATALOG_ORDER)
    ready = [step.id for step in steps if step.status == pc.STATUS_READY]
    assert "deepseek" in ready  # its key is in the env


def test_nothing_is_invented_without_a_configuration():
    steps = pc.auto_chain(env={}, config_path=CONFIG)
    assert all(
        step.id in pc.CATALOG_ORDER or step.id.startswith("config:") for step in steps
    )
    assert pc.chain_report(steps)["ready"] == []


def test_without_a_configuration_every_skip_has_a_reason():
    report = pc.chain_report(pc.auto_chain(env={}, config_path=CONFIG))
    assert report["ready_count"] == 0
    reasons = {item["id"]: item["reason"] for item in report["skipped"]}
    assert reasons["anthropic"] == pc.SKIP_CLIENT_UNSUPPORTED
    assert reasons["deepseek"] == pc.SKIP_MISSING_CREDENTIAL
    assert reasons["local"] == pc.SKIP_NO_MODEL
    assert all(item["detail"] for item in report["skipped"])


def test_include_unconfigured_false_drops_only_the_keyless_ones():
    env = {"DEEPSEEK_API_KEY": "x", "SKALDIR_LLM_MODEL": "m"}
    steps = pc.auto_chain(env=env, config_path=CONFIG, include_unconfigured=False)
    ids = [step.id for step in steps]
    assert "deepseek" in ids
    assert "openai" not in ids and "openrouter" not in ids


def test_a_configured_store_entry_becomes_the_first_candidate(tmp_path):
    from skaldir import provider_config as pcfg

    config = tmp_path / "config.yaml"
    pcfg.add_provider(
        "stub", base_url="http://127.0.0.1:9/v1", model="stub-model",
        api_key_env="STUB_KEY", path=config,
    )
    steps = pc.auto_chain(env={"STUB_KEY": "1"}, config_path=config)
    first = steps[0]
    assert first.id == "config:stub"
    assert first.candidate.source == pc.SOURCE_CONFIG
    assert first.status == pc.STATUS_READY
    assert first.credential == "set"


def test_chain_with_catalogs_is_one_report():
    payload = pc.chain_with_catalogs(env={}, config_path=CONFIG)
    assert payload["require"] == [pc.FEATURE_STRUCTURED]
    assert payload["chain"]["count"] == payload["catalog"]["count"] + 1 or (
        payload["chain"]["count"] >= 1
    )
    assert payload["catalog"]["entries"][0]["id"] == pc.CATALOG_ORDER[0]


# --------------------------------------------------------------------------- #
# run_chain: who answered, and why the others did not                          #
# --------------------------------------------------------------------------- #


def test_the_chain_reports_which_candidate_answered():
    from skaldir.llm_provider import ProviderRateLimitError

    steps = pc.build_chain(
        [_candidate(id="a"), _candidate(id="b")], env={"C_KEY": "1"}
    )

    def call(step):
        if step.id == "a":
            raise ProviderRateLimitError("429", status=429)
        return st.StructuredOutcome(
            ok=True, value={"answer": "pong"}, raw='{"answer": "pong"}', mode=st.MODE_NATIVE
        )

    outcome = pc.run_chain(steps, call)
    assert outcome.ok is True
    assert outcome.answered_by == "b"
    assert outcome.mode == st.MODE_NATIVE
    assert outcome.raw == '{"answer": "pong"}'
    assert [(item["id"], item["status"], item["reason"]) for item in outcome.attempts] == [
        ("a", pc.STATUS_FAILED, pc.SKIP_RATE_LIMIT),
        ("b", pc.STATUS_ANSWERED, st.REASON_ANSWERED),
    ]
    assert outcome.ready_count == 2 and outcome.skipped_count == 0


def test_a_validation_failure_does_not_repeat_on_the_same_candidate():
    steps = pc.build_chain([_candidate(id="a"), _candidate(id="b")], env={"C_KEY": "1"})
    calls: list[str] = []

    def call(step):
        calls.append(step.id)
        if step.id == "a":
            return st.StructuredOutcome(
                ok=False, error="invalid", validation_error="$.probe: expected integer"
            )
        return st.StructuredOutcome(ok=True, value={"answer": "pong"}, mode=st.MODE_PROMPTED)

    outcome = pc.run_chain(steps, call)
    assert calls == ["a", "b"]  # one attempt each, in order
    assert outcome.ok is True and outcome.answered_by == "b"
    assert outcome.attempts[0]["reason"] == pc.SKIP_INVALID
    assert "expected integer" in outcome.attempts[0]["detail"]


def test_the_chain_never_repeats_a_candidate_unless_asked():
    steps = pc.build_chain([_candidate(id="a")], env={"C_KEY": "1"})
    calls: list[str] = []

    def call(step):
        calls.append(step.id)
        raise lp.ProviderTimeoutError("no answer in time")

    pc.run_chain(steps, call)
    assert calls == ["a"]
    pc.run_chain(steps, call, attempts_per_step=3)
    assert calls == ["a", "a", "a", "a"]


def test_every_candidate_failing_reports_each_one():
    steps = pc.build_chain([_candidate(id="a"), _candidate(id="b")], env={"C_KEY": "1"})

    def call(step):
        raise lp.ProviderAuthError("401 refused", status=401)

    outcome = pc.run_chain(steps, call)
    assert outcome.ok is False and outcome.answered_by == ""
    assert "a failed (auth_failed" in outcome.error
    assert "b failed (auth_failed" in outcome.error
    assert [item["reason"] for item in outcome.attempts] == [pc.SKIP_AUTH, pc.SKIP_AUTH]


def test_an_empty_or_hopeless_chain_says_so():
    empty = pc.run_chain([], lambda step: None)
    assert empty.ok is False
    assert "chain is empty" in empty.error
    steps = pc.build_chain([_candidate(id="a")], env={})
    skipped = pc.run_chain(steps, lambda step: None)
    assert skipped.ok is False
    assert "no candidate is ready" in skipped.error
    assert "missing_credential" in skipped.error


def test_the_exception_to_reason_mapping_is_the_shared_vocabulary():
    assert pc.reason_for_exception(lp.ProviderAuthError("x", status=401)) == pc.SKIP_AUTH
    assert pc.reason_for_exception(lp.ProviderRateLimitError("x", status=429)) == pc.SKIP_RATE_LIMIT
    assert pc.reason_for_exception(lp.ProviderTimeoutError("x")) == pc.SKIP_TIMEOUT
    assert (
        pc.reason_for_exception(lp.StructuredOutputNotSupportedError("x", status=400))
        == pc.SKIP_UNSUPPORTED
    )
    assert pc.reason_for_exception(lp.ProviderResponseError("x")) == pc.SKIP_PROVIDER_ERROR
    assert pc.reason_for_exception(ValueError("x")) == pc.SKIP_PROVIDER_ERROR
    for reason in (
        pc.SKIP_AUTH, pc.SKIP_RATE_LIMIT, pc.SKIP_TIMEOUT, pc.SKIP_UNSUPPORTED,
        pc.SKIP_PROVIDER_ERROR, pc.SKIP_INVALID,
    ):
        assert reason in pc.SKIP_REASONS


def test_the_outcome_is_json_safe():
    import json

    steps = pc.build_chain([_candidate(id="a")], env={"C_KEY": "1"})
    outcome = pc.run_chain(
        steps,
        lambda step: st.StructuredOutcome(ok=True, value={"a": 1}, mode=st.MODE_NATIVE),
    )
    rendered = json.dumps(outcome.to_dict())
    assert '"answered_by": "a"' in rendered
    assert json.dumps(pc.catalog_report())
    assert json.dumps(pc.chain_report(steps))
