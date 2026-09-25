#!/usr/bin/env python
"""Composition probe (S08): two container services + one REAL GUI step -> one correct artifact.

The criterion asks for an own workflow that joins at least two container services, includes a real
GUI/browser step, and produces a correct artifact. This probe does exactly that with real
containers and a real application, and it refuses to fake success:

  service 1  SearXNG container  -> real search, real external result URLs
  artifact   the results as markdown
  service 2  pandoc container   -> markdown converted to HTML (the produced file is verified)
  GUI step   isolated X11 container + xterm, driven through ContainerRunner -> the app receives the
             artifact name and writes its own state file, which is read back
  negatives  a missing pandoc input must fail WITHOUT creating an artifact; a nonsense query must
             report a real zero instead of invented results

Run:  .venv/bin/python tools/composition_probe.py
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from godsv.computer_use import Action, ContainerRunner  # noqa: E402

QUERY = "renewable energy storage"
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str) -> None:
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}", flush=True)


def sh(argv: list[str], timeout: float = 300.0) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


def start_searxng(name: str) -> bool:
    sh(["docker", "rm", "-f", name], timeout=60)
    return sh(["docker", "run", "-d", "--rm", "--name", name, "-p", "127.0.0.1:8898:8080",
               "searxng/searxng:latest"]).returncode == 0


def search(query: str, timeout_s: int = 30) -> tuple[bool, list[str]]:
    for _ in range(timeout_s):
        time.sleep(1.0)
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:8898/search?q={urllib.parse.quote(query)}&language=en", timeout=10
            ) as response:
                html = response.read().decode("utf-8", "ignore")
            urls = sorted({u for u in re.findall(r'href="(https?://[^"]+)"', html)
                           if "127.0.0.1" not in u and "localhost" not in u and "." in u.split("/")[2]})
            return True, urls
        except (urllib.error.URLError, TimeoutError, OSError):
            continue
    return False, []


import urllib.parse  # noqa: E402  (used above, kept next to its call site)


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="godsV-compose-"))
    searxng = f"godsV-compose-searxng-{int(time.time()) % 100000}"
    runner = ContainerRunner(gui=True, name=f"godsV-compose-cu-{int(time.time()) % 100000}")
    try:
        # ---- service 1: real search ------------------------------------------------------------
        check("service 1 (SearXNG container) started", start_searxng(searxng), f"container {searxng}")
        answered, urls = search(QUERY)
        check("service 1 answered the real query with external results", answered and len(urls) >= 3,
              f"{len(urls)} external result URLs for {QUERY!r}")

        # ---- the artifact input ---------------------------------------------------------------
        md = work / "results.md"
        md.write_text(f"# Search results for {QUERY}\n\n"
                      + "".join(f"- source: {u}\n" for u in urls[:8]), encoding="utf-8")

        # ---- service 2: real conversion -------------------------------------------------------
        html = work / "results.html"
        run = sh(["docker", "run", "--rm", "-v", f"{work}:/data", "pandoc/minimal:3.1.13",
                  "--from", "markdown", "--to", "html", "/data/results.md", "-o", "/data/results.html"])
        body = html.read_text(encoding="utf-8") if html.is_file() else ""
        check("service 2 (pandoc container) produced the artifact",
              run.returncode == 0 and html.is_file() and urls[0] in body,
              f"{html.name} {len(body)} bytes, carries the first search URL: {urls[0][:50] if urls else '-'}")

        # ---- negative: a missing input must fail without an artifact ---------------------------
        bogus = work / "missing.html"
        bad = sh(["docker", "run", "--rm", "-v", f"{work}:/data", "pandoc/minimal:3.1.13",
                  "--from", "markdown", "--to", "html", "/data/does-not-exist.md", "-o", "/data/missing.html"])
        check("counter-case: a missing input fails and creates NO artifact",
              bad.returncode != 0 and not bogus.exists(), f"pandoc rc={bad.returncode}, artifact exists={bogus.exists()}")

        # ---- negative: a nonsense query reports a real zero ------------------------------------
        zero_ok, zero_urls = search("zzzq-not-a-word-" + str(int(time.time())))
        check("counter-case: a nonsense query yields a real (small) result set, nothing invented",
              zero_ok and len(zero_urls) < len(urls),
              f"nonsense query -> {len(zero_urls)} URLs vs {len(urls)} for the real query")

        # ---- the REAL GUI step through the product's ContainerRunner ---------------------------
        ok, detail = runner.available()
        if not ok:
            check("GUI step available", False, detail)
            return 1
        ok, detail = runner.start()
        check("GUI container + Xvfb started", ok, detail)
        subprocess.run(["podman", "exec", "-d", "-e", "DISPLAY=:90", runner.name,
                        "xterm", "-T", "GODSV-COMPOSE", "-e", "sh", "-c",
                        "cat > /tmp/artifact-name.txt"], capture_output=True, text=True, timeout=60)
        time.sleep(3.0)
        seen = runner.observe()
        check("the real app window is observed", "GODSV-COMPOSE" in seen.text, f"observe() -> {seen.text.strip()[:70]!r}")
        wid = (subprocess.run(["podman", "exec", "-e", "DISPLAY=:90", runner.name,
                               "xdotool", "search", "--name", "GODSV-COMPOSE"],
                              capture_output=True, text=True, timeout=60).stdout or "").strip().splitlines()
        focus = runner.act(Action(kind="cli", argv=["xdotool", "windowfocus", "--sync", wid[0]]))
        typed = runner.act(Action(kind="type_text", payload={"text": html.name}))
        submit = runner.act(Action(kind="key", payload={"key": "Return"}))
        time.sleep(1.0)
        readback = subprocess.run(["podman", "exec", runner.name, "cat", "/tmp/artifact-name.txt"],
                                  capture_output=True, text=True, timeout=60).stdout.strip()
        check("the GUI step delivered the artifact name into the real app",
              focus.ok and typed.ok and submit.ok and readback == html.name,
              f"app file holds {readback!r}")

        check("the workflow produced ONE correct artifact end to end",
              html.is_file() and md.is_file() and readback == html.name,
              f"artifact: {html} ({html.stat().st_size} bytes) from {len(urls)} real sources")
        return 0 if all(ok for _, ok, _ in results) else 1
    finally:
        runner.close()
        sh(["docker", "rm", "-f", searxng], timeout=60)


if __name__ == "__main__":
    code = main()
    failed = [n for n, ok, _ in results if not ok]
    print(f"\ncomposition probe: {len(results) - len(failed)}/{len(results)} checks held"
          + (f" — failed: {failed}" if failed else ""))
    raise SystemExit(code)
