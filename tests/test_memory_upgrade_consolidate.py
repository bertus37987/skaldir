"""Typed long-term memory — consolidation, duplicates, contradictions (MVP/31).

The properties pinned here:

1. duplicates (exact and near) merge, and the *stronger* copy survives;
2. contradictions are detected from documented, deterministic signals — and the
   detector's known blind spot is pinned as a known blind spot, not hidden;
3. the resolution rule is the documented one, in the documented order
   (owner → weighted evidence → confidence → newest → **flag, never guess**);
4. a dry run writes nothing; ``--apply`` writes and journals;
5. running consolidation twice changes nothing the second time.

The synthetic corpus is 71 memories with 11 duplicates, 6 contradictions, 5
expired rows and one known false negative; the plan declares what must happen.
"""

import pytest

from skaldir.memory_store import (
    STATUS_ACTIVE,
    STATUS_CONTRADICTED,
    STATUS_MERGED,
    STATUS_SUPERSEDED,
    TypedMemoryStore,
    build_synthetic_corpus,
    contradiction_reason,
    effective_score,
    resolve_contradiction,
)

NOW = 1_800_000_000.0  # fixed clock: the corpus is deterministic in structure


@pytest.fixture
def corpus(tmp_path):
    with TypedMemoryStore(tmp_path / "corpus.db", clock=lambda: NOW) as store:
        plan = build_synthetic_corpus(store, now=NOW)
        yield store, plan


def _state(store):
    return store.status_counts(now=NOW)


# -- duplicate merging ------------------------------------------------------- #


def test_dry_run_reports_the_merges_without_writing(corpus):
    store, plan = corpus
    before_rows = store.count()
    before_journal = len(store.journal())
    report = store.consolidate(apply=False)
    assert report.dry_run is True
    assert report.scanned == plan.stored == 71
    assert report.merged == plan.expected_merges == 11
    assert report.superseded == plan.expected_supersessions == 4
    assert report.unresolved_pairs == plan.expected_unresolved_pairs == 1
    assert report.flagged == plan.expected_flags == 2
    assert report.before == report.after, "a dry run cannot change the counts"
    assert store.count() == before_rows
    assert len(store.journal()) == before_journal, "a dry run writes no journal row"
    assert report.needs_attention is True


def test_apply_merges_and_the_counts_move_exactly_as_declared(corpus):
    store, plan = corpus
    report = store.consolidate(apply=True)
    assert report.dry_run is False
    after = report.after
    assert after["active"] == plan.expected_active_after == 49
    assert after["merged"] == 11
    assert after["superseded"] == 4
    assert after["contradicted"] == 2
    assert after["expired"] == plan.expected_expired == 5
    assert sum(after.values()) == store.count() == plan.stored
    assert report.before != after, "before/after must show the change, not two copies of one"


def test_every_merged_row_points_at_its_keeper_and_is_journaled(corpus):
    store, plan = corpus
    store.consolidate(apply=True)
    merged = [memory for memory in store.all() if memory.status == STATUS_MERGED]
    assert len(merged) == 11
    for loser in merged:
        keeper = store.get(loser.merged_into)
        assert keeper is not None and keeper.status == STATUS_ACTIVE
        actions = [row["action"] for row in store.journal(loser.id)]
        assert actions == ["remember", "merge"]
        assert store.journal(loser.id)[-1]["related_id"] == keeper.id
    assert store.journal_consistency()["ok"] is True


def test_the_stronger_copy_survives_a_merge(corpus):
    """The keeper of a duplicate pair is the one with the better evidence."""
    store, plan = corpus
    store.consolidate(apply=True)
    keeper = store.get(plan.ids["dup_gate_a"])  # owner-authored, 1 evidence ref
    loser = store.get(plan.ids["dup_gate_b"])  # agent, no evidence
    assert keeper.status == STATUS_ACTIVE
    assert loser.status == STATUS_MERGED and loser.merged_into == keeper.id
    assert effective_score(keeper) > effective_score(loser)
    assert loser.id in keeper.provenance["merged_from"]


