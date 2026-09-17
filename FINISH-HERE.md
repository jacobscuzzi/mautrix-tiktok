# How to finish the TikTok bridge (one human step)

Everything runs on this server. The only thing that needs you is scanning a QR
with your TikTok app once (the login is approved on your phone, so no password
and no captcha).

## One command

```sh
cd ex02
./run-web-login.sh
```

It prints when the QR is ready at `browser-data/qr.png`. Open that file, scan it
in the TikTok app (Profile -> menu -> scan icon), and approve. The script then:

1. captures your web session (encrypted-at-rest capable via SessionStore), and
2. opens TikTok's web Messages and records the real DM endpoints and websocket
   frames to `browser-data/dm_capture.jsonl` (+ a screenshot `messages.png`).

That capture answers the last open question: whether the web session reads your
DMs, and exactly which endpoints to use. From there the bridge normalizes those
into events (the pipeline in `DESIGN.md` / `DESIGN-BROWSER.md`).

## Why this is the server-runnable path

The raw mobile API is IP-blocked from a datacenter (`error_code 7`) and device
registration needs an encryption blob no public library has. A real browser is
not blocked, because it has Chrome's fingerprint and runs TikTok's own JS
signing. See `DESIGN-BROWSER.md`.

## If you'd rather I drive it

Run the command, scan, and tell me "scanned" — I'll take over from the capture
and map the DM API. Or tell me "new QR" and I'll generate and send you a fresh
one to scan.
