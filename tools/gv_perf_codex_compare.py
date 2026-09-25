#!/usr/bin/env python3
"""Codex-comparison performance harness (CODEX-PERF block).

Five metrics, each measured against a **local stub provider** so no network,
credential or model variance is in the number:

| mode | metric |
|---|---|
| ``firstbyte`` | time to the first *visible* token on a streaming model call |
| ``toolrt`` | tool-call round trip: request → tool start → result → next request |
| ``turn`` | wall time of a 2-step task, split into model / tool / loop overhead |
| ``memory`` | RSS + traced Python growth after 100 agent turns (clean child) |
| ``commands`` | cold vs warm p50/p95 on the N heaviest CLI commands |
| ``progress`` | cost of 100k progress events, coalesced vs written one by one |

Reproduce the before/after table in ``MVP/47-performance-codex.md`` with the
kill switch as the only difference between the two columns::

    cd /home/willy/projects/gods-voice
    env -u PYTHONPATH -u HERMES_HOME -u SKALDIR_HOME SKALDIR_PERF_NOCACHE=1 \\
        .venv/bin/python tools/gv_perf_codex_compare.py all --label before --runs 15
    env -u PYTHONPATH -u HERMES_HOME -u SKALDIR_HOME \\
        .venv/bin/python tools/gv_perf_codex_compare.py all --label after --runs 15
    .venv/bin/python tools/gv_perf_codex_compare.py table

Measurement rules kept (see ``references/cli-performance.md``):

* the tree is pinned (``PYTHONPATH=<repo>/src``) and ``skaldir.__file__`` is
  printed, so a sibling checkout can never be silently measured;
* every sample is a real product call (``OpenAICompatibleLLM.chat`` through
  ``AgentLoop``), never a mock that replaces the code under test;
* p50 **and** p95, plus the run count and load average;
* memory comes from a **fresh child process** (its own ``ru_maxrss`` / VmRSS)
  *and* from ``tracemalloc`` — two independent methods, because one memory
  number is not evidence;
* the stub speaks HTTP/1.1 with keep-alive and counts distinct TCP client
  ports, so "the connection was reused" is an observation, not a claim.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

OUTPUT_DIR = REPO / "outputs" / "perf-codex"
WARM_HARNESS = REPO / "tools" / "gv_warm_perf.py"

#: Turns for the memory metric (the brief names 100).
MEMORY_TURNS = 100
#: How many times the memory child runs; a single RSS sample is not evidence.
MEMORY_REPEATS = 3
#: The **pinned** heaviest commands — measured once (cold p50, load1 7.4) and then
#: fixed, because a load-dependent ranking picks a *different* five on each run and
#: the two columns would then compare different command sets (measured: the first
#: two snapshots shared only three of five, which made a meaningless "−10 %").
#: Re-select deliberately with ``--auto-heavy`` when the CLI's cost profile moves.
HEAVY_COMMANDS: tuple[tuple[str, ...], ...] = (
    ("selftest", "--json"),  # ~1 190 ms cold / ~470 ms warm
    ("doctor",),  # ~870 ms / ~290 ms
    ("completion", "status", "--json"),  # ~840 ms / ~60 ms
    ("registry", "status", "--json"),  # ~800 ms / ~140 ms
    ("contract", "dump", "--json"),  # ~720 ms / ~125 ms
)
#: Candidates for ``--auto-heavy`` (superset of the pinned five).
HEAVY_CANDIDATES: tuple[tuple[str, ...], ...] = HEAVY_COMMANDS + (
    ("tools", "list", "--json"),
    ("skills", "list", "--json"),
    ("memory", "stats", "--json"),
    ("tools", "list"),
)


# ------------------------------------------------------------------ statistics


def stats_of(values: Sequence[float]) -> dict[str, Any]:
    """p50/p95/min/max of a sample list — the only shape reported anywhere."""
    clean = sorted(float(value) for value in values)
    if not clean:
        return {"n": 0, "p50": None, "p95": None, "min": None, "max": None}
    return {
        "n": len(clean),
        "p50": round(statistics.median(clean), 3),
        "p95": round(clean[min(len(clean) - 1, int(round(0.95 * (len(clean) - 1))))], 3),
        "min": round(clean[0], 3),
        "max": round(clean[-1], 3),
    }


def load_average() -> float:
    try:
        return round(os.getloadavg()[0], 2)
    except OSError:  # pragma: no cover
        return 0.0


def cache_enabled() -> bool:
    """Whether the perf seams are on in this process (the before/after switch)."""
    from skaldir.perf_runtime import CACHE_ENABLED

    return bool(CACHE_ENABLED)


def measured_tree() -> dict[str, Any]:
    """What was actually imported — guards the sibling-checkout trap."""
    import skaldir

    return {"module": str(skaldir.__file__), "src": str(SRC), "python": sys.executable}


# ------------------------------------------------------------- stub provider


@dataclass
class Step:
    """One scripted answer: streamed deltas, or a whole JSON body."""

    deltas: list[str] = field(default_factory=list)
    body: dict[str, Any] | None = None
    delay_first: float = 0.0
    delay_between: float = 0.0

    @property
    def streams(self) -> bool:
        return bool(self.deltas)


def sse_step(
    deltas: Sequence[str], *, delay_first: float = 0.0, delay_between: float = 0.0
) -> Step:
    return Step(deltas=list(deltas), delay_first=delay_first, delay_between=delay_between)


def tool_call_body(name: str, arguments: str, call_id: str = "call_1") -> dict[str, Any]:
    """An OpenAI-shaped answer that asks for one tool."""
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {"name": name, "arguments": arguments},
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16},
    }


def answer_body(text: str) -> dict[str, Any]:
    return {
        "choices": [
            {"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 20, "completion_tokens": 6, "total_tokens": 26},
    }


class _KeepAliveServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request: Any, client_address: Any) -> None:  # noqa: ARG002
        return  # a client that timed out is expected, not a failure


class StubProvider:
    """An OpenAI-compatible loopback endpoint that answers from a script.

    HTTP/1.1 with keep-alive, so a *reused* client shows up as **one** TCP
    connection for the whole run. Every request records the TCP client port; the
    count of distinct ports is the connection count.
    """

    def __init__(self, steps: Sequence[Step]) -> None:
        assert steps, "the stub needs at least one step"
        self._steps = list(steps)
        self._lock = threading.Lock()
        self.requests: list[dict[str, Any]] = []
        self.ports: list[int] = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            # Measured trap: without this the *stub's* socket has Nagle on, and on
            # a re-used connection the 12 ms-then-chunk write pattern stalls ~28 ms
            # on the client's delayed ACK — which reads exactly like the product
            # getting slower once the connection is pooled. It is the rig, not the
            # product (our client already sets TCP_NODELAY: httpcore `sync.py:215`).
            disable_nagle_algorithm = True

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002, ARG002
                return

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                payload = self.rfile.read(length) if length else b""
                try:
                    body = json.loads(payload) if payload else {}
                except ValueError:
                    body = {"_unparsed": payload.decode("utf-8", "replace")}
                with stub._lock:
                    stub.requests.append({"path": self.path, "body": body})
                    stub.ports.append(self.client_address[1])
                    step = stub._steps[min(len(stub.requests) - 1, len(stub._steps) - 1)]
                if step.streams:
                    self._stream(step)
                else:
                    self._json(step)

            def _stream(self, step: Step) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                try:
                    if step.delay_first:
                        time.sleep(step.delay_first)
                    for piece in step.deltas:
                        line = (
                            "data: "
                            + json.dumps({"choices": [{"delta": {"content": piece}}]})
                            + "\n\n"
                        )
                        self._chunk(line.encode())
                        if step.delay_between:
                            time.sleep(step.delay_between)
                    self._chunk(b"data: [DONE]\n\n")
                    self.wfile.write(b"0\r\n\r\n")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def _chunk(self, data: bytes) -> None:
                self.wfile.write(f"{len(data):X}\r\n".encode() + data + b"\r\n")
                self.wfile.flush()

            def _json(self, step: Step) -> None:
                payload = json.dumps(step.body or {}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                try:
                    self.wfile.write(payload)
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self._server = _KeepAliveServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(
            target=lambda: self._server.serve_forever(poll_interval=0.02), daemon=True
        )

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}/v1"

    def start(self) -> StubProvider:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    def __enter__(self) -> StubProvider:
        return self.start()

    def __exit__(self, *exc: Any) -> bool:
        self.stop()
        return False

    @property
    def connections(self) -> int:
        """Distinct TCP client ports seen — i.e. how many connections were opened."""
        with self._lock:
            return len(set(self.ports))

    @property
    def request_count(self) -> int:
        with self._lock:
            return len(self.requests)


def provider_for(
    stub: StubProvider, *, stream: bool, api_key: str = "stub-key", client: Any = None
) -> Any:
    """A real ``OpenAICompatibleLLM`` pointed at the stub (no env, no config file)."""
    from skaldir.llm_provider import OpenAICompatibleLLM, resolve_provider_config

    cfg = resolve_provider_config(
        {
            "base_url": stub.base_url,
            "model": "stub-model",
            "api_key": api_key,
            "stream": stream,
            "max_retries": 0,
            "timeout_s": 30.0,
        },
        env={},
        config_path=REPO / "outputs" / "perf-codex" / "absent.yaml",
    )
    return OpenAICompatibleLLM(cfg, client=client)


# ------------------------------------------------------------------- timing


class TimingProvider:
    """Wraps the real client and stamps every request boundary (monotonic).

    The loop is untouched: it still calls ``chat()``. Each stamped name is
    ``request_N`` (entering the Nth model call) and ``response_N`` (it returned).
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.stamps: dict[str, float] = {}
        self.count = 0
        self.first_byte_ms: list[float] = []

    def chat(self, *args: Any, **kwargs: Any) -> Any:
        self.count += 1
        self.stamps[f"request_{self.count}"] = time.monotonic()
        result = self._inner.chat(*args, **kwargs)
        self.stamps[f"response_{self.count}"] = time.monotonic()
        value = getattr(result, "first_byte_ms", None)
        if isinstance(value, (int, float)):
            self.first_byte_ms.append(float(value))
        return result

    @property
    def config(self) -> Any:
        return getattr(self._inner, "config", None)


