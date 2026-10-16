"""AGENT toolset — every Skaldir layer, wrapped once, exposed to the AgentLoop.

The tool hand-off: layer services (EXPAND builds, ACT runs declared actions,
COMPOSE validates chains, VERIFY judges real workspaces) become the loop's
tools, next to the builtin file tools and the registry tool runtime, so a goal
run (``skaldir agent run``) can reach all 8 layers by name.

Nothing here invents evidence. Each wrapper is a thin, typed, static function
over an existing service: a refusal stays a refusal, an UNKNOWN verdict
returns ``ok=False`` — "unknown" is not a success — and build failures are
honest tool errors, never half-built tools.

Sources of truth (all existing modules of this package, no shared file touched):

* builtin file tools — :func:`skaldir.agent_loop.builtin_tools`
* registry tools    — :func:`skaldir.agent_loop.registry_tools` (the tool
  runtime of approved specs, sandboxed by :mod:`skaldir.mcpserver`)
* EXPAND            — :class:`skaldir.toolbuilder.ToolBuilder` with real
  builds (``run(False)`` discipline: the builder never falls back to a draft
  fixture; a build without source and without a configured client is a
  blocked result naming exactly that)
* ACT               — :func:`skaldir.act_service.run_action_list` +
  :func:`skaldir.act_service.capability_report` (declared actions only; the
  dry-run / sandbox / container runners — ``auto`` never falls back to the
  in-process fixture, and the host's own honesty — the ACT check — is a tool
  of its own)
* COMPOSE           — :func:`skaldir.compose_service.validate_composite`
  over a parsed :class:`skaldir.compose_service.CompositeSpec` (the already
  validated compose path — no ``step_runners`` wired here)
* VERIFY            — :mod:`skaldir.verify_service` (exact ``PASS`` /
  ``FAIL`` / ``UNKNOWN``; UNKNOWN returns ``ok=False``) and
  :func:`skaldir.verify_flows.run_flows` (declared user flows)

Environment knobs (read through :mod:`skaldir.env`, so the legacy ``GODSV_*``
twins keep working; all three default to reasonable values):

* ``SKALDIR_TOOLBUILDER_TOPN`` — how many compose-first candidates EXPAND
  reports (default ``5``);
* ``SKALDIR_CU_SANDBOX`` — the ACT runner kind used when the caller does not
  name one and ``auto`` cannot resolve a real bridge (default ``sandbox``,
  i.e. CLI-only, bwrap-fenced);
* ``SKALDIR_ACT_POLICIES`` — extra comma-separated action kinds the policy
  may allow on top of the Computer Use default (default: none).

No dependency outside the standard library and this package.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .agent_loop import AgentLoopError, AgentTool, ToolSet
from .compose_service import parse_composite, save_composite, validate_composite
from .env import getenv
from .toolbuilder import ToolBuilder, ToolBuildRequest
from .tools import ToolStore
from .verify_flows import FlowStore, load_flows, run_flows
from .verify_service import VERDICT_PASS, build_plan, contract_criteria, run_plan

__all__ = [
    "DEFAULT_ACT_SANDBOX",
    "DEFAULT_ACT_POLICIES",
    "DEFAULT_TOOLBUILDER_TOPN",
    "skill_expand_tool",
    "skill_expand_build_tool",
    "skill_act_tool",
    "skill_act_check_tool",
    "skill_compose_tool",
    "skill_verify_tool",
    "skill_verify_flows_tool",
    "build_agent_toolset",
]

#: compose-first candidate cap (``SKALDIR_TOOLBUILDER_TOPN`` / legacy ``GODSV_`` twin).
DEFAULT_TOOLBUILDER_TOPN = 5

#: ACT runner when the caller names none and ``auto`` finds no real bridge.
DEFAULT_ACT_SANDBOX = "sandbox"

#: Extra action kinds the ACT policy may allow, beyond the Computer Use default.
DEFAULT_ACT_POLICIES = ""

#: How long ``skill_act_check`` keeps its capability matrix (computer-use turns
#: must stay fast — measured: a fresh check is ~150 ms per step).
CHECK_CACHE_S = 30.0
_CHECK_CACHE = None

_TOOLBUILDER_TOPN_ENV = "TOOLBUILDER_TOPN"
_CU_SANDBOX_ENV = "CU_SANDBOX"
_ACT_POLICIES_ENV = "ACT_POLICIES"


def _toolbuilder_topn() -> int:
    """The compose-first candidate cap an operator tuned, else the default."""
    raw = getenv(_TOOLBUILDER_TOPN_ENV, "")
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return DEFAULT_TOOLBUILDER_TOPN
    return value if value > 0 else DEFAULT_TOOLBUILDER_TOPN


def _act_runner_default() -> str:
    """The named runner kind for ACT runs started without one (default sandbox)."""
    raw = str(getenv(_CU_SANDBOX_ENV, DEFAULT_ACT_SANDBOX) or "").strip().lower()
    return raw or DEFAULT_ACT_SANDBOX


_LIVE_GOAL_HINTS = (
    "my desktop",
    "my machine",
    "live desktop",
    "real desktop",
    "on my screen",
    "my screen",
    "the host desktop",
)


def _prefer_live_runner(goal: str, runner: str) -> str:
    """``live-desktop`` when the owner asked for *their* machine and the opt-in allows it.

    An isolated host-session/container run **closes its display afterwards** — that is
    correct for isolation, and wrong for "open firefox on my desktop" (measured: Firefox
    lived on :91 and died with the run). Live-desktop is the bridge that leaves the app
    on the owner's screen; it is only chosen when the goal says so *or* the opt-in is
    already granted and the caller left ``runner`` on auto.
    """
    named = str(runner or "").strip().lower()
    if named and named not in ("auto", ""):
        return named
    text = str(goal or "").lower()
    wants_live = any(hint in text for hint in _LIVE_GOAL_HINTS)
    try:
        from .cu_host_session import live_desktop_optin

        allowed = bool(live_desktop_optin().get("allowed"))
    except Exception:  # noqa: BLE001
        allowed = False
    if wants_live:
        return "live-desktop"
    if allowed and any(
        word in text for word in ("firefox", "chrome", "browser", "zen", "opera", "brave", "edge")
    ):

        return "live-desktop"
    return named or _act_runner_default()


def _act_policy_kinds(default: Any) -> list[str]:
    """The policy's own kinds plus the ``SKALDIR_ACT_POLICIES`` extras, deduped."""
    base = list(getattr(default, "allowed_kinds", None) or [])
    extras = [
        part.strip()
        for part in str(getenv(_ACT_POLICIES_ENV, DEFAULT_ACT_POLICIES) or "").split(",")
        if part.strip()
    ]
    seen: set[str] = set()
    kinds: list[str] = []
    for kind in [*base, *extras]:
        if kind and kind not in seen:
            seen.add(kind)
            kinds.append(kind)
    return kinds


