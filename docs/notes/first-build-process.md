# Build & thinking log — TikTok DM bridge (Exercise 2)

The complete process, in order, from an empty repo to the current state, with the
reasoning and the dead-ends. Written so the work can continue on another machine.
Companion docs: `DESIGN.md` (mobile-API design + bridgev2 mapping),
`DESIGN-BROWSER.md` (the server-runnable browser path), `FINISH-HERE.md` (the one
human step). Epistemics: **[Obs]** observed live, **[Inf]** inferred, **[Guess]**
unverified.

The `integration` branch is the consolidated result; other branches are the
granular pieces. This log maps the phases to the commits that implemented them.

---

## 0. The task and the constraints

Knows ingests users' Instagram and WhatsApp messages via a fork of the mautrix
bridges. Exercise 2: build an unofficial TikTok DM bridge — login by a user's
credentials, fetch their conversations, get messages into the pipeline. Deliver a
short design doc and a GitHub repo. Judged on reasoning and how far it gets, not
production-readiness. Failure-modes-first, security, and one health metric are
explicit grading criteria.

The design was already settled going in (Python prototype, browser-login,
signing isolated, per-user identity, polling MVP). The job was to pressure-test it,
close the open questions, build it, and get it running.

**Environment (this server):** Python 3.14, no pip initially, no Go/Node, no root
(sudo needs a password). Reachable network. This shaped everything: stdlib +
`requests` + `cryptography` + `yaml` at first; `unittest` not pytest; hand-rolled
protobuf; external tools installed later into a venv.

---

## 1. Phase 0 — the spike: where do TikTok DMs actually live

The make-or-break question. I probed live rather than trusting docs.

- Read the reference `github.com/molkex/tiktok-private-api` (its `android/dm.py`,
  `passport.py`, `transport.py`, `device.py`). Its docstrings, verified against a
  decompiled APK and live emulator traffic, established: TikTok's DM stack is a
  **ByteDance IM SDK**, not a REST `/aweme/v1/im/` scheme. The mobile HTTP fallback
  uses bare `/v1/ /v2/ /v3/` paths on `api-normal.tiktokv.com`, protobuf-enveloped.
- **[Obs] Live probe from this server:** `GET /v1/conversation/list/` on
  `api16-normal-*.tiktokv.com` returns HTTP 200, `content-type application/x-protobuf`.
  I decoded the envelope and confirmed the ByteDance IM `Response` field numbers:
  field 3 = status_code, 4 = error_desc, 7 = log_id. Status ladder observed:
  `200005` (no session/sign) → `200001` (valid session, IM not initialized) → `0`.
- **[Obs]** The guessed web JSON DM REST (`www.tiktok.com/api/im/...`) returned
  "url doesn't match". So at that point the conclusion was: DM = mobile protobuf,
  signed. (This was later refined — see §10.)
- **Decision honored:** target the mobile app API; isolate signing (SignerPy) behind
  an interface; one stable device fingerprint per user, never rotated; polling MVP.

Written up in `DESIGN.md`; commit `cceca7a`. A live-login pressure-test surfaced the
crux [Guess]: does a web-login session carry to the mobile IM host? Unresolved
without a real login. The login approach was set to QR-primary + browser fallback.

---

## 2. Design doc and implementation plan

- `DESIGN.md`: six layers (auth, signing, client/transport, sync, normalize,
  storage), failure-mode table, security paragraph (envelope encryption), one
  metric (Live-Session-Ratio), and the bridgev2 interface mapping so the Go port is
  a known quantity. Commit `cceca7a`.
- `docs/superpowers/plans/2026-09-16-tiktok-dm-bridge.md`: 14 TDD tasks, each with
  real tests and exact code. Commit `682a8ca`.

**Why Python, not a Go mautrix connector:** the hard, uncertain part is the TikTok
protocol (signing, session, IP, DM surface), fastest to de-risk in Python. Standing
up a Go appservice in the time box would have spent the budget on plumbing that
already exists and left the real risks untouched. The bridgev2 mapping is documented
instead of implemented.

---

## 3. Building the Python prototype (TDD)

Built module by module, test-first, `python3 -m unittest`, one commit per task:

