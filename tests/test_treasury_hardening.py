"""Treasury-Re-Audit (Release-Härtung, 12.09.): Invarianten, die Tests beweisen.

Abgedeckt: Betrags-Validierung (NaN/inf/<=0), Intent-Pflicht für Auto-Approve,
Datei-Permissions (0600), Scrub-Varianten (URL-encoded/strip), Purpose-Redaktion,
Sandbox-Kind erbt nie Parent-Env (Key-Canary), TOTP respektiert `grant:required`.
"""

import ast
import inspect
import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

import pytest

from skaldir import treasury
from skaldir.events import EventStore
from skaldir.treasury import (
    REDACTED,
    TIER_WHITELIST,
    Broker,
    SpendIntent,
    SpendLedger,
    SpendRules,
    StatePathError,
    TreasuryState,
    Vault,
)

MASTER = "audit-master-123"
SECRET = "tok_" + "Z9y8X7w6V5u4T3s2R1q0P9o8N7m6"
TOTP_SEED = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"


@pytest.fixture
def vault(tmp_path):
    return Vault(tmp_path / "vault.json", MASTER)


@pytest.fixture
def state(tmp_path):
    handle = TreasuryState(tmp_path / "treasury.db")
    yield handle
    handle.close()


def _ledger(state, tmp_path, rules=None):
    events = EventStore(tmp_path / "events.db")
    return SpendLedger(state, rules, events=events), events


class TestSpendHardening:
    def test_invalid_amounts_denied(self, state, tmp_path):
        ledger, _events = _ledger(state, tmp_path)
        for bad in (0.0, -5.0, float("nan"), float("inf")):
            request = ledger.request(amount=bad, merchant="m", purpose="p")
            assert request.status == "denied", repr(bad)
            assert "amount" in request.reason
        assert state.open_spends() == []

    def test_auto_approve_requires_verifiable_intent(self, state, tmp_path):
        rules = SpendRules(tier=TIER_WHITELIST, per_tx_cap=5.0, whitelist=["hosting-x"])
        ledger, _events = _ledger(state, tmp_path, rules)
        without = ledger.request(amount=4.0, merchant="hosting-x", purpose="x")
        assert without.status == "pending_human" and "intent" in without.reason
        intent = SpendIntent(amount=4.0, currency="EUR", merchant="hosting-x", purpose="x")
        with_intent = ledger.request(
            amount=4.0, merchant="hosting-x", purpose="x", intent=intent
        )
        assert with_intent.status == "approved" and with_intent.approved_by == "policy"


class TestVaultHardening:
    def test_vault_file_permissions(self, vault, tmp_path):
        vault.add(service="s", name="n", secret=SECRET)
        mode = stat.S_IMODE(os.stat(tmp_path / "vault.json").st_mode)
        assert mode == 0o600

    def test_state_file_permissions(self, state, tmp_path):
        mode = stat.S_IMODE(os.stat(tmp_path / "treasury.db").st_mode)
        assert mode == 0o600

    def test_scrub_variants(self, vault, state):
        broker = Broker(vault, state)
        url_form = quote(SECRET, safe="")
        text = f"a {SECRET} b {url_form} c {SECRET}  d"
        scrubbed, replaced = broker._scrub(text, SECRET)
        assert replaced is True
        assert SECRET not in scrubbed and url_form not in scrubbed
        assert REDACTED in scrubbed

    def test_short_secret_scrubs_exactly(self, vault, state):
        broker = Broker(vault, state)
        scrubbed, replaced = broker._scrub("xx ab yy", "ab")
        assert replaced is True and scrubbed == f"xx {REDACTED} yy"

    def test_purpose_with_secret_never_reaches_events(self, vault, state, tmp_path):
        handle = vault.add(service="s", name="n", secret=SECRET)
        events = EventStore(tmp_path / "events.db")
        broker = Broker(vault, state, events=events)
        result = broker.run_with_secret(
            handle,
            argv=["python3", "-c", "print('done')"],
            env_var="TOK",
            purpose=f"use {SECRET} now",
            workdir=tmp_path,
        )
        assert result.ok
        dump = json.dumps(
            [event.payload for event in events.iter_session("treasury")], ensure_ascii=False
        )
        assert SECRET not in dump


