"""The bridge program's HTTP face, backed by LiveBridge.

This is the one bridge program the wrapper talks to. JSON API + Prometheus metrics;
no UI here (the wrapper serves that). Endpoints:

  POST /api/connect                 -> {login_id}
  GET  /api/status?login_id=        -> {state, account, last_error, ...}
  GET  /api/logins                  -> [ ... ]
  GET  /api/chats?login_id=         -> {contacts, threads}
  GET  /api/messages?login_id=&thread_id=&cursor=
  POST /api/logout {login_id}
  GET  /api/health                  -> {live_session_ratio, password_login_share, ...}
  GET  /metrics                     -> Prometheus text
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from . import metrics


class _Handler(BaseHTTPRequestHandler):
    live = None

    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _text(self, code, text):
        body = text.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.send_header("Access-Control-Allow-Origin", "*")
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

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        lid = (q.get("login_id") or [""])[0]
        if u.path == "/metrics":
            return self._text(200, self.live.metrics_text())
        if u.path == "/api/health":
            return self._json(200, self._health())
        if u.path == "/api/logins":
            return self._json(200, self.live.list_logins())
        if u.path == "/api/status":
            st = self.live.status(lid)
            return self._json(200 if st else 404, st or {"error": "no such login"})
        if u.path == "/api/chats":
            return self._json(200, self.live.chats(lid))
        if u.path == "/api/messages":
            tid = (q.get("thread_id") or [""])[0]
            cur = (q.get("cursor") or ["0"])[0]
            return self._json(200, self.live.messages(lid, tid, cur))
        return self._json(404, {"error": "not found"})

    def do_POST(self):
        u = urlparse(self.path)
        if u.path == "/api/connect":
            flow = (self._body().get("flow") or "password")
            flow = flow if flow in ("password", "qr") else "password"
            return self._json(200, {"login_id": self.live.connect(flow)})
        if u.path == "/api/logout":
            self.live.logout(self._body().get("login_id", ""))
            return self._json(200, {"ok": True})
        if u.path == "/api/load_older":
            b = self._body()
            try:
                res = self.live.load_older(b.get("login_id", ""), b.get("thread_id", ""))
            except Exception as e:  # noqa: BLE001
                return self._json(200, {"added": 0, "has_more": False, "error": str(e)})
            return self._json(200, res)
        if u.path == "/api/send":
            b = self._body()
            try:
                res = self.live.send_message(b.get("login_id", ""), b.get("thread_id", ""),
                                             b.get("text", ""))
            except Exception as e:  # noqa: BLE001
                return self._json(200, {"ok": False, "error": str(e)})
            return self._json(200, res)
        return self._json(404, {"error": "not found"})

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _health(self):
        dicts = [l.metrics_dict() for l in self.live.logins.values()]
        now = int(__import__("time").time() * 1000)
        interval = self.live.poll_seconds * 1000
        return {
            "live_session_ratio": round(metrics.live_session_ratio(dicts, now, interval), 4),
            "password_login_share": round(metrics.password_login_share(dicts), 4),
            "reauth_share": round(metrics.reauth_share(dicts), 4),
            "delivery_lag_p95_seconds": round(metrics.delivery_lag_p95(self.live.lags), 3),
            "logins_total": len(dicts),
            "error_totals": dict(self.live.error_totals),
            "states": [d["state"] for d in dicts],
        }


def make_bridge_server(live, host="127.0.0.1", port=8771):
    handler = type("BridgeHandler", (_Handler,), {"live": live})
    return ThreadingHTTPServer((host, port), handler)
