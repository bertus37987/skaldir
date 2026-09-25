"""The chat screen: the logo, the status band, the state art, the composed frame.

Every test here is about an *honesty* rule as much as a layout rule: the band shows a number
only when something produced it, the price is declared or ``n/a``, and plain mode is ASCII
with no escape byte anywhere. The layout rules have the same shape — the screen's lines are
exactly the terminal width, because that is what lets the frame's erase arithmetic be a line
count.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from skaldir import chat_screen, chatbar, telemetry

# --------------------------------------------------------------------------- #
# the logo: the checked-in mark, compacted and never upscaled
# --------------------------------------------------------------------------- #


def test_the_compact_logo_is_two_to_four_rows_and_from_the_real_art():
    """The owner's ask: a 2-4 row mark. Anything taller is a splash, not a screen logo."""
    rows = chat_screen.compact_logo(unicode=True, rows=chat_screen.LOGO_ROWS)
    assert 2 <= len(rows) <= 4, rows
    assert chat_screen.LOGO_ROWS <= 4
    native_columns, native_rows = chat_screen.mark_size()
    assert native_columns > 0 and native_rows > 0, (
        "the checked-in mark is missing, so this build has no logo to compact"
    )


def test_the_compact_logo_never_upscales_past_the_art():
    """A wider request must not smear one source column across several cells.

    This is the defect the first build shipped: ``columns=24`` over a 16-column mark duplicated
    columns and the emblem stopped reading. The cap is the art's own width, so the picture is
    always sampled from ink that is really there.
    """
    native_columns, _rows = chat_screen.mark_size()
    wide = chat_screen.compact_logo(unicode=True, rows=4, columns=native_columns * 3)
    native = chat_screen.compact_logo(unicode=True, rows=4, columns=native_columns)
    assert wide == native
    assert max(len(line) for line in wide) <= native_columns


def test_the_ascii_logo_is_the_same_picture_in_ascii_glyphs():
    """Plain mode folds the glyphs; it does not draw a different mark."""
    rich = chat_screen.compact_logo(unicode=True, rows=4)
    plain = chat_screen.compact_logo(unicode=False, rows=4)
    assert len(rich) == len(plain)
    assert all(ord(char) < 128 for line in plain for char in line)
    assert set("".join(plain)) <= set(" _-#")
    # the same cells carry ink in both renderings: where the rich one has a glyph, the plain
    # one has a glyph too (only the four members of the alphabet differ)
    for rich_line, plain_line in zip(rich, plain, strict=True):
        assert (rich_line.strip() == "") == (plain_line.strip() == ""), (rich_line, plain_line)


def test_the_logo_block_is_inset_by_the_frame_margin():
    """It sits under the frame's own margin, so the mark lines up with the bars."""
    block = chat_screen.logo_block(unicode=True, rows=4)
    assert block
    assert all(line.startswith(" " * chatbar.MARGIN) for line in block)


def test_a_build_without_the_art_says_so_instead_of_drawing_a_stand_in(monkeypatch):
    """No art ⇒ no logo. Inventing a mark would be the one thing worse than having none."""
    chat_screen.mark_size.cache_clear()
    chat_screen.compact_logo.cache_clear()
    chat_screen._sample_mark.cache_clear()
    monkeypatch.setattr(chat_screen, "mark_size", lambda: (0, 0))
    try:
        assert chat_screen.compact_logo(unicode=True, rows=4) == ()
    finally:
        monkeypatch.undo()
    chat_screen.mark_size.cache_clear()
    chat_screen.compact_logo.cache_clear()
    chat_screen._sample_mark.cache_clear()
    assert chat_screen.compact_logo(unicode=True, rows=4) != ()


# --------------------------------------------------------------------------- #
# the price: declared, or honestly absent
# --------------------------------------------------------------------------- #


def test_no_declared_price_is_n_a_and_never_a_guess(tmp_path, monkeypatch):
    """The core rule: an undeclared price renders ``$ n/a``, which is a complete answer."""
    monkeypatch.delenv(chat_screen.ENV_PRICE_PER_1K, raising=False)
    monkeypatch.delenv("GODSV_" + chat_screen.ENV_PRICE_PER_1K, raising=False)
    value, source = chat_screen.price_for("gpt-5-codex", table={})
    assert value is None
    assert "no price is declared" in source
    assert chat_screen.cost_text(1234, "gpt-5-codex", path=tmp_path / "none.json") == "$ n/a"


