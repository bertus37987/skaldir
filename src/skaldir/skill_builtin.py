"""Preinstalled skills shipped with the package.

Import-only plumbing (SECURITY.md: skill bodies are proposals; nothing here is
auto-injected into a prompt). Every builtin is a valid SKILL.md body stored as
package data under ``skaldir/skills_builtin/``; ``import_builtin`` inserts it
into a :class:`skaldir.skills.SkillStore` with ``promoted`` status.
"""

from __future__ import annotations

import importlib.resources

from .skills import STATUS_PROMOTED, SkillCandidate, SkillStore

#: Files live in `src/skaldir/skills_builtin/<name>.md`. Names come from the
#: ``name:`` in each file's frontmatter — kept in sync by the catalog test
#: (``tests/test_skills_builtin.py``).
BUILTIN_SKILL_NAMES = (
    "repo-audit",
    "write-test",
    "explain-code",
    "review-pr",
    "docker-triage",
    "computer-use",
)


def load_builtin(name: str) -> str:
    """The SKILL.md body shipped for a builtin skill (KeyError when unknown)."""
    if name not in BUILTIN_SKILL_NAMES:
        raise KeyError(f"unknown builtin skill: {name}")
    ref = importlib.resources.files("skaldir").joinpath(f"skills_builtin/{name}.md")
    return ref.read_text(encoding="utf-8")


def import_builtin(store: SkillStore, name: str) -> SkillCandidate:
    """Import a builtin skill into the store as an already-``promoted`` candidate.

    Idempotent: re-importing an existing builtin updates body/title, keeping
    usage stats (``SkillStore.upsert`` merges by name).
    """
    body = load_builtin(name)
    candidate = SkillCandidate(
        name=name,
        title=_title(body, name),
        tool_chain=[],
        occurrences=1,
        sessions=["builtin"],
        evidence=[f"builtin skill shipped with skaldir ({name})"],
        body=body,
        status=STATUS_PROMOTED,
        version="0.1.0",
    )
    return store.upsert(candidate)


def _title(body: str, fallback: str) -> str:
    """The first `# `-heading line (without a leading '#'), else the name."""
    for line in body.splitlines():
        if line.startswith("# ") and len(line) > 2:
            return line[2:].strip() or fallback
    return fallback