def _tool_error(tool: str, exc: BaseException) -> dict[str, Any]:
    """A failure is an honest tool error — never a half result (fail-closed)."""
    return {
        "ok": False,
        "tool": tool,
        "error": f"{type(exc).__name__}: {exc}"[:400],
    }


# --------------------------------------------------------------------------- #
# EXPAND — the tool builder (real builds; failures are honest tool errors)      #
# --------------------------------------------------------------------------- #

def _store_for(runtime: str | Path | None, store: ToolStore | None) -> ToolStore:
    if store is not None:
        return store
    return ToolStore(Path(str(runtime or ".")).expanduser() / "tools.db")


def skill_expand(
    capability: str,
    *,
    runtime: str | Path | None = None,
    store: ToolStore | None = None,
    topn: int | None = None,
) -> dict[str, Any]:
    """EXPAND / compose-first: rank existing tools against a needed capability.

    Real scoring over the registry (:func:`skaldir.tools.compose_candidates_scored`).
    ``refuse`` means an already approved tool covers the need (>= 3 shared
    terms — the builder's own duplicate rule), ``compose`` names the pieces
    to compose first, ``build`` hands the need to the builder.
    """
    builder = ToolBuilder(
        Path(str(runtime or ".")).expanduser(),
        store=_store_for(runtime, store),
    )
    result = builder.compose_check(capability=capability, name="")
    result["topn"] = int(topn or _toolbuilder_topn())
    result.setdefault("ok", True)
    return result


def skill_expand_build(
    request: Mapping[str, Any],
    *,
    runtime: str | Path | None = None,
    store: ToolStore | None = None,
    session_id: str = "",
    llm: Any = None,
    budget: Any = None,
    prompts: Any = None,
    events: Any = None,
) -> dict[str, Any]:
    """EXPAND / build: run the **real** build pipeline for one capability.

    The request maps onto :class:`skaldir.toolbuilder.ToolBuildRequest` field
    by field (unknown keys are refused, not ignored). A build with neither
    ``source_code``/``source_path`` nor a configured model client is reported
    as the precise blocker it is — never smoothed into a fake tool.

    Paket 4 (audit F07): the **run's own** provider and budget are handed to the builder.
    Without them a build from a bare need cannot reach a model at all — which is why the
    adapter used to stay stuck at the missing client while the engine was fine.
    """
    known = {name for name in ToolBuildRequest.__dataclass_fields__}
    unknown = sorted(set(request) - known)
    if unknown:
        return _tool_error(
            "skill_expand_build", ValueError(f"unknown request field(s) {unknown}")
        )
    builder = ToolBuilder(
        Path(str(runtime or ".")).expanduser(),
        store=_store_for(runtime, store),
        session_id=session_id,
        llm=llm,
        budget=budget,
        prompts=prompts,
        events=events,
    )
    result = builder.build(ToolBuildRequest(**dict(request)))
    result.setdefault("ok", bool(result.get("ok")))
    result.setdefault("llm_bound", llm is not None)
    result.setdefault("budget_bound", budget is not None)
    return result


# --------------------------------------------------------------------------- #
# ACT — declared actions on the isolated runners, plus the honest host report   #
# --------------------------------------------------------------------------- #

def _compact_act_record(record: dict) -> dict:
    """Trim the record to what the model needs (full trace stays on disk)."""
    out = {
        key: record[key]
        for key in (
            "ok",
            "status",
            "skipped",
            "reason",
            "end_state",
            "live_state",
            "runner_kind",
            "via_subtool",
            "learned_subtool",
            "schema_hint",
            "retry_same_shape",
            "allowed_kinds",
        )
        if key in record
    }
    actions = record.get("actions") or []
    if actions:
        out["actions"] = [
            {
                "index": a.get("index"),
                "kind": a.get("kind"),
                "status": a.get("status"),
                "detail": str(a.get("detail") or "")[:120],
            }
            for a in actions
        ]
    out["ok_flag"] = bool(out.get("ok"))
    return out


def _maybe_learn_subtool(
    actions: list[Mapping[str, Any]],
    end_state: Mapping[str, Any],
    *,
    goal: str,
    runner_kind: str,
) -> str:
    """Save a verified multi-step flow as a reusable subtool (context-sparing reuse)."""
    try:
        import re as _re

        from .act_service import save_subtool

        name = _re.sub(r"[^a-z0-9]+", "_", (goal or "learned_flow").lower()).strip("_")[:40]
        if not name or name.startswith("_"):
            name = "learned_flow"
        spec = {
            "name": name,
            "version": "1.0",
            "description": f"learned flow: {goal[:120]}",
            "applicability": {
                "app": "any",
                "app_versions": [">=0"],
                "locales": ["*"],
            },
            # end_state-first: if the goal is already true, the engine skips (idempotent)
            "preconditions": [],
            "anchors": [],
            "actions": [dict(a) for a in actions],
            "end_state": dict(end_state),
            "verification_contract": f"learned from a verified run: {goal[:80]}",
        }
        # preconditions/anchors must be non-empty for subtool_errors — use a
        # trivial always-true host fact so the flow stays runnable
        spec["preconditions"] = [
            {"id": "session", "text": "session", "source": "host", "fact": "host_session"}
        ]
        spec["anchors"] = [
            {"id": "session", "text": "session", "source": "host", "fact": "host_session"}
        ]
        save_subtool(spec, force=True)
        return name
    except Exception:  # noqa: BLE001 — learning must never fail the run
        return ""


#: Recent successful flows (goal+end_state) — one per 30 s so the same flow is never
#: executed twice in a row (measured: 3 empty Google Docs because the model retried).
_RECENT_FLOWS: dict[str, float] = {}
FLOW_DEDUP_S = 30.0
#: Loop detection: same (goal, end_state, n-actions) failing N times = stop.
#: Measured: 42 identical Return presses with the same end_state failure.
_FAILURE_COUNTS: dict[str, int] = {}
MAX_IDENTICAL_FAILURES = 5


