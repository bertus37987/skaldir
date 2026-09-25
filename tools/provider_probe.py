#!/usr/bin/env python
"""Live provider probe — measure every provider a credential really exists for.

What this measures, per provider, against the **real** endpoint:

* ``auth``            ``GET /models`` — does the credential work at all, how many ids
* ``auth_control``    the same call with a deliberately wrong key — proves the 401 is
                      credential-driven and not a shape error
* ``chat``            a real completion: model echo, content, ``content_chars``,
                      ``reasoning_content`` (a reasoning backend may answer with
                      **only** ``reasoning_content`` and leave ``content`` empty —
                      that is a real answer, not a failure), latency
* ``stream``          ``stream:true`` — SSE chunk count and time-to-first-chunk
* ``json_schema``     ``response_format={"type":"json_schema",...}`` — native structured output
* ``json_object``     ``response_format={"type":"json_object"}`` — the fallback ceiling
* ``vision``          a genuine 64x64 truecolour PNG in an ``image_url`` part

Rules this file obeys:

* a credential value is **never** printed and never written to disk — only its variable
  name and its length;
* ``SKALDIR_*`` and stub-shaped values are cleared before anything is read, because this
  host's environment has carried leaked probe variables before;
* a probe that cannot run is recorded as ``not_run`` with its reason, never as a failure;
* the script is sequential and small: one client, small ``max_tokens``, no parallelism.

Usage::

    .venv/bin/python tools/provider_probe.py
"""

from __future__ import annotations

import base64
import json
import os
import socket
import struct
import sys
import time
import zlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

TIMEOUT = 60.0
CHAT_MAX_TOKENS = 24
STRUCT_MAX_TOKENS = 96

REPO = Path(__file__).resolve().parents[1]
DEFAULT_OUT = Path("/home/willy/gv-prov-probe-round2/probe-raw.json")

#: Where a credential may legitimately live on this host. Names only are ever read out.
ENV_FILES = (
    Path.home() / ".hermes" / ".env",
    Path.home() / ".hermes" / "godsv" / ".env",
)

#: Providers we would probe, and the variable that would authorise it.
TARGETS: tuple[dict[str, Any], ...] = (
    {
        "id": "deepseek",
        "base_url": "https://api.deepseek.com/v1",
        "key_env": ("DEEPSEEK_API_KEY",),
        "models": ("deepseek-chat", "deepseek-reasoner"),
    },
    {
        "id": "openrouter",
        "base_url": "https://openrouter.ai/api/v1",
        "key_env": ("OPENROUTER_API_KEY",),
        "models": (),  # chosen after GET /models — a free route keeps the probe costless
    },
    {
        "id": "opencode-go",
        "base_url": "https://opencode.ai/zen/go/v1",
        "key_env": ("OPENCODE_GO_API_KEY", "OPENCODE_API_KEY"),
        "models": (),  # chosen after GET /models
    },
    {
        "id": "openai",
        "base_url": "https://api.openai.com/v1",
        "key_env": ("OPENAI_API_KEY",),
        "models": ("gpt-4o-mini",),
    },
    {
        "id": "anthropic",
        "base_url": "https://api.anthropic.com/v1",
        "key_env": ("ANTHROPIC_API_KEY",),
        "models": (),
    },
    {
        "id": "google-gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "key_env": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        "models": (),
    },
    {
        "id": "xai",
        "base_url": "https://api.x.ai/v1",
        "key_env": ("XAI_API_KEY",),
        "models": (),
    },
    {
        "id": "moonshot",
        "base_url": "https://api.moonshot.ai/v1",
        "key_env": ("MOONSHOT_API_KEY",),
        "models": (),
    },
    {
        "id": "alibaba-qwen",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "key_env": ("DASHSCOPE_API_KEY",),
        "models": (),
    },
    {
        "id": "zai",
        "base_url": "https://api.z.ai/api/paas/v4",
        "key_env": ("ZAI_API_KEY", "ZHIPUAI_API_KEY"),
        "models": (),
    },
    {
        "id": "xiaomi-mimo",
        "base_url": "https://api.xiaomimimo.com/v1",
        "key_env": ("MIMO_API_KEY",),
        "models": (),
    },
    {
        "id": "ollama-cloud",
        "base_url": "https://ollama.com/v1",
        "key_env": ("OLLAMA_API_KEY",),
        "models": (),
    },
)

