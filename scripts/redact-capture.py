#!/usr/bin/env python3
"""Turn a raw logged-in capture into redacted test fixtures.

  .venv/bin/python scripts/redact-capture.py 'browser-data/<name>/capture-*.jsonl'

Reads the raw capture (which contains real secrets and third-party PII), strips
every secret, pseudonymizes user ids / handles / display names with a STABLE map
(same-length numeric ids whose varint encoding is also the same length, so protobuf
bodies stay valid byte for byte), and writes one fixture per DM-relevant `kind` into
tests/fixtures/web/ plus a manifest.json. Hand-made fixtures already listed in the
manifest are kept.

Redacted, in JSON text and inside protobuf / websocket bodies alike: cookies,
msToken, sessionid*, sid_*, ttwid, uid_tt, verifyFp, device_id, X-Bogus / X-Gnarly /
_signature, phone / email, avatar-URL signatures and object hashes, profile bios.
Message text is kept unless --drop-text. Nothing written here may contain a live
secret -- tests/test_redact.py checks the committed fixtures, including the decoded
protobuf bodies -- and the raw capture stays gitignored.
"""
import argparse
import base64
import glob
import hashlib
import json
import os
import re
import sys
import time
from urllib.parse import parse_qsl, urlsplit

FIX_DIR = os.path.join("tests", "fixtures", "web")

# query-string parameters that are per-session or per-request secrets
SECRET_QS = ("msToken", "verifyFp", "X-Bogus", "X-Gnarly", "X-Dynosaur",
             "_signature", "a_bogus", "x-signature", "refresh_token", "device_id",
             "ttwid", "odin_tt")
# JSON keys whose value is always blanked
SECRET_KEYS = {"sessionid", "sessionid_ss", "sid_tt", "sid_guard", "sid_ucp_v1",
               "ssid_ucp_v1", "uid_tt", "uid_tt_ss", "mstoken", "ttwid", "verifyfp",
               "s_v_web_id", "odin_tt", "tt_csrf_token", "tt_chain_token", "token",
               "access_key", "cmpl_token", "d_ticket", "multi_sids", "csrftoken",
               "device_id", "email", "phone", "mobile"}
# protobuf header entries {1: key, 2: value} whose value is blanked in place
PB_SECRET_KEYS = ("device_id", "verifyFp", "Web-Sdk-Ms-Token", "msToken", "ttwid",
                  "odin_tt", "sessionid", "sid_tt", "sid_guard", "uid_tt",
                  "tt_csrf_token", "x-tt-token")
ID_KEYS = ("uid", "user_id", "user_id_str", "sender", "sender_id", "to_user_id",
           "from_user_id", "conversation_id", "owner", "recipient_id")
HANDLE_KEYS = ("unique_id", "handle", "username", "remark_name")
NAME_KEYS = ("nickname", "nick_name", "display_name")
MIN_SECRET_LEN = 8          # never blanket-replace a value shorter than this

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
BIGID_RE = re.compile(r"^\d{6,}$")
SIG_IN_URL = re.compile(r"([?&])(" + "|".join(SECRET_QS) + r")=[^&\"']*", re.I)
HEX32_RE = re.compile(r"(?<![0-9a-fA-F])([0-9a-f]{32})(?![0-9a-fA-F])")


# ---- protobuf helpers (local, so the script runs without the package) --------

def _read_varint(buf, i):
    shift, result = 0, 0
    while True:
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, i
        shift += 7


def _varint(v):
    out = bytearray()
    while True:
        b = v & 0x7F
        v >>= 7
        if v:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def pb_header_values(raw, key):
    """Yield (start, end) byte offsets of the value in every protobuf header entry
    shaped {1: key, 2: value} -- the `f15` request metadata TikTok's web IM sends."""
    kb = key.encode()
    pat = b"\x0a" + _varint(len(kb)) + kb + b"\x12"
    i = 0
    while True:
        j = raw.find(pat, i)
        if j < 0:
            return
        try:
            ln, p = _read_varint(raw, j + len(pat))
        except IndexError:
            return
        yield p, p + ln
        i = p + ln


def _filler(value):
    """Same-length stand-in that is obviously not a secret."""
    n = len(value)
    if value.isdigit():
        return "0" * n
    return ("REDACTED" + "x" * n)[:n]


def _fake_hash(h):
    return hashlib.sha256(("avatar:" + h).encode()).hexdigest()[:32]


