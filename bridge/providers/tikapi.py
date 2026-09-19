"""TikApiProvider — the "buy" option, delegating DMs to TikAPI (tikapi.io).

Implements the same `MessageProvider` contract as the native client
(bridge/provider.py), so `Syncer`, `SyncState`, `normalize`, and `metrics` are
reused unchanged. What this swaps out is the whole self-signed stack: no signer,
no device fingerprint, no protobuf, no proxy management — TikAPI owns all of that
server-side. In exchange we take a paid vendor dependency and a polling-only
inbound path.

Confirmed from TikAPI's OpenAPI docs (2026-09-17; the trade-off is in DESIGN.md §1):

- Base URL            https://api.tikapi.io   (sandbox https://sandbox.tikapi.io)
- Auth headers        X-API-KEY (developer key) + X-ACCOUNT-KEY (per-user OAuth token)
- List conversations  GET  /user/conversations
- Message history     GET  /user/messages?conversation_id=..&conversation_short_id=..
- Send text           POST /user/message/send  {text, conversation_id, conversation_short_id, ticket}
- Session check       GET  /user/session/check
- Notifications       GET  /user/notifications
- Session expiry      HTTP 428 (statusCode 8)  -> AuthError (needs-reauth)
- TikTok upstream err  HTTP 503 "TikTok Gateway Error" -> Transient
- Rate limit          HTTP 429 -> RateLimited

[Inf] marks fields whose exact JSON key we inferred from TikTok's native IM
shape; they are isolated in `_parse_*` and must be reconciled against one live
authenticated response (the one thing that needs a real API key). The transport,
auth, error mapping, pagination, ticket handling, and idempotency logic are all
real and unit-tested against fixtures.
"""
import json

from .. import errors
from ..normalize import extract_text

BASE_URL = "https://api.tikapi.io"
SANDBOX_URL = "https://sandbox.tikapi.io"


def _requests_transport():
    import requests

    sess = requests.Session()

    def call(method, url, headers, body):
        r = sess.request(method, url, headers=headers, data=body, timeout=30)
        return r.status_code, dict(r.headers), r.content

    return call


# TikTok DM `content` is a JSON string like {"text":"hi","aweType":0}; the shared
# normalizer turns a dict, a JSON string or plain text into plain text.
_extract_text = extract_text


