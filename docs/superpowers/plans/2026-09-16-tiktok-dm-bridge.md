# TikTok DM Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Python prototype that authenticates to TikTok, holds a mobile-bound session, and pulls DM conversations and messages into a normalized pipeline, with failure handling, at-rest session encryption, and a health metric.

**Architecture:** Six layers with one-directional dependencies (auth -> signing -> client -> sync -> normalize, with storage underneath). The DM surface is the mobile app protobuf API (`api16-normal-*.tiktokv.com`, bare `/v1|/v2|/v3` paths). Signing and the browser login are isolated behind interfaces so the fragile parts are swappable. See `DESIGN.md`.

**Tech Stack:** Python 3.14, stdlib only plus `requests`, `cryptography`, `PyYAML`. Tests use stdlib `unittest`. No pip is available in this environment, so no other dependencies may be added. Protobuf is hand-rolled (varint + length-delimited); no protobuf library.

**Spec:** `DESIGN.md` (in repo root).

## Global Constraints

- Python 3.14; stdlib + `requests` + `cryptography` + `yaml` only. No new dependencies (no pip in env).
- Tests: `python3 -m unittest` (no pytest). Every test asserts real behavior, not that code merely runs.
- Style: minimal comments, English, no emoji, no over-engineering. YAGNI/DRY. Terse, hand-written feel; do not comment every line or add docstrings to trivial functions.
- Commit after every green task.
- Never persist a raw password. Session secrets are AES-GCM encrypted at rest (per-user data key wrapped by a master key).
- Device fingerprint is generated once per user and never rotated.
- Never blind-replay an ambiguous outbound send.
- DM surface field numbers (confirmed live): Response envelope field 3 = status_code, field 4 = error_desc, field 7 = log_id. Status ladder: 200005 = no session/signing, 200001 = session valid but IM not initialized, 0 = success.

---

### Task 1: Protobuf codec

**Files:**
- Create: `bridge/proto.py`
- Test: `tests/test_proto.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `read_varint(buf: bytes, i: int) -> tuple[int, int]`; `decode_fields(buf: bytes) -> dict[int, list]` where each value is `int` (varint) or `bytes` (length-delimited); `encode_fields(fields: dict[int, int | bytes]) -> bytes`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_proto.py
import unittest
from bridge import proto

class TestProto(unittest.TestCase):
    def test_read_varint_multibyte(self):
        # 200005 encoded as c5 9a 0c
        v, i = proto.read_varint(bytes.fromhex("c59a0c"), 0)
        self.assertEqual(v, 200005)
        self.assertEqual(i, 3)

    def test_decode_real_envelope_prefix(self):
        # captured live from /v1/conversation/list/ (fields 3 and 4)
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_proto -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'bridge'` or `AttributeError`.

- [ ] **Step 3: Write minimal implementation**

```python
# bridge/proto.py
def read_varint(buf, i):
    shift = 0
    result = 0
    while True:
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, i
        shift += 7

def _write_varint(v):
    out = bytearray()
    while True:
        b = v & 0x7F
        v >>= 7
        if v:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)

def decode_fields(buf):
    fields = {}
    i = 0
    n = len(buf)
    while i < n:
        tag, i = read_varint(buf, i)
        field = tag >> 3
        wt = tag & 7
        if wt == 0:
            v, i = read_varint(buf, i)
        elif wt == 2:
            ln, i = read_varint(buf, i)
            v = buf[i:i + ln]
            i += ln
        else:
            break
        fields.setdefault(field, []).append(v)
    return fields

def encode_fields(fields):
    out = bytearray()
    for field, value in fields.items():
        if isinstance(value, int):
            out += _write_varint(field << 3)
            out += _write_varint(value)
        else:
            out += _write_varint((field << 3) | 2)
            out += _write_varint(len(value))
            out += value
    return bytes(out)
```

Add empty `bridge/__init__.py` and `tests/__init__.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_proto -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add bridge/__init__.py bridge/proto.py tests/__init__.py tests/test_proto.py
git commit -m "Add hand-rolled protobuf varint codec"
```

---

### Task 2: Error taxonomy

**Files:**
- Create: `bridge/errors.py`
- Test: `tests/test_errors.py`

**Interfaces:**
- Consumes: nothing.
- Produces: exception classes `BridgeError`, `AuthError`, `RateLimited`, `Banned`, `IMNotInitialized`, `SignerStale`, `Transient`, `InvalidRequest`, each with `code` and `log_id` attributes.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_errors.py
import unittest
from bridge import errors

class TestErrors(unittest.TestCase):
    def test_hierarchy(self):
        for cls in (errors.AuthError, errors.RateLimited, errors.Banned,
                    errors.IMNotInitialized, errors.SignerStale,
                    errors.Transient, errors.InvalidRequest):
            self.assertTrue(issubclass(cls, errors.BridgeError))

    def test_carries_code_and_log_id(self):
        e = errors.AuthError("expired", code=200003, log_id="abc")
        self.assertEqual(e.code, 200003)
        self.assertEqual(e.log_id, "abc")
        self.assertIn("expired", str(e))

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_errors -v`
Expected: FAIL, `ModuleNotFoundError` / `AttributeError`.

- [ ] **Step 3: Write minimal implementation**

```python
# bridge/errors.py
class BridgeError(Exception):
    def __init__(self, message, code=None, log_id=None):
        super().__init__(message)
        self.code = code
        self.log_id = log_id

