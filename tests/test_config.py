import base64
import os
import tempfile
import unittest
from bridge.config import Config

class TestConfig(unittest.TestCase):
    def test_env_master_key_decoded(self):
        key = base64.b64encode(b"k" * 32).decode()
        c = Config.load(path="/nonexistent.yaml", environ={"BRIDGE_MASTER_KEY": key})
        self.assertEqual(c.master_key, b"k" * 32)

    def test_missing_key_is_none(self):
        c = Config.load(path="/nonexistent.yaml", environ={})
        self.assertIsNone(c.master_key)

    def test_bad_key_length_raises(self):
        key = base64.b64encode(b"short").decode()
        with self.assertRaises(ValueError):
            Config.load(path="/nonexistent.yaml", environ={"BRIDGE_MASTER_KEY": key})

    def test_yaml_values(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "config.yaml")
        with open(p, "w") as f:
            f.write("base_host: h.example\npoll_interval_seconds: 5\nproxies: [http://a, http://b]\n")
        c = Config.load(path=p, environ={})
        self.assertEqual(c.base_host, "h.example")
        self.assertEqual(c.poll_interval_seconds, 5)
        self.assertEqual(c.proxies, ["http://a", "http://b"])

if __name__ == "__main__":
    unittest.main()