class Redactor:
    """Stable pseudonymizer. Same real value -> same fake value, same length."""

    def __init__(self, keep_text=True):
        self.keep_text = keep_text
        self.ids = {}
        self.handles = {}
        self.secuids = {}
        self.nicks = {}
        self.secrets = {}       # real secret value -> same-length filler
        self._n = 0

    # -- collection ----------------------------------------------------------

    def collect_secrets(self, records):
        """Remember every secret query-string value seen in the capture so a bare
        copy of it (inside a protobuf body, a websocket frame, a URL) is blanked."""
        for rec in records:
            url = rec.get("url") or ""
            if "?" not in url:
                continue
            for k, v in parse_qsl(urlsplit(url).query, keep_blank_values=False):
                if k in SECRET_QS and len(v) >= MIN_SECRET_LEN:
                    self.secrets.setdefault(v, _filler(v))

    def _remember_secret(self, value):
        if len(value) >= MIN_SECRET_LEN:
            self.secrets.setdefault(value, _filler(value))

    # -- pseudonyms ----------------------------------------------------------

    def fake_id(self, real):
        real = str(real)
        if real in self.ids:
            return self.ids[real]
        self._n += 1
        tail = str(self._n).rjust(4, "0")
        n = len(real)
        if n <= len(tail):
            fake = tail[-n:]
        else:
            # keep the decimal length AND the varint length, so the fake can stand in
            # for the real id inside protobuf bodies without re-encoding anything
            target = len(_varint(int(real)))
            fake = None
            for lead in "987654321":
                cand = lead + "9" * (n - 1 - len(tail)) + tail
                if len(_varint(int(cand))) == target:
                    fake = cand
                    break
            fake = fake or "9" * (n - len(tail)) + tail
        if fake == real:
            return self.fake_id(real)      # astronomically unlikely; bump the counter
        self.ids[real] = fake
        return fake

    def fake_handle(self, real):
        return self.handles.setdefault(str(real), f"user_fake{len(self.handles) + 1}")

    def fake_nick(self, real):
        return self.nicks.setdefault(str(real), f"Contact {len(self.nicks) + 1}")

    def fake_secuid(self, real):
        real = str(real)
        if real not in self.secuids:
            n = str(len(self.secuids) + 1).rjust(4, "0")
            self.secuids[real] = ("SEC_" + "F" * max(0, len(real) - 8) + n)[:len(real)] or "SEC_FAKE"
        return self.secuids[real]

    # -- scrubbing -----------------------------------------------------------

    def scrub_str(self, s):
        s = SIG_IN_URL.sub(lambda m: f"{m.group(1)}{m.group(2)}=REDACTED", s)
        s = EMAIL_RE.sub("redacted@example.com", s)
        for real, fake in self.secrets.items():
            s = s.replace(real, fake)
        if "tos-" in s or "tiktokcdn" in s:
            s = HEX32_RE.sub(lambda m: _fake_hash(m.group(1)), s)
        return s

    def walk(self, obj, key=None):
        if isinstance(obj, dict):
            out = {}
            for k, v in obj.items():
                lk = str(k).lower()
                if lk in SECRET_KEYS:
                    out[k] = "REDACTED"
                    continue
                out[k] = self.walk(v, lk)
            return out
        if isinstance(obj, list):
            return [self.walk(v, key) for v in obj]
        if isinstance(obj, str):
            if key in ID_KEYS and BIGID_RE.match(obj):
                return self.fake_id(obj)
            if key in HANDLE_KEYS:
                return self.fake_handle(obj) if obj else obj
            if key in NAME_KEYS and obj:
                return self.fake_nick(obj)
            if key in ("sec_uid", "secuid"):
                return self.fake_secuid(obj) if obj else obj
            if key in ("signature", "bio"):
                return ""                       # third-party profile text
            if key in ("content", "text") and not self.keep_text:
                return ""
            return self.scrub_str(obj)
        if isinstance(obj, int) and key in ID_KEYS and len(str(obj)) >= 6:
            return int(self.fake_id(obj))
        return obj

    def scrub_bytes(self, raw):
        """Redact a protobuf body or websocket frame in place, byte-length preserving:
        header entries, every remembered secret, and every mapped id (as ASCII and as a
        varint of the same width)."""
        buf = bytearray(raw)
        for key in PB_SECRET_KEYS:
            for s, e in list(pb_header_values(bytes(buf), key)):
                value = bytes(buf[s:e]).decode("utf-8", "replace")
                self._remember_secret(value)
                buf[s:e] = _filler(value).encode()
        raw = bytes(buf)
        for real, fake in self.secrets.items():
            raw = raw.replace(real.encode(), fake.encode())
        for real, fake in self.ids.items():
            raw = raw.replace(real.encode(), fake.encode())
            rv, fv = _varint(int(real)), _varint(int(fake))
            if len(rv) == len(fv):
                raw = raw.replace(rv, fv)
        return raw


# ---- capture -> fixtures ----------------------------------------------------

def _match_kind(rec):
    if rec.get("kind") == "login_success":
        return "login_success"
    if rec.get("kind") == "ws_out":
        return "ws_outbound"
    if rec.get("kind") == "ws_in":
        return "ws_frontier_sync"
    if rec.get("kind") != "response":
        return None
    url = rec.get("url", "")
    path = urlsplit(url).netloc + urlsplit(url).path
    if "spotlight/relation" in path:
        return "contacts"
    if path.endswith("/tiktok/v1/im/user/profile/"):
        return "profile_other"
    if path.endswith("/passport/token/beat/web/"):
        return "session_check"
    if "get_by_user_init" in path:
        return "conv_list"
    if "get_by_user_combo" in path or "get_message_by_conversation" in path:
        return "messages_page"
    return None


