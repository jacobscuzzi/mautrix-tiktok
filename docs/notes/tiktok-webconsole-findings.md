# TikTok LIVE chat — transport findings

Observed on 2026-09-17 in Chrome on `www.tiktok.com/@<user>/live` (EU region, logged in). Everything below was verified by hooking `window.WebSocket` in the page and decoding the frames.

## 1. Transport

- No HTTP polling. `webcast/im/fetch` is never called on the web client.
- On room entry the page opens exactly one WebSocket:

```
wss://webcast-ws.eu.tiktok.com/webcast/im/ws_proxy/ws_reuse_supplement/?<params>
```

- The socket is reused across rooms inside the SPA ("ws_reuse_supplement"). A hook installed after the first room was entered saw nothing until the page entered a new room, so hook `WebSocket` before the page loads (userscript at `document-start`, or reload after patching).
- Client-side room switching does not trigger a new connection; a full room enter does.
- Video comes separately over FLV (`pull-flv-*.tiktokcdn.com/stage/stream-<id>_hd5.flv`), unrelated to chat.

### Query parameters seen on the WS URL

| param | value | note |
|---|---|---|
| `room_id` | `7686565533176630047` | from `webcast/room/enter` / `api-live/user/room` |
| `compress` | `gzip` | payload compression |
| `resp_content_type` | `protobuf` | |
| `heartbeat_duration` | `10000` | ms, client sends `hb` every 10 s |
| `identity` | `audience` | |
| `history_comment_count` | `6` | server replays last 6 comments on enter |
| `client_enter` | `1` | |
| `ws_direct` | `1` | |
| `sup_ws_ds_opt` | `1` | |
| `did_rule` | `3` | |
| `live_id` | `12` | |
| `last_rtt` | `241.69…` | |
| `aid` | `1988` | |
| `version_code` | `270000` | |
| `app_name`, `app_language`, `webcast_language`, `browser_*`, `screen_*`, `tz_name`, `device_platform=web`, `cookie_enabled` | fingerprint | |
| `update_version_code` | JWT-like signed token | signature |
| `X-Bogus` | 16-char signed value | signature |

The two signed values are the obstacle for a standalone client. Every other webcast HTTP call also carries `X-Bogus`, `X-Gnarly`, `X-Dynosaur`, `msToken`, `verifyFp`.

## 2. Frame format

All frames in both directions are protobuf (`WebcastPushFrame`). Layout as observed:

| field | type | meaning |
|---|---|---|
| 1 | varint | `seqId` (1, 2, 3, …) |
| 2 | varint | `logId` (large, used in acks) |
| 3 | string | service (`9528`-style ids, not needed) |
| 4 | string | method |
| 5 | repeated message `{1: key, 2: value}` | headers |
| 6 | string | payload encoding, always `pb` |
| 7 | string | payload type |
| 8 | bytes | payload |

Payload types seen from the server: `im_enter_room_resp` (first frame), `msg`, `hb`.

Headers seen:

```
compress_type=gzip | none
im_cursor=1789679230710_7686613763180497737_1_1_1789679228811_0
im_register_log_id=2026091721…
with_sys_msg=0
```

When `compress_type=gzip`, field 8 is a gzip stream; decompress before parsing. In the browser `DecompressionStream('gzip')` is enough.

### Client → server

Raw hex of the first sends:

```
32 02 70 62  3a 02 68 62  42 0a 08 …            -> {6:"pb", 7:"hb",            8:<bytes>}
32 02 70 62  3a 0d 69 6d 5f 65 6e 74 65 72 …    -> {6:"pb", 7:"im_enter_room", 8:<bytes>}
10 <logId>   32 02 70 62  3a 03 61 63 6b  42 01 2d -> {2:logId, 6:"pb", 7:"ack", 8:"-"}
```

So the client:

