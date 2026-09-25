"""End-to-end: demo pipeline numbers + CLI smoke tests."""

from skaldir.cli import main
from skaldir.demo import run_demo

FAKE_OPENAI = "sk-" + "A1b2C3d4E5f6G7h8I9j0K1l2"


class TestDemo:
    def test_demo_is_deterministic(self, tmp_path):
        summary = run_demo(db_path=tmp_path / "demo.db", verbose=False)
        assert summary["events"] == 8
        assert summary["signals"] == 10  # incl. strategy_repeat (same strategy retried)
        assert summary["hint_kinds"] == [
            "error_burst",
            "loop",
            "slop_bare_except",
            "slop_assert_true",
        ]
        for formatted in summary["hints"]:
            assert "verify it yourself" in formatted
        assert "0/2 backed" in summary["ledger_markdown"]
        assert "◐ [ac1]" in summary["ledger_markdown"]


class TestCli:
    def test_init_and_show(self, tmp_path, capsys):
        workdir = tmp_path / "work"
        assert main(["init", "--dir", str(workdir)]) == 0
        assert (workdir / "pact.yaml").exists()
        assert (workdir / "ledger.yaml").exists()

        assert main(["pact-show", "--dir", str(workdir)]) == 0
        assert "goal" in capsys.readouterr().out

        assert main(["ledger-show", "--dir", str(workdir)]) == 0
        out = capsys.readouterr().out
        assert "Ledger" in out and "○ [ac1]" in out

    def test_init_refuses_overwrite(self, tmp_path, capsys):
        workdir = tmp_path / "work"
        assert main(["init", "--dir", str(workdir)]) == 0
        assert main(["init", "--dir", str(workdir)]) == 1
        assert main(["init", "--dir", str(workdir), "--force"]) == 0
        capsys.readouterr()

    def test_prompts_list(self, capsys):
        assert main(["prompts", "list"]) == 0
        out = capsys.readouterr().out
        assert "main_agent_steering.v1" in out
        assert "MISSING" not in out

    def test_scan_command(self, tmp_path, capsys):
        clean = tmp_path / "clean.txt"
        clean.write_text("hello world", encoding="utf-8")
        assert main(["scan", str(clean)]) == 0

        dirty = tmp_path / "dirty.txt"
        dirty.write_text(f"key {FAKE_OPENAI}", encoding="utf-8")
        assert main(["scan", str(dirty)]) == 1
        assert "openai" in capsys.readouterr().out
