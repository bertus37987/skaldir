"""Skaldir Agent desktop panel backend (read-only) — mounted at /api/plugins/godsv/.

All reads are read-only sqlite against the runtime dir ($HERMES_HOME/godsv):
events, pauses, and — if present — the test-money ledger. This module never
touches the vault, never writes, and returns no secrets. Self-contained
(stdlib only) so it also imports in minimal test environments; FastAPI is
imported defensively because it only exists inside the Hermes gateway.
"""


from __future__ import annotations

# --- product package resolution (defect fix, measured in the built app) ---------
# The engine process that loads this plugin runs in the Hermes engine venv, which does
# NOT contain `godsv`: the package lives in the product checkout (`src/godsv`). Until now
# that only worked when the launching shell happened to export PYTHONPATH. Without it the
# engine logged `Failed to load plugin 'godsv': No module named 'godsv'` and every panel
# route raised ModuleNotFoundError (measured in one app session: 636 module errors, 127
# HTTP 500s, 0 events recorded). Resolve the package from our own location — the plugin
# and the package ship from the same checkout; no ambient variable is trusted.
import sys as _sys
from pathlib import Path as _Path

for _parent in _Path(__file__).resolve().parents:
    _candidate = _parent / "src" / "godsv" / "__init__.py"
    if _candidate.is_file():
        _src = str(_candidate.parent.parent)
        if _src not in _sys.path:
            _sys.path.insert(0, _src)
        break
# -------------------------------------------------------------------------------

import hmac
import json
import logging
import os
import re
import sqlite3
import time
from pathlib import Path

_log = logging.getLogger("godsv.panel")

try:  # the gateway provides fastapi; local environments may not
    from fastapi import APIRouter, HTTPException, Request
except ImportError:  # pragma: no cover - exercised in minimal venvs only
    APIRouter = None  # type: ignore[assignment]
    HTTPException = None  # type: ignore[assignment,misc]
    Request = None  # type: ignore[assignment,misc]

PANEL_VERSION = "1.0.0"


def runtime_dir() -> Path:
    home = os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")
    return Path(home) / "godsv"


def _connect(path: Path) -> sqlite3.Connection | None:
    if not path.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2.0)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error:
        return None


def _kind_counts(conn: sqlite3.Connection) -> dict[str, int]:
    try:
        rows = conn.execute(
            "SELECT kind, COUNT(*) AS n FROM events GROUP BY kind ORDER BY n DESC"
        ).fetchall()
    except sqlite3.Error:
        return {}
    return {str(row["kind"]): int(row["n"]) for row in rows}


def _recent_events(conn: sqlite3.Connection, limit: int) -> list[dict]:
    try:
        rows = conn.execute(
            "SELECT ts, session_id, step, kind, payload FROM events"
            " ORDER BY ts DESC, rowid DESC LIMIT ?",
            (limit,),
        ).fetchall()
    except sqlite3.Error:
        return []
    events: list[dict] = []
    for row in rows:
        payload: object = {}
        try:
            payload = json.loads(row["payload"])
        except (ValueError, TypeError):
            payload = str(row["payload"])[:200]
        events.append(
            {
                "ts": row["ts"],
                "session": row["session_id"],
                "step": row["step"],
                "kind": row["kind"],
                "payload": payload,
            }
        )
    return events


def _pauses(conn: sqlite3.Connection, limit: int) -> list[dict]:
    try:
        rows = conn.execute("SELECT * FROM pauses ORDER BY rowid DESC LIMIT ?", (limit,)).fetchall()
    except sqlite3.Error:
        return []
    return [dict(row) for row in rows]


def _allowance(conn: sqlite3.Connection) -> dict:
    """Aggregated test-money bookkeeping (no payment rails exist)."""
    try:
        rows = conn.execute(
            "SELECT status, COALESCE(SUM(amount), 0.0) AS total, COUNT(*) AS n"
            " FROM spends GROUP BY status"
        ).fetchall()
    except sqlite3.Error:
        return {"mode": "testgeld", "by_status": {}, "counts": {}}
    return {
        "mode": "testgeld",
        "by_status": {str(row["status"]): round(float(row["total"]), 2) for row in rows},
        "counts": {str(row["status"]): int(row["n"]) for row in rows},
    }


def summary_payload(
    *,
    events_db: Path | None = None,
    pauses_db: Path | None = None,
    treasury_db: Path | None = None,
    limit: int = 25,
) -> dict:
    """Aggregate panel data; every source is optional and failure-tolerant."""
    base = events_db.parent if events_db else runtime_dir()
    events_db = events_db or base / "events.db"
    pauses_db = pauses_db or base / "pauses.db"
    treasury_db = treasury_db or base / "treasury.db"

    payload: dict = {
        "mode": "testgeld",
        "version": PANEL_VERSION,
        "runtime": str(base),
        "events_present": events_db.exists(),
        "kinds": {},
        "recent": [],
        "pauses": [],
        "allowance": None,
        "voice_mode": os.environ.get("GODSV_MODE") or "shadow",
        "generated_at": time.time(),
    }

    conn = _connect(events_db)
    if conn is not None:
        try:
            payload["kinds"] = _kind_counts(conn)
            payload["recent"] = _recent_events(conn, max(5, min(100, limit)))
        finally:
            conn.close()

    pconn = _connect(pauses_db)
    if pconn is not None:
        try:
            payload["pauses"] = _pauses(pconn, max(5, min(50, limit)))
        finally:
            pconn.close()

    tconn = _connect(treasury_db)
    if tconn is not None:
        try:
            payload["allowance"] = _allowance(tconn)
        finally:
            tconn.close()

    return payload


def _rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict]:
    try:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]
    except sqlite3.Error:
        return []


def memory_payload(*, memory_db: Path | None = None, limit: int = 50) -> dict:
    """Incidents + research notes (read-only; no secrets live in these tables)."""
    memory_db = memory_db or runtime_dir() / "memory.db"
    payload: dict = {"incidents": [], "notes": [], "db_present": memory_db.exists()}
    conn = _connect(memory_db)
    if conn is None:
        return payload
    try:
        payload["incidents"] = _rows(
            conn,
            "SELECT id, signature, title, kind, status, confidence, occurrences,"
            " first_seen, last_seen FROM incidents ORDER BY last_seen DESC LIMIT ?",
            (limit,),
        )
        payload["notes"] = _rows(
            conn,
            "SELECT id, topic, claim, source_type, source_url, trust, status, created"
            " FROM notes ORDER BY created DESC LIMIT ?",
            (limit,),
        )
    finally:
        conn.close()
    return payload


def skills_payload(*, memory_db: Path | None = None, limit: int = 50) -> dict:
    """Skill candidates with lifecycle status + usage counters."""
    memory_db = memory_db or runtime_dir() / "memory.db"
    payload: dict = {"skills": [], "db_present": memory_db.exists()}
    conn = _connect(memory_db)
    if conn is None:
        return payload
    try:
        payload["skills"] = _rows(
            conn,
            "SELECT id, name, title, status, occurrences, sessions, applied, succeeded,"
            " updated FROM skill_candidates ORDER BY updated DESC LIMIT ?",
            (limit,),
        )
    except sqlite3.Error:
        pass
    finally:
        conn.close()
    return payload


def treasury_payload(*, treasury_db: Path | None = None, limit: int = 50) -> dict:
    """Test-money bookkeeping only: spend requests + grants. Never vault material."""
    treasury_db = treasury_db or runtime_dir() / "treasury.db"
    payload: dict = {"spends": [], "grants": [], "db_present": treasury_db.exists()}
    conn = _connect(treasury_db)
    if conn is None:
        return payload
    try:
        payload["spends"] = _rows(
            conn,
            "SELECT id, amount, currency, merchant, status, reason, created"
            " FROM spends ORDER BY created DESC LIMIT ?",
            (limit,),
        )
        payload["grants"] = _rows(
            conn,
            "SELECT id, handle, status, created, decided FROM grants"
            " ORDER BY created DESC LIMIT ?",
            (limit,),
        )
    finally:
        conn.close()
    return payload


def _audit(kind: str, payload: dict) -> bool:
    """Append a panel action to the event store (audit trail for mutations).

    Auditing must never break the action it records — but it must also never
    fail *silently* (Block 1 / SEC-06). The old bare ``except Exception:
    pass`` hid a real bug: the action name was passed as the event *kind*,
    which is not a registered kind, so ``EventStore.append`` raised on EVERY
    call and the audit trail stayed empty while the panel reported nothing.

    The entry is now stored as a registered ``system`` event carrying
    ``panel_action`` — the trail survives and stays queryable. Failures are
    logged (WARNING, action + exception type only, never secret values) and
    reported via the return value so a caller/test can see them.
    """
    try:
        from godsv.events import EventStore

        runtime = runtime_dir()
        runtime.mkdir(parents=True, exist_ok=True)
        store = EventStore(runtime / "events.db")
        try:
            store.append("panel", 0, "system", {"panel_action": kind, **(payload or {})})
        finally:
            store.close()
    except Exception as exc:  # audit failure is visible, never a silent pass
        _log.warning(
            "panel audit append failed (action=%s): %s: %s",
            kind,
            type(exc).__name__,
            exc,
        )
        return False
    return True


