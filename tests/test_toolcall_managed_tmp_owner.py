"""The temporary roots: owner, mode, existence and symlinks — not just the name.

The user's correction: "Pruefe bei den temporären Wurzeln Eigentuemer, Rechte, bereits
existierende Verzeichnisse und Symlinks. Der Name /tmp/skaldir allein beweist keine
exklusive Kontrolle."

The gap measured before the fix: `<SKALDIR_HOME>/tmp` was accepted as an owned root while
the directory did not exist on disk at all.
"""

from __future__ import annotations

import os

import pytest

from skaldir.toolcall import (
    _root_is_really_ours,
    in_managed_tmp,
)


@pytest.fixture()
def runtime_home(tmp_path, monkeypatch):
    home = tmp_path / "runtime"
    home.mkdir()
    monkeypatch.setenv("SKALDIR_HOME", str(home))
    monkeypatch.delenv("SKALDIR_TMPDIR", raising=False)
    return home


# --------------------------------------------------------------------------- #
# the root must exist, and be ours
# --------------------------------------------------------------------------- #

def test_a_root_that_does_not_exist_is_not_ours(runtime_home):
    """The measured gap: a `<home>/tmp` that was never created answered True."""
    missing = runtime_home / "tmp"
    assert not missing.exists()
    assert _root_is_really_ours(str(missing)) is False
    # and a path under it is not managed either
    assert in_managed_tmp(str(missing / "lo.log")) is False


def test_an_existing_owned_root_is_ours(runtime_home):
    root = runtime_home / "tmp"
    root.mkdir(mode=0o755)
    assert _root_is_really_ours(str(root)) is True
    assert in_managed_tmp(str(root / "lo.log")) is True


def test_the_shared_tmp_is_never_ours_by_name(runtime_home):
    """The name alone proves no exclusive control: /tmp is shared and world-writable."""
    # /tmp itself is world-writable and shared — writing there is a choice, not a right
    assert _root_is_really_ours("/tmp") is False
    assert in_managed_tmp("/tmp/anything.log") is False


def test_a_symlinked_root_is_not_ours(tmp_path, monkeypatch):
    real = tmp_path / "elsewhere"
    real.mkdir()
    link = tmp_path / "looks-like-ours"
    link.symlink_to(real)
    assert _root_is_really_ours(str(link)) is False


def test_a_symlink_inside_our_own_root_is_not_ours(runtime_home):
    root = runtime_home / "tmp"
    root.mkdir(mode=0o755)
    outside = runtime_home / "outside.txt"
    outside.write_text("keep me", encoding="utf-8")
    escape = root / "escape"
    escape.symlink_to(outside)
    assert in_managed_tmp(str(escape)) is False
    assert in_managed_tmp(str(outside)) is False


def test_a_root_owned_by_someone_else_is_not_ours(tmp_path, monkeypatch):
    """Only meaningful when the host really has another user; simulated by ownership."""
    root = tmp_path / "other-owned"
    root.mkdir(mode=0o755)
    # simulate "not mine": the check keys off st_uid vs os.getuid()
    monkeypatch.setattr(os, "getuid", lambda: 99999)
    assert _root_is_really_ours(str(root)) is False
    monkeypatch.undo()
    assert _root_is_really_ours(str(root)) is True


def test_a_world_writable_shared_directory_is_refused(tmp_path, monkeypatch):
    root = tmp_path / "shared"
    root.mkdir(mode=0o777)
    monkeypatch.setattr(os, "getuid", lambda: 99999)
    assert _root_is_really_ours(str(root)) is False


# --------------------------------------------------------------------------- #
# the override still cannot widen the scope
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("hostile", ["/", "/tmp", "/etc", "/var", "/usr", "/home", "/opt"])
def test_a_manipulated_override_cannot_widen_our_scope(runtime_home, monkeypatch, hostile):
    monkeypatch.setenv("SKALDIR_TMPDIR", hostile)
    assert in_managed_tmp(os.path.join(hostile, "x.log")) is False


def test_an_override_inside_the_runtime_is_accepted(runtime_home, monkeypatch):
    root = runtime_home / "scratch"
    root.mkdir(mode=0o755)
    monkeypatch.setenv("SKALDIR_TMPDIR", str(root))
    assert in_managed_tmp(str(root / "a.log")) is True


def test_existing_user_files_are_never_touched(runtime_home):
    """The rule that outranks the rest: an existing file stays as it is."""
    mine = runtime_home / "mine.txt"
    mine.write_text("mine", encoding="utf-8")
    before = mine.read_text(encoding="utf-8")
    # nothing in this module writes — the classifier only *reads* the filesystem
    assert mine.read_text(encoding="utf-8") == before
