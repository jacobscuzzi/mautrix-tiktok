import logging
import unittest

from bridge import errors
from bridge.auth import web_cookie_import as cookie_import
from bridge.auth.web_password_login import (Ladder, LoginResult, CONNECTED,
                                            NEEDS_USER, BAD_CREDENTIALS, BLOCKED)
from bridge.auth.login import LOGIN_MODES
from bridge.web.session import WebSession


class FakeDriver:
    """Scripts is_alive and a single password_login outcome; counts attempts."""

    def __init__(self, alive=False, result=None):
        self._alive = alive
        self._result = result
        self.login_attempts = 0

    def is_alive(self):
        return self._alive

    def password_login(self, username, password):
        self.login_attempts += 1
        return self._result


class TestLadder(unittest.TestCase):
    def test_alive_session_reused_no_login(self):
        d = FakeDriver(alive=True)
        out = Ladder(d).connect("u", "p")
        self.assertEqual(out.state, CONNECTED)
        self.assertEqual(d.login_attempts, 0)

    def test_one_login_on_dead_session(self):
        d = FakeDriver(alive=False, result=LoginResult("success"))
        out = Ladder(d).connect("u", "p")
        self.assertEqual(out.state, CONNECTED)
        self.assertEqual(d.login_attempts, 1)

    def test_2fa_challenge_stops_the_ladder(self):
        d = FakeDriver(alive=False, result=LoginResult("challenge", "2fa"))
        out = Ladder(d).connect("u", "p")
        self.assertEqual(out.state, NEEDS_USER)
        self.assertEqual(out.reason, "2fa")
        self.assertEqual(d.login_attempts, 1)  # no retry

    def test_wrong_password_never_retried(self):
        d = FakeDriver(alive=False, result=LoginResult("bad_credentials"))
        out = Ladder(d).connect("u", "wrong")
        self.assertEqual(out.state, BAD_CREDENTIALS)
        self.assertEqual(d.login_attempts, 1)
        # a second connect still attempts at most once (no auto-loop within a call)
        out2 = Ladder(d).connect("u", "wrong")
        self.assertEqual(d.login_attempts, 2)
        self.assertEqual(out2.state, BAD_CREDENTIALS)

    def test_locked_is_blocked(self):
        d = FakeDriver(alive=False, result=LoginResult("blocked"))
        self.assertEqual(Ladder(d).connect("u", "p").state, BLOCKED)

    def test_no_credentials_needs_user(self):
        d = FakeDriver(alive=False)
        self.assertEqual(Ladder(d).connect().state, NEEDS_USER)
        self.assertEqual(d.login_attempts, 0)

    def test_mode_registered(self):
        self.assertEqual(LOGIN_MODES["password"], "user_input")
        self.assertEqual(LOGIN_MODES["cookies"], "cookies")


class TestCookieImport(unittest.TestCase):
    PAYLOAD = {"cookies": {"sessionid": "S", "ttwid": "T"}, "user_agent": "UA/1",
               "device": {"ttwid": "T", "region": "DE"}}

    def test_builds_alive_session(self):
        s = cookie_import.import_cookies(self.PAYLOAD)
        self.assertTrue(s.is_logged_in)
        self.assertEqual(s.region, "DE")

    def test_no_cookies_refused(self):
        with self.assertRaises(errors.InvalidRequest):
            cookie_import.import_cookies({"cookies": {}})

    def test_no_sessionid_refused(self):
        with self.assertRaises(errors.AuthError):
            cookie_import.import_cookies({"cookies": {"ttwid": "T"}})

    def test_fingerprint_mismatch_refused(self):
        existing = WebSession(user_agent="UA/OLD")
        with self.assertRaises(errors.BridgeError):
            cookie_import.import_cookies(self.PAYLOAD, existing=existing)

    def test_matching_fingerprint_ok(self):
        existing = WebSession(user_agent="UA/1", proxy_id="px1")
        s = cookie_import.import_cookies(self.PAYLOAD, existing=existing)
        self.assertEqual(s.proxy_id, "px1")


class TestSecretsNotLogged(unittest.TestCase):
    def test_ladder_does_not_log_password(self):
        buf = []

        class H(logging.Handler):
            def emit(self, r):
                buf.append(self.format(r))

        logger = logging.getLogger("bridge.auth.web_password")
        old_level, handler = logger.level, H()
        logger.setLevel(logging.DEBUG)
        logger.addHandler(handler)
        try:
            d = FakeDriver(alive=False, result=LoginResult("bad_credentials"))
            Ladder(d).connect("alice", "hunter2-secret")
        finally:
            logger.removeHandler(handler)
            logger.setLevel(old_level)
        joined = "\n".join(buf)
        self.assertNotIn("hunter2-secret", joined)


if __name__ == "__main__":
    unittest.main()
