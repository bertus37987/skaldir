"""Typed long-term memory — the store (MVP/31, STORE side).

Every test here pins one property the upgrade claims, so the claim is checkable
rather than asserted in prose:

1. types carry provenance, confidence, scope and a decay policy;
2. no silent writes: every mutation is journaled, and integrity is checkable;
3. TTL is enforced (an expired memory is not retrievable) and decay is a
   documented, deterministic function;
4. refusals are explicit (unknown kind/scope, bad confidence, scope without id);
5. the embedding provider was *verified usable* before anything ranked on it —
   and it is lexical, not semantic (probed, not assumed).
"""

import math

import pytest

from skaldir.embedding import HashingEmbeddings
from skaldir.memory_store import (
    DEFAULT_HALF_LIFE_DAYS,
    DEFAULT_TTL_DAYS,
    KINDS,
    SOURCE_WEIGHT,
    STATUS_EXPIRED,
    STATUS_FORGOTTEN,
    MemoryNotFound,
    MemoryValidationError,
    TypedMemoryStore,
    decay_factor,
    effective_score,
    evidence_weight,
    normalize_subject,
    polarity,
    split_subject,
    validate_embedding_provider,
    values_in,
)


@pytest.fixture
def store(tmp_path):
    with TypedMemoryStore(tmp_path / "typed.db") as opened:
        yield opened


# -- types, provenance, scope ------------------------------------------------ #


def test_every_declared_kind_stores_with_its_own_decay_policy(store):
    for kind in KINDS:
        memory = store.remember(f"{kind}.example: a {kind} claim", kind=kind)
        assert memory.kind == kind
        assert memory.half_life_seconds == DEFAULT_HALF_LIFE_DAYS[kind] * 86400
        assert memory.ttl_seconds == DEFAULT_TTL_DAYS[kind] * 86400
    assert store.count() == len(KINDS)
    # only incidents expire by default: an operational trace goes stale, a
    # preference does not
    expired = store.status_counts()
    assert list(expired) == ["active"]


def test_provenance_confidence_scope_and_timestamps_are_recorded(store):
    memory = store.remember(
        "deploy.port: prod listens on 8080",
        kind="fact",
        scope="project",
        scope_id="prod",
        confidence=0.9,
        provenance={
            "source": "owner",
            "session": "s-1",
            "evidence": ["src/skaldir/cli.py", "run:42"],
        },
    )
    assert memory.scope == "project" and memory.scope_id == "prod"
    assert memory.provenance["source"] == "owner"
    assert memory.provenance["evidence"] == ["src/skaldir/cli.py", "run:42"]
    assert memory.created_at == memory.updated_at
    assert memory.revision == 1
    assert memory.subject == "deploy port"
    assert memory.polarity == 1
    assert store.get(memory.id).to_dict() == memory.to_dict()


def test_scope_refusals_are_explicit(store):
    with pytest.raises(MemoryValidationError, match="scope"):
        store.remember("a: b", scope="project")  # project scope without an id
    with pytest.raises(MemoryValidationError, match="global"):
        store.remember("a: b", scope="global", scope_id="prod")
    assert store.count() == 0, "a refused memory must not be half-written"


@pytest.mark.parametrize(
    "kwargs,message",
    [
        ({"kind": "factoid"}, "kind"),
        ({"scope": "galaxy"}, "scope"),
        ({"confidence": 1.5}, "confidence"),
        ({"confidence": "high"}, "confidence"),
        ({"text": "   "}, "empty"),
        ({"ttl_seconds": -5}, "ttl"),
    ],
)
def test_input_refusals_name_the_field(store, kwargs, message):
    payload = {"text": "a: b", **kwargs}
    with pytest.raises(MemoryValidationError, match=message):
        store.remember(payload.pop("text"), **payload)
    assert store.count() == 0


def test_confidence_zero_is_allowed_and_low_confidence_loses(store):
    low = store.remember("a.answer: 1", confidence=0.0)
    high = store.remember("a.answer: 2", confidence=0.9)
    assert low.confidence == 0.0
    assert effective_score(high) > effective_score(low)


# -- subject, polarity, values (the deterministic comparison keys) ----------- #


def test_subject_convention_is_the_comparison_key(store):
    memory = store.remember("deploy.port: prod listens on 8080")
    assert memory.subject == "deploy port"
    assert normalize_subject("deploy.port") == normalize_subject("Deploy_Port")
    assert split_subject("plain prose without a key") == ("", "plain prose without a key")
    assert store.remember("plain prose without a key").subject == ""


@pytest.mark.parametrize(
    "text,expected",
    [
        ("never use sed in place", -1),
        ("avoid the global lock", -1),
        ("use sed in place", 1),
        ("the port is 8080", 1),
    ],
)
def test_polarity_is_a_signal_not_a_parser(text, expected):
    assert polarity(text) == expected


def test_values_in_reads_numbers():
    assert values_in("port 8080, timeout 30s") == ["8080", "30"]
    assert values_in("no numbers here") == []


# -- decay and TTL ----------------------------------------------------------- #


def test_decay_is_half_life_based_and_deterministic(store):
    now = 1_700_000_000.0
    memory = store.remember("a: b", kind="fact", now=now)
    half_life = memory.half_life_seconds
    assert decay_factor(memory, now) == 1.0
    assert math.isclose(decay_factor(memory, now + half_life), 0.5, rel_tol=1e-9)
    assert math.isclose(decay_factor(memory, now + 2 * half_life), 0.25, rel_tol=1e-9)


