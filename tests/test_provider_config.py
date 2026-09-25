"""The provider configuration store: atomic, backed up, and secret-free.

No network and no real install: every test writes into ``tmp_path`` and passes an
explicit path (and ``env`` where the store takes one), so neither the developer's
``~/.hermes`` nor a ``GODSV_LLM_*`` variable exported in the shell can influence a
result. The live behaviour (``provider test``) is proven in
``tests/test_provider_cli.py`` against a local stub endpoint.
"""

from __future__ import annotations

import pytest
import yaml

from skaldir import llm_provider as lp
from skaldir import provider_config as pc

#: A fake that LOOKS like a key (never a real one). The store must refuse it, and
#: no refusal message may ever contain it.
FAKE_KEY = "sk-" + "test" * 8
LOCAL_URL = "http://127.0.0.1:8080/v1"
REMOTE_URL = "https://api.deepseek.com/v1"

ENV_NAMES = (
    "GODSV_LLM_CONFIG",
    "GODSV_LLM_PROVIDER",
    "GODSV_LLM_BASE_URL",
    "GODSV_LLM_API_BASE",
    "GODSV_LLM_API_KEY",
    "GODSV_LLM_KEY",
    "GODSV_LLM_MODEL",
    "GODSV_LLM_TIER_MODELS",
    "OPENAI_BASE_URL",
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
)


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch):
    """The store is never measured against the developer's own shell exports."""
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GODSV_HOME", "/nonexistent/godsv-provider-config-tests")
    monkeypatch.delenv("HERMES_HOME", raising=False)


def _write(path, document) -> None:
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")


