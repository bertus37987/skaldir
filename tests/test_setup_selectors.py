"""The interactive selector: one engine for ``skaldir setup``, ``skaldir model`` and the pipe.

What this file pins, in the owner's words:

* **one code path** — ``setup`` and ``model`` ask through the same ``model_picker.choose``, so a
  question cannot be a list in one verb and a free-text prompt in the other;
* **the keys work** — ``↑``/``↓`` (and ``k``/``j``), ``PgUp``/``PgDn``, live filtering, a
  first-letter jump, ``Enter`` selects, ``Esc``/``Ctrl-C`` cancel without killing the run;
* **80×24 survives** — at most 10 visible rows plus a ``… N more`` line, every painted line is
  one cell short of the edge, and the block's height never changes between paints (a block that
  grows walks down the screen);
* **it degrades honestly** — a pipe, ``TERM=dumb``, ``NO_COLOR``, a captured stream or a
  terminal too small gets the same rows as lines and one line of input, ASCII only;
* **the swap point is real** — the day the shared ``skaldir.picker`` widget lands with the
  documented signature, every list is handed to it and the loop here is deleted.
"""

from __future__ import annotations

import io
import re
from typing import Any

import pytest
from test_model_picker import _hermetic_env  # noqa: F401 — the same clean environment

from skaldir import model_cli as mcli
from skaldir import model_picker as mp
from skaldir import provider_catalog as catalog
from skaldir import provider_registry_data as registry
from skaldir import setup_cli as scli
from skaldir import setup_flow as sf

#: The width of the widest line the selector may paint on an 80-column terminal.
EDGE = 79

_ESCAPES = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _painted(sink: io.StringIO) -> list[str]:
    """Every line the block drew, escapes stripped, in paint order."""
    plain = _ESCAPES.sub("", sink.getvalue())
    return [line.lstrip("\r") for line in plain.split("\r\n")]


def _ask(keys: list[str], **kwargs: Any) -> tuple[str | None, io.StringIO]:
    sink = io.StringIO()
    answer = mp.select_in_terminal(
        kwargs.pop("title", "provider"), kwargs.pop("rows", None) or mp.provider_rows(),
        stream_out=sink, size=kwargs.pop("size", (80, 24)), keys=keys, **kwargs
    )
    return answer, sink


def _last_line(sink: io.StringIO) -> str:
    """The block's status line — the last line it painted that is not blank."""
    drawn = [line for line in _painted(sink) if line.strip()]
    return drawn[-1] if drawn else ""


#: The block's height at 80×24: a title, ten rows, the ``… N more`` line, the status line.
BLOCK = 13


def _last_block(sink: io.StringIO) -> list[str]:
    """The lines of the **last** paint: what is on the screen when the question ends."""
    painted = _painted(sink)
    while painted and not painted[-1].strip():
        painted.pop()  # the paint ends with a newline, which splits off an empty tail
    return painted[-BLOCK:] if len(painted) >= BLOCK else painted


# --------------------------------------------------------------------------- #
# 1. the gate: when is a list drawn, and when are lines asked                    #
# --------------------------------------------------------------------------- #


def test_a_plain_terminal_takes_the_list_and_a_pipe_does_not():
    live = {"TERM": "xterm-256color"}
    ok, why = mp.selectable(env=live, stream_in=_Tty(), stream_out=_Tty())
    assert ok is True and why == ""

    for name, variable in (
        ("SKALDIR_PLAIN", "1"),
        ("NO_COLOR", "1"),
        ("NO_COLOR", ""),
    ):
        env = {"TERM": "xterm-256color", name: variable}
        ok, why = mp.selectable(env=env, stream_in=_Tty(), stream_out=_Tty())
        assert ok is False and name in why

    for term in ("dumb", ""):
        ok, why = mp.selectable(env={"TERM": term}, stream_in=_Tty(), stream_out=_Tty())
        assert ok is False and "TERM" in why

    ok, why = mp.selectable(
        env={"TERM": "xterm-256color"}, stream_in=io.StringIO(), stream_out=_Tty()
    )
    assert ok is False and "stdin" in why
    ok, why = mp.selectable(
        env={"TERM": "xterm-256color"}, stream_in=_Tty(), stream_out=io.StringIO()
    )
    assert ok is False and "stderr" in why


