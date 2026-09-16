import tempfile
import unittest
from bridge.auth.login import LoginProcess
from bridge.device import Device
from bridge.session_store import SessionStore

class FakeQR:
    def __init__(self, cookies_after=1):
        self.calls = 0
        self.cookies_after = cookies_after
    def start(self):
        return {"qrcode": "data:img", "token": "t1"}
    def poll(self, token):
        self.calls += 1
        if self.calls >= self.cookies_after:
            return {"status": "confirmed",
                    "cookies": "sessionid=s; sid_tt=st; uid_tt=9"}
        return {"status": "pending"}

class FakeBrowser:
    def capture(self):
        return {"cookies": "sessionid=web; uid_tt=9"}

class TestLogin(unittest.TestCase):
    def _proc(self, qr=None, browser=None):
        store = SessionStore(tempfile.mkdtemp(), master_key=b"0" * 32)
        return LoginProcess(Device.generate(), store, qr or FakeQR(), browser or FakeBrowser())

    def test_qr_happy_path(self):
        p = self._proc(qr=FakeQR(cookies_after=2))
        s = p.start("qr")
        self.assertEqual(s.kind, "display_and_wait")
        self.assertEqual(p.advance().kind, "display_and_wait")
        final = p.advance()
        self.assertEqual(final.kind, "complete")
        self.assertTrue(p.device.is_authenticated)
        self.assertEqual(p.device.sessionid, "s")

    def test_browser_capture_sets_cookies(self):
        p = self._proc()
        p.start("browser")
        final = p.advance()
        self.assertEqual(final.kind, "complete")
        self.assertEqual(p.device.sessionid, "web")

if __name__ == "__main__":
    unittest.main()
