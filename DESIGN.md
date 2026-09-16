# Unofficial TikTok DM Bridge — Design

Exercise 2, Knows case study. Prototype scope: authenticate to TikTok, hold a
session, get messages into a pipeline. Judged on reasoning and how far the work
gets, not production-readiness.

Epistemic markers are honest throughout: **[Obs]** = observed live, **[Inf]** =
inferred from a credible source (the `molkex/tiktok-private-api` reverse-engineering
reference, live-tested by its authors), **[Guess]** = unverified assumption to
close later.

---

## 0. Phase 0 result (the surface decision)

Probed live on 2026-09-16 from a datacenter IP.

- **[Obs] DMs are the mobile app API, as protobuf.** `/v1/conversation/list/` and
  `/v1/client/unread_count/` on `api16-normal-*.tiktokv.com` and `api.tiktokv.com`
  return HTTP 200, `content-type: application/x-protobuf`. The decoded envelope
  matches the ByteDance IM `Response` message: field 3 = `status_code`, field 4 =
  `error_desc`, field 7 = `log_id`.
- **[Obs] No web JSON DM REST surface exists.** `www.tiktok.com/api/im/...` returns
  `"url doesn't match"`. The web client speaks the same IM SDK over a websocket.
  So the target is the mobile protobuf surface, signed — not the web, and not the
  guessed `/aweme/v1/im/` scheme (which the reference confirmed absent from the APK).
- **[Obs] Status ladder:** `200005` (no session/signing) -> `200001` (valid mobile
  session, IM subsystem not initialized) -> `0` (success). The first is my live
  result; the second is the reference's live emulator result.
- **[Guess] The crux: does a session carry from a desktop-web login to the mobile IM
  host?** Cookie names are shared (`sessionid`, `sid_tt`, `sid_guard`, `uid_tt`), but
  `200001` implies IM binds to a mobile `install_id`/device registered through the
  mobile passport flow. A desktop-web session is not device-bound, so it may never
  clear `200001`. This risk drives the login design below.

Consequence: the client is a **signed mobile client**. The session must be
**mobile-bound**. Signing is mandatory and is the single most fragile dependency.

---

## 1. Scope and non-goals

**v1 does:** login (QR primary, browser+capture fallback), hold and persist an
encrypted mobile session, fetch conversations + message history (cursor-paginated),
poll for new messages with gap reconciliation, normalize TikTok objects into
canonical events (thread -> portal, user -> ghost, message -> event), send text,
mark read.

**v1 does not:** run a full Go mautrix appservice, implement the `im-ws` websocket,
handle media beyond avatar/attachment URLs, or ship the signing algorithm itself.
The bridgev2 interface mapping is **documented, not implemented** (Section 7), so
the architecture is legible without spending the time budget on Go + appservice.

**Language:** Python prototype. Deliberate: the proven TikTok path (passport login,
signing, device params, endpoint shapes) is all Python in the reference, and the
grading is reasoning + reach, not a production Go bridge.

---

## 2. Architecture — six layers, one direction of dependency

```
auth/session   ->  signing  ->  client/transport  ->  sync/ingest  ->  normalization  ->  (pipeline)
                                       ^                                                        |
state/storage  <---------------------- persists session, cursors, dedup  <--------------------
```

Each layer has one job, a narrow interface, and no upward imports.

1. **auth/session** — device identity, login flows, challenge handling, produces a
   persisted encrypted session blob. Depends on client/transport for the passport
   calls, nothing above.
2. **signing** — SignerPy wrapper, isolated behind a `Signer` interface with one
   method: given request parts, return the `x-argus/x-gorgon/x-ladon/x-khronos/x-ss-stub`
   headers. Swappable for a remote signing microservice. This is the hot-swap seam
   for the day the algorithm rotates.
3. **client/transport** — HTTP client. Injects the stable per-user device
   fingerprint + geo-matched proxy, calls the signer, parses the protobuf/JSON
   envelope, classifies errors (rate-limit / ban / session-expiry / captcha / IM-not-init).
