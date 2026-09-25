"""§1.2: EINE Registry-Wahrheit — Resolver + sichere Legacy-Migration.

Rot-first gegen die alte Strecke: die CLI zeigte mit ihrem Default
``.godsv/tools.db`` (cwd-relativ, Parser ``cli.py``) auf eine andere Datenbank
als Builder/Panel/MCP (``$HERMES_HOME/godsv/tools.db``) — out of the box eine
leere Oberfläche. Hier wird geprüft:

- ``resolve_registry`` liefert EINEN Kontext (Runtime-Dir, Tools-DB,
  generated_tools, Projekt-ID) mit Runtime-Default und meldet Legacy-Funde,
  ohne Verhalten zu ändern, wenn die Runtime-DB existiert;
- ``migrate_legacy`` ist trocken per Default, sichert Quelle UND Ziel vor dem
  Merge, überschreibt bei Namenskonflikten nichts still, dokumentiert
  Übernahmen in ``registry_migration.jsonl``, lässt die Quelle unangetastet
  und ist idempotent;
- die CLI-Pfade (``registry status|migrate``, ``tools list|show``) laufen über
  den Parser + ``main()`` (nicht nur über Handler) und zeigen Scope/Version
  aus den Manifesten.
"""

import json
from pathlib import Path

import pytest

from skaldir import cli
from skaldir.capability import project_id_for
from skaldir.registry import (
    default_runtime_dir,
    migrate_legacy,
    registry_snapshot,
    resolve_registry,
)
from skaldir.tools import ToolSpec, ToolStore


def _tmp_home(tmp_path, monkeypatch) -> Path:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("GODSV_HOME", raising=False)
    return home


def _project(tmp_path, monkeypatch, name: str = "proj") -> Path:
    project = tmp_path / name
    project.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(project)
    return project