# --------------------------------------------------------------------------- #
# models: provider catalog + roles + live test                                #
# --------------------------------------------------------------------------- #


def _config_models() -> dict:
    """model.default/provider + fallback_providers from Hermes' config.yaml."""
    home = os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")
    path = Path(home) / "config.yaml"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {"provider": "", "model": "", "fallbacks": []}
    out: dict = {"provider": "", "model": "", "fallbacks": []}
    lines = text.splitlines()
    in_model = False
    in_fallback = False
    current: dict = {}
    for raw in lines:
        line = raw.rstrip()
        if line.startswith("model:"):
            in_model, in_fallback = True, False
            continue
        if line.startswith("fallback_providers:"):
            in_model, in_fallback = False, True
            continue
        if not line.startswith((" ", "\t")):
            in_model = in_fallback = False
            continue
        stripped = line.strip()
        if in_model and ":" in stripped:
            key, _, value = stripped.partition(":")
            value = value.strip().strip("'\"")
            if key == "provider":
                out["provider"] = value
            elif key == "default":
                out["model"] = value
        if in_fallback:
            if stripped.startswith("- "):
                if current:
                    out["fallbacks"].append(current)
                current = {}
                stripped = stripped[2:].strip()
            if ":" in stripped:
                key, _, value = stripped.partition(":")
                current[key.strip()] = value.strip().strip("'\"")
    if current:
        out["fallbacks"].append(current)
    return out


def _provider_catalog() -> list[dict]:
    """Real providers + live model lists (Hermes' own provider_models_cache.json)."""
    home = os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")
    catalog: dict[str, dict] = {}
    try:
        cache = json.loads((Path(home) / "provider_models_cache.json").read_text(encoding="utf-8"))
        for slug, entry in cache.items():
            if isinstance(entry, dict) and isinstance(entry.get("models"), list):
                catalog[slug] = {
                    "slug": slug,
                    "models": [str(m) for m in entry["models"]],
                    "fetched_at": entry.get("at"),
                    "source": "hermes-cache",
                }
    except (OSError, ValueError, AttributeError):
        pass
    cfg = _config_models()
    for extra in [cfg.get("provider")] + [f.get("provider") for f in cfg.get("fallbacks", [])]:
        if extra and extra not in catalog:
            catalog[extra] = {"slug": extra, "models": [], "fetched_at": None, "source": "config"}
    return sorted(catalog.values(), key=lambda row: row["slug"])


def _strictness_payload() -> dict:
    """Current voice strictness + options (P4: real runtime setting)."""
    from godsv.pause import STRICTNESS_LEVELS, load_strictness

    try:
        level = load_strictness(runtime_dir())
    except Exception:
        level = "standard"
    return {"level": level, "options": list(STRICTNESS_LEVELS)}


def models_payload(*, roles: dict | None = None) -> dict:
    """Everything the Models UI needs — names only, never credentials."""
    payload: dict = {"providers": _provider_catalog(), "hermes": _config_models()}
    if roles is None:
        try:
            from godsv.modelroles import roles_payload

            payload.update(roles_payload())
        except Exception:  # pragma: no cover — panel stays usable without the package
            payload.update({"roles": {}, "roles_file": "", "known_roles": []})
    else:
        payload.update(roles)
    payload["strictness"] = _strictness_payload()
    return payload


def set_role_payload(role: str, provider: str, model: str, reasoning: str | None = None) -> dict:
    """Validate + persist one role; unknown provider/model is flagged, not blocked."""
    from godsv.modelroles import ROLES, save_role

    if role not in ROLES:
        raise ValueError(f"unknown role: {role}")
    catalog = {row["slug"]: row for row in _provider_catalog()}
    if catalog and provider not in catalog:
        raise ValueError(f"unknown provider: {provider!r} (not in Hermes' provider registry)")
    provider_known = provider in catalog
    models_for_provider = catalog[provider]["models"] if provider_known else []
    model_listed = provider_known and (not models_for_provider or model in models_for_provider)
    roles = save_role(role, provider=provider, model=model, reasoning=reasoning)
    _audit(
        "model_role_set",
        {"role": role, "provider": provider, "model": model, "reasoning": reasoning or "",
         "provider_known": provider_known, "model_listed": model_listed},
    )
    return {
        "ok": True,
        "role": role,
        "roles": roles,
        "provider_known": provider_known,
        "model_listed": model_listed,
    }


def test_model_payload(provider: str, model: str) -> dict:
    """Small connectivity + tool-use smoke test via Hermes' own LLM runtime.

    Never returns credentials — only the model names, a tiny reply excerpt,
    latency and capability verdicts.
    """
    provider = (provider or "").strip()
    model = (model or "").strip()
    if not provider or not model:
        raise ValueError("provider and model required")
    result: dict = {
        "provider": provider,
        "model": model,
        "ok": False,
        "authentication": "unknown",
        "response": False,
        "tool_call": "unknown",
        "latency_ms": None,
        "error": "",
    }
    try:
        from agent.auxiliary_client import call_llm
    except Exception as exc:  # pragma: no cover — gateway always ships agent/
        result["error"] = f"llm runtime unavailable: {type(exc).__name__}"
        _audit("model_test", {**result})
        return result
    started = time.time()
    try:
        response = call_llm(
            messages=[{"role": "user", "content": "Reply with exactly: OK"}],
            provider=provider,
            model=model,
            max_tokens=10,
            timeout=25,
        )
        text = _response_text(response)
        result["latency_ms"] = int((time.time() - started) * 1000)
        result["ok"] = True
        result["authentication"] = True
        result["response"] = bool(text)
        result["reply_excerpt"] = text[:40]
    except Exception as exc:  # noqa: BLE001 — report any provider failure as-is
        result["latency_ms"] = int((time.time() - started) * 1000)
        message = str(exc)
        lowered = message.lower()
        auth_markers = ("401", "auth", "api key", "unauthorized", "credential")
        if any(marker in lowered for marker in auth_markers):
            result["authentication"] = False
        result["error"] = message[:300]
        _audit("model_test", {k: v for k, v in result.items() if k != "reply_excerpt"})
        return result
    # optional tool-use probe (best effort; provider formats differ)
    try:
        probe = call_llm(
            messages=[
                {"role": "user", "content": "Call the ping tool with note='hi'. Nothing else."}
            ],
            provider=provider,
            model=model,
            max_tokens=60,
            timeout=25,
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "ping",
                        "description": "Ping the service",
                        "parameters": {
                            "type": "object",
                            "properties": {"note": {"type": "string"}},
                        },
                    },
                }
            ],
        )
        result["tool_call"] = bool(_response_tool_calls(probe))
    except Exception as exc:  # noqa: BLE001 — tool probe is additive
        result["tool_call"] = "unknown"
        result["tool_probe_error"] = str(exc)[:120]
    _audit("model_test", {k: v for k, v in result.items() if k != "reply_excerpt"})
    return result


def _response_text(response: object) -> str:
    """Best-effort text from a provider chat response (dict or object)."""
    message: object = None
    if isinstance(response, dict):
        try:
            message = response["choices"][0]["message"]
        except (KeyError, IndexError, TypeError):
            message = None
    if message is None:
        choices = getattr(response, "choices", None)
        if choices:
            message = getattr(choices[0], "message", None)
    if message is None:
        return ""
    if isinstance(message, dict):
        text = message.get("content")
    else:
        text = getattr(message, "content", None)
    return str(text or "").strip()


def _response_tool_calls(response: object) -> list:
    message: object = None
    if isinstance(response, dict):
        try:
            message = response["choices"][0]["message"]
        except (KeyError, IndexError, TypeError):
            message = None
    if message is None:
        choices = getattr(response, "choices", None)
        if choices:
            message = getattr(choices[0], "message", None)
    if isinstance(message, dict):
        calls = message.get("tool_calls")
    else:
        calls = getattr(message, "tool_calls", None)
    return list(calls or [])


# --------------------------------------------------------------------------- #
# treasury: credentials + grants — metadata only, values stay on the host      #
# --------------------------------------------------------------------------- #

_CRED_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,40}")


def _open_treasury():
    """Vault + treasury state for the panel; None when the master key is absent."""
    try:
        from godsv.treasury import TreasuryState, Vault
    except Exception:
        return None
    key = os.environ.get("GODSV_VAULT_KEY")
    if not key:
        return None
    runtime = runtime_dir()
    try:
        return Vault(runtime / "vault.json", key), TreasuryState(runtime / "treasury.db")
    except Exception:
        return None


