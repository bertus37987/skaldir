#!/usr/bin/env python3
"""Host-Overlay RF-05: fallback fitness gate — context / vision / tools fit.

Befund (Kapitel 09 §3C): ``try_activate_fallback`` tauscht model und provider IN
PLACE aus, ohne das Kontextfenster oder die Fähigkeiten des Kandidaten gegen die
GEBaute Anfrage zu prüfen: ein kleineres Fallback-Modell bekommt stillschweigend
den 152k-Request (im Log: „Could not determine context length … falling back to
256,000 tokens“), gegen ein nutzerkonfiguriertes ``context_length`` wird nicht
wertschätzend abgelehnt. Passt nichts, wird trotzdem gefallbackt.

Fix (kleinste wirksame Änderung, Verhalten bei PASS endet identisch):

* Neue Helper-Familie ``_rf05_*`` (direkt vor ``try_activate_fallback``):
    - ``_rf05_request_estimate(agent)``: Kontext-Schätzung der Güte der
      NÄCHSTEN Anfrage (Pinnea ``agent._rf05_pending_request_tokens`` →
      compressor.last_real_prompt_tokens → last_prompt_tokens → 0 unbekannt) + dem
      konfigurierten Ausgabe-Limit (Output-Reserve).
    - ``_rf05_fallback_fitness_ok(agent, fb) -> tuple[bool, list[str]]``:
      - Kontext: Schätzung > gelöste Kontextlänge des Kandidaten
        (``get_model_context_length`` — der EINSTELLFÄHIGE Lese-Zugriff, den die
        Engine bereits benutzt, reale Konfiguration-Overrides segnen);
      - Vision: die Anfrage bringt Bilder mit (``agent._rf05_request_needs``)
        UND Model-Capabilities (models.dev, cache-only, ``supports_vision is False``);
      - Tools: gleiche Heuristik für ``supports_tools``. Unbekannter Catalog
        (``None``) ist KEIN Ablehnungs-Beweis — wir blockieren nicht mitten im
        Urlaub einer Offline-Datenquelle.
* Der Gate läuft pro Kettenkandidaten VOR `resolve_provider_client`:
    passt er → gewünschtes Verhalten unangetastet;
    passt er nicht → ``unavailable.add(fb_key)`` (Bestehender Mechanismus) +
    Log-Zeile („provider_unavailable“ in RFC-Begriffen) + nächster Kandidat.
  Erschöpft die Kette bei einemfitness-Bedenkau zu einem PAUSE-SIGNAL:
    ``agent._rf05_no_compatible_fallback = {reason, at, detail}`` +
    log(m: provider_unavailable: no compatible fallback) + return False
    (kein automatischer Versand des oversized Requests).

Verankert als versioniertes Overlay: ``python
host-overlay/patch_rf05_fallback_fitness.py [--check] [HERMES_SRC]``.
Idempotent; bricht laut ab, wenn ein Anker fehlt (kein stiller No-op).
Reihenfolge-unabhängig zu RF-02/RF-03/RF-04 (dieselben Dateien, keine geteilten
Anker-Zeilen).

Nachweis: ``tools/host_rf05_fallback_probe.py`` und
``tests/test_rf05_fallback_fitness.py``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

DEFAULT_SRC = Path.home() / ".hermes" / "hermes-agent"


def _refuse_live_engine(target: Path, *, check: bool) -> None:
    """Never write into the RUNNING Hermes install by default.

    ``DEFAULT_SRC`` is ``~/.hermes/hermes-agent`` -- the live tree. A bare
    invocation used to mean "patch the engine that is running right now": that
    silently changed the host and made every later "pristine vs patched"
    measurement meaningless (one overlay was found applied in the live tree).
    Writing there now requires GV_ALLOW_LIVE_ENGINE=1; ``--check`` stays allowed
    because it changes nothing.
    """
    if check:
        return
    try:
        is_live = target.resolve() == DEFAULT_SRC.resolve()
    except OSError:
        is_live = False
    if is_live and os.environ.get("GV_ALLOW_LIVE_ENGINE", "") != "1":
        print(
            f"refusing to patch the live engine tree ({target}).\n"
            "Pass an explicit target copy, e.g.:\n"
            "    cp -a ~/.hermes/hermes-agent /tmp/gv-try && "
            "python3 " + Path(__file__).name + " /tmp/gv-try\n"
            "Set GV_ALLOW_LIVE_ENGINE=1 only if you really mean the running install.",
            file=sys.stderr,
        )
        raise SystemExit(2)

MARKER = "gv-host-overlay RF-05"

HELPERS = Path("agent") / "chat_completion_helpers.py"

#: Fitness helpers insert directly BEFORE ``try_activate_fallback`` (anchored on
#: ``_buffer_fallback_notice``, whose last line is directly above it).
FITNESS_HELPERS_ANCHOR = (
    "        agent._pending_fallback_notice = [str(pending), notice] if pending else [notice]\n"
    "\n"
    "\n"
    "def try_activate_fallback(agent, reason: \"FailoverReason | None\" = None) -> bool:\n"
)
FITNESS_HELPERS_NEW = '''        agent._pending_fallback_notice = [str(pending), notice] if pending else [notice]


# gv-host-overlay RF-05 — fallback fitness gate (context fit, vision, tools).
#
# RFC §3C: "Ein kleineres Modell bekommt keinen unpassenden 152k-Request." The
# heuristics only REFUSE on EVIDENCE: a pinned/compressor prompt estimate larger
# than the candidate's context window, or an explicit catalog capability
# conflict. Unknown data deliberately keeps the shipped fallback behaviour.

RF05_CONTEXT_HEADROOM = 256  # protocol overhead (messages/tools format) per request


def _rf05_request_estimate_tokens(agent) -> int:
    """Pilot estimate of the pending request size (prompt + output reserve).

    Order: a caller-pinned reading (``agent._rf05_pending_request_tokens``) → the
    compressor's last REAL prompt tokens → its last prompt tokens → 0 (unknown).
    Unknown is NOT proof of a mismatch, so 0 never refuses.
    """
    estimate = 0
    compressor = getattr(agent, "context_compressor", None)
    if compressor is not None:
        real = getattr(compressor, "last_real_prompt_tokens", 0) or 0
        last = getattr(compressor, "last_prompt_tokens", 0) or 0
        estimate = int(real if real > 0 else max(last, 0))
    pinned = getattr(agent, "_rf05_pending_request_tokens", None)
    if isinstance(pinned, (int, float)) and pinned > 0:
        estimate = int(max(estimate, int(pinned)))
    reserve = getattr(agent, "max_tokens", None)
    if isinstance(reserve, (int, float)) and reserve > 0:
        estimate += int(reserve)
    return estimate


def _rf05_request_needs(agent) -> dict:
    """What the pending request REQUIRES of the model (pinned by preflight probes,
    or defaulted empty = no evidence of need). Keys: vision, tools."""
    needs = getattr(agent, "_rf05_request_needs", None)
    return needs if isinstance(needs, dict) else {}


def _rf05_fallback_fitness(agent, fb: dict) -> tuple:
    """Return ``(fits, reasons)`` for one candidate fallback entry.

    Refusal requires EVIDENCE from one of:
      - context: estimate > candidate context window (resolved through the
        engine's own ``get_model_context_length`` so config overrides win);
      - vision: the request needs images AND the cached capabilities say the
        candidate has none (models.dev cache, no network on this path);
      - tools: the request needs tool schemas AND the capabilities say the
        candidate cannot call tools.
    """
    reasons: list[str] = []
    provider = (fb.get("provider") or "").strip() or getattr(agent, "provider", "")
    model = (fb.get("model") or "").strip() or ""
    base_url = (fb.get("base_url") or "").strip()
    needs = _rf05_request_needs(agent)
    try:
        from agent.models_dev import get_model_capabilities
        caps = get_model_capabilities(provider, model, allow_network=False)
    except Exception:
        caps = None
    if caps is not None:
        if needs.get("vision") and caps.supports_vision is False:
            reasons.append(f"vision unsupported: {model}")
        if needs.get("tools") and caps.supports_tools is False:
            reasons.append(f"tool calls unsupported: {model}")
    # Context fit — use the configurable engine reader so config-level context_length
    # overrides directly participate (also the ordering the engine itself trusts).
    try:
        from agent.model_metadata import get_model_context_length
        context_length = get_model_context_length(
            model, base_url=base_url_safe(fb), api_key="",
            provider=provider, config_context_length=fb.get("context_length"),
            custom_providers=getattr(agent, "_custom_providers", None),
        )
    except Exception:
        context_length = 0
    estimate = _rf05_request_estimate_tokens(agent)
    if context_length > 0 and estimate > 0 and estimate > context_length:
        reasons.append(
            f"request estimate ~{estimate:,} tokens exceeds candidate context "
            f"{context_length:,} ({model})")
    return (not reasons, reasons)


def base_url_safe(entry: dict) -> str:
    return (entry.get("base_url") or "").strip()


def _rf05_mark_pause(agent, reasons: list[str]) -> None:
    """Record the structured pause for the turn layer (§3C: provider_unavailable)."""
    agent._rf05_no_compatible_fallback = {
        "reason": "provider_unavailable: no compatible fallback",
        "at": time.monotonic(),
        "detail": "; ".join(reasons),
    }
    logger.warning(
        "provider_unavailable: no compatible fallback — %s", "; ".join(reasons))


def try_activate_fallback(agent, reason: "FailoverReason | None" = None) -> bool:
'''
FITNESS_IN_LOOP_ANCHOR = '''        try:
            from agent.auxiliary_client import resolve_provider_client
'''
FITNESS_IN_LOOP_NEW = '''        # gv-host-overlay RF-05: gate the candidate on the built request BEFORE a
        # client is constructed. A refusal marks the candidate unavailable (the same
        # mechanism the engine already uses for provider-not-configured) and walks on;
        # an exhausted chain records the pause flag so the turn layer can surface it.
        _fits, _fitness_reasons = _rf05_fallback_fitness(agent, fb)
        if not _fits:
            unavailable.add(fb_key)
            continue
        try:
            from agent.auxiliary_client import resolve_provider_client
'''
FITNESS_EXHAUSTED_ANCHOR = '''    while True:
        if agent._fallback_index >= len(agent._fallback_chain):
            return _fallback_chain_exhausted(agent, reason)
'''
FITNESS_EXHAUSTED_NEW = '''    while True:
        if agent._fallback_index >= len(agent._fallback_chain):
            # gv-host-overlay RF-05: chain walked with fitness refusals — surface the
            # structured pause, not a silently oversized request.
            pause_detail = _rf05_no_compatible_fallback_pending(agent)
            if pause_detail:
                _rf05_mark_pause(agent, pause_detail)
                return False
            return _fallback_chain_exhausted(agent, reason)
'''
#: The pendings are installed into the chain-walk (per candidate) too: remember the
#: refusal reasons when no candidate survived:
FITNESS_RUN_REASONS_ANCHOR = '''    from agent.fallback_cooldown import _arm_rate_limit_cooldown
    cooldown_seconds = _arm_rate_limit_cooldown(agent, reason)
'''
FITNESS_RUN_REASONS_NEW = '''    # gv-host-overlay RF-05: accumulate PER-CANDIDATE fitness refusals; read by the
    # chain-exhausted branch above when nothing fit.
    agent._rf05_fitness_reasons = []
    from agent.fallback_cooldown import _arm_rate_limit_cooldown
    cooldown_seconds = _arm_rate_limit_cooldown(agent, reason)
'''
#: patch the skip to also remember the reason (under unavailable.add) — fitness refusal point:
FITNESS_REFUSAL_RECORD_ANCHOR = '''        _fits, _fitness_reasons = _rf05_fallback_fitness(agent, fb)
        if not _fits:
            unavailable.add(fb_key)
            continue
'''
FITNESS_REFUSAL_RECORD_NEW = '''        _fits, _fitness_reasons = _rf05_fallback_fitness(agent, fb)
        if not _fits:
            agent._rf05_fitness_reasons.extend(_fitness_reasons)
            unavailable.add(fb_key)
            continue
'''
#: the pendings reader the exhausted branch consults — standalone accessor next to
#: the other helpers, so the loop anchor stays tiny:
FITNESS_PENDING_ANCHOR = "def base_url_safe(entry: dict) -> str:\n"
FITNESS_PENDING_NEW = '''def _rf05_no_compatible_fallback_pending(agent) -> list:
    """Every recorded fitness refusal of the just-walked chain (empty → no pause)."""
    return list(getattr(agent, "_rf05_fitness_reasons", []) or [])


def base_url_safe(entry: dict) -> str:
'''
HELPERS_EDITS = [
    (FITNESS_HELPERS_ANCHOR, FITNESS_HELPERS_NEW, 1),
    (FITNESS_PENDING_ANCHOR, FITNESS_PENDING_NEW, 1),
    (FITNESS_RUN_REASONS_ANCHOR, FITNESS_RUN_REASONS_NEW, 1),
    (FITNESS_EXHAUSTED_ANCHOR, FITNESS_EXHAUSTED_NEW, 1),
    (FITNESS_IN_LOOP_ANCHOR, FITNESS_IN_LOOP_NEW, 1),
    (FITNESS_REFUSAL_RECORD_ANCHOR, FITNESS_REFUSAL_RECORD_NEW, 1),
]


def apply_file(path: Path, edits: list[tuple[str, str, int]], *, check: bool) -> int:
    if not path.is_file():
        print(f"✗ not found: {path}")
        return 2
    original = path.read_text(encoding="utf-8")
    if MARKER in original:
        print(f"✓ already patched: {path}")
        return 0
    text = original
    for old, new, expected in edits:
        found = text.count(old)
        if found != expected:
            print(
                f"✗ anchor missing/ambiguous in {path}: {found}× {old.strip()[:60]!r} "
                f"(expected {expected}) — Hermes version changed? Nothing written."
            )
            return 1
        text = text.replace(old, new, expected)
    if check:
        print(f"• would patch: {path}")
        return 0
    backup = path.with_suffix(".py.gv-backup")
    backup.write_text(original, encoding="utf-8")
    path.write_text(text, encoding="utf-8")
    print(f"✓ patched: {path} (backup: {backup.name})")
    return 0


def patch(target: Path, *, check: bool = False) -> int:
    return apply_file(target / HELPERS, HELPERS_EDITS, check=check)


def main() -> int:
    args = [a for a in sys.argv[1:]]
    check = "--check" in args
    args = [a for a in args if not a.startswith("--")]
    target = Path(args[0]) if args else DEFAULT_SRC
    _refuse_live_engine(target, check=check)
    return patch(target, check=check)


if __name__ == "__main__":
    raise SystemExit(main())
