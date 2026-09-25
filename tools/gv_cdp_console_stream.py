#!/usr/bin/env python3
"""Catch [f15] console.info lines live via CDP events (no hook, no guessing).

Phase A: enable Runtime events, reload the renderer, collect console messages
for ~60s (the overlay publish should log 'armed' within seconds).
Phase B (separate run): after the deadline, collect again + read the DOM.
"""
import asyncio
import json
import sys
import urllib.request

import websockets


def page_ws() -> str:
    with urllib.request.urlopen("http://127.0.0.1:9333/json", timeout=10) as r:
        targets = json.loads(r.read().decode())
    for t in targets:
        if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
            return t["webSocketDebuggerUrl"]
    raise SystemExit("no page target")


async def main(duration: float, reload_first: bool) -> None:
    ws_url = page_ws()
    hits: list[str] = []
    async with websockets.connect(ws_url, max_size=64 * 1024 * 1024) as ws:
        rid = 0

        async def send(method, **params):
            nonlocal rid
            rid += 1
            await ws.send(json.dumps({"id": rid, "method": method, "params": params}))
            while True:
                msg = json.loads(await ws.recv())
                if msg.get("id") == rid:
                    return msg.get("result", msg)
                handle_event(msg)

        def handle_event(msg):
            if msg.get("method") != "Runtime.consoleAPICalled":
                return
            params = msg.get("params") or {}
            text = " ".join(
                str(a.get("value", a.get("description", ""))) for a in params.get("args", [])
            )
            if "f15" in text or "deck" in text or "wake" in text.lower():
                hits.append(f"{params.get('type')}: {text[:200]}")

        await send("Runtime.enable")
        if reload_first:
            await send("Runtime.evaluate", expression="location.reload()", returnByValue=True)
        deadline = asyncio.get_event_loop().time() + duration
        while asyncio.get_event_loop().time() < deadline:
            try:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=2.0))
                handle_event(msg)
            except TimeoutError:
                continue
    print("HITS:", len(hits))
    for h in hits:
        print(" ", h)


if __name__ == "__main__":
    dur = float(sys.argv[1]) if len(sys.argv) > 1 else 60.0
    rl = (sys.argv[2] if len(sys.argv) > 2 else "reload") == "reload"
    asyncio.run(main(dur, rl))
