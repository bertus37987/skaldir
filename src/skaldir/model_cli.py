"""``skaldir model`` — pick the provider and the model behind one of the two keys.

    skaldir model                                   # role → provider → model → key (hidden)
    skaldir model --role llm                        # skip the role question
    skaldir model --role llm --provider deepseek    # endpoint, model list and key name come with it
    skaldir model --role typesafe --provider ollama --model llama3.2
    skaldir model --role llm --provider openai --model gpt-4o-mini --key-stdin
    skaldir model --check --json                    # re-validate, write nothing
    skaldir model --list                            # the catalog rows the picker shows

Two roles and no more (``AGENTS.md``): the **TypeSafe/Jev provider** (the decision layer)
and **one LLM provider** (the general model). The flow asks the role first, then the
provider — every row carries the base URL the catalog already knows, so accepting a row is
the whole endpoint answer — then the model, pre-filled from the catalog's own model list,
and finally the key, which is the **only** thing a human types.

**The key never reaches argv, stdout, a log or the config.** The prompt is
``getpass.getpass(prompt, stream=sys.stderr)`` — hidden, and off stdout so the one JSON
document stays clean. The scripted form is ``--key-stdin`` (one line on stdin); there is
no ``--key VALUE``, because an argv value is in the shell history by construction. The
value is written to the ``.env`` beside the config with mode ``0600`` (the same
``setup_flow.write_env_file`` ``skaldir setup`` uses), while ``config.yaml`` names the
*variable* (``api_key_env``). A key that already lives in the environment or in that file
is reused, and the report says which one answered.

The interactive flow and the flags run **one** code path: :func:`model_picker.plan_for`
resolves the choice and :func:`model_picker.apply_choice` writes the credential, validates
live (model echo · latency · streaming · structured output, exactly as ``skaldir setup``
does) and writes the config atomically with a backup. The in-chat ``/model`` picker calls
the same two functions.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from collections.abc import Mapping, Sequence
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
EXIT_INTERNAL = int(getattr(_CONTRACT, "EXIT_INTERNAL", sf.EXIT_INTERNAL))
EXIT_TIMEOUT = int(getattr(_CONTRACT, "EXIT_TIMEOUT", sf.EXIT_TIMEOUT))

#: The one refusal every question shares: an empty answer means the selection was left
#: (``Esc``/``Ctrl-C``) — nothing was chosen, so nothing may be written. It is the same
#: verdict ``--no-input`` gives when it is missing a flag.
CANCELLED = "nothing was chosen — the selection was cancelled (nothing was written)"


def _hand_over(payload: dict[str, Any]) -> bool:
    """Give the payload to the shared envelope when the product driver is carrying it."""
    contract = _CONTRACT
    if contract is None or not envelope_mode():
        return False
    try:
        contract.set_data(dict(payload))
    except Exception:  # noqa: BLE001 — an envelope problem must not break the verb
        return False
    return True


def _say(line: str = "") -> None:
    """Prompts and notes go to **stderr**: stdout carries the one JSON document."""
    sys.stderr.write(str(line) + "\n")
    sys.stderr.flush()


def _finish(payload: dict[str, Any], args: argparse.Namespace, code: int) -> int:
    """One payload — the human lines when they were asked for, else the JSON document.

    Wired into ``skaldir``, the driver carries the envelope and stdout stays empty of it
    (exactly like every other group); run standalone (``python -m skaldir.model_cli``) the
    payload is printed here. ``--format text`` renders on stdout even for a refusal: the
    report *is* the answer, and the exit code carries the verdict.
    """
    if text_mode(args):
        print(_text(payload))
        return code
    if _hand_over(payload):
        return code
    dump(payload)
    return code


def _text(payload: dict[str, Any]) -> str:
    """Black and white: the choice, the measurement and where the key came from."""
    lines: list[str] = [f"skaldir model ({payload.get('action')})", "=" * 58]
    if payload.get("checked"):
        for role in payload["checked"]:
            item = dict((payload.get("roles") or {}).get(role) or {})
            mark = "ok  " if item.get("ok") else "FAIL"
            lines.append(f"[{mark}] {item.get('label') or role}")
            if not item.get("present"):
                lines.append(f"         {item.get('problem')}")
                continue
            measure = dict(item.get("measure") or {})
            streaming = dict(measure.get("streaming") or {})
            structured = dict(measure.get("structured") or {})
            lines.append(
                f"         model echo {measure.get('model_echo') or 'n/a'} · "
                f"{measure.get('latency_ms')} ms · "
                f"streaming {'yes' if streaming.get('supported') else 'no'} · "
                f"structured {structured.get('mode_used') or 'not measured'}"
            )
            if item.get("problem"):
                lines.append(f"         {item['problem']}")
        if payload.get("credentials_file"):
            credential = dict(payload["credentials_file"])
            lines.append(
                f"credentials:  {credential.get('path')} "
                f"(mode {credential.get('mode') or '?'}, read)"
            )
        lines.append(f"verdict:      {payload.get('verdict')}")
        return "\n".join(lines)

    if payload.get("provider_rows"):
        lines.append(f"providers:    {payload.get('count')} rows, every one with its base URL")
        for row in payload["provider_rows"]:
            lines.append(f"  {row.get('label')}" + (" (local)" if row.get("local") else ""))
            lines.append(f"      {row.get('base_url')}")
            models = ", ".join(str(item) for item in (row.get("models") or [])) or "none documented"
            lines.append(f"      models: {models}")
        return "\n".join(lines)

    plan = dict(payload.get("plan") or {})
    lines.append(f"role:         {plan.get('label') or plan.get('role')} ({plan.get('role')})")
    lines.append(f"provider:     {plan.get('provider')}")
    lines.append(f"endpoint:     {plan.get('base_url')}")
    lines.append(f"model:        {plan.get('model')} ({plan.get('model_source') or 'given'})")
    for role, item in dict(payload.get("key_sources") or {}).items():
        source = dict(item)
        where = (
            "typed at the hidden prompt"
            if source.get("kind") == "typed"
            else (f"reused from {source.get('source')}" if source.get("available") else "not set")
        )
        lines.append(f"key:          {source.get('name') or role} — {where}")
    measure = dict(payload.get("measure") or {})
    if measure:
        streaming = dict(measure.get("streaming") or {})
        structured = dict(measure.get("structured") or {})
        lines.append(
            f"validated:    model echo {measure.get('model_echo') or 'n/a'} · "
            f"{measure.get('latency_ms')} ms · "
            f"streaming {'yes' if streaming.get('supported') else 'no'} · "
            f"structured {structured.get('mode_used') or 'not measured'}"
        )
    if payload.get("credentials_file"):
        credential = dict(payload["credentials_file"])
        lines.append(
            f"credentials:  {credential.get('path')} "
            f"(mode {credential.get('mode') or '?'}, "
            f"{'written' if credential.get('written') else 'unchanged'})"
        )
    write = payload.get("write")
    if isinstance(write, Mapping):
        lines.append(
            f"config:       {write.get('path')} "
            f"({'written' if write.get('written') else 'unchanged'}"
            f"{', backup kept' if write.get('backup') else ''})"
        )
    reread = dict(payload.get("reread") or {})
    if reread:
        lines.append(
            f"re-read:      {reread.get('model')} at {reread.get('base_url')} "
            f"({'matches' if reread.get('matches') else 'DOES NOT MATCH'})"
        )
    lines.append(f"verdict:      {payload.get('verdict')}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# the interactive questions                                                    #
# --------------------------------------------------------------------------- #


def _read_masked(prompt: str) -> str:
    """The key: **no echo, no history, never printed** (the one hidden question)."""
    try:
        return str(getpass.getpass(prompt, stream=sys.stderr) or "").strip()
    except (EOFError, KeyboardInterrupt):
        return ""
    except Exception:  # noqa: BLE001 — no controlling terminal is not a value
        return ""


def _line(prompt: str) -> str:
    """One visible answer, with the prompt on **stderr**: stdout carries the one document.

    This is the plain path's reader (a pipe, ``TERM=dumb``, ``NO_COLOR``, a captured stream):
    the prompt goes to stderr next to the rows, and ``input()`` only reads the line.
    """
    print(prompt, end="", file=sys.stderr, flush=True)
    try:
        return str(input()).strip()
    except (EOFError, KeyboardInterrupt):
        return ""
    except Exception:  # noqa: BLE001 — a stdin that cannot be read is not an answer
        return ""


def _choose(title: str, rows: Sequence[mp.PickRow], *, default: str, allow_text: bool) -> str:
    """Ask one question through the picker's own engine — list, keys and resolution in one place.

    On a terminal that can take it, ``rows`` is a **selection list**: ``↑``/``↓`` (or
    ``k``/``j``) move, ``Enter`` selects the highlighted row, a number or a name narrows the
    list, and with ``allow_text`` a typed id nobody documented is used as it is. Anywhere else
    (a pipe, ``--json``, ``TERM=dumb``, ``NO_COLOR``, a terminal too small for a list) the same
    rows are printed as lines and one line is read — the behaviour this verb always had.

    ``default`` is the pre-filled **value** (``llm``, ``deepseek``, ``deepseek-chat``): it is
    the row the pointer starts on and what Enter means. An empty return is "no answer" — the
    caller refuses rather than guessing a key.
    """
    if not rows and not allow_text:
        raise sf.SetupRefused(f"{title}: nothing to choose from")
    return mp.choose(
        title,
        rows,
        default_value=default,
        allow_text=allow_text,
        interactive=True,
        env=dict(os.environ),
        stream_out=sys.stderr,
        line_reader=_line,
    )


def _ask_role(*, env: Mapping[str, str]) -> str:
    """The picker's first question, in the picker's own words: agent or voice.

    ``agent`` answers the general key (``ROLE_LLM``). ``voice`` moves to the second
    question — LLM or Jev as the voice backing — which answers the decision key
    (``ROLE_TYPESAFE``). Two questions, two keys underneath, unchanged.
    """
    rows = mp.role_rows(state=mp.role_state(env=env))
    default = mp.default_role(env=env)
    answer = _choose(
        "which role — agent or voice? (agent: the general model; voice: then asks "
        "LLM or Jev as its backing)",
        rows,
        default=default,
        allow_text=False,
    )
    if not answer:
        raise sf.SetupRefused(CANCELLED)
    if mp.resolve_role(answer) == sf.ROLE_TYPESAFE:
        # the second-level question the ``voice`` row promises; both answers land on
        # the decision key — they differ in which provider row is pre-selected next
        backing = _choose(
            "voice backing — LLM or Jev? (LLM: an ordinary chat model answering "
            "Jev's typed questions; Jev: the TypeSafe endpoint)",
            mp.voice_backing_rows(),
            default="",
            allow_text=False,
        )
        if not backing:
            raise sf.SetupRefused(CANCELLED)
    role = mp.resolve_role(answer)
    if not role:
        raise sf.SetupRefused(_unknown_role(answer))
    return role


def _unknown_role(answer: str) -> str:
    return (
        f"unknown role {answer!r} — the two roles are 'voice' (the Jev decision "
        "key, asked as LLM or Jev backing) and 'agent' (the general model)"
    )


def _ask_provider(role: str, *, env: Mapping[str, str], config: str | None = None) -> str:
    """The provider question: the catalog rows, each with its base URL pre-filled.

    For the decision layer (TypeSafe / Jev) the measured and local endpoints come first — the
    rest of the catalog is for the general LLM role. The pre-selected row is the provider this
    role is **already** configured with (matched by endpoint), so Enter keeps the decision layer
    where it is instead of dropping back to a catalogue default; only when nothing is
    configured yet does the catalogue's own default answer.
    """
    rows = mp.provider_rows(role=role)
    configured = sf.configured_provider(role, path=config, env=env)
    default = configured or sf.default_provider(role, env=env)
    answer = _choose(
        "provider (number or id) — every row carries its base URL",
        rows,
        default=default,
        allow_text=False,
    )
    if not answer:
        raise sf.SetupRefused(CANCELLED)
    return answer


def _ask_model(
    role: str, provider: str, *, env: Mapping[str, str], config: str | None = None,
    base_url: str = "", env_file: str = "",
) -> str:
    """Select live account models, or the source-backed fallback when unavailable."""
    rows = mp.live_model_rows(provider, role=role, env=env, config=config,
                              base_url=base_url, env_file=env_file,
                              discover=bool(sys.stdin and sys.stdin.isatty()))
    if not rows:
        _say(
            f"  {provider} names no model in the catalog — type the id "
            "(e.g. `skaldir provider catalog --text` lists what is documented)"
        )
        answer = _choose(
            "model id (nothing is documented for this endpoint)",
            rows,
            default="",
            allow_text=True,
        )
    else:
        answer = _choose(
            "model (Enter accepts the pre-filled one)",
            rows,
            default=rows[0].value,
            allow_text=True,
        )
    if not answer:
        raise sf.SetupRefused(CANCELLED)
    return answer


def _interactive(args: argparse.Namespace) -> bool:
    """Prompt only when a human is there to answer (never in a headless run)."""
    if bool(getattr(args, "yes", False)) or bool(getattr(args, "no_input", False)):
        return False
    if bool(getattr(args, "check", False)) or bool(getattr(args, "list", False)):
        return False
    if bool(getattr(args, "key_stdin", False)):
        return False
    try:
        return bool(sys.stdin is not None and sys.stdin.isatty())
    except Exception:  # noqa: BLE001 — a closed stdin is not an interactive one
        return False


def _key_value(
    plan: mp.Plan,
    args: argparse.Namespace,
    *,
    interactive: bool,
    env: Mapping[str, str],
    env_file: str | None,
) -> tuple[str, str]:
    """The credential: piped, typed hidden, or reused — and the label of where it came from.

    Returns ``(value, source)``. An empty value with a source means "the key exists, keep
    it"; an empty value **and** an empty source means there is no key anywhere, and
    :func:`model_picker.apply_choice` then refuses to write anything.
    """
    if bool(getattr(args, "key_stdin", False)):
        return sf.key_value_from_stream(), "stdin"
    name = plan.credential_name()
    credential_path = sf.env_file_path(config_path=getattr(args, "config", "") or None,
                                       explicit=str(env_file or ""), env=env)
    seen = sf.read_env_file(credential_path)
    where = mp.key_provenance(plan.role, name, env=env, seen=seen)
    reused = str(where.get("source") or "")
    in_environment = str(where.get("kind") or "") == "environment" and bool(where.get("name"))
    if not interactive:
        # headless: reuse what exists, never invent a value, never block on input — a key
        # that lives only in the shell is copied into the file by apply_choice itself,
        # which keeps the provenance label ("environment") honest
        return ("", reused) if in_environment else ("", reused if where.get("available") else "")
    prefix = "  the key for " + (name or "this endpoint") + " (hidden)"
    if where.get("available"):
        # Layer 1: K / S / R. Enter here means *keep* — it never confirms a typed key.
        action = mp.ask_existing_key(
            where, name=name,
            say=_say,
            read=_line,
        )
        if action == "keep":
            return "", reused
        if action == "skip":
            return "", ""
        # replace: layer 2 — Enter confirms the key you type
        prompt = prefix + " — Enter confirms the key you typed"
    elif plan.local:
        prompt = prefix + " — not required for a local endpoint, Enter skips it"
    else:
        prompt = prefix + " — Enter confirms the key you typed"
    typed = _read_masked(prompt + ": ")
    if typed:
        return sf.refuse_key_value(typed, name=name or "the key"), "prompt (hidden)"
    if where.get("available"):
        return "", reused
    if plan.local:
        return "", ""
    _say("  no key was entered — the endpoint will be validated without one")
    return "", ""


# --------------------------------------------------------------------------- #
# the verbs                                                                    #
# --------------------------------------------------------------------------- #


def _list_payload() -> dict[str, Any]:
    """``--list``: the same rows the picker shows, each with its own model list."""
    rows = mp.provider_rows()
    payload_rows: list[dict[str, Any]] = []
    for choice in sf.provider_choices():
        payload_rows.append(
            {
                "id": choice.id,
                "label": choice.label,
                "base_url": choice.base_url,
                "api_key_env": choice.api_key_env,
                "local": choice.local,
                "verification": choice.verification,
                "notes": choice.note,
                "models": [item.id for item in mp.model_choices(choice.id)],
            }
        )
    return {
        "action": "model-list",
        "count": len(rows),
        "provider_rows": payload_rows,
        "note": "every row carries the base URL the picker pre-fills; the key is asked for, "
                "never listed",
        "ok": True,
        "verdict": f"{len(rows)} providers, {len(payload_rows)} rows with their base URLs",
    }


def cmd_model(args: argparse.Namespace) -> int:
    """``skaldir model`` — pick, validate live, write the .env (0600) and the config."""
    config = str(getattr(args, "config", "") or "") or None
    env_file_arg = str(getattr(args, "env_file", "") or "").strip() or None
    timeout_s = getattr(args, "timeout", None)
    measure_streaming = not bool(getattr(args, "no_streaming_probe", False))
    env_map = dict(os.environ)
    try:
        if bool(getattr(args, "list", False)):
            return _finish(_list_payload(), args, EXIT_OK)
        role_answer = str(getattr(args, "role", "") or "").strip()
        role = mp.resolve_role(role_answer) if role_answer else ""
        if role_answer and not role:
            payload = {
                "action": "model",
                "ok": False,
                "problem": _unknown_role(role_answer),
                "verdict": "refused",
            }
            return _finish(payload, args, EXIT_USAGE)
        if bool(getattr(args, "check", False)):
            payload, code = mp.check_choice(
                role=role,
                path=config,
                env_file=env_file_arg,
                timeout_s=timeout_s,
                measure_streaming=measure_streaming,
            )
            return _finish(payload, args, code)

        interactive = _interactive(args)
        provider = str(getattr(args, "provider", "") or "").strip()
        model = str(getattr(args, "model", "") or "").strip()
        base_url = str(getattr(args, "base_url", "") or "").strip()
        if not role and interactive:
            role = _ask_role(env=env_map)
        if not role:
            role = mp.default_role(env=env_map)
        if not provider and interactive:
            provider = _ask_provider(role, env=env_map, config=config)
        if not model and interactive:
            model = _ask_model(role, provider, env=env_map, config=config,
                               base_url=base_url, env_file=env_file_arg or "")
        plan = mp.plan_for(role, provider=provider, model=model, base_url=base_url, env=env_map)
        key_value, key_source = _key_value(
            plan, args, interactive=interactive, env=env_map, env_file=env_file_arg
        )
        payload, code = mp.apply_choice(
            plan,
            key_value=key_value,
            key_source=key_source,
            env=env_map,
            env_file=env_file_arg,
            config_path=config,
            write_env=not bool(getattr(args, "no_env_write", False)),
            write_config=not bool(getattr(args, "no_write", False)),
            backup=not bool(getattr(args, "no_backup", False)),
            timeout_s=timeout_s,
            measure_streaming=measure_streaming,
        )
        payload["asked"] = {
            "role": role,
            "provider": provider or plan.provider,
            "model": model or plan.model,
        }
        return _finish(payload, args, code)
    except sf.SetupRefused as exc:
        payload = {
            "action": "model",
            "ok": False,
            "problem": str(exc)[:300],
            "verdict": "refused: nothing was written",
        }
        return _finish(payload, args, EXIT_BLOCKED)
    except sf.SetupIncomplete as exc:
        payload = {
            "action": "model",
            "ok": False,
            "problem": str(exc)[:300],
            "verdict": "incomplete",
        }
        return _finish(payload, args, EXIT_BLOCKED)
    except Exception as exc:  # noqa: BLE001 — one JSON envelope, never a traceback
        payload = {
            "action": "model",
            "ok": False,
            "problem": f"{type(exc).__name__}: {exc}"[:300],
            "verdict": "internal error",
        }
        return _finish(payload, args, EXIT_INTERNAL)


# --------------------------------------------------------------------------- #
# parser                                                                       #
# --------------------------------------------------------------------------- #

_MODEL_HELP = (
    "pick the provider and the model behind one of the two keys (a TypeSafe/Jev provider "
    "for the decision layer, or one LLM provider for the general model). The provider list "
    "carries the base URL, so accepting a row is the whole endpoint answer; the model is "
    "pre-filled from the catalog's own list; the key is the only thing typed — hidden, and "
    "written to the .env beside the config with mode 0600 while config.yaml names the "
    "variable (api_key_env). The choice is validated live (model echo · latency · streaming "
    "· structured output) before the config is written."
)


def _add_model_arguments(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """The verb's own arguments — one definition, used by both parser spellings."""
    parser.add_argument(
        "--role",
        default="",
        help="which of the two keys to change: typesafe (or jev) · llm (default: the one "
             "that is not usable yet)",
    )
    parser.add_argument("--provider", default="",
                        help="a catalog provider id or row number (supplies the base URL, the "
                             "model default and the credential variable name)")
    parser.add_argument("--model", default="", help="a model id (default: the catalog's own)")
    parser.add_argument("--base-url", dest="base_url", default="",
                        help="override the endpoint the provider row would pre-fill")
    parser.add_argument("--key-stdin", dest="key_stdin", action="store_true",
                        help="read the key from one line on stdin (there is no --key VALUE: "
                             "argv is shell history)")
    parser.add_argument("--env-file", dest="env_file", default="",
                        help="where the credential is written (default: .env beside the config)")
    parser.add_argument("--no-env-write", dest="no_env_write", action="store_true",
                        help="validate without writing the credential file")
    parser.add_argument("--no-write", dest="no_write", action="store_true",
                        help="validate the choice and write no config")
    parser.add_argument("--no-backup", dest="no_backup", action="store_true",
                        help="do not back up the config file before replacing it")
    parser.add_argument("--check", action="store_true",
                        help="re-validate the configured role(s) and write nothing")
    parser.add_argument("--list", action="store_true",
                        help="print the provider rows (name, base URL, model list) and stop")
    parser.add_argument("--config", default="",
                        help="the runtime config file (default: $SKALDIR_LLM_CONFIG, else "
                             "<runtime>/config.yaml)")
    parser.add_argument("--timeout", type=float, default=None,
                        help="per-request timeout in seconds")
    parser.add_argument("--no-streaming-probe", dest="no_streaming_probe", action="store_true",
                        help="skip the streaming measurement (one request fewer)")
    parser.add_argument("--yes", "-y", action="store_true",
                        help="never prompt: a missing field is reported instead of asked for")
    parser.add_argument("--no-input", dest="no_input", action="store_true",
                        help="alias of --yes for a headless caller")
    parser.add_argument("--json", action="store_true", help="print one JSON payload on stdout")
    parser.add_argument("--format", choices=("text", "json"), help="output format (text|json)")
    parser.set_defaults(func=cmd_model)
    return parser


