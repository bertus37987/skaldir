"""Tier C: property tests (Schemathesis) over the CLI's JSON contract.

``MVP/08-test-matrix.md`` §0 names the prerequisite for this tier and the route-inventory rule;
``MVP/15-property-tests.md`` is the report this file belongs to.

What this file proves, and what it cannot
-----------------------------------------
* The **schema is real**: it is compiled by ``tools/gv_schemathesis_run.py`` from
  ``skaldir contract dump --json`` (73 leaf commands, each with its args and its own JSON Schema
  for ``data``). :func:`test_openapi_inventory_matches_the_contract` pins that compile step against
  the contract, so a command that ships without a contract entry, or an argument that ships without
  a schema, fails here.
* Schemathesis really runs: :func:`test_schemathesis_runs_against_a_real_server` starts a local HTTP
  surface in this process and runs ``schemathesis run`` against the URL, asserting on the JSON
  report it writes (operations tested, generated cases, failures).
* The **server is a stand-in by default, but the product surface exists**.
  ``skaldir daemon serve --http`` — the command the plan designed as this tier's
  prerequisite — ships one HTTP route per CLI command plus ``/openapi.json``; measured, with
  its startup banner, by :func:`test_the_http_surface_the_tier_needs_ships`. The canonical
  suite still runs against the thin ``tools/`` shim (it executes ``skaldir <command> --json``
  in a throwaway ``GODSV_HOME``/``HERMES_HOME`` sandbox) because the shim needs no port and no
  token; the runner's ``--base-url`` targets the real surface unchanged.
* The generated cases are bounded (a documented subset, one seed, one worker) so the canonical
  suite stays usable: ``.venv/bin/python -m pytest tests -q -o addopts=""``. The full run
  (64 operations, coverage + fuzzing) is ``.venv/bin/python tools/gv_schemathesis_run.py``.

Counter-case (the tier must show one way failure cannot look like success):
:func:`test_the_shim_rejects_bad_bodies_instead_of_inventing_success` proves a schema-violating
request answers 4xx with an error record and **no** envelope, never a fabricated ``ok: true``.
"""

from __future__ import annotations

import importlib.util
import json
import os
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

#: Load the runner **by path** instead of mutating ``sys.path`` for the whole pytest session:
#: a session-wide ``tools/`` entry would shadow nothing today, but it changes import resolution for
#: every test collected after this file, and that is not this test's business.
RUNNER_PATH = ROOT / "tools" / "gv_schemathesis_run.py"
_SPEC = importlib.util.spec_from_file_location("gv_schemathesis_run", RUNNER_PATH)
assert _SPEC is not None and _SPEC.loader is not None
gvr = importlib.util.module_from_spec(_SPEC)
sys.modules.setdefault("gv_schemathesis_run", gvr)
_SPEC.loader.exec_module(gvr)

from skaldir import cli_contract  # noqa: E402  (src is on the path via pyproject `pythonpath`)

#: The operations the canonical suite runs. Chosen for one contract shape each, not for speed:
#: declared data schema (contract.list, jev.kinds, metrics), declared NO_SCHEMA and consistent
#: (prompts.list), the five commands whose data_available contradicts what they really emit
#: (completion.override, cu.demo, desktop.status, memory-retrieval-check, skills.list — the recorded
#: gaps), and a positional argument whose generated value cannot exist (scan).
SUBSET = (
    "completion.override",
    "contract.list",
    "cu.demo",
    "desktop.status",
    "jev.kinds",
    "memory-retrieval-check",
    "metrics",
    "prompts.list",
    "scan",
    "skills.list",
)


# --------------------------------------------------------------------------- #
# the compile step: contract dump -> OpenAPI
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def dump() -> dict:
    """``skaldir contract dump --json`` — the real inventory, read once."""
    return gvr.contract_dump(gvr.GODSVOICE)


def _records(dump: dict) -> dict[str, dict]:
    return {str(item["command"]): item for item in dump["data"]["commands"]}


