import base64
import importlib.util
import json
import os
import re
import unittest

from bridge import proto

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_HERE, rel))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


redact = _load("redact_capture", "scripts/redact-capture.py")

# obviously synthetic identifiers (no real account has these)
UID_A = "7000000000000000123"
UID_B = "7000000000000000456"
FILLER = re.compile(r"^(REDACTEDx*|0+)$")


class TestRedactor(unittest.TestCase):
    def setUp(self):
        self.r = redact.Redactor(keep_text=True)

    def test_ids_are_stable_and_length_preserving(self):
        a = self.r.fake_id(UID_A)
        b = self.r.fake_id(UID_A)
        self.assertEqual(a, b)
        self.assertEqual(len(a), len(UID_A))
        self.assertNotEqual(a, UID_A)

    def test_fake_id_keeps_varint_width(self):
        for real in (UID_A, "1234567890123456789", "6000000000000000001", "99990226", "123456"):
            fake = self.r.fake_id(real)
            self.assertEqual(len(fake), len(real))
            self.assertEqual(len(proto._write_varint(int(fake))),
                             len(proto._write_varint(int(real))), real)

    def test_handles_pseudonymized(self):
        self.assertTrue(self.r.fake_handle("some.handle").startswith("user_fake"))

    def test_secret_keys_blanked(self):
        out = self.r.walk({"sessionid": "abc123", "msToken": "zzz", "keep": "ok"})
        self.assertEqual(out["sessionid"], "REDACTED")
        self.assertEqual(out["msToken"], "REDACTED")
        self.assertEqual(out["keep"], "ok")

    def test_uid_and_handle_fields_replaced(self):
        out = self.r.walk({"uid": UID_B, "unique_id": "some.handle",
                           "nickname": "Ada Lovelace"})
        self.assertNotEqual(out["uid"], UID_B)
        self.assertTrue(out["unique_id"].startswith("user_fake"))
        self.assertNotIn("Ada", out["nickname"])

    def test_short_legacy_uid_replaced(self):
        out = self.r.walk({"uid": "99990226"})
        self.assertNotEqual(out["uid"], "99990226")
        self.assertEqual(len(out["uid"]), 8)

    def test_url_signature_scrubbed_text_kept(self):
        s = self.r.scrub_str("https://cdn/x.webp?refresh_token=deadbeef&x-signature=AB%3D&t=1")
        self.assertNotIn("deadbeef", s)
        self.assertNotIn("AB%3D", s)
        self.assertIn("REDACTED", s)

    def test_email_scrubbed(self):
        self.assertNotIn("bob@evil.com", self.r.scrub_str("user bob@evil.com here"))

    def test_message_text_dropped_when_requested(self):
        r = redact.Redactor(keep_text=False)
        self.assertEqual(r.walk({"content": "secret dm"})["content"], "")

    def test_bio_always_blanked(self):
        self.assertEqual(self.r.walk({"signature": "lives in Berlin"})["signature"], "")

    def test_avatar_object_hash_replaced_stably(self):
        h = "ab" * 16
        uri = "tos-alisg-avt-0068/" + h
        out = self.r.scrub_str(uri)
        self.assertNotIn(h, out)
        self.assertEqual(len(out), len(uri))
        self.assertEqual(out, self.r.scrub_str(uri))

    def test_collected_query_secrets_replaced_everywhere(self):
        tok = "T" * 40
        self.r.collect_secrets([{"url": "https://x/api?msToken=" + tok + "&aid=1"}])
        self.assertNotIn(tok, self.r.scrub_str("prefix " + tok))
        self.assertNotIn(tok.encode(), self.r.scrub_bytes(b"pb " + tok.encode()))

    def test_protobuf_header_values_blanked_in_place(self):
        body = proto.encode_tree({
            9: [UID_A],
            15: [{1: ["device_id"], 2: [UID_A]},
                 {1: ["verifyFp"], 2: ["verify_" + "a" * 45]},
                 {1: ["Web-Sdk-Ms-Token"], 2: ["m" * 152]},
                 {1: ["region"], 2: ["DE"]}],
        })
        out = self.r.scrub_bytes(body)
        self.assertEqual(len(out), len(body))
        tree = proto.decode_tree(out)
        entries = {e[1][0]: e[2][0] for e in tree[15]}
        self.assertEqual(entries["device_id"], "0" * 19)
        self.assertRegex(entries["verifyFp"], FILLER)
        self.assertRegex(entries["Web-Sdk-Ms-Token"], FILLER)
        self.assertEqual(entries["region"], "DE")
        self.assertEqual(tree[9][0], "0" * 19)     # the bare copy is blanked too

    def test_varint_ids_replaced_in_protobuf(self):
        fake = self.r.fake_id(UID_A)
        body = proto.encode_fields({15: int(UID_A), 3: 7})
        out = self.r.scrub_bytes(body)
        decoded = proto.decode_fields(out)
        self.assertEqual(decoded[15][0], int(fake))
        self.assertEqual(decoded[3][0], 7)


