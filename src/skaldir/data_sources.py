"""Data-source honesty for container tools — DOCKER-05.

The requirement is short and pointed:

    "Stock Research, Editing-Suite, Medien-Download und weitere Tools sind mögliche
     Aufgaben. Konkrete Zugriffsrechte, Datenverfügbarkeit und jeweilige
     Nutzungsbedingungen berücksichtigen; keine universelle Onlineverfügbarkeit
     behaupten. Keine bezahlten Datenquellen stillschweigend hinzufügen."

Three things are forbidden, and this module is shaped so each of them is hard:

* **"keine universelle Onlineverfügbarkeit behaupten"** — availability is a *measurement*,
  never a default. A source that has not been measured reports ``unknown``, not ``yes``.
  There is no "available: true" anywhere in this file that is not the result of a probe.
* **"keine bezahlten Datenquellen stillschweigend hinzufügen"** — a paid source cannot be
  attached to a tool silently. Adding one raises :class:`PaidSourceNotAllowed` unless the
  caller names it explicitly *and* the run carries a spending approval (APR-02).
* **Nutzungsbedingungen berücksichtigen** — every source carries its terms and licence, and
  a source without them is incomplete rather than assumed fine.

What a source costs matters twice here: for the user's money, and for the user's account.
``kind`` separates them: ``free`` · ``free_account`` · ``paid`` · ``metered``.
"""

from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping

__all__ = [
    "Availability",
    "PaidSourceNotAllowed",
    "SourceKind",
    "DataSource",
    "SourceRegistry",
    "ToolSources",
]


class Availability(str, enum.Enum):
    """Whether a source actually answers. ``UNKNOWN`` is the honest default."""

    UNKNOWN = "unknown"          # not measured — the only safe default
    AVAILABLE = "available"      # measured, answered
    UNAVAILABLE = "unavailable"  # measured, did not answer
    RESTRICTED = "restricted"    # answered only under conditions (region, login, quota)


class SourceKind(str, enum.Enum):
    FREE = "free"                 # no money, no account
    FREE_ACCOUNT = "free_account" # no money, but a login and its terms
    PAID = "paid"                 # costs money — APR-02 territory
    METERED = "metered"           # free up to a quota, then money — APR-02 territory


PAID_KINDS = frozenset({SourceKind.PAID, SourceKind.METERED})


class PaidSourceNotAllowed(RuntimeError):
    """A money-costing source was attached silently — exactly what DOCKER-05 forbids."""


@dataclass
class DataSource:
    """One external source a tool may use, with what it actually costs and requires."""

    name: str
    kind: SourceKind = SourceKind.FREE
    #: where the data comes from, in a form a human can check
    provider: str = ""
    terms_url: str = ""
    licence: str = ""
    #: what the caller needs to supply: an API key, an account, a region, nothing
    requires: tuple[str, ...] = ()
    #: measured facts — never assumed
    availability: Availability = Availability.UNKNOWN
    availability_detail: str = ""
    measured_at: float = 0.0
    #: the user must be told this before the tool is used at all
    notes: str = ""

    @property
    def costs_money(self) -> bool:
        return self.kind in PAID_KINDS

    @property
    def is_complete(self) -> bool:
        """A source without terms or licence is incomplete, not "probably fine"."""
        return bool(self.terms_url or self.licence)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind.value,
            "provider": self.provider,
            "terms_url": self.terms_url,
            "licence": self.licence,
            "requires": list(self.requires),
            "availability": self.availability.value,
            "availability_detail": self.availability_detail,
            "measured_at": self.measured_at,
            "costs_money": self.costs_money,
            "terms_documented": self.is_complete,
            "notes": self.notes,
        }


@dataclass
class ToolSources:
    """The sources one container tool depends on."""

    tool: str
    sources: list[DataSource] = field(default_factory=list)
    declared_at: float = field(default_factory=time.time)

    @property
    def costs_money(self) -> bool:
        return any(s.costs_money for s in self.sources)

    @property
    def unknown_availability(self) -> list[str]:
        return [s.name for s in self.sources if s.availability is Availability.UNKNOWN]

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "sources": [s.to_dict() for s in self.sources],
            "costs_money": self.costs_money,
            "unknown_availability": self.unknown_availability,
        }


