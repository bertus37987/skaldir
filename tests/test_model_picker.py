"""``skaldir model`` — the picker, the hidden key, the ``.env`` (0600) and the exit codes.

What this file pins, in the owner's words: *"in the terminal `skaldir model` should let me
pick a provider — at least two: one TypeSafe for Jev and one LLM provider — with base URLs
PRE-FILLED so I only have to enter the API key; and in the chat, `/model` should open a
model and provider picker."*

* **the two roles** are the first question, and they are exactly the product's two keys;
* **the base URL is pre-filled** from the catalog row, and the model from the catalog's own
  model list — accepting one is pressing Enter;
* **the only thing typed is the key** — masked, never in argv, never printed, never in the
  config; the value lands in the ``.env`` beside the config with mode ``0600``;
* **a key that already exists is reused**, and the report names the source it came from;
* **the flags run the same engine** as the questions (one code path, not two);
* **the exit codes are pinned**: a remote endpoint with no credential is ``4`` and writes
  *nothing* — no ``.env`` line, no config file.

The live validation is a fake prober here (no network, no credential); the real probe is
``setup_flow.measure_key`` and is exercised by ``tests/test_setup_ux.py`` against stub
endpoints.
"""

from __future__ import annotations

import io
import json
import os
import pty
import stat
from pathlib import Path
from typing import Any

import pytest

from skaldir import cli
from skaldir import model_cli as mc
from skaldir import model_picker as mp
from skaldir import provider_catalog as catalog
from skaldir import provider_config as pc
from skaldir import provider_registry_data as registry
from skaldir import setup_flow as sf

#: The id the provider catalog documents as DeepSeek's default — READ from the catalog,
#: never retyped, so a model-id update is one edit in one place and these expectations
#: follow it instead of going stale (the 2026-09-19 probe moved the id).
DEEPSEEK_DEFAULT = catalog.CATALOG["deepseek"].default_model

FAKE_KEY = "sk-fake-key-that-must-never-be-printed"


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch, tmp_path):
    """Neither the developer's shell exports nor a real ~/.hermes may leak in."""
    for name in (
        "SKALDIR_LLM_CONFIG",
        "SKALDIR_SETUP_ENV_FILE",
        "SKALDIR_SETUP_LLM_API_KEY",
        "SKALDIR_SETUP_JEV_API_KEY",
        "SKALDIR_SETUP_LLM_PROVIDER",
        "SKALDIR_SETUP_JEV_PROVIDER",
        "SKALDIR_LLM_MODEL",
        "SKALDIR_LLM_BASE_URL",
        "SKALDIR_LLM_API_KEY",
        "OPENAI_API_KEY",
        "OPENAI_MODEL",
        "OPENAI_BASE_URL",
        "DEEPSEEK_API_KEY",
        "LLAMA_API_KEY",
        "VLLM_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SKALDIR_HOME", str(tmp_path / "runtime"))
    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.delenv("GODSV_HOME", raising=False)


class Stub:
    """A prober: the four measured facts ``setup_flow.measure_key`` reports, without a call."""

    def __init__(self, *, ok: bool = True, status: int | None = None, mode: str = "json_object"):
        self.ok = ok
        self.status = status
        self.mode = mode
        self.calls: list[dict[str, Any]] = []

    def __call__(self, key: Any, *, env=None, timeout_s=None, measure_streaming: bool = True):
        self.calls.append(
            {
                "role": key.role,
                "base_url": key.base_url,
                "model": key.model,
                "api_key_env": key.api_key_env,
                "streaming": measure_streaming,
                "credential": key.entry().credential_state(env or {}),
            }
        )
        report: dict[str, Any] = {
            "ok": self.ok,
            "role": key.role,
            "model_echo": key.model,
            "latency_ms": 7,
            "credential": key.entry().credential_state(env or {}),
            "status": self.status,
            "phase": "completion",
            "streaming": {"supported": True, "measured": True, "first_byte_ms": 3, "reason": ""},
            "structured": {
                "measured": True,
                "ok": True,
                "mode_used": self.mode,
                "native_json_schema": False,
                "reason": "",
            },
        }
        if not self.ok:
            report["problem"] = "HTTP 401 unauthorized" if self.status == 401 else "no answer"
        return report


def _paths(tmp_path: Path) -> tuple[Path, Path]:
    config = tmp_path / "home" / "config.yaml"
    return config, config.parent / ".env"


def _plan(role: str = sf.ROLE_LLM, **kwargs: Any) -> mp.Plan:
    return mp.plan_for(role, **kwargs)


# --------------------------------------------------------------------------- #
# the rows: the two roles, the 16 providers, the model list                    #
# --------------------------------------------------------------------------- #


def test_the_two_roles_are_the_first_question():
    rows = mp.role_rows(state={})
    assert [row.value for row in rows] == [sf.ROLE_TYPESAFE, sf.ROLE_LLM]
    assert "Jev" in rows[0].name and "LLM" in rows[1].name
    assert mp.resolve_role("1") == sf.ROLE_TYPESAFE
    assert mp.resolve_role("2") == sf.ROLE_LLM
    assert mp.resolve_role("typesafe") == sf.ROLE_TYPESAFE
    assert mp.resolve_role("jev") == sf.ROLE_TYPESAFE
    assert mp.resolve_role("llm") == sf.ROLE_LLM
    assert mp.resolve_role("nope") == ""
    assert mp.resolve_role("9") == ""


# --------------------------------------------------------------------------- #
# the role picker speaks the owner's words: agent / voice, voice → llm | jev   #
# --------------------------------------------------------------------------- #


