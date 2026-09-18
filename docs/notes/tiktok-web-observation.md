# TikTok Web — live observation (2026-09-17)

Unauthenticated (guest) session against `www.tiktok.com`, driven headless with a
real Chromium and a Windows Chrome 140 user agent, from a European (DE) datacenter
IP. Steps visited: `/`, `/foryou`, `/login`, `/login/qrcode`, `/messages`,
`/explore`. Cookie banners were declined. Raw capture, including every request,
WebSocket frame, console line, cookie snapshot and hydration blob, is in
`tiktok-web-capture-2026-09-17.json` (1559 requests, 3 websockets, 555 console
lines) next to this file. Epistemic markers match DESIGN.md: **[Obs]** observed
live here, **[Inf]** inferred, **[Guess]** unverified.

## Headline: the web DM surface DESIGN.md said was absent is present

DESIGN.md §0 records "No web JSON DM REST surface exists" and treats the mobile
protobuf API as the only DM target. The live guest page disproves the "absent"
half and pins down what the web client actually uses. The page's own hydration
config (`webapp.biz-context.domains`, identical on the login page's `SIGI_STATE`)
declares two DM endpoints:

```
imApi       https://im-api.tiktok.com
imFrontier  wss://im-ws.tiktok.com/ws/v2
```

- **[Obs]** Both hosts are live. `im-api.tiktok.com` answers `404` from an
  edge tagged `TLB` (ByteDance load balancer) on an unknown path; `im-ws.tiktok.com/ws/v2`
  answers `400` to a plain GET and `101 Switching Protocols` to a real WebSocket
  upgrade. So the web DM transport is a **ByteDance "frontier" WebSocket**, with
  `im-api.tiktok.com` as its REST companion, not the mobile `api16-normal-*.tiktokv.com`
  protobuf host. The two surfaces coexist.
- **[Obs]** The `ZTI_test` experiment on the guest page carries a
  `consumer_path_list` whose first entry is `/v1/message/send` — the same DM send
  path the mobile client uses and the one in DESIGN.md §8. The web and mobile IM
  paths share names; the difference is host, framing and signer, not the verb set.

Consequence for the bridge: there is a **third viable path** beyond QR and
browser-cookie-to-mobile — drive the web frontier WebSocket directly with a
web session. It still needs the web signer (below) and is region-routed (below),
so it is not free, but it removes the "does a web session carry to the *mobile*
IM host" crux by not using the mobile host at all. Worth a real logged-in probe.

## The frontier WebSocket (the realtime channel)

- **[Obs]** URL as opened by the page (guest, so it connects but stays idle):

  ```
  wss://im-ws.tiktok.com/ws/v2?fpid=32&service=33554513&method=2&aid=1988
      &device_id=7686577476357588502&access_key=111a402a7fd6455c8b06983ae2345aa5
      &device_platform=web
  ```

  `aid=1988` is TikTok web. `service=33554513` identifies the IM service.
  `access_key` is a per-connection 32-hex token minted client-side.
- **[Obs]** Handshake is served through Akamai (`X-Cache: ... AkamaiGHost`, Akamai
  IPs `2.19.x`, `104.126.x`). Response negotiates subprotocol `pbbp2` and sets
  `Handshake-Options: ping-interval=30`, `Handshake-Status: 0`,
  `X-Frontier-Logid: ...`. Request offers `Sec-WebSocket-Protocol: binary, base64, pbbp2`.
- **[Obs]** Frames are length-delimited protobuf ("pbbp2"). Decoded shapes:
  - **Outbound client hello / subscribe** (75 B): a `Frame{ seqid, logid_ts,
    service=33554513, method=2, payload_type="2", body{ header{ 1:2, device_id,
    ts }, repeated cursor{ topic=3, idx } } }`. A later 238 B outbound frame lists
    ~11 message-id cursors (`4: {topic, ?, 1, message_id}`) — an ack/fetch of
    inbox positions.
  - **Inbound** (≈900 B): a `Frame` whose field 5 is a repeated header map
    (`X-Method: PayloadRelatedMethod`, `X-PSM: bytedance.bsync.cache_svr`,
    `x_frontier_msg_id`, `x_frontier_traceid`, `traceparent`), field 6 =
    `"gzip"`, field 8 = a gzipped inner protobuf. Gunzipped, the inner body is a
    list of per-topic entries carrying message-id + timestamp tuples — the inbox
    sync/cursor state, empty of message content because the session is a guest.
- **[Inf]** This is ByteDance's generic "frontier"/bsync push channel: the client
  subscribes to topics, the server pushes gzipped protobuf payloads tagged by
  method, and `x_frontier_*` headers carry idempotency/tracing. DM messages ride
  it as one payload type; the same channel is reused for other realtime signals.
- **[Obs]** `WebAppRealtimeSignalDB` and `sync-sdk-db` (v10) IndexedDB databases
  are created on every page — the client persists frontier sync cursors locally,
  mirroring the watermark/dedup model the bridge already has in `state.py`.

## Signing (the fragile dependency, confirmed and named)

- **[Obs]** The web signer is **`webmssdk`** (`webmssdk/1.0.0.417/webmssdk.js`,
  plus `ttweb_webmssdk_ex/1.0.0.2865`), exposed as `window.byted_acrawler`. It
  installs by monkey-patching `fetch`/`XMLHttpRequest.open`
  (`__ac_intercepted_fetch`, `__ac_intercepted_open`). Its API includes
  `frontierSign`, `registerWsSigner`, `setTTWid`, `setTTWebid`, `report`.
  `frontierSign` + `registerWsSigner` are exactly the hooks that sign the
  WebSocket handshake/frames — the WS `access_key` comes from here.
- **[Obs]** HTTP request signing produces these query params, observed live on
  guest calls (e.g. `/api/ba/business/suite/permission/list/`,
  `webcast/.../check_external_entry`, `passport/web/store_region/`, `/shorten/`):
  `X-Bogus`, `X-Gnarly`, `X-Dynosaur`, `msToken`, plus `verifyFp` and the full
  device/browser param block (`device_id`, `odinId`, `aid`, `browser_*`, `os`,
  `screen_*`, `tz_name`, `webcast_language`, ...). This is the current web scheme;
  note it is `X-Bogus`/`X-Gnarly`, **not** the mobile `x-argus`/`x-gorgon`/`x-ladon`/
  `x-khronos` set in DESIGN.md §2. A web-frontier bridge would swap the `Signer`
  implementation, which is exactly the seam DESIGN.md §2 built for.
- **[Obs]** `webmssdk` beacons signed telemetry to
  `webmssdk16-normal-no1a.tiktokw.eu/web/report` (magic `538969122`, base64
  `strData`) and fetches keys from `.../web/resource?eq=...`. `secsdk` and a
  separate `ZTI` SDK (`ucenter_tiktok_zti_sdk`) also load. The signer stack is
  multi-SDK, not one file.
- **[Obs]** `msToken` is refreshed constantly and stored both as a cookie (on
  `.tiktok.com`, `.tiktokw.eu` and `www.tiktok.com`) and in `localStorage`; its
  value rotates on nearly every navigation. Any signer replica must reproduce
  `msToken` issuance, not just cache one.

## Login / QR flow (matches DESIGN.md §3, with the real endpoints)

- **[Obs]** `/login/qrcode` calls `GET /passport/web/get_qrcode/` (`aid=1459`,
  `did=<device_id>`, `sdk_version="1.0.16-alpha.2"`, `account_sdk_source=web`,
  `unified_sdk=1`), returning `{ data: { qrcode: <base64 png>, token,
  expire_time, app_name:"TikTok PWA" } }`. QR TTL ≈ 100 s (`expire_time` was
  `now+100`).
- **[Obs]** It then polls `GET /passport/web/check_qrconnect/?token=...&verifyFp=...`
  every ~1.5–2 s. Unscanned returns `{ data:{ status:"new",
  ttwid_migration_ticket:"..." }, message:"success" }`. The status ladder to watch
  for is `new` → (scanned) → (confirmed). `verifyFp` on these polls is the only
  signed param.
- **[Obs]** The poll fans out to **three** passport sync hosts in parallel —
  `web-sg.tiktok.com` (global), `login-us.www.tiktok.com` (ttp),
  `login-eu.www.tiktok.com` (gcp) — listed in `login-config`'s `ttpConfig.syncSeverList`.
  This is the TikTok "TTP" (data-partitioning) multi-region login sync; a bridge
  must hit the region matching the account or the connect never confirms.
- **[Obs]** Social login order is `FACEBOOK, GOOGLE, APPLE, INSTAGRAM`
  (`node-webapp/api/login-config`). Google One-Tap (`accounts.google.com/gsi/client`)
  is attempted but CSP-blocked in this harness.
- **[Obs]** `/messages` while logged out `302`s to `/login?redirect_url=/messages`,
  and `aweme/v1/report/inbox/notice/` returns `{status_code:8,"Login expired"}` —
  clean, catchable "needs-reauth" signals, matching the DESIGN.md failure table.

## Identity, region and cookies

- **[Obs]** A guest identity is minted immediately with no login: `wid` /
  `device_id` = `7686577476357588502`, `odinId` = `7686577470389273622`, plus
  `ttwid` (httpOnly), `s_v_web_id`, `tt_csrf_token`, `tt_chain_token`,
  `x-web-secsdk-uid`. `ttwid` (~127 chars, ~1-year expiry) is the durable device
  cookie; `odinId` is the analytics identity. This is the "device fingerprint"
  DESIGN.md §3 wants to mint once and never rotate — the web mints it for us.
- **[Obs]** Region routing is **EU-TTP2** (`idc:"no1a"`, `vregion:"EU-TTP2"`,
  `clusterRegion:"EU_TTP"`, `region:"DE"`). All telemetry/config/DM-adjacent hosts
  are `.tiktokw.eu` / `.tiktokv.eu`, while the DM frontier and IM REST stay on the
  global `.tiktok.com`. A bridge's proxy region decides which passport/TTP cluster
  the session binds to; mismatch is a real failure mode.
- **[Obs]** CSP (report-only) `connect-src` allowlists `wss://*.tiktokv.com` and
  `wss://*.tiktokv.eu` but the DM socket is `wss://im-ws.tiktok.com` — logged as a
  violation but allowed because the policy is report-only. Indicates the web DM
  socket is comparatively new / not yet in the locked-down CSP.
- **[Obs]** `firebase` API key `AIzaSyDHGqRfibWT6DffZBTYlhXfTQHAP_ri1MI` is shipped
  in `biz-context.apiKeys` (web push). Not secret, but noted.

## Telemetry surface (noise to expect, and to redact)

- **[Obs]** The dominant traffic by volume is analytics, not product: 192 POSTs to
  `mcs16-normal-no1a.tiktokw.eu/v1/list` (Tea/AppLog events) and ~130 to
  `monitor_browser/collect/batch/` (Slardar). A bridge that reuses these hosts
  should suppress or ignore them; they carry `device_id`, `user_id` and page URLs
  and must be treated as PII if ever logged.
- **[Obs]** A/B config comes from `libraweb-ttp2.tiktokw.eu/service/2/abtest_config/`
  and the inline `abTestVersion` blob. DM-relevant flags seen: `enable_message_refactor`,
  `web_dm_im_react_sdk` (v4), `web_dm_flexible_drawer`, `web_dm_message_reaction`,
  `web_dm_quote_message`, `privacy_add_dm_permission`, `enable_dm_side_nav`,
  `use_inbox_notice_count_api`. Confirms an actively developed web DM product.

## Console / errors

- **[Obs]** 555 console lines, only 2 real page errors (`a.init is not a function`,
  a benign SDK race). The rest are CSP report-only notices (245×
  `upgrade-insecure-requests ignored`), `AppContext will be overwritten` (54×), and
  Slardar/Tea init logs that echo the guest `userInfo` (`user_is_login:false`).
  No crash, no bot-detection challenge, no captcha was triggered by the guest browse.

## What this changes for the bridge

1. **Re-open the "web has no DM surface" assumption.** It does:
   `im-api.tiktok.com` + `wss://im-ws.tiktok.com/ws/v2` (frontier/pbbp2). A
   web-session frontier client is a real design option and sidesteps the
   mobile-bind crux. Needs a logged-in capture to see message bodies.
2. **Name the web signer explicitly:** `webmssdk` / `byted_acrawler`, params
   `X-Bogus`/`X-Gnarly`/`X-Dynosaur`/`msToken`/`verifyFp`. Distinct from the mobile
   `x-argus/x-gorgon` set. The `Signer` seam in DESIGN.md §2 already anticipates
   swapping this; a web target needs a `frontierSign`/`registerWsSigner` equivalent.
3. **Multi-region TTP is mandatory, not optional.** QR confirm and session sync
   fan out to sg / us-ttp / eu-gcp hosts; the proxy region must match the account.
4. **The realtime channel is a frontier WebSocket with gzipped protobuf and
   `x_frontier_msg_id` idempotency** — the bridge's polling+watermark MVP maps
   onto it cleanly, and `WebAppRealtimeSignalDB` shows TikTok itself persists the
   same cursor state client-side.

### Suggested next probe (needs a real login)
With a logged-in web session: watch `im-ws.tiktok.com/ws/v2` frames on `/messages`,
capture the inbound DM payload protobuf, and call `im-api.tiktok.com` for the
conversation list. That closes the body-shape gap DESIGN.md §"What is not built"
leaves open for `_parse_conv_list` / `_parse_messages`.