class SourceRegistry:
    """Which sources a tool uses, and whether they were actually checked.

    The registry is what stops the silent additions: :meth:`attach` is the only way a
    source joins a tool, and it refuses money-costing sources unless the caller says so on
    purpose.
    """

    def __init__(self) -> None:
        self._by_tool: dict[str, ToolSources] = {}
        self._sources: dict[str, DataSource] = {}

    # -- sources -----------------------------------------------------------

    def register_source(self, source: DataSource) -> DataSource:
        self._sources[source.name] = source
        return source

    def source(self, name: str) -> DataSource | None:
        return self._sources.get(name)

    def attach(self, tool: str, source: DataSource | str, *,
               allow_paid: bool = False, approved_by: str = "") -> ToolSources:
        """Attach a source to a tool.

        ``allow_paid`` must be given deliberately *and* name who approved it, because
        DOCKER-05 forbids adding a paid source silently. Passing ``allow_paid=True``
        without ``approved_by`` is refused too — "somebody said yes" has to be traceable.
        """
        resolved = source if isinstance(source, DataSource) else self._sources.get(source)
        if resolved is None:
            raise KeyError(f"unknown data source {source!r} — register it first")

        if resolved.costs_money and not allow_paid:
            raise PaidSourceNotAllowed(
                f"{resolved.name!r} costs money ({resolved.kind.value}) and cannot be "
                "attached to a tool silently — DOCKER-05 requires it to be named "
                "explicitly and approved (APR-02: new spending needs explicit approval "
                "in every mode)"
            )
        if resolved.costs_money and allow_paid and not approved_by:
            raise PaidSourceNotAllowed(
                f"{resolved.name!r} costs money; `allow_paid` without `approved_by` is "
                "not an approval — say who approved the spend"
            )

        entry = self._by_tool.setdefault(tool, ToolSources(tool=tool))
        if all(s.name != resolved.name for s in entry.sources):
            entry.sources.append(resolved)
        return entry

    def detach(self, tool: str, source_name: str) -> bool:
        entry = self._by_tool.get(tool)
        if not entry:
            return False
        before = len(entry.sources)
        entry.sources = [s for s in entry.sources if s.name != source_name]
        return len(entry.sources) != before

    def for_tool(self, tool: str) -> ToolSources | None:
        return self._by_tool.get(tool)

    def all_tools(self) -> list[ToolSources]:
        return list(self._by_tool.values())

    # -- measurement -------------------------------------------------------

    def measure(self, name: str, probe: Callable[[], tuple[bool, str]]) -> DataSource:
        """Measure one source's availability. This is the only way it stops being unknown."""
        source = self._sources.get(name)
        if source is None:
            raise KeyError(f"unknown data source {name!r}")
        try:
            answered, detail = probe()
        except Exception as exc:  # noqa: BLE001 — a failed probe is not a pass
            source.availability = Availability.UNKNOWN
            source.availability_detail = f"probe failed: {type(exc).__name__}: {exc}"[:300]
            source.measured_at = time.time()
            return source
        source.availability = Availability.AVAILABLE if answered else Availability.UNAVAILABLE
        source.availability_detail = detail[:300]
        source.measured_at = time.time()
        return source

    # -- the honesty report -------------------------------------------------

    def report(self, tool: str) -> dict[str, Any]:
        """What can honestly be said about a tool's sources — including what is not known."""
        entry = self._by_tool.get(tool)
        if entry is None:
            return {
                "tool": tool,
                "declared": False,
                "message": ("no data sources are declared for this tool — that is not the "
                            "same as 'it needs none'"),
            }
        unknown = entry.unknown_availability
        return {
            "tool": tool,
            "declared": True,
            "costs_money": entry.costs_money,
            "sources": [s.to_dict() for s in entry.sources],
            "unknown_availability": unknown,
            "incomplete_terms": [s.name for s in entry.sources if not s.is_complete],
            "message": (
                "availability is unknown for: " + ", ".join(unknown)
                if unknown else
                "every declared source has been measured"
            ),
            "disclaimer": (
                "Online availability is not universal and is not claimed here. Access "
                "rights, data availability and each provider's terms apply."
            ),
        }

    def assert_no_silent_paid(self, tool: str) -> None:
        """Fail if a money-costing source sits on a tool without recorded approval."""
        entry = self._by_tool.get(tool)
        if entry is None:
            return
        paid = [s.name for s in entry.sources if s.costs_money]
        if paid:
            raise PaidSourceNotAllowed(
                f"tool {tool!r} uses money-costing sources ({', '.join(paid)}); the run "
                "must carry an explicit spending approval before it is offered"
            )