def test_ttl_expiry_is_enforced_and_computed_not_stored(store):
    now = 1_700_000_000.0
    memory = store.remember(
        "flaky.incident: the suite flakes under load",
        kind="incident",
        ttl_seconds=5 * 86400,
        now=now,
    )
    assert not memory.is_expired(now + 4 * 86400)
    assert memory.is_expired(now + 6 * 86400)
    counts = store.status_counts(now=now + 6 * 86400)
    assert counts == {STATUS_EXPIRED: 1}
    # the stored row still says active: expiry is a policy reading, not a write
    assert store.get(memory.id).status == "active"


def test_ttl_zero_means_never_expires(store):
    now = 1_700_000_000.0
    memory = store.remember("a: b", ttl_seconds=0, now=now)
    assert not memory.is_expired(now + 100 * 365 * 86400)


# -- no silent writes -------------------------------------------------------- #


def test_every_mutation_writes_a_journal_row(store):
    memory = store.remember("a: b", source="agent")
    store.forget(memory.id, reason="wrong")
    actions = [row["action"] for row in store.journal(memory.id)]
    assert actions == ["remember", "forget"]
    assert store.journal_consistency()["ok"] is True


def test_journal_integrity_detects_a_hand_written_row(store):
    """The integrity check is real: break the journal and it must notice."""
    memory = store.remember("a: b")
    store._conn.execute("DELETE FROM memory_journal WHERE memory_id = ?", (memory.id,))
    store._conn.commit()
    report = store.journal_consistency()
    assert report["ok"] is False
    assert report["missing"] == [{"id": memory.id, "missing": "remember"}]


def test_remember_dry_run_writes_nothing_at_all(store):
    memory = store.remember("a: b", dry_run=True)
    assert memory.id.startswith("mem_")
    assert store.count() == 0
    assert store.journal() == []


# -- forget ------------------------------------------------------------------ #


def test_forget_is_soft_by_default_and_keeps_the_history(store):
    memory = store.remember("a: b")
    forgotten = store.forget(memory.id, actor="owner", reason="no longer true")
    assert forgotten.status == STATUS_FORGOTTEN
    assert store.get(memory.id).status == STATUS_FORGOTTEN
    assert [row["action"] for row in store.journal(memory.id)] == ["remember", "forget"]
    assert store.journal(memory.id)[-1]["detail"] == {"reason": "no longer true", "hard": False}


def test_forget_hard_removes_the_row_but_keeps_the_journal(store):
    memory = store.remember("a: b")
    store.forget(memory.id, hard=True)
    assert store.get(memory.id) is None
    assert [row["action"] for row in store.journal(memory.id)] == ["remember", "forget"]


def test_forget_unknown_id_raises_not_found(store):
    with pytest.raises(MemoryNotFound):
        store.forget("mem_doesnotexist")
    with pytest.raises(MemoryNotFound):
        store.require("mem_doesnotexist")


def test_forget_dry_run_writes_nothing(store):
    memory = store.remember("a: b")
    store.forget(memory.id, dry_run=True, reason="pretend")
    assert store.get(memory.id).status == "active"
    assert [row["action"] for row in store.journal(memory.id)] == ["remember"]


# -- evidence weight --------------------------------------------------------- #


def test_evidence_weight_ranks_sources_and_counts_refs():
    owner = {"source": "owner", "evidence": ["a", "b", "c"]}
    guess = {"source": "guess"}
    assert evidence_weight(owner) == pytest.approx(1.0)
    assert evidence_weight(guess) == pytest.approx(SOURCE_WEIGHT["guess"])
    assert evidence_weight({}) == 0.5, "an unknown source is neither trusted nor dismissed"
    assert evidence_weight({"source": "agent", "evidence": ["x"]}) > SOURCE_WEIGHT["agent"]
    # the OWNER with no refs still beats a high-confidence guess with three
    assert evidence_weight({"source": "owner"}) > evidence_weight(
        {"source": "guess", "evidence": ["a"]}
    )


# -- the embedding provider is verified, not assumed ------------------------- #


def test_embedding_provider_is_usable_but_lexical():
    facts = validate_embedding_provider()
    assert facts["usable"] is True
    assert facts["deterministic"] is True
    assert facts["dim_matches_contract"] is True
    assert facts["lexical_probe"]["same_tokens_reworded"] > 0.3
    # A paraphrase sharing no tokens scores ~0: this is the documented reason the
    # ranker cannot answer "how do we roll back a bad release" from "revert
    # procedure for broken deploys" (MVP/31, measured failures).
    assert facts["lexical_probe"]["paraphrase_no_shared_tokens"] < 0.15
    assert facts["semantic"] is False


def test_provider_is_deterministic_and_unit_norm():
    provider = HashingEmbeddings()
    vector = provider.embed("deploy port 8080")
    assert vector == provider.embed("deploy port 8080")
    assert math.isclose(math.sqrt(sum(value * value for value in vector)), 1.0, rel_tol=1e-9)


# -- scope report ------------------------------------------------------------ #


def test_scope_report_counts_scopes_kinds_and_integrity(store):
    store.remember("a: 1", scope="project", scope_id="prod")
    store.remember("b: 2", scope="project", scope_id="prod", kind="decision")
    store.remember("c: 3")
    report = store.scope_report()
    assert report["total"] == 3
    scopes = {f"{item['scope']}:{item['scope_id']}" for item in report["scopes"]}
    assert scopes == {"global:", "project:prod"}
    assert report["journal_rows"] == 3
    assert report["decay_policy"]["incident"]["ttl_days"] == DEFAULT_TTL_DAYS["incident"]
    assert store.journal_consistency()["ok"] is True


# -- persistence ------------------------------------------------------------- #


def test_store_survives_reopen(tmp_path):
    path = tmp_path / "typed.db"
    with TypedMemoryStore(path) as first:
        memory = first.remember("a: b", kind="preference")
    with TypedMemoryStore(path) as second:
        assert second.get(memory.id).text == "a: b"
        assert second.journal_consistency()["ok"] is True
