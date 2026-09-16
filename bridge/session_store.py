import json
import os
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

class SessionStore:
    def __init__(self, dir_path, master_key):
        if len(master_key) != 32:
            raise ValueError("master_key must be 32 bytes")
        self.dir = dir_path
        self.master = AESGCM(master_key)
        os.makedirs(dir_path, exist_ok=True)

    def _path(self, user_id):
        return os.path.join(self.dir, f"{user_id}.session")

    def save(self, user_id, blob):
        data_key = os.urandom(32)
        dn = os.urandom(12)
        ct = AESGCM(data_key).encrypt(dn, json.dumps(blob).encode(), None)
        wn = os.urandom(12)
        wrapped = self.master.encrypt(wn, data_key, None)
        out = b"".join([len(wrapped).to_bytes(2, "big"), wn, wrapped, dn, ct])
        tmp = self._path(user_id) + ".tmp"
        with open(tmp, "wb") as f:
            f.write(out)
        os.replace(tmp, self._path(user_id))

    def load(self, user_id):
        with open(self._path(user_id), "rb") as f:
            raw = f.read()
        wl = int.from_bytes(raw[:2], "big")
        i = 2
        wn = raw[i:i + 12]; i += 12
        wrapped = raw[i:i + wl]; i += wl
        dn = raw[i:i + 12]; i += 12
        ct = raw[i:]
        data_key = self.master.decrypt(wn, wrapped, None)
        pt = AESGCM(data_key).decrypt(dn, ct, None)
        return json.loads(pt)

    def delete(self, user_id):
        try:
            os.remove(self._path(user_id))
        except FileNotFoundError:
            pass
