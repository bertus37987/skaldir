#!/usr/bin/env python3
"""Benchmark-Harness für God's Voice (Master-§37/§43) — Vergleichsläufe nach Schema.

Wahrheitsquelle ist ``docs/BENCHMARK-RUN-SCHEMA.json``. Dieser Harness erzeugt
NICHTS von selbst: er lädt und validiert das Schema, lädt eine benannte
Konfiguration, erzwingt die Vergleichbarkeit der Konstanten, aggregiert Metriken
aus vorhandenen Lauf-Ergebnissen und schreibt einen ``run_record`` im
Schema-Format.

Zwei Regeln sind hart:

1. **Konstanten NIE variieren.** Ein Vergleichsblock, in dem eine geforderte
   Konstante fehlt, leer ist oder zwischen Konfigurationen abweicht, ist
   wertlos — der Harness bricht dann LAUT ab (``ComparabilityError``,
   CLI-Exit != 0). Es gibt keinen "weiter so"-Pfad.
2. **NIE schätzen.** Eine Metrik, die aus dem vorliegenden Lauf-Ergebnis nicht
   ableitbar ist, wird als ``None`` mit Begründung eingetragen — niemals als
   ``0``, ``False`` oder interpolierter Wert.

Kein Modellaufruf, kein LLM, kein Netz: reine Funktionen + argparse-CLI.

Nutzung::

    .venv/bin/python tools/gv_benchmark.py --validate-schema
    .venv/bin/python tools/gv_benchmark.py --list-configurations
    .venv/bin/python tools/gv_benchmark.py --configuration full_godsv_capabilities
    .venv/bin/python tools/gv_benchmark.py --constants /tmp/gv_comparability.json
    .venv/bin/python tools/gv_benchmark.py --metrics-from /tmp/run.json --out /tmp/metrics.json
    .venv/bin/python tools/gv_benchmark.py --run-record-from /tmp/run.json \
        --configuration full_godsv --out /tmp/run_record.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SCHEMA = ROOT / "docs" / "BENCHMARK-RUN-SCHEMA.json"

SCHEMA_VERSION = "1.0"

# Die Konstanten, die das Schema führen MUSS (Vergleichbarkeit §37): Modell,
# exakte Version, Reasoning, Tools, Environment, Context, Timeout,
# Token-/Kostenbudget, Retries, Temperature, Seed (+ Workspace-Baseline, Voice-Modus).
CONSTANT_KEYS = (
    "main_model",
    "exact_model_version",
    "reasoning",
    "tools",
    "environment",
    "context_window",
    "timeout_s",
    "token_budget",
    "cost_budget",
    "retries",
    "temperature",
    "seed",
    "workspace_baseline_commit",
    "voice_mode",
)

# Fähigkeits-Schalter je Konfiguration (§37 Stufenfolge).
SWITCH_KEYS = (
    "completion",
    "watcher",
    "investigator",
    "memory",
    "learned_capabilities",
)

CONFIGURATION_IDS = (
    "hermes_baseline",
    "hermes_completion",
    "hermes_watcher",
    "hermes_watcher_investigator",
    "full_godsv",
    "full_godsv_memory",
    "full_godsv_capabilities",
)

METRIC_GROUPS = ("effectiveness", "intervention_quality", "cost", "reliability")

# Metriken, die das Schema führen MUSS (Master-§37 Metrikset + wrong_memory_match).
REQUIRED_METRICS = (
    "task_success",
    "false_completion",
    "steps_to_correction",
    "intervention_precision",
    "intervention_recall",
    "false_intervention",
    "missed_intervention",
    "no_intervention_rate",
    "wrong_redirect",
    "wrong_memory_match",
    "total_tokens",
    "main_agent_tokens",
    "voice_tokens",
    "cost",
    "runtime_s",
    "tool_calls",
    "crash_recovery_ok",
    "exactly_once_continuation",
    "capability_reuse",
)

RUN_RECORD_FIELDS = (
    "run_id",
    "session_id",
    "goal_revision",
    "configuration",
    "started_at",
    "finished_at",
    "final_state",
    "evidence_refs",
    "artifact_refs",
    "incidents",
    "capabilities_learned",
    "notes",
)

GOLDEN_SET_IDS = tuple(f"GS-{n:02d}" for n in range(1, 16))


class ComparabilityError(RuntimeError):
    """Ein Vergleichsblock verletzt die Konstanten-Regel (§37) — fail-loud.

    Die Meldung nennt jede verletzte Konstante einzeln mit den abweichenden
    Werten; es gibt keinen stillen Korrektur- oder Default-Pfad.
    """


# --- 1) Schema laden + validieren --------------------------------------------


def load_schema(path: str | Path = DEFAULT_SCHEMA) -> dict:
    """Schema-JSON laden. Fehlt/kaputt → ComparabilityError (fail-loud)."""
    p = Path(path)
    if not p.is_file():
        raise ComparabilityError(f"Schema nicht gefunden: {p}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ComparabilityError(f"Schema ist kein gültiges JSON ({p}): {exc}") from exc
    if not isinstance(data, dict):
        raise ComparabilityError(f"Schema-Wurzel muss ein Objekt sein ({p})")
    return data


def _held_constant_keys(schema: dict) -> list[str]:
    held = schema.get("held_constant") or {}
    return [k for k in held if not k.startswith("$")]


def _metric_names(schema: dict) -> list[str]:
    metrics = schema.get("metrics") or {}
    names: list[str] = []
    for group in METRIC_GROUPS:
        for name in metrics.get(group) or []:
            names.append(str(name))
    return names


def validate_schema(schema: dict) -> list[str]:
    """Strukturprüfung des Schemas — gibt die Probleme zurück (leer = ok)."""
    problems: list[str] = []
    if not isinstance(schema, dict):
        return ["Schema-Wurzel ist kein Objekt"]
    for key in ("schema_version", "held_constant", "configurations", "metrics",
                "task_suite", "run_record"):
        if key not in schema:
            problems.append(f"Top-Level-Schlüssel fehlt: {key}")
    if problems:
        return problems

    if str(schema.get("schema_version")) != SCHEMA_VERSION:
        problems.append(
            f"schema_version unerwartet: {schema.get('schema_version')!r}"
            f" (Harness kennt {SCHEMA_VERSION!r})"
        )

    # held_constant: jeder geforderte Schlüssel muss geführt werden
    held_keys = _held_constant_keys(schema)
    if not held_keys:
        problems.append("held_constant führt keine Konstanten")
    for key in CONSTANT_KEYS:
        if key not in held_keys:
            problems.append(f"held_constant fehlt geforderte Konstante: {key}")

    # configurations: alle 7 Stufen, boolesche Schalter
    configs = schema.get("configurations")
    if not isinstance(configs, list) or not configs:
        problems.append("configurations ist keine nicht-leere Liste")
    else:
        seen: set[str] = set()
        for entry in configs:
            if not isinstance(entry, dict):
                problems.append(f"configuration-Eintrag ist kein Objekt: {entry!r}")
                continue
            cid = entry.get("id")
            if not isinstance(cid, str) or not cid:
                problems.append(f"configuration ohne id: {entry!r}")
                continue
            seen.add(cid)
            for switch in SWITCH_KEYS:
                if not isinstance(entry.get(switch), bool):
                    problems.append(f"{cid}: Schalter {switch} fehlt oder ist nicht boolesch")
        for cid in CONFIGURATION_IDS:
            if cid not in seen:
                problems.append(f"geforderte Konfiguration fehlt: {cid}")
        dupes = {c for c in seen if [e.get("id") for e in configs].count(c) > 1}
        for cid in sorted(dupes):
            problems.append(f"Konfiguration doppelt geführt: {cid}")

    # metrics: vier Gruppen, alle geforderten Metriken
    names = set(_metric_names(schema))
    metrics = schema.get("metrics") or {}
    for group in METRIC_GROUPS:
        if not isinstance(metrics.get(group), list) or not metrics.get(group):
            problems.append(f"metrics.{group} fehlt oder ist leer")
    for name in REQUIRED_METRICS:
        if name not in names:
            problems.append(f"geforderte Metrik fehlt im Schema: {name}")

    # task_suite: 15 Golden-Set-Fälle + E2E-Kette
    suite = schema.get("task_suite") or {}
    cases = ((suite.get("golden_set") or {}).get("cases")) or []
    if len(cases) != len(GOLDEN_SET_IDS):
        problems.append(f"golden_set.cases: {len(cases)} statt {len(GOLDEN_SET_IDS)}")
    case_ids = {str(c.get("id")) for c in cases if isinstance(c, dict)}
    for gid in GOLDEN_SET_IDS:
        if gid not in case_ids:
            problems.append(f"Golden-Set-Fall fehlt: {gid}")
    for case in cases:
        if isinstance(case, dict) and not isinstance(case.get("intervention_expected"), bool):
            problems.append(
                f"Golden-Set-Fall {case.get('id')!r} ohne boolesches intervention_expected"
            )
    stages = ((suite.get("e2e_chain") or {}).get("stages")) or []
    if not stages:
        problems.append("task_suite.e2e_chain.stages ist leer")

    # run_record: alle Felder geführt
    record = schema.get("run_record")
    if not isinstance(record, dict):
        problems.append("run_record ist kein Objekt")
    else:
        for field in RUN_RECORD_FIELDS:
            if field not in record:
                problems.append(f"run_record fehlt Feld: {field}")

    return problems


def load_and_validate_schema(path: str | Path = DEFAULT_SCHEMA) -> dict:
    """Schema laden UND validieren — bei Problemen fail-loud."""
    schema = load_schema(path)
    problems = validate_schema(schema)
    if problems:
        raise ComparabilityError("Schema ungültig:\n- " + "\n- ".join(problems))
    return schema


# --- 2) Konfiguration + Fähigkeits-Schalter ----------------------------------


def list_configurations(schema: dict) -> list[dict]:
    """Alle Konfigurationen in Schema-Reihenfolge (je mit id, label, Schaltern)."""
    out: list[dict] = []
    for entry in schema.get("configurations") or []:
        if not isinstance(entry, dict):
            continue
        cid = str(entry.get("id") or "")
        if not cid:
            continue
        out.append(
            {
                "id": cid,
                "label": str(entry.get("label") or ""),
                **{switch: bool(entry.get(switch)) for switch in SWITCH_KEYS},
            }
        )
    return out


def load_configuration(schema: dict, config_id: str) -> dict:
    """Eine benannte Konfiguration laden. Unbekannte id → fail-loud (§37)."""
    for entry in list_configurations(schema):
        if entry["id"] == config_id:
            return entry
    known = ", ".join(e["id"] for e in list_configurations(schema))
    raise ComparabilityError(f"Unbekannte Konfiguration {config_id!r}. Bekannt: {known}")


def configuration_switches(schema: dict, config_id: str) -> dict[str, bool]:
    """Nur die Fähigkeits-Schalter einer Konfiguration."""
    return {switch: load_configuration(schema, config_id)[switch] for switch in SWITCH_KEYS}


# --- 3) Konstanten-Vergleichsblock (§37) — fail-loud --------------------------


def _is_unset(value: object) -> bool:
    """Fehlend/leer ist NICHT gleich 'bewusst gesetzt' — auch nicht für 0/False.

    ``0``, ``0.0`` und ``False`` sind gültige, bewusst gesetzte Konstanten
    (z. B. temperature=0, retries=0); nur ``None``, ``""``, ``[]`` und ``{}``
    gelten als nicht gesetzt.
    """
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, (list, dict, tuple, set)):
        return len(value) == 0
    return False


def required_constant_keys(schema: dict) -> list[str]:
    """Konstanten, die das Schema führt UND der Harness fordert."""
    held = set(_held_constant_keys(schema))
    return [key for key in CONSTANT_KEYS if key in held]


def check_constants(
    schema: dict,
    observed: dict,
    *,
    label: str = "observed",
    require_schema_reference: bool = False,
) -> dict[str, object]:
    """Erzwingt identische Konstanten über einen Vergleichsblock (fail-loud).

    Geprüft wird je geforderter Konstante:

    * **identisch im Block** — sobald zwei Läufe eines Vergleichsblocks
      unterschiedliche Werte führen, bricht der Vergleich ab. ``tools`` und
      andere Listen werden dabei als Menge verglichen (Reihenfolge ist keine
      Konstante).
    * **gesetzt** — ein leerer/fehlender Wert (``""``, ``None``, ``[]``) ist eine
      Vergleichsverletzung, kein "unbekannt": mit ihm ist die Vergleichbarkeit
      nicht belegbar. ``0``/``0.0``/``False`` bleiben gültige gesetzte Werte
      (``temperature=0``, ``retries=0``).
    * **Schema-Referenz** — führt das Schema einen Wert (nicht leer) und weicht
      der beobachtete davon ab, bricht der Vergleich ab. Führt das Schema an
      dieser Stelle noch einen **Platzhalter** (``""``/``null``/``[]``, wie im
      Schema-Stand 1.0 für ``exact_model_version``, ``reasoning``, ``tools`` und
      Budgets), wird das **nicht** verschwiegen, sondern als
      ``schema_reference_missing`` zurückgemeldet; mit
      ``require_schema_reference=True`` ist es eine Verletzung.

    ``observed`` darf eine einzelne Beobachtung (dict) oder ein Vergleichsblock
    mehrerer Läufe sein: ``{key: wert}``, ``{"runs": {...}}`` bzw.
    ``{"configurations": {...}}``.

    Rückgabe: ``{"ok": True, "constants": {...}, "runs": [...],
    "schema_reference_missing": [...]}`` — andernfalls
    :class:`ComparabilityError` mit jeder Verletzung einzeln.
    """
    keys = required_constant_keys(schema)
    missing_keys = [key for key in CONSTANT_KEYS if key not in set(_held_constant_keys(schema))]
    held = {k: v for k, v in (schema.get("held_constant") or {}).items() if not k.startswith("$")}

    violations: list[str] = []
    for key in missing_keys:
        violations.append(f"{key}: fehlt im Schema-held_constant (nicht prüfbar)")

    block = _constant_block(observed)
    if not block:
        violations.append(f"{label}: kein Beobachtungsblock übergeben (nichts zu vergleichen)")

    values: dict[str, object] = {}
    unfilled: list[str] = []
    for key in (keys if block else []):
        reference = held.get(key)
        per_run = [
            (run_label, entry.get(key) if isinstance(entry, dict) else None)
            for run_label, entry in block.items()
        ]

        if _is_unset(reference):
            unfilled.append(key)
            if require_schema_reference:
                violations.append(
                    f"{key}: Schema führt keinen Referenzwert — mit "
                    "require_schema_reference=true ist das ein Abbruchgrund"
                )

        unset_runs = [run for run, value in per_run if _is_unset(value)]
        if unset_runs:
            violations.append(
                f"{key}: nicht gesetzt in {', '.join(unset_runs)} "
                f"(leer ist eine Vergleichsverletzung, nicht 'unbekannt')"
            )
            continue

        normalized = {run: _normalize_constant(value) for run, value in per_run}
        distinct = {json.dumps(value, sort_keys=True, default=str) for value in normalized.values()}
        if len(distinct) > 1:
            detail = "; ".join(
                f"{run}={json.dumps(value, sort_keys=True, default=str)}"
                for run, value in normalized.items()
            )
            violations.append(f"{key}: nicht identisch im Vergleichsblock ({detail})")
            continue

        observed_value = next(iter(normalized.values()))
        if not _is_unset(reference) and _normalize_constant(reference) != observed_value:
            violations.append(
                f"{key}: weicht vom Schema-Referenzwert ab "
                f"(schema={json.dumps(reference, sort_keys=True, default=str)}, "
                f"beobachtet={json.dumps(observed_value, sort_keys=True, default=str)})"
            )
            continue

        values[key] = observed_value

    if violations:
        raise ComparabilityError(
            f"Konstanten-Prüfung fehlgeschlagen ({len(violations)} Verletzung(en)) — "
            "Vergleich wäre wertlos:\n- " + "\n- ".join(violations)
        )
    return {
        "ok": True,
        "constants": values,
        "runs": sorted(block),
        "schema_reference_missing": unfilled,
    }


def _constant_block(observed: dict) -> dict[str, dict]:
    """Beobachtung(en) auf ein Mapping ``run_label -> {konstante: wert}`` bringen.

    Akzeptierte Formen:

    * ``{"main_model": ..., "seed": ...}`` — eine einzelne Beobachtung
    * ``{"run_a": {...}, "run_b": {...}}`` — bereits ein Vergleichsblock
    * ``{"runs": {...}}`` / ``{"configurations": {...}}`` — benannter Block
    """
    if not isinstance(observed, dict) or not observed:
        return {}
    for wrapper in ("runs", "configurations", "block"):
        inner = observed.get(wrapper)
        if isinstance(inner, dict) and inner:
            return _constant_block(inner)
    if any(key in observed for key in CONSTANT_KEYS):
        return {"observed": {k: v for k, v in observed.items() if not k.startswith("$")}}
    block: dict[str, dict] = {}
    for label, entry in observed.items():
        if isinstance(entry, dict):
            block[str(label)] = {k: v for k, v in entry.items() if not k.startswith("$")}
    return block


def _normalize_constant(value: object) -> object:
    """Werte vergleichbar machen, ohne sie umzudeuten.

    * ``tools`` (und andere Listen) werden als sortierte Menge verglichen — die
      Reihenfolge einer Tool-Liste ist keine Konstante.
    * ``0`` und ``0.0`` sind derselbe Konstantenwert (``temperature=0``), werden
      also auf eine Zahlform gebracht.
    * ``False``/``True`` bleiben boolesch und damit von ``0``/``1``
      unterscheidbar — ein Schalter ist keine Zahl.
    """
    if isinstance(value, (list, tuple, set)):
        return sorted(json.dumps(item, sort_keys=True, default=str) for item in value)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return float(value)
    return value


def constants_report(
    schema: dict,
    observed: dict,
    *,
    label: str = "observed",
    require_schema_reference: bool = False,
) -> dict:
    """Prüfergebnis statt Ausnahme — für CLI-Ausgabe/JSON, ohne fail-loud."""
    try:
        result = check_constants(
            schema,
            observed,
            label=label,
            require_schema_reference=require_schema_reference,
        )
    except ComparabilityError as exc:
        return {"ok": False, "error": str(exc), "violations": _violations_of(exc)}
    return dict(result)


def _violations_of(exc: ComparabilityError) -> list[str]:
    text = str(exc)
    _, _, body = text.partition(":\n- ")
    return body.split("\n- ") if body else []


# --- 4) Metrik-Aggregation aus vorhandenen Quellen ---------------------------
#
# Eingabe ist ein Lauf-Ergebnis (dict). Erkannte Quellen (alle optional, keine
# erfundenen)::
#
#   events        Liste von Event-dicts (kind, step, ts, payload) ODER Pfad zu
#                 einer events.db (über godsv.events.EventStore gelesen)
#   pytest        {"exit_code": 0, "passed": n, "failed": n, "summary": "..."}
#   eval          beliebiges Eval-JSON (tools/gv_eval_longhorizon.py --json)
#   usage         host-gemessene Nutzung (godsv.metrics.measured_session_usage)
#   crash_recovery {"exit_code": 0, "findings": [{"kind": "double_effect", ...}]}
#   supervisor    {"continuation_claimed": n, "continuation_committed": n}
#   cases         Liste je Fall: intervention_should_have_happened, intervened,
#                 redirect_correct, memory_match_correct
#   final_state   Endzustand des Runs ("VERIFIED_COMPLETE" / "INCOMPLETE" / …)
#
# Jede Metrik des Schemas bekommt einen Eintrag:
#   {"value": <messwert|None>, "source": <herkunft|None>, "reason": <text|None>}
# ``value=None`` heißt: aus diesen Daten nicht ableitbar — ``reason`` sagt, warum.
# Ein fehlender Messwert wird NIE durch 0/False/"" ersetzt.

COMPLETE_STATES = frozenset({"VERIFIED_COMPLETE", "COMPLETE", "verified_complete"})
INCOMPLETE_STATES = frozenset({"INCOMPLETE", "FAILED", "ABORTED", "incomplete", "failed"})


def _metric(value: object, source: str, reason: str | None = None) -> dict:
    return {"value": value, "source": source, "reason": reason}


def _not_derivable(reason: str) -> dict:
    return {"value": None, "source": None, "reason": reason}


def load_events(run_result: dict) -> tuple[list[dict], str | None]:
    """Events aus ``run_result`` holen — Liste oder events.db-Pfad.

    Gibt ``(events, fehler)`` zurück; ``fehler`` ist gesetzt, wenn eine
    angegebene Quelle nicht lesbar war (dann ist keine Ableitung möglich).
    """
    raw = run_result.get("events")
    if raw is None:
        path = run_result.get("events_path")
        if not path:
            return [], None
        if not Path(path).is_file():
            # NIE eine leere DB anlegen: ein Tippfehler im Pfad wäre sonst ein
            # Lauf ohne Events statt ein Fehler.
            return [], f"events_path existiert nicht ({path})"
        try:
            from godsv.events import EventStore
        except Exception as exc:  # pragma: no cover — Import sollte stehen
            return [], f"events_path angegeben, Import fehlgeschlagen: {exc}"
        try:
            store = EventStore(path)
            try:
                rows = store.stream()
            finally:
                store.close()
        except Exception as exc:
            return [], f"events_path nicht lesbar ({path}): {exc}"  # noqa: TRY003
        return [
            {
                "kind": getattr(e, "kind", None),
                "step": getattr(e, "step", None),
                "ts": getattr(e, "ts", None),
                "session_id": getattr(e, "session_id", None),
                "payload": getattr(e, "payload", {}) or {},
            }
            for e in rows
        ], None
    if isinstance(raw, list):
        out: list[dict] = []
        for item in raw:
            if isinstance(item, dict):
                out.append(item)
            else:  # Event-Dataclass o. ä.
                out.append(
                    {
                        "kind": getattr(item, "kind", None),
                        "step": getattr(item, "step", None),
                        "ts": getattr(item, "ts", None),
                        "payload": getattr(item, "payload", {}) or {},
                    }
                )
        return out, None
    return [], "events hat einen unbekannten Typ (weder Liste noch events_path)"


def _of_kind(events: list[dict], kinds: str | tuple[str, ...]) -> list[dict]:
    wanted = (kinds,) if isinstance(kinds, str) else tuple(kinds)
    return [e for e in events if str(e.get("kind") or "") in wanted]


def _payload(event: dict) -> dict:
    payload = event.get("payload")
    return payload if isinstance(payload, dict) else {}


def _step(event: dict) -> int | None:
    step = event.get("step")
    return step if isinstance(step, int) else None


def _num(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _task_outcome(run_result: dict) -> tuple[dict, dict]:
    """``(task_success, false_completion)`` aus Pytest-Ausgabe und Endzustand.

    ``task_success`` ist nur belegbar, wenn ein reales Signal vorliegt: der
    Pytest-Exit-Code ODER ein Endzustand aus ``final_state``. Ein
    ``VERIFIED_COMPLETE`` ohne messbaren Testlauf bleibt ``None`` — der Zustand
    ist eine Behauptung des Agenten, kein Nachweis (der Nachweis steht im Gate).
    """
    pytest_info = run_result.get("pytest")
    pytest_rc = (
        _num(pytest_info.get("exit_code")) if isinstance(pytest_info, dict) else None
    )
    failed = _num(pytest_info.get("failed")) if isinstance(pytest_info, dict) else None
    explicit = run_result.get("task_success")
    final_state = run_result.get("final_state")
    state = str(final_state) if final_state is not None else ""

    # task_success braucht ein OBJEKTIVES Ausführungs-Signal. Der Endzustand allein
    # ist die Behauptung des Laufs — und genau gegen diese Behauptung wird
    # false_completion gemessen. Ohne Signal: None, nicht True/False.
    if pytest_rc is not None:
        success = _metric(pytest_rc == 0, "pytest.exit_code")
        objective: bool | None = pytest_rc == 0
    elif failed is not None:
        success = _metric(failed == 0, "pytest.failed")
        objective = failed == 0
    elif isinstance(explicit, bool):
        success = _metric(explicit, "run_result.task_success")
        objective = explicit
    else:
        success = _not_derivable(
            "kein objektives Ausführungs-Signal (Pytest-Exit/Zähler) und kein "
            "task_success-Feld — ein Endzustand ist eine Behauptung, kein Nachweis"
        )
        objective = None

    # falsche Fertigmeldung: der Lauf behauptet 'fertig', die Evidenz widerlegt ihn
    if state in COMPLETE_STATES:
        if objective is None:
            false_completion = _not_derivable(
                f"final_state={state}, aber keine Evidenz im Lauf-Ergebnis — "
                "'fertig' ist unbelegt, also auch keine widerlegte Fertigmeldung messbar"
            )
        else:
            false_completion = _metric(not objective, "final_state+pytest-Evidenz")
    elif state in INCOMPLETE_STATES:
        # kein Fertig-Anspruch → keine falsche Fertigmeldung (auch ohne Evidenz belegt)
        false_completion = _metric(False, "final_state ohne Fertig-Anspruch")
    else:
        false_completion = _not_derivable("kein abschließender Zustand im Lauf-Ergebnis")
    return success, false_completion


def _steps_to_correction(events: list[dict], run_result: dict) -> dict:
    """Schritte vom ersten Widerspruch bis zur ersten Wiederaufnahme/Fix.

    Ableitbar nur, wenn beide Zeitpunkte als Event vorliegen: ein
    Widerspruch/Signal VOR einer Korrektur (``completion_verdict`` ohne
    Rejection, ``guard_check`` bestanden, ``pause_delivered`` gefolgt von einem
    späteren Event) — konkret gezählt wird ab dem ersten ``contradiction``
    (oder falls nicht vorhanden: erstem ``guard_check`` mit Rejection) bis zum
    ersten darauffolgenden ``completion_verdict``/Fix-Event.
    """
    explicit = (run_result.get("eval") or {}).get("steps_to_verified") if isinstance(
        run_result.get("eval"), dict
    ) else None
    if isinstance(explicit, int) and not isinstance(explicit, bool):
        return _metric(explicit, "eval.steps_to_verified")

    starts = _of_kind(events, ("contradiction", "completion_rejected"))
    if not starts:
        return _not_derivable(
            "kein contradiction/completion_rejected Event — es gab keinen "
            "messbaren Widerspruchspunkt (nicht dasselbe wie 0 Schritte)"
        )
    start_step = _step(starts[0])
    if start_step is None:
        return _not_derivable("Widerspruchs-Event ohne numerischen step")
    fixes = [
        e
        for e in _of_kind(events, ("completion_verdict", "file_edit", "test_run"))
        if (_step(e) or -1) > start_step
    ]
    if not fixes:
        return _not_derivable(
            "kein Korrektur-Event nach dem Widerspruch — bis zum Laufende wurde "
            "keine Korrektur beobachtet (kein 0-Wert, sondern kein Wert)"
        )
    end_step = _step(fixes[0])
    if end_step is None:
        return _not_derivable("Korrektur-Event ohne numerischen step")
    return _metric(end_step - start_step, "events: contradiction → correction step delta")


def _intervention_counts(run_result: dict) -> tuple[dict, str | None]:
    """Precision/Recall/FP/FN aus Fällen MIT Ground-Truth-Label.

    Ohne explizite Ground Truth (``intervention_should_have_happened``) sind
    Precision/Recall nicht berechenbar — die Bewertung „hätte eingegriffen
    werden sollen" ist eine Annotation, keine Messung. Aus den Events wird sie
    NICHT abgeleitet.
    """
    cases = run_result.get("cases")
    if not isinstance(cases, list) or not cases:
        return {}, "kein cases-Array mit Ground-Truth-Label im Lauf-Ergebnis"
    labelled = [
        c
        for c in cases
        if isinstance(c, dict) and isinstance(c.get("intervention_should_have_happened"), bool)
    ]
    if not labelled:
        return {}, "cases vorhanden, aber ohne boolesches intervention_should_have_happened"
    tp = fp = fn = tn = 0
    for case in labelled:
        should = bool(case["intervention_should_have_happened"])
        did = bool(case.get("intervened"))
        if should and did:
            tp += 1
        elif should and not did:
            fn += 1
        elif not should and did:
            fp += 1
        else:
            tn += 1
    return (
        {"tp": tp, "fp": fp, "fn": fn, "tn": tn, "labelled": len(labelled)},
        None,
    )


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4)


def _aggregate_effectiveness(events: list[dict], run_result: dict) -> dict[str, dict]:
    success, false_completion = _task_outcome(run_result)
    return {
        "task_success": success,
        "false_completion": false_completion,
        "steps_to_correction": _steps_to_correction(events, run_result),
    }


def _aggregate_intervention(events: list[dict], run_result: dict) -> dict[str, dict]:
    counts, why = _intervention_counts(run_result)
    out: dict[str, dict] = {}

    if counts:
        tp, fp, fn, labelled = (
            counts["tp"],
            counts["fp"],
            counts["fn"],
            counts["labelled"],
        )
        source = f"cases: {labelled} Fälle mit Ground-Truth-Label"
        out["intervention_precision"] = _metric(
            _ratio(tp, tp + fp) if tp + fp else None,
            source,
            None if tp + fp else "keine Intervention ausgeführt (Precision nicht definiert)",
        )
        out["intervention_recall"] = _metric(
            _ratio(tp, tp + fn) if tp + fn else None,
            source,
            None if tp + fn else "kein Fall mit intervention_expected=true",
        )
        out["false_intervention"] = _metric(fp, source)
        out["missed_intervention"] = _metric(fn, source)
        out["wrong_redirect"] = _wrong_redirects(run_result, source)
    else:
        reason = f"{why} — Precision/Recall brauchen Ground Truth, Events liefern sie nicht"
        for name in (
            "intervention_precision",
            "intervention_recall",
            "false_intervention",
            "missed_intervention",
            "wrong_redirect",
        ):
            out[name] = _not_derivable(reason)

    out["no_intervention_rate"] = _no_intervention_rate(events, run_result)
    out["wrong_memory_match"] = _wrong_memory_match(run_result, counts or {})
    return out


def _wrong_redirects(run_result: dict, source: str) -> dict:
    cases = run_result.get("cases") or []
    flagged = [
        c
        for c in cases
        if isinstance(c, dict)
        and c.get("intervened")
        and c.get("redirect_correct") is False
    ]
    if not any(isinstance(c, dict) and "redirect_correct" in c for c in cases):
        return _not_derivable(
            "kein redirect_correct-Label in den Fällen — falsche Redirects sind nicht zählbar"
        )
    return _metric(len(flagged), source + " (redirect_correct=false)")


def _no_intervention_rate(events: list[dict], run_result: dict) -> dict:
    """Anteil geplanter Pausen, die ohne Eingriff endeten.

    Nenner: ``pause_requested``-Events, Zähler: ``pause_no_action`` (+
    ``pause_expired``/``pause_stale``) — die Absichts-Menge, nicht die
    Gesamt-Eventzahl, sonst wäre die Rate bedeutungslos. Explizit nicht
    ableitbar, wenn der Lauf keine Pause geplant hat.
    """
    requested = len(_of_kind(events, "pause_requested"))
    if not requested:
        eval_no_intervention = (run_result.get("eval") or {}).get("no_intervention")
        if isinstance(eval_no_intervention, (int, float)) and not isinstance(
            eval_no_intervention, bool
        ):
            return _metric(float(eval_no_intervention), "eval.no_intervention")
        return _not_derivable(
            "kein pause_requested-Event — ohne geplante Pause ist die Rate nicht definiert"
        )
    quiet = len(_of_kind(events, ("pause_no_action", "pause_expired", "pause_stale")))
    return _metric(_ratio(quiet, requested), "events: pause_no_action+pause_expired/stale")


def _wrong_memory_match(run_result: dict, counts: dict) -> dict:
    """Falscher Incident-/Memory-Treffer — braucht ein explizites Label.

    Weder Events noch Eval-JSON führen „die Erinnerung war die falsche"; das ist
    eine Bewertung (GS-04-artig), kein Messwert. Deshalb: nur aus einem
    expliziten Zähler/Feld, sonst ``None``.
    """
    for key in ("wrong_memory_match", "wrong_memory_matches"):
        value = run_result.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return _metric(value, f"run_result.{key}")
    cases = run_result.get("cases") or []
    labelled = [
        c
        for c in cases
        if isinstance(c, dict)
        and c.get("intervened")
        and c.get("memory_match_correct") is False
    ]
    if labelled:
        return _metric(len(labelled), "cases: memory_match_correct=false")
    if counts:
        return _not_derivable(
            "Fälle tragen kein memory_match_correct-Label — falsche Memory-Treffer "
            "sind aus Events/Pytest/Eval nicht ableitbar"
        )
    return _not_derivable(
        "kein Memory-Treffer-Label im Lauf-Ergebnis (Bewertung, kein Messwert)"
    )


def _usage_values(run_result: dict) -> tuple[dict, str | None]:
    """Host-gemessene Nutzung (``measured_session_usage``) normalisieren."""
    usage = run_result.get("usage")
    if not isinstance(usage, dict) or not usage:
        return {}, "kein usage-Block (host-gemessene Tokens) im Lauf-Ergebnis"
    tokens_in = _num(usage.get("input_tokens"))
    tokens_out = _num(usage.get("output_tokens"))
    total = None
    if tokens_in is not None or tokens_out is not None:
        total = (tokens_in or 0) + (tokens_out or 0)
        for extra in ("cache_read_tokens", "cache_write_tokens", "reasoning_tokens"):
            value = _num(usage.get(extra))
            if value is not None and extra != "reasoning_tokens":
                total += value
    cost = _num(usage.get("actual_cost_usd"))
    if cost is None:
        cost = _num(usage.get("estimated_cost_usd"))
    return {"total_tokens": total, "cost": cost}, None


def _aggregate_cost(events: list[dict], run_result: dict) -> dict[str, dict]:
    usage, why = _usage_values(run_result)
    out: dict[str, dict] = {}

    if usage.get("total_tokens") is not None:
        out["total_tokens"] = _metric(usage["total_tokens"], "usage: host-gemessene Tokens")
    else:
        estimated = (run_result.get("eval") or {}).get("research_tokens_est")
        out["total_tokens"] = _not_derivable(
            f"{why} — geschätzte Werte werden nicht als Messwert eingetragen"
            + (f" (nur research_tokens_est={estimated} vorhanden, Schätzung)" if estimated else "")
        )

    out["main_agent_tokens"] = _main_agent_tokens(run_result, usage, why)
    out["voice_tokens"] = _voice_tokens(events, run_result, why)
    out["cost"] = (
        _metric(usage["cost"], "usage: actual_cost_usd/estimated_cost_usd")
        if usage.get("cost") is not None
        else _not_derivable(f"{why} — kein gemessener USD-Wert")
    )
    out["runtime_s"] = _runtime_s(run_result)
    out["tool_calls"] = _tool_calls(events, run_result)
    return out


def _main_agent_tokens(run_result: dict, usage: dict, why: str | None) -> dict:
    why = why or ""
    explicit = (run_result.get("eval") or {}).get("main_agent_tokens")
    if isinstance(explicit, int) and not isinstance(explicit, bool):
        return _metric(explicit, "eval.main_agent_tokens")
    measured = (run_result.get("measured_usage") or {}).get("main_agent_tokens_measured")
    if isinstance(measured, (int, float)) and not isinstance(measured, bool):
        return _metric(measured, "measured_usage.main_agent_tokens_measured")
    if usage.get("total_tokens") is None:
        return _not_derivable(
            f"{why or 'kein usage-Block'} — die Zuordnung 'Hauptagent vs. Stimme' "
            "steht in keiner Quelle dieses Lauf-Ergebnisses"
        )
    return _not_derivable(
        "Gesamt-Tokens sind gemessen, aber der Stimm-Anteil nicht — die Differenz "
        "waere eine Schätzung (kein voice_tokens belegbar)"
    )


def _voice_tokens(events: list[dict], run_result: dict, why: str) -> dict:
    explicit = (run_result.get("eval") or {}).get("voice_tokens")
    if isinstance(explicit, int) and not isinstance(explicit, bool):
        return _metric(explicit, "eval.voice_tokens")
    built = _of_kind(events, "voice_context_built")
    if not built:
        return _not_derivable(
            "kein voice_context_built-Event — dann gibt es keine Stimm-Nutzung zu zählen"
            if events
            else f"{why} — keine Events und kein voice_tokens-Feld"
        )
    measured = [
        _num(_payload(e).get("tokens")) for e in built if _payload(e).get("tokens") is not None
    ]
    measured = [m for m in measured if m is not None]
    if measured and all(_payload(e).get("measured") is True for e in built):
        return _metric(sum(measured), "events: voice_context_built (host-gemessen)")
    return _not_derivable(
        "voice_context_built führt nur geschätzte Token-Zahlen (chars/4) — "
        "eine Schätzung wird nicht als Messwert eingetragen"
    )


def _runtime_s(run_result: dict) -> dict:
    for key in ("runtime_s", "duration_s"):
        value = _num(run_result.get(key))
        if value is not None:
            return _metric(value, f"run_result.{key}")
    started, finished = run_result.get("started_at"), run_result.get("finished_at")
    if isinstance(started, str) and isinstance(finished, str) and started and finished:
        try:
            t0 = datetime.fromisoformat(started.replace("Z", "+00:00"))
            t1 = datetime.fromisoformat(finished.replace("Z", "+00:00"))
        except ValueError:
            return _not_derivable("started_at/finished_at sind keine ISO-8601-Zeitstempel")
        return _metric(round((t1 - t0).total_seconds(), 3), "started_at → finished_at")
    return _not_derivable("kein runtime_s/duration_s und kein Zeitstempel-Paar")


def _tool_calls(events: list[dict], run_result: dict) -> dict:
    explicit = (run_result.get("eval") or {}).get("tool_calls")
    if isinstance(explicit, int) and not isinstance(explicit, bool):
        return _metric(explicit, "eval.tool_calls")
    counted = len(_of_kind(events, "tool_call"))
    if counted:
        return _metric(counted, "events: tool_call")
    if events:
        return _not_derivable("kein tool_call-Event im Lauf (Events vorhanden)")
    return _not_derivable("keine Events und kein tool_calls-Feld im Lauf-Ergebnis")


def _aggregate_reliability(events: list[dict], run_result: dict) -> dict[str, dict]:
    return {
        "crash_recovery_ok": _crash_recovery_ok(run_result, events),
        "exactly_once_continuation": _exactly_once(run_result, events),
        "capability_reuse": _capability_reuse(events, run_result),
    }


def _crash_recovery_ok(run_result: dict, events: list[dict]) -> dict:
    crash = run_result.get("crash_recovery")
    if isinstance(crash, dict):
        findings = crash.get("findings")
        rc = _num(crash.get("exit_code"))
        if isinstance(findings, list):
            double_effects = [
                f for f in findings if isinstance(f, dict) and f.get("kind") == "double_effect"
            ]
            state_issues = [
                f for f in findings if isinstance(f, dict) and f.get("kind") == "state"
            ]
            return _metric(
                not double_effects and not state_issues,
                "crash_recovery: findings (double_effect/state)",
            )
        if rc is not None:
            return _metric(rc == 0, "crash_recovery.exit_code")
    if not events:
        return _not_derivable("kein crash_recovery-Block und keine Events im Lauf-Ergebnis")
    return _not_derivable(
        "kann aus Events allein nicht belegt werden — der Beweis braucht den "
        "Snapshot-Vergleich vor/nach dem Absturz (tools/gv_crash_recovery.py)"
    )


def _exactly_once(run_result: dict, events: list[dict]) -> dict:
    sup = run_result.get("supervisor")
    if isinstance(sup, dict):
        claimed = _num(sup.get("continuation_claimed"))
        committed = _num(sup.get("continuation_committed"))
        if claimed is not None and committed is not None and claimed > 0:
            return _metric(
                claimed == committed == 1,
                "supervisor: continuation_claimed/committed",
                None
                if claimed == committed == 1
                else f"claimed={int(claimed)}, committed={int(committed)}",
            )
        replay = _num(sup.get("applied_count"))
        if replay is not None:
            return _metric(replay == 1, "supervisor: applied_count (Replay)")
    explicit = run_result.get("exactly_once_continuation")
    if isinstance(explicit, bool):
        return _metric(explicit, "run_result.exactly_once_continuation")
    if not events:
        return _not_derivable(
            "kein supervisor-Block und keine Events — genau-einmal ist so nicht prüfbar"
        )
    return _not_derivable(
        "Events zeigen geplante Wiederaufnahmen, aber nicht die Zahl der "
        "tatsächlichen Effekte — dafuer braucht es Revision/Replay aus dem "
        "Run-Supervisor"
    )


def _capability_reuse(events: list[dict], run_result: dict) -> dict:
    explicit = run_result.get("capability_reuse")
    if isinstance(explicit, bool):
        return _metric(explicit, "run_result.capability_reuse")
    if isinstance(explicit, int) and not isinstance(explicit, bool):
        return _metric(explicit, "run_result.capability_reuse (Anzahl)")
    built = len(
        _of_kind(
            events,
            (
                "tool_build_ready",
                "capability_generalized",
                "capability_promoted",
                "capability_mined",
            ),
        )
    )
    consumed = len(_of_kind(events, ("skill_consumed", "capability_used", "capability_reused")))
    if consumed:
        return _metric(consumed, "events: skill_consumed/capability_used")
    if built:
        return _not_derivable(
            f"{built} gebaute/verallgemeinerte Fähigkeit(en), aber kein "
            "Wiederverwendungs-Event — gebaut ist nicht wiederverwendet"
        )
    return _not_derivable(
        "kein Wiederverwendungs-Event und kein Feld — eine Fähigkeit ohne "
        "Aufruf im selben Lauf belegt keine Wiederverwendung"
    )


def aggregate_metrics(run_result: dict, schema: dict | None = None) -> dict:
    """Alle Schema-Metriken aus einem Lauf-Ergebnis ableiten, Lücken benennen.

    Rückgabe::

        {"metrics": {name: {"value":…, "source":…, "reason":…}, …},
         "derived": [namen mit Messwert], "not_derivable": [namen], "reasons": {name: text}}

    Es gibt keinen Schätzpfad: was nicht in den Daten steht, ist ``None``.
    """
    if not isinstance(run_result, dict):
        raise ComparabilityError("run_result muss ein Objekt sein (dict)")
    events, load_error = load_events(run_result)
    metrics: dict[str, dict] = {}
    if load_error:
        for name in REQUIRED_METRICS:
            metrics[name] = _not_derivable(f"Events nicht lesbar: {load_error}")
    else:
        metrics.update(_aggregate_effectiveness(events, run_result))
        metrics.update(_aggregate_intervention(events, run_result))
        metrics.update(_aggregate_cost(events, run_result))
        metrics.update(_aggregate_reliability(events, run_result))

    schema_names = _metric_names(schema) if schema else list(REQUIRED_METRICS)
    for name in schema_names:
        metrics.setdefault(name, _not_derivable("Metrik ist in diesem Harness nicht implementiert"))

    ordered = {name: metrics[name] for name in schema_names if name in metrics}
    derived = [n for n, m in ordered.items() if m["value"] is not None]
    nope = [n for n, m in ordered.items() if m["value"] is None]
    return {
        "metrics": ordered,
        "derived": derived,
        "not_derivable": nope,
        "reasons": {n: str(ordered[n]["reason"]) for n in nope},
    }


# --- 5) run_record im Schema-Format -------------------------------------------


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _clean_refs(value: object) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item) for item in value if str(item).strip()]


def build_run_record(
    *,
    run_id: str,
    session_id: str,
    configuration: str,
    final_state: str,
    schema: dict | None = None,
    started_at: str | None = None,
    finished_at: str | None = None,
    goal_revision: int | None = None,
    evidence_refs: object = (),
    artifact_refs: object = (),
    incidents: object = (),
    capabilities_learned: object = (),
    notes: str = "",
) -> dict:
    """``run_record`` exakt in der Feldreihenfolge/Feldmenge des Schemas erzeugen.

    Pflichtangaben, die nicht vorhanden sind, werden NICHT erfunden:
    ``run_id``/``session_id``/``configuration`` müssen nicht-leer sein (sonst
    fail-loud), ``goal_revision`` bleibt ``None``, wenn der Lauf keine Revision
    führt. Zeitstempel werden nur gesetzt, wenn sie übergeben wurden — der
    fehlende ``finished_at`` eines laufenden Laufs darf nicht durch "jetzt"
    ersetzt werden.
    """
    missing = [
        name
        for name, value in (
            ("run_id", run_id),
            ("session_id", session_id),
            ("configuration", configuration),
            ("final_state", final_state),
        )
        if _is_unset(value)
    ]
    if missing:
        raise ComparabilityError(
            "run_record unvollständig — Pflichtfelder ohne Wert: " + ", ".join(missing)
        )

    if schema is not None:
        if configuration not in {c["id"] for c in list_configurations(schema)}:
            raise ComparabilityError(
                f"run_record: Konfiguration {configuration!r} steht nicht im Schema "
                "(kein Freitext erlaubt — sonst ist der Vergleich nicht zuordenbar)"
            )

    fields = RUN_RECORD_FIELDS
    if schema is not None:
        fields = tuple(
            key
            for key in (schema.get("run_record") or {})
            if not key.startswith("$")
        ) or RUN_RECORD_FIELDS

    record = {
        "run_id": str(run_id),
        "session_id": str(session_id),
        "goal_revision": goal_revision if isinstance(goal_revision, int) else None,
        "configuration": str(configuration),
        "started_at": str(started_at or ""),
        "finished_at": str(finished_at or ""),
        "final_state": str(final_state),
        "evidence_refs": _clean_refs(evidence_refs),
        "artifact_refs": _clean_refs(artifact_refs),
        "incidents": list(incidents) if isinstance(incidents, (list, tuple)) else [],
        "capabilities_learned": (
            list(capabilities_learned)
            if isinstance(capabilities_learned, (list, tuple))
            else []
        ),
        "notes": str(notes or ""),
    }
    extra = [key for key in record if key not in fields]
    return {key: record[key] for key in fields if key in record} if not extra else {
        **{key: record[key] for key in fields if key in record},
        **{key: record[key] for key in extra},
    }


def write_json(path: str | Path, payload: object) -> Path:
    """JSON-Datei schreiben (Elternverzeichnis wird angelegt)."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False, default=str),
        encoding="utf-8",
    )
    return out


