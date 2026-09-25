"""WEB-01…04: a free search path exists, and the gaps are named rather than glossed over.

WEB-01: "Mindestens einen tatsächlich nutzbaren Pfad ohne zusätzlichen kostenpflichtigen
Such-API-Schlüssel bereitstellen. Nicht pauschal behaupten, kostenlose öffentliche
Endpunkte seien immer verfügbar."

That second sentence is what makes the honest report necessary: the code must *measure*
which free endpoint answers instead of promising they all do. Measured on this host, one
of the two answers and one times out — and the report says exactly that.
"""

from __future__ import annotations

import json

import pytest

from skaldir.tools_core import web


# --------------------------------------------------------------------------- #
# WEB-01 — a free path exists, without a mandatory paid key
# --------------------------------------------------------------------------- #

def test_every_declared_search_backend_needs_no_api_key():
    """A paid key must not be mandatory on any declared path (WEB-01).

    Each backend declares this explicitly — `key_required: False` — and the URL is checked
    too, so a key could not be smuggled in behind a "no" that nobody verified.
    """
    backends = web.BACKENDS
    assert backends, "the module must declare its search backends"
    for name, cfg in backends.items():
        assert cfg.get("key_required") is False, (
            f"{name} does not declare itself key-free — WEB-01 requires at least one"
        )
        assert cfg.get("note"), f"{name} must say where its data comes from"
        url = str(cfg.get("url", ""))
        assert "api_key" not in url and "key=" not in url, (
            f"{name} embeds a key in its URL: {url}"
        )


def test_at_least_one_declared_backend_is_key_free():
    """WEB-01 asks for *at least one* usable path without a paid key."""
    key_free = [n for n, c in web.BACKENDS.items() if c.get("key_required") is False]
    assert key_free, "there must be a search path that needs no paid key (WEB-01)"


def test_the_probe_states_that_no_key_is_required():
    report = web.probe_backends(timeout_s=5, query="test")
    assert report["key_required"] is False, (
        "the probe must say plainly that no paid key is needed (WEB-01)"
    )


# --------------------------------------------------------------------------- #
# "nicht pauschal behaupten, kostenlose Endpunkte seien immer verfügbar"
# --------------------------------------------------------------------------- #

def test_the_probe_measures_each_backend_instead_of_promising_them():
    """Availability is a measurement. An endpoint that does not answer is a gap."""
    report = web.probe_backends(timeout_s=10, query="python")
    assert "backends" in report
    for entry in report["backends"]:
        assert "answered" in entry, "each backend must report whether it actually answered"
        assert "key_required" in entry
        # a backend that did not answer must carry a reason, not just a silent false
        if not entry["answered"]:
            assert entry.get("error"), f"{entry['backend']} did not answer and gave no reason"


def test_an_unreachable_free_endpoint_is_named_as_a_gap():
    """The report's `missing` field is the honest gap list."""
    report = web.probe_backends(timeout_s=10, query="python")
    answered = set(report.get("answering", []))
    missing = report.get("missing", [])
    declared = {e["backend"] for e in report["backends"]}
    assert answered | {m.split(" ")[0] for m in missing} == declared, (
        f"every declared backend is either answering or a named gap — got {answered}, {missing}"
    )


def test_at_least_one_free_path_actually_works_or_the_situation_is_reported():
    """WEB-01 asks for a usable free path. Either it works, or the failure is explicit."""
    report = web.probe_backends(timeout_s=15, query="python")
    if report.get("available"):
        assert report.get("answering"), "`available` with no answering backend is a lie"
    else:
        assert report.get("missing"), "an unavailable report must name the gaps"
    assert "available" in report


# --------------------------------------------------------------------------- #
# WEB-02 — result shape: URL, title, snippet, source
# --------------------------------------------------------------------------- #

def test_a_search_result_carries_url_title_snippet_and_source():
    """WEB-02: "Resultate mit URL/Titel/Snippet/Quelle"."""
    parser = web._PARSERS.get("duckduckgo")
    assert parser is not None, "a parser per backend is what makes the shape checkable"
    payload = json.dumps({
        "AbstractText": "Python is a language",
        "AbstractURL": "https://example.org/python",
        "RelatedTopics": [{"Text": "Python (programming) — a language",
                           "FirstURL": "https://example.org/related"}],
    }).encode()
    results = parser(payload, 10)
    assert results, "the parser produced nothing from a valid payload"
    for item in results:
        assert {"url", "title", "snippet"} <= set(item), (
            f"a result is missing required fields: {sorted(item)}"
        )
        assert item.get("source"), "every result must name its source (WEB-02)"


def test_rate_limit_and_network_errors_are_reported_as_themselves():
    """WEB-02: "Rate-Limit-/Netzwerkfehler" must be distinguishable from "no results"."""
    report = web.probe_backends(timeout_s=3, query="python")
    for entry in report["backends"]:
        if not entry["answered"] and entry.get("error"):
            error = entry["error"]
            assert any(word in error for word in
                       ("Timeout", "timed out", "HTTP", "Error", "refused", "status")), (
                f"an error was reported in an unrecognisable form: {error!r}"
            )


# --------------------------------------------------------------------------- #
# WEB-04 — snippets are not articles, and gaps are named
# --------------------------------------------------------------------------- #

def test_a_snippet_is_never_presented_as_a_read_article():
    """WEB-04: "Suchsnippet ist kein vollständig gelesener Artikel"."""
    parser = web._PARSERS["duckduckgo"]
    payload = json.dumps({
        "AbstractText": "Short abstract",
        "AbstractURL": "https://example.org/a",
    }).encode()
    results = parser(payload, 10)
    for item in results:
        assert item.get("kind") != "article", (
            "a search snippet must not be typed as a full article"
        )
        assert "snippet" in item, "the summary is a snippet and is labelled as one"


def test_the_module_documents_where_its_data_comes_from():
    """The docstring is where a human checks which endpoints are used and at what cost."""
    doc = (web.__doc__ or "")
    assert doc, "a module that fetches from the internet must document its sources"
    assert "duckduckgo" in doc.lower() or "marginalia" in doc.lower()