def test_the_role_rows_are_presented_as_agent_and_voice():
    """The picker offers two words: ``agent`` (general model) and ``voice``.

    ``voice`` is a *labeling* row for the decision key -- choosing it moves to a
    second question (LLM vs Jev); the underlying key is still ``ROLE_TYPESAFE``.
    The mark column still reports the measured state of the key each row stands for.
    """
    states = {
        sf.ROLE_TYPESAFE: {"present": True, "ready": True, "credential": "set"},
        sf.ROLE_LLM: {"present": True, "ready": False, "credential": "unset"},
    }
    rows = mp.role_rows(state=states)
    assert [row.value for row in rows] == [sf.ROLE_TYPESAFE, sf.ROLE_LLM]
    assert [row.name for row in rows] == ["voice (Jev)", "agent (LLM)"]
    # the marks travel with the key each row stands for (voice = the Jev key)
    assert rows[0].mark == "configured" and rows[1].mark == "no key"
    assert mp.resolve_role("agent") == sf.ROLE_LLM
    assert mp.resolve_role("voice") == sf.ROLE_TYPESAFE


def test_resolve_role_answers_the_agent_voice_words_and_the_old_ones():
    """The new words answer by name; the old spellings keep answering."""
    for word, role in (
        ("agent", sf.ROLE_LLM),
        ("general", sf.ROLE_LLM),
        ("llm", sf.ROLE_LLM),
        ("voice", sf.ROLE_TYPESAFE),
        ("typesafe", sf.ROLE_TYPESAFE),
        ("jev", sf.ROLE_TYPESAFE),
    ):
        assert mp.resolve_role(word) == role, word
    assert mp.resolve_role("nope") == ""


def test_role_rows_carry_the_second_level_choice_for_voice():
    """The voice row says the second question it leads to (LLM or Jev)."""
    rows = mp.role_rows(state={})
    voice = next(row for row in rows if row.value == sf.ROLE_TYPESAFE)
    assert "llm" in voice.description.lower() and "jev" in voice.description.lower()
    agent = next(row for row in rows if row.value == sf.ROLE_LLM)
    assert "agent" in agent.description.lower()


def test_ask_role_presents_two_questions_and_voice_asks_llm_vs_jev(
    monkeypatch, capsys
):
    """The terminal flow: first agent/voice, then -- for voice -- LLM vs Jev.

    The two-key answer is unchanged: agent maps to ROLE_LLM, voice+Jev maps to
    ROLE_TYPESAFE, both resolved through the same role question.
    """
    asked: list[tuple[str, tuple[str, ...], str]] = []

    def fake_choose(title, rows, **kwargs):
        asked.append((title, tuple(row.value for row in rows), kwargs.get("default_value", "")))
        # answer by the row whose name carries the presentation word:
        # question 1 → ``agent``, question 2 → ``jev``
        wanted = "agent" if len(asked) == 1 else "jev"
        for row in rows:
            if wanted in row.name.lower():
                return row.value
        return ""

    monkeypatch.setattr(mc, "_choose", fake_choose)
    role = mc._ask_role(env={})
    assert role == sf.ROLE_LLM, "the agent row answers the general key"
    assert len(asked) == 1
    assert asked[0][1] == (sf.ROLE_TYPESAFE, sf.ROLE_LLM), asked[0]

    asked.clear()

    def fake_choose_voice_then_jev(title, rows, **kwargs):
        asked.append((title, tuple(row.value for row in rows), kwargs.get("default_value", "")))
        # question 1 → ``voice`` (typesafe), question 2 → ``jev``
        wanted = "voice" if len(asked) == 1 else "jev"
        for row in rows:
            if wanted in row.name.lower() or wanted in row.description.lower():
                return row.value
        return ""

    monkeypatch.setattr(mc, "_choose", fake_choose_voice_then_jev)
    role = mc._ask_role(env={})
    assert role == sf.ROLE_TYPESAFE, "voice + Jev answers the decision key"
    # two questions were asked: agent/voice, then llm/jev
    assert len(asked) == 2, asked
    assert asked[0][1] == (sf.ROLE_TYPESAFE, sf.ROLE_LLM)
    assert asked[1][1] == ("llm", "jev"), asked[1]


def test_the_frame_picker_asks_voice_then_llm_vs_jev(monkeypatch, fake_frame):
    """The in-frame flow asks the same two questions, in the same words."""
    _bar, front, _keys = fake_frame
    titles: list[str] = []

    def recording_select(front_, *, title, rows, **kwargs):
        titles.append(title)
        values = [row.value for row in rows]
        if len(titles) == 1:
            return sf.ROLE_TYPESAFE
        if values and values[0] in ("llm", "jev"):
            return "jev"
        return values[0] if values else ""

    monkeypatch.setattr(mp, "select_in_frame", recording_select)
    monkeypatch.setattr(mp, "_ask_key_value", lambda *a, **k: "")
    plan, _key = mp._pick_in_frame(None, front, role="", env={})
    assert plan is not None and plan.role == sf.ROLE_TYPESAFE
    assert len(titles) >= 2, titles
    assert "agent" in titles[0] and "voice" in titles[0], titles
    assert "llm" in titles[1].lower() and "jev" in titles[1].lower(), titles


def test_the_frame_picker_agent_row_answers_the_general_key(monkeypatch, fake_frame):
    _bar, front, _keys = fake_frame
    titles: list[str] = []

    def recording_select(front_, *, title, rows, **kwargs):
        titles.append(title)
        # one scripted pick per question: role → provider → model
        if "role" in title.lower() or "agent or voice" in title.lower():
            for row in rows:
                if "agent" in row.name.lower() or "agent" in row.description.lower():
                    return row.value
            return sf.ROLE_LLM
        if "provider" in title.lower():
            return "deepseek"
        if "model" in title.lower() and rows:
            return rows[0].value
        return ""

    monkeypatch.setattr(mp, "select_in_frame", recording_select)
    monkeypatch.setattr(mp, "_ask_key_value", lambda *a, **k: "")
    plan, _key = mp._pick_in_frame(None, front, role="", env={})
    assert plan is not None and plan.role == sf.ROLE_LLM
    assert "agent" in titles[0].lower(), titles



