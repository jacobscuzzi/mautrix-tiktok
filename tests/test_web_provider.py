import json
import os
import unittest

from bridge import errors
from bridge.provider import MessageProvider
from bridge.providers.web import (WebProvider, AvatarClient, parse_contacts,
                                  parse_profile, parse_conversations, parse_messages)
from bridge.state import SyncState
from bridge.sync import Syncer

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "web")


def load(name):
    with open(os.path.join(FIX, name)) as f:
        return json.load(f)


class FakePage:
    """Scripted PageClient: map a URL substring to a queue of parsed responses."""

    def __init__(self, routes):
        self.routes = {k: list(v) for k, v in routes.items()}
        self.calls = []
        self._cbs = []

    def call(self, method, url, params=None, body=None):
        self.calls.append((method, url, params))
        for frag, queue in self.routes.items():
            if frag in url:
                return queue.pop(0) if len(queue) > 1 else queue[0]
        raise AssertionError(f"no route for {url}")

    def on_frame(self, cb):
        self._cbs.append(cb)


class TestParsers(unittest.TestCase):
    def test_contacts_real_fixture(self):
        users = parse_contacts(load("contacts.json")["response"])
        self.assertGreaterEqual(len(users), 10)
        u = users[0]
        self.assertTrue(u["uid"] and u["unique_id"])
        self.assertIn("avatar_thumb", u)

    def test_profile_real_fixture(self):
        p = parse_profile(load("profile_other.json")["response"])
        self.assertTrue(p["user_id"])
        self.assertTrue(p["avatars"])

    def test_conversations_synth_fixture(self):
        convs = parse_conversations(load("conv_list_synth.json")["response"])
        self.assertEqual(len(convs), 1)
        self.assertTrue(convs[0]["conversation_id"])

    def test_messages_synth_fixture(self):
        msgs = parse_messages(load("messages_synth.json")["response"])
        self.assertEqual(len(msgs), 3)
        self.assertTrue(all(m["server_message_id"] for m in msgs))

    def test_schema_change_on_renamed_field(self):
        # rename followings -> followings_v2: the parser must raise, not return []
        bad = {"followings_v2": [], "status_code": 0}
        with self.assertRaises(errors.SchemaChange):
            parse_contacts(bad)
        with self.assertRaises(errors.SchemaChange):
            parse_messages({"msgs": []})


class TestWebProviderContract(unittest.TestCase):
    def _provider(self):
        page = FakePage({
            "spotlight/relation": [load("contacts.json")["response"]],
            "spotlight/inbox": [load("conv_list_synth.json")["response"]],
            "spotlight/messages": [load("messages_synth.json")["response"],
                                   {"messages": [], "has_more": 0, "next_cursor": ""}],
            "im/user/profile": [load("profile_other.json")["response"]],
        })
        return WebProvider(page), page

    def test_is_a_message_provider(self):
        wp, _ = self._provider()
        self.assertIsInstance(wp, MessageProvider)

    def test_list_contacts_returns_users_with_avatars(self):
        wp, _ = self._provider()
        users, cursor, more = wp.list_contacts()
        self.assertTrue(users[0].user_id)
        self.assertTrue(users[0].handle)
        self.assertTrue(users[0].avatar_url)

    def test_get_profile(self):
        wp, _ = self._provider()
        u = wp.get_profile("9999990007")
        self.assertTrue(u.avatar_url and u.display_name)

    def test_send_and_mark_read_refused(self):
        wp, _ = self._provider()
        with self.assertRaises(errors.NotSupported):
            wp.send_text("c1", "hi")
        with self.assertRaises(errors.NotSupported):
            wp.mark_read("c1")

    def test_through_syncer_with_dedup(self):
        # The interchangeability proof: WebProvider drives the real Syncer, and a
        # re-poll does not re-emit already-seen messages.
        wp, _ = self._provider()
        state = SyncState()
        emitted = []
        syncer = Syncer(wp, state, emitted.append)
        n1 = syncer.backfill("0:1:9999990001:9999990002")
        self.assertEqual(n1, 3)
        ids = sorted(e.message_id for e in emitted)
        self.assertEqual(ids, ["7500000000000000001", "7500000000000000002",
                               "7500000000000000003"])
        # a message of a non-text type is classified
        kinds = {e.message_id: e.kind for e in emitted}
        self.assertEqual(kinds["7500000000000000003"], "share")
        # re-poll: everything already seen -> 0 new
        emitted.clear()
        n2 = syncer.poll_once()
        self.assertEqual(n2, 0)


class TestAvatarClient(unittest.TestCase):
    def test_allowlist_enforced(self):
        ac = AvatarClient(transport=lambda u: b"img")
        with self.assertRaises(errors.InvalidRequest):
            ac.fetch("https://evil.example.com/a.jpg")

    def test_hashes_by_url(self):
        ac = AvatarClient(transport=lambda u: b"bytes")
        key, data = ac.fetch("https://p16-common-sign.tiktokcdn-eu.com/x.webp?sig=1")
        self.assertEqual(len(key), 64)
        self.assertEqual(data, b"bytes")


if __name__ == "__main__":
    unittest.main()
