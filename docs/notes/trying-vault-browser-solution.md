# vaultbrowser — why it was built, how, and what went wrong

A build log, written for someone who has to explain this project in five minutes.
`DESIGN.md` says what the system *is*; this file says why it is that and not
something else, and what fought back on the way. Built in one session on 2026-09-17.

---

## The 60-second version

**The ask:** safely store a user's username and password for a web service, then use
a stealth browser so the service can read that user's messages and use the platform
normally.

**The problem underneath the ask:** those two halves pull in opposite directions.
Storing a password well means never using it. Using a platform "normally" means
almost never logging in, because repeated logins are the loudest bot signal a
platform has. So the design collapses into one rule: **the password is the recovery
path, the session is the working credential.** Everything else follows from it.

**What was built:** a Node service that holds credentials envelope-encrypted, drives
a patched Chromium with one fixed identity per user, keeps the resulting platform
session encrypted and reuses it for every action, and only reaches for the password
when the session is actually dead — once, never in a loop, and never at all after a
captcha or a wrong password.

**How it's proven:** a fake messaging platform ships in the repo. The end-to-end
tests drive a real browser through the real HTTP API against it, including the cases
that matter: session reuse across a service restart with no password, logged-out-
elsewhere, 2FA, and wrong password. 36 tests, all green.

**The honest gap:** the TikTok adapter's routes are observed, its DOM selectors are
guesses. It has never run against a real account.

---

## Why this exists at all

It did not start from zero. The sibling project `../mautrix-tiktok` is a TikTok DM
bridge whose design (from 2026-09-16) already chose **QR login as primary** and
**browser-and-capture as the fallback**, precisely because the QR flow never touches
a password. That fallback existed as a stub.

This request is that fallback taken seriously: the case where a platform gives you
no password-free path, so you have to hold the password and still behave. Three
things came across from the bridge rather than being re-invented:

1. **The device identity is never rotated.** The bridge's design calls out that
   rotating device fingerprints on a live session looks like account takeover. Same
   rule here, at the browser level.
2. **Never act irreversibly on an ambiguous signal.** The bridge refuses to
   blind-replay a send whose result is unknown. Here an ambiguous send returns
   `unconfirmed` and the caller reconciles on the next read.
3. **Detect at the right blast radius.** All users failing at once is our bug (key,
   adapter, browser). One user failing is that account.

The 2026-09-17 web observation in the bridge's `docs/observations/` also supplied the
real TikTok routes and the fact that its web signer stack (`webmssdk`, `secsdk`)
cross-checks browser properties — which decided the stealth approach below.

---

## The decisions, with what was rejected

**Password as last resort, not as the credential.** The alternative is to log in
fresh for each work session, which is simpler and would have been faster to build.
Rejected: it maximises exactly the event platforms score hardest, and it means
decrypting the password constantly instead of rarely. The ladder is: restore
session → is it alive? → if yes act; if no, one password login; if that hits a
challenge, stop and tell the user.

**Stop on challenge instead of solving it.** No captcha solver, no 2FA guessing.
Partly because retrying a challenge is what escalates an account to a lock, partly
because a service that can defeat the user's own second factor is a worse thing to
hold a password than the password itself. The status becomes `needs_user`.

**Patchright over playwright-extra plus the stealth plugin.** The stealth plugin
family works by injecting JavaScript that overwrites `navigator` properties. The
observation notes say TikTok's signer SDKs check those properties for internal
consistency, and inconsistency is itself a signal. Patchright patches the driver —
the CDP `Runtime.enable` leak, the console hook, `--enable-automation` — so the page
sees an ordinary browser rather than a decorated one. Verified in a test:
`navigator.webdriver` is false with no page-level patching.

**One fixed fingerprint per user, generated at enrolment, never rotated.** Locale
and timezone are always chosen as a matching pair, and a per-user proxy field exists
to be geo-matched to it. Randomising per session was rejected for the same reason
the bridge rejected it.

**Node, not Python.** The bridge is Python and it would have been natural to
continue. But this box has no `pip`, and the stealth tooling that matters is
Node-native. Python would have meant hand-rolling a CDP client. The adapter
interface is small enough that the language boundary costs nothing.

**The fake platform.** A hundred of the eight hundred lines of source are a small
local web app with a login form, a wrong-password path, a 2FA mode, a lock mode, an inbox and a send box.
Without it, every interesting failure path would be untestable and the whole service
would rest on assertions about a site nobody can reach from CI. With it, the 2FA and
logged-out-elsewhere paths are covered by tests that drive a real browser.

**One action per user at a time.** A real person has one device. Two overlapping
browser actions race the same session and look robotic. A per-user queue serialises
them; different users still run concurrently.

---

## What went wrong, in order

**1. No git repository.** The working directory was not a repo, so worktree
isolation was unavailable. Resolved by initialising `vaultbrowser` as its own repo,
which is the right shape anyway since it is a separate service from the bridge.

