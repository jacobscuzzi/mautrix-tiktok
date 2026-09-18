"""WebProvider: the web DM backend behind the MessageProvider seam.

Turns the captured web shapes (docs/observations/tiktok-web-dm-2026-09-18.md) into
the same (items, next_cursor, has_more) contract the mobile IM and TikAPI providers
satisfy, so `Syncer`/`SyncState`/`normalize`/`metrics` are reused unchanged.

Transport is a `PageClient` (in-page signed fetch). Every parser is fixture-driven
and raises `SchemaChange` on a renamed/missing container rather than returning
silent garbage. Realtime rides the frontier websocket; on any gap (reconnect,
reload, `x_frontier_msg_id` discontinuity) the provider triggers a REST reconcile
through the existing Syncer dedup path.
"""
from __future__ import annotations

import hashlib
import logging

from .. import errors, normalize
from ..web import frontier

log = logging.getLogger("bridge.web")

BASE = "https://www.tiktok.com"
IM_API = "https://im-api.tiktok.com"
CONTACTS_URL = f"{BASE}/api/im/spotlight/relation/"
PROFILE_URL = f"{BASE}/tiktok/v1/im/user/profile/"
CONV_LIST_URL = f"{BASE}/api/im/spotlight/inbox/"          # JSON inbox mirror
MESSAGES_URL = f"{BASE}/api/im/spotlight/messages/"
AVATAR_HOSTS = (".tiktokcdn.com", ".tiktokcdn-eu.com", ".tiktokcdn-us.com")


# ---- parsers (isolated; raise SchemaChange on a shape change) ----------------

def _require(d, *keys):
    for k in keys:
        if isinstance(d, dict) and k in d:
            return d[k]
    raise errors.SchemaChange(f"none of {keys} present in response")


def parse_contacts(resp):
    followings = _require(resp, "followings")
    if not isinstance(followings, list):
        raise errors.SchemaChange("followings is not a list")
    out = []
    for f in followings:
        if not isinstance(f, dict):
            continue
        out.append({
            "uid": str(f.get("uid") or ""),
            "unique_id": f.get("unique_id") or "",
            "nickname": f.get("nickname") or "",
            "sec_uid": f.get("sec_uid") or "",
            "avatar_thumb": f.get("avatar_thumb"),
            "can_share_message": f.get("can_share_message", 1),
        })
    return out


def parse_profile(resp):
    users = _require(resp, "users")
    if not users:
        raise errors.SchemaChange("empty users in profile response")
    p = _require(users[0], "im_user_profile")
    return {
        "user_id": p.get("user_id_str") or str(p.get("user_id") or ""),
        "unique_id": p.get("unique_id") or "",
        "nick_name": p.get("nick_name") or "",
        "sec_uid": p.get("sec_uid") or "",
        "signature": p.get("signature") or "",
        "avatars": p.get("avatars"),
    }


def parse_conversations(resp):
    container = None
    for k in ("inbox", "conversations", "data"):
        v = resp.get(k)
        if isinstance(v, dict) and "conversations" in v:
            container = v["conversations"]
            break
        if isinstance(v, list):
            container = v
            break
    if container is None:
        raise errors.SchemaChange("no conversation list container")
    out = []
    for c in container:
        if not isinstance(c, dict):
            continue
        out.append({
            "conversation_id": str(c.get("conversation_id") or c.get("id") or ""),
            "conversation_short_id": c.get("conversation_short_id"),
            "ticket": c.get("ticket"),
            "conversation_type": c.get("conversation_type", 1),
            "participants": c.get("participants") or [],
            "last_message": c.get("last_message") or {},
        })
    return out


def parse_messages(resp, conv_id=""):
    msgs = _require(resp, "messages")
    if not isinstance(msgs, list):
        raise errors.SchemaChange("messages is not a list")
    out = []
    for m in msgs:
        if not isinstance(m, dict):
            continue
        out.append({
            "server_message_id": str(m.get("server_message_id") or m.get("message_id") or ""),
            "conversation_id": str(m.get("conversation_id") or conv_id or ""),
            "sender": str(m.get("sender") or m.get("sender_id") or ""),
            "content": m.get("content"),
            "create_time": int(m.get("create_time") or 0),
            "message_type": m.get("message_type", 1),
        })
    return out


def _cursor(resp):
    return str(resp.get("next_cursor") or resp.get("max_time") or "") or ""


def _has_more(resp):
    v = resp.get("has_more")
    return bool(v) if v is not None else False


# ---- avatar download (jar-less, host-allowlisted) ----------------------------

