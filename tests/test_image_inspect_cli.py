"""``skaldir image …`` — the CLI envelope of the shell-free inspector.

Two things are pinned here:

1. the standalone group (``image inspect`` / ``image layers``) with its exit-code
   mapping — 0 for a produced classification (even an all-``impossible`` one, which
   *is* the answer), 3 blocked, 5 not found, 1 usage;
2. **the wiring snippet against the real parser** — the CLI owner's three lines are
   applied to ``cli.build_parser()`` in a scratch parser and the product entry point
   ``cli.main(["image", …])`` is called for real, so the snippet is proven instead of
   claimed. The snippet is only applied when the group is not already wired, so this
   file stays green before and after the CLI owner's edit.
"""

from __future__ import annotations

import argparse
import json

import pytest

from skaldir import cli, cli_contract
from skaldir import image_inspect as II
from skaldir import image_inspect_cli as C

#: a canned report, shaped exactly like ``image_inspect.inspect_image`` returns
REPORT = {
    "schema_version": 1,
    "ok": True,
    "blocked": "",
    "reason": "",
    "image": "hello-world",
    "image_digest": "sha256:" + "ab" * 32,
    "method": "shell-free",
    "container_started": True,
    "metadata": {"architecture": "amd64", "os": "linux", "size_bytes": 15077,
                 "entrypoint": [], "cmd": ["/hello"], "exposed_ports": [], "labels": {},
                 "user": "", "env": [], "volumes": [], "working_dir": "/"},
    "filesystem": {"layer_count": 1, "layer_source": "manifest.json", "entries_scanned": 1,
                   "executables": ["/hello"], "libraries": [], "shells": [],
                   "os_release": {}, "warnings": [], "truncated": False,
                   "layers": [{"path": "blobs/sha256/x", "status": "ok", "entries": 1,
                               "executables": 1, "libraries": 0}]},
    "shell": {"available": False, "paths": [], "note": "no shell in the image filesystem"},
    "http": {"exposed_ports": [], "labels": {}, "volumes": [], "user": ""},
    "candidates": [{"id": "cli:/hello", "kind": "cli", "path": "/hello", "basename": "hello",
                    "source": "cmd", "evidence": [], "probe": {"attempted": True, "ran": True,
                                                               "exit_code": 0,
                                                               "argv": ["/hello",
                                                                        "--help"]},
                    "classification": "inspectable", "reason": "ran directly"}],
    "classification": {"verdict": "shell-free",
                       "counts": {"inspectable": 1, "needs-shell": 0, "impossible": 0},
                       "shell_available": False, "candidates": {}, "note": ""},
    "probes": [{"probe": "exec:/hello", "attempted": True, "ran": True, "exit_code": 0,
                "argv": ["/hello", "--help"]}],
    "adapter": {"handoff": "refused", "reason": "AdapterRefusal: no verb"},
    "containers": {"before": {}, "after": {}, "sweep": {}},
    "inspected_at": 1.0,
    "duration_ms": 12,
}


def _subparsers(parser: argparse.ArgumentParser):
    return [action for action in parser._actions
            if isinstance(action, argparse._SubParsersAction)][0]


@pytest.fixture
def canned(monkeypatch):
    """Replace the real inspector with a canned report and record the calls."""
    calls: list[dict] = []

    def fake_inspect(**kwargs):
        calls.append(kwargs)
        return dict(REPORT)

    monkeypatch.setattr(II, "inspect_image", fake_inspect)
    return calls


# --------------------------------------------------------------------------- #
# the standalone group                                                          #
# --------------------------------------------------------------------------- #


def test_the_standalone_parser_exposes_both_verbs():
    parser = C.build_parser()
    leaves = dict((path, sub) for path, sub in cli_contract.leaf_commands(parser))
    assert set(leaves) == {"inspect", "layers"}
    assert leaves["inspect"].get_default("command_path") == "image.inspect"
    assert leaves["layers"].get_default("command_path") == "image.layers"
    names = {arg.dest for arg in leaves["inspect"]._actions}
    assert {"image", "digest", "capability", "no_exec", "exec_probes", "compare_shell"} <= names


def test_register_cli_returns_the_image_parser_and_names_it_image():
    parent = argparse.ArgumentParser(prog="skaldir")
    sub = parent.add_subparsers(dest="command")
    parser = C.register_cli(sub)
    assert "image" in sub.choices
    assert parser is sub.choices["image"]


def test_inspect_prints_the_report_and_exits_zero(canned, capsys):
    code = C.main(["inspect", "--image", "hello-world", "--json"])
    assert code == C.EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["method"] == "shell-free"
    assert payload["classification"]["verdict"] == "shell-free"
    assert canned[0]["image"] == "hello-world"
    assert canned[0]["probe_exec"] is True


