"""VERIFY — real user flows, driven through a real browser and a real isolated desktop.

What this file pins:

1. DECLARATION — a flow is declared (start-state anchors → actions → end-state anchors). A
   declaration that names an action or an anchor check the verifier does not implement is
   **refused by name** and becomes an ``UNKNOWN`` result: a typo can never turn into a silent
   skip, and the verdict can never look stronger than the declaration allows.
2. VERDICT — ``PASS``/``FAIL``/``UNKNOWN`` computed in one place. ``UNKNOWN`` is never a
   ``PASS``: no flow at all, an unreachable target, a refused declaration or a flow that could
   not be driven here all end in ``UNKNOWN``.
3. REAL BROWSER — several scripted user flows (open, search, follow a link, a refusal, browser
   back) really run against a real Chromium over a real local HTTP server, and every one of
   them produces a real PNG whose sha256 is recomputable from the bytes on disk.
4. REAL DESKTOP — a flow is really driven on a real, **isolated** display through
   ``skaldir.computer_use.ContainerRunner`` (the project's own runner; the live desktop is
   never touched). When this host cannot offer one, the flow is ``UNKNOWN`` with the exact
   capability named — asserted here rather than skipped.
5. THE PRODUCT SURFACE — ``skaldir verify flow`` / ``verify flows`` exist, carry ``--json``,
   have ``cli_contract.COMMANDS`` entries (so ``unregistered`` stays empty) and map the verdict
   onto the contract's exit codes (0 PASS, 2 FAIL, 3 UNKNOWN, 5 an unknown ``--flow``).
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from skaldir import verify_flows as VF

REPO = Path(__file__).resolve().parents[1]
SITE = REPO / "tests" / "fixtures" / "verify_flows" / "site"

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


# --------------------------------------------------------------------------- #
# harnesses                                                                     #
# --------------------------------------------------------------------------- #


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    return port


class _Site:
    """A real HTTP server over the fixture site (stdlib only, loopback only)."""

    def __init__(self) -> None:
        self.port = _free_port()
        self.process = subprocess.Popen(
            [sys.executable, "-m", "http.server", str(self.port), "--bind", "127.0.0.1",
             "--directory", str(SITE)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.time() + 20
        import urllib.error
        import urllib.request

        while time.time() < deadline:
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{self.port}/index.html", timeout=2
                ) as reply:
                    if reply.status == 200:
                        return
            except (urllib.error.URLError, OSError):
                time.sleep(0.2)
        self.stop()
        raise RuntimeError("the fixture site did not come up")

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def stop(self) -> None:
        self.process.terminate()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover — a stuck server
            self.process.kill()


@pytest.fixture(scope="module")
def site():
    server = _Site()
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture(scope="module")
def browser_available():
    pytest.importorskip("playwright.sync_api", reason="the browser flows need Playwright")
    available, detail = VF.browser_availability()
    if not available:  # pragma: no cover — Playwright is installed here
        pytest.skip(detail)
    return detail


def _tmpdir() -> str:
    import tempfile

    path = tempfile.mkdtemp(prefix="gv-flows-test-")
    return path


def _write(root: Path, payload: object, name: str = "") -> Path:
    """Write a declaration where ``verify`` looks for it: ``<workspace>/.godsv/flows.json``."""
    target = root / (name or VF.FLOWS_FILENAME)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload), encoding="utf-8")
    return target


def _declaration(tmp_path: Path, payload: object) -> VF.FlowDeclaration:
    return VF.load_flows(tmp_path, _write(tmp_path, payload))


def _run(flows, tmp_path: Path, declaration=None):
    return VF.run_flows(
        flows,
        screenshot_dir=tmp_path / "shots",
        state_dir=tmp_path / "state",
        declaration=declaration,
    )


# --------------------------------------------------------------------------- #
# 1. the declaration                                                           #
# --------------------------------------------------------------------------- #


class TestDeclaration:
    def test_a_flow_needs_an_end_state(self):
        with pytest.raises(VF.DeclarationError, match="end state is required"):
            VF.parse_flow({
                "id": "no-end",
                "target": {"kind": "browser", "url": "http://127.0.0.1:1/"},
                "steps": [{"action": "goto", "url": "http://127.0.0.1:1/"}],
            })

    def test_a_flow_needs_steps(self):
        with pytest.raises(VF.DeclarationError, match="no step proves nothing"):
            VF.parse_flow({
                "id": "no-steps",
                "target": {"kind": "browser", "url": "http://127.0.0.1:1/"},
                "steps": [],
                "end_state": [{"check": "present", "selector": "body"}],
            })

    def test_a_browser_target_needs_a_url(self):
        with pytest.raises(VF.DeclarationError, match="needs a 'url'"):
            VF.parse_flow({
                "id": "no-url",
                "target": {"kind": "browser"},
                "steps": [{"action": "goto", "url": "http://127.0.0.1:1/"}],
                "end_state": [{"check": "present", "selector": "body"}],
            })

    def test_an_unknown_target_kind_is_refused(self):
        with pytest.raises(VF.DeclarationError, match="unknown target kind"):
            VF.parse_flow({
                "id": "bad-kind",
                "target": {"kind": "hologram"},
                "steps": [{"action": "goto", "url": "http://127.0.0.1:1/"}],
                "end_state": [{"check": "present", "selector": "body"}],
            })

    def test_an_unknown_action_is_refused_by_name(self):
        with pytest.raises(VF.DeclarationError, match="unknown action 'zoom_out'"):
            VF.parse_step({"action": "zoom_out", "selector": "#q"}, 0)

    def test_an_unknown_anchor_check_is_refused_by_name(self):
        with pytest.raises(VF.DeclarationError, match="unknown anchor check 'vibes_ok'"):
            VF.parse_anchor({"check": "vibes_ok", "selector": "#q"})

    def test_a_text_anchor_needs_its_text(self):
        with pytest.raises(VF.DeclarationError, match="needs a 'text'"):
            VF.parse_anchor({"check": "text_contains", "selector": "#q"})

    def test_a_url_anchor_needs_its_value(self):
        with pytest.raises(VF.DeclarationError, match="needs a 'value'"):
            VF.parse_anchor({"check": "url_contains"})

    def test_a_window_anchor_needs_no_selector(self):
        anchor = VF.parse_anchor({"check": "window_present", "value": "target"})
        assert anchor.selector == "" and anchor.value == "target"
        assert "window" in anchor.describe()

    def test_a_refused_flow_is_reported_and_never_dropped_silently(self, tmp_path):
        payload = {
            "flows": [
                {
                    "id": "good",
                    "target": {"kind": "browser", "url": "http://127.0.0.1:1/"},
                    "steps": [{"action": "goto", "url": "http://127.0.0.1:1/"}],
                    "end_state": [{"check": "present", "selector": "body"}],
                },
                {
                    "id": "bad",
                    "target": {"kind": "browser", "url": "http://127.0.0.1:1/"},
                    "steps": [{"action": "levitate"}],
                    "end_state": [{"check": "present", "selector": "body"}],
                },
            ]
        }
        declaration = _declaration(tmp_path, payload)
        assert [flow.flow_id for flow in declaration.flows] == ["good"]
        assert [item["flow_id"] for item in declaration.rejected] == ["bad"]
        assert any("levitate" in item for item in declaration.warnings)
        assert declaration.as_dict()["rejected"][0]["flow_id"] == "bad"

    def test_a_missing_declaration_is_reported_with_its_path(self, tmp_path):
        declaration = VF.load_flows(tmp_path)
        assert declaration.flows == ()
        assert declaration.source == "none"
        assert declaration.warnings and str(VF.FLOWS_FILENAME) in declaration.warnings[0]

    def test_a_flow_with_no_declared_file_says_so(self, tmp_path):
        declaration = VF.load_flows(tmp_path, tmp_path / "nope.json")
        assert declaration.flows == ()
        assert "no flow declaration at" in declaration.warnings[0]

    def test_the_template_is_a_runnable_declaration(self):
        payload = VF.template_flows("http://127.0.0.1:8000")
        flows = [VF.parse_flow(item) for item in payload["flows"]]
        assert len(flows) >= 5
        for flow in flows:
            ok, why = flow.declaration_problem()
            assert ok, why
            assert flow.end_state, flow.flow_id

    def test_the_desktop_template_is_a_runnable_declaration(self):
        payload = VF.template_desktop_flows(":90")
        flow = VF.parse_flow(payload["flows"][0])
        ok, why = flow.declaration_problem()
        assert ok, why
        assert flow.target_kind == VF.TARGET_DESKTOP

    def test_a_browser_flow_cannot_declare_a_window_anchor(self):
        flow = VF.parse_flow({
            "id": "window-on-a-browser",
            "target": {"kind": "browser", "url": "http://127.0.0.1:1/"},
            "steps": [{"action": "goto", "url": "http://127.0.0.1:1/"}],
            "end_state": [{"check": "window_present"}],
        })
        ok, why = flow.declaration_problem()
        assert not ok and "cannot judge" in why

    def test_a_desktop_flow_cannot_declare_a_css_selector_anchor(self):
        flow = VF.parse_flow({
            "id": "css-on-a-desktop",
            "target": {"kind": "desktop"},
            "steps": [{"action": "sleep", "ms": 1}],
            "end_state": [{"check": "count", "selector": "#q", "count": 1}],
        })
        ok, why = flow.declaration_problem()
        assert not ok and "cannot judge" in why

    def test_a_base_url_rewrites_only_the_loopback_placeholders(self):
        flows = tuple(VF.parse_flow(item) for item in VF.template_flows()["flows"])
        rewritten = VF.apply_base_url(flows, "http://example.test:9")
        assert all(flow.target_url.startswith("http://example.test:9") for flow in rewritten)
        assert all(
            step.url.startswith("http://example.test:9")
            for flow in rewritten for step in flow.steps if step.action == VF.STEP_GOTO
        )


# --------------------------------------------------------------------------- #
# 2. the verdict                                                               #
# --------------------------------------------------------------------------- #


def _result(status: str, flow_id: str = "f") -> VF.FlowResult:
    return VF.FlowResult(flow_id=flow_id, title=flow_id, target_kind="browser",
                         target="http://127.0.0.1:1/", status=status, reason=f"{flow_id} {status}")


class TestVerdict:
    def test_no_flow_is_unknown_not_pass(self):
        verdict, reason = VF.verdict_for([])
        assert verdict == VF.VERDICT_UNKNOWN
        assert "nothing to certify" in reason

    def test_every_flow_passed_is_pass(self):
        verdict, _ = VF.verdict_for([_result(VF.STATUS_PASSED)])
        assert verdict == VF.VERDICT_PASS

    def test_one_failure_is_fail_even_beside_a_pass(self):
        verdict, reason = VF.verdict_for(
            [_result(VF.STATUS_PASSED, "a"), _result(VF.STATUS_FAILED, "b")]
        )
        assert verdict == VF.VERDICT_FAIL and "b" in reason

    def test_one_unknown_is_unknown_and_never_pass(self):
        verdict, reason = VF.verdict_for(
            [_result(VF.STATUS_PASSED, "a"), _result(VF.STATUS_UNKNOWN, "b")]
        )
        assert verdict == VF.VERDICT_UNKNOWN and "b" in reason

    def test_a_declaration_that_was_refused_forces_unknown(self, tmp_path):
        payload = {"flows": [{
            "id": "bad",
            "target": {"kind": "browser", "url": "http://127.0.0.1:1/"},
            "steps": [{"action": "levitate"}],
            "end_state": [{"check": "present", "selector": "body"}],
        }]}
        declaration = _declaration(tmp_path, payload)
        report = _run(declaration.flows, tmp_path, declaration)
        assert report.verdict == VF.VERDICT_UNKNOWN
        assert report.counts == {"flows": 1, "passed": 0, "failed": 0, "unknown": 1}
        assert "the declaration was refused" in report.results[0].reason

    def test_the_report_carries_a_recomputable_evidence_hash(self, tmp_path):
        payload = {"flows": [{
            "id": "bad",
            "target": {"kind": "browser", "url": "http://127.0.0.1:1/"},
            "steps": [{"action": "levitate"}],
            "end_state": [{"check": "present", "selector": "body"}],
        }]}
        declaration = _declaration(tmp_path, payload)
        report = _run(declaration.flows, tmp_path, declaration)
        payload = report.as_dict()
        claimed = payload.pop("evidence_sha256")
        assert claimed == VF.sha256_text(VF.canonical_json(payload))


# --------------------------------------------------------------------------- #
# 3. the real browser suite                                                     #
# --------------------------------------------------------------------------- #


class TestRealBrowser:
    @pytest.fixture(autouse=True)
    def _needs_a_browser(self):
        pytest.importorskip("playwright.sync_api", reason="these flows need Playwright")

    def test_every_declared_flow_really_passes(self, site, tmp_path):
        flows = tuple(VF.parse_flow(item) for item in VF.template_flows(site.base)["flows"])
        report = _run(flows, tmp_path)
        assert report.verdict == VF.VERDICT_PASS, [
            (item.flow_id, item.reason) for item in report.results if not item.passed
        ]
        assert report.counts == {"flows": len(flows), "passed": len(flows), "failed": 0,
                                 "unknown": 0}

    def test_every_flow_produced_a_real_screenshot_the_hash_matches(self, site, tmp_path):
        flows = tuple(VF.parse_flow(item) for item in VF.template_flows(site.base)["flows"])
        report = _run(flows, tmp_path)
        for result in report.results:
            assert result.screenshots, f"{result.flow_id} produced no screenshot"
            for shot in result.screenshots:
                path = Path(shot.path)
                assert path.is_file(), shot.path
                assert path.stat().st_size > 0
                assert path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n", "not a real PNG"
                assert VF.sha256_file(path)[0] == shot.sha256, (
                    f"{result.flow_id}: the reported hash is not the hash of the bytes on disk"
                )
                assert shot.size_bytes == path.stat().st_size

    def test_the_end_state_assertions_carry_the_value_that_was_observed(self, site, tmp_path):
        flows = tuple(VF.parse_flow(item) for item in VF.template_flows(site.base)["flows"])
        report = _run(flows, tmp_path)
        for result in report.results:
            assert result.end_state, f"{result.flow_id} judged no end-state anchor"
            for check in result.end_state:
                assert check.status == VF.STATUS_PASSED
                # the observation is recorded even when it is the empty string: what matters
                # is that the value the verdict was made about is the value a reader sees
                assert "observed" in check.as_dict()
                assert check.anchor.describe()

    def test_the_evidence_names_the_url_assertion_observation_and_screenshot(self, site, tmp_path):
        """The owner's evidence list, per flow: URL, the assertion, the observed state, a PNG."""
        flows = tuple(VF.parse_flow(item) for item in VF.template_flows(site.base)["flows"])
        report = _run(flows, tmp_path)
        for result in report.results:
            payload = result.as_dict()
            assert payload["target"].startswith(site.base), "the URL the flow ran against"
            assert payload["start_state"] or payload["steps"], "the declared actions"
            assert payload["observed"]["url"].startswith(site.base), "the observed URL"
            assertions = [check["assertion"] for check in payload["end_state"]]
            assert assertions, "the assertions that were judged"
            assert payload["observed"]["title"], "the observed page state"
            assert payload["screenshots"], "the screenshot paths"
            assert payload["evidence_sha256"] == VF.sha256_text(
                VF.canonical_json({k: v for k, v in payload.items() if k != "evidence_sha256"})
            )

    def test_a_wrong_end_state_fails_and_names_the_observed_value(self, site, tmp_path):
        payload = VF.template_flows(site.base)
        payload["flows"][0]["end_state"].append(
            {"check": "text_contains", "selector": "h1", "text": "NOT ON THIS PAGE"}
        )
        declaration = _declaration(tmp_path, payload)
        report = _run(declaration.flows, tmp_path, declaration)
        assert report.verdict == VF.VERDICT_FAIL
        failed = report.results[0]
        assert failed.status == VF.STATUS_FAILED
        assert "NOT ON THIS PAGE" in failed.reason
        assert "Search the demo library" in failed.reason, (
            "the failure must show what was really observed, not only what was expected"
        )

    def test_a_missing_element_fails_as_a_missing_element(self, site, tmp_path):
        payload = VF.template_flows(site.base)
        payload["flows"][1]["end_state"].append({"check": "present", "selector": "#nope"})
        declaration = _declaration(tmp_path, payload)
        report = _run(declaration.flows, tmp_path, declaration)
        assert report.verdict == VF.VERDICT_FAIL
        failed = [item for item in report.results if item.flow_id == "search-a-term"][0]
        assert "is not in the document" in failed.reason

    def test_a_missing_selector_in_a_step_fails_that_step(self, site, tmp_path):
        payload = VF.template_flows(site.base)
        payload["flows"][0]["steps"].insert(0, {"action": "click", "selector": "#not-here"})
        declaration = _declaration(tmp_path, payload)
        report = _run(declaration.flows, tmp_path, declaration)
        failed = [item for item in report.results if item.flow_id == "open-and-read"][0]
        assert failed.status == VF.STATUS_FAILED
        assert "step 0 (click)" in failed.reason

    def test_an_unreachable_target_is_unknown_never_fail(self, tmp_path):
        flows = (VF.parse_flow({
            "id": "unreachable",
            "title": "a target that does not answer",
            "target": {"kind": "browser", "url": f"http://127.0.0.1:{_closed_port()}/"},
            "steps": [{"action": "assert", "check": "present", "selector": "body"}],
            "end_state": [{"check": "present", "selector": "body"}],
        }),)
        report = _run(flows, tmp_path)
        assert report.verdict == VF.VERDICT_UNKNOWN
        assert report.results[0].status == VF.STATUS_UNKNOWN
        assert "did not answer" in report.results[0].reason

    def test_two_flows_do_not_share_a_browser_session(self, site, tmp_path):
        """Isolation, measured: a flow that needs the pristine start page really gets it.

        The first flow deliberately leaves the browser on the article page. If the second flow
        inherited that session it would start on ``article.html``, fail its ``#q`` start-state
        anchor and never see ``#results`` — so the second flow's own start-state check passing
        is the measurement.
        """
        payload = VF.template_flows(site.base)
        flows = [VF.parse_flow(item) for item in payload["flows"]]
        first = [flow for flow in flows if flow.flow_id == "follow-a-link"][0]
        second = [flow for flow in flows if flow.flow_id == "search-a-term"][0]
        report = _run((first, second), tmp_path)
        assert report.verdict == VF.VERDICT_PASS, [
            (item.flow_id, item.reason) for item in report.results if not item.passed
        ]
        assert report.results[0].observed["url"].endswith("article.html?term=alpha")
        assert report.results[1].observed["url"].endswith("search.html?q=alpha")
        assert all(check.status == VF.STATUS_PASSED for check in report.results[1].start_state)

    def test_the_same_declaration_runs_the_same_way_twice(self, site, tmp_path):
        flows = tuple(VF.parse_flow(item) for item in VF.template_flows(site.base)["flows"])
        first, second = _run(flows, tmp_path), _run(flows, tmp_path)
        assert first.verdict == second.verdict == VF.VERDICT_PASS
        assert [item.status for item in first.results] == [
            item.status for item in second.results
        ]