def _document(path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# where the file lives                                                        #
# --------------------------------------------------------------------------- #


def test_config_path_prefers_godsv_llm_config(tmp_path, monkeypatch):
    monkeypatch.setenv("GODSV_LLM_CONFIG", str(tmp_path / "explicit.yaml"))
    assert pc.config_path() == tmp_path / "explicit.yaml"


def test_config_path_follows_the_runtime_rule(tmp_path, monkeypatch):
    monkeypatch.setenv("GODSV_HOME", str(tmp_path / "home"))
    assert pc.config_path() == tmp_path / "home" / "config.yaml"
    monkeypatch.delenv("GODSV_HOME")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    # The rename rule (src/skaldir/registry.py): a NEW installation gets
    # `~/.hermes/skaldir`; only an existing `~/.hermes/godsv` (with no `skaldir`)
    # keeps its state. A fresh HERMES_HOME has neither, so the new name wins.
    assert pc.config_path() == tmp_path / "hermes" / "skaldir" / "config.yaml"
    (tmp_path / "hermes" / "godsv").mkdir(parents=True)
    assert pc.config_path() == tmp_path / "hermes" / "godsv" / "config.yaml", (
        "an existing pre-rename directory is still used"
    )


def test_config_path_is_the_file_the_provider_layer_resolves(tmp_path, monkeypatch):
    """One file, one rule: the store writes what ``llm_provider`` reads."""
    monkeypatch.setenv("GODSV_LLM_CONFIG", str(tmp_path / "config.yaml"))
    assert pc.config_path() == lp.default_config_file()
    assert pc.config_path(None) == lp.default_config_file()


# --------------------------------------------------------------------------- #
# writing the registry + the active projection                                #
# --------------------------------------------------------------------------- #


def test_add_writes_the_registry_and_projects_the_default(tmp_path):
    path = tmp_path / "config.yaml"
    report = pc.add_provider(
        "local", base_url=LOCAL_URL, model="qwen2.5-7b-instruct",
        tiers={"small": "qwen2.5-3b-instruct"}, path=path,
    )
    assert report["action"] == "added"
    assert report["default"] == "local"
    assert report["projected"] is True
    assert report["backup"] is None  # nothing to back up on the first write
    assert report["created"] is True

    document = _document(path)
    assert document["godsv"]["providers"]["default"] == "local"
    entry = document["godsv"]["providers"]["entries"]["local"]
    assert entry == {
        "kind": "openai-compatible",
        "base_url": LOCAL_URL,
        "model": "qwen2.5-7b-instruct",
        "tiers": {"small": "qwen2.5-3b-instruct"},
    }
    assert document["godsv"]["llm"] == {
        "provider": "openai-compatible",
        "base_url": LOCAL_URL,
        "model": "qwen2.5-7b-instruct",
        "tiers": {"small": "qwen2.5-3b-instruct"},
    }


def test_the_written_file_is_what_the_runtime_resolves(tmp_path):
    path = tmp_path / "config.yaml"
    pc.add_provider("local", base_url=LOCAL_URL, model="m1", path=path)
    cfg = lp.resolve_provider_config(env={}, config_path=path)
    assert cfg.problems == []
    assert cfg.ready() is True
    assert cfg.base_url == LOCAL_URL
    assert cfg.model == "m1"
    client = lp.provider_for(env={}, config_path=path)
    assert isinstance(client, lp.OpenAICompatibleLLM)


def test_the_projection_carries_the_credential_name_and_the_runtime_reads_it(tmp_path):
    path = tmp_path / "config.yaml"
    pc.add_provider(
        "deepseek", base_url=REMOTE_URL, model="deepseek-chat",
        api_key_env="GODSV_TEST_DEEPSEEK_KEY", path=path,
    )
    entry = _document(path)["godsv"]["providers"]["entries"]["deepseek"]
    assert entry["api_key_env"] == "GODSV_TEST_DEEPSEEK_KEY"
    projection = _document(path)["godsv"]["llm"]
    assert projection["api_key_env"] == "GODSV_TEST_DEEPSEEK_KEY"

    cfg = lp.resolve_provider_config(
        env={"GODSV_TEST_DEEPSEEK_KEY": "resolved-in-test"}, config_path=path
    )
    assert cfg.api_key == "resolved-in-test"  # from the environment, never the file
    assert cfg.sources["api_key"] == "env:GODSV_TEST_DEEPSEEK_KEY"


def test_the_file_never_carries_a_literal_credential(tmp_path):
    path = tmp_path / "config.yaml"
    pc.add_provider(
        "deepseek", base_url=REMOTE_URL, model="deepseek-chat",
        api_key_env="GODSV_TEST_DEEPSEEK_KEY", path=path,
    )
    text = path.read_text(encoding="utf-8")
    document = _document(path)
    assert set(document["godsv"]["llm"]).isdisjoint(lp.SECRET_FILE_KEYS)
    assert set(document["godsv"]["providers"]["entries"]["deepseek"]).isdisjoint(
        lp.SECRET_FILE_KEYS
    )
    assert "sk-" not in text
    # the credential really is read from the environment only
    assert "GODSV_TEST_DEEPSEEK_KEY" in text


def test_second_add_does_not_steal_the_default(tmp_path):
    path = tmp_path / "config.yaml"
    pc.add_provider("a", base_url=LOCAL_URL, model="m1", path=path)
    report = pc.add_provider("b", base_url="http://127.0.0.1:8081/v1", model="m2", path=path)
    assert report["projected"] is False
    document = _document(path)
    assert document["godsv"]["providers"]["default"] == "a"
    assert document["godsv"]["llm"]["base_url"] == LOCAL_URL


def test_add_can_force_a_new_provider_to_become_the_default(tmp_path):
    path = tmp_path / "config.yaml"
    pc.add_provider("a", base_url=LOCAL_URL, model="m1", path=path)
    pc.add_provider(
        "b", base_url="http://127.0.0.1:8081/v1", model="m2", make_default=True, path=path
    )
    assert _document(path)["godsv"]["providers"]["default"] == "b"


def test_add_refuses_a_duplicate_unless_forced(tmp_path):
    path = tmp_path / "config.yaml"
    pc.add_provider("a", base_url=LOCAL_URL, model="m1", path=path)
    with pytest.raises(pc.ProviderExists):
        pc.add_provider("a", base_url=LOCAL_URL, model="m2", path=path)
    report = pc.add_provider("a", base_url=LOCAL_URL, model="m2", replace=True, path=path)
    assert report["action"] == "replaced"
    assert _document(path)["godsv"]["providers"]["entries"]["a"]["model"] == "m2"


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"base_url": "", "model": "m"}, pc.ProviderInvalid),
        ({"base_url": "api.example.com/v1", "model": "m"}, pc.ProviderInvalid),
        ({"base_url": REMOTE_URL, "model": ""}, pc.ProviderInvalid),
        ({"base_url": REMOTE_URL, "model": "m", "kind": "magic"}, pc.ProviderInvalid),
        ({"base_url": REMOTE_URL, "model": "m", "tiers": {"huge": "m"}}, pc.ProviderInvalid),
        ({"base_url": REMOTE_URL, "model": "m", "timeout_s": 0}, pc.ProviderInvalid),
        ({"base_url": REMOTE_URL, "model": "m", "max_tokens": 0}, pc.ProviderInvalid),
    ],
)
def test_add_refuses_unusable_input(tmp_path, kwargs, error):
    with pytest.raises(error):
        pc.add_provider("x", path=tmp_path / "config.yaml", **kwargs)
    assert not (tmp_path / "config.yaml").exists()


