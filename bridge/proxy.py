import hashlib

# Operator-supplied proxies. Each login is pinned to one stable proxy so a
# user's traffic keeps a consistent origin (the design's per-user identity).
# failover() moves a login to a different configured proxy only on a transport
# failure; it is not a mechanism to defeat rate limiting by cycling IPs.
class ProxyPool:
    def __init__(self, proxies=None):
        self.proxies = [p for p in (proxies or []) if p]

    def __bool__(self):
        return bool(self.proxies)

    def _index(self, login_id):
        h = hashlib.sha256(login_id.encode()).digest()
        return int.from_bytes(h[:4], "big") % len(self.proxies)

    def for_login(self, login_id):
        if not self.proxies:
            return None
        return self.proxies[self._index(login_id)]

    def failover(self, login_id, current):
        if len(self.proxies) <= 1:
            return current
        start = self._index(login_id)
        for step in range(1, len(self.proxies)):
            cand = self.proxies[(start + step) % len(self.proxies)]
            if cand != current:
                return cand
        return current
