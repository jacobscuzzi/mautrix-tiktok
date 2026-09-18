import base64
import gzip
import json
import os
import unittest

from bridge import proto
from bridge.web import frontier
from bridge.providers.web import WebProvider

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "web")


def _frame(service, method, headers, body_bytes, gzip_it=True):
    payload = gzip.compress(body_bytes) if gzip_it else body_bytes
    raw = bytearray(proto.encode_fields({1: 1, 3: service, 4: method}))
    for k, v in headers.items():
        raw += proto.encode_fields({5: proto.encode_fields({1: k.encode(), 2: v.encode()})})
    raw += proto.encode_fields({6: (b"gzip" if gzip_it else b""), 8: payload})
    return bytes(raw)


def _message_frame(conv, mid, ts_us, sender, content, headers=None):
    # real layout (confirmed live): body f6 -> f500 -> f5 = Message{1 conv, 3 mid,
    # 4 create_time(us), 7 sender, 8 content JSON}.
    msg = proto.encode_fields({1: conv.encode(), 3: mid, 4: ts_us,
                               7: sender, 8: content.encode()})
    envelope = proto.encode_fields({2: conv.encode(), 5: msg})
    body = proto.encode_fields({1: 500, 6: proto.encode_fields({500: envelope})})
    return _frame(20032, 1, headers or {":x_frontier_msg_id": "msg_x"}, body)


class TestFrontier(unittest.TestCase):
    def test_decode_envelope_and_headers(self):
        raw = _frame(20032, 1, {"X-Method": "PayloadRelatedMethod",
                                ":x_frontier_msg_id": "msg_abc"}, proto.encode_fields({1: b"x"}))
        d = frontier.decode_frame(raw)
        self.assertEqual(d["service"], 20032)
        self.assertEqual(d["headers"]["X-Method"], "PayloadRelatedMethod")
        self.assertEqual(d["headers"]["x_frontier_msg_id"], "msg_abc")

    def test_sync_frame_yields_no_messages(self):
        # a pure cursor entry (no f6/f500 message) -> nothing (empty-inbox case)
        body = proto.encode_fields({1: 500, 2: proto.encode_fields({1: 3, 4: 6983896500996660010})})
        raw = _frame(20032, 1, {"X-Method": "PayloadRelatedMethod"}, body)
        self.assertEqual(list(frontier.messages_from_frame(raw)), [])

    def test_message_frame_yields_text(self):
        raw = _message_frame("0:1:900:901", 7500000000000000009, 1789707000000000, 901,
                             '{"aweType":0,"text":"hello dm"}',
                             headers={":x_frontier_msg_id": "msg_z"})
        msgs = list(frontier.messages_from_frame(raw))
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["server_message_id"], "7500000000000000009")
        self.assertEqual(msgs[0]["sender"], "901")
        self.assertEqual(msgs[0]["content"], "hello dm")
        self.assertEqual(msgs[0]["create_time"], 1789707000000)  # us -> ms
        self.assertEqual(msgs[0]["raw_ref"], "msg_z")

    def test_read_receipt_command_frame_skipped(self):
        # a mark-read command carries command_type, not a DM
        raw = _message_frame("0:1:900:901", 7500000000000000010, 1789707000000000, 901,
                             '{"command_type":1,"read_index":123}')
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

    def test_ws_inbound_dm_fixture(self):
        # the committed ws_inbound_dm fixture (real layout, neutral text) must parse
        path = os.path.join(FIX, "ws_inbound_dm.json")
        if not os.path.exists(path):
            self.skipTest("no ws_inbound_dm fixture")
        with open(path) as f:
            ent = json.load(f)
        msgs = list(frontier.messages_from_frame(base64.b64decode(ent["frame_b64"])))
        self.assertEqual(len(msgs), 1)
        self.assertTrue(msgs[0]["server_message_id"] and msgs[0]["sender"])
        self.assertTrue(msgs[0]["content"])

    def test_real_captured_dm_frames_if_present(self):
        # decode the real (gitignored) G4 DM capture if it exists on this machine;
        # proves the parser on live data without committing anyone's real messages.
        import glob
        caps = glob.glob("browser-data/*/capture-g4-*.jsonl")
        if not caps:
            self.skipTest("no live G4 capture present")
        found = 0
        for cap in caps:
            with open(cap) as fh:
                lines = fh.readlines()
            for line in lines:
                rec = json.loads(line)
                if rec.get("kind") != "ws_in":
                    continue
                for m in frontier.messages_from_frame(base64.b64decode(rec["data_b64"])):
                    found += 1
                    self.assertTrue(m["server_message_id"])
                    self.assertTrue(m["conversation_id"].count(":") >= 2)
        self.assertGreater(found, 0, "expected at least one real DM in the G4 capture")


class TestInitBacklog(unittest.TestCase):
    def _init_body(self, messages):
        # real layout (confirmed live): body f6 -> f203 -> f1[] = Message
        block = bytearray()
        for conv, mid, ts_us, sender, content in messages:
            block += proto.encode_fields({1: proto.encode_fields(
                {1: conv.encode(), 3: mid, 4: ts_us, 7: sender, 8: content.encode()})})
        return proto.encode_fields({1: 203, 4: b"OK",
                                    6: proto.encode_fields({203: bytes(block)})})

    def test_backlog_yields_every_message_across_conversations(self):
        body = self._init_body([
            ("0:1:1:2", 101, 1789751000000000, 2, '{"aweType":0,"text":"old one"}'),
            ("0:1:1:2", 102, 1789751001000000, 1, '{"aweType":0,"text":"old two"}'),
            ("0:1:1:3", 201, 1789751002000000, 3, '{"aweType":0,"text":"other chat"}'),
        ])
        msgs = list(frontier.messages_from_init_body(body))
        self.assertEqual([m["server_message_id"] for m in msgs], ["101", "102", "201"])
        self.assertEqual({m["conversation_id"] for m in msgs}, {"0:1:1:2", "0:1:1:3"})
        self.assertEqual(msgs[1]["sender"], "1")
        self.assertEqual(msgs[2]["content"], "other chat")

    def test_backlog_skips_commands_and_garbage(self):
        body = self._init_body([("0:1:1:2", 5, 1, 2, '{"command_type":1}')])
        self.assertEqual(list(frontier.messages_from_init_body(body)), [])
        self.assertEqual(list(frontier.messages_from_init_body(b"\xff\x00junk")), [])


class TestFrontierThroughProvider(unittest.TestCase):
    def test_provider_emits_and_flags_gap(self):
        class P:
            def on_frame(self, cb):
                self.cb = cb
        wp = WebProvider(P())
        raw = _message_frame("0:1:c1a:c1b", 12345, 1789707000000000, 901,
                             '{"aweType":0,"text":"hi"}', headers={":x_frontier_msg_id": "msg_1"})
        got = wp.on_frontier_frame(raw)
        self.assertEqual(got[0]["server_message_id"], "12345")
        self.assertEqual(got[0]["content"], "hi")

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
