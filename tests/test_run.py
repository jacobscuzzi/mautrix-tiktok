import unittest
from bridge.cmd.run import probe_status
from bridge import errors

class FakeClient:
    def __init__(self, exc=None):
        self.exc = exc
    def get_im(self, path, params=None):
        if self.exc:
            raise self.exc
        return {"status_code": 0}

class TestProbeStatus(unittest.TestCase):
    def test_connected(self):
        self.assertEqual(probe_status(FakeClient()), "connected")

    def test_rate_limited(self):
        self.assertEqual(probe_status(FakeClient(errors.RateLimited("429"))), "rate_limited")

    def test_im_not_initialized(self):
        self.assertEqual(probe_status(FakeClient(errors.IMNotInitialized("200001"))),
                         "im-not-initialized")

    def test_needs_reauth(self):
        self.assertEqual(probe_status(FakeClient(errors.AuthError("dead"))), "needs-reauth")

if __name__ == "__main__":
    unittest.main()