4. **sync/ingest** — backfill (conversation list + per-thread history, cursor
   pagination) and realtime (polling MVP with reconciliation against stored
   watermarks so no message is lost across a gap).
5. **normalization** — TikTok protobuf/JSON objects -> canonical `Event` /
   `Thread` / `User` records. This is the bridgev2 boundary in miniature.
6. **state/storage** — envelope-encrypted session store, per-user cursor/watermarks,
   message dedup set.

The client/transport, signing, and normalization layers have **no auth or storage
imports**, so each is unit-testable in isolation (LINE/Tumblr separation lesson).

---

## 3. Login flow (QR primary, browser fallback) — one state machine

Phase 0 says the session must be mobile-bound. Both flows produce that.

**Flow A — QR (recommended).** The bridge, acting as a device, calls
`/passport/mobile/get_qrcode/` on the mobile passport host, renders the QR, and
polls `/passport/mobile/check_qrconnect/` until the user scans and approves in
their real TikTok app. The session is minted **bound to our device fingerprint
from birth**, so IM works. No password ever touches us; captcha/2FA/IDV happen
inside the user's own app.

**Flow B — browser + capture (fallback, for accounts that can't scan).** Render
TikTok's real login page in a controlled browser (per-user residential proxy),
let the user and TikTok resolve credentials + captcha + 2FA on TikTok's own page,
then extract the session cookies. **Immediately verify against the mobile host**
(`/v1/client/unread_count/`): a `200001`/non-carry result marks the session
"web-only, not mobile-bound" and surfaces a re-auth-via-QR action rather than
silently failing later.

Both flows are steps of a single login state machine (bridgev2's `LoginProcess`
vocabulary: `display_and_wait` for the QR, `cookies` for capture, `complete`).
The password, if ever entered, is entered into TikTok's page, never persisted and
never seen by us.

**Device identity:** one stable fingerprint per user (`device_id`, `install_id`/`iid`,
`openudid`, `cdid`, `user_agent`), generated once and **reused forever, never
rotated**. Rotating these on a logged-in session looks like account takeover and
gets the account challenged or banned — this fixes the bug in the reference scripts
that randomized them per request.

---

## 4. Failure modes — per step: break, detect, recover

| Step | How it breaks | Detection | Recovery |
|---|---|---|---|
| Signing | Algorithm rotates on app update | **4xx/rejection spike across ALL users at once** (canary) | Hot-swap the signer package/service; version-pin; alert on the canary, not per-user |
| Signing (single user) | Bad device/proxy for one account | 4xx for one user only | Isolate to that user; do not treat as a signer outage |
| Session | Expired / logged out elsewhere | body `status_code` = auth error, cookie cleared | 401 -> one token-beat refresh -> if still bad, mark `needs-reauth`; **never spin-relogin** and fight the user's phone session |
| Login (QR) | User never scans | poll timeout | expire the login, prompt retry |
| Login (browser) | Web session doesn't carry to mobile | verify probe returns 200001 | mark web-only, offer QR |
| Rate limit | Too many calls / IP flagged | HTTP 429 or body rate-limit code | backoff + jitter, rotate proxy, lengthen poll interval |
| Ban / lock | account challenged | body code / redirect | mark `needs-reauth`, user action, do not retry |
| Outbound send | ambiguous result (timeout after send) | no confirmed `server_message_id` | **never blind-replay**; mark pending, reconcile against next history fetch using `client_message_id` idempotency key |
| Sync gap | poll missed messages | watermark vs fetched cursor mismatch | reconcile: fetch history from last watermark, dedup, advance watermark only after persist |
| IM not initialized | 200001 on a valid session | body `status_code` 200001 | run IM init/session-bind step; if unavailable, re-auth via QR |

Design principle throughout: **detect at the right blast radius** (all-users = signer,
one-user = account/proxy) and **never take an irreversible action on an ambiguous
signal** (no blind send-replay, no auto-relogin loop).

---

## 5. Security (concrete)

Never persist the raw password; ideally never receive it (QR flow, and browser flow
types it into TikTok's real page). Persist only the session blob + device
fingerprint, **envelope-encrypted at rest**: a per-user data key encrypts the blob,
and a KMS master key wraps the data key; the master key never leaves the KMS.
Contact and message content is third-party PII: encrypted at rest, retained only as
long as needed, and **deleted on logout** (session blob, cursors, cached
contacts/messages all purged). Service secrets (KMS key id, proxy creds, signer
API key) live in a secrets manager injected at deploy time, never in the repo. Logs
redact every secret — booleans and keys about an auth failure, never values. The
cookie jar is scoped to TikTok hosts; media downloads go through a jar-less,
host-allowlisted client. Each user gets a geo-matched residential proxy, with a
consistent IP + device + session coupling so the traffic looks like one real phone.

---

## 6. Health metric

**Primary — Live-Session-Ratio:** fraction of logins that are both authenticated
AND actively syncing (last successful sync within the expected poll interval).
Every failure mode collapses into a drop here: session death, signer breakage,
bans, and rate-limit stalls all move logins out of "actively syncing." Alert on a
sudden drop (signer outage) and on a rising `needs-reauth` share (credential churn).

**Secondary — message-delivery-lag p95:** time from a message existing on TikTok to
it landing in our pipeline. Catches silent degradation where sessions are alive but
polling has fallen behind.

---

## 7. bridgev2 interface mapping (documented, not built)

So the mautrix-fork target is legible without implementing Go:

| bridgev2 concept | This prototype |
|---|---|
| `NetworkConnector` | the top-level bridge object wiring the six layers |
| `LoginProcess` | the QR/browser login state machine (Section 3) |
| `NetworkAPI` | client/transport + sync/ingest per login |
| `UserLoginMetadata` | the encrypted session blob: cookies, device fingerprint, user id, region |
| `Portal` | a TikTok conversation (`PortalKey{ID: conversation_id, Receiver: login_id}`) |
| `Ghost` | a TikTok user, avatar from the profile object |
| `RemoteEvent` / `simplevent.Message` | a normalized message event queued to the pipeline |
| `BackfillingNetworkAPI.FetchMessages` | cursor-paginated history fetch |
| `IdentifierResolving` / `UserSearching` | friends/contacts lookup |

---

## 8. Client endpoints (the surface the client wraps)

`login` (passport QR / cookie import), `current_user`, `list_friends/contacts`,
`list_conversations(cursor)`, `get_messages(conv, cursor)`, `send_text`,
`mark_read`, `download_media`. Protobuf envelope on the IM paths; JSON on passport
and profile paths. Confirmed paths from Phase 0 + the reference:
`/v1/conversation/list/`, `/v1/conversation/get_core_info/`,
`/v1/message/get_by_conversation/`, `/v1/message/send/`, `/v3/conversation/mark_read/`,
`/v1/client/unread_count/`, `/v1/stranger/get_conversation_list/` (message requests).

---

## 9. Repo shape

```
bridge/
  auth/         device identity, login flows (QR + browser), challenge handling
  signing/      SignerPy wrapper behind a Signer interface (swappable)
  client/       HTTP transport, device fingerprint + proxy, envelope parse, error classify
  sync/         backfill + polling with reconciliation
  normalize/    TikTok objects -> canonical events
  storage/      envelope-encrypted session store, cursors/watermarks, dedup
  cmd/          run the bridge / a login CLI to exercise flows locally
tests/          unit tests that assert real behavior (envelope parse, error classify,
                reconciliation dedup, signer seam)
README.md       structured around FAILURE MODES, not features
DESIGN.md       this document
```

Build order and per-file plan follow in the implementation plan (writing-plans).
