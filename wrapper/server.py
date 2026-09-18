"""The tester/wrapper: serves the demo UI and proxies API calls to the bridge.

Kept separate from the bridge program on purpose -- it is a client of the bridge's
HTTP API, exactly like a real app would be. It serves the static UI at `/` and
forwards `/api/*` and `/metrics` to the bridge (same origin from the browser, so no
CORS surprises).
"""
from __future__ import annotations

import os
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
CTYPES = {".html": "text/html", ".css": "text/css", ".js": "application/javascript",
          ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon"}


class _Handler(BaseHTTPRequestHandler):
    bridge_base = None

    def log_message(self, *a):
        pass

    def _proxy(self):
        url = self.bridge_base + self.path
        length = int(self.headers.get("Content-Length", 0))
        data = self.rfile.read(length) if length else None
        req = urllib.request.Request(url, data=data, method=self.command)
        ct = self.headers.get("Content-Type")
        if ct:
            req.add_header("Content-Type", ct)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                body, code = r.read(), r.status
                ctype = r.headers.get("Content-Type", "application/json")
        except urllib.error.HTTPError as e:
            body, code, ctype = e.read(), e.code, "application/json"
        except Exception as e:
            body = f'{{"error":"bridge unreachable: {e}"}}'.encode()
            code, ctype = 502, "application/json"
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _static(self, path):
        if path in ("", "/"):
            path = "/index.html"
        full = os.path.normpath(os.path.join(STATIC, path.lstrip("/")))
        if not full.startswith(STATIC) or not os.path.isfile(full):
            self.send_error(404)
            return
        with open(full, "rb") as f:
            body = f.read()
        ext = os.path.splitext(full)[1]
        self.send_response(200)
        self.send_header("Content-Type", CTYPES.get(ext, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/api/") or self.path == "/metrics":
            return self._proxy()
        self._static(self.path)

    def do_POST(self):
        if self.path.startswith("/api/"):
            return self._proxy()
        self.send_error(404)


def make_wrapper_server(bridge_base, host="127.0.0.1", port=8770):
    handler = type("WrapperHandler", (_Handler,), {"bridge_base": bridge_base.rstrip("/")})
    return ThreadingHTTPServer((host, port), handler)