def build_model_parser(subparsers: Any) -> Any:
    """Add ``model`` to an existing ``subparsers`` handle."""
    parser = subparsers.add_parser(
        "model",
        help="pick the provider and the model behind one of the two keys",
        description=_MODEL_HELP,
        epilog=(
            "headless: --role llm --provider deepseek --model deepseek-chat --key-stdin\n"
            "exit codes: 0 ok · 1 usage · 2 the choice is configured but does not work · "
            "3 blocked (unknown provider, no model) · 4 a remote endpoint has no credential "
            "· 7 internal · 8 no answer in time"
        ),
    )
    return _add_model_arguments(parser)


def register_cli(subparsers: Any) -> Any:
    """The CLI envelope contract: ``register_cli(subparsers) -> parser``."""
    parser = build_model_parser(subparsers)
    install_contract()
    return parser


def build_parser() -> argparse.ArgumentParser:
    """A standalone parser with just this verb (``python -m skaldir.model_cli``)."""
    parser = argparse.ArgumentParser(prog="skaldir model", description=_MODEL_HELP)
    return _add_model_arguments(parser)


def main(argv: list[str] | None = None) -> int:
    """``skaldir model …`` — returns the exit code, never raises for a request."""
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse already chose a code (--help exits 0)
        return EXIT_OK if not exc.code else EXIT_USAGE
    return int(cmd_model(args))


