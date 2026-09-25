"""ART — the activity vocabulary: marks, frames, widths, and the ASCII promise.

What is proved here, in this order:

1. **Every activity draws** — one line each, in both glyph modes, at a width the table in
   ``MVP/67-activity-art.md`` states and this file pins.
2. **The ASCII fallback is real ASCII** — on a plain theme every line of the vocabulary
   (including the frame strips and the safety marks) contains no escape sequence and no
   non-ASCII character, and the fallbacks are *different characters*, never mangled ones.
3. **The timing is a measurement, not a vibe** — ``thinking``/``working`` have ≤ 4 frames at
   150 ms, :func:`skaldir.art.frame_index` is pure and wraps, and a static mark never moves.
4. **Nothing is invented** — an unknown duration, hit count or diff stat is *omitted*
   (``ms_label(None) == ""``); a real zero is printed; a 300-character path cannot push a
   line past :data:`skaldir.art.LINE_BUDGET`.
5. **The safety marks cannot be confused with success** — ``UNKNOWN``/``BLOCKED``/
   ``DECLINED`` are words, the blocked mark is the theme's ``■``/``#`` and never ``✓``/``+``.
6. **The marks agree with the theme** — the status marks *are*
   :meth:`skaldir.theme.Theme.mark`, the bars are :meth:`skaldir.theme.Theme.glyph`, so the
   vocabulary cannot drift away from the CLI's own look.
"""

from __future__ import annotations

import io

import pytest

from skaldir import art, render
from skaldir import theme as T

#: The frozen width table. Every number here is asserted below, so a change to a mark or a
#: line has to be a deliberate change to the documentation as well.
WIDTH_TABLE: dict[str, tuple[int, int, int]] = {
    # activity: (mark width unicode, mark width ascii, ascii line width)
    "thinking": (1, 1, 11),
    "working": (4, 4, 24),
    "tool": (4, 4, 22),
    "search": (2, 2, 37),
    "read": (1, 3, 29),
    "write": (1, 3, 34),
    "edit": (1, 2, 36),
    "subagents": (3, 3, 24),
    "verify": (1, 1, 27),
    "blocked": (1, 1, 41),
}

#: The unicode line widths of the same examples.
UNICODE_LINE_WIDTHS: dict[str, int] = {
    "thinking": 11, "working": 24, "tool": 22, "search": 37, "read": 27,
    "write": 32, "edit": 35, "subagents": 24, "verify": 27, "blocked": 41,
}


def rich() -> T.Theme:
    """A terminal that can do it all (explicit, so no test depends on the environment)."""
    return T.Theme(mode=T.MODE_RICH, depth=T.DEPTH_256, glyphs=T.GLYPHS_UNICODE,
                   width=80, height=24, source="explicit")


def plain() -> T.Theme:
    """What a pipe, a log file or ``--no-color`` gets: plain and ASCII."""
    return T.Theme(mode=T.MODE_PLAIN, depth=T.DEPTH_NONE, glyphs=T.GLYPHS_ASCII,
                   width=80, height=24, source="explicit")


def bare(text: str) -> str:
    """The line without its escape sequences (what a reader sees)."""
    return render.strip_ansi(text)


# --------------------------------------------------------------------------- #
# 1. every activity draws                                                      #
# --------------------------------------------------------------------------- #


def test_all_eight_activities_of_the_task_are_in_the_vocabulary():
    """The task names eight; the table has the blocked/declined pair as one entry."""
    for name in ("thinking", "working", "tool", "search", "read", "write", "edit",
                 "subagents", "verify", "blocked"):
        assert name in art.VOCABULARY
    assert set(art.NAMES) == set(WIDTH_TABLE)


@pytest.mark.parametrize("name", sorted(WIDTH_TABLE))
def test_each_activity_draws_one_line_in_both_modes(name):
    for theme in (rich(), plain()):
        drawn = art.example(name, theme=theme)
        assert drawn
        assert "\n" not in drawn, "a vocabulary line is one line"
        assert art.VOCABULARY[name].purpose and art.VOCABULARY[name].when


