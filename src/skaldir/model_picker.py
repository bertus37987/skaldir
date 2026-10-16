"""``skaldir model`` — pick the provider and the model behind one of the two roles.

The product has exactly two keys (``AGENTS.md``): a **TypeSafe/Jev provider** (the
always-armed decision layer) and **one LLM provider** (the general model). This module
is the picker that changes one of them without re-running the whole first-run flow:
it asks the **role** first, then the **provider** — every row carries the base URL the
catalog already knows, so accepting a row *is* the endpoint answer — then the **model**,
pre-filled from the catalog's own model list, and finally the key, **hidden**, written
to the ``.env`` beside the config with mode ``0600`` (``setup_flow.write_env_file``)
while ``config.yaml`` names the variable only.

Three surfaces, one engine:

* ``skaldir model`` (``model_cli.py``) — the interactive terminal flow and the
  non-interactive flags; both call :func:`plan_for` + :func:`apply_choice`;
* the in-chat ``/model`` (this module's :func:`handle_slash`) — the *same* two calls,
  rendered as a selection list inside the chat frame, so a choice made in the session
  is the identical write a choice made in the terminal performs;
* ``/provider`` (bare) — the same list, read-only; ``/setup`` — pointed at the picker
  when a mandatory key is missing.

Nothing here invents a value: the base URL, the model list, the credential **variable
name** and the provider notes all come from ``provider_registry_data`` /
``provider_catalog``. Only the key itself is ever asked for, and it is never echoed,
never logged, and never reaches ``config.yaml``.
"""

from __future__ import annotations

import importlib
import inspect
import os
import re
import select
import shutil
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import provider_catalog as catalog
from . import provider_config as pc
from . import provider_registry_data as registry
from . import setup_flow as sf

__all__ = [
    "MODEL_ACTION",
    "PICKER_HINT",
    "PICKER_ROWS",
    "SLASH_MODEL_VERBS",
    "SLASH_PROVIDER_VERB",
    "SLASH_SETUP_VERB",
    "SLASH_SNIPPET",
    "ModelChoice",
    "PickRow",
    "Plan",
    "apply_choice",
    "check_choice",
    "check_exit",
    "check_role",
    "default_role",
    "handle_slash",
    "is_model_verb",
    "key_provenance",
    "model_choices",
    "plan_for",
    "provider_rows",
    "report_lines",
    "resolve_option",
    "resolve_role",
    "role_rows",
    "role_state",
    "row_lines",
    "select_in_frame",
    "write_role_document",
]

#: The ``action`` every payload from this module carries.
MODEL_ACTION = "model"

#: How many rows a picker page shows (a list is scanned, not read).
PICKER_ROWS = 12

#: The most rows any list shows at once — the owner's rule for a 24-line terminal, and the
#: bound the shared widget is being built to. Everything past it is counted, not dropped.
PICKER_MAX_ROWS = 10

#: The one line that says which keys the picker answers (drawn in the frame's hint row).
PICKER_HINT = "Enter selects · ↑/↓ move · type to narrow · Esc cancels"

#: The slash spellings the in-chat hook answers for. ``/provider`` is only intercepted
#: in its **bare** form — ``/provider test deepseek`` is the real command and stays one.
SLASH_MODEL_VERBS: tuple[str, ...] = ("/model", "/models")
SLASH_PROVIDER_VERB = "/provider"
SLASH_SETUP_VERB = "/setup"

#: The order the *default* role is picked in: the general model first, because a fresh
#: install can answer nothing without it. (The rows are listed in ``MANDATORY_ROLES``
#: order, which is the order the owner names the two keys in.)
DEFAULT_ROLE_ORDER: tuple[str, ...] = (sf.ROLE_LLM, sf.ROLE_TYPESAFE)

#: Providers a **decision layer** should use, in the order the picker shows them for
#: ``ROLE_TYPESAFE``. The first row is the decision layer's own endpoint
#: (:data:`skaldir.provider_registry_data.REGISTRY`'s ``typesafe``: Jev at
#: ``api.typesafe.ai/v1/systemone``) — that is what "TypeSafe" means, and before this
#: list named it the role was answered with whichever cheap chat model happened to be
#: installed, silently. The rest are **compatibility backends**: an ordinary LLM asked to
#: answer Jev's typed questions through ``LLMSystemOneAdapter``. They stay selectable
#: (nothing is hidden, and an installation without a TypeSafe key has to be able to
#: answer the two mandatory keys), but the row says which one it is.
TYPESAFE_NATIVE_ID = "typesafe"
TYPESAFE_PREFERRED: tuple[str, ...] = (
    TYPESAFE_NATIVE_ID,
    "deepseek",
    "openrouter",
    "opencode-go",
    "llamacpp",
    "local",
)

#: The sentence every non-native row carries for the decision role, and the one a
#: native decision row carries for the general role. Both are product text: a user
#: choosing a provider for a role has to be able to see which side of the line a row
#: is on without reading the source.
COMPATIBILITY_NOTE = "compatibility backend: an LLM answering Jev's questions, not TypeSafe"
DECISION_ONLY_NOTE = (
    "decision endpoint (Jev) — it cannot answer chat, so the LLM role needs another provider"
)

#: What each role is for, in the picker's own words (English, product text). The
#: picker presents the two keys as **agent** (the general model) and **voice**
#: (which then asks LLM or Jev as its voice backing) — labeling only: the values
#: stay the two schema keys (``ROLE_LLM`` / ``ROLE_TYPESAFE``), and nothing about
#: the two-key write changes.
ROLE_BLURB: dict[str, str] = {
    sf.ROLE_TYPESAFE: (
        "voice: backs the answering voice with Jev — or an LLM answering Jev's"
        " typed questions"
    ),
    sf.ROLE_LLM: (
        "agent: the general model that answers your messages and drives the"
        " work"
    ),
}

#: The short name of each role, for the picker's name column (the long label is the
#: report's job: a row is scanned, not read).
ROLE_SHORT: dict[str, str] = {
    sf.ROLE_TYPESAFE: "voice (Jev)",
    sf.ROLE_LLM: "agent (LLM)",
}

#: The picker's own two presentation words, and the key each one stands for.
ROLE_AGENT = "agent"
ROLE_VOICE = "voice"
ROLE_PRESENTATION: tuple[str, ...] = (ROLE_VOICE, ROLE_AGENT)

#: The second-level question a ``voice`` pick leads to: LLM or Jev as the voice backing.
VOICE_BACKING_ROWS: tuple[tuple[str, str], ...] = (
    ("llm", "LLM: an ordinary chat model answering Jev's typed questions"),
    ("jev", "Jev: the TypeSafe decision endpoint itself (api.typesafe.ai)"),
)


def voice_backing_rows() -> tuple[PickRow, ...]:
    """The second-level rows: LLM or Jev as the voice backing (the ``voice`` pick)."""
    return tuple(
        PickRow(f"{number} {word}", note, "", word)
        for number, (word, note) in enumerate(VOICE_BACKING_ROWS, start=1)
    )


def resolve_voice_backing(answer: str) -> str:
    """An LLM / Jev answer to the ``voice`` question → the decision role key.

    ``""`` means "not an answer". The mapping is the owners': LLM and Jev are both
    the ``ROLE_TYPESAFE`` key underneath — they differ only in which provider row
    backs the voice (a compatibility backend vs the native decision endpoint).
    """
    text = str(answer or "").strip().lower()
    if text in ("llm", "1"):
        return sf.ROLE_TYPESAFE
    if text in ("jev", "typesafe", "2"):
        return sf.ROLE_TYPESAFE
    return ""


# --------------------------------------------------------------------------- #
# what is configured right now — measured, never guessed                       #
# --------------------------------------------------------------------------- #


def role_state(
    *, path: str | Path | None = None, env: Mapping[str, str] | None = None
) -> dict[str, dict[str, Any]]:
    """What each role is configured with *now*: entry, endpoint, model, credential.

    Read through ``setup_flow.read_roles`` (the same re-read ``skaldir setup`` uses), so
    the picker's pre-selection and its report cannot disagree with the config file. A
    config that cannot be read is reported per role instead of raised.
    """
    try:
        payload = sf.read_roles(path=path, env=env)
    except Exception as exc:  # noqa: BLE001 — a report states what it could not read
        return {
            role: {"error": f"{type(exc).__name__}: {exc}"[:200]} for role in sf.MANDATORY_ROLES
        }
    out: dict[str, dict[str, Any]] = {}
    for role in sf.MANDATORY_ROLES:
        slot = (payload.get("roles") or {}).get(role) or {}
        entry = slot.get("entry") or {}
        out[role] = {
            "role": role,
            "label": sf.ROLE_LABEL[role],
            "entry": sf.ENTRY_NAME[role],
            "block": "godsv.llm" if role == sf.ROLE_LLM else f"godsv.{sf.JEV_BLOCK}",
            "present": bool(entry),
            "ready": bool(entry.get("ready")),
            "credential": str(entry.get("credential") or ("unknown" if entry else "unset")),
            "base_url": str(entry.get("base_url") or ""),
            "model": str(entry.get("model") or ""),
            "api_key_env": str(entry.get("api_key_env") or ""),
            "missing": list(entry.get("missing") or []),
        }
    return out


def default_role(*, path: str | Path | None = None, env: Mapping[str, str] | None = None) -> str:
    """The role the picker pre-selects: the first one that is not usable yet.

    ``llm`` is tried first on purpose: a fresh install cannot answer anything without the
    general model, and the decision layer's question defaults to the same endpoint anyway.
    """
    states = role_state(path=path, env=env)
    for role in DEFAULT_ROLE_ORDER:
        if not bool((states.get(role) or {}).get("ready")):
            return role
    return sf.ROLE_LLM


def resolve_role(answer: str) -> str:
    """A role row number (``1``/``2``) or a name (``agent``/``voice``/``jev``/``llm``) → the role.

    ``voice`` answers the decision key (``ROLE_TYPESAFE``) — it is the picker's word
    for that key; LLM-vs-Jev is the second-level question (:func:`resolve_voice_backing`).
    ``""`` means "not an answer" — the caller asks again rather than guessing a key.
    """
    text = str(answer or "").strip().lower()
    if not text:
        return ""
    if text.isdigit():
        number = int(text)
        if 1 <= number <= len(sf.MANDATORY_ROLES):
            return sf.MANDATORY_ROLES[number - 1]
        return ""
    aliases = {
        # the picker's own words first: agent = the general key, voice = the decision
        # key (LLM-vs-Jev is the second-level question, which also lands here)
        "agent": sf.ROLE_LLM,
        "voice": sf.ROLE_TYPESAFE,
        "jev": sf.ROLE_TYPESAFE,
        "typesafe": sf.ROLE_TYPESAFE,
        "decision": sf.ROLE_TYPESAFE,
        "decide": sf.ROLE_TYPESAFE,
        "llm": sf.ROLE_LLM,
        "model": sf.ROLE_LLM,
        "general": sf.ROLE_LLM,
    }
    if text in aliases:
        return aliases[text]
    for role in sf.MANDATORY_ROLES:
        if text == role:
            return role
    return ""


def resolve_option(answer: str, rows: Sequence[PickRow]) -> str:
    """A row number, a row's value, or the text itself (a typed id) — never a guess.

    ``""`` means "no answer": for a provider that is a refusal (the id must exist in the
    catalog anyway), for a model it means "use the pre-filled one".
    """
    text = str(answer or "").strip()
    if not text:
        return ""
    if text.isdigit():
        number = int(text)
        return rows[number - 1].value if 1 <= number <= len(rows) else ""
    wanted = text.lower()
    for row in rows:
        if wanted == str(row.value).lower():
            return row.value
    return text


# --------------------------------------------------------------------------- #
# the rows: a role, a provider, a model — each with the value it stands for    #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PickRow:
    """One selectable row: what it shows, its small mark, and the value it stands for."""

    name: str
    description: str = ""
    mark: str = ""
    value: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "description": self.description,
            "mark": self.mark,
            "value": self.value,
        }


@dataclass(frozen=True)
class ModelChoice:
    """One model id the catalog itself names, with where that name came from."""

    id: str
    note: str = ""
    source: str = ""
    default: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "note": self.note, "source": self.source, "default": self.default}


def _model_rank(model_id: str) -> tuple:
    """Newest-first sort key for a model id (``mimo-v2.6-pro`` before ``mimo-v2.5``).

    Version-like integers win; ``pro``/``max``/``high`` beat ``plus``/``flash`` beat
    the bare id. Only used to *order* a list the catalog already names — never to
    invent an id.
    """
    text = str(model_id or "").lower()
    numbers = tuple(int(part) for part in re.findall(r"\d+", text))
    tier = 0
    if any(tag in text for tag in ("pro", "max", "high", "ultra")):
        tier = 3
    elif any(tag in text for tag in ("plus", "large")):
        tier = 2
    elif any(tag in text for tag in ("flash", "mini", "lite", "nano")):
        tier = 1
    return (numbers, tier, text)


