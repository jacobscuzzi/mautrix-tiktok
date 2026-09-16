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