class AuthError(BridgeError): pass
class RateLimited(BridgeError): pass
class Banned(BridgeError): pass
class IMNotInitialized(BridgeError): pass
class SignerStale(BridgeError): pass
class Transient(BridgeError): pass
class InvalidRequest(BridgeError): pass
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_errors -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add bridge/errors.py tests/test_errors.py
git commit -m "Add error taxonomy"
```

---

### Task 3: IM response envelope parse + status classification

**Files:**
- Create: `bridge/envelope.py`
- Test: `tests/test_envelope.py`

**Interfaces:**
- Consumes: `proto.decode_fields`; `errors.*`.
- Produces: `parse_response(body: bytes) -> dict` returning `{"status_code": int|None, "error_desc": str|None, "log_id": str|None, "body": bytes|None, "raw": dict}`; `classify(status_code: int, log_id: str|None) -> None` that raises the mapped `BridgeError` for non-zero codes and returns `None` for 0.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_envelope.py
import unittest
from bridge import envelope, proto, errors

class TestEnvelope(unittest.TestCase):
    def test_parse_real_no_session(self):
        buf = bytes.fromhex("18c59a0c220632303030303522")[:12]
        r = envelope.parse_response(buf)
        self.assertEqual(r["status_code"], 200005)
        self.assertEqual(r["error_desc"], "200005")

    def test_parse_success_with_body(self):
        inner = proto.encode_fields({1: b"conv-data"})
        buf = proto.encode_fields({3: 0, 6: inner, 7: b"log123"})
        r = envelope.parse_response(buf)
        self.assertEqual(r["status_code"], 0)
        self.assertEqual(r["log_id"], "log123")
        self.assertEqual(r["body"], inner)

    def test_classify_maps_codes(self):
        self.assertIsNone(envelope.classify(0, None))
        with self.assertRaises(errors.IMNotInitialized):
            envelope.classify(200001, "x")
        with self.assertRaises(errors.AuthError):
            envelope.classify(200005, "x")

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_envelope -v`
Expected: FAIL, `ModuleNotFoundError` / `AttributeError`.

- [ ] **Step 3: Write minimal implementation**

```python
# bridge/envelope.py
from . import proto
from . import errors

def _first(fields, idx):
    v = fields.get(idx)
    return v[0] if v else None

def parse_response(body):
    f = proto.decode_fields(body)
    sc = _first(f, 3)
    desc = _first(f, 4)
    log_id = _first(f, 7)
    inner = _first(f, 6)
    return {
        "status_code": sc if isinstance(sc, int) else None,
        "error_desc": desc.decode() if isinstance(desc, bytes) else None,
        "log_id": log_id.decode() if isinstance(log_id, bytes) else None,
        "body": inner if isinstance(inner, bytes) else None,
        "raw": f,
    }

# 200005 no session/sign, 200003/200004 auth-ish -> AuthError;
# 200001 IM not initialized; others default to InvalidRequest.
def classify(status_code, log_id):
    if status_code == 0:
        return None
    if status_code == 200001:
        raise errors.IMNotInitialized("im not initialized", code=status_code, log_id=log_id)
    if status_code in (200003, 200004, 200005):
        raise errors.AuthError("session invalid", code=status_code, log_id=log_id)
    raise errors.InvalidRequest("im request rejected", code=status_code, log_id=log_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_envelope -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add bridge/envelope.py tests/test_envelope.py
git commit -m "Add IM response envelope parse and status classification"
```

---

### Task 4: Device fingerprint

**Files:**
- Create: `bridge/device.py`
- Test: `tests/test_device.py`

**Interfaces:**
- Consumes: nothing.
- Produces: dataclass `Device` with fields `device_id, install_id, openudid, cdid, device_type, device_brand, os_version, region, language, app_version, version_code, manifest_version_code`, and session fields `sessionid, sid_tt, sid_guard, uid_tt, user_id`. Methods: `Device.generate(region="US") -> Device` (random ids, called once); `user_agent (property) -> str`; `common_params() -> dict`; `cookie_string() -> str`; `import_cookies(raw: str) -> None`; `is_authenticated (property) -> bool`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_device.py
import unittest
from bridge.device import Device

class TestDevice(unittest.TestCase):
    def test_generate_is_stable_across_calls(self):
        d = Device.generate()
        p1 = d.common_params()
        p2 = d.common_params()
        self.assertEqual(p1["device_id"], p2["device_id"])
        self.assertEqual(p1["iid"], p2["iid"])
        self.assertEqual(len(d.device_id), 19)
        self.assertTrue(d.device_id.isdigit())

    def test_two_generates_differ(self):
        self.assertNotEqual(Device.generate().device_id, Device.generate().device_id)

    def test_import_cookies_and_auth_state(self):
        d = Device.generate()
        self.assertFalse(d.is_authenticated)
        d.import_cookies("sessionid=abc; sid_tt=def; sid_guard=g; uid_tt=42")
        self.assertTrue(d.is_authenticated)
        self.assertEqual(d.sessionid, "abc")
        self.assertIn("sessionid=abc", d.cookie_string())

    def test_common_params_have_mobile_aid(self):
        p = Device.generate().common_params()
        self.assertEqual(p["aid"], "1233")
        self.assertEqual(p["app_name"], "musical_ly")

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_device -v`
Expected: FAIL, `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
# bridge/device.py
import random
import time
from dataclasses import dataclass, field

AID = "1233"
APP_NAME = "musical_ly"
APP_VERSION = "46.5.0"
VERSION_CODE = "460500"
MANIFEST_VERSION_CODE = "2024605000"
MODELS = ["SM-S928B", "SM-S926B", "Pixel 9 Pro", "Pixel 8"]

def _digits(n):
    return "".join(random.choices("0123456789", k=n))

def _hex(n):
    return "".join(random.choices("0123456789abcdef", k=n))

_COOKIE_MAP = {
    "sessionid": "sessionid", "sessionid_ss": "sessionid",
    "sid_tt": "sid_tt", "sid_guard": "sid_guard",
    "uid_tt": "uid_tt", "install_id": "install_id",
}