def test_a_single_differing_token_is_a_near_duplicate(corpus):
    """'pytest runs the test suite' vs '... the full test suite' → merged."""
    store, plan = corpus
    store.consolidate(apply=True)
    assert store.get(plan.ids["near_pytest_b"]).merged_into == plan.ids["near_pytest_a"]
    assert store.get(plan.ids["near_sed_b"]).merged_into == plan.ids["near_sed_a"]


def test_a_pair_below_the_threshold_is_left_alone(tmp_path):
    with TypedMemoryStore(tmp_path / "t.db") as store:
        first = store.remember("alpha.rule: keep the retry budget small", confidence=0.9)
        second = store.remember("beta.rule: keep the retry budget small", confidence=0.8)
        report = store.consolidate(apply=True)
        assert report.merged == 0
        assert store.get(first.id).status == STATUS_ACTIVE
        assert store.get(second.id).status == STATUS_ACTIVE


def test_duplicates_in_different_scopes_are_not_merged(tmp_path):
    """Merging across scopes would erase the scope distinction the store keeps."""
    with TypedMemoryStore(tmp_path / "t.db") as store:
        prod = store.remember("deploy.port: 8080", scope="project", scope_id="prod")
        staging = store.remember("deploy.port: 8080", scope="project", scope_id="staging")
        report = store.consolidate(apply=True)
        assert report.merged == 0
        assert store.get(prod.id).status == STATUS_ACTIVE
        assert store.get(staging.id).status == STATUS_ACTIVE


# -- contradiction detection ------------------------------------------------- #


@pytest.mark.parametrize(
    "left,right,reason_part",
    [
        ("port: use 8080", "port: use 9090", "conflicting values"),
        ("logging: use journald", "logging: avoid journald", "opposite polarity"),
        ("build.state: stable", "build.state: broken", "antonym"),
    ],
)
def test_detection_signals(tmp_path, left, right, reason_part):
    with TypedMemoryStore(tmp_path / "t.db") as store:
        first = store.remember(left)
        second = store.remember(right)
        reason = contradiction_reason(first, second)
        assert reason_part in reason


def test_an_explicit_link_names_the_pair_but_does_not_pick_a_winner(tmp_path):
    """``--contradicts`` says "these cannot both hold" — not which one is right.

    Nothing in an explicit link ranks the two sides, so with equal weight,
    confidence and timestamp the rule flags them instead of guessing.
    """
    with TypedMemoryStore(tmp_path / "t.db") as store:
        first = store.remember("colour.rule: blue")
        second = store.remember("colour.rule: green", contradicts=[first.id])
        assert "explicit link" in contradiction_reason(first, second)
        report = store.consolidate(apply=True)
        assert report.superseded == 0
        assert report.flagged == 2 and report.unresolved_pairs == 1
        assert store.get(first.id).status == STATUS_CONTRADICTED
        assert store.get(second.id).status == STATUS_CONTRADICTED


def test_an_explicit_link_resolves_when_one_side_is_stronger(tmp_path):
    with TypedMemoryStore(tmp_path / "t.db") as store:
        owner = store.remember("colour.rule: blue", source="owner")
        agent = store.remember("colour.rule: green", source="agent", contradicts=[owner.id])
        report = store.consolidate(apply=True)
        assert report.superseded == 1
        assert store.get(agent.id).status == STATUS_SUPERSEDED
        assert store.get(owner.id).status == STATUS_ACTIVE


def test_a_different_scope_suppresses_a_false_contradiction(corpus):
    """The measured guard: 9090 in staging does not contradict 8080 in prod."""
    store, plan = corpus
    staging = store.get(plan.ids["port_staging"])
    owner = store.get(plan.ids["port_prod_owner"])
    assert contradiction_reason(staging, owner) == ""
    assert staging.scope_key() != owner.scope_key()
    assert staging.status == STATUS_ACTIVE and owner.status == STATUS_ACTIVE
    report = store.consolidate(apply=True)
    assert not any(
        action.get("ids") == [staging.id, owner.id] for action in report.actions
    ), "the scope guard must keep this pair out of the conflict list"


def test_a_different_subject_is_never_a_contradiction(tmp_path):
    with TypedMemoryStore(tmp_path / "t.db") as store:
        first = store.remember("port: use 8080")
        second = store.remember("timeout: use 9090")
        assert contradiction_reason(first, second) == ""


