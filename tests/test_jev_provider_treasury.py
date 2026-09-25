"""JEV-08/JEV-07 (§16) — the TypeSafe ConnectionRef resolved against the REAL
treasury, and the proof that the key never leaves the broker boundary.

ROT-FIRST evidence, captured before the module existed:

    $ .venv/bin/python -c "import godsv.system_one_treasury"
    ModuleNotFoundError: No module named 'godsv.system_one_treasury'

    $ .venv/bin/python -m pytest tests/test_jev_provider_treasury.py -p no:randomly
    ERROR tests/test_jev_provider_treasury.py - ModuleNotFoundError:
        No module named 'godsv.system_one_treasury'
    !!! Interrupted: 1 error during collection !!!

What this file pins (behaviour, never source text):

* §16: the ConnectionRef is exactly ``provider=typesafe, target=system_one,
  scope=evaluate, secret_handle, status, revision`` — and it is resolved against
  the repo's real :class:`godsv.treasury.Vault`, not a second store;
* §16: the key may not appear in prompt, event, model state, memory, logs,
  diagnostics or screenshots. The vault here is real (encrypted at rest, scrypt
  + Fernet) and the secret is a real stored value, so the negative test
  (:func:`test_the_leak_guard_fails_when_a_key_really_leaks`) really is the
  assertion that would have to fail if the key leaked;
* §16: with no handle configured the evaluator stays
  ``blocked_external_access`` — no configured connection, no call, no invented
  answer;
* the adapter hands the *reference* to the evaluator (never the material), and
  the material is fetched per attempt by the header provider, used once and
  dropped.

Evidence class: integration (real Vault + real TreasuryState + the real
evaluator) with a transport double standing in for the network. NO live TypeSafe
call happened: ``live_jev`` is False everywhere except where a double transport
makes the adapter label a result live, which is a labelling contract, not a live
call. No real screenshot exists on this host (``skaldir cu check``: container
GUI image missing), so the screenshot clause is ``not_run``, not "verified".
"""

from __future__ import annotations

import asyncio
import json

import pytest

from skaldir.secretscan import redact, scan
from skaldir.system_one import (
    ACCESS_BLOCKED,
    EvaluationContext,
    SystemOneBlockedExternal,
    SystemOneState,
)
from skaldir.system_one_adapters import TypeSafeJevEvaluator
from skaldir.system_one_questions import Question, QuestionSet
from skaldir.system_one_treasury import (
    CONNECTION_FIELDS,
    CONNECTION_PROVIDER,
    CONNECTION_SCOPE,
    CONNECTION_STATUSES,
    CONNECTION_TARGET,
    DEFAULT_HANDLE,
    ConnectionRef,
    GuardedEmitter,
    SystemOneTreasuryAdapter,
    TreasuryLeakError,
    assert_no_secret,
)
from skaldir.treasury import TreasuryState, Vault

MASTER = "jev-provider-test-master-key"
#: A real stored secret. It must never appear in any recorded artifact.
SECRET = "tsk_" + "Z9y8X7w6V5u4T3s2R1q0P9o8N7m6L5kJ"


@pytest.fixture
def vault(tmp_path):
    return Vault(tmp_path / "vault.json", MASTER)


@pytest.fixture
def treasury_state(tmp_path):
    handle = TreasuryState(tmp_path / "treasury.db")
    yield handle
    handle.close()


def _questions() -> QuestionSet:
    return QuestionSet(
        id="treasury_contract_test",
        version="v1",
        owner="test",
        questions=(
            Question(
                id="test.noul.question",
                version="v1",
                owner="test",
                kind="noul",
                text="Is the connection usable?",
                group="g",
            ),
        ),
    )


def _answers() -> dict:
    return {"test.noul.question": {"value": 0.4}}


def _context() -> EvaluationContext:
    return EvaluationContext(run_id="run-1", goal_id="goal-1", goal_revision=1)


def _run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# the ConnectionRef shape (§16)                                                #
# --------------------------------------------------------------------------- #


def test_the_connection_ref_is_exactly_the_documented_shape():
    assert CONNECTION_FIELDS == (
        "provider",
        "target",
        "scope",
        "secret_handle",
        "status",
        "revision",
    )
    ref = ConnectionRef(
        secret_handle=DEFAULT_HANDLE, status="active", revision="rev-abc123"
    )
    assert ref.provider == CONNECTION_PROVIDER == "typesafe"
    assert ref.target == CONNECTION_TARGET == "system_one"
    assert ref.scope == CONNECTION_SCOPE == "evaluate"
    assert tuple(ref.as_dict()) == CONNECTION_FIELDS
    assert ref.active is True
    with pytest.raises(ValueError):
        ConnectionRef(secret_handle="not-a-handle", status="active")
    with pytest.raises(ValueError):
        ConnectionRef(secret_handle=DEFAULT_HANDLE, status="maybe")


