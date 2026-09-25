#!/usr/bin/env python3
"""Refresh the numeric claims of ``README-CLI.md`` from the machine's own probes.

The README's §2 (first-run table), §4 (the eight layers) and §10 (the honest
gaps) carry *measured* numbers: selftest exit codes, layer verdicts, leaf/root
counts, the ``data: null`` list. When the tree moves those numbers rot. Re-deriving
them by hand drifts; hand-editing the README mid-wave has already produced
contradictions. This script re-runs the two probes the README itself declares
authoritative —::

    skaldir selftest --json        (§2 exit code, §4 layer table, §10 blocked list)
    skaldir contract dump --json   (§2 contract counts, §7 group table)

— and rewrites ONLY the numeric/prose rows those probes answer, keeping every
other paragraph byte-identical. The output is a **draft** (``docs/GV-README-DRAFT.md``);
the parent reviews it and copies it over ``README-CLI.md`` — this script never
touches the README itself (the parent reviews before any overwrite).

Usage::

    .venv/bin/python tools/gv_readme_refresh.py            # write the draft
    .venv/bin/python tools/gv_readme_refresh.py --print    # only show the diff
    .venv/bin/python tools/gv_readme_refresh.py --runtime DIR  # probe a specific runtime

The probes run the CLI the way a user would (`python -m skaldir <verb> --json`),
with ``PYTHONPATH`` stripped so the measurement can never resolve a sibling
checkout's copy (a leaked PYTHONPATH made a fixed bug look alive once).
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
README = REPO / "README-CLI.md"
DRAFT = REPO / "docs" / "GV-README-DRAFT.md"
ENV_CLEAN_VARS = ("GODSV_HOME", "HERMES_HOME", "SKALDIR_HOME")

EXIT_NAMES = {
    0: "0", 1: "1", 2: "2",
}
LAYER_KEYS = (
    "EXPAND", "ACT", "COMPOSE", "ROUTE", "SUPERVISE", "ENFORCE", "VERIFY", "PERSIST",
)
STAMP = time.strftime("%Y-%m-%d %H:%M")

def _clean_env(runtime: str | None, extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("PYTHONPATH",) and k not in ENV_CLEAN_ENV_LEAKS
    }
    if runtime:
        env["GODSV_HOME"] = runtime
        env["HERMES_HOME"] = str(Path(runtime).parent)
    env.update(extra or {})
    return env


ENV_CLEAN_ENV_LEAKS = frozenset({"GODSV_HOME", "HERMES_HOME", "SKALDIR_HOME", "TMPDIR", "SKALDIR_LLM_API_BASE", "GODSV_LLM_BASE_URL", "SKALDIR_LLM_BASE_URL", "GODSV_LLM_BASE_URL"})


def _run(argv: list[str], env: dict[str, str]) -> tuple[int, dict, str]:
    """``(exit_code, parsed json or {}, raw stdout)`` — probed as a user would."""
    proc = subprocess.run(
        argv,
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )
    try:
        data = json.loads(proc.stdout) if proc.stdout.strip() else {}
    except json.JSONDecodeError:
        data = {}
    return proc.returncode, data, proc.stdout


# --------------------------------------------------------------------------- #
# probes                                                                        #
# --------------------------------------------------------------------------- #


def probe(
    runtime: str | None, python: str
) -> dict:
    """Both probes in one dict: the machine's own numbers, honestly labelled."""
    selftest_argv = [python, "-m", "skaldir", "selftest", "--json"]
    if runtime:
        selftest_argv += ["--runtime", runtime]
    selftest_exit, selftest, selftest_raw = _run(selftest_argv, _clean_env(runtime))
    dump_argv = [python, "-m", "skaldir", "contract", "dump", "--json"]
    if runtime:
        dump_argv += ["--runtime", runtime]
    dump_exit, dump, dump_raw = _run(dump_argv, _clean_env(runtime))
    # the CLI wraps the dump payload in the envelope (`data` key); unwrap once so the
    ### rest of the script reads the flat shape it quotes (leaf_commands, commands, …)
    if isinstance(dump.get("data"), dict) and "leaf_commands" not in dump:
        dump = dump["data"]
    expected_selftest_exit = 0 if selftest.get("ok") else 2
    return {
        "stamp": STAMP,
        "runtime": runtime or "",
        "selftest_exit": selftest_exit,
        "selftest_expected_exit": selftest_exit,
        "selftest": selftest,
        "selftest_raw": selftest_raw,
        "dump_exit": dump_exit,
        "dump": dump,
        "dump_raw": dump_raw,
    }


