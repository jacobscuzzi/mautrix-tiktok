import json
import threading
import unittest
import urllib.request

from bridge.webapp import make_bridge_server
from bridge.pipeline import Pipeline
from bridge.normalize import User, Thread, Event
from wrapper.server import make_wrapper_server


class StubLive:
    """A LiveBridge stand-in: real pipeline, scripted login state, no browser."""

    def __init__(self):
        self.pipeline = Pipeline(":memory:")
        self.poll_seconds = 20
        self.error_totals = {}
        self.lags = []
        self._logins = {}
        self.connected = []

    class _L:
        def __init__(self, lid, state):
            self.login_id, self.state = lid, state
            self.account = {"handle": "jakob", "uid": "42", "nickname": "Jakob"}
        def public(self):
            return {"login_id": self.login_id, "state": self.state, "account": self.account,
                    "last_error": None, "last_sync_ms": 0}
        def metrics_dict(self):
            return {"authenticated": self.state == "connected", "last_sync_ms": 0,
                    "state": self.state, "password_login_used": True}

    @property
    def logins(self):
        return self._logins

    def connect(self):
        lid = "live%d" % (len(self._logins) + 1)
        self._logins[lid] = self._L(lid, "connected")
        self.pipeline.upsert_login(lid, state="connected", password_login_used=True)
        self.pipeline.upsert_user(lid, User("7072", "Bob", "http://a", handle="bob"))
        self.pipeline.upsert_thread(lid, Thread("0:1:42:7072", ["42", "7072"], last_ts=5))
        self.pipeline.ingest_event(Event("m1", "0:1:42:7072", "7072", "hi jakob", 5), lid)
        return lid

    def status(self, lid):
        l = self._logins.get(lid)
        return l.public() if l else None

    def list_logins(self):
        return [l.public() for l in self._logins.values()]

    def chats(self, lid):
        return {"contacts": self.pipeline.list_contacts(lid),
                "threads": self.pipeline.list_threads(lid)}

    def messages(self, lid, tid, cursor=0):
        return self.pipeline.get_messages(tid, cursor)

    def logout(self, lid):
        self.pipeline.wipe_login(lid)
        self._logins.pop(lid, None)

    def load_older(self, lid, tid, count=30):
        # stub: pretend one older message got added, no more history
        self.pipeline.ingest_event(Event("m0", tid, "7072", "older one", 1), lid)
        return {"added": 1, "has_more": False}

    def metrics_text(self):
        from bridge.metrics import render_prometheus
        import time
        return render_prometheus([l.metrics_dict() for l in self._logins.values()],
                                 int(time.time() * 1000), 20000, self.error_totals, self.lags)


class TestWebapp(unittest.TestCase):
    def setUp(self):
        self.live = StubLive()
        self.bridge = make_bridge_server(self.live, "127.0.0.1", 0)
        self.bport = self.bridge.server_address[1]
        threading.Thread(target=self.bridge.serve_forever, daemon=True).start()
        self.wrap = make_wrapper_server(f"http://127.0.0.1:{self.bport}", "127.0.0.1", 0)
        self.wport = self.wrap.server_address[1]
        threading.Thread(target=self.wrap.serve_forever, daemon=True).start()

    def tearDown(self):
        for s in (self.bridge, self.wrap):
            s.shutdown(); s.server_close()

    def _get(self, path, base=None):
        base = base or f"http://127.0.0.1:{self.wport}"
        with urllib.request.urlopen(base + path) as r:
            return r.status, r.read()

    def _post(self, path, body=None):
        data = json.dumps(body).encode() if body is not None else b""
        req = urllib.request.Request(f"http://127.0.0.1:{self.wport}{path}", data=data,
                                     method="POST", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())

    # wrapper serves the UI
    def test_wrapper_serves_index(self):
        code, body = self._get("/")
        self.assertEqual(code, 200)
        self.assertIn(b"TikTok DM Bridge", body)

    def test_wrapper_serves_appjs(self):
        code, body = self._get("/app.js")
        self.assertEqual(code, 200)
        self.assertIn(b"connect", body)

    def test_no_path_traversal(self):
        with self.assertRaises(urllib.error.HTTPError):
            self._get("/../bridge/live.py")

    # proxy + API through the wrapper
    def test_connect_then_chats_and_messages(self):
        _, res = self._post("/api/connect")
        lid = res["login_id"]
        _, st = self._get_json(f"/api/status?login_id={lid}")
        self.assertEqual(st["state"], "connected")
        _, chats = self._get_json(f"/api/chats?login_id={lid}")
        self.assertTrue(chats["threads"] and chats["contacts"])
        tid = chats["threads"][0]["thread_id"]
        _, msgs = self._get_json(f"/api/messages?login_id={lid}&thread_id={tid}")
        self.assertEqual(msgs[0]["content"], "hi jakob")

    def test_health_and_metrics_through_proxy(self):
        self._post("/api/connect")
        _, h = self._get_json("/api/health")
        self.assertIn("live_session_ratio", h)
        code, body = self._get("/metrics")
        self.assertIn(b"bridge_live_session_ratio", body)

    def test_load_older_endpoint(self):
        _, res = self._post("/api/connect")
        lid = res["login_id"]
        _, out = self._post("/api/load_older", {"login_id": lid, "thread_id": "0:1:42:7072"})
        self.assertEqual(out["added"], 1)
        self.assertIn("has_more", out)

    def test_logout_wipes(self):
        _, res = self._post("/api/connect")
        lid = res["login_id"]
        self._post("/api/logout", {"login_id": lid})
        _, chats = self._get_json(f"/api/chats?login_id={lid}")
        self.assertEqual(chats["threads"], [])

    def _get_json(self, path):
        code, body = self._get(path)
        return code, json.loads(body)


import urllib.error  # noqa: E402

if __name__ == "__main__":
    unittest.main()