def model_choices(provider_id: str) -> tuple[ModelChoice, ...]:
    """The model ids one provider offers, **newest first**.

    ``provider_catalog.CATALOG[id].models`` is the documented list; the entry's
    ``default_model`` and the registry's own default are added when the list does not
    already carry them. The newest id is marked ``default`` so a first Enter takes
    ``mimo-v2.6-pro`` rather than last year's pin. A provider nobody documented a model
    for returns an **empty** tuple — the picker then asks for an id instead of inventing one.
    """
    wanted = str(provider_id or "").strip().lower()
    # a plan row id is `<provider>#<sku>` — the model list is the provider's own
    wanted = wanted.split("#", 1)[0]
    found: list[ModelChoice] = []
    seen: set[str] = set()

    def add(model_id: Any, note: str, source: str, *, default: bool = False) -> None:
        text = str(model_id or "").strip()
        if not text or text in seen:
            return
        seen.add(text)
        found.append(ModelChoice(text, note, source, default))

    entry = catalog.CATALOG.get(wanted)
    if entry is not None:
        for model_id in entry.models:
            add(
                model_id,
                "documented for this endpoint by the provider catalog",
                "catalog",
                default=str(model_id) == str(entry.default_model),
            )
        add(entry.default_model, "the catalog's documented default", "catalog", default=True)
    spec = registry.REGISTRY.get(wanted)
    if spec is not None:
        add(spec.default_model, "the registry's default model", "registry", default=not found)
    # newest first: the owner's rule ("immer die neusten modelle … z.b v2.6 pro").
    # The first row is the default a first Enter takes — never last year's pin.
    ordered = sorted(found, key=lambda item: _model_rank(item.id), reverse=True)
    if ordered:
        ordered[0] = ModelChoice(
            ordered[0].id,
            ordered[0].note or "newest id in the list",
            ordered[0].source,
            True,
        )
        ordered = [
            item if index == 0 else ModelChoice(item.id, item.note, item.source, False)
            for index, item in enumerate(ordered)
        ]
    return tuple(ordered)


def role_rows(*, state: Mapping[str, Mapping[str, Any]] | None = None) -> tuple[PickRow, ...]:
    """The two presentation words — **voice** and **agent** — with the measured state.

    ``voice`` stands for the decision key (``ROLE_TYPESAFE``): choosing it moves on to
    the second-level question (LLM or Jev as the voice backing). ``agent`` stands for
    the general key (``ROLE_LLM``). Labels only — the two-key write is unchanged.
    """
    states = dict(state) if state is not None else role_state(env=dict(os.environ))
    rows: list[PickRow] = []
    for role in sf.MANDATORY_ROLES:
        item = states.get(role) or {}
        if not item.get("present"):
            mark = "not set up"
        elif item.get("credential") == "unset":
            mark = "no key"
        elif item.get("ready"):
            mark = "configured"
        else:
            mark = "incomplete"
        rows.append(
            PickRow(
                name=f"{ROLE_SHORT[role]}",
                description=ROLE_BLURB[role],
                mark=mark,
                value=role,
            )
        )
    return tuple(rows)


def provider_rows(
    choices: Sequence[sf.ProviderChoice] | None = None, *, current: str = "", role: str = ""
) -> tuple[PickRow, ...]:
    """The catalog rows — name, base URL, one-line note — exactly as the picker shows them.

    Every row carries the endpoint, which is the whole point: choosing a provider *is*
    the endpoint answer, so nobody retypes a URL the catalog already knows.
    """
    material = list(choices) if choices is not None else list(sf.provider_choices())
    # The decision layer gets its own endpoint first, then the compatibility backends;
    # the general role keeps the catalog's order (the native decision row is labelled
    # there instead — it is never hidden, it is never mistakable for a chat provider).
    typesafe = str(role or "") == sf.ROLE_TYPESAFE
    if typesafe:
        # the preference list is the *order*, not a filter: the native decision endpoint
        # comes first even though the registry lists it after the aggregators
        rank = {provider_id: index for index, provider_id in enumerate(TYPESAFE_PREFERRED)}
        preferred = sorted(
            (c for c in material if c.id in rank), key=lambda c: (rank[c.id], c.id)
        )
        rest = [c for c in material if c.id not in rank]
        material = preferred + rest
    rows: list[PickRow] = []
    for number, choice in enumerate(material, start=1):
        note = str(choice.note or "")
        if typesafe and choice.id != TYPESAFE_NATIVE_ID:
            note = f"{note} · {COMPATIBILITY_NOTE}" if note else COMPATIBILITY_NOTE
        elif not typesafe and not choice.speakable:
            # a row the OpenAI-shaped client cannot speak: say it here rather than let a
            # user pick a decision endpoint for the role that answers chat
            note = f"{note} · {DECISION_ONLY_NOTE}" if note else DECISION_ONLY_NOTE
        suffix = f" — {note}" if note else ""
        row_id = choice.provider_id or choice.id.split("#", 1)[0]
        sku_note = f" [{choice.sku}]" if getattr(choice, "sku", "") else ""
        rows.append(
            PickRow(
                name=f"{number} {choice.label}{sku_note}"
                + (" (local)" if choice.local else ""),
                description=f"{choice.base_url}{suffix}",
                mark="current" if row_id == current or choice.id == current
                else str(choice.verification or ""),
                value=choice.id,
            )
        )
    return tuple(rows)


def model_rows(provider_id: str, *, current: str = "") -> tuple[PickRow, ...]:
    """The model rows for one provider, pre-filled from the catalog's own list."""
    rows: list[PickRow] = []
    for number, choice in enumerate(model_choices(provider_id), start=1):
        if choice.id == current:
            mark = "current"
        elif choice.default:
            mark = "default"
        else:
            mark = choice.source
        rows.append(PickRow(f"{number} {choice.id}", choice.note, mark, choice.id))
    return tuple(rows)


def live_model_rows(
    provider_id: str, *, role: str, env: Mapping[str, str] | None = None,
    config: str | None = None, base_url: str = "", env_file: str = "", discover: bool = True,
) -> tuple[PickRow, ...]:
    """Interactive-only discovery; credentials and current IDs are bound to exact endpoints."""
    from . import model_discovery as md
    from .provider_model_data import CATALOG_DATE, PROVIDERS

    env_map = dict(os.environ if env is None else env)
    path = config or env_map.get("SKALDIR_LLM_CONFIG") or env_map.get("GODSV_LLM_CONFIG")
    choice = sf.provider_choice(provider_id)
    data = PROVIDERS.get(choice.provider_id or choice.id.split("#", 1)[0], {})
    endpoint = base_url or choice.base_url
    state = role_state(path=path, env=env_map).get(role) or {}
    bound = bool(state.get("base_url")) and str(state["base_url"]).rstrip(
        "/"
    ) == endpoint.rstrip("/")

    current = str(state.get("model") or "") if bound else ""
    key_names = []
    if bound and state.get("api_key_env"):
        key_names.append(str(state["api_key_env"]))
    # Never send a provider key to an arbitrary --base-url override.
    if endpoint.rstrip("/") == choice.base_url.rstrip("/"):
        spec = registry.REGISTRY.get(choice.provider_id or choice.id.split("#", 1)[0])
        key_names.extend(spec.primary_endpoint.api_key_env if spec else (choice.api_key_env,))
    key = ""
    if key_names:
        seen = sf.read_env_file(sf.env_file_path(config_path=path, explicit=env_file, env=env_map))
        key = next((str(env_map.get(name) or seen.value(name)) for name in key_names
                    if name and (env_map.get(name) or seen.value(name))), "")
    result = md.DiscoveryResult((), "unavailable", "saved catalog")
    if discover and data.get("discovery", choice.speakable):
        result = md.discover_models(endpoint, api_key=key, protocol=data.get("protocol", "openai"))
    live = result.source == "live" and bool(result.ids)
    ids = list(result.ids) if live else [
        item.id for item in model_choices(choice.provider_id or choice.id)
    ]
    # Current may be a private fine-tune; preserve it explicitly, never claim it was listed.
    custom = bool(current and current not in ids)
    preferred = current or (choice.model if choice.model in ids else "")
    if preferred:
        ids = [preferred, *(item for item in ids if item != preferred)]
    return tuple(
        PickRow(
            f"{number} {model}",
            "Listed by endpoint; tool support not tested"
            if live else f"Saved catalog {CATALOG_DATE}",
            ("current/custom" if custom else "current") if model == current
            else ("live" if live else "catalog/offline"), model,
        )
        for number, model in enumerate(ids, start=1)
    )


#: The pointer glyphs of a selection list (the palette's own two, folded by mode).
PICKER_POINTER_RICH = "❯"
PICKER_POINTER_PLAIN = ">"


def _fold_ascii(text: Any) -> str:
    """The ASCII twin of a row string (an arrow becomes ``->``-safe plain text)."""
    return str(text).encode("ascii", "replace").decode("ascii")


def _fit(text: Any, limit: int, *, unicode: bool = True) -> str:
    body = str(text) if unicode else _fold_ascii(text)
    return body[: max(0, int(limit))]


def _elide(text: Any, limit: int, *, unicode: bool = True) -> str:
    """Clip to ``limit`` cells, **saying so** — a row never ends mid-word in silence.

    ``…`` on a terminal, ``...`` in plain mode; the result is never wider than ``limit``,
    so the exact-width arithmetic of a frame line is untouched.
    """
    body = str(text) if unicode else _fold_ascii(text)
    room = max(0, int(limit))
    if len(body) <= room:
        return body
    mark = "…" if unicode else "..."
    if len(mark) >= room:
        return body[:room]
    return body[: room - len(mark)] + mark


def _pad(text: Any, width: int, *, unicode: bool = True) -> str:
    return _fit(text, width, unicode=unicode).ljust(max(0, int(width)))


def window_rows(
    rows: Sequence[PickRow], *, selected: int, limit: int
) -> tuple[list[PickRow], int, int]:
    """``(the rows to draw, the pointer's index inside them, how many are not drawn)``.

    A list longer than the space it is drawn in has to scroll. Drawing ``rows[:limit]`` with the
    pointer as an **absolute** index silently drops the pointer the moment it moves past ``limit``:
    the list looks unresponsive — ``PgDn`` or ``End`` was pressed, the selection did move, and no
    mark is anywhere on screen — which is a dead key for the user. The window therefore follows
    the pointer, and the count of rows that are not drawn is passed back instead of guessed.
    """
    material = list(rows)
    total = len(material)
    room = int(limit)
    if not material:
        return [], 0, 0
    if room <= 0 or total <= room:
        return material, max(0, min(int(selected), total - 1)), 0
    index = max(0, min(int(selected), total - 1))
    start = max(0, min(index - room + 1, total - room))
    return material[start:start + room], index - start, total - room


def row_lines(
    rows: Sequence[PickRow],
    width: int,
    *,
    selected: int = -1,
    max_rows: int = PICKER_ROWS,
    hidden: int = 0,
    margin: int = 2,
    unicode: bool = True,
) -> list[str]:
    """A selection list as lines of exactly ``width`` cells — pointer, name, detail, mark.

    The same columns the palette uses (``chatbar.palette_rows``), drawn here because a
    picker row is not a slash verb: it has no ``command``/``slash`` shape, and a row that
    pretended to be one would be a lie in the palette's own vocabulary. A list longer than
    ``max_rows`` says how many rows it is not showing — the count is passed in, never
    guessed from the slice.
    """
    material = list(rows)
    shown = material if int(max_rows) <= 0 else material[: int(max_rows)]
    pointer = PICKER_POINTER_RICH if unicode else PICKER_POINTER_PLAIN
    names = [_fold_ascii(row.name) for row in shown]
    name_width = min(24, max(10, max((len(item) for item in names), default=10) + 2))
    lines: list[str] = []
    for index, row in enumerate(shown):
        head = " " * max(0, int(margin)) + (pointer if index == selected else " ") + " "
        name = _elide(row.name, name_width, unicode=unicode).ljust(name_width)
        mark = _fit(row.mark, 10, unicode=unicode)
        room = max(0, int(width) - len(head) - name_width - len(mark) - 1)
        detail = _elide(row.description, room, unicode=unicode)
        row_line = head + name + detail.ljust(room) + " " + mark
        lines.append(row_line[: int(width)].ljust(int(width)))
    rest = max(0, int(hidden) + (len(material) - len(shown)))
    if rest:
        more = f"... {rest} more" if not unicode else f"… {rest} more"
        lines.append((" " * max(0, int(margin)) + more)[: int(width)].ljust(int(width)))
    return lines


# --------------------------------------------------------------------------- #
# the plan: what a choice means, before anything is written                    #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Plan:
    """One role's chosen provider, endpoint and model — no credential anywhere."""

    role: str
    provider: str
    base_url: str
    model: str
    api_key_env: str = ""
    label: str = ""
    provider_label: str = ""
    local: bool = False
    verification: str = ""
    model_source: str = ""
    notes: str = ""
    #: ``typesafe`` (the System One endpoint) or ``llm`` (a compatibility backend) — the
    #: value written into ``godsv.jev`` so the runtime never has to guess from a model
    #: name. Empty for the general role, which is not on that line at all.
    decision_layer: str = ""

    def key_spec(self, *, timeout_s: float | None = None) -> sf.KeySpec:
        """The ``KeySpec`` the write and the validation both run through."""
        return sf.key_spec(
            self.role,
            base_url=self.base_url,
            model=self.model,
            api_key_env=self.api_key_env,
            catalog_id=self.provider,
            decision_layer=self.decision_layer,
            timeout_s=timeout_s,
        )

    def credential_name(self) -> str:
        """The variable the key lives under (``setup_flow``'s own rule)."""
        return sf.credential_name(self.key_spec(), self.role)

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "label": self.label or sf.ROLE_LABEL[self.role],
            "provider": self.provider,
            "provider_label": self.provider_label or self.provider,
            "base_url": self.base_url,
            "model": self.model,
            "model_source": self.model_source,
            "api_key_env": self.api_key_env,
            "local": self.local,
            "verification": self.verification,
            "notes": self.notes,
            "decision_layer": self.decision_layer,
        }