1. sends `im_enter_room` once (payload: room id + cursor),
2. sends `hb` every `heartbeat_duration` ms,
3. sends `ack` with field 2 = the received frame's `logId` and field 8 = `"-"` for every server frame.

If you hold the socket yourself, do the acks and heartbeats or the server closes the connection.

## 3. Decoded payload (`msg` frames)

Field 8 of a `msg` frame, after gunzip, is a `WebcastResponse`:

| field | meaning |
|---|---|
| 1 (repeated) | `Message { 1: method (string), 2: payload (bytes), 3: msgId, 4: msgType, … }` |
| 2 | cursor |
| 3 | fetch interval |
| others | internal ext, heartbeat, etc. |

Message methods counted over ~90 s in one room (~630 viewers):

| method | count |
|---|---|
| WebcastMemberMessage (joins) | 123 |
| WebcastLikeMessage | 49 |
| WebcastRoomUserSeqMessage (viewer count / top viewers) | 33 |
| WebcastChatMessage | 28 |
| WebcastGiftMessage | 7 |
| WebcastViewerPicksUpdateMessage | 7 |
| WebcastLinkMicFanTicketMethod | 6 |
| WebcastLinkMicMethod | 5 |
| WebcastSocialMessage (follow/share) | 5 |
| WebcastGiftPanelUpdateMessage | 4 |
| WebcastLinkLayerMessage, WebcastLinkMessage, WebcastGuideMessage | 1 each |

### WebcastChatMessage

Top-level fields present: `1, 2, 3, 14, 18, 19 (repeated), 20, 21, 23, 24`.

| field | meaning |
|---|---|
| 1 | common header (msgId, roomId, timestamps) |
| 2 | `User` |
| 3 | comment text (UTF-8) |
| 19 (repeated) | emote / badge list |

`User` fields that matter:

| field | meaning |
|---|---|
| 1 | user id (uint64, exceeds JS safe int — read as BigInt or string) |
| 3 | nickname (display name) |
| 38 | uniqueId (`@handle`) |

Verified sample decoded from the stream:

```
c4ldo8 (C4LDO)               -> "Song name"
zachooh (zachooh)            -> "@Chef Jacques you have just reached Level: 9!"
iskydl (Silencooh)           -> "we larping ?"
bequietappidontcare (Chef Jacques) -> "GG"
```

## 4. DOM side (fallback)

The rendered chat is well-labelled with `data-e2e` attributes:

```
[data-e2e="live-chat-container"]     chat list root
[data-e2e="chat-message"]            one comment
[data-e2e="message-owner-name"]      nickname inside a comment
[data-e2e="enter-message"]           "X ist beigetreten"
[data-e2e="room-chat-input-field"]   input box
[data-e2e="room-header-anchor-name"] streamer
[data-e2e="room-header-like-count"]  likes
```

Limits: TikTok keeps only ~6 comment nodes in the DOM and recycles them, so a `MutationObserver` on the container catches new comments but nothing else (no gifts, likes, joins, user ids). React can also swap the whole container on room change, so re-query the root after navigation.

## 5. Working in-page decoder

This is the code that produced the results above. Paste it in the console **before** entering a room (or reload afterwards and enter again). It prints every comment and keeps them in `window.__chatLog`.

