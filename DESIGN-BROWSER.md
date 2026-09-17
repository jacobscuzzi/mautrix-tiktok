# Browser-based bridge: the server-runnable path

An addendum to `DESIGN.md`, written after testing what actually runs on a plain
datacenter server. The original design targeted TikTok's mobile app API (signed
protobuf). That path works but is gated by three things a rented server does not
have: a clean residential IP, a registered device, and a maintained signer. This
addendum documents the path that does run on the server, verified live tonight.

## What the live tests established (2026-09-17)

- **[Obs] The mobile API is IP-gated from a datacenter.** With correct SignerPy
  signatures accepted by TikTok, `send_email_code`, `available_ways`, and
  `account_lookup` all return `error_code 7` ("max attempts") from this IP, on the
  first attempt. Signing is fine; the IP reputation is the wall.
- **[Obs] Device registration is unavailable.** It needs TTEncrypt, which is not in
  any public library or SignerPy, so `device_register` returns `device_id: 0`.
- **[Obs] A real browser is not IP-gated.** Headless Chromium loads tiktok.com from
  the same IP (HTTP 200, cookies set, not blocked) and generates a login QR. A
  browser carries Chrome's TLS fingerprint and runs TikTok's own JS signing
  (msToken, X-Bogus, X-Gnarly), so TikTok treats it as an ordinary visitor.
- **[Inf] Therefore the browser is the server-runnable substrate.** It sidesteps
  the IP, device-registration, and signer problems at once, because TikTok's own
  page does all three.

## Architecture

A headless browser per logged-in user, driven by the bridge.

```
User's phone (TikTok app)
        │ scans QR, approves
        ▼
[Headless Chromium context per user]
  ├─ web_qr_login: tiktok.com/login QR → capture session (storage_state)
  ├─ session store: storage_state encrypted at rest (reuse SessionStore, AES-GCM)
  └─ web_dm_capture / sync: open tiktok.com/messages
         ├─ intercept the web IM traffic (XHR + websocket frames)  → inbound DMs
         └─ drive the composer / replay signed XHR                  → outbound DMs
        │ normalized events
        ▼
[normalize] → [pipeline / Matrix portal]  (unchanged from DESIGN.md)
```

The mobile-API client from `DESIGN.md` stays in the tree as the second backend:
if the operator supplies per-user residential proxies and a maintained signer, the
signed-HTTP path is lighter (no browser per user). The two are behind the same
`Signer`/session abstractions, so the bridge can prefer HTTP and fall back to the
browser, or vice-versa.

## Login (verified server-runnable)

`bridge/auth/web_qr_login.py`: launch headless Chromium, open the login page, click
"Use QR code", read the `get_qrcode` response (token + base64 PNG + expiry), present
the QR, and poll until a `sessionid` cookie appears, then persist `storage_state`.
Passwordless, no captcha (the phone approves), no credential stored. Proven live:
the QR generates and is scannable from this server. The only human step is the scan.

Chromium runs without root: its shared libraries were fetched with `apt-get
download`, extracted locally, and exposed via `LD_LIBRARY_PATH` (see
`.chromium-libs/`). Reproducible in a container via the `Dockerfile`.

## Reading and sending DMs

The web DM protocol is not publicly documented (public reverse-engineering covers
TikTok LIVE, not private messages), so the design discovers it at runtime rather
than guessing. `bridge/auth/web_dm_capture.py` opens `tiktok.com/messages` in the
logged-in context and records every IM request, response shape, and websocket
frame. From that capture the bridge takes one of two inbound strategies:

- **Intercept** the web IM websocket/XHR frames the page already receives (the
  browser maintains the connection, heartbeats, and decryption for us), and emit
  normalized events. Lowest-fragility because the page does the hard part.
- **Replay** the discovered XHR endpoints with the browser-generated signing
  (msToken/X-Bogus) via `page.evaluate` or a `curl_cffi` client seeded with the
  session, for a lighter footprint once the shapes are known.

Outbound: type into the composer and send, or replay the discovered send XHR. Sends
are never blind-replayed on an ambiguous result (same rule as `DESIGN.md`).

## Failure modes (browser path)

| Condition | Detection | Recovery |
|---|---|---|
| QR expires before scan | poll timeout, `phase: timeout` | regenerate QR (page auto-rotates; daemon re-reads) |
| Session expires / logged out | `sessionid` cookie gone, redirect to /login | mark `needs-reauth`, re-run QR login |
| Browser crash / OOM | context closed event | supervisor restarts the context from stored `storage_state` |
| TikTok web UI change | selector/endpoint miss in capture | capture re-maps; selectors kept in one module |
| Headless detected / challenge | challenge element on page | surface `needs-reauth`; consider a stealth profile |
| Rate / anomaly on the account | web error banner / empty inbox | back off; one browser per user keeps it human-paced |

## Security

No password is ever handled (QR approval on the phone). The session lives in
`storage_state` (cookies + origin storage) and is encrypted at rest with the
existing envelope scheme (`bridge/session_store.py`), deleted on logout. The
browser profile is per-user and isolated. `browser-data/` (live session material)
is gitignored. Secrets (KMS key, any proxy) come from the environment.

## Health metric

Unchanged in spirit: **Live-Session-Ratio** = logins whose browser context is
authenticated and has read the inbox within the poll window. A browser that drops
to `needs-reauth` or fails to load `/messages` leaves the live set immediately.

## Honest status

- **Proven on this server:** headless browser loads TikTok, generates a scannable
  login QR, from a datacenter IP the raw API can't use.
- **Pending one human step:** the QR scan, to capture a live session.
- **Automatic once scanned:** `web_dm_capture` confirms whether the web session
  reads DMs and maps the exact endpoints — the last open question in the design.
