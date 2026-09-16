import json
import os

class SyncState:
    def __init__(self, path=None):
        self.path = path
        self.cursors = {}
        self._seen = set()

    def get_cursor(self, conv_id):
        return self.cursors.get(conv_id, "")

    def set_cursor(self, conv_id, cursor):
        self.cursors[conv_id] = cursor

    def seen(self, message_id):
        return message_id in self._seen

    def mark_seen(self, message_id):
        self._seen.add(message_id)

    def save(self):
        if not self.path:
            return
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"cursors": self.cursors, "seen": list(self._seen)}, f)
        os.replace(tmp, self.path)

    def load(self):
        if not self.path or not os.path.exists(self.path):
            return
        with open(self.path) as f:
            d = json.load(f)
        self.cursors = d.get("cursors", {})
        self._seen = set(d.get("seen", []))
