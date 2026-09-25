"""Capability gaps → pause → tool build → redirect → resume (spec §6/§17/§18).

The main agent (or the user) records a capability request — usually by running
``skaldir tools request …`` mid-session. The plugin notices the new request
on the agent's tool call, raises a ``capability_gap`` signal, and the *same*
pause/research/redirect pipeline runs the **ToolBuilder** as its researcher.
When the build is done, the redirect the agent receives is tiny and actionable
(``tool_created`` payload): capability, tool, how to use it, constraints —
never the build transcript (spec §19).

This module deliberately reuses everything: ``ToolBuilder`` for the pipeline,
``ResearchResult`` as the runner return type, ``VoicePause`` as the controller.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .llm import LLMClient
from .research import ResearchResult
from .toolbuilder import ToolBuilder, ToolBuildRequest

STATUS_OPEN = "open"
STATUS_FULFILLED = "fulfilled"
STATUS_FAILED = "failed"

_CMD_HINT = re.compile(r"\b(skaldir|godsv)\b.*\btools\b.*\brequest\b")
_SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,40}")


@dataclass
class CapabilityRequest:
    """One capability the agent is missing, with its build material."""

    capability: str
    reason: str = ""
    requested_by: str = "main-agent"
    session_id: str = ""
    project_id: str = ""  # §7: stable id of the requesting project
    kind: str = "auto"
    name: str = ""
    source_code: str = ""
    source_path: str = ""
    tests_code: str = ""
    tests_path: str = ""
    module: str = ""
    entrypoint: str = ""
    sample_input: dict = field(default_factory=dict)
    required_secrets: list[str] = field(default_factory=list)
    allowed_domains: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    research_notes: str = ""
    status: str = STATUS_OPEN
    request_id: str = ""
    validation_cases: list[tuple[str, bool]] = field(default_factory=list)
    created: float = 0.0
    result_tool: str = ""
    error: str = ""
    # --- B6 (manifest v2): the docker request, forwarded verbatim ------------
    # Without these the docker stages of the builder were unreachable from the
    # capability surface (P0 finding §1.3.9): a request could ask for a tool but
    # never for a container. All defaults keep a local python tool a local tool.
    runtime: str = ""  # "" = local python tool, "docker" = pinned image
    image: str = ""  # tagged source the build pulls and pins by digest
    image_digest: str = ""  # the pin the build must produce/verify
    mounts: list[dict] = field(default_factory=list)
    resource_limits: dict = field(default_factory=dict)
    healthcheck: dict = field(default_factory=dict)

    def to_tool_request(self) -> ToolBuildRequest:
        return ToolBuildRequest(
            capability=self.capability,
            reason=self.reason,
            requested_by=self.requested_by,
            kind=self.kind if self.kind in ("mcp", "cli", "testing_tool") else "auto",
            name=self.name,
            image=str(self.image or ""),
            source_code=self.source_code,
            source_path=self.source_path,
            tests_code=self.tests_code,
            tests_path=self.tests_path,
            module=self.module,
            entrypoint=self.entrypoint,
            sample_input=dict(self.sample_input),
            research_notes=self.research_notes,
            required_secrets=list(self.required_secrets),
            allowed_domains=list(self.allowed_domains),
            dependencies=list(self.dependencies),
            session_id=self.session_id,
            project_id=self.project_id,
            validation_cases=list(self.validation_cases or []),
            runtime=str(self.runtime or ""),
            image_digest=str(self.image_digest or ""),
            mounts=[dict(m) for m in (self.mounts or [])],
            resource_limits=dict(self.resource_limits or {}),
            healthcheck=dict(self.healthcheck or {}),
        )


class CapabilityStore:
    """Append-only JSONL of capability requests (raw, inspectable, no DB)."""

    def __init__(self, runtime: str | Path) -> None:
        self.path = Path(runtime) / "capability_requests.jsonl"

    def add(self, request: CapabilityRequest) -> CapabilityRequest:
        if not request.request_id:
            request.request_id = f"cap-{uuid.uuid4().hex[:10]}"
        request.created = request.created or time.time()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(request), ensure_ascii=False, default=str) + "\n")
        return request

    def all(self) -> list[CapabilityRequest]:
        if not self.path.exists():
            return []
        requests: list[CapabilityRequest] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            known = {f for f in CapabilityRequest.__dataclass_fields__}
            requests.append(CapabilityRequest(**{k: v for k, v in payload.items() if k in known}))
        return requests

    def newest_open(self, *, session_id: str = "") -> CapabilityRequest | None:
        """Latest state per request wins (the file is append-only)."""
        latest: dict[str, CapabilityRequest] = {}
        for request in self.all():
            latest[request.request_id] = request
        for request in reversed(list(latest.values())):
            if request.status != STATUS_OPEN:
                continue
            if session_id and request.session_id and request.session_id != session_id:
                continue
            return request
        return None

    def update(self, request: CapabilityRequest) -> None:
        """Append the updated record; ``all()`` resolves to the latest state."""
        self.add(request)


def requests_runtime_dir(store) -> Path:
    return Path(store.path).parent if getattr(store, "path", None) else Path(".")


def looks_like_request_command(command: str) -> bool:
    """Cheap filter: did the agent just run ``skaldir tools request …``?"""
    return bool(_CMD_HINT.search(command or ""))


class ToolBuildRunner:
    """A ``research_runner`` that builds a tool instead of researching a topic."""

    def __init__(
        self,
        *,
        runtime: str | Path,
        builder: ToolBuilder | None = None,
        store: CapabilityStore | None = None,
        llm: LLMClient | None = None,
    ) -> None:
        # §1.8: the DRAFT runs on the caller's already-configured model client. The caller is
        # whoever builds this runner — the plugin session (its host-owned `ctx.llm`), a test, or
        # the CLI. Without one the builder reports the gap honestly instead of inventing code,
        # which is what `tools fulfill` does standalone (measured 17.09.: the in-session builder
        # was constructed without any client although the session had one).
        self.runtime = Path(runtime)
        self.llm = llm
        self.builder = builder if builder is not None else ToolBuilder(self.runtime, llm=llm)
        self.store = store if store is not None else CapabilityStore(self.runtime)

    def run(
        self,
        *,
        session_id: str,
        base_step: int,
        trigger_kind: str,
        trigger_fp: str,
        problem: str,
    ) -> ResearchResult:
        result = ResearchResult(
            research_id=f"toolbuild-{uuid.uuid4().hex[:10]}",
            session_id=session_id,
            base_step=base_step,
            trigger_kind=trigger_kind,
            trigger_fp=trigger_fp,
            problem=problem,
        )
        started = time.monotonic()
        request = self.store.newest_open(session_id=session_id) or self.store.newest_open()
        if request is None:
            result.status = "failed"
            result.direction = (
                "No open capability request found — first run `skaldir tools request "
                "--capability … --source …`."
            )
            result.avoid = "Starting a capability build without a specification."
            result.uncertainty = "Without a request (source/tests) the builder builds nothing."
            result.duration_ms = int((time.monotonic() - started) * 1000)
            return result

        self.builder.session_id = str(session_id or "")
        build = self.builder.build(request.to_tool_request())
        result.evidence = [
            f"capability-request {request.request_id}: {request.capability}",
            "build {}: {}".format(
                build.get("build_id", "?"),
                "ok" if build.get("ok") else build.get("error", "failed"),
            ),
        ]
        if build.get("ok"):
            request.status = STATUS_FULFILLED
            request.result_tool = str(build.get("tool", ""))
            self.store.update(request)
            from .tools import ToolStore

            tool_store = ToolStore(self.runtime / "tools.db")
            try:
                spec = tool_store.get(request.result_tool)
            finally:
                tool_store.close()
            if spec is not None:
                from .toolbuilder import ToolBuilder as _Builder

                payload = _Builder.redirect_payload(
                    spec,
                    request.to_tool_request(),
                    wrapper=str(build.get("wrapper", "")),
                    kind=str(build.get("kind", "")),
                )
                created = payload["tool_created"]
                result.direction = (
                    f"Capability available: '{created['tool']}' ({created['kind']}) "
                    "is now registered and tested. "
                    f"usage: {created['how_to_use']} "
                    f"Constraints: {'; '.join(created['important_constraints'])}"
                )
                result.avoid = "Re-building the capability manually or ignoring the tool."
                result.uncertainty = (
                    "A real API smoke against the live service is still pending "
                    "(mock/gate tests only)."
                )
                result.status = "ok"
            else:
                result.status = "failed"
                result.direction = (
                    "Build was ok, but the tool is missing from the registry — "
                    "check the registry (tools list / tools builds)."
                )
                result.avoid = "Assuming the tool is available without checking the registry."
                result.uncertainty = "Registry state inconsistent."
        else:
            request.status = STATUS_FAILED
            request.error = str(build.get("error", "build failed"))
            self.store.update(request)
            # Still a usable outcome: the build ran, the gates said why not, and
            # the agent gets a concrete next step. That is a real "direction" —
            # abandoning the pause would leave the agent waiting for nothing.
            result.status = "ok"
            result.direction = (
                f"Capability build failed ({request.error[:160]}). Next step: "
                "check source/tests (gates) or renew the request with a better specification."
            )
            result.avoid = "Repeating the failed build unchanged."
            result.uncertainty = "Without green gates no tool is released."
        result.duration_ms = int((time.monotonic() - started) * 1000)
        tokens_blob = result.direction + result.avoid + "".join(result.evidence)
        result.tokens_est = max(1, len(tokens_blob) // 4)
        return result


class CapabilityAwareRunner:
    """Dispatch: capability gaps build tools; everything else researches."""

    def __init__(self, *, primary, tool_builder: ToolBuildRunner) -> None:
        self.primary = primary
        self.tool_builder = tool_builder

    def run(self, *, session_id, base_step, trigger_kind, trigger_fp, problem):
        if trigger_kind == "capability_gap":
            return self.tool_builder.run(
                session_id=session_id,
                base_step=base_step,
                trigger_kind=trigger_kind,
                trigger_fp=trigger_fp,
                problem=problem,
            )
        return self.primary.run(
            session_id=session_id,
            base_step=base_step,
            trigger_kind=trigger_kind,
            trigger_fp=trigger_fp,
            problem=problem,
        )

    def __getattr__(self, item):
        return getattr(self.primary, item)


# -- testing tools -> completion gate (P0-2) ----------------------------------


def _testing_tool_runner(runtime: Path, name: str, module_path: Path):
    """Build a completion check runner for one generated testing tool."""

    def run(target):
        import sys
        import time as _time

        from skaldir.completion import RunEvidence
        from skaldir.execution import RunPolicy, ToolRun, run_local
        from skaldir.gates import NO_SANDBOX_EXIT, parse_testing_result
        from skaldir.toolusage import record_usage

        target = str(target)
        started = _time.time()
        try:
            # shared execution boundary (§1.1): sandboxed, argv-fixed, target as
            # read-only bind; without a sandbox the run is not-certifiable and
            # never executed — which parse_testing_result must see as a failure
            result = run_local(
                ToolRun(
                    argv=[sys.executable, str(module_path), target],
                    workdir=str(module_path.parent),
                    timeout=60.0,
                    extra_binds=[target],
                ),
                policy=RunPolicy(require_sandbox=True),
            )
            if result.status == "not-certifiable":
                code = NO_SANDBOX_EXIT
                output = result.detail
            else:
                code = result.exit_code if result.exit_code is not None else NO_SANDBOX_EXIT
                output = (result.stdout + result.stderr) or result.detail
            ok, reason = parse_testing_result(code, output)
            detail = ((f"{reason} | " if reason else "") + output[:200])[:220]
        except Exception as exc:  # a crashing tool is a failure, never a pass
            ok = False
            detail = f"{type(exc).__name__}: {exc}"[:200]
        try:
            record_usage(
                runtime,
                name,
                ok=ok,
                error="" if ok else detail[:120],
                duration_ms=int((_time.time() - started) * 1000),
            )
        except Exception:
            pass  # metrics are best effort — never break the gate
        return RunEvidence(
            name=name,
            check_id=name,
            # only runner-executed evidence may certify a requirement (P1):
            # this ran a fixed command through the sandboxed runner.
            source="runner",
            status="passed" if ok else "failed",
            ok=ok,
            cmd=f"python {module_path.name} {target}",
            detail=detail,
            ts=_time.time(),
        )

    return run


def register_testing_tools(runtime: str | Path) -> list[str]:
    """Register validated generated testing tools as completion checks.

    Idempotent and failure-tolerant: an unreadable manifest is skipped, a
    broken module stays unregistered. Returns the names registered now.
    """
    import yaml

    from skaldir.completion import register_check

    runtime = Path(runtime)
    base = runtime / "generated_tools"
    registered: list[str] = []
    if not base.is_dir():
        return registered
    for tool_dir in sorted(path for path in base.iterdir() if path.is_dir()):
        manifest_path = tool_dir / "manifest.yaml"
        if not manifest_path.is_file():
            continue
        try:
            manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        if str(manifest.get("type") or "") != "testing_tool":
            continue
        # resolve the module via the manifest — never force module == dirname
        # (audit P1: a 'checker/' tool dir with module: custom_impl was skipped)
        module_name = str(manifest.get("module") or "").strip() or tool_dir.name
        module_path = tool_dir / f"{module_name}.py"
        if not module_path.is_file():
            continue
        if str(manifest.get("status") or "") not in ("ready", "approved"):
            continue
        validation = manifest.get("validation") or {}
        if str(validation.get("status") or "") != "validated":
            continue
        register_check(
            tool_dir.name, _testing_tool_runner(runtime, tool_dir.name, module_path)
        )
        registered.append(tool_dir.name)
    return registered


def testing_tool_stats(runtime: str | Path) -> dict:
    """§26 summary: reusable-proof stats across the generated testing tools.

    Runs/passes/fails come from the append-only usage ledger — a fail is a
    gate run that refused (the tool detected the target as broken). False
    passes/false fails are structurally excluded by the validation gate
    (BROKEN→FAIL and WORKING→PASS proven, ``validation.status=validated``);
    tools without that proof are not registered for the completion gate and
    are counted here as unvalidated.
    """
    runtime = Path(runtime)
    from skaldir.toolusage import usage_stats

    usage = usage_stats(runtime)
    tools: dict[str, dict] = {}
    base = runtime / "generated_tools"
    for tool_dir in sorted(p for p in base.glob("*") if p.is_dir()):
        name = tool_dir.name
        manifest: dict = {}
        try:
            import yaml

            manifest = (
                yaml.safe_load((tool_dir / "manifest.yaml").read_text(encoding="utf-8")) or {}
            )
        except Exception:
            manifest = {}
        validation = manifest.get("validation") or {}
        stats = usage.get(name) or {}
        tools[name] = {
            "capability": str(manifest.get("capability") or "")[:120],
            "runs": int(stats.get("runs", 0)),
            "passes": int(stats.get("passes", 0)),
            "fails": int(stats.get("fails", 0)),
            "bugs_detected": int(stats.get("fails", 0)),
            "validation": str(validation.get("status") or "unvalidated"),
            "last_error": str(stats.get("last_error") or ""),
        }
    totals = {
        "tools": len(tools),
        "validated": sum(1 for t in tools.values() if t["validation"] == "validated"),
        "runs": sum(t["runs"] for t in tools.values()),
        "passes": sum(t["passes"] for t in tools.values()),
        "fails": sum(t["fails"] for t in tools.values()),
        "bugs_detected": sum(t["bugs_detected"] for t in tools.values()),
        "false_pass_policy": "structurally excluded by validation (BROKEN->FAIL, WORKING->PASS)",
    }
    return {"totals": totals, "tools": tools}


# ---------------------------------------------------------------- tool scopes
# §6/§7: ONE registry, orthogonal properties. Scope (project|global) decides
# VISIBILITY only — never an extra permission. New learned capabilities are
# project-scoped by default; global means user-wide in this installation, not
# published. Name conflicts resolve by (project beats global, then higher
# version) and are SURFACED when unresolvable — no silent overwrite. A record
# without an explicit scope keeps its provable context and is never folded
# into a project or promoted to global by accident (migration rule §7).

SCOPE_PROJECT = "project"
SCOPE_GLOBAL = "global"
SCOPE_LEGACY = "legacy"


def _version_key(value) -> int:
    """Monotone ordering key. Manifests carry ints (promotions) or the
    builder's semver strings ("0.1.0") — expand dots to major*1e6+minor*1e3+patch."""

    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    total = 0
    for index, part in enumerate(str(value or "").strip().split(".")[:3]):
        digits = "".join(ch for ch in part if ch.isdigit())
        total += (int(digits or 0) % 1000) * (1000 ** (2 - index))
    return total