# --------------------------------------------------------------------------- #
# what the README's rows say — and what the probes say back                     #
# --------------------------------------------------------------------------- #


def layer_rows(probe_result: dict) -> list[dict]:
    """One row per layer: layer, status, blocked_reason, proving commands."""
    layers = probe_result["selftest"].get("data", {}).get("layers") or []
    rows = []
    for layer in layers:
        rows.append(
            {
                "layer": layer["layer"],
                "status": layer.get("status") or ("reachable" if layer.get("ok") else "blocked"),
                "reason": layer.get("blocked_reason") or "",
                "commands": list(layer.get("cli_commands") or []),
                "exit_code": layer.get("exit_code"),
            }
        )
    return rows


def layer_the_reasons(probe_result: dict) -> str:
    """The mappable prose: layer → its real blocked_reason (Hermes never paraphrased)."""
    rows = layer_rows(probe_result)
    out = []
    for row in rows:
        if row["reason"]:
            out.append(f'* **{row["layer"]}** — {row["reason"]}')
        else:
            out.append(f'* **{row["layer"]}** — reachable')
    return "\n".join(out)


def groups_table(probe_result: dict) -> str:
    """The §7 group table from the dump (group → layer(s) → count → commands)."""
    commands = probe_result["dump"].get("commands") or []
    by_group: dict[str, list[dict]] = {}
    for item in commands:
        group = str(item.get("group") or "(top level)")
        by_group.setdefault(group, []).append(item)
    rows: list[str] = []
    for group in sorted(by_group):
        items = by_group[group]
        reads = sum(1 for item in items if not item.get("mutates"))
        writes = len(items) - reads
        layer_set = sorted({str(item.get("layer") or "kernel") for item in items})
        names = ", ".join(sorted(item["command"] for item in items))
        rows.append(
            f"| `{group}` | {', '.join(layer_set)} | {len(items)} | "
            f"{reads} read / {writes} write | {names} |"
        )
    return "\n".join(rows)


def inventory_line(probe_result: dict) -> str:
    dump = probe_result["dump"]
    return (
        f"{dump.get('leaf_commands')} leaf commands in {len({c.get('group') for c in dump.get('commands') or []})} groups "
        f"({dump.get('root_commands')} root groups; {dump.get('parser_nodes')} parser nodes, "
        f"{len(dump.get('unregistered') or [])} unregistered)"
    )


BLOCKED_PROSE = {
    "EXPAND": None,
    "ACT": (
        "no live desktop bridge: no CLI command drives a real window, click or keystroke.\n"
        "  `cu.demo` is an in-process fixture (it says so in `data.scope` and in a warning)"
    ),
    "COMPOSE": (
        "no composite tool can be created, validated or run from the CLI;\n"
        "  `tools compose` only ranks candidates"
    ),
    "ROUTE": (
        "Jev cannot decide: System-One is disabled in the runtime settings and no provider\n"
        "  adapter is wired into the service. `godsvoice jev selftest` also exits `1` by design while a\n"
        "  decision kind is unreachable"
    ),
    "SUPERVISE": None,
    "ENFORCE": None,
    "VERIFY": (
        "no command verifies against the real workspace. `eval` runs in-process episodes\n"
        "  and its own note says it is \"never a PASS authority\""
    ),
    "PERSIST": (
        "no run store (`runs.db`) has ever been written: no long-run record exists to\n"
        "  resume from. Events and memory are reachable, the run lifecycle is not"
    ),
}