if __name__ == "__main__":  # ``python -m skaldir.model_cli``
    raise SystemExit(main())


#: The ``cli_contract.COMMANDS`` entry this verb hands over. ``register_cli`` installs it,
#: so the wiring line in ``cli.py`` cannot leave the verb reachable-but-undocumented.
CONTRACTS_SNIPPET = '''
    # -- the model picker: one role, one provider, one hidden key ----------- #
    "model": _C("kernel", (_ev("src/skaldir/model_picker.py", note="the picker: role → "
                               "provider (base URL pre-filled) → model → hidden key"),
                           _ev("src/skaldir/setup_flow.py", note="the credential store "
                               "(write_env_file, 0600) and the live probe it reuses"),
                           _ev("src/skaldir/provider_registry_data.py", note="the catalog the "
                               "rows and their base URLs come from"),
                           _ev("tests/test_model_picker.py", "test", "the state machine, the "
                               "fake prober and the pinned exit codes")),
             "pick the provider and the model behind one of the two keys",
             exit_codes=(0, 1, 2, 3, 4, 7, 8), mutates=True, requires_credentials=True,
             data_schema={"type": "object", "properties": {
                 "action": {"type": "string"}, "plan": {"type": "object"},
                 "key_sources": {"type": "object"}, "measure": {"type": "object"},
                 "credentials_file": {"type": "object"}, "write": {"type": "object"},
                 "reread": {"type": "object"}, "env_file": {"type": "string"},
                 "config_file": {"type": "string"}, "roles": {"type": "object"},
                 "checked": {"type": "array"}, "provider_rows": {"type": "array"},
                 "asked": {"type": "object"}, "ok": {"type": "boolean"},
                 "verdict": {"type": "string"}},
                 "required": ["action", "ok"]},
             notes="the key is typed at a hidden prompt (or piped with --key-stdin) and "
                   "written to the .env beside the config with mode 0600; config.yaml names "
                   "the variable (api_key_env) and never the value. Every provider row "
                   "pre-fills the base URL, the model comes from the catalog's own list, and "
                   "the choice is validated live (model echo · latency · streaming · "
                   "structured) before the config is written atomically with a backup. A "
                   "remote endpoint with no credential anywhere exits 4 and writes nothing; "
                   "--check re-validates and writes nothing; in the chat, /model opens the "
                   "same picker inside the frame"),
'''

