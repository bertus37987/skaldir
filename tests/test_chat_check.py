"""The ChatBar's first-use Computer-Use gate (owner requirement).

A `computer_use` living action that would drive the **user's own session** is refused
by default (``CUPolicy.require_isolation``, ``cu_host_session.live_desktop_optin``).
The statement asks **once** — a Slider-style prompt in the ChatBar — and the session
answer must stick for the session:

* headless / non-tty: unchanged refusal, no prompt, nothing auto-allowed;
* tty, first live action: prompt with `allow once` / `allow session` / `deny`;
* ``allow session`` persists in the shell's runtime state and the agent proceeds
  without being asked again this session;
* ``deny`` keeps the refusal, with the reason the user gave.

State lives in the shell ("runtime state"), not on disk: a new session asks again.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest


def _mkchatbar():
    import skaldir.chatbar as chatbar

    return chatbar


def test_headless_default_is_refused_and_never_prompted():
    chatbar = _mkchatbar()
    shell = SimpleShell(tty=False, session_id="s1")
    decision = chatbar.cut_first_use_decision(shell, interactive=False)
    assert decision["decision"] == "refused"
    assert decision["prompted"] is False
    assert "headless" in decision["reason"].lower()


def test_first_use_prompts_a_tty_user_and_the_session_answer_persists():
    chatbar = _mkchatbar()
    shell = SimpleShell(tty=True, session_id="s1", answers=["session"])
    first = chatbar.cut_first_use_decision(shell, interactive=True)
    assert first["prompted"] is True
    assert first["decision"] == "session_allow"
    # the answer sticks: the second live action is NOT asked again
    second = chatbar.cut_first_use_decision(shell, interactive=True)
    assert second["prompted"] is False
    assert second["decision"] == "session_allow"


def test_allow_once_is_available_but_not_persisted():
    chatbar = _mkchatbar()
    shell = SimpleShell(tty=True, session_id="s2", answers=["once"])
    first = chatbar.cut_first_use_decision(shell, interactive=True)
    assert first["decision"] == "once_allow"
    assert first.get("persisted") is not True
    # the next action has the same one-shot rights inside this turn
    assert first.get("granted_now") is True


def test_deny_keeps_the_refusal_with_the_reason():
    chatbar = _mkchatbar()
    shell = SimpleShell(
        tty=True, session_id="s3", answers=["deny", "I am using the desktop right now"]
    )
    decision = chatbar.cut_first_use_decision(shell, interactive=True)
    assert decision["decision"] == "refused"
    assert decision["prompted"] is True
    assert "I am using the desktop right now" in decision["reason"]


def test_the_framed_approve_bar_moves_with_left_right_and_enter():
    """Owner UX: the opt-in opens as an approve bar — ←/→ then Enter."""
    from skaldir import cut as cut_mod

    class PickerShell(SimpleShell):
        def __init__(self, keys, **kwargs):
            super().__init__(**kwargs)
            self._keys = list(keys)
            self.chooser = None

        def pick(self, chooser):
            from skaldir.picker import SELECT, CANCEL

            self.chooser = chooser
            for key in self._keys:
                decision = chooser.handle(key)
                if decision in (SELECT, CANCEL):
                    break
            return chooser.result()

    # → once = "allow for this session", Enter
    shell = PickerShell(["right", "enter"], tty=True, session_id="p1", answers=[])
    decision = cut_mod.live_desktop_decision(shell, interactive=True, goal="open a window")
    assert decision["decision"] == "session_allow"
    assert decision["prompted"] is True
    assert decision["persisted"] is True
    # the approve bar really rendered horizontally
    rows = shell.chooser.rows()
    assert any("allow once" in row and "deny" in row for row in rows)
    assert any("←/→" in row or "Enter" in row for row in rows)


def test_the_approve_bar_enter_on_the_first_option_is_allow_once():
    from skaldir import cut as cut_mod

    class PickerShell(SimpleShell):
        def __init__(self, keys, **kwargs):
            super().__init__(**kwargs)
            self._keys = list(keys)

        def pick(self, chooser):
            for key in self._keys:
                if chooser.handle(key) in ("select", "cancel"):
                    break
            return chooser.result()

    shell = PickerShell(["enter"], tty=True, session_id="p2")
    decision = cut_mod.live_desktop_decision(shell, interactive=True)
    assert decision["decision"] == "once_allow"
    assert decision["persisted"] is not True


def test_left_arrow_wraps_to_deny_and_esc_cancels():
    from skaldir import cut as cut_mod

    class PickerShell(SimpleShell):
        def __init__(self, keys, **kwargs):
            super().__init__(**kwargs)
            self._keys = list(keys)

        def pick(self, chooser):
            for key in self._keys:
                if chooser.handle(key) in ("select", "cancel"):
                    break
            return chooser.result()

    # from "allow once", ← wraps to "deny" (last option)
    shell = PickerShell(["left", "enter"], tty=True, session_id="p3", answers=["because no"])
    decision = cut_mod.live_desktop_decision(shell, interactive=True)
    assert decision["decision"] == "refused"
    assert "because no" in decision["reason"]

    shell2 = PickerShell(["esc"], tty=True, session_id="p4")
    decision2 = cut_mod.live_desktop_decision(shell2, interactive=True)
    assert decision2["decision"] == "refused"
    assert "cancelled" in decision2["reason"]


def test_the_clock_ticks_during_a_turn_and_stops_after():
    """A silent tool used to freeze the band on ``00:02`` (no stream, no draw)."""
    import time

    from skaldir import chatbar as cb

    bar = cb.ChatBar(width=80, unicode=False, fd=-1)
    bar.begin_turn("tool")
    assert bar.turn is not None
    time.sleep(1.1)
    assert bar.turn.elapsed >= 1.0, bar.turn.elapsed
    assert bar.turn.clock() != "00:00"
    bar.end_turn()
    assert bar.turn is None
    assert bar._last_clock != "00:00"


def test_agent_tool_progress_drives_the_live_row(monkeypatch):
    """tool.start → live row names the tool; model.request → thinking."""
    from skaldir import agent_loop_cli as alc

    states: list[tuple[str, str]] = []
    lines: list[str] = []

    class FakeFront:
        def activity(self, state, detail="", **_kw):
            states.append((state, detail))

    class FakeProgress:
        def event(self, kind, fields=None, **extra):
            return f"{kind}"

    import os

    monkeypatch.setattr(os, "_skaldir_chat_front", FakeFront(), raising=False)
    monkeypatch.setattr(alc, "_human", lambda text="": lines.append(str(text)))
    event = alc._chat_progress(FakeProgress(), chat_output=True)
    event("model.request", {"step": 1})
    event("tool.start", {"name": "skill_act", "arguments": {"runner": "auto"}})
    event("tool.end", {"name": "skill_act", "ok": True, "duration_ms": 12})
    event("model.response", {"tool_calls": 0, "text_chars": 42})
    assert states[0] == ("thinking", "step 1")
    assert states[1] == ("tool", "skill_act")
    assert states[2][0] == "working"
    # every step is a history line (the owner's "actual chat history")
    assert any("thinking" in line for line in lines)
    assert any("→ skill_act" in line for line in lines)
    assert any("← skill_act ok" in line for line in lines)
    assert any("answer 42 chars" in line for line in lines)


def test_state_is_runtime_only_not_a_file(tmp_path):
    chatbar = _mkchatbar()
    shell = SimpleShell(tty=True, session_id="s4", answers=["session"], tmp=tmp_path)
    chatbar.cut_first_use_decision(shell, interactive=True)
    # Only runtime state — no settings file flip on the first-use answer.
    if isinstance(tmp_path, Path):
        survivors = [str(p) for p in tmp_path.rglob("*")]
        assert survivors == [], f"nothing should be persisted on disk, but {survivors}"


# --------------------------------------------------------------------------- #
# a minimal stand-in shell: tty flag, answers, runtime dir                     #
# --------------------------------------------------------------------------- #


class SimpleShell:
    def __init__(self, *, tty: bool, session_id: str, answers=None, tmp=None):
        import tempfile

        self.tty = tty
        self.session_id = session_id
        self.cx_answer_index = 0
        self.cx_answers = list(answers or [])
        self.out = io.StringIO()
        self.cx_temp = tmp or tempfile.mkdtemp()

    def paint(self, text, _style=""):
        return text

    def say(self, text=""):
        self.out.write(text + "\n")

    def ask(self, prompt_text: str, *, default: str = "") -> str:
        """One answer from the user, from the scripted queue (like repl.Shell.ask)."""
        if not self.tty:
            return default
        if self.cx_answer_index >= len(self.cx_answers):
            return default
        answer = self.cx_answers[self.cx_answer_index]
        self.cx_answer_index += 1
        return answer

    @property
    def interactive(self) -> bool:
        return self.tty


@pytest.fixture()
def no_existing_optin(monkeypatch):
    """No configured opt-in: the environment, the settings, everything empty."""
    import os

    for key in ("SKALDIR_CU_ALLOW_LIVE_DESKTOP", "SKALDIR_CU_LIVE_TARGET"):
        monkeypatch.delenv(key, raising=False)
    return monkeypatch