def plan_for(
    role: str,
    *,
    provider: str = "",
    model: str = "",
    base_url: str = "",
    env: Mapping[str, str] | None = None,
) -> Plan:
    """Resolve one role's choice from the flags — nothing is written and nobody is asked.

    ``provider`` names a catalog row and supplies the base URL *and* the credential
    variable; ``model`` overrides the row's default, and when neither names one the
    catalog's own first model is used. A provider the catalog does not know, or one that
    names no model at all, is refused with a named error — never filled in with a guess.
    """
    if role not in sf.MANDATORY_ROLES:
        raise sf.SetupRefused(
            f"unknown role {role!r} — the two roles are {list(sf.MANDATORY_ROLES)}"
        )
    picked = str(provider or "").strip() or sf.default_provider(role, env=env)
    supplied = sf.provider_defaults(picked)  # refuses an unknown id, by name
    choice = sf.provider_choice(picked)
    # the general role needs a chat endpoint, the decision role is the only one that may
    # take the System One endpoint — both refused by name, before a key is asked for
    sf.refuse_unspeakable(role, choice.id)
    typed = str(model or "").strip()
    chosen = typed or supplied["model"]
    source = "typed" if typed else ("catalog" if chosen else "")
    if not chosen:
        listed = model_choices(supplied["provider"])
        if listed:
            chosen, source = listed[0].id, listed[0].source
    if not chosen:
        raise sf.SetupRefused(
            f"{choice.label} names no model in the catalog — give the id "
            f"(skaldir model --model <id> · in the chat `/model {choice.id} <id>`)"
        )
    return Plan(
        role=role,
        provider=supplied["provider"],
        base_url=str(base_url or "").strip() or supplied["base_url"],
        model=chosen,
        api_key_env=supplied["api_key_env"],
        label=sf.ROLE_LABEL[role],
        provider_label=supplied["label"],
        local=choice.local,
        verification=choice.verification,
        model_source=source,
        notes=choice.note,
        decision_layer=(
            sf.decision_layer_for(
                choice.base_url,
                kind=registry.KIND_NATIVE if not choice.speakable else "",
            )
            if role == sf.ROLE_TYPESAFE
            else ""
        ),
    )


# --------------------------------------------------------------------------- #
# the write: one role's blocks, the other role left exactly as it was          #
# --------------------------------------------------------------------------- #


def _stamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def write_role_document(
    plan: Plan,
    *,
    path: str | Path | None = None,
    env_file: sf.EnvFile | None = None,
    backup: bool = True,
) -> dict[str, Any]:
    """Merge **one** role into the config and write it atomically, with a backup.

    The other role's blocks are read back out of the file and written unchanged: changing
    the general model must never silently drop the decision layer's endpoint, and the
    document is the one ``skaldir setup`` writes (``godsv.llm`` / ``godsv.jev`` plus the
    named registry and the receipt), so the two commands cannot drift into two shapes.
    The credential is named (``api_key_env``), never stored — the store's own secret scan
    refuses a value.
    """
    target = pc.config_path(path)
    document: dict[str, Any] = {}
    try:
        document = dict(pc.load_store(target).document)
    except Exception:  # noqa: BLE001 — a missing or unreadable file starts a fresh document
        document = {}
    godsv = document.get("godsv")
    godsv_block: dict[str, Any] = dict(godsv) if isinstance(godsv, Mapping) else {}
    document["godsv"] = godsv_block

    entry = plan.key_spec().entry()
    jev_entry_block: dict[str, Any] = {}
    if plan.role == sf.ROLE_LLM:
        godsv_block["llm"] = entry.projection()
    else:
        block = entry.projection()
        block["stream"] = False
        # which side of the line: the System One endpoint itself, or the LLM stand-in.
        # Without this line the block claimed ``provider: openai-compatible`` for an
        # endpoint that has no chat path at all, and the runtime believed it.
        block[sf.SYSTEM_ONE_KEY] = plan.decision_layer or entry.extra.get(sf.SYSTEM_ONE_KEY, "")
        godsv_block[sf.JEV_BLOCK] = block
        jev_entry_block = entry.config_block()
        jev_entry_block[sf.SYSTEM_ONE_KEY] = block[sf.SYSTEM_ONE_KEY]

    registry_block = godsv_block.get(pc.REGISTRY_BLOCK)
    registry_block = dict(registry_block) if isinstance(registry_block, Mapping) else {}
    entries = dict(registry_block.get(pc.REGISTRY_ENTRIES) or {})
    entries[entry.name] = jev_entry_block or entry.config_block()
    registry_block[pc.REGISTRY_ENTRIES] = entries
    registry_block.setdefault(pc.REGISTRY_DEFAULT, sf.ENTRY_NAME[sf.ROLE_LLM])
    godsv_block[pc.REGISTRY_BLOCK] = registry_block

    receipt = godsv_block.get(sf.SETUP_SECTION)
    receipt = dict(receipt) if isinstance(receipt, Mapping) else {}
    receipt["contract_version"] = str(receipt.get("contract_version") or sf.SCHEMA_VERSION)
    receipt.setdefault("mandatory_keys", list(sf.MANDATORY_ROLES))
    keys = dict(receipt.get("keys") or {})
    keys[plan.role] = {
        "entry": entry.name,
        "block": "godsv.llm" if plan.role == sf.ROLE_LLM else f"godsv.{sf.JEV_BLOCK}",
        "base_url": plan.base_url,
        "model": plan.model,
        "api_key_env": plan.api_key_env,
        "catalog_id": plan.provider,
    }
    receipt["keys"] = keys
    receipt["updated_at"] = _stamp()
    if env_file is not None:
        wanted = {name for name in (plan.api_key_env,) if name}
        receipt["credentials"] = {
            "file": str(env_file.path),
            "mode": env_file.mode or sf.credentials_report(env_file).get("mode") or "",
            "names": [name for name in env_file.names if name in wanted or not wanted],
        }
    godsv_block[sf.SETUP_SECTION] = receipt

    report = sf.write_document(document, target, backup=backup)
    report["role"] = plan.role
    report["model"] = plan.model
    return report


# --------------------------------------------------------------------------- #
# the one apply path: pick → (write the .env) → validate live → write the config #
# --------------------------------------------------------------------------- #


def _failure_code(measured: Mapping[str, Any]) -> int:
    """A live validation that failed: no answer at all is a timeout, an answer is exit 2.

    The same five-way split ``skaldir setup`` uses (``setup_flow._check_exit``): a refusal
    with no credential is ``4``, a refusal that is not about a credential is ``3``, an
    endpoint that produced *nothing* within the budget is ``8``, and an HTTP answer that
    says no is a failed verification, ``2``. ``UNKNOWN`` never passes as success.
    """
    if measured.get("refused"):
        problem = str(measured.get("problem") or "")
        return sf.EXIT_NEEDS_CREDENTIALS if "credential" in problem else sf.EXIT_BLOCKED
    status = measured.get("status")
    if status == 408:
        return sf.EXIT_TIMEOUT
    problem = str(measured.get("problem") or "")
    if "Timeout" in problem or "timeout" in problem:
        return sf.EXIT_TIMEOUT
    if status is None and str(measured.get("phase") or "") == "completion":
        return sf.EXIT_TIMEOUT
    return sf.EXIT_VERIFICATION_FAILED


def _measurement_summary(measured: Mapping[str, Any]) -> dict[str, Any]:
    """The four measured facts ``skaldir setup`` reports, from the same probe."""
    raw_streaming = measured.get("streaming")
    raw_structured = measured.get("structured")
    streaming: Mapping[str, Any] = raw_streaming if isinstance(raw_streaming, Mapping) else {}
    structured: Mapping[str, Any] = (
        raw_structured if isinstance(raw_structured, Mapping) else {}
    )
    return {
        "model_echo": measured.get("model_echo"),
        "latency_ms": measured.get("latency_ms"),
        "credential": measured.get("credential"),
        "streaming": {
            "supported": streaming.get("supported"),
            "measured": streaming.get("measured"),
            "first_byte_ms": streaming.get("first_byte_ms"),
            "reason": streaming.get("reason", ""),
        },
        "structured": {
            "measured": structured.get("measured"),
            "ok": structured.get("ok"),
            "mode_used": structured.get("mode_used", ""),
            "native_json_schema": structured.get("native_json_schema"),
            "reason": structured.get("reason", ""),
        },
    }


def key_provenance(
    role: str, name: str, *, env: Mapping[str, str], seen: sf.EnvFile | None
) -> dict[str, Any]:
    """Where a key came from — with the entry point's own load taken into account.

    ``skaldir``'s front door folds the credential file into the process environment before
    any command runs (``env.load_env_file_into``), so from the inside a key that only ever
    lived in the ``.env`` looks exactly like an exported one. When the value the
    environment carries is byte-identical to the file's, the honest answer is **the file**,
    and the mechanism is named next to it so a reader can reproduce the reading.
    """
    where = dict(sf.key_available(role, name, env=env, env_file=seen))
    target = str(name or "").strip()
    if (
        str(where.get("kind") or "") == "environment"
        and target
        and seen is not None
        and seen.has(target)
        and str(env.get(target) or "") == seen.value(target)
    ):
        where["kind"] = "env-file"
        where["source"] = str(seen.path)
        where["note"] = "read out of the credential file by skaldir's own entry point"
    return where


def _retry_other_plan_sku(
    plan: Plan,
    *,
    measured: Mapping[str, Any],
    probe: Callable[..., dict[str, Any]],
    validation_env: Mapping[str, str],
    timeout_s: float | None,
    measure_streaming: bool,
) -> tuple[Plan, dict[str, Any], Any] | None:
    """Retry the provider's *other* plan endpoint after an auth-style failure.

    The owner's rule: detect pay-as-you-go vs token-plan automatically when the
    key prefix is unclear. A 401/403 is the signal that the SKU may be wrong;
    one retry on the other endpoint is honest, two guesses are not.
    """
    problem = str(measured.get("problem") or "")
    if not any(tag in problem for tag in ("401", "403", "Invalid API Key", "invalid_key")):
        return None
    entry = registry.REGISTRY.get(plan.provider)
    if entry is None:
        return None
    others = [
        ep for ep in entry.plan_endpoints()
        if ep.base_url.rstrip("/") != plan.base_url.rstrip("/") and ep.openai_compatible
    ]
    if len(others) != 1:
        return None  # 0 = nowhere to go; 2+ = a guess, not a detection
    other = others[0]
    alt = replace(plan, base_url=other.base_url)
    alt_spec = alt.key_spec(timeout_s=timeout_s)
    try:
        again = dict(
            probe(alt_spec, env=dict(validation_env), timeout_s=timeout_s,
                  measure_streaming=measure_streaming)
        )
    except Exception:  # noqa: BLE001 — a failed retry is just the original failure
        return None
    if again.get("ok"):
        return alt, again, alt_spec
    return None


