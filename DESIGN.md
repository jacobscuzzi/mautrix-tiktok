# TikTok DM bridge — design

Exercise 2, Knows case study. Goal: anyone logs in with their TikTok account and
their conversations (contacts, profiles, threads, messages) are fetched on our
backend and land in a mautrix-style pipeline. Judged on reasoning, failure modes,
security, one health metric, and how far it gets — not production-readiness.

Epistemics: **[Obs]** observed live, **[Inf]** inferred from a credible source,
**[Guess]** unverified. What is proven versus assumed is listed in §11.

---

## 1. Path selection — the web path ships, the mobile path is documented

Two DM surfaces exist; we probed both live.

- **[Obs] The mobile app API is correct but not runnable from our backend.** With
  valid SignerPy signatures accepted by TikTok, `send_email_code`/`account_lookup`
  return `error_code 7` from a datacenter IP on the first attempt (IP reputation,
  not signing), and `device_register` needs TTEncrypt, which no public library has
  (`device_id: 0`). So the mobile path is gated by IP + device registration + a
  signer we do not own.
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

**Cost, stated honestly:** one browser context per connected user while syncing.
Escalation levers, documented not built (§8): a shared "signing-oracle" browser +
`curl_cffi` HTTP polling; driving the frontier socket directly (pbbp2 framing
already decoded in `bridge/proto.py` + `bridge/web/frontier.py`).

The mobile client (`bridge/im.py`, `bridge/client.py`, `bridge/signing.py`) and a
third-party vendor (`TikApiProvider`) stay in the tree behind one seam. The new
web backend is `bridge/providers/web.py` (`WebProvider`). `Syncer`, `SyncState`,
`normalize`, `metrics`, `session_store`, `pipeline` are the shared pipeline.

---

## 2. Architecture — six layers + a provider seam

```
auth/session -> [ MessageProvider ] -> sync/ingest -> normalize -> pipeline(SQLite) -> API
                   |    |     |                                          ^
              web  mobile  tikapi          state/storage: session_store (AES-GCM),
                                           SyncState cursors + dedup  <---+
```

1. **auth/session** — login flows (password ladder, cookies import, QR), device
   identity per user (the persistent browser profile; `ttwid` minted once, never
   rotated), `WebSession` <-> encrypted blob.
2. **provider seam** (`bridge/provider.py`) — the build-vs-buy boundary. A
   `MessageProvider` is one login's `NetworkAPI`: `list_conversations`,
   `get_messages`, `list_contacts`, `get_profile`, `send_text`, `mark_read`,
   `subscribe`. `WebProvider`, native `IM`, and `TikApiProvider` are interchangeable.
3. **sync/ingest** (`bridge/sync.py`) — backfill (cursor pagination) + poll with
   gap reconciliation against stored watermarks; no message lost across a gap.
4. **normalize** (`bridge/normalize.py`) — TikTok objects -> canonical `User`/
   `Thread`/`Event{kind}`. The bridgev2 boundary in miniature.
5. **pipeline** (`bridge/pipeline.py`) — SQLite raw layer everything reads from:
   `logins`/`users`/`threads`/`events`, `stream_order` = ms ts, unique
   `(thread_id, message_id)`, idempotent upsert, optional webhook + dead-letter.
6. **state/storage** — envelope-encrypted `session_store`, per-user cursors, dedup.

The provider, normalize, and pipeline layers have no auth imports, so each is
unit-tested in isolation.

---

## 3. Login and session

Three flows, one state machine (`bridge/auth/login.py`, `LOGIN_MODES`):

- **password** (bridgev2 `user_input`) — the ladder (`web_password_login.py`):
  restore session -> `is_alive()` -> alive: done; else **exactly one** password
  login into TikTok's own form -> success: persist; challenge (captcha/2FA/verify/
  IDV): stop with `needs_user`, persist nothing new; wrong credentials:
  `bad_credentials`, no further automatic attempt; locked: `blocked`. Selectors are
  pinned from the capture in one dict with the verified date; a missing selector is
  `schema_change`, not a crash. **Never retry a challenge, never auto-loop.**
- **cookies** (bridgev2 `cookies`) — `web_cookie_import.py`: import a session
  captured elsewhere (laptop -> server, and the future iPhone path). Pins the
  backend context's UA/locale/timezone/viewport to what was reported and reuses the
  reported `ttwid`; a UA that contradicts an existing profile is refused with
  `fingerprint_mismatch`, never silently replaced.
- **qr** (bridgev2 `display_and_wait`) — passwordless; the phone approves.

`is_alive()` is the cheapest logged-in call the capture shows:
`GET /passport/token/beat/web/` (`error_code 0` = alive), with a `/messages` load
+ `/login` 302 / `status_code 8` fallback. Password custody default is
`store_password: false` — in memory for the single login, then dropped.

---

