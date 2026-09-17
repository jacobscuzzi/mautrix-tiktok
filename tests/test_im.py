import unittest
from bridge.im import IM
from bridge import proto

class FakeClient:
    def __init__(self):
        self.last_body = None
        self.last_path = None
    def get_im(self, path, params=None):
        self.last_path = path
        return {"status_code": 0, "body": b"", "raw": {}}
    def post_im(self, path, body, params=None):
        self.last_path = path
        self.last_body = body
        return {"status_code": 0, "body": None, "raw": {}}

class TestIM(unittest.TestCase):
    def test_send_text_builds_envelope(self):
        c = FakeClient()
        IM(c).send_text("conv7", "hello", ticket="tk", client_message_id="cm1")
        self.assertEqual(c.last_path, "/v1/message/send/")
        outer = proto.decode_fields(c.last_body)
        self.assertEqual(outer[1][0], 1)
        rb = proto.decode_fields(outer[8][0])
        smb = proto.decode_fields(rb[1][0])
        self.assertEqual(smb[1][0], b"conv7")
        self.assertEqual(smb[4][0], b"hello")
        self.assertEqual(smb[6][0], 1)
        self.assertEqual(smb[7][0], b"tk")
        self.assertEqual(smb[8][0], b"cm1")

    def test_list_conversations_hits_path(self):
        c = FakeClient()
        items, nxt, more = IM(c).list_conversations("0")
        self.assertEqual(c.last_path, "/v1/conversation/list/")
        self.assertEqual(items, [])

if __name__ == "__main__":
    unittest.main()


class TestSendResponseParse(unittest.TestCase):
    def test_parses_server_message_id(self):
        from bridge.im import _parse_send_response
        smb = proto.encode_fields({1: b"srv-999", 3: 0, 4: b"cm1", 9: b"ticket2"})
        response_body = proto.encode_fields({1: smb})
        resp = {"status_code": 0, "body": response_body, "log_id": "L"}
        out = _parse_send_response(resp, "cm1")
        self.assertTrue(out["ok"])
        self.assertEqual(out["server_message_id"], "srv-999")
        self.assertEqual(out["new_ticket"], "ticket2")

    def test_missing_body_is_not_ok_ids(self):
        from bridge.im import _parse_send_response
        out = _parse_send_response({"status_code": 0, "body": None}, "cm1")
        self.assertTrue(out["ok"])
        self.assertIsNone(out["server_message_id"])
