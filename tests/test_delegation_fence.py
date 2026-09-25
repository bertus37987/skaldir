"""THE DELEGATED FENCE — no read and no write may cross the workspace, whatever the spelling.

The reproduced defect (``evidence/18-boundary-review/test3_delegation.py``): a child's
``search_files`` walked the workspace with ``rglob("*")`` and *read* ``ws/filelink.txt`` — a
symlink to ``outside/secret.txt`` — returning the outside file's matching line, while
``read_file`` on the same name was refused. A fence that refuses a name but not the same name's
link is not a fence.

What this file pins, all of it with a synthetic outside marker in ``tmp_path`` (no real secret,
no network, no provider):

* ``search_files``/``grep``/``glob`` never return one byte of a file outside the workspace —
  symlinked file, symlinked directory, absolute path, ``..`` path, absolute and ``..`` glob
  patterns — and the outside marker never appears anywhere in the JSON a child receives;
* the same fence holds for every read spelling and for ``list_dir`` (which used to report the
  *outside* file's size through the link);
* ``fenced_read`` re-checks the opened descriptor, so a path that becomes a link after the walk
  is refused too (TOCTOU), and a link is refused at open time (``O_NOFOLLOW``);
* a ``worker``'s writes — core toolset *and* the atomic edit transaction engine — cannot escape
  through a symlink, ``..``, an absolute path or a symlinked directory, and the outside file is
  byte-identical afterwards;
* the declared check's read-only guarantee is **asked of the gate and reported**, never assumed
  from a tool name: a read-only role's result carries ``read_only_enforced`` / ``workspace_bind``
  and the write sentinel names every path its check changed.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from skaldir import gates
from skaldir import runtime_delegation as rd
from skaldir import subagent_roles as roles

#: A line of the outside file — the marker a leak would hand back. It is *also* used as the
#: ``old`` string of the escaping edit transactions below.
MARKER = "outside line one"
#: The second line of the outside file: neither may ever appear in a child's result.
MARKER_2 = "outside line two"
#: What a child greps for. Deliberately *not* the full line: a search result echoes the pattern
#: the child asked for, so the assertion below checks the outside *content*, not the request.
MARKER_PATTERN = "outside line"


# --------------------------------------------------------------------------- #
# fixtures                                                                      #
# --------------------------------------------------------------------------- #


def _workspace(tmp_path: Path) -> tuple[Path, Path]:
    """``(workspace, outside)`` — a real little workspace with a link out of it.

    ``outside/synthetic-marker.txt`` holds the marker, ``ws/filelink.txt`` links to it and
    ``ws/linkdir`` links to the whole outside directory: the two spellings the fence must refuse.
    """
    workspace = tmp_path / "ws"
    (workspace / "nested").mkdir(parents=True)
    (workspace / "inside.txt").write_text("inside line\n", encoding="utf-8")
    (workspace / "nested" / "deep.py").write_text("DEEP = 1\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "synthetic-marker.txt").write_text(f"{MARKER}\n{MARKER_2}\n", encoding="utf-8")
    os.symlink(outside / "synthetic-marker.txt", workspace / "filelink.txt")
    os.symlink(outside, workspace / "linkdir")
    return workspace, outside


def _blob(value: Any) -> str:
    return json.dumps(value, default=str)


def _check_project() -> str:
    """A ``.godsv/checks.json`` declaration — the check tool must never invent its own command."""
    return json.dumps({"pytest": {"argv": [sys.executable, "declared_check.py"]}})


# --------------------------------------------------------------------------- #
# the reproduced leak, closed                                                   #
# --------------------------------------------------------------------------- #


def test_search_files_never_returns_outside_content_through_a_symlinked_file(tmp_path: Path):
    """**The reproduction.** ``filelink.txt`` links to a file outside; the content stays out."""
    workspace, _outside = _workspace(tmp_path)
    handlers = rd.child_handlers(workspace, role="explore", state_dir=tmp_path / "state")

    result = handlers["search_files"](MARKER_PATTERN)

    assert result["count"] == 0, result
    assert result["matches"] == [], result
    assert MARKER not in _blob(result), "an outside line reached the child's search result"
    assert MARKER_2 not in _blob(result), "an outside line reached the child's search result"
    # ... and the same fence holds for the handler's other spellings
    for name in ("grep",):
        assert MARKER not in _blob(handlers[name](MARKER_PATTERN))
    assert MARKER not in _blob(handlers["glob"]("**/*"))
    assert MARKER not in _blob(handlers["glob"]("*.txt"))


def test_search_files_never_walks_a_symlinked_directory(tmp_path: Path):
    """``ws/linkdir`` is the outside directory: walking it would leak the whole tree."""
    workspace, _outside = _workspace(tmp_path)
    handlers = rd.child_handlers(workspace, role="explore", state_dir=tmp_path / "state")

    result = handlers["search_files"]("synthetic-marker")

    assert result["count"] == 0, result
    assert MARKER not in _blob(result)
    # a path argument that *is* the link is refused by the fence, not walked
    refused = handlers["search_files"]("synthetic-marker", path="linkdir")
    assert refused.get("error"), refused
    assert "leaves the workspace" in str(refused["error"]), refused


def test_search_files_names_the_links_it_refused(tmp_path: Path):
    """A refusal a child can read: the links are listed, so the answer is a fence not a nothing."""
    workspace, _outside = _workspace(tmp_path)
    handlers = rd.child_handlers(workspace, role="explore", state_dir=tmp_path / "state")

    refused = {
        item["path"]: item["reason"] for item in handlers["search_files"]("inside")["refused"]
    }

    assert refused.get("filelink.txt") == "symlink (not followed)"
    assert refused.get("linkdir") == "symlink (not followed)"
    # no absolute outside path is ever emitted, not even as a refusal's label
    assert str(tmp_path) not in _blob(handlers["search_files"]("inside")["refused"])
    assert "outside/synthetic-marker.txt" not in _blob(
        handlers["search_files"]("inside")["refused"]
    )


def test_search_files_still_finds_inside_content(tmp_path: Path):
    """The fence is not a blanket refusal: the workspace's own files are still searched."""
    workspace, _outside = _workspace(tmp_path)
    handlers = rd.child_handlers(workspace, role="explore", state_dir=tmp_path / "state")

    result = handlers["search_files"]("DEEP")

    assert result["count"] == 1, result
    assert result["matches"][0]["path"] == "nested/deep.py"
    assert "DEEP = 1" in result["matches"][0]["text"]


