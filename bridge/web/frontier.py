"""Decode inbound frontier (pbbp2) websocket frames into normalized message tuples.

Frame envelope (confirmed live, see docs/observations/frontier-fields.md):
  1 seqid | 2 logid | 3 service | 4 method | 5 repeated header {1:key,2:value}
  6 encoding ("gzip"|"") | 8 payload (gunzip when field 6 == "gzip")

The account inbox was empty at capture, so no DM message body was seen; the inner
body layout below is [Inf] from the mobile IM SDK and is isolated here so a wrong
guess is one function to fix, never a raise. Unknown payload methods are logged at
debug and dropped, never raised. Dedup key = x_frontier_msg_id + message id.
"""
from __future__ import annotations

import gzip
import json
import logging

from .. import proto

log = logging.getLogger("bridge.web.frontier")

# X-Method values that carry DM payloads. PayloadRelatedMethod is what the capture
# showed for the sync channel; NewMessage/MessageByUser are [Inf] names to accept.
DM_METHODS = {"PayloadRelatedMethod", "NewMessage", "MessagePush", "MessageByUser"}


def _s(v):
    return v.decode("utf-8", "replace") if isinstance(v, (bytes, bytearray)) else v


def parse_headers(fields):
    out = {}
    for h in fields.get(5, []):
        if isinstance(h, (bytes, bytearray)):
            kv = proto.decode_fields(h)
            k = kv.get(1, [b""])[0]
            v = kv.get(2, [b""])[0]
            out[_s(k).lstrip(":")] = _s(v)
    return out


def decode_frame(raw):
    """Return the frame envelope: {service, method, headers, encoding, body(bytes)}."""
    f = proto.decode_fields(raw)
    enc = _s(f.get(6, [b""])[0]) if 6 in f else ""
    payload = f.get(8, [b""])[0] if 8 in f else b""
    body = payload
    if payload[:2] == b"\x1f\x8b" or enc == "gzip":
        try:
            body = gzip.decompress(payload)
        except Exception:
            body = payload
    return {
        "seqid": (f.get(1) or [None])[0],
        "service": (f.get(3) or [None])[0],
        "method": (f.get(4) or [None])[0],
        "headers": parse_headers(f),
        "encoding": enc,
        "body": body,
    }


def _first(d, idx):
    v = d.get(idx)
    return v[0] if v else None


def _content_text(content):
    """Return the text of a DM content JSON, or None for a command / non-text.

    Content is a JSON string like {"aweType":0,"text":"hi"}. A read-receipt /
    system frame carries {"command_type":1,...} and is not a message.
    """
    if not isinstance(content, str) or not content:
        return None, True
    if '"command_type"' in content:
        return None, False   # a conversation command (mark-read etc.), not a DM
    try:
        obj = json.loads(content)
    except ValueError:
        return content, True
    if isinstance(obj, dict):
        return obj.get("text"), True
    return None, True


def _message_from_inner(m, header_msg_id):
    """Map one inner Message body -> normalized dict, or None.

    Field numbers confirmed from a live capture (2026-09-18), see
    docs/observations/frontier-fields.md:
      f1 conversation_id | f3 server_message_id | f4 create_time (microseconds) |
      f7 sender_id | f8 content JSON | f14 sender sec_uid.
    """
    conv = _first(m, 1)
    mid = _first(m, 3)
    ts_us = _first(m, 4)
    short = _first(m, 5)       # conversation_short_id (needed to page history)
    sender = _first(m, 7)
    content = _first(m, 8)
    if mid is None:
        return None
    text, is_message = _content_text(content)
    if not is_message:
        return None            # command / read-receipt frame
    ts_ms = int(ts_us // 1000) if isinstance(ts_us, int) else 0
    return {
        "conversation_id": str(conv) if conv is not None else "",
        "server_message_id": str(mid),
        "sender": str(sender) if sender is not None else "",
        "create_time": ts_ms,
        "create_time_us": ts_us if isinstance(ts_us, int) else 0,
        "conv_short_id": short if isinstance(short, int) else None,
        "content": text or "",
        "raw_ref": header_msg_id,
    }


def _messages_in_block(tree, block_field):
    """body -> f6 -> f<block_field> -> f1[] = Message (init uses 203, history 301)."""
    for wrapper in tree.get(6, []):
        if not isinstance(wrapper, dict):
            continue
        for block in wrapper.get(block_field, []):
            if isinstance(block, dict):
                for m in block.get(1, []):
                    if isinstance(m, dict):
                        rec = _message_from_inner(m, None)
                        if rec:
                            yield rec


def messages_from_conversation_body(raw_body):
    """Older messages from `POST im-api.../v1/message/get_by_conversation` (protobuf).

    Layout confirmed live 2026-09-18: body -> f6 -> f301 -> f1[] = Message.
    """
    try:
        tree = proto.decode_tree(raw_body)
    except Exception as e:
        log.debug("conversation body decode failed: %s", e)
        return
    yield from _messages_in_block(tree, 301)


def conversation_cursor(raw_body):
    """Return (next_cursor_us, has_more) from a get_by_conversation response.

    The f301 block carries f2 = next cursor (oldest ts in this page, microseconds)
    and f3 = has_more (1 = older pages remain).
    """
    try:
        tree = proto.decode_tree(raw_body)
    except Exception:
        return None, False
    for wrapper in tree.get(6, []):
        if not isinstance(wrapper, dict):
            continue
        for block in wrapper.get(301, []):
            if isinstance(block, dict):
                nxt = (block.get(2) or [None])[0]
                more = (block.get(3) or [0])[0]
                return (nxt if isinstance(nxt, int) else None), bool(more)
    return None, False


def _messages_in_tree(tree):
    """Walk body -> f6 -> f500 (repeated envelope) -> f5 (the Message)."""
    for wrapper in tree.get(6, []):
        if not isinstance(wrapper, dict):
            continue
        for env in wrapper.get(500, []):
            if isinstance(env, dict):
                msg = _first(env, 5)
                if isinstance(msg, dict):
                    yield msg


def messages_from_frame(raw):
    """Yield normalized message dicts from a frame. Empty for sync/receipt frames.

    Never raises on an unknown shape; logs at debug and yields nothing.
    """
    try:
        frame = decode_frame(raw)
    except Exception as e:  # pragma: no cover - defensive
        log.debug("undecodable frontier frame: %s", e)
        return
    if not frame["body"]:
        return
    try:
        tree = proto.decode_tree(frame["body"])
    except Exception as e:
        log.debug("frontier body decode failed: %s", e)
        return
    msg_id = frame["headers"].get("x_frontier_msg_id")
    for m in _messages_in_tree(tree):
        rec = _message_from_inner(m, msg_id)
        if rec:
            yield rec


def dedup_key(frame_headers, message_id):
    return f"{frame_headers.get('x_frontier_msg_id', '')}:{message_id}"


def messages_from_init_body(raw_body):
    """Existing conversations' recent messages from the REST inbox init.

    `POST im-api.tiktok.com/v2/message/get_by_user_init` (protobuf, made by the
    /messages page itself on load) carries the backlog at
    `body -> f6 -> f203 -> f1[]`, each entry being the SAME Message shape as a
    frontier frame (f1 conv, f3 id, f4 ts_us, f7 sender, f8 content). Confirmed
    live 2026-09-18 (16 messages across several conversations). Yields normalized
    message dicts; never raises.
    """
    try:
        tree = proto.decode_tree(raw_body)
    except Exception as e:
        log.debug("init body decode failed: %s", e)
        return
    yield from _messages_in_block(tree, 203)
