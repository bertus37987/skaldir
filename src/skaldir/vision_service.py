"""Vision — one image in, one answer out, through the configured model.

The provider side (how an image becomes a content part, the size/type guards,
the "this model cannot see" error) lives in :mod:`skaldir.llm_provider`
(§ *vision / image input*). This module is the product half: it resolves a
*file or a remote URL* into exactly one image, sends it once, reads the answer
back, and reports — in the payload, not in a log line — **which file went where**
and **what it cost**.

What a call looks like
----------------------

============================  ====================================================
``vision describe <path|url>``  send the image, return the model's answer.
``vision ocr <path>``           same call with an OCR instruction ("read every
                                piece of text") — no second code path, no OCR
                                model of our own.
``vision capabilities``         *measure* whether the configured model accepts an
                                image part at all: one 1x1 PNG probe plus the
                                model-name catalog, reported side by side so a
                                catalog guess never passes as a measurement.
``vision screenshot``           capture the screen (or one window) through the
                                existing Computer-Use layer and describe it.
============================  ====================================================

Honesty rules this module enforces
----------------------------------

* **A local file's bytes are never sent silently.** Every success payload carries
  ``source`` (the absolute path, mime, byte count, sha256), ``sent_to`` (the
  endpoint the bytes went to) and a one-sentence ``disclosure``; the CLI prints
  that sentence to stderr *before* the request leaves.
* **A symlink may not leave the workspace.** The *resolved* target of a symlink
  must sit inside the workspace root (``$SKALDIR_VISION_WORKSPACE``, else the
  current directory). A link pointing outside is refused (exit 3) with the link
  and the target named. Only regular files are read — a fifo/device is refused
  rather than opened.
* **Images are not free.** Every payload carries ``cost``: the documented
  image-token estimate (85 + pixels/750) *and* the provider's own ``usage``
  counters when it returns them, with a note saying which of the two is the
  measurement. A provider that reports no usage is said to have reported none —
  never silently replaced by the estimate.
* **An unknown capability is reported as unknown.** ``accepts_images`` is
  ``true``/``false``/``null``; only a provider answer or a provider refusal
  makes it true/false. No-network, no-key and timeouts are ``null`` with the
  reason attached.
* **No secret, and no image payload, is ever rendered.** The api key lives in
  the request header only; the report uses ``ProviderConfig.redacted()`` and
  the request summary collapses data URLs to mime + size + sha256.

Nothing here prints: the CLI (:mod:`skaldir.vision_cli`) owns output, and the
functions return the verdict dicts the envelope carries.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from . import llm_provider as LP
from .env import first_env, getenv
from .llm import LLMError

__all__ = [
    "DEFAULT_DESCRIBE_PROMPT",
    "DEFAULT_MAX_IMAGE_MB",
    "DEFAULT_OCR_PROMPT",
    "ENV_MAX_IMAGE_MB",
    "ENV_WORKSPACE",
    "ImageSource",
    "VisionBlocked",
    "VisionNeedsCredentials",
    "VisionNotFound",
    "capture_screen",
    "capabilities",
    "describe",
    "load_source",
    "max_image_bytes",
    "ocr",
    "screenshot",
    "workspace_root",
]

#: One image, 10 MiB — the same default the provider-side guard uses.
DEFAULT_MAX_IMAGE_MB = LP.DEFAULT_MAX_IMAGE_BYTES // (1024 * 1024)

ENV_MAX_IMAGE_MB = "SKALDIR_VISION_MAX_IMAGE_MB"
ENV_WORKSPACE = "SKALDIR_VISION_WORKSPACE"

DEFAULT_DESCRIBE_PROMPT = (
    "Describe this image precisely: what it shows, every visible text string, and "
    "anything a reader would need to know. Answer in English, in at most 6 sentences."
)

DEFAULT_OCR_PROMPT = (
    "Read the text in this image and print it exactly, preserving line breaks. "
    "Output only the text — no commentary, no translation. If there is no text, "
    "answer with the single word: (none)."
)

#: The screenshot backend the Computer-Use layer uses (see ``workflows.screenshot``).
CU_SCREENSHOT_TOOL = "import"
#: The window inventory tool that layer uses (see ``workflows.x11_windows``).
CU_WINDOW_TOOL = "xprop"


# --------------------------------------------------------------------------- #
# the three outcomes a caller must be able to tell apart                        #
# --------------------------------------------------------------------------- #


class VisionBlocked(RuntimeError):
    """A precondition is missing (no provider, refused symlink, no CU bridge) → exit 3."""


class VisionNotFound(RuntimeError):
    """The named file/window is not there → exit 5."""


class VisionNeedsCredentials(RuntimeError):
    """A remote endpoint without a key — no request is attempted → exit 4."""


# --------------------------------------------------------------------------- #
# one resolved image                                                           #
# --------------------------------------------------------------------------- #


@dataclass
class ImageSource:
    """One image the call will send: what it is, how big, where the bytes go.

    ``payload`` is the file's bytes and is deliberately **not** part of
    :meth:`report`/``__repr__`` — it exists to build the request part and is
    dropped afterwards.
    """

    kind: str  # "file" | "url"
    origin: str  # exactly what the caller passed (path or URL)
    path: str = ""
    url: str = ""
    mime: str = ""
    bytes_len: int = 0
    sha256: str = ""
    width: int | None = None
    height: int | None = None
    symlink: str = ""
    payload: bytes | None = field(default=None, repr=False, compare=False)

    @property
    def size(self) -> tuple[int, int] | None:
        if self.width and self.height:
            return (int(self.width), int(self.height))
        return None

    def report(self, *, sent_to: str = "") -> dict[str, Any]:
        """The JSON-safe description of this image (never the payload)."""
        record: dict[str, Any] = {
            "kind": self.kind,
            "origin": self.origin,
            "mime": self.mime,
            "bytes": self.bytes_len,
            "sha256": self.sha256,
        }
        if self.path:
            record["path"] = self.path
        if self.url:
            record["url"] = self.url
        if self.width and self.height:
            record["width"] = int(self.width)
            record["height"] = int(self.height)
        if self.symlink:
            record["symlink"] = self.symlink
        if sent_to:
            record["sent_to"] = sent_to
        return record


def disclosure(source: ImageSource, *, sent_to: str) -> str:
    """The one sentence that says which bytes leave the machine and where to."""
    if source.kind == "file":
        where = source.path or source.origin
        link = f" (via the symlink {source.symlink})" if source.symlink else ""
        return (
            f"sending the bytes of {where}{link} ({source.bytes_len} bytes, "
            f"{source.mime}, base64-encoded) to {sent_to}"
        )
    return f"sending the URL {source.url} to {sent_to} — the provider fetches the bytes"


# --------------------------------------------------------------------------- #
# bounds and the workspace rule                                                #
# --------------------------------------------------------------------------- #


def max_image_bytes(override: int | None = None) -> int:
    """The size cap: explicit → ``$SKALDIR_VISION_MAX_IMAGE_MB`` → 10 MiB."""
    if override is not None:
        try:
            value = int(override)
        except (TypeError, ValueError):
            return LP.DEFAULT_MAX_IMAGE_BYTES
        return value if value > 0 else LP.DEFAULT_MAX_IMAGE_BYTES
    from_env = first_env((ENV_MAX_IMAGE_MB,))
    if from_env:
        try:
            megabytes = int(str(from_env).strip())
        except ValueError:
            megabytes = 0
        if megabytes > 0:
            return megabytes * 1024 * 1024
    return LP.DEFAULT_MAX_IMAGE_BYTES


def workspace_root(explicit: str | Path | None = None) -> Path:
    """The root a symlink may not leave: explicit → ``$SKALDIR_VISION_WORKSPACE`` → cwd."""
    if explicit:
        return Path(explicit).expanduser().resolve()
    from_env = str(getenv("VISION_WORKSPACE", "") or "").strip()
    if from_env:
        return Path(from_env).expanduser().resolve()
    return Path.cwd().resolve()


def _inside(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def load_source(
    target: str,
    *,
    workspace: str | Path | None = None,
    max_bytes: int | None = None,
    allow_remote: bool = True,
) -> ImageSource:
    """Resolve ``<path|http(s)-url>`` into one guarded :class:`ImageSource`.

    Raises :class:`VisionNotFound` for a missing file, :class:`VisionBlocked`
    for a refused symlink / a non-regular file / a remote URL that is not
    allowed, and :class:`LP.ImageInputError` for the size and type guards.
    """
    text = str(target or "").strip()
    if not text:
        raise VisionBlocked("no image given — pass a file path or an http(s) URL")
    lowered = text.lower()
    if lowered.startswith(("http://", "https://")):
        if not allow_remote:
            raise VisionBlocked(
                f"--no-remote refused the URL {text}: remote images are opt-in in this call"
            )
        parsed = urlparse(text)
        if not parsed.netloc:
            raise VisionBlocked(f"{text} is not a usable URL (no host)")
        return ImageSource(kind="url", origin=text, url=text, mime="", bytes_len=0)

    path = Path(text).expanduser()
    if not path.exists():
        raise VisionNotFound(f"{path} does not exist")
    root = workspace_root(workspace)
    link = str(path) if path.is_symlink() else ""
    resolved = path.resolve()
    if link and not _inside(resolved, root):
        raise VisionBlocked(
            f"refusing the symlink {link} → {resolved}: it resolves outside the "
            f"workspace {root} (set --workspace or {ENV_WORKSPACE} if that is intended)"
        )
    if resolved.is_dir():
        raise VisionBlocked(f"{resolved} is a directory, not an image")
    if not resolved.is_file():
        raise VisionBlocked(
            f"{resolved} is not a regular file (fifo/device/socket) — refusing to read it"
        )
    limit = max_image_bytes(max_bytes)
    try:
        with resolved.open("rb") as handle:
            data = handle.read(limit + 1)
    except OSError as exc:
        raise VisionBlocked(f"{resolved} could not be read: {exc}") from exc
    if len(data) > limit:
        raise LP.ImageInputError(
            f"{resolved} is larger than the {limit} byte image cap "
            f"({limit / (1024 * 1024):.1f} MiB) — raise --max-image-mb if this is intended"
        )
    declared = LP.IMAGE_MIME_BY_EXT.get(resolved.suffix.lower(), "")
    mime = LP.guard_image(
        data, mime=declared, origin=str(resolved), max_bytes=limit
    )
    size = LP.image_size(data)
    return ImageSource(
        kind="file",
        origin=text,
        path=str(resolved),
        mime=mime,
        bytes_len=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        width=size[0] if size else None,
        height=size[1] if size else None,
        symlink=link,
        payload=data,
    )


# --------------------------------------------------------------------------- #
# the configured model                                                         #
# --------------------------------------------------------------------------- #


def _host_of(base_url: str) -> str:
    host = str(base_url or "").split("://", 1)[-1].split("/", 1)[0].rsplit("@", 1)[-1]
    return host.rsplit(":", 1)[0].strip("[]").lower()


def _is_local(base_url: str) -> bool:
    return _host_of(base_url) in (
        "localhost",
        "127.0.0.1",
        "::1",
        "0.0.0.0",
        "host.docker.internal",
    )


def resolve_client(
    *,
    client: Any = None,
    config: Any = None,
    model: str = "",
    env: Mapping[str, str] | None = None,
    config_path: str | Path | None = None,
) -> tuple[Any, LP.ProviderConfig | None, str, str]:
    """Give back ``(client, config, model, tier)`` for one image request.

    An injected ``client`` is used as-is (its ``config`` is read when it has one).
    Otherwise the provider is resolved from explicit config → environment → the
    config file, exactly like every other real call, and the *vision* model is
    picked by :func:`skaldir.llm_provider.vision_model` (``--model`` →
    ``$SKALDIR_LLM_VISION_MODEL`` → ``tiers.vision`` → the configured model) and
    made the model of the request. Raises :class:`VisionBlocked` when nothing is
    configured and :class:`VisionNeedsCredentials` for a remote endpoint with no
    key (no request is attempted).
    """
    if client is not None:
        cfg = getattr(client, "config", None)
        if not isinstance(cfg, LP.ProviderConfig):
            cfg = None
        name = str(model or "").strip()
        if not name and cfg is not None:
            name = LP.vision_model(cfg)
        return client, cfg, name, "main" if not name else _tier_for(cfg, name)

    cfg = LP.resolve_provider_config(config, env=env, config_path=config_path)
    if cfg.problems:
        raise VisionBlocked("; ".join(cfg.problems))
    if cfg.provider == "none":
        raise VisionBlocked(
            "the provider is disabled (provider: none) — enable it before sending an image"
        )
    if cfg.provider != "openai-compatible":
        raise VisionBlocked(
            f"provider {cfg.provider!r} has no image path; only 'openai-compatible' "
            f"speaks the content-part format this command sends"
        )
    if not cfg.ready():
        raise VisionBlocked(LP.not_configured_message(cfg))
    name = LP.vision_model(cfg, override=model)
    if not name:
        raise VisionBlocked(
            "no model configured — export SKALDIR_LLM_MODEL (or set godsv.llm.model)"
        )
    if not cfg.api_key and not _is_local(cfg.base_url):
        raise VisionNeedsCredentials(
            f"{LP.endpoint_for(cfg.base_url)} is remote and {LP.ENV_API_KEY} is unset — "
            f"export it (or its OPENAI_API_KEY fallback) and retry; no request was sent"
        )
    tier = _tier_for(cfg, name)
    if tier == "main" and name != cfg.model:
        cfg = LP.config_with(cfg, model=name)
    return LP.OpenAICompatibleLLM(cfg), cfg, name, tier


def _tier_for(cfg: LP.ProviderConfig | None, name: str) -> str:
    if cfg is None:
        return "main"
    return LP.vision_tier_for(cfg, name)


# --------------------------------------------------------------------------- #
# the cost note                                                                #
# --------------------------------------------------------------------------- #


def cost_note(
    source: ImageSource | None, usage: Mapping[str, Any] | None, *, text_chars: int = 0
) -> dict[str, Any]:
    """What this call costs, bounded and honest about which number is measured."""
    reported = {str(k): int(v) for k, v in (usage or {}).items() if isinstance(v, (int, float))}
    pixels = None
    estimate = None
    if source is not None and source.size:
        pixels = int(source.size[0]) * int(source.size[1])
        estimate = LP.estimate_image_tokens(source.size)
    note = (
        "an image part is billed as tokens, not as a free attachment: the estimate uses "
        "the documented rule (85 + pixels/750) and covers the image only"
    )
    if estimated_text := max(1, int(text_chars) // 4):
        note += f"; the prompt text adds roughly {estimated_text} tokens"
    if reported:
        note += (
            f"; the provider reported {reported.get('total_tokens')} total tokens for "
            f"this call — that is the measured number"
        )
    else:
        note += (
            "; the provider reported no usage counters for this call — the estimate is "
            "the only number here and it is a floor, not a bill"
        )
    return {
        "image_pixels": pixels,
        "image_tokens_estimated": estimate,
        "text_tokens_estimated": max(1, int(text_chars) // 4) if text_chars else 0,
        "reported_usage": reported,
        "reported": bool(reported),
        "note": note,
    }


# --------------------------------------------------------------------------- #
# the verbs                                                                    #
# --------------------------------------------------------------------------- #


def _request_part(source: ImageSource, *, max_bytes: int | None) -> dict[str, Any]:
    if source.kind == "url":
        return LP.image_part_from_url(source.url)
    return LP.image_part_from_bytes(
        source.payload or b"",
        mime=source.mime,
        origin=source.path or source.origin,
        max_bytes=max_image_bytes(max_bytes),
    )


def _call_verb(
    verb: str,
    target: str | None,
    *,
    prompt: str,
    client: Any = None,
    config: Any = None,
    model: str = "",
    tier: str | None = None,
    max_bytes: int | None = None,
    workspace: str | Path | None = None,
    allow_remote: bool = True,
    env: Mapping[str, str] | None = None,
    config_path: str | Path | None = None,
    source: ImageSource | None = None,
    on_send: Any = None,
) -> dict[str, Any]:
    """The one path ``describe`` and ``ocr`` share: resolve → send → report."""
    resolved_source = source
    loaded = False
    if resolved_source is None:
        if target is None:
            raise VisionBlocked("no image given")
        resolved_source = load_source(
            target, workspace=workspace, max_bytes=max_bytes, allow_remote=allow_remote
        )
        loaded = True
    client_obj, cfg, model_name, resolved_tier = resolve_client(
        client=client, config=config, model=model, env=env, config_path=config_path
    )
    if tier:
        resolved_tier = str(tier)
    endpoint = LP.endpoint_for(cfg.base_url) if cfg is not None else ""
    part = _request_part(resolved_source, max_bytes=max_bytes)
    body = LP.vision_request_body(cfg or LP.ProviderConfig(), prompt, [part], tier=resolved_tier)
    summary = LP.request_image_summary(body)
    sentence = disclosure(resolved_source, sent_to=endpoint or "<provider endpoint>")
    if on_send is not None:
        # The disclosure is emitted *before* the bytes move, by the only code that
        # knows both the file and the endpoint — never after the fact.
        try:
            on_send(sentence)
        except Exception:  # noqa: BLE001 — a logging hook must not break the call
            pass
    started = time.time()
    try:
        answer = LP.vision_chat(
            client_obj, prompt=prompt, parts=[part], tier=resolved_tier
        )
    except LP.ImageNotSupportedError as exc:
        return {
            "ok": False,
            "blocked": "model_rejects_images",
            "verb": verb,
            "reason": LP.scrub_image_data_urls(str(exc), limit=600),
            "model": model_name,
            "endpoint": endpoint,
            "source": resolved_source.report(sent_to=endpoint),
            "disclosure": sentence,
            "request": summary,
            "hint": "run `skaldir vision capabilities` to measure it, then point "
            "$SKALDIR_LLM_VISION_MODEL at a model that accepts images",
        }
    except LP.LLMProviderError as exc:
        return {
            "ok": False,
            "blocked": "provider_error",
            "verb": verb,
            "reason": LP.scrub_image_data_urls(str(exc), limit=600),
            "status": exc.status,
            "model": model_name,
            "endpoint": endpoint,
            "source": resolved_source.report(sent_to=endpoint),
            "disclosure": sentence,
            "request": summary,
        }
    except LLMError as exc:
        return {
            "ok": False,
            "blocked": "provider_error",
            "verb": verb,
            "reason": LP.scrub_image_data_urls(str(exc), limit=600),
            "model": model_name,
            "endpoint": endpoint,
            "source": resolved_source.report(sent_to=endpoint),
            "disclosure": sentence,
            "request": summary,
        }
    latency_ms = int((time.time() - started) * 1000)
    text = answer.text or ""
    if not text.strip():
        # The request went out and was billed, but there is no answer to print.
        # Returning an empty "answer" would look like a description, so this is a
        # verification failure with the finish reason and the usage in the message.
        total = (answer.usage or {}).get("total_tokens")
        return {
            "ok": False,
            "blocked": "empty_answer",
            "verb": verb,
            "reason": (
                f"the model answered with no text (finish_reason="
                f"{answer.finish_reason or 'unset'}, "
                f"{total if total is not None else 'no'} tokens reported) — the image was "
                f"sent and the call was billed, but there is nothing to print"
            ),
            "hint": (
                "raise --max-tokens (SKALDIR_LLM_MAX_TOKENS): finish_reason 'length' means "
                "the budget was spent before any text was produced"
            ),
            "finish_reason": answer.finish_reason,
            "model": model_name,
            "endpoint": endpoint,
            "source": resolved_source.report(sent_to=endpoint),
            "sent_to": endpoint,
            "disclosure": sentence,
            "request": summary,
            "usage": dict(answer.usage),
            "latency_ms": latency_ms,
            "cost": cost_note(
                resolved_source if resolved_source.kind == "file" else None,
                answer.usage,
                text_chars=summary.get("text_chars", 0),
            ),
        }
    payload: dict[str, Any] = {
        "ok": True,
        "verb": verb,
        "answer": answer.text,
        "finish_reason": answer.finish_reason,
        "model": model_name,
        "tier": resolved_tier,
        "endpoint": endpoint,
        "prompt": prompt,
        "source": resolved_source.report(sent_to=endpoint),
        "sent_to": endpoint,
        "disclosure": sentence,
        "request": summary,
        "usage": dict(answer.usage),
        "latency_ms": latency_ms,
        "cost": cost_note(
            resolved_source if resolved_source.kind == "file" else None,
            answer.usage,
            text_chars=summary.get("text_chars", 0),
        ),
    }
    if cfg is not None:
        payload["provider"] = _provider_summary(cfg)
    payload["cache_key"] = _cache_key(resolved_source, prompt, model_name)
    if loaded:
        payload["loaded"] = True
    return payload


def _provider_summary(cfg: LP.ProviderConfig | None) -> dict[str, Any]:
    """The bounded, credential-free view of the provider a call used.

    ``ProviderConfig.redacted()`` in full is 26 keys of config machinery; this is
    the six facts a reader of one image call needs, and ``api_key`` stays the
    word ``set``/``unset`` (the key itself is never rendered anywhere).
    """
    if cfg is None:
        return {}
    return {
        "provider": cfg.provider,
        "base_url": cfg.base_url,
        "endpoint": LP.endpoint_for(cfg.base_url),
        "model": cfg.model,
        "tiers": dict(cfg.tier_models),
        "api_key": "set" if cfg.api_key else "unset",
        "api_key_env": cfg.api_key_env,
        "config_file": cfg.config_file,
        "stream": bool(cfg.stream),
        "max_tokens": int(cfg.max_tokens),
        "timeout_s": float(cfg.timeout_s),
    }


def _cache_key(source: ImageSource, prompt: str, model: str) -> str:
    """A stable id for "this image + this prompt + this model" (evidence, not storage)."""
    material = "\x1f".join(
        [
            source.kind,
            source.sha256 or source.url,
            source.mime,
            str(source.bytes_len),
            prompt,
            model,
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def describe(
    target: str,
    *,
    prompt: str = DEFAULT_DESCRIBE_PROMPT,
    **kwargs: Any,
) -> dict[str, Any]:
    """``vision describe`` — send one image, return the model's answer."""
    return _call_verb("describe", target, prompt=prompt, **kwargs)


