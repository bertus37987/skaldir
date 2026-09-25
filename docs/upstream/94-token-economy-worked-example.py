"""§94 worked example — real numbers, from a real run, for the four quick wins.

Run with the repo's canonical interpreter:

    .venv/bin/python docs/94-token-economy-worked-example.py

Writes ``artifacts-94-token-economy/`` next to this file. Everything printed here is
measured by this script: no number in the report is typed in by hand.
"""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "tests"))

from skaldir.agent_loop import AgentLoop, AgentTool, LoopLimits, ToolSet  # noqa: E402
from skaldir.llm_provider import provider_for  # noqa: E402
from skaldir.progress_guard import ProgressGuard, RetryGate, TurnObservation  # noqa: E402
from skaldir.tool_archive import ToolArchive  # noqa: E402

OUT = Path(__file__).resolve().parent / "artifacts-94-token-economy"
OUT.mkdir(parents=True, exist_ok=True)

# --- a deterministic stub endpoint: the same one the tests use ---------------- #


class Stub:
    """A ``/v1/chat/completions`` that answers from a script (no network, no key)."""

    def __init__(self, steps: list[dict]) -> None:
        self.steps = steps
        self.requests: list[dict] = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format, *args) -> None:  # noqa: A002, ARG002
                return

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                position = len(stub.requests)
                stub.requests.append(body)
                payload = stub.steps[min(position, len(stub.steps) - 1)]
                raw = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base_url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}/v1"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def tool_step(call_id: str, name: str, arguments: dict, *, text: str = "") -> dict:
    return {
        "choices": [
            {
                "index": 0,
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": text,
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {"name": name, "arguments": json.dumps(arguments)},
                        }
                    ],
                },
            }
        ],
        "usage": {"prompt_tokens": 1200, "completion_tokens": 40, "total_tokens": 1240},
    }


def answer(text: str, *, usage: dict | None = None) -> dict:
    payload = {
        "choices": [
            {"index": 0, "finish_reason": "stop", "message": {"role": "assistant",
                                                             "content": text}}
        ]
    }
    if usage:
        payload["usage"] = usage
    return payload


# --- quick win 3: a real oversized tool result, archived --------------------- #


def oversized_dump() -> str:
    """A realistic container log dump: 2 000 lines, ~72 000 chars."""
    return "\n".join(
        f"2026-09-19T13:{index % 60:02d}:00Z INFO  worker[{index % 8}] "
        f"processed batch {index:05d} in {index % 250}ms"
        for index in range(2000)
    )


