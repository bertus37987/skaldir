"""`memory-search` must find what `memory remember` wrote — one memory, not two stores.

`memory remember` writes the typed store (`typed_memory.db`); `memory-search` used to read
only the legacy `memory.db`, so a successful remember was followed by "no memory db". The
search is now unified over both stores and labels where each hit came from.
"""

from __future__ import annotations

from argparse import Namespace

from skaldir import cli
from skaldir.memory_store import TypedMemoryStore


def test_search_finds_a_remembered_fact_when_only_the_typed_store_exists(tmp_path, capsys):
    """The exact owner flow: remember → search, with NO legacy memory.db present."""
    typed = tmp_path / "typed_memory.db"
    with TypedMemoryStore(typed) as store:
        store.remember("the sky is blue")
    args = Namespace(db=str(tmp_path / "memory.db"), query="sky", signature="", limit=5)
    code = cli._cmd_memory_search(args)
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert code == 0, combined
    assert "no memory db" not in combined.lower(), combined
    assert "sky" in combined.lower(), combined


def test_search_labels_the_typed_store_as_the_source(tmp_path, capsys):
    typed = tmp_path / "typed_memory.db"
    with TypedMemoryStore(typed) as store:
        store.remember("the sky is blue")
    args = Namespace(db=str(tmp_path / "memory.db"), query="sky", signature="", limit=5)
    code = cli._cmd_memory_search(args)
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert code == 0, combined
    # the typed store is named as the source — a hit is never unattributed
    assert "typed" in combined.lower(), combined


def test_search_with_neither_store_says_no_memory_db(tmp_path, capsys):
    args = Namespace(db=str(tmp_path / "absent.db"), query="x", signature="", limit=5)
    code = cli._cmd_memory_search(args)
    captured = capsys.readouterr()
    assert code == 1
    assert "no memory db" in (captured.out + captured.err).lower()