def test_openapi_inventory_matches_the_contract(dump: dict) -> None:
    """One operation per leaf command, every argument typed, the exclusions recorded."""
    records = _records(dump)
    document, operations, skipped = gvr.build_openapi(dump)

    assert len(records) == dump["data"]["leaf_commands"]
    assert not dump["data"]["unregistered"], "a parser leaf shipped without a contract entry"

    covered = {item["command"] for item in operations} | {item["command"] for item in skipped}
    assert covered == set(records), "an operation is neither compiled nor excluded with a reason"
    assert {item["command"] for item in skipped} <= set(gvr.EXCLUDED)
    assert set(gvr.EXCLUDED) <= set(records), "a recorded exclusion names no real command"

    assert set(document["paths"]) == {item["path"] for item in operations}
    for item in operations:
        assert item["path"] == gvr.command_path(item["command"])
        assert set(document["paths"][item["path"]]) == {"post"}

    # the response schema is the envelope the CLI really builds — no hand-copied key list
    envelope = document["paths"]["/cli/contract/list"]["post"]["responses"]["200"]["content"][
        "application/json"]["schema"]
    assert envelope["required"] == gvr.envelope_keys()
    assert envelope["properties"]["exit_code"]["minimum"] == min(cli_contract.EXIT_NAMES)
    assert envelope["properties"]["exit_code"]["maximum"] == max(cli_contract.EXIT_NAMES)
    assert envelope["properties"]["schema_version"]["enum"] == [cli_contract.SCHEMA_VERSION]
    assert envelope["properties"]["error"]["anyOf"][0]["properties"]["code"]["enum"] == list(
        cli_contract.EXIT_ERROR_CODES.values()
    )

    # every declared argument has a schema, and nothing else is generated
    for item in operations:
        declared = {str(record["dest"]) for record in gvr.declared_args(records[item["command"]])}
        generated = set(item["request_schema"]["properties"]["args"]["properties"])
        assert generated == declared, f"{item['command']}: args in the contract != args generated"
        assert not (generated & set(gvr.COMMON_FLAGS)), "a common flag leaked into the request body"


def test_declared_data_schema_is_taken_from_the_contract(dump: dict) -> None:
    """The per-operation ``data`` schema is the contract's own; ``data_available`` is honoured."""
    records = _records(dump)
    document, operations, _ = gvr.build_openapi(dump)
    for item in operations:
        record = records[item["command"]]
        schema = document["paths"][item["path"]]["post"]["responses"]["200"]["content"][
            "application/json"]["schema"]
        assert item["data_available"] is bool(record["data_available"])
        if record["data_available"]:
            assert schema["properties"]["data"]["anyOf"][0] == record["data_schema"]
        else:
            # NO_SCHEMA: the contract promises data: null (cli_contract.NO_SCHEMA)
            assert schema["properties"]["data"] == {"type": "null"}


# --------------------------------------------------------------------------- #
# the stand-in surface: behaviour, including how failure is *not* success
# --------------------------------------------------------------------------- #


def _call(base_url: str, path: str, payload: object | None = None, *, method: str = "POST",
          raw: bytes | None = None) -> tuple[int, dict, dict]:
    body = raw if raw is not None else json.dumps(payload).encode()
    request = urllib.request.Request(f"{base_url}{path}", data=body if method == "POST" else None,
                                    method=method, headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.status, dict(response.headers), json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), json.loads(error.read() or b"{}")


@pytest.fixture(scope="module")
def shim(tmp_path_factory: pytest.TempPathFactory, dump: dict):
    """A real HTTP server in this process, running the real CLI in a throwaway sandbox."""
    _, operations, _ = gvr.build_openapi(dump)
    sandbox = tmp_path_factory.mktemp("gv-schemathesis-sandbox")
    server, stats, _ = gvr.build_shim(gvr.GODSVOICE, operations, sandbox=sandbox, timeout_s=120)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield {"url": f"http://127.0.0.1:{server.server_address[1]}", "stats": stats,
               "sandbox": sandbox}
    finally:
        server.shutdown()
        server.server_close()


def test_the_shim_returns_the_envelope_the_cli_wrote(shim: dict) -> None:
    status, _, body = _call(shim["url"], "/cli/metrics", {"args": {}})
    assert status == 200
    assert set(gvr.envelope_keys()) <= set(body)
    assert body["command"] == "metrics"
    assert body["exit_code"] in cli_contract.EXIT_NAMES
    assert body["schema_version"] == cli_contract.SCHEMA_VERSION

    # a usage error is still a transport success: the CLI's verdict lives in the envelope
    status, _, usage = _call(shim["url"], "/cli/scan", {"args": {}})
    assert status == 200
    assert usage["exit_code"] == cli_contract.EXIT_USAGE
    assert usage["ok"] is False
    assert isinstance(usage["error"], dict) and usage["error"]["code"] == "usage"


