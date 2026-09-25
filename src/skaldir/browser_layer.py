"""The browser layer: one session manager, typed operations, no window flood.

This module implements ``docs/masterplan/07-BROWSER-UPGRADE.md`` — the confirmed
integration direction (§1). What it is **not**:

* not a second autonomous agent loop (§2: the tool is an executor, Skaldir decides);
* not a second MCP stack (§12: ``adapter_mcp.py`` is reused, not duplicated);
* not a claim that an existing Zen session is controllable (§3: that needs its own
  successful attach attempt first, and Chromium success does not count).

Three rules shape the code:

**§4 — the layer is typed, not loose shell.** Every call carries ``run_id``, ``session_id``,
``tab_id``, a deadline and a cancellation signal. Every result carries status, the
*actual* backend, duration, a short preview, evidence references and an error class. Only
operations the running backend really supports are offered; the rest refuse by name.

**§6 — no window flood.** The state sequence is *discover → attach (unique) → ready →
act*. A launch happens only when no suitable instance exists **and** creating one fits the
task, and a lock prevents parallel starts. A start timeout leads to diagnosis and state
reconciliation, never to the next identical launch. "Not observable" is never read as "the
browser does not exist". User tabs are kept; only agent-created tabs are closed, and only
by their real use.

**§5 — references are bound to (session, tab, observation revision).** After navigation or
a relevant DOM change the observation is redone. Element numbers from an earlier run are
never reused.
"""

from __future__ import annotations

import enum
import json
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

__all__ = [
    "BackendKind",
    "BrowserBackend",
    "BrowserCapability",
    "BrowserLayer",
    "BrowserResult",
    "ErrorClass",
    "Observation",
    "Operation",
    "SESSION_LOCK_NAME",
    "SessionManager",
    "TabInfo",
    "agent_browser_probe",
]

#: The lock file that makes "one start at a time" true across processes (§6).
SESSION_LOCK_NAME = "browser-session.lock"

#: The operations §4 names. These are an **internal interface**; the plan is explicit that
#: the names do not assert any existing CLI command.
OPERATIONS: tuple[str, ...] = (
    "browser.discover", "browser.attach", "browser.tabs", "browser.observe",
    "browser.act", "browser.read", "browser.batch", "browser.verify", "browser.detach",
)


class ErrorClass(str, enum.Enum):
    """The error classes a result may carry — never a bare string."""

    NONE = ""
    NOT_INSTALLED = "not_installed"
    NOT_CONNECTED = "not_connected"
    UNSUPPORTED = "unsupported"
    STALE_REFERENCE = "stale_reference"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    DEADLINE = "deadline_exceeded"
    CONFLICT = "conflict"
    REFUSED = "refused"
    BACKEND = "backend_error"
    UNKNOWN = "unknown"


class BackendKind(str, enum.Enum):
    """Which executor a result actually came from (§4: "tatsächliches Backend")."""

    NONE = "none"
    AGENT_BROWSER = "agent-browser"
    ACT_HOST = "act-host"
    PLAYWRIGHT = "playwright"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class BrowserCapability:
    """What a backend can honestly do here, measured once (§9.4)."""

    backend: BackendKind
    available: bool
    version: str = ""
    binary: str = ""
    license: str = ""
    supports: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend.value,
            "available": self.available,
            "version": self.version,
            "binary": self.binary,
            "license": self.license,
            "supports": list(self.supports),
            "missing": list(self.missing),
            "reason": self.reason,
        }

    def supports_op(self, operation: str) -> bool:
        return operation in self.supports


@dataclass
class BrowserResult:
    """One answer from the layer (§4's result contract)."""

    operation: str
    ok: bool = False
    status: str = "unknown"
    backend: str = BackendKind.NONE.value
    run_id: str = ""
    session_id: str = ""
    tab_id: str = ""
    duration_ms: float = 0.0
    preview: str = ""
    evidence: list[dict[str, str]] = field(default_factory=list)
    error_class: ErrorClass = ErrorClass.NONE
    error: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "ok": self.ok,
            "status": self.status,
            "backend": self.backend,
            "run_id": self.run_id,
            "session_id": self.session_id,
            "tab_id": self.tab_id,
            "duration_ms": round(self.duration_ms, 3),
            "preview": self.preview[:200],
            "evidence": list(self.evidence),
            "error_class": self.error_class.value,
            "error": self.error[:400],
            "data": self.data,
        }


