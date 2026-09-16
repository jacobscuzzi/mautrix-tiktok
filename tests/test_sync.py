import unittest
from bridge.sync import Syncer
from bridge.state import SyncState

class FakeFetcher:
    def __init__(self, convs, messages):
        self.convs = convs
        self.messages = messages
        self._page = {}
    def list_conversations(self, cursor):
        return self.convs, "", False
    def get_messages(self, conv_id, cursor):
        pages = self.messages[conv_id]
        idx = self._page.get(conv_id, 0)
        self._page[conv_id] = idx + 1
        return pages[idx]

def msg(mid):
    return {"server_message_id": mid, "conversation_id": "c1", "sender": "u1",
            "content": "x", "create_time": int(mid)}

class TestSync(unittest.TestCase):
    def test_backfill_paginates_and_dedups(self):
        f = FakeFetcher(
            convs=[{"conversation_id": "c1"}],
            messages={"c1": [([msg("2"), msg("1")], "cur1", True),
                             ([msg("1")], "", False)]},
        )
        state = SyncState()
        emitted = []
        n = Syncer(f, state, emitted.append).backfill("c1")
        ids = sorted(e.message_id for e in emitted)
        self.assertEqual(ids, ["1", "2"])
        self.assertEqual(n, 2)

    def test_poll_does_not_re_emit_seen(self):
        f = FakeFetcher(
            convs=[{"conversation_id": "c1"}],
            messages={"c1": [([msg("5")], "", False)]},
        )
        state = SyncState()
        state.mark_seen("5")
        emitted = []
        n = Syncer(f, state, emitted.append).poll_once()
        self.assertEqual(n, 0)

if __name__ == "__main__":
    unittest.main()