def _flow_key(goal: str, end_state: Mapping[str, Any] | None, actions: Sequence) -> str:
    import json as _json

    try:
        return _json.dumps(
            {"g": (goal or "")[:80], "e": dict(end_state or {}), "a": len(list(actions or ()))},
            sort_keys=True,
        )
    except Exception:  # noqa: BLE001
        return f"{goal}|{id(end_state)}"


def _infer_end_state(actions: Sequence[Mapping[str, Any]], goal: str = "") -> dict[str, Any]:
    for a in actions:
        if str((a or {}).get("kind", "")) == "launch":
            payload = (a.get("payload") or {}) if isinstance(a.get("payload"), Mapping) else {}
            argv = payload.get("argv") or a.get("argv") or []
            cmd = payload.get("command") or a.get("command") or ""
            target = ""
            if argv and isinstance(argv, (list, tuple)):
                target = str(argv[0])
            elif cmd:
                target = str(cmd).split()[0]
            target_base = target.lower().split("/")[-1]
            if any(b in target_base for b in ("chrome", "chromium", "brave", "firefox", "zen")):
                return {
                    "channel": "window_title",
                    "expect_observe_contains": "Zen|Firefox|Chromium|Chrome|Brave",
                }
            if "calc" in target_base or "localc" in target_base:
                return {"channel": "window_title", "expect_observe_contains": "Calc|LibreOffice"}
            if "writer" in target_base or "lowriter" in target_base:
                return {"channel": "window_title", "expect_observe_contains": "Writer|LibreOffice"}
            if "terminal" in target_base or "ptyxis" in target_base:
                return {"channel": "window_title", "expect_observe_contains": "Terminal|Ptyxis"}
            if target_base:
                return {"channel": "window_title", "expect_observe_contains": target_base}
    for a in actions:
        if str((a or {}).get("kind", "")) == "window":
            payload = (a.get("payload") or {}) if isinstance(a.get("payload"), Mapping) else {}
            title = str(payload.get("title", "") or "").strip()
            if title and title != ".":
                return {"channel": "window_title", "expect_observe_contains": title}
    for a in actions:
        if str((a or {}).get("kind", "")) == "cli":
            verify = (a.get("verify") or {}) if isinstance(a.get("verify"), Mapping) else {}
            exp = str(verify.get("expect_stdout_contains") or "").strip()
            if exp:
                return {"channel": "stdout", "expect_observe_contains": exp}
    if goal:
        goal_l = goal.lower()
        browser_kw = (
            "chrome", "chromium", "brave", "firefox", "zen",
            "browser", "youtube", "x.com", "twitter",
        )
        if any(b in goal_l for b in browser_kw):
            return {
                "channel": "window_title",
                "expect_observe_contains": "Zen|Firefox|Chromium|Chrome|Brave",
            }
        if "calc" in goal_l:
            return {"channel": "window_title", "expect_observe_contains": "Calc|LibreOffice"}
        if "writer" in goal_l:
            return {"channel": "window_title", "expect_observe_contains": "Writer|LibreOffice"}
        if "terminal" in goal_l:
            return {"channel": "window_title", "expect_observe_contains": "Terminal|Ptyxis"}
    return {"expect_ok": True}


