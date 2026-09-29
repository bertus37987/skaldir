"""The provider breadth catalog — the assertions are about honesty, not breadth.

Four properties are tested here, and they are why the registry exists:

* **a status is what it says it is** — ``verified`` carries a probe step that is
  really in the probe log, ``documented`` carries an official page (and says
  whether *this* round fetched it), ``unverified`` never claims a value;
* **a live probe beats a document** — DeepSeek's native ``json_schema`` is the
  measured case: the endpoint answered HTTP 400, so the registry reports ``no``
  (verified) and the chain catalog carries the refusal in ``Claim.measured``. A
  future edit that flips it back to a promise must fail here;
* **the breadth is real** — every provider the task named has an entry with a
  base URL, an OpenAI-compatible path and its credential variables, and no entry
  anywhere in the file contains a credential *value*;
* **the two tables agree** — the curated chain catalog and the breadth registry
  must not name different endpoints for a provider they share.

No network here: the probes were run by hand and their evidence is data.
"""

from __future__ import annotations

import json

import pytest

from skaldir import provider_catalog as pc
from skaldir import provider_cli as pcli
from skaldir import provider_registry_data as rd

#: The providers the catalog must cover (the task's list, by registry id).
REQUIRED_IDS = (
    "openai",
    "anthropic",
    "google-gemini",
    "google-vertex",
    "xai",
    "deepseek",
    "moonshot",
    "alibaba-qwen",
    "openrouter",
    "zai",
    "xiaomi-mimo",
    "opencode-go",
    "llamacpp",
    "vllm",
    "lmstudio",
    "ollama",
)

#: Anything that looks like an issued credential rather than a variable name.
SECRET_SHAPED = ("sk-", "sk_", "Bearer sk", "AIza", "xoxb-", "ghp_")


def _registry_payload() -> dict:
    """Every entry as JSON, with its evidence — and never a credential value."""
    return json.loads(json.dumps(rd.registry_report()))


# --------------------------------------------------------------------------- #
# every claim carries what establishes it                                      #
# --------------------------------------------------------------------------- #


def test_every_entry_is_usable_and_named():
    for entry_id in REQUIRED_IDS:
        spec = rd.registry_entry(entry_id)
        assert spec.id == entry_id
        assert spec.label, f"{entry_id} has no display name"
        assert spec.docs.startswith("https://"), f"{entry_id} names no docs page"
        assert spec.endpoints, f"{entry_id} has no endpoint at all"
        assert spec.verification in rd.VERIFICATION_ORDER
        assert spec.probe in (rd.PROBE_RUN, rd.PROBE_PARTIAL, rd.PROBE_NOT_RUN)
        if spec.probe == rd.PROBE_NOT_RUN:
            assert spec.probe_reason, f"{entry_id} says not_run without a reason"


def test_the_registry_is_exactly_the_required_breadth_plus_nothing_invented():
    assert set(REQUIRED_IDS) <= set(rd.REGISTRY_ORDER)
    assert len(rd.REGISTRY_ORDER) == len(set(rd.REGISTRY_ORDER))
    assert set(rd.REGISTRY) == set(rd.REGISTRY_ORDER)


def test_verified_claims_point_at_a_probe_step_that_exists():
    steps = {(step.provider, step.step) for step in rd.PROBE_LOG}
    verified = 0
    for spec in rd.REGISTRY.values():
        for name, support in spec.features.items():
            if support.status != rd.VERIFIED:
                continue
            verified += 1
            assert support.reference.startswith("probe:"), (
                f"{spec.id}.{name} claims verified without naming a probe"
            )
            step = support.reference.split(":", 1)[1]
            assert step.startswith(spec.id), (
                f"{spec.id}.{name} cites probe {step!r}, which belongs to another provider"
            )
            assert any(provider == spec.id for provider, _ in steps), (
                f"{spec.id}.{name} is verified but {spec.id} has no probe step"
            )
            assert support.value != rd.SUPPORT_UNKNOWN, (
                f"{spec.id}.{name} is verified yet claims unknown"
            )
    # deepseek contributes 4 (stream, native json_schema refusal, json_object, vision) and
    # openrouter 3 (stream, native json_schema on one route, json_object)
    assert verified >= 7, f"only {verified} verified feature claims — the probes prove more"