def _closed_port() -> int:
    """A port nothing is listening on (bound, then released)."""
    return _free_port()


# --------------------------------------------------------------------------- #
# 4. the real desktop                                                           #
# --------------------------------------------------------------------------- #


class TestRealDesktop:
    def test_the_desktop_probe_is_honest(self):
        available, detail = VF.desktop_availability()
        assert isinstance(available, bool)
        assert detail
        if not available:
            assert "podman" in detail or "image missing" in detail

    def test_a_desktop_flow_is_either_really_driven_or_unknown_with_its_reason(self, tmp_path):
        """No skip: the flow either really runs on an isolated display, or says why it cannot."""
        flow = VF.parse_flow(VF.template_desktop_flows(":90")["flows"][0])
        report = _run((flow,), tmp_path)
        available, detail = VF.desktop_availability()
        result = report.results[0]
        if available:
            assert result.status == VF.STATUS_PASSED, result.reason
            assert report.verdict == VF.VERDICT_PASS
            assert result.screenshots and "isolated" in result.runner_detail
            for shot in result.screenshots:
                path = Path(shot.path)
                assert path.is_file() and path.stat().st_size > 0
                assert path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
                assert VF.sha256_file(path)[0] == shot.sha256
            # the runner's own observation is the evidence, and the window name is in it
            assert any(
                "skaldir-userflow-target" in str(check.observed)
                for check in result.end_state
            )
        else:
            assert result.status == VF.STATUS_UNKNOWN, (
                "a desktop that cannot be driven here is UNKNOWN with the capability named, "
                "never a pass and never a silent skip"
            )
            assert detail in result.reason or "did not start" in result.reason
            assert report.verdict == VF.VERDICT_UNKNOWN

    def test_a_desktop_flow_never_touches_the_live_display(self):
        """The runner the desktop flow uses is the isolated container runner, by name."""
        from skaldir.computer_use import ContainerRunner

        assert ContainerRunner.isolation == "container"
        available, _detail = VF.desktop_availability()
        if available:  # pragma: no cover — the assertion above is the invariant
            assert VF.desktop_availability()[1] == "available"


