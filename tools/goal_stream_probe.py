#!/usr/bin/env python
"""Stream-reconnect (S03) and goal-revision (S04) probe.

Both criteria are driven through the product's OWN functions, never through a test double:

S03  Streaming/reconnect: the chat polls `events_since_payload` with the last seen sequence.
     Reconnect must produce neither duplicates nor lost final results. Counter-cases: non-chat
     kinds must never reach the chat feed, and a secret payload must be refused (or redacted on
     the explicit redacting path) instead of travelling to the UI.
S04  Goals: criteria edited while work is running must bump the revision; a stale
     `expected_revision` must be refused instead of silently applied, a repeated request must
     replay idempotently, and evidence bound to an older revision/timestamp must not count as
     fresh.

Run:  .venv/bin/python tools/goal_stream_probe.py
"""

from __future__ import annotations

import inspect
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from godsv.completion import CompletionStore, CriterionConflict, DodItem, RunEvidence  # noqa: E402
from godsv.events import CHAT_KINDS, KINDS, EventStore, SecretLeakError  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integrations" / "hermes"))
from godsv_plugin.dashboard import plugin_api  # noqa: E402

checks: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str) -> None:
    checks.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}", flush=True)


def feed(after: int, *, db: Path, session: str, limit: int = 200) -> tuple[int, list[dict]]:
    payload = plugin_api.events_since_payload(after_seq=after, limit=limit, events_db=db, session_id=session)
    events = payload.get("events") or payload.get("items") or []
    cursor = payload.get("latest_seq", payload.get("cursor"))
    if cursor is None and events:
        cursor = events[-1].get("seq") or events[-1].get("id")
    return int(cursor or after), list(events)


def part_s03() -> None:
    chat_kinds = sorted(CHAT_KINDS)[:3]
    other_kinds = sorted(KINDS - CHAT_KINDS)[:2]
    with tempfile.TemporaryDirectory(prefix="godsV-s03-") as tmp:
        db = Path(tmp) / "events.db"
        store = EventStore(db)
        session = "probe-session"
        for step, kind in enumerate(chat_kinds, start=1):
            store.append(session, step, kind, {"note": f"chat event {kind}"})
        for step, kind in enumerate(other_kinds, start=10):
            store.append(session, step, kind, {"note": f"panel-only event {kind}"})

        cursor1, first = feed(0, db=db, session=session)
        kinds_seen = {e.get("kind") for e in first}
        check("chat feed carries the chat kinds and hides panel-only kinds",
              kinds_seen == set(chat_kinds) and not (kinds_seen & set(other_kinds)),
              f"feed kinds={sorted(kinds_seen)}, panel-only={other_kinds}")

        cursor2, again = feed(cursor1, db=db, session=session)
        check("reconnect with the last seen sequence delivers nothing twice",
              again == [] and cursor2 == cursor1, f"reconnect returned {len(again)} events")

        for step, kind in ((20, chat_kinds[0]), (21, chat_kinds[1])):
            store.append(session, step, kind, {"note": "produced while the client was away"})
        cursor3, tail = feed(cursor2, db=db, session=session)
        check("every event produced during the disconnect is delivered",
              {e.get("kind") for e in tail} == {chat_kinds[0], chat_kinds[1]},
              f"tail kinds={sorted({e.get('kind') for e in tail})}")
        check("the feed delivers newest first, as its own contract says",
              [e["seq"] for e in tail] == sorted((e["seq"] for e in tail), reverse=True),
              f"delivered seqs={[e['seq'] for e in tail]} (newest first)")

        seqs = [e["seq"] for e in first] + [e["seq"] for e in again] + [e["seq"] for e in tail]
        check("no sequence number is delivered twice across reconnects",
              len(seqs) == len(set(seqs)) and set(seqs) == {e.seq for e in store.stream(0) if e.kind in CHAT_KINDS},
              f"{len(seqs)} distinct sequences, completeness against the store holds")

        other_session = "someone-else"
        store.append(other_session, 1, chat_kinds[0], {"note": "another session"})
        _, mine = feed(0, db=db, session=session)
        check("the feed stays inside the requested session",
              all(e.get("session_id") in (session, None) for e in mine)
              and len({e.get("session_id") for e in mine} - {session, None}) == 0,
              f"sessions in feed: {sorted({e.get('session_id') for e in mine})}")

        # Backlog larger than the feed limit: does the documented poll pattern
        # ("poll with the last seen latest_seq") still deliver everything?
        burst = []
        for step in range(100, 160):
            burst.append(store.append(session, step, chat_kinds[0], {"note": f"burst {step}"}).seq)
        # the chat's own default page size, not the probe's generous one
        cursor4, page1 = feed(cursor3, db=db, session=session, limit=40)
        cursor5, page2 = feed(cursor4, db=db, session=session, limit=40)
        delivered = {e["seq"] for e in page1} | {e["seq"] for e in page2}
        skipped = sorted(set(burst) - delivered)
        check("a backlog beyond the limit loses the OLDEST unseen events for the documented "
              "poll pattern (finding AF-14, reproducible)",
              len(page1) == 40 and page2 == [] and len(skipped) == 20,
              f"appended {len(burst)} during one disconnect, poll at the chat default limit=40 returned "
              f"{len(page1)} (the NEWEST ones), the follow-up poll with the returned cursor returned "
              f"{len(page2)}, so {len(skipped)} of the oldest unseen events were never delivered; the "
              f"payload keys carry no truncation flag")

        refused = False
        try:
            store.append(session, 30, chat_kinds[0], {"token": "sk-abcdefghijklmnopqrstuvwxyz012345"})
        except SecretLeakError:
            refused = True
        redacted = store.append_redacted(session, 31, chat_kinds[0], {"token": "sk-abcdefghijklmnopqrstuvwxyz012345"})
        leaked = "sk-abcdefghijklmnopqrstuvwxyz" in json.dumps(redacted.payload)
        check("a secret payload is refused, and the redacting path stores no secret",
              refused and not leaked and redacted.payload.get("_godsv_redacted", 0) >= 1,
              f"refused={refused}, redaction marker={redacted.payload.get('_godsv_redacted')}, secret still present={leaked}")
        store.close()


