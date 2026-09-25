"""Treasury: vault encryption, broker injection/scrubbing, grants, TOTP, staged money."""

import json

import pytest

from skaldir.events import EventStore
from skaldir.treasury import (
    PRESETS,
    REDACTED,
    TIER_WHITELIST,
    Broker,
    SpendIntent,
    SpendLedger,
    SpendRules,
    TreasuryState,
    Vault,
    VaultError,
    draft_spend_intent,
    totp_now,
)

MASTER = "test-master-key-123"
SECRET = "tok_" + "A1b2C3d4E5f6G7h8I9j0K1l2Q7w8"

RFC_6238_SEED = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # "12345678901234567890"


@pytest.fixture
def vault(tmp_path):
    return Vault(tmp_path / "vault.json", MASTER)


@pytest.fixture
def state(tmp_path):
    handle = TreasuryState(tmp_path / "treasury.db")
    yield handle
    handle.close()


class TestVault:
    def test_roundtrip_and_metadata_only(self, vault):
        handle = vault.add(
            service="openrouter", name="main", secret=SECRET, scopes=["use"], description="Test-Key"
        )
        assert handle == "secret://openrouter/main"
        info = vault.describe(handle)
        assert info is not None and info.service == "openrouter"
        dumped = json.dumps([item.to_dict() for item in vault.list_handles()])
        assert SECRET not in dumped

    def test_secret_not_plaintext_on_disk(self, vault, tmp_path):
        vault.add(service="s", name="n", secret=SECRET)
        raw = (tmp_path / "vault.json").read_text(encoding="utf-8")
        assert SECRET not in raw
        assert '"ciphertext"' in raw

    def test_wrong_key_fails_decrypt(self, tmp_path):
        Vault(tmp_path / "vault.json", MASTER).add(service="s", name="n", secret=SECRET)
        other = Vault(tmp_path / "vault.json", "other-key")
        with pytest.raises(VaultError):
            other._decrypt("secret://s/n")

    def test_revoke(self, vault):
        handle = vault.add(service="s", name="n", secret=SECRET)
        assert vault.revoke(handle) is True
        assert vault.describe(handle) is None
        assert vault.revoke(handle) is False

    def test_expired_handle_refused(self, vault):
        handle = vault.add(service="s", name="n", secret=SECRET, expires_at=1.0)
        with pytest.raises(VaultError):
            vault._decrypt(handle)


class TestTotp:
    def test_rfc6238_vectors(self):
        assert totp_now(RFC_6238_SEED, digits=8, at_time=59) == "94287082"
        assert totp_now(RFC_6238_SEED, digits=8, at_time=1111111109) == "07081804"
        assert totp_now(RFC_6238_SEED, digits=8, at_time=1234567890) == "89005924"

    def test_broker_mints_code_not_seed(self, vault, state):
        handle = vault.add(
            service="mail", name="totp", secret="", scopes=["totp"], totp_seed=RFC_6238_SEED
        )
        code = Broker(vault, state).totp_code(handle, purpose="login", at_time=59, digits=8)
        assert code == "94287082"


