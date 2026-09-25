#!/usr/bin/env python3
"""Keyless Bing web search via curl + regex parse.

Fallback fuer den Hermes-Such-Backend (Firecrawl keyless war 403).
Provenance: eigene Live-Abfrage, normal zitierbar.

Usage:
  python3 bing_search.py "query" [-n 8]          # eine Query
  python3 bing_search.py --batch queries.txt     # eine Query pro Zeile, +Ausgabe je Query
  python3 bing_search.py --file page.html        # nur parsen (Debug)
"""
import base64
import html
import re
import subprocess
import sys
import time
import urllib.parse

UA = ("Mozilla/5.0 (X11; Linux x86_64; rv:128.0) "
      "Gecko/20100101 Firefox/128.0")

def decode_bing_url(u: str) -> str:
    """Bing verpackt Treffer in bing.com/ck/a?-Redirects (u=a1<base64url>)."""
    u = html.unescape(u)
    if "bing.com/ck/a" in u:
        m = re.search(r"[?&]u=a1([A-Za-z0-9_\-=]+)", u)
        if m:
            s = m.group(1)
            s += "=" * (-len(s) % 4)
            try:
                return base64.urlsafe_b64decode(s).decode("utf-8", "replace")
            except Exception:
                return u
    return u


def fetch(q: str, n: int = 10) -> str:
    url = ("https://www.bing.com/search?q=" + urllib.parse.quote(q)
           + f"&count={max(n, 10)}")
    r = subprocess.run(["curl", "-s", "--max-time", "25", "-A", UA, url],
                       capture_output=True, text=True)
    return r.stdout or ""

def strip_tags(s: str) -> str:
    s = re.sub(r"<[^>]+>", "", s)
    return html.unescape(s).strip()

def parse(h: str):
    results = []
    for block in re.findall(r'<li class="b_algo".*?</li>', h, re.S):
        m = re.search(r'<h2[^>]*><a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
        if not m:
            continue
        url, title = decode_bing_url(m.group(1)), strip_tags(m.group(2))
        sm = re.search(r'<p[^>]*>(.*?)</p>', block, re.S)
        snippet = strip_tags(sm.group(1)) if sm else ""
        results.append((title, url, snippet))
    return results

def show(q: str, n: int = 8, sleep: float = 1.5):
    h = fetch(q, n)
    rows = parse(h)[:n]
    print(f"## {q}")
    if not rows:
        print(f"  (keine Treffer oder Blockierung; HTML len={len(h)})")
    for i, (t, u, s) in enumerate(rows, 1):
        print(f"{i}. {t}\n   {u}\n   {s[:260]}")
    print()
    sys.stdout.flush()
    time.sleep(sleep)

if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--file":
        for t, u, s in parse(open(sys.argv[2], encoding="utf-8", errors="ignore").read()):
            print(f"- {t}\n  {u}\n  {s[:200]}")
    elif len(sys.argv) >= 3 and sys.argv[1] == "--batch":
        for line in open(sys.argv[2], encoding="utf-8"):
            q = line.strip()
            if q and not q.startswith("#"):
                show(q)
    else:
        q = sys.argv[1]
        n = int(sys.argv[sys.argv.index("-n") + 1]) if "-n" in sys.argv else 8
        show(q, n, sleep=0)
