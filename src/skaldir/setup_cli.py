"""``skaldir setup`` — the two-key first-run flow, as a CLI verb.

    skaldir setup                 # pick → ask (hidden) → validate live → write → verify
    skaldir setup --check         # re-validate only; exit 0 only if both keys answer
    skaldir setup --state         # the honest current state; writes nothing
    skaldir setup --providers     # the pickable providers: name, note, base URL

Two mandatory keys and no more:

1. the **TypeSafe/Jev provider** (``--jev-provider`` · ``--jev-base-url`` ·
   ``--jev-model`` · ``--jev-api-key-env``) — the endpoint the always-armed
   decision layer evaluates through;
2. **one LLM provider** (``--llm-provider`` · ``--llm-base-url`` · ``--llm-model`` ·
   ``--llm-api-key-env``, or just ``--llm-provider deepseek``) — the general model.

**The key never enters the config, and never reaches argv.** The interactive flow
asks for the provider first (every row already carries its base URL, so accepting
one is a single Enter), then for the key with a **hidden prompt**: no echo, no
shell history, and the value is written to the ``.env`` beside the config with
mode ``0600`` — the config names the *variable* (``api_key_env``), never the value.
A key that is already in the environment or in that file is reused and reported,
not asked for again.

**Headless is the same path.** Every question has a flag and a variable
(``SKALDIR_SETUP_*``); a key for a script comes from ``--llm-key-stdin`` /
``--jev-key-stdin`` or ``SKALDIR_SETUP_LLM_API_KEY`` / ``SKALDIR_SETUP_JEV_API_KEY``.
With a non-TTY stdin, or with ``--yes``, the flow never prompts and a missing field
is reported as the exact flag to pass instead of blocking on input.

The module follows the CLI envelope contract
(:func:`skaldir.cli_shared.install_contract_entries`): one JSON document per run,
the shared exit codes, and a ``COMMANDS`` entry handed to ``cli_contract``.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import model_picker as mp
from . import setup_flow as sf
from .cli_shared import (
    dump,
    envelope_mode,
    install_contract_entries,
    product_contract,
    text_mode,
)

#: The product contract's exit codes, with the fallbacks other groups carry.
_CONTRACT = product_contract()
EXIT_OK = int(getattr(_CONTRACT, "EXIT_OK", sf.EXIT_OK))
EXIT_USAGE = int(getattr(_CONTRACT, "EXIT_USAGE", sf.EXIT_USAGE))
EXIT_VERIFICATION_FAILED = int(
    getattr(_CONTRACT, "EXIT_VERIFICATION_FAILED", sf.EXIT_VERIFICATION_FAILED)
)
EXIT_BLOCKED = int(getattr(_CONTRACT, "EXIT_BLOCKED", sf.EXIT_BLOCKED))
EXIT_NEEDS_CREDENTIALS = int(
    getattr(_CONTRACT, "EXIT_NEEDS_CREDENTIALS", sf.EXIT_NEEDS_CREDENTIALS)
)
EXIT_NOT_FOUND = int(getattr(_CONTRACT, "EXIT_NOT_FOUND", sf.EXIT_NOT_FOUND))
EXIT_CONFLICT = int(getattr(_CONTRACT, "EXIT_CONFLICT", 6))
EXIT_INTERNAL = int(getattr(_CONTRACT, "EXIT_INTERNAL", sf.EXIT_INTERNAL))
EXIT_TIMEOUT = int(getattr(_CONTRACT, "EXIT_TIMEOUT", sf.EXIT_TIMEOUT))

#: Our own module-level contract constant (the group owns its exit vocabulary).

_ROLE_FLAGS: dict[str, dict[str, str]] = {
    sf.ROLE_TYPESAFE: {
        "base_url": "--jev-base-url",
        "model": "--jev-model",
        "api_key_env": "--jev-api-key-env",
    },
    sf.ROLE_LLM: {
        "base_url": "--llm-base-url",
        "model": "--llm-model",
        "api_key_env": "--llm-api-key-env",
        "catalog": "--llm-catalog",
    },
}


def _report(payload: dict[str, Any]) -> bool:
    """Hand the payload to the shared envelope when the product driver is carrying it."""
    if _CONTRACT is None or not envelope_mode():
        return False
    try:
        _CONTRACT.set_data(payload)
    except Exception:  # noqa: BLE001 — an envelope problem must not break the verb
        return False
    return True


def _emit(payload: dict[str, Any], args: argparse.Namespace, *, ok: bool, code: int) -> int:
    """One payload — JSON on stdout, human lines when text was asked for.

    Wired into ``skaldir``, the product driver carries the envelope: the payload is
    handed over and stdout stays empty of it, exactly like every other group. Run
    standalone (``python -m skaldir.setup_cli``) this prints the JSON itself.

    ``--format text`` is the one case that renders on **stdout** even when the run
    is a refusal: the report is the answer, and the exit code carries the verdict.
    """
    driven = _report(payload)
    if text_mode(args):
        print(_human(payload))
        if payload.get("verdict"):
            print(f"verdict: {payload['verdict']}")
        if payload.get("problem"):
            print(f"problem: {payload['problem']}")
        if payload.get("next_command"):
            print(f"next:    {payload['next_command']}")
        return code
    if driven:
        return code
    if not ok and _CONTRACT is not None and envelope_mode() and hasattr(_CONTRACT, "fail"):
        try:
            return int(
                _CONTRACT.fail(
                    code,
                    str(payload.get("problem") or payload.get("verdict") or "setup failed"),
                    hint=str(payload.get("next_command") or ""),
                    details={},
                )
            )
        except Exception:  # noqa: BLE001 — fall through to the plain report
            pass
    dump(payload)
    return code


def _human(payload: dict[str, Any]) -> str:
    """Black-and-white text: what was measured, in the order it happened.

    Handles both payload shapes: the flow's (``roles`` carries the validation
    result) and ``--state``'s (``roles`` carries the honest configured/not state).
    """
    if payload.get("action") in ("providers", "catalog") and payload.get("providers"):
        return _human_providers(payload)
    lines: list[str] = []
    action = str(payload.get("action") or "setup")
    lines.append(f"skaldir setup ({action})")
    lines.append("=" * 58)
    if payload.get("config_file"):
        lines.append(f"config file: {payload['config_file']}")

    # the detected state: the payload itself for --state, the `state` block otherwise
    state = payload.get("state")
    if not isinstance(state, dict):
        state = payload if action == "state" else None
    if isinstance(state, dict):
        lines.append("")
        lines.append("-- detected ------------------------------------------------")
        lines.append(f"runtime dir:  {state.get('runtime_dir')} ({state.get('runtime_reason')})")
        cfg = state.get("config") or {}
        lines.append(
            f"config:       {'present' if cfg.get('exists') else 'absent'}"
            f"{'' if cfg.get('readable', True) else ' — UNREADABLE'}"
        )
        providers = cfg.get("providers") or []
        lines.append(f"providers:    {len(providers)} configured")
        tools = state.get("tools") or {}
        lines.append(
            f"tools:        {tools.get('count', 0)} registered"
            f"{'' if tools.get('exists') else ' (no tools.db yet)'}"
        )
        docker = state.get("docker") or {}
        lines.append(f"docker:       {'available' if docker.get('available') else 'not on PATH'}")
        settings = state.get("settings") or {}
        lines.append(
            f"system-one:   {'on' if settings.get('enabled') else 'off'}"
            f"{'' if settings.get('exists') else ' (no settings.yaml)'}"
        )

    roles = payload.get("roles")
    if isinstance(roles, dict):
        lines.append("")
        lines.append("-- keys ---------------------------------------------------")
        for role in sf.MANDATORY_ROLES:
            item = roles.get(role) or {}
            label = str(item.get("label") or sf.ROLE_LABEL[role])
            # the flow/check shapes carry a measurement; --state's does not
            measured = any(
                key in item for key in ("measure", "model_echo", "measurements")
            )
            if not measured:
                _human_state_role(lines, item, label)
                continue
            mark = "ok  " if item.get("ok") else "FAIL"
            lines.append(f"[{mark}] {label}")
            if item.get("present") is False:
                lines.append(f"         {item.get('problem')}")
                continue
            measurement = item.get("measure") or item
            if measurement.get("model_echo"):
                streaming = measurement.get("streaming") or {}
                structured = measurement.get("structured") or {}
                lines.append(
                    f"         model echo {measurement.get('model_echo')}"
                    f" · {measurement.get('latency_ms')} ms"
                    f" · streaming {'yes' if streaming.get('supported') else 'no'}"
                    f" · structured {structured.get('mode_used') or 'n/a'}"
                )
            if item.get("problem"):
                lines.append(f"         {item['problem']}")

    verify = payload.get("verify")
    if isinstance(verify, dict):
        lines.append("")
        lines.append("-- verified with two real calls ---------------------------")
        completion = verify.get("llm_completion") or {}
        decision = verify.get("jev_decision") or {}
        lines.append(
            f"completion:   {'ok' if completion.get('ok') else 'FAILED'}"
            f" ({completion.get('model_echo') or completion.get('problem') or ''})"
        )
        lines.append(
            f"jev decision: {'ok' if decision.get('ok') else 'FAILED'}"
            f" ({decision.get('status') or decision.get('problem') or ''}"
            f" · {decision.get('answer_kind') or ''}"
            f" · {decision.get('latency_ms')} ms)"
        )

    write = payload.get("write")
    if isinstance(write, dict) and write.get("written"):
        lines.append("")
        lines.append("-- written ------------------------------------------------")
        lines.append(f"path:         {write.get('path')} ({write.get('bytes')} bytes)")
        lines.append(f"backup:       {write.get('backup') or 'none (new file)'}")
        lines.append(f"secret scan:  {write.get('secret_scan', 'clean')}")

    _human_credentials(lines, payload)
    _human_next(lines, payload)

    missing = payload.get("missing")
    if isinstance(missing, list) and missing:
        lines.append("")
        lines.append("-- missing ------------------------------------------------")
        lines.extend(f"  {item}" for item in missing)
    return "\n".join(lines)


def _human_providers(payload: dict[str, Any]) -> str:
    """The picker as text: number, name, note, and the URL that would be pre-filled."""
    lines = [f"skaldir setup ({payload.get('action')})", "=" * 58]
    for number, row in enumerate(payload.get("providers") or [], start=1):
        choice = sf.ProviderChoice(
            id=str(row.get("id")),
            label=str(row.get("label")),
            note=str(row.get("note") or ""),
            base_url=str(row.get("base_url") or ""),
            api_key_env=str(row.get("api_key_env") or ""),
            local=bool(row.get("local")),
            speakable=bool(row.get("speakable", True)),
        )
        lines.append(choice.prompt_line(number))
        if row.get("api_key_env"):
            lines.append(f"      key variable: {row['api_key_env']}")
    extras = payload.get("extras") or []
    if extras:
        names = ", ".join(str(row.get("id")) for row in extras)
        lines.append("")
        lines.append(f"also by id (not in the 16 above): {names}")
    lines.append("")
    lines.append(f"default: {payload.get('default')} — pass --llm-provider <id>, Enter accepts it")
    return "\n".join(lines)


def _human_credentials(lines: list[str], payload: dict[str, Any]) -> None:
    """The credential file and the key's origin — **names only**, never a value."""
    cred = payload.get("credentials_file")
    if isinstance(cred, dict) and cred.get("path"):
        lines.append("")
        lines.append("-- credentials --------------------------------------------")
        state = "written" if cred.get("written") else "read"
        lines.append(
            f"file:         {cred.get('path')}"
            f" (mode {cred.get('mode') or 'not created'}, {state})"
        )
        if cred.get("names"):
            lines.append(f"names:        {', '.join(str(name) for name in cred['names'])}")
        if cred.get("reason"):
            lines.append(f"note:         {cred['reason']}")
        if cred.get("duplicates"):
            lines.append(f"deduplicated: {', '.join(str(name) for name in cred['duplicates'])}")
    sources = payload.get("key_sources")
    if isinstance(sources, dict) and sources:
        for role in sf.MANDATORY_ROLES:
            item = sources.get(role) or {}
            if not item:
                continue
            lines.append(_key_source_line(item))


