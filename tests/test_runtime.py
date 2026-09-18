import unittest

from bridge import errors
from bridge.app import BridgeRuntime
from bridge.pipeline import Pipeline


class DeadProvider:
    def list_contacts(self, cursor="0", count=90):
        raise errors.AuthError("session dead")

    def list_conversations(self, cursor="0", count=20):
        raise errors.AuthError("session dead")

    def get_messages(self, conv_id, cursor="0", count=20):
        raise errors.AuthError("session dead")


class RateLimitedProvider(DeadProvider):
    def list_contacts(self, cursor="0", count=90):
        return [], "", False

    def list_conversations(self, cursor="0", count=20):
        raise errors.RateLimited("429")


class TestRuntime(unittest.TestCase):
    def setUp(self):
        self.p = Pipeline(":memory:")
        self.rt = BridgeRuntime(self.p)

    def test_dead_session_surfaces_needs_user_and_stops(self):
        self.rt.add_login("L1", DeadProvider())
        self.assertIsNone(self.rt.connect("L1"))
        self.assertEqual(self.rt.status("L1")["state"], "needs_user")
        self.assertIn("needs_user", self.rt.error_totals)

    def test_rate_limited_state(self):
        self.rt.add_login("L2", RateLimitedProvider())
        self.rt.connect("L2")
        self.assertEqual(self.rt.status("L2")["state"], "rate_limited")

    def test_metrics_reflect_states(self):
        self.rt.add_login("L1", DeadProvider(), password_login_used=True)
        self.rt.connect("L1")
        text = self.rt.render_metrics()
        self.assertIn("bridge_live_session_ratio 0", text)
        self.assertIn('bridge_error_total{state="needs_user"}', text)


if __name__ == "__main__":
    unittest.main()
