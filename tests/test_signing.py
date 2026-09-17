import unittest
from bridge import signing, errors
from bridge.device import Device

class TestSigning(unittest.TestCase):
    def test_null_signer_returns_no_headers(self):
        h = signing.NullSigner().sign("GET", "/v1/conversation/list/", "", b"", Device.generate())
        self.assertEqual(h, {})

    def test_subprocess_signer_parses_json(self):
        s = signing.SubprocessSigner(["python3", "-c",
            "import json,sys; print(json.dumps({'x-gorgon':'g','x-khronos':'k'}))"])
        h = s.sign("GET", "/v1/x/", "a=b", b"", Device.generate())
        self.assertEqual(h["x-gorgon"], "g")
        self.assertEqual(h["x-khronos"], "k")

    def test_subprocess_signer_failure_raises_signer_stale(self):
        s = signing.SubprocessSigner(["python3", "-c", "import sys; sys.exit(3)"])
        with self.assertRaises(errors.SignerStale):
            s.sign("GET", "/v1/x/", "", b"", Device.generate())

if __name__ == "__main__":
    unittest.main()


class TestSignerOutputGuard(unittest.TestCase):
    def test_non_json_output_raises_signer_stale(self):
        s = signing.SubprocessSigner(["python3", "-c", "print('not json')"])
        with self.assertRaises(errors.SignerStale):
            s.sign("GET", "/v1/x/", "", b"", Device.generate())
