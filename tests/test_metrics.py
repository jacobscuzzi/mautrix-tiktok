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


class TestMetricsGrace(unittest.TestCase):
    def test_grace_window_keeps_recent_sync_live(self):
        now = 1_000_000
        interval = 60_000
        # synced 90s ago: stale at 1x interval, live within 2x grace
        logins = [{"authenticated": True, "last_sync_ms": now - 90_000, "state": "connected"}]
        self.assertEqual(metrics.live_session_ratio(logins, now, interval, grace=1.0), 0.0)
        self.assertEqual(metrics.live_session_ratio(logins, now, interval, grace=2.0), 1.0)


class TestExposition(unittest.TestCase):
    def test_reauth_share_counts_demo_app_state(self):
        logins = [{"state": "needs_user"}, {"state": "needs-reauth"},
                  {"state": "connected"}, {"state": "connected"}]
        self.assertAlmostEqual(metrics.reauth_share(logins), 0.5)

    def test_error_counter_declared_once(self):
        text = metrics.render_prometheus([], 0, 1000, {"needs_user": 2, "error": 1})
        self.assertEqual(text.count("# TYPE bridge_error_total counter"), 1)
        self.assertIn('bridge_error_total{state="error"} 1', text)
        self.assertIn('bridge_error_total{state="needs_user"} 2', text)
