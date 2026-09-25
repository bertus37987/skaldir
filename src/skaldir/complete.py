"""Tab completion, fuzzy matching and the inline ``?`` help overlay.

Everything here reads the **real** parser (``skaldir.cli.build_parser()``), never a
hand-written copy of the command list: a command that is not wired into this build
cannot be completed, and the palette says so instead of promising a verb that would
answer ``usage: … invalid choice``.

Three surfaces:

``build_tree(parser)``
    the command tree as :class:`Node` objects — usage, one-line summary, sub-commands,
    options (with ``choices``) and positionals, all taken from argparse itself.
``suggest(...)`` / ``complete_line(...)``
    the completion candidates for a half-typed line (commands, sub-commands, options,
    option values, and — through the ``slash`` hook — the ``/`` palette).
``help_overlay(...)``
    the ``?`` overlay: the usage, the summary, the sub-commands that exist *here*,
    the options and (when the contract is importable) the exit-code vocabulary and
    the evidence a command really reports.

**Values that are not in the parser** (:data:`VALUE_TABLE`) — a model id, a provider id, a
workflow id, a session id, a checkpoint id, a run id or a goal id is not an ``argparse``
choice, it lives in a real store. :func:`value_suggestions` reads those stores (the provider
catalog, the session store, the checkpoint manifests, the run transcripts, ``runs.db``)
**read-only and bounded**,
and returns nothing at all when the store is absent — a completion menu that invents an id
the command would then fail to find is worse than an empty menu.

**Files** — a token that starts with ``@`` (or an argument the table marks as a path) is
completed from the working tree through the **codectx walker's own ignore rules**
(:mod:`skaldir.codectx`), so ``@src/ska<Tab>`` offers exactly the files ``code pack`` would
see, ``/.git`` and the build directories excluded for the same reason.

Nothing in this module writes, spawns a process or reaches the network. It is
import-safe without ``cli_contract`` (an older build) and without ``readline``
(a non-TTY run).
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:  # the product contract: exit codes, layers, per-command specs
    from . import cli_contract as _CONTRACT
except Exception:  # pragma: no cover — standalone builds without the contract module
    _CONTRACT = None

#: Exit codes used by the shell when a command's own vocabulary is not available.
EXIT_OK = int(getattr(_CONTRACT, "EXIT_OK", 0))
EXIT_USAGE = int(getattr(_CONTRACT, "EXIT_USAGE", 1))

_SUB_ACTION = argparse._SubParsersAction


# --------------------------------------------------------------------------- #
# fuzzy matching (the Claude-Code-ish "just type a few letters" behaviour)
# --------------------------------------------------------------------------- #


def fuzzy_score(query: str, candidate: str) -> int | None:
    """``None`` when ``query`` is not in ``candidate``; otherwise a bigger-is-better score.

    Exact and prefix matches win by a wide margin, an inner substring next, and a
    scattered subsequence last — with a bonus for word starts (``pt`` finds
    ``provider test``) and for consecutive characters.
    """
    if not query:
        return 0
    q = query.lower()
    c = str(candidate).lower()
    if not c:
        return None
    if q == c:
        return 1000
    if c.startswith(q):
        return 900 - (len(c) - len(q))
    at = c.find(q)
    if at >= 0:
        return 700 - at
    score = 0
    index = 0
    last = -2
    for char in q:
        found = c.find(char, index)
        if found < 0:
            return None
        if found == last + 1:
            score += 6  # consecutive
        if found == 0 or c[found - 1] in "-_./: ":
            score += 8  # a word start
        score += 1
        last = found
        index = found + 1
    # a match that spans a long name is weaker than a compact one
    score -= max(0, (last - len(q)) // 4)
    return 100 + score


def fuzzy_filter(
    query: str,
    items: Iterable[Any],
    *,
    key: Callable[[Any], str] = str,
    limit: int | None = None,
) -> list[Any]:
    """``items`` ranked by :func:`fuzzy_score`; an empty query keeps the given order."""
    material = list(items)
    if not query:
        return material[:limit] if limit else material
    scored: list[tuple[int, int, int, Any]] = []  # (prefix tier, score, position, item)
    q = query.lower()
    for position, item in enumerate(material):
        score = fuzzy_score(query, key(item))
        if score is None:
            continue
        # exact-prefix matches outrank inner-substring matches even when the substring
        # candidate is shorter: ``dee`` should settle on the ``deepseek-…`` ids over a
        # shorter id that merely contains ``dee`` earlier (``DeepSeek-V3.1``).
        # the prefix here is CASE-SENSITIVE: a typed-lowercase ``dee`` qualifies the
        # ``deepseek-…`` ids but not ``DeepSeek-V3.1`` (case-insensitive fuzzy_score
        # already bands that as a prefix — the exact spelling the user typed wins).
        tier = 1 if (q and key(item).startswith(query)) or score >= 900 else 0
        scored.append((tier, score, -position, item))
    scored.sort(key=lambda row: (-row[0], -row[1], row[2]))
    ranked = [item for _tier, _score, _pos, item in scored]
    return ranked[:limit] if limit else ranked


def longest_common_prefix(values: Sequence[str]) -> str:
    """The insertable common prefix of ``values`` (``""`` for none)."""
    if not values:
        return ""
    prefix = values[0]
    for value in values[1:]:
        while prefix and not value.startswith(prefix):
            prefix = prefix[:-1]
        if not prefix:
            return ""
    return prefix


# --------------------------------------------------------------------------- #
# the command tree, read from the real parser
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Option:
    """One ``--flag`` of a parser."""

    name: str
    aliases: tuple[str, ...] = ()
    help: str = ""
    takes_value: bool = False
    choices: tuple[str, ...] = ()
    required: bool = False
    metavar: str = ""

    @property
    def names(self) -> tuple[str, ...]:
        return (self.name, *self.aliases)


@dataclass(frozen=True)
class Positional:
    """One positional argument of a parser."""

    dest: str
    help: str = ""
    choices: tuple[str, ...] = ()
    required: bool = False
    nargs: str = ""
    greedy: bool = False


@dataclass
class Node:
    """A parser in the command tree."""

    path: str
    name: str
    summary: str = ""
    description: str = ""
    usage: str = ""
    options: tuple[Option, ...] = ()
    positionals: tuple[Positional, ...] = ()
    children: dict[str, Node] = field(default_factory=dict)
    aliases: tuple[str, ...] = ()

    @property
    def is_group(self) -> bool:
        return bool(self.children)

    def option(self, token: str) -> Option | None:
        for candidate in self.options:
            if token in candidate.names:
                return candidate
        return None

    @property
    def greedy(self) -> bool:
        return any(item.greedy for item in self.positionals)


def _sub_action(parser: argparse.ArgumentParser) -> Any:
    for action in getattr(parser, "_actions", []):
        if isinstance(action, _SUB_ACTION):
            return action
    return None


def _child_help(parent: argparse.ArgumentParser) -> dict[str, str]:
    """``name -> one-line help`` for a parent's sub-commands (argparse's own table)."""
    action = _sub_action(parent)
    if action is None:
        return {}
    helped: dict[str, str] = {}
    for pseudo in getattr(action, "_choices_actions", []):
        helped[str(pseudo.dest)] = " ".join(str(pseudo.help or "").split())
    return helped


def _first_paragraph(text: str) -> str:
    return " ".join(str(text or "").split())


def _option_of(action: argparse.Action) -> Option:
    takes_value = not isinstance(
        action, (argparse._StoreTrueAction, argparse._StoreFalseAction, argparse._HelpAction,
                 argparse._VersionAction)
    )
    names = list(action.option_strings)
    if isinstance(action, (argparse._StoreTrueAction, argparse._StoreFalseAction)):
        takes_value = False
    return Option(
        name=names[0] if names else str(action.dest),
        aliases=tuple(names[1:]),
        help=_first_paragraph(action.help or ""),
        takes_value=takes_value,
        choices=tuple(str(item) for item in (action.choices or ())),
        required=bool(action.required),
        metavar=str(action.metavar or action.dest).upper() if takes_value else "",
    )


def _positional_of(action: argparse.Action) -> Positional:
    nargs = action.nargs
    greedy = nargs is argparse.REMAINDER or nargs == argparse.REMAINDER
    return Positional(
        dest=str(action.dest),
        help=_first_paragraph(action.help or ""),
        choices=tuple(str(item) for item in (action.choices or ())),
        required=nargs not in ("?", "*") and not greedy,
        nargs="" if nargs in (None, argparse.OPTIONAL, argparse.ZERO_OR_MORE,
                              argparse.ONE_OR_MORE, argparse.REMAINDER) else str(nargs),
        greedy=bool(greedy),
    )


def build_tree(parser: argparse.ArgumentParser, *, path: str = "", summary: str = "",
               description: str = "") -> Node:
    """The whole command tree as :class:`Node` objects (recursive, real metadata)."""
    options: list[Option] = []
    positionals: list[Positional] = []
    children: dict[str, Node] = {}
    for action in getattr(parser, "_actions", []):
        if isinstance(action, argparse._HelpAction):
            continue
        if isinstance(action, _SUB_ACTION):
            helped = _child_help(parser)
            choices = dict(getattr(action, "choices", None) or {})
            for name, child in choices.items():
                child_path = f"{path}.{name}" if path else str(name)
                children[str(name)] = build_tree(
                    child,
                    path=child_path,
                    summary=helped.get(str(name), ""),
                )
            continue
        if action.option_strings:
            options.append(_option_of(action))
        elif action.dest not in ("help",):
            positionals.append(_positional_of(action))
    return Node(
        path=path,
        name=path.rsplit(".", 1)[-1] if path else (parser.prog or "skaldir"),
        summary=summary,
        description=description or _first_paragraph(parser.description or ""),
        usage=_first_paragraph(parser.format_usage().replace("usage:", "").strip()),
        options=tuple(options),
        positionals=tuple(positionals),
        children=children,
    )


def node_for(tree: Node, tokens: Sequence[str]) -> tuple[Node, list[str]]:
    """Walk ``tokens``; returns the deepest known node and the tokens it could not claim."""
    node = tree
    leftovers: list[str] = []
    for token in tokens:
        child = node.children.get(str(token))
        if child is None:
            leftovers.append(str(token))
            continue
        node = child
    return node, leftovers


def path_for(tree: Node, tokens: Sequence[str]) -> str:
    """The dotted command path ``tokens`` resolves to, when it resolves at all."""
    node = tree
    for token in tokens:
        child = node.children.get(str(token))
        if child is None:
            return ""
        node = child
    return node.path


def known_paths(tree: Node) -> list[str]:
    """Every dotted path in the tree (group and leaf alike)."""
    paths: list[str] = []

    def walk(node: Node) -> None:
        if node.path:
            paths.append(node.path)
        for child in node.children.values():
            walk(child)

    walk(tree)
    return sorted(paths)


# --------------------------------------------------------------------------- #
# value sources — the real stores behind a completion slot
# --------------------------------------------------------------------------- #
#
# A flag like ``provider tiers --main`` does not carry argparse ``choices``: the model ids
# live in the provider catalog, and they change when the catalog does. The same is true for
# ``--session`` (the session store), ``checkpoint show <id>`` (the checkpoint manifests),
# ``agent show <run>`` (the run transcripts) and ``runs resume --goal-id`` (``runs.db``).
# This section reads those stores and nothing else: read-only, bounded, and silent when the
# store does not exist.

VALUE_SESSION = "session"
VALUE_CHECKPOINT = "checkpoint"
VALUE_RUN = "run"
VALUE_GOAL = "goal"
VALUE_MODEL = "model"
VALUE_PROVIDER = "provider"
VALUE_FILE = "file"
#: a declared workflow id (``skaldir workflows list``) — the ``/<workflow-id>`` name a caller
#: types after ``/workflows``. Read from the real contracts, never from a directory listing.
VALUE_WORKFLOW = "workflow"
#: a skill candidate name (``skaldir skills list``) — what ``skills promote/validate/show``
#: take as their positional. Read from the real skills DB (``memory.db``), which is the same
#: store the commands themselves read, so completion and command agree on what exists.
VALUE_SKILL = "skill"

#: How many values a single completion slot may offer (the menu is a menu, not a dump).
VALUE_LIMIT = 30

#: ``(command path, argument) -> kind``. The table is the honest half: a slot that is not
#: listed here completes only what argparse itself knows (``choices``). Argument spellings
#: are the parser's own (``--main``, ``--session``, ``checkpoint_id``).
VALUE_TABLE: dict[tuple[str, str], str] = {
    # the role → model mapping and the model-carrying flags of the provider group
    ("provider.tiers", "--small"): VALUE_MODEL,
    ("provider.tiers", "--mid"): VALUE_MODEL,
    ("provider.tiers", "--main"): VALUE_MODEL,
    ("provider.tiers", "name"): VALUE_PROVIDER,
    ("provider.add", "--model"): VALUE_MODEL,
    ("provider.test", "name"): VALUE_PROVIDER,
    ("coding.model.set", "--model"): VALUE_MODEL,
    ("coding.model.set", "--provider"): VALUE_PROVIDER,
    # sessions: the store is the only place these ids exist
    ("session.show", "--session"): VALUE_SESSION,
    ("session.resume", "--session"): VALUE_SESSION,
    ("session.export", "--session"): VALUE_SESSION,
    ("session.search", "--session"): VALUE_SESSION,
    ("session.fork", "--session"): VALUE_SESSION,
    ("session.archive", "--session"): VALUE_SESSION,
    ("session.rotate", "--session"): VALUE_SESSION,
    ("session.append", "--session"): VALUE_SESSION,
    ("session.verify", "--session"): VALUE_SESSION,
    ("coding.status", "--session"): VALUE_SESSION,
    ("coding.model.set", "--session"): VALUE_SESSION,
    ("coding.model.show", "--session"): VALUE_SESSION,
    ("session.new", "--parent"): VALUE_SESSION,
    ("goal.show", "--session"): VALUE_SESSION,
    ("runs.checkpoint", "--session"): VALUE_SESSION,
    ("runs.resume", "--session"): VALUE_SESSION,
    # checkpoints: the manifests under the state dir
    ("checkpoint.show", "checkpoint_id"): VALUE_CHECKPOINT,
    ("checkpoint.restore", "checkpoint_id"): VALUE_CHECKPOINT,
    ("checkpoint.resume", "checkpoint_id"): VALUE_CHECKPOINT,
    # runs: the transcripts under the state dir
    ("agent.show", "run_id"): VALUE_RUN,
    ("agent.transcript", "run_id"): VALUE_RUN,
    ("agent.retry", "run_id"): VALUE_RUN,
    ("agent.cancel", "run_id"): VALUE_RUN,
    # goals: the durable records in runs.db
    ("runs.resume", "--goal-id"): VALUE_GOAL,
    ("runs.checkpoint", "--goal-id"): VALUE_GOAL,
    ("goal.show", "--goal-id"): VALUE_GOAL,
    ("goal.criteria", "--goal-id"): VALUE_GOAL,
    # …and the run transcripts the agent loop wrote (the same ids `agent show` takes)
    ("context.usage", "--run-id"): VALUE_RUN,
    ("context.compact", "--run-id"): VALUE_RUN,
    # paths: the codectx walker decides what a path even is here
    ("code.run", "--file"): VALUE_FILE,
    ("context.usage", "--transcript"): VALUE_FILE,
    ("context.compact", "--transcript"): VALUE_FILE,
    # …and the root the scan verb audits (`/scan <path>` → `security audit --path <path>`)
    ("security.audit", "--path"): VALUE_FILE,
    ("security.audit", "--root"): VALUE_FILE,
    # the workflow id is a positional, and the declared name is the only thing the runner
    # accepts: `workflows run` refuses an id `workflows list` does not know
    ("workflows.run", "workflow_id"): VALUE_WORKFLOW,
    ("workflows.show", "workflow_id"): VALUE_WORKFLOW,
    # the skill candidate name is a positional of `skills promote/validate/show/demote`:
    # completion offers exactly the names the same DB backs (the command's own store)
    ("skills.promote", "name"): VALUE_SKILL,
    ("skills.validate", "name"): VALUE_SKILL,
    ("skills.show", "name"): VALUE_SKILL,
    ("skills.demote", "name"): VALUE_SKILL,
}

#: Argument names that carry an id even when the (command, arg) pair is not in the table.
#: Conservative on purpose: a wrong guess fills the menu with ids the command would refuse.
_NAME_HEURISTIC: tuple[tuple[str, str], ...] = (
    ("session_id", VALUE_SESSION),
    ("session", VALUE_SESSION),
    ("parent", VALUE_SESSION),
    ("checkpoint_id", VALUE_CHECKPOINT),
    ("checkpoint", VALUE_CHECKPOINT),
    ("goal_id", VALUE_GOAL),
    ("run_id", VALUE_RUN),
    ("model", VALUE_MODEL),
    ("provider", VALUE_PROVIDER),
)


def value_kind_for(path: str, argument: str) -> str:
    """The kind of value this argument takes: the table first, then the name heuristic.

    ``""`` means "this module knows nothing to offer" — which is not the same as "the
    argument has no values"; it says the parser's own ``choices`` are all the evidence
    there is.
    """
    key = (str(path or ""), str(argument or ""))
    if key in VALUE_TABLE:
        return VALUE_TABLE[key]
    name = key[1].lstrip("-").lower()
    if not name:
        return ""
    for needle, kind in _NAME_HEURISTIC:
        if name == needle or name.endswith("_" + needle):
            return kind
    return ""


def runtime_dir() -> Path:
    """The installation's runtime directory (the same one every command uses)."""
    try:
        from .registry import default_runtime_dir

        return Path(default_runtime_dir())
    except Exception:  # noqa: BLE001 — completion must never raise
        return Path.home() / ".hermes" / "skaldir"


