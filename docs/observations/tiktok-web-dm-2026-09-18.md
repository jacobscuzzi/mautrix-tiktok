# TikTok web DM surface — logged-in observation (2026-09-18)

First **authenticated** web capture of the DM surface, from a real logged-in
session (account `Contact 34`, uid `0000000000000000000`, region DE /
EU-TTP2), driven headful via `bridge/cmd/capture.py`. Raw capture (secrets +
third-party PII) is gitignored under `browser-data/jakob/`; redacted fixtures are
in `tests/fixtures/web/`. This closes the body-shape gap the guest observation
(`tiktok-web-observation.md`) left open — for every endpoint that had data. The
**inbox was empty** ("No messages yet"), so the message-bearing endpoints are
present but return zero rows; those are documented gaps, not guesses.

Markers: **[Obs]** seen live in this capture, **[Inf]** inferred, **[Guess]**
unverified.

## Headline: the page is the signer (settled)

- **[Obs]** The in-page probe (`inpage_probe` records) fetched a DM URL twice
  inside the page: once with the signing params stripped, once as captured. Both
  returned **HTTP 200 with real JSON**, and the network trace shows TikTok's own
  `webmssdk` re-appended `X-Bogus`, `X-Gnarly`, `X-Dynosaur`, `msToken` to the
  stripped URL before it left the browser. So a `page.evaluate(() => fetch(...))`
  is signed by TikTok's code. `PageClient.call` (Task B) uses in-page fetch; the
  §2 fallback (replay the page's own navigation) is **not needed**.
- **[Obs]** `window.byted_acrawler` is present with `frontierSign`,
  `registerWsSigner`, `setTTWid`, `setTTWebid` — the WS `access_key` and frame
  signatures come from here.

## DM-relevant endpoints (authenticated)

| Purpose | Method + URL | Shape | Status |
|---|---|---|---|
| **Contacts** | `GET www.tiktok.com/api/im/spotlight/relation/` | `{followings:[{uid, unique_id, nickname, sec_uid, avatar_thumb:{uri,url_list}, follow_status, can_share_message}], has_more, min_time, max_time, next_req_count}` | **[Obs]** 17–18 rows, real |
| **Profile (other)** | `GET www.tiktok.com/tiktok/v1/im/user/profile/` | `{users:[{im_user_profile:{user_id_str, unique_id, nick_name, signature, avatars:{avatar_medium,avatar_small:{uri,url_list}}}}]}` | **[Obs]** real (a contact) |
| **Conversation list / inbox init** | `POST im-api.tiktok.com/v2/message/get_by_user_init` | protobuf `Response{1:code, 2:seq, 18:"OK"...}`; request field 15 = repeated device params | **[Obs]** 200 OK, **empty** |
| **Messages (per user combo)** | `POST im-api.tiktok.com/v1/message/get_by_user_combo` | protobuf | **[Obs]** 200, empty |
| **Session check** | `GET www.tiktok.com/passport/token/beat/web/` | `{data:{user_id_str, error_code:0, session_expired_description}, message:"success"}` | **[Obs]** the cheap is-alive call |
| **Realtime** | `wss://im-ws.tiktok.com/ws/v2` | pbbp2 frames (below) | **[Obs]** sync frames here; DM bodies captured live at G4 |

Notes on params (all HTTP DM calls): the signed query block carries `aid=1988`,
`device_id`, `msToken`, `X-Bogus`, `X-Gnarly`, `X-Dynosaur`, `verifyFp`, plus the
browser/device descriptor (`browser_*`, `screen_*`, `tz_name`, `priority_region`,
`region=DE`). The `im-api` host uses `application/x-protobuf`; everything on
`www.tiktok.com` is JSON.

## Frontier websocket (pbbp2) [Obs]

- URL `wss://im-ws.tiktok.com/ws/v2?fpid=...&service=33554513&method=2&aid=1988&
  device_id=...&access_key=<32hex>&device_platform=web`. Subprotocol `pbbp2`,
  `ping-interval=30`.
- **Frame envelope [Obs]** (see `frontier-fields.md`): field 1 seqid, 2 logid,
  3 service (`33554513` IM, `20032` push), 4 method (`2`=subscribe), 5 repeated
  header map `{1:key,2:value}`, 6 encoding (`"gzip"|""`), 8 payload (gunzip when 6
  is gzip).
- **Outbound subscribe [Obs]**: `body{1:{1:2, 2:conv/0, 3:device_id, 4:ts},
  2:[repeated cursor {1:topic, 3:idx}]}`; a larger frame lists ~10 per-topic
  message-id cursors — the inbox watermark ack.
- **Inbound [Obs]**: field 5 header carries `X-Method: PayloadRelatedMethod`,
  `X-PSM: bytedance.bsync.cache_svr`, `x_frontier_msg_id`, `x_frontier_traceid`;
  field 6 = `"gzip"`, field 8 gunzips to a per-topic sync list here (empty inbox).
  At G4 (2026-09-18) a live DM exchange over the same socket carried real message
  bodies: `f6->f500->f5 = Message{1 conv, 3 server_message_id, 4 create_time(us),
  7 sender, 8 content JSON}` — see `frontier-fields.md`.
- `x_frontier_msg_id` + message id is the dedup key (Task B `frontier.py`).

## In-page signing probe (the G1 settle) [Obs]

```
requested (stripped):  /api/im/spotlight/relation/?...&count=90   (no signature params)
actually sent:         same URL + &X-Dynosaur=...&msToken=...&X-Bogus=1&X-Gnarly=...
status:                200, body = real followings JSON
```
Conclusion: **in-page fetch is re-signed by the page.** `[Obs]`, not `[Guess]`.

## Required fixture checklist (G2 self-approval)

| Required kind | Present? | Fixture / gap |
|---|---|---|
| `conv_list` | partial | `conv_list.json` — real init envelope, **empty inbox** (0 conversations) |
| `messages_*` with pagination | partial | REST message list still empty at capture (synthetic-tested); the realtime message BODY is now captured live via the frontier socket |
| `ws_inbound_dm` decoded to text | **yes (live, 2026-09-18)** | a friend's ~10 DMs captured at G4 and decoded to readable text; field map in `frontier-fields.md`; `ws_inbound_dm.json` fixture (real layout, neutral text) |
| `contacts` | **yes** | `contacts.json` — real, 18 followings with avatar URLs |
| `profile_other` with avatar URL | **yes** | `profile_other.json` — real, avatars present |
| (extra) `session_check` | yes | `session_check.json` — real `token/beat` |
| (extra) `ws_outbound` | yes | `ws_outbound.json` — real subscribe frame |
| (extra) `login_success` | yes | `login_success.json` |

**G2 result (updated 2026-09-18 after the G4 DM run):** contacts, profile_other,
and **ws_inbound_dm** are now captured live with real data and decoded; `conv_list`
and the REST `messages_*` list were empty at capture (synthetic-tested). The
frontier message field numbers are confirmed live, not inferred. The parsers are built and
tested against the real fixtures. The frontier message layout (`f6->f500->f5`) is
pinned in `frontier-fields.md` from the live G4 DM run.
