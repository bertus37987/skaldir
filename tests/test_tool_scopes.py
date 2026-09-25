"""§6/§7: eine Registry, mehrere Eigenschaften — Scope ist Sichtbarkeit,
nie eine zusätzliche Berechtigung. Projekt-Tools gelten nur für ihr Projekt;
globale überall; neue Fähigkeiten sind standardmäßig Projekt-Tools; Konflikte
werden über (Projekt>global, dann Version) gelöst und sonst SICHTBAR gemeldet
statt still überschrieben. Altdaten ohne Scope werden nicht still global.

Die Projekt-ID ist stabil über Umbenennungen und Worktrees: remote > shared
git dir > kanonischer Pfad (schwächste Stufe, so vermerkt).
"""

import subprocess

from skaldir.capability import (
    load_tool_entries,
    project_id_for,
    resolve_tool_names,
    visible_tools,
)


def _tool(
    runtime, name, *, scope=None, project_id="", version=1, type_="testing_tool", status="approved"
):
    d = runtime / "generated_tools" / name
    d.mkdir(parents=True, exist_ok=True)
    lines = [f"name: {name}", f"type: {type_}", f"status: {status}"]
    if scope:
        lines.append(f"scope: {scope}")
    if project_id:
        lines.append(f"project_id: {project_id}")
    lines.append(f"version: {version}")
    (d / "manifest.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return d


class TestProjectId:
    def test_stable_across_worktrees(self, tmp_path):
        main = tmp_path / "main"
        main.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=main, check=True)
        subprocess.run(
            [
                "git", "-c", "user.email=t@t", "-c", "user.name=t",
                "commit", "-q", "--allow-empty", "-m", "x",
            ],
            cwd=main,
            check=True,
        )
        wt = tmp_path / "wt"
        subprocess.run(["git", "worktree", "add", "-q", str(wt)], cwd=main, check=True)
        assert project_id_for(main) == project_id_for(wt)
        assert project_id_for(main).startswith("gitdir:")

    def test_remote_wins(self, tmp_path):
        repo = tmp_path / "r"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(
            ["git", "remote", "add", "origin", "git@github.com:Someone/Awesome.git"],
            cwd=repo,
            check=True,
        )
        assert project_id_for(repo) == "remote:github.com/someone/awesome"

    def test_plain_path_fallback_is_marked(self, tmp_path):
        plain = tmp_path / "norepo"
        plain.mkdir()
        assert project_id_for(plain) == "path:" + str(plain.resolve())


class TestVisibility:
    def test_project_tool_only_in_its_project(self, tmp_path):
        _tool(tmp_path, "proj_check", scope="project", project_id="remote:git/x")
        _tool(tmp_path, "other_check", scope="project", project_id="remote:git/y")
        names = {e["name"] for e in visible_tools(tmp_path, "remote:git/x")}
        assert "proj_check" in names and "other_check" not in names

    def test_global_tool_visible_everywhere(self, tmp_path):
        _tool(tmp_path, "glob_check", scope="global")
        assert "glob_check" in {e["name"] for e in visible_tools(tmp_path, "remote:git/whatever")}
        assert "glob_check" in {e["name"] for e in visible_tools(tmp_path, "")}

    def test_legacy_without_scope_never_silently_project_or_global(self, tmp_path):
        _tool(tmp_path, "old_check")  # no scope field at all
        assert "old_check" not in {e["name"] for e in visible_tools(tmp_path, "remote:git/x")}
        assert "old_check" not in {e["name"] for e in visible_tools(tmp_path, "")}
        # … but the cockpit's explicit "everything" view still shows it
        assert "old_check" in {e["name"] for e in visible_tools(tmp_path, "", include_legacy=True)}

    def test_english_and_uppercase_scope_spellings(self, tmp_path):
        _tool(tmp_path, "u_check", scope="GLOBAL")
        assert "u_check" in {e["name"] for e in visible_tools(tmp_path, "")}


