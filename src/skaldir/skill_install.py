"""`skaldir skill install <name-or-github-repo>` — installer (stdlib only, no LLM).

Accepts a builtin skill name (``write-test``) or a GitHub repo
(``https://github.com/owner/repo`` or ``owner/repo``). GitHub repos are fetched
as a tarball from ``https://codeload.github.com/<owner>/<repo>/tar.gz/HEAD`` and
validated: only ``.md``/``.json``/``.yaml``/``.toml`` files, max 256 KB,
installed into ``$SKALDIR_HOME/skills``. Non-skill repos are refused honestly.
Registration reuses the existing skills.py store surface.
"""

from __future__ import annotations

import io
import tarfile
from pathlib import Path
from urllib import request

from .skill_builtin import BUILTIN_SKILL_NAMES, load_builtin
from .skills import STATUS_PROMOTED, SkillCandidate, SkillStore, is_valid_skill_body

#: Total decompressed skill content we are willing to accept (safety cap).
MAX_BYTES = 256 * 1024

ALLOWED_SUFFIXES = (".md", ".json", ".yaml", ".yml", ".toml")

CODELOAD_URL = "https://codeload.github.com/{owner}/{repo}/tar.gz/HEAD"


class InstallError(Exception):
    """An install was refused — the message names the exact reason."""


def parse_target(target: str) -> tuple[str, str, str | None]:
    """Classify the argument: ``("builtin", name, None)`` or ``("github", owner, repo)``."""
    target = (target or "").strip()
    if not target:
        raise InstallError("empty install target — pass a skill name or owner/repo")
    marker = "github.com/"
    if marker in target:
        tail = target.split(marker, 1)[1].split("?")[0].split("#")[0]
        parts = [p for p in tail.split("/") if p]
        if len(parts) < 2:
            raise InstallError(f"cannot parse github url: {target}")
        return "github", parts[0], parts[1]
    if "/" in target:
        parts = [p for p in target.split("/") if p]
        if len(parts) != 2:
            raise InstallError(f"cannot parse owner/repo: {target}")
        return "github", parts[0], parts[1]
    return "builtin", target, None


def _fetch_codeload(url: str, max_bytes: int) -> bytes:
    """GET the tarball (stdlib urllib, no tokens needed); InstallError names the failure."""
    try:
        req = request.Request(url, headers={"User-Agent": "skaldir-skill-install"})
        with request.urlopen(req, timeout=30) as resp:
            data = resp.read(max_bytes + 1)
    except Exception as exc:  # noqa: BLE001 — the reason text is the contract
        raise InstallError(f"network down: cannot reach {url} ({exc})") from exc
    if len(data) > max_bytes:
        raise InstallError(f"tarball exceeds {max_bytes} bytes — refusing to unpack")
    return data


def _extract_skill_files(data: bytes) -> list[tuple[str, bytes]]:
    """Allowed files from the tarball, emptied honestly when nothing applies."""
    try:
        members: list[tuple[tarfile.TarInfo, bytes]] = []
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            for member in tar.getmembers():
                if not member.isfile():
                    continue
                fh = tar.extractfile(member)
                if fh is None:
                    continue
                members.append((member, fh.read()))
    except tarfile.TarError as exc:
        raise InstallError(f"not a valid tar.gz ({exc})") from exc
    allowed: list[tuple[str, bytes]] = []
    refused: list[str] = []
    total = 0
    for member, content in members:
        # Strip the top-level `repo-<sha>/` directory codeload adds.
        parts = member.name.split("/", 1)
        rel = parts[1] if len(parts) == 2 and parts[1] else member.name
        if Path(rel).suffix.lower() not in ALLOWED_SUFFIXES:
            refused.append(rel)
            continue
        total += len(content)
        if total > MAX_BYTES:
            raise InstallError(f"skill content exceeds the 256KB ({MAX_BYTES} byte) cap")
        allowed.append((rel, content))
    detail = ", ".join(refused[:3]) if refused else ""
    if not allowed:
        if detail:
            raise InstallError(
                f"no skill: repo contains no installable skill file "
                f"(non-skill files: {detail} — only .md/.json/.yaml/.toml accepted)"
            )
        raise InstallError("no skill: repo is empty or contains no .md files")
    if detail:
        raise InstallError(
            f"refused: non-skill files present (e.g. {detail}) — "
            "only .md/.json/.yaml/.toml files may be installed (no scripts/binaries)"
        )
    return allowed


def _pick_skill_body(allowed: list[tuple[str, bytes]], repo: str) -> str:
    """The best .md body in an allowed file set (honest refusal otherwise)."""
    for rel, data in allowed:
        if rel.lower().endswith(".md"):
            text = data.decode("utf-8", errors="replace")
            return _rename_frontmatter(text, repo)
    raise InstallError(
        "no skill: repo has only .json/.yaml/.toml files but no .md skill body"
    )


def _rename_frontmatter(body: str, name: str) -> str:
    """Point the frontmatter's ``name:`` at the install name (everything else kept)."""
    if not body.startswith("---"):
        return body
    parts = body.split("---", 2)
    if len(parts) < 3:
        return body
    lines = [
        f"name: {name}" if ln.strip().startswith("name:") else ln
        for ln in parts[1].splitlines()
    ]
    if not any(ln.strip().startswith("name:") for ln in parts[1].splitlines()):
        lines.insert(0, f"name: {name}")
    return f"---" + "\n".join(lines) + "---" + parts[2]


def install_skill(
    target: str,
    *,
    db: str | Path,
    skills_dir: str | Path,
    _fetch=None,
) -> str:
    """Install one skill into ``skills_dir`` + the memory db; returns a status line.

    ``_fetch(url, max_bytes)`` is the injectable network seam (tests); the real
    default is :func:`_fetch_codeload`. Builtin names are installed from package
    data with no network access.
    """
    download = _fetch or _fetch_codeload
    kind, a, b = parse_target(target)
    skills_dir = Path(skills_dir)
    if kind == "builtin":
        name = a
        try:
            body = load_builtin(name)
        except KeyError as exc:
            raise InstallError(str(exc)) from exc
    else:
        owner, repo = a, b
        if not repo:
            raise InstallError(f"cannot parse github target: {target}")
        url = CODELOAD_URL.format(owner=owner, repo=repo)
        allowed = _extract_skill_files(download(url, MAX_BYTES * 4))
        name = repo
        body = _pick_skill_body(allowed, name)
    if not is_valid_skill_body(body):
        raise InstallError(
            f"refused: skill body for {name} lacks frontmatter name/description "
            "(add `name:` and `description:` to the YAML frontmatter)"
        )
    skills_dir.mkdir(parents=True, exist_ok=True)
    path = skills_dir / f"{name}.md"
    path.write_text(body, encoding="utf-8")
    store = SkillStore(db)
    try:
        store.upsert(
            SkillCandidate(
                name=name,
                title=name,
                tool_chain=[],
                occurrences=1,
                sessions=["install"],
                evidence=[f"installed via `skaldir skill install` ({target})"],
                body=body,
                status=STATUS_PROMOTED,
            )
        )
    finally:
        store.close()
    return f"installed: {name} -> {path}"


__all__ = ["InstallError", "MAX_BYTES", "parse_target", "install_skill"]