def _normalize_remote(url: str) -> str:
    import re

    text = str(url or "").strip()
    if text.endswith(".git"):
        text = text[:-4]
    text = re.sub(r"^[a-z+]+://", "", text)
    text = re.sub(r"^[^@/]+@", "", text)
    head, _, tail = text.partition("/")
    if ":" in head and not tail:
        # scp-style host:path
        text = head.replace(":", "/", 1)
    elif ":" in head:
        text = head.replace(":", "/", 1) + "/" + tail
    return text.lower()


def project_id_for(path: str | Path) -> str:
    """Stable project id for a workspace.

    Folder names are not identities (renames, worktrees, clones): prefer the
    git remote, then the SHARED git dir (a worktree and its main checkout
    share it), and only then the canonical path — the weakest tier, marked as
    such so callers can tell.
    """

    root = Path(path)
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=5,
        )
        remote = (out.stdout or "").strip()
        if out.returncode == 0 and remote:
            return "remote:" + _normalize_remote(remote)
        out = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--git-common-dir"],
            capture_output=True, text=True, timeout=5,
        )
        common = (out.stdout or "").strip()
        if out.returncode == 0 and common:
            common_path = Path(common)
            if not common_path.is_absolute():
                common_path = root / common_path
            return "gitdir:" + str(common_path.resolve())
    except Exception:
        pass
    try:
        return "path:" + str(root.resolve())
    except Exception:
        return "path:" + str(root)