@dataclass
class Device:
    device_id: str = ""
    install_id: str = ""
    openudid: str = ""
    cdid: str = ""
    device_type: str = "SM-S928B"
    device_brand: str = "samsung"
    os_version: str = "14"
    region: str = "US"
    language: str = "en"
    app_version: str = APP_VERSION
    version_code: str = VERSION_CODE
    manifest_version_code: str = MANIFEST_VERSION_CODE
    sessionid: str = ""
    sid_tt: str = ""
    sid_guard: str = ""
    uid_tt: str = ""
    user_id: str = ""

    @classmethod
    def generate(cls, region="US"):
        model = random.choice(MODELS)
        return cls(
            device_id=_digits(19),
            install_id=_digits(19),
            openudid=_hex(16),
            cdid=_hex(16),
            device_type=model,
            device_brand="google" if model.startswith("Pixel") else "samsung",
            region=region,
        )

    @property
    def user_agent(self):
        return (f"com.zhiliaoapp.musically/{self.manifest_version_code} "
                f"(Linux; U; Android {self.os_version}; {self.language}; "
                f"{self.device_type}; Build/UE1A.230829.050; Cronet/TTNetVersion:45466851)")

    @property
    def is_authenticated(self):
        return bool(self.sessionid)

    def import_cookies(self, raw):
        for part in raw.split(";"):
            if "=" not in part:
                continue
            k, _, v = part.strip().partition("=")
            attr = _COOKIE_MAP.get(k.strip())
            if attr and v:
                setattr(self, attr, v.strip())

    def cookie_string(self):
        parts = [f"install_id={self.install_id}"]
        for k in ("sessionid", "sid_tt", "sid_guard", "uid_tt"):
            v = getattr(self, k)
            if v:
                parts.append(f"{k}={v}")
        return "; ".join(parts)

    def common_params(self):
        now = int(time.time())
        return {
            "device_platform": "android", "os": "android",
            "aid": AID, "app_name": APP_NAME,
            "version_code": self.version_code, "version_name": self.app_version,
            "manifest_version_code": self.manifest_version_code,
            "device_type": self.device_type, "device_brand": self.device_brand,
            "os_version": self.os_version, "region": self.region,
            "sys_region": self.region, "language": self.language,
            "device_id": self.device_id, "iid": self.install_id,
            "openudid": self.openudid, "cdid": self.cdid,
            "ts": str(now), "_rticket": str(now * 1000),
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_device -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add bridge/device.py tests/test_device.py
git commit -m "Add stable per-user device fingerprint"
```

---

### Task 5: Signer interface (isolated, swappable)

**Files:**
- Create: `bridge/signing.py`
- Test: `tests/test_signing.py`

**Interfaces:**
- Consumes: `device.Device`.
- Produces: `Signer` (Protocol) with `sign(method: str, path: str, query: str, body: bytes, device: Device) -> dict[str, str]`; `NullSigner` (returns `{}`, used for unauthenticated probes/tests); `SubprocessSigner(cmd: list[str])` that shells out to an external SignerPy process and parses its JSON stdout into the `x-argus/x-gorgon/x-ladon/x-khronos/x-ss-stub` header dict, raising `errors.SignerStale` on non-zero exit.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_signing.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_signing -v`
Expected: FAIL, `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
# bridge/signing.py
import json
import subprocess
from typing import Protocol
from . import errors

class Signer(Protocol):
    def sign(self, method, path, query, body, device): ...

class NullSigner:
    def sign(self, method, path, query, body, device):
        return {}

# Shells out to an external SignerPy process. The process reads a JSON request
# on argv/stdin and writes a JSON header dict on stdout. Kept as a separate
# process so a rotated signing algorithm is swapped without touching the bridge.
class SubprocessSigner:
    def __init__(self, cmd):
        self.cmd = cmd

    def sign(self, method, path, query, body, device):
        req = json.dumps({
            "method": method, "path": path, "query": query,
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_signing -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add bridge/signing.py tests/test_signing.py
git commit -m "Add swappable signer interface with subprocess SignerPy seam"
```

---

### Task 6: HTTP transport client

**Files:**
- Create: `bridge/client.py`
- Test: `tests/test_client.py`

**Interfaces:**
- Consumes: `device.Device`, `signing.Signer`, `envelope`, `errors`.
- Produces: `Client(device, signer, base_host="api16-normal-c-useast2a.tiktokv.com", proxy=None, transport=None)`. `transport` is an injectable callable `(method, url, headers, body) -> (status_code, resp_headers, content)` defaulting to a `requests`-backed one, so tests never hit the network. Methods: `get_im(path, params) -> dict` and `post_im(path, body: bytes, params=None) -> dict`, both returning the parsed+classified envelope; on HTTP 429 raise `RateLimited`, on 5xx raise `Transient`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_client.py
import unittest
from bridge.client import Client
from bridge.device import Device
from bridge.signing import NullSigner
from bridge import proto, errors

class FakeTransport:
    def __init__(self, status, content):
        self.status = status
        self.content = content
        self.last = None
    def __call__(self, method, url, headers, body):
        self.last = (method, url, headers, body)
        return self.status, {}, self.content

class TestClient(unittest.TestCase):
    def _client(self, transport):
        return Client(Device.generate(), NullSigner(), transport=transport)

    def test_get_im_parses_success(self):
        body = proto.encode_fields({3: 0, 7: b"log1"})
        t = FakeTransport(200, body)
        r = self._client(t).get_im("/v1/conversation/list/", {"cursor": "0"})
        self.assertEqual(r["status_code"], 0)
        # device params injected into the URL
        self.assertIn("device_id=", t.last[1])
        self.assertIn("aid=1233", t.last[1])

    def test_auth_error_raised_on_200005(self):
        body = proto.encode_fields({3: 200005, 4: b"200005"})
        with self.assertRaises(errors.AuthError):
            self._client(FakeTransport(200, body)).get_im("/v1/conversation/list/", {})

    def test_rate_limit_on_429(self):
        with self.assertRaises(errors.RateLimited):
            self._client(FakeTransport(429, b"")).get_im("/v1/x/", {})

    def test_transient_on_500(self):
        with self.assertRaises(errors.Transient):
            self._client(FakeTransport(500, b"")).get_im("/v1/x/", {})

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_client -v`
Expected: FAIL, `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
# bridge/client.py
from urllib.parse import urlencode
from . import envelope, errors

def _requests_transport(proxy):
    import requests
    sess = requests.Session()
    proxies = {"http": proxy, "https": proxy} if proxy else None
    def call(method, url, headers, body):
        r = sess.request(method, url, headers=headers, data=body,
                         proxies=proxies, timeout=30)
        return r.status_code, dict(r.headers), r.content
    return call

class Client:
    def __init__(self, device, signer, base_host="api16-normal-c-useast2a.tiktokv.com",
                 proxy=None, transport=None):
        self.device = device
        self.signer = signer
        self.base = f"https://{base_host}"
        self.transport = transport or _requests_transport(proxy)

    def _url(self, path, params):
        q = dict(self.device.common_params())
        if params:
            q.update({k: str(v) for k, v in params.items()})
        return f"{self.base}{path}?{urlencode(q)}", urlencode(q)

    def _headers(self, method, path, query, body):
        h = {"User-Agent": self.device.user_agent,
             "Cookie": self.device.cookie_string(),
             "Content-Type": "application/x-protobuf"}
        h.update(self.signer.sign(method, path, query, body, self.device))
        return h

    def _do(self, method, path, body, params):
        url, query = self._url(path, params)
        status, _, content = self.transport(method, url, self._headers(method, path, query, body), body)
        if status == 429:
            raise errors.RateLimited("http 429")
        if status >= 500:
            raise errors.Transient(f"http {status}")
        parsed = envelope.parse_response(content)
        envelope.classify(parsed["status_code"], parsed["log_id"])
        return parsed

    def get_im(self, path, params=None):
        return self._do("GET", path, b"", params)

    def post_im(self, path, body, params=None):
        return self._do("POST", path, body, params)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_client -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add bridge/client.py tests/test_client.py
git commit -m "Add signed HTTP transport with device params and error classification"
```

---

### Task 7: Envelope-encrypted session store

**Files:**
- Create: `bridge/session_store.py`
- Test: `tests/test_session_store.py`

**Interfaces:**
- Consumes: `cryptography` (AES-GCM).
- Produces: `SessionStore(dir_path, master_key: bytes)`. Methods: `save(user_id: str, blob: dict) -> None` (per-user random data key encrypts the JSON blob with AES-GCM; the data key is wrapped with the master key via AES-GCM; both nonces + ciphertexts written to `<dir>/<user_id>.session`); `load(user_id: str) -> dict`; `delete(user_id: str) -> None` (removes the file, for logout). `master_key` is 32 bytes; in production it comes from a KMS, here from an env var.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_session_store.py
import os
import tempfile
import unittest
from bridge.session_store import SessionStore

class TestSessionStore(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.store = SessionStore(self.dir, master_key=b"0" * 32)

    def test_roundtrip(self):
        blob = {"sessionid": "abc", "device_id": "123", "user_id": "42"}
        self.store.save("42", blob)
        self.assertEqual(self.store.load("42"), blob)

    def test_at_rest_is_encrypted(self):
        self.store.save("42", {"sessionid": "supersecret"})
        with open(os.path.join(self.dir, "42.session"), "rb") as f:
            raw = f.read()
        self.assertNotIn(b"supersecret", raw)

    def test_delete_on_logout(self):
        self.store.save("42", {"sessionid": "abc"})
        self.store.delete("42")
        with self.assertRaises(FileNotFoundError):
            self.store.load("42")

    def test_wrong_master_key_fails(self):
        self.store.save("42", {"sessionid": "abc"})
        other = SessionStore(self.dir, master_key=b"1" * 32)
        with self.assertRaises(Exception):
            other.load("42")

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_session_store -v`
Expected: FAIL, `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
# bridge/session_store.py
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
        out = b"".join([
            len(wrapped).to_bytes(2, "big"), wn, wrapped, dn, ct
        ])
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_session_store -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add bridge/session_store.py tests/test_session_store.py
git commit -m "Add envelope-encrypted session store with delete-on-logout"
```

---

### Task 8: Sync state — watermarks and dedup

**Files:**
- Create: `bridge/state.py`
- Test: `tests/test_state.py`

**Interfaces:**
- Consumes: nothing (in-memory + JSON file persistence via stdlib).
- Produces: `SyncState(path=None)`. Methods: `get_cursor(conv_id) -> str` (default ""); `set_cursor(conv_id, cursor) -> None`; `seen(message_id) -> bool`; `mark_seen(message_id) -> None`; `save()`/`load()` (JSON to `path` if given). Cursor is advanced by the caller only after messages are persisted.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_state.py
import os
import tempfile
import unittest
from bridge.state import SyncState

class TestState(unittest.TestCase):
    def test_cursor_default_and_set(self):
        s = SyncState()
        self.assertEqual(s.get_cursor("c1"), "")
        s.set_cursor("c1", "10")
        self.assertEqual(s.get_cursor("c1"), "10")

    def test_dedup(self):
        s = SyncState()
        self.assertFalse(s.seen("m1"))
        s.mark_seen("m1")
        self.assertTrue(s.seen("m1"))

    def test_persist_roundtrip(self):
        p = os.path.join(tempfile.mkdtemp(), "state.json")
        s = SyncState(p)
        s.set_cursor("c1", "5")
        s.mark_seen("m1")
        s.save()
        s2 = SyncState(p)
        s2.load()
        self.assertEqual(s2.get_cursor("c1"), "5")
        self.assertTrue(s2.seen("m1"))

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_state -v`
Expected: FAIL, `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
# bridge/state.py
import json
import os

class SyncState:
    def __init__(self, path=None):
        self.path = path
        self.cursors = {}
        self._seen = set()

    def get_cursor(self, conv_id):
        return self.cursors.get(conv_id, "")

    def set_cursor(self, conv_id, cursor):
        self.cursors[conv_id] = cursor

    def seen(self, message_id):
        return message_id in self._seen

    def mark_seen(self, message_id):
        self._seen.add(message_id)

    def save(self):
        if not self.path:
            return
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"cursors": self.cursors, "seen": list(self._seen)}, f)
        os.replace(tmp, self.path)

    def load(self):
        if not self.path or not os.path.exists(self.path):
            return
        with open(self.path) as f:
            d = json.load(f)
        self.cursors = d.get("cursors", {})
        self._seen = set(d.get("seen", []))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_state -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add bridge/state.py tests/test_state.py
