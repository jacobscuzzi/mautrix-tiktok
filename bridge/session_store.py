"""Envelope-encrypted session blobs at rest (AES-GCM).

One file per login: a fresh per-login data key encrypts the JSON blob and the master
key (env/KMS) wraps that data key. The login id is bound as associated data, so a
blob cannot be swapped between logins undetected, and it is validated before it
becomes a file name.
"""
import json
import os
import re

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_ID_RE = re.compile(r"[A-Za-z0-9_.-]{1,64}")


class SessionStore:
    def __init__(self, dir_path, master_key):
        if len(master_key) != 32:
            raise ValueError("master_key must be 32 bytes")
        self.dir = dir_path
        self.master = AESGCM(master_key)
        os.makedirs(dir_path, exist_ok=True)

    def _path(self, user_id):
        if not _ID_RE.fullmatch(str(user_id)):
            raise ValueError("invalid login id for a session file")
        return os.path.join(self.dir, f"{user_id}.session")

    def save(self, user_id, blob):
        aad = str(user_id).encode()
        data_key = os.urandom(32)
        dn = os.urandom(12)
        ct = AESGCM(data_key).encrypt(dn, json.dumps(blob).encode(), aad)
        wn = os.urandom(12)
        wrapped = self.master.encrypt(wn, data_key, aad)
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
        aad = str(user_id).encode()
        data_key = self.master.decrypt(wn, wrapped, aad)
        pt = AESGCM(data_key).decrypt(dn, ct, aad)
        return json.loads(pt)

    def delete(self, user_id):
        try:
            os.remove(self._path(user_id))
        except FileNotFoundError:
            pass


def load_or_create_master_key(path):
    """The master key for a local install: read it from `path` (base64 text), or
    make a fresh 32-byte key and store it there owner-readable only (0600).

    This is what runs when `BRIDGE_MASTER_KEY` is not set: the tester needs no
    configuration and the sealed blob survives restarts. The key then sits on the
    same disk as the blob, protected by file permissions only -- the same level as
    the browser profile. A KMS-held key via the env var stays the production path.
    """
    import base64
    if os.path.exists(path):
        with open(path, "rb") as f:
            raw = f.read().strip()
        try:
            key = base64.b64decode(raw, validate=True)
        except ValueError:
            key = b""
        if len(key) != 32:
            raise ValueError(f"{path} does not hold a 32-byte base64 key; delete it "
                             f"to generate a new one (old sealed blobs become unreadable)")
        return key
    key = os.urandom(32)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(base64.b64encode(key) + b"\n")
    return key