class TestConflictResolution:
    def _entry(self, name, scope, version, project_id=""):
        return {
            "name": name, "scope": scope, "version": version,
            "project_id": project_id, "path": f"/x/{name}-{scope}-{version}",
        }

    def test_project_beats_global_on_same_name(self):
        picks, conflicts = resolve_tool_names([
            self._entry("check", "project", 1, project_id="p"),
            self._entry("check", "global", 9),
        ])
        assert not conflicts
        assert [p["scope"] for p in picks] == ["project"]

    def test_higher_version_wins_within_same_scope(self):
        picks, conflicts = resolve_tool_names([
            self._entry("check", "global", 1),
            self._entry("check", "global", 2),
        ])
        assert not conflicts
        assert picks[0]["version"] == 2

    def test_ambiguous_tie_is_surfaced_not_overwritten(self):
        picks, conflicts = resolve_tool_names([
            self._entry("check", "global", 2),
            self._entry("check", "global", 2),
        ])
        assert picks == []
        assert conflicts and conflicts[0]["name"] == "check"
        assert len(conflicts[0]["candidates"]) == 2

    def test_distinct_names_all_picked(self):
        picks, conflicts = resolve_tool_names([
            self._entry("a", "global", 1),
            self._entry("b", "project", 1, project_id="p"),
        ])
        assert {p["name"] for p in picks} == {"a", "b"} and not conflicts


class TestManifestFacts:
    def test_entries_carry_scope_version_and_health_facts(self, tmp_path):
        _tool(
            tmp_path,
            "t1",
            scope="project",
            project_id="remote:git/x",
            version=3,
            status="quarantined",
        )
        entry = next(e for e in load_tool_entries(tmp_path) if e["name"] == "t1")
        assert entry["scope"] == "project"
        assert entry["project_id"] == "remote:git/x"
        assert entry["version"] == 3
        assert entry["status"] == "quarantined"  # health is its own fact, not scope

    def test_unreadable_manifest_is_skipped(self, tmp_path):
        d = tmp_path / "generated_tools" / "broken"
        d.mkdir(parents=True)
        (d / "manifest.yaml").write_text("{{{{ not yaml", encoding="utf-8")
        assert all(e["name"] != "broken" for e in load_tool_entries(tmp_path))

class TestVersionOrdering:
    """Manifests carry ints (promotions) or semver strings (builder)."""

    def test_semver_strings_order_like_versions(self, tmp_path):
        from skaldir.capability import _version_key

        assert _version_key("0.1.0") < _version_key("0.2.0") < _version_key("1.0.0")

    def test_same_family_conflict_resolves_by_semver(self, tmp_path):
        _tool(tmp_path, "v_check", scope="global", version="0.1.0")
        # second family member would be a promotion candidate; resolver picks higher
        picks, conflicts = resolve_tool_names([
            load_tool_entries(tmp_path)[0],
            {
                "name": "v_check",
                "scope": "global",
                "version": 2_000_000,
                "project_id": "",
                "path": "/promoted",
            },
        ])
        assert not conflicts
        assert picks[0]["path"] == "/promoted"

