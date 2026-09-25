"""The security audit's own rules: one test per class, plus the audit's invariants.

The audit is only worth shipping if its rules fire on the defect they name and stay
quiet on the patterns that merely *look* like it. Each test below is written as the
smallest source text that carries (or does not carry) one defect, so a rule that
starts over-reporting fails here first — and the two "quiet" tests are the ones that
matter most, because an audit nobody trusts gets ignored.

The last group runs the audit over *this repository* and pins what it must not say:
no ``shell=True``, no ``os.system``, no ``eval``, no ``yaml.load`` without a safe
loader, no world-writable chmod — and no secret value in the report.
"""

from __future__ import annotations

import json
import shutil
import stat
import tempfile
from pathlib import Path

import pytest

from skaldir import security_audit as A

REPO = Path(A.__file__).resolve().parents[2]


def rules_for(text: str) -> set[str]:
    """The rule ids a snippet triggers."""
    return {finding.rule for finding in A.audit_source(text, "snippet.py")}


def rule_of(text: str, rule: str) -> A.Finding:
    found = [finding for finding in A.audit_source(text, "snippet.py") if finding.rule == rule]
    assert found, f"{rule} did not fire on:\n{text}"
    return found[0]


# --------------------------------------------------------------------------- #
# the catalogue                                                                 #
# --------------------------------------------------------------------------- #


def test_every_rule_is_complete_and_unique() -> None:
    ids = [rule.id for rule in A.RULES]
    assert len(ids) == len(set(ids)), "duplicate rule id"
    for rule in A.RULES:
        assert rule.klass in A.CLASSES, rule.id
        assert rule.severity in A.SEVERITIES, rule.id
        assert rule.fix.strip(), f"{rule.id} carries no fix"
        assert rule.title.strip(), rule.id
    for name in A.CLASSES:
        assert [r for r in A.RULES if r.klass == name], f"class {name} has no rule"
        assert A.CLASS_HELP[name]


def test_rules_payload_covers_every_class() -> None:
    payload = A.rules_payload()
    assert {item["class"] for item in payload["classes"]} == set(A.CLASSES)
    assert len(payload["rules"]) == len(A.RULES)
    for item in payload["rules"]:
        assert item["fix"] and item["severity"] in A.SEVERITIES


# --------------------------------------------------------------------------- #
# command_injection                                                             #
# --------------------------------------------------------------------------- #


def test_shell_true_and_os_system_are_command_injection() -> None:
    assert "SHELL-TRUE" in rules_for(
        "import subprocess\nsubprocess.run('ls ' + name, shell=True)\n"
    )
    assert "OS-SYSTEM" in rules_for("import os\nos.system('rm -rf ' + target)\n")


def test_interpolated_sh_dash_c_is_command_injection() -> None:
    """The defect the ACT layer had: a value spliced into a shell script."""
    snippet = (
        "def focus(payload):\n"
        "    title = str(payload.get('title', ''))\n"
        "    return ['sh', '-c', f\"xdotool search --name '{title}' | head -1\"]\n"
    )
    finding = rule_of(snippet, "SHELL-C-INTERPOLATION")
    assert finding.severity == "critical"
    assert finding.line == 3


def test_quoted_shell_script_is_not_reported() -> None:
    """The fix: the value is quoted, so the shell sees data and not syntax."""
    snippet = (
        "import shlex\n"
        "def focus(payload):\n"
        "    pattern = shlex.quote(str(payload.get('title', '')))\n"
        "    return ['sh', '-c', f'xdotool search --name {pattern} | head -1']\n"
    )
    assert "SHELL-C-INTERPOLATION" not in rules_for(snippet)


def test_an_unrelated_run_call_is_not_a_subprocess_finding() -> None:
    """``executor.run(...)`` is not ``subprocess.run`` — the rule must not fire."""
    assert rules_for("def f(executor):\n    return executor.run('demo', [], runner)\n") == set()
    assert "SUBPROCESS-NO-TIMEOUT" in rules_for(
        "import subprocess\nsubprocess.run(['ls'])\n"
    )


# --------------------------------------------------------------------------- #
# path_traversal                                                                #
# --------------------------------------------------------------------------- #