def test_a_terminal_too_small_for_a_list_says_so(monkeypatch):
    monkeypatch.setattr(mp, "terminal_size", lambda size=None: (20, 5))
    ok, why = mp.selectable(env={"TERM": "xterm"}, stream_in=_Tty(), stream_out=_Tty())
    assert ok is False and "narrower" in why
    monkeypatch.setattr(mp, "terminal_size", lambda size=None: (80, 4))
    ok, why = mp.selectable(env={"TERM": "xterm"}, stream_in=_Tty(), stream_out=_Tty())
    assert ok is False and "shorter" in why


class _Tty:
    """A stream that answers ``isatty`` and nothing else (the gate asks nothing else)."""

    encoding = "utf-8"

    def isatty(self) -> bool:
        return True

    def fileno(self) -> int:  # pragma: no cover — the gate never reads a descriptor
        raise OSError("not a real terminal")


# --------------------------------------------------------------------------- #
# 2. the drawing: 80x24, fixed height, one cell short of the edge                #
# --------------------------------------------------------------------------- #


def test_the_block_is_the_same_height_on_every_paint_and_fits_the_screen():
    rows = mp.provider_rows()
    answer, sink = _ask(["down", "down", "down", "enter"])
    assert answer == rows[3].value
    raw = sink.getvalue()
    plain = _painted(sink)

    # every painted line is one cell short of 80 — never wrapping, never spilling
    assert max(len(line) for line in plain) == EDGE
    # the block is erased and redrawn by moving the cursor back up its own height: that
    # height must never change (13 = title + 10 rows + "… N more" + status)
    ups = set(re.findall(r"\x1b\[(\d+)A", raw))
    assert ups == {"13"}, f"the block's height changed between paints: {ups}"
    assert raw.count("\x1b[2K") % 13 == 0


def test_ten_rows_are_shown_and_the_rest_are_counted():
    rows = mp.provider_rows()
    answer, sink = _ask(["esc"])
    assert answer is None
    lines = _painted(sink)
    # every registry row, at most 10 on screen: the rest are counted, never dropped
    hidden = len(rows) - 10
    assert sum(1 for line in lines if line.strip().endswith(f"{hidden} more")) >= 1
    drawn = [line for line in lines if re.match(r"^  [ >] ?\d+ ", line)]
    assert drawn, "the rows must be drawn"
    assert all(line[4:6].strip().isdigit() for line in drawn)
    # multi-SKU providers expand into one row per plan endpoint
    assert len(rows) >= len(registry.REGISTRY_ORDER)


def test_the_count_of_hidden_rows_shrinks_as_the_filter_narrows():
    answer, sink = _ask(["e", "esc"])
    assert answer is None
    assert any("more" in line for line in _last_block(sink)), "more matches than one screen"
    answer, sink = _ask(["o", "l", "l", "a", "m", "a", "esc"])
    assert answer is None
    on_screen = _last_block(sink)
    assert not any("more" in line for line in on_screen), "a narrowed list has nothing 'more'"
    # title + matching row(s) + status — never a "N more" row
    nonempty = sum(1 for line in on_screen if line.strip())
    assert 3 <= nonempty <= 5, nonempty


def test_a_narrow_terminal_still_paints_exactly_its_own_width():
    answer, sink = _ask(["esc"], size=(48, 24))
    assert answer is None
    assert max(len(line) for line in _painted(sink)) == 47


# --------------------------------------------------------------------------- #
# 3. the keys                                                                    #
# --------------------------------------------------------------------------- #


def test_arrows_jk_and_home_end_move_the_pointer():
    rows = mp.provider_rows()
    assert _ask(["down", "enter"])[0] == rows[1].value
    assert _ask(["j", "j", "enter"])[0] == rows[2].value
    assert _ask(["down", "up", "enter"])[0] == rows[0].value
    assert _ask(["up", "enter"])[0] == rows[-1].value, "up from the first row wraps to the last"
    assert _ask(["down", "k", "enter"])[0] == rows[0].value, "k is the same as up"
    # the two keys the test's own name promises: Home/End, not a silent no-op
    assert _ask(["end", "enter"])[0] == rows[-1].value
    assert _ask(["down", "down", "home", "enter"])[0] == rows[0].value