def _rows(source: Callable[[], list[tuple[str, str]]]) -> list[tuple[str, str]]:
    """Run one store read; a store that is missing or broken yields nothing, never a raise."""
    try:
        return list(source())
    except Exception:  # noqa: BLE001
        return []


def session_values(*, limit: int = VALUE_LIMIT) -> list[tuple[str, str]]:
    """``(session_id, title)`` for the newest sessions, read-only, from the real store."""
    from . import session_store

    def read() -> list[tuple[str, str]]:
        store = session_store.open_store(runtime=runtime_dir(), create=False)
        try:
            out: list[tuple[str, str]] = []
            for row in store.sessions(include_archived=True, limit=max(1, int(limit))):
                sid = str(row.get("session_id") or "")
                if not sid:
                    continue
                count = row.get("message_count")
                title = str(row.get("title") or "").strip()
                note = title or f"{count if count is not None else '?'} message(s)"
                out.append((sid, f"session — {note}"))
            return out
        finally:
            store.close()

    return _rows(read)


def checkpoint_values(*, limit: int = VALUE_LIMIT) -> list[tuple[str, str]]:
    """``(checkpoint_id, when/what)`` from the checkpoint manifests of the state dir."""
    from . import checkpoints

    def read() -> list[tuple[str, str]]:
        # the state dir the checkpoint commands themselves use: <runtime>/checkpoint
        state = runtime_dir() / getattr(checkpoints, "CHECKPOINT_DIRNAME", "checkpoint")
        report = checkpoints.list_checkpoints(state, limit=max(1, int(limit)))
        out: list[tuple[str, str]] = []
        for row in report.get("checkpoints", []):
            cid = str(row.get("checkpoint_id") or row.get("id") or "")
            if not cid:
                continue
            files = row.get("files")
            messages = row.get("messages")
            created = str(row.get("created_at") or "")
            label = " ".join(str(row.get("label") or "").split())
            note = " · ".join(
                part
                for part in (
                    created,
                    label,
                    f"{files} file(s)" if files is not None else "",
                    f"{messages} message(s)" if messages is not None else "",
                )
                if part
            )
            out.append((cid, f"checkpoint — {note}" if note else "checkpoint"))
        out.append(("last", "checkpoint — the newest one"))
        return out

    return _rows(read)


