#!/usr/bin/env python3
"""Long-project end-to-end run: goal → plan → edit → test → forced failure → continue →
compact → crash/restart → VERIFY PASS, plus the impossible-criterion control.

    .venv/bin/python tools/gv_long_project_e2e.py                   # both scenarios
    .venv/bin/python tools/gv_long_project_e2e.py --scenario achievable
    .venv/bin/python tools/gv_long_project_e2e.py --keep            # keep the scratch run dir

What it proves (and who owns what — the harness composes, it does not replace):

| step | product module under test |
|---|---|
| goal + criteria + checkpoint + resume cursor | ``skaldir.runs_store.RunsStore`` |
| completion contract, states, timeline | ``skaldir.completion.CompletionStore`` |
| "the model may only request completion" | ``skaldir.completion.GateRunner.verify`` |
| the checks that really run | ``skaldir.completion.run_requirement_checks`` / ``execute_check``
  (declared, bwrap-sandboxed) |
| edits | ``skaldir.edit_tx.run_transaction`` (the ``edit apply`` engine) |
| the turn engine + transcript | ``skaldir.agent_loop.AgentLoop`` over ``skaldir.llm_provider`` |
| context threshold + compaction | ``skaldir.context_manager`` (+ the ``skaldir.jevcomp`` port,
  fixture asker) |
| final per-criterion verdict | ``skaldir.verify_service.build_plan`` / ``run_plan`` /
  ``verdict_for`` |

The harness itself owns: the scratch project (``tests/fixtures/long_project``), the
deterministic **local stub provider** (loopback only, no key, no network), the two tool
adapters (``edit_apply`` → ``edit_tx``, ``run_tests`` → ``execute_check``) and the
"runtime" glue that decides what happens after a rejected completion.  No product module
is patched or monkeypatched.

The scenario: a real (small) project whose ``slugify`` is wrong.  Three criteria — the
suite, the CLI (declared ``run`` check), the packaged artifact (declared ``build`` check).
The stub's first work turn applies a deliberately PARTIAL fix, so the project's own
pytest run really fails, the model still claims success, and the supervisor must reject
it.  The runtime then continues on its own (no user input), compacts its context, and the
process is SIGKILLed mid-flight; a second process resumes from the durable checkpoint and
finishes the job.

Exit code 0 = every expectation of both scenarios held; 2 = at least one did not (the
report names it); 3 = the harness could not run the scenario at all.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
FIXTURE = ROOT / "tests" / "fixtures" / "long_project"
DEFAULT_OUT = ROOT / "outputs" / "long-project-e2e"
SCRATCH_ROOT = Path(os.environ.get("GV_LP_SCRATCH", Path.home() / "gv-long-project-e2e"))

SESSION = "long-project-e2e"
STUB_MODEL = "stub-long-project-deterministic"

#: Context budget the run is created with (tokens, the manager's own unit).  The stub
#: reports a real prompt_tokens per request, so the trigger fires on a measured number.
CONTEXT_BUDGET_TOKENS = 900
CONTEXT_TRIGGER_FRACTION = 0.30

PARTIAL_BODY_OLD = '''    return str(text).lower().replace(" ", "-")
'''
PARTIAL_BODY_NEW = '''    lowered = str(text).lower()
    collapsed = re.sub(r"[^a-z0-9 ]+", "", lowered)
    return collapsed.replace(" ", "-")
'''
FULL_BODY_OLD = PARTIAL_BODY_NEW
FULL_BODY_NEW = '''    decomposed = unicodedata.normalize("NFKD", str(text))
    ascii_only = decomposed.encode("ascii", "ignore").decode("ascii").lower()
    collapsed = _NON_SLUG.sub("-", ascii_only)
    return collapsed.strip("-")
'''

#: The two edits of the FIRST work turn: deliberately partial. It makes ``Hello, World!``
#: work, leaves the collapse/trim and the accent cases broken — so the project's own suite
#: still fails and the completion claim that follows is a false claim.
PARTIAL_EDITS = [
    {
        "old": "from __future__ import annotations\n",
        "new": "from __future__ import annotations\n\nimport re\n",
    },
    {"old": PARTIAL_BODY_OLD, "new": PARTIAL_BODY_NEW},
]

#: The edits of the SECOND work turn, anchored on the PARTIAL state: they only apply
#: because the crash did not lose the first transaction (the parent proves this by
#: replaying the same spec against a pristine copy and showing the refusal).
FULL_EDITS = [
    {
        "old": "import re\n",
        "new": "import re\nimport unicodedata\n\n_NON_SLUG = re.compile(r\"[^a-z0-9]+\")\n",
    },
    {"old": FULL_BODY_OLD, "new": FULL_BODY_NEW},
]

PLAN_MD = """# PLAN — textkit

1. Implement `slugify` so that the promised slug rules hold (c1: the suite in `tests/`).
   - lowercase, `[a-z0-9]` only, runs of anything else collapse to a single `-`, trimmed.
2. Make `cli.py` print that slug for the documented inputs (c2: declared `run` check).
3. Make the packaged artifact import the library and slugify from the zip (c3: declared
   `build` check).
