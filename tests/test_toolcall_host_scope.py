"""The four corrections to the host-scope acceptance, and the basics that must hold.

Three of these correct my own earlier work, measured before the fix rather than assumed:

* "only redirects write" was wrong — ``cp a /etc/passwd``, ``sed -i /etc/hosts``,
  ``chmod 777 /etc/shadow`` never open a redirect and were still ALLOW;
* "the managed temporary area" was all of ``/tmp``, which holds other people's files,
  and a manipulated ``SKALDIR_TMPDIR=/etc`` turned ``/etc/passwd`` into "our own";
* ``/dev/stdout`` was lumped in with ``/dev/null``: one forwards, one discards.

The earlier acceptance file (`test_toolcall_host_scope_corrections.py`) keeps its own
cases; these are the corrected rules.
"""

from __future__ import annotations

import os

import pytest

from skaldir.toolcall import (
    DEVICE_DISCARD,
    DEVICE_FORWARD,
    ToolIntent,
    argument_write_targets,
    in_managed_tmp,
    judge_tool_call,
    redirect_target,
    writes_via_argument,
)

WS = "/home/willy/Agentic_Projects/Skaldir_Agent"
BLOCK = "BLOCK"
ALLOW = "ALLOW"


@pytest.fixture()
def managed(tmp_path, monkeypatch):
    """A real, owned directory to write into."""
    home = tmp_path / "runtime"
    (home / "tmp").mkdir(parents=True)
    monkeypatch.setenv("SKALDIR_HOME", str(home))
    monkeypatch.delenv("SKALDIR_TMPDIR", raising=False)
    return home / "tmp"


def _decide(command: str):
    return judge_tool_call(ToolIntent(tool="terminal", args={"command": command},
                                      workspace=WS, project_root=WS))


# --------------------------------------------------------------------------- #
# 1. a really controlled temporary area
# --------------------------------------------------------------------------- #

def test_plain_tmp_is_not_our_own_area():
    """The user's correction: "Ganz /tmp ist kein eigener verwalteter Arbeitsraum"."""
    assert in_managed_tmp("/tmp/lo.log") is False
    assert in_managed_tmp("/tmp/someoneelses/notes.txt") is False


def test_the_owned_scratch_area_is_ours(managed):
    assert in_managed_tmp(str((managed / "lo.log").resolve())) is True
    assert in_managed_tmp(str((managed / "deep/nested.log").resolve())) is True


def test_a_manipulated_tmpdir_cannot_widen_our_scope(monkeypatch):
    monkeypatch.setenv("SKALDIR_TMPDIR", "/etc")
    assert in_managed_tmp("/etc/passwd") is False, (
        "a manipulated SKALDIR_TMPDIR made a system file 'our own'"
    )


@pytest.mark.parametrize("hostile", ["/", "/tmp", "/etc", "/var", "/usr", "/home"])
def test_the_well_known_host_directories_are_never_ours(monkeypatch, hostile):
    monkeypatch.setenv("SKALDIR_TMPDIR", hostile)
    assert in_managed_tmp(os.path.join(hostile, "x.log")) is False


def test_a_symlink_inside_our_area_is_not_ours(managed):
    """/tmp/skaldir/escape -> outside.txt must not read as an owned target."""
    link = managed / "escape"
    target = managed.parent / "outside.txt"
    target.write_text("secret", encoding="utf-8")
    link.symlink_to(target)
    assert in_managed_tmp(str(link)) is False, "the symlink escaped undetected"
    assert in_managed_tmp(str(target)) is False


def test_a_write_through_an_escaping_symlink_is_blocked(managed):
    link = managed / "escape"
    target = managed.parent / "victim.txt"
    target.write_text("keep me", encoding="utf-8")
    link.symlink_to(target)
    d = _decide(f"echo overwritten > {link}")
    assert d.classification == BLOCK, d.reasons
    assert d.checks.get("symlink_escape_paths")


# --------------------------------------------------------------------------- #
# 2. writes through arguments — "only redirects write" was my wrong assumption
# --------------------------------------------------------------------------- #

def test_copy_into_a_foreign_target_is_blocked():
    d = _decide("cp /etc/passwd /tmp/stolen")
    assert d.classification == BLOCK, d.reasons
    assert "/tmp/stolen" in (d.checks.get("foreign_paths") or [])


def test_move_over_a_foreign_file_is_blocked():
    d = _decide("mv notes.txt /etc/passwd")
    assert d.classification == BLOCK
    assert "/etc/passwd" in (d.checks.get("foreign_paths") or [])


