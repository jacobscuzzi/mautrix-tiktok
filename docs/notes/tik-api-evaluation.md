# TikAPI as a DM backend — evaluation and decision

Exercise 2, Knows case study. Question posed: could tikapi.io be the backend for
the TikTok DM bridge, and is it the best solution? Verdict up front, then the
evidence, the failure-mode remap, the cost ceiling, and what was built to prove it.

Epistemic markers as in DESIGN.md: **[Obs]** verified live on 2026-09-17,
**[Inf]** from TikAPI's own OpenAPI docs / SDK, **[Guess]** unverified (needs a
paid account to close).

---

## Verdict

**Do not make TikAPI the sole backend. Keep it as an optional, swappable provider
behind the `MessageProvider` seam, and use it for two concrete jobs: (1) a paid
one-month bootstrap to capture real DM JSON and unblock the body-shape gap
DESIGN.md leaves open, and (2) a fallback for accounts where self-signing is too
risky.** For a production realtime DM bridge, the native path wins because TikAPI
has **no inbound push** (polling only), **meters on requests + bandwidth**, and
**explicitly disclaims TikTok-ToS compliance**. All three cut against exactly what
a DM bridge needs. The build I did makes either choice a one-line swap, so the
decision is reversible and not load-bearing.

---

## What TikAPI is, and does it even do DMs

**[Obs]** `https://api.tikapi.io` is live. Unauthenticated probes return a clean,
structured contract:

```
GET  /user/conversations  -> 400 {"fields":{"X-ACCOUNT-KEY":"A valid account key is required."}, ...}
GET  /user/message/send   -> 405 (method not allowed; it is POST-only)
POST /user/message/send   -> 400 {"fields":{"conversation_id":"A valid conversation ID is required."}, ...}
GET  /customer/plans      -> 400 {"fields":{"Authorization":"Your authentication token is missing."}, ...}
```

So the DM endpoints exist, the two-key auth model is real, and pricing is behind a
dashboard login (hence unconfirmable here). **[Inf]** From the OpenAPI docs, the DM
surface is first-class:

| Need | TikAPI |
|---|---|
| List inbox | `GET /user/conversations` |
| Message history | `GET /user/messages?conversation_id=..&conversation_short_id=..` |
| Send text | `POST /user/message/send` `{text, conversation_id, conversation_short_id, ticket}` |
| Message requests (strangers) | `GET /user/messages/requests`, accept/delete conversation requests |
| Notifications | `GET /user/notifications` |
| Realtime push | **none** — no webhook, no WebSocket |

Auth is `X-API-KEY` (developer key) + `X-ACCOUNT-KEY` (per-user OAuth token,
obtained through TikAPI's hosted `/account/authorize` page; the bridge never sees
TikTok credentials). Session expiry surfaces as **HTTP 428**; TikTok upstream
errors as **HTTP 503**.

## Does it solve the case study's actual problem?

DESIGN.md is organized around one crux: **signing is the single most fragile
dependency**, and the open question is whether a session can be made mobile-bound.
TikAPI's central value is that it **makes both disappear**:

- No signer to reverse, version-pin, or hot-swap. When TikTok rotates the
  algorithm, that is TikAPI's outage to fix, not ours.
- No device fingerprint, no residential-proxy fleet, no geo-matching. TikAPI
  routes server-side (the OAuth `country` param is the only region knob). **[Inf]**
- No "does the web session carry to the mobile IM host" question — TikAPI holds
  the session server-side and hands us an opaque Account Key.

That is a real and large reduction in the thing the case study says is hardest.
The cost is that it moves the risk, it does not remove it — and the new risks land
in worse places for a DM bridge.

---

## Failure-mode remap (the case study's own lens)

DESIGN.md §4 detects failures at the right blast radius. Here is how each mode
changes under TikAPI. Bold = strictly worse than the native path.

| Mode | Native (self-signed) | Under TikAPI |
|---|---|---|
| Signing rotates | 4xx spike across all users; hot-swap signer | **Gone from us** — becomes TikAPI's outage; we see 503s we cannot fix, no SLA published [Guess] |
| Session expired | body auth code → needs-reauth | HTTP 428 → `AuthError` → needs-reauth (clean, maps cleanly) |
| Rate limit | HTTP 429 → backoff, rotate proxy | HTTP 429 → backoff; **can't rotate proxy (vendor owns it); quota is a hard 24h sliding budget** [Inf] |
| New inbound message | poll or (web) frontier WebSocket | **poll only — no push at all; latency floored at the poll interval, quota burned every tick** [Inf] |
| Outbound send ambiguous | reconcile via `client_message_id` idempotency key | **TikAPI accepts no client message id; reconcile only by history match — the idempotency guarantee is weaker** [Inf] |
| Mark read | `/v3/conversation/mark_read/` | **no documented endpoint — unsupported** [Inf] |
| Ban / challenge | body code → needs-reauth | inherited from TikTok via TikAPI; **plus TikAPI forbids "spam/unsolicited" DMs in its terms** [Inf] |
| Cost of running | proxies + infra | **metered per request AND per GB; a polling bridge's bill scales with users × poll frequency** [Inf] |

