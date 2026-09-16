import os
import tempfile
import unittest
from bridge.state import SyncState

class TestState(unittest.TestCase):
    def test_cursor_default_and_set(self):
        s = SyncState()
        self.assertEqual(s.get_cursor("c1"), "")
        s.set_cursor("c1", "10")
        self.assertEqual(s.get_cursor("c1"), "10")

    def test_dedup(self):
        s = SyncState()
        self.assertFalse(s.seen("m1"))
        s.mark_seen("m1")
        self.assertTrue(s.seen("m1"))

    def test_persist_roundtrip(self):
        p = os.path.join(tempfile.mkdtemp(), "state.json")
        s = SyncState(p)
        s.set_cursor("c1", "5")
        s.mark_seen("m1")
        s.save()
        s2 = SyncState(p)
        s2.load()
        self.assertEqual(s2.get_cursor("c1"), "5")
        self.assertTrue(s2.seen("m1"))

if __name__ == "__main__":
    unittest.main()