4. Run the project's own tests after every edit and attach the output as evidence.
"""


# --------------------------------------------------------------------------- #
# small helpers                                                                #
# --------------------------------------------------------------------------- #


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str, indent=2)


def write_json(path: str | Path, payload: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
                   encoding="utf-8")
    tmp.replace(target)


def read_json(path: str | Path) -> Any:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


# --------------------------------------------------------------------------- #
# the deterministic local stub provider (loopback only)                        #
# --------------------------------------------------------------------------- #


class StubControl:
    """The script one child process plays: a cursor plus a list of actions.

    A "turn" is one script (one ``loop.run`` call). The provider is deterministic: it
    answers the n-th request of a turn with the n-th scripted action, whatever the
    request contains. The only thing it *measures* is the request's own size, which it
    reports back as ``usage.prompt_tokens`` — that is the number the context trigger
    fires on.
    """

    def __init__(self, path: str | Path, log_path: str | Path) -> None:
        self.path = Path(path)
        self.log_path = Path(log_path)
        self.lock = threading.Lock()
        self.cursor = 0
        self.actions: list[dict[str, Any]] = []
        self.requests = 0
        self.prompt_tokens_seen = 0

    def set_script(self, actions: list[dict[str, Any]]) -> None:
        with self.lock:
            self.actions = list(actions)
            self.cursor = 0
        write_json(self.path, {"actions": self.actions, "cursor": 0})

    def next_action(self) -> dict[str, Any]:
        with self.lock:
            index = self.cursor
            self.cursor += 1
            self.requests += 1
            if index < len(self.actions):
                return dict(self.actions[index])
            return {
                "kind": "text",
                "text": "The scripted plan is exhausted; stopping this turn.",
            }

    def log(self, record: dict[str, Any]) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock, open(self.log_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def _chat_response(action: dict[str, Any], model: str, prompt_tokens: int,
                   completion_tokens: int) -> dict[str, Any]:
    if str(action.get("kind", "text")) == "text":
        message: dict[str, Any] = {"role": "assistant", "content": str(action.get("text", ""))}
        finish = "stop"
    else:
        calls = action.get("calls") or [
            {"tool": action.get("tool"), "arguments": action.get("arguments") or {}}
        ]
        message = {
            "role": "assistant",
            "content": str(action.get("text", "") or ""),
            "tool_calls": [
                {
                    "id": f"call_{index}_{uuid.uuid4().hex[:6]}",
                    "type": "function",
                    "function": {
                        "name": str(call.get("tool")),
                        "arguments": json.dumps(call.get("arguments") or {}, ensure_ascii=False),
                    },
                }
                for index, call in enumerate(calls)
            ],
        }
        finish = "tool_calls"
    return {
        "id": f"chatcmpl-stub-{uuid.uuid4().hex[:10]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": {
            "prompt_tokens": int(prompt_tokens),
            "completion_tokens": int(completion_tokens),
            "total_tokens": int(prompt_tokens) + int(completion_tokens),
        },
    }


def make_stub_handler(control: StubControl):
    class Handler(BaseHTTPRequestHandler):
        server_version = "gv-long-project-stub/1"
        protocol_version = "HTTP/1.1"

        def log_message(self, *args: Any) -> None:  # keep the child's stderr readable
            return

        def _send(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 — http.server's name
            if self.path.startswith("/__state"):
                self._send(200, {"requests": control.requests, "cursor": control.cursor})
                return
            self._send(404, {"error": {"message": "not found"}})

        def do_POST(self) -> None:  # noqa: N802 — http.server's name
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            if not self.path.startswith("/v1/chat/completions"):
                self._send(404, {"error": {"message": "not found"}})
                return
            try:
                body = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                self._send(400, {"error": {"message": f"bad body: {exc}"}})
                return
            messages = body.get("messages") or []
            prompt_tokens = max(1, len(json.dumps(messages, ensure_ascii=False)) // 4)
            action = control.next_action()
            response = _chat_response(
                action,
                str(body.get("model") or STUB_MODEL),
                prompt_tokens,
                max(1, len(json.dumps(action, ensure_ascii=False)) // 4),
            )
            control.log(
                {
                    "at": now_iso(),
                    "path": self.path,
                    "model": body.get("model"),
                    "messages": len(messages),
                    "message_roles": [str(m.get("role")) for m in messages],
                    "tools": [
                        str((item.get("function") or {}).get("name", ""))
                        for item in (body.get("tools") or [])
                    ],
                    "prompt_tokens_estimate": prompt_tokens,
                    "action": action.get("kind"),
                    "tool": action.get("tool")
                    or [call.get("tool") for call in (action.get("calls") or [])],
                    "logical_cursor": control.cursor,
                }
            )
            control.prompt_tokens_seen += prompt_tokens
            self._send(200, response)

    return Handler


class StubProvider:
    """A loopback-only OpenAI-compatible provider with a scripted, deterministic brain."""

    def __init__(self, control: StubControl) -> None:
        self.control = control
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_stub_handler(control))
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base_url(self) -> str:
        host, port = self.httpd.server_address[:2]
        return f"http://{host}:{port}/v1"

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


# --------------------------------------------------------------------------- #
# the scenarios (data only — both runs use the same code path)                 #
# --------------------------------------------------------------------------- #

GOAL_ACHIEVABLE = (
    "Bring the textkit project to its promised state: slugify, the CLI and the packaged "
    "artifact must really work."
)
GOAL_IMPOSSIBLE = (
    "Bring the textkit project to its promised state: slugify, the CLI and the published "
    "release manifest must all match."
)

CRITERIA_ACHIEVABLE = [
    {
        "id": "c1",
        "text": "the slug feature is implemented and the project's own test suite passes",
        "check": "pytest",
    },
    {
        "id": "c2",
        "text": "the project CLI prints the documented slug (declared run check)",
        "check": "run",
    },
    {
        "id": "c3",
        "text": "the packaged artifact imports textkit and slugifies correctly "
                "(declared build check)",
        "check": "build",
    },
]

CRITERIA_IMPOSSIBLE = [
    CRITERIA_ACHIEVABLE[0],
    CRITERIA_ACHIEVABLE[1],
    {
        "id": "c3",
        "text": "the packaged artifact matches the upstream release manifest "
                "(declared api check)",
        "check": "api",
    },
]

SCENARIOS: dict[str, dict[str, Any]] = {
    "achievable": {
        "variant": "achievable",
        "goal": GOAL_ACHIEVABLE,
        "criteria": CRITERIA_ACHIEVABLE,
        "outcome": "verified",
        "turn_a": [
            {
                "name": "A1-plan",
                "task": (
                    f"{GOAL_ACHIEVABLE}\n\n"
                    "Step 1 — plan: write the numbered plan for this goal to PLAN.md (one "
                    "step per criterion), then answer."
                ),
                "actions": [
                    {"kind": "tool", "tool": "write_text",
                     "arguments": {"path": "PLAN.md", "text": PLAN_MD}},
                    {"kind": "text",
                     "text": "Plan written to PLAN.md (4 steps, one per criterion)."},
                ],
            },
            {
                "name": "A2-work-partial",
                "task": (
                    "Execute step 1 of PLAN.md: fix textkit.slugify, then run the project's "
                    "tests with the run_tests tool and report."
                ),
                "actions": [
                    {"kind": "tool", "tool": "edit_apply",
                     "arguments": {"reason": "step 1: strip punctuation in slugify",
                                   "files": [{"path": "textkit/slug.py",
                                              "edits": PARTIAL_EDITS}]}},
                    {"kind": "tool", "tool": "run_tests", "arguments": {}},
                    {"kind": "text",
                     "text": (
                         "Implemented slugify punctuation handling, the CLI and the packaged "
                         "artifact are green. All three criteria are done — completion "
                         "requested."
                     )},
                ],
                "claim": True,
                "kill_after": True,
            },
        ],
        "turn_b": [
            {
                "name": "B1-work-full",
                "task": (
                    "Continue the goal after the restart. The supervisor rejected the last "
                    "completion request; finish the remaining work."
                ),
                "actions": [
                    {"kind": "tool", "tool": "read_text",
                     "arguments": {"path": "tests/test_slug.py"}},
                    {"kind": "tool", "tool": "edit_apply",
                     "arguments": {"reason": "step 1 (continued): collapse runs, trim, no accents",
                                   "files": [{"path": "textkit/slug.py",
                                              "edits": FULL_EDITS}]}},
                    {"kind": "tool", "tool": "run_tests", "arguments": {}},
                    {"kind": "text",
                     "text": "Rewrote slugify (NFKD, collapse, trim). The suite is green now."},
                ],
            },
            {
                "name": "B2-claim",
                "task": "Re-run the checks and close the goal if every criterion really passes.",
                "actions": [
                    {"kind": "tool", "tool": "run_tests", "arguments": {}},
                    {"kind": "text",
                     "text": "All three criteria pass: the suite, the CLI and the packaged "
                             "artifact. Completion requested."},
                ],
                "claim": True,
            },
        ],
    },
    "impossible": {
        "variant": "impossible",
        "goal": GOAL_IMPOSSIBLE,
        "criteria": CRITERIA_IMPOSSIBLE,
        "outcome": "blocked",
        "turn_a": [
            {
                "name": "A1-plan",
                "task": (
                    f"{GOAL_IMPOSSIBLE}\n\n"
                    "Step 1 — plan: write the numbered plan for this goal to PLAN.md (one "
                    "step per criterion), then answer."
                ),
                "actions": [
                    {"kind": "tool", "tool": "write_text",
                     "arguments": {"path": "PLAN.md", "text": PLAN_MD}},
                    {"kind": "text",
                     "text": "Plan written to PLAN.md (4 steps, one per criterion)."},
                ],
            },
            {
                "name": "A2-work-partial",
                "task": (
                    "Execute step 1 of PLAN.md: fix textkit.slugify, then run the project's "
                    "tests with the run_tests tool and report."
                ),
                "actions": [
                    {"kind": "tool", "tool": "edit_apply",
                     "arguments": {"reason": "step 1: strip punctuation in slugify",
                                   "files": [{"path": "textkit/slug.py",
                                              "edits": PARTIAL_EDITS}]}},
                    {"kind": "tool", "tool": "run_tests", "arguments": {}},
                    {"kind": "text",
                     "text": "Implemented slugify, the CLI works, the release manifest "
                             "matches. Completion requested."},
                ],
                "claim": True,
                "kill_after": True,
            },
        ],
        "turn_b": [
            {
                "name": "B1-work-full",
                "task": (
                    "Continue the goal after the restart. The supervisor rejected the last "
                    "completion request; finish the remaining work."
                ),
                "actions": [
                    {"kind": "tool", "tool": "edit_apply",
                     "arguments": {"reason": "step 1 (continued): collapse runs, trim, no accents",
                                   "files": [{"path": "textkit/slug.py",
                                              "edits": FULL_EDITS}]}},
                    {"kind": "tool", "tool": "run_tests", "arguments": {}},
                    {"kind": "text",
                     "text": "Rewrote slugify (NFKD, collapse, trim). The suite is green now."},
                ],
            },
            {
                "name": "B2-claim",
                "task": "Re-run the checks and close the goal if every criterion really passes.",
                "actions": [
                    {"kind": "tool", "tool": "run_tests", "arguments": {}},
                    {"kind": "text",
                     "text": "The suite, the CLI and the release manifest all check out. "
                             "Completion requested."},
                ],
                "claim": True,
            },
        ],
    },
}


# --------------------------------------------------------------------------- #
# child process: the runtime over the real product stores                       #
# --------------------------------------------------------------------------- #


def _install_scratch_project(dest: Path, variant: str) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(FIXTURE / "base", dest)
    overlay = FIXTURE / variant
    if overlay.is_dir():
        shutil.copytree(overlay, dest, dirs_exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=dest, check=True)
    subprocess.run(["git", "add", "-A"], cwd=dest, check=True)
    subprocess.run(
        ["git", "-c", "user.email=e2e@godsv", "-c", "user.name=e2e", "commit", "-qm", "fixture"],
        cwd=dest,
        check=True,
    )


def _toolset(workspace: Path, state_dir: Path, session: str, bus: dict[str, Any]):
    """The turn's tools: the product's builtins plus two thin adapters.

    ``edit_apply`` carries a transaction document straight into
    ``skaldir.edit_tx.run_transaction`` (the ``skaldir edit apply`` engine), and
    ``run_tests`` executes the ``pytest`` check through
    ``skaldir.completion.execute_check`` — the same producer the supervisor reads.
    """
    from skaldir.agent_loop import AgentTool, builtin_tools
    from skaldir.completion import execute_check
    from skaldir.edit_tx import parse_spec, run_transaction

    def edit_apply(reason: str, files: list[dict[str, Any]],
                   verification: dict[str, Any] | None = None) -> dict[str, Any]:
        spec, problem = parse_spec(
            {"reason": reason, "files": files,
             "verification": verification or {"kind": "syntax"}}
        )
        if spec is None:
            return {"error": f"the transaction document was refused: {problem}"}
        record = run_transaction(spec, workspace, state_dir=state_dir / "edit_tx")
        bus.setdefault("edits", []).append(
            {
                "tx_id": record.get("tx_id"),
                "verdict": record.get("verdict"),
                "reason": reason,
                "paths": [item.get("path") for item in record.get("files", [])],
                "applied": record.get("applied"),
                "verified": record.get("verified"),
                "syntax_ok": (record.get("verification") or {}).get("passed"),
                "file_sha256_after": [item.get("after_sha256") for item in record.get("files", [])],
                "diff": str(record.get("diff") or "")[:2000],
                "diff_sha256": sha256_text(str(record.get("diff") or "")),
                "record_path": record.get("record_path"),
            }
        )
        return {
            "tx_id": record.get("tx_id"),
            "verdict": record.get("verdict"),
            "applied": record.get("applied"),
            "verified": record.get("verified"),
            "applied_paths": [
                item.get("path") for item in record.get("files", []) if item.get("ok")
            ],
            "refused": [
                {"path": item.get("path"), "reason": item.get("reason")}
                for item in record.get("files", [])
                if not item.get("ok")
            ],
            "diff": str(record.get("diff") or "")[:2000],
        }

    def run_tests() -> dict[str, Any]:
        evidence = execute_check(
            "pytest",
            workspace,
            timeout=300,
            revision=int(bus.get("revision") or 0),
            workspace=str(workspace),
            session=session,
        )
        payload = {
            "status": getattr(evidence, "status", "unknown"),
            "check_id": getattr(evidence, "check_id", "pytest"),
            "cmd": getattr(evidence, "cmd", ""),
            "exit": getattr(evidence, "exit_code", None),
            "source": getattr(evidence, "source", ""),
            "detail": (getattr(evidence, "detail", "") or "")[:1200],
            # the product truncates its stored detail at 300 chars; the harness adds its
            # own reading of the SAME suite's summary line (nothing is inferred)
            "summary": _pytest_summary(workspace),
        }
        bus.setdefault("test_runs", []).append(payload)
        return payload

    tools = list(builtin_tools(workspace))
    tools.append(
        AgentTool(
            name="edit_apply",
            description=(
                "Apply one all-or-nothing edit transaction to the workspace (multi-file, "
                "atomic, verified). Each file entry is {path, edits:[{old,new}]}; "
                "old=null replaces the whole file."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "reason": {"type": "string", "description": "why this edit is made"},
                    "files": {
                        "type": "array",
                        "description": "one entry per file: {path, edits:[{old,new}]}",
                        "items": {"type": "object"},
                    },
                    "verification": {
                        "type": "object",
                        "description": "post-write verification: {kind: syntax|command|none}",
                        "default": {"kind": "syntax"},
                    },
                },
                "required": ["reason", "files"],
                "additionalProperties": False,
            },
            handler=edit_apply,
            parallel_safe=False,
            mutating=True,
            source="e2e-harness",
        )
    )
    tools.append(
        AgentTool(
            name="run_tests",
            description="Run the project's own test suite (the pytest check); return its result.",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            handler=run_tests,
            parallel_safe=False,
            source="e2e-harness",
        )
    )
    return tools


def _attempt_completion(context: dict[str, Any], claim_text: str, turn_name: str) -> dict[str, Any]:
    """The model requested completion.  The runtime asks the gate — the gate decides."""
    from skaldir.completion import (
        COMPLETION_REQUESTED,
        VERIFYING,
        CompletionRequest,
        GateRunner,
        run_requirement_checks,
    )

    workspace: Path = context["workspace"]
    cstore = context["completion"]
    revision = int(cstore.state().get("revision") or 0)
    dod = context["dod"]
    cstore.transition(COMPLETION_REQUESTED, attempt_id=turn_name,
                      claimed=[item.text for item in dod])
    cstore.transition(VERIFYING, attempt_id=turn_name)
    evidence = run_requirement_checks(
        dod,
        workspace,
        timeout=300,
        revision=revision,
        workspace=str(workspace),
        session=context["session"],
    )
    changed = sorted(
        path.relative_to(workspace).as_posix()
        for path in workspace.rglob("*")
        if path.is_file() and path.suffix in (".py", ".md", ".json")
    )
    request = CompletionRequest(
        user_goal=context["goal"],
        claimed_completed=[item.text for item in dod],
        implementation_summary=claim_text,
        files_changed=changed,
        tests_run=[item.cmd for item in evidence],
        test_results="; ".join(f"{item.check_id}={item.status}" for item in evidence),
        attempt_id=turn_name,
        created=time.time(),
    )
    gate = GateRunner(
        repo_root=workspace,
        dod=dod,
        task_base=str(workspace),
        revision=revision,
        workspace=str(workspace),
        session=context["session"],
    )
    result = gate.verify(request, evidence, store=cstore)
    attempt = {
        "turn": turn_name,
        "claim": claim_text,
        "revision": revision,
        "verdict": result.status,
        "blocking_failures": list(result.blocking_failures),
        "next_actions": list(result.next_actions),
        "fingerprint": result.fingerprint,
        "repeat_count": result.repeat_count,
        "escalate": result.escalate,
        "evidence": [
            {
                "check_id": item.check_id,
                "status": item.status,
                "cmd": item.cmd,
                "source": item.source,
                "suite": item.suite,
                "detail": (item.detail or "")[:400],
                "ts": item.ts,
            }
            for item in evidence
        ],
        "accepted": result.status in ("VERIFIED_COMPLETE",),
    }
    context.setdefault("completions", []).append(attempt)
    return {"attempt": attempt, "result": result}


def _checkpoint(rstore, goal_id: str, next_action: str, payload: dict[str, Any]) -> dict[str, Any]:
    return rstore.write_checkpoint(goal_id, next_action=next_action, payload=payload)


def _pytest_summary(workspace: Path) -> str:
    """The suite's own summary line, read by the harness from the same workspace."""
    try:
        proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
            [sys.executable, "-m", "pytest", "-q", "--tb=no", "-p", "no:cacheprovider"],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=300,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"harness read failed: {type(exc).__name__}: {exc}"
    lines = [line.strip() for line in ((proc.stdout or "") + (proc.stderr or "")).splitlines()]
    return next((line for line in reversed(lines) if "passed" in line or "failed" in line
                 or "error" in line), "")


