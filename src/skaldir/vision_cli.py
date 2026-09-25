"""``skaldir vision …`` — put an image in front of the configured model.

Four verbs over :mod:`skaldir.vision_service` + :mod:`skaldir.llm_provider`:

============================  ====================================================
``vision describe <path|url>``  send one image, print the model's answer.
``vision ocr <path>``           the same call with a read-the-text instruction.
``vision capabilities``         *measure* (one 1x1 PNG probe) whether the
                                configured model accepts an image part, and print
                                that next to the model-name catalog's expectation.
``vision screenshot``           capture the screen (or one window) through the
                                existing Computer-Use layer, then describe it.
                                ``--capture-only`` stops after the capture, and
                                ``--list-windows`` prints the CU window inventory
                                without capturing anything.
============================  ====================================================

What the output always says (an image is not free, and a file is not sent silently)
-----------------------------------------------------------------------------------

Every success payload carries ``source`` (absolute path, mime, byte count, sha256),
``sent_to`` (the endpoint the bytes went to), a one-sentence ``disclosure`` and a
``cost`` block: the documented image-token estimate (85 + pixels/750) **and** the
provider's own ``usage`` counters, with a note naming which one is the measurement.
The disclosure is also written to stderr before the request leaves, so a piped
``--json`` run still shows the human what left the machine.

Exit codes — the product CLI's ``cli_contract`` vocabulary::

    0   ok                 a real answer / a measured capability was produced
    1   usage              no image, no ``--out`` directory, argparse refused the call
    2   verification       the call ran but produced nothing usable:
                           ``vision capabilities --no-probe`` (a name match, not a
                           measurement) or a model that answered with **no text**
                           (e.g. ``finish_reason: length`` after spending the whole
                           ``max_tokens`` budget on reasoning) — an empty
                           ``answer`` is never printed as a description
    3   blocked            no provider configured · the model refused the image part
                           · a refused symlink · the size/type guard · **no
                           Computer-Use screenshot bridge** (the verdict names the
                           missing capability: ``missing_capability``)
    4   needs credentials  remote endpoint without a key (no request is attempted),
                           or the endpoint answered 401/403
    5   not found          the file (or the window) is not there
    7   internal           an envelope problem
    8   unknown            the provider call itself failed (transport/timeout/5xx)

Never ``0`` for "could not try": every refusal above is its own code with its own
message, and ``accepts_images``/``measured`` are ``null``/``false`` when nothing was
measured.

The group is added by the product CLI through one call (the CLI owner wires it; this
module never edits ``cli.py``)::

    # Vision / images: one image in front of the configured model. The group owns
    # its four verbs and their `cli_contract.COMMANDS` entries — see the hand-over
    # note in src/skaldir/vision_cli.py.
    from .vision_cli import register_cli as _register_vision_cli

    _register_vision_cli(sub)

``register_cli`` is idempotent and adds the four dotted paths
(``vision.describe``, ``vision.ocr``, ``vision.capabilities``,
``vision.screenshot``) to ``cli_contract.COMMANDS`` itself, so a wired-in group
never ships without a contract (``tests/test_cli_contract_dump.py``).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from . import vision_service as VS
from .cli_shared import (
    dump,
    install_contract_entries,
    product_contract,
    runtime_dir,
    share_common_flags,
    text_mode,
)

_CONTRACT = product_contract()

EXIT_OK = int(getattr(_CONTRACT, "EXIT_OK", 0))
EXIT_USAGE = int(getattr(_CONTRACT, "EXIT_USAGE", 1))
EXIT_VERIFICATION_FAILED = int(getattr(_CONTRACT, "EXIT_VERIFICATION_FAILED", 2))
EXIT_BLOCKED = int(getattr(_CONTRACT, "EXIT_BLOCKED", 3))
EXIT_NEEDS_CREDENTIALS = int(getattr(_CONTRACT, "EXIT_NEEDS_CREDENTIALS", 4))
EXIT_NOT_FOUND = int(getattr(_CONTRACT, "EXIT_NOT_FOUND", 5))
EXIT_INTERNAL = int(getattr(_CONTRACT, "EXIT_INTERNAL", 7))
EXIT_UNKNOWN = int(getattr(_CONTRACT, "EXIT_UNKNOWN", 8))

_EVIDENCE_KINDS = ("code", "test", "file", "run", "verification")

#: ``blocked`` value → the sentence a caller reads (the reason carries the detail).
_BLOCKED_LABELS = {
    "no_cu_bridge": "the Computer-Use layer has no screenshot bridge",
    "window_not_found": "no window matches that name",
    "file_not_found": "the file is not there",
    "model_rejects_images": "the configured model does not accept image parts",
    "provider_error": "the provider call failed",
    "capture_unreadable": "the capture could not be read back",
    "empty_answer": "no usable answer",
    "image_guard": "the image guard refused the file",
    "needs_credentials": "the provider needs a credential",
    "internal": "the command could not complete",
}


# --------------------------------------------------------------------------- #
# the shared envelope helpers (the same three refusals every group reports)     #
# --------------------------------------------------------------------------- #


def _notice(text: str) -> None:
    """The human line (stdout is the JSON document in ``--json`` mode)."""
    print(str(text), file=sys.stderr, flush=True)


def _usage(message: str, *, hint: str = "") -> int:
    if _CONTRACT is not None and hasattr(_CONTRACT, "usage_error"):
        return int(_CONTRACT.usage_error(message, hint=hint))
    _notice(f"usage: {message}")
    return EXIT_USAGE


def _blocked(
    message: str, *, hint: str = "", next_command: str = "", reason: str = "",
    details: dict | None = None,
) -> int:
    if _CONTRACT is not None and hasattr(_CONTRACT, "blocked"):
        return int(
            _CONTRACT.blocked(
                message, hint=hint, next_command=next_command, reason=reason, details=details
            )
        )
    _notice(f"blocked: {message}")
    return EXIT_BLOCKED


def _not_found(message: str, *, hint: str = "", details: dict | None = None) -> int:
    if _CONTRACT is not None and hasattr(_CONTRACT, "not_found"):
        return int(_CONTRACT.not_found(message, hint=hint, details=details))
    _notice(f"not found: {message}")
    return EXIT_NOT_FOUND


def _needs_credentials(message: str, *, hint: str = "", details: dict | None = None) -> int:
    if _CONTRACT is not None and hasattr(_CONTRACT, "needs_credentials"):
        return int(_CONTRACT.needs_credentials(message, hint=hint, details=details))
    _notice(f"needs credentials: {message}")
    return EXIT_NEEDS_CREDENTIALS


def _verification_failed(message: str, *, hint: str = "", details: dict | None = None) -> int:
    if _CONTRACT is not None and hasattr(_CONTRACT, "verification_failed"):
        return int(_CONTRACT.verification_failed(message, hint=hint, details=details))
    _notice(f"verification failed: {message}")
    return EXIT_VERIFICATION_FAILED


def _fail(
    message: str, *, hint: str = "", code: int = EXIT_INTERNAL, retryable: bool = False
) -> int:
    """The contract's ``fail`` (note: the code is its *first* argument)."""
    if _CONTRACT is not None and hasattr(_CONTRACT, "fail"):
        return int(_CONTRACT.fail(code, message, hint=hint, retryable=retryable))
    _notice(f"failed: {message}")
    return code