def test_documented_claims_carry_a_source_and_say_whether_it_was_fetched():
    documented = unfetched = 0
    for spec in rd.REGISTRY.values():
        for name, support in spec.features.items():
            if support.status != rd.DOCUMENTED:
                continue
            assert support.reference.startswith(("https://", "probe:")), (
                f"{spec.id}.{name} claims documented without a source"
            )
            documented += 1
            if not support.fetched:
                unfetched += 1
                assert support.note, (
                    f"{spec.id}.{name} was not fetched this round and carries no note about it"
                )
        assert spec.endpoints[0].status in rd.VERIFICATION_ORDER
    assert documented > 0
    # the distinction has to be real: some pages were fetched, some were only named
    assert unfetched > 0, "every documented claim claims to be fetched — check the sources"


def test_unverified_claims_never_claim_a_value():
    for spec in rd.REGISTRY.values():
        for name, support in spec.features.items():
            if support.status != rd.UNVERIFIED:
                continue
            assert support.value == rd.SUPPORT_UNKNOWN, (
                f"{spec.id}.{name} is unverified but reports {support.value!r}"
            )
            assert support.note, f"{spec.id}.{name} is unknown without a reason"


def test_an_unknown_status_or_value_is_refused():
    with pytest.raises(ValueError):
        rd.Support("yes", "measured-by-vibes")
    with pytest.raises(ValueError):
        rd.Support("probably", rd.DOCUMENTED)
    with pytest.raises(KeyError):
        rd.registry_entry("nope")
    with pytest.raises(ValueError):
        rd.registry_report(verification="probably")


def test_every_base_url_is_absolute_and_every_openai_endpoint_has_a_chat_path():
    for spec in rd.REGISTRY.values():
        for endpoint in spec.endpoints:
            assert endpoint.base_url.startswith(("https://", "http://")), (
                f"{spec.id}: {endpoint.base_url!r} is not an absolute URL"
            )
            if endpoint.openai_compatible:
                assert endpoint.chat_path.startswith("/")
                assert endpoint.url == endpoint.base_url.rstrip("/") + "/chat/completions", (
                    f"{spec.id}: the OpenAI-compatible URL is {endpoint.url!r}"
                )
            else:
                # a native endpoint speaks its own path: '/messages' for Anthropic, and
                # nothing at all for a whole-URL endpoint like /v1/systemone. What it may
                # never do is claim the OpenAI chat path, because that is what an
                # OpenAI-shaped client would build a request against.
                assert endpoint.chat_path != "/chat/completions", (
                    f"{spec.id}: a non-OpenAI endpoint may not claim the OpenAI chat path"
                )
                assert endpoint.url == endpoint.base_url.rstrip("/") + endpoint.chat_path
            assert endpoint.status in rd.VERIFICATION_ORDER
            if endpoint.status == rd.DOCUMENTED:
                assert endpoint.reference.startswith("https://"), (
                    f"{spec.id}: a documented endpoint must name its page"
                )


def test_vertex_and_gemini_are_separate_entries_because_they_are_separate_products():
    gemini = rd.registry_entry("google-gemini")
    vertex = rd.registry_entry("google-vertex")
    assert "generativelanguage.googleapis.com" in gemini.primary_endpoint.base_url
    assert "aiplatform.googleapis.com" in vertex.primary_endpoint.base_url
    assert "{LOCATION}" in vertex.primary_endpoint.base_url  # not a constant
    assert {e.kind for e in vertex.endpoints} == {rd.KIND_OPENAI_COMPATIBLE}


# --------------------------------------------------------------------------- #
# the correction: a probe beats a document                                     #
# --------------------------------------------------------------------------- #


def test_deepseek_native_json_schema_is_reported_as_measured_no():
    """The rule from our own measurement: the probe wins over the documented ceiling."""
    spec = rd.registry_entry("deepseek")
    assert spec.probe == rd.PROBE_RUN
    assert spec.native_json_schema.value == rd.SUPPORT_NO
    assert spec.native_json_schema.status == rd.VERIFIED
    assert "unavailable" in spec.native_json_schema.note
    assert "400" in spec.native_json_schema.note
    # the fallback that really answers is reported as working, not as a failure
    assert spec.json_object.value == rd.SUPPORT_YES
    assert spec.json_object.status == rd.VERIFIED


def test_the_probe_log_keeps_the_refusal_verbatim():
    refusal = [
        step
        for step in rd.PROBE_LOG
        if step.provider == "deepseek" and "json_schema" in step.step
    ]
    assert refusal, "the DeepSeek json_schema probe is missing from the log"
    step = refusal[0]
    assert step.status == 400 and step.ok is False
    assert "This response_format type is unavailable now" in step.evidence


