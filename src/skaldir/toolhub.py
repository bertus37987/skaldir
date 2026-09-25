"""Local tool registry + public-hub manifest builder (DoD §12).

Two clearly separated halves — never mixed:

- **Local registry (built here, read-only):** discovers generated tools
  (``generated_tools/*/manifest.yaml``), testing tools (their validation
  state), installed skills (frontmatter) and configured MCP servers, and
  searches them deterministically (word-overlap, no LLM) so an existing
  proven capability is preferred over rebuilding it.
- **Public hub (designed, never automatic):** ``build_hub_manifest()``
  renders the shareable manifest with security metadata and the real
  verification state — eligibility requires status/validation AND that
  the tool was actually executed against its defined tests. Publishing is
  a separate, explicit, user-approved step; this module never publishes
  and never touches the network. Treasury stays the sole secret authority:
  manifests carry ``secret://`` handles only, and plaintext is refused.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

_MANIFEST_KEYS = (
    "name",
    "version",
    "type",
    "purpose",
    "status",
    "network",
    "filesystem",
    "required_secrets",
    "dependencies",
    "risk_level",
    "created_by",
    "created_at",
    "last_tested_at",
    "validation",
)

_SECRETISH = re.compile(r"(?i)(api[_-]?key|token|secret|password|bearer)\s*[:=]\s*\S+")


def _load_yaml(path: Path) -> dict:
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _skill_entries(skills_dir: Path, limit: int = 200) -> list[dict]:
    entries: list[dict] = []
    if not skills_dir.is_dir():
        return entries
    for path in sorted(skills_dir.rglob("SKILL.md"))[:limit]:
        text = ""
        try:
            text = path.read_text(encoding="utf-8", errors="replace")[:4000]
        except Exception:
            continue
        name = path.parent.name
        description = ""
        match = re.search(r"(?m)^description:\s*(.+)$", text)
        if match:
            description = match.group(1).strip().strip('"').strip("'")[:200]
        if text.startswith("---"):
            fm = re.search(r"(?m)^name:\s*(.+)$", text)
            if fm:
                name = fm.group(1).strip().strip('"').strip("'") or name
        entries.append(
            {
                "name": name,
                "source": "skill",
                "type": "skill",
                "purpose": description,
                "path": str(path),
                "permissions": {"network": [], "filesystem": "skill-defined"},
                "secrets": [],
                "verification": {"state": "workflow"},  # skills are procedures, not proofs
                "reliability": {},
            }
        )
    return entries


def _mcp_entries(config_path: Path) -> list[dict]:
    data = _load_yaml(config_path)
    servers = data.get("mcp_servers") or data.get("mcpServers") or data.get("mcp") or {}
    if not isinstance(servers, dict):
        return []
    out = []
    for name, spec in servers.items():
        command = ""
        if isinstance(spec, dict):
            command = str(spec.get("command") or spec.get("url") or "")
        elif isinstance(spec, list):
            command = " ".join(str(p) for p in spec[:3])
        out.append(
            {
                "name": str(name),
                "source": "mcp",
                "type": "mcp",
                "purpose": f"MCP server ({command[:80]})",
                "path": str(config_path),
                "permissions": {"network": ["mcp transport"], "filesystem": "server-defined"},
                "secrets": [],
                "verification": {"state": "external"},  # the host owns MCP health
                "reliability": {},
            }
        )
    return out


def _usage_stats(runtime: Path) -> dict[str, dict]:
    """Per-tool reliability from the real append-only ledger (§11 metrics)."""
    try:
        from .toolusage import usage_stats

        return usage_stats(runtime)
    except Exception:
        return {}


def discover(
    runtime: str | Path,
    *,
    skills_dir: str | Path | None = None,
    config_path: str | Path | None = None,
) -> list[dict]:
    """All installed capabilities in one read-only, deterministic view."""
    runtime = Path(runtime)
    entries: list[dict] = []
    usage = _usage_stats(runtime)
    tools_root = runtime / "generated_tools"
    if tools_root.is_dir():
        for manifest_path in sorted(tools_root.glob("*/manifest.yaml")):
            manifest = _load_yaml(manifest_path)
            name = str(manifest.get("name") or manifest_path.parent.name)
            validation = manifest.get("validation") or {}
            verification = {
                "state": str(validation.get("status") or "unvalidated"),
                "checks": validation.get("checks") or [],
            }
            network = (manifest.get("network") or {}).get("allowed_domains") or []
            filesystem = (manifest.get("filesystem") or {}).get("access") or ""
            entries.append(
                {
                    "name": name,
                    "source": "generated",
                    "type": str(manifest.get("type") or "cli"),
                    "version": str(manifest.get("version") or ""),
                    "purpose": str(manifest.get("purpose") or ""),
                    "status": str(manifest.get("status") or ""),
                    "path": str(manifest_path.parent),
                    "permissions": {"network": list(network), "filesystem": filesystem},
                    "secrets": [str(s) for s in (manifest.get("required_secrets") or [])],
                    "verification": verification,
                    "reliability": {
                        "uses": int((usage.get(name) or {}).get("runs") or 0),
                        "passes": int((usage.get(name) or {}).get("passes") or 0),
                        "fails": int((usage.get(name) or {}).get("fails") or 0),
                    },
                    "provenance": {
                        "created_by": manifest.get("created_by"),
                        "created_at": manifest.get("created_at"),
                        "last_tested_at": manifest.get("last_tested_at"),
                    },
                }
            )
    default_skills = Path.home() / ".hermes" / "skills"
    entries.extend(_skill_entries(Path(skills_dir) if skills_dir else default_skills))
    if config_path:
        entries.extend(_mcp_entries(Path(config_path)))
    return entries


def search(entries: list[dict], query: str, *, limit: int = 8) -> list[dict]:
    """Deterministic relevance: word overlap over name/purpose/type (no LLM)."""
    from .research import _terms

    wanted = set(_terms(query, limit=12))
    if not wanted:
        return []
    scored = []
    for entry in entries:
        haystack = " ".join(
            [entry.get("name", ""), entry.get("purpose", ""), entry.get("type", "")]
        ).lower()
        overlap = sum(1 for term in wanted if term in haystack)
        if overlap:
            scored.append((overlap, entry))
    scored.sort(key=lambda pair: -pair[0])
    return [entry for _score, entry in scored[:limit]]


def _tree_hash(directory: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(directory.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            digest.update(path.relative_to(directory).as_posix().encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


#: A tool name is a *directory name* under ``generated_tools`` — nothing else.
_TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def tool_dir(runtime: str | Path, name: str) -> Path:
    """The tool's own directory, contained in ``generated_tools`` — or refuse.

    ``name`` arrives from a caller (``skaldir tools hub --manifest <name>``) and
    from manifests. ``Path(base) / name`` alone follows ``../..`` straight out of the
    tool tree, so ``--manifest ../../../etc`` would read any ``manifest.yaml`` on the
    host; resolving and comparing against the base also refuses a symlinked entry.
    """
    base = (Path(runtime) / "generated_tools").resolve()
    text = str(name or "").strip()
    if not _TOOL_NAME_RE.fullmatch(text) or ".." in text:
        raise ValueError(f"not a usable tool name: {name!r}")
    directory = (base / text).resolve()
    if directory.parent != base:
        raise ValueError(f"tool name leaves the tool tree: {name!r}")
    return directory


def build_hub_manifest(runtime: str | Path, name: str) -> dict:
    """Render the shareable manifest for one local tool — or refuse.

    Eligibility: status 'ready' AND (for testing tools) validation
    'validated' — 'verified' must mean the tool was installed and executed
    against defined tests, never merely code-reviewed. Plaintext secrets
    are refused outright (Treasury handles only). This creates a FILE; it
    never publishes.
    """
    directory = tool_dir(runtime, name)
    manifest_path = directory / "manifest.yaml"
    if not manifest_path.is_file():
        raise ValueError(f"unknown tool: {name}")
    manifest = _load_yaml(manifest_path)
    if manifest.get("status") != "ready":
        raise ValueError(f"tool {name} is not ready (status={manifest.get('status')!r})")
    validation = manifest.get("validation") or {}
    if manifest.get("type") == "testing_tool" and validation.get("status") != "validated":
        raise ValueError(f"testing tool {name} has no validation evidence")
    blob = json.dumps(manifest, ensure_ascii=False)
    if _SECRETISH.search(blob):
        raise ValueError("manifest contains a plaintext-looking secret — Treasury handles only")
    hub = {
        "manifest_version": 1,
        "name": name,
        "version": str(manifest.get("version") or ""),
        "type": str(manifest.get("type") or "cli"),
        "purpose": str(manifest.get("purpose") or ""),
        "provenance": {
            "created_by": manifest.get("created_by"),
            "created_at": manifest.get("created_at"),
            "source": "godsv-local-registry",
        },
        "permissions": {
            "network": list((manifest.get("network") or {}).get("allowed_domains") or []),
            "filesystem": (manifest.get("filesystem") or {}).get("access") or "",
        },
        "secret_handles": [str(s) for s in (manifest.get("required_secrets") or [])],
        "verification": {
            "state": str(validation.get("status") or "unvalidated"),
            "checks": validation.get("checks") or [],
            "last_tested_at": manifest.get("last_tested_at"),
            "trust": "godsv-verified" if validation.get("status") == "validated" else "local",
        },
        "compatibility": {"runtime": "python>=3.11", "platform": "linux"},
        "checksum_sha256": _tree_hash(manifest_path.parent),
        "publish": {"automatic": False, "requires": "explicit owner approval"},
    }
    for key in _MANIFEST_KEYS:
        if key in manifest and key not in hub:
            hub.setdefault("extras", {})[key] = manifest[key]
    return hub
