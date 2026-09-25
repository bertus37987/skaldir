"""``skaldir provider …`` — configure the LLM provider layer from the CLI.

The group is added by the product CLI through one call:

.. code-block:: python

    # src/skaldir/cli.py, inside build_parser(), right after
    #     sub = parser.add_subparsers(dest="command", required=False)
    from .provider_cli import register_cli

    register_cli(sub)

``register_cli`` is the whole contract: it takes the CLI's own ``subparsers``
handle, adds the ``provider`` group with its eight verbs and returns the parser.
Nothing here edits ``cli.py``.

Verbs
-----

===========================  ==================================================
``provider list``            configured providers, redacted (default marked)
``provider add``             name, kind, base_url, model, api_key_env NAME
``provider test``            one real call: latency, model echo, answer
``provider remove``          drop one provider (a removed default clears the
                             active projection — loudly)
``provider default``         which provider the runtime uses (``godsv.llm``)
``provider tiers``           the small/mid/main → model mapping (show or set)
``provider doctor``          what is missing for each configured provider
``provider catalog``         the verified multi-provider catalog: base URLs,
                             auth, streaming, structured output, vision — each
                             claim with its verification status and its evidence
===========================  ==================================================

Exit codes (the product's ``cli_contract`` vocabulary; the group adopts it when
that module is importable and never re-invents one)::

    0   ok
    1   usage          bad flag/name/URL — the input is not usable
    3   blocked        the provider cannot be called as configured (doctor exits 3
                       when a configured provider is unusable)
    4   needs creds    ``provider test`` on a remote endpoint whose ``api_key_env``
                       is unset in the environment (or a real 401/403)
    5   not found      no such configured provider
    6   conflict       the name is already configured (``add`` without ``--force``)
    7   internal       a store/write failure of our own
    8   unknown        the probe timed out (retryable)

A secret never reaches a disk, a stdout, or this module's messages: the
credential is only ever *named* (``api_key_env``), the probe answer is redacted
before it is reported, and the store refuses a literal key outright
(:mod:`skaldir.provider_config`).

Hand-over for the CLI owner — three lines of wiring and eight contracts
-----------------------------------------------------------------------

The three lines are the ``code-block`` above (``cli.py``). The group is also
responsible for its eight ``cli_contract.COMMANDS`` entries; that snippet (paste
next to the other entries in ``src/skaldir/cli_contract.py``) is
:data:`CONTRACTS_SNIPPET` at the bottom of this module — :data:`CONTRACT_PATHS`
names the eight paths, and ``tests/test_provider_cli.py`` skips with the names of
the missing ones until they are pasted in (``tests/test_cli_contract_dump.py`` is
the CLI's own invariant for the same table).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import provider_catalog as catalog
from . import provider_config as pc
from . import structured as st
from .cli_shared import dump, product_contract
from .llm import TIERS

_CONTRACT = product_contract()

EXIT_OK = int(getattr(_CONTRACT, "EXIT_OK", 0))
EXIT_USAGE = int(getattr(_CONTRACT, "EXIT_USAGE", 1))
EXIT_VERIFICATION_FAILED = int(getattr(_CONTRACT, "EXIT_VERIFICATION_FAILED", 2))
EXIT_BLOCKED = int(getattr(_CONTRACT, "EXIT_BLOCKED", 3))
EXIT_NEEDS_CREDENTIALS = int(getattr(_CONTRACT, "EXIT_NEEDS_CREDENTIALS", 4))
EXIT_NOT_FOUND = int(getattr(_CONTRACT, "EXIT_NOT_FOUND", 5))
EXIT_CONFLICT = int(getattr(_CONTRACT, "EXIT_CONFLICT", 6))
EXIT_INTERNAL = int(getattr(_CONTRACT, "EXIT_INTERNAL", 7))
EXIT_UNKNOWN = int(getattr(_CONTRACT, "EXIT_UNKNOWN", 8))

#: the eight leaves this group adds — the CLI owner needs one ``COMMANDS`` entry each
CONTRACT_PATHS = (
    "provider.add",
    "provider.catalog",
    "provider.default",
    "provider.doctor",
    "provider.list",
    "provider.remove",
    "provider.test",
    "provider.tiers",
)

# --------------------------------------------------------------------------- #
# output plumbing (envelope when wired, one JSON document when standalone)     #
# --------------------------------------------------------------------------- #


def _json_mode(args: argparse.Namespace) -> bool:
    if bool(getattr(args, "json", False)):
        return True
    fmt = str(getattr(args, "format", "") or "").strip().lower()
    return fmt in ("json", "jsonl")


def _driven() -> bool:
    """True while the product driver (``cli_contract.run_command``) runs the verb."""
    if _CONTRACT is None:
        return False
    try:
        return _CONTRACT.current() is not None
    except Exception:  # noqa: BLE001 — never let plumbing break a verb
        return False


def _report(
    payload: Any, evidence: tuple[dict, ...] = (), next_actions: tuple[dict, ...] = ()
) -> None:
    """Hand the payload (and its evidence) to the shared envelope, when there is one."""
    if _CONTRACT is None or not _driven():
        return
    try:
        _CONTRACT.set_data(payload)
        for item in evidence:
            _CONTRACT.add_evidence(
                str(item.get("ref", "")), str(item.get("kind", "code")), str(item.get("note", ""))
            )
        for item in next_actions:
            _CONTRACT.next_action(str(item.get("command", "")), str(item.get("reason", "")))
    except Exception:  # noqa: BLE001 — an envelope problem must not break the verb
        return


def _emit(
    payload: Any,
    human: str,
    *,
    args: argparse.Namespace,
    evidence: tuple[dict, ...] = (),
    next_actions: tuple[dict, ...] = (),
) -> None:
    """One JSON document in JSON mode, human lines otherwise.

    Wired into ``skaldir``, the product driver captures stdout around the
    handler and moves it to stderr, so the human view stays diagnostics and
    stdout carries exactly the one envelope.
    """
    driven = _driven()
    _report(payload, evidence, next_actions)
    if _json_mode(args):
        if driven:
            if human:
                print(human)
        else:
            dump(payload)
    elif human:
        print(human)


def _verdict(code: int, message: str, *, hint: str = "", details: dict | None = None) -> int:
    """Record the verdict (envelope) and print it on stderr. Never a secret in it."""
    if _CONTRACT is not None and _driven():
        return int(_CONTRACT.fail(code, message, hint=hint, details=details or {}))
    print(f"provider: {message}", file=sys.stderr)
    if hint:
        print(f"hint: {hint}", file=sys.stderr)
    return int(code)


def _evidence_for(path: str, note: str) -> tuple[dict, ...]:
    return (
        {"ref": path, "kind": "file", "note": note},
        {
            "ref": "src/skaldir/provider_config.py",
            "kind": "code",
            "note": "the store that wrote it",
        },
    )


def _config_arg(args: argparse.Namespace) -> str | None:
    value = getattr(args, "config", None)
    return str(value) if value else None


def _map_store_error(exc: Exception) -> tuple[str, int]:
    """The store's refusals mapped onto the shared exit codes (never a secret in the text)."""
    for kind, code in (
        (pc.ProviderNotFound, EXIT_NOT_FOUND),
        (pc.ProviderExists, EXIT_CONFLICT),
        (pc.ProviderSecretRefused, EXIT_BLOCKED),
        (pc.ProviderNeedsCredential, EXIT_NEEDS_CREDENTIALS),
        (pc.ProviderUnusable, EXIT_BLOCKED),
        (pc.ProviderInvalid, EXIT_USAGE),
        (pc.ProviderWriteError, EXIT_INTERNAL),
    ):
        if isinstance(exc, kind):
            return str(exc), code
    return str(exc), EXIT_INTERNAL