def _key_source_line(item: Mapping[str, Any]) -> str:
    """One key row: the variable, and **where** the value came from — no value."""
    name = str(item.get("name") or "unnamed")
    kind = str(item.get("kind") or "")
    where = str(item.get("source") or "")
    if not where:
        return f"key {name:<22} not provided"
    if kind == "environment":
        return f"key {name:<22} reused from the environment ({where})"
    if kind == "env-file":
        return f"key {name:<22} reused from {where}"
    if kind == "typed":
        return f"key {name:<22} typed at the hidden prompt"
    return f"key {name:<22} from {where}"


def _human_next(lines: list[str], payload: dict[str, Any]) -> None:
    """``what happens next`` — the commands, each one checked against the real tree."""
    steps = payload.get("next_steps")
    if not isinstance(steps, dict):
        return
    commands = [item for item in steps.get("commands") or [] if item.get("command")]
    if not commands:
        return
    lines.append("")
    lines.append(f"what happens next (checked against {steps.get('verified_against')})")
    for item in commands:
        lines.append(f"  {item['command']}")


def _human_state_role(lines: list[str], item: dict[str, Any], label: str) -> None:
    """One ``--state`` key row: what is configured, or that nothing is."""
    entry = item.get("entry")
    if not isinstance(entry, dict) or not entry:
        lines.append(f"[FAIL] {label}")
        lines.append("         not configured")
        for note in (item.get("problem"), item.get("error")):
            if note:
                lines.append(f"         {note}")
        return
    lines.append(f"[{'ok  ' if entry.get('ready') else 'FAIL'}] {label}")
    lines.append(
        f"         {entry.get('base_url')} · {entry.get('model')}"
        f" · key {entry.get('api_key_env') or 'not required'}"
        f" ({entry.get('credential')})"
    )
    for miss in entry.get("missing") or []:
        lines.append(f"         {miss}")