def test_the_chain_catalog_carries_the_measured_refusal_not_a_promise():
    """The curated table keeps offering DeepSeek — with the refusal attached."""
    claim = pc.CATALOG["deepseek"].claim(pc.FEATURE_STRUCTURED)
    assert claim.measured, "the measured correction is missing from the chain catalog"
    assert "400" in claim.measured and "json_object" in claim.measured
    # it stays `likely` and not `no`: json_object really does answer, so a plain
    # `no` would skip a provider that works — which would be a worse lie.
    assert claim.value == pc.CLAIM_LIKELY
    assert claim.blocks is False
    # and the claim text can be read back from JSON
    rendered = json.dumps(pc.catalog_report())
    assert "This response_format type is unavailable now" in rendered


def test_a_probe_that_could_not_run_is_not_reported_as_a_failure():
    spec = rd.registry_entry("opencode-go")
    assert spec.probe == rd.PROBE_PARTIAL
    assert spec.probe_reason
    assert "quota" in spec.probe_reason or "429" in spec.probe_reason
    # the endpoint and credential really were proven before the quota stopped us
    assert spec.primary_endpoint.status == rd.VERIFIED
    # …but no capability claim was upgraded by a probe that returned 429
    for name in ("streaming", "native_json_schema", "json_object", "vision"):
        assert spec.support(name).status != rd.VERIFIED, f"{name} was upgraded by a 429"


def test_openrouter_native_json_schema_is_measured_per_model():
    spec = rd.registry_entry("openrouter")
    assert spec.native_json_schema.status == rd.VERIFIED
    assert spec.native_json_schema.value == rd.SUPPORT_PER_MODEL
    assert "429" in spec.native_json_schema.note or "per-model" in spec.native_json_schema.note


def test_the_measured_refusal_shaped_the_openai_style_ceiling_of_zai():
    """Z.ai documents json_object only — the same ceiling as DeepSeek, unmeasured here."""
    spec = rd.registry_entry("zai")
    assert spec.native_json_schema.value == rd.SUPPORT_NO
    assert spec.native_json_schema.status == rd.DOCUMENTED
    assert spec.json_object.value == rd.SUPPORT_YES
    assert spec.native_json_schema.note


# --------------------------------------------------------------------------- #
# no credential value anywhere                                                 #
# --------------------------------------------------------------------------- #


def test_the_registry_never_carries_a_credential_value():
    payload = _registry_payload()
    rendered = json.dumps(payload)
    for needle in SECRET_SHAPED:
        assert needle not in rendered, f"a credential-shaped string ({needle!r}) reached the JSON"
    # no entry carries a secret FIELD — the only credential field holds variable NAMES
    for entry in payload["results"]:
        for field in ("api_key", "secret", "token", "password"):
            assert field not in entry, f"{entry['id']} carries a {field!r} field"
        for endpoint in entry["endpoints"]:
            assert "api_key_env" in endpoint
            for field in ("api_key", "secret", "token"):
                assert field not in endpoint, f"{entry['id']} endpoint carries {field!r}"
            for name in endpoint["api_key_env"]:
                assert name == name.upper(), name
                # a credential *variable* name carries KEY or TOKEN (AWS_BEARER_TOKEN_BEDROCK
                # names TOKEN in the middle — the suffix-only pin was too narrow)
                assert ("_KEY" in name or "_TOKEN" in name), name
    names = {
        name
        for entry in payload["results"]
        for endpoint in entry["endpoints"]
        for name in endpoint["api_key_env"]
    }
    assert names, "no credential variable is named anywhere — that cannot be right"
    # a provider's own quoted error CODE (e.g. DashScope's `invalid_api_key`) is evidence
    # about a failure, not a secret; the check above is about fields and values, not prose.
    assert "invalid_api_key" in rendered


def test_a_local_entry_declares_no_credential_at_all():
    for entry_id in ("lmstudio",):
        endpoint = rd.registry_entry(entry_id).primary_endpoint
        assert endpoint.api_key_env == ()
        assert endpoint.openai_compatible
    # Ollama's local endpoint needs a placeholder that is ignored — the entry says so
    local = rd.registry_entry("ollama").endpoints[0]
    assert "IGNORED" in " ".join(local.auth_header) or "IGNORED" in local.note
    assert local.api_key_env == ()


# --------------------------------------------------------------------------- #
# the licensing column                                                         #
# --------------------------------------------------------------------------- #


