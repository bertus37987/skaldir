"""GUIState contract (ABNAHME §52/§53/§54, Teil XV §93/JEV-15).

Verbatim contract of the criterion:

    "GUIState-Vertrag: Computer Use erzeugt strukturierte, versionierte
    GUIStates aus aktueller Wahrnehmung. Ground Truth bleibt echte Appwirkung."

What this module is, in the repo's vocabulary:

* **structured** — one :class:`GUIState` carries the §52 field set (application,
  window title, focus, document, blocking dialog, visible anchors, previous
  action, observed changes) plus the step's goal and the *pre-approved* action
  candidates. Every canonical field is reported: a field that was not perceived
  carries ``value: null`` and its name appears in :attr:`GUIState.unperceived`
  — never a plausible default.
* **versioned** — every state carries ``schema_version`` (this build speaks
  :data:`GUISTATE_SCHEMA_VERSION`; anything else is refused), a content
  ``fingerprint`` over the structured payload, and a revision chain
  (``revision``/``previous``) built with :func:`build_gui_state`.
* **from current perception** — :func:`parse_perception_text` reads exactly one
  perception channel's current text. Nothing is remembered, inferred from an
  older state, or filled in from what "usually" happens.
* **the full contract field list, additively** — the goal's §10.2 list is
  reported in full by :meth:`GUIState.contract_view` (see
  :data:`GUI_STATE_CONTRACT_FIELDS`). The fields the §52 vocabulary did not have
  (``app_id``, ``app_version``, ``window_id``, ``expected_transition``,
  ``user_takeover_state``, ``capture_timestamp``) are real canonical fields, so
  a channel can perceive them and an unperceived one stays explicitly
  unperceived; the field names the runtime already reads (``application``,
  ``blocking_dialog``, ``visible_anchors``, ``observed_changes``) are **not**
  renamed — the view names their source field instead (
  :data:`CONTRACT_FIELD_SOURCES`). Values that are not perception at all —
  ``current_goal_step`` and ``candidate_actions`` from the caller's context,
  ``observation_fingerprint`` from the state itself — are labelled with their
  provenance so nothing looks like a channel reading that it is not.
* **ground truth stays the real app effect** — a GUIState is an *Ableitung*
  (§52). It therefore never certifies an effect: :attr:`GUIState.certifies_effect`
  is ``False`` by construction, and :func:`effect_check` reads the *post-action*
  perception and still returns ``certifies: False``.

Boundaries this module keeps:

* it never widens the pre-approved action set — candidates outside the
  pre-approved set are a contract error, and ``allowed_kinds`` may only shrink
  along a revision chain (§50: allowed kinds stay state-based authority);
* it performs no action and opens no session: the Computer-Use runner path
  (``skaldir.computer_use``) stays the only place that acts;
* no image understanding and no OCR model: a channel that cannot see a field
  leaves it unperceived (§3 — Jev does not replace screenshot perception).

Honest status of this implementation: everything here is provable without a
live desktop (schema, parser, versioning, binding). A GUIState built from a real
host application requires a live GUI session — on a headless host that part
stays ``not_run``; ``live_display`` is ``False`` unless the state was really
built from a live channel (see ``docs/audit/SYSTEM_ONE_GUISTATE.md``).
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from .system_one import SystemOneContractError, state_fingerprint

#: The schema version every GUIState of this build carries.
GUISTATE_SCHEMA_VERSION = "guistate/v1"

#: §52: a GUIState is a derivation — this is the one ground truth it may name.
GROUND_TRUTH_APP_EFFECT = "app_effect"

#: §51 perception inputs. A state is built from exactly one of them.
PERCEPTION_CHANNELS = (
    "screenshot",
    "accessibility_tree",
    "dom",
    "browser",
    "window_metadata",
    "process_metadata",
    "ocr",
    "filesystem",
    "app_state",
)

#: Channels that only exist when a real GUI surface is up. Any other channel
#: must not claim a live display.
LIVE_PERCEPTION_CHANNELS = ("screenshot", "accessibility_tree", "dom", "browser")

#: The §52 field set — every state reports all of them, perceived or not. The
#: §10.2 contract adds six fields the §52 vocabulary did not name; they are
#: appended here so a channel can really perceive them (§52 stays untouched).
GUI_STATE_FIELDS = (
    "application",
    "window_title",
    "focused",
    "document",
    "blocking_dialog",
    "visible_anchors",
    "previous_action",
    "observed_changes",
    # §10.2 additions — all of them sensory, none of them derived.
    "app_id",
    "app_version",
    "window_id",
    "expected_transition",
    "user_takeover_state",
    "capture_timestamp",
)

#: The goal's §10.2 field list, in the goal's order. A GUIState contract check
#: names every one of these — a field this state cannot report is a gap, not a
#: silence (:func:`contract_field_completeness`).
GUI_STATE_CONTRACT_FIELDS = (
    "app_id",
    "app_name",
    "app_version",
    "window_id",
    "window_title",
    "focused",
    "document",
    "dialog_state",
    "visible_semantic_controls",
    "current_goal_step",
    "candidate_actions",
    "previous_action",
    "expected_transition",
    "observed_transition",
    "user_takeover_state",
    "capture_timestamp",
    "observation_fingerprint",
)

#: Where a §10.2 contract value comes from. ``perception`` means a channel read
#: it (and it names its channel); ``context`` means the caller supplied it for
#: this step; ``derivation`` means the state computed it. Nothing is labelled
#: perception unless a channel really reported it.
CONTRACT_FIELD_PROVENANCE: dict[str, str] = {
    "app_id": "perception",
    "app_name": "perception",
    "app_version": "perception",
    "window_id": "perception",
    "window_title": "perception",
    "focused": "perception",
    "document": "perception",
    "dialog_state": "perception",
    "visible_semantic_controls": "perception",
    "current_goal_step": "context",
    "candidate_actions": "context",
    "previous_action": "perception",
    "expected_transition": "perception",
    "observed_transition": "perception",
    "user_takeover_state": "perception",
    "capture_timestamp": "perception",
    "observation_fingerprint": "derivation",
}

#: Which canonical field (or context slot) carries a §10.2 contract field.
CONTRACT_FIELD_SOURCES: dict[str, str] = {
    "app_id": "app_id",
    "app_name": "application",
    "app_version": "app_version",
    "window_id": "window_id",
    "window_title": "window_title",
    "focused": "focused",
    "document": "document",
    "dialog_state": "blocking_dialog",
    "visible_semantic_controls": "visible_anchors",
    "current_goal_step": "goal_step",
    "candidate_actions": "action_candidates",
    "previous_action": "previous_action",
    "expected_transition": "expected_transition",
    "observed_transition": "observed_changes",
    "user_takeover_state": "user_takeover_state",
    "capture_timestamp": "capture_timestamp",
    "observation_fingerprint": "fingerprint",
}

#: Fields that collect several values from one perception text.
MULTI_VALUE_FIELDS = ("visible_anchors", "observed_changes")

#: Upper bound for the verbatim perception kept on a state (audit trail, not a
#: screenshot store).
PERCEPTION_TEXT_LIMIT = 2000


class GuiStateContractError(SystemOneContractError):
    """A GUIState that would invent perception or widen the action set (§52/§53)."""


# --------------------------------------------------------------------------- #
# one perceived field                                                          #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PerceivedField:
    """One structured field + whether it was really perceived (§93/JEV-15).

    Contract rules, enforced here rather than by convention:

    * a *perceived* value must name the channel that perceived it — even a
      perceived absence (``blocking_dialog: none`` → ``value=None`` with a
      channel) is a reading and is allowed;
    * an *unperceived* field must not carry a value at all: a plausible default
      is exactly what this contract forbids.
    """

    name: str
    value: Any = None
    perceived: bool = False
    channel: str = ""

    def __post_init__(self) -> None:
        if self.name not in GUI_STATE_FIELDS:
            raise GuiStateContractError(f"unknown GUIState field: {self.name!r}")
        if self.perceived and not str(self.channel or "").strip():
            raise GuiStateContractError(
                f"{self.name}: a perceived value must name its perception channel"
            )
        if not self.perceived and self.value is not None:
            raise GuiStateContractError(
                f"{self.name}: an unperceived field must not carry a value — "
                "report it as unperceived instead of defaulting to a plausible one"
            )

    def as_dict(self) -> dict:
        value = list(self.value) if isinstance(self.value, (tuple, list)) else self.value
        return {"perceived": bool(self.perceived), "value": value, "channel": self.channel}


# --------------------------------------------------------------------------- #
# current perception → fields                                                  #
# --------------------------------------------------------------------------- #

_LINE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_ -]*)\s*[:=]\s*(.*)$")

_ALIASES = {
    "application": "application",
    "app": "application",
    "window_title": "window_title",
    "window": "window_title",
    "focused": "focused",
    "focus": "focused",
    "document": "document",
    "doc": "document",
    "blocking_dialog": "blocking_dialog",
    "dialog": "blocking_dialog",
    "modal": "blocking_dialog",
    "anchor": "visible_anchors",
    "anchors": "visible_anchors",
    "visible_anchor": "visible_anchors",
    "visible_anchors": "visible_anchors",
    "previous_action": "previous_action",
    "last_action": "previous_action",
    "observed_change": "observed_changes",
    "observed_changes": "observed_changes",
    "observed_transition": "observed_changes",
    "change": "observed_changes",
    # §10.2 spellings map onto the same canonical field — one reading, one field.
    "app_id": "app_id",
    "app_name": "application",
    "app_version": "app_version",
    "window_id": "window_id",
    "dialog_state": "blocking_dialog",
    "visible_semantic_controls": "visible_anchors",
    "semantic_controls": "visible_anchors",
    "expected_transition": "expected_transition",
    "user_takeover": "user_takeover_state",
    "user_takeover_state": "user_takeover_state",
    "capture_timestamp": "capture_timestamp",
    "captured_at": "capture_timestamp",
    "capture_time": "capture_timestamp",
}

_NULL_TOKENS = frozenset({"", "-", "none", "null", "nil", "n/a", "(none)"})
_TRUE_TOKENS = frozenset({"true", "yes", "on", "1", "focused"})
_FALSE_TOKENS = frozenset({"false", "no", "off", "0", "unfocused"})


def parse_perception_text(text: str, *, channel: str) -> dict[str, PerceivedField]:
    """Read ONLY what one perception channel actually reported (§52/§93).

    Recognised ``key: value`` lines fill a field; every field the text does not
    mention stays unperceived. A value that does not read as what the field means
    (a non-boolean ``focused``, an unknown key) is **not** coerced into a
    plausible default — the field stays unperceived, and the raw perception text
    stays on the state for the audit trail.

    Returns a mapping over the complete :data:`GUI_STATE_FIELDS` set.
    """
    if channel not in PERCEPTION_CHANNELS:
        raise GuiStateContractError(
            f"unknown perception channel: {channel!r} (known: {PERCEPTION_CHANNELS})"
        )
    collected: dict[str, list[Any]] = {}
    channels: dict[str, str] = {}
    for line in str(text or "").splitlines():
        match = _LINE.match(line)
        if match is None:
            continue
        name = _ALIASES.get(match.group(1).strip().lower().replace(" ", "_"))
        if name is None:
            continue  # an unknown key is not guessed into a field
        value = match.group(2).strip()
        if name in MULTI_VALUE_FIELDS:
            if value.lower() in _NULL_TOKENS:
                continue
            collected.setdefault(name, []).append(value)
            channels[name] = channel
            continue
        lowered = value.lower()
        if lowered in _NULL_TOKENS:
            collected[name] = [None]  # perceived absence, not a missing reading
        elif name == "focused" and lowered in _TRUE_TOKENS:
            collected[name] = [True]
        elif name == "focused" and lowered in _FALSE_TOKENS:
            collected[name] = [False]
        elif name == "focused":
            continue  # unreadable flag → unperceived, never a plausible default
        else:
            collected[name] = [value]
        channels[name] = channel
    fields: dict[str, PerceivedField] = {}
    for name in GUI_STATE_FIELDS:
        if name not in collected:
            fields[name] = PerceivedField(name=name)
            continue
        raw = collected[name]
        value: Any = tuple(raw) if name in MULTI_VALUE_FIELDS else raw[0]
        fields[name] = PerceivedField(
            name=name, value=value, perceived=True, channel=channels[name]
        )
    return fields


# --------------------------------------------------------------------------- #
# the state                                                                    #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class GUIState:
    """One structured, versioned reading of the current GUI (§52/§93).

    ``certifies_effect`` is always ``False``: the state is a derivation, ground
    truth stays the real app effect. ``action_candidates`` are validated against
    ``pre_approved_candidates`` — a GUIState never widens the allowed action set.
    """

    gui_state_id: str
    fields: dict[str, PerceivedField]
    goal_step: str = ""
    action_candidates: tuple[str, ...] = ()
    pre_approved_candidates: tuple[str, ...] = ()
    allowed_kinds: tuple[str, ...] = ()
    channel: str = ""
    perception: dict[str, str] = field(default_factory=dict)
    live_display: bool = False
    revision: int = 1
    previous: str = ""
    observed_at: float = 0.0
    schema_version: str = GUISTATE_SCHEMA_VERSION
    ground_truth: str = GROUND_TRUTH_APP_EFFECT
    fingerprint: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "fields", dict(self.fields))
        object.__setattr__(
            self, "action_candidates", tuple(str(item) for item in self.action_candidates)
        )
        object.__setattr__(
            self,
            "pre_approved_candidates",
            tuple(str(item) for item in self.pre_approved_candidates),
        )
        object.__setattr__(
            self, "allowed_kinds", tuple(str(item) for item in self.allowed_kinds)
        )
        object.__setattr__(self, "perception", dict(self.perception))
        if self.schema_version != GUISTATE_SCHEMA_VERSION:
            raise GuiStateContractError(
                f"unsupported GUIState schema version: {self.schema_version!r} "
                f"(this build speaks {GUISTATE_SCHEMA_VERSION})"
            )
        if self.ground_truth != GROUND_TRUTH_APP_EFFECT:
            raise GuiStateContractError(
                "ground truth of a GUIState is the real app effect "
                f"({GROUND_TRUTH_APP_EFFECT!r}), got {self.ground_truth!r}"
            )
        missing = [name for name in GUI_STATE_FIELDS if name not in self.fields]
        if missing:
            raise GuiStateContractError(
                "every §52 field must be reported — a field that was not perceived is "
                f"listed in `unperceived`, never omitted: {missing}"
            )
        for name, perceived_field in self.fields.items():
            if name not in GUI_STATE_FIELDS:
                raise GuiStateContractError(f"unknown GUIState field: {name!r}")
            if perceived_field.name != name:
                raise GuiStateContractError(
                    f"field {name!r} carries another field's name: {perceived_field.name!r}"
                )
        if int(self.revision) < 1:
            raise GuiStateContractError("a GUIState needs a revision >= 1")
        widening = [
            item for item in self.action_candidates if item not in self.pre_approved_candidates
        ]
        if widening:
            raise GuiStateContractError(
                f"action candidate(s) outside the pre-approved set: {widening} — "
                "a GUIState never widens the allowed action set (§53)"
            )
        if self.live_display and self.channel not in LIVE_PERCEPTION_CHANNELS:
            raise GuiStateContractError(
                f"channel {self.channel!r} is not a live GUI channel — a state must not "
                "claim a live display it did not perceive"
            )
        if not self.fingerprint:
            object.__setattr__(self, "fingerprint", self.recomputed_fingerprint())
        elif self.fingerprint != self.recomputed_fingerprint():
            raise GuiStateContractError("fingerprint does not match the state content")
        if not self.gui_state_id:
            # content-addressed: identical perception under the same revision
            # yields the same id, so a replayed state is recognisable
            object.__setattr__(self, "gui_state_id", f"gui-{self.fingerprint[:16]}")

    # -- accessors ----------------------------------------------------------

    @property
    def unperceived(self) -> tuple[str, ...]:
        """What this state could NOT perceive — explicit, never defaulted."""
        names = [name for name in GUI_STATE_FIELDS if not self.fields[name].perceived]
        if not str(self.goal_step or "").strip():
            names.append("goal_step")
        return tuple(names)

    @property
    def certifies_effect(self) -> bool:
        """Always ``False`` — a derivation never certifies the real app effect."""
        return False

    def perceived(self, name: str) -> Any:
        """The value of one field, or ``None`` — queries never invent one."""
        field_value = self.fields.get(name)
        if field_value is None or not field_value.perceived:
            return None
        return field_value.value

    def fingerprint_payload(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "revision": int(self.revision),
            "previous": self.previous,
            "channel": self.channel,
            "fields": {name: self.fields[name].as_dict() for name in GUI_STATE_FIELDS},
            "goal_step": str(self.goal_step or ""),
            "action_candidates": list(self.action_candidates),
            "pre_approved_candidates": list(self.pre_approved_candidates),
            "allowed_kinds": list(self.allowed_kinds),
            "live_display": bool(self.live_display),
            "ground_truth": self.ground_truth,
        }

    # -- the §10.2 contract view -------------------------------------------

    def contract_view(self) -> dict:
        """All 17 §10.2 fields with their value, channel and **provenance**.

        The mapping itself is honest rather than decorative: a field whose
        provenance is ``perception`` is a real channel reading (and names the
        channel), ``context`` means the caller supplied it for this step
        (``current_goal_step``, ``candidate_actions``), and ``derivation``
        means the state computed it (``observation_fingerprint``). A field that
        was not perceived is ``perceived: False`` with ``value: None`` — never a
        plausible default, exactly as on the state itself.
        """
        fields: dict[str, dict] = {}
        unperceived: list[str] = []
        for name in GUI_STATE_CONTRACT_FIELDS:
            provenance = CONTRACT_FIELD_PROVENANCE[name]
            source = CONTRACT_FIELD_SOURCES[name]
            if provenance == "perception":
                perceived_field = self.fields[source]
                value = perceived_field.value
                fields[name] = {
                    "perceived": bool(perceived_field.perceived),
                    "value": list(value) if isinstance(value, (tuple, list)) else value,
                    "channel": perceived_field.channel,
                    "provenance": provenance,
                    "source_field": source,
                }
                if not perceived_field.perceived:
                    unperceived.append(name)
                continue
            if provenance == "context":
                if source == "goal_step":
                    raw: Any = str(self.goal_step or "").strip()
                    value = raw or None
                else:
                    listed = [str(item) for item in tuple(self.action_candidates)]
                    value = listed or None
                fields[name] = {
                    "perceived": bool(value),
                    "value": value,
                    "channel": "",
                    "provenance": provenance,
                    "source_field": source,
                }
                if not value:
                    unperceived.append(name)
                continue
            fields[name] = {
                "perceived": True,
                "value": self.fingerprint,
                "channel": "",
                "provenance": provenance,
                "source_field": source,
            }
        return {
            "gui_state_id": self.gui_state_id,
            "schema_version": self.schema_version,
            "revision": int(self.revision),
            "previous": self.previous,
            "channel": self.channel,
            "fingerprint": self.fingerprint,
            "observation_fingerprint": self.fingerprint,
            "observed_at": float(self.observed_at),
            "live_display": bool(self.live_display),
            "ground_truth": self.ground_truth,
            "certifies_effect": self.certifies_effect,
            "advisory_only": True,
            "contract_fields": list(GUI_STATE_CONTRACT_FIELDS),
            "fields": fields,
            "unperceived": unperceived,
        }

    def recomputed_fingerprint(self) -> str:
        """The content fingerprint of this state (``observed_at`` excluded)."""
        return state_fingerprint(self.fingerprint_payload())

    def as_dict(self) -> dict:
        return {
            **self.fingerprint_payload(),
            "gui_state_id": self.gui_state_id,
            "fingerprint": self.fingerprint,
            "observed_at": float(self.observed_at),
            "perception": dict(self.perception),
            "unperceived": list(self.unperceived),
            "certifies_effect": self.certifies_effect,
            "advisory_only": True,
        }


def build_gui_state(
    *,
    perception_text: str,
    channel: str,
    goal_step: str = "",
    previous: GUIState | None = None,
    action_candidates: Iterable[str] = (),
    pre_approved_candidates: Iterable[str] = (),
    allowed_kinds: Iterable[str] = (),
    live_display: bool = False,
    observed_at: float | None = None,
) -> GUIState:
    """Build one GUIState from the CURRENT perception of one channel (§52/§93).

    ``previous`` chains the version: the new state gets ``revision + 1`` and the
    previous state's id. Along that chain ``allowed_kinds`` may only shrink —
    a wider set (or a candidate outside the pre-approved set) is refused, so
    Computer Use's pre-approved action set is never widened by a state.
    """
    if channel not in PERCEPTION_CHANNELS:
        raise GuiStateContractError(
            f"unknown perception channel: {channel!r} (known: {PERCEPTION_CHANNELS})"
        )
    if live_display and channel not in LIVE_PERCEPTION_CHANNELS:
        raise GuiStateContractError(
            f"channel {channel!r} is not a live GUI channel — a state must not claim "
            "a live display it did not perceive"
        )
    kinds = tuple(str(item) for item in allowed_kinds)
    revision = 1
    previous_id = ""
    if previous is not None:
        revision = int(previous.revision) + 1
        previous_id = str(previous.gui_state_id)
        widened = [item for item in kinds if item not in tuple(previous.allowed_kinds)]
        if widened:
            raise GuiStateContractError(
                f"allowed_kinds widened beyond the previous state: {widened} — the "
                "pre-approved action set is never widened (§50/§53)"
            )
    return GUIState(
        gui_state_id="",
        fields=parse_perception_text(perception_text, channel=channel),
        goal_step=str(goal_step or ""),
        action_candidates=tuple(str(item) for item in action_candidates),
        pre_approved_candidates=tuple(str(item) for item in pre_approved_candidates),
        allowed_kinds=kinds,
        channel=channel,
        perception={channel: str(perception_text or "")[:PERCEPTION_TEXT_LIMIT]},
        live_display=bool(live_display),
        revision=revision,
        previous=previous_id,
        observed_at=time.time() if observed_at is None else float(observed_at),
    )


def contract_field_completeness(mapping: Mapping[str, str] | None = None) -> tuple[str, ...]:
    """Which §10.2 contract fields have no provenance entry (empty = complete)."""
    table = CONTRACT_FIELD_PROVENANCE if mapping is None else mapping
    return tuple(name for name in GUI_STATE_CONTRACT_FIELDS if name not in table)


def effect_check(
    gui_state: GUIState,
    *,
    expect_change: str,
    post_text: str,
    channel: str = "",
) -> dict:
    """§54.2 postcondition: did the *real* app effect appear? Advisory reading.

    Reads the post-action perception — never the GUIState's own proposal — and
    reports ``certifies: False`` even when the expected change is there: the
    ground truth is the real app effect, and an observation is evidence for the
    Computer-Use verification loop, not a certificate for it.
    """
    wanted = str(expect_change or "").strip()
    observed = bool(wanted) and wanted.lower() in str(post_text or "").lower()
    return {
        "gui_state_id": gui_state.gui_state_id,
        "schema_version": gui_state.schema_version,
        "revision": int(gui_state.revision),
        "expect_change": wanted,
        "effect_observed": observed,
        "ground_truth": GROUND_TRUTH_APP_EFFECT,
        "channel": str(channel or gui_state.channel or ""),
        "certifies": False,
        "advisory_only": True,
        "reason": (
            "expected change appears in the post-action perception"
            if observed
            else "expected change is not in the post-action perception — reobserve "
            "or escalate (§54.2), never assume the effect"
        ),
    }


__all__ = [
    "CONTRACT_FIELD_PROVENANCE",
    "CONTRACT_FIELD_SOURCES",
    "GROUND_TRUTH_APP_EFFECT",
    "GUI_STATE_CONTRACT_FIELDS",
    "GUI_STATE_FIELDS",
    "GUISTATE_SCHEMA_VERSION",
    "LIVE_PERCEPTION_CHANNELS",
    "MULTI_VALUE_FIELDS",
    "PERCEPTION_CHANNELS",
    "GUIState",
    "GuiStateContractError",
    "PerceivedField",
    "build_gui_state",
    "contract_field_completeness",
    "effect_check",
    "parse_perception_text",
]