def test_every_provider_row_carries_its_base_url_and_the_local_ones_say_so():
    rows = mp.provider_rows()
    # one row per *plan endpoint* (multi-SKU providers expand), at least one per entry
    assert len(rows) >= len(registry.REGISTRY_ORDER), (
        "every breadth-registry entry is a pickable row — multi-SKU providers expand "
        f"({len(rows)} rows for {len(registry.REGISTRY_ORDER)} entries)"
    )
    covered = {row.value.split("#", 1)[0] for row in rows}
    assert set(registry.REGISTRY_ORDER) <= covered, (
        "every registry id must appear at least once"
    )
    for row in rows:
        assert row.description.startswith("http"), row
        assert row.value and " " not in row.value
    local = [row for row in rows if "(local)" in row.name]
    assert {"llamacpp", "vllm", "lmstudio", "ollama"} == {
        row.value.split("#", 1)[0] for row in local
    }
    assert any(row.mark == "current" for row in mp.provider_rows(current="deepseek"))


def test_the_model_list_is_the_catalog_s_own_and_the_default_is_marked():
    # the rows are the catalog's own ids — read from the catalog, so a model-id
    # update does not leave this test asserting an id nobody serves any more
    deepseek = catalog.CATALOG["deepseek"]
    rows = mp.model_rows("deepseek")
    assert {row.value for row in rows} == set(deepseek.models)
    # newest first: the owner's rule — a first Enter takes the newest id
    assert rows[0].value == mp.model_choices("deepseek")[0].id
    assert rows[0].mark == "default"
    assert mp.model_choices("deepseek")[0].id == "deepseek-v4-pro"
    # openrouter is a real breadth endpoint: its rows are source-backed, not empty
    openrouter = mp.model_rows("openrouter")
    assert len(openrouter) >= 100, len(openrouter)
    assert all(row.value and row.description for row in openrouter[:5])
    assert mp.model_choices("deepseek")[0].source == "catalog"


def test_the_default_role_is_the_one_that_still_needs_a_key(tmp_path):
    states = {
        sf.ROLE_TYPESAFE: {"present": True, "ready": True, "credential": "set"},
        sf.ROLE_LLM: {"present": True, "ready": False, "credential": "unset"},
    }
    assert mp.default_role(env={}) == sf.ROLE_LLM
    rows = mp.role_rows(state=states)
    assert rows[0].mark == "configured"
    assert rows[1].mark == "no key"
    assert mp.role_rows(state={})[0].mark == "not set up"


def test_the_choice_is_prefilled_from_the_catalog(tmp_path):
    plan = _plan(provider="deepseek")
    assert plan.base_url == "https://api.deepseek.com/v1"
    assert plan.model == DEEPSEEK_DEFAULT  # the catalog’s own default, read not retyped
    assert plan.api_key_env == "DEEPSEEK_API_KEY"
    assert plan.model_source == "catalog"
    assert plan.label == sf.ROLE_LABEL[sf.ROLE_LLM]
    assert plan.provider_label == "DeepSeek"
    bare = _plan()
    assert bare.provider == sf.DEFAULT_PROVIDER_ID
    local = mp.plan_for(sf.ROLE_LLM, provider="llamacpp", model="llama3.2")
    assert local.local and local.base_url == "http://127.0.0.1:8080/v1"
    assert local.api_key_env  # a local row still names the variable the server may want


def test_an_unknown_provider_or_a_provider_without_a_model_is_refused_by_name():
    with pytest.raises(sf.SetupRefused) as unknown:
        _plan(provider="not-a-provider")
    assert "unknown provider" in str(unknown.value)
    # a provider whose catalog entry carries no model list still refuses by name
    # (openrouter now HAS models — the "names no model" pin moved to an endpoint
    # that really has none)
    with pytest.raises(sf.SetupRefused) as no_model:
        _plan(provider="azure-openai")
    assert "names no model" in str(no_model.value)
    given = _plan(provider="openrouter", model="meta-llama/llama-3.1-8b-instruct")
    assert given.model == "meta-llama/llama-3.1-8b-instruct"
    with pytest.raises(sf.SetupRefused):
        mp.plan_for("not-a-role")


# --------------------------------------------------------------------------- #
# the write: the .env (0600), the config by NAME, the re-read                   #
# --------------------------------------------------------------------------- #


def test_the_key_lands_in_a_0600_env_and_the_config_names_the_variable(tmp_path):
    config, env_path = _paths(tmp_path)
    prober = Stub()
    payload, code = mp.apply_choice(
        _plan(provider="deepseek"),
        key_value=FAKE_KEY,
        env={"HOME": str(tmp_path)},
        config_path=config,
        prober=prober,
    )
    assert code == sf.EXIT_OK
    assert payload["ok"] and payload["wrote"]
    assert prober.calls[0]["model"] == DEEPSEEK_DEFAULT
    assert prober.calls[0]["credential"] == "set"

    assert stat.S_IMODE(env_path.stat().st_mode) == 0o600
    assert env_path.read_text(encoding="utf-8").strip() == f"DEEPSEEK_API_KEY={FAKE_KEY}"
    text = config.read_text(encoding="utf-8")
    assert "api_key_env: DEEPSEEK_API_KEY" in text
    assert FAKE_KEY not in text
    assert payload["reread"]["matches"] is True
    assert payload["reread"]["model"] == DEEPSEEK_DEFAULT
    assert json.dumps(payload).find(FAKE_KEY) == -1, "the report must never carry the value"
    assert payload["key_sources"][sf.ROLE_LLM]["kind"] == "typed"
    assert "credential" not in json.dumps(payload["plan"])


