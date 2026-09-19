# TikTok DM bridge: design

Case study for Knows. Goal: anyone logs in with their TikTok account and their
conversations (contacts, profiles, threads, messages) are fetched on our backend
and land in a mautrix-style pipeline. Scope: reasoning, failure modes, security, one
health metric, and how far a prototype gets, not production-readiness. Everything
in this repo is Python; the bridgev2 Go connector is the port target (§9, §12), not
something that exists here.

**[Obs]** marks a claim observed live, **[Inf]** one inferred from a credible source
(also used in code comments). What is proven versus assumed is listed in §11.

---

## 0. How this design was reached: the process and the learning curve

The design was not chosen up front; it was found by hitting walls and pivoting. The
order matters, because each wall is why a later decision exists.

**Phase 1, the mobile app API (and three walls).** The first target was TikTok's
mobile DM API: signed protobuf on `api16-normal-*.tiktokv.com`, the same surface the
reference reverse-engineering (`molkex/tiktok-private-api`) and Knows' own scripts
use. It was built end to end: hand-rolled protobuf codec, the IM envelope, a device
fingerprint, the `Signer` seam wrapping SignerPy, an encrypted session store.
[Obs] `GET /v1/conversation/list/` answers HTTP 200 as `application/x-protobuf`; the
decoded envelope walks the status ladder `200005` (no session or signature),
`200001` (valid session, IM not initialised), `0` (success), and the protobuf parser
is unit-tested against those captured bytes. Then it hit three walls from a
datacenter, **all confirmed live**: (1) **IP reputation**: even with SignerPy
signatures TikTok accepts, `send_email_code`/`account_lookup` return `error_code 7`
on the first attempt, from three regional hosts; the signature is fine, the IP is
the wall. (2) **Device registration**: the mobile IM binds to a device registered
through a TTEncrypt-encrypted call that no public library has, so `device_register`
returns `device_id: 0`. (3) **A signer we do not own**: the algorithm rotates and is
a third-party dependency. Lesson: the mobile path is *correct but not runnable* from
our backend. It stays in the tree as the documented alternative (§1, §9).

**Phase 2, the pivot: the page is the signer.** A plain headless Chromium loads
tiktok.com from the *same* IP, is not blocked, and runs TikTok's own web signer
(`webmssdk` / `window.byted_acrawler`). The key experiment: an in-page `fetch()`
with the signing params stripped came back with `X-Bogus`/`X-Gnarly`/`msToken`
re-appended and HTTP 200 real data. So **the page signs for us**; we never reverse
the algorithm. This turned the whole problem from "reverse a signer" into "drive a
browser and tap its traffic", which sidesteps all three Phase-1 walls at once. A
full guest-session capture confirmed the web DM surface everyone assumed was absent:
the web client declares `imApi = https://im-api.tiktok.com` and
`imFrontier = wss://im-ws.tiktok.com/ws/v2` in its own hydration config, both live.
The same capture named the web signer and the multi-region TTP login sync
(sg / us-ttp / eu-gcp): the egress region has to match the account's cluster or a
QR confirm never lands (§5, region mismatch). This is the path that ships.

