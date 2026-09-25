"""``skaldir telemetry …`` — the standard displays of a run, and their export.

Three verbs over :mod:`skaldir.telemetry`:

======================  ==========================================================
``telemetry show``      the record of one run: duration, turns, tool calls with
                        durations, tokens (provider-reported or labelled estimate),
                        tokens/second over a named window, context usage against the
                        compaction trigger, model/provider and the cost when a price
                        was declared. Rebuilt from the transcript, then filed beside
                        it as ``<run-id>.telemetry.json`` so a later call works even
                        when the transcript is gone.
``telemetry live``      the same numbers while the run is still writing: one line per
                        turn and the one-line footer, read from the transcript file
                        (the run is never touched). ``--follow`` waits for
                        ``run.finished``; the default replays what is already there.
``telemetry export``    the whole record as ``json``, ``jsonl`` (one line per turn plus
                        a ``run`` line) or ``csv``, to a file or to stdout (``--out -``).
======================  ==========================================================

The footer format the shell shows is ``model · ctx 34% · 1.2k tok/s · 02:14``; a leading
``~`` marks a number we estimated, and an unavailable part reads ``n/a`` instead of
disappearing. Honesty is not decoration here: an unpriced run reports ``cost.usd: null``
with the declaration that would fix it, never ``0.0``, and every estimate is listed in
``estimates`` while every missing number is listed in ``nulls`` with its reason.

Exit codes — the product CLI's ``cli_contract`` vocabulary::

    0   ok                 the record was built (a run that is still open is reported as
                           such, with the numbers it recorded so far)
    1   usage              argparse refused the call, or a flag value is unusable
    3   blocked            the transcript exists but holds nothing to report
    5   not found          no transcript and no filed record for that run id
    7   internal           the transcript could not be read, or the record not written

The group installs its own ``COMMANDS`` entries when it is registered, so the CLI owner
wires it with the usual two lines and nothing has to be pasted into ``cli_contract``::

    from .telemetry_cli import register_cli
    register_cli(sub)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from . import telemetry as T
from .cli_shared import (
    blocked,
    dump,
    envelope_mode,
    install_contract_entries,
    not_found,
    product_contract,
    share_common_flags,
    text_mode,
)

GROUP_HELP = "run telemetry: duration, tokens/second, context usage, cost, export"

SHOW_HELP = "one run's telemetry record (rebuilt from its transcript, then filed beside it)"
LIVE_HELP = "the per-turn lines and the shell footer of a run, live or replayed"
EXPORT_HELP = "write the record as json, jsonl or csv"

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_BLOCKED = 3
EXIT_NOT_FOUND = 5
EXIT_INTERNAL = 7

EXPORT_CHOICES = ("json", "jsonl", "csv")

_CONTRACT = product_contract()

#: The three ``COMMANDS`` entries, installed when the group is registered (idempotent).
CONTRACT_EVIDENCE: dict[str, tuple[dict, ...]] = {
    "telemetry.show": (
        {
            "ref": "src/skaldir/telemetry.py:build_record",
            "kind": "code",
            "note": "the record derived from the run's own transcript (read-only)",
        },
        {
            "ref": "src/skaldir/agent_loop.py",
            "kind": "code",
            "note": "the transcript: run header, request bodies, answers, tool durations",
        },
    ),
    "telemetry.live": (
        {
            "ref": "src/skaldir/telemetry.py:TelemetryWatcher",
            "kind": "code",
            "note": "tails the transcript file by byte offset; the run is never touched",
        },
        {
            "ref": "src/skaldir/telemetry.py:footer_line",
            "kind": "code",
            "note": "the one-line footer: model · ctx · tok/s · duration",
        },
    ),
    "telemetry.export": (
        {
            "ref": "src/skaldir/telemetry.py:export_text",
            "kind": "code",
            "note": "json / jsonl (per turn + run) / csv of the same record",
        },
    ),
}

CONTRACT_ENTRIES: dict[str, Any] = {}


def contract_entries() -> dict[str, Any]:
    """The ``cli_contract.COMMANDS`` entries for this group (built once, installed on wiring)."""
    global CONTRACT_ENTRIES
    if CONTRACT_ENTRIES:
        return CONTRACT_ENTRIES
    if _CONTRACT is None or not hasattr(_CONTRACT, "CommandSpec"):
        return {}
    spec = _CONTRACT.CommandSpec
    shared_properties = {
        "run_id": {"type": "string"},
        "duration_ms": {"type": ["integer", "null"]},
        "duration": {"type": "string"},
        "turns": {"type": "integer"},
        "tool_calls": {"type": "object"},
        "tokens": {"type": "object"},
        "tokens_per_second": {"type": "object"},
        "context": {"type": "object"},
        "cost": {"type": "object"},
        "model": {"type": ["string", "null"]},
        "provider": {"type": "object"},
        "footer": {"type": "string"},
        "estimates": {"type": "array"},
        "nulls": {"type": "array"},
        "warnings": {"type": "array"},
    }
    CONTRACT_ENTRIES = {
        "telemetry.show": spec(
            "PERSIST",
            CONTRACT_EVIDENCE["telemetry.show"],
            SHOW_HELP,
            exit_codes=(EXIT_OK, EXIT_USAGE, EXIT_BLOCKED, EXIT_NOT_FOUND, EXIT_INTERNAL),
            data_schema={
                "type": "object",
                "properties": shared_properties,
                "required": ["run_id", "turns", "tokens", "tokens_per_second", "context"],
            },
            notes=(
                "the transcript is the source of truth and the record is filed beside it as "
                "<run-id>.telemetry.json; an unpriced run reports cost.usd=null with the "
                "reason and a labelled estimate says so — `estimates` and `nulls` index both"
            ),
        ),
        "telemetry.live": spec(
            "PERSIST",
            CONTRACT_EVIDENCE["telemetry.live"],
            LIVE_HELP,
            exit_codes=(EXIT_OK, EXIT_USAGE, EXIT_BLOCKED, EXIT_NOT_FOUND, EXIT_INTERNAL),
            data_schema={
                "type": "object",
                "properties": {
                    **shared_properties,
                    "lines": {"type": "array"},
                    "followed": {"type": "boolean"},
                },
                "required": ["run_id", "lines"],
            },
            notes=(
                "read-only: the transcript is tailed, never written; a run still open is "
                "reported as running with the numbers recorded so far"
            ),
        ),
        "telemetry.export": spec(
            "PERSIST",
            CONTRACT_EVIDENCE["telemetry.export"],
            EXPORT_HELP,
            exit_codes=(EXIT_OK, EXIT_USAGE, EXIT_BLOCKED, EXIT_NOT_FOUND, EXIT_INTERNAL),
            mutates=True,
            data_schema={
                "type": "object",
                "properties": {
                    **shared_properties,
                    "format": {"type": "string"},
                    "path": {"type": "string"},
                    "bytes": {"type": "integer"},
                    "content": {"type": "string"},
                },
                "required": ["run_id", "format"],
            },
            notes=(
                "writes the export to --out (default: beside the transcript) or to stdout "
                "with --out -; in --json mode the export rides in data.content instead, so "
                "stdout stays one parseable document"
            ),
        ),
    }
    return CONTRACT_ENTRIES


def register_contract_entries() -> list[str]:
    """Add this group's ``COMMANDS`` entries (idempotent); returns the paths added.

    The contract is re-resolved here: the group may be registered after ``cli.py`` was
    imported, and a contract that only appears later must still receive the entries. With
    no contract module at all there is nothing to install — this module still runs.
    """
    global _CONTRACT
    resolved = product_contract()
    if resolved is None:
        return []
    _CONTRACT = resolved
    return install_contract_entries(contract_entries(), _CONTRACT)


# --------------------------------------------------------------------------- #
# shared plumbing                                                              #
# --------------------------------------------------------------------------- #


def _report(payload: dict[str, Any], rows: list[dict[str, str]], warnings: list[str]) -> None:
    """Hand the payload, the evidence and the warnings to the shared envelope."""
    if _CONTRACT is None or not hasattr(_CONTRACT, "set_data"):
        return
    try:
        _CONTRACT.set_data(payload)
        for row in rows:
            _CONTRACT.add_evidence(row["ref"], row["kind"], note=row["note"])
        for warning in warnings:
            _CONTRACT.warn(str(warning))
    except Exception:  # noqa: BLE001 — an envelope problem must not break a verb
        return


def _emit_json(payload: dict[str, Any]) -> None:
    """Print the payload when we own stdout; the product envelope prints it otherwise."""
    if not envelope_mode():
        dump(payload)


def _state_dir(args: argparse.Namespace) -> Path:
    """Where the runs live: ``<runtime>/agent`` — the loop group's own rule.

    ``--state-dir`` wins when given, so a caller can point at a run directory that was
    produced elsewhere (a CI workspace, a rotated copy) without inventing a runtime.
    """
    explicit = str(getattr(args, "state_dir", "") or "").strip()
    if explicit:
        return Path(explicit).expanduser()
    runtime = str(getattr(args, "runtime", "") or "").strip()
    if not runtime:
        from .registry import default_runtime_dir

        return default_runtime_dir() / "agent"
    return Path(runtime).expanduser() / "agent"


def _build_options(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "context_budget": getattr(args, "context_budget", None),
        "trigger_fraction": getattr(args, "trigger_fraction", None),
        "price_per_1k_tokens": getattr(args, "price_per_1k_tokens", None),
    }


def _resolve(args: argparse.Namespace) -> tuple[Path, str]:
    """``(transcript, run_id)`` or the not-found refusal this call deserves."""
    transcript = str(getattr(args, "transcript", "") or "").strip()
    run_id = str(getattr(args, "run_id", "") or "").strip()
    if transcript:
        return Path(transcript).expanduser(), run_id
    if not run_id:
        raise T.TelemetryError(
            "give a run: `telemetry show <run-id>` (with --state-dir when the run lives "
            "elsewhere), or --transcript <file>",
            code="usage",
        )
    return T.resolve_run_path(_state_dir(args), run_id), run_id


def _record_for(args: argparse.Namespace) -> tuple[T.TelemetryRecord, list[str], str]:
    """The record plus what the caller should be told about how it was obtained."""
    path, run_id = _resolve(args)
    record, notes = T.show_record(
        transcript=path,
        state_dir=_state_dir(args),
        run_id=run_id,
        persist=not bool(getattr(args, "no_persist", False)),
        session_id=str(getattr(args, "session", "") or ""),
        **_build_options(args),
    )
    return record, notes, str(path)


def _evidence_for(record: T.TelemetryRecord) -> list[dict[str, str]]:
    rows = [
        {
            "ref": "src/skaldir/telemetry.py",
            "kind": "code",
            "note": "the record builder: every number carries its source",
        },
    ]
    if record.transcript:
        rows.append(
            {
                "ref": record.transcript,
                "kind": "file",
                "note": "the run transcript this record was derived from",
            }
        )
    if record.record_path:
        rows.append(
            {
                "ref": record.record_path,
                "kind": "file",
                "note": "the filed record (a later `telemetry show` works from it)",
            }
        )
    return rows


def _exit_for(exc: T.TelemetryError) -> int:
    code = str(getattr(exc, "code", "internal"))
    if code == "usage":
        return EXIT_USAGE
    if code == "not_found":
        return EXIT_NOT_FOUND
    if code == "blocked":
        return EXIT_BLOCKED
    return EXIT_INTERNAL


def _refuse(exc: T.TelemetryError, *, hint: str = "") -> int:
    """Report a refusal through the shared code helpers, with its own reason named."""
    message = str(exc)
    code = str(getattr(exc, "code", "internal"))
    if code == "not_found":
        return not_found(message, hint=hint or "the run id must be one `telemetry live` or "
                                              "`agent run` printed")
    if code == "blocked":
        return blocked(message, reason="empty-transcript", hint=hint)
    sys.stderr.write(f"skaldir telemetry: {message}\n")
    return _exit_for(exc)


# --------------------------------------------------------------------------- #
# the verbs                                                                    #
# --------------------------------------------------------------------------- #


def cmd_show(args: argparse.Namespace) -> int:
    """One run's record, human or JSON."""
    try:
        record, notes, _path = _record_for(args)
    except T.TelemetryError as exc:
        return _refuse(exc)
    payload = record.to_dict()
    _report(payload, _evidence_for(record), [*record.warnings, *notes])
    if text_mode(args):
        for line in record.lines():
            print(line)
        for note in notes:
            print(f"# {note}", file=sys.stderr)
    else:
        _emit_json(payload)
    return EXIT_OK


