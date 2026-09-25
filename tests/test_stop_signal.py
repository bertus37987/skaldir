"""STOP-01..STOP-05: the double-Escape detector and the measured backend report.

Every assertion here maps to a sentence in ``docs/masterplan/01-MASTERPLAN.md`` §8.3. The
tests are pure logic plus one read-only environment probe — no display, no key press, no
sleep, and nothing that could move the user's mouse.
"""

from __future__ import annotations

import json
import time

import pytest

from skaldir.stop_signal import (
    DEFAULT_MIN_GAP_MS,
    DEFAULT_WINDOW_MS,
    Availability,
    BackendProbe,
    DoubleEscapeDetector,
    InputEvent,
    Origin,
    detect,
    probe_backends,
    write_report,
)

T0 = 1000.0


def esc(at: float, *, origin: Origin = Origin.USER, autorepeat: bool = False,
        pressed: bool = True) -> InputEvent:
    return InputEvent(key="esc", at=at, origin=origin, autorepeat=autorepeat, pressed=pressed)


# --------------------------------------------------------------------------- #
# STOP-01: two deliberate presses inside the window pause; autorepeat does not
# --------------------------------------------------------------------------- #


def test_two_presses_inside_the_window_fire_one_stop():
    det = DoubleEscapeDetector(window_ms=500)
    assert det.feed(esc(T0)) is None
    hit = det.feed(esc(T0 + 0.20))
    assert hit is not None
    assert hit.gap_ms == pytest.approx(200.0)
    assert hit.window_ms == 500
    assert hit.first_at == T0
    assert hit.second_at == T0 + 0.20


def test_the_gap_is_reported_not_hidden():
    det = DoubleEscapeDetector(window_ms=500)
    det.feed(esc(T0))
    hit = det.feed(esc(T0 + 0.499))
    assert hit is not None and hit.gap_ms == pytest.approx(499.0)
    assert hit.to_dict()["kind"] == "double_escape_stop"
    assert hit.to_dict()["gap_ms"] == pytest.approx(499.0)


def test_three_presses_fire_one_stop_not_two():
    det = DoubleEscapeDetector(window_ms=500)
    det.feed(esc(T0))
    assert det.feed(esc(T0 + 0.1)) is not None
    # the third press lands inside the window too, but the stop already fired
    assert det.feed(esc(T0 + 0.2)) is None
    # only after a full reset is a new double press possible
    det.reset()
    det.feed(esc(T0 + 0.3))
    assert det.feed(esc(T0 + 0.4)) is not None


def test_autorepeat_never_counts_as_two_presses():
    det = DoubleEscapeDetector(window_ms=500, min_gap_ms=30)
    det.feed(esc(T0))
    # a held key emits repeats ~10-50 ms apart, flagged autorepeat by evdev
    assert det.feed(esc(T0 + 0.012, autorepeat=True)) is None
    assert det.feed(esc(T0 + 0.024, autorepeat=True)) is None
    assert det.feed(esc(T0 + 0.036, autorepeat=True)) is None


def test_a_gap_below_min_gap_is_treated_as_autorepeat_even_without_the_flag():
    det = DoubleEscapeDetector(window_ms=500, min_gap_ms=30)
    det.feed(esc(T0))
    assert det.feed(esc(T0 + 0.010)) is None  # 10 ms: too fast to be a deliberate press
    assert det.feed(esc(T0 + 0.018)) is None
    # and the arming press did NOT slide forward, so a later real press still fires
    hit = det.feed(esc(T0 + 0.150))
    assert hit is not None and hit.first_at == T0


def test_a_press_outside_the_window_re_arms_instead_of_firing():
    det = DoubleEscapeDetector(window_ms=500)
    det.feed(esc(T0))
    assert det.feed(esc(T0 + 0.9)) is None  # 900 ms later: a single, unrelated Escape
    hit = det.feed(esc(T0 + 1.1))
    assert hit is not None
    assert hit.first_at == T0 + 0.9  # the second press paired with the *new* one


def test_the_window_is_configurable_and_the_default_is_500ms():
    assert DEFAULT_WINDOW_MS == 500
    assert DEFAULT_MIN_GAP_MS == 30
    det = DoubleEscapeDetector(window_ms=120)
    det.feed(esc(T0))
    assert det.feed(esc(T0 + 0.30)) is None  # outside the short window
    det.feed(esc(T0 + 0.31))
    assert det.feed(esc(T0 + 0.35)) is not None


# --------------------------------------------------------------------------- #
# STOP-03: the agent's own Escape is never a user stop
# --------------------------------------------------------------------------- #


def test_synthetic_escape_from_the_agent_never_fires():
    det = DoubleEscapeDetector(window_ms=500)
    assert det.feed(esc(T0, origin=Origin.AGENT)) is None
    assert det.feed(esc(T0 + 0.1, origin=Origin.AGENT)) is None
    assert det.feed(esc(T0 + 0.2, origin=Origin.SYNTHETIC)) is None
    assert det.feed(esc(T0 + 0.3, origin=Origin.UNKNOWN)) is None


def test_a_mixed_origin_sequence_never_completes():
    det = DoubleEscapeDetector(window_ms=500)
    det.feed(esc(T0, origin=Origin.USER))
    assert det.feed(esc(T0 + 0.1, origin=Origin.AGENT)) is None
    # the agent press did not arm, so this user press only arms again
    assert det.feed(esc(T0 + 0.2, origin=Origin.USER)) is None


def test_a_release_never_arms_and_never_fires():
    det = DoubleEscapeDetector(window_ms=500)
    assert det.feed(esc(T0, pressed=False)) is None
    assert det.feed(esc(T0 + 0.1)) is None  # only one press -> armed, no fire
    assert det.feed(esc(T0 + 0.15, pressed=False)) is None