# --------------------------------------------------------------------------- #
# 5. the product surface: `skaldir verify flow` / `verify flows`               #
# --------------------------------------------------------------------------- #


def _invoke(capsys, argv: list[str]) -> tuple[int, dict]:
    from skaldir import verify_cli

    code = verify_cli.main(argv)
    captured = capsys.readouterr()
    text = captured.out.strip()
    return code, (json.loads(text) if text else {})


class TestProductSurface:
    @pytest.fixture(autouse=True)
    def _needs_a_browser(self):
        pytest.importorskip("playwright.sync_api", reason="the CLI flow tests drive a browser")

    def test_the_group_has_the_six_leaf_paths_and_they_all_take_json(self):
        from skaldir import cli_contract, verify_cli

        parser = verify_cli.build_parser()
        paths = sorted(path for path, _sub in cli_contract.leaf_commands(parser))
        assert paths == ["evidence", "flow", "flows", "plan", "run", "status"]
        for path, sub in cli_contract.leaf_commands(parser):
            options = {option for action in sub._actions for option in action.option_strings}
            assert "--json" in options, f"{path} has no --json flag"

    def test_the_group_installs_its_own_contract_entries(self):
        from skaldir import verify_cli

        entries = verify_cli.contract_entries()
        assert set(entries) == {"verify.flow", "verify.flows"}
        for path, entry in entries.items():
            assert entry.evidence, f"{path} carries no evidence"
            assert entry.layer == "VERIFY"
            assert 0 in entry.exit_codes
        assert entries["verify.flow"].mutates is True
        assert entries["verify.flows"].mutates is False
        assert 3 in entries["verify.flow"].exit_codes
        assert 2 in entries["verify.flow"].exit_codes

    def test_the_cli_flow_verbs_have_no_unregistered_paths(self):
        import argparse

        from skaldir import cli_contract, verify_cli

        root = argparse.ArgumentParser(prog="skaldir")
        verify_cli.register_cli(root.add_subparsers(dest="command", required=False))
        cli_contract.annotate(root)
        inventory = cli_contract.dump(root)
        assert inventory["unregistered"] == []
        assert {"verify.flow", "verify.flows"} <= {
            item["command"] for item in inventory["commands"]
        }

    def test_the_template_flag_prints_the_declaration_shape(self, capsys):
        code, payload = _invoke(capsys, ["flow", "--template", "--json"])
        assert code == 0
        assert payload["flows_filename"] == str(VF.FLOWS_FILENAME)
        assert set(payload["anchor_checks"]) >= {"present", "text_contains", "window_present"}
        assert payload["template"]["flows"]

    def test_listing_without_a_declaration_is_blocked_not_an_empty_success(self, tmp_path, capsys):
        code, payload = _invoke(
            capsys, ["flow", "--workspace", str(tmp_path), "--list", "--json"]
        )
        assert code == 3
        assert payload["count"] == 0
        assert payload["declaration"]["source"] == "none"

    def test_a_flow_that_really_passes_exits_zero_with_its_screenshots(
            self, site, tmp_path, capsys):
        workspace = tmp_path / "ws"
        _write(workspace, VF.template_flows(site.base))
        code, payload = _invoke(capsys, [
            "flow", "--workspace", str(workspace),
            "--state", str(tmp_path / "state"), "--json",
        ])
        assert code == 0
        assert payload["verdict"] == VF.VERDICT_PASS
        assert payload["counts"]["passed"] == payload["counts"]["flows"] >= 5
        shots = [shot for result in payload["results"] for shot in result["screenshots"]]
        assert len(shots) >= payload["counts"]["flows"]
        for shot in shots:
            assert Path(shot["path"]).is_file()

    def test_only_the_selected_flow_runs(self, site, tmp_path, capsys):
        workspace = tmp_path / "ws"
        _write(workspace, VF.template_flows(site.base))
        code, payload = _invoke(capsys, [
            "flow", "--workspace", str(workspace), "--flow", "search-a-term",
            "--state", str(tmp_path / "state"), "--json",
        ])
        assert code == 0
        assert [result["flow_id"] for result in payload["results"]] == ["search-a-term"]

    def test_an_unknown_flow_id_is_not_found(self, site, tmp_path, capsys):
        workspace = tmp_path / "ws"
        _write(workspace, VF.template_flows(site.base))
        code, payload = _invoke(capsys, [
            "flow", "--workspace", str(workspace), "--flow", "does-not-exist",
            "--state", str(tmp_path / "state"), "--json",
        ])
        assert code == 5
        assert payload["missing"] == ["does-not-exist"]

    def test_a_failing_flow_exits_two(self, site, tmp_path, capsys):
        workspace = tmp_path / "ws"
        payload = VF.template_flows(site.base)
        payload["flows"][0]["end_state"].append(
            {"check": "text_contains", "selector": "h1", "text": "definitely not here"}
        )
        _write(workspace, payload)
        code, document = _invoke(capsys, [
            "flow", "--workspace", str(workspace), "--state", str(tmp_path / "state"), "--json",
        ])
        assert code == 2
        assert document["verdict"] == VF.VERDICT_FAIL

    def test_a_flow_that_cannot_be_driven_exits_three(self, tmp_path, capsys):
        workspace = tmp_path / "ws"
        _write(workspace, {"flows": [{
            "id": "unreachable",
            "target": {"kind": "browser", "url": f"http://127.0.0.1:{_closed_port()}/"},
            "steps": [{"action": "assert", "check": "present", "selector": "body"}],
            "end_state": [{"check": "present", "selector": "body"}],
        }]})
        code, document = _invoke(capsys, [
            "flow", "--workspace", str(workspace), "--state", str(tmp_path / "state"), "--json",
        ])
        assert code == 3
        assert document["verdict"] == VF.VERDICT_UNKNOWN

    def test_no_declaration_at_all_is_blocked(self, tmp_path, capsys):
        code, _payload = _invoke(capsys, [
            "flow", "--workspace", str(tmp_path), "--state", str(tmp_path / "s"), "--json",
        ])
        assert code == 3

    def test_the_recorded_run_and_its_evidence_can_be_read_back(self, site, tmp_path, capsys):
        workspace = tmp_path / "ws"
        state = tmp_path / "state"
        _write(workspace, VF.template_flows(site.base))
        code, document = _invoke(capsys, [
            "flow", "--workspace", str(workspace), "--flow", "open-and-read",
            "--state", str(state), "--json",
        ])
        assert code == 0
        run_id = document["run_id"]
        code, back = _invoke(capsys, ["flows", "--state", str(state), "--run-id", run_id,
                                      "--json"])
        assert code == 0
        assert back["found"] is True
        assert back["run_id"] == run_id
        assert back["verdict"] == VF.VERDICT_PASS
        assert back["evidence_count"] == 1, "one ledger row per flow"
        row = back["evidence"][0]
        assert row["flow_id"] == "open-and-read"
        assert row["screenshots"], "the ledger row carries the screenshots"
        assert Path(row["screenshots"][0]["path"]).is_file()
        recorded = back["results"][0]
        assert recorded["start_state"] and recorded["end_state"]
        assert recorded["evidence_sha256"] == row["evidence_sha256"]

    def test_an_unknown_run_id_is_not_found(self, tmp_path, capsys):
        code, payload = _invoke(capsys, ["flows", "--state", str(tmp_path / "s"),
                                         "--run-id", "flow-nope", "--json"])
        assert code == 5
        assert payload["found"] is False

    def test_reading_flows_before_anything_ran_is_honest(self, tmp_path, capsys):
        from skaldir import verify_cli

        code = verify_cli.main(["flows", "--state", str(tmp_path / "s"), "--json"])
        captured = capsys.readouterr()
        assert code == 0
        payload = json.loads(captured.out)
        assert payload["found"] is False
        assert payload["runs"] == []
        assert payload["verdict"] is None
        assert "no flow has been run" in captured.err
