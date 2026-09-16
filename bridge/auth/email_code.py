# Email + one-time-code login. TikTok sends a code to the account email;
# the user supplies it. No password is ever handled.
class EmailCodeFlow:
    def __init__(self, passport, email):
        self.passport = passport
        self.email = email

    def send_code(self):
        return self.passport.send_email_code(self.email)

    def login(self, code):
        self.passport.email_code_login(self.email, code)
        return {"cookies": self.passport.device.cookie_string()}