def skill_act(
    actions: list[Mapping[str, Any]],
    *,
    runner: str = "",
    workspace: str | Path = ".",
    goal: str = "",
    end_state: Mapping[str, Any] | None = None,
    timeout: float = 90.0,
    write_record: bool = True,
    subtool: str = "",
) -> dict[str, Any]:
    """ACT: run a **declared** action list and verify the end state.

    **Idempotent fast path** (owner: "if I close Firefox he must not blindly re-open"):
    when ``end_state`` already holds on the live screen, the run is skipped and
    reported ``skipped: true`` — one observe, zero actions. ``subtool`` runs a
    stored/builtin flow (``open_browser``, ``open_youtube``) in one call instead
    of rebuilding the action list every time.
    """
    from .act_service import (
        check_end_state,
        default_policy,
        policy_record,
        run_action_list,
        run_subtool,
    )

    runner_kind = _prefer_live_runner(goal, runner)
    policy = default_policy()
    allowed = _act_policy_kinds(policy)

    # Normalize actions: map value to key/text, normalize return, normalize launch argv
    normalized_actions: list[dict[str, Any]] = []
    for item in (actions or []):
        act_dict = dict(item)
        ch = str(act_dict.get("channel", "") or "").strip().lower()
        if ch not in ("api", "dom", "vision"):
            act_dict["channel"] = "api"
        verify = act_dict.get("verify")
        if not isinstance(verify, Mapping) or not verify:
            act_dict["verify"] = {"expect_ok": True}
        payload = dict(act_dict.get("payload") or {})
        if "value" in payload and "key" not in payload and act_dict.get("kind") == "key":
            payload["key"] = payload["value"]
        if "value" in act_dict and "key" not in payload and act_dict.get("kind") == "key":
            payload["key"] = act_dict["value"]
        if "value" in payload and "text" not in payload and act_dict.get("kind") == "type_text":
            payload["text"] = payload["value"]
        if "value" in act_dict and "text" not in payload and act_dict.get("kind") == "type_text":
            payload["text"] = act_dict["value"]
        if act_dict.get("kind") == "key":
            k = str(payload.get("key", "") or act_dict.get("key", "") or "").strip()
            if k.lower() in ("enter", "return"):
                payload["key"] = "Return"
            elif k:
                payload["key"] = k
        if act_dict.get("kind") == "launch":
            if "argv" in act_dict and "argv" not in payload:
                if isinstance(act_dict["argv"], str):
                    import shlex
                    try:
                        payload["argv"] = shlex.split(act_dict["argv"])
                    except Exception:
                        payload["argv"] = act_dict["argv"].split()
                elif isinstance(act_dict["argv"], (list, tuple)):
                    payload["argv"] = [str(x) for x in act_dict["argv"]]
            if "command" in act_dict and "command" not in payload:
                payload["command"] = str(act_dict["command"])
            if not payload.get("argv") and payload.get("command"):
                import shlex
                try:
                    payload["argv"] = shlex.split(str(payload["command"]))
                except Exception:
                    payload["argv"] = str(payload["command"]).split()
        act_dict["payload"] = payload
        normalized_actions.append(act_dict)
    actions = normalized_actions

    if not end_state:
        end_state = _infer_end_state(actions, goal)

    # Fast path: the goal is already true on screen → do not repeat the action
    # (measured: the agent opened Firefox twice because it never looked first).
    # Plus a 30 s flow lock: the same (goal, end_state, n-actions) never runs twice
    # in a row (measured: 3 empty Google Docs).
    # ONLY GUI-mutating actions may be skipped — a `cli`/`api_call` action is opaque
    # (could be a diagnostic like `pgrep`) and must always execute (measured: the
    # fast-path matched "window" in "mutter guard window" and ate a pgrep diagnostic).
    import time as _time

    _GUI_SAFE_KINDS = frozenset(
        {"mouse", "key", "type_text", "dom_click", "dom_fill", "drag", "launch", "window", "scroll"}
    )
    _has_diagnostic = any(
        str((a or {}).get("kind", "")) not in _GUI_SAFE_KINDS for a in (actions or [])
    )
    key = _flow_key(goal, end_state, actions)
    last = _RECENT_FLOWS.get(key)
    if not _has_diagnostic and last and _time.monotonic() - last < FLOW_DEDUP_S:
        return {
            "ok": True,
            "status": "verified",
            "skipped": True,
            "reason": "same flow already succeeded within 30s — not repeating (dedup)",
            "runner_kind": runner_kind,
            "ok_flag": True,
        }
    # Loop detection: same flow failing N times = stop (measured: 42 Return loops)
    fail_count = _FAILURE_COUNTS.get(key, 0)
    if fail_count >= MAX_IDENTICAL_FAILURES:
        return {
            "ok": False,
            "status": "loop_detected",
            "skipped": True,
            "reason": (
                f"same flow failed {fail_count} times in a row — stopping to prevent "
                f"an infinite loop. Change the approach: observe the screen, pick a "
                f"different action, or declare a different end_state."
            ),
            "runner_kind": runner_kind,
            "ok_flag": False,
            "loop_detected": True,
        }
    try:
        from .cu_host_session import list_session_windows

        live_state = list_session_windows()
        titles = "\n".join(
            str(w.get("title") or "")
            for w in (live_state.get("windows") or [])
            if not w.get("system")
        )
        if end_state and not _has_diagnostic:
            already = check_end_state(end_state, titles)
            if already.get("ok"):
                return {
                    "ok": True,
                    "status": "verified",
                    "skipped": True,
                    "reason": "already observed on the live screen — no action needed",
                    "end_state": already,
                    "live_state": live_state,
                    "runner_kind": runner_kind,
                    "ok_flag": True,
                }
    except Exception:  # noqa: BLE001 — never block the real run on an observe failure
        live_state = {}

    # Subtool fast path: one named flow (open_browser / open_youtube / …)
    # If subtool is passed alone without actions, run it directly and return.
    if subtool and not actions:
        record = run_subtool(
            subtool,
            runner_kind=runner_kind,
            workdir=workspace,
            runtime=workspace,
            timeout=timeout,
            write_record=write_record,
            allow_live_desktop=runner_kind == "live-desktop",
        )
        record["runner_kind"] = runner_kind
        record["ok"] = record.get("status") == "verified"
        record["via_subtool"] = subtool
        try:
            record["live_state"] = list_session_windows()
        except Exception:  # noqa: BLE001
            record["live_state"] = live_state
        return record

    # If subtool is passed WITH actions, launch via subtool first only if actions lacks launch
    if subtool and actions:
        has_launch = any(str((a or {}).get("kind", "")) == "launch" for a in actions)
        if not has_launch:
            run_subtool(
                subtool,
                runner_kind=runner_kind,
                workdir=workspace,
                runtime=workspace,
                timeout=timeout,
                write_record=False,
                allow_live_desktop=runner_kind == "live-desktop",
            )
    # If the owner's goal is on *their* machine but the opt-in is still closed,
    # open the approve bar BEFORE the first run (not after a refusal).
    from .cu_host_session import live_desktop_optin

    if runner_kind == "live-desktop" and not live_desktop_optin().get("allowed"):
        from . import cut as _cut

        gate = _cut.maybe_approve_live(goal=goal or "a computer-use action")
        if gate.get("granted_now"):
            target = str(gate.get("target") or "")
            record = run_action_list(
                [dict(item) for item in (actions or [])],
                runner_kind="live-desktop",
                workdir=str(workspace),
                runtime=str(workspace),
                end_state=dict(end_state) if end_state else None,
                goal=goal,
                timeout=float(timeout),
                session_id="agent-toolset",
                write_record=bool(write_record),
                allow_live_desktop=True,
                live_target=target,
            )
            record["live_desktop_gate"] = gate
            record["policy_report"] = policy_record(policy)
            record["allowed_kinds"] = allowed
            record["runner_kind"] = "live-desktop"
            record["ok"] = record.get("status") == "verified"
            record["left_running"] = True
            return record
        if gate.get("prompted"):
            return {
                "ok": False,
                "status": "blocked",
                "reason": f"live desktop refused: {gate.get('reason', 'no opt-in')}",
                "live_desktop_gate": gate,
                "runner_kind": "live-desktop",
                "schema_hint": (
                    "grant the approve bar (or `skaldir act live-setup --target … --write`)"
                ),
            }
    record = run_action_list(
        [dict(item) for item in (actions or [])],
        runner_kind=runner_kind,
        workdir=str(workspace),
        runtime=str(workspace),
        end_state=dict(end_state) if end_state else None,
        goal=goal,
        timeout=float(timeout),
        session_id="agent-toolset",
        policy=policy,
        write_record=bool(write_record),
    )
    # Live-desktop first-use gate: when the run was refused for the opt-in — or the
    # caller asked for the live desktop by name — open the chat's approve bar
    # (←/→ + Enter) instead of only explaining the refusal.
    reason = str(record.get("reason", "")) + str(record.get("missing_capability", ""))
    wants_live = runner_kind in ("live-desktop", "host-session") or "live desktop" in reason.lower()
    if wants_live and record.get("status") in ("blocked", "usage") and (
        "live desktop" in reason.lower() or runner_kind == "live-desktop"
    ):
        from . import cut as _cut

        decision = _cut.maybe_approve_live(goal=goal or "a computer-use action")
        record["live_desktop_gate"] = decision
        if decision.get("granted_now"):
            record = run_action_list(
                [dict(item) for item in (actions or [])],
                runner_kind="live-desktop",
                workdir=str(workspace),
                runtime=str(workspace),
                end_state=dict(end_state) if end_state else None,
                goal=goal,
                timeout=float(timeout),
                session_id="agent-toolset",
                write_record=bool(write_record),
                allow_live_desktop=True,
                live_target=str(decision.get("target") or ""),
            )
            record["live_desktop_gate"] = decision
            record["left_running"] = True
    record["policy_report"] = policy_record(policy)
    record["allowed_kinds"] = allowed
    record["runner_kind"] = runner_kind
    record["ok"] = record.get("status") == "verified"
    # Live state *after* the run: what a human sees on the screen now (windows,
    # focus). The agent must react to the current screen — if the user closed
    # Firefox, the next step must see it gone and decide (re-open or stop).
    try:
        from .cu_host_session import list_session_windows

        record["live_state"] = list_session_windows()
    except Exception:  # noqa: BLE001
        record["live_state"] = {}
    # Auto-learn: a verified multi-step flow becomes a reusable subtool (context-sparing
    # next time: one `subtool: name` call instead of rebuilding the action list).
    if record.get("ok") and len(actions or []) >= 2 and end_state:
        learned = _maybe_learn_subtool(
            actions, end_state, goal=goal, runner_kind=runner_kind
        )
        if learned:
            record["learned_subtool"] = learned
    if record.get("ok"):
        _RECENT_FLOWS[key] = _time.monotonic()
        _FAILURE_COUNTS.pop(key, None)  # reset on success
    else:
        _FAILURE_COUNTS[key] = _FAILURE_COUNTS.get(key, 0) + 1
    # Context economy: keep the full record on disk (the write_record path already
    # did); the dict returned to the model is trimmed to what it needs to decide.
    return _compact_act_record(record)


