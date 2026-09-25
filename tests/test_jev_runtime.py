"""The Jev runtime binding: settings enable, provider wiring, ROUTE reachability.

What this file pins, in the order the feature was built:

1. **The settings edit is atomic, reversible and reported** —
   ``settings.yaml`` is written through ``tmp + fsync + os.replace`` with a
   byte-exact backup, a dotted-key diff and a loader read-back; a dry run writes
   nothing, and ``disable --restore`` puts the previous file back byte for byte
   (:mod:`skaldir.settings_service`).
2. **The provider is really wired** — ``runtime.service_for()`` resolves
   ``skaldir.llm_provider.provider_for()`` when the flags name the LLM adapter, so
   the service is *not* the disabled evaluator; with no credential it degrades to
   the substrate's disabled evaluator and says so, and it never substitutes a
   scripted client.
3. **The fixture evaluator stays selectable for tests** — ``probe="fixture"``
   answers every kind offline (``provider_mode="replay_fixture"``), all six
   kinds, with the four hard rules re-measured on every decision.
4. **The four hard rules cannot be bypassed through this path** — advisory-only
   decisions, no forbidden field, a denied candidate un-routable even when the
   evaluator tries, and no write into the runtime directory (the goal file is
   never touched).
5. **The ROUTE probe really flips** — the documented two-line change to
   ``cli_contract.py`` is applied to a *copy* of that file and run: the layer goes
   from ``ok:false``/"probe failed" to ``reachable``, and the honest
   ``runtime status`` verdict is checked against it (the stock probe accepts an
   empty evaluation as reachable; the runtime verdict does not).

Everything here is offline: the provider resolution is pinned to an empty
environment plus a non-existent config file, so an inherited ``GODSV_LLM_*`` can
never turn a test into a live call. The one live test lives in
``tests/test_jev_runtime_live.py`` and is skipped unless ``GODSV_LLM_LIVE=1``.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from skaldir import settings_service
from skaldir.jev import contracts as C
from skaldir.jev import rules as jev_rules
from skaldir.jev import runtime
from skaldir.jev.contracts import JEV_ANSWERS
from skaldir.jev.decide import decide
from skaldir.jev.fixture import FixtureJevEvaluator
from skaldir.jev.kinds import DECISION_KINDS
from skaldir.jev.rules import JevRuleViolation
from skaldir.jev.selftest import SELFTEST_CASES
from skaldir.llm import ScriptedLLM
from skaldir.settings_service import SettingsError
from skaldir.system_one import PROVIDER_DISABLED, PROVIDER_LLM_ADAPTER, parse_answer
from skaldir.system_one_flags import SystemOneFlags

SRC = Path(__file__).resolve().parents[1] / "src"
RUNTIME_MODULE = SRC / "skaldir" / "jev" / "runtime.py"
SETTINGS_SERVICE = SRC / "skaldir" / "settings_service.py"

OFF_SECTION = """\
system_one:
  enabled: false
  provider: disabled
  voice_triage: true