class TestBrokerIsolation:
    def test_sandbox_child_never_inherits_parent_env(self, vault, state, tmp_path, monkeypatch):
        monkeypatch.setenv("GODSV_VAULT_KEY", "leak-canary-master")
        handle = vault.add(service="s", name="n", secret=SECRET)
        broker = Broker(vault, state)
        result = broker.run_with_secret(
            handle,
            argv=["sh", "-c", "env"],
            env_var="TOK",
            purpose="env-dump",
            workdir=tmp_path,
        )
        assert result.ok
        assert "leak-canary-master" not in result.stdout
        assert "GODSV_VAULT_KEY" not in result.stdout
        # the injected variable arrives (name visible, value scrubbed)
        assert "TOK=" in result.stdout


class TestGrantHardening:
    def test_totp_requires_grant_when_flagged(self, vault, state):
        handle = vault.add(
            service="bank",
            name="totp",
            secret="",
            scopes=["totp", "grant:required"],
            totp_seed=TOTP_SEED,
        )
        broker = Broker(vault, state)
        assert broker.totp_code(handle, purpose="login", at_time=59) is None
        grant = broker.request_grant(handle, purpose="login")
        assert broker.approve_grant(grant) is True
        assert broker.totp_code(handle, purpose="login", at_time=59) is not None

    def test_totp_without_flag_needs_no_grant(self, vault, state):
        handle = vault.add(
            service="mail", name="totp2", secret="", scopes=["totp"], totp_seed=TOTP_SEED
        )
        broker = Broker(vault, state)
        assert broker.totp_code(handle, purpose="login", at_time=59) is not None


# --------------------------------------------------------------------------- #
# the empty-path chmod class (measured defect: `spend allowance --db ""`)
# --------------------------------------------------------------------------- #
#
# `Path("") == Path(".")`: a blank path argument used to reach `_restrict(Path(""), 0o600)`, which
# chmod'ed the CURRENT WORKING DIRECTORY to 0600 (no execute bit) while the envelope still answered
# `ok: true`. Every later process started in that directory died with EACCES. The invariants below
# pin the three properties of the fix: the exact repro leaves the cwd alone and exits non-zero; no
# code path chmods a directory the caller did not name; the intended restrictions still work.

ROOT = Path(__file__).resolve().parents[1]
MASTER_KEY = "chmod-class-master-key"
#: The directory-shaped path arguments that must never name a state file.
BAD_PATH_ARGUMENTS = ("", ".", "./", "..", "../", "   ")


def _skaldir_cli() -> list[str]:
    """The console script when it is installed, else the identical ``python -m skaldir`` entry."""
    script = Path(sys.executable).parent / "skaldir"
    return [str(script)] if script.is_file() else [sys.executable, "-m", "skaldir"]


def _run_cli(cwd: Path, *argv: str) -> subprocess.CompletedProcess:
    environment = {**os.environ, "GODSV_HOME": str(cwd), "HERMES_HOME": str(cwd),
                   "GODSV_VAULT_KEY": MASTER_KEY, "NO_COLOR": "1",
                   # …this checkout, not whatever a concurrent `pip install -e` in the shared
                   # venv currently resolves `godsv` to: the test is about THIS tree's code.
                   "PYTHONPATH": str(ROOT / "src")}
    environment.pop("GODSV_FORMAT", None)
    return subprocess.run([*_skaldir_cli(), *argv], cwd=str(cwd), env=environment,
                          capture_output=True, text=True, timeout=120)


def _modes(*paths: Path) -> tuple[int, ...]:
    return tuple(stat.S_IMODE(os.stat(path).st_mode) for path in paths)


@pytest.fixture
def probe(tmp_path):
    """A 0755 scratch directory: a cwd whose mode is observable and must not change."""
    sandbox = tmp_path / "probe"
    sandbox.mkdir()
    sandbox.chmod(0o755)
    return sandbox