def test_the_shim_rejects_bad_bodies_instead_of_inventing_success(shim: dict) -> None:
    """Counter-case: malformed input is visibly refused, never answered with an envelope."""
    bad_calls = [
        ("/cli/metrics", None, b"not json at all"),                     # body is not JSON
        ("/cli/metrics", None, b"[]"),                                   # body is not an object
        ("/cli/metrics", {"flags": {}}, None),                            # required `args` absent
        ("/cli/metrics", {"args": "nope"}, None),                         # `args` is not an object
        ("/cli/metrics", {"args": {}, "extra": 1}, None),                 # unknown body key
        ("/cli/metrics", {"args": {"nope": 1}}, None),                     # unknown argument
        ("/cli/tools/list", {"args": {"limit": "not-an-int"}}, None),      # declared type violation
    ]
    for path, payload, raw in bad_calls:
        status, _, body = _call(shim["url"], path, payload, raw=raw)
        assert status == 400, f"{path} {payload!r} -> {status}"
        assert body.get("error"), "a refusal without a reason"
        assert "exit_code" not in body and "ok" not in body, (
            "a rejected request answered with something that looks like an envelope"
        )

    status, headers, _ = _call(shim["url"], "/cli/metrics", None, method="GET")
    assert status == 405
    assert "POST" in str(headers.get("Allow", ""))

    status, _, body = _call(shim["url"], "/cli/not/a/route", {"args": {}})
    assert status == 404 and body.get("error")


def test_no_response_is_a_5xx(shim: dict) -> None:
    """A 5xx means the surface produced no envelope at all — the one thing it must never do."""
    for command in ("metrics", "jev.kinds", "skills.list"):
        status, _, _ = _call(shim["url"], "/cli/" + command.replace(".", "/"), {"args": {}})
        assert status == 200, f"{command} answered {status}"
    assert not any(code >= 500 for code in shim["stats"].statuses), shim["stats"].statuses


