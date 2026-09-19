"""LiveBridge -- the running bridge for the demo wrapper.

One background thread owns Playwright (the sync API is single-threaded). The demo
runs a single login (`login_id = "session"`, one persistent browser profile); the
multi-login shape is `BridgeRuntime` in app.py. For that login it detects the real
TikTok login, seals the session at rest, and syncs the account's real contacts /
threads / messages into the SQLite pipeline -- REST through the in-page signer,
realtime over the frontier websocket. HTTP handlers call the thread-safe methods
here (connect / status / logout); reads come from the pipeline.

Security: the session blob is envelope-encrypted (`session_store`, AES-GCM, per-login
data key wrapped by a master key); without `BRIDGE_MASTER_KEY` the key is ephemeral
and the blob does not survive a restart. What survives a restart is the Chromium
profile directory under `data_dir`, protected only by file permissions, and the
SQLite cache is plaintext. On logout everything for that login is wiped: the
session blob, the pipeline rows, and the browser profile.
"""
from __future__ import annotations

import collections
import logging
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

log = logging.getLogger("bridge.live")

# TikTok's own chooser: "Use QR code" / "Use phone / email / username" / social
LOGIN_URL = "https://www.tiktok.com/login"
QR_URL = "https://www.tiktok.com/login/qrcode"
MESSAGES_URL = "https://www.tiktok.com/messages"

# Chromium's process-singleton files inside the profile directory.
_SINGLETON_FILES = ("SingletonLock", "SingletonSocket", "SingletonCookie")


def wait_profile_unlocked(profile_root, timeout=15.0):
    """Wait until no Chromium holds the profile. Launching on a still-locked
    profile does not fail: Chromium hands the launch to the running instance,
    which opens an extra empty window there and gives us no page at all (the
    macOS "empty windows + NoneType goto" crash). Returns False on timeout."""
    profile = os.path.join(profile_root, "profile")
    deadline = time.time() + timeout
    while True:
        if not any(os.path.lexists(os.path.join(profile, f)) for f in _SINGLETON_FILES):
            return True
        if time.time() >= deadline:
            return False
        time.sleep(0.1)


