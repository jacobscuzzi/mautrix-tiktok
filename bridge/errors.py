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


# Web path additions.
class SchemaChange(BridgeError):
    """A parser hit a renamed/missing field: TikTok changed the response shape."""


class NotSupported(BridgeError):
    """The capability is not available on this provider (e.g. no send fixture)."""