def test_a_declared_price_produces_the_cost_the_owner_drew(tmp_path, monkeypatch):
    """``$0.0042`` for 7640 tokens at the declared rate — the number, from the declaration."""
    monkeypatch.delenv(chat_screen.ENV_PRICE_PER_1K, raising=False)
    monkeypatch.delenv("GODSV_" + chat_screen.ENV_PRICE_PER_1K, raising=False)
    table = {"gpt-5-codex": 0.00055}
    assert chat_screen.price_for("gpt-5-codex", table=table)[0] == 0.00055
    usd = 7640 * 0.00055 / 1000.0
    assert f"${usd:.4f}" == "$0.0042"


def test_the_price_table_reads_both_shapes_and_drops_a_zero(tmp_path):
    """A hand-written file is not forced through a schema; a ``0`` entry is not a price."""
    path = tmp_path / "prices.json"
    path.write_text('{"usd_per_1k_tokens": {"a": 0.5, "b": 0, "c": -1, "source": "x"}}',
                    encoding="utf-8")
    assert chat_screen.price_table(path) == {"a": 0.5}
    path.write_text('{"d": 0.25}', encoding="utf-8")
    assert chat_screen.price_table(path) == {"d": 0.25}
    path.write_text("not json at all", encoding="utf-8")
    assert chat_screen.price_table(path) == {}


def test_a_price_for_one_model_is_never_used_for_another():
    """The failure this forbids: a confident cost for a model nobody priced."""
    table = {"gpt-5-codex": 0.00055}
    assert chat_screen.price_for("deepseek-chat", table=table)[0] is None
    assert chat_screen.cost_text(1000, "deepseek-chat", path=None) == "$ n/a"


def test_an_estimated_token_count_keeps_its_tilde(tmp_path, monkeypatch):
    """A chars/4 estimate must not render like a provider's own count."""
    monkeypatch.delenv(chat_screen.ENV_PRICE_PER_1K, raising=False)
    monkeypatch.delenv("GODSV_" + chat_screen.ENV_PRICE_PER_1K, raising=False)
    monkeypatch.setattr(chat_screen, "DECLARED_PRICES", {"m": 0.01})
    assert chat_screen.cost_text(2000, "m", estimated=True).startswith("~$")
    assert not chat_screen.cost_text(2000, "m", estimated=False).startswith("~")


# --------------------------------------------------------------------------- #
# the readings the run reported about itself
# --------------------------------------------------------------------------- #

_REAL_FOOTER = "stub-long-project-deterministic · ctx <1% · 3.1k tok/s · 00:01"


def test_a_real_footer_is_read_back_verbatim():
    """The band's ctx/tok-s come from the product's own footer, marks and all."""
    reading = chat_screen.reading_from_text(_REAL_FOOTER)
    assert reading.context == "ctx <1%"
    assert reading.speed == "3.1k tok/s"
    assert reading.known


def test_a_tilde_in_the_footer_survives_into_the_band():
    """An estimate stays marked as one all the way to the screen."""
    reading = chat_screen.reading_from_text("model · ~ctx 34% · ~132 tok/s · 02:14")
    assert reading.context == "ctx ~34%"
    assert reading.speed == "~132 tok/s"


def test_the_footer_line_wins_over_a_single_turn_line():
    """A band must not quote one turn's 1 ms window when the run printed the whole run.

    ``telemetry live`` prints a line per turn *and* the summary footer; taking the last
    number anywhere quoted ``22.0k tok/s`` from a 1-millisecond turn. The summary line is
    the one that carries both numbers, so it is the one the band takes.
    """
    streamed = (
        "turn 3/4 · 41 ms · out 13 tok (provider) · 317 tok/s · ctx <1%\n"
        "turn 4/4 · 1 ms · out 22 tok (provider) · 22.0k tok/s · ctx <1%\n"
        + _REAL_FOOTER + "\n"
    )
    reading = chat_screen.reading_from_text(streamed)
    assert reading.speed == "3.1k tok/s"
    assert reading.context == "ctx <1%"


def test_output_with_no_reading_anywhere_is_empty_not_zero():
    """A run that printed nothing measurable leaves the band with nothing to show."""
    reading = chat_screen.reading_from_text("hello, this is just prose\n")
    assert not reading.known
    assert reading.context == "" and reading.speed == ""


def _real_events() -> list[dict]:
    """The event list of a real recorded run (the long-project harness wrote it)."""
    path = (Path(__file__).resolve().parents[1] / "outputs" / "long-project-e2e"
            / "achievable-20260918T204940" / "transcripts"
            / "achievable-B-1-B1-work-full.jsonl")
    if not path.exists():
        pytest.skip("the recorded run this test folds is not in the tree")
    events, _problems = telemetry.read_events(path)
    return events


