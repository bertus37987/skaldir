"""Project context — the repository's own instruction files, discovered and typed.

A session that does not read the project's instructions makes the same mistakes the project
already wrote down. This module finds those files, decides **which** of several competing
files wins, turns them into a typed block, and fingers exactly what was injected — so a run
can prove which context it used instead of asserting that it read "the docs".

Discovery ladder
----------------

Walked **up** from the working directory, every level is checked for these names, and the
ladder below is the precedence order inside one level (highest first)::

    0  SKALDIR.md           this product's own instruction file
    1  SOUL.md              the owner's voice / non-negotiables for the agent
    2  AGENTS.md            the file other agent harnesses read
    3  CLAUDE.md            read for compatibility only — never written, never trusted over 0-2
    4  .skaldir/context.md  the hidden variant, for context that should not sit in the repo root

Precedence — the full order, highest first
------------------------------------------

1. **Deeper beats shallower.** The file closest to the working directory wins over one
   further up: a nested ``AGENTS.md`` is this directory's law, and the repository root's file
   is its default. (This is the rule that survives every configuration.)
2. **Within one directory the ladder order decides**: ``SKALDIR.md`` > ``SOUL.md`` >
   ``AGENTS.md`` > ``CLAUDE.md`` > ``.skaldir/context.md``. The product's own file outranks
   the generic one; the vendor file we only read for compatibility ranks last.
3. **Last resort, for determinism only**: the lexicographically smaller relative path wins.
   Without it the order would depend on directory iteration, and a fingerprint that depends
   on iteration order is not evidence.

The order is a strict total order (level, ladder rank, path), so the resolution — and
therefore the fingerprint — is reproducible on any machine with the same files.

Resolution is **per entry**, not per file: an entry with an explicit id (``- [DB_WRITE] …``
or ``- DB_WRITE: …``) is replaced by the same id from a higher-precedence file, and the
decision is recorded in :attr:`ProjectContext.overrides` — which file won, which lost, and
what the losing text said. ``role`` is a scalar: the highest-precedence file that defines one
wins outright. Everything else is additive, so a nested file extends the root rules instead
of erasing them.

Typed model, limits, truncation
-------------------------------

Six sections are recognized and typed: ``role``, ``rules``, ``constraints``, ``conventions``,
``commands``, ``glossary``. An unrecognized heading is *reported* (``context explain`` lists it
with its character count) but never reaches the prompt through a heading nobody mapped.

**A file with no recognized heading at all is injected whole**, as one ``rules`` entry under the
reserved id :data:`WHOLE_FILE_ID`, and says so: a real-world ``AGENTS.md`` is prose with headings
like "Code Shape Rules" or "Testing (applies everywhere)", and refusing to inject it would make
this feature useless exactly where it is needed. The rule is therefore: *a file that speaks the
vocabulary is injected by section; a file that does not is taken as a whole, marked ``fallback``
in the report, named in a warning, and still capped like everything else.* Two prose files share
that one id, so they compete like two rules with the same id: the ladder decides which file's
text is injected and the other is recorded as overridden — the resolution never depends on the
size cap. A file with *some* recognized headings keeps the strict behaviour — its unmapped
sections are reported, not mixed in.

Budget (``DEFAULT_MAX_CHARS`` = 8 000 chars, per-section quotas in :data:`SECTION_QUOTAS`)::

    rules first  → role → constraints → conventions → commands → glossary

**The rules section is funded before every other section, and only for as much as it actually
needs.** The only thing that can leave it short is a total cap below that need, and then the
rules are still present — clipped, with an explicit marker — while the other sections are
marked omitted in the block itself.
``max_chars`` bounds the sections' text: the provenance header and the omission markers are
not charged against it (an omitted section costs one marker line, never nothing). Nothing is
dropped silently: every clip sets :attr:`ProjectContext.truncated`, appends a marker to the
block, adds a warning naming what was lost, and lands in ``context explain``.

Safety
------

* **Read-only.** Nothing here opens a file for writing, and nothing creates one. The files are
  inputs.
* **Symlinks that leave the workspace are refused**, not followed: a candidate whose resolved
  target is outside the workspace boundary is skipped and reported. (Boundary: ``--workspace``
  when given, else the git root above the working directory, else the filesystem root.)
* **Bounded read**: at most :data:`MAX_FILE_BYTES` bytes per file (with ``file_truncated`` on
  the record) and at most :data:`DEFAULT_MAX_LEVELS` levels walked.
* **The fingerprint goes into the evidence** (:meth:`ProjectContext.evidence`), so a run record
  names the exact context — by digest per file and as one block fingerprint.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "DEFAULT_MAX_CHARS",
    "DEFAULT_MAX_LEVELS",
    "FILENAME_LADDER",
    "MAX_FILE_BYTES",
    "SCHEMA",
    "SECTIONS",
    "SECTION_QUOTAS",
    "SECTION_TITLES",
    "WHOLE_FILE_ID",
    "Candidate",
    "ProjectContext",
    "Resolution",
    "Section",
    "auto_id",
    "build",
    "discover",
    "explain",
    "explain_report",
    "plan_budgets",
    "prompt_block",
    "resolve_workspace",
]

#: The fingerprint/metadata schema. Bump it when the payload shape changes — a run record that
#: carries a fingerprint has to name the recipe that produced it.
SCHEMA = "skaldir.project_context/1"

#: The ladder: highest precedence first, checked in this order inside every directory.
FILENAME_LADDER: tuple[str, ...] = (
    "SKALDIR.md",
    "SOUL.md",
    "AGENTS.md",
    "CLAUDE.md",
    ".skaldir/context.md",
)

FILENAME_RANK: dict[str, int] = {name: index for index, name in enumerate(FILENAME_LADDER)}

#: Total character budget of the injected block.
DEFAULT_MAX_CHARS = 8000

#: Per-section cap. Their sum (7 800) is just below the default total, so the default run
#: injects every section at full size and only a smaller cap forces a decision.
SECTION_QUOTAS: dict[str, int] = {
    "role": 1200,
    "rules": 3200,
    "constraints": 1200,
    "conventions": 900,
    "commands": 800,
    "glossary": 500,
}

#: The order the budget is *allocated* in — ``rules`` first, because the rules are the section
#: that must never be squeezed out.
FUNDING_ORDER: tuple[str, ...] = (
    "rules",
    "role",
    "constraints",
    "conventions",
    "commands",
    "glossary",
)

#: The order sections are *rendered* in. A prompt reads better with the role first; funding
#: order and render order are deliberately separate.
SECTION_ORDER: tuple[str, ...] = (
    "role",
    "rules",
    "constraints",
    "conventions",
    "commands",
    "glossary",
)

SECTIONS: tuple[str, ...] = SECTION_ORDER

#: How a section heading reads in the injected block (the heading is the prompt's, not the
#: file's — the source heading is already normalized away by then).
SECTION_TITLES: dict[str, str] = {
    "role": "Role",
    "rules": "Rules",
    "constraints": "Constraints",
    "conventions": "Conventions",
    "commands": "Commands",
    "glossary": "Glossary",
}

#: Heading text (lowercased, punctuation-stripped) -> section name.
SECTION_ALIASES: dict[str, str] = {
    "role": "role",
    "persona": "role",
    "identity": "role",
    "agent": "role",
    "who you are": "role",
    "rules": "rules",
    "rule": "rules",
    "requirements": "rules",
    "policies": "rules",
    "policy": "rules",
    "constraints": "constraints",
    "limits": "constraints",
    "hard constraints": "constraints",
    "constraints and limits": "constraints",
    "conventions": "conventions",
    "style": "conventions",
    "coding style": "conventions",
    "code style": "conventions",
    "conventions and style": "conventions",
    "commands": "commands",
    "build commands": "commands",
    "build": "commands",
    "tooling": "commands",
    "scripts": "commands",
    "testing": "commands",
    "glossary": "glossary",
    "terms": "glossary",
    "terminology": "glossary",
    "vocabulary": "glossary",
}

#: A file is read at most this far; the rest is reported, never silently ignored.
MAX_FILE_BYTES = 64 * 1024

#: Upper bound on the walk (a filesystem root is reached long before this).
DEFAULT_MAX_LEVELS = 64

#: Status values on a :class:`Candidate` row.
STATUS_USED = "used"
STATUS_REFUSED_ESCAPE = "refused-symlink-escape"
STATUS_REFUSED_NOT_FILE = "refused-not-a-file"
STATUS_REFUSED_UNREADABLE = "refused-unreadable"

_HEADING_RE = re.compile(r"^(?P<hashes>#{1,6})\s+(?P<title>.*?)\s*#*\s*$")
_BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(?P<text>.*\S)\s*$")
#: ``- [DB_WRITE] text`` — brackets for ids that are not symbolic.
_BRACKET_ID_RE = re.compile(r"^\[(?P<id>[^\]]{1,64})\]\s*(?P<text>.+)$")
#: ``- DB_WRITE: text`` — the symbolic form. Only UPPER_SNAKE keys count as ids, so ordinary
#: prose ("Note: …") is never mistaken for one.
_KEY_ID_RE = re.compile(r"^(?P<id>[A-Z][A-Z0-9_]{0,63}):\s*(?P<text>.+)$")

_TRUNCATED_MARKER = "…[truncated]"

#: The reserved id of a whole-file fallback (a file with no recognized heading). Sharing one id
#: is the point: two prose files then compete exactly like two rules with the same id, so the
#: precedence ladder decides which file's text is injected — not the size cap.
WHOLE_FILE_ID = "#whole-file"


def _dropped_marker(count: int) -> str:
    """The one line that says entries were dropped — the reverse of a silent cut."""
    return f"…[+{count} not shown]"


def _canonical(payload: object) -> str:
    """One canonical JSON form, so a fingerprint never depends on dict ordering."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_text(text: str) -> str:
    return _sha256_bytes(text.encode("utf-8"))