def test_the_page_keys_jump_a_page_and_clamp():
    rows = mp.provider_rows()
    assert _ask(["pagedown", "enter"])[0] == rows[min(10, len(rows) - 1)].value
    # enough page-jumps to reach the end of a 40+-row catalog (two was written for a
    # shorter list); the last jump clamps on the last row
    pages = (len(rows) + 9) // 10
    keys = ["pagedown"] * pages + ["enter"]
    assert _ask(keys)[0] == rows[-1].value
    assert _ask(["pagedown", "pageup", "enter"])[0] == rows[0].value


def test_typing_narrows_the_list_and_the_number_picks_the_row():
    assert _ask(["d", "e", "e", "p", "enter"])[0] == "deepseek"
    # "13" narrows by row number: the thirteenth row of the list as the picker shows it
    assert _ask(["1", "3", "enter"])[0] == mp.provider_rows()[12].value
    assert _ask(["m", "o", "o", "n", "enter"])[0] == "moonshot"


def test_a_letter_jump_lands_on_the_first_name_that_starts_with_it():
    rows = mp.provider_rows()
    labels = [row.value for row in rows]
    answer, sink = _ask(["x", "enter"])
    assert answer == "xai"
    assert "xai" in labels
    # the row numbers are part of the name: a digit still lands on that row
    assert _ask(["7", "enter"])[0] == rows[6].value


def test_backspace_and_ctrl_u_edit_the_filter():
    assert _ask(["d", "backspace", "o", "p", "e", "n", "enter"])[0] == "openai"
    assert _ask(["z", "z", "ctrl-u", "down", "enter"])[0] == "anthropic"


def test_a_typed_id_nobody_documented_is_used_as_it_is_when_text_is_allowed():
    assert _ask(["z", "z", "z", "enter"], allow_text=True)[0] == "zzz"
    assert _ask(["z", "z", "z", "enter"])[0] == ""


def test_escape_and_ctrl_c_cancel_without_an_answer_and_say_so():
    for key in ("esc", "ctrl-c", "ctrl-d"):
        answer, sink = _ask(["down", key])
        assert answer is None
        assert "cancelled: nothing was written" in _last_line(sink)
    # the run is not killed: the cancelled question returns and the caller decides
    assert _ask(["x", "ctrl-c"])[0] is None


def test_enter_on_an_empty_filter_takes_the_pre_filled_row():
    # the pre-filled value is the catalog's own default model, not a literal: the
    # id the endpoint serves is the catalog's to say, and it moved on 2026-09-19
    default = catalog.CATALOG["deepseek"].default_model
    rows = mp.model_rows("deepseek")
    answer = mp.select_in_terminal(
        "model", rows, stream_out=io.StringIO(), size=(80, 24), keys=["enter"],
        default_value=default,
    )
    assert answer == default

    answer, sink = _ask(["backspace"], title="nothing", rows=mp.provider_rows())
    assert answer is None  # the script ran out: no answer, and the block says so
    assert "no answer" in _last_line(sink)


# --------------------------------------------------------------------------- #
# 4. the plain fallback: the same rows as lines, ASCII only                       #
# --------------------------------------------------------------------------- #


def test_the_fallback_prints_the_rows_and_reads_one_line():
    sink = io.StringIO()
    asked: list[str] = []

    def reader(prompt: str) -> str:
        asked.append(prompt)
        # the row number of deepseek (plan rows shift the index)
        return str(next(i for i, r in enumerate(mp.provider_rows()) if r.value == "deepseek") + 1)

    answer = mp.choose(
        "provider (number or id)",
        mp.provider_rows(),
        default_value="openai",
        interactive=True,
        env={"TERM": "dumb"},
        stream_out=sink,
        line_reader=reader,
    )
    assert answer == "deepseek"
    body = sink.getvalue()
    assert "https://api.deepseek.com/v1" in body
    assert asked == ["  provider (number or id) [openai]: "]
    assert "\x1b" not in body, "a pipe gets no screen control"
    assert "…" not in body, "the plain path clips with ASCII dots"
    assert "llama.cpp (local server) (local)" in body, "the label is never folded into a guess"


