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
import uuid

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
    def __init__(self, login_id, profile_dir):
        self.login_id = login_id
        self.profile_dir = profile_dir
        self.state = OPENING
        self.account = None            # {handle, uid, nickname, avatar}
        self.last_error = None
        self.password_login_used = True   # real interactive login
        self.connected_at = None
        self.last_sync_ms = 0
        self.authenticated = False
        self.headful_login = False
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

    def connect(self):
        login_id = uuid.uuid4().hex[:12]
        profile = os.path.join(self.data_dir, login_id, "profile")
        os.makedirs(profile, exist_ok=True)
        login = LiveLogin(login_id, os.path.join(self.data_dir, login_id))
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
            self._open_context(login, headless=self.headless)
            login.headful_login = not self.headless
            login.page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=45000)
            self._set_state(login, WAITING_LOGIN)
        except Exception as e:
            self._fail(login, ERROR, e)

    def _open_context(self, login, headless):
        ctx, _ = browserfac.launch_persistent(
            self._p, login.profile_dir, headless=headless, channel="chromium")
        login.ctx = ctx
        login.page = ctx.pages[0] if ctx.pages else ctx.new_page()
        login.page.on("websocket", lambda ws: self._attach_ws(login, ws))

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
            login.page.wait_for_timeout(300)   # pump events
            return
        # logged in -> hand off to a background headless context, then connect
        self._set_state(login, CONNECTING)
        self._go_background(login)
        self._establish(login, cookies)

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
        # first sync
        self._sync_now(login, first=True)
        login.authenticated = True
        login.connected_at = time.time()
        self._set_state(login, CONNECTED)

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
        ev = normalize.to_event(m)
        if not ev.message_id:
            return
        # make sure the thread exists so the chats view lists it
        self.pipeline.upsert_thread(login.login_id,
                                    normalize.to_thread({"conversation_id": ev.conversation_id,
                                                         "last_message": {"create_time": ev.timestamp_ms}}))
        if self.pipeline.ingest_event(ev, login.login_id):
            lag = max(0.0, (int(time.time() * 1000) - ev.timestamp_ms) / 1000.0)
            with self._lock:
                self.lags.append(lag)

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