Two of these are structural, not incidental:

1. **No realtime.** A DM bridge's whole point is that a message arriving on TikTok
   shows up in the bridged room promptly. TikAPI can only be polled. Every poll of
   every active conversation costs quota and bandwidth, and the best achievable
   `message-delivery-lag p95` (DESIGN.md's secondary health metric) is the poll
   interval. The native web path we observed on 2026-09-17 (`wss://im-ws.tiktok.com/ws/v2`,
   the frontier channel — see `docs/observations/tiktok-web-2026-09-17.md`) is a
   real push socket. On realtime, native strictly wins.
2. **Metered cost meets polling.** **[Guess]** Pricing is auth-gated; third-party
   sources (unverified) suggest a Business tier around 10k requests/day. A bridge
   polling conversations + per-conversation history every 30 s is ~2 requests ×
   2,880 polls/day ≈ 5–6k requests **per user per day** before backfill or
   fan-out. That implies roughly a single-digit number of users per top-tier
   account. Even if the numbers are off by 2–3×, metered polling caps how far this
   scales and makes cost grow with liveness — the opposite of what you want.

---

## Where TikAPI is genuinely the better tool

- **Bootstrapping the unknown body shapes.** DESIGN.md and the README both flag
  that `_parse_conv_list` / `_parse_messages` return `[]` because the live
  authenticated JSON was never captured. One month of TikAPI's cheapest tier plus
  one authorized test account yields real `/user/conversations` and `/user/messages`
  responses immediately, with no signing work. Feed those shapes into **both**
  providers' normalization. This is the fastest way to close the biggest open gap,
  and it is cheap and time-boxed.
- **A fallback provider** for an account that keeps getting challenged on the
  self-signed path — route just that login through TikAPI while the native path
  stays default for everyone else. The `MessageProvider` seam makes this per-login.
- **A hedge during a signer outage.** If the native signer breaks across all users
  (the canary fires), flipping a cohort to TikAPI keeps DMs flowing while the
  signer is rebuilt — provided the ToS posture is acceptable to the business.

## Where it is the wrong tool

- As the **sole, always-on backend** for a realtime multi-user DM bridge: polling
  latency + metered cost + no push + no mark-read + weaker send idempotency.
- Anywhere the **ToS exposure** is unacceptable: TikAPI's own terms state that use
  "may break some of [TikTok's] terms" and place that risk on the customer, and
  forbid unsolicited/spam messaging. A bridge that sends DMs on users' behalf must
  get an explicit business/legal decision here, independent of the tech.

---

## What was built (so the decision is proven, not asserted)

A `MessageProvider` seam and a real, tested TikAPI adapter, so "native vs TikAPI"
is a one-line swap under an unchanged `Syncer`/`SyncState`/`normalize`/`metrics`.

- `bridge/provider.py` — the `MessageProvider` Protocol (the build-vs-buy
  boundary; maps to bridgev2 `NetworkAPI`). The native `IM` satisfies it as-is.
- `bridge/providers/tikapi.py` — `TikApiProvider`: two-key auth, HTTP→taxonomy
  error mapping (428→`AuthError`, 503→`Transient`, 429→`RateLimited`), cursor
  pagination, conversation ticket/short-id caching (required to send), a local
  double-send guard with an explicit note that durable idempotency still needs
  history reconciliation, and honest `mark_read`-unsupported. Parsing is isolated;
  `[Inf]` field names are the one thing needing a live key to confirm.
- `bridge/auth/tikapi_oauth.py` — hosted-OAuth URL builder + redirect parser +
  scope-gap check (`view_messages`, `send_messages`, `conversation_requests`,
  `view_notifications`).
- `tests/test_provider.py`, `tests/test_tikapi.py` — 29 tests: auth headers, every
  error mapping, parsing (incl. TikTok's JSON-string `content`), pagination, ticket
  resolution, send-payload construction, idempotency refusal, and a test that
  drives the **TikAPI provider through the real `Syncer` with dedup working** —
  the interchangeability proof. Full suite: **67 passing** (was 38).

### Confirmed against the live contract
The adapter's auth headers, POST-only send, required `conversation_id`, and the
`{"status":"error","message":...}` envelope all match unauthenticated live probes.

---

## Recommendation and next step

Keep the seam; default **native**; wire TikAPI as an opt-in provider per login.
Concretely, next:

1. Buy one month of the lowest TikAPI tier, authorize one throwaway account with
   the four DM scopes, and capture real `/user/conversations` + `/user/messages`
   JSON. Confirm the `[Inf]` field names in `tikapi.py::_parse_*` and port the same
   shapes into the native `IM._parse_*`. (Closes the case study's biggest gap.)
2. Read pricing/quota in the dashboard and compute the real users-per-account
   ceiling; that number decides whether TikAPI is ever viable as more than a
   bootstrap/fallback.
3. Get an explicit business decision on the ToS exposure before any send path goes
   live through TikAPI.

Bottom line: TikAPI is an excellent accelerator and a reasonable fallback, but the
native frontier-WebSocket path is the better long-term realtime backend. Building
the provider seam means we do not have to bet the architecture on that call.