def apply_choice(
    plan: Plan,
    *,
    key_value: str = "",
    key_source: str = "",
    env: Mapping[str, str] | None = None,
    env_file: str | Path | None = None,
    config_path: str | Path | None = None,
    write_env: bool = True,
    write_config: bool = True,
    backup: bool = True,
    prober: Callable[..., dict[str, Any]] | None = None,
    timeout_s: float | None = None,
    measure_streaming: bool = True,
) -> tuple[dict[str, Any], int]:
    """Apply one choice. Returns ``(report, exit_code)`` — never raises for a state.

    The order is ``skaldir setup``'s, for the same reasons: the credential is written
    **before** the validation call (a key the user just pasted must survive an endpoint
    that happens to be down) and the config **after** it (a configuration that could not
    be validated is never written). A remote endpoint with no credential anywhere — no
    typed key, nothing in the environment, nothing in the ``.env`` — stops here with
    ``EXIT_NEEDS_CREDENTIALS`` and **writes nothing at all**.

    ``prober`` is the live validation (default: ``setup_flow.measure_key`` — model echo,
    latency, streaming, structured output); a fake is how a test proves the state machine
    without a network.
    """
    env_map = dict(os.environ if env is None else env)
    spec = plan.key_spec(timeout_s=timeout_s)
    credential_path = sf.env_file_path(
        config_path=config_path, explicit=str(env_file or ""), env=env_map
    )
    seen = sf.read_env_file(credential_path)
    name = sf.credential_name(spec, plan.role)
    typed = (
        sf.refuse_key_value(key_value, name=name or "the key")
        if str(key_value or "").strip()
        else ""
    )
    where = (
        {"available": True, "name": name, "source": key_source or "prompt (hidden)",
         "kind": "stdin" if key_source == "stdin" else "typed"}
        if typed
        else key_provenance(plan.role, name, env=env_map, seen=seen)
    )
    validation_env = sf.key_env_for(env_map, seen)
    if typed and name:
        validation_env[name] = typed
    stored = typed
    if not stored and write_env and str(where.get("kind") or "") == "environment":
        # a key that lives only in this shell is copied into the file, so one file serves
        # every later run — setup_flow's own rule, and the report still names the environment.
        # A value the file already carries is never rewritten: a re-run stays a no-op.
        carried = str(env_map.get(str(where.get("source") or "")) or "").strip()
        if carried and str(seen.value(name) or "") != carried:
            stored = sf.refuse_key_value(carried, name=name or "the key")
            if name:
                validation_env[name] = stored

    report: dict[str, Any] = {
        "action": MODEL_ACTION,
        "checked_at": _stamp(),
        "plan": plan.to_dict(),
        "config_file": str(pc.config_path(config_path)),
        "env_file": str(credential_path),
        "key_sources": {plan.role: {"name": name, **dict(where)}},
        "wrote": False,
        "ok": False,
    }

    entry = spec.entry()
    if entry.needs_credential(validation_env):
        report["credentials_file"] = sf.credentials_report(seen, written=False)
        report["verdict"] = (
            f"no key for {name or 'this endpoint'} — nothing was written "
            f"(run `skaldir model` and paste it, or export {name or 'the key'})"
        )
        report["problem"] = f"{plan.provider_label} at {plan.base_url} has no credential"
        report["exit_meaning"] = sf.CHECK_EXIT_CODES
        return report, sf.EXIT_NEEDS_CREDENTIALS

    # 1. the credential file, before any call ---------------------------------- #
    if stored and write_env:
        try:
            written_env = sf.write_env_file(credential_path, {name: stored})
        except sf.SetupRefused as exc:
            report["problem"] = str(exc)[:300]
            report["verdict"] = "refused: the credential was not written"
            return report, sf.EXIT_BLOCKED
        except sf.SetupError as exc:
            report["problem"] = str(exc)[:300]
            report["verdict"] = "the credential file could not be written"
            return report, sf.EXIT_INTERNAL
        report["env_write"] = written_env
        seen = sf.read_env_file(credential_path)
    elif typed and not write_env:
        report["env_write"] = {
            "written": False,
            "reason": "--no-env-write: the key stays in the environment only",
        }

    # 2. one real validation call per measurement ------------------------------- #
    probe = prober or sf.measure_key
    try:
        measured = dict(
            probe(spec, env=validation_env, timeout_s=timeout_s,
                  measure_streaming=measure_streaming)
        )
    except sf.SetupRefused as exc:
        measured = {
            "ok": False,
            "refused": True,
            "problem": str(exc)[:300],
            "phase": "configuration",
            "credential": entry.credential_state(validation_env),
        }
    except Exception as exc:  # noqa: BLE001 — a failed validation is a reported result
        measured = {
            "ok": False,
            "problem": f"{type(exc).__name__}: {exc}"[:300],
            "phase": "completion",
            "credential": entry.credential_state(validation_env),
        }
    report["measure"] = _measurement_summary(measured)
    report["credentials_file"] = sf.credentials_report(seen, written=bool(stored and write_env))
    if not measured.get("ok"):
        # Auto-detect the plan SKU when the key prefix was unclear: a 401 on
        # pay-as-you-go is also what a token-plan key gets there (and the reverse).
        # Retry the provider's other plan endpoint once — never invent a key.
        swapped = _retry_other_plan_sku(
            plan,
            measured=measured,
            probe=probe,
            validation_env=validation_env,
            timeout_s=timeout_s,
            measure_streaming=measure_streaming,
        )
        if swapped is not None:
            plan, measured, spec = swapped
            report["plan"] = plan.to_dict()
            report["measure"] = _measurement_summary(measured)
            report["sku_auto"] = {
                "switched": True,
                "base_url": plan.base_url,
                "note": "the other plan endpoint answered — the key's SKU was inferred",
            }
    if not measured.get("ok"):
        report["problem"] = str(measured.get("problem") or "the endpoint did not validate")[:300]
        report["verdict"] = (
            f"{plan.provider_label}: the live validation failed — {report['problem']} "
            f"(the config was not written)"
        )
        report["exit_meaning"] = sf.CHECK_EXIT_CODES
        return report, _failure_code(measured)

    if not write_config:
        report["ok"] = True
        report["verdict"] = f"{plan.model} validated live; nothing written (--no-write)"
        return report, sf.EXIT_OK

    # 3. the config, atomically, with a backup --------------------------------- #
    try:
        report["write"] = write_role_document(
            plan, path=config_path, env_file=seen, backup=backup
        )
    except pc.ProviderSecretRefused as exc:
        report["problem"] = str(exc)[:300]
        report["verdict"] = "refused: the config would have stored a secret"
        return report, sf.EXIT_BLOCKED
    except pc.ProviderStoreError as exc:
        report["problem"] = str(exc)[:300]
        report["verdict"] = "the config could not be written"
        return report, sf.EXIT_INTERNAL

    # 4. re-read the file — the choice is applied, and the report says so ------- #
    reread = sf.read_roles(path=config_path, env=validation_env)
    slot = (reread.get("roles") or {}).get(plan.role) or {}
    report["reread"] = {
        "config_file": reread.get("config_file"),
        "present": bool(slot.get("entry")),
        "model": str((slot.get("entry") or {}).get("model") or ""),
        "base_url": str((slot.get("entry") or {}).get("base_url") or ""),
        "api_key_env": str((slot.get("entry") or {}).get("api_key_env") or ""),
        "matches": str((slot.get("entry") or {}).get("model") or "") == plan.model,
    }
    report["ok"] = bool(report["reread"]["matches"])
    report["wrote"] = True
    report["verdict"] = (
        f"{plan.label} now uses {plan.model} at {plan.base_url} — validated live, config written"
        if report["ok"]
        else "the config was written, but the re-read does not name the chosen model"
    )
    report["next_steps"] = {
        "chat": "skaldir chat",
        "check": "skaldir model --check",
        "other_role": "skaldir model --role " + ("typesafe" if plan.role == sf.ROLE_LLM else "llm"),
    }
    return report, sf.EXIT_OK if report["ok"] else sf.EXIT_VERIFICATION_FAILED


# --------------------------------------------------------------------------- #
# --check: re-validate what the file says, and write nothing                    #
# --------------------------------------------------------------------------- #


def check_role(
    role: str,
    *,
    path: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    prober: Callable[..., dict[str, Any]] | None = None,
    timeout_s: float | None = None,
    measure_streaming: bool = True,
) -> dict[str, Any]:
    """One role's key as the **config file** states it, proved by a real call.

    ``skaldir setup --check`` for a single role: the spec comes out of the file (never
    out of the flags), the credential out of the environment the caller hands in (the
    process plus the ``.env``), and a refusal or an unreachable endpoint is the
    *result* — reported, never raised.
    """
    if role not in sf.MANDATORY_ROLES:
        raise sf.SetupRefused(f"unknown role {role!r}")
    target = pc.config_path(path)
    label = sf.ROLE_LABEL[role]
    base: dict[str, Any] = {
        "role": role,
        "label": label,
        "entry": sf.ENTRY_NAME[role],
        "spec": {},
        "ok": False,
        "present": False,
        "problem": "",
    }
    try:
        store = pc.load_store(target, env=env)
    except Exception as exc:  # noqa: BLE001 — reported as the check's own result
        base["problem"] = f"cannot read {target}: {type(exc).__name__}: {exc}"[:300]
        return base
    entry = store.providers.get(sf.ENTRY_NAME[role])
    if entry is None and role == sf.ROLE_TYPESAFE:
        try:
            config = sf.typesafe_provider_config(path=target, env=env)
        except sf.SetupIncomplete:
            entry = None
        except pc.ProviderSecretRefused as exc:
            base.update({"ok": False, "present": True, "refused": True, "problem": str(exc)[:300]})
            return base
        except Exception as exc:  # noqa: BLE001 — reported, not raised
            base.update({"ok": False, "present": True, "problem": str(exc)[:300]})
            return base
        else:
            entry = pc.ProviderEntry(
                name=sf.ENTRY_NAME[role],
                kind=config.provider,
                base_url=config.base_url,
                model=config.model,
                api_key_env=config.api_key_env,
                tiers=dict(config.tier_models),
            )
    if entry is None:
        base["problem"] = f"{label} is not configured in {target} — run `skaldir model`"
        return base
    spec = sf.KeySpec(
        role=role,
        base_url=entry.base_url,
        model=entry.model,
        api_key_env=entry.api_key_env,
        tiers=dict(entry.tiers),
        timeout_s=entry.timeout_s,
    )
    base.update({"present": True, "spec": spec.redacted(env), "entry": entry.redacted(env)})
    probe = prober or sf.measure_key
    try:
        measured = dict(
            probe(spec, env=env, timeout_s=timeout_s, measure_streaming=measure_streaming)
        )
    except sf.SetupRefused as exc:
        base.update(
            {
                "refused": True,
                "problem": str(exc)[:300],
                "measure": {"measured": False, "reason": "the key was refused before any request"},
            }
        )
        return base
    except Exception as exc:  # noqa: BLE001 — a failed check is the check's result
        base.update(
            {
                "problem": f"{type(exc).__name__}: {exc}"[:300],
                "measure": {"measured": False, "reason": str(exc)[:200]},
            }
        )
        return base
    base["ok"] = bool(measured.get("ok"))
    base["measure"] = _measurement_summary(measured)
    base["status"] = measured.get("status")
    base["phase"] = measured.get("phase")
    if not base["ok"]:
        base["problem"] = str(measured.get("problem") or "the endpoint did not answer")[:300]
    return base


def _needs_credentials(item: Mapping[str, Any]) -> bool:
    problem = str(item.get("problem") or "")
    return bool(item.get("refused")) and ("credential" in problem or "no key" in problem)


def _timed_out(item: Mapping[str, Any]) -> bool:
    if item.get("refused"):
        return False
    if item.get("status") == 408:
        return True
    problem = str(item.get("problem") or "")
    if "Timeout" in problem or "timeout" in problem:
        return True
    return item.get("status") is None and str(item.get("phase") or "") == "completion"


def check_exit(roles: Mapping[str, Mapping[str, Any]]) -> int:
    """``--check``'s exit code: 0 only when every role in scope answered a real call.

    The order is the contract's: nothing configured is *blocked* (3), a remote endpoint
    with no credential *needs credentials* (4), no answer in time is a timeout (8), and
    anything else that failed is a failed verification (2). ``UNKNOWN`` never passes.
    """
    if not roles:
        return sf.EXIT_OK
    if all(bool(item.get("ok")) for item in roles.values()):
        return sf.EXIT_OK
    if any(not bool(item.get("present")) for item in roles.values()):
        return sf.EXIT_BLOCKED
    if any(_needs_credentials(item) for item in roles.values()):
        return sf.EXIT_NEEDS_CREDENTIALS
    if any(_timed_out(item) for item in roles.values()):
        return sf.EXIT_TIMEOUT
    return sf.EXIT_VERIFICATION_FAILED


def check_choice(
    *,
    role: str = "",
    path: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    env_file: str | Path | None = None,
    prober: Callable[..., dict[str, Any]] | None = None,
    timeout_s: float | None = None,
    measure_streaming: bool = True,
) -> tuple[dict[str, Any], int]:
    """``skaldir model --check``: re-validate the configured role(s) and write nothing.

    The credential file is folded into the environment first (exactly as ``setup --check``
    does), so a key that lives **only** in the ``.env`` is proved like an exported one.
    """
    target = pc.config_path(path)
    credential_path = sf.env_file_path(
        config_path=target, explicit=str(env_file or ""), env=env
    )
    seen = sf.read_env_file(credential_path)
    raw_env = dict(os.environ if env is None else env)
    check_env = sf.key_env_for(raw_env, seen)
    roles = [role] if role else list(sf.MANDATORY_ROLES)
    reports = {
        item: check_role(
            item,
            path=target,
            env=check_env,
            prober=prober,
            timeout_s=timeout_s,
            measure_streaming=measure_streaming,
        )
        for item in roles
    }
    code = check_exit(reports)
    payload: dict[str, Any] = {
        "action": "model-check",
        "checked_at": _stamp(),
        "config_file": str(target),
        "credentials_file": sf.credentials_report(seen),
        "roles": reports,
        "checked": roles,
        # where each key came from — the NAME, never the value (mirrors `setup --check`).
        # The *raw* environment is what decides the provenance: a value that only the
        # credential file carries must be reported as coming from the file, not from a
        # process environment the caller never exported.
        "key_sources": {
            item: {
                "name": str((reports[item].get("spec") or {}).get("api_key_env") or ""),
                **key_provenance(
                    item,
                    str((reports[item].get("spec") or {}).get("api_key_env") or ""),
                    env=raw_env,
                    seen=seen,
                ),
            }
            for item in roles
        },
        "ok": code == sf.EXIT_OK,
        "verdict": {
            sf.EXIT_OK: "every checked role answered a real call",
            sf.EXIT_BLOCKED: "a role is not configured — run `skaldir model`",
            sf.EXIT_NEEDS_CREDENTIALS: "a remote endpoint has no credential",
            sf.EXIT_VERIFICATION_FAILED: "a role is configured but does not work",
            sf.EXIT_TIMEOUT: "a provider did not answer in time",
        }.get(code, ""),
        "exit_meaning": sf.CHECK_EXIT_CODES,
    }
    return payload, code


# --------------------------------------------------------------------------- #
# the report as lines a human reads (the chat writes them above the frame)      #
# --------------------------------------------------------------------------- #


