"""The ChatBar's first-use Computer-Use gate (owner requirement).

A ``computer_use`` live-desktop action that would drive the **user's own session** is
refused by default (``CUPolicy.require_isolation``, ``live_desktop_optin``). The
statement asks *once* — an **approve bar** in the ChatBar — and the session answer
must stick:

* headless / non-tty: unchanged refusal, no prompt, nothing auto-allowed;
* tty, first live action: the approve bar opens with ``←``/``→`` (or ``↑``/``↓``)
  moving and ``Enter`` confirming: ``allow once`` · ``allow for this session`` ·
  ``deny``; ``Esc``/``Ctrl-C`` cancel the question (refusal, no reason needed);
* ``allow session`` persists in the shell's runtime state and the agent proceeds
  without being asked again this session; ``allow once`` grants exactly this turn
  and is deliberately never persisted (the same rule ``approval.py`` keeps);
* ``deny`` may ask one optional reason (the agent is told it).

The state lives in the shell ("runtime state"), not on disk: a new session asks
again. This module decides; the ChatBar renders through its own ``pick``/``say``/
``ask`` seam. The configured opt-in (env / settings.yaml) is respected: an
already opted-in machine never sees the prompt.

Letter keys (``a``/``s``/``d``) still work when the shell only has ``ask`` — the
framed approve bar is preferred, the letters are the plain fallback.
"""

from __future__ import annotations

from typing import Any

#: The approve-bar rows: (label, value). ``←/→`` walks them, ``Enter`` confirms.
OPTIONS: tuple[tuple[str, str], ...] = (
    ("allow once", "once_allow"),
    ("allow for this session", "session_allow"),
    ("allow always (write settings)", "always_allow"),
    ("deny", "refused"),
)

#: The answers the *letter* fallback accepts — keys, words and what they mean.
ANSWERS = (
    ("a", "allow once", "once_allow"),
    ("s", "allow for this session", "session_allow"),
    ("w", "allow always", "always_allow"),
    ("d", "deny", "refused"),
)

#: The runtime-state attribute the session-scoped answer is remembered under.
STATE_ATTR = "cu_live_desktop_allowed"

#: The installed chat shell (the approve bar's host). ``repl.Shell`` registers
#: itself at startup; a headless run leaves this ``None`` and the gate stays closed.
_GATE_SHELL: Any = None


def install_gate(shell: Any) -> None:
    """Register the interactive shell whose ``pick`` draws the approve bar."""
    global _GATE_SHELL
    _GATE_SHELL = shell


def installed_gate() -> Any:
    return _GATE_SHELL


def maybe_approve_live(*, goal: str = "", target: str = "") -> dict[str, Any]:
    """Open the approve bar once when a live-desktop action is about to be refused.

    Called from the agent/tool path (``skill_act``) *before* it reports the
    refusal, so the owner sees the slider in the chat instead of a prose
    explanation. A denied/absent gate returns a plain refusal — never a silent allow.

    **No prompt when already opted in** (owner: "es prompted mich am Anfang trotz"):
    ``live_desktop_optin()`` reads env **and** settings.yaml — when it reports
    ``allowed``, the prompt is skipped entirely (was: the prompt block was printed
    before the short-circuit check, so the owner saw the question every time).
    """
    from .cu_host_session import live_desktop_optin

    optin = live_desktop_optin()
    if optin.get("allowed"):
        return {
            "decision": "configured_allow", "prompted": False, "persisted": True,
            "granted_now": True,
            "target": str(optin.get("target") or ""),
            "reason": optin.get("reason", "already opted in"),
        }
    shell = _GATE_SHELL
    if shell is None:
        return {
            "decision": "refused",
            "prompted": False,
            "persisted": False,
            "granted_now": False,
            "reason": (
                "no interactive shell is installed — the live-desktop approve bar cannot open"
            ),
        }
    # Always print the question first: even if the framed picker cannot draw, the
    # owner sees *why* and the three choices (the letter fallback reads them).
    say = getattr(shell, "say", None)
    if callable(say):
        try:
            say(prompt_block(goal=goal, target=target))
        except Exception:  # noqa: BLE001
            pass
    return first_use_gate(shell, goal=goal, target=target, interactive=True)


