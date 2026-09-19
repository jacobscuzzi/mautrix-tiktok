"""One logged-in TikTok web capture -> browser-data/<user>/.

Drives a persistent browser (bridge/auth/browser.py) to a logged-in
`/messages`, and records every DM-relevant request/response, every frontier
websocket frame, the hydration blob, the login-page DOM, an in-page signing
probe, and the browser fingerprint. Nothing is injected into the page: TikTok's
own webmssdk signs, we only tap Playwright's native network + websocket events.

  ./run-web-capture.sh --mode manual --headful --user <name>

Modes:
  manual  a human logs in and triggers a DM (the only mode implemented; the
          password ladder is exercised against the fake platform in the tests).

The session is saved encrypted into session_store and, transiently, as
storage_state.json (gitignored). Delete the plaintext after importing it.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import threading
import time
from urllib.parse import urlsplit

from ..auth import browser as browserfac

LOGIN_URL = "https://www.tiktok.com/login/phone-or-email/email"
MESSAGES_URL = "https://www.tiktok.com/messages"

TT_HOST = re.compile(r"(tiktok\.com|tiktokv\.(com|eu|us)|tiktokw\.(eu|us)|musical\.ly)$", re.I)
NOISE = re.compile(r"(mcs\d*-|/monitor_browser/|/collect/|/v1/list\b|/log/|slardar|libraweb|/report\b)", re.I)
DM_HINT = re.compile(
    r"(im-api\.|/api/im/|/im/|/passport/web/|/api/user/|/api/friend|/api/relation|"
    r"/api/dm/|/api/inbox|conversation|message|spotlight|token/beat)", re.I)
BODY_TYPES = ("xhr", "fetch", "document", "other")
MAX_BODY = 1_500_000

DOM_JS = """() => Array.from(document.querySelectorAll('[data-e2e], input, button, form, a[href*="login"]'))
  .map(e => ({tag: e.tagName, e2e: e.getAttribute('data-e2e'), type: e.type || null,
              name: e.name || null, ph: e.placeholder || null, id: e.id || null,
              text: (e.innerText || '').trim().slice(0, 60)}))"""
FP_JS = """() => ({ua: navigator.userAgent, lang: navigator.language, langs: navigator.languages,
  tz: Intl.DateTimeFormat().resolvedOptions().timeZone, w: screen.width, h: screen.height,
  iw: innerWidth, ih: innerHeight, dpr: devicePixelRatio, webdriver: navigator.webdriver,
  platform: navigator.platform, hc: navigator.hardwareConcurrency, mem: navigator.deviceMemory})"""
HYDRATION_JS = """() => ({
  universal: document.getElementById('__UNIVERSAL_DATA_FOR_REHYDRATION__')?.textContent || null,
  sigi: document.getElementById('SIGI_STATE')?.textContent || null,
  hasAcrawler: typeof window.byted_acrawler !== 'undefined',
  acrawlerKeys: window.byted_acrawler ? Object.keys(window.byted_acrawler) : [],
  localStorageKeys: Object.keys(localStorage)})"""
PROBE_JS = ("u => fetch(u, {credentials: 'include'})"
            ".then(r => r.text().then(t => ({status: r.status, body: t.slice(0, 6000)})))")


class Recorder:
    def __init__(self, cap_file):
        self._f = cap_file
        self.counts = {"resp": 0, "ws_in": 0, "ws_out": 0, "dm_hits": 0}
        self.seen_paths = set()
        self.last_dm_get = None

    def rec(self, kind, **d):
        d["kind"] = kind
        d["ts"] = time.time()
        self._f.write(json.dumps(d, ensure_ascii=False) + "\n")
        self._f.flush()

    def on_response(self, resp):
        req = resp.request
        url = resp.url
        host = urlsplit(url).netloc
        if not TT_HOST.search(host) or NOISE.search(url):
            return
        path = host + urlsplit(url).path
        if path not in self.seen_paths:
            self.seen_paths.add(path)
            self.rec("path_seen", path=path, resource_type=req.resource_type)
        if req.resource_type not in BODY_TYPES:
            return
        entry = {"url": url, "method": req.method, "status": resp.status,
                 "resource_type": req.resource_type, "request_headers": dict(req.headers),
                 "response_headers": dict(resp.headers), "dm_hint": bool(DM_HINT.search(url))}
        try:
            pd = req.post_data_buffer
            if pd:
                entry["request_body_b64"] = base64.b64encode(pd[:MAX_BODY]).decode()
        except Exception:
            pass
        try:
            body = resp.body()
            entry["body_len"] = len(body)
            ctype = resp.headers.get("content-type", "")
            if len(body) <= MAX_BODY:
                if "json" in ctype or "text" in ctype or "javascript" in ctype:
                    entry["body_text"] = body.decode("utf-8", "replace")
                else:
                    entry["body_b64"] = base64.b64encode(body).decode()
        except Exception as e:
            entry["body_error"] = str(e)[:200]
        self.counts["resp"] += 1
        if entry["dm_hint"]:
            self.counts["dm_hits"] += 1
            if req.method == "GET" and ("im-api" in host or "/api/im" in url):
                self.last_dm_get = url
        self.rec("response", **entry)

    def on_websocket(self, ws):
        self.rec("ws_open", url=ws.url)

        def frame(direction):
            def h(payload):
                data = payload if isinstance(payload, (bytes, bytearray)) else payload.encode()
                self.counts[direction] += 1
                self.rec(direction, url=ws.url, data_b64=base64.b64encode(data).decode(), n=len(data))
            return h

        ws.on("framereceived", frame("ws_in"))
        ws.on("framesent", frame("ws_out"))
        ws.on("close", lambda w: self.rec("ws_close", url=ws.url))


def _has_session(context):
    return any(c["name"] == "sessionid" and "tiktok" in c["domain"] for c in context.cookies())


def _dump_dom(page, rec, label):
    try:
        rec.rec("dom", label=label, url=page.url, elements=page.evaluate(DOM_JS))
    except Exception as e:
        rec.rec("dom_error", label=label, err=str(e)[:200])


def _wait_enter(page, prompt):
    done = threading.Event()
    threading.Thread(target=lambda: (input(prompt), done.set()), daemon=True).start()
    while not done.is_set():
        page.wait_for_timeout(500)


def _inpage_probe(page, rec):
    url = rec.last_dm_get
    if not url:
        rec.rec("inpage_probe", skipped="no DM GET captured")
        return
    stripped = re.sub(r"&?(X-Bogus|X-Gnarly|X-Dynosaur|msToken|verifyFp)=[^&]*", "", url)
    sent = []
    page.on("request", lambda r: sent.append(r.url) if ("im-api" in r.url or "/api/im" in r.url) else None)
    for label, u in (("resigned", stripped), ("as_captured", url)):
        try:
            res = page.evaluate(PROBE_JS, u)
            page.wait_for_timeout(500)
            rec.rec("inpage_probe", label=label, requested=u,
                    actually_sent=sent[-1] if sent else None, **res)
        except Exception as e:
            rec.rec("inpage_probe", label=label, requested=u, error=str(e)[:300])


def run(user, *, mode="manual", headful=False, allow_send=False, channel=None,
        login_timeout=600, watch=90, browser_root="browser-data"):
    from playwright.sync_api import sync_playwright

    out = os.path.join(browser_root, user)
    os.makedirs(out, exist_ok=True)
    cap_path = os.path.join(out, f"capture-{int(time.time())}.jsonl")
    with open(cap_path, "a", encoding="utf-8") as cap_file:
        rec = Recorder(cap_file)
        with sync_playwright() as p:
            context, channel_used = browserfac.launch_persistent(
                p, out, headless=not headful, channel=channel)
            rec.rec("launch", mode="persistent", channel=channel_used, extra=[])
            context.on("response", rec.on_response)
            page = context.pages[0] if context.pages else context.new_page()
            page.on("websocket", rec.on_websocket)
            context.on("page", lambda pg: pg.on("websocket", rec.on_websocket))
            _enable_cdp_ws_tap(context, page, rec)

            page.goto(LOGIN_URL, wait_until="domcontentloaded")
            fp = page.evaluate(FP_JS)
            rec.rec("fingerprint", **fp)
            with open(os.path.join(out, "meta.json"), "w") as f:
                json.dump(fp, f, indent=2)
            _dump_dom(page, rec, "login_initial")

            if _has_session(context):
                print("already logged in from the saved profile")
            elif mode == "manual":
                print(f"\nLog in now in the browser window. Waiting up to "
                      f"{login_timeout}s for a sessionid cookie...\n")
                t0 = time.time()
                last = page.url
                while not _has_session(context):
                    page.wait_for_timeout(1000)
                    if page.url != last:
                        last = page.url
                        _dump_dom(page, rec, "login_step")
                    if time.time() - t0 > login_timeout:
                        rec.rec("login_timeout", seconds=int(time.time() - t0))
                        print("timed out waiting for login")
                        context.close()
                        return cap_path
                rec.rec("login_success", url=page.url, seconds=round(time.time() - t0))
            else:
                raise SystemExit("only --mode manual is implemented")

            page.goto(MESSAGES_URL, wait_until="domcontentloaded")
            page.wait_for_timeout(4000)
            rec.rec("hydration", url=page.url, **page.evaluate(HYDRATION_JS))
            _dump_dom(page, rec, "messages")
            try:
                page.screenshot(path=os.path.join(out, "messages.png"))
            except Exception:
                pass

            if mode == "manual":
                print("\n" + "=" * 70)
                print("Recording. In the browser window, take your time:")
                print("  1. have the second account send you a DM NOW; wait for it")
                print("  2. click 3-5 conversations; in each scroll UP for older messages")
                print("  3. open your own profile and one contact's profile")
                if allow_send:
                    print("  4. (allow-send ON) send one DM: bridge test")
                print("  5. return to /messages and leave it ~30 s")
                print("=" * 70)
                _wait_enter(page, "\nPress Enter here when finished... ")
            else:
                page.wait_for_timeout(watch * 1000)

            try:
                page.goto(MESSAGES_URL, wait_until="domcontentloaded")
                page.wait_for_timeout(3000)
                _inpage_probe(page, rec)
            except Exception as e:
                rec.rec("inpage_probe", error=str(e)[:300])

            context.storage_state(path=os.path.join(out, "storage_state.json"))
            rec.rec("cookies", names=[c["name"] for c in context.cookies()])
            context.close()

    c = rec.counts
    print(f"\nsaved: {cap_path}")
    print(f"       {out}/storage_state.json  (PLAINTEXT SESSION - gitignored, delete after import)")
    print(f"responses {c['resp']}  dm-related {c['dm_hits']}  ws in {c['ws_in']}  ws out {c['ws_out']}")
    if c["dm_hits"] == 0:
        print("WARNING: no DM-related responses - did /messages load while logged in?")
    if c["ws_in"] == 0:
        print("WARNING: no frontier frames - socket did not open or was reused before the hook")
    return cap_path


def _enable_cdp_ws_tap(context, page, rec):
    # Second websocket tap via CDP (Chromium only), in case Playwright's event
    # misses frames on a socket opened before the page listener attached.
    try:
        session = context.new_cdp_session(page)
        session.send("Network.enable")

        def _recv(kind):
            def h(params):
                resp = params.get("response", {})
                data = resp.get("payloadData")
                if data is not None:
                    rec.rec(kind, url="cdp", data_b64=_as_b64(data),
                            n=len(data), via="cdp")
            return h

        session.on("Network.webSocketFrameReceived", _recv("ws_in"))
        session.on("Network.webSocketFrameSent", _recv("ws_out"))
    except Exception as e:
        rec.rec("cdp_tap_unavailable", err=str(e)[:120])


def _as_b64(data):
    # CDP payloadData is base64 for binary frames, utf-8 text otherwise.
    if isinstance(data, str):
        try:
            base64.b64decode(data, validate=True)
            return data
        except Exception:
            return base64.b64encode(data.encode()).decode()
    return base64.b64encode(data).decode()


def main(argv=None):
    ap = argparse.ArgumentParser(description="TikTok web DM capture")
    ap.add_argument("--user", required=True)
    ap.add_argument("--mode", choices=("manual",), default="manual")
    ap.add_argument("--headful", action="store_true")
    ap.add_argument("--allow-send", action="store_true")
    ap.add_argument("--channel", choices=("chrome", "chromium"), default=None)
    ap.add_argument("--login-timeout", type=int, default=600)
    ap.add_argument("--watch", type=int, default=90)
    a = ap.parse_args(argv)
    run(a.user, mode=a.mode, headful=a.headful, allow_send=a.allow_send,
        channel=a.channel, login_timeout=a.login_timeout, watch=a.watch)


if __name__ == "__main__":
    main()
