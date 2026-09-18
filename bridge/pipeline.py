"""The raw layer everything else reads from: a SQLite store of logins, users,
threads and events.

`Syncer` writes here (idempotent upsert keyed on (thread_id, message_id)); the API
reads here. `stream_order` is the message timestamp in ms so a reader can page in
order. An optional webhook fires once per newly-ingested event, with a bounded
retry and a dead-letter file so a webhook outage never loses an event.

bridgev2 mapping: threads -> Portal, users -> Ghost, events -> Message, a login
row -> UserLogin.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS logins (
  login_id TEXT PRIMARY KEY, source TEXT, state TEXT, last_error TEXT,
  last_sync_ts INTEGER, password_login_used INTEGER DEFAULT 0, created_at INTEGER
);
CREATE TABLE IF NOT EXISTS users (
  user_id TEXT, login_id TEXT, handle TEXT, nickname TEXT, avatar_url TEXT,
  sec_uid TEXT, PRIMARY KEY (user_id, login_id)
);
CREATE TABLE IF NOT EXISTS threads (
  thread_id TEXT, login_id TEXT, thread_type TEXT, last_ts INTEGER,
  participants TEXT, PRIMARY KEY (thread_id, login_id)
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, thread_id TEXT, message_id TEXT,
  login_id TEXT, sender_id TEXT, ts INTEGER, stream_order INTEGER, kind TEXT,
  content TEXT, source TEXT, ingested_at INTEGER,
  UNIQUE (thread_id, message_id)
);
CREATE INDEX IF NOT EXISTS ix_events_thread ON events (thread_id, stream_order);
"""


class Pipeline:
    def __init__(self, db_path=":memory:", webhook=None, source="web"):
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.source = source
        self._webhook = webhook          # callable(dict) -> None, may raise
        self.dead_letter = (os.path.join(os.path.dirname(db_path), "webhook-deadletter.jsonl")
                            if db_path != ":memory:" else None)

    # ---- writes --------------------------------------------------------------

    def upsert_login(self, login_id, source=None, state="connecting",
                     last_error=None, password_login_used=False):
        self.db.execute(
            """INSERT INTO logins (login_id, source, state, last_error, last_sync_ts,
                                   password_login_used, created_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(login_id) DO UPDATE SET
                 state=excluded.state, last_error=excluded.last_error,
                 password_login_used=logins.password_login_used | excluded.password_login_used""",
            (login_id, source or self.source, state, last_error, None,
             int(bool(password_login_used)), int(time.time())))
        self.db.commit()

    def set_login_state(self, login_id, state, last_error=None, last_sync_ts=None):
        self.db.execute(
            "UPDATE logins SET state=?, last_error=?, last_sync_ts=COALESCE(?, last_sync_ts) "
            "WHERE login_id=?", (state, last_error, last_sync_ts, login_id))
        self.db.commit()

    def upsert_user(self, login_id, user):
        self.db.execute(
            """INSERT INTO users (user_id, login_id, handle, nickname, avatar_url, sec_uid)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(user_id, login_id) DO UPDATE SET
                 handle=excluded.handle, nickname=excluded.nickname,
                 avatar_url=COALESCE(excluded.avatar_url, users.avatar_url),
                 sec_uid=excluded.sec_uid""",
            (user.user_id, login_id, user.handle, user.display_name,
             user.avatar_url, user.sec_uid))
        self.db.commit()

    def upsert_thread(self, login_id, thread):
        self.db.execute(
            """INSERT INTO threads (thread_id, login_id, thread_type, last_ts, participants)
               VALUES (?,?,?,?,?)
               ON CONFLICT(thread_id, login_id) DO UPDATE SET
                 thread_type=excluded.thread_type, last_ts=MAX(threads.last_ts, excluded.last_ts),
                 participants=excluded.participants""",
            (thread.conversation_id, login_id, thread.thread_type, thread.last_ts,
             json.dumps(thread.participants)))
        self.db.commit()

    def ingest_event(self, event, login_id="default"):
        """Idempotent upsert of one normalized Event. Returns True if newly inserted."""
        cur = self.db.execute(
            """INSERT OR IGNORE INTO events
               (thread_id, message_id, login_id, sender_id, ts, stream_order, kind,
                content, source, ingested_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (event.conversation_id, event.message_id, login_id, event.sender_id,
             event.timestamp_ms, event.timestamp_ms, event.kind, event.text,
             self.source, int(time.time() * 1000)))
        self.db.commit()
        if cur.rowcount:
            self._fire_webhook(event, login_id)
            return True
        return False

    def make_emit(self, login_id="default"):
        """A one-arg emit(event) closure for Syncer(fetcher, state, emit)."""
        return lambda event: self.ingest_event(event, login_id)

    # ---- webhook (optional) --------------------------------------------------

    def _fire_webhook(self, event, login_id):
        if not self._webhook:
            return
        payload = {"login_id": login_id, "thread_id": event.conversation_id,
                   "message_id": event.message_id, "sender_id": event.sender_id,
                   "ts": event.timestamp_ms, "kind": event.kind, "text": event.text}
        for attempt in range(3):
            try:
                self._webhook(payload)
                return
            except Exception:
                time.sleep(0)  # bounded, no real sleep in tests
        if self.dead_letter:
            with open(self.dead_letter, "a") as f:
                f.write(json.dumps(payload) + "\n")

    # ---- reads ---------------------------------------------------------------

    def list_threads(self, login_id):
        rows = self.db.execute(
            "SELECT thread_id, thread_type, last_ts, participants FROM threads "
            "WHERE login_id=? ORDER BY last_ts DESC", (login_id,)).fetchall()
        return [dict(r) for r in rows]

    def list_contacts(self, login_id):
        rows = self.db.execute(
            "SELECT user_id, handle, nickname, avatar_url, sec_uid FROM users "
            "WHERE login_id=? ORDER BY nickname", (login_id,)).fetchall()
        return [dict(r) for r in rows]

    def get_messages(self, thread_id, cursor=0, limit=50):
        rows = self.db.execute(
            "SELECT message_id, sender_id, ts, stream_order, kind, content FROM events "
            "WHERE thread_id=? AND stream_order > ? ORDER BY stream_order LIMIT ?",
            (thread_id, int(cursor or 0), limit)).fetchall()
        return [dict(r) for r in rows]

    def last_events(self, n=5, login_id=None):
        if login_id:
            rows = self.db.execute(
                "SELECT * FROM events WHERE login_id=? ORDER BY id DESC LIMIT ?",
                (login_id, n)).fetchall()
        else:
            rows = self.db.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?",
                                   (n,)).fetchall()
        return [dict(r) for r in rows]

    def logins(self):
        return [dict(r) for r in self.db.execute("SELECT * FROM logins").fetchall()]

    def close(self):
        self.db.close()