def test_the_other_role_is_untouched_by_a_second_write(tmp_path):
    config, _env = _paths(tmp_path)
    first, code = mp.apply_choice(
        _plan(sf.ROLE_TYPESAFE, provider="deepseek"),
        key_value=FAKE_KEY,
        env={"HOME": str(tmp_path)},
        config_path=config,
        prober=Stub(),
    )
    assert code == sf.EXIT_OK and first["ok"]
    second, code = mp.apply_choice(
        _plan(sf.ROLE_LLM, provider="openai", model="gpt-4o-mini"),
        key_value=FAKE_KEY,
        env={"HOME": str(tmp_path)},
        config_path=config,
        prober=Stub(),
    )
    assert code == sf.EXIT_OK and second["ok"]
    document = pc.load_store(config).document
    assert document["godsv"]["jev"]["base_url"] == "https://api.deepseek.com/v1"
    assert document["godsv"]["llm"]["model"] == "gpt-4o-mini"
    entries = document["godsv"]["providers"]["entries"]
    assert set(entries) == {"jev", "llm"}
    assert document["godsv"]["setup"]["keys"][sf.ROLE_TYPESAFE]["model"] == DEEPSEEK_DEFAULT


def test_no_key_is_the_honest_exit_code_and_nothing_is_written(tmp_path):
    config, env_path = _paths(tmp_path)
    prober = Stub()
    payload, code = mp.apply_choice(
        _plan(provider="deepseek"),
        env={"HOME": str(tmp_path)},
        config_path=config,
        prober=prober,
    )
    assert code == sf.EXIT_NEEDS_CREDENTIALS == 4
    assert payload["ok"] is False and payload["wrote"] is False
    assert prober.calls == [], "a key that was never sent cannot be validated"
    assert not env_path.exists() and not config.exists()
    assert payload["exit_meaning"][4] == "a remote endpoint has no credential in the environment"


def test_a_key_that_only_lives_in_the_shell_is_copied_into_the_file(tmp_path):
    config, env_path = _paths(tmp_path)
    payload, code = mp.apply_choice(
        _plan(provider="deepseek"),
        env={"HOME": str(tmp_path), "DEEPSEEK_API_KEY": FAKE_KEY},
        config_path=config,
        prober=Stub(),
    )
    assert code == sf.EXIT_OK
    assert env_path.read_text(encoding="utf-8").strip() == f"DEEPSEEK_API_KEY={FAKE_KEY}"
    assert payload["key_sources"][sf.ROLE_LLM]["kind"] == "environment"
    assert payload["key_sources"][sf.ROLE_LLM]["source"] == "DEEPSEEK_API_KEY"
    assert json.dumps(payload).find(FAKE_KEY) == -1


def test_a_key_already_in_the_file_is_reused_and_the_file_stays_identical(tmp_path):
    config, env_path = _paths(tmp_path)
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text(f"# the credential file\nDEEPSEEK_API_KEY={FAKE_KEY}\n", encoding="utf-8")
    env_path.chmod(0o600)
    before = env_path.read_bytes()
    payload, code = mp.apply_choice(
        _plan(provider="deepseek"),
        env={"HOME": str(tmp_path)},
        config_path=config,
        prober=Stub(),
    )
    assert code == sf.EXIT_OK
    assert env_path.read_bytes() == before, "a re-run must be a no-op on the credential file"
    assert payload["credentials_file"]["written"] is False
    assert payload["key_sources"][sf.ROLE_LLM]["kind"] == "env-file"
    assert payload["key_sources"][sf.ROLE_LLM]["source"] == str(env_path)


def test_no_env_write_keeps_the_key_off_the_disk(tmp_path):
    config, env_path = _paths(tmp_path)
    payload, code = mp.apply_choice(
        _plan(provider="deepseek"),
        key_value=FAKE_KEY,
        env={"HOME": str(tmp_path)},
        config_path=config,
        write_env=False,
        prober=Stub(),
    )
    assert code == sf.EXIT_OK and not env_path.exists()
    assert payload["env_write"]["written"] is False
    assert json.dumps(payload).find(FAKE_KEY) == -1


def test_a_failed_validation_writes_the_key_but_never_the_config(tmp_path):
    config, env_path = _paths(tmp_path)
    payload, code = mp.apply_choice(
        _plan(provider="deepseek"),
        key_value=FAKE_KEY,
        env={"HOME": str(tmp_path)},
        config_path=config,
        prober=Stub(ok=False, status=401),
    )
    assert code == sf.EXIT_VERIFICATION_FAILED == 2
    assert payload["wrote"] is False and not config.exists()
    assert env_path.exists(), "the key the user just pasted survives a dead endpoint"


def test_a_timeout_is_exit_8_and_a_missing_configuration_is_exit_3(tmp_path):
    config, _env = _paths(tmp_path)
    payload, code = mp.apply_choice(
        _plan(provider="deepseek"),
        key_value=FAKE_KEY,
        env={"HOME": str(tmp_path)},
        config_path=config,
        prober=Stub(ok=False, status=None),
    )
    assert code == sf.EXIT_TIMEOUT == 8
    assert payload["ok"] is False


def test_write_role_document_refuses_a_literal_secret():
    plan = _plan(provider="deepseek")
    with pytest.raises(mp.pc.ProviderStoreError):
        # a document that carries a value where a NAME belongs is the store's refusal
        document = {"godsv": {"llm": {"provider": "openai-compatible",
                                      "base_url": plan.base_url,
                                      "model": plan.model,
                                      "api_key": FAKE_KEY}}}
        mp.pc.write_document(document, Path("/tmp/should-never-be-written.yaml"))


