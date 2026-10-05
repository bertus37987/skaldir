"""VAL-WEB-001: the key-free fallback chain must have a backend that ANSWERS.

Measured 2026-10-05 on this host (library/environment.md "Free general-web
SEARCH backends"): every chain entry that existed before this feature —
Marginalia (intermittent), DuckDuckGo IA API and DuckDuckGo Lite — was in a
CAPTCHA/quota window OR is challenge-walled, so the honest-fallback property had
no backend left behind it. Bing answers from this host: organic HTML, no
CAPTCHA. Its parse shape is recorded below (measured, not invented): each hit is
an ``<li class="b_algo" ...>`` preceded by injected ``<link rel=stylesheet>``
elements; the real URL lives in ``<cite>`` (the ``href`` is a ``bing.com/ck/a``
click-wrapper); titles sit in ``<h2>`` with an anchor-text fallback; Bing
breadcrumb paths use ``›`` (U+203A) as separator —
``https://www.w3schools.com›python`` is NOT a fetchable URL and must be
normalized. A challenge/anomaly body is the first-class rate_limited class,
never "answered with no results".
"""

from __future__ import annotations

import re

from test_tools_core_tools import make_transport

from skaldir.tools_core import web

# ------------------------------------------------------------------ #
# The measured single-row shape (chunk #1 of the 2026-10-05 live probe,
# trimmed to only the elements the parser reads).
# ------------------------------------------------------------------ #

_BING_ROW = (
    ' iid=SERP.5335><div class="b_tpcn"><a class="tilk" aria-label="rust-lang.org"'
    ' href="https://www.bing.com/ck/a?w&u=a1aHR0cHM6Ly9ydXN0LWxhbmcub3JnLw">'
    '<div class="tptxt"><div class="tptt">rust-lang.org</div>'
    '<div class="tpmeta"><cite>https://<strong>rust</strong>-lang.org</cite>'
    "</div></div></a></div>"
    '<h2><a href="https://www.bing.com/ck/a?u=a1aHR0cHM6Ly9ydXN0LWxhbmcub3JnLw">'
    "<strong>Rust</strong> Programming Language</a></h2>"
    '<div class="b_caption"><p class="b_lineclamp2">'
    "A language empowering everyone to build reliable and efficient software.</p>"
    "</div></li>"
)

_BING_ROW_BREADCRUMB = (
    ' iid=SERP.6002><div class="tptxt">'
    '<cite>https://www.w3schools.com›<strong>python</strong></cite>'
    "</div>"
    '<h2><a href="https://www.bing.com/ck/a">Python Tutorial</a></h2>'
    '<div class="b_caption"><p>W3Schools</p></div></li>'
)

_BING_ROW_ANCHOR_TITLE = (
    ' iid=SERP.7001><div class="tptxt"><cite>https://example.test/page</cite></div>'
    '<a href="https://example.test/page">The Example Page</a>'
    '<div class="b_caption"><p>motto text</p></div></li>'
)


def _parse_bing(rows: tuple[str, ...] = (_BING_ROW,), limit: int = 20):
    """Call the REAL bing parser (the same dispatcher _try_once uses)."""
    document = (
        "<html><body>"
        + "".join(f'<li class="b_algo"{row}' for row in rows)
        + "</body></html>"
    )
    return web._PARSERS["bing"](document.encode(), limit)


# ------------------------------------------------------------------ #
# Registration facts: Bing is declared, key-free, nameable
# ------------------------------------------------------------------ #
# Registration facts: Bing is declared, key-free, nameable
# ------------------------------------------------------------------ #


def test_bing_is_a_declared_key_free_search_backend():
    cfg = web.BACKENDS["bing"]
    assert cfg["key_required"] is False
    assert "www.bing.com/search" in cfg["url"]
    assert cfg["note"], "every backend must say where its data comes from"


def test_the_search_input_schema_accepts_bing():
    args = {"query": "rust", "backend": "bing"}
    assert web.enforce_input(web.SEARCH_CONTRACT, args) is None


def test_the_documented_fallback_order_names_a_backend_that_actually_answers():
    """The docs must name the same order the code iterates (measured first)."""
    description = web.SEARCH_CONTRACT.description
    match = re.search(r"fixed fallback order \((.+?)\)", description)
    assert match, f"the contract no longer documents the order: {description!r}"
    parts = [part.strip() for part in match.group(1).split("\u2192")]
    # The FIRST named backend must be the measured-working one, not a walled
    # engine: a chain whose first entry is walled is the defect this feature
    # exists to fix.
    first = parts[0].lower()
    for candidate in sorted(web.BACKENDS, key=len, reverse=True):
        if candidate.replace("-", " ") in first:
            named_first = candidate
            break
    else:
        raise AssertionError(f"the first named backend is not a declared one: {parts[0]!r}")
    assert named_first == next(iter(web.BACKENDS)), (
        f"the documented first backend ({named_first!r}) must equal the "
        f"actual first iteration entry ({next(iter(web.BACKENDS))!r})"
    )
    module_doc = (web.__doc__ or "").lower()
    assert "fallback order is fixed" in module_doc, "the module docstring must name the order"