@dataclass
class Observation:
    """One reading, bound to (session, tab, revision) — §5's hard rule."""

    session_id: str = ""
    tab_id: str = ""
    revision: str = ""
    at: float = 0.0
    kind: str = "dom"          # dom | a11y | screenshot | text
    text: str = ""
    element_refs: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"session_id": self.session_id, "tab_id": self.tab_id,
                "revision": self.revision, "at": self.at, "kind": self.kind,
                "text": self.text[:2000], "element_refs": dict(self.element_refs)}


@dataclass
class TabInfo:
    """A tab with a stable id, its URL, its owner and the run it belongs to (§4)."""

    tab_id: str
    url: str = ""
    title: str = ""
    owner: str = "user"        # user | agent — §6: only agent tabs may be closed
    run_id: str = ""
    session_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"tab_id": self.tab_id, "url": self.url, "title": self.title,
                "owner": self.owner, "run_id": self.run_id, "session_id": self.session_id}


class Operation(str, enum.Enum):
    DISCOVER = "browser.discover"
    ATTACH = "browser.attach"
    TABS = "browser.tabs"
    OBSERVE = "browser.observe"
    ACT = "browser.act"
    READ = "browser.read"
    BATCH = "browser.batch"
    VERIFY = "browser.verify"
    DETACH = "browser.detach"


# --------------------------------------------------------------------------- #
# capability probe (§9.2/§9.4): measured, pinned, never "latest" at start
# --------------------------------------------------------------------------- #


#: Where a project-local install puts the binary (§9.3: "Abhängigkeiten isoliert zum
#: Projekt installieren"). The probe looks here *before* PATH, so a pinned project install
#: wins over whatever happens to be global on the machine.
PROJECT_BIN = Path("node_modules") / ".bin"


def _resolve_binary(binary: str, *, root: str | Path | None = None) -> str:
    """Find the tool: project-local first, then PATH. Never downloads anything.

    ``root`` is the project to look in. When it is given it **replaces** the auto-detected
    repository root rather than adding to it — otherwise a caller could not express "this
    project has no install", which is exactly the case the refusal path has to be testable
    for.
    """
    if root is not None:
        candidate = Path(root) / PROJECT_BIN / binary
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
        return shutil.which(binary) or ""
    here = Path(__file__).resolve().parents[2]      # <repo>/src/skaldir/… -> <repo>
    candidate = here / PROJECT_BIN / binary
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return str(candidate)
    return shutil.which(binary) or ""