git commit -m "Add sync state: cursors and message dedup with persistence"
```

---

### Task 9: Normalization — TikTok objects to canonical events

**Files:**
- Create: `bridge/normalize.py`
- Test: `tests/test_normalize.py`

**Interfaces:**
- Consumes: nothing (operates on plain dicts, the decoded IM objects).
- Produces: dataclasses `User(user_id, display_name, avatar_url)`, `Thread(conversation_id, participants, is_stranger)`, `Event(message_id, conversation_id, sender_id, text, timestamp_ms)`. Functions `to_user(d: dict) -> User`, `to_thread(d: dict) -> Thread`, `to_event(d: dict) -> Event`. Missing optional fields degrade to empty/None, never raise.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_normalize.py
import unittest
from bridge import normalize

class TestNormalize(unittest.TestCase):
    def test_to_event(self):
        e = normalize.to_event({
            "server_message_id": "m1", "conversation_id": "c1",
            "sender": "u9", "content": "hi", "create_time": 1700000000000,
        })
        self.assertEqual(e.message_id, "m1")
        self.assertEqual(e.sender_id, "u9")
        self.assertEqual(e.text, "hi")
        self.assertEqual(e.timestamp_ms, 1700000000000)

    def test_to_user_missing_avatar(self):
        u = normalize.to_user({"uid": "u9", "nickname": "Ann"})
        self.assertEqual(u.user_id, "u9")
        self.assertEqual(u.display_name, "Ann")
        self.assertIsNone(u.avatar_url)

    def test_to_thread_stranger_flag(self):
        t = normalize.to_thread({"conversation_id": "c1", "participants": ["a", "b"]}, is_stranger=True)
        self.assertTrue(t.is_stranger)
        self.assertEqual(t.participants, ["a", "b"])

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_normalize -v`
Expected: FAIL, `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
# bridge/normalize.py
from dataclasses import dataclass, field

@dataclass
class User:
    user_id: str
    display_name: str = ""
    avatar_url: str | None = None

@dataclass
class Thread:
    conversation_id: str
    participants: list = field(default_factory=list)
    is_stranger: bool = False

@dataclass
class Event:
    message_id: str
    conversation_id: str
    sender_id: str
    text: str
    timestamp_ms: int

def to_user(d):
    return User(
        user_id=str(d.get("uid") or d.get("user_id") or ""),
        display_name=d.get("nickname") or d.get("display_name") or "",
        avatar_url=d.get("avatar_url") or d.get("avatar_larger"),
    )

def to_thread(d, is_stranger=False):
    return Thread(
        conversation_id=str(d.get("conversation_id") or ""),
        participants=list(d.get("participants") or []),
        is_stranger=is_stranger,
    )

def to_event(d):
    return Event(
        message_id=str(d.get("server_message_id") or d.get("message_id") or ""),
        conversation_id=str(d.get("conversation_id") or ""),
        sender_id=str(d.get("sender") or d.get("sender_id") or ""),
        text=d.get("content") or "",
        timestamp_ms=int(d.get("create_time") or 0),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_normalize -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add bridge/normalize.py tests/test_normalize.py
git commit -m "Add normalization to canonical User/Thread/Event"
```