def stamped_tools(workspace: Path, stamps: dict[str, float]) -> list[Any]:
    """The real builtin tools, with the ``read_text`` handler time-stamped."""
    from skaldir.agent_loop import AgentTool, builtin_tools

    out: list[Any] = []
    for tool in builtin_tools(workspace):
        if tool.name != "read_text":
            out.append(tool)
            continue
        inner = tool.handler

        def handler(*args: Any, _inner: Any = inner, **kwargs: Any) -> Any:
            stamps["tool_start"] = time.monotonic()
            try:
                return _inner(*args, **kwargs)
            finally:
                stamps["tool_end"] = time.monotonic()

        out.append(
            AgentTool(
                name=tool.name,
                description=tool.description,
                input_schema=tool.input_schema,
                handler=handler,
                parallel_safe=tool.parallel_safe,
                mutating=tool.mutating,
                source=tool.source,
                source_name=tool.source_name,
            )
        )
    return out


def workspace_for(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "note.txt").write_text("hello from the workspace\n" * 8, encoding="utf-8")
    return root


def fresh_state(label: str) -> Path:
    return Path(tempfile.mkdtemp(prefix=f"gv-perf-{label}-"))


# -------------------------------------------------------------------- modes


def measure_firstbyte(runs: int) -> dict[str, Any]:
    """(a) Time to the first visible token on a streaming call.

    The stub sleeps ``DELAY_FIRST`` before its first delta and then streams 20
    tokens every 2 ms. ``first_byte_ms`` is measured *inside* the provider (send
    → first content delta), which is exactly what a user perceives as "it
    started answering"; ``call_ms`` is the whole stream.
    """
    from skaldir.perf_runtime import cache_stats, reset_caches

    delay_first, tokens, between = 0.012, 20, 0.002
    step = sse_step(
        [f"token{i} " for i in range(tokens)], delay_first=delay_first, delay_between=between
    )
    firsts: list[float] = []
    totals: list[float] = []
    total_connections = 0
    reset_caches()
    with StubProvider([step]) as stub:
        client = provider_for(stub, stream=True)
        for _ in range(runs):
            started = time.monotonic()
            result = client.chat([{"role": "user", "content": "stream please"}])
            totals.append((time.monotonic() - started) * 1000.0)
            value = getattr(result, "first_byte_ms", None)
            if isinstance(value, (int, float)):
                firsts.append(float(value))
        total_connections = stub.connections
    counters = cache_stats()
    reset_caches()
    return {
        "runs": runs,
        "stub_first_token_delay_ms": delay_first * 1000.0,
        "first_byte_ms": stats_of(firsts),
        "call_ms": stats_of(totals),
        "tcp_connections_total": total_connections,
        "tcp_connections_per_call": round(total_connections / max(1, runs), 3),
        "measured_samples_ms": [round(value, 3) for value in firsts],
        "runtime_counters": counters,
    }


