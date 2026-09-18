"""End-to-end ladder tests against the fake platform, driven by a REAL browser.

Skipped when Playwright/Chromium cannot launch, so CI without a browser still
passes; where a browser exists (the laptop/server) these prove the ladder against
real HTTP + real DOM, including the failure paths tiktok.com will not reproduce
on demand.
"""
import unittest

from tests.fake_platform import FakePlatform
from bridge.auth.web_password_login import (Ladder, PlaywrightPasswordDriver,
                                            CONNECTED, NEEDS_USER, BAD_CREDENTIALS,
                                            BLOCKED)


def _browser_ok():
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            b.close()
        return True
    except Exception:
        return False


BROWSER = _browser_ok()


@unittest.skipUnless(BROWSER, "no headless browser available")
class TestLadderE2E(unittest.TestCase):
    def setUp(self):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self.browser = self._pw.chromium.launch(
            headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])

    def tearDown(self):
        self.browser.close()
        self._pw.stop()

    def _driver(self, fp, page):
        return PlaywrightPasswordDriver(
            page, f"{fp.url}/login", f"{fp.url}/messages",
            session_check=f"{fp.url}/session/check")

    def test_first_login_succeeds(self):
        with FakePlatform(password="hunter2") as fp:
            page = self.browser.new_context().new_page()
            out = Ladder(self._driver(fp, page)).connect("jakob", "hunter2")
            self.assertEqual(out.state, CONNECTED)

    def test_session_reuse_across_restart_zero_logins(self):
        with FakePlatform(password="hunter2") as fp:
            ctx = self.browser.new_context()
            page = ctx.new_page()
            d1 = self._driver(fp, page)
            self.assertEqual(Ladder(d1).connect("jakob", "hunter2").state, CONNECTED)
            state = ctx.storage_state()
            # "restart": brand-new context seeded with the saved session
            ctx2 = self.browser.new_context(storage_state=state)
            page2 = ctx2.new_page()
            d2 = self._driver(fp, page2)
            d2.password_login = self._forbidden  # any login here is a bug
            out = Ladder(d2).connect("jakob", "hunter2")
            self.assertEqual(out.state, CONNECTED)

    @staticmethod
    def _forbidden(*a, **k):
        raise AssertionError("password_login called on an alive session")

    def test_logged_out_elsewhere_recovers_with_one_login(self):
        with FakePlatform(password="hunter2") as fp:
            ctx = self.browser.new_context()
            page = ctx.new_page()
            d = self._driver(fp, page)
            Ladder(d).connect("jakob", "hunter2")
            fp.logout_all()  # session invalidated server-side
            calls = {"n": 0}
            real = d.password_login
            def counting(u, p):
                calls["n"] += 1
                return real(u, p)
            d.password_login = counting
            out = Ladder(d).connect("jakob", "hunter2")
            self.assertEqual(out.state, CONNECTED)
            self.assertEqual(calls["n"], 1)

    def test_wrong_password_bad_credentials(self):
        with FakePlatform(password="hunter2") as fp:
            page = self.browser.new_context().new_page()
            out = Ladder(self._driver(fp, page)).connect("jakob", "WRONG")
            self.assertEqual(out.state, BAD_CREDENTIALS)

    def test_twofa_stops_with_needs_user(self):
        with FakePlatform(mode="twofa", password="hunter2") as fp:
            page = self.browser.new_context().new_page()
            out = Ladder(self._driver(fp, page)).connect("jakob", "hunter2")
            self.assertEqual(out.state, NEEDS_USER)

    def test_locked_is_blocked(self):
        with FakePlatform(mode="locked", password="hunter2") as fp:
            page = self.browser.new_context().new_page()
            out = Ladder(self._driver(fp, page)).connect("jakob", "hunter2")
            self.assertEqual(out.state, BLOCKED)


if __name__ == "__main__":
    unittest.main()