def credentials_payload() -> dict:
    """Credential + grant metadata for the panel — never a secret value."""
    opened = _open_treasury()
    if opened is None:
        return {
            "available": False,
            "reason": "GODSV_VAULT_KEY not set in the gateway environment",
            "credentials": [],
            "grants": [],
        }
    vault, state = opened
    creds = [info.to_dict() for info in vault.list_handles()]
    grants = state.list_grants()
    used_by: dict[str, list[str]] = {}
    for grant in grants:
        tool_id = grant.get("tool_id")
        if grant["status"] == "approved" and tool_id:
            used_by.setdefault(grant["handle"], [])
            if tool_id not in used_by[grant["handle"]]:
                used_by[grant["handle"]].append(tool_id)
    for cred in creds:
        cred["used_by"] = used_by.get(cred["handle"], [])
    return {"available": True, "credentials": creds, "grants": grants}


def add_credential_payload(
    *, service: str, name: str, secret: str, description: str = ""
) -> dict:
    """Store a credential; the response carries the handle and a mask only."""
    service = (service or "").strip().lower()
    name = (name or "").strip().lower()
    if not _CRED_RE.fullmatch(service) or not _CRED_RE.fullmatch(name):
        raise ValueError("service/name: letters, digits, . _ - only")
    if not secret or len(secret) > 8192:
        raise ValueError("secret must be non-empty (and sane in size)")
    opened = _open_treasury()
    if opened is None:
        raise RuntimeError("vault unavailable: GODSV_VAULT_KEY not set in the gateway environment")
    vault, _state = opened
    handle = vault.add(
        service=service,
        name=name,
        secret=secret,
        scopes=("grant:required", "use"),
        description=(description or "")[:200],
    )
    _audit(
        "treasury_credential_added",
        {"handle": handle, "service": service, "name": name, "scopes": ["grant:required", "use"]},
    )
    return {"ok": True, "handle": handle, "masked": "••••••••"}


def test_credential_payload(handle: str) -> dict:
    """Safe credential self-test (spec §16): decrypt + isolated injection.

    No network call, no grant needed, no plaintext anywhere in the response —
    proves the handle works end-to-end for the vault owner.
    """
    handle = (handle or "").strip()
    if not handle.startswith("secret://") or len(handle) > 128:
        raise ValueError("invalid handle")
    opened = _open_treasury()
    if opened is None:
        raise RuntimeError("vault unavailable: GODSV_VAULT_KEY not set")
    vault, state = opened
    from godsv.treasury import Broker

    result = Broker(vault, state).selftest(handle)
    return {
        "handle": handle,
        "ok": bool(result.ok),
        "denied": bool(result.denied),
        "detail": result.detail,
    }


def revoke_credential_payload(handle: str) -> dict:
    opened = _open_treasury()
    if opened is None:
        raise RuntimeError("vault unavailable: GODSV_VAULT_KEY not set")
    vault, _state = opened
    removed = vault.revoke(handle)
    _audit("treasury_credential_revoked", {"handle": handle, "removed": removed})
    return {"ok": removed, "handle": handle}


def grant_credential_payload(
    *, handle: str, tool_id: str, ttl: int = 3600, purpose: str = ""
) -> dict:
    """Grant a handle to ONE tool (the panel click is the human approval)."""
    tool_id = (tool_id or "").strip()
    if not tool_id or not _CRED_RE.fullmatch(tool_id.replace("/", "-").replace(" ", "-")):
        raise ValueError("tool_id required (letters, digits, . _ -)")
    if not (60 <= int(ttl) <= 30 * 24 * 3600):
        raise ValueError("ttl must be 60..2592000 seconds")
    opened = _open_treasury()
    if opened is None:
        raise RuntimeError("vault unavailable: GODSV_VAULT_KEY not set")
    vault, state = opened
    if vault.describe(handle) is None:
        raise ValueError(f"unknown handle: {handle}")
    grant_id = state.add_grant_request(
        handle, purpose[:200] or "panel grant", int(ttl), tool_id=tool_id
    )
    approved = state.approve_grant(grant_id, by="owner-panel")
    _audit(
        "treasury_grant",
        {"handle": handle, "tool_id": tool_id, "ttl": int(ttl), "grant_id": grant_id},
    )
    return {"ok": approved, "grant_id": grant_id, "handle": handle, "tool_id": tool_id}


def revoke_grant_payload(grant_id: str) -> dict:
    opened = _open_treasury()
    if opened is None:
        raise RuntimeError("vault unavailable: GODSV_VAULT_KEY not set")
    _vault, state = opened
    revoked = state.revoke_grant(grant_id, by="owner-panel")
    _audit("treasury_grant_revoked", {"grant_id": grant_id, "revoked": revoked})
    return {"ok": revoked, "grant_id": grant_id}


# --------------------------------------------------------------------------- #
# tools: installed / generated / build queue / mcp servers                    #
# --------------------------------------------------------------------------- #


def _read_manifest(tool_name: str) -> dict:
    path = runtime_dir() / "generated_tools" / tool_name / "manifest.yaml"
    try:
        import yaml

        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def mcp_servers_payload() -> list[dict]:
    """Configured MCP servers from Hermes' own config (metadata only)."""
    try:
        from hermes_cli.config import load_config_readonly

        cfg = load_config_readonly() or {}
    except Exception:
        return []
    servers = cfg.get("mcp_servers")
    if not isinstance(servers, dict):
        return []
    rows: list[dict] = []
    for name, entry in sorted(servers.items()):
        if not isinstance(entry, dict):
            continue
        if entry.get("command"):
            transport = "stdio"
        elif entry.get("url"):
            transport = "http/sse"
        else:
            transport = "?"
        tools = entry.get("allowed_tools") or entry.get("tools") or []
        rows.append(
            {
                "name": str(name),
                "transport": transport,
                "tool_count": len(tools) if isinstance(tools, list) else None,
                "auth_configured": bool(entry.get("env") or entry.get("headers")),
                "enabled": bool(entry.get("enabled", True)),
            }
        )
    return rows


def tools_payload() -> dict:
    """Tool registry + builder queue + MCP servers for the panel."""
    runtime = runtime_dir()
    try:
        from godsv.toolbuilder import ToolBuilder
        from godsv.tools import ToolStore

        store = ToolStore(runtime / "tools.db")
    except Exception:
        return {
            "installed": [], "generated": [], "queue": [], "mcp_servers": [],
            "error": "tool store unavailable",
        }
    queue = ToolBuilder(runtime).queue(limit=20)
    usage = {}
    try:
        from godsv.toolusage import usage_counts

        usage = usage_counts(runtime)
    except Exception:
        usage = {}
    requests: list[dict] = []
    try:
        from godsv.capability import CapabilityRequest, CapabilityStore

        latest: dict[str, CapabilityRequest] = {}
        for request in CapabilityStore(runtime).all():
            latest[request.request_id] = request  # append-only: letzter Stand gilt
        builds_by_name: dict[str, dict] = {}
        for entry in ToolBuilder(runtime).queue(limit=50):
            key = str(entry.get("name") or "")
            if key and key not in builds_by_name:
                builds_by_name[key] = entry  # queue() liefert newest-first
        for request in reversed(list(latest.values())):
            build = builds_by_name.get(request.name) if request.name else None
            requests.append(
                {
                    "request_id": request.request_id,
                    "capability": request.capability,
                    "reason": request.reason,
                    "requested_by": request.requested_by,
                    "kind": request.kind,
                    "name": request.name,
                    "status": request.status,
                    "result_tool": request.result_tool,
                    "error": request.error,
                    "created": request.created,
                    # handles only — never credential values
                    "required_secrets": list(request.required_secrets),
                    "allowed_domains": list(request.allowed_domains),
                    "build": None
                    if build is None
                    else {
                        "build_id": build.get("build_id", ""),
                        "stage": build.get("stage", ""),
                        "ok": build.get("ok"),
                        "stages": [s.get("stage", "") for s in build.get("stages", [])],
                    },
                }
            )
    except Exception:
        requests = []
    scope_by_name: dict[str, dict] = {}
    promo_by_name: dict[str, dict] = {}
    try:
        from godsv.capability import load_tool_entries, promotion_report

        scope_by_name = {row["name"]: row for row in load_tool_entries(runtime)}
        promo_by_name = {row["name"]: row for row in promotion_report(runtime)["tools"]}
    except Exception:
        scope_by_name, promo_by_name = {}, {}
    installed = []
    for spec in store.all():
        manifest = _read_manifest(spec.name)
        installed.append(
            {
                "name": spec.name,
                "title": spec.title,
                "kind": spec.kind,
                "type": manifest.get("type", ""),
                "status": spec.status,
                "source": spec.provenance,
                "generated": str(spec.provenance).startswith("toolbuilder:"),
                "health": spec.scan_summary or "",
                "last_tested_at": manifest.get("last_tested_at"),
                "usage_count": usage.get(spec.name, 0),
                "required_secrets": manifest.get("required_secrets", []),
                "allowed_domains": (manifest.get("network") or {}).get("allowed_domains", []),
                "risk_level": manifest.get("risk_level", ""),
                # §7/§10: scope is visibility; promotion is evidenced; health
                # stays its own fact (manifest validation/status above).
                "scope": (scope_by_name.get(spec.name) or {}).get("scope", ""),
                "version": (scope_by_name.get(spec.name) or {}).get("version_raw"),
                "project_id": (scope_by_name.get(spec.name) or {}).get("project_id", ""),
                "promoted_from": manifest.get("promoted_from") or None,
                "promotion": promo_by_name.get(spec.name) or None,
            }
        )
    return {
        "installed": installed,
        "generated": [row for row in queue if row.get("stage") == "ready"],
        "queue": queue,
        "requests": requests,
        "mcp_servers": mcp_servers_payload(),
    }