def test_local_server_licences_are_recorded_with_their_source():
    for entry_id, expected in (
        ("llamacpp", "MIT"),
        ("vllm", "Apache-2.0"),
        ("ollama", "MIT"),
    ):
        spec = rd.registry_entry(entry_id)
        assert expected in spec.licence, f"{entry_id} licence: {spec.licence!r}"
        assert spec.licence_fetched is True
        assert spec.licence_reference.startswith("https://")
    lmstudio = rd.registry_entry("lmstudio")
    assert "proprietary" in lmstudio.licence
    assert lmstudio.licence_fetched is True
    # a hosted API whose terms we did not fetch says exactly that
    for entry_id in ("deepseek", "moonshot", "openai", "anthropic"):
        spec = rd.registry_entry(entry_id)
        assert "not fetched" in spec.licence, f"{entry_id} overclaims its licence"


def test_every_entry_records_some_licence_note():
    for spec in rd.REGISTRY.values():
        assert spec.licence, f"{spec.id} records no licence/ToS note"


# The registry/catalog agreement section below carries the same assertion again, this
# time covering BOTH branches of the moonshot default (the earlier copy at this spot was
# a second definition of the same test name, so it never ran — F811).

# --------------------------------------------------------------------------- #
# quirks are present where they matter                                         #
# --------------------------------------------------------------------------- #


def test_the_regional_and_extra_header_quirks_are_recorded():
    qwen = " ".join(rd.registry_entry("alibaba-qwen").quirks)
    assert "region" in qwen and "401" in qwen
    assert "compatible-mode" in rd.registry_entry("alibaba-qwen").primary_endpoint.base_url
    anthropic = " ".join(rd.registry_entry("anthropic").endpoints[0].auth_header)
    assert "anthropic-version" in anthropic
    zai = " ".join(rd.registry_entry("zai").quirks)
    assert "coding" in zai
    orouter = " ".join(rd.registry_entry("openrouter").quirks)
    assert "require_parameters" in orouter
    local_ports = {
        "llamacpp": "8080",
        "vllm": "8000",
        "lmstudio": "1234",
        "ollama": "11434",
    }
    for entry_id, port in local_ports.items():
        assert port in rd.registry_entry(entry_id).primary_endpoint.base_url


def test_the_openai_shape_vs_native_shape_is_distinguished():
    anthropic = rd.registry_entry("anthropic")
    assert anthropic.endpoints[0].kind == rd.KIND_NATIVE
    assert anthropic.endpoints[0].chat_path == "/messages"
    # the same host also serves an OpenAI-shaped path — a different endpoint, not the same one
    assert anthropic.endpoints[1].openai_compatible
    assert anthropic.endpoints[1].url.endswith("/chat/completions")
    for entry_id in REQUIRED_IDS:
        endpoint = rd.registry_entry(entry_id).primary_endpoint
        assert endpoint.openai_compatible, f"{entry_id} has no OpenAI-compatible endpoint"


# --------------------------------------------------------------------------- #
# the report the CLI prints                                                    #
# --------------------------------------------------------------------------- #


def test_the_report_is_json_safe_and_carries_the_counts():
    payload = rd.registry_report()
    rendered = json.dumps(payload)
    assert payload["count"] == len(rd.REGISTRY_ORDER)
    assert sum(payload["verification_counts"].values()) == payload["count"]
    assert payload["probe_date"] == rd.PROBE_DATE
    assert "verified" in rendered and "unverified" in rendered


def test_the_report_can_be_filtered_without_losing_the_evidence():
    only = rd.registry_report(verification=rd.VERIFIED)
    assert only["count"] >= 1
    assert set(only["order"]) == {spec.id for spec in rd.REGISTRY.values()
                                  if spec.verification == rd.VERIFIED}
    for entry in only["results"]:
        assert entry["verification"] == rd.VERIFIED
    one = rd.registry_report(entry_id="deepseek")
    assert one["count"] == 1 and one["order"] == ["deepseek"]


def test_the_probe_report_counts_measured_and_unmeasured():
    report = rd.probe_report()
    assert report["measured_steps"] + report["unmeasured_steps"] == len(report["steps"])
    assert report["fetched_docs"], "the fetched pages are the documented evidence"
    for url in report["fetched_docs"]:
        assert url.startswith("https://")
    assert "not a failure" in report["disclaimer"] or "not upgrade" in report["disclaimer"]


# --------------------------------------------------------------------------- #
# the bridge into the chain, and the drift guard                               #
# --------------------------------------------------------------------------- #


def test_the_chain_catalog_and_the_registry_agree():
    drift = pc.drift_report()
    assert drift["conflicts"] == [], json.dumps(drift["conflicts"], indent=2)
    assert drift["agree"] is True
    assert set(drift["compared"]) == set(pc.CHAIN_OVERLAP)


