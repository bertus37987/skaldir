"""The first-run screen — what a fresh start shows, and what it may **not** claim.

Three pieces, specified in ``MVP/64-first-run.md`` §3 and derived from the runtime rather
than typed into a string:

* :func:`logo_lines` — the art the bare call prints, from the one art module
  (:mod:`skaldir.logo`). Never a second drawing.
* :func:`missing_provider_line` — ``no provider configured — run `skaldir setup```, and
  **only** when :func:`skaldir.setup_flow.detect_state` really reports
  ``degraded.needs_setup: true``. When both keys answer, the line is absent, not reworded.
  When the state cannot be read at all the answer is ``None`` (unknown), and an unknown
  state never prints the line and never claims the provider works.
* :func:`try_this_row` — three concrete inputs, each resolved against the real command
  tree / slash table before it is printed. A verb this build does not carry is dropped (or
  marked planned), never offered as a working example.

The module is presentation only: it probes, it formats, it never configures anything and
never writes.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

#: The one string for "the two mandatory keys are not usable yet" (MVP/64 §3).
MISSING_PROVIDER = "no provider configured — run `skaldir setup`"

#: The candidates for the ``try this`` row, in the order the spec asks for them. Each one
#: names the command path it needs, so the row can be generated instead of typed.
TRY_THIS_CANDIDATES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ('agent run "what does this repo do?"', ("agent.run",)),
    ("/goal", ("goal.show",)),
    ("/verify status", ("verify.status",)),
    ("/tools list", ("tools.list",)),
    ("/provider list", ("provider.list",)),
    ("/status", ("session.status",)),
)


def logo_lines(*, theme: Any = None, version: str = "") -> list[str]:
    """The startup signature: the art, its caption and the version line.

    Read from :mod:`skaldir.logo`, the same module the bare call renders through, so the
    shell cannot grow a second drawing. An art that cannot be read yields the plain word
    mark — a first start never dies for a missing asset.
    """
    try:
        from . import logo
    except Exception:  # noqa: BLE001 — a build without the art module still starts
        return ["  SKALDIR"]
    try:
        text = logo.splash(theme=theme, version=version)
    except Exception:  # noqa: BLE001 — a broken asset is not a reason to refuse to start
        return ["  SKALDIR"]
    lines = [line.rstrip() for line in str(text).splitlines()]
    while lines and not lines[-1].strip():
        lines.pop()
    return lines or ["  SKALDIR"]


def provider_state(
    *, runtime: str | Path | None = None, config_path: str | Path | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """The measured provider state, or an honest ``unknown``.

    Returns ``{"needs_setup": bool|None, "reason": str, "degraded": dict}``. ``None`` means
    the state could not be read — the caller then prints no missing-provider line, because
    an unread state is not evidence that something is missing (nor that it is present).
    """
    try:
        from .setup_flow import detect_state

        state = detect_state(runtime=runtime, config_path=config_path, env=env)
        degraded = dict(state.get("degraded") or {})
    except Exception as exc:  # noqa: BLE001 — a report that raises is worse than "unknown"
        return {
            "needs_setup": None,
            "reason": f"{type(exc).__name__}: {exc}"[:200],
            "degraded": {},
        }
    needs = degraded.get("needs_setup")
    if not isinstance(needs, bool):
        return {
            "needs_setup": None,
            "reason": "the setup state carried no degraded.needs_setup reading",
            "degraded": degraded,
        }
    return {
        "needs_setup": needs,
        "reason": "" if not needs else "degraded.needs_setup is true",
        "degraded": degraded,
    }


def missing_provider_line(
    *, runtime: str | Path | None = None, config_path: str | Path | None = None,
    env: Mapping[str, str] | None = None,
) -> str:
    """The line, or ``""`` when the two keys are usable (or the state is unknown)."""
    state = provider_state(runtime=runtime, config_path=config_path, env=env)
    if state["needs_setup"] is True:
        return MISSING_PROVIDER
    return ""


def _command_paths() -> set[str]:
    """Every command path this build really carries (the contract's own table)."""
    try:
        from . import cli as cli_module
        from . import cli_contract

        cli_module.build_parser()  # every group installs its own entries here
        return {str(path) for path in cli_contract.COMMANDS}
    except Exception:  # noqa: BLE001 — an unbuildable CLI carries no example
        return set()


def _slash_kinds(tree: Any = None) -> dict[str, tuple[str, str]]:
    """``{slash: (kind, command_path)}`` from :func:`skaldir.slash.mapping`.

    The ``kind`` alone is not enough: without a parser tree the table honestly says
    ``unverified`` for every command-backed verb, and the first-run row must still be
    resolvable. ``path`` carries the verb's command path, which the contract table can
    confirm — so a slash example resolves on either signal.
    """
    try:
        from . import slash

        rows = slash.mapping(tree) if tree is not None else slash.mapping()
    except Exception:  # noqa: BLE001 — no table, no slash examples
        return {}
    out: dict[str, tuple[str, str]] = {}
    for row in rows or []:
        slash_name = str(row.get("slash") or "")
        if slash_name:
            out[slash_name] = (str(row.get("kind") or ""), str(row.get("path") or ""))
    return out


def resolved_examples(
    *,
    tree: Any = None,
    limit: int = 3,
    candidates: Iterable[tuple[str, tuple[str, ...]]] | None = None,
) -> list[str]:
    """The first ``limit`` examples that resolve against *this* build, in spec order.

    A CLI example resolves when the command path it names is in the contract table; a slash
    example resolves when the slash table calls it ``real``/``builtin`` **or** its own
    command path is in that table. Anything else is dropped — the row is generated, so a
    planned verb cannot slip into it.
    """
    commands = _command_paths()
    kinds = _slash_kinds(tree)
    chosen: list[str] = []
    for example, needed in candidates or TRY_THIS_CANDIDATES:
        if example.startswith("/"):
            kind, path = kinds.get(example, ("", ""))
            if kind in ("real", "builtin") or (path and path in commands):
                chosen.append(example)
        elif all(path in commands for path in needed):
            chosen.append(example)
        if len(chosen) >= int(limit):
            break
    return chosen


def try_this_row(
    *, tree: Any = None, limit: int = 3, sep: str = " · ",
    paint: Callable[[str, str], str] | None = None,
) -> str:
    """``try this: <example> · <example> · <example>`` — or ``""`` when none resolves."""
    examples = resolved_examples(tree=tree, limit=limit)
    if not examples:
        return ""
    row = f"try this: {sep.join(examples)}"
    return paint(row, "dim") if callable(paint) else row


def screen_lines(
    *,
    tree: Any = None,
    runtime: str | Path | None = None,
    config_path: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    theme: Any = None,
    version: str = "",
    sep: str = " · ",
    paint: Callable[[str, str], str] | None = None,
    logo: bool = True,
) -> list[str]:
    """The three derived pieces in the order the spec prints them.

    The bars (MVP/64 §3 item 3) and the prompt belong to the shell, which already draws
    them; this returns the logo block, the honest missing-provider line (when it applies)
    and the generated ``try this`` row. Empty pieces are omitted, never blank-filled.
    """
    lines: list[str] = []
    if logo:
        lines.extend(logo_lines(theme=theme, version=version))
    missing = missing_provider_line(runtime=runtime, config_path=config_path, env=env)
    if missing:
        lines.append(f"  {missing}" if paint is None else f"  {paint(missing, 'warn')}")
    row = try_this_row(tree=tree, sep=sep, paint=paint)
    if row:
        lines.append(f"  {row}")
    return lines


def as_data(
    *,
    tree: Any = None,
    runtime: str | Path | None = None,
    config_path: str | Path | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """The first-run facts as data (the ``--json`` form of the same screen)."""
    state = provider_state(runtime=runtime, config_path=config_path, env=env)
    examples = resolved_examples(tree=tree)
    return {
        "needs_setup": state["needs_setup"],
        "state_reason": state["reason"],
        "degraded": state["degraded"],
        "missing_provider_line": MISSING_PROVIDER if state["needs_setup"] is True else "",
        "try_this": examples,
        "logo_rows": len(logo_lines()),
    }


__all__ = [
    "MISSING_PROVIDER",
    "TRY_THIS_CANDIDATES",
    "as_data",
    "logo_lines",
    "missing_provider_line",
    "provider_state",
    "resolved_examples",
    "screen_lines",
    "try_this_row",
]
