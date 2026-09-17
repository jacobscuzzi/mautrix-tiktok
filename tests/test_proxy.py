import unittest
from bridge.proxy import ProxyPool

class TestProxyPool(unittest.TestCase):
    def test_empty_pool(self):
        p = ProxyPool([])
        self.assertFalse(p)
        self.assertIsNone(p.for_login("u1"))

    def test_stable_assignment(self):
        p = ProxyPool(["http://a", "http://b", "http://c"])
        first = p.for_login("user-42")
        self.assertEqual(first, p.for_login("user-42"))
        self.assertIn(first, p.proxies)

    def test_failover_picks_different(self):
        p = ProxyPool(["http://a", "http://b"])
        cur = p.for_login("u")
        self.assertNotEqual(p.failover("u", cur), cur)

    def test_failover_single_proxy_stays(self):
        p = ProxyPool(["http://only"])
        self.assertEqual(p.failover("u", "http://only"), "http://only")

if __name__ == "__main__":
    unittest.main()
