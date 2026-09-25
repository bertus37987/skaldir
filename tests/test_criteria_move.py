"""Criteria reorder — the plan surface's `move` op on the real completion store.

Owner 17.09.: plan points must be sortable. The order lives in the ledger's own
`dod` list, so the op is a list move with the same revision fence and idempotent
`request_id` every other criterion edit uses — and it must never be able to
masquerade as verification (no status parameter exists).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "integrations" / "hermes" / "godsv_plugin" / "dashboard"))

from skaldir.completion import CompletionStore, CriterionConflict  # noqa: E402


def _store(tmp_path: Path) -> CompletionStore:
    store = CompletionStore(str(tmp_path / "runtime"), session_id="s-ui")
    store.set_goal("ship the plan")
    store.add_criteria(["first point", "second point", "third point"])
    return store


def _ids(store: CompletionStore) -> list[str]:
    return [str(item.get("id")) for item in store.state().get("dod") or []]


def test_move_reorders_the_ledger_list_and_bumps_the_revision(tmp_path: Path) -> None:
    store = _store(tmp_path)
    before = _ids(store)
    revision = int(store.state().get("revision") or 0)

    record = store.move_criterion(before[0], after=before[1])

    assert _ids(store) == [before[1], before[0], before[2]]
    assert int(record.get("revision") or 0) == revision + 1, (
        "a move is a goal edit and bumps the revision"
    )


def test_move_before_a_neighbour_is_exact(tmp_path: Path) -> None:
    store = _store(tmp_path)
    before = _ids(store)

    store.move_criterion(before[2], before=before[0])

    assert _ids(store) == [before[2], before[0], before[1]]


def test_move_needs_exactly_one_anchor(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = _ids(store)[0]

    with pytest.raises(ValueError):
        store.move_criterion(first, before="c2", after="c3")
    with pytest.raises(ValueError):
        store.move_criterion(first)
    with pytest.raises(ValueError):
        store.move_criterion(first, after="c99"), "an unknown anchor must not reorder anything"


def test_move_honours_the_revision_fence_and_never_replays_twice(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first, second, third = _ids(store)
    current = int(store.state().get("revision") or 0)

    # 1) the move itself: order follows the ledger, revision moves forward
    store.move_criterion(first, after=second, request_id="move-1", expected_revision=current)
    assert _ids(store) == [second, first, third]
    after_move = int(store.state().get("revision") or 0)

    # 2) the SAME request id is an idempotent replay: nothing moves, nothing is
    #    written twice (a retry after a dropped response must be safe)
    replay = store.move_criterion(
        first, after=second, request_id="move-1", expected_revision=after_move
    )
    assert replay.get("replayed") is True
    assert _ids(store) == [second, first, third]
    assert int(store.state().get("revision") or 0) == after_move

    # 3) a NEW intent from the stale view is refused, not silently applied
    with pytest.raises(CriterionConflict):
        store.move_criterion(first, after=third, request_id="move-2", expected_revision=current)


def test_move_never_accepts_a_status(tmp_path: Path) -> None:
    """The op surface has no status parameter — a reorder cannot look like proof."""
    store = _store(tmp_path)
    first, second, _third = _ids(store)
    store.move_criterion(first, after=second)
    assert {str(item.get("status")) for item in store.state().get("dod") or []} == {"open"}


def test_panel_op_payload_routes_move(tmp_path: Path, monkeypatch) -> None:
    """`op=move` on the panel API reaches the store with the neighbour intact."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    import plugin_api

    store = _store(tmp_path)
    monkeypatch.setattr(plugin_api, "_completion_store", lambda _sid: store)
    first, second, _third = _ids(store)

    result = plugin_api.criteria_op_payload(
        store,
        {
            "op": "move",
            "id": first,
            "after": second,
            "expected_revision": int(store.state().get("revision") or 0),
        },
    )

    assert result["op"] == "move" and result["id"] == first
    assert _ids(store) == [second, first, _third]