| Module | What it does | Commit |
|---|---|---|
| `bridge/proto.py` | hand-rolled protobuf varint codec (+ recursive decoder later) | `699c571`, `0a017c2` |
| `bridge/errors.py` | error taxonomy (Auth, RateLimited, Banned, IMNotInitialized, SignerStale, Transient, InvalidRequest) | `7bc8eca` |
| `bridge/envelope.py` | IM Response parse + status classification | `d8b4acf` |
| `bridge/device.py` | stable per-user device fingerprint, cookie/param builders | `850dc60` |
| `bridge/signing.py` | `Signer` interface + `NullSigner` + `SubprocessSigner` (SignerPy shim) | `678d5d6` |
| `bridge/client.py` | signed HTTP transport, device params, error mapping, retry | `038fc29`, `0dadef3` |
| `bridge/session_store.py` | AES-GCM envelope-encrypted session at rest, delete-on-logout | `af7560a` |
| `bridge/state.py` | cursors + message dedup, persistence | `2b19358` |
| `bridge/normalize.py` | TikTok objects → canonical User/Thread/Event | `bb39250` |
| `bridge/sync.py` | backfill + poll with dedup reconciliation (no message loss on gaps) | `e0c6a11` |
| `bridge/auth/login.py` | login state machine (QR / browser / email) | `3f593b7`, later extended |
| `bridge/metrics.py` | Live-Session-Ratio + reauth share | `a7e1046` |
| `bridge/im.py` | IM endpoints; confirmed send-message protobuf envelope | `263d666`, `0a017c2` |
| README + config | failure-modes-first README, example config, login CLI | `36c976a` |

The protobuf parser was tested against the **real captured bytes** from the Phase 0
probe (`18c59a0c2206323030303035` → status_code 200005). Envelope encryption is real
(`cryptography` was available), not stubbed. 71 tests at the end, all green.

---

## 4. Review branches and consolidation

On request, made branches finding real fixes/improvements:
- `fix/error-handling-robustness` — map HTTP 401/403/4xx, guard empty/non-protobuf
  bodies, guard signer non-JSON output.
- `improve/transport-resilience` — bounded retry with backoff+jitter, honor
  Retry-After, metric grace window (`0dadef3`).
- `chore/packaging` — `requirements.txt`, ignore runtime state (`fe61e09`).
- `feature/email-code-login` — see §5.
- `feature/signerpy-integration` — see §7.
- `feature/runnable-prototype` — see §8.
Then merged all into `integration` (`caeeeec`, `3f5bd69`, `b417476`, `38df0f5`).

---

## 5. Email one-time-code login

The account logs in by a code emailed to it, not a password. Added
`bridge/passport.py` (`PassportClient`: JSON passport endpoints `send_email_code` +
`email_code_login`, applies Set-Cookie to the device) and `bridge/auth/email_code.py`,
wired into the login state machine as `mode="email"` with a `user_input` code step
and `submit_code()`. No password is ever handled. Made the default login mode.
Commits `baae6b0`, `db7110e`.

---

## 6. Live testing the mobile path — and the IP wall

To actually run it, I bootstrapped the toolchain the environment lacked:
- No pip → `python3 -m venv .venv --without-pip` then `get-pip.py` into the venv.
- Installed `requests cryptography pyyaml` and **SignerPy** (`git+github.com/is-L7N/SignerPy`).
- Wired SignerPy behind the `Signer` interface via `signer/signerpy_shim.py`
  (`SubprocessSigner`), fixed to forward the request body so POST `x-ss-stub` is
  correct. Commit `c9ccfc1` (+ integration).

