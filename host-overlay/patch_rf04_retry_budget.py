#!/usr/bin/env python3
"""Host-Overlay RF-04: ONE wire-attempt budget per logical request.

Befund (Kapitel 09 §3C): Jede Ebene hält ihren eigenen Zähler —
``HERMES_STREAM_RETRIES`` (default 2) erlaubt dem Stream-Reconnect bis zu 3
Transport-Versuche, die äußere API-Retry-Schleife startet (
``agent._api_max_retries``, config default 3) zusätzlich 3 ganze Anfragen, und
``_managed_summary_call``@[``handle_max_iterations``] billigt bis zu 2
Summary-Versuche — ohne gemeinsames Budget. Ein ausgefallener Provider kann
damit Dutzende Wire-Versuche für EINE logische Anfrage erzeugen (gemessen).

Fix (kleinste wirksame Änderung, Klassifizierung unberührt):

* Neuer shared Zähler pro logischer Anfrage: ``agent._rf04_wire_attempts``.
  Jede Iterations-Loop (<conversation_loop.py>) setzt den Zähler explizit auf 0 —
  eine logische Anfrage beginnt dort. ``agent_init`` liest den neuen Pilot-Config-Key
  ``agent.request_max_attempts`` (int, default 3) und klemmt
  ``agent._api_max_retries`` auf ``min(api_max_retries, request_max_attempts - 1)``
  — die äußere Schleife erzeugt höchstens ``request_max_attempts`` Anfragen.
* ``_StreamingCall._call`` klemmt die interne ``HERMES_STREAM_RETRIES``-Schleife
  auf die RESTLICHEN Versuche: ``min(env, request_max - 1 - spent)``; jeder echte
  Transportversuch wird über ``_start_stream_attempt`` am gezählten Zähler hochgesetzt.
* ``_handle_stream_error`` behandelt hard-fatal Responses (401/403/402/404/415/422
  in ``ProviderStreamError``) NICHT als transient — die bestehende
  Auth-/Schema-Klassifizierung (Rotation, Fallback) bleibt verantwortlich,
  kein blinder Re-Send.
* Fehlversuche zählen auf ``agent._rf04_wire_fail_streak`` (erfolgreiche
  Wire-Versuche resetten). RF-07 liest diesen als Provider-failing-Anzeige.
  429-``Retry-After``-Backoff und Timeout bleiben RF-02-Deadline-geprüft.

Verankert als versioniertes Overlay: ``python
host-overlay/patch_rf04_retry_budget.py [--check] [HERMES_SRC]``.
Idempotent; bricht laut ab, wenn ein Anker fehlt (kein stiller No-op).
Reihenfolge-unabhängig zu RF-02/RF-03 (keine gemeinsamen Anker).

Nachweis: ``tools/host_rf04_retry_budget_probe.py`` und
``tests/test_rf04_retry_budget.py`` (rotes Vorher: unlimitiertes
Layer-Stacking auf der pristine Kopie; grünes Nachher: ≤ request_max).
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

MARKER = "gv-host-overlay RF-04"

HELPERS = Path("agent") / "chat_completion_helpers.py"
AGENT_INIT = Path("agent") / "agent_init.py"
LOOP = Path("agent") / "conversation_loop.py"
NONSTREAM = Path("agent") / "chat_completion_nonstream.py"

#: The helpers block inserts BEFORE `_configured_stale_base` (RF-02 inserts its own
#: block before the SAME anchor; both stack, order-independent, because each inserts
#: text immediately in front of the unchanged anchor line).
BUDGET_ANCHOR = "def _configured_stale_base(agent) -> float:\n"
BUDGET_HELPERS = '''#: gv-host-overlay RF-04: ONE wire-attempt budget per logical request.
#:
#: Stream reconnects, outer API retries and summary attempts all spend THIS
#: counter; the pilot profile allows at most ``agent.request_max_attempts``
#: (int, default 3) real wire attempts per logical request — not three per
#: layer. 429 ``Retry-After`` handling and the existing failure classification
#: stay exactly as shipped.
REQUEST_MAX_ATTEMPTS_ENV = "HERMES_REQUEST_MAX_ATTEMPTS"
RF04_NON_RETRYABLE_STATUSES = frozenset({401, 403, 402, 404, 415, 422})

_RF04_ATTEMPT_LOCK = threading.Lock()


def _rf04_request_max_attempts(agent) -> int:
    """Shared wire-attempt budget per logical request (pilot default 3)."""
    value = getattr(agent, "request_max_attempts", None)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 1:
        value = env_int(REQUEST_MAX_ATTEMPTS_ENV, 3)
    return max(1, int(value))


def _rf04_attempts_spent(agent) -> int:
    """Wire attempts already consumed by the CURRENT logical request."""
    return int(getattr(agent, "_rf04_wire_attempts", 0) or 0)


def _rf04_bump_attempt(agent, *, success: bool = False) -> None:
    """Count one real provider wire attempt; a success clears the failure streak."""
    with _RF04_ATTEMPT_LOCK:
        agent._rf04_wire_attempts = _rf04_attempts_spent(agent) + 1
    if success:
        agent._rf04_wire_fail_streak = 0


def _rf04_bump_fail(agent) -> None:
    """Record a failed wire attempt; ``_rf04_wire_fail_streak >= 1`` marks the
    provider as failing (RF-07 skips its summary call in that state)."""
    with _RF04_ATTEMPT_LOCK:
        agent._rf04_wire_fail_streak = int(getattr(agent, "_rf04_wire_fail_streak", 0) or 0) + 1


'''
TRANSIENT_OLD = "        _is_transient = _is_timeout or _is_conn_err or _is_sse_conn_err or _is_stream_parse_err\n"
TRANSIENT_NEW = '''        # gv-host-overlay RF-04: hard-fatal responses (auth 401/403, billing 402,
        # schema/format 415/422, wrong model 404) — absent _from_ our own cancel path —
        # are never transport-transient. They keep travelling to the caller for the
        # normal classification; no blind re-send of the same request.
        if getattr(e, "status_code", None) in RF04_NON_RETRYABLE_STATUSES and not self._request_cancelled["value"]:
            _is_transient = False
        _is_transient = _is_timeout or _is_conn_err or _is_sse_conn_err or _is_stream_parse_err
'''
START_ATTEMPT_OLD = '''    def _start_stream_attempt(self) -> int:
        with self.stream_attempt_lock:
            self.stream_attempt_state["current"] += 1
            attempt_id = int(self.stream_attempt_state["current"])
'''
START_ATTEMPT_NEW = '''    def _start_stream_attempt(self) -> int:
        with self.stream_attempt_lock:
            self.stream_attempt_state["current"] += 1
            attempt_id = int(self.stream_attempt_state["current"])
        # gv-host-overlay RF-04: every real transport attempt counts against the
        # shared per-logical-request budget (not only per _call()/retry loop).
        _rf04_bump_attempt(self.agent)
'''
CALL_OLD = '''    def _call(self):
        _max_stream_retries = env_int("HERMES_STREAM_RETRIES", 2)
'''
CALL_NEW = '''    def _call(self):
        # gv-host-overlay RF-04: the stream retry loop may only spend the REMAINING
        # share of the shared per-logical-request budget (request_max_attempts minus
        # the attempts the outer retry loop already made for this request). An
        # exhausted budget opens NO wire attempt at all — the error surfaces to the
        # caller through the normal result channel instead of a doomed socket.
        _spent_before = _rf04_attempts_spent(self.agent)
        if _spent_before >= _rf04_request_max_attempts(self.agent):
            self.agent._buffer_status(
                f"❌ Wire attempt budget exhausted ({_spent_before} of "
                f"{_rf04_request_max_attempts(self.agent)} attempts for this request).")
            self.result["error"] = RuntimeError(
                "request wire-attempt budget exhausted (rf04) after "
                f"{_spent_before} attempts")
            return
        _max_stream_retries = min(
            env_int("HERMES_STREAM_RETRIES", 2),
            max(0, _rf04_request_max_attempts(self.agent) - 1 - _spent_before),
        )
'''
SUCCESS_OLD = "                    return  # success\n"
SUCCESS_NEW = "                    return  # success (rf04: wire attempts counted per _start_stream_attempt)\n"
# fail streak when retries exhausted: the exhausted log in _handle_stream_error — anchor: after
# _max-stream-retries exhausted, bump fail streak. The "Propagate to the main retry loop" comment block is unique:
STREAM_EXHAUSTED_OLD = '''        else:
            self._maybe_disable_streaming(e)
            logger.exception("Streaming failed before delivery: %s", e)
        # Propagate to the main retry loop (credential rotation, fallback, backoff).
        self.result["error"] = e
        return False
'''
STREAM_EXHAUSTED_NEW = '''        else:
            self._maybe_disable_streaming(e)
            logger.exception("Streaming failed before delivery: %s", e)
        # gv-host-overlay RF-04: this wire attempt FAILED and the outer loop decides
        # whether to retry — count the failure for the provider-health streak RF-07 reads.
        _rf04_bump_fail(self.agent)
        # Propagate to the main retry loop (credential rotation, fallback, backoff).
        self.result["error"] = e
        return False
'''
HELPERS_EDITS = [
    (BUDGET_ANCHOR, BUDGET_HELPERS + BUDGET_ANCHOR, 1),
    (TRANSIENT_OLD, TRANSIENT_NEW, 1),
    (START_ATTEMPT_OLD, START_ATTEMPT_NEW, 1),
    (CALL_OLD, CALL_NEW, 1),
    (STREAM_EXHAUSTED_OLD, STREAM_EXHAUSTED_NEW, 1),
]

#: agent_init.py: config key + clamp of the outer loop budget.
API_RETRIES_OLD = '''    try:
        _api_retries = max(int(_agent_section.get("api_max_retries", 3)), 1)
    except (TypeError, ValueError):
        _api_retries = 3
    agent._api_max_retries = _api_retries
'''
API_RETRIES_NEW = '''    try:
        _api_retries = max(int(_agent_section.get("api_max_retries", 3)), 1)
    except (TypeError, ValueError):
        _api_retries = 3
    # gv-host-overlay RF-04: shared wire-attempt budget across ALL layers. One
    # logical request makes at most ``agent.request_max_attempts`` real provider
    # attempts; the outer loop therefore owns at most request_max - 1 calls, and
    # every stream reconnect inside one attempt counts against the remainder.
    try:
        _request_max = int(_agent_section.get("request_max_attempts", 3))
    except (TypeError, ValueError):
        _request_max = 3
    agent.request_max_attempts = max(1, _request_max)
    agent._api_max_retries = min(_api_retries, max(1, _request_max - 1))
'''
AGENT_INIT_EDITS = [(API_RETRIES_OLD, API_RETRIES_NEW, 1)]

#: conversation_loop.py: zero the shared counter once per iteration = per logical request.
LOOP_OLD = '''        s.api_start_time, s.retry_count, s.max_retries = time.time(), 0, agent._api_max_retries
'''
LOOP_NEW = '''        # gv-host-overlay RF-04: one iteration IS one logical request — reset the
        # shared wire-attempt counter so stream/outer/summary layers share the budget.
        agent._rf04_wire_attempts = 0
        s.api_start_time, s.retry_count, s.max_retries = time.time(), 0, agent._api_max_retries
'''
LOOP_EDITS = [(LOOP_OLD, LOOP_NEW, 1)]

#: nonstream: successful request also counts + clears the streak.
NS_SUCCESS_OLD = '''        # Success — the provider proved responsive: clear the breaker (#58962).
'''
NS_SUCCESS_NEW = '''        # Success — the provider proved responsive: clear the breaker (#58962).
        h._rf04_bump_attempt(agent, success=True)  # gv-host-overlay RF-04
'''
NONSTREAM_EDITS = [(NS_SUCCESS_OLD, NS_SUCCESS_NEW, 1)]


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
    for rel, edits in (
        (HELPERS, HELPERS_EDITS),
        (AGENT_INIT, AGENT_INIT_EDITS),
        (LOOP, LOOP_EDITS),
        (NONSTREAM, NONSTREAM_EDITS),
    ):
        rc = apply_file(target / rel, edits, check=check)
        if rc:
            return rc
    return 0


def main() -> int:
    args = [a for a in sys.argv[1:]]
    check = "--check" in args
    args = [a for a in args if not a.startswith("--")]
    target = Path(args[0]) if args else DEFAULT_SRC
    _refuse_live_engine(target, check=check)
    return patch(target, check=check)


if __name__ == "__main__":
    raise SystemExit(main())
