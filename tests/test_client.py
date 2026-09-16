import unittest
from bridge.client import Client
from bridge.device import Device
from bridge.signing import NullSigner
from bridge import proto, errors

class FakeTransport:
    def __init__(self, status, content):
        self.status = status
        self.content = content
        self.last = None
    def __call__(self, method, url, headers, body):
        self.last = (method, url, headers, body)
        return self.status, {}, self.content

class TestClient(unittest.TestCase):
    def _client(self, transport):
        return Client(Device.generate(), NullSigner(), transport=transport)

    def test_get_im_parses_success(self):
        body = proto.encode_fields({3: 0, 7: b"log1"})
        t = FakeTransport(200, body)
        r = self._client(t).get_im("/v1/conversation/list/", {"cursor": "0"})
        self.assertEqual(r["status_code"], 0)
        self.assertIn("device_id=", t.last[1])
        self.assertIn("aid=1233", t.last[1])

    def test_auth_error_raised_on_200005(self):
        body = proto.encode_fields({3: 200005, 4: b"200005"})
        with self.assertRaises(errors.AuthError):
            self._client(FakeTransport(200, body)).get_im("/v1/conversation/list/", {})

    def test_rate_limit_on_429(self):
        with self.assertRaises(errors.RateLimited):
            self._client(FakeTransport(429, b"")).get_im("/v1/x/", {})

    def test_transient_on_500(self):
        with self.assertRaises(errors.Transient):
            self._client(FakeTransport(500, b"")).get_im("/v1/x/", {})

if __name__ == "__main__":
    unittest.main()