class TestNoDirectoryIsChmodded:
    """The measured repro, at the CLI boundary where it happened."""

    def test_the_exact_repro_leaves_the_working_directory_unchanged(self, probe):
        """`spend allowance --db "" --json` — the 19-invocation finding, one invocation."""
        before = _modes(probe)

        proc = _run_cli(probe, "spend", "allowance", "--db", "", "--json")

        assert _modes(probe) == before, (
            f"the working directory went from {oct(before[0])} to {oct(_modes(probe)[0])}: a "
            f"command chmod'ed the directory it ran in"
        )
        assert os.access(probe, os.X_OK), "the working directory lost its execute bit"
        # …and a later process can really be started there (the consequence that was measured)
        assert subprocess.run(["sh", "-c", "true"], cwd=str(probe)).returncode == 0

        envelope = json.loads(proc.stdout)
        assert envelope["ok"] is False, "a refused path argument must not answer ok: true"
        assert proc.returncode != 0 and envelope["exit_code"] == 1, envelope
        assert envelope["error"]["code"] == "usage", envelope["error"]
        assert "--db" in envelope["error"]["message"], envelope["error"]

    def test_the_refusal_reaches_a_text_mode_user(self, probe):
        """Without --json the message is on stderr, because the exit code alone explains nothing."""
        proc = _run_cli(probe, "spend", "allowance", "--db", "")
        assert proc.returncode == 1
        assert "--db" in proc.stderr and "empty" in proc.stderr, proc.stderr

    @pytest.mark.parametrize("value", BAD_PATH_ARGUMENTS)
    def test_no_directory_valued_path_argument_creates_a_state_file(self, probe, value):
        before = _modes(probe)

        for command in (["spend", "allowance", "--db", value, "--json"],
                        ["vault", "list", "--vault", value, "--json"]):
            proc = _run_cli(probe, *command)
            assert proc.returncode == 1, (command, proc.stdout, proc.stderr)
            assert json.loads(proc.stdout)["ok"] is False, command
            assert _modes(probe) == before, (command, oct(_modes(probe)[0]))

    def test_a_directory_argument_is_refused_and_keeps_its_mode(self, probe, tmp_path):
        named = tmp_path / "a-directory"
        named.mkdir()
        named.chmod(0o755)
        before = _modes(named)

        proc = _run_cli(probe, "spend", "allowance", "--db", str(named), "--json")

        assert proc.returncode == 1 and json.loads(proc.stdout)["ok"] is False
        assert _modes(named) == before, "a directory named as a database lost its mode"

    def test_the_classes_refuse_a_directory_shaped_path(self, probe, monkeypatch):
        """Defence in depth for non-CLI callers (the plugin/panel builds the same classes)."""
        monkeypatch.chdir(probe)
        before = _modes(probe)
        for value in BAD_PATH_ARGUMENTS + (str(probe),):
            for build in (lambda raw: TreasuryState(raw),
                          lambda raw: Vault(raw, MASTER_KEY)):
                with pytest.raises(StatePathError) as caught:
                    build(value)
                assert caught.value.code == 1, (value, caught.value.code)
                assert caught.value.message.split(":")[0] in ("--db", "--vault"), (
                    value, caught.value.message
                )
        assert _modes(probe) == before


