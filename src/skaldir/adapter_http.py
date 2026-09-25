"""HTTP/API surface half of the self-expanding tool system (EXPAND — gap A).

The container half of the tool builder (``skaldir.toolbuilder``) mines a
capability from a container's **CLI** surface: it inspects the verbs inside the
pinned image, maps the capability onto one of them, renders an argv, tests the
adapter against the real container and registers it. Its surface probe *detects*
an API/MCP surface (``surface["api"]`` / ``surface["mcp"]``) but cannot adapt it:
``match_verb_template`` refuses and ``tools mine`` exits 3.

This module fixes the **API** half. The chain stays exactly the one the owner
stated, only the surface is HTTP instead of a shell verb:

    discover (OpenAPI/Swagger document or a documented endpoint list)
      → map the operation onto a TYPED capability definition (inputs/outputs)
      → generate a typed adapter (pinned to the surface document bytes)
      → TEST it against the REAL service, checked against an INDEPENDENT local
        oracle (hashlib/base64/len — never the service itself)
      → stage the artifacts
      → register through the EXISTING registration path (``ToolStore.publish``
        + ``capability_health``, version/health/permissions/rollback)
      → call it.

Three rules, the same ones the container half holds itself to:

* **Nothing is generated from an uninspected surface.** Every probe is recorded
  (URL, HTTP status, bytes, error). No machine-readable document ⇒
  ``failure_class="surface_unavailable"`` + the probe list + exit 3. Endpoints
  are never invented, and a capability that maps onto no documented operation is
  refused (``adapter_refused``).
* **An adapter is bound to the bytes that were tested.** ``adapter.json`` is
  digested into the evidence (registration refuses changed bytes), and the
  generated module refuses to call a service whose surface document no longer
  matches the pinned digest (``surface_changed``) — the HTTP counterpart of the
  image-digest refusal.
* **A capability is tested against an INDEPENDENT oracle.** The expected value
  comes from a local stdlib implementation (hashlib/base64/len) or an oracle the
  *document itself* declares (``x-godsv-oracle``); it never comes from the
  service under test. Without an independent oracle the build refuses
  (``oracle_missing``) instead of certifying the service against itself.

Reuse (this module implements no second registry):

* the tool directory layout and every path come from
  ``toolbuilder.container_tool_paths``,
* the registry commit is ``tools.ToolStore.publish`` on the same ``tools.db``,
* the health ledger is ``capability_health`` (``record_verification``/``file_state``),
* the rollback snapshot is ``ContainerToolBuilder._snapshot_for_rollback`` and the
  teardown is ``ContainerToolBuilder.rollback_container_tool`` (label-scoped,
  runtime-agnostic: it removes containers carrying ``godsv.capability=<name>``),
* the CLI wrapper template is the builder's own ``_CONTAINER_WRAPPER``.

Safety: a container started here is always OUR container — ``godsv.owner=skaldir``
plus ``godsv.purpose=test`` while it is being inspected/tested (a warm service
registered by a capability carries ``godsv.purpose=service``), and it is torn
down through ``cleanup_by_labels``/``stop_and_remove`` on ids that came from a
label-filtered listing. Never by name, never blanket: seven foreign
``penpot-local`` containers share this daemon.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import yaml

from . import capability_health as _health
from .docker_runtime import RunConfig, owned_labels
from .toolbuilder import (
    _CONTAINER_WRAPPER,
    CAPABILITY_LABEL,
    PURPOSE_LABEL,
    PURPOSE_SERVICE,
    PURPOSE_TEST,
    AdapterRefusal,
    ContainerToolBuilder,
    SurfaceRefusal,
    _sha256_file,
    _slug,
    _write_text,
    capability_containers,
    container_tool_paths,
)
from .tools import STATUS_APPROVED, ToolSpec

#: The capability kind the registry row carries for an HTTP/API adapter.
HTTP_KIND = "http"

#: The runtime id in the manifest (the container half uses ``docker``).
HTTP_RUNTIME = "http"

#: The evidence file of one HTTP adapter (same tool dir as the container half).
HTTP_EVIDENCE_FILE = "http_evidence.json"

#: The full discovery record (raw document excerpt + every probe).
HTTP_SURFACE_FILE = "surface.json"

#: Well-known machine-readable surface documents, probed in this order. Each
#: probe is recorded with its HTTP status — a 404 is an answer, not a guess.
HTTP_DOCUMENT_PROBES: tuple[str, ...] = (
    "/openapi.json",
    "/openapi.yaml",
    "/openapi.yml",
    "/swagger.json",
    "/swagger.yaml",
    "/v3/api-docs",
    "/api-docs",
    "/api-docs.json",
    "/docs/openapi.json",
    "/swagger/v1/swagger.json",
    "/.well-known/openapi.json",
)

_HTTP_METHODS = ("get", "post", "put", "patch", "delete")
_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_JSON_TYPES = ("string", "integer", "number", "boolean", "object", "array")

#: What an HTTP adapter may look like: no mounts, no credentials, no privilege —
#: and the one host it is allowed to talk to, recorded as data.
HTTP_PERMISSIONS: dict = {
    "network": "service-host",
    "allowed_hosts": [],
    "mounts": [],
    "read_only_rootfs": True,
    "no_new_privileges": True,
    "privileged": False,
    "resource_limits": {"cpu": "1", "ram": "128m"},
    "secrets": [],
    "allowed_domains": [],
    "publish_ports": {},
    "methods": ["GET"],
}

_SAFE_EXCERPT = 4000


# --------------------------------------------------------------------------- #
# HTTP plumbing (stdlib only)
# --------------------------------------------------------------------------- #


def _base_url(value: str) -> str:
    """The service address without a trailing slash."""
    return str(value or "").strip().rstrip("/")


def _host_of(url: str) -> str:
    """The ``host:port`` of a URL — the allowlist entry of the adapter."""
    return urllib.parse.urlsplit(str(url or "")).netloc


def _valid_url(url: str) -> bool:
    parts = urllib.parse.urlsplit(str(url or ""))
    return parts.scheme in ("http", "https") and bool(parts.netloc)


def http_request(
    method: str,
    url: str,
    *,
    body: bytes | None = None,
    headers: dict | None = None,
    timeout: float = 10.0,
) -> dict:
    """One HTTP call — every answer (including 4xx/5xx) comes back as data.

    A transport failure is ``ok: False`` with ``status: 0`` and an error text;
    an HTTP error *status* is a real answer from the service (``answered`` is
    true in both cases when a status exists), which is what makes the surface
    probes evidence instead of guesses.
    """
    request = urllib.request.Request(  # noqa: S310 — http(s) only, checked below
        str(url),
        data=body,
        method=str(method or "GET").upper(),
        headers=dict(headers or {}),
    )
    if not _valid_url(url):
        return {
            "ok": False, "status": 0, "body": b"", "content_type": "",
            "error": f"not an http(s) URL: {url!r}", "answered": False,
        }
    try:
        with urllib.request.urlopen(request, timeout=float(timeout)) as response:  # noqa: S310
            payload = response.read()
            return {
                "ok": True,
                "status": int(getattr(response, "status", 0) or 0),
                "body": payload,
                "content_type": str(response.headers.get("Content-Type") or ""),
                "error": "",
                "answered": True,
            }
    except urllib.error.HTTPError as exc:
        payload = b""
        try:
            payload = exc.read() or b""
        except Exception:  # noqa: BLE001 — a body we cannot read is still a status
            payload = b""
        return {
            "ok": False,
            "status": int(exc.code or 0),
            "body": payload,
            "content_type": str(exc.headers.get("Content-Type") or "") if exc.headers else "",
            "error": f"HTTP {exc.code}",
            "answered": True,
        }
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return {
            "ok": False, "status": 0, "body": b"", "content_type": "",
            "error": f"{type(exc).__name__}: {exc}", "answered": False,
        }


def _json_body(adapter: dict, payload: dict) -> bytes | None:
    """The request body of one call (``None`` for a bodyless operation)."""
    body_inputs = {
        name: spec
        for name, spec in (adapter["operation"]["inputs"] or {}).items()
        if str(spec.get("in")) == "body"
    }
    if not body_inputs:
        return None
    document: dict = {}
    for name in body_inputs:
        if name in payload:
            document[name] = _coerce(payload[name], body_inputs[name].get("type"))
        elif body_inputs[name].get("required"):
            raise AdapterRefusal(f"missing required input: {name}")
    return json.dumps(document, ensure_ascii=False).encode("utf-8")


def _coerce(value: object, type_name: object) -> object:
    """Coerce a payload value to its declared JSON type — or refuse."""
    name = str(type_name or "string")
    if name == "integer":
        try:
            return int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise AdapterRefusal(f"input is not an integer: {value!r}") from exc
    if name == "number":
        try:
            return float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise AdapterRefusal(f"input is not a number: {value!r}") from exc
    if name == "boolean":
        text = str(value).strip().lower()
        if text in ("true", "1", "yes"):
            return True
        if text in ("false", "0", "no"):
            return False
        raise AdapterRefusal(f"input is not a boolean: {value!r}")
    return str(value)


def free_port() -> int:
    """A free host port for a service container (bound, read, released)."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


# --------------------------------------------------------------------------- #
# the raw document → TYPED operations
# --------------------------------------------------------------------------- #


