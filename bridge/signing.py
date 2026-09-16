import base64
import json
import subprocess
from typing import Protocol
from . import errors

class Signer(Protocol):
    def sign(self, method, path, query, body, device): ...

class NullSigner:
    def sign(self, method, path, query, body, device):
        return {}

class SubprocessSigner:
    def __init__(self, cmd):
        self.cmd = cmd

    def sign(self, method, path, query, body, device):
        req = json.dumps({
            "method": method, "path": path, "query": query,
            "body_b64": base64.b64encode(body or b"").decode(),
            "cookie": device.cookie_string(),
            "device_id": device.device_id, "install_id": device.install_id,
        }).encode()
        try:
            out = subprocess.run(self.cmd, input=req, capture_output=True, timeout=5)
        except subprocess.SubprocessError as e:
            raise errors.SignerStale(f"signer process error: {e}")
        if out.returncode != 0:
            raise errors.SignerStale(f"signer exit {out.returncode}: {out.stderr.decode()[:200]}")
        return json.loads(out.stdout)
