import unittest
from bridge.device import Device

class TestDevice(unittest.TestCase):
    def test_generate_is_stable_across_calls(self):
        d = Device.generate()
        p1 = d.common_params()
        p2 = d.common_params()
        self.assertEqual(p1["device_id"], p2["device_id"])
        self.assertEqual(p1["iid"], p2["iid"])
        self.assertEqual(len(d.device_id), 19)
        self.assertTrue(d.device_id.isdigit())

    def test_two_generates_differ(self):
        self.assertNotEqual(Device.generate().device_id, Device.generate().device_id)

    def test_import_cookies_and_auth_state(self):
        d = Device.generate()
        self.assertFalse(d.is_authenticated)
        d.import_cookies("sessionid=abc; sid_tt=def; sid_guard=g; uid_tt=42")
        self.assertTrue(d.is_authenticated)
        self.assertEqual(d.sessionid, "abc")
        self.assertIn("sessionid=abc", d.cookie_string())

    def test_common_params_have_mobile_aid(self):
        p = Device.generate().common_params()
        self.assertEqual(p["aid"], "1233")
        self.assertEqual(p["app_name"], "musical_ly")

if __name__ == "__main__":
    unittest.main()