def ocr(target: str, *, prompt: str = DEFAULT_OCR_PROMPT, **kwargs: Any) -> dict[str, Any]:
    """``vision ocr`` — the same call with a read-the-text instruction.

    Deliberately not a second code path and not an OCR model of our own: the
    answer is the model's reading, quoted verbatim, and the payload says so.
    """
    return _call_verb("ocr", target, prompt=prompt, **kwargs)


def capabilities(
    *,
    client: Any = None,
    config: Any = None,
    model: str = "",
    probe: bool = True,
    tier: str | None = None,
    env: Mapping[str, str] | None = None,
    config_path: str | Path | None = None,
) -> dict[str, Any]:
    """``vision capabilities`` — does the configured model accept an image part?

    Measured by default: one 1x1 PNG is sent and the answer decides
    (``probe.accepts_images``). The model-name catalog is reported *next to* it
    with an explicit agreement statement, so the two can never be confused.
    """
    client_obj, cfg, model_name, resolved_tier = resolve_client(
        client=client, config=config, model=model, env=env, config_path=config_path
    )
    if tier:
        resolved_tier = str(tier)
    endpoint = LP.endpoint_for(cfg.base_url) if cfg is not None else ""
    catalog = LP.vision_catalog(model_name)
    payload: dict[str, Any] = {
        "ok": True,
        "verb": "capabilities",
        "model": model_name,
        "tier": resolved_tier,
        "endpoint": endpoint,
        "catalog": catalog,
        "probe": {"attempted": False, "accepts_images": None},
        "accepts_images": None,
        "measured": False,
        "agreement": "not_measured",
        "cost": cost_note(None, {}, text_chars=len(LP.PROBE_PROMPT)),
    }
    if not probe:
        payload["reason"] = (
            "the probe was skipped (--no-probe): the catalog verdict is a name match, "
            "not a measurement — nothing here proves the model can see"
        )
        payload["cost"]["note"] = (
            "no request was sent, so nothing was billed; no capability was measured either"
        )
        return payload
    result = LP.vision_probe(client_obj, tier=resolved_tier)
    payload["probe"] = result
    payload["accepts_images"] = result.get("accepts_images")
    payload["measured"] = result.get("accepts_images") is not None
    verdict = str(catalog.get("verdict", "unknown"))
    accepts = result.get("accepts_images")
    if accepts is None:
        payload["agreement"] = "inconclusive"
        payload["reason"] = (
            f"the probe could not decide ({(result.get('detail') or 'no detail')}) — the "
            f"capability stays unmeasured; the catalog says {verdict}"
        )
    elif accepts:
        payload["agreement"] = "catalog_agrees" if verdict == "likely" else (
            "catalog_was_wrong" if verdict == "unlikely" else "catalog_silent"
        )
    else:
        payload["agreement"] = "catalog_agrees" if verdict == "unlikely" else (
            "catalog_was_wrong" if verdict == "likely" else "catalog_silent"
        )
    payload["cost"] = cost_note(
        None, result.get("usage"), text_chars=len(str(result.get("prompt") or ""))
    )
    payload["cost"]["image_pixels"] = 1
    payload["cost"]["image_tokens_estimated"] = LP.estimate_image_tokens((1, 1))
    payload["cost"]["note"] = (
        "the probe is one 1x1 PNG (about "
        f"{LP.estimate_image_tokens((1, 1))} image tokens) — the smallest request the "
        "guard allows, and the only way to answer this without guessing"
    )
    return payload


