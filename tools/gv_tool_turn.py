#!/usr/bin/env python
"""Does the app's chat really run the AGENT (tools, hooks) or just a model conversation?

Sends a prompt that requires a tool call, then dumps what the chat surface actually shows.
Read-only for the app: it types into the composer and clicks send, like a user.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

COMPOSER = '[data-slot="composer-rich-input"]'


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9333)
    ap.add_argument("--wait", type=int, default=120)
    ap.add_argument("--text", required=True)
    args = ap.parse_args()

    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{args.port}")
        page = None
        for ctx in browser.contexts:
            for p in ctx.pages:
                if "hermes" in (p.url or "") or "index.html" in (p.url or ""):
                    page = p
        if page is None:
            page = browser.contexts[0].pages[-1]
        page.evaluate("() => { window.location.hash = '#/' }")
        page.wait_for_timeout(2500)
        btn = page.query_selector('button[aria-label^="New session"]')
        if btn:
            btn.click()
            page.wait_for_timeout(2000)
        page.click(COMPOSER)
        page.keyboard.type(args.text, delay=5)
        page.wait_for_timeout(400)
        page.click('button[aria-label*="end"], [data-slot="composer-send"]')
        shown = False
        for _ in range(args.wait // 5):
            page.wait_for_timeout(5000)
            text = page.inner_text("body")
            if len(text) > 400 and not shown:
                shown = True
            if "date" in text and "──" not in text:
                pass
        body = page.inner_text("body")
        out = Path("/home/willy/projects/gods-voice/outputs/ui-audit") / (
            time.strftime("%Y%m%d-%H%M%S") + "-toolturn"
        )
        out.mkdir(parents=True, exist_ok=True)
        (out / "body.txt").write_text(body, encoding="utf-8")
        print(json.dumps({"hash": page.evaluate("() => location.hash"), "chars": len(body)}))
        print("--- sichtbarer Text (letzte 1800 Zeichen) ---")
        print(body[-1800:])
        print("--- Datei:", out / "body.txt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
