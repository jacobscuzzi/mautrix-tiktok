import unittest
from bridge import envelope, proto, errors

class TestEnvelope(unittest.TestCase):
    def test_parse_real_no_session(self):
        buf = bytes.fromhex("18c59a0c220632303030303522")[:12]
        r = envelope.parse_response(buf)
        self.assertEqual(r["status_code"], 200005)
        self.assertEqual(r["error_desc"], "200005")

    def test_parse_success_with_body(self):
        inner = proto.encode_fields({1: b"conv-data"})
        buf = proto.encode_fields({3: 0, 6: inner, 7: b"log123"})
        r = envelope.parse_response(buf)
        self.assertEqual(r["status_code"], 0)
        self.assertEqual(r["log_id"], "log123")
        self.assertEqual(r["body"], inner)

    def test_classify_maps_codes(self):
        self.assertIsNone(envelope.classify(0, None))
        with self.assertRaises(errors.IMNotInitialized):
            envelope.classify(200001, "x")
        with self.assertRaises(errors.AuthError):
            envelope.classify(200005, "x")

if __name__ == "__main__":
    unittest.main()