# --------------------------------------------------------------------------- #
# the interactive half (asks, masks, and never echoes a value)                  #
# --------------------------------------------------------------------------- #


def _say(text: str = "") -> None:
    """Prompts and notes go to **stderr**: stdout carries the one JSON document."""
    print(text, file=sys.stderr)


def _read_line(prompt: str) -> str:
    """One visible answer, with the prompt on **stderr** so stdout stays the one document.

    A closed or interrupted stdin is an empty answer. ``input()`` is asked for the line only:
    the echo a person sees comes from the terminal itself, so routing the prompt to stderr
    costs nothing visually and keeps ``--json`` consumers of stdout clean even on the plain
    path (where there is no screen control to draw a prompt with).
    """
    print(prompt, end="", file=sys.stderr, flush=True)
    try:
        return str(input())
    except (EOFError, KeyboardInterrupt):
        return ""
    except Exception:  # noqa: BLE001 — a stdin that cannot be read is not an answer
        return ""


def _read_secret(prompt: str) -> str:
    """**Masked** input: no echo, no shell history, never printed, never logged.

    ``getpass`` writes the prompt to stderr and reads from the controlling
    terminal, so the value never touches stdout and never lands in a log that
    captures stdout. An empty answer means "keep what is already there".
    """
    try:
        return str(getpass.getpass(prompt, stream=sys.stderr))
    except (EOFError, KeyboardInterrupt):
        return ""
    except Exception:  # noqa: BLE001 — no controlling terminal is not a value
        return ""


def _ask(question: str, *, default: str = "") -> str:
    """One question whose answer is already filled in — Enter accepts it."""
    hint = f" [{default}]" if default else ""
    answer = _read_line(f"  {question}{hint}: ").strip()
    return answer or default


def _ask_required(role: str, field: str, *, question: str, default: str = "") -> str:
    """Ask until there is an answer: a default, or the caller's own words."""
    for _ in range(3):
        answer = _ask(question, default=default)
        if answer:
            return answer
        _say(f"  {sf.ROLE_LABEL[role]}: {field.replace('_', ' ')} is required")
    raise sf.SetupRefused(f"{sf.ROLE_LABEL[role]}: no {field.replace('_', ' ')} was given")


def _resolve_provider_answer(answer: str, choices: tuple[sf.ProviderChoice, ...]) -> str:
    """A row number, or an id — anything else is not an answer."""
    text = str(answer or "").strip().lower()
    if not text:
        return ""
    if text.isdigit():
        number = int(text)
        if 1 <= number <= len(choices):
            return choices[number - 1].id
        return ""
    for choice in choices:
        if choice.id == text:
            return choice.id
    return ""


def _choose_provider(
    role: str,
    *,
    choices: tuple[sf.ProviderChoice, ...],
    default_id: str,
    current: str = "",
    env: Mapping[str, str] | None = None,
) -> str:
    """The provider question: the catalog as a **selection list**, base URL and all.

    Drawn by the picker's own engine (``model_picker.choose``), so this question and
    ``skaldir model``'s are the same list, the same keys and the same resolution — one code
    path, two verbs. Every row carries the endpoint the flow would pre-fill, so accepting a
    row *is* the endpoint answer; where the terminal cannot take a list (a pipe, ``TERM=dumb``,
    ``NO_COLOR``, a captured stream) the engine prints those rows as lines and reads a row
    number or an id, which is what this verb has always accepted.

    ``role`` is passed to the rows as well: the decision layer's question shows the measured
    and local endpoints first and marks the rest as meant for the general model, exactly as
    ``skaldir model --role typesafe`` does — a role is answered the same way in both verbs.
    """
    rows = mp.provider_rows(choices, current=current, role=role)
    _say("")
    _say(sf.ROLE_LABEL[role])
    answer = mp.choose(
        sf.PROMPTS["provider"],
        rows,
        default_value=default_id,
        allow_text=False,
        interactive=True,
        env=env,
        stream_out=sys.stderr,
        line_reader=_read_line,
    )
    picked = _resolve_provider_answer(answer, choices)
    if not picked:
        raise sf.SetupRefused(
            "no provider was chosen — the selection was cancelled or answered nothing; "
            "pass --llm-provider / --jev-provider for a headless run"
        )
    return picked


