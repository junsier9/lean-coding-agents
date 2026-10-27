class TransientError(ConnectionError):
    """The gateway supports retrying this with a stable idempotency key."""
    pass

class DeclinedError(RuntimeError):
    """The request was rejected; repeating it is not recovery."""
    pass

def validate_request(amount, key):
    if type(amount) is not int or amount <= 0:
        raise ValueError("amount")
    if not isinstance(key, str) or not key.strip():
        raise ValueError("key")

def validate_attempts(value):
    if type(value) is not int or not 1 <= value <= 5:
        raise ValueError("max_attempts")

class InvoiceRequest:
    """A validated request owned by this invocation.

    The supplied key identifies the logical invoice across attempts.
    """
    def __init__(self, amount, key):
        validate_request(amount, key)
        self.amount = amount
        self.key = key
