# TikTok DM bridge (prototype)

An unofficial TikTok DM bridge: a user logs in with their TikTok account and their
conversations — contacts, profiles, threads, messages — are fetched on our backend
and normalized into a mautrix-style pipeline (mautrix is the bridge framework a Go
port would target). Python prototype. This README leads with how it breaks; the
full design and the path to it: `DESIGN.md`.

## Test it in your browser (one command)

```bash
./bridge-app.sh                 # or: bash bridge-app.sh  /  sh bridge-app.sh
```

That is the whole setup: the script creates the venv, installs the Python deps and
a Chromium on the first run, starts the bridge plus the tester UI and opens
<http://127.0.0.1:8770> in your browser. Re-running it is fast. If the file is not
executable (fresh clone, zip download), `sh bridge-app.sh` or `bash bridge-app.sh`
does the same thing. Login opens a real Chromium window on TikTok's own login page
(QR code or phone / email / username, your choice there), so it needs a display (on
WSL that is WSLg `$DISPLAY`); the window closes itself once you are in. Use a
throwaway TikTok account.

The tester UI (`wrapper/`) is a small web page that talks to the bridge over HTTP:

1. **Connect** — a plain-language panel explains exactly how your data is handled,
   then one button opens TikTok's own login page in a real Chromium window, where
   you pick QR (scan it in the TikTok app) or phone / email / username. The window
   closes itself once you are in. Either way the bridge never sees your password.
2. **Chats** — once connected, your real contacts, threads and messages sync in and
   render; new DMs arrive live over the frontier socket.
3. **Health** — the one production number, Live-Session-Ratio, shown big, with the
   leading indicator (password-login share), delivery-lag p95, and error states.

