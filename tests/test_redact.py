import importlib.util
import os
import unittest

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_HERE, rel))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


redact = _load("redact_capture", "scripts/redact-capture.py")


class TestRedactor(unittest.TestCase):
    def setUp(self):
        self.r = redact.Redactor(keep_text=True)

    def test_ids_are_stable_and_length_preserving(self):
        a = self.r.fake_id("0000000000000000000")
        b = self.r.fake_id("0000000000000000000")
        self.assertEqual(a, b)
        self.assertEqual(len(a), len("0000000000000000000"))
        self.assertNotEqual(a, "0000000000000000000")

    def test_handles_pseudonymized(self):
        self.assertTrue(self.r.fake_handle("user_fake213").startswith("user_fake"))

    def test_secret_keys_blanked(self):
        out = self.r.walk({"sessionid": "abc123", "msToken": "zzz", "keep": "ok"})
        self.assertEqual(out["sessionid"], "REDACTED")
        self.assertEqual(out["msToken"], "REDACTED")
        self.assertEqual(out["keep"], "ok")

    def test_uid_and_handle_fields_replaced(self):
        out = self.r.walk({"uid": "8999999999999990212", "unique_id": "user_fake213",
                           "nickname": "Contact 281"})
        self.assertNotEqual(out["uid"], "8999999999999990212")
        self.assertTrue(out["unique_id"].startswith("user_fake"))
        self.assertNotIn("Karim", out["nickname"])

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


class TestFixturesOnDisk(unittest.TestCase):
    """The committed fixtures must parse and must not carry live secrets."""

    FIX = os.path.join(_HERE, "tests", "fixtures", "web")

    def test_manifest_lists_files(self):
        import json
        with open(os.path.join(self.FIX, "manifest.json")) as fh:
            man = json.load(fh)
        self.assertTrue(man["fixtures"])
        for e in man["fixtures"]:
            self.assertTrue(os.path.exists(os.path.join(self.FIX, e["file"])), e["file"])

    def test_no_live_secrets(self):
        import re
        bad = re.compile(r"(sessionid|sid_tt|sid_guard|msToken|verifyFp)=[A-Za-z0-9_-]{8,}"
                         r"|x-signature=[A-Za-z0-9%+/]{8,}(?<!REDACTED)")
        for fn in os.listdir(self.FIX):
            with open(os.path.join(self.FIX, fn)) as fh:
                text = fh.read()
            hits = [h for h in bad.findall(text) if "REDACTED" not in str(h)]
            self.assertEqual(hits, [], f"secret-like content in {fn}: {hits[:3]}")


if __name__ == "__main__":
    unittest.main()