def test_unguarded_name_join_is_reported() -> None:
    snippet = (
        "from pathlib import Path\n"
        "def manifest_path(root, name):\n"
        "    return Path(root) / 'generated_tools' / name / 'manifest.yaml'\n"
    )
    assert "PATH-JOIN-UNTRUSTED" in rules_for(snippet)


def test_guarded_name_join_is_not_reported() -> None:
    guarded = (
        "import re\n"
        "from pathlib import Path\n"
        "NAME = re.compile(r'^[A-Za-z0-9_.-]{1,64}$')\n"
        "def manifest_path(root, name):\n"
        "    if not NAME.fullmatch(name):\n"
        "        raise ValueError(name)\n"
        "    return Path(root) / 'generated_tools' / name / 'manifest.yaml'\n"
    )
    assert "PATH-JOIN-UNTRUSTED" not in rules_for(guarded)

    contained = (
        "from pathlib import Path\n"
        "def manifest_path(root, name):\n"
        "    base = (Path(root) / 'generated_tools')\n"
        "    target = (base / name).resolve()\n"
        "    if base not in target.parents:\n"
        "        raise ValueError(name)\n"
        "    return target / 'manifest.yaml'\n"
    )
    assert "PATH-JOIN-UNTRUSTED" not in rules_for(contained)


def test_a_literal_path_with_dotdot_is_reported_and_a_regex_is_not() -> None:
    assert "PATH-DOTDOT-LITERAL" in rules_for("p = '../../etc/passwd'\n")
    regex_not_a_path = "import re\nPAT = re.compile(r'(^|/)\\.\\.(/|$)')\n"
    assert "PATH-DOTDOT-LITERAL" not in rules_for(regex_not_a_path)
    assert "PATH-DOTDOT-LITERAL" not in rules_for("if part == '..':\n    depth -= 1\n")


# --------------------------------------------------------------------------- #
# secrets                                                                       #
# --------------------------------------------------------------------------- #


def test_secret_shaped_literal_is_reported_and_masked() -> None:
    secret = "ghp_" + "a" * 36
    finding = rule_of(f'TOKEN = "{secret}"\n', "SECRET-LITERAL")
    assert secret not in finding.as_dict()["evidence"]
    assert "36" in finding.evidence or "chars" in finding.evidence


def test_whole_environment_inheritance_is_reported() -> None:
    assert "WHOLE-ENVIRON" in rules_for(
        "import subprocess, os\nsubprocess.run(['x'], env=os.environ)\n"
    )
    assert "ENVIRON-COPY" in rules_for("import os\nenv = dict(os.environ)\n")
    assert "ENVIRON-COPY" in rules_for("import os\nenv = os.environ.copy()\n")


def test_secret_interpolation_is_reported_at_a_message_sink_only() -> None:
    leak = "print(f'using key {api_key}')\n"
    assert "SECRET-INTERPOLATION" in rules_for(leak)
    header = "headers['Authorization'] = f'Bearer {self._cfg.api_key}'\n"
    assert "SECRET-INTERPOLATION" not in rules_for(header)
    redacted = "print(f'key {\"set\" if api_key else \"unset\"}')\n"
    assert "SECRET-INTERPOLATION" not in rules_for(redacted)
    ambiguous = "report.append(f'argv token {token!r} uses $input.x')\n"
    assert "SECRET-INTERPOLATION" not in rules_for(ambiguous)


# --------------------------------------------------------------------------- #
# unsafe_deserialisation                                                        #
# --------------------------------------------------------------------------- #


def test_unsafe_loaders_are_reported() -> None:
    assert "YAML-UNSAFE-LOAD" in rules_for("import yaml\nyaml.load(text)\n")
    assert "YAML-UNSAFE-LOAD" in rules_for("import yaml\nyaml.load(text, Loader=yaml.FullLoader)\n")
    assert "PICKLE" in rules_for("import pickle\npickle.loads(blob)\n")
    assert "EVAL-EXEC" in rules_for("eval(source)\n")
    assert "DYNAMIC-IMPORT" in rules_for("import importlib\nimportlib.import_module(name)\n")