# --------------------------------------------------------------------------- #
# verdict → exit code (one place, so every verb refuses alike)                  #
# --------------------------------------------------------------------------- #


def _refusal_code(payload: dict) -> int:
    """Map a non-ok service verdict onto the contract's exit vocabulary."""
    blocked = str(payload.get("blocked") or "")
    if blocked in ("window_not_found", "file_not_found"):
        return EXIT_NOT_FOUND
    if blocked == "needs_credentials":
        return EXIT_NEEDS_CREDENTIALS
    if blocked == "provider_error":
        if payload.get("status") in (401, 403):
            return EXIT_NEEDS_CREDENTIALS
        return EXIT_UNKNOWN
    if blocked == "internal":
        return EXIT_INTERNAL
    if blocked == "empty_answer":
        return EXIT_VERIFICATION_FAILED
    return EXIT_BLOCKED


def refusal_payload(exc: Exception, *, verb: str, subject: str = "") -> dict:
    """Turn a service exception into the same verdict dict the service returns.

    One shape for every refusal, so the exit code, the message and the details
    are decided in one place instead of per verb.
    """
    reason = str(exc)
    verdict: dict[str, Any] = {"ok": False, "verb": verb, "reason": reason}
    if isinstance(exc, VS.VisionNotFound):
        verdict["blocked"] = "file_not_found"
        verdict["hint"] = "check the path, or pass an http(s) URL"
    elif isinstance(exc, VS.VisionNeedsCredentials):
        verdict["blocked"] = "needs_credentials"
        verdict["hint"] = f"export {VS.LP.ENV_API_KEY} (or OPENAI_API_KEY) and retry"
    elif isinstance(exc, VS.VisionBlocked):
        verdict["blocked"] = "blocked"
        verdict["hint"] = "see `skaldir provider list --json` for the configured provider"
    elif isinstance(exc, VS.LP.ImageInputError):
        verdict["blocked"] = "image_guard"
        verdict["hint"] = (
            "the provider-side guard refused the image before a request was built: only "
            f"{', '.join(VS.LP.SUPPORTED_IMAGE_MIME)} under the size cap are sent"
        )
    else:
        verdict["blocked"] = "internal"
        verdict["hint"] = "unexpected failure — the reason above is the exception's own text"
    if subject:
        verdict["source"] = subject
    return verdict


