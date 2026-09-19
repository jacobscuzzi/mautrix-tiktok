"""LiveBridge login-phase behaviour with a fake browser (no Playwright)."""
import os
import queue
import re
import tempfile
import threading
import time
import unittest

from bridge import live


class FakePage:
    def __init__(self, text="", url="https://www.tiktok.com/login/phone-or-email/email"):
        self.text = text
        self.url = url
        self.visited = []
        self.closed = False

    def is_closed(self):
        return self.closed

    def goto(self, url, **kw):
        self.visited.append(url)
        self.url = url

    def evaluate(self, js):
        # the only evaluate used during the login phase is the throttle regex
        m = re.search(r"/(.+?)/i", js)
        return bool(re.search(m.group(1), self.text, re.I)) if m else None

    def wait_for_timeout(self, ms):
        pass


class FakeCtx:
    def __init__(self, cookies=None):
        self._cookies = list(cookies or [])
        self.closed = False
        self.added = []

    def cookies(self):
        return list(self._cookies)

    def add_cookies(self, cookies):
        self.added.extend(cookies)
        self._cookies.extend(cookies)

    def close(self):
        self.closed = True


SESSION = [{"name": "sessionid", "value": "abc", "domain": ".tiktok.com", "path": "/"}]


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.bridge = live.LiveBridge(data_dir=self.tmp, master_key=b"k" * 32,
                                      login_timeout=300)
        self.established = []
        self.bridge._establish = lambda login, cookies: self.established.append(
            (login.login_id, cookies.get("sessionid")))
        # every launch is recorded (headless flag) and gets a fresh fake browser
        self.opened = []
        self.next_cookies = []
        self.next_url = "https://www.tiktok.com/login/phone-or-email/email"

        def fake_open(login, headless):
            self.opened.append(headless)
            login.ctx = FakeCtx(self.next_cookies)
            login.page = FakePage(url=self.next_url)
            login.headless = headless
        self.bridge._open_context = fake_open

    def _login(self, flow="password", state=live.WAITING_LOGIN, text="", cookies=None,
               headless=False):
        login = live.LiveLogin("session", os.path.join(self.tmp, "session"), flow=flow)
        login.state = state
        login.ctx = FakeCtx(cookies)
        login.page = FakePage(text=text)
        login.headless = headless
        self.bridge.logins["session"] = login
        self.bridge.pipeline.upsert_login("session", source="web", state=state,
                                          password_login_used=True)
        return login


class TestLoginStaysWatched(_Base):
    """The login window stays open after a throttle message or a timeout, so the
    user can still finish logging in there -- and the bridge must notice."""

    def test_login_after_throttle_message_is_still_detected(self):
        login = self._login(text="Maximum number of attempts reached. Try again later.")
        self.bridge._tick()
        self.assertEqual(login.state, live.NEEDS_USER)
        self.assertIn("rate-limiting", login.last_error)
        # the user waits, retries in the same window and gets in:
        login.ctx._cookies = list(SESSION)
        login.page.text = "For You"
        self.bridge._tick()
        self.assertEqual(self.established, [("session", "abc")])
        self.assertEqual(login.state, live.CONNECTING)

    def test_login_after_timeout_is_still_detected(self):
        login = self._login()
        login._t_login = time.time() - 301
        self.bridge._tick()
        self.assertEqual(login.state, live.NEEDS_USER)
        self.assertIn("timed out", login.last_error)
        login.ctx._cookies = list(SESSION)
        self.bridge._tick()
        self.assertEqual(self.established, [("session", "abc")])

    def test_needs_user_without_a_window_is_left_alone(self):
        login = self._login(state=live.NEEDS_USER)
        login.ctx = None
        self.bridge._tick()
        self.assertEqual(self.established, [])
        self.assertEqual(login.state, live.NEEDS_USER)

    def test_throttle_message_is_not_re_reported_every_tick(self):
        login = self._login(text="too many attempts")
        self.bridge._tick()
        self.bridge._tick()
        self.bridge._tick()
        self.assertEqual(self.bridge.error_totals.get(live.NEEDS_USER), 1)


class TestWindowClosedByUser(_Base):
    """macOS users close windows with the red button. A closed login window must
    become a clear 'needs you' message, not a raw TargetClosedError, and the
    windowless browser that keeps the profile locked must be closed."""

    def test_closed_login_window_is_reported_and_browser_closed(self):
        login = self._login()
        login.page.closed = True
        ctx = login.ctx
        self.bridge._tick()
        self.assertEqual(login.state, live.NEEDS_USER)
        self.assertIn("window was closed", login.last_error)
        self.assertTrue(ctx.closed)
        self.assertIsNone(login.ctx)
        self.bridge._tick()                      # nothing left to watch, no re-fail
        self.assertEqual(self.bridge.error_totals.get(live.NEEDS_USER), 1)
        self.assertEqual(self.established, [])