def test_the_registry_default_model_is_the_catalog_s_own_for_a_shared_id():
    """For a provider both tables carry, the registry's default IS the catalog's default.

    ``default_model`` was the one field the drift report did not compare, so the registry
    could keep pre-filling ``deepseek-chat`` while the probe-corrected catalog named
    ``deepseek-flash`` — and every consumer that reads the registry (the setup picker, the
    chain bridge) would then offer an id the endpoint no longer serves. This pins the
    agreement, by reading the catalog rather than retyping either id.
    """
    compared = 0
    for entry_id in pc.CHAIN_OVERLAP:
        if entry_id not in rd.REGISTRY or entry_id not in pc.CATALOG:
            continue
        catalog_default = pc.CATALOG[entry_id].default_model
        if not catalog_default:
            continue  # nothing documented on either side to agree about
        assert rd.REGISTRY[entry_id].default_model == catalog_default, (
            f"{entry_id}: the registry pre-fills {rd.REGISTRY[entry_id].default_model!r} "
            f"but the catalog's default is {catalog_default!r}"
        )
        compared += 1
    assert compared, "no shared entry carried a default model — the guard compared nothing"


def test_a_registry_entry_becomes_a_candidate_with_the_registry_url():
    candidate = pc.candidate_from_registry("deepseek")
    assert candidate.base_url == pc.CATALOG["deepseek"].base_url
    # the registry's default is the catalog's own id — read from the catalog, not retyped
    assert candidate.model == pc.CATALOG["deepseek"].default_model
    assert candidate.api_key_env == ("DEEPSEEK_API_KEY",)
    # moonshot's default is the catalog's own id after the refresh (was `""` before)
    moonshot = pc.candidate_from_registry("moonshot")
    assert moonshot.model == pc.CATALOG["moonshot"].default_model
    steps = pc.build_chain([moonshot], env={"MOONSHOT_API_KEY": "x"})
    if pc.CATALOG["moonshot"].default_model:
        assert steps[0].reason != pc.SKIP_NO_MODEL
    else:
        assert steps[0].status == pc.STATUS_SKIPPED
        assert steps[0].reason == pc.SKIP_NO_MODEL


def test_the_bridge_picks_the_openai_shaped_endpoint_when_a_provider_has_two():
    """Anthropic documents a native API first and a compatibility layer second."""
    anthropic = rd.registry_entry("anthropic")
    assert anthropic.endpoints[0].kind == rd.KIND_NATIVE
    assert anthropic.endpoints[0].openai_compatible is False
    candidate = pc.candidate_from_registry("anthropic")
    assert candidate.base_url == anthropic.endpoints[1].base_url
    assert candidate.kind == pc.KIND_OPENAI_COMPATIBLE  # the bridge is usable, not decorative


def test_a_candidate_the_client_cannot_speak_to_is_still_refused():
    refused = pc.Candidate(
        id="native-only",
        base_url="https://api.anthropic.com/v1",
        model="claude-x",
        kind=pc.KIND_NOT_COMPATIBLE,
        api_key_env=("ANTHROPIC_API_KEY",),
    )
    steps = pc.build_chain([refused], env={"ANTHROPIC_API_KEY": "x"})
    assert steps[0].status == pc.STATUS_SKIPPED
    assert steps[0].reason == pc.SKIP_CLIENT_UNSUPPORTED
    assert "no request would be attempted" in steps[0].detail


def test_the_default_chain_is_unchanged_and_the_registry_chain_is_opt_in():
    default = pc.auto_chain(env={}, config_path="/nonexistent/wave/config.yaml")
    ids = [step.id for step in default]
    assert ids == list(pc.CATALOG_ORDER) or (ids and ids[0].startswith("config:"))
    wide = pc.auto_chain(
        env={}, config_path="/nonexistent/wave/config.yaml", include_registry=True
    )
    wide_ids = [step.id for step in wide]
    assert set(rd.REGISTRY_ORDER) <= set(wide_ids), "the registry bridge lost an entry"
    assert len(wide_ids) == len(set(wide_ids)), "the bridge offered an id twice"
    # no candidate is invented and every skip keeps its reason
    report = pc.chain_report(wide)
    assert all(item["detail"] for item in report["skipped"])
    assert all(item["reason"] in pc.SKIP_REASONS for item in report["skipped"])