def test_the_fallback_takes_enter_as_the_pre_filled_value_and_an_id_by_name():
    for answer, wanted in (("", "openai"), ("openai", "openai"), ("  deepseek ", "deepseek")):
        got = mp.choose(
            "provider",
            mp.provider_rows(),
            default_value="openai",
            interactive=True,
            env={"TERM": "dumb"},
            stream_out=io.StringIO(),
            line_reader=lambda prompt, reply=answer: reply,
        )
        assert got == wanted


def test_a_headless_question_asks_nothing_and_returns_no_answer():
    sink = io.StringIO()
    asked: list[str] = []
    answer = mp.choose(
        "provider",
        mp.provider_rows(),
        interactive=False,
        env={"TERM": "xterm"},
        stream_out=sink,
        line_reader=lambda prompt: asked.append(prompt) or "1",
    )
    assert answer == "" and asked == [] and sink.getvalue() == ""


def test_the_rows_of_the_fallback_are_the_rows_of_the_selector():
    """One list, two renderings: the plain lines carry the same rows in the same order."""
    sink = io.StringIO()
    mp.choose(
        "provider",
        mp.provider_rows(),
        interactive=True,
        env={"NO_COLOR": "1"},
        stream_out=sink,
        line_reader=lambda prompt: "",
    )
    body = sink.getvalue()
    rows = mp.provider_rows()
    positions = [body.index(row.name) for row in rows]
    assert positions == sorted(positions), "the rows keep the catalog's order"
    # the endpoints a person actually needs are on the lines, not clipped away
    for wanted in ("https://api.deepseek.com/v1", "http://127.0.0.1:8080/v1"):
        assert wanted in body


# --------------------------------------------------------------------------- #
# 5. the swap point: the shared widget, when it arrives                           #
# --------------------------------------------------------------------------- #


class _Shared:
    """A stand-in for ``skaldir.picker``: records the call it was handed."""

    def __init__(self, answer: str | None = "deepseek") -> None:
        self.calls: list[dict[str, Any]] = []
        self._answer = answer

    def select(self, title, options, *, default=0, allow_text=False, out=None,
               env=None, keys=None, size=None):  # noqa: ANN001, ANN201 — the documented shape
        self.calls.append(
            {
                "title": title,
                "options": list(options),
                "default": default,
                "allow_text": allow_text,
                "out": out,
                "env": env,
                "size": size,
            }
        )
        return self._answer


def test_the_selector_hands_every_list_to_the_shared_widget_when_it_exists(monkeypatch):
    shared = _Shared()
    monkeypatch.setattr(mp, "shared_picker", lambda: shared)
    sink = io.StringIO()
    answer = mp.select_in_terminal(
        "provider", mp.provider_rows(), stream_out=sink, size=(80, 24),
        default_value="deepseek",
    )
    assert answer == "deepseek"
    assert len(shared.calls) == 1
    call = shared.calls[0]
    assert call["title"] == "provider"
    assert call["options"][:2] == [
        ("1 OpenAI", "openai"),
        ("2 Anthropic (Claude)", "anthropic"),
    ]
    assert call["default"] == next(
        i for i, r in enumerate(mp.provider_rows()) if r.value == "deepseek"
    ), "the index of the pre-filled value follows the expanded plan rows"
    assert call["allow_text"] is False
    assert call["out"] is sink and call["size"] == (80, 24)
    assert sink.getvalue() == "", "the shared widget draws, this module must not"


def test_a_widget_with_a_different_signature_is_not_called_with_the_wrong_meaning(monkeypatch):
    class Wrong:
        def select(self, prompt, choices):  # pragma: no cover — it must never be called
            raise AssertionError("a widget with another vocabulary must not be used")

    module = type("m", (), {"select": Wrong().select})()
    monkeypatch.setattr(mp.importlib, "import_module", lambda *a, **k: module)
    assert mp.shared_picker() is None

    # and the question is still answered by this module's own loop
    answer, _ = _ask(["down", "enter"])
    assert answer == "anthropic"