def part_s04() -> None:
    with tempfile.TemporaryDirectory(prefix="godsV-s04-") as tmp:
        store = CompletionStore(tmp, session_id="probe-goal")
        session_id = "probe-goal"
        try:
            store.begin_goal(session_id, "probe goal for the revision fence",
                             [DodItem(id="c0", text="criterion zero", blocking=True, verification="pytest")])
        except TypeError:
            print("  note: begin_goal signature:", inspect.signature(store.begin_goal))
            raise
        rev_before = int(store.state().get("revision") or 0)
        store.add_criteria(["criterion one"])
        rev_one = int(store.state().get("revision") or 0)
        record, _ = store.add_criteria(["criterion two"], request_id="req-1")
        rev_two = int(store.state().get("revision") or 0)
        replayed, _ = store.add_criteria(["criterion two"], request_id="req-1")
        ids = [item["id"] for item in store.state().get("dod") or []]
        check("editing criteria while the goal runs bumps the revision",
              rev_before == 1 and rev_one == 2 and rev_two == 3,
              f"revision {rev_before} -> {rev_one} -> {rev_two}, criteria={ids}")

        conflict = False
        try:
            store.add_criteria(["criterion three"], expected_revision=rev_one)
        except CriterionConflict as exc:
            conflict = str(getattr(exc, "current_revision", "")) == str(rev_two)
        check("a stale expected_revision is refused instead of silently applied",
              conflict and [i["id"] for i in store.state().get("dod") or []] == ids,
              f"CriterionConflict with current_revision={rev_two}, criteria unchanged={[i['id'] for i in store.state().get('dod') or []]}")

        check("a repeated request id replays idempotently instead of appending twice",
              replayed.get("replayed") is True
              and [i["id"] for i in store.state().get("dod") or []] == ids,
              f"replayed={replayed.get('replayed')}, criteria={[i['id'] for i in store.state().get('dod') or []]}")

        bump = time.time()
        old = RunEvidence(name="c1", cmd="pytest", ok=True, status="passed", ts=bump - 60.0,
                          revision=rev_one, workspace="/somewhere/else", session="other-session")
        fresh = RunEvidence(name="c1", cmd="pytest", ok=True, status="passed", ts=bump + 1.0,
                            revision=rev_two, workspace=str(Path(tmp)), session="probe-goal")
        check("evidence from before the revision bump is not fresh, evidence after it is",
              old.fresh(bump) is False and fresh.fresh(bump) is True,
              f"old.fresh={old.fresh(bump)}, fresh.fresh={fresh.fresh(bump)}")

        state = store.state()
        ledger = state.get("verification") or state.get("evidence") or []
        check("the goal record carries its revision and criteria for later evidence binding",
              bool(state.get("revision")) and bool(state.get("dod")),
              f"state keys={sorted(state)[:8]}, revision={state.get('revision')}, evidence entries={len(ledger) if isinstance(ledger, list) else 'n/a'}")


def main() -> int:
    part_s03()
    part_s04()
    failed = [n for n, ok, _ in checks if not ok]
    print(f"\ngoal/stream probe: {len(checks) - len(failed)}/{len(checks)} checks held"
          + (f" — failed: {failed}" if failed else ""))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