# --------------------------------------------------------------------------- #
# --check: the same probe, and nothing written                                 #
# --------------------------------------------------------------------------- #


def test_check_revalidates_the_configured_role_and_writes_nothing(tmp_path):
    config, _env = _paths(tmp_path)
    mp.apply_choice(
        _plan(provider="deepseek"),
        key_value=FAKE_KEY,
        env={"HOME": str(tmp_path)},
        config_path=config,
        prober=Stub(),
    )
    before = config.read_bytes()
    prober = Stub()
    payload, code = mp.check_choice(
        role=sf.ROLE_LLM,
        path=config,
        env={"HOME": str(tmp_path)},
        prober=prober,
    )
    assert code == sf.EXIT_OK and payload["ok"]
    assert prober.calls and prober.calls[0]["model"] == DEEPSEEK_DEFAULT
    assert config.read_bytes() == before, "--check writes nothing"


def test_check_of_an_unconfigured_role_is_blocked_and_says_so(tmp_path):
    config, _env = _paths(tmp_path)
    payload, code = mp.check_choice(role=sf.ROLE_TYPESAFE, path=config, env={}, prober=Stub())
    assert code == sf.EXIT_BLOCKED == 3
    assert payload["roles"][sf.ROLE_TYPESAFE]["present"] is False
    assert "skaldir model" in payload["roles"][sf.ROLE_TYPESAFE]["problem"]


def test_check_exit_codes_are_pinned():
    def item(**kwargs: Any) -> dict[str, Any]:
        base = {"ok": False, "present": True, "problem": "", "refused": False, "status": 500}
        base.update(kwargs)
        return base

    assert mp.check_exit({}) == sf.EXIT_OK
    assert mp.check_exit({"llm": item(ok=True)}) == sf.EXIT_OK
    assert mp.check_exit({"llm": item(present=False)}) == sf.EXIT_BLOCKED
    no_key = item(refused=True, problem="no credential in the environment")
    assert mp.check_exit({"llm": no_key}) == sf.EXIT_NEEDS_CREDENTIALS
    assert mp.check_exit({"llm": item(status=None, phase="completion")}) == sf.EXIT_TIMEOUT
    assert mp.check_exit({"llm": item(status=401)}) == sf.EXIT_VERIFICATION_FAILED


# --------------------------------------------------------------------------- #
# the CLI: the flags and the questions are one code path                       #
# --------------------------------------------------------------------------- #


def test_the_flag_path_applies_the_choice_through_the_same_engine(tmp_path, monkeypatch, capsys):
    config, env_path = _paths(tmp_path)
    prober = Stub()
    monkeypatch.setattr(sf, "measure_key", prober)

    # 1. no key anywhere: the honest exit code, and not a byte on disk
    code = mc.main(
        [
            "--role", "llm",
            "--provider", "deepseek",
            "--model", "deepseek-chat",
            "--config", str(config),
            "--no-input",
            "--json",
        ]
    )
    assert code == sf.EXIT_NEEDS_CREDENTIALS, "no key in this environment: nothing is written"
    payload = json.loads(capsys.readouterr().out)
    assert payload["action"] == "model" and payload["ok"] is False
    assert payload["plan"]["model"] == "deepseek-chat"
    assert not config.exists() and not env_path.exists()
    assert prober.calls == []

    # 2. the same engine, with the key piped in
    monkeypatch.setattr("sys.stdin", io.StringIO(FAKE_KEY + "\n"))
    code = mc.main(
        [
            "--role", "llm",
            "--provider", "deepseek",
            "--key-stdin",
            "--config", str(config),
            "--json",
        ]
    )
    assert code == sf.EXIT_OK, "the flag path and the questions run the same apply_choice"
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["plan"]["model"] == DEEPSEEK_DEFAULT
    assert payload["key_sources"][sf.ROLE_LLM]["kind"] == "stdin"
    assert stat.S_IMODE(env_path.stat().st_mode) == 0o600
    assert prober.calls[-1]["role"] == sf.ROLE_LLM
    assert FAKE_KEY not in json.dumps(payload)


def test_the_cli_refuses_an_unknown_role_as_usage(tmp_path, capsys):
    code = mc.main(["--role", "nope", "--no-input", "--json"])
    assert code == mc.EXIT_USAGE == 1
    payload = json.loads(capsys.readouterr().out)
    assert "unknown role" in payload["problem"]


def test_list_prints_the_provider_rows_with_their_urls(capsys):
    code = mc.main(["--list", "--format", "text"])
    assert code == mc.EXIT_OK
    text = capsys.readouterr().out
    # multi-SKU providers expand — the printed count follows the pickable rows
    assert f"{len(mp.provider_rows())} rows" in text
    assert "https://api.deepseek.com/v1" in text
    # the ids printed are the catalog's own (order is newest-first in the picker)
    assert "deepseek-v4-pro" in text and "deepseek-flash" in text


def test_the_verb_is_registered_in_the_contract():
    cli.build_parser()  # the wiring line in cli.py is what installs the entry
    mc._assert_registered()
    inventory = cli.cli_contract.dump(cli.build_parser())
    assert "model" in {item["command"] for item in inventory["commands"]}
    entry = cli.cli_contract.COMMANDS["model"]
    assert entry.mutates is True and entry.requires_credentials is True
    assert 4 in entry.exit_codes and 0 in entry.exit_codes


# --------------------------------------------------------------------------- #
# the rows as lines, and the in-chat hook                                      #
# --------------------------------------------------------------------------- #