def _read_jsonl_tail(path: Path, *, limit: int = 10) -> list[dict]:
    """Last records of an append-only JSONL file (newest last)."""
    if not path.exists():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows[-limit:]


def cu_payload() -> dict:
    """Computer Use: runner isolations, policy, last real dry-runs (spec §22)."""
    try:
        from godsv.computer_use import CUPolicy, runner_availability
    except Exception:
        return {"runners": [], "policy": None, "runs": [], "error": "computer-use unavailable"}
    policy = CUPolicy()
    try:
        report = runner_availability()
    except Exception:
        report = {}
    return {
        "runners": [{"name": name, "status": detail} for name, detail in report.items()],
        "policy": {
            "channel_order": "API > DOM > Vision",
            "allowed_kinds": list(policy.allowed_kinds),
            "max_actions": policy.max_actions,
            "max_fallbacks": policy.max_fallbacks,
            "require_isolation": policy.require_isolation,
        },
        "runs": _read_jsonl_tail(runtime_dir() / "cu_runs.jsonl", limit=8),
    }


def cu_demo_payload() -> dict:
    """Run the deterministic dry-run demo through the real CU executor."""
    from godsv.computer_use import (
        Action,
        ActionResult,
        Attempt,
        CUExecutor,
        CUPolicy,
        DryRunRunner,
        Verification,
    )

    policy = CUPolicy()
    runner = DryRunRunner(
        behaviors={
            "deploy via API": ActionResult(ok=False, detail="HTTP 500"),
            "close window": ActionResult(ok=True, stdout="window closed"),
        }
    )
    attempts = [
        Attempt(
            channel="vision",
            action=Action(kind="mouse", payload={"x": 10, "y": 10}, description="click the button"),
            verify=Verification(expect_observe_contains="closed"),
        ),
        Attempt(
            channel="api",
            action=Action(kind="cli", argv=["deploy", "--api"], description="deploy via API"),
            verify=Verification(expect_stdout_contains="deployed"),
        ),
        Attempt(
            channel="dom",
            action=Action(
                kind="dom_click", payload={"selector": "#close"}, description="close window"
            ),
            verify=Verification(expect_stdout_contains="window closed"),
        ),
    ]
    result = CUExecutor(policy).run("panel-demo", attempts, runner)
    record = {
        "ts": time.time(),
        "step": result.step,
        "status": result.status,
        "channel": result.channel,
        "actions": result.actions,
        "fallbacks": result.fallbacks,
        "detail": result.detail,
        "kind": "dry-run",
    }
    path = runtime_dir() / "cu_runs.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return {"ok": True, "run": record}


def _token_metrics_payload() -> dict:
    """Spec §20: MEASURED counts from the event store + labelled ESTIMATES."""
    events_db = runtime_dir() / "events.db"
    measured = {
        "events_total": 0,
        "pauses": 0,
        "deliveries": 0,
        "research_runs": 0,
        "research_failed": 0,
        "capability_requests": 0,
        "context_builds": 0,
        "context_chars": 0,
    }
    estimated: dict = {
        "voice_ctx_tokens": 0,
        "investigator_tokens": 0,
        "notes": [
            "Tokens ≈ chars/4 (ESTIMATED). Hermes keeps the main-agent tokens — "
            "Skaldir Agent does not duplicate them.",
        ],
    }
    conn = _connect(events_db)
    if conn is None:
        return {"measured": measured, "estimated": estimated, "available": False}
    try:
        counts = _kind_counts(conn)
        measured["events_total"] = sum(counts.values())
        measured["pauses"] = counts.get("pause_requested", 0)
        measured["deliveries"] = counts.get("pause_delivered", 0)
        measured["research_runs"] = counts.get("research_done", 0)
        measured["research_failed"] = counts.get("research_failed", 0)
        measured["capability_requests"] = counts.get("capability_requested", 0)
        measured["context_builds"] = counts.get("voice_context_built", 0)
        for row in conn.execute(
            "SELECT kind, payload FROM events WHERE kind IN ('research_done','voice_context_built')"
        ):
            try:
                payload = json.loads(row[1] or "{}")
            except (TypeError, json.JSONDecodeError):
                continue
            if row[0] == "research_done":
                estimated["investigator_tokens"] += int(payload.get("tokens_est") or 0)
            elif row[0] == "voice_context_built":
                measured["context_chars"] += int(payload.get("chars") or 0)
    finally:
        conn.close()
    estimated["voice_ctx_tokens"] = measured["context_chars"] // 4
    return {"measured": measured, "estimated": estimated, "available": True}


def evals_payload() -> dict:
    """Eval harness: episode catalogue, last run, token metrics (spec §20/§22)."""
    try:
        from godsv.evalharness import episodes
    except Exception:
        return {
            "episodes": [],
            "metrics": _token_metrics_payload(),
            "last_run": None,
            "error": "eval harness unavailable",
        }
    catalogue = [
        {
            "name": episode.name,
            "expect_signals": list(episode.expect_signals or []),
            "expect_guard": episode.expect_guard,
            "expect_pause_delivered": bool(episode.expect_pause_delivered),
            "events": len(episode.events),
        }
        for episode in episodes()
    ]
    tail = _read_jsonl_tail(runtime_dir() / "eval_runs.jsonl", limit=1)
    return {
        "episodes": catalogue,
        "metrics": _token_metrics_payload(),
        "last_run": tail[0] if tail else None,
    }


def evals_run_payload() -> dict:
    """Run the deterministic episode suite for real (no LLM, seconds)."""
    from godsv.evalharness import run_eval

    results = run_eval()
    record = {
        "ts": time.time(),
        "passed": sum(1 for r in results if r.passed),
        "total": len(results),
        "results": [
            {
                "name": r.name,
                "passed": r.passed,
                "signals": list(r.signal_kinds),
                "notes": list(r.notes),
            }
            for r in results
        ],
    }
    path = runtime_dir() / "eval_runs.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return {"ok": True, "run": record}


def _require_tool(name: str):
    name = (name or "").strip()
    if not _CRED_RE.fullmatch(name):
        raise ValueError("invalid tool name")
    from godsv.tools import ToolStore

    store = ToolStore(runtime_dir() / "tools.db")
    spec = store.get(name)
    if spec is None:
        raise ValueError(f"unknown tool: {name}")
    return store, spec


def tool_detail_payload(name: str) -> dict:
    """Everything the detail view needs — no secrets, no transcript."""
    store, spec = _require_tool(name)
    manifest = _read_manifest(spec.name)
    grants: list[dict] = []
    opened = _open_treasury()
    if opened is not None:
        _vault, state = opened
        grants = [g for g in state.list_grants() if g.get("tool_id") == spec.name]
    from godsv.toolbuilder import ToolBuilder

    builds = [
        entry for entry in ToolBuilder(runtime_dir()).queue(limit=50)
        if entry.get("name") == spec.name
    ]
    usage: dict = {}
    try:
        from godsv.toolusage import usage_summary

        usage = usage_summary(runtime_dir(), spec.name)
    except Exception:
        usage = {}
    return {
        "spec": spec.to_dict(),
        "manifest": manifest,
        "grants": grants,
        "builds": builds,
        "usage": usage,
        "wrapper": str(runtime_dir() / "generated_tools" / spec.name / "bin" / spec.name),
    }