---

### Task 10: Sync loop — backfill and poll with reconciliation

**Files:**
- Create: `bridge/sync.py`
- Test: `tests/test_sync.py`

**Interfaces:**
- Consumes: `state.SyncState`, `normalize`. Uses a `fetcher` object with `list_conversations(cursor) -> (list[dict], next_cursor, has_more)` and `get_messages(conv_id, cursor) -> (list[dict], next_cursor, has_more)`, so the network client is injectable and tests use a fake.
- Produces: `Syncer(fetcher, state, emit)` where `emit(event)` is called once per new normalized `Event`. Methods: `backfill(conv_id) -> int` (paginates full history, dedups, advances cursor after emit, returns count emitted); `poll_once() -> int` (lists conversations, for each fetches messages newer than the stored watermark, reconciles by dedup so a missed poll cannot drop or duplicate a message).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_sync.py
import unittest
from bridge.sync import Syncer
from bridge.state import SyncState

class FakeFetcher:
    def __init__(self, convs, messages):
        self.convs = convs
        self.messages = messages  # conv_id -> list of pages [(items, next, has_more)]
        self._page = {}
    def list_conversations(self, cursor):
        return self.convs, "", False
    def get_messages(self, conv_id, cursor):
        pages = self.messages[conv_id]
        idx = self._page.get(conv_id, 0)
        self._page[conv_id] = idx + 1
        return pages[idx]