@pytest.mark.parametrize(
    "pattern",
    ["../outside/synthetic-marker.txt", "/etc/hostname", "..", os.path.join("..", "outside")],
)
def test_search_files_refuses_a_path_outside_the_workspace(tmp_path: Path, pattern: str):
    workspace, _outside = _workspace(tmp_path)
    handlers = rd.child_handlers(workspace, role="explore", state_dir=tmp_path / "state")

    result = handlers["search_files"](MARKER, path=pattern)

    assert "error" in result, result
    assert MARKER not in _blob(result)


@pytest.mark.parametrize(
    "pattern",
    ["../*", "/etc/*", "**/../../*", "../outside/*", "~/*", "/"],
)
def test_glob_refuses_a_pattern_outside_the_workspace(tmp_path: Path, pattern: str):
    workspace, _outside = _workspace(tmp_path)
    handlers = rd.child_handlers(workspace, role="explore", state_dir=tmp_path / "state")

    result = handlers["glob"](pattern)

    assert "error" in result, result
    assert "leaves the workspace" in str(result["error"])


def test_glob_never_returns_a_path_outside_and_still_matches_inside(tmp_path: Path):
    """Glob semantics survive the fence: ``**`` crosses directories, links and ``.`` do not leak."""
    workspace, _outside = _workspace(tmp_path)
    handlers = rd.child_handlers(workspace, role="explore", state_dir=tmp_path / "state")

    every = handlers["glob"]("**/*")
    assert every["count"] == 2, every
    assert sorted(every["matches"]) == ["inside.txt", "nested/deep.py"], every
    assert "filelink.txt" not in every["matches"], every
    assert "linkdir" not in every["matches"], every
    assert MARKER not in _blob(every) and str(tmp_path) not in _blob(every)

    nested = handlers["glob"]("**/*.py")
    assert nested["matches"] == ["nested/deep.py"], nested
    top = handlers["glob"]("*.txt")
    assert top["matches"] == ["inside.txt"], top
    hidden = handlers["glob"]("*")
    assert hidden["matches"] == ["inside.txt"], hidden