def run_record_from_run_result(
    run_result: dict, *, schema: dict, configuration: str, notes: str = ""
) -> dict:
    """``run_record`` aus einem Lauf-Ergebnis bauen (ohne zu raten).

    Übernommen wird nur, was das Ergebnis führt: ``run_id`` (sonst aus
    ``session_id`` + Startzeit gebildet und als solches in ``notes`` vermerkt),
    ``goal_revision``, letzter ``final_state`` aus Events/``final_state`` und die
    Evidenz-/Artefakt-Referenzen. ``incidents`` kommen aus den Event-Payloads
    der Incident-Kinds; ``capabilities_learned`` aus den Bau-/Promote-Events.
    """
    if not isinstance(run_result, dict):
        raise ComparabilityError("run_result muss ein Objekt sein (dict)")
    events, load_error = load_events(run_result)
    if load_error:
        events = []
    session_id = str(run_result.get("session_id") or "")
    started = str(run_result.get("started_at") or "")
    finished = str(run_result.get("finished_at") or "")
    run_id = str(run_result.get("run_id") or "")
    note_bits = [notes] if notes else []
    if not run_id:
        stamp = (started or _now_iso()).replace(":", "").replace("-", "")[:15]
        run_id = f"{session_id or 'run'}-{stamp}"
        note_bits.append("run_id aus session_id+Startzeit gebildet (kein run_id im Lauf-Ergebnis)")

    final_state = str(run_result.get("final_state") or "")
    if not final_state:
        verdicts = _of_kind(events, "completion_verdict")
        final_state = str((_payload(verdicts[-1]).get("state") if verdicts else "") or "")
        if not final_state:
            final_state = "UNKNOWN"
            note_bits.append("kein final_state belegbar → 'UNKNOWN' (nicht als Erfolg gewertet)")

    incidents: list[dict] = []
    for event in _of_kind(events, ("contradiction", "tool_call_intercepted",
                                   "prompt_injection_signal", "pattern_tool_loop")):
        incidents.append(
            {
                "kind": str(event.get("kind")),
                "step": _step(event),
                "detail": str(_payload(event).get("detail")
                              or _payload(event).get("reason")
                              or _payload(event).get("fingerprint")
                              or "")[:200],
            }
        )
    learned: list[dict] = []
    for event in _of_kind(events, ("tool_build_ready", "capability_promoted",
                                   "capability_generalized")):
        learned.append(
            {
                "kind": str(event.get("kind")),
                "step": _step(event),
                "name": str(_payload(event).get("name") or _payload(event).get("tool") or ""),
            }
        )

    record = build_run_record(
        run_id=run_id,
        session_id=session_id or "UNKNOWN",
        configuration=configuration,
        final_state=final_state,
        schema=schema,
        started_at=started,
        finished_at=finished,
        goal_revision=(
            run_result["goal_revision"]
            if isinstance(run_result.get("goal_revision"), int)
            and not isinstance(run_result.get("goal_revision"), bool)
            else None
        ),
        evidence_refs=run_result.get("evidence_refs") or [],
        artifact_refs=run_result.get("artifact_refs") or [],
        incidents=run_result.get("incidents") or incidents,
        capabilities_learned=run_result.get("capabilities_learned") or learned,
        notes="; ".join(b for b in note_bits if b),
    )
    return record


