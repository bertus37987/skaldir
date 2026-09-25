"""tests/test_gv_benchmark.py — Block 8a: Harness für Benchmark-Vergleichsläufe.

Prüft ``tools/gv_benchmark.py`` gegen seinen Vertrag (Quelle:
``docs/BENCHMARK-RUN-SCHEMA.json`` + Master-§37/§43):

* Schema laden und validieren (echtes Schema + kaputte Varianten),
* die 7 benannten Konfigurationen samt Fähigkeits-Schaltern,
* **Konstanten-Vergleichsblock** — gut UND verletzt, inklusive CLI-Exit != 0,
* Metrik-Aggregation aus einem Fixture-Lauf-Ergebnis mit ehrlichen ``None``-Werten,
* ``run_record`` im Schema-Format.

Alles läuft ohne Modellaufruf, ohne Netz und ohne die echte App: reine Funktionen
plus tmp-Verzeichnisse. Kein Test schreibt in Repo-Pfade oder ``$HOME/.hermes``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import gv_benchmark as bench  # noqa: E402

SCHEMA_PATH = ROOT / "docs" / "BENCHMARK-RUN-SCHEMA.json"


@pytest.fixture()
def schema() -> dict:
    """Das echte Schema — die Wahrheitsquelle, nicht eine Test-Kopie."""
    return bench.load_and_validate_schema(SCHEMA_PATH)


def _complete_constants(schema: dict) -> dict:
    """Der Schema-Block mit den Leerstellen gefüllt, die ein echter Lauf setzt.

    Die leeren Felder im Schema sind bewusst leer (sie sind noch nicht
    festgelegt); ein Vergleichslauf MUSS sie tragen — genau das prüft der
    Harness. Hier wird nur für den Test ein vollständiger Satz gebaut.
    """
    values = {k: v for k, v in schema["held_constant"].items() if not k.startswith("$")}
    values.update(
        {
            "exact_model_version": "deepseek-v4.1-flash-2026-08-01",
            "reasoning": "medium",
            "tools": ["bash", "read_file", "write_file"],
            "context_window": 131072,
            "timeout_s": 900,
            "token_budget": 2_000_000,
            "cost_budget": 5.0,
            "workspace_baseline_commit": "7ab0167",
        }
    )
    return values


# --- (1) Schema laden + validieren -------------------------------------------


def test_real_schema_loads_and_validates():
    schema = bench.load_and_validate_schema(SCHEMA_PATH)
    assert schema["schema_version"] == "1.0"
    assert bench.validate_schema(schema) == []


def test_schema_follows_the_seven_configurations_and_full_metric_set(schema):
    assert [c["id"] for c in bench.list_configurations(schema)] == list(bench.CONFIGURATION_IDS)
    names = {
        name
        for group in bench.METRIC_GROUPS
        for name in schema["metrics"][group]
    }
    for metric in bench.REQUIRED_METRICS:
        assert metric in names, metric


def test_missing_schema_file_is_fail_loud(tmp_path):
    with pytest.raises(bench.ComparabilityError) as excinfo:
        bench.load_schema(tmp_path / "gibts-nicht.json")
    assert "nicht gefunden" in str(excinfo.value)


def test_broken_json_is_fail_loud(tmp_path):
    broken = tmp_path / "broken.json"
    broken.write_text("{ das ist kein json", encoding="utf-8")
    with pytest.raises(bench.ComparabilityError) as excinfo:
        bench.load_schema(broken)
    assert "kein gültiges JSON" in str(excinfo.value)


def test_validate_schema_names_every_missing_piece(schema):
    import copy

    broken = copy.deepcopy(schema)
    broken["held_constant"].pop("seed")
    broken["configurations"][0]["memory"] = "ja"  # kein bool
    broken["metrics"]["reliability"] = []
    broken["task_suite"]["golden_set"]["cases"].pop()
    broken["run_record"].pop("notes")

    problems = bench.validate_schema(broken)
    joined = "\n".join(problems)
    assert "seed" in joined
    assert "memory" in joined
    assert "metrics.reliability" in joined
    assert "golden_set.cases" in joined
    assert "notes" in joined


def test_validate_schema_rejects_wrong_schema_version(schema):
    import copy

    broken = copy.deepcopy(schema)
    broken["schema_version"] = "0.9"
    assert any("schema_version" in p for p in bench.validate_schema(broken))


def test_load_and_validate_raises_for_invalid_schema(tmp_path, schema):
    import copy

    broken = copy.deepcopy(schema)
    broken.pop("task_suite")
    path = tmp_path / "schema-luecke.json"
    path.write_text(json.dumps(broken), encoding="utf-8")
    with pytest.raises(bench.ComparabilityError) as excinfo:
        bench.load_and_validate_schema(path)
    assert "task_suite" in str(excinfo.value)


# --- (2) Konfiguration + Fähigkeits-Schalter ---------------------------------


def test_every_configuration_can_be_loaded_with_its_switches(schema):
    expected = {
        "hermes_baseline": (False, False, False, False, False),
        "hermes_completion": (True, False, False, False, False),
        "hermes_watcher": (True, True, False, False, False),
        "hermes_watcher_investigator": (True, True, True, False, False),
        "full_godsv": (True, True, True, False, False),
        "full_godsv_memory": (True, True, True, True, False),
        "full_godsv_capabilities": (True, True, True, True, True),
    }
    for config_id, switches in expected.items():
        loaded = bench.load_configuration(schema, config_id)
        assert loaded["id"] == config_id
        assert tuple(loaded[key] for key in bench.SWITCH_KEYS) == switches
        assert loaded["label"]


def test_capability_switches_are_a_flat_bool_mapping(schema):
    switches = bench.configuration_switches(schema, "full_godsv_memory")
    assert switches == {
        "completion": True,
        "watcher": True,
        "investigator": True,
        "memory": True,
        "learned_capabilities": False,
    }
    assert all(isinstance(v, bool) for v in switches.values())


def test_unknown_configuration_is_fail_loud(schema):
    with pytest.raises(bench.ComparabilityError) as excinfo:
        bench.load_configuration(schema, "hermes_v2")
    assert "hermes_v2" in str(excinfo.value)
    assert "full_godsv_capabilities" in str(excinfo.value)  # bekannte ids genannt


# --- (3) Konstanten-Vergleichsblock: gut -------------------------------------


def test_complete_constant_block_passes(schema):
    report = bench.check_constants(schema, _complete_constants(schema))
    assert report["ok"] is True
    assert report["constants"]["seed"] == 20260913
    assert report["constants"]["temperature"] == 0
    assert report["constants"]["main_model"] == "opencode-go/deepseek-v4.1-flash"
    assert report["runs"] == ["observed"]
    # Das Schema führt für diese Felder noch leere Platzhalter — der Harness
    # verschweigt das nicht, er meldet es als Lücke (statt sie als "geprüft"
    # auszugeben).
    assert "exact_model_version" in report["schema_reference_missing"]
    assert "token_budget" in report["schema_reference_missing"]


def test_unfilled_schema_placeholder_still_blocks_in_strict_mode(schema):
    """Mit require_schema_reference=true MUSS die Schemalücke abbrechen."""
    with pytest.raises(bench.ComparabilityError) as excinfo:
        bench.check_constants(
            schema, _complete_constants(schema), require_schema_reference=True
        )
    message = str(excinfo.value)
    assert "keinen Referenzwert" in message
    assert "exact_model_version" in message


def test_cli_strict_mode_exits_nonzero_while_default_mode_passes(schema, tmp_path, capsys):
    path = tmp_path / "block.json"
    path.write_text(json.dumps(_complete_constants(schema)), encoding="utf-8")
    assert bench.main(["--schema", str(SCHEMA_PATH), "--constants", str(path)]) == 0
    strict = bench.main(
        ["--schema", str(SCHEMA_PATH), "--constants", str(path), "--require-schema-reference"]
    )
    assert strict != 0
    assert "Referenzwert" in capsys.readouterr().err


def test_zero_and_false_are_valid_set_constants(schema):
    # temperature=0 und retries=0 sind bewusst gesetzte Werte, nicht "leer"
    constants = _complete_constants(schema)
    assert constants["temperature"] == 0
    assert constants["retries"] == 0

    def run(temperature, retries):
        block = dict(constants)
        block.update({"temperature": temperature, "retries": retries})
        return block

    report = bench.check_constants(
        schema, {"run_a": run(0, 0), "run_b": run(0.0, 0)}
    )
    assert report["ok"] is True


def test_identical_multi_run_block_passes_and_normalizes_tools_order(schema):
    first = _complete_constants(schema)
    second = dict(first)
    second["tools"] = list(reversed(first["tools"]))  # Reihenfolge ist keine Konstante
    report = bench.check_constants(schema, {"hermes_baseline": first, "full_godsv": second})
    assert report["ok"] is True
    assert report["runs"] == ["full_godsv", "hermes_baseline"]


def test_block_wrapper_and_inline_observation_both_work(schema):
    constants = _complete_constants(schema)
    wrapped = bench.check_constants(schema, {"runs": {"a": constants, "b": dict(constants)}})
    inline = bench.check_constants(schema, constants)
    assert wrapped["ok"] is True and inline["ok"] is True
    assert wrapped["constants"] == inline["constants"]


# --- (3) Konstanten-Vergleichsblock: Verletzung (fail-loud) ------------------


def test_varying_constant_across_the_block_aborts(schema):
    a = _complete_constants(schema)
    b = dict(a)
    b["temperature"] = 0.7  # der klassische Vergleichsbruch
    with pytest.raises(bench.ComparabilityError) as excinfo:
        bench.check_constants(schema, {"hermes_baseline": a, "full_godsv": b})
    message = str(excinfo.value)
    assert "temperature" in message
    assert "0.7" in message and "nicht identisch" in message


def test_deviation_from_schema_reference_value_aborts(schema):
    wrong = _complete_constants(schema)
    wrong["seed"] = 1  # Schema führt seed=20260913
    wrong["main_model"] = "openrouter/anthropic/claude"  # Modell-Routing verletzt
    with pytest.raises(bench.ComparabilityError) as excinfo:
        bench.check_constants(schema, wrong)
    message = str(excinfo.value)
    assert "seed" in message and "20260913" in message
    assert "main_model" in message and "openrouter" in message


def test_unset_constant_is_a_violation_not_an_unknown(schema):
    """Leer/fehlend ist ein Vergleichsbruch — es gibt keinen 'unbekannt'-Pfad."""
    partial = _complete_constants(schema)
    partial["exact_model_version"] = ""
    partial.pop("timeout_s")
    with pytest.raises(bench.ComparabilityError) as excinfo:
        bench.check_constants(schema, partial)
    message = str(excinfo.value)
    assert "exact_model_version" in message
    assert "nicht gesetzt" in message
    assert "timeout_s" in message


def test_empty_constant_block_aborts(schema):
    with pytest.raises(bench.ComparabilityError) as excinfo:
        bench.check_constants(schema, {})
    message = str(excinfo.value)
    assert "kein Beobachtungsblock" in message
    assert "Vergleich wäre wertlos" in message


@pytest.mark.parametrize("key", bench.CONSTANT_KEYS)
def test_every_required_constant_is_policed(schema, key):
    """Jede geforderte Konstante einzeln: Verdrehen MUSS auffallen."""
    good = _complete_constants(schema)
    reference = {k: v for k, v in schema["held_constant"].items() if not k.startswith("$")}
    # einen anderen, garantiert abweichenden Wert erzeugen
    if isinstance(reference.get(key), bool):
        broken_value: object = not reference[key]
    elif isinstance(reference.get(key), (int, float)):
        broken_value = (reference[key] or 0) + 42
    elif isinstance(reference.get(key), list):
        broken_value = [*reference[key], "extra-tool"]
    else:
        broken_value = "abweichender-wert-42"
    bad = dict(good)
    bad[key] = broken_value

    # require_schema_reference=true ist hier der ehrliche Modus: für die Felder,
    # die das Schema noch leer führt, ist schon ein fehlender Referenzwert ein
    # Abbruchgrund.
    with pytest.raises(bench.ComparabilityError) as excinfo:
        bench.check_constants(schema, bad, require_schema_reference=True)
    assert key in str(excinfo.value)


def test_constants_report_is_the_non_raising_view(schema):
    good = bench.constants_report(schema, _complete_constants(schema))
    bad = bench.constants_report(schema, {**_complete_constants(schema), "retries": 3})
    assert good["ok"] is True
    assert bad["ok"] is False
    assert any("retries" in v for v in bad["violations"])


def test_cli_constants_pass_exits_zero(schema, tmp_path, capsys):
    path = tmp_path / "block-ok.json"
    path.write_text(
        json.dumps({"runs": {"hermes_baseline": _complete_constants(schema)}}),
        encoding="utf-8",
    )
    rc = bench.main(["--schema", str(SCHEMA_PATH), "--constants", str(path)])
    assert rc == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["constants"]["ok"] is True


def test_cli_constants_violation_exits_nonzero(schema, tmp_path, capsys):
    violating = _complete_constants(schema)
    violating["temperature"] = 0.7  # Schema führt temperature=0
    path = tmp_path / "block-bad.json"
    path.write_text(json.dumps(violating), encoding="utf-8")
    rc = bench.main(["--schema", str(SCHEMA_PATH), "--constants", str(path)])
    assert rc != 0
    err = capsys.readouterr().err
    assert "FEHLER" in err and "temperature" in err


def test_cli_unknown_configuration_exits_nonzero(capsys):
    rc = bench.main(["--schema", str(SCHEMA_PATH), "--configuration", "nicht_vorhanden"])
    assert rc != 0
    assert "Unbekannte Konfiguration" in capsys.readouterr().err


def test_cli_without_arguments_prints_help(capsys):
    assert bench.main([]) == 0
    assert "gv_benchmark" in capsys.readouterr().out


# --- (4) Metrik-Aggregation aus einem Fixture-Lauf-Ergebnis -------------------


def _fixture_run() -> dict:
    """Ein Lauf-Ergebnis mit genau den Quellen, die der Harness kennt.

    Absichtlich unvollständig: ``wrong_memory_match``, ``main_agent_tokens``,
    ``voice_tokens`` und ``capability_reuse`` sind daraus NICHT ableitbar und
    müssen ``None`` bleiben.
    """
    return {
        "session_id": "bench-run-1",
        "configuration": "full_godsv",
        "final_state": "VERIFIED_COMPLETE",
        "started_at": "2026-09-14T10:00:00+00:00",
        "finished_at": "2026-09-14T10:04:12+00:00",
        "pytest": {"exit_code": 0, "passed": 42, "failed": 0},
        "usage": {"input_tokens": 12000, "output_tokens": 3000, "actual_cost_usd": 0.42},
        "crash_recovery": {"exit_code": 0, "findings": []},
        "supervisor": {"continuation_claimed": 1, "continuation_committed": 1},
        "cases": [
            {"id": "GS-02", "intervention_should_have_happened": True, "intervened": True},
            {"id": "GS-05", "intervention_should_have_happened": True, "intervened": True},
            {"id": "GS-01", "intervention_should_have_happened": False, "intervened": False},
            {"id": "GS-04", "intervention_should_have_happened": False, "intervened": True,
             "redirect_correct": False},
        ],
        "events": [
            {"kind": "tool_call", "step": 1, "payload": {}},
            {"kind": "tool_call", "step": 2, "payload": {}},
            {"kind": "tool_call", "step": 3, "payload": {}},
            {"kind": "contradiction", "step": 10, "payload": {"fingerprint": "abc"}},
            {"kind": "pause_requested", "step": 11, "payload": {}},
            {"kind": "pause_delivered", "step": 12, "payload": {}},
            {"kind": "pause_requested", "step": 20, "payload": {}},
            {"kind": "pause_no_action", "step": 21, "payload": {}},
            {"kind": "completion_rejected", "step": 13, "payload": {"state": "INCOMPLETE"}},
            {"kind": "file_edit", "step": 15, "payload": {}},
            {"kind": "completion_verdict", "step": 18, "payload": {"state": "VERIFIED_COMPLETE"}},
            {"kind": "tool_build_ready", "step": 30, "payload": {"name": "mean_check"}},
        ],
    }


def test_all_required_metrics_get_an_entry(schema):
    report = bench.aggregate_metrics(_fixture_run(), schema)
    for name in bench.REQUIRED_METRICS:
        assert name in report["metrics"], name
        entry = report["metrics"][name]
        assert set(entry) == {"value", "source", "reason"}
        if entry["value"] is None:
            assert entry["reason"], f"{name}: None ohne Begründung"


def test_effectiveness_metrics_from_the_fixture(schema):
    metrics = bench.aggregate_metrics(_fixture_run(), schema)["metrics"]
    assert metrics["task_success"]["value"] is True
    assert metrics["task_success"]["source"] == "pytest.exit_code"
    assert metrics["false_completion"]["value"] is False
    # contradiction bei Schritt 10 → erster Fix (file_edit) bei Schritt 15
    assert metrics["steps_to_correction"]["value"] == 5


def test_false_completion_is_flagged_when_evidence_contradicts_the_state(schema):
    run = _fixture_run()
    run["pytest"] = {"exit_code": 1, "failed": 3}
    metrics = bench.aggregate_metrics(run, schema)["metrics"]
    assert metrics["task_success"]["value"] is False
    assert metrics["false_completion"]["value"] is True


def test_intervention_quality_uses_the_ground_truth_labels(schema):
    metrics = bench.aggregate_metrics(_fixture_run(), schema)["metrics"]
    assert metrics["intervention_precision"]["value"] == 0.6667  # 2 TP / (2 TP + 1 FP)
    assert metrics["intervention_recall"]["value"] == 1.0        # 2 TP / (2 TP + 0 FN)
    assert metrics["false_intervention"]["value"] == 1
    assert metrics["missed_intervention"]["value"] == 0
    assert metrics["wrong_redirect"]["value"] == 1
    # 2 geplante Pausen, eine davon ohne Eingriff
    assert metrics["no_intervention_rate"]["value"] == 0.5


def test_cost_and_runtime_metrics(schema):
    metrics = bench.aggregate_metrics(_fixture_run(), schema)["metrics"]
    assert metrics["total_tokens"]["value"] == 15000
    assert metrics["cost"]["value"] == 0.42
    assert metrics["runtime_s"]["value"] == 252.0
    assert metrics["tool_calls"]["value"] == 3


def test_reliability_metrics(schema):
    metrics = bench.aggregate_metrics(_fixture_run(), schema)["metrics"]
    assert metrics["crash_recovery_ok"]["value"] is True
    assert metrics["exactly_once_continuation"]["value"] is True


def test_crash_double_effect_turns_crash_recovery_ok_false(schema):
    run = _fixture_run()
    run["crash_recovery"] = {
        "exit_code": 3,
        "findings": [
            {"kind": "double_effect", "detail": "replayed request applied 2x"},
        ],
    }
    metrics = bench.aggregate_metrics(run, schema)["metrics"]
    assert metrics["crash_recovery_ok"]["value"] is False


def test_capability_reuse_counts_only_real_reuse(schema):
    run = _fixture_run()
    metrics = bench.aggregate_metrics(run, schema)["metrics"]
    # nur ein tool_build_ready → gebaut ist nicht wiederverwendet
    assert metrics["capability_reuse"]["value"] is None
    assert "Wiederverwendungs-Event" in metrics["capability_reuse"]["reason"]

    run["events"].append({"kind": "skill_consumed", "step": 40, "payload": {"name": "mean_check"}})
    reused = bench.aggregate_metrics(run, schema)["metrics"]
    assert reused["capability_reuse"]["value"] == 1


def test_not_derivable_metrics_are_none_with_a_reason_never_guessed(schema):
    report = bench.aggregate_metrics(_fixture_run(), schema)
    for name in ("main_agent_tokens", "voice_tokens", "wrong_memory_match"):
        entry = report["metrics"][name]
        assert entry["value"] is None, name
        assert entry["source"] is None, name
        assert entry["reason"], name
    assert set(report["not_derivable"]) == {
        "main_agent_tokens",
        "voice_tokens",
        "wrong_memory_match",
        "capability_reuse",
    }
    assert report["reasons"]["wrong_memory_match"]


def test_precision_and_recall_stay_none_without_ground_truth(schema):
    run = _fixture_run()
    run.pop("cases")
    report = bench.aggregate_metrics(run, schema)
    for name in (
        "intervention_precision",
        "intervention_recall",
        "false_intervention",
        "missed_intervention",
        "wrong_redirect",
    ):
        assert report["metrics"][name]["value"] is None, name
        assert "Ground Truth" in report["metrics"][name]["reason"]
    # die unabhängige Ausfallrate bleibt berechenbar
    assert report["metrics"]["no_intervention_rate"]["value"] == 0.5


def test_empty_run_result_derives_nothing_and_invents_nothing(schema):
    report = bench.aggregate_metrics({}, schema)
    assert report["derived"] == []
    assert len(report["not_derivable"]) == len(
        [n for n in bench.REQUIRED_METRICS if n in report["metrics"]]
    )


def test_completion_state_without_pytest_evidence_is_not_a_success(schema):
    """VERIFIED_COMPLETE ohne messbaren Testlauf bleibt None — keine Behauptung."""
    run = {"final_state": "VERIFIED_COMPLETE", "events": []}
    metrics = bench.aggregate_metrics(run, schema)["metrics"]
    assert metrics["task_success"]["value"] is None
    assert "objektives Ausführungs-Signal" in metrics["task_success"]["reason"]
    assert metrics["false_completion"]["value"] is None
    assert "unbelegt" in metrics["false_completion"]["reason"]


def test_task_success_field_is_accepted_when_no_test_output_exists(schema):
    run = {"final_state": "VERIFIED_COMPLETE", "task_success": True, "events": []}
    metrics = bench.aggregate_metrics(run, schema)["metrics"]
    assert metrics["task_success"]["value"] is True
    assert metrics["task_success"]["source"] == "run_result.task_success"
    assert metrics["false_completion"]["value"] is False


def test_failed_counter_without_exit_code_is_enough(schema):
    run = {"final_state": "INCOMPLETE", "pytest": {"failed": 2}, "events": []}
    metrics = bench.aggregate_metrics(run, schema)["metrics"]
    assert metrics["task_success"]["value"] is False
    assert metrics["task_success"]["source"] == "pytest.failed"
    assert metrics["false_completion"]["value"] is False


def test_events_can_come_from_an_event_store_db(schema, tmp_path):
    from skaldir.events import EventStore

    db = tmp_path / "events.db"
    store = EventStore(db)
    store.append("bench-db-1", 1, "tool_call", {"cmd": "pytest -q"})
    store.append("bench-db-1", 2, "tool_call", {"cmd": "ruff check"})
    store.close()

    metrics = bench.aggregate_metrics({"events_path": str(db)}, schema)["metrics"]
    assert metrics["tool_calls"]["value"] == 2


def test_unreadable_event_source_marks_everything_not_derivable(schema, tmp_path):
    report = bench.aggregate_metrics({"events_path": str(tmp_path / "nope.db")}, schema)
    assert report["derived"] == []
    assert not (tmp_path / "nope.db").exists()  # kein Anlegen einer leeren DB
    assert all("Events nicht lesbar" in r for r in report["reasons"].values())


def test_estimated_tokens_are_never_reported_as_measured(schema):
    run = _fixture_run()
    run.pop("usage")
    run["eval"] = {"research_tokens_est": 4321}
    metrics = bench.aggregate_metrics(run, schema)["metrics"]
    assert metrics["total_tokens"]["value"] is None
    assert "geschätzte" in metrics["total_tokens"]["reason"]
    assert "4321" in metrics["total_tokens"]["reason"]


def test_cli_metrics_from_writes_a_json_file(schema, tmp_path):
    run_path = tmp_path / "run.json"
    run_path.write_text(json.dumps(_fixture_run()), encoding="utf-8")
    out_path = tmp_path / "metrics.json"
    rc = bench.main(
        [
            "--schema", str(SCHEMA_PATH),
            "--metrics-from", str(run_path),
            "--out", str(out_path),
        ]
    )
    assert rc == 0
    written = json.loads(out_path.read_text(encoding="utf-8"))
    assert written["metrics"]["task_success"]["value"] is True
    assert "main_agent_tokens" in written["not_derivable"]


# --- (5) run_record im Schema-Format -----------------------------------------


def test_run_record_has_exactly_the_schema_fields(schema):
    record = bench.run_record_from_run_result(
        _fixture_run(), schema=schema, configuration="full_godsv", notes="Fixture"
    )
    assert list(record) == list(bench.RUN_RECORD_FIELDS)
    schema_fields = [k for k in schema["run_record"] if not k.startswith("$")]
    assert list(record) == schema_fields


def test_run_record_carries_the_run_data(schema):
    run = _fixture_run()
    run.update(
        {
            "run_id": "run-2026-09-14-001",
            "goal_revision": 3,
            "evidence_refs": ["/tmp/gv/events.db", "pytest:exit=0"],
            "artifact_refs": ["tools/gv_benchmark.py"],
        }
    )
    record = bench.run_record_from_run_result(run, schema=schema, configuration="full_godsv")
    assert record["run_id"] == "run-2026-09-14-001"
    assert record["session_id"] == "bench-run-1"
    assert record["goal_revision"] == 3
    assert record["configuration"] == "full_godsv"
    assert record["started_at"] == "2026-09-14T10:00:00+00:00"
    assert record["finished_at"] == "2026-09-14T10:04:12+00:00"
    assert record["final_state"] == "VERIFIED_COMPLETE"
    assert record["evidence_refs"] == ["/tmp/gv/events.db", "pytest:exit=0"]
    assert record["artifact_refs"] == ["tools/gv_benchmark.py"]
    # Incidents kommen aus den Event-Payloads, nicht aus dem Nichts
    assert [i["kind"] for i in record["incidents"]] == ["contradiction"]
    assert [c["name"] for c in record["capabilities_learned"]] == ["mean_check"]


def test_run_record_final_state_comes_from_the_last_verdict_event(schema):
    run = _fixture_run()
    run.pop("final_state")
    record = bench.run_record_from_run_result(run, schema=schema, configuration="full_godsv")
    assert record["final_state"] == "VERIFIED_COMPLETE"


def test_run_record_without_any_state_is_unknown_and_says_so(schema):
    record = bench.run_record_from_run_result(
        {"session_id": "s1", "events": []}, schema=schema, configuration="hermes_baseline"
    )
    assert record["final_state"] == "UNKNOWN"
    assert "UNKNOWN" in record["notes"]
    assert record["started_at"] == "" and record["finished_at"] == ""


def test_run_record_notes_that_a_run_id_was_derived(schema):
    run = _fixture_run()
    run.pop("run_id", None)
    record = bench.run_record_from_run_result(run, schema=schema, configuration="full_godsv")
    assert record["run_id"].startswith("bench-run-1-")
    assert "run_id aus session_id" in record["notes"]


def test_run_record_rejects_a_configuration_that_is_not_in_the_schema(schema):
    with pytest.raises(bench.ComparabilityError) as excinfo:
        bench.build_run_record(
            run_id="r1",
            session_id="s1",
            configuration="full_godsv_plus",
            final_state="VERIFIED_COMPLETE",
            schema=schema,
        )
    assert "full_godsv_plus" in str(excinfo.value)


def test_run_record_requires_its_mandatory_fields(schema):
    with pytest.raises(bench.ComparabilityError) as excinfo:
        bench.build_run_record(
            run_id="", session_id="s1", configuration="full_godsv",
            final_state="VERIFIED_COMPLETE", schema=schema,
        )
    message = str(excinfo.value)
    assert "run_id" in message
    assert "Pflichtfelder" in message


def test_run_record_goal_revision_stays_none_when_the_run_has_none(schema):
    record = bench.run_record_from_run_result(
        _fixture_run(), schema=schema, configuration="full_godsv"
    )
    assert record["goal_revision"] is None


def test_run_record_can_be_written_as_json(schema, tmp_path):
    out_path = tmp_path / "nested" / "run_record.json"
    record = bench.run_record_from_run_result(
        _fixture_run(), schema=schema, configuration="full_godsv_memory"
    )
    written = bench.write_json(out_path, {"run_record": record})
    assert written == out_path
    loaded = json.loads(out_path.read_text(encoding="utf-8"))
    assert loaded["run_record"]["configuration"] == "full_godsv_memory"
    assert list(loaded["run_record"]) == list(bench.RUN_RECORD_FIELDS)


def test_cli_run_record_from_writes_the_schema_format(schema, tmp_path, capsys):
    run_path = tmp_path / "run.json"
    run = _fixture_run()
    run["run_id"] = "cli-run-7"
    run_path.write_text(json.dumps(run), encoding="utf-8")
    out_path = tmp_path / "run_record.json"

    rc = bench.main(
        [
            "--schema", str(SCHEMA_PATH),
            "--run-record-from", str(run_path),
            "--configuration", "full_godsv_capabilities",
            "--out", str(out_path),
        ]
    )
    assert rc == 0
    record = json.loads(out_path.read_text(encoding="utf-8"))["run_record"]
    assert record["run_id"] == "cli-run-7"
    assert record["configuration"] == "full_godsv_capabilities"
    assert set(record) == set(bench.RUN_RECORD_FIELDS)
    assert "json ->" in capsys.readouterr().out


def test_cli_run_record_without_configuration_exits_nonzero(tmp_path, capsys):
    run_path = tmp_path / "run.json"
    run_path.write_text(json.dumps(_fixture_run()), encoding="utf-8")
    rc = bench.main(["--schema", str(SCHEMA_PATH), "--run-record-from", str(run_path)])
    assert rc != 0
    assert "--configuration" in capsys.readouterr().err


def test_cli_zero_run_invents_no_metrics(schema, tmp_path):
    """Ein komplett leerer Lauf ergibt nur None-Werte — kein erfundener Erfolg."""
    run_path = tmp_path / "empty.json"
    run_path.write_text(json.dumps({}), encoding="utf-8")
    out_path = tmp_path / "metrics.json"
    rc = bench.main(
        ["--schema", str(SCHEMA_PATH), "--metrics-from", str(run_path), "--out", str(out_path)]
    )
    assert rc == 0
    written = json.loads(out_path.read_text(encoding="utf-8"))
    assert written["derived"] == []
    assert written["metrics"]["task_success"]["value"] is None