#: Ports the four documented local servers would listen on. No credential involved.
LOCAL_PORTS: tuple[tuple[str, int], ...] = (
    ("llamacpp", 8080),
    ("vllm", 8000),
    ("lmstudio", 1234),
    ("ollama", 11434),
)

#: What a value must not look like to be treated as a real credential.
STUB_MARKERS = (
    "stub",
    "fake",
    "dummy",
    "placeholder",
    "changeme",
    "xxxxxxxx",
    "your-api-key",
    "redacted",
    "test-key",
)

SCHEMA = {
    "type": "object",
    "properties": {"probe": {"type": "integer"}, "answer": {"type": "string"}},
    "required": ["probe", "answer"],
    "additionalProperties": False,
}

cleaned: list[str] = []


# --------------------------------------------------------------------------- #
# environment hygiene — no value ever leaves this process                       #
# --------------------------------------------------------------------------- #


def clean_environment() -> None:
    """Drop leaked probe variables before anything reads a credential.

    The environment has carried ``SKALDIR_*`` and stub-shaped provider variables
    before; a probe that reads one of those measures nothing.
    """
    for name in sorted(os.environ):
        if name.startswith(("SKALDIR_", "GODSV_PROBE_")):
            os.environ.pop(name, None)
            cleaned.append(name)


def load_env_files() -> None:
    """Read the two dotenv files into ``os.environ`` — values are never echoed."""
    for path in ENV_FILES:
        if not path.is_file():
            continue
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.removeprefix("export ").strip()
            value = value.strip().strip("'\"")
            if key:
                os.environ[key] = value


def credential(names: tuple[str, ...]) -> tuple[str, str, str]:
    """``(variable_name, value, refusal)`` — the value is never printed."""
    for name in names:
        value = (os.environ.get(name) or "").strip()
        if not value:
            continue
        lowered = value.lower()
        if any(marker in lowered for marker in STUB_MARKERS) or len(value) < 16:
            return name, "", f"{name} is present but stub-shaped (len={len(value)}) — not a credential"
        return name, value, ""
    return "", "", "no credential available on this host"


# --------------------------------------------------------------------------- #
# probe primitives                                                              #
# --------------------------------------------------------------------------- #


def _snippet(text: Any, limit: int = 400) -> str:
    return " ".join(str(text or "").split())[:limit]