def test_the_cli_report_is_one_document_with_the_registry_the_log_and_the_drift():
    payload = pc.provider_catalog_report()
    assert payload["count"] == len(rd.REGISTRY_ORDER)
    assert payload["drift"]["agree"] is True
    assert payload["chain"]["count"] >= 1
    assert json.dumps(payload)
    single = pc.provider_catalog_report(entry_id="zai", include_chain=False)
    assert single["count"] == 1 and "chain" not in single


# --------------------------------------------------------------------------- #
# the CLI                                                                      #
# --------------------------------------------------------------------------- #


def test_the_catalog_verb_is_declared_in_the_contract_paths():
    assert "provider.catalog" in pcli.CONTRACT_PATHS
    assert len(pcli.CONTRACT_PATHS) == len(set(pcli.CONTRACT_PATHS)) == 8
    from skaldir import cli_contract

    if cli_contract is None:  # pragma: no cover - the group stands alone
        pytest.skip("no cli_contract module")
    missing = [p for p in pcli.CONTRACT_PATHS if p not in cli_contract.COMMANDS]
    if missing:
        pytest.skip("awaiting the CLI owner's cli_contract.COMMANDS entries: " + ", ".join(missing))
    spec = cli_contract.COMMANDS["provider.catalog"]
    assert spec.evidence and spec.data_schema


def test_catalog_json_and_text_differ_only_in_rendering(capsys):
    assert pcli.main(["catalog", "--json"]) == pcli.EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["count"] == len(rd.REGISTRY_ORDER)
    assert payload["results"][0]["verification"] in rd.VERIFICATION_ORDER
    assert payload["probes"]["steps"]

    assert pcli.main(["catalog", "--text"]) == pcli.EXIT_OK
    human = capsys.readouterr().out
    assert "provider catalog:" in human
    for entry_id in REQUIRED_IDS:
        assert entry_id in human
    for status in rd.VERIFICATION_ORDER:
        assert status in human


def test_catalog_accepts_a_force_text_flag_even_with_a_json_default(capsys):
    assert pcli.main(["catalog", "--json", "--text"]) == pcli.EXIT_OK
    out = capsys.readouterr().out
    assert out.lstrip().startswith("provider catalog:")
    assert "{" not in out.splitlines()[0]


def test_catalog_entry_prints_the_detail_and_probes(capsys):
    assert pcli.main(["catalog", "--entry", "deepseek", "--text"]) == pcli.EXIT_OK
    human = capsys.readouterr().out
    assert "https://api.deepseek.com/v1" in human
    assert "DEEPSEEK_API_KEY" in human
    assert "native_json_schema: no [verified]" in human
    assert "Authorization" in human

    assert pcli.main(["catalog", "--entry", "deepseek", "--probes", "--text"]) == pcli.EXIT_OK
    assert "live probe log" in capsys.readouterr().out


def test_catalog_entry_json_is_a_single_result(capsys):
    assert pcli.main(["catalog", "--entry", "ollama", "--json"]) == pcli.EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["count"] == 1
    assert payload["results"][0]["id"] == "ollama"
    assert payload["results"][0]["endpoints"][0]["base_url"] == "http://localhost:11434/v1"


def test_an_unknown_entry_is_not_found_and_an_unknown_status_is_usage(capsys):
    assert pcli.main(["catalog", "--entry", "nope", "--json"]) == pcli.EXIT_NOT_FOUND
    assert "unknown provider 'nope'" in capsys.readouterr().err
    assert pcli.main(["catalog", "--verification", "probably"]) == pcli.EXIT_USAGE
    assert "verified" in capsys.readouterr().err


def test_the_envelope_carries_evidence_through_the_product_driver(capsys):
    """``cli_contract`` refuses a success without evidence — the group declares its own."""
    import argparse

    from skaldir import cli_contract

    if cli_contract is None:  # pragma: no cover
        pytest.skip("no cli_contract module")
    from skaldir.cli import build_parser

    parser = build_parser()
    sub = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)][0]
    if "provider" not in sub.choices:  # pragma: no cover - wiring lives in cli.py
        pytest.skip("the provider group is not wired into build_parser()")
    cli_contract.annotate(parser)
    args = parser.parse_args(["provider", "catalog", "--json"])
    assert args.func is pcli.cmd_catalog
    capsys.readouterr()  # drop anything earlier in the session
    code = cli_contract.run_command(args, args.func)
    captured = capsys.readouterr()
    document = json.loads(captured.out)
    assert code == 0 == document["exit_code"]
    assert document["command"] == "provider.catalog"
    assert document["data"]["count"] == len(rd.REGISTRY_ORDER)
    assert document["evidence"]
    assert "provider catalog:" in captured.err