#: The paths this verb owns in ``cli_contract.COMMANDS``.
CONTRACT_PATHS = ("model",)

#: The payload's data schema, shared by the snippet and the installer.
DATA_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {"type": "string"},
        "plan": {"type": "object"},
        "key_sources": {"type": "object"},
        "measure": {"type": "object"},
        "credentials_file": {"type": "object"},
        "write": {"type": "object"},
        "reread": {"type": "object"},
        "env_file": {"type": "string"},
        "config_file": {"type": "string"},
        "roles": {"type": "object"},
        "checked": {"type": "array"},
        "provider_rows": {"type": "array"},
        "asked": {"type": "object"},
        "ok": {"type": "boolean"},
        "verdict": {"type": "string"},
    },
    "required": ["action", "ok"],
}

DESCRIPTION = "pick the provider and the model behind one of the two keys"

NOTES = (
    "the key is typed at a hidden prompt (or piped with --key-stdin) and written to the "
    ".env beside the config with mode 0600; config.yaml names the variable (api_key_env) "
    "and never the value. Every provider row pre-fills the base URL, the model comes from "
    "the catalog's own list, and the choice is validated live (model echo · latency · "
    "streaming · structured) before the config is written atomically with a backup. A "
    "remote endpoint with no credential anywhere exits 4 and writes nothing; --check "
    "re-validates and writes nothing; in the chat, /model opens the same picker inside the "
    "frame"
)