def _headers(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _post(client: httpx.Client, url: str, key: str, body: dict) -> tuple[dict, httpx.Response | None]:
    started = time.monotonic()
    try:
        r = client.post(url, headers=_headers(key), json=body, timeout=TIMEOUT)
    except Exception as exc:  # noqa: BLE001
        return (
            {"status": None, "ok": False, "latency_ms": _ms(started),
             "detail": f"{type(exc).__name__}: {_snippet(exc, 200)}"},
            None,
        )
    out: dict[str, Any] = {
        "status": r.status_code,
        "ok": r.status_code == 200,
        "latency_ms": _ms(started),
        "detail": "" if r.status_code == 200 else _snippet(r.text, 400),
    }
    return out, r


def _answer_fields(payload: dict) -> dict[str, Any]:
    """What a completion really answered — including a reasoning-only answer."""
    choice = (payload.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    content = message.get("content") or ""
    reasoning = message.get("reasoning_content") or message.get("reasoning") or ""
    return {
        "model_echo": payload.get("model", ""),
        "content": _snippet(content, 200),
        "content_chars": len(content),
        "reasoning_content": _snippet(reasoning, 200),
        "reasoning_chars": len(reasoning),
        "answered": bool(content.strip() or str(reasoning).strip()),
        "finish_reason": choice.get("finish_reason", ""),
        "usage": payload.get("usage") or {},
    }


def probe_models(client: httpx.Client, base: str, key: str) -> tuple[dict, list[str]]:
    started = time.monotonic()
    try:
        r = client.get(f"{base}/models", headers=_headers(key), timeout=TIMEOUT)
    except Exception as exc:  # noqa: BLE001
        return (
            {"status": None, "ok": False, "latency_ms": _ms(started),
             "detail": f"{type(exc).__name__}: {_snippet(exc, 200)}"},
            [],
        )
    out: dict[str, Any] = {
        "status": r.status_code,
        "ok": r.status_code == 200,
        "latency_ms": _ms(started),
        "detail": "" if r.status_code == 200 else _snippet(r.text, 400),
    }
    ids: list[str] = []
    if r.status_code == 200:
        try:
            data = r.json().get("data") or []
            ids = [str(m.get("id", "")) for m in data if isinstance(m, dict)]
            out["count"] = len(ids)
            out["sample"] = ids[:12]
        except Exception as exc:  # noqa: BLE001
            out["detail"] = f"model list not JSON: {type(exc).__name__}"
    return out, ids


def probe_chat(client: httpx.Client, base: str, key: str, model: str) -> dict:
    res, r = _post(
        client, f"{base}/chat/completions", key,
        {
            "model": model,
            "messages": [{"role": "user", "content": "Reply with the single word pong."}],
            "max_tokens": CHAT_MAX_TOKENS,
            "temperature": 0,
        },
    )
    if r is not None and res["ok"]:
        try:
            res.update(_answer_fields(r.json()))
        except Exception as exc:  # noqa: BLE001
            res["ok"] = False
            res["detail"] = f"answer not JSON: {type(exc).__name__}"
    return res


def probe_stream(client: httpx.Client, base: str, key: str, model: str) -> dict:
    body = {
        "model": model,
        "messages": [{"role": "user", "content": "Count: one two three"}],
        "max_tokens": CHAT_MAX_TOKENS,
        "stream": True,
    }
    started = time.monotonic()
    chunks = 0
    first_ms: int | None = None
    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    try:
        with client.stream("POST", f"{base}/chat/completions", headers=_headers(key),
                           json=body, timeout=TIMEOUT) as r:
            ctype = r.headers.get("content-type", "")
            if r.status_code != 200:
                r.read()
                return {"status": r.status_code, "ok": False, "latency_ms": _ms(started),
                        "detail": _snippet(r.text, 400)}
            for line in r.iter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    continue
                chunks += 1
                if first_ms is None:
                    first_ms = _ms(started)
                try:
                    delta = ((json.loads(payload).get("choices") or [{}])[0].get("delta") or {})
                except Exception:  # noqa: BLE001
                    continue
                if delta.get("content"):
                    text_parts.append(str(delta["content"]))
                if delta.get("reasoning_content"):
                    reasoning_parts.append(str(delta["reasoning_content"]))
    except Exception as exc:  # noqa: BLE001
        return {"status": None, "ok": False, "latency_ms": _ms(started),
                "detail": f"{type(exc).__name__}: {_snippet(exc, 200)}"}
    text = "".join(text_parts)
    reasoning = "".join(reasoning_parts)
    return {
        "status": 200,
        "ok": chunks > 0 and bool(text.strip() or reasoning.strip()),
        "latency_ms": _ms(started),
        "sse_chunks": chunks,
        "time_to_first_chunk_ms": first_ms,
        "content_type": ctype,
        "content": _snippet(text, 200),
        "content_chars": len(text),
        "reasoning_chars": len(reasoning),
        "detail": "" if chunks else "no data: events on the stream",
    }


def probe_structured(client: httpx.Client, base: str, key: str, model: str, mode: str) -> dict:
    if mode == "native":
        rf: dict[str, Any] = {
            "type": "json_schema",
            "json_schema": {"name": "probe", "strict": True, "schema": SCHEMA},
        }
    else:
        rf = {"type": "json_object"}
    res, r = _post(
        client, f"{base}/chat/completions", key,
        {
            "model": model,
            "messages": [
                {"role": "system", "content": 'Return JSON: {"probe":1,"answer":"pong"}'},
                {"role": "user", "content": "Answer."},
            ],
            "max_tokens": STRUCT_MAX_TOKENS,
            "temperature": 0,
            "response_format": rf,
        },
    )
    if r is not None and res["ok"]:
        try:
            fields = _answer_fields(r.json())
            res["raw"] = fields.get("content", "")
            res["content_chars"] = fields.get("content_chars", 0)
            res["reasoning_chars"] = fields.get("reasoning_chars", 0)
            res["parsed_json"] = None
            try:
                res["parsed_json"] = json.loads(fields.get("content") or "")
            except Exception:  # noqa: BLE001
                pass
            res["valid_json"] = res["parsed_json"] is not None
            if not res.get("valid_json"):
                res["ok"] = False
                res["detail"] = "HTTP 200 but the answer is not parseable JSON"
        except Exception as exc:  # noqa: BLE001
            res["ok"] = False
            res["detail"] = f"answer not JSON: {type(exc).__name__}"
    return res


def real_png(size: int = 64) -> str:
    """A genuine 64x64 truecolour PNG, stdlib only.

    A hand-typed 1x1 PNG was CRC-broken and read as an unsupported image — an
    unfair probe. The smallest honest payload is a real picture.
    """
    rows = []
    for y in range(size):
        row = b""
        for x in range(size):
            row += bytes(((x * 4) % 256, (y * 4) % 256, 128))
        rows.append(b"\x00" + row)
    raw = b"".join(rows)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(
            ">I", zlib.crc32(tag + data) & 0xFFFFFFFF
        )

    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )
    return base64.b64encode(png).decode()


def probe_vision(client: httpx.Client, base: str, key: str, model: str, png_b64: str) -> dict:
    res, r = _post(
        client, f"{base}/chat/completions", key,
        {
            "model": model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": "What is in this image? One word."},
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/png;base64,{png_b64}"}},
                ],
            }],
            "max_tokens": CHAT_MAX_TOKENS,
        },
    )
    res["payload"] = f"64x64 truecolour PNG (stdlib zlib/struct, ~{len(png_b64)} b64 chars)"
    if r is not None and res["ok"]:
        try:
            fields = _answer_fields(r.json())
            res["content"] = fields["content"]
            res["answered"] = fields["answered"]
            res["ok"] = fields["answered"]
        except Exception as exc:  # noqa: BLE001
            res["ok"] = False
            res["detail"] = f"answer not JSON: {type(exc).__name__}"
    return res


