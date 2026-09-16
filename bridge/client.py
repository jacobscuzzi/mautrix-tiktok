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
                 proxy=None, transport=None):
        self.device = device
        self.signer = signer
        self.base = f"https://{base_host}"
        self.transport = transport or _requests_transport(proxy)

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

    def _do(self, method, path, body, params):
        url, query = self._url(path, params)
        status, _, content = self.transport(method, url, self._headers(method, path, query, body), body)
        if status == 429:
            raise errors.RateLimited("http 429")
        if status >= 500:
            raise errors.Transient(f"http {status}")
        parsed = envelope.parse_response(content)
        envelope.classify(parsed["status_code"], parsed["log_id"])
        return parsed

    def get_im(self, path, params=None):
        return self._do("GET", path, b"", params)

    def post_im(self, path, body, params=None):
        return self._do("POST", path, body, params)