```js
(() => {
  const td = new TextDecoder();
  const str = u8 => u8 ? td.decode(u8) : '';

  function pb(buf) {
    const b = buf instanceof Uint8Array ? buf : new Uint8Array(buf);
    const out = [];
    let i = 0;
    const varint = () => {
      let r = 0, s = 1;
      for (;;) { const x = b[i++]; r += (x & 0x7f) * s; if (!(x & 0x80)) return r; s *= 128; }
    };
    while (i < b.length) {
      const tag = varint(), f = Math.floor(tag / 8), wt = tag & 7;
      if (wt === 0) out.push({ f, v: varint() });
      else if (wt === 2) { const n = varint(); out.push({ f, v: b.slice(i, i + n) }); i += n; }
      else if (wt === 1) { out.push({ f, v: b.slice(i, i + 8) }); i += 8; }
      else if (wt === 5) { out.push({ f, v: b.slice(i, i + 4) }); i += 4; }
      else throw new Error('wire type ' + wt);
    }
    return out;
  }
  const get = (fields, f) => fields.find(x => x.f === f)?.v;

  async function gunzip(u8) {
    const ds = new DecompressionStream('gzip');
    const w = ds.writable.getWriter(); w.write(u8); w.close();
    return new Uint8Array(await new Response(ds.readable).arrayBuffer());
  }

  window.__chatLog = [];

  async function onFrame(data) {
    const frame = pb(data);
    if (str(get(frame, 7)) !== 'msg') return;
    const gz = frame.filter(x => x.f === 5).some(h => {
      const kv = pb(h.v);
      return str(get(kv, 1)) === 'compress_type' && str(get(kv, 2)) === 'gzip';
    });
    const payload = get(frame, 8);
    if (!payload) return;
    const resp = pb(gz ? await gunzip(payload) : payload);
    for (const m of resp.filter(x => x.f === 1)) {
      const msg = pb(m.v);
      if (str(get(msg, 1)) !== 'WebcastChatMessage') continue;
      const body = pb(get(msg, 2));
      const user = pb(get(body, 2) || new Uint8Array());
      const rec = {
        t: new Date().toISOString(),
        uniqueId: str(get(user, 38)),
        nickname: str(get(user, 3)),
        comment: str(get(body, 3)),
      };
      window.__chatLog.push(rec);
      console.log('%c[chat]', 'color:#25f4ee', rec.uniqueId, '->', rec.comment);
    }
  }

  const Orig = window.WebSocket;
  function Hooked(url, protocols) {
    const ws = protocols === undefined ? new Orig(url) : new Orig(url, protocols);
    if (String(url).includes('webcast/im/ws_proxy')) {
      ws.addEventListener('message', ev => {
        const d = ev.data;
        if (d instanceof Blob) d.arrayBuffer().then(onFrame);
        else onFrame(d);
      });
    }
    return ws;
  }
  Hooked.prototype = Orig.prototype;
  Object.setPrototypeOf(Hooked, Orig);
  window.WebSocket = Hooked;
})();
```

Swap the `'WebcastChatMessage'` check for `WebcastGiftMessage`, `WebcastLikeMessage`, etc. to log other events; the outer framing is identical.

## 6. Options for a scraper

**In-browser (no signing needed).** Userscript (Tampermonkey, `@run-at document-start`) or a small extension that installs the hook above and ships `__chatLog` to a local endpoint via `fetch`. The page does all the auth, signing and heartbeats. Downside: needs a browser tab per room.

**Headless / standalone.** Requires reproducing the signed WS URL (`X-Bogus`, `update_version_code`) plus the `webcast/room/enter` call for the room id and initial cursor, then the `im_enter_room` / `ack` / `hb` dance. Existing libraries already do this and ship the full `.proto` files:

- `TikTokLive` (Python, github.com/isaackogan/TikTokLive)
- `tiktok-live-connector` (Node, github.com/zerodytrash/TikTok-Live-Connector)

Both rely on an external signing service (`tiktok.eulerstream.com`) for the URL signature; that is the realistic route if you don't want to reverse `X-Bogus` yourself.

**DOM observer.** Cheapest to write, but only comments and only what's currently rendered. Fine for a quick demo, not for a pipeline.

## 7. Misc

- Console noise on the live page: `WebAssembly.instantiate(): expected magic word` from a background-colour helper (`bg color computed error`) and `[i18n] missing key`. Unrelated to chat.
- The `webcast/room/check_alive/` endpoint (GET, `room_ids=` comma list) tells you which rooms are still live without opening a socket — useful for a poller.
- User ids arrive as uint64 and overflow `Number`; decode them as BigInt or strings if you need them exact.
