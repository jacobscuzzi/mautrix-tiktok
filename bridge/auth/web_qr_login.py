"""Web QR login: drive a headless browser to tiktok.com/login, capture the
scannable QR, and wait for the user to approve it in their TikTok app. On
success the browser holds a real web session; we persist its storage state.

This is server-runnable and passwordless: the auth happens on the user's phone,
so there is no captcha to solve and no credential to store. The browser also
generates TikTok's web signing (msToken / X-Bogus / X-Gnarly) itself.
"""
from __future__ import annotations

import base64
import json
import os
import time

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")

LAUNCH_ARGS = [
    "--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
    "--disable-software-rasterizer",
]


def _write(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=2)
    os.replace(tmp, path)


def run(browser_dir, *, max_wait=600, poll_every=3.0, executable_path=None):
    from playwright.sync_api import sync_playwright

    os.makedirs(browser_dir, exist_ok=True)
    qr_png = os.path.join(browser_dir, "qr.png")
    status_path = os.path.join(browser_dir, "qr_status.json")
    session_path = os.path.join(browser_dir, "session.json")
    state = {"phase": "starting", "token": None, "expire_time": None}
    _write(status_path, state)

    def onr(r):
        if "get_qrcode" in r.url:
            try:
                d = r.json().get("data", {})
            except Exception:
                return
            if d.get("qrcode"):
                with open(qr_png, "wb") as f:
                    f.write(base64.b64decode(d["qrcode"]))
                state.update(phase="waiting_scan", token=d.get("token"),
                             expire_time=d.get("expire_time"))
                _write(status_path, state)
        elif "check_qrconnect" in r.url:
            try:
                d = r.json().get("data", {})
            except Exception:
                return
            # status: 1 new, 2 scanned, 3 confirm, 4 approved/redirect
            s = d.get("status")
            if s in ("scanned", 2):
                state["phase"] = "scanned"
                _write(status_path, state)

    with sync_playwright() as p:
        launch = {"headless": True, "args": LAUNCH_ARGS}
        if executable_path:
            launch["executable_path"] = executable_path
        b = p.chromium.launch(**launch)
        ctx = b.new_context(user_agent=UA, locale="en-US",
                            viewport={"width": 1280, "height": 900})
        pg = ctx.new_page()
        pg.on("response", onr)
        pg.goto("https://www.tiktok.com/login", timeout=60000,
                wait_until="domcontentloaded")
        pg.wait_for_timeout(2500)
        try:
            pg.click("text=Use QR code", timeout=8000)
        except Exception:
            pass

        deadline = time.time() + max_wait
        logged_in = False
        while time.time() < deadline:
            cks = {c["name"]: c["value"] for c in ctx.cookies()}
            if cks.get("sessionid"):
                logged_in = True
                ctx.storage_state(path=session_path)
                state.update(phase="logged_in",
                             sessionid_present=True,
                             cookies=sorted(cks.keys()))
                _write(status_path, state)
                break
            # refresh an expired QR by re-clicking the option
            if state.get("expire_time") and time.time() > int(state["expire_time"]) - 5:
                try:
                    pg.click("text=Use QR code", timeout=4000)
                except Exception:
                    pass
            try:
                pg.wait_for_timeout(int(poll_every * 1000))
            except Exception:
                break
        b.close()

    if not logged_in:
        state["phase"] = "timeout"
        _write(status_path, state)
    return state


if __name__ == "__main__":
    import sys
    d = sys.argv[1] if len(sys.argv) > 1 else "browser-data"
    result = run(d, max_wait=int(os.environ.get("QR_MAX_WAIT", "600")))
    print("final phase:", result["phase"])