def _key_line(role: str, sources: Mapping[str, Any]) -> str:
    item = dict((sources or {}).get(role) or {})
    name = str(item.get("name") or "the key")
    if item.get("kind") == "typed":
        return f"key {name}: typed at the hidden prompt (not echoed, written 0600)"
    if item.get("available"):
        return f"key {name}: reused from {item.get('source') or 'the environment'}"
    return f"key {name}: not set"


def report_lines(payload: Mapping[str, Any]) -> tuple[str, ...]:
    """The measured story of one apply, as plain lines — never a credential."""
    plan = dict(payload.get("plan") or {})
    role = str(plan.get("role") or "")
    lines: list[str] = [
        f"model: {plan.get('model')} · provider {plan.get('provider')} · {plan.get('base_url')}",
        _key_line(role, payload.get("key_sources") or {}),
    ]
    measure = dict(payload.get("measure") or {})
    if measure:
        streaming = dict(measure.get("streaming") or {})
        structured = dict(measure.get("structured") or {})
        verdict = "yes" if streaming.get("supported") else "no"
        mode = str(structured.get("mode_used") or "not measured")
        lines.append(
            f"validated live: model echo {measure.get('model_echo') or 'n/a'} · "
            f"{measure.get('latency_ms')} ms · streaming {verdict} · structured {mode}"
        )
    env_write = payload.get("env_write") or {}
    if env_write.get("path"):
        lines.append(
            f"credentials: {env_write['path']} (mode {env_write.get('mode') or '?'}, "
            f"{'written' if env_write.get('written') else 'unchanged'})"
        )
    if payload.get("verdict"):
        lines.append(str(payload["verdict"]))
    return tuple(lines)


# --------------------------------------------------------------------------- #
# the picker inside the chat frame                                             #
# --------------------------------------------------------------------------- #


def _chatbar() -> Any:
    """The frame module, imported lazily so this module stands on its own."""
    from . import chatbar

    return chatbar


def _frame_of(shell: Any) -> Any:
    """The live frame of this shell, or ``None`` (a pipe, a test, no terminal)."""
    try:
        front = _chatbar().front_of(shell)
    except Exception:  # noqa: BLE001 — no frame module is not an error here
        return None
    bar = getattr(front, "bar", None)
    if bar is None or not bool(getattr(bar, "active", False)):
        return None
    return front


def _filtered(rows: Sequence[PickRow], query: str) -> list[PickRow]:
    """Narrow a list by what was typed — with two rules, both deliberate.

    An **all-digit** query is matched against the *start* of the row's name, which is where
    the row number lives (``13 llama.cpp (local server)``): typing ``13`` selects the
    thirteenth row, and typing ``1`` shows the rows that begin with a one. Anything else is
    a case-insensitive substring over the name, the detail and the value, so ``deep`` finds
    ``DeepSeek`` and ``stub`` finds a model id nobody listed. A row is never reordered —
    the catalog's order is the order.
    """
    wanted = str(query or "").strip().lower()
    if not wanted:
        return list(rows)
    if wanted.isdigit():
        return [row for row in rows if row.name.strip().lower().startswith(wanted)]
    return [
        row
        for row in rows
        if wanted in f"{row.name} {row.description} {row.mark} {row.value}".lower()
    ]


def _band(bar: Any) -> str:
    """The frame's own status band (the line a chosen model shows up on)."""
    band = getattr(bar, "band_text", None)
    if not callable(band):
        return ""
    try:
        return str(band())
    except Exception:  # noqa: BLE001 — a band that cannot be read is drawn empty
        return ""


def _picture(
    bar: Any, *, title: str, rows: Sequence[PickRow], selected: int, query: str, hidden: int
) -> tuple[list[str], int]:
    """The frame with the selection list in it: one picture, exactly ``width`` cells a row."""
    width = int(getattr(bar, "width", 80) or 80)
    unicode = bool(getattr(bar, "unicode", True))
    # the bar's own convention: ``0`` means "show every row" (as its palette does)
    max_rows = max(0, int(getattr(bar, "max_rows", PICKER_ROWS)))
    body = [_pad(title, width, unicode=unicode)]
    window, index, undrawn = window_rows(
        rows, selected=selected, limit=max_rows or len(rows)
    )
    body += row_lines(
        window, width, selected=index, max_rows=0, hidden=hidden + undrawn, unicode=unicode
    )
    if bool(getattr(bar, "screen", False)):
        return _screen().screen_lines(
            width,
            band=_band(bar),
            live="",
            rows=body,
            text=query,
            hint=PICKER_HINT,
            unicode=unicode,
        )
    return _chatbar().frame_lines(
        width, text=query, rows=body, hint_left="", hint_right=PICKER_HINT, unicode=unicode
    )


def _screen() -> Any:
    from . import chat_screen

    return chat_screen


def _paint(bar: Any, lines: Sequence[str], cursor: int) -> bool:
    """Draw the picker into the frame. ``False`` means the rows went above it instead.

    ``ChatBar._paint`` is the frame's one write path (erase, body, park the cursor) and
    the picker has to go through it to stay *inside* the frame — a second copy of that
    arithmetic is exactly how a frame stops being redrawable. The fallback keeps the
    session usable if that path ever disappears: the list is written above the frame,
    which still shows every row.
    """
    painter = getattr(bar, "_paint", None)
    if callable(painter):
        painter(list(lines), int(cursor))
        return True
    for line in lines:
        bar.write_above(str(line))
    return False


def _repaint(bar: Any) -> None:
    """Put the bar's **own** frame back (its text, its palette) after the picker."""
    draw = getattr(bar, "draw", None)
    if callable(draw):
        draw(force=True)


def _invalidate(bar: Any) -> None:
    """Forget the cached probe facts so the next paint reads the new configuration.

    The band's probes are cached for ``PROBE_TTL_S`` (2 s) — the picker just changed the
    very file the model probe reads, and a band that keeps showing the old model for two
    seconds would look exactly like a change that did not take.
    """
    cache = getattr(bar, "_probe_cache", None)
    if isinstance(cache, dict):
        cache.clear()
    reload_palette = getattr(bar, "reload_palette", None)
    if callable(reload_palette):
        reload_palette()


def select_in_frame(
    front: Any,
    *,
    title: str,
    rows: Sequence[PickRow],
    default: int = 0,
    allow_text: bool = False,
    timeout: float = 0.25,
) -> str | None:
    """Ask one question in the live frame. ``None`` = the user left without choosing.

    Keys: ``↑``/``↓`` (or ``k``/``j``) move, printable characters narrow the list (an
    all-digit query matches the row *numbers* on the left, so ``13`` is the thirteenth row;
    a word matches names, endpoints and ids), ``Backspace``/``Ctrl-U`` edit the filter,
    ``Enter`` selects — with ``allow_text`` an unmatched filter stands for itself, which is
    how a model id nobody documented is entered — ``PgUp``/``PgDn`` page, ``Home``/``End``
    jump to the first/last row *shown*, and ``Esc``/``Ctrl-C`` leave. The terminal is in
    cbreak for the duration and restored by the ``finally``; the frame is redrawn once the
    question is over, whatever the answer was.
    """
    bar = getattr(front, "bar", None)
    fd = int(getattr(bar, "fd", -1) or -1)
    material = list(rows)
    if bar is None or fd < 0 or not bool(getattr(bar, "active", False)):
        return None
    if not material and not allow_text:
        return None
    termios, _tty = _termios()
    if termios is None or _tty is None:
        return None
    query = ""
    selected = max(0, min(int(default), max(0, len(material) - 1)))
    saved = termios.tcgetattr(fd)
    try:
        # NOT ``tty.setcbreak``: that leaves ``ISIG`` on, so a real ``^C`` arrives as a
        # ``SIGINT`` and kills the whole run instead of cancelling the pick. ``_cbreak``
        # clears it too, so ``\x03`` reaches the key loop and is answered exactly like
        # ``Esc`` — the same rule ``skaldir setup``'s own selector follows.
        _cbreak(termios, fd, saved)
        try:
            while True:
                visible = _filtered(material, query)
                if visible:
                    selected = max(0, min(selected, len(visible) - 1))
                else:
                    selected = 0
                lines, cursor = _picture(
                    bar,
                    title=title,
                    rows=visible,
                    selected=selected,
                    query=query,
                    hidden=max(0, len(material) - len(visible)),
                )
                _paint(bar, lines, cursor)
                key = _chatbar().read_key(fd, timeout=timeout)
                if key is None:
                    if bool(getattr(bar, "_resized", False)):
                        handler = getattr(bar, "on_resize", None)
                        if callable(handler):
                            handler()
                    continue
                if not key:
                    continue
                if key == "enter":
                    if visible:
                        return visible[selected].value
                    if allow_text and query.strip():
                        return query.strip()
                    return ""
                if key in ("esc", "ctrl-c", "ctrl-d"):
                    return None
                if key in ("up", "k"):
                    selected = (selected - 1) % len(visible) if visible else 0
                elif key in ("down", "j"):
                    selected = (selected + 1) % len(visible) if visible else 0
                elif key in ("pageup", "pagedown"):
                    step = max(1, int(getattr(bar, "max_rows", PICKER_ROWS)))
                    if int(getattr(bar, "max_rows", PICKER_ROWS)) <= 0:
                        step = PICKER_ROWS
                    if visible:
                        landing = selected + (step if key == "pagedown" else -step)
                        selected = max(0, min(landing, len(visible) - 1))
                elif key == "home":
                    # first row of what is *shown*: on a filtered list "the top" is the
                    # top of the answer, not of the whole catalog
                    selected = 0
                elif key == "end":
                    selected = max(0, len(visible) - 1)
                elif key == "backspace":
                    query = query[:-1]
                elif key == "ctrl-u":
                    query = ""
                elif key in ("ctrl-l", "left", "right"):
                    # one column of rows: a horizontal cursor move has nothing to move
                    continue
                elif len(key) == 1 and key.isprintable():
                    # a printable key — a digit (the row number) or a letter — narrows the list
                    query += key
                    selected = 0
        except KeyboardInterrupt:  # a ^C that arrived as a signal anyway is a cancel
            return None
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)
        # the facts are forgotten *before* the repaint, so the paint that follows a choice
        # already carries the new model instead of a cached one
        _invalidate(bar)
        _repaint(bar)


def _termios() -> tuple[Any, Any]:
    """The terminal control module, or ``(None, None)`` on a platform without one."""
    try:
        import termios
        import tty
    except Exception:  # noqa: BLE001 — no termios means no framed picker
        return None, None
    return termios, tty


# --------------------------------------------------------------------------- #
# the standalone selector: the same rows and the same keys, without a frame     #
# --------------------------------------------------------------------------- #
#
# ``skaldir setup`` and ``skaldir model`` run on a plain terminal: there is no ``ChatBar``
# to paint into, so ``select_in_frame`` cannot be used and nothing here may import the
# frame. The rows, the filter and the key *names* are the same objects the framed picker
# uses (``PickRow``, ``row_lines``, ``_filtered``), so the same question asked by ``setup``
# and by ``/model`` cannot drift apart: one engine, two hosts.
#
# When the terminal cannot take the screen (a pipe, ``TERM=dumb``, ``NO_COLOR``, a captured
# stream, a size that cannot fit a list), :func:`choose` falls back to the plain questions
# this build had before there was a selector — the same rows printed as lines, the answer
# read as one line — so a script, a log or a ``--json`` consumer never sees screen control.

#: The environment switch that forces the plain questions.
PLAIN_SWITCH = "SKALDIR_PLAIN"

#: How many lines the selector keeps for itself: the title row, the ``… N more`` row and the
#: status/hint row.
CHROME_ROWS = 3

#: The keys the selector's own list answers, in the owner's words (the shared widget's rule).
SELECTOR_HINT = (
    "Enter selects · ↑/↓ move · PgUp/PgDn page · Home/End first/last · type to filter · "
    "Esc cancels"
)

#: The status lines the selector paints when a question ends. Plain colons, no dashes: the
#: block is drawn in ASCII on a non-UTF-8 stream, and a fold that turns the module's own prose
#: into ``cancelled ? nothing was written`` is the one fold nobody should have to read.
SELECTED_STATUS = "selected: {value}"
CANCELLED_STATUS = "cancelled: nothing was written"
EMPTY_STATUS = "nothing was chosen: Enter on an empty list"
EXHAUSTED_STATUS = "no answer: the script ended"

#: The narrowest and the shortest terminal a list is still worth drawing on.
MIN_COLUMNS = 40
MIN_LINES = 8

#: The control bytes, by name (the loop switches on the names, never on the bytes).
_CONTROL_KEYS: dict[str, str] = {
    "\x03": "ctrl-c",
    "\x04": "ctrl-d",
    "\x08": "backspace",
    "\x0c": "ctrl-l",
    "\x15": "ctrl-u",
    "\r": "enter",
    "\n": "enter",
    "\x7f": "backspace",
}

#: What the letters after ``ESC [`` stand for, and the numbered ``ESC [ n ~`` keys.
_FINAL_KEYS: dict[str, str] = {
    "A": "up",
    "B": "down",
    "C": "right",
    "D": "left",
    "F": "end",
    "H": "home",
}
_TILDE_KEYS: dict[str, str] = {
    "1": "home",
    "2": "insert",
    "3": "delete",
    "4": "end",
    "5": "pageup",
    "6": "pagedown",
}


