import unittest
from bridge import metrics

class TestMetrics(unittest.TestCase):
    def test_live_ratio(self):
        now = 1_000_000
        logins = [
            {"authenticated": True, "last_sync_ms": now - 1000, "state": "connected"},
            {"authenticated": True, "last_sync_ms": now - 999999, "state": "connected"},
            {"authenticated": False, "last_sync_ms": now, "state": "needs-reauth"},
        ]
        self.assertAlmostEqual(metrics.live_session_ratio(logins, now, 60000), 1/3)

    def test_reauth_share(self):
        logins = [{"state": "needs-reauth"}, {"state": "connected"}]
        self.assertAlmostEqual(metrics.reauth_share(logins), 0.5)

    def test_empty(self):
        self.assertEqual(metrics.live_session_ratio([], 0, 1), 0.0)
        self.assertEqual(metrics.reauth_share([]), 0.0)

if __name__ == "__main__":
    unittest.main()