def test_no_key_history_is_kept_beyond_the_arming_press():
    det = DoubleEscapeDetector()
    det.feed(esc(T0))
    assert det._armed_at == T0
    det.feed(InputEvent(key="a", at=T0 + 0.05))
    assert det._armed_at == T0  # a foreign key changed nothing
    assert not hasattr(det, "_events")


def test_the_detector_only_ever_reads_escape_signals():
    det = DoubleEscapeDetector(window_ms=500)
    det.feed(InputEvent(key="f12", at=T0))
    det.feed(InputEvent(key="f12", at=T0 + 0.05))
    assert det.feed(esc(T0 + 0.1)) is None  # one Escape only: armed, not fired


def test_detect_helper_runs_a_whole_list():
    assert detect([esc(T0), esc(T0 + 0.2)]) is not None
    assert detect([esc(T0)]) is None
    assert detect([esc(T0), esc(T0 + 2.0), esc(T0 + 2.05)]) is not None


def test_a_clock_that_goes_backwards_re_arms_rather_than_firing():
    det = DoubleEscapeDetector(window_ms=500)
    det.feed(esc(T0))
    assert det.feed(esc(T0 - 5.0)) is None
    assert det._armed_at == T0 - 5.0


# --------------------------------------------------------------------------- #
# STOP-04 / STOP-05: measured availability, and no false "complete"
# --------------------------------------------------------------------------- #


def test_probe_backends_measures_every_candidate_without_writing():
    probes = probe_backends()
    names = [p.name for p in probes]
    assert names == ["evdev", "libinput", "xdg-global-shortcuts", "x11-record",
                     "agent-injection"]
    for probe in probes:
        assert isinstance(probe, BackendProbe)
        assert probe.availability in Availability
        # every non-available verdict must name what would unblock it (STOP-05)
        if probe.availability is not Availability.AVAILABLE:
            assert probe.reason, probe.name
            assert probe.unblock, probe.name


def test_agent_injection_is_never_a_stop_source():
    for probe in probe_backends():
        if probe.name == "agent-injection":
            assert "origin=agent" in probe.detail["note"]
            assert "never fire a stop" in probe.detail["note"]
            return
    raise AssertionError("agent-injection probe missing")


def test_the_report_never_claims_the_double_escape_is_proven(tmp_path):
    target = tmp_path / "feasibility.json"
    report = write_report(target, session={"type": "wayland", "desktop": "GNOME"})
    on_disk = json.loads(target.read_text(encoding="utf-8"))
    assert on_disk == report
    verdict = report["verdict"]
    # STOP-05: the honest verdict is "not proven" until a backend delivers it
    assert verdict["double_escape_proven"] is False
    assert "NOT declared complete" in verdict["statement"]
    assert report["window_ms"] == 500
    assert report["min_gap_ms"] == 30
    assert report["session"] == {"type": "wayland", "desktop": "GNOME"}


def test_the_global_shortcuts_probe_never_claims_the_sequence_works():
    for probe in probe_backends():
        if probe.name != "xdg-global-shortcuts":
            continue
        if probe.availability is Availability.AVAILABLE:
            assert probe.detail["double_escape_sequence"] == "unproven"
            assert "does not guarantee" in probe.detail["double_escape_note"]
        return
    raise AssertionError("xdg-global-shortcuts probe missing")


def test_report_is_written_under_a_new_directory(tmp_path):
    target = tmp_path / "nested" / "dir" / "r.json"
    write_report(target)
    assert target.exists()
    assert json.loads(target.read_text(encoding="utf-8"))["kind"] == "skaldir.stop-feasibility"


def test_a_first_press_at_exactly_zero_is_a_real_timestamp_not_a_missing_one():
    # Regression: `event.at or clock` treated 0.0 as "no timestamp", so a synthetic
    # sequence starting at 0.0 lost its first press and never fired. Measured before the
    # fix: `stop detect --gap-ms 200` reported stopped:false for two deliberate presses.
    det = DoubleEscapeDetector(window_ms=500)
    assert det.feed(esc(0.0), now=0.0) is None
    hit = det.feed(esc(0.2), now=0.2)
    assert hit is not None
    assert hit.first_at == 0.0
    assert hit.gap_ms == pytest.approx(200.0)


def test_a_missing_timestamp_falls_back_to_the_injected_clock():
    det = DoubleEscapeDetector(window_ms=500)
    assert det.feed(InputEvent(key="esc"), now=10.0) is None
    assert det.feed(InputEvent(key="esc"), now=10.2) is not None


def test_a_list_only_libinput_probe_is_not_availability():
    # `libinput list-devices` exits 0 even when it cannot open the event nodes; listing
    # devices is not the ability to read their events.
    for probe in probe_backends():
        if probe.name != "libinput":
            continue
        if probe.availability is Availability.AVAILABLE:
            assert probe.detail.get("denied") is not True
        else:
            assert "group 'input'" in probe.unblock
        return
    raise AssertionError("libinput probe missing")


def test_agent_injection_never_counts_as_a_usable_stop_backend(tmp_path):
    report = write_report(tmp_path / "stop-report.json")
    assert "agent-injection" not in report["verdict"]["usable_backends"]


def test_stop_signal_carries_a_monotonic_timestamp():
    det = DoubleEscapeDetector(window_ms=500)
    det.feed(esc(T0))
    hit = det.feed(esc(T0 + 0.2), now=T0 + 0.2)
    assert hit is not None
    assert hit.at == T0 + 0.2
    assert isinstance(hit.gap_ms, float)
