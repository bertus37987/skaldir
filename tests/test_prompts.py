"""Prompt registry: versioning, parsing, required inventory."""

import pytest

from skaldir.prompts import REQUIRED_IDS, PromptRegistry


class TestPromptRegistry:
    def test_latest_version_wins(self, tmp_path):
        (tmp_path / "foo.v1.md").write_text("---\npurpose: old\n---\nBODY1\n", encoding="utf-8")
        (tmp_path / "foo.v2.md").write_text("---\npurpose: new\n---\nBODY2\n", encoding="utf-8")
        prompt = PromptRegistry(tmp_path).get("foo")
        assert prompt.version == 2
        assert prompt.body == "BODY2"
        assert prompt.meta["purpose"] == "new"

    def test_unknown_id_raises(self, tmp_path):
        (tmp_path / "foo.v1.md").write_text("---\npurpose: x\n---\nB\n", encoding="utf-8")
        with pytest.raises(KeyError):
            PromptRegistry(tmp_path).get("bar")

    def test_missing_dir_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            PromptRegistry(tmp_path / "nope").load()

    def test_repo_prompts_cover_required_ids(self):
        missing = [pid for pid in REQUIRED_IDS if pid not in PromptRegistry().ids()]
        assert missing == []

    def test_all_repo_prompts_parse_with_consistent_version(self):
        for prompt in PromptRegistry().all():
            assert prompt.meta.get("purpose"), f"{prompt.id} has no purpose"
            assert prompt.body, f"{prompt.id} has an empty body"
            assert int(prompt.meta.get("version", 0)) == prompt.version
