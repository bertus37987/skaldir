#!/usr/bin/env python3
"""RF-01/RF-12 measurement: real HTTP/SSE transport against the fault provider.

Run with the ENGINE interpreter (it has the SDKs the agent uses):

    ~/.hermes/hermes-agent/venv/bin/python tools/rf_measure.py [--out DIR]

What it produces, per RFC §3A:
  * one raw record per logical request with the phase timestamps (connect, response
    headers, first chunk, first semantic delta, last semantic progress, finish/abort),
    the transport outcome, and the provider's OWN record for the same request
    (two-sided attribution: client observation vs server observation);
  * the effective stale-stream thresholds the engine derives for the context sizes
    RFC §5 names (16k/64k/96k/152k) — derived by calling the engine's own functions,
    not re-implemented here;
  * a summary that states plainly what is measured and what remains not_run.

It does NOT drive the full agent loop (no model, no tool budget), so RF-01 stays
open: this is the fixture and the raw data layer RF-02/RF-12 build on.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[1]
ENGINE = Path.home() / ".hermes" / "hermes-agent"

#: RFC §5 names these context sizes for the cost-bounded sample.
CONTEXT_SIZES = (16_000, 64_000, 96_000, 152_000)

#: Modes measured in one run, with the property each one probes.
MODES = (
    ("healthy_fast", "baseline: normal streaming completes"),
    ("healthy_slow", "RF-03: slow-but-healthy progress must not be treated as silence"),
    ("headers_no_sse", "RF-02: headers, then no body byte at all"),
    ("ping_only", "RF-02: SSE pings without a single semantic delta"),
    ("reasoning_only", "RF-02: reasoning deltas are progress but produce no output yet"),
    ("stall_mid", "RF-02: progress, then a long silence mid-stream"),
    ("slow_prefill", "RF-02: long time-to-first-byte"),
    ("partial_toolcall_disconnect", "RF-04/09: partial tool call, connection cut"),
    ("rate_limit", "RF-04: 429 with a long Retry-After"),
    ("unavailable", "RF-04: 503"),
    ("auth_error", "RF-04: auth error must not be retried blindly"),
    ("context_overflow", "RF-04: context overflow must not be retried blindly"),
)


def load_provider(port: int, **opts):
    spec = importlib.util.spec_from_file_location("rf_provider", REPO / "tools" / "rf_test_provider.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["rf_provider"] = module
    spec.loader.exec_module(module)
    state = module.State()
    state.mode = "healthy_fast"
    state.options = SimpleNamespace(**opts)
    module.Handler.state = state
    server = module.QuietServer(("127.0.0.1", port), module.Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return module, state, server


def engine_thresholds() -> dict:
    """Call the engine's OWN stale-timeout derivation for each context size."""
    sys.path.insert(0, str(ENGINE))
    from agent.chat_completion_helpers import (
        _cloud_stale_timeout,
        _configured_stale_base,
        _derive_stream_stale_timeout,
        _scale_stale_timeout_for_context,
    )

    agent = SimpleNamespace(provider="openai", model="rf-test", base_url="https://example.invalid")
    base = _configured_stale_base(agent)
    out: dict[str, dict] = {}
    for tokens in CONTEXT_SIZES:
        out[str(tokens)] = {
            "configured_base_secs": base,
            "after_context_scaling_secs": _scale_stale_timeout_for_context(base, tokens),
            "cloud_stale_timeout_secs": _cloud_stale_timeout(base, {"model": "rf-test", "estimated_tokens": tokens}),
        }
    out["stream_derived_default_secs"] = _derive_stream_stale_timeout(agent, {"model": "rf-test"})
    return out


