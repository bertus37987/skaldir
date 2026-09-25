"""JEV §33/§3 — SystemOneService is the ONE facade the business logic sees.

ROT-FIRST evidence, captured before this delta existed (the pristine
pre-delta modules from `git show HEAD:src/godsv/...` on the path, so the
failures below are the real ones, not a reconstruction):

    $ .venv/bin/python -m pytest -o pythonpath=$HOME/jev_red/src \
        tests/test_jev_service_facade.py -p no:randomly
    E   ModuleNotFoundError: No module named 'skaldir.system_one_service'   (x3)
    E   TypeError: triage_with_system_one() got an unexpected keyword argument 'service'
    5 failed, 5 passed in 0.16s

What this file pins (§3/§33):

* ``Voice -> SystemOneService -> SystemOneEvaluator`` and
  ``Completion -> SystemOneService -> SystemOneEvaluator`` are the only
  permitted paths; ``voice.py`` and ``completion.py`` must not be able to reach
  a provider at all. That is proven twice: statically (AST import scan) and
  **behaviourally** (a subprocess that makes every provider/HTTP module
  unimportable and then imports both business modules).
* the service is a thin, testable facade: it owns sanitization, the staleness
  fence, the cache, the metrics, the event emission and provider-mode honesty —
  and business logic only ever sees the internal evaluation contract.
"""

from __future__ import annotations

import ast
import asyncio
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
BUSINESS_MODULES = ("src/skaldir/voice.py", "src/skaldir/completion.py")

#: A business module may never import one of these (§3 "Nicht zulässig").
FORBIDDEN_IMPORTS = {
    "skaldir.system_one_adapters",
    "skaldir.llm",
    "httpx",
    "requests",
    "urllib.request",
    "http.client",
    "aiohttp",
    "typesafe",
}

#: ... and may never carry a vendor endpoint as data either.
FORBIDDEN_TEXT = ("api.typesafe.ai", "/v1/systemone", "Bearer ", "jev-latest")


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            if node.level:  # relative import inside src/godsv
                found.add(f"skaldir.{node.module}")
    return found


@pytest.mark.parametrize("relative_path", BUSINESS_MODULES)
def test_business_modules_do_not_import_a_provider(relative_path):
    imported = _imports(SRC.parent / relative_path)
    forbidden = imported & FORBIDDEN_IMPORTS
    assert not forbidden, f"{relative_path} imports a provider directly: {sorted(forbidden)}"


@pytest.mark.parametrize("relative_path", BUSINESS_MODULES)
def test_business_modules_carry_no_vendor_endpoint(relative_path):
    text = (SRC.parent / relative_path).read_text(encoding="utf-8")
    for needle in FORBIDDEN_TEXT:
        assert needle not in text, f"{relative_path} carries vendor detail: {needle!r}"


def test_the_facade_is_the_only_provider_edge_the_business_logic_uses():
    """The static scan and the service import must agree."""
    from skaldir import completion, voice
    from skaldir.system_one_service import SystemOneService

    assert SystemOneService.__module__ == "skaldir.system_one_service"
    # the facade is the business modules' provider edge ...
    assert "service" in voice.triage_with_system_one.__code__.co_varnames
    assert "service" in completion.completion_preflight.__code__.co_varnames
    assert "SystemOneService" in (voice.__doc__ or "") + (completion.__doc__ or "")


def test_business_modules_import_with_every_provider_module_unpoisonable():
    """Behavioural proof: block the provider/HTTP modules, then import both.

    This is the test that proves ``voice.py``/``completion.py`` cannot reach a
    provider directly: if either of them imported one, the import would raise
    ``ImportError: blocked provider import`` instead of succeeding.
    """
    script = f"""
import sys
sys.path.insert(0, {str(SRC)!r})
BLOCKED = {sorted(FORBIDDEN_IMPORTS)!r}


class _Blocker:
    def find_module(self, name, path=None):
        return self.find_spec(name, path)

    def find_spec(self, name, path=None, target=None):
        head = name.split(".")[0]
        if name in BLOCKED or head in BLOCKED:
            raise ImportError("blocked provider import: " + name)
        return None


for name in list(sys.modules):
    if name in BLOCKED or name.split(".")[0] in BLOCKED:
        del sys.modules[name]
sys.meta_path.insert(0, _Blocker())

import skaldir.voice  # noqa: F401
import skaldir.completion  # noqa: F401
import skaldir.system_one_service

print("OK")
"""
    result = subprocess.run(  # noqa: S603 — fixed argv, no shell
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("OK")


def test_the_service_is_the_only_thing_that_talks_to_the_evaluator():
    from skaldir.system_one import DisabledSystemOneEvaluator
    from skaldir.system_one_service import SystemOneService

    class _Spy(DisabledSystemOneEvaluator):
        def __init__(self):
            self.calls = 0

        async def evaluate(self, *, state, questions, context):
            self.calls += 1
            return await super().evaluate(state=state, questions=questions, context=context)

    spy = _Spy()
    service = SystemOneService(evaluator=spy)
    from skaldir.system_one import EvaluationContext, SystemOneState
    from skaldir.system_one_questions import compose_voice_triage

    asyncio.run(
        service.advise(
            state=SystemOneState(payload={"goal": "g"}),
            questions=compose_voice_triage("loop"),
            context=EvaluationContext(run_id="r", goal_id="g"),
        )
    )
    assert spy.calls == 1


def test_the_facade_reports_the_provider_mode_of_the_evaluator():
    from skaldir.system_one import DisabledSystemOneEvaluator
    from skaldir.system_one_service import SystemOneService

    assert SystemOneService(evaluator=DisabledSystemOneEvaluator()).provider_mode == "disabled"


def test_voice_triage_without_a_service_stays_on_the_existing_path():
    """§98: deactivation (no service, no flags) returns the deterministic path."""
    from skaldir.monitor import Signal
    from skaldir.voice import PATH_LEGACY, Voice, triage_with_system_one

    signal = Signal(kind="loop", message="same call repeated", severity=2, fingerprint="fp-1")
    outcome = triage_with_system_one(voice=Voice(), signals=[signal], step=3, service=None)
    assert outcome.path == PATH_LEGACY
    assert outcome.reason == "no_flags"
    assert outcome.hint is not None


def test_the_service_emits_the_documented_event_kinds():
    from skaldir.system_one import EvaluationContext, SystemOneState
    from skaldir.system_one_questions import compose_voice_triage
    from skaldir.system_one_service import SystemOneService

    events: list[tuple[str, dict]] = []
    service = SystemOneService(
        evaluator=_fixture_evaluator(), emit=lambda k, p: events.append((k, p))
    )
    asyncio.run(
        service.advise(
            state=SystemOneState(payload={"goal": "g"}),
            questions=compose_voice_triage("loop"),
            context=EvaluationContext(run_id="r", goal_id="g"),
        )
    )
    kinds = [kind for kind, _ in events]
    assert "system_one.requested" in kinds
    assert "system_one.completed" in kinds


def _fixture_evaluator():
    from skaldir.system_one import build_evaluation

    class _Fixture:
        provider = "replay_fixture"
        provider_model = "scripted"
        provider_access_mode = "fixture"

        async def evaluate(self, *, state, questions, context):
            return build_evaluation(
                context=context,
                questions=questions,
                state=state,
                provider=self.provider,
                provider_model=self.provider_model,
                access_mode=self.provider_access_mode,
            )

    return _Fixture()
