"""The data-built system prompt: capability awareness from the real toolset.

A model can only behave well with what it *knows* it has. The hardcoded prompt
was a small contract; this module keeps that contract and adds what the loop
actually offers this run — derived from the assembled :class:`ToolSet`, never
from a hardcoded capability list, so the prompt can never claim a tool the
run did not put in the request.

Categories are matched by wire-name pattern against the real tools array:

* file tools — the workspace-fenced builtins (``read_text``/``list_dir`` /
  ``sha256_file``/``write_text`` or whatever the toolset actually carries);
* expand — the support tools that classify + build new tools (real builds,
  honest gates);
* act — the tools that run actions (isolation-first; the honesty check tool;
  a live desktop act needs the on-screen approval, a deny persists);
* compose — the tool that validates composite pipelines without executing them;
* verify — the tools that judge real workspaces (``PASS``/``FAIL``/``UNKNOWN``
  verbatim; UNKNOWN is never success);
* spawn — delegation tools (≤3 children, depth 1, tests bounded);
* jev — one advisory line when the decision layer is armed (never obeyed).

Standard library and this package only. Nothing here executes a tool.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

__all__ = [
    "CORE_PROMPT_MARKERS",
    "SystemPrompt",
    "build_system_prompt",
    "has_non_ascii_words",
    "layer_capabilities",
]

#: The non-negotiable contract text fragments every variant must still carry.
#: The agent loop's fallback ``SYSTEM_PROMPT`` is built from these, so a test
#: on the constant and a test on a built prompt check the same guarantees.
CORE_PROMPT_MARKERS: tuple[str, ...] = (
    "Call a tool when you need information or an effect; never describe a call you did not make.",
    "a failed step is never a success",
    "Answer without a tool call only when the task is actually finished",
)

#: The core contract every prompt variant carries (English-only, product rule).
CORE_CONTRACT: tuple[str, ...] = (
    "You are Skaldir's agent loop. Work the task with the tools you are actually given.",
    "Call a tool when you need information or an effect; never describe a call you did not make.",
    "Read a tool error and correct your next call; a failed step is never a success.",
    "Answer without a tool call only when the task is actually finished. The answer must be true of what the tools returned.",  # noqa: E501
    "The harness records every call and may stop the run at a step, token, cost or time bound. If you are stopped, say plainly what is still open — a partial answer is never presented as done.",  # noqa: E501
    "If you are uncertain, say so instead of guessing; uncertainty is better than an invented result.",  # noqa: E501
)


def has_non_ascii_words(text: str) -> bool:
    """True when the prompt carries words outside the English-only rule.

    Guarantees the product's language rule at build time: any German chatter
    line (or any non-ASCII word) in a prompt build is a defect. Quoted
    identifiers the *user* supplied in the goal are tolerated — the check
    looks at the words the *builder* wrote, i.e. everything but the goal line.
    """
    return any(
        any(ord(ch) > 127 for ch in word)
        for word in text.split()
        if word.isalpha()
    )


def _names(tools: Iterable[Any]) -> list[str]:
    return [str(getattr(tool, "name", "") or "") for tool in tools]


@dataclass(frozen=True)
class SystemPrompt:
    """One built prompt — frozen so a build is a value, not a mutable state."""

    core: tuple[str, ...]
    capabilities: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    goal: str = ""
    jev: str = ""

    def text(self) -> str:
        """The full prompt bytes (deterministic for the same fields)."""
        parts = ["\n".join(self.core)]
        if self.goal:
            parts.append(f"## Current task\n{self.goal}")
        if self.capabilities:
            parts.append("\n".join(self.capabilities))
        if self.skills:
            parts.append("## Proven skills available\n" + "\n".join(self.skills))
        if self.jev:
            parts.append(self.jev)
        return "\n\n".join(part for part in parts if part)


def layer_capabilities(tools: Sequence[Any]) -> dict[str, list[Any]]:
    """Bucket the toolset's real tools into the capability categories.

    Every bucket is matched by what is actually in ``tools`` — a category with
    no tool is simply absent from the returned mapping, and the prompt text
    that reads this never claims it.
    """
    buckets: dict[str, list[Any]] = {}
    for tool in tools:
        name = str(getattr(tool, "name", "") or "")
        if not name:
            continue
        if name.startswith("skill_expand") or "expand" in name:
            buckets.setdefault("expand", []).append(tool)
        elif name.startswith("skill_act") or "act" in name:
            buckets.setdefault("act", []).append(tool)
        elif name.startswith("skill_compose") or "compose" in name:
            buckets.setdefault("compose", []).append(tool)
        elif name.startswith("skill_verify") or "verify" in name:
            buckets.setdefault("verify", []).append(tool)
        elif "spawn" in name or "delegate" in name:
            buckets.setdefault("spawn", []).append(tool)
        elif getattr(tool, "source", "registry") == "builtin":
            buckets.setdefault("files", []).append(tool)
        else:
            buckets.setdefault("tools", []).append(tool)
    return buckets


def _builtin_file_names(tools: Iterable[Any]) -> list[str]:
    names = _names(tools)
    return [name for name in names if name]


def _files_line(names: Sequence[str]) -> str:
    listed = ", ".join(f"`{name}`" for name in names)
    return (
        "## File tools (workspace-fenced)\n"
        f"{listed}.\n"
        "Read before you write: look at the actual file, then change it. "
        "`sha256_file` gives you a verifiable digest — use it when a check needs one."
    )


_EXPAND_TEXT = (
    "## EXPAND — grow the toolset\n"
    "Classify a need into an existing tool's family first (the support tool), then "
    "build the missing tool for real. A build runs the real pipeline; the gate is "
    "honest: a build it cannot complete is a named refusal, never a half-built tool "
    "and never a made-up success."
)

_ACT_TEXT = (
    "## ACT — act on the world\n"
    "Run declared actions only (the support tool lists what is really possible), and "
    "always check capability honestly first (the check tool's `ok` is true only for a "
    "real capability — a fixture is not real). Isolation first: prefer the "
    "sandbox/runner kind before anything touching the live desktop. A live desktop "
    "act needs the on-screen approval; if it is denied, the denial persists — take "
    "the no and adapt, never retry around it."
)

_VERIFY_TEXT = (
    "## VERIFY — prove the work\n"
    "Judge real workspaces against criteria; the verdict comes back verbatim as "
    "PASS / FAIL / UNKNOWN. UNKNOWN is never success: a check you could not run "
    "does not count as passed, and `ok` is true only for PASS. Run the checks "
    "before you claim the outcome."
)

_COMPOSE_TEXT = (
    "## COMPOSE — chain tools into a pipeline\n"
    "Validate a composite spec against the real toolset; the validator registers "
    "cleanly against real tools and refuses chains that cannot be honoured. "
    "Validation is not execution — a validated composite does not run until "
    "something actually runs it."
)


def _capability_lines(buckets: dict[str, list[Any]]) -> tuple[str, ...]:
    lines: list[str] = []
    files = buckets.get("files") or buckets.get("tools") or []
    file_names = _builtin_file_names(files)
    if file_names:
        lines.append(_files_line(file_names))
    if buckets.get("expand"):
        names = ", ".join(f"`{name}`" for name in _names(buckets["expand"]))
        lines.append(f"{_EXPAND_TEXT} Real tools here: {names}.")
    if buckets.get("act"):
        names = ", ".join(f"`{name}`" for name in _names(buckets["act"]))
        lines.append(f"{_ACT_TEXT} Real tools here: {names}.")
    if buckets.get("compose"):
        names = ", ".join(f"`{name}`" for name in _names(buckets["compose"]))
        lines.append(f"{_COMPOSE_TEXT} Real tool here: {names}.")
    if buckets.get("verify"):
        names = ", ".join(f"`{name}`" for name in _names(buckets["verify"]))
        lines.append(f"{_VERIFY_TEXT} Real tools here: {names}.")
    if buckets.get("spawn"):
        names = _names(buckets["spawn"])
        lines.append(
            "## Delegation\n"
            + "Delegate genuinely independent work ("
            + ", ".join(f"`{name}`" for name in names)
            + "). The family is bounded: at most 3 children, depth 1 (children may not "
            "delegate further), and a child's test check is time-bounded. A refused "
            "delegation comes back as a named refusal — accept it, do not force it."
        )
    return tuple(lines)


def _jev_line() -> str:
    return (
        "## Decision advisory\n"
        "A decision layer publishes an advisory at each turn boundary. It is "
        "advisory only, never authority: read it if it is useful, and always obey "
        "your own judgement over it. It is never a command and never your result."
    )


def build_system_prompt(
    tools: Iterable[Any],
    *,
    goal: str = "",
    skills: Sequence[str] = (),
    jev_armed: bool = False,
) -> str:
    """The system prompt for one run, built from the toolset the run built.

    ``tools`` is the real :class:`skaldir.agent_loop.ToolSet` (or any iterable
    of tools); every capability named in the text is one of its tools.
    ``skills`` are pre-formatted skill-hint lines from the skills store
    (``consumption_hint`` of promoted skills only); lines are carried verbatim.
    ``jev_armed`` adds the one-line advisory-with-no-authority note.
    """
    buckets = layer_capabilities(list(tools))
    prompt = SystemPrompt(
        core=CORE_CONTRACT,
        capabilities=_capability_lines(buckets),
        skills=tuple(str(item) for item in skills if str(item).strip()),
        goal=str(goal or "").strip(),
        jev=_jev_line() if jev_armed else "",
    )
    return prompt.text()
