# TikTok DM Bridge (prototype)

An unofficial TikTok DM bridge prototype: it authenticates to TikTok, holds a
mobile-bound session, and pulls direct messages into a normalized pipeline. Python,
targeting the mobile app protobuf API. This README is organized around how it
breaks, because that is what the job is about. Design detail is in `DESIGN.md`.

## Phase 0 finding (the surface)

Probed live on 2026-09-16.

- **[Obs] DMs are the mobile app API, as protobuf.** `/v1/conversation/list/` and
  `/v1/client/unread_count/` on `api16-normal-*.tiktokv.com` return HTTP 200,
  `application/x-protobuf`. The decoded envelope matches ByteDance IM `Response`:
  field 3 = status_code, field 4 = error_desc, field 7 = log_id.
- **[Obs] No web JSON DM REST surface.** `www.tiktok.com/api/im/...` returns
  `"url doesn't match"`; the web client uses the same IM SDK over a websocket.
- **[Obs] Status ladder:** `200005` (no session/signing) -> `200001` (valid mobile
  session, IM not initialized) -> `0` (success).
- **[Guess] Open crux:** whether a desktop-web login session carries to the mobile
  IM host. Cookie names are shared, but `200001` implies IM binds to a mobile
  device registered through the mobile passport flow. Unverified; needs a real
  login to close. This is why login is QR-primary (device-bound from birth) with
  the browser-capture path treated as a fallback that self-verifies.

## Run it

```bash
python3 -m unittest discover -s tests        # run the whole suite
python3 -m bridge.cmd.login qr               # exercise the QR login flow (demo fakes)
python3 -m bridge.cmd.login browser          # exercise the browser-capture flow (demo fakes)
```

No pip is available in the target environment, so the code uses only the standard
library plus `requests`, `cryptography`, and `PyYAML`.

## Failure modes

Detection is scoped to blast radius: a signer break shows up as a 4xx spike across
**all** users at once (canary), a single-user 4xx is that account's proxy/session.
No irreversible action is taken on an ambiguous signal.

| Step | How it breaks | Detection | Recovery |
|---|---|---|---|
| Signing | Algorithm rotates on app update | 4xx/rejection spike across ALL users | Hot-swap the signer process (`SubprocessSigner`), version-pin, alert on the canary |
| Signing (one user) | Bad device/proxy for one account | 4xx for that user only | Isolate; do not treat as a signer outage |
| Session | Expired / logged out elsewhere | `AuthError` (body 200003/4/5), cookie cleared | one token refresh, else mark `needs-reauth`; never spin-relogin |
| Login (QR) | User never scans | poll timeout | expire login, prompt retry |
| Login (browser) | Web session does not carry to mobile | `verify_mobile()` returns False (200001) | mark web-only, offer QR |
| Rate limit | Too many calls / flagged IP | `RateLimited` (HTTP 429) | backoff + jitter, rotate proxy, lengthen poll |
| Ban / lock | Account challenged | body code / redirect | mark `needs-reauth`, user action, do not retry |
| Outbound send | Ambiguous result after send | no confirmed `server_message_id` | never blind-replay; reconcile against next history fetch via `client_message_id` |
| Sync gap | Poll missed messages | watermark vs fetched cursor mismatch | reconcile from last watermark, dedup, advance cursor only after persist |
| IM not initialized | 200001 on a valid session | `IMNotInitialized` | run IM init/session-bind; else re-auth via QR |

## Security

