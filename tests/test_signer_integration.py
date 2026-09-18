import os
import unittest
from bridge import signing
from bridge.device import Device

VENV_PY = ".venv/bin/python"
SHIM = "signer/signerpy_shim.py"


def _signer_available():
    if not (os.path.exists(VENV_PY) and os.path.exists(SHIM)):
        return False
    import subprocess
    # SignerPy is a third-party dep installed on the server but not the laptop;
    # the mobile signer is irrelevant to the web path. Skip unless it can sign.
    out = subprocess.run([VENV_PY, "-c", "import SignerPy"], capture_output=True)
    return out.returncode == 0


@unittest.skipUnless(_signer_available(), "SignerPy not importable in this venv")
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