def measure_toolrt(runs: int) -> dict[str, Any]:
    """(b) Tool round trip: request → tool start → result → next request.

    One stub **per run** (each run starts its own script), so the reported
    "connections per run" is the honest per-run number: 2 model calls should
    share one TCP connection.
    """
    from skaldir.agent_loop import AgentLoop, LoopLimits
    from skaldir.perf_runtime import cache_stats, reset_caches

    segments: dict[str, list[float]] = {"request_to_tool": [], "tool_exec": [], "tool_to_next": []}
    totals: list[float] = []
    connections: list[float] = []
    requests_per_run: list[float] = []
    reset_caches()
    for _ in range(runs):
        steps = [
            Step(body=tool_call_body("read_text", json.dumps({"path": "note.txt"}))),
            Step(body=answer_body("the note is there")),
        ]
        with StubProvider(steps) as stub:
            root = fresh_state("toolrt")
            provider = TimingProvider(provider_for(stub, stream=False))
            stamps = provider.stamps  # one timeline: model boundaries + the tool
            loop = AgentLoop(
                provider,
                stamped_tools(workspace_for(root), stamps),
                state_dir=root / "state",
                workspace=root,
                limits=LoopLimits(max_steps=4),
                policy=True,
            )
            started = time.monotonic()
            result = loop.run("read the note", run_id="rt")
            totals.append((time.monotonic() - started) * 1000.0)
            if result.status != "answered":
                raise RuntimeError(f"the tool round trip did not answer: {result.to_dict()}")
            segments["request_to_tool"].append(
                (stamps["tool_start"] - stamps["request_1"]) * 1000.0
            )
            segments["tool_exec"].append((stamps["tool_end"] - stamps["tool_start"]) * 1000.0)
            segments["tool_to_next"].append((stamps["request_2"] - stamps["tool_end"]) * 1000.0)
            connections.append(float(stub.connections))
            requests_per_run.append(float(stub.request_count))
    counters = cache_stats()
    reset_caches()
    return {
        "runs": runs,
        "steps": 2,
        "requests_per_run": stats_of(requests_per_run),
        "segments_ms": {name: stats_of(values) for name, values in segments.items()},
        "round_trip_ms": stats_of(totals),
        "sum_of_segments_p50_ms": round(
            sum(stats_of(values)["p50"] or 0.0 for values in segments.values()), 3
        ),
        "tcp_connections_per_run": stats_of(connections),
        "runtime_counters": counters,
    }