class TestPromotion:
    """§7: project → global is an evidenced, versioned act — never automatic."""

    def _validated_tool(
        self, tmp_path, name="promo_check", project_id="remote:git/origin", secrets=None
    ):
        d = tmp_path / "generated_tools" / name
        d.mkdir(parents=True)
        lines = [
            f"name: {name}",
            "type: testing_tool",
            "status: approved",
            "scope: project",
            f"project_id: {project_id}",
            "version: 0.1.0",
        ]
        if secrets:
            lines.append("required_secrets:")
            lines.extend(f"  - {s}" for s in secrets)
        lines.append("validation:")
        lines.append("  status: validated")
        (d / "manifest.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")
        return d

    def _manifest(self, tmp_path, name):
        import yaml

        return yaml.safe_load(
            (tmp_path / "generated_tools" / name / "manifest.yaml").read_text(encoding="utf-8")
        )

    def test_no_promotion_without_two_foreign_projects(self, tmp_path):
        from skaldir.capability import promote_tool

        self._validated_tool(tmp_path)
        out = promote_tool(tmp_path, "promo_check", evidence_projects=["remote:git/origin"])
        assert out["promoted"] is False and out["candidate"] is True
        assert any("2 independent" in b for b in out["blockers"])
        assert self._manifest(tmp_path, "promo_check")["scope"] == "project"  # unchanged

    def test_no_promotion_without_validation(self, tmp_path):
        from skaldir.capability import promote_tool

        d = self._validated_tool(tmp_path, name="raw_check")
        text = (d / "manifest.yaml").read_text(encoding="utf-8").replace("validated", "unvalidated")
        (d / "manifest.yaml").write_text(text, encoding="utf-8")
        out = promote_tool(
            tmp_path, "raw_check", evidence_projects=["p1", "p2"]
        )
        assert out["promoted"] is False
        assert any("negative tests" in b for b in out["blockers"])

    def test_promotion_writes_global_version_with_provenance(self, tmp_path):
        from skaldir.capability import load_tool_entries, promote_tool, visible_tools

        self._validated_tool(tmp_path)
        out = promote_tool(
            tmp_path, "promo_check", evidence_projects=["remote:git/a", "remote:git/b"]
        )
        assert out["promoted"] is True and out["version"] == "1.0.0"
        manifest = self._manifest(tmp_path, "promo_check")
        assert manifest["scope"] == "global"
        assert manifest["promoted_from"]["project_id"] == "remote:git/origin"
        assert manifest["promoted_from"]["evidence_projects"] == ["remote:git/a", "remote:git/b"]
        # visible everywhere now, exactly once
        entries = load_tool_entries(tmp_path)
        assert entries[0]["scope"] == "global"
        assert "promo_check" in {e["name"] for e in visible_tools(tmp_path, "remote:git/other")}

    def test_plaintext_secret_blocks_promotion(self, tmp_path):
        from skaldir.capability import promote_tool

        self._validated_tool(tmp_path, secrets=["sk-live-123"])
        out = promote_tool(tmp_path, "promo_check", evidence_projects=["p1", "p2"])
        assert out["promoted"] is False
        assert any("secret://" in b for b in out["blockers"])

    def test_secret_handles_pass(self, tmp_path):
        from skaldir.capability import promote_tool

        self._validated_tool(tmp_path, secrets=["secret://openrouter-main"])
        out = promote_tool(tmp_path, "promo_check", evidence_projects=["p1", "p2"])
        assert out["promoted"] is True

    def test_report_lists_candidates_and_blockers(self, tmp_path):
        from skaldir.capability import promotion_report

        self._validated_tool(tmp_path, name="ready_check")
        self._validated_tool(tmp_path, name="unvalidated_check")
        d = tmp_path / "generated_tools" / "unvalidated_check" / "manifest.yaml"
        d.write_text(
            d.read_text(encoding="utf-8").replace("validated", "unvalidated"), encoding="utf-8"
        )
        report = {row["name"]: row for row in promotion_report(tmp_path)["tools"]}
        assert report["ready_check"]["candidate"] is True and not report["ready_check"]["blockers"]
        assert report["unvalidated_check"]["candidate"] is False
        assert any("negative tests" in b for b in report["unvalidated_check"]["blockers"])


class TestProjectIdPlumbing:
    def test_capability_request_carries_project_id_into_tool_request(self):
        from skaldir.capability import CapabilityRequest

        request = CapabilityRequest(capability="x", project_id="remote:git/x")
        assert request.to_tool_request().project_id == "remote:git/x"

    def test_builder_manifest_writes_the_request_project_id(self):
        import inspect

        from skaldir.toolbuilder import ToolBuilder, ToolBuildRequest

        assert "project_id" in ToolBuildRequest.__dataclass_fields__
        assert "request.project_id" in inspect.getsource(ToolBuilder.build)


