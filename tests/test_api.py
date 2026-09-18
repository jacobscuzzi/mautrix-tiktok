import json
import os
import threading
import unittest
import urllib.request
import urllib.error

from bridge.api import make_server, LoginManager
from bridge.app import BridgeRuntime
from bridge.pipeline import Pipeline
from bridge.providers.web import WebProvider
from bridge.auth.web_password_login import LoginResult
from tests.test_web_provider import FakePage, load


def _fixture_page():
    return FakePage({
        "spotlight/relation": [load("contacts.json")["response"]],
        "spotlight/inbox": [load("conv_list_synth.json")["response"]],
        "spotlight/messages": [load("messages_synth.json")["response"],
                               {"messages": [], "has_more": 0, "next_cursor": ""}],
        "im/user/profile": [load("profile_other.json")["response"]],
    })


class FakeDriver:
    def is_alive(self):
        return False

    def password_login(self, u, p):
        return LoginResult("success") if p == "right" else LoginResult("bad_credentials")


class TestAPI(unittest.TestCase):
    TOKEN = "secret-token"

    def setUp(self):
        self.pipeline = Pipeline(":memory:")
        self.runtime = BridgeRuntime(self.pipeline)
        self.manager = LoginManager(
            self.runtime,
            driver_factory=lambda creds: FakeDriver(),
            provider_factory=lambda lid, ctx: WebProvider(_fixture_page()),
            cookie_importer=None)
        self.srv = make_server("127.0.0.1", 0, self.manager, self.runtime,
                               self.pipeline, token=self.TOKEN)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def _req(self, method, path, body=None, token="secret-token"):
        url = f"http://127.0.0.1:{self.port}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def _json(self, *a, **k):
        code, body = self._req(*a, **k)
        return code, json.loads(body)

    def test_unauthorized_without_token(self):
        code, _ = self._req("POST", "/v1/login/start", {"flow": "password"}, token=None)
        self.assertEqual(code, 401)

    def test_password_login_end_to_end(self):
        _, start = self._json("POST", "/v1/login/start", {"flow": "password"})
        lid = start["login_id"]
        self.assertEqual(start["step"]["kind"], "user_input")
        _, res = self._json("POST", f"/v1/login/{lid}/step",
                            {"username": "jakob", "password": "right"})
        self.assertTrue(res["complete"])
        self.assertEqual(res["state"], "connected")
        # status
        _, st = self._json("GET", f"/v1/logins/{lid}/status")
        self.assertEqual(st["state"], "connected")
        # contacts populated from the fixture
        _, contacts = self._json("GET", f"/v1/logins/{lid}/contacts")
        self.assertGreaterEqual(len(contacts), 10)
        # threads + messages
        _, threads = self._json("GET", f"/v1/logins/{lid}/threads")
        self.assertTrue(threads)
        tid = threads[0]["thread_id"]
        _, msgs = self._json("GET", f"/v1/logins/{lid}/threads/{tid}/messages")
        self.assertEqual(len(msgs), 3)

    def test_bad_password_is_not_complete(self):
        _, start = self._json("POST", "/v1/login/start", {"flow": "password"})
        _, res = self._json("POST", f"/v1/login/{start['login_id']}/step",
                            {"username": "jakob", "password": "WRONG"})
        self.assertFalse(res["complete"])
        self.assertEqual(res["state"], "bad_credentials")

    def test_needs_user_carries_action(self):
        # a driver that always challenges
        self.manager.driver_factory = lambda creds: _Challenger()
        _, start = self._json("POST", "/v1/login/start", {"flow": "password"})
        _, res = self._json("POST", f"/v1/login/{start['login_id']}/step",
                            {"username": "u", "password": "p"})
        self.assertEqual(res["state"], "needs_user")
        self.assertEqual(res["action"]["then"], "cookies")

    def test_delete_wipes_login(self):
        _, start = self._json("POST", "/v1/login/start", {"flow": "password"})
        lid = start["login_id"]
        self._json("POST", f"/v1/login/{lid}/step", {"username": "u", "password": "right"})
        code, _ = self._req("DELETE", f"/v1/logins/{lid}")
        self.assertEqual(code, 200)
        _, st = self._json("GET", f"/v1/logins/{lid}/status")
        self.assertIn("error", st)

    def test_metrics_is_public_prometheus(self):
        code, body = self._req("GET", "/metrics", token=None)
        self.assertEqual(code, 200)
        self.assertIn(b"bridge_live_session_ratio", body)


class _Challenger:
    def is_alive(self):
        return False

    def password_login(self, u, p):
        return LoginResult("challenge", "2fa")


if __name__ == "__main__":
    unittest.main()
