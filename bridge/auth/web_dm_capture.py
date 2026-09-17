"""Once a web session exists (from web_qr_login), reuse the same browser context
to open TikTok's web Messages and record the real DM network calls: the inbox
and thread endpoints, their signed query params, and any websocket frames.

This does two jobs at once. It confirms the captured session actually reads DMs
(the open question from DESIGN.md), and it maps the web DM API so the bridge can
either replay those calls with browser-generated signing or keep driving the
browser. Output: browser-data/dm_capture.jsonl and a screenshot.
"""
from __future__ import annotations

import json
import os
import time

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")
LAUNCH_ARGS = ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
               "--disable-software-rasterizer"]

# URL fragments that mark a DM-related call.
IM_HINTS = ("/im/", "conversation", "/message", "inbox", "get_by_conversation",
            "imapi", "stranger", "/v1/", "/v2/")


def _is_im(url):
    u = url.lower()
    return any(h in u for h in IM_HINTS) and "tiktok" in u


def run(browser_dir, *, watch_seconds=60, executable_path=None):
    from playwright.sync_api import sync_playwright

    session_path = os.path.join(browser_dir, "session.json")
    if not os.path.exists(session_path):
        raise SystemExit("no session.json yet; run web_qr_login and scan first")
    out = os.path.join(browser_dir, "dm_capture.jsonl")
    logf = open(out, "w")

    def rec(obj):
        logf.write(json.dumps({"t": time.time(), **obj}) + "\n")
        logf.flush()

    with sync_playwright() as p:
        launch = {"headless": True, "args": LAUNCH_ARGS}
        if executable_path:
            launch["executable_path"] = executable_path
        b = p.chromium.launch(**launch)
        ctx = b.new_context(storage_state=session_path, user_agent=UA,
                            locale="en-US", viewport={"width": 1280, "height": 900})
        pg = ctx.new_page()

        def on_request(r):
            if _is_im(r.url):
                rec({"kind": "req", "method": r.method, "url": r.url[:400]})

        def on_response(r):
            if _is_im(r.url):
                entry = {"kind": "resp", "status": r.status, "url": r.url[:400]}
                try:
                    j = r.json()
                    entry["json_keys"] = list(j.keys())[:15]
                    data = j.get("data") if isinstance(j, dict) else None
                    if isinstance(data, dict):
                        entry["data_keys"] = list(data.keys())[:20]
                except Exception:
                    pass
                rec(entry)

        def on_ws(ws):
            rec({"kind": "ws_open", "url": ws.url[:200]})
            ws.on("framereceived", lambda f: rec({"kind": "ws_frame", "len": len(f) if f else 0}))

        pg.on("request", on_request)
        pg.on("response", on_response)
        pg.on("websocket", on_ws)

        pg.goto("https://www.tiktok.com/messages", timeout=60000,
                wait_until="domcontentloaded")
        deadline = time.time() + watch_seconds
        while time.time() < deadline:
            pg.wait_for_timeout(2000)
        pg.screenshot(path=os.path.join(browser_dir, "messages.png"))
        # who are we logged in as?
        cks = {c["name"]: c["value"] for c in ctx.cookies()}
        rec({"kind": "session", "cookies": sorted(cks.keys()),
             "sessionid_present": "sessionid" in cks})
        b.close()
    logf.close()
    return out


if __name__ == "__main__":
    import sys
    d = sys.argv[1] if len(sys.argv) > 1 else "browser-data"
    print("captured ->", run(d, watch_seconds=int(os.environ.get("WATCH", "60"))))