def msg(mid):
    return {"server_message_id": mid, "conversation_id": "c1", "sender": "u1",
            "content": "x", "create_time": int(mid)}

class TestSync(unittest.TestCase):
    def test_backfill_paginates_and_dedups(self):
        f = FakeFetcher(
            convs=[{"conversation_id": "c1"}],
            messages={"c1": [([msg("2"), msg("1")], "cur1", True),
                             ([msg("1")], "", False)]},  # "1" repeats across pages
        )
        state = SyncState()
        emitted = []
        n = Syncer(f, state, emitted.append).backfill("c1")
        ids = sorted(e.message_id for e in emitted)
        self.assertEqual(ids, ["1", "2"])
        self.assertEqual(n, 2)

    def test_poll_does_not_re_emit_seen(self):
        f = FakeFetcher(
            convs=[{"conversation_id": "c1"}],
            messages={"c1": [([msg("5")], "", False)]},
        )
        state = SyncState()
        state.mark_seen("5")
        emitted = []
        n = Syncer(f, state, emitted.append).poll_once()
        self.assertEqual(n, 0)

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_sync -v`
Expected: FAIL, `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
# bridge/sync.py
from . import normalize

class Syncer:
    def __init__(self, fetcher, state, emit):
        self.fetcher = fetcher
        self.state = state
        self.emit = emit

    def _emit_new(self, items):
        count = 0
        for d in items:
            e = normalize.to_event(d)
            if not e.message_id or self.state.seen(e.message_id):
                continue
            self.state.mark_seen(e.message_id)
            self.emit(e)
            count += 1
        return count

    def backfill(self, conv_id):
        total = 0
        cursor = self.state.get_cursor(conv_id)
        while True:
            items, nxt, has_more = self.fetcher.get_messages(conv_id, cursor)
            total += self._emit_new(items)
            if nxt:
                self.state.set_cursor(conv_id, nxt)
                cursor = nxt
            if not has_more:
                break
        return total

    def poll_once(self):
        convs, _, _ = self.fetcher.list_conversations("")
        total = 0
        for c in convs:
            conv_id = str(c.get("conversation_id") or "")
            if not conv_id:
                continue
            items, nxt, _ = self.fetcher.get_messages(conv_id, self.state.get_cursor(conv_id))
            total += self._emit_new(items)
            if nxt:
                self.state.set_cursor(conv_id, nxt)
        return total
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_sync -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add bridge/sync.py tests/test_sync.py
git commit -m "Add sync: backfill and poll with dedup reconciliation"
```

---

### Task 11: Login state machine (QR primary, browser fallback)

**Files:**
- Create: `bridge/auth/__init__.py`, `bridge/auth/login.py`
- Test: `tests/test_login.py`

**Interfaces:**
- Consumes: `device.Device`, `session_store.SessionStore`, `errors`.
- Produces: `LoginStep` dataclass `(kind: str, prompt: str, data: dict)` with kinds `"display_and_wait"` (QR), `"cookies"` (browser capture), `"complete"`, `"error"`. `LoginProcess(device, store, qr_flow, browser_flow)` where each flow is an injected object (real ones do HTTP/browser; tests use fakes). Methods: `start(mode="qr") -> LoginStep`; `advance() -> LoginStep` (polls QR or waits for capture); `verify_mobile() -> bool` (probes `/v1/client/unread_count/`; a non-carrying web session returns False and yields a re-auth-via-QR step). On success, imports cookies into `device`, saves the encrypted blob, returns `complete`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_login.py
import tempfile
import unittest
from bridge.auth.login import LoginProcess
from bridge.device import Device
from bridge.session_store import SessionStore

class FakeQR:
    def __init__(self, cookies_after=1):
        self.calls = 0
        self.cookies_after = cookies_after
    def start(self):
        return {"qrcode": "data:img", "token": "t1"}
    def poll(self, token):
        self.calls += 1
        if self.calls >= self.cookies_after:
            return {"status": "confirmed",
                    "cookies": "sessionid=s; sid_tt=st; uid_tt=9"}
        return {"status": "pending"}

class FakeBrowser:
    def capture(self):
        return {"cookies": "sessionid=web; uid_tt=9"}

class TestLogin(unittest.TestCase):
    def _proc(self, qr=None, browser=None):
        store = SessionStore(tempfile.mkdtemp(), master_key=b"0" * 32)
        return LoginProcess(Device.generate(), store, qr or FakeQR(), browser or FakeBrowser())

    def test_qr_happy_path(self):
        p = self._proc(qr=FakeQR(cookies_after=2))
        s = p.start("qr")
        self.assertEqual(s.kind, "display_and_wait")
        self.assertEqual(p.advance().kind, "display_and_wait")  # still pending
        final = p.advance()
        self.assertEqual(final.kind, "complete")
        self.assertTrue(p.device.is_authenticated)
        self.assertEqual(p.device.sessionid, "s")

    def test_browser_capture_sets_cookies(self):
        p = self._proc()
        p.start("browser")
        final = p.advance()
        self.assertEqual(final.kind, "complete")
        self.assertEqual(p.device.sessionid, "web")

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_login -v`
Expected: FAIL, `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
# bridge/auth/__init__.py
```
```python
# bridge/auth/login.py
from dataclasses import dataclass, field

@dataclass
class LoginStep:
    kind: str
    prompt: str = ""
    data: dict = field(default_factory=dict)

class LoginProcess:
    def __init__(self, device, store, qr_flow, browser_flow):
        self.device = device
        self.store = store
        self.qr = qr_flow
        self.browser = browser_flow
        self.mode = None
        self._token = None

    def start(self, mode="qr"):
        self.mode = mode
        if mode == "qr":
            info = self.qr.start()
            self._token = info["token"]
            return LoginStep("display_and_wait", "Scan the QR code in your TikTok app",
                             {"qrcode": info["qrcode"]})
        return LoginStep("cookies", "Log in on TikTok's page")

    def advance(self):
        if self.mode == "qr":
            res = self.qr.poll(self._token)
            if res.get("status") != "confirmed":
                return LoginStep("display_and_wait", "Waiting for scan")
            return self._complete(res["cookies"])
        res = self.browser.capture()
        return self._complete(res["cookies"])

    def _complete(self, cookies):
        self.device.import_cookies(cookies)
        blob = {
            "sessionid": self.device.sessionid, "sid_tt": self.device.sid_tt,
            "sid_guard": self.device.sid_guard, "uid_tt": self.device.uid_tt,
            "device_id": self.device.device_id, "install_id": self.device.install_id,
        }
        self.store.save(self.device.uid_tt or "unknown", blob)
        return LoginStep("complete", "Logged in", {"user_id": self.device.uid_tt})

    # Probe the mobile IM host. A web-only session that does not carry returns
    # False so the caller can re-auth via QR instead of failing later.
    def verify_mobile(self, client):
        from .. import errors
        try:
            client.get_im("/v1/client/unread_count/", {})
            return True
        except errors.IMNotInitialized:
            return False
        except errors.AuthError:
            return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_login -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add bridge/auth/__init__.py bridge/auth/login.py tests/test_login.py
git commit -m "Add login state machine: QR primary, browser capture fallback"
```