def run_values(*, limit: int = VALUE_LIMIT) -> list[tuple[str, str]]:
    """``(run_id, status/task)`` from the run transcripts the agent loop wrote."""
    from . import agent_loop

    def read() -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        for row in agent_loop.list_runs(runtime_dir(), limit=max(1, int(limit))):
            rid = str(row.get("run_id") or "")
            if not rid:
                continue
            status = str(row.get("status") or "?")
            task = " ".join(str(row.get("task") or "").split())[:60]
            out.append((rid, f"run — {status}" + (f" · {task}" if task else "")))
        return out

    return _rows(read)


def goal_values(*, limit: int = VALUE_LIMIT) -> list[tuple[str, str]]:
    """``(goal_id, state)`` from ``runs.db`` (the durable goal records)."""
    from . import runs_store

    def read() -> list[tuple[str, str]]:
        path = runs_store.run_store_path(str(runtime_dir()))
        if not Path(path).exists():
            return []
        store = runs_store.RunsStore(path)
        try:
            out: list[tuple[str, str]] = []
            for goal in store.goals():
                gid = str(getattr(goal, "goal_id", "") or "")
                if not gid:
                    continue
                state = " ".join(str(getattr(goal, "state", "") or "").split())
                out.append((gid, f"goal — {state}" if state else "goal"))
                if len(out) >= max(1, int(limit)):
                    break
            return out
        finally:
            store.close()

    return _rows(read)