"""


def _isolated(tmp_path: Path) -> dict:
    """Pin the provider resolution: empty environment, no config file at all."""
    return {"env": {}, "config_path": str(tmp_path / "no-llm-config.yaml")}


# --------------------------------------------------------------------------- #
# 1. the settings edit: atomic, backed up, reported, reversible                 #
# --------------------------------------------------------------------------- #


def test_enable_writes_the_section_atomically_with_a_backup_and_a_diff(tmp_path):
    settings = tmp_path / "settings.yaml"
    settings.write_text("strictness: strict\n" + OFF_SECTION, encoding="utf-8")
    before_bytes = settings.read_bytes()

    report = runtime.enable(tmp_path)

    assert report["wrote"] is True
    assert report["path"] == str(settings)
    assert report["backup"], "a write over an existing file must leave a backup"
    backup = Path(report["backup"])
    assert backup.read_bytes() == before_bytes, "the backup must be byte-exact"
    assert backup.name.startswith("settings.yaml.bak-")
    changed = {row["key"] for row in report["changed"]}
    assert "system_one.enabled" in changed
    assert "system_one.provider" in changed
    assert "system_one.voice_triage" not in changed, (
        "a key the caller did not name keeps its value (merge, not replace)"
    )
    document = settings_service.read_document(settings)
    assert document["strictness"] == "strict", "unrelated sections must survive"
    assert document["system_one"]["voice_triage"] is True
    assert document["system_one"]["enabled"] is True
    assert document["system_one"]["provider"] == PROVIDER_LLM_ADAPTER
    assert report["loader_verified"] is True
    assert report["read_back"]["system_one.enabled"] is True
    assert list(tmp_path.glob("*.tmp-*")) == [], "the temporary file must be gone"


def test_enable_is_idempotent_and_says_so(tmp_path):
    first = runtime.enable(tmp_path)
    second = runtime.enable(tmp_path)
    assert first["wrote"] is True
    assert second["changed"] == []
    assert second["wrote"] is False
    assert second["backup"] == ""
    assert len(settings_service.list_backups(tmp_path / "settings.yaml")) == 0, (
        "the first write created the file, so there was nothing to back up"
    )


def test_dry_run_reports_the_diff_and_writes_nothing(tmp_path):
    report = runtime.enable(tmp_path, dry_run=True)
    assert report["dry_run"] is True
    assert report["wrote"] is False
    assert report["changed"], "a dry run still reports what would change"
    assert sorted(item.name for item in tmp_path.iterdir()) == []
    assert report["bytes_after"] > 0


def test_disable_restore_puts_the_previous_file_back_byte_for_byte(tmp_path):
    settings = tmp_path / "settings.yaml"
    settings.write_text("strictness: strict\n" + OFF_SECTION, encoding="utf-8")
    original = settings.read_bytes()
    runtime.enable(tmp_path)
    assert settings.read_bytes() != original

    report = runtime.disable(tmp_path, restore=True)

    assert report["wrote"] is True
    assert settings.read_bytes() == original
    assert report["after"]["provider"] == PROVIDER_DISABLED


def test_disable_without_restore_sets_the_all_off_defaults(tmp_path):
    runtime.enable(tmp_path)
    runtime.disable(tmp_path)
    flags = runtime.flags_for_probe(tmp_path, "settings")
    assert flags == SystemOneFlags.disabled()


def test_disable_restore_removes_a_file_the_enable_created(tmp_path):
    runtime.enable(tmp_path)
    settings = tmp_path / "settings.yaml"
    assert settings.is_file()

    report = runtime.disable(tmp_path, restore=True)

    assert report["removed"] is True
    assert report["wrote"] is True
    assert not settings.exists(), "created from nothing ⇒ removed again"
    assert runtime.flags_for_probe(tmp_path, "settings") == SystemOneFlags.disabled()


def test_disable_restore_never_deletes_unrelated_sections(tmp_path):
    settings = tmp_path / "settings.yaml"
    settings.write_text("strictness: strict\n", encoding="utf-8")
    original = settings.read_bytes()
    runtime.enable(tmp_path)

    report = runtime.disable(tmp_path, restore=True)

    assert report["restored_from"], "there was a file, so there is a real backup"
    assert settings.read_bytes() == original, "byte-exact restore of the pre-enable file"
    document = settings_service.read_document(settings)
    assert document["strictness"] == "strict"
    assert "system_one" not in document, "the section the enable added is gone again"


def test_enable_refuses_a_section_the_loader_would_reject(tmp_path):
    (tmp_path / "settings.yaml").write_text(
        "system_one:\n  enabled: true\n  brand_new_hack: true\n", encoding="utf-8"
    )
    with pytest.raises(SettingsError) as exc:
        runtime.enable(tmp_path)
    assert "loader would reject" in str(exc.value)
    assert "brand_new_hack" in str(exc.value)


def test_a_malformed_settings_file_is_never_silently_replaced(tmp_path):
    (tmp_path / "settings.yaml").write_text("system_one: [1, 2\n", encoding="utf-8")
    with pytest.raises(SettingsError):
        runtime.enable(tmp_path)
    assert (tmp_path / "settings.yaml").read_text(encoding="utf-8") == "system_one: [1, 2\n"


def test_restore_refuses_a_file_that_is_not_a_backup(tmp_path):
    settings = tmp_path / "settings.yaml"
    settings.write_text("system_one: {enabled: true}\n", encoding="utf-8")
    stranger = tmp_path / "somewhere-else.yaml"
    stranger.write_text("system_one: {enabled: false}\n", encoding="utf-8")
    with pytest.raises(SettingsError):
        settings_service.restore(settings, stranger)


# --------------------------------------------------------------------------- #
# 2. the provider wiring                                                        #
# --------------------------------------------------------------------------- #


def test_service_for_wires_the_real_adapter_when_flags_name_it():
    flags = SystemOneFlags(enabled=True, provider=PROVIDER_LLM_ADAPTER)
    scripted = ScriptedLLM(responses={}, default="{}")
    service = runtime.service_for(flags, llm=scripted)
    assert service.provider_name == PROVIDER_LLM_ADAPTER
    assert service.enabled is True
    assert service.evaluator.llm is scripted


def test_service_for_degrades_to_disabled_and_never_substitutes_a_scripted_client(tmp_path):
    flags = SystemOneFlags(enabled=True, provider=PROVIDER_LLM_ADAPTER)
    service = runtime.service_for(flags, **_isolated(tmp_path))
    assert service.provider_name == PROVIDER_DISABLED, (
        "no credential must mean the disabled evaluator, never a fabricated one"
    )
    problem = runtime.provider_problem(flags, **_isolated(tmp_path))
    assert problem, "the blocker must be named"
    assert "SKALDIR_LLM_BASE_URL" in problem


def test_provider_report_is_redacted(tmp_path):
    report = runtime.provider_report(**_isolated(tmp_path))
    assert report["api_key"] in ("set", "unset")
    assert report["ready"] is False
    assert "hint" in report and report["hint"]


# --------------------------------------------------------------------------- #
# 3. status: reachable, blocked, and why                                        #
# --------------------------------------------------------------------------- #


def test_status_is_blocked_without_a_credential_and_names_the_fix(tmp_path):
    runtime.enable(tmp_path)
    report = runtime.status(tmp_path, **_isolated(tmp_path))

    assert report["route"]["reachable"] is False
    assert report["route"]["exit_code"] == 3
    causes = {row["cause"]: row for row in report["causes"]}
    assert causes["system_one_flags"]["state"] == "fixed"
    assert causes["advise"]["state"] == "pending"
    assert "provider_unconfigured" in report["route"]["blocked_reason"]
    assert report["decision"]["status"] == "blocked"
    assert report["decision"]["fallback"], "a blocked decision names the caller's own path"
    assert report["decision"]["authority"] == "advisory"
    assert report["rules_rechecked"]["violations"] == []
    assert report["provider"]["api_key"] in ("set", "unset")
    assert "sk-" not in json.dumps(report), "a report never carries a key"


def test_status_fixture_probe_is_reachable_offline(tmp_path):
    report = runtime.status(tmp_path, probe="fixture", **_isolated(tmp_path))

    assert report["route"]["reachable"] is True
    assert report["route"]["exit_code"] == 0
    assert report["route"]["provider_mode"] == "replay_fixture"
    assert report["advise"]["answers"] > 0, "an empty evaluation is not reachability"
    assert report["decision"]["status"] == "ok"
    assert report["flags"] is None, "the fixture probe does not consult the flag file"
    assert report["warnings"] == []


def test_status_disabled_probe_is_the_honest_blocked_path(tmp_path):
    report = runtime.status(tmp_path, probe="disabled", **_isolated(tmp_path))

    assert report["route"]["reachable"] is False
    assert report["route"]["exit_code"] == 3
    assert "provider_disabled" in report["route"]["blocked_reason"]
    assert report["decision"]["status"] == "blocked"
    assert report["decision"]["fallback"], "the caller's own path is named"
    assert report["decision"]["refusal_reason"] == "provider_disabled"
    answer = report["decision"]["answer"]
    assert answer["chosen_kind"] == "no_route" and answer["route"] is None, (
        "a blocked routing decision must not name a route"
    )
    assert answer["reason_code"] == "no_advisory"
    voice, _flags, _problem = runtime.decide_through_runtime(
        "voice_triage", runtime=tmp_path, probe="disabled", **_isolated(tmp_path)
    )
    assert voice.status == "blocked"
    assert voice.answer.chosen == "defer", (
        "a blocked Voice decision must be the neutral one — never 'stay_silent'"
    )
    assert voice.answer.chosen != "stay_silent"


def test_status_does_not_write_into_the_runtime_directory(tmp_path):
    runtime.enable(tmp_path)
    before = _snapshot(tmp_path)
    runtime.status(tmp_path, probe="fixture", **_isolated(tmp_path))
    runtime.status(tmp_path, probe="disabled", **_isolated(tmp_path))
    assert _snapshot(tmp_path) == before, "status must only probe"


def _snapshot(directory: Path) -> dict:
    return {
        item.name: (item.stat().st_size, item.stat().st_mtime_ns, item.read_bytes())
        for item in sorted(directory.iterdir())
        if item.is_file()
    }


# --------------------------------------------------------------------------- #
# 4. the runtime path cannot bypass the four hard rules                          #
# --------------------------------------------------------------------------- #

DENIED_CANDIDATE = "tool.denied"


def _routing_request_with_a_denied_candidate():
    payload = json.loads(json.dumps(SELFTEST_CASES["tool_routing"]))
    payload["candidates"].append(
        {
            "candidate_id": DENIED_CANDIDATE,
            "kind": "native_tool",
            "name": "denied tool",
            "version": "1.0.0",
            "source": "registry",
            "input_types": [],
            "required_params": [],
            "preconditions_satisfied": True,
            "health": "ok",
            "permission_state": "denied",
            "risk_class": "R1",
        }
    )
    return C.request_for("tool_routing", payload)


class DeniedRouteEvaluator(FixtureJevEvaluator):
    """A hostile evaluator: it answers "route to the denied candidate" (an R2 attempt)."""

    def answers_for(self, questions):
        built = super().answers_for(questions)
        for question in questions.questions:
            if question.id.endswith("routing.choice") and DENIED_CANDIDATE in question.options:
                built[question.id] = parse_answer(
                    question_id=question.id,
                    kind=question.kind,
                    payload={
                        "choice": DENIED_CANDIDATE,
                        "probabilities": {
                            str(option): (1.0 if str(option) == DENIED_CANDIDATE else 0.0)
                            for option in question.options
                        },
                        "confidence": 0.99,
                    },
                )
        return built


def test_every_runtime_decision_is_advisory_only_for_all_six_kinds(tmp_path):
    for kind in DECISION_KINDS:
        decision, _flags, _problem = runtime.decide_through_runtime(
            kind, runtime=tmp_path, probe="fixture", **_isolated(tmp_path)
        )
        assert decision.kind == kind
        assert decision.status == "ok", decision.refusal_reason
        assert type(decision.answer) is JEV_ANSWERS[kind]
        assert decision.authority == "advisory"
        assert decision.certifies is False
        assert tuple(decision.grants) == ()
        assert decision.goal_revision == 0
        assert _forbidden_names(decision.model_dump(mode="json")["answer"]) == set(), (
            "R1/R2/R3: a forbidden field name appeared in an answer"
        )
        assert set(decision.model_dump(mode="json")) == set(
            C.JevDecision.model_fields
        ), "the envelope may only narrow the declared fields"


def _forbidden_names(node, path: str = "") -> set[str]:
    found: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            if str(key) in jev_rules.FORBIDDEN_EXACT:
                found.add(str(key))
            found |= _forbidden_names(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found |= _forbidden_names(value, f"{path}[{index}]")
    return found


def test_the_runtime_path_cannot_route_to_a_denied_candidate(tmp_path):
    request = _routing_request_with_a_denied_candidate()
    service = runtime.service_for(None, evaluator=DeniedRouteEvaluator())
    with pytest.raises(JevRuleViolation) as exc:
        decide(request, service=service)
    assert exc.value.rule == jev_rules.RULE_R2
    assert DENIED_CANDIDATE in str(exc.value)


def test_a_denied_only_candidate_set_is_answered_without_a_provider(tmp_path):
    payload = json.loads(json.dumps(SELFTEST_CASES["tool_routing"]))
    payload["candidates"][0]["permission_state"] = "denied"
    request = C.request_for("tool_routing", payload)
    decision, _flags, _problem = runtime.decide_through_runtime(
        request, runtime=tmp_path, probe="settings", **_isolated(tmp_path)
    )
    assert decision.status == "ok"
    assert decision.answer.chosen_kind == "no_route"
    assert decision.answer.route is None
    assert decision.answer.reason_code == "permission_denied_only"
    assert decision.provider_mode == PROVIDER_DISABLED, "no provider ran for a deterministic no"


def test_a_decision_never_touches_the_runtime_directory(tmp_path):
    runtime.enable(tmp_path)
    before = _snapshot(tmp_path)
    for kind in DECISION_KINDS:
        runtime.decide_through_runtime(
            kind, runtime=tmp_path, probe="fixture", **_isolated(tmp_path)
        )
    assert _snapshot(tmp_path) == before, "R4: nothing may rewrite a goal file"


def test_the_runtime_module_keeps_the_facade_structure():
    forbidden = {
        "httpx",
        "requests",
        "aiohttp",
        "urllib.request",
        "http.client",
        "skaldir.system_one_service",
        "skaldir.system_one_adapters",
        "skaldir.llm",
    }
    text = RUNTIME_MODULE.read_text(encoding="utf-8")
    tree = ast.parse(text)
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            if node.level:
                found.add(f"skaldir.{node.module}")
    assert not (found & forbidden), sorted(found & forbidden)
    for needle in ("api.typesafe.ai", "/v1/systemone", "Bearer ", "jev-latest"):
        assert needle not in text
    assert "llm_provider" in found, "the runtime is the one place that asks for the real client"


# --------------------------------------------------------------------------- #
# 5. the ROUTE probe: the documented change, applied to a real copy of the file #
# --------------------------------------------------------------------------- #


def _unpatched_contract_source() -> str:
    """``cli_contract.py`` with exactly the two documented lines REVERTED.

    The shipped tree already carries them (that is what ``integration_change``
    reports as ``applied``), so the pre-fix spelling is rebuilt here to keep the
    line-number check meaningful.
    """
    source = (SRC / "skaldir" / "cli_contract.py").read_text(encoding="utf-8")
    wiring = "from .jev.runtime import service_for as build_service"
    verdict = 'return not blocked, " ".join(blocked), probes'
    assert wiring in source and verdict in source, "the two documented lines are not in the tree"
    reverted = source.replace(wiring, "from .system_one_service import build_service", 1)
    marker = "def _probe_route("
    head, _, tail = reverted.partition(marker)
    assert verdict in tail
    return head + marker + tail.replace(
        verdict, 'return False, " ".join(blocked), probes', 1
    )


def test_integration_change_points_at_the_real_lines(tmp_path):
    path = tmp_path / "cli_contract_unpatched.py"
    path.write_text(_unpatched_contract_source(), encoding="utf-8")
    report = runtime.integration_change(path)
    assert report["applied"] is False, "a reverted copy must not read as applied"
    assert report["changes"], "a reverted copy must report the two lines"
    raw = path.read_text(encoding="utf-8").splitlines()
    for line in report["changes"]:
        assert line["from"] in raw[line["line"] - 1], (
            f"reported line {line['line']} does not carry {line['from']!r}"
        )


def test_integration_change_is_marked_applied_on_the_shipped_tree():
    report = runtime.integration_change()
    assert report["file"] == "src/skaldir/cli_contract.py"
    assert report["applied"] is True and report["changes"] == [], report
    assert report["after"]


def _patched_contract_source() -> str:
    """The shipped ``cli_contract.py`` as a standalone module (absolute imports).

    Both documented lines are already in the tree; the relative imports are
    rewritten so the copy can be executed outside the package.
    """
    source = (SRC / "skaldir" / "cli_contract.py").read_text(encoding="utf-8")
    wiring = "from .jev.runtime import service_for as build_service"
    verdict = 'return not blocked, " ".join(blocked), probes'
    assert wiring in source and verdict in source, "the two documented lines are not in the tree"
    patched = re.sub(r"(?m)^(\s*)from \. import ", r"\1from skaldir import ", source)
    return re.sub(
        r"(?m)^(\s*)from \.([A-Za-z_][\w.]*) import ", r"\1from skaldir.\2 import ", patched
    )


def _patched_contract_module(tmp_path: Path):
    path = tmp_path / "cli_contract_probing_copy.py"
    path.write_text(_patched_contract_source(), encoding="utf-8")
    spec = importlib.util.spec_from_file_location("cli_contract_probing_copy", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_the_wired_route_probe_is_reachable_and_follows_its_probes(tmp_path, monkeypatch):
    """The two documented lines are IN the tree — measured on the shipped file.

    The stock probe follows its probes (``not blocked``) and builds its service
    through the runtime's own path (``service_for``), so it sees the provider the
    settings name. The injected scripted service below proves that the wired path
    is the one that runs, offline.
    """
    from skaldir import cli_contract, system_one_service

    runtime.enable(tmp_path)

    stock = cli_contract.run_selftest(tmp_path, layers=["ROUTE"])
    layer = stock["layers"][0]
    assert stock["ok"] is True, layer["blocked_reason"]
    assert layer["status"] == "reachable", (
        "the shipped verdict follows its probes — see integration_change"
    )
    assert layer["probes"][1]["ok"] is True, (
        "the advise probe reaches the runtime's own construction path"
    )

    def _wired(flags=None, **kwargs):
        return system_one_service.build_service(
            flags=flags or SystemOneFlags(enabled=True, provider=PROVIDER_LLM_ADAPTER),
            llm=ScriptedLLM(responses={}, default="{}"),
        )

    monkeypatch.setattr(runtime, "service_for", _wired)
    patched_wired = _patched_contract_module(tmp_path).run_selftest(tmp_path, layers=["ROUTE"])
    layer = patched_wired["layers"][0]
    advise = next(item for item in layer["probes"] if item["name"] == "advise")
    assert patched_wired["ok"] is True, layer["blocked_reason"]
    assert layer["status"] == "reachable", "the stock verdict was hard-coded False"
    assert layer["exit_code"] == 0
    assert {probe["name"] for probe in layer["probes"]} == {"system_one_flags", "advise"}
    assert all(probe["ok"] for probe in layer["probes"])
    assert "provider_mode=llm_system_one_adapter" in advise["detail"], (
        "the probe now reports the wired provider instead of a disabled evaluator"
    )


def test_the_runtime_verdict_is_stricter_than_the_stock_probe(tmp_path):
    """The stock probe accepts an *empty* evaluation; the runtime status does not.

    Measured both ways on the same runtime: with the settings enabled and no
    credential, the stock probe reports both probes ``ok`` (an evaluation came
    back, it just carried no answers and ``provider_mode="disabled"``) — that is
    the CLI-surface reading, and it is deliberately blunt. ``runtime status`` goes
    further: it refuses to call that reachability and names the missing credential
    through its own ``route`` verdict and causes.
    """
    from skaldir import cli_contract

    runtime.enable(tmp_path)
    stock = cli_contract.run_selftest(tmp_path, layers=["ROUTE"])["layers"][0]
    probe = next(item for item in stock["probes"] if item["name"] == "advise")
    assert probe["ok"] is True
    assert "provider_mode=disabled" in probe["detail"]
    assert stock["ok"] is True, "the CLI probe reads its own probes and reports reachable"

    verdict = runtime.status(tmp_path, **_isolated(tmp_path))
    assert verdict["route"]["reachable"] is False
    assert verdict["cli_contract_probe"]["ok"] is True, (
        "the CLI probe is the blunter reading — the runtime verdict is the strict one"
    )
    assert any("disabled" in warning for warning in verdict["warnings"]) or any(
        "provider_unconfigured" in row["detail"]
        for row in verdict["causes"]
        if row["state"] == "pending"
    )


def test_a_provider_contract_refusal_is_reported_not_raised(tmp_path, monkeypatch):
    """A refused provider answer is "no advisory" — reported, and exit 3.

    Measured live: deepseek-chat once answered ``routing.missing_inputs.url`` with a
    choice that was not the argmax of its own distribution; the façade refuses such an
    answer (never a fabricated decision). A diagnostic must report that state instead
    of crashing with exit 7, and the decide CLI must not pretend a decision happened.
    """
    from skaldir.system_one import SystemOneContractError

    refusal = (
        "routing.missing_inputs.url: choice 'unknown' is not the argmax of its own "
        "distribution"
    )

    def _explode(*args, **kwargs):
        raise SystemOneContractError(refusal)

    monkeypatch.setattr(runtime, "decide", _explode)
    report = runtime.status(tmp_path, probe="fixture", **_isolated(tmp_path))
    assert report["route"]["reachable"] is False
    assert report["route"]["exit_code"] == 3
    assert report["decision"] is None
    assert any("provider_contract_refusal" in row["detail"] for row in report["causes"])
    assert any("refused" in warning for warning in report["warnings"])

    request = dict(SELFTEST_CASES["tool_routing"])
    request["kind"] = "tool_routing"
    path = tmp_path / "request.json"
    path.write_text(json.dumps(request), encoding="utf-8")
    monkeypatch.setattr(runtime, "decide_through_runtime", _explode)
    code = runtime.main(
        [
            "decide",
            "--kind",
            "tool_routing",
            "--probe",
            "fixture",
            "--runtime",
            str(tmp_path),
            "--request",
            str(path),
            "--json",
        ]
    )
    assert code == runtime.EXIT_BLOCKED


# --------------------------------------------------------------------------- #
# the standalone CLI                                                            #
# --------------------------------------------------------------------------- #


def test_status_cli_prints_one_json_document(tmp_path, capsys):
    code = runtime.main(["status", "--json", "--runtime", str(tmp_path), "--probe", "fixture"])
    out = capsys.readouterr().out
    document = json.loads(out)
    assert code == 0
    assert document["command"] == "jev.runtime status"
    assert document["data"]["route"]["reachable"] is True
    assert document["evidence"], "a success without evidence is not a success"
    assert out.strip().startswith("{") and out.strip().endswith("}")


def test_status_cli_exits_three_when_blocked(tmp_path, capsys):
    code = runtime.main(["status", "--json", "--runtime", str(tmp_path), "--probe", "disabled"])
    document = json.loads(capsys.readouterr().out)
    assert code == 3
    assert document["exit_code"] == 3
    assert document["ok"] is False
    assert document["error"]["code"] == "blocked"
    assert document["next_actions"], "a blocked status names how to unblock it"


def test_enable_cli_reports_the_diff_and_writes(tmp_path, capsys):
    code = runtime.main(["enable", "--json", "--runtime", str(tmp_path)])
    document = json.loads(capsys.readouterr().out)
    assert code == 0
    assert document["data"]["wrote"] is True
    assert document["data"]["changed"]
    assert (tmp_path / "settings.yaml").is_file()


def test_decide_cli_runs_a_kind_through_the_runtime(tmp_path, capsys):
    request = dict(SELFTEST_CASES["voice_triage"])
    request["kind"] = "voice_triage"
    path = tmp_path / "request.json"
    path.write_text(json.dumps(request), encoding="utf-8")
    code = runtime.main(
        [
            "decide",
            "--kind",
            "voice_triage",
            "--probe",
            "fixture",
            "--runtime",
            str(tmp_path),
            "--request",
            str(path),
            "--json",
        ]
    )
    document = json.loads(capsys.readouterr().out)
    assert code == 0
    assert document["data"]["decision"]["kind"] == "voice_triage"
    assert document["data"]["decision"]["provider_mode"] == "replay_fixture"
    assert document["data"]["rules_rechecked"]["advisory_only"] is True


def test_an_unknown_flag_value_is_a_usage_error(tmp_path, capsys):
    code = runtime.main(["status", "--json", "--runtime", str(tmp_path), "--probe", "nope"])
    captured = capsys.readouterr()
    assert code == runtime.EXIT_USAGE
    assert captured.err or captured.out

# --------------------------------------------------------------------------- #
# 6. the TypeSafe config decides (godsv.jev before godsv.llm)                     #
# --------------------------------------------------------------------------- #


def _config_file(tmp_path: Path, *, typesafe: dict | None, general: dict | None) -> Path:
    """A provider config file with one/both blocks, built as the setup flow writes them."""
    import yaml

    godsv: dict = {}
    if general is not None:
        godsv["llm"] = dict(general)
    if typesafe is not None:
        godsv["jev"] = dict(typesafe)
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"godsv": godsv}, sort_keys=False), encoding="utf-8")
    return path


def _typesafe_block(base_url: str, model: str = "typesafe-model") -> dict:
    return {
        "provider": "openai-compatible",
        "base_url": base_url,
        "model": model,
        "api_key_env": "TYPESAFE_KEY",
        "stream": False,
    }


def _general_block(base_url: str, model: str = "general-model") -> dict:
    return {
        "provider": "openai-compatible",
        "base_url": base_url,
        "model": model,
        "api_key_env": "GENERAL_KEY",
    }


def _dead_url() -> str:
    """A base_url on a port nobody listens on — the fallback that must not be used."""
    import socket

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return f"http://127.0.0.1:{port}/v1"


class _Recorder:
    """Thread-safe request log for the stub endpoint below."""

    def __init__(self) -> None:
        import threading

        self.requests: list[dict] = []
        self.lock = threading.Lock()


def _answers_for_prompt(prompt: str) -> dict:
    """The wire payload a *healthy* provider would send for the question set it was asked.

    The question ids are read out of the request itself (the adapter serializes every
    registered question into the prompt), so the stub answers exactly the set that asked —
    an extra or a missing id is a contract violation both ways, and a fixed payload would
    only ever fit one caller. The readings come from the fixture evaluator's own table of
    healthy values, so the decisions that come back are real ones (``status == "ok"``).
    """
    lines = prompt.splitlines()
    try:
        start = lines.index("QUESTIONS:") + 1
        end = lines.index("STATE:")
    except ValueError:  # not the adapter's prompt shape
        return {"answers": {}}
    fixture = FixtureJevEvaluator()
    answers: dict[str, dict] = {}
    for line in lines[start:end]:
        text = line.strip()
        if not text.startswith("{"):
            continue
        spec = json.loads(text)
        question = SimpleNamespace(
            id=str(spec.get("id") or ""),
            kind=str(spec.get("kind") or ""),
            options=list(spec.get("options") or []),
            legend=list(spec.get("legend") or []),
        )
        if not question.id:
            continue
        answers[question.id] = fixture._payload(question)
    return {"answers": answers}


def _prompt_of(body: bytes) -> str:
    """The prompt the adapter sent, out of the OpenAI request body."""
    try:
        payload = json.loads(body.decode("utf-8"))
    except ValueError:
        return ""
    messages = payload.get("messages") or []
    for message in reversed(list(messages)):
        if isinstance(message, dict) and message.get("content"):
            return str(message["content"])
    return ""


def _stub_endpoint(recorder: _Recorder, *, model: str = "stub-model"):
    """A real, threaded HTTP endpoint that answers one chat completion (stdlib only)."""
    import http.server

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):  # keep the test output clean
            return

        def do_POST(self):  # noqa: N802 — the stdlib's spelling
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            prompt = _prompt_of(raw)
            with recorder.lock:
                recorder.requests.append({"path": self.path, "prompt": prompt})
            body = json.dumps(
                {
                    "id": "stub-1",
                    "object": "chat.completion",
                    "model": model,
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": json.dumps(_answers_for_prompt(prompt)),
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 11,
                        "completion_tokens": 7,
                        "total_tokens": 18,
                    },
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    class Server(http.server.ThreadingHTTPServer):
        daemon_threads = True
        allow_reuse_address = True

    return Server(("127.0.0.1", 0), Handler)


class _Endpoint:
    """Context manager: a stub endpoint on a real port, torn down deterministically."""

    def __init__(self) -> None:
        self.recorder = _Recorder()
        self.server = _stub_endpoint(self.recorder)
        self.hits = 0
        self.thread: Any = None

    def __enter__(self):
        import threading

        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def base_url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}/v1"

    def count(self) -> int:
        """Requests seen *so far* — readable inside the block (``hits`` is teardown)."""
        return len(self.recorder.requests)

    def question_ids(self) -> set[str]:
        """Every question id the endpoint was actually asked, across all requests."""
        ids: set[str] = set()
        for row in list(self.recorder.requests):
            lines = str(row.get("prompt") or "").splitlines()
            try:
                start, end = lines.index("QUESTIONS:") + 1, lines.index("STATE:")
            except ValueError:
                continue
            for line in lines[start:end]:
                text = line.strip()
                if text.startswith("{"):
                    ids.add(str(json.loads(text).get("id") or ""))
        return ids

    def __exit__(self, *exc):
        self.hits = len(self.recorder.requests)
        self.server.shutdown()
        self.server.server_close()
        if self.thread is not None:
            self.thread.join(timeout=10)
        return False


def test_the_typesafe_block_wins_when_both_blocks_are_configured(tmp_path):
    """The whole point: the block the user configured for TypeSafe is the one read."""
    path = _config_file(
        tmp_path,
        typesafe=_typesafe_block("https://typesafe.example/v1"),
        general=_general_block("https://general.example/v1"),
    )
    resolution = runtime.provider_resolution(
        env={"TYPESAFE_KEY": "kv-not-a-real-key"}, config_path=str(path)
    )

    assert resolution["source"] == runtime.SOURCE_TYPESAFE == "godsv.jev"
    assert resolution["client"] is not None, resolution["problem"]
    assert resolution["client"].config.base_url == "https://typesafe.example/v1"
    assert resolution["client"].config.model == "typesafe-model"
    assert resolution["client"].config.api_key_env == "TYPESAFE_KEY", (
        "the key comes from the variable the TypeSafe block names, not the general one"
    )
    assert resolution["block"]["api_key"] == "set"
    assert "kv-not-a-real-key" not in json.dumps(resolution["block"]), (
        "never a key, only its name"
    )


def test_the_named_registry_entry_is_used_when_the_projection_is_absent(tmp_path):
    """``godsv.providers.entries.jev`` is the same provider, and it is read too."""
    import yaml

    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "godsv": {
                    "llm": _general_block("https://general.example/v1"),
                    "providers": {
                        "default": "llm",
                        "entries": {"jev": _typesafe_block("https://entry.example/v1")},
                    },
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    resolution = runtime.provider_resolution(
        env={"TYPESAFE_KEY": "kv-not-a-real-key"}, config_path=str(path)
    )

    assert resolution["source"] == runtime.SOURCE_TYPESAFE_ENTRY
    assert resolution["client"].config.base_url == "https://entry.example/v1"


def test_the_general_block_is_the_fallback_when_no_typesafe_block_exists(tmp_path):
    """An installation that never ran `skaldir setup` keeps its old behaviour."""
    path = _config_file(
        tmp_path, typesafe=None, general=_general_block("https://general.example/v1")
    )
    resolution = runtime.provider_resolution(env={}, config_path=str(path))

    assert resolution["source"] == runtime.SOURCE_GENERAL
    assert resolution["client"].config.base_url == "https://general.example/v1"


def test_a_configured_typesafe_block_blocks_by_name_and_never_falls_back(tmp_path):
    """A present-but-unusable ``godsv.jev`` is a blocker — not a silent substitution.

    The general block in the same file is perfectly usable; using it would decide on an
    endpoint (and a key) the user never chose for Jev.
    """
    path = _config_file(
        tmp_path,
        typesafe=_typesafe_block("https://typesafe.example/v1"),
        general=_general_block("https://general.example/v1"),
    )
    resolution = runtime.provider_resolution(env={}, config_path=str(path))

    assert resolution["client"] is None
    assert resolution["source"] == runtime.SOURCE_TYPESAFE
    assert "TYPESAFE_KEY" in resolution["problem"], resolution["problem"]

    flags = SystemOneFlags(enabled=True, provider=PROVIDER_LLM_ADAPTER)
    assert "TYPESAFE_KEY" in runtime.provider_problem(flags, env={}, config_path=str(path))


def test_an_invalid_typesafe_block_is_reported_as_the_godsv_jev_problem(tmp_path):
    """A malformed TypeSafe block names itself — the general block does not cover it."""
    path = _config_file(
        tmp_path,
        typesafe=_typesafe_block("not-a-url"),
        general=_general_block("https://general.example/v1"),
    )
    resolution = runtime.provider_resolution(env={}, config_path=str(path))

    assert resolution["client"] is None
    assert resolution["source"] == runtime.SOURCE_TYPESAFE
    assert "base_url" in resolution["problem"], resolution["problem"]


def test_a_jev_decision_really_goes_to_the_typesafe_endpoint(tmp_path, capsys):
    """End to end: the request lands on the TypeSafe endpoint, not on the general one.

    ``godsv.llm`` in the same file points at a **dead port**, so a decision that comes
    back ``ok`` can only have been answered by the TypeSafe stub.
    """
    with _Endpoint() as stub:
        path = _config_file(
            tmp_path,
            typesafe=_typesafe_block(stub.base_url()),
            general=_general_block(_dead_url()),
        )
        runtime.enable(tmp_path)
        report = runtime.status(tmp_path, probe="settings", env={}, config_path=str(path))

        assert stub.count() > 0, "the TypeSafe endpoint was never asked"
        assert report["route"]["reachable"] is True, report["route"]["blocked_reason"]
        assert report["decision"]["status"] == "ok", report["decision"]["refusal_reason"]
        assert report["provider_config"]["source"] == runtime.SOURCE_TYPESAFE
        assert report["provider_config"]["block"]["model"] == "typesafe-model"

        code = runtime.main(
            [
                "status",
                "--probe",
                "settings",
                "--runtime",
                str(tmp_path),
                "--config-path",
                str(path),
                "--json",
            ]
        )
    document = json.loads(capsys.readouterr().out)

    assert code == 0
    notes = " ".join(str(row.get("note")) for row in document["evidence"])
    assert "godsv.jev" in notes, "the evidence must name which config decided"
    assert "typesafe-model" in notes
    assert "sk-" not in json.dumps(document)


def test_a_jev_decision_falls_back_to_the_general_endpoint(tmp_path):
    """No TypeSafe block: the general stub answers, and the payload says so."""
    with _Endpoint() as stub:
        path = _config_file(tmp_path, typesafe=None, general=_general_block(stub.base_url()))
        runtime.enable(tmp_path)
        report = runtime.status(tmp_path, probe="settings", env={}, config_path=str(path))

        assert stub.count() > 0
        assert report["decision"]["status"] == "ok", report["decision"]["refusal_reason"]
        assert report["provider_config"]["source"] == runtime.SOURCE_GENERAL
        assert report["provider_config"]["block"]["model"] == "general-model"


def test_the_typesafe_wiring_cannot_bypass_the_four_prohibitions(tmp_path, capsys):
    """The new provider edge is still only ROUTE: the four hard rules hold on it."""
    with _Endpoint() as stub:
        path = _config_file(
            tmp_path,
            typesafe=_typesafe_block(stub.base_url()),
            general=_general_block(_dead_url()),
        )
        runtime.enable(tmp_path)
        code = runtime.main(
            [
                "decide",
                "--kind",
                "tool_routing",
                "--probe",
                "settings",
                "--runtime",
                str(tmp_path),
                "--config-path",
                str(path),
                "--json",
            ]
        )
        document = json.loads(capsys.readouterr().out)
        assert code == 0, document
        assert document["data"]["provider_config"]["source"] == "godsv.jev"
        decision = document["data"]["decision"]
        assert decision["status"] == "ok", decision["refusal_reason"]
        assert decision["authority"] == "advisory", "R3: Jev never certifies"
        assert decision["certifies"] is False, "R1: never a PASS"
        assert decision["grants"] == [], "R2: never a grant"
        assert document["data"]["rules_rechecked"]["violations"] == []
        assert document["data"]["rules_rechecked"]["advisory_only"] is True
        assert _forbidden_names(decision["answer"]) == set(), (
            "R1/R2/R3: a forbidden field name appeared in an answer"
        )
        assert set(decision) == set(C.JevDecision.model_fields)

        # a denied-only candidate set stays un-routable, provider or not
        payload = json.loads(json.dumps(SELFTEST_CASES["tool_routing"]))
        for item in payload["candidates"]:
            item["permission_state"] = "denied"
        denied, _flags, _problem = runtime.decide_through_runtime(
            payload, runtime=tmp_path, probe="settings", env={}, config_path=str(path)
        )
        assert stub.count() == 1, "a denied-only candidate set is answered without a provider"
        assert denied.status == "ok"
        assert denied.answer.chosen_kind == "no_route" and denied.answer.route is None