def _choose_model(
    role: str,
    provider: str,
    *,
    supplied: str,
    current: str = "",
    env: Mapping[str, str] | None = None,
    config: str | None = None,
    base_url: str = "",
    env_file: str = "",
) -> str:
    """The model question: the catalog's own list as a **selection list**, typed id allowed.

    ``""`` means "this endpoint names no model in the catalog" (a local server names its own):
    the caller then asks for the id as a line, exactly as it did before there was a list, and
    nothing is invented on the server's behalf.
    """
    rows = mp.live_model_rows(provider, role=role, env=env, config=config,
                              base_url=base_url, env_file=env_file,
                              discover=bool(sys.stdin and sys.stdin.isatty()))
    if not rows:
        return ""
    answer = mp.choose(
        sf.PROMPTS["model"],
        rows,
        default_value=rows[0].value,
        allow_text=True,
        interactive=True,
        env=env,
        stream_out=sys.stderr,
        line_reader=_read_line,
    )
    if not answer:
        raise sf.SetupRefused(
            f"{sf.ROLE_LABEL[role]}: no model was chosen (the selection was cancelled)"
        )
    return answer


@dataclass
class RoleAnswers:
    """What one role's questions produced — a key, a value, and where it came from."""

    key: sf.KeySpec | None = None
    value: str = ""
    source: dict[str, Any] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    provider: str = ""
    asked: list[str] = field(default_factory=list)


def _flag_values(role: str, args: argparse.Namespace) -> dict[str, str]:
    word = _flag_key(role)
    return {
        "provider": str(getattr(args, f"{word}_provider", "") or "").strip(),
        "base_url": str(getattr(args, f"{word}_base_url", "") or "").strip(),
        "model": str(getattr(args, f"{word}_model", "") or "").strip(),
        "api_key_env": str(getattr(args, f"{word}_api_key_env", "") or "").strip(),
    }


def _collect(
    role: str,
    args: argparse.Namespace,
    *,
    interactive: bool,
    env: Mapping[str, str],
    env_file: sf.EnvFile,
    fallback_provider: str = "",
) -> RoleAnswers:
    """One role's key: flags and variables first, a question only for what is left.

    In a headless run nothing is asked and every empty field is reported as the
    exact flag to pass (:func:`skaldir.setup_flow.missing_fields`).
    """
    word = _flag_key(role)
    values = _flag_values(role, args)
    catalog_flag = str(getattr(args, f"{word}_catalog", "") or "").strip()
    timeout_s = getattr(args, "timeout", None)
    picked = values["provider"] or catalog_flag

    def build(**changes: str) -> sf.KeySpec | None:
        merged = dict(values)
        merged.update({key: value for key, value in changes.items() if value})
        try:
            return sf.key_from_env(
                role,
                provider=merged["provider"] or picked,
                base_url=merged["base_url"],
                model=merged["model"],
                api_key_env=merged["api_key_env"],
                catalog_id=catalog_flag,
                timeout_s=timeout_s,
                env=env,
            )
        except sf.SetupRefused:
            return None

    answers = RoleAnswers()
    key = build()
    # the endpoint this role ends up with: the flag when there is one, else what the questions
    # settle on (the provider row's URL, or the override typed at the endpoint prompt)
    base_url = values["base_url"]
    if key is None and interactive:
        answers.asked.append("provider")
        # the role's OWN configured provider first: a re-run must not answer Enter by
        # replacing a configured decision layer with the general model's endpoint
        configured = sf.configured_provider(
            role, path=str(getattr(args, "config", "") or "") or None, env=env
        )
        default_id = (
            picked or configured or fallback_provider or sf.default_provider(role, env=env)
        )
        choices = sf.provider_choices()
        chosen = _choose_provider(
            role,
            choices=choices,
            default_id=default_id,
            current=configured,
            env=env,
        )
        picked = chosen
        supplied = sf.provider_defaults(chosen)
        base_url = values["base_url"] or supplied["base_url"]
        model = values["model"] or supplied["model"]
        key_name = values["api_key_env"] or supplied["api_key_env"]
        if not values["base_url"]:
            answers.asked.append("base_url")
            # the row already answered the endpoint: this line is the *override*, said so in
            # one sentence, and Enter keeps exactly what the catalog supplies
            _say(
                f"  endpoint: {base_url or 'not documented by the catalog'}"
                f" — from the {chosen} row; Enter keeps it"
            )
            base_url = _ask_required(
                role, "base_url", question=sf.PROMPTS["base_url"], default=base_url
            )
        if not values["model"]:
            answers.asked.append("model")
            listed = _choose_model(
                role, chosen, supplied=model, env=env, base_url=base_url,
                config=str(getattr(args, "config", "") or "") or None,
                env_file=str(env_file.path),
            )
            # the catalog's own list when it names models, a line for the id when it does not
            model = listed or _ask_required(
                role, "model", question=sf.PROMPTS["model"], default=model
            )
        # a local endpoint needs no credential, and the catalog already names the
        # variable when the server does want one — so that question is not asked
        if not values["api_key_env"] and not sf.pc.endpoint_is_local(base_url):
            answers.asked.append("api_key_env")
            key_name = _ask_required(
                role, "api_key_env", question=sf.PROMPTS["api_key_env"], default=key_name
            )
        key = build(
            provider=chosen, base_url=base_url, model=model, api_key_env=key_name
        )
    answers.key = key
    answers.provider = (
        (key.catalog_id if key and key.catalog_id else picked)
        or (values["provider"] or fallback_provider)
    )
    answers.missing = sf.missing_fields(
        role,
        values=values,
        provider_id=answers.provider or picked,
        env=env,
    )
    if key is not None:
        answers.value, answers.source = _key_value(
            role,
            sf.credential_name(key, role),
            args,
            interactive=interactive,
            env=env,
            env_file=env_file,
            base_url=base_url,
        )
        if answers.value or answers.source:
            answers.asked.append("api_key")
    return answers