def tool_action_payload(name: str, action: str) -> dict:
    """Enable / disable / re-run gates / smoke-test — audited, argv-fixed."""
    action = (action or "").strip()
    if action not in ("enable", "disable", "recheck", "test"):
        raise ValueError(f"unknown action: {action!r}")
    store, spec = _require_tool(name)
    if action in ("enable", "disable"):
        from godsv.tools import STATUS_APPROVED, STATUS_REVOKED

        status = STATUS_APPROVED if action == "enable" else STATUS_REVOKED
        ok = store.set_status(spec.name, status)
        _audit("tool_action", {"name": spec.name, "action": action, "ok": ok})
        return {"ok": ok, "name": spec.name, "status": status}
    if action == "recheck":
        from godsv.gates import run_gates

        tool_dir = runtime_dir() / "generated_tools" / spec.name
        if not tool_dir.exists():
            raise RuntimeError("no generated sources for this tool")
        report = run_gates(spec, workdir=tool_dir)
        checks = [
            {"name": check.gate, "ok": bool(check.passed), "detail": check.detail}
            for check in report.checks
        ]
        _audit("tool_action", {"name": spec.name, "action": action, "passed": report.passed})
        return {"ok": report.passed, "name": spec.name, "checks": checks}
    # test: run the wrapper through the shared execution boundary (§1.1) —
    # argv-fixed, no shell, sandboxed via bwrap; without a sandbox the action
    # is honestly ``not_certifiable`` and NOTHING is executed (no unprotected
    # fallback — this used to be a bare subprocess.run).
    from godsv.execution import RunPolicy, ToolRun, run_local

    tool_dir = runtime_dir() / "generated_tools" / spec.name
    wrapper = tool_dir / "bin" / spec.name
    if not wrapper.exists():
        raise RuntimeError("no CLI wrapper for this tool (MCP-only or not generated)")
    argv = [str(wrapper)]
    if spec.sample_input:
        argv.append(json.dumps(spec.sample_input))
    # Wave 6: a tool that declares credentials runs under the treasury gate — grant-checked per
    # handle, material in the child env only, output scrubbed, use recorded. Denied means NOT run.
    handles = [str(item) for item in (_read_manifest(spec.name).get("required_secrets") or [])]
    if handles:
        from godsv.treasury import run_tool_with_secrets

        outcome = run_tool_with_secrets(
            argv=argv,
            runtime=runtime_dir(),
            handles=handles,
            tool_id=spec.name,
            purpose=f"panel:tool-test:{spec.name}",
            workdir=tool_dir,
            timeout=30.0,
        )
        _audit("tool_test", {"name": spec.name, "ok": bool(outcome["ok"]), "handles": handles})
        if not outcome["ok"]:
            return {
                "ok": False,
                "name": spec.name,
                "output": (outcome["reason"] or "denied by the treasury gate")[:300],
                "treasury_denied": True,
            }
        return {
            "ok": True,
            "name": spec.name,
            "output": (outcome["output"] or "ran under an active grant")[:400],
            "treasury": outcome["env"],
        }
    try:
        result = run_local(
            ToolRun(argv=argv, workdir=str(tool_dir), timeout=30.0),
            policy=RunPolicy(require_sandbox=True),
        )
    except Exception as exc:  # a crashing runner is a failed test, never a 500
        _audit("tool_test", {"name": spec.name, "ok": False})
        return {"ok": False, "name": spec.name, "output": f"{type(exc).__name__}: {exc}"[:300]}
    if result.status == "not-certifiable":
        _audit("tool_test", {"name": spec.name, "ok": False, "reason": "not-certifiable"})
        return {
            "ok": False,
            "name": spec.name,
            "output": result.detail[:300],
            "not_certifiable": True,
        }
    ok = result.status == "ok"
    output = ((result.stdout + result.stderr).strip() or result.detail)[:400]
    _audit("tool_test", {"name": spec.name, "ok": ok})
    return {"ok": ok, "name": spec.name, "output": output}


# -- strict completion supervisor (Goal §35–§39) ----------------------------


def _verification_for(text: str) -> str:
    """Derive the check for a criterion — registered testing tools included.

    Live-found 13.09. (supervised run): everything without tests/ui/api wording
    fell back to ``run``, which nothing can certify (``gate_dod`` needs runner
    evidence with a matching check id) — a criterion naming a registered
    testing tool deadlocked. The tool name in the criterion text now wires the
    criterion to that tool's check id; the ``run`` fallback stays for
    everything else.
    """
    from godsv.capability import register_testing_tools
    from godsv.completion import plan_verification

    try:
        tool_names = register_testing_tools(runtime_dir())
    except Exception:
        tool_names = []
    return plan_verification(text, tool_names=tool_names) or "run"


def _gate_lines(last: dict | None) -> list[dict]:
    """PRODUCT STATUS grid (§37) from the most recent verification."""
    if not last:
        return []
    failed: set[str] = set()
    for blob in (last.get("blocking_failures") or []) + (last.get("major_failures") or []):
        failed.add(str(blob).split(":", 1)[0].strip())
    known = (
        ("acceptance", "definition-of-done"),
        ("evidence", "evidence"),
        ("tests", "tests"),
        ("build", "build"),
        ("integration", "integration"),
        ("fake-data", "fake-completion"),
        ("security", "security"),
        ("cleanup", "cleanup"),
    )
    return [
        {"name": label, "status": "FAIL" if gate_id in failed else "PASS"}
        for label, gate_id in known
    ]


def origin_allowed(origin: str) -> bool:
    """Exact allowlist for panel mutations (P1/B3, Block 1).

    Only these qualify: ``null`` / ``file:`` / ``app:`` / ``hermes:``
    documents, or an http(s) origin whose hostname is exactly localhost /
    127.0.0.1 / ::1.

    Fail-closed (Block 1 / SEC-06): a **missing** Origin header is rejected
    too. It used to be allowed as a "same-origin fetch" shortcut, but a
    non-browser caller (curl, a local process, the agent's own tooling)
    simply omits the header — that was the trust hole, not a browser case.
    The desktop app is unaffected: it always sends its own ``app://``
    origin plus the session token (see ``_guard_origin``).
    """
    from urllib.parse import urlsplit

    clean = str(origin or "").strip().lower()
    if not clean:
        return False
    if clean in ("null", "file://", "app://"):
        return True
    parts = urlsplit(clean)
    if parts.scheme in ("file", "app", "hermes"):
        return True
    if parts.scheme in ("http", "https") and parts.hostname in (
        "localhost",
        "127.0.0.1",
        "::1",
    ):
        return True
    return False


def panel_token() -> str:
    """The gateway's panel session token, read from THIS process' env.

    The plugin runs inside the Hermes gateway process, so it sees the very
    token the gateway authenticates with (``HERMES_DASHBOARD_SESSION_TOKEN``
    — the desktop shell mints it per spawn). Comparing it here is a second,
    plugin-local enforcement point: the host gateway already 401s
    unauthenticated ``/api/*`` calls, but the trust boundary must not rest on
    a property of the host alone (audit SEC-06: "nur durch Doku belegt").
    Empty means "unknown" — never a wildcard.
    """
    return str(os.environ.get("HERMES_DASHBOARD_SESSION_TOKEN") or "").strip()


def _token_presented(request) -> bool:
    """True iff the request carries the panel token (header or bearer)."""
    expected = panel_token()
    if not expected:
        return False
    try:
        header = str(request.headers.get("x-hermes-session-token") or "").strip()
        auth = str(request.headers.get("authorization") or "").strip()
    except Exception:  # pragma: no cover — header access failing is not a pass
        return False
    if header and hmac.compare_digest(header, expected):
        return True
    if auth.lower().startswith("bearer "):
        return hmac.compare_digest(auth[7:].strip(), expected)
    return False


def _request_is_authenticated(request) -> bool:
    """Requests the host gateway already proved, or that carry our token.

    Two ways in, both explicit:

    * ``request.state.token_authenticated`` — the gateway's own token-auth
      seam (``hermes_cli/dashboard_auth/token_auth.py``) has already
      verified the caller and attached a principal. Trusting it is the
      point of that seam.
    * ``X-Hermes-Session-Token`` / ``Authorization: Bearer`` matching
      :func:`panel_token` — re-verified here so a plugin-local caller
      cannot mutate state by merely omitting the Origin header.
    """
    if getattr(getattr(request, "state", None), "token_authenticated", False):
        return True
    return _token_presented(request)


def _completion_store(session_id: str = ""):
    """Session-scoped store for panel reads/mutations.

    With a session id: exactly that session's contract. Without: the most
    recently updated contract — an explicit, named policy (`latest`), not a
    hidden "last session" grab; the supervisor and gates always pass a real id.
    """
    from godsv.completion import CompletionStore

    sid = str(session_id or "").strip()
    if sid:
        return CompletionStore(runtime_dir(), session_id=sid)
    return CompletionStore.latest(runtime_dir())


def completion_payload(session_id: str = "") -> dict:
    """Goal state for the panel: status, requirements, last verdict, timeline, metrics."""
    from godsv.completion import TERMINAL_STATES

    store = _completion_store(session_id)
    state = store.state()
    timeline = store.timeline(limit=40)
    last = None
    for row in reversed(timeline):
        if row.get("kind") == "verification":
            last = row
            break
    failed_blob = " || ".join(
        list((last or {}).get("blocking_failures") or [])
        + list((last or {}).get("major_failures") or [])
    )
    requirements = []
    for item in state.get("dod") or []:
        text = str(item.get("text") or "")
        requirements.append(
            {
                "id": item.get("id"),
                "text": text,
                "blocking": bool(item.get("blocking", True)),
                "verified": bool(text) and text not in failed_blob,
            }
        )
    return {
        "state": state.get("state") or "WORKING",
        "terminal": (state.get("state") or "") in TERMINAL_STATES,
        "goal": state.get("goal") or "",
        "started": state.get("started"),
        "session": state.get("session_id") or "",
        "test": str(state.get("session_id") or "").startswith("prodtest-"),
        "requirements": requirements,
        # The chat live block renders criteria + the editor's revision fence
        # from these; live-found missing 13.09. (mock payloads hid it).
        "dod": [dict(item) for item in (state.get("dod") or [])],
        "revision": int(state.get("revision") or 0),
        "trajectory": {
            "decisions": state.get("decisions") or [],
            "assumptions": state.get("assumptions") or [],
            "non_goals": state.get("non_goals") or [],
            "constraints": state.get("constraints") or [],
        },
        "verified_count": sum(1 for r in requirements if r["verified"]),
        "total_count": len(requirements),
        "last_verdict": {
            "status": (last or {}).get("status"),
            "blocking_failures": list((last or {}).get("blocking_failures") or [])[:6],
            "major_failures": list((last or {}).get("major_failures") or [])[:6],
            "minor_findings": list((last or {}).get("minor_findings") or [])[:6],
            "evidence": list((last or {}).get("evidence") or [])[:6],
            "next_actions": list((last or {}).get("next_actions") or [])[:6],
            "created": (last or {}).get("created"),
        },
        "gates": _gate_lines(last),
        "timeline": [
            {
                "kind": row.get("kind"),
                "state": row.get("state"),
                "status": row.get("status"),
                "reason": row.get("reason"),
                "attempt_id": row.get("attempt_id"),
                "created": row.get("created"),
            }
            for row in timeline[-20:]
        ],
        "metrics": store.metrics(),
    }