# --------------------------------------------------------------------------- #
# the whole read class                                                          #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("tool", ["read_file", "read_text", "read", "sha256_file"])
def test_every_read_spelling_refuses_the_link(tmp_path: Path, tool: str):
    """``read_file`` refused it before the fix; the fence must hold for every spelling."""
    workspace, _outside = _workspace(tmp_path)
    handlers = rd.child_handlers(workspace, role="explore", state_dir=tmp_path / "state")

    result = handlers[tool]("filelink.txt")

    assert MARKER not in _blob(result), (tool, result)
    assert result.get("error") or result.get("text") is None, (tool, result)
    # the same spelling still reads a real workspace file
    inside = handlers[tool]("inside.txt")
    if tool == "sha256_file":
        import hashlib

        assert inside["sha256"] == hashlib.sha256(b"inside line\n").hexdigest(), inside
    else:
        assert "inside line" in _blob(inside), inside


def test_list_dir_marks_a_link_and_does_not_report_the_outside_size(tmp_path: Path):
    """The listing used to ``stat()`` through the link, handing back the outside file's size."""
    workspace, outside = _workspace(tmp_path)
    handlers = rd.child_handlers(workspace, role="explore", state_dir=tmp_path / "state")

    listing = handlers["list_dir"](".")
    entries = {entry["name"]: entry for entry in listing["entries"]}

    assert entries["filelink.txt"]["type"] == "link"
    assert entries["filelink.txt"]["size"] is None
    assert entries["filelink.txt"]["target_outside"] is True
    assert entries["linkdir"]["target_outside"] is True
    assert "synthetic-marker.txt" not in _blob(listing)
    assert entries["inside.txt"]["type"] == "file"
    # a link that stays inside the workspace is reported as a link too, and read normally
    os.symlink(workspace / "inside.txt", workspace / "inner_link.txt")
    entries = {entry["name"]: entry for entry in handlers["list_dir"](".")["entries"]}
    assert entries["inner_link.txt"]["type"] == "link"
    assert entries["inner_link.txt"]["target_outside"] is False
    assert "inside line" in _blob(handlers["read_file"]("inner_link.txt"))
    assert (outside / "synthetic-marker.txt").read_text(encoding="utf-8").startswith(MARKER)


def test_fenced_read_refuses_a_link_at_open_time_and_by_descriptor(tmp_path: Path):
    """Two moments, one fence: ``O_NOFOLLOW`` for the last component, the descriptor for
    the rest."""
    workspace, outside = _workspace(tmp_path)

    text, problem = rd.fenced_read(workspace / "filelink.txt", workspace)
    assert text is None and problem, (text, problem)
    assert MARKER not in problem

    # a link swapped in *after* the walk: the name is inside, the descriptor's real path is not
    swapped = workspace / "nested" / "swapped.txt"
    swapped.write_text("harmless\n", encoding="utf-8")
    assert rd.fenced_read(swapped, workspace)[0] == "harmless\n"
    swapped.unlink()
    os.symlink(outside / "synthetic-marker.txt", swapped)
    text, problem = rd.fenced_read(swapped, workspace)
    assert text is None, "a link swapped in after the walk was still read"
    assert MARKER not in problem
    # the walk itself never yields that name either
    files, skipped, _truncated = rd.fenced_files(workspace, workspace)
    assert swapped not in files
    assert any(item["path"] == "nested/swapped.txt" for item in skipped), skipped


def test_fenced_read_refuses_a_descriptor_whose_real_path_left_the_workspace(tmp_path: Path):
    """``linkdir/file``: the *parent* is a link, so only the descriptor's own path can refuse it."""
    workspace, outside = _workspace(tmp_path)

    text, problem = rd.fenced_read(workspace / "linkdir" / "synthetic-marker.txt", workspace)

    assert text is None, "an outside file was read through a symlinked directory"
    assert "outside the workspace" in problem, problem
    # ... while the same call with the real (inside) path reads normally
    assert rd.fenced_read(workspace / "inside.txt", workspace)[0] == "inside line\n"
    assert (outside / "synthetic-marker.txt").read_text(encoding="utf-8").startswith(MARKER)