def test_in_place_edit_of_a_foreign_file_is_blocked():
    d = _decide("sed -i 's/a/b/' /etc/hosts")
    assert d.classification == BLOCK
    assert "/etc/hosts" in (d.checks.get("foreign_paths") or [])


def test_in_place_perl_is_blocked_too():
    d = _decide("perl -i -pe 's/a/b/' /etc/hosts")
    assert d.classification == BLOCK


def test_a_hard_link_into_a_system_file_is_blocked():
    d = _decide("ln -sf /etc/shadow /tmp/l")
    assert d.classification == BLOCK
    assert "/etc/shadow" in (d.checks.get("foreign_paths") or [])


def test_chmod_on_a_system_file_is_blocked():
    d = _decide("chmod 777 /etc/shadow")
    assert d.classification == BLOCK
    assert "/etc/shadow" in (d.checks.get("foreign_paths") or [])


def test_a_recursive_copy_is_blocked():
    d = _decide("cp -r /etc /tmp/copy")
    assert d.classification == BLOCK


def test_the_destination_is_the_target_not_the_source():
    targets = argument_write_targets("cp /usr/share/doc/readme notes.txt")
    # the destination is resolved against the caller's cwd, and the source stays out
    assert targets == [os.path.join(WS, "notes.txt")], targets
    assert not any("readme" in t for t in targets)


def test_read_only_commands_are_not_widened():
    """A read is not a write, and must not be mistaken for one."""
    for command in ("cat /etc/passwd", "grep -r foo /usr/share", "ls -la /etc",
                    "head -5 /etc/hosts", "tar tzf /tmp/a.tgz"):
        assert writes_via_argument(command) == [], (command, writes_via_argument(command))
        assert argument_write_targets(command) == [], command


def test_a_copy_inside_the_project_stays_allowed():
    d = _decide("cp a.txt b.txt")
    assert d.classification == ALLOW, d.reasons


# --------------------------------------------------------------------------- #
# 3. a forward is not a discard
# --------------------------------------------------------------------------- #

def test_dev_null_is_a_discard():
    assert redirect_target("cmd >/dev/null") == ["/dev/null"]
    assert "/dev/null" in DEVICE_DISCARD
    assert "/dev/null" not in DEVICE_FORWARD


def test_dev_stdout_is_a_forward():
    assert redirect_target("cmd >/dev/stdout") == ["/dev/stdout"]
    assert "/dev/stdout" in DEVICE_FORWARD
    assert "/dev/stdout" not in DEVICE_DISCARD


def test_dev_stderr_is_a_forward():
    assert "/dev/stderr" in DEVICE_FORWARD
    assert "/dev/stderr" not in DEVICE_DISCARD


@pytest.mark.parametrize("command", [
    "soffice --version >/dev/null",
    "soffice --version >/dev/stdout",
    "soffice --version 2>/dev/stderr",
    "soffice --version 2>/dev/null",
    "make 2>/dev/stdout",
])
def test_device_targets_never_trip_the_project_fence(command):
    d = _decide(command)
    assert d.classification == ALLOW, d.reasons
    assert d.checks.get("foreign_paths") == []


# --------------------------------------------------------------------------- #
# the earlier holes must stay closed
# --------------------------------------------------------------------------- #

def test_overwriting_a_foreign_system_file_stays_blocked():
    d = _decide("echo x > /etc/passwd")
    assert d.classification == BLOCK


def test_a_traversal_write_stays_blocked():
    d = judge_tool_call(ToolIntent(tool="write_file",
                                   args={"path": "../../elsewhere/secret.txt"},
                                   workspace=WS, project_root=WS))
    assert d.classification == BLOCK
    assert d.checks.get("traversal_paths") == ["../../elsewhere/secret.txt"]


def test_reading_outside_the_project_is_still_normal_work():
    for command in ("cat /var/log/Xorg.0.log | tail -20",
                    "soffice --calc /home/willy/AMD_latest_data.ods",
                    "xdpyinfo -display :0 | grep dimensions"):
        assert _decide(command).classification == ALLOW, command


def test_every_refusal_still_names_source_access_rule_and_next_step():
    d = _decide("cp /etc/passwd /tmp/stolen")
    text = d.render()
    for required in ("access affected:", "decided by: path classification",
                     "rule:", "next step:"):
        assert required in text, (required, text)