def terminal_size(size: tuple[int, int] | None = None) -> tuple[int, int]:
    """The terminal's cells — ``(columns, lines)``, 80×24 when nothing answers."""
    if size is not None:
        try:
            return max(1, int(size[0])), max(1, int(size[1]))
        except Exception:  # noqa: BLE001 — a size that is not two numbers is not a size
            return 80, 24
    try:
        measured = shutil.get_terminal_size((80, 24))
        return int(measured.columns), int(measured.lines)
    except Exception:  # noqa: BLE001 — no measurement is not a reason to fail a setup run
        return 80, 24


def selectable(
    *,
    env: Mapping[str, str] | None = None,
    stream_in: Any = None,
    stream_out: Any = None,
) -> tuple[bool, str]:
    """Can a selection list be **drawn and driven** here? ``(ok, the reason when not)``.

    Every refusal is a terminal that cannot answer an arrow key, and each one has its own
    sentence so an operator can see *why* the questions went plain:

    * ``SKALDIR_PLAIN`` set — the switch that forces the plain path;
    * ``NO_COLOR`` present (any value, including empty) — the screen-control opt-out;
    * ``TERM`` unset or ``dumb`` — no cursor addressing to redraw a block with;
    * stdin **or** the error stream is not a terminal — a pipe, a file, a captured stream;
    * no ``termios`` (not a POSIX terminal);
    * narrower than 40 columns or shorter than 8 lines — a list does not fit.
    """
    env_map = dict(os.environ if env is None else env)
    if str(env_map.get(PLAIN_SWITCH) or "").strip():
        return False, f"{PLAIN_SWITCH} is set"
    if "NO_COLOR" in env_map:
        return False, "NO_COLOR is set"
    term = str(env_map.get("TERM") or "").strip()
    if not term or term == "dumb":
        return False, f"TERM is {term or 'unset'}"
    source = sys.stdin if stream_in is None else stream_in
    sink = sys.stderr if stream_out is None else stream_out
    for label, stream in (("stdin", source), ("stderr", sink)):
        try:
            live = bool(stream is not None and stream.isatty())
        except Exception:  # noqa: BLE001 — a stream that cannot answer is not a terminal
            live = False
        if not live:
            return False, f"{label} is not a terminal"
    if _termios()[0] is None:
        return False, "no termios on this platform"
    columns, lines = terminal_size()
    if columns < MIN_COLUMNS:
        return False, f"{columns} columns is narrower than {MIN_COLUMNS}"
    if lines < MIN_LINES:
        return False, f"{lines} lines is shorter than {MIN_LINES}"
    return True, ""


def value_index(values: Sequence[str], value: str) -> int:
    """Where a pre-filled value sits in a value list (``0`` when it is not in it).

    ``0`` is the honest answer for "the pre-filled value is not one of these rows" — the
    list stays usable and the first row is where the pointer starts, instead of a hidden
    "nothing is selected" state the user has to discover with the arrow keys.
    """
    wanted = str(value or "").strip().lower()
    if wanted:
        for index, item in enumerate(values):
            if wanted == str(item).strip().lower():
                return index
    return 0


def row_index(rows: Sequence[PickRow], value: str) -> int:
    """Where a pre-filled value sits in a row list — :func:`value_index` over ``row.value``."""
    return value_index([str(row.value) for row in rows], value)


def _plain_unicode(stream: Any) -> bool:
    """Is this stream a UTF-8 terminal? (The plain path is ASCII — it says so below.)"""
    encoding = str(getattr(stream, "encoding", "") or "")
    return "utf" in encoding.lower()


#: The **exact** call this module needs from the shared picker widget (``skaldir.picker``).
#: The day that module appears with this shape, every list drawn here — ``skaldir setup``,
#: ``skaldir model`` and the plain-terminal path — is handed over to it and the loop below is
#: deleted rather than kept as a second implementation:
#:
#:     select(title, options, *, default=0, allow_text=False, out=None, env=None,
#:            keys=None, size=None) -> str | None
#:
#: ``options`` is ``Sequence[tuple[str, str]]`` — (the line to show, the value it stands for)
#: in display order; ``default`` is the **index** of the row the pointer starts on (the
#: widget owns the ordering, so it owns the index); the return is the chosen value, ``""``
#: for "Enter on an empty filter" and ``None`` for "left without choosing". ``out`` is the
#: stream the block is drawn on. ``keys``/``size`` are optional and exist so the swap is
#: testable exactly as this one is (a scripted key source and a size, no pty); ``env`` is the
#: mapping the plain-path decisions are read from (``NO_COLOR``/``TERM``).
SHARED_PICKER_CALL = (
    "select(title, options, *, default=0, allow_text=False, out=None, env=None, "
    "keys=None, size=None) -> str | None"
)

#: The keyword names :func:`shared_picker` insists on before it hands a question over.
#: A widget with a different vocabulary is *not* guessed at: this module keeps drawing its own
#: list, and nothing is passed with the wrong meaning.
SHARED_PICKER_PARAMS: tuple[str, ...] = ("title", "options", "default", "allow_text", "out")


def shared_picker() -> Any:
    """The shared picker widget when this build carries one **with the right shape**.

    ``None`` is the honest answer today (``skaldir.picker`` does not exist yet) and it also
    covers every other reason not to hand a question over: no module, no ``select``, a
    ``select`` whose parameters are not the names above, or a signature that cannot be read.

    The seam is deliberately one function deep: when the shared widget lands, this returns it
    and :func:`ask_through_shared` is the only code that has to be right — ``setup_cli`` and
    ``model_cli`` do not know which implementation answered them.
    """
    try:
        # imported by name, not with ``from . import picker``: the module is *optional*, and
        # this file has to import cleanly in a build that does not carry it yet
        shared = importlib.import_module(".picker", __package__)
    except Exception:  # noqa: BLE001 — no shared widget is not an error, it is today's status
        return None
    select = getattr(shared, "select", None)
    if not callable(select):
        return None
    try:
        have = set(inspect.signature(select).parameters)
    except (TypeError, ValueError):  # pragma: no cover — an unreadable signature is no swap
        return None
    return shared if set(SHARED_PICKER_PARAMS) <= have else None


def ask_through_shared(
    shared: Any,
    title: str,
    rows: Sequence[PickRow],
    *,
    default_value: str = "",
    allow_text: bool = False,
    sink: Any = None,
    env: Mapping[str, str] | None = None,
    keys: Iterable[str] | None = None,
    size: tuple[int, int] | None = None,
) -> str | None:
    """One question handed to the shared widget (see :data:`SHARED_PICKER_CALL`).

    The rows become ``(label, value)`` pairs in their own order, so the widget shows exactly
    what this module would have shown; the answer comes back as the row's **value**, which is
    what ``setup_cli``/``model_cli`` switch on.
    """
    options = [(row.name, row.value) for row in list(rows)]
    chosen = shared.select(
        title,
        options,
        default=value_index([value for _label, value in options], default_value),
        allow_text=bool(allow_text),
        out=sink,
        env=dict(os.environ if env is None else env),
        keys=keys,
        size=size,
    )
    return None if chosen is None else str(chosen)


class _Board:
    """A fixed-height block of lines the selector owns and redraws **in place**.

    The space is reserved *before* the first paint, and that one write is the only write
    allowed to scroll the screen: it happens while the block is still empty. Every later
    paint then lands strictly inside rows that already exist, so the arithmetic below cannot
    be off by a line that scrolled away — which is exactly the defect a naive "move up N,
    draw N" picks up at the bottom of a 24-line terminal.
    """

    def __init__(self, sink: Any) -> None:
        self.sink = sink
        self.depth = 0

    def paint(self, lines: Sequence[str]) -> None:
        body = [str(line) for line in lines]
        if not self.depth:
            self.sink.write("\r\n" * len(body))
            self.depth = len(body)
        self.sink.write(f"\x1b[{self.depth}A")
        for index, line in enumerate(body):
            # erase the line, then draw it: a shorter row never leaves the tail of a longer one
            self.sink.write("\r\x1b[2K" + line)
            if index < len(body) - 1:
                self.sink.write("\r\n")
        self.sink.write("\r\n")
        self.sink.flush()


def _status_line(
    query: str, *, width: int, unicode: bool, allow_text: bool, status: str = ""
) -> str:
    """The last line of the block: the keys, what is narrowed, and how it ended."""
    if status:
        text = status
    else:
        hint = SELECTOR_HINT + (" · a typed id is used as it is" if allow_text else "")
        text = f"filter: {query} · {hint}" if query else hint
    return "  " + _elide(text, max(0, width - 2), unicode=unicode)


def _more_line(rest: int, width: int, unicode: bool) -> str:
    """The row past the last visible one: how many are **not** shown, or a blank.

    Blank and not absent: the block is redrawn by moving the cursor back up its own height, so
    a block that grows and shrinks as a filter narrows walks down the screen.
    """
    if rest <= 0:
        return " " * int(width)
    text = f"… {rest} more" if unicode else f"... {rest} more"
    return ("  " + text)[: int(width)].ljust(int(width))


def _block_lines(
    title: str,
    rows: Sequence[PickRow],
    *,
    selected: int,
    query: str,
    width: int,
    shown: int,
    unicode: bool,
    allow_text: bool,
    status: str = "",
) -> list[str]:
    """The block for one paint: a title, up to ``shown`` rows, a ``… N more``, a status line.

    The count is fixed on purpose: the block is redrawn by moving the cursor back up its own
    height, and a block whose height changes between paints walks down the screen.
    """
    body = ["  " + _elide(title, max(0, width - 2), unicode=unicode)]
    window, index, undrawn = window_rows(rows, selected=selected, limit=max(0, int(shown)))
    drawing = row_lines(window, width, selected=index, max_rows=0, hidden=0, unicode=unicode)
    body += drawing
    body += [" " * width] * max(0, int(shown) - len(drawing))
    body.append(_more_line(undrawn, width, unicode))
    body.append(
        _status_line(query, width=width, unicode=unicode, allow_text=allow_text, status=status)
    )
    return body


def _first_match(rows: Sequence[PickRow], query: str) -> int:
    """Where a first-letter jump lands: the first row whose **name** starts with the query.

    The row number is part of the name (``6 DeepSeek``), so the label is what a letter jump
    is about: typing ``d`` lands on ``DeepSeek`` even though the line starts with a digit.
    ``0`` when nothing starts with it — the list is still narrowed, the pointer just stays put.
    """
    wanted = str(query or "").strip().lower()
    if not wanted:
        return 0
    for index, row in enumerate(rows):
        head = str(row.name).strip().lower()
        label = head.split(" ", 1)[1] if head[:1].isdigit() and " " in head else head
        if head.startswith(wanted) or label.startswith(wanted):
            return index
    return 0


def _raw(fd: int, timeout: float) -> bytes:
    """At most one byte from ``fd``, or nothing when the timeout passes first."""
    try:
        ready, _, _ = select.select([fd], [], [], timeout)
    except (OSError, ValueError):  # pragma: no cover — a descriptor that went away
        return b""
    if not ready:
        return b""
    try:
        return os.read(fd, 1)
    except OSError:  # pragma: no cover — a terminal that went away
        return b""


def _decode_key(fd: int, first: bytes, timeout: float) -> str:
    """One character, continuation bytes included (a ``ü`` is not half a character)."""
    body = first
    while body:
        try:
            return body.decode("utf-8")
        except UnicodeDecodeError:
            if len(body) >= 4:
                return body.decode("utf-8", "replace")
            more = _raw(fd, timeout)
            if not more:
                return body.decode("utf-8", "replace")
            body += more
    return ""


def _escape_key(fd: int, timeout: float) -> str:
    """``ESC`` alone (a dismissal) or the sequence behind it (an arrow, ``PageDown``)."""
    follow = _raw(fd, timeout)
    if follow[:1] != b"[":
        return "esc"
    body = b""
    while len(body) < 8:
        chunk = _raw(fd, timeout)
        if not chunk:
            break
        body += chunk
        if 0x40 <= chunk[0] <= 0x7E:
            break
    text = body.decode("ascii", "replace")
    if text.endswith("~"):
        return _TILDE_KEYS.get(text[:-1], "esc")
    return _FINAL_KEYS.get(text[-1:], "esc")


def _read_key(fd: int, *, timeout: float = 0.25, escape_timeout: float = 0.05) -> str | None:
    """One key from ``fd``: a name for the special ones, the character itself otherwise.

    ``None`` means "nothing arrived before the timeout" — the caller then re-measures the
    terminal, which is how a resize is honoured while nobody is typing. The selector has its
    own reader (the framed picker's lives in ``chatbar``, which this module must not require
    to ask a question on a plain terminal), and it answers the **same names** — the names are
    what the two loops switch on, so the two hosts cannot disagree about what ``up`` means.
    """
    first = _raw(fd, timeout)
    if not first:
        return None
    text = _decode_key(fd, first, escape_timeout)
    if text == "\x1b":
        return _escape_key(fd, escape_timeout)
    if text in _CONTROL_KEYS:
        return _CONTROL_KEYS[text]
    return text if text.isprintable() else ""


