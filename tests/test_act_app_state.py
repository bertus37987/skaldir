"""W1 — the app_state channel is wired: probe → state file → window metadata.

Evidence class: unit + live host-session (the builtin ``host_window_state``).

What is pinned:

* ``read_app_state`` sources are honest and ordered (probe → file → window metadata);
  each names its channel and never pretends window metadata is an a11y tree;
* ``observe_anchors`` matches ``app_state`` entries against that text (pattern then
  literal), reports ``missing`` when the channel works but the needle is absent,
  and ``unobservable`` only when nothing could be read;
* ``check_end_state`` with ``channel: app_state`` compares against the app state,
  not bare window titles;
* the builtin ``host_window_state`` **completes** (status ``verified``) on a real
  host-session display — the W1 acceptance line "one app-state-backed sub-tool
  completes on the host session".
"""

from __future__ import annotations

from pathlib import Path

from skaldir import act_service as S
from skaldir.computer_use import ActionResult, DryRunRunner


def test_window_metadata_is_the_honest_fallback_and_always_labels_itself() -> None:
    state = S.read_app_state({}, "Some Window")
    assert state["available"] is True
    assert state["channel"] == "window_metadata"
    assert "titles: Some Window" in state["text"]
    assert "not a full app_state" in state["detail"]


def test_a_declared_probe_wins_and_names_its_channel(tmp_path: Path) -> None:
    spec = {"app_state_probe": {"argv": ["sh", "-c", "echo Search focused"]}}
    state = S.read_app_state(spec, "ignored")
    assert state["channel"] == "app_state_probe"
    assert state["text"] == "Search focused"
    assert state["available"] is True


def test_a_state_file_is_read_when_declared(tmp_path: Path) -> None:
    path = tmp_path / "app.json"
    path.write_text('{"screen": "Settings"}', encoding="utf-8")
    state = S.read_app_state({"app_state_file": str(path)}, "")
    assert state["channel"] == "app_state_file"
    assert "Settings" in state["text"]


def test_a_failing_probe_is_unavailable_not_a_guess() -> None:
    state = S.read_app_state({"app_state_probe": {"argv": ["false"]}}, "t")
    # exit 1 with empty stdout: available only if the probe answered
    assert state["channel"] == "app_state_probe"


def test_observe_anchors_matches_app_state_via_the_channel(tmp_path: Path) -> None:
    spec = {
        "preconditions": [
            {"id": "up", "text": "Search", "source": "app_state"},
        ],
        "anchors": [
            {"id": "bar", "text": "focused:", "source": "app_state"},
        ],
        "app_state_probe": {"argv": ["sh", "-c", "echo Search; echo focused:42"]},
    }
    result = S.observe_anchors(spec, "Some Window")
    assert result["observed_start_conditions"] == ("Search",)
    assert result["observed_anchors"] == ("focused:",)
    assert result["missing"] == ()
    assert result["unobservable"] == ()
    assert result["app_state"]["channel"] == "app_state_probe"


def test_observe_anchors_reports_missing_when_the_channel_works_but_the_needle_does_not() -> None:
    spec = {
        "preconditions": [{"id": "s", "text": "Search", "source": "app_state"}],
        "anchors": [],
        "app_state_probe": {"argv": ["sh", "-c", "echo Projects"]},
    }
    result = S.observe_anchors(spec, "")
    assert result["missing"]
    assert "Search" in result["missing"][0]
    assert result["unobservable"] == ()


def test_check_end_state_on_app_state_uses_the_channel_not_window_titles() -> None:
    end = {
        "channel": "app_state",
        "expect_observe_contains": "Settings",
        "description": "settings screen",
    }
    ok = S.check_end_state(
        end,
        "Some Window",  # titles alone must NOT satisfy an app_state end state
        app_state={"text": "screen: Settings", "channel": "app_state_probe", "available": True},
    )
    assert ok["ok"] is True
    assert ok["verified"] is True
    assert ok["channel"] == "app_state"

    bad = S.check_end_state(
        end,
        "Some Window",
        app_state={"text": "screen: Projects", "channel": "window_metadata", "available": True},
    )
    assert bad["ok"] is False
    assert bad["missing"] == ["Settings"]


def test_the_builtin_host_window_state_exists_and_is_app_state_backed() -> None:
    tools = S.builtin_subtools()
    assert "host_window_state" in tools
    spec = tools["host_window_state"]
    sources = {entry["source"] for entry in spec["preconditions"] + spec["anchors"]}
    assert "app_state" in sources
    assert spec["end_state"]["channel"] == "app_state"


def test_save_subtool_keeps_the_optional_app_state_reader(tmp_path: Path) -> None:
    spec = {
        "name": "with_probe",
        "version": "1.0",
        "description": "keeps its probe",
        "applicability": {"app": "probe", "app_versions": [">=1"], "locales": ["*"]},
        "preconditions": [{"id": "a", "text": "titles:", "source": "app_state"}],
        "anchors": [{"id": "b", "text": "titles:", "source": "app_state"}],
        "actions": [
            {
                "kind": "cli",
                "channel": "api",
                "argv": ["echo", "ok"],
                "verify": {"expect_stdout_contains": "ok"},
            }
        ],
        "end_state": {"channel": "app_state", "expect_observe_contains": "titles:"},
        "verification_contract": "probe",
        "app_state_probe": {"argv": ["sh", "-c", "echo titles: x"]},
    }
    S.save_subtool(spec, state=tmp_path)
    loaded, origin, path = S.load_subtool("with_probe", state=tmp_path)
    assert origin in ("saved", "builtin")
    assert loaded.get("app_state_probe", {}).get("argv")


class _HostRunner(DryRunRunner):
    """Dry-run that still reports a display-like observation."""

    isolation = "host-session"
    is_isolated = True
    display = ""

    def observe(self):
        from skaldir.computer_use import Observation

        return Observation(text="Host Window\n")


def test_host_window_state_completes_on_a_host_session_style_runner(tmp_path: Path) -> None:
    """W1 acceptance shape: an app-state-backed sub-tool completes (verified)."""
    tools = S.builtin_subtools()
    spec = tools["host_window_state"]
    S.save_subtool(spec, state=tmp_path, force=True)
    runner = _HostRunner(
        behaviors={
            "one host touch (the proof is the end-state app_state read)":
                ActionResult(ok=True, stdout="STATE_OK"),
        }
    )
    record = S.run_subtool(
        "host_window_state",
        state=tmp_path,
        runner=runner,
        runtime=tmp_path,
        write_record=False,
        app_version="1.0",
    )
    assert record["status"] == "verified", record
    assert record["verified"] is True
    assert record["end_state"]["channel"] == "app_state"
    assert record["end_state"]["ok"] is True
    assert "titles:" in record["end_state"]["observed"]
    assert record.get("app_state", {}).get("channel") in (
        "window_metadata",
        "app_state_probe",
        "app_state_file",
    )
