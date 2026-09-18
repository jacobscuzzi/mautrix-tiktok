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
        "content": text or "",
        "raw_ref": header_msg_id,
    }


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
