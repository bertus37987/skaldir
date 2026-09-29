"""Context compaction modes (owner requirement): ``jev`` | ``always`` | ``off``.

Compaction previously worked only through the Jev-triggered path. ``always`` runs the
deterministic path with the SAME existing trigger machinery (the ``always`` trigger
fires on every ``maybe_compact``), so compaction also works when Jev is off; ``off``
disables; ``jev`` keeps today's behaviour and is the default.
"""

from __future__ import annotations

import pytest

from skaldir import context_manager as C
from tests.test_context_manager import (  # the shared loop-history fixture builders
    loop_history,
    manager,
)

# --------------------------------------------------------------------------- #
# the mode is resolved from the flag first, then the environment                #
# --------------------------------------------------------------------------- #


def test_the_default_mode_is_jev_and_it_is_the_only_today() -> None:
    assert C.DEFAULT_COMPACTION_MODE == "jev"
    assert C.COMPACTION_MODES == ("jev", "always", "off")
    assert manager().mode == "jev"


def test_an_explicit_mode_beats_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SKALDIR_CONTEXT_COMPACTION_MODE", "off")
    assert C.ContextManager(mode="always", asker=C.fixture_asker()).mode == "always"


def test_the_environment_sets_the_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SKALDIR_CONTEXT_COMPACTION_MODE", "always")
    live = manager()
    assert live.mode == "always"


def test_an_unknown_mode_is_a_loud_usage_error_not_a_silent_jev(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValueError) as exc_info:
        manager(mode="ALWYAS")
    assert "one of" in str(exc_info.value)


# --------------------------------------------------------------------------- #
# mode off: compaction is disabled, both spellings agree                        #
# --------------------------------------------------------------------------- #


def test_mode_off_disables_the_trigger() -> None:
    live = manager(mode="off")
    history = loop_history(10)
    outgoing, report = live.maybe_compact(history)
    assert report is None and outgoing == history
    assert live.trigger(history).fire is False


def test_mode_off_agrees_with_the_enabled_flag_and_both_disable() -> None:
    history = loop_history(10)
    assert manager(enabled=False).trigger(history).fire is False
    assert manager(mode="off", enabled=False).trigger(history).fire is False


# --------------------------------------------------------------------------- #
# mode always: the deterministic path, no Jev, same trigger machinery           #
# --------------------------------------------------------------------------- #


def test_mode_always_fires_deterministically_at_woefully_small_history() -> None:
    live = manager(mode="always", budget_tokens=10_000_000)
    history = loop_history(3)
    trigger = live.trigger(history)
    assert trigger.fire is True
    assert trigger.mode == "always"
    assert "always" in trigger.reason.lower()


def test_mode_always_compacts_a_history_the_jev_trigger_would_leave() -> None:
    """The whole point: compaction works when Jev is off."""
    history = loop_history(3)
    jev_live = manager(budget_tokens=10_000_000)
    assert jev_live.trigger(history).fire is False          # jev: below trigger
    always_live = manager(mode="always", budget_tokens=10_000_000)
    outgoing, report = always_live.maybe_compact(history)
    assert report is not None and report.applied is True    # always: it ran
    # ... with the real trigger machinery behind the report
    assert report.trigger["mode"] == "always"
    assert len(outgoing) <= len(history)


def test_mode_jev_keeps_todays_behaviour() -> None:
    """The jev trigger below the threshold stays a no-op (unchanged)."""
    live = manager(mode="jev", budget_tokens=10_000_000)
    history = loop_history(3)
    outgoing, report = live.maybe_compact(history)
    assert report is None and outgoing == history


def test_mode_always_still_honours_the_disabled_switch() -> None:
    live = manager(mode="always", enabled=False)
    outgoing, report = live.maybe_compact(loop_history(3))
    assert report is None and outgoing == loop_history(3)