def local_ports() -> dict[str, str]:
    out: dict[str, str] = {}
    for name, port in LOCAL_PORTS:
        s = socket.socket()
        s.settimeout(0.4)
        try:
            s.connect(("127.0.0.1", port))
            out[f"{name}:{port}"] = "listening"
        except OSError:
            out[f"{name}:{port}"] = "closed"
        finally:
            s.close()
    return out


def pick_models(ids: list[str], limit: int = 2) -> list[str]:
    """Prefer free routes (a costless probe), then plain instruct models."""
    if not ids:
        return []
    free = [i for i in ids if i.endswith(":free")]
    preferred = [i for i in free if "small" in i or "mini" in i or "flash" in i]
    ordered = preferred + [i for i in free if i not in preferred]
    if not ordered:
        ordered = [i for i in ids if "vision" not in i.lower()]
    return ordered[:limit]


# --------------------------------------------------------------------------- #
# the run                                                                       #
# --------------------------------------------------------------------------- #


def main() -> int:
    out_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUT
    started_at = datetime.now(UTC).astimezone().isoformat(timespec="seconds")

    clean_environment()
    load_env_files()

    report: dict[str, Any] = {
        "probe_date": datetime.now().astimezone().date().isoformat(),
        "started_at": started_at,
        "runner": ".venv/bin/python tools/provider_probe.py",
        "cleaned_env_vars": cleaned,
        "local_ports": local_ports(),
        "credential_library": {},
        "providers": [],
        "unprobed": [],
    }

    png = real_png(64)

    for target in TARGETS:
        var, key, refusal = credential(target["key_env"])
        record: dict[str, Any] = {
            "id": target["id"],
            "endpoint": target["base_url"] + "/chat/completions",
            "base_url": target["base_url"],
            "credential_var": var or " / ".join(target["key_env"]),
            "credential_len": len(key),
            "probe": "not_run",
            "reason": refusal,
            "timestamp": datetime.now(UTC).astimezone().isoformat(timespec="seconds"),
            "models_asked": [],
            "models_answered": [],
            "steps": {},
        }
        if var:
            report["credential_library"][var] = {"present": True, "len": len(key)}
        if not key:
            report["unprobed"].append(
                {"id": target["id"], "endpoint": target["base_url"], "reason": refusal}
            )
            report["providers"].append(record)
            print(f"[not_run] {target['id']:<14} {refusal}", flush=True)
            continue

        print(f"[probe  ] {target['id']:<14} {var} (len={len(key)}) -> {target['base_url']}",
              flush=True)

        with httpx.Client() as client:
            models_res, ids = probe_models(client, target["base_url"], key)
            record["steps"]["auth"] = models_res

            started = time.monotonic()
            try:
                control = client.post(
                    f"{target['base_url']}/chat/completions",
                    headers=_headers("sk-probe-control-not-a-real-key-000000"),
                    json={"model": (target["models"] or ("probe",))[0],
                          "messages": [{"role": "user", "content": "pong"}],
                          "max_tokens": 4},
                    timeout=TIMEOUT,
                )
                record["steps"]["auth_control"] = {
                    "status": control.status_code,
                    "ok": control.status_code in (400, 401, 403, 404),
                    "latency_ms": _ms(started),
                    "detail": _snippet(control.text, 300),
                }
            except Exception as exc:  # noqa: BLE001
                record["steps"]["auth_control"] = {
                    "status": None, "ok": None, "latency_ms": _ms(started),
                    "detail": f"{type(exc).__name__}: {_snippet(exc, 200)}",
                }

            candidates = list(target["models"]) or pick_models(ids)
            if not candidates:
                record["probe"] = "partial"
                record["reason"] = (
                    "credential and endpoint answered, but no model id could be chosen for a "
                    "completion probe (GET /models gave no usable ids)"
                )
            for model in candidates:
                record["models_asked"].append(model)
                block: dict[str, Any] = {
                    "model": model,
                    "timestamp": datetime.now(UTC).astimezone().isoformat(
                        timespec="seconds"
                    ),
                }
                block["chat"] = probe_chat(client, target["base_url"], key, model)
                block["stream"] = probe_stream(client, target["base_url"], key, model)
                block["json_schema"] = probe_structured(
                    client, target["base_url"], key, model, "native"
                )
                block["json_object"] = probe_structured(
                    client, target["base_url"], key, model, "json_object"
                )
                block["vision"] = probe_vision(client, target["base_url"], key, model, png)
                if block["chat"].get("model_echo"):
                    record["models_answered"].append(block["chat"]["model_echo"])
                record["steps"][f"model:{model}"] = block
                if block["chat"].get("ok") or block["json_object"].get("ok"):
                    record["probe"] = "run"
                elif record["probe"] == "not_run":
                    record["probe"] = "partial"

        if record["probe"] == "run":
            record["reason"] = ""
        elif record["probe"] == "partial" and not record["reason"]:
            statuses: set[Any] = set()
            for name, step in record["steps"].items():
                if not name.startswith("model:") or not isinstance(step, dict):
                    continue
                for value in step.values():
                    if isinstance(value, dict) and value.get("status") is not None:
                        statuses.add(value["status"])
            record["reason"] = (
                "the credential and the endpoint answered, but no completion probe returned a "
                f"usable answer (HTTP statuses seen: {sorted(statuses)})"
            )
        report["providers"].append(record)
        print(
            f"         -> probe={record['probe']} models_answered={record['models_answered']} "
            f"{record['reason'][:120]}",
            flush=True,
        )

    report["finished_at"] = datetime.now(UTC).astimezone().isoformat(timespec="seconds")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\nraw payloads written: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
