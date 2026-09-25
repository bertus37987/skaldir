"""Process-level performance seams for the Skaldir runtime (CODEX-PERF block).

Three costs are paid *per call* rather than *per process* in the current
runtime, and all three are pure waste inside a run:

1. **A new HTTP client per provider call.** :meth:`OpenAICompatibleLLM.chat`
   opened ``httpx.Client(...)`` for every call and closed it again, so every
   step of an ``agent run`` paid a fresh TCP connect (and a fresh TLS session on
   an ``https`` base_url) instead of reusing one pooled connection. Measured:
   the stub endpoint saw one TCP connection per step.
2. **Re-reading and re-parsing the config file per resolution.** ``_load_block``
   does ``path.read_text()`` + ``yaml.safe_load()`` on every
   :func:`resolve_provider_config` call. Inside one run the file does not change.
3. **Uncoalesced progress output.** There is no rate limit between a producer
   that emits one event per streamed chunk and the terminal, so 100k events cost
   100k ``write`` syscalls (see :mod:`skaldir.perf_progress`).

What Codex does here (read-only study of ``openai/codex``, 2026-09-18) and what
we mirror:

* a client **pool / factory** reused for the whole session
  (``codex-rs/http-client/src/route_aware_client_pool.rs``,
  ``HttpClientFactory``) instead of a client per request;
* config resolved in **layers with a fingerprint** and handed around as an
  ``Arc<Config>`` for the life of the session
  (``codex-rs/config/src/fingerprint.rs``, ``ThreadConfigLoader``) rather than
  re-resolved per call;
* a **frame rate limiter + coalescing scheduler** in front of the renderer
  (``codex-rs/tui/src/tui/frame_rate_limiter.rs``, ``frame_requester.rs``).

Every seam here is *opt-out*: ``SKALDIR_PERF_NOCACHE=1`` restores the exact old
behaviour (fresh client per call, config re-read per call) so a before/after
comparison runs the same code twice — and so a stale cache can never be the only
explanation for a number.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any

from .env import getenv

__all__ = [
    "CACHE_ENABLED",
    "cache_stats",
    "close_shared_http_clients",
    "config_block",
    "http_client",
    "reset_caches",
    "runtime_report",
]

#: ``SKALDIR_PERF_NOCACHE=1`` turns every seam off (the pre-fix behaviour). The
#: before/after table in ``MVP/47-performance-codex.md`` is produced with this
#: flag, so both columns are the same code path with one switch between them.
CACHE_ENABLED = str(getenv("SKALDIR_PERF_NOCACHE", "") or "").strip() not in {
    "1", "true", "yes",
}

#: Bound on cached yaml blocks. A process touches one config file; the cap only
#: exists so a pathological caller cannot grow the cache without limit.
_CONFIG_CACHE_MAX = 8

_lock = threading.RLock()
_clients: dict[tuple[Any, ...], Any] = {}
_configs: OrderedDict[tuple[Any, ...], dict[str, Any]] = OrderedDict()
_stats: dict[str, int] = {
    "http_client_created": 0,
    "http_client_reused": 0,
    "config_read": 0,
    "config_cache_hit": 0,
}


def _bump(key: str) -> None:
    with _lock:
        _stats[key] = _stats.get(key, 0) + 1


def cache_stats() -> dict[str, int]:
    """Counter snapshot — what the harness prints as proof of reuse."""
    with _lock:
        return dict(_stats)


def reset_caches(*, close: bool = True) -> None:
    """Drop every cached object (tests use this between cases)."""
    global _clients, _configs
    with _lock:
        clients, _clients = _clients, {}
        _configs = OrderedDict()
        for key in _stats:
            _stats[key] = 0
    if close:
        for client in clients.values():
            try:
                client.close()
            except Exception:  # noqa: BLE001 - a close failure is never fatal
                pass


# --------------------------------------------------------------------- http


def http_client(*, follow_redirects: bool = False, **options: Any) -> Any:
    """One process-wide pooled ``httpx.Client`` per distinct option set.

    The returned client is **shared** and must not be closed by the caller —
    that is the whole point (a closed client cannot be reused). Call
    :func:`close_shared_http_clients` at the end of a process, or rely on
    :func:`skaldir.llm_provider.OpenAICompatibleLLM.close`.

    Raises the same import error httpx would, so the fallback path stays honest.
    """
    if not CACHE_ENABLED:
        import httpx

        return httpx.Client(follow_redirects=follow_redirects, **options)
    key = (
        bool(follow_redirects),
        tuple(sorted((str(k), str(v)) for k, v in options.items())),
    )
    with _lock:
        existing = _clients.get(key)
        if existing is not None:
            _bump("http_client_reused")
            return existing
    import httpx

    client = httpx.Client(follow_redirects=follow_redirects, **options)
    with _lock:
        # a racing caller may have won; keep the first one so everyone shares it
        winner = _clients.setdefault(key, client)
    if winner is not client:
        client.close()
        _bump("http_client_reused")
        return winner
    _bump("http_client_created")
    return winner


def close_shared_http_clients() -> int:
    """Close every pooled client; returns how many were open."""
    with _lock:
        clients = list(_clients.values())
        _clients.clear()
    for client in clients:
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass
    return len(clients)


# ------------------------------------------------------------------- config


def _stat_key(path: Path) -> tuple[Any, ...]:
    try:
        info = path.stat()
    except OSError:
        return (str(path), None, None, None)
    return (str(path), info.st_mtime_ns, info.st_size, info.st_ino)


def config_block(path: Path, loader: Any) -> Any:
    """Memoise ``loader(path)`` on the file's identity, not on a clock.

    The key is ``(path, mtime_ns, size, inode)``: a rewrite that changes any byte
    changes at least one of mtime/size, so an edit is always seen. That is the
    cheap-safe check the repo already uses for the coding-context cache — a
    *directory* mtime would miss in-place edits, a bare path would serve stale
    facts forever.

    ``loader`` is the caller's own reader (we never import yaml here, so this
    module stays off the cold-start path).
    """
    if not CACHE_ENABLED:
        return loader(path)
    key = _stat_key(path)
    with _lock:
        if key in _configs:
            _configs.move_to_end(key)
            _bump("config_cache_hit")
            return _configs[key]
    value = loader(path)
    with _lock:
        if key in _configs:
            _bump("config_cache_hit")
            return _configs[key]
        _configs[key] = value
        while len(_configs) > _CONFIG_CACHE_MAX:
            _configs.popitem(last=False)
    _bump("config_read")
    return value


def runtime_report() -> dict[str, Any]:
    """The honest state of the seams, for ``--json`` output and the doc."""
    with _lock:
        return {
            "cache_enabled": CACHE_ENABLED,
            "http_clients_open": len(_clients),
            "config_entries": len(_configs),
            "counters": dict(_stats),
            "kill_switch": "SKALDIR_PERF_NOCACHE=1",
        }
