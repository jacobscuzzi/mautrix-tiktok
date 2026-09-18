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

    import base64
    from bridge.web import frontier
    from playwright.sync_api import sync_playwright
    result = {"user": a.user, "started": time.strftime("%Y-%m-%d %H:%M:%S")}
    frames = {"in": 0}
    # save every inbound frame so a real DM is never lost, and decode it live.
    cap_path = os.path.join(out, f"capture-g4-{int(time.time())}.jsonl")
    cap = open(cap_path, "a", encoding="utf-8")
    dm_texts = []            # (conversation_id, sender, text) extracted live
    richest = {"len": 0, "tree": None, "headers": None}

    def _on_frame(ws_url, payload):
        frames["in"] += 1
        raw = payload if isinstance(payload, (bytes, bytearray)) else payload.encode()
        cap.write(json.dumps({"kind": "ws_in", "url": ws_url,
                              "data_b64": base64.b64encode(raw).decode(),
                              "n": len(raw), "ts": time.time()}) + "\n")
        cap.flush()
        try:
            dec = frontier.decode_frame(raw)
            if dec["body"] and len(dec["body"]) > richest["len"]:
                richest.update(len=len(dec["body"]), headers=dec["headers"],
                               tree=str(__import__("bridge.proto", fromlist=["proto"])
                                        .decode_tree(dec["body"]))[:2000])
            for m in frontier.messages_from_frame(raw):
                dm_texts.append((m["conversation_id"], m["sender"], m["content"]))
        except Exception:
            pass

    with sync_playwright() as p:
        ctx, channel = browserfac.launch_persistent(p, out, headless=True, channel="chromium")
        result["channel"] = channel
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.on("websocket", lambda ws: ws.on(
            "framereceived", lambda f: _on_frame(ws.url, f)))
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
        # hold the socket for any inbound DM; decode + ingest each as it arrives.
        deadline = time.time() + a.watch
        while time.time() < deadline:
            page.wait_for_timeout(1000)
            for conv, sender, text in dm_texts:
                from bridge.normalize import Event
                pipe.ingest_event(Event(message_id=f"{conv}:{sender}:{hash(text) & 0xffff}",
                                        conversation_id=conv, sender_id=sender,
                                        text=text, timestamp_ms=int(time.time() * 1000)),
                                  a.user)
            if dm_texts:
                break
        cap.close()
        result["frontier_frames_in"] = frames["in"]
        result["dm_messages_decoded"] = len(dm_texts)
        result["dm_samples"] = [{"conv": c, "sender": s, "text": t[:120]}
                                for c, s, t in dm_texts[:5]]
        result["capture_file"] = cap_path
        if not dm_texts and richest["tree"]:
            # no text extracted: dump the richest frame so the field numbers can be pinned
            result["richest_frame_headers"] = {k: v[:40] for k, v in
                                               (richest["headers"] or {}).items()}
            result["richest_frame_tree"] = richest["tree"]
        result["outcome"] = "connected"
        result["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
        pipe.close()
        ctx.close()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