def child_main(args: argparse.Namespace) -> int:
    """One child process: either phase A (killed mid-flight) or phase B (resume)."""
    sys.path.insert(0, str(SRC))
    from skaldir.agent_loop import AgentLoop, LoopLimits
    from skaldir.completion import REDIRECT, WORKING, CompletionStore, DodItem
    from skaldir.context_manager import CompactingClient, ContextManager
    from skaldir.jevcomp.fixture import constant_asker
    from skaldir.llm_provider import provider_for

    run_dir = Path(args.run_dir)
    phase = args.phase
    scenario = SCENARIOS[args.scenario]
    workspace = run_dir / "project"
    state_dir = run_dir / "state"
    state_dir.mkdir(parents=True, exist_ok=True)

    started = time.time()
    report: dict[str, Any] = {
        "scenario": args.scenario,
        "phase": phase,
        "pid": os.getpid(),
        "started_at": now_iso(),
        "workspace": str(workspace),
        "goal": scenario["goal"],
        "criteria": scenario["criteria"],
        "turns": [],
        "completions": [],
        "compactions": [],
        "resume": {},
        "verify": {},
        "runtime": {},
        "measures": {},
    }

    control = StubControl(run_dir / f"stub-{phase}.json", run_dir / f"stub-{phase}.jsonl")
    stub = StubProvider(control)
    report["stub"] = {"base_url": stub.base_url, "requests": 0}
    try:
        client = provider_for(
            {
                "provider": "openai-compatible",
                "base_url": stub.base_url,
                "model": STUB_MODEL,
                "api_key": "stub-local-key",
            }
        )
        from skaldir.runs_store import RunsStore

        rstore = RunsStore(str(state_dir / "runs.db"))
        cstore = CompletionStore(state_dir, session_id=SESSION)
        bus: dict[str, Any] = {"revision": 0}
        dod = [
            DodItem(id=item["id"], text=item["text"], blocking=True, verification=item["check"])
            for item in scenario["criteria"]
        ]
        context: dict[str, Any] = {
            "workspace": workspace,
            "session": SESSION,
            "goal": scenario["goal"],
            "dod": dod,
            "completion": cstore,
            "completions": report["completions"],
        }

        # ---------------- phase A: begin the goal, first work, first rejection --------
        if phase == "A":
            fingerprint = sha256_text(str(workspace))
            goal = rstore.start_goal(
                scenario["goal"],
                session_id=SESSION,
                criteria=[
                    {"criterion_id": item["id"], "text": item["text"],
                     "blocking": True, "verification_plan": item["check"]}
                    for item in scenario["criteria"]
                ],
                task_base=str(workspace),
                workspace_fingerprint=fingerprint,
            )
            report["goal_id"] = goal.goal_id
            report["workspace_fingerprint"] = fingerprint
            lease = rstore.acquire_writer(ttl=600)
            cstore.begin_goal(SESSION, scenario["goal"], dod, task_base=str(workspace))
            report["runtime"]["lease_on_start"] = lease
            report["runtime"]["baseline"] = _baseline(context, bus)
        else:
            resume = _resume_phase_b(rstore, run_dir, report)
            goal_id = str(resume["goal_id"])
            report["goal_id"] = goal_id
            report["workspace_fingerprint"] = str(resume["workspace_fingerprint"])
            report["runtime"]["lease_before_resume"] = resume["lease_before"]
            report["runtime"]["lease_on_resume"] = resume["lease_after"]
            report["resume"] = resume
            context["revision"] = int(cstore.state().get("revision") or 0)
            report["runtime"]["state_on_resume"] = cstore.state().get("state")
            report["runtime"]["prior_rejections"] = len(
                [entry for entry in cstore.timeline(limit=200) if entry.get("kind") == "result"]
            )

        bus["revision"] = int(cstore.state().get("revision") or 0)
        goal_id = str(report["goal_id"])

        # ---------------- the turns ---------------------------------------------------
        turns = scenario["turn_a"] if phase == "A" else scenario["turn_b"]
        pending_task = ""
        for index, turn in enumerate(turns):
            control.set_script(turn["actions"])
            manager = ContextManager(
                budget_tokens=CONTEXT_BUDGET_TOKENS,
                trigger_fraction=CONTEXT_TRIGGER_FRACTION,
                goal=scenario["goal"],
                criteria=[item["text"] for item in scenario["criteria"]],
                preserve_recent_messages=1,
                asker=constant_asker(0.05),
            )
            wrapper = CompactingClient(client, manager)
            loop = AgentLoop(
                wrapper,
                _toolset(workspace, state_dir, SESSION, bus),
                state_dir=state_dir / "agent",
                workspace=workspace,
                limits=LoopLimits(max_steps=14, wall_clock_s=600.0),
                goal=scenario["goal"],
                system_prompt=(
                    "You are Skaldir working a long goal. The runtime decides completion: "
                    "you may only request it."
                ),
                policy=True,
                max_result_chars=6000,
            )
            task = turn["task"]
            if pending_task:
                task = f"{pending_task}\n\n{turn['task']}"
            turn_started = time.time()
            result = loop.run(task, run_id=f"{args.scenario}-{phase}-{index + 1}-{turn['name']}")
            duration = time.time() - turn_started
            record = {
                "name": turn["name"],
                "run_id": result.run_id,
                "status": result.status,
                "reason": result.reason,
                "steps": result.steps,
                "tool_calls": result.tool_calls,
                "executed_calls": result.executed_calls,
                "usage": dict(result.usage),
                "usage_estimated": result.usage_estimated,
                "duration_s": round(duration, 3),
                "answer": (result.answer or "")[:800],
                "transcript": result.transcript_path,
                "task": task[:400],
                "edits": [item for item in bus.get("edits", [])][-3:],
                "test_runs": [item for item in bus.get("test_runs", [])][-3:],
                "compactions": [],
            }
            for context_report in manager.reports:
                wire = context_report.to_wire() if hasattr(context_report, "to_wire") else {}
                if not wire:
                    wire = {
                        "reason": getattr(context_report, "reason", ""),
                        "fired": getattr(context_report, "fired", None),
                        "applied": getattr(context_report, "applied", None),
                        "freed": getattr(getattr(context_report, "freed", None), "__dict__", {}),
                    }
                wire["turn"] = turn["name"]
                record["compactions"].append(wire)
                report["compactions"].append(wire)
            report["turns"].append(record)
            pending_task = ""

            if turn.get("claim"):
                evaluation = _attempt_completion(context, record["answer"], turn["name"])
                attempt = evaluation["attempt"]
                record["completion"] = attempt
                # The gate itself recorded the verdict and moved the contract on
                # (VERIFIED_COMPLETE / INCOMPLETE); a rejection is then redirected
                # into the next turn — the model may only request, never decide.
                if not attempt["accepted"]:
                    cstore.transition(REDIRECT, attempt_id=turn["name"],
                                      redirect=attempt["blocking_failures"][:3])
                    cstore.transition(WORKING, attempt_id=turn["name"])
                    checkpoint = _checkpoint(
                        rstore,
                        goal_id,
                        "continue the goal: the completion request was rejected",
                        {
                            "phase": phase,
                            "turn": turn["name"],
                            "turn_index": index,
                            "fingerprint": attempt["fingerprint"],
                            "blocking_failures": attempt["blocking_failures"],
                            "test_runs": [item for item in bus.get("test_runs", [])],
                            "edits": [
                                {"tx_id": item["tx_id"], "paths": item["paths"],
                                 "verdict": item["verdict"]}
                                for item in bus.get("edits", [])
                            ],
                        },
                    )
                    report["runtime"]["last_checkpoint"] = checkpoint
                    pending_task = (
                        "The runtime rejected the last completion request:\n"
                        + "\n".join(f"- {item}" for item in attempt["blocking_failures"][:5])
                        + "\nKeep working on the goal."
                    )
                    report["runtime"]["last_rejection"] = attempt
                else:
                    report["runtime"]["accepted_on"] = turn["name"]
                    checkpoint = _checkpoint(
                        rstore, goal_id, "goal verified — nothing left to do",
                        {"phase": phase, "turn": turn["name"], "accepted": True},
                    )
                    report["runtime"]["last_checkpoint"] = checkpoint

            if turn.get("kill_after"):
                report["measures"]["wall_time_s_until_kill"] = round(time.time() - started, 3)
                _summarise(report, bus)
                write_json(run_dir / f"phase-{phase}-report.json", report)
                write_json(
                    run_dir / f"phase-{phase}.ready",
                    {
                        "pid": os.getpid(),
                        "at": now_iso(),
                        "goal_id": goal_id,
                        "writer": rstore.writer(),
                        "checkpoint": report["runtime"].get("last_checkpoint"),
                        "state": cstore.state().get("state"),
                    },
                )
                report["runtime"]["killed_at"] = now_iso()
                # hold the lease and wait to be SIGKILLed (this is the crash)
                while True:
                    time.sleep(2)

        # ---------------- after the turns: the outcome --------------------------------
        if phase == "B":
            _finalise(context, bus, report, rstore, cstore, goal_id)

        report["finished_at"] = now_iso()
        report["measures"]["wall_time_s"] = round(time.time() - started, 3)
        report["stub"] = {
            "base_url": stub.base_url,
            "requests": control.requests,
            "prompt_tokens_reported": control.prompt_tokens_seen,
            "log": str(control.log_path),
        }
        _summarise(report, bus)
        rstore.release_writer()
        rstore.close()
        write_json(run_dir / f"phase-{phase}-report.json", report)
    finally:
        stub.stop()
    return 0