def skill_act_check(*, probe_image: bool = False) -> dict[str, Any]:
    """ACT check: the honest capability matrix of this host.

    Read-only (with ``probe_image=False`` nothing is started). ``ok`` is True
    only when at least one capability is *real*. **Fast path**: the matrix is
    cached for :data:`CHECK_CACHE_S` so a computer-use turn does not pay 150 ms
    per step re-probing the same host.

    **Omarchy auto-setup**: when the host is Omarchy, the platform adapter's
    ``install_omarchy_skills`` fetches Omarchy's own ``agents/skills/*.md``
    (shell-dev, visual-verification, install-scripts, …) into the runtime's
    skill directory — once, idempotent.
    """
    import time as _time

    global _CHECK_CACHE
    now = _time.monotonic()
    cached = globals().get("_CHECK_CACHE")
    if cached and not probe_image and now - cached[0] < CHECK_CACHE_S:
        return cached[1]
    from .act_service import capability_report

    report = capability_report(probe_image=bool(probe_image))
    report["ok"] = bool((report.get("summary") or {}).get("real"))
    # Omarchy: install the platform's own agent skills (idempotent, one-shot)
    try:
        from .cu_platform import detect_platform, install_omarchy_skills

        pinfo = detect_platform()
        if pinfo.is_omarchy:
            report["omarchy_skills"] = install_omarchy_skills()
        report["platform"] = {
            "os": pinfo.os,
            "distro": pinfo.distro,
            "compositor": pinfo.compositor,
        }
    except Exception:  # noqa: BLE001 — platform extras never block the check
        pass
    if not probe_image:
        _CHECK_CACHE = (now, report)
    return report


# --------------------------------------------------------------------------- #
# COMPOSE — the validated compose path (parse → validate → save)                #
# --------------------------------------------------------------------------- #

def skill_compose(
    payload: Mapping[str, Any],
    *,
    runtime: str | Path | None = None,
    store: ToolStore | None = None,
) -> dict[str, Any]:
    """COMPOSE: validate one composite chain and register it when clean.

    Thin over :func:`skaldir.compose_service.parse_composite` →
    :func:`validate_composite` → :func:`save_composite`. A composite with
    blocking problems is never saved; ``ok`` mirrors the validation report.
    No step runner is started here — execution happens through the registry.
    """
    store = _store_for(runtime, store)
    comp = parse_composite(dict(payload))
    report = validate_composite(store, comp)
    result = report.to_dict()
    if report.ok:
        path = save_composite(Path(str(runtime or ".")).expanduser(), comp)
        result["path"] = str(path)
        result["registered"] = True
    else:
        result["registered"] = False
    result["ok"] = report.ok
    return result


# --------------------------------------------------------------------------- #
# VERIFY — PASS / FAIL / UNKNOWN against the real workspace                     #
# --------------------------------------------------------------------------- #

def skill_verify(
    criteria: list[Any],
    *,
    workspace: str | Path = ".",
    project_id: str = "",
    goal: str = "",
    plan_file: str | Path = "",
    timeout: float = 600.0,
    state_dir: str | Path | None = None,
) -> dict[str, Any]:
    """VERIFY: plan and execute real checks — ``UNKNOWN`` is never a success.

    Thin over :func:`skaldir.verify_service.build_plan` → :func:`run_plan`.
    The record keeps the verdict verbatim (``PASS`` / ``FAIL`` / ``UNKNOWN``);
    ``ok`` is True **only** for ``PASS``, so an unknown or unexecutable check
    can never look like a success to the loop.
    """
    root = Path(str(workspace)).expanduser()
    _igs, contract = contract_criteria(
        root, session_id=str(project_id or "")
    ) if project_id else ("", [])
    plan = build_plan(
        [*contract, *list(criteria or ())],
        goal=goal,
        workspace=root,
        plan_file=str(plan_file) if plan_file else "",
    )
    record = run_plan(plan, timeout=float(timeout), state_dir=state_dir or root / ".godsv")
    record["ok"] = record.get("verdict") == VERDICT_PASS
    return record


def skill_verify_flows(
    *,
    workspace: str | Path = ".",
    flow_file: str | Path = "",
    headless: bool = True,
    screenshot_dir: str | Path = "",
    state_dir: str | Path | None = None,
) -> dict[str, Any]:
    """VERIFY (flows): run the declared user flows and record the verdict.

    Thin over :func:`skaldir.verify_flows.load_flows` → :func:`run_flows`
    (+ :class:`FlowStore` evidence when a state dir is given). A flow the
    loader refused is an ``UNKNOWN`` result of its own; ``ok`` is True only
    for a ``passed`` verdict.
    """
    root = Path(str(workspace)).expanduser()
    declaration = load_flows(root, flow_file)
    shots = Path(str(screenshot_dir)) if screenshot_dir else root / ".godsv" / "flow_shots"
    report = run_flows(
        declaration.flows,
        screenshot_dir=shots,
        declaration=declaration,
        headless=bool(headless),
    )
    record = report.as_dict()
    if state_dir:
        FlowStore(state_dir).record(report)
    record["rejected"] = [dict(item) for item in (declaration.rejected or ())]
    record["ok"] = record.get("verdict") == VERDICT_PASS
    return record