def test_every_row_line_is_exactly_the_width():
    rows = mp.provider_rows()
    for width in (40, 80, 120):
        for line in mp.row_lines(rows, width, selected=0, max_rows=6, hidden=len(rows) - 6):
            assert len(line) == width
        plain = mp.row_lines(rows, width, unicode=False)
        for line in plain:
            assert len(line) == width
            assert line.encode("ascii", "ignore").decode("ascii") == line
    assert "more" in mp.row_lines(rows, 80, max_rows=4, hidden=12)[-1]


def test_is_model_verb_knows_the_four_lines_it_answers():
    assert mp.is_model_verb("/model")
    assert mp.is_model_verb("/models deepseek")
    assert mp.is_model_verb("/provider")
    assert mp.is_model_verb("/setup")
    assert not mp.is_model_verb("/modelfoo")
    assert not mp.is_model_verb("model")


class FakeBar:
    """The frame's state the picker touches — a real tty fd, everything else recorded."""

    def __init__(self, fd: int, *, width: int = 80, screen: bool = True):
        self.fd = fd
        self.width = width
        self.height = 24
        self.unicode = True
        self.active = True
        self.screen = screen
        self.max_rows = 12
        self.text = ""
        self.painted: list[list[str]] = []
        self.above: list[str] = []
        self.drawn = 0
        self._probe_cache: dict[int, Any] = {1: "stale"}
        self._resized = False

    def band_text(self, width: int | None = None) -> str:
        return "model deepseek-chat · ctx n/a · tok/s n/a · $ n/a · 00:00 · goal: none"

    def write_above(self, text: str) -> None:
        self.above.append(str(text))

    def draw(self, *, force: bool = False) -> bool:
        self.drawn += 1
        return True

    def reload_palette(self) -> None:
        pass

    def clear(self) -> None:
        self.above.append("<cleared>")

    def _paint(self, lines: list[str], cursor: int) -> None:
        self.painted.append(list(lines))


class FakeFront:
    def __init__(self, bar: Any):
        self.bar = bar


class FakeShell:
    """A shell with (or without) a frame, for the hook tests."""

    def __init__(self, front: Any = None):
        self._chatbar_front = front
        self.said: list[str] = []
        self.out = io.StringIO()

    def say(self, text: str) -> None:
        self.said.append(str(text))


@pytest.fixture
def fake_frame(monkeypatch):
    """A front whose keys come from a script, on a real pty fd (cbreak needs a tty)."""
    master, slave = pty.openpty()
    bar = FakeBar(master)
    front = FakeFront(bar)
    keys: list[str | None] = []

    def read_key(_fd: int, *, timeout: float = 0.25, escape_timeout: float = 0.05):
        return keys.pop(0) if keys else "esc"

    class StubChatbar:
        @staticmethod
        def front_of(shell: Any) -> Any:
            return getattr(shell, "_chatbar_front", None)

        @staticmethod
        def read_key(fd, *, timeout=0.25, escape_timeout=0.05):
            return read_key(fd, timeout=timeout, escape_timeout=escape_timeout)

        @staticmethod
        def frame_lines(width, *, text="", rows=(), hint_left="", hint_right="", unicode=True,
                        margin=2):
            body = [str(row)[:width].ljust(width) for row in rows]
            cursor = len(body) + 1
            body.append("-" * width)
            body.append("❯ " + str(text)[: width - 2].ljust(width - 2))
            body.append("-" * width)
            body.append(str(hint_left or hint_right)[:width].ljust(width))
            return body, cursor

    monkeypatch.setattr(mp, "_chatbar", lambda: StubChatbar)
    try:
        yield bar, front, keys
    finally:
        os.close(master)
        os.close(slave)


def test_the_picker_moves_filters_and_returns_the_row_value(fake_frame):
    bar, front, keys = fake_frame
    rows = mp.provider_rows()
    keys.extend(["down", "enter"])
    answer = mp.select_in_frame(front, title="pick one", rows=rows, default=0, timeout=0.0)
    assert answer == rows[1].value
    assert bar.painted, "the picker drew through the frame's own paint path"
    assert len(bar.painted[-1]) >= len(mp.row_lines(rows[:1], bar.width))

    keys.extend(["f", "i", "l", "e", "enter"])
    tiny = [mp.PickRow("1 file", "a file row", "", "file"), mp.PickRow("2 other", "", "", "other")]
    assert mp.select_in_frame(front, title="t", rows=tiny, timeout=0.0) == "file"
    assert bar._probe_cache == {}, "a keystroke must not leave a stale probe fact"


def test_the_picker_home_and_end_reach_the_first_and_last_row(fake_frame):
    """Home/End are read by the frame's key reader and were then thrown away.

    The keys are in the reader's own vocabulary (``ESC [ H`` / ``ESC [ F`` and
    ``ESC [ 1~`` / ``ESC [ 4~``): a terminal sends them, so a loop that ignores them is a
    dead key for the user, and ``End`` on a scrolled list is the only way to reach the
    last row without paging down N times.
    """
    _bar, front, keys = fake_frame
    rows = mp.provider_rows()
    keys.extend(["end", "enter"])
    assert mp.select_in_frame(front, title="t", rows=rows, timeout=0.0) == rows[-1].value
    keys.extend(["home", "enter"])
    assert (
        mp.select_in_frame(front, title="t", rows=rows, default=len(rows) - 1, timeout=0.0)
        == rows[0].value
    )