def blocked_list(probe_result: dict) -> str:
    lines = []
    for row in layer_rows(probe_result):
        if row["status"] != "blocked":
            continue
        reason = row["reason"] or BLOCKED_PROSE.get(row["layer"]) or "blocked (the probe says so)"
        lines.append(f'* **{row["layer"]}** — {reason.splitlines()[0]}')
        for line in reason.splitlines()[1:]:
            lines.append(f"  {line.strip()}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# the README edits — row by row, nothing else moves                             #
# --------------------------------------------------------------------------- #


def _single(text: str, start: str, end: str) -> str:
    """The block between two unique markers (both markers stay)."""
    first = text.find(start)
    if first < 0:
        raise ValueError(f"anchor not found: {start!r}")
    second = text.find(end, first + len(start))
    if second < 0:
        raise ValueError(f"anchor not found: {end!r}")
    return text[first + len(start) : second]


def render_draft(current: str, probe_result: dict) -> str:
    """Rewrite ONLY the numeric/prose rows the probes answer; nothing else."""
    updated = current

    # -- the opener ("Read this first"): reachable layers, from the probe ----- #
    updated = _patch_opener(updated, probe_result)

    # -- the measured-date note ---------------------------------------------- #
    updated = _patch_date(updated, probe_result)

    # -- §2 the first-run table ---------------------------------------------- #
    updated = _patch_first_run_table(updated, probe_result)

    # -- §2 the count line (`contract list` → what the dump says today) ------- #
    updated = _patch_contract_list_line(updated, probe_result)

    # -- §4 the eight-layers table ------------------------------------------- #
    updated = _patch_layers_table(updated, probe_result)

    # -- §7 the inventory line + group table --------------------------------- #
    updated = _patch_groups_line(updated, probe_result)
    updated = _patch_group_table(updated, probe_result)

    # -- §10 the honest gaps ------------------------------------------------- #
    updated = _patch_gaps(updated, probe_result)
    return updated


def _patch_date(text: str, probe_result: dict) -> str:
    """The README's 'measured on YYYY-MM-DD' stamps: today, with the probe source."""
    stamp_today = probe_result["stamp"].split()[0]
    out = []
    for line in text.splitlines():
        if "measured on 2026-09-18 with" in line:
            line = line.replace("measured on 2026-09-18 with", f"measured on {stamp_today} with")
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def _patch_first_run_table(text: str, probe_result: dict) -> str:
    """The §2 measured table: one row per probe the README names."""
    stamp = probe_result["stamp"].split()[0]
    layers = layer_rows(probe_result)
    blocked = [row for row in layers if row["status"] != "reachable"]
    blocked_names = ", ".join(row["layer"] for row in blocked) or "none"
    selftest_exit = probe_result["selftest_exit"]
    open_ = "| Command | Result |\n|---|---|"
    body = (
        f"\n| `skaldir selftest --json` | exit **{selftest_exit}** — "
        f"{(len(layers) - len(blocked))}/{len(layers)} layers reachable"
        + (f" ({blocked_names} blocked — see §4)" if blocked else " (all eight reachable)")
        + " |\n"
    )
    start = text.find(open_)
    if start < 0:
        return text
    end = text.find("\n\n", start + len(open_))
    return text[: start] + open_ + body + text[end:]


def _patch_contract_list_line(text: str, probe_result: dict) -> str:
    dump = probe_result["dump"]
    needle = "contract list    # all "
    first = text.find(needle)
    if first < 0:
        return text
    end = text.find("\n", first)
    line = f"{needle}{dump.get('leaf_commands')} leaf commands with layer, --json, read/write"
    return text[:first] + line + text[end:]


def _patch_layers_table(text: str, probe_result: dict) -> str:
    """§4's table: one row per layer from the machine's own verdict."""
    open_ = "| Layer | Measured | Proving command(s) | What it means when it reports blocked |"
    first = text.find(open_)
    if first < 0:
        return text
    end = text.find("\n\n", first)
    rows = layer_rows(probe_result)
    table = [open_, "|---|---|---|---|"]
    for row in rows:
        state = (
            "**reachable**" if row["status"] == "reachable" else f"`blocked` (exit {row['exit_code']})"
        )
        commands = ", ".join(f"`{item}`" for item in row["commands"][:8]) or "—"
        if commands != "—" and len(row["commands"]) > 8:
            commands += ", …"
        reason = (row["reason"] or "—").splitlines()[0][:160]
        table.append(f"| **{row['layer']}** | {state} | {commands} | {reason} |")
    return text[:first] + "\n".join(table) + text[end:]


def _patch_groups_line(text: str, probe_result: dict) -> str:
    """The §7 inventory line (counts only)."""
    wanted = inventory_line(probe_result)
    before, after = "73 leaf commands in ", ":"
    first = text.find(before)
    if first < 0:
        return text
    end = text.find(after, first)
    return text[:first] + wanted + text[end:]


def _patch_group_table(text: str, probe_result: dict) -> str:
    open_ = "| Group | Layer | # | Access | Commands |"
    first = text.find(open_)
    if first < 0:
        return text
    end = text.find("\n\n", first)
    table = [open_, "|---|---|---|---|---|", groups_table(probe_result)]
    return text[:first] + "\n".join(table) + text[end:]


def _patch_opener(text: str, probe_result: dict) -> str:
    """The 'Read this first' paragraph: reachable/blocked layers, from the probe only.

    The paragraph is a *measured claim* (which layers answer today, which refuse) — it
    rots exactly like the tables, so it is patched from the same probe, never by hand.
    """
    rows = layer_rows(probe_result)
    reachable = [row["layer"] for row in rows if row["status"] == "reachable"]
    blocked = [row["layer"] for row in rows if row["status"] != "reachable"]
    exit_code = probe_result["selftest_exit"]
    stamp = probe_result["stamp"].split()[0]
    opener_start = text.find("**Read this first, it is the honest part:**")
    if opener_start < 0:
        return text
    end = text.find("\n\n", opener_start)
    if blocked:
        replacement = (
            f"**Read this first, it is the honest part:** of the eight layers, "
            f"**{len(reachable)} are reachable today** ({', '.join(reachable) or 'none'}). "
            f"{', '.join(blocked)} report `blocked` with a reason of their own — measured "
            f"on {stamp} with `godsvoice selftest --json` (exit {exit_code}). Section 4\n"
            "lists every layer with the command that probes it, and section 10 lists every "
            "gap we know about.\n"
            "Nothing in this file describes a command that does not exist: the "
            "machine-readable reference in\n[`docs/CLI-REFERENCE.md`](docs/CLI-REFERENCE.md) "
            "is generated from the CLI itself."
        )
    else:
        replacement = (
            f"**Read this first, it is the honest part:** all eight layers are reachable "
            f"today — measured on {stamp} with `godsvoice selftest --json` (exit "
            f"{exit_code}). Section 4\nlists every layer with the command that probes it, "
            "and section 10 lists every gap we know about.\n"
            "Nothing in this file describes a command that does not exist: the "
            "machine-readable reference in\n[`docs/CLI-REFERENCE.md`](docs/CLI-REFERENCE.md) "
            "is generated from the CLI itself."
        )
    return text[:opener_start] + replacement + text[end:]


def _patch_gaps(text: str, probe_result: dict) -> str:
    marker = "**Layers that report blocked (measured, `selftest --json` exit 2):**"
    first = text.find(marker)
    if first < 0:
        return text
    end = text.find("**Gaps in the CLI surface itself:**", first)
    blocked = layer_rows(probe_result)
    exclusions = [row for row in blocked if row["status"] != "reachable"]
    if not exclusions:
        replacement = (
            "**Layers that report blocked (measured, `selftest --json`):**\n\n"
            f"* none — measured on this machine at {probe_result['stamp']}: all eight layers "
            "probe reachable. Re-measure before believing this row\n"
        )
    else:
        replacement = marker + "\n\n" + blocked_list(probe_result) + "\n\n"
    return text[:first] + replacement + text[end:]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runtime", default="", help="probe this runtime dir (default: the resolved one)")
    parser.add_argument("--python", default=str(REPO / ".venv" / "bin" / "python"))
    parser.add_argument("--print", action="store_true", dest="show", help="print the diff, write nothing")
    args = parser.parse_args(argv)

    if not README.is_file():
        print(f"no README-CLI.md at {README}", file=sys.stderr)
        return 1
    current = README.read_text(encoding="utf-8")
    result = probe(args.runtime or None, args.python)
    draft = render_draft(current, result)
    if args.show:
        for line in difflib.unified_diff(
            current.splitlines(), draft.splitlines(), "README-CLI.md", "draft", lineterm=""
        ):
            print(line)
        return 0
    target = REPO / "docs" / "GV-README-DRAFT.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(draft, encoding="utf-8")
    print(
        f"written: {target} ({STAMP}; selftest exit {result['selftest_exit']}, "
        f"{result['dump'].get('leaf_commands')} / {result['dump'].get('root_commands')} leaves/roots)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