def _shell_state(shell: Any, default: str = "") -> str:
    value = getattr(shell, STATE_ATTR, default)
    return str(value if value is not None else default)


def _set_shell_state(shell: Any, value: str) -> None:
    setattr(shell, STATE_ATTR, value)


def prompt_block(*, goal: str = "", target: str = "") -> str:
    """The one-time prompt block, in the shell's own vocabulary (also for a pipe)."""
    target_note = f", scoped to {target!r}" if target else ""
    return (
        "Computer use wants to drive your live desktop"
        f"{target_note} to: {goal}\n"
        "  ←/→ move · Enter approve · Esc cancel\n"
        "  [a] once  [s] session  [w] always  [d] deny"
    )


def prompt_question() -> str:
    return "answer [a/s/d] › "


def reason_question() -> str:
    return "reason (the agent is told this; Enter = none) › "


def approve_picker(*, goal: str = "", target: str = "") -> Any:
    """The framed approve bar (``←``/``→`` + ``Enter``) as a shared Picker."""
    from .picker import Item, Picker

    target_note = f"  (scoped to {target!r})" if target else ""
    picker = Picker(
        items=[Item(value=value, title=label) for label, value in OPTIONS],
        title=f"Approve live desktop?{target_note}  —  {goal or 'a computer-use action'}",
        page=len(OPTIONS),
    )
    # horizontal bar: one line of options + the key hint
    picker.rows = picker.rows_horizontal  # type: ignore[method-assign]
    return picker


def _decide_from_value(
    value: str, *, shell: Any, asked_reason: Any = None, target: str = ""
) -> dict[str, Any]:
    """Map one chosen value onto the decision record (the single place that does)."""
    if value == "once_allow":
        return {
            "decision": "once_allow", "prompted": True, "persisted": False,
            "granted_now": True,
            "target": target or "*",
            "reason": "allowed once by the user",
        }
    if value == "session_allow":
        _set_shell_state(shell, "session")
        return {
            "decision": "session_allow", "prompted": True, "persisted": True,
            "granted_now": True,
            "target": target or "*",
            "reason": "allowed for this session",
        }
    if value == "always_allow":
        # persist to settings.yaml so a new session is already opted in
        _set_shell_state(shell, "session")
        written = {}
        try:
            from .cu_host_session import write_live_optin

            windows = {}
            try:
                from .cu_host_session import list_session_windows

                windows = list_session_windows()
            except Exception:  # noqa: BLE001
                pass
            target = str(target or "")
            if not target:
                rows = windows.get("windows") or []
                target = str(rows[0].get("title") or rows[0].get("id") or "") if rows else "Firefox"
            written = write_live_optin(target)
        except Exception as exc:  # noqa: BLE001 — report, never crash the gate
            written = {"ok": False, "reason": str(exc)}
        return {
            "decision": "always_allow", "prompted": True, "persisted": True,
            "granted_now": True,
            "reason": "allowed always (settings.yaml written)" if written.get("ok")
            else f"allowed always (settings write failed: {written.get('reason')})",
            "settings": written,
            "target": target,
        }
    reason = ""
    if callable(asked_reason):
        reason = str(asked_reason(reason_question()) or "").strip()
    return {
        "decision": "refused", "prompted": True, "persisted": False,
        "granted_now": False,
        "reason": reason or "the user declined the live desktop",
    }