def _page_closed(page):
    try:
        return bool(page.is_closed())
    except Exception:  # noqa: BLE001
        return True


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
        self.last_sync_ms = 0
        self.authenticated = False
        self.qr_png = None             # base64 QR image, shown in the wrapper UI
        self.headless = True           # current browser mode (a window only for typing)
        self.init_bodies = []          # get_by_user_init protobuf bodies (backlog)
        self.init_request = None        # decoded init request envelope (history template)
        self.im_api_url = None          # a signed im-api URL to reuse for history
        self.known_users = set()
        self.short_ids = {}            # conversation_id -> conversation_short_id
        self.oldest_us = {}            # conversation_id -> oldest message ts (us)
        # worker-thread-only:
        self.ctx = None
        self.page = None
        self.provider = None
        self.sync_state = SyncState()
        self._t_login = time.time()

    def public(self):
        return {"login_id": self.login_id, "state": self.state,
                "account": self.account, "last_error": self.last_error,
                "last_sync_ms": self.last_sync_ms, "qr": self.qr_png}

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
        if master_key is None:
            log.warning("BRIDGE_MASTER_KEY not set: using an ephemeral key, the sealed "
                        "session will not be readable after a restart")
            master_key = os.urandom(32)
        self.store = SessionStore(data_dir, master_key)
        self.headless = headless
        self.poll_seconds = poll_seconds
        self.login_timeout = login_timeout
        self.logins = {}
        self.error_totals = {}
        self.lags = collections.deque(maxlen=1000)   # recent delivery lags (s)
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
                                   password_login_used=login.password_login_used)
        if existing is not None:
            # a failed login may still own the window on this profile: close it
            # first, or the new launch races the profile lock / opens a 2nd window
            self._q.put(("close", existing))
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
                elif cmd == "close":
                    self._do_close(arg)
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
        # Everything runs HEADLESS -- the user reads their chats in the wrapper UI.
        # The one exception is a password login: the user must type into TikTok's
        # own page, so that (and only that) gets a visible window, which is swapped
        # back for a headless browser on the same profile as soon as the session
        # exists (`_check_login`). QR login shows the code inside the wrapper UI.
        try:
            self._open_context(login, headless=True)
        except Exception as e:  # browser failed to launch (e.g. Chromium not installed)
            self._fail(login, ERROR, RuntimeError(
                f"could not start the browser: {e}. Run ./setup.sh (installs "
                f"Playwright + Chromium)."))
            return
        try:
            # try an existing session first: no window at all
            login.page.goto(MESSAGES_URL, wait_until="domcontentloaded", timeout=45000)
            login.page.wait_for_timeout(2500)
            cookies = {c["name"]: c["value"] for c in login.ctx.cookies()}
            if cookies.get("sessionid") and "/login" not in login.page.url:
                self._set_state(login, CONNECTING)
                self._establish(login, cookies)     # already logged in
                return
            if login.flow == "qr":
                login.page.goto(QR_URL, wait_until="domcontentloaded", timeout=45000)
                login.page.wait_for_timeout(1500)   # let the QR image render/arrive
            else:
                self._relaunch(login, headless=self.headless)   # the login window
                login.page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=45000)
                login.page.wait_for_timeout(1500)
            self._set_state(login, WAITING_LOGIN)
        except Exception as e:
            self._fail(login, ERROR, e)

    def _open_context(self, login, headless):
        if not wait_profile_unlocked(login.profile_dir):
            log.warning("profile %s still locked after 15s; launching anyway",
                        login.profile_dir)
        ctx, _ = browserfac.launch_persistent(
            self._p, login.profile_dir, headless=headless, channel="chromium")
        login.ctx = ctx
        login.headless = headless
        login.page = ctx.pages[0] if ctx.pages else ctx.new_page()
        login.page.on("websocket", lambda ws: self._attach_ws(login, ws))
        # the /messages page fetches the existing-conversation backlog itself
        # (protobuf get_by_user_init); capture it so existing chats show at once.
        ctx.on("response", lambda r: self._capture_init(login, r))

    def _relaunch(self, login, headless):
        """Close the current browser and reopen the SAME profile in the other mode.

        The profile keeps the device identity (never rotated) and the cookies are
        carried over explicitly, so the session survives even if Chromium had not
        flushed them to disk yet. `_open_context` waits for the old process to
        release the profile before launching -- the missing step that made the
        earlier swap leak empty windows on macOS.
        """
        if login.ctx is not None and login.headless == headless:
            return
        cookies = []
        if login.ctx is not None:
            try:
                cookies = login.ctx.cookies()
            except Exception as e:  # noqa: BLE001
                log.debug("cookie read before relaunch: %s", e)
            try:
                login.ctx.close()
            except Exception as e:  # noqa: BLE001
                log.debug("context close before relaunch: %s", e)
            login.ctx = None
            login.page = None
        self._open_context(login, headless=headless)
        if cookies:
            try:
                login.ctx.add_cookies(cookies)
            except Exception as e:  # noqa: BLE001
                log.debug("cookie carry-over: %s", e)

    def _capture_init(self, login, resp):
        try:
            if "get_qrcode" in resp.url:
                data = resp.json().get("data", {})
                if data.get("qrcode"):
                    login.qr_png = data["qrcode"]   # base64 PNG shown in the wrapper
                return
            if "check_qrconnect" in resp.url:
                return
            if "get_by_conversation" in resp.url:
                return   # history pages are fetched on demand, not part of the backlog
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
        except Exception as e:  # noqa: BLE001
            log.debug("response capture skipped: %s", e)

    def _attach_ws(self, login, ws):
        if "im-ws.tiktok.com" in ws.url and login.provider:
            ws.on("framereceived",
                   lambda f: login.provider.page._emit_frame(
                       f if isinstance(f, (bytes, bytearray)) else f.encode()))

    def _tick(self):
        for login in list(self.logins.values()):
            try:
                if login.state == WAITING_LOGIN or (
                        login.state == NEEDS_USER and login.ctx is not None
                        and login.provider is None):
                    # NEEDS_USER after a throttle message or the timeout is not the
                    # end: the window is still open and the user may still get in.
                    self._check_login(login)
                elif login.state == CONNECTED:
                    self._sync_tick(login)
            except Exception as e:
                self._fail(login, ERROR, e)

    def _check_login(self, login):
        if login.ctx is None:
            return
        if login.page is None or _page_closed(login.page):
            # the user closed the login window (macOS: the red button leaves a
            # windowless Chromium behind that still locks the profile)
            if login.state == WAITING_LOGIN:
                self._do_close(login)
                self._fail(login, NEEDS_USER, RuntimeError(
                    "the TikTok window was closed before the login finished. "
                    "Click 'Log in with TikTok' to open it again."))
            return
        cookies = {c["name"]: c["value"] for c in login.ctx.cookies()}
        if not cookies.get("sessionid"):
            if login.state == WAITING_LOGIN:
                # surfaced once; the window stays open and is still watched
                if time.time() - login._t_login > self.login_timeout:
                    self._fail(login, NEEDS_USER, RuntimeError(
                        "login timed out. The TikTok window is still open: finish "
                        "logging in there and this page connects by itself."))
                    return
                if self._login_blocked(login.page):
                    self._fail(login, NEEDS_USER, RuntimeError(
                        "TikTok is rate-limiting logins (max attempts). Pick 'Use QR "
                        "code' on TikTok's page (usually not throttled), or wait ~15-60 "
                        "min, or use a different throwaway account. The window stays "
                        "open: once you're in, the session is saved and reused -- don't "
                        "log out & wipe."))
                    return
            login.page.wait_for_timeout(300)   # pump events
            return
        # logged in -> the window goes away, a headless browser on the same
        # profile takes over and syncs in the background
        self._set_state(login, CONNECTING)
        self._relaunch(login, headless=True)
        self._establish(login, cookies)

    @staticmethod
    def _login_blocked(page):
        try:
            return bool(page.evaluate(
                "() => /maximum number of attempts|too many attempts|try again later/i"
                ".test((document.body && document.body.innerText) || '')"))
        except Exception as e:  # noqa: BLE001
            log.debug("login-blocked check failed: %s", e)
            return False

    def _establish(self, login, cookies):
        page = login.page
        # Clear any stale data from a previous session/account so the shown chats
        # always match the account that is actually logged in now. Without this the
        # UI shows phantom conversations that no longer exist.
        self.pipeline.wipe_login(login.login_id)
        self.pipeline.upsert_login(login.login_id, source="web", state=CONNECTING,
                                   password_login_used=login.password_login_used)
        try:
            page.goto(MESSAGES_URL, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(3500)
        except Exception as e:  # noqa: BLE001
            log.debug("/messages load: %s", e)
        pc = PageClient(playwright_evaluator(page))
        login.provider = WebProvider(pc, avatar_client=None)
        # subscribe frontier -> ingest live messages
        login.provider.subscribe(lambda m: self._ingest_message(login, m, live=True))
        # identity + encrypted session at rest
        self._save_session(login)
        # existing conversations: the page's own backlog fetch, captured on load.
        # The list loads asynchronously, so retry a few reloads until it arrives.
        self._ingest_init(login)
        for _ in range(4):
            if self.pipeline.list_threads(login.login_id):
                break
            try:
                page.goto(MESSAGES_URL, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(3000)
            except Exception as e:  # noqa: BLE001
                log.debug("/messages reload: %s", e)
                break
            self._ingest_init(login)
        # first sync (contacts, account label)
        self._sync_now(login, first=True)
        self._resolve_peers(login)
        login.authenticated = True
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
        except Exception as e:  # noqa: BLE001
            log.warning("could not seal the session at rest: %s", e)

    def _meta(self, login):
        try:
            return login.page.evaluate(
                "() => ({ua: navigator.userAgent, lang: navigator.language,"
                " tz: Intl.DateTimeFormat().resolvedOptions().timeZone,"
                " iw: innerWidth, ih: innerHeight})")
        except Exception as e:  # noqa: BLE001
            log.debug("fingerprint read failed: %s", e)
            return {}

    def _own_uid(self, login):
        try:
            uid = login.page.evaluate(
                "() => { const el = document.getElementById('__UNIVERSAL_DATA_FOR_REHYDRATION__');"
                " if(!el) return ''; try { const d = JSON.parse(el.textContent);"
                " return d.__DEFAULT_SCOPE__['webapp.app-context'].user.uid || ''; } catch(e){ return ''; } }")
            return uid or ""
        except Exception as e:  # noqa: BLE001
            log.debug("own uid read failed: %s", e)
            return ""

    def _sync_tick(self, login):
        # pump websocket events, and poll REST gently
        try:
            login.page.wait_for_timeout(300)
        except Exception as e:  # noqa: BLE001
            log.debug("event pump: %s", e)
        # a conversation list that rendered after connect: ingest it now
        if login.init_bodies:
            self._ingest_init(login)
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
        except errors.BridgeError as e:
            self._note(login, e)
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
        except errors.BridgeError as e:
            self._note(login, e)
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
        except Exception as e:  # noqa: BLE001
            log.debug("account label read failed: %s", e)

    def _ingest_message(self, login, m, live=False):
        """Ingest one normalized message dict. Returns True if newly inserted.

        `live` marks a message that just arrived over the frontier websocket: only
        those measure delivery lag. The backlog and paged history are old by
        definition and would otherwise report the age of the inbox as lag."""
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
        if not self.pipeline.ingest_event(ev, login.login_id):
            return False
        if live:
            lag = max(0.0, (int(time.time() * 1000) - ev.timestamp_ms) / 1000.0)
            with self._lock:
                self.lags.append(lag)
        return True

    # ---- state helpers -------------------------------------------------------

    def _set_state(self, login, state):
        with self._lock:
            login.state = state
        self.pipeline.set_login_state(login.login_id, state)

    def _do_close(self, login):
        """Close a failed login's browser so the profile is free for the next one."""
        try:
            if login.ctx:
                login.ctx.close()
        except Exception as e:  # noqa: BLE001
            log.debug("context close on reconnect: %s", e)
        with self._lock:
            login.ctx = None
            login.page = None
            login.provider = None

    def _fail(self, login, state, exc):
        login.authenticated = False
        login.last_error = f"{type(exc).__name__}: {exc}"
        log.warning("login %s -> %s: %s", login.login_id, state, login.last_error)
        with self._lock:
            login.state = state
            self.error_totals[state] = self.error_totals.get(state, 0) + 1
        self.pipeline.set_login_state(login.login_id, state, login.last_error)

    def _note(self, login, exc):
        """A recoverable provider error: the login stays connected, but it is counted
        and surfaced -- a SchemaChange here means TikTok changed a response shape."""
        state = "schema_change" if isinstance(exc, errors.SchemaChange) else "transient"
        login.last_error = f"{type(exc).__name__}: {exc}"
        with self._lock:
            self.error_totals[state] = self.error_totals.get(state, 0) + 1
        log.info("%s during sync of %s: %s", state, login.login_id, exc)

    def _do_logout(self, login_id):
        login = self.logins.get(login_id)
        if not login:
            return
        try:
            if login.ctx:
                login.ctx.close()
        except Exception as e:  # noqa: BLE001
            log.debug("context close on logout: %s", e)
        # wipe everything for this login (data deletion on logout)
        self.store.delete(login_id)
        self.pipeline.wipe_login(login_id)
        shutil.rmtree(login.profile_dir, ignore_errors=True)
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
            except Exception as e:  # noqa: BLE001
                log.debug("context close on shutdown: %s", e)