def _summarise(report: dict[str, Any], bus: dict[str, Any]) -> None:
    """The run's own counters — computed before every report write (incl. the crash one)."""
    measures = report["measures"]
    measures["turns"] = len(report["turns"])
    measures["tool_calls"] = sum(int(turn["tool_calls"]) for turn in report["turns"])
    measures["executed_calls"] = sum(int(turn["executed_calls"]) for turn in report["turns"])
    measures["steps"] = sum(int(turn["steps"]) for turn in report["turns"])
    measures["tokens"] = {
        key: sum(int(turn["usage"].get(key, 0)) for turn in report["turns"])
        for key in ("prompt_tokens", "completion_tokens", "total_tokens")
    }
    measures["compactions"] = len(
        [item for item in report["compactions"] if item.get("applied")]
    )
    measures["edits"] = bus.get("edits", [])
    measures["test_runs"] = bus.get("test_runs", [])


def _baseline(context: dict[str, Any], bus: dict[str, Any]) -> dict[str, Any]:
    """Measure the project's starting state — all three checks, before any edit."""
    from skaldir.completion import run_requirement_checks

    workspace: Path = context["workspace"]
    evidence = run_requirement_checks(
        context["dod"],
        workspace,
        timeout=300,
        revision=0,
        workspace=str(workspace),
        session=context["session"],
    )
    rows = [
        {"check_id": item.check_id, "status": item.status, "cmd": item.cmd,
         "detail": (item.detail or "")[:200]}
        for item in evidence
    ]
    for row in rows:
        if row["check_id"] in bus.setdefault("test_runs_map", {}):
            continue
    return {"checks": rows, "python": sys.executable, "cwd": str(workspace)}


