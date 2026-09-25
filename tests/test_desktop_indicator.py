"""DESK-03: the visible host corner indicator, and the honesty of its status.

The requirement is a *small status bar in one corner* that is visible while control is
held, "auch ohne fokussierte TUI", and that shows exactly controls / waits / paused with
"controls" only under a valid lease. These tests pin the parts that do not need a display
— placement, text, and above all the refusal path: a module that cannot show anything must
say so rather than let a caller report "indicator shown".
"""

from __future__ import annotations

from skaldir.desktop_indicator import (
    BOX_MARGIN_PX,
    DEFAULT_WIDTH_CELLS,
    FontMetrics,
    HostIndicator,
    IndicatorStatus,
    layout_geometry,
    render_box,
    status,
)
from skaldir.desktop_lease import IndicatorState, LeaseState


# --------------------------------------------------------------------------- #
# the box: exactly three confirmed lines, in the confirmed wording
# --------------------------------------------------------------------------- #


def test_the_box_is_the_confirmed_three_lines_in_the_confirmed_wording():
    lines = render_box(IndicatorState(state=LeaseState.CONTROLLING, task="Edit video",
                                      run_id="42"), width_cells=34)
    assert len(lines) == 3
    assert lines[0].rstrip() == "Skaldir controls the desktop"
    assert lines[2].rstrip() == "Esc, Esc to pause"
    assert all(len(line) == 34 for line in lines)


def test_the_box_never_says_controls_without_a_lease():
    for state in (LeaseState.WAITING, LeaseState.PAUSED, LeaseState.FREE):
        head = render_box(IndicatorState(state=state, task="T"), width_cells=30)[0]
        assert "controls the desktop" not in head, state


def test_render_box_reuses_the_lease_module_so_the_statements_cannot_drift():
    import inspect

    from skaldir import desktop_indicator
    assert "indicator_lines" in inspect.getsource(desktop_indicator.render_box)


# --------------------------------------------------------------------------- #
# placement: a corner, small, and never off-screen
# --------------------------------------------------------------------------- #


def test_the_box_sits_in_the_bottom_right_corner_by_default():
    geom = layout_geometry(1920, 1080, lines=3, width_cells=34)
    assert geom.corner == "bottom-right"
    assert geom.x + geom.w <= 1920 - BOX_MARGIN_PX + 1
    assert geom.y + geom.h <= 1080 - BOX_MARGIN_PX + 1
    assert geom.x > 1920 // 2, "bottom-right means the right half"
    assert geom.y > 1080 // 2, "bottom-right means the lower half"


def test_every_corner_is_reachable_and_stays_inside_the_screen():
    for corner in ("top-left", "top-right", "bottom-left", "bottom-right"):
        geom = layout_geometry(1280, 800, lines=3, width_cells=34, corner=corner)
        assert 0 <= geom.x < 1280, corner
        assert 0 <= geom.y < 800, corner
        assert geom.x + geom.w <= 1280, corner
        assert geom.y + geom.h <= 800, corner
        assert geom.corner == corner


def test_the_box_is_small_and_never_takes_over_the_screen():
    geom = layout_geometry(1920, 1080, lines=3, width_cells=34)
    metrics = FontMetrics()
    assert geom.w <= metrics.text_width(34)
    assert geom.h <= 3 * metrics.line_height_px + 8
    assert geom.w * geom.h < (1920 * 1080) * 0.05, "a corner bar, not a banner"


def test_a_tiny_screen_clamps_instead_of_drifting_off_screen():
    geom = layout_geometry(80, 40, lines=3, width_cells=34)
    assert geom.w > 0 and geom.h > 0
    assert geom.x + geom.w <= 80
    assert geom.y + geom.h <= 40


def test_geometry_is_machine_readable():
    geom = layout_geometry(1024, 768, lines=3, width_cells=34)
    payload = geom.to_dict()
    assert payload["screen"] == [1024, 768]
    assert payload["w"] == geom.w and payload["h"] == geom.h
    assert payload["corner"] == "bottom-right"


# --------------------------------------------------------------------------- #
# the honesty rule: no display -> "unavailable", never a silent success
# --------------------------------------------------------------------------- #


def test_without_a_display_the_indicator_refuses_instead_of_pretending(monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    indicator = HostIndicator()
    result = indicator.show(IndicatorState(state=LeaseState.CONTROLLING, task="T"))
    assert result.state == "unavailable"
    assert result.visible is False
    assert "DISPLAY" in result.reason
    assert indicator.status().visible is False


def test_a_refusal_still_carries_the_text_it_would_have_drawn(monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    indicator = HostIndicator()
    result = indicator.show(IndicatorState(state=LeaseState.PAUSED, task="Edit video"))
    assert result.lines and result.lines[0].rstrip() == "Skaldir desktop control paused"


def test_the_status_never_reports_visible_without_a_window(monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    indicator = HostIndicator()
    assert IndicatorStatus().visible is False
    indicator.show(IndicatorState())
    assert indicator.status().to_dict()["visible"] is False


def test_hide_without_a_window_is_honest_not_a_crash(monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    indicator = HostIndicator()
    assert indicator.hide().state in ("hidden", "unavailable")


def test_close_is_safe_without_ever_having_shown_anything():
    indicator = HostIndicator()
    indicator.close()
    assert indicator.status().visible is False


def test_update_without_a_display_refuses_too(monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    indicator = HostIndicator()
    assert indicator.update(IndicatorState()).state == "unavailable"


# --------------------------------------------------------------------------- #
# the backend is named, not implied (DESK-03 must fit the measured backend)
# --------------------------------------------------------------------------- #


def test_status_names_the_backend_and_denies_being_native_wayland():
    report = status()
    assert report["backend"] == "x11-xwayland"
    assert report["native_wayland_layer_shell"] is False
    assert "NOT a native Wayland layer-shell" in report["note"]


def test_status_is_measured_not_assumed(monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    report = status()
    assert report["state"] == "unavailable"
    assert "DISPLAY" in report["reason"]
    assert report["display"] == ""


def test_status_reports_available_when_a_display_is_there(monkeypatch):
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    report = status()
    # python-xlib is a declared extra; either it is there (available) or it is named
    assert report["state"] in ("available", "unavailable")
    if report["state"] == "unavailable":
        assert "python-xlib" in report["reason"]
    assert report["display"] == ":0"
    assert report["wayland_display"] is True


def test_the_default_width_is_the_three_line_bar():
    assert DEFAULT_WIDTH_CELLS == 34
    assert len(render_box(IndicatorState(), width_cells=DEFAULT_WIDTH_CELLS)) == 3
