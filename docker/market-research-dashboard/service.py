#!/usr/bin/env python3
"""Skaldir market-research snapshot service — stdlib only, no network fetch.

Two faces of the SAME capability:

1. one-shot CLI mode (the mined call path of the container adapter):
       python3 /srv/service.py snapshot --symbol AMD --detail brief
   Prints the container tool's keyed output protocol, `name=value` per line.

2. dashboard mode (the warm service, declared in the adapter's service spec):
       python3 /srv/service.py serve --port 8080
   Serves the interactive localhost dashboard (HTML) plus JSON endpoints
   (/health, /api/dataset, /api/snapshot). It only ever answers from the
   static dataset on disk: no live prices, NO TRADES, no external fetches.

Unknown symbols fail with exit code 2 (CLI) / HTTP 404 (dashboard).
"""

from __future__ import annotations

import json
import sys
from datetime import date
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

DATASET_PATH = Path(__file__).with_name("dataset.json")
VALID_DETAILS = ("brief", "full")

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Skaldir market research — snapshot</title>
<style>
 body{font:14px/1.45 monospace;background:#101210;color:#d7dcd4;margin:2rem}
 h1{font-size:1.2rem;color:#9fd38a}
 form,button{font:inherit;color:inherit}
 input,select{background:#1b201b;color:#d7dcd4;border:1px solid #3a4436;padding:.3rem .5rem}
 fieldset{border:1px solid #3a4436;margin:1rem 0}
 pre{background:#161a16;border:1px solid #2c332b;padding:.7rem;white-space:pre-wrap}
 .note{color:#8b948a}
 .err{color:#e07a7a}
</style></head><body>
<h1>Skaldir market research — snapshot (reference data, no trades)</h1>
<p class="note">Static curated research dataset __VERSION__ (as of __AS_OF__); source: __SOURCE__.</p>

<fieldset id="panel_snapshot"><legend>Action: request a snapshot</legend>
 <label>symbol <input id="symbol" size="10" value="AMD"></label>
 <label>detail
  <select id="detail"><option value="brief">brief</option><option value="full">full</option></select>
 </label>
 <button id="fetch">Request snapshot</button>
 <button id="load_dataset">Load dataset inventory</button>
</fieldset>
<pre id="out">Pick a symbol and request a snapshot, or load the dataset inventory.</pre>

<script>
const out = document.getElementById("out");
const $ = (id) => document.getElementById(id);
async function render(response) {
  const body = await response.text();
  let text = body;
  try { text = JSON.stringify(JSON.parse(body), null, 1); } catch (e) { /* keep text */ }
  out.className = response.ok ? "" : "err";
  out.textContent = "HTTP " + response.status + "\\n" + text;
}
$("fetch").addEventListener("click", async () => {
  const q = new URLSearchParams({symbol: $("symbol").value.trim(), detail: $("detail").value});
  await render(await fetch("/api/snapshot?" + q));
});
$("load_dataset").addEventListener("click", async () => {
  await render(await fetch("/api/dataset"));
});
</script>
</body></html>
"""


def load_dataset() -> dict:
    return json.loads(DATASET_PATH.read_text(encoding="utf-8"))


def snapshot(dataset: dict, symbol: str, detail: str) -> dict:
    """The typed result object — one source of truth for CLI and dashboard."""
    symbol = str(symbol).strip().upper()
    if detail not in VALID_DETAILS:
        raise KeyError(f"detail must be one of {VALID_DETAILS}, got {detail!r}")
    company = dataset.get("companies", {}).get(symbol)
    if company is None:
        known = ", ".join(sorted(dataset.get("companies", {})))
        raise KeyError(f"unknown symbol {symbol!r} (dataset holds: {known})")
    full = detail == "full"
    return {
        "symbol": symbol,
        "detail": detail,
        "sector": company["sector"],
        "market_cap_band": company["market_cap_band"],
        "risk_flags": company["risk_flags"],
        "sections": ",".join(
            dataset["sections_full"] if full else dataset["sections_brief"]
        ),
        "research_note": company["research_note_full" if full else "research_note_brief"],
        "analysts_view": company["analysts_view"] if full else "",
        "headquarters": company["headquarters"] if full else "",
        "dataset_version": dataset["version"],
        "source": dataset["source"],
        "as_of": dataset["as_of"],
    }


def run_snapshot_cli(argv: list[str]) -> int:
    args: dict[str, str] = {}
    i = 0
    while i < len(argv):
        token = argv[i]
        if token in ("--symbol", "--detail"):
            if i + 1 >= len(argv):
                print(f"error: {token} needs a value", file=sys.stderr)
                return 2
            args[token.lstrip("-")] = argv[i + 1]
            i += 2
        elif token == "--help":
            print(__doc__)
            return 0
        else:
            print(f"error: unknown argument {token!r}", file=sys.stderr)
            return 2
    if "symbol" not in args:
        print("error: --symbol is required", file=sys.stderr)
        return 2
    try:
        result = snapshot(load_dataset(), args["symbol"], args.get("detail", "brief"))
    except KeyError as exc:
        print(f"error: {exc.args[0]}", file=sys.stderr)
        return 2
    # keyed output protocol: `name=value` per line, single-line values
    for key, value in result.items():
        value = str(value).replace("\n", " ")
        print(f"{key}={value}")
    return 0


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "SkaldirMarketResearch/1.0"

    def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler API
        parsed = urlparse(self.path)
        route = parsed.path
        if route == "/":
            self._send_html(PAGE
                            .replace("__VERSION__", escape(str(self._dataset["version"])))
                            .replace("__AS_OF__", escape(str(self._dataset["as_of"])))
                            .replace("__SOURCE__", escape(str(self._dataset["source"]))))
        elif route == "/health":
            self._send_json({"status": "ok", "service": "skaldir-market-research"})
        elif route == "/api/dataset":
            companies = self._dataset.get("companies", {})
            self._send_json({
                "dataset_version": self._dataset["version"],
                "as_of": self._dataset["as_of"],
                "source": self._dataset["source"],
                "available_symbols": sorted(companies),
                "disclaimer": self._dataset["disclaimer"],
            })
        elif route == "/api/snapshot":
            params = parse_qs(parsed.query)
            symbol = (params.get("symbol") or [""])[0]
            detail = (params.get("detail") or ["brief"])[0]
            self._snapshot_json(symbol, detail)
        else:
            self._send_json({"error": f"unknown route {route}"}, status=404)

    def do_POST(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler API
        parsed = urlparse(self.path)
        if parsed.path != "/api/snapshot":
            self._send_json({"error": f"unknown route {parsed.path}"}, status=404)
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            self._send_json({"error": "body must be a JSON object"}, status=400)
            return
        if not isinstance(body, dict):
            self._send_json({"error": "body must be a JSON object"}, status=400)
            return
        self._snapshot_json(str(body.get("symbol", "")), str(body.get("detail", "brief")))

    def _snapshot_json(self, symbol: str, detail: str) -> None:
        try:
            result = snapshot(self._dataset, symbol, detail)
        except KeyError as exc:
            self._send_json({"error": exc.args[0]}, status=404)
            return
        self._send_json(result)

    @property
    def _dataset(self) -> dict:
        return self.server.dataset  # type: ignore[attr-defined]

    def _send_json(self, payload: dict, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode("utf-8"))

    def _send_html(self, page: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(page.encode("utf-8"))

    def log_message(self, fmt: str, *args: object) -> None:  # one line per request
        sys.stdout.write(f"{self.address_string()} {fmt % args}\n")
        sys.stdout.flush()


def main(argv: list[str]) -> int:
    if len(argv) >= 1 and argv[0] == "snapshot":
        return run_snapshot_cli(argv[1:])
    if len(argv) >= 1 and argv[0] == "serve":
        port = 8080
        for i, token in enumerate(argv[1:]):
            if token == "--port":
                port = int(argv[i + 2])
        server = ThreadingHTTPServer(("0.0.0.0", port), DashboardHandler)
        server.dataset = load_dataset()  # type: ignore[attr-defined]
        print(f"dashboard serving on 0.0.0.0:{port}", flush=True)
        server.serve_forever()
        return 0
    if len(argv) >= 1 and argv[0] == "--selfcheck":
        dataset = load_dataset()
        result = snapshot(dataset, "AMD", "brief")
        assert result["symbol"] == "AMD" and result["research_note"]
        print(f"selfcheck ok: {len(dataset['companies'])} symbols, version {dataset['version']}")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