def _key_value(
    role: str,
    name: str,
    args: argparse.Namespace,
    *,
    interactive: bool,
    env: Mapping[str, str],
    env_file: sf.EnvFile,
    base_url: str = "",
) -> tuple[str, dict[str, Any]]:
    """The credential: reused when it is already somewhere, hidden when it is not.

    Returns ``(value, where)``. ``value`` is empty when nothing new has to be
    written (the same value is already in the credential file), which is how a
    re-run stays a no-op instead of rewriting the file it just read. ``where`` is
    the provenance row the report carries — the variable name, or the file, or the
    hidden prompt — **never** the value.

    ``base_url`` is the endpoint the *question* settled on. It is passed in because in the
    interactive flow the endpoint is an **answer**, not a flag: reading it back off ``args``
    would say "remote" about the local row the user just picked, and the key question would
    stop telling them that a loopback endpoint needs no credential.
    """
    word = _flag_key(role)
    from_var = sf.key_value_from_env(role, env=env)
    if from_var:
        return from_var, {"source": sf.KEY_VALUE_ENV[role][0], "kind": "environment"}
    if bool(getattr(args, f"{word}_key_stdin", False)):
        try:
            return sf.key_value_from_stream(), {"source": "stdin", "kind": "stdin"}
        except sf.SetupRefused as exc:
            _say(f"  {exc}")
            return "", {}
    where = sf.key_available(role, name, env=env, env_file=env_file)
    available = bool(where.get("available"))
    in_environment = where.get("kind") == "environment" and bool(name)
    reuse = {
        "source": str(where.get("source") or ""),
        "kind": str(where.get("kind") or ""),
    }
    if not interactive:
        # headless: reuse what exists, never invent a value, never block on input —
        # and a key that only lives in the environment is made durable in the file
        if in_environment:
            return str(env.get(where["source"]) or ""), reuse
        return "", reuse if available else {}
    local = sf.pc.endpoint_is_local(
        str(base_url or "").strip()
        or str(getattr(args, f"{word}_base_url", "") or "")
        or _url_for_role(role, args)
    )
    prompt = f"  {sf.PROMPTS['api_key']} for {name or 'this endpoint'} (hidden)"
    if available:
        # Layer 1: K / S / R before the hidden prompt (Enter there confirms a typed key)
        from . import model_picker as mp

        action = mp.ask_existing_key(where, name=name, say=_say, read=_read_line)
        if action == "keep":
            if in_environment:
                return str(env.get(where["source"]) or ""), reuse
            return "", reuse
        if action == "skip":
            return "", {}
        prompt += " — Enter confirms the key you typed"
    elif local:
        prompt += " — not required for a local endpoint, Enter skips it"
    else:
        prompt += " — Enter confirms the key you typed"
    typed = str(_read_secret(prompt + ": ") or "").strip()
    if typed:
        try:
            value = sf.refuse_key_value(typed, name=name or "the key")
        except sf.SetupRefused as exc:
            _say(f"  {exc}")
            return "", {}
        return value, {"source": "prompt (hidden)", "kind": "typed"}
    if available:
        if in_environment:
            # a key that only lives in the shell is copied into the file, so one
            # file serves every later run — and the report says so
            return str(env.get(where["source"]) or ""), reuse
        return "", reuse
    if local:
        return "", {}
    _say("  no key was entered — the endpoint will be validated without one")
    return "", {}


def _url_for_role(role: str, args: argparse.Namespace) -> str:
    """The endpoint a role is configured with, from the flags, for a local check."""
    word = _flag_key(role)
    given = str(getattr(args, f"{word}_base_url", "") or "").strip()
    if given:
        return given
    picked = str(getattr(args, f"{word}_provider", "") or "").strip() or _catalog_flag(
        role, args
    )
    if not picked:
        return ""
    try:
        return sf.provider_defaults(picked)["base_url"]
    except sf.SetupRefused:
        return ""


def _catalog_flag(role: str, args: argparse.Namespace) -> str:
    return str(getattr(args, f"{_flag_key(role)}_catalog", "") or "").strip()


def _flag_key(role: str) -> str:
    return "jev" if role == sf.ROLE_TYPESAFE else "llm"


def _banner(args: argparse.Namespace, credential_path: Path, *, interactive: bool) -> None:
    """The one screen that says what this is about to do — and where the key goes."""
    if not interactive:
        return
    _say("")
    _say("skaldir setup — two keys, one pass")
    _say("  1. the LLM provider        (the general model)")
    _say("  2. the TypeSafe/Jev provider (the decision layer)")
    _say("")
    _say("Every answer is pre-filled: press Enter to accept it. The key is typed")
    _say(f"hidden, written to {credential_path} (mode 0600) and never printed.")


# --------------------------------------------------------------------------- #
# the verbs                                                                    #
# --------------------------------------------------------------------------- #


def _interactive_ok(args: argparse.Namespace) -> bool:
    """Prompt only when a human is there to answer (never in a headless run)."""
    if bool(getattr(args, "yes", False)) or bool(getattr(args, "no_input", False)):
        return False
    if bool(getattr(args, "check", False)) or bool(getattr(args, "state", False)):
        return False
    if bool(getattr(args, "catalog", False)) or bool(getattr(args, "providers", False)):
        return False
    try:
        return bool(sys.stdin is not None and sys.stdin.isatty())
    except Exception:  # noqa: BLE001 — a closed stdin is not an interactive one
        return False