def test_safe_loaders_are_not_reported() -> None:
    assert "YAML-UNSAFE-LOAD" not in rules_for("import yaml\nyaml.safe_load(text)\n")
    assert "EVAL-EXEC" not in rules_for("import ast\nast.literal_eval(text)\n")
    literal_import = "import importlib\nimportlib.import_module('godsv.tools')\n"
    assert "DYNAMIC-IMPORT" not in rules_for(literal_import)


# --------------------------------------------------------------------------- #
# permissions                                                                   #
# --------------------------------------------------------------------------- #


def test_world_writable_and_write_then_chmod_are_reported() -> None:
    assert "WORLD-WRITABLE" in rules_for("import os\nos.chmod(path, 0o777)\n")
    world_writable = "import os, stat\nos.chmod(path, stat.S_IWOTH)\n"
    assert "WORLD-WRITABLE" in rules_for(world_writable)
    window = (
        "import os\n"
        "def save(path, data):\n"
        "    path.write_text(data)\n"
        "    os.chmod(path, 0o600)\n"
    )
    assert "WRITE-THEN-CHMOD" in rules_for(window)
    safe = (
        "import os\n"
        "def save(path, data):\n"
        "    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)\n"
        "    with os.fdopen(fd, 'wb') as handle:\n"
        "        handle.write(data)\n"
    )
    assert "WRITE-THEN-CHMOD" not in rules_for(safe)


def test_privileged_container_and_socket_mount_are_reported() -> None:
    assert "PRIVILEGED-CONTAINER" in rules_for(
        "import subprocess\nsubprocess.run(['docker', 'run', '--privileged', image])\n"
    )
    assert "DOCKER-SOCKET" in rules_for(
        "import subprocess\n"
        "subprocess.run(['docker', 'run', '-v', '/var/run/docker.sock:/var/run/docker.sock', i])\n"
    )


# --------------------------------------------------------------------------- #
# toctou                                                                        #
# --------------------------------------------------------------------------- #


def test_check_then_write_is_reported_for_a_write_and_not_for_a_read() -> None:
    write = (
        "from pathlib import Path\n"
        "def save(path: Path, data: str):\n"
        "    if path.exists():\n"
        "        return\n"
        "    path.write_text(data)\n"
    )
    assert "TOCTOU-CHECK-USE" in rules_for(write)
    read = (
        "from pathlib import Path\n"
        "def load(path: Path) -> str:\n"
        "    if not path.is_file():\n"
        "        return ''\n"
        "    return path.open('r', encoding='utf-8').read()\n"
    )
    assert "TOCTOU-CHECK-USE" not in rules_for(read)


def test_a_bind_spec_built_from_the_raw_path_is_reported() -> None:
    snippet = (
        "def argv(mount):\n"
        "    spec = f\"type=bind,src={mount.get('host_path', '')},dst=/data,readonly\"\n"
        "    return ['docker', 'run', '--mount', spec]\n"
    )
    assert "TOCTOU-SYMLINK" in rules_for(snippet)


# --------------------------------------------------------------------------- #
# resource_limits                                                               #
# --------------------------------------------------------------------------- #


def test_missing_timeouts_are_reported_and_an_explicit_one_is_not() -> None:
    assert "SUBPROCESS-NO-TIMEOUT" in rules_for("import subprocess\nsubprocess.run(['ls'])\n")
    assert "SUBPROCESS-NO-TIMEOUT" not in rules_for(
        "import subprocess\nsubprocess.run(['ls'], timeout=10)\n"
    )
    assert "HTTP-NO-TIMEOUT" in rules_for(
        "import urllib.request\nurllib.request.urlopen(url)\n"
    )
    assert "HTTP-NO-TIMEOUT" not in rules_for(
        "import urllib.request\nurllib.request.urlopen(url, timeout=5)\n"
    )


def test_a_declared_timeout_that_is_not_passed_through_is_reported() -> None:
    """The defect: the function takes a ``timeout`` and the call ignores it."""
    snippet = (
        "import urllib.request\n"
        "def targets(port: int, *, timeout: float = 5.0):\n"
        "    with urllib.request.urlopen(f'http://127.0.0.1:{port}/json') as resp:\n"
        "        return resp.read()\n"
    )
    finding = rule_of(snippet, "DECLARED-TIMEOUT-UNUSED")
    assert finding.line == 3
    fixed = snippet.replace("/json'", "/json', timeout=timeout")
    assert "DECLARED-TIMEOUT-UNUSED" not in rules_for(fixed)