def load_tool_entries(runtime: str | Path) -> list[dict]:
    """Every generated tool with its manifest facts (scope/version/health)."""

    import yaml

    runtime = Path(runtime)
    base = runtime / "generated_tools"
    entries: list[dict] = []
    if not base.is_dir():
        return entries
    for tool_dir in sorted(path for path in base.iterdir() if path.is_dir()):
        manifest_path = tool_dir / "manifest.yaml"
        if not manifest_path.is_file():
            continue
        try:
            manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        scope = str(manifest.get("scope") or "").strip().lower()
        entries.append(
            {
                "name": tool_dir.name,
                "type": str(manifest.get("type") or ""),
                "status": str(manifest.get("status") or ""),
                "version": _version_key(manifest.get("version")),
                "version_raw": manifest.get("version"),
                "scope": scope if scope in (SCOPE_PROJECT, SCOPE_GLOBAL) else SCOPE_LEGACY,
                "project_id": str(manifest.get("project_id") or ""),
                "path": str(tool_dir),
                # B6/Manifest v2: a container capability carries its runtime facts
                # (the pinned image digest is the part that may never drift)
                "runtime": str(manifest.get("runtime") or ""),
                "image": str(manifest.get("image") or ""),
                "image_digest": str(manifest.get("image_digest") or ""),
            }
        )
    return entries


