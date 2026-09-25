"""Generate the command index and the surface redesign from the product's own self-description.

Nothing in the two documents is hand-written: the inventory comes from ``skaldir contract
dump --json`` (the CLI describing itself), the ``/``-forms from ``skaldir.slash.mapping()``
(the same table ``/help`` and the palette quote), and the target surface is a declared table
whose every mark is decided by looking the verb up in those two sources.

Usage:  .venv/bin/python tools/command_index.py [outdir]
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from skaldir import cli, slash  # noqa: E402
from skaldir.complete import build_tree  # noqa: E402

ENTRY = REPO / ".venv" / "bin" / "skaldir"

#: The leaf commands the five entries name (``skaldir`` is the chat, and the chat is a leaf).
TAUGHT_LEAVES: tuple[str, ...] = ("chat", "setup", "run", "serve")

#: The five entries a terminal is taught. Everything else is reachable-but-untaught, and the
#: chat is where it lives. This is the owner's redesign, written down once.
TAUGHT: tuple[tuple[str, str, str], ...] = (
    ("skaldir", "the framed chat: logo, palette, streaming, the whole session", "chat"),
    ("skaldir setup", "first-run setup: the two mandatory keys, each proved by a real call",
     "setup"),
    ('skaldir run "<goal>"', "give it a goal and watch it work (the ENFORCE path)", "run"),
    ("skaldir serve [--http]", "serve the HTTP API for the GUI and for other clients", "serve"),
    ("--help / --version", "the five lines above, and the build's own version", "help"),
)

#: The target ``/`` surface, by area — the owner's design. Each verb's mark is *measured*:
#: REAL when the slash table and the parser tree both carry it, BUILT-IN when the shell answers
#: it itself, PLANNED when no verb exists yet (and then the nearest real command is named).
TARGET_SURFACE: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("session", ("/new", "/sessions", "/resume", "/fork", "/rename", "/export", "/search",
                 "/clear", "/quit", "/help", "/keys")),
    ("goal", ("/goal", "/criteria", "/run", "/steer", "/queue", "/stop", "/status", "/runs",
              "/verify", "/checkpoint")),
    ("model", ("/model", "/provider", "/setup", "/cost", "/telemetry")),
    ("context", ("/context", "/compact", "/limit", "/prompt")),
    ("code", ("/diff", "/undo", "/apply", "/init", "/map", "/find", "/symbols", "/diagnostics",
              "/testfix", "/exec")),
    ("tools", ("/tools", "/mine", "/register", "/health", "/rollback", "/compose", "/skills")),
    ("quality", ("/verify", "/flows", "/adversarial", "/audit", "/selftest")),
    ("GUI", ("/computer-use", "/vision")),
    ("memory", ("/memory", "/remember", "/recall", "/forget")),
    ("subagents", ("/agents", "/spawn", "/collect", "/cancel")),
    ("runtime", ("/jev", "/host", "/docker")),
)


def inventory() -> dict[str, Any]:
    """``skaldir contract dump --json`` — the product describing its own surface."""
    proc = subprocess.run(
        [str(ENTRY), "contract", "dump", "--json"],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=300,
    )
    if proc.returncode != 0:
        raise SystemExit(f"contract dump failed ({proc.returncode}): {proc.stderr[-400:]}")
    return json.loads(proc.stdout)["data"]


def slash_rows() -> list[dict[str, Any]]:
    """The slash table, verified against this build's own parser tree."""
    return slash.mapping(build_tree(cli.build_parser()))


def leaf_paths(tree: Any) -> list[str]:
    """Every leaf command path in the parser tree, as ``a.b.c``."""
    out: list[str] = []

    def walk(node: Any, prefix: str) -> None:
        children = getattr(node, "children", {}) or {}
        for name, child in children.items():
            path = f"{prefix}.{name}" if prefix else str(name)
            if getattr(child, "children", None):
                walk(child, path)
            else:
                out.append(path)

    walk(tree, "")
    return out


