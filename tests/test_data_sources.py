"""DOCKER-05: no universal availability claim, no silent paid source.

The requirement forbids three things. Each is one test here:

* "keine universelle Onlineverfügbarkeit behaupten"
* "keine bezahlten Datenquellen stillschweigend hinzufügen"
* "jeweilige Nutzungsbedingungen berücksichtigen"
"""

from __future__ import annotations

import pytest

from skaldir.data_sources import (
    Availability,
    DataSource,
    PaidSourceNotAllowed,
    SourceKind,
    SourceRegistry,
)


@pytest.fixture()
def registry():
    return SourceRegistry()


def _free(name="stooq", **kw):
    return DataSource(name=name, kind=SourceKind.FREE, provider="stooq.com",
                      terms_url="https://stooq.com/legal/", licence="free for research",
                      **kw)


def _paid(name="paid-ticker"):
    return DataSource(name=name, kind=SourceKind.PAID, provider="example-data.com",
                      terms_url="https://example-data.com/terms", licence="commercial")


# --------------------------------------------------------------------------- #
# "keine universelle Onlineverfügbarkeit behaupten"
# --------------------------------------------------------------------------- #

def test_a_source_is_unknown_until_someone_actually_measures_it(registry):
    """Availability is a measurement, never a default."""
    source = registry.register_source(_free())
    assert source.availability is Availability.UNKNOWN
    assert source.measured_at == 0.0
    report = registry.report("stock-research")   # tool not even attached yet
    assert report["declared"] is False
    assert "not the same as 'it needs none'" in report["message"]


def test_the_report_never_claims_availability_it_has_not_measured(registry):
    source = registry.register_source(_free())
    registry.attach("stock-research", source)
    report = registry.report("stock-research")
    assert report["unknown_availability"] == ["stooq"]
    assert "availability is unknown" in report["message"]
    assert "not universal" in report["disclaimer"]


def test_a_probe_that_answers_is_recorded_as_measured_not_assumed(registry):
    source = registry.register_source(_free())
    registry.measure("stooq", lambda: (True, "HTTP 200, 4 quotes returned"))
    assert source.availability is Availability.AVAILABLE
    assert source.measured_at > 0
    assert "HTTP 200" in source.availability_detail
    registry.attach("stock-research", source)
    assert registry.report("stock-research")["unknown_availability"] == []


def test_a_probe_that_throws_is_not_a_pass(registry):
    """A failed measurement must never read as "available"."""
    source = registry.register_source(_free())

    def boom():
        raise OSError("network unreachable")

    registry.measure("stooq", boom)
    assert source.availability is Availability.UNKNOWN
    assert "probe failed" in source.availability_detail


def test_a_source_that_did_not_answer_is_recorded_as_unavailable(registry):
    source = registry.register_source(_free())
    registry.measure("stooq", lambda: (False, "connection refused"))
    assert source.availability is Availability.UNAVAILABLE


# --------------------------------------------------------------------------- #
# "keine bezahlten Datenquellen stillschweigend hinzufügen"
# --------------------------------------------------------------------------- #

def test_a_paid_source_cannot_be_attached_silently(registry):
    paid = registry.register_source(_paid())
    with pytest.raises(PaidSourceNotAllowed) as exc:
        registry.attach("stock-research", paid)
    assert "silently" in str(exc.value)
    assert "APR-02" in str(exc.value), "the money rule must be named, not implied"
    assert registry.for_tool("stock-research") is None


def test_a_paid_source_needs_a_named_approver_not_just_a_flag(registry):
    """`allow_paid=True` alone is not an approval — it has to say who approved it."""
    paid = registry.register_source(_paid())
    with pytest.raises(PaidSourceNotAllowed, match="approved_by"):
        registry.attach("stock-research", paid, allow_paid=True)
    entry = registry.attach("stock-research", paid, allow_paid=True, approved_by="willy")
    assert entry.costs_money is True


def test_a_metered_source_is_also_money_and_also_needs_the_same_attention(registry):
    metered = DataSource(name="quota-api", kind=SourceKind.METERED, provider="example.com",
                         terms_url="https://example.com/terms", licence="metered")
    assert metered.costs_money is True
    with pytest.raises(PaidSourceNotAllowed):
        registry.attach("editing-suite", metered)


def test_a_free_source_is_attached_without_any_ceremony(registry):
    entry = registry.attach("stock-research", _free())
    assert entry.costs_money is False
    assert [s.name for s in entry.sources] == ["stooq"]


def test_attaching_the_same_source_twice_does_not_duplicate_it(registry):
    source = registry.register_source(_free())
    registry.attach("stock-research", source)
    entry = registry.attach("stock-research", source)
    assert len(entry.sources) == 1


def test_a_tool_with_a_paid_source_must_carry_a_spending_approval(registry):
    paid = registry.register_source(_paid())
    registry.attach("stock-research", paid, allow_paid=True, approved_by="willy")
    with pytest.raises(PaidSourceNotAllowed, match="explicit spending approval"):
        registry.assert_no_silent_paid("stock-research")


# --------------------------------------------------------------------------- #
# "jeweilige Nutzungsbedingungen berücksichtigen"
# --------------------------------------------------------------------------- #

def test_a_source_without_terms_or_licence_is_incomplete_not_probably_fine(registry):
    bare = DataSource(name="bare", kind=SourceKind.FREE, provider="somewhere")
    assert bare.is_complete is False
    registry.attach("media-download", bare)
    report = registry.report("media-download")
    assert report["incomplete_terms"] == ["bare"]


def test_a_source_records_what_the_caller_has_to_supply(registry):
    source = DataSource(name="account-api", kind=SourceKind.FREE_ACCOUNT,
                        provider="example.org", terms_url="https://example.org/terms",
                        licence="free with account", requires=("api_key", "region=eu"))
    registry.register_source(source)
    assert source.to_dict()["requires"] == ["api_key", "region=eu"]
    assert source.costs_money is False, "an account is not money — the kinds stay distinct"


def test_detach_removes_the_source_but_keeps_the_history_silent(registry):
    source = registry.register_source(_free())
    registry.attach("stock-research", source)
    assert registry.detach("stock-research", "stooq") is True
    assert registry.for_tool("stock-research").sources == []
    assert registry.detach("stock-research", "stooq") is False


def test_an_unattached_tool_says_so_instead_of_looking_clean(registry):
    report = registry.report("editing-suite")
    assert report["declared"] is False
    assert "not the same as 'it needs none'" in report["message"]