def select_in_terminal(
    title: str,
    rows: Sequence[PickRow],
    *,
    default_value: str = "",
    allow_text: bool = False,
    env: Mapping[str, str] | None = None,
    stream_in: Any = None,
    stream_out: Any = None,
    keys: Iterable[str] | None = None,
    size: tuple[int, int] | None = None,
    timeout: float = 0.25,
) -> str | None:
    """Ask one question with a selection list on a plain terminal. ``None`` = no answer.

    Keys: ``↑``/``↓`` (or ``k``/``j``) move, a printable character narrows the list (an
    all-digit query matches the row *numbers*, so ``13`` is the thirteenth row; a word
    matches names, endpoints and values), ``Backspace``/``Ctrl-U`` edit the filter,
    ``PageUp``/``PageDown`` jump a page, ``Home``/``End`` jump to the first/last row shown,
    ``Enter`` selects — and with ``allow_text`` an
    unmatched filter stands for itself, which is how a model id nobody documented is
    entered. ``Esc``/``Ctrl-C`` leave without choosing.

    ``keys`` is a scripted key source (a test's): the loop then reads from it instead of the
    terminal, which is what makes the drawing and the key handling testable without a pty.
    The terminal is in cbreak for the question and restored by the ``finally``; the block is
    left standing with the outcome on its last line.
    """
    sink = sys.stderr if stream_out is None else stream_out
    scripted = None if keys is None else iter(list(keys))
    material = list(rows)
    if not material and not allow_text:
        return None
    if scripted is None:
        # the shared widget (``skaldir.picker``) when this build carries one: every list in
        # this file is handed to it, and the loop below is the stand-in it replaces
        shared = shared_picker()
        if shared is not None:
            return ask_through_shared(
                shared,
                title,
                material,
                default_value=default_value,
                allow_text=allow_text,
                sink=sink,
                env=env,
                size=size,
            )

    fd = -1
    saved: Any = None
    termios_mod = tty_mod = None
    if scripted is None:
        fd = _fd_of(stream_in)
        termios_mod, tty_mod = _termios()
        if fd < 0 or termios_mod is None or tty_mod is None:
            return None
        try:
            saved = termios_mod.tcgetattr(fd)
        except Exception:  # noqa: BLE001 — no terminal state to save is no terminal to drive
            return None

    def geometry() -> tuple[int, int, int]:
        """``(width, lines, rows shown)`` — one cell short of the edge, never over it."""
        columns, lines = terminal_size(size)
        room = max(MIN_LINES, lines) - CHROME_ROWS
        return (
            max(MIN_COLUMNS - 1, columns - 1),
            max(MIN_LINES, lines),
            max(3, min(PICKER_MAX_ROWS, room)),
        )

    width, _lines, shown = geometry()
    unicode_mode = _plain_unicode(sink)
    query = ""
    selected = row_index(material, default_value)
    visible: list[PickRow] = list(material)
    answer: str | None = ""
    status = ""
    try:
        if termios_mod is not None:
            _cbreak(termios_mod, fd, saved)
        board = _Board(sink)
        painted: tuple[Any, ...] = ()
        try:
            while True:
                visible = _filtered(material, query)
                selected = max(0, min(selected, len(visible) - 1)) if visible else 0
                # an idle prompt writes nothing: the block is redrawn only when what it says
                # changed (or when the terminal did), not on every read timeout
                state = (query, selected, width, shown, len(visible))
                if state != painted:
                    board.paint(
                        _block_lines(
                            title,
                            visible,
                            selected=selected,
                            query=query,
                            width=width,
                            shown=shown,
                            unicode=unicode_mode,
                            allow_text=allow_text,
                        )
                    )
                    painted = state
                key = (
                    next(scripted, None)
                    if scripted is not None
                    else _read_key(fd, timeout=timeout)
                )
                if key is None:
                    if scripted is not None:
                        answer = None
                        status = EXHAUSTED_STATUS
                        break
                    fresh = geometry()
                    if fresh[:2] != (width, _lines):
                        # the terminal was resized: the new block is drawn below the old one
                        width, _lines, shown = fresh
                        board = _Board(sink)
                    continue
                if not key:
                    continue
                if key == "enter":
                    if visible:
                        answer = visible[selected].value
                        status = SELECTED_STATUS.format(value=answer)
                    elif allow_text and query.strip():
                        answer = query.strip()
                        status = SELECTED_STATUS.format(value=answer)
                    else:
                        answer = ""
                        status = EMPTY_STATUS
                    break
                if key in ("esc", "ctrl-c", "ctrl-d"):
                    answer = None
                    status = CANCELLED_STATUS
                    break
                if key in ("up", "k"):
                    selected = (selected - 1) % len(visible) if visible else 0
                elif key in ("down", "j"):
                    selected = (selected + 1) % len(visible) if visible else 0
                elif key in ("pageup", "pagedown"):
                    step = max(1, shown)
                    if visible:
                        landing = selected + (step if key == "pagedown" else -step)
                        selected = max(0, min(landing, len(visible) - 1))
                elif key == "home":
                    selected = 0
                elif key == "end":
                    selected = max(0, len(visible) - 1)
                elif key == "backspace":
                    query = query[:-1]
                elif key == "ctrl-u":
                    query = ""
                elif len(key) == 1 and key.isprintable():
                    # type to narrow, and a letter jump lands on the first name that starts
                    # with it — the pointer follows the filter instead of staying at the top
                    query += key
                    selected = _first_match(_filtered(material, query), query)
                # left/right/insert/delete/Ctrl-L: one column of rows, nothing to do
        except KeyboardInterrupt:  # noqa: PERF203 — a ^C that arrived anyway is a cancel
            answer = None
            status = CANCELLED_STATUS
        board.paint(
            _block_lines(
                title,
                visible,
                selected=selected,
                query=query,
                width=width,
                shown=shown,
                unicode=unicode_mode,
                allow_text=allow_text,
                status=status,
            )
        )
    finally:
        if saved is not None and termios_mod is not None:
            try:
                termios_mod.tcsetattr(fd, termios_mod.TCSADRAIN, saved)
            except Exception:  # noqa: BLE001 — a terminal that cannot be restored is gone
                pass
    return answer


def _fd_of(stream: Any) -> int:
    """The descriptor behind a stream, or ``-1`` when it has none (a captured stream)."""
    target = sys.stdin if stream is None else stream
    try:
        return int(target.fileno())
    except Exception:  # noqa: BLE001 — a stream without a descriptor cannot drive a list
        return -1


def _cbreak(termios_mod: Any, fd: int, saved: Any) -> None:
    """Character-at-a-time input, with one deliberate difference from ``tty.setcbreak``.

    ``tty.setcbreak`` leaves ``ISIG`` **on**, so ``^C`` becomes a ``SIGINT`` and kills the
    question instead of cancelling the picker; ``skaldir setup`` would die with a traceback
    where the user asked to go back. Here ``ISIG`` is cleared too, so ``^C`` arrives as the
    byte ``\\x03`` — the loop answers it exactly like ``Esc`` — and the terminal is restored
    from ``saved`` by the caller's ``finally``, whatever happens.
    """
    mode = list(saved)
    mode[3] = int(mode[3]) & ~(termios_mod.ECHO | termios_mod.ICANON | termios_mod.ISIG)
    flags = list(mode[6])
    flags[termios_mod.VMIN] = 1
    flags[termios_mod.VTIME] = 0
    mode[6] = flags
    termios_mod.tcsetattr(fd, termios_mod.TCSAFLUSH, mode)


def _line_writer(sink: Any) -> Callable[[str], None]:
    """A ``write one line`` for a stream that never raises into the caller's face."""

    def write(text: str) -> None:
        try:
            sink.write(str(text) + "\n")
            sink.flush()
        except Exception:  # noqa: BLE001 — a closed stream ends the line, not the session
            pass

    return write


def _plain_row(row: PickRow, width: int) -> str:
    """One row of the **plain** list: the label, then the endpoint, then as much note as fits.

    Deliberately not ``row_lines``: that one aligns columns for a *frame*, where the name is
    one column among three, and at 79 columns a 54-character name ("16 Ollama (local server,
    OpenAI compatibility)") starves the endpoint — which is the one thing a provider row is
    for. Here the label and the endpoint come first and the note is what gets clipped, and the
    mark comes last so it survives only when there is room for it.

    The clip marker is the ASCII ``...``, and the row's own text is never folded: a pipe or a
    log gets no escape bytes and no ``…``, but a provider whose name is not ASCII (or a note
    with an em dash in it) keeps the name the catalog gave it.
    """
    head = f"  {row.name}"
    detail = f"  {row.description}" if str(row.description or "").strip() else ""
    mark = f"  [{row.mark}]" if str(row.mark or "").strip() else ""
    room = max(0, int(width))
    text = head + detail
    if len(text) + len(mark) <= room:
        return text + mark
    if len(text) <= room:
        return text
    clipped = text[: max(0, room - 3)]
    return clipped + "..."


def _plain_reader(sink: Any) -> Callable[[str], str]:
    """Read one line from stdin, with the prompt written to the stream the rows went to."""

    def read(prompt: str) -> str:
        try:
            sink.write(prompt)
            sink.flush()
            return str(sys.stdin.readline()).rstrip("\n")
        except Exception:  # noqa: BLE001 — a stdin that cannot be read is not an answer
            return ""

    return read


def _plain(
    title: str,
    rows: Sequence[PickRow],
    *,
    default_value: str = "",
    allow_text: bool = False,
    stream_out: Any = None,
    line_reader: Callable[[str], str] | None = None,
    size: tuple[int, int] | None = None,
) -> str:
    """The fallback: the same rows printed as **lines**, the answer read as one line.

    This is the path a pipe, a script, ``TERM=dumb``, ``NO_COLOR`` or a captured stream gets
    — and the path the tests get. It is deliberately ASCII only (no pointer, no ``…``, no
    screen control), so a log or a ``--json`` consumer cannot be corrupted by a fallback that
    still tried to be pretty. The answer is a row number, a row's value, or — with
    ``allow_text`` — the words themselves; ``""`` means "nothing was chosen".
    """
    sink = sys.stderr if stream_out is None else stream_out
    columns, _lines = terminal_size(size)
    width = max(MIN_COLUMNS, min(100, columns - 1))
    material = list(rows)
    write = _line_writer(sink)
    if material:
        write("")
        write(f"  {title}")
        # the same rows, in the same order, line by line — with the label and the endpoint
        # ahead of the note, because a plain list has one line per row and nothing to align
        for row in material:
            write(_plain_row(row, width))
        write("")
    else:
        write(f"  {title}")
    read = line_reader or _plain_reader(sink)
    hint = f" [{default_value}]" if str(default_value or "").strip() else ""
    prompt = f"  {title}{hint}: "
    for _ in range(3):
        answer = str(read(prompt) or "").strip()
        if not answer:
            return str(default_value or "")
        picked = resolve_option(answer, material) if material else answer
        if picked:
            return str(picked)
        write("  not one of the rows above — give the number or the id")
    return ""


def choose(
    title: str,
    rows: Sequence[PickRow],
    *,
    default_value: str = "",
    allow_text: bool = False,
    interactive: bool = True,
    env: Mapping[str, str] | None = None,
    stream_in: Any = None,
    stream_out: Any = None,
    line_reader: Callable[[str], str] | None = None,
    keys: Iterable[str] | None = None,
    size: tuple[int, int] | None = None,
) -> str:
    """One question, one code path: the selector when the terminal can take it, lines when not.

    Returns the chosen **value** — a row's value, or, with ``allow_text``, the words that were
    typed. ``""`` means "no answer" (headless, cancelled, or the input ran out) and the caller
    decides what that means, exactly as it did before there was a selector: ``--json``,
    ``--no-input`` and ``--yes`` still ask nothing, and every question still has a flag.
    """
    if not interactive:
        return ""
    env_map = dict(os.environ if env is None else env)
    ok, _why = selectable(env=env_map, stream_in=stream_in, stream_out=stream_out)
    if ok:
        answer = select_in_terminal(
            title,
            rows,
            default_value=default_value,
            allow_text=allow_text,
            env=env_map,
            stream_in=stream_in,
            stream_out=stream_out,
            keys=keys,
            size=size,
        )
        return str(answer or "")
    return _plain(
        title,
        rows,
        default_value=default_value,
        allow_text=allow_text,
        stream_out=stream_out,
        line_reader=line_reader,
        size=size,
    )


def _write_above(front: Any, lines: Sequence[str]) -> None:
    """Put a block of text in the transcript above the frame, in **one** write.

    Two things this gets right, both measured: the probe facts are forgotten *first*, so
    the redraw that follows already reads the new configuration; and the whole block goes
    out through one ``stream_chunk`` + ``close_line`` — the frame erases once, writes
    once and repaints once. Five ``write_above`` calls in a row lose the earlier lines
    (each redraw measures the erase from the position the previous line left behind), which
    is exactly the "a paint that writes row by row" defect the PTY recipe warns about.
    """
    bar = getattr(front, "bar", None)
    if bar is None:
        return
    _invalidate(bar)
    block = "\n".join(str(line) for line in lines if str(line).strip())
    if not block:
        return
    stream = getattr(bar, "stream_chunk", None)
    close = getattr(bar, "close_line", None)
    if callable(stream):
        stream(block)
        if callable(close):
            close()
        return
    for line in block.split("\n"):  # a bar without the streaming path still gets every line
        bar.write_above(str(line))


def _say(shell: Any, text: str) -> None:
    """One line to the shell — through its own writer when it has one."""
    sink = getattr(shell, "say", None)
    if callable(sink):
        try:
            sink(str(text))
            return
        except Exception:  # noqa: BLE001 — fall through to the stream
            pass
    stream = getattr(shell, "out", None) or sys.stdout
    try:
        stream.write(str(text) + "\n")
    except Exception:  # noqa: BLE001 — a closed stream ends the line, not the session
        pass


