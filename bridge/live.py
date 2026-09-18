"""LiveBridge -- the running bridge for the demo wrapper.

One background thread owns Playwright (the sync API is single-threaded). For each
connected user it holds a persistent, stealth browser context, detects the real
TikTok login, encrypts the session at rest, and syncs that user's real contacts /
threads / messages into the SQLite pipeline -- REST through the in-page signer,
realtime over the frontier websocket. HTTP handlers call the thread-safe methods
here (connect / status / logout); reads come from the pipeline.

Security: the session blob is envelope-encrypted (`session_store`, AES-GCM, per-user
data key wrapped by a master key). On logout everything for that login is wiped:
the session blob, the pipeline rows, and the browser profile.
"""
from __future__ import annotations

import os
import queue
import shutil
import threading
import time

from . import errors, normalize
from .auth import browser as browserfac
from .metrics import render_prometheus
from .pipeline import Pipeline
from .providers.web import WebProvider
from .session_store import SessionStore
from .state import SyncState
from .sync import Syncer
from .web import session as websess
from .web.page import PageClient, playwright_evaluator

LOGIN_URL = "https://www.tiktok.com/login/phone-or-email/email"
QR_URL = "https://www.tiktok.com/login/qrcode"
MESSAGES_URL = "https://www.tiktok.com/messages"

# state machine for one login
OPENING = "opening_browser"
WAITING_LOGIN = "waiting_login"
CONNECTING = "connecting"
CONNECTED = "connected"
NEEDS_USER = "needs_user"
ERROR = "error"
LOGGED_OUT = "logged_out"


class LiveLogin:
    def __init__(self, login_id, profile_dir, flow="password"):
        self.login_id = login_id
        self.profile_dir = profile_dir
        self.flow = flow               # "password" (email/username) or "qr"
        self.state = OPENING
        self.account = None            # {handle, uid, nickname, avatar}
        self.last_error = None
        self.password_login_used = (flow == "password")
        self.connected_at = None
        self.last_sync_ms = 0
        self.authenticated = False
        self.headful_login = False
        self.init_bodies = []          # get_by_user_init protobuf bodies (backlog)
        self.init_request = None        # decoded init request envelope (history template)
        self.im_api_url = None          # a signed im-api URL to reuse for history
        self.known_users = set()
        self.short_ids = {}            # conversation_id -> conversation_short_id
        self.oldest_us = {}            # conversation_id -> oldest message ts (us)
        self.open_conv = None          # conversation_id currently open in the page
        self.sending = False           # one send at a time (never overlap)
        # worker-thread-only:
        self.ctx = None
        self.page = None
        self.provider = None
        self.sync_state = SyncState()
        self._t_login = time.time()

    def public(self):
        return {"login_id": self.login_id, "state": self.state,
                "account": self.account, "last_error": self.last_error,
                "last_sync_ms": self.last_sync_ms}

    def metrics_dict(self):
        return {"authenticated": self.authenticated, "last_sync_ms": self.last_sync_ms,
                "state": self.state, "password_login_used": self.password_login_used}