**2. Chromium would not start — the real time sink.** The bundled browser died with
`libnspr4.so: cannot open shared object file`. Four libraries were missing
(`libnspr4`, `libnss3`, `libnssutil3`, `libasound2`) and there is no passwordless
`sudo` on this machine, so the normal fix — installing system packages — was closed.
Resolved without root by downloading the `.deb` packages with `apt-get download` and
unpacking them with `dpkg -x` into a cache directory, then pointing the browser's
library path at it. `scripts/ensure-browser-deps.sh` reproduces this, and
`src/browser.js` picks the directory up automatically. This is also why the service
runs headless here: there is no Xvfb on the box either.

**3. Headless Chrome announces itself.** The first successful launch reported
`HeadlessChrome` in its user agent. That is a one-line tell for any detector. It
cannot be fixed on this machine, so it is recorded instead: production runs headed
under Xvfb via `HEADLESS=false`, and the README says so rather than pretending the
tests prove stealth against a real platform.

**4. A design claim that was not true.** The first draft of `DESIGN.md` promised the
password would be held in a buffer and zeroed after use. Writing the login path made
it obvious that this is theatre in Node: the page API takes a string, and strings
cannot be wiped. The document was corrected to say the protection is *scope* — the
password is decrypted inside the login step and dropped — rather than claim a wipe
that does not happen.

**5. The queue leaked a pending count and an unhandled rejection.** A failing task
left its counter incremented, and the rejection escaped through the chain used to
serialise the next task. Caught by the test that asserts a failure does not block the
following action. Rewritten so the counter is decremented in the task's own `finally`
and the stored tail is a separately-caught promise.

**6. Explicit login returned a stale status, twice.** After a user cleared a 2FA
challenge, `POST /login` still answered `needs_user`; after re-enrolling a corrected
password it still answered `enrolled`. The status was being read inside the action,
before the success was persisted. Both tests were written before the implementation
and both caught it immediately. Fixed by computing the status after the action
completes.

**7. Small friction, worth knowing.** A smoke script placed outside the project could
not resolve the browser package. The test runner rejects a bare directory path and
needs globs. Neither is interesting except as a reminder that the first run of
anything on a new box is where the time goes.

---

## What is proven and what is not

| Claim | Evidence |
|---|---|
| Credentials and sessions are unreadable at rest | tests assert no plaintext on disk, wrong key fails, tampering fails, files cannot be swapped between users or slots |
| Actions reuse the session and skip the password | end-to-end test restarts the service from the encrypted files and reads messages with zero password logins |
| A dead session recovers once, automatically | logged-out-elsewhere test: exactly one password login, then the action |
| A challenge stops the ladder | 2FA test: second request does not touch the login form |
| A wrong password is never retried | bad-credentials test: even an explicit login refuses until re-enrolment |
| The browser is not trivially detectable | `navigator.webdriver` false, timezone and locale match the stored fingerprint |
| Secrets never reach logs or responses | redaction unit test plus a live boot where the log was grepped for the password |
| **TikTok actually works** | **not proven.** Routes observed, selectors guessed, no account ever used |
| **Stealth survives a real detector** | **not proven.** Tested against a local app, and headless here, which production must not be |

---

## The assumptions I made instead of asking

This ran as a background job with no one to answer questions, so the ambiguous
points were decided rather than parked. If any of these is wrong, it is cheap to
change and the rest of the design survives:

- **A service, not a library.** An HTTP API with enrol, status and delete as
  first-class endpoints, because the user whose password is held should be able to
  see and revoke it.
- **The platform is TikTok-shaped**, matching the ex02 exercise, but the
  platform-specific code is confined to one adapter file behind a five-method
  interface. A different platform is a new adapter, not a new service.
- **The master key comes from an environment variable**, behind a one-method seam
  meant to be replaced by a KMS. Building a KMS integration with no cloud account
  would have been unverifiable.
- **Messages are read on demand**, not pushed. Realtime would mean TikTok's frontier
  WebSocket, which the bridge's observation documents but nobody has driven.

---

## What I would do next, in order

1. **Close the TikTok selector guesses** against one real logged-in session. Nothing
   else in the project is blocked on anything, and everything TikTok-specific is
   blocked on this.
2. **Run headed under Xvfb** and re-check the user agent and the obvious detection
   surfaces on a box that allows it.
3. **Add the KMS master key** behind the existing seam, then rotate the key once to
   prove the rotation path.
4. **Watch the password-login share metric**, not just the live-session ratio. It is
   the leading indicator: sessions dying early is what precedes challenges and bans,
   and it shows up there first.

---

## The uncomfortable part, stated plainly

This is credential custody, and credential custody is worse than not having the
password. The mitigations here are real — the blob is useless without the master
key, the password is used rarely and typed only into the platform's own page, and
the user can wipe it — but they shrink an exposure window rather than close it. Where
a platform offers a path that never needs the password, which for TikTok is the QR
login the sibling bridge already implements, that path is strictly better and should
win. This service is for the platforms that leave no such door.

Automating a user's own account with their consent is what every messaging bridge
does. It can still breach a platform's terms and get the account restricted. That is
why enrolment, status and delete are first-class endpoints and not afterthoughts.