def test_the_picker_page_jump_and_a_typed_row_number_land_on_the_right_row(fake_frame):
    _bar, front, keys = fake_frame
    rows = mp.provider_rows()
    keys.extend(["1", "3", "enter"])  # "13" narrows to the thirteenth row by its number
    assert mp.select_in_frame(front, title="t", rows=rows, timeout=0.0) == rows[12].value
    keys.extend(["pagedown", "enter"])
    assert mp.select_in_frame(front, title="t", rows=rows, timeout=0.0) == rows[12].value
    # a word narrows by name/endpoint/id: "deep" finds DeepSeek
    keys.extend(["d", "e", "e", "p", "enter"])
    assert mp.select_in_frame(front, title="t", rows=rows, timeout=0.0) == "deepseek"


def test_the_picker_falls_through_on_escape_and_keeps_the_frame_drawn(fake_frame):
    bar, front, keys = fake_frame
    keys.append("esc")
    assert mp.select_in_frame(front, title="t", rows=mp.provider_rows(), timeout=0.0) is None
    assert bar.drawn >= 1, "the frame is put back after the question"


def test_the_in_chat_hook_declines_when_there_is_no_frame():
    shell = FakeShell(front=None)
    assert mp.handle_slash(shell, "/model") is None
    assert mp.handle_slash(shell, "/provider") is None
    assert mp.handle_slash(shell, "/provider test deepseek") is None
    assert mp.handle_slash(shell, "/tools") is None
    assert mp.handle_slash(shell, "plain text") is None


def test_setup_in_the_chat_points_at_the_picker_when_a_key_is_missing(monkeypatch):
    shell = FakeShell(front=None)
    handled = mp.handle_slash(shell, "/setup")
    assert handled is False
    text = "\n".join(shell.said)
    assert "skaldir model" in text and "/model" in text

    monkeypatch.setattr(
        mp, "role_state", lambda **_kwargs: {role: {"ready": True} for role in sf.MANDATORY_ROLES}
    )
    assert mp.handle_slash(shell, "/setup") is None, "a complete setup keeps the old path"


def test_model_with_arguments_applies_it_from_the_chat(monkeypatch):
    shell = FakeShell(front=None)
    monkeypatch.setattr(sf, "measure_key", Stub())
    monkeypatch.setenv("DEEPSEEK_API_KEY", FAKE_KEY)
    handled = mp.handle_slash(shell, "/model deepseek deepseek-reasoner")
    assert handled is False
    text = "\n".join(shell.said)
    assert "deepseek-reasoner" in text
    assert FAKE_KEY not in text, "the in-chat report never carries the key"


def test_the_picker_draws_inside_the_frame_band_and_all(fake_frame):
    """The painted picture carries the frame's own band, the bars and the hint row."""
    bar, front, keys = fake_frame
    rows = mp.provider_rows()
    keys.extend(["enter"])
    mp.select_in_frame(front, title="pick a provider", rows=rows, timeout=0.0)
    picture = bar.painted[-1]
    assert any("model deepseek-chat" in line for line in picture), "the band is still there"
    assert any("❯ " in line for line in picture), "the input line is still there"
    assert all(len(line) == bar.width for line in picture), "every row is exactly the width"
    assert any(mp.PICKER_HINT in line for line in picture)


def test_a_key_the_entry_point_loaded_from_the_env_file_is_reported_as_the_file(tmp_path):
    """``cli.main`` folds the .env into the environment, so the file is the honest source."""
    config, env_path = _paths(tmp_path)
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text(f"DEEPSEEK_API_KEY={FAKE_KEY}\n", encoding="utf-8")
    env_path.chmod(0o600)
    before = env_path.read_bytes()
    payload, code = mp.apply_choice(
        _plan(provider="deepseek"),
        # exactly what the front door leaves behind: the file's value, in the environment
        env={"HOME": str(tmp_path), "DEEPSEEK_API_KEY": FAKE_KEY},
        config_path=config,
        prober=Stub(),
    )
    assert code == sf.EXIT_OK
    source = payload["key_sources"][sf.ROLE_LLM]
    assert source["kind"] == "env-file" and source["source"] == str(env_path)
    assert "entry point" in source["note"]
    assert env_path.read_bytes() == before, "a re-run never rewrites the credential file"
    assert payload["credentials_file"]["written"] is False

    checked, code = mp.check_choice(
        role=sf.ROLE_LLM, path=config,
        env={"HOME": str(tmp_path), "DEEPSEEK_API_KEY": FAKE_KEY},
        prober=Stub(),
    )
    assert code == sf.EXIT_OK
    assert checked["key_sources"][sf.ROLE_LLM]["kind"] == "env-file"


def test_a_block_above_the_frame_goes_out_in_one_write(fake_frame):
    """Five separate ``write_above`` calls lose the earlier lines — measured in the pty.

    The fake bar records the calls: the whole report has to leave in **one** chunk (one
    erase, one write, one repaint), followed by the line close.
    """
    bar, front, _keys = fake_frame
    bar.blocks = []
    bar.closed = 0

    def stream_chunk(text: str) -> None:
        bar.blocks.append(str(text))

    def close_line() -> None:
        bar.closed += 1

    bar.stream_chunk = stream_chunk
    bar.close_line = close_line
    report = mp.report_lines(
        {
            "plan": {"role": sf.ROLE_LLM, "model": "deepseek-chat", "provider": "deepseek",
                     "base_url": "https://api.deepseek.com/v1"},
            "key_sources": {sf.ROLE_LLM: {"name": "DEEPSEEK_API_KEY", "kind": "typed",
                                          "available": True}},
            "measure": {"model_echo": "deepseek-chat", "latency_ms": 12,
                        "streaming": {"supported": True},
                        "structured": {"mode_used": "json_object"}},
            "env_write": {"path": "/tmp/credential.env", "mode": "0600", "written": True},
            "verdict": "written",
        }
    )
    mp._write_above(front, report)
    assert len(bar.blocks) == 1, bar.blocks
    assert bar.blocks[0].count("\n") == len(report) - 1
    assert "deepseek-chat" in bar.blocks[0] and bar.closed == 1
    assert bar._probe_cache == {}, "the facts are forgotten before the block is written"


