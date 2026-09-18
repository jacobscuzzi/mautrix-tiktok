"""G4: live, READ-ONLY sync with the captured session (overnight, allow-send: no).

Launches the persistent profile that logged in, checks liveness via the signed
in-page token/beat call, and — if alive — pulls contacts + conversations through
the real WebProvider into a scratch pipeline, then holds the frontier socket
briefly for any inbound frame. No sends, no mark-read, no profile edits. Gentle:
one pass + a short watch, not a night-long hold.

  .venv/bin/python scripts/g4_live_sync.py --user jakob --watch 60

Outcome (connected / needs_user / error) is printed for the DECISIONS log.
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.getcwd())

from bridge.auth import browser as browserfac
from bridge.web.page import PageClient, playwright_evaluator
from bridge.web import session as websess
from bridge.providers.web import WebProvider
from bridge.pipeline import Pipeline
from bridge.app import BridgeRuntime
from bridge import errors

MESSAGES_URL = "https://www.tiktok.com/messages"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", default="jakob")
    ap.add_argument("--watch", type=int, default=60)
    ap.add_argument("--db", default="/tmp/claude-1000/g4-live.sqlite")
    a = ap.parse_args()
    out = os.path.join("browser-data", a.user)

    from playwright.sync_api import sync_playwright
    result = {"user": a.user, "started": time.strftime("%Y-%m-%d %H:%M:%S")}
    frames = {"in": 0}
    with sync_playwright() as p:
        ctx, channel = browserfac.launch_persistent(p, out, headless=True, channel="chromium")
        result["channel"] = channel
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.on("websocket", lambda ws: ws.on("framereceived",
                lambda f: frames.__setitem__("in", frames["in"] + 1)))
        try:
            page.goto(MESSAGES_URL, wait_until="domcontentloaded", timeout=45000)
        except Exception as e:
            result["outcome"] = "browser_error"
            result["detail"] = str(e)[:200]
            print(json.dumps(result, indent=2))
            ctx.close()
            return
        page.wait_for_timeout(4000)
        result["landed_url"] = page.url
        pc = PageClient(playwright_evaluator(page))

        # liveness via the signed in-page call
        alive = websess.is_alive(lambda m, u, params, b: pc.call(m, u, params, b))
        result["alive"] = alive
        if "/login" in page.url:
            result["outcome"] = "needs_user"
            result["detail"] = "redirected to /login (session dead)"
            print(json.dumps(result, indent=2))
            ctx.close()
            return
        if not alive:
            result["outcome"] = "needs_user"
            print(json.dumps(result, indent=2))
            ctx.close()
            return

        # read-only pull through the real provider
        pipe = Pipeline(a.db)
        rt = BridgeRuntime(pipe)
        wp = WebProvider(pc, avatar_client=None)
        try:
            users, _, _ = wp.list_contacts()
            result["contacts"] = len(users)
            for u in users:
                pipe.upsert_user(a.user, u)
        except errors.BridgeError as e:
            result["contacts_error"] = f"{type(e).__name__}: {e}"
        # hold the socket briefly for any inbound DM (gentle; none expected tonight)
        time.sleep(min(a.watch, 90))
        result["frontier_frames_in"] = frames["in"]
        result["outcome"] = "connected"
        result["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
        pipe.close()
        ctx.close()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
