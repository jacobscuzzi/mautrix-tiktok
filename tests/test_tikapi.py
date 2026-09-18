import json
import unittest

from bridge import errors, normalize
from bridge.provider import MessageProvider, describe
from bridge.providers.tikapi import TikApiProvider, _extract_text
from bridge.sync import Syncer
from bridge.state import SyncState
from bridge.auth import tikapi_oauth


class FakeTransport:
    """Scripted transport: queue (status, dict_body) tuples; records calls."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def __call__(self, method, url, headers, body):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers,
                "body": json.loads(body) if body else None,
            }
        )
        status, payload = self.script.pop(0)
        return status, {}, json.dumps(payload).encode()


def provider(script):
    return TikApiProvider("APIKEY", "ACCTKEY", transport=FakeTransport(script))


class TestAuthAndErrors(unittest.TestCase):
    def test_requires_both_keys(self):
        with self.assertRaises(ValueError):
            TikApiProvider("", "x", transport=FakeTransport([]))
        with self.assertRaises(ValueError):
            TikApiProvider("x", "", transport=FakeTransport([]))

    def test_sends_both_auth_headers(self):
        p = provider([(200, {"conversations": []})])
        p.list_conversations()
        h = p._transport.calls[0]["headers"]
        self.assertEqual(h["X-API-KEY"], "APIKEY")
        self.assertEqual(h["X-ACCOUNT-KEY"], "ACCTKEY")
        self.assertEqual(h["Content-Type"], "application/json")

    def test_428_maps_to_auth_error(self):
        p = provider([(428, {"message": "Account session expired"})])
        with self.assertRaises(errors.AuthError):
            p.list_conversations()

    def test_429_maps_to_rate_limited(self):
        p = provider([(429, {"message": "slow down"})])
        with self.assertRaises(errors.RateLimited):
            p.list_conversations()

    def test_503_maps_to_transient(self):
        p = provider([(503, {"message": "TikTok Gateway Error"})])
        with self.assertRaises(errors.Transient):
            p.list_conversations()

    def test_4xx_maps_to_invalid_request(self):
        p = provider([(400, {"message": "bad"})])
        with self.assertRaises(errors.InvalidRequest):
            p.list_conversations()

    def test_check_session_true_and_false(self):
        self.assertTrue(provider([(200, {"status": "success"})]).check_session())
        self.assertFalse(provider([(428, {"message": "expired"})]).check_session())


class TestParsingAndPagination(unittest.TestCase):
    def test_list_conversations_parses_and_caches_ticket(self):
        p = provider(
            [
                (
                    200,
                    {
                        "conversations": [
                            {
                                "conversation_id": "0:1:aaa:bbb",
                                "conversation_short_id": "6940245147502654884",
                                "ticket": "TICKET123",
                                "participants": [{"uid": "42"}],
                            }
                        ],
                        "nextCursor": "CURSOR2",
                        "hasMore": True,
                    },
                )
            ]
        )
        convs, nxt, more = p.list_conversations()
        self.assertEqual(nxt, "CURSOR2")
        self.assertTrue(more)
        self.assertEqual(convs[0]["conversation_id"], "0:1:aaa:bbb")
        self.assertEqual(
            p._conv_meta["0:1:aaa:bbb"],
            {"short_id": "6940245147502654884", "ticket": "TICKET123"},
        )

    def test_conversations_as_keyed_map(self):
        p = provider([(200, {"conversations": {"c1": {"conversation_id": "c1"}}})])
        convs, _, _ = p.list_conversations()
        self.assertEqual(convs[0]["conversation_id"], "c1")

    def test_get_messages_parses_content_json_string(self):
        p = provider(
            [
                (
                    200,
                    {
                        "messages": [
                            {
                                "server_message_id": "m1",
                                "conversation_id": "c1",
                                "sender": "42",
                                "content": json.dumps({"text": "hello there", "aweType": 0}),
                                "create_time": 1700000000,
                            }
                        ],
                        "hasMore": False,
                    },
                )
            ]
        )
        msgs, _, more = p.get_messages("c1")
        self.assertFalse(more)
        self.assertEqual(msgs[0]["content"], "hello there")
        # and it normalizes into a canonical Event cleanly
        ev = normalize.to_event(msgs[0])
        self.assertEqual(ev.text, "hello there")
        self.assertEqual(ev.message_id, "m1")
        self.assertEqual(ev.sender_id, "42")

    def test_extract_text_variants(self):
        self.assertEqual(_extract_text('{"text":"hi"}'), "hi")
        self.assertEqual(_extract_text({"text": "yo"}), "yo")
        self.assertEqual(_extract_text("plain"), "plain")
        self.assertEqual(_extract_text(None), "")
        self.assertEqual(_extract_text("{bad json"), "{bad json")

    def test_get_messages_passes_short_id_when_known(self):
        p = provider(
            [
                (200, {"conversations": [
                    {"conversation_id": "c1", "conversation_short_id": "999", "ticket": "tk"}]}),
                (200, {"messages": []}),
            ]
        )
        p.list_conversations()
        p.get_messages("c1")
        self.assertIn("conversation_short_id=999", p._transport.calls[1]["url"])


class TestSend(unittest.TestCase):
    def test_send_builds_payload_with_ticket(self):
        p = provider(
            [
                (200, {"conversations": [
                    {"conversation_id": "c1", "conversation_short_id": "s1", "ticket": "tk1"}]}),
                (200, {"message_id": "srv1"}),
            ]
        )
        p.list_conversations()
        res = p.send_text("c1", "hey", client_message_id="cm1")
        body = p._transport.calls[1]["body"]
        self.assertEqual(body, {
            "text": "hey", "conversation_id": "c1",
            "conversation_short_id": "s1", "ticket": "tk1"})
        self.assertEqual(res["server_message_id"], "srv1")

    def test_send_resolves_ticket_by_fetching_when_missing(self):
        # no prior list_conversations; send must fetch conversations first
        p = provider(
            [
                (200, {"conversations": [
                    {"conversation_id": "c1", "conversation_short_id": "s1", "ticket": "tk1"}]}),
                (200, {"message_id": "srv1"}),
            ]
        )
        res = p.send_text("c1", "hi")
        self.assertEqual(res["server_message_id"], "srv1")
        self.assertEqual(p._transport.calls[0]["url"].split("?")[0].split("/")[-1], "conversations")

    def test_send_without_ticket_refuses(self):
        p = provider([(200, {"conversations": []})])  # conv not found
        with self.assertRaises(errors.InvalidRequest):
            p.send_text("unknown", "hi")

    def test_send_is_idempotent_on_client_message_id(self):
        p = provider(
            [
                (200, {"conversations": [
                    {"conversation_id": "c1", "conversation_short_id": "s1", "ticket": "tk1"}]}),
                (200, {"message_id": "srv1"}),
            ]
        )
        p.list_conversations()
        p.send_text("c1", "once", client_message_id="dup")
        with self.assertRaises(errors.InvalidRequest):
            p.send_text("c1", "twice", client_message_id="dup")

    def test_mark_read_surfaces_unsupported(self):
        with self.assertRaises(errors.InvalidRequest):
            provider([]).mark_read("c1")


class TestInterchangeableWithNative(unittest.TestCase):
    """The whole point: TikApiProvider drops into Syncer like the native IM."""

    def test_is_a_message_provider(self):
        self.assertIsInstance(provider([]), MessageProvider)
        self.assertEqual(describe(provider([])), "tikapi")

    def test_syncer_drives_tikapi_provider(self):
        p = provider(
            [
                # poll_once -> list_conversations
                (200, {"conversations": [
                    {"conversation_id": "c1", "conversation_short_id": "s1", "ticket": "tk"}]}),
                # -> get_messages(c1)
                (200, {"messages": [
                    {"server_message_id": "m1", "conversation_id": "c1",
                     "sender": "42", "content": "hi", "create_time": 1}],
                    "hasMore": False}),
            ]
        )
        emitted = []
        syncer = Syncer(p, SyncState(), emitted.append)
        n = syncer.poll_once()
        self.assertEqual(n, 1)
        self.assertEqual(emitted[0].text, "hi")
        # dedup: re-poll same message id -> no re-emit
        p2 = provider(
            [
                (200, {"conversations": [
                    {"conversation_id": "c1", "conversation_short_id": "s1", "ticket": "tk"}]}),
                (200, {"messages": [
                    {"server_message_id": "m1", "conversation_id": "c1",
                     "sender": "42", "content": "hi", "create_time": 1}],
                    "hasMore": False}),
            ]
        )
        syncer2 = Syncer(p2, syncer.state, emitted.append)
        self.assertEqual(syncer2.poll_once(), 0)


class TestOAuthHelper(unittest.TestCase):
    def test_authorize_url_builds_scopes(self):
        url = tikapi_oauth.authorize_url("CID", "https://bridge/cb", state="xyz")
        self.assertIn("client_id=CID", url)
        self.assertIn("redirect_uri=https%3A%2F%2Fbridge%2Fcb", url)
        self.assertIn("send_messages", url)
        self.assertIn("state=xyz", url)

    def test_authorize_requires_client_and_redirect(self):
        with self.assertRaises(ValueError):
            tikapi_oauth.authorize_url("", "x")

    def test_parse_redirect_query(self):
        r = tikapi_oauth.parse_redirect(
            "https://bridge/cb?access_token=ACC123&scope=view_messages%20send_messages&state=xyz")
        self.assertEqual(r["account_key"], "ACC123")
        self.assertEqual(r["scope"], ["view_messages", "send_messages"])
        self.assertEqual(r["state"], "xyz")

    def test_parse_redirect_fragment(self):
        r = tikapi_oauth.parse_redirect("https://bridge/cb#access_token=ACC456")
        self.assertEqual(r["account_key"], "ACC456")

    def test_parse_redirect_denied_raises(self):
        with self.assertRaises(ValueError):
            tikapi_oauth.parse_redirect("https://bridge/cb?error=access_denied")

    def test_missing_scopes(self):
        self.assertEqual(
            tikapi_oauth.missing_scopes(["view_messages"]),
            ["send_messages", "conversation_requests", "view_notifications"],
        )
        self.assertEqual(tikapi_oauth.missing_scopes(tikapi_oauth.DM_SCOPES), [])


if __name__ == "__main__":
    unittest.main()