def live_desktop_decision(
    shell: Any,
    *,
    interactive: bool,
    goal: str = "",
    target: str = "",
    _prompt=None,
) -> dict[str, Any]:
    """Decide whether a live-desktop action may run, prompting at most once.

    ``decision`` is one of ``session_allow`` (persisted for this session),
    ``once_allow`` (granted now, remembered for nothing), ``refused`` (with the
    reason), all carrying ``prompted`` (was the user asked in this call) and
    ``persisted`` (does the answer outlive this turn).

    The framed approve bar (``shell.pick``) is preferred: ``←``/``→`` move,
    ``Enter`` confirms. A shell with only ``ask`` gets the letter fallback.
    """
    state = _shell_state(shell)
    if state == "session":
        return {
            "decision": "session_allow", "prompted": False, "persisted": True,
            "granted_now": True, "reason": "allowed for this session earlier",
        }
    if not interactive:
        return {
            "decision": "refused", "prompted": False, "persisted": False,
            "granted_now": False,
            "reason": (
                "headless session — no interactive user can answer the prompt, so the "
                "live desktop stays refused"
            ),
        }

    # (1) framed approve bar: ←/→ + Enter (the owner's UX)
    pick = getattr(shell, "pick", None)
    if callable(pick):
        chooser = approve_picker(goal=goal, target=target)
        try:
            result = pick(chooser)
        except Exception:  # noqa: BLE001 — fall through to letters
            result = None
        if result is not None:
            if getattr(result, "ok", False) and getattr(result, "item", None) is not None:
                want_reason = str(result.item.value) == "refused"
                return _decide_from_value(
                    str(result.item.value),
                    shell=shell,
                    target=target,
                    asked_reason=getattr(shell, "ask", None) if want_reason else None,
                )
            return {
                "decision": "refused", "prompted": True, "persisted": False,
                "granted_now": False,
                "reason": "the user cancelled the live-desktop approve bar",
            }

    # (2) standalone selector (setup-style terminal, no ChatBar frame)
    select = getattr(shell, "picker_select", None)
    if callable(select):
        value = select(prompt_block(goal=goal, target=target), OPTIONS)
        if value in {item[1] for item in OPTIONS}:
            want_reason = value == "refused"
            return _decide_from_value(
                str(value),
                shell=shell,
                asked_reason=getattr(shell, "ask", None) if want_reason else None,
            )
        return {
            "decision": "refused", "prompted": True, "persisted": False,
            "granted_now": False,
            "reason": "the user left the live-desktop approve bar without choosing",
        }

    # (3) letter fallback (pipes / a shell with only ask)
    say = getattr(shell, "say", None)
    ask = getattr(shell, "ask", None)
    if callable(say) and callable(ask):
        say(prompt_block(goal=goal, target=target))
        answer = (str(ask(prompt_question()) or "")).strip().lower()
        if answer in ("a", "once", "allow once", "1", "once_allow"):
            return _decide_from_value("once_allow", shell=shell)
        if answer in ("s", "session", "allow session", "allow for this session", "2",
                     "session_allow"):

            return _decide_from_value("session_allow", shell=shell)
        if answer in ("w", "always", "allow always", "a+", "always_allow"):
            return _decide_from_value("always_allow", shell=shell, target=target)
        reason = ""
        if answer in ("d", "deny", "deny with a reason", "3", "refused"):
            reason = str(ask(reason_question()) or "").strip()
        return {
            "decision": "refused", "prompted": True, "persisted": False,
            "granted_now": False,
            "reason": reason or "the user declined the live desktop",
        }

    return {
        "decision": "refused", "prompted": False, "persisted": False,
        "granted_now": False,
        "reason": (
            "headless session — no interactive user can answer the prompt, so the "
            "live desktop stays refused"
        ),
    }


def first_use_gate(
    shell: Any,
    *,
    goal: str = "",
    target: str = "",
    interactive: bool | None = None,
) -> dict[str, Any]:
    """The ChatBar's entry: one decision per live-desktop first use.

    An already-configured opt-in (``SKALDIR_CU_ALLOW_LIVE_DESKTOP`` etc.) short
    circuits: the configured switch is the product's rule and the prompt never
    replaces it. A headless session keeps the plain refusal.
    """
    from . import env as _env

    env_allow = str(_env.getenv("SKALDIR_CU_ALLOW_LIVE_DESKTOP", "") or "").strip().lower()
    env_target = str(_env.getenv("SKALDIR_CU_LIVE_TARGET", "") or "").strip()
    allowed = env_allow in ("1", "true", "yes", "on")
    if allowed and (env_target or target):
        return {
            "decision": "configured_allow", "prompted": False, "persisted": True,
            "granted_now": True,
            "reason": "already opted in via SKALDIR_CU_ALLOW_LIVE_DESKTOP",
        }
    if interactive is None:
        interactive = bool(getattr(shell, "interactive", False))
    return live_desktop_decision(
        shell, interactive=bool(interactive), goal=goal,
        target=target or env_target,
    )