def _report_refusal(payload: dict, *, subject: str = "") -> int:
    """One refusal path for every verb: the reason, the label, the hint, the code."""
    blocked = str(payload.get("blocked") or "")
    label = _BLOCKED_LABELS.get(blocked, "the call could not be made")
    reason = str(payload.get("reason") or label)
    hint = str(payload.get("hint") or "")
    details = {
        "blocked": blocked,
        "verb": str(payload.get("verb") or ""),
        "model": str(payload.get("model") or ""),
        "missing_capability": str(payload.get("missing_capability") or ""),
        "source": payload.get("source") or subject,
        "windows_seen": (payload.get("capture") or {}).get("windows_seen"),
        # A refused call still says what would have been sent (and the pre-send
        # notice on stderr already named the file and the endpoint).
        "disclosure": payload.get("disclosure"),
        "sent_to": payload.get("sent_to"),
    }
    details = {key: value for key, value in details.items() if value not in ("", None)}
    code = _refusal_code(payload)
    if code == EXIT_NOT_FOUND:
        return _not_found(reason, hint=hint, details=details)
    if code == EXIT_NEEDS_CREDENTIALS:
        return _needs_credentials(reason, hint=hint, details=details)
    if code == EXIT_VERIFICATION_FAILED:
        return _verification_failed(
            reason if label in reason else f"{label} — {reason}", hint=hint, details=details
        )
    if code in (EXIT_INTERNAL, EXIT_UNKNOWN):
        _notice(f"{label}: {reason}")
        return _fail(reason, hint=hint, code=code, retryable=code == EXIT_UNKNOWN)
    message = reason if label in reason else f"{label} — {reason}"
    return _blocked(message, hint=hint, details=details)


# --------------------------------------------------------------------------- #
# evidence                                                                      #
# --------------------------------------------------------------------------- #


def _call_evidence(payload: dict) -> tuple[dict, ...]:
    """The evidence one image call stands on: the request, the file, the cost."""
    request = payload.get("request") or {}
    source = payload.get("source") or {}
    parts = request.get("image_parts") or []
    cost = payload.get("cost") or {}
    reported = cost.get("reported_usage") or {}
    evidence = [
        {
            "ref": f"POST {payload.get('endpoint') or '<unset>'}",
            "kind": "run",
            "note": (
                f"model {payload.get('model') or '<unset>'}, "
                f"{len(parts)} image part(s), {int(request.get('image_bytes') or 0)} image "
                f"byte(s), {payload.get('latency_ms')} ms; image tokens estimated "
                f"{cost.get('image_tokens_estimated')}, provider reported "
                f"{reported.get('total_tokens') if reported else 'no usage'}"
            ),
        }
    ]
    where = str(source.get("path") or source.get("url") or "")
    if where:
        evidence.append(
            {
                "ref": where,
                "kind": "file" if source.get("kind") == "file" else "run",
                "note": (
                    f"{source.get('mime') or 'remote'} {int(source.get('bytes') or 0)} "
                    f"byte(s) sha256:{str(source.get('sha256') or '')[:16]}"
                    + (f" via symlink {source.get('symlink')}" if source.get("symlink") else "")
                ),
            }
        )
    return tuple(evidence)


# --------------------------------------------------------------------------- #
# text rendering                                                                #
# --------------------------------------------------------------------------- #