No raw password is ever persisted; ideally it is never received (QR flow, or the
browser flow types it into TikTok's own page). The session blob plus device
fingerprint is stored **envelope-encrypted at rest**: a per-user random data key
encrypts the JSON with AES-GCM, and a KMS master key wraps that data key
(`bridge/session_store.py`). Contact and message data is third-party PII: encrypted
at rest and deleted on logout (`SessionStore.delete`). Service secrets (master key,
proxy creds, signer key) come from a secrets manager or env at deploy time, never
the repo. Logs must redact secret values.

## Health metric

**Primary — Live-Session-Ratio:** fraction of logins that are authenticated AND
synced within the poll interval (`bridge/metrics.live_session_ratio`). Every failure
mode collapses into a drop here. Alert on a sudden drop (signer outage) and on a
rising `needs-reauth` share (`reauth_share`). **Secondary — message-delivery-lag
p95**, to catch silent polling lag while sessions stay alive.

## What is not built

- The signing algorithm itself. It is isolated behind `Signer`; `SubprocessSigner`
  shells to an external SignerPy process so a rotated algorithm is swapped without
  touching bridge logic.
- IM body payload parsing (`IM._parse_conv_list` / `_parse_messages` return `[]`).
  The conversation/message body field shapes are best-effort until captured from a
  live authenticated session (see `DESIGN.md` §0); the sync engine is fully tested
  against a fake fetcher in the meantime.
- The `im-ws` websocket realtime channel. v1 uses polling with reconciliation.
- The Go mautrix appservice. The bridgev2 interface mapping is documented in
  `DESIGN.md` §7 instead of implemented, to keep the prototype within the time box.

## Layout

```
bridge/proto.py          protobuf varint codec (hand-rolled)
bridge/envelope.py       IM Response envelope parse + status classification
bridge/errors.py         error taxonomy
bridge/device.py         stable per-user device fingerprint
bridge/signing.py        swappable Signer (Null + Subprocess SignerPy seam)
bridge/client.py         signed HTTP transport, device params, error mapping
bridge/session_store.py  AES-GCM envelope-encrypted session store
bridge/state.py          cursors + message dedup
bridge/normalize.py      TikTok objects -> canonical User/Thread/Event
bridge/sync.py           backfill + poll with reconciliation
bridge/im.py             IM endpoints (send envelope confirmed)
bridge/auth/login.py     QR + browser login state machine
bridge/metrics.py        Live-Session-Ratio
bridge/cmd/login.py      CLI to exercise login flows
```

## Running the prototype on a server

The prototype runs the full path — config, device, real signing, live request,
health classification — end to end. It has been verified live: signed requests
reach TikTok's business logic. The remaining blocker for a completed login is a
clean per-user IP.

```bash
# one-time setup (no pip on the host): a venv with the signer
python3 -m venv .venv
.venv/bin/python <(curl -s https://bootstrap.pypa.io/get-pip.py)
.venv/bin/python -m pip install requests cryptography pyyaml
.venv/bin/python -m pip install "git+https://github.com/is-L7N/SignerPy"

# config.yaml points signer_cmd at the shim (see config.example.yaml)
python3 -m bridge.cmd.run probe      # prints a health state: connected /
                                     # im-not-initialized / rate_limited / needs-reauth
python3 -m bridge.cmd.run run        # poll loop (needs a logged-in session)
```

Docker: `docker build -t tiktok-bridge .` then
`docker run --rm -e BRIDGE_MASTER_KEY=$KEY -e BRIDGE_PROXY=$PROXY tiktok-bridge probe`.
The signer is not baked into the image by default (untrusted third-party code);
install it explicitly per the Dockerfile comments after reviewing it.

## The IP problem, and how the prototype handles it

TikTok rate-limits and geo-gates by IP. From a datacenter IP the probe returns
`rate_limited` (passport `error_code 7`) or `needs-reauth` on the IM path, even
with correct signing — verified live from this server. The fix is not to rotate
IPs to dodge the limit (that is detection evasion and out of scope); it is the
design's per-user model: each consented user's session runs through one stable,
geo-matched residential proxy the operator supplies (`BRIDGE_PROXY`, or a
`proxies` pool with stable per-login assignment in `bridge/proxy.py`). Rate
limits are then a handled failure mode: `BridgeApp` backs off rather than
spinning, and the `rate_limited` health state tells the operator a proxy is
degraded. The single health metric, Live-Session-Ratio, drops when this happens.