def auto_id(text: str) -> str:
    """The id of an entry that carries none: ``#<12 hex>`` of its normalized text.

    Derived from the text on purpose — the *same* rule written in two files is one rule, and
    the higher-precedence file is then recorded as the source of it.
    """
    return "#" + _sha256_text(" ".join(text.split()))[:12]


def normalize_heading(text: str) -> str:
    """Heading text as the alias table expects it: lowercase, no trailing punctuation."""
    return " ".join(text.strip().split()).strip(":.,#").strip().lower()


def git_root(start: Path) -> Path | None:
    """The nearest ancestor (or ``start`` itself) that carries a ``.git`` entry."""
    current = Path(start)
    if current.is_file():
        current = current.parent
    for directory in (current, *current.parents):
        if (directory / ".git").exists():
            return directory
    return None


def resolve_workspace(start: Path, workspace: str | Path | None = None) -> tuple[Path, str]:
    """The boundary the walk may not leave, and why it is that one.

    ``--workspace`` when given; else the git root above ``start``; else the filesystem root
    (with the reason saying so, so ``context explain`` never implies a boundary that is not
    there).
    """
    if workspace:
        return Path(workspace).expanduser().resolve(), "explicit workspace boundary"
    root = git_root(start)
    if root is not None:
        return root, "the git root above the working directory (.git found)"
    anchor = Path(start).anchor or str(Path(start))
    return Path(anchor).resolve(), "no .git found — the boundary is the filesystem root"


def _rel(path: Path, boundary: Path) -> str:
    """``path`` relative to the boundary, falling back to the absolute path."""
    try:
        return str(path.relative_to(boundary))
    except ValueError:
        return str(path)


