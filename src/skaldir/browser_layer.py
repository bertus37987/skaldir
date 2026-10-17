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
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "PRODUCT_SESSION_NAME",
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
    "session_state_dir",
]

#: The lock file that makes "one start at a time" true across processes (§6).
SESSION_LOCK_NAME = "browser-session.lock"

#: The agent-browser session the product drives (§6). A named session is what keeps the
#: product's browser apart from whatever the *user* runs under agent-browser's
#: ``default`` session — the user's tabs must never be reachable through this state.
PRODUCT_SESSION_NAME = "skaldir"


def session_state_dir(runtime: str | Path | None = None) -> Path:
    """The ONE browser session state dir — the CLI's resolution, shared by every path.

    Reuse lives in this directory (the session state file *and* the cross-process start
    lock inside it, §6), so a per-run directory would give every run its own lock and its
    own "session": two demands could not merge, and `skaldir browser discover` would
    read a different inventory than the agent path writes — the divergence measured in
    scrutiny m3 round 2. An explicit CLI ``--runtime`` still scopes it deliberately; the default is
    the installation runtime that both entries resolve identically.
    """
    explicit = Path(str(runtime)).expanduser() if runtime else None
    if explicit is not None:
        return explicit / "browser"
    from .registry import default_runtime_dir

    return Path(default_runtime_dir()) / "browser"

#: The operations §4 names. These are an **internal interface**; the plan is explicit that
#: the names do not assert any existing CLI command.
OPERATIONS: tuple[str, ...] = (
    "browser.discover", "browser.attach", "browser.tabs", "browser.observe",
    "browser.act", "browser.read", "browser.batch", "browser.verify", "browser.detach",
)


class ErrorClass(enum.StrEnum):
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


class BackendKind(enum.StrEnum):
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