def _call(func, *args: Any, **kwargs: Any) -> tuple[Any, int]:
    """Run a store operation, mapping its refusals onto the shared exit codes."""
    try:
        return func(*args, **kwargs), EXIT_OK
    except pc.ProviderStoreError as exc:
        return _map_store_error(exc)


def _call_probe(func, *args: Any, **kwargs: Any) -> tuple[Any, int]:
    """``_call`` plus the provider's own failures (the retry policy lives there)."""
    try:
        return func(*args, **kwargs), EXIT_OK
    except pc.ProviderStoreError as exc:
        return _map_store_error(exc)
    except Exception as exc:  # noqa: BLE001 — mapped below, never a bare traceback
        return _map_provider_error(exc)


def _map_provider_error(exc: Exception) -> tuple[str, int]:
    from . import llm_provider as lp

    if isinstance(exc, lp.ProviderAuthError):
        return str(exc), EXIT_NEEDS_CREDENTIALS
    if isinstance(exc, lp.ProviderTimeoutError):
        return f"{exc} (retryable)", EXIT_UNKNOWN
    if isinstance(exc, lp.ProviderRateLimitError):
        return f"{exc} (retryable)", EXIT_BLOCKED
    if isinstance(exc, lp.LLMProviderError):
        return str(exc), EXIT_BLOCKED
    if isinstance(exc, lp.ProviderResponseError):
        return str(exc), EXIT_BLOCKED
    if isinstance(exc, lp.ProviderConfigError):
        return str(exc), EXIT_BLOCKED
    if isinstance(exc, lp.LLMError):
        return str(exc), EXIT_INTERNAL
    return f"{type(exc).__name__}: {exc}", EXIT_INTERNAL


# --------------------------------------------------------------------------- #
# human views                                                                  #
# --------------------------------------------------------------------------- #


def _human_row(row: dict[str, Any]) -> str:
    marker = "*" if row.get("default") else " "
    tiers = ",".join(f"{tier}={row['tiers'][tier]}" for tier in TIERS if row["tiers"].get(tier))
    credential = row.get("credential", "unset")
    if credential == "set" and row.get("api_key_env"):
        credential = f"set ({row['api_key_env']})"
    elif credential == "unset" and row.get("api_key_env"):
        credential = f"unset ({row['api_key_env']})"
    parts = [
        f"{marker} {row['name']:<16}",
        f"{row['kind']:<17}",
        f"{row['base_url'] or '<no base_url>':<34}",
        f"model={row['model'] or '<unset>'}",
        f"credential={credential}",
    ]
    if tiers:
        parts.append(f"tiers={tiers}")
    parts.append("ready" if row.get("ready") else "NOT READY")
    return "  ".join(parts)


def _human_list(payload: dict[str, Any]) -> str:
    lines = [
        f"provider list: {payload['count']} configured"
        f" (default: {payload['default'] or 'none'}) · {payload['config_file']}"
    ]
    for row in payload["providers"]:
        lines.append(_human_row(row))
    for warning in payload.get("warnings", []):
        lines.append(f"  warning: {warning}")
    if not payload["providers"]:
        lines.append(f"  nothing configured — {pc.EXAMPLE_ADD}")
    if payload.get("hand_written_block"):
        lines.append("  note: godsv.llm is hand-written (no registry entries)")
    return "\n".join(lines)