**Everything is wiped on logout** (session blob, messages, contacts, browser
profile). The bridge program is `bridge/` (API in `bridge/webapp.py`, live browser
worker in `bridge/live.py`). The sealed session blob is wrapped with
`BRIDGE_MASTER_KEY` (32 bytes, base64) if set, otherwise with a key the bridge
creates on first start and keeps in `browser-data/_live/master.key` (owner-only).
The login itself survives a restart via the browser profile under `browser-data/`,
so treat that whole directory as sensitive.
Offline tests and the fixture-backed demo: [Run it](#run-it).

## The surface, in one paragraph

The mobile app API is IP-gated from a datacenter (`error_code 7`) and needs device
registration we cannot do (TTEncrypt, TikTok's proprietary device-registration
encryption) — correct, not runnable. A real Chromium is
**not** gated: it loads tiktok.com, and TikTok's own web signer (`webmssdk`) signs
every request the page makes. So the bridge drives a headless browser per user and
taps its network + the `wss://im-ws.tiktok.com/ws/v2` "frontier" socket (TikTok's
push WebSocket; length-delimited protobuf frames, "pbbp2"). **The page is
the signer** — confirmed live: an in-page `fetch` with the signing params stripped
came back signed and 200. Details and the mobile/TikAPI alternatives: `DESIGN.md` §1.

## Failure modes (first)

Detection is scoped to blast radius: **all users failing at once = our bug**
(signer/browser/key); **one user = that account**. No irreversible action on an
ambiguous signal (no blind send-replay, no auto-relogin loop). Every row is backed
by a test or a live observation — the full table with citations is `DESIGN.md` §5.

| Condition | Detection | State / action | Recovery |
|---|---|---|---|
| Password wrong | login-error element | `bad_credentials` | user retries; never auto-retried |
| Challenge (captcha / 2FA / identity check) | challenge element | `needs_user` + action | app opens URL → cookies flow |
| Session expired / logged-out elsewhere | `token/beat` code 8, `/login` 302 | `needs_user` | one re-login, never auto-loop |
| Rate limited | HTTP 429 / body code 7 | `rate_limited` | backoff + jitter, never spin |
| Locked / banned | `account-locked` / 403 | `blocked` | user action |
| Frontier socket drop | websocket close / stale poll | reconcile | the periodic REST poll re-pulls via Syncer dedup |
| DOM/schema change | missing selector / renamed field | `schema_change` | one module to fix |
| Signer stale for ALL users | cross-user 4xx spike (canary) | alert | hot-swap the browser image |
| Fingerprint mismatch on import | UA vs stored profile | `fingerprint_mismatch` | refuse; never rotate identity |

## Security

No raw password persisted, ideally never received (QR); the password flow types it
into TikTok's own page and the bridge API never carries it. The session blob +
fingerprint is **envelope-encrypted** (`bridge/session_store.py`): a per-login
AES-GCM data key, wrapped by a master key from env (`BRIDGE_MASTER_KEY`), a KMS,
or -- for a zero-config local run -- a key file next to the data (`master.key`, 0600),
with the login id bound as associated data. Honest limits of the demo: the browser
profile that keeps the login across restarts and the SQLite cache of contacts and
messages are plaintext on disk, protected by file permissions only, and both are
deleted on logout (`POST /api/logout` in the app, `DELETE /v1/logins/{id}` in the
v1 API). Secrets come from env/KMS, never the repo; a unit test asserts the login
ladder never logs the password. The raw capture and `storage_state.json` are
gitignored and deleted after import; a redactor produces the fixtures and a test
decodes every protobuf body in them to prove no token or real id survived. Full
paragraph: `DESIGN.md` §6.

## Health metric

**`bridge_live_session_ratio`** = connected / total (headline; every failure mode
drops it). Leading indicator **`bridge_password_login_share`** (rising = sessions
dying early, precedes bans). Plus `bridge_delivery_lag_seconds` p95 and
`bridge_error_total{state}`. Prometheus at `GET /metrics`.

## Run it

```bash
.venv/bin/python -m unittest discover -s tests      # 213 tests (skips: SignerPy shim; 9 browser tests without Chromium)
./scripts/demo.sh                                   # end-to-end API demo (fixture-backed)
```

The demo starts the v1 API, logs in, syncs contacts/threads/messages into SQLite,
prints `/metrics` and writes `demo-transcript.md` (gitignored) — fixture-backed, so
it reproduces with no TikTok login.

## How to reproduce the capture

```bash
./setup.sh                                          # venv, Playwright, Chromium (+libs on the server)
./run-web-capture.sh --mode manual --headful --user <name>
# log in yourself, clear any challenge, have a second account send a DM, press Enter
.venv/bin/python scripts/redact-capture.py 'browser-data/<name>/capture-*.jsonl'   # -> tests/fixtures/web/
.venv/bin/python scripts/decode-frontier.py 'browser-data/<name>/capture-*.jsonl'  # decode the frontier frames
```

To re-check a captured session live and read-only (this is what pulled 19 real
contacts into SQLite on 2026-09-18):
`.venv/bin/python scripts/live_readonly_sync.py --user <name> --watch 60`.
`scripts/export-session.py` pushes a captured session to a server running the v1
API (`bridge/api.py`) through its cookies flow.

## What is proven / not built

Verified live: mobile signing accepted (blocked by IP), browser QR from a
datacenter, logged-in DM capture, in-page re-signing, **session import + 19 real
contacts + a live DM exchange over the frontier socket** (2026-09-18). Fixtures:
contacts/profile/message parsers + Syncer dedup. Fake-platform only: the full
password ladder. Not built: the JSON inbox-mirror parser is synthetic-fixture only
(the app lists conversations from the page's own `get_by_user_init` protobuf); web
send/mark-read (a signed WebSocket frame, `DESIGN.md` §13); the Go appservice (the
Matrix-side bridge process; mapping in `DESIGN.md` §9). Proven vs. not built:
`DESIGN.md` §11; next steps: §12.

## Layout

One `MessageProvider` seam (`bridge/provider.py`) lets three backends be
interchangeable. **The web browser path ships**; the mobile signed-API client and
the TikAPI vendor adapter are the documented build-vs-buy alternatives (kept behind
the seam and unit-tested, see `DESIGN.md` §1). The pipeline below the seam — sync,
normalize, SQLite store, metrics — is shared by all three.

```
bridge/
  live.py            the running bridge: one browser worker (the demo runs a
                     single login; login, sync, realtime, history)
  webapp.py          bridge HTTP API (JSON + /metrics), backed by live.py
  api.py app.py      v1 provisioning API (login/start|step, DELETE /v1/logins/{id})
                     + the multi-login runtime; used by the fixture demo and tests
  pipeline.py        SQLite raw layer (logins/users/threads/events), idempotent
  sync.py state.py   backfill + poll; dedup by message id, opaque cursors
  normalize.py       TikTok objects -> canonical User/Thread/Event
  metrics.py         Live-Session-Ratio + leading indicators (Prometheus)
  session_store.py   AES-GCM envelope-encrypted session at rest
  proto.py           schemaless protobuf codec (no .proto files exist for these APIs)
  provider.py        the MessageProvider seam (web | mobile | tikapi)
  web/               session, page client (in-page signed fetch), frontier
                     (pbbp2) decode, WKWebView inject assets
  providers/         web.py (shipped), tikapi.py (vendor alternative)
  auth/              login state machine, web_password_login (ladder),
                     web_cookie_import, browser (one fixed profile per user)
  im.py client.py    the mobile signed-protobuf client (documented alternative)
  signing.py device.py passport.py envelope.py proxy.py config.py
  cmd/               serve (demo launcher), capture, demo; run + login (mobile path)
wrapper/             the tester UI: static/ (connect / chats / health) + a proxy
                     server; a client of the bridge API
scripts/             redact-capture, decode-frontier, export-session, live_readonly_sync, demo.sh
tests/               unittest + fixtures/web/ + fake_platform.py
```