def _assert_registered() -> None:
    """Fail loudly when the hand-over is incomplete (used by the tests)."""
    contract = product_contract()
    if contract is None:
        return
    missing = [path for path in CONTRACT_PATHS if path not in contract.COMMANDS]
    if missing:
        raise AssertionError(
            "these model paths have no cli_contract.COMMANDS entry: " + ", ".join(missing)
        )


def contract_entry() -> Any:
    """The ``cli_contract.CommandSpec`` this verb hands over, or ``None``."""
    contract = product_contract()
    if contract is None or not hasattr(contract, "CommandSpec"):
        return None
    return contract.CommandSpec(
        "kernel",
        (
            contract._ev(
                "src/skaldir/model_picker.py",
                note="the picker: role → provider (base URL pre-filled) → model → hidden key",
            ),
            contract._ev(
                "src/skaldir/setup_flow.py",
                note="the credential store (write_env_file, 0600) and the live probe it reuses",
            ),
            contract._ev(
                "src/skaldir/provider_registry_data.py",
                note="the catalog the rows and their base URLs come from",
            ),
            contract._ev(
                "tests/test_model_picker.py",
                "test",
                "the state machine, the fake prober and the pinned exit codes",
            ),
        ),
        DESCRIPTION,
        exit_codes=(EXIT_OK, EXIT_USAGE, EXIT_VERIFICATION_FAILED, EXIT_BLOCKED,
                    EXIT_NEEDS_CREDENTIALS, EXIT_INTERNAL, EXIT_TIMEOUT),
        mutates=True,
        requires_credentials=True,
        data_schema=DATA_SCHEMA,
        notes=NOTES,
    )