def test_inspect_maps_a_missing_image_to_exit_five(monkeypatch):
    monkeypatch.setattr(II, "inspect_image", lambda **kw: {
        "ok": False, "blocked": "image_not_found", "image": kw.get("image"),
        "reason": "the image 'nope' is not local: No such image: nope:latest",
    })
    assert C.main(["inspect", "--image", "nope", "--json"]) == C.EXIT_NOT_FOUND


def test_inspect_maps_an_unreachable_daemon_to_blocked(monkeypatch):
    monkeypatch.setattr(II, "inspect_image", lambda **kw: {
        "ok": False, "blocked": "docker_unreachable", "image": kw.get("image"),
        "reason": "`docker image inspect` did not answer: Cannot connect to the Docker daemon",
    })
    assert C.main(["inspect", "--image", "alpine:latest", "--json"]) == C.EXIT_BLOCKED


def test_an_all_impossible_classification_still_exits_zero(monkeypatch, capsys):
    """The classification IS the answer — refusing to report it is the old bug."""
    report = dict(REPORT)
    report["classification"] = {"verdict": "impossible",
                                "counts": {"inspectable": 0, "needs-shell": 0, "impossible": 3},
                                "shell_available": False, "candidates": {}, "note": ""}
    report["adapter"] = {"handoff": "refused", "reason": "AdapterRefusal: no verb"}
    monkeypatch.setattr(II, "inspect_image", lambda **kw: dict(report))
    code = C.main(["inspect", "--image", "distroless/base", "--json"])
    assert code == C.EXIT_OK
    assert json.loads(capsys.readouterr().out)["classification"]["verdict"] == "impossible"


def test_a_missing_image_argument_is_a_usage_error_not_a_crash():
    assert C.main(["inspect", "--json"]) == C.EXIT_USAGE


def test_no_exec_is_forwarded_as_probe_exec_false(canned):
    C.main(["inspect", "--image", "hello-world", "--no-exec", "--json"])
    assert canned[0]["probe_exec"] is False
    assert canned[0]["capability"] == ""
    assert canned[0]["exec_probe_limit"] == II.DEFAULT_EXEC_PROBES


def test_the_capability_and_limits_reach_the_inspector(canned):
    C.main(["inspect", "--image", "alpine:latest", "--capability", "hash a string",
            "--name", "hasher", "--exec-probes", "5", "--exec-timeout", "3.5",
            "--candidates", "7", "--max-save-mb", "8", "--compare-shell", "--json"])
    kwargs = canned[0]
    assert kwargs["capability"] == "hash a string" and kwargs["name"] == "hasher"
    assert kwargs["exec_probe_limit"] == 5 and kwargs["exec_timeout"] == 3.5
    assert kwargs["candidate_limit"] == 7
    assert kwargs["max_save_bytes"] == 8 * 1024 * 1024
    assert kwargs["compare_shell"] is True


def test_layers_is_container_free_by_construction(monkeypatch, capsys):
    payload = {"ok": True, "image": "alpine:latest", "image_digest": "sha256:" + "cd" * 32,
               "method": "shell-free", "container_started": False, "metadata": {},
               "shell": {"available": True, "note": "a shell is present"},
               "http": {}, "candidates": [],
               "classification": {"verdict": "shell-free", "counts": {}},
               "filesystem": {"layer_count": 1, "layer_source": "manifest.json",
                              "entries_scanned": 3, "executables": ["/bin/sh"],
                              "executables_total": 1, "libraries": [], "libraries_total": 0,
                              "layers": [{"path": "blobs/x", "status": "ok", "entries": 3,
                                          "executables": 1, "libraries": 0}],
                              "shells": ["/bin/sh"], "os_release": {"PRETTY_NAME": "Alpine"},
                              "directories": ["bin"], "warnings": [], "truncated": False}}
    seen: list[dict] = []

    def fake_layers(**kwargs):
        seen.append(kwargs)
        return dict(payload)

    monkeypatch.setattr(II, "layers_only", fake_layers)
    code = C.main(["layers", "--image", "alpine:latest", "--limit", "4", "--json"])
    assert code == C.EXIT_OK
    assert seen[0]["excerpt_limit"] == 4
    out = json.loads(capsys.readouterr().out)
    assert out["container_started"] is False
    assert out["filesystem"]["executables"] == ["/bin/sh"]


def test_layers_reports_a_missing_image_as_not_found(monkeypatch):
    monkeypatch.setattr(II, "layers_only", lambda **kw: {
        "ok": False, "blocked": "image_not_found", "image": kw.get("image"),
        "reason": "the image 'nope' is not local",
    })
    assert C.main(["layers", "--image", "nope", "--json"]) == C.EXIT_NOT_FOUND


def test_the_text_rendering_is_human_and_says_whether_a_shell_exists(monkeypatch, capsys):
    monkeypatch.setattr(II, "inspect_image", lambda **kw: dict(REPORT))
    code = C.main(["inspect", "--image", "hello-world", "--text"])
    assert code == C.EXIT_OK
    text = capsys.readouterr().out
    assert "shell: NO" in text and "classification: shell-free" in text