# --------------------------------------------------------------------------- #
# discovery
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Candidate:
    """One file that *could* be injected, with the reason it was — or was not.

    Every existing candidate gets a row, used or refused: a refusal that leaves no trace is
    how a context rule silently disappears.
    """

    path: Path
    rel_path: str
    level: int
    kind: str
    status: str
    reason: str = ""
    sha256: str = ""
    chars: int = 0
    nbytes: int = 0
    file_truncated: bool = False
    text: str = ""

    @property
    def rank(self) -> int:
        """Ladder rank of the file name (0 wins)."""
        return FILENAME_RANK.get(self.kind, len(FILENAME_LADDER))

    @property
    def precedence(self) -> tuple[int, int, str]:
        """The strict total order: level, ladder rank, path — highest precedence first."""
        return (self.level, self.rank, self.rel_path)

    @property
    def used(self) -> bool:
        return self.status == STATUS_USED

    def to_dict(self) -> dict:
        """The row as it appears in ``context explain --json``."""
        return {
            "path": self.rel_path,
            "absolute_path": str(self.path),
            "level": self.level,
            "kind": self.kind,
            "rank": self.rank,
            "status": self.status,
            "reason": self.reason,
            "sha256": self.sha256,
            "chars": self.chars,
            "bytes": self.nbytes,
            "file_truncated": self.file_truncated,
        }


def _read_candidate(
    path: Path, *, rel_path: str, level: int, kind: str, workspace: Path
) -> Candidate:
    """Read one existing candidate under the safety rules, or refuse it with a reason."""
    resolved = path.resolve()
    if not resolved.is_relative_to(workspace):
        return Candidate(
            path=path,
            rel_path=rel_path,
            level=level,
            kind=kind,
            status=STATUS_REFUSED_ESCAPE,
            reason=(
                f"resolved to {resolved}, outside the workspace {workspace} — a symlink that "
                "leaves the project is never followed"
            ),
        )
    if not resolved.is_file():
        return Candidate(
            path=path,
            rel_path=rel_path,
            level=level,
            kind=kind,
            status=STATUS_REFUSED_NOT_FILE,
            reason=f"{resolved} is not a regular file",
        )
    try:
        with resolved.open("rb") as handle:
            data = handle.read(MAX_FILE_BYTES + 1)
    except OSError as exc:  # a permission problem is a finding, not a crash
        return Candidate(
            path=path,
            rel_path=rel_path,
            level=level,
            kind=kind,
            status=STATUS_REFUSED_UNREADABLE,
            reason=f"{type(exc).__name__}: {exc}",
        )
    file_truncated = len(data) > MAX_FILE_BYTES
    data = data[:MAX_FILE_BYTES]
    notes: list[str] = []
    if resolved != path:
        notes.append(f"resolved via {resolved}")
    if file_truncated:
        notes.append(f"read the first {MAX_FILE_BYTES} bytes only")
    return Candidate(
        path=path,
        rel_path=rel_path,
        level=level,
        kind=kind,
        status=STATUS_USED,
        reason=" · ".join(notes),
        sha256=_sha256_bytes(data),
        chars=len(data.decode("utf-8", errors="replace")),
        nbytes=len(data),
        file_truncated=file_truncated,
        text=data.decode("utf-8", errors="replace"),
    )


def _levels(start: Path, workspace: Path, max_levels: int) -> Iterator[tuple[int, Path]]:
    """``(level, directory)`` from the start directory up to the workspace boundary.

    The walk stops at the boundary: a file above the project is not this project's context,
    and reading one would make the result depend on where the checkout happens to live.
    """
    current = Path(start)
    for level in range(max(1, int(max_levels))):
        yield level, current
        if current == workspace or current.parent == current:
            return
        if not current.is_relative_to(workspace):
            return
        current = current.parent


def discover(
    start: str | Path | None = None,
    *,
    workspace: str | Path | None = None,
    filenames: Sequence[str] = FILENAME_LADDER,
    max_levels: int = DEFAULT_MAX_LEVELS,
) -> tuple[tuple[Candidate, ...], tuple[dict, ...], Path, Path, str]:
    """Walk up and report every existing candidate file. Read-only.

    Returns ``(candidates, levels, start_dir, workspace, workspace_reason)``, where ``levels``
    is one row per visited directory — including how many names were checked there, so the
    absences are visible and not just the hits.
    """
    raw_start = Path(start).expanduser() if start else Path.cwd()
    if raw_start.is_file():
        raw_start = raw_start.parent
    start_dir = raw_start.resolve()
    boundary, why = resolve_workspace(start_dir, workspace)
    if not start_dir.is_relative_to(boundary):
        boundary, why = start_dir, "the start directory is outside the given boundary"

    candidates: list[Candidate] = []
    levels: list[dict] = []
    for level, directory in _levels(start_dir, boundary, max_levels):
        present: list[str] = []
        for name in filenames:
            path = directory / name
            if not path.exists():
                continue
            candidates.append(
                _read_candidate(
                    path,
                    rel_path=_rel(path, boundary),
                    level=level,
                    kind=name,
                    workspace=boundary,
                )
            )
            present.append(name)
        levels.append(
            {
                "level": level,
                "directory": str(directory),
                "relative": _rel(directory, boundary) or ".",
                "checked": len(filenames),
                "present": present,
            }
        )
    candidates.sort(key=lambda item: item.precedence)
    return tuple(candidates), tuple(levels), start_dir, boundary, why


# --------------------------------------------------------------------------- #
# parsing
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RawEntry:
    """One bullet as parsed from one file, before precedence is applied."""

    section: str
    id: str
    explicit_id: bool
    text: str
    line: int
    block: bool = False


@dataclass(frozen=True)
class Parsed:
    """What one file contributed: its title, its entries, and what was *not* mapped."""

    title: str = ""
    entries: tuple[RawEntry, ...] = ()
    unmapped: tuple[dict, ...] = ()
    preamble: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    fallback: bool = False


