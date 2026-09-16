import unittest
from bridge import errors

class TestErrors(unittest.TestCase):
    def test_hierarchy(self):
        for cls in (errors.AuthError, errors.RateLimited, errors.Banned,
                    errors.IMNotInitialized, errors.SignerStale,
                    errors.Transient, errors.InvalidRequest):
            self.assertTrue(issubclass(cls, errors.BridgeError))

    def test_carries_code_and_log_id(self):
        e = errors.AuthError("expired", code=200003, log_id="abc")
        self.assertEqual(e.code, 200003)
        self.assertEqual(e.log_id, "abc")
        self.assertIn("expired", str(e))

if __name__ == "__main__":
    unittest.main()
