"""``web_fetch`` / ``web_search`` — the network hands, key-free by construction.

``web_fetch`` fetches one ``http(s)`` URL (stdlib ``urllib``; the transport is
injectable, which is how the test suite runs it without a network). ``web_search``
queries **key-free** backends only and reports which one answered:

======================  ===================================================  ============
backend                 endpoint                                             key
======================  ===================================================  ============
``marginalia``          ``https://api.marginalia.nu/public/search/<query>``  none
``duckduckgo``          ``https://api.duckduckgo.com/?q=…&format=json``       none
======================  ===================================================  ============

No paid and no key-based source is used, and no key is ever read. When no
backend answers, the call **refuses** with ``blocked`` (CLI exit 3) and names
exactly what was missing per backend (HTTP status, timeout, transport error) —
it never reports an empty result as a search that ran. Registration probes the
same way (see :func:`probe_backends`), so ``tools-core register`` states the
measured availability instead of assuming a network.

Link-local/metadata addresses (``169.254.169.254`` and friends) are refused
outright; loopback is allowed because a local dev server is a legitimate fetch
target, and that is what the permission declaration says.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from . import sandbox
from .contract import (
    FilesystemPermission,
    NetworkPermission,
    Permission,
    ToolContext,
    ToolContract,
    ToolResult,
    TruncationRule,
    enforce_input,
    failure,
)

MODULE = "skaldir.tools_core.web"

_FETCH_NAME = "web_fetch"
_SEARCH_NAME = "web_search"

_USER_AGENT = "skaldir-core-tools/1.0 (key-free fetch; +https://github.com/bertus37987/gods-voice)"

#: Addresses that are never fetched, whatever the permission says.
DENY_HOSTS: tuple[str, ...] = (
    "169.254.169.254",
    "metadata.google.internal",
    "metadata.goog",
    "100.100.100.200",
)

#: The key-free search backends, in preference order.
BACKENDS: dict[str, dict] = {
    "marginalia": {
        "url": "https://api.marginalia.nu/public/search/{query}",
        "key_required": False,
        "note": "Marginalia public API (independent index of the non-commercial web)",
    },
    "duckduckgo": {
        "url": "https://api.duckduckgo.com/?q={query}&format=json&no_html=1",
        "key_required": False,
        "note": "DuckDuckGo Instant Answer API (abstract + related topics)",
    },
}


# ------------------------------------------------------------------ transport


class _RedirectRecorder(urllib.request.HTTPRedirectHandler):
    def __init__(self) -> None:
        self.hops: list[str] = []

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, D102
        # SSRF guard: re-check the redirect target against host_denied (measured:
        # the old code only checked the original URL, so 302 → 169.254.169.254
        # fetched cloud metadata).
        reason = host_denied(newurl)
        if reason:
            raise urllib.error.URLError(f"redirect to {newurl} refused: {reason}")
        self.hops.append(f"{code} {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def http_get(
    url: str,
    *,
    timeout_s: int,
    max_bytes: int,
    transport=None,
    extra_headers: dict[str, str] | None = None,
) -> dict:
    """One GET through the injected transport, else stdlib ``urllib``.

    Returns ``{"status", "final_url", "content_type", "body", "truncated",
    "redirects", "engine", "error"}``; a transport-level failure (DNS, refused
    connection, timeout) is reported in ``error`` rather than raised, so the
    caller can name it.
    """
    if transport is not None:
        try:
            outcome = dict(
                transport(
                    url=url, timeout_s=timeout_s, max_bytes=max_bytes, headers=extra_headers or {}
                )
            )
        except Exception as exc:  # noqa: BLE001 — a failing transport is a reported result
            return {
                "status": 0,
                "final_url": url,
                "content_type": "",
                "body": b"",
                "truncated": False,
                "redirects": [],
                "engine": "injected transport",
                "error": f"{type(exc).__name__}: {exc}",
            }
        outcome.setdefault("redirects", [])
        outcome.setdefault("truncated", len(outcome.get("body") or b"") > max_bytes)
        outcome.setdefault("error", "")
        outcome["engine"] = outcome.get("engine", "injected transport")
        return outcome
    headers = {"User-Agent": _USER_AGENT, "Accept": "*/*"}
    headers.update(extra_headers or {})
    request = urllib.request.Request(url, headers=headers)
    recorder = _RedirectRecorder()
    opener = urllib.request.build_opener(recorder)
    try:
        with opener.open(request, timeout=timeout_s) as response:  # noqa: S310 — scheme checked
            body = response.read(max_bytes + 1)
            return {
                "status": int(response.status),
                "final_url": str(response.geturl()),
                "content_type": str(response.headers.get("Content-Type", "")),
                "body": body,
                "truncated": len(body) > max_bytes,
                "redirects": list(recorder.hops),
                "engine": "urllib",
                "error": "",
            }
    except urllib.error.HTTPError as exc:
        body = b""
        try:
            body = exc.read(max_bytes + 1)
        except Exception:  # noqa: BLE001 — a body we cannot read is not the point
            body = b""
        return {
            "status": int(exc.code),
            "final_url": str(exc.geturl() or url),
            "content_type": str(exc.headers.get("Content-Type", "") if exc.headers else ""),
            "body": body,
            "truncated": len(body) > max_bytes,
            "redirects": list(recorder.hops),
            "engine": "urllib",
            "error": "",
        }
    except Exception as exc:  # noqa: BLE001 — DNS/TLS/timeout: reported, never hidden
        return {
            "status": 0,
            "final_url": url,
            "content_type": "",
            "body": b"",
            "truncated": False,
            "redirects": list(recorder.hops),
            "engine": "urllib",
            "error": f"{type(exc).__name__}: {exc}",
        }


def host_denied(url: str) -> str:
    """The refusal reason for a URL that must never be fetched, else ``""``."""
    parsed = urllib.parse.urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        return f"scheme {scheme or '(none)'} is not allowed (http/https only)"
    host = (parsed.hostname or "").lower()
    if not host:
        return "no host in the URL"
    if host in DENY_HOSTS:
        return f"{host} is a link-local/metadata address and is never fetched"
    return ""


def _strip_html(document: str) -> str:
    without_scripts = re.sub(r"(?is)<(script|style|noscript)\b.*?</\1>", " ", document)
    without_blocks = re.sub(r"(?i)<(br|/p|/div|/li|/tr|/h[1-6])\s*/?>", "\n", without_scripts)
    text = re.sub(r"(?s)<[^>]+>", "", without_blocks)
    text = html.unescape(text)
    text = re.sub(r"[ \t\f\v]+", " ", text)
    lines = [line.strip() for line in text.splitlines()]
    kept: list[str] = []
    for line in lines:
        if line or (kept and kept[-1]):
            kept.append(line)
    return "\n".join(kept).strip()


# ------------------------------------------------------------------ web_fetch

FETCH_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "url": {
            "type": "string",
            "minLength": 8,
            "maxLength": 2048,
            "description": "absolute http(s) URL to fetch",
        },
        "extract": {
            "type": "string",
            "enum": ["text", "raw"],
            "description": "strip markup (default) or return the raw body",
        },
        "timeout_s": {
            "type": "integer",
            "minimum": 1,
            "maximum": 60,
            "description": "request timeout in seconds (default 15)",
        },
        "max_bytes": {
            "type": "integer",
            "minimum": 1024,
            "maximum": 2000000,
            "description": "byte cap for the returned body (default 100000)",
        },
        "headers": {
            "type": "object",
            "additionalProperties": {"type": "string"},
            "description": (
                "extra request headers, name -> value; both strings (a value is "
                "stringified; a credential-looking name is refused) — an open string "
                "map, not a free-form object"
            ),
        },
    },
    "required": ["url"],
    "additionalProperties": False,
}

FETCH_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "url": {"type": "string", "description": "the URL as given"},
        "final_url": {"type": "string", "description": "URL after redirects"},
        "status": {"type": "integer", "description": "HTTP status (0 when the transport failed)"},
        "content_type": {"type": "string", "description": "response content type"},
        "shape": {"type": "string", "description": "text | html | json | binary"},
        "text": {
            "type": "string",
            "description": "the body (extracted or raw), truncated per the rule",
        },
        "content_bytes": {"type": "integer", "description": "bytes read from the response"},
        "truncated": {"type": "boolean", "description": "the body exceeded the cap"},
        "redirects": {"type": "array", "description": "redirect hops that were followed"},
        "sha256": {"type": "string", "description": "sha256 of the body read"},
        "engine": {"type": "string", "description": "the transport that ran"},
        "elapsed_ms": {"type": "integer", "description": "wall-clock duration"},
        "spill": {"type": "array", "description": "paths of spilled full bodies"},
    },
    "required": ["url", "final_url", "status", "shape", "text", "content_bytes"],
    "additionalProperties": False,
}

FETCH_CONTRACT = ToolContract(
    name=_FETCH_NAME,
    title="Fetch a URL (key-free, capped)",
    description=(
        "GET one http(s) URL, optionally stripping markup, inside a byte cap and a timeout; "
        "the status, the redirect chain and the size are reported, not assumed."
    ),
    module=MODULE,
    input_schema=FETCH_INPUT_SCHEMA,
    output_schema=FETCH_OUTPUT_SCHEMA,
    permission=Permission(
        filesystem=FilesystemPermission(scope="none", mode="none", roots=()),
        network=NetworkPermission(
            mode="fetch",
            schemes=("http", "https"),
            hosts=("*",),
            deny_hosts=DENY_HOSTS,
            requires_key=False,
            key_env="",
            endpoints=(),
            max_bytes=2000000,
            timeout_s=15,
            private_hosts="loopback allowed (local dev servers); link-local/metadata refused",
        ),
    ),
    truncation=TruncationRule(max_bytes=100000, strategy="head+tail", spill=True),
    timeout_s=15,
    tags=("network", "fetch"),
    mutates=False,
    smoke={
        "network": True,
        "input": {"url": "https://example.com/", "max_bytes": 20000},
        "expect": {"status_in": [200, 301, 302]},
    },
)


def _extra_headers(value: object) -> tuple[dict[str, str] | None, str]:
    if value is None:
        return {}, ""
    if not isinstance(value, dict):
        return None, "headers must be an object of strings"
    import re as _re

    deny = _re.compile(sandbox.ENV_DENY_PATTERN)
    headers: dict[str, str] = {}
    for name, item in value.items():
        key = str(name)
        if deny.search(key):
            return None, f"header {key!r} looks like a credential — refused"
        headers[key] = str(item)
    return headers, ""


def _fetch_shape(content_type: str, body: bytes) -> str:
    kind = content_type.split(";")[0].strip().lower()
    if "json" in kind:
        return "json"
    if "html" in kind:
        return "html"
    if kind.startswith("text/") or kind == "":
        return "text" if not sandbox.is_probably_binary(body) else "binary"
    return "binary"


def run_fetch(args: dict, ctx: ToolContext) -> ToolResult:
    refusal = enforce_input(FETCH_CONTRACT, args)
    if refusal is not None:
        return refusal
    url = str(args["url"])
    reason = host_denied(url)
    if reason:
        return failure(_FETCH_NAME, "blocked", f"refused: {reason}", data={"url": url})
    if ctx.offline:
        return failure(
            _FETCH_NAME,
            "blocked",
            "this context is offline: network access was refused at the context level",
            data={"url": url, "missing": "network access (offline context)"},
        )
    headers, problem = _extra_headers(args.get("headers"))
    if headers is None:
        return failure(_FETCH_NAME, "usage", problem, data={"url": url})
    timeout_s = int(args.get("timeout_s", FETCH_CONTRACT.timeout_s) or FETCH_CONTRACT.timeout_s)
    max_bytes = int(args.get("max_bytes", 100000) or 100000)
    started = time.time()
    outcome = http_get(
        url,
        timeout_s=timeout_s,
        max_bytes=max_bytes,
        transport=ctx.transport,
        extra_headers=headers,
    )
    if outcome.get("error"):
        return failure(
            _FETCH_NAME,
            "network",
            f"the request to {url} failed: {outcome['error']}",
            data={"url": url, "missing": "a reachable endpoint", "error": outcome["error"]},
        )
    body = bytes(outcome.get("body") or b"")
    shape = _fetch_shape(str(outcome.get("content_type", "")), body)
    text = body.decode("utf-8", "replace")
    if str(args.get("extract", "text")) == "text" and shape == "html":
        text = _strip_html(text)
    rule = FETCH_CONTRACT.truncation
    report = sandbox.enforce_truncation(text, rule, label="body", runtime=ctx.runtime)
    data = {
        "url": url,
        "final_url": str(outcome.get("final_url") or url),
        "status": int(outcome.get("status") or 0),
        "content_type": str(outcome.get("content_type") or ""),
        "shape": shape,
        "text": report["text"],
        "content_bytes": len(body),
        "truncated": bool(report["truncated"] or outcome.get("truncated")),
        "redirects": list(outcome.get("redirects") or []),
        "sha256": hashlib.sha256(body).hexdigest(),
        "engine": str(outcome.get("engine") or "urllib"),
        "elapsed_ms": int((time.time() - started) * 1000),
        "spill": [report["spill_path"]] if report["spill_path"] else [],
    }
    result = ToolResult(
        tool=_FETCH_NAME,
        ok=True,
        data=data,
        evidence=[{"ref": data["final_url"], "kind": "run", "note": f"HTTP {data['status']}"}],
    )
    if data["status"] >= 400:
        result.warnings.append(f"the server answered HTTP {data['status']}")
    return result


# ----------------------------------------------------------------- web_search

SEARCH_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "minLength": 1,
            "maxLength": 512,
            "description": "the search query",
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 20,
            "description": "maximum results (default 5)",
        },
        "backend": {
            "type": "string",
            "enum": ["auto", "marginalia", "duckduckgo"],
            "description": "which key-free backend to use (default: try them in order)",
        },
    },
    "required": ["query"],
    "additionalProperties": False,
}

SEARCH_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "the query"},
        "backend": {"type": "string", "description": "the backend that answered"},
        "results": {"type": "array", "description": "title/url/snippet records"},
        "count": {"type": "integer", "description": "number of results returned"},
        "key_required": {
            "type": "boolean",
            "description": "always false: no key-based source is used",
        },
        "endpoints": {"type": "array", "description": "the key-free endpoints that were tried"},
        "attempts": {"type": "array", "description": "per-backend outcome of this call"},
        "truncated": {"type": "boolean", "description": "the limit cut the results"},
    },
    "required": ["query", "backend", "results", "count", "key_required", "attempts"],
    "additionalProperties": False,
}

SEARCH_CONTRACT = ToolContract(
    name=_SEARCH_NAME,
    title="Search the web (key-free backends only)",
    description=(
        "Query the key-free Marginalia public API and/or the DuckDuckGo Instant Answer API; "
        "when none answers, refuse and name what is missing instead of returning nothing."
    ),
    module=MODULE,
    input_schema=SEARCH_INPUT_SCHEMA,
    output_schema=SEARCH_OUTPUT_SCHEMA,
    permission=Permission(
        filesystem=FilesystemPermission(scope="none", mode="none", roots=()),
        network=NetworkPermission(
            mode="search",
            schemes=("https",),
            hosts=("api.marginalia.nu", "api.duckduckgo.com"),
            deny_hosts=DENY_HOSTS,
            requires_key=False,
            key_env="",
            endpoints=tuple(item["url"] for item in BACKENDS.values()),
            max_bytes=200000,
            timeout_s=10,
            private_hosts="not applicable (fixed public endpoints only)",
        ),
    ),
    truncation=TruncationRule(max_bytes=40000, strategy="head", spill=False),
    timeout_s=10,
    tags=("network", "search"),
    mutates=False,
    smoke={
        "network": True,
        "input": {"query": "rust programming language", "limit": 3},
        "expect": {"min_count": 1},
    },
)


def _parse_marginalia(payload: bytes, limit: int) -> list[dict]:
    data = json.loads(payload.decode("utf-8", "replace"))
    out: list[dict] = []
    for item in (data.get("results") or [])[:limit]:
        if not isinstance(item, dict):
            continue
        out.append(
            {
                "title": str(item.get("title") or "")[:300],
                "url": str(item.get("url") or ""),
                "snippet": _strip_html(str(item.get("description") or ""))[:400],
                "source": "marginalia",
            }
        )
    return out


def _parse_duckduckgo(payload: bytes, limit: int) -> list[dict]:
    data = json.loads(payload.decode("utf-8", "replace"))
    out: list[dict] = []
    abstract = str(data.get("AbstractText") or "")
    if abstract:
        out.append(
            {
                "title": str(data.get("Heading") or data.get("AbstractSource") or "")[:300],
                "url": str(data.get("AbstractURL") or ""),
                "snippet": _strip_html(abstract)[:400],
                "source": "duckduckgo:abstract",
            }
        )
    for item in data.get("Results") or []:
        if isinstance(item, dict) and item.get("FirstURL"):
            out.append(
                {
                    "title": str(item.get("Text") or "")[:300],
                    "url": str(item.get("FirstURL") or ""),
                    "snippet": _strip_html(str(item.get("Text") or ""))[:400],
                    "source": "duckduckgo:result",
                }
            )
    for topic in data.get("RelatedTopics") or []:
        entries = topic.get("Topics") if isinstance(topic, dict) else None
        for item in entries or ([topic] if isinstance(topic, dict) else []):
            if not isinstance(item, dict) or not item.get("FirstURL"):
                continue
            out.append(
                {
                    "title": str(item.get("Text") or "")[:300],
                    "url": str(item.get("FirstURL") or ""),
                    "snippet": _strip_html(str(item.get("Text") or ""))[:400],
                    "source": "duckduckgo:related",
                }
            )
    return out[:limit]


_PARSERS = {"marginalia": _parse_marginalia, "duckduckgo": _parse_duckduckgo}


def _attempt(backend: str, query: str, *, timeout_s: int, transport) -> dict:
    template = BACKENDS[backend]["url"]
    url = template.format(query=urllib.parse.quote_plus(query))
    outcome = http_get(url, timeout_s=timeout_s, max_bytes=200000, transport=transport)
    record = {
        "backend": backend,
        "url": url,
        "key_required": False,
        "status": int(outcome.get("status") or 0),
        "error": str(outcome.get("error") or ""),
        "results": 0,
        "answered": False,
    }
    if record["error"] or not (200 <= record["status"] < 300):
        return record
    try:
        results = _PARSERS[backend](bytes(outcome.get("body") or b""), 20)
    except (json.JSONDecodeError, ValueError) as exc:
        record["error"] = f"unparseable answer: {type(exc).__name__}: {exc}"
        return record
    record["results"] = len(results)
    record["answered"] = bool(results)
    record["_parsed"] = results
    if not results:
        record["error"] = "endpoint answered but carried no results for this query"
    return record


def probe_backends(*, transport=None, timeout_s: int = 8, query: str = "rust") -> dict:
    """Probe the key-free backends — the honest availability fact for registration."""
    attempts = [
        _attempt(name, query, timeout_s=timeout_s, transport=transport) for name in BACKENDS
    ]
    answering = [item["backend"] for item in attempts if item["answered"]]
    return {
        "query": query,
        "key_required": False,
        "backends": [
            {key: value for key, value in item.items() if key != "_parsed"} for item in attempts
        ],
        "answering": answering,
        "available": bool(answering),
        "missing": [
            f"{item['backend']} ({BACKENDS[item['backend']]['url']}) answered nothing: "
            f"{item['error'] or ('HTTP ' + str(item['status']))}"
            for item in attempts
            if not item["answered"]
        ],
    }


def run_search(args: dict, ctx: ToolContext) -> ToolResult:
    refusal = enforce_input(SEARCH_CONTRACT, args)
    if refusal is not None:
        return refusal
    query = str(args["query"])
    limit = int(args.get("limit", 5) or 5)
    wanted = str(args.get("backend", "auto") or "auto")
    order = list(BACKENDS) if wanted == "auto" else [wanted]
    if ctx.offline:
        return failure(
            _SEARCH_NAME,
            "blocked",
            "this context is offline: the key-free search backends cannot be reached",
            data={"query": query, "missing": ["network access (offline context)"]},
        )
    attempts: list[dict] = []
    chosen = ""
    results: list[dict] = []
    for name in order:
        record = _attempt(name, query, timeout_s=SEARCH_CONTRACT.timeout_s, transport=ctx.transport)
        results = list(record.pop("_parsed", []) or [])
        attempts.append(record)
        if record["answered"]:
            chosen = name
            break
    if not chosen:
        missing = [
            f"{item['backend']} ({item['url']}): {item['error'] or ('HTTP ' + str(item['status']))}"
            for item in attempts
        ]
        return failure(
            _SEARCH_NAME,
            "blocked",
            "no key-free search endpoint answered: " + "; ".join(missing),
            data={
                "query": query,
                "attempts": attempts,
                "missing": missing,
                "key_required": False,
                "endpoints": [item["url"] for item in attempts],
            },
        )
    truncated = len(results) > limit
    results = results[:limit]
    return ToolResult(
        tool=_SEARCH_NAME,
        ok=True,
        data={
            "query": query,
            "backend": chosen,
            "results": results,
            "count": len(results),
            "key_required": False,
            "endpoints": [item["url"] for item in attempts],
            "attempts": attempts,
            "truncated": truncated,
        },
        evidence=[{"ref": attempts[-1]["url"], "kind": "run", "note": f"backend={chosen}"}],
    )


RUNNERS = {_FETCH_NAME: run_fetch, _SEARCH_NAME: run_search}

CONTRACTS = (FETCH_CONTRACT, SEARCH_CONTRACT)

__all__ = [
    "BACKENDS",
    "CONTRACTS",
    "DENY_HOSTS",
    "FETCH_CONTRACT",
    "MODULE",
    "RUNNERS",
    "SEARCH_CONTRACT",
    "host_denied",
    "http_get",
    "probe_backends",
    "run_fetch",
    "run_search",
]