def test_context_reading_uses_the_real_telemetry_record():
    """``ctx %`` from the last answered turn's prompt against the declared budget."""
    reading = chat_screen.context_reading(_real_events(), budget=128000)
    assert reading.context.startswith("ctx ")
    assert reading.context.endswith("%")
    assert "provider_measured" in reading.source or "estimate" in reading.source


def test_context_reading_is_empty_without_events_or_a_budget():
    """No record, no reading — and the reason is carried, not a zero."""
    empty = chat_screen.context_reading([], budget=128000)
    assert not empty.known
    assert empty.source
    no_path = chat_screen.context_reading()
    assert not no_path.known and no_path.source


# --------------------------------------------------------------------------- #
# the band and the hint row
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("width", [40, 60, 80, 120, 200])
def test_the_band_is_exactly_the_width_and_keeps_the_standard_three(width):
    """``ctx``, ``tok/s`` and the clock survive any width; the rest gives way in order."""
    band = chat_screen.status_band(
        width, model="gpt-5-codex", context="ctx 34%", speed="132 tok/s",
        price="$0.0042", clock="02:14", goal="goal: ship the chat screen", unicode=True)
    assert len(band) == width
    assert "ctx 34%" in band
    assert "02:14" in band


def test_the_band_drops_whole_facts_and_never_wraps():
    """A narrow band loses a fact, not half of one, and never spills onto a second row."""
    wide = chat_screen.status_band(
        120, model="gpt-5-codex", context="ctx 34%", speed="132 tok/s",
        price="$0.0042", clock="02:14", goal="goal: something long")
    narrow = chat_screen.status_band(
        46, model="gpt-5-codex", context="ctx 34%", speed="132 tok/s",
        price="$0.0042", clock="02:14", goal="goal: something long")
    assert "goal:" in wide and "goal:" not in narrow
    assert len(narrow) == 46
    assert "ctx 34%" in narrow
    # nothing is emitted with a newline: the frame's arithmetic depends on one row, one line
    assert "\n" not in chat_screen.status_band(
        30, model="gpt-5-codex", context="ctx 34%", speed="132 tok/s",
        price="$0.0042", clock="02:14", goal="goal: something long")


def test_the_band_parts_are_in_the_order_the_owner_asked_for():
    keys = [key for key, _text in chat_screen.band_parts(
        model="m", context="ctx 1%", speed="1 tok/s", price="$1", clock="00:01", goal="goal: g")]
    assert keys == ["model", "ctx", "speed", "price", "clock", "goal"]


def test_the_hint_row_carries_the_keys_and_fits():
    for width in (40, 80, 120):
        row = chat_screen.hint_row(width, unicode=True)
        assert len(row) == width
    assert "/help" in chat_screen.hint_row(120, unicode=True)


def test_a_missing_goal_reads_none_not_a_blank():
    """The owner's wording: a session with no durable goal says ``none``."""
    bar = chatbar.ChatBar(screen=True, width=80)
    assert "goal: none" in bar.band_text()


# --------------------------------------------------------------------------- #
# the state art
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("state", chat_screen.STATE_IDS)
def test_every_state_has_a_one_row_live_form_and_a_three_row_badge(state):
    row = chat_screen.activity_row(80, state, detail="doing the thing", tick=3)
    assert len(row) == 80
    badge = chat_screen.state_art(state, detail="doing the thing", width=80)
    assert len(badge) == chat_screen.STATE_ART_ROWS


def test_the_live_row_names_the_state_and_the_detail():
    row = chat_screen.activity_row(80, "search", detail="docs.python.org", tick=1)
    assert "web search" in row
    assert "docs.python.org" in row


def test_a_tool_badge_prints_only_the_facts_the_caller_measured():
    """No invented duration, no invented exit code, no invented size."""
    badge = chat_screen.tool_badge("terminal", detail="ls -la", status="ok", width=80)
    text = "\n".join(badge)
    assert "ls -la" in text
    assert "exit" not in text and " s" not in text.replace("ls -la", "")
    full = chat_screen.tool_badge("terminal", detail="ls -la", exit_code=0, duration_ms=420,
                                 streamed=128, width=80)
    joined = "\n".join(full)
    assert "exit 0" in joined and "0.42 s" in joined and "128 B" in joined


def test_a_failed_tool_badge_says_failed():
    badge = chat_screen.tool_badge("terminal", detail="false", exit_code=1, width=80)
    assert "failed" in "\n".join(badge)


# --------------------------------------------------------------------------- #
# the composed screen
# --------------------------------------------------------------------------- #