class TestOnlyNamedPathsAreTightened:
    """Invariant: the product restricts files it owns — never a directory the caller
    did not name."""

    def test_a_relative_vault_never_changes_the_working_tree(self, probe, monkeypatch):
        sub = probe / "sub"
        sub.mkdir()
        sub.chmod(0o755)
        before = _modes(probe, sub)
        monkeypatch.chdir(probe)

        vault = Vault(Path("sub") / "vault.json", MASTER_KEY)
        vault.add(service="s", name="n", secret=SECRET)

        assert _modes(probe, sub) == before, (
            "a relative --vault tightened a directory inside the caller's working tree"
        )
        assert stat.S_IMODE(os.stat(sub / "vault.json").st_mode) == 0o600

    def test_a_bare_relative_vault_name_never_changes_the_cwd(self, probe, monkeypatch):
        before = _modes(probe)
        monkeypatch.chdir(probe)

        Vault("vault.json", MASTER_KEY)

        assert _modes(probe) == before, "the working directory (. as parent) was tightened"

    def test_restrict_only_touches_files(self, probe):
        directory = probe / "d"
        directory.mkdir()
        directory.chmod(0o755)
        regular = probe / "f"
        regular.write_text("x", encoding="utf-8")
        regular.chmod(0o644)

        treasury._restrict(directory, 0o600)
        treasury._restrict(regular, 0o600)

        assert stat.S_IMODE(os.stat(directory).st_mode) == 0o755, "_restrict chmod'ed a directory"
        assert stat.S_IMODE(os.stat(regular).st_mode) == 0o600, "a real file lost its restriction"

    def test_restrict_dir_refuses_the_cwd_and_shared_directories(self, probe, monkeypatch):
        shared = probe / "shared"
        shared.mkdir()
        shared.chmod(0o1777)  # a /tmp-like directory: sticky + world-writable
        group = probe / "group"
        group.mkdir()
        group.chmod(0o775)
        monkeypatch.chdir(probe)
        before = _modes(probe, shared, group)

        treasury._restrict_dir(probe, 0o700)      # the working directory
        treasury._restrict_dir(shared, 0o700)     # world-writable + sticky
        treasury._restrict_dir(group, 0o700)      # group-writable

        assert _modes(probe, shared, group) == before, (
            "a directory that is not the product's own was tightened"
        )

    def test_the_guarded_helpers_keep_their_guards(self):
        """The AST-level shape the class depends on — a refactor must not drop a guard."""
        restrict = inspect.getsource(treasury._restrict)
        restrict_dir = inspect.getsource(treasury._restrict_dir)
        assert "os.chmod" in restrict and "is_file()" in restrict, (
            "_restrict must only ever chmod a file"
        )
        assert "Path.cwd()" in restrict_dir, "_restrict_dir must refuse the working directory"
        assert "S_ISVTX" in restrict_dir and "S_IWOTH" in restrict_dir, (
            "_restrict_dir must refuse shared directories"
        )

    def test_every_chmod_call_site_in_the_package_is_recorded(self):
        """A new chmod is a decision, not an accident: it must be added here with a reason."""
        recorded = {
            "src/skaldir/treasury.py": "only _restrict (file-only) and _restrict_dir (guarded)",
            "src/skaldir/settings_service.py": "a NamedTemporaryFile and a backup copy — files",
            "src/skaldir/toolbuilder.py": (
                "generated wrapper/entrypoint scripts — files just written"),
            "src/skaldir/edit_tx.py": (
                "the atomic-write temp file it just created in the target's own directory "
                "(os.open 0o600, then the pre-existing mode restored) — a file, never a directory"
            ),
            "src/skaldir/warm_session.py": (
                "the session's OWN socket directory under the runtime dir: owner-checked first, "
                "and only group/other-reachable bits are cleared (0700)"
            ),
            "src/skaldir/checkpoints.py": (
                "the atomic-write temp file it just created in the target's own directory "
                "(the pre-existing mode re-applied before os.replace) — a file, never a "
                "directory"
            ),
            "src/skaldir/setup_flow.py": (
                "write_env_file's own temp file, restricted through the descriptor it just "
                "opened with O_CREAT 0o600 (os.fchmod on that fd) — a regular file, never a "
                "directory the caller named, and the mode is set *before* os.replace installs "
                "the key file, so there is no write-then-chmod window"
            ),
        }
        sites = _chmod_call_sites()
        unrecorded = sorted(set(sites) - set(recorded))
        assert not unrecorded, (
            f"new chmod call site(s) {unrecorded}: decide whether they can touch a directory the "
            f"caller did not name (fix it in the file that owns it, then record it here)"
        )
        assert set(recorded) - set(sites) == set(), (
            "a recorded chmod site disappeared — drop it from this list"
        )

    def test_treasury_chmods_only_where_the_guards_are(self):
        """Inside treasury.py every chmod must sit in a function whose safety is argued."""
        owners = {name for name, _line in _chmod_call_sites()["src/skaldir/treasury.py"]}
        assert {"_restrict", "_restrict_dir"} <= owners, (
            f"the two guarded helpers are no longer the ones restricting: {sorted(owners)}"
        )
        unguarded = sorted(owners - set(TREASURY_CHMOD_FUNCTIONS))
        assert not unguarded, (
            f"treasury.py chmods outside the argued helpers {unguarded}: a directory the caller "
            f"did not name could lose its mode — guard it or remove it"
        )


