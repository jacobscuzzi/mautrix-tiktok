from dataclasses import dataclass, field

@dataclass
class LoginStep:
    kind: str
    prompt: str = ""
    data: dict = field(default_factory=dict)

class LoginProcess:
    def __init__(self, device, store, qr_flow=None, browser_flow=None, email_flow=None):
        self.device = device
        self.store = store
        self.qr = qr_flow
        self.browser = browser_flow
        self.email = email_flow
        self.mode = None
        self._token = None

    def start(self, mode="qr"):
        self.mode = mode
        if mode == "qr":
            info = self.qr.start()
            self._token = info["token"]
            return LoginStep("display_and_wait", "Scan the QR code in your TikTok app",
                             {"qrcode": info["qrcode"]})
        if mode == "email":
            self.email.send_code()
            return LoginStep("user_input", "Enter the code sent to your email",
                             {"field": "email_code"})
        return LoginStep("cookies", "Log in on TikTok's page")

    def advance(self):
        if self.mode == "qr":
            res = self.qr.poll(self._token)
            if res.get("status") != "confirmed":
                return LoginStep("display_and_wait", "Waiting for scan")
            return self._complete(res["cookies"])
        if self.mode == "email":
            return LoginStep("user_input", "Enter the code sent to your email",
                             {"field": "email_code"})
        res = self.browser.capture()
        return self._complete(res["cookies"])

    # Called for the email flow once the user has supplied the code.
    def submit_code(self, code):
        res = self.email.login(code)
        return self._complete(res["cookies"])

    def _complete(self, cookies):
        self.device.import_cookies(cookies)
        blob = {
            "sessionid": self.device.sessionid, "sid_tt": self.device.sid_tt,
            "sid_guard": self.device.sid_guard, "uid_tt": self.device.uid_tt,
            "device_id": self.device.device_id, "install_id": self.device.install_id,
        }
        self.store.save(self.device.uid_tt or "unknown", blob)
        return LoginStep("complete", "Logged in", {"user_id": self.device.uid_tt})

    def verify_mobile(self, client):
        from .. import errors
        try:
            client.get_im("/v1/client/unread_count/", {})
            return True
        except errors.IMNotInitialized:
            return False
        except errors.AuthError:
            return False