def visible_tools(
    runtime: str | Path, project_id: str = "", *, include_legacy: bool = False
) -> list[dict]:
    """Tools visible for a project: its own tools plus the global ones.

    - ``project`` scope: only its project (stable id match)
    - ``global`` scope: every context
    - ``legacy`` (no scope): only the explicit "show everything" view, never
      an automatic selection in a project context
    """

    out: list[dict] = []
    for entry in load_tool_entries(runtime):
        scope = entry["scope"]
        if scope == SCOPE_GLOBAL:
            out.append(entry)
        elif scope == SCOPE_PROJECT:
            if project_id and entry["project_id"] == project_id:
                out.append(entry)
        elif include_legacy:
            out.append(entry)
    return out


def resolve_tool_names(entries: list[dict]) -> tuple[list[dict], list[dict]]:
    """Pick one entry per name; return (picks, conflicts).

    Precedence: project-scope beats global, then the higher version wins.
    A remaining tie is a CONFLICT returned to the caller — the registry never
    overwrites silently (§7).
    """

    groups: dict[str, list[dict]] = {}
    for entry in entries:
        groups.setdefault(str(entry.get("name") or ""), []).append(entry)
    picks: list[dict] = []
    conflicts: list[dict] = []
    for name, group in sorted(groups.items()):
        if not name:
            continue
        if len(group) == 1:
            picks.append(group[0])
            continue

        def rank(entry: dict) -> tuple[int, int]:
            return (1 if entry.get("scope") == SCOPE_PROJECT else 0, int(entry.get("version") or 0))

        ordered = sorted(group, key=rank, reverse=True)
        best = ordered[0]
        ties = [entry for entry in ordered if rank(entry) == rank(best)]
        if len(ties) > 1:
            conflicts.append({"name": name, "candidates": ties})
        else:
            picks.append(best)
    return picks, conflicts