def test_cli_promote_global_runs_promote_tool(tmp_path, capsys):
    """T11: scope promotion gains a real entry point (CLI)."""
    import argparse

    from skaldir import cli

    runtime = tmp_path / "godsv"
    tool_dir = runtime / "generated_tools" / "tool_x"
    tool_dir.mkdir(parents=True)
    (tool_dir / "manifest.yaml").write_text(
        "status: ready\nscope: project\nproject_id: own-proj\nversion: 1\n"
        "type: testing\nvalidation:\n  status: validated\n",
        encoding="utf-8",
    )
    ns = argparse.Namespace(
        name="tool_x",
        evidence_project=["other-1", "other-2"],
        db=str(runtime / "tools.db"),
        runtime="",
        json=True,
    )
    assert cli._cmd_tools_promote_global(ns) == 0
    out = capsys.readouterr().out
    assert '"promoted": true' in out
    manifest = (tool_dir / "manifest.yaml").read_text(encoding="utf-8")
    assert "scope: global" in manifest


def test_cli_promote_global_refuses_without_evidence(tmp_path, capsys):
    import argparse

    from skaldir import cli

    runtime = tmp_path / "godsv"
    tool_dir = runtime / "generated_tools" / "tool_y"
    tool_dir.mkdir(parents=True)
    (tool_dir / "manifest.yaml").write_text(
        "status: ready\nscope: project\nproject_id: own-proj\nversion: 1\n"
        "type: testing\nvalidation:\n  status: validated\n",
        encoding="utf-8",
    )
    ns = argparse.Namespace(
        name="tool_y", evidence_project=["only-one"], db=str(runtime / "tools.db"),
        runtime="", json=False,
    )
    assert cli._cmd_tools_promote_global(ns) == 1
    assert "not promoted" in capsys.readouterr().out


# ---------------------------------------------------------------- §1.9 CLI-Seite
# Scope-Promotion hat jetzt einen echten CLI-Eintritt mit Belegen: Parser UND
# Handler laufen über ``main()`` (nicht nur der Handler). Belege sind variable
# Eingaben (Projekt-PFAD oder bereits aufgelöste Projekt-ID); <2 fremde
# Kontexte, fehlende Negativtests und Klartext-Secrets bleiben Blocker.
# "global" heißt nutzerweit lokal in DIESER Installation — nie veröffentlicht.


def _git_repo(path, remote):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "remote", "add", "origin", remote], cwd=path, check=True)
    return path