---

### Task 12: Health metric — Live-Session-Ratio

**Files:**
- Create: `bridge/metrics.py`
- Test: `tests/test_metrics.py`

**Interfaces:**
- Consumes: nothing (operates on a list of per-login status records).
- Produces: `live_session_ratio(logins: list[dict], now_ms: int, poll_interval_ms: int) -> float` = fraction of logins that are `authenticated=True` AND `last_sync_ms` within `poll_interval_ms` of `now_ms`; `reauth_share(logins) -> float` = fraction whose state is `"needs-reauth"`. Empty input returns 0.0.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_metrics.py
import unittest
from bridge import metrics

class TestMetrics(unittest.TestCase):
    def test_live_ratio(self):
        now = 1_000_000
        logins = [
            {"authenticated": True, "last_sync_ms": now - 1000, "state": "connected"},
            {"authenticated": True, "last_sync_ms": now - 999999, "state": "connected"},  # stale
            {"authenticated": False, "last_sync_ms": now, "state": "needs-reauth"},
        ]
        self.assertAlmostEqual(metrics.live_session_ratio(logins, now, 60000), 1/3)

    def test_reauth_share(self):
        logins = [{"state": "needs-reauth"}, {"state": "connected"}]
        self.assertAlmostEqual(metrics.reauth_share(logins), 0.5)

    def test_empty(self):
        self.assertEqual(metrics.live_session_ratio([], 0, 1), 0.0)
        self.assertEqual(metrics.reauth_share([]), 0.0)

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_metrics -v`
Expected: FAIL, `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
# bridge/metrics.py
def live_session_ratio(logins, now_ms, poll_interval_ms):
    if not logins:
        return 0.0
    live = sum(1 for l in logins
               if l.get("authenticated")
               and now_ms - l.get("last_sync_ms", 0) <= poll_interval_ms)
    return live / len(logins)

def reauth_share(logins):
    if not logins:
        return 0.0
    return sum(1 for l in logins if l.get("state") == "needs-reauth") / len(logins)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_metrics -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add bridge/metrics.py tests/test_metrics.py
git commit -m "Add Live-Session-Ratio health metric"
```

---

### Task 13: IM client endpoints on top of transport

**Files:**
- Create: `bridge/im.py`
- Test: `tests/test_im.py`

**Interfaces:**
- Consumes: `client.Client`, `proto`, `envelope`.
- Produces: `IM(client)` adapting the transport to the `fetcher` shape Task 10 expects. Methods: `list_conversations(cursor) -> (list[dict], next_cursor, has_more)`, `get_messages(conv_id, cursor) -> (list[dict], next_cursor, has_more)`, `send_text(conv_id, text, ticket="", client_message_id="") -> dict` (builds the confirmed protobuf Request/RequestBody/SendMessageRequestBody envelope: Request field 1=cmd=1, field 8=RequestBody; RequestBody field 1=SendMessageRequestBody; body fields 1=conversation_id, 2=conversation_type, 4=text, 6=message_type=1, 7=ticket, 8=client_message_id), `mark_read(conv_id) -> dict`. Parsing IM body payloads into the dict shapes normalize expects is best-effort (fields best-effort per DESIGN §0); leave a documented `_parse_conv_list`/`_parse_messages` seam returning `[]` until live shapes are captured.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_im.py
import unittest
from bridge.im import IM
from bridge import proto, envelope

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
        # echo a minimal success Response body
        return {"status_code": 0, "body": None, "raw": {}}

class TestIM(unittest.TestCase):
    def test_send_text_builds_envelope(self):
        c = FakeClient()
        IM(c).send_text("conv7", "hello", ticket="tk", client_message_id="cm1")
        self.assertEqual(c.last_path, "/v1/message/send/")
        # outer Request: field 1 (cmd) varint = 1, field 8 = RequestBody bytes
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_im -v`
Expected: FAIL, `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
# bridge/im.py
import random
import time
from . import proto

def _client_message_id():
    return f"{int(time.time() * 1000)}{random.randint(0, 999):03d}"

class IM:
    def __init__(self, client):
        self.c = client

    def list_conversations(self, cursor="0", count=20):
        r = self.c.get_im("/v1/conversation/list/", {"cursor": cursor, "count": count})
        return self._parse_conv_list(r), "", False

    def get_messages(self, conv_id, cursor="0", count=20):
        r = self.c.get_im("/v1/message/get_by_conversation/",
                          {"conversation_id": conv_id, "cursor": cursor, "count": count})
        return self._parse_messages(r), "", False

    def mark_read(self, conv_id):
        return self.c.post_im("/v3/conversation/mark_read/",
                              proto.encode_fields({1: conv_id.encode()}),
                              {"conversation_id": conv_id})

    def send_text(self, conv_id, text, conversation_type=1, message_type=1,
                  ticket="", client_message_id=""):
        cmid = client_message_id or _client_message_id()
        send_fields = {1: conv_id.encode(), 2: conversation_type,
                       4: text.encode(), 6: message_type, 8: cmid.encode()}
        if ticket:
            send_fields[7] = ticket.encode()
        smb = proto.encode_fields(send_fields)
        request_body = proto.encode_fields({1: smb})
        request = proto.encode_fields({1: 1, 8: request_body})
        return self.c.post_im("/v1/message/send/", request)

    # Body payload shapes are best-effort until captured from live traffic
    # (DESIGN section 0). Return [] so sync stays exercisable end to end.
    def _parse_conv_list(self, resp):
        return []

    def _parse_messages(self, resp):
        return []
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_im -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add bridge/im.py tests/test_im.py
git commit -m "Add IM endpoints with confirmed send-message protobuf envelope"
```