def _fill_db(path: Path, specs: list[ToolSpec]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    store = ToolStore(path)
    try:
        for spec in specs:
            store.upsert(spec)
    finally:
        store.close()
    return path


def _spec(name: str, **kwargs) -> ToolSpec:
    kwargs.setdefault("title", name)
    return ToolSpec(name=name, **kwargs)


def _names(db: Path) -> list[str]:
    store = ToolStore(db)
    try:
        return sorted(spec.name for spec in store.all())
    finally:
        store.close()


def _get(db: Path, name: str) -> ToolSpec:
    store = ToolStore(db)
    try:
        spec = store.get(name)
    finally:
        store.close()
    assert spec is not None, f"{name} missing in {db}"
    return spec


def _manifest(runtime: Path, name: str, *lines: str) -> Path:
    tool_dir = runtime / "generated_tools" / name
    tool_dir.mkdir(parents=True, exist_ok=True)
    path = tool_dir / "manifest.yaml"
    path.write_text("\n".join([f"name: {name}", *lines]) + "\n", encoding="utf-8")
    return path


class TestResolverDefaults:
    def test_defaults_are_runtime_based_not_cwd(self, tmp_path, monkeypatch):
        home = _tmp_home(tmp_path, monkeypatch)
        project = _project(tmp_path, monkeypatch)
        context = resolve_registry()
        assert context.runtime_dir == home / "skaldir"
        assert context.tools_db == home / "skaldir" / "tools.db"
        assert context.generated_tools_dir == home / "skaldir" / "generated_tools"
        assert context.project_id == project_id_for(project)
        assert context.project_id.startswith("path:")  # outside git: marked fallback
        assert "HERMES_HOME" in context.selection_reason
        # the legacy location is REPORTED (exists=False), never selected
        assert [c["exists"] for c in context.legacy_candidates] == [False]

    def test_godsv_home_overrides_hermes_home(self, tmp_path, monkeypatch):
        _tmp_home(tmp_path, monkeypatch)
        monkeypatch.setenv("GODSV_HOME", str(tmp_path / "godsv-home"))
        context = resolve_registry()
        assert context.runtime_dir == tmp_path / "godsv-home"
        assert context.tools_db == tmp_path / "godsv-home" / "tools.db"
        assert "GODSV_HOME" in context.selection_reason

    def test_default_runtime_dir_matches_env(self, tmp_path, monkeypatch):
        home = _tmp_home(tmp_path, monkeypatch)
        assert default_runtime_dir() == home / "skaldir"

    def test_explicit_db_wins_and_supplies_the_runtime(self, tmp_path, monkeypatch):
        _tmp_home(tmp_path, monkeypatch)
        _project(tmp_path, monkeypatch)
        db = tmp_path / "elsewhere" / "tools.db"
        context = resolve_registry(db=db)
        assert context.tools_db == db
        assert context.runtime_dir == db.parent
        assert context.selection_reason.startswith("explicit --db")

    def test_explicit_runtime_wins(self, tmp_path, monkeypatch):
        _tmp_home(tmp_path, monkeypatch)
        _project(tmp_path, monkeypatch)
        runtime = tmp_path / "rt"
        context = resolve_registry(runtime=runtime)
        assert context.runtime_dir == runtime
        assert context.tools_db == runtime / "tools.db"
        assert context.selection_reason == "explicit --runtime"

    def test_existing_runtime_db_changes_nothing_and_no_legacy(self, tmp_path, monkeypatch):
        home = _tmp_home(tmp_path, monkeypatch)
        _project(tmp_path, monkeypatch)
        runtime = home / "godsv"
        _fill_db(runtime / "tools.db", [_spec("rt_tool", status="approved")])
        context = resolve_registry()
        snapshot = registry_snapshot(context)
        assert snapshot["tools_db_exists"] is True
        assert snapshot["tool_specs"] == 1
        assert snapshot["tool_names"] == ["rt_tool"]
        assert all(c["exists"] is False for c in context.legacy_candidates)
        # nothing was created or moved by resolving
        assert not (tmp_path / "proj" / ".godsv").exists()

    def test_legacy_find_in_cwd_is_reported_with_specs(self, tmp_path, monkeypatch):
        _tmp_home(tmp_path, monkeypatch)
        project = _project(tmp_path, monkeypatch)
        _fill_db(
            project / ".godsv" / "tools.db",
            [_spec("old_a", status="approved", version="0.1.0"), _spec("old_b")],
        )
        context = resolve_registry()
        found = [c for c in context.legacy_candidates if c["exists"]]
        assert len(found) == 1
        assert found[0]["path"] == str(project / ".godsv" / "tools.db")
        assert found[0]["tool_count"] == 2
        assert found[0]["names"] == ["old_a", "old_b"]
        assert found[0]["readable"] is True
        # selection is untouched: the runtime DB stays the target
        assert context.tools_db == tmp_path / "home" / "skaldir" / "tools.db"

    def test_legacy_find_via_explicit_project_path(self, tmp_path, monkeypatch):
        _tmp_home(tmp_path, monkeypatch)
        _project(tmp_path, monkeypatch)
        other = tmp_path / "other_project"
        (other / ".godsv").mkdir(parents=True)
        _fill_db(other / ".godsv" / "tools.db", [_spec("from_other")])
        context = resolve_registry(project=other)
        paths = {c["path"] for c in context.legacy_candidates if c["exists"]}
        assert str(other / ".godsv" / "tools.db") in paths
        assert context.project_id == project_id_for(other)


class TestDryRun:
    def test_dry_run_reports_specs_without_writing_anything(self, tmp_path, monkeypatch):
        _tmp_home(tmp_path, monkeypatch)
        project = _project(tmp_path, monkeypatch)
        legacy = _fill_db(
            project / ".godsv" / "tools.db",
            [_spec("alpha", status="approved", version="0.1.0"), _spec("beta", version="0.2.0")],
        )
        context = resolve_registry()
        report = migrate_legacy(context)
        assert report["apply"] is False
        assert report["migrated"] == 2
        assert report["target_db"] == str(context.tools_db)
        source = report["sources"][0]
        assert source["path"] == str(legacy)
        assert source["backup"] is None
        rows = {row["name"]: row for row in source["tools"]}
        assert rows["alpha"]["status"] == "approved"
        assert rows["alpha"]["version"] == "0.1.0"
        assert rows["alpha"]["updated"] > 0
        assert rows["beta"]["action"] == "new"
        # dry run: no target db, no backups, no journal
        assert not context.tools_db.exists()
        assert not list(legacy.parent.glob("*.bak"))
        assert not (context.runtime_dir / "registry_migration.jsonl").exists()

    def test_no_legacy_is_a_clean_noop_even_on_apply(self, tmp_path, monkeypatch):
        _tmp_home(tmp_path, monkeypatch)
        _project(tmp_path, monkeypatch)
        context = resolve_registry()
        report = migrate_legacy(context, apply=True)
        assert report["sources"] == [] and report["migrated"] == 0
        assert report["target_backup"] is None
        assert not context.tools_db.exists()  # apply creates nothing without a source


class TestApply:
    def _legacy_setup(self, tmp_path, monkeypatch):
        _tmp_home(tmp_path, monkeypatch)
        project = _project(tmp_path, monkeypatch)
        legacy = _fill_db(
            project / ".godsv" / "tools.db",
            [_spec("alpha", status="approved", version="0.1.0"), _spec("beta", status="ready")],
        )
        return legacy, resolve_registry()

    def test_apply_backs_up_source_and_target_then_merges(self, tmp_path, monkeypatch):
        legacy, context = self._legacy_setup(tmp_path, monkeypatch)
        report = migrate_legacy(context, apply=True)
        assert report["migrated"] == 2
        source = report["sources"][0]
        source_backup = Path(source["backup"])
        assert source_backup.is_file()
        assert source_backup.name.startswith("tools.db.pre-migration-")
        assert source_backup.name.endswith(".bak")
        # source survives untouched
        assert legacy.is_file()
        assert _names(legacy) == ["alpha", "beta"]
        # target now carries the records; no target backup (it did not exist)
        assert report["target_backup"] is None
        assert _names(context.tools_db) == ["alpha", "beta"]
        journal = [
            json.loads(line)
            for line in (context.runtime_dir / "registry_migration.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        assert [row["action"] for row in journal] == ["imported", "imported"]
        assert {row["tool"] for row in journal} == {"alpha", "beta"}

    def test_apply_backs_up_an_existing_target(self, tmp_path, monkeypatch):
        legacy, context = self._legacy_setup(tmp_path, monkeypatch)
        _fill_db(context.tools_db, [_spec("existing", status="approved")])
        report = migrate_legacy(context, apply=True)
        target_backup = Path(report["target_backup"])
        assert target_backup.is_file()
        assert target_backup.name.startswith("tools.db.pre-migration-")
        assert _names(context.tools_db) == ["alpha", "beta", "existing"]

    def test_second_apply_is_idempotent(self, tmp_path, monkeypatch):
        legacy, context = self._legacy_setup(tmp_path, monkeypatch)
        migrate_legacy(context, apply=True)
        journal = context.runtime_dir / "registry_migration.jsonl"
        lines_after_first = len(journal.read_text(encoding="utf-8").splitlines())
        second = migrate_legacy(context, apply=True)
        assert second["migrated"] == 0
        assert second["identical"] == 2
        assert second["conflicts"] == []
        assert len(journal.read_text(encoding="utf-8").splitlines()) == lines_after_first
        assert len(list(legacy.parent.glob("*.bak"))) == 1


class TestConflicts:
    def _collision_setup(self, tmp_path, monkeypatch, *, target_ts, source_ts, target_title):
        _tmp_home(tmp_path, monkeypatch)
        project = _project(tmp_path, monkeypatch)
        legacy = _fill_db(
            project / ".godsv" / "tools.db",
            [_spec("shared", title="Legacy record", status="approved")],
        )
        # the legacy record carries an explicit timestamp …
        store = ToolStore(legacy)
        store.upsert(_spec("shared", title="Legacy record", status="approved"), ts=source_ts)
        store.close()
        context = resolve_registry()
        # … and the target its own, different record for the same name
        _fill_db(context.tools_db, [_spec("shared", title="Target record", status="approved")])
        store = ToolStore(context.tools_db)
        store.upsert(
            _spec("shared", title=target_title, status="approved"), ts=target_ts
        )
        store.close()
        return legacy, context

    def test_name_collision_is_surfaced_never_overwritten(self, tmp_path, monkeypatch):
        legacy, context = self._collision_setup(
            tmp_path, monkeypatch, target_ts=5000.0, source_ts=1000.0, target_title="Target wins"
        )
        report = migrate_legacy(context, apply=True)
        assert report["migrated"] == 0
        assert report["conflicts"] == [
            {
                "name": "shared",
                "source": str(legacy),
                "source_updated": 1000.0,
                "target_updated": 5000.0,
                "resolution": "target-kept",
            }
        ]
        payload = _get(context.tools_db, "shared")
        assert payload.title == "Target wins"  # nothing overwritten silently
        journal = json.loads(
            (context.runtime_dir / "registry_migration.jsonl").read_text(encoding="utf-8")
        )
        assert journal["action"] == "target-kept" and journal["tool"] == "shared"

    def test_newer_legacy_record_is_imported_and_journaled(self, tmp_path, monkeypatch):
        legacy, context = self._collision_setup(
            tmp_path, monkeypatch, target_ts=1000.0, source_ts=5000.0, target_title="Target old"
        )
        report = migrate_legacy(context, apply=True)
        assert report["migrated"] == 1
        assert report["conflicts"][0]["resolution"] == "legacy-newer-imported+journaled"
        assert report["sources"][0]["conflicts"][0]["name"] == "shared"
        record = _get(context.tools_db, "shared")
        assert record.title == "Legacy record"
        assert record.updated == 5000.0  # the record's own bookkeeping survives
        journal = json.loads(
            (context.runtime_dir / "registry_migration.jsonl").read_text(encoding="utf-8")
        )
        assert journal["action"] == "imported-newer"
        assert journal["target_updated"] == 1000.0 and journal["source_updated"] == 5000.0

    def test_dry_run_announces_the_takeover_it_would_do(self, tmp_path, monkeypatch):
        _legacy, context = self._collision_setup(
            tmp_path, monkeypatch, target_ts=1000.0, source_ts=5000.0, target_title="Target old"
        )
        report = migrate_legacy(context)
        assert report["apply"] is False
        assert report["conflicts"][0]["resolution"] == "would-import-newer"
        assert _get(context.tools_db, "shared").title == "Target old"
        assert not (context.runtime_dir / "registry_migration.jsonl").exists()


class TestCliSurface:
    def _runtime_with_tool(self, tmp_path, monkeypatch, *, scope="project", project_id="path:/x"):
        home = _tmp_home(tmp_path, monkeypatch)
        _project(tmp_path, monkeypatch)
        runtime = home / "godsv"
        _fill_db(
            runtime / "tools.db",
            [_spec("check_x", title="Check the X", kind="python", status="approved")],
        )
        _manifest(
            runtime,
            "check_x",
            f"scope: {scope}",
            f"project_id: {project_id}",
            "version: 0.1.0",
            "status: ready",
        )
        return runtime

    def test_registry_status_json_via_main(self, tmp_path, monkeypatch, capsys):
        runtime = self._runtime_with_tool(tmp_path, monkeypatch)
        assert cli.main(["registry", "status", "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["runtime_dir"] == str(runtime)
        assert payload["tools_db"] == str(runtime / "tools.db")
        assert payload["tool_specs"] == 1
        assert payload["generated_tools"][0]["name"] == "check_x"
        assert payload["generated_tools"][0]["scope"] == "project"
        assert payload["legacy_candidates"][0]["exists"] is False

    def test_registry_status_text_lists_legacy_find(self, tmp_path, monkeypatch, capsys):
        _tmp_home(tmp_path, monkeypatch)
        project = _project(tmp_path, monkeypatch)
        _fill_db(project / ".godsv" / "tools.db", [_spec("old_a")])
        assert cli.main(["registry", "status"]) == 0
        out = capsys.readouterr().out
        assert "FOUND" in out and "old_a" in out
        assert "registry migrate" in out

    def test_registry_migrate_via_main_applies_with_backup(self, tmp_path, monkeypatch, capsys):
        home = _tmp_home(tmp_path, monkeypatch)
        project = _project(tmp_path, monkeypatch)
        legacy = _fill_db(project / ".godsv" / "tools.db", [_spec("old_a", status="approved")])
        assert cli.main(["registry", "migrate", "--apply"]) == 0
        out = capsys.readouterr().out
        assert "migrated: 1" in out
        assert list(legacy.parent.glob("*.pre-migration-*.bak"))
        assert _names(home / "skaldir" / "tools.db") == ["old_a"]

    def test_tools_list_default_is_the_runtime_db_not_cwd(self, tmp_path, monkeypatch, capsys):
        """Rot-first: the old default read cwd ``.godsv/tools.db`` — an empty view."""
        home = _tmp_home(tmp_path, monkeypatch)
        project = _project(tmp_path, monkeypatch)
        _fill_db(project / ".godsv" / "tools.db", [_spec("cwd_only", status="approved")])
        _fill_db(
            home / "godsv" / "tools.db", [_spec("runtime_tool", status="approved")]
        )
        _manifest(
            home / "godsv", "runtime_tool", "scope: global", "version: 0.2.0", "status: ready"
        )
        assert cli.main(["tools", "list"]) == 0
        out = capsys.readouterr().out
        assert "runtime_tool" in out and "cwd_only" not in out
        assert "scope=global" in out and "version=0.2.0" in out

    def test_tools_list_shows_scope_version_and_project(self, tmp_path, monkeypatch, capsys):
        self._runtime_with_tool(
            tmp_path, monkeypatch, scope="project", project_id="remote:git/own"
        )
        assert cli.main(["tools", "list"]) == 0
        out = capsys.readouterr().out
        assert "scope=project" in out
        assert "version=0.1.0" in out
        assert "project=remote:git/own" in out

    def test_tools_list_without_manifest_marks_missing_facts(self, tmp_path, monkeypatch, capsys):
        home = _tmp_home(tmp_path, monkeypatch)
        _project(tmp_path, monkeypatch)
        _fill_db(home / "godsv" / "tools.db", [_spec("bare_tool", status="approved")])
        assert cli.main(["tools", "list"]) == 0
        assert "scope=- · version=-" in capsys.readouterr().out

    def test_tools_show_json_carries_registry_block(self, tmp_path, monkeypatch, capsys):
        self._runtime_with_tool(tmp_path, monkeypatch, scope="global", project_id="")
        assert cli.main(["tools", "show", "check_x"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["registry"]["scope"] == "global"
        assert payload["registry"]["version"] == "0.1.0"
        assert payload["registry"]["project_id"] == ""

    def test_tools_list_before_first_write_is_not_an_error(self, tmp_path, monkeypatch, capsys):
        _tmp_home(tmp_path, monkeypatch)
        _project(tmp_path, monkeypatch)
        assert cli.main(["tools", "list"]) == 0
        out = capsys.readouterr().out
        assert "not created yet" in out
        assert not (tmp_path / "home" / "godsv" / "tools.db").exists()

    def test_explicit_db_still_wins_in_the_cli(self, tmp_path, monkeypatch, capsys):
        _tmp_home(tmp_path, monkeypatch)
        _project(tmp_path, monkeypatch)
        explicit = tmp_path / "elsewhere" / "tools.db"
        _fill_db(explicit, [_spec("explicit_tool", status="approved")])
        assert cli.main(["tools", "list", "--db", str(explicit)]) == 0
        assert "explicit_tool" in capsys.readouterr().out

    @pytest.mark.parametrize(
        "argv",
        [
            ["tools", "list"],
            ["tools", "show"],
            ["tools", "build"],
            ["tools", "promote-global"],
            ["registry", "status"],
            ["registry", "migrate"],
            ["mcp", "serve"],
        ],
    )
    def test_help_smoke_defines_every_flag_the_handlers_read(self, argv):
        """argparse layer: every subcommand must build and answer --help (exit 0)."""
        with pytest.raises(SystemExit) as excinfo:
            cli.main([*argv, "--help"])
        assert excinfo.value.code == 0

    def test_parser_defaults_point_at_the_registry(self):
        args = cli.build_parser().parse_args(["tools", "list"])
        assert args.db is None
        migrate = cli.build_parser().parse_args(["registry", "migrate", "--apply", "--json"])
        assert migrate.apply is True and migrate.json is True and migrate.db is None
        status = cli.build_parser().parse_args(["registry", "status"])
        assert status.func is cli._cmd_registry_status
