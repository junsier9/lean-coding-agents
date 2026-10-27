import time
import uuid
from gateway import TransientError, DeclinedError, InvoiceRequest, validate_attempts

def create_invoice(transport, amount, key, max_attempts=3, sleep=time.sleep):
    request = InvoiceRequest(amount, key)
    validate_attempts(max_attempts)
    last_error = None
    for attempt in range(max_attempts):
        try:
            return transport(request.amount, str(uuid.uuid4()))
        except Exception as exc:
            last_error = exc
            if attempt + 1 < max_attempts:
                sleep(0.01 * (2 ** attempt))
    raise last_error
