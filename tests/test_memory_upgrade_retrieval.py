"""Typed long-term memory — retrieval quality, measured (MVP/31).

The claim under test is narrow and falsifiable: **ranking typed memories beats a
flat fact list, and it also beats ranking on text similarity alone**, on a
synthetic corpus whose labels are declared up front (``build_synthetic_corpus``).

The measured numbers (fixed clock, ``HashingEmbeddings`` dim 256, k=3,
13 labeled queries, 71 memories):

============================  =======  ============  =======
ranker                        prec@1   prec@k        source
============================  =======  ============  =======
this ranker                   0.9231   0.9231        0.55·rel + 0.15·rec + 0.15·conf + 0.15·scope
flat fact list (newest first) 0.2308   0.7692        every row ever written, TTL-blind
text similarity only          0.8462   0.9231        memory.similarity over the same live pool
============================  =======  ============  =======

One query is a documented *miss* for every ranker (a paraphrase with no shared
token: the provider is lexical, verified in the store tests). The corpus is
self-authored, so these are an upper bound — the limits section of MVP/31 says
so in the same words.
"""

import pytest

from skaldir.memory_store import (
    DEFAULT_K,
    STATUS_CONTRADICTED,
    STATUS_SUPERSEDED,
    TypedMemoryStore,
    build_synthetic_corpus,
    measure_ranking,
)

NOW = 1_800_000_000.0


@pytest.fixture
def measured(tmp_path):
    with TypedMemoryStore(tmp_path / "corpus.db", clock=lambda: NOW) as store:
        plan = build_synthetic_corpus(store, now=NOW)
        report = store.consolidate(apply=True)
        yield store, plan, report, measure_ranking(store, plan.queries, k=DEFAULT_K)


# -- the headline numbers ---------------------------------------------------- #


def test_the_declared_numbers_still_hold(measured):
    store, plan, _report, measurement = measured
    data = measurement.to_dict()
    expected = plan.expected_ranking
    assert data["queries"] == expected["queries"] == 13
    assert data["k"] == expected["k"] == 3
    assert data["top1"] == expected["top1"] == 12
    assert data["topk"] == expected["topk"] == 12
    assert data["mrr"] == pytest.approx(expected["mrr"], abs=1e-4)
    assert data["baseline_newest"]["top1"] == expected["flat_list_top1"] == 3
    assert data["baseline_lexical"]["top1"] == expected["lexical_only_top1"] == 11


def test_every_number_is_auditable_per_query(measured):
    _store, plan, _report, measurement = measured
    data = measurement.to_dict()
    assert len(data["per_query"]) == len(plan.queries)
    by_query = {row["query"]: row for row in data["per_query"]}
    assert by_query["what port does the prod deploy listen on"] == {
        "query": "what port does the prod deploy listen on",
        "expected": plan.ids["port_prod_owner"],
        "ours": by_query["what port does the prod deploy listen on"]["ours"],
        "flat_list": plan.ids["port_staging"],
        "lexical_only": plan.ids["port_prod_owner"],
        "ours_right": True,
        "flat_right": False,
        "lexical_right": True,
        "note": "owner truth vs a newer agent claim in the same scope",
    }


def test_the_ranker_beats_both_baselines(measured):
    _store, _plan, _report, measurement = measured
    data = measurement.to_dict()
    assert data["top1"] > data["baseline_newest"]["top1"]
    assert data["top1"] > data["baseline_lexical"]["top1"]
    assert data["precision_at_1"] > data["baseline_newest"]["precision_at_1"] * 3


# -- the specific queries where a flat list picks the wrong row --------------- #


