from . import normalize

class Syncer:
    def __init__(self, fetcher, state, emit):
        self.fetcher = fetcher
        self.state = state
        self.emit = emit

    def _emit_new(self, items):
        count = 0
        for d in items:
            e = normalize.to_event(d)
            if not e.message_id or self.state.seen(e.message_id):
                continue
            self.state.mark_seen(e.message_id)
            self.emit(e)
            count += 1
        return count

    def backfill(self, conv_id):
        total = 0
        cursor = self.state.get_cursor(conv_id)
        while True:
            items, nxt, has_more = self.fetcher.get_messages(conv_id, cursor)
            total += self._emit_new(items)
            if nxt:
                self.state.set_cursor(conv_id, nxt)
                cursor = nxt
            if not has_more:
                break
        return total

    def poll_once(self):
        convs, _, _ = self.fetcher.list_conversations("")
        total = 0
        for c in convs:
            conv_id = str(c.get("conversation_id") or "")
            if not conv_id:
                continue
            items, nxt, _ = self.fetcher.get_messages(conv_id, self.state.get_cursor(conv_id))
            total += self._emit_new(items)
            if nxt:
                self.state.set_cursor(conv_id, nxt)
        return total
