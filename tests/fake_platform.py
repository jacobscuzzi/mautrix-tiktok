"""A minimal fake TikTok-shaped platform, served over real HTTP.

A Python port of vaultbrowser's fake platform: a login form, a wrong-password
path, a 2FA mode, a lock mode, an inbox, and a send box. It exists so the password
ladder and cookies import can be driven by a REAL headless browser against real
HTTP, including the failure paths that are untestable against tiktok.com.

  with FakePlatform(mode="normal", password="hunter2") as fp:
      fp.url            # base url
      ...

Modes: normal | wrong_password | twofa | locked. `mode` picks what
POST /passport/login does. The DOM uses the same data-e2e / input names the real
login page uses (pinned from the Task A capture), so the same selectors drive both.
"""
from __future__ import annotations

import http.server
import threading
import uuid
from urllib.parse import parse_qs, urlparse

LOGIN_HTML = """<!doctype html><html><head><title>Log in to TikTok</title></head><body>
<h2 data-e2e="login-title">Log in</h2>
<form method="POST" action="/passport/login">
  <input name="username" type="text" placeholder="Email or username">
  <input name="password" type="password" placeholder="Password">
  <button data-e2e="login-button" type="submit">Log in</button>
</form>
{error}
</body></html>"""

INBOX_HTML = """<!doctype html><html><head><title>Messages</title></head><body>
<div data-e2e="dm-conversation-list">
  <div data-e2e="conversation" data-id="c1">Alice</div>
</div>
<div data-e2e="dm-chatbox"><textarea data-e2e="message-input"></textarea>
<button data-e2e="send-button">Send</button></div>
</body></html>"""

VERIFY_HTML = """<!doctype html><html><head><title>Verify</title></head><body>
<h2 data-e2e="verify-title">Enter the code we sent you</h2>
<input name="code" data-e2e="2fa-input"></body></html>"""

LOCKED_HTML = """<!doctype html><html><head><title>Account locked</title></head><body>
<h2 data-e2e="account-locked">Your account is locked</h2></body></html>"""


class FakePlatform:
    def __init__(self, mode="normal", username="jakob", password="hunter2"):
        self.mode = mode
        self.username = username
        self.password = password
        self.sessions = set()
        self._srv = None
        self._thread = None

    # allow tests to invalidate the session (logged-out-elsewhere)
    def logout_all(self):
        self.sessions.clear()

    def __enter__(self):
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body, ctype="text/html", cookie=None, location=None):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                if cookie:
                    self.send_header("Set-Cookie", cookie)
                if location:
                    self.send_header("Location", location)
                data = body.encode()
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _sid(self):
                c = self.headers.get("Cookie", "")
                for part in c.split(";"):
                    if part.strip().startswith("sessionid="):
                        return part.strip().split("=", 1)[1]
                return None

            def _alive(self):
                return self._sid() in outer.sessions

            def do_GET(self):
                path = urlparse(self.path).path
                if path in ("/login", "/passport/login"):
                    self._send(200, LOGIN_HTML.format(error=""))
                elif path == "/verify":
                    self._send(200, VERIFY_HTML)
                elif path == "/locked":
                    self._send(200, LOCKED_HTML)
                elif path == "/messages":
                    if self._alive():
                        self._send(200, INBOX_HTML)
                    else:
                        self._send(302, "", location="/login?redirect_url=/messages")
                elif path == "/session/check":
                    self._send(200, '{"alive": %s}' % ("true" if self._alive() else "false"),
                               ctype="application/json")
                else:
                    self._send(404, "not found")

            def do_POST(self):
                path = urlparse(self.path).path
                if path != "/passport/login":
                    self._send(404, "not found")
                    return
                ln = int(self.headers.get("Content-Length", 0))
                form = parse_qs(self.rfile.read(ln).decode())
                pw = (form.get("password") or [""])[0]
                if outer.mode == "locked":
                    self._send(302, "", location="/locked")
                    return
                if outer.mode == "wrong_password" or pw != outer.password:
                    self._send(200, LOGIN_HTML.format(
                        error='<div data-e2e="login-error">Incorrect password</div>'))
                    return
                if outer.mode == "twofa":
                    self._send(302, "", location="/verify")
                    return
                sid = uuid.uuid4().hex
                outer.sessions.add(sid)
                self._send(302, "", cookie=f"sessionid={sid}; Path=/",
                           location="/messages")

        self._srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        self._thread = threading.Thread(target=self._srv.serve_forever, daemon=True)
        self._thread.start()
        self.port = self._srv.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        return self

    def __exit__(self, *a):
        if self._srv:
            self._srv.shutdown()
            self._srv.server_close()