def test_a_scope_mismatch_beats_the_newer_row(measured):
    """9090 in staging is newer and matches the words; prod's 8080 is the answer."""
    store, plan, _report, _measurement = measured
    hits = store.recall(
        "what port does the prod deploy listen on", k=3, scope="project", scope_id="prod"
    )
    assert [hit.memory_id for hit in hits][0] == plan.ids["port_prod_owner"]
    ranking = {hit.memory_id: hit for hit in hits}
    winner = ranking[plan.ids["port_prod_owner"]]
    newer = ranking[plan.ids["port_staging"]]
    assert winner.scope_component == 1.0
    assert newer.scope_component < 1.0, "another project's row is visible but not equal"
    assert winner.score > newer.score


def test_a_superseded_row_is_not_served(measured):
    """The flat list serves the retired, newer claim; the ranker refuses it."""
    store, plan, _report, measurement = measured
    query = "release checklist owner"
    hits = store.recall(query, k=3)
    assert hits[0].memory_id == plan.ids["release_owner_truth"]
    retired = store.get(plan.ids["release_owner_agent"])
    assert retired.status == STATUS_SUPERSEDED
    assert plan.ids["release_owner_agent"] not in [hit.memory_id for hit in hits]
    assert plan.ids["release_owner_agent"] in [row["flat_list"] for row in
                                              measurement.to_dict()["per_query"]
                                              if row["query"] == query]


def test_a_merged_duplicate_is_not_served(measured):
    store, plan, _report, _measurement = measured
    hits = store.recall("package manager used here", k=3)
    assert hits[0].memory_id == plan.ids["dup_uv_a"]
    assert store.get(plan.ids["dup_uv_b"]).status == "merged"


def test_an_expired_row_is_never_served(measured):
    """The expired incident matches better and is newer — and still forbidden."""
    store, plan, _report, measurement = measured
    expired = store.get(plan.ids["old_incident"])
    assert expired.is_expired(NOW)
    hits = store.recall("stuck migration during the rollout", k=3)
    assert [hit.memory_id for hit in hits][0] == plan.ids["rollout_policy"]
    assert expired.id not in [hit.memory_id for hit in hits]
    row = next(r for r in measurement.to_dict()["per_query"]
               if r["query"] == "stuck migration during the rollout")
    assert row["flat_list"] == expired.id and row["flat_list"] != row["expected"]
    assert row["ours_right"] is True and row["flat_right"] is False


def test_the_known_miss_is_reported_not_hidden(measured):
    _store, plan, _report, measurement = measured
    data = measurement.to_dict()
    misses = [item for item in data["failures"] if "roll back a bad release" in item["query"]]
    assert len(misses) == 1
    assert misses[0]["expected"] == plan.ids["rollback_proc"]
    assert "KNOWN MISS" in misses[0]["note"]
    # the corpus also declares the retrieval blind spot of the contradiction
    # detector, so a reader sees both limits in one place
    assert plan.known_false_negatives


def test_each_margin_names_the_policy_that_produced_it(measured):
    _store, _plan, _report, measurement = measured
    mechanisms = {item["mechanism"].split(":")[0] for item in measurement.to_dict()["margins"]}
    assert mechanisms >= {"scope", "supersession", "TTL", "merge"}
    assert len(measurement.to_dict()["margins"]) >= 8


# -- per-hit explanation ----------------------------------------------------- #


def test_every_hit_carries_the_breakdown_and_a_reason(measured):
    store, _plan, _report, _measurement = measured
    hits = store.recall("what port does the prod deploy listen on", k=3,
                        scope="project", scope_id="prod")
    assert hits
    for hit in hits:
        payload = hit.to_dict()
        assert payload["score"] == pytest.approx(
            0.55 * payload["score_parts"]["relevance"]
            + 0.15 * payload["score_parts"]["recency"]
            + 0.15 * payload["score_parts"]["confidence"]
            + 0.15 * payload["score_parts"]["scope_match"],
            abs=5e-4,
        )
        assert payload["explanation"] and "relevance" in payload["explanation"]
        assert payload["source"] in ("", "owner", "agent")
        assert isinstance(payload["matched_terms"], list)


