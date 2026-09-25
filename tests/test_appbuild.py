"""God's Voice app overlay + native build: anchor safety, hardlink safety, detection.

The overlay must never be able to corrupt the Hermes checkout: patches are
anchored (fail loudly when a line moved) and writes break hardlinks. These
tests pin both properties, plus the binary-detection of the built GV app.
"""

import importlib.util
import os
import shutil
import types
from pathlib import Path

import pytest

from skaldir import desktop

REPO = Path(__file__).resolve().parent.parent
PATCH_APP = REPO / "app-overlay" / "patch_app.py"


def _load_patch_app():
    spec = importlib.util.spec_from_file_location("godsv_patch_app", PATCH_APP)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def patch_app():
    return _load_patch_app()


@pytest.fixture
def repo_fixture(tmp_path):
    """A repo stand-in carrying the overlay's source files as dummies."""
    patch_app = _load_patch_app()
    repo = tmp_path / "repo"
    for src_rel, _dst_rel in patch_app.FILES:
        target = repo / src_rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("dummy", encoding="utf-8")
    return repo


def _make_tree(tmp_path, patch_app, *, omit: str | None = None) -> Path:
    tree = tmp_path / "tree"
    for rel, old, _new, expected in patch_app.PATCHES:
        if rel == omit:
            continue
        path = tree / "apps" / "desktop" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        path.write_text(existing + "\n".join([old] * expected) + "\n", encoding="utf-8")
    return tree


class TestPatchApplication:
    def test_all_anchors_apply_on_a_generated_tree(self, patch_app, tmp_path, repo_fixture):
        tree = _make_tree(tmp_path, patch_app)
        report = patch_app.apply_patches(tree, repo_fixture)
        assert report["errors"] == []
        for rel, old, new, expected in patch_app.PATCHES:
            text = (tree / "apps" / "desktop" / rel).read_text(encoding="utf-8")
            assert text.count(new) >= expected, f"{rel}: new missing"
            if old not in new:
                assert text.count(old) == 0, f"{rel}: old still present"

    def test_moved_anchor_fails_loudly(self, patch_app, tmp_path, repo_fixture):
        tree = _make_tree(tmp_path, patch_app, omit="src/themes/presets.ts")
        with pytest.raises(RuntimeError, match="presets.ts"):
            patch_app.apply_patches(tree, repo_fixture)

    def test_partial_anchor_count_fails(self, patch_app, tmp_path, repo_fixture):
        tree = _make_tree(tmp_path, patch_app)
        path = tree / "apps" / "desktop" / "package.json"
        text = path.read_text(encoding="utf-8").replace('"productName": "Hermes",', "", 1)
        path.write_text(text, encoding="utf-8")  # now only 1 of 2 occurrences
        with pytest.raises(RuntimeError, match="expected 2"):
            patch_app.apply_patches(tree, repo_fixture)

    def test_writes_break_hardlinks_never_touch_the_source_tree(
        self, patch_app, tmp_path, repo_fixture
    ):
        tree = _make_tree(tmp_path, patch_app)
        original = tree / "apps" / "desktop" / "src" / "components" / "chat" / "intro.tsx"
        source_copy = tmp_path / "checkout-intro.tsx"
        source_copy.write_text(original.read_text(encoding="utf-8"), encoding="utf-8")
        # simulate the rsync hardlink mirror: tree entry links to the checkout file
        target = original
        target.unlink()
        os.link(source_copy, target)
        before = source_copy.read_text(encoding="utf-8")
        patch_app.apply_patches(tree, repo_fixture)
        assert source_copy.read_text(encoding="utf-8") == before, "hardlink broke through!"
        assert "SKALDIR AGENT" in target.read_text(encoding="utf-8")

    def test_files_copied_and_removals_happen(self, patch_app, tmp_path, repo_fixture):
        tree = _make_tree(tmp_path, patch_app)
        removal = tree / "apps" / "desktop" / "public" / "nous-girl.jpg"
        removal.parent.mkdir(parents=True, exist_ok=True)
        removal.write_text("x", encoding="utf-8")
        report = patch_app.apply_patches(tree, repo_fixture)
        assert "apps/desktop/src/plugins/godsv/plugin.js" in report["copied"]
        assert (tree / "apps" / "desktop" / "src" / "plugins" / "godsv" / "plugin.js").exists()
        assert "apps/desktop/public/nous-girl.jpg" in report["removed"]
        assert not removal.exists()


