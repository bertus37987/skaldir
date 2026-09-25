"""P2-8 — local tool registry + hub manifest builder (separated, no auto-publish)."""

import json
from pathlib import Path

import pytest

from skaldir.toolhub import build_hub_manifest, discover, search


def _runtime(tmp_path) -> Path:
    rt = tmp_path / "godsv"
    tool = rt / "generated_tools" / "check_readme"
    tool.mkdir(parents=True)
    (tool / "manifest.yaml").write_text(
        "name: check_readme\nversion: 0.1.0\ntype: testing_tool\n"
        "purpose: Prove a target ships a README\nstatus: ready\n"
        "validation:\n  status: validated\n  checks:\n  - case: broken\n    ok: true\n"
        "  - case: working\n    ok: true\ncreated_by: godsv-toolbuilder\ncreated_at: 1.0\n"
        "last_tested_at: 2.0\n",
        encoding="utf-8",
    )
    raw = rt / "generated_tools" / "money_convert"
    raw.mkdir(parents=True)
    (raw / "manifest.yaml").write_text(
        "name: money_convert\nversion: 0.1.0\ntype: cli\n"
        "purpose: Convert amounts between currencies\nstatus: draft\n"
        "network:\n  allowed_domains:\n  - rates.example.com\n"
        "filesystem:\n  access: sandboxed workdir\n"
        "required_secrets:\n- secret://rates/example\n",
        encoding="utf-8",
    )
    (rt / "tool_usage.jsonl").write_text(
        "\n".join(
            json.dumps({"ts": float(i), "name": "check_readme", "ok": True}) for i in range(3)
        )
        + "\n",
        encoding="utf-8",
    )
    return rt


def _skills(tmp_path) -> Path:
    skills = tmp_path / "skills"
    sdir = skills / "repo-recap"
    sdir.mkdir(parents=True)
    (sdir / "SKILL.md").write_text(
        "---\nname: repo-recap\n"
        "description: Summarize a repository's recent activity\n---\n# body\n",
        encoding="utf-8",
    )
    return skills


class TestLocalRegistry:
    def test_discover_finds_tools_and_skills(self, tmp_path):
        entries = discover(_runtime(tmp_path), skills_dir=_skills(tmp_path))
        by_name = {e["name"]: e for e in entries}
        assert by_name["check_readme"]["source"] == "generated"
        assert by_name["check_readme"]["verification"]["state"] == "validated"
        assert by_name["check_readme"]["reliability"] == {"uses": 3, "passes": 3, "fails": 0}
        assert by_name["money_convert"]["secrets"] == ["secret://rates/example"]
        assert by_name["repo-recap"]["source"] == "skill"

    def test_search_prefers_relevant_capabilities(self, tmp_path):
        entries = discover(_runtime(tmp_path), skills_dir=_skills(tmp_path))
        hits = search(entries, "verify the readme state of a target")
        assert hits and hits[0]["name"] == "check_readme"

    def test_discover_is_read_only_and_empty_safe(self, tmp_path):
        assert discover(tmp_path / "empty", skills_dir=tmp_path / "none") == []


class TestHubManifest:
    def test_verified_tool_becomes_eligible_with_security_metadata(self, tmp_path):
        rt = _runtime(tmp_path)
        hub = build_hub_manifest(rt, "check_readme")
        assert hub["verification"]["trust"] == "godsv-verified"
        assert hub["verification"]["checks"][0]["case"] == "broken"
        assert hub["publish"]["automatic"] is False
        assert len(hub["checksum_sha256"]) == 64

    def test_unvalidated_or_not_ready_is_refused(self, tmp_path):
        rt = _runtime(tmp_path)
        with pytest.raises(ValueError):
            build_hub_manifest(rt, "money_convert")  # status: draft
        with pytest.raises(ValueError):
            build_hub_manifest(rt, "nope")

    def test_plaintext_secret_is_refused(self, tmp_path):
        rt = _runtime(tmp_path)
        bad = rt / "generated_tools" / "leaky"
        bad.mkdir()
        (bad / "manifest.yaml").write_text(
            "name: leaky\nversion: '1'\ntype: cli\npurpose: x\nstatus: ready\n"
            'note: "api_key: sk-12345"\n',
            encoding="utf-8",
        )
        with pytest.raises(ValueError):
            build_hub_manifest(rt, "leaky")