# --------------------------------------------------------------------------- #
# the toolset — every layer as one AgentTool                                    #
# --------------------------------------------------------------------------- #

_OBJECT = {"type": "object", "additionalProperties": True}


def skill_expand_tool(
    *, runtime: str | Path | None = None, store: ToolStore | None = None
) -> AgentTool:
    """EXPAND as a tool: compose-first candidates for a capability need."""
    return AgentTool(
        name="skill_expand",
        description=(
            "EXPAND (compose-first): score existing approved/scanned tools "
            "against a needed capability; decision compose|refuse|build."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "capability": {"type": "string", "minLength": 1},
                "runtime": {"type": "string"},
            },
            "required": ["capability"],
            "additionalProperties": False,
        },
        handler=lambda **kw: skill_expand(
            str(kw["capability"]),
            runtime=kw.get("runtime") or runtime,
            store=store,
        ),
    )


def tool_search_tool(
    *, runtime: str | Path | None = None, project_id: str = ""
) -> AgentTool:
    """TOOL-01: discover tools by capability need instead of listing the full catalog."""
    def _handler(**kw: Any) -> dict:
        from .capability import project_id_for
        from .discovery import default_runtime_dir, discover

        effective_runtime = kw.get("runtime") or runtime or default_runtime_dir()
        effective_project_id = str(kw.get("project_id", "") or project_id or project_id_for("."))
        result = discover(
            str(kw.get("need", "")),
            runtime=effective_runtime,
            project_id=effective_project_id,
            limit=int(kw.get("limit", 5) or 5),
        )
        # Enrich matches with input_schema so the model knows how to call them
        items = result.get("matches", []) or result.get("results", [])
        for match in items:
            tool_id = match.get("id") or match.get("tool_id") or match.get("name") or ""
            if tool_id and "input_schema" not in match:
                try:
                    from .tools_core.registry import descriptor as _descriptor

                    desc = _descriptor(tool_id)
                    if desc and "input_schema" in desc:
                        match["input_schema"] = desc["input_schema"]
                except Exception:  # noqa: BLE001
                    pass
                if "input_schema" not in match:
                    try:
                        from .agent_loop import input_schema_for_spec
                        from .tools import ToolStore

                        tools_db = Path(effective_runtime) / "tools.db"
                        if tools_db.is_file():
                            store = ToolStore(str(tools_db))
                            spec = store.get(tool_id)
                            store.close()
                            if spec:
                                match["input_schema"] = input_schema_for_spec(
                                    spec, base_dir=effective_runtime
                                )
                    except Exception:  # noqa: BLE001
                        pass
        return result

    return AgentTool(
        name="tool_search",
        description=(
            "Discover tools by capability need. Returns ranked candidates with "
            "tool-id, version, purpose, side-effect class, and availability. "
            "Use this instead of loading the full tool catalog into context."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "need": {
                    "type": "string",
                    "minLength": 1,
                    "description": "what capability is needed",
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
                "project_id": {"type": "string"},
                "runtime": {"type": "string"},
            },
            "required": ["need"],
            "additionalProperties": False,
        },
        handler=_handler,
    )


def knowledge_search_tool(
    *, runtime: str | Path | None = None
) -> AgentTool:
    """INFO-02: search the knowledge base by query with scope/kind/status filters."""
    def _handler(**kw: Any) -> dict:
        from .knowledge import KnowledgeStore

        store = KnowledgeStore(runtime=kw.get("runtime") or runtime)
        return store.search(
            str(kw.get("query", "")),
            scope=str(kw.get("scope", "") or ""),
            kinds=list(kw.get("kinds") or []) or None,
            statuses=list(kw.get("statuses") or []) or None,
            limit=int(kw.get("limit", 5) or 5),
            max_output_tokens=int(kw.get("max_output_tokens", 1200) or 1200),
        )

    return AgentTool(
        name="knowledge_search",
        description=(
            "Search the knowledge base. Returns matching entries with id, title, "
            "kind, status, revision, snippet, and line range. Filter by scope, "
            "kind, or status. Use knowledge_read for exact line ranges."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1},
                "scope": {"type": "string"},
                "kinds": {"type": "array", "items": {"type": "string"}},
                "statuses": {"type": "array", "items": {"type": "string"}},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
                "max_output_tokens": {
                    "type": "integer",
                    "minimum": 100,
                    "maximum": 4000,
                    "default": 1200,
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        handler=_handler,
    )


def knowledge_read_tool(
    *, runtime: str | Path | None = None
) -> AgentTool:
    """INFO-03: read exact line ranges from a versioned knowledge entry."""
    def _handler(**kw: Any) -> dict:
        from .knowledge import KnowledgeStore

        store = KnowledgeStore(runtime=kw.get("runtime") or runtime)
        return store.read_lines(
            str(kw.get("id", "")),
            expected_revision=str(kw.get("expected_revision", "") or ""),
            start_line=int(kw.get("start_line", 1) or 1),
            end_line=int(kw.get("end_line", 0) or 0),
            max_output_tokens=int(kw.get("max_output_tokens", 1200) or 1200),
        )

    return AgentTool(
        name="knowledge_read",
        description=(
            "Read exact line ranges from a knowledge entry by id. Pass "
            "expected_revision (sha256) to get STALE_REFERENCE if the content "
            "changed. Returns numbered lines with total_lines and revision."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "id": {"type": "string", "minLength": 1},
                "expected_revision": {"type": "string"},
                "start_line": {"type": "integer", "minimum": 1, "default": 1},
                "end_line": {"type": "integer", "minimum": 0, "default": 0},
                "max_output_tokens": {
                    "type": "integer",
                    "minimum": 100,
                    "maximum": 4000,
                    "default": 1200,
                },
            },
            "required": ["id"],
            "additionalProperties": False,
        },
        handler=_handler,
    )