def render_text(payload: dict) -> str:
    """The human rendering (``--text``); the JSON payload stays the contract."""
    if not payload.get("ok"):
        return (
            f"{payload.get('verb') or 'vision'}: {payload.get('blocked') or 'failed'} — "
            f"{payload.get('reason') or ''}"
        )
    verb = str(payload.get("verb") or "")
    lines: list[str] = []
    if verb == "capabilities":
        accepts = payload.get("accepts_images")
        measured = "measured" if payload.get("measured") else "NOT measured"
        answer = {True: "yes", False: "no", None: "unknown"}[accepts]
        lines.append(f"model {payload.get('model')}  endpoint {payload.get('endpoint')}")
        lines.append(f"  accepts images: {answer} ({measured})")
        lines.append(
            f"  catalog says: {payload.get('catalog', {}).get('verdict')} — "
            f"{payload.get('catalog', {}).get('reason')}"
        )
        lines.append(f"  agreement: {payload.get('agreement')}")
        probe = payload.get("probe") or {}
        if probe.get("attempted"):
            lines.append(
                f"  probe: 1x1 PNG ({probe.get('image', {}).get('bytes')} bytes) → "
                f"HTTP {probe.get('status')} · accepts_images={probe.get('accepts_images')} "
                f"· answer {probe.get('answer')!r} {probe.get('detail') or ''}".rstrip()
            )
        if payload.get("reason"):
            lines.append(f"  {payload['reason']}")
    else:
        capture = payload.get("capture") or {}
        if capture:
            lines.append(
                f"captured {capture.get('path')} via {capture.get('backend')} "
                f"({capture.get('bytes')} bytes, size {capture.get('size')})"
            )
        else:
            windows = list(payload.get("windows") or [])
            lines.append(f"no capture: {len(windows)} window(s) in the Computer-Use inventory")
            for window in windows:
                lines.append(
                    f"  {window.get('xid')}  {window.get('title')}  [{window.get('wm_class')}]"
                )
            if payload.get("warning"):
                lines.append(f"  {payload['warning']}")
        lines.append(str(payload.get("disclosure") or ""))
        if payload.get("answer") is not None:
            lines.append("")
            lines.append(str(payload.get("answer")))
        for line in (payload.get("describe") or {}).values():
            lines.append(f"  {line}")
    cost = payload.get("cost") or {}
    lines.append("")
    lines.append(
        f"cost: image {cost.get('image_pixels')} px ≈ {cost.get('image_tokens_estimated')} "
        f"tokens (estimate) · text ≈ {cost.get('text_tokens_estimated')} tokens · "
        f"provider usage {cost.get('reported_usage') or 'not reported'}"
    )
    lines.append(f"  {cost.get('note')}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# verbs                                                                         #
# --------------------------------------------------------------------------- #


def _call_kwargs(args: argparse.Namespace) -> dict[str, Any]:
    """The service arguments every image verb shares (CLI flags → service call)."""
    megabytes = int(getattr(args, "max_image_mb", 0) or 0)
    return {
        "model": str(getattr(args, "model", "") or ""),
        "tier": str(getattr(args, "tier", "") or "") or None,
        "max_bytes": megabytes * 1024 * 1024 if megabytes > 0 else None,
        "workspace": str(getattr(args, "workspace", "") or "") or None,
        "on_send": _notice_for(args),
    }


def _notice_for(args: argparse.Namespace) -> Any:
    """The pre-send disclosure hook — ``--quiet`` silences the stderr copy only.

    The sentence stays in the payload either way (``disclosure``/``source``/
    ``sent_to``), so the machine-readable record never depends on stderr.
    """
    if bool(getattr(args, "quiet", False)):
        return lambda text: None
    return _notice


def _emit(
    payload: dict,
    args: argparse.Namespace,
    *,
    evidence: Any = None,
    warnings: tuple[str, ...] = (),
    next_actions: tuple[tuple[str, str], ...] = (),
) -> None:
    """Print one payload (human text or the one JSON document) and fill the envelope.

    Printing and the envelope stay together here because every verb in this group
    does both: the JSON document is the product's record of exactly the payload the
    human rendering shows. ``evidence=None`` means "this group's image-call evidence";
    a verb that sent nothing (``capabilities``, ``--list-windows``) passes its own.
    """
    if text_mode(args):
        print(render_text(payload))
        for command, reason in next_actions:
            print(f"next: {command}  ({reason})")
    else:
        dump(payload)
    if _CONTRACT is None:
        return
    try:
        items = tuple(_call_evidence(payload) if evidence is None else evidence)
        for item in items:
            kind = str(item.get("kind", "code"))
            _CONTRACT.add_evidence(
                str(item.get("ref", "")),
                kind if kind in _EVIDENCE_KINDS else "code",
                str(item.get("note", "")),
            )
        for text in warnings:
            _CONTRACT.warn(str(text))
        for command, reason in next_actions:
            _CONTRACT.next_action(str(command), str(reason))
        # Last, so a broken evidence entry can never leave a half-filled payload.
        _CONTRACT.set_data(payload)
    except Exception:  # noqa: BLE001 — an envelope problem must not break a verb
        return


def _probe_evidence(payload: dict) -> tuple[dict, ...]:
    """The evidence a *measured* capability stands on: the 1x1 probe and its answer."""
    probe = payload.get("probe") or {}
    image = probe.get("image") or {}
    usage = probe.get("usage") or {}
    return (
        {
            "ref": f"POST {payload.get('endpoint') or '<unset>'}",
            "kind": "run",
            "note": (
                f"model {payload.get('model')}, one {image.get('width')}x{image.get('height')} "
                f"PNG ({image.get('bytes')} bytes) → detects "
                f"accepts_images={probe.get('accepts_images')} (HTTP {probe.get('status')}), "
                f"provider reported {usage.get('total_tokens', 'no')} tokens"
            ),
        },
        {
            "ref": "src/skaldir/llm_provider.py:vision_probe",
            "kind": "code",
            "note": (
                "the probe sends the smallest image the guard allows, through the real "
                "chat path"
            ),
        },
    )


def _finish_image_call(
    payload: dict,
    args: argparse.Namespace,
    *,
    verb: str,
    extra_evidence: tuple[dict, ...] = (),
) -> int:
    """Shared tail of ``describe``/``ocr``/``screenshot``: output + the exit code.

    The disclosure already went to stderr from the service, *before* the request
    left (``on_send``); here the payload carries it for the record.
    """
    if not payload.get("ok"):
        return _report_refusal(payload)
    warnings = ()
    reason = str(payload.get("finish_reason") or "")
    if reason and reason != "stop":
        warnings = (f"the model stopped with finish_reason={reason}",)
    _emit(
        payload,
        args,
        evidence=tuple(extra_evidence) + _call_evidence(payload),
        warnings=warnings,
        next_actions=(
            (
                f"skaldir vision capabilities --model {payload.get('model')} --json",
                "measure whether this model really accepts images",
            ),
        ),
    )
    return EXIT_OK


def cmd_describe(args: argparse.Namespace) -> int:
    """``skaldir vision describe <path|url>``."""
    source = str(getattr(args, "source", "") or "").strip()
    if not source:
        return _usage(
            "vision describe needs an image (a file path or an http(s) URL)",
            hint="e.g. skaldir vision describe ./shot.png --json",
        )
    prompt = str(getattr(args, "prompt", "") or "") or VS.DEFAULT_DESCRIBE_PROMPT
    try:
        payload = VS.describe(
            source,
            prompt=prompt,
            allow_remote=not bool(getattr(args, "no_remote", False)),
            **_call_kwargs(args),
        )
    except Exception as exc:  # noqa: BLE001 — every refusal is mapped, never swallowed
        payload = refusal_payload(exc, verb="describe", subject=source)
    return _finish_image_call(payload, args, verb="describe")


def cmd_ocr(args: argparse.Namespace) -> int:
    """``skaldir vision ocr <path>`` — the same call, read-the-text instruction."""
    source = str(getattr(args, "source", "") or "").strip()
    if not source:
        return _usage(
            "vision ocr needs an image file",
            hint="e.g. skaldir vision ocr ./screenshot.png --json",
        )
    prompt = str(getattr(args, "prompt", "") or "") or VS.DEFAULT_OCR_PROMPT
    try:
        payload = VS.ocr(
            source,
            prompt=prompt,
            allow_remote=not bool(getattr(args, "no_remote", False)),
            **_call_kwargs(args),
        )
    except Exception as exc:  # noqa: BLE001
        payload = refusal_payload(exc, verb="ocr", subject=source)
    payload["text"] = payload.get("answer", "")
    return _finish_image_call(payload, args, verb="ocr")


def cmd_capabilities(args: argparse.Namespace) -> int:
    """``skaldir vision capabilities`` — measured by default, never assumed."""
    probe = not bool(getattr(args, "no_probe", False))
    try:
        payload = VS.capabilities(
            model=str(getattr(args, "model", "") or ""),
            probe=probe,
            tier=str(getattr(args, "tier", "") or "") or None,
        )
    except Exception as exc:  # noqa: BLE001
        return _report_refusal(refusal_payload(exc, verb="capabilities"))
    payload["cost"]["text_tokens_estimated"] = 0
    warnings: list[str] = []
    if payload.get("agreement") == "catalog_was_wrong":
        warnings.append(
            "the model-name catalog's expectation was wrong here — the probe, not the "
            "name, is the answer"
        )
    if payload.get("accepts_images") is False:
        warnings.append(
            "the model refused the image part: `vision describe`/`ocr` cannot work until a "
            "vision-capable model is configured"
        )
    _emit(payload, args, evidence=_probe_evidence(payload), warnings=tuple(warnings))
    if not payload.get("measured"):
        return _verification_failed(
            str(payload.get("reason") or "the capability was not measured"),
            hint="run without --no-probe to send the 1x1 PNG probe",
            details={"model": payload.get("model"), "route": "catalog-and-probe"},
        )
    return EXIT_OK


def cmd_screenshot(args: argparse.Namespace) -> int:
    """``skaldir vision screenshot`` — capture via Computer Use, then describe."""
    if bool(getattr(args, "list_windows", False)):
        windows = VS.list_windows()
        payload = {
            "ok": True,
            "verb": "screenshot",
            "capture": None,
            "windows": windows,
            "windows_seen": len(windows),
            "disclosure": "nothing was sent: this only listed the windows",
            "cost": {
                "image_pixels": None,
                "image_tokens_estimated": None,
                "text_tokens_estimated": 0,
                "reported_usage": {},
                "reported": False,
                "note": "no request was sent, so nothing was billed",
            },
        }
        if not windows:
            payload["warning"] = (
                "the Computer-Use window inventory is empty — no X11/XWayland windows "
                "are visible to `xprop` on this session"
            )
        _emit(
            payload,
            args,
            evidence=(
                {"ref": "xprop -root _NET_CLIENT_LIST", "kind": "run",
                 "note": f"{len(windows)} window(s) seen by the Computer-Use inventory"},
            ),
        )
        return EXIT_OK
    out = str(getattr(args, "out", "") or "").strip()
    if not out:
        out = str(_default_out_dir(args) / _stamp_name())
    try:
        payload = VS.screenshot(
            out=out,
            window=str(getattr(args, "window", "") or ""),
            prompt=str(getattr(args, "prompt", "") or "") or VS.DEFAULT_DESCRIBE_PROMPT,
            capture_only=bool(getattr(args, "capture_only", False)),
            **_call_kwargs(args),
        )
    except Exception as exc:  # noqa: BLE001
        return _report_refusal(refusal_payload(exc, verb="screenshot", subject=out))
    if not payload.get("ok"):
        if payload.get("capture"):
            payload.setdefault("source", payload["capture"].get("path", ""))
        return _report_refusal(payload, subject=str(out))
    capture = payload.get("capture") or {}
    extra: tuple[dict, ...] = ()
    if capture:
        extra = (
            {
                "ref": str(capture.get("path")),
                "kind": "file",
                "note": (
                    f"captured via {capture.get('backend')} ({capture.get('bytes')} bytes, "
                    f"size {capture.get('size')})"
                ),
            },
        )
    return _finish_image_call(payload, args, verb="screenshot", extra_evidence=extra)


def _default_out_dir(args: argparse.Namespace) -> Path:
    """Where a capture lands by default: the runtime dir's ``vision/`` folder."""
    try:
        return runtime_dir(args) / "vision"
    except Exception:  # noqa: BLE001 — no runtime resolution is not a refusal to capture
        return Path.cwd() / ".godsv" / "vision"


def _stamp_name() -> str:
    import time

    return f"screen-{time.strftime('%Y%m%d-%H%M%S', time.localtime())}.png"


# --------------------------------------------------------------------------- #
# parser + entry points                                                         #
# --------------------------------------------------------------------------- #


def _add_shared_image_args(parser: Any) -> None:
    parser.add_argument("--model", default="",
                        help="the model to ask (default: $SKALDIR_LLM_VISION_MODEL, then "
                             "tiers.vision, then the configured model)")


#: The one help string ``--text`` carries on every leaf of this group.
_TEXT_HELP = "human-readable output instead of the JSON report"


def _add_output_flags(parsers: Sequence[Any]) -> None:
    """``--text`` on every leaf of this group (``--format``/``--json`` come from cli_shared).

    One loop rather than one call per parser: the flag vocabulary of the group stays in
    a single place, and adding a fifth verb cannot forget the flag.
    """
    for parser in parsers:
        parser.add_argument(
            "--text", action="store_true", help=_TEXT_HELP
        )


def _add_limits(parser: Any) -> None:
    parser.add_argument("--max-image-mb", type=int, default=0, metavar="MB",
                        help=f"cap for one image (default {VS.DEFAULT_MAX_IMAGE_MB} MiB, "
                             f"or $SKALDIR_VISION_MAX_IMAGE_MB)")
    parser.add_argument("--workspace", default="",
                        help="the root a symlink may not leave (default: "
                             "$SKALDIR_VISION_WORKSPACE, else the current directory)")
    parser.add_argument("--tier", default="",
                        help="the tier to send as (default: the vision/model tier)")


def _describe_args(parser: Any) -> None:
    parser.add_argument("source", nargs="?", default="",
                        help="image file path or http(s) URL")
    parser.add_argument("--prompt", default="",
                        help="override the instruction sent with the image")
    parser.add_argument("--no-remote", action="store_true",
                        help="refuse http(s) URLs (only a local file is sent)")
    _add_shared_image_args(parser)
    _add_limits(parser)


def _ocr_args(parser: Any) -> None:
    parser.add_argument("source", nargs="?", default="", help="image file path")
    parser.add_argument("--prompt", default="",
                        help="override the OCR instruction sent with the image")
    parser.add_argument("--no-remote", action="store_true",
                        help="refuse http(s) URLs (only a local file is sent)")
    _add_shared_image_args(parser)
    _add_limits(parser)


def _capabilities_args(parser: Any) -> None:
    parser.add_argument("--no-probe", action="store_true",
                        help="skip the 1x1 PNG probe (the verdict is then NOT measured: "
                             "exit 2)")
    _add_shared_image_args(parser)
    parser.add_argument("--tier", default="",
                        help="the tier to probe as (default: the model/vision tier)")


def _screenshot_args(parser: Any) -> None:
    parser.add_argument("--out", default="",
                        help="where the capture is written (default: "
                             "<runtime>/vision/screen-<timestamp>.png)")
    parser.add_argument("--window", default="",
                        help="capture one window instead of the screen: a title or WM_CLASS "
                             "substring from the Computer-Use inventory")
    parser.add_argument("--capture-only", action="store_true",
                        help="capture and stop — nothing is sent to any provider")
    parser.add_argument("--list-windows", action="store_true",
                        help="print the Computer-Use window inventory and exit")
    parser.add_argument("--prompt", default="",
                        help="override the instruction sent with the capture")
    _add_shared_image_args(parser)
    _add_limits(parser)


def _add_vision_verbs(sub: Any) -> Any:
    """Add the four ``vision`` verbs to a subparsers handle (one definition)."""
    p_describe = sub.add_parser(
        "describe",
        help="send an image (file path or URL) to the configured model and print the answer",
    )
    _describe_args(p_describe)
    p_describe.set_defaults(func=cmd_describe, command_path="vision.describe")

    p_ocr = sub.add_parser(
        "ocr", help="read the text in an image (the same call, read-the-text instruction)"
    )
    _ocr_args(p_ocr)
    p_ocr.set_defaults(func=cmd_ocr, command_path="vision.ocr")

    p_capabilities = sub.add_parser(
        "capabilities",
        help="measure whether the configured model accepts an image part (1x1 PNG probe)",
    )
    _capabilities_args(p_capabilities)
    p_capabilities.set_defaults(func=cmd_capabilities, command_path="vision.capabilities")

    p_screenshot = sub.add_parser(
        "screenshot",
        help="capture the screen (or a window) through Computer Use, then describe it",
    )
    _screenshot_args(p_screenshot)
    p_screenshot.set_defaults(func=cmd_screenshot, command_path="vision.screenshot")

    leaves = (p_describe, p_ocr, p_capabilities, p_screenshot)
    _add_output_flags(leaves)
    for parser in leaves:
        share_common_flags(parser)
    return sub


def build_vision_parser(subparsers: Any) -> Any:
    """Add the ``vision`` command group to a CLI's ``subparsers`` handle."""
    parser = subparsers.add_parser(
        "vision",
        help="put an image in front of the configured model (describe / ocr / capabilities)",
        description=(
            "Send one image to the configured OpenAI-compatible model and read the answer "
            "back. A local file's bytes are base64-encoded into the request and the output "
            "names the file, the endpoint they went to and the sha256; a remote URL is sent "
            "as a URL (the provider fetches it). `vision capabilities` measures — with one "
            "1x1 PNG — whether the model accepts images at all, next to what the model-name "
            "catalog expects. `vision screenshot` captures the screen (or one window) "
            "through the existing Computer-Use layer and describes that capture; with no "
            "Computer-Use screenshot bridge it exits 3 and names the missing capability. "
            "Every image call reports an estimate and the provider's own usage counters, "
            "because an image part is not free."
        ),
    )
    sub = parser.add_subparsers(dest="vision_command", required=True)
    _add_vision_verbs(sub)
    return parser


def register_cli(subparsers: Any) -> Any:
    """The CLI envelope contract: ``register_cli(subparsers) -> parser``.

    Idempotent — called twice on the same handle it returns the existing group
    instead of letting argparse raise ``conflicting subparser``. It also adds this
    group's four ``cli_contract.COMMANDS`` entries, so a wired-in group never ships
    without a contract.
    """
    existing = getattr(subparsers, "choices", {}).get("vision")
    if existing is not None:
        _install_entries()
        return existing
    parser = build_vision_parser(subparsers)
    _install_entries()
    return parser


def build_parser() -> argparse.ArgumentParser:
    """A standalone parser with just this group's verbs (tests / ``--help``)."""
    parser = argparse.ArgumentParser(prog="skaldir vision", description="vision / images")
    sub = parser.add_subparsers(dest="command", required=True)
    _add_vision_verbs(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    """``python -m skaldir.vision_cli …`` — returns the exit code, never raises.

    Unlike the group's ``main`` in the other ``*_cli`` modules this one goes through
    the shared envelope when the build has one, so the module runner emits exactly the
    document the product CLI does (``python -m skaldir.vision_cli describe x --json``);
    without a contract module it stays a bare dispatcher.
    """
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return EXIT_OK if not exc.code else EXIT_USAGE
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return EXIT_USAGE
    if _CONTRACT is not None and hasattr(_CONTRACT, "run_command"):
        return int(_CONTRACT.run_command(args, func))
    return int(func(args))


# --------------------------------------------------------------------------- #
# cli_contract entries                                                          #
# --------------------------------------------------------------------------- #


def contract_entries() -> dict[str, Any]:
    """The four ``cli_contract.COMMANDS`` entries for this group.

    A new ``skaldir`` command cannot ship without a contract entry; the group adds
    its own at register time, so nothing has to be pasted by hand (the CLI owner
    only wires ``register_cli(sub)``).
    """
    if _CONTRACT is None or not hasattr(_CONTRACT, "CommandSpec"):
        return {}
    spec = _CONTRACT.CommandSpec
    image_schema = {
        "type": "object",
        "properties": {
            "kind": {"type": "string"},
            "path": {"type": "string"},
            "url": {"type": "string"},
            "mime": {"type": "string"},
            "bytes": {"type": "integer"},
            "sha256": {"type": "string"},
            "width": {"type": "integer"},
            "height": {"type": "integer"},
            "sent_to": {"type": "string"},
        },
        "required": ["kind"],
    }
    answer_schema = {
        "type": "object",
        "properties": {
            "ok": {"type": "boolean"},
            "verb": {"type": "string"},
            "answer": {"type": "string"},
            "model": {"type": "string"},
            "endpoint": {"type": "string"},
            "sent_to": {"type": "string"},
            "disclosure": {"type": "string"},
            "latency_ms": {"type": "integer"},
            "finish_reason": {"type": "string"},
            "source": image_schema,
            "usage": {"type": "object"},
            "request": {"type": "object"},
            "cost": {"type": "object"},
            "cache_key": {"type": "string"},
        },
        "required": ["ok", "verb", "model"],
    }
    shared = (
        "the payload names the file, the endpoint its bytes went to (sent_to) and the "
        "sha256; the cost block carries the estimate and the provider's own usage counters"
    )
    return {
        "vision.describe": spec(
            "EXPAND",
            (
                {"ref": "src/skaldir/vision_service.py", "kind": "code",
                 "note": "resolve → guard → one image part → report"},
                {"ref": "src/skaldir/llm_provider.py", "kind": "code",
                 "note": "guard_image/image_part_from_bytes/vision_chat (the wire format)"},
            ),
            "send one image (file path or http(s) URL) to the configured model",
            exit_codes=(EXIT_OK, EXIT_USAGE, EXIT_BLOCKED, EXIT_NEEDS_CREDENTIALS,
                        EXIT_NOT_FOUND, EXIT_INTERNAL, EXIT_UNKNOWN),
            requires_credentials=True,
            data_schema=answer_schema,
            notes=f"exit 3 when the model refuses the image part or a guard/symlink rule "
                  f"refuses the file; exit 4 for a remote endpoint with no key (no request "
                  f"is sent); exit 5 for a missing file. {shared}",
        ),
        "vision.ocr": spec(
            "EXPAND",
            (
                {"ref": "src/skaldir/vision_service.py", "kind": "code",
                 "note": "DEFAULT_OCR_PROMPT — a prompt, not a second code path"},
                {"ref": "src/skaldir/llm_provider.py", "kind": "code",
                 "note": "the same vision_chat answer"},
            ),
            "read the text in an image (the describe call with an OCR instruction)",
            exit_codes=(EXIT_OK, EXIT_USAGE, EXIT_BLOCKED, EXIT_NEEDS_CREDENTIALS,
                        EXIT_NOT_FOUND, EXIT_INTERNAL, EXIT_UNKNOWN),
            requires_credentials=True,
            data_schema=answer_schema,
            notes="no OCR engine of our own: the answer is the model's reading, quoted "
                  "verbatim in `answer` (and repeated in `text`)",
        ),
        "vision.capabilities": spec(
            "EXPAND",
            (
                {"ref": "src/skaldir/llm_provider.py", "kind": "code",
                 "note": "vision_probe — one 1x1 PNG through vision_chat"},
                {"ref": "src/skaldir/llm_provider.py:vision_catalog", "kind": "code",
                 "note": "the model-name catalog, reported as an expectation only"},
            ),
            "measure whether the configured model accepts an image part",
            exit_codes=(EXIT_OK, EXIT_USAGE, EXIT_VERIFICATION_FAILED, EXIT_BLOCKED,
                        EXIT_NEEDS_CREDENTIALS, EXIT_INTERNAL),
            requires_credentials=True,
            data_schema={
                "type": "object",
                "properties": {
                    "ok": {"type": "boolean"},
                    "model": {"type": "string"},
                    "endpoint": {"type": "string"},
                    "accepts_images": {"type": ["boolean", "null"]},
                    "measured": {"type": "boolean"},
                    "agreement": {"type": "string"},
                    "catalog": {"type": "object"},
                    "probe": {"type": "object"},
                    "cost": {"type": "object"},
                },
                "required": ["ok", "model", "measured", "catalog", "probe"],
            },
            notes="exit 0 with accepts_images true/false is a real answer; "
                  "--no-probe exits 2 because nothing was measured then",
        ),
        "vision.screenshot": spec(
            "ACT",
            (
                {"ref": "src/skaldir/workflows.py:screenshot", "kind": "code",
                 "note": "the existing Computer-Use screenshot backend (import, X11/XWayland)"},
                {"ref": "src/skaldir/vision_service.py:capture_screen", "kind": "code",
                 "note": "the bridge: windows inventory + capture + missing_capability"},
            ),
            "capture the screen (or one window) through Computer Use, then describe it",
            exit_codes=(EXIT_OK, EXIT_USAGE, EXIT_BLOCKED, EXIT_NEEDS_CREDENTIALS,
                        EXIT_NOT_FOUND, EXIT_INTERNAL, EXIT_UNKNOWN),
            requires_credentials=True,
            data_schema={
                "type": "object",
                "properties": {
                    "ok": {"type": "boolean"},
                    "verb": {"type": "string"},
                    "capture": {"type": "object"},
                    "windows": {"type": "array"},
                    "answer": {"type": "string"},
                    "model": {"type": "string"},
                    "cost": {"type": "object"},
                },
                "required": ["ok", "verb", "capture"],
            },
            notes="with no Computer-Use screenshot bridge it exits 3 and names the missing "
                  "capability in `missing_capability` — never a substitute image; "
                  "--capture-only sends nothing to any provider",
        ),
    }


def _install_entries() -> list[str]:
    """Put this group's four ``COMMANDS`` entries in the table; returns the new paths.

    Re-resolves the contract only when the import did not find one (a build without
    ``cli_contract`` at import time, but with it later), so a group that is already
    wired keeps the handle its ``cli_contract.COMMANDS`` entries were installed into.
    """
    global _CONTRACT
    _CONTRACT = _CONTRACT or product_contract()
    return install_contract_entries(contract_entries(), _CONTRACT)


#: The public name the hand-over note and the tests call (one idempotent install).
register_contract_entries = _install_entries


__all__ = [
    "EXIT_BLOCKED",
    "EXIT_INTERNAL",
    "EXIT_NEEDS_CREDENTIALS",
    "EXIT_NOT_FOUND",
    "EXIT_OK",
    "EXIT_UNKNOWN",
    "EXIT_USAGE",
    "EXIT_VERIFICATION_FAILED",
    "build_parser",
    "build_vision_parser",
    "cmd_capabilities",
    "cmd_describe",
    "cmd_ocr",
    "cmd_screenshot",
    "contract_entries",
    "main",
    "register_cli",
    "register_contract_entries",
    "render_text",
]


if __name__ == "__main__":  # pragma: no cover — a standalone run, not a product path
    raise SystemExit(main())