# --------------------------------------------------------------------------- #
# the write side: a worker cannot escape either                                 #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("tool", ["write_file", "write_text", "patch"])
def test_a_worker_cannot_write_outside_the_workspace(tmp_path: Path, tool: str):
    """Every spelling of "outside" is refused by the core toolset, and the file is untouched."""
    workspace, outside = _workspace(tmp_path)
    victim = outside / "synthetic-marker.txt"
    before = victim.read_text(encoding="utf-8")
    handlers = rd.child_handlers(workspace, role="worker", state_dir=tmp_path / "state")

    calls = {
        "symlinked file": ("filelink.txt", "PWNED\n", "PWNED"),
        "symlinked dir": ("linkdir/new.txt", "PWNED\n", "PWNED"),
        "parent path": ("../outside/new.txt", "PWNED\n", "PWNED"),
        "absolute path": (str(outside / "new.txt"), "PWNED\n", "PWNED"),
        "absolute victim": (str(victim), "PWNED\n", "PWNED"),
    }
    for label, (path, *text) in calls.items():
        if tool == "patch":
            result = handlers[tool](path, before.strip(), "PWNED")
        else:
            result = handlers[tool](path, text[0])
        assert result.get("ok") is False, (label, result)
        assert str(result.get("error") or "").strip(), (label, result)

    assert victim.read_text(encoding="utf-8") == before, "a worker rewrote an outside file"
    assert sorted(item.name for item in outside.iterdir()) == ["synthetic-marker.txt"]
    assert not (workspace / "linkdir" / "new.txt").exists()
    # ... and the worker's write inside the workspace still works
    written = handlers["write_file"]("nested/ok.txt", "fine\n")
    assert written["ok"] is True and written["output"]["verified"] is True
    assert (workspace / "nested" / "ok.txt").read_text(encoding="utf-8") == "fine\n"


def test_edit_transactions_cannot_escape_the_workspace(tmp_path: Path):
    """The atomic engine is a second write seam: it refuses the same four spellings."""
    workspace, outside = _workspace(tmp_path)
    victim = outside / "synthetic-marker.txt"
    before = victim.read_text(encoding="utf-8")
    handlers = rd.child_handlers(workspace, role="worker", state_dir=tmp_path / "state")

    def transaction(path: str) -> list[dict[str, Any]]:
        return [{"path": path, "edits": [{"old": MARKER, "new": "PWNED"}]}]

    for label, path in (
        ("symlinked file", "filelink.txt"),
        ("symlinked dir", "linkdir/synthetic-marker.txt"),
        ("parent path", "../outside/synthetic-marker.txt"),
        ("absolute path", str(victim)),
    ):
        result = handlers["edit_apply"]("pwn", transaction(path), {"kind": "none"})
        assert result["ok"] is False, (label, result)
        assert result["applied"] is False, (label, result)
        assert result["refused"], (label, result)

    assert victim.read_text(encoding="utf-8") == before, (
        "an edit transaction rewrote an outside file"
    )
    assert sorted(item.name for item in outside.iterdir()) == ["synthetic-marker.txt"]


def test_only_a_writing_role_holds_write_handlers_and_the_scope_is_the_workspace(tmp_path: Path):
    """The tables agree, and the scope a worker gets is the run's workspace path, not the cwd."""
    workspace, _outside = _workspace(tmp_path)
    writer = {"write_file", "write_text", "patch", "edit_apply"}
    for role in rd.GRANTABLE_ROLES:
        handlers = set(rd.child_handlers(workspace, role=role, state_dir=tmp_path / "state"))
        assert handlers <= set(roles.tools_for(role)), (
            role,
            sorted(handlers - set(roles.tools_for(role))),
        )
        assert (handlers & writer) == (
            writer if (set(roles.tools_for(role)) & writer) else set()
        ), role
    assert "run_tests" in rd.child_handlers(workspace, role="verifier", state_dir=tmp_path / "s")
    assert "run_tests" not in rd.child_handlers(workspace, role="explore", state_dir=tmp_path / "s")


# --------------------------------------------------------------------------- #
# the read-only guarantee of a read-only child's declared check                 #
# --------------------------------------------------------------------------- #


