"""Das Host-Overlay (host-overlay/patch_agent.py) ist das verankerte Artefakt
für die Stop-Lücken-Reparatur — es muss Fixture-tauglich, idempotent und laut
bei fehlendem Anker sein."""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "host-overlay"))


def _fixture_tree(tmp_path: Path) -> Path:
    import patch_agent as pa

    path = tmp_path / "agent" / "turn_stop_gates.py"
    path.parent.mkdir(parents=True)
    path.write_text(
        "import x\n" + pa.OLD + "\n" + pa.OLD2 + "\nreturn\n", encoding="utf-8"
    )
    return path


def test_overlay_patches_and_is_idempotent(tmp_path):
    import patch_agent as pa

    path = _fixture_tree(tmp_path)
    assert pa.patch(tmp_path) == 0
    patched = path.read_text(encoding="utf-8")
    assert pa.MARKER in patched
    assert "if has_hook(\"pre_verify\") and attempt < max_verify_nudges():" in patched
    assert pa.MARKER2 in patched, "trace diagnostics part must be applied"
    assert "HERMES_TURN_TRACE" in patched
    assert path.with_suffix(".py.gv-backup").exists()
    # zweiter Lauf: bereits gepatcht, kein Fehler
    assert pa.patch(tmp_path) == 0


def test_overlay_refuses_on_missing_anchor(tmp_path):
    import patch_agent as pa

    path = tmp_path / "agent" / "turn_stop_gates.py"
    path.parent.mkdir(parents=True)
    original = "def _pre_verify_nudge():\n    return None\n"
    path.write_text(original, encoding="utf-8")
    assert pa.patch(tmp_path) == 1
    assert path.read_text(encoding="utf-8") == original, "must not write on bad anchor"


def test_overlay_check_mode_writes_nothing(tmp_path):
    import patch_agent as pa

    path = _fixture_tree(tmp_path)
    before = path.read_text(encoding="utf-8")
    assert pa.patch(tmp_path, check=True) == 0
    assert path.read_text(encoding="utf-8") == before