@pytest.mark.parametrize("name", sorted(WIDTH_TABLE))
def test_the_width_table_is_pinned(name):
    """The mark widths and the ASCII line widths are the documented numbers."""
    unicode_mark, ascii_mark, ascii_line = WIDTH_TABLE[name]
    assert art.mark_width(name, theme=rich()) == unicode_mark
    assert art.mark_width(name, theme=plain()) == ascii_mark
    assert render.width(art.example(name, theme=plain())) == ascii_line
    assert render.width(art.example(name, theme=rich())) == UNICODE_LINE_WIDTHS[name]


@pytest.mark.parametrize("name", sorted(WIDTH_TABLE))
def test_no_line_leaves_the_budget(name):
    """76 + the chat's 2-space indent still fits an 80-column screen."""
    for theme in (rich(), plain()):
        assert render.width(art.example(name, theme=theme)) <= art.LINE_BUDGET
        assert render.width(art.example(name, theme=theme)) + art.CHAT_INDENT <= 80


def test_the_widths_helper_agrees_with_the_table():
    rows = {row[0]: row for row in art.widths(theme=rich())}
    for name, (unicode_mark, ascii_mark, _) in WIDTH_TABLE.items():
        assert (rows[name][1], rows[name][2]) == (unicode_mark, ascii_mark)
    assert art.table(theme=rich()).count("\n") >= len(art.NAMES)


# --------------------------------------------------------------------------- #
# 2. the ASCII fallback                                                        #
# --------------------------------------------------------------------------- #


def test_a_plain_theme_produces_pure_ascii_with_no_escapes():
    for name in art.NAMES:
        drawn = art.example(name, theme=plain())
        assert drawn.isascii(), f"{name} is not ASCII: {drawn!r}"
        assert "\x1b" not in drawn


def test_the_ascii_twins_are_different_characters_not_mangled_ones():
    """``▤`` has the twin ``[=]``; the ASCII name for it is not ``?`` or a tofu box."""
    twins = {name: art.mark(name, theme=plain()) for name in art.NAMES}
    assert twins == {
        "thinking": ".", "working": "=---", "tool": "[>_]", "search": "o~",
        "read": "[=]", "write": "[+]", "edit": "+-", "subagents": "|||",
        "verify": "+", "blocked": "#",
    }
    assert art.mark("blocked", theme=plain()) != art.mark("verify", theme=plain())


def test_the_ascii_frame_strips_are_ascii_too():
    for name in ("thinking", "working"):
        drawn = art.frames(name, theme=plain())
        assert len(drawn) == 4
        for frame in drawn:
            assert frame.isascii() and frame


def test_the_preview_renders_in_both_modes():
    unicode_preview = art.preview(theme=rich())
    ascii_preview = art.preview(theme=plain())
    assert "VOCABULARY" in unicode_preview and "VOCABULARY" in ascii_preview
    assert "\x1b" not in ascii_preview and ascii_preview.isascii()
    for name in art.NAMES:
        assert name in ascii_preview


def test_colour_is_never_the_information():
    """Strip the escapes from a colour line and it is the same line colour-off.

    The vocabulary's shape must survive the loss of the grey ramp: same characters, same
    columns, only the SGR runs are gone.
    """
    colourless = T.Theme(mode=T.MODE_PLAIN, depth=T.DEPTH_NONE, glyphs=T.GLYPHS_UNICODE,
                         width=80, height=24, source="explicit")
    for name in art.NAMES:
        assert bare(art.example(name, theme=rich())) == art.example(name, theme=colourless)


# --------------------------------------------------------------------------- #
# 3. the timing                                                                #
# --------------------------------------------------------------------------- #