def _system_one_preflight(
    *,
    request,
    evidence,
    revision: int,
    workspace: str,
    session_id: str,
    events=None,
) -> dict:
    """§9.1/§33 — the System-One advisory reading for a completion claim, now reachable.

    Measured 17.09. at HEAD: `godsv.completion.completion_precheck` and
    `godsv.system_one_flags.load_system_one_flags` had **no production caller** — the flags, the §33
    facade and the §91/JEV-13 reading never ran anywhere in the product. The visible verify route is
    where a completion claim is decided, so the advisory is computed here.

    Advisory by construction: it never certifies (§9.8), it is reported next to the deterministic
    verdict and never changes it. With the flags off (the default) it says exactly that instead of
    pretending an evaluation happened. Only a runner-executed check can certify — that stays the
    gate's job.
    """

    import dataclasses

    from godsv.completion import completion_precheck
    from godsv.system_one_flags import build_evaluator, load_system_one_flags

    flags = load_system_one_flags(runtime_dir())
    payload: dict = {
        "available": False,
        "reason": "",
        "flags": {
            "enabled": bool(getattr(flags, "enabled", False)),
            "provider": str(getattr(flags, "provider", "")),
            "completion_precheck": bool(getattr(flags, "completion_precheck", False)),
        },
        "reading": None,
    }
    if not (payload["flags"]["enabled"] and payload["flags"]["completion_precheck"]):
        payload["reason"] = (
            "system one flags off — set system_one.enabled and system_one.completion_precheck in "
            "the runtime settings.yaml to get the advisory reading"
        )
        return payload
    evaluation = None
    try:
        evaluator = build_evaluator(flags)
        if type(evaluator).__name__ == "DisabledSystemOneEvaluator":
            payload["reason"] = (
                "structural reading only — the selected provider has no client in the "
                "panel context "
                f"(provider={payload['flags']['provider'] or 'disabled'})"
            )
        reading = completion_precheck(
            request=request,
            evidence=evidence,
            revision=revision,
            workspace=workspace,
            session=session_id,
            flags=flags,
            evaluation=evaluation,
        )
    except Exception as exc:  # noqa: BLE001 — an advisory must never break the gate
        payload["reason"] = f"{type(exc).__name__}: {exc}"[:200]
        return payload
    if reading is None:
        payload["reason"] = payload["reason"] or "no advisory reading"
        return payload
    payload["available"] = True
    payload["reading"] = dataclasses.asdict(reading)
    payload["reading"]["certifies_completion"] = bool(reading.certifies_completion())
    if events is not None:
        try:
            events.append(
                session_id or "panel",
                0,
                "system_one_decision",
                {
                    "purpose": "completion_preflight",
                    "provider": payload["reading"].get("provider") or payload["flags"]["provider"],
                    "live_jev": bool(payload["reading"].get("live_jev")),
                    "signals": list(payload["reading"].get("signals") or [])[:8],
                    "advisory_only": bool(payload["reading"].get("advisory_only", True)),
                },
            )
        except Exception:  # noqa: BLE001 — the record is best effort, the reading stands
            pass
    return payload


def completion_verify_payload(payload: dict) -> dict:
    """Run the real gate pipeline now and record the verdict (UI: verify now)."""
    from godsv.completion import (
        TERMINAL_STATES,
        CompletionRequest,
        GateRunner,
        run_evidence_from_events,
    )
    from godsv.events import EventStore

    store = _completion_store(str(payload.get("session_id") or ""))
    state = store.state()
    if (state.get("state") or "") in TERMINAL_STATES:
        raise RuntimeError("goal is in a terminal state — begin a new goal first")
    text = str(payload.get("text") or "")
    if not text:
        for row in reversed(store.timeline(limit=200)):
            if row.get("kind") == "completion_request":
                claims = row.get("claimed_completed") or []
                text = " ".join(str(c) for c in claims)
                break
    evidence: list = []
    events = None
    session_id = str(payload.get("session_id") or getattr(store, "session_id", "") or "")
    try:
        events = EventStore(runtime_dir() / "events.db")
        if not session_id:
            ids = events.session_ids()
            session_id = ids[-1] if ids else ""
        if session_id:
            evidence = run_evidence_from_events(events.iter_session(session_id))
    except Exception:
        evidence = []
    declared_base = str(payload.get("repo_root") or (state.get("task_base") or "")).strip()
    repo_root = Path(declared_base) if declared_base else Path.cwd()
    # P1: specific requirements are certified by runner-executed checks only —
    # tool-event evidence is context, the runner produces the verdicts
    from godsv.completion import run_requirement_checks

    _rev = int(state.get("revision") or 0)
    _ws = str(state.get("task_base") or repo_root)
    evidence = list(evidence) + run_requirement_checks(
        state.get("dod") or [], repo_root, revision=_rev, workspace=_ws
    )
    request = CompletionRequest(
        user_goal=str(state.get("goal") or ""),
        claimed_completed=[text] if text else [],
        test_results="panel verification",
        attempt_id=f"panel-{int(time.time())}",
        created=time.time(),
    )
    dod = []
    from godsv.completion import DodItem

    for item in state.get("dod") or []:
        dod.append(
            DodItem(
                id=str(item.get("id") or "c"),
                text=str(item.get("text") or ""),
                blocking=bool(item.get("blocking", True)),
                verification=str(item.get("verification") or ""),
            )
        )
    GateRunner(repo_root=repo_root, dod=dod, revision=_rev, workspace=_ws).verify(
        request, evidence, store=store
    )
    # §9.1/§33: the advisory reading next to the deterministic verdict (never a certificate)
    preflight = _system_one_preflight(
        request=request,
        evidence=evidence,
        revision=_rev,
        workspace=_ws,
        session_id=session_id,
        events=events,
    )
    result = completion_payload(str(payload.get("session_id") or ""))
    result["preflight"] = preflight
    return result


def completion_override_payload(payload: dict) -> dict:
    """§35: only the user may consciously accept a blocker — never 'VERIFIED'."""

    reason = str(payload.get("reason") or "").strip()
    if not reason:
        raise ValueError("reason required — the override must be explicit")
    store = _completion_store(str(payload.get("session_id") or ""))
    store.transition("SHIPPED_WITH_USER_OVERRIDE", reason=reason[:300])
    return completion_payload(str(payload.get("session_id") or ""))


def completion_goal_payload(payload: dict) -> dict:
    """Begin a new goal (re-arms the supervisor after a terminal state)."""
    from godsv.completion import DodItem

    goal = str(payload.get("goal") or "").strip()
    if not goal:
        raise ValueError("goal required")
    criteria = [str(c).strip() for c in (payload.get("criteria") or []) if str(c).strip()]
    dod = [
        DodItem(
            id=f"c{i + 1}",
            text=text,
            blocking=True,
            verification=_verification_for(text),
        )
        for i, text in enumerate(criteria)
    ]
    store = _completion_store(str(payload.get("session_id") or ""))
    record = store.begin_goal(
        str(payload.get("session_id") or "panel"),
        goal,
        dod,
        task_base=str(payload.get("repo_root") or ""),
    )
    return {"state_record": record, **completion_payload(str(payload.get("session_id") or ""))}