def _entry_for(section: str, raw: str, *, line: int) -> RawEntry:
    """Split one line into ``(id, text)`` — explicit id, or the text-derived one."""
    text_value = raw.strip()
    bracketed = _BRACKET_ID_RE.match(text_value)
    if bracketed:
        return RawEntry(
            section=section,
            id=bracketed.group("id").strip(),
            explicit_id=True,
            text=bracketed.group("text").strip(),
            line=line,
        )
    keyed = _KEY_ID_RE.match(text_value)
    if keyed:
        return RawEntry(
            section=section,
            id=keyed.group("id").strip(),
            explicit_id=True,
            text=keyed.group("text").strip(),
            line=line,
        )
    return RawEntry(
        section=section, id=auto_id(text_value), explicit_id=False, text=text_value, line=line
    )


def duplicate_keys(entries: Sequence[RawEntry]) -> list[tuple[str, str]]:
    """``(section, id)`` pairs that occur more than once inside one file, in first-seen order."""
    seen: set[tuple[str, str]] = set()
    duplicates: list[tuple[str, str]] = []
    for entry in entries:
        key = (entry.section, entry.id)
        if key in seen and key not in duplicates:
            duplicates.append(key)
        seen.add(key)
    return duplicates


def parse_source(text: str, *, source: str) -> Parsed:
    """Parse one instruction file into typed entries.

    Recognized: a ``## Role``-style heading (see :data:`SECTION_ALIASES`) and bullets
    (``-``/``*``/``+``/``1.``). A plain or indented line continues the entry above it; a list
    section written without bullets at all treats each non-empty line as its own entry.

    Not mapped, only reported: a leading level-1 title, the text before the first recognized
    heading, and every unrecognized heading with its character count.

    A file where **no** heading is recognized is returned whole, as one ``rules`` entry with
    :data:`WHOLE_FILE_ID` (blank lines collapsed): a real-world instruction file is prose, and
    taking it whole beats injecting nothing. ``Parsed.fallback`` says it happened.
    """
    entries: list[RawEntry] = []
    unmapped: list[dict] = []
    preamble: list[str] = []
    title = ""
    current: str | None = None
    open_unmapped: str | None = None
    unmapped_chars = 0
    bullets: list[tuple[int, str]] = []
    bare: list[tuple[int, str]] = []

    def close_unmapped() -> None:
        nonlocal open_unmapped, unmapped_chars
        if open_unmapped is not None:
            unmapped.append({"heading": open_unmapped, "chars": unmapped_chars})
            open_unmapped, unmapped_chars = None, 0

    def flush() -> None:
        """Turn the buffered lines of the section that just ended into entries.

        A ``role`` is one value, not a list: its lines are joined into a single entry, so a
        multi-line role statement survives as the prose it was written as.
        """
        nonlocal bullets, bare
        if current is not None:
            buffered = bullets if bullets else bare
            if current == "role":
                joined = "\n".join(text for _line, text in buffered).strip()
                if joined:
                    entries.append(
                        RawEntry(
                            section="role",
                            id=auto_id(joined),
                            explicit_id=True,
                            text=joined,
                            line=buffered[0][0],
                        )
                    )
            else:
                for line, raw in buffered:
                    entries.append(_entry_for(current, raw, line=line))
        bullets, bare = [], []

    lines = text.splitlines()
    for number, line in enumerate(lines, start=1):
        heading = _HEADING_RE.match(line)
        if heading:
            flush()
            close_unmapped()
            name = normalize_heading(heading.group("title"))
            if name in SECTION_ALIASES:
                current = SECTION_ALIASES[name]
                continue
            current = None
            if not title and len(heading.group("hashes")) == 1 and not entries:
                title = heading.group("title").strip()  # the file's title, not a section
                continue
            open_unmapped = heading.group("title").strip()
            continue
        if open_unmapped is not None:
            unmapped_chars += len(line)
            continue
        if current is None:
            if line.strip():
                preamble.append(line.strip())  # before any recognized heading: never injected
            continue
        bullet = _BULLET_RE.match(line)
        if bullet:
            bullets.append((number, bullet.group("text")))
        elif line.strip():
            bare.append((number, line.strip()))
    flush()
    close_unmapped()

    fallback = False
    if not entries:
        whole = "\n".join(line.rstrip() for line in lines if line.strip()).strip()
        if whole:
            entries.append(
                RawEntry(
                    section="rules",
                    id=WHOLE_FILE_ID,
                    explicit_id=True,
                    text=whole,
                    line=1,
                    block=True,
                )
            )
            fallback = True

    warnings: list[str] = [
        f"{source}: section '{row['heading']}' is not one of {', '.join(SECTIONS)} — "
        + (
            "inside the whole-file fallback below, not mapped to a section"
            if fallback
            else f"{row['chars']} chars reported, not injected"
        )
        for row in unmapped
    ]
    if preamble:
        warnings.append(
            f"{source}: {len(preamble)} line(s) before the first recognized section are "
            + ("inside the whole-file fallback" if fallback else "not injected")
            + " — put them under a heading to have them typed"
        )
    if fallback:
        warnings.append(
            f"{source}: no recognized section heading — the file is injected whole as one "
            f"rules entry ({len(entries[-1].text)} chars); rename its headings to one of "
            f"{', '.join(SECTIONS)} to make it typed"
        )
    for section, entry_id in duplicate_keys(entries):
        warnings.append(
            f"{source}: id '{entry_id}' appears twice in '{section}' — "
            "the first occurrence wins"
        )
    return Parsed(
        title=title,
        entries=tuple(entries),
        unmapped=tuple(unmapped),
        preamble=tuple(preamble),
        warnings=tuple(warnings),
        fallback=fallback,
    )