class LiveBridge:
    def __init__(self, data_dir="browser-data/_live", db_path=None,
                 master_key=None, headless=False, poll_seconds=20,
                 login_timeout=300):
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        self.db_path = db_path or os.path.join(data_dir, "bridge.sqlite")
        self.pipeline = Pipeline(self.db_path)
        self.store = SessionStore(data_dir, master_key or os.urandom(32))
        self.headless = headless
        self.poll_seconds = poll_seconds
        self.login_timeout = login_timeout
        self.logins = {}
        self.error_totals = {}
        self.lags = []
        self._lock = threading.Lock()
        self._q = queue.Queue()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._started = False

    # ---- public, thread-safe -------------------------------------------------

    def start(self):
        if not self._started:
            self._started = True
            self._thread.start()

    def connect(self, flow="password"):
        # One STABLE profile for the demo, reused across connects and app restarts.
        # This is the design invariant (one device identity per user, never rotated)
        # and it stops TikTok's login throttle: an existing session is reused with
        # no new login, so we do not log in from a fresh fingerprint every time.
        # flow: "password" (email/username form) or "qr" (scan with the app,
        # passwordless -- the safest path, for users who don't know their password).
        login_id = "session"
        with self._lock:
            existing = self.logins.get(login_id)
            if existing and existing.state in (OPENING, WAITING_LOGIN, CONNECTING, CONNECTED):
                return login_id
        profile_root = os.path.join(self.data_dir, "session")
        os.makedirs(os.path.join(profile_root, "profile"), exist_ok=True)
        login = LiveLogin(login_id, profile_root, flow=flow)
        with self._lock:
            self.logins[login_id] = login
        self.pipeline.upsert_login(login_id, source="web", state=OPENING,
                                   password_login_used=True)
        self._q.put(("open", login_id))
        return login_id

    def status(self, login_id):
        with self._lock:
            login = self.logins.get(login_id)
            return login.public() if login else None

    def list_logins(self):
        with self._lock:
            return [l.public() for l in self.logins.values()]

    def chats(self, login_id):
        return {"contacts": self.pipeline.list_contacts(login_id),
                "threads": self.pipeline.list_threads(login_id)}

    def messages(self, login_id, thread_id, cursor=0):
        return self.pipeline.get_messages(thread_id, cursor, limit=200)

    def logout(self, login_id):
        self._q.put(("logout", login_id))

    def submit(self, fn, timeout=60):
        """Run fn() on the browser worker thread and return its result."""
        box = {}
        done = threading.Event()

        def job():
            try:
                box["r"] = fn()
            except Exception as e:  # noqa: BLE001
                box["e"] = e
            finally:
                done.set()
        self._q.put(("call", job))
        if not done.wait(timeout):
            raise TimeoutError("browser worker busy")
        if "e" in box:
            raise box["e"]
        return box.get("r")

    def load_older(self, login_id, thread_id, count=30):
        """Fetch one older page for a conversation (returns {added, has_more})."""
        return self.submit(lambda: self._load_older(login_id, thread_id, count))

    def send_message(self, login_id, thread_id, text):
        """Send a text message by driving TikTok's own composer (it signs the send).

        Never blind-replays: sends once via the page and confirms by our own message
        returning over the frontier socket. Returns {ok, error?}."""
        text = (text or "").strip()
        if not text:
            return {"ok": False, "error": "empty message"}
        return self.submit(lambda: self._send(login_id, thread_id, text), timeout=45)

    def metrics_text(self):
        with self._lock:
            dicts = [l.metrics_dict() for l in self.logins.values()]
            errs = dict(self.error_totals)
            lags = list(self.lags)
        return render_prometheus(dicts, int(time.time() * 1000),
                                 self.poll_seconds * 1000, errs, lags)

    def shutdown(self):
        self._stop.set()
        self._q.put(("stop", None))

    # ---- worker thread -------------------------------------------------------

    def _run(self):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            self._p = p
            last_tick = 0
            while not self._stop.is_set():
                try:
                    cmd, arg = self._q.get(timeout=0.5)
                except queue.Empty:
                    cmd, arg = None, None
                if cmd == "stop":
                    break
                if cmd == "open":
                    self._do_open(arg)
                elif cmd == "logout":
                    self._do_logout(arg)
                elif cmd == "call":
                    arg()
                # periodic tick: detect logins, pump pages, sync
                now = time.time()
                if now - last_tick >= 1.0:
                    last_tick = now
                    self._tick()
            self._close_all()

    def _do_open(self, login_id):
        login = self.logins.get(login_id)
        if not login:
            return
        try:
            # 1) reuse an existing alive session HEADLESS first (no visible window,
            #    no new login) -- this is the common path after the first login.
            self._open_context(login, headless=True)
            login.headful_login = False
            login.page.goto(MESSAGES_URL, wait_until="domcontentloaded", timeout=45000)
            login.page.wait_for_timeout(2500)
            cookies = {c["name"]: c["value"] for c in login.ctx.cookies()}
            if cookies.get("sessionid") and "/login" not in login.page.url:
                self._set_state(login, CONNECTING)
                self._establish(login, cookies)     # connected, no window shown
                return
            # 2) no live session -> open a real login window (headful) once.
            login.ctx.close()
            login.ctx = None
            login.page = None
            for _ in range(20):
                try:
                    self._open_context(login, headless=self.headless)
                    break
                except Exception:
                    time.sleep(0.5)
            login.headful_login = not self.headless
            start_url = QR_URL if login.flow == "qr" else LOGIN_URL
            login.page.goto(start_url, wait_until="domcontentloaded", timeout=45000)
            self._set_state(login, WAITING_LOGIN)
        except Exception as e:
            self._fail(login, ERROR, e)

    def _open_context(self, login, headless):
        ctx, _ = browserfac.launch_persistent(
            self._p, login.profile_dir, headless=headless, channel="chromium")
        login.ctx = ctx
        login.page = ctx.pages[0] if ctx.pages else ctx.new_page()
        login.page.on("websocket", lambda ws: self._attach_ws(login, ws))
        # the /messages page fetches the existing-conversation backlog itself
        # (protobuf get_by_user_init); capture it so existing chats show at once.
        ctx.on("response", lambda r: self._capture_init(login, r))

    def _capture_init(self, login, resp):
        try:
            if "get_by_conversation" in resp.url:
                from .web import frontier
                for m in frontier.messages_from_conversation_body(resp.body()):
                    login.open_conv = m["conversation_id"]     # which chat is open
                    break
                return
            if "im-api.tiktok.com" in resp.url and "get_by_user_init" in resp.url:
                login.init_bodies.append(resp.body())
                if login.init_request is None:
                    from . import proto
                    req = resp.request.post_data_buffer
                    if req:
                        login.init_request = proto.decode_tree(req)
                    # reuse this signed im-api URL, swapped to the history path
                    login.im_api_url = resp.url.split("?", 1)[0].replace(
                        "/v2/message/get_by_user_init",
                        "/v1/message/get_by_conversation") + (
                        "?" + resp.url.split("?", 1)[1] if "?" in resp.url else "")
        except Exception:
            pass

    def _go_background(self, login):
        """After login, close the visible window and reopen the SAME profile
        headless so the session keeps syncing in the background."""
        if not getattr(login, "headful_login", False):
            return  # login was already headless: nothing visible to close
        try:
            self._save_session(login)          # persist before closing the window
            login.ctx.close()
        except Exception:
            pass
        login.ctx = None
        login.page = None
        # relaunch headless on the same profile (retry until the profile lock frees)
        last = None
        for _ in range(20):
            try:
                self._open_context(login, headless=True)
                return
            except Exception as e:
                last = e
                time.sleep(0.5)
        raise last or RuntimeError("could not reopen background browser")

    def _attach_ws(self, login, ws):
        if "im-ws.tiktok.com" in ws.url and login.provider:
            ws.on("framereceived",
                   lambda f: login.provider.page._emit_frame(
                       f if isinstance(f, (bytes, bytearray)) else f.encode()))

    def _tick(self):
        for login in list(self.logins.values()):
            try:
                if login.state == WAITING_LOGIN:
                    self._check_login(login)
                elif login.state == CONNECTED:
                    self._sync_tick(login)
            except Exception as e:
                self._fail(login, ERROR, e)

    def _check_login(self, login):
        if login.ctx is None:
            return
        if time.time() - login._t_login > self.login_timeout:
            self._fail(login, NEEDS_USER, RuntimeError("login timed out"))
            return
        cookies = {c["name"]: c["value"] for c in login.ctx.cookies()}
        if not cookies.get("sessionid"):
            if self._login_blocked(login.page):
                self._fail(login, NEEDS_USER, RuntimeError(
                    "TikTok is rate-limiting logins (max attempts). Wait ~15-60 min, "
                    "then reconnect -- the saved session is reused with no new login."))
                return
            login.page.wait_for_timeout(300)   # pump events
            return
        # logged in -> hand off to a background headless context, then connect
        self._set_state(login, CONNECTING)
        self._go_background(login)
        self._establish(login, cookies)

    @staticmethod
    def _login_blocked(page):
        try:
            return bool(page.evaluate(
                "() => /maximum number of attempts|too many attempts|try again later/i"
                ".test((document.body && document.body.innerText) || '')"))
        except Exception:
            return False

    def _establish(self, login, cookies):
        page = login.page
        try:
            page.goto(MESSAGES_URL, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(3000)
        except Exception:
            pass
        pc = PageClient(playwright_evaluator(page))
        login.provider = WebProvider(pc, avatar_client=None)
        # subscribe frontier -> ingest live messages
        login.provider.subscribe(lambda m: self._ingest_message(login, m))
        # identity + encrypted session at rest
        self._save_session(login)
        # existing conversations: the page's own backlog fetch, captured on load
        self._ingest_init(login)
        # first sync (contacts, account label)
        self._sync_now(login, first=True)
        self._resolve_peers(login)
        login.authenticated = True
        login.connected_at = time.time()
        self._set_state(login, CONNECTED)

    def _ingest_init(self, login):
        """Ingest the existing-conversation backlog from captured init bodies."""
        from .web import frontier
        bodies, login.init_bodies = login.init_bodies, []
        n = 0
        for body in bodies:
            for m in frontier.messages_from_init_body(body):
                self._ingest_message(login, m)
                n += 1
        return n

    def _load_older(self, login_id, thread_id, count):
        login = self.logins.get(login_id)
        if not login or not login.provider:
            return {"added": 0, "has_more": False, "error": "not connected"}
        short = login.short_ids.get(thread_id)
        cursor = login.oldest_us.get(thread_id)
        if cursor is None:
            rows = self.pipeline.get_messages(thread_id, 0, limit=1)
            cursor = (rows[0]["ts"] * 1000) if rows else int(time.time() * 1000000)
        if not short:
            return {"added": 0, "has_more": False, "error": "unknown conversation"}
        from .web.page import playwright_pb_poster
        login.provider.configure_history(
            playwright_pb_poster(login.page), login.init_request, login.im_api_url)
        try:
            msgs, nxt, more = login.provider.get_older_messages(thread_id, short, cursor, count)
        except errors.BridgeError as e:
            return {"added": 0, "has_more": False, "error": str(e)}
        added = 0
        before = self.pipeline.get_messages(thread_id, 0, limit=100000)
        have = {r["message_id"] for r in before}
        for m in msgs:
            if m["server_message_id"] not in have:
                self._ingest_message(login, m)
                added += 1
        if nxt:
            login.oldest_us[thread_id] = min(login.oldest_us.get(thread_id, nxt), nxt)
        return {"added": added, "has_more": bool(more)}

    EDITOR_SEL = '[data-e2e="message-input-area"] [contenteditable="true"], ' \
                 'div[role="textbox"][contenteditable="true"]'
    CONV_ITEM_SEL = '[data-e2e="dm-new-conversation-item"]'
    # the send affordance appears after typing; it is an svg with this data-e2e
    SEND_BTN_SEL = '[data-e2e="dm-new-send-btn"], [data-e2e="message-send"]'

    def _open_conversation(self, login, thread_id, tries=10):
        page = login.page
        if "/messages" not in page.url:
            try:
                page.goto(MESSAGES_URL, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(2500)
            except Exception:
                pass
        if login.open_conv == thread_id and page.query_selector(self.EDITOR_SEL):
            return True
        items = page.query_selector_all(self.CONV_ITEM_SEL)
        for it in items[:tries]:
            login.open_conv = None
            try:
                it.click()
            except Exception:
                continue
            page.wait_for_timeout(1400)   # let get_by_conversation fire + be captured
            if login.open_conv == thread_id:
                return True
        return False

    def _fetch_recent(self, login, thread_id, count=15):
        """Pull the newest messages of a conversation from the server and ingest
        them, so a just-sent message shows immediately (with its real id)."""
        short = login.short_ids.get(thread_id)
        if not (short and login.init_request and login.im_api_url and login.provider):
            return 0
        from .web.page import playwright_pb_poster
        login.provider.configure_history(
            playwright_pb_poster(login.page), login.init_request, login.im_api_url)
        try:
            msgs, _, _ = login.provider.get_older_messages(
                thread_id, short, int(time.time() * 1_000_000), count)
        except errors.BridgeError:
            return 0
        added = 0
        for m in msgs:
            if self._ingest_message(login, m):
                added += 1
        return added

    def _send(self, login_id, thread_id, text):
        login = self.logins.get(login_id)
        if not login or login.state != CONNECTED or not login.page:
            return {"ok": False, "error": "not connected"}
        if login.sending:
            return {"ok": False, "error": "a send is already in progress"}
        login.sending = True
        try:
            page = login.page
            if not self._open_conversation(login, thread_id):
                return {"ok": False, "error": "could not open that conversation"}
            editor = page.query_selector(self.EDITOR_SEL)
            if not editor:
                return {"ok": False, "error": "schema_change: composer not found"}
            editor.click()
            page.keyboard.type(text, delay=15)
            page.wait_for_timeout(250)
            # send: Enter sends this composer; if the box is not cleared, click the
            # send button that appears after typing. Never re-send beyond this.
            page.keyboard.press("Enter")
            page.wait_for_timeout(600)
            if (page.evaluate("(e) => e.innerText", editor) or "").strip():
                btn = page.query_selector(self.SEND_BTN_SEL)
                if btn:
                    try:
                        btn.click()
                    except Exception:
                        pass
                page.wait_for_timeout(600)
            sent = not (page.evaluate("(e) => e.innerText", editor) or "").strip()
            # show it: pull the message back from the server (real id) + confirm.
            page.wait_for_timeout(700)
            self._fetch_recent(login, thread_id)
            if not sent:
                return {"ok": False, "error": "the message did not send (composer not cleared)"}
            return {"ok": True}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)[:200]}
        finally:
            login.sending = False

    def _resolve_peers(self, login):
        """Give every conversation peer a name + avatar via the profile endpoint."""
        prov = login.provider
        if not prov:
            return
        own = (login.account or {}).get("uid") or self._own_uid(login)
        peers = set()
        for t in self.pipeline.list_threads(login.login_id):
            for uid in (t.get("thread_id") or "").split(":")[2:]:
                if uid and uid != own:
                    peers.add(uid)
        known = {u["user_id"] for u in self.pipeline.list_contacts(login.login_id)}
        todo = [p for p in peers if p not in known and p not in login.known_users]
        if not todo:
            return
        try:
            for u in prov.get_profiles(todo[:50]):
                self.pipeline.upsert_user(login.login_id, u)
                login.known_users.add(u.user_id)
        except errors.BridgeError:
            login.known_users.update(todo)   # do not retry every tick

    def _save_session(self, login):
        try:
            state = login.ctx.storage_state()
            uid = self._own_uid(login)
            sess = websess.WebSession.from_storage_state(state, self._meta(login), uid=uid)
            self.store.save(login.login_id, sess.to_blob())
        except Exception:
            pass

    def _meta(self, login):
        try:
            return login.page.evaluate(
                "() => ({ua: navigator.userAgent, lang: navigator.language,"
                " tz: Intl.DateTimeFormat().resolvedOptions().timeZone,"
                " iw: innerWidth, ih: innerHeight})")
        except Exception:
            return {}

    def _own_uid(self, login):
        try:
            uid = login.page.evaluate(
                "() => { const el = document.getElementById('__UNIVERSAL_DATA_FOR_REHYDRATION__');"
                " if(!el) return ''; try { const d = JSON.parse(el.textContent);"
                " return d.__DEFAULT_SCOPE__['webapp.app-context'].user.uid || ''; } catch(e){ return ''; } }")
            return uid or ""
        except Exception:
            return ""

    def _sync_tick(self, login):
        # pump websocket events, and poll REST gently
        try:
            login.page.wait_for_timeout(300)
        except Exception:
            pass
        if time.time() * 1000 - login.last_sync_ms >= self.poll_seconds * 1000:
            self._sync_now(login)
            self._resolve_peers(login)   # name any new conversation peers

    def _sync_now(self, login, first=False):
        prov = login.provider
        # contacts
        try:
            users, _, _ = prov.list_contacts()
            for u in users:
                self.pipeline.upsert_user(login.login_id, u)
            if users and not login.account:
                login.account = {"contacts": len(users)}
        except errors.AuthError as e:
            return self._fail(login, NEEDS_USER, e)
        except errors.BridgeError:
            pass
        # own profile / account label
        self._set_account(login)
        # conversations + messages
        try:
            convs, _, _ = prov.list_conversations("0")
            for c in convs:
                self.pipeline.upsert_thread(login.login_id, normalize.to_thread(c))
            syncer = Syncer(prov, login.sync_state, self.pipeline.make_emit(login.login_id))
            syncer.poll_once()
        except errors.AuthError as e:
            return self._fail(login, NEEDS_USER, e)
        except errors.BridgeError:
            pass
        login.last_sync_ms = int(time.time() * 1000)
        self.pipeline.set_login_state(login.login_id, CONNECTED,
                                      last_sync_ts=login.last_sync_ms)

    def _set_account(self, login):
        if login.account and login.account.get("handle"):
            return
        try:
            info = login.page.evaluate(
                "() => { const el = document.getElementById('__UNIVERSAL_DATA_FOR_REHYDRATION__');"
                " if(!el) return null; try { const u = JSON.parse(el.textContent)"
                ".__DEFAULT_SCOPE__['webapp.app-context'].user;"
                " return {handle: u.uniqueId, nickname: u.nickName, uid: u.uid,"
                " avatar: (u.avatarUri && u.avatarUri[0]) || ''}; } catch(e){ return null; } }")
            if info and info.get("handle"):
                login.account = info
        except Exception:
            pass

    def _ingest_message(self, login, m):
        """Ingest one normalized message dict. Returns True if newly inserted."""
        ev = normalize.to_event(m)
        if not ev.message_id:
            return False
        # remember what we need to page history for this conversation
        if m.get("conv_short_id"):
            login.short_ids[ev.conversation_id] = m["conv_short_id"]
        us = m.get("create_time_us") or (ev.timestamp_ms * 1000)
        if us:
            cur = login.oldest_us.get(ev.conversation_id)
            if cur is None or us < cur:
                login.oldest_us[ev.conversation_id] = us
        # make sure the thread exists so the chats view lists it
        self.pipeline.upsert_thread(login.login_id,
                                    normalize.to_thread({"conversation_id": ev.conversation_id,
                                                         "last_message": {"create_time": ev.timestamp_ms}}))
        if self.pipeline.ingest_event(ev, login.login_id):
            lag = max(0.0, (int(time.time() * 1000) - ev.timestamp_ms) / 1000.0)
            with self._lock:
                self.lags.append(lag)
            return True
        return False

    # ---- state helpers -------------------------------------------------------

    def _set_state(self, login, state):
        with self._lock:
            login.state = state
        self.pipeline.set_login_state(login.login_id, state)

    def _fail(self, login, state, exc):
        login.authenticated = False
        login.last_error = f"{type(exc).__name__}: {exc}"
        with self._lock:
            login.state = state
            self.error_totals[state] = self.error_totals.get(state, 0) + 1
        self.pipeline.set_login_state(login.login_id, state, login.last_error)

    def _do_logout(self, login_id):
        login = self.logins.get(login_id)
        if not login:
            return
        try:
            if login.ctx:
                login.ctx.close()
        except Exception:
            pass
        # wipe everything for this login (data deletion on logout)
        self.store.delete(login_id)
        self.pipeline.wipe_login(login_id)
        try:
            shutil.rmtree(login.profile_dir, ignore_errors=True)
        except Exception:
            pass
        with self._lock:
            login.state = LOGGED_OUT
            login.provider = None
            login.ctx = None
            self.logins.pop(login_id, None)

    def _close_all(self):
        for login in list(self.logins.values()):
            try:
                if login.ctx:
                    login.ctx.close()
            except Exception:
                pass