## 4. How messages land

`WebProvider` calls run inside the page (signed). `list_contacts` reads
`/api/im/spotlight/relation/` (real: 18–19 followings with avatars); `get_profile`
reads `/tiktok/v1/im/user/profile/`; conversations/messages read the `im-api`
surface. Each response is parsed by an isolated parser that raises `SchemaChange`
on a renamed/missing container. Realtime rides the frontier websocket: inbound
pbbp2 frames are gunzipped (`frontier.py`) to `(conversation_id, message_id, ts,
sender_id, text)`; unknown methods are dropped, never raised; `x_frontier_msg_id`
+ message id is the dedup key. On any gap (reconnect, reload, id discontinuity) the
provider triggers a REST reconcile through the `Syncer` dedup path. `Syncer` writes
normalized events into the SQLite pipeline (idempotent on `(thread_id, message_id)`);
the API reads from the pipeline. Avatars download through a jar-less,
host-allowlisted client (`*.tiktokcdn.com`, `*.tiktokcdn-eu.com`), hashed by URL.

---

## 5. Failure modes (first — this is the job)

Detect at the right blast radius: **all users failing at once = our bug**
(signer/browser/key); **one user failing = that account**. Never take an
irreversible action on an ambiguous signal. Every row names the test or live
observation that backs it.

| Condition | Detection | State | Recovery | Backed by |
|---|---|---|---|---|
| Password wrong | `login-error` element | `bad_credentials` | user retries; never auto-retried | ladder unit + e2e |
| Challenge (captcha/2FA/IDV) | challenge element / `/verify` | `needs_user` + action | app opens URL -> cookies flow | ladder + api tests |
| Session expired / logged out elsewhere | `token/beat` code 8, `/login` 302 | `needs_user` | one re-login, no auto-loop | e2e logged-out-elsewhere; is_alive tests |
| Rate limited | HTTP 429 / body code 7 | `rate_limited` | backoff + jitter; do not spin | PageClient + runtime tests |
| Account locked/banned | `account-locked` / HTTP 403 | `blocked` | user action | ladder e2e; PageClient 403 |
| Region mismatch (TTP cluster vs egress) | `vregion` (EU-TTP2) vs proxy region | `needs_user`/empty | geo-match the proxy to the account | observation (region DE/EU-TTP2) |
| Frontier socket drop / gap | id discontinuity / close | reconcile | REST reconcile via Syncer dedup | frontier reconcile test |
| Page reload loses signer state | reload re-runs webmssdk | reconnect | reload `/messages`, re-subscribe | design ([Obs] page is signer) |
| TikTok DOM/schema change | missing selector / renamed field | `schema_change` | one selector/parser module to fix | `SchemaChange` parser test |
| Signer/browser stale for ALL users | cross-user 4xx spike (canary) | alert | hot-swap the browser/signer image | metric design (canary) |
| Proxy dead | transport error | `transient` | stable per-user failover, not IP cycling | proxy pool (existing) |
| Double login race | one action per user | serialize | per-user queue | invariant |
| Avatar CDN URL expired | 403 on download | refresh | re-fetch signed URL on download | avatar allowlist test |
| Ambiguous send | no confirmed server id | pending | never blind-replay; reconcile next fetch | invariant; send NotSupported here |
| Fingerprint mismatch on import | UA vs stored profile | `fingerprint_mismatch` | refuse; never rotate identity | cookie-import test |

---

## 6. Security (concrete)

The raw password is never persisted (default `store_password: false`) and ideally
never received (QR); the browser flow types it into TikTok's own page. Persisted is
the session blob + fingerprint, **envelope-encrypted at rest** (`session_store.py`):
a per-user AES-GCM data key encrypts the blob, a master key (env `BRIDGE_MASTER_KEY`
/ KMS seam) wraps the data key. If a custody mode stores the password it uses a
separate per-user data key — custody shrinks the exposure window, it does not close
it; session-only is strictly better where it works. Contact/message content is
third-party PII: encrypted at rest, deleted on logout (`SessionStore.delete`,
`DELETE /v1/logins/{id}`). Secrets come from env/KMS at deploy, never the repo; logs
redact values (a grep test asserts the ladder never logs the password). The cookie
jar is scoped to TikTok hosts; avatars download through a jar-less, host-allowlisted
client. The raw capture and the plaintext `storage_state.json` are gitignored and
shredded after a verified import (done live at G4); a length-preserving redactor
(`scripts/redact-capture.py`) strips cookies/`msToken`/`sessionid`/`ttwid`/`verifyFp`
+ email and pseudonymizes ids/handles/names before anything reaches the fixtures.

---

## 7. Health metric

