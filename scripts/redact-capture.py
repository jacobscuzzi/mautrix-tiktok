#!/usr/bin/env python3
"""Turn a raw logged-in capture into redacted test fixtures.

  python scripts/redact-capture.py browser-data/jakob/capture-*.jsonl

Reads the raw capture (which contains real secrets and third-party PII), strips
every secret, pseudonymizes user ids / handles / display names with a STABLE map
(same-length numeric ids so protobuf stays valid), and writes one fixture per
DM-relevant `kind` into tests/fixtures/web/ with a manifest.json.

Redacted: cookies, msToken, sessionid*, sid_*, ttwid, uid_tt, verifyFp, X-Bogus/
X-Gnarly/X-Dynosaur/_signature, phone/email, avatar-URL signatures. Message text
is kept unless --drop-text. Nothing written here should contain a live secret;
the raw capture stays gitignored.
"""
import argparse
import base64
import glob
import json
import os
import re
import sys
from urllib.parse import urlsplit

FIX_DIR = os.path.join("tests", "fixtures", "web")

SECRET_QS = ("msToken", "verifyFp", "X-Bogus", "X-Gnarly", "X-Dynosaur",
             "_signature", "a_bogus", "x-signature", "refresh_token")
SECRET_KEYS = {"sessionid", "sessionid_ss", "sid_tt", "sid_guard", "sid_ucp_v1",
               "ssid_ucp_v1", "uid_tt", "uid_tt_ss", "mstoken", "ttwid", "verifyfp",
               "s_v_web_id", "odin_tt", "tt_csrf_token", "tt_chain_token", "token",
               "access_key", "cmpl_token", "d_ticket", "multi_sids", "csrftoken"}
SECRET_HEADERS = {"cookie", "set-cookie", "authorization", "x-tt-token",
                  "x-secsdk-csrf-token", "x-secsdk-csrf-request"}
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE_RE = re.compile(r"(?<!\d)\+?\d[\d ()-]{7,}\d(?!\d)")
BIGID_RE = re.compile(r"^\d{15,}$")
SIG_IN_URL = re.compile(r"([?&])(" + "|".join(SECRET_QS) + r")=[^&\"']*", re.I)


class Redactor:
    """Stable pseudonymizer. Same real id -> same fake id, same length."""

    def __init__(self, keep_text=True):
        self.keep_text = keep_text
        self.ids = {}
        self.handles = {}
        self.secuids = {}
        self._n = 0

    def fake_id(self, real):
        real = str(real)
        if real in self.ids:
            return self.ids[real]
        self._n += 1
        # keep the length so protobuf varint-prefixed byte lengths stay valid
        tail = str(self._n).rjust(4, "0")
        fake = ("9" * (len(real) - len(tail)) + tail) if len(real) > len(tail) else tail
        self.ids[real] = fake
        return fake

    def fake_handle(self, real):
        return self.handles.setdefault(str(real), f"user_fake{len(self.handles) + 1}")

    def fake_secuid(self, real):
        return self.secuids.setdefault(str(real), "SEC_" + "F" * max(0, len(str(real)) - 4) + str(len(self.secuids) + 1).rjust(4, "0"))[:len(str(real))] or "SEC_FAKE"

    def scrub_str(self, s):
        s = SIG_IN_URL.sub(lambda m: f"{m.group(1)}{m.group(2)}=REDACTED", s)
        s = EMAIL_RE.sub("redacted@example.com", s)
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
            if key in ("uid", "user_id", "user_id_str", "sender", "sender_id",
                       "to_user_id", "from_user_id", "conversation_id", "owner",
                       "recipient_id") and BIGID_RE.match(obj):
                return self.fake_id(obj)
            if key in ("unique_id", "handle", "username", "remark_name"):
                return self.fake_handle(obj) if obj else obj
            if key in ("nickname", "nick_name", "display_name") and obj:
                return f"Contact {len(self.handles) + len(self.ids)}"
            if key in ("sec_uid", "secuid"):
                return self.fake_secuid(obj) if obj else obj
            if key in ("signature", "bio") and not self.keep_text:
                return ""
            if key in ("content", "text") and not self.keep_text:
                return ""
            return self.scrub_str(obj)
        if isinstance(obj, int) and key in ("uid", "user_id", "user_id_str", "sender",
                                            "conversation_id") and len(str(obj)) >= 15:
            return int(self.fake_id(obj))
        return obj

    def scrub_bytes(self, raw):
        # replace every mapped real id (ascii) with its same-length fake in raw
        # protobuf bytes; blank obvious secret ascii runs left over.
        for real, fake in self.ids.items():
            raw = raw.replace(real.encode(), fake.encode())
        return raw


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
    # prefer a non-empty payload for each kind (the empty inbox produced empties too)
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
        ent["notes"] = "protobuf body (im-api); ids pseudonymized"
    if rec.get("request_body_b64"):
        ent["request_body_b64"] = base64.b64encode(
            red.scrub_bytes(base64.b64decode(rec["request_body_b64"]))).decode()
    return ent


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("captures", nargs="+")
    ap.add_argument("--drop-text", action="store_true")
    ap.add_argument("--out", default=FIX_DIR)
    a = ap.parse_args(argv)

    paths = []
    for pat in a.captures:
        paths.extend(sorted(glob.glob(pat)) or [pat])
    red = Redactor(keep_text=not a.drop_text)
    os.makedirs(a.out, exist_ok=True)

    # first pass: build the id map from the richest records (profiles/contacts)
    records = []
    for path in paths:
        for line in open(path, encoding="utf-8"):
            records.append(json.loads(line))
    for rec in records:
        if rec.get("kind") == "response" and rec.get("body_text"):
            try:
                red.walk(json.loads(rec["body_text"]))
            except ValueError:
                pass

    chosen = {}
    for rec in records:
        kind = _match_kind(rec)
        if not kind or kind in chosen:
            continue
        if not _good(kind, rec):
            continue
        chosen[kind] = rec

    manifest = []
    for kind, rec in chosen.items():
        ent = _fixture_from(kind, rec, red)
        fname = f"{kind}.json"
        with open(os.path.join(a.out, fname), "w", encoding="utf-8") as f:
            json.dump(ent, f, indent=2, ensure_ascii=False)
        manifest.append({k: ent[k] for k in
                         ("kind", "url_pattern", "method", "captured_at", "redacted", "notes")}
                        | {"file": fname})

    with open(os.path.join(a.out, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump({"generated_from": "browser-data/jakob (empty inbox, 2026-09-18)",
                   "fixtures": manifest}, f, indent=2)
    print(f"wrote {len(manifest)} fixtures to {a.out}")
    for m in manifest:
        print(f"  {m['kind']:16s} {m['method']:4s} {m['url_pattern']}")


if __name__ == "__main__":
    main(sys.argv[1:])
