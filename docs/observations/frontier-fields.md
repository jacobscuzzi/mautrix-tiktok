# Frontier websocket (pbbp2) — decoded field map

Decoded from real captures with `scripts/decode-frontier.py`. The frame envelope is
`[Obs]` from the first capture; the **message body is now `[Obs]` too**, confirmed
from a live DM exchange captured at G4 on 2026-09-18 (a friend sent ~10 messages).

## Frame envelope [Obs]

| field | meaning |
|---|---|
| 1 | seqid |
| 2 | logid (ns) |
| 3 | service (33554513 IM subscribe, 20032 push/message) |
| 4 | method (2 = subscribe) |
| 5 | repeated header map {1:key, 2:value} |
| 6 | payload encoding ("gzip" \| "") |
| 8 | payload (gunzip when field 6 == "gzip") |

Inbound message frames carry header `X-Method: PayloadRelatedMethod` (or none),
`X-PSM: bytedance.bsync.cache_svr`, `x_frontier_msg_id`, `x_frontier_traceid`.
`x_frontier_msg_id` + `server_message_id` is the dedup key.

## Message body [Obs] (confirmed live 2026-09-18)

Gunzipped body -> `f6 -> f500 (repeated envelope) -> f5 = Message`:

```
body
  f1: 500                       (marker)
  f6:
    f500 (repeated):            one per delivered message
      f2: conversation_id       "0:1:<uidA>:<uidB>"
      f5: Message
        f1: conversation_id     "0:1:<uidA>:<uidB>"
        f2: conversation_type   (1 = 1:1 DM)
        f3: server_message_id    <- dedup / ordering id
        f4: create_time         microseconds (divide by 1000 for ms)
        f7: sender_id           the actual sender's uid (self or peer)
        f8: content             JSON string {"aweType":0,"text":"..."}
        f9 (repeated): {f1:key, f2:value}  extended props
                       (client_message_id, im_client_send_msg_time, is_stranger, ...)
        f14: sender sec_uid
```

Notes:
- **Text** is `json.loads(f8)["text"]`. `aweType` 0 = plain text; 700 = text with
  effect; a sticker/share has no `text`.
- A **read receipt / conversation command** rides the same shape but its `f8` is
  `{"command_type":1,"read_index":...}` — parsed and **skipped**, not emitted.
- **Sender attribution verified**: in the live capture, self-sent messages carried
  `f7 = self uid` and the friend's carried `f7 = the friend's uid`, so `f7` is the
  true sender, not just the peer.

The parser is `bridge/web/frontier.py::messages_from_frame`; the committed
`tests/fixtures/web/ws_inbound_dm.json` reproduces this layout with neutral text
and pseudonymized ids (a friend's real messages stay only in the gitignored capture).

## Existing-conversation backlog: REST init [Obs] (confirmed live 2026-09-18)

The frontier does **not** replay history on connect (0 messages, 1 sync frame on a
populated account). The backlog comes from the page's own
`POST im-api.tiktok.com/v2/message/get_by_user_init` (protobuf), captured when
`/messages` loads:

```
body
  f1: 203   f4: "OK"   f15: own uid
  f6:
    f203:
      f1 (repeated): Message   <- IDENTICAL shape to the frontier Message
        f1 conversation_id | f3 server_message_id | f4 create_time (us) |
        f7 sender_id | f8 content JSON | f14 sender sec_uid
```

16 messages across several conversations were decoded live with this. Parser:
`bridge/web/frontier.py::messages_from_init_body`; the live bridge captures the
response on connect (`LiveBridge._capture_init` / `_ingest_init`) so existing chats
render immediately. Peer names/avatars for non-followed contacts come from
`GET /tiktok/v1/im/user/profile/?user_ids=["<uid>"]` (`WebProvider.get_profiles`).

## Older history: get_by_conversation [Obs] (confirmed live 2026-09-18)

Scrolling back further than the init backlog uses the page's history call
`POST im-api.tiktok.com/v1/message/get_by_conversation` (protobuf), which the bridge
replays through the in-page signer:

```
REQUEST  (same envelope as get_by_user_init; f1=301, f2=10004, f8=command)
  f8 -> f301: f1 conversation_id | f2 conversation_type(1) |
              f3 conversation_short_id (= message f5) | f4 1 |
              f5 cursor (microseconds; return messages OLDER than this) | f6 count
RESPONSE
  f6 -> f301: f1[] Message (same shape) | f2 next_cursor (us) | f3 has_more (1/0)
```

Parsers: `frontier.messages_from_conversation_body` + `frontier.conversation_cursor`;
the signed protobuf POST is `page.playwright_pb_poster`; `WebProvider.get_older_messages`
builds the request via `proto.encode_tree` from the captured init template. Verified
live: a conversation showing 5 backlog messages paged to 21 with one call (16 older,
has_more=0). `LiveBridge.load_older` / API `POST /api/load_older` / the wrapper's
"Load older" button drive it.