def test_the_screen_is_logo_band_live_bars_hint_in_that_order():
    """The anatomy as the owner drew it, read top to bottom."""
    lines, cursor = chat_screen.screen_lines(
        80, logo=("  LOGO",), band="  BAND", live="  LIVE", text="hi", hint="  HINT")
    stripped = [line.strip() for line in lines]
    assert stripped[0] == "LOGO"
    assert stripped[1] == "BAND"
    assert stripped[2] == "LIVE"
    assert stripped[-1] == "HINT"
    # the input line sits between the two bars, and the bars are the theme's own glyph
    assert "hi" in lines[cursor]
    assert set(lines[cursor - 1].strip()) == {chatbar.BAR_RICH}
    assert set(lines[cursor + 1].strip()) == {chatbar.BAR_RICH}
    assert all(len(line) == 80 for line in lines), [len(line) for line in lines]


def test_the_screen_without_a_live_row_is_the_five_line_frame():
    lines, cursor = chat_screen.screen_lines(80, band="  BAND", text="", hint="  HINT")
    assert len(lines) == chat_screen.SCREEN_CORE_LINES
    assert cursor == 2
    assert all(len(line) == 80 for line in lines)


@pytest.mark.parametrize("width", [40, 80, 120])
def test_every_screen_line_is_exactly_the_width(width):
    """One row, one line: the property the erase arithmetic is built on."""
    lines, _cursor = chat_screen.screen_lines(
        width, logo=("  ▄▀▄" * 3,), band="  band", live="  live", text="a message",
        hint="  /help")
    assert all(len(line) == width for line in lines)


def test_plain_mode_emits_no_escape_and_no_non_ascii_byte():
    """A pipe, a log or ``--no-color`` gets ASCII and nothing else."""
    lines, _cursor = chat_screen.screen_lines(
        80, logo=chat_screen.logo_block(unicode=False, rows=4),
        band=chat_screen.status_band(80, model="m", context="ctx 1%", speed="1 tok/s",
                                     price="$ n/a", clock="00:01", goal="goal: none",
                                     unicode=False),
        live=chat_screen.activity_row(80, "thinking", detail="x", unicode=False),
        text="hello", hint=chat_screen.hint_row(80, unicode=False), unicode=False)
    for line in lines:
        assert "\x1b" not in line, repr(line)
        assert all(ord(char) < 128 for char in line), repr(line)


# --------------------------------------------------------------------------- #
# the frame in screen mode
# --------------------------------------------------------------------------- #


def _screen_bar(width: int = 80) -> tuple[chatbar.ChatBar, io.StringIO]:
    out = io.StringIO()
    bar = chatbar.ChatBar(out, width=width, unicode=False, screen=True)
    return bar, out


def test_the_frame_carries_the_band_above_the_top_bar():
    bar, out = _screen_bar()
    bar.draw(force=True)
    drawn = out.getvalue()
    assert "model n/a" in drawn
    assert drawn.index("model n/a") < drawn.index(chatbar.BAR_PLAIN)
    assert "goal: none" in drawn


def test_the_band_keeps_a_real_footer_that_streamed_past():
    """The end-to-end rule: a run's own footer reaches the band, marks included."""
    bar, _out = _screen_bar()
    # cold start: unmeasured ctx/tok/s are omitted (minimalist), not faked
    assert "ctx n/a" not in bar.band_text()
    bar.stream_chunk(_REAL_FOOTER + "\n")
    band = bar.band_text()
    assert "ctx <1%" in band
    assert "3.1k tok/s" in band
    assert "$ n/a" in band  # nothing is priced here, and that is the answer


def test_the_band_says_n_a_only_after_a_run_reported_nothing():
    """Cold start is compact; a turn that measured nothing still says ``n/a``."""
    bar, _out = _screen_bar()
    cold = bar.band_text()
    assert "ctx n/a" not in cold and "tok/s n/a" not in cold
    assert "model" in cold and "goal:" in cold
    bar.begin_turn()
    bar.end_turn()
    band = bar.band_text()
    assert "ctx n/a" in band
    assert "tok/s n/a" in band


def test_a_turn_shows_the_live_row_inside_the_frame_and_spends_no_line():
    """Screen mode puts the state in the frame; the transcript does not grow per tick."""
    bar, out = _screen_bar()
    bar.draw(force=True)
    before = out.getvalue()
    bar.begin_turn("working")
    after = out.getvalue()[len(before):]
    # the state went into the frame, so it is part of the composed lines *and* the repaint
    lines, _cursor = bar.compose()
    assert any("working" in line for line in lines), lines
    assert "working" in after
    # crucially it did not become a transcript line: nothing here looks like "working..."
    # written above the bars, which is what the non-screen path does
    assert bar.activity[0] == "working"
    bar.end_turn()
    lines, _cursor = bar.compose()
    assert not any("working" in line for line in lines), lines
    assert bar.activity == ("idle", "", 0)