def test_a_kwargs_splat_is_not_reported_as_a_missing_timeout() -> None:
    """``subprocess.run(cmd, **kwargs)`` may carry the timeout — no finding."""
    snippet = (
        "import subprocess\n"
        "def run(cmd, **kwargs):\n"
        "    return subprocess.run(cmd, **kwargs)\n"
    )
    assert "SUBPROCESS-NO-TIMEOUT" not in rules_for(snippet)


# --------------------------------------------------------------------------- #
# error_leakage                                                                 #
# --------------------------------------------------------------------------- #


def test_traceback_rendering_is_reported() -> None:
    finding = rule_of("import traceback\nprint(traceback.format_exc())\n", "TRACEBACK-TO-USER")
    assert finding.severity == "low"


# --------------------------------------------------------------------------- #
# the audit's invariants on this repository                                     #
# --------------------------------------------------------------------------- #


def test_audit_is_deterministic() -> None:
    first = A.audit([REPO / "src"], base=REPO, live=False)
    second = A.audit([REPO / "src"], base=REPO, live=False)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_every_reported_finding_carries_its_class_severity_and_fix() -> None:
    report = A.audit([REPO / "src"], base=REPO, live=False)
    assert report["files_scanned"] > 50
    for item in report["findings"]:
        assert item["class"] in A.CLASSES, item
        assert item["severity"] in A.SEVERITIES, item
        assert item["fix"].strip(), item
        assert item["path"], item
        assert "location" in item


def test_the_tree_has_no_finding_for_the_classes_that_are_structurally_absent() -> None:
    """Pins the honest part of the report: these defect shapes are not in the tree."""
    report = A.audit([REPO / "src", REPO / "integrations"], base=REPO, live=False)
    rules = {item["rule"] for item in report["findings"]}
    for absent in (
        "SHELL-TRUE",
        "OS-SYSTEM",
        "SHELL-STRING-ARGV",
        "OS-SYSTEM",
        "EVAL-EXEC",
        "YAML-UNSAFE-LOAD",
        "PICKLE",
        "WORLD-WRITABLE",
        "HTTP-NO-TIMEOUT",
        "PRIVILEGED-CONTAINER",
        "DOCKER-SOCKET",
    ):
        hits = [i for i in report["findings"] if i["rule"] == absent]
        assert absent not in rules, f"{absent} appeared: {hits}"
    assert "unsafe_deserialisation" in report["classes_audited"]


def test_the_audit_module_is_clean_by_its_own_rules() -> None:
    """Self-scan: the auditor may not ship a defect it reports on others."""
    text = (REPO / "src" / "skaldir" / "security_audit.py").read_text(encoding="utf-8")
    findings = A.audit_source(text, "src/skaldir/security_audit.py")
    assert findings == [], [finding.as_dict() for finding in findings]
    cli_text = (REPO / "src" / "skaldir" / "security_audit_cli.py").read_text(encoding="utf-8")
    assert A.audit_source(cli_text, "src/skaldir/security_audit_cli.py") == []


def test_an_unparseable_file_is_reported_and_never_certified_clean() -> None:
    findings = A.audit_source("def broken(:\n", "broken.py")
    assert findings and findings[0].rule == "RUNTIME-EXPOSURE"
    assert "skipped" in findings[0].message


def test_a_secret_value_never_reaches_the_report() -> None:
    """The audit prints the shape of a match, never the value."""
    secret = "AKIA" + "ABCDEFGHIJKLMNOP"
    findings = A.audit_source(f'KEY = "{secret}"\n', "leak.py")
    assert findings, "a secret literal must be reported"
    blob = json.dumps([item.as_dict() for item in findings])
    assert secret not in blob, "the report quoted the secret it reported"
    assert "AKIA" in blob  # the masked preview keeps four characters
    report = A.audit([], base=REPO, live=False)
    assert secret not in json.dumps(report)


