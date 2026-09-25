"""Knowledge CLI: tested through the real argparse parser + handler.

Punkt 6: knowledge.list/show/search/add must work via the actual CLI entry,
not just as COMMANDS entries.
"""
from __future__ import annotations

import argparse
import json

import pytest

from skaldir.knowledge import KnowledgeStore
from skaldir.knowledge_cli import register_cli, contract_entries


@pytest.fixture
def parser(tmp_path, monkeypatch):
    monkeypatch.setenv("GODSV_HOME", str(tmp_path))
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="command")
    register_cli(sub)
    return p


def test_knowledge_add_via_parser(parser, tmp_path):
    args = parser.parse_args([
        "knowledge", "add", "test-entry",
        "--title", "Test", "--kind", "fact", "--scope", "global",
        "--body", "Hello world.",
    ])
    assert args.func is not None
    code = args.func(args)
    assert code == 0
    store = KnowledgeStore(runtime=tmp_path)
    entry = store.get("test-entry")
    assert entry is not None
    assert entry.title == "Test"


def test_knowledge_list_via_parser(parser, tmp_path, capsys):
    # add first
    args = parser.parse_args([
        "knowledge", "add", "e1", "--title", "E1", "--body", "content",
    ])
    args.func(args)
    capsys.readouterr()
    # then list
    args = parser.parse_args(["knowledge", "list", "--json"])
    code = args.func(args)
    assert code == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert isinstance(data, list)
    assert any(e["id"] == "e1" for e in data)


def test_knowledge_show_via_parser(parser, tmp_path, capsys):
    args = parser.parse_args([
        "knowledge", "add", "e2", "--title", "E2", "--body", "body text",
    ])
    args.func(args)
    capsys.readouterr()
    args = parser.parse_args(["knowledge", "show", "e2", "--json"])
    code = args.func(args)
    assert code == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["id"] == "e2"
    assert data["body"] == "body text"


def test_knowledge_show_not_found_returns_5(parser, capsys):
    args = parser.parse_args(["knowledge", "show", "nope", "--json"])
    code = args.func(args)
    assert code == 5


def test_knowledge_search_via_parser(parser, tmp_path, capsys):
    args = parser.parse_args([
        "knowledge", "add", "e3", "--title", "SearchTest", "--body", "unique_keyword_xyz",
    ])
    args.func(args)
    capsys.readouterr()
    args = parser.parse_args(["knowledge", "search", "unique_keyword_xyz", "--json"])
    code = args.func(args)
    assert code == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["matches"]
    assert data["matches"][0]["id"] == "e3"


def test_contract_entries_cover_all_four_verbs():
    """knowledge.list/show/search/add must all have contract entries."""
    entries = contract_entries()
    assert "knowledge.list" in entries
    assert "knowledge.show" in entries
    assert "knowledge.search" in entries
    assert "knowledge.add" in entries
