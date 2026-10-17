#!/usr/bin/env python3
"""Small dependency-free SearXNG JSON search client."""
import argparse, json, os, sys
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

def main():
    p = argparse.ArgumentParser(description="Search through SearXNG")
    p.add_argument("query", nargs="+", help="search terms")
    p.add_argument("--url", default=os.getenv("SEARXNG_URL", "http://searxng:8080"), help="SearXNG base URL")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--language", default="en-US")
    p.add_argument("--time", choices=["day", "week", "month", "year"], help="time range")
    args = p.parse_args()
    params = {"q": " ".join(args.query), "format": "json", "language": args.language}
    if args.time: params["time_range"] = args.time
    url = args.url.rstrip("/") + "/search?" + urlencode(params)
    try:
        req = Request(url, headers={"Accept": "application/json", "User-Agent": "skaldir-searxng-search/1.0"})
        with urlopen(req, timeout=20) as response:
            data = json.load(response)
    except (HTTPError, URLError, TimeoutError, ValueError) as exc:
        print(f"search error: {exc}", file=sys.stderr)
        return 2
    results = data.get("results", [])[:max(0, args.limit)]
    print(json.dumps({"query": " ".join(args.query), "results": [
        {k: item.get(k, "") for k in ("title", "url", "content", "engine", "publishedDate")}
        for item in results
    ]}, ensure_ascii=False, indent=2))
    return 0

if __name__ == "__main__": raise SystemExit(main())
