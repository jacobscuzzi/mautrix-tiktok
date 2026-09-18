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

CURRENT: Task B done.
NEXT: Task C - password ladder + cookies import + tests/fake_platform.py; then
Task D (API + pipeline + demo), E (docs), F (iPhone assets).
