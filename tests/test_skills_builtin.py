"""Preinstalled (builtin) skills shipped with the product: usable out of the box."""

from skaldir.skill_builtin import BUILTIN_SKILL_NAMES, load_builtin
from skaldir.skills import SkillStore, is_valid_skill_body, parse_skill_header


def test_builtin_catalog_is_nonempty_and_unique():
    assert 4 <= len(BUILTIN_SKILL_NAMES) <= 8
    assert len(set(BUILTIN_SKILL_NAMES)) == len(BUILTIN_SKILL_NAMES)


def test_every_builtin_body_is_valid_skill_md():
    for name in BUILTIN_SKILL_NAMES:
        body = load_builtin(name)
        assert is_valid_skill_body(body), f"{name}: invalid SKILL.md body"
        header = parse_skill_header(body)
        assert header["name"] == name
        assert len(header.get("description", "")) >= 10
        assert "# " in body  # actionable headings, English-only file


def test_load_builtin_unknown_name_raises():
    try:
        load_builtin("no-such-skill")
    except KeyError:
        return
    raise AssertionError("expected KeyError")


def test_import_builtin_into_store(tmp_path):
    """Import-only plumbing: a builtin becomes a promoted store candidate."""
    from skaldir.skill_builtin import import_builtin

    store = SkillStore(tmp_path / "memory.db")
    candidate = import_builtin(store, BUILTIN_SKILL_NAMES[0])
    row = store.get(candidate.name)
    assert row is not None and row.status == "promoted"
    assert row.body == load_builtin(BUILTIN_SKILL_NAMES[0])
    store.close()


def test_import_builtin_twice_is_idempotent(tmp_path):
    from skaldir.skill_builtin import import_builtin

    store = SkillStore(tmp_path / "memory.db")
    import_builtin(store, BUILTIN_SKILL_NAMES[0])
    second = import_builtin(store, BUILTIN_SKILL_NAMES[0])
    assert store.count() == 1
    assert second.name == BUILTIN_SKILL_NAMES[0]
