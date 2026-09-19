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


class TestContentKinds(unittest.TestCase):
    def _ev(self, content):
        return normalize.to_event({"server_message_id": "m", "conversation_id": "c",
                                   "sender": "u", "content": content, "create_time": 1})

    def test_text_json(self):
        e = self._ev('{"aweType":0,"text":"hello"}')
        self.assertEqual((e.kind, e.text), ("text", "hello"))

    def test_video_share_json(self):
        e = self._ev('{"aweType":2,"itemId":"7300000000000000001","title":"a title",'
                     '"coverUrl":"https://p16-sign.tiktokcdn.com/c.jpeg"}')
        self.assertEqual((e.kind, e.text), ("share", "a title"))

    def test_video_share_without_title(self):
        e = self._ev('{"awemeId":"7300000000000000001","secUid":"x"}')
        self.assertEqual(e.kind, "share")
        self.assertEqual(e.text, "")

    def test_gif_json_still_media(self):
        e = self._ev('{"aweType":701,"url_list":["https://p16.tiktokcdn.com/a.gif"]}')
        self.assertEqual(e.kind, "gif")
        self.assertTrue(e.text.startswith("https://"))

    def test_unknown_json_is_unknown_not_blank_text(self):
        e = self._ev('{"aweType":999,"foo":{"bar":1}}')
        self.assertEqual((e.kind, e.text), ("unknown", ""))

    def test_plain_string_is_text(self):
        e = self._ev("just words")
        self.assertEqual((e.kind, e.text), ("text", "just words"))


class TestLiveShapes(unittest.TestCase):
    """Shapes confirmed on a real inbox (2026-09-19)."""

    def _ev(self, content, ext=None):
        return normalize.to_event({"server_message_id": "m", "conversation_id": "c",
                                   "sender": "u", "content": content, "create_time": 1,
                                   "ext": ext or {}})

    def test_shared_video_awetype_800_with_caption(self):
        e = self._ev('{"aweType":800,"content_name":"someone","content_title":"the caption",'
                     '"content_thumb":"https://p16.tiktokcdn.com/t.jpeg","cover_url":'
                     '"https://p16.tiktokcdn.com/c.jpeg","cover_height":1024,"cover_width":576,'
                     '"itemId":"7300000000000000001","uid":"42"}')
        self.assertEqual(e.kind, "share")
        self.assertEqual(e.text, "the caption · someone")

    def test_shared_video_awetype_800_without_caption(self):
        e = self._ev('{"aweType":800,"content_name":"someone","content_thumb":"https://x/t.jpeg",'
                     '"cover_url":"https://x/c.jpeg","cover_height":1,"cover_width":1,'
                     '"itemId":"7300000000000000001","uid":"42"}')
        self.assertEqual((e.kind, e.text), ("share", "someone"))

    def test_one_sided_server_notice_is_system(self):
        # f8 was the 2-byte "pl", f9 ext carried s:visible = the one viewer's uid
        e = self._ev("pl", ext={"s:visible": "7072771823255405573", "s:client_message_id": "x"})
        self.assertEqual(e.kind, "system")
        e = self._ev("", ext={"s:visible": "7072771823255405573"})
        self.assertEqual(e.kind, "system")

    def test_real_text_with_s_visible_stays_text(self):
        e = self._ev('{"aweType":0,"text":"hello"}', ext={"s:visible": "1"})
        self.assertEqual((e.kind, e.text), ("text", "hello"))


if __name__ == "__main__":
    unittest.main()