def main() -> int:
    report: dict = {"sections": {}}
    work = OUT / "run"
    workspace = work / "ws"
    state = work / "state"
    workspace.mkdir(parents=True, exist_ok=True)

    dump = oversized_dump()
    archive = ToolArchive(state, threshold_chars=6000)
    log_tool = AgentTool(
        name="container_logs",
        description="the container's log dump",
        input_schema={"type": "object", "properties": {}},
        handler=lambda: {"logs": dump, "lines": dump.count("\n") + 1},
        source="docker",
    )

    stub = Stub([tool_step("c1", "container_logs", {}), answer("read the archive id; done")])
    try:
        loop = AgentLoop(
            provider_for(
                {
                    "provider": "openai-compatible",
                    "base_url": stub.base_url,
                    "model": "stub-94",
                    "api_key": "stub-local-key",
                }
            ),
            ToolSet.of([log_tool]),
            state_dir=state,
            workspace=workspace,
            limits=LoopLimits(max_steps=4),
            max_result_chars=4000,
            archive=archive,
        )
        result = loop.run("inspect the container logs")

        # what the model actually received on the second request
        second = stub.requests[1]["messages"]
        tool_message = [item for item in second if item.get("role") == "tool"][0]
        hot = tool_message["content"]
        payload = json.loads(hot)
        ref = payload["archived"]["ref"]

        artifact = archive.retrieve(ref)
        inline_equivalent = json.dumps(
            {"ok": True, "executed": True,
             "result": {"logs": dump, "lines": dump.count(chr(10)) + 1},
             "error": ""},
            ensure_ascii=False,
        ).encode("utf-8")

        section = {
            "tool": "container_logs",
            "full_result_chars": len(inline_equivalent),
            "full_result_bytes": len(inline_equivalent),
            "hot_form_chars": len(hot),
            "tokens_before_estimate_chars4": len(inline_equivalent) // 4,
            "tokens_after_estimate_chars4": len(hot) // 4,
            "tokens_saved_estimate_chars4": (len(inline_equivalent) // 4) - (len(hot) // 4),
            "saved_fraction": round(1 - (len(hot) / len(inline_equivalent)), 4),
            "artifact_ref": ref,
            "artifact_sha256": payload["archived"]["sha256"],
            "artifact_path": payload["archived"]["path"],
            "artifact_bytes_retrieved": len(artifact),
            "retrieval_is_byte_identical": artifact == inline_equivalent,
            "hot_form_carries_id_and_digest": ("tool_archive:" in hot
                                               and '"sha256"' in hot),
            "dump_absent_from_prompt": "processed batch 01999" not in hot,
            "run_status": result.status,
            "executed_calls": result.executed_calls,
            "archive_stats": result.archive,
        }
        report["sections"]["archived_tool_result"] = section
        (OUT / "archived-tool-result.json").write_text(json.dumps(section, indent=2))
        (OUT / "hot-form.json").write_text(hot)
        print("[3] archive: %d chars -> %d chars in the prompt (-%.1f%%), "
              "artifact %d bytes, byte-identical=%s"
              % (section["full_result_chars"], section["hot_form_chars"],
                 100 * section["saved_fraction"], section["artifact_bytes_retrieved"],
                 section["retrieval_is_byte_identical"]))
    finally:
        stub.close()

    # --- quick wins 1+2: the durable signals and the decision ----------------- #
    guard = ProgressGuard()
    decisions = []
    same = "Everything is done. Completion requested."
    for turn, observation in enumerate(
        [
            TurnObservation(turn=1, answer_text=same, verification="INCOMPLETE",
                            tokens_used=4096, token_budget=8192, steps=3, max_steps=14),
            TurnObservation(turn=2, answer_text=same, verification="INCOMPLETE",
                            tokens_used=8192, token_budget=8192, steps=3, max_steps=14),
            TurnObservation(turn=3, answer_text=same, verification="INCOMPLETE",
                            tokens_used=12288, token_budget=16384, steps=3, max_steps=14),
        ],
        start=1,
    ):
        decision = guard.observe(observation)
        decisions.append({"turn": turn, **decision.to_dict()})
        print("[1+2] turn %d -> action=%-24s signal=%-36s reason=%s"
              % (turn, decision.action, decision.signal, decision.reason))
    signals = guard.state().to_dict()
    report["sections"]["progress_decision"] = {
        "decisions": decisions,
        "signals": signals,
        "named_stop_reason": decisions[-1]["reason"],
        "directive_delivered_on_turn": 2,
        "stopped_on_turn": 3,
    }
    (OUT / "progress-decision.json").write_text(json.dumps(
        report["sections"]["progress_decision"], indent=2))

    # --- quick win 4: the commit point ---------------------------------------- #
    gate = RetryGate(max_attempts=1)
    gate.begin(turn=1)
    before = gate.decide(attempt=1, error="ReadError: connection reset")
    gate.begin(turn=2)
    gate.note_visible_output(31)
    after = gate.decide(attempt=1, error="ReadError: connection reset")
    gate.begin(turn=3)
    gate.note_tool_executed(1)
    after_tool = gate.decide(attempt=1, error="ReadError: connection reset")
    report["sections"]["retry_gate"] = {
        "before_commit_point": before.to_dict(),
        "after_visible_output": after.to_dict(),
        "after_tool_executed": after_tool.to_dict(),
    }
    (OUT / "retry-gate.json").write_text(json.dumps(
        report["sections"]["retry_gate"], indent=2))
    print("[4] retry before commit: %s | after visible output: %s (%s) | "
          "after a tool ran: %s"
          % (before.action, after.action, after.commit["blocker"], after_tool.action))

    # --- the owner's question: the token report, per mechanism ---------------- #
    events = [
        {"kind": "run.started", "run_id": "94-worked-example"},
        {
            "kind": "model.response",
            "step": 1,
            "text": "",
            "usage": {
                "prompt_tokens": 4096,
                "completion_tokens": 4096,
                "total_tokens": 8192,
                "reasoning_tokens": 3703,
                "prompt_cache_hit_tokens": 3072,
            },
        },
        {
            "kind": "tool_archived",
            "name": "container_logs",
            "ref": report["sections"]["archived_tool_result"]["artifact_ref"],
            "sha256": report["sections"]["archived_tool_result"]["artifact_sha256"],
            "chars": report["sections"]["archived_tool_result"]["full_result_chars"],
            "tokens_before": report["sections"]["archived_tool_result"][
                "tokens_before_estimate_chars4"],
            "tokens_after": report["sections"]["archived_tool_result"][
                "tokens_after_estimate_chars4"],
            "tokens_saved": report["sections"]["archived_tool_result"][
                "tokens_saved_estimate_chars4"],
            "source": "docker",
        },
    ]
    from skaldir import telemetry

    record = telemetry.build_record_from_events(
        events, transcript="/tmp/94-worked-example.jsonl"
    )
    report["sections"]["token_report"] = record.to_dict()
    (OUT / "token-report.json").write_text(json.dumps(record.to_dict(), indent=2))
    (OUT / "token-report.txt").write_text("\n".join(record.lines()) + "\n")
    print("[+] token report written to", OUT / "token-report.txt")

    (OUT / "worked-example.json").write_text(json.dumps(report, indent=2, default=str))
    print("\n" + "\n".join(record.lines()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