class TestBroker:
    def _broker(self, vault, state, tmp_path):
        events = EventStore(tmp_path / "events.db")
        return Broker(vault, state, events=events), events

    def test_injection_scrubs_the_secret(self, vault, state, tmp_path):
        handle = vault.add(service="s", name="n", secret=SECRET)
        broker, events = self._broker(vault, state, tmp_path)
        script = (
            "import os\n"
            "token = os.environ.get('TOK', '')\n"
            "print('laenge', len(token))\n"
            "print('raw', token)\n"
        )
        result = broker.run_with_secret(
            handle,
            argv=["python3", "-c", script],
            env_var="TOK",
            purpose="test",
            workdir=tmp_path,
        )
        assert result.ok
        assert SECRET not in result.stdout
        assert REDACTED in result.stdout
        assert f"laenge {len(SECRET)}" in result.stdout
        kinds = [event.kind for event in events.iter_session("treasury")]
        assert "treasury_use" in kinds

    def test_unknown_handle_denied(self, vault, state, tmp_path):
        broker, events = self._broker(vault, state, tmp_path)
        result = broker.run_with_secret(
            "secret://ghost/x",
            argv=["/usr/bin/echo", "x"],
            env_var="T",
            purpose="p",
            workdir=tmp_path,
        )
        assert result.denied and not result.ok
        assert "treasury_denied" in [event.kind for event in events.iter_session("treasury")]

    def test_scope_enforced(self, vault, state, tmp_path):
        handle = vault.add(service="s", name="n", secret=SECRET, scopes=["read"])
        broker, _events = self._broker(vault, state, tmp_path)
        result = broker.run_with_secret(
            handle,
            argv=["/usr/bin/echo", "x"],
            env_var="T",
            purpose="p",
            require_scopes=("write",),
            workdir=tmp_path,
        )
        assert result.denied

    def test_rate_limit(self, vault, state, tmp_path):
        handle = vault.add(service="s", name="n", secret=SECRET)
        broker, _events = self._broker(vault, state, tmp_path)
        for _ in range(3):
            assert broker.run_with_secret(
                handle,
                argv=["/usr/bin/echo", "ok"],
                env_var="T",
                purpose="p",
                hourly_limit=3,
                workdir=tmp_path,
            ).ok
        denied = broker.run_with_secret(
            handle,
            argv=["/usr/bin/echo", "ok"],
            env_var="T",
            purpose="p",
            hourly_limit=3,
            workdir=tmp_path,
        )
        assert denied.denied

    def test_grant_flow(self, vault, state, tmp_path):
        handle = vault.add(
            service="bank", name="read", secret=SECRET, scopes=["use", "grant:required"]
        )
        broker, events = self._broker(vault, state, tmp_path)
        denied = broker.run_with_secret(
            handle, argv=["/usr/bin/echo", "x"], env_var="T", purpose="p", workdir=tmp_path
        )
        assert denied.denied and "grant" in denied.detail

        grant = broker.request_grant(handle, purpose="Saldo prüfen", ttl_seconds=600)
        assert broker.approve_grant(grant, by="willy") is True
        allowed = broker.run_with_secret(
            handle, argv=["/usr/bin/echo", "grant-ok"], env_var="T", purpose="p", workdir=tmp_path
        )
        assert allowed.ok

        denied_grant = broker.request_grant(handle, purpose="nochmal", ttl_seconds=600)
        assert broker.deny_grant(denied_grant, by="willy") is True
        kinds = [event.kind for event in events.iter_session("treasury")]
        assert "treasury_grant_requested" in kinds
        assert "treasury_grant_approved" in kinds
        assert "treasury_grant_denied" in kinds


class TestSpending:
    def _ledger(self, state, tmp_path, rules=None):
        events = EventStore(tmp_path / "events.db")
        return SpendLedger(state, rules or PRESETS["stufe0"], events=events), events

    def test_stufe0_needs_human_every_time(self, state, tmp_path):
        ledger, events = self._ledger(state, tmp_path)
        request = ledger.request(amount=4.99, merchant="hosting-x", purpose="GPU-Stunde")
        assert request.status == "pending_human"
        assert ledger.complete(request.request_id) is False
        assert ledger.approve(request.request_id, by="willy") is True
        assert ledger.complete(request.request_id) is True
        kinds = [event.kind for event in events.iter_session("treasury")]
        assert kinds == ["spend_requested", "spend_approved", "spend_completed"]

    def test_stufe1_whitelist_and_caps(self, state, tmp_path):
        rules = SpendRules(
            tier=TIER_WHITELIST, per_tx_cap=5.0, weekly_cap=20.0, whitelist=["hosting-x"]
        )
        ledger, _events = self._ledger(state, tmp_path, rules)
        intent = SpendIntent(amount=4.0, currency="EUR", merchant="hosting-x", purpose="x")
        auto = ledger.request(amount=4.0, merchant="hosting-x", purpose="x", intent=intent)
        assert auto.status == "approved" and auto.approved_by == "policy"
        no_intent = ledger.request(amount=3.0, merchant="hosting-x", purpose="y")
        assert no_intent.status == "pending_human" and "intent" in no_intent.reason
        manual = ledger.request(amount=4.0, merchant="other", purpose="y")
        assert manual.status == "pending_human" and "whitelist" in manual.reason
        too_big = ledger.request(amount=9.0, merchant="hosting-x", purpose="z")
        assert too_big.status == "denied" and "per-tx cap" in too_big.reason

    def test_weekly_budget_enforced(self, state, tmp_path):
        rules = SpendRules(
            tier=TIER_WHITELIST, per_tx_cap=5.0, weekly_cap=6.0, whitelist=["m"]
        )
        ledger, _events = self._ledger(state, tmp_path, rules)
        first = SpendIntent(amount=4.0, currency="EUR", merchant="m", purpose="a")
        assert (
            ledger.request(amount=4.0, merchant="m", purpose="a", intent=first).status
            == "approved"
        )
        second_intent = SpendIntent(amount=4.0, currency="EUR", merchant="m", purpose="b")
        second = ledger.request(amount=4.0, merchant="m", purpose="b", intent=second_intent)
        assert second.status == "denied" and "weekly" in second.reason

    def test_intent_must_match_request(self, state, tmp_path):
        ledger, _events = self._ledger(state, tmp_path)
        intent = SpendIntent(amount=4.99, currency="EUR", merchant="hosting-x", purpose="x")
        mismatch = ledger.request(
            amount=9.99, merchant="hosting-x", purpose="x", intent=intent
        )
        assert mismatch.status == "denied" and "intent" in mismatch.reason
        matching = ledger.request(
            amount=4.99, currency="EUR", merchant="hosting-x", purpose="x", intent=intent
        )
        assert matching.status == "pending_human"
        assert matching.intent_id == intent.intent_id

    def test_expired_intent_blocks_complete(self, state, tmp_path):
        ledger, _events = self._ledger(state, tmp_path)
        intent = SpendIntent(amount=1.0, currency="EUR", merchant="m", purpose="p", expires_at=1.0)
        request = ledger.request(amount=1.0, merchant="m", purpose="p", intent=intent)
        assert ledger.approve(request.request_id, by="willy") is True
        assert ledger.complete(request.request_id) is False
        final = state.get_spend(request.request_id)
        assert final is not None and final.status == "denied"