def model_values(*, limit: int = 60) -> list[tuple[str, str]]:
    """``(model_id, where it comes from)`` from the verified catalog and the real config.

    The catalog is the product's own table of *verified* endpoints; a model id that is
    neither in it nor configured is not offered, because the run would only fail on it.
    """
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    defaults: list[tuple[str, str]] = []

    def add(model: str, note: str) -> None:
        text = str(model or "").strip()
        if not text or text in seen:
            return
        seen.add(text)
        out.append((text, note))

    try:
        from . import provider_catalog

        # the catalog defaults come FIRST: every entry that declares a default is a model
        # id a caller can always reach, so the display slice (``limit``) may hide the long
        # tails of a big catalog — it may not hide a declared default. The old shape took
        # the first N rows of the walk and dropped ids the catalog itself still declares.
        verified: list[tuple[str, str]] = []
        for entry_id, entry in provider_catalog.CATALOG.items():
            if getattr(entry, "default_model", ""):
                defaults.append(
                    (
                        str(entry.default_model).strip(),
                        f"{entry_id} — the catalog default",
                    )
                )
            for model in getattr(entry, "models", ()) or ():
                verified.append((str(model).strip(), f"{entry_id} — verified in the catalog"))
        for model, note in (*defaults, *verified):
            add(model, note)
    except Exception:  # noqa: BLE001
        pass
    try:
        from . import provider_config

        store = provider_config.load_store()
        for entry in store.providers.values():
            add(getattr(entry, "model", ""), f"{entry.name} — configured provider model")
            for tier, model in (getattr(entry, "tiers", None) or {}).items():
                add(model, f"{entry.name} — tier {tier}")
    except Exception:  # noqa: BLE001 — no config yet is the normal first-run state
        pass
    # the slice may drop the verified tails, never a catalog default (they lead the list,
    # so a limit smaller than the defaults' count is raised to cover them)
    kept = out[: max(1, int(limit))]
    if len(defaults) > len(kept):
        kept = out[: len(defaults)]
    return kept


