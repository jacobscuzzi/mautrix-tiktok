# Progress (unattended run, 2026-09-18)

One line per finished step. Current task and next action at the bottom. After any
context compaction, re-read `docs/BRIEF.md` §0 and this file before doing anything.

- Task 0 — read brief + all notes; detected env (laptop/WSL2, Playwright chromium);
  restored brief/notes/capture into the tree; merged the provider seam (+29 tests);
  wrote capture-notes, DECISIONS, PROGRESS, CLAUDE.md. Tests: 100 (1 signer test
  skipped when SignerPy absent).

- Task A - capture tool (bridge/cmd/capture.py + browser.py), redact-capture.py,
  decode-frontier.py, fixtures + manifest, observation doc (G2 self-approved).
- Task B - bridge/web/{session,page,frontier}.py + providers/web.py; normalize
  extended (kind/handle/sec_uid/last_ts, additive); parsers fixture-tested;
  WebProvider driven through the real Syncer with dedup; SchemaChange on renamed
  field; frontier decode; avatar allowlist. Tests: 146 (1 skipped).

- Task C - web_password_login.py (Ladder state machine + PlaywrightPasswordDriver,
  selectors pinned from the capture), web_cookie_import.py (fingerprint-mismatch
  guard), password/cookies registered in login.py; tests/fake_platform.py (real
  HTTP: login/wrong-pw/2fa/lock/inbox). Ladder proven vs the fake platform with a
  real headless browser (6 e2e) + unit tests; secrets-not-logged test. G3 real
  attempt deferred to morning. Tests: 165 (1 skipped).

- Task D - bridge/pipeline.py (SQLite raw layer, idempotent upsert, webhook +
  dead-letter), bridge/api.py (stdlib http.server, Bearer auth, login start/step/
  status/delete, contacts/threads/messages, /metrics), BridgeRuntime (N logins,
  dead session -> needs_user, never spins), metrics leading indicators +
  Prometheus, scripts/demo.sh + bridge/cmd/demo.py -> docs/demo-transcript.md,
  scripts/export-session.py. Tests: 180 (1 skipped).

- G4 LIVE (read-only): captured session still alive; 19 real contacts pulled
  through WebProvider into SQLite; frontier socket opened (1 sync frame, no DM).
  Session imported to encrypted session_store; plaintext storage_state.json shredded.

- Task F - inject assets (navigator untouched) + needs_user action.
- Task E - DESIGN.md distilled (path/architecture/login/failure-table/security/
  metric/ToS+escalation/bridgev2/iPhone/proven-list/Monday-plan), README failure-
  modes-first, build log updated.
- G5 - FINISH-HERE.md morning checklist written.

DONE. Definition-of-done (BRIEF §9) all green:
1. 183 tests pass (1 skipped).  2. manifest + fixtures parse via WebProvider;
5 required kinds listed (2 real, conv_list empty, 2 gaps).  3. observation doc
with in-page probe [Obs].  4. ladder+cookies pass fake platform; no real password
attempt.  5. G4 live sync attempted -> connected, 19 contacts, storage_state
shredded, recorded w/ timestamps.  6. API+pipeline demo-transcript exists.
7. DESIGN <=3pp all sections + Monday plan + proven list; README failure-first.
8. FINISH-HERE morning checklist; DECISIONS+PROGRESS current; CLAUDE.md -> brief.
9. no secrets in history outside redacted fixtures; no browser-data tracked.

POST-RUN (2026-09-18): a live DM exchange was captured at a second G4 run; the
frontier message parser was corrected against the real layout (was [Inf], now
[Obs]) and extracts all 10 messages with correct sender attribution.
ws_inbound_dm is now verified live. Tests: 185.

STOP CONDITION: checklist complete. Remaining morning gate: real password login
(G3) + push+tag - they need Jakob. See FINISH-HERE.md.
NEXT: Task D (API + pipeline + SQLite + demo, and G4 live read-only import), then
Task E (docs distill), F (iPhone assets).