def test_add_refuses_a_name_that_is_not_a_slug(tmp_path):
    for bad in ("Local", "../x", "with space", "", "-leading", "a" * 70):
        with pytest.raises(pc.ProviderInvalid):
            pc.add_provider(bad, base_url=LOCAL_URL, model="m", path=tmp_path / "config.yaml")


# --------------------------------------------------------------------------- #
# a literal secret is refused (read side and write side)                       #
# --------------------------------------------------------------------------- #


def test_add_refuses_a_literal_key_masquerading_as_a_variable_name(tmp_path):
    path = tmp_path / "config.yaml"
    with pytest.raises(pc.ProviderInvalid) as excinfo:
        pc.add_provider("x", base_url=REMOTE_URL, model="m", api_key_env=FAKE_KEY, path=path)
    assert FAKE_KEY not in str(excinfo.value)
    assert "NAME of an environment variable" in str(excinfo.value)
    assert not path.exists()


def test_add_refuses_a_secret_smuggled_into_a_value(tmp_path):
    path = tmp_path / "config.yaml"
    with pytest.raises(pc.ProviderSecretRefused) as excinfo:
        pc.add_provider(
            "x", base_url=f"https://api.example.com/v1?api_key={FAKE_KEY}", model="m", path=path
        )
    assert FAKE_KEY not in str(excinfo.value)
    assert "refusing to write" in str(excinfo.value)
    assert not path.exists()


def test_a_refused_write_leaves_the_previous_file_and_no_temp_files(tmp_path):
    path = tmp_path / "config.yaml"
    pc.add_provider("a", base_url=LOCAL_URL, model="m1", path=path)
    before = path.read_bytes()
    with pytest.raises(pc.ProviderSecretRefused):
        pc.add_provider("b", base_url="http://127.0.0.1:8081/v1", model=FAKE_KEY, path=path)
    assert path.read_bytes() == before
    assert [p.name for p in tmp_path.iterdir()] == [path.name]


def test_load_refuses_a_literal_secret_in_the_lm_block(tmp_path):
    path = tmp_path / "config.yaml"
    _write(
        path,
        {
            "godsv": {
                "llm": {
                    "provider": "openai-compatible",
                    "base_url": REMOTE_URL,
                    "model": "deepseek-chat",
                    "api_key": FAKE_KEY,
                }
            }
        },
    )
    with pytest.raises(pc.ProviderSecretRefused) as excinfo:
        pc.load_store(path)
    message = str(excinfo.value)
    assert FAKE_KEY not in message
    assert "api_key" in message and str(path) in message

    # the provider layer's own invariant, unchanged: a secret is never read from a file
    with pytest.raises(lp.ProviderConfigError) as lp_error:
        lp.provider_for(env={}, config_path=path)
    assert FAKE_KEY not in str(lp_error.value)
    assert "never read from config files" in str(lp_error.value)
    assert lp.provider_status(env={}, config_path=path)["ready"] is False


