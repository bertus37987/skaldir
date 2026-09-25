"""An empty value slot must not collapse to the flags' shared "--" on Tab.

``/resume <Tab>`` and ``/skills-promote <Tab>`` fill a *value* slot (a session id, a
skill name). When the backing store is empty the menu used to fall through to the
verb's flags, whose shared prefix is ``--`` — so Tab inserted ``--`` and the slot read
as "completion is dead". A value slot with nothing saved now says so, and never offers
the flags at the empty slot (typing ``-`` still reaches them).
"""

from __future__ import annotations

from skaldir import complete as C


def _tree():
    from skaldir import cli

    return C.build_tree(cli.build_parser())


def test_empty_session_store_says_so_instead_of_inserting_dashdash(monkeypatch):
    tree = _tree()
    monkeypatch.setitem(C.VALUE_SOURCES, "session", lambda: [])  # fresh home: no sessions
    comp = C.complete_line(tree, "/resume ", len("/resume "))
    assert comp.insert != "--", f"Tab inserted {comp.insert!r} — the flags' shared prefix"
    kinds = {s.kind for s in comp.suggestions}
    assert "option" not in kinds, f"value slot offered flags: {[s.text for s in comp.suggestions]}"
    assert any("session" in s.text.lower() or "session" in s.description.lower()
               for s in comp.suggestions), [s.text for s in comp.suggestions]
    # the honest hint is never insertable (it is not a real id)
    hint = [s for s in comp.suggestions if not s.insertable]
    assert hint, "the empty-store hint must be present and non-insertable"


def test_filled_session_store_offers_the_ids_at_the_value_slot(monkeypatch):
    tree = _tree()
    monkeypatch.setitem(
        C.VALUE_SOURCES, "session", lambda: [("s-1234", "Test session")]
    )
    comp = C.complete_line(tree, "/resume ", len("/resume "))
    texts = [s.text for s in comp.suggestions]
    assert "s-1234" in texts, texts
    assert "--" != comp.insert


def test_typing_a_dash_still_reaches_the_flags(monkeypatch):
    """The value slot hides flags at the empty slot, but '-' is the documented way in."""
    tree = _tree()
    monkeypatch.setitem(C.VALUE_SOURCES, "session", lambda: [])
    comp = C.complete_line(tree, "/resume --", len("/resume --"))
    texts = [s.text for s in comp.suggestions]
    assert any(t.startswith("--") for t in texts), texts
