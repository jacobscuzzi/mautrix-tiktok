import sys
from ..device import Device
from ..session_store import SessionStore
from ..auth.login import LoginProcess

class _DemoQR:
    def start(self):
        return {"qrcode": "<qr-image-data>", "token": "demo-token"}
    def poll(self, token):
        return {"status": "confirmed", "cookies": "sessionid=demo; sid_tt=demo; uid_tt=1000"}

class _DemoBrowser:
    def capture(self):
        return {"cookies": "sessionid=demo-web; uid_tt=1000"}

def main(argv=None):
    argv = argv or sys.argv[1:]
    mode = argv[0] if argv else "qr"
    store = SessionStore("./sessions", master_key=b"0" * 32)
    proc = LoginProcess(Device.generate(), store, _DemoQR(), _DemoBrowser())
    step = proc.start(mode)
    print(f"[{step.kind}] {step.prompt}")
    while step.kind not in ("complete", "error"):
        step = proc.advance()
        print(f"[{step.kind}] {step.prompt}")
    print(f"logged in as uid_tt={proc.device.uid_tt}, session stored encrypted")

if __name__ == "__main__":
    main()
