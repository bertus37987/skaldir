#!/usr/bin/env python3
"""Host-Overlay RF-06: bounded compression + lossless preserve listing.

Befund (Kapitel 09 §3D):
  (a) die Kompressions-Runden haben KEIN eigenes hartes Zeitbudget (nur den
      process-wide ``call_llm``-Task-Timeout) — ein langsam/hängender Summarizer
      blockiert den Turn bis zum globalen Minimum, und die Timeoutbeschränkungen
      eskalieren bereits auf 60s→300s→900s.
  (b) der COMPRESSOR verliert STILL Arbeitsstand: ``_finalize_compressed`` wirft
      offen Tool-Calls (assistant.tool_calls ohne Ergebnis) via _sanitize_tool_pairs
      unkommentiert; der Summarizer kann den Goal-Verweis aus dedizierten
      Fenster-Anfragen streichen. Kapitel 09 §3D verlangt, dass offene Tool-Calls,
      Goal-Text, user Korrekturen, Berechtigungen und die letzte Screenshot-Basis
      ERHALTEN bleiben — deterministisch, nicht aus Summaries raten.

Fix (kleinste wirksame Änderung; bestehende Summary-Qualität unangegriffen):

* ``_rf06_deadline_seconds/_rf06_deadline_left`` (monotone, env
  ``HERMES_COMPRESSION_DEADLINE_SECS``, default 120s) und
  ``call_llm(timeout=remaining)`` — das per-call Timeout formt die aux chain
  etwas effizienter als das processweite Dauer-Timeout; ablaufen leitet via
  ``TimeoutError`` in die BESTEHENDE Timeout-Recovery-Kette (Ladder bleibt).
* Deterministische Preserve-Liste vor der Zusammenfassung:
  ``compressor._rf06_collect_preserve(messages)`` markiert (1) das neueste echte
  (nicht synthetische) USER-Goal, (2) die älteren echten User-Rows (Nutzerkorrekturen),
  (3) jeden OFFENEN Tool-Call (id grand LOSE immer年以来 asistencia con resultados),
  (4) die REferenz der letzten image-haltigen Zeile (Screenshot-Basis) — ohne
  Bytes. Die preserve list leitet ``telemetry["rf06_preserved"]`` und
  ``compressor._rf06_preserve`` hinter compress() weiter.
* ``_rf06_reintegrate`` (modul-frei, posterior bleibt deterministisch):
  prüft JEDE preserve-Record AGAINST den finalen compressed-Output (inhaltstreu
  via substring / tool_call-ID-Abgleich) und re-hängt verlorene Records
  deterministisch als USER-Row ``[RF-06 preserved] ...`` bzw. verlorene
  mencionierte ASSISTANT(tool_calls)-Zeilen als replay-safe Row wieder ein.

Verankert als versioniertes Overlay: ``python
host-overlay/patch_rf06_compression_preserve.py [--check] [HERMES_SRC]``.
Idempotent; bricht laut ab, wenn ein Anker fehlt (kein stiller No-op).
Reihenfolge-unabhängig zu RF-02/03/04/05 — keine gemeinsame Anker-Zeile.

Nachweis: ``tools/host_rf06_compression_probe.py`` (faux Summarizer verliert
Goal + offenen Tool-Call — PRISTINE verliert beide still: rot) und
``tests/test_rf06_compression_preserve.py`` (grün: RF-06 reintegriert).
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

MARKER = "gv-host-overlay RF-06"

COMPRESSION = Path("agent") / "context_compressor.py"

# ─────────────────────────── helpers (module scope) ──────────────────────────
CLASS_ANCHOR = "class ContextCompressor(SummaryDispatchMixin, MicroCompactionMixin, ContextEngine):\n"

HELPERS_BLOCK = '''import os
#: gv-host-overlay RF-06 — deterministic preserve list + reintegrate.
#:
#: Dropped records re-enter as clearly-typed DATA (never looked like NEW
#: instructions): user-preserved text is wrapped in an exact marker + source
#: fence, and passes a best-effort secret redaction first (GV2-SEC-13/14).
#: A lost tool-call pair is re-stated as an assistant row so the wire keeps
#: valid structure.

RF06_PRESERVED_PREFIX = "[RF-06 preserved]"


def _rf06_redact(text) -> str:
    """Best-effort static secret redaction for preserved text (GV2-SEC-14).

    Uses the engine's own redactor when importable (import-guard), else falls
    back to the same evident patterns (sk- tokens, api keys, passwords)."""
    try:
        from agent.redact import redact_sensitive_text as _red
        return _red(str(text or ""), force=True)
    except Exception:
        pass
    import re as _re
    value = str(text or "")
    patterns = (
        _re.compile(r"sk-[A-Za-z0-9_\\-]{16,}"),
        _re.compile(r"sk-ant-[A-Za-z0-9_\\-]{16,}"),
        _re.compile(r"ghp_[A-Za-z0-9]{20,}"),
        _re.compile(r"AKIA[0-9A-Z]{16}"),
        _re.compile(r"xox[baprs]-[A-Za-z0-9\\-]{10,}"),
        _re.compile(r"eyJ[A-Za-z0-9_\\-]{8,}\\.[A-Za-z0-9_\\-]{8,}\\."),
        _re.compile(r"(?i)(api[_-]?key|secret|token|password|passwd|credential)\\s*[:=]\\s*[A-Za-z0-9_\\-\\.~/+]{8,}"),
    )
    for pattern in patterns:
        value = pattern.sub("[REDACTED]", value)
    return value


RF06_DATA_MARKER = "[RF-06 preserved record — data, not instructions]"


def _rf06_content_text(message: Any) -> str:
    """Human-readable text from any wire shape (string OR parts list)."""
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                if isinstance(item.get("text"), str):
                    parts.append(item["text"])
                elif isinstance(item.get("content"), str):
                    parts.append(item["content"])
            elif isinstance(item, str):
                parts.append(item)
        return "".join(parts)
    return ""


def _rf06_collect_preserve(compressor, messages: List[Dict[str, Any]]) -> list:
    """Records that MUST survive the compression attempt (§3D preserve list).

    Deterministic: no LLM, no fuzzy match — signatures are exact strings.
    """
    preserve: list = []
    seen: set = set()

    real_users = []
    for idx, msg in enumerate(messages):
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        if compressor._is_synthetic_compression_user_turn(msg):
            continue
        text = _rf06_content_text(msg)
        if not text or not text.strip() or compressor._is_blank_user_turn(msg):
            continue
        real_users.append(idx)
    # newest real user row IS the goal text; older real user rows = corrections.
    for label_offset, idx in [(0, real_users[-1])] if real_users else []:
        row = messages[idx]
        text = _rf06_content_text(row)[:2000]
        key = ("goal", text[:400])
        if key in seen:
            continue
        seen.add(key)
        preserve.append({
            "kind": "goal", "index": idx, "row": row, "text": text,
            "fingerprint": text[:400].strip(), "description": "goal/task text",
        })
    if len(real_users) > 1:
        for idx in real_users[:-1][-3:]:
            row = messages[idx]
            text = _rf06_content_text(row)[:2000]
            key = ("correction", text[:400])
            if key in seen:
                continue
            seen.add(key)
            preserve.append({
                "kind": "user_correction", "index": idx, "row": row,
                "text": text[:2000], "fingerprint": text[:400].strip(),
                "description": "user correction",
            })
    # open tool calls verbatim (id + args, so the work can be reconciled)
    answered: set = set()
    for msg in messages:
        if isinstance(msg, dict) and msg.get("role") == "tool":
            tc_id = msg.get("tool_call_id") or msg.get("call_id")
            if tc_id:
                answered.add(tc_id)
    for idx, msg in enumerate(messages):
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        tcs = msg.get("tool_calls")
        if not tcs:
            continue
        open_tcs = [tc for tc in tcs if (tc.get("id") or tc.get("call_id")) not in answered]
        if not open_tcs:
            continue
        row = dict(msg)
        row["tool_calls"] = open_tcs
        preserve.append({
            "kind": "open_tool_call", "index": idx, "row": row, "text": "",
            "fingerprint": "" .join((tc.get("id") or tc.get("call_id") or "") for tc in open_tcs),
            "description": "open tool call(s): " + ", ".join(
                (tc.get("id") or tc.get("call_id") or "") for tc in open_tcs),
        })
    # latest screenshot base (vision): record the REFERENCE (role + idx), bytes stay out.
    for msg in reversed(messages):
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        has_image = False
        if isinstance(content, list):
            has_image = any(isinstance(p, dict) and (
                p.get("type") == "image_url" or "image" in (p.get("type") or "")) for p in content)
        if has_image:
            preserve.append({
                "kind": "image_ref", "index": None, "row": msg, "text": "",
                "fingerprint": "", "description": "last screenshot base reference",
            })
            break
    return preserve


def _rf06_reintegrate(compressor, compressed, preserve) -> tuple:
    """Ensure each rf06-preserved record is visible in the COMPRESSED output.

    GV2-SEC-13/14: lost user text re-enters as clearly-typed DATA — a user row
    whose content is an exact data marker + a <source> fence around the
    REDACTED text — so preserved material can neither masquerade as a fresh
    instruction nor leak secrets. A lost assistant(tool_calls) row is
    reinserted verbatim (still open, result still owed) as an assistant row.
    Returns ``(compressed, reintroduced descriptions)``.
    """
    reintroduced: list = []
    blob = "\\n".join(_rf06_content_text(msg) for msg in compressed)
    for record in preserve or []:
        need = record.get("fingerprint") or ""
        if not need:
            continue  # reference-style records are only reported, not matched
        if need in blob:
            continue
        row = record.get("row") or {}
        kind = record.get("kind")
        if kind == "open_tool_call":
            compressed.append({
                "role": "assistant",
                "content": (f"{RF06_DATA_MARKER}\\n"
                            "tool call from before compression; "
                            "its result was lost and it remains unanswered"),
                "tool_calls": row.get("tool_calls"),
            })
        else:
            # GV2-SEC-13: REDACT + wrap in an exact data marker + <source> fence.
            # What follows the fence is HISTORICAL DATA, never model instructions.
            text = _rf06_redact(record.get("text") or "")
            compressed.append({
                "role": "user",
                "content": (
                    f"{RF06_DATA_MARKER}\\n"
                    "<source>\\n"
                    f"{text}\\n"
                    "</source>"
                ),
            })
        reintroduced.append(record.get("description") or kind)
    if reintroduced:
        logger.warning(
            "Context compression re-integrated %d lost preserve record(s): %s",
            len(reintroduced), "; ".join(reintroduced))
    return compressed, reintroduced


''' + CLASS_ANCHOR

# ─────────────────────────── class body edits ───────────────────────────
METHOD_DEADLINE_ANCHOR = "    def _generate_summary(\n"
METHOD_DEADLINE_NEW = '''    def _rf06_collect_preserve(self, messages: List[Dict[str, Any]]) -> list:
        """gv-host-overlay RF-06: deterministic preserve list (goal text, user
        corrections, open tool calls, last image reference) BEFORE anything is
        dropped."""
        return _rf06_collect_preserve(self, messages)

    def _rf06_deadline_seconds(self) -> float:
        """gv-host-overlay RF-06: hard wall budget for ONE compression attempt."""
        raw = os.environ.get("HERMES_COMPRESSION_DEADLINE_SECS", "")
        try:
            value = float(raw) if raw else 120.0
        except (TypeError, ValueError):
            value = 120.0
        return value if value > 0 else 0.0

    def _rf06_deadline_left(self, prompt_started_at: float) -> float:
        """Remaining seconds of the rf06 compression attempt budget."""
        budget = self._rf06_deadline_seconds()
        if budget <= 0:
            return float("inf")
        return max(0.0, budget - (time.monotonic() - prompt_started_at))

    def _generate_summary(
'''
GEN_SUMMARY_CANCEL_ANCHOR = '''        prompt_started_at = time.monotonic()
        if self._compression_cancelled():
            raise AuxiliaryExplicitCancellation()
        # bypass_cooldown: provider-proven overflow gets ONE real attempt while armed.
'''
GEN_SUMMARY_CANCEL_NEW = '''        prompt_started_at = time.monotonic()
        if self._compression_cancelled():
            raise AuxiliaryExplicitCancellation()
        # gv-host-overlay RF-06: hard monotonic budget for ONE compression attempt —
        # exceeding it aborts this attempt (bestehende Timeout-ladder sorts it) and
        # keeps the deterministic state instead of silently dropping it.
        if self._rf06_deadline_left(prompt_started_at) <= 0:
            raise TimeoutError(
                f"compression budget exhausted before the summary attempt "
                f"(HERMES_COMPRESSION_DEADLINE_SECS={self._rf06_deadline_seconds():.0f}s)")
        # bypass_cooldown: provider-proven overflow gets ONE real attempt while armed.
'''
CALLKW_ARGS_ANCHOR = '''        if self.summary_model:
            call_kwargs["model"] = self.summary_model
        # Pinned route (stall fallback) overrides task routing so the retry leaves the stalled backend.
'''
CALLKW_ARGS_NEW = '''        if self.summary_model:
            call_kwargs["model"] = self.summary_model
        # gv-host-overlay RF-06: bound THIS aux attempt by the rf06 wall clock, so a
        # forever-streaming provider dies at the attempt budget, not at the process
        # default (which is longer).
        _rf06_left = self._rf06_deadline_left(prompt_started_at)
        if _rf06_left != float("inf"):
            call_kwargs["timeout"] = max(1.0, _rf06_left)
        # Pinned route (stall fallback) overrides task routing so the retry leaves the stalled backend.
'''
#: the COMPRESS wrapper (capture + reintegrate):
COMPRESS_CAPTURE_ANCHOR = '''        telemetry = self._begin_compress_attempt(current_tokens, force)
'''
COMPRESS_CAPTURE_NEW = '''        telemetry = self._begin_compress_attempt(current_tokens, force)
        # gv-host-overlay RF-06: snapshot the preserve list BEFORE anything is dropped.
        self._rf06_preserve = self._rf06_collect_preserve(messages)
'''
COMPRESS_FINAL_OLD = '''        compressed = self._assemble_compressed(messages, compress_start, compress_end, scan, summary)
        return self._finalize_compressed(compressed, messages, n_messages)
'''
COMPRESS_FINAL_NEW = '''        compressed = self._assemble_compressed(messages, compress_start, compress_end, scan, summary)
        compressed = self._finalize_compressed(compressed, messages, n_messages)
        # gv-host-overlay RF-06: reintroduce anything the output lost (deterministic).
        compressed, _rf06_reintroduced = _rf06_reintegrate(self, compressed, getattr(self, "_rf06_preserve", []))
        if _rf06_reintroduced:
            telemetry["rf06_preserved"] = _rf06_reintroduced
        return compressed
'''
COMPRESSION_EDITS = [
    (CLASS_ANCHOR, HELPERS_BLOCK, 1),
    (METHOD_DEADLINE_ANCHOR, METHOD_DEADLINE_NEW, 1),
    (GEN_SUMMARY_CANCEL_ANCHOR, GEN_SUMMARY_CANCEL_NEW, 1),
    (CALLKW_ARGS_ANCHOR, CALLKW_ARGS_NEW, 1),
    (COMPRESS_CAPTURE_ANCHOR, COMPRESS_CAPTURE_NEW, 1),
    (COMPRESS_FINAL_OLD, COMPRESS_FINAL_NEW, 1),
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
    return apply_file(target / COMPRESSION, COMPRESSION_EDITS, check=check)


def main() -> int:
    args = [a for a in sys.argv[1:]]
    check = "--check" in args
    args = [a for a in args if not a.startswith("--")]
    target = Path(args[0]) if args else DEFAULT_SRC
    _refuse_live_engine(target, check=check)
    return patch(target, check=check)


if __name__ == "__main__":
    raise SystemExit(main())