def _validated_project_tool(runtime, name, *, project_id, validation="validated"):
    d = runtime / "generated_tools" / name
    d.mkdir(parents=True, exist_ok=True)
    lines = [
        f"name: {name}",
        "type: testing_tool",
        "status: approved",
        "scope: project",
        f"project_id: {project_id}",
        "version: 0.1.0",
        "required_secrets:",
        "  - secret://unused-handle",
        "validation:",
        f"  status: {validation}",
    ]
    (d / "manifest.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return d


class TestCliPromotionEvidence:
    def test_two_foreign_contexts_promote_via_main(self, tmp_path, capsys):
        import json

        import yaml

        from skaldir import cli

        runtime = tmp_path / "godsv"
        own = _git_repo(tmp_path / "own", "git@github.com:Someone/Own.git")
        evidence_a = _git_repo(tmp_path / "ev-a", "git@github.com:Someone/A.git")
        evidence_b = _git_repo(tmp_path / "ev-b", "git@github.com:Someone/B.git")
        _validated_project_tool(runtime, "promo_x", project_id=project_id_for(own))

        rc = cli.main(
            [
                "tools", "promote-global", "promo_x",
                "--runtime", str(runtime),
                "--evidence-project", str(evidence_a),
                "--evidence-project", str(evidence_b),
                "--json",
            ]
        )
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["promoted"] is True
        assert payload["version"] == "1.0.0"
        manifest = yaml.safe_load(
            (runtime / "generated_tools" / "promo_x" / "manifest.yaml").read_text(encoding="utf-8")
        )
        assert manifest["scope"] == "global"
        # paths were normalized to stable ids (remote:human/repo)
        assert manifest["promoted_from"]["evidence_projects"] == [
            "remote:github.com/someone/a",
            "remote:github.com/someone/b",
        ]

    def test_variable_evidence_inputs_path_and_id(self, tmp_path, capsys):
        import yaml

        from skaldir import cli

        runtime = tmp_path / "godsv"
        own = _git_repo(tmp_path / "own", "git@github.com:Someone/Own.git")
        evidence_path = _git_repo(tmp_path / "ev-a", "git@github.com:Someone/A.git")
        _validated_project_tool(runtime, "promo_mix", project_id=project_id_for(own))

        rc = cli.main(
            [
                "tools", "promote-global", "promo_mix",
                "--runtime", str(runtime),
                "--evidence-project", str(evidence_path),  # a path
                "--evidence-project", "remote:github.com/someone/b",  # already an id
            ]
        )
        assert rc == 0
        assert "promoted" in capsys.readouterr().out
        manifest_path = runtime / "generated_tools" / "promo_mix" / "manifest.yaml"
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        assert manifest["promoted_from"]["evidence_projects"] == [
            "remote:github.com/someone/a",
            "remote:github.com/someone/b",
        ]

    def test_fewer_than_two_foreign_contexts_block(self, tmp_path, capsys):
        from skaldir import cli

        runtime = tmp_path / "godsv"
        own = _git_repo(tmp_path / "own", "git@github.com:Someone/Own.git")
        only = _git_repo(tmp_path / "ev-a", "git@github.com:Someone/A.git")
        _validated_project_tool(runtime, "promo_solo", project_id=project_id_for(own))

        rc = cli.main(
            [
                "tools", "promote-global", "promo_solo",
                "--runtime", str(runtime),
                "--evidence-project", str(only),
                "--evidence-project", project_id_for(own),  # own context is no evidence
            ]
        )
        assert rc == 1
        out = capsys.readouterr().out
        assert "not promoted" in out and "2 independent" in out
        manifest = (runtime / "generated_tools" / "promo_solo" / "manifest.yaml").read_text(
            encoding="utf-8"
        )
        assert "scope: project" in manifest  # unchanged on refusal

    def test_unvalidated_tool_is_blocked_even_with_evidence(self, tmp_path, capsys):
        from skaldir import cli

        runtime = tmp_path / "godsv"
        own = _git_repo(tmp_path / "own", "git@github.com:Someone/Own.git")
        evidence_a = _git_repo(tmp_path / "ev-a", "git@github.com:Someone/A.git")
        evidence_b = _git_repo(tmp_path / "ev-b", "git@github.com:Someone/B.git")
        _validated_project_tool(
            runtime, "promo_raw", project_id=project_id_for(own), validation="unvalidated"
        )

        rc = cli.main(
            [
                "tools", "promote-global", "promo_raw",
                "--runtime", str(runtime),
                "--evidence-project", str(evidence_a),
                "--evidence-project", str(evidence_b),
            ]
        )
        assert rc == 1
        out = capsys.readouterr().out
        assert "not promoted" in out and "negative tests" in out

    def test_plaintext_secret_blocks_promotion_via_main(self, tmp_path, capsys):
        from skaldir import cli

        runtime = tmp_path / "godsv"
        tool = _validated_project_tool(
            runtime, "promo_secret", project_id="remote:git/own",
            validation="validated",
        )
        text = (tool / "manifest.yaml").read_text(encoding="utf-8").replace(
            "secret://unused-handle", "sk-live-plaintext"
        )
        (tool / "manifest.yaml").write_text(text, encoding="utf-8")
        rc = cli.main(
            [
                "tools", "promote-global", "promo_secret",
                "--runtime", str(runtime),
                "--evidence-project", "remote:git/a",
                "--evidence-project", "remote:git/b",
            ]
        )
        assert rc == 1
        assert "secret://" in capsys.readouterr().out

    def test_help_states_global_is_user_wide_and_never_published(self, capsys):
        import re

        import pytest as _pytest

        from skaldir import cli

        # the sentence lives in the parent listing (argparse shows it only there)
        with _pytest.raises(SystemExit) as excinfo:
            cli.main(["tools", "--help"])
        assert excinfo.value.code == 0
        listing = re.sub(r"\s+", " ", capsys.readouterr().out)
        assert "user-wide in this installation, never published" in listing
        assert "foreign project contexts" in listing
        # … and the subcommand itself still answers --help
        with _pytest.raises(SystemExit) as excinfo:
            cli.main(["tools", "promote-global", "--help"])
        assert excinfo.value.code == 0