@pytest.mark.parametrize("key", ["api_key", "key", "token", "secret", "access_token"])
def test_every_secret_key_name_is_refused(tmp_path, key):
    path = tmp_path / "config.yaml"
    _write(path, {"godsv": {"llm": {"base_url": REMOTE_URL, "model": "m", key: "value"}}})
    with pytest.raises(pc.ProviderSecretRefused):
        pc.load_store(path)


def test_load_refuses_a_literal_secret_inside_a_provider_entry(tmp_path):
    path = tmp_path / "config.yaml"
    _write(
        path,
        {
            "godsv": {
                "providers": {
                    "default": "x",
                    "entries": {
                        "x": {
                            "kind": "openai-compatible",
                            "base_url": REMOTE_URL,
                            "model": "m",
                            "api_key": FAKE_KEY,
                        }
                    },
                }
            }
        },
    )
    with pytest.raises(pc.ProviderSecretRefused) as excinfo:
        pc.load_store(path)
    assert FAKE_KEY not in str(excinfo.value)
    assert "entries.x" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# durability: atomic write + backup + foreign keys preserved                  #
# --------------------------------------------------------------------------- #


def test_every_write_is_atomic_and_backed_up(tmp_path):
    path = tmp_path / "config.yaml"
    pc.add_provider("a", base_url=LOCAL_URL, model="m1", path=path)
    first = path.read_bytes()
    report = pc.add_provider("b", base_url="http://127.0.0.1:8081/v1", model="m2", path=path)
    backup = report["backup"]
    assert backup and backup.endswith(".bak")
    from pathlib import Path

    assert Path(backup).read_bytes() == first
    assert report["created"] is False
    assert report["previous_bytes"] == len(first)
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(
        [path.name, Path(backup).name]
    )


def test_an_old_backup_is_never_overwritten(tmp_path):
    path = tmp_path / "config.yaml"
    pc.add_provider("a", base_url=LOCAL_URL, model="m1", path=path)
    first = pc.add_provider("b", base_url="http://127.0.0.1:8081/v1", model="m2", path=path)
    second = pc.add_provider("c", base_url="http://127.0.0.1:8082/v1", model="m3", path=path)
    assert first["backup"] != second["backup"]


def test_other_top_level_keys_survive_a_write(tmp_path):
    path = tmp_path / "config.yaml"
    _write(path, {"mcp_servers": {"x": {"command": "y"}}, "godsv": {"other": 1}})
    pc.add_provider("a", base_url=LOCAL_URL, model="m1", path=path)
    document = _document(path)
    assert document["mcp_servers"] == {"x": {"command": "y"}}
    assert document["godsv"]["other"] == 1


def test_unknown_entry_keys_are_preserved(tmp_path):
    path = tmp_path / "config.yaml"
    _write(
        path,
        {
            "godsv": {
                "providers": {
                    "default": "a",
                    "entries": {
                        "a": {
                            "kind": "openai-compatible",
                            "base_url": LOCAL_URL,
                            "model": "m",
                            "headers": {"X-Trace": "1"},
                        }
                    },
                }
            }
        },
    )
    pc.set_tiers("a", tiers={"small": "s"}, path=path)
    entry = _document(path)["godsv"]["providers"]["entries"]["a"]
    assert entry["headers"] == {"X-Trace": "1"}
    assert entry["tiers"] == {"small": "s"}


