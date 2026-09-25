"""Read-only, bounded model discovery. No inference and no implicit credentials.

A listing proves availability in the list, not tool support or a successful completion.
Provider-specific protocol restrictions belong to the curated picker metadata.
"""

from __future__ import annotations

import ipaddress
import json
import math
import time
from dataclasses import dataclass
from http.client import HTTPConnection, HTTPSConnection
from typing import Any
from urllib.parse import urlencode, urlsplit

MAX_PAGES = 8
MAX_IDS = 8192
MAX_RESPONSE_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class DiscoveryResult:
    ids: tuple[str, ...]
    source: str
    reason: str = ""


def chat_model_id(item: Any) -> str:
    """Preserve exact IDs; reject unsafe IDs and explicitly non-chat metadata."""
    if not isinstance(item, dict):
        return ""
    value = item.get("id")
    if (not isinstance(value, str) or not value or len(value) > 512
            or any(c.isspace() or not c.isprintable() or c in "*{}" for c in value)):
        return ""
    architecture = item.get("architecture") or {}
    if not isinstance(architecture, dict):
        architecture = {}
    outputs = item.get("output_modalities", architecture.get("output_modalities"))
    if isinstance(outputs, list) and "text" not in outputs:
        return ""
    kind = item.get("type", item.get("model_type", ""))
    if kind in {"embedding", "embeddings", "image", "audio", "video", "rerank", "reranker"}:
        return ""
    metadata = item.get("metadata") or {}
    tags = metadata.get("tags", []) if isinstance(metadata, dict) else []
    nonchat = {"embeddings", "embedding", "reranker", "text-to-image", "text-to-video",
               "image-to-video", "text-to-speech", "speech-to-text"}
    if isinstance(tags, list) and nonchat.intersection(str(tag) for tag in tags):
        return ""
    # The OpenAI list commonly lacks capability metadata for these dedicated APIs.
    name = value.lower().split("/")[-1]
    if any(token in name for token in ("embedding", "rerank", "whisper", "moderation",
                                      "realtime", "transcribe", "-tts", "dall-e", "gpt-image")):
        return ""
    endpoints = item.get("endpoints")
    if isinstance(endpoints, list) and endpoints and not any(
        "chat/completions" in str(endpoint) for endpoint in endpoints
    ):
        return ""
    return value


def _endpoint(base_url: str):
    if not base_url or any(c.isspace() or not c.isprintable() for c in base_url):
        raise ValueError("invalid endpoint")
    parsed = urlsplit(base_url.rstrip("/"))
    if (not parsed.hostname or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment or "{" in base_url or "}" in base_url):
        raise ValueError("invalid endpoint")
    if parsed.scheme == "http":
        if parsed.hostname != "localhost" and not ipaddress.ip_address(parsed.hostname).is_loopback:
            raise ValueError("insecure endpoint")
    elif parsed.scheme != "https":
        raise ValueError("invalid endpoint")
    _ = parsed.port  # Validate the port before any contact.
    return parsed


def _page(parsed, path: str, headers: dict[str, str], deadline: float) -> Any:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError
    connection_type = HTTPSConnection if parsed.scheme == "https" else HTTPConnection
    connection = connection_type(parsed.hostname, parsed.port, timeout=remaining)
    try:
        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
        if response.status != 200:
            # No redirects, response bodies or headers reach output or exception messages.
            raise ValueError("model listing refused")
        body = bytearray()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            if connection.sock is not None:
                connection.sock.settimeout(remaining)
            chunk = response.read1(min(65536, MAX_RESPONSE_BYTES + 1 - len(body)))
            body.extend(chunk)
            if len(body) > MAX_RESPONSE_BYTES:
                raise ValueError("model listing too large")
            if not chunk:
                break
        return json.loads(body)
    finally:
        connection.close()


def discover_models(
    base_url: str, *, api_key: str = "", protocol: str = "openai", timeout: float = 2.0
) -> DiscoveryResult:
    """GET /models, with bounded JSON pagination. Failures never masquerade as live lists."""
    try:
        parsed = _endpoint(base_url)
        if not math.isfinite(timeout) or timeout <= 0 or protocol not in {"openai", "anthropic"}:
            raise ValueError("invalid discovery options")
        deadline = time.monotonic() + min(timeout, 10.0)
        headers = {"Accept": "application/json", "User-Agent": "Skaldir-model-picker/1.0"}
        if protocol == "anthropic":
            headers["anthropic-version"] = "2023-06-01"
            if api_key:
                headers["x-api-key"] = api_key
        elif api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        path = parsed.path if parsed.path.endswith("/models") else parsed.path + "/models"
        request_path = path
        ids: dict[str, None] = {}
        cursors: set[str] = set()
        for _ in range(MAX_PAGES):
            payload = _page(parsed, request_path, headers, deadline)
            rows = payload if isinstance(payload, list) else payload.get("data")
            if not isinstance(rows, list):
                raise ValueError("invalid model listing")
            for item in rows:
                model = chat_model_id(item)
                if model:
                    ids[model] = None
                if len(ids) > MAX_IDS:
                    raise ValueError("model listing too large")
            more = isinstance(payload, dict) and payload.get("has_more") is True
            if not more:
                if not ids:
                    raise ValueError("no chat model IDs returned")
                return DiscoveryResult(tuple(ids), "live")
            cursor = payload.get("last_id")
            if not isinstance(cursor, str) or not cursor or cursor in cursors or len(cursor) > 512:
                raise ValueError("invalid pagination")
            cursors.add(cursor)
            request_path = path + "?" + urlencode({"after_id": cursor})
        return DiscoveryResult((), "unavailable", "model listing exceeded pagination limit")
    except TimeoutError:
        return DiscoveryResult((), "unavailable", "model listing timed out")
    except Exception:  # noqa: BLE001 — remote data and secrets never reach diagnostics
        return DiscoveryResult((), "unavailable", "model listing unavailable; using saved catalog")