def _human_mutation(payload: dict[str, Any], verb: str) -> str:
    backup = payload.get("backup") or "no previous file"
    lines = [
        f"provider {verb}: {payload['name']} → {payload['path']} (backup: {backup})",
        "  " + _human_row(payload.get("provider") or payload.get("removed") or {}).strip(),
    ]
    if payload.get("default"):
        lines.append(f"  default: {payload['default']}")
    if payload.get("next_export"):
        lines.append(
            f"  next: {payload['next_export']}   then: "
            f"skaldir provider test {payload['name']}"
        )
    for warning in payload.get("warnings", []):
        lines.append(f"  warning: {warning}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# catalog views — every claim shown with its verification status                #
# --------------------------------------------------------------------------- #

_CATALOG_COLUMNS = (
    ("id", 16),
    ("verification", 14),
    ("stream", 16),
    ("json_schema", 18),
    ("vision", 17),
)


def _cell(entry: dict[str, Any], feature: str, width: int) -> str:
    support = (entry.get("features") or {}).get(feature) or {}
    shown = f"{support.get('value', '?')}[{str(support.get('status', '?'))[:4]}]"
    return f"{shown:<{width}}"


def _human_catalog_table(payload: dict[str, Any]) -> str:
    counts = payload["verification_counts"]
    probes = payload["probe_counts"]
    header = "".join(f"{name:<{width}}" for name, width in _CATALOG_COLUMNS)
    lines = [
        f"provider catalog: {payload['count']} entries · "
        f"verified={counts.get('verified', 0)} documented={counts.get('documented', 0)} "
        f"unverified={counts.get('unverified', 0)}",
        f"  live probes {payload['probe_date']}: {probes.get('run', 0)} run · "
        f"{probes.get('partial', 0)} partial · {probes.get('not_run', 0)} not run",
        f"  {header}primary endpoint",
    ]
    for entry in payload["results"]:
        endpoints = entry.get("endpoints") or [{}]
        lines.append(
            f"  {entry['id']:<16}{entry['verification']:<14}"
            + _cell(entry, "streaming", 16)
            + _cell(entry, "native_json_schema", 18)
            + _cell(entry, "vision", 17)
            + str(endpoints[0].get("base_url", "<none>"))
        )
    for entry in payload["results"]:
        if entry.get("probe") == "not_run" and entry.get("probe_reason"):
            lines.append(f"  not probed: {entry['id']} — {entry['probe_reason']}")
    footer = payload.get("disclaimer", "")
    if footer:
        lines.append(f"  {footer}")
    return "\n".join(lines)


def _human_catalog_entry(entry: dict[str, Any], probes: dict[str, Any]) -> str:
    reason = f" ({entry['probe_reason']})" if entry["probe_reason"] else ""
    lines = [
        f"{entry['label']} ({entry['id']}) — {entry['verification']} · "
        f"probe: {entry['probe']}{reason}",
        f"  docs: {entry['docs'] or '<none>'}",
    ]
    for endpoint in entry["endpoints"]:
        shape = ("(openai-compatible)" if endpoint["openai_compatible"]
                 else "(NOT openai-compatible)")
        lines.append(f"  endpoint: {endpoint['label']} [{endpoint['status']}]")
        lines.append(f"    base_url: {endpoint['base_url']}")
        lines.append(f"    chat POST: {endpoint['url']}")
        lines.append(f"    kind: {endpoint['kind']} {shape}")
        for header in endpoint["auth_header"]:
            lines.append(f"    auth: {header}")
        lines.append(
            f"    key env: {', '.join(endpoint['api_key_env']) or '<none — no credential>'}"
        )
        if endpoint.get("reference"):
            lines.append(
                f"    source: {endpoint['reference']}"
                f"{' (fetched)' if endpoint.get('fetched') else ' (not fetched)'}"
            )
        if endpoint.get("note"):
            lines.append(f"    note: {endpoint['note']}")
    for name, support in entry["features"].items():
        lines.append(f"  {name}: {support['value']} [{support['status']}]")
        if support.get("reference"):
            lines.append(
                f"    source: {support['reference']}"
                f"{' (fetched)' if support.get('fetched') else ''}"
            )
        if support.get("note"):
            lines.append(f"    note: {support['note']}")
    if entry["model_patterns"]:
        lines.append(f"  model ids: {', '.join(entry['model_patterns'])}")
    if entry["default_model"]:
        lines.append(f"  default model: {entry['default_model']}")
    for quirk in entry["quirks"]:
        lines.append(f"  quirk: {quirk}")
    lines.append(
        f"  licence: {entry['licence'] or '<not recorded>'}"
        f"{' (fetched)' if entry.get('licence_fetched') else ''}"
    )
    if entry.get("licence_reference"):
        lines.append(f"    source: {entry['licence_reference']}")
    if entry.get("note"):
        lines.append(f"  note: {entry['note']}")
    for step in probes.get("steps", []):
        if step["provider"] == entry["id"]:
            lines.append(
                f"  probe {step['step']}: status={step['status']} ok={step['ok']} — "
                f"{str(step['evidence'])[:200]}"
            )
    return "\n".join(lines)


def _human_probe_log(probes: dict[str, Any]) -> str:
    lines = [
        f"live probe log ({probes['date']}): {probes['measured_steps']} measured steps · "
        f"{probes['unmeasured_steps']} steps that produced no measurement"
    ]
    for step in probes["steps"]:
        lines.append(
            f"  {step['provider']:<12} {step['step']:<62} status={step['status']} "
            f"ok={step['ok']}"
        )
        lines.append(f"      {str(step['evidence'])[:220]}")
    for url in sorted(probes.get("fetched_docs", {})):
        lines.append(f"  fetched: {url}")
    return "\n".join(lines)


def cmd_catalog(args: argparse.Namespace) -> int:
    """``provider catalog`` — the verified breadth catalog, not a promise list.

    Every entry is printed with its verification status; ``--probes`` adds the
    live evidence, ``--entry`` narrows to one provider with its full detail. The
    exit code is honest in both directions: an unknown ``--entry`` or an unknown
    ``--verification`` is exit 1/5, never a silently empty success.
    """
    entry_id = str(getattr(args, "entry", "") or "").strip()
    verification = str(getattr(args, "verification", "") or "").strip()
    if verification and verification not in catalog.VERIFICATION_ORDER:
        return _verdict(
            EXIT_USAGE,
            f"unknown --verification {verification!r}",
            hint="one of: " + ", ".join(catalog.VERIFICATION_ORDER),
        )
    try:
        payload = catalog.provider_catalog_report(
            entry_id=entry_id, verification=verification, include_chain=not entry_id
        )
    except KeyError as exc:
        return _verdict(EXIT_NOT_FOUND, str(exc).strip("'\""), hint="--entry takes a known id")
    payload["probes"] = catalog.probe_report()

    probes = payload["probes"]
    if bool(getattr(args, "text", False)):
        # an explicit --text wins over an inherited --json/--format default
        args.json = False
        args.format = "text"
    human = (
        _human_catalog_entry(payload["results"][0], probes)
        if entry_id and payload["results"]
        else _human_catalog_table(payload)
    )
    if bool(getattr(args, "probes", False)):
        human = "\n".join([human, "", _human_probe_log(probes)])
    if not payload["drift"]["agree"]:
        for conflict in payload["drift"]["conflicts"]:
            _verdict_warning(
                f"the chain catalog and the registry disagree about {conflict['id']}."
                f"{conflict['field']}: {conflict['chain']!r} vs {conflict['registry']!r}"
            )
    _emit(
        payload,
        human,
        args=args,
        evidence=(
            {
                "ref": "src/skaldir/provider_registry_data.py",
                "kind": "code",
                "note": (
                    f"{payload['count']} entries; verified claims come from the probe log dated "
                    f"{payload['probe_date']}"
                ),
            },
            {
                "ref": "src/skaldir/provider_catalog.py",
                "kind": "verification",
                "note": (
                    f"chain catalog drift: {len(payload['drift']['conflicts'])} conflict(s) "
                    f"over {len(payload['drift']['compared'])} shared id(s)"
                ),
            },
        ),
    )
    return EXIT_OK


def cmd_list(args: argparse.Namespace) -> int:
    payload, code = _call(pc.store_report, None, path=_config_arg(args))
    if code != EXIT_OK:
        return _verdict(code, str(payload), hint="fix the config file, then re-run")
    _emit(
        payload,
        _human_list(payload),
        args=args,
        evidence=_evidence_for(
            payload["config_file"], f"{payload['count']} configured provider(s)"
        ),
    )
    if payload.get("drift"):
        for line in payload["drift"]:
            _verdict_warning(line)
    return EXIT_OK


def _verdict_warning(text: str) -> None:
    if _CONTRACT is not None and _driven():
        try:
            _CONTRACT.warn(text)
            return
        except Exception:  # noqa: BLE001
            pass
    print(f"provider: warning: {text}", file=sys.stderr)


def cmd_add(args: argparse.Namespace) -> int:
    tiers: dict[str, str] = {}
    for tier in TIERS:
        value = getattr(args, f"tier_{tier}", None)
        if value:
            tiers[tier] = str(value)
    make_default: bool | None = None
    if bool(getattr(args, "make_default", False)):
        make_default = True
    elif bool(getattr(args, "no_default", False)):
        make_default = False
    payload, code = _call(
        pc.add_provider,
        args.name,
        base_url=args.base_url,
        model=args.model,
        api_key_env=args.api_key_env or "",
        kind=args.kind,
        tiers=tiers,
        timeout_s=args.timeout,
        max_tokens=args.max_tokens,
        make_default=make_default,
        replace=bool(getattr(args, "force", False)),
        path=_config_arg(args),
    )
    if code != EXIT_OK:
        return _verdict(code, str(payload), hint="see `skaldir provider add --help`")
    next_actions = ()
    if payload.get("next_export"):
        next_actions = (
            {
                "command": f"skaldir provider test {payload['name']}",
                "reason": "export the credential named by --api-key-env first",
            },
        )
    _emit(
        payload,
        _human_mutation(payload, payload["action"]),
        args=args,
        evidence=_evidence_for(
            payload["path"], f"provider {payload['name']} ({payload['action']})"
        ),
        next_actions=next_actions,
    )
    return EXIT_OK


def cmd_test(args: argparse.Namespace) -> int:
    if bool(getattr(args, "structured", False)):
        return _cmd_test_structured(args)
    payload, code = _call_probe(
        pc.probe_provider,
        args.name,
        path=_config_arg(args),
        tier=args.tier,
        prompt=args.prompt or pc.PROBE_PROMPT,
        timeout_s=args.timeout,
    )
    if code != EXIT_OK:
        hint = ""
        if code == EXIT_NEEDS_CREDENTIALS:
            hint = "export the named variable — the credential is never stored or read from a file"
        elif code == EXIT_BLOCKED:
            hint = "run `skaldir provider doctor` for what is missing"
        elif code == EXIT_UNKNOWN:
            hint = "the endpoint did not answer in time — retry, or raise --timeout"
        return _verdict(code, str(payload), hint=hint)
    human = (
        f"provider test: {payload['name']} ok in {payload['latency_ms']} ms · "
        f"tier={payload['tier']} model={payload['model']} "
        f"echo={payload['model_echo'] or '<none>'} · {payload['endpoint']}\n"
        f"  answer ({payload['answer_chars']} chars): {payload['answer_preview']}"
    )
    _emit(
        payload,
        human,
        args=args,
        evidence=(
            {"ref": payload["endpoint"], "kind": "run",
             "note": f"{payload['client']} answered in {payload['latency_ms']} ms"},
            {"ref": payload["config_file"], "kind": "file", "note": f"provider {payload['name']}"},
        ),
    )
    return EXIT_OK


def _load_schema(path: Any) -> dict[str, Any] | None:
    """Read a JSON Schema object from ``--schema-file`` (``None`` = the probe schema)."""
    text = str(path or "").strip()
    if not text:
        return None
    raw = Path(text).expanduser().read_text(encoding="utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("the schema file must contain a JSON object")
    return data


def _cmd_test_structured(args: argparse.Namespace) -> int:
    """``provider test --structured`` — one schema-constrained call, with evidence.

    Reports which mode answered (native ``json_schema`` / ``json_object`` /
    a prompted JSON answer validated locally), the ``response_format`` that
    actually left the machine, the raw answer, and the validation backend that
    judged it. A native refusal is a **warning** plus the provider's own text —
    never a silent downgrade. An answer that does not satisfy the schema exits 2
    with the raw text and the validation error attached.
    """
    try:
        schema = _load_schema(getattr(args, "schema_file", None))
    except (OSError, ValueError) as exc:
        return _verdict(
            EXIT_USAGE,
            f"--schema-file is not usable: {exc}",
            hint="pass a JSON Schema object in a .json file",
        )
    payload, code = _call_probe(
        st.probe_structured,
        args.name,
        path=_config_arg(args),
        tier=args.tier,
        prompt=args.prompt or st.PROBE_PROMPT,
        schema=schema,
        mode=getattr(args, "mode", None) or st.MODE_AUTO,
        timeout_s=args.timeout,
    )
    if code != EXIT_OK:
        hint = ""
        if code == EXIT_NEEDS_CREDENTIALS:
            hint = "export the named variable — the credential is never stored or read from a file"
        elif code == EXIT_BLOCKED:
            hint = "run `skaldir provider doctor` for what is missing"
        elif code == EXIT_UNKNOWN:
            hint = "the endpoint did not answer in time — retry, or raise --timeout"
        return _verdict(code, str(payload), hint=hint)

    try:
        steps = catalog.auto_chain(
            require=(catalog.FEATURE_STRUCTURED,), config_path=_config_arg(args)
        )
        payload["fallback_chain"] = catalog.chain_report(steps)
    except Exception as exc:  # noqa: BLE001 — the chain is context, not the verdict
        payload["fallback_chain"] = {"error": f"{type(exc).__name__}: {exc}"}

    sent = json.dumps(payload["request"]["response_format"], sort_keys=True)
    lines = [
        f"provider test --structured: {payload['name']} "
        f"{'ok' if payload['ok'] else 'NOT VALID'} in {payload['latency_ms']} ms · "
        f"tier={payload['tier']} model={payload['model']} · {payload['endpoint']}",
        f"  schema={payload['schema_name']} mode_used={payload['mode_used'] or '<none>'} "
        f"native_json_schema={payload['native_json_schema']}",
        f"  response_format sent: {sent}",
        f"  raw answer ({payload['answer']['raw_chars']} chars): {payload['answer']['raw']}",
        f"  validation: {payload['validation']['backend'] or '<none>'} "
        f"ok={payload['validation']['ok']}",
    ]
    if payload["native_reason"]:
        lines.append(f"  native json_schema was refused: {payload['native_reason']}")
    for item in payload["mode_trail"]:
        if item["status"] != "answered":
            lines.append(
                f"  mode {item['mode']}: {item['status']} "
                f"({item['reason']}: {str(item['detail'])[:120]})"
            )
    if not payload["ok"]:
        detail = payload["validation"]["errors"] or [payload["error"]]
        lines.append(f"  validation error: {'; '.join(str(part) for part in detail)}")
    chain = payload.get("fallback_chain") or {}
    if chain.get("ready"):
        lines.append(f"  fallback chain: {' -> '.join(chain['ready'])}")
    for item in chain.get("skipped", []):
        lines.append(
            f"      {item['id']}: skipped ({item['reason']}: {str(item['detail'])[:100]})"
        )
    _emit(
        payload,
        "\n".join(lines),
        args=args,
        evidence=(
            {
                "ref": payload["endpoint"],
                "kind": "run",
                "note": (
                    f"response_format={sent} answered by mode="
                    f"{payload['mode_used'] or '<none>'} in {payload['latency_ms']} ms"
                ),
            },
            {
                "ref": payload["config_file"],
                "kind": "file",
                "note": f"provider {payload['name']}",
            },
            {
                "ref": f"schema:{payload['schema_name']}",
                "kind": "verification",
                "note": (
                    f"validated with {payload['validation']['backend'] or '<none>'}: "
                    f"ok={payload['validation']['ok']}"
                ),
            },
        ),
    )
    for item in payload["mode_trail"]:
        if item["status"] == "skipped":
            _verdict_warning(
                f"mode {item['mode']} is not supported by this endpoint "
                f"({str(item['detail'])[:120]})"
            )
    if not payload["ok"]:
        detail = payload["validation"]["errors"] or [payload["error"]]
        return _verdict(
            EXIT_VERIFICATION_FAILED,
            f"the endpoint answered, but not with a value that satisfies "
            f"{payload['schema_name']}: {'; '.join(str(part) for part in detail)}",
            hint="the raw answer is in the payload; --mode json_object|prompted may help",
        )
    return EXIT_OK


def cmd_remove(args: argparse.Namespace) -> int:
    payload, code = _call(
        pc.remove_provider, args.name, path=_config_arg(args)
    )
    if code != EXIT_OK:
        return _verdict(code, str(payload), hint="run `skaldir provider list`")
    _emit(
        payload,
        _human_mutation(payload, "remove"),
        args=args,
        evidence=_evidence_for(payload["path"], f"provider {payload['name']} removed"),
    )
    return EXIT_OK


def cmd_default(args: argparse.Namespace) -> int:
    payload, code = _call(pc.set_default, args.name, path=_config_arg(args))
    if code != EXIT_OK:
        return _verdict(code, str(payload), hint="run `skaldir provider list`")
    _emit(
        payload,
        _human_mutation(payload, "default"),
        args=args,
        evidence=_evidence_for(payload["path"], f"active provider {payload['name']}"),
    )
    return EXIT_OK


def cmd_tiers(args: argparse.Namespace) -> int:
    requested = {
        tier: getattr(args, tier)
        for tier in TIERS
        if getattr(args, tier, None) is not None
    }
    if requested:
        payload, code = _call(
            pc.set_tiers,
            args.name,
            tiers=requested,
            replace=bool(getattr(args, "replace", False)),
            path=_config_arg(args),
        )
        if code != EXIT_OK:
            return _verdict(code, str(payload), hint="run `skaldir provider list`")
        human = (
            f"provider tiers: {payload['name']} → {payload['tiers'] or '<none>'} "
            f"({payload['path']})"
        )
        _emit(
            payload,
            human,
            args=args,
            evidence=_evidence_for(payload["path"], f"tiers of {payload['name']}") ,
        )
        return EXIT_OK

    payload, code = _call(pc.store_report, args.name, path=_config_arg(args))
    if code != EXIT_OK:
        return _verdict(code, str(payload), hint="run `skaldir provider list`")
    row = payload.get("provider")
    if row is None:
        return _verdict(EXIT_NOT_FOUND, "no provider configured — run `skaldir provider list`")
    tiers = row["tiers"] or {}
    human = "\n".join(
        [
            f"provider tiers: {row['name']} (model fallback {row['model'] or '<unset>'})",
            *(f"  {tier}: {tiers.get(tier) or '<provider model>'}" for tier in TIERS),
            f"  set with: skaldir provider tiers {row['name']} --small <model> --main <model>",
        ]
    )
    _emit(
        payload,
        human,
        args=args,
        evidence=_evidence_for(payload["config_file"], f"tier mapping of {row['name']}"),
    )
    return EXIT_OK


def cmd_doctor(args: argparse.Namespace) -> int:
    payload, code = _call(pc.doctor, path=_config_arg(args))
    if code != EXIT_OK:
        return _verdict(code, str(payload), hint="fix the config file, then re-run")
    lines = [
        f"provider doctor: {payload['count']} configured · ok={payload['ok']} · "
        f"{payload['config_file']}"
    ]
    for row in payload["providers"]:
        lines.append(_human_row(row))
        for item in row["missing"]:
            lines.append(f"      missing: {item}")
    for warning in payload["warnings"]:
        lines.append(f"  warning: {warning}")
    if not payload["providers"]:
        lines.append(f"  nothing configured — {pc.EXAMPLE_ADD}")
    next_actions = tuple(payload["next_actions"])
    _emit(
        payload,
        "\n".join(lines),
        args=args,
        evidence=_evidence_for(
            payload["config_file"], f"{len(payload['usable'])} of {payload['count']} usable"
        ),
        next_actions=next_actions,
    )
    if payload["count"] and not payload["ok"]:
        return _verdict(
            EXIT_BLOCKED,
            f"{len(payload['broken'])} of {payload['count']} configured providers are not usable: "
            + ", ".join(payload["broken"]),
            hint="`skaldir provider doctor --json` lists what is missing per provider",
        )
    return EXIT_OK


# --------------------------------------------------------------------------- #
# parser                                                                       #
# --------------------------------------------------------------------------- #

_CONFIG_HELP = (
    "the runtime config file (default: $SKALDIR_LLM_CONFIG, else <runtime>/config.yaml — "
    "$SKALDIR_HOME, $HERMES_HOME/godsv, ~/.hermes/godsv); the same file the provider layer reads"
)


def _add_config(parser: argparse.ArgumentParser, *, inherited: bool) -> None:
    kwargs: dict[str, Any] = {"metavar": "PATH", "help": _CONFIG_HELP}
    if inherited:
        kwargs["default"] = argparse.SUPPRESS  # the group-level --config survives
    parser.add_argument("--config", **kwargs)


def _add_common(parser: argparse.ArgumentParser, *, inherited: bool = False) -> None:
    suppress: dict[str, Any] = {"default": argparse.SUPPRESS} if inherited else {}
    parser.add_argument(
        "--json", action="store_true", **suppress, help="print one JSON payload on stdout"
    )
    parser.add_argument(
        "--format", choices=("text", "json", "jsonl"), **suppress,
        help="output format (text|json|jsonl)",
    )


def _add_group_verbs(sub: Any) -> None:
    p_list = sub.add_parser("list", help="configured providers, redacted (default marked)")
    p_list.set_defaults(func=cmd_list)

    p_catalog = sub.add_parser(
        "catalog",
        help="the verified multi-provider catalog (base URLs, auth, streaming, structured "
        "output, vision) with each claim's verification status",
    )
    p_catalog.add_argument(
        "--entry", default="", metavar="ID",
        help=f"one provider in full detail (known: {', '.join(catalog.REGISTRY_ORDER)})",
    )
    p_catalog.add_argument(
        "--verification", default=None, metavar="STATUS",
        help="only entries at this status: " + " | ".join(catalog.VERIFICATION_ORDER),
    )
    p_catalog.add_argument(
        "--probes", action="store_true",
        help="also print the live probe log (the evidence behind every verified claim)",
    )
    p_catalog.add_argument(
        "--text", action="store_true", help="human text even when a JSON default is inherited"
    )
    p_catalog.set_defaults(func=cmd_catalog)

    p_add = sub.add_parser(
        "add", help="add (or replace) a provider — names a key, never stores one"
    )
    p_add.add_argument("name", help="provider name (lowercase slug)")
    p_add.add_argument("--base-url", dest="base_url", required=True,
                       help="endpoint root, e.g. https://<provider-host>/v1")
    p_add.add_argument("--model", required=True, help="default model for this provider")
    p_add.add_argument("--api-key-env", dest="api_key_env", default="",
                       help="the NAME of the environment variable holding the key (never the key)")
    p_add.add_argument("--kind", default=pc.DEFAULT_KIND, choices=list(pc.PROVIDER_KINDS),
                       help=f"provider kind (default {pc.DEFAULT_KIND})")
    for tier in TIERS:
        p_add.add_argument(f"--tier-{tier}", dest=f"tier_{tier}", default=None,
                           help=f"model for the '{tier}' tier")
    p_add.add_argument("--timeout", type=float, default=None, help="per-request timeout in seconds")
    p_add.add_argument("--max-tokens", dest="max_tokens", type=int, default=None,
                       help="max_tokens sent per request")
    p_add.add_argument("--default", dest="make_default", action="store_true",
                       help="make it the active provider (default: the first one)")
    p_add.add_argument("--no-default", dest="no_default", action="store_true",
                       help="add it without touching the active provider")
    p_add.add_argument(
        "--force", action="store_true", help="replace an existing provider of that name"
    )
    p_add.set_defaults(func=cmd_add)

    p_test = sub.add_parser("test", help="one real call: latency, model echo, answer")
    p_test.add_argument("name", nargs="?", default=None, help="provider (default: the default one)")
    p_test.add_argument("--tier", default=pc.PROBE_TIER, choices=list(TIERS),
                        help=f"tier to probe (default {pc.PROBE_TIER})")
    p_test.add_argument(
        "--prompt", default=None, help="prompt to send (default: a fixed 'pong' probe)"
    )
    p_test.add_argument(
        "--timeout", type=float, default=None, help="per-request timeout in seconds"
    )
    p_test.add_argument(
        "--structured",
        action="store_true",
        help="ask for a schema-constrained answer and report which mode answered "
        "(native json_schema / json_object / a prompted answer validated locally)",
    )
    p_test.add_argument(
        "--mode", default=None, choices=list(st.MODES),
        help="which structured mode to use with --structured (default: auto)",
    )
    p_test.add_argument(
        "--schema-file", dest="schema_file", default=None,
        help="a JSON Schema file for --structured (default: the built-in probe schema)",
    )
    p_test.set_defaults(func=cmd_test)

    p_rm = sub.add_parser(
        "remove", help="remove a provider (a removed default clears the projection)"
    )
    p_rm.add_argument("name", help="provider name")
    p_rm.set_defaults(func=cmd_remove)

    p_default = sub.add_parser("default", help="which provider the runtime uses")
    p_default.add_argument("name", help="provider name")
    p_default.set_defaults(func=cmd_default)

    p_tiers = sub.add_parser("tiers", help="the small/mid/main → model mapping (show or set)")
    p_tiers.add_argument(
        "name", nargs="?", default=None, help="provider (default: the default one)"
    )
    p_tiers.add_argument("--small", default=None, help="model for the 'small' tier")
    p_tiers.add_argument("--mid", default=None, help="model for the 'mid' tier")
    p_tiers.add_argument("--main", default=None, help="model for the 'main' tier")
    p_tiers.add_argument("--replace", action="store_true", help="replace the whole mapping")
    p_tiers.set_defaults(func=cmd_tiers)

    p_doctor = sub.add_parser("doctor", help="what is missing for each configured provider")
    p_doctor.set_defaults(func=cmd_doctor)


def build_provider_parser(subparsers: Any) -> Any:
    """Add the ``provider`` group to an existing ``subparsers`` handle."""
    parser = subparsers.add_parser(
        "provider",
        help="configure LLM providers (add · list · test · remove · default · tiers · doctor)",
        description=(
            "Configure the OpenAI-compatible provider layer. Configuration is written to the "
            "runtime config file (atomic, with a backup) and mirrored into the godsv.llm block "
            "the provider layer reads. Credentials are only ever NAMED (api_key_env): a literal "
            "key is refused, never stored, never printed."
        ),
    )
    _add_config(parser, inherited=False)
    _add_common(parser, inherited=False)
    sub = parser.add_subparsers(dest="provider_command")
    _add_group_verbs(sub)
    for child in _children(parser):
        _add_config(child, inherited=True)  # a group-level --config/--json survives
        _add_common(child, inherited=True)
    return parser


def _children(parser: argparse.ArgumentParser) -> list[argparse.ArgumentParser]:
    for action in parser._actions:  # noqa: SLF001 — argparse has no public API for this
        if isinstance(action, argparse._SubParsersAction):  # noqa: SLF001
            return list(action.choices.values())
    return []


def register_cli(subparsers: Any) -> Any:
    """The CLI envelope contract: ``register_cli(subparsers) -> parser``."""
    return build_provider_parser(subparsers)


def build_parser() -> argparse.ArgumentParser:
    """A standalone parser with just this group's verbs (``python -m`` / tests).

    Same verbs as :func:`build_provider_parser` minus the wrapping ``provider``
    word, so ``main(["list", "--config", path])`` and the product's
    ``skaldir provider list --config path`` run exactly the same handler.
    """
    parser = argparse.ArgumentParser(
        prog="skaldir provider", description="provider configuration"
    )
    _add_config(parser, inherited=False)
    _add_common(parser, inherited=False)
    sub = parser.add_subparsers(dest="command")
    _add_group_verbs(sub)
    for child in _children(parser):
        _add_config(child, inherited=True)  # a leading --config/--json survives
        _add_common(child, inherited=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    """``skaldir provider …`` — returns the exit code, never raises for a request."""
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse already chose a code (--help exits 0)
        return EXIT_OK if not exc.code else EXIT_USAGE
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return EXIT_USAGE
    return int(func(args))


#: The ``cli_contract.COMMANDS`` entries this group hands over (see the module
#: docstring). The CLI owner pastes the block; a test here fails while one of the
#: eight paths is missing from ``cli_contract.COMMANDS``.
CONTRACTS_SNIPPET = '''
    # -- the provider configuration surface (kernel) ------------------------- #
    # `register_cli(sub)` is called in `build_parser()` right after the root
    # `add_subparsers(...)`; the group owns its eight entries below.
    "provider.list": _C("kernel", (_ev("src/skaldir/provider_config.py", note="the store"),
                                   _ev("src/skaldir/llm_provider.py",
                                       note="the same file/keys the runtime resolves")),
                        "configured providers, redacted (default marked)", exit_codes=(0, 1, 3, 7),
                        data_schema={"type": "object", "properties": {
                            "config_file": {"type": "string"}, "default": {"type": "string"},
                            "count": {"type": "integer"}, "providers": {"type": "array"},
                            "active": {"type": "object"}, "drift": {"type": "array"}},
                            "required": ["config_file", "providers", "count"]},
                        notes="never prints a credential: api_key_env is a NAME, the state is "
                              "set/unset/not-required"),
    "provider.catalog": _C("kernel", (_ev("src/skaldir/provider_registry_data.py",
                                         note="the breadth registry + the live probe log"),
                                    _ev("src/skaldir/provider_catalog.py",
                                        note="drift check against the chain catalog")),
                           "the verified multi-provider catalog: base URLs, auth, streaming, "
                           "structured output, vision — each claim with its status",
                           exit_codes=(0, 1, 3, 5, 7),
                           data_schema={"type": "object", "properties": {
                               "count": {"type": "integer"}, "order": {"type": "array"},
                               "verification_counts": {"type": "object"},
                               "probe_counts": {"type": "object"},
                               "probe_date": {"type": "string"},
                               "results": {"type": "array"}, "features": {"type": "array"},
                               "drift": {"type": "object"}, "probes": {"type": "object"},
                               "chain": {"type": "object"}},
                               "required": ["count", "results", "verification_counts"]},
                           notes="verified = a live probe we ran; documented = an official page "
                                 "(the fetched flag says whether we retrieved it); unverified = "
                                 "nobody proved it. --entry names one provider, --verification "
                                 "filters, --probes prints the evidence. An unknown --entry "
                                 "exits 5, an unknown --verification exits 1"),
    "provider.add": _C("kernel",
                       (_ev("src/skaldir/provider_config.py", note="atomic write + backup"),
                                  _ev("tests/test_provider_cli.py", "test",
                                      "the secret-refusal and stub-endpoint proofs")),
                       "add (or replace) one provider and project it into godsv.llm",
                       exit_codes=(0, 1, 3, 5, 6, 7), mutates=True,
                       data_schema={"type": "object", "properties": {
                           "action": {"type": "string"}, "name": {"type": "string"},
                           "default": {"type": "string"}, "path": {"type": "string"},
                           "backup": {"type": ["string", "null"]}, "provider": {"type": "object"}},
                           "required": ["action", "name", "path"]},
                       notes="a literal api_key/token/secret is refused (exit 3); "
                             "--api-key-env takes a variable NAME only; the previous "
                             "file is backed up"),
    "provider.test": _C("kernel", (_ev("src/skaldir/llm_provider.py",
                                       note="OpenAICompatibleLLM, the real client"),
                                   _ev("src/skaldir/system_one_policy.py",
                                       note="the retry/timeout policy the call follows"),
                                   _ev("src/skaldir/structured.py",
                                       note="--structured: the schema-constrained call, its "
                                            "validation and the mode trail"),
                                   _ev("src/skaldir/provider_catalog.py",
                                       note="--structured: the fallback chain it reports")),
                        "one real call: latency, model echo, answer (redacted); "
                        "--structured asks for a schema-constrained answer",
                        exit_codes=(0, 1, 2, 3, 4, 5, 7, 8),
                        requires_credentials=True,
                        data_schema={"type": "object", "properties": {
                            "name": {"type": "string"}, "endpoint": {"type": "string"},
                            "model": {"type": "string"}, "model_echo": {"type": "string"},
                            "latency_ms": {"type": "integer"}, "answer_preview": {"type": "string"},
                            "credential": {"type": "string"}, "client": {"type": "string"},
                            "mode_used": {"type": "string"},
                            "native_json_schema": {"type": ["boolean", "null"]},
                            "validation": {"type": "object"}, "fallback_chain": {"type": "object"}},
                            "required": ["name", "endpoint", "model", "latency_ms"]},
                        notes="exit 4 when a remote provider has no credential — no request is "
                              "attempted; 401/403 also exit 4; a timeout exits 8; with "
                              "--structured an answer that does not satisfy the schema exits 2 "
                              "and carries the raw text + the validation error (never coerced)"),
    "provider.remove": _C("kernel", (_ev("src/skaldir/provider_config.py", note="registry write"),
                                     _ev("tests/test_provider_cli.py", "test",
                                         "the removal cases")),
                          "remove one provider (a removed default clears the projection)",
                          exit_codes=(0, 1, 5, 7), mutates=True,
                          data_schema={"type": "object", "properties": {
                              "action": {"type": "string"}, "name": {"type": "string"},
                              "remaining": {"type": "array"},
                              "projection_cleared": {"type": "boolean"}},
                              "required": ["action", "name", "remaining"]},
                          notes="never guesses a new default: the runtime stays unconfigured until "
                                "`provider default` names one"),
    "provider.default": _C("kernel", (_ev("src/skaldir/provider_config.py",
                                           note="projection write"),
                                      _ev("src/skaldir/llm_provider.py",
                                          note="the reader of godsv.llm")),
                           "which provider the runtime uses (mirrored into godsv.llm)",
                           exit_codes=(0, 1, 5, 7), mutates=True,
                           data_schema={"type": "object", "properties": {
                               "action": {"type": "string"}, "name": {"type": "string"},
                               "projection": {"type": "object"}}, "required": ["action", "name"]},
                           notes="the projection is what `skaldir` runs with; the secret is "
                                 "still resolved from the environment"),
    "provider.tiers": _C("kernel", (_ev("src/skaldir/provider_config.py", note="tier mapping"),
                                    _ev("src/skaldir/llm.py", note="TIERS: small/mid/main")),
                         "the small/mid/main → model mapping (show, or set with --small/--main)",
                         exit_codes=(0, 1, 5, 7), mutates=True,
                         data_schema={"type": "object", "properties": {
                             "name": {"type": "string"}, "tiers": {"type": "object"},
                             "requested": {"type": "object"}}, "required": ["name"]},
                         notes="without --small/--mid/--main it only reads; an empty value clears "
                               "that tier and the provider model stays the fallback"),
    "provider.doctor": _C("kernel", (_ev("src/skaldir/provider_config.py",
                                         note="offline diagnosis"),
                                     _ev("tests/test_provider_cli.py", "test", "the doctor cases")),
                          "what is missing for each configured provider (offline)",
                          exit_codes=(0, 1, 3, 7),
                          data_schema={"type": "object", "properties": {
                              "config_file": {"type": "string"}, "default": {"type": "string"},
                              "count": {"type": "integer"}, "ok": {"type": "boolean"},
                              "providers": {"type": "array"}, "usable": {"type": "array"},
                              "broken": {"type": "array"}, "next_actions": {"type": "array"}},
                              "required": ["config_file", "providers", "ok"]},
                          notes="exit 3 when a CONFIGURED provider is unusable; nothing configured "
                                "exits 0 with ok=false and the command to run; no request is made"),
'''

__all__ = [
    "CONTRACTS_SNIPPET",
    "CONTRACT_PATHS",
    "EXIT_BLOCKED",
    "EXIT_CONFLICT",
    "EXIT_INTERNAL",
    "EXIT_NEEDS_CREDENTIALS",
    "EXIT_NOT_FOUND",
    "EXIT_OK",
    "EXIT_UNKNOWN",
    "EXIT_USAGE",
    "build_parser",
    "build_provider_parser",
    "cmd_add",
    "cmd_catalog",
    "cmd_default",
    "cmd_doctor",
    "cmd_list",
    "cmd_remove",
    "cmd_test",
    "cmd_tiers",
    "main",
    "register_cli",
]