def test_the_known_false_negative_is_pinned(corpus):
    """A blind spot of a lexical detector, kept visible on purpose.

    Two conflicting proper nouns under one subject share polarity, share no
    antonym and carry no number, so nothing flags them. MVP/31 reports this
    instead of claiming complete contradiction detection; catching it needs a
    semantic provider (the current provider is verified lexical — see the store
    tests).
    """
    store, plan = corpus
    redis = store.get(plan.ids["cache_redis"])
    memcached = store.get(plan.ids["cache_memcached"])
    assert contradiction_reason(redis, memcached) == ""
    assert plan.known_false_negatives[0]["ids"] == [redis.id, memcached.id]
    report = store.consolidate(apply=True)
    assert report.flagged == 2, "only the genuine tie pair is flagged, not this pair"
    assert redis.status == STATUS_ACTIVE and memcached.status == STATUS_ACTIVE


# -- the resolution rule ----------------------------------------------------- #


def test_owner_authored_wins_over_a_newer_more_confident_agent_claim(corpus):
    store, plan = corpus
    report = store.consolidate(apply=True)
    winner = store.get(plan.ids["release_owner_truth"])
    loser = store.get(plan.ids["release_owner_agent"])
    assert winner.status == STATUS_ACTIVE
    assert loser.status == STATUS_SUPERSEDED and loser.superseded_by == winner.id
    assert loser.confidence > winner.confidence, "raw confidence must not have decided it"
    assert loser.created_at > winner.created_at, "nor recency"
    basis = next(a["basis"] for a in report.actions if a.get("superseded") == loser.id)
    assert "owner-authored" in basis


def test_weighted_evidence_beats_higher_raw_confidence(corpus):
    store, plan = corpus
    store.consolidate(apply=True)
    winner = store.get(plan.ids["log_winner"])
    loser = store.get(plan.ids["log_loser"])
    assert loser.status == STATUS_SUPERSEDED and winner.status == STATUS_ACTIVE
    assert loser.created_at > winner.created_at, "the loser is the newer one here"
    _, _, verdict, basis = resolve_contradiction(winner, loser)
    assert verdict == "supersede" and "evidence-decisive" in basis


def test_newest_wins_when_weight_and_confidence_are_equal(corpus):
    store, plan = corpus
    store.consolidate(apply=True)
    winner = store.get(plan.ids["timeout_new"])
    loser = store.get(plan.ids["timeout_old"])
    assert winner.status == STATUS_ACTIVE
    assert loser.status == STATUS_SUPERSEDED and loser.superseded_by == winner.id
    _, _, verdict, basis = resolve_contradiction(loser, winner)
    assert verdict == "supersede" and "newest" in basis


def test_a_genuine_tie_is_flagged_not_guessed(corpus):
    store, plan = corpus
    report = store.consolidate(apply=True)
    first = store.get(plan.ids["tie_redis"])
    second = store.get(plan.ids["tie_memcached"])
    assert first.status == second.status == STATUS_CONTRADICTED
    assert first.conflict_group == second.conflict_group != ""
    assert first.superseded_by == "" and second.superseded_by == ""
    _, _, verdict, basis = resolve_contradiction(first, second)
    assert verdict == "unresolved"
    assert "undecidable" in basis and "owner-authored decision" in basis
    assert report.warnings and "unresolved contradiction" in report.warnings[0]
    assert report.needs_attention is True
    for memory in (first, second):
        rows = [row["action"] for row in store.journal(memory.id, include_related=False)]
        assert rows == ["remember", "contradict"]


def test_confidence_decides_when_evidence_is_equal(tmp_path):
    """The confidence branch, isolated inside its reachable band.

    With equal evidence weight the weighted score is ``0.75·confidence``, so the
    evidence branch takes over whenever the confidence gap exceeds ~0.27. The
    confidence branch therefore decides the band 0.15 … 0.27 — this test pins
    that band explicitly rather than pretending the branches are independent.
    """
    with TypedMemoryStore(tmp_path / "t.db") as store:
        high = store.remember("timeout: use 30 seconds", confidence=0.9)
        low = store.remember("timeout: use 20 seconds", confidence=0.7)
        winner, loser, verdict, basis = resolve_contradiction(high, low)
        assert verdict == "supersede" and "confidence-decisive" in basis
        assert abs(effective_score(high) - effective_score(low)) < 0.20
        report = store.consolidate(apply=True)
        assert report.superseded == 1
        assert store.get(low.id).status == STATUS_SUPERSEDED


