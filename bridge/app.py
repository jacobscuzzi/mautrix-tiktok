import collections
import time

from . import errors
from . import metrics as _metrics
from .state import SyncState
from .sync import Syncer

# The long-running sync driver for one login. It owns the poll cadence and the
# failure-mode reactions: rate limits back off (they do not spin), a dead
# session stops the loop and surfaces needs-reauth rather than re-logging in.
class BridgeApp:
    def __init__(self, syncer, poll_interval, on_state, sleep=time.sleep,
                 max_backoff=300):
        self.syncer = syncer
        self.poll_interval = poll_interval
        self.on_state = on_state
        self._sleep = sleep
        self.max_backoff = max_backoff
        self._backoff = 0

    def run_once(self):
        try:
            n = self.syncer.poll_once()
        except errors.RateLimited:
            self._backoff = min(self.max_backoff,
                                self._backoff * 2 if self._backoff else self.poll_interval)
            self.on_state("rate_limited")
            self._sleep(self._backoff)
            return None
        except errors.AuthError:
            self.on_state("needs-reauth")
            raise
        except errors.Transient:
            self.on_state("transient")
            self._sleep(self.poll_interval)
            return None
        self._backoff = 0
        self.on_state("connected")
        return n

    def run(self, iterations=None):
        count = 0
        while iterations is None or count < iterations:
            try:
                self.run_once()
            except errors.AuthError:
                return "needs-reauth"
            if self._backoff == 0:
                self._sleep(self.poll_interval)
            count += 1
        return "stopped"


# ---- multi-login runtime -----------------------------------------------------


class LoginRecord:
    def __init__(self, login_id, provider, source="web", password_login_used=False):
        self.login_id = login_id
        self.provider = provider
        self.source = source
        self.password_login_used = password_login_used
        self.state = "connecting"
        self.last_error = None
        self.last_sync_ms = 0
        self.authenticated = False
        self.sync_state = SyncState()


class BridgeRuntime:
    """One process managing N logins over a shared pipeline. Provider-agnostic:
    each login's `provider` is any MessageProvider (web / tikapi / native / fake).
    A dead session surfaces needs-reauth and stops; it never spins."""

    def __init__(self, pipeline, poll_interval=30, clock=None):
        self.pipeline = pipeline
        self.poll_interval = poll_interval
        self._clock = clock or (lambda: int(time.time() * 1000))
        self.logins = {}
        self.error_totals = {}
        self.lags = collections.deque(maxlen=1000)   # recent delivery lags (s)

    def add_login(self, login_id, provider, source="web", password_login_used=False):
        rec = LoginRecord(login_id, provider, source, password_login_used)
        self.logins[login_id] = rec
        self.pipeline.upsert_login(login_id, source=source, state="connecting",
                                   password_login_used=password_login_used)
        return rec

    def connect(self, login_id):
        rec = self.logins[login_id]
        try:
            # backfill contacts + conversations into the pipeline, then poll once.
            self._pull_contacts(rec)
            self._pull_threads(rec)
            syncer = Syncer(rec.provider, rec.sync_state, self.pipeline.make_emit(login_id))
            n = syncer.poll_once()
            self._record_lag(login_id)
            rec.authenticated = True
            rec.state = "connected"
            rec.last_error = None
            rec.last_sync_ms = self._clock()
            self.pipeline.set_login_state(login_id, "connected",
                                          last_sync_ts=rec.last_sync_ms)
            return n
        except errors.AuthError as e:
            return self._fail(rec, "needs_user", e)
        except errors.RateLimited as e:
            return self._fail(rec, "rate_limited", e)
        except errors.Banned as e:
            return self._fail(rec, "blocked", e)
        except errors.NotSupported as e:
            # read path is fine even if send is not; treat as connected-with-note
            rec.state = "connected"
            rec.authenticated = True
            self.pipeline.set_login_state(login_id, "connected", str(e))
            return 0
        except Exception as e:
            return self._fail(rec, "unknown_error", e)

    def _pull_contacts(self, rec):
        lister = getattr(rec.provider, "list_contacts", None)
        if not lister:
            return
        try:
            users, _, _ = lister()
        except errors.BridgeError:
            return
        for u in users:
            self.pipeline.upsert_user(rec.login_id, u)

    def _pull_threads(self, rec):
        from . import normalize
        try:
            convs, _, _ = rec.provider.list_conversations("0")
        except errors.BridgeError:
            return
        for c in convs:
            self.pipeline.upsert_thread(rec.login_id, normalize.to_thread(c))

    def _record_lag(self, login_id):
        for ev in self.pipeline.last_events(20, login_id):
            if ev.get("ts") and ev.get("ingested_at"):
                self.lags.append(max(0.0, (ev["ingested_at"] - ev["ts"]) / 1000.0))

    def _fail(self, rec, state, exc):
        rec.state = state
        rec.authenticated = False
        rec.last_error = f"{type(exc).__name__}: {exc}"
        self.error_totals[state] = self.error_totals.get(state, 0) + 1
        self.pipeline.set_login_state(rec.login_id, state, rec.last_error)
        return None

    def status(self, login_id):
        rec = self.logins.get(login_id)
        if not rec:
            return None
        return {"login_id": login_id, "state": rec.state, "last_error": rec.last_error,
                "last_sync_ms": rec.last_sync_ms, "source": rec.source}

    def remove_login(self, login_id):
        self.logins.pop(login_id, None)

    def metrics_dicts(self):
        return [{"authenticated": r.authenticated, "last_sync_ms": r.last_sync_ms,
                 "state": r.state, "password_login_used": r.password_login_used}
                for r in self.logins.values()]

    def render_metrics(self):
        return _metrics.render_prometheus(
            self.metrics_dicts(), self._clock(), self.poll_interval * 1000,
            self.error_totals, self.lags)