def test_the_two_animated_marks_have_four_frames_at_150_ms():
    assert art.FRAME_INTERVAL_MS == 150
    for name in ("thinking", "working"):
        assert art.frame_count(name) == 4
        assert art.interval_ms(name) == 150
        assert art.frame_count(name) <= 4, "the task caps the animated marks at four frames"


def test_a_static_mark_has_one_frame_and_never_moves():
    for name in ("tool", "search", "read", "write", "edit", "subagents", "verify", "blocked"):
        assert art.frame_count(name) == 1
        assert art.interval_ms(name) == 0
        for elapsed in (0, 150, 10_000):
            assert art.frame_index(name, elapsed) == 0


def test_the_frame_index_is_pure_arithmetic():
    assert [art.frame_index("thinking", ms) for ms in (0, 149, 150, 300, 450, 600)] == \
        [0, 0, 1, 2, 3, 0]
    assert art.frame_index("working", 1500) == 2          # 10 frames later: wraps at 4
    assert art.frame_index("thinking", -50) == 0          # a negative clock is not a crash
    assert art.frame_index("thinking", None) == 0


def test_the_breathing_frames_return_to_their_start():
    """A breath: out and back in, so the mark does not jump when the cycle repeats."""
    assert art.frames("thinking", theme=rich()) == ("◇", "◈", "◆", "◈")
    assert art.frames("thinking", theme=plain()) == (".", "o", "O", "o")
    assert art.frames("working", theme=rich()) == ("▓░░░", "░▓░░", "░░▓░", "░░░▓")


def test_elapsed_time_picks_the_frame_and_the_line_says_so():
    frames = [bare(art.thinking(elapsed_ms=ms, theme=rich())) for ms in (0, 150, 300, 450)]
    assert frames == ["◇  thinking", "◈  thinking", "◆  thinking", "◈  thinking"]
    # the working track, one frame per 150 ms: the head walks left to right
    assert [art.working("indexing", elapsed_ms=ms, theme=plain()) for ms in (0, 300)] == \
        ["=---  indexing", "--=-  indexing"]


def test_a_known_fraction_is_drawn_and_an_unknown_one_is_not():
    measured = bare(art.working("indexing", progress=0.62, theme=rich()))
    assert measured == "██░░  indexing  62%"
    assert bare(art.working("indexing", theme=rich())) == "▓░░░  indexing"
    assert "%" not in bare(art.working("indexing", theme=rich()))
    assert bare(art.progress_track(0.5, theme=plain())) == "##--"


# --------------------------------------------------------------------------- #
# 4. nothing is invented                                                       #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("value", [None, "", True, False, "soon", [], {}])
def test_an_unknown_duration_is_omitted(value):
    assert art.ms_label(value) == ""
    assert "ms" not in art.tool("read_text", value, theme=plain())
    assert "ms" not in art.read("a.py", ms=value, theme=plain())


def test_a_measured_duration_is_printed_including_a_real_zero():
    assert art.ms_label(412) == "412 ms"
    assert art.ms_label(0) == "0 ms"
    assert art.ms_label("41") == "41 ms"
    assert art.tool("read_text", 41, theme=plain()) == "[>_]  read_text  41 ms"


@pytest.mark.parametrize("value", [None, "", True, "later"])
def test_an_unknown_hit_count_is_omitted(value):
    assert art.hits_label(value) == ""
    assert "hit" not in bare(art.search("anything", value, theme=rich()))


def test_a_reported_hit_count_is_printed_and_singularised():
    assert art.hits_label(0) == "0 hits"
    assert art.hits_label(1) == "1 hit"
    assert art.hits_label(12) == "12 hits"


def test_the_diff_stat_echoes_what_the_record_carried():
    assert art.diff_label(12, 4, theme=rich()) == "+12 \u22124"
    assert art.diff_label(12, 4, theme=plain()) == "+12 -4"
    assert art.diff_label(3, None, theme=plain()) == "+3"
    assert art.diff_label(None, 3, theme=plain()) == "-3"       # never a fake +0
    assert art.diff_label(None, None, theme=plain()) == ""
    assert bare(art.edit("a.py", 12, 4, theme=rich())) == "±  a.py  +12 \u22124"
    assert art.edit("a.py", 12, 4, theme=plain()) == "+-  a.py  +12 -4"
    assert art.edit("a.py", theme=plain()) == "+-  a.py"


