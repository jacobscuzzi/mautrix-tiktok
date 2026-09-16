import json
import unittest
from bridge.passport import PassportClient
from bridge.device import Device
from bridge.signing import NullSigner
from bridge import errors

class FakeTransport:
    def __init__(self, status, cookies, body):
        self.status = status
        self.cookies = cookies
        self.body = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.last = None
    def __call__(self, method, url, headers, data):
        self.last = (method, url, headers, data)
        return self.status, self.cookies, self.body

class TestPassport(unittest.TestCase):
    def _pc(self, t):
        return PassportClient(Device.generate(), NullSigner(), transport=t)

    def test_send_code_success(self):
        t = FakeTransport(200, {}, {"message": "success", "data": {}})
        self._pc(t).send_email_code("a@b.com")
        self.assertIn("/passport/email/send_code/", t.last[1])
        self.assertIn(b"scene=login", t.last[3])

    def test_login_applies_session_cookies(self):
        t = FakeTransport(200, {"sessionid": "S", "sid_tt": "T", "uid_tt": "42"},
                          {"message": "success", "data": {"user_id": 42}})
        pc = self._pc(t)
        pc.email_code_login("a@b.com", "123456")
        self.assertTrue(pc.device.is_authenticated)
        self.assertEqual(pc.device.sessionid, "S")
        self.assertEqual(pc.device.uid_tt, "42")

    def test_wrong_code_raises_invalid(self):
        t = FakeTransport(200, {}, {"message": "error",
                                    "data": {"error_code": 1033, "description": "wrong code"}})
        with self.assertRaises(errors.InvalidRequest):
            self._pc(t).email_code_login("a@b.com", "000000")

    def test_rate_limit_maps(self):
        t = FakeTransport(200, {}, {"message": "error",
                                    "data": {"error_code": 7, "description": "too many"}})
        with self.assertRaises(errors.RateLimited):
            self._pc(t).send_email_code("a@b.com")

if __name__ == "__main__":
    unittest.main()
