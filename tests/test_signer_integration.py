import os
import unittest
from bridge import signing
from bridge.device import Device

VENV_PY = ".venv/bin/python"
SHIM = "signer/signerpy_shim.py"

@unittest.skipUnless(os.path.exists(VENV_PY) and os.path.exists(SHIM),
                     "venv with SignerPy not present")
class TestSignerIntegration(unittest.TestCase):
    def test_shim_produces_all_headers(self):
        s = signing.SubprocessSigner([VENV_PY, SHIM])
        dev = Device.generate()
        query = "&".join(f"{k}={v}" for k, v in dev.common_params().items())
        h = s.sign("POST", "/passport/email/send_code/", query,
                   b"email=x@y.com&scene=login&mix_mode=1", dev)
        for k in ("x-gorgon", "x-khronos", "x-ladon", "x-argus", "x-ss-stub"):
            self.assertIn(k, h)
            self.assertTrue(h[k])

if __name__ == "__main__":
    unittest.main()