def skill_expand_build_tool(
    *, runtime: str | Path | None = None, store: ToolStore | None = None,
    llm: Any = None, budget: Any = None, prompts: Any = None, events: Any = None,
) -> AgentTool:
    """EXPAND as a tool: run the real build pipeline for a declared need.

    The run's provider and budget ride along (audit F07): a build from a bare need has to
    reach the *configured* client, not a missing one.
    """
    return AgentTool(
        name="skill_expand_build",
        parallel_safe=False,
        mutating=True,
        description=(
            "EXPAND (build): run the real tool build pipeline for one "
            "capability request (source or configured model required; "
            "failures come back as precise, honest errors)."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "request": {
                    "type": "object",
                    "description": "ToolBuildRequest fields (capability required)",
                },
                "runtime": {"type": "string"},
                "session_id": {"type": "string"},
            },
            "required": ["request"],
            "additionalProperties": False,
        },
        handler=lambda **kw: skill_expand_build(
            kw.get("request") or {},
            runtime=kw.get("runtime") or runtime,
            store=store,
            session_id=str(kw.get("session_id", "") or ""),
            # F07 (Paket 4): the run's provider/budget must REACH the builder —
            # a build from a bare need drafts through this client. The closure
            # used to capture them and drop them, so the builder reported
            # "no model client is configured" inside a fully configured run.
            llm=llm,
            budget=budget,
            prompts=prompts,
            events=events,
        ),
    )


def skill_act_tool() -> AgentTool:
    """ACT as a tool: declared actions, real runners, honest records."""
    return AgentTool(
        name="skill_act",
        parallel_safe=False,
        mutating=True,
        description=(
            "ACT: run declared actions on a real bridge. GO STRAIGHT TO skill_act — "
            "do not call skill_act_check first (it is cached; one call is enough). "
            "BATCH all GUI steps in ONE call (fast). "
            "For multi-step flows (launch + navigate / type / key), declare all "
            "steps in 'actions'. Only use 'subtool' alone (e.g. open_browser | open_youtube) "
            "when you only need that single predefined operation. "
            "If end_state already holds (result.skipped) do NOTHING — the screen "
            "already matches. "
            'EACH action is {"kind":"cli"|"api_call"|"launch"|"key"|"type_text"|"mouse"|'
            '"scroll"|"drag"|"window", '
            '"channel":"api"|"dom"|"vision", "argv":["firefox"] or "payload":{…}, '
            '"verify":{"expect_ok":true}|{"expect_stdout_contains":…}'
            '|{"expect_observe_contains":…}}. '
            'CLI commands (cat, curl, ps, which): use "kind":"cli" with "argv":[…] and '
            'verify with expect_stdout_contains or expect_ok. For CLI end_state use '
            '"channel":"stdout" (checks command output) — NOT "window_title" (checks '
            'desktop windows). Example: {"actions":[{"kind":"cli","channel":"api",'
            '"argv":["curl","-s","https://example.com"],"verify":{"expect_stdout_contains":"Example"}}],'
            '"end_state":{"channel":"stdout","expect_observe_contains":"Example"}}. '
            'GUI apps: {"actions":[{"kind":"launch","channel":"api",'
            '"payload":{"command":"firefox"},"verify":{"expect_ok":true}},'
            '{"kind":"type_text","channel":"api","payload":{"text":"https://x.com"}},'
            '{"kind":"key","channel":"api","payload":{"key":"Return"}}],'
            '"runner":"live-desktop","end_state":{"channel":"window_title",'
            '"expect_observe_contains":"Firefox"}}. '
            'IMPORTANT: to call a registered tool (web_search, web_fetch, terminal), '
            'use the tool name directly as a tool call — NOT via skill_act argv. '
            'skill_act is for GUI/desktop/CLI actions only. '
            "result.live_state is the screen NOW (windows/focus) — react to it; if the "
            "user closed an app, do not blindly re-open unless the goal needs it. "
            "ok is True ONLY for verified."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "actions": {
                    "type": "array",
                    "items": _OBJECT,
                    "minItems": 1,
                },
                "subtool": {
                    "type": "string",
                    "description": (
                        "run a named flow instead of actions: open_browser | "
                        "open_youtube | open_website | host_window_state | …"
                    ),
                },
                "runner": {
                    "type": "string",
                    "description": (
                        "auto, container-gui, host-session, sandbox, live-desktop "
                        "(needs the owner's approve bar), dry-run"
                    ),
                },
                "goal": {"type": "string"},
                "end_state": {
                    "type": "object",
                    "description": (
                        "What the world looks like after the actions. "
                        "channel: 'window_title' (desktop windows), 'stdout' (CLI output), "
                        "or 'app_state' (app state file). "
                        "expect_observe_contains: string to find in the observation "
                        "(use '|' for OR). "
                        "For CLI commands use channel:'stdout' + expect_observe_contains "
                        "matching the command output."
                    ),
                    "properties": {
                        "channel": {
                            "type": "string",
                            "enum": ["window_title", "stdout", "app_state"],
                            "default": "window_title",
                        },
                        "expect_observe_contains": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "expect_state_contains": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "description": {"type": "string"},
                    },
                    "additionalProperties": True,
                },
                "timeout": {"type": "number", "exclusiveMinimum": 0, "default": 90.0},
                "write_record": {"type": "boolean", "default": True},
                "workspace": {"type": "string", "default": "."},
            },
            "additionalProperties": False,
        },
        handler=lambda **kw: skill_act(
            [dict(item) for item in (kw.get("actions") or [])],
            runner=str(kw.get("runner", "") or ""),
            workspace=kw.get("workspace", "."),
            goal=str(kw.get("goal", "") or ""),
            end_state=kw.get("end_state"),
            timeout=float(kw.get("timeout", 90.0) or 90.0),
            write_record=bool(kw.get("write_record", True)),
            subtool=str(kw.get("subtool", "") or ""),
        ),
    )


def skill_act_check_tool() -> AgentTool:
    """ACT check as a tool: the honest capability matrix (read-only)."""
    return AgentTool(
        name="skill_act_check",
        description=(
            "ACT check: the honest capability matrix of this host (read-only; "
            "probe_image=True re-verifies the container tools live)."
        ),
        input_schema={
            "type": "object",
            "properties": {"probe_image": {"type": "boolean", "default": False}},
            "additionalProperties": False,
        },
        handler=lambda **kw: skill_act_check(
            probe_image=bool(kw.get("probe_image", False))
        ),
    )


def skill_compose_tool(
    *, runtime: str | Path | None = None, store: ToolStore | None = None
) -> AgentTool:
    """COMPOSE as a tool: parse → validate → register (when clean)."""
    return AgentTool(
        name="skill_compose",
        parallel_safe=False,
        mutating=True,
        description=(
            "COMPOSE: parse a composite tool document, run the full "
            "validation (shape, registry targets, dataflow) and register it "
            "only when there is no blocking problem."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "payload": _OBJECT,
                "runtime": {"type": "string"},
            },
            "required": ["payload"],
            "additionalProperties": False,
        },
        handler=lambda **kw: skill_compose(
            kw.get("payload") or {},
            runtime=kw.get("runtime") or runtime,
            store=store,
        ),
    )


