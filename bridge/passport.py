import json
from urllib.parse import urlencode
from . import errors

def _passport_requests_transport(proxy):
    import requests
    sess = requests.Session()
    proxies = {"http": proxy, "https": proxy} if proxy else None
    def call(method, url, headers, data):
        r = sess.request(method, url, headers=headers, data=data,
                         proxies=proxies, timeout=30)
        return r.status_code, dict(r.cookies), r.content
    return call

# Passport (auth) endpoints return JSON {"message": "...", "data": {...}}.
# Session cookies arrive via Set-Cookie and are applied to the device.
class PassportClient:
    def __init__(self, device, signer, base_host="api16-normal-c-useast2a.tiktokv.com",
                 proxy=None, transport=None):
        self.device = device
        self.signer = signer
        self.base = f"https://{base_host}"
        self.transport = transport or _passport_requests_transport(proxy)

    def _post(self, path, data):
        q = urlencode(self.device.common_params())
        url = f"{self.base}{path}?{q}"
        body = urlencode(data).encode()
        headers = {"User-Agent": self.device.user_agent,
                   "Cookie": self.device.cookie_string(),
                   "Content-Type": "application/x-www-form-urlencoded"}
        headers.update(self.signer.sign("POST", path, q, body, self.device))
        status, cookies, content = self.transport("POST", url, headers, body)
        if status == 429:
            raise errors.RateLimited("http 429")
        if status >= 500:
            raise errors.Transient(f"http {status}")
        return self._handle(content, cookies)

    def _handle(self, content, cookies):
        try:
            env = json.loads(content)
        except (ValueError, TypeError):
            raise errors.InvalidRequest("passport response not json")
        if env.get("message") == "success":
            if cookies:
                self.device.import_cookies("; ".join(f"{k}={v}" for k, v in cookies.items()))
            return env.get("data", {})
        data = env.get("data") or {}
        code = data.get("error_code")
        desc = data.get("description") or "passport error"
        if code == 7:
            raise errors.RateLimited(desc, code=code)
        raise errors.InvalidRequest(desc, code=code)

    def send_email_code(self, email, scene="login"):
        return self._post("/passport/email/send_code/",
                          {"email": email, "scene": scene, "mix_mode": "1"})

    def email_code_login(self, email, code, type_=13):
        return self._post("/passport/app/email/code_login/",
                          {"email": email, "code": code, "type": str(type_), "mix_mode": "1"})
