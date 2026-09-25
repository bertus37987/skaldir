#!/usr/bin/env python
"""Seed the running Skaldir app with REAL runtime state and capture what the user sees.

Purpose (UI refresh goal §2/§5/§18): the audit instance starts with an empty runtime, so
half of the E2E expectations legitimately have no data. This drives the app like a user —
type a  goal into the composer, send it via a real click — and then records the observable
result: the transcript text, the live block, the chat feed rows, the completion payload the
panel reads, plus screenshots for the evidence directory.

Usage:  .venv/bin/python tools/gv_seed_turn.py --port 9333 --goal "<goal text>" [--wait 150]
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

STATE_SCRIPT = """
() => {
  const composer = document.querySelector('[data-slot="composer-rich-input"]')
    || document.querySelector('[contenteditable="true"]')
  const live = document.querySelector('[data-godsv-live]')
  const feed = document.querySelector('[data-godsv-feed]')
  const strip = document.querySelector('[data-godsv-strip]')
  const send = Array.from(document.querySelectorAll('button'))
    .find((b) => /send/i.test(String(b.getAttribute('aria-label') || b.textContent || '')))
  return {
    hash: location.hash,
    title: document.title,
    composer: Boolean(composer),
    composerEditable: composer ? composer.getAttribute('contenteditable') : null,
    live: live ? { stage: live.getAttribute('data-godsv-live'), text: String(live.textContent).slice(0, 400) } : null,
    feedRows: feed ? feed.querySelectorAll('button').length : 0,
    strip: strip ? String(strip.textContent).slice(0, 200) : null,
    sendButton: Boolean(send),
    messageCount: document.querySelectorAll('[data-message-role], [data-testid*="message"]').length
  }
}
"""

TRANSCRIPT_SCRIPT = """
() => {
  const nodes = Array.from(document.querySelectorAll('[data-message-role], [data-testid*="message"], .prose'))
  const texts = nodes.map((n) => String(n.textContent || '').trim()).filter(Boolean)
  return texts.slice(-6)
}
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9333)
    ap.add_argument("--goal", required=True)
    ap.add_argument("--wait", type=int, default=150, help="seconds to wait for the turn")
    ap.add_argument("--out", default="outputs/ui-audit")
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    stamp = time.strftime("%Y%m%d-%H%M%S")
    outdir = REPO / args.out / f"{stamp}-seed"
    outdir.mkdir(parents=True, exist_ok=True)
    result: dict = {"goal": args.goal, "stamp": stamp}

    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{args.port}")
        context = browser.contexts[0]
        page = context.pages[0]
        page.wait_for_timeout(1500)

        # the app opens on the panel route; the chat lives at '#/' (and a fresh
        # session is the only sane place to seed a goal — never an existing one)
        page.evaluate("() => { window.location.hash = '#/' }")
        page.wait_for_timeout(2500)
        page.evaluate(
            """() => {
              const btn = Array.from(document.querySelectorAll('button'))
                .find((b) => String(b.getAttribute('aria-label') || '').startsWith('New session'))
              if (btn) btn.click()
            }"""
        )
        page.wait_for_timeout(2500)
        before = page.evaluate(STATE_SCRIPT)
        result["before"] = before
        print("before:", json.dumps(before, ensure_ascii=False)[:400])
        if not before["composer"]:
            print("no composer found — cannot seed")
            return 2

        # type the goal the way a user does and send it with a real click
        composer = page.query_selector('[data-slot="composer-rich-input"]') or page.query_selector('[contenteditable="true"]')
        composer.click()
        page.keyboard.type(args.goal, delay=25)
        page.wait_for_timeout(600)
        clicked = page.evaluate(
            """() => {
              const send = Array.from(document.querySelectorAll('button'))
                .find((b) => /send/i.test(String(b.getAttribute('aria-label') || b.textContent || '')));
              if (!send) return false;
              const r = send.getBoundingClientRect();
              return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) };
            }"""
        )
        if isinstance(clicked, dict):
            page.mouse.click(clicked["x"], clicked["y"])
        else:
            page.keyboard.press("Enter")
        result["sent"] = True
        page.screenshot(path=str(outdir / "shot-after-send.png"))
        print("sent the goal turn, waiting for the runtime to work …")

        deadline = time.time() + args.wait
        last_text = ""
        stable = 0
        while time.time() < deadline:
            page.wait_for_timeout(4000)
            texts = page.evaluate(TRANSCRIPT_SCRIPT)
            joined = " | ".join(texts)[-1200:]
            if joined == last_text:
                stable += 1
                if stable >= 3 and joined:
                    break
            else:
                stable = 0
                last_text = joined
                print(f"  … stream advanced ({len(joined)} chars)")

        after = page.evaluate(STATE_SCRIPT)
        result["after"] = after
        result["transcript"] = page.evaluate(TRANSCRIPT_SCRIPT)
        try:
            completion = page.evaluate(
                """async () => {
                  const r = await fetch('/api/plugins/godsv/completion', { headers: { accept: 'application/json' } });
                  return { status: r.status, body: (await r.text()).slice(0, 1500) };
                }"""
            )
            result["completion_payload"] = completion
        except Exception as err:  # noqa: BLE001
            result["completion_payload"] = {"error": str(err)[:200]}
        try:
            feed = page.evaluate(
                """async () => {
                  const r = await fetch('/api/plugins/godsv/events?limit=20', { headers: { accept: 'application/json' } });
                  return { status: r.status, body: (await r.text()).slice(0, 1200) };
                }"""
            )
            result["events_payload"] = feed
        except Exception as err:  # noqa: BLE001
            result["events_payload"] = {"error": str(err)[:200]}
        page.screenshot(path=str(outdir / "shot-after-turn.png"))

    (outdir / "seed.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\nafter:", json.dumps(result["after"], ensure_ascii=False)[:500])
    print("transcript tail:", " | ".join(result["transcript"])[-400:])
    print("completion:", str(result.get("completion_payload"))[:200])
    print("events:", str(result.get("events_payload"))[:200])
    print(f"evidence: {outdir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
