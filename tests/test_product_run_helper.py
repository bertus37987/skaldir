"""gv_serve() must verify the backend endpoint's identity instead of trusting
scan order (audit: the Electron debug port 9333 inherited the same env marker
and was picked over the real API port)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from gv_product_run import _pick_verified_serve  # noqa: E402


def test_debug_port_is_not_picked_when_api_answers_elsewhere():
    def probe(port, token):
        if port == 9333:
            return None  # DevTools port: no godsv payload
        return {"state": "WORKING", "revision": 1}

    port, token = _pick_verified_serve([(9333, "t-debug"), (44039, "t-api")], probe)
    assert (port, token) == (44039, "t-api")


def test_first_verified_candidate_wins_in_order():
    seen = []

    def probe(port, token):
        seen.append(port)
        return {"revision": 2} if port == 44039 else None

    assert _pick_verified_serve([(9333, "a"), (44039, "b")], probe) == (44039, "b")
    assert seen == [9333, 44039]


def test_falls_back_to_first_candidate_when_nothing_verifies():
    assert _pick_verified_serve([(9333, "t")], lambda p, t: None) == (9333, "t")
    assert _pick_verified_serve([], lambda p, t: None) == (0, "")


def test_http_error_payloads_do_not_verify_but_keep_the_old_fallback():
    # a single, unverifiable candidate is still returned so the next API call
    # fails loudly instead of pretending the serve is not running
    port, _ = _pick_verified_serve([(44039, "t")], lambda p, t: {"http_error": 404})
    assert port == 44039