def slug_capability(capability: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", (capability or "capability").lower()).strip("_")
    return (slug or "capability")[:40]

# ------------------------------------------------------------ promotion §7
# project -> global is an evidenced, versioned act: validated negative tests,
# explicit evidence from >=2 independent (foreign) project contexts, secret://
# handles only. Anything missing keeps the tool a PROJECT tool with candidate
# status — nothing flips silently, and the promotion writes a NEW manifest
# version so running callers keep the version they started with.


def _bump_version(value):
    """New MAJOR version, same family as the previous value (int or semver)."""

    if isinstance(value, bool):
        return 1
    if isinstance(value, (int, float)):
        return int(value) + 1
    parts = [
        int("".join(ch for ch in part if ch.isdigit()) or 0)
        for part in str(value or "0.1.0").split(".")[:3]
    ]
    while len(parts) < 3:
        parts.append(0)
    return f"{parts[0] + 1}.0.0"


def _load_manifest(path: str | Path) -> dict:
    import yaml

    try:
        return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def promotion_report(runtime: str | Path) -> dict:
    """§7: which project-scoped tools are promotion candidates and what is missing."""

    rows: list[dict] = []
    for entry in load_tool_entries(runtime):
        if entry["scope"] != SCOPE_PROJECT:
            continue
        manifest = _load_manifest(Path(entry["path"]) / "manifest.yaml")
        validated = str((manifest.get("validation") or {}).get("status") or "") == "validated"
        blockers = []
        if not entry["project_id"]:
            blockers.append("no project context recorded")
        if not validated:
            blockers.append("negative tests missing (validation.status != validated)")
        rows.append(
            {
                "name": entry["name"],
                "project_id": entry["project_id"],
                "version": entry["version_raw"],
                "candidate": not blockers,
                "blockers": blockers,
            }
        )
    return {"tools": rows}


def promote_tool(
    runtime: str | Path,
    name: str,
    *,
    evidence_projects: list[str] | None = None,
    promoted_by: str = "owner",
) -> dict:
    """Promote a validated project tool to a global version (§7).

    ``evidence_projects`` are the OTHER project contexts in which this tool
    proved itself; fewer than two independent ones refuse the promotion.
    """

    runtime = Path(runtime)
    entry = next((e for e in load_tool_entries(runtime) if e["name"] == name), None)
    if entry is None or entry["scope"] != SCOPE_PROJECT:
        return {"promoted": False, "blockers": ["tool is not a project-scoped tool"]}
    manifest_path = Path(entry["path"]) / "manifest.yaml"
    manifest = _load_manifest(manifest_path)
    blockers: list[str] = []
    validated = str((manifest.get("validation") or {}).get("status") or "") == "validated"
    if not validated:
        blockers.append("negative tests missing (validation.status != validated)")
    foreign = sorted({str(p).strip() for p in (evidence_projects or []) if str(p).strip()})
    foreign = [p for p in foreign if p != entry["project_id"]]
    if len(foreign) < 2:
        blockers.append("evidence from >=2 independent project contexts required")
    secrets = [str(item) for item in manifest.get("required_secrets") or []]
    if any(not item.startswith("secret://") for item in secrets):
        blockers.append("plaintext secrets must become secret:// handles first")
    if blockers:
        return {"promoted": False, "candidate": True, "name": name, "blockers": blockers}
    previous = manifest.get("version")
    manifest.update(
        {
            "scope": SCOPE_GLOBAL,
            "version": _bump_version(previous),
            "promoted_from": {
                "project_id": entry["project_id"],
                "evidence_projects": foreign,
                "at": time.time(),
                "promoted_by": promoted_by,
            },
        }
    )
    import yaml

    manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    return {
        "promoted": True,
        "name": name,
        "previous_version": previous,
        "version": manifest["version"],
    }