def cmd_setup(args: argparse.Namespace) -> int:
    """``skaldir setup`` — pick, ask (masked), validate, write, verify (or ``--check``)."""
    config = str(getattr(args, "config", "") or "") or None
    runtime = getattr(args, "runtime", "") or None
    env_file_arg = str(getattr(args, "env_file", "") or "").strip() or None

    try:
        if bool(getattr(args, "providers", False)):
            listing = sf.provider_report()
            return _emit(
                {
                    "action": "providers",
                    **listing,
                    "verdict": f"{listing['count']} providers, every one with its base URL",
                },
                args,
                ok=True,
                code=EXIT_OK,
            )
        if bool(getattr(args, "catalog", False)):
            listing = sf.catalog_listing()
            return _emit(
                {
                    "action": "catalog",
                    **listing,
                    "providers": sf.provider_report()["providers"],
                    "provider_count": sf.provider_report()["count"],
                    "verdict": f"{listing['count']} LLM providers",
                },
                args,
                ok=True,
                code=EXIT_OK,
            )
        if bool(getattr(args, "state", False)):
            state = sf.detect_state(runtime=runtime, config_path=config)
            return _emit(
                {"action": "state", **state,
                 "verdict": "configured" if state["configured"] else "needs setup"},
                args,
                ok=bool(state["configured"]),
                code=EXIT_OK if state["configured"] else EXIT_BLOCKED,
            )
        if bool(getattr(args, "check", False)):
            payload, code = sf.check_setup(
                path=config, runtime=runtime, env_file=env_file_arg
            )
            return _emit(payload, args, ok=bool(payload.get("ok")), code=code)

        interactive = _interactive_ok(args)
        env_map = dict(os.environ)
        credential_path = sf.env_file_path(
            config_path=config, explicit=env_file_arg or "", env=env_map
        )
        env_seen = sf.read_env_file(credential_path)
        _banner(args, credential_path, interactive=interactive)

        # the LLM provider is asked first, so the decision layer's own question can
        # default to the same endpoint — one pass, two keys, one choice
        collected: dict[str, RoleAnswers] = {}
        fallback = ""
        for role in (sf.ROLE_LLM, sf.ROLE_TYPESAFE):
            item = _collect(
                role,
                args,
                interactive=interactive,
                env=env_map,
                env_file=env_seen,
                fallback_provider=fallback,
            )
            collected[role] = item
            fallback = item.provider or fallback

        payload, code = sf.run_setup(
            typesafe=collected[sf.ROLE_TYPESAFE].key,
            llm=collected[sf.ROLE_LLM].key,
            path=config,
            runtime=runtime,
            check_only=False,
            write=not bool(getattr(args, "no_write", False)),
            backup=not bool(getattr(args, "no_backup", False)),
            timeout_s=getattr(args, "timeout", None),
            measure_streaming=not bool(getattr(args, "no_streaming_probe", False)),
            env_file=env_file_arg,
            write_env=not bool(getattr(args, "no_env_write", False)),
            key_values={role: item.value for role, item in collected.items() if item.value},
            key_sources={role: item.source for role, item in collected.items() if item.source},
            missing_by_role={
                role: item.missing
                for role, item in collected.items()
                if item.key is None and item.missing
            },
        )
        payload["asked"] = {
            role: item.asked for role, item in collected.items() if item.asked
        }
        return _emit(payload, args, ok=bool(payload.get("ok")), code=code)
    except sf.SetupRefused as exc:
        payload = {"action": "setup", "ok": False, "problem": str(exc)[:300],
                   "verdict": "refused"}
        return _emit(payload, args, ok=False, code=EXIT_BLOCKED)
    except sf.SetupIncomplete as exc:
        payload = {"action": "setup", "ok": False, "problem": str(exc)[:300],
                   "verdict": "incomplete"}
        return _emit(payload, args, ok=False, code=EXIT_BLOCKED)
    except Exception as exc:  # noqa: BLE001 — one JSON envelope, never a traceback
        payload = {
            "action": "setup",
            "ok": False,
            "problem": f"{type(exc).__name__}: {exc}"[:300],
            "verdict": "internal error",
        }
        return _emit(payload, args, ok=False, code=EXIT_INTERNAL)


# --------------------------------------------------------------------------- #
# parser                                                                       #
# --------------------------------------------------------------------------- #

_SETUP_HELP = (
    "first-run setup: the two mandatory keys (a TypeSafe/Jev provider and one LLM "
    "provider), each proved by a real call. A literal key is never stored: pass the "
    "NAME of the environment variable that holds it (`--jev-api-key-env`, "
    "`--llm-api-key-env`). Writes the runtime config atomically with a backup, then "
    "re-reads it and verifies with one real Jev decision and one real completion."
)

_CONFIG_HELP = (
    "the runtime config file (default: $SKALDIR_LLM_CONFIG, else <runtime>/config.yaml) — "
    "the same file the provider layer reads"
)