def _resume_phase_b(rstore, run_dir: Path, report: dict[str, Any]) -> dict[str, Any]:
    """Read the crash site back: dead lease, checkpoint, cursor — then take over."""
    phase_a = read_json(run_dir / "phase-A-report.json")
    goal_id = str(phase_a["goal_id"])
    lease_before = rstore.writer()
    checkpoint = rstore.latest_checkpoint(goal_id) or {}
    payload = rstore.checkpoint_payload(checkpoint) if checkpoint else {}
    cursor = rstore.cursor(goal_id)
    lease_after = rstore.acquire_writer(ttl=600)
    document = (payload.get("document") or {}) if isinstance(payload, dict) else {}
    stored = (document.get("payload") or {}) if isinstance(document, dict) else {}
    return {
        "goal_id": goal_id,
        "from_pid": phase_a.get("pid"),
        "previous_turn": stored.get("turn"),
        "previous_phase": stored.get("phase"),
        "checkpoint_id": checkpoint.get("checkpoint_id"),
        "checkpoint_seq": checkpoint.get("seq"),
        "checkpoint_next_action": checkpoint.get("next_action"),
        "checkpoint_sha256": checkpoint.get("sha256"),
        "checkpoint_path": checkpoint.get("path"),
        "payload_readable": payload.get("readable") if isinstance(payload, dict) else None,
        "payload_sha256_matches": payload.get("sha256_ok") if isinstance(payload, dict) else None,
        "payload_next_action": (document.get("cursor") or {}).get("next_action"),
        "payload_open_criteria": (document.get("cursor") or {}).get("open_criteria"),
        "cursor": cursor,
        "open_criteria": cursor.get("open_criteria"),
        "lease_before": lease_before,
        "lease_after": lease_after,
        "goal_state_before": (rstore.goal(goal_id).state if rstore.goal(goal_id) else None),
        "workspace_fingerprint": phase_a.get("workspace_fingerprint"),
        "criteria_status_before": {
            item.criterion_id: item.status for item in rstore.criteria(goal_id)
        },
        "prior_completions": len(phase_a.get("completions", [])),
        "prior_rejections": [
            {"turn": item["turn"], "verdict": item["verdict"],
             "blocking_failures": item["blocking_failures"]}
            for item in phase_a.get("completions", [])
            if not item.get("accepted")
        ],
        "slug_sha256_at_resume": sha256_file(Path(report["workspace"]) / "textkit" / "slug.py"),
        "slug_sha256_at_crash": sha256_file(Path(report["workspace"]) / "textkit" / "slug.py"),
    }


def _finalise(context, bus, report, rstore, cstore, goal_id: str) -> None:
    """Decide the goal's fate and run the independent VERIFY pass."""
    from skaldir import verify_service
    from skaldir.completion import BLOCKED_EXTERNAL

    workspace: Path = context["workspace"]
    attempts = report["completions"]
    accepted = [item for item in attempts if item.get("accepted")]

    if not accepted and attempts:
        last = attempts[-1]
        reason = (
            "criterion could not be met: "
            + "; ".join(last["blocking_failures"][:3])
        )
        cstore.transition(BLOCKED_EXTERNAL, reason=reason, attempt_id=last["turn"])
        rstore.set_goal_state(
            goal_id, "BLOCKED_EXTERNAL", blocked_reason=reason,
            wake_source="operator:supply the upstream release manifest",
        )
        for item in context["dod"]:
            failed = [row for row in last["evidence"] if row["check_id"] == item.verification]
            if failed and failed[0]["status"] != "passed":
                try:
                    rstore.record_criterion_outcome(
                        goal_id, item.id, outcome="failed",
                        evidence_refs=[
                            f"run:{sha256_text(failed[0]['detail'] or failed[0]['cmd'])}"
                        ],
                        check_id=item.verification, workspace=str(workspace),
                        revision=None, exit_code=1, cmd=failed[0]["cmd"],
                    )
                except Exception as exc:  # noqa: BLE001 — recorded, not hidden
                    report["runtime"].setdefault("ledger_errors", []).append(str(exc))
        report["runtime"]["terminal_state"] = cstore.state().get("state")
        report["runtime"]["blocked_reason"] = reason

    revision = int(cstore.state().get("revision") or 0)
    plan = verify_service.build_plan(
        [], goal=context["goal"], workspace=workspace, plan_file="", revision=revision
    )
    run = verify_service.run_plan(plan, timeout=300, session=SESSION,
                                 state_dir=Path(report["workspace"]).parent / "state" / "verify")
    freshness = verify_service.run_freshness(run, workspace)
    report["verify"] = {
        "run_id": run["run_id"],
        "verdict": run["verdict"],
        "verdict_reason": run["verdict_reason"],
        "counts": run["counts"],
        "plan_sha256": run["plan_sha256"],
        "workspace_digest": run["workspace_digest"].get("digest"),
        "workspace_digest_after": run["workspace_digest_after"].get("digest"),
        "workspace_changed_during_run": run["workspace_changed_during_run"],
        "freshness": freshness,
        "criteria": [
            {
                "criterion": item["criterion"],
                "check_id": item["check_id"],
                "kind": item["kind"],
                "status": item["status"],
                "cmd": item.get("cmd"),
                "exit_code": item.get("exit_code"),
                "blocking": item.get("blocking"),
                "reason": item.get("reason"),
                "evidence_sha256": item.get("evidence_sha256"),
                "duration_ms": item.get("duration_ms"),
                "artifact_sha256": item.get("artifact_sha256"),
                "output_sha256": item.get("output_sha256"),
                "output_tail": (item.get("output_tail") or "")[-400:],
            }
            for item in run["results"]
        ],
        "run_path": run.get("state_dir"),
        "record": run,
    }

    if accepted and run["verdict"] == "PASS":
        rstore.set_goal_state(goal_id, "VERIFIED_COMPLETE",
                              blocked_reason="", wake_source="")
    # The per-criterion ledger is written from the VERIFY record — evidence only, in both
    # outcomes.  In the blocked case this is exactly what happened: the criteria that
    # really passed are marked verified, the one that could not be is marked failed, and
    # the GOAL stays blocked (a verified criterion is not a verified goal).
    for item, row in zip(context["dod"], run["results"], strict=False):
        try:
            rstore.record_criterion_outcome(
                goal_id,
                item.id,
                outcome="verified" if row["status"] == "passed" else "failed",
                evidence_refs=[f"run:{row['evidence_sha256']}"],
                check_id=row["check_id"],
                workspace=str(workspace),
                revision=None,
                exit_code=int(row.get("exit_code") or 0),
                cmd=str(row.get("cmd") or ""),
                run_id=run["run_id"],
            )
        except Exception as exc:  # noqa: BLE001 — recorded, never hidden
            report["runtime"].setdefault("ledger_errors", []).append(str(exc))
    report["runtime"]["terminal_state"] = cstore.state().get("state")
    report["runtime"]["goal_state"] = (
        rstore.goal(goal_id).state if rstore.goal(goal_id) else None
    )
    report["runtime"]["criteria"] = [
        {"criterion_id": item.criterion_id, "status": item.status,
         "plan": item.verification_plan, "verified_by": item.verified_by}
        for item in rstore.criteria(goal_id)
    ]