def agent_browser_probe(*, binary: str = "agent-browser",
                        root: str | Path | None = None) -> BrowserCapability:
    """Is agent-browser installed here, which version, and what does it claim?

    §9.2 forbids an unversioned ``latest`` install at every start; §9.4 wants a capability
    probe whose failures are visible instead of silent download loops. So this reads the
    installed tool and reports ``not_installed`` with the exact install command when it is
    absent — it never installs anything on its own.

    §9.3 asks for the dependency to be installed *isolated to the project*, so the
    project-local ``node_modules/.bin`` is consulted before PATH: a pinned project install
    must win over whatever happens to be installed globally.
    """
    path = _resolve_binary(binary, root=root)
    if not path:
        return BrowserCapability(
            BackendKind.AGENT_BROWSER, available=False, binary=binary,
            reason=(
                "`%s` is neither project-local (`node_modules/.bin`) nor on PATH. Install "
                "it pinned (never `latest`), e.g. `npm install --save-dev "
                "agent-browser@<exact version>`, then record the version and its licence "
                "here — §9.2/§9.3"
            ) % binary,
            missing=OPERATIONS,
        )
    version = ""
    try:
        proc = subprocess.run([path, "--version"], capture_output=True, text=True,
                              timeout=15, check=False)
        lines = [line for line in (proc.stdout or proc.stderr or "").splitlines() if line.strip()]
        version = lines[0].strip()[:80] if lines else ""
    except (OSError, subprocess.SubprocessError) as exc:
        return BrowserCapability(
            BackendKind.AGENT_BROWSER, available=False, binary=path, version=version,
            reason=f"installed but `--version` failed: {type(exc).__name__}: {exc}",
            missing=OPERATIONS,
        )
    if not version:
        # §9.2: an unversioned install is exactly what must not happen. A tool that will
        # not name its version cannot be pinned, so it is NOT treated as usable.
        return BrowserCapability(
            BackendKind.AGENT_BROWSER, available=False, binary=path, version="",
            reason=(f"`{path}` is present but reported no version (`--version` answered "
                    f"with exit {proc.returncode} and no output) — §9.2 requires a pinned, "
                    "verified version before this backend may be used"),
            missing=OPERATIONS,
        )
    # The documented surface (§2). Declared here, and each one is *verified* per call:
    # a declared capability is not the same as a working one.
    return BrowserCapability(
        BackendKind.AGENT_BROWSER, available=True, binary=path, version=version,
        license="Apache-2.0 (per upstream documentation; verify against the installed "
                "package before a release note)",
        supports=OPERATIONS,
        missing=(),
        reason="",
    )


# --------------------------------------------------------------------------- #
# session manager (§6): discover → attach → ready → act, one start at a time
# --------------------------------------------------------------------------- #