def measure_one(client, mode: str, record: dict) -> dict:
    """Stream one logical request; stamp the phases the client can see."""
    t0 = time.monotonic()
    result: dict = {"mode": mode, "http_status": None, "chunks": 0, "semantic_chunks": 0}
    try:
        stream = client.chat.completions.create(
            model="rf-test",
            messages=[{"role": "user", "content": "rf measurement"}],
            stream=True,
            # Bounded on purpose: a fault mode that never terminates its chunked body
            # must surface as a client timeout, not hang the measurement.
            timeout=15,
        )
        for chunk in stream:
            now = time.monotonic()
            result.setdefault("t_first_chunk", round(now - t0, 3))
            delta = chunk.choices[0].delta if chunk.choices else None
            semantic = bool(delta and (delta.content or getattr(delta, "reasoning_content", None)))
            if semantic:
                result["semantic_chunks"] += 1
                result.setdefault("t_first_semantic", round(now - t0, 3))
                result["t_last_semantic"] = round(now - t0, 3)
            else:
                result.setdefault("t_first_frame", round(now - t0, 3))
            if chunk.choices and chunk.choices[0].finish_reason:
                result["finish_reason"] = chunk.choices[0].finish_reason
            result["chunks"] += 1
        result["completed"] = True
    except Exception as exc:  # noqa: BLE001 — the outcome IS the measurement
        result["error_class"] = type(exc).__name__
        result["error_text"] = str(exc)[:180]
        status = getattr(exc, "status_code", None)
        result["http_status"] = status
    result["client_total_secs"] = round(time.monotonic() - t0, 3)
    result["provider_record"] = record
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8741)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    if not (ENGINE / "agent").is_dir():
        print("engine checkout not found", file=sys.stderr)
        return 2

    stamp = time.strftime("%Y-%m-%d_%H%M%S")
    out_dir = args.out or (REPO / "outputs" / "rf" / stamp)
    out_dir.mkdir(parents=True, exist_ok=True)

    provider, state, server = load_provider(
        args.port,
        deltas=5,
        interval=0.3,
        stall_after=2,
        stall_secs=6.0,
        prefill_secs=3.0,
        ping_every=1.0,
        retry_after=120,
        silence_max=6.0,
    )
    thresholds = engine_thresholds()

    from openai import OpenAI  # the engine venv ships the SDK the agent uses

    client = OpenAI(api_key="rf-test-key", base_url=f"http://127.0.0.1:{args.port}/v1")
    results: list[dict] = []
    try:
        for mode, probes in MODES:
            with state.lock:
                state.mode = mode
            before = len(state.records)
            result = measure_one(client, mode, {})
            deadline = time.monotonic() + 8.0
            while len(state.records) <= before and time.monotonic() < deadline:
                time.sleep(0.1)
            with state.lock:
                result["provider_record"] = state.records[-1] if len(state.records) > before else None
            result["probes"] = probes
            results.append(result)
            print(
                f"{mode:32s} status={result.get('http_status')} "
                f"semantic={result.get('semantic_chunks')} "
                f"total={result['client_total_secs']}s "
                f"error={result.get('error_class', '-')}"
            )
    finally:
        server.shutdown()
        server.server_close()

    raw = out_dir / "raw.jsonl"
    with raw.open("w", encoding="utf-8") as handle:
        for result in results:
            handle.write(json.dumps(result, sort_keys=True) + "\n")

    measured = {
        "generated_at": stamp,
        "engine_head": _git_head(ENGINE),
        "repo_head": _git_head(REPO),
        "provider": "tools/rf_test_provider.py (isolated, no network egress)",
        "transport": "openai SDK over real HTTP/1.1 + SSE (chunked)",
        "engine_stale_thresholds": thresholds,
        "modes": [
            {
                "mode": r["mode"],
                "probes": r["probes"],
                "http_status": r.get("http_status"),
                "semantic_chunks": r.get("semantic_chunks", 0),
                "client_secs": r["client_total_secs"],
                "completed": r.get("completed", False),
                "error_class": r.get("error_class"),
                "provider_secs": (r.get("provider_record") or {}).get("total_secs"),
                "provider_finished": (r.get("provider_record") or {}).get("finished", False),
                "provider_aborted": (r.get("provider_record") or {}).get("client_aborted", False),
            }
            for r in results
        ],
        "not_run": [
            "full agent loop / 80-iteration segment life (needs a model + budget)",
            "real provider (GLM) sample — no authorized budget in this window",
            "compression and summary paths (RF-06/RF-07)",
            "crash/restart windows (RF-09) and stop races (RF-10)",
        ],
    }
    summary = out_dir / "summary.json"
    summary.write_text(json.dumps(measured, indent=2), encoding="utf-8")
    # A relative --out is a legal invocation, so never assume the paths sit under REPO.
    print(f"\nwrote {_display(raw)} and {_display(summary)}")
    print("stale thresholds:", json.dumps(thresholds["152000"]))
    return 0


def _display(path: Path) -> str:
    """Repo-relative when possible, absolute otherwise — never raise on a legal --out."""
    try:
        return str(path.resolve().relative_to(REPO))
    except ValueError:
        return str(path.resolve())


def _git_head(path: Path) -> str:
    import subprocess

    try:
        return subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        ).stdout.strip()
    except OSError:
        return ""


if __name__ == "__main__":
    raise SystemExit(main())