@dataclass(frozen=True)
class Resolution:
    """One win/lose decision — the answer to "which rule won, and from where?"."""

    section: str
    id: str
    winner: str
    winner_level: int
    winner_kind: str
    text: str
    losers: tuple[str, ...] = ()
    losing_text: tuple[str, ...] = ()
    auto_id: bool = False
    line: int = 0
    block: bool = False

    def to_dict(self) -> dict:
        return {
            "section": self.section,
            "id": self.id,
            "auto_id": self.auto_id,
            "block": self.block,
            "winner": self.winner,
            "winner_level": self.winner_level,
            "winner_kind": self.winner_kind,
            "text": self.text,
            "losers": list(self.losers),
            "losing_text": list(self.losing_text),
            "line": self.line,
        }


# --------------------------------------------------------------------------- #
# the injected block
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Section:
    """One typed section as it will be injected, with its budget accounting."""

    name: str
    text: str
    quota: int
    used: int
    total: int
    included: int
    clipped: bool = False
    dropped_ids: tuple[str, ...] = ()
    omitted_reason: str = ""

    @property
    def omitted(self) -> bool:
        return bool(self.omitted_reason)

    @property
    def dropped(self) -> int:
        return len(self.dropped_ids)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "quota": self.quota,
            "chars": self.used,
            "entries_total": self.total,
            "entries_included": self.included,
            "entries_dropped": self.dropped,
            "dropped_ids": list(self.dropped_ids),
            "clipped": self.clipped,
            "omitted": self.omitted,
            "omitted_reason": self.omitted_reason,
            "text": self.text,
        }


def plan_budgets(
    max_chars: int,
    quotas: Mapping[str, int] | None = None,
    needs: Mapping[str, int] | None = None,
) -> dict[str, int]:
    """Split ``max_chars`` by :data:`FUNDING_ORDER`: the rules are funded first.

    Each section gets at most its quota, at most what it actually needs (``needs``), and never
    more than what is left. Funding the rules *first* is what makes them impossible to starve:
    a large rules section takes its room before anything else is considered, and a small one
    leaves the room to the other sections instead of holding it. A cap above the sum of the
    quotas leaves the remainder unused on purpose — quotas are ceilings, not targets, and a
    section with little to say must not hand its room to a more verbose neighbour (that would
    make the block depend on an unrelated file's length).
    """
    table = dict(quotas or SECTION_QUOTAS)
    budget: dict[str, int] = {}
    remaining = max(0, int(max_chars))
    for name in FUNDING_ORDER:
        give = max(0, int(table.get(name, 0)))
        if needs is not None and name in needs:
            give = min(give, max(0, int(needs[name])))
        give = min(give, remaining)
        budget[name] = give
        remaining -= give
    return budget


def _list_chars(lines: Sequence[str]) -> int:
    """Characters a bullet list costs, separators included — what a section *needs*."""
    return sum(len(line) + (1 if index else 0) for index, line in enumerate(lines))


def _clip_bullets(
    rows: Sequence[tuple[str, str]], budget: int
) -> tuple[list[str], list[str], list[str], int, bool]:
    """Keep whole ``(id, line)`` entries while they fit, then say what was dropped.

    Returns ``(kept_lines, kept_ids, dropped_ids, used, clipped)``. The dropped marker is paid
    for out of the same budget: entries are handed back until it fits. A single entry longer
    than the whole section is clipped rather than dropped — the section keeps its head, and
    ``clipped`` is true even though no id was lost, because a cut is a cut. The one floor is a
    budget smaller than the marker itself: then the marker is all that fits, and it is emitted
    anyway (an empty section would read as "nothing to say").
    """
    if budget <= 0:
        return [], [], [entry_id for entry_id, _line in rows], 0, False
    kept: list[tuple[str, str]] = []
    used = 0
    for entry_id, line in rows:
        cost = len(line) + (1 if kept else 0)
        if used + cost > budget:
            break
        kept.append((entry_id, line))
        used += cost
    dropped = [entry_id for entry_id, _line in rows[len(kept):]]
    if not dropped:
        return [line for _id, line in kept], [entry_id for entry_id, _line in kept], [], used, False
    marker = _dropped_marker(len(dropped))
    while kept and used + 1 + len(marker) > budget:
        popped = kept.pop()
        used -= len(popped[1]) + 1
        dropped.insert(0, popped[0])
        marker = _dropped_marker(len(dropped))
    if not kept:
        head = rows[0][1][: max(0, budget - len(_TRUNCATED_MARKER))].rstrip()
        clipped_line = head + _TRUNCATED_MARKER
        return (
            [clipped_line],
            [rows[0][0]],
            [entry_id for entry_id, _line in rows[1:]],
            len(clipped_line),
            True,
        )
    kept.append(("", marker))
    return (
        [line for _id, line in kept],
        [entry_id for entry_id, _line in kept if entry_id],
        dropped,
        used + 1 + len(marker),
        True,
    )