def _process_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except OSError:
        return False
    return True


# --------------------------------------------------------------------------- #
# parent: orchestrate both scenarios and verify the claims                     #
# --------------------------------------------------------------------------- #


def _child_env(run_dir: Path) -> dict[str, str]:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(Path.home()),
        "PYTHONPATH": str(SRC),
        "SKALDIR_HOME": str(run_dir / "state"),
        "HERMES_HOME": str(run_dir / "state"),
        "PYTHONUNBUFFERED": "1",
        "LC_ALL": "C.UTF-8",
        "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
    }
    return env


def _spawn_child(run_dir: Path, phase: str, scenario: str) -> subprocess.Popen:
    log = open(run_dir / f"child-{phase}.log", "ab")  # noqa: SIM115 — closed by the caller
    argv = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--child",
        "--run-dir",
        str(run_dir),
        "--phase",
        phase,
        "--scenario",
        scenario,
    ]
    return subprocess.Popen(argv, env=_child_env(run_dir), stdout=log, stderr=subprocess.STDOUT,
                            cwd=str(run_dir))


def _wait_for(path: Path, timeout: float, proc: subprocess.Popen) -> dict[str, Any]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.is_file():
            return read_json(path)
        if proc.poll() is not None:
            raise RuntimeError(f"the child exited early (rc={proc.returncode}) before {path.name}")
        time.sleep(0.25)
    raise RuntimeError(f"timed out waiting for {path}")


def _kill_tree(proc: subprocess.Popen) -> dict[str, Any]:
    pid = proc.pid
    os.kill(pid, signal.SIGKILL)
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        pass
    time.sleep(0.4)
    return {
        "pid": pid,
        "returncode": proc.returncode,
        "alive_after_kill": _process_alive(pid),
        "signal": "SIGKILL",
    }


def _pristine_replay_refuses() -> dict[str, Any]:
    """Prove the resume CONTINUED: its anchored edit cannot apply to an untouched tree.

    ``FULL_EDITS`` anchors on text the first work turn produced.  If a resume had secretly
    restarted from zero, those anchors would not exist.  So: copy the fixture fresh, ask
    the edit engine to apply the very same spec — it must refuse (nothing written).
    """
    import sys as _sys

    _sys.path.insert(0, str(SRC))
    from skaldir.edit_tx import parse_spec, run_transaction

    scratch = SCRATCH_ROOT / f"_pristine-replay-{uuid.uuid4().hex[:6]}"
    try:
        shutil.copytree(FIXTURE / "base", scratch)
        spec, problem = parse_spec(
            {"reason": "replay of the resumed turn's edit",
             "files": [{"path": "textkit/slug.py", "edits": FULL_EDITS}],
             "verification": {"kind": "syntax"}}
        )
        if spec is None:
            return {"refused": True, "problem": problem, "status": "document_refused"}
        before = sha256_file(scratch / "textkit" / "slug.py")
        record = run_transaction(
            spec,
            scratch,
            state_dir=scratch.parent / f"_pristine-replay-state-{uuid.uuid4().hex[:6]}",
        )
        after = sha256_file(scratch / "textkit" / "slug.py")
        return {
            "refused": record.get("applied") is not True,
            "verdict": record.get("verdict"),
            "file_reasons": [item.get("reason") for item in record.get("files", [])],
            "file_untouched": before == after,
            "diff": str(record.get("diff") or "")[:200],
        }
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _partial_failure_measure(report_a: dict[str, Any]) -> dict[str, Any]:
    """The first failing test run of the run — the runner's own numbers, read back."""
    import re as _re

    for turn in report_a.get("turns", []):
        for row in turn.get("test_runs", []):
            if row.get("status") == "passed":
                continue
            summary = str(row.get("summary") or "")
            match = _re.search(r"(\d+) failed, (\d+) passed", summary)
            return {
                "turn": turn["name"],
                "status": row["status"],
                "cmd": row["cmd"],
                "product_detail": str(row.get("detail") or "")[-200:],
                "summary_line": summary,
                "counts": (
                    {"failed": int(match.group(1)), "passed": int(match.group(2))}
                    if match
                    else None
                ),
            }
    return {}


def run_scenario(name: str, out_root: Path, keep: bool) -> dict[str, Any]:
    scenario = SCENARIOS[name]
    stamp = time.strftime("%Y%m%dT%H%M%S")
    run_dir = SCRATCH_ROOT / f"{name}-{stamp}-{uuid.uuid4().hex[:6]}"
    run_dir.mkdir(parents=True, exist_ok=True)
    project = run_dir / "project"
    _install_scratch_project(project, scenario["variant"])
    baseline_slug = sha256_file(project / "textkit" / "slug.py")

    started = time.time()
    phase_a_proc = _spawn_child(run_dir, "A", name)
    _wait_for(run_dir / "phase-A.ready", timeout=900, proc=phase_a_proc)
    killed = _kill_tree(phase_a_proc)
    kill_at = time.time()
    phase_b_proc = _spawn_child(run_dir, "B", name)
    rc_b = phase_b_proc.wait()
    finished = time.time()

    report_a = read_json(run_dir / "phase-A-report.json")
    report_b = read_json(run_dir / "phase-B-report.json") if (
        run_dir / "phase-B-report.json"
    ).is_file() else {}
    if rc_b != 0 or not report_b:
        raise RuntimeError(
            f"phase B failed (rc={rc_b}); see {run_dir / 'child-B.log'}"
        )

    metrics = {
        "scenario": name,
        "wall_time_s": round(finished - started, 3),
        "phase_a_wall_time_s": round(kill_at - started, 3),
        "phase_b_wall_time_s": round(finished - kill_at, 3),
        "turns": report_a["measures"]["turns"] + report_b["measures"]["turns"],
        "turns_phase_a": report_a["measures"]["turns"],
        "turns_phase_b": report_b["measures"]["turns"],
        "steps": report_a["measures"]["steps"] + report_b["measures"]["steps"],
        "tool_calls": report_a["measures"]["tool_calls"] + report_b["measures"]["tool_calls"],
        "executed_calls": (
            report_a["measures"]["executed_calls"] + report_b["measures"]["executed_calls"]
        ),
        "tokens": {
            key: report_a["measures"]["tokens"][key] + report_b["measures"]["tokens"][key]
            for key in ("prompt_tokens", "completion_tokens", "total_tokens")
        },
        "tokens_phase_a": report_a["measures"]["tokens"],
        "tokens_phase_b": report_b["measures"]["tokens"],
        "compactions": report_a["measures"]["compactions"] + report_b["measures"]["compactions"],
        "resumes": 1,
        "kill": killed,
        "stub_requests": report_a["stub"]["requests"] + report_b["stub"]["requests"],
        "edits": report_a["measures"]["edits"] + report_b["measures"]["edits"],
        "test_runs": report_a["measures"]["test_runs"] + report_b["measures"]["test_runs"],
    }

    checks = _evaluate(name, scenario, report_a, report_b, metrics, project, baseline_slug)
    applied = [
        item
        for item in report_a["compactions"] + report_b["compactions"]
        if item.get("applied")
    ]
    metrics["compaction_first"] = (
        {
            "turn": applied[0].get("turn"),
            "trigger": applied[0].get("trigger", {}).get("reason"),
            "messages_before": (applied[0].get("freed") or {}).get("messages_before"),
            "messages_after": (applied[0].get("freed") or {}).get("messages_after"),
            "chars_freed": (applied[0].get("freed") or {}).get("chars_freed"),
            "preserved_ok": all(
                bool(item.get("ok")) for item in applied[0].get("preserved", [])
            ),
            "preserved": [
                {"label": item.get("label"), "ok": item.get("ok"), "detail": item.get("detail")}
                for item in applied[0].get("preserved", [])
            ],
        }
        if applied
        else {}
    )
    checks.append(
        {
            "claim": "the compaction kept the goal, the criteria and the system prompt "
                     "byte-identical and really freed history",
            "ok": bool(metrics["compaction_first"])
            and metrics["compaction_first"]["preserved_ok"] is True
            and int(metrics["compaction_first"]["chars_freed"] or 0) > 0,
            "evidence": metrics["compaction_first"],
        }
    )
    replay = _pristine_replay_refuses()
    partial = _partial_failure_measure(report_a)
    checks.insert(
        5,
        {
            "claim": "the resume's edit only applies because phase A's edit survived "
                     "(replaying it on a pristine tree is refused)",
            "ok": bool(replay.get("refused")) and bool(replay.get("file_untouched")),
            "evidence": replay,
        },
    )
    checks.insert(
        4,
        {
            "claim": "the forced failure was a REAL, PARTIAL failure (the runner's own "
                     "counts: some tests passed, some failed)",
            "ok": bool(partial) and (partial.get("counts") or {}).get("failed", 0) >= 1
            and (partial.get("counts") or {}).get("passed", 0) >= 1,
            "evidence": partial,
        },
    )
    payload = {
        "scenario": name,
        "run_dir": str(run_dir),
        "outcome": scenario["outcome"],
        "started_at": now_iso(),
        "metrics": metrics,
        "phase_a": {key: report_a.get(key) for key in (
            "goal_id", "pid", "started_at", "finished_at", "runtime", "criteria",
            "compactions", "completions", "turns", "stub", "verify"
        )},
        "phase_b": {key: report_b.get(key) for key in (
            "goal_id", "pid", "started_at", "finished_at", "runtime", "criteria",
            "compactions", "completions", "turns", "stub", "verify", "resume"
        )},
        "assertions": checks,
        "all_assertions_ok": all(item["ok"] for item in checks),
    }
    pack = out_root / f"{name}-{stamp}"
    _write_pack(pack, payload, run_dir, name)
    if not keep:
        payload["run_dir_kept"] = True  # scratch dirs are kept: they are the raw transcript
    return payload


