"""``MarkdownStream(incremental=True)``: the chat presentation mode.

The chat surface cannot wait for a newline to paint: a single answer row that stays empty
until the model finishes is what the owner sees as "one update for 47 deltas". This module
pins the opt-in mode that fixes it, and pins the default that must not move.

What is asserted here, and why each one is a rule rather than a nicety:

* **the first delta is on the screen before the model has finished the line** — that is the
  whole change; without it the mode is indistinguishable from the default;
* **later deltas still obey the interval** — the liveness must not turn into one write per
  delta on a 47-delta answer;
* **nothing is printed past a half-written marker** — a terminal cannot take characters back,
  so ``**`` with no partner waits;
* **a fence and a code row keep their line buffering** — a code row's rule starts it and its
  wrap is computed against the whole row, so half a row would break the block's left edge;
* **no indent, bullet or marker is drawn twice** — this is the failure mode of naive partial
  rendering, and the reason only the first segment of a line chooses the block form;
* **``incremental=False`` and the pipe path are byte-for-byte what they were** — the default
  is used by ``agent run``, ``goal run`` and every test in ``test_stream_render.py``.
"""

from __future__ import annotations

import io

import pytest

from skaldir import stream_render


class FakeTTY(io.StringIO):
    """A stream that claims to be a terminal."""

    def isatty(self) -> bool:  # noqa: D102 — the point of the double
        return True


class Clock:
    """A clock a test moves by hand — no sleeps, no flakiness."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def chat_stream(tty, clock, **kwargs):
    """The exact construction the chat presentation uses."""
    options = {"tty": True, "interval_ms": 60, "clock": clock, "incremental": True,
               "footer": False, "indent": 0}
    options.update(kwargs)
    stream = stream_render.MarkdownStream(tty, **options)
    stream.arm()
    return stream


# --------------------------------------------------------------------------- #
# the mode: partial prose on the first delta, coalesced after                  #
# --------------------------------------------------------------------------- #


def test_first_delta_reaches_the_screen_before_the_line_ends():
    clock, tty = Clock(), FakeTTY()
    stream = chat_stream(tty, clock)

    stream.delta("Hello")  # no newline yet — the default mode would print nothing

    assert tty.getvalue() == "Hello"


def test_later_deltas_still_obey_the_interval():
    clock, tty = Clock(), FakeTTY()
    stream = chat_stream(tty, clock)

    stream.delta("Hello")          # first: immediate
    clock.advance(0.01)
    stream.delta(" World")         # inside the interval: coalesced, not lost
    assert tty.getvalue() == "Hello"
    assert stream.metrics.coalesced == 1

    clock.advance(0.1)
    stream.delta("!")              # due: the coalesced tail goes out in one write
    assert tty.getvalue() == "Hello World!"
    assert stream.metrics.updates == 2


def test_several_partial_writes_do_not_duplicate_indent_or_markers():
    """The naive bug: every chunk re-drawing the indent, the bullet or the marker."""
    clock, tty = Clock(), FakeTTY()
    stream = chat_stream(tty, clock, indent=2)

    stream.delta("- An it")
    clock.advance(0.1)
    stream.delta("em that ")
    clock.advance(0.1)
    stream.delta("wraps")

    output = tty.getvalue()
    assert output == "  - An item that wraps"
    assert output.count("  ") == 1          # the indent, drawn once
    assert output.count("- ") == 1          # the bullet, drawn once


def test_a_half_written_marker_is_held_not_printed():
    clock, tty = Clock(), FakeTTY()
    stream = chat_stream(tty, clock)

    stream.delta("Hello **")       # the bold span is not decided yet
    assert tty.getvalue() == "Hello "

    clock.advance(0.1)
    stream.delta("world**.")       # the partner arrives: the span is rendered once
    assert tty.getvalue() == "Hello world."


def test_a_code_span_is_never_cut_in_half():
    clock, tty = Clock(), FakeTTY()
    stream = chat_stream(tty, clock)

    stream.delta("run `gv")
    assert tty.getvalue() == "run "

    clock.advance(0.1)
    stream.delta(" stream` now")
    assert tty.getvalue() == "run gv stream now"


def test_a_possible_fence_is_held_rather_than_guessed():
    """``` is either a fence or a code span: three characters is the cost of not guessing."""
    clock, tty = Clock(), FakeTTY()
    stream = chat_stream(tty, clock)

    stream.delta("``")
    assert tty.getvalue() == ""

    clock.advance(0.1)
    stream.delta("`python\n")
    # the fence line is scaffolding: the block is carried by the rule on its content rows
    assert "```" not in tty.getvalue()


