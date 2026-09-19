import base64
import gzip
import importlib.util
import os
import unittest

from bridge import proto

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_HERE, rel))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


decoder = _load("decode_frontier", "scripts/decode-frontier.py")


def _make_frame(service, method, headers, payload, gzip_it):
    enc = "gzip" if gzip_it else ""
    body = gzip.compress(payload) if gzip_it else payload
    raw = bytearray(proto.encode_fields({1: 5, 3: service, 4: method}))
    for k, v in headers.items():
        h = proto.encode_fields({1: k.encode(), 2: v.encode()})
        raw += proto.encode_fields({5: h})
    raw += proto.encode_fields({6: enc.encode(), 8: body})
    return bytes(raw)


class TestFrontierDecode(unittest.TestCase):
    def test_decodes_envelope_headers_and_gzip(self):
        payload = proto.encode_fields({1: b"hello dm", 3: 42})
        raw = _make_frame(20032, 1, {"X-Method": "PayloadRelatedMethod"}, payload, gzip_it=True)
        d = decoder.decode_frame(raw)
        self.assertEqual(d["service"], [20032])
        self.assertEqual(d["method"], [1])
        self.assertTrue(d["gzipped"])
        self.assertEqual(d["headers"].get("X-Method"), "PayloadRelatedMethod")
        self.assertIn("hello dm", d["text_runs"])

    def test_plain_payload_not_gunzipped(self):
        payload = proto.encode_fields({1: b"plain"})
        raw = _make_frame(33554513, 2, {}, payload, gzip_it=False)
        d = decoder.decode_frame(raw)
        self.assertFalse(d["gzipped"])
        self.assertIn("plain", d["text_runs"])

    def test_real_captured_frame_if_present(self):
        # decode the committed real subscribe fixture if it exists
        import json
        f = os.path.join(_HERE, "tests", "fixtures", "web", "ws_outbound.json")
        if not os.path.exists(f):
            self.skipTest("no ws fixture")
        with open(f) as fh:
            ent = json.load(fh)
        raw = base64.b64decode(ent["frame_b64"])
        d = decoder.decode_frame(raw)
        self.assertEqual(d["service"], [33554513])
        self.assertEqual(d["method"], [2])


if __name__ == "__main__":
    unittest.main()