class Operation(enum.StrEnum):
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
                f"`{binary}` is neither project-local (`node_modules/.bin`) nor on PATH. Install "
                "it pinned (never `latest`), e.g. `npm install --save-dev "
                "agent-browser@<exact version>`, then record the version and its licence "
                "here — §9.2/§9.3"
            ),
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
    * tabs carry ``owner``; ``close_agent_tabs`` never touches a user tab, closes
      through the transport (never a report-only close), and there is no
      ``close --all``.
    """

    def __init__(self, state_dir: str | Path, *, backend: BrowserBackend | None = None) -> None:
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._lock_path = self.state_dir / SESSION_LOCK_NAME
        self._backend = backend
        self._guard = threading.RLock()
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

    def _lock(self, *, blocking: bool = True):
        """Cross-process exclusive lock around a start (§6: "ein Lock parallele Starts").

        ``blocking=False`` is for best-effort writes (state reconciliation while another
        process's start may be in flight): if the lock is contended, the write is skipped
        instead of blocking — and never deadlocks a caller that already holds the lock.
        """

        class _Guard:
            def __init__(self, path: Path, blocking: bool) -> None:
                self.path = path
                self.blocking = blocking
                self.handle: Any = None

            def __enter__(self):
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.handle = open(self.path, "a+", encoding="utf-8")
                try:
                    import fcntl

                    mode = fcntl.LOCK_EX if self.blocking \
                        else fcntl.LOCK_EX | fcntl.LOCK_NB
                    fcntl.flock(self.handle.fileno(), mode)
                except Exception:  # noqa: BLE001 — no fcntl: the thread guard still holds
                    if not self.blocking:
                        self.handle.close()
                        self.handle = None
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

        return _Guard(self._lock_path, blocking)

    # -- the state sequence ---------------------------------------------

    def _liveness_verdict(self, session_id: str) -> tuple[bool | None, str]:
        """The cheap reconciliation question: is the recorded session's browser alive?

        Returns ``(verdict, diagnosis)``. ``None`` means the transport cannot say —
        that is never read as "alive" and never as "dead"; the state keeps its claim
        only with the honest caveat attached.
        """
        if self._backend is None:
            return None, "no backend in this session manager; liveness cannot be measured"
        alive = getattr(self._backend, "session_alive", None)
        if alive is None:
            return None, ""
        try:
            verdict = alive(session_id)
        except Exception as exc:  # noqa: BLE001 — a broken probe is not a verdict
            return None, f"liveness probe failed: {type(exc).__name__}: {exc}"[:300]
        if verdict is True:
            return True, ""
        if verdict is False:
            return False, (
                f"the recorded session '{session_id}' is not live: the backend reports "
                "the browser is gone (session info: browserLaunched=false)")
        return None, "the backend cannot say whether the recorded session is live"

    def _reconcile_dead(self, session_id: str, diagnosis: str) -> dict[str, Any]:
        """Record that a ``ready`` session's browser is actually gone (§6).

        A concurrent ensure holds the start lock and owns the state file meanwhile, so
        the write is best-effort (non-blocking lock): the REPORT is honest either way.
        """
        with self._guard:
            data = self._read()
            if data.get("state") == "ready":
                data["state"] = "unknown"
                data["reconciled_dead_session"] = session_id
                data["reconciled_diagnosis"] = str(diagnosis)[:300]
                data["reconciled_at"] = time.time()
                with self._lock(blocking=False) as handle:
                    if handle is not None:
                        self._write(data)
        return data

    def discover(self) -> dict[str, Any]:
        """What exists right now. Read-only; never creates anything.

        The help text promises "what browser state exists **right now**" — so a recorded
        ``ready`` state that the liveness verdict cannot confirm is reported as it is:
        reconciled to ``unknown`` with the diagnosis named, never a bare ready (measured
        defect: the file kept saying ready after the browser was gone, and the next
        transport call let agent-browser relaunch silently). A liveness check here asks
        ``session info``, which launches nothing.
        """
        with self._guard:
            state = self._read()
        live: bool | None = None
        diagnosis = ""
        session_id = str(state.get("session_id") or "")
        if state.get("state") == "ready" and session_id:
            live, diagnosis = self._liveness_verdict(session_id)
            if live is False:
                state = self._reconcile_dead(session_id, diagnosis)
        elif state.get("state") == "unknown" and state.get("reconciled_diagnosis"):
            live = False
            diagnosis = str(state.get("reconciled_diagnosis") or "")
        return {
            "state": state.get("state", "unknown"),
            "live": live,
            "diagnosis": diagnosis,
            "reconciled_dead_session": str(state.get("reconciled_dead_session") or ""),
            "reconciled_diagnosis": str(state.get("reconciled_diagnosis") or ""),
            "session_id": session_id,
            "backend": state.get("backend", BackendKind.NONE.value),
            "tab_count": len(state.get("tabs", []) or []),
            "agent_tabs": [t for t in (state.get("tabs", []) or []) if t.get("owner") == "agent"],
            "user_tabs": [t for t in (state.get("tabs", []) or []) if t.get("owner") == "user"],
            "last_launch_attempt": state.get("last_launch_attempt", 0.0),
            "starts": int(state.get("starts", 0) or 0),
            "unconfirmed_reason": str(state.get("unconfirmed_reason", "") or ""),
        }

    def record_observed_relaunch(self, *, session_id: str, detail: str = "",
                                 now: float | None = None) -> dict[str, Any]:
        """Record a real launch the product OBSERVED agent-browser performing per-call.

        Measured (0.38.1): every transport call auto-launches a dead session's browser
        and says so in its envelope (``lifecycle.launched: true``, fresh targetId). The
        old state file never heard of it, so ``starts`` — the measuring instrument of
        VAL-WEB-003's "concurrent demands yield exactly ONE start" — silently missed
        real launches. Recording them here keeps ``starts`` a truthful count.

        The recorded tab inventory belonged to the PREVIOUS browser process; the
        relaunched one starts with its own tabs. Keeping the old list made
        ``skaldir browser tabs`` (BrowserLayer.tabs -> ``_read()['tabs']``) report
        phantom tabs of a browser that no longer exists, so the supersession clears
        it — the next real inventory read (post-launch/post-act) repopulates it.
        Ownership is untouched: no entry changes owner, nothing becomes closable
        that was not before (§6/BR-13).
        """
        stamp = time.time() if now is None else float(now)
        with self._guard:
            data = self._read()
            superseded = [t for t in (data.get("tabs") or []) if isinstance(t, dict)]
            data["starts"] = int(data.get("starts", 0) or 0) + 1
            data["last_launch_attempt"] = stamp
            data["last_relaunch_observed"] = {
                "at": stamp,
                "session_id": str(session_id),
                "detail": str(detail or "")[:200],
                "superseded_tabs": len(superseded),
                "superseded_tab_ids": [str(t.get("tab_id") or "")
                                       for t in superseded][:20],
            }
            data["tabs"] = []
            self._write(data)
        return self.discover()

    def note_agent_tab(self, tab_id: str, *, url: str = "", title: str = "",
                       run_id: str = "") -> None:
        """Record a tab the agent itself created (§6).

        Only tabs recorded with ``owner="agent"`` are ever closable by cleanup — a tab
        this manager never recorded stays unowned, which is the conservative side of
        BR-13: an unrecorded tab is treated as the user's.
        """
        with self._guard:
            data = self._read()
            tabs = [t for t in (data.get("tabs") or [])
                    if str(t.get("tab_id") or "") != str(tab_id)]
            tabs.append(TabInfo(tab_id=str(tab_id), url=url, title=title,
                                owner="agent", run_id=run_id).to_dict())
            data["tabs"] = tabs
            self._write(data)

    def refresh_tab_metadata(self, tab_id: str, *, url: str = "",
                             title: str = "") -> bool:
        """Refresh the recorded url/title of an ALREADY inventoried tab — metadata
        only, never ownership (§6/BR-13).

        Measured staleness (2026-10-07): the post-act inventory refresh skipped known
        tab ids, so a recorded tab's url/title stayed frozen at its first note even
        after in-tab navigation — the same honesty class as the relaunch phantoms:
        the state file asserting something that is no longer true. Metadata is not
        ownership: this never changes an entry's ``owner``, so it can never make a
        user tab closable, and an unknown id changes nothing.

        The recorded value always reflects the LATEST observation (m4 scrutiny
        round 1): an empty observed url/title clears the recorded one — the old
        ``if url:``/``if title:`` guards kept a stale value the transport no longer
        reports. Callers pass what the transport actually returned.
        """
        with self._guard:
            data = self._read()
            tabs = list(data.get("tabs") or [])
            updated = False
            for tab in tabs:
                if str(tab.get("tab_id") or "") != str(tab_id):
                    continue
                new_url, new_title = str(url), str(title)
                if new_url != str(tab.get("url") or ""):
                    tab["url"] = new_url
                    updated = True
                if new_title != str(tab.get("title") or ""):
                    tab["title"] = new_title
                    updated = True
            if updated:
                data["tabs"] = tabs
                self._write(data)
            return updated

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
                # Single liveness check via discover() — avoids the double-probe race
                # where verdict was True and a second discover() saw False.
                disc = self.discover()
                verdict = disc.get("live")
                diagnosis = str(disc.get("diagnosis") or "")
                if verdict is True:
                    return {"action": "reused", **disc, "live": True,
                            "task": task, "run_id": run_id}
                if verdict is None:
                    return {"action": "reused", **disc, "live": None,
                            "liveness_note": diagnosis
                            or "liveness could not be measured",
                            "task": task, "run_id": run_id}
                # verdict False: discover() already reconciled the dead session.
                state = self._read()
                current = "unknown"
            if current == "unconfirmed":
                # §6: a start timeout leads to diagnosis and state reconciliation, never to
                # the next identical launch. The one honest reconciliation question is
                # "is the recorded session actually alive?" — a positive answer lifts the
                # state WITHOUT a launch; a negative answer has *diagnosed* the session
                # dead, so clearing the stuck state (and creating a new one below) is a
                # concluded reconciliation, not a blind retry of the unknown. When the
                # backend cannot say (verdict None), the state stays `unconfirmed`.
                session_id = str(state.get("session_id") or "")
                verdict: bool | None = None
                if self._backend is not None:
                    try:
                        verdict = self._backend.session_alive(session_id)
                    except Exception:  # noqa: BLE001 — a broken probe is not a verdict
                        verdict = None
                if verdict is True:
                    data = self._read()
                    data["state"] = "ready"
                    data.pop("unconfirmed_reason", None)
                    self._write(data)
                    return {"action": "reconciled", **self.discover(), "task": task,
                            "run_id": run_id}
                if verdict is False:
                    data = self._read()
                    data["state"] = "unknown"
                    data["reconciled_dead_session"] = session_id
                    self._write(data)
                    state = data
                    current = "unknown"
                else:
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

            # One named agent-browser session per state dir (§6): concurrent "provide
            # browser" demands merge into it, and it is never agent-browser's `default`
            # session, which the user may be driving themselves.
            session = str(state.get("session_id") or "") or PRODUCT_SESSION_NAME
            started = self._backend.launch(task=task, run_id=run_id, session=session)
            self._last_launch_attempt = stamp
            data = {
                "state": "ready" if started.get("ok") else "unconfirmed",
                "session_id": str(started.get("session_id") or session),
                "backend": str(started.get("backend") or BackendKind.AGENT_BROWSER.value),
                "tabs": list(started.get("tabs") or []),
                # the counter is read from the state file inside the lock, so it stays
                # true across processes — an in-memory counter reported 1 per new process.
                "starts": int(state.get("starts", 0) or 0) + 1,
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
        """Close only agent-created tabs, by id — through the transport, not on paper.

        Measured honesty defect (m4 scrutiny round 1): the previous version removed
        agent-owned ids from the recorded inventory and reported them under ``closed``,
        but never called the transport (:meth:`AgentBrowserBackend.close_tab` had no
        call site) — `skaldir browser detach --tab <id>` reported closure while the
        browser tab stayed open. The close now goes through the transport, one named
        tab per call, still ownership-gated (§6/BR-13): only recorded ``owner=agent``
        ids are sent, a user tab is refused, an unknown id is kept, a transport that
        cannot close leaves the tab recorded and reports it under ``close_failed`` —
        the report is only true when the transport actually closed the tab.
        """
        wanted = {str(t) for t in tab_ids}
        with self._guard:
            data = self._read()
            tabs = [t for t in (data.get("tabs") or []) if isinstance(t, dict)]
            # The product drives ONE named session per state dir (§6) — never the
            # user's unnamed `default` agent-browser session.
            session_id = str(data.get("session_id") or "") or PRODUCT_SESSION_NAME
        refused = [
            dict(t) for t in tabs
            if str(t.get("tab_id") or "") in wanted
            and str(t.get("owner") or "") != "agent"
        ]
        to_close = [t for t in tabs
                    if str(t.get("tab_id") or "") in wanted
                    and str(t.get("owner") or "") == "agent"]
        closer = getattr(self._backend, "close_tab", None) \
            if self._backend is not None else None
        closed, close_failed = [], []
        for tab in to_close:
            tid = str(tab.get("tab_id") or "")
            if closer is None:
                close_failed.append({
                    "tab_id": tid,
                    "detail": ("no transport close in this session manager — the tab "
                               "stays recorded and open"),
                })
                continue
            result = closer(tab=tid, session=session_id) or {}
            if result.get("ok"):
                closed.append(tab)
            else:
                close_failed.append({
                    "tab_id": tid,
                    "detail": str(result.get("detail") or result.get("error")
                                  or "the transport did not close the tab")[:300],
                })
        closed_ids = {str(t.get("tab_id") or "") for t in closed}
        kept = [t for t in tabs if str(t.get("tab_id") or "") not in closed_ids]
        with self._guard:
            data = self._read()
            data["tabs"] = kept
            self._write(data)
        return {"closed": closed, "kept": kept, "refused_user_tabs": refused,
                "close_failed": close_failed, "closed_all": False}

    def transport_truth(self, session_id: str = "") -> dict[str, Any]:
        """The transport's REAL current tab inventory — only when reading it cannot
        mutate anything (§6).

        Measured (0.38.1): `tab list` on a dead session AUTO-LAUNCHES the browser, so
        the transport is only asked when the liveness verdict says the session is
        live; otherwise the recorded inventory is the honest fallback and ``source``
        names which one backed the answer (m4 scrutiny round 1: a detach payload's
        ``left_open`` must agree with what is really open, never with a stale count).
        """
        with self._guard:
            sid = str(session_id or "") or str(self._read().get("session_id") or "")
            recorded = [t for t in (self._read().get("tabs") or []) if isinstance(t, dict)]
        verdict: bool | None = None
        if sid:
            verdict, _diagnosis = self._liveness_verdict(sid)
        if verdict is not True or self._backend is None:
            return {"live": verdict, "source": "recorded",
                    "tabs": recorded,
                    "tab_ids": [str(t.get("tab_id") or "") for t in recorded],
                    "tab_count": len(recorded)}
        tabs_fn = getattr(self._backend, "tabs", None)
        if tabs_fn is None:
            return {"live": True, "source": "recorded",
                    "tabs": recorded,
                    "tab_ids": [str(t.get("tab_id") or "") for t in recorded],
                    "tab_count": len(recorded)}
        result = tabs_fn(session=sid) or {}
        raw_tabs = [t for t in (result.get("tabs") or []) if isinstance(t, dict)]
        ids = [str(t.get("tab_id") or t.get("tabId") or "") for t in raw_tabs]
        ids = [i for i in ids if i]
        by_recorded = {str(t.get("tab_id") or ""): dict(t) for t in recorded}
        merged_tabs: list[dict[str, Any]] = []
        for t in raw_tabs:
            tid = str(t.get("tab_id") or t.get("tabId") or "")
            if not tid:
                continue
            rec = by_recorded.get(tid)
            if rec is not None:
                entry = dict(rec)
                if t.get("url"):
                    entry["url"] = str(t.get("url"))
                if t.get("title"):
                    entry["title"] = str(t.get("title"))
                merged_tabs.append(entry)
            else:
                merged_tabs.append(
                    TabInfo(
                        tab_id=tid,
                        url=str(t.get("url") or ""),
                        title=str(t.get("title") or ""),
                        owner="user",
                        session_id=sid,
                    ).to_dict()
                )
        for tid, rec in by_recorded.items():
            if tid and tid not in ids:
                merged_tabs.append(rec)
        return {"live": True, "source": "transport",
                "tabs": merged_tabs,
                "tab_ids": ids, "tab_count": len(ids)}

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
        truth = self.sessions.transport_truth()
        tabs = list(truth.get("tabs") or [])
        agent_tabs = [t for t in tabs if t.get("owner") == "agent"]
        user_tabs = [t for t in tabs if t.get("owner") == "user"]
        return self._result(
            Operation.TABS.value, ok=True, status="ok",
            preview=f"{len(tabs)} tab(s)",
            data={"tabs": tabs, "agent_tabs": agent_tabs,
                  "user_tabs": user_tabs,
                  "inventory_source": truth.get("source", "recorded")})

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

    def launch(self, *, task: str = "", run_id: str = "", session: str = "") -> dict[str, Any]:
        raise NotImplementedError

    def session_alive(self, session_id: str) -> bool | None:
        """Reconciliation verdict for a recorded session (§5/BR-09).

        ``True`` = the session answers, ``False`` = it is gone, ``None`` = this transport
        cannot say (the manager then stays ``unconfirmed`` — never a launch on a guess).
        """
        return None

    def probe(self) -> BrowserCapability:
        return BrowserCapability(self.kind, available=False, reason="no probe implemented")


def _unwrap_json(text: str) -> tuple[dict[str, Any], str]:
    """Split `--json` output into its envelope and the readable content inside it.

    Returns ``(envelope, content)``. Anything that is not a JSON envelope is returned as
    plain text with an empty envelope — a tool that prints prose is not an error.
    """
    stripped = (text or "").strip()
    if not stripped:
        return {}, ""
    try:
        envelope = json.loads(stripped)
    except json.JSONDecodeError:
        return {}, stripped
    if not isinstance(envelope, dict):
        return {}, stripped
    data = envelope.get("data")
    if isinstance(data, dict) and isinstance(data.get("content"), str):
        return envelope, data["content"]
    return envelope, stripped


def _process_alive(pid: int) -> bool:
    """Whether a process id answers — the last resort of the liveness verdict."""
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _normalize_browser_key(raw_key: str) -> str:
    """Normalize keyboard shortcuts/key names for browser press actions."""
    key = str(raw_key or "").strip()
    if not key:
        return ""
    parts = [p.strip() for p in key.replace("-", "+").split("+") if p.strip()]
    norm_parts = []
    for p in parts:
        low = p.lower()
        if low in ("ctrl", "control"):
            norm_parts.append("Control")
        elif low in ("alt", "option"):
            norm_parts.append("Alt")
        elif low in ("shift",):
            norm_parts.append("Shift")
        elif low in ("cmd", "command", "super", "win", "meta"):
            norm_parts.append("Meta")
        elif low in ("enter", "return"):
            norm_parts.append("Enter")
        elif low in ("esc", "escape"):
            norm_parts.append("Escape")
        elif low in ("space", "spacebar"):
            norm_parts.append("Space")
        elif low in ("tab",):
            norm_parts.append("Tab")
        elif low in ("backspace",):
            norm_parts.append("Backspace")
        else:
            if len(p) == 1 and p.isalpha():
                norm_parts.append(p.lower())
            else:
                norm_parts.append(p.capitalize() if p.islower() else p)
    return "+".join(norm_parts)


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

    #: The typed action vocabulary of `browser.act` (§4) → one agent-browser verb.
    #: Only what the tool actually offers is listed; anything else is refused by name
    #: instead of being silently mapped onto something similar.
    ACTION_VERBS: dict[str, str] = {
        "navigate": "open",
        "goto": "open",
        "open": "open",
        "click": "click",
        "dblclick": "dblclick",
        "type": "type",
        "fill": "fill",
        "press": "press",
        "key": "press",
        "keypress": "press",
        "hotkey": "press",
        "shortcut": "press",
        "hover": "hover",
        "focus": "focus",
        "check": "check",
        "uncheck": "uncheck",
        "select": "select",
        "drag": "drag",
        "upload": "upload",
        "download": "download",
        "scroll": "scroll",
        "scrollintoview": "scrollintoview",
        "wait": "wait",
    }

    def launch(self, *, task: str = "", run_id: str = "",
               session: str = "", url: str = "") -> dict[str, Any]:
        """Start the browser through `agent-browser open`.

        ``open`` without a URL is deliberate: it stages the session without navigating, so
        a caller can set up state first. Never ``close --all`` anywhere in this class —
        BR-13 requires foreign tabs to survive.
        """
        cap = self.probe()
        if not cap.available:
            return {"ok": False, "backend": self.kind.value, "detail": cap.reason,
                    "session_id": "", "tabs": []}
        argv = ["open", url] if url else ["open"]
        argv += self._session_args(session) + ["--json"]
        result = self.run(argv)
        return {
            "ok": bool(result.get("ok")),
            "backend": self.kind.value,
            "detail": result.get("stderr") or result.get("detail") or "",
            "session_id": session,
            "tabs": [],
            "duration_ms": result.get("duration_ms"),
            "raw": result.get("stdout", "")[:2000],
        }

    def _session_args(self, session: str) -> list[str]:
        return ["--session", session] if session else []

    def dismiss_consent(self, *, session: str = "") -> bool:
        """Attempt to dismiss standard cookie/consent modals (Google, YouTube, CMPs)."""
        labels = (
            "Reject all",
            "Reject the use of cookies and other data for the purposes described",
            "Reject",
            "Alle ablehnen",
            "Ablehnen",
            "Accept all",
            "Accept the use of cookies and other data for the purposes described",
            "Accept",
            "Alle akzeptieren",
            "Akzeptieren",
            "I agree",
            "Ich stimme zu",
        )
        for label in labels:
            res = self.run([
                "find", "role", "button", "click", "--name", label,
                *self._session_args(session), "--json",
            ])
            if res.get("ok"):
                return True
        return False

    def act(self, action: Mapping[str, Any], *, session: str = "") -> dict[str, Any]:
        """One typed browser action (§4 `browser.act`).

        The mapping is ``{"kind": ..., "target": ..., "value": ...}``. An unknown kind is
        refused *by name*: mapping it onto a similar verb would execute something the
        caller did not ask for.
        """
        # The vocabulary is checked FIRST: an unknown action is unsupported whether or not
        # the tool is installed. Reporting "not installed" instead would hide the caller's
        # own mistake behind an environment problem.
        kind = str(action.get("kind", "") or "").lower()
        verb = self.ACTION_VERBS.get(kind)
        if verb is None:
            return {
                "ok": False,
                "backend": self.kind.value,
                "error_class": ErrorClass.UNSUPPORTED.value,
                "detail": (f"browser.act: {kind or '(none)'} is not supported by this "
                           f"backend — known kinds: {', '.join(sorted(self.ACTION_VERBS))}"),
            }

        cap = self.probe()
        if not cap.available:
            return {"ok": False, "backend": self.kind.value,
                    "error_class": ErrorClass.NOT_INSTALLED.value, "detail": cap.reason}

        target = str(action.get("target", "") or action.get("selector", "") or "")
        value = action.get("value", action.get("text", ""))
        argv = [verb]
        if verb in ("open", "goto"):
            argv.append(str(action.get("url", "") or target))
        elif verb == "press":
            key_raw = str(value or action.get("key", "") or "")
            if not key_raw and target.lower() not in ("body", "window", "document", "html", ""):
                key_raw = target
            argv.append(_normalize_browser_key(key_raw) or key_raw)
        elif verb == "scroll":
            argv += [str(value or "down")]
            if action.get("px"):
                argv.append(str(action["px"]))
        elif verb == "drag":
            argv += [target, str(value)]
        elif verb == "wait":
            argv.append(target or str(value or "1000"))
        elif verb in ("type", "fill"):
            argv += [target, str(value)]
        elif verb == "select":
            argv += [target, str(value)]
        elif verb in ("upload", "download"):
            argv += [target, str(value)]
        else:
            argv.append(target)

        argv += self._session_args(session) + ["--json"]
        result = self.run(argv)
        err_msg = (
            (result.get("stderr") or "")
            + " "
            + (result.get("stdout") or "")
            + " "
            + (result.get("detail") or "")
        ).lower()
        if (
            not result.get("ok")
            and ("is covered by" in err_msg or "cb-header" in err_msg or "consent" in err_msg)
        ):
            if self.dismiss_consent(session=session):
                result = self.run(argv)
        if verb in ("open", "goto") and result.get("ok"):
            opened_url = str(action.get("url", "") or target).lower()
            if "youtube.com" in opened_url or "google.com" in opened_url:
                self.dismiss_consent(session=session)
        envelope, _ = _unwrap_json(result.get("stdout") or "")
        return {
            "ok": bool(result.get("ok")),
            "backend": self.kind.value,
            "error_class": result.get("error_class", ""),
            "detail": result.get("stderr") or result.get("detail") or "",
            "duration_ms": result.get("duration_ms"),
            "action": {"kind": kind, "verb": verb, "target": target},
            "preview": (result.get("stdout") or "")[:400],
            "relaunched": self._relaunch_flag(envelope),
        }

    def snapshot(self, *, interactive: bool = True, session: str = "") -> dict[str, Any]:
        """`browser.snapshot`: accessibility / interactive elements tree with element refs."""
        cap = self.probe()
        if not cap.available:
            return {"ok": False, "backend": self.kind.value,
                    "error_class": ErrorClass.NOT_INSTALLED.value, "detail": cap.reason}
        argv = ["snapshot"]
        if interactive:
            argv.append("-i")
        argv += self._session_args(session) + ["--json"]
        result = self.run(argv)
        envelope, content = _unwrap_json(result.get("stdout") or "")
        if envelope.get("success") is False:
            return {
                "ok": False,
                "backend": self.kind.value,
                "error_class": ErrorClass.BACKEND.value,
                "detail": str(envelope.get("error") or "agent-browser snapshot failed")[:300],
            }
        return {
            "ok": bool(result.get("ok")),
            "backend": self.kind.value,
            "error_class": result.get("error_class", ""),
            "content": content[:12000],
            "preview": content[:400],
            "duration_ms": result.get("duration_ms"),
            "relaunched": self._relaunch_flag(envelope),
            "detail": result.get("stderr") or "",
        }

    def read(self, *, url: str = "", session: str = "") -> dict[str, Any]:
        """`browser.read` (§4): rendered content with URL, time and source.

        Omitting ``url`` reads the rendered DOM of the active tab — the rendered state,
        which is what the requirement asks for (not a raw HTTP fetch).
        """
        cap = self.probe()
        if not cap.available:
            return {"ok": False, "backend": self.kind.value,
                    "error_class": ErrorClass.NOT_INSTALLED.value, "detail": cap.reason}
        argv = ["read"] + ([url] if url else []) + self._session_args(session) + ["--json"]
        result = self.run(argv)
        text = (result.get("stdout") or "")
        # `--json` wraps the page in an envelope: {"success", "data": {"content", "url",
        # "status", "source", ...}}. Unwrapping it is what makes `browser.read` return the
        # selective page content the requirement asks for instead of a JSON blob.
        envelope, content = _unwrap_json(text)
        data = envelope.get("data") if isinstance(envelope.get("data"), dict) else {}
        if envelope.get("success") is False:
            return {
                "ok": False, "backend": self.kind.value,
                "error_class": ErrorClass.BACKEND.value,
                "detail": str(envelope.get("error") or "agent-browser reported failure")[:300],
            }
        low_content = content.lower()
        if (
            "before you continue to youtube" in low_content
            or "vor dem fortfahren zu youtube" in low_content
        ):
            if self.dismiss_consent(session=session):
                reread = self.run(
                    ["read"] + ([url] if url else [])
                    + self._session_args(session) + ["--json"]
                )
                env2, cont2 = _unwrap_json(reread.get("stdout") or "")
                if env2.get("success") is not False and cont2:
                    envelope, content = env2, cont2
                    data = envelope.get("data") if isinstance(envelope.get("data"), dict) else {}
        return {
            "ok": bool(result.get("ok")),
            "backend": self.kind.value,
            "error_class": result.get("error_class", ""),
            "url": str(data.get("finalUrl") or data.get("url") or url),
            "status": data.get("status"),
            "read_at": time.time(),
            "source": f"agent-browser read ({data.get('source') or 'rendered'})",
            "content_type": data.get("contentType", ""),
            "truncated": bool(data.get("truncated", False)),
            "content": content[:8000],
            "preview": content[:400],
            "duration_ms": result.get("duration_ms"),
            "relaunched": self._relaunch_flag(envelope),
            "detail": result.get("stderr") or "",
        }

    def tabs(self, *, session: str = "") -> dict[str, Any]:
        """List the tabs of ONE session. Read-only; it never closes anything (BR-13).

        The real verb is ``tab list`` (measured 0.38.1: the bare ``tabs`` is refused as
        ``Unknown command``); the envelope carries ``data.tabs`` with stable ``tabId``s.
        """
        result = self.run(["tab", "list", *self._session_args(session), "--json"])
        envelope, _ = _unwrap_json(result.get("stdout") or "")
        data = envelope.get("data") if isinstance(envelope.get("data"), dict) else {}
        listed = [
            {
                "tab_id": str(t.get("tabId") or ""),
                "url": str(t.get("url") or ""),
                "title": str(t.get("title") or ""),
                "active": bool(t.get("active", False)),
            }
            for t in (data.get("tabs") or []) if isinstance(t, dict)
        ]
        return {"ok": bool(result.get("ok")), "backend": self.kind.value,
                "tabs": listed, "preview": (result.get("stdout") or "")[:800],
                "relaunched": self._relaunch_flag(envelope),
                "detail": result.get("stderr") or ""}

    def session_info(self, session: str = "") -> dict[str, Any]:
        """`session info` for ONE session — the cheap, non-mutating liveness reading.

        Measured (0.38.1, live 2026-10-06): the envelope carries ``data.active``,
        ``data.pid`` and ``data.runtime.browserLaunched``. After a graceful close AND
        after an out-of-band SIGKILL of the browser, ``active`` (and ``session list``)
        can stay stale for a moment while ``runtime.browserLaunched`` already says
        ``false`` — so the verdict reads that field, not the stale flags. Asking for it
        launches nothing.
        """
        result = self.run(["session", "info", *self._session_args(session), "--json"])
        envelope, _ = _unwrap_json(result.get("stdout") or "")
        data = envelope.get("data") if isinstance(envelope.get("data"), dict) else {}
        return {
            "ok": bool(result.get("ok")) and envelope.get("success") is not False,
            "backend": self.kind.value,
            "data": data,
            "detail": result.get("stderr") or (envelope.get("error") or "") or "",
        }

    def session_alive(self, session_id: str) -> bool | None:
        """The reconciliation verdict for a recorded session — measured, not assumed.

        The old verdict ran ``tab list``, which agent-browser answers by AUTO-LAUNCHING
        a dead session's browser (measured: ``lifecycle.launched: true`` with a fresh
        targetId) — a "verdict" that silently launched the thing it was asked about.
        Now the verdict reads ``session info`` (launches nothing):

        * ``False`` — the session's browser is gone: a stale daemon entry
          (``active: true``) with ``runtime.browserLaunched: false``, a session that
          never existed, or a dead pid (out-of-band kill);
        * ``True`` — the browser answers;
        * ``None`` — only when this transport cannot say (binary unusable, call failed).
        """
        if not session_id:
            return False
        cap = self.probe()
        if not cap.available:
            return None
        info = self.session_info(session=session_id)
        if not info.get("ok"):
            return None
        data = info.get("data") or {}
        if not data.get("active"):
            return False
        runtime = data.get("runtime") if isinstance(data.get("runtime"), dict) else None
        if runtime is not None and runtime.get("browserLaunched") is False:
            return False
        pid = data.get("pid") or (runtime or {}).get("backgroundPid")
        if pid and not _process_alive(int(pid)):
            return False
        return True

    @staticmethod
    def _relaunch_flag(envelope: Mapping[str, Any]) -> bool:
        """Whether THIS call auto-launched the session's browser (§6 honesty).

        Measured (0.38.1): every transport call — ``tab list``, ``open``, ``read`` —
        carries ``data.lifecycle.launched: true`` exactly when it started the browser
        itself; while the browser is already up the same envelope says
        ``launched: false, reused: true``. Surfacing it lets the product RECORD a real
        launch instead of letting the start counter miss it.
        """
        data = envelope.get("data") if isinstance(envelope.get("data"), dict) else {}
        lifecycle = data.get("lifecycle") if isinstance(data.get("lifecycle"), dict) else {}
        return bool(lifecycle.get("launched") or lifecycle.get("relaunchedBrowser"))

    def close_tab(self, *, tab: str = "", session: str = "") -> dict[str, Any]:
        """Close ONE tab by name — never ``--all`` (BR-13: foreign tabs survive).

        The real verb is ``tab close <id>`` (measured 0.38.1); the bare ``close`` closes
        the whole session browser, and ``--all`` closes EVERY session — both are out of
        bounds for a tab-scoped close.
        """
        if not tab:
            return {"ok": False, "backend": self.kind.value,
                    "error_class": ErrorClass.UNSUPPORTED.value,
                    "detail": "close_tab needs a named tab — there is no 'close everything' here"}
        if tab in ("--all", "*"):
            return {"ok": False, "backend": self.kind.value,
                    "error_class": ErrorClass.UNSUPPORTED.value,
                    "detail": ("refused: closing every tab would also close the user's own "
                               "tabs (BR-13)")}
        result = self.run(["tab", "close", tab, *self._session_args(session), "--json"])
        return {"ok": bool(result.get("ok")), "backend": self.kind.value,
                "detail": result.get("stderr") or ""}

    def run(self, argv: list[str]) -> dict[str, Any]:
        """Run one agent-browser invocation and report it honestly (§4)."""
        cap = self.probe()
        if not cap.available:
            return {"ok": False, "error_class": ErrorClass.NOT_INSTALLED.value,
                    "detail": cap.reason}
        # Resolve the binary exactly as the probe does. Using ``self.binary`` raw meant
        # the probe found the project-local install while every *call* failed with
        # FileNotFoundError — measured, not assumed.
        binary = _resolve_binary(self.binary, root=self.root) or self.binary
        started = time.monotonic()
        try:
            proc = subprocess.run([binary, *argv], capture_output=True, text=True,
                                  timeout=self.timeout, check=False)
        except subprocess.TimeoutExpired:
            return {"ok": False, "error_class": ErrorClass.TIMEOUT.value,
                    "detail": f"`{binary} {' '.join(argv)}` exceeded {self.timeout:g}s"}
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