def _chat_event_rows(
    conn: sqlite3.Connection,
    after_seq: int,
    limit: int,
    session_id: str = "",
    before_seq: int | None = None,
) -> list[dict]:
    """Important events (CHAT_KINDS) after a sequence number, newest first.

    With a session id the feed only shows the task it belongs to — a failure
    from another session must never render as the current state (P1/A5).
    ``before_seq`` pages backwards (exclusive upper bound) so a window that was
    cut by ``limit`` can be walked instead of dropped.
    """
    from godsv.events import CHAT_KINDS

    kinds = sorted(CHAT_KINDS)
    placeholders = ",".join("?" for _ in kinds)
    session_filter = " AND session_id = ?" if session_id else ""
    before_filter = " AND seq < ?" if before_seq else ""
    params = [after_seq, *kinds]
    if session_id:
        params.append(session_id)
    if before_seq:
        params.append(int(before_seq))
    params.append(limit)
    try:
        rows = conn.execute(
            "SELECT seq, ts, session_id, step, kind, payload FROM events"
            f" WHERE seq > ? AND kind IN ({placeholders}){session_filter}{before_filter}"
            " ORDER BY seq DESC LIMIT ?",
            tuple(params),
        ).fetchall()
    except sqlite3.Error:
        return []
    events: list[dict] = []
    for row in rows:
        payload: object = {}
        try:
            payload = json.loads(row["payload"])
        except (ValueError, TypeError):
            payload = str(row["payload"])[:200]
        events.append(
            {
                "seq": int(row["seq"]),
                "ts": row["ts"],
                "session": row["session_id"],
                "step": row["step"],
                "kind": str(row["kind"]),
                "payload": payload,
            }
        )
    return events


def _older_unread_count(
    conn: sqlite3.Connection,
    after_seq: int,
    oldest_delivered: int,
    session_id: str = "",
) -> int:
    """How many chat events sit between ``after_seq`` and the delivered window.

    That number was previously invisible: with ``limit`` smaller than the backlog the client got
    the newest slice and never learned the rest existed (audit AF-14: 60 unread events, default
    limit 40 -> the 20 oldest were never delivered and nothing said so).
    """
    from godsv.events import CHAT_KINDS

    if not oldest_delivered:
        return 0
    kinds = sorted(CHAT_KINDS)
    placeholders = ",".join("?" for _ in kinds)
    session_filter = " AND session_id = ?" if session_id else ""
    params: tuple = (after_seq, oldest_delivered, *kinds, session_id) if session_id else (
        after_seq, oldest_delivered, *kinds
    )
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM events"
            f" WHERE seq > ? AND seq < ? AND kind IN ({placeholders}){session_filter}",
            params,
        ).fetchone()
    except sqlite3.Error:
        return 0
    return int(row["n"]) if row else 0


def events_since_payload(
    after_seq: int = 0,
    limit: int = 40,
    events_db: Path | None = None,
    session_id: str = "",
    before_seq: int | None = None,
) -> dict:
    """Chat feed (§16/§22): the important events after a sequence number.

    The chat polls this with the last seen `latest_seq`; only CHAT_KINDS pass
    (interventions, verification, capability and security gates), everything
    else stays in the panel. Store payloads are already secret-screened at
    append time (godsv.events), so the feed never carries vault material.
    """
    from godsv.events import CHAT_KINDS

    limit = max(1, min(200, int(limit)))
    db = events_db or runtime_dir() / "events.db"
    sid = str(session_id or "")
    if not sid:
        # default: the session of the newest contract — the task the user last
        # touched. Explicit policy, reported in the payload.
        try:
            from godsv.completion import CompletionStore

            newest = CompletionStore.sessions(db.parent)
            sid = newest[0]["session_id"] if newest else ""
        except Exception:
            sid = ""
    conn = _connect(db)
    events: list[dict] = []
    latest = 0
    older_unread = 0
    if conn is not None:
        try:
            events = _chat_event_rows(
                conn, max(0, int(after_seq)), limit, session_id=sid, before_seq=before_seq
            )
            row = conn.execute("SELECT COALESCE(MAX(seq), 0) AS m FROM events").fetchone()
            latest = int(row["m"]) if row else 0
            oldest = min((int(item["seq"]) for item in events), default=0)
            older_unread = _older_unread_count(conn, max(0, int(after_seq)), oldest, session_id=sid)
        except sqlite3.Error:
            events, latest, older_unread = [], 0, 0
        finally:
            conn.close()
    oldest_seq = min((int(item["seq"]) for item in events), default=0)
    return {
        "events": events,
        "latest_seq": latest,
        # Cursor contract (Wave 9): a window cut by `limit` must be walkable, not silently lost.
        "oldest_seq": oldest_seq,
        "remaining": older_unread,
        "truncated": older_unread > 0,
        "next_before": oldest_seq if older_unread else 0,
        "kinds": sorted(CHAT_KINDS),
        "session_id": sid,
    }


def completion_trajectory_payload(payload: dict) -> dict:
    """Record a decision / assumption / non-goal on the active contract (P0-1)."""
    sid = str(payload.get("session_id") or "")
    store = _completion_store(sid)
    store.record_trajectory(
        str(payload.get("kind") or ""),
        str(payload.get("text") or ""),
        str(payload.get("reason") or ""),
    )
    return completion_payload(session_id=sid)


def criteria_op_payload(store, payload: dict) -> dict:
    """Apply one criteria op — add/update/remove (Session-UI Stufe 3).

    Ops may carry ``expected_revision`` (conflict fence) and ``request_id``
    (idempotent retry). ``status`` is never accepted from the client: an edit
    must not masquerade as verification evidence.
    """

    op = str(payload.get("op") or "add").strip().lower()
    expected = payload.get("expected_revision")
    expected_revision = int(expected) if expected not in (None, "") else None
    request_id = str(payload.get("request_id") or "").strip() or None
    if op == "add":
        criteria = [str(c).strip() for c in (payload.get("criteria") or []) if str(c).strip()]
        if not criteria:
            raise ValueError("criteria required")
        record, added = store.add_criteria(
            criteria, expected_revision=expected_revision, request_id=request_id
        )
        return {"op": "add", "added": added, "replayed": bool(record.get("replayed"))}
    cid = str(payload.get("id") or "").strip()
    if not cid:
        raise ValueError("criterion id required")
    if op == "update":
        text = payload.get("text")
        blocking = payload.get("blocking")
        record = store.update_criterion(
            cid,
            text=None if text is None else str(text),
            blocking=None if blocking is None else bool(blocking),
            expected_revision=expected_revision,
            request_id=request_id,
        )
        return {"op": "update", "id": cid, "replayed": bool(record.get("replayed"))}
    if op == "remove":
        record = store.remove_criterion(
            cid, expected_revision=expected_revision, request_id=request_id
        )
        return {"op": "remove", "id": cid, "replayed": bool(record.get("replayed"))}
    if op == "move":
        before = str(payload.get("before") or "").strip() or None
        after = str(payload.get("after") or "").strip() or None
        record = store.move_criterion(
            cid,
            before=before,
            after=after,
            expected_revision=expected_revision,
            request_id=request_id,
        )
        return {"op": "move", "id": cid, "replayed": bool(record.get("replayed"))}
    raise ValueError(f"unknown criteria op: {op}")


def completion_criteria_payload(payload: dict) -> dict:
    """Add or edit `@goal` criteria (chat mentions + cockpit editor, plan §4/§5)."""

    sid = str(payload.get("session_id") or "")
    store = _completion_store(sid)
    return {**criteria_op_payload(store, payload), **completion_payload(sid)}


def goal_set_payload(payload: dict) -> dict:
    """Edit the active goal text (inline chat edit)."""

    goal = str(payload.get("goal") or "").strip()
    if not goal:
        raise ValueError("goal required")
    store = _completion_store(str(payload.get("session_id") or ""))
    store.set_goal(goal)
    return completion_payload(session_id=str(payload.get("session_id") or ""))


APPROVAL_MODES = ("manual", "smart", "off")


def _hermes_config_path() -> Path:
    home = os.environ.get("HERMES_HOME") or str(Path.home() / ".hermes")
    return Path(home) / "config.yaml"


def approvals_payload() -> dict:
    """The real Hermes approval mode (`/approvals` — manual|smart|off).

    Reads the config FILE directly (the ground truth the engine itself loads),
    with the config API as fallback. Availability is reported honestly so
    older/foreign runtimes degrade instead of lying.
    """
    try:
        import yaml

        data = yaml.safe_load(_hermes_config_path().read_text()) or {}
        mode = str((data.get("approvals") or {}).get("mode") or "off").strip().lower()
        if mode not in APPROVAL_MODES:
            mode = "off"
        return {"mode": mode, "available": True, "source": "file"}
    except Exception:
        pass
    try:
        from hermes_cli.config import get_config_value

        mode = str(get_config_value("approvals.mode") or "off").strip().lower()
        if mode not in APPROVAL_MODES:
            mode = "off"
        return {"mode": mode, "available": True, "source": "api"}
    except Exception:
        return {"mode": None, "available": False}


def approvals_set_payload(payload: dict) -> dict:
    """Switch the engine approval mode (origin-guarded; validated)."""
    mode = str(payload.get("mode") or "").strip().lower()
    if mode not in APPROVAL_MODES:
        raise ValueError(f"mode must be one of: {', '.join(APPROVAL_MODES)}")
    try:
        from hermes_cli.config import set_config_value

        set_config_value("approvals.mode", mode)
    except Exception as exc:
        raise ValueError(f"approvals not settable in this runtime: {type(exc).__name__}") from exc
    return approvals_payload()