def _render_section(
    name: str, rows: Sequence[tuple[str, str]], budget: int, role_text: str
) -> Section:
    """Budget one section and render its text (bullets for the list sections, prose for role).

    An empty section is **empty, not omitted**: a section with nothing to inject is left out
    silently (there is nothing to lose), while a section with entries the cap could not pay for
    says so in the block. Those are different findings and are reported as such.
    """
    total = len(rows) if name != "role" else (1 if role_text else 0)
    if total == 0:
        return Section(
            name=name,
            text="",
            quota=budget,
            used=0,
            total=0,
            included=0,
        )
    if budget <= 0:
        reason = "no budget left — the rules section is funded first"
        text = f"…[section omitted: {reason}]"
        return Section(
            name=name,
            text=text,
            quota=0,
            used=len(text),
            total=total,
            included=0,
            dropped_ids=tuple(entry_id for entry_id, _line in rows),
            omitted_reason=reason,
        )
    if name == "role":
        if len(role_text) <= budget:
            return Section(
                name=name, text=role_text, quota=budget, used=len(role_text), total=1, included=1
            )
        head = role_text[: max(0, budget - len(_TRUNCATED_MARKER))].rstrip()
        clipped_text = head + _TRUNCATED_MARKER
        return Section(
            name=name,
            text=clipped_text,
            quota=budget,
            used=len(clipped_text),
            total=1,
            included=1,
            clipped=True,
        )
    kept_lines, kept_ids, dropped_ids, used, clipped = _clip_bullets(rows, budget)
    return Section(
        name=name,
        text="\n".join(kept_lines),
        quota=budget,
        used=used,
        total=len(rows),
        included=len(kept_ids),
        clipped=clipped,
        dropped_ids=tuple(dropped_ids),
    )


@dataclass
class ProjectContext:
    """The typed context, the block it renders to, and the fingerprint of exactly that."""

    start_dir: Path
    workspace: Path
    workspace_reason: str
    candidates: tuple[Candidate, ...] = ()
    levels: tuple[dict, ...] = ()
    sections: tuple[Section, ...] = ()
    resolutions: tuple[Resolution, ...] = ()
    overrides: tuple[Resolution, ...] = ()
    fallbacks: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    max_chars: int = DEFAULT_MAX_CHARS
    block: str = ""
    fingerprint: str = ""
    _payload: dict = field(default_factory=dict, repr=False)

    @property
    def sources(self) -> tuple[Candidate, ...]:
        """The files that were actually injected, in precedence order (highest first)."""
        return tuple(item for item in self.candidates if item.used)

    @property
    def refused(self) -> tuple[Candidate, ...]:
        """The candidates that were seen and not injected, each with its reason."""
        return tuple(item for item in self.candidates if not item.used)

    @property
    def fingerprint_short(self) -> str:
        return self.fingerprint[:16]

    @property
    def chars(self) -> int:
        return len(self.block)

    @property
    def truncated(self) -> bool:
        """True when a section was clipped or omitted — the block says so in-band."""
        return any(section.clipped or section.omitted for section in self.sections)

    @property
    def entries_total(self) -> int:
        return sum(section.total for section in self.sections)

    @property
    def entries_included(self) -> int:
        return sum(section.included for section in self.sections)

    @property
    def budgeted_chars(self) -> int:
        """The characters that count against ``max_chars`` (omission markers sit outside it)."""
        return sum(section.used for section in self.sections if not section.omitted)

    @property
    def omitted_names(self) -> tuple[str, ...]:
        """Sections the cap pushed out of the block — each one is named in a warning too."""
        return tuple(section.name for section in self.sections if section.omitted)

    @property
    def clipped_names(self) -> tuple[str, ...]:
        """Sections the cap cut short — each one carries an in-band marker."""
        return tuple(section.name for section in self.sections if section.clipped)

    @property
    def empty(self) -> bool:
        """No instruction file was found: an empty block is a finding, not a success."""
        return not self.sources

    def payload(self) -> dict:
        """The exact object the fingerprint is taken over (recomputable by hand)."""
        return dict(self._payload)

    def fingerprint_of_payload(self) -> str:
        """Recompute the fingerprint from :meth:`payload` — the check a verifier runs."""
        return _sha256_text(_canonical(self._payload))

    def evidence(self) -> dict:
        """What a run record carries to prove which context it used."""
        return {
            "schema": SCHEMA,
            "fingerprint": self.fingerprint,
            "fingerprint_short": self.fingerprint_short,
            "chars": self.chars,
            "max_chars": self.max_chars,
            "truncated": self.truncated,
            "start_dir": str(self.start_dir),
            "workspace": str(self.workspace),
            "sources": [
                {
                    "path": item.rel_path,
                    "sha256": item.sha256,
                    "level": item.level,
                    "kind": item.kind,
                    "chars": item.chars,
                }
                for item in self.sources
            ],
            "refused": [
                {"path": item.rel_path, "status": item.status, "reason": item.reason}
                for item in self.refused
            ],
        }

    def to_dict(self) -> dict:
        """The full report behind ``context show|explain --json``."""
        return {
            "schema": SCHEMA,
            "block": self.block,
            "fingerprint": self.fingerprint,
            "fingerprint_short": self.fingerprint_short,
            "chars": self.chars,
            "max_chars": self.max_chars,
            "truncated": self.truncated,
            "empty": self.empty,
            "start_dir": str(self.start_dir),
            "workspace": str(self.workspace),
            "workspace_reason": self.workspace_reason,
            "sections": [section.to_dict() for section in self.sections],
            "entries_total": self.entries_total,
            "entries_included": self.entries_included,
            "budgeted_chars": self.budgeted_chars,
            "omitted_sections": list(self.omitted_names),
            "clipped_sections": list(self.clipped_names),
            "sources": [item.to_dict() for item in self.sources],
            "refused": [item.to_dict() for item in self.refused],
            "levels": [dict(row) for row in self.levels],
            "overrides": [row.to_dict() for row in self.overrides],
            "resolutions": [row.to_dict() for row in self.resolutions],
            "fallback_files": list(self.fallbacks),
            "warnings": list(self.warnings),
            "fingerprint_payload": self.payload(),
        }