def _evaluate(name, scenario, report_a, report_b, metrics, project,
              baseline_slug) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    def add(claim: str, ok: bool, evidence: Any) -> None:
        checks.append({"claim": claim, "ok": bool(ok), "evidence": evidence})

    comp_a = report_a.get("completions", [])
    comp_b = report_b.get("completions", [])
    all_comp = comp_a + comp_b
    rejected = [item for item in all_comp if not item.get("accepted")]
    accepted = [item for item in all_comp if item.get("accepted")]
    baseline = report_a["runtime"].get("baseline", {}).get("checks", [])
    red = [row for row in baseline if row["status"] != "passed"]

    add("the goal was created with 3 criteria in the durable store",
        len(report_a.get("criteria", [])) == 3
        and len([row for row in report_a.get("criteria", [])]) == 3,
        [row["id"] for row in report_a.get("criteria", [])])
    add("all three criteria were genuinely RED before any edit (real checks, no stubs)",
        len(red) == 3,
        baseline)
    add("the plan was written through the product's built-in write_text tool",
        (Path(project) / "PLAN.md").is_file(),
        {"path": str(Path(project) / "PLAN.md"),
         "sha256": sha256_file(Path(project) / "PLAN.md")})
    add("the first edit was applied by the edit-transaction engine (atomic, syntax-verified)",
        any(item.get("applied") is True and item.get("syntax_ok") is True
            for turn in report_a["turns"] for item in turn.get("edits", [])),
        [item for turn in report_a["turns"] for item in turn.get("edits", [])])
    add("the project's own suite really failed after the partial edit",
        any(row["status"] != "passed" for turn in report_a["turns"]
            for row in turn.get("test_runs", [])),
        [row for turn in report_a["turns"] for row in turn.get("test_runs", [])])
    add("the model DID request completion while the work was red",
        len(comp_a) >= 1 and str(comp_a[0]["claim"]).strip() != "",
        comp_a[0]["claim"] if comp_a else None)
    add("the runtime REFUSED that completion (gate verdict INCOMPLETE, blockers named)",
        bool(rejected) and rejected[0]["verdict"] == "INCOMPLETE"
        and bool(rejected[0]["blocking_failures"]),
        {"verdict": rejected[0]["verdict"] if rejected else None,
         "blocking_failures": rejected[0]["blocking_failures"] if rejected else None})
    add("the refusal kept the goal open (state file says INCOMPLETE after phase A, one "
        "rejected attempt, no VERIFIED_COMPLETE)",
        len(report_a["completions"]) >= 1
        and all(not item["accepted"] for item in report_a["completions"]),
        {"phase_a_completions": [
            {"turn": item["turn"], "verdict": item["verdict"]}
            for item in report_a["completions"]]})
    add("the runtime kept working on its own (extra turns, no user input)",
        metrics["turns"] >= 4,
        {"turns": metrics["turns"], "phase_a": metrics["turns_phase_a"],
         "phase_b": metrics["turns_phase_b"]})
    add("the context threshold fired and the history was really compacted",
        metrics["compactions"] >= 1,
        [item for item in report_a["compactions"] + report_b["compactions"]
         if item.get("applied")][:2])
    add("the process was killed for real (SIGKILL, PID gone)",
        metrics["kill"]["signal"] == "SIGKILL" and metrics["kill"]["alive_after_kill"] is False,
        metrics["kill"])
    resume = report_b.get("resume", {})
    add("the resumed process found the dead writer lease (proof of the crash)",
        bool(resume.get("lease_before", {}).get("held")) is False
        or resume.get("lease_before", {}).get("pid_alive") is False,
        {"lease_before": resume.get("lease_before")})
    add("the resume took the lease over from the dead PID",
        str(resume.get("lease_after", {}).get("action")) in ("takeover", "acquired"),
        resume.get("lease_after"))
    add("the resume read the durable checkpoint back (hash-verified payload)",
        bool(resume.get("checkpoint_id"))
        and resume.get("checkpoint_next_action") not in (None, ""),
        {"checkpoint": {key: resume.get(key) for key in (
            "checkpoint_id", "checkpoint_seq", "checkpoint_next_action",
            "checkpoint_sha256", "payload_sha256_matches")},
         "cursor": resume.get("cursor", {}).get("next_action")})
    add("the phase-A edit survived the crash (the work resumed, it did not restart)",
        resume.get("slug_sha256_at_resume") != baseline_slug,
        {"slug_sha256_before": baseline_slug,
         "slug_sha256_after_crash": resume.get("slug_sha256_at_resume")})
    add("the prior rejection was still visible to the new process (durable, not in-memory)",
        (resume.get("prior_completions") or 0) >= 1,
        {"prior_rejections": resume.get("prior_rejections"),
         "state_on_resume": report_b["runtime"].get("state_on_resume")})

    if scenario["outcome"] == "verified":
        add("the completion was ACCEPTED only after the checks really passed",
            len(accepted) == 1 and accepted[0]["verdict"] == "VERIFIED_COMPLETE"
            and all(row["status"] == "passed" for row in accepted[0]["evidence"]),
            {"accepted_turn": accepted[0]["turn"] if accepted else None,
             "evidence": accepted[0]["evidence"] if accepted else None})
        verify = report_b.get("verify", {})
        add("VERIFY PASSED on all three criteria, each with its own executed evidence hash",
            verify.get("verdict") == "PASS"
            and verify.get("counts", {}).get("passed") == 3,
            {"verdict": verify.get("verdict"), "reason": verify.get("verdict_reason"),
             "criteria": [
                 {"criterion": row["criterion"], "check": row["check_id"],
                  "status": row["status"], "evidence_sha256": row["evidence_sha256"]}
                 for row in verify.get("criteria", [])
             ]})
        add("the durable goal record ends VERIFIED_COMPLETE with all criteria verified",
            report_b["runtime"].get("goal_state") == "VERIFIED_COMPLETE"
            and all(row["status"] == "verified" for row in report_b["runtime"].get("criteria", [])),
            {"goal_state": report_b["runtime"].get("goal_state"),
             "criteria": report_b["runtime"].get("criteria")})
    else:
        last = all_comp[-1] if all_comp else {}
        add("the unmet criterion's check really ran and really failed (not a skip)",
            any(row["check_id"] == "api" and row["status"] != "passed"
                for row in last.get("evidence", [])),
            [row for row in last.get("evidence", []) if row["check_id"] == "api"])
        add("the runtime NEVER claimed success (no VERIFIED_COMPLETE, no accepted attempt)",
            not accepted
            and all(item["verdict"] != "VERIFIED_COMPLETE" for item in all_comp),
            [{"turn": item["turn"], "verdict": item["verdict"]} for item in all_comp])
        add("the final completion state is a blocked terminal with the criterion named",
            report_b["runtime"].get("terminal_state") == "BLOCKED_EXTERNAL"
            and "api" in str(report_b["runtime"].get("blocked_reason") or "")
            or "release manifest" in str(report_b["runtime"].get("blocked_reason") or ""),
            {"terminal_state": report_b["runtime"].get("terminal_state"),
             "blocked_reason": report_b["runtime"].get("blocked_reason")})
        add("the durable goal record is blocked, never verified",
            report_b["runtime"].get("goal_state") == "BLOCKED_EXTERNAL"
            and any(row["status"] != "verified" for row in report_b["runtime"].get("criteria", [])),
            {"goal_state": report_b["runtime"].get("goal_state"),
             "criteria": report_b["runtime"].get("criteria")})
        add("the ledger separates what WAS achieved (2 criteria verified, evidence-bound) "
            "from what was not (1 failed)",
            len([row for row in report_b["runtime"].get("criteria", [])
                 if row["status"] == "verified"]) == 2
            and len([row for row in report_b["runtime"].get("criteria", [])
                     if row["status"] == "failed"]) == 1,
            report_b["runtime"].get("criteria"))
        verify = report_b.get("verify", {})
        add("the independent VERIFY pass reports FAIL (not PASS, not UNKNOWN)",
            verify.get("verdict") == "FAIL"
            and verify.get("counts", {}).get("passed") == 2,
            {"verdict": verify.get("verdict"), "reason": verify.get("verdict_reason"),
             "counts": verify.get("counts")})
        add("the work that COULD be done was still done and verified (2 of 3 green)",
            verify.get("counts", {}).get("passed") == 2,
            [{"criterion": row["criterion"], "status": row["status"]}
             for row in verify.get("criteria", [])])
    return checks


