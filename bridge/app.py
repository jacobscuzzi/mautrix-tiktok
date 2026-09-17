import time
from . import errors

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
