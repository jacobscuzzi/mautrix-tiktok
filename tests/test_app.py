import unittest
from bridge.app import BridgeApp
from bridge import errors

class FakeSyncer:
    def __init__(self, script):
        self.script = list(script)
        self.calls = 0
    def poll_once(self):
        r = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        if isinstance(r, Exception):
            raise r
        return r

def _noslp(_):
    return None

class TestBridgeApp(unittest.TestCase):
    def _app(self, syncer, states):
        return BridgeApp(syncer, poll_interval=30, on_state=states.append, sleep=_noslp)

    def test_normal_poll_emits_connected(self):
        states = []
        app = self._app(FakeSyncer([3]), states)
        self.assertEqual(app.run_once(), 3)
        self.assertEqual(states, ["connected"])

    def test_rate_limit_backs_off_then_recovers(self):
        states = []
        app = self._app(FakeSyncer([errors.RateLimited("429"), 1]), states)
        app.run_once()  # rate limited -> backoff = poll_interval
        self.assertEqual(app._backoff, 30)
        app.run_once()  # recovers -> backoff reset
        self.assertEqual(app._backoff, 0)
        self.assertEqual(states, ["rate_limited", "connected"])

    def test_auth_error_stops_with_needs_reauth(self):
        states = []
        app = self._app(FakeSyncer([errors.AuthError("dead")]), states)
        result = app.run(iterations=5)
        self.assertEqual(result, "needs-reauth")
        self.assertIn("needs-reauth", states)

if __name__ == "__main__":
    unittest.main()