# --------------------------------------------------------------------------- #
# the Computer-Use screenshot bridge                                           #
# --------------------------------------------------------------------------- #


def _missing_capability(reason: str) -> str:
    """Name the missing piece for a refused capture (never a generic "failed")."""
    text = str(reason or "")
    if "import missing" in text or CU_SCREENSHOT_TOOL in text:
        return f"screenshot backend (ImageMagick `{CU_SCREENSHOT_TOOL}`)"
    return f"screenshot backend ({text or 'unknown'})"


def default_capture_path(*, out: str | Path | None = None) -> Path:
    """Where a capture lands: ``--out``, else ``<tmp>/skaldir-vision/screen-<ts>.png``."""
    if out:
        return Path(str(out)).expanduser()
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    return Path(tempfile.gettempdir()) / "skaldir-vision" / f"screen-{stamp}.png"


def capture_screen(
    *,
    out: str | Path | None = None,
    window: str = "",
    timeout: float = 20.0,
) -> dict[str, Any]:
    """Capture the screen (or one window) through the existing Computer-Use layer.

    The layer is :mod:`skaldir.workflows` — ``x11_windows``/``find_window`` for the
    inventory and ``screenshot()`` for the capture. When the backend is absent the
    verdict is ``blocked: no_cu_bridge`` and ``missing_capability`` names exactly
    what is missing; nothing is faked and no substitute image is produced.
    """
    from . import workflows as WF  # the Computer-Use layer, imported lazily

    windows: list[dict[str, str]] = []
    inventory_error = ""
    try:
        windows = WF.x11_windows()
    except Exception as exc:  # noqa: BLE001 — an inventory failure is reported, not fatal
        inventory_error = f"{type(exc).__name__}: {exc}"
    target = default_capture_path(out=out)
    verdict: dict[str, Any] = {
        "ok": False,
        "verb": "screenshot",
        "capture": {
            "path": str(target),
            "window": str(window or ""),
            "xid": "",
            "backend": "",
            "windows_seen": len(windows),
        },
        "windows": windows[:20],
    }
    if shutil.which(CU_SCREENSHOT_TOOL) is None:
        verdict.update(
            {
                "blocked": "no_cu_bridge",
                "missing_capability": f"screenshot backend (ImageMagick `{CU_SCREENSHOT_TOOL}`)",
                "reason": f"the Computer-Use layer has no screenshot backend: "
                f"`{CU_SCREENSHOT_TOOL}` is not in PATH",
                "hint": "install ImageMagick (`dnf install ImageMagick`) or run "
                "`skaldir cu check` for the layer's own probe",
            }
        )
        return verdict
    if window:
        if not windows and inventory_error:
            verdict.update(
                {
                    "blocked": "no_cu_bridge",
                    "missing_capability": "window inventory (xprop)",
                    "reason": f"the window inventory failed: {inventory_error}",
                }
            )
            return verdict
        hit = WF.find_window(title_contains=window, wm_class_contains=window)
        if hit is None:
            verdict.update(
                {
                    "blocked": "window_not_found",
                    "reason": f"no window matches {window!r} (inventory: "
                    f"{len(windows)} window(s))",
                    "hint": "run `skaldir vision screenshot --list-windows` to see the titles",
                }
            )
            return verdict
        verdict["capture"]["xid"] = str(hit.get("xid", ""))
        verdict["capture"]["window_title"] = str(hit.get("title", ""))
    if not target.parent.exists():
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            verdict.update(
                {
                    "blocked": "no_cu_bridge",
                    "missing_capability": f"a writable capture directory ({target.parent})",
                    "reason": f"{target.parent} could not be created: {exc}",
                }
            )
            return verdict
    try:
        ok, backend = WF.screenshot(target, xid=str(verdict["capture"]["xid"]))
    except Exception as exc:  # noqa: BLE001 — the layer's own failure is the verdict
        ok, backend = False, f"{type(exc).__name__}: {exc}"
    if not ok:
        verdict.update(
            {
                "blocked": "no_cu_bridge",
                "missing_capability": _missing_capability(backend),
                "reason": f"the Computer-Use layer could not capture the screen: {backend}",
                "hint": "the layer's own probe is `skaldir cu check` and "
                "`skaldir cu demo --json`; on a Wayland session an X11 fallback may be "
                "unavailable (a real limitation, not a retry loop)",
            }
        )
        return verdict
    data = target.read_bytes() if target.exists() else b""
    size = LP.image_size(data) if data else None
    capture = dict(verdict["capture"])
    capture.update(
        {
            "backend": str(backend),
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest() if data else "",
            "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime()),
            "size": [int(size[0]), int(size[1])] if size else None,
        }
    )
    verdict["ok"] = True
    verdict["capture"] = capture
    verdict["missing_capability"] = ""
    return verdict