def test_a_broken_yaml_file_is_refused_and_not_overwritten(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("godsv: [unclosed\n", encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(pc.ProviderInvalid):
        pc.load_store(path)
    with pytest.raises(pc.ProviderInvalid):
        pc.add_provider("a", base_url=LOCAL_URL, model="m", path=path)
    assert path.read_bytes() == before


# --------------------------------------------------------------------------- #
# default / remove / tiers                                                    #
# --------------------------------------------------------------------------- #


def test_set_default_switches_the_projection(tmp_path):
    path = tmp_path / "config.yaml"
    pc.add_provider("a", base_url=LOCAL_URL, model="m1", path=path)
    pc.add_provider(
        "b", base_url=REMOTE_URL, model="m2", api_key_env="B_KEY", path=path
    )
    report = pc.set_default("b", path=path, env={"B_KEY": "x"})
    assert report["action"] == "default" and report["default"] == "b"
    assert report["projection"]["model"] == "m2"
    document = _document(path)
    assert document["godsv"]["providers"]["default"] == "b"
    assert document["godsv"]["llm"]["model"] == "m2"
    assert document["godsv"]["llm"]["api_key_env"] == "B_KEY"
    assert pc.load_store(path).default == "b"


def test_set_default_refuses_an_unknown_provider(tmp_path):
    path = tmp_path / "config.yaml"
    pc.add_provider("a", base_url=LOCAL_URL, model="m1", path=path)
    with pytest.raises(pc.ProviderNotFound) as excinfo:
        pc.set_default("nope", path=path)
    assert "a" in str(excinfo.value)  # what IS configured


def test_remove_clears_the_projection_and_names_what_is_left(tmp_path):
    path = tmp_path / "config.yaml"
    pc.add_provider("a", base_url=LOCAL_URL, model="m1", path=path)
    pc.add_provider("b", base_url="http://127.0.0.1:8081/v1", model="m2", path=path)
    report = pc.remove_provider("a", path=path)
    assert report["action"] == "removed"
    assert report["projection_cleared"] is True
    assert report["remaining"] == ["b"]
    assert report["warnings"]  # no default any more — said out loud
    document = _document(path)
    assert "llm" not in document["godsv"]
    assert document["godsv"]["providers"]["default"] == ""
    assert not pc.load_store(path).default


def test_remove_of_a_non_default_keeps_the_projection(tmp_path):
    path = tmp_path / "config.yaml"
    pc.add_provider("a", base_url=LOCAL_URL, model="m1", path=path)
    pc.add_provider("b", base_url="http://127.0.0.1:8081/v1", model="m2", path=path)
    report = pc.remove_provider("b", path=path)
    assert report["projection_cleared"] is False
    assert _document(path)["godsv"]["llm"]["model"] == "m1"


def test_remove_refuses_an_unknown_provider(tmp_path):
    path = tmp_path / "config.yaml"
    pc.add_provider("a", base_url=LOCAL_URL, model="m1", path=path)
    with pytest.raises(pc.ProviderNotFound):
        pc.remove_provider("nope", path=path)


def test_removing_the_last_provider_leaves_a_clean_file(tmp_path):
    path = tmp_path / "config.yaml"
    pc.add_provider("a", base_url=LOCAL_URL, model="m1", path=path)
    pc.remove_provider("a", path=path)
    document = _document(path)
    assert document == {"godsv": {}}
    assert pc.load_store(path).names() == []


def test_tiers_are_stored_in_the_registry_and_projected(tmp_path):
    path = tmp_path / "config.yaml"
    pc.add_provider("a", base_url=LOCAL_URL, model="m1", path=path)
    report = pc.set_tiers("a", tiers={"small": "s", "main": "M"}, path=path)
    assert report["tiers"] == {"small": "s", "main": "M"}
    assert report["projection_updated"] is True
    assert _document(path)["godsv"]["llm"]["tiers"] == {"small": "s", "main": "M"}

    pc.set_tiers("a", tiers={"small": ""}, path=path)
    assert _document(path)["godsv"]["providers"]["entries"]["a"]["tiers"] == {"main": "M"}

    pc.set_tiers("a", tiers={"mid": "mi"}, path=path)
    assert pc.load_store(path).providers["a"].tiers == {"mid": "mi", "main": "M"}

    pc.set_tiers("a", tiers={}, replace=True, path=path)
    assert pc.load_store(path).providers["a"].tiers == {}


def test_tiers_refuse_an_unknown_tier(tmp_path):
    path = tmp_path / "config.yaml"
    pc.add_provider("a", base_url=LOCAL_URL, model="m1", path=path)
    with pytest.raises(pc.ProviderInvalid):
        pc.set_tiers("a", tiers={"gigantic": "m"}, path=path)


# --------------------------------------------------------------------------- #
# reporting                                                                   #
# --------------------------------------------------------------------------- #


def test_list_report_marks_the_default_and_redacts_the_credential(tmp_path):
    path = tmp_path / "config.yaml"
    pc.add_provider("local", base_url=LOCAL_URL, model="m1", path=path)
    pc.add_provider(
        "deepseek", base_url=REMOTE_URL, model="deepseek-chat",
        api_key_env="GODSV_TEST_DEEPSEEK_KEY", path=path,
    )
    payload = pc.store_report(None, path=path, env={"GODSV_TEST_DEEPSEEK_KEY": "in-the-env"})
    assert payload["count"] == 2
    assert payload["default"] == "local"
    rows = {row["name"]: row for row in payload["providers"]}
    assert rows["local"]["default"] is True and rows["local"]["ready"] is True
    assert rows["local"]["credential"] == "not-required"
    assert rows["deepseek"]["default"] is False
    assert rows["deepseek"]["credential"] == "set"
    assert rows["deepseek"]["endpoint"] == REMOTE_URL + "/chat/completions"
    assert "in-the-env" not in str(payload)
    assert payload["active"]["api_key"] in ("set", "unset")


def test_report_names_the_environment_when_it_overrides_the_file(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    pc.add_provider("local", base_url=LOCAL_URL, model="m1", path=path)
    monkeypatch.setenv("GODSV_LLM_BASE_URL", REMOTE_URL)
    payload = pc.store_report(None, path=path)
    assert "base_url" in payload["active_env_overrides"]
    assert payload["active"]["sources"]["base_url"] == "env:GODSV_LLM_BASE_URL"


def test_report_marks_a_hand_written_block(tmp_path):
    path = tmp_path / "config.yaml"
    _write(
        path,
        {"godsv": {"llm": {"provider": "openai-compatible", "base_url": LOCAL_URL, "model": "m"}}},
    )
    payload = pc.store_report(None, path=path, env={})
    assert payload["count"] == 0
    assert payload["hand_written_block"] is True
    assert payload["active"]["ready"] is True
    assert payload["drift"] == []


def test_drift_between_the_registry_and_the_projection_is_reported(tmp_path):
    path = tmp_path / "config.yaml"
    pc.add_provider("local", base_url=LOCAL_URL, model="m1", path=path)
    document = _document(path)
    document["godsv"]["llm"]["model"] = "hand-edited"
    _write(path, document)
    payload = pc.store_report(None, path=path, env={})
    assert payload["drift"]
    assert "hand-edited" in payload["drift"][0]


def test_doctor_lists_what_is_missing_per_provider(tmp_path):
    path = tmp_path / "config.yaml"
    pc.add_provider("local", base_url=LOCAL_URL, model="m1", path=path)
    pc.add_provider(
        "remote", base_url=REMOTE_URL, model="deepseek-chat",
        api_key_env="GODSV_TEST_DEEPSEEK_KEY", make_default=True, path=path,
    )
    payload = pc.doctor(path=path, env={})
    assert payload["count"] == 2
    assert payload["ok"] is False
    assert payload["usable"] == ["local"]
    assert payload["broken"] == ["remote"]
    missing = {row["name"]: row["missing"] for row in payload["providers"]}
    assert missing["remote"] and "GODSV_TEST_DEEPSEEK_KEY" in missing["remote"][0]
    assert missing["local"] == []
    assert payload["next_actions"]


def test_doctor_is_ok_when_every_provider_is_usable(tmp_path):
    path = tmp_path / "config.yaml"
    pc.add_provider("local", base_url=LOCAL_URL, model="m1", path=path)
    payload = pc.doctor(path=path, env={})
    assert payload["ok"] is True
    assert payload["broken"] == []
    assert payload["next_actions"] == []


def test_doctor_on_an_empty_config_names_the_command_to_run(tmp_path):
    payload = pc.doctor(path=tmp_path / "config.yaml", env={})
    assert payload["ok"] is False
    assert payload["count"] == 0
    assert payload["next_actions"][0]["command"].startswith("skaldir provider add")


# --------------------------------------------------------------------------- #
# credential rules                                                            #
# --------------------------------------------------------------------------- #


def test_a_local_endpoint_needs_no_credential():
    entry = pc.ProviderEntry(name="local", base_url=LOCAL_URL, model="m")
    assert entry.is_local() is True
    assert entry.credential_state({}) == "not-required"
    assert entry.ready({}) is True
    assert entry.missing({}) == []


def test_a_remote_endpoint_without_a_credential_is_not_ready():
    entry = pc.ProviderEntry(
        name="remote", base_url=REMOTE_URL, model="m", api_key_env="MY_KEY"
    )
    assert entry.needs_credential({}) is True
    assert entry.credential_state({}) == "unset"
    assert entry.ready({}) is False
    assert "export MY_KEY" in " ".join(entry.missing({}))
    assert entry.ready({"MY_KEY": "x"}) is True


def test_ready_needs_a_base_url_and_a_model():
    bare = pc.ProviderEntry(name="x")
    missing = bare.missing({})
    assert any("base_url" in item for item in missing)
    assert any("model" in item for item in missing)
    assert bare.ready({}) is False


def test_local_detection_agrees_with_the_provider_layer():
    """The store mirrors ``llm_provider._is_local`` — pinned so they cannot drift."""
    for url in (
        LOCAL_URL,
        "http://localhost:1234/v1",
        "https://api.deepseek.com/v1",
        "http://host.docker.internal:9000/v1",
        "https://user:pw@example.com:443/v1",
        "https://[::1]:8080/v1",
        "not-a-url",
    ):
        assert pc.endpoint_is_local(url) == lp._is_local(url), url


def test_probe_without_a_credential_makes_no_call(tmp_path):
    path = tmp_path / "config.yaml"
    pc.add_provider(
        "remote", base_url="https://api.invalid.example/v1", model="m",
        api_key_env="GODSV_TEST_UNSET_KEY", path=path,
    )
    with pytest.raises(pc.ProviderNeedsCredential) as excinfo:
        pc.probe_provider("remote", path=path, env={}, timeout_s=0.5)
    message = str(excinfo.value)
    assert "GODSV_TEST_UNSET_KEY" in message
    assert "api.invalid.example" in message


def test_probe_refuses_an_unusable_provider(tmp_path):
    path = tmp_path / "config.yaml"
    _write(
        path,
        {
            "godsv": {
                "providers": {
                    "default": "half",
                    "entries": {"half": {"kind": "openai-compatible", "base_url": LOCAL_URL}},
                }
            }
        },
    )
    with pytest.raises(pc.ProviderUnusable) as excinfo:
        pc.probe_provider("half", path=path, env={})
    assert "model is unset" in str(excinfo.value)


def test_probe_refuses_an_unknown_tier(tmp_path):
    path = tmp_path / "config.yaml"
    pc.add_provider("a", base_url=LOCAL_URL, model="m", path=path)
    with pytest.raises(pc.ProviderInvalid):
        pc.probe_provider("a", path=path, env={}, tier="enormous")


def test_probe_refuses_an_unknown_provider(tmp_path):
    path = tmp_path / "config.yaml"
    pc.add_provider("a", base_url=LOCAL_URL, model="m", path=path)
    with pytest.raises(pc.ProviderNotFound):
        pc.probe_provider("nope", path=path, env={})


def test_probe_never_renders_a_credential(tmp_path):
    """A provider that LOOKS configured but whose credential is missing says so."""
    path = tmp_path / "config.yaml"
    pc.add_provider(
        "remote", base_url=REMOTE_URL, model="m", api_key_env="GODSV_TEST_MISSING", path=path
    )
    store = pc.load_store(path, env={})
    entry = store.providers["remote"]
    row = entry.redacted({})
    assert row["credential"] == "unset"
    assert "api_key" not in row  # only api_key_env (a NAME)
    text = repr(entry)
    assert "GODSV_TEST_MISSING" in text  # the NAME is fine to show
    assert "api_key=<unset>" in text  # the state, never a value
    assert "value" not in text
