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

    def test_rejects_unsafe_login_id(self):
        with self.assertRaises(ValueError):
            self.store.save("../evil", {"sessionid": "abc"})

    def test_blob_is_bound_to_its_login_id(self):
        self.store.save("42", {"sessionid": "abc"})
        os.rename(os.path.join(self.dir, "42.session"), os.path.join(self.dir, "43.session"))
        with self.assertRaises(Exception):
            self.store.load("43")

if __name__ == "__main__":
    unittest.main()
