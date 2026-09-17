import random
import time
from urllib.parse import urlencode
from . import envelope, errors

def _requests_transport(proxy):
    import requests
    sess = requests.Session()
    proxies = {"http": proxy, "https": proxy} if proxy else None
    def call(method, url, headers, body):
        r = sess.request(method, url, headers=headers, data=body,
                         proxies=proxies, timeout=30)
        return r.status_code, dict(r.headers), r.content
    return call

class Client:
    def __init__(self, device, signer, base_host="api16-normal-c-useast2a.tiktokv.com",
                 proxy=None, transport=None, max_retries=2, backoff_base=0.5, sleep=time.sleep):
        self.device = device
        self.signer = signer
        self.base = f"https://{base_host}"
        self.transport = transport or _requests_transport(proxy)
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self._sleep = sleep

    def _url(self, path, params):
        q = dict(self.device.common_params())
        if params:
            q.update({k: str(v) for k, v in params.items()})
        return f"{self.base}{path}?{urlencode(q)}", urlencode(q)

    def _headers(self, method, path, query, body):
        h = {"User-Agent": self.device.user_agent,
             "Cookie": self.device.cookie_string(),
             "Content-Type": "application/x-protobuf"}
        h.update(self.signer.sign(method, path, query, body, self.device))
        return h

    def _backoff(self, attempt):
        return min(60.0, self.backoff_base * (2 ** attempt)) + random.uniform(0, 0.25)

    def _retry_after(self, headers, attempt):
        ra = headers.get("Retry-After") or headers.get("retry-after")
        if ra:
            try:
                return float(ra)
            except ValueError:
                pass
        return self._backoff(attempt)

    def _do(self, method, path, body, params):
        url, query = self._url(path, params)
        attempt = 0
        while True:
            headers = self._headers(method, path, query, body)
            status, resp_headers, content = self.transport(method, url, headers, body)
            resp_headers = resp_headers or {}
            if status == 429:
                if attempt < self.max_retries:
                    self._sleep(self._retry_after(resp_headers, attempt))
                    attempt += 1
                    continue
                raise errors.RateLimited("http 429")
            if status in (401, 407):
                raise errors.AuthError(f"http {status}")
            if status == 403:
                raise errors.Banned(f"http {status}")
            if status >= 500:
                if attempt < self.max_retries:
                    self._sleep(self._backoff(attempt))
                    attempt += 1
                    continue
                raise errors.Transient(f"http {status}")
            if status >= 400:
                raise errors.InvalidRequest(f"http {status}")
            parsed = envelope.parse_response(content)
            if parsed["status_code"] is None:
                raise errors.InvalidRequest("empty or non-protobuf IM response")
            envelope.classify(parsed["status_code"], parsed["log_id"])
            return parsed

    def get_im(self, path, params=None):
        return self._do("GET", path, b"", params)

    def post_im(self, path, body, params=None):
        return self._do("POST", path, body, params)