# --------------------------------------------------------------------------- #
# the CLI-owner snippet, proven against the REAL parser                         #
# --------------------------------------------------------------------------- #


def _wire_the_snippet_into_the_real_parser() -> argparse.ArgumentParser:
    """Apply the CLI owner's three lines to a real parser (idempotent)."""
    parser = cli.build_parser()
    sub = _subparsers(parser)
    if "image" not in sub.choices:  # never wire twice ("conflicting subparser")
        C.register_cli(sub)
    return parser


def test_the_snippet_registers_the_two_paths_on_the_real_parser():
    parser = _wire_the_snippet_into_the_real_parser()
    leaves = [path for path, _sub in cli_contract.leaf_commands(parser)]
    assert "image.inspect" in leaves
    assert "image.layers" in leaves
    inventory = cli_contract.dump(parser)
    dumped = {item["command"]: item for item in inventory["commands"]}
    assert dumped["image.inspect"]["has_json_flag"] is True
    assert dumped["image.layers"]["has_json_flag"] is True
    # the contract entries are the CLI owner's job — until they exist the dump says so
    unregistered = set(inventory["unregistered"])
    assert {"image.inspect", "image.layers"} <= (
        unregistered | set(cli_contract.COMMANDS)
    )


def test_the_product_entry_point_runs_the_wired_command_end_to_end(monkeypatch, capsys):
    """``cli.main(["image", "inspect", …])`` — the real parse + envelope + handler."""
    parser = _wire_the_snippet_into_the_real_parser()
    assert "image" in _subparsers(parser).choices
    monkeypatch.setattr(II, "inspect_image", lambda **kw: dict(REPORT))
    monkeypatch.setattr(cli, "build_parser", lambda: parser)

    code = cli.main(["image", "inspect", "--image", "hello-world", "--json"])
    captured = capsys.readouterr()
    envelope = json.loads(captured.out)
    assert code == 0
    assert envelope["schema_version"]
    assert envelope["command"] == "image.inspect"
    assert envelope["ok"] is True
    assert envelope["exit_code"] == 0
    assert envelope["data"]["classification"]["verdict"] == "shell-free"
    # "no success without evidence": the envelope must carry what the claim stands on
    refs = [item["ref"] for item in envelope["evidence"]]
    assert any("docker image inspect" in ref for ref in refs)
    assert any("docker save" in ref for ref in refs)
    assert any("docker run" in ref for ref in refs)


def test_the_product_entry_point_warns_about_an_incomplete_sweep(monkeypatch, capsys):
    """A teardown that failed is surfaced as a warning, not swallowed."""
    parser = _wire_the_snippet_into_the_real_parser()
    report = dict(REPORT)
    report["containers"] = {
        "before": {}, "after": {},
        "sweep": {"swept": False, "new": ["abc123"], "removed": [], "remaining": ["abc123"],
                  "failure_class": "cleanup_partial",
                  "detail": "abc123: rm: removal of container is already in progress"},
    }
    monkeypatch.setattr(II, "inspect_image", lambda **kw: report)
    monkeypatch.setattr(cli, "build_parser", lambda: parser)
    code = cli.main(["image", "inspect", "--image", "alpine:latest", "--json"])
    envelope = json.loads(capsys.readouterr().out)
    assert code == 0  # the inspection itself succeeded; the teardown is the caveat
    assert any("cleanup_partial" in str(item) for item in envelope["warnings"])


def test_starting_no_container_needs_no_sweep_warning(monkeypatch, capsys):
    parser = _wire_the_snippet_into_the_real_parser()
    monkeypatch.setattr(II, "inspect_image", lambda **kw: dict(REPORT))
    monkeypatch.setattr(cli, "build_parser", lambda: parser)
    cli.main(["image", "inspect", "--image", "hello-world", "--no-exec", "--json"])
    envelope = json.loads(capsys.readouterr().out)
    assert not any("cleanup_partial" in str(item) for item in envelope["warnings"])


def test_the_product_entry_point_reports_a_blocked_inspection(monkeypatch, capsys):
    parser = _wire_the_snippet_into_the_real_parser()
    monkeypatch.setattr(II, "inspect_image", lambda **kw: {
        "ok": False, "blocked": "docker_unreachable", "image": kw.get("image"),
        "reason": "`docker image inspect` did not answer: Cannot connect to the Docker daemon",
    })
    monkeypatch.setattr(cli, "build_parser", lambda: parser)
    code = cli.main(["image", "inspect", "--image", "alpine:latest", "--json"])
    envelope = json.loads(capsys.readouterr().out)
    assert code == 3
    assert envelope["ok"] is False
    assert envelope["exit_code"] == 3
    assert envelope["error"]["code"] == "blocked"
    assert "did not answer" in envelope["error"]["message"]
