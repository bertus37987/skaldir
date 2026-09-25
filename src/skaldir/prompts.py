"""Prompt registry — versioned prompt files as first-class artifacts.

Prompts are data: ``prompts/<id>.v<N>.md`` with YAML frontmatter. The registry
loads the latest version per id. See docs/PROMPTS.md for the inventory and
policy; prompts never contain secrets, and external content is only included
via spotlight() wrappers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from .env import getenv

_FILENAME = re.compile(r"^(?P<id>[a-z0-9_\-]+)\.v(?P<version>\d+)\.md$")

REQUIRED_IDS = (
    "main_agent_steering",
    "voice_triage",
    "voice_synthesis",
    "same_problem_judge",
    "incident_extract",
    "skill_draft",
    "tool_spec_draft",
    "cu_step_plan",
    "spend_intent",
    "completion_check",
    "voice_redirect",
    "slop_check",
    "research_note",
)


@dataclass
class Prompt:
    id: str
    version: int
    meta: dict
    body: str
    path: Path


def default_root() -> Path:
    """Resolve the prompts directory: $SKALDIR_PROMPTS or the repo's ``prompts/``."""
    override = getenv("PROMPTS")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2] / "prompts"


class PromptRegistry:
    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root) if root else default_root()

    def load(self) -> dict[str, Prompt]:
        """Load the latest version of every prompt in the root directory."""
        if not self.root.is_dir():
            raise FileNotFoundError(f"prompts directory not found: {self.root}")
        latest: dict[str, Prompt] = {}
        for path in sorted(self.root.glob("*.md")):
            match = _FILENAME.match(path.name)
            if not match:
                continue
            prompt = _parse(path, match.group("id"), int(match.group("version")))
            current = latest.get(prompt.id)
            if current is None or prompt.version > current.version:
                latest[prompt.id] = prompt
        return latest

    def get(self, prompt_id: str) -> Prompt:
        prompts = self.load()
        if prompt_id not in prompts:
            raise KeyError(f"unknown prompt id: {prompt_id} (known: {sorted(prompts)})")
        return prompts[prompt_id]

    def all(self) -> list[Prompt]:
        return sorted(self.load().values(), key=lambda p: p.id)

    def ids(self) -> list[str]:
        return sorted(self.load().keys())


def _parse(path: Path, prompt_id: str, version: int) -> Prompt:
    text = path.read_text(encoding="utf-8")
    meta: dict = {}
    body = text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            meta = yaml.safe_load(parts[1]) or {}
            body = parts[2].lstrip("\n")
    if not isinstance(meta, dict):
        raise ValueError(f"{path}: frontmatter must be a YAML mapping")
    return Prompt(id=prompt_id, version=version, meta=meta, body=body.strip(), path=path)