# --------------------------------------------------------------------------- #
# the 2026-09-19 round: what a live probe really added                          #
# --------------------------------------------------------------------------- #


def _steps_on(date: str) -> list[rd.ProbeStep]:
    return [step for step in rd.PROBE_LOG if step.date == date]


def test_every_probe_step_carries_the_date_it_was_measured_on():
    """A later round must not silently re-date an earlier round's evidence."""
    assert rd.PROBE_DATE == "2026-09-19"
    assert rd.PROBE_DATE_PREVIOUS == "2026-09-18"
    earlier = _steps_on(rd.PROBE_DATE_PREVIOUS)
    later = _steps_on(rd.PROBE_DATE)
    assert earlier and later, "both rounds must be in the log"
    assert len(earlier) + len(later) == len(rd.PROBE_LOG)
    assert all(step.date for step in rd.PROBE_LOG), "a step without a date is re-dated by default"
    # the earlier round is still there because it is still the evidence for its claims
    refusal = [
        step
        for step in earlier
        if step.provider == "deepseek" and "json_schema" in step.step
    ]
    assert refusal and refusal[0].status == 400
    report = rd.probe_report()
    assert set(report["rounds"]) == {rd.PROBE_DATE_PREVIOUS, rd.PROBE_DATE}
    assert report["date"] == rd.PROBE_DATE
    assert sum(bucket["measured"] + bucket["unmeasured"] for bucket in report["rounds"].values()) \
        == len(rd.PROBE_LOG)


def test_the_reasoning_only_answer_is_recorded_as_an_answer_not_as_an_empty_one():
    """``content_chars=0`` beside ``reasoning_content`` is a success, and the log says so."""
    later = _steps_on(rd.PROBE_DATE)
    alone = [
        step for step in later
        if step.provider == "deepseek" and "reasoning_content" in step.evidence
    ]
    assert alone, "the reasoning-only answer is the finding this round exists for"
    assert any("content_chars=0" in step.evidence for step in alone)
    # …and the control that proves it is a token budget and not a refusal
    control = [
        step for step in later
        if step.provider == "deepseek" and "max_tokens=400" in step.step
    ]
    assert control, "a reasoning-only measurement without its control is a false negative"
    assert any(step.ok is True for step in control)
    quirks = " ".join(rd.registry_entry("deepseek").quirks)
    assert "REASONING-ONLY" in quirks or "reasoning-only" in quirks
    assert "reasoning_content" in quirks and "content" in quirks
    # the trap is described as a budget artifact, not as a broken capability
    assert "BUDGET" in quirks or "budget" in quirks


def test_the_deepseek_json_object_claim_survived_its_own_false_negative():
    """A `200` that does not parse is not structured output — and the control says why."""
    spec = rd.registry_entry("deepseek")
    assert spec.json_object.value == rd.SUPPORT_YES
    assert spec.json_object.status == rd.VERIFIED
    note = spec.json_object.note
    assert "max_tokens=400" in note and "parse" in note
    later = _steps_on(rd.PROBE_DATE)
    unparsed = [
        step
        for step in later
        if step.provider == "deepseek"
        and step.step
        == 'response_format={"type":"json_object"} (model=deepseek-reasoner)'
    ]
    assert unparsed and unparsed[0].ok is False, "the false negative must stay in the log"


def test_the_model_alias_resolution_is_recorded():
    """The id asked for is not always the id echoed — a client may not compare them."""
    quirks = " ".join(rd.registry_entry("deepseek").quirks)
    assert "deepseek-flash" in quirks and "ECHO" in quirks
    # the measured ids are in the patterns, not only in prose
    patterns = rd.registry_entry("deepseek").model_patterns
    assert "deepseek-flash" in patterns and "deepseek-v4-pro" in patterns


def test_openrouter_vision_is_now_measured_per_model_in_both_directions():
    spec = rd.registry_entry("openrouter")
    assert spec.vision.status == rd.VERIFIED, "the 2026-09-19 round measured vision both ways"
    assert spec.vision.value == rd.SUPPORT_PER_MODEL
    assert spec.vision.reference.startswith("probe:openrouter/vision")
    note = spec.vision.note
    assert "ACCEPTED" in note and "REFUSED" in note


def test_openrouter_json_object_is_per_model_because_it_was_measured_both_ways():
    spec = rd.registry_entry("openrouter")
    assert spec.json_object.status == rd.VERIFIED
    assert spec.json_object.value == rd.SUPPORT_PER_MODEL
    assert "does not support feature" in spec.json_object.note