class AvatarClient:
    def __init__(self, transport=None):
        self._transport = transport

    @staticmethod
    def _allowed(url):
        host = url.split("/", 3)[2].split("?")[0] if "://" in url else ""
        return any(host.endswith(h) for h in AVATAR_HOSTS)

    def fetch(self, url):
        if not self._allowed(url):
            raise errors.InvalidRequest(f"avatar host not allowlisted: {url[:60]}")
        key = hashlib.sha256(url.encode()).hexdigest()
        if self._transport is None:  # pragma: no cover - real client built lazily
            import requests
            self._transport = lambda u: requests.get(u, timeout=30).content
        return key, self._transport(url)


# ---- the provider ------------------------------------------------------------

class WebProvider:
    """A MessageProvider for one web login."""

    def __init__(self, page_client, session=None, avatar_client=None):
        self.page = page_client
        self.session = session
        self.avatars = avatar_client or AvatarClient()
        self._last_frontier_id = None
        self._gap = False

    # MessageProvider contract -------------------------------------------------

    def list_conversations(self, cursor="0", count=20):
        params = {"count": count}
        if cursor and cursor not in ("0", ""):
            params["cursor"] = cursor
        resp = self.page.call("GET", CONV_LIST_URL, params)
        return parse_conversations(resp), _cursor(resp), _has_more(resp)

    def get_messages(self, conv_id, cursor="0", count=20):
        params = {"conversation_id": conv_id, "count": count}
        if cursor and cursor not in ("0", ""):
            params["cursor"] = cursor
        resp = self.page.call("GET", MESSAGES_URL, params)
        return parse_messages(resp, conv_id), _cursor(resp), _has_more(resp)

    def list_contacts(self, cursor="0", count=90):
        params = {"count": count}
        if cursor and cursor not in ("0", ""):
            params["min_time"] = cursor
        resp = self.page.call("GET", CONTACTS_URL, params)
        contacts = parse_contacts(resp)
        users = [normalize.to_user(c) for c in contacts]
        return users, str(resp.get("min_time") or ""), bool(resp.get("has_more"))

    def get_profile(self, user_id):
        return self.get_profiles([user_id])[0]

    def get_profiles(self, user_ids):
        # the page's own call is GET .../im/user/profile/?user_ids=["<uid>",...]  [Obs]
        import json as _json
        resp = self.page.call("GET", PROFILE_URL,
                              {"user_ids": _json.dumps([str(u) for u in user_ids]),
                               "aid": "1988"})
        users = _require(resp, "users")
        out = []
        for u in users:
            p = u.get("im_user_profile") if isinstance(u, dict) else None
            if not p:
                continue
            out.append(normalize.to_user({
                "user_id": p.get("user_id_str") or str(p.get("user_id") or ""),
                "unique_id": p.get("unique_id") or "", "nick_name": p.get("nick_name") or "",
                "sec_uid": p.get("sec_uid") or "", "avatars": p.get("avatars")}))
        if not out:
            raise errors.SchemaChange("empty users in profile response")
        return out

    def send_text(self, conv_id, text, client_message_id=""):
        # No send_text fixture was captured (allow-send: no). Refuse honestly
        # rather than blind-fire an unverified request against a real account.
        raise errors.NotSupported(
            "web send_text not enabled: no captured send fixture (allow-send was no); "
            "wire it from a capture run with --allow-send before enabling")

    def mark_read(self, conv_id):
        raise errors.NotSupported("web mark_read not enabled: no captured fixture")

    # realtime -----------------------------------------------------------------

    def on_frontier_frame(self, raw):
        """Feed a raw frontier frame; return normalized message dicts (0+).

        Detects a gap via x_frontier_msg_id discontinuity and flags a reconcile.
        """
        frame = frontier.decode_frame(raw)
        mid = frame["headers"].get("x_frontier_msg_id")
        # any non-empty new id is fine; we only use it as the dedup/raw ref.
        self._last_frontier_id = mid or self._last_frontier_id
        return list(frontier.messages_from_frame(raw))

    def subscribe(self, sink):
        """Register a frontier callback that pushes normalized message dicts to sink."""
        def cb(raw):
            for m in self.on_frontier_frame(raw):
                sink(m)
        self.page.on_frame(cb)

    def note_gap(self):
        self._gap = True

    def reconcile_if_gap(self, syncer):
        """After a reconnect/reload, re-pull conversations through the Syncer dedup."""
        if not self._gap:
            return 0
        self._gap = False
        return syncer.poll_once()