#: Every function in ``treasury.py`` that may change a mode, and why that is safe.
TREASURY_CHMOD_FUNCTIONS = {
    "_restrict": "chmods a regular file only (is_file() guard)",
    "_restrict_dir": "chmods a directory we own (cwd + shared-directory guards, absolute only)",
    "_write": "fchmods the descriptor of the temp file it opened with O_CREAT 0o600",
}


def _chmod_call_sites() -> dict[str, list[tuple[str, int]]]:
    """Every ``chmod``/``fchmod`` call in ``src/skaldir`` as ``{file: [(function, line)]}``.

    ``fchmod`` is included: a file-descriptor chmod cannot reach a directory the caller named, but
    it is still a mode change and must stay visible to the inventory.
    """
    sites: dict[str, list[tuple[str, int]]] = {}
    for source_file in sorted((ROOT / "src/skaldir").rglob("*.py")):
        tree = ast.parse(source_file.read_text(encoding="utf-8"))
        parents: dict[ast.AST, ast.AST] = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parents[child] = node
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in ("chmod", "fchmod")):
                continue
            function = ""
            cursor: ast.AST = node
            while cursor in parents:
                cursor = parents[cursor]
                if isinstance(cursor, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    function = cursor.name
                    break
            relative = str(source_file.relative_to(ROOT))
            sites.setdefault(relative, []).append((function, node.lineno))
    return sites


class TestIntendedRestrictionsStillHold:
    """The fix must not weaken the real protection: files 0600, the vault's own directory 0700."""

    def test_the_vault_directory_and_its_files_are_still_restricted(self, probe):
        """An absolute path (the default runtime shape) still gets SECURITY.md T-A4."""
        target = probe / "state" / "vault.json"

        vault = Vault(target, MASTER_KEY)
        vault.add(service="s", name="n", secret=SECRET)

        assert stat.S_IMODE(os.stat(target).st_mode) == 0o600
        assert stat.S_IMODE(os.stat(target.parent).st_mode) == 0o700, (
            "the directory that holds the vault is no longer owner-only"
        )
        assert stat.S_IMODE(os.stat(probe).st_mode) == 0o755, "an unnamed parent was tightened"

    def test_lock_and_temp_files_are_still_restricted(self, probe):
        vault = Vault(probe / "state" / "vault.json", MASTER_KEY)
        vault.add(service="s", name="n", secret=SECRET)
        with vault._file_lock():
            for path in (vault._lock_path, vault.path):
                assert stat.S_IMODE(os.stat(path).st_mode) == 0o600, path

    def test_the_state_database_is_still_restricted(self, probe):
        state_dir = probe / "state"
        state_dir.mkdir()
        state_dir.chmod(0o755)
        db = state_dir / "treasury.db"

        handle = TreasuryState(db)
        try:
            assert db.is_file()
            assert stat.S_IMODE(os.stat(db).st_mode) == 0o600
        finally:
            handle.close()
        assert stat.S_IMODE(os.stat(probe).st_mode) == 0o755
        assert stat.S_IMODE(os.stat(state_dir).st_mode) == 0o755, (
            "the sqlite state file's directory was tightened — it is the caller's"
        )

    def test_shared_directory_files_stay_restricted(self, tmp_path):
        """A vault in /tmp-like storage keeps its 0600 file without touching the shared dir."""
        shared = tmp_path / "shared"
        shared.mkdir()
        shared.chmod(0o1777)
        target = shared / "vault.json"
        before = _modes(shared)

        vault = Vault(target, MASTER_KEY)
        vault.add(service="s", name="n", secret=SECRET)

        assert stat.S_IMODE(os.stat(target).st_mode) == 0o600
        assert _modes(shared) == before