def _resolve_entries(
    sources: Sequence[Candidate],
) -> tuple[dict[str, list[str]], dict[tuple[str, str], Resolution], list[str], list[str]]:
    """Apply the precedence ladder per entry.

    Returns ``(entry order per section, winner per (section, id), warnings, fallback paths)``.
    Identical text under the same id is a dedupe; *different* text under the same id is an
    override and is recorded with the losing text, because that is the decision a reader wants
    to see. A file whose headings are all unrecognized lands in ``fallbacks``: it was injected
    whole rather than not at all.
    """
    warnings: list[str] = []
    fallbacks: list[str] = []
    winners: dict[tuple[str, str], Resolution] = {}
    order: dict[str, list[str]] = {name: [] for name in SECTIONS}
    for candidate in sources:
        parsed = parse_source(candidate.text, source=candidate.rel_path)
        warnings.extend(parsed.warnings)
        if parsed.fallback:
            fallbacks.append(candidate.rel_path)
        for entry in parsed.entries:
            key = (entry.section, entry.id)
            previous = winners.get(key)
            if previous is None:
                winners[key] = Resolution(
                    section=entry.section,
                    id=entry.id,
                    winner=candidate.rel_path,
                    winner_level=candidate.level,
                    winner_kind=candidate.kind,
                    text=entry.text,
                    auto_id=not entry.explicit_id and not entry.block,
                    line=entry.line,
                    block=entry.block,
                )
                order[entry.section].append(entry.id)
                continue
            if entry.text.strip() == previous.text.strip():
                continue  # the same rule in two files: one rule, no noise
            winners[key] = Resolution(
                section=previous.section,
                id=previous.id,
                winner=previous.winner,
                winner_level=previous.winner_level,
                winner_kind=previous.winner_kind,
                text=previous.text,
                losers=previous.losers + (candidate.rel_path,),
                losing_text=previous.losing_text + (entry.text,),
                auto_id=previous.auto_id,
                line=previous.line,
                block=previous.block,
            )
    return order, winners, warnings, fallbacks


def _display_line(row: Resolution, section: str) -> str:
    """One entry as the block shows it.

    A bullet carries ``- `` and, when the id was written in the file, the id. A whole-file
    fallback (a file with no recognized heading) is rendered as the text it is — wrapping prose
    in a bullet would change what the file says. ``role`` is prose either way.
    """
    if section == "role" or row.block:
        return row.text
    body = row.text if row.auto_id else f"[{row.id}] {row.text}"
    return f"- {body}"


def _fingerprint_payload(
    *, max_chars: int, sections: Sequence[Section], sources: Sequence[Candidate]
) -> dict:
    """What the fingerprint covers: the rendered sections, the sources, and the cap.

    Deliberately *not* in it: absolute paths (the fingerprint must be comparable between
    machines and checkouts) and timestamps. The rendered section text *is* in it, so the digest
    changes exactly when the injected characters change.
    """
    return {
        "schema": SCHEMA,
        "max_chars": int(max_chars),
        "sections": [
            {
                "name": section.name,
                "text": section.text,
                "quota": section.quota,
                "omitted": section.omitted,
                "clipped": section.clipped,
            }
            for section in sections
        ],
        "sources": [
            {
                "path": item.rel_path,
                "kind": item.kind,
                "level": item.level,
                "sha256": item.sha256,
                "chars": item.chars,
            }
            for item in sources
        ],
    }


def _render_block(
    *,
    sections: Sequence[Section],
    sources: Sequence[Candidate],
    fingerprint_short: str,
    max_chars: int,
    budgeted_chars: int,
) -> str:
    """The prompt block, verbatim: one provenance header, then the sections in render order.

    The header names the files, *displays* the fingerprint and reports the budget honestly:
    ``chars`` is what the sections cost against the cap (the omission markers are not charged),
    and the omitted sections are named in the header as well as in the body. The fingerprint
    itself is taken over :meth:`ProjectContext.payload`, so the block never hashes itself — the
    header is not part of the payload, because a display change must not change the context.
    """
    body: list[str] = []
    for section in sections:
        if not section.text:
            continue  # nothing to inject for it: an empty section is not a section
        body.append(f"## {SECTION_TITLES.get(section.name, section.name)}")
        body.append(section.text)
        body.append("")
    while body and not body[-1]:
        body.pop()
    text = "\n".join(body)
    names = ", ".join(item.rel_path for item in sources) if sources else "none found"
    omitted = [section.name for section in sections if section.omitted]
    marker = f" · omitted {','.join(omitted)}" if omitted else ""
    header = (
        f"<!-- project context · {SCHEMA} · fingerprint {fingerprint_short} · "
        f"chars {budgeted_chars}/{int(max_chars)}{marker} · sources: {names} -->"
    )
    return f"{header}\n{text}\n" if text else f"{header}\n"