**Phase 3, build the read path, fixture-first.** From one real logged-in capture we
learned the DM surface: contacts (`/api/im/spotlight/relation/`), profiles, the
`im-api` protobuf endpoints, and the `wss://im-ws.tiktok.com/ws/v2` frontier
websocket (pbbp2). The frontier framing itself had been decoded by hand before that,
on a live LIVE-chat room: hooking `window.WebSocket` exposed the `WebcastPushFrame`
protobuf (seq, logid, method, headers, gzip payload) and the ack and heartbeat
exchange the server requires; the DM socket uses the same frontier shape. A redactor
turned the raw capture (secrets plus a friend's real DMs) into safe fixtures, and
every parser was written against them. The `WebProvider` plugs into the same
`Syncer`/`normalize`/pipeline the mobile client would have used; the provider seam
paid off.

**Phase 4, prove it live.** The captured session was imported read-only and pulled
**19 real contacts** into SQLite, then a live DM exchange over the frontier socket
was captured and decoded to text with correct self/peer attribution. Two things were
learned only from real data: the frontier does **not** replay history on connect
(the backlog comes from the page's own `get_by_user_init` protobuf), and the message
field layout (`f6→f500→f5` for frontier frames, `f6→f203→f1` for the init backlog);
the earlier guess was wrong and only the capture fixed it.

**Phase 5, a runnable demo app.** A `LiveBridge` (one Playwright worker; the demo
runs a single login) plus a stdlib HTTP API plus a small web wrapper (connect /
chats / health). This is where the gap between "works in a test" and "works for a
person" showed up, and each fix taught something:

- **Login throttle** ("maximum attempts"): caused by opening a *new* browser profile
  per connect, a fresh device identity every time, which TikTok rate-limits and
  which violates the never-rotate-identity invariant. Fix: one stable profile,
  reused; log in once, then reuse the session. This is the invariant, made real.
- **Phantom chats**: the persistent SQLite showed conversations from a previous
  session/account that no longer existed in the logged-in account. Fix: wipe and
  re-sync per connect so the shown chats always match the live account.
- **QR in the UI**: the login browser window was intrusive and, on macOS, the trick
  to hide it (close and reopen the same profile) raced the profile lock and leaked
  empty windows, crashing with `NoneType … goto`. Fix: run QR login fully headless
  and render the QR image *inside* the web UI, no window at all. The password
  window is hidden the same way again since the login-watch fix (close after
  login, reopen the profile headless), but the relaunch now waits for Chromium's
  `SingletonLock` to disappear first, the missing step that caused the leak.
- **Login watch stopped too early**: a throttle message or the 5-minute timeout
  put the login into a terminal `needs_user` while the window was still open, so
  a login finished there afterwards was never noticed (and a reconnect launched
  a second browser on the locked profile). Fix: an open login window is watched
  until it is closed; a reconnect closes the old browser first.
- **Async loading**: the conversation list loads late, so the backlog is reloaded
  until it appears rather than assumed present on the first paint.

**Phase 6, sending: investigated and cut.** The one feature that could not be made
robust. Measured live, a web DM send is a **signed pbbp2 frame over the frontier
WebSocket**, signed by `webmssdk` in the page; there is no REST endpoint to replay.
Driving the composer works but is inherently fragile, and it was outside the case
study's read-focused core. It was removed on purpose; §13 is the honest outlook on
how to add it later.

**Dead-ends, so nobody repeats them.**

- TTEncrypt device registration: not public, on no PyPI package. Raw mobile
  registration from a datacenter is not worth more time.
- Web email-code login: the passwordless email code is a mobile-app feature the web
  UI does not expose (email plus password on TikTok's login page works). For the
  browser path use QR, phone/SMS or the password form.
- Long-polling a QR: the login QR is valid for about 100 seconds, so a stale one is
  useless. Coordinate the scan live (the app renders it in the UI) or run the
  browser headful locally.
- Full Chromium headless on the server: missing system libraries and no root. The
  headless-shell build works, with the libraries fetched via `apt-get download`
  plus `dpkg -x`, no `sudo` needed.
- IP rotation to dodge rate limits: detection evasion, out of scope, and
  guard-blocked when probed. The legitimate answer is per-user residential proxies
  supplied by the operator (§8).

The throughline: **de-risk the protocol in the cheapest place (Python plus a
browser), let TikTok's own code do the signing, prove each claim against a real
capture, and be honest about the one wall (sending) that a browser cannot cross.**

---

## 1. Path selection: the web path ships, the mobile path is documented

Two DM surfaces exist; we probed both live.

- **[Obs] The mobile app API is correct but not runnable from our backend.** With
  valid SignerPy signatures accepted by TikTok, `send_email_code`/`account_lookup`
  return `error_code 7` from a datacenter IP on the first attempt (IP reputation,
  not signing), and `device_register` needs TTEncrypt, which no public library has
  (`device_id: 0`). So the mobile path is gated by IP plus device registration plus
  a signer we do not own.
- **[Obs] A real Chromium is not gated.** It loads tiktok.com from the same IP,
  sets cookies, is not blocked, and runs TikTok's own web signer (`webmssdk` /
  `window.byted_acrawler`), which installs by monkey-patching `fetch` and
  `XMLHttpRequest.open`.
- **[Obs] Any request executed inside the page is therefore signed by TikTok's own
  code.** The logged-in capture's in-page probe fetched a DM URL with the signing
  params stripped; `webmssdk` re-appended `X-Bogus`/`X-Gnarly`/`X-Dynosaur`/`msToken`
  and it returned 200 with real data. **The page is the signer**; we never reverse
  the web signing algorithm.
- **[Obs] The `/messages` page opens `wss://im-ws.tiktok.com/ws/v2` itself** and
  handles acks/heartbeats. Playwright's native `page.on("websocket")` exposes every
  frame without injecting anything.

**Cost:** one browser context per connected user while syncing. Escalation levers,
documented not built (§8): a shared "signing-oracle" browser plus `curl_cffi` HTTP
polling; driving the frontier socket directly (pbbp2 framing already decoded in
`bridge/proto.py` and `bridge/web/frontier.py`).

The mobile client (`bridge/im.py`, `bridge/client.py`, `bridge/signing.py`) and a
third-party vendor (`TikApiProvider`) stay in the tree behind one seam. The web
backend is `bridge/providers/web.py` (`WebProvider`). `Syncer`, `SyncState`,
`normalize`, `metrics`, `session_store`, `pipeline` are the shared pipeline.

TikAPI was probed against its live contract and kept as a unit-tested adapter behind
the seam. Verdict: good as a short bootstrap for capturing real DM JSON and as a
fallback, wrong as the sole backend: no push (polling only), metered on requests and
bandwidth, and it explicitly disclaims TikTok ToS compliance. The seam makes native
versus TikAPI a one-line swap, so the call is reversible.

---

## 2. Architecture: six layers, one of them the provider seam

```
auth/session -> [ MessageProvider ] -> sync/ingest -> normalize -> pipeline(SQLite) -> API
                   |    |     |                                          ^
              web  mobile  tikapi          state/storage: session_store (AES-GCM),
                                           SyncState cursors + dedup  <---+
```

1. **auth/session**: login flows (password ladder, cookies import, QR), device
   identity per user (the persistent browser profile; `ttwid` minted once, never
   rotated), `WebSession` ↔ encrypted blob.
2. **provider seam** (`bridge/provider.py`): the build-vs-buy boundary. A
   `MessageProvider` is one login's `NetworkAPI`: `list_conversations`,
   `get_messages`, `list_contacts`, `get_profile`, `send_text`, `mark_read`,
   `subscribe`. `WebProvider`, native `IM`, and `TikApiProvider` are interchangeable.
3. **sync/ingest** (`bridge/sync.py`): backfill (cursor pagination) plus poll; dedup
   by message id (in-memory set plus the SQLite unique key), cursors are opaque and
   only advance. After a socket gap the REST poll re-pulls through the same dedup.
4. **normalize** (`bridge/normalize.py`): TikTok objects → canonical `User`/
   `Thread`/`Event{kind}`. The bridgev2 boundary in miniature.
5. **pipeline** (`bridge/pipeline.py`): SQLite raw layer everything reads from:
   `logins`/`users`/`threads`/`events`, `stream_order` = ms ts, unique
   `(thread_id, message_id)`, idempotent upsert, optional webhook plus dead-letter.
6. **state/storage**: envelope-encrypted `session_store`, per-user cursors, dedup.

The provider, normalize, and pipeline layers have no auth imports, so each is
unit-tested in isolation.

---

## 3. Login and session

Three web flows (plus the legacy `email`/`browser` modes of the mobile path), one
state machine (`bridge/auth/login.py`, `LOGIN_MODES`):

- **password** (bridgev2 `user_input`): the ladder (`web_password_login.py`):
  restore session → `is_alive()` → alive: done; else **exactly one** password
  login into TikTok's own form → success: persist; challenge (captcha/2FA/verify/
  IDV): stop with `needs_user`, persist nothing new; wrong credentials:
  `bad_credentials`, no further automatic attempt; locked: `blocked`. Selectors are
  pinned from the capture in one dict with the verified date; a missing selector is
  `schema_change`, not a crash. **Never retry a challenge, never auto-loop.**
- **cookies** (bridgev2 `cookies`): `web_cookie_import.py`: import a session
  captured elsewhere (laptop → server, and the future iPhone path). Pins the
  backend context's UA/locale/timezone/viewport to what was reported and reuses the
  reported `ttwid`; a UA that contradicts an existing profile is refused with
  `fingerprint_mismatch`, never silently replaced.
- **qr** (bridgev2 `display_and_wait`): passwordless; the phone approves.
  [Obs] `GET /passport/web/get_qrcode/` returns a base64 QR plus a token valid for
  about 100 s; polling `check_qrconnect/` walks `new` → `scanned` → `confirmed`. No
  captcha appears on this path. The demo runs it headless and renders the QR inside
  the web UI (§0, Phase 5).

`is_alive()` is the cheapest logged-in call the capture shows:
`GET /passport/token/beat/web/` (`error_code 0` = alive), with a `/messages` load
and a `/login` 302 / `status_code 8` fallback. The raw password is never persisted:
the v1 flow holds it in memory for the single login step; the demo app never
receives it (the user types it into TikTok's own page).

---

## 4. How messages land

`WebProvider` calls run inside the page (signed). `list_contacts` reads
`/api/im/spotlight/relation/` (19 followings in the live pull; the redacted fixture
holds 17); `get_profile` reads `/tiktok/v1/im/user/profile/`; conversations/messages
read the `im-api` surface. Each response is parsed by an isolated parser that raises
`SchemaChange` on a renamed/missing container. Realtime rides the frontier
websocket: inbound pbbp2 frames are gunzipped (`frontier.py`) to
`(conversation_id, message_id, ts, sender_id, text)`; unknown methods are dropped,
never raised; `x_frontier_msg_id` travels with each message as its raw reference and
the message id is the dedup key. After a reconnect or reload the REST poll re-pulls
through the `Syncer` dedup path. `Syncer` writes normalized events into the SQLite
pipeline (idempotent on `(thread_id, message_id)`); the API reads from the pipeline.
Avatars download through a jar-less, host-allowlisted client (`*.tiktokcdn.com`,
`*.tiktokcdn-eu.com`, `*.tiktokcdn-us.com`), hashed by URL.

---

## 5. Failure modes

Detect at the right blast radius: **all users failing at once = our bug**
(signer/browser/key); **one user failing = that account**. Never take an
irreversible action on an ambiguous signal. Every row names the test or live
observation that backs it.

| Condition | Detection | State / action | Recovery | Backed by |
| --- | --- | --- | --- | --- |
| Password wrong | `login-error` element | `bad_credentials` | user retries; never auto-retried | ladder unit + e2e |
| Challenge (captcha / 2FA / identity verification) | challenge element / `/verify` | `needs_user` + action | app opens URL → cookies flow | ladder + api tests |
| Session expired / logged out elsewhere | `token/beat` code 8, `/login` 302 | `needs_user` | one re-login, no auto-loop | e2e logged-out-elsewhere; is_alive tests |
| Rate limited | HTTP 429 / body code 7 | `rate_limited` | backoff + jitter; do not spin | PageClient + runtime tests |
| Account locked/banned | `account-locked` / HTTP 403 | `blocked` | user action | ladder e2e; PageClient 403 |
| Region mismatch (account's data-center cluster vs egress) | `tt-target-idc` / `store-idc` cookies vs proxy region | `needs_user`/empty | geo-match the proxy to the account | live observation (EU-TTP2 cluster); cookies kept in `WebSession` |
| Frontier socket drop | websocket close / stale poll | reconcile | the periodic REST poll re-pulls via Syncer dedup | frontier reconcile test |
| Page reload loses signer state | reload re-runs webmssdk | reconnect | reload `/messages`, re-subscribe | design ([Obs] page is signer) |
| TikTok DOM/schema change | missing selector / renamed field | `schema_change` | one selector/parser module to fix | `SchemaChange` parser test |
| Signer/browser stale for ALL users | cross-user 4xx spike (canary) | alert | hot-swap the browser/signer image | metric design (canary) |
| Proxy dead | transport error | `transient` | stable per-user failover, not IP cycling | proxy pool test (mobile client only; the browser takes no proxy yet) |
| Double login race | one action per user | serialize | per-user queue | invariant |
| Avatar CDN URL expired | 403 on download | refresh | re-fetch signed URL on download | avatar allowlist test |
| Ambiguous send | no confirmed server id | pending | never blind-replay; reconcile next fetch | invariant; send NotSupported here |
| Fingerprint mismatch on import | UA vs stored profile | `fingerprint_mismatch` | refuse; never rotate identity | cookie-import test |

States are those of the v1 runtime (`bridge/app.py`) and the login ladder; the demo
app collapses non-recoverable cases into `needs_user` / `error` and counts recoverable
provider errors (`schema_change`, `transient`) without dropping the login.

---

## 6. Security (concrete)

The raw password is never persisted and ideally never received (QR); the browser
flow types it into TikTok's own page, and the demo app's API never carries it.
Persisted is the session blob plus fingerprint, **envelope-encrypted at rest**
(`session_store.py`): a per-login AES-GCM data key encrypts the blob, a master key
(env `BRIDGE_MASTER_KEY` / KMS seam) wraps the data key, and the login id is bound as
associated data so blobs cannot be swapped between logins. Without a master key the
demo uses an ephemeral one and says so; the blob is then an export format, not a
restart mechanism. What the demo actually reuses across restarts is the Chromium
profile directory, and the SQLite cache of contacts/messages is plaintext; both are
protected by file permissions only, both are deleted on logout (`POST /api/logout`
in the app, `DELETE /v1/logins/{id}` in the v1 API). Contact/message content is
third-party PII and is treated as such: never in the repo, gone on logout. Secrets
come from env/KMS at deploy, never the repo; a unit test asserts the ladder never logs
the password. The cookie jar is scoped to TikTok hosts; avatars download through a
jar-less, host-allowlisted client. The raw capture and the plaintext
`storage_state.json` are gitignored and deleted after a verified import; the redactor
(`scripts/redact-capture.py`) blanks cookies / `msToken` / `sessionid` / `ttwid` /
`verifyFp` / `device_id` / emails, also inside protobuf and websocket bodies, and
pseudonymizes ids, handles, names and avatar object hashes with stable same-width
fakes before anything reaches the fixtures; a test decodes every committed body to
check that.

---

## 7. Health metric

**Headline: `bridge_live_session_ratio`** = connected / total logins. Every failure
mode collapses into a drop here. **Leading indicator: `bridge_password_login_share`**,
the share of connects that needed a password login; rising means sessions are dying
early, which precedes challenges and bans, so it moves before the headline does. Also
`bridge_delivery_lag_seconds` p95 (TikTok ts → `ingested_at`) to catch silent polling
lag, and `bridge_error_total{state}`. Exposed as Prometheus text at `GET /metrics`.
Alert on a sudden headline drop (signer/browser outage, all users) and on a rising
password-login share (credential churn).

---

## 8. ToS / ban / detection, and the escalation ladder

Automating a user's own account with consent is what every messaging bridge does;
it can still breach TikTok's terms and get an account restricted, so login, status,
and delete are first-class. We do **not** add captcha solving, IP rotation, or
fingerprint randomization; those are detection evasion and out of scope. The
legitimate levers, in order: **per-user residential proxy** geo-matched to the
account (`proxy.py`, stable per-login, no IP cycling) → **a shared signing-oracle
browser** (one browser signs, plain `curl_cffi` HTTP polling per user, far cheaper
than a browser each) → **edge egress** matching the account's region. One stable
device/browser identity per user, never rotated (rotation reads as takeover). The
only automation tell removed is Chromium's `AutomationControlled` flag and banner, a
fixed Playwright setting, not fingerprint randomization; UA, locale and timezone are
pinned to what the user's own capture reported. `proxy.py` is wired into the mobile
client only; the browser context does not take a proxy yet, a documented lever, not
built for the web path.

---

## 9. bridgev2 mapping (documented, not implemented in Go)

| bridgev2 | this prototype |
| --- | --- |
| `NetworkConnector` | the bridge object wiring the six layers |
| `LoginProcess` | password `user_input` / qr `display_and_wait` / cookies `cookies` |
| `NetworkAPI` | a `MessageProvider` (web / mobile / tikapi) per login |
| `UserLogin.metadata` | the encrypted `WebSession` blob (cookies, fingerprint, ttwid, region) |
| `Portal` | a `Thread` (`PortalKey{ID: conversation_id, Receiver: login_id}`) |
| `Ghost` | a `User`, avatar from the profile object |
| `Message` / `RemoteEvent` | a normalized `Event` in the pipeline |
| `BackfillingNetworkAPI.FetchMessages` | `get_messages` cursor pagination |
| `IdentifierResolving` / `UserSearching` | `list_contacts` / `get_profile` |

---

## 10. iPhone client path

The bridge stays on the backend; an iOS app is a client of the bridge HTTP API
(`bridge/api.py`) the same way Knows' apps are clients of its mautrix fork. iOS
cannot run a long-lived background sync (Background App Refresh is throttled, no
persistent sockets), so on-device ingest is not a goal. The **cookies flow is the
phone's login path**: password relay → on `needs_user` the API returns
`{action:{open_url, then:"cookies"}}` → the app opens TikTok's page in a
`WKWebView` → the user solves the challenge → the app reads `WKHTTPCookieStore` →
`cookies` step → connected. The injected assets
(`bridge/web/inject/{request,ws_hook,fetch_tap}.js`) are standalone, post through
one `postToHost(kind, payload)` (server: `expose_binding`; iOS: `WKUserScript` at
`.atDocumentStart` plus `WKScriptMessageHandler`), and never touch navigator
properties (tested). **What does not transfer:** the mobile-app-API path (SignerPy,
`device_register`, TTEncrypt) hits the same wall on a phone as on a server; "run the
bridge on the device" is rejected for iOS background limits; edge egress stays a
documented lever, not a build target.

---

## 11. What is proven, and what is not

- **Runnable demo:** `./bridge-app.sh` starts the bridge (`bridge/live.py`, one
  Playwright worker, a single login in the demo) plus the `wrapper/` tester UI
  (connect → chats → health). Real TikTok login in a local browser; the account's
  chats render and DMs stream in live; the session blob is sealed at rest and
  everything is wiped on logout.
- **Verified live:** mobile signing accepted by TikTok (blocked only by IP); a
  headless browser loads TikTok plus a scannable QR from a datacenter IP; a
  logged-in web capture of the DM surface; the in-page re-signing probe (**the page
  signs**); **the captured session imported read-only, 19 real contacts pulled
  through `WebProvider` into SQLite; then a live DM exchange (~10 messages) captured
  over the frontier socket and decoded to text with correct self/peer sender
  attribution** (2026-09-18).
- **Verified against fixtures:** contacts/profile parsers (real redacted data);
  conversation/message parsers plus cursor pagination plus `Syncer` dedup (synthetic
  fixtures, empty live inbox); frontier decode; SchemaChange on a renamed field.
- **Fake-platform only:** the full password ladder (session reuse across restart =
  zero logins, logged-out-elsewhere = one login, 2FA stops, wrong password not
  retried, locked = blocked) driven by a real headless browser against
  `tests/fake_platform.py`.
- **Not built:** the live web conversation-list JSON mirror (REST inbox was empty,
  so it is synthetic-tested; the realtime message body IS captured live); outbound
  send / mark-read (the web DM send is a signed WebSocket frame built by TikTok's
  own page JS, so there is no request to replay; sending would mean driving the live
  composer in the browser, too fragile to ship, so it is read-only); the Go mautrix
  appservice (mapping documented, §9); mobile device registration (TTEncrypt).

The offline suite and its current count are in the README (Run it).

---

## 12. Next steps: the first five tickets

1. **Replace the synthetic message fixtures** (`messages_synth`, `conv_list_synth`)
   with redacted ones from a non-empty inbox; the frontier message layout is already
   pinned in `bridge/web/frontier.py`.
2. **Sending, behind a flag:** §13 option 1 (hardened browser-in-the-loop) with the
   never-blind-replay rule; a capture of one real send pins the frame layout.
3. **Ship the signing-oracle escalation**: one browser signs, `curl_cffi` HTTP
   polling per user, to drop the browser-per-user cost.
4. **Per-user residential proxy wiring end to end** with region matching to the
   account's TTP cluster, and the cross-user 4xx canary alert on the metric.
5. **Port to a bridgev2 Go connector** (mapping in §9), reusing this Python as the
   protocol reference. *(Go port last: the risky protocol work is already de-risked.)*

---

## 13. Sending messages: the obstacle, and how to add it in the future

Sending is the one capability this prototype does not ship, and the reason is
specific, not a matter of effort or language.

**The obstacle (measured live).** A web DM is **not** sent over a REST endpoint that
could be signed and replayed. It goes out as a **length-delimited protobuf ("pbbp2")
frame over the frontier WebSocket** (`wss://im-ws.tiktok.com/ws/v2`), and that frame
is signed by TikTok's own `webmssdk` in the page (`frontierSign` / `registerWsSigner`,
the same SDK that mints the socket's `access_key`; [Obs] on `window.byted_acrawler`
during the 2026-09-18 capture, not preserved as a fixture). Confirmed: when the
composer sends, the outbound WS frame carries the message text and is already signed;
no `/v1/message/send` HTTP call is made. So there is nothing to "replay"; to send off
our own code we would have to reproduce `frontierSign`, which is exactly the signer we
chose never to reverse. This is a signing wall, not a language or framework wall; a
Go bridgev2 connector does not change it.

**Five ways to add sending later, cheapest/most-fragile first:**

1. **Browser-in-the-loop, hardened (fastest, medium reliability).** Keep driving
   TikTok's own composer (the page signs), but make it reliable: pre-open the target
   conversation in the background page when the user selects it (so opening is not
   done at send time), a per-conversation send queue (one action at a time; a real
   person has one device), and confirm delivery by the message returning over the
   frontier (never blind-replay; reconcile on the next read). This is a real product
   path; it is how a "foreground, live while the app is open" iOS `WKWebView` mode
   would send too (§10). Cost: a browser context per active sender.

2. **A signing-oracle service (the scalable version of #1).** Run *one* browser
   that exposes TikTok's own signer as an endpoint:
   `expose_binding("__sign", (parts) => window.byted_acrawler.frontierSign(parts))`.
   Lightweight per-user clients build the send frame, call the oracle to sign it, and
   push it over their own websocket. This decouples signing from a browser-per-user
   and is the same shape as the escalation ladder in §8 (shared signing browser plus
   thin clients). It needs the exact frame layout, which a capture of one real send
   gives (we already decode the receive side).

3. **A ported/maintained web signer (the robust, whatsmeow-style path).** Reproduce
   `frontierSign` off-browser, by reversing `webmssdk` or transpiling it, so the send
   frame is signed with no browser at all. This is exactly the role `whatsmeow` plays
   for mautrix-whatsapp and `eulerstream` plays for TikTok **LIVE**: a maintained
   library that owns the signing. It is the most work and the most durable; it is
   also the thing whose absence is *the* reason sending is hard. A bridgev2 Go
   connector would depend on this, not replace it.

4. **The mobile send path, if the gates open.** `/v1/message/send` with the mobile
   `x-argus`/`x-gorgon` signer (SignerPy) already has a confirmed envelope
   (`bridge/im.py`). It is blocked today by IP reputation and device registration
   (TTEncrypt, §0 Phase 1). With a clean per-user residential IP *and* a way to
   register a device, this becomes viable, independent of the web signer.

5. **A vendor send endpoint (buy, not build).** TikAPI exposes
   `POST /user/message/send`; the `TikApiProvider` seam already implements it. This
   trades the signing problem for a paid dependency, polling-only inbound, and an
   explicit ToS decision (the TikAPI trade-off, §1); acceptable as a fallback, not as
   the default.

**Recommended sequence.** Start with #1 for a working-but-supervised send in the
demo/product, move to #2 (signing oracle) to scale it, and treat #3 (a maintained web
signer) as the real long-term investment, the same investment every serious bridge
(WhatsApp, LINE, Instagram) made for its platform. In every case the invariants hold:
one stable identity, one action per user at a time, and **never blind-replay an
ambiguous send**: mark it pending and reconcile against the next history fetch, which
is why `get_by_conversation` (already built for "load older") is also the delivery
check.