#!/usr/bin/env python3
"""Which build does the RUNNING renderer actually execute?

Enables the CDP debugger (replays scriptParsed for every already-loaded
script), then searches each script's source for our patch markers. Answers
asar-vs-unpacked definitively.
"""
import asyncio
import json
import urllib.request

import websockets


def page_ws() -> str:
    with urllib.request.urlopen("http://127.0.0.1:9333/json", timeout=10) as r:
        targets = json.loads(r.read().decode())
    for t in targets:
        if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
            return t["webSocketDebuggerUrl"]
    raise SystemExit("no page target")


async def main() -> None:
    ws_url = page_ws()
    found = {}
    scripts = {}
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
                if msg.get("method") == "Debugger.scriptParsed":
                    scripts[msg["params"]["scriptId"]] = msg["params"]["url"]

        await send("Debugger.enable")
        await asyncio.sleep(2.0)  # let scriptParsed events flush
        print("scripts parsed:", len(scripts))
        markers = ("[f15]", "watchdog tracking", "stuck on")
        for script_id, url in scripts.items():
            if not url.endswith(".js"):
                continue
            for marker in markers:
                res = await send("Debugger.searchInContent", scriptId=script_id, query=marker)
                hits = res.get("result") if isinstance(res, dict) else res
                hits = hits if isinstance(hits, list) else []
                if hits:
                    found.setdefault(marker, []).append((url.split("/")[-1], len(hits)))
        for marker in markers:
            print(marker, "->", found.get(marker, "NOT IN RUNNING RENDERER"))


asyncio.run(main())