class SessionManager:
    """Owns the browser process, host window, tab ids, backend and owner — separately.

    §6 is the rule this exists for: *„Fensterflut zuverlässig verhindern“*. The measured
    defect in the current code (§12) is that a launch is treated as the fix for "window not
    observable", which is exactly how twelve windows appear. Here:

    * ``ensure`` **discovers first** and only launches when nothing suitable exists and the
      task allows creation;
    * a cross-process lock serialises starts, so two concurrent ``ensure`` calls cannot
      both launch;
    * a start that times out leaves the state as ``unconfirmed`` and reports it — it never
      launches again to "fix" the unknown;
    * tabs carry ``owner``; ``close_agent_tabs`` never touches a user tab and there is no
      ``close --all``.
    """

    def __init__(self, state_dir: str | Path, *, backend: BrowserBackend | None = None) -> None:
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._lock_path = self.state_dir / SESSION_LOCK_NAME
        self._backend = backend
        self._guard = threading.RLock()
        self._starts = 0
        self._last_launch_attempt = 0.0

    # -- state ----------------------------------------------------------

    def _state_file(self) -> Path:
        return self.state_dir / "browser-session.json"

    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self._state_file().read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, data: dict[str, Any]) -> None:
        tmp = self._state_file().with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, self._state_file())

    def _lock(self):
        """Cross-process exclusive lock around a start (§6: "ein Lock parallele Starts")."""

        class _Guard:
            def __init__(self, path: Path) -> None:
                self.path = path
                self.handle: Any = None

            def __enter__(self):
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.handle = open(self.path, "a+", encoding="utf-8")
                try:
                    import fcntl

                    fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX)
                except Exception:  # noqa: BLE001 — no fcntl: the thread guard still holds
                    pass
                return self.handle

            def __exit__(self, *exc: Any) -> bool:
                try:
                    if self.handle is not None:
                        import fcntl

                        fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
                        self.handle.close()
                except Exception:  # noqa: BLE001
                    pass
                return False

        return _Guard(self._lock_path)

    # -- the state sequence ---------------------------------------------

    def discover(self) -> dict[str, Any]:
        """What exists right now. Read-only; never creates anything."""
        with self._guard:
            state = self._read()
        return {
            "state": state.get("state", "unknown"),
            "session_id": state.get("session_id", ""),
            "backend": state.get("backend", BackendKind.NONE.value),
            "tab_count": len(state.get("tabs", []) or []),
            "agent_tabs": [t for t in (state.get("tabs", []) or []) if t.get("owner") == "agent"],
            "user_tabs": [t for t in (state.get("tabs", []) or []) if t.get("owner") == "user"],
            "last_launch_attempt": state.get("last_launch_attempt", 0.0),
            "starts": int(state.get("starts", 0) or 0),
        }

    def ensure(
        self,
        *,
        task: str = "",
        run_id: str = "",
        allow_create: bool = True,
        now: float | None = None,
    ) -> dict[str, Any]:
        """The one entry point that may start a browser — and only once.

        Returns the state, and crucially ``action``: ``reused`` | ``launched`` | ``refused`` |
        ``unconfirmed``. A caller that gets ``unconfirmed`` must diagnose, not launch again.
        """
        stamp = time.time() if now is None else float(now)
        with self._lock():
            state = self._read()
            current = state.get("state", "unknown")
            if current == "ready" and state.get("session_id"):
                return {"action": "reused", **self.discover(), "task": task,
                        "run_id": run_id}
            if current == "unconfirmed":
                # §6: a start timeout leads to diagnosis and state reconciliation, never to
                # the next identical launch.
                return {
                    "action": "unconfirmed",
                    **self.discover(),
                    "task": task,
                    "run_id": run_id,
                    "reason": (
                        "the previous start left the state `unconfirmed`; diagnose it "
                        "(`skaldir desk indicator` / `browser.discover`) instead of "
                        "launching again"
                    ),
                }
            if not allow_create:
                return {"action": "refused", **self.discover(), "task": task,
                        "run_id": run_id,
                        "reason": "allow_create is false and no session exists"}
            if self._backend is None:
                return {"action": "refused", **self.discover(), "task": task,
                        "run_id": run_id,
                        "reason": "no browser backend is configured in this session manager"}

            started = self._backend.launch(task=task, run_id=run_id)
            self._starts += 1
            self._last_launch_attempt = stamp
            data = {
                "state": "ready" if started.get("ok") else "unconfirmed",
                "session_id": str(started.get("session_id") or f"bs-{int(stamp)}"),
                "backend": str(started.get("backend") or BackendKind.AGENT_BROWSER.value),
                "tabs": list(started.get("tabs") or []),
                "starts": self._starts,
                "last_launch_attempt": stamp,
                "task": task,
                "run_id": run_id,
                "launch_detail": str(started.get("detail") or "")[:300],
            }
            self._write(data)
            return {"action": "launched" if started.get("ok") else "unconfirmed",
                    **self.discover(), "task": task, "run_id": run_id}

    def record_tabs(self, tabs: list[TabInfo]) -> None:
        """Remember the tab inventory, including who owns each tab (§6)."""
        with self._guard:
            data = self._read()
            data["tabs"] = [t.to_dict() for t in tabs]
            self._write(data)

    def close_agent_tabs(self, tab_ids: list[str]) -> dict[str, Any]:
        """Close only agent-created tabs, by id.

        There is deliberately no ``close --all`` (§6). A tab that is not in the inventory
        or is owned by the user is left alone and reported as such.
        """
        with self._guard:
            data = self._read()
            tabs = list(data.get("tabs", []) or [])
        kept, closed, refused = [], [], []
        for tab in tabs:
            tid = str(tab.get("tab_id") or "")
            if tid not in tab_ids:
                kept.append(tab)
                continue
            if str(tab.get("owner") or "") != "agent":
                refused.append(tab)          # a user tab is never ours to close
                kept.append(tab)
                continue
            closed.append(tab)
        with self._guard:
            data = self._read()
            data["tabs"] = kept
            self._write(data)
        return {"closed": closed, "kept": kept, "refused_user_tabs": refused,
                "closed_all": False}

    def mark_unconfirmed(self, reason: str) -> dict[str, Any]:
        """After an unknown outcome: reconcile, never blind-retry (§5, BR-09)."""
        with self._guard:
            data = self._read()
            data["state"] = "unconfirmed"
            data["unconfirmed_reason"] = str(reason)[:300]
            self._write(data)
        return self.discover()


