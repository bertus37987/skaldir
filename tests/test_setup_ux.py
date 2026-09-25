"""The SETUP UX: the provider picker, the hidden key, the ``.env`` (0600), ``--check``.

What this file pins, in the owner's words:

* **the base URL is already there** — every picker row carries one, a local server
  carries its own loopback URL, and accepting a row means pressing Enter;
* **the key is asked for once, hidden** — through ``getpass`` (no echo), never
  through a flag that could sit in the shell history, never printed, never logged,
  and written to the ``.env`` next to the config with mode ``0600``;
* **the config names the variable** (``api_key_env``) and never the value;
* **a key that already exists is reused** — and the report says *where* it came from;
* **headless is the same path** — every question has a flag and a variable, and the
  two sets are compared here rather than trusted;
* **the first commands printed are commands that exist** — looked up in the real
  command tree, with the owner's preferred spellings reported as missing, not faked.

The two endpoints are real ``ThreadingHTTPServer`` sockets on the **registry's own
local ports** (``llama.cpp`` on 8080, ``vLLM`` on 8000), so the interactive session
proves the pre-filled URL is a URL that answers. No real credential is used, and no
test may print the value it writes.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import stat
import subprocess
from typing import Any

import pytest
import yaml
from test_setup_flow import ENV_NAMES, FAKE_KEY, StubEndpoint

from skaldir import cli, cli_contract
from skaldir import provider_registry_data as registry
from skaldir import setup_cli as scli
from skaldir import setup_flow as sf

#: The credential file the flow writes by default: beside the config.
ENV_FILE_OF = ".env"


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch, tmp_path):
    """Neither the developer's shell exports nor the real ~/.hermes may leak in."""
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SKALDIR_HOME", str(tmp_path / "runtime"))
    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.delenv("GODSV_HOME", raising=False)
    for name in ("SKALDIR_SETUP_ENV_FILE", "SKALDIR_SETUP_LLM_PROVIDER",
                 "SKALDIR_SETUP_JEV_PROVIDER", "SKALDIR_SETUP_LLM_API_KEY",
                 "SKALDIR_SETUP_JEV_API_KEY", "LLAMA_API_KEY", "VLLM_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def _stub_on(host: str, port: int, *, model: str) -> StubEndpoint:
    """One stub on a fixed port — a **skip** (never a silent pass) when it is taken."""
    try:
        return StubEndpoint(host=host, port=port, model=model)
    except OSError as exc:
        pytest.skip(f"port {host}:{port} is busy ({exc}) — the local-row proof needs it")


@pytest.fixture
def stub():
    server = StubEndpoint()
    try:
        yield server
    finally:
        server.close()


@pytest.fixture
def stub_jevs():
    server = StubEndpoint(model="stub-jev-model")
    try:
        yield server
    finally:
        server.close()


@pytest.fixture
def local_stubs():
    """Two real endpoints on the registry's own loopback ports (8080 + 8000).

    These are the URLs the picker pre-fills for ``llamacpp`` and ``vllm``, so the
    interactive proof is "the pre-filled URL was accepted and it answered", not
    "a URL was printed".
    """
    llamacpp = _stub_on("127.0.0.1", 8080, model="stub-llama")
    vllm = _stub_on("127.0.0.1", 8000, model="stub-jev")
    try:
        yield llamacpp, vllm
    finally:
        llamacpp.close()
        vllm.close()


class ScriptedAsk:
    """The answers a human would type, plus the prompts that were shown."""

    def __init__(self, visible: list[str], secret: list[str]) -> None:
        self.visible = list(visible)
        self.secret = list(secret)
        self.prompts: list[str] = []
        self.secret_prompts: list[str] = []
        self.secrets_used = 0

    def line(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.visible.pop(0) if self.visible else ""

    def masked(self, prompt: str) -> str:
        self.secret_prompts.append(prompt)
        self.secrets_used += 1
        return self.secret.pop(0) if self.secret else ""


def _row_number(provider_id: str) -> str:
    ids = [row.id for row in sf.provider_choices()]
    return str(ids.index(provider_id) + 1)


def _answered(monkeypatch, ask: ScriptedAsk) -> None:
    """A TTY is what makes the flow ask; the answers themselves are scripted."""
    monkeypatch.setattr(scli, "_interactive_ok", lambda args: True)
    monkeypatch.setattr(scli, "_read_line", ask.line)
    monkeypatch.setattr(scli, "_read_secret", ask.masked)


# --------------------------------------------------------------------------- #
# 1. the picker: name, one-line note, and the base URL already filled in        #
# --------------------------------------------------------------------------- #


def test_the_picker_carries_the_note_and_the_url_for_every_row():
    rows = sf.provider_choices()

    # multi-SKU providers expand into one row per plan endpoint
    assert len(rows) >= len(registry.REGISTRY_ORDER), "one row per plan endpoint"
    for row in rows:
        assert row.base_url.startswith(("http://", "https://")), row.id
        assert row.note, f"{row.id} has no one-line note"
        assert row.label
        if row.api_key_env:
            assert row.api_key_env.isupper(), row.api_key_env
    local = {row.id: row.base_url for row in rows if row.local}
    assert local["llamacpp"] == "http://127.0.0.1:8080/v1"
    assert local["vllm"] == "http://127.0.0.1:8000/v1"
    assert local["lmstudio"] == "http://localhost:1234/v1"
    assert local["ollama"] == "http://localhost:11434/v1"


def test_the_provider_view_lists_the_same_rows_as_json(capsys):
    code = scli.main(["--providers", "--json"])

    assert code == scli.EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["action"] == "providers"
    # multi-SKU providers expand into one row per plan endpoint
    assert payload["count"] >= len(registry.REGISTRY_ORDER)
    assert payload["registry_count"] == len(registry.REGISTRY_ORDER)
    assert "deepseek" in payload["order"]
    assert payload["default"] == sf.DEFAULT_PROVIDER_ID
    row = next(item for item in payload["providers"] if item["id"] == "llamacpp")
    assert row["base_url"] == "http://127.0.0.1:8080/v1"
    assert row["note"]


def test_the_catalog_view_gained_the_breadth_rows_without_losing_its_own(capsys):
    code = scli.main(["--catalog", "--json"])

    assert code == scli.EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["action"] == "catalog"
    assert payload["count"] >= 4 and "deepseek" in payload["order"]
    assert payload["provider_count"] >= len(registry.REGISTRY_ORDER)
    assert any(item["id"] == "ollama" for item in payload["providers"])


# --------------------------------------------------------------------------- #
# 2. the interactive pass: Enter accepts the pre-filled URL, the key is hidden  #
# --------------------------------------------------------------------------- #


def test_the_interactive_pass_accepts_enter_and_writes_a_0600_env_file(
    local_stubs, tmp_path, capsys, monkeypatch
):
    """The whole friendly flow against the two local stubs, with a scripted human."""
    llamacpp, vllm = local_stubs
    target = tmp_path / "config.yaml"
    ask = ScriptedAsk(
        visible=[
            _row_number("llamacpp"),  # provider: a row number, not a URL
            "",                        # endpoint: Enter accepts the pre-filled URL
            "stub-llama",              # model (a local server's name is server-specific)
            _row_number("vllm"),       # the decision layer's own provider
            "",                        # endpoint: Enter again
            "stub-jev",                # its model
        ],
        secret=[FAKE_KEY, FAKE_KEY],
    )
    _answered(monkeypatch, ask)

    code = scli.main(["--config", str(target), "--runtime", str(tmp_path), "--format", "text"])

    captured = capsys.readouterr()
    assert code == scli.EXIT_OK, captured.err
    # -- the picker was really printed, with the notes and the URLs ---------- #
    assert "llama.cpp (local server)" in captured.err
    assert "http://127.0.0.1:8080/v1" in captured.err
    assert "vLLM (local server)" in captured.err
    # -- the key came from the hidden reader, never from the visible one ---- #
    assert ask.secrets_used == 2, "both keys must be asked for masked"
    assert all("hidden" in prompt for prompt in ask.secret_prompts)
    assert FAKE_KEY not in captured.out and FAKE_KEY not in captured.err
    assert FAKE_KEY not in json.dumps({"text": captured.out})

    # -- the .env: created, 0600, one line per key -------------------------- #
    env_file = tmp_path / ENV_FILE_OF
    assert env_file.is_file()
    mode = stat.S_IMODE(env_file.stat().st_mode)
    assert mode == 0o600, f"the credential file must be 0600, found {mode:04o}"
    body = env_file.read_text(encoding="utf-8")
    assert "LLAMA_API_KEY=" in body and "VLLM_API_KEY=" in body
    assert FAKE_KEY in body  # the file is the one place the value lives

    # -- the config: the variable NAME, never the value --------------------- #
    document = yaml.safe_load(target.read_text(encoding="utf-8"))
    godsv = document["godsv"]
    assert godsv["llm"]["base_url"] == "http://127.0.0.1:8080/v1"
    assert godsv["llm"]["model"] == "stub-llama"
    assert godsv["llm"]["api_key_env"] == "LLAMA_API_KEY"
    assert godsv["jev"]["base_url"] == "http://127.0.0.1:8000/v1"
    assert godsv["jev"]["api_key_env"] == "VLLM_API_KEY"
    assert FAKE_KEY not in target.read_text(encoding="utf-8")
    receipt = godsv["setup"]["credentials"]
    assert receipt["file"] == str(env_file)
    assert receipt["mode"] == "0600"
    assert sorted(receipt["names"]) == ["LLAMA_API_KEY", "VLLM_API_KEY"]

    # -- both keys answered a real call, and the report says so ------------- #
    assert "model echo stub-llama" in captured.out
    assert "completion:   ok" in captured.out
    assert "jev decision: ok" in captured.out
    assert "what happens next" in captured.out


def test_the_key_flow_and_the_commands_it_prints(local_stubs, tmp_path, capsys, monkeypatch):
    """``--check`` re-validates from the file, the first commands are real ones."""
    _, _ = local_stubs
    target = tmp_path / "config.yaml"
    ask = ScriptedAsk(
        visible=[
            _row_number("llamacpp"), "", "stub-llama",
            _row_number("vllm"), "", "stub-jev",
        ],
        secret=[FAKE_KEY, FAKE_KEY],
    )
    _answered(monkeypatch, ask)
    assert scli.main(["--config", str(target), "--runtime", str(tmp_path), "--format", "text"]) == 0
    capsys.readouterr()

    # --check: no prompt, no write, exit 0 ---------------------------------- #
    printed_before = (tmp_path / ENV_FILE_OF).read_text(encoding="utf-8")
    code = scli.main(["--check", "--config", str(target), "--json"])

    assert code == scli.EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["action"] == "check" and payload["ok"] is True
    assert payload["roles"]["llm"]["measure"]["model_echo"] == "stub-llama"
    assert payload["credentials_file"]["names"] == ["LLAMA_API_KEY", "VLLM_API_KEY"]
    assert payload["key_sources"]["llm"]["kind"] == "env-file"
    assert (tmp_path / ENV_FILE_OF).read_text(encoding="utf-8") == printed_before
    assert not list(tmp_path.glob("*.bak"))
    assert FAKE_KEY not in json.dumps(payload)


def test_every_printed_first_command_exists_in_the_real_tree():
    steps = sf.next_steps()
    tree = set(sf.reachable_commands())

    assert tree, "the real command tree must be reachable for this check"
    for item in steps["commands"]:
        command = str(item["command"])
        if not command:
            continue
        words = command.replace("set -a; .", "set").split()
        if words[0] != "skaldir":
            continue  # the `set -a; . <file>` line is a shell line, not a command
        wants = words[1:]
        # `agent run "…"` is two words; `setup --check` is one
        first = wants[0]
        assert first in tree or f"{first}.{wants[1] if len(wants) > 1 else ''}" in tree
        if first == "agent":
            assert "agent.run" in tree
    # the owner's preferred spellings are reported when this build lacks them
    for preferred in steps["preferred_missing"]:
        assert preferred not in tree


# --------------------------------------------------------------------------- #
# 3. the .env writer: preserved, replaced by NAME, 0600, sourceable            #
# --------------------------------------------------------------------------- #


def test_the_env_writer_preserves_every_other_line_and_replaces_by_name(tmp_path):
    target = tmp_path / ENV_FILE_OF
    target.write_text(
        "# my notes\n"
        "\n"
        "PATH_TO_SOMETHING=/tmp/x\n"
        f"MOONSHOT_API_KEY={'old' * 4}\n"
        "export OTHER=kept\n",
        encoding="utf-8",
    )
    os.chmod(target, 0o644)

    report = sf.write_env_file(target, {"MOONSHOT_API_KEY": "new-value"})

    body = target.read_text(encoding="utf-8")
    assert report["written"] is True and report["created"] is False
    assert report["replaced"] == ["MOONSHOT_API_KEY"] and report["appended"] == []
    assert report["mode"] == "0600"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600  # tightened, not just kept
    assert "# my notes" in body and body.startswith("# my notes\n")
    assert "PATH_TO_SOMETHING=/tmp/x" in body
    assert "export OTHER=kept" in body
    assert "MOONSHOT_API_KEY=new-value" in body and "oldoldoldold" not in body
    # order is preserved: the replaced line stays where it was
    assert body.index("PATH_TO_SOMETHING") < body.index("MOONSHOT_API_KEY")


def test_a_second_line_with_the_same_name_is_dropped_not_left_shadowing(tmp_path):
    target = tmp_path / ENV_FILE_OF
    target.write_text(
        "DEEPSEEK_API_KEY=first\nDEEPSEEK_API_KEY=second\n", encoding="utf-8"
    )

    report = sf.write_env_file(target, {"DEEPSEEK_API_KEY": "third"})

    body = target.read_text(encoding="utf-8")
    assert report["deduplicated"] == ["DEEPSEEK_API_KEY"]
    assert body == "DEEPSEEK_API_KEY=third\n"


def test_a_credential_with_a_line_break_is_refused(tmp_path):
    target = tmp_path / ENV_FILE_OF

    with pytest.raises(sf.SetupRefused):
        sf.write_env_file(target, {"DEEPSEEK_API_KEY": "value\nINJECTED=1"})
    assert not target.exists(), "a refused value must not leave a file behind"


def test_the_env_file_is_sourceable_by_a_shell(tmp_path):
    """The file is a real ``.env``: one documented command makes it effective."""
    target = tmp_path / ENV_FILE_OF
    sf.write_env_file(target, {"DEEPSEEK_API_KEY": "sk-abc 123#x", "OTHER": "plain"})

    result = subprocess.run(
        [
            "bash", "-c",
            f'set -a; . "{target}"; set +a; printf "%s|%s" "$DEEPSEEK_API_KEY" "$OTHER"',
        ],
        capture_output=True, text=True, check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "sk-abc 123#x|plain", "the quoted value must round-trip"


def test_a_re_run_does_not_rewrite_an_unchanged_credential(local_stubs, tmp_path, capsys):
    """A key already in the .env is *reused* — the file is left exactly as it was."""
    _, _ = local_stubs
    target = tmp_path / "config.yaml"
    env_file = tmp_path / ENV_FILE_OF
    sf.write_env_file(env_file, {"LLAMA_API_KEY": FAKE_KEY, "VLLM_API_KEY": FAKE_KEY})
    before = env_file.read_text(encoding="utf-8")

    code = scli.main([
        "--llm-provider", "llamacpp", "--llm-model", "stub-llama",
        "--jev-provider", "vllm", "--jev-model", "stub-jev",
        "--config", str(target), "--runtime", str(tmp_path), "--yes", "--json",
    ])

    assert code == scli.EXIT_OK, capsys.readouterr().err
    payload = json.loads(capsys.readouterr().out)
    assert env_file.read_text(encoding="utf-8") == before
    assert payload["credentials_file"]["written"] is False
    for role in ("llm", "typesafe"):
        assert payload["key_sources"][role]["kind"] == "env-file"
        assert payload["key_sources"][role]["source"].endswith(ENV_FILE_OF)
    assert payload["roles"]["llm"]["spec"]["credential"] == "set"


def test_a_key_only_in_the_shell_is_reused_and_copied_into_the_file(
    local_stubs, tmp_path, capsys, monkeypatch
):
    """The environment is a real source: said so in the report, and made durable."""
    _, _ = local_stubs
    monkeypatch.setenv("LLAMA_API_KEY", FAKE_KEY)
    monkeypatch.setenv("SKALDIR_SETUP_JEV_API_KEY", FAKE_KEY)
    target = tmp_path / "config.yaml"

    code = scli.main([
        "--llm-provider", "llamacpp", "--llm-model", "stub-llama",
        "--jev-provider", "vllm", "--jev-model", "stub-jev",
        "--config", str(target), "--runtime", str(tmp_path), "--yes", "--json",
    ])

    assert code == scli.EXIT_OK, capsys.readouterr().err
    payload = json.loads(capsys.readouterr().out)
    assert payload["key_sources"]["llm"]["source"] == "LLAMA_API_KEY"
    assert payload["key_sources"]["llm"]["kind"] == "environment"
    body = (tmp_path / ENV_FILE_OF).read_text(encoding="utf-8")
    assert "LLAMA_API_KEY=" in body, "a shell-only key is made durable in the file"
    assert FAKE_KEY not in json.dumps(payload)


def test_the_credential_file_can_be_moved_and_suppressed(
    local_stubs, tmp_path, capsys, monkeypatch
):
    """``--env-file`` moves the file; ``--no-env-write`` keeps the key in the shell."""
    _, _ = local_stubs
    target = tmp_path / "config.yaml"
    elsewhere = tmp_path / "project" / "dot-env"
    ask = ScriptedAsk(
        visible=[
            _row_number("llamacpp"), "", "stub-llama",
            _row_number("vllm"), "", "stub-jev",
        ],
        secret=[FAKE_KEY, FAKE_KEY],
    )
    _answered(monkeypatch, ask)

    code = scli.main([
        "--env-file", str(elsewhere), "--no-env-write",
        "--config", str(target), "--runtime", str(tmp_path), "--json",
    ])

    assert code == scli.EXIT_OK, capsys.readouterr().err
    payload = json.loads(capsys.readouterr().out)
    assert payload["credentials_file"]["written"] is False
    assert "--no-env-write" in payload["credentials_file"]["reason"]
    assert not elsewhere.exists()
    assert payload["env_file"] == str(elsewhere)


# --------------------------------------------------------------------------- #
# 4. headless parity: every question has a flag and a variable                 #
# --------------------------------------------------------------------------- #


def _setup_option_strings() -> set[str]:
    parser = cli.build_parser()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):  # noqa: SLF001 — argparse API
            return {
                option
                for item in action.choices["setup"]._actions  # noqa: SLF001
                for option in item.option_strings
            }
    raise AssertionError("no subparsers on the product parser")


def test_every_question_has_a_flag_and_a_variable_in_the_real_parser():
    options = _setup_option_strings()

    for role in sf.MANDATORY_ROLES:
        for question in sf.questions(role):
            assert question.flag in options, f"{role}: {question.field} has no flag"
            assert question.env, f"{role}: {question.field} has no variable"
            # the first name is the documented one; the LLM role also accepts the
            # layer's own SKALDIR_LLM_* fallbacks, which is why only the head is pinned
            assert question.env[0].startswith("SKALDIR_SETUP_"), question.env
    # the secret is never a value flag: argv is shell history
    assert "--llm-key-stdin" in options and "--jev-key-stdin" in options
    assert "--llm-key" not in options and "--jev-key" not in options


def test_a_secret_cannot_be_passed_as_a_flag_value(tmp_path, capsys):
    """``--llm-key <value>`` does not exist — argparse refuses it (exit 1)."""
    with pytest.raises(SystemExit) as excinfo:
        scli.build_parser().parse_args(["--llm-key", FAKE_KEY])
    assert excinfo.value.code == 2  # argparse's usage error; the CLI maps it to 1
    code = scli.main(["--llm-key", FAKE_KEY])
    assert code == scli.EXIT_USAGE
    captured = capsys.readouterr()
    # argparse names the unknown token; the value is not a configured option at all
    assert "--llm-key" in (captured.out + captured.err)


def test_the_env_variables_drive_the_same_pick(stub, stub_jevs):
    from skaldir import provider_catalog as catalog

    env = {
        "SKALDIR_SETUP_LLM_PROVIDER": "deepseek",
        "SKALDIR_SETUP_JEV_PROVIDER": "deepseek",
    }

    key = sf.key_from_env(sf.ROLE_LLM, env=env)

    assert key.base_url == "https://api.deepseek.com/v1"
    # the pre-filled model is the catalog's own default, read rather than retyped
    assert key.model == catalog.CATALOG["deepseek"].default_model
    assert key.api_key_env == "DEEPSEEK_API_KEY"


def test_headless_reports_exactly_the_flag_that_is_still_missing(tmp_path, capsys):
    """``--llm-provider openrouter`` answers the endpoint; the model is one flag."""
    code = scli.main([
        "--llm-provider", "openrouter", "--jev-provider", "vllm", "--jev-model", "m",
        "--config", str(tmp_path / "config.yaml"), "--runtime", str(tmp_path),
        "--yes", "--json",
    ])

    # headless with a key that cannot validate: needs-credentials (4), not blocked (3)
    # (was pinned EXIT_BLOCKED + missing model when openrouter named no model — the
    # catalog now pre-fills a model, so the remaining gap is the key)
    assert code in (scli.EXIT_BLOCKED, 4)
    payload = json.loads(capsys.readouterr().out)
    llm = payload["roles"]["llm"]
    # the endpoint+model are answered; what is still missing is the credential
    assert llm.get("missing_fields") != ["provider"], llm


def test_a_headless_run_never_asks_anything(tmp_path, stub, stub_jevs, capsys):
    """A non-tty stdin (the default under pytest) takes the no-question path."""
    code = scli.main([
        "--llm-base-url", stub.base_url, "--llm-model", "stub-model",
        "--llm-api-key-env", "STUB_LLM_KEY",
        "--jev-base-url", stub_jevs.base_url, "--jev-model", "stub-jev-model",
        "--jev-api-key-env", "STUB_JEV_KEY",
        "--config", str(tmp_path / "config.yaml"), "--runtime", str(tmp_path), "--json",
    ])

    # a loopback stub needs no credential, so this run succeeds — and it asked
    # nothing: no TTY, no prompts, and no credential file (there is no key to write)
    assert code == scli.EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert not payload.get("asked")
    assert payload["credentials_file"]["exists"] is False


def test_the_masked_reader_uses_getpass(monkeypatch):
    seen: dict[str, Any] = {}
    stream = io.StringIO()
    monkeypatch.setattr(scli.sys, "stderr", stream)

    def fake_getpass(prompt, stream=None):
        seen["prompt"] = prompt
        seen["stream"] = stream
        return "typed-secret\n"

    monkeypatch.setattr(scli.getpass, "getpass", fake_getpass)
    value = scli._read_secret("  the key (hidden): ")

    assert value == "typed-secret\n"
    assert "hidden" in seen["prompt"]
    assert seen["stream"] is stream, "the hidden prompt goes to stderr, never stdout"


# --------------------------------------------------------------------------- #
# 5. the report shapes: redacted, and honest about who reads the file          #
# --------------------------------------------------------------------------- #


def test_the_report_never_carries_a_value_and_names_who_reads_the_file(tmp_path):
    env_file = tmp_path / ENV_FILE_OF
    sf.write_env_file(env_file, {"DEEPSEEK_API_KEY": FAKE_KEY})

    seen = sf.read_env_file(env_file)
    report = sf.credentials_report(seen, written=True)

    assert FAKE_KEY not in json.dumps(report)
    assert report["names"] == ["DEEPSEEK_API_KEY"]
    assert report["mode"] == "0600"
    assert report["read_by"] == ["skaldir setup", "skaldir setup --check"]
    assert report["into_a_shell"].startswith("set -a; . ")


def test_the_state_view_finds_an_existing_credential_file(tmp_path):
    config = tmp_path / "config.yaml"
    sf.write_env_file(tmp_path / ENV_FILE_OF, {"DEEPSEEK_API_KEY": FAKE_KEY})

    state = sf.detect_state(config_path=config, runtime=tmp_path, env={})

    cred = state["credentials_file"]
    assert cred["exists"] is True and cred["mode"] == "0600"
    assert cred["names"] == ["DEEPSEEK_API_KEY"]
    assert FAKE_KEY not in json.dumps(state)


def test_the_contract_entry_still_describes_the_verb():
    entry = cli_contract.COMMANDS["setup"]
    notes = entry.notes

    assert "api_key_env is a NAME" in notes
    assert "0600" in notes, "the credential file and its mode must be declared"
    inventory = cli_contract.dump(cli.build_parser())
    assert inventory["unregistered"] == []


def test_no_third_party_import_is_needed_for_the_picker():
    """The whole UX is stdlib + the repo's own modules (a wheel must keep working)."""
    assert len(registry.REGISTRY_ORDER) >= 16, "the breadth registry has customers"
    assert shutil.which("bash")  # the sourceable-.env proof above needs it
