# Finish here — morning checklist (Jakob)

The overnight run (per `docs/BRIEF.md` §0.5) is complete: Tasks A–F built and
committed on `integration`, 183 tests green (1 skipped), and the read path proven
**live** at G4. Nothing is blocking. Below is what needs you in the morning, then
the state.

## No blocker at the top

The run hit no dead-end it had to stall on. The two honest gaps below are caused by
the empty inbox overnight, not by the code, and are the first two morning items.

## Morning checklist (in order)

1. **Real password login (gate G3).** Not attempted overnight on purpose (a
   challenge nobody can clear could burn the good session). To try it against the
   throwaway account:
   ```sh
   ./run-web-capture.sh --mode manual --headful --user jakob   # or a fresh --user
   ```
   Log in with username + password. Record the outcome in the failure-mode row
   "password login from a fresh fingerprint: <what happened>" (success / needs_user
   / verification are all valid data). The ladder itself is proven against the fake
   platform (6 real-browser e2e); this is the one thing only a real account shows.

2. **A live DM to fill the two gaps.** The inbox was empty all night, so
   `messages_*` and `ws_inbound_dm` are synthetic-only. Have a friend send one DM,
   then:
   ```sh
   .venv/bin/python scripts/g4_live_sync.py --user jakob --watch 120
   python scripts/redact-capture.py 'browser-data/jakob/capture-*.jsonl'
   python scripts/decode-frontier.py 'browser-data/jakob/capture-*.jsonl'
   ```
   That captures the real message body + a real inbound frontier frame, pins the
   field numbers in `docs/observations/frontier-fields.md`, and replaces the
   synthetic `messages_synth.json` / `conv_list_synth.json` fixtures.

3. **Re-run the demo with you watching.**
   ```sh
   ./scripts/demo.sh          # writes docs/demo-transcript.md
   ```
   Confirm it logs in, syncs contacts/threads/messages into SQLite, and prints
   `/metrics`. For a live demo, point the demo's provider factory at a `WebProvider`
   over the imported session instead of the fixture page.

4. **Submit.** When you are happy:
   ```sh
   git tag case-study-submission
   git push origin integration --tags
   ```
   (The overnight run never pushes and never tags — that is your call.)

## What is proven live vs otherwise

- **Live:** mobile signing accepted (blocked only by IP); headless browser QR from a
  datacenter; a logged-in DM-surface capture; the in-page re-signing probe (the page
  signs); **G4 — the captured session imported read-only, 19 real contacts pulled
  through `WebProvider` into SQLite, frontier socket opened** (08:23 CEST).
- **Fixtures:** contacts/profile parsers (real redacted), conversation/message
  parsers + cursor pagination + `Syncer` dedup (synthetic), frontier decode,
  SchemaChange.
- **Fake-platform only:** the full password ladder (6 real-browser e2e).
- **Not built:** live web conv-list JSON mirror (empty inbox), web send/mark-read
  (no fixture, allow-send was no), the Go appservice (mapping in `DESIGN.md` §9),
  mobile device registration (TTEncrypt unavailable).

## Open [Guess] list (to verify)

- The inner frontier message-body field numbers (`frontier.py::_message_from_inner`)
  are `[Inf]` from the mobile IM SDK — pin them from item 2's real DM.
- The live web conversation-list JSON shape (we saw the protobuf `get_by_user_init`
  envelope, empty) — confirm against a non-empty inbox.
- The web `send_text` request shape — needs an `--allow-send` capture.

## Pointers

`docs/DECISIONS.md` (running log, incl. the G4 result and the shred), `DESIGN.md`
§11 (proven/not-built) and §12 (Monday plan), `docs/PROGRESS.md` (per-step),
`docs/demo-transcript.md` (the evidence graders can read). Submission commit:
the tip of `integration` after this run.
