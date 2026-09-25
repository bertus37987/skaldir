"""The teardown EVIDENCE line of ``tools rollback`` must show the real command.

Defect found while fixing the label-filter bug: the handler rendered its teardown
evidence as ``docker ps -a --filter label={teardown_scope}``, where
``teardown_scope`` is the scope's comma-joined TEXT form
(``key=value,key=value``, from ``docker_runtime.label_filters``). For any scope with
more than one label that re-printed the OLD, broken form: docker reads
``--filter label=a=1,b=2`` as ONE label pair and matches **nothing** (measured live
on Server 29.6.1) — exactly the defect the runtime fixed by emitting one
``--filter`` per label (``docker_runtime.label_filter_args``). The envelope's
evidence therefore described a command line the runtime never executed.

What this module pins:

* the rendered evidence carries ONE ``--filter`` per label and never a comma inside
  a single ``--filter`` value;
* it is built by the runtime's own ``label_filter_args`` (spied on, so a second
  comma-joining call site in the CLI cannot return silently);
* the label pairs travel as DATA from the builder's rollback result
  (``teardown_labels``, a list of ``key=value`` pairs) and win over the scope TEXT,
  so a label VALUE containing a comma round-trips (the text form cannot tell that
  comma from the pair separator — pinned as the documented limitation below);
* the one-label scope keeps its earlier, working rendering;
* a rollback whose sweep never ran (empty scope) claims no command line at all
  instead of printing the old ``--filter label=None``.

No docker is needed: the handler is driven with a fake builder, and the assertion is
on the envelope's evidence, i.e. on the text the product shows.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from skaldir import cli_contract, toolbuilder_cli
from skaldir import docker_runtime as dr

CAPABILITY = "container_sha256"

#: A successful rollback as ``ContainerToolBuilder.rollback_container_tool`` returns it.
ROLLBACK = {
    "ok": True,
    "rolled_back": True,
    "name": CAPABILITY,
    "reason": "evidence test",
    "quarantine": "/tmp/gv-quarantine/container_sha256",
    "containers_removed": ["0ur00000001"],
    "teardown_scope": "",
    # the scope as DATA (key=value pairs, in sweep order) — empty here, so the
    # tests that model an older text-only result still exercise the fallback
    "teardown_labels": [],
    "teardown_ok": True,
    "teardown_detail": "removed 1 leftovers",
    "health": {"state": "RETIRED"},
    "removed_registration": True,
    "restored_spec": None,
    "failure_class": "",
}


class _FakeBuilder:
    """The one call the handler makes — recorded, so the renderer is isolated."""

    def __init__(self, result: dict, calls: list) -> None:
        self.result = result
        self.calls = calls

    def rollback_container_tool(self, name: str, *, reason: str = "") -> dict:
        self.calls.append({"name": name, "reason": reason})
        return dict(self.result)


def _rollback(monkeypatch, capsys, *, teardown_scope: str, teardown_ok: bool = True,
              teardown_labels: list[str] | None = None):
    """Drive the real handler through the real envelope and return (envelope, calls).

    ``teardown_labels`` is the DATA the builder's rollback carries (the
    ``key=value`` pairs the sweep was given); ``None`` models an older result that
    only has the scope TEXT, so the fallback path stays under test too.
    """
    calls: list = []
    result = dict(ROLLBACK, teardown_scope=teardown_scope, teardown_ok=teardown_ok)
    if teardown_labels is not None:
        result["teardown_labels"] = list(teardown_labels)
    monkeypatch.setattr(
        toolbuilder_cli, "_builder", lambda _args: _FakeBuilder(result, calls)
    )
    args = argparse.Namespace(
        command_path="tools.rollback",
        name=CAPABILITY,
        reason="evidence test",
        json=True,
        quiet=True,
    )
    exit_code = cli_contract.run_command(
        args, toolbuilder_cli._cmd_tools_rollback, json_mode=True
    )
    envelope = json.loads(capsys.readouterr().out)
    assert exit_code == cli_contract.EXIT_OK, envelope
    assert calls == [{"name": CAPABILITY, "reason": "evidence test"}], calls
    return envelope, calls


def _command_evidence(envelope: dict) -> list[dict]:
    """The executed-command evidence (kind ``"run"``) of the envelope."""
    return [item for item in envelope["evidence"] if item["kind"] == "run"]


def _filter_values(command: str) -> list[str]:
    """The values of every ``--filter`` in a rendered command line."""
    tokens = command.split()
    return [tokens[index + 1] for index, item in enumerate(tokens) if item == "--filter"]


def test_a_multi_label_scope_renders_one_filter_per_label(monkeypatch, capsys):
    """The regression: scope text is a DESCRIPTION, not a single filter argument."""
    scope = f"{dr.OWNER_LABEL_KEY}={dr.OWNER_LABEL_VALUE},godsv.capability={CAPABILITY}"

    envelope, _calls = _rollback(monkeypatch, capsys, teardown_scope=scope)

    commands = _command_evidence(envelope)
    assert len(commands) == 1, commands
    command = commands[0]["ref"]
    values = _filter_values(command)
    assert values == [
        f"label={dr.OWNER_LABEL_KEY}={dr.OWNER_LABEL_VALUE}",
        f"label=godsv.capability={CAPABILITY}",
    ], command
    assert not any("," in value for value in values), command
    assert "," not in command, command
    # the old rendering put the whole scope into ONE --filter value
    assert f"label={scope}" not in command, command
    # …and it is the runtime's own filter line, not a second implementation
    assert command == " ".join(
        [
            "docker",
            "ps",
            "-a",
            *dr.label_filter_args(
                {
                    dr.OWNER_LABEL_KEY: dr.OWNER_LABEL_VALUE,
                    "godsv.capability": CAPABILITY,
                }
            ),
        ]
    ), command
    assert f"scope {scope}" in commands[0]["note"], commands[0]


@pytest.mark.parametrize(
    "scope",
    [
        f"{dr.OWNER_LABEL_KEY}={dr.OWNER_LABEL_VALUE}",
        f"{dr.OWNER_LABEL_KEY}={dr.OWNER_LABEL_VALUE},godsv.build_id=b-1",
        "godsv.owner=skaldir,godsv.build_id=b-1,godsv.capability=container_sha256",
    ],
)
def test_no_comma_ever_lands_inside_a_rendered_filter(monkeypatch, capsys, scope):
    envelope, _calls = _rollback(monkeypatch, capsys, teardown_scope=scope)

    commands = _command_evidence(envelope)
    assert len(commands) == 1, commands
    command = commands[0]["ref"]
    values = _filter_values(command)
    assert len(values) == len(scope.split(",")), command
    assert not any("," in value for value in values), command
    assert command == " ".join(
        ["docker", "ps", "-a", *dr.label_filter_args(dict(
            pair.split("=", 1) for pair in scope.split(",")
        ))]
    ), command


def test_the_rendered_filters_come_from_the_runtime_helper(monkeypatch, capsys):
    """A static CLI re-implementation could drift; the runtime call is asserted."""
    seen: list[dict] = []
    original = dr.label_filter_args

    def spy(labels):
        seen.append(dict(labels))
        return original(labels)

    monkeypatch.setattr(dr, "label_filter_args", spy)

    envelope, _calls = _rollback(
        monkeypatch, capsys, teardown_scope="godsv.owner=skaldir,godsv.build_id=b-1"
    )

    assert seen == [{"godsv.owner": "skaldir", "godsv.build_id": "b-1"}], seen
    assert _filter_values(_command_evidence(envelope)[0]["ref"]) == [
        "label=godsv.owner=skaldir",
        "label=godsv.build_id=b-1",
    ], envelope


def test_a_one_label_scope_keeps_the_single_filter_rendering(monkeypatch, capsys):
    """No regression for the scope shape that already worked."""
    envelope, _calls = _rollback(
        monkeypatch, capsys, teardown_scope=f"godsv.capability={CAPABILITY}"
    )

    command = _command_evidence(envelope)[0]["ref"]
    assert command == f"docker ps -a --filter label=godsv.capability={CAPABILITY}", command


def test_a_rollback_without_a_sweep_claims_no_command(monkeypatch, capsys):
    """Empty scope ⇒ no listing happened: no fabricated command, no `label=None`."""
    envelope, _calls = _rollback(monkeypatch, capsys, teardown_scope="", teardown_ok=False)

    assert _command_evidence(envelope) == [], envelope["evidence"]
    rendered = json.dumps(envelope)
    assert "--filter" not in rendered, rendered
    assert "label=None" not in rendered, rendered
    # the evidence that IS claimed (the quarantine) is untouched
    assert envelope["ok"] is True and envelope["exit_code"] == cli_contract.EXIT_OK
    assert any(item["kind"] == "file" for item in envelope["evidence"]), envelope["evidence"]


def test_the_renderer_has_no_second_comma_joining_call_site():
    """The old shape came from an f-string; a grep guard keeps it from coming back."""
    source = Path(toolbuilder_cli.__file__).read_text(encoding="utf-8")
    offenders = [
        line.strip()
        for line in source.splitlines()
        if "--filter label={" in line
    ]
    assert offenders == [], offenders


# --------------------------------------------------------------------------- #
# the labels travel as DATA (not re-parsed from the scope text)
# --------------------------------------------------------------------------- #


def test_the_label_pairs_travel_as_data_and_win_over_the_scope_text(
    monkeypatch, capsys
):
    """The pairs the sweep was GIVEN are what ran — the data, not the prose."""
    pairs = [f"{dr.OWNER_LABEL_KEY}={dr.OWNER_LABEL_VALUE}", "godsv.build_id=b-1"]

    envelope, _calls = _rollback(
        monkeypatch, capsys,
        teardown_scope=f"{dr.OWNER_LABEL_KEY}={dr.OWNER_LABEL_VALUE},godsv.build_id=b-1",
        teardown_labels=pairs,
    )

    command = _command_evidence(envelope)[0]["ref"]
    assert command == " ".join(
        ["docker", "ps", "-a",
         *dr.label_filter_args(dict(pair.split("=", 1) for pair in pairs))]
    ), command
    # …and the data really is carried in the envelope, not only rendered
    assert envelope["data"]["teardown_labels"] == pairs, envelope["data"]


def test_a_comma_inside_a_label_value_survives_the_renderer(monkeypatch, capsys):
    """A label VALUE containing a comma round-trips as DATA (regression, N12).

    ``docker_runtime.label_filters`` joins the scope into ``key=value,key=value``
    text, so a comma inside a VALUE is indistinguishable from the pair separator
    there. The builder's rollback therefore hands the pairs over as data, and the
    renderer must use exactly those pairs.
    """
    value = "sha256:d9e853e87e55,build-7"
    pair = f"godsv.capability={value}"

    envelope, _calls = _rollback(
        monkeypatch, capsys,
        teardown_scope=pair,          # the text form of the very same scope
        teardown_labels=[pair],       # …and the data form
    )

    command = _command_evidence(envelope)[0]["ref"]
    assert command == f"docker ps -a --filter label=godsv.capability={value}", command
    assert command.count("--filter") == 1, command
    assert _filter_values(command) == [f"label=godsv.capability={value}"], command


def test_the_text_form_alone_cannot_round_trip_a_comma_value(monkeypatch, capsys):
    """Documented limitation of the fallback: that is WHY the data form exists.

    A result that carries no ``teardown_labels`` (an older/foreign result) is read
    from the scope TEXT, where the comma is taken for a pair separator — the value
    is split. The data path above is the fix; this pins the reason for it.
    """
    value = "a,b"

    envelope, _calls = _rollback(
        monkeypatch, capsys, teardown_scope=f"godsv.capability={value}"
    )

    values = _filter_values(_command_evidence(envelope)[0]["ref"])
    assert len(values) == 2, values
    assert f"label=godsv.capability={value}" not in values, values