def _silent(_line: str) -> None:
    """Swallow a line in ``--json`` mode: the document on stdout must stay parseable."""
    return None


def cmd_live(args: argparse.Namespace) -> int:
    """The per-turn lines and the footer — replayed, or followed while the run works."""
    try:
        path, run_id = _resolve(args)
    except T.TelemetryError as exc:
        return _refuse(exc)
    if not path.is_file():
        return not_found(
            f"no transcript at {path}",
            hint="start the run first (`skaldir agent run …`), then follow its run id",
            details={"path": str(path)},
        )
    options = _build_options(args)
    echo = print if (text_mode(args) or envelope_mode()) else _silent
    lines: list[str] = []
    if bool(getattr(args, "follow", False)):
        deadline = float(getattr(args, "timeout_s", 0.0) or 0.0)
        lines, watcher = T.follow(
            path,
            run_id=run_id,
            interval_s=float(getattr(args, "interval_s", 0.25) or 0.25),
            timeout_s=deadline,
            on_line=echo,
            **options,
        )
        record = watcher.record()
    else:
        watcher = T.TelemetryWatcher(path, run_id=run_id, **options)
        watcher.poll()
        lines = watcher.lines()
        for line in lines:
            echo(line)
        record = watcher.record()
    notes: list[str] = []
    if record is not None and not bool(getattr(args, "no_persist", False)):
        try:
            saved = T.save_record(record)
        except T.TelemetryError as exc:  # noqa: BLE001 — a report that cannot be filed
            notes.append(str(exc))
        else:
            notes.append(f"record written to {saved}")
    payload = {
        "run_id": run_id or path.stem,
        "transcript": str(path),
        "followed": bool(getattr(args, "follow", False)),
        "lines": lines,
        **(record.to_dict() if record is not None else {}),
    }
    _report(payload, _evidence_for(record) if record is not None else [], notes)
    if not text_mode(args):
        _emit_json(payload)
    for note in notes:
        print(f"# {note}", file=sys.stderr)
    return EXIT_OK