@pytest.mark.parametrize("subject", ["x" * 300, "🎯" * 90, "a" * 2000])
def test_a_hostile_label_cannot_push_a_line_past_the_budget(subject):
    for drawn in (art.read(subject, ms=9, theme=rich()),
                  art.search(subject, 3, theme=plain()),
                  art.blocked(subject, theme=rich()),
                  art.subagents(12, label=subject, theme=plain())):
        assert render.width(drawn) <= art.LINE_BUDGET


def test_a_truncated_line_keeps_its_measurement():
    """The clock never disappears behind a long title (the rule the shell's indicator uses)."""
    drawn = bare(art.tool("a-very-long-tool-name-" + "x" * 200, 412, theme=rich()))
    assert drawn.endswith("412 ms")
    assert "\u2026" in drawn


def test_a_long_path_keeps_the_diff_stat():
    drawn = bare(art.edit("src/" + "y" * 200 + ".py", 12, 4, theme=rich()))
    assert drawn.endswith("+12 \u22124")


# --------------------------------------------------------------------------- #
# 5. the safety marks                                                          #
# --------------------------------------------------------------------------- #


def test_unknown_is_a_word_and_a_mark_of_its_own():
    drawn = bare(art.unknown("which provider answered", theme=rich()))
    assert drawn == "?  UNKNOWN which provider answered"
    assert art.KEYWORD_UNKNOWN == "UNKNOWN"
    for status in art.VERIFY_STATUSES:
        assert art.VERIFY_KEYWORDS[status]


def test_verify_carries_its_three_outcomes():
    assert bare(art.verify("80 columns hold", theme=plain())) == "+  verified 80 columns hold"
    assert bare(art.verify("t", status="fail", theme=plain())) == "x  failed t"
    assert bare(art.verify("t", status="nonsense", theme=plain())).startswith("?  UNKNOWN")
    assert bare(art.verify("t", status="unknown", theme=plain())).startswith("?  UNKNOWN")


def test_blocked_and_declined_are_words_and_share_the_gate_mark():
    assert bare(art.blocked("approval refused", theme=plain())) == "#  BLOCKED approval refused"
    assert art.declined("owner said no", theme=plain())
    assert bare(art.declined("owner said no", theme=plain())) == "#  DECLINED owner said no"
    assert art.KEYWORD_BLOCKED == "BLOCKED" and art.KEYWORD_DECLINED == "DECLINED"


def test_a_gate_line_can_never_read_as_a_success():
    for theme in (rich(), plain()):
        for drawn in (art.blocked("x", theme=theme), art.declined("x", theme=theme),
                      art.unknown("x", theme=theme)):
            head = drawn.split("  ")[0]
            assert head not in {theme.mark("ok"), theme.mark("info"), theme.mark("warn")}
            assert theme.mark("ok") not in drawn[:4]


def test_a_blocked_line_without_a_reason_is_still_a_blocked_line():
    assert bare(art.blocked(theme=plain())) == "#  BLOCKED"
    assert bare(art.blocked(theme=rich())) == "■  BLOCKED"


# --------------------------------------------------------------------------- #
# 6. the marks come from the theme                                             #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("theme_factory", [rich, plain])
def test_the_status_marks_are_the_theme_marks(theme_factory):
    theme = theme_factory()
    assert art.mark("verify", theme=theme) == theme.mark("ok")
    assert art.mark("blocked", theme=theme) == theme.mark("blocked")
    assert art.verify("x", status="fail", theme=theme).split("  ")[0] == \
        theme.style(theme.mark("fail"), "fail")


