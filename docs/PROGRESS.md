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

CURRENT: Task C done.
NEXT: Task D (API + pipeline + SQLite + demo, and G4 live read-only import), then
Task E (docs distill), F (iPhone assets).