class TestBinaryDetection:
    def test_packaged_binary_prefers_gods_voice_executable(self, tmp_path):
        base = tmp_path / "app" / "linux-unpacked"
        base.mkdir(parents=True)
        binary = base / "gods-voice"
        binary.write_text("#!/bin/sh\n", encoding="utf-8")
        os.chmod(binary, 0o755)
        from skaldir import appbuild

        assert appbuild.packaged_binary(tmp_path / "app") == binary

    def test_packaged_binary_none_when_absent(self, tmp_path):
        from skaldir import appbuild

        assert appbuild.packaged_binary(tmp_path / "nope") is None

    def test_hermes_app_binary_prefers_gv_build(self, tmp_path, monkeypatch):
        gv = tmp_path / "gods-voice"
        gv.write_text("x", encoding="utf-8")
        os.chmod(gv, 0o755)
        monkeypatch.setattr(desktop, "_running_app_binary", lambda skaldir_only=False: None)
        monkeypatch.setattr(desktop, "gods_voice_app_binary", lambda: str(gv))
        assert desktop.hermes_app_binary() == str(gv)

    def test_gods_voice_app_binary_finds_the_deployed_build(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
        assert desktop.gods_voice_app_binary() is None
        base = tmp_path / "hermes" / "godsv" / "app" / "linux-unpacked"
        base.mkdir(parents=True)
        binary = base / "gods-voice"
        binary.write_text("x", encoding="utf-8")
        os.chmod(binary, 0o755)
        assert desktop.gods_voice_app_binary() == str(binary)


class _FakeProc:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.fixture
def mocked_build(tmp_path, monkeypatch):
    """appbuild.build() with every external step mocked — no rsync, no npm, no build.

    Yields ``(appbuild, calls)``; ``calls`` records the argv + env of each
    subprocess.run, so the `npm run pack` environment can be asserted directly.
    """
    from skaldir import appbuild

    mirror = tmp_path / "mirror"
    desktop_dir = mirror / "apps" / "desktop"
    desktop_dir.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    monkeypatch.setattr(appbuild, "sync_source", lambda force=False: mirror)
    monkeypatch.setattr(appbuild, "_run_overlay", lambda mirror_path, repo: {"errors": []})
    monkeypatch.setattr(appbuild, "_npm", lambda: "/usr/bin/npm")

    calls: list[dict] = []

    def fake_run(cmd, **kwargs):
        cmd = [str(c) for c in cmd]
        calls.append({"cmd": cmd, "env": kwargs.get("env"), "cwd": kwargs.get("cwd")})
        if cmd[1:3] == ["run", "pack"]:
            built = desktop_dir / "release" / "linux-unpacked"
            built.mkdir(parents=True, exist_ok=True)
            binary = built / "gods-voice"
            binary.write_text("#!/bin/sh\n", encoding="utf-8")
            os.chmod(binary, 0o755)
        elif cmd[0] == "rsync":
            # deploy step: emulate the copy into the staging dir
            shutil.copytree(cmd[-2].rstrip("/"), cmd[-1].rstrip("/"), dirs_exist_ok=True)
        return _FakeProc()

    monkeypatch.setattr(appbuild, "subprocess", types.SimpleNamespace(run=fake_run))
    return appbuild, calls


def _pack_env(calls: list[dict]) -> dict:
    packs = [c for c in calls if c["cmd"][1:3] == ["run", "pack"]]
    assert packs, "no `npm run pack` call recorded"
    return packs[-1]["env"]


class TestPerfProbeBuild:
    """VITE_PERF_PROBE=1 reaches `npm run pack` only via the GV opt-in."""

    def test_opt_in_passes_vite_perf_probe_to_the_build_env(self, mocked_build, monkeypatch):
        appbuild, calls = mocked_build
        monkeypatch.setenv("GODSV_PERF_PROBE", "1")
        monkeypatch.delenv("VITE_PERF_PROBE", raising=False)

        report = appbuild.build()

        env = _pack_env(calls)
        assert env["VITE_PERF_PROBE"] == "1"
        assert report["perf_probe"] is True
        assert report["ok"] is True

    def test_default_build_has_no_probe(self, mocked_build, monkeypatch):
        appbuild, calls = mocked_build
        monkeypatch.delenv("GODSV_PERF_PROBE", raising=False)
        monkeypatch.delenv("VITE_PERF_PROBE", raising=False)

        report = appbuild.build()

        assert "VITE_PERF_PROBE" not in _pack_env(calls)
        assert report["perf_probe"] is False

    def test_ambient_vite_perf_probe_alone_never_probes(self, mocked_build, monkeypatch):
        """Ein global exportiertes VITE_PERF_PROBE darf kein Probe-Artefakt erzeugen."""
        appbuild, calls = mocked_build
        monkeypatch.delenv("GODSV_PERF_PROBE", raising=False)
        monkeypatch.setenv("VITE_PERF_PROBE", "1")

        report = appbuild.build()

        assert "VITE_PERF_PROBE" not in _pack_env(calls)
        assert report["perf_probe"] is False

    def test_only_the_exact_value_one_opts_in(self, mocked_build, monkeypatch):
        appbuild, calls = mocked_build
        monkeypatch.delenv("VITE_PERF_PROBE", raising=False)
        for value in ("", "0", "true", "yes"):
            monkeypatch.setenv("GODSV_PERF_PROBE", value)
            assert appbuild.build()["perf_probe"] is False, value
        monkeypatch.setenv("GODSV_PERF_PROBE", "1")
        assert appbuild.build()["perf_probe"] is True

    def test_explicit_kwarg_beats_the_env_var(self, mocked_build, monkeypatch):
        appbuild, calls = mocked_build
        monkeypatch.setenv("GODSV_PERF_PROBE", "1")
        report = appbuild.build(perf_probe=False)
        assert "VITE_PERF_PROBE" not in _pack_env(calls)
        assert report["perf_probe"] is False

        monkeypatch.delenv("GODSV_PERF_PROBE")
        report = appbuild.build(perf_probe=True)
        assert _pack_env(calls)["VITE_PERF_PROBE"] == "1"
        assert report["perf_probe"] is True

    def test_probe_build_keeps_the_rest_of_the_pipeline_unchanged(self, mocked_build, monkeypatch):
        appbuild, calls = mocked_build
        monkeypatch.setenv("GODSV_PERF_PROBE", "1")
        appbuild.build()
        pack = next(c for c in calls if c["cmd"][1:3] == ["run", "pack"])
        assert pack["cmd"] == [
            "/usr/bin/npm",
            "run",
            "pack",
            "--",
            "--publish",
            "never",
        ]
        assert pack["env"]["NODE_OPTIONS"] == "--max-old-space-size=16384"

    def test_build_app_cli_flag_maps_to_the_probe_opt_in(self, monkeypatch):
        from skaldir import appbuild as appbuild_mod
        from skaldir import cli

        calls: list[dict] = []

        def fake_build(**kwargs):
            calls.append(kwargs)
            return {
                "ok": True,
                "binary": "/tmp/x",
                "mirror": "/tmp/m",
                "perf_probe": kwargs["perf_probe"],
                "seconds": 0.1,
            }

        monkeypatch.setattr(appbuild_mod, "build", fake_build)
        # --perf-probe → perf_probe=True; ohne Flag bleibt es None, damit die
        # Env-Opt-in (GODSV_PERF_PROBE=1) entscheidet.
        assert cli._cmd_desktop_build_app(types.SimpleNamespace(force=False, perf_probe=True)) == 0
        assert calls[-1]["perf_probe"] is True
        assert cli._cmd_desktop_build_app(types.SimpleNamespace(force=False, perf_probe=False)) == 0
        assert calls[-1]["perf_probe"] is None