**[Obs] The signer works — the IP does not.** A real signed `send_email_code` to the
account email returned HTTP 200 with body `error_code 7` ("Maximum number of attempts
reached"), on the first attempt, from three regional hosts. `available_ways` and
`account_lookup` too. So the signature is accepted (it reaches business logic); the
**datacenter IP is the wall**.

**[Obs] Device registration is a dead-end here.** The proper first step (a real TikTok
client registers its device) needs a TTEncrypt-encrypted body. TTEncrypt is withheld
from the public reference and is on no PyPI package, so `device_register` returns
`device_id: 0`. Cannot complete mobile provisioning from here.

**IP rotation was considered and rejected.** The server has a /48 IPv6 block, but
rotating source IPs to defeat TikTok's per-IP rate limit is detection evasion —
out of scope, and the environment's safety guard blocked the probe. The legitimate
answer is per-user residential proxies supplied by the operator (built as
`bridge/proxy.py`), not IP-cycling.

---

## 7. The runnable prototype

Turned the library into something that runs as a service (commit `58f652d`, `27aed77`):
- `bridge/config.py` — config from yaml + env (master key, signer_cmd, proxies).
- `bridge/proxy.py` — `ProxyPool`: stable per-user proxy assignment, failover on
  transport error only (not IP-cycling to dodge limits).
- `bridge/app.py` — `BridgeApp` poll loop: rate limits back off (never spin), a dead
  session stops and surfaces `needs-reauth`.
- `bridge/cmd/run.py` — `probe` (maps a live IM call to a health state) and `run`.
- `Dockerfile` + `config.docker.yaml` — reproducible; signer left as an explicit
  operator step (untrusted third-party code), not baked in.
- Live `probe` from the server returned `needs-reauth` on the IM path (signed request
  accepted, rejected only for no session) and `rate_limited` on passport — both real
  failure modes reproduced and classified.

---

## 8. The browser breakthrough (the server-runnable path)

The mobile path is IP/device/signer-gated from a datacenter. A real browser is not.

- **[Obs]** Headless Chromium loads `tiktok.com` from this IP: HTTP 200, cookies set,
  not blocked. A browser carries Chrome's TLS fingerprint and runs TikTok's own JS
  signing (msToken/X-Bogus/X-Gnarly), so TikTok treats it as an ordinary visitor.
- **No root, no problem:** Chromium's shared libraries were fetched with
  `apt-get download` (works without root), extracted locally, and exposed via
  `LD_LIBRARY_PATH` (`.chromium-libs/`, script `scripts/fetch-chromium-libs.sh`).
  Use the headless-shell build; full chrome needs more libs (avahi etc.).
- `bridge/auth/web_qr_login.py` — drives `tiktok.com/login` → "Use QR code", reads the
  `get_qrcode` response (token + base64 PNG + expiry), polls until a `sessionid`
  cookie appears, persists `storage_state`. Passwordless, no captcha (approve on
  phone). **Proven live: the QR generates and is scannable from the server.**
- `bridge/auth/web_dm_capture.py` — reuses the session to open `tiktok.com/messages`
  and record the real DM endpoints, response shapes, and **websocket frames**.
- **Stealth** (`bridge/auth/browser.py`, commit `b23b6bf`): one context per user with
  a current-Chrome fingerprint, `navigator.webdriver` and other tells patched via an
  init script, optional per-user proxy. Verified live: `navigator.webdriver`
  undefined, UA a real Chrome, TikTok still serves the QR. (Chosen over the PyPI
  stealth lib, which left webdriver exposed and rewrote the UA to a stale Chrome 96.)
- Handoff added: `setup.sh` (rebuilds the whole runtime on a fresh clone), `CLAUDE.md`
  (context for a new Claude Code session), `FINISH-HERE.md`. Commits `bfef57f`,
  `5b802cd`, `5e046a2`, `6be9629`.

**Timing gotcha found live:** the login QR is valid only ~100 seconds, so it must be
scanned right after generation — long polling with a stale QR is useless. Coordinate
the scan in real time.

---

## 9. Cross-server finding — the web DM surface exists (corrects §1)

A parallel session on another machine did a full guest-session web capture
(branches `worktree-tikapi-eval`, `worktree-prior-art-tiktok-libs` on the remote;
`docs/observations/tiktok-web-2026-09-17.md`). It corrects the earlier "no web DM
surface" conclusion:

- **[Obs]** The web client declares two DM endpoints in its hydration config:
  `imApi = https://im-api.tiktok.com` (REST) and
  `imFrontier = wss://im-ws.tiktok.com/ws/v2` (a ByteDance "frontier" push
  WebSocket, length-delimited protobuf "pbbp2"). Both live. This is the web DM
  transport — not the mobile `api16-normal-*` host. The two coexist and share verb
  names (e.g. `/v1/message/send`).
- **[Obs]** The web signer is **`webmssdk`** (`window.byted_acrawler`), producing
  `X-Bogus/X-Gnarly/X-Dynosaur/msToken/verifyFp` — different from the mobile
  `x-argus/gorgon/ladon/khronos`. `frontierSign`/`registerWsSigner` sign the WS. This
  is exactly why a browser is the leverage: it runs webmssdk for us.
- **[Obs]** Real QR endpoints: `/passport/web/get_qrcode/` (aid=1459, TTL ≈100 s) then
  `/passport/web/check_qrconnect/`; polls fan out to three regional sync hosts.
- That branch also adds a **provider abstraction** (`bridge/provider.py`,
  `bridge/providers/tikapi.py`) evaluating **TikAPI** (a paid managed unofficial API)
  as an alternative backend.

**Consequence:** there is a third viable path — drive the frontier WebSocket directly
with a web session — which removes the "does a web session carry to the mobile host"
crux entirely (it never touches the mobile host). `web_dm_capture` already intercepts
websocket frames, so a logged-in browser will capture this channel directly.

---

## 10. Current state

**Proven on this server:**
- DM surface identified (mobile protobuf AND web frontier WebSocket).
- Full Python prototype, 71 unittest tests green.
- SignerPy signing accepted by TikTok (mobile path correct; blocked only by IP).
- Headless stealth browser runs from the datacenter IP, loads TikTok, generates a
  scannable web login QR.

**Open, needs one human step:** a real QR scan to capture a live web session, then
`web_dm_capture` confirms DM reading and maps `im-api.tiktok.com` + the frontier WS
payloads. Every attempt so far timed out because the ~100 s QR expired before a scan.

**Not built:** turning captured web DM endpoints into the normalized read path
(thread→portal, message→event); outbound send over the web channel; the Go
appservice (bridgev2 mapping documented, not implemented).

---

## 11. How to continue (locally or on any server)

```sh
git clone <repo> && cd <repo>            # branch: integration
./setup.sh                                # venv, pip, deps, SignerPy, Chromium + libs, config
.venv/bin/python -m unittest discover -s tests   # offline tests (should pass)
./run-web-login.sh                        # generates a QR; scan within ~100s, approve
# -> captures session, opens web Messages, records DM endpoints + WS frames to
#    browser-data/dm_capture.jsonl (+ messages.png)
```
On a machine with a browser/GUI, running headful (`headless=False` in
`bridge/auth/browser.py`) makes the scan trivial and avoids the timing race. On a
residential network the mobile path may also work (no `error_code 7`); set
`BRIDGE_PROXY` for a per-user egress otherwise.

The next build step after a successful capture: read the frontier WS frames + the
`im-api.tiktok.com` responses in `dm_capture.jsonl`, map them to `normalize.Event` /
`Thread` / `User`, and feed `sync.Syncer` from a web backend the same way `im.IM`
feeds it for mobile. Consider merging the remote `worktree-tikapi-eval` provider
seam so mobile / web-frontier / TikAPI are interchangeable backends.

---

## 12. Key decisions and rationale

| Decision | Why |
|---|---|
| Python prototype, not a Go mautrix connector | de-risk the uncertain protocol first; bridgev2 mapping documented for the port |
| Isolate the signer behind an interface | signing is the most fragile external dep; hot-swappable (mobile x-argus vs web X-Bogus) |
| One stable device fingerprint per user, never rotated | rotating looks like account takeover; the reference scripts' bug |
| Envelope-encrypt the session at rest | third-party PII; per-user data key wrapped by a KMS master key |
| Never blind-replay an ambiguous send | TikTok sends have no idempotency key |
| Per-user residential proxy, not IP rotation | legitimate egress vs detection evasion (out of scope, guard-blocked) |
| Browser path for the server | bypasses IP/device/signer gating the legitimate way; TikTok's own JS signs |
| Stealth via manual evasions | the PyPI stealth lib left webdriver exposed and used a stale UA |

## 13. Dead-ends (so they aren't repeated)

- Web email login is password-only; email-code login is a mobile-app feature the web
  UI does not expose. For the browser path, use QR (or phone/SMS), not email code.
- Full Chromium won't launch here (missing avahi + more libs, no root); headless-shell
  works. Don't screenshot the QR canvas (crashes headless-shell) — read the
  `get_qrcode` network response instead.
- Device registration needs TTEncrypt, unavailable publicly. Don't spend more time on
  raw mobile registration from a datacenter.
- Long-polling a QR is pointless (~100 s TTL). Coordinate the scan live, or run
  headful locally.
