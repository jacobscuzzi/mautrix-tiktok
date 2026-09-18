# Exercise 2 — Beeper extraction: what to reuse to build a TikTok DM bridge
 
Purpose: a reference of everything in Beeper's open-source bridges worth copying for an
unofficial TikTok DM bridge, grounded against the four Knows internal TikTok scripts. This is
step one (the "what exists and what to steal"); the design doc and repo build on it.
 
Repos read: `mautrix-go` (the `bridgev2` framework), Beeper `tumblr-dms`, `reddit`,
`dummybridge`, `line`, `bridge-manager`; `mautrix-meta` (Instagram), `mautrix-linkedin`,
`mautrix-whatsapp`. Knows scripts: `rest.py`, `Videos_Manager.py`, `CommentsScraper.py`,
`TiktokUserInfo.py` (from the exercise's Drive folder).
 
---
 
## 0. The one-paragraph answer
 
Knows already runs a fork of the mautrix bridges, so the TikTok bridge should be a **bridgev2
network connector in Go**, structured like **`tumblr-dms`** (the only true HTTP-scraping bridge:
cookie session, pinned user-agent, polling plus durable job tables). Borrow the **username/password
plus captcha/2FA login state machine from `reddit`**, the **mobile-device-emulation and
challenge-as-login-step patterns from `mautrix-meta` (Instagram)** since it is the closest analog
and the one Knows already forks, and the **request-signing-in-its-own-package pattern from `line`**
(which transpiled a white-box crypto blob to Go). The single hard dependency is TikTok's request
signing (`X-Argus`, `X-Gorgon`, `X-Ladon`, `X-Khronos`, `X-Bogus`, `msToken`) — the Knows scripts
already solve it with the open-source `SignerPy`, which is the TikTok analog of LINE's `ltsm.wasm`.
 
---
 
## 1. What the Knows scripts already prove (the TikTok side)
 
These four scripts are the most valuable input: they show how Knows already talks to TikTok, so
the bridge does not start from zero on the protocol.
 
- **Two API surfaces.**
  - **Mobile app API**: hosts like `api16-normal-c-alisg.tiktokv.com`, `api22-normal-...`,
    `api.tiktokv.com`, `api2.musical.ly`. Emulates the TikTok Lite / musical.ly Android app.
    Authenticated with a `sessionid` cookie; every request carries signed headers.
  - **Web API**: `www.tiktok.com/@<user>` pages, scraping the
    `__UNIVERSAL_DATA_FOR_REHYDRATION__` / `SIGI_STATE` JSON blob for profile, avatar, `secUid`,
    `userId`, stats. No signing, but rate-limited and captcha-gated (`curl_cffi` browser
    impersonation used to get past TLS fingerprinting).
- **Login is the passport flow** (`rest.py`): `POST /passport/account_lookup/username/` then
  `POST /passport/user/login_by_passport_ticket/`, against the same rotating host list. Session is
  held via the `sessionid` cookie thereafter. The verification decision (email domain / phone
  prefix) comes back in the `x-tt-verify-idv-decision-conf` response header.
- **Request signing is the crux.** All mobile calls call `SignerPy.sign(params, cookie)` and attach
  `x-argus`, `x-gorgon`, `x-ladon`, `x-khronos`, and `x-ss-stub`. `SignerPy` is open source
  (`github.com/is-L7N/SignerPy`); alternatives exist (`tiktok-signer` on PyPI, `TikTok-Encryption`).
  This is exactly analogous to LINE's `x-hmac` produced by the LTSM WASM blob.
- **Device identity is a large fixed parameter block**: `device_id`, `iid` (install id),
  `openudid`, `cdid`, plus `aid` (`1233` = musical_ly, `1340` = musically_go / TikTok Lite),
  `app_version`, `manifest_version_code`, `device_type`/`device_brand` (e.g. realme RMX3269),
  region/locale, and a matching `User-Agent`
  (`com.zhiliaoapp.musically/... Cronet/TTNetVersion:...`). One device profile per account.
- **Rate-limit hygiene already present**: rotating host list, optional proxy list, random 1–2 s
  delays between paginated calls, capped parallelism (5 workers), cursor pagination with `has_more`.
 
**Gap to flag:** none of the four scripts touch direct messages. They cover profiles, videos, and
comments. So the DM inbox, thread, and message endpoints (TikTok's `/v1/im/...` family, plus the
`im-ws` websocket if used) are what the bridge still has to reverse-engineer. But auth, session,
signing, device emulation, and pagination are proven — reuse the parameter block, host list, and
`SignerPy` verbatim (port `SignerPy` to Go, or shell out to it initially).
 
---
 
## 2. The framework: bridgev2 architecture
 
Every bridge implements a **network connector** against `mautrix-go/bridgev2`. The framework owns
Matrix, portal/ghost/message state, backfill orchestration, and bridge-state plumbing. You write
three interfaces plus optional extras.
 
| Interface | Role | Key methods |
|---|---|---|
| `NetworkConnector` | Entry point, not user-specific | `Init`, `Start`, `GetName`, `GetDBMetaTypes`, `GetConfig`, `GetLoginFlows`, `CreateLogin`, `LoadUserLogin`, `GetCapabilities` |
| `LoginProcess` | Per-login state machine | `Start`, `Cancel` + `SubmitUserInput` / `SubmitCookies` / `Wait` per step type |
| `NetworkAPI` | Per-login remote client | `Connect`, `Disconnect`, `IsLoggedIn`, `LogoutRemote`, `IsThisUser`, `GetChatInfo`, `GetUserInfo`, `HandleMatrixMessage` |
| `RemoteEvent` (via `simplevent.*`) | One inbound event | queued with `Bridge.QueueRemoteEvent` |
 
Optional interfaces you will want: `BackfillingNetworkAPI` (`FetchMessages`),
`IdentifierResolvingNetworkAPI` + `UserSearchingNetworkAPI` (contacts / start-DM — this is where
"fetch friends and contacts" plugs in), `ReadReceiptHandlingNetworkAPI`, `PushableNetworkAPI` +
`BackgroundSyncingNetworkAPI` (push wake-ups), `NetworkAPIWithUserID`, `LoginProcessWithOverride`
(re-login into the same account).
 
**Login step types** (`user_input`, `cookies`, `display_and_wait`, `client_http`, `webauthn`,
`complete`). Input field types: `username, password, phone_number, email, 2fa_code, token, url,
domain, select, captcha_code`. Cookie-field sources: `cookie, local_storage, request_header,
request_body, special` plus `extract_js` and `WaitForURLPattern` (keeps a webview open through a
challenge). This vocabulary already covers every TikTok login challenge — you never invent UI.
 
**Inbound message flow**: network lib → `NetworkConnector` builds a `simplevent.Message[T]` →
`QueueRemoteEvent` → `GetPortalByID` → portal event queue → `ConvertMessage` → Matrix send → DB
insert. You provide `ConvertMessageFunc`; the framework does room creation, dedup, and ordering
(`StreamOrder = timestamp ms`).
 
**Persistence**: standard tables (`user`, `user_login`, `portal`, `ghost`, `message`, `reaction`,
`backfill_task`, `kv_store`, ...). Network-specific state is a **JSON blob in
`user_login.metadata`**, typed by `GetDBMetaTypes()`. That blob is **plaintext** — only Matrix olm
pickles are encrypted (`encryption.pickle_key`). So TikTok session secrets must be encrypted by you
before marshalling, or protected at the DB layer.
 
**Session resume on restart**: `StartLogins` loads every `user_login` row → `LoadUserLogin` (builds
the client struct, must not fail even on bad metadata) → `Client.Connect`. If the session is dead,
`Connect` emits `StateBadCredentials`; it must not make the account silently vanish.
 
---
 
## 3. Which bridge to copy, and for what
 
| Source | What to take |
|---|---|
| **`tumblr-dms`** | **Overall skeleton.** Cookie-session HTTP scraping bridge with pinned UA in metadata, session validation on `Connect`, connection generations (clean reconnect without goroutine leaks), polling + durable job tables, avatar download via `Avatar.Get`, DM-only portal keying, secret redaction in logs, FCM web-push receiver. |
| **`reddit`** | **Username/password + captcha-in-webview + OTP login sequencing**: a blocking library login driven on a goroutine, pumping challenge requests out and tokens in over channels, restarting the flow with a 2FA code when needed. |
| **`mautrix-meta` (Instagram)** | **Closest analog** (Knows forks it). Mobile Android device emulation, one durable device identity per Matrix user, every challenge (2FA / method-select / captcha / approve-on-phone) modelled as a login step, reconnection-state snapshot with max age, `get_proxy_from` proxy rotation on transport error, full bridge-state error vocabulary, redacted login logging, inbox-first sync then backfill. |
| **`line`** | **Reverse-engineering method** (`.agents/` capture→analyze→implement loop) and **signing in its own package** (white-box crypto transpiled to Go). Source-aware token recovery, `SessionInvalidated` flag so the bridge doesn't fight the user's phone session, error taxonomy as `Is*` predicates, function-variable + `roundTripFunc` test seams. |
| **`linkedin`** | **Cookie-jar persistence** (`StringCookieJar` JSON-embedded in metadata), `IsLoggedIn` = session-cookie presence, an `authedRequest` builder with header presets and typed `ResponseError`, a **realtime loop skeleton** (bad-creds vs transient vs unknown triad, session-id rotation, capped backoff, resync on reconnect), sync-token-first + cursor-fallback pagination. |
| **`dummybridge`** | Minimal reference for every login step/param shape, and `cmd/loginhelper` to exercise the cookies/display-and-wait flows locally. |
| **`bridge-manager`** | How Beeper runs a self-hosted bridge over websocket instead of appservice HTTP (`bbctl register/config/run`), the hungryserv registration, config template. |
 
---
 
## 4. Login flow for TikTok (synthesized)
 
Offer **two flows from day one** (Instagram's lesson): `password` and a `cookies` fallback for when
password login is blocked.
 
Password flow steps, mapping to the framework:
1. `user_input` — fields `username` + `password`. Never retry/redirect the credential POST
   (Instagram: "neither retries nor redirects may silently submit the password again").
2. Backend runs the passport login (`rest.py` endpoints), TikTok-style encrypting the password;
   captures `sessionid` / `sid_tt` / `tt-token` and the device ids (`device_id`, `iid`, `openudid`,
   `cdid`).
3. If TikTok returns a verification decision (`x-tt-verify-idv-decision-conf`) →
   `user_input` `2fa_code` step (SMS/email), pattern `^[0-9]{4,6}$`. Rejected code → one automatic
   resend, honoring cooldown.
4. If captcha/slider → a `cookies` step with `extract_js` running in the app's webview to harvest
   the captcha/`msToken` token (Instagram's captcha-as-cookies-step pattern). Fingerprint the token
   so it is never replayed.
5. `complete` → `user.NewLogin(ctx, &UserLogin{ID: tiktokUserID, RemoteName, RemoteProfile,
   Metadata}, &NewLoginParams{DeleteOnConflict: true})` then `go ul.Client.Connect(...)`.
 
`UserLoginID` must be the stable TikTok user id (it is the `user_login.id` primary key and the
`remote_id` in every bridge-state update). Implement `LoginProcessWithOverride` so re-login after
`BAD_CREDENTIALS` reuses the same login (`?login_id=<old>`).
 
Suggested `UserLoginMetadata`:
```go
type UserLoginMetadata struct {
    SessionCookies map[string]string `json:"session_cookies,omitempty"` // sessionid, sid_tt, tt-token, ...
    DeviceID       string            `json:"device_id,omitempty"`
    InstallID      string            `json:"iid,omitempty"`
    OpenUDID       string            `json:"openudid,omitempty"`
    CDID           string            `json:"cdid,omitempty"`
    UserAgent      string            `json:"user_agent,omitempty"`     // pinned to the logging-in identity
    UserID         string            `json:"user_id,omitempty"`
    Username       string            `json:"username,omitempty"`
    Region         string            `json:"region,omitempty"`
    // encrypt the secret fields before marshalling; the blob is stored plaintext
}
```
Add `MarshalZerologObject` redaction and a `normalized...` validator (Tumblr pattern) so secrets
never hit logs and `LoadUserLogin` never rejects a login.
 
**On storing the password**: LINE stores the plaintext password for silent re-login, but only
because LINE issues a device `certificate` that bypasses the second factor. TikTok has no
equivalent, so a stored password will hit captcha/verification on reuse. Prefer to store the
**session + device identifiers** and treat session loss as `BAD_CREDENTIALS` (re-login), not a
stored password.
 
---
 
## 5. Session, HTTP client, and signing
 
Put the API client in its own package (`pkg/tiktok/`) with **no bridgev2 imports** (Tumblr/LINE
separation). Give it:
- `Options{UserAgent, SessionCookies, DeviceID, InstallID, HTTPClient}`, a `cookiejar` with
  `publicsuffix`, `SessionSnapshot()` and a `SessionUpdates()` channel so the connector persists
  cookie/token rotation after every request (Tumblr pattern).
- A `do()` method with envelope parsing (TikTok returns **HTTP 200 with a body-level `status_code`**
  — handle that like LINE's refresh guard), one auth refresh, transient retry on 5xx, and typed
  `Error{StatusCode, ErrorCode}` with `IsAuthError` / `IsNotFound` / `IsRateLimited` predicates.
- A **signing hook** in `setHeaders`: call the Go port of `SignerPy` to attach
  `x-argus`/`x-gorgon`/`x-ladon`/`x-khronos`/`x-ss-stub` (mobile) — this is the LINE
  `Runner.GetSignature` analog. Keep it in its own package (`pkg/tiktoksign/`) so version/algorithm
  changes are isolated and testable with deterministic vectors.
- Browser-impersonating TLS for the web surface (Instagram forces a Chrome uTLS fingerprint;
  `TiktokUserInfo.py` uses `curl_cffi impersonate`).
- Proxy support via a `get_proxy_from` hook and **rotate proxy on transport error** (Instagram's
  `UpdateProxy(reason)`), because TikTok geo-gates and rate-limits by IP.
 
Endpoints the client needs: `Login`, `CurrentUser`, `ListFriends/Contacts`, `ListConversations(cursor)`,
`GetMessages(convID, cursor)`, `SendText`, `MarkRead`, `DownloadMedia` (host-allowlisted, jar-less client).
 
---
 
## 6. Inbound sync and data model
 
TikTok has no public SSE, so expect **polling of the DM inbox with a cursor** (LinkedIn's
`syncConversations` shape), optionally upgraded to the `im-ws` websocket later (needs
capture-driven RE, same treatment as LINE's SSE / Instagram's DGW socket).
 
Design (Tumblr's, trimmed):
- On `Connect`: `StateConnecting` → validate session with a cheap `CurrentUser` call → list
  conversations → for each, queue `simplevent.ChatResync{CreatePortal: true, GetChatInfoFunc,
  LatestMessageTS}` (inbox-first; do not backfill from inbox stubs) → `StateConnected`.
- Polling loop with **jittered adaptive interval** (hot ~2 s after activity, idle up to 60 s), a
  head-probe comparing latest message ids against an in-memory seen-set, and periodic full
  reconciliation.
- Per changed conversation: fetch messages → `portal.CreateMatrixRoom`/`UpdateInfo` with
  `ChatInfo{Type: DM, Members: ChatMemberMap{"": self, otherUID: other}, OtherUserID, Avatar,
  CanBackfill: true}` → `QueueRemoteEvent(&simplevent.Message[tiktok.Message]{...ID, Data,
  ConvertMessageFunc})` → `simplevent.Receipt` for read state.
- **Backfill**: implement `FetchMessages` with cursor pagination (`Cursor` = TikTok pagination
  token, `HasMore` from the API, `AggressiveDeduplication: true`). The framework does initial +
  catchup automatically from the resync events (`max_initial_messages` / `max_catchup_messages`).
- **Durability**: start with the in-memory seen-set; add a bridge-private DB (`tiktokdb`, Tumblr's
  `tumblrdb` pattern) with a conversation-sync job table (`revision` for optimistic concurrency,
  `attempt_count`, `next_attempt_ts` backoff, `last_error_code`) and a sync-state watermark once
  durability across restarts matters. Tumblr keeps these because bridgev2 persists portals/messages
  but not *work*.
 
**IDs**: `PortalKey{ID: conversationID, Receiver: loginID}` for DMs always (WhatsApp/Tumblr
invariant), `UserID` = TikTok user id, `UserLoginID` = own user id, `MessageID` = TikTok message id.
If TikTok exposes multiple ids per object (Instagram has three thread ids), keep bidirectional
mapping tables with in-memory caches and use the stable numeric id as the Matrix portal/user id.
 
**Contacts / friends** ("fetch friends and contacts" in the brief): implement
`IdentifierResolvingNetworkAPI` + `UserSearchingNetworkAPI` over the friends/following list. Avatars:
`Avatar{ID: sha256(url), Get: downloadFunc}`, from the web profile blob (`avatarLarger`) or the
mobile user object.
 
---
 
## 7. Provisioning and "login from our app"
 
The backend logs users in through the **provisioning API** at `/_matrix/provision/`:
- Auth: `Authorization: Bearer <provisioning.shared_secret>` (≥16 chars, backend-only). Acting user
  is `?user_id=<mxid>`. Do **not** enable `allow_matrix_auth` unless the app itself holds Matrix
  tokens.
- `POST /v3/login/start/password?user_id=@app-user:domain` → `{login_id, ...LoginStep}`.
- `POST /v3/login/step/{login_id}/{step_id}/user_input?txn_id=<txn>` with `{"username":...,
  "password":...}`. Reuse `txn_id` for idempotent retries. 30-minute process context;
  `display_and_wait` long-polls.
- `complete` returns `user_login_id`; store `mxid ↔ user_login_id` in the app backend.
- `GET /v3/whoami` returns each login's last BridgeState — the app's "connected / needs re-login" UI.
- `GET /v3/contacts`, `POST /v3/create_dm/{identifier}`, `POST /v3/backfill/{roomID}` as needed.
 
The chat-command login path drives the same `LoginProcess`, so the app flow and the debug command
flow share one implementation.
 
---
 
## 8. Failure modes, detection, recovery (exercise asks for this first)
 
Map TikTok conditions onto the bridge-state vocabulary and register human strings in
`status.BridgeStateHumanErrors`:
 
| Condition | Detection | Bridge state | Recovery |
|---|---|---|---|
| Wrong password | login response error | login-step `RespError` `FI.MAU.TIKTOK_BAD_PASSWORD` | user retries step |
| 2FA / verification required | `x-tt-verify-idv-decision-conf` header / body flag | extra `user_input` step | user enters code; one auto-resend |
| Captcha / slider | body flag / challenge redirect | `cookies` step (webview) | user solves in webview; token fingerprinted |
| Session expired / logged out elsewhere | 401-equivalent body `status_code`, cookie cleared | `BAD_CREDENTIALS` + `UserActionRelogin`, set `SessionInvalidated` | re-login via `?login_id=<old>`; do not auto-relogin and fight the phone |
| Rate limited | HTTP 429 or body code | `TRANSIENT_DISCONNECT` `tt-rate-limited` | client backoff + jitter, proxy rotate |
| Account banned / locked | redirect / body code | `BAD_CREDENTIALS` `tt-account-locked` + `open_url` | user action |
| Transport / socket drop | HTTP/dial error | `TRANSIENT_DISCONNECT` | own backoff, capped 60 s |
| Unclassified | anything else | `UNKNOWN_ERROR` | framework auto-reconnect (`unknown_error_auto_reconnect`, max ~10) |
 
Framework behavior to rely on: **no automatic re-login** (bad creds always need the user);
auto-reconnect only for `UNKNOWN_ERROR`; user notices to the management room gated by
`bridge_status_notices`; `transient_state_debounce` suppresses reconnect flapping. Never replay an
ambiguous outbound send — Tumblr wraps sends in a `Definite` flag and, at the durable tier, an
at-most-once outbound table, because TikTok sends have no idempotency key.
 
**Fragile manual steps this design kills**: no human copies cookies or solves a captcha per user in
a terminal; the webview + provisioning API make login a self-serve app flow, and session rotation is
persisted automatically after every request.
 
---
 
## 9. Security (one concrete paragraph)
 
Store only `mxid ↔ user_login_id`, the last BridgeState, and the provisioning shared secret in the
app backend. The TikTok session (cookies, `device_id`, `iid`, `msToken`, timestamps) lives in
`user_login.metadata` in the framework DB (Postgres in production). That blob is stored **plaintext**
by the framework, so encrypt the secret fields with a service-held key before marshalling, or use
DB-level encryption; the framework only encrypts Matrix olm pickles (`encryption.pickle_key`). Never
persist the raw password — log in once, keep the session, treat session loss as `BAD_CREDENTIALS`.
Inject `as_token`, `hs_token`, DB URI, and the shared secret at deploy time via `env_config_prefix`
(`BRIDGE_..._FILE` reads secrets from files). Redact all secrets from logs (`MarshalZerologObject`
redaction; log only booleans/keys about failed auth, never values). Scope the cookie jar to TikTok
domains; download media through a jar-less, host-allowlisted client.
 
---
 
## 10. One health metric
 
`connected_logins / total_logins` — the fraction of `user_login` rows whose last BridgeState is
`CONNECTED`. It is already emitted per account, deduplicated, and TTL-stamped, every failure mode
collapses into "not CONNECTED", and TikTok's dominant failure (credential/session churn) shows up
directly as a rising `BAD_CREDENTIALS` share. Alert on the `BAD_CREDENTIALS` fraction and on
`UNKNOWN_ERROR` counts approaching the auto-reconnect cap. The framework ships no Prometheus metrics;
add an exporter that reads the states pushed to `homeserver.status_endpoint`.
 
---
 
## 11. Build order for the repo
 
1. `go.mod` on `maunium.net/go/mautrix` bridgev2 + `go.mau.fi/util`; `cmd/tiktok/main.go` =
   `mxmain.BridgeMain{Connector: &connector.TikTokConnector{}}` (copy dummybridge).
2. `pkg/tiktoksign/` — Go port of `SignerPy` (or a cgo/subprocess shim initially), with vectors.
3. `pkg/tiktok/` — API client: options, cookiejar, `do()` + envelope, typed errors, signing hook,
   endpoints (`Login`, `CurrentUser`, `ListConversations`, `GetMessages`, `SendText`, `MarkRead`,
   `ListFriends`, `DownloadMedia`). Reuse the device-parameter block and host list from the scripts.
4. `pkg/tiktokid/id.go` — portal/user/login/message id constructors (DM `Receiver = loginID`).
5. `pkg/connector/` — `connector.go`, `config.go`, `example-config.yaml`, `capabilities.go`,
   `dbmeta.go` (metadata + redaction + validator).
6. `pkg/connector/login.go` — flows, steps, `completeLogin`, `LoginProcessWithOverride`, error mapping.
7. `pkg/connector/client.go` + `lifecycle.go` — `LoadUserLogin`, `Connect` (validate → states),
   `Disconnect`, `IsLoggedIn`, `LogoutRemote`, `IsThisUser`, `GetChatInfo`, `GetUserInfo`,
   session-update persistence loop.
8. `pkg/connector/sync.go` + `inbound_sync.go` — polling loop, conversation → portal, message events.
9. `pkg/msgconv/from_tiktok.go` — text/image/notice → `ConvertedMessage`.
10. `pkg/connector/backfill.go` — `FetchMessages` cursor pagination.
11. `pkg/connector/handlematrix.go` + `read.go` — outbound text, read receipts.
12. `pkg/connector/startchat.go` — `ResolveIdentifier` / `SearchUsers` over friends (contacts).
13. Later: `tiktokdb` durable job tables; `im-ws` websocket if it exists; outbound at-most-once table.
 
---
 
## 12. Pitfalls Beeper's bridges clearly hit (so budget for them)
 
- Platforms change app ids / protocols / signing versions repeatedly — pin exact versions, expect
  to maintain more than one login path, and assert request shape in tests.
- Silent logouts need dedicated detection (cookie deletion on redirect, body-level error codes,
  socket 4003-style close) plus a `SessionInvalidated` flag so the bridge doesn't re-login into and
  kick the user's real session.
- Media URLs expire → refresh CDN links on download; large videos need chunked/range fallback.
- Inbox listings return stub messages unsafe for backfill — always fetch the full thread first.
- Reaction deletions can arrive without the original sender — store reaction↔message mappings.
- Proxy must be applied to the **login** client too, and be separately configurable for media.
- Rate limiting and 5xx flakiness are constant — adaptive polling, jitter, capped backoff, cached
  reconnection state, periodic forced full reconnect.
  