def cmd_export(args: argparse.Namespace) -> int:
    """Write the record as json / jsonl / csv — to a file, or to stdout with ``--out -``."""
    try:
        record, notes, _path = _record_for(args)
    except T.TelemetryError as exc:
        return _refuse(exc)
    fmt = str(getattr(args, "as_format", "json") or "json").strip().lower()
    try:
        text = T.export_text(record, fmt=fmt)
    except T.TelemetryError as exc:
        return _refuse(exc)
    out = str(getattr(args, "out", "") or "").strip()
    human = text_mode(args) or envelope_mode()
    target = ""
    content_in_payload = ""
    if out == "-":
        if human:
            sys.stdout.write(text)
        else:
            # `--json` owns stdout: the document must stay parseable, so the export rides
            # in the payload instead of being interleaved with the JSON below
            content_in_payload = text
    elif out:
        path = Path(out).expanduser()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        except OSError as exc:
            sys.stderr.write(f"skaldir telemetry: cannot write {path}: {exc}\n")
            return EXIT_INTERNAL
        target = str(path)
    else:
        target = str(Path(record.record_path).with_suffix(f".{fmt}")) if record.record_path else ""
        if not target:
            sys.stderr.write(
                "skaldir telemetry: this record has no transcript on disc — give --out <file>\n"
            )
            return EXIT_USAGE
        path = Path(target)
        try:
            path.write_text(text, encoding="utf-8")
        except OSError as exc:
            sys.stderr.write(f"skaldir telemetry: cannot write {path}: {exc}\n")
            return EXIT_INTERNAL
    payload = {
        "run_id": record.run_id,
        "format": fmt,
        "path": target,
        "bytes": len(text.encode("utf-8")),
        "written_to_stdout": out == "-" and human,
        "content": content_in_payload,
        **record.to_dict(),
    }
    _report(payload, _evidence_for(record), [*record.warnings, *notes])
    if not text_mode(args):
        _emit_json(payload)
    print(
        f"# exported {payload['bytes']} bytes of {fmt} to "
        f"{target or ('stdout' if human else 'the payload')}",
        file=sys.stderr,
    )
    return EXIT_OK