def provider_values(*, limit: int = 30) -> list[tuple[str, str]]:
    """``(provider_id, where it comes from)`` from the catalog plus the configured registry."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(pid: str, note: str) -> None:
        text = str(pid or "").strip()
        if not text or text in seen:
            return
        seen.add(text)
        out.append((text, note))

    try:
        from . import provider_catalog

        for entry_id, entry in provider_catalog.CATALOG.items():
            add(entry_id, f"catalog — {getattr(entry, 'label', entry_id)}")
    except Exception:  # noqa: BLE001
        pass
    try:
        from . import provider_config

        store = provider_config.load_store()
        for name in store.providers:
            add(str(name), "configured in config.yaml")
        add(str(store.default or ""), "the configured default")
    except Exception:  # noqa: BLE001
        pass
    return out[: max(1, int(limit))]


def workflow_values(*, limit: int = VALUE_LIMIT) -> list[tuple[str, str]]:
    """``(workflow_id, scope/success)`` from the declared workflow contracts.

    The same list ``skaldir workflows list`` prints: a name the contract registry does not
    carry is one `workflows run` refuses, so offering anything else would only fail.
    """
    from . import workflows

    def read() -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        for contract in workflows.workflow_contracts():
            name = str(contract.get("name") or "")
            if not name:
                continue
            scope = str(contract.get("scope") or "")
            success = " ".join(str(contract.get("success") or "").split())[:60]
            note = " · ".join(
                part
                for part in (
                    f"workflow — {scope}" if scope else "workflow",
                    success,
                )
                if part
            )
            out.append((name, note))
            if len(out) >= max(1, int(limit)):
                break
        return out

    return _rows(read)


def skill_values(*, limit: int = VALUE_LIMIT) -> list[tuple[str, str]]:
    """``(skill name, status/occurrences)`` from the real skills DB — the store
    ``skills promote/validate/show`` read the same names from."""
    from . import skills as _skills

    def read() -> list[tuple[str, str]]:
        db = runtime_dir() / "memory.db"
        if not db.is_file():
            return []
        store = _skills.SkillStore(db)
        try:
            out: list[tuple[str, str]] = []
            for skill in store.all():
                name = str(skill.name or "").strip()
                if not name:
                    continue
                occurrences = getattr(skill, "occurrences", None)
                note = " · ".join(
                    part
                    for part in (
                        str(getattr(skill, "status", "") or ""),
                        f"{occurrences} occurrence(s)" if occurrences else "",
                    )
                    if part
                )
                out.append((name, f"skill — {note}" if note else "skill"))
            return out
        finally:
            store.close()

    return _rows(read)


#: The id stores, by kind. One dict so ``/help`` and the tests can quote it.
VALUE_SOURCES: dict[str, Callable[[], list[tuple[str, str]]]] = {
    VALUE_SESSION: session_values,
    VALUE_CHECKPOINT: checkpoint_values,
    VALUE_RUN: run_values,
    VALUE_GOAL: goal_values,
    VALUE_MODEL: model_values,
    VALUE_PROVIDER: provider_values,
    VALUE_WORKFLOW: workflow_values,
    VALUE_SKILL: skill_values,
}


def value_suggestions(
    kind: str,
    partial: str = "",
    *,
    limit: int = VALUE_LIMIT,
) -> list[Suggestion]:
    """Fuzzy-ranked suggestions for one value kind; ``[]`` when there is nothing real.

    A partial that already carries the ``@`` marker is a path, whatever the kind says.
    """
    if not kind and not partial.startswith("@"):
        return []
    text = str(partial or "")
    if kind == VALUE_FILE or text.startswith("@"):
        return file_suggestions(text, limit=max(limit, 48))
    source = VALUE_SOURCES.get(str(kind))
    if source is None:
        return []
    pairs = _rows(source)
    if not pairs:
        return []
    ranked = fuzzy_filter(text, pairs, key=lambda item: str(item[0]), limit=limit)
    return [Suggestion(str(value), note, "choice") for value, note in ranked]


# --------------------------------------------------------------------------- #
# files — ``@path`` completion through the codectx walker's own ignore rules
# --------------------------------------------------------------------------- #

#: ``(root, directory) -> (relative names, fetched_at)``. A directory listing is cheap but
#: not free; the shell asks on every Tab, and a menu must not walk a repository per keypress.
_DIR_CACHE: dict[tuple[str, str], tuple[float, list[str]]] = {}
_DIR_CACHE_TTL = 2.0
_DIR_CACHE_MAX = 256
#: ``root -> IgnoreRules`` — the same rules ``code pack`` obeys (``.gitignore`` included).
_RULES_CACHE: dict[str, Any] = {}


def _ignore_rules(root: Path) -> Any:
    """The codectx ignore rules for ``root`` (cached; ``None`` when codectx is unavailable)."""
    key = str(root)
    if key in _RULES_CACHE:
        return _RULES_CACHE[key]
    rules = None
    try:
        from . import codectx

        rules = codectx.load_ignore_rules(root)
    except Exception:  # noqa: BLE001 — no codectx: the walk still works, only unsmart
        rules = None
    if len(_RULES_CACHE) >= 32:
        _RULES_CACHE.clear()
    _RULES_CACHE[key] = rules
    return rules


def _skip_dir(name: str) -> bool:
    """The directories the codectx walker never enters (so the menu never offers them)."""
    try:
        from .tools_core import sandbox

        if name in set(sandbox.SKIP_DIRS):
            return True
    except Exception:  # noqa: BLE001
        pass
    try:
        from .codectx import EXTRA_SKIP_DIRS

        if name in set(EXTRA_SKIP_DIRS):
            return True
    except Exception:  # noqa: BLE001
        pass
    return name.startswith(".git") or name in {"node_modules", "__pycache__"}


def _list_dir(root: Path, directory: str) -> list[str]:
    """The entries of ``root/directory`` as completion-ready names (dirs carry a ``/``)."""
    import time

    key = (str(root), str(directory))
    hit = _DIR_CACHE.get(key)
    now = time.monotonic()
    if hit is not None and now - hit[0] < _DIR_CACHE_TTL:
        return hit[1]
    target = Path(root) / directory if directory else Path(root)
    names: list[str] = []
    rules = _ignore_rules(Path(root))
    try:
        with os.scandir(target) as handle:
            for entry in sorted(handle, key=lambda item: item.name):
                name = entry.name
                rel = f"{directory.rstrip('/')}/{name}" if directory else name
                try:
                    is_dir = entry.is_dir(follow_symlinks=False)
                except OSError:  # pragma: no cover
                    continue
                if is_dir and _skip_dir(name):
                    continue
                if rules is not None:
                    try:
                        if rules.decide(rel, is_dir=is_dir)[0]:
                            continue
                    except Exception:  # noqa: BLE001
                        pass
                names.append(name + ("/" if is_dir else ""))
    except OSError:
        names = []
    if len(_DIR_CACHE) >= _DIR_CACHE_MAX:
        _DIR_CACHE.clear()
    _DIR_CACHE[key] = (now, names)
    return names


def file_suggestions(
    partial: str,
    *,
    root: str | os.PathLike[str] | None = None,
    limit: int = 48,
) -> list[Suggestion]:
    """``@file`` / ``@folder`` completions from the working tree, codectx rules applied.

    ``partial`` may carry the ``@`` (``@src/ska``) or not (``src/ska``). Directories are
    offered with a trailing ``/`` so the next Tab descends into them, and a suggestion
    whose text is already complete (a directory) says so in its description.
    """
    base = Path(root) if root is not None else Path.cwd()
    text = str(partial or "")
    marked = text.startswith("@")
    query = text[1:] if marked else text
    head, _slash, tail = query.rpartition("/")
    directory = head
    names = _list_dir(base, directory)
    prefix = f"@{directory}/" if marked and directory else ("@" if marked else (
        f"{directory}/" if directory else ""))
    ranked = fuzzy_filter(tail, names, key=str, limit=max(1, int(limit)))
    out: list[Suggestion] = []
    for name in ranked:
        is_dir = name.endswith("/")
        out.append(
            Suggestion(
                f"{prefix}{name}",
                "directory — Tab descends into it" if is_dir else "file",
                "argument",
                # a directory is complete with the trailing slash; a file is complete too
                insertable=is_dir or bool(name),
            )
        )
    return out


# --------------------------------------------------------------------------- #
# suggestions
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Suggestion:
    """One entry of the completion menu."""

    text: str
    description: str = ""
    kind: str = "command"  # command | group | option | choice | argument | slash
    insertable: bool = True
    available: bool = True

    def line(self, *, text_width: int = 26, width: int = 80, ellipsis: str = "…") -> str:
        """One aligned row: the candidate, then its one-line description.

        ``text_width`` is the caller's column width (the shell computes it from the whole
        menu so every row lines up); the description is clipped to what is left of
        ``width`` so a long help string cannot wrap the menu into a wall.
        """
        note = "" if self.available else "  (not wired in this build)"
        if not self.description:
            return f"{self.text}{note}"
        room = max(8, int(text_width))
        tail = max(8, int(width) - room - 1)
        description = self.description
        if len(description) > tail:
            keep = max(1, tail - len(ellipsis))
            description = description[:keep].rstrip() + ellipsis
        return f"{self.text:<{room}} {description}{note}"


def _option_suggestions(node: Node, partial: str) -> list[Suggestion]:
    """Options of ``node`` matching ``partial`` (name + value spelling as description)."""
    out: list[Suggestion] = []
    for option in node.options:
        spelling = option.name + (f" {option.metavar}" if option.takes_value else "")
        if option.choices:
            spelling += f" {{{'|'.join(option.choices)}}}"
        description = " · ".join(part for part in (spelling, option.help) if part)
        for name in option.names:
            if not partial or name.startswith(partial):
                out.append(Suggestion(name, description, "option"))
    return out


def _node_for_argv(tree: Node, argv: Sequence[str]) -> Node | None:
    """The node a real command path resolves to (``("provider","tiers")`` → that node)."""
    node = tree
    for token in argv:
        if str(token).startswith("-"):
            continue
        child = node.children.get(str(token))
        if child is None:
            return None
        node = child
    return node


def _choice_suggestions(
    choices: Iterable[str], partial: str, note: str
) -> list[Suggestion]:
    """argparse ``choices`` as suggestions (the parser's own vocabulary)."""
    return [
        Suggestion(str(choice), note, "choice")
        for choice in choices
        if not partial or str(choice).lower().startswith(str(partial).lower())
    ]


def _pending_positional(node: Node, filled: int) -> Positional | None:
    """The next positional the typed words have not filled yet (``None`` when none left)."""
    if filled < len(node.positionals):
        return node.positionals[filled]
    return None


def _planned_rows(command: Any) -> list[Suggestion]:
    """The nearest real commands for a planned verb — shown, never insertable."""
    return [
        Suggestion(
            str(item),
            "nearest real command (this verb is planned) — run it as a plain CLI line",
            "argument",
            insertable=False,
        )
        for item in (command.nearest or ())
    ]


def slash_args(
    tree: Node,
    tokens: Sequence[str],
    partial: str = "",
) -> list[Suggestion] | None:
    """Candidates *after* a slash verb: its real flags, and the real values of its slots.

    ``None`` when ``tokens`` is not a slash line this module can route (an unknown verb, or
    a line that does not start with ``/``) — the caller then falls back to the palette.

    The value side is where a chat surface wins or lies: ``/resume <Tab>`` offers the
    session ids the **session store** holds, ``/model --main <Tab>`` the model ids of the
    **provider catalog**, ``/restore <Tab>`` the checkpoint ids of the state dir. A store
    that is absent contributes nothing (an empty menu, not an invented id).
    """
    from . import slash as _slash  # local import: slash imports this module at load time

    if not tokens or not str(tokens[0]).startswith(_slash.SLASH):
        return None
    command = _slash.command_for(tokens[0])
    if command is None:
        return None
    # the *resolver* decides what the line runs (`/agent show …` → `agent show`): the node
    # the flags and values come from is read from that answer, so the two can never drift
    probe = " ".join([str(item) for item in tokens] + ([partial] if partial else [])).strip()
    invocation = _slash.resolve(probe, tree)
    if invocation is None:
        return None
    if command.planned:
        return _planned_rows(command)
    if command.builtin and not command.argv:
        # the shell answers this verb itself (help/quit/keys/clear/diff/init): it has no
        # real command behind it, so there are no flags or values to offer — and offering
        # the *root* parser's flags would be an invention
        return []
    node = _node_for_argv(tree, list(invocation.verb_argv or command.argv))
    if node is None:
        return []
    typed = [str(item) for item in tokens[1:]]
    if invocation.mode == "verb" and typed:
        # the first word was not a value: it *became* the verb, so it does not fill a
        # positional either
        typed = typed[1:]
    if typed:
        previous = typed[-1]
        option = node.option(previous)
        if option is not None and option.takes_value:
            if option.choices:
                return _choice_suggestions(option.choices, partial, f"value of {option.name}")
            kind = value_kind_for(node.path, option.name)
            if kind:
                return value_suggestions(kind, partial)
            return []
    if partial.startswith("-"):
        return _option_suggestions(node, partial)
    out: list[Suggestion] = []
    kind = command.values
    if not kind:
        filled = len([item for item in typed if not item.startswith("-")])
        positional = _pending_positional(node, filled)
        if positional is not None:
            kind = value_kind_for(node.path, positional.dest)
    if kind:
        found = value_suggestions(kind, partial)
        if found:
            out.extend(found)
        elif not partial:
            # A value slot with nothing saved says so — it must never fall through to the
            # verb's flags, whose shared prefix is "--" (Tab then inserted "--" and the
            # slot read as "completion is dead"). Typing "-" reaches the flags instead.
            out.append(
                Suggestion(
                    f"(no saved {kind} yet)",
                    "this slot takes a real id — create one first, or type '-' for flags",
                    "note",
                    insertable=False,
                )
            )
        return out
    if not partial:
        seen = {item.text for item in out}
        out.extend(item for item in _option_suggestions(node, "") if item.text not in seen)
    return out


def suggest(
    tree: Node,
    tokens: Sequence[str],
    partial: str = "",
    *,
    slash: Callable[[str], list[Suggestion]] | None = None,
    include_options: bool = True,
) -> list[Suggestion]:
    """The candidates for the token being typed.

    ``tokens`` are the words *before* the one being typed. A leading ``/`` is routed to
    ``slash`` (the palette), so the same engine completes ``skaldir`` and ``/`` lines — and
    a token *after* a slash verb completes that verb's own flags and values (see
    :func:`slash_args`). ``@path`` is completed from the working tree.
    """
    tokens = [str(item) for item in tokens]
    if partial.startswith("@"):
        return file_suggestions(partial)
    if tokens and tokens[0].startswith("/"):
        routed = slash_args(tree, tokens, partial)
        if routed is not None:
            return routed
        # an unknown `/verb`: the shell says so and shows the closest real rows; completing
        # the *root* parser's flags for it would be an invention
        return []
    if slash is not None and (partial.startswith("/") or not tokens):
        if partial.startswith("/") or partial == "":
            routed = slash(partial)
            if routed:
                return routed
    node, leftovers = node_for(tree, tokens)
    if tokens:
        typed = node.option(tokens[-1])
        if typed is not None and typed.takes_value:
            if typed.choices:
                return _choice_suggestions(typed.choices, partial, f"value of {typed.name}")
            kind = value_kind_for(node.path, typed.name)
            if kind:
                return value_suggestions(kind, partial)
    if partial.startswith("-") and not node.greedy:
        return _option_suggestions(node, partial)
    out: list[Suggestion] = []
    if node.children and not leftovers:
        for name, child in node.children.items():
            if partial and fuzzy_score(partial, name) is None:
                continue
            out.append(
                Suggestion(
                    name,
                    child.summary or child.description,
                    "group" if child.is_group else "command",
                )
            )
        if partial:
            out = fuzzy_filter(partial, out, key=lambda item: item.text)
    else:
        filled = len([item for item in leftovers if not str(item).startswith("-")])
        positional = _pending_positional(node, filled)
        if positional is not None:
            if positional.choices:
                out.extend(_choice_suggestions(positional.choices, partial, positional.help))
            else:
                kind = value_kind_for(node.path, positional.dest)
                if kind:
                    out.extend(value_suggestions(kind, partial))
    if include_options and not node.greedy:
        seen = {item.text for item in out}
        out.extend(
            item for item in _option_suggestions(node, partial) if item.text not in seen
        )
    return out


# --------------------------------------------------------------------------- #
# line completion
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Completion:
    """The result of completing a line: the insertable token and the menu."""

    line: str
    start: int
    end: int
    partial: str
    insert: str
    suggestions: tuple[Suggestion, ...] = ()
    reason: str = ""

    @property
    def unique(self) -> bool:
        return len(self.suggestions) == 1

    def completed_line(self) -> str:
        """The line with the token completed (a unique match also gets a space)."""
        if not self.insert:
            return self.line
        tail = self.line[self.end:]
        suffix = " " if self.unique else ""
        return f"{self.line[:self.start]}{self.insert}{suffix}{tail}"

    def menu_lines(self, limit: int = 12, *, width: int = 80, ellipsis: str = "…") -> list[str]:
        return menu_lines(self.suggestions, limit=limit, width=width, ellipsis=ellipsis)


def menu_lines(
    suggestions: Sequence[Suggestion],
    *,
    limit: int = 12,
    width: int = 80,
    ellipsis: str = "…",
) -> list[str]:
    """The quiet menu: one aligned row per candidate, then an honest ``… n more``.

    The text column is sized from the candidates themselves (a menu of ``--json``-length
    names should not all but one row misaligned), and every row is clipped to ``width`` so
    a long description cannot wrap into a wall.
    """
    material = list(suggestions)
    if not material:
        return []
    shown = material[: max(0, int(limit))]
    text_width = min(28, max(12, max(len(item.text) for item in shown) + 2))
    rows = [item.line(text_width=text_width, width=width, ellipsis=ellipsis) for item in shown]
    hidden = len(material) - len(shown)
    if hidden > 0:
        rows.append(f"{ellipsis} {hidden} more")
    return [row[:width] for row in rows]


def _split(line: str, cursor: int) -> tuple[list[str], str]:
    """Split ``line[:cursor]``; ``partial`` is the token under the cursor (may be '')."""
    prefix = line[:cursor]
    tokens = prefix.split()
    if prefix and not prefix[-1].isspace():
        return tokens[:-1], (tokens[-1] if tokens else "")
    return tokens, ""


def complete_line(
    tree: Node,
    line: str,
    cursor: int | None = None,
    *,
    slash: Callable[[str], list[Suggestion]] | None = None,
    limit: int | None = None,
) -> Completion:
    """Complete ``line`` at ``cursor`` against the real tree."""
    cursor = len(line) if cursor is None else max(0, min(int(cursor), len(line)))
    tokens, partial = _split(line, cursor)
    start = cursor - len(partial)
    suggestions = suggest(tree, tokens, partial, slash=slash)
    if limit:
        suggestions = suggestions[:limit]
    insert = ""
    reason = ""
    if len(suggestions) == 1 and suggestions[0].insertable:
        insert = suggestions[0].text
        reason = "the only candidate"
    else:
        insertable = [item.text for item in suggestions if item.insertable]
        common = longest_common_prefix(insertable)
        if len(common) > len(partial):
            insert, reason = common, "the candidates' common prefix"
        else:
            # nothing shared across the whole menu — but the candidates that carry the
            # typed spelling (case-sensitive) name a family of their own: extend the
            # partial to that family's shared prefix. A farther family cousin ('deepseek/…'
            # beside 'deepseek-…', or any single far row like 'claude-sonnet-5') must not
            # drag the shared prefix back under what was typed.
            family = [text for text in insertable if text.startswith(partial)]
            if len(family) >= 2:
                reach = min(len(text) for text in family) + 10
                pfx = longest_common_prefix(
                    [text for text in family if len(text) <= reach]
                )
                if len(pfx) > len(partial):
                    insert, reason = pfx, "the typed prefix's family common prefix"
        if (
            not insert
            and partial
            and partial != "/"
            and suggestions
            and suggestions[0].insertable
        ):
            # nothing shared, but the best candidate really starts with what was typed:
            # take it (the Claude-Code menu picks the first entry on Tab).
            # A bare "/" only opens the menu — it must not commit to the first entry.
            best = suggestions[0]
            if best.text.lower().startswith(partial.lower()):
                insert, reason = best.text, "the best (prefix) match — the first menu entry"
    return Completion(
        line=line,
        start=start,
        end=cursor,
        partial=partial,
        insert=insert,
        suggestions=tuple(suggestions),
        reason=reason,
    )


# --------------------------------------------------------------------------- #
# the ``?`` overlay — parser facts plus the command's contract
# --------------------------------------------------------------------------- #


def _contract_facts(path: str) -> str:
    if _CONTRACT is None or not path:
        return ""
    try:
        spec = _CONTRACT.spec_for(path)
    except Exception:  # noqa: BLE001 — an old contract must not break the overlay
        return ""
    if spec.layer == "(unregistered)":
        return "contract: (unregistered — no cli_contract.COMMANDS entry yet)"
    codes = ", ".join(str(code) for code in spec.exit_codes)
    parts = [
        f"layer: {spec.layer}",
        f"exit codes: {codes}",
        "mutates" if spec.mutates else "read-only",
    ]
    if spec.requires_credentials:
        parts.append("needs credentials")
    return " · ".join(parts)


def help_overlay(
    tree: Node,
    tokens: Sequence[str],
    *,
    width: int = 100,
    indent: str = "  ",
) -> str:
    """The inline ``?`` overlay for a command path (or the root when ``tokens`` is empty)."""
    tokens = [str(item) for item in tokens if str(item).strip()]
    node, leftovers = node_for(tree, tokens)
    if tokens and (node.path == "" or leftovers):
        missing = leftovers[-1] if leftovers else tokens[-1]
        near = fuzzy_filter(
            missing, known_paths(tree), limit=5
        )
        lines = [f"no command {missing!r} in this build."]
        if near:
            lines.append("closest: " + ", ".join(near))
        return "\n".join(f"{indent}{line}" for line in lines)
    lines: list[str] = []
    untruncated: set[int] = set()
    label = node.path or "skaldir"
    lines.append(f"{label} — {node.summary or node.description or 'the Skaldir CLI'}")
    if node.usage:
        usage = node.usage if node.usage.startswith("skaldir") else f"skaldir {node.usage}"
        untruncated.add(len(lines))
        lines.append(f"usage: {usage}".strip())
    if node.children:
        lines.append("commands:")
        for name, child in node.children.items():
            note = child.summary or child.description
            lines.append(f"{indent}{name:<22} {note}")
    if node.positionals:
        lines.append("arguments:")
        for positional in node.positionals:
            suffix = f" {{{'|'.join(positional.choices)}}}" if positional.choices else ""
            lines.append(f"{indent}{positional.dest:<22} {positional.help}{suffix}".rstrip())
    if node.options:
        lines.append("options:")
        for option in node.options:
            names = ", ".join(option.names)
            value = f" {option.metavar}" if option.takes_value else ""
            choices = f" {{{'|'.join(option.choices)}}}" if option.choices else ""
            lines.append(f"{indent}{names + value + choices:<34} {option.help}".rstrip())
    facts = _contract_facts(node.path)
    if facts:
        lines.append(facts)
    return "\n".join(
        line if index in untruncated else line[:width] for index, line in enumerate(lines)
    )


# --------------------------------------------------------------------------- #
# readline bridge (interactive mode)
# --------------------------------------------------------------------------- #


class LineCompleter:
    """A ``readline``-compatible completer over the real tree.

    ``matches`` holds the entries readline will show; ``menu`` keeps the richer
    :class:`Suggestion` list so the shell can print a described menu itself.
    """

    def __init__(
        self,
        tree: Node,
        *,
        slash: Callable[[str], list[Suggestion]] | None = None,
        limit: int = 40,
    ) -> None:
        self.tree = tree
        self.slash = slash
        self.limit = limit
        self.matches: list[str] = []
        self.menu: list[Suggestion] = []

    def compute(self, text: str) -> Completion:
        completion = complete_line(self.tree, text, len(text), slash=self.slash,
                                   limit=self.limit)
        self.menu = list(completion.suggestions)
        if completion.insert:
            # the engine already decided: hand readline that one token and let it insert
            self.matches = [completion.insert]
        else:
            # nothing to insert (a bare "/", an empty line): hand readline the candidates,
            # so it knows there are several and calls the display hook — which draws the
            # product's palette instead of readline's column list
            self.matches = [item.text for item in completion.suggestions if item.insertable]
        return completion

    def __call__(self, text: str, state: int) -> str | None:
        if state == 0:
            try:
                import readline

                buffer = readline.get_line_buffer()
                begin = readline.get_begidx()
                line = buffer[:begin] + text
                self.compute(line)
                # Measured: libedit does not append the completion's trailing space (GNU
                # readline does), so `selftest --lay<Tab>ACT` would become `--layerACT`.
                # The space is added here, where the buffer is known — never duplicated
                # when the user already typed one.
                if len(self.matches) == 1 and self.matches[0]:
                    following = buffer[begin + len(text): begin + len(text) + 1]
                    if not following.isspace():
                        self.matches = [self.matches[0] + " "]
            except Exception:  # noqa: BLE001 — a broken readline must not break input
                self.matches = []
        return self.matches[state] if state < len(self.matches) else None

    def menu_lines(self, limit: int = 12) -> list[str]:
        return [item.line() for item in self.menu[:limit]]


def install_readline(
    completer: LineCompleter,
    *,
    history_file: str | os.PathLike[str] | None = None,
    display: Callable[[str, list[str], int], None] | None = None,
) -> bool:
    """Wire ``completer`` into readline; returns ``False`` when readline is unavailable.

    ``display`` becomes readline's own menu-painting hook, so a Tab press on a real
    terminal draws the *product's* aligned palette instead of readline's column list.
    (Measured: ESC cannot be bound from Python's ``readline`` — it stays the meta prefix —
    so dismissal is handled by the shell on any line that carries an ESC byte.)
    """
    try:
        import readline
    except Exception:  # noqa: BLE001 — Windows / minimal builds
        return False
    try:
        readline.set_completer(completer)
        readline.set_completer_delims(" \t\n")
        parse_and_bind = getattr(readline, "parse_and_bind", None)
        if parse_and_bind is not None:
            for binding in (
                # GNU readline spelling …
                "set show-all-if-ambiguous on",
                "set completion-ignore-case on",
                "tab: complete",
                # … and the libedit spelling (measured: a Python built against libedit
                # ignores the GNU lines, so Tab inserts a literal tab and never completes)
                "bind ^I rl_complete",
            ):
                try:
                    parse_and_bind(binding)
                except Exception:  # noqa: BLE001 — the other implementation ignores it
                    continue
        if display is not None:
            setter = getattr(readline, "set_completion_display_matches_hook", None)
            if callable(setter):
                try:
                    setter(display)
                except Exception:  # noqa: BLE001 — libedit's shim has no such hook
                    pass
        if history_file:
            try:
                readline.read_history_file(os.fspath(history_file))
            except OSError:
                pass
    except Exception:  # noqa: BLE001
        return False
    return True


def main(argv: Sequence[str] | None = None) -> int:
    """``python -m skaldir.complete "<partial line>"`` — a read-only completion probe."""
    args = list(sys.argv[1:] if argv is None else argv)
    from .cli import build_parser

    tree = build_tree(build_parser())
    line = args[0] if args else ""
    completion = complete_line(tree, line, len(line))
    print(f"line     : {line!r}")
    print(f"insert   : {completion.insert!r}")
    print(f"would be : {completion.completed_line()!r}")
    for item in completion.suggestions[:20]:
        print(f"  {item.line()}")
    return EXIT_OK


__all__ = [
    "Completion",
    "LineCompleter",
    "Node",
    "Option",
    "Positional",
    "Suggestion",
    "VALUE_CHECKPOINT",
    "VALUE_FILE",
    "VALUE_GOAL",
    "VALUE_LIMIT",
    "VALUE_MODEL",
    "VALUE_PROVIDER",
    "VALUE_WORKFLOW",
    "VALUE_RUN",
    "VALUE_SESSION",
    "VALUE_SKILL",
    "VALUE_SOURCES",
    "VALUE_TABLE",
    "build_tree",
    "checkpoint_values",
    "complete_line",
    "file_suggestions",
    "fuzzy_filter",
    "fuzzy_score",
    "goal_values",
    "help_overlay",
    "install_readline",
    "known_paths",
    "longest_common_prefix",
    "main",
    "menu_lines",
    "model_values",
    "node_for",
    "path_for",
    "provider_values",
    "run_values",
    "runtime_dir",
    "session_values",
    "skill_values",
    "slash_args",
    "suggest",
    "value_kind_for",
    "value_suggestions",
    "workflow_values",
]


if __name__ == "__main__":  # pragma: no cover — a probe, not a product path
    raise SystemExit(main())