class TestFixturesOnDisk(unittest.TestCase):
    """The committed fixtures must parse and must not carry live secrets -- neither
    in the JSON text nor inside the base64 protobuf / websocket bodies."""

    FIX = os.path.join(_HERE, "tests", "fixtures", "web")
    TEXT_SECRET = re.compile(r"(sessionid|sid_tt|sid_guard|msToken|verifyFp)=[A-Za-z0-9_-]{8,}"
                             r"|x-signature=[A-Za-z0-9%+/]{8,}(?<!REDACTED)")
    # a 19-digit TikTok-shaped id that is not obviously synthetic
    REAL_ID = re.compile(rb"(?<!\d)[2-7]\d{18}(?!\d)")

    def _fixtures(self):
        for fn in sorted(os.listdir(self.FIX)):
            if fn.endswith(".json"):
                with open(os.path.join(self.FIX, fn), encoding="utf-8") as fh:
                    yield fn, json.load(fh)

    def test_manifest_lists_files(self):
        with open(os.path.join(self.FIX, "manifest.json")) as fh:
            man = json.load(fh)
        self.assertTrue(man["fixtures"])
        for e in man["fixtures"]:
            self.assertTrue(os.path.exists(os.path.join(self.FIX, e["file"])), e["file"])

    def test_no_live_secrets_in_text(self):
        for fn in os.listdir(self.FIX):
            with open(os.path.join(self.FIX, fn)) as fh:
                text = fh.read()
            hits = [h for h in self.TEXT_SECRET.findall(text) if "REDACTED" not in str(h)]
            self.assertEqual(hits, [], f"secret-like content in {fn}: {hits[:3]}")

    def _assert_no_real_ids(self, raw, where):
        for m in self.REAL_ID.finditer(raw):
            run = m.group()
            self.assertTrue(b"000000" in run or b"999999" in run,
                            f"real-looking id {run.decode()} in {where}")

    def test_no_live_secrets_in_encoded_bodies(self):
        for fn, ent in self._fixtures():
            for key, val in ent.items():
                if not key.endswith("_b64"):
                    continue
                raw = base64.b64decode(val)
                self.assertNotIn(b"verify_", raw, f"{fn}:{key}")
                for hk in redact.PB_SECRET_KEYS:
                    for s, e in redact.pb_header_values(raw, hk):
                        self.assertRegex(raw[s:e].decode("utf-8", "replace"), FILLER,
                                         f"{fn}:{key} header {hk} not blanked")
                self._assert_no_real_ids(raw, f"{fn}:{key}")

    def test_no_real_looking_ids_in_json(self):
        def walk(o, where):
            if isinstance(o, dict):
                for k, v in o.items():
                    if not str(k).endswith("_b64"):
                        walk(v, f"{where}.{k}")
            elif isinstance(o, list):
                for v in o:
                    walk(v, where)
            elif isinstance(o, (str, int)) and not isinstance(o, bool):
                self._assert_no_real_ids(str(o).encode(), where)
        for fn, ent in self._fixtures():
            walk(ent, fn)


if __name__ == "__main__":
    unittest.main()