# --------------------------------------------------------------------------- #
# parser                                                                       #
# --------------------------------------------------------------------------- #


def _verb_args(parser: argparse.ArgumentParser, *, want_transcript: bool = True) -> None:
    """The flags every verb takes: which run, and what the measurement settings are."""
    parser.add_argument("run_id", nargs="?", default="", help="the run id (`agent run` printed it)")
    parser.add_argument("--state-dir", dest="state_dir", default="",
                        help="directory holding runs/ (default: <runtime>/agent)")
    parser.add_argument("--runtime", default="", help="runtime dir (default: the registry's)")
    if want_transcript:
        parser.add_argument("--transcript", default="",
                            help="read this transcript file instead of resolving a run id")
    parser.add_argument("--context-budget", dest="context_budget", type=int, default=None,
                        help="declared context window in tokens (default: the layer's "
                             "SKALDIR_CONTEXT_BUDGET, else 128000)")
    parser.add_argument("--trigger-fraction", dest="trigger_fraction", type=float, default=None,
                        help="compaction fires at this share of the budget (default 0.75)")
    parser.add_argument("--price-per-1k-tokens", dest="price_per_1k_tokens", type=float,
                        default=None,
                        help="declared price in USD per 1000 tokens; without one the cost is "
                             "null, never guessed")
    parser.add_argument("--no-persist", dest="no_persist", action="store_true",
                        help="do not file the record beside the transcript")
    parser.add_argument("--session", default="", help="record the evidence under this session id")