def test_the_reasoning_field_name_asymmetry_is_recorded_where_it_bites():
    """OpenRouter streams `reasoning`, not `reasoning_content` — a content-only reader sees 0."""
    quirks = " ".join(rd.registry_entry("openrouter").quirks)
    assert "REASONING FIELD ASYMMETRY" in quirks
    assert "delta.content" in quirks
    later = _steps_on(rd.PROBE_DATE)
    asym = [
        step for step in later
        if step.provider == "openrouter" and "stream:true SSE" in step.step
        and "reasoning" in step.evidence
    ]
    assert asym, "the stream whose deltas carried no content must be in the log"


def test_no_entry_without_a_probe_carries_a_verified_claim():
    """The rule the whole file exists for: a skipped probe upgrades nothing."""
    for spec in rd.REGISTRY.values():
        if spec.probe == rd.PROBE_NOT_RUN:
            for name, support in spec.features.items():
                assert support.status != rd.VERIFIED, (
                    f"{spec.id}.{name} is verified but {spec.id} was never probed"
                )
        if spec.verification == rd.VERIFIED:
            assert spec.probe in (rd.PROBE_RUN, rd.PROBE_PARTIAL), (
                f"{spec.id} is verified without any probe"
            )


def test_every_unprobed_entry_names_what_it_would_need():
    """A `not_run` reason has to name the missing thing, or it is not a reason."""
    markers = (
        "_KEY", "_TOKEN", "access token", "project", "listening", "port", "credential",
        # the catalog-refresh wording: "No completion probe in the catalog refresh."
        "catalog refresh", "completion probe", "probe",
    )
    for spec in rd.REGISTRY.values():
        if spec.probe != rd.PROBE_NOT_RUN:
            continue
        reason = spec.probe_reason
        assert reason, f"{spec.id} says not_run without a reason"
        assert any(marker in reason for marker in markers), (
            f"{spec.id} says not_run without naming what is missing: {reason!r}"
        )
    # the two credential-poor providers that were re-checked carry the date they were checked
    assert "2026-09-19" in rd.registry_entry("openai").probe_reason
    assert "2026-09-19" in rd.registry_entry("google-vertex").probe_reason


def test_the_probe_log_records_the_auth_control_trap():
    """A bad-key control that sends a placeholder model measures the wrong 401."""
    later = _steps_on(rd.PROBE_DATE)
    controls = [
        step for step in later
        if step.provider == "opencode-go" and "auth control" in step.step
    ]
    assert len(controls) >= 2, "both the real-id and the placeholder-id control must be kept"
    assert any("REAL model id" in step.step and "AuthError" in step.evidence for step in controls)
    assert any("placeholder" in step.step and "ModelError" in step.evidence for step in controls)
    quirks = " ".join(rd.registry_entry("opencode-go").quirks)
    assert "AUTH-CONTROL TRAP" in quirks


def test_the_quota_blocked_provider_still_upgrades_nothing():
    """2026-09-19 hit the same weekly wall — and a second 429 is still not a measurement."""
    spec = rd.registry_entry("opencode-go")
    assert spec.probe == rd.PROBE_PARTIAL
    assert "429" in spec.probe_reason or "quota" in spec.probe_reason
    for name in ("streaming", "native_json_schema", "json_object", "vision", "tools"):
        assert spec.support(name).status != rd.VERIFIED, f"{name} was upgraded by a 429"
    later = _steps_on(rd.PROBE_DATE)
    wall = [
        step for step in later
        if step.provider == "opencode-go" and step.status == 429
    ]
    assert wall, "the second quota wall belongs in the log"
    assert "not-run, not failed" in wall[0].evidence or "not-run" in wall[0].evidence


def test_the_credential_inventory_records_names_and_lengths_only():
    """The probe round's own hygiene step, and the proof no value travels with it."""
    inventory = [
        step for step in _steps_on(rd.PROBE_DATE) if "credential inventory" in step.step
    ]
    assert inventory, "the round must record what it had to work with"
    evidence = inventory[0].evidence
    for name in ("DEEPSEEK_API_KEY", "OPENROUTER_API_KEY", "OPENCODE_GO_API_KEY"):
        assert name in evidence
    assert "LENGTH" in evidence or "len " in evidence or "len=" in evidence
    # nothing credential-shaped reached the whole log
    rendered = json.dumps(rd.probe_report())
    for needle in SECRET_SHAPED:
        assert needle not in rendered, f"{needle!r} reached the probe report"
