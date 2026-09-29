"""`skill install <name-or-github>`: builtin name or GitHub tarball install."""

import tarfile
from pathlib import Path

import pytest

from skaldir.skill_install import InstallError, install_skill, parse_target

# ---------- target parsing ----------


def test_parse_target_github_https():
    assert parse_target("https://github.com/owner/repo") == ("github", "owner", "repo")


def test_parse_target_github_shorthand():
    assert parse_target("owner/repo") == ("github", "owner", "repo")


def test_parse_target_builtin_name():
    assert parse_target("write-test") == ("builtin", "write-test", None)


def test_parse_target_rejects_empty():
    with pytest.raises(InstallError):
        parse_target("")


# ---------- github install (offline, via monkeypatched fetcher) ----------


def _tar_bytes(files: dict[str, bytes]) -> bytes:
    """One tar.gz whose top dir is <repo>-<sha>, like codeload ships it."""
    import io

    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return out.getvalue()


def _fake_github(url_to_tar: dict[str, bytes]):
    def fetch(url: str, max_bytes: int):
        if url not in url_to_tar:
            raise InstallError("network down: cannot reach GitHub")
        return url_to_tar[url]

    return fetch


def test_install_from_github_writes_skill_md(tmp_path, monkeypatch):
    tar = _tar_bytes(
        {
            "repo-abc123/write-test.md": (
                b"---\nname: write-test\ndescription: write a focused test\n"
                b"---\n\n# Write test\n1. read the code\n"
            )
        }
    )
    monkeypatch.setattr("skaldir.skill_install._fetch_codeload", lambda u, m: tar)
    store = Path(tmp_path / "memory.db")
    skills_dir = Path(tmp_path / "skills")
    install_skill("someone/write-test", db=store, skills_dir=skills_dir)
    skill_md = skills_dir / "write-test.md"
    assert skill_md.is_file()
    assert skill_md.read_text().startswith("---")
    from skaldir.skills import SkillStore

    st = SkillStore(store)
    assert st.get("write-test").status == "promoted"
    st.close()


def test_install_refuses_disallowed_file_types(tmp_path, monkeypatch):
    tar = _tar_bytes(
        {
            "repo-abc123/skill.md": b"---\nname: x\ndescription: x skill here\n---\n# x\n",
            "repo-abc123/run.sh": b"#!/bin/sh\nrm -rf /\n",
        }
    )
    monkeypatch.setattr("skaldir.skill_install._fetch_codeload", lambda u, m: tar)
    with pytest.raises(InstallError, match="run.sh"):
        install_skill("a/skill", db=tmp_path / "db", skills_dir=tmp_path / "skills")


def test_install_refuses_when_no_skill_files(tmp_path, monkeypatch):
    tar = _tar_bytes({"repo-abc123/NOTES.json": b"{}\n"})
    monkeypatch.setattr("skaldir.skill_install._fetch_codeload", lambda u, m: tar)
    with pytest.raises(InstallError, match="no skill"):
        install_skill("a/repo", db=tmp_path / "db", skills_dir=tmp_path / "skills")


def test_install_refuses_oversize(tmp_path, monkeypatch):
    big = b"# pad\n" + b"x" * (300 * 1024)
    tar = _tar_bytes(
        {"repo-abc123/big.md": b"---\nname: big\ndescription: a big skill\n---\n" + big}
    )
    monkeypatch.setattr("skaldir.skill_install._fetch_codeload", lambda u, m: tar)
    with pytest.raises(InstallError, match="256KB"):
        install_skill("a/big", db=tmp_path / "db", skills_dir=tmp_path / "skills")


def test_install_builtin_by_name(tmp_path):
    from skaldir.skill_builtin import BUILTIN_SKILL_NAMES

    skills_dir = Path(tmp_path / "skills")
    install_skill(BUILTIN_SKILL_NAMES[0], db=tmp_path / "db", skills_dir=skills_dir)
    assert (skills_dir / f"{BUILTIN_SKILL_NAMES[0]}.md").is_file()


def test_install_network_down_is_honest(tmp_path, monkeypatch):
    def boom(url, max_bytes):
        raise InstallError("network down: cannot reach GitHub")

    monkeypatch.setattr("skaldir.skill_install._fetch_codeload", boom)
    with pytest.raises(InstallError, match="network down"):
        install_skill("a/repo", db=tmp_path / "db", skills_dir=tmp_path / "skills")


def test_install_unknown_builtin_refused(tmp_path):
    with pytest.raises(InstallError, match="unknown builtin skill"):
        install_skill("definitely-not-builtin", db=tmp_path / "db", skills_dir=tmp_path / "s")
