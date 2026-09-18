import unittest

from bridge.pipeline import Pipeline
from bridge.normalize import Event, User, Thread


def ev(mid, ts, text="hi", kind="text", conv="c1", sender="u2"):
    return Event(message_id=mid, conversation_id=conv, sender_id=sender, text=text,
                 timestamp_ms=ts, kind=kind)


class TestPipeline(unittest.TestCase):
    def setUp(self):
        self.p = Pipeline(":memory:")
        self.p.upsert_login("L1", source="web")

    def test_ingest_is_idempotent(self):
        self.assertTrue(self.p.ingest_event(ev("m1", 100), "L1"))
        self.assertFalse(self.p.ingest_event(ev("m1", 100), "L1"))  # dup dropped
        rows = self.p.get_messages("c1")
        self.assertEqual(len(rows), 1)

    def test_stream_order_and_cursor(self):
        for mid, ts in (("m1", 100), ("m2", 200), ("m3", 300)):
            self.p.ingest_event(ev(mid, ts), "L1")
        after = self.p.get_messages("c1", cursor=100)
        self.assertEqual([r["message_id"] for r in after], ["m2", "m3"])

    def test_kind_preserved(self):
        self.p.ingest_event(ev("m9", 1, kind="share"), "L1")
        self.assertEqual(self.p.get_messages("c1")[0]["kind"], "share")

    def test_users_and_threads(self):
        self.p.upsert_user("L1", User("u2", "Ann", "http://a", handle="ann", sec_uid="S"))
        self.p.upsert_thread("L1", Thread("c1", ["u1", "u2"], last_ts=300))
        self.assertEqual(self.p.list_contacts("L1")[0]["handle"], "ann")
        self.assertEqual(self.p.list_threads("L1")[0]["thread_id"], "c1")

    def test_login_password_flag_is_sticky(self):
        self.p.upsert_login("L1", state="connected", password_login_used=True)
        self.p.upsert_login("L1", state="connected", password_login_used=False)
        row = [l for l in self.p.logins() if l["login_id"] == "L1"][0]
        self.assertEqual(row["password_login_used"], 1)

    def test_webhook_fires_once_per_new_event(self):
        fired = []
        p = Pipeline(":memory:", webhook=lambda payload: fired.append(payload))
        p.upsert_login("L1")
        p.ingest_event(ev("m1", 1), "L1")
        p.ingest_event(ev("m1", 1), "L1")  # dup: no webhook
        self.assertEqual(len(fired), 1)
        self.assertEqual(fired[0]["message_id"], "m1")


if __name__ == "__main__":
    unittest.main()