def test_a_code_block_keeps_line_buffering_and_its_rule():
    clock, tty = Clock(), FakeTTY()
    stream = chat_stream(tty, clock)

    stream.delta("```python\n")
    assert tty.getvalue() == ""          # the fence line itself is never printed

    stream.delta("x =")
    assert tty.getvalue() == ""          # half a code row: the rule could not start it

    clock.advance(0.1)
    stream.delta(" 1\n")
    output = tty.getvalue()
    assert "x = 1" in output
    assert output.count("|") == 1        # the rule, once, on the finished row
    assert "```" not in output


def test_full_text_is_lossless_across_partial_renders():
    clock, tty = Clock(), FakeTTY()
    stream = chat_stream(tty, clock)
    source = "Bold **text**, a `span`, and a line that ends.\nsecond line\n"

    for index in range(0, len(source), 7):
        if index:
            clock.advance(0.1)
        stream.delta(source[index:index + 7])
    stream.finish()

    assert stream.text == source


def test_no_footer_is_drawn_when_footer_is_false():
    clock, tty = Clock(), FakeTTY()
    stream = chat_stream(tty, clock)

    stream.delta("An answer without chrome\n")
    clock.advance(0.1)
    stream.delta("and its second row\n")
    stream.finish()

    output = tty.getvalue()
    assert "streaming" not in output
    assert "\x1b[K" not in output        # no in-place repaint of the answer either
    assert output == "An answer without chrome\nand its second row\n"


# --------------------------------------------------------------------------- #
# the default: unchanged, and the pipe unchanged with it                       #
# --------------------------------------------------------------------------- #


def test_the_default_mode_still_waits_for_the_newline():
    clock, tty = Clock(), FakeTTY()
    stream = stream_render.MarkdownStream(tty, tty=True, interval_ms=60, clock=clock,
                                          footer=False, indent=0)
    stream.arm()

    stream.delta("Hello")
    clock.advance(0.2)
    stream.delta(" world")
    assert tty.getvalue() == ""           # held: exactly the old behaviour

    stream.delta("\n")
    assert tty.getvalue() == "Hello world\n"


def test_incremental_defaults_to_false():
    assert stream_render.MarkdownStream(FakeTTY(), tty=True).incremental is False


def test_incremental_true_does_not_change_the_pipe_path():
    clock = Clock()
    pipe = io.StringIO()
    stream = stream_render.MarkdownStream(pipe, tty=False, interval_ms=60, clock=clock,
                                          incremental=True, footer=False)
    stream.arm()
    stream.delta("Hello ")
    clock.advance(0.1)
    stream.delta("**world**\n")
    stream.finish()

    # a pipe is written once, at the end, byte-identical to the model's own output
    assert pipe.getvalue() == "Hello **world**\n"
    assert stream.metrics.updates == 1


def test_incremental_true_does_not_change_what_nowait_means():
    """``incremental=True`` must not turn the opt-in tail flush into something else."""
    clock, tty = Clock(), FakeTTY()
    stream = chat_stream(tty, clock, tail_flush_ms=250, tail_flush_chars=40)

    stream.delta("x" * 60)
    assert tty.getvalue() == "x" * 60


@pytest.mark.parametrize("source", [
    "plain prose with no newline at all",
    "**bold** and `code`",
    "- one\n- two\n",
    "# Heading\n\nbody text\n",
])
def test_incremental_output_never_contains_a_repeated_block_prefix(source):
    clock, tty = Clock(), FakeTTY()
    stream = chat_stream(tty, clock, indent=2)
    for index in range(0, len(source), 5):
        if index:
            clock.advance(0.1)
        stream.delta(source[index:index + 5])
    stream.finish()

    for row in tty.getvalue().split("\n"):
        if row.strip():
            assert not row.startswith("      "), row       # two indents stacked