def _project(tmp_path: Path, script: str) -> Path:
    """A workspace whose declared check is a real script — the declaration's argv, in a file."""
    workspace = tmp_path / "project"
    (workspace / ".godsv").mkdir(parents=True)
    (workspace / "declared_check.py").write_text(script, encoding="utf-8")
    (workspace / ".godsv" / "checks.json").write_text(_check_project(), encoding="utf-8")
    (workspace / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    return workspace


def test_the_gate_is_asked_about_the_read_only_bind_not_assumed():
    """The shared gate today binds the workdir ``--bind``; the answer must say so."""
    capable, detail = rd._gate_read_only_support()
    assert isinstance(capable, bool)
    assert detail
    if capable:  # the gate patch landed: the snippet in the docstring is in place
        assert "--ro-bind" in detail
    else:
        assert "read-write" in detail
        # the refusal names the three signatures the snippet has to touch
        assert "run_declared_criterion" in detail and "gates._run" in detail


def test_a_read_only_childs_test_result_reports_the_bind_it_really_got(tmp_path: Path, monkeypatch):
    """``read_only_enforced``/``workspace_bind`` are the gate's answer, per run."""
    workspace = _project(tmp_path, "print('ok')")
    seen: dict[str, Any] = {}

    def judge(criterion, *, workspace, timeout, label="", **kwargs):  # noqa: ANN001, ANN003
        seen.update(kwargs)
        return "passed", 0, "ok", "", "stub"

    from skaldir import verify_service

    monkeypatch.setattr(verify_service, "run_declared_criterion", judge)
    monkeypatch.setattr(
        rd, "_gate_read_only_support", lambda: (False, "the gate binds it read-write")
    )
    handlers = rd.child_handlers(workspace, role="verifier", state_dir=tmp_path / "state")
    result = handlers["run_tests"]()

    assert seen == {}, "a read-only bind was requested from a gate that cannot build it"
    assert result["read_only_requested"] is True
    assert result["read_only_enforced"] is False
    assert result["workspace_bind"] == rd.READ_WRITE_BIND
    assert result["workspace_measured"] is True
    assert result["gate"]["read_only_capable"] is False
    assert "read-write" in result["read_only_detail"]

    monkeypatch.setattr(
        rd, "_gate_read_only_support", lambda: (True, "the gate can bind read-only")
    )
    handlers = rd.child_handlers(workspace, role="verifier", state_dir=tmp_path / "state")
    result = handlers["run_tests"]()

    assert seen == {"read_only_workspace": True}, seen
    assert result["read_only_enforced"] is True
    assert result["workspace_bind"] == rd.READ_ONLY_BIND


def test_a_writing_role_never_asks_for_the_read_only_bind(tmp_path: Path, monkeypatch):
    """A worker's check really writes (the workspace fixture proves it): it must stay read-write."""
    workspace = _project(tmp_path, "print('ok')")
    seen: dict[str, Any] = {}

    def judge(criterion, *, workspace, timeout, label="", **kwargs):  # noqa: ANN001, ANN003
        seen.update(kwargs)
        return "passed", 0, "ok", "", "stub"

    from skaldir import verify_service

    monkeypatch.setattr(verify_service, "run_declared_criterion", judge)
    monkeypatch.setattr(rd, "_gate_read_only_support", lambda: (True, "capable"))
    handlers = rd.child_handlers(workspace, role="worker", state_dir=tmp_path / "state")
    result = handlers["run_tests"]()

    assert seen == {}, seen
    assert result["read_only_requested"] is False
    assert result["workspace_measured"] is False
    assert result["workspace_changes"] == []
    assert result["independent"] is None


def test_the_write_sentinel_names_what_a_read_only_check_changed(tmp_path: Path, monkeypatch):
    """When the bind cannot be read-only, the paths the check touched are *named*, not hidden."""
    workspace = _project(tmp_path, "print('ok')")

    def judge(criterion, *, workspace, timeout, label="", **kwargs):  # noqa: ANN001, ANN003
        Path(workspace, "leaked-by-check.txt").write_text("the check wrote it\n", encoding="utf-8")
        Path(workspace, "module.py").write_text("VALUE = 2\n", encoding="utf-8")
        return "passed", 0, "ok", "", "stub"

    from skaldir import verify_service

    monkeypatch.setattr(verify_service, "run_declared_criterion", judge)
    monkeypatch.setattr(rd, "_gate_read_only_support", lambda: (False, "read-write today"))
    handlers = rd.child_handlers(workspace, role="verifier", state_dir=tmp_path / "state")

    result = handlers["run_tests"]()

    changes = {item["path"]: item["change"] for item in result["workspace_changes"]}
    assert changes == {"leaked-by-check.txt": "created", "module.py": "modified"}, result
    assert result["workspace_changed"] is True
    assert result["independent"] is False, "a check that changed a file is not an independent check"
    assert result["workspace_changes_count"] == 2
    # a clean run says so, and is not made to look like a mutating one
    monkeypatch.setattr(
        verify_service, "run_declared_criterion",
        lambda *a, **k: ("passed", 0, "ok", "", "stub"),
    )
    handlers = rd.child_handlers(workspace, role="verifier", state_dir=tmp_path / "state")
    clean = handlers["run_tests"]()
    assert clean["workspace_changes"] == [] and clean["independent"] is True


def test_a_gate_that_accepts_the_flag_but_does_not_apply_it_is_caught(
    tmp_path: Path, monkeypatch
):
    """The claim and the measurement are two facts: when they disagree, the result says so.

    A gate that takes ``read_only_workspace`` and ignores it would let a read-only child's check
    write while ``workspace_bind`` said ``read-only``. The sentinel is what catches that, and the
    disagreement is reported as a gate bug rather than smoothed over.
    """
    workspace = _project(tmp_path, "print('ok')")

    def lying_judge(criterion, *, workspace, timeout, label="", read_only_workspace=False):
        Path(workspace, "written-anyway.txt").write_text(
            "the gate did not apply it\n", encoding="utf-8"
        )
        return "passed", 0, "ok", "", "stub"

    from skaldir import verify_service

    monkeypatch.setattr(verify_service, "run_declared_criterion", lying_judge)
    monkeypatch.setattr(rd, "_gate_read_only_support", lambda: (True, "claims capable"))
    handlers = rd.child_handlers(workspace, role="verifier", state_dir=tmp_path / "state")

    result = handlers["run_tests"]()

    assert result["read_only_enforced"] is True  # the gate's answer
    assert {"path": "written-anyway.txt", "change": "created"} in result["workspace_changes"]
    assert "WARNING" in result["read_only_detail"], result
    assert result["independent"] is False


def test_a_write_refused_by_the_read_only_bind_is_not_reported_as_a_red_suite(
    tmp_path: Path, monkeypatch
):
    """``EROFS`` is the fence, not the project's suite: the reason must name the fence."""
    workspace = _project(tmp_path, "print('ok')")

    def judge(criterion, *, workspace, timeout, label="", **kwargs):  # noqa: ANN001, ANN003
        # what pytest really prints when a check tries to write a read-only workspace
        detail = (
            "PermissionError: [Errno 30] Read-only file system: "
            f"'{workspace}/declared_check.py'"
        )
        return "failed", 1, detail, "exit 1, expected 0", "stub"

    from skaldir import verify_service

    monkeypatch.setattr(verify_service, "run_declared_criterion", judge)
    monkeypatch.setattr(
        rd, "_gate_read_only_support", lambda: (True, "the gate can bind read-only")
    )
    handlers = rd.child_handlers(workspace, role="verifier", state_dir=tmp_path / "state")

    result = handlers["run_tests"]()

    assert result["read_only_blocked_write"] is True, result
    assert "read-only workspace bind" in result["reason"], result


def test_a_gate_that_advertises_a_bind_it_refuses_is_not_a_silent_downgrade(
    tmp_path: Path, monkeypatch
):
    """A drifted signature is ``not-certifiable``: never a run that claims to be read-only."""
    workspace = _project(tmp_path, "print('ok')")

    def judge(criterion, *, workspace, timeout, label="", read_only_workspace=False):  # noqa: ANN001
        raise TypeError("run_declared_criterion() got an unexpected keyword argument")

    from skaldir import verify_service

    monkeypatch.setattr(verify_service, "run_declared_criterion", judge)
    monkeypatch.setattr(rd, "_gate_read_only_support", lambda: (True, "claims capable"))
    handlers = rd.child_handlers(workspace, role="verifier", state_dir=tmp_path / "state")

    result = handlers["run_tests"]()

    assert result["status"] == "not-certifiable" and result["ok"] is False, result
    assert result["read_only_enforced"] is False
    assert result["workspace_bind"] == rd.READ_WRITE_BIND
    assert "not run" in result["reason"], result


def test_the_payload_states_the_bind_a_read_only_childs_check_will_get(tmp_path: Path):
    """The model reads the bind up front — in the payload's own limits, not only per call."""
    from skaldir import subagents

    workspace = _project(tmp_path, "print('ok')")
    family = rd.spawn_family(
        tasks=["verify it"],
        role="verifier",
        provider=subagents.StubProvider(latency_s=0.0, default_turns=1),
        workspace=workspace,
        state_dir=tmp_path / "state",
        session="s1",
    )

    limits = family.payload()["limits"]

    assert limits["may_write"] is False and limits["may_test"] is True
    assert limits["tests_workspace"] in (rd.READ_ONLY_BIND, rd.READ_WRITE_BIND)
    capable, _detail = rd._gate_read_only_support()
    assert limits["read_only_gate"]["capable"] is capable
    assert limits["tests_workspace"] == (rd.READ_ONLY_BIND if capable else rd.READ_WRITE_BIND)
    assert limits["write_scope"] == str(workspace)
    assert "never follow a symlink" in limits["workspace_fence"]


# --------------------------------------------------------------------------- #
# live: the real sandbox, the real declared check                               #
# --------------------------------------------------------------------------- #


def _requires_sandbox() -> None:
    if not gates._sandbox_available():
        pytest.skip("no bwrap sandbox on this host — the declared check could not be executed")


def test_live_a_read_only_childs_declared_check_and_the_workspace_agree(tmp_path: Path):
    """**Live (real sandbox).** The report must match the filesystem, whichever bind it got.

    A verifier's declared check here *writes* a file into the workspace. Two outcomes are
    possible and both are asserted as facts, so the test flips with the gate patch instead of
    pretending: with the workspace bound read-only the write is refused (``EROFS``, named as a
    fence) and the file never appears; while the gate binds it read-write the write lands and the
    sentinel names it, and the result says ``workspace_bind: read-write`` rather than claiming a
    read-only verifier.
    """
    _requires_sandbox()
    workspace = _project(
        tmp_path,
        "import pathlib\n"
        "pathlib.Path('written-by-check.txt').write_text('the check wrote\\n')\n"
        "print('ok')\n",
    )
    handlers = rd.child_handlers(workspace, role="verifier", state_dir=tmp_path / "state")

    result = handlers["run_tests"]()
    wrote = workspace / "written-by-check.txt"

    if result["read_only_enforced"]:
        assert result["workspace_bind"] == rd.READ_ONLY_BIND
        assert not wrote.exists(), "a read-only bound check wrote the workspace"
        assert result["workspace_changes"] == []
        assert result["ok"] is False and result["read_only_blocked_write"] is True, result
    else:
        assert result["workspace_bind"] == rd.READ_WRITE_BIND
        assert wrote.exists(), "the check ran but its write did not land — the fixture is wrong"
        assert {"path": "written-by-check.txt", "change": "created"} in result["workspace_changes"]
        assert result["independent"] is False
    assert (workspace / ".godsv" / "checks.json").exists()


def test_live_a_worker_declared_check_keeps_its_writable_workspace(tmp_path: Path):
    """**Live.** A worker's check really runs in the workspace: its writes must keep landing."""
    _requires_sandbox()
    workspace = _project(
        tmp_path,
        "import pathlib\n"
        "pathlib.Path('worker-check.txt').write_text('the check wrote\\n')\n"
        "print('ok')\n",
    )
    handlers = rd.child_handlers(workspace, role="worker", state_dir=tmp_path / "state")

    result = handlers["run_tests"]()

    assert result["status"] == "passed", result
    assert (workspace / "worker-check.txt").read_text(encoding="utf-8") == "the check wrote\n"
    assert result["read_only_enforced"] is False
    assert result["workspace_measured"] is False