def measure_turn(runs: int, *, turns: int = 2) -> dict[str, Any]:
    """(c) Wall time of a 2-step task, split into model / tool / loop overhead."""
    from skaldir.agent_loop import AgentLoop, LoopLimits
    from skaldir.perf_runtime import reset_caches

    model_ms: list[float] = []
    tool_ms: list[float] = []
    overhead_ms: list[float] = []
    totals: list[float] = []
    reset_caches()
    for _round in range(runs):
        steps = [
            Step(body=tool_call_body("read_text", json.dumps({"path": "note.txt"}))),
            Step(body=answer_body("done")),
        ]
        with StubProvider(steps) as stub:
            root = fresh_state("turn")
            provider = TimingProvider(provider_for(stub, stream=False))
            stamps = provider.stamps
            loop = AgentLoop(
                provider,
                stamped_tools(workspace_for(root), stamps),
                state_dir=root / "state",
                workspace=root,
                limits=LoopLimits(max_steps=6),
            )
            started = time.monotonic()
            result = loop.run("two step task", run_id="turn")
            totals.append((time.monotonic() - started) * 1000.0)
            if result.status != "answered":
                raise RuntimeError(f"the turn did not answer: {result.to_dict()}")
            spent_model = sum(
                (provider.stamps[f"response_{n}"] - provider.stamps[f"request_{n}"]) * 1000.0
                for n in range(1, provider.count + 1)
            )
            spent_tool = (stamps["tool_end"] - stamps["tool_start"]) * 1000.0
            model_ms.append(spent_model)
            tool_ms.append(spent_tool)
            overhead_ms.append(totals[-1] - spent_model - spent_tool)
    reset_caches()
    return {
        "runs": runs,
        "turns": turns,
        "turn_ms": stats_of(totals),
        "model_ms": stats_of(model_ms),
        "tool_ms": stats_of(tool_ms),
        "loop_overhead_ms": stats_of(overhead_ms),
    }


def measure_memory(turns: int, repeats: int = MEMORY_REPEATS) -> dict[str, Any]:
    """(d) Memory after N agent turns — measured in *fresh child* processes.

    Repeated ``repeats`` times: one RSS sample is not evidence, and the run time
    is the metric most sensitive to a quadratic writer, so both are reported as
    p50/p95 across clean processes.
    """
    payloads: list[dict[str, Any]] = []
    for _ in range(max(1, repeats)):
        argv = [
            sys.executable,
            str(Path(__file__).resolve()),
            "_memchild",
            "--turns",
            str(turns),
        ]
        env = dict(os.environ)
        env["PYTHONPATH"] = str(SRC)
        proc = subprocess.run(argv, capture_output=True, text=True, env=env, timeout=900)
        if proc.returncode != 0:
            raise RuntimeError(
                f"memory child failed ({proc.returncode}): {proc.stderr[-2000:]}"
            )
        payloads.append(json.loads(proc.stdout.strip().splitlines()[-1]))
    payload = dict(payloads[-1])
    payload["repeats"] = len(payloads)
    for key in ("rss_growth_mb", "run_ms", "tracemalloc_peak_mb", "rss_after_mb"):
        values = [item[key] for item in payloads]
        payload[f"{key}_all"] = [round(value, 3) for value in values]
        payload[key] = stats_of(values)
    payload["rss_growth_mb_median"] = payload["rss_growth_mb"]["p50"]
    return payload


def _memchild(turns: int) -> int:
    """The measured side of :func:`measure_memory` (runs in its own process)."""
    import resource
    import tracemalloc

    from skaldir.agent_loop import AgentLoop, LoopLimits
    from skaldir.perf_runtime import reset_caches

    def vm_rss_mb() -> float:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return round(int(line.split()[1]) / 1024.0, 3)
        return 0.0

    read_step = Step(body=tool_call_body("read_text", json.dumps({"path": "note.txt"})))
    steps = [read_step] * max(1, turns - 1)
    steps.append(Step(body=answer_body("turn complete")))
    root = workspace_for(fresh_state("mem"))
    reset_caches()
    before = {
        "rss_mb": vm_rss_mb(),
        "maxrss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0, 3),
    }
    result: Any = None
    elapsed = 0.0
    peak = current = 0
    connections = 0
    with StubProvider(steps) as stub:
        provider = provider_for(stub, stream=False)
        loop = AgentLoop(
            provider,
            stamped_tools(root, {}),
            state_dir=root / "state",
            workspace=root,
            limits=LoopLimits(max_steps=turns + 5, wall_clock_s=900.0),
        )
        tracemalloc.start()
        started = time.monotonic()
        result = loop.run("keep reading the note", run_id="mem-run")
        elapsed = time.monotonic() - started
        current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        connections = stub.connections
    after = {
        "rss_mb": vm_rss_mb(),
        "maxrss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0, 3),
    }
    reset_caches()
    payload = {
        "turns_requested": turns,
        "steps_run": result.steps,
        "status": result.status,
        "reason": result.reason,
        "run_ms": round(elapsed * 1000.0, 1),
        "rss_before_mb": before["rss_mb"],
        "rss_after_mb": after["rss_mb"],
        "rss_growth_mb": round(after["rss_mb"] - before["rss_mb"], 3),
        "maxrss_before_mb": before["maxrss_mb"],
        "maxrss_after_mb": after["maxrss_mb"],
        "tracemalloc_peak_mb": round(peak / (1024 * 1024), 3),
        "tracemalloc_current_mb": round(current / (1024 * 1024), 3),
        "tcp_connections": connections,
        "transcript_bytes": Path(result.transcript_path).stat().st_size,
    }
    print(json.dumps(payload))
    return 0