# --------------------------------------------------------------------------- #
# the property run itself (Schemathesis over the live surface)
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def property_run(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """Run the runner for the documented subset; the artifact is the evidence."""
    out = tmp_path_factory.mktemp("gv-schemathesis-run")
    command = [
        sys.executable, str(RUNNER_PATH),
        "--only", ",".join(SUBSET),
        "--examples", "1",
        "--phases", "coverage",
        "--workers", "1",
        "--seed", "20260918",
        "--report-dir", str(out),
        "--schema-out", str(out / "cli_contract.openapi.json"),
        "--artifact", str(out / "artifact.json"),
    ]
    proc = subprocess.run(command, capture_output=True, text=True, timeout=1800, cwd=str(ROOT))
    artifact_path = out / "artifact.json"
    artifact = json.loads(artifact_path.read_text()) if artifact_path.exists() else None
    return {"proc": proc, "out": out, "artifact": artifact, "command": command}


def test_schemathesis_runs_against_a_real_server(property_run: dict) -> None:
    artifact = property_run["artifact"]
    assert artifact, (
        "the property run produced no artifact\n"
        f"exit={property_run['proc'].returncode}\n{property_run['proc'].stdout[-3000:]}\n"
        f"{property_run['proc'].stderr[-2000:]}"
    )
    assert artifact["target_kind"] == "stand-in shim (test only)"
    assert artifact["schemathesis"]["version"], "schemathesis did not report its version"

    operations = artifact["schemathesis"]["operations"]
    assert operations["selected"] == len(SUBSET)
    assert operations["tested"] == len(SUBSET), operations
    assert operations["errored"] == 0, operations

    cases = artifact["schemathesis"]["test_cases"]
    assert cases["generated"] > 0, "Schemathesis generated nothing — the run proves nothing"

    # every command in the subset was really executed at least once by the reference pass and the
    # generated cases both reached the CLI (a 4xx-only run would prove nothing about the envelope)
    assert artifact["shim"]["cli_invocations"] > 0
    assert artifact["shim"]["statuses"].get("200", 0) > 0
    assert artifact["shim"]["envelope_failures"] == 0, artifact["shim"]["errors"][:5]
    assert artifact["shim"]["timeouts"] == 0, artifact["shim"]["errors"][:5]
    assert not [code for code in artifact["shim"]["statuses"] if int(code) >= 500], \
        artifact["shim"]["statuses"]

    # the report is the real thing, not a stub
    assert artifact["schemathesis"]["seed"] == 20260918
    assert artifact["schemathesis"]["checks"], "no checks were configured"


def test_every_answer_obeys_the_envelope_contract(property_run: dict) -> None:
    """The CLI's own hard rules, re-checked on every answer Schemathesis really received."""
    artifact = property_run["artifact"]
    assert artifact
    invariants = artifact["cli_invariants"]
    assert invariants["answers_rechecked"] > 0, "no response was re-checked"
    assert invariants["envelope_violations"] == [], invariants["envelope_violations"][:3]

    # the reference pass compared every subset command against its declared data_available
    rows = artifact["reference_pass"]["rows"]
    assert len(rows) == len(SUBSET)
    answered = [row for row in rows if row["status"] == "answered"]
    assert len(answered) == len(SUBSET), rows
    assert [row["command"] for row in answered] == sorted(SUBSET)


def test_every_observed_failure_is_classified_and_recorded(property_run: dict) -> None:
    """A new failure is a gate; a recorded gap is a report. The difference is explicit."""
    artifact = property_run["artifact"]
    assert artifact
    for example in artifact["failing_examples"]:
        assert example["classification"], example
        assert example["classification"].split(" (")[0] in (
            "real_bug", "expected_4xx", "schema_gap", "unclassified"
        ), example["classification"]

    assert artifact["new_failures"] == [], (
        "Schemathesis found a failure that is not in tools/gv_schemathesis_run.py "
        f"KNOWN_CONTRACT_GAPS: {artifact['new_failures']}"
    )
    for example in artifact["failing_examples"]:
        assert example["known_gap"], example

    recorded = {(gap["operation"], gap["check"]) for gap in artifact["known_gaps"]}
    observed = {(example["operation"], example["check"])
                for example in artifact["failing_examples"]}
    assert observed <= recorded

    # a gap that only the generator reproduces must still be reproduced here, not just remembered
    generated = {
        gap["operation"] for gap in artifact["known_gaps"]
        if gap["reproduced_by"] == "generation" and gap["operation"] in SUBSET
    }
    assert generated <= {example["operation"] for example in artifact["failing_examples"]}, (
        f"recorded generation-only gaps {sorted(generated)} did not reproduce"
    )


def test_the_recorded_data_schema_gaps_reproduce(property_run: dict) -> None:
    """A recorded gap is a measurement, not a memory: it must still be there, per command."""
    artifact = property_run["artifact"]
    assert artifact
    mismatches = {row["command"] for row in artifact["reference_pass"]["data_mismatches"]}
    expected = {
        gap["operation"] for gap in artifact["known_gaps"]
        if gap["kind"] == "schema_gap" and gap["reproduced_by"] == "reference_pass"
        and gap["operation"] in SUBSET
    }
    assert mismatches == expected, (
        f"declared-data contradictions {sorted(mismatches)} != recorded {sorted(expected)} — "
        "update KNOWN_CONTRACT_GAPS (or the contract) with the new measurement"
    )


def test_no_command_destroys_the_directory_it_runs_in() -> None:
    """A directory-shaped path argument must not chmod the directory the command runs in.

    This pinned the measured defect ``KNOWN_DEFECTS['cwd-chmod-on-empty-path-argument']`` as
    ``xfail(strict=True)``; it is green because the class is fixed (``treasury.state_path`` refuses
    a blank/``.``/``..``/directory path with exit 1, and ``treasury._restrict`` can only ever chmod
    a file). The property run reproduced the original behaviour as: ``spend allowance --db ""``
    answered ``ok: true`` and left its cwd at ``0600`` with no execute bit, so every later process
    started there failed with ``EACCES`` — only a check *outside* the envelope finds this class.
    """
    with tempfile.TemporaryDirectory(prefix="gv-chmod-probe-") as sandbox:
        os.chmod(sandbox, 0o755)
        environment = {**os.environ, "GODSV_HOME": sandbox, "HERMES_HOME": sandbox,
                       "NO_COLOR": "1",
                       # this checkout's src, not whatever the shared venv's editable install
                       # resolves `godsv` to while other waves install their own tree into it
                       "PYTHONPATH": str(ROOT / "src")}
        before = stat.S_IMODE(os.stat(sandbox).st_mode)
        proc = subprocess.run(
            [str(gvr.GODSVOICE), "spend", "allowance", "--db", "", "--json"],
            capture_output=True, text=True, timeout=120, cwd=sandbox, env=environment,
        )
        after = stat.S_IMODE(os.stat(sandbox).st_mode)
        traversable = os.access(sandbox, os.X_OK)
    assert after == before, (
        f"the working directory went from {oct(before)} to {oct(after)} "
        f"(traversable={traversable}) — a command destroyed the directory it ran in"
    )
    assert traversable, "the working directory lost its execute bit (EACCES for later processes)"
    envelope = json.loads(proc.stdout)
    assert envelope["ok"] is False, "a refused path argument answered ok: true"
    assert proc.returncode != 0 and envelope["exit_code"] == cli_contract.EXIT_USAGE, envelope
    assert envelope["error"]["code"] == "usage", envelope["error"]


def test_every_destroyed_cwd_is_explained_by_a_recorded_defect(property_run: dict) -> None:
    """The property run repairs its own sandbox, but the finding must never be unattributed."""
    artifact = property_run["artifact"]
    assert artifact
    for change in artifact["shim"]["sandbox_mode_changes"]:
        assert change["classified"], change
        assert change["defect_id"], change
        assert change["restored_mode"] == oct(gvr.SANDBOX_MODE), change
    assert artifact["shim"]["unexplained_mode_changes"] == []


def test_the_cli_never_answers_with_a_broken_envelope(property_run: dict) -> None:
    """Every response was a single JSON document with the contract's hard rules intact."""
    artifact = property_run["artifact"]
    assert artifact
    assert artifact["shim"]["envelope_failures"] == 0, artifact["shim"]["errors"][:5]
    # usage envelopes name the group, not the leaf: reported as a gap, never asserted away
    laxity = artifact["cli_invariants"]["envelope_command_naming_the_group"]
    assert isinstance(laxity, list)
    for item in laxity:
        assert item["detail"], item


# --------------------------------------------------------------------------- #
# the product surface this tier can now target
# --------------------------------------------------------------------------- #


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_the_http_surface_the_tier_needs_ships() -> None:
    """``daemon serve --http`` — this tier's prerequisite (MVP/08 §0) — exists and serves.

    The gap this used to assert ("invalid choice: 'daemon'") is closed: one HTTP route
    per CLI command plus the OpenAPI document, from the product itself. Measured here by
    booting the real console script on a free port, reading ``/health`` and
    ``/openapi.json`` off it and naming the banner it printed — so the shim is a
    fallback, not the only surface, and the runner's ``--base-url`` switch is real.
    """
    port = _free_port()
    # The two paths the product surface documents (api_server.HEALTH_PATH / OPENAPI_PATH);
    # spelled out here so this test measures the *served* surface, not our own constants.
    proc = subprocess.Popen(
        [str(gvr.GODSVOICE), "daemon", "serve", "--http", "--port", str(port)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=str(ROOT),
    )
    health = None
    document: dict = {}
    try:
        deadline = time.time() + 90
        while time.time() < deadline and proc.poll() is None:
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/health", timeout=2
                ) as response:
                    health = response.status
                break
            except (urllib.error.URLError, OSError):
                time.sleep(0.5)
        assert health == 200, f"the HTTP surface did not answer /health (exit {proc.poll()})"
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/openapi.json", timeout=30
        ) as response:
            document = json.loads(response.read())
    finally:
        proc.terminate()
        try:
            _stdout, stderr = proc.communicate(timeout=60)
        except subprocess.TimeoutExpired:  # a warm server drains slowly: be explicit
            proc.kill()
            _stdout, stderr = proc.communicate(timeout=60)

    assert f"listening on http://127.0.0.1:{port}" in stderr, stderr[-400:]
    # the served document is the product's own — one operation per CLI route
    assert document["x-godsv-command-count"] > 100, document.get("x-godsv-command-count")
    assert document["paths"], "the served OpenAPI document carries no paths"

    # and the runner can already target a real surface, so the switch is a flag, not a rewrite
    assert "--base-url" in subprocess.run(
        [sys.executable, str(RUNNER_PATH), "--help"],
        capture_output=True, text=True, timeout=120, cwd=str(ROOT),
    ).stdout