# ------------------------------------------------------------------ #
# Classification: challenge/CAPTCHA/anomaly = first-class rate_limited
# ------------------------------------------------------------------ #


def test_a_walled_bing_is_rate_limited_first_class_and_never_loops():
    """A challenged attempt is a rate_limited result with the same retry budget.

    Bing answers with an anomaly/challenge body; the other backends rate-limit.
    The chain must end in a bounded refusal (exactly MAX_ATTEMPTS_PER_BACKEND
    calls per backend), every bing record must name rate_limited — a challenge
    body must not be renamed as "answered with no results".
    """
    transport = make_transport(
        {
            "https://www.bing.com/": {
                "status": 200,
                "content_type": "text/html",
                "body": b"Please complete the anomaly challenge (captcha).",
            },
            "https://api.marginalia.nu/": {"status": 429, "body": b""},
            "https://api.duckduckgo.com/": {"status": 429, "body": b""},
            "https://lite.duckduckgo.com/lite/": {"status": 429, "body": b""},
        }
    )
    result = web.run_search(
        {"query": "rust"}, web.ToolContext(transport=transport)
    )
    assert result.ok is False and result.error_kind == "blocked"
    assert "rate_limited" in result.data["failure_classes"], (
        f"a challenge page must classify as rate_limited, got "
        f"{result.data['failure_classes']}"
    )
    backends = {record["backend"] for record in result.data["attempts"]}
    assert backends == set(web.BACKENDS), f"the chain must reach every backend: {backends}"
    bing_records = [
        record for record in result.data["attempts"] if record["backend"] == "bing"
    ]
    assert bing_records and all(
        record["failure_class"] == "rate_limited" for record in bing_records
    ), f"the challenge body must be named rate_limited every time: {bing_records}"
    bing_calls = [call for call in transport.calls if "www.bing.com" in call["url"]]
    assert len(bing_calls) == web.MAX_ATTEMPTS_PER_BACKEND, (
        f"bounded budget per backend: {len(bing_calls)} bing calls would be a loop"
    )
    assert result.exit_code == 3  # blocked, not fabricated


# ------------------------------------------------------------------ #
# Bing parsing: measured shapes
# ------------------------------------------------------------------ #


def test_bing_url_comes_from_cite_not_from_the_bing_click_wrapper():
    url = _parse_bing()[0]["url"]
    assert url == "https://rust-lang.org", (
        f"the real URL must come from <cite>, not the bing.com/ck/a wrapper: {url}"
    )


def test_bing_breadcrumb_separator_is_normalized_to_a_fetchable_url():
    url = _parse_bing((_BING_ROW_BREADCRUMB,))[0]["url"]
    assert url == "https://www.w3schools.com/python", (
        f"a breadcrumb cite must normalize to a fetchable URL, got: {url!r}"
    )
    assert web.host_denied(url) == "", "the normalized URL must pass host_denied"


def test_bing_title_falls_back_to_anchor_text_when_h2_is_absent():
    hit = _parse_bing((_BING_ROW_ANCHOR_TITLE,))[0]
    assert hit["title"] == "The Example Page", hit
    assert hit["url"] == "https://example.test/page"
    assert hit["snippet"] == "motto text"
    assert hit["source"] == "bing"


def test_bing_results_carry_url_title_snippet_and_source():
    for item in _parse_bing():
        assert {"url", "title", "snippet"} <= set(item), sorted(item)
        assert item["source"] == "bing"


def test_bing_link_stylesheet_injection_never_breaks_the_cite_search():
    """12 injected <link rel=stylesheet> elements must be stripped (measured: they
    break naive `<cite>` scans)."""
    noisy = f'{"<link rel=stylesheet>" * 12}<li class="b_algo"{_BING_ROW}'
    hit = _parse_bing((noisy,))[0]
    assert hit["url"] == "https://rust-lang.org", hit


def test_a_challenge_body_is_never_answered_by_the_probe():
    """probe_backends on a challenge body: bing does not 'answer' — and the
    probe names the class instead of counting the page as a result."""
    challenge = web.probe_backends(
        transport=make_transport(
            {
                "https://www.bing.com/": {
                    "status": 200,
                    "content_type": "text/html",
                    "body": b"Unusual traffic: anomaly challenge (captcha).",
                }
            }
        ),
        timeout_s=1,
        query="rust",
    )
    bing = next(e for e in challenge["backends"] if e["backend"] == "bing")
    assert bing["answered"] is False
    assert bing["failure_class"] == "rate_limited"
    assert bing["rate_limited"] is True
    assert "captcha" in bing["error"].lower()