# --- 6) CLI -------------------------------------------------------------------


def _load_json(path: str | Path) -> object:
    p = Path(path)
    if not p.is_file():
        raise ComparabilityError(f"Datei nicht gefunden: {p}")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ComparabilityError(f"Kein gültiges JSON ({p}): {exc}") from exc


def main(argv: list[str] | None = None) -> int:
    """Fail-loud CLI. Jede Verletzung endet mit Exit != 0 und Klartext auf stderr."""
    parser = argparse.ArgumentParser(
        prog="gv_benchmark",
        description="God's Voice Benchmark-Harness (Master-§37/§43) — kein Modellaufruf.",
    )
    parser.add_argument("--schema", default=str(DEFAULT_SCHEMA), help="Pfad zum Schema-JSON")
    parser.add_argument("--validate-schema", action="store_true", help="Schema laden + prüfen")
    parser.add_argument(
        "--list-configurations", action="store_true", help="Konfigurationen auflisten"
    )
    parser.add_argument("--configuration", help="Konfigurations-ID laden (z. B. full_godsv)")
    parser.add_argument(
        "--constants",
        metavar="JSON_OR_PATH",
        help="Konstanten-Beobachtung(en) prüfen: Pfad zu JSON oder Inline-JSON",
    )
    parser.add_argument("--metrics-from", metavar="RUN_JSON", help="Metriken aus Lauf-Ergebnis")
    parser.add_argument(
        "--run-record-from", metavar="RUN_JSON", help="run_record aus Lauf-Ergebnis bauen"
    )
    parser.add_argument("--session-id", help="session_id für den run_record")
    parser.add_argument("--run-id", help="run_id für den run_record")
    parser.add_argument("--final-state", help="final_state für den run_record")
    parser.add_argument(
        "--require-schema-reference",
        action="store_true",
        help="Schemap Platzhalter (leere Konstanten) als Verletzung behandeln",
    )
    parser.add_argument("--out", help="Zieldatei für JSON-Ausgaben (sonst stdout)")
    parser.add_argument("--notes", default="", help="Freitext für run_record.notes")
    args = parser.parse_args(argv)

    try:
        schema = load_and_validate_schema(args.schema)
        payload: dict = {}

        if args.validate_schema:
            payload["schema"] = {
                "path": str(Path(args.schema).resolve()),
                "schema_version": schema["schema_version"],
                "valid": True,
            }

        if args.constants:
            observed = _observed_constants(args.constants)
            report = constants_report(
                schema,
                observed,
                label="Vergleichsblock",
                require_schema_reference=args.require_schema_reference,
            )
            if not report.get("ok"):
                raise ComparabilityError(str(report.get("error")))
            payload["constants"] = report

        if args.list_configurations:
            payload["configurations"] = list_configurations(schema)

        if args.configuration:
            payload["configuration"] = load_configuration(schema, args.configuration)

        if args.metrics_from:
            run_result = _load_json(args.metrics_from)
            if not isinstance(run_result, dict):
                raise ComparabilityError("Lauf-Ergebnis muss ein JSON-Objekt sein")
            report = aggregate_metrics(run_result, schema)
            payload["metrics"] = report["metrics"]
            payload["derived"] = report["derived"]
            payload["not_derivable"] = report["not_derivable"]
            payload["reasons"] = report["reasons"]

        if args.run_record_from:
            run_result = _load_json(args.run_record_from)
            if not isinstance(run_result, dict):
                raise ComparabilityError("Lauf-Ergebnis muss ein JSON-Objekt sein")
            if not args.configuration:
                raise ComparabilityError(
                    "--run-record-from braucht --configuration (kann nicht geraten werden)"
                )
            merged = dict(run_result)
            if args.session_id:
                merged["session_id"] = args.session_id
            if args.run_id:
                merged["run_id"] = args.run_id
            if args.final_state:
                merged["final_state"] = args.final_state
            record = run_record_from_run_result(
                merged, schema=schema, configuration=args.configuration, notes=args.notes
            )
            payload["run_record"] = record

        if not payload:
            parser.print_help()
            return 0
        if args.out:
            print(f"json -> {write_json(args.out, payload)}")
        else:
            print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return 0
    except ComparabilityError as exc:
        sys.stderr.write(f"FEHLER: {exc}\n")
        return 2


def _observed_constants(value: str) -> dict:
    """``--constants`` akzeptiert einen JSON-Pfad oder Inline-JSON."""
    text = value
    candidate = Path(value)
    if candidate.is_file():
        payload = _load_json(candidate)
    else:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ComparabilityError(
                f"--constants ist weder ein Pfad noch gültiges Inline-JSON: {exc}"
            ) from exc
    if not isinstance(payload, dict):
        raise ComparabilityError("--constants braucht ein JSON-Objekt")
    return payload


if __name__ == "__main__":
    raise SystemExit(main())