def _load_document_text(raw: bytes) -> object:
    """JSON first, then YAML — never a silent empty document."""
    text = raw.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    try:
        import yaml  # noqa: PLC0415 — optional, only for a YAML document
    except Exception as exc:  # noqa: BLE001
        raise SurfaceRefusal(
            "the surface document is not JSON and YAML is unavailable: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    try:
        return yaml.safe_load(text)
    except Exception as exc:  # noqa: BLE001
        raise SurfaceRefusal(f"the surface document is neither JSON nor YAML: {exc}") from exc


def _resolve_ref(document: dict, node: object, *, depth: int = 0) -> dict:
    """Resolve a LOCAL ``$ref`` (``#/components/schemas/X``), bounded."""
    if not isinstance(node, dict) or depth > 3:
        return node if isinstance(node, dict) else {}
    ref = str(node.get("$ref") or "")
    if not ref or not ref.startswith("#/"):
        return node
    target: object = document
    for part in ref[2:].split("/"):
        if not isinstance(target, dict):
            return {}
        target = target.get(part)
    resolved = _resolve_ref(document, target, depth=depth + 1)
    if resolved:
        merged = {key: value for key, value in node.items() if key != "$ref"}
        merged.update(resolved)
        return merged
    return {}


def _json_type(schema: object) -> str:
    """The declared JSON type of a schema node (``string`` when undeclared)."""
    node = schema if isinstance(schema, dict) else {}
    declared = node.get("type")
    if isinstance(declared, list):
        declared = next((item for item in declared if item != "null"), "")
    name = str(declared or "")
    if name in _JSON_TYPES:
        return name
    if node.get("properties"):
        return "object"
    if node.get("items"):
        return "array"
    return "string"


def _pattern(schema: object) -> str:
    node = schema if isinstance(schema, dict) else {}
    return str(node.get("pattern") or "")


def _inputs_from_schema(document: dict, schema: object, *, location: str) -> dict:
    """Flatten a request schema into typed inputs (``in`` = path/query/body)."""
    node = _resolve_ref(document, schema)
    properties = node.get("properties") or {}
    if str(node.get("type") or "") == "object" or properties:
        required = {str(item) for item in (node.get("required") or [])}
        return {
            str(name): {
                "type": _json_type(_resolve_ref(document, spec)),
                "in": location,
                "required": str(name) in required,
            }
            for name, spec in properties.items()
        }
    return {"body": {"type": _json_type(node), "in": location, "required": True}}


def _outputs_from_schema(document: dict, schema: object) -> dict:
    """The declared outputs of a response schema (typed, pattern-checked)."""
    node = _resolve_ref(document, schema)
    properties = node.get("properties") or {}
    if properties:
        required = {str(item) for item in (node.get("required") or [])}
        return {
            str(name): {
                "type": _json_type(_resolve_ref(document, spec)),
                "required": str(name) in required,
                "pattern": _pattern(_resolve_ref(document, spec)),
            }
            for name, spec in properties.items()
        }
    return {"body": {"type": _json_type(node), "required": True, "pattern": _pattern(node)}}


def _operation_id(method: str, path: str, declared: object) -> str:
    name = str(declared or "").strip()
    return name or f"{method.upper()} {path}"


def _openapi_operations(document: dict) -> list[dict]:
    """OpenAPI 3: paths → typed operations (parameters + JSON request/response)."""
    operations: list[dict] = []
    for path, item in (document.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        shared = [entry for entry in (item.get("parameters") or []) if isinstance(entry, dict)]
        for method, operation in item.items():
            if method.lower() not in _HTTP_METHODS or not isinstance(operation, dict):
                continue
            raw_params = shared + [
                entry for entry in (operation.get("parameters") or []) if isinstance(entry, dict)
            ]
            inputs: dict = {}
            for param in raw_params:
                resolved = _resolve_ref(document, param)
                name = str(resolved.get("name") or "").strip()
                location = str(resolved.get("in") or "query")
                if not name or location not in ("query", "path", "header"):
                    continue
                inputs[name] = {
                    "type": _json_type(resolved.get("schema")),
                    "in": location,
                    "required": bool(resolved.get("required")) or location == "path",
                }
            body = _resolve_ref(document, operation.get("requestBody") or {})
            content = body.get("content") or {}
            json_content = next(
                (value for key, value in content.items() if "json" in str(key).lower()),
                None,
            )
            if isinstance(json_content, dict) and json_content.get("schema"):
                inputs.update(
                    _inputs_from_schema(
                        document, json_content.get("schema"), location="body"
                    )
                )
            produces, schema = _openapi_response(document, operation)
            outputs = _outputs_from_schema(document, schema) if isinstance(schema, dict) else {}
            operations.append(
                {
                    "id": _operation_id(method, path, operation.get("operationId")),
                    "method": method.upper(),
                    "path": str(path),
                    "summary": str(operation.get("summary") or ""),
                    "description": str(operation.get("description") or ""),
                    "tags": [str(tag) for tag in (operation.get("tags") or [])],
                    "inputs": inputs,
                    "outputs": outputs,
                    "produces": produces,
                    "oracle": str(operation.get("x-godsv-oracle") or ""),
                    "documented": True,
                }
            )
    return operations


def _openapi_response(document: dict, operation: dict) -> tuple[str, dict]:
    """The success response of an OpenAPI 3 operation → (media type, schema)."""
    responses = operation.get("responses") or {}
    chosen = None
    for status in sorted(responses, key=lambda item: str(item)):
        if re.fullmatch(r"2\d\d", str(status)) or str(status) in ("default", "200"):
            chosen = responses[status]
            break
    if chosen is None and responses:
        chosen = next(iter(responses.values()))
    resolved = _resolve_ref(document, chosen or {})
    content = resolved.get("content") or {}
    for media, payload in content.items():
        if "json" in str(media).lower():
            schema = _resolve_ref(document, (payload or {}).get("schema") or {})
            return str(media), schema
    if content:
        media, payload = next(iter(content.items()))
        return str(media), _resolve_ref(document, (payload or {}).get("schema") or {})
    return "application/json", {}


def _swagger_operations(document: dict) -> list[dict]:
    """Swagger 2: the same normalization from the 2.0 shapes."""
    operations: list[dict] = []
    for path, item in (document.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        shared = [entry for entry in (item.get("parameters") or []) if isinstance(entry, dict)]
        for method, operation in item.items():
            if method.lower() not in _HTTP_METHODS or not isinstance(operation, dict):
                continue
            raw_params = shared + [
                entry for entry in (operation.get("parameters") or []) if isinstance(entry, dict)
            ]
            inputs: dict = {}
            for param in raw_params:
                resolved = _resolve_ref(document, param)
                name = str(resolved.get("name") or "").strip()
                location = str(resolved.get("in") or "query")
                if location == "body":
                    inputs.update(
                        _inputs_from_schema(
                            document, resolved.get("schema") or {}, location="body"
                        )
                    )
                    continue
                if not name or location not in ("query", "path", "header"):
                    continue
                inputs[name] = {
                    "type": _json_type(resolved),
                    "in": location,
                    "required": bool(resolved.get("required")) or location == "path",
                }
            responses = operation.get("responses") or {}
            chosen = None
            for status in sorted(responses, key=lambda item: str(item)):
                if re.fullmatch(r"2\d\d", str(status)) or str(status) in ("default", "200"):
                    chosen = responses[status]
                    break
            if chosen is None and responses:
                chosen = next(iter(responses.values()))
            schema = _resolve_ref(document, (chosen or {}).get("schema") or {})
            produces = operation.get("produces") or document.get("produces") or ["application/json"]
            outputs = _outputs_from_schema(document, schema) if schema else {}
            operations.append(
                {
                    "id": _operation_id(method, path, operation.get("operationId")),
                    "method": method.upper(),
                    "path": str(path),
                    "summary": str(operation.get("summary") or ""),
                    "description": str(operation.get("description") or ""),
                    "tags": [str(tag) for tag in (operation.get("tags") or [])],
                    "inputs": inputs,
                    "outputs": outputs,
                    "produces": str(next(iter(produces))),
                    "oracle": str(operation.get("x-godsv-oracle") or ""),
                    "documented": True,
                }
            )
    return operations


def _endpoint_list_operations(document: dict) -> list[dict]:
    """A DOCUMENTED endpoint list: every entry must name path + method.

    The entry may carry its typed inputs/outputs and the local oracle that
    certifies it — a list that documents neither is refused (it cannot be
    tested, so it must not be registered).
    """
    operations: list[dict] = []
    for index, entry in enumerate(document.get("endpoints") or []):
        if not isinstance(entry, dict):
            continue
        path = str(entry.get("path") or "").strip()
        method = str(entry.get("method") or "").strip().upper()
        if not path or method.lower() not in _HTTP_METHODS:
            raise SurfaceRefusal(
                f"documented endpoint #{index} names no path/method — a documented "
                "endpoint list must carry both (nothing is invented from a name)"
            )
        inputs = {
            str(name): {
                "type": str((spec or {}).get("type") or "string"),
                "in": str((spec or {}).get("in") or "query"),
                "required": bool((spec or {}).get("required")),
            }
            for name, spec in (entry.get("inputs") or {}).items()
        }
        outputs = {
            str(name): {
                "type": str((spec or {}).get("type") or "string"),
                "required": bool((spec or {}).get("required")),
                "pattern": str((spec or {}).get("pattern") or ""),
            }
            for name, spec in (entry.get("outputs") or {}).items()
        }
        operations.append(
            {
                "id": _operation_id(method, path, entry.get("operationId")),
                "method": method,
                "path": path,
                "summary": str(entry.get("summary") or ""),
                "description": str(entry.get("description") or ""),
                "tags": [str(tag) for tag in (entry.get("tags") or [])],
                "inputs": inputs,
                "outputs": outputs,
                "produces": str(entry.get("produces") or "application/json"),
                "oracle": str(entry.get("oracle") or ""),
                "documented": True,
            }
        )
    return operations


def parse_surface_document(raw: bytes, *, source: str = "") -> dict:
    """A fetched document → ``{kind, operations, sha256, excerpt}`` or a refusal."""
    document = _load_document_text(raw)
    if not isinstance(document, dict):
        raise SurfaceRefusal(
            f"the surface document at {source or 'the service'} is not an object "
            f"(got {type(document).__name__})"
        )
    if document.get("openapi"):
        kind = "openapi3"
        operations = _openapi_operations(document)
    elif document.get("swagger"):
        kind = "swagger2"
        operations = _swagger_operations(document)
    elif isinstance(document.get("endpoints"), list):
        kind = "endpoint-list"
        operations = _endpoint_list_operations(document)
    else:
        raise SurfaceRefusal(
            "not a machine-readable surface document"
            f" ({source or 'the service'}): no 'openapi', 'swagger' or 'endpoints' key — "
            "nothing is generated from a document nobody documented"
        )
    if not operations:
        raise SurfaceRefusal(
            f"the surface document from {source or 'the service'} documents no "
            "operation (paths/endpoints is empty) — there is nothing to adapt"
        )
    text = raw.decode("utf-8", errors="replace")
    return {
        "kind": kind,
        "title": str((document.get("info") or {}).get("title") or document.get("title") or ""),
        "version": str(
            (document.get("info") or {}).get("version") or document.get("version") or ""
        ),
        "operations": operations,
        "document_sha256": hashlib.sha256(raw).hexdigest(),
        "raw_excerpt": text[:_SAFE_EXCERPT],
        "raw_bytes": len(raw),
    }


# --------------------------------------------------------------------------- #
# discovery — evidence-based, refusing when there is no document
# --------------------------------------------------------------------------- #


def _read_document_source(source: str, *, timeout: float) -> dict:
    """The document bytes from a URL or a local path (one code path)."""
    if _valid_url(source):
        call = http_request("GET", source, timeout=timeout)
        return {
            "ok": call["ok"] and call["status"] == 200,
            "status": call["status"],
            "raw": call["body"] if call["ok"] else b"",
            "content_type": call["content_type"],
            "error": call["error"],
        }
    path = Path(source)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return {"ok": False, "status": 0, "raw": b"", "content_type": "", "error": str(exc)}
    return {"ok": True, "status": 200, "raw": raw, "content_type": "", "error": ""}


def inspect_http_surface(
    base_url: str,
    *,
    document: str = "",
    timeout: float = 10.0,
    document_paths: tuple[str, ...] = HTTP_DOCUMENT_PROBES,
) -> dict:
    """Discover a service's HTTP surface — or refuse, with the probes as evidence.

    Three documented sources are accepted, in this order:

    * ``document`` (an explicit OpenAPI/Swagger document or documented endpoint
      list, as a URL or a local path),
    * otherwise the well-known document paths are probed on ``base_url``.

    The winning document is fetched TWICE: bytes that change between two reads
    cannot be pinned (``surface_unstable``), so the build refuses instead of
    generating an adapter whose call-time pin would always fail.
    """
    base = _base_url(base_url)
    result: dict = {
        "inspected": False,
        "failure_class": "",
        "reason": "",
        "base_url": base,
        "host": _host_of(base),
        "source": "",
        "source_kind": "",
        "kind": "",
        "title": "",
        "version": "",
        "operations": [],
        "operation_count": 0,
        "document_sha256": "",
        "document_url": "",
        "raw_excerpt": "",
        "raw_bytes": 0,
        "probes": [],
        "inspected_at": 0.0,
    }
    if not base and not document:
        result["failure_class"] = "service_required"
        result["reason"] = (
            "no service to inspect — give a base URL (a running service) or an image "
            "with the port its API listens on"
        )
        return result

    probes: list[dict] = []
    source = ""
    if document:
        source = str(document)
        read = _read_document_source(source, timeout=timeout)
        probes.append(
            {
                "probe": "document",
                "url": source,
                "path": "",
                "status": read["status"],
                "bytes": len(read["raw"]),
                "ok": bool(read["ok"]),
                "note": "explicitly documented surface" if read["ok"] else read["error"],
            }
        )
        if not read["ok"]:
            result["probes"] = probes
            result["failure_class"] = "surface_unavailable"
            result["reason"] = (
                f"the documented surface {source!r} could not be read: {read['error']} — "
                "no adapter is generated from an unread document"
            )
            return result
        raw = read["raw"]
        result["source_kind"] = "url" if _valid_url(source) else "file"
        result["document_url"] = source if _valid_url(source) else ""
    else:
        if not _valid_url(base):
            result["failure_class"] = "service_required"
            result["reason"] = f"not an http(s) base URL: {base!r}"
            return result
        raw = b""
        for path in document_paths:
            url = f"{base}{path}"
            call = http_request("GET", url, timeout=timeout)
            probes.append(
                {
                    "probe": "document",
                    "url": url,
                    "path": path,
                    "status": call["status"],
                    "bytes": len(call["body"]),
                    "ok": bool(call["ok"] and call["status"] == 200 and call["body"]),
                    "note": call["error"] or f"HTTP {call['status']}",
                }
            )
            if call["ok"] and call["status"] == 200 and call["body"]:
                source = url
                raw = call["body"]
                break
        result["source_kind"] = "url" if raw else ""
        result["document_url"] = source if raw else ""

    result["probes"] = probes
    result["source"] = source
    if not raw:
        tried = ", ".join(
            f"{probe['path']} → {probe['note']}" for probe in probes if probe.get("path")
        ) or "no path"
        result["failure_class"] = "surface_unavailable"
        result["reason"] = (
            f"no machine-readable surface document answered on {base} "
            f"(probed {len(probes)} well-known path(s): {tried}) — pass --document "
            "<openapi.json|endpoints.json> when the documentation lives elsewhere; "
            "endpoints are never invented"
        )
        return result

    try:
        parsed = parse_surface_document(raw, source=source)
    except SurfaceRefusal as exc:
        result["failure_class"] = "surface_unavailable"
        result["reason"] = str(exc)
        return result

    # stability: the document must be pinnable (two reads, same bytes)
    if _valid_url(source):
        second = _read_document_source(source, timeout=timeout)
        if not second["ok"] or second["raw"] != raw:
            result["failure_class"] = "surface_unstable"
            result["reason"] = (
                f"the surface document at {source} changed between two reads "
                f"({len(raw)} vs {len(second['raw'])} bytes) — an unstable document "
                "cannot be pinned, so the adapter would refuse every call"
            )
            return result
    result.update(
        {
            "inspected": True,
            "failure_class": "",
            "reason": "",
            "kind": parsed["kind"],
            "title": parsed["title"],
            "version": parsed["version"],
            "operations": parsed["operations"],
            "operation_count": len(parsed["operations"]),
            "document_sha256": parsed["document_sha256"],
            "raw_excerpt": parsed["raw_excerpt"],
            "raw_bytes": parsed["raw_bytes"],
            "inspected_at": time.time(),
        }
    )
    return result


def surface_summary(surface: dict, *, operation_id: str = "") -> dict:
    """The inspection record, small enough for an envelope (full one on disk)."""
    operations = list(surface.get("operations") or [])
    return {
        "inspected": bool(surface.get("inspected")),
        "reason": str(surface.get("reason") or ""),
        "failure_class": str(surface.get("failure_class") or ""),
        "base_url": str(surface.get("base_url") or ""),
        "host": str(surface.get("host") or ""),
        "source": str(surface.get("source") or ""),
        "source_kind": str(surface.get("source_kind") or ""),
        "kind": str(surface.get("kind") or ""),
        "title": str(surface.get("title") or ""),
        "version": str(surface.get("version") or ""),
        "operation_count": int(surface.get("operation_count") or 0),
        "operations": [
            {
                "id": str(operation.get("id") or ""),
                "method": str(operation.get("method") or ""),
                "path": str(operation.get("path") or ""),
                "summary": str(operation.get("summary") or "")[:120],
                "inputs": sorted((operation.get("inputs") or {}).keys()),
                "outputs": sorted((operation.get("outputs") or {}).keys()),
            }
            for operation in operations[:25]
        ],
        "matched": {
            "operation_id": operation_id,
            "operation": next(
                (
                    {
                        "id": operation.get("id"),
                        "method": operation.get("method"),
                        "path": operation.get("path"),
                    }
                    for operation in operations
                    if str(operation.get("id")) == str(operation_id)
                ),
                None,
            ),
        },
        "document_sha256": str(surface.get("document_sha256") or ""),
        "document_url": str(surface.get("document_url") or ""),
        "raw_bytes": int(surface.get("raw_bytes") or 0),
        "probes": [
            {
                "url": str(probe.get("url") or ""),
                "status": probe.get("status"),
                "bytes": probe.get("bytes"),
                "ok": bool(probe.get("ok")),
                "note": str(probe.get("note") or "")[:120],
            }
            for probe in (surface.get("probes") or [])
        ],
        "excerpt": str(surface.get("raw_excerpt") or "")[:600],
    }


# --------------------------------------------------------------------------- #
# capability → operation (never invented)
# --------------------------------------------------------------------------- #

_BOILERPLATE_TERMS = frozenset(
    {"the", "and", "for", "with", "that", "this", "a", "an", "of", "to", "in", "on", "from"}
)


def _terms(text: str) -> set[str]:
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9_.-]{1,}", str(text or "").lower())
    return {word for word in words if word not in _BOILERPLATE_TERMS}


def match_http_operation(
    capability: str, operations: list[dict], *, operation_id: str = ""
) -> dict:
    """Resolve a capability to ONE documented operation — or refuse.

    An explicit ``operation_id`` must exist in the document (a typo is a
    refusal, not a silent fallback). Without one, the operation is scored on the
    capability's own words against the documented id/summary/description/path/
    tags; no hit or a tie is refused, so an adapter never lands on a guessed
    endpoint.
    """
    if operation_id:
        chosen = next(
            (item for item in operations if str(item.get("id")) == str(operation_id)), None
        )
        if chosen is None:
            listed = ", ".join(str(item.get("id")) for item in operations[:20]) or "none"
            raise AdapterRefusal(
                f"the documented surface offers no operation '{operation_id}' "
                f"(documented: {listed})"
            )
        return chosen
    wanted = _terms(capability)
    scored: list[tuple[int, dict]] = []
    for operation in operations:
        haystack = " ".join(
            [
                str(operation.get("id") or ""),
                str(operation.get("method") or ""),
                str(operation.get("path") or ""),
                str(operation.get("summary") or ""),
                str(operation.get("description") or ""),
                " ".join(str(tag) for tag in (operation.get("tags") or [])),
            ]
        ).lower()
        hits = sum(1 for term in wanted if term in haystack)
        if hits:
            scored.append((hits, operation))
    scored.sort(key=lambda pair: (-pair[0], str(pair[1].get("id"))))
    if not scored:
        listed = ", ".join(
            f"{item.get('method')} {item.get('path')}" for item in operations[:20]
        ) or "none"
        raise AdapterRefusal(
            f"the inspected surface offers no operation for '{capability}' — refused "
            f"instead of inventing an endpoint (documented operations: {listed})"
        )
    top_hits, top = scored[0]
    if len(scored) > 1 and scored[1][0] == top_hits:
        def label(operation: dict) -> str:
            return (
                f"'{operation.get('id')}' ({operation.get('method')} {operation.get('path')})"
            )

        raise AdapterRefusal(
            f"ambiguous capability '{capability}': {label(top)} and "
            f"{label(scored[1][1])} both match — name one with --operation-id"
        )
    return top


# --------------------------------------------------------------------------- #
# the adapter: typed, generated from the inspected surface only
# --------------------------------------------------------------------------- #


def _request_template(operation: dict) -> dict:
    """How the call is rendered: path, query, headers and body — all typed."""
    return {
        "method": str(operation.get("method") or "GET").upper(),
        "path": str(operation.get("path") or "/"),
        "query": sorted(
            name
            for name, spec in (operation.get("inputs") or {}).items()
            if str(spec.get("in")) == "query"
        ),
        "headers": sorted(
            name
            for name, spec in (operation.get("inputs") or {}).items()
            if str(spec.get("in")) == "header"
        ),
        "body": sorted(
            name
            for name, spec in (operation.get("inputs") or {}).items()
            if str(spec.get("in")) == "body"
        ),
        "accept": str(operation.get("produces") or "application/json"),
    }


def generate_http_adapter(
    capability: str,
    *,
    name: str,
    service: dict,
    surface: dict,
    operation: dict,
) -> dict:
    """The TYPED adapter: which operation, which inputs, which outputs, which pin."""
    if not surface.get("inspected"):
        raise SurfaceRefusal(
            "no inspected surface — an HTTP adapter cannot be derived from "
            f"{surface.get('reason') or 'an unknown surface'}"
        )
    if not operation.get("documented"):
        raise AdapterRefusal(
            "the chosen operation is not documented by the surface — refusing to "
            "generate an adapter from an invented endpoint"
        )
    method = str(operation.get("method") or "GET").upper()
    host = _host_of(str(service.get("base_url") or "")) or str(service.get("host") or "")
    permissions = dict(HTTP_PERMISSIONS)
    permissions.update(
        {
            "allowed_hosts": [host] if host else [],
            "allowed_domains": [host] if host else [],
            "methods": [method],
            "network": "bridge" if str(service.get("mode")) == "container" else "service-host",
            "publish_ports": (
                {int(service["container_port"]): int(service["host_port"])}
                if service.get("mode") == "container"
                else {}
            ),
        }
    )
    return {
        "schema_version": 1,
        "kind": HTTP_KIND,
        "name": name,
        "capability": capability,
        "runtime": HTTP_RUNTIME,
        "service": dict(service),
        "operation": {
            "id": str(operation.get("id") or ""),
            "method": method,
            "path": str(operation.get("path") or "/"),
            "summary": str(operation.get("summary") or ""),
            "description": str(operation.get("description") or ""),
            "inputs": dict(operation.get("inputs") or {}),
            "outputs": dict(operation.get("outputs") or {}),
            "produces": str(operation.get("produces") or "application/json"),
            "oracle": str(operation.get("oracle") or ""),
        },
        "request": _request_template(operation),
        "permissions": permissions,
        "pin": {
            "document_url": str(surface.get("document_url") or ""),
            "document_sha256": str(surface.get("document_sha256") or ""),
            "kind": str(surface.get("kind") or ""),
        },
        "surface": {
            "inspected_at": float(surface.get("inspected_at") or time.time()),
            "base_url": str(surface.get("base_url") or ""),
            "source": str(surface.get("source") or ""),
            "kind": str(surface.get("kind") or ""),
            "title": str(surface.get("title") or ""),
            "version": str(surface.get("version") or ""),
            "operations_found": int(surface.get("operation_count") or 0),
            "operation": str(operation.get("id") or ""),
            "document_sha256": str(surface.get("document_sha256") or ""),
        },
        "timeout_seconds": 30,
        "created_at": time.time(),
        "created_by": "godsv-toolbuilder",
    }


def render_http_request(adapter: dict, payload: dict) -> dict:
    """Render one call from a payload — every input is validated, unknown refused."""
    inputs = dict((adapter.get("operation") or {}).get("inputs") or {})
    values: dict[str, object] = {}
    for param, spec in inputs.items():
        if param in payload:
            values[param] = _coerce(payload[param], spec.get("type"))
        elif spec.get("required"):
            raise AdapterRefusal(f"missing required input: {param}")
        else:
            values[param] = "" if str(spec.get("type") or "string") == "string" else None
    unknown = sorted(set(payload) - set(inputs))
    if unknown:
        raise AdapterRefusal("unknown input(s) for this adapter: " + ", ".join(unknown))

    base = _base_url(str((adapter.get("service") or {}).get("base_url") or ""))
    host = str((adapter.get("service") or {}).get("host") or "")
    operation = dict(adapter.get("operation") or {})
    path = str(operation.get("path") or "/")
    query: dict[str, object] = {}
    headers: dict[str, str] = {
        "Accept": str((adapter.get("request") or {}).get("accept") or "application/json")
    }
    for param, spec in inputs.items():
        value = values.get(param)
        location = str(spec.get("in") or "query")
        if location == "path":
            path = path.replace("{" + param + "}", urllib.parse.quote(str(value), safe=""))
        elif location == "header":
            headers[param] = str(value)
        elif location == "query":
            if value not in ("", None):
                query[param] = value
    url = f"{base}{path}"
    if query:
        url = f"{url}?{urllib.parse.urlencode(query)}"
    return {
        "method": str(operation.get("method") or "GET").upper(),
        "url": url,
        "path": path,
        "query": query,
        "headers": headers,
        "host": _host_of(url) or host,
    }


def _value_matches(value: object, spec: dict) -> str:
    """``""`` when the value satisfies the declared output — else the reason."""
    declared = str(spec.get("type") or "string")
    checks = {
        "string": lambda item: isinstance(item, str),
        "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
        "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
        "boolean": lambda item: isinstance(item, bool),
        "object": lambda item: isinstance(item, dict),
        "array": lambda item: isinstance(item, list),
    }
    check = checks.get(declared)
    if check is not None and not check(value):
        return f"'{declared}' expected, got {type(value).__name__}"
    pattern = str(spec.get("pattern") or "")
    if pattern and isinstance(value, str) and not re.fullmatch(pattern, value):
        return f"value does not match {pattern}: {value[:60]!r}"
    return ""


def parse_http_response(
    adapter: dict, status: int, body: bytes, content_type: str = ""
) -> tuple[bool, dict, str]:
    """Parse the service's answer into the declared outputs (typed, checked)."""
    if not (200 <= int(status) < 300):
        text = body.decode("utf-8", errors="replace")[:200]
        return False, {}, f"the service answered HTTP {status}: {text}"
    outputs = dict((adapter.get("operation") or {}).get("outputs") or {})
    media = str((adapter.get("operation") or {}).get("produces") or "application/json")
    text = body.decode("utf-8", errors="replace")
    parsed: object = text
    if "json" in media.lower() or "json" in str(content_type).lower():
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            return False, {}, f"the answer is not JSON: {exc}: {text[:120]!r}"
    collected: dict = {}
    for out_name, spec in outputs.items():
        if isinstance(parsed, dict):
            if out_name not in parsed:
                if spec.get("required"):
                    declare = "declared required"
                    return False, {}, (
                        f"output '{out_name}' is missing from the answer ({declare}); "
                        f"keys: {sorted(parsed)[:10]}"
                    )
                continue
            value: object = parsed[out_name]
        else:
            value = parsed
        problem = _value_matches(value, spec)
        if problem:
            return False, {}, f"output '{out_name}': {problem}"
        collected[out_name] = value
    if not collected:
        return False, {}, "no declared output was found in the answer"
    return True, collected, ""


# --------------------------------------------------------------------------- #
# the INDEPENDENT oracle (never the service under test)
# --------------------------------------------------------------------------- #


def _primary_output_key(adapter: dict, prefer: tuple[str, ...] = ()) -> str:
    outputs = dict((adapter.get("operation") or {}).get("outputs") or {})
    for key in prefer:
        if key in outputs:
            return key
    for key, spec in outputs.items():
        if str(spec.get("type") or "") == "string":
            return str(key)
    return str(next(iter(outputs), ""))


def _text_input(payload: dict) -> str:
    if "text" not in payload:
        raise AdapterRefusal(
            "the local oracle needs a string input named 'text' — this operation "
            "cannot be certified by it"
        )
    return str(payload["text"])


def _oracle_sha256(adapter: dict, payload: dict) -> dict:
    key = _primary_output_key(adapter, ("sha256", "digest", "hash", "checksum"))
    return {key: hashlib.sha256(_text_input(payload).encode("utf-8")).hexdigest()}


def _oracle_md5(adapter: dict, payload: dict) -> dict:
    key = _primary_output_key(adapter, ("md5", "digest", "hash", "checksum"))
    return {
        key: hashlib.md5(  # noqa: S324 — a digest, not a security decision
            _text_input(payload).encode("utf-8"), usedforsecurity=False
        ).hexdigest()
    }


def _oracle_base64(adapter: dict, payload: dict) -> dict:
    key = _primary_output_key(adapter, ("base64", "encoded", "value"))
    return {key: base64.b64encode(_text_input(payload).encode("utf-8")).decode("ascii")}


def _oracle_upper(adapter: dict, payload: dict) -> dict:
    key = _primary_output_key(adapter, ("upper", "uppercase", "text", "value"))
    return {key: _text_input(payload).upper()}


def _oracle_length(adapter: dict, payload: dict) -> dict:
    key = _primary_output_key(adapter, ("length", "count", "size", "words"))
    return {key: len(_text_input(payload))}


def _oracle_echo(adapter: dict, payload: dict) -> dict:
    key = _primary_output_key(adapter, ("echo", "text", "value"))
    return {key: _text_input(payload)}


#: The local oracles: an id, the words that map a documented operation onto it,
#: the output key it prefers, and a stdlib implementation. Every one of them is
#: independent of the service being tested.
HTTP_ORACLES: tuple[dict, ...] = (
    {
        "id": "sha256-text",
        "terms": ("sha-256", "sha256", "sha 256", "digest", "hash", "checksum"),
        "fn": _oracle_sha256,
        "detail": "hashlib.sha256 of the input (local stdlib)",
    },
    {
        "id": "md5-text",
        "terms": ("md5", "md-5"),
        "fn": _oracle_md5,
        "detail": "hashlib.md5 of the input (local stdlib)",
    },
    {
        "id": "base64-text",
        "terms": ("base64", "base-64"),
        "fn": _oracle_base64,
        "detail": "base64.b64encode of the input (local stdlib)",
    },
    {
        "id": "upper-text",
        "terms": ("uppercase", "upper-case", "to upper"),
        "fn": _oracle_upper,
        "detail": "str.upper() of the input (local stdlib)",
    },
    {
        "id": "length-text",
        "terms": ("length", "word count", "wordcount", "size"),
        "fn": _oracle_length,
        "detail": "len() of the input (local stdlib)",
    },
    {
        "id": "echo-text",
        "terms": ("echo", "identity", "reflect"),
        "fn": _oracle_echo,
        "detail": "the input itself (the documented effect is identity)",
    },
)


def _oracle_by_id(oracle_id: str) -> dict:
    found = next((item for item in HTTP_ORACLES if item["id"] == oracle_id), None)
    if found is None:
        known = ", ".join(item["id"] for item in HTTP_ORACLES)
        raise AdapterRefusal(
            f"no local oracle named '{oracle_id}' — this capability cannot be "
            f"certified independently (known oracles: {known})"
        )
    return found


def resolve_http_oracle(adapter: dict, *, oracle: str = "") -> dict:
    """The local oracle for this adapter — explicit, documented, or matched.

    Order: ``oracle`` (caller) → ``x-godsv-oracle`` in the surface document →
    the operation's own documented words. Nothing matched ⇒ ``AdapterRefusal``:
    a capability without an independent check is never registered.
    """
    operation = dict(adapter.get("operation") or {})
    if oracle:
        return _oracle_by_id(str(oracle))
    declared = str(operation.get("oracle") or "")
    if declared:
        return _oracle_by_id(declared)
    haystack = " ".join(
        [
            str(operation.get("id") or ""),
            str(operation.get("path") or ""),
            str(operation.get("summary") or ""),
            str(operation.get("description") or ""),
        ]
    ).lower()
    scored: list[tuple[int, dict]] = []
    for item in HTTP_ORACLES:
        hits = sum(1 for term in item["terms"] if term in haystack)
        if hits:
            scored.append((hits, item))
    scored.sort(key=lambda pair: (-pair[0], pair[1]["id"]))
    if not scored:
        known = ", ".join(item["id"] for item in HTTP_ORACLES)
        raise AdapterRefusal(
            f"no independent local oracle matches operation '{operation.get('id')}' — "
            "a capability is only registered when it can be checked independently "
            f"(pass --oracle <id> or declare x-godsv-oracle in the document; known: {known})"
        )
    if len(scored) > 1 and scored[1][0] == scored[0][0]:
        raise AdapterRefusal(
            f"ambiguous oracle for '{operation.get('id')}': {scored[0][1]['id']} and "
            f"{scored[1][1]['id']} both match — name one with --oracle"
        )
    return scored[0][1]


def expected_http_outputs(adapter: dict, payload: dict, *, oracle: str = "") -> tuple[dict, dict]:
    """The independently computed outputs + the oracle that produced them."""
    chosen = resolve_http_oracle(adapter, oracle=oracle)
    expected = dict(chosen["fn"](adapter, dict(payload)))
    if not expected:
        raise AdapterRefusal(
            f"the local oracle '{chosen['id']}' produced no expected output — "
            "nothing can be certified"
        )
    return expected, chosen


# --------------------------------------------------------------------------- #
# the generated artifacts (stdlib only, self-contained)
# --------------------------------------------------------------------------- #

_HTTP_MODULE = '''"""HTTP adapter '__TOOL__' — generated by the skaldir tool builder.

Self-contained (stdlib only): it reads ``adapter.json`` next to this file,
verifies that the service still serves the PINNED surface document, performs the
documented request and returns the declared outputs. A changed surface REFUSES
the call instead of talking to an API nobody inspected — the HTTP counterpart of
the container adapter's image-digest refusal.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

_HERE = pathlib.Path(__file__).resolve().parent
ADAPTER = json.loads((_HERE / "adapter.json").read_text(encoding="utf-8"))


def base_url() -> str:
    """The service address — an explicit override wins (a restarted container).

    Both spellings are read here on purpose: this module is generated into the
    runtime and must stay stdlib-only, so it cannot import the product's env
    helper. The pre-rename name keeps working for adapters generated before the
    rename; ``SKALDIR_HTTP_BASE_URL`` wins when both are set.
    """
    import os

    return str(
        os.environ.get("SKALDIR_HTTP_BASE_URL")
        or os.environ.get("GODSV_HTTP_BASE_URL")
        or (ADAPTER.get("service") or {}).get("base_url")
        or ""
    ).rstrip("/")


def live_surface_digest() -> str:
    """sha256 of the document the service serves right now ('' when unpinned)."""
    pin = ADAPTER.get("pin") or {}
    url = str(pin.get("document_url") or "")
    if not url:
        return ""
    try:
        with urllib.request.urlopen(  # noqa: S310 — http(s) URL from the adapter
            url, timeout=float(ADAPTER.get("timeout_seconds") or 30)
        ) as response:
            return hashlib.sha256(response.read()).hexdigest()
    except Exception:  # noqa: BLE001 — unreachable is reported, never a pass
        return "unreachable"


def verify_surface() -> None:
    """Refuse when the live surface document no longer matches the pinned digest."""
    pin = ADAPTER.get("pin") or {}
    pinned = str(pin.get("document_sha256") or "")
    if not pinned or not str(pin.get("document_url") or ""):
        return
    actual = live_surface_digest()
    if actual != pinned:
        raise RuntimeError(
            "surface digest mismatch: the adapter pins "
            + pinned
            + ", the service serves "
            + (actual or "nothing")
            + " — refusing to call an API that changed after the test"
        )


def _coerce(value, type_name) -> object:
    name = str(type_name or "string")
    if name == "integer":
        return int(value)
    if name == "number":
        return float(value)
    if name == "boolean":
        text = str(value).strip().lower()
        if text in ("true", "1", "yes"):
            return True
        if text in ("false", "0", "no"):
            return False
        raise ValueError("input is not a boolean: " + repr(value))
    return str(value)


def render(payload: dict) -> dict:
    inputs = dict(ADAPTER["operation"]["inputs"])
    values = {}
    for param, spec in inputs.items():
        if param in payload:
            values[param] = _coerce(payload[param], spec.get("type"))
        elif spec.get("required"):
            raise ValueError("missing required input: " + param)
        else:
            values[param] = "" if str(spec.get("type") or "string") == "string" else None
    unknown = sorted(set(payload) - set(inputs))
    if unknown:
        raise ValueError("unknown input(s): " + ", ".join(unknown))
    path = str(ADAPTER["operation"]["path"])
    query = {}
    headers = {"Accept": str((ADAPTER.get("request") or {}).get("accept") or "application/json")}
    body = {}
    for param, spec in inputs.items():
        value = values.get(param)
        location = str(spec.get("in") or "query")
        if location == "path":
            path = path.replace("{" + param + "}", urllib.parse.quote(str(value), safe=""))
        elif location == "header":
            headers[param] = str(value)
        elif location == "body":
            body[param] = value
        elif value not in ("", None):
            query[param] = value
    url = base_url() + path
    if query:
        url = url + "?" + urllib.parse.urlencode(query)
    data = json.dumps(body).encode("utf-8") if body else None
    if data is not None:
        headers["Content-Type"] = "application/json"
    return {"method": ADAPTER["operation"]["method"], "url": url, "headers": headers, "data": data}


def call(payload: dict) -> dict:
    """Call the pinned service once and return the declared outputs."""
    verify_surface()
    request = render(payload)
    http_request = urllib.request.Request(
        request["url"], data=request["data"], method=request["method"], headers=request["headers"]
    )
    try:
        with urllib.request.urlopen(  # noqa: S310 — http(s) URL from the adapter
            http_request, timeout=float(ADAPTER.get("timeout_seconds") or 30)
        ) as response:
            status = int(response.status)
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            "the service answered HTTP " + str(exc.code) + ": " + (exc.read() or b"")[:200].decode(
                "utf-8", errors="replace"
            )
        ) from exc
    text = raw.decode("utf-8", errors="replace")
    parsed = json.loads(text) if "json" in str(
        (ADAPTER.get("operation") or {}).get("produces") or "application/json"
    ).lower() else text
    out = {}
    for name, spec in dict(ADAPTER["operation"]["outputs"]).items():
        if isinstance(parsed, dict):
            if name not in parsed:
                if spec.get("required"):
                    raise RuntimeError("declared output " + name + " is missing from the answer")
                continue
            value = parsed[name]
        else:
            value = parsed
        pattern = str(spec.get("pattern") or "")
        if pattern and isinstance(value, str) and not re.fullmatch(pattern, value):
            raise RuntimeError("output " + name + " does not match " + pattern)
        out[name] = value
    if not out:
        raise RuntimeError("no declared output was found in the answer (HTTP " + str(status) + ")")
    return out


def main(**payload) -> dict:
    return call(payload)


if __name__ == "__main__":  # pragma: no cover
    print(json.dumps(main(**json.loads(sys.argv[1] if len(sys.argv) > 1 else "{}"))))
'''


_HTTP_TEST = '''"""Adapter test for '__TOOL__' (the REAL service, or skipped).

The expectation comes from a local stdlib implementation (the independent
oracle), never from the service under test.
"""
import json
from importlib import util as _util
from pathlib import Path
import urllib.request

import pytest

_HERE = Path(__file__).resolve().parents[1]
_ADAPTER = json.loads((_HERE / "adapter.json").read_text(encoding="utf-8"))
_EXPECTED = __EXPECTED__
_PAYLOAD = __PAYLOAD__
_OUTPUT_KEY = __OUTPUT_KEY__


def _document_url() -> str:
    return str((_ADAPTER.get("pin") or {}).get("document_url") or "")


def _service_answers() -> bool:
    url = _document_url()
    if not url:
        return False
    try:
        with urllib.request.urlopen(url, timeout=5) as response:  # noqa: S310
            return int(getattr(response, "status", 0) or 0) == 200
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _service_answers(), reason="the HTTP service of this adapter is not reachable"
)


def _module():
    spec = _util.spec_from_file_location("_http_adapter", _HERE / "__MODULE__.py")
    module = _util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_adapter_returns_the_independently_computed_value():
    assert _module().call(_PAYLOAD)[_OUTPUT_KEY] == _EXPECTED


def test_adapter_refuses_a_changed_pinned_surface():
    module = _module()
    original = str((module.ADAPTER.get("pin") or {}).get("document_sha256") or "")
    module.ADAPTER["pin"]["document_sha256"] = "0" * 64
    try:
        with pytest.raises(RuntimeError):
            module.call(_PAYLOAD)
    finally:
        module.ADAPTER["pin"]["document_sha256"] = original
    assert original == (_ADAPTER.get("pin") or {}).get("document_sha256")
'''


# --------------------------------------------------------------------------- #
# builder
# --------------------------------------------------------------------------- #


def http_tool_paths(runtime: str | Path, name: str) -> dict:
    """Every path of one HTTP tool — the container layout, one evidence name.

    The tool directory, adapter, surface record, manifest, wrapper, tests,
    rollback snapshots and cid file are the builder's own layout (reused, so
    ``tools rollback`` and the discovery index keep working); only the evidence
    file name carries the runtime (``http_evidence.json``).
    """
    paths = dict(container_tool_paths(runtime, name))
    paths["evidence"] = Path(runtime) / "generated_tools" / str(name) / HTTP_EVIDENCE_FILE
    return paths


def _service_cid_file(runtime: str | Path, name: str, service: dict) -> Path:
    """The cid file of a service container: what the adapter recorded, else the layout."""
    recorded = str((service or {}).get("cid_file") or "")
    return Path(recorded or http_tool_paths(runtime, name)["cid_file"])


class HttpToolBuilder(ContainerToolBuilder):
    """The HTTP/API half: discover → adapt → TEST → register → call → rollback.

    Inherits the container builder's pipeline plumbing (runtime dir, tools DB,
    health ledger, build queue, search-before-build, rollback snapshots) and
    replaces the container stages with the HTTP ones.
    """

    # -- (2) discover --------------------------------------------------------- #
    def inspect_http(
        self, *, base_url: str = "", document: str = "", timeout: float = 10.0
    ) -> dict:
        """One HTTP surface inspection (the seam tests inject their own)."""
        return inspect_http_surface(base_url, document=document, timeout=timeout)

    # -- (2)+(3)+(4) mine: discover → adapt → test ---------------------------- #
    def mine_http(
        self,
        capability: str,
        *,
        base_url: str = "",
        image: str = "",
        image_digest: str = "",
        container_port: int = 0,
        service_argv: list[str] | None = None,
        document: str = "",
        name: str = "",
        operation_id: str = "",
        oracle: str = "",
        test_input: dict | None = None,
        expect: str = "",
        allow_mutating: bool = False,
        pull: bool = False,
        session_id: str = "",
        timeout: float = 10.0,
    ) -> dict:
        """Discover an HTTP surface, generate a typed adapter, TEST it for real.

        Nothing is staged before the surface was inspected (a documented
        operation) and the adapter passed a test against the real service with an
        independent oracle. A service container this build starts is ``ours`` by
        label and is torn down again before the result is returned — a failed
        build never leaves a container behind.
        """
        manager = self.manager()
        started = time.time()
        name = _slug(name or capability)
        result: dict = {
            "ok": False, "name": name, "capability": capability, "runtime": HTTP_RUNTIME,
            "search": {}, "surface": {}, "adapter": {}, "test": {}, "service": {},
            "staged": False, "stages": [], "failure_class": "", "reason": "",
            "container": {}, "document": "",
        }

        def stage(label: str, ok: bool, **extra: object) -> None:
            result["stages"].append(
                {"stage": label, "ok": bool(ok),
                 "seconds": round(time.time() - started, 3), **extra}
            )

        # (1) search — never build what already exists
        search = self.search_existing_adapter(capability, name=name)
        result["search"] = search
        if name and str((search.get("match") or {}).get("name") or "") == name:
            result["search"]["decision"] = "repair"
            search["decision"] = "repair"
        stage("search", search["decision"] != "reuse", decision=search["decision"])
        if search["decision"] == "reuse":
            match = search.get("match") or {}
            result["failure_class"] = "reuse"
            result["reason"] = (
                f"reuse instead of duplicate: '{match.get('name')}' "
                f"({match.get('surface')}) already provides this capability"
            )
            self._record("", "http_mine", capability=capability, name=name,
                         decision="reuse", match=match)
            return result

        # (2) resolve the service: a container of ours, or a running base URL
        service: dict = {}
        started_container: dict = {}
        if image:
            digest = image_digest or manager.resolve_digest(image)
            if pull and not digest:
                pulled = manager.pull(image)
                digest = pulled.stdout.strip() if pulled.ok else ""
                if not pulled.ok:
                    result["failure_class"] = pulled.failure_class or "docker_error"
                    result["reason"] = f"pull failed ({pulled.failure_class}): {pulled.detail}"
                    stage("resolve_service", False, reason=result["reason"])
                    return result
            if not digest:
                result["failure_class"] = "image_missing"
                result["reason"] = (
                    f"image '{image}' is not local and no pinned digest is known — run "
                    "with --pull (or pin a digest) instead of mining from a mutable tag"
                )
                stage("resolve_service", False, reason=result["reason"])
                return result
            if not container_port:
                result["failure_class"] = "port_required"
                result["reason"] = (
                    "a container service needs the port its API listens on "
                    "(--port 8080) — the host port is chosen by the builder"
                )
                stage("resolve_service", False, reason=result["reason"])
                return result
            host_port = free_port()
            base_url = f"http://127.0.0.1:{host_port}"
            service = {
                "mode": "container",
                "image": image,
                "image_digest": digest,
                "container_port": int(container_port),
                "host_port": int(host_port),
                "base_url": base_url,
                "argv": list(service_argv or []),
                "cid_file": str(http_tool_paths(self.runtime, name)["cid_file"]),
            }
            started_container = self._start_http_service(
                name=name, service=service, purpose=PURPOSE_TEST
            )
            result["container"] = started_container
            if not started_container.get("started"):
                result["failure_class"] = (
                    started_container.get("failure_class") or "service_unavailable"
                )
                result["reason"] = (
                    "the API service container did not start: "
                    f"{started_container.get('reason')}"
                )
                stage("resolve_service", False, reason=result["reason"])
                return result
            stage("resolve_service", True, mode="container", image=image,
                  image_digest=digest, host_port=host_port, cid=started_container.get("cid"))
            ready = self._wait_for_service(base_url, timeout=max(20.0, timeout * 3))
            stage("service_ready", bool(ready.get("ready")), detail=str(ready.get("detail"))[:160])
            if not ready.get("ready"):
                self._teardown_service(name)
                result["failure_class"] = "service_unavailable"
                result["reason"] = (
                    f"the API service at {base_url} did not answer within the startup "
                    f"window: {ready.get('detail')}"
                )
                return result
        elif base_url:
            service = {"mode": "external", "base_url": _base_url(base_url), "argv": []}
            stage("resolve_service", True, mode="external", base_url=service["base_url"])
        else:
            result["failure_class"] = "service_required"
            result["reason"] = (
                "no service to inspect — give --base-url of a running service, or "
                "--image with --port for a container whose API must be adapted"
            )
            stage("resolve_service", False, reason=result["reason"])
            return result
        result["service"] = service
        result["base_url"] = service["base_url"]

        # (2) discover the HTTP surface (document, or a documented endpoint list)
        try:
            surface = self.inspect_http(
                base_url=service["base_url"], document=document, timeout=timeout
            )
        except SurfaceRefusal as exc:
            surface = {"inspected": False, "failure_class": "surface_unavailable",
                       "reason": str(exc), "operations": [], "probes": []}
        result["surface"] = surface
        result["document"] = str(surface.get("source") or "")
        stage("inspect_surface", bool(surface.get("inspected")),
              operations=int(surface.get("operation_count") or 0),
              kind=str(surface.get("kind") or ""))
        if not surface.get("inspected"):
            self._teardown_service(name)
            result["failure_class"] = surface.get("failure_class") or "surface_unavailable"
            result["reason"] = surface.get("reason") or "the HTTP surface could not be inspected"
            return result

        # (3) map the capability onto a DOCUMENTED operation
        try:
            operation = match_http_operation(
                capability,
                list(surface.get("operations") or []),
                operation_id=operation_id,
            )
        except AdapterRefusal as exc:
            self._teardown_service(name)
            result["failure_class"] = "adapter_refused"
            result["reason"] = str(exc)
            stage("match_operation", False, reason=str(exc))
            return result
        method = str(operation.get("method") or "GET").upper()
        stage("match_operation", True, operation=operation.get("id"), method=method)
        if method in _MUTATING_METHODS and not allow_mutating:
            self._teardown_service(name)
            result["failure_class"] = "mutating_operation"
            result["reason"] = (
                f"'{operation.get('id')}' is documented as {method} — a write needs an "
                "explicit acknowledgement (--allow-mutating); the safe default is a read"
            )
            return result

        try:
            adapter = generate_http_adapter(
                capability, name=name, service=service, surface=surface, operation=operation
            )
        except (AdapterRefusal, SurfaceRefusal) as exc:
            self._teardown_service(name)
            result["failure_class"] = "adapter_refused"
            result["reason"] = str(exc)
            stage("generate_adapter", False, reason=str(exc))
            return result
        result["adapter"] = adapter
        stage("generate_adapter", True, operation=adapter["operation"]["id"],
              inputs=sorted(adapter["operation"]["inputs"]),
              outputs=sorted(adapter["operation"]["outputs"]))

        # (4) TEST against the real service, checked against a local oracle
        payload = dict(test_input or _sample_input(adapter))
        test = self._test_http_adapter(
            adapter, payload=payload, base_url=service["base_url"], expect=expect,
            oracle=oracle, timeout=float(adapter.get("timeout_seconds") or 30),
        )
        result["test"] = test
        stage("test_adapter", bool(test.get("ok")), detail=str(test.get("detail") or "")[:160])
        if not test["ok"]:
            self._teardown_service(name)
            result["failure_class"] = "test_failed"
            result["reason"] = test.get("detail") or "the adapter test failed"
            return result

        # stage the artifacts (still NOT registered — evidence does that)
        paths = http_tool_paths(self.runtime, name)
        result["staged"] = self._stage_http_tool(adapter, paths, test, payload, surface=surface)
        stage("stage", bool(result["staged"]), tool_dir=str(paths["tool_dir"]))
        result["ok"] = bool(result["staged"])
        result["adapter_path"] = str(paths["adapter"])
        result["evidence_path"] = str(paths["evidence"])
        result["tool_dir"] = str(paths["tool_dir"])
        result["wrapper"] = str(paths["wrapper"])
        # the fixture container exists for the test only: nothing is left running
        stopped = self._teardown_service(name)
        result["container"]["stopped"] = stopped
        if stopped:
            stage("teardown", True, detail=f"removed {len(stopped)} container(s) of ours")
        self._record("", "http_mine", capability=capability, name=name,
                     base_url=service["base_url"], operation=adapter["operation"]["id"],
                     tested=bool(test["ok"]))
        return result

    def _start_http_service(
        self, *, name: str, service: dict, purpose: str = PURPOSE_TEST, timeout: float = 60.0
    ) -> dict:
        """Start ONE labelled service container with a published port.

        Ownership is structural: ``owned_labels`` puts ``godsv.owner=skaldir`` on
        it, the purpose label says what it exists for, and
        ``godsv.capability=<name>`` is the only scope a teardown may use later.
        """
        manager = self.manager()
        available, detail = manager.available()
        if not available:
            return {"started": False, "failure_class": "docker_unreachable",
                    "reason": f"docker is not reachable: {detail}"}
        config = RunConfig(
            image=str(service.get("image") or ""),
            image_digest=str(service.get("image_digest") or ""),
            name=f"godsv_http_{name}"[:60],
            entrypoint_argv=list(service.get("argv") or []),
            port_bindings={int(service["container_port"]): int(service["host_port"])},
            resource_limits={"cpu": "1", "ram": "128m"},
            readonly_rootfs=True,
            labels=owned_labels(**{PURPOSE_LABEL: purpose, CAPABILITY_LABEL: str(name)}),
        )
        cid_file = _service_cid_file(self.runtime, name, service)
        try:
            started = manager.start(config, cid_file=cid_file, timeout=timeout)
        except Exception as exc:  # noqa: BLE001 — a start that raises is still reported
            return {"started": False, "failure_class": "docker_error",
                    "reason": f"{type(exc).__name__}: {exc}"[:200]}
        if not getattr(started, "ok", False):
            return {
                "started": False,
                "failure_class": str(getattr(started, "failure_class", "") or "docker_error"),
                "reason": str(getattr(started, "detail", "") or "container start failed")[:300],
                "labels": dict(config.labels),
            }
        return {
            "started": True,
            "reused": False,
            "cid": str(getattr(started, "cid", "") or ""),
            "cid_file": str(cid_file),
            "port_bindings": dict(config.port_bindings),
            "labels": dict(config.labels),
            "reason": f"started one labelled service container for '{name}'",
        }

    def _wait_for_service(
        self, base_url: str, *, path: str = "/", timeout: float = 30.0, interval: float = 0.4
    ) -> dict:
        """Poll a service address until it answers (or the window closes)."""
        deadline = time.time() + max(1.0, float(timeout))
        last: dict = {}
        attempts = 0
        while time.time() < deadline:
            attempts += 1
            last = http_request(
                "GET", f"{_base_url(base_url)}{path}", timeout=min(5.0, float(timeout))
            )
            if last.get("answered"):
                return {"ready": True, "attempts": attempts, "status": last["status"],
                        "detail": f"the service answered HTTP {last['status']}"}
            time.sleep(interval)
        return {"ready": False, "attempts": attempts, "status": last.get("status", 0),
                "detail": str(last.get("error") or "no answer")}

    def _teardown_service(self, name: str) -> list[str]:
        """Remove the containers of ONE capability — by our label, never by name."""
        if not name:
            return []
        result = self.manager().cleanup_by_labels({CAPABILITY_LABEL: str(name)})
        return list((getattr(result, "payload", {}) or {}).get("removed") or [])

    def _test_http_adapter(
        self,
        adapter: dict,
        *,
        payload: dict,
        base_url: str = "",
        expect: str = "",
        oracle: str = "",
        timeout: float = 30.0,
    ) -> dict:
        """Run the generated adapter ONCE against the real service."""
        started = time.time()
        try:
            expected, chosen = expected_http_outputs(adapter, payload, oracle=oracle)
        except AdapterRefusal as exc:
            return {"ok": False, "detail": str(exc), "input": dict(payload),
                    "failure_class": "oracle_missing"}
        primary = _primary_output_key(adapter)
        expected_value = expected.get(primary)
        if expect and str(expect).strip().lower() != str(expected_value).strip().lower():
            return {
                "ok": False, "input": dict(payload), "expected": expected_value,
                "detail": (
                    f"the declared expectation {str(expect)[:80]!r} disagrees with the "
                    f"independent local oracle ({expected_value})"
                ),
                "oracle": chosen["id"],
            }
        effective = dict(adapter)
        if base_url:
            effective["service"] = dict(adapter.get("service") or {}, base_url=_base_url(base_url))
        try:
            request = render_http_request(effective, payload)
        except AdapterRefusal as exc:
            return {"ok": False, "input": dict(payload), "detail": str(exc)}
        call = http_request(
            request["method"], request["url"], timeout=timeout,
            body=_json_body(effective, payload), headers=request["headers"],
        )
        if not call["answered"]:
            return {
                "ok": False, "input": dict(payload), "expected": expected_value,
                "oracle": chosen["id"], "oracle_detail": chosen["detail"],
                "request": {key: request[key] for key in ("method", "url", "query")},
                "failure_class": "service_unavailable",
                "detail": f"the service did not answer: {call['error']}",
                "duration_ms": int((time.time() - started) * 1000),
            }
        ok, outputs, detail = parse_http_response(
            effective, call["status"], call["body"], call["content_type"]
        )
        observed_value = outputs.get(primary)
        matched = bool(ok) and observed_value == expected_value
        return {
            "ok": matched,
            "input": dict(payload),
            "expected": expected_value,
            "observed": observed_value,
            "observed_all": outputs,
            "oracle": chosen["id"],
            "oracle_detail": chosen["detail"],
            "request": {key: request[key] for key in ("method", "url", "query")},
            "status": call["status"],
            "content_type": call["content_type"],
            "body_excerpt": call["body"].decode("utf-8", errors="replace")[:400],
            "surface_document_sha256": str((adapter.get("pin") or {}).get("document_sha256") or ""),
            "surface_document_url": str((adapter.get("pin") or {}).get("document_url") or ""),
            "failure_class": "" if matched else ("output_mismatch" if ok else "unparsable_answer"),
            "detail": "" if matched else (detail or (
                f"the service returned {observed_value!r}, the independent local oracle "
                f"requires {expected_value!r}"
            )),
            "duration_ms": int((time.time() - started) * 1000),
            "permissions": dict(adapter.get("permissions") or {}),
        }

    def _stage_http_tool(
        self,
        adapter: dict,
        paths: dict,
        test: dict,
        payload: dict,
        surface: dict | None = None,
    ) -> bool:
        """Write adapter.json, the module, the wrapper, the manifest and the test."""
        name = str(adapter["name"])
        name_slug = name.replace("-", "_")
        try:
            _write_text(paths["adapter"], json.dumps(adapter, indent=2, sort_keys=True) + "\n")
            if surface:
                _write_text(
                    paths["surface"],
                    json.dumps(surface, indent=2, sort_keys=True, default=str),
                )
            module_name = f"http_{name_slug}"[:60]
            _write_text(
                paths["tool_dir"] / f"{module_name}.py",
                _HTTP_MODULE.replace("__TOOL__", name),
            )
            module_path = paths["tool_dir"] / f"{module_name}.py"
            paths["module"] = module_path
            # the call path (argv + usage ledger) is runtime-agnostic: the
            # builder's own wrapper template is reused, not re-implemented
            paths["wrapper"] = _write_text(
                paths["tool_dir"] / "bin" / name,
                _CONTAINER_WRAPPER.replace("__TOOL__", name)
                .replace("__MODULE__", module_name)
                .replace("__NAME__", repr(name)),
                executable=True,
            )
            output_key = _primary_output_key(adapter)
            _write_text(
                paths["tool_dir"] / "tests" / f"test_{name_slug}.py",
                _HTTP_TEST.replace("__TOOL__", name)
                .replace("__MODULE__", module_name)
                .replace("__EXPECTED__", repr(str(test.get("expected") or "")))
                .replace("__PAYLOAD__", repr(dict(payload)))
                .replace("__OUTPUT_KEY__", repr(output_key)),
            )
            manifest = self._http_manifest(adapter, name=name, status="built",
                                           module_name=module_name)
            _write_text(paths["manifest"], yaml.safe_dump(manifest, sort_keys=False))
            evidence = dict(test)
            evidence.update(
                {
                    "adapter_digest": _sha256_file(paths["adapter"]),
                    "adapter_file": str(paths["adapter"]),
                    "tested_at": time.time(),
                    "registered": False,
                    "runtime": HTTP_RUNTIME,
                }
            )
            _write_text(paths["evidence"], json.dumps(evidence, indent=2, sort_keys=True))
        except OSError as exc:
            self._record("", "http_stage", name=name, error=str(exc))
            return False
        return True

    def _http_manifest(
        self,
        adapter: dict,
        *,
        name: str,
        status: str,
        module_name: str,
        version: str = "0.1.0",
        project_id: str = "",
    ) -> dict:
        """The manifest of an HTTP capability (manifest v2 shape, runtime http)."""
        perms = dict(adapter.get("permissions") or HTTP_PERMISSIONS)
        operation = dict(adapter.get("operation") or {})
        service = dict(adapter.get("service") or {})
        request_method = str(operation.get("method") or "")
        request_path = str(operation.get("path") or "")
        request_line = f"{request_method} {request_path}".strip()
        return {
            "name": name,
            "version": version,
            "scope": "project",
            "project_id": project_id,
            "type": HTTP_RUNTIME,
            "purpose": adapter.get("capability", ""),
            "created_for": adapter.get("capability", ""),
            "created_by": "godsv-toolbuilder",
            "commands_or_tools": [request_line],
            "network": {"allowed_domains": list(perms.get("allowed_domains") or [])},
            "filesystem": {"access": "no host mounts; the service is called over HTTP",
                           "mounts": []},
            "required_secrets": list(perms.get("secrets") or []),
            "dependencies": [],
            "tests": [sys.executable, "-m", "pytest", "-q", "tests/"],
            "risk_level": "low",
            "status": status,
            "created_at": time.time(),
            "last_tested_at": None,
            "runtime": HTTP_RUNTIME,
            "image": str(service.get("image") or ""),
            "image_digest": str(service.get("image_digest") or ""),
            "entrypoint_argv": [request_method, request_path],
            "mounts": [],
            "resource_limits": dict(perms.get("resource_limits") or {}),
            "require_bind_ro": True,
            "healthcheck": {
                "verb": str(operation.get("id") or ""),
                "runtime": HTTP_RUNTIME,
                "method": str(operation.get("method") or ""),
                "path": str(operation.get("path") or ""),
                "document_url": str((adapter.get("pin") or {}).get("document_url") or ""),
            },
            "permissions": perms,
            "service": service,
            "adapter": {"file": "adapter.json", "module": module_name,
                        "operation": str(operation.get("id") or "")},
            "surface": dict(adapter.get("surface") or {}),
            "validation": {"status": "not_declared"},
        }

    # -- (5) register --------------------------------------------------------- #
    def _load_http_tool(self, name: str) -> dict:
        paths = http_tool_paths(self.runtime, name)
        if not paths["adapter"].is_file():
            return {"ok": False, "failure_class": "not_found",
                    "reason": f"no staged HTTP adapter for '{name}'",
                    "paths": {key: str(value) for key, value in paths.items()}}
        try:
            adapter = json.loads(paths["adapter"].read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return {"ok": False, "failure_class": "adapter_unreadable",
                    "reason": f"adapter.json is not readable: {exc}", "paths": {}}
        evidence: dict = {}
        if paths["evidence"].is_file():
            try:
                evidence = json.loads(paths["evidence"].read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                evidence = {}
        return {"ok": True, "adapter": adapter, "evidence": evidence,
                "paths": {key: str(value) for key, value in paths.items()}}

    def http_adapter(self, name: str) -> dict:
        """The typed adapter of a staged/registered HTTP capability."""
        loaded = self._load_http_tool(name)
        if not loaded["ok"]:
            return loaded
        adapter = loaded["adapter"]
        manifest: dict = {}
        path = Path(loaded["paths"]["manifest"])
        if path.is_file():
            try:
                manifest = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except Exception:  # noqa: BLE001 — a broken manifest is reported as absent
                manifest = {}
        return {
            "ok": True,
            "name": name,
            "adapter": adapter,
            "manifest": manifest,
            "permissions": dict(adapter.get("permissions") or {}),
            "test": loaded["evidence"],
            "wrapper": loaded["paths"]["wrapper"],
            "tool_dir": loaded["paths"]["tool_dir"],
        }

    def register_http_tool(
        self, name: str, *, version: str = "", project_id: str = "",
        requested_by: str = "cli",
    ) -> dict:
        """Register a TESTED HTTP adapter — through the existing registration path.

        Refuses (never a green) when the evidence is missing, when the recorded
        test did not pass, or when ``adapter.json`` changed after that test. The
        registry commit is ``ToolStore.publish`` on this builder's own
        ``tools.db``; the health verdict is ``capability_health`` — the same two
        calls the container half registers through.
        """
        loaded = self._load_http_tool(name)
        if not loaded["ok"]:
            return loaded
        adapter = loaded["adapter"]
        evidence = loaded["evidence"]
        paths = http_tool_paths(self.runtime, name)
        digest_now = _sha256_file(Path(loaded["paths"]["adapter"]))
        if not evidence:
            return {
                "ok": False, "failure_class": "unverified",
                "reason": (
                    f"'{name}' has no test evidence — a capability is only registered "
                    "after its adapter ran against the real service"
                ),
            }
        if not evidence.get("ok"):
            return {
                "ok": False, "failure_class": "unverified",
                "reason": (
                    "the recorded adapter test did not pass: "
                    + str(evidence.get("detail") or "no detail")[:200]
                ),
            }
        if str(evidence.get("adapter_digest") or "") != digest_now:
            return {
                "ok": False, "failure_class": "adapter_changed",
                "reason": (
                    "adapter.json changed after the test — the evidence no longer covers "
                    "these bytes; re-run `tools api discover` instead of registering"
                ),
            }

        previous = self.store.get(name)
        version = version or self._next_version(previous)
        module_name = f"http_{str(name).replace('-', '_')}"[:60]
        if paths["manifest"].is_file():
            try:
                manifest_doc = yaml.safe_load(paths["manifest"].read_text(encoding="utf-8")) or {}
                module_name = str((manifest_doc.get("adapter") or {}).get("module") or module_name)
            except Exception:  # noqa: BLE001 — a broken manifest falls back to the layout
                module_name = module_name
        manifest = self._http_manifest(
            adapter, name=name, status="ready", module_name=module_name,
            version=version, project_id=project_id,
        )
        manifest["last_tested_at"] = float(evidence.get("tested_at") or time.time())
        manifest["validation"] = {
            "status": "validated",
            "at": evidence.get("tested_at"),
            "detail": (
                f"adapter called {adapter.get('service', {}).get('base_url')} "
                f"({adapter.get('operation', {}).get('method')} "
                f"{adapter.get('operation', {}).get('path')}): {evidence.get('observed')} == "
                f"{evidence.get('expected')} (local oracle {evidence.get('oracle')})"
            )[:300],
        }
        perms = dict(adapter.get("permissions") or HTTP_PERMISSIONS)
        operation = dict(adapter.get("operation") or {})
        spec = ToolSpec(
            name=name,
            title=f"HTTP: {adapter.get('capability', name)}",
            description=(
                f"{adapter.get('capability')} — calls "
                f"{operation.get('method')} {operation.get('path')} on "
                f"{adapter.get('service', {}).get('base_url')} (pinned to the surface "
                f"document {str((adapter.get('pin') or {}).get('document_sha256'))[:16]}…)"
            ),
            tags=["http", str(operation.get("method") or ""), str(operation.get("id") or ""),
                  str(adapter.get("service", {}).get("mode") or "")],
            kind=HTTP_KIND,
            module=module_name,
            entrypoint="main",
            test_command=[sys.executable, "-m", "pytest", "-q", "tests/"],
            sample_input=dict(evidence.get("input") or {}),
            rationale=(
                f"mined from the {adapter.get('surface', {}).get('kind')} surface of "
                f"{adapter.get('service', {}).get('base_url')}; tested against the real "
                f"service ({evidence.get('observed')} via oracle {evidence.get('oracle')})"
            ),
            provenance=f"http-toolbuilder:{requested_by}",
            status=STATUS_APPROVED,
            version=version,
            scan_summary=(
                "http adapter: documented operation only, pinned surface document, "
                "allowed host recorded, no mounts, no credentials"
            ),
        )
        publish = self.store.publish(
            spec,
            payload={
                "kind": HTTP_KIND,
                "runtime": HTTP_RUNTIME,
                "service": dict(adapter.get("service") or {}),
                "operation": {
                    "id": operation.get("id"),
                    "method": operation.get("method"),
                    "path": operation.get("path"),
                },
                "version": version,
                "adapter_digest": digest_now,
                "surface_document_sha256": str((adapter.get("pin") or {}).get("document_sha256")),
                "tested_at": evidence.get("tested_at"),
            },
        )
        rollback_id = self._snapshot_for_rollback(name, previous)
        _write_text(paths["manifest"], yaml.safe_dump(manifest, sort_keys=False))
        evidence = dict(evidence)
        evidence.update({"registered": True, "registered_at": time.time(),
                         "version": version, "rollback_id": rollback_id})
        _write_text(paths["evidence"], json.dumps(evidence, indent=2, sort_keys=True))
        state = _health.record_verification(
            self.runtime, name, ok=True,
            detail=(
                f"http adapter tested against {adapter.get('service', {}).get('base_url')}: "
                f"{evidence.get('observed')}"
            )[:200],
        )
        health_report = _health.file_state(self.runtime, name)
        self._record("", "http_register", name=name, version=version,
                     status=spec.status, runtime=HTTP_RUNTIME, rollback_id=rollback_id)
        return {
            "ok": True,
            "registered": True,
            "name": name,
            "version": version,
            "status": spec.status,
            "kind": HTTP_KIND,
            "runtime": HTTP_RUNTIME,
            "service": dict(adapter.get("service") or {}),
            "operation": {
                "id": operation.get("id"),
                "method": operation.get("method"),
                "path": operation.get("path"),
            },
            "permissions": perms,
            "health": health_report,
            "health_state": str(state),
            "rollback_id": rollback_id,
            "publish": publish,
            "wrapper": str(paths["wrapper"]),
            "spec": spec.to_dict(),
            "test": {
                "input": evidence.get("input"),
                "expected": evidence.get("expected"),
                "observed": evidence.get("observed"),
                "oracle": evidence.get("oracle"),
                "status": evidence.get("status"),
                "tested_at": evidence.get("tested_at"),
            },
        }

    # -- call / health / rollback --------------------------------------------- #
    def ensure_http_service(self, name: str, *, manager=None) -> dict:
        """Exactly ONE running service container for a capability — or an honest no.

        An already running container of OURS is adopted when its published port
        mapping still matches the adapter (its id came from a label-filtered
        listing); a leftover with the wrong mapping is removed and replaced, so a
        port mapping that was never published cannot be mistaken for a healthy
        service. Foreign containers are invisible to this scope by construction.
        """
        loaded = self._load_http_tool(name)
        if not loaded["ok"]:
            return {"started": False, "failure_class": "not_found", "reason": loaded["reason"]}
        service = dict(loaded["adapter"].get("service") or {})
        mode = str(service.get("mode") or "")
        if mode == "external":
            base = _base_url(str(service.get("base_url") or ""))
            probe = http_request("GET", f"{base}/", timeout=5.0)
            return {
                "started": bool(probe.get("answered")),
                "reused": True,
                "mode": "external",
                "base_url": base,
                "external": True,
                "reason": (
                    f"the adapter points at the external service {base}; nothing is started"
                    if probe.get("answered")
                    else f"the external service {base} does not answer: {probe.get('error')}"
                ),
                "failure_class": "" if probe.get("answered") else "service_unavailable",
            }
        runtime_manager = manager if manager is not None else self.manager()
        available, detail = runtime_manager.available()
        if not available:
            return {"started": False, "failure_class": "docker_unreachable",
                    "reason": f"docker is not reachable: {detail}"}
        wanted = {int(service["container_port"]): int(service["host_port"])}
        for cid in capability_containers(runtime_manager, name):
            if self._container_publishes(runtime_manager, cid, wanted):
                cid_file = _service_cid_file(self.runtime, name, service)
                try:
                    cid_file.parent.mkdir(parents=True, exist_ok=True)
                    cid_file.write_text(cid + "\n", encoding="utf-8")
                except OSError:
                    pass
                return {
                    "started": True, "reused": True, "mode": "container", "cid": cid,
                    "cid_file": str(cid_file),
                    "base_url": f"http://127.0.0.1:{int(service['host_port'])}",
                    "reason": (
                        "a running service container of ours already publishes "
                        f"{wanted} — adopted by label, exactly one is used"
                    ),
                }
            # ours, but not the service this adapter documented: never adopt it
            runtime_manager.stop_and_remove(cid)
        started = self._start_http_service(
            name=name, service=service, purpose=PURPOSE_SERVICE
        )
        if not started.get("started"):
            return started
        base_url = f"http://127.0.0.1:{int(service['host_port'])}"
        ready = self._wait_for_service(base_url, timeout=30.0)
        if not ready.get("ready"):
            self._teardown_service(name)
            return {
                "started": False, "failure_class": "service_unavailable",
                "reason": (
                    f"the service container started but never answered at {base_url}: "
                    f"{ready.get('detail')}"
                ),
            }
        started.update({"mode": "container", "base_url": base_url, "ready": ready})
        return started

    @staticmethod
    def _container_publishes(manager, cid: str, wanted: dict) -> bool:
        """True iff this container is running and publishes exactly ``wanted``."""
        inspect = manager._invoke(  # noqa: SLF001 — the runtime's own docker edge
            ["docker", "inspect", "--format", "{{json .NetworkSettings.Ports}}", cid],
            timeout=20.0,
        )
        if inspect.exit_code != 0:
            return False
        try:
            ports = json.loads(inspect.stdout or "{}")
        except json.JSONDecodeError:
            return False
        published: dict = {}
        for key, bindings in (ports or {}).items():
            container_port = str(key).split("/")[0]
            for binding in bindings or []:
                if binding.get("HostPort"):
                    published[int(container_port)] = int(binding["HostPort"])
        return published == {int(port): int(host) for port, host in wanted.items()}

    def verify_surface_pin(self, adapter: dict, base_url: str = "") -> dict:
        """Does the service still serve the surface document this adapter pins?"""
        pin = dict(adapter.get("pin") or {})
        pinned = str(pin.get("document_sha256") or "")
        url = str(pin.get("document_url") or "")
        if not pinned or not url:
            return {"checked": False, "ok": True, "pinned": pinned, "live": "",
                    "reason": "the adapter pins no document URL — nothing to compare"}
        read = _read_document_source(url, timeout=float(adapter.get("timeout_seconds") or 30))
        if not read["ok"] or not read["raw"]:
            return {"checked": True, "ok": False, "pinned": pinned, "live": "",
                    "failure_class": "service_unavailable",
                    "reason": f"the surface document {url} could not be read: {read['error']}"}
        live = hashlib.sha256(read["raw"]).hexdigest()
        return {
            "checked": True,
            "ok": live == pinned,
            "pinned": pinned,
            "live": live,
            "failure_class": "" if live == pinned else "surface_changed",
            "reason": (
                "the live surface document matches the pinned digest"
                if live == pinned
                else (
                    "the service serves a different surface document than the one that "
                    f"was tested (pinned {pinned[:16]}…, live {live[:16]}…) — re-discover "
                    "instead of calling a changed API"
                )
            ),
        }

    def call_http_tool(
        self, name: str, payload: dict, *, timeout: float = 30.0, ensure_service: bool = True
    ) -> dict:
        """Call a registered HTTP capability — real request, declared outputs."""
        loaded = self._load_http_tool(name)
        if not loaded["ok"]:
            return loaded
        adapter = loaded["adapter"]
        service = dict(adapter.get("service") or {})
        mode = str(service.get("mode") or "")
        ensured: dict = {"started": True, "mode": mode, "reused": True,
                         "base_url": _base_url(str(service.get("base_url") or ""))}
        if mode == "container" and ensure_service:
            ensured = self.ensure_http_service(name)
            if not ensured.get("started"):
                return {
                    "ok": False, "name": name,
                    "failure_class": str(ensured.get("failure_class") or "service_unavailable"),
                    "reason": str(ensured.get("reason") or "the service is not available"),
                    "service": ensured,
                }
        base_url = _base_url(str(ensured.get("base_url") or service.get("base_url") or ""))
        started = time.time()
        pin = self.verify_surface_pin(adapter, base_url)
        if pin["checked"] and not pin["ok"]:
            return {
                "ok": False, "name": name, "mode": mode, "base_url": base_url,
                "failure_class": str(pin.get("failure_class") or "surface_changed"),
                "reason": str(pin.get("reason") or "the pinned surface changed"),
                "pin": pin,
                "duration_ms": int((time.time() - started) * 1000),
            }
        effective = dict(adapter, service=dict(service, base_url=base_url))
        try:
            request = render_http_request(effective, dict(payload))
            body = _json_body(effective, dict(payload))
        except AdapterRefusal as exc:
            return {
                "ok": False, "name": name, "failure_class": "adapter_refused",
                "reason": str(exc), "input": dict(payload),
                "duration_ms": int((time.time() - started) * 1000),
            }
        call = http_request(
            request["method"], request["url"], timeout=timeout,
            body=body, headers=request["headers"],
        )
        if not call["answered"]:
            return {
                "ok": False, "name": name, "mode": mode, "base_url": base_url,
                "failure_class": "service_unavailable",
                "reason": f"the service did not answer: {call['error']}",
                "request": {key: request[key] for key in ("method", "url", "query")},
                "duration_ms": int((time.time() - started) * 1000),
            }
        ok, outputs, detail = parse_http_response(
            effective, call["status"], call["body"], call["content_type"]
        )
        return {
            "ok": bool(ok),
            "name": name,
            "mode": mode,
            "base_url": base_url,
            "input": dict(payload),
            "output": outputs,
            "request": {key: request[key] for key in ("method", "url", "query")},
            "status": call["status"],
            "body_excerpt": call["body"].decode("utf-8", errors="replace")[:400],
            "pin": pin,
            "service": ensured,
            "failure_class": "" if ok else "unparsable_answer",
            "reason": "" if ok else detail,
            "detail": "" if ok else detail,
            "duration_ms": int((time.time() - started) * 1000),
        }

    def http_health(self, name: str, *, probe: bool = False) -> dict:
        """Health of an HTTP capability: ledger state + live service facts."""
        loaded = self._load_http_tool(name)
        if not loaded["ok"]:
            return loaded
        adapter = loaded["adapter"]
        state = _health.file_state(self.runtime, name)
        selectable, why = _health.selectable(self.runtime, name)
        service = dict(adapter.get("service") or {})
        mode = str(service.get("mode") or "")
        live: dict = {"mode": mode}
        if mode == "external":
            base = _base_url(str(service.get("base_url") or ""))
            probe_call = http_request("GET", f"{base}/", timeout=5.0)
            live.update(
                {
                    "base_url": base,
                    "reachable": bool(probe_call.get("answered")),
                    "detail": (
                        f"HTTP {probe_call['status']}" if probe_call.get("answered")
                        else str(probe_call.get("error") or "no answer")
                    ),
                }
            )
        else:
            containers = capability_containers(self.manager(), name)
            cid_file = _service_cid_file(self.runtime, name, service)
            running = ""
            for cid in containers:
                if self._container_publishes(
                    self.manager(), cid,
                    {int(service["container_port"]): int(service["host_port"])},
                ):
                    running = cid
            live.update(
                {
                    "base_url": f"http://127.0.0.1:{int(service.get('host_port') or 0)}",
                    "running_cid": running,
                    "started": bool(running),
                    "containers_of_ours": containers,
                    "cid_file": str(cid_file),
                    "container_port": int(service.get("container_port") or 0),
                    "host_port": int(service.get("host_port") or 0),
                }
            )
        report = {
            "ok": True,
            "name": name,
            "health": state,
            "auto_routable": bool(state.get("auto_routable")),
            "selectable": bool(selectable),
            "selectable_reason": why,
            "registered": bool(loaded["evidence"].get("registered")),
            "version": str(loaded["evidence"].get("version") or ""),
            "operation": dict(adapter.get("operation") or {}),
            "permissions": dict(adapter.get("permissions") or {}),
            "service": live,
            "pin": self.verify_surface_pin(adapter),
        }
        if probe:
            report["probe"] = self.call_http_tool(
                name, dict(loaded["evidence"].get("input") or _sample_input(adapter))
            )
        return report

    def rollback_http_tool(self, name: str, *, reason: str = "") -> dict:
        """Take an HTTP capability back: registration, files, health, containers.

        The teardown is the container half's own path, reused deliberately: it is
        runtime-agnostic (it removes containers by ``godsv.capability=<name>``,
        quarantines the tool directory and retires the health ledger) — an HTTP
        adapter needs exactly that, and nothing here may invent a name-based
        removal.
        """
        result = self.rollback_container_tool(
            name, reason=reason or "rolled back: registration and containers removed"
        )
        if isinstance(result, dict) and result.get("ok"):
            result["runtime"] = HTTP_RUNTIME
        return result


def _sample_input(adapter: dict) -> dict:
    """A test payload derived from the adapter's own required inputs."""
    inputs = dict((adapter.get("operation") or {}).get("inputs") or {})
    payload: dict = {}
    for param, spec in inputs.items():
        if not spec.get("required"):
            continue
        declared = str(spec.get("type") or "string")
        payload[param] = {"integer": 7, "number": 7.5, "boolean": True}.get(declared, "skaldir")
    if not payload:
        for param, spec in inputs.items():
            payload[param] = "skaldir" if str(spec.get("type") or "string") == "string" else 1
            break
    return payload


__all__ = [
    "HTTP_DOCUMENT_PROBES",
    "HTTP_EVIDENCE_FILE",
    "HTTP_KIND",
    "HTTP_ORACLES",
    "HTTP_PERMISSIONS",
    "HTTP_RUNTIME",
    "HTTP_SURFACE_FILE",
    "HttpToolBuilder",
    "expected_http_outputs",
    "free_port",
    "generate_http_adapter",
    "http_request",
    "http_tool_paths",
    "inspect_http_surface",
    "match_http_operation",
    "parse_http_response",
    "parse_surface_document",
    "render_http_request",
    "resolve_http_oracle",
    "surface_summary",
]
