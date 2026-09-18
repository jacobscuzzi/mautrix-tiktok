# Frontier websocket (pbbp2) — decoded field map

Decoded from `browser-data/jakob` with `scripts/decode-frontier.py`. The
account inbox was **empty** at capture, so these frames are the subscribe
hello and the server's sync/cursor pushes — no DM message payload was
captured. Field numbers below are `[Obs]` for the framing, `[Inf]` for the
message-body layout (mirrored from the mobile IM SDK / webcast findings).

## Frame envelope [Obs]

| field | meaning |
|---|---|
| 1 | seqid |
| 2 | logid (ns) |
| 3 | service (33554513 IM, 20032 push) |
| 4 | method (2 = subscribe) |
| 5 | repeated header map {1:key, 2:value} |
| 6 | payload encoding ("gzip" \| "") |
| 8 | payload (gunzip when field 6==gzip) |

## Outbound subscribe/hello [Obs]

```
{1: [{1: [2], 2: ['0'], 3: ['0000000000000000000'], 4: [1789707402245]}], 2: [{1: [3], 3: [0]}, {1: [3], 3: [1]}, {1: [3], 3: [2]}]}
```

`body{1:{1:2, 2:device_id, 3:device_id, 4:ts}, 2:[repeated cursor {1:topic, 3:idx}]}` — the client subscribes to inbox topics with cursor positions.

## Inbound headers seen

X-Method values: PayloadRelatedMethod

## Inbound payload (sync/cursor; DM body TODO) [Inf]

```
{1: [{1: [2], 2: ['0'], 3: ['0000000000000000000'], 4: [1789707401601], 5: [0], 6: [''], 7: [0], 8: [0]}], 2: [{1: [3], 2: [0], 3: [1], 4: [6983896500996660050], 5: [0], 6: [0], 8: [1], 9: [0], 255: ['']}, {1: [3], 2: [0], 3: [1], 4: [6983896500996669982], 5: [0], 6: [0], 8: [1], 9: [0], 255: ['']}, {1: [3], 2: [0], 3: [1], 4: [6983896585927131662], 5: [0], 6: [0], 8: [1], 9: [0], 255: ['']}, {1: [3], 2: [1], 3: [1], 4: [740570539769349401], 5: [0], 6: [0], 8: [1], 9: [0], 255: ['']}, {1: [3], 2: [0], 3: [0], 4: [7686729937617864712], 5: [0], 6: [0], 7: [{1: [{1: [0]}], 2: [{1: [1], 2: [0], 3: ['']}], 3: [0], 4: [0], 5: [0], 6: [''], 7: [''], 255: [0]}, {1: [{1: [0]}], 2: [{1: [2], 2: [0], 3: ['']}], 3: [0], 4: [0], 5: [0], 6: [''], 7: [''], 255: [0]}, {1: [{1: [0]}], 2: [{1: [3], 2: [1], 3: ['']}], 3: [0], 4: [0], 5: [0], 6: [''], 7: [''], 255: [0]}, {1: [{1: [0]}], 2: [{1: [8], 2: [0], 3: ['']}], 3: [0], 4: [0], 5: [0], 6: [''], 7: [''], 255: [0]}, {1: [{1: [0]}], 2: [{1: [10], 2: [0], 3: ['']}], 3: [0], 4: [0], 5: [0], 6: [''], 7: [''], 255: [0]}, {1: [{1: [0]}], 2: [{1: [11], 2: [0], 3: ['']}], 3: [0], 4: [0], 5: [0], 6: [''], 7: [''], 255: [0]}, {1: [{1: [0]}], 2: [{1: [12], 2
```

**TODO once a real DM is captured:** the message payload rides one
`X-Method: PayloadRelatedMethod` frame; decode its inner body to
`(conversation_id, message_id, ts, sender_id, text)` and pin the field
numbers here. `x_frontier_msg_id` (header) + message id = the dedup key.