def test_rank_orders_severities_and_tolerates_unknown_names() -> None:
    ordered = [A.rank(name) for name in A.SEVERITIES]
    assert ordered == sorted(ordered)
    assert A.rank("who knows") == 0


# --------------------------------------------------------------------------- #
# runtime checks (live filesystem, all in a temp dir)                           #
# --------------------------------------------------------------------------- #


def test_runtime_permissions_are_info_when_a_0700_ancestor_blocks(tmp_path: Path) -> None:
    runtime = tmp_path / "godsv"
    runtime.mkdir()
    (runtime / "vault.json").write_text("{}", encoding="utf-8")
    (runtime / "vault.json").chmod(0o644)
    findings, facts = A.audit_runtime(runtime)
    assert facts["runtime_exists"] is True
    vault = [f for f in findings if f.path.endswith("vault.json")]
    assert vault, "a 0644 credential file is always reported"
    assert vault[0].severity == "info"  # blocked by the 0700 tmp_path ancestor
    assert "blocked" in vault[0].message or "0700" in vault[0].evidence


def test_runtime_permissions_are_medium_when_the_file_is_reachable(tmp_path: Path) -> None:
    """A wide mode under a traversable ancestor is a real exposure, not a footnote."""
    base = Path(tempfile.mkdtemp(dir="/tmp"))
    try:
        if not stat.S_IMODE(base.parent.stat().st_mode) & 0o001:
            pytest.skip("/tmp is not world-traversable here")
        base.chmod(0o755)  # the ancestors (/tmp, /) are world-traversable
        runtime = base / "godsv"
        runtime.mkdir()
        (runtime / "vault.json").write_text("{}", encoding="utf-8")
        (runtime / "vault.json").chmod(0o644)
        findings, _facts = A.audit_runtime(runtime)
        vault = [f for f in findings if f.path.endswith("vault.json")]
        assert vault and vault[0].severity == "medium"
        assert "readable by another local user" in vault[0].message
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_a_leftover_atomic_write_target_is_reported(tmp_path: Path) -> None:
    runtime = tmp_path / "godsv"
    runtime.mkdir()
    (runtime / "vault.tmp").write_text("{}", encoding="utf-8")
    findings, _facts = A.audit_runtime(runtime)
    assert any(f.rule == "WRITE-THEN-CHMOD" for f in findings)


def test_a_manifest_asking_for_privileges_is_reported(tmp_path: Path) -> None:
    runtime = tmp_path / "godsv"
    tool = runtime / "generated_tools" / "risky"
    tool.mkdir(parents=True)
    (tool / "manifest.yaml").write_text(
        "name: risky\nprivileged: true\nnetwork: host\n", encoding="utf-8"
    )
    findings, _facts = A.audit_runtime(runtime)
    hits = [f for f in findings if f.rule == "TOOL-PRIVILEGE-REQUEST"]
    assert hits and "privileged: true" in hits[0].evidence


def test_a_secret_on_our_own_command_line_is_reported(tmp_path: Path) -> None:
    secret = "sk-" + "b" * 32
    findings, facts = A.audit_runtime(tmp_path, argv=["skaldir", "vault", "add", secret])
    assert facts["argv_secret_shapes"] == 1
    hit = [f for f in findings if f.rule == "SECRET-IN-ARGV"]
    assert hit and secret not in json.dumps([f.as_dict() for f in findings])


def test_credential_names_are_reported_without_values(tmp_path: Path) -> None:
    _findings, facts = A.audit_runtime(
        tmp_path, environ={"GODSV_LLM_API_KEY": "sk-" + "c" * 30, "PATH": "/usr/bin"}
    )
    assert facts["credential_names_set"] == ["GODSV_LLM_API_KEY"]
    assert "sk-" not in json.dumps(facts)


def test_missing_runtime_dir_yields_no_findings_and_says_so(tmp_path: Path) -> None:
    findings, facts = A.audit_runtime(tmp_path / "absent")
    assert findings == []
    assert facts["runtime_exists"] is False


def test_skipping_test_directories_keeps_their_fixtures_out() -> None:
    report = A.audit([REPO / "src"], base=REPO, live=False)
    assert all("tests/" not in item["path"] for item in report["findings"])