def _add_setup_arguments(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """The verb's own arguments — one definition, used by both parser spellings.

    One row per question the interactive flow asks (`skaldir.setup_flow.questions`):
    a flag, and a variable. A test compares the two sets, so no question can exist
    without a headless equivalent.
    """
    parser.add_argument("--jev-provider", dest="jev_provider", default="",
                        help="pick the TypeSafe/Jev provider by id (see `skaldir setup "
                             "--providers`) — supplies the base URL and the key variable name")
    parser.add_argument("--jev-base-url", dest="jev_base_url", default="",
                        help="the TypeSafe/Jev provider's OpenAI-compatible endpoint root")
    parser.add_argument("--jev-model", dest="jev_model", default="",
                        help="the model the decision layer evaluates with")
    parser.add_argument("--jev-api-key-env", dest="jev_api_key_env", default="",
                        help="the NAME of the variable holding the Jev key (never the key)")
    parser.add_argument("--jev-key-stdin", dest="jev_key_stdin", action="store_true",
                        help="read the Jev key from one line on stdin — the scripted form of "
                             "the hidden prompt (a value in argv would be in the shell history)")
    parser.add_argument("--llm-provider", dest="llm_provider", default="",
                        help="pick the LLM provider by id (deepseek, openrouter, openai, "
                             "ollama, …) — supplies the base URL, the model and the key name")
    parser.add_argument("--llm-base-url", dest="llm_base_url", default="",
                        help="the general provider's OpenAI-compatible endpoint root")
    parser.add_argument("--llm-model", dest="llm_model", default="",
                        help="the general model")
    parser.add_argument("--llm-api-key-env", dest="llm_api_key_env", default="",
                        help="the NAME of the variable holding the LLM key (never the key)")
    parser.add_argument("--llm-key-stdin", dest="llm_key_stdin", action="store_true",
                        help="read the LLM key from one line on stdin — the scripted form of "
                             "the hidden prompt (a value in argv would be in the shell history)")
    parser.add_argument("--llm-catalog", dest="llm_catalog", default="",
                        help="pick the LLM provider from the catalog instead of spelling it "
                             "out (deepseek / openrouter / openai / local)")
    parser.add_argument("--env-file", dest="env_file", default="", metavar="PATH",
                        help="the credential file the key is written to (default: "
                             "<config dir>/.env, mode 0600)")
    parser.add_argument("--no-env-write", dest="no_env_write", action="store_true",
                        help="never write the key into the credential file (the environment "
                             "keeps it)")
    parser.add_argument("--config", default="", metavar="PATH", help=_CONFIG_HELP)
    parser.add_argument("--runtime", default="", metavar="PATH",
                        help="the runtime directory (default: $SKALDIR_HOME, $HERMES_HOME/skaldir)")
    parser.add_argument("--check", action="store_true",
                        help="re-run the validation only and exit 0 only if both keys answer")
    parser.add_argument("--state", action="store_true",
                        help="print what is already configured and write nothing")
    parser.add_argument("--providers", action="store_true",
                        help="list the pickable providers (name, note, base URL) and stop")
    parser.add_argument("--catalog", action="store_true",
                        help="list the LLM providers a first run may pick from")
    parser.add_argument("--no-write", dest="no_write", action="store_true",
                        help="validate both keys and stop (write nothing)")
    parser.add_argument("--no-backup", dest="no_backup", action="store_true",
                        help="do not back up an existing config file before replacing it")
    parser.add_argument("--no-streaming-probe", dest="no_streaming_probe", action="store_true",
                        help="skip the streaming measurement (one request fewer per key)")
    parser.add_argument("--timeout", type=float, default=None,
                        help="per-request timeout in seconds")
    parser.add_argument("--yes", "-y", action="store_true",
                        help="never prompt: missing fields are reported as the flags to pass")
    parser.add_argument("--no-input", dest="no_input", action="store_true",
                        help="alias of --yes for a headless caller")
    parser.add_argument("--json", action="store_true", help="print one JSON payload on stdout")
    parser.add_argument("--format", choices=("text", "json"), help="output format (text|json)")
    parser.set_defaults(func=cmd_setup)
    return parser


def build_setup_parser(subparsers: Any) -> Any:
    """Add ``setup`` to an existing ``subparsers`` handle."""
    parser = subparsers.add_parser(
        "setup",
        help="first-run setup: the two mandatory keys, each proved by a real call",
        description=_SETUP_HELP,
        epilog=(
            "headless: " + sf.scratch_hint() + "\n"
            "exit codes: 0 ok · 1 usage · 2 a key is configured but does not work · "
            "3 a mandatory key is missing · 4 a remote endpoint has no credential · "
            "7 internal · 8 no answer in time"
        ),
    )
    return _add_setup_arguments(parser)


def register_cli(subparsers: Any) -> Any:
    """The CLI envelope contract: ``register_cli(subparsers) -> parser``.

    The group carries its own ``COMMANDS`` entry (adding it when the CLI owner has
    not pasted it yet, refreshing it when a pasted copy went stale), so the wiring
    line in ``cli.py`` cannot leave the verb reachable-but-undocumented.
    """
    parser = build_setup_parser(subparsers)
    install_contract()
    return parser


def build_parser() -> argparse.ArgumentParser:
    """A standalone parser with just this verb (``python -m skaldir.setup_cli``).

    Same arguments as :func:`build_setup_parser` minus the wrapping ``setup`` word,
    so ``main(["--check"])`` and the product's ``skaldir setup --check`` run exactly
    the same handler.
    """
    parser = argparse.ArgumentParser(prog="skaldir setup", description=_SETUP_HELP)
    return _add_setup_arguments(parser)


def main(argv: list[str] | None = None) -> int:
    """``skaldir setup …`` — returns the exit code, never raises for a request.

    This is a single-verb group, so the handler is known: there is no ``func``
    lookup to get wrong and no second spelling to keep in sync. ``--help`` is the
    one argv shape argparse answers itself, and its exit code is honoured as-is.
    """
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse already chose a code (--help exits 0)
        return EXIT_OK if not exc.code else EXIT_USAGE
    return int(cmd_setup(args))


if __name__ == "__main__":  # ``python -m skaldir.setup_cli``
    raise SystemExit(main())


#: The ``cli_contract.COMMANDS`` entry this verb hands over. The CLI owner pastes
#: the block into ``cli_contract.COMMANDS`` — a test here fails while the path is
#: missing, so the verb cannot ship reachable-but-undocumented.
CONTRACTS_SNIPPET = '''
    # -- first-run setup: the two mandatory keys ---------------------------- #
    "setup": _C("kernel", (_ev("src/skaldir/setup_flow.py", note="the flow: detect, "
                               "validate, atomic write, verify"),
                           _ev("src/skaldir/provider_config.py", note="the store it "
                               "writes through (backup + secret refusal)"),
                           _ev("src/skaldir/jev/runtime.py", note="the decision layer the "
                               "Jev key is verified against"),
                           _ev("tests/test_setup_flow.py", "test", "the stub-endpoint "
                               "end-to-end proofs")),
             "first-run setup: the two mandatory keys, each proved by a real call",
             exit_codes=(0, 1, 2, 3, 4, 7, 8), mutates=True, requires_credentials=True,
             data_schema={"type": "object", "properties": {
                 "action": {"type": "string"}, "config_file": {"type": "string"},
                 "env_file": {"type": "string"}, "credentials_file": {"type": "object"},
                 "key_sources": {"type": "object"}, "next_steps": {"type": "object"},
                 "asked": {"type": "object"}, "providers": {"type": "array"},
                 "mandatory_keys": {"type": "array"}, "state": {"type": "object"},
                 "roles": {"type": "object"}, "write": {"type": "object"},
                 "verify": {"type": "object"}, "reread": {"type": "object"},
                 "ok": {"type": "boolean"}, "verdict": {"type": "string"}},
                 "required": ["action", "ok", "mandatory_keys"]},
             notes="api_key_env is a NAME — the credential itself is typed at a hidden "
                   "prompt (or piped with --llm-key-stdin/--jev-key-stdin) and written to "
                   "the .env beside the config with mode 0600; the config never carries a "
                   "value. The provider picker pre-fills the base URL for all 16 catalog "
                   "rows (a local server carries its own loopback URL), a key that is "
                   "already in the environment or in the .env is reused and reported "
                   "instead of asked for, --check re-validates and writes nothing (it reads "
                   "the .env, exit 0 only when both keys answer; exit 4 when a remote "
                   "endpoint has no credential), and the config is written atomically with "
                   "a backup and then re-read before one real Jev decision and one real "
                   "completion verify it"),
'''

#: The paths this verb owns in ``cli_contract.COMMANDS``.
CONTRACT_PATHS = ("setup",)


#: The setup payload's data schema, shared by the snippet and the installer.
DATA_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {"type": "string"}, "config_file": {"type": "string"},
        "env_file": {"type": "string"}, "credentials_file": {"type": "object"},
        "key_sources": {"type": "object"}, "next_steps": {"type": "object"},
        "asked": {"type": "object"}, "providers": {"type": "array"},
        "mandatory_keys": {"type": "array"}, "state": {"type": "object"},
        "roles": {"type": "object"}, "write": {"type": "object"},
        "verify": {"type": "object"}, "reread": {"type": "object"},
        "ok": {"type": "boolean"}, "verdict": {"type": "string"},
    },
    "required": ["action", "ok", "mandatory_keys"],
}