def build(
    start: str | Path | None = None,
    *,
    workspace: str | Path | None = None,
    max_chars: int = DEFAULT_MAX_CHARS,
    quotas: Mapping[str, int] | None = None,
    filenames: Sequence[str] = FILENAME_LADDER,
    max_levels: int = DEFAULT_MAX_LEVELS,
) -> ProjectContext:
    """Discover, resolve, budget and render the project context. Read-only.

    The returned :class:`ProjectContext` carries the block, the fingerprint of exactly that
    block, and the whole resolution trail (which file won which entry, what was refused and
    why, what the cap cut), so ``context explain`` has nothing left to guess.
    """
    candidates, levels, start_dir, boundary, why = discover(
        start, workspace=workspace, filenames=filenames, max_levels=max_levels
    )
    used = [item for item in candidates if item.used]
    warnings: list[str] = []
    for item in candidates:
        if item.file_truncated:
            warnings.append(
                f"{item.rel_path}: read the first {MAX_FILE_BYTES} bytes only "
                f"({item.nbytes} bytes read)"
            )
        if not item.used:
            warnings.append(f"{item.rel_path}: {item.status} — {item.reason}")
    if not used:
        warnings.append(
            f"no project context file found in the walk from {start_dir} up to {boundary} "
            f"(looked for {', '.join(FILENAME_LADDER)}) — the block is empty"
        )

    order, winners, parse_warnings, fallbacks = _resolve_entries(used)
    warnings.extend(parse_warnings)
    resolutions: list[Resolution] = []
    overrides: list[Resolution] = []
    for section in SECTIONS:
        for entry_id in order[section]:
            row = winners[(section, entry_id)]
            resolutions.append(row)
            if row.losers:
                overrides.append(row)
                warnings.append(
                    f"{section} '{entry_id}': {row.winner} (level {row.winner_level}) wins over "
                    f"{', '.join(row.losers)}"
                )

    role_rows = [
        (_display_line(winners[("role", entry_id)], "role"), winners[("role", entry_id)].text)
        for entry_id in order["role"]
    ]
    if len(role_rows) > 1:
        losers = ", ".join(
            winners[("role", entry_id)].winner for entry_id in order["role"][1:]
        )
        warnings.append(
            f"role: {winners[('role', order['role'][0])].winner} wins over {losers} — the other "
            "role statement(s) are not injected (see `context explain`)"
        )
    role_text = role_rows[0][1] if role_rows else ""
    rows_by_section: dict[str, list[tuple[str, str]]] = {}
    needs: dict[str, int] = {"role": len(role_text)}
    for name in SECTION_ORDER:
        if name == "role":
            continue
        rows = [
            (entry_id, _display_line(winners[(name, entry_id)], name))
            for entry_id in order[name]
        ]
        rows_by_section[name] = rows
        needs[name] = _list_chars([line for _entry_id, line in rows])

    budgets = plan_budgets(max_chars, quotas, needs)
    sections: list[Section] = []
    for name in SECTION_ORDER:
        if name == "role":
            sections.append(_render_section(name, [], budgets.get(name, 0), role_text))
            continue
        sections.append(_render_section(name, rows_by_section[name], budgets.get(name, 0), ""))

    for section in sections:
        if section.clipped:
            where = f"at max_chars={max_chars} (quota {section.quota})"
            if section.dropped_ids:
                warnings.append(
                    f"{section.name}: {len(section.dropped_ids)} of {section.total} entries are "
                    f"not in the block {where} — dropped: "
                    + ", ".join(section.dropped_ids[:4])
                    + (" …" if len(section.dropped_ids) > 4 else "")
                )
            else:
                warnings.append(
                    f"{section.name}: its entry is cut short (…[truncated]) {where} — "
                    f"{section.used} of its characters are in the block"
                )
        if section.omitted:
            warnings.append(f"{section.name}: omitted — {section.omitted_reason}")

    payload = _fingerprint_payload(max_chars=max_chars, sections=sections, sources=used)
    fingerprint = _sha256_text(_canonical(payload))
    block = _render_block(
        sections=sections,
        sources=used,
        fingerprint_short=fingerprint[:16],
        max_chars=max_chars,
        budgeted_chars=sum(
            section.used for section in sections if not section.omitted
        ),
    )
    return ProjectContext(
        start_dir=start_dir,
        workspace=boundary,
        workspace_reason=why,
        candidates=candidates,
        levels=levels,
        sections=tuple(sections),
        resolutions=tuple(resolutions),
        overrides=tuple(overrides),
        fallbacks=tuple(fallbacks),
        warnings=tuple(warnings),
        max_chars=int(max_chars),
        block=block,
        fingerprint=fingerprint,
        _payload=payload,
    )


def prompt_block(
    start: str | Path | None = None,
    *,
    workspace: str | Path | None = None,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> tuple[str, str]:
    """The two things a caller needs: ``(block, fingerprint)``.

    The fingerprint is stable for the same workspace content — same files, same cap, same
    digest on any machine — so it can be recorded in a run's evidence and compared later.
    """
    context = build(start, workspace=workspace, max_chars=max_chars)
    return context.block, context.fingerprint


def explain_report(
    context: ProjectContext, *, quotas: Mapping[str, int] | None = None
) -> dict:
    """The "what would be injected, and why" report for an **already built** context.

    Split out so ``context explain`` reads the files once: the CLI builds the context (that is
    the read) and this only adds the why — the ladder that was applied, the budget plan and its
    truncation rule.
    """
    report = context.to_dict()
    report["ladder"] = {
        "checked_in_each_directory": list(FILENAME_LADDER),
        "rules": [
            "a file closer to the working directory wins over one further up",
            "inside one directory: " + " > ".join(FILENAME_LADDER),
            "last resort (determinism only): the lexicographically smaller relative path wins",
            "per entry: an explicit id is replaced by the same id from a higher-precedence "
            "file; 'role' is taken whole from the highest-precedence file that defines one; "
            "everything else is additive",
        ],
        "why_this_order": (
            "the product's own file (SKALDIR.md) states the constraint that binds this agent, "
            "the owner's SOUL.md comes next, then the generic AGENTS.md other harnesses read; "
            "CLAUDE.md is read for compatibility only and never outranks them"
        ),
    }
    report["budget"] = {
        "max_chars": context.max_chars,
        "quotas": dict(quotas or SECTION_QUOTAS),
        "funding_order": list(FUNDING_ORDER),
        "planned": {section.name: section.quota for section in context.sections},
        "truncation_rule": (
            "the rules section is funded first (and only for what it actually needs), so it "
            "cannot be starved by another section — a cap below its need still emits it "
            "clipped with an explicit marker while the other sections are marked omitted; "
            "whole entries are dropped from the end of a section first (the lowest-precedence "
            "entries go first, and later entries inside one file after them) before any single "
            "entry is cut, and every dropped entry is named in the warnings"
        ),
    }
    return report


def explain(
    start: str | Path | None = None,
    *,
    workspace: str | Path | None = None,
    max_chars: int = DEFAULT_MAX_CHARS,
    quotas: Mapping[str, int] | None = None,
) -> dict:
    """Build, then report — the full ``context explain`` document in one call."""
    context = build(start, workspace=workspace, max_chars=max_chars, quotas=quotas)
    return explain_report(context, quotas=quotas)