def test_an_unconfigured_connection_is_not_an_active_one():
    ref = SystemOneTreasuryAdapter(vault=None, handle=DEFAULT_HANDLE).connection_ref()
    assert ref.status in CONNECTION_STATUSES
    assert ref.status != "active"
    assert ref.active is False
    assert ref.as_dict()["secret_handle"] == DEFAULT_HANDLE


def test_a_missing_handle_resolves_to_missing_and_not_to_an_error(vault, treasury_state):
    adapter = SystemOneTreasuryAdapter(vault, handle=DEFAULT_HANDLE, state=treasury_state)
    ref = adapter.connection_ref()
    assert ref.status == "missing"
    assert ref.revision == ""
    assert adapter.credential_reference(_context()) is None


def test_the_connection_ref_resolves_against_the_real_vault(vault, treasury_state):
    vault.add(service="typesafe", name="systemone", secret=SECRET, scopes=["use", "evaluate"])
    adapter = SystemOneTreasuryAdapter(vault, handle=DEFAULT_HANDLE, state=treasury_state)
    ref = adapter.connection_ref()
    assert ref.status == "active"
    assert ref.secret_handle == DEFAULT_HANDLE
    assert ref.revision.startswith("rev-")
    # the revision is derived from the entry, not stored: same entry, same revision
    assert SystemOneTreasuryAdapter(vault, handle=DEFAULT_HANDLE).connection_ref().revision == (
        ref.revision
    )


def test_a_handle_without_the_evaluate_scope_is_refused(vault, treasury_state):
    vault.add(service="typesafe", name="systemone", secret=SECRET, scopes=["use"])
    adapter = SystemOneTreasuryAdapter(vault, handle=DEFAULT_HANDLE, state=treasury_state)
    ref = adapter.connection_ref()
    assert ref.status == "unauthorized"
    assert adapter.credential_reference(_context()) is None


def test_a_revoked_handle_stops_resolving_immediately(vault, treasury_state):
    vault.add(service="typesafe", name="systemone", secret=SECRET, scopes=["use", "evaluate"])
    adapter = SystemOneTreasuryAdapter(vault, handle=DEFAULT_HANDLE, state=treasury_state)
    assert adapter.connection_ref().status == "active"
    vault.revoke(DEFAULT_HANDLE)
    assert adapter.connection_ref().status == "missing"
    assert adapter.credential_reference(_context()) is None


def test_an_expired_handle_is_reported_as_expired(vault, treasury_state):
    import time

    vault.add(
        service="typesafe",
        name="systemone",
        secret=SECRET,
        scopes=["use", "evaluate"],
        expires_at=time.time() - 60,
    )
    adapter = SystemOneTreasuryAdapter(vault, handle=DEFAULT_HANDLE, state=treasury_state)
    assert adapter.connection_ref().status == "expired"
    assert adapter.credential_reference(_context()) is None


# --------------------------------------------------------------------------- #
# the reference crosses the boundary, never the material                        #
# --------------------------------------------------------------------------- #


def test_the_credential_resolver_returns_a_reference_and_never_the_key(vault, treasury_state):
    vault.add(service="typesafe", name="systemone", secret=SECRET, scopes=["use", "evaluate"])
    adapter = SystemOneTreasuryAdapter(vault, handle=DEFAULT_HANDLE, state=treasury_state)
    reference = adapter.credential_reference(_context())
    assert reference
    blob = json.dumps(reference, default=str)
    assert SECRET not in blob
    assert reference["handle"] == DEFAULT_HANDLE
    assert reference["provider"] == "typesafe"
    assert reference["scope"] == "evaluate"
    assert reference["revision"].startswith("rev-")


def test_the_evaluator_stays_blocked_without_a_configured_handle(vault, treasury_state):
    adapter = SystemOneTreasuryAdapter(vault, handle=DEFAULT_HANDLE, state=treasury_state)
    evaluator = TypeSafeJevEvaluator(
        credential_resolver=adapter.credential_reference,
        transport=None,
    )
    with pytest.raises(SystemOneBlockedExternal) as excinfo:
        _run(
            evaluator.evaluate(
                state=SystemOneState(payload={"task": "x"}),
                questions=_questions(),
                context=_context(),
            )
        )
    assert excinfo.value.blocker["state"] == ACCESS_BLOCKED
    assert excinfo.value.blocker["live_jev"] is False