router = None
if APIRouter is not None:  # pragma: no cover - covered in the gateway
    router = APIRouter()

    @router.get("/health")
    def health() -> dict:
        return {"ok": True, "panel": "godsv", "version": PANEL_VERSION}

    @router.get("/summary")
    def summary(limit: int = 25) -> dict:
        return summary_payload(limit=limit)

    @router.get("/memory")
    def memory(limit: int = 50) -> dict:
        return memory_payload(limit=max(5, min(200, limit)))

    @router.get("/events")
    def events_feed(
        since: int = 0, limit: int = 40, session_id: str = "", before: int = 0
    ) -> dict:
        return events_since_payload(
            after_seq=since, limit=limit, session_id=session_id, before_seq=before or None
        )

    @router.get("/skills")
    def skills(limit: int = 50) -> dict:
        return skills_payload(limit=max(5, min(200, limit)))

    @router.get("/treasury")
    def treasury(limit: int = 50) -> dict:
        return treasury_payload(limit=max(5, min(200, limit)))

    # -- models (mutations are audit-logged, local-origin only) ---------------

    def _guard_origin(request) -> None:
        """Panel mutations: local app origin AND a proven caller (Block 1).

        Measured wire reality of the desktop app (see the origin check below): the Electron
        main process presents the per-spawn panel token and no Origin header, so the token is
        the deciding factor; a *stated* origin must still be allowlisted.

        Fail-closed twice over:

        * a header-read failure is a **rejection**, not a skip — the old
          ``except Exception: return`` made a broken request adapter silently
          bypass the whole check;
        * an allowed Origin is necessary but no longer sufficient — the
          caller must also be the gateway-authenticated principal or present
          the panel token.

        What stays allowed (and why the desktop app keeps working): the app
        calls through ``ctx.rest`` → the Electron main process, which sends
        ``Origin: app://…`` **and** ``X-Hermes-Session-Token: <minted
        per-spawn token>``. Both conditions hold, so no app path breaks.
        A plain local ``curl`` without token now gets 403 — that is the
        point (SEC-06: origin alone proved nothing).

        Documented residual: if the gateway env carries no session token
        (a bare library/test runtime), only the gateway's own
        ``token_authenticated`` seam can admit a mutation; every other
        caller is refused.
        """
        try:
            origin = request.headers.get("origin") or ""
            if os.environ.get("GODSV_GUARD_DEBUG") == "1":  # audit diagnosis, env-gated
                try:
                    with open("/tmp/gv_guard.log", "a", encoding="utf-8") as _fh:
                        _fh.write(
                            time.strftime("%H:%M:%S")
                            + " origin=" + repr(origin)
                            + " token=" + str(bool(request.headers.get("x-hermes-session-token")))
                            + " auth=" + str(_request_is_authenticated(request))
                            + " path=" + str(getattr(request, "url", ""))
                            + "\n"
                        )
                except Exception:
                    pass
        except Exception as exc:  # a request we cannot inspect is not a pass
            raise HTTPException(
                status_code=403,
                detail="panel mutation rejected: request headers unreadable",
            ) from exc
        # A *stated* foreign origin is still rejected. An *absent* Origin is not a rejection
        # by itself: the desktop app mutates through the Electron main process, which sends the
        # per-spawn panel token but NO Origin header (measured in the built app 17.09.:
        # origin='' token=True auth=True -> 403 "cross-origin panel mutation rejected", i.e. every
        # mutating control was dead: CU demo, eval run, approvals, model roles, treasury actions,
        # and the user saw the raw IPC error). The token is the strong factor (SEC-06); demanding a
        # header the real client never sends only broke the product. Tokenless callers stay refused
        # by the check below, so omitting Origin still buys nothing.
        if origin.strip() and not origin_allowed(origin):
            raise HTTPException(
                status_code=403, detail="cross-origin panel mutation rejected"
            )
        if not _request_is_authenticated(request):
            raise HTTPException(
                status_code=403,
                detail=(
                    "panel mutation rejected: missing panel session token "
                    "(X-Hermes-Session-Token)"
                ),
            )

    @router.get("/models")
    def models() -> dict:
        return models_payload()

    @router.post("/models/strictness")
    def models_strictness(payload: dict, request: Request) -> dict:
        _guard_origin(request)
        from godsv.pause import save_strictness

        try:
            level = save_strictness(runtime_dir(), str((payload or {}).get("level") or "").strip())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"strictness": {"level": level}}

    @router.post("/models/roles")
    def models_roles(payload: dict, request: Request) -> dict:
        _guard_origin(request)
        try:
            return set_role_payload(
                str(payload.get("role") or ""),
                str(payload.get("provider") or ""),
                str(payload.get("model") or ""),
                payload.get("reasoning") or None,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/models/test")
    def models_test(payload: dict, request: Request) -> dict:
        _guard_origin(request)
        provider = str(payload.get("provider") or "").strip()
        model = str(payload.get("model") or "").strip()
        if not provider or not model:
            raise HTTPException(status_code=400, detail="provider and model required")
        return test_model_payload(provider, model)

    @router.get("/treasury/credentials")
    def treasury_credentials() -> dict:
        return credentials_payload()

    @router.post("/treasury/credentials")
    def treasury_credentials_add(payload: dict, request: Request) -> dict:
        _guard_origin(request)
        try:
            return add_credential_payload(
                service=str(payload.get("service", "")),
                name=str(payload.get("name", "")),
                secret=str(payload.get("secret", "")),
                description=str(payload.get("description", "")),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/treasury/credentials/revoke")
    def treasury_credentials_revoke(payload: dict, request: Request) -> dict:
        _guard_origin(request)
        try:
            return revoke_credential_payload(str(payload.get("handle", "")))
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/treasury/credentials/test")
    def treasury_credentials_test(payload: dict, request: Request) -> dict:
        _guard_origin(request)
        try:
            return test_credential_payload(str(payload.get("handle", "")))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/treasury/grants")
    def treasury_grants_add(payload: dict, request: Request) -> dict:
        _guard_origin(request)
        try:
            return grant_credential_payload(
                handle=str(payload.get("handle", "")),
                tool_id=str(payload.get("tool_id", "")),
                ttl=int(payload.get("ttl") or 3600),
                purpose=str(payload.get("purpose", "")),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/treasury/grants/revoke")
    def treasury_grants_revoke(payload: dict, request: Request) -> dict:
        _guard_origin(request)
        try:
            return revoke_grant_payload(str(payload.get("grant_id", "")))
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.get("/tools")
    def tools() -> dict:
        return tools_payload()

    @router.get("/tools/{name}")
    def tools_detail(name: str) -> dict:
        try:
            return tool_detail_payload(name)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/tools/{name}/action")
    def tools_action(name: str, payload: dict, request: Request) -> dict:
        _guard_origin(request)
        try:
            return tool_action_payload(name, str(payload.get("action", "")))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc


    @router.post("/tools/{name}/promote")
    def tools_promote(name: str, payload: dict, request: Request) -> dict:
        _guard_origin(request)
        from godsv.capability import promote_tool

        result = promote_tool(
            runtime_dir(),
            name,
            evidence_projects=list((payload or {}).get("evidence_projects") or []),
        )
        _audit("tools_promote", {"name": name, "promoted": bool(result.get("promoted"))})
        return result

    @router.get("/cu")
    def cu() -> dict:
        return cu_payload()

    @router.post("/cu/demo")
    def cu_demo(request: Request) -> dict:
        _guard_origin(request)
        try:
            return cu_demo_payload()
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.get("/evals")
    def evals() -> dict:
        return evals_payload()

    @router.post("/evals/run")
    def evals_run(request: Request) -> dict:
        _guard_origin(request)
        try:
            return evals_run_payload()
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.get("/completion")
    def completion(session_id: str = "") -> dict:
        return completion_payload(session_id=session_id)

    @router.post("/completion/verify")
    def completion_verify(payload: dict, request: Request) -> dict:
        _guard_origin(request)
        try:
            return completion_verify_payload(payload or {})
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/completion/override")
    def completion_override(payload: dict, request: Request) -> dict:
        _guard_origin(request)
        try:
            return completion_override_payload(payload or {})
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/completion/goal")
    def completion_goal(payload: dict, request: Request) -> dict:
        _guard_origin(request)
        try:
            return completion_goal_payload(payload or {})
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/completion/trajectory")
    def completion_trajectory(payload: dict, request: Request) -> dict:
        _guard_origin(request)
        try:
            return completion_trajectory_payload(payload or {})
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/completion/criteria")
    def completion_criteria(payload: dict, request: Request) -> dict:
        _guard_origin(request)
        from godsv.completion import CriterionConflict

        try:
            return completion_criteria_payload(payload or {})
        except CriterionConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/goal")
    def goal_set(payload: dict, request: Request) -> dict:
        _guard_origin(request)
        try:
            return goal_set_payload(payload or {})
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/approvals")
    def approvals() -> dict:
        return approvals_payload()

    @router.post("/approvals")
    def approvals_set(payload: dict, request: Request) -> dict:
        _guard_origin(request)
        try:
            return approvals_set_payload(payload or {})
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