@pytest.mark.parametrize("theme_factory", [rich, plain])
def test_the_bars_and_the_vertical_are_the_theme_glyphs(theme_factory):
    theme = theme_factory()
    assert art.frames("working", theme=theme)[0] == \
        theme.glyph("bar_head") + theme.glyph("bar_empty") * 3
    assert art.progress_track(1.0, theme=theme) == theme.glyph("bar_full") * 4
    assert art.mark("subagents", theme=theme) == theme.glyph("v") * 3


def test_resolving_a_name_never_guesses():
    assert art.activity_for("read_text") == "read"
    assert art.activity_for("READ_TEXT") == "read"
    assert art.activity_for("apply_patch") == "edit"
    assert art.activity_for("web_search") == "search"
    assert art.activity_for("unread_text") == "tool"        # not a substring match
    assert art.activity_for("") == "tool"
    assert art.resolve("thinking") == "thinking"
    assert art.verify  # the module is importable as documented


# --------------------------------------------------------------------------- #
# the record seam the chat and the tool lines use                              #
# --------------------------------------------------------------------------- #


def test_a_record_draws_as_its_activity():
    assert bare(art.line({"activity": "read", "path": "a.py", "ms": 41}, theme=rich())) == \
        "▤  a.py  41 ms"
    assert bare(art.line({"kind": "search", "query": "x", "hits": 2}, theme=plain())) == \
        'o~  "x"  2 hits'
    assert bare(art.line({"activity": "subagents", "count": 3}, theme=plain())) == \
        "|||  3 subagents running"


def test_a_record_without_an_activity_uses_the_tool_name():
    record = {"name": "read_text", "arguments": {"path": "hello.txt"},
              "duration_ms": 41, "ok": True}
    assert bare(art.line(record, theme=rich())) == "▤  hello.txt  41 ms"


def test_a_real_tool_event_draws_and_a_refusal_draws_blocked():
    allowed = {"name": "write_text", "arguments": {"path": "a.py"}, "duration_ms": 12,
               "ok": True, "decision": "allowed"}
    assert bare(art.from_tool_event(allowed, theme=plain())) == "[+]  a.py  12 ms"
    refused = {"name": "shell", "decision": "denied_approval", "ok": False}
    assert bare(art.from_tool_event(refused, theme=plain())) == \
        "#  DECLINED shell - denied_approval"
    assert bare(art.from_tool_event(refused, theme=rich())) == \
        "\u25a0  DECLINED shell \u00b7 denied_approval"


def test_uppercase_and_lowercase_allow_outcomes_draw_as_tool_lines():
    # agent_loop classifies with UPPERCASE words: ALLOW / ASK_APPROVAL / ...
    for outcome in ("ALLOW", "allow", "Allow", "EXECUTED", "OK", "Run", "RUN"):
        event = {"name": "write_text", "arguments": {"path": "a.py"}, "duration_ms": 12,
                 "ok": True, "decision": outcome}
        assert bare(art.from_tool_event(event, theme=plain())) == "[+]  a.py  12 ms", outcome


def test_allow_with_ok_false_still_draws_refused():
    event = {"name": "write_text", "arguments": {"path": "a.py"}, "duration_ms": 12,
             "ok": False, "decision": "ALLOW"}
    drawn = bare(art.from_tool_event(event, theme=plain()))
    assert "DECLINED" in drawn, drawn


def test_mixed_case_genuine_refusals_stay_declined():
    for outcome in ("ASK_APPROVAL", "ask_approval", "Pause_And_Investigate",
                    "BLOCK", "block", "DenIed"):
        event = {"name": "shell", "decision": outcome, "ok": False}
        drawn = bare(art.from_tool_event(event, theme=plain()))
        assert "DECLINED" in drawn, (outcome, drawn)