def _good(kind, rec):
    # prefer a non-empty payload for each kind (an empty inbox produces empties too)
    if kind == "contacts":
        try:
            return bool(json.loads(rec["body_text"]).get("followings"))
        except Exception:
            return False
    if kind == "profile_other":
        try:
            return bool(json.loads(rec["body_text"]).get("users"))
        except Exception:
            return False
    return True


def _fixture_from(kind, rec, red):
    url = rec.get("url", "")
    ent = {"kind": kind, "url_pattern": urlsplit(url).path or url[:40],
           "method": rec.get("method", "GET"), "status": rec.get("status"),
           "captured_at": rec.get("ts"), "redacted": True, "notes": ""}
    if kind in ("ws_outbound", "ws_frontier_sync"):
        ent["method"] = "WS"
        ent["url_pattern"] = "wss://im-ws.tiktok.com/ws/v2"
        ent["frame_b64"] = base64.b64encode(
            red.scrub_bytes(base64.b64decode(rec["data_b64"]))).decode()
        ent["notes"] = ("frontier pbbp2 frame; sync/cursor only (empty inbox, no DM "
                        "payload captured)" if kind == "ws_frontier_sync" else
                        "frontier subscribe/hello frame")
        return ent
    if kind == "login_success":
        ent["notes"] = "logged-in marker; sessionid cookie present (redacted)"
        ent["url_pattern"] = "/login"
        return ent
    body = rec.get("body_text")
    if body is not None:
        try:
            ent["response"] = red.walk(json.loads(body))
        except ValueError:
            ent["response_text"] = red.scrub_str(body)[:2000]
    elif rec.get("body_b64"):
        ent["response_b64"] = base64.b64encode(
            red.scrub_bytes(base64.b64decode(rec["body_b64"]))).decode()
        ent["notes"] = "protobuf body (im-api); ids pseudonymized, request metadata blanked"
    if rec.get("request_body_b64"):
        ent["request_body_b64"] = base64.b64encode(
            red.scrub_bytes(base64.b64decode(rec["request_body_b64"]))).decode()
    return ent


def load_records(paths):
    records = []
    for path in paths:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                records.append(json.loads(line))
    return records


def build_redactor(records, keep_text=True):
    """A Redactor whose secret list and id map cover the whole capture. Walking every
    JSON body first makes the pseudonyms stable across fixtures."""
    red = Redactor(keep_text=keep_text)
    red.collect_secrets(records)
    for rec in records:
        if rec.get("kind") == "response" and rec.get("body_text"):
            try:
                red.walk(json.loads(rec["body_text"]))
            except ValueError:
                pass
    return red


def select_records(records):
    chosen = {}
    for rec in records:
        kind = _match_kind(rec)
        if not kind or kind in chosen or not _good(kind, rec):
            continue
        chosen[kind] = rec
    return chosen


def write_fixtures(chosen, red, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    entries = []
    for kind, rec in chosen.items():
        ent = _fixture_from(kind, rec, red)
        fname = f"{kind}.json"
        with open(os.path.join(out_dir, fname), "w", encoding="utf-8") as f:
            json.dump(ent, f, indent=2, ensure_ascii=False)
        entries.append({k: ent[k] for k in
                        ("kind", "url_pattern", "method", "captured_at", "redacted", "notes")}
                       | {"file": fname})
    return entries


def update_manifest(out_dir, entries, generated_from):
    """Rewrite manifest.json; entries for files not produced by this run (hand-made
    or synthetic fixtures) are kept as they are."""
    path = os.path.join(out_dir, "manifest.json")
    kept = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            old = json.load(f)
        new_files = {e["file"] for e in entries}
        kept = [e for e in old.get("fixtures", []) if e.get("file") not in new_files]
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"generated_from": generated_from, "fixtures": entries + kept},
                  f, indent=2)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    ap.add_argument("captures", nargs="+", help="capture-*.jsonl files or globs")
    ap.add_argument("--drop-text", action="store_true", help="blank message text too")
    ap.add_argument("--out", default=FIX_DIR)
    a = ap.parse_args(argv)

    paths = []
    for pat in a.captures:
        paths.extend(sorted(glob.glob(pat)) or [pat])
    records = load_records(paths)
    red = build_redactor(records, keep_text=not a.drop_text)
    entries = write_fixtures(select_records(records), red, a.out)
    stamps = [r["ts"] for r in records if isinstance(r.get("ts"), (int, float))]
    day = time.strftime("%Y-%m-%d", time.gmtime(min(stamps))) if stamps else "unknown date"
    update_manifest(a.out, entries,
                    f"a logged-in web capture ({day}), redacted with scripts/redact-capture.py")
    print(f"wrote {len(entries)} fixtures to {a.out}")
    for m in entries:
        print(f"  {m['kind']:16s} {m['method']:4s} {m['url_pattern']}")


if __name__ == "__main__":
    main(sys.argv[1:])