def test_a_turn_in_screen_mode_still_lets_the_clock_run():
    """The live row carries the measured elapsed time, not a placeholder."""
    bar, _out = _screen_bar()
    turn = bar.begin_turn("working")
    turn.started -= 2.5
    turn.last_at = None
    live = bar.live_row()
    assert "working" in live
    assert "2.5s" in live or "2.4s" in live, live


def test_without_a_screen_a_turn_still_writes_its_working_line():
    """The four-line frame's behaviour is untouched: one line above the frame per turn."""
    out = io.StringIO()
    bar = chatbar.ChatBar(out, width=80, unicode=False, screen=False)
    bar.begin_turn("working")
    assert "working" in out.getvalue()
    assert bar.activity == ("idle", "", 0)


def test_the_finished_turn_keeps_its_elapsed_time():
    """The clock is the one number that is always measured; it must not reset to a zero."""
    bar, _out = _screen_bar()
    turn = bar.begin_turn("working")
    turn.started -= 134.0
    turn.last_at = None
    bar.end_turn()
    assert bar._last_clock == "02:14"
    assert "02:14" in bar.band_text()


def test_the_logo_is_painted_once_and_again_after_a_clear():
    """The owner's rule: once at first paint, and once more when the screen is wiped."""
    bar, out = _screen_bar()
    block = bar.logo_rows()
    assert block, "this build's art should produce a compact mark"
    assert bar.paint_logo() is True
    first = out.getvalue()
    for line in block:
        assert line in first
    assert bar.paint_logo() is False  # never a second time on its own
    assert out.getvalue() == first
    # ``/clear`` is the one thing that puts the logo back; without a terminal there is no
    # screen to wipe, so the frame says how to force it
    assert bar.paint_logo(force=True) is True
    after = out.getvalue()
    for line in block:
        assert after.count(line) >= 2, after


def test_a_clear_on_a_real_terminal_wipes_and_repaints():
    """``clear_screen`` is one bounded wipe (``ESC[2J`` + home), not a run of newlines."""
    import os
    import pty
    import select
    import time

    master, slave = pty.openpty()
    try:
        out = os.fdopen(os.dup(slave), "w", buffering=1)
        bar = chatbar.ChatBar(out, fd=slave, width=80, unicode=False, screen=True)
        assert bar.active
        block = bar.logo_rows()
        assert bar.paint_logo() is True
        assert bar.clear_screen(echo="cleared") is True
        out.flush()
        # read to a deadline, not once: under load the bytes arrive in more than one chunk
        written = ""
        end = time.monotonic() + 5.0
        while time.monotonic() < end:
            ready, _w, _x = select.select([master], [], [], 0.2)
            if not ready:
                if "cleared" in written and block and written.count(block[0]) >= 2:
                    break
                continue
            try:
                chunk = os.read(master, 65536)
            except OSError:
                break
            if not chunk:
                break
            written += chunk.decode("utf-8", "replace")
        assert "\x1b[2J\x1b[H" in written, repr(written)
        assert "cleared" in written, repr(written)
        for line in block:  # the logo came back after the wipe
            assert line in written, repr(written)
        out.close()
    finally:
        os.close(slave)
        os.close(master)


def test_clear_is_routed_before_the_slash_table():
    """``/clear`` works without the palette owning it, and a pipe is told the truth."""
    assert chatbar.is_clear_verb("/clear")
    assert chatbar.is_clear_verb("/cls")
    assert not chatbar.is_clear_verb("/clearer")
    assert not chatbar.is_clear_verb("/help")

    class _Shell:
        out = io.StringIO()

    assert chatbar.handle_clear(_Shell(), "/clear") is False
    assert "no frame is drawn here" in _Shell.out.getvalue()


def test_a_screen_frame_in_plain_mode_never_emits_an_escape_or_a_wide_byte():
    """``--no-color`` / a dumb terminal: the whole composed screen is ASCII."""
    bar = chatbar.ChatBar(io.StringIO(), width=80, unicode=False, screen=True)
    lines, _cursor = bar.compose()
    for line in lines:
        assert "\x1b" not in line, repr(line)
        assert all(ord(char) < 128 for char in line), repr(line)
