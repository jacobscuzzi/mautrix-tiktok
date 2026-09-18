import unittest
from bridge import proto

class TestProto(unittest.TestCase):
    def test_read_varint_multibyte(self):
        v, i = proto.read_varint(bytes.fromhex("c59a0c"), 0)
        self.assertEqual(v, 200005)
        self.assertEqual(i, 3)

    def test_decode_real_envelope_prefix(self):
        buf = bytes.fromhex("18c59a0c220632303030303522")[:12]
        f = proto.decode_fields(buf)
        self.assertEqual(f[3][0], 200005)
        self.assertEqual(f[4][0], b"200005")

    def test_encode_decode_roundtrip(self):
        buf = proto.encode_fields({1: 7, 3: 200005, 4: b"hello"})
        f = proto.decode_fields(buf)
        self.assertEqual(f[1][0], 7)
        self.assertEqual(f[3][0], 200005)
        self.assertEqual(f[4][0], b"hello")

if __name__ == "__main__":
    unittest.main()


class TestProtoTree(unittest.TestCase):
    def test_valid_message_detection(self):
        good = proto.encode_fields({1: 5, 2: b"hi"})
        self.assertTrue(proto._valid_message(good))
        self.assertFalse(proto._valid_message(b"\xff\xff\xffnonsense"))

    def test_decode_tree_nested(self):
        inner = proto.encode_fields({1: b"conv7", 3: 42})
        outer = proto.encode_fields({6: inner, 3: 0, 4: b"text"})
        tree = proto.decode_tree(outer)
        self.assertEqual(tree[3][0], 0)
        self.assertEqual(tree[4][0], "text")
        self.assertEqual(tree[6][0][1][0], "conv7")
        self.assertEqual(tree[6][0][3][0], 42)

    def test_decode_tree_keeps_binary_as_bytes(self):
        buf = proto.encode_fields({1: b"\x00\x01\x02\xff\xfe"})
        tree = proto.decode_tree(buf)
        self.assertIsInstance(tree[1][0], (bytes, str))


class TestEncodeTree(unittest.TestCase):
    def test_roundtrip_nested(self):
        tree = {1: [301], 2: [10004],
                8: [{301: [{1: ["0:1:1:2"], 2: [1], 3: [7686889546618028310],
                            4: [1], 5: [1789743696583737], 6: [30]}]}],
                15: [{1: ["aid"], 2: ["1988"]}]}
        back = proto.decode_tree(proto.encode_tree(tree))
        self.assertEqual(back[1][0], 301)
        cmd = back[8][0][301][0]
        self.assertEqual(cmd[1][0], "0:1:1:2")
        self.assertEqual(cmd[3][0], 7686889546618028310)
        self.assertEqual(cmd[6][0], 30)
