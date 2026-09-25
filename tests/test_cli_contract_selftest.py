"""P1a — ``skaldir selftest``: one honest verdict per layer, never a claim.

The rule (MVP/02 §5.10): a layer that is not wired reports ``ok: false`` with a
``blocked_reason`` and evidence — never ``reachable``. The command exits 2 when at
least one layer is not reachable, because "could not verify" must not look like "ok".
"""

from __future__ import annotations

import json

from skaldir import cli, cli_contract

LAYER_KEYS = {"layer", "ok", "status", "exit_code", "blocked_reason", "probes", "evidence"}


def _run(capsys, argv: list[str]) -> tuple[int, dict]:
    code = cli.main(argv)
    return code, json.loads(capsys.readouterr().out)


def test_selftest_shape_is_the_documented_one(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GODSV_HOME", str(tmp_path / "godsv"))
    code, document = _run(capsys, ["selftest", "--json"])
    assert code in (cli_contract.EXIT_OK, cli_contract.EXIT_VERIFICATION_FAILED)
    data = document["data"]
    assert [layer["layer"] for layer in data["layers"]] == list(cli_contract.LAYERS)
    assert data["probes"] == sum(len(layer["probes"]) for layer in data["layers"])
    assert data["ok"] is (data["failed"] == 0)
    assert document["exit_code"] == code == (0 if data["ok"] else 2)
    for layer in data["layers"]:
        assert set(layer) >= LAYER_KEYS
        assert layer["status"] in {"reachable", "blocked", "failed"}
        assert layer["ok"] is (layer["status"] == "reachable")
        if layer["ok"]:
            assert layer["exit_code"] == cli_contract.EXIT_OK
        assert layer["evidence"], f"{layer['layer']} reported no evidence"
        for probe in layer["probes"]:
            assert set(probe) >= {"name", "ok", "status", "detail", "evidence_ref"}
            assert isinstance(probe["ok"], bool)


def test_an_unreachable_layer_carries_a_reason_and_evidence(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GODSV_HOME", str(tmp_path / "empty-runtime"))
    code, document = _run(capsys, ["selftest", "--json"])
    assert code == cli_contract.EXIT_VERIFICATION_FAILED
    data = document["data"]
    assert data["failed"] > 0
    assert document["error"]["code"] == "verification_failed"
    for layer in data["layers"]:
        if layer["ok"]:
            continue
        assert layer["blocked_reason"], f"{layer['layer']} fails without a reason"
        assert layer["exit_code"] in (
            cli_contract.EXIT_BLOCKED,
            cli_contract.EXIT_VERIFICATION_FAILED,
        )
    assert set(data["failed_layers"]) == {
        layer["layer"] for layer in data["layers"] if not layer["ok"]
    }


def test_the_persist_layer_names_the_missing_run_store(tmp_path, monkeypatch, capsys):
    """The doc's acceptance case: an empty runtime makes the PERSIST probes fail."""
    monkeypatch.setenv("GODSV_HOME", str(tmp_path / "empty-runtime"))
    _code, document = _run(capsys, ["selftest", "--layer", "PERSIST", "--json"])
    layer = document["data"]["layers"][0]
    assert layer["layer"] == "PERSIST"
    assert layer["ok"] is False
    assert "runs.db" in layer["blocked_reason"]
    probes = {probe["name"]: probe for probe in layer["probes"]}
    assert probes["run_store"]["ok"] is False
    assert "no run has ever been started" in probes["run_store"]["detail"]


def test_selftest_is_read_only(tmp_path, monkeypatch, capsys):
    runtime = tmp_path / "untouched"
    runtime.mkdir()
    monkeypatch.setenv("GODSV_HOME", str(runtime))
    before = sorted(item.name for item in runtime.iterdir())
    _run(capsys, ["selftest", "--json"])
    assert sorted(item.name for item in runtime.iterdir()) == before, (
        "selftest wrote into the runtime dir — it must only probe"
    )


def test_layer_filter_returns_only_that_layer(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GODSV_HOME", str(tmp_path / "godsv"))
    code, document = _run(capsys, ["selftest", "--layer", "ENFORCE", "--json"])
    layers = document["data"]["layers"]
    assert [layer["layer"] for layer in layers] == ["ENFORCE"]
    assert code == document["exit_code"]


def test_an_unknown_layer_is_a_usage_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GODSV_HOME", str(tmp_path / "godsv"))
    code, document = _run(capsys, ["selftest", "--layer", "NOPE", "--json"])
    assert code == cli_contract.EXIT_USAGE
    assert document["error"]["code"] == "usage"
    assert "known layers" in (document["error"]["hint"] or "")


def test_selftest_reports_the_cli_surface_of_each_layer(tmp_path, monkeypatch, capsys):
    """The layer record says which commands exist — ROUTE has the Jev façade, SUPERVISE none."""
    monkeypatch.setenv("GODSV_HOME", str(tmp_path / "godsv"))
    _code, document = _run(capsys, ["selftest", "--json"])
    surfaces = {
        layer["layer"]: layer["cli_commands"] for layer in document["data"]["layers"]
    }
    assert "completion.status" in surfaces["ENFORCE"]
    # wired in cli.py: `register_cli(sub)` + the ROUTE contracts in COMMANDS. The list grew
    # by the two compaction verbs when `context_manager.register_cli` was wired onto the
    # `context` group, and by the three `coding.model.*` verbs when the coding-agent group
    # (`coding_features_cli.register_cli`) was wired in — the assertion pins the surface, it
    # is not a claim it cannot grow.
    assert surfaces["ROUTE"] == [
        "coding.model.clear",
        "coding.model.set",
        "coding.model.show",
        "context.compact",
        "context.usage",
        "jev.decide",
        "jev.history",
        "jev.kinds",
        "jev.packs",
        "jev.selftest",
    ]
    # SUPERVISE grew the four `voice.*` verbs when `voice_cli.register_cli` was wired in
    # cli.py (a sibling wave), and the two `coding.events.*` verbs with the coding-agent
    # group in this round. Same rule as above: the assertion pins the surface.
    assert surfaces["SUPERVISE"] == [
        "coding.events.run",
        "coding.events.show",
        "voice.check",
        "voice.explain",
        "voice.status",
        "voice.triage",
    ]


def test_selftest_human_output_lists_every_layer(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GODSV_HOME", str(tmp_path / "godsv"))
    cli.main(["selftest"])
    out = capsys.readouterr().out
    for layer in cli_contract.LAYERS:
        assert layer in out