def _share_flags(parser: argparse.ArgumentParser) -> None:
    """The product CLI's shared flags plus ``--text`` (a group must declare its own)."""
    share_common_flags(parser)
    if not any("--text" in action.option_strings for action in parser._actions):
        parser.add_argument("--text", action="store_true", help="print the human view")


def _add_telemetry_verbs(subparsers: Any) -> None:
    p_show = subparsers.add_parser("show", help=SHOW_HELP, description=(
        "The telemetry record of one run, derived from its transcript: duration, turns, "
        "tool calls with their durations, prompt/completion tokens (the provider's own "
        "when it reported them, otherwise a labelled chars/4 estimate), tokens per second "
        "over the run's own generation window, context usage with the compaction trigger, "
        "model and provider, and the cost when a price was declared. The record is filed "
        "beside the transcript, so a later call still answers when the transcript is gone."
    ))
    _verb_args(p_show)
    _share_flags(p_show)
    p_show.set_defaults(func=cmd_show, command_path="telemetry.show")

    p_live = subparsers.add_parser("live", help=LIVE_HELP, description=(
        "The standard displays while the run is still going: one line per turn plus the "
        "footer `model · ctx 34% · 1.2k tok/s · 02:14`. Without --follow the transcript "
        "is replayed once; with --follow it is tailed until run.finished (or --timeout-s). "
        "The run is only read."
    ))
    _verb_args(p_live)
    p_live.add_argument("--follow", action="store_true",
                        help="keep reading until the run finishes (or the timeout)")
    p_live.add_argument("--interval-s", dest="interval_s", type=float, default=0.25,
                        help="poll interval while following (default 0.25 s)")
    p_live.add_argument("--timeout-s", dest="timeout_s", type=float, default=0.0,
                        help="stop following after this many seconds (default: wait for the run)")
    _share_flags(p_live)
    p_live.set_defaults(func=cmd_live, command_path="telemetry.live")

    p_export = subparsers.add_parser("export", help=EXPORT_HELP, description=(
        "The whole record as one document: `json` (the record), `jsonl` (one line per turn "
        "plus a `run` line) or `csv` (a `run` row then one row per turn). Written beside "
        "the transcript by default, to --out <file>, or to stdout with --out -."
    ))
    _verb_args(p_export)
    p_export.add_argument("--as", dest="as_format", choices=EXPORT_CHOICES, default="json",
                          help="export format (default json)")
    p_export.add_argument("--out", default="", help="target file, or - for stdout")
    _share_flags(p_export)
    p_export.set_defaults(func=cmd_export, command_path="telemetry.export")