#: The fields of a ``CommandSpec`` this group owns — compared when a pasted copy is stale.
_OWNED_FIELDS = (
    "layer",
    "description",
    "exit_codes",
    "mutates",
    "requires_credentials",
    "data_schema",
    "notes",
)


def _stale(current: Any, wanted: Any) -> bool:
    """Does an already-registered entry describe an older verb than this one?

    One field at a time, on purpose: the first difference is the one a reader wants named,
    and a spelled-out loop keeps this group's helper from being a copy of the setup
    group's (``tests/test_cli_shared.py`` fails a new byte-identical helper between groups).
    """
    if current is None:
        return False
    for field in _OWNED_FIELDS:
        if getattr(current, field, None) != getattr(wanted, field, None):
            return True
    return False


def install_contract(*, refresh: bool = True) -> tuple[str, ...]:
    """Register our ``COMMANDS`` entry when the contract is importable.

    Adding is idempotent; a pasted copy that went stale is refreshed, so the entry's notes
    and payload schema cannot keep describing an older verb. (The hand-over block is in
    :data:`CONTRACTS_SNIPPET` for the CLI owner to paste.)
    """
    contract = product_contract()
    entry = contract_entry()
    if entry is None:
        return ()
    added = tuple(install_contract_entries({CONTRACT_PATHS[0]: entry}, contract))
    if not added and refresh and _stale(contract.COMMANDS.get(CONTRACT_PATHS[0]), entry):
        contract.COMMANDS[CONTRACT_PATHS[0]] = entry
    return added


__all__ = [
    "CONTRACTS_SNIPPET",
    "CONTRACT_PATHS",
    "DATA_SCHEMA",
    "DESCRIPTION",
    "EXIT_BLOCKED",
    "EXIT_INTERNAL",
    "EXIT_NEEDS_CREDENTIALS",
    "EXIT_OK",
    "EXIT_TIMEOUT",
    "EXIT_USAGE",
    "EXIT_VERIFICATION_FAILED",
    "NOTES",
    "build_model_parser",
    "build_parser",
    "cmd_model",
    "contract_entry",
    "install_contract",
    "main",
    "register_cli",
]