class TestDraftIntent:
    def test_draft_parsed(self):
        from skaldir.llm import ScriptedLLM
        from skaldir.prompts import PromptRegistry

        payload = json.dumps(
            {
                "amount": 4.99,
                "currency": "eur",
                "merchant": "hosting-x",
                "purpose": "GPU-Stunde",
                "note": "kleinste Option",
            }
        )
        client = ScriptedLLM(responses={"spend_intent": payload})
        intent = draft_spend_intent(
            client, PromptRegistry(), goal="GPU fürs Training", whitelist=["hosting-x"]
        )
        assert intent is not None
        assert intent.amount == 4.99 and intent.currency == "EUR"

    def test_zero_amount_rejected(self):
        from skaldir.llm import ScriptedLLM
        from skaldir.prompts import PromptRegistry

        client = ScriptedLLM(responses={"spend_intent": json.dumps({"amount": 0, "merchant": "x"})})
        assert draft_spend_intent(client, PromptRegistry(), goal="x", whitelist=[]) is None


class TestCli:
    def test_vault_list_cli_hides_secrets(self, tmp_path, monkeypatch, capsys):
        from skaldir.cli import main

        vault_path = tmp_path / "vault.json"
        Vault(vault_path, MASTER).add(service="s", name="n", secret=SECRET, description="Test")
        monkeypatch.setenv("GODSV_VAULT_KEY", MASTER)
        assert main(["vault", "list", "--vault", str(vault_path)]) == 0
        out = capsys.readouterr().out
        assert "secret://s/n" in out
        assert SECRET not in out

    def test_vault_cli_requires_key(self, tmp_path, monkeypatch, capsys):
        from skaldir.cli import main

        monkeypatch.delenv("SKALDIR_VAULT_KEY", raising=False)
        monkeypatch.delenv("GODSV_VAULT_KEY", raising=False)
        assert main(["vault", "list", "--vault", str(tmp_path / "v.json")]) == 1
        # the primary spelling is named; the pre-rename twin is still read
        assert "SKALDIR_VAULT_KEY" in capsys.readouterr().out

    def test_spend_cli_flow(self, tmp_path, capsys):
        from skaldir.cli import main

        db = tmp_path / "treasury.db"
        code = main(
            [
                "spend", "request", "4.99",
                "--merchant", "hosting-x", "--purpose", "GPU", "--db", str(db),
            ]
        )
        assert code == 0
        out = capsys.readouterr().out
        assert "pending_human" in out
        request_id = out.split()[0]

        assert main(["spend", "list", "--db", str(db)]) == 0
        assert "pending_human" in capsys.readouterr().out

        assert main(["spend", "approve", request_id, "--db", str(db)]) == 0
        assert "approved" in capsys.readouterr().out

        assert main(["spend", "complete", request_id, "--db", str(db)]) == 0
        assert "completed" in capsys.readouterr().out