def build_telemetry_parser(subparsers: Any) -> Any:
    """Add the ``telemetry`` command group to a CLI's ``subparsers`` handle."""
    parser = subparsers.add_parser(
        "telemetry",
        help=GROUP_HELP,
        description=(
            "Run telemetry, honestly labelled: duration, turns, tool-call durations, "
            "tokens (provider-reported or an estimate that says so), tokens per second "
            "over the run's own generation window, context usage against the compaction "
            "trigger, model/provider, and the cost when a price was declared. `show` "
            "reports one run, `live` prints a line per turn plus the shell footer while "
            "the run works, `export` writes the record as json, jsonl or csv."
        ),
    )
    sub = parser.add_subparsers(dest="telemetry_command", required=True)
    _add_telemetry_verbs(sub)
    return parser


def _existing_group(subparsers: Any, name: str) -> Any:
    """The group already registered under that name, or ``None`` — idempotent wiring.

    Kept as its own helper so a second ``register_cli`` call on the same handle hands the
    group back instead of letting argparse raise ``conflicting subparser: telemetry``.
    """
    registered = getattr(subparsers, "choices", None) or {}
    return registered.get(name)


def register_cli(subparsers: Any) -> Any:
    """The CLI envelope contract: ``register_cli(subparsers) -> parser``.

    Idempotent: a second call on the same handle returns the existing ``telemetry`` group
    instead of letting argparse raise ``conflicting subparser``, and the contract entries
    are installed either way — a wired-in group never ships without them.
    """
    group = _existing_group(subparsers, "telemetry")
    if group is None:
        group = build_telemetry_parser(subparsers)
    register_contract_entries()
    return group


def build_parser() -> argparse.ArgumentParser:
    """A standalone parser with just this group's verbs (tests / ``python -m``)."""
    parser = argparse.ArgumentParser(prog="skaldir telemetry",
                                     description="run telemetry: duration, speed, context, cost")
    sub = parser.add_subparsers(dest="command", required=True)
    _add_telemetry_verbs(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    """``skaldir telemetry …`` standalone — returns the exit code, never raises for a request."""
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse owns --help and usage errors
        return EXIT_OK if not exc.code else EXIT_USAGE
    handler = getattr(args, "func", None)
    if handler is None:
        parser.print_help()
        return EXIT_USAGE
    try:
        return int(handler(args))
    except T.TelemetryError as exc:  # a refusal that reached the top is still a refusal
        return _refuse(exc)
    except BrokenPipeError:  # `… live --follow | head` must not traceback
        return EXIT_OK


__all__ = [
    "CONTRACT_ENTRIES",
    "EXIT_BLOCKED",
    "EXIT_INTERNAL",
    "EXIT_NOT_FOUND",
    "EXIT_OK",
    "EXIT_USAGE",
    "EXPORT_CHOICES",
    "build_parser",
    "build_telemetry_parser",
    "cmd_export",
    "cmd_live",
    "cmd_show",
    "contract_entries",
    "main",
    "register_cli",
    "register_contract_entries",
]


if __name__ == "__main__":  # pragma: no cover — `python -m skaldir.telemetry_cli`
    raise SystemExit(main())