def skill_verify_tool() -> AgentTool:
    """VERIFY as a tool: plan + run real checks; UNKNOWN is ok=False."""
    return AgentTool(
        name="skill_verify",
        description=(
            "VERIFY: plan the real check for each criterion and execute it "
            "against the workspace. verdict PASS|FAIL|UNKNOWN verbatim; "
            "ok is True only for PASS — UNKNOWN is never a success."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "criteria": {"type": "array", "items": {}},
                "workspace": {"type": "string", "default": "."},
                "goal": {"type": "string"},
                "plan_file": {"type": "string"},
                "project_id": {"type": "string"},
                "timeout": {"type": "number", "default": 600.0},
                "state_dir": {"type": "string"},
            },
            "additionalProperties": False,
        },
        handler=lambda **kw: skill_verify(
            list(kw.get("criteria") or ()),
            project_id=str(kw.get("project_id", "") or ""),
            workspace=kw.get("workspace", "."),
            goal=str(kw.get("goal", "") or ""),
            plan_file=str(kw.get("plan_file", "") or ""),
            timeout=float(kw.get("timeout", 600.0) or 600.0),
            state_dir=kw.get("state_dir"),
        ),
    )


def skill_verify_flows_tool() -> AgentTool:
    """VERIFY flows as a tool: declared user flows, run and judged."""
    return AgentTool(
        name="skill_verify_flows",
        description=(
            "VERIFY (flows): run the user flows declared in "
            ".godsv/flows.json against their real targets; a refused "
            "declaration is UNKNOWN, and ok is True only for a passed verdict."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "workspace": {"type": "string", "default": "."},
                "flow_file": {"type": "string"},
                "headless": {"type": "boolean", "default": True},
                "screenshot_dir": {"type": "string"},
                "state_dir": {"type": "string"},
            },
            "additionalProperties": False,
        },
        handler=lambda **kw: skill_verify_flows(
            workspace=kw.get("workspace", "."),
            flow_file=str(kw.get("flow_file", "") or ""),
            headless=bool(kw.get("headless", True)),
            screenshot_dir=str(kw.get("screenshot_dir", "") or ""),
            state_dir=kw.get("state_dir"),
        ),
    )


def build_agent_toolset(
    *,
    workspace: str | Path,
    kind: str = "builtin",
    store: ToolStore | None = None,
    runtime: str | Path | None = None,
    project_id: str = "",
    llm: Any = None,
    budget: Any = None,
    prompts: Any = None,
    events: Any = None,
) -> ToolSet:
    """Assemble the agent toolset: builtin + registry + every layer tool.

    ``kind`` is the :func:`skaldir.agent_loop.tool_set_for` selection
    (``builtin`` | ``registry`` | ``both``); on top of it this adds the layer
    tools (EXPAND ×2, ACT ×2, COMPOSE, VERIFY ×2) so one goal run reaches all
    8 layers by tool name. Nothing here starts a container or a provider.
    """
    from .agent_loop import builtin_tools, registry_tools

    selection = str(kind or "builtin").strip().lower()
    if selection not in ("builtin", "registry", "both"):
        raise AgentLoopError(
            f"unknown tool set {selection!r} (expected builtin|registry|both)"
        )
    tools: list[AgentTool] = []
    if selection in ("builtin", "both"):
        tools.extend(builtin_tools(str(workspace)))
    # TOOL-01 + INFO-02/03: discovery and knowledge tools
    tools.append(tool_search_tool(runtime=runtime, project_id=project_id))
    tools.append(knowledge_search_tool(runtime=runtime))
    tools.append(knowledge_read_tool(runtime=runtime))
    # Paket 1 (audit F01/F02): one shared, context-bound capability set. This is where
    # run_code (CORE-06), terminal/patch/search and the browser enter the model toolset —
    # they were reachable from one entry point and missing from the other.
    from .core_capabilities import core_capability_tools

    already = {getattr(t, "name", "") for t in tools}
    for cap_tool in core_capability_tools(
        workspace=workspace,
        runtime=runtime,
        run_id=str(project_id or ""),
        exclude=already,          # one capability, one model tool (no list_dir/list_dir_2)
    ):
        tools.append(cap_tool)
    if selection in ("registry", "both"):
        # `_store_for` already resolves `<runtime>/tools.db` when no store is handed in;
        # the two tool-set builders used to refuse instead, so the documented
        # `store: ToolStore | None = None` signature was unusable (measured:
        # tests/test_tool_search_integration.py — 4 failures at the initial transfer
        # commit, before any change here).
        store = _store_for(runtime if runtime is not None else workspace, store)
        directory = (
            Path(str(runtime)).expanduser()
            if runtime is not None
            else Path(str(workspace)).expanduser()
        )
        # the scope filter (visible_tools + health) only applies when the runtime
        # really carries a generated_tools surface; otherwise the store is the truth
        runtime_tools_dir = (
            Path(str(runtime)).expanduser() / "generated_tools" if runtime else None
        )
        if runtime_tools_dir is not None and runtime_tools_dir.is_dir():
            registry_variant = registry_tools(
                store, runtime=runtime, project_id=project_id, base_dir=directory
            )
        else:
            registry_variant = registry_tools(store, runtime=None, base_dir=directory)
        # F03 class (measured 2026-10-04, feature harness-chat-standardtest): a runtime
        # with the registered core toolset publishes specs under the names the shared
        # chain above already bound, and `ToolSet.of` renamed the second copy to
        # `read_file_2`/`terminal_2`/`patch_2`/`web_search_2` — duplicate wire names whose
        # copy resolved its root away from the run binding. The already-bound
        # (run-context) tool wins; genuinely new registry names still pass through.
        bound = {getattr(t, "name", "") for t in tools}
        tools.extend(
            tool
            for tool in registry_variant
            if getattr(tool, "name", "") not in bound
        )
    for layer_tool in (
        skill_expand_tool(runtime=runtime, store=store),
        skill_expand_build_tool(
            runtime=runtime, store=store,
            llm=llm, budget=budget, prompts=prompts, events=events,
        ),
        skill_act_tool(),
        skill_act_check_tool(),
        skill_compose_tool(runtime=runtime, store=store),
        skill_verify_tool(),
        skill_verify_flows_tool(),
    ):
        tools.append(layer_tool)
    return ToolSet.of(tools)