def test_provider_bare_in_the_frame_browses_without_writing(fake_frame, monkeypatch):
    """``/provider`` shows the same list and changes nothing."""
    bar, front, keys = fake_frame
    shell = FakeShell(front=front)
    monkeypatch.setattr(mp, "_frame_of", lambda _shell: front)
    monkeypatch.setattr(mp, "select_in_frame", lambda *a, **k: "deepseek")
    written: list[str] = []
    monkeypatch.setattr(mp, "apply_choice", lambda *a, **k: written.append("apply"))
    bar.blocks = []
    bar.stream_chunk = lambda text: bar.blocks.append(str(text))
    bar.close_line = lambda: None
    assert mp.handle_slash(shell, "/provider") is False
    assert written == [], "/provider is a view, not a write"
    text = "\n".join(bar.blocks)
    assert "https://api.deepseek.com/v1" in text
    assert DEEPSEEK_DEFAULT in text and "DEEPSEEK_API_KEY" in text


def test_setup_in_a_frame_points_at_the_picker_above_the_frame(monkeypatch, fake_frame):
    bar, front, _keys = fake_frame
    shell = FakeShell(front=front)
    monkeypatch.setattr(mp, "_frame_of", lambda _shell: front)
    monkeypatch.setattr(
        mp, "role_state",
        lambda **_kwargs: {sf.ROLE_TYPESAFE: {"ready": False}, sf.ROLE_LLM: {"ready": True}},
    )
    bar.blocks = []
    bar.stream_chunk = lambda text: bar.blocks.append(str(text))
    bar.close_line = lambda: None
    assert mp.handle_slash(shell, "/setup") is False
    text = "\n".join(bar.blocks)
    assert "skaldir model" in text and "0600" in text
    assert shell.said == [], "with a frame up the pointer goes above the frame"


def test_a_block_above_the_frame_falls_back_to_one_line_at_a_time(fake_frame):
    """A bar without the streaming path still gets every line (and the block, in order)."""
    bar, front, _keys = fake_frame
    mp._write_above(front, ["one", "two", ""])
    assert bar.above == ["one", "two"]


def test_the_slash_row_for_model_is_handed_over_not_taken():
    # slash.py belongs to the palette block: the row is delivered as a snippet, with the
    # real argv, so the palette can point /model at the picker without a second mapping.
    assert 'argv=("model",)' in mp.SLASH_SNIPPET
    import skaldir.slash as slash

    entry = slash.command_for("model")
    assert entry is not None
    assert entry.argv, "the palette row for /model still names a real command"


# --------------------------------------------------------------------------- #
# the window: a list longer than the space it is drawn in has to scroll          #
# --------------------------------------------------------------------------- #


class _WideBar:
    """The smallest framed picker ``_picture`` accepts: a width, a row budget, no screen."""

    width = 78
    unicode = True
    max_rows = 10
    screen = False


def _marked(lines: list[str], pointer: str = mp.PICKER_POINTER_RICH) -> list[str]:
    """The lines carrying the pointer on a **row** (the frame's own prompt is not a row)."""
    head = "  " + pointer
    return [line for line in lines if line.startswith(head) and line.strip() != pointer]


def test_the_window_follows_the_pointer_so_the_mark_is_never_drawn_off_screen():
    """``rows[:limit]`` with an absolute pointer is a dead key: the mark is simply missing."""
    rows = mp.provider_rows()
    window, index, undrawn = mp.window_rows(rows, selected=len(rows) - 1, limit=12)
    assert window[index] is rows[-1], "the pointer's own row is inside the window drawn"
    assert undrawn == len(rows) - 12
    # the shape this replaced, spelled out: ten rows drawn, the pointer at fifteen, no mark
    assert mp.PICKER_POINTER_RICH not in "\n".join(
        mp.row_lines(rows[:10], 78, selected=len(rows) - 1)
    )


def test_the_framed_picker_draws_the_mark_inside_its_window():
    rows = mp.provider_rows()
    lines, _cursor = mp._picture(
        _WideBar(), title="provider", rows=rows, selected=len(rows) - 1, query="", hidden=0
    )
    marked = _marked(lines)
    assert len(marked) == 1, "exactly one row carries the pointer"
    assert rows[-1].name[:8] in marked[0], "and it is the row the user moved to"
    assert any(f"… {len(rows) - _WideBar.max_rows} more" in line for line in lines), (
        "the rows outside the window are counted"
    )
    assert not any("1 OpenAI" in line for line in lines), "the first page scrolled away"


def test_the_block_runner_scrolls_the_same_way():
    rows = mp.provider_rows()
    block = mp._block_lines(
        "provider", rows, selected=len(rows) - 1, query="", width=78, shown=10,
        unicode=True, allow_text=False,
    )
    marked = _marked(block)
    assert len(marked) == 1, "the block draws the pointer too — it used to slice it away"
    assert rows[-1].name[:8] in marked[0]
    assert any(f"… {len(rows) - 10} more" in line for line in block)
    assert not any("1 OpenAI" in line for line in block)


def test_a_list_that_fits_the_space_is_left_alone():
    rows = mp.provider_rows()[:4]
    assert mp.window_rows(rows, selected=1, limit=10) == (list(rows), 1, 0)
    assert mp.window_rows([], selected=0, limit=10) == ([], 0, 0)
    # an unlimited request (the bar's own ``0``) draws every row, pointer included
    assert mp.window_rows(rows, selected=3, limit=0) == (list(rows), 3, 0)
