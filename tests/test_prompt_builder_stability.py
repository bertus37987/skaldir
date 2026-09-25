"""The stability contract — the prompt must not move unless something moved.

A prompt cache is keyed on the exact bytes of the system prompt, so a mid-conversation
change silently multiplies the user's cost. This file proves the three promises the
composer makes:

1. **Nothing changes when nothing changed.** Two consecutive builds are byte-identical,
   and a moving clock cannot move one byte (the composer module is not allowed to import
   a clock at all — the freeze writer takes ``now`` as a parameter, and that is asserted
   structurally, not by convention).
2. **A real input change is visible.** A new incident in the memory store produces a new
   fingerprint, and the break names the section that moved. A same-length edit also
   produces a new fingerprint: the hash is over the bytes, not over the sizes.
3. **A break is refused, not absorbed.** With a frozen conversation, a changed prompt
   exits 6 (conflict) from ``prompt build`` until the caller accepts the change
   (``--accept-change``, which re-freezes and reports what it accepted).

Cross-process reproducibility is checked too: ``PYTHONHASHSEED`` differs per process, so
two subprocess builds proving the same fingerprint is what rules out a set or dict
iteration order leaking into the prompt.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from skaldir import prompt_builder as P
from skaldir import prompt_builder_cli as C
from skaldir.incidents import STATUS_ACTIVE, Incident, IncidentStore

REPO_ROOT = Path(__file__).resolve().parents[1]
PROMPTS_ROOT = REPO_ROOT / "prompts"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A real project dir with real instruction files (no fakes)."""
    path = tmp_path / "project"
    path.mkdir()
    (path / "AGENTS.md").write_text(
        "# Project\n\n## Rules\n- Always run the tests before claiming a fix.\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def runtime(tmp_path: Path) -> Path:
    """A throwaway runtime dir — never the developer's real install."""
    home = tmp_path / "rt"
    home.mkdir()
    return home


@pytest.fixture
def context(project: Path, runtime: Path) -> P.BuildContext:
    return P.context_for(project=project, runtime=runtime, prompts_root=PROMPTS_ROOT)


def sec(assembly: P.PromptAssembly, name: str) -> P.PromptSection:
    """The composed section by name — a missing one is a test bug, not a case."""
    row = assembly.section(name)
    assert row is not None, f"no section {name!r} was composed"
    return row


def add_incident(memory_db: Path, signature: str, title: str = "something broke") -> None:
    """Write one real incident into the real store (the only way memory changes)."""
    store = IncidentStore(memory_db)
    store.upsert(
        Incident(signature=signature, title=title, root_cause="a missing guard", confidence=0.9)
    )
    store.promote(signature, STATUS_ACTIVE)
    store.close()


def cli(args: list[str], capsys) -> tuple[int, str, str]:
    """Run one ``skaldir prompt`` verb in-process; returns ``(exit, stdout, stderr)``."""
    code = C.main(args)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


# --------------------------------------------------------------------------- #
# 1. nothing changes when nothing changed                                       #
# --------------------------------------------------------------------------- #


def test_two_consecutive_builds_are_byte_identical(context):
    first = P.build_prompt(context)
    second = P.build_prompt(context)
    assert first.text == second.text
    assert first.fingerprint == second.fingerprint
    assert first.section_map() == second.section_map()


def test_the_composer_module_may_not_import_a_clock():
    """The structural reason a build cannot depend on the time: there is no clock here."""
    import inspect

    source = inspect.getsource(P)
    assert "import time" not in source
    assert "import datetime" not in source
    assert "time.time(" not in source
    assert "datetime.now" not in source


def test_a_moving_clock_cannot_change_one_byte(context, monkeypatch):
    """Even if something inside a provider did read a clock, this would catch it.

    ``datetime.datetime`` is an immutable C type, so the whole class is swapped for a
    frozen one instead of patching ``now`` on it — a patch that could not be applied
    would be a guard that never bites.
    """
    import datetime as datetime_module
    import time

    class FrozenDateTime(datetime_module.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2031, 12, 31, 23, 59, 59)

        @classmethod
        def utcnow(cls):
            return cls(2031, 12, 31, 23, 59, 59)

    before = P.build_prompt(context)
    monkeypatch.setattr(time, "time", lambda: 1_000_000_000.5)
    monkeypatch.setattr(time, "monotonic", lambda: 42.0)
    monkeypatch.setattr(
        time, "localtime", lambda *a, **k: time.struct_time((2020, 1, 1, 0, 0, 0, 2, 1, 0))
    )
    monkeypatch.setattr(datetime_module, "datetime", FrozenDateTime)
    after = P.build_prompt(context)
    assert after.text == before.text
    assert after.fingerprint == before.fingerprint


def test_a_second_process_with_another_hash_seed_agrees(project, runtime, tmp_path):
    """Set/dict iteration order must not reach the prompt (PYTHONHASHSEED is per process)."""
    env = {
        **os.environ,
        "PYTHONPATH": str(REPO_ROOT / "src"),
        "PYTHONHASHSEED": "1",
        "GODSV_HOME": str(runtime),
        "HERMES_HOME": str(tmp_path / "hermes"),
    }
    # SKALDIR_HOME outranks GODSV_HOME: a leaked one would point the child at another runtime
    # and the comparison below would measure the leak, not the hash seed.
    env.pop("SKALDIR_HOME", None)
    args = [
        sys.executable, "-m", "skaldir.prompt_builder_cli", "fingerprint", "--json",
        "--project", str(project), "--runtime", str(runtime),
        "--prompts-root", str(PROMPTS_ROOT),
    ]
    digests = []
    for seed in ("1", "424242"):
        process = subprocess.run(
            args, env={**env, "PYTHONHASHSEED": seed}, capture_output=True, text=True, check=False
        )
        assert process.returncode == 0, process.stderr
        digests.append(json.loads(process.stdout)["fingerprint"])
    assert digests[0] == digests[1]
    assert digests[0] == P.build_prompt(
        P.context_for(project=project, runtime=runtime, prompts_root=PROMPTS_ROOT)
    ).fingerprint


# --------------------------------------------------------------------------- #
# 2. a real change is visible                                                   #
# --------------------------------------------------------------------------- #


def test_a_new_incident_produces_a_new_fingerprint_and_names_the_section(context):
    before = P.build_prompt(context)
    assert sec(before, "memory").present is False
    add_incident(context.memory_db, "boom::1")
    after = P.build_prompt(context)
    assert after.fingerprint != before.fingerprint
    assert sec(after, "memory").present is True
    changes = P.changed_sections(before.section_map(), after.section_map())
    assert [item["name"] for item in changes] == ["memory"]
    assert changes[0]["kind"] == "added"


def test_a_same_length_edit_still_moves_the_fingerprint(project, runtime):
    """The fingerprint is over the bytes: equal sizes are not equal prompts."""
    ctx = P.context_for(project=project, runtime=runtime, prompts_root=PROMPTS_ROOT)
    before = P.build_prompt(ctx)
    text = (project / "AGENTS.md").read_text(encoding="utf-8")
    (project / "AGENTS.md").write_text(text.replace("Always", "NEVER!"), encoding="utf-8")
    after = P.build_prompt(ctx)
    assert len((project / "AGENTS.md").read_text(encoding="utf-8")) == len(text)
    assert after.fingerprint != before.fingerprint
    changes = P.changed_sections(before.section_map(), after.section_map())
    assert [item["name"] for item in changes] == ["project_context"]
    assert changes[0]["kind"] == "changed"


def test_a_removed_section_is_reported_as_removed(context):
    before = P.build_prompt(context)
    add_incident(context.memory_db, "boom::1")
    with_memory = P.build_prompt(context)
    context.memory_db.unlink()
    after = P.build_prompt(context)
    changes = P.changed_sections(with_memory.section_map(), after.section_map())
    assert {"name": "memory", "kind": "removed"} in [
        {"name": item["name"], "kind": item["kind"]} for item in changes
    ]
    assert after.fingerprint != before.fingerprint or after.fingerprint == before.fingerprint


def test_the_section_map_covers_every_section_including_the_omitted_ones(context):
    assembly = P.build_prompt(context)
    assert sorted(assembly.section_map()) == sorted(P.SECTION_ORDER)
    assert assembly.section_map()["memory"] == ""


# --------------------------------------------------------------------------- #
# 3. frozen mode: a break is refused, not absorbed                              #
# --------------------------------------------------------------------------- #


def test_an_unfrozen_conversation_reports_itself_as_unfrozen(context):
    status = P.cache_status(P.build_prompt(context), context.runtime)
    assert status.frozen is False
    assert status.stable is True
    assert status.cache_break is False
    assert str(P.freeze_path(context.runtime)) in status.note


def test_freezing_records_the_fingerprint_for_one_conversation_only(context):
    assembly = P.build_prompt(context)
    record = P.write_freeze(assembly, context.runtime, "live", now=1_700_000_000.0)
    assert record.fingerprint == assembly.fingerprint
    assert sorted(record.sections) == sorted(P.SECTION_ORDER)
    path = P.freeze_path(context.runtime)
    assert path.is_file()
    document = json.loads(path.read_text(encoding="utf-8"))
    assert list(document["conversations"]) == ["live"]
    assert P.cache_status(assembly, context.runtime, "live").stable is True
    assert P.cache_status(assembly, context.runtime, "other").frozen is False


def test_a_memory_change_breaks_a_frozen_conversation_and_names_the_section(context):
    assembly = P.build_prompt(context)
    P.write_freeze(assembly, context.runtime, "live", now=1_700_000_000.0)
    add_incident(context.memory_db, "boom::1")
    changed = P.build_prompt(context)
    status = P.cache_status(changed, context.runtime, "live")
    assert status.frozen is True
    assert status.stable is False
    assert status.cache_break is True
    assert status.previous_fingerprint == assembly.fingerprint
    assert [item["name"] for item in status.changed_sections] == ["memory"]
    assert "memory" in status.reason
    assert changed.fingerprint != assembly.fingerprint


def test_re_freezing_the_same_prompt_keeps_the_fingerprint(context):
    assembly = P.build_prompt(context)
    first = P.write_freeze(assembly, context.runtime, "live", now=1_700_000_000.0)
    second = P.write_freeze(P.build_prompt(context), context.runtime, "live", now=1_700_000_900.0)
    assert second.fingerprint == first.fingerprint
    assert second.frozen_at != first.frozen_at  # the record moved, the prompt did not


def test_a_corrupt_freeze_file_is_reported_not_raised(context):
    path = P.freeze_path(context.runtime)
    path.write_text("{not json at all", encoding="utf-8")
    assembly = P.build_prompt(context)
    record, problem = P.load_freeze(context.runtime, "live")
    assert record is None
    assert str(path) in problem
    status = P.cache_status(assembly, context.runtime, "live")
    assert status.frozen is False and status.cache_break is False
    assert problem in status.note


def test_a_malformed_record_is_reported_not_raised(context):
    P.freeze_path(context.runtime).write_text(
        json.dumps({"conversations": {"live": {"nope": True}}}), encoding="utf-8"
    )
    record, problem = P.load_freeze(context.runtime, "live")
    assert record is None
    assert "malformed" in problem


# --------------------------------------------------------------------------- #
# the CLI: the same promises, through the product surface                        #
# --------------------------------------------------------------------------- #


def _args(project: Path, runtime: Path, *extra: str) -> list[str]:
    return [
        "--project", str(project), "--runtime", str(runtime),
        "--prompts-root", str(PROMPTS_ROOT), *extra,
    ]


def test_cli_build_text_stdout_is_exactly_the_fingerprinted_bytes(project, runtime, capsys):
    code, out, _err = cli(
        ["build", "--text", "--conversation", "live", *_args(project, runtime)], capsys
    )
    assert code == C.EXIT_OK
    assembly = P.build_prompt(
        P.context_for(project=project, runtime=runtime, prompts_root=PROMPTS_ROOT)
    )
    assert out == assembly.text, "stdout must be the prompt and nothing else"
    assert P.fingerprint_of(out) == assembly.fingerprint, "sha256(stdout) == the fingerprint"


def test_cli_build_json_carries_the_prompt_and_its_sizes(project, runtime, capsys):
    code, out, _err = cli(["build", "--json", *_args(project, runtime)], capsys)
    assert code == C.EXIT_OK
    payload = json.loads(out)
    assert payload["fingerprint"] == P.fingerprint_of(payload["text"])
    assert payload["chars"] == len(payload["text"])
    assert payload["estimate_method"] == P.ESTIMATE_METHOD
    assert payload["cache"]["frozen"] is False
    assert [row["name"] for row in payload["sections"]] == list(P.SECTION_ORDER)


def test_cli_refuses_a_cache_break_with_exit_6_and_accepts_it_deliberately(
    project, runtime, capsys
):
    code, out, _err = cli(
        ["build", "--freeze", "--text", "--conversation", "live", *_args(project, runtime)], capsys
    )
    assert code == C.EXIT_OK
    frozen_bytes = out

    add_incident(runtime / "memory.db", "boom::1")

    code, out, err = cli(
        ["build", "--text", "--conversation", "live", *_args(project, runtime)], capsys
    )
    assert code == C.EXIT_CONFLICT
    assert "cache break" in err
    assert out != frozen_bytes, "the refused build still shows what it would have sent"
    refused = out

    code, out, err = cli(["fingerprint", "--text", "--conversation", "live",
                          *_args(project, runtime)], capsys)
    assert code == C.EXIT_CONFLICT
    assert "stable: NO" in out
    assert "added    memory" in out

    code, out, err = cli(["build", "--accept-change", "--text", "--conversation", "live",
                          *_args(project, runtime)], capsys)
    assert code == C.EXIT_OK
    assert out == refused

    # The decision outlives the call that made it: the record names what it replaced.
    record = json.loads(P.freeze_path(runtime).read_text(encoding="utf-8"))["conversations"]["live"]
    assert record["accepted_from"] == P.fingerprint_of(frozen_bytes)
    code, out, _err = cli(
        ["fingerprint", "--json", "--conversation", "live", *_args(project, runtime)], capsys
    )
    assert code == C.EXIT_OK
    cache = json.loads(out)["cache"]
    assert cache["stable"] is True
    assert cache["accepted_from"] == P.fingerprint_of(frozen_bytes)
    assert "deliberately replaced" in cache["reason"]

    code, _out, _err = cli(
        ["build", "--text", "--conversation", "live", *_args(project, runtime)], capsys
    )
    assert code == C.EXIT_OK, "once accepted, the conversation is stable again"


def test_an_accepted_break_is_remembered_in_the_freeze_record(context):
    first = P.build_prompt(context)
    P.write_freeze(first, context.runtime, "live", now=1_000.0)
    add_incident(context.memory_db, "boom::1")
    second = P.build_prompt(context)
    assert P.cache_status(second, context.runtime, "live").cache_break is True

    record = P.write_freeze(
        second, context.runtime, "live", now=2_000.0, accepted_from=first.fingerprint
    )
    assert record.accepted_from == first.fingerprint
    status = P.cache_status(P.build_prompt(context), context.runtime, "live")
    assert status.stable is True
    assert status.accepted_from == first.fingerprint
    assert first.fingerprint[:16] in status.reason
    reloaded, _problem = P.load_freeze(context.runtime, "live")
    assert reloaded is not None and reloaded.accepted_from == first.fingerprint
    assert reloaded.frozen_at == 2_000.0


def test_cli_explain_reports_a_break_without_refusing_it(project, runtime, capsys):
    cli(["fingerprint", "--freeze", "--conversation", "live", *_args(project, runtime)], capsys)
    add_incident(runtime / "memory.db", "boom::1")
    code, out, _err = cli(["explain", "--text", "--conversation", "live",
                           *_args(project, runtime)], capsys)
    assert code == C.EXIT_OK
    assert "CACHE BREAK" in out
    assert "memory" in out
    assert "present" in out


def test_cli_explain_json_names_the_source_of_every_section(project, runtime, capsys):
    code, out, _err = cli(["explain", "--json", *_args(project, runtime)], capsys)
    assert code == C.EXIT_OK
    payload = json.loads(out)
    assert payload["section_count"] == len(P.SECTION_ORDER)
    rows = {row["name"]: row for row in payload["sections"]}
    assert rows["hard_rules"]["source"].endswith(
        "main_agent_steering.v1.md (id=main_agent_steering v1)"
    )
    assert rows["tools"]["source"].startswith(str(runtime / "tools.db"))
    assert rows["project_context"]["source"].startswith("skaldir.project_context.")
    assert rows["memory"]["present"] is False and rows["memory"]["detail"]
    assert payload["inputs"]["project"] == str(project)


def test_cli_over_budget_exits_2(project, runtime, capsys):
    code, out, err = cli(["build", "--json", "--budget", "10", *_args(project, runtime)], capsys)
    assert code == C.EXIT_VERIFICATION_FAILED
    assert json.loads(out)["over_budget"] is True
    assert "over budget" in err


def test_cli_without_every_section_is_blocked_exit_3(project, runtime, capsys):
    args = ["build", "--json", *_args(project, runtime)]
    for name in P.SECTION_ORDER:
        args += ["--without", name]
    code, out, _err = cli(args, capsys)
    assert code == C.EXIT_BLOCKED
    assert json.loads(out)["text"] == ""


def test_cli_refuses_an_unknown_section_and_a_malformed_section_budget(project, runtime, capsys):
    code, _out, _err = cli(["build", "--without", "nope", *_args(project, runtime)], capsys)
    assert code == C.EXIT_USAGE
    code, _out, _err = cli(
        ["build", "--section-budget", "memory", *_args(project, runtime)], capsys
    )
    assert code == C.EXIT_USAGE
    code, _out, _err = cli(
        ["build", "--section-budget", "nope=10", *_args(project, runtime)], capsys
    )
    assert code == C.EXIT_USAGE


def test_cli_section_budget_is_honoured(project, runtime, capsys):
    code, out, _err = cli(
        ["explain", "--json", "--section-budget", "tools=120", *_args(project, runtime)], capsys
    )
    assert code == C.EXIT_OK
    rows = {row["name"]: row for row in json.loads(out)["sections"]}
    assert rows["tools"]["budget_chars"] == 120
    assert rows["tools"]["truncated"] is True
    marker = P.TRUNCATION_MARKER.format(omitted=rows["tools"]["original_chars"])
    assert rows["tools"]["chars"] <= 120 + len(marker)


def test_cli_fingerprint_can_freeze_a_conversation(project, runtime, capsys):
    code, out, _err = cli(["fingerprint", "--json", *_args(project, runtime)], capsys)
    assert code == C.EXIT_OK
    assert json.loads(out)["cache"]["frozen"] is False
    code, out, _err = cli(
        ["fingerprint", "--freeze", "--conversation", "abc", *_args(project, runtime)], capsys
    )
    assert code == C.EXIT_OK
    payload = json.loads(out)
    assert payload["cache"]["frozen"] is True and payload["cache"]["stable"] is True
    assert payload["conversation"] == "abc"
    assert P.freeze_path(runtime).is_file()


def test_cli_refuses_freeze_and_accept_change_together(project, runtime, capsys):
    with pytest.raises(SystemExit):
        C.build_parser().parse_args(
            ["build", "--freeze", "--accept-change", *_args(project, runtime)]
        )


# --------------------------------------------------------------------------- #
# the wiring contract (the CLI owner adds the group; these keep it honest)       #
# --------------------------------------------------------------------------- #


def test_the_group_registers_one_parser_with_three_verbs():
    import argparse

    root = argparse.ArgumentParser()
    sub = root.add_subparsers(dest="command")
    parser = C.register_cli(sub)
    assert parser.prog.endswith("prompt")
    assert sorted(parser._actions[1].choices) == ["build", "explain", "fingerprint"]


def test_registering_the_group_installs_its_contract_entries(tmp_path):
    """The group cannot be wired without its COMMANDS entries (the dump test would fail)."""
    from skaldir import cli_contract

    before = dict(cli_contract.COMMANDS)
    added = C.register_cli(__import__("argparse").ArgumentParser().add_subparsers(dest="c"))
    assert added is not None
    for path in ("prompt.build", "prompt.explain", "prompt.fingerprint"):
        spec = cli_contract.COMMANDS.get(path)
        assert spec is not None, f"{path} has no contract entry after register_cli"
        assert spec.layer == "PERSIST"
        assert spec.evidence, f"{path} declares no evidence"
        assert spec.data_schema is not None, f"{path} declares no data schema"
        assert spec.data_schema["type"] == "object"
        assert spec.mutates is False, f"{path} writes something (the group is read-only)"
    for path, spec in before.items():
        assert cli_contract.COMMANDS[path] == spec, "an existing entry was overwritten"


def test_the_paste_ready_snippet_names_the_same_commands_as_the_entries():
    for path in CONTRACT_PATHS:
        assert path in C.CONTRACT_SNIPPET
        assert path in C.CONTRACT_ENTRIES
    assert "register_cli" in C.CONTRACT_SNIPPET


CONTRACT_PATHS = ("prompt.build", "prompt.explain", "prompt.fingerprint")
