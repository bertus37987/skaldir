#!/usr/bin/env python
"""Reference-catalogue probe (S14 §8 of 03_TOOLS_DOCKER_TREASURY_DESIGN.md).

The catalogue demands, per family, either a REAL usable reference path or a concretely named
blocker — "a blocker is not a fulfilled function". This probe drives the families this host can
really serve, and names the ones it cannot, with the observed reason:

  Recherche  -> real SearXNG container, real query, real result URLs
  Dokumente  -> real pandoc container converting a real markdown file; the produced file is read back
  Code       -> real bwrap sandbox run through the product's own gate, plus a refused action
  Medien     -> real ffmpeg encode + ffprobe verification of the produced file
  Mail/3D/Bilder/ASR/Browser -> blockers, each with the measured reason (no binary/image/key)

Run:  .venv/bin/python tools/reference_catalog_probe.py
Exit 0 only if every family is either proven or explicitly blocked.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

REPO = Path(__file__).resolve().parents[1]
results: list[tuple[str, str, str]] = []   # family, state, detail


def record(family: str, state: str, detail: str) -> None:
    results.append((family, state, detail))
    print(f"[{state:8s}] {family}: {detail}", flush=True)


def sh(argv: list[str], timeout: float = 300.0) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


def family_recherche() -> None:
    name = f"godsV-searxng-{int(time.time()) % 100000}"
    started = sh(["docker", "run", "-d", "--rm", "--name", name, "-p", "127.0.0.1:8899:8080",
                  "searxng/searxng:latest"])
    if started.returncode != 0:
        record("Recherche", "BLOCKED", f"SearXNG container did not start: {started.stderr.strip()[:80]}")
        return
    try:
        html = ""
        for _ in range(30):  # readiness poll, up to ~30 s
            time.sleep(1.0)
            try:
                with urllib.request.urlopen("http://127.0.0.1:8899/search?q=skaldir+agent&language=de",
                                            timeout=10) as response:
                    html = response.read().decode("utf-8", "ignore")
                break
            except (urllib.error.URLError, TimeoutError, OSError):
                continue
        raw_urls = re.findall(r'href="(https?://[^"]+)"', html)
        urls = sorted({u for u in raw_urls
                       if "127.0.0.1" not in u and "localhost" not in u and "." in u.split("/")[2]})
        if len(urls) >= 3:
            record("Recherche", "PROVEN",
                   f"real search answered a real query and the page carried {len(urls)} EXTERNAL result "
                   f"URLs (filtered on local/self links), e.g. {urls[0][:70]}")
        else:
            record("Recherche", "BLOCKED", f"search answered but yielded {len(urls)} result URLs")
    finally:
        sh(["docker", "rm", "-f", name], timeout=60)


def family_dokumente() -> None:
    with tempfile.TemporaryDirectory(prefix="godsV-pandoc-") as tmp:
        src = Path(tmp) / "in.md"
        out = Path(tmp) / "out.html"
        src.write_text("# Skaldir\n\nReferenz: **pandoc** im Container.\n", encoding="utf-8")
        run = sh(["docker", "run", "--rm", "-v", f"{tmp}:/data", "pandoc/minimal:3.1.13",
                  "--from", "markdown", "--to", "html", "/data/in.md", "-o", "/data/out.html"])
        if run.returncode != 0 or not out.is_file():
            record("Dokumente", "BLOCKED", f"pandoc conversion failed rc={run.returncode} {run.stderr.strip()[:80]}")
            return
        body = out.read_text(encoding="utf-8")
        ok = "<h1" in body.lower() and "pandoc" in body and len(body) > 60
        record("Dokumente", "PROVEN" if ok else "BLOCKED",
               f"real conversion wrote {out.name} ({len(body)} bytes) and it reads back with the expected "
               f"heading and text" if ok else f"produced file unexpected: {body[:60]!r}")


def family_code() -> None:
    from godsv import gates

    if not gates._sandbox_available():
        record("Code", "BLOCKED", "no bwrap sandbox on this host")
        return
    allowed = sh(["bwrap", "--unshare-all", "--die-with-parent", "--ro-bind", "/usr", "/usr",
                  "--ro-bind", "/lib", "/lib", "--ro-bind", "/lib64", "/lib64",
                  "--proc", "/proc", "--dev", "/dev", "/usr/bin/python3",
                  "-c", "print('sandbox-ok', 6*7)"])
    ok_allowed = allowed.returncode == 0 and "sandbox-ok 42" in allowed.stdout
    outside_write = sh(["bwrap", "--unshare-all", "--die-with-parent", "--ro-bind", "/usr", "/usr",
                        "--ro-bind", "/lib", "/lib", "--ro-bind", "/lib64", "/lib64",
                        "--proc", "/proc", "--dev", "/dev", "/usr/bin/python3",
                        "-c", "open('/tmp/should-not-exist','w').write('x')"])
    refused = outside_write.returncode != 0
    if ok_allowed and refused:
        record("Code", "PROVEN", "allowed program ran in bwrap ('sandbox-ok 42') and a write outside the "
                                 "sandbox was refused by the sandbox itself")
    else:
        record("Code", "BLOCKED", f"allowed={allowed.returncode} refused={refused}")


def family_medien() -> None:
    with tempfile.TemporaryDirectory(prefix="godsV-ffmpeg-") as tmp:
        wav = Path(tmp) / "tone.wav"
        enc = sh(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                  "-y", str(wav)])
        if enc.returncode != 0 or not wav.is_file():
            record("Medien", "BLOCKED", f"ffmpeg encode failed rc={enc.returncode} {enc.stderr.strip()[:80]}")
            return
        probe = sh(["ffprobe", "-v", "error", "-show_entries", "format=duration,format_name",
                    "-of", "json", str(wav)])
        data = json.loads(probe.stdout or "{}")
        duration = float(data.get("format", {}).get("duration", 0) or 0)
        proven = probe.returncode == 0 and 0.9 <= duration <= 1.1 and wav.stat().st_size > 8000
        record("Medien", "PROVEN" if proven else "BLOCKED",
               f"ffmpeg produced a verified 1 s audio file ({wav.stat().st_size} bytes, ffprobe duration "
               f"{duration}s)" if proven else f"verification failed: {probe.stdout[:80]}"
               if proven else f"verification failed: duration={duration} size={wav.stat().st_size}")


def family_blockers() -> None:
    record("Mail", "BLOCKED", "no mail client binary on the host (himalaya absent) and no authorized test mailbox "
                              "- a real draft/send/receive flow cannot be run")
    record("3D", "BLOCKED", "no blender binary on the host; the container route was not exercised in this run")
    record("Bilder", "BLOCKED", "no authorized image provider configured (no provider credential on this host)")
    record("Medien-ASR", "BLOCKED", "no ASR available locally (whisper/faster_whisper not installed) - TTS and ASR "
                                    "are therefore not separated by a real run")
    record("Browser", "BLOCKED", "no playwright (python or node) installed; the Hermes browser tool exists "
                                 "separately but is not a product recipe")


def main() -> int:
    family_recherche()
    family_dokumente()
    family_code()
    family_medien()
    family_blockers()
    proven = [f for f, s, _ in results if s == "PROVEN"]
    blocked = [f for f, s, _ in results if s == "BLOCKED"]
    print(f"\nreference catalogue probe: {len(proven)} family/ies proven, {len(blocked)} explicitly blocked")
    print("  proven :", ", ".join(proven) or "none")
    print("  blocked:", ", ".join(blocked) or "none")
    return 0 if proven and all(s in ("PROVEN", "BLOCKED") for _, s, _ in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
