"""Wave 6 — the treasury must influence real tool calls, not just its own tests.

Measured 17.09.: ``Broker.run_with_secret`` had **no production caller** anywhere in ``src/`` or
``integrations/`` — a generated tool could declare ``required_secrets`` and still run outside any
grant. ``run_tool_with_secrets`` is that caller: the panel's tool test (and anything else that runs
a generated tool) now goes through the broker, which gate-checks every declared handle before the
single run, keeps the material in the child env only, scrubs the output and records the use.

Covered here: valid grant · missing grant · revoked grant · a grant scoped to another tool ·
rotation · expired credential · leak prevention (output and events).

Not covered, because the model has no such dimension: a *project* scope on a credential. Grants are
handle+tool based (``treasury.TreasuryState``); tool manifests carry the project scope. A credential
that belongs to another project therefore cannot be refused on that ground today — reported as a
gap, not silently assumed away.
"""

from __future__ import annotations

import time

import pytest

from skaldir.events import EventStore
from skaldir.execution import sandbox_available
from skaldir.treasury import TreasuryState, Vault, run_tool_with_secrets, secret_env_var

MASTER = "test-master-key-9f2c1a"
SECRET = "test-only-not-a-key-1234567890"
HANDLE = "secret://openrouter/main"
ENV_VAR = "SKALDIR_SECRET_OPENROUTER_MAIN"

pytestmark = pytest.mark.skipif(
    not sandbox_available(), reason="no sandbox (bwrap) — the broker refuses injection by design"
)


@pytest.fixture
def vault(tmp_path):
    return Vault(tmp_path / "vault.json", MASTER)


@pytest.fixture
def state(tmp_path):
    store = TreasuryState(tmp_path / "treasury.db")
    yield store
    store.close()


@pytest.fixture
def events(tmp_path):
    store = EventStore(tmp_path / "events.db")
    yield store
    store.close()


def _handle(vault: Vault, *, secret: str = SECRET, expires_at: float | None = None) -> str:
    return vault.add(
        service="openrouter",
        name="main",
        secret=secret,
        scopes=("use", "grant:required"),
        expires_at=expires_at,
    )


def _grant_for(state: TreasuryState, tool_id: str | None, *, ttl: int = 3600) -> str:
    grant_id = state.add_grant_request(HANDLE, "tool call", ttl, tool_id=tool_id)
    assert state.approve_grant(grant_id, by="owner")
    return grant_id


def _run(tmp_path, argv, *, tool_id="webp_resize", events=None, **kwargs):
    return run_tool_with_secrets(
        argv=argv,
        runtime=tmp_path,
        handles=[HANDLE],
        tool_id=tool_id,
        purpose="wave6: tool call needs a connection",
        workdir=tmp_path,
        master_key=MASTER,
        events=events,
        **kwargs,
    )


def test_the_env_var_convention_is_derived_from_the_handle():
    assert secret_env_var(HANDLE) == ENV_VAR
    assert secret_env_var("secret://github/bertus-37987") == "SKALDIR_SECRET_GITHUB_BERTUS_37987"


def test_a_valid_grant_reaches_the_tool(tmp_path, vault, state):
    _handle(vault)
    _grant_for(state, "webp_resize")
    outcome = _run(tmp_path, ["/bin/sh", "-c", f'test -n "${ENV_VAR}" && echo injection-ok'])
    assert outcome["denied"] is False and outcome["ok"] is True, outcome
    assert "injection-ok" in outcome["output"]
    assert outcome["env"] == {HANDLE: ENV_VAR}


def test_a_missing_grant_stops_the_call_before_it_runs(tmp_path, vault, state):
    _handle(vault)
    marker = tmp_path / "ran.marker"
    outcome = _run(tmp_path, ["/bin/touch", str(marker)])
    assert outcome["denied"] is True
    assert "no active grant" in outcome["reason"]
    assert not marker.exists(), "a tool without a grant must not run at all"


def test_a_revoked_grant_stops_the_call(tmp_path, vault, state):
    _handle(vault)
    grant_id = _grant_for(state, "webp_resize")
    assert state.revoke_grant(grant_id)
    marker = tmp_path / "ran.marker"
    outcome = _run(tmp_path, ["/bin/touch", str(marker)])
    assert outcome["denied"] is True and not marker.exists()


def test_a_grant_scoped_to_another_tool_does_not_authorise(tmp_path, vault, state):
    """The closest existing equivalent of "wrong project": a grant that names another tool."""
    _handle(vault)
    _grant_for(state, "other_tool")
    marker = tmp_path / "ran.marker"
    denied = _run(tmp_path, ["/bin/touch", str(marker)], tool_id="webp_resize")
    assert denied["denied"] is True and not marker.exists()
    # the same grant does authorise the tool it names
    allowed = _run(tmp_path, ["/bin/echo", "ok"], tool_id="other_tool")
    assert allowed["denied"] is False and allowed["ok"] is True


def test_rotation_swaps_the_material_without_a_rebuild(tmp_path, vault, state):
    _handle(vault, secret="short-secret")
    _grant_for(state, "webp_resize")
    first = _run(tmp_path, ["/bin/sh", "-c", f'echo -n "${{{ENV_VAR}}}" | wc -c'])
    # rotating the credential rewrites the same handle (the tool keeps its grant)
    _handle(vault, secret="a-considerably-longer-rotated-secret")
    second = _run(tmp_path, ["/bin/sh", "-c", f'echo -n "${{{ENV_VAR}}}" | wc -c'])
    assert first["ok"] and second["ok"], (first, second)
    assert first["output"].strip() != second["output"].strip(), (
        "the rotated material must be in use"
    )


def test_an_expired_credential_is_refused(tmp_path, vault, state):
    _handle(vault, expires_at=time.time() - 60)
    _grant_for(state, "webp_resize")
    marker = tmp_path / "ran.marker"
    outcome = _run(tmp_path, ["/bin/touch", str(marker)])
    assert outcome["denied"] is True
    assert "expired" in outcome["reason"].lower()
    assert not marker.exists()


def test_the_material_never_reaches_output_or_events(tmp_path, vault, state, events):
    _handle(vault)
    _grant_for(state, "webp_resize")
    outcome = _run(tmp_path, ["/bin/sh", "-c", f'echo "leak: ${{{ENV_VAR}}}"'], events=events)
    assert SECRET not in outcome["output"], "output must be scrubbed"
    assert "[redacted]" in outcome["output"] or "leak: " in outcome["output"]
    recorded = list(events.iter_session("treasury"))
    blob = repr(recorded)
    assert SECRET not in blob, "no raw material in the event log"
    assert [row.kind for row in recorded] == ["treasury_use"]
    assert SECRET not in repr(recorded[0].payload)