class TikApiProvider:
    """A MessageProvider backed by TikAPI for one authorized TikTok account."""

    def __init__(self, api_key, account_key, base_url=BASE_URL, transport=None):
        if not api_key or not account_key:
            raise ValueError("api_key and account_key are required")
        self._api_key = api_key
        self._account_key = account_key
        self.base = base_url.rstrip("/")
        self._transport = transport or _requests_transport()
        # conversation_id -> {"short_id":..., "ticket":...}; populated by
        # list_conversations and required to send (TikAPI has no send-by-userid).
        self._conv_meta = {}
        # client_message_id set of in-flight/sent ids, for local double-send guard
        # (TikAPI does NOT echo a client message id, so cross-process idempotency
        # must still reconcile against history — see send_text docstring).
        self._sent_cmids = set()

    # ---- transport / error mapping -------------------------------------------

    def _headers(self):
        return {
            "X-API-KEY": self._api_key,
            "X-ACCOUNT-KEY": self._account_key,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _request(self, method, path, params=None, json_body=None):
        url = f"{self.base}{path}"
        if params:
            from urllib.parse import urlencode

            url = f"{url}?{urlencode({k: v for k, v in params.items() if v is not None})}"
        body = json.dumps(json_body).encode() if json_body is not None else b""
        status, _headers, content = self._transport(method, url, self._headers(), body)
        return self._handle(status, content)

    def _handle(self, status, content):
        if status == 200:
            try:
                return json.loads(content or b"{}")
            except ValueError:
                raise errors.Transient("tikapi: non-json 200 body")
        # Map TikAPI/HTTP statuses onto the bridge's existing taxonomy so the
        # sync engine and metrics treat both providers identically.
        detail = self._err_detail(content)
        if status == 428:  # session expired / re-authorization required
            raise errors.AuthError(f"tikapi 428 reauth required: {detail}", code=428)
        if status == 401 or status == 403:
            raise errors.AuthError(f"tikapi {status}: {detail}", code=status)
        if status == 429:
            raise errors.RateLimited(f"tikapi 429: {detail}", code=429)
        if status == 503:  # "TikTok Gateway Error"
            raise errors.Transient(f"tikapi 503 upstream: {detail}", code=503)
        if status >= 500:
            raise errors.Transient(f"tikapi {status}: {detail}", code=status)
        raise errors.InvalidRequest(f"tikapi {status}: {detail}", code=status)

    @staticmethod
    def _err_detail(content):
        try:
            d = json.loads(content or b"{}")
            return d.get("message") or d.get("detail") or str(d)[:200]
        except ValueError:
            return (content or b"").decode("utf-8", "replace")[:200]

    # ---- MessageProvider ------------------------------------------------------

    def list_conversations(self, cursor="0", count=20):
        params = {"count": count}
        if cursor and cursor not in ("0", ""):
            params["cursor"] = cursor
        resp = self._request("GET", "/user/conversations", params=params)
        convs = self._parse_conversations(resp)
        for c in convs:
            cid = c.get("conversation_id")
            if cid:
                self._conv_meta[cid] = {
                    "short_id": c.get("conversation_short_id"),
                    "ticket": c.get("ticket"),
                }
        return convs, self._next_cursor(resp), self._has_more(resp)

    def get_messages(self, conv_id, cursor="0", count=20):
        meta = self._conv_meta.get(conv_id, {})
        params = {
            "conversation_id": conv_id,
            "conversation_short_id": meta.get("short_id"),
            "count": count,
        }
        if cursor and cursor not in ("0", ""):
            params["cursor"] = cursor
        resp = self._request("GET", "/user/messages", params=params)
        # capture/refresh the ticket if the messages response carries it
        t = self._find(resp, "ticket")
        if t and conv_id in self._conv_meta:
            self._conv_meta[conv_id]["ticket"] = t
        return self._parse_messages(resp, conv_id), self._next_cursor(resp), self._has_more(resp)

    def send_text(self, conv_id, text, client_message_id=""):
        """Send a text DM.

        TikAPI requires conversation_short_id + ticket, obtained by first reading
        the conversation (list_conversations / get_messages populate the cache).
        TikAPI does NOT accept or echo a client message id, so this method keeps a
        local in-process guard against immediate double-sends; durable idempotency
        after an ambiguous result (timeout) must still reconcile against the next
        history fetch, exactly as bridge/sync.py already does by message id. We
        never blind-replay a send whose result is unknown.
        """
        if client_message_id and client_message_id in self._sent_cmids:
            raise errors.InvalidRequest(
                f"refusing duplicate send for client_message_id {client_message_id}"
            )
        meta = self._conv_meta.get(conv_id)
        if not meta or not meta.get("ticket") or not meta.get("short_id"):
            # resolve by reading the conversation list once
            self.list_conversations()
            meta = self._conv_meta.get(conv_id)
        if not meta or not meta.get("ticket") or not meta.get("short_id"):
            raise errors.InvalidRequest(
                f"no ticket/short_id for conversation {conv_id}; cannot send via TikAPI"
            )
        body = {
            "text": text,
            "conversation_id": conv_id,
            "conversation_short_id": meta["short_id"],
            "ticket": meta["ticket"],
        }
        resp = self._request("POST", "/user/message/send", json_body=body)
        if client_message_id:
            self._sent_cmids.add(client_message_id)
        return {
            "server_message_id": self._find(resp, "message_id")
            or self._find(resp, "server_message_id"),
            "client_message_id": client_message_id,
            "raw": resp,
        }

    def mark_read(self, conv_id):
        # TikAPI exposes conversation-request accept/delete but no documented
        # mark-read endpoint. Surface that honestly rather than silently no-op.
        raise errors.NotSupported("tikapi: mark_read not supported by vendor API")

    def check_session(self):
        """True if the account session is still valid (GET /user/session/check)."""
        try:
            self._request("GET", "/user/session/check")
            return True
        except errors.AuthError:
            return False

    # ---- parsing (isolated; [Inf] shapes to confirm on one live response) -----

    def _parse_conversations(self, resp):
        raw = self._conversation_list(resp)
        out = []
        for c in raw:
            if not isinstance(c, dict):
                continue
            out.append(
                {
                    "conversation_id": str(
                        c.get("conversation_id") or c.get("id") or ""
                    ),
                    "conversation_short_id": self._str_or_none(
                        c.get("conversation_short_id") or c.get("short_id")
                    ),
                    "ticket": c.get("ticket"),
                    "participants": c.get("participants") or c.get("members") or [],
                    "is_stranger": bool(
                        c.get("is_stranger") or c.get("is_request") or False
                    ),
                }
            )
        return out

    def _parse_messages(self, resp, conv_id):
        raw = self._message_list(resp)
        out = []
        for m in raw:
            if not isinstance(m, dict):
                continue
            out.append(
                {
                    "server_message_id": str(
                        m.get("server_message_id")
                        or m.get("message_id")
                        or m.get("id")
                        or ""
                    ),
                    "conversation_id": str(m.get("conversation_id") or conv_id or ""),
                    "sender": str(m.get("sender") or m.get("sender_id") or ""),
                    "content": _extract_text(m.get("content") or m.get("text")),
                    "create_time": int(
                        m.get("create_time") or m.get("timestamp") or 0
                    ),
                }
            )
        return out

    # ---- response-envelope helpers (tolerant of TikAPI's wrapping) ------------

    @staticmethod
    def _conversation_list(resp):
        for k in ("conversations", "inbox", "data", "items"):
            v = resp.get(k)
            if isinstance(v, list):
                return v
            if isinstance(v, dict):
                # a map keyed by conversation id
                return list(v.values())
        return []

    @staticmethod
    def _message_list(resp):
        for k in ("messages", "data", "items"):
            v = resp.get(k)
            if isinstance(v, list):
                return v
        return []

    @staticmethod
    def _next_cursor(resp):
        return str(resp.get("nextCursor") or resp.get("cursor") or "") or ""

    @staticmethod
    def _has_more(resp):
        v = resp.get("hasMore")
        if v is None:
            v = resp.get("has_more")
        return bool(v)

    @staticmethod
    def _str_or_none(v):
        return str(v) if v is not None else None

    @staticmethod
    def _find(resp, key):
        """Shallow search for a key in the response or its first-level dicts."""
        if not isinstance(resp, dict):
            return None
        if key in resp and resp[key]:
            return resp[key]
        for v in resp.values():
            if isinstance(v, dict) and v.get(key):
                return v[key]
            if isinstance(v, list) and v and isinstance(v[0], dict) and v[0].get(key):
                return v[0][key]
        return None