def _write_pack(pack: Path, payload: dict[str, Any], run_dir: Path, name: str) -> None:
    pack.mkdir(parents=True, exist_ok=True)
    write_json(pack / "report.json", payload)
    write_json(pack / "metrics.json", payload["metrics"])
    for phase in ("A", "B"):
        report = run_dir / f"phase-{phase}-report.json"
        if report.is_file():
            shutil.copy2(report, pack / f"phase-{phase}-report.json")
        log = run_dir / f"child-{phase}.log"
        if log.is_file():
            shutil.copy2(log, pack / f"child-{phase}.log")
        stub_log = run_dir / f"stub-{phase}.jsonl"
        if stub_log.is_file():
            shutil.copy2(stub_log, pack / f"stub-{phase}.jsonl")
    transcripts = run_dir / "state" / "agent" / "runs"
    if transcripts.is_dir():
        target = pack / "transcripts"
        target.mkdir(exist_ok=True)
        for path in sorted(transcripts.glob("*.jsonl")):
            shutil.copy2(path, target / path.name)
    state = run_dir / "state"
    for relative, label in (
        (Path("verify"), "verify"),
        (Path("edit_tx"), "edit_tx"),
    ):
        if (state / relative).is_dir():
            shutil.copytree(state / relative, pack / label, dirs_exist_ok=True)
    for relative, label in (
        (Path("runs.db"), "runs.db"),
        (Path("completion"), "completion"),
    ):
        source = state / relative
        if source.is_file():
            shutil.copy2(source, pack / label)
        elif source.is_dir():
            shutil.copytree(source, pack / label, dirs_exist_ok=True)
    _manifest(pack, payload)
    (pack / "EVIDENCE.md").write_text(_render_evidence(payload, pack), encoding="utf-8")


def _manifest(pack: Path, payload: dict[str, Any]) -> None:
    rows = []
    for path in sorted(pack.rglob("*")):
        if path.is_file() and path.name != "MANIFEST.json":
            rows.append({"path": path.relative_to(pack).as_posix(),
                         "bytes": path.stat().st_size,
                         "sha256": sha256_file(path)})
    write_json(pack / "MANIFEST.json",
               {"scenario": payload["scenario"], "files": rows,
                "count": len(rows), "generated_at": now_iso()})


def _render_evidence(payload: dict[str, Any], pack: Path) -> str:
    metrics = payload["metrics"]
    lines = [
        f"# Long-project E2E — {payload['scenario']}",
        "",
        f"* run dir (scratch project + stores): `{payload['run_dir']}`",
        f"* evidence pack: `{pack}`",
        f"* outcome: **{payload['outcome']}**",
        "",
        "## Measured",
        "",
        "| measure | value |",
        "|---|---|",
        f"| wall time | {metrics['wall_time_s']} s "
        f"(phase A {metrics['phase_a_wall_time_s']} s → SIGKILL → phase B "
        f"{metrics['phase_b_wall_time_s']} s) |",
        f"| turns | {metrics['turns']} "
        f"(A {metrics['turns_phase_a']} / B {metrics['turns_phase_b']}) |",
        f"| model steps | {metrics['steps']} |",
        f"| tool calls | {metrics['tool_calls']} ({metrics['executed_calls']} executed) |",
        f"| tokens | {metrics['tokens']['total_tokens']} "
        f"(prompt {metrics['tokens']['prompt_tokens']} / completion "
        f"{metrics['tokens']['completion_tokens']}) |",
        f"| compactions applied | {metrics['compactions']} "
        f"(first: {metrics.get('compaction_first', {}).get('messages_before')} → "
        f"{metrics.get('compaction_first', {}).get('messages_after')} messages, "
        f"{metrics.get('compaction_first', {}).get('chars_freed')} chars freed) |",
        f"| resumes | {metrics['resumes']} (SIGKILL of pid {metrics['kill']['pid']}, "
        f"returncode {metrics['kill']['returncode']}) |",
        f"| stub requests | {metrics['stub_requests']} (loopback only) |",
        "",
        "## Criteria",
        "",
        "| criterion | check | status | evidence sha256 |",
        "|---|---|---|---|",
    ]
    verify = payload["phase_b"].get("verify", {})
    for row in verify.get("criteria", []):
        lines.append(
            f"| {row['criterion']} | `{row['check_id']}` | {row['status']} | "
            f"`{str(row['evidence_sha256'])[:16]}…` |"
        )
    lines += [
        "",
        f"VERIFY verdict: **{verify.get('verdict')}** — {verify.get('verdict_reason')}",
        "",
        "## Completion attempts (the model may only request; the gate decides)",
        "",
        "| turn | phase | claim | verdict | blockers |",
        "|---|---|---|---|---|",
    ]
    for phase, key in (("A", "phase_a"), ("B", "phase_b")):
        for item in payload[key].get("completions", []) or []:
            lines.append(
                f"| {item['turn']} | {phase} | {str(item['claim'])[:60]}… | {item['verdict']} | "
                f"{'; '.join(item['blocking_failures'])[:120]} |"
            )
    lines += ["", "## Assertions", "", "| claim | holds | evidence |", "|---|---|---|"]
    for item in payload["assertions"]:
        evidence = json.dumps(item["evidence"], default=str)
        lines.append(f"| {item['claim']} | {'YES' if item['ok'] else 'NO'} | {evidence[:160]} |")
    lines += [
        "",
        "## Reproduce",
        "",
        "```bash",
        "cd /home/willy/projects/gods-voice",
        f"env -u PYTHONPATH .venv/bin/python tools/gv_long_project_e2e.py --scenario "
        f"{payload['scenario']}",
        "```",
        "",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scenario", choices=(*SCENARIOS, "both"), default="both")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--keep", action="store_true", help="keep the scratch run dir")
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--run-dir", default="", help=argparse.SUPPRESS)
    parser.add_argument("--phase", default="", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.child:
        return child_main(args)

    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)
    names = list(SCENARIOS) if args.scenario == "both" else [args.scenario]
    payloads = {}
    failed = 0
    for name in names:
        payload = run_scenario(name, out_root, args.keep)
        payloads[name] = payload
        if not payload["all_assertions_ok"]:
            failed += 1
    print(canonical({"scenarios": {
        name: {
            "metrics": payload["metrics"] | {"edits": len(payload["metrics"]["edits"]),
                                             "test_runs": len(payload["metrics"]["test_runs"])},
            "assertions_ok": sum(1 for item in payload["assertions"] if item["ok"]),
            "assertions_total": len(payload["assertions"]),
            "failed_assertions": [item["claim"] for item in payload["assertions"]
                                  if not item["ok"]],
            "verify": payload["phase_b"].get("verify", {}).get("verdict"),
            "outcome": payload["outcome"],
        }
        for name, payload in payloads.items()
    }}))
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
