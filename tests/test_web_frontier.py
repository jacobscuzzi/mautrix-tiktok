import base64
import gzip
import json
import os
import unittest

from bridge import proto
from bridge.web import frontier
from bridge.providers.web import WebProvider

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "web")


def _frame(service, method, headers, body_fields, gzip_it=True):
    inner = proto.encode_fields(body_fields)
    payload = gzip.compress(inner) if gzip_it else inner
    raw = bytearray(proto.encode_fields({1: 1, 3: service, 4: method}))
    for k, v in headers.items():
        raw += proto.encode_fields({5: proto.encode_fields({1: k.encode(), 2: v.encode()})})
    raw += proto.encode_fields({6: (b"gzip" if gzip_it else b""), 8: payload})
    return bytes(raw)


class TestFrontier(unittest.TestCase):
    def test_decode_envelope_and_headers(self):
        raw = _frame(20032, 1, {"X-Method": "PayloadRelatedMethod",
                                ":x_frontier_msg_id": "msg_abc"}, {1: b"x"})
        d = frontier.decode_frame(raw)
        self.assertEqual(d["service"], 20032)
        self.assertEqual(d["headers"]["X-Method"], "PayloadRelatedMethod")
        self.assertEqual(d["headers"]["x_frontier_msg_id"], "msg_abc")

    def test_sync_frame_yields_no_messages(self):
        # a pure cursor entry (no nested field 7) -> nothing (real empty-inbox case)
        body = {2: proto.encode_fields({1: 3, 4: 6983896500996660010})}
        raw = _frame(20032, 1, {"X-Method": "PayloadRelatedMethod"}, body)
        self.assertEqual(list(frontier.messages_from_frame(raw)), [])

    def test_message_frame_yields_text(self):
        # synthetic inner message: conv(1), sender(3), mid(4), ts(5), content(7)
        inner = proto.encode_fields({1: b"0:1:900:901", 3: b"901", 4: b"7500000000000000009",
                                     5: 1789707000000, 7: b'{"text":"hello dm"}'})
        entry = proto.encode_fields({7: inner})
        raw = _frame(20032, 1, {"X-Method": "PayloadRelatedMethod",
                                ":x_frontier_msg_id": "msg_z"}, {2: entry})
        msgs = list(frontier.messages_from_frame(raw))
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["server_message_id"], "7500000000000000009")
        self.assertIn("hello dm", msgs[0]["content"])
        self.assertEqual(msgs[0]["raw_ref"], "msg_z")

    def test_unknown_method_dropped_not_raised(self):
        raw = _frame(20032, 1, {"X-Method": "SomeOtherMethod"}, {2: b""})
        self.assertEqual(list(frontier.messages_from_frame(raw)), [])

    def test_garbage_frame_never_raises(self):
        self.assertEqual(list(frontier.messages_from_frame(b"\xff\xff\xffnonsense")), [])

    def test_real_captured_sync_fixture_decodes(self):
        with open(os.path.join(FIX, "ws_frontier_sync.json")) as f:
            ent = json.load(f)
        raw = base64.b64decode(ent["frame_b64"])
        d = frontier.decode_frame(raw)
        self.assertIn(d["service"], (20032, 33554513))
        # empty inbox -> the real frame carries no DM text
        self.assertEqual(list(frontier.messages_from_frame(raw)), [])


class TestFrontierThroughProvider(unittest.TestCase):
    def test_provider_emits_and_flags_gap(self):
        class P:
            def on_frame(self, cb):
                self.cb = cb
        wp = WebProvider(P())
        inner = proto.encode_fields({1: b"c1", 3: b"901", 4: b"m1", 5: 1, 7: b'{"text":"hi"}'})
        raw = _frame(20032, 1, {":x_frontier_msg_id": "msg_1"}, {2: proto.encode_fields({7: inner})})
        got = wp.on_frontier_frame(raw)
        self.assertEqual(got[0]["server_message_id"], "m1")

    def test_reconcile_only_when_gap(self):
        class P:
            def on_frame(self, cb):
                pass
        class FakeSyncer:
            def __init__(self):
                self.n = 0
            def poll_once(self):
                self.n += 1
                return 5
        wp = WebProvider(P())
        s = FakeSyncer()
        self.assertEqual(wp.reconcile_if_gap(s), 0)
        wp.note_gap()
        self.assertEqual(wp.reconcile_if_gap(s), 5)
        self.assertEqual(wp.reconcile_if_gap(s), 0)  # gap cleared


if __name__ == "__main__":
    unittest.main()