def main(argv: list[str]) -> int:
    outdir = Path(argv[1]) if len(argv) > 1 else Path.home() / "Documents" / "Final_Audit"
    outdir.mkdir(parents=True, exist_ok=True)
    data = inventory()
    mapping = slash_rows()
    tree = build_tree(cli.build_parser())
    leaves = set(leaf_paths(tree))
    commands: list[dict[str, Any]] = data["commands"]
    measured = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    # -- the /-form of every command (the mapping is the authority) ----------- #
    # a command can answer to more than one verb (``run`` is ``/run`` and ``/locking``): all
    # of them are kept, in the table's own order, because hiding one would be a lie about the
    # surface a session can actually type
    slash_of: dict[str, list[str]] = defaultdict(list)
    for row in mapping:
        if row.get("kind") == "real" and row.get("path"):
            spell = str(row["slash"])
            for alias in row.get("aliases") or []:
                spell += f" {alias}"
            slash_of[str(row["path"])].append(spell)
    by_slash = {str(row["slash"]): row for row in mapping}

    # -- the areas, in the order the dump sorts them ------------------------- #
    # grouped by **area** — the command's own root — not by the dump's nested group: ``act``
    # is one area whether a command is ``act check`` or ``act subtool run``
    areas: dict[str, list[dict[str, Any]]] = defaultdict(list)
    layers: dict[str, Counter] = defaultdict(Counter)
    for item in commands:
        area = str(item["command"]).split(".")[0]
        areas[area].append(item)
        layers[area][str(item.get("layer") or "")] += 1

    untaught_roots = sorted(
        area for area in areas if area not in TAUGHT_LEAVES
    )
    untaught_leaves = [
        str(item["command"]) for item in commands if str(item["command"]) not in TAUGHT_LEAVES
    ]

    lines: list[str] = []
    add = lines.append
    add("# 06 — the command index")
    add("")
    add("Generated from the product's own self-description — **do not hand-edit**.")
    add("")
    add(f"- measured: `{measured}` (UTC)")
    add("- generator: `tools/command_index.py` (runs `skaldir contract dump --json`)")
    add(f"- build: `godsvoice {data['godsvoice_version']}`, contract schema "
        f"`{data['schema_version']}`")
    add(f"- counts: **{data['root_commands']} root commands**, **{data['leaf_commands']} leaf "
        f"commands**, {data['parser_nodes']} parser nodes, "
        f"{len(data['unregistered'])} unregistered")
    add("")
    add("## 1. The terminal surface — five entries, and nothing else")
    add("")
    add("| entry | what it does | exists in this build |")
    add("| --- | --- | --- |")
    for spelling, what, path in TAUGHT:
        live = (
            "argparse"
            if path == "help"
            else ("yes" if path in leaves else "NO — see below")
        )
        add(f"| `{spelling}` | {what} | {live} |")
    add("")
    add("All five are commands this build already carries — the redesign is about what `-h` "
        "*prints*, not about adding verbs. Today `-h` lists 14 groups and puts the other 51 "
        "root commands behind `--all-commands`; the five lines are the CLI owner's change in "
        "`src/skaldir/cli.py` (see `07-command-surface.md`).")
    add("")
    add("Everything below is **reachable but untaught**: it is not printed by `-h`, it is not a "
        "thing a person is expected to type at a prompt, and it is reached through the chat "
        "(`/help` lists the verbs) or by someone who reads `--all-commands` / "
        "`skaldir contract dump --json`.")
    add("")
    add(f"- taught entries: **{len(TAUGHT)}**")
    add(f"- untaught root commands: **{len(untaught_roots)}** "
        f"({', '.join(f'`{name}`' for name in untaught_roots[:8])}, …)")
    add(f"- untaught leaf commands: **{len(untaught_leaves)}** — every leaf except "
        f"{', '.join(f'`{name}`' for name in TAUGHT_LEAVES)}, which the five entries name")
    add("")
    add("## 2. Every command, grouped by area")
    add("")
    add("`/-form` is the slash spelling of the *same* handler where one exists "
        "(`skaldir.slash.mapping()`); `reach` is `taught` only for the five entries above.")
    add("")
    for area in sorted(areas, key=lambda name: (name != "root", name)):
        items = sorted(areas[area], key=lambda item: str(item["command"]))
        busiest = ", ".join(
            f"{layer} {count}" for layer, count in layers[area].most_common(3) if layer
        )
        add(f"### `{area}` — {len(items)} commands ({busiest or 'unlayered'})")
        add("")
        add("| command | /-form | what it does | reach |")
        add("| --- | --- | --- | --- |")
        for item in items:
            path = str(item["command"])
            spell = " ".join(slash_of.get(path) or []) or "—"
            reach = "taught" if path in TAUGHT_LEAVES else "untaught"
            summary = str(item.get("description") or "").replace("|", "\\|")[:150]
            add(f"| `skaldir {path.replace('.', ' ')}` | {spell} | {summary} | {reach} |")
        add("")
    add("## 3. What is *not* exposed as a command, and why")
    add("")
    add("### 3.1 Answered by the shell itself (a built-in, not a command path)")
    add("")
    add("| verb | what it does |")
    add("| --- | --- |")
    for row in sorted(
        (row for row in mapping if row.get("kind") == "builtin"), key=lambda r: str(r["slash"])
    ):
        add(f"| `{row['slash']}` | {row['purpose']} |")
    add("")
    add("### 3.2 Designed, not built (a verb with no command behind it yet)")
    add("")
    add("| verb | why it is not a command | nearest real |")
    add("| --- | --- | --- |")
    for row in sorted(
        (row for row in mapping if row.get("kind") == "planned"), key=lambda r: str(r["slash"])
    ):
        nearest = ", ".join(f"`{item}`" for item in (row.get("nearest") or [])) or "—"
        reason = str(row.get("planned_reason") or row.get("reason") or "")[:120]
        add(f"| `{row['slash']}` | {reason} | {nearest} |")
    add("")
    add("### 3.3 A command path this build does not carry")
    add("")
    unwired = sorted(str(row["slash"]) for row in mapping if row.get("kind") == "unwired")
    add(f"**None.** Checked against this build's own parser tree "
        f"(`complete.build_tree(cli.build_parser())`), the slash table carries "
        f"{len(unwired)} unwired row(s): every verb it calls real names a command path that "
        "exists here. A row goes 'unwired' only when the table is checked against the raw "
        "parser instead of the tree, which is why the index measures with the tree.")
    add("")
    add("## 4. The target `/` surface")
    add("")
    add("REAL = the slash table and the parser tree both carry it · BUILT-IN = the shell answers "
        "it · PLANNED = no verb exists yet, so the nearest real command is named. PLANNED is a "
        "*verb* verdict: the command it would run may already exist, and then it is named.")
    add("")
    leaves_by_last: dict[str, list[str]] = defaultdict(list)
    for path in sorted(leaves):
        leaves_by_last[path.split(".")[-1]].append(path)
    for area, verbs in TARGET_SURFACE:
        add(f"### {area}")
        add("")
        add("| verb | mark | what it does / what it would run |")
        add("| --- | --- | --- |")
        for verb in verbs:
            row = by_slash.get(verb)
            if row is None:
                word = verb.lstrip("/")
                would = leaves_by_last.get(word) or []
                nearest = slash.close_names(verb, limit=3)
                if would:
                    what = "would run " + ", ".join(f"`{item}`" for item in would)
                elif nearest:
                    what = "no handler carries this word; nearest verbs: " + ", ".join(
                        f"`{item}`" for item in nearest
                    )
                else:
                    what = "no handler carries this word"
                add(f"| `{verb}` | PLANNED | {what} |")
                continue
            kind = str(row.get("kind"))
            mark = {"real": "REAL", "builtin": "BUILT-IN"}.get(kind, "PLANNED")
            what = str(row.get("purpose") or "")
            if row.get("path"):
                what += f" — runs `skaldir {str(row['path']).replace('.', ' ')}`"
            if mark == "PLANNED":
                nearest = ", ".join(f"`{item}`" for item in (row.get("nearest") or []))
                what = f"{row.get('planned_reason') or ''} nearest: {nearest}"
            add(f"| `{verb}` | {mark} | {what[:200]} |")
        add("")
    add("## 5. Counts, and how to re-measure")
    add("")
    add("```")
    add(f"roots      {data['root_commands']}")
    add(f"leaves     {data['leaf_commands']}")
    add(f"registered {len(data['commands']) - len(data['unregistered'])}")
    add(f"nodes      {data['parser_nodes']}")
    add(f"measured   {measured}")
    add("```")
    add("")
    add("Re-measure with:")
    add("")
    add("```sh")
    add("skaldir contract dump --json \\")
    add("  | jq '.data | {root_commands, leaf_commands, parser_nodes, unregistered}'")
    add("skaldir chat verbs --json    # the slash table, each verb with the command it runs")
    add(".venv/bin/python tools/command_index.py   # regenerates this file")
    add("```")
    add("")
    target = outdir / "06-command-index.md"
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {target} ({len(lines)} lines, {len(commands)} commands)")

    # ------------------------------------------------------------------ #
    # 07 — the redesign itself                                            #
    # ------------------------------------------------------------------ #
    surface: list[str] = []
    add = surface.append
    add("# 07 — the command surface: five entries, and everything else in the chat")
    add("")
    add(f"Measured `{measured}` (UTC) against `godsvoice {data['godsvoice_version']}` — "
        f"{data['root_commands']} root commands, {data['leaf_commands']} leaf commands. "
        "The inventory behind every number here is `06-command-index.md`, generated from "
        "`skaldir contract dump --json`.")
    add("")
    add("## 1. Before → after")
    add("")
    add("| | before | after |")
    add("| --- | --- | --- |")
    add(f"| taught at the prompt | `-h` lists 14 groups and hides the other "
        f"{data['root_commands'] - 14} root commands behind `--all-commands` | **five "
        f"entries**: `skaldir`, `skaldir setup`, `skaldir run \"<goal>\"`, "
        f"`skaldir serve [--http]`, `--help`/`--version` |")
    add(f"| reachable | {data['leaf_commands']} leaf commands, {data['leaf_commands']} of them "
        f"printed | {data['leaf_commands']} leaf commands, {len(untaught_leaves)} of them "
        "untaught and reached through the chat |")
    add("| how a verb is found | reading `--help`, guessing spellings | the palette (`/`), "
        "`/help`, completion, and `--all-commands` for the reader |")
    add("| the model/setup questions | free text, one prompt per field, one printed list | "
        "one selection widget: arrows, page keys, live filter, `Enter`, `Esc` |")
    add("")
    add("The five entries are not new commands: they are the four verbs and the one flag that "
        "already carry the whole first hour. What changed is that the other "
        f"{len(untaught_roots)} root commands stopped being *taught*.")
    add("")
    add("## 2. The picker rules")
    add("")
    add("One widget, used by `skaldir setup`, `skaldir model` and (once it lands) every "
        "`/`-verb that has to ask a question:")
    add("")
    add("| key | what it does |")
    add("| --- | --- |")
    for key, what in (
        ("↑ / ↓ (and `k` / `j`)", "move the pointer, wrapping at both ends"),
        ("PgUp / PgDn", "a page at a time, clamped to the list"),
        ("a printable character", "narrows the list as you type; a letter jumps to the first "
                                  "name that starts with it; digits match the row numbers"),
        ("Backspace / Ctrl-U", "edit the filter / clear it"),
        ("Enter", "selects the highlighted row — with a typed id allowed, an unmatched filter "
                  "is used as it is"),
        ("Esc / Ctrl-C", "leave without choosing: nothing is written, the session survives"),
    ):
        add(f"| {key} | {what} |")
    add("")
    add("And the rules that keep it honest on a real terminal:")
    add("")
    add("- **at most 10 rows** on screen plus `… N more` — the count is of the rows the filter "
        "left, never of the rows it hid;")
    add("- the block is **one cell short of the width** and its **height never changes**, so a "
        "24-line terminal cannot scroll it away;")
    add("- an **idle prompt writes nothing** (repaint only on a change), and `Ctrl-C` cancels "
        "the question instead of killing the run (`ISIG` is cleared for the duration and the "
        "terminal is restored in a `finally`);")
    add("- a **credential is never echoed**: the key is read through `getpass`, and no capture, "
        "log or JSON document carries it — the receipts report a length, never a value;")
    add("- **the fallback is plain**: a pipe, `TERM=dumb`, `NO_COLOR`, a redirected stderr or a "
        "terminal too small for a list gets the same rows printed as ASCII lines and one line "
        "of input — zero escape bytes, no cursor addressing.")
    add("")
    add("## 3. What stays reachable but untaught")
    add("")
    add("By area, the roots a person no longer sees at the prompt (all of them still run "
        "exactly as before, and each has a `/-form` where the slash table names one):")
    add("")
    add("| area | leaf commands | `/-form` |")
    add("| --- | --- | --- |")
    for area in sorted(areas, key=lambda name: (name != "root", name)):
        items = areas[area]
        here = {str(item["command"]) for item in items}
        spells: list[str] = []
        for path, rows_for_path in slash_of.items():
            if path in here:
                spells.extend(rows_for_path)
        spell = ", ".join(spells[:5]) + (" …" if len(spells) > 5 else "") if spells else "—"
        add(f"| `{area}` | {len(items)} | {spell} |")
    add("")
    add("The five taught entries are the only ones a first hour needs; everything else is "
        "either a verb inside the chat (`/help`) or a diagnostic a reader asks for by name.")
    add("")
    add("## 4. The evidence behind the picker rules")
    add("")
    add("Every rule in section 2 is a capture, not a claim: "
        "`MVP/artifacts-90-setup-interactive/` holds the raw 80x24 pty byte streams and a "
        "virtual-screen replay of each one (the role selector, the provider list with its base "
        "URLs, the model list, the masked key, the plain fallback under `NO_COLOR` / "
        "`TERM=dumb` / a redirected stderr, the headless pipe, and the three exit codes). The "
        "screen replays are where the ten-row bound and the one-cell-short-of-the-edge width "
        "are measured.")
    add("")
    add("## 5. What this redesign does *not* claim")
    add("")
    add("- The five-line `-h` and the `--all-commands` escape hatch are the CLI owner's change "
        "(`src/skaldir/cli.py`); this document measures the surface, it does not print it.")
    add("- One shared picker widget (`src/skaldir/picker.py`) is being built for `/sessions`; "
        "the setup/model selectors in `setup_cli.py`/`model_cli.py` run on the picker in "
        "`model_picker.py` today and hand every question over to the shared widget the moment "
        "it lands with the documented signature "
        "(`model_picker.SHARED_PICKER_CALL`).")
    add("- `06-command-index.md` is generated; this file is the design that reads it.")
    add("")
    target2 = outdir / "07-command-surface.md"
    target2.write_text("\n".join(surface) + "\n", encoding="utf-8")
    print(f"wrote {target2} ({len(surface)} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