def test_the_evidence_branch_takes_over_above_its_threshold(tmp_path):
    """A larger confidence gap is decided by the weighted score, not by confidence.

    This is the documented interaction between steps 2 and 3 — and the reason
    the basis string is always reported, so a reader can see which step fired.
    """
    with TypedMemoryStore(tmp_path / "t.db") as store:
        high = store.remember("timeout: use 30 seconds", confidence=0.9)
        low = store.remember("timeout: use 20 seconds", confidence=0.5)
        _, _, verdict, basis = resolve_contradiction(high, low)
        assert verdict == "supersede" and basis.startswith("evidence-decisive")


def test_a_live_conflict_is_surfaced_before_consolidation(tmp_path):
    """The ranker may not resolve a conflict — but it must not hide one either.

    Measured before this check landed: the newer agent claim took rank 1
    (0.9557 vs 0.9044) and the dispute was invisible until `consolidate` ran.
    Now both sides are penalised, both are flagged, and the hit names which side
    the documented rule would keep — so a caller reading one hit learns there is
    a dispute instead of trusting the newer one.
    """
    with TypedMemoryStore(tmp_path / "t.db", clock=lambda: NOW) as store:
        owner = store.remember(
            "release.owner: marta",
            kind="entity",
            source="owner",
            confidence=0.9,
            provenance={"source": "owner", "evidence": ["a", "b"]},
            now=NOW - 200 * 86400,
        )
        agent = store.remember(
            "release.owner: not marta any more, tomas owns it",
            kind="entity",
            source="agent",
            confidence=0.95,
            now=NOW - 2 * 86400,
        )
        store.consolidate(apply=False)  # the dispute is still live
        hits = store.recall("release owner", k=2, now=NOW)
        assert {hit.memory_id for hit in hits} == {owner.id, agent.id}
        for hit in hits:
            assert "contradicts-active" in hit.flags
            assert hit.to_dict()["contradicts"][0]["would_win"] == owner.id
            assert hit.to_dict()["contradicts"][0]["basis"].startswith("owner-authored")
        # and once consolidation has run, the loser is gone and the flags with it
        store.consolidate(apply=True)
        after = store.recall("release owner", k=2, now=NOW)
        assert [hit.memory_id for hit in after] == [owner.id]
        assert after[0].flags == []


def test_a_conflict_with_a_retired_row_does_not_penalise_the_winner(corpus):
    store, plan = corpus
    store.consolidate(apply=True)
    hits = store.recall("logging sink decision", k=3)
    winner = next(hit for hit in hits if hit.memory_id == plan.ids["log_winner"])
    assert "contradicts-active" not in winner.flags


def test_a_flagged_contradiction_is_penalised_but_still_returned(corpus):
    """Unresolved means "usable but suspect" — never "silently trusted"."""
    store, plan = corpus
    store.consolidate(apply=True)
    hits = [hit for hit in store.recall("cache eviction preference", k=10)
            if hit.status == STATUS_CONTRADICTED]
    assert hits, "both sides of an unresolved pair stay retrievable"
    for hit in hits:
        assert "unresolved-contradiction" in hit.flags


def test_consolidation_is_idempotent(corpus):
    store, plan = corpus
    store.consolidate(apply=True)
    journal_after_first = len(store.journal())
    second = store.consolidate(apply=True)
    assert second.merged == 0 and second.superseded == 0 and second.flagged == 0
    assert second.unresolved_pairs == 1, "the open contradiction is still open"
    assert second.needs_attention is True
    assert len(store.journal()) == journal_after_first, "nothing was written a second time"
    assert any(a["action"] == "already-flagged" for a in second.actions)
    assert store.count() == plan.stored


def test_consolidation_run_is_recorded(corpus):
    store, _plan = corpus
    store.consolidate(apply=True)
    row = store._conn.execute(
        "SELECT dry_run, scanned, merged, flagged, report FROM consolidation_runs"
    ).fetchone()
    assert row is not None and row["dry_run"] == 0
    assert row["scanned"] == 71 and row["merged"] == 11
    assert '"merged": 11' in row["report"]