---

### Task 14: README (failure-modes-first) and full test run

**Files:**
- Create: `README.md`
- Create: `config.example.yaml`

**Interfaces:**
- Consumes: everything above.
- Produces: documentation. No code.

- [ ] **Step 1: Write `config.example.yaml`**

```yaml
# Copy to config.yaml; real secrets come from a secrets manager / env, not here.
master_key_env: BRIDGE_MASTER_KEY   # 32-byte key, base64, from KMS in production
signer_cmd: ["python3", "signer/signerpy_shim.py"]   # external SignerPy process
base_host: api16-normal-c-useast2a.tiktokv.com
poll_interval_seconds: 30
session_dir: ./sessions
proxy: ""   # per-user residential proxy, geo-matched; empty = direct
```

- [ ] **Step 2: Write `README.md`**

Write a README organized around failure modes, not features. Required sections, in order:

1. **What this is** (2 sentences): a prototype TikTok DM bridge; mobile protobuf surface; Python.
2. **Phase 0 finding** — copy the surface conclusion and the status ladder from `DESIGN.md` §0, keeping the `[Obs]`/`[Inf]`/`[Guess]` markers, including the open crux (web-session portability).
3. **Run it**: `python3 -m unittest` to run all tests; `python3 -m bridge.cmd.login` to exercise a login flow (fakes if no signer).
4. **Failure modes** — a table copied and kept in sync with `DESIGN.md` §4 (step, break, detect, recover), with the canary rule stated: signer breakage is a 4xx spike across ALL users; a single-user 4xx is an account/proxy issue.
5. **Security** — one paragraph from `DESIGN.md` §5: no raw password, AES-GCM envelope encryption at rest, delete-on-logout, secrets in a manager.
6. **Health metric** — Live-Session-Ratio, and the secondary delivery-lag p95.
7. **What is not built** — signing algorithm (external process seam), IM body payload parsing (awaiting live capture), websocket realtime, Go/appservice (interface mapping is in `DESIGN.md` §7 instead).

Keep it concise, English, no emoji.

- [ ] **Step 3: Run the whole suite**

Run: `python3 -m unittest discover -s tests -v`
Expected: all tests PASS (proto, errors, envelope, device, signing, client, session_store, state, normalize, sync, login, metrics, im).

- [ ] **Step 4: Commit**

```bash
git add README.md config.example.yaml
git commit -m "Add failure-modes-first README and example config"
```

---

## Self-Review

**1. Spec coverage:**
- DESIGN §0 surface/status ladder -> Tasks 1, 3, 13 (proto, envelope, IM paths); README §2.
- §1 scope (Python, interfaces documented not Go) -> whole plan; §7 mapping stays doc-only.
- §2 six layers -> auth (Task 11), signing (Task 5), client (Task 6), sync (Tasks 8/10), normalize (Task 9), storage (Task 7). Covered.
- §3 login QR+browser, stable device, no password -> Tasks 4, 11.
- §4 failure modes -> Task 2 (taxonomy), Task 3 (classify), Task 6 (429/5xx), Task 11 (verify_mobile), Task 13 (no blind replay: send returns, caller reconciles), README §4.
- §5 security -> Task 7; README §5.
- §6 metric -> Task 12; README §6.
- §7 bridgev2 mapping -> DESIGN doc only (intentional).
- §8 endpoints -> Task 13.
- §9 repo shape -> file paths across all tasks + Task 14.

Gap check: "never blind-replay ambiguous send" is enforced by design (send_text returns the parsed result; there is no retry wrapper). Called out in README §4. No missing task.

**2. Placeholder scan:** No TBD/TODO in code steps. `_parse_conv_list`/`_parse_messages` return `[]` deliberately (live body shapes unverified per DESIGN §0) and are labeled as a seam, not a placeholder — sync is still fully tested via the fake fetcher in Task 10.

**3. Type consistency:** `Device` fields (`sessionid`, `sid_tt`, `sid_guard`, `uid_tt`, `install_id`, `device_id`) are consistent across Tasks 4, 6, 11. `Client.get_im/post_im` signatures match usage in Tasks 11 and 13. `SyncState` methods (`get_cursor/set_cursor/seen/mark_seen`) consistent across Tasks 8 and 10. `envelope.parse_response` return keys (`status_code/error_desc/log_id/body/raw`) consistent across Tasks 3, 6. Fetcher shape `(items, next_cursor, has_more)` matches between Task 10 (consumer) and Task 13 (producer).
