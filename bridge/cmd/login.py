import sys
from ..device import Device
from ..session_store import SessionStore
from ..auth.login import LoginProcess
from ..auth.email_code import EmailCodeFlow

class _DemoQR:
    def start(self):
        return {"qrcode": "<qr-image-data>", "token": "demo-token"}
    def poll(self, token):
        return {"status": "confirmed", "cookies": "sessionid=demo; sid_tt=demo; uid_tt=1000"}

class _DemoBrowser:
    def capture(self):
        return {"cookies": "sessionid=demo-web; uid_tt=1000"}

class _DemoPassport:
    def __init__(self, device):
        self.device = device
    def send_email_code(self, email, scene="login"):
        print(f"  (demo) code sent to {email}")
    def email_code_login(self, email, code, type_=13):
        self.device.import_cookies("sessionid=demo-mail; sid_tt=demo; uid_tt=1000")

def main(argv=None):
    argv = argv or sys.argv[1:]
    mode = argv[0] if argv else "email"
    dev = Device.generate()
    store = SessionStore("./sessions", master_key=b"0" * 32)
    email_flow = EmailCodeFlow(_DemoPassport(dev), "j.baumfalk@yahoo.de")
    proc = LoginProcess(dev, store, _DemoQR(), _DemoBrowser(), email_flow)

    step = proc.start(mode)
    print(f"[{step.kind}] {step.prompt}")
    if mode == "email":
        code = input("  enter code: ").strip() if sys.stdin.isatty() else "123456"
        step = proc.submit_code(code)
        print(f"[{step.kind}] {step.prompt}")
    else:
        while step.kind not in ("complete", "error"):
            step = proc.advance()
            print(f"[{step.kind}] {step.prompt}")
    print(f"logged in as uid_tt={proc.device.uid_tt}, session stored encrypted")

if __name__ == "__main__":
    main()