def screenshot(
    *,
    out: str | Path | None = None,
    window: str = "",
    prompt: str = DEFAULT_DESCRIBE_PROMPT,
    capture_only: bool = False,
    client: Any = None,
    config: Any = None,
    model: str = "",
    tier: str | None = None,
    max_bytes: int | None = None,
    workspace: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    config_path: str | Path | None = None,
    on_send: Any = None,
) -> dict[str, Any]:
    """Capture through the Computer-Use layer, then describe the capture.

    A refused capture is returned as ``blocked`` (exit 3) with the precise
    missing capability — the describe half is simply not reached. With
    ``capture_only`` the file is captured and *not* sent anywhere, and the
    payload says so.
    """
    captured = capture_screen(out=out, window=window)
    if not captured.get("ok"):
        return captured
    payload: dict[str, Any] = {
        "ok": True,
        "verb": "screenshot",
        "capture": captured["capture"],
        "windows": captured.get("windows", []),
        "sent_to": "",
        "disclosure": "nothing was sent: this was a capture-only run",
    }
    if capture_only:
        payload["describe"] = {
            "skipped": "capture-only",
            "reason": "--capture-only stopped after the capture; the PNG stayed local",
        }
        payload["cost"] = cost_note(None, {})
        payload["cost"]["note"] = "no request was sent, so nothing was billed"
        return payload
    try:
        source = load_source(
            str(captured["capture"]["path"]), workspace=workspace, max_bytes=max_bytes
        )
    except (VisionBlocked, VisionNotFound, LP.ImageInputError) as exc:
        payload.update(
            {
                "ok": False,
                "blocked": "capture_unreadable",
                "reason": f"the capture could not be read back: {exc}",
            }
        )
        return payload
    described = _call_verb(
        "screenshot",
        None,
        prompt=prompt,
        client=client,
        config=config,
        model=model,
        tier=tier,
        max_bytes=max_bytes,
        workspace=workspace,
        env=env,
        config_path=config_path,
        source=source,
        on_send=on_send,
    )
    payload["capture"] = captured["capture"]
    for key in ("answer", "model", "tier", "endpoint", "usage", "cost", "latency_ms",
                "request", "source", "sent_to", "disclosure", "provider", "cache_key",
                "finish_reason"):
        if key in described:
            payload[key] = described[key]
    if not described.get("ok"):
        payload["ok"] = False
        payload["blocked"] = str(described.get("blocked") or "provider_error")
        payload["reason"] = str(described.get("reason") or "")
        payload["hint"] = str(described.get("hint") or "")
    payload["prompt"] = prompt
    return payload


def list_windows() -> list[dict[str, str]]:
    """The Computer-Use layer's window inventory (titles, classes, ids)."""
    from . import workflows as WF

    try:
        return list(WF.x11_windows())
    except Exception:  # noqa: BLE001 — no bridge means no windows, not a crash
        return []


def environ_workspace() -> str:
    """The workspace an operator configured (empty when unset) — for the report."""
    return str(os.environ.get(ENV_WORKSPACE, "") or "")