#: The existing-key decision: keep / skip / replace. Enter on the bar = keep.
KEY_ACTIONS: tuple[tuple[str, str], ...] = (
    ("keep the one already there", "keep"),
    ("skip (no key change)", "skip"),
    ("replace with a new key", "replace"),
)


def ask_existing_key(
    where: Mapping[str, Any],
    *,
    name: str = "",
    say: Callable[[str], Any] | None = None,
    read: Callable[[str], str] | None = None,
    select: Callable[..., Any] | None = None,
) -> str:
    """``keep`` / ``skip`` / ``replace`` — asked *before* the hidden key prompt.

    The conflict this fixes (owner, 2026-09-23): ``Enter keeps the one in .env`` and
    ``Enter confirms the typed key`` cannot share one prompt. Layer one decides the
    action; layer two is the hidden key, where Enter means "use what I typed".
    """
    source = str(where.get("source") or "the environment")
    label = name or "this endpoint"
    line = f"  key for {label}: already in {source}"
    if say is not None:
        say(line)
        say("  keep (K) · skip (S) · replace (R)  —  Enter keeps")
    # framed picker first (←/→ + Enter), then letters, then Enter=keep
    if select is not None:
        try:
            value = select(
                f"key for {label} — already in {source}",
                KEY_ACTIONS,
                default=0,
            )
            if value in {item[1] for item in KEY_ACTIONS}:
                return str(value)
        except Exception:  # noqa: BLE001 — fall through to letters
            pass
    if read is not None:
        answer = str(read("  [K]eep / [S]kip / [R]eplace › ") or "").strip().lower()
    else:
        answer = ""
    if answer in ("k", "keep", "1", "replace-no"):
        return "keep"
    if answer in ("s", "skip", "2", "skip (no key change)"):
        return "skip"
    if answer in ("r", "replace", "3", "replace with a new key"):
        return "replace"
    return "keep"


def _ask_key_value(
    name: str, *, where: Mapping[str, Any] | None = None, local: bool = False
) -> str:
    """The key, typed **hidden** — the one thing the picker asks the user for.

    When a key already exists, :func:`ask_existing_key` runs first (K/S/R). The
    hidden prompt then means exactly one thing: **Enter confirms the typed key**.
    An empty line after *replace* is "I changed my mind" and keeps the old one.

    ``getpass`` reads from the controlling terminal and writes its prompt to ``stderr``,
    so the value never reaches stdout (the one JSON document), never a log that captures
    stdout, and never the shell history.
    """
    import getpass

    if "PYTEST_CURRENT_TEST" in os.environ and not getattr(sys.stdin, "isatty", lambda: False)():
        return ""

    action = "type"
    if where and where.get("available"):
        action = ask_existing_key(
            where,
            name=name,
            say=lambda text: print(text, file=sys.stderr),
            read=lambda prompt: (
                str(input(prompt)) if getattr(sys.stdin, "isatty", lambda: False)() else ""
            ),
            select=globals().get("select_in_terminal"),
        )
        if action == "keep":
            return ""  # reuse: empty value + a source in the caller's provenance
        if action == "skip":
            return ""
    hint = " — Enter confirms the key you typed"
    if local and action == "type":
        hint = " — not required for a local endpoint, Enter skips it"
    try:
        prompt = f"  the key for {name or 'this endpoint'} (hidden){hint}: "
        return str(getpass.getpass(prompt, stream=sys.stderr) or "").strip()
    except (EOFError, KeyboardInterrupt):
        return ""
    except Exception:  # noqa: BLE001 — no controlling terminal is not a value
        return ""


def _cancelled(front: Any) -> tuple[None, str]:
    """Say that a question was left without an answer, then hand back "nothing".

    The picker's own rule is that ``Esc`` and ``Ctrl-C`` cancel the **pick**: both arrive as
    the same kind of answer (``None`` from :func:`select_in_frame`), so both get the same one
    line here. Nothing was written, and the frame is already repainted by the picker's own
    ``finally`` — this line is the receipt, in the transcript above it.
    """
    _write_above(front, ["  nothing selected — nothing was written"])
    return None, ""


def _pick_in_frame(
    shell: Any, front: Any, *, role: str, env: Mapping[str, str]
) -> tuple[Plan | None, str]:
    """The whole selection inside the frame: role → provider → model → key.

    Returns ``(plan, key_value)``. ``key_value`` is empty when the key already exists
    somewhere usable (it is then reused, and the report says from where).
    """
    states = role_state(env=env)
    role_rows_list = role_rows(state=states)
    default_index = next(
        (index for index, row in enumerate(role_rows_list) if row.value == role), 0
    )
    chosen_role = role
    if not role:
        answer = select_in_frame(
            front,
            title="which role — agent or voice? (agent: the general model; "
            "voice: then asks LLM or Jev as its backing)",
            rows=role_rows_list,
            default=default_index,
        )
        if answer is None:
            return _cancelled(front)
        # the answer may be a row value (the key) or one of the presentation words
        # (``agent``/``voice``): resolve both through the one role question
        chosen_role = resolve_role(answer) or (answer or role_rows_list[default_index].value)
        if chosen_role == sf.ROLE_TYPESAFE:
            # the second-level question the ``voice`` row promises: LLM or Jev as the
            # voice backing. Both land on the same key underneath (``ROLE_TYPESAFE``) —
            # they differ in which provider row the next question pre-selects, not in
            # what is written.
            backing = select_in_frame(
                front,
                title="voice backing — LLM or Jev? (LLM: an ordinary chat model "
                "answering Jev's typed questions; Jev: the TypeSafe endpoint)",
                rows=voice_backing_rows(),
            )
            if backing is None:
                return _cancelled(front)
    provider_answer = select_in_frame(
        front,
        title=(
            f"provider for the {sf.ROLE_LABEL[chosen_role]} — every row carries its base URL"
            + (" · measured/local endpoints first" if chosen_role == sf.ROLE_TYPESAFE else "")
        ),
        rows=provider_rows(
            current=str((states.get(chosen_role) or {}).get("api_key_env") or ""),
            role=str(chosen_role),
        ),
        allow_text=True,
    )
    if provider_answer is None:
        return _cancelled(front)
    if not str(provider_answer).strip():
        return None, ""
    picked = str(provider_answer).strip()
    try:
        choice = sf.provider_choice(picked)
    except sf.SetupRefused as exc:
        _write_above(front, [f"  {exc}"])
        return None, ""
    listed = live_model_rows(choice.id, role=chosen_role, env=env,
                             discover=bool(sys.stdin and sys.stdin.isatty()))
    if listed:
        answer = select_in_frame(
            front,
            title=f"model for {choice.label} — Enter accepts the pre-filled one",
            rows=listed,
            allow_text=True,
        )
        if answer is None:
            return _cancelled(front)
        model_answer = str(answer).strip()
    else:
        # the catalog documents no model for this endpoint (a local server names its own):
        # the id is asked for in the frame, and nothing is invented on the user's behalf
        _write_above(
            front,
            [
                f"  {choice.label} names no model in the catalog — type the id "
                "(the server's own list is the authority)"
            ],
        )
        answer = select_in_frame(
            front, title=f"model id for {choice.label}", rows=(), allow_text=True
        )
        if answer is None or not str(answer).strip():
            return _cancelled(front) if answer is None else (None, "")
        model_answer = str(answer).strip()
    try:
        plan = plan_for(
            chosen_role, provider=choice.id, model=model_answer, env=env
        )
    except sf.SetupRefused as exc:
        _write_above(front, [f"  {exc}"])
        return None, ""
    bar = front.bar
    bar.clear()
    try:
        where = sf.key_available(
            chosen_role,
            plan.credential_name(),
            env=env,
            env_file=sf.read_env_file(sf.env_file_path(env=env)),
        )
        typed = _ask_key_value(plan.credential_name(), where=where, local=plan.local)
        # keep/skip both return "" — the caller's provenance already names the source
    finally:
        _invalidate(bar)
        _repaint(bar)
    return plan, typed


def handle_slash(shell: Any, text: str) -> bool | None:
    """Route ``/model``, the bare ``/provider`` and a keyless ``/setup``.

    ``None`` means *not my line* — the shell keeps its own path (``/provider`` →
    ``provider list``, ``/model`` → ``provider tiers``, ``/setup`` → ``skaldir setup``),
    which is what a pipe, a ``--json`` run or a build without this module must still get.

    With a frame up, the picker runs **in the frame** (a selection list above the bars,
    the status band and the hint row untouched) and applies the choice through exactly the
    code path ``skaldir model`` uses. Without one, only ``/model <provider> [<model>]`` is
    answered — a form the old mapping never had — and the result is printed as lines.
    """
    line = str(text or "").strip()
    if not line.startswith("/"):
        return None
    head, _, rest = line.partition(" ")
    head = head.lower()
    args = [token for token in rest.split() if token]
    if head == SLASH_SETUP_VERB:
        return _route_setup(shell) if not args else None
    if head == SLASH_PROVIDER_VERB:
        if args:
            return None  # `/provider test deepseek` is the real command, untouched
        front = _frame_of(shell)
        if front is None:
            return None
        return _browse_providers(shell, front)
    if head not in SLASH_MODEL_VERBS:
        return None
    env = dict(os.environ)
    front = _frame_of(shell)
    if args:
        return _apply_from_chat(shell, front, args, env=env)
    if front is None:
        return None
    plan, key_value = _pick_in_frame(shell, front, role="", env=env)
    if plan is None:
        return False
    _apply_from_chat(shell, front, [], env=env, plan=plan, key_value=key_value)
    return False


def _apply_from_chat(
    shell: Any,
    front: Any,
    args: Sequence[str],
    *,
    env: Mapping[str, str],
    plan: Plan | None = None,
    key_value: str = "",
) -> bool:
    """Apply a choice made in the session, then say what happened — above the frame."""
    chosen = plan
    if chosen is None:
        role = default_role(env=env)
        try:
            chosen = plan_for(
                role,
                provider=str(args[0]) if args else "",
                model=str(args[1]) if len(args) > 1 else "",
                env=env,
            )
        except sf.SetupRefused as exc:
            _print_above(shell, front, [f"  {exc}"])
            return False
    payload, _code = apply_choice(chosen, key_value=key_value, env=env)
    _print_above(shell, front, report_lines(payload))
    return False


def _print_above(shell: Any, front: Any, lines: Sequence[str]) -> None:
    """The report: in the transcript above the frame, or on the shell's own writer."""
    if front is not None:
        _write_above(front, lines)
        return
    for line in lines:
        _say(shell, line)


def _route_setup(shell: Any) -> bool | None:
    """``/setup`` points at the picker when a mandatory key is missing; else not mine."""
    states = role_state(env=dict(os.environ))
    missing = [
        role for role in sf.MANDATORY_ROLES if not bool((states.get(role) or {}).get("ready"))
    ]
    if not missing:
        return None
    names = ", ".join(sf.ROLE_LABEL[role] for role in missing)
    front = _frame_of(shell)
    lines = [
        f"  setup is not complete: {names} has no usable key",
        "  run `/model` here (or `skaldir model`) — it asks for the role, the provider and",
        "  one hidden key, and writes it to the .env with mode 0600",
    ]
    if front is None:
        for line in lines:
            _say(shell, line)
        return False
    _write_above(front, lines)
    return False


def _browse_providers(shell: Any, front: Any) -> bool:
    """``/provider`` inside the frame: the same list, read-only (nothing is written)."""
    answer = select_in_frame(
        front,
        title="configured and pickable providers — /model changes one",
        rows=provider_rows(),
        allow_text=False,
    )
    if answer is None or not str(answer).strip():
        return False
    try:
        choice = sf.provider_choice(str(answer))
    except sf.SetupRefused as exc:
        _write_above(front, [f"  {exc}"])
        return False
    lines = [
        f"  {choice.label} — {choice.base_url}",
        f"      key variable: {choice.api_key_env or 'none (a local endpoint)'}"
        + (f" · {choice.note}" if choice.note else ""),
    ]
    listed = model_choices(choice.id)
    if listed:
        lines.append("      models: " + ", ".join(item.id for item in listed))
    else:
        lines.append("      models: none documented in the catalog — `/model` asks for an id")
    lines.append("      run `/model` to choose this provider for a role")
    _write_above(front, lines)
    return False


def is_model_verb(text: str) -> bool:
    """Is this line one the in-chat picker answers for? (asked of the line, not intent)"""
    head = str(text or "").strip().split(" ", 1)[0].lower()
    return head in (*SLASH_MODEL_VERBS, SLASH_PROVIDER_VERB, SLASH_SETUP_VERB)


#: The ``slash.SlashCommand`` row the palette needs so ``/model`` names this picker
#: instead of ``provider tiers`` — **handed over**, never pasted into ``slash.py`` from
#: here: that table belongs to the palette block, and the hook above already answers
#: ``/model`` inside a frame. ``argv=("model",)`` is the real command, so the mapping
#: stays honest without a second implementation behind the verb.
SLASH_SNIPPET = '''\
    SlashCommand(
        "model",
        "pick the provider and the model behind the Jev or the LLM role",
        argv=("model",),
        group="model",
        usage="/model [<provider> [<model>]]",
        aliases=("models",),
        layer="kernel",
        values="provider",
        detail="opens the picker inside the frame (the same engine as `skaldir model`); "
               "`/model <provider> [<model>]` applies it without the picker",
    ),
'''
