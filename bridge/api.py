"""Backend HTTP API -- "anyone can login via your app".

Stdlib http.server (no framework dependency). Bearer-auth, backend-only. Maps 1:1
to bridgev2 provisioning (`/_matrix/provision/v3/login/*`):

  POST   /v1/login/start            {flow: password|qr|cookies} -> {login_id, step}
  POST   /v1/login/{id}/step        {...}                       -> {step} | {complete}
  GET    /v1/logins/{id}/status     -> connected|needs_user|bad_credentials|...
  DELETE /v1/logins/{id}            -> remote logout + wipe session/password
  GET    /v1/logins/{id}/contacts
  GET    /v1/logins/{id}/threads
  GET    /v1/logins/{id}/threads/{tid}/messages?cursor=
  GET    /metrics                   -> Prometheus text

Login flows are delegated to a LoginManager with injected factories so the same
code drives the fake platform (CI/demo) and a real browser. The API never sees or
stores a raw password beyond the single login step. The demo app
(bridge/cmd/serve.py) uses the simpler `webapp.py` + `LiveBridge`; this v1 API is
what the demo transcript and the tests exercise.
"""
from __future__ import annotations

import hmac
import json
import os
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from . import errors
from .auth.web_password_login import Ladder, NEEDS_USER, CONNECTED


class LoginManager:
    """Drives login flows and registers the resulting provider in the runtime.

    factories:
      driver_factory(creds) -> a ladder driver (password flow)
      provider_factory(login_id, context) -> a MessageProvider (to register + connect)
    """

    def __init__(self, runtime, driver_factory=None, provider_factory=None,
                 cookie_importer=None):
        self.runtime = runtime
        self.driver_factory = driver_factory
        self.provider_factory = provider_factory
        self.cookie_importer = cookie_importer
        self.pending = {}   # login_id -> {"flow":...}

    def start(self, flow):
        if flow not in ("password", "qr", "cookies"):
            raise errors.InvalidRequest(f"unknown flow {flow}")
        login_id = uuid.uuid4().hex[:12]
        self.pending[login_id] = {"flow": flow}
        step = {
            "password": {"kind": "user_input", "fields": ["username", "password"]},
            "cookies": {"kind": "cookies", "fields": ["cookies", "user_agent"]},
            "qr": {"kind": "display_and_wait", "prompt": "scan the QR in the TikTok app"},
        }[flow]
        return {"login_id": login_id, "step": step}

    def step(self, login_id, data):
        pend = self.pending.get(login_id)
        if not pend:
            raise errors.InvalidRequest("unknown login_id")
        flow = pend["flow"]
        if flow == "password":
            return self._password(login_id, data)
        if flow == "cookies":
            return self._cookies(login_id, data)
        raise errors.NotSupported(f"flow {flow} step not implemented")

    def _password(self, login_id, data):
        driver = self.driver_factory(data)
        out = Ladder(driver).connect(data.get("username"), data.get("password"))
        if out.state == CONNECTED:
            self._register_and_connect(login_id, data, password_login_used=True)
            return {"complete": True, "user_login_id": login_id, "state": "connected"}
        return {"complete": False, "state": out.state, "reason": out.reason,
                "action": self._action_for(out)}

    def _cookies(self, login_id, data):
        if self.cookie_importer:
            self.cookie_importer(login_id, data)     # raises on bad cookies
        self._register_and_connect(login_id, data, password_login_used=False)
        return {"complete": True, "user_login_id": login_id, "state": "connected"}

    def _register_and_connect(self, login_id, data, password_login_used):
        provider = self.provider_factory(login_id, data)
        self.runtime.add_login(login_id, provider,
                               source="web", password_login_used=password_login_used)
        self.runtime.connect(login_id)
        self.pending.pop(login_id, None)

    @staticmethod
    def _action_for(out):
        # what the app should render next: degrade to the cookies flow (DESIGN.md §10).
        if out.state == NEEDS_USER:
            return {"open_url": "https://www.tiktok.com/login", "then": "cookies",
                    "reason": out.reason}
        return None


class _Handler(BaseHTTPRequestHandler):
    manager = None
    runtime = None
    pipeline = None
    token = None

    def log_message(self, *a):
        pass

    # ---- helpers -------------------------------------------------------------

    def _authed(self):
        if not self.token:
            return True
        given = (self.headers.get("Authorization") or "").encode()
        return hmac.compare_digest(given, f"Bearer {self.token}".encode())

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _text(self, code, text):
        body = text.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        ln = int(self.headers.get("Content-Length", 0))
        if not ln:
            return {}
        try:
            return json.loads(self.rfile.read(ln))
        except ValueError:
            return {}

    # ---- routing -------------------------------------------------------------

    def do_GET(self):
        p = urlparse(self.path)
        parts = [x for x in p.path.split("/") if x]
        if p.path == "/metrics":
            return self._text(200, self.runtime.render_metrics())
        if not self._authed():
            return self._json(401, {"error": "unauthorized"})
        if parts[:1] == ["v1"] and parts[1:2] == ["logins"] and len(parts) >= 3:
            login_id = parts[2]
            tail = parts[3:]
            if tail == ["status"]:
                st = self.runtime.status(login_id)
                return self._json(200 if st else 404, st or {"error": "no such login"})
            if tail == ["contacts"]:
                return self._json(200, self.pipeline.list_contacts(login_id))
            if tail == ["threads"]:
                return self._json(200, self.pipeline.list_threads(login_id))
            if len(tail) == 3 and tail[0] == "threads" and tail[2] == "messages":
                cur = (parse_qs(p.query).get("cursor") or ["0"])[0]
                return self._json(200, self.pipeline.get_messages(tail[1], cur))
        return self._json(404, {"error": "not found"})

    def do_POST(self):
        if not self._authed():
            return self._json(401, {"error": "unauthorized"})
        parts = [x for x in urlparse(self.path).path.split("/") if x]
        try:
            if parts == ["v1", "login", "start"]:
                return self._json(200, self.manager.start(self._body().get("flow", "")))
            if parts[:2] == ["v1", "login"] and parts[-1:] == ["step"]:
                return self._json(200, self.manager.step(parts[2], self._body()))
        except errors.BridgeError as e:
            return self._json(400, {"error": type(e).__name__, "detail": str(e)})
        return self._json(404, {"error": "not found"})

    def do_DELETE(self):
        if not self._authed():
            return self._json(401, {"error": "unauthorized"})
        parts = [x for x in urlparse(self.path).path.split("/") if x]
        if parts[:2] == ["v1", "logins"] and len(parts) == 3:
            self.runtime.remove_login(parts[2])
            return self._json(200, {"deleted": True, "login_id": parts[2]})
        return self._json(404, {"error": "not found"})


def make_server(host, port, manager, runtime, pipeline, token=None):
    handler = type("Handler", (_Handler,),
                   {"manager": manager, "runtime": runtime, "pipeline": pipeline,
                    "token": token or os.environ.get("BRIDGE_API_TOKEN")})
    return ThreadingHTTPServer((host, port), handler)
