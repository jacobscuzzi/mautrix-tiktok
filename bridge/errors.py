class BridgeError(Exception):
    def __init__(self, message, code=None, log_id=None):
        super().__init__(message)
        self.code = code
        self.log_id = log_id

class AuthError(BridgeError): pass
class RateLimited(BridgeError): pass
class Banned(BridgeError): pass
class IMNotInitialized(BridgeError): pass
class SignerStale(BridgeError): pass
class Transient(BridgeError): pass
class InvalidRequest(BridgeError): pass
