#!/usr/bin/env python3
"""Decode captured frontier (pbbp2) websocket frames.

  python scripts/decode-frontier.py browser-data/jakob/capture-*.jsonl

The web DM realtime channel is `wss://im-ws.tiktok.com/ws/v2`, length-delimited
protobuf ("pbbp2"). Each frame is a `Frame`:
  field 1  seqid            field 5  repeated header map {1:key, 2:value}
  field 2  logid            field 6  payload encoding ("gzip" or "")
  field 3  service          field 8  payload bytes (gunzip when field 6 == "gzip")
  field 4  method

This prints the field tree per frame, flags UTF-8 text runs in the payload, and
writes a frontier-fields.md skeleton to fill in by hand.
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

    _write_skeleton(frames)
    print("\nwrote frontier-fields.md")


def _write_skeleton(frames):
    methods_in = sorted({str(f.get("headers", {}).get("X-Method")) for f in frames["ws_in"]})
    with open("frontier-fields.md", "w") as f:
        f.write("# Frontier websocket (pbbp2) — decoded field map\n\n")
        f.write("Decoded from `browser-data/jakob` with `scripts/decode-frontier.py`. The\n"
                "account inbox was **empty** at capture, so these frames are the subscribe\n"
                "hello and the server's sync/cursor pushes — no DM message payload was\n"
                "captured. Field numbers below are `[Obs]` for the framing, `[Inf]` for the\n"
                "message-body layout (mirrored from the mobile IM SDK / webcast findings).\n\n")
        f.write("## Frame envelope [Obs]\n\n")
        f.write("| field | meaning |\n|---|---|\n"
                "| 1 | seqid |\n| 2 | logid (ns) |\n| 3 | service (33554513 IM, 20032 push) |\n"
                "| 4 | method (2 = subscribe) |\n| 5 | repeated header map {1:key, 2:value} |\n"
                "| 6 | payload encoding (\"gzip\" \\| \"\") |\n| 8 | payload (gunzip when field 6==gzip) |\n\n")
        f.write("## Outbound subscribe/hello [Obs]\n\n")
        if frames["ws_out"]:
            f.write("```\n" + str(frames["ws_out"][0].get("tree")) + "\n```\n\n")
        f.write("`body{1:{1:2, 2:device_id, 3:device_id, 4:ts}, 2:[repeated cursor {1:topic, 3:idx}]}`"
                " — the client subscribes to inbox topics with cursor positions.\n\n")
        f.write("## Inbound headers seen\n\n")
        f.write("X-Method values: " + ", ".join(m for m in methods_in if m != "None") + "\n\n")
        f.write("## Inbound payload (sync/cursor; DM body TODO) [Inf]\n\n")
        if frames["ws_in"]:
            f.write("```\n" + str(frames["ws_in"][0].get("tree"))[:1200] + "\n```\n\n")
        f.write("**TODO once a real DM is captured:** the message payload rides one\n"
                "`X-Method: PayloadRelatedMethod` frame; decode its inner body to\n"
                "`(conversation_id, message_id, ts, sender_id, text)` and pin the field\n"
                "numbers here. `x_frontier_msg_id` (header) + message id = the dedup key.\n")


if __name__ == "__main__":
    main(sys.argv[1:])
