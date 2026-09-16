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

class SequenceTransport:
    # Each item is (status, resp_headers, content). Returns them in order,
    # repeating the last once exhausted.
    def __init__(self, items):
        self.items = items
        self.calls = 0
    def __call__(self, method, url, headers, body):
        i = min(self.calls, len(self.items) - 1)
        self.calls += 1
        return self.items[i]

def _noslp(_):
    return None

class TestClient(unittest.TestCase):
    def _client(self, transport):
        return Client(Device.generate(), NullSigner(), transport=transport, sleep=_noslp)

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

class TestClientHttpStatuses(unittest.TestCase):
    def _client(self, status, content=b""):
        return Client(Device.generate(), NullSigner(),
                      transport=FakeTransport(status, content), sleep=_noslp)

    def test_401_is_auth_error(self):
        with self.assertRaises(errors.AuthError):
            self._client(401).get_im("/v1/x/", {})

    def test_403_is_banned(self):
        with self.assertRaises(errors.Banned):
            self._client(403).get_im("/v1/x/", {})

    def test_404_is_invalid_request(self):
        with self.assertRaises(errors.InvalidRequest):
            self._client(404).get_im("/v1/x/", {})

    def test_empty_200_body_is_invalid_request(self):
        with self.assertRaises(errors.InvalidRequest):
            self._client(200, b"").get_im("/v1/x/", {})

class TestClientRetry(unittest.TestCase):
    def _client(self, transport, **kw):
        return Client(Device.generate(), NullSigner(), transport=transport, sleep=_noslp, **kw)

    def test_retries_5xx_then_succeeds(self):
        ok = proto.encode_fields({3: 0, 7: b"l"})
        t = SequenceTransport([(500, {}, b""), (500, {}, b""), (200, {}, ok)])
        r = self._client(t, max_retries=2).get_im("/v1/x/", {})
        self.assertEqual(r["status_code"], 0)
        self.assertEqual(t.calls, 3)

    def test_gives_up_after_max_retries(self):
        t = SequenceTransport([(500, {}, b"")])
        with self.assertRaises(errors.Transient):
            self._client(t, max_retries=2).get_im("/v1/x/", {})
        self.assertEqual(t.calls, 3)

    def test_retries_429_then_succeeds(self):
        ok = proto.encode_fields({3: 0, 7: b"l"})
        t = SequenceTransport([(429, {"Retry-After": "0"}, b""), (200, {}, ok)])
        r = self._client(t, max_retries=2).get_im("/v1/x/", {})
        self.assertEqual(r["status_code"], 0)
        self.assertEqual(t.calls, 2)

if __name__ == "__main__":
    unittest.main()
