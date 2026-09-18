import unittest

from bridge import errors
from bridge.web import session as sess
from bridge.web.page import PageClient


class TestWebSession(unittest.TestCase):
    STATE = {"cookies": [{"name": "sessionid", "value": "SECRET", "domain": ".tiktok.com"},
                         {"name": "ttwid", "value": "TW", "domain": ".tiktok.com"}],
             "origins": [{"origin": "https://www.tiktok.com", "localStorage": []}]}
    META = {"ua": "UA/1", "lang": "en-US", "tz": "Europe/Paris", "iw": 1280, "ih": 720}

    def test_from_storage_state_and_blob_roundtrip(self):
        s = sess.WebSession.from_storage_state(self.STATE, self.META, region="DE", uid="42")
        self.assertTrue(s.is_logged_in)
        self.assertEqual(s.ttwid, "TW")
        self.assertEqual(s.region, "DE")
        blob = s.to_blob()
        s2 = sess.WebSession.from_blob(blob)
        self.assertEqual(s2.sessionid, "SECRET")
        self.assertEqual(s2.storage_state()["cookies"], self.STATE["cookies"])

    def test_cookie_import_builds_session(self):
        s = sess.WebSession.from_cookie_import({
            "cookies": {"sessionid": "X", "ttwid": "T"},
            "user_agent": "UA/9", "device": {"ttwid": "T", "region": "US"}})
        self.assertTrue(s.is_logged_in)
        self.assertEqual(s.user_agent, "UA/9")
        self.assertEqual(s.region, "US")

    def test_fingerprint_mismatch_guard(self):
        s = sess.WebSession(user_agent="UA/1")
        self.assertTrue(s.fingerprint_matches("UA/1"))
        self.assertFalse(s.fingerprint_matches("UA/2"))


class TestIsAlive(unittest.TestCase):
    def test_alive_when_token_beat_ok(self):
        def caller(m, u, p, b):
            return {"data": {"error_code": 0, "user_id_str": "42"}, "message": "success"}
        self.assertTrue(sess.is_alive(caller))

    def test_dead_on_login_expired_code_8(self):
        def caller(m, u, p, b):
            return {"data": {"error_code": 8}, "message": "error"}
        self.assertFalse(sess.is_alive(caller))

    def test_dead_on_auth_error(self):
        def caller(m, u, p, b):
            raise errors.AuthError("401")
        self.assertFalse(sess.is_alive(caller))


class TestPageClientErrorMapping(unittest.TestCase):
    def _pc(self, status, text):
        return PageClient(lambda m, u, p, b: (status, text))

    def test_json_ok(self):
        self.assertEqual(self._pc(200, '{"a":1}').call("GET", "https://x/"), {"a": 1})

    def test_401_auth(self):
        with self.assertRaises(errors.AuthError):
            self._pc(401, "").call("GET", "https://x/")

    def test_403_banned(self):
        with self.assertRaises(errors.Banned):
            self._pc(403, "").call("GET", "https://x/")

    def test_429_rate(self):
        with self.assertRaises(errors.RateLimited):
            self._pc(429, "").call("GET", "https://x/")

    def test_500_transient(self):
        with self.assertRaises(errors.Transient):
            self._pc(503, "").call("GET", "https://x/")

    def test_body_code_8_is_auth(self):
        with self.assertRaises(errors.AuthError):
            self._pc(200, '{"status_code":8}').call("GET", "https://x/")

    def test_non_json_is_schema_change(self):
        with self.assertRaises(errors.SchemaChange):
            self._pc(200, "<html>login</html>").call("GET", "https://x/")

    def test_params_appended(self):
        seen = {}
        def ev(m, u, p, b):
            seen["url"] = u
            return 200, "{}"
        PageClient(ev).call("GET", "https://x/api", {"a": "1", "b": "2"})
        self.assertIn("a=1", seen["url"])


if __name__ == "__main__":
    unittest.main()
