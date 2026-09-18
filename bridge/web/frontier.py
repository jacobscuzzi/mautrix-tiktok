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


def _message_from_inner(inner, header_msg_id):
    """Map one inner message body -> normalized dict, or None if it has no id/text.

    [Inf] field numbers mirrored from the mobile IM SDK Message:
      1 conversation_id/short | 2 sender/server_message_id | 4 create_time |
      6 content (JSON string). Isolated so a live DM re-pins these in one place.
    """
    m = inner if isinstance(inner, dict) else proto.decode_tree(inner)
    conv = _first(m, 1)
    mid = _first(m, 4) or _first(m, 2)
    sender = _first(m, 3) or _first(m, 2)
    ts = _first(m, 5) or _first(m, 6)
    content = _first(m, 7) or _first(m, 8)
    text = content if isinstance(content, str) else None
    if mid is None:
        return None
    return {
        "conversation_id": str(conv) if conv is not None else "",
        "server_message_id": str(mid),
        "sender": str(sender) if sender is not None else "",
        "create_time": int(ts) if isinstance(ts, int) else 0,
        "content": text or "",
        "raw_ref": header_msg_id,
    }


def messages_from_frame(raw):
    """Yield normalized message dicts from a frame. Empty for sync/heartbeat frames.

    Never raises on an unknown shape; logs at debug and yields nothing.
    """
    try:
        frame = decode_frame(raw)
    except Exception as e:  # pragma: no cover - defensive
        log.debug("undecodable frontier frame: %s", e)
        return
    method = frame["headers"].get("X-Method")
    if method and method not in DM_METHODS:
        log.debug("frontier method %s dropped", method)
        return
    msg_id = frame["headers"].get("x_frontier_msg_id")
    if not frame["body"]:
        return
    try:
        tree = proto.decode_tree(frame["body"])
    except Exception as e:
        log.debug("frontier body decode failed: %s", e)
        return
    # field 2 of the body is the repeated per-topic / per-message list.
    for entry in tree.get(2, []):
        if not isinstance(entry, dict):
            continue
        # a message rides field 7 (nested body) in the sync entries; a pure cursor
        # entry has no field 7 -> yields nothing (sync/heartbeat).
        payloads = entry.get(7) or []
        for p in payloads:
            msg = _message_from_inner(p, msg_id)
            if msg and msg.get("content"):
                yield msg


def dedup_key(frame_headers, message_id):
    return f"{frame_headers.get('x_frontier_msg_id', '')}:{message_id}"