def test_the_build_now_carries_the_shared_widget_and_hands_it_the_same_rows(monkeypatch):
    """The flip of the old pin: ``skaldir.picker`` landed, so the list is handed over.

    The rows are the swap's whole point — the widget is given the *same* lines, in the same
    order, and the *value* of the pre-filled row as an index, which is what it starts on.
    """
    from skaldir import picker as shared

    assert mp.shared_picker() is shared, "the real widget is found and its shape is accepted"
    assert mp.SHARED_PICKER_CALL.startswith("select(title, options, *, default=0")

    seen: list[dict[str, Any]] = []

    def recorder(title, options, *, default=0, allow_text=False, out=None,
                 env=None, keys=None, size=None):  # noqa: ANN001, ANN201 — the documented shape
        seen.append({
            "title": title, "options": list(options), "default": default,
            "allow_text": allow_text, "out": out, "size": size,
        })
        return "deepseek"

    monkeypatch.setattr(shared, "select", recorder)
    sink = io.StringIO()
    answer = mp.select_in_terminal(
        "provider", mp.provider_rows(), stream_out=sink, size=(80, 24),
        default_value="deepseek",
    )
    assert answer == "deepseek"
    assert len(seen) == 1
    assert seen[0]["title"] == "provider"
    assert seen[0]["options"] == [(row.name, row.value) for row in mp.provider_rows()]
    assert seen[0]["default"] == mp.row_index(mp.provider_rows(), "deepseek")
    assert seen[0]["allow_text"] is False
    assert seen[0]["out"] is sink and seen[0]["size"] == (80, 24)
    assert sink.getvalue() == "", "the shared widget draws, model_picker must not"


# --------------------------------------------------------------------------- #
# 6. one code path: setup and model ask through the same engine                   #
# --------------------------------------------------------------------------- #


def test_both_verbs_ask_their_questions_through_the_same_engine(monkeypatch):
    seen: list[dict[str, Any]] = []

    def recorder(title, rows, **kwargs):  # noqa: ANN001, ANN201
        seen.append({"title": title, "values": [row.value for row in rows], **kwargs})
        return "deepseek"

    monkeypatch.setattr(mp, "choose", recorder)
    env = {"TERM": "dumb"}

    assert scli._choose_provider(
        sf.ROLE_LLM, choices=sf.provider_choices(), default_id="openai", env=env
    ) == "deepseek"
    assert mcli._ask_provider(sf.ROLE_LLM, env=env) == "deepseek"
    assert len(seen) == 2
    for call in seen:
        assert call["values"] == [row.id for row in sf.provider_choices()]
        assert call["allow_text"] is False
        assert callable(call["line_reader"]), "the plain path goes through the caller's reader"
    assert seen[0]["default_value"] == "openai" and seen[1]["default_value"] == "deepseek"


def test_the_model_question_is_a_list_of_the_catalog_s_own_models(monkeypatch):
    seen: list[Any] = []
    monkeypatch.setattr(mp, "choose", lambda title, rows, **kw: seen.append(list(rows)) or "x")
    # the expected ids come FROM the catalog, so a model-id update is one edit in
    # one place and this test follows it instead of going stale
    expected = list(catalog.CATALOG["deepseek"].models)
    assert scli._choose_model(sf.ROLE_LLM, "deepseek", supplied="deepseek-chat") == "x"
    assert [row.value for row in seen[0]] == expected
    assert scli._choose_model(sf.ROLE_LLM, "llamacpp", supplied="") == "", (
        "an endpoint the catalog documents no model for is not given a list of one"
    )


def test_a_cancelled_selection_is_a_refusal_that_writes_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(mp, "choose", lambda title, rows, **kw: "")
    with pytest.raises(sf.SetupRefused):
        scli._choose_provider(sf.ROLE_LLM, choices=sf.provider_choices(), default_id="openai")
    with pytest.raises(sf.SetupRefused):
        mcli._ask_role(env={"TERM": "dumb"})
    assert not list(tmp_path.iterdir())


def test_the_plain_prompt_goes_to_stderr_and_never_the_key(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("yes\n"))
    scli._read_line("  the key (hidden): ")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "the key (hidden)" in captured.err