#: The entry's notes, shared by the snippet and the installer.
NOTES = (
    "api_key_env is a NAME — the credential itself is typed at a hidden prompt (or piped "
    "with --llm-key-stdin/--jev-key-stdin) and written to the .env beside the config with "
    "mode 0600; the config never carries a value. The provider picker pre-fills the base "
    "URL for all 16 catalog rows (a local server carries its own loopback URL), a key that "
    "is already in the environment or in the .env is reused and reported instead of asked "
    "for, --check re-validates and writes nothing (it reads the .env, exit 0 only when both "
    "keys answer; exit 4 when a remote endpoint has no credential), and the config is "
    "written atomically with a backup and then re-read before one real Jev decision and one "
    "real completion verify it"
)

DESCRIPTION = "first-run setup: the two mandatory keys, each proved by a real call"


def _assert_registered() -> None:
    """Fail loudly when the hand-over is incomplete (used by the tests)."""
    contract = product_contract()
    if contract is None:
        return
    missing = [path for path in CONTRACT_PATHS if path not in contract.COMMANDS]
    if missing:
        raise AssertionError(
            "these setup paths have no cli_contract.COMMANDS entry: " + ", ".join(missing)
        )


def contract_entry() -> Any:
    """The ``cli_contract.CommandSpec`` this verb hands over, or ``None``."""
    contract = product_contract()
    if contract is None or not hasattr(contract, "CommandSpec"):
        return None
    return contract.CommandSpec(
        "kernel",
        (
            contract._ev("src/skaldir/setup_flow.py", note="the flow: detect, validate, "
                                                           "atomic write, verify"),
            contract._ev("src/skaldir/provider_config.py",
                         note="the store it writes through (backup + secret refusal)"),
            contract._ev("src/skaldir/jev/runtime.py",
                         note="the decision layer the Jev key is verified against"),
            contract._ev("tests/test_setup_flow.py", "test",
                         "the stub-endpoint end-to-end proofs"),
        ),
        DESCRIPTION,
        exit_codes=(EXIT_OK, EXIT_USAGE, EXIT_VERIFICATION_FAILED, EXIT_BLOCKED,
                    EXIT_NEEDS_CREDENTIALS, EXIT_INTERNAL, EXIT_TIMEOUT),
        mutates=True,
        requires_credentials=True,
        data_schema=DATA_SCHEMA,
        notes=NOTES,
    )


#: The fields of a ``CommandSpec`` this group owns — compared when a pasted copy
#: is checked against the snippet it came from.
_OWNED_FIELDS = (
    "layer",
    "description",
    "exit_codes",
    "mutates",
    "requires_credentials",
    "data_schema",
    "notes",
)


def _differs(current: Any, wanted: Any) -> bool:
    """Does an already-registered entry describe an older verb than this one?"""
    if current is None:
        return False
    return any(
        getattr(current, field, None) != getattr(wanted, field, None)
        for field in _OWNED_FIELDS
    )


def install_contract(*, refresh: bool = True) -> tuple[str, ...]:
    """Register our ``COMMANDS`` entry when the contract is importable.

    **Refreshing** matters: the entry is normally pasted in by hand (that is how
    ``cli_contract.py`` got it), so without a refresh a pasted copy silently
    outlives the snippet it came from and the notes plus the payload schema keep
    describing an older verb. Adding stays idempotent — nothing is added twice and
    an entry that already matches is left alone — so the two never disagree.
    """
    contract = product_contract()
    entry = contract_entry()
    if entry is None:
        return ()
    added = tuple(install_contract_entries({CONTRACT_PATHS[0]: entry}, contract))
    if not added and refresh and _differs(contract.COMMANDS.get(CONTRACT_PATHS[0]), entry):
        contract.COMMANDS[CONTRACT_PATHS[0]] = entry
    return added


__all__ = [
    "CONTRACTS_SNIPPET",
    "CONTRACT_PATHS",
    "DATA_SCHEMA",
    "DESCRIPTION",
    "EXIT_BLOCKED",
    "EXIT_CONFLICT",
    "EXIT_INTERNAL",
    "EXIT_NEEDS_CREDENTIALS",
    "EXIT_NOT_FOUND",
    "EXIT_OK",
    "EXIT_TIMEOUT",
    "EXIT_USAGE",
    "EXIT_VERIFICATION_FAILED",
    "NOTES",
    "build_parser",
    "build_setup_parser",
    "cmd_setup",
    "contract_entry",
    "install_contract",
    "main",
    "register_cli",
]
