#!/usr/bin/env python3
"""Decode captured frontier (pbbp2) websocket frames.

  .venv/bin/python scripts/decode-frontier.py 'browser-data/<name>/capture-*.jsonl'

The web DM realtime channel is `wss://im-ws.tiktok.com/ws/v2`, length-delimited
protobuf ("pbbp2"). Each frame is a `Frame`:
  field 1  seqid            field 5  repeated header map {1:key, 2:value}
  field 2  logid            field 6  payload encoding ("gzip" or "")
  field 3  service          field 8  payload bytes (gunzip when field 6 == "gzip")
  field 4  method

This prints the field tree per frame and flags UTF-8 text runs in the payload.
"""
import base64
import glob
import gzip
import json
import os
import re
import sys

sys.path.insert(0, os.getcwd())
from bridge import proto  # noqa: E402

TEXT_RUN = re.compile(rb"[\x20-\x7e]{4,}")


def _s(v):
    return v.decode("utf-8", "replace") if isinstance(v, (bytes, bytearray)) else v


def headers(fields):
    out = {}
    for h in fields.get(5, []):
        if isinstance(h, (bytes, bytearray)):
            kv = proto.decode_fields(h)
            k = kv.get(1, [b""])[0]
            v = kv.get(2, [b""])[0]
            out[_s(k)] = _s(v)
    return out


def decode_frame(raw):
    f = proto.decode_fields(raw)
    enc = _s(f.get(6, [b""])[0]) if 6 in f else ""
    payload = f.get(8, [b""])[0] if 8 in f else b""
    body = payload
    gzipped = False
    if enc == "gzip" or payload[:2] == b"\x1f\x8b":
        try:
            body = gzip.decompress(payload)
            gzipped = True
        except Exception:
            body = payload
    return {
        "seqid": f.get(1), "logid": f.get(2), "service": f.get(3),
        "method": f.get(4), "encoding": enc, "gzipped": gzipped,
        "headers": headers(f), "payload_len": len(payload), "body_len": len(body),
        "tree": proto.decode_tree(body) if body else {},
        "text_runs": [m.decode() for m in TEXT_RUN.findall(body)][:40],
    }


def main(argv):
    paths = []
    for pat in argv or ["browser-data/*/capture-*.jsonl"]:
        paths.extend(sorted(glob.glob(pat)) or [pat])
    frames = {"ws_out": [], "ws_in": []}
    for path in paths:
        for line in open(path, encoding="utf-8"):
            rec = json.loads(line)
            if rec.get("kind") in ("ws_in", "ws_out") and rec.get("data_b64"):
                try:
                    frames[rec["kind"]].append(decode_frame(base64.b64decode(rec["data_b64"])))
                except Exception as e:
                    frames[rec["kind"]].append({"error": str(e)})

    for direction in ("ws_out", "ws_in"):
        print(f"\n===== {direction}: {len(frames[direction])} frames =====")
        for i, fr in enumerate(frames[direction][:6]):
            print(f"\n[{direction} #{i}] service={fr.get('service')} method={fr.get('method')} "
                  f"enc={fr.get('encoding')!r} gz={fr.get('gzipped')} "
                  f"payload={fr.get('payload_len')}B body={fr.get('body_len')}B")
            if fr.get("headers"):
                print("  headers:", {k: v[:50] for k, v in fr["headers"].items()})
            print("  tree:", str(fr.get("tree"))[:600])
            if fr.get("text_runs"):
                print("  text:", fr["text_runs"][:10])



if __name__ == "__main__":
    main(sys.argv[1:])