# --------------------------------------------------------------------------- #
# the backend seam: what actually talks to a browser
# --------------------------------------------------------------------------- #


class BrowserLayer:
    """The §4 interface: nine typed operations over one session manager.

    Everything a caller may ask for goes through here, so the rules of §4/§5/§6 hold for
    every path — not just for the one the first caller happened to use. Each method returns
    a :class:`BrowserResult` that names the *actual* backend it talked to.

    The layer refuses by name what the backend does not support. That is the difference
    between "offered" and "claimed": an operation that is listed but not implemented answers
    ``unsupported`` rather than quietly doing something else.
    """

    def __init__(self, sessions: SessionManager, backend: BrowserBackend,
                 *, run_id: str = "", session_id: str = "", deadline: float = 0.0,
                 cancelled: Callable[[], bool] | None = None) -> None:
        self.sessions = sessions
        self.backend = backend
        self.run_id = run_id
        self.session_id = session_id
        self.deadline = float(deadline)
        self._cancelled = cancelled
        self._started = time.monotonic()

    # -- helpers --------------------------------------------------------

    def _result(self, operation: str, **kw: Any) -> BrowserResult:
        kw.setdefault("run_id", self.run_id)
        kw.setdefault("session_id", self.session_id)
        kw.setdefault("backend", getattr(self.backend, "kind", BackendKind.UNKNOWN).value)
        kw.setdefault("duration_ms", (time.monotonic() - self._started) * 1000.0)
        return BrowserResult(operation=operation, **kw)

    def _preflight(self, operation: str) -> BrowserResult | None:
        """The two checks every operation shares: cancellation and the deadline."""
        if self._cancelled is not None:
            try:
                if self._cancelled():
                    return self._result(operation, ok=False, status="cancelled",
                                        error_class=ErrorClass.CANCELLED,
                                        error="the run was cancelled before this call")
            except Exception:  # noqa: BLE001 — a broken signal must not fake a cancel
                pass
        if self.deadline and time.monotonic() > self.deadline:
            return self._result(operation, ok=False, status="blocked",
                                error_class=ErrorClass.DEADLINE,
                                error="the deadline passed before this call ran")
        cap = self.backend.probe()
        if not cap.available:
            return self._result(operation, ok=False, status="blocked",
                                error_class=ErrorClass.NOT_INSTALLED,
                                error=cap.reason)
        if not cap.supports_op(operation):
            return self._result(operation, ok=False, status="unsupported",
                                error_class=ErrorClass.UNSUPPORTED,
                                error=f"`{operation}` is not offered by backend "
                                      f"`{cap.backend.value}` (declared: "
                                      f"{', '.join(cap.supports) or 'none'})")
        return None

    # -- the nine operations (§4) ---------------------------------------

    def discover(self) -> BrowserResult:
        blocked = self._preflight(Operation.DISCOVER.value)
        if blocked:
            return blocked
        inventory = self.sessions.discover()
        return self._result(
            Operation.DISCOVER.value, ok=True, status="ok",
            preview=f"{inventory['state']} · {inventory['tab_count']} tab(s)",
            data=inventory,
            evidence=[{"ref": "src/skaldir/browser_layer.py:SessionManager.discover",
                       "kind": "code"}],
        )

    def attach(self, *, session_id: str = "", require_existing: bool = True) -> BrowserResult:
        """Connect to an existing session. **No implicit creation** (§4)."""
        blocked = self._preflight(Operation.ATTACH.value)
        if blocked:
            return blocked
        inventory = self.sessions.discover()
        if not inventory["session_id"] or inventory["state"] != "ready":
            if require_existing:
                return self._result(
                    Operation.ATTACH.value, ok=False, status="blocked",
                    error_class=ErrorClass.NOT_CONNECTED,
                    error=("no existing browser session to attach to; `browser.attach` "
                           "never creates one implicitly (§4)"),
                    data=inventory)
        self.session_id = session_id or str(inventory.get("session_id") or self.session_id)
        return self._result(
            Operation.ATTACH.value, ok=True, status="ok", session_id=self.session_id,
            preview=f"attached to {self.session_id}", data=inventory)

    def tabs(self) -> BrowserResult:
        blocked = self._preflight(Operation.TABS.value)
        if blocked:
            return blocked
        inventory = self.sessions.discover()
        tabs = [dict(t) for t in (self.sessions._read().get("tabs") or [])]
        return self._result(
            Operation.TABS.value, ok=True, status="ok",
            preview=f"{len(tabs)} tab(s)",
            data={"tabs": tabs, "agent_tabs": inventory["agent_tabs"],
                  "user_tabs": inventory["user_tabs"]})

    def observe(self, *, tab_id: str = "", kind: str = "dom") -> BrowserResult:
        """One reading, bound to (session, tab, revision) — §5."""
        blocked = self._preflight(Operation.OBSERVE.value)
        if blocked:
            return blocked
        observation = Observation(
            session_id=self.session_id, tab_id=tab_id,
            revision=f"rev-{int(time.time() * 1000)}", at=time.time(), kind=kind, text="")
        return self._result(
            Operation.OBSERVE.value, ok=True, status="ok", tab_id=tab_id,
            preview=f"{kind} observation {observation.revision}",
            data=observation.to_dict())

    def act(self, *, tab_id: str = "", action: str = "", **params: Any) -> BrowserResult:
        blocked = self._preflight(Operation.ACT.value)
        if blocked:
            return blocked
        return self._result(
            Operation.ACT.value, ok=False, status="unsupported",
            tab_id=tab_id,
            error_class=ErrorClass.UNSUPPORTED,
            error=(f"the action {action!r} is not wired to a transport yet; the layer "
                   "refuses rather than guessing (§4: nur tatsächlich unterstützte "
                   "Operationen anbieten)"))

    def read(self, *, tab_id: str = "") -> BrowserResult:
        blocked = self._preflight(Operation.READ.value)
        if blocked:
            return blocked
        return self._result(
            Operation.READ.value, ok=False, status="unsupported", tab_id=tab_id,
            error_class=ErrorClass.UNSUPPORTED,
            error="no content reader is wired to a transport yet")

    def batch(self, steps: list[dict[str, Any]]) -> BrowserResult:
        """A checked sequence with per-step outcomes (§5, BR-07).

        Each step is classified and reported separately: executed, failed and **not
        started** are three different things, and a failure stops the remaining steps
        instead of running them anyway.
        """
        blocked = self._preflight(Operation.BATCH.value)
        if blocked:
            return blocked
        executed, failed, not_started = [], [], []
        for index, step in enumerate(steps):
            name = str(step.get("name") or f"step-{index}")
            try:
                outcome = self.act(tab_id=str(step.get("tab_id") or ""),
                                   action=str(step.get("action") or ""),
                                   **dict(step.get("params") or {}))
            except Exception as exc:  # noqa: BLE001 — a step failure is a reported outcome
                failed.append({"name": name, "error": f"{type(exc).__name__}: {exc}"[:200]})
                not_started.extend(s.get("name", f"step-{i}")
                                   for i, s in enumerate(steps[index + 1:], index + 1))
                break
            if outcome.ok:
                executed.append({"name": name, "status": outcome.status})
            else:
                failed.append({"name": name, "error": outcome.error[:200],
                               "error_class": outcome.error_class.value})
                not_started.extend(str(s.get("name") or f"step-{i}")
                                   for i, s in enumerate(steps[index + 1:], index + 1))
                break
        return self._result(
            Operation.BATCH.value, ok=not failed and not not_started,
            status="verified" if (not failed and not not_started) else "partial",
            preview=(f"{len(executed)} executed, {len(failed)} failed, "
                     f"{len(not_started)} not started"),
            data={"executed": executed, "failed": failed, "not_started": not_started})

    def verify(self, *, expectation: str = "") -> BrowserResult:
        """State is verified independently of an exit code (§8)."""
        blocked = self._preflight(Operation.VERIFY.value)
        if blocked:
            return blocked
        return self._result(
            Operation.VERIFY.value, ok=False, status="unknown",
            error_class=ErrorClass.UNKNOWN,
            error=("no verifier is wired to a transport yet — `unknown` is the honest "
                   "answer and never counts as success (§8)"),
            data={"expectation": expectation})

    def detach(self) -> BrowserResult:
        """Release the connection; never close a foreign browser (§4, BR-13)."""
        inventory = self.sessions.discover()
        self.session_id = ""
        return self._result(
            Operation.DETACH.value, ok=True, status="ok",
            preview="detached; foreign browsers were not closed",
            data={"tabs_left_open": inventory.get("tab_count", 0),
                  "user_tabs_left_open": len(inventory.get("user_tabs") or [])})