def test_a_configured_connection_reaches_the_transport_with_a_bearer_header(vault, treasury_state):
    """Integration: real Vault → adapter → evaluator → transport double."""
    vault.add(service="typesafe", name="systemone", secret=SECRET, scopes=["use", "evaluate"])
    adapter = SystemOneTreasuryAdapter(vault, handle=DEFAULT_HANDLE, state=treasury_state)
    seen: dict = {}

    async def transport(request, headers=None):
        seen["request"] = request
        seen["headers"] = dict(headers or {})
        return {"answers": _answers(), "usage": {}, "sdk_retries": 0}

    evaluator = TypeSafeJevEvaluator(
        credential_resolver=adapter.credential_reference,
        transport=transport,
        auth_headers=adapter.authorization_headers,
    )
    evaluation = _run(
        evaluator.evaluate(
            state=SystemOneState(payload={"task": "render_scene"}),
            questions=_questions(),
            context=_context(),
        )
    )
    assert seen["headers"]["Authorization"] == f"Bearer {SECRET}"
    assert evaluation.provider == "typesafe_jev"
    # the key is in the transport header and NOWHERE else
    artifacts = json.dumps(
        {
            "request": seen["request"],
            "usage": evaluation.usage,
            "evidence": evaluation.evidence(),
            "evaluation": evaluation.as_dict(),
            "raw_ref": evaluation.raw_ref,
        },
        default=str,
    )
    assert SECRET not in artifacts
    remaining, findings = redact(artifacts)
    assert SECRET not in remaining
    assert findings == []


# --------------------------------------------------------------------------- #
# the leak guard — the test that has to fail if the key leaks                    #
# --------------------------------------------------------------------------- #


def test_the_leak_guard_fails_when_a_key_really_leaks():
    """The guard's exact-match check works regardless of the redactor's patterns.

    ``secretscan``'s generic rule only fires in key-like *context*
    (``api_key``/``secret``/``token``); the guard also matches the material
    itself, which is the case that matters here.
    """
    leaking = {"event": "system_one_requested", "state": {"payload": {"api_key": SECRET}}}
    with pytest.raises(TreasuryLeakError):
        assert_no_secret(leaking, SECRET, where="test")
    survey = scan(f"api_key: {SECRET}")
    assert survey, "the project redactor sees the key in key-like context"
    bare = {"event": "system_one_requested", "state": {"payload": {"key": SECRET}}}
    assert scan(json.dumps(bare)) == [], "the redactor alone would have missed this one"
    with pytest.raises(TreasuryLeakError):
        assert_no_secret(bare, SECRET, where="test")
    clean = {"event": "system_one_requested", "state": {"payload": {"task": "x"}}}
    assert_no_secret(clean, SECRET, where="test")


def test_a_guarded_emitter_refuses_to_record_a_payload_with_the_key():
    recorded: list[dict] = []
    emitter = GuardedEmitter(
        recorder=lambda kind, payload: recorded.append(dict(payload)), secret=SECRET
    )
    emitter("system_one_completed", {"evaluation_id": "soe-1"})
    assert recorded, "a clean payload is recorded"
    with pytest.raises(TreasuryLeakError):
        emitter("system_one_failed", {"code": "connection_invalid", "detail": f"Bearer {SECRET}"})
    assert recorded == [{"evaluation_id": "soe-1"}], "the leak was refused, not recorded"


def test_the_guarded_emitter_works_from_the_real_adapter(vault, treasury_state):
    """The adapter's own emitter path is guarded — with the real stored key."""
    vault.add(service="typesafe", name="systemone", secret=SECRET, scopes=["use", "evaluate"])
    adapter = SystemOneTreasuryAdapter(vault, handle=DEFAULT_HANDLE, state=treasury_state)
    recorded: list[tuple[str, dict]] = []
    emitter = adapter.guarded_emitter(lambda kind, payload: recorded.append((kind, payload)))
    emitter("system_one_evaluation", {"provider": "typesafe_jev", "usage": {"input_tokens": 3}})
    assert recorded and recorded[0][0] == "system_one_evaluation"
    assert SECRET not in json.dumps(recorded, default=str)
    with pytest.raises(TreasuryLeakError):
        emitter("system_one_requested", {"authorization": f"Bearer {SECRET}"})


def test_the_key_is_absent_from_the_request_state_and_ref():
    """Prompt/state/ref carry the task, never the credential."""
    ref = ConnectionRef(secret_handle=DEFAULT_HANDLE, status="active", revision="rev-1")
    state = SystemOneState(payload={"task": "render_scene", "goal_revision": 3})
    blob = json.dumps(
        {"ref": ref.as_dict(), "state": state.as_dict(), "scope": state.input_scope}, default=str
    )
    assert SECRET not in blob
    assert DEFAULT_HANDLE in blob, "the handle is what travels, and it is not a secret"

    # and the negative direction: the same check catches a payload that does carry it
    leaked = dict(ref.as_dict())
    leaked["secret_handle"] = SECRET
    with pytest.raises(TreasuryLeakError):
        assert_no_secret(leaked, SECRET, where="connection ref")
