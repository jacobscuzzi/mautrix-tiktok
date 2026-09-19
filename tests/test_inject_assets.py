"""Each injected JS asset must install cleanly and not touch navigator properties.

The iPhone-path contract (DESIGN.md §10): the WKWebView host assets install at
document-start without
changing fingerprinted `navigator` fields (webmssdk cross-checks them). Skipped
when no browser is available.
"""
import os
import unittest


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
INJECT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "bridge", "web", "inject")


def _asset(name):
    with open(os.path.join(INJECT, name)) as f:
        return f.read()


@unittest.skipUnless(BROWSER, "no headless browser available")
class TestInjectAssets(unittest.TestCase):
    def setUp(self):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self.browser = self._pw.chromium.launch(
            headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])

    def tearDown(self):
        self.browser.close()
        self._pw.stop()

    def _navigator_snapshot(self, page):
        return page.evaluate(
            "() => ({webdriver: navigator.webdriver, ua: navigator.userAgent, "
            "platform: navigator.platform, langs: navigator.languages.join(',')})")

    def test_each_asset_installs_without_touching_navigator(self):
        page = self.browser.new_context().new_page()
        page.set_content("<html><body></body></html>")
        before = self._navigator_snapshot(page)
        page.evaluate(_asset("request.js"))
        page.evaluate(_asset("ws_hook.js"))
        page.evaluate(_asset("fetch_tap.js"))
        after = self._navigator_snapshot(page)
        self.assertEqual(before, after)
        # entry point present, WebSocket + fetch wrapped
        self.assertTrue(page.evaluate("() => typeof window.__bridgePostToHost === 'function'"))
        self.assertTrue(page.evaluate("() => window.__bridgeWsHooked === true"))
        self.assertTrue(page.evaluate("() => window.__bridgeFetchTapped === true"))

    def test_post_to_host_queues_when_no_host(self):
        page = self.browser.new_context().new_page()
        page.set_content("<html></html>")
        page.evaluate(_asset("request.js"))
        page.evaluate("() => window.__bridgePostToHost('test', {a: 1})")
        q = page.evaluate("() => window.__bridgeQueue")
        self.assertEqual(q[0]["kind"], "test")

    def test_host_binding_receives_message(self):
        # simulate the server binding: expose __bridge_host and see a post arrive
        received = []
        ctx = self.browser.new_context()
        page = ctx.new_page()
        page.expose_binding("__bridge_host", lambda source, msg: received.append(msg))
        page.set_content("<html></html>")
        page.evaluate(_asset("request.js"))
        page.evaluate("() => window.__bridgePostToHost('ws_in', {b64: 'AA=='})")
        page.wait_for_timeout(200)
        self.assertTrue(received and received[0]["kind"] == "ws_in")


if __name__ == "__main__":
    unittest.main()
