#!/usr/bin/env python3
"""Batch arXiv API search — kompakter Digest. Nutzung:
  python3 arxiv_search.py queries.txt
Eine Query pro Zeile (# = Kommentar). Rate-Limit ~1 req/3s wird eingehalten.
"""
import subprocess
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET

NS = {"a": "http://www.w3.org/2005/Atom"}

def search(q: str, n: int = 4):
    url = ("https://export.arxiv.org/api/query?search_query="
           + urllib.parse.quote(q) + f"&max_results={n}&sortBy=relevance")
    out = subprocess.run(["curl", "-s", "--max-time", "30", url],
                         capture_output=True, text=True).stdout
    try:
        root = ET.fromstring(out)
    except Exception as e:
        return [(f"PARSE ERROR: {e}", "", "", "")]
    rows = []
    for e in root.findall("a:entry", NS):
        t = " ".join((e.find("a:title", NS).text or "").split())
        aid = e.find("a:id", NS).text.split("/abs/")[-1]
        pub = e.find("a:published", NS).text[:10]
        s = " ".join((e.find("a:summary", NS).text or "").split())[:300]
        rows.append((t, aid, pub, s))
    return rows

if __name__ == "__main__":
    for line in open(sys.argv[1], encoding="utf-8"):
        q = line.strip()
        if not q or q.startswith("#"):
            continue
        print(f"## {q}")
        rows = search(q)
        if not rows:
            print("  (keine Treffer)")
        for t, aid, pub, s in rows:
            print(f"- [{aid}] {pub} {t}\n  {s}")
        print()
        sys.stdout.flush()
        time.sleep(3.2)
