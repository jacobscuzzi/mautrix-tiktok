import tempfile
import unittest
from bridge.auth.login import LoginProcess
from bridge.auth.email_code import EmailCodeFlow
from bridge.device import Device
from bridge.session_store import SessionStore

class FakePassport:
    def __init__(self, device):
        self.device = device
        self.sent = None
        self.logged = None
    def send_email_code(self, email, scene="login"):
        self.sent = email
    def email_code_login(self, email, code, type_=13):
        self.logged = (email, code)
        self.device.import_cookies("sessionid=mail; sid_tt=st; uid_tt=77")

class TestEmailLogin(unittest.TestCase):
    def test_email_flow_sends_code_then_completes(self):
        dev = Device.generate()
        pp = FakePassport(dev)
        flow = EmailCodeFlow(pp, "user@example.com")
        store = SessionStore(tempfile.mkdtemp(), master_key=b"0" * 32)
        proc = LoginProcess(dev, store, email_flow=flow)

        step = proc.start("email")
        self.assertEqual(step.kind, "user_input")
        self.assertEqual(step.data["field"], "email_code")
        self.assertEqual(pp.sent, "user@example.com")

        final = proc.submit_code("123456")
        self.assertEqual(final.kind, "complete")
        self.assertEqual(pp.logged, ("user@example.com", "123456"))
        self.assertTrue(proc.device.is_authenticated)
        self.assertEqual(proc.device.sessionid, "mail")

if __name__ == "__main__":
    unittest.main()