def test_a_transcript_is_several_lines_at_the_chat_indent():
    records = [
        {"activity": "thinking", "elapsed_ms": 320},
        {"activity": "read", "path": "a.py", "ms": 4},
        {"activity": "verify", "text": "the read matched"},
    ]
    lines = art.transcript(records, theme=plain()).split("\n")
    assert lines == [
        "  .  thinking",
        "  [=]  a.py  4 ms",
        "  +  verified the read matched",
    ]
    assert all(line.startswith(" " * art.CHAT_INDENT) for line in lines)
    assert art.transcript(records, theme=plain(), indent=4).split("\n")[0] == \
        "    .  thinking"


def test_a_non_record_draws_nothing_rather_than_a_guess():
    assert art.line(None, theme=rich()) == ""
    assert art.line("read a.py", theme=rich()) == ""
    assert art.transcript([None, {}], theme=plain())
    assert bare(art.line({}, theme=plain())).startswith("[>_]")


def test_a_tool_failure_is_marked_with_the_theme_fail_mark():
    assert bare(art.tool("shell", 9, ok=False, theme=rich())) == "[▸_]  shell  9 ms  ✗"
    assert art.tool("shell", 9, ok=False, theme=plain()).endswith("  x")
    assert art.tool("shell", 9, ok=True, theme=plain()) == "[>_]  shell  9 ms"


def test_a_borrowed_unicode_label_is_folded_on_a_plain_theme():
    """A reason that arrived from elsewhere cannot break the ASCII promise."""
    drawn = art.blocked("refused \u2014 the owner said no", theme=plain())
    assert drawn.isascii() and "\u2014" not in drawn
    assert "refused - the owner said no" in drawn


def test_the_module_cli_prints_the_preview(capsys):
    assert art.main(["preview", "--ascii"]) == 0
    printed = capsys.readouterr().out
    assert printed.strip()
    assert "\x1b" not in printed
    assert "thinking" in printed and "BLOCKED" in printed


def test_the_module_cli_json_lists_every_activity(capsys):
    import json

    assert art.main(["json", "--ascii"]) == 0
    document = json.loads(capsys.readouterr().out)
    assert [row["activity"] for row in document["activities"]] == list(art.NAMES)
    assert document["line_budget"] == art.LINE_BUDGET
    assert document["frame_ms"] == 150
    assert document["statuses"] == ["ok", "fail", "unknown"]
    for row in document["activities"]:
        assert row["line_width"] + row["indented_width"] - row["line_width"] <= 80
        assert row["mark_ascii"].isascii()


def test_the_vocabulary_lines_up_in_the_chat_frame():
    """The chat's two line kinds share one left margin at 80 columns.

    An activity line (this module) and a status line (:mod:`skaldir.render`) both put their
    mark at :data:`skaldir.art.CHAT_INDENT`, and neither leaves the 80-column screen.
    """
    records = [
        {"activity": "thinking", "elapsed_ms": 0},
        {"activity": "search", "query": "activity vocabulary", "hits": 12},
        {"activity": "edit", "path": "src/skaldir/art.py", "added": 12, "removed": 4},
        {"activity": "subagents", "count": 3},
        {"activity": "blocked", "reason": "approval declined by the owner"},
    ]
    block = art.transcript(records, theme=plain())
    status = render.status_line("ok", "the turn finished", indent=art.CHAT_INDENT, theme=plain())
    frame = block.split("\n") + [status]
    for line in frame:
        assert line.startswith(" " * art.CHAT_INDENT)
        assert render.width(line) <= 80
        assert not line.endswith(" ")
    assert frame[-1] == "  +  the turn finished"
    assert [line[art.CHAT_INDENT] for line in frame] == [".", "o", "+", "|", "#", "+"]


def test_the_preview_does_not_depend_on_a_terminal(monkeypatch):
    """A pipe gets the ASCII rendering, like every other command in this CLI."""
    stream = io.StringIO()
    monkeypatch.setattr("sys.stdout", stream)
    assert art.main(["preview"]) == 0
    assert "\x1b" not in stream.getvalue()
