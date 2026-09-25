#!/usr/bin/env python3
"""Generate `docs/CLI-REFERENCE.md` from the CLI's own contract dump.

Source of truth is **the running CLI**, never this file and never a hand-edit:

    godsvoice contract dump --json      # every command: args + JSON Schema + exit codes
    godsvoice selftest --json           # the eight layers: reachability + which commands prove them

Usage (from the repo root, with the project venv so `godsv` is importable)::

    .venv/bin/python tools/gv_cli_reference.py                 # writes docs/CLI-REFERENCE.md
    .venv/bin/python tools/gv_cli_reference.py --check         # exit 1 when the file is stale
    .venv/bin/python tools/gv_cli_reference.py --dump d.json --selftest s.json --out /tmp/ref.md

Why a generator: the contract dump is the same document the desktop GUI renders, so a
hand-written reference drifts silently. Nothing in the emitted prose is invented — every
command, argument, schema, exit code and limitation sentence is copied from the dump, and
the exit-code vocabulary is read from `godsv.cli_contract` (the module that defines it).

Exit codes of this tool: 0 wrote/verified · 1 dump unusable or --check found drift · 2 usage.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO_ROOT / "docs" / "CLI-REFERENCE.md"

#: Phrase markers that mean "the contract itself declares this as less than a finished
#: feature". They are matched case-insensitively against a command's `description` and
#: `notes`; the matching field is then quoted VERBATIM in the reference. The list is a
#: classifier for the report's summary table only — it never rewrites a sentence.
LIMITATION_MARKERS = (
    "no execution loop",
    "no loop is wired",
    "no loop",
    "not wired",
    "no live",
    "in-process",
    "in process",
    "dry-run",
    "dry run",
    "not real-world",
    "never a pass",
    "only lists",
    "no desktop",
    "no real action",
    "fixture",
    "no run store",
)

#: Layer order used for the command chapters. The contract's own `layer` vocabulary is
#: ACT, COMPOSE, ENFORCE, EXPAND, PERSIST, ROUTE, VERIFY plus `kernel` for the
#: cross-cutting commands; SUPERVISE exists as a layer but owns no command today.
LAYER_ORDER = (
    "EXPAND",
    "ACT",
    "COMPOSE",
    "ROUTE",
    "SUPERVISE",
    "ENFORCE",
    "VERIFY",
    "PERSIST",
    "kernel",
)

REQUIRED_COMMAND_KEYS = (
    "command",
    "layer",
    "description",
    "exit_codes",
    "mutates",
    "requires_credentials",
    "data_available",
    "data_schema",
    "default_evidence",
    "notes",
    "group",
    "args",
)


# --------------------------------------------------------------------------- #
# collecting the facts
# --------------------------------------------------------------------------- #


def _run_json(cli: list[str], argv: list[str], env: dict | None) -> tuple[dict | None, int, str]:
    """Run the CLI and return (parsed envelope, exit code, raw output for the error path)."""
    proc = subprocess.run(
        [*cli, *argv],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    raw = proc.stdout.strip()
    if not raw:
        raw = proc.stderr.strip()
    try:
        return json.loads(raw), proc.returncode, raw
    except json.JSONDecodeError:
        return None, proc.returncode, raw[:2000]


def _exit_vocabulary() -> tuple[dict[int, str], dict[int, str], str]:
    """The exit-code names + `error.code` strings, read from the module that defines them.

    Returns (names, error codes, provenance). If `godsv` is not importable (the generator was
    started with the wrong interpreter) the vocabulary is derived from the dump's own
    `exit_codes` numbers with a `UNKNOWN-<n>` name, and the provenance line says so.
    """
    try:
        sys.path.insert(0, str(REPO_ROOT / "src"))
        from godsv import cli_contract as cc  # noqa: PLC0415

        return (
            dict(cc.EXIT_NAMES),
            dict(cc.EXIT_ERROR_CODES),
            "read from `godsv.cli_contract.EXIT_NAMES` / `.EXIT_ERROR_CODES`",
        )
    except Exception:  # noqa: BLE001 — a wrong interpreter must not kill the generator
        return {}, {}, "NOT AVAILABLE (run with .venv/bin/python) — names derived from the dump"


def _contract_command_count() -> int | None:
    """``len(cli_contract.COMMANDS)`` *after* the parser is built.

    The hook modules install their own entries from ``register_cli(sub)``, so the
    table is only complete once ``cli.build_parser()`` has run — reading it cold
    counts the literal entries alone and would report a false mismatch against the
    dump (measured: 126 of 220 before the parser was built).
    """
    try:
        sys.path.insert(0, str(REPO_ROOT / "src"))
        from godsv import cli, cli_contract as cc  # noqa: PLC0415

        cli.build_parser()
        return len(cc.COMMANDS)
    except Exception:  # noqa: BLE001
        return None


def _cell(value: object) -> str:
    """A markdown table cell: no raw pipes, no newlines."""
    text = "" if value is None else str(value)
    text = text.replace("|", "\\|").replace("\n", " ").strip()
    return text or "—"


def _code(code: int) -> str:
    return f"{code}"


def _fmt_default(value: object) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _arg_type(arg: dict) -> str:
    as_type = arg.get("type") or "—"
    if arg.get("positional"):
        return f"{as_type} (positional)"
    return str(as_type)


def _schema_block(schema: object) -> list[str]:
    if not isinstance(schema, dict) or schema.get("type") == "null":
        desc = ""
        if isinstance(schema, dict):
            desc = str(schema.get("description", ""))
        line = "No structured payload: `data` is `null` in the envelope"
        if desc:
            line += f" — {desc}"
        return [line]
    body = json.dumps(schema, indent=2, ensure_ascii=False, sort_keys=False)
    return ["```json", body, "```"]


def _has_limitation(command: dict) -> list[tuple[str, str]]:
    """[(field, verbatim text)] for every field that declares a limitation."""
    hits: list[tuple[str, str]] = []
    for field in ("description", "notes"):
        text = str(command.get(field) or "")
        if not text:
            continue
        low = text.lower()
        if any(marker in low for marker in LIMITATION_MARKERS):
            hits.append((field, text))
    return hits


def _slug(command: str) -> str:
    return "#" + re.sub(r"[^a-z0-9]+", "-", command.lower()).strip("-")


def _fingerprint(data: dict) -> str:
    """A stable hash of the contract inventory this file documents.

    Everything inside `data` is deterministic (versions, counts, per-command records), so the
    hash changes exactly when the CLI contract changes — not when a probe happened to be a few
    milliseconds slower. `--check` compares this against the marker stored in the file.
    """
    import hashlib  # noqa: PLC0415

    canonical = json.dumps(data, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


FINGERPRINT_RE = re.compile(r"inventory-sha256:\s*([0-9a-f]{64})")

#: The marker line written into the generated file; `--check` compares the stored hash with a
#: fresh one instead of diffing text (probe timings and the generation time always differ).
FINGERPRINT_MARKER = "inventory-sha256"


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #


def render(
    envelope: dict,
    *,
    cli_repr: str,
    dump_exit: int,
    selftest: dict | None,
    selftest_exit: int | None,
    selftest_cli_repr: str,
    generated_at: str,
) -> str:
    data = envelope["data"]
    commands: list[dict] = data["commands"]
    names, error_codes, exit_provenance = _exit_vocabulary()
    contract_count = _contract_command_count()

    groups: dict[str, list[dict]] = {}
    for command in commands:
        raw_group = str(command.get("group") or "")
        groups.setdefault(raw_group, []).append(command)

    def group_label(raw: str) -> str:
        return raw if raw else "(top level)"

    def group_key(item: tuple[str, list[dict]]) -> tuple[int, str]:
        name = item[0]
        layers = {str(c.get("layer")) for c in item[1]}
        if len(layers) == 1 and next(iter(layers)) in LAYER_ORDER:
            return (LAYER_ORDER.index(next(iter(layers))), name)
        return (len(LAYER_ORDER), name)

    ordered_groups = sorted(groups.items(), key=group_key)

    out: list[str] = []
    add = out.append

    # ---- header / provenance ------------------------------------------------ #
    add("# Skaldir CLI (`godsvoice`) — generated command reference")
    add("")
    add(f"<!-- {FINGERPRINT_MARKER}: {_fingerprint(data)} -->")
    add("")
    add("> **Generated file — do not edit by hand.** Every command, argument, JSON schema,")
    add("> exit code and limitation sentence below is copied from the CLI's own contract dump.")
    add("")
    add("| Fact | Value |")
    add("|---|---|")
    add(f"| Generator | `tools/gv_cli_reference.py` |")
    add(
        "| Regenerate | `.venv/bin/python tools/gv_cli_reference.py "
        "--out docs/CLI-REFERENCE.md` |"
    )
    add("| Staleness gate | `.venv/bin/python tools/gv_cli_reference.py --check` |")
    add(f"| Inventory fingerprint | `sha256:{_fingerprint(data)}` |")
    add(f"| Source command | `{cli_repr} contract dump --json` (exit {dump_exit}) |")
    add(f"| `godsvoice_version` | {_cell(data.get('godsvoice_version'))} |")
    add(f"| contract `schema_version` | {_cell(data.get('schema_version'))} |")
    add(f"| Generated at (UTC) | {generated_at} |")
    add(
        f"| Inventory | {_cell(data.get('root_commands'))} root · "
        f"{_cell(data.get('leaf_commands'))} leaf commands · "
        f"{_cell(data.get('parser_nodes'))} parser nodes |"
    )
    unregistered = data.get("unregistered") or []
    add(
        "| Unregistered parser nodes | "
        f"{len(unregistered)}"
        + (f" — {_cell(', '.join(map(str, unregistered)))}" if unregistered else "")
        + " |"
    )
    if contract_count is not None:
        same = "matches" if contract_count == len(commands) else "**MISMATCH**"
        add(f"| Cross-check `cli_contract.COMMANDS` | {contract_count} entries — {same} the dump |")
    add(f"| Exit-code vocabulary | {exit_provenance} |")
    add("")
    add(
        "The dump is the GUI's source of truth as well: the desktop panel renders forms and "
        "tables from the same records, so this file and the GUI cannot disagree about a "
        "command's arguments."
    )
    add("")

    # ---- envelope ----------------------------------------------------------- #
    add("## 1. The envelope")
    add("")
    add(
        "`godsvoice contract dump` is itself an ordinary command: its `--json` output *is* the "
        "envelope. The field list below is the top-level key list of the dump that produced "
        "this file, with the JSON type observed in that document."
    )
    add("")
    add("| Field | Type in this dump |")
    add("|---|---|")
    for key in envelope:
        value = envelope[key]
        if value is None:
            kind = "null"
        elif isinstance(value, bool):
            kind = "boolean"
        elif isinstance(value, (int, float)):
            kind = "number"
        elif isinstance(value, str):
            kind = "string"
        elif isinstance(value, list):
            kind = f"array ({len(value)})"
        elif isinstance(value, dict):
            kind = "object"
        else:  # pragma: no cover — the envelope is JSON, so this cannot happen
            kind = type(value).__name__
        add(f"| `{key}` | {kind} |")
    add("")
    add("Rules the module `src/godsv/cli_contract.py` *enforces* rather than documents (its")
    add("own header, lines 18–24):")
    add("")
    add('- a success envelope without evidence is downgraded to exit `7` — "no command reports')
    add('  success it cannot back" (`build_envelope`);')
    add("- `--json` never mixes text and JSON on stdout (human lines move to stderr);")
    add("- `selftest` reports a layer that is not wired as `ok: false` with a `blocked_reason` —")
    add("  never as reachable.")
    add("")
    add(
        "Beyond the four flags every command gets for free (`--json`, `--format "
        "{text,json,jsonl}`, `--quiet`, `--no-color`), a command declares its own arguments; "
        "`GODSV_FORMAT` is the environment fallback for `--format`."
    )
    add("")

    # ---- exit codes --------------------------------------------------------- #
    add("## 2. Exit codes")
    add("")
    add(f"Vocabulary source: {exit_provenance}. One meaning per code, one `error.code` per code.")
    add("")
    add("| Code | Name | `error.code` | Declared by (commands) |")
    add("|---|---|---|---|")
    by_code: dict[int, list[str]] = {}
    for command in commands:
        for code in command.get("exit_codes") or []:
            by_code.setdefault(int(code), []).append(str(command["command"]))
    for code in sorted(set(by_code) | set(names) | set(error_codes)):
        name = names.get(code, f"UNKNOWN-{code}")
        err = error_codes.get(code, "—")
        declared = by_code.get(code, [])
        add(f"| `{_code(code)}` | {name} | `{err}` | {len(declared)} |")
    add("")
    for code in sorted(set(by_code) | set(names) | set(error_codes)):
        name = names.get(code, f"UNKNOWN-{code}")
        declared = by_code.get(code, [])
        if declared:
            add(f"- **`{code}` {name}** — {', '.join(f'`{c}`' for c in declared)}")
        else:
            add(f"- **`{code}` {name}** — declared by no command in this inventory")
    add("")
    add(
        "argparse usage errors exit `1` (`ContractArgumentParser.error`), not `2`. A code that "
        "a command does not list is not part of that command's contract."
    )
    add("")

    # ---- layers ------------------------------------------------------------- #
    add("## 3. The eight layers and the command that proves each one")
    add("")
    if selftest is not None:
        sd = selftest.get("data", {})
        add(
            f"Source: `{selftest_cli_repr} selftest --json` (exit {selftest_exit}) — "
            f"runtime `{_cell(sd.get('runtime'))}`, overall `ok: {json.dumps(sd.get('ok'))}`."
        )
        add("")
        add("| Layer | Reachable | Exit | CLI commands that prove it | `blocked_reason` (verbatim) |")
        add("|---|---|---|---|---|")
        for layer in sd.get("layers") or []:
            cmds = ", ".join(f"`{c}`" for c in layer.get("cli_commands") or []) or "— none today —"
            add(
                f"| {_cell(layer.get('layer'))} | {_cell(layer.get('status'))} "
                f"(`ok: {json.dumps(layer.get('ok'))}`) | `{_cell(layer.get('exit_code'))}` | "
                f"{cmds} | {_cell(layer.get('blocked_reason'))} |"
            )
        add("")
        failed = sd.get("failed_layers") or sd.get("failed") or []
        if failed:
            add(f"**Layers not reachable in the measured run:** {_cell(', '.join(map(str, failed)))}")
            add("")
        add("Per-layer probes (the inner checks behind each verdict):")
        add("")
        for layer in sd.get("layers") or []:
            probes = layer.get("probes") or []
            if not probes:
                continue
            add(f"- **{_cell(layer.get('layer'))}**")
            for probe in probes:
                add(
                    f"  - `{_cell(probe.get('name'))}` — {_cell(probe.get('status'))} — "
                    f"{_cell(probe.get('detail'))} (`{_cell(probe.get('evidence_ref'))}`, "
                    f"{_cell(probe.get('duration_ms'))} ms)"
                )
        add("")
    else:
        add("No `selftest --json` run was available when this file was generated, so only the")
        add("layer each command *declares* is listed here.")
        add("")

    add("### Commands per layer (from the contract dump)")
    add("")
    add("| Layer | Commands |")
    add("|---|---|")
    per_layer: dict[str, list[str]] = {}
    for command in commands:
        per_layer.setdefault(str(command.get("layer")), []).append(str(command["command"]))
    for layer in sorted(per_layer, key=lambda n: (LAYER_ORDER.index(n) if n in LAYER_ORDER else 99, n)):
        add(f"| {layer} | {', '.join(f'`{c}`' for c in sorted(per_layer[layer]))} |")
    add("")

    # ---- index -------------------------------------------------------------- #
    add("## 4. Command index")
    add("")
    add(f"{len(commands)} leaf commands in {len(groups)} groups. `write` mutates runtime state;")
    add("`read` does not. Every command has `--json`.")
    add("")
    for group, items in ordered_groups:
        layer_set = sorted({str(c.get("layer")) for c in items})
        plural = "command" if len(items) == 1 else "commands"
        add(
            f"### `{group_label(group)}` — layer {', '.join(layer_set)} ({len(items)} {plural})"
        )
        add("")
        add("| Command | Access | Exit codes | Credentials | JSON payload | Description |")
        add("|---|---|---|---|---|---|")
        for command in sorted(items, key=lambda c: str(c["command"])):
            add(
                f"| [`{_cell(command['command'])}`]({_slug(str(command['command']))}) "
                f"| {'write' if command.get('mutates') else 'read'} "
                f"| {_cell(', '.join(str(x) for x in command.get('exit_codes') or []))} "
                f"| {'required' if command.get('requires_credentials') else 'no'} "
                f"| {'yes' if command.get('data_available') else 'no'} "
                f"| {_cell(command.get('description'))} |"
            )
        add("")

    # ---- per command -------------------------------------------------------- #
    add("## 5. Command reference")
    add("")
    add(
        "A command marked **declared limitation** carries a sentence in its own contract record "
        "that says the feature does not do the whole thing yet. Section 6 quotes those "
        "sentences verbatim."
    )
    add("")
    for group, items in ordered_groups:
        add(f"### Group `{group_label(group)}`")
        add("")
        for command in sorted(items, key=lambda c: str(c["command"])):
            name = str(command["command"])
            limits = _has_limitation(command)
            tag = " · **declared limitation**" if limits else ""
            add(f"#### `{name}` — {_cell(command.get('layer'))}{tag}")
            add("")
            add(f"{_cell(command.get('description'))}")
            add("")
            add("| | |")
            add("|---|---|")
            add(f"| Group | `{group_label(str(command.get('group') or ''))}` |")
            add(f"| Layer | {_cell(command.get('layer'))} |")
            add(
                f"| Runtime effect | {'mutates state' if command.get('mutates') else 'read-only'} |"
            )
            add(
                "| Credentials | "
                + ("required" if command.get("requires_credentials") else "not required")
                + " |"
            )
            add(f"| Registered in the parser | {_cell(command.get('registered'))} |")
            add(
                "| Compatibility top-level mirror | "
                + ("yes" if command.get("compat_top_level_keys") else "no")
                + " |"
            )
            codes = ", ".join(
                f"`{c}` ({names.get(int(c), f'UNKNOWN-{c}')})" for c in command.get("exit_codes") or []
            )
            add(f"| Exit codes | {codes or '—'} |")
            add("")
            args = command.get("args") or []
            if args:
                add("**Arguments**")
                add("")
                add("| Argument | Type | Required | Repeatable | Choices | Default | Help |")
                add("|---|---|---|---|---|---|---|")
                for arg in args:
                    add(
                        f"| `{_cell(arg.get('name'))}` | {_cell(_arg_type(arg))} "
                        f"| {'yes' if arg.get('required') else 'no'} "
                        f"| {'yes' if arg.get('repeatable') else 'no'} "
                        f"| {_cell(', '.join(map(str, arg.get('choices') or [])))} "
                        f"| `{_cell(_fmt_default(arg.get('default')))}` "
                        f"| {_cell(arg.get('help'))} |"
                    )
                add("")
            else:
                add("**Arguments:** none beyond the four contract flags.")
                add("")
            if args:
                add("**Envelope `data` schema**")
                add("")
            else:
                add("**Envelope `data` schema**")
                add("")
            out.extend(_schema_block(command.get("data_schema")))
            add("")
            evidence = command.get("default_evidence") or []
            if evidence:
                add("**Default evidence**")
                add("")
                for item in evidence:
                    note = f" — {_cell(item.get('note'))}" if item.get("note") else ""
                    add(f"- `{_cell(item.get('kind'))}`: `{_cell(item.get('ref'))}`{note}")
                add("")
            if command.get("notes"):
                add("**Contract note (verbatim)**")
                add("")
                for line in str(command["notes"]).splitlines():
                    add(f"> {line}")
                add("")

    # ---- declared limitations ---------------------------------------------- #
    add("## 6. Declared limitations (quoted from the contract records)")
    add("")
    add(
        "These are the places where a command's own contract record says it does not yet do the "
        "whole job. Nothing here is inferred from the source code or from a test run — each row "
        "is the command's `description` or `notes` field, copied unchanged."
    )
    add("")
    add("| Command | Field | Verbatim text |")
    add("|---|---|---|")
    limited = 0
    for command in sorted(commands, key=lambda c: str(c["command"])):
        for field, text in _has_limitation(command):
            limited += 1
            add(f"| `{_cell(command['command'])}` | `{field}` | {_cell(text)} |")
    if not limited:
        add("| — | — | No command declares a limitation today. |")
    add("")
    add(f"{limited} declared-limitation sentence(s) across {len(commands)} commands.")
    add("")
    no_payload = [
        str(c["command"])
        for c in sorted(commands, key=lambda c: str(c["command"]))
        if isinstance(c.get("data_schema"), dict) and c["data_schema"].get("type") == "null"
    ]
    add(f"**Commands that declare no structured payload yet (`data: null`), {len(no_payload)}:**")
    add("")
    add(", ".join(f"`{c}`" for c in no_payload) if no_payload else "— none —")
    add("")
    add("---")
    add("")
    add(
        "Generated by `tools/gv_cli_reference.py` from "
        f"`{cli_repr} contract dump --json`. Re-run it after any CLI change; `--check` fails when "
        "this file is stale (a hand-edit is a stale file by definition)."
    )
    add("")

    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate the CLI reference from the CLI's own contract dump."
    )
    parser.add_argument(
        "--cli",
        default=None,
        help="path to the godsvoice executable (default: .venv/bin/godsvoice, else godsvoice on PATH)",
    )
    parser.add_argument("--dump", default=None, help="use a saved contract dump instead of running the CLI")
    parser.add_argument(
        "--selftest", default=None, help="use a saved selftest report instead of running the CLI"
    )
    parser.add_argument("--no-selftest", action="store_true", help="do not run/probe selftest at all")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help=f"output file (default {DEFAULT_OUT})")
    parser.add_argument("--runtime", default=None, help="GODSV_HOME to use for the CLI runs")
    parser.add_argument(
        "--check",
        action="store_true",
        help="do not write; exit 1 when the file on disk differs from what would be generated",
    )
    args = parser.parse_args(argv)

    if args.dump:
        dump_doc = json.loads(Path(args.dump).read_text(encoding="utf-8"))
        dump_exit = 0
        cli_repr = f"<saved dump {args.dump}>"
    else:
        cli = args.cli or (
            str(REPO_ROOT / ".venv" / "bin" / "godsvoice")
            if (REPO_ROOT / ".venv" / "bin" / "godsvoice").exists()
            else "godsvoice"
        )
        env = dict(os.environ)
        if args.runtime:
            env["GODSV_HOME"] = args.runtime
        dump_doc, dump_exit, raw = _run_json([cli], ["contract", "dump", "--json"], env)
        cli_repr = cli
        if dump_doc is None:
            print(f"error: contract dump did not return JSON\n{raw}", file=sys.stderr)
            return 1

    if not isinstance(dump_doc, dict) or not dump_doc.get("ok"):
        print("error: the contract dump reports ok=false — refusing to document a broken contract", file=sys.stderr)
        return 1
    data = dump_doc.get("data") or {}
    commands = data.get("commands") or []
    if not commands:
        print("error: the contract dump carries no commands — refusing to write an empty reference", file=sys.stderr)
        return 1
    for command in commands:
        missing = [k for k in REQUIRED_COMMAND_KEYS if k not in command]
        if missing:
            print(
                f"error: command {command.get('command')!r} is missing {missing} — the dump is not "
                "the document this generator expects",
                file=sys.stderr,
            )
            return 1

    selftest_doc = None
    selftest_exit: int | None = None
    selftest_cli_repr = ""
    if args.selftest:
        selftest_doc = json.loads(Path(args.selftest).read_text(encoding="utf-8"))
        selftest_exit = selftest_doc.get("exit_code")
        selftest_cli_repr = f"<saved report {args.selftest}>"
    elif not args.no_selftest:
        cli = args.cli or (
            str(REPO_ROOT / ".venv" / "bin" / "godsvoice")
            if (REPO_ROOT / ".venv" / "bin" / "godsvoice").exists()
            else "godsvoice"
        )
        env = dict(os.environ)
        if args.runtime:
            env["GODSV_HOME"] = args.runtime
        selftest_doc, selftest_exit, _raw = _run_json([cli], ["selftest", "--json"], env)
        selftest_cli_repr = cli
        # exit 2 is the documented "a layer is not reachable" verdict, not a failure of the probe
        if selftest_doc is None or selftest_exit not in (0, 2):
            print(
                f"warning: selftest --json was unusable (exit {selftest_exit}) — the layer section "
                "falls back to the contract dump",
                file=sys.stderr,
            )
            selftest_doc = None

    text = render(
        dump_doc,
        cli_repr=cli_repr,
        dump_exit=dump_exit,
        selftest=selftest_doc,
        selftest_exit=selftest_exit,
        selftest_cli_repr=selftest_cli_repr,
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )

    out_path = Path(args.out)
    if args.check:
        if not out_path.exists():
            print(f"STALE: {out_path} does not exist", file=sys.stderr)
            return 1
        current = out_path.read_text(encoding="utf-8")
        stored = FINGERPRINT_RE.search(current)
        fresh = _fingerprint(data)
        if not stored:
            print(
                f"STALE: {out_path} carries no `{FINGERPRINT_MARKER}:` marker — it was not "
                "generated by this tool",
                file=sys.stderr,
            )
            return 1
        if stored.group(1) != fresh:
            print(
                f"STALE: {out_path} documents fingerprint {stored.group(1)[:12]}…, the CLI "
                f"contract now hashes to {fresh[:12]}… (run the generator again)",
                file=sys.stderr,
            )
            return 1
        documented = len(re.findall(r"^#### `", current, flags=re.MULTILINE))
        if documented != len(commands):
            print(
                f"STALE: {out_path} documents {documented} commands, the contract has "
                f"{len(commands)}",
                file=sys.stderr,
            )
            return 1
        print(f"ok: {out_path} is current ({len(commands)} commands, fingerprint {fresh[:12]}…)")
        return 0

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")
    print(
        f"wrote {out_path}: {len(commands)} commands, "
        f"{len(groups_count(commands))} groups, "
        f"{len(text.splitlines())} lines"
    )
    return 0


def groups_count(commands: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for command in commands:
        key = str(command.get("group") or "(none)")
        counts[key] = counts.get(key, 0) + 1
    return counts


if __name__ == "__main__":
    raise SystemExit(main())