class TestWindowGoesAwayAfterLogin(_Base):
    """A password login happens in a visible window; once the session cookie is
    there, that window is closed and the SAME profile reopens headless, so the
    user only ever looks at the wrapper UI."""

    def test_password_login_swaps_the_window_for_a_headless_browser(self):
        login = self._login(cookies=SESSION)
        old = login.ctx
        self.bridge._tick()
        self.assertTrue(old.closed)
        self.assertEqual(self.opened, [True])
        self.assertTrue(login.headless)
        self.assertEqual(login.ctx.added, SESSION)      # session carried over explicitly
        self.assertEqual(self.established, [("session", "abc")])

    def test_qr_login_is_already_headless_and_is_not_relaunched(self):
        login = self._login(flow="qr", cookies=SESSION, headless=True)
        self.bridge._tick()
        self.assertEqual(self.opened, [])
        self.assertFalse(login.ctx.closed)
        self.assertEqual(self.established, [("session", "abc")])


class TestDoOpen(_Base):
    def test_existing_session_connects_headless_without_a_window(self):
        self._login(state=live.OPENING)
        self.next_cookies = SESSION
        self.next_url = live.MESSAGES_URL
        self.bridge._do_open("session")
        self.assertEqual(self.opened, [True])
        self.assertEqual(self.established, [("session", "abc")])

    def test_password_login_without_session_opens_one_window(self):
        login = self._login(state=live.OPENING)
        self.bridge._do_open("session")
        self.assertEqual(self.opened, [True, False])      # probe headless, then the window
        self.assertEqual(login.state, live.WAITING_LOGIN)
        self.assertEqual(login.page.visited[-1], live.LOGIN_URL)
        self.assertFalse(login.headless)

    def test_qr_login_without_session_stays_headless(self):
        login = self._login(flow="qr", state=live.OPENING)
        self.bridge._do_open("session")
        self.assertEqual(self.opened, [True])
        self.assertEqual(login.state, live.WAITING_LOGIN)
        self.assertEqual(login.page.visited[-1], live.QR_URL)

    def test_headless_bridge_never_opens_a_window(self):
        self.bridge.headless = True
        login = self._login(state=live.OPENING)
        self.bridge._do_open("session")
        self.assertEqual(self.opened, [True])
        self.assertEqual(login.state, live.WAITING_LOGIN)
        self.assertEqual(login.page.visited[-1], live.LOGIN_URL)


class TestProfileLock(unittest.TestCase):
    """Chromium must be fully gone before the same profile is reopened: launching
    while `SingletonLock` exists hands the launch to the running instance (an empty
    extra window there, no page for us -- the macOS 'NoneType goto' crash)."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.root, "profile"))
        self.lock = os.path.join(self.root, "profile", "SingletonLock")

    def test_free_profile_returns_at_once(self):
        t = time.time()
        self.assertTrue(live.wait_profile_unlocked(self.root, timeout=2))
        self.assertLess(time.time() - t, 0.5)

    def test_waits_for_the_lock_to_disappear(self):
        os.symlink("host-1234", self.lock)
        threading.Timer(0.3, os.unlink, [self.lock]).start()
        self.assertTrue(live.wait_profile_unlocked(self.root, timeout=5))
        self.assertFalse(os.path.lexists(self.lock))

    def test_gives_up_after_timeout(self):
        os.symlink("host-1234", self.lock)
        self.assertFalse(live.wait_profile_unlocked(self.root, timeout=0.3))


class TestReconnectClosesStaleBrowser(_Base):
    def test_connect_after_failure_closes_the_old_window_first(self):
        old = self._login(state=live.NEEDS_USER)
        self.bridge.connect("qr")
        cmds = []
        while True:
            try:
                cmds.append(self.bridge._q.get_nowait())
            except queue.Empty:
                break
        self.assertEqual([c for c, _ in cmds], ["close", "open"])
        for cmd, arg in cmds:
            if cmd == "close":
                self.bridge._do_close(arg)
        self.assertTrue(old.ctx is None)
        self.assertIsNot(self.bridge.logins["session"], old)
        self.assertEqual(self.bridge.logins["session"].flow, "qr")


if __name__ == "__main__":
    unittest.main()