def measure_commands(
    runs: int, count: int = 5, scratch: str = "", *, auto_heavy: bool = False
) -> dict[str, Any]:
    """(e) Cold vs warm p50/p95 on the N heaviest commands.

    Delegates to ``tools/gv_warm_perf.py`` — the harness that produced the
    warm-session numbers — so "cold" and "warm" mean exactly what they mean
    there: a fresh process per sample, and the same command through the live
    session socket.

    The command set is **pinned** (:data:`HEAVY_COMMANDS`) unless ``auto_heavy``
    is asked for, because a load-dependent ranking selects a different five on
    each run and the two columns would silently compare different sets.
    """
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "HERMES_HOME": os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")),
        "PYTHONPATH": str(SRC),
        "SKALDIR_PERF_NOCACHE": os.environ.get("SKALDIR_PERF_NOCACHE", ""),
    }
    workdir = scratch or tempfile.mkdtemp(prefix="gv-perf-cmd-")
    candidates: tuple[tuple[str, ...], ...] = tuple(HEAVY_COMMANDS[:count])
    if auto_heavy:
        baseline_file = Path(workdir) / "baseline.json"
        subprocess.run(
            [
                sys.executable,
                str(WARM_HARNESS),
                "baseline",
                "--runs",
                str(max(3, runs // 3)),
                "--scratch",
                workdir,
                "--write-json",
                str(baseline_file),
            ],
            env=env,
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
        if baseline_file.is_file():
            measured = json.loads(baseline_file.read_text()).get("commands", {})
            ranked = sorted(measured.items(), key=lambda item: -(item[1].get("p50_ms") or 0.0))
            if ranked:
                candidates = tuple(tuple(name.split()) for name, _ in ranked[:count])

    compare_file = Path(workdir) / "compare.json"
    argv = [
        sys.executable,
        str(WARM_HARNESS),
        "compare",
        "--runs",
        str(runs),
        "--scratch",
        workdir,
        "--write-json",
        str(compare_file),
    ]
    for command in candidates:
        argv += ["--command", " ".join(command)]
    proc = None
    for attempt in range(2):
        # A session start can lose the race with a busy box or a sibling session
        # (measured once at load1 7.4: "session did not start"). One retry, and the
        # failure is reported with the child's own stdout rather than guessed at.
        if compare_file.is_file():
            compare_file.unlink()
        proc = subprocess.run(
            argv, env=env, cwd=workdir, capture_output=True, text=True, timeout=1800, check=False
        )
        if compare_file.is_file():
            break
        if attempt == 0:
            time.sleep(1.0)
    if not compare_file.is_file():  # pragma: no cover - reported, never guessed
        assert proc is not None
        raise RuntimeError(
            f"gv_warm_perf compare produced no payload (exit {proc.returncode}).\n"
            f"stdout: {proc.stdout[-1500:]}\nstderr: {proc.stderr[-1500:]}"
        )
    payload = json.loads(compare_file.read_text())
    rows = []
    for item in payload.get("commands", []):
        cold, warm = item.get("cold", {}), item.get("warm", {})
        rows.append(
            {
                "command": item.get("command", ""),
                "cold_p50_ms": cold.get("p50_ms"),
                "cold_p95_ms": cold.get("p95_ms"),
                "warm_p50_ms": warm.get("p50_ms"),
                "warm_p95_ms": warm.get("p95_ms"),
                "cold_rss_mb": cold.get("rss_mb"),
                "warm_rss_mb": warm.get("rss_mb"),
                "in_session_p50_ms": item.get("daemon_p50_ms"),
                "speedup_p50": (
                    round(cold["p50_ms"] / warm["p50_ms"], 2)
                    if cold.get("p50_ms") and warm.get("p50_ms")
                    else None
                ),
            }
        )
    hot = [row for row in rows if row["speedup_p50"]]
    return {
        "runs": runs,
        "load1": load_average(),
        "interpreter_floor_ms": payload.get("floor_ms"),
        "selected": [row["command"] for row in rows],
        "rows": rows,
        "median_speedup_p50": (
            round(statistics.median(row["speedup_p50"] for row in hot), 2) if hot else None
        ),
        "cold_p50_sum_ms": round(sum(row["cold_p50_ms"] or 0.0 for row in rows), 1),
        "warm_p50_sum_ms": round(sum(row["warm_p50_ms"] or 0.0 for row in rows), 1),
    }


def measure_progress(events: int = 100_000) -> dict[str, Any]:
    """Cost of N progress events, three ways — because a counting stream hides the cost.

    A ``write`` into a Python object costs nothing, so "100k writes vs 14 writes"
    only becomes a *time* win against a sink that behaves like a terminal:

    * ``slow sink`` (50 µs per write — the shape of a terminal flush): what the
      producer waits for, i.e. the responsiveness argument, plus the write count;
    * ``push-only`` — the coalescer's own per-event cost with no I/O at all:
      what a producer pays to hand one line over.
    """
    from skaldir.perf_progress import ProgressCoalescer

    class CountingStream:
        def __init__(self, *, delay: float = 0.0) -> None:
            self.writes = 0
            self.bytes = 0
            self.delay = delay

        def write(self, text: str) -> int:
            self.writes += 1
            self.bytes += len(text)
            if self.delay:
                time.sleep(self.delay)
            return len(text)

        def flush(self) -> None:
            return None

    lines = [f"step {index}: tool progress line" for index in range(events)]

    # 1) naive: one write + flush per event, against a terminal-shaped sink
    slow_naive = CountingStream(delay=50e-6)
    started = time.monotonic()
    for line in lines:
        slow_naive.write(line + "\n")
        slow_naive.flush()
    slow_naive_ms = (time.monotonic() - started) * 1000.0

    # 2) coalesced: push per event (cheap) + a tick loop (one write per batch)
    slow_coalesced = CountingStream(delay=50e-6)
    sink = ProgressCoalescer(stream=slow_coalesced, max_queued=events + 8)
    started = time.monotonic()
    for line in lines:
        sink.push(line)
    pushed_ms = (time.monotonic() - started) * 1000.0
    while True:
        committed = sink.write(sink.tick())
        if not committed and not sink.stats["queued"]:
            break
    slow_coalesced_ms = (time.monotonic() - started) * 1000.0

    # 3) producer cost alone: hand 100k lines over, no sink in the way
    bare = ProgressCoalescer(stream=None, max_queued=events + 8)
    started = time.monotonic()
    for line in lines:
        bare.push(line)
    push_only_ms = (time.monotonic() - started) * 1000.0
    handled = 0
    while True:
        chunk = bare.drain()
        if not chunk:
            break
        handled += len(chunk)

    coalesced_writes = slow_coalesced.writes
    return {
        "events": events,
        "naive_writes": slow_naive.writes,
        "coalesced_writes": coalesced_writes,
        "write_reduction": round(slow_naive.writes / max(1, coalesced_writes), 1),
        "slow_sink_naive_ms": round(slow_naive_ms, 3),
        "slow_sink_coalesced_ms": round(slow_coalesced_ms, 3),
        "slow_sink_ratio": round(slow_naive_ms / max(0.001, slow_coalesced_ms), 2),
        "push_before_drain_ms": round(pushed_ms, 3),
        "push_only_ms": round(push_only_ms, 3),
        "push_ns_per_event": round(push_only_ms * 1e6 / max(1, events), 1),
        "lines_handled": handled,
        "coalesced_batches": coalesced_writes,
    }


MODES = ("firstbyte", "toolrt", "turn", "memory", "commands", "progress")


def run_all(args: argparse.Namespace) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "label": args.label,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "cache_enabled": cache_enabled(),
        "runs": args.runs,
        "load1": load_average(),
        "tree": measured_tree(),
        "metrics": {},
    }
    wanted = list(MODES) if args.mode == "all" else [args.mode]
    for mode in wanted:
        started = time.monotonic()
        if mode == "firstbyte":
            payload["metrics"][mode] = measure_firstbyte(args.runs)
        elif mode == "toolrt":
            payload["metrics"][mode] = measure_toolrt(args.runs)
        elif mode == "turn":
            payload["metrics"][mode] = measure_turn(args.runs)
        elif mode == "memory":
            payload["metrics"][mode] = measure_memory(args.turns)
        elif mode == "commands":
            payload["metrics"][mode] = measure_commands(
                args.runs, args.heavy, auto_heavy=args.auto_heavy
            )
        elif mode == "progress":
            payload["metrics"][mode] = measure_progress(args.events)
        payload.setdefault("mode_seconds", {})[mode] = round(time.monotonic() - started, 1)
        print(f"[{mode}] done in {payload['mode_seconds'][mode]} s", file=sys.stderr)
    return payload


# ------------------------------------------------------------------- tables


def _cell(value: Any, digits: int = 3) -> str:
    if value is None:
        return "—"
    if isinstance(value, (int, float)):
        return f"{value:,.{digits}f}"
    return str(value)


def _stat(table: dict[str, Any], key: str) -> str:
    entry = (table or {}).get(key) or {}
    median, p95 = entry.get("p50"), entry.get("p95")
    if median is None:
        return "—"
    return f"{median:,.2f} / {p95:,.2f}"


def render_table(before: dict[str, Any], after: dict[str, Any]) -> str:
    """The before/after table — p50/p95 per metric, and the honest delta."""
    lines: list[str] = []
    lines.append(f"| metric | {before.get('label', 'before')} p50/p95 | "
                 f"{after.get('label', 'after')} p50/p95 | change (p50) |")
    lines.append("|---|---:|---:|---:|")
    b, a = before.get("metrics", {}), after.get("metrics", {})

    rows: list[tuple[str, str, str, str, str]] = []

    def delta(before_value: Any, after_value: Any) -> str:
        try:
            first, second = float(before_value), float(after_value)
        except (TypeError, ValueError):
            return "—"
        if not first:
            return "—"
        return f"{(second - first) / first * 100:+.1f}%"

    fb_b, fb_a = b.get("firstbyte", {}), a.get("firstbyte", {})
    rows.append(("first-byte latency (ms)", _stat(fb_b, "first_byte_ms"),
                 _stat(fb_a, "first_byte_ms"),
                 delta((fb_b.get("first_byte_ms") or {}).get("p50"),
                       (fb_a.get("first_byte_ms") or {}).get("p50")),
                 "local stub, 12 ms first-token delay + 20 tokens @ 2 ms"))
    rows.append(("full stream call (ms)", _stat(fb_b, "call_ms"), _stat(fb_a, "call_ms"),
                 delta((fb_b.get("call_ms") or {}).get("p50"),
                       (fb_a.get("call_ms") or {}).get("p50")), "same stub"))
    rows.append(("TCP connections / call", _cell(fb_b.get("tcp_connections_per_call"), 3),
                 _cell(fb_a.get("tcp_connections_per_call"), 3),
                 delta(fb_b.get("tcp_connections_per_call"), fb_a.get("tcp_connections_per_call")),
                 "pooled client reuse, counted by the stub's distinct client ports"))

    rt_b, rt_a = b.get("toolrt", {}), a.get("toolrt", {})
    rows.append(("tool round trip (ms)", _stat(rt_b, "round_trip_ms"),
                 _stat(rt_a, "round_trip_ms"),
                 delta((rt_b.get("round_trip_ms") or {}).get("p50"),
                       (rt_a.get("round_trip_ms") or {}).get("p50")),
                 "2-step run: request → tool → next request"))
    for name, label in (
        ("request_to_tool", "  request → tool start"),
        ("tool_exec", "  tool execution"),
        ("tool_to_next", "  result → next request"),
    ):
        rows.append((label,
                     _stat((rt_b.get("segments_ms") or {}), name),
                     _stat((rt_a.get("segments_ms") or {}), name),
                     delta((((rt_b.get("segments_ms") or {}).get(name) or {}).get("p50")),
                           (((rt_a.get("segments_ms") or {}).get(name) or {}).get("p50"))),
                     "segment"))
    rows.append(("TCP connections / run", _stat(rt_b, "tcp_connections_per_run"),
                 _stat(rt_a, "tcp_connections_per_run"),
                 delta((rt_b.get("tcp_connections_per_run") or {}).get("p50"),
                       (rt_a.get("tcp_connections_per_run") or {}).get("p50")),
                 "2 model calls per run"))

    t_b, t_a = b.get("turn", {}), a.get("turn", {})
    rows.append(("2-step turn total (ms)", _stat(t_b, "turn_ms"), _stat(t_a, "turn_ms"),
                 delta((t_b.get("turn_ms") or {}).get("p50"),
                       (t_a.get("turn_ms") or {}).get("p50")), "wall clock"))
    rows.append(("  of which loop overhead", _stat(t_b, "loop_overhead_ms"),
                 _stat(t_a, "loop_overhead_ms"),
                 delta((t_b.get("loop_overhead_ms") or {}).get("p50"),
                       (t_a.get("loop_overhead_ms") or {}).get("p50")),
                 "transcript + body build + message assembly"))

    m_b, m_a = b.get("memory", {}), a.get("memory", {})
    repeats = m_b.get("repeats") or m_a.get("repeats") or 1
    rows.append(("100 turns RSS growth (MB)", _stat(m_b, "rss_growth_mb"),
                 _stat(m_a, "rss_growth_mb"),
                 delta((m_b.get("rss_growth_mb") or {}).get("p50"),
                       (m_a.get("rss_growth_mb") or {}).get("p50")),
                 f"fresh child x{repeats}, own /proc VmRSS"))
    rows.append(("100 turns run time (ms)", _stat(m_b, "run_ms"), _stat(m_a, "run_ms"),
                 delta((m_b.get("run_ms") or {}).get("p50"),
                       (m_a.get("run_ms") or {}).get("p50")),
                 f"python-only loop, x{repeats}"))
    rows.append(("100 turns traced peak (MB)", _stat(m_b, "tracemalloc_peak_mb"),
                 _stat(m_a, "tracemalloc_peak_mb"),
                 delta((m_b.get("tracemalloc_peak_mb") or {}).get("p50"),
                       (m_a.get("tracemalloc_peak_mb") or {}).get("p50")),
                 "tracemalloc, independent method"))

    c_b, c_a = b.get("commands", {}), a.get("commands", {})
    pinned_b = ", ".join(c_b.get("selected", [])) or "?"
    pinned_a = ", ".join(c_a.get("selected", [])) or "?"
    same_set = pinned_b == pinned_a
    rows.append(("5 heaviest commands cold (ms)", _cell(c_b.get("cold_p50_sum_ms"), 1),
                 _cell(c_a.get("cold_p50_sum_ms"), 1),
                 delta(c_b.get("cold_p50_sum_ms"), c_a.get("cold_p50_sum_ms")),
                 f"sum of p50, fresh process each; same set: {same_set}"))
    rows.append(("5 heaviest commands warm (ms)", _cell(c_b.get("warm_p50_sum_ms"), 1),
                 _cell(c_a.get("warm_p50_sum_ms"), 1),
                 delta(c_b.get("warm_p50_sum_ms"), c_a.get("warm_p50_sum_ms")),
                 "through the live session socket"))
    rows.append(("warm speedup (median)", _cell(c_b.get("median_speedup_p50"), 2),
                 _cell(c_a.get("median_speedup_p50"), 2),
                 delta(c_b.get("median_speedup_p50"), c_a.get("median_speedup_p50")),
                 "cold p50 / warm p50"))

    p_a = a.get("progress", {})
    # These three rows are an A/B of two *strategies* measured inside one run
    # (one write per event vs the coalescer), not of two snapshots — they read the
    # same in every snapshot, so they must not be mistaken for a before/after.
    rows.append(("100k progress events: writes (naive → coalesced)",
                 _cell(p_a.get("naive_writes"), 0), _cell(p_a.get("coalesced_writes"), 0),
                 delta(p_a.get("naive_writes"), p_a.get("coalesced_writes")),
                 "same run, two strategies"))
    rows.append(("100k events on a 50µs sink (ms, naive → coalesced)",
                 _cell(p_a.get("slow_sink_naive_ms"), 1),
                 _cell(p_a.get("slow_sink_coalesced_ms"), 1),
                 delta(p_a.get("slow_sink_naive_ms"), p_a.get("slow_sink_coalesced_ms")),
                 "what the producer waits for"))
    rows.append(("100k events, producer cost (ms, sink → push-only)",
                 _cell(p_a.get("slow_sink_naive_ms"), 1), _cell(p_a.get("push_only_ms"), 1),
                 delta(p_a.get("slow_sink_naive_ms"), p_a.get("push_only_ms")),
                 "no I/O in the producer"))

    for name, before_value, after_value, change, _note in rows:
        lines.append(f"| {name} | {before_value} | {after_value} | {change} |")
    lines.append("")
    lines.append("Notes per row: " + "; ".join(f"{name} — {note}" for name, _, _, _, note in rows))
    lines.append("")
    lines.append(
        f"measured tree: `{after.get('tree', {}).get('module', '?')}` · "
        f"runs {after.get('runs')} · load1 {after.get('load1')} · "
        f"cache/coalescing {'enabled' if after.get('cache_enabled') else 'DISABLED (before)'}"
    )
    return "\n".join(lines)


# --------------------------------------------------------------------- main


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gv_perf_codex_compare", description=(__doc__ or "").splitlines()[0]
    )
    parser.add_argument("--label", default="run", help="name for the snapshot (before/after)")
    parser.add_argument("--runs", type=int, default=15, help="samples per metric")
    parser.add_argument("--turns", type=int, default=MEMORY_TURNS, help="turns for `memory`")
    parser.add_argument("--events", type=int, default=100_000, help="events for `progress`")
    parser.add_argument("--heavy", type=int, default=5, help="how many commands count as heavy")
    parser.add_argument("--auto-heavy", dest="auto_heavy", action="store_true",
                        help="re-select the heaviest commands instead of the pinned set")
    parser.add_argument("--json", action="store_true", help="print the payload as JSON")
    parser.add_argument("--write-json", dest="write_json", default="", help="payload path")
    parser.add_argument("--label-file", dest="label_file", default="",
                        help="snapshot path to read (default: outputs/perf-codex/<label>.json)")
    parser.add_argument("--before", default="", help="before snapshot for `table`")
    parser.add_argument("--after", default="", help="after snapshot for `table`")
    parser.add_argument("--md", default="", help="write the table here")
    parser.add_argument("mode", choices=[*MODES, "all", "table", "_memchild"])
    return parser


def snapshot_path(label: str) -> Path:
    return OUTPUT_DIR / f"{label}.json"


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args([str(item) for item in (argv if argv is not None else sys.argv[1:])])
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if args.mode == "_memchild":
        return _memchild(args.turns)

    if args.mode == "table":
        before_path = Path(args.before) if args.before else snapshot_path("before")
        after_path = Path(args.after) if args.after else snapshot_path("after")
        if not before_path.is_file() or not after_path.is_file():
            print(f"missing snapshot: {before_path} / {after_path}", file=sys.stderr)
            return 1
        table = render_table(
            json.loads(before_path.read_text()), json.loads(after_path.read_text())
        )
        print(table)
        if args.md:
            Path(args.md).write_text(table + "\n", encoding="utf-8")
        return 0

    payload = run_all(args)
    target = Path(args.write_json) if args.write_json else snapshot_path(args.label)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(f"snapshot written to {target}", file=sys.stderr)
    if args.json:
        print(json.dumps(payload, indent=2, default=str))
    else:
        for mode, value in payload["metrics"].items():
            print(f"--- {mode}")
            print(json.dumps(value, indent=2, default=str)[:4000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
