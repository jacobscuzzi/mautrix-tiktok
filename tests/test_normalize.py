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