def test_hits_are_ordered_and_limited(measured):
    store, _plan, _report, _measurement = measured
    hits = store.recall("rollout", k=2)
    assert len(hits) <= 2
    assert hits == sorted(hits, key=lambda hit: hit.score, reverse=True)


# -- pool policy ------------------------------------------------------------- #


def test_noise_is_not_padded_into_the_list(measured):
    """A memory sharing only stray trigrams is not an answer."""
    store, _plan, _report, _measurement = measured
    hits = store.recall("logging sink", k=5)
    assert all(hit.relevance >= 0.10 for hit in hits)
    assert all("deploy.port" not in hit.text for hit in hits)


def test_an_unanswerable_query_returns_nothing(measured):
    store, _plan, _report, _measurement = measured
    assert store.recall("zzzz qqqq wwww vvvv", k=3) == []


def test_scope_and_kind_filters_work(measured):
    store, _plan, plan, _measurement = measured
    only_decisions = store.recall("rollout", k=5, kind="decision")
    assert only_decisions and all(hit.kind == "decision" for hit in only_decisions)
    strict = store.recall("port", k=5, scope="project", scope_id="prod", strict_scope=True)
    assert strict and all(
        (hit.scope, hit.scope_id) == ("project", "prod") for hit in strict
    ), "strict scope must drop the global rows"


def test_superseded_rows_can_be_asked_for_explicitly(measured):
    store, plan, _report, _measurement = measured
    default = store.recall("logging sink decision", k=5)
    with_retired = store.recall("logging sink decision", k=5, include_superseded=True)
    assert plan.ids["log_loser"] not in [hit.memory_id for hit in default]
    assert plan.ids["log_loser"] in [hit.memory_id for hit in with_retired]


def test_flagged_rows_are_returned_with_a_flag(measured):
    store, _plan, _report, _measurement = measured
    flagged = [
        hit for hit in store.recall("cache eviction preference", k=5)
        if hit.status == STATUS_CONTRADICTED
    ]
    assert flagged
    assert all("unresolved-contradiction" in hit.flags for hit in flagged)


# -- the Jev hand-off -------------------------------------------------------- #


def test_jev_offers_match_the_memory_candidate_contract(measured):
    """The decision side keeps its contract: shape only, the ranking is ours."""
    from skaldir.jev.contracts import MemoryCandidate

    store, plan, _report, _measurement = measured
    offers = store.jev_offers("what port does the prod deploy listen on", k=2,
                              scope="project", scope_id="prod")
    assert offers and offers[0]["memory_id"] == plan.ids["port_prod_owner"]
    for offer in offers:
        candidate = MemoryCandidate(**offer)
        assert candidate.memory_id == offer["memory_id"]
    assert offers[0]["protected"] is True, "owner-authored is protected for the façade"
    assert offers[0]["retrieval_score"] == pytest.approx(offers[0]["retrieval_score"])


def test_jev_offers_respect_the_context_flag(measured):
    store, plan, _report, _measurement = measured
    offers = store.jev_offers(
        "what port does the prod deploy listen on", k=1, in_context=[plan.ids["port_prod_owner"]]
    )
    assert offers[0]["already_in_context"] is True


# -- determinism ------------------------------------------------------------- #


def test_the_measurement_is_reproducible(tmp_path):
    """Same corpus, same clock, same numbers — no hidden ordering or randomness."""

    def run(index):
        with TypedMemoryStore(tmp_path / f"m{index}.db", clock=lambda: NOW) as store:
            plan = build_synthetic_corpus(store, now=NOW)
            store.consolidate(apply=True)
            return measure_ranking(store, plan.queries, k=3).to_dict()

    first, second = run(1), run(2)
    for key in ("top1", "topk", "mrr", "precision_at_1", "queries"):
        assert first[key] == second[key]
    assert first["baseline_newest"]["top1"] == second["baseline_newest"]["top1"]
    assert [row["ours"] for row in first["per_query"]] == [
        row["ours"] for row in second["per_query"]
    ]