**Headline — `bridge_live_session_ratio`** = connected / total logins. Every
failure mode collapses into a drop here. **Leading indicator —
`bridge_password_login_share`**: the share of connects that needed a password
login; rising means sessions are dying early, which precedes challenges and bans,
so it moves before the headline does. Also `bridge_delivery_lag_seconds` p95
(TikTok ts -> `ingested_at`) to catch silent polling lag, and
`bridge_error_total{state}`. Exposed as Prometheus text at `GET /metrics`. Alert on
a sudden headline drop (signer/browser outage, all users) and on a rising password-
login share (credential churn).

---

## 8. ToS / ban / detection, and the escalation ladder

Automating a user's own account with consent is what every messaging bridge does;
it can still breach TikTok's terms and get an account restricted, so login, status,
and delete are first-class. We do **not** add captcha solving, IP rotation, or
fingerprint randomisation — those are detection evasion and out of scope. The
legitimate levers, in order: **per-user residential proxy** geo-matched to the
account (`proxy.py`, stable per-login, no IP cycling) -> **a shared signing-oracle
browser** (one browser signs, plain `curl_cffi` HTTP polling per user, far cheaper
than a browser each) -> **edge egress** matching the account's region. One stable
device/browser identity per user, never rotated (rotation reads as takeover).

---

## 9. bridgev2 mapping (documented, not implemented in Go)

| bridgev2 | this prototype |
|---|---|
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

## 10. iPhone client path (Task F)

The bridge stays on the backend; an iOS app is a client of the §"API" the same way
Knows' apps are clients of its mautrix fork. iOS cannot run a long-lived background
sync (Background App Refresh is throttled, no persistent sockets), so on-device
ingest is not a goal. The **cookies flow is the phone's login path**: password
relay -> on `needs_user` the API returns `{action:{open_url, then:"cookies"}}` ->
the app opens TikTok's page in a `WKWebView` -> the user solves the challenge ->
the app reads `WKHTTPCookieStore` -> `cookies` step -> connected. The injected
assets (`bridge/web/inject/{request,ws_hook,fetch_tap}.js`) are standalone, post
through one `postToHost(kind, payload)` (server: `expose_binding`; iOS:
`WKUserScript` at `.atDocumentStart` + `WKScriptMessageHandler`), and never touch
navigator properties (tested). **What does not transfer:** the mobile-app-API path
(SignerPy, `device_register`, TTEncrypt) hits the same wall on a phone as on a
server; "run the bridge on the device" is rejected for iOS background limits; edge
egress stays a documented lever, not a build target.

---

## 11. What is proven, and what is not

- **Runnable demo:** `./bridge-app.sh` starts the bridge (`bridge/live.py` — one
  Playwright worker per user) + the `wrapper/` tester UI (connect → chats → health).
  Real TikTok login in a local browser; the account's chats render and DMs stream
  in live; sessions are envelope-encrypted and wiped on logout.

- **Verified live:** mobile signing accepted by TikTok (blocked only by IP); a
  headless browser loads TikTok + a scannable QR from a datacenter IP; a logged-in
  web capture of the DM surface; the in-page re-signing probe (**the page signs**);
  **G4: the captured session imported read-only, 19 real contacts pulled through
  `WebProvider` into SQLite; then a live DM exchange (~10 messages) captured over
  the frontier socket and decoded to text with correct self/peer sender attribution**
  (2026-09-18).
- **Verified against fixtures:** contacts/profile parsers (real redacted data);
  conversation/message parsers + cursor pagination + `Syncer` dedup (synthetic
  fixtures, empty live inbox); frontier decode; SchemaChange on a renamed field.
- **Fake-platform only:** the full password ladder (session reuse across restart =
  zero logins, logged-out-elsewhere = one login, 2FA stops, wrong password not
  retried, locked = blocked) driven by a real headless browser.
- **Not built:** the live web conversation-list JSON mirror (REST inbox was empty —
  synthetic-tested; but the realtime message body IS captured live); outbound send /
  mark-read over the web channel (no fixture, allow-send was no); the Go mautrix
  appservice (mapping documented, §9); mobile device registration (TTEncrypt).

---

## 12. Monday plan — the first five tickets

1. **Re-capture a non-empty inbox** (one real DM) to fill `messages_*` +
   `ws_inbound_dm`, pin the frontier message-body field numbers
   (`frontier-fields.md`), and replace the synthetic message fixtures.
2. **Enable web `send_text`** from a `--allow-send` capture: add the `send_text`
   fixture, implement, keep the never-blind-replay rule.
3. **Ship the signing-oracle escalation**: one browser signs, `curl_cffi` HTTP
   polling per user, to drop the browser-per-user cost.
4. **Per-user residential proxy wiring end to end** with region matching to the
   account's TTP cluster, and the cross-user 4xx canary alert on the metric.
5. **Port to a bridgev2 Go connector** (mapping in §9), reusing this Python as the
   protocol reference. *(Go port last: the risky protocol work is already de-risked.)*
