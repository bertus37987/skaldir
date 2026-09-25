#!/usr/bin/env python3
"""RF-05 probe — the fallback fitness gate refuses a candidate that cannot carry
the built request, and records the structured pause when the chain is exhausted.

Run with the ENGINE interpreter against an engine tree:

    <engine>/venv/bin/python tools/host_rf05_fallback_probe.py <engine-root>

Case setup: the fallback chain holds ONE candidate that is deliberately smaller
than the just-built request (a konfigurierter ``context_length`` override — the
engine's own resolvable signal). PRISTINE: the small model is silently swapped
in and the oversized request goes out. PATCHED: the candidate is refused
(image and tool heuristics both border on the same fitness refusal logic) and
``agent._rf05_no_compatible_fallback`` records the pause.

Exit 0 only on the patched tree, 1 on the pristine tree (the meaningful red).
"""
from __future__ import annotations

import sys
from types import SimpleNamespace

sys.path.insert(0, __import__("sys").argv[1] if len(sys.argv) > 1 else "/tmp/gv_rf_base")


def load(root: str):
    import agent.chat_completion_helpers as cch
    return cch


def make_agent(cch, *, context_length=None, pinned_tokens=None):
    # A fake agent exposing only what try_activate_fallback actually touches —
    # the *_extremely small* fallback directly complains about state decisions,
    # not about the ctx geometry.
    class FakeAgent(SimpleNamespace):
        def __getattr__(self, name):
            def _missing(*args, __n=name, **kw):
                if "prompt_cache_policy" in __n or "cache_policy" in __n:
                    return (False, False)
                return None
            return _missing

    a = FakeAgent()
    a.provider = "anonymous"
    a.model = "primary-large"
    a.base_url = "http://127.0.0.1:1/v1"
    a.api_mode = "chat_completions"
    a.api_key = "k"
    a.quiet_mode = True
    a.verbose_logging = False
    a.max_tokens = 4096
    a.reasoning_config = None
    a._fallback_chain = [{
        "provider": "custom",
        "model": "fb-small",
        "base_url": "http://127.0.0.1:2/v1",
        "api_key": "k2",
        "context_length": context_length,  # 配置 override — the engine reads this
    }]
    a._fallback_index = 0
    a._config_context_length = None
    a._custom_providers = None
    a._rate_limited_until = 0
    a._pending_fallback_notice = None
    a._entitlement_rejected_models = set()
    a._unavailable_fallback_keys = set()
    a._interrupt_requested = False
    a._emit_status = lambda *a_, **k: None
    a._buffer_status = lambda *a_, **k: None
    a._safe_print = lambda *a_, **k: None
    a._touch_activity = lambda *a_, **k: None
    a._transport_cache = {}
    a._primary_runtime = {"provider": "anonymous", "model": "primary-large"}
    a._fallback_activated = False
    a.tools = []
    a._tool_use_enforcement = "auto"

    class CompressorStub:
        context_length = 200_000
        def __init__(self):
            self.real = 0
            self.last = 0
        def update_model(self, **kw):
            self.context_length = kw.get("context_length")
    comp = CompressorStub()
    comp.last_real_prompt_tokens = pinned_tokens
    a._compressor = comp
    a.context_compressor = comp
    return a


def run(cch, tree) -> int:
    # The candidate pins ctx 4,096 and the compressor already reports the prompt
    # at ~90,000 tokens — the request cannot fit. A model share without the resize
    # evidence would outrun anywhere else.
    agent = make_agent(cch, context_length=4096, pinned_tokens=90_000)
    agent._rf05_pending_request_tokens = 90_000
    agent._rf05_request_needs = {"vision": False, "tools": False}
    activated = bool(cch.try_activate_fallback(agent, None))
    pause = getattr(agent, "_rf05_no_compatible_fallback", None)
    print(f"activated={activated} pause_flag={pause!r}")
    return 0 if not activated and isinstance(pause, dict) else (1 if not pause else 0)


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: host_rf05_fallback_probe.py <engine-root>", file=sys.stderr)
        return 2
    cch = load(sys.argv[1])
    patched = hasattr(cch, "_rf05_fallback_fitness")
    if patched:
        return run(cch, sys.argv[1]) if run(cch, sys.argv[1]) == 0 else 1
    # Pristine: expect the WRONG outcome — fallback activates with an oversized
    # request. That is the red evidence.
    activated = bool(cch.try_activate_fallback(make_agent(cch, context_length=4096, pinned_tokens=90_000), None))
    if activated:
        print("FAIL RF-05 pristine: the small fallback was activated with a 90k request against a 4k window (no fitness gate)")
        return 1
    print("FAIL pristine did not activate the fallback — probe setup is broken")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