class BrowserBackend:
    """The one place that may start or drive a browser.

    Subclasses provide the real transport (agent-browser CLI, the ACT host path, or a
    test double). Everything above this class is transport-independent on purpose: §12
    asks for one shared adapter over the existing ACT/Computer-Use path, not a second
    parallel stack.
    """

    kind: BackendKind = BackendKind.UNKNOWN

    def launch(self, *, task: str = "", run_id: str = "") -> dict[str, Any]:
        raise NotImplementedError

    def probe(self) -> BrowserCapability:
        return BrowserCapability(self.kind, available=False, reason="no probe implemented")


class AgentBrowserBackend(BrowserBackend):
    """agent-browser over its CLI, pinned and versioned (§9.2).

    Nothing here installs anything. When the binary is missing the operations refuse by
    name with the exact reason — a silent download loop is what §9.4 forbids.
    """

    kind = BackendKind.AGENT_BROWSER

    def __init__(self, binary: str = "agent-browser", *, timeout: float = 60.0,
                 root: str | Path | None = None) -> None:
        self.binary = binary
        self.timeout = float(timeout)
        self.root = root

    def probe(self) -> BrowserCapability:
        return agent_browser_probe(binary=self.binary, root=self.root)

    def launch(self, *, task: str = "", run_id: str = "") -> dict[str, Any]:
        cap = self.probe()
        if not cap.available:
            return {"ok": False, "backend": self.kind.value, "detail": cap.reason,
                    "session_id": "", "tabs": []}
        # A real launch belongs to the transport; here we only report that the tool is
        # present so the session manager can record an honest `unconfirmed`/`ready`.
        return {"ok": False, "backend": self.kind.value,
                "detail": ("agent-browser is installed, but no launch verb is wired yet — "
                           "the session stays `unconfirmed` rather than pretending it is "
                           "ready"),
                "session_id": "", "tabs": []}

    def run(self, argv: list[str]) -> dict[str, Any]:
        """Run one agent-browser invocation and report it honestly (§4)."""
        cap = self.probe()
        if not cap.available:
            return {"ok": False, "error_class": ErrorClass.NOT_INSTALLED.value,
                    "detail": cap.reason}
        started = time.monotonic()
        try:
            proc = subprocess.run([self.binary, *argv], capture_output=True, text=True,
                                  timeout=self.timeout, check=False)
        except subprocess.TimeoutExpired:
            return {"ok": False, "error_class": ErrorClass.TIMEOUT.value,
                    "detail": f"`{self.binary} {' '.join(argv)}` exceeded {self.timeout:g}s"}
        except (OSError, subprocess.SubprocessError) as exc:
            return {"ok": False, "error_class": ErrorClass.BACKEND.value,
                    "detail": f"{type(exc).__name__}: {exc}"}
        elapsed = (time.monotonic() - started) * 1000.0
        return {
            "ok": proc.returncode == 0,
            "exit_code": proc.returncode,
            "stdout": (proc.stdout or "")[:20000],
            "stderr": (proc.stderr or "")[:4000],
            "duration_ms": elapsed,
            "error_class": "" if proc.returncode == 0 else ErrorClass.BACKEND.value,
        }